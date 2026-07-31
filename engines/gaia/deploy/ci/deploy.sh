#!/bin/bash
# ============================================================
# Gaia 引擎 CI 部署脚本（build/deploy 分离版）
# 用法: bash scripts/deploy.sh <版本号>
# 示例: bash scripts/deploy.sh 0.1.0
#
# 前置：解压 gaia-deploy-<VERSION>.tar.gz 后在此目录执行
# 目录结构：
#   gaia-deploy-0.1.0/
#   ├── manifests/          # 清单模板（${VAR} 占位符）
#   ├── scripts/            # 本脚本 + preflight/load-images/envsubst-all
#   ├── images/             # 自有镜像 OCI tar（多架构）
#   ├── jars/               # 特殊 jar 依赖（可选）
#   ├── secret.yaml.template
#   └── .env.local.example
#
# 流程：load images → preflight → envsubst → kubectl apply → wait
# ============================================================
set -e

if [ $# -lt 1 ]; then
  echo "用法: bash scripts/deploy.sh <版本号>"
  echo "示例: bash scripts/deploy.sh 0.1.0"
  exit 1
fi

VERSION="$1"
NAMESPACE="gaia"
PKG_DIR="$(cd "$(dirname "$0")/.." && pwd)"   # 制品根目录（scripts/ 的上级）
SCRIPTS_DIR="${PKG_DIR}/scripts"

# 0. 加载本地敏感配置
if [ -f "${PKG_DIR}/.env.local" ]; then
  set -a; source "${PKG_DIR}/.env.local"; set +a
else
  echo "❌ 未找到 .env.local，请先 cp .env.local.example .env.local 并填写"
  exit 1
fi

# 变量加载校验（防止 source 异常导致变量丢失）
_missing_vars=()
for v in GAIA_PG_USER GAIA_PG_PASSWORD GAIA_PG_DATABASE GAIA_BETTER_AUTH_SECRET GAIA_TRUSTED_ORIGINS; do
  [ -n "${!v:-}" ] || _missing_vars+=("${v}")
done
if [ ${#_missing_vars[@]} -gt 0 ]; then
  echo "❌ .env.local 加载后以下变量仍为空：${_missing_vars[*]}"
  echo "   请检查 .env.local 格式（用 set -a; source; set +a 加载，不支持 xargs 方式）"
  exit 1
fi

# 集群参数默认值（k3s 默认；标准 k8s 在 .env.local 覆盖）
POD_CIDR="${POD_CIDR:-10.42.0.0/16}"
STORAGE_CLASS="${STORAGE_CLASS:-local-path}"
NODE_PORT_WEB_UI="${NODE_PORT_WEB_UI:-30082}"
IMAGE_PULL_POLICY_INFRA="${IMAGE_PULL_POLICY_INFRA:-IfNotPresent}"
IMAGE_PULL_POLICY_SERVICES="${IMAGE_PULL_POLICY_SERVICES:-Never}"   # 自有镜像已 import，不拉
K8S_RUNTIME="${K8S_RUNTIME:-k3s}"   # k3s | k8s
# 部署 profile：minimal（虚拟表/联邦查询，只起 PG+Gravitino+Trino+API+web-ui）
#           full（全量，含 Doris/RustFS/SeaTunnel/Kafka/Kestra/Neo4j）
DEPLOY_PROFILE="${DEPLOY_PROFILE:-minimal}"

# 切换集群
if [ -n "${KUBECONFIG_PATH:-}" ] && [ -f "${KUBECONFIG_PATH}" ]; then
  export KUBECONFIG="${KUBECONFIG_PATH}"
  echo "→ 使用集群: $(kubectl config current-context 2>/dev/null || echo 'unknown')"
fi

# export 占位符变量（envsubst 读取）
export VERSION NAMESPACE POD_CIDR STORAGE_CLASS NODE_PORT_WEB_UI DEPLOY_PROFILE
export IMAGE_PULL_POLICY_INFRA IMAGE_PULL_POLICY_SERVICES
# AI_MODEL 走 envsubst 渲染到 api.yaml（${GAIA_AI_MODEL}），默认 openai:gpt-4o
# 保持与本地 .env 默认值一致；未在 .env.local 配置时用默认
export GAIA_AI_MODEL="${GAIA_AI_MODEL:-openai:gpt-4o}"

echo "=========================================="
echo "  Gaia 引擎部署"
echo "  版本: ${VERSION}"
echo "  Namespace: ${NAMESPACE}"
echo "  Runtime: ${K8S_RUNTIME}"
echo "  Profile: ${DEPLOY_PROFILE}"
echo "=========================================="
echo ""

# 1. 预检
echo "[1/7] 预检 ..."
bash "${SCRIPTS_DIR}/preflight.sh"

# 2. 导入自有镜像到 containerd
# 设置 SKIP_LOAD_IMAGES=1 可跳过（镜像已在节点上时用）
echo ""
echo "[2/7] 导入自有镜像 ..."
if [ "${SKIP_LOAD_IMAGES:-0}" = "1" ]; then
  echo "  ⏭️  跳过镜像导入（SKIP_LOAD_IMAGES=1）"
elif [ -d "${PKG_DIR}/images" ] && ls "${PKG_DIR}/images"/gaia-*.tar >/dev/null 2>&1; then
  # load-images.sh 需 root；非 root 时用 sudo -E（保留环境变量传给子进程）
  export VERSION NAMESPACE LOAD_IMAGES_NODES SSH_PRIVATE_KEY SSH_PORT
  if [ "$(id -u)" -ne 0 ]; then
    sudo -E bash "${SCRIPTS_DIR}/load-images.sh" "${PKG_DIR}/images" "${K8S_RUNTIME}"
  else
    bash "${SCRIPTS_DIR}/load-images.sh" "${PKG_DIR}/images" "${K8S_RUNTIME}"
  fi
  # load-images.sh 退出码 2 表示部分节点失败（本机已成功），继续部署
  rc=$?
  [ ${rc} -eq 2 ] && echo "  ⚠️  部分节点镜像分发失败，继续部署（未分发节点上的 Pod 可能失败）"
  [ ${rc} -gt 2 ] && { echo "  ❌ 本机镜像导入失败"; exit 1; }
else
  echo "  ⚠️ 未找到 images/ 目录，跳过镜像导入（假设镜像已在节点上）"
fi

# 3. 渲染清单（envsubst 占位符 → 实际值）
echo ""
echo "[3/7] 渲染清单模板 ..."
RENDERED=$(mktemp -d)
trap 'rm -rf ${RENDERED}' EXIT
bash "${SCRIPTS_DIR}/envsubst-all.sh" "${PKG_DIR}/manifests" "${RENDERED}"

# 4. namespace + secret
echo ""
echo "[4/7] 创建 namespace + Secret ..."
kubectl apply -f "${RENDERED}/namespace.yaml" -n ${NAMESPACE}
# Secret 幂等化：已存在则保留（避免重部署覆盖 BETTER_AUTH_SECRET 导致所有 session 失效）
# 首次部署用 .env.local 的值创建；后续升级跳过，保留现有密钥
if kubectl get secret gaia-secret -n ${NAMESPACE} >/dev/null 2>&1; then
  echo "  ⏭️  Secret gaia-secret 已存在，保留现有值（不覆盖 BETTER_AUTH_SECRET）"
  echo "     如需更新密钥：kubectl delete secret gaia-secret -n ${NAMESPACE} 后重跑部署"
else
  # Secret 模板只替换 GAIA_* 变量（避免吞掉其他内容）
  GAIA_VARS='${GAIA_PG_USER} ${GAIA_PG_PASSWORD} ${GAIA_PG_DATABASE} ${GAIA_S3_ACCESS_KEY} ${GAIA_S3_SECRET_KEY} ${GAIA_DORIS_USER} ${GAIA_DORIS_PASSWORD} ${GAIA_NEO4J_PASSWORD} ${GAIA_BETTER_AUTH_SECRET} ${GAIA_PROVISION_TOKEN} ${GAIA_OPENAI_API_KEY} ${GAIA_OPENAI_BASE_URL} ${GAIA_DEEPSEEK_API_KEY} ${GAIA_ANTHROPIC_API_KEY} ${GAIA_GOOGLE_API_KEY} ${GAIA_MOONSHOT_API_KEY} ${GAIA_ALIBABA_API_KEY} ${GAIA_TRUSTED_ORIGINS}'
  envsubst "${GAIA_VARS}" < "${PKG_DIR}/secret.yaml.template" | kubectl apply -f - -n ${NAMESPACE}
  echo "  ✅ Secret gaia-secret 首次创建"
fi

# Secret 事后校验（防止占位符未替换 / 密钥过短）
check_secret() {
  local key="$1" min_len="$2" desc="$3"
  local val; val=$(kubectl get secret gaia-secret -n ${NAMESPACE} -o jsonpath="{.data.${key}}" 2>/dev/null | base64 -d 2>/dev/null || echo "")
  if [ -z "${val}" ]; then
    echo "  ❌ Secret key '${key}' (${desc}) 为空"
    return 1
  fi
  if [ -n "${min_len}" ] && [ ${#val} -lt ${min_len} ]; then
    echo "  ⚠️  Secret key '${key}' (${desc}) 长度 ${#val} < ${min_len}，可能未正确生成"
    return 1
  fi
  echo "  ✅ ${desc}: 已设置（长度 ${#val}）"
  return 0
}
check_secret better-auth-secret 32 "BETTER_AUTH_SECRET" || true
check_secret trusted-origins 0 "TRUSTED_ORIGINS" || true
echo "  ✅ namespace + Secret ready"

# 5. 基础设施
echo ""
echo "[5/7] 部署基础设施 ..."
# Job 资源 spec.template 不可变，已存在时需先删再建（gravitino-init）
kubectl delete job gaia-gravitino-init -n ${NAMESPACE} --ignore-not-found 2>/dev/null || true
# core/ 任何 profile 都部署（PG/Gravitino/Trino/secret）
kubectl apply -f "${RENDERED}/infra/core/" -n ${NAMESPACE}
if [ "${DEPLOY_PROFILE}" = "full" ]; then
  # optional/ 仅 full profile 部署（Doris/RustFS/SeaTunnel/Kafka/Kestra/Neo4j）
  kubectl apply -f "${RENDERED}/infra/optional/" -n ${NAMESPACE}
  echo "  ✅ infra applied（core + optional）"
else
  echo "  ✅ infra applied（core only，profile=${DEPLOY_PROFILE}）"
fi

# 等 PostgreSQL 就绪（migrate/api 前置）
echo "  等待 PostgreSQL 就绪 ..."
kubectl wait --for=condition=ready --timeout=180s pod -l app=gaia-postgres -n ${NAMESPACE}
echo "  ✅ PostgreSQL ready"

# 等 Gravitino metalake 初始化（Trino 依赖）
# Gravitino 镜像约 1.6GB，首次拉取可能较慢（国内 mirror 约 3~5 分钟）
echo "  等待 Gravitino metalake 初始化 Job 完成（镜像约 1.6GB，首次拉取可能较慢）..."
kubectl wait --for=condition=complete --timeout=300s job/gaia-gravitino-init -n ${NAMESPACE} || {
  echo "  ❌ gravitino-init Job 未在 300s 内完成，请检查: kubectl logs job/gaia-gravitino-init -n ${NAMESPACE}"
  exit 1
}
echo "  ✅ metalake ontology ready"

# 6. 后端服务
echo ""
echo "[6/7] 部署后端服务（migrate + api + better-auth）..."
# migrate Job：首次部署必须跑（建表）；后续升级也跑（alembic 幂等，保证 schema 最新）
# Job 资源 spec.template 不可变，已存在时需先删再建
kubectl delete job gaia-migrate -n ${NAMESPACE} --ignore-not-found 2>/dev/null || true
kubectl apply -f "${RENDERED}/services/" -n ${NAMESPACE}

echo "  等待 migrate Job 完成（alembic 业务表 + better_auth 认证表）..."
kubectl wait --for=condition=complete --timeout=300s job/gaia-migrate -n ${NAMESPACE} || {
  echo "  ❌ migrate Job 未在 300s 内完成，请检查: kubectl logs job/gaia-migrate -n ${NAMESPACE}"
  exit 1
}
echo "  ✅ migrate done（业务表 + better_auth 认证表）"

# 7. 前端 + 等待就绪
echo ""
echo "[7/7] 部署前端 + 等待就绪 ..."
kubectl apply -f "${RENDERED}/apps/" -n ${NAMESPACE}

# 镜像 tag 不变时 kubectl apply 不会触发滚动更新，需显式 rollout restart
# 方案 A：无条件 restart 所有自有镜像的 Deployment（简单可靠，中断约 5-15 秒/服务）
# 首次部署时 Deployment 刚创建，rollout restart 无副作用（kubectl 对新 Deployment 报 NotFound 忽略）
echo "  🔄 滚动重启自有镜像 Deployment（确保新镜像生效）..."
for deploy in gaia-api gaia-better-auth gaia-web-ui gaia-trino gaia-gravitino; do
  if kubectl get deployment -n ${NAMESPACE} "${deploy}" >/dev/null 2>&1; then
    kubectl rollout restart deployment/"${deploy}" -n ${NAMESPACE} 2>/dev/null && \
      echo "    ✅ rollout restart: ${deploy}"
  fi
done

# 等待核心服务就绪
kubectl wait --for=condition=available --timeout=180s deployment/gaia-api -n ${NAMESPACE}
kubectl wait --for=condition=available --timeout=120s deployment/gaia-web-ui -n ${NAMESPACE}
# Trino 镜像较大（~1.7GB），首次拉取可能较慢，给足超时
# minimal profile 的联邦查询依赖 Trino，必须等其就绪才算部署成功
echo "  等待 Trino 就绪（镜像约 1.7GB，首次拉取可能需要 5-10 分钟）..."
kubectl wait --for=condition=available --timeout=600s deployment/gaia-trino -n ${NAMESPACE} || {
  echo "  ⚠️  Trino 未在 600s 内就绪（可能仍在拉取镜像）"
  echo "  可手动查看: kubectl get pods -n ${NAMESPACE} -l app=gaia-trino"
  echo "  Trino 就绪后联邦查询功能才可用"
}

# 探测节点 IP（优先 ExternalIP，其次 InternalIP，最后 hostname）
NODE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="ExternalIP")].address}' 2>/dev/null)
[ -z "${NODE_IP}" ] && NODE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}' 2>/dev/null)
[ -z "${NODE_IP}" ] && NODE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="Hostname")].address}' 2>/dev/null)
[ -z "${NODE_IP}" ] && NODE_IP="<节点IP>"

