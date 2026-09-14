#!/bin/bash
set -e

echo "=== Llama 多架构镜像构建脚本 ==="
echo "作者: Docker 助手"
echo "日期: $(date)"
echo "================================="

# 工作目录
WORKDIR="/home/master/llama-multi-arch"
cd "$WORKDIR" || { echo "错误: 无法进入目录 $WORKDIR"; exit 1; }

# 镜像仓库配置
REGISTRY="ghcr.io/lzx147607"
IMAGE_NAME="llama-server"
VERSION=$(date +%Y%m%d)

# 1. 检查必要文件
echo "✓ 检查必要文件..."
if [ ! -f "models/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf" ]; then
    echo "❌ 错误: 模型文件不存在"
    echo "请确认 models/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf 存在"
    exit 1
fi
echo "  ✓ 模型文件存在"

# 2. 检查各架构目录
echo "✓ 检查架构目录..."
ARCHS=()
[ -d "x86/bin" ] && ARCHS+=("x86")
[ -d "arm64/bin" ] && ARCHS+=("arm64")
[ -d "riscv64/bin" ] && ARCHS+=("riscv64")

if [ ${#ARCHS[@]} -eq 0 ]; then
    echo "❌ 错误: 没有找到任何架构的二进制目录"
    echo "请确认至少有一个架构目录 (x86/bin, arm64/bin, riscv64/bin)"
    exit 1
fi
echo "  ✓ 找到架构: ${ARCHS[*]}"

# 3. 函数：推送镜像到仓库
push_image() {
    local arch=$1
    local image_name="$REGISTRY/$IMAGE_NAME-$arch"
    
    echo "📤 推送 $arch 镜像到 $REGISTRY..."
    
    # 重命名镜像
    docker tag "$IMAGE_NAME-$arch:latest" "$image_name:latest"
    docker tag "$IMAGE_NAME-$arch:$VERSION" "$image_name:$VERSION"
    
    # 推送镜像
    if docker push "$image_name:latest" && docker push "$image_name:$VERSION"; then
        echo "✅ $arch 镜像推送成功: $image_name"
        return 0
    else
        echo "❌ $arch 镜像推送失败"
        return 1
    fi
}

# 函数：创建并推送多架构镜像
push_manifest() {
    local image_name="$REGISTRY/$IMAGE_NAME"
    
    echo "📤 创建并推送多架构镜像到 $REGISTRY..."
    
    # 构建镜像列表
    IMAGE_LIST=""
    for arch in "${SUCCESS_ARCHS[@]}"; do
        IMAGE_LIST="$IMAGE_LIST $REGISTRY/$IMAGE_NAME-$arch:latest"
    done
    
    # 删除现有的 manifest（如果存在）
    echo "  删除现有的 manifest（如果存在）..."
    docker manifest rm "$image_name:latest" 2>/dev/null || true
    docker manifest rm "$image_name:$VERSION" 2>/dev/null || true
    
    # 创建 manifest
    echo "  创建新的 manifest..."
    if docker manifest create "$image_name:latest" $IMAGE_LIST; then
        echo "✅ 多架构 manifest 创建成功"
        
        # 推送 manifest
        if docker manifest push "$image_name:latest"; then
            echo "✅ 多架构镜像推送成功: $image_name:latest"
            return 0
        else
            echo "❌ 多架构镜像推送失败"
            return 1
        fi
    else
        echo "❌ 多架构 manifest 创建失败"
        return 1
    fi
}

# 3. 函数：构建单个架构
build_arch() {
    local arch=$1
    local image_name="$IMAGE_NAME-$arch"
    
    echo ""
    echo "=== 构建 $arch 架构 ==="
    
    # 根据架构选择基础镜像
    case $arch in
        x86)
            BASE_IMAGE="ubuntu:24.04"
            PLATFORM="linux/amd64"
            ;;
        arm64)
            BASE_IMAGE="ubuntu:24.04"
            PLATFORM="linux/arm64"
            ;;
        riscv64)
            BASE_IMAGE="ubuntu:24.04"
            PLATFORM="linux/riscv64"
            ;;
        *)
            echo "❌ 不支持的架构: $arch"
            return 1
            ;;
    esac
    
    # 创建 Dockerfile
    cat > "Dockerfile.$arch" <<EOF
