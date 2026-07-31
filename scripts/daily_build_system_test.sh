#!/bin/bash
# ============================================================
# UnionAgents (知行) 每日构建系统测试
#
# 将构建出的 Docker 镜像导入 k3s containerd，重启部署，
# 等待 Pod 就绪后执行冒烟测试（API 健康检查 + NodePort 可达性）。
# 测试结果写入 JSON 文件供日报脚本读取。
#
# 用法:
#   bash scripts/daily_build_system_test.sh
#
# 依赖: docker (构建镜像已存在), k3s
# 环境变量 (通过 .env.local 设置):
#   REPO_DIR — 仓库根目录 (默认 /root/union_agent)
# ============================================================
set -eu

export PATH="/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH"

REPO_DIR="${REPO_DIR:-/root/union_agent}"
LOG_DIR="${REPO_DIR}/logs"
DATE=$(date +%Y%m%d)
mkdir -p "$LOG_DIR"

LOG_FILE="${LOG_DIR}/daily-systest-${DATE}.log"
RESULT_FILE="${LOG_DIR}/daily-systest-${DATE}.json"

log() { echo "[$(date '+%H:%M:%S')] $1" | tee -a "$LOG_FILE"; }

# 镜像清单 (deployment 中使用的 :latest tag)
IMAGES=(
    "unionagents/manager:latest"
    "unionagents/gateway:latest"
    "unionagents/hub:latest"
    "unionagents/console-admin:latest"
    "unionagents/enduser-portal:latest"
    "unionagents/litellm-custom:latest"
)

NAMESPACE="unionagents"
# 需要检查就绪的 Deployment 列表
DEPLOYMENTS="manager gateway hub console-admin enduser-portal litellm"
# 健康检查: 服务名:端口:路径 (从主机通过 ClusterIP curl，Pod 内可能无 curl)
HEALTH_CHECKS=(
    "manager:8002:/docs"
    "gateway:8010:/docs"
    "hub:8003:/docs"
    "litellm:4000:/health"
)

log "=== UnionAgents 每日系统测试 ==="

# 初始化 JSON 结果
cat > "$RESULT_FILE" << EOF
{
  "date": "$DATE",
  "image_import": {},
  "pod_status": {},
  "pod_detail": [],
  "smoke_test": {},
  "overall": "pending"
}
EOF

UPDATE_JSON="$REPO_DIR/.venv/bin/python"
if [ ! -f "$UPDATE_JSON" ]; then
    UPDATE_JSON="/usr/bin/python3"
fi

# ── 0. 清理旧的非 Running Pod（Evicted/Completed/Error/Unknown）──
log "[0/5] 清理旧的异常 Pod ..."
CLEANUP_COUNT=0
# 清理 Evicted Pod
EVICTED=$(k3s kubectl get pods -n "$NAMESPACE" --field-selector=status.phase=Failed -o jsonpath='{.items[*].metadata.name}' 2>/dev/null || true)
if [ -n "$EVICTED" ]; then
    for pod in $EVICTED; do
        k3s kubectl delete pod "$pod" -n "$NAMESPACE" --force --grace-period=0 2>/dev/null || true
        CLEANUP_COUNT=$((CLEANUP_COUNT + 1))
    done
fi
# 清理 Completed 的旧 ReplicaSet Pod
COMPLETED=$(k3s kubectl get pods -n "$NAMESPACE" --field-selector=status.phase=Succeeded -o jsonpath='{.items[*].metadata.name}' 2>/dev/null || true)
if [ -n "$COMPLETED" ]; then
    for pod in $COMPLETED; do
        k3s kubectl delete pod "$pod" -n "$NAMESPACE" --force --grace-period=0 2>/dev/null || true
        CLEANUP_COUNT=$((CLEANUP_COUNT + 1))
    done
fi
# 清理旧 ReplicaSet（保留最新的）
k3s kubectl delete rs -n "$NAMESPACE" --field-selector=status.numberReplicas=0 2>/dev/null || true
log "  清理了 $CLEANUP_COUNT 个异常 Pod"

