#!/usr/bin/env bash
# ── Gaia 一键健康检查 ──
# 探活 docker-compose 全部组件 + API，输出全绿即部署完成。
#
# 用法：
#   bash scripts/healthcheck.sh                 # 自动适配：起的服务全绿即可
#   bash scripts/healthcheck.sh --no-optional   # 额外跳过可选服务（kestra/neo4j）
#
# 智能裁剪适配：通过 docker ps 检测容器是否在运行。
#   - 裁剪部署（如虚拟表只读场景省掉 doris/kafka/seatunnel）时，未起的服务
#     自动标记 ⊘ skipped (not deployed)，不报红。
#   - 已起的服务探活失败才报红。
#
# 退出码：0 = 已部署服务全绿；1 = 有已部署服务不健康。

set -uo pipefail

SKIP_OPTIONAL=false
[ "${1:-}" = "--no-optional" ] && SKIP_OPTIONAL=true

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; NC='\033[0m'
PASS=0; FAIL=0; SKIP=0

# 容器名 → 是否在运行
is_running() {
  docker ps --format '{{.Names}}' --filter "status=running" 2>/dev/null | grep -qx "$1"
}

# args: name  port  probe  container  [optional]
check() {
  local name="$1" port="$2" probe="$3" container="$4" optional="${5:-false}"
  if [ "$optional" = "true" ] && [ "$SKIP_OPTIONAL" = "true" ]; then
    printf "%-22s %s\n" "[$name]" "${YELLOW}⊘ skipped (optional)${NC}"; SKIP=$((SKIP+1)); return
  fi
  # 容器没起 → 视为裁剪跳过，不报红（裁剪部署的正常状态）
  if [ -n "$container" ] && ! is_running "$container"; then
    printf "%-22s %s\n" "[$name]" "${YELLOW}⊘ skipped (not deployed)${NC}"; SKIP=$((SKIP+1)); return
  fi
  # shellcheck disable=SC2086
  if eval "$probe" >/dev/null 2>&1; then
    printf "%-22s ${GREEN}✓ %-6s %s${NC}\n" "[$name]" "$port" "ok"
    PASS=$((PASS+1))
  else
    printf "%-22s ${RED}✗ %-6s %s${NC}\n" "[$name]" "$port" "unhealthy"
    FAIL=$((FAIL+1))
  fi
}

echo "Gaia 部署健康检查"
echo "────────────────────────────────────────"

check "PostgreSQL"        5432  "docker exec ontology-postgres pg_isready -U ontology" "ontology-postgres"
check "RustFS (S3)"       9000  "curl -sf http://localhost:9000/health" "ontology-rustfs"
check "Gravitino API"     8090  "curl -sf http://localhost:8090/api/health" "ontology-gravitino"
# Iceberg REST 不带 ?warehouse=（Gravitino 1.3.0 已知 404 缺陷）
check "Iceberg REST"      9001  "curl -sf http://localhost:9001/iceberg/v1/config" "ontology-gravitino"
check "Doris FE"          8030  "curl -sf http://localhost:8030/api/health" "ontology-doris-fe"
check "Doris BE"          8040  "curl -sf http://localhost:9050/api/health || curl -sf http://localhost:8040/api/health" "ontology-doris-be"
check "Trino"             8080  "curl -sf http://localhost:8080/v1/info" "ontology-trino"
check "Kafka"             9092  "docker exec ontology-kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --list" "ontology-kafka"
check "SeaTunnel cluster" 5801  "[ \$(curl -s http://localhost:5801/hazelcast/rest/maps/cluster-info | python3 -c 'import json,sys;print(len(json.load(sys.stdin).get(\"members\",[])))' 2>/dev/null || echo 0) -ge 2 ]" "ontology-seatunnel-master"
check "Better Auth"       3000  "curl -sf http://localhost:3000/health" "gaia-better-auth"
check "Kestra"            28080 "curl -sf http://localhost:28080/api/v1/configs" "ontology-kestra" true
check "Neo4j (graph)"     7687  "docker exec gaia-neo4j cypher-shell -u neo4j -p ${NEO4J_PASSWORD:-change-me} 'RETURN 1'" "gaia-neo4j" true
check "Gaia API"          8000  "curl -sf http://localhost:8000/health" "ontology-api"

echo "────────────────────────────────────────"
DEPLOYED=$((PASS + FAIL))
TOTAL=$((PASS + FAIL + SKIP))
if [ "$FAIL" -eq 0 ]; then
  printf "${GREEN}部署完成 ✓  %d/%d 已部署组件健康${NC}" "$PASS" "$DEPLOYED"
  [ "$SKIP" -gt 0 ] && printf "  ${YELLOW}(%d 跳过：裁剪/可选)${NC}" "$SKIP"
  echo ""
  echo "默认入口: http://localhost:5173 (前端) · http://localhost:8000/docs (API 文档)"
  exit 0
else
  printf "${RED}部署未完成 ✗  %d 健康 / %d 不健康 / %d 跳过${NC}\n" "$PASS" "$FAIL" "$SKIP"
  echo "排查：docker compose ps ; docker compose logs <服务名> | tail -50"
  exit 1
fi
