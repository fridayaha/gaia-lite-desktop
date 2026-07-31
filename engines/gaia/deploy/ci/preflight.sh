#!/bin/bash
# ============================================================
# 部署前预检：检查集群环境 + 必填配置 + 官方镜像多架构支持
# 只警告不阻断（除必填项缺失），让用户知情后继续
#
# 用法: bash scripts/preflight.sh
# 依赖: kubectl, envsubst（已由 deploy.sh source .env.local 后调用）
# ============================================================

WARN=0
ERR=0

ok()   { echo "  ✅ $1"; }
warn() { echo "  ⚠️  $1"; WARN=$((WARN+1)); }
err()  { echo "  ❌ $1"; ERR=$((ERR+1)); }
info() { echo "  ℹ️  $1"; }

echo "========== Gaia 部署预检 =========="
echo ""

# 加载 .env.local（独立运行时；被 deploy.sh 调用时已 source 过，重复 source 无害）
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PKG_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
if [ -z "${GAIA_PG_USER:-}" ] && [ -f "${PKG_DIR}/.env.local" ]; then
  set -a; source "${PKG_DIR}/.env.local"; set +a
fi

# --- 1. 必填配置项 ---
echo "[1/5] 检查必填配置 ..."
for var in GAIA_PG_USER GAIA_PG_PASSWORD GAIA_PG_DATABASE \
           GAIA_S3_ACCESS_KEY GAIA_S3_SECRET_KEY GAIA_BETTER_AUTH_SECRET; do
  if [ -z "${!var:-}" ]; then
    err "未设置 ${var}（请在 .env.local 中填写）"
  fi
done
[ -n "${VERSION:-}" ] || err "VERSION 未设置（deploy.sh 参数）"
[ ${ERR} -eq 0 ] && ok "必填配置齐全" || { echo ""; echo "❌ 必填项有缺失，请修正 .env.local 后重试"; exit 1; }

# --- 2. kubectl + 集群 ---
echo ""
echo "[2/5] 检查 kubectl + 集群连通性 ..."
if ! command -v kubectl >/dev/null 2>&1; then
  err "kubectl 未安装"
  exit 1
fi
if kubectl cluster-info >/dev/null 2>&1; then
  ctx=$(kubectl config current-context 2>/dev/null || echo "unknown")
  ok "集群可达（context: ${ctx}）"
else
  err "kubectl 无法连接集群（检查 KUBECONFIG 或 context）"
  exit 1
fi

# namespace 是否可创建（幂等）
if kubectl get namespace "${NAMESPACE}" >/dev/null 2>&1; then
  info "namespace ${NAMESPACE} 已存在（将复用）"
else
  ok "namespace ${NAMESPACE} 可创建"
fi

# --- 3. 集群参数探测 ---
echo ""
echo "[3/5] 探测集群参数 ..."

# Pod CIDR（与 .env.local 的 POD_CIDR 比对；仅 full profile 部署 Doris 时需要）
if [ "${DEPLOY_PROFILE:-minimal}" = "full" ]; then
  DETECTED_CIDR=$(kubectl get nodes -o jsonpath='{.items[0].spec.podCIDR}' 2>/dev/null | head -1)
  if [ -n "${DETECTED_CIDR}" ]; then
    info "集群 Pod CIDR: ${DETECTED_CIDR}（配置值: ${POD_CIDR}）"
    [ "${DETECTED_CIDR}" != "${POD_CIDR}" ] && warn "配置 POD_CIDR=${POD_CIDR} 与集群实际 ${DETECTED_CIDR} 不符，Doris priority_networks 可能异常"
  else
    info "无法自动探测 Pod CIDR（多节点集群或字段为空），使用配置值 ${POD_CIDR}"
  fi
else
  info "profile=${DEPLOY_PROFILE:-minimal}，跳过 Pod CIDR 检查（Doris 不部署）"
fi

# StorageClass
if kubectl get storageclass "${STORAGE_CLASS}" >/dev/null 2>&1; then
  ok "StorageClass '${STORAGE_CLASS}' 存在"
else
  warn "StorageClass '${STORAGE_CLASS}' 不存在，可用: $(kubectl get sc -o jsonpath='{.items[*].metadata.name}' 2>/dev/null | tr ' ' ',')"
  warn "请在 .env.local 中设置 STORAGE_CLASS 为集群已有的 StorageClass"
fi

# NodePort k8s Service 占用
if kubectl get svc -A -o jsonpath='{range .items[*]}{.spec.ports[*].nodePort}{end}' 2>/dev/null | tr -s ' ' '\n' | grep -qw "${NODE_PORT_WEB_UI}"; then
  warn "NodePort ${NODE_PORT_WEB_UI} 已被占用，请在 .env.local 中改 NODE_PORT_WEB_UI"
