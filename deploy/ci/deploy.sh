#!/bin/bash
# ============================================================
# UnionAgents CI 一键部署脚本
# 用法: bash deploy/ci/deploy.sh <版本号>
# 示例: bash deploy/ci/deploy.sh 0.9.2
# 域名在 deploy/ci/.env.local 中配置（DOMAIN/REGISTRY/DB/OSS/PVC_STORAGE_CLASS 等）
# ============================================================
# 升级注意（v0.7.0 → 0.8.0）：旧云 DB 缺 group isolation 列（user_groups.code、
# 各资源表 group_id 等），首次部署 0.8.0 前必须先跑迁移（在已部署的 manager pod 内）：
#   kubectl exec -i -n unionagents deploy/manager -- env PYTHONPATH=/app python3 - \
#     < scripts/migrate_group_isolation.py
# 迁移幂等（col_exists 检查），重复执行无副作用。新集群（空 DB）无需此步，create_all 即可。
# ============================================================
set -e

if [ $# -lt 1 ]; then
  echo "用法: bash deploy/ci/deploy.sh <版本号>"
  echo "示例: bash deploy/ci/deploy.sh 0.9.2"
  echo "域名请在 deploy/ci/.env.local 中设置 DOMAIN= 变量"
  exit 1
fi

VERSION="$1"
NAMESPACE="unionagents"
CI_DIR="$(cd "$(dirname "$0")" && pwd)"

# 0. 加载本地敏感配置
if [ -f "${CI_DIR}/.env.local" ]; then
  set -a; source "${CI_DIR}/.env.local"; set +a
fi

# 对象存储 endpoint：默认本地 MinIO；云上在 .env.local 设 UA_MINIO_ENDPOINT 为 S3 兼容 endpoint（OSS/COS 等）
UA_MINIO_ENDPOINT="${UA_MINIO_ENDPOINT:-http://minio:9000}"
# 镜像仓库地址（仅主机名，命名空间 unionagents 由部署清单自动拼接）；云上在 .env.local 设为真实仓库
REGISTRY="${REGISTRY:-registry.example.com}"

# 切换到指定集群（如果有 KUBECONFIG_PATH）
if [ -n "${KUBECONFIG_PATH}" ] && [ -f "${KUBECONFIG_PATH}" ]; then
  export KUBECONFIG="${KUBECONFIG_PATH}"
  echo "→ 使用集群: $(kubectl config current-context 2>/dev/null || echo 'unknown')"
fi

echo "=========================================="
echo "  UnionAgents CI 部署"
echo "  版本: ${VERSION}"
echo "  域名: ${DOMAIN}"
echo "=========================================="
echo ""

# 校验必填项
if [ -z "${DOMAIN}" ]; then
  echo "❌ 未设置 DOMAIN"
  echo "   请在 deploy/ci/.env.local 中填写"
  exit 1
fi
if [ -z "${REGISTRY_USERNAME}" ] || [ -z "${REGISTRY_PASSWORD}" ]; then
  echo "❌ 未设置 REGISTRY_USERNAME / REGISTRY_PASSWORD"
  echo "   请在 deploy/ci/.env.local 中填写（参考 .env.local.example）"
  exit 1
fi
for var in DB_USER DB_PASSWORD DB_HOST JWT_SECRET OSS_ACCESS_KEY OSS_SECRET_KEY API_SERVER_KEY LITELLM_MASTER_KEY LITELLM_SALT_KEY API_KEY_HMAC_SECRET; do
  if [ -z "${!var}" ]; then
    echo "❌ 未设置 ${var}"
    echo "   请在 deploy/ci/.env.local 中填写"
    exit 1
  fi
done

# ASR key 可选（不配语音不可用，其他功能不受影响）
if [ -z "${ASR_VOLC_API_KEY}" ]; then
  echo "⚠️ 未设置 ASR_VOLC_API_KEY，语音识别不可用（其他功能不受影响）"
  echo "   如需语音，请在 deploy/ci/.env.local 中填写"
fi

# APK 发布 keystore 可选（不配 APP 管理的发布功能不可用，其他功能不受影响）
if [ -z "${RELEASE_KEYSTORE_PATH}" ] || [ ! -f "${RELEASE_KEYSTORE_PATH}" ]; then
  echo "⚠️ 未设置 RELEASE_KEYSTORE_PATH 或文件不存在，APP 管理的 APK 重签功能不可用（其他功能不受影响）"
  echo "   如需启用，请在 deploy/ci/.env.local 中填写并配置 KEYSTORE_ALIAS/KEYSTORE_PASSWORD/KEY_PASSWORD"
  export RELEASE_KEYSTORE_BASE64=""
  export KEYSTORE_ALIAS=""
  export KEYSTORE_PASSWORD=""
  export KEY_PASSWORD=""
else
  echo "✓ RELEASE_KEYSTORE_PATH=${RELEASE_KEYSTORE_PATH}，base64 编码后注入 secret"
  export RELEASE_KEYSTORE_BASE64="$(base64 -i "${RELEASE_KEYSTORE_PATH}" | tr -d '\n')"
  if [ -z "${KEYSTORE_ALIAS}" ] || [ -z "${KEYSTORE_PASSWORD}" ] || [ -z "${KEY_PASSWORD}" ]; then
    echo "❌ 设置了 RELEASE_KEYSTORE_PATH 但 KEYSTORE_ALIAS/KEYSTORE_PASSWORD/KEY_PASSWORD 不全"
    exit 1
  fi
fi

# 开始部署
echo "[0/7] 创建命名空间 + 配置镜像拉取凭据 ..."
kubectl create namespace ${NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -
kubectl create secret docker-registry registry-secret \
  --docker-server="${REGISTRY}" \
  --docker-username="${REGISTRY_USERNAME}" \
  --docker-password="${REGISTRY_PASSWORD}" \
  -n ${NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -

# 1. 基础设施（Secret）— 替换敏感变量后 apply
echo "[1/7] 创建基础设施 ..."
export DB_URL="postgresql+asyncpg://${DB_USER}:${DB_PASSWORD}@${DB_HOST}:5432/${DB_NAME}"
export LITELLM_DATABASE_URL="postgresql://${DB_USER}:${DB_PASSWORD}@${DB_HOST}:5432/litellm"
# hub 共用 unionagents database（psycopg 同步驱动，hub 用同步 SQLAlchemy）
export HUB_DATABASE_URL="postgresql+psycopg://${DB_USER}:${DB_PASSWORD}@${DB_HOST}:5432/${DB_NAME}"
# skill-engine 独立 database（同步 postgres 驱动；DB 由下方 1.55 幂等创建）
export SKILL_ENGINE_DATABASE_URL="postgresql://${DB_USER}:${DB_PASSWORD}@${DB_HOST}:5432/skill_engine"
envsubst < ${CI_DIR}/secret.yaml | kubectl apply -f - -n ${NAMESPACE}

# 1.5 幂等创建 litellm database（云 PG 为外部实例，用 psql 创建）
echo "[1.5/7] 确保 litellm database 存在 ..."
PGPASSWORD="${DB_PASSWORD}" psql -h "${DB_HOST}" -U "${DB_USER}" -d postgres -tc \
  "SELECT 1 FROM pg_database WHERE datname='litellm'" | grep -q 1 \
  || PGPASSWORD="${DB_PASSWORD}" psql -h "${DB_HOST}" -U "${DB_USER}" -d postgres -c "CREATE DATABASE litellm"
echo "  ✅ litellm database ready"

# 1.55 幂等创建 skill_engine database（skill-engine 独立库，表启动自动迁移）
echo "[1.55/7] 确保 skill_engine database 存在 ..."
PGPASSWORD="${DB_PASSWORD}" psql -h "${DB_HOST}" -U "${DB_USER}" -d postgres -tc \
  "SELECT 1 FROM pg_database WHERE datname='skill_engine'" | grep -q 1 \
  || PGPASSWORD="${DB_PASSWORD}" psql -h "${DB_HOST}" -U "${DB_USER}" -d postgres -c "CREATE DATABASE skill_engine"
echo "  ✅ skill_engine database ready"

# 1.6 部署 LiteLLM 模型网关（统一模型入口，引擎经此调用供应商）
echo "[1.6/7] 部署 LiteLLM 模型网关 ..."
sed "s|\${REGISTRY}|${REGISTRY}|g" ${CI_DIR}/litellm.yaml | kubectl apply -f - -n ${NAMESPACE}

# 1.9 TLS 证书 secret（admin/chat/landing 共用 unionagentservice-tls）
echo "[1.9/7] 确保 TLS 证书 secret 存在 ..."
if kubectl get secret unionagentservice-tls -n ${NAMESPACE} >/dev/null 2>&1; then
  echo "  ✓ unionagentservice-tls 已存在，跳过"
elif [ -n "${TLS_CERT_PATH}" ] && [ -n "${TLS_KEY_PATH}" ] && [ -f "${TLS_CERT_PATH}" ] && [ -f "${TLS_KEY_PATH}" ]; then
  kubectl create secret tls unionagentservice-tls \
    --cert="${TLS_CERT_PATH}" --key="${TLS_KEY_PATH}" \
    -n ${NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -
  echo "  ✓ unionagentservice-tls 已创建（来自 ${TLS_CERT_PATH}）"
else
  echo "  ⚠️ 未设置 TLS_CERT_PATH/TLS_KEY_PATH 或文件不存在，HTTPS 将使用自签证书"
  echo "     请在 .env.local 中配置 TLS_CERT_PATH 和 TLS_KEY_PATH（指向域名证书文件）"
fi

# 2. Ingress（替换域名+ingress class，chat 和管理台各一个）
echo "[2/7] 创建 Ingress ..."
INGRESS_CLASS="${INGRESS_CLASS:-nginx}"
sed "s/__DOMAIN__/${DOMAIN}/g; s/__INGRESS_CLASS__/${INGRESS_CLASS}/g" ${CI_DIR}/chat-ingress.yaml | kubectl apply -f - -n ${NAMESPACE}
sed "s/__DOMAIN__/${DOMAIN}/g; s/__INGRESS_CLASS__/${INGRESS_CLASS}/g" ${CI_DIR}/admin-ingress.yaml | kubectl apply -f - -n ${NAMESPACE}
sed "s/__DOMAIN__/${DOMAIN}/g; s/__INGRESS_CLASS__/${INGRESS_CLASS}/g" ${CI_DIR}/landing-ingress.yaml | kubectl apply -f - -n ${NAMESPACE}

# 3. 部署所有服务（替换版本号 + 镜像仓库 + 对象存储 endpoint + 域名用于 CORS 白名单）
echo "[3/7] 部署所有服务 ..."
sed "s|\${VERSION}|${VERSION}|g; s|\${REGISTRY}|${REGISTRY}|g; s|\${UA_MINIO_ENDPOINT}|${UA_MINIO_ENDPOINT}|g; s|\${UA_MINIO_REGION}|${UA_MINIO_REGION:-}|g; s|\${GRAFANA_EXTERNAL_URL}|${GRAFANA_EXTERNAL_URL:-}|g; s|__DOMAIN__|${DOMAIN}|g; s|__PVC_SC__|${PVC_STORAGE_CLASS:-local-path}|g" ${CI_DIR}/deployment.yaml | kubectl apply -f -
echo "  ✅ gateway, manager, hub, skill-engine, console-admin, enduser-portal, console-landing"

# 4. 等待后端就绪
echo "[4/7] 等待后端就绪 ..."
kubectl wait --for=condition=available --timeout=180s deployment/gateway -n ${NAMESPACE}
kubectl wait --for=condition=available --timeout=180s deployment/manager -n ${NAMESPACE}
kubectl wait --for=condition=available --timeout=180s deployment/hub -n ${NAMESPACE}
# skill-engine 非用户直连，但 manager 代理 /api/skill-engine/* 依赖它；best-effort 等待
kubectl wait --for=condition=available --timeout=120s deployment/skill-engine -n ${NAMESPACE} || echo "  ⚠️ skill-engine 未就绪（manager 代理技能工作室会 502）"

# 4.5 清理已废弃的独立 controller（controller 已并入 manager，新清单不再含它；
#     kubectl apply 是叠加式，不会自动删除存量 controller 资源，需显式清理）
echo "[4.5/7] 清理废弃的独立 controller ..."
kubectl delete deployment controller -n ${NAMESPACE} --ignore-not-found
kubectl delete service controller -n ${NAMESPACE} --ignore-not-found
kubectl delete serviceaccount controller -n ${NAMESPACE} --ignore-not-found 2>/dev/null || true

# 5. 等待前端就绪
echo "[5/7] 等待前端就绪 ..."
kubectl wait --for=condition=available --timeout=120s deployment/console-admin -n ${NAMESPACE}
kubectl wait --for=condition=available --timeout=120s deployment/enduser-portal -n ${NAMESPACE}
kubectl wait --for=condition=available --timeout=120s deployment/console-landing -n ${NAMESPACE}

echo "[6/7] 部署完成！"
echo ""
echo "  chat.${DOMAIN}  → 用户门户"
echo "  admin.${DOMAIN} → 管理后台"
echo "  ${DOMAIN}       → 产品主页"
echo ""
echo "查看状态: kubectl get pods -n ${NAMESPACE}"
