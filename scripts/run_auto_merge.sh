#!/bin/bash
set -u
cd /root/union_agent
mkdir -p logs
LOGFILE="logs/auto-merge-$(date +%Y%m%d).log"
TS_START="$(date '+%Y-%m-%d %H:%M:%S')"
echo "===== Run started: $TS_START =====" >> "$LOGFILE"

OUTPUT="$(python3 scripts/auto_merge_prs.py 2>&1)"
RC=$?
echo "$OUTPUT" >> "$LOGFILE"

MERGED=$(echo "$OUTPUT" | grep -c 'MERGED successfully' || true)

if [ "$MERGED" -gt 0 ]; then
  echo "[SUMMARY] $(date '+%Y-%m-%d %H:%M:%S') — $MERGED PR(s) merged this run." >> "$LOGFILE"
fi

TS_END="$(date '+%Y-%m-%d %H:%M:%S')"
echo "===== Run ended: $TS_END | exit=$RC | merged=$MERGED =====" >> "$LOGFILE"