else
  ok "NodePort ${NODE_PORT_WEB_UI} 可用"
fi

# 宿主机端口占用（hostNetwork Pod 或其他进程可能占用 NodePort 范围的宿主机端口）
if command -v ss >/dev/null 2>&1; then
  if ss -tlnp 2>/dev/null | grep -qw ":${NODE_PORT_WEB_UI} "; then
    warn "宿主机端口 ${NODE_PORT_WEB_UI} 已被进程占用（可能是 hostNetwork Pod 或其他服务），NodePort 可能冲突"
  else
    ok "宿主机端口 ${NODE_PORT_WEB_UI} 未被占用"
  fi
fi

# 多节点镜像分发检查
NODE_COUNT=$(kubectl get nodes -o jsonpath='{.items[*].metadata.name}' 2>/dev/null | wc -w)
if [ "${NODE_COUNT:-1}" -gt 1 ]; then
  info "集群有 ${NODE_COUNT} 个节点"
  if [ -z "${LOAD_IMAGES_NODES:-}" ]; then
    warn "多节点集群但未配置 LOAD_IMAGES_NODES，镜像只会导入本机"
    warn "未配置节点的 Pod 会报 ErrImageNeverPull，请在 .env.local 设置 LOAD_IMAGES_NODES"
  else
    NODES_CONFIGURED=$(echo "${LOAD_IMAGES_NODES}" | tr ',' '\n' | grep -c .)
    ok "配置了 ${NODES_CONFIGURED} 个 SSH 节点用于镜像分发"
  fi
fi

# --- 4. 集群架构 ---
echo ""
echo "[4/5] 探测集群架构 ..."
ARCHS=$(kubectl get nodes -o jsonpath='{.items[*].status.nodeInfo.architecture}' 2>/dev/null | tr ' ' '\n' | sort -u | tr '\n' ',')
info "集群节点架构: ${ARCHS}"
case "${ARCHS}" in
  *amd64,*arm64*|*arm64,*amd64*) info "混合架构集群" ;;
  *arm64*) info "arm64 集群（将检测官方镜像 arm64 支持）" ;;
  *amd64*) info "amd64 集群" ;;
esac

# --- 5. 官方镜像多架构支持（仅 arm64 集群时检测）---
echo ""
echo "[5/5] 检查官方镜像多架构支持 ..."
if ! echo "${ARCHS}" | grep -q "arm64"; then
  info "amd64 集群，跳过 arm64 检测"
elif ! command -v docker >/dev/null 2>&1; then
  info "docker 未安装，跳过镜像 manifest 检测（部署时 containerd 会自动拉取，失败再排障）"
else
  # minimal profile 只检查实际会拉取的镜像；full profile 检查全部
  if [ "${DEPLOY_PROFILE:-minimal}" = "full" ]; then
    OFFICIAL_IMAGES=(
      "apache/doris:fe-4.0.5" "apache/doris:be-4.0.5"
      "apache/gravitino:1.3.0" "apache/kafka:4.3.0" "apache/seatunnel:2.3.13"
      "ngosang/timescaledb-postgis:2.24.0-pg16-postgis3.6"
      "rustfs/rustfs:latest" "neo4j:5-community"
      "trinodb/trino:478" "nginx:alpine" "kestra/kestra:latest"
    )
  else
    # minimal profile 实际部署的官方镜像：PG / Gravitino / Trino / nginx（initContainer wait 用）
    OFFICIAL_IMAGES=(
      "ngosang/timescaledb-postgis:2.24.0-pg16-postgis3.6"
      "apache/gravitino:1.3.0"
      "trinodb/trino:478"
      "nginx:alpine"
    )
  fi
  info "检查 ${#OFFICIAL_IMAGES[@]} 个镜像（profile=${DEPLOY_PROFILE:-minimal}）..."
  for img in "${OFFICIAL_IMAGES[@]}"; do
    archs=$(docker manifest inspect "$img" 2>/dev/null | grep -oE '"architecture": "[^"]+"' | sort -u | tr '\n' ' ')
    if echo "$archs" | grep -q "arm64"; then
      ok "$img 支持 arm64"
    else
      warn "$img 无 arm64（arm64 节点将无法运行，需降级或禁用）"
    fi
  done
fi

echo ""
echo "========== 预检完成: ${ERR} 错误, ${WARN} 警告 =========="
[ ${ERR} -gt 0 ] && exit 1
exit 0
