"""批量提交 LLM 推理任务到 Kubernetes。"""

import argparse
import random
import subprocess
import time

PROMPTS = [
    "Explain what a transformer is.",
    "Write a short poem about autumn.",
    "What is the capital of France?",
    "Summarize the theory of relativity in one sentence.",
    "Describe how RISC-V vector extension works.",
]


def build_pod_yaml(name: str, prompt: str, input_tokens: int, pred_tokens: int) -> str:
    return f"""
apiVersion: v1
kind: Pod
metadata:
  name: {name}
  annotations:
    llama.input-tokens: "{input_tokens}"
    llama.predicted-output-tokens: "{pred_tokens}"
    llama.prompt: "{prompt}"
spec:
  schedulerName: llama-scheduler
  restartPolicy: Never
  containers:
    - name: infer
      image: ghcr.io/sage-liuzx/llama-infer:latest
      imagePullPolicy: Always
      resources:
        requests:
          cpu: "1"
          memory: "1Gi"
        limits:
          cpu: "2"
          memory: "2Gi"
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", type=int, default=100)
    parser.add_argument("--namespace", default="default")
    args = parser.parse_args()

    for i in range(args.tasks):
        name = f"llm-task-{i:04d}"
        prompt = random.choice(PROMPTS)
        input_tokens = random.randint(5, 50)
        pred_tokens = random.randint(20, 200)
        yaml_str = build_pod_yaml(name, prompt, input_tokens, pred_tokens)
        yaml_file = f"/tmp/{name}.yaml"
        with open(yaml_file, "w") as f:
            f.write(yaml_str)
        subprocess.run(
            ["kubectl", "apply", "-f", yaml_file, "-n", args.namespace],
            check=False,
        )
        time.sleep(0.1)


if __name__ == "__main__":
    main()