# ── 1. 确保 docker 运行 ──
log "[1/5] 检查 Docker ..."
if ! docker info &>/dev/null; then
    log "  Docker 未运行，启动中 ..."
    systemctl start docker 2>/dev/null || true
    sleep 3
    if ! docker info &>/dev/null; then
        log "  ❌ Docker 启动失败"
        "$UPDATE_JSON" -c "
import json
with open('$RESULT_FILE') as f: d=json.load(f)
d['overall']='fail'
d['error']='Docker not running'
with open('$RESULT_FILE','w') as f: json.dump(d,f,indent=2,ensure_ascii=False)
"
        exit 1
    fi
fi
log "  ✅ Docker 运行中"

# ── 2. 导入镜像到 k3s containerd ──
log "[2/5] 导入镜像到 k3s containerd ..."
IMPORT_FAIL=0
for img in "${IMAGES[@]}"; do
    short=$(echo "$img" | sed 's|unionagents/||;s|:latest||')
    if docker image inspect "$img" &>/dev/null; then
        log "  导入 $img ..."
        if docker save "$img" | k3s ctr images import - >/dev/null 2>&1; then
            log "    ✅ $short 导入成功"
            "$UPDATE_JSON" -c "
import json
with open('$RESULT_FILE') as f: d=json.load(f)
d['image_import']['$short']='pass'
with open('$RESULT_FILE','w') as f: json.dump(d,f,indent=2,ensure_ascii=False)
"
        else
            log "    ❌ $short 导入失败"
            IMPORT_FAIL=$((IMPORT_FAIL + 1))
            "$UPDATE_JSON" -c "
import json
with open('$RESULT_FILE') as f: d=json.load(f)
d['image_import']['$short']='fail'
with open('$RESULT_FILE','w') as f: json.dump(d,f,indent=2,ensure_ascii=False)
"
        fi
    else
        log "    ⚠️ $img 在 Docker 中不存在，跳过"
        "$UPDATE_JSON" -c "
import json
with open('$RESULT_FILE') as f: d=json.load(f)
d['image_import']['$short']='missing'
with open('$RESULT_FILE','w') as f: json.dump(d,f,indent=2,ensure_ascii=False)
"
    fi
done

if [ "$IMPORT_FAIL" -gt 0 ]; then
    log "  ⚠️ $IMPORT_FAIL 个镜像导入失败"
fi

# ── 3. 重启部署并等待 Pod 就绪 ──
log "[3/5] 重启 k3s 部署 ..."
k3s kubectl rollout restart deployment -n "$NAMESPACE" 2>&1 | tee -a "$LOG_FILE"

# 记录 Pod 明细到 JSON 的函数
record_pod_detail() {
    # 获取当前所有 Pod 的名称、READY 状态、STATUS、RESTARTS
    POD_INFO=$(k3s kubectl get pods -n "$NAMESPACE" --no-headers 2>/dev/null || true)
    "$UPDATE_JSON" << PYEOF
import json
with open('$RESULT_FILE') as f:
    d = json.load(f)
pod_detail = []
for line in """$POD_INFO""".strip().split('\n'):
    if not line.strip():
        continue
    parts = line.split()
    if len(parts) >= 3:
        name = parts[0]
        ready = parts[1] if len(parts) > 1 else "?"
        status = parts[2] if len(parts) > 2 else "?"
        restarts = parts[3] if len(parts) > 3 else "?"
        # 只记录 UnionAgents 服务 Pod（排除 postgres/minio 等基础设施）
        is_ua = any(svc in name for svc in ["manager", "gateway", "hub", "console-admin", "enduser-portal", "litellm"])
        if is_ua:
            is_ready = ready.startswith("1/1") or ready.startswith("2/2")
            pod_detail.append({
                "name": name,
                "ready": ready,
                "status": status,
                "restarts": restarts,
                "is_ready": is_ready
            })
d['pod_detail'] = pod_detail
with open('$RESULT_FILE', 'w') as f:
    json.dump(d, f, indent=2, ensure_ascii=False)
PYEOF
}

