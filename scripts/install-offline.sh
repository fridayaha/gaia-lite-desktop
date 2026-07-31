#!/bin/bash
# ============================================================
# UnionAgents (知行) — 离线 ECS 一键部署脚本
# 适用环境: EulerOS 2.0 / CentOS 7+ / 完全离线（无外网）
# 用法: cd unionagents-offline-<version> && bash install-offline.sh
# ============================================================
set -euo pipefail

# ── 颜色 ──
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
err()   { echo -e "${RED}[ERR]${NC}  $1"; exit 1; }

VERSION="$(cat VERSION 2>/dev/null || echo 'v1.0.0')"
NAMESPACE="unionagents"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MANIFESTS="${SCRIPT_DIR}/manifests"
IMAGES="${SCRIPT_DIR}/images"
NODE_IP=""

echo "=========================================="
echo " UnionAgents 知行  v${VERSION}"
echo " 离线 ECS 一键部署"
echo "=========================================="
echo ""

# ── 0. 检查环境 ──
info "检查环境 ..."

if [ -f /etc/os-release ]; then
    . /etc/os-release
    info "  操作系统: ${PRETTY_NAME:-$ID}"
fi

# 获取本机 IP（兼容 EulerOS 无 grep -oP）
NODE_IP=$(ip -4 addr show | grep 'inet ' | grep -v '127.0.0.1' | awk '{print $2}' | cut -d/ -f1 | head -1)
if [ -z "$NODE_IP" ]; then
    NODE_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
fi
if [ -z "$NODE_IP" ]; then
    NODE_IP="127.0.0.1"
fi
info "  本机 IP: ${NODE_IP}"

# ── 1. 检查 / 安装 k3s ──
echo ""
info "[1/8] 检查 k3s ..."

K3S_DATA_DIR="/var/lib/rancher/k3s"

start_k3s() {
    info "  启动 k3s server (单节点) ..."
    mkdir -p "$K3S_DATA_DIR"
    nohup /usr/local/bin/k3s server \
        --disable=traefik \
        --disable=servicelb \
        --write-kubeconfig-mode=644 \
        --data-dir="$K3S_DATA_DIR" \
        --kubelet-arg="eviction-hard=imagefs.available<5%,nodefs.available<5%" \
        --kubelet-arg="eviction-minimum-reclaim=imagefs.available=5%,nodefs.available=5%" \
        > /var/log/k3s.log 2>&1 &
    info "  k3s 已启动 (PID: $!)"
    sleep 10
}

if command -v k3s &>/dev/null; then
    info "  k3s 已安装: $(k3s --version 2>/dev/null | head -1)"
    # 确保 k3s 正在运行
    if ! k3s kubectl get nodes &>/dev/null; then
        warn "  k3s 未运行，重新启动 ..."
        start_k3s
    fi
else
    warn "  k3s 未安装，查找离线包中的二进制 ..."

    K3S_BINARY=""
    for candidate in "${SCRIPT_DIR}/k3s" "$(dirname "${SCRIPT_DIR}")/k3s" /usr/local/bin/k3s; do
        if [ -f "$candidate" ]; then
            K3S_BINARY="$candidate"
            break
        fi
    done

    if [ -n "$K3S_BINARY" ]; then
        info "  找到 k3s 二进制: ${K3S_BINARY}"
        cp "$K3S_BINARY" /usr/local/bin/k3s
        chmod +x /usr/local/bin/k3s
    else
        err "无法获取 k3s 二进制。请将 k3s 二进制放入离线包根目录"
    fi

    start_k3s
fi

# 设置 kubeconfig
export KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"
if [ ! -f "$KUBECONFIG" ]; then
    KUBECONFIG="${HOME}/.kube/config"
fi
if [ ! -f "$KUBECONFIG" ]; then
    if [ -f /etc/rancher/k3s/k3s.yaml ]; then
        KUBECONFIG="/etc/rancher/k3s/k3s.yaml"
    else
        err "k3s config 未找到"
    fi
fi
export KUBECONFIG

info "  k3s 就绪: $(k3s kubectl get nodes 2>/dev/null | tail -1)"

# ── 2. 导入容器镜像 ──
echo ""
info "[2/8] 导入容器镜像到 k3s ..."

K3S_IMAGES_DIR="${K3S_DATA_DIR}/agent/images"
mkdir -p "$K3S_IMAGES_DIR"

