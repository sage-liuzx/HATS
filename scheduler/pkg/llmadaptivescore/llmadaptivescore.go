package llmadaptivescore

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"strconv"

	v1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/klog/v2"
	"k8s.io/kubernetes/pkg/scheduler/framework"
)

const Name = "LLMAdaptiveScore"

// ===== Annotation Keys =====
const (
	NodeGenerationThroughputAnnotation = "llama.generation-tokens-per-sec"
	NodePromptThroughputAnnotation     = "llama.prompt-tokens-per-sec"
	// NodeGenerationThroughputAnnotation = "node-capabilities.llama-generation-tokens-per-second"
	// NodePromptThroughputAnnotation     = "node-capabilities.llama-prompt-tokens-per-second"
	PodPredTokensAnnotation  = "llama.predicted-output-tokens"
	PodInputTokensAnnotation = "llama.input-tokens"
)

// ===== 参数 =====
type Args struct {
	TimeWeight  float64 `json:"timeWeight,omitempty"`
	QueueWeight float64 `json:"queueWeight,omitempty"`
	UtilWeight  float64 `json:"utilWeight,omitempty"`
}

// ===== 插件结构 =====
type LLMAdaptiveScore struct {
	handle framework.Handle
	args   Args
}

var _ framework.ScorePlugin = &LLMAdaptiveScore{}

// ===== 初始化 =====
func New(_ context.Context, obj runtime.Object, handle framework.Handle) (framework.Plugin, error) {
	klog.Infof("Creating %s plugin", Name)

	args := Args{
		TimeWeight:  1.0,
		QueueWeight: 0.0,
		UtilWeight:  0.0,
	}

	if obj != nil {
		if err := decodeInto(obj, &args); err != nil {
			return nil, err
		}
	}

	sum := args.TimeWeight + args.QueueWeight + args.UtilWeight
	if sum > 0 {
		args.TimeWeight /= sum
		args.QueueWeight /= sum
		args.UtilWeight /= sum
	}

	return &LLMAdaptiveScore{
		handle: handle,
		args:   args,
	}, nil
}

func (pl *LLMAdaptiveScore) Name() string {
	return Name
}

// ===== 核心 Score =====
func (pl *LLMAdaptiveScore) Score(
	ctx context.Context,
	state *framework.CycleState,
	pod *v1.Pod,
	nodeName string,
) (int64, *framework.Status) {

	node, err := pl.handle.ClientSet().CoreV1().Nodes().Get(ctx, nodeName, metav1.GetOptions{})
	if err != nil {
		return 0, framework.NewStatus(framework.Error, err.Error())
	}

	// === 获取能力 ===
	genTPS, _ := getNodeFloatAnnotation(node, NodeGenerationThroughputAnnotation)
	promptTPS, _ := getNodeFloatAnnotation(node, NodePromptThroughputAnnotation)

	inputTokens, _ := getInputTokensFromPod(pod)
	outputTokens, _ := getPredTokensFromPod(pod)

	// === 计算预测时间 ===
	predTime := inputTokens/(promptTPS+1e-6) + outputTokens/(genTPS+1e-6)
	// predTime := inputTokens/(promptTPS+1e-6)

	// === 获取最优时间 ===
	bestTime := pl.getBestPredTime(ctx, pod)

	// === 负载 ===
	util, loadScore, utilScore, _ := pl.computeUtilizationScores(node)

	// === 连续时间衰减模型 ===
	diff := predTime - bestTime

	// 防止数值异常（理论上不会 <0，但保险）
	if diff < 0 {
		diff = 0
	}
	// 控制调度器“容忍慢节点”的程度（核心参数）
	beta := 10.0
	// 时间评分（0~100，连续变化）
	latencyScore := 100 * math.Exp(-diff / beta)
	
	// === 最终得分 ===
	final := pl.args.TimeWeight*latencyScore +
		pl.args.QueueWeight*loadScore +
		pl.args.UtilWeight*utilScore

	if final < 0 {
		final = 0
	}
	if final > 100 {
		final = 100
	}

	klog.Infof("=== [LLMAdaptiveScore] 节点详情: %s ===", nodeName)
	klog.Infof("  输入Tokens: %.0f | 输出Tokens: %.0f", inputTokens, outputTokens)
	klog.Infof("  节点能力 - 生成TPS: %.2f | 提示TPS: %.2f", genTPS, promptTPS)
	klog.Infof("  预测时间: %.2f秒 | 最优时间: %.2f秒", predTime, bestTime)
	klog.Infof("  评分详情 - 延迟评分: %.2f | 负载评分: %.2f | 利用率评分: %.2f", latencyScore, loadScore, utilScore)
	klog.Infof("  权重 - 时间权重: %.2f | 队列权重: %.2f | 利用率权重: %.2f", pl.args.TimeWeight, pl.args.QueueWeight, pl.args.UtilWeight)
	klog.Infof("  最终得分: %.2f | 节点利用率: %.2f%%", final, util*100)
	klog.Infof("=== 节点 %s 评分完成 ===", nodeName)

	return int64(final), framework.NewStatus(framework.Success)
}