log "  等待 Pod 就绪 (最多 180s) ..."
WAIT_OK=0
for i in $(seq 1 36); do
    sleep 5
    # 使用 --no-headers + awk 精确统计，避免 jsonpath 多行问题
    POD_LINES=$(k3s kubectl get pods -n "$NAMESPACE" --no-headers 2>/dev/null || true)
    # 只统计 UnionAgents 服务 Pod（排除 postgres/minio 等基础设施）
    UA_LINES=$(echo "$POD_LINES" | grep -E 'manager|gateway|hub|console-admin|enduser-portal|litellm' || true)
    TOTAL=$(echo "$UA_LINES" | grep -c . || echo 0)
    READY=$(echo "$UA_LINES" | awk '$2 ~ /^1\/1$|^2\/2$/ {count++} END {print count+0}')
    log "    [$((i*5))s] UA Pod 就绪: ${READY}/${TOTAL}"
    if [ "$READY" -eq "$TOTAL" ] && [ "$TOTAL" -gt 0 ]; then
        WAIT_OK=1
        break
    fi
    # 超过一半时间还没就绪，打印当前状态帮助排查
    if [ "$i" -eq 12 ] || [ "$i" -eq 24 ]; then
        log "    当前 Pod 状态:"
        echo "$POD_LINES" | head -20 | while read -r line; do
            log "      $line"
        done
    fi
done

if [ "$WAIT_OK" -eq 0 ]; then
    log "  ⚠️ Pod 未全部就绪，记录详情并继续对已就绪 Pod 做冒烟测试"
    # 记录 Pod 明细到 JSON
    record_pod_detail
    # 打印当前 Pod 状态
    log "  当前 Pod 状态:"
    k3s kubectl get pods -n "$NAMESPACE" -o wide 2>&1 | tee -a "$LOG_FILE"
    "$UPDATE_JSON" -c "
import json
with open('$RESULT_FILE') as f: d=json.load(f)
d['pod_status']['ready']='timeout'
d['overall']='fail'
with open('$RESULT_FILE','w') as f: json.dump(d,f,indent=2,ensure_ascii=False)
"
    # 不再 exit 1，继续执行冒烟测试
else
    log "  ✅ 所有 UA Pod 就绪"
    record_pod_detail
    "$UPDATE_JSON" -c "
import json
with open('$RESULT_FILE') as f: d=json.load(f)
d['pod_status']['ready']='pass'
with open('$RESULT_FILE','w') as f: json.dump(d,f,indent=2,ensure_ascii=False)
"
fi

# ── 4. 冒烟测试 ──
log "[4/5] 冒烟测试 ..."
SMOKE_FAIL=0

# 4a. 服务 API 健康检查 (从主机通过 ClusterIP curl)
for check in "${HEALTH_CHECKS[@]}"; do
    svc=$(echo "$check" | cut -d: -f1)
    port=$(echo "$check" | cut -d: -f2)
    path=$(echo "$check" | cut -d: -f3)
    # 动态获取 ClusterIP
    cluster_ip=$(k3s kubectl get svc "$svc" -n "$NAMESPACE" -o jsonpath='{.spec.clusterIP}' 2>/dev/null)
    if [ -z "$cluster_ip" ]; then
        log "  ❌ $svc 无法获取 ClusterIP"
        SMOKE_FAIL=$((SMOKE_FAIL + 1))
        "$UPDATE_JSON" -c "
import json
with open('$RESULT_FILE') as f: d=json.load(f)
d['smoke_test']['${svc}_api']='fail'
d['smoke_test']['${svc}_http_code']='000'
d['smoke_test']['${svc}_error']='no ClusterIP'
with open('$RESULT_FILE','w') as f: json.dump(d,f,indent=2,ensure_ascii=False)
"
        continue
    fi
    url="http://${cluster_ip}:${port}${path}"
    log "  检查 $svc ($url) ..."
    http_code=$(curl -s -o /dev/null -w '%{http_code}' --connect-timeout 5 "$url" 2>/dev/null || echo "000")
    if [ "$http_code" -ge 200 ] && [ "$http_code" -lt 500 ]; then
        log "    ✅ $svc HTTP $http_code"
        "$UPDATE_JSON" -c "