image_count=0
# 先解压所有 tar.gz 到 k3s 镜像目录（k3s 重启时自动加载）
for img_gz in "${IMAGES}"/*.tar.gz; do
    [ -f "$img_gz" ] || continue
    name="$(basename "$img_gz")"
    info "  解压 ${name} ..."
    gunzip -c "$img_gz" > "${K3S_IMAGES_DIR}/${name%.gz}"
    image_count=$((image_count + 1))
done

# 通过 ctr 手动导入（更可靠，不依赖 k3s 重启自动扫描）
info "  通过 containerd ctr 导入镜像 ..."
for img_tar in "${K3S_IMAGES_DIR}"/*.tar; do
    [ -f "$img_tar" ] || continue
    name="$(basename "$img_tar")"
    k3s ctr -n k8s.io images import "$img_tar" 2>/dev/null && \
        info "  ✅ ${name}" || \
        warn "  ⚠️ ${name} 导入失败（可能已存在）"
done

info "  已处理 ${image_count} 个镜像"

# ── 3. 创建命名空间 ──
echo ""
info "[3/8] 创建命名空间 ..."
k3s kubectl apply -f "${MANIFESTS}/00-namespace.yaml" 2>/dev/null || true

# ── 4. 部署基础设施 ──
echo ""
info "[4/8] 部署基础设施 ..."
for f in "${MANIFESTS}/infra/"*.yaml; do
    name="$(basename "$f")"
    info "  ${name} ..."
    k3s kubectl apply -f "$f" -n "${NAMESPACE}"
done

# 等待 PostgreSQL 就绪
info "  等待 PostgreSQL 就绪 ..."
k3s kubectl wait --for=condition=ready pod -l app=postgres -n "${NAMESPACE}" --timeout=120s 2>/dev/null || \
    warn "  PostgreSQL 未就绪（可稍后检查）"

info "  等待 MinIO 就绪 ..."
k3s kubectl wait --for=condition=ready pod -l app=minio -n "${NAMESPACE}" --timeout=120s 2>/dev/null || \
    warn "  MinIO 未就绪（可稍后检查）"

# ── 5. 创建 LiteLLM 数据库 ──
echo ""
info "[5/8] 创建 LiteLLM 数据库 ..."
# LiteLLM 需要独立的 litellm 数据库（PostgreSQL 默认只创建 unionagents 库）
PG_POD=$(k3s kubectl get pod -l app=postgres -n "${NAMESPACE}" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")
if [ -n "$PG_POD" ]; then
    k3s kubectl exec -n "${NAMESPACE}" "${PG_POD}" -- \
        psql -U unionagents -d unionagents -c "CREATE DATABASE litellm;" 2>/dev/null && \
        info "  ✅ litellm 数据库已创建" || \
        info "  litellm 数据库已存在或跳过"
else
    warn "  PostgreSQL Pod 未找到，跳过 litellm 数据库创建"
fi

# ── 6. 部署后端服务 ──
echo ""
info "[6/8] 部署后端服务 ..."
for f in "${MANIFESTS}/services/"*.yaml; do
    name="$(basename "$f")"
    info "  ${name} ..."
    k3s kubectl apply -f "$f" -n "${NAMESPACE}"
done

# 等待后端就绪
for svc in manager gateway; do
    info "  等待 ${svc} 就绪 ..."
    k3s kubectl wait --for=condition=available deployment/"${svc}" -n "${NAMESPACE}" --timeout=120s 2>/dev/null || \
        warn "  ${svc} 未就绪（可稍后检查）"
done

# ── 7. 部署前端 ──
echo ""
info "[7/8] 部署前端应用 ..."
for f in "${MANIFESTS}/apps/"*.yaml; do
    name="$(basename "$f")"
    info "  ${name} ..."
    k3s kubectl apply -f "$f" -n "${NAMESPACE}"
done

for svc in console-admin enduser-portal; do
    info "  等待 ${svc} 就绪 ..."
    k3s kubectl wait --for=condition=available deployment/"${svc}" -n "${NAMESPACE}" --timeout=120s 2>/dev/null || \
        warn "  ${svc} 未就绪（可稍后检查）"
done

# ── 8. 加载 Hermes 引擎模板 ──
echo ""
info "[8/8] 加载 Hermes 引擎模板（Manager 动态使用）..."
k3s kubectl apply -f "${MANIFESTS}/60-hermes-engine.yaml" -n "${NAMESPACE}" 2>/dev/null || \
    warn "  Hermes 引擎模板加载失败（可稍后检查）"

# ── 输出访问信息 ──
echo ""
echo "=========================================="
echo " ✅ UnionAgents ${VERSION} 部署完成！"
echo "=========================================="
echo ""
echo "  管理后台:      http://${NODE_IP}:3000"
echo "  用户门户:      http://${NODE_IP}:3001"
echo "  Manager API:   http://${NODE_IP}:8002"
echo "  Gateway:       http://${NODE_IP}:8010"
echo "  LiteLLM:       http://${NODE_IP}:4000  (ClusterIP, 需 port-forward)"
echo "  Hub API:       http://${NODE_IP}:8003  (ClusterIP, 需 port-forward)"
echo ""
echo "  查看状态:"
echo "    k3s kubectl get pods -n ${NAMESPACE}"
echo "    k3s kubectl get svc -n ${NAMESPACE}"
echo ""
echo "  查看日志:"
echo "    k3s kubectl logs -n ${NAMESPACE} deployment/manager -f"
echo "    k3s kubectl logs -n ${NAMESPACE} deployment/gateway -f"
echo ""

# ── 当前 Pod 状态 ──
echo "── Pod 状态 ──"
k3s kubectl get pods -n "${NAMESPACE}" 2>/dev/null
