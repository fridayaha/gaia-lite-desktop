#!/bin/bash
# ============================================================
# 将指定 email 的 Better Auth 用户提升为 admin（首次部署后必做）
#
# 背景：better-auth admin 插件 defaultRole="user"，新注册用户无管理权限。
# 本脚本通过 PG 直接把用户 role 改为 "admin"，破解"没有管理员就无法授权"的死锁。
#
# 用法:
#   bash scripts/make-first-admin.sh <email>
#   bash scripts/make-first-admin.sh admin@gaia.local
#
# 前置: 用户已通过 web-ui 注册（/api/auth/sign-up/email）
# 后续: 该用户重新登录后即拥有 admin 角色（JWT 带 roles=["admin"]）
# ============================================================
set -e

EMAIL="${1:?用法: bash scripts/make-first-admin.sh <email>}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PKG_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# 加载 .env.local 获取 PG 凭据
if [ -f "${PKG_DIR}/.env.local" ]; then
  set -a; source "${PKG_DIR}/.env.local"; set +a
fi

NAMESPACE="${NAMESPACE:-gaia}"
PG_USER="${GAIA_PG_USER:-ontology}"
PG_DATABASE="${GAIA_PG_DATABASE:-ontology}"

echo "=========================================="
echo "  提升用户为 admin"
echo "  Email: ${EMAIL}"
echo "=========================================="

# 检查用户是否存在
echo "[1/3] 检查用户是否存在 ..."
USER_INFO=$(kubectl exec -n "${NAMESPACE}" gaia-postgres-0 -- \
  psql -U "${PG_USER}" -d "${PG_DATABASE}" -t -c \
  "SELECT id, email, role FROM better_auth.\"user\" WHERE email = '${EMAIL}';" 2>/dev/null || true)

if [ -z "${USER_INFO}" ] || [ "${USER_INFO// /}" = "" ]; then
  echo "  ❌ 用户 ${EMAIL} 不存在"
  echo "  请先在 web-ui 注册该用户，再运行本脚本"
  echo "  注册地址: 访问前端 → 点击注册 → 填写邮箱密码"
  exit 1
fi

echo "  当前用户信息: ${USER_INFO}"

# 检查是否已经是 admin
CURRENT_ROLE=$(echo "${USER_INFO}" | awk -F'|' '{print $3}' | tr -d ' ')
if [ "${CURRENT_ROLE}" = "admin" ]; then
  echo "  ℹ️  用户 ${EMAIL} 已经是 admin，无需操作"
  exit 0
fi

# 提升为 admin
echo ""
echo "[2/3] 提升为 admin ..."
kubectl exec -n "${NAMESPACE}" gaia-postgres-0 -- \
  psql -U "${PG_USER}" -d "${PG_DATABASE}" -c \
  "UPDATE better_auth.\"user\" SET role = 'admin' WHERE email = '${EMAIL}';"

echo ""
echo "[3/3] 验证 ..."
NEW_INFO=$(kubectl exec -n "${NAMESPACE}" gaia-postgres-0 -- \
  psql -U "${PG_USER}" -d "${PG_DATABASE}" -t -c \
  "SELECT id, email, role FROM better_auth.\"user\" WHERE email = '${EMAIL}';")
echo "  更新后: ${NEW_INFO}"

echo ""
echo "✅ 用户 ${EMAIL} 已提升为 admin"
echo ""
echo "  请该用户重新登录（退出再登录），新 JWT 会带 roles=[\"admin\"]"
echo "  登录后即可在「身份管理」页面管理其他用户/群组/权限"