import json
with open('$RESULT_FILE') as f: d=json.load(f)
d['smoke_test']['${svc}_api']='pass'
d['smoke_test']['${svc}_http_code']='$http_code'
with open('$RESULT_FILE','w') as f: json.dump(d,f,indent=2,ensure_ascii=False)
"
    else
        log "    ❌ $svc HTTP $http_code"
        SMOKE_FAIL=$((SMOKE_FAIL + 1))
        "$UPDATE_JSON" -c "
import json
with open('$RESULT_FILE') as f: d=json.load(f)
d['smoke_test']['${svc}_api']='fail'
d['smoke_test']['${svc}_http_code']='$http_code'
with open('$RESULT_FILE','w') as f: json.dump(d,f,indent=2,ensure_ascii=False)
"
    fi
done

# 4b. NodePort 可达性检查
for np in "admin:30080" "enduser:30081"; do
    name=$(echo "$np" | cut -d: -f1)
    port=$(echo "$np" | cut -d: -f2)
    log "  检查 NodePort $name ($port) ..."
    http_code=$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:$port/" 2>/dev/null || echo "000")
    if [ "$http_code" -ge 200 ] && [ "$http_code" -lt 500 ]; then
        log "    ✅ NodePort $name HTTP $http_code"
        "$UPDATE_JSON" -c "
import json
with open('$RESULT_FILE') as f: d=json.load(f)
d['smoke_test']['nodeport_${name}']='pass'
d['smoke_test']['nodeport_${name}_http_code']='$http_code'
with open('$RESULT_FILE','w') as f: json.dump(d,f,indent=2,ensure_ascii=False)
"
    else
        log "    ❌ NodePort $name HTTP $http_code"
        SMOKE_FAIL=$((SMOKE_FAIL + 1))
        "$UPDATE_JSON" -c "
import json
with open('$RESULT_FILE') as f: d=json.load(f)
d['smoke_test']['nodeport_${name}']='fail'
d['smoke_test']['nodeport_${name}_http_code']='$http_code'
with open('$RESULT_FILE','w') as f: json.dump(d,f,indent=2,ensure_ascii=False)
"
    fi
done

# ── 5. 汇总 ──
echo "" | tee -a "$LOG_FILE"

# 最终更新 pod_detail（可能在冒烟测试期间 Pod 状态变化）
record_pod_detail

if [ "$SMOKE_FAIL" -eq 0 ] && [ "$WAIT_OK" -eq 1 ]; then
    log "=== 系统测试结果: PASSED ==="
    "$UPDATE_JSON" -c "
import json
with open('$RESULT_FILE') as f: d=json.load(f)
d['overall']='pass'
with open('$RESULT_FILE','w') as f: json.dump(d,f,indent=2,ensure_ascii=False)
"
    exit 0
else
    FAIL_REASONS=""
    if [ "$WAIT_OK" -eq 0 ]; then
        FAIL_REASONS="Pod未就绪"
    fi
    if [ "$SMOKE_FAIL" -gt 0 ]; then
        if [ -n "$FAIL_REASONS" ]; then
            FAIL_REASONS="$FAIL_REASONS, "
        fi
        FAIL_REASONS="${FAIL_REASONS}冒烟测试${SMOKE_FAIL}项失败"
    fi
    log "=== 系统测试结果: FAILED ($FAIL_REASONS) ==="
    "$UPDATE_JSON" -c "
import json
with open('$RESULT_FILE') as f: d=json.load(f)
d['overall']='fail'
d['smoke_fail_count']=$SMOKE_FAIL
d['fail_reasons']='$FAIL_REASONS'
with open('$RESULT_FILE','w') as f: json.dump(d,f,indent=2,ensure_ascii=False)
"
    exit 1
fi
