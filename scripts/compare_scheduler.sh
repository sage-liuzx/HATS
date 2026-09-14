#!/bin/bash
set -e

NUM_TASKS=${1:-100}
NS_CUSTOM="custom-sched-test"
NS_DEFAULT="default-sched-test"

kubectl delete namespace $NS_CUSTOM --ignore-not-found=true
kubectl delete namespace $NS_DEFAULT --ignore-not-found=true
sleep 3
kubectl create namespace $NS_CUSTOM
kubectl create namespace $NS_DEFAULT

echo "=== HATS 调度器 ==="
start=$(date +%s)
python scripts/submit_batch.py --tasks $NUM_TASKS --namespace $NS_CUSTOM
# 等待所有 Pod 完成
kubectl wait --for=condition=Ready pod --all -n $NS_CUSTOM --timeout=3600s || true
end=$(date +%s)
echo "HATS makespan: $((end - start)) s"

echo "=== 默认调度器 ==="
start=$(date +%s)
python scripts/submit_batch.py --tasks $NUM_TASKS --namespace $NS_DEFAULT
kubectl wait --for=condition=Ready pod --all -n $NS_DEFAULT --timeout=3600s || true
end=$(date +%s)
echo "Default makespan: $((end - start)) s"
