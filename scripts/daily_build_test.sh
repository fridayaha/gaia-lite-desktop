#!/bin/bash
# ============================================================
# UnionAgents (知行) 每日构建单元测试
#
# 在 ARM 本机运行 manager + gateway + hub + hermes + sidecar 单元测试。
# 测试结果写入 JSON 文件供日报脚本读取。
#
# 用法:
#   bash scripts/daily_build_test.sh
#
# 依赖: .venv (Python 3.11+) + requirements-test.txt
#   首次运行前: uv venv .venv --python 3.11 && uv pip install -r requirements-test.txt
#
# 环境变量:
#   UA_TEST_DATABASE_URL — manager 测试库地址 (默认用 k3s postgres ClusterIP)
# ============================================================
set -eu

export PATH="/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH"

REPO_DIR="${REPO_DIR:-/root/union_agent}"
LOG_DIR="${REPO_DIR}/logs"
DATE=$(date +%Y%m%d)
mkdir -p "$LOG_DIR"

LOG_FILE="${LOG_DIR}/daily-test-${DATE}.log"
RESULT_FILE="${LOG_DIR}/daily-test-${DATE}.json"
TMP_OUTPUT="${LOG_DIR}/.pytest-output.tmp"

# ── 探测 k3s postgres ClusterIP（测试库） ──
PG_CLUSTER_IP=$(k3s kubectl get svc postgres -n unionagents -o jsonpath='{.spec.clusterIP}' 2>/dev/null || echo "")
if [ -z "$PG_CLUSTER_IP" ]; then
    echo "[$(date '+%H:%M:%S')] WARNING: k3s postgres ClusterIP 未找到，manager DB 测试将跳过" | tee -a "$LOG_FILE"
    export UA_TEST_DATABASE_URL=""
else
    export UA_TEST_DATABASE_URL="postgresql+asyncpg://unionagents:change-me@${PG_CLUSTER_IP}:5432/unionagents_test"
    # 确保测试库存在
    k3s kubectl exec -n unionagents postgres-0 -- psql -U unionagents -d unionagents -tAc "SELECT 1 FROM pg_database WHERE datname='unionagents_test'" 2>/dev/null | grep -q 1 || \
    k3s kubectl exec -n unionagents postgres-0 -- psql -U unionagents -d unionagents -c "CREATE DATABASE unionagents_test" 2>/dev/null || true
fi

VENV="${REPO_DIR}/.venv/bin/python"
if [ ! -f "$VENV" ]; then
    echo "[$(date '+%H:%M:%S')] .venv 不存在，创建中..." | tee -a "$LOG_FILE"
    cd "$REPO_DIR"
    uv venv .venv --python 3.11 2>&1 | tee -a "$LOG_FILE"
    uv pip install -r requirements-test.txt 2>&1 | tee -a "$LOG_FILE"
    uv pip install Pillow 2>&1 | tee -a "$LOG_FILE"
fi

# 确保 manager 运行时依赖已安装（zxcvbn-python 密码强度、alibabacloud-dysmsapi SMS 等）
# requirements-test.txt 只含测试框架，manager/requirements.txt 含运行时依赖
if ! "$VENV" -c "import zxcvbn" 2>/dev/null; then
    echo "[$(date '+%H:%M:%S')] 安装 manager 运行时依赖..." | tee -a "$LOG_FILE"
    uv pip install -r "${REPO_DIR}/services/manager/requirements.txt" 2>&1 | tee -a "$LOG_FILE"
fi

echo "[$(date '+%H:%M:%S')] === UnionAgents 每日单元测试 ===" | tee -a "$LOG_FILE"

# 初始化 JSON 结果
echo "{\"date\":\"${DATE}\",\"modules\":{}}" > "$RESULT_FILE"

# ── Python 解析器：从 pytest 输出提取结果并更新 JSON ──
update_result() {
    local name="$1"
    local exit_code="$2"
    local start_ts="$3"
    local end_ts="$4"

    "$VENV" "$REPO_DIR/scripts/_parse_pytest.py" \
        "$RESULT_FILE" "$name" "$exit_code" "$start_ts" "$end_ts" \
        < "$TMP_OUTPUT"
}

