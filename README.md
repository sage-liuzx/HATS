# HATS: 面向大模型推理的异构边缘设备协同调度研究

> Heterogeneity-Aware Task Scheduling for LLM Inference on Heterogeneous Edge Clusters

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://www.python.org/)
[![Kubernetes](https://img.shields.io/badge/Kubernetes-v1.28%2B-326CE5)](https://kubernetes.io/)
[![llama.cpp](https://img.shields.io/badge/llama.cpp-LLM%20Inference-orange)](https://github.com/ggerganov/llama.cpp)

---

## 项目简介

HATS 是一个面向**边缘异构集群大语言模型推理**的 Kubernetes 自定义调度系统。边缘集群通常由 x86、ARM、RISC-V 等多种架构设备混合组成，不同架构之间的 LLM 推理性能可能相差两个数量级；同时，LLM 请求的输出长度在到达时未知，节点性能又容易受到温度、背景负载、动态调频等因素影响。传统基于静态资源请求或固定权重的调度器难以在“性能优先”与“负载均衡”之间取得稳定平衡。

HATS 通过三个核心模块解决上述问题：

1. **动态节点能力感知**：周期性微基准测试 + 滑动窗口平滑，实时估计节点 `TPS_prompt` 与 `TPS_gen`。
2. **轻量级输出长度预测**：基于 DistilBERT 的 8 分类长度预测器，将不可知输出长度转化为可计算执行时间。
3. **多目标调度决策**：综合时间评分、队列评分、资源利用率评分，在性能与负载之间平滑权衡。

实验在包含 **11 台真实异构节点** 的集群上进行，覆盖 x86、ARM、RISC-V 三种架构，验证了 HATS 在强异构环境下的有效性。

---

## 核心特性

- **异构感知**：支持 x86 / ARM / RISC-V 混合集群，不依赖静态硬件标签。
- **动态能力建模**：每 20 分钟注入轻量级 token 微基准，使用滑动窗口平滑 TPS 波动。
- **输出长度预测**：DistilBERT + 线性分类头，将连续长度预测离散为 8 个长度桶，预测桶中位数。
- **在线自进化**：收集真实 `(prompt, 实际输出长度)` 样本，累计 500 条后触发增量微调。
- **多目标评分**：  
  `Score = w1 × TimeScore + w2 × QueueScore + w3 × UtilScore`
- **Kubernetes 原生集成**：基于 Scheduler Framework 的 Filter / Score 扩展点，以自定义镜像方式注入，无需修改 K8s 核心代码。
- **真实集群验证**：11 节点、TinyLlama 1.1B、llama.cpp、MAX_RUNNING=32 并发控制。

---

## 系统架构

```text
用户提交 LLM 推理任务
        │
        ▼
┌────────────────────────────────────────────┐
│ 感知层：动态 TPS 基准测试 + 滑动窗口平滑      │
├────────────────────────────────────────────┤
│ 预测层：DistilBERT 轻量级输出长度预测 + 在线反馈 │
├────────────────────────────────────────────┤
│ 决策层：多目标优化调度决策                    │
└────────────────────────────────────────────┘
        │
        ▼
x86 / ARM / RISC-V 异构边缘节点
```

调度器以 `llama-scheduler` 自定义镜像运行在 Kubernetes 中，通过节点 Annotation 读取实时 TPS 与任务预测信息，并在 Score 扩展点计算最终节点得分。

---

## 方法细节

### 1. 动态节点能力感知

每个节点以固定周期执行一次轻量级基准测试，使用与真实任务相同的 TinyLlama 1.1B 模型及固定 prompt，记录：

- `TPS_prompt`：Prompt 处理吞吐，单位 token/s；
- `TPS_gen`：Token 生成吞吐，单位 token/s。

为降低短期扰动影响，每个节点维护长度为 5 的滑动窗口：

```math
TPS_{prompt} = \frac{1}{W}\sum_{i=t-W+1}^{t} TPS_{prompt}^{i}
```

```math
TPS_{gen} = \frac{1}{W}\sum_{i=t-W+1}^{t} TPS_{gen}^{i}
```

估计值通过 Kubernetes Annotation 动态发布，调度器每次决策前实时读取。

### 2. LLM 任务执行时间预测

给定输入长度 `N_in`、预测输出长度 `N_out`，任务在节点 `i` 上的预测执行时间为：

```math
T_i = \frac{N_{in}}{TPS_{prompt}(i)} + \frac{N_{out}}{TPS_{gen}(i)}
```

输出长度预测器采用 DistilBERT 作为基座特征提取器，后接线性分类头，将输出长度离散为 8 个桶，预测目标为桶中位数。该设计轻量、低延迟，且对调度决策而言，长度区间的相对排序比精确数值更重要。

### 3. 多目标调度决策

每个候选节点计算综合得分：

```math
Score = w_1 \times TimeScore + w_2 \times QueueScore + w_3 \times UtilScore
```

时间评分采用指数衰减函数，避免硬阈值导致决策不连续：

```math
TimeScore = 100 \times e^{-\frac{T_i - T_{best}}{\beta}}, \quad \beta = 10
```


---

## 实验环境

实验集群共 11 台物理设备：

| 架构 | 型号 | CPU 核心数 | 内存 | 数量 |
|---|---|---|---|---|
| x86 | 虚拟机 | 4 核 | 8 GB | 3 |
| ARM | NVIDIA Jetson Orin | 8 核 | 16 GB | 2 |
| ARM | Raspberry Pi 4B | 4 核 | 8 GB | 3 |
| RISC-V | Milk-V Meles | 4 核 | 4 GB | 3 |

- 推理模型：TinyLlama 1.1B，基于 llama.cpp；
- 并发控制：`MAX_RUNNING = 32`，保留约 20% 资源余量；
- 对比基线：Kubernetes Default、SJF、Least-load；
- 任务规模：100 ~ 1000 个推理任务。

---

## 实验结果

在强异构环境下，HATS 相比 Kubernetes Default 调度器取得以下效果：

- **500 任务规模**：makespan 降低 **58.9%**；
- 系统吞吐量提升约 **1.44 倍**；
- P95 尾延迟降低约 **26%**；
- 有效避免 RISC-V 慢节点，将任务均匀分布在高性能节点；
- 输出长度预测器 8 桶配置下长度 MAE 为 **20.86 token**，比固定基线降低约 **48.1%**；
- 执行时间预测 MAE 为 **65.57s**，Default 为 **130.72s**；
- 在同构或弱异构环境中，HATS 性能与传统调度策略基本持平，说明其具有良好适应性。

消融实验表明：

- 去除动态 TPS 感知后，性能退化节点会导致排队积压与长尾延迟；
- 去除输出长度预测后，仅使用输入长度会严重低估长任务，导致 makespan 与 P99 明显恶化。

---

# HATS 运行操作手册

> 镜像构建与交叉编译见 [docs/build.md](docs/build.md)。

## 前置条件

- Kubernetes v1.28+
- 集群已加入 x86 / ARM / RISC-V 异构节点
- 已配置 ghcr.io 镜像拉取权限
- 已安装 `kubectl`、`python3`

## 1. 部署 llama.cpp 推理服务

```bash
kubectl apply -f deploy/llama-server-daemonset.yaml
```

## 2. 部署输出长度预测器

```bash
kubectl apply -f deploy/token-predictor.yaml
```

## 3. 部署自定义调度器

```bash
kubectl apply -f deploy/llama-scheduler.yaml
```

## 4. 标注节点能力

```bash
# 示例：ARM 节点
kubectl annotate node arm207 \
  node-capabilities.llama-prompt-tokens-per-second=10.5 \
  node-capabilities.llama-generation-tokens-per-second=4.8 \
  --overwrite

# 示例：RISC-V 节点
kubectl annotate node riscv105 \
  node-capabilities.llama-prompt-tokens-per-second=0.4 \
  node-capabilities.llama-generation-tokens-per-second=0.3 \
  --overwrite
```

> 节点名称和数值请根据实际集群调整。

## 5. 运行 benchmark

```bash
# 批量提交推理任务
python scripts/submit_batch.py --tasks 100

# 调度器对比实验（HATS vs Default / SJF / Least-load）
bash scripts/compare_scheduler.sh
```

## 6. 查看结果

```bash
kubectl get pods -A -o wide
kubectl logs -l app=llama-scheduler -n llama-scheduler
```

## 7. 清理

```bash
kubectl delete -f deploy/llama-scheduler.yaml
kubectl delete -f deploy/token-predictor.yaml
kubectl delete -f deploy/llama-server-daemonset.yaml
```

## 8. 复现实验步骤

1. 按表准备异构节点；
2. 在所有节点部署 llama.cpp 与 TinyLlama 1.1B；
3. 部署 HATS 调度器，确认节点 Annotation 可读取动态 TPS；
4. 依次运行 100、200、300、500、1000 任务规模实验；
5. 对比 Default、SJF、Least-load 三种基线；
6. 分别关闭动态 TPS 与输出长度预测器，完成消融实验；
7. 在同构 x86、ARM、RISC-V 集群中重复实验。
