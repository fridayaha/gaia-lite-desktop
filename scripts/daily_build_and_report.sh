#!/bin/bash
# ============================================================
# UnionAgents (知行) 每日构建流水线
#
# 流程: 单元测试 → 构建归档 → 系统测试 → 发日报
# 任何环节失败都会在日报中体现，但日报只在全部流程结束后发送。
# 系统测试失败时仍发日报（报告失败原因），但标注整体状态为失败。
#
# crontab: 0 22 * * * /root/union_agent/scripts/daily_build_and_report.sh
# ============================================================
set -a
source /root/union_agent/.env.local
set +a

export PATH="/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH"

LOG_DIR="/root/union_agent/logs"
DATE=$(date +%Y%m%d)
mkdir -p "$LOG_DIR"

SCRIPTS_DIR="/root/union_agent/scripts"

echo "============================================"
echo " UnionAgents 每日构建流水线"
echo " 日期: $(date '+%Y-%m-%d %H:%M:%S')"
echo "============================================"

# ── 1. 单元测试 ──
echo "[$(date '+%H:%M:%S')] [1/4] 开始单元测试 ..."
bash "$SCRIPTS_DIR/daily_build_test.sh" 2>&1 | tee "$LOG_DIR/daily-test-${DATE}.log"
TEST_EXIT=${PIPESTATUS[0]}
echo "[$(date '+%H:%M:%S')] 单元测试结束，退出码: $TEST_EXIT"

if [ "$TEST_EXIT" -ne 0 ]; then
    echo "[$(date '+%H:%M:%S')] ⚠️ 单元测试有失败用例，继续构建归档"
fi

# ── 2. 构建归档 ──
echo "[$(date '+%H:%M:%S')] [2/4] 开始构建归档 ..."
bash "$SCRIPTS_DIR/daily_build_archive.sh" 2>&1 | tee "$LOG_DIR/daily-archive-${DATE}.log"
BUILD_EXIT=${PIPESTATUS[0]}
echo "[$(date '+%H:%M:%S')] 构建归档结束，退出码: $BUILD_EXIT"

if [ "$BUILD_EXIT" -ne 0 ]; then
    echo "[$(date '+%H:%M:%S')] ⚠️ 构建归档有失败，继续系统测试"
fi

# ── 3. 系统测试 (部署到 k3s + 冒烟测试) ──
echo "[$(date '+%H:%M:%S')] [3/4] 开始系统测试 ..."
bash "$SCRIPTS_DIR/daily_build_system_test.sh" 2>&1 | tee "$LOG_DIR/daily-systest-${DATE}.log"
SYSTEST_EXIT=${PIPESTATUS[0]}
echo "[$(date '+%H:%M:%S')] 系统测试结束，退出码: $SYSTEST_EXIT"

# ── 4. 汇总状态并发送日报 ──
echo "[$(date '+%H:%M:%S')] [4/4] 汇总状态 ..."

# 计算整体状态
OVERALL_STATUS="PASS"
if [ "$TEST_EXIT" -ne 0 ]; then
    OVERALL_STATUS="FAIL"
fi
if [ "$BUILD_EXIT" -ne 0 ]; then
    OVERALL_STATUS="FAIL"
fi
if [ "$SYSTEST_EXIT" -ne 0 ]; then
    OVERALL_STATUS="FAIL"
fi

# 写入流水线汇总 JSON (供日报脚本读取)
PIPELINE_FILE="$LOG_DIR/daily-pipeline-${DATE}.json"
cat > "$PIPELINE_FILE" << EOF
{
  "date": "$DATE",
  "pipeline": {
    "unit_test": {
      "exit_code": $TEST_EXIT,
      "status": "$([ $TEST_EXIT -eq 0 ] && echo 'pass' || echo 'fail')",
      "log": "daily-test-${DATE}.log"
    },
    "build_archive": {
      "exit_code": $BUILD_EXIT,
      "status": "$([ $BUILD_EXIT -eq 0 ] && echo 'pass' || echo 'fail')",
      "log": "daily-archive-${DATE}.log"
    },
    "system_test": {
      "exit_code": $SYSTEST_EXIT,
      "status": "$([ $SYSTEST_EXIT -eq 0 ] && echo 'pass' || echo 'fail')",
      "log": "daily-systest-${DATE}.log"
    }
  },
  "overall_status": "$OVERALL_STATUS"
}
EOF

echo "[$(date '+%H:%M:%S')] 整体状态: $OVERALL_STATUS"
echo "  单元测试: $([ $TEST_EXIT -eq 0 ] && echo '✅ PASS' || echo '❌ FAIL')"
echo "  构建归档: $([ $BUILD_EXIT -eq 0 ] && echo '✅ PASS' || echo '❌ FAIL')"
echo "  系统测试: $([ $SYSTEST_EXIT -eq 0 ] && echo '✅ PASS' || echo '❌ FAIL')"

# 发送日报 (无论成功失败都发送，日报内容体现各环节状态)
echo "[$(date '+%H:%M:%S')] 发送日报 ..."
/usr/bin/python3 /root/hermes_daily_report.py >> /root/hermes_daily.log 2>&1
echo "[$(date '+%H:%M:%S')] 日报发送完成"

echo "============================================"
echo " 流水线完成: $OVERALL_STATUS"
echo "============================================"