run_module() {
    local name="$1"
    local cmd="$2"
    local start_ts=$(date '+%H:%M:%S')
    echo "[$(date '+%H:%M:%S')] 运行 ${name} 测试..." | tee -a "$LOG_FILE"

    set +e
    eval "$cmd" > "$TMP_OUTPUT" 2>&1
    local exit_code=$?
    set -e

    local end_ts=$(date '+%H:%M:%S')
    tail -5 "$TMP_OUTPUT" | tee -a "$LOG_FILE"

    update_result "$name" "$exit_code" "$start_ts" "$end_ts"

    # 读取解析结果并打印
    local summary
    summary=$("$VENV" -c "
import json,sys
with open('$RESULT_FILE') as f:
    d=json.load(f)
m=d['modules'].get('$name',{})
print('{passed} passed, {failed} failed, {skipped} skipped, {errors} errors [{status}]'.format(**m))
" 2>&1)
    echo "[$(date '+%H:%M:%S')] ${name}: ${summary}" | tee -a "$LOG_FILE"
}

# ── 运行各模块测试 ──

# manager (需要 DB)
run_module "manager" \
    "cd ${REPO_DIR}/services/manager && ${VENV} -m pytest tests/ -q --tb=no -p no:cacheprovider"

# gateway (纯单元测试)
run_module "gateway" \
    "cd ${REPO_DIR}/services/gateway && ${VENV} -m pytest tests/ -q --tb=no -p no:cacheprovider"

# hub (sqlite, 无外部依赖)
run_module "hub" \
    "cd ${REPO_DIR}/services/hub/backend && DATABASE_URL=sqlite:///./hub_test.db PYTHONPATH=. ${VENV} -m pytest tests/ -q --tb=no -p no:cacheprovider"

# hermes
run_module "hermes" \
    "cd ${REPO_DIR} && PYTHONPATH=. ${VENV} -m pytest engines/hermes/tests/ -q --tb=no -p no:cacheprovider"

# sidecar
run_module "sidecar" \
    "cd ${REPO_DIR} && PYTHONPATH=. ${VENV} -m pytest services/skill-secret-sidecar/tests/ -q --tb=no -p no:cacheprovider"

# ── 汇总 ──
SUMMARY=$("$VENV" -c "
import json
with open('$RESULT_FILE') as f:
    d=json.load(f)
p=sum(m['passed'] for m in d['modules'].values())
f=sum(m['failed'] for m in d['modules'].values())
s=sum(m['skipped'] for m in d['modules'].values())
e=sum(m['errors'] for m in d['modules'].values())
print(f'{p} {f} {s} {e}')
")

TOTAL_PASS=$(echo "$SUMMARY" | cut -d' ' -f1)
TOTAL_FAIL=$(echo "$SUMMARY" | cut -d' ' -f2)
TOTAL_SKIP=$(echo "$SUMMARY" | cut -d' ' -f3)
TOTAL_ERR=$(echo "$SUMMARY" | cut -d' ' -f4)

echo "" | tee -a "$LOG_FILE"
echo "[$(date '+%H:%M:%S')] === 测试汇总 ===" | tee -a "$LOG_FILE"
echo "  通过: ${TOTAL_PASS}" | tee -a "$LOG_FILE"
echo "  失败: ${TOTAL_FAIL}" | tee -a "$LOG_FILE"
echo "  跳过: ${TOTAL_SKIP}" | tee -a "$LOG_FILE"
echo "  错误: ${TOTAL_ERR}" | tee -a "$LOG_FILE"

# 清理临时文件
rm -f "$TMP_OUTPUT"

if [ "$((TOTAL_FAIL + TOTAL_ERR))" -gt 0 ]; then
    echo "[$(date '+%H:%M:%S')] === 测试结果: FAILED ===" | tee -a "$LOG_FILE"
    exit 1
else
    echo "[$(date '+%H:%M:%S')] === 测试结果: PASSED ===" | tee -a "$LOG_FILE"
    exit 0
fi