# 8. Pod 调度状态检查 + 镜像分布提示（部署后验证）
# 检测是否有 Pod 处于非 Running/Completed 状态（可能是 ErrImageNeverPull）
BAD_PODS=$(kubectl get pods -n ${NAMESPACE} --no-headers 2>/dev/null | \
  grep -vE "Running|Completed" | head -10)
if [ -n "${BAD_PODS}" ]; then
  echo "  ⚠️  以下 Pod 未就绪（可能是镜像未分发到该节点）："
  echo "${BAD_PODS}" | sed 's/^/    /'
  echo ""
  echo "  若报 ErrImageNeverPull：镜像只在部分节点导入，需分发到 Pod 所在节点"
  echo "  方案 1（推荐）：在 .env.local 配置 LOAD_IMAGES_NODES，重跑部署自动分发"
  echo "  方案 2：手动在目标节点 ctr import 镜像"
  echo "  已导入镜像的节点：kubectl get nodes -l gaia-images=loaded"
else
  echo "  ✅ 所有 Pod 正常"
fi

echo ""
echo "✅ Gaia 部署完成"
echo ""
echo "  前端地址: http://${NODE_IP}:${NODE_PORT_WEB_UI}"
echo "  API:      http://${NODE_IP}:${NODE_PORT_WEB_UI}/health （经 web-ui nginx 反代）"
echo "  端口转发: kubectl port-forward -n ${NAMESPACE} svc/gaia-web-ui 8088:80"
echo ""
echo "查看状态: kubectl get pods -n ${NAMESPACE}"
echo "镜像节点: kubectl get nodes -l gaia-images=loaded"
