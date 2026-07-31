#!/usr/bin/env bash
# 数据库正确性一键校验 — 对应 frontend-integration-test-plan.md §十四
# 用法: ./scripts/verify_db_consistency.sh
# 依赖: psql, mysql client (可选，连 Doris)
set -euo pipefail

PG_DSN="${PG_DSN:-postgresql://ontology:ontology@localhost:5432/ontology}"
DORIS_HOST="${DORIS_HOST:-127.0.0.1}"
DORIS_PORT="${DORIS_PORT:-9030}"
DORIS_USER="${DORIS_USER:-root}"

# psql/mysql 优先用本地客户端；缺失时回退到 docker compose exec 容器内客户端
psql_cmd=(psql)
mysql_cmd=(mysql)
if ! command -v psql >/dev/null 2>&1; then
  if docker compose ps postgres 2>/dev/null | grep -q postgres; then
    psql_cmd=(docker compose exec -T postgres psql)
    echo "  (本地无 psql，改用 docker compose exec postgres psql)"
  else
    echo "  ❌ 无 psql 且 postgres 容器未运行"; exit 2
  fi
fi
if ! command -v mysql >/dev/null 2>&1; then
  # 用 doris-fe 容器内的 mysql client（若有）
  if docker compose ps doris-fe 2>/dev/null | grep -q doris-fe; then
    mysql_cmd=(docker compose exec -T doris-fe mysql)
  fi
fi

# 包装：PG 查询
pg() { "${psql_cmd[@]}" "$PG_DSN" "$@"; }
# 包装：Doris 查询
doris() { "${mysql_cmd[@]}" -h "$DORIS_HOST" -P "$DORIS_PORT" -u "$DORIS_USER" "$@" 2>/dev/null; }

pass=0; fail=0
check() { # check <label> <expected_zero|nonzero> <actual_count>
  local label="$1" mode="$2" actual="$3"
  if [[ "$mode" == "zero" && "$actual" == "0" ]]; then
    echo "  ✅ $label (0 行，符合预期)"; pass=$((pass+1))
  elif [[ "$mode" == "nonzero" && "$actual" != "0" ]]; then
    echo "  ✅ $label ($actual 行，符合预期)"; pass=$((pass+1))
  else
    echo "  ❌ $label (实际 $actual 行，不符合预期)"; fail=$((fail+1))
  fi
}

echo "═══════════════════════════════════════════════════════════"
echo "  Gaia 数据库正确性校验 (PG + Doris)"
echo "  PG: $PG_DSN"
echo "  Doris: $DORIS_HOST:$DORIS_PORT"
echo "═══════════════════════════════════════════════════════════"

echo ""
echo "── R5: storage_type 取值（应仅 MANAGED/VIRTUAL，无 PHYSICAL）──"
pg -At -c "SELECT count(*) FROM object_types WHERE storage_type NOT IN ('MANAGED','VIRTUAL');" | { read n; check "storage_type 非 MANAGED/VIRTUAL 的行" zero "$n"; }
pg -c "SELECT storage_type, count(*) FROM object_types GROUP BY storage_type;"

echo ""
echo "── R5: datasets.kind 取值（应仅 MANAGED/VIRTUAL）──"
pg -At -c "SELECT count(*) FROM datasets WHERE kind NOT IN ('MANAGED','VIRTUAL');" | { read n; check "datasets.kind 非法值行" zero "$n"; }
pg -c "SELECT kind, count(*) FROM datasets GROUP BY kind;"

echo ""
echo "── R4: datasets 表无 object_type 反向外键（单向引用）──"
n=$(pg -At -c "SELECT count(*) FROM information_schema.columns WHERE table_name='datasets' AND column_name LIKE 'object_type%';")
check "datasets 含 object_type_* 列" zero "$n"

echo ""
echo "── R4: properties 物理映射列存在（dataset 关联落地）──"
pg -c "SELECT column_name FROM information_schema.columns WHERE table_name='properties' AND column_name LIKE 'physical_%' ORDER BY column_name;"

echo ""
echo "── 唯一性: 同一 ontology 下 object_type api_name 不重复 ──"
n=$(pg -At -c "SELECT count(*) FROM (SELECT ontology_id, api_name, count(*) c FROM object_types GROUP BY ontology_id, api_name HAVING count(*)>1) t;")
check "重复 (ontology_id, api_name)" zero "$n"

echo ""
echo "── R6: 配了 effects 的 execution 必有对应 outbox（原子事务）──"
n=$(pg -At -c "SELECT count(*) FROM (SELECT el.id FROM action_execution_logs el JOIN action_types at ON at.api_name=el.action_type_api_name WHERE el.status='COMPLETED' AND jsonb_array_length(COALESCE(at.rules->'effects','[]'::jsonb))>0 GROUP BY el.id HAVING count((SELECT 1 FROM outbox o WHERE o.action_execution_id=el.id))=0) t;")
check "配 effects 但缺 outbox（应为 0）" zero "$n"
echo "  注: 未配 effects 的 action 不产生 outbox 属正常（副作用可选）"

echo ""
echo "── outbox 终态分布（PENDING 不应长期堆积）──"
pg -c "SELECT status, count(*) FROM outbox GROUP BY status ORDER BY status;"

echo ""
echo "── R7: object_state 版本递增（read-your-writes 兜底表）──"
pg -c "SELECT count(*) AS total_state_rows, max(version) AS max_version FROM object_state;"

echo ""
echo "── R2: Doris 索引表清单（应仅 idx_* 前缀，无业务全量表）──"
if command -v mysql >/dev/null 2>&1 || docker compose ps doris-fe 2>/dev/null | grep -q doris-fe; then
  DORIS_DB="${DORIS_DB:-ontology}"
  echo "  idx_ 表清单 (库: $DORIS_DB):"
  doris -N -e "SHOW TABLES FROM $DORIS_DB LIKE 'idx_%'" | sed 's/^/    /' || echo "    (Doris 不可达或无 idx 表)"
  echo "  非 idx_ 业务表（应为空）:"
  n=$(doris -N -e "SHOW TABLES FROM $DORIS_DB" 2>/dev/null | grep -v '^idx_' | grep -c . || true)
  check "Doris 非 idx_ 业务表" zero "$n"
else
  echo "  ⚠️  未安装 mysql client 且 doris-fe 容器未运行，跳过 Doris 校验"
fi

echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  汇总: ✅ 通过 $pass  |  ❌ 失败 $fail"
echo "═══════════════════════════════════════════════════════════"
[[ "$fail" -eq 0 ]] && exit 0 || exit 1
