#!/bin/bash
# ============================================================
# UnionAgents (知行) 离线升级脚本（包内）
# 用法: cd unionagents-upgrade-xxx && bash upgrade.sh
# 前提: 已通过 install-offline.sh 部署过旧版本，k3s 运行中
# ============================================================
set -euo pipefail

# ── 颜色 ──
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
err()   { echo -e "${RED}[ERR]${NC}  $1"; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
IMAGES_DIR="${SCRIPT_DIR}/images"
MIGRATIONS_DIR="${SCRIPT_DIR}/migrations"
NAMESPACE="unionagents"
HUB_NAMESPACE="unionagents-hub"

# 读取版本信息
if [ -f "${SCRIPT_DIR}/VERSION" ]; then
    source "${SCRIPT_DIR}/VERSION"
    info "升级: ${FROM_TAG} → ${TO_VERSION} (${ARCH})"
    info "构建: ${BUILD_DATE} @ ${GIT_COMMIT}"
else
    err "找不到 VERSION 文件，请确认在升级包根目录执行"
fi

echo ""
echo "=========================================="
echo " UnionAgents 知行  升级"
echo "  ${FROM_TAG} → ${TO_VERSION}"
echo "  架构: ${ARCH}"
echo "=========================================="

# ── 0. 检查 k3s ──
info "[0/5] 检查 k3s ..."
if ! command -v k3s &>/dev/null; then
    err "k3s 未安装，请先通过安装包部署"
fi
export KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"
if [ ! -f "$KUBECONFIG" ]; then
    err "kubeconfig 未找到: $KUBECONFIG"
fi
info "  k3s 就绪: $(k3s kubectl get nodes 2>/dev/null | tail -1)"

# ── 1. 导入容器镜像 ──
echo ""
info "[1/5] 导入容器镜像 ..."

image_count=0
K3S_DATA_DIR="/var/lib/rancher/k3s"
K3S_IMAGES_DIR="${K3S_DATA_DIR}/agent/images"
mkdir -p "$K3S_IMAGES_DIR"

for img_gz in "${IMAGES_DIR}"/*.tar.gz; do
    [ -f "$img_gz" ] || continue
    name="$(basename "$img_gz")"
    info "  导入 ${name} ..."
    gunzip -c "$img_gz" > "${K3S_IMAGES_DIR}/${name%.gz}"
    k3s ctr -n k8s.io images import "${K3S_IMAGES_DIR}/${name%.gz}" 2>/dev/null && \
        info "  ✅ ${name}" || \
        warn "  ⚠️ ${name} 导入失败（可能已存在）"
    rm -f "${K3S_IMAGES_DIR}/${name%.gz}"  # 清理临时 tar
    image_count=$((image_count + 1))
done
info "  已导入 ${image_count} 个镜像"

# ── 2. 执行数据库迁移 ──
echo ""
info "[2/5] 执行数据库迁移 ..."

if [ -d "$MIGRATIONS_DIR" ] && [ -n "$(ls -A "$MIGRATIONS_DIR" 2>/dev/null)" ]; then
    PG_POD=$(k3s kubectl get pod -l app=postgres -n "$NAMESPACE" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")
    if [ -n "$PG_POD" ]; then
        for sql in "${MIGRATIONS_DIR}"/*.sql; do
            [ -f "$sql" ] || continue
            name="$(basename "$sql")"
            info "  执行 ${name} ..."
            k3s kubectl exec -n "$NAMESPACE" "$PG_POD" -- \
                psql -U unionagents -d unionagents -f - < "$sql" 2>&1 | grep -v "^$" || true
            info "  ✅ ${name}"
        done
    else
        warn "  PostgreSQL Pod 未找到，跳过迁移"
    fi
else
    info "  无数据库迁移"
fi

# ── 3. 更新 K8s manifests 中的镜像标签 ──
echo ""
info "[3/5] 更新 K8s 部署镜像标签 ..."

# 读取镜像列表
declare -a IMAGE_MAP_NAME=()
declare -a IMAGE_MAP_TAG=()
if [ -f "${SCRIPT_DIR}/IMAGES.txt" ]; then
    while IFS=' ' read -r name tag || [ -n "$name" ]; do
        [[ "$name" =~ ^# ]] && continue
        [ -z "$name" ] && continue
        IMAGE_MAP_NAME+=("$name")
        IMAGE_MAP_TAG+=("$tag")
    done < "${SCRIPT_DIR}/IMAGES.txt"
fi

# 对每个变化的镜像，执行 kubectl set image
rollout_count=0
for i in "${!IMAGE_MAP_NAME[@]}"; do
    name="${IMAGE_MAP_NAME[$i]}"
    tag="${IMAGE_MAP_TAG[$i]}"
    new_image="unionagents/${name}:${tag}"

    # 确定 deployment 名称
    case "$name" in
        console-admin)      deployment="console-admin"; ns="$NAMESPACE" ;;
        console-admin-hub)  deployment="console-admin-hub"; ns="$HUB_NAMESPACE" ;;
        console-enduser)    deployment="console-enduser"; ns="$NAMESPACE" ;;
        enduser-portal)     deployment="enduser-portal"; ns="$NAMESPACE" ;;
        engine-hermes)      info "  ${name}: 引擎模板镜像，跳过 rollout（Manager 动态管理）"; continue ;;
        llm-gateway)        deployment="llm-gateway"; ns="$NAMESPACE"; new_image="unionagents/litellm-custom:${tag}" ;;
        *)                  deployment="$name"; ns="$NAMESPACE" ;;
    esac

    info "  ${deployment} → ${new_image} ..."
    k3s kubectl set image deployment/"${deployment}" \
        "${deployment}=${new_image}" -n "${ns}" 2>/dev/null && \
        info "  ✅ ${deployment}" || \
        warn "  ⚠️ ${deployment} 更新失败（可能不存在）"
    rollout_count=$((rollout_count + 1))
done

# ── 4. 等待 rollout 完成 ──
echo ""
info "[4/5] 等待 Rollout 完成 ..."

for i in "${!IMAGE_MAP_NAME[@]}"; do
    name="${IMAGE_MAP_NAME[$i]}"
    case "$name" in
        console-admin)      deployment="console-admin"; ns="$NAMESPACE" ;;
        console-admin-hub)  deployment="console-admin-hub"; ns="$HUB_NAMESPACE" ;;
        console-enduser)    deployment="console-enduser"; ns="$NAMESPACE" ;;
        enduser-portal)     deployment="enduser-portal"; ns="$NAMESPACE" ;;
        engine-hermes)      continue ;;
        llm-gateway)        deployment="llm-gateway"; ns="$NAMESPACE" ;;
        *)                  deployment="$name"; ns="$NAMESPACE" ;;
    esac
    info "  等待 ${deployment} ..."
    k3s kubectl rollout status deployment/"${deployment}" -n "${ns}" --timeout=180s 2>/dev/null || \
        warn "  ⚠️ ${deployment} rollout 超时"
done

# ── 5. 输出状态 ──
echo ""
info "[5/5] 升级完成"
echo ""
echo "=========================================="
echo " ✅ UnionAgents 已升级到 ${TO_VERSION}"
echo "=========================================="
echo ""
echo "── 主命名空间 Pod 状态 ──"
k3s kubectl get pods -n "$NAMESPACE" 2>/dev/null
echo ""
echo "── Hub 命名空间 Pod 状态 ──"
k3s kubectl get pods -n "$HUB_NAMESPACE" 2>/dev/null
echo ""
echo "变更记录见 CHANGELOG.txt"
