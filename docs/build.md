# HATS 镜像构建操作手册

> 默认使用预构建镜像运行。仅在需要重新构建镜像时参考本文档。

## 环境要求

- Go 1.22+
- Docker buildx
- qemu-user-static
- Python 3.9+
- CMake

## 1. 注册 qemu-user-static

```bash
docker run --rm --privileged multiarch/qemu-user-static --reset -p yes
```

## 2. 交叉编译调度器

```bash
export GOPROXY=https://goproxy.cn,direct
export PATH=/usr/local/go/bin:$PATH

cd scheduler-plugins

# ARM64
GOOS=linux GOARCH=arm64 make build LOCAL_ONLY=true WHAT=./cmd/scheduler

# RISC-V
GOOS=linux GOARCH=riscv64 make build LOCAL_ONLY=true WHAT=./cmd/scheduler
```

## 3. 构建调度器镜像

```bash
# Dockerfile.riscv64 示例
cat > Dockerfile.riscv64 <<'EOF'
FROM scratch
COPY bin/kube-scheduler /usr/local/bin/kube-scheduler
ENTRYPOINT ["kube-scheduler"]
EOF

# 构建并推送
docker buildx build \
  --platform linux/riscv64 \
  -t ghcr.io/<your-username>/hpl-scheduler-riscv64:v0.31.8 \
  --push \
  -f Dockerfile.riscv64 .
```

## 4. 构建预测器镜像

```bash
# Dockerfile.predictor 示例
cat > Dockerfile.predictor <<'EOF'
FROM ubuntu-arm64:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Asia/Shanghai

WORKDIR /app

RUN apt-get update && apt-get install -y \
    python3.9 python3-pip python3-dev \
    && ln -sf /usr/bin/python3.9 /usr/bin/python \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install --no-cache-dir \
    torch torchvision torchaudio \
    --index-url https://download.pytorch.org/whl/cpu

RUN pip3 install --no-cache-dir \
    transformers safetensors flask gunicorn

COPY token_predictor_infer.py .
COPY predictor_server.py .
COPY bucket_config.json .
COPY distilbert-base-uncased-finetuned/ ./model/

EXPOSE 8080
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "predictor_server:app"]
EOF

docker build --no-cache --platform linux/arm64 \
  -f Dockerfile.predictor \
  -t ghcr.io/<your-username>/token-predictor:arm64 .
```

## 5. 构建 llama-server 镜像

```bash
# 编译 llama.cpp
cmake .. \
  -DLLAMA_CURL=OFF \
  -DLLAMA_BUILD_SERVER=ON \
  -DCMAKE_BUILD_TYPE=Release
make -j$(nproc) llama-server

# Dockerfile.server 示例
cat > Dockerfile.server <<'EOF'
FROM ubuntu:22.04
RUN apt update && apt install -y libgomp1 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY build/bin/ /app/bin/
COPY models/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf /app/models/
ENV LD_LIBRARY_PATH=/app/bin:$LD_LIBRARY_PATH
EXPOSE 8080
ENTRYPOINT ["/app/bin/llama-server"]
CMD ["-m", "/app/models/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf", "--host", "0.0.0.0", "--port", "8080"]
EOF

docker buildx build \
  --platform linux/amd64,linux/arm64,linux/riscv64 \
  -t ghcr.io/<your-username>/llama-server:latest \
  --push \
  -f Dockerfile.server .
```

## 6. 注意事项

- 调度器版本需与 Kubernetes API 版本兼容。实验中 `v0.33.5` 与 K8s 1.31 不兼容，最终使用 `v0.31.8`。
- RISC-V 节点需要手动导入 `pause` 镜像。
- 节点加入集群后如出现 `cni0` IP 冲突：

```bash
sudo ip link delete cni0
```

- kubelet 不健康时尝试关闭 swap：

```bash
sudo swapoff -a
```
