#!/bin/bash
# 离线打包脚本 - 在有网机器上执行
# 将 Hermes 镜像（基础镜像 + 自定义镜像）和部署文件打包为离线安装包
# 目标机器只需 k3s，无需 Docker，无需外网
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
OUTPUT_DIR="${1:-${PROJECT_DIR}/offline-package}"
IMAGE_NAME="hermes-profile"
IMAGE_TAG="latest"
BASE_IMAGE="nousresearch/hermes-agent:latest"

echo "=== Hermes Agent K3s 离线打包 ==="
echo "目标: 生成可在无网环境部署的离线包"
echo ""
mkdir -p "$OUTPUT_DIR"

# 1. Pull base image (ensure latest)
echo "[1/6] 拉取基础镜像..."
docker pull "$BASE_IMAGE"

# 2. Build custom image
echo "[2/6] 构建自定义镜像..."
docker build -t "${IMAGE_NAME}:${IMAGE_TAG}" -f "${PROJECT_DIR}/Dockerfile.profile" "${PROJECT_DIR}"

# 3. Export both images
echo "[3/6] 导出镜像..."
docker save "${IMAGE_NAME}:${IMAGE_TAG}" -o "${OUTPUT_DIR}/${IMAGE_NAME}.tar"
docker save "$BASE_IMAGE" -o "${OUTPUT_DIR}/hermes-agent-base.tar"
echo "  自定义镜像: $(du -h "${OUTPUT_DIR}/${IMAGE_NAME}.tar" | cut -f1)"
echo "  基础镜像:   $(du -h "${OUTPUT_DIR}/hermes-agent-base.tar" | cut -f1)"
docker save "${IMAGE_NAME}:${IMAGE_TAG}" -o "${OUTPUT_DIR}/${IMAGE_NAME}.tar"
echo "  镜像大小: $(du -h "${OUTPUT_DIR}/${IMAGE_NAME}.tar" | cut -f1)"

# 4. Copy manifests
echo "[4/6] 复制部署文件..."
cp -r "${PROJECT_DIR}/k8s" "${OUTPUT_DIR}/k8s"
mkdir -p "${OUTPUT_DIR}/configs" "${OUTPUT_DIR}/scripts/lib"
cp -r "${PROJECT_DIR}/configs/"* "${OUTPUT_DIR}/configs/" 2>/dev/null || true
for f in k3s-deploy.sh offline-load.sh profile.sh profile-supervisor.sh; do
    [[ -f "${PROJECT_DIR}/scripts/$f" ]] && cp "${PROJECT_DIR}/scripts/$f" "${OUTPUT_DIR}/scripts/"
done
cp "${PROJECT_DIR}/scripts/lib/"*.sh "${OUTPUT_DIR}/scripts/lib/"
chmod +x "${OUTPUT_DIR}/scripts/"*.sh

# 5. Create install entry point
echo "[5/6] 创建安装入口..."
cat > "${OUTPUT_DIR}/install.sh" << 'INNER'
#!/bin/bash
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
echo "=== Hermes K3s 离线安装 ==="
echo ""

# Check k3s
if ! command -v k3s &>/dev/null; then
    echo "错误: k3s 未安装"
    echo "安装方法: curl -sfL https://get.k3s.io | sh -"
    echo ""
    echo "如果是离线环境，需要先在联网机器下载 k3s 离线包:"
    echo "  https://github.com/k3s-io/k3s/releases"
    exit 1
fi

# Import images
echo "[1/2] 导入镜像..."
bash "$DIR/scripts/offline-load.sh" "$DIR"

# Deploy
echo "[2/2] 部署 K8s 资源..."
bash "$DIR/scripts/k3s-deploy.sh" apply

echo ""
echo "✓ 安装完成!"
echo ""
echo "验证:"
echo "  kubectl get pods -n hermes"
echo "  curl http://<节点IP>:30642/v1/models"
echo ""
echo "Dashboard: http://<节点IP>:30119"
INNER
chmod +x "${OUTPUT_DIR}/install.sh"

# 6. Package
echo "[6/6] 打包..."
PKG="hermes-offline-$(date +%Y%m%d).tar.gz"
tar czf "${PROJECT_DIR}/${PKG}" -C "$(dirname "$OUTPUT_DIR")" "$(basename "$OUTPUT_DIR")"
echo ""
echo "✓ 离线包: ${PKG} ($(du -h "${PROJECT_DIR}/${PKG}" | cut -f1))"
echo ""
echo "使用方法:"
echo "  1. 传输到目标机器: scp ${PKG} user@target:/opt/"
echo "  2. 在目标机器解压: tar xzf ${PKG}"
echo "  3. 编辑 API Key:  vi offline-package/k8s/secret.yaml"
echo "  4. 执行安装:      cd offline-package && bash install.sh"
echo ""
echo "注意: 目标机器需要已安装 k3s 和配置好安全组 (开放 30642, 30119 端口)"