# ============================================
# Llama Server - $arch 架构
# 生成时间: $(date)
# ============================================
FROM $BASE_IMAGE

# 设置时区为上海
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/\$TZ /etc/localtime && echo \$TZ > /etc/timezone

# 安装依赖
RUN apt-get update && \\
    apt-get install -y libgomp1 ca-certificates && \\
    apt-get install -y -t jammy-backports libstdc++6 || apt-get install -y libstdc++6 && \\
    rm -rf /var/lib/apt/lists/*

# 设置工作目录
WORKDIR /app

# 复制二进制文件
COPY $arch/bin/ /app/bin/

# 复制模型文件
COPY models/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf /app/models/

# 设置环境变量
ENV LD_LIBRARY_PATH=/app/bin
ENV MODEL_PATH=/app/models/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf

# 暴露端口
EXPOSE 8080

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \\
    CMD curl -f http://localhost:8080/ || exit 1

# 启动命令
ENTRYPOINT ["/app/bin/llama-server"]
CMD ["-m", "/app/models/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf", "--host", "0.0.0.0", "--port", "8080"]
EOF
    
    echo "✓ 创建 Dockerfile.$arch"
    
    # 构建镜像
    echo "🚀 开始构建 $arch 镜像..."
    if docker build \
        --platform "$PLATFORM" \
        -f "Dockerfile.$arch" \
        -t "$image_name:latest" \
        -t "$image_name:$(date +%Y%m%d)" \
        .; then
        echo "✅ $arch 镜像构建成功: $image_name"
        echo "$image_name" >> .built_images.txt
        return 0
    else
        echo "❌ $arch 镜像构建失败"
        return 1
    fi
}

# 4. 函数：测试基础镜像
test_base_image() {
    local arch=$1
    local platform=$2
    
    echo "  测试 $arch 基础镜像..."
    if docker run --rm --platform "$platform" ubuntu:24.04 echo "测试成功" 2>/dev/null; then
        echo "  ✓ $arch 基础镜像正常"
        return 0
    else
        echo "  ⚠ $arch 基础镜像可能有问题，尝试修复..."
        
        # 尝试设置 QEMU
        if [ "$arch" != "x86" ]; then
            echo "  设置 QEMU 支持..."
            docker run --rm --privileged multiarch/qemu-user-static --reset -p yes 2>/dev/null || true
        fi
        
        # 重新拉取镜像
        echo "  重新拉取 $arch 基础镜像..."
        docker pull --platform "$platform" ubuntu:24.04 2>/dev/null || true
        
        # 再次测试
        if docker run --rm --platform "$platform" ubuntu:24.04 echo "测试成功" 2>/dev/null; then
            echo "  ✓ $arch 基础镜像修复成功"
            return 0
        else
            echo "  ❌ $arch 基础镜像无法使用"
            return 1
        fi
    fi
}

# 5. 主构建流程
echo ""
echo "=== 开始构建流程 ==="
echo "工作目录: $(pwd)"
echo ""

# 记录成功和失败的架构
SUCCESS_ARCHS=()
FAILED_ARCHS=()

# 清理旧的记录
> .built_images.txt

# 遍历所有架构
for arch in "${ARCHS[@]}"; do
    case $arch in
        x86) PLATFORM="linux/amd64" ;;
        arm64) PLATFORM="linux/arm64" ;;
        riscv64) PLATFORM="linux/riscv64" ;;
    esac
    
    # 测试基础镜像
    if test_base_image "$arch" "$PLATFORM"; then
        # 构建镜像
        if build_arch "$arch"; then
            SUCCESS_ARCHS+=("$arch")
        else
            FAILED_ARCHS+=("$arch")
        fi
    else
        FAILED_ARCHS+=("$arch")
    fi
done

# 6. 推送镜像到仓库
echo ""
echo "=== 推送镜像到仓库 ==="
if [ ${#SUCCESS_ARCHS[@]} -gt 0 ]; then
    echo "开始推送镜像到 $REGISTRY..."
    
    # 推送各个架构的镜像
    for arch in "${SUCCESS_ARCHS[@]}"; do
        if push_image "$arch"; then
            echo "✅ $arch 镜像推送完成"
        else
            echo "❌ $arch 镜像推送失败"
        fi
    done
    
    # 推送多架构镜像（如果多个架构成功）
    if [ ${#SUCCESS_ARCHS[@]} -ge 2 ]; then
        echo ""
        echo "=== 创建并推送多架构镜像 ==="
        if push_manifest; then
            echo "✅ 多架构镜像推送完成"
        else
            echo "⚠ 多架构镜像推送失败"
        fi
    else
        echo "⚠ 少于2个架构成功，跳过多架构镜像推送"
    fi
else
    echo "⚠ 没有成功构建的架构，跳过推送"
fi

# 7. 清理临时文件
echo ""
echo "=== 清理临时文件 ==="
rm -f Dockerfile.x86 Dockerfile.arm64 Dockerfile.riscv64 2>/dev/null || true
echo "✓ 清理完成"

# 8. 构建结果汇总
echo ""
echo "================================="
echo "=== 构建结果汇总 ==="
echo "================================="
echo "成功架构: ${SUCCESS_ARCHS[*]:-无}"
echo "失败架构: ${FAILED_ARCHS[*]:-无}"
echo ""

# 显示构建的镜像
if [ -s .built_images.txt ]; then
    echo "已构建的镜像:"
    while read -r image; do
        echo "  - $image:latest"
    done < .built_images.txt
    rm -f .built_images.txt
fi

# 使用示例
echo ""
echo "=== 使用示例 ==="
if [ ${#SUCCESS_ARCHS[@]} -gt 0 ]; then
    echo "# 从本地运行 x86 版本:"
    echo "docker run -d -p 8080:8080 --name llama-x86 llama-server-x86"
    
    if [[ " ${SUCCESS_ARCHS[*]} " =~ " arm64 " ]]; then
        echo ""
        echo "# 从本地运行 arm64 版本:"
        echo "docker run -d -p 8081:8080 --name llama-arm64 llama-server-arm64"
    fi
    
    if [[ " ${SUCCESS_ARCHS[*]} " =~ " riscv64 " ]]; then
        echo ""
        echo "# 从本地运行 riscv64 版本:"
        echo "docker run -d -p 8082:8080 --name llama-riscv64 llama-server-riscv64"
    fi
    
    echo ""
    echo "# 从仓库拉取并运行 x86 版本:"
    echo "docker run -d -p 8080:8080 --name llama-x86 $REGISTRY/$IMAGE_NAME-x86:latest"
    
    if [[ " ${SUCCESS_ARCHS[*]} " =~ " arm64 " ]]; then
        echo ""
        echo "# 从仓库拉取并运行 arm64 版本:"
        echo "docker run -d -p 8081:8080 --name llama-arm64 $REGISTRY/$IMAGE_NAME-arm64:latest"
    fi
    
    if [[ " ${SUCCESS_ARCHS[*]} " =~ " riscv64 " ]]; then
        echo ""
        echo "# 从仓库拉取并运行 riscv64 版本:"
        echo "docker run -d -p 8082:8080 --name llama-riscv64 $REGISTRY/$IMAGE_NAME-riscv64:latest"
    fi
    
    if [ ${#SUCCESS_ARCHS[@]} -ge 2 ]; then
        echo ""
        echo "# 使用多架构镜像（自动选择适合的架构）:"
        echo "docker run -d -p 8083:8080 --name llama-multi $REGISTRY/$IMAGE_NAME:latest"
    fi
fi

echo ""
echo "✅ 脚本执行完成！"