func (pl *LLMAdaptiveScore) ScoreExtensions() framework.ScoreExtensions {
	return nil
}

// ===== 计算最优时间 =====
func (pl *LLMAdaptiveScore) getBestPredTime(ctx context.Context, pod *v1.Pod) float64 {
	nodes, _ := pl.handle.SnapshotSharedLister().NodeInfos().List()

	inputTokens, _ := getInputTokensFromPod(pod)
	outputTokens, _ := getPredTokensFromPod(pod)

	best := math.MaxFloat64

	for _, n := range nodes {
		node := n.Node()

		genTPS, err1 := getNodeFloatAnnotation(node, NodeGenerationThroughputAnnotation)
		promptTPS, err2 := getNodeFloatAnnotation(node, NodePromptThroughputAnnotation)

		if err1 != nil || err2 != nil {
			continue
		}

		t := inputTokens/(promptTPS+1e-6) + outputTokens/(genTPS+1e-6)

		if t < best {
			best = t
		}
	}

	return best
}

// ===== Util =====
func (pl *LLMAdaptiveScore) computeUtilizationScores(node *v1.Node) (float64, float64, float64, error) {
	nodeInfo, err := pl.handle.SnapshotSharedLister().NodeInfos().Get(node.Name)
	if err != nil {
		return 0, 50, 50, err
	}

	cpuAlloc := node.Status.Allocatable.Cpu().MilliValue()
	memAlloc := node.Status.Allocatable.Memory().Value()

	usedCPU := nodeInfo.Requested.MilliCPU
	usedMem := nodeInfo.Requested.Memory

	utilCPU := float64(usedCPU) / float64(cpuAlloc)
	utilMem := float64(usedMem) / float64(memAlloc)

	util := (utilCPU + utilMem) / 2

	queueScore := (1 - util) * 100
	utilScore := util * 100

	return util, queueScore, utilScore, nil
}

// ===== 工具函数 =====
func getNodeFloatAnnotation(node *v1.Node, key string) (float64, error) {
	val, ok := node.Annotations[key]
	if !ok {
		return 0, fmt.Errorf("missing %s", key)
	}
	return strconv.ParseFloat(val, 64)
}

func getPredTokensFromPod(pod *v1.Pod) (float64, bool) {
	val, ok := pod.Annotations[PodPredTokensAnnotation]
	if !ok {
		return 0, false
	}
	v, err := strconv.ParseFloat(val, 64)
	return v, err == nil
}

func getInputTokensFromPod(pod *v1.Pod) (float64, bool) {
	val, ok := pod.Annotations[PodInputTokensAnnotation]
	if !ok {
		return 0, false
	}
	v, err := strconv.ParseFloat(val, 64)
	return v, err == nil
}

func decodeInto(obj runtime.Object, into interface{}) error {
	data, _ := json.Marshal(obj)
	return json.Unmarshal(data, into)
}