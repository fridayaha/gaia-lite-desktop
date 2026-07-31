#!/bin/bash
# 离线镜像导入 - 在无网 k3s 节点上执行
set -euo pipefail

PACKAGE_DIR="${1:-.}"
IMAGE_TAR="${PACKAGE_DIR}/hermes-profile.tar"
BASE_TAR="${PACKAGE_DIR}/hermes-agent-base.tar"

echo "=== 导入 Hermes 镜像到 k3s ==="

if ! command -v k3s &>/dev/null && ! command -v ctr &>/dev/null; then
    echo "错误: 未找到 k3s 或 ctr 命令"
    exit 1
fi

import_image() {
    local tar_file="$1"
    local desc="$2"

    if [[ ! -f "$tar_file" ]]; then
        echo "跳过: $desc ($tar_file 不存在)"
        return 0
    fi

    echo "导入 $desc..."
    if command -v k3s &>/dev/null; then
        sudo k3s ctr images import "$tar_file"
    elif command -v ctr &>/dev/null; then
        sudo ctr -n k8s.io images import "$tar_file"
    fi
}

# Import base image first (if present)
import_image "$BASE_TAR" "基础镜像 (hermes-agent-base.tar)"

# Import custom image
import_image "$IMAGE_TAR" "自定义镜像 (hermes-profile.tar)"

if [[ ! -f "$IMAGE_TAR" ]]; then
    echo "错误: 自定义镜像文件不存在: $IMAGE_TAR"
    exit 1
fi

echo ""
echo "已导入镜像:"
if command -v k3s &>/dev/null; then
    sudo k3s ctr images ls | grep hermes
elif command -v ctr &>/dev/null; then
    sudo ctr -n k8s.io images ls | grep hermes
fi

echo ""
echo "✓ 镜像导入完成"
