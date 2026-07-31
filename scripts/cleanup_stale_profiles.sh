#!/bin/bash
# ============================================================
# 一次性清理 stale profile 状态（修 INDEPENDENT hash 漂移后的残留）
#
# 背景：渠道默认 INDEPENDENT 改动后 profile_name hash 从 7555a4(USER_GROUP+group)
# 变为 cfd2a9(USER+user)，旧 7555a4 profile 行/目录/nginx 残留，与新 cfd2a9 共存
# 污染 → 端口泄漏 + 502。此脚本清空所有 profile 状态，让首次消息干净重建。
#
# 必须在新版 manager/gateway 部署后运行（含 fix(profile) 修复）。运行后重启 engine pod。
#
# 用法：在云服务器 ~/union_agent 下运行
#   bash scripts/cleanup_stale_profiles.sh
# 幂等：可重复运行（DELETE/UPDATE 都是清空）。
# ============================================================
set -euo pipefail
CI_DIR="$(cd "$(dirname "$0")/../deploy/ci" && pwd)"
if [ -f "${CI_DIR}/.env.local" ]; then set -a; source "${CI_DIR}/.env.local"; set +a; fi
NS="${K8S_NAMESPACE:-unionagents}"

: "${DB_HOST:?需 DB_HOST（deploy/ci/.env.local）}"
: "${DB_USER:?需 DB_USER}"
: "${DB_NAME:?需 DB_NAME}"
: "${DB_PASSWORD:?需 DB_PASSWORD}"

echo "=== [1/3] 清空 agent_profiles + 重置 internal_port_map ==="
PGPASSWORD="${DB_PASSWORD}" psql -h "${DB_HOST}" -U "${DB_USER}" -d "${DB_NAME}" <<'SQL'
BEGIN;
-- 解除 current_version 循环无关；直接清 profile 表
DELETE FROM agent_profiles;
-- 重置每个 deployment 的端口映射，next_port 回到 8644
UPDATE agent_deployments
SET internal_port_map = json_build_object('profiles', '{}'::jsonb, 'next_port', 8644)::jsonb;
COMMIT;
SELECT 'profiles=' || count(*) FROM agent_profiles
UNION ALL SELECT 'deployments_reset=' || count(*) FROM agent_deployments WHERE internal_port_map ? 'profiles';
SQL

echo "=== [2/3] 清理各 engine pod 的 profile 目录（保留 base）==="
for POD in $(kubectl get pods -n "${NS}" -o name | grep engine-hermes | sed 's|pod/||'); do
  echo "  pod ${POD}: rm /opt/data/profiles/* (keep base)"
  kubectl exec -n "${NS}" "${POD}" -- sh -c \
    'cd /opt/data/profiles && find . -maxdepth 1 -mindepth 1 -type d ! -name base -exec rm -rf {} +' 2>/dev/null || \
    echo "    (skip ${POD}: exec failed)"
done

echo "=== [3/3] 完成。接下来重启 engine pod 让 nginx 干净重建 ==="
echo "  kubectl rollout restart -n ${NS} -l app=engine-hermes  # 或逐个 delete pod"
echo "  首条消息会触发新版 ensure 重建 cfd2a9 profile（端口复用 + 幂等）。"
