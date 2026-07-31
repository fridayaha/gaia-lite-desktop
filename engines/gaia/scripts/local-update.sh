#!/usr/bin/env bash
# ============================================================
# 本地 k3s 一键更新 api + web-ui（开发调试用）
#
# 流程：
#   api:   docker build → k3s ctr import → rollout restart → wait
#   web-ui: docker build → k3s ctr import → rollout restart → wait
#   port-forward: 重启 api(8000) + web-ui(5173) → 验证
#
# 用法：
#   bash scripts/local-update-webui.sh           # 默认 tag latest
#   bash scripts/local-update-webui.sh 0.1.0     # 指定 tag
#
# 前置：
#   - docker 可用（WSL2 下 --network host 绕开 bridge 外网不通）
#   - sudo 可用（k3s ctr images import 需 root）
#   - kubectl 已配置 k3s kubeconfig
#
# port-forward 用 setsid nohup 常驻，脚本负责杀掉旧进程 + 重启。
# 重启 WSL/Windows 后需重跑本脚本恢复 port-forward。
# ============================================================
set -euo pipefail

TAG="${1:-latest}"
NAMESPACE="gaia"

# 端口：api=8000（默认），web-ui=5173（对齐 pnpm dev 本地端口；勿碰 8080 留給 Trino）
API_PORT="${API_PORT:-8000}"
WEB_PORT="${WEB_PORT:-5173}"

PF_DIR="/tmp/gaia-pf"
mkdir -p "${PF_DIR}"
API_PF_LOG="${PF_DIR}/api-pf.log"
WEB_PF_LOG="${PF_DIR}/webui-pf.log"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
API_IMAGE="unionagents/gaia-api:${TAG}"
WEB_IMAGE="unionagents/gaia-web-ui:${TAG}"

red()   { echo -e "\033[31m$*\033[0m"; }
green() { echo -e "\033[32m$*\033[0m"; }
bold()  { echo -e "\033[1m$*\033[0m"; }

# ── 0. 关闭热重载残留（如果开着）────────────────────────
# dev-hotload-api.sh 会给 deploy/gaia-api 加 hostPath volume + 改启动命令挂宿主源码，
# 不关掉的话新镜像 rollout 后还是挂着宿主源码，等于白构建。
# dev-hotload-webui.sh 会在本地起 vite 占 5173，不关掉会和下面的 web-ui port-forward 冲突。
# 做成幂等：检测到才关，没开就静默跳过。

HOTLOAD_DIR="${REPO_ROOT}/scripts"

# 检测 api 是否开了热重载：deploy 里有 ontology-src volume 或 --reload 命令
API_HAS_HOTLOAD=""
if kubectl -n "${NAMESPACE}" get deploy gaia-api >/dev/null 2>&1; then
  VOL_COUNT=$(kubectl -n "${NAMESPACE}" get deploy gaia-api \
    -o jsonpath='{.spec.template.spec.volumes}' 2>/dev/null | grep -c 'ontology-src' || true)
  CMD_HAS_RELOAD=$(kubectl -n "${NAMESPACE}" get deploy gaia-api \
    -o jsonpath='{.spec.template.spec.containers[0].command}' 2>/dev/null | grep -c '\-\-reload' || true)
  if [ "${VOL_COUNT}" -gt 0 ] || [ "${CMD_HAS_RELOAD}" -gt 0 ]; then
    API_HAS_HOTLOAD=1
  fi
fi

if [ -n "${API_HAS_HOTLOAD}" ]; then
  echo "$(bold "▶ 0/6 检测到 api 热重载残留，先关闭（否则新镜像被宿主源码覆盖）")"
  if [ -x "${HOTLOAD_DIR}/dev-hotload-api.sh" ]; then
    bash "${HOTLOAD_DIR}/dev-hotload-api.sh" off >/dev/null 2>&1 || true
    green "   ✓ api 热重载已关闭"
  else
    # 兜底：直接 patch 移除（脚本不存在时）
    kubectl -n "${NAMESPACE}" patch deploy gaia-api --type=json -p='[
      {"op":"remove","path":"/spec/template/spec/volumes"},
      {"op":"remove","path":"/spec/template/spec/containers/0/volumeMounts"},
      {"op":"remove","path":"/spec/template/spec/containers/0/command"},
      {"op":"remove","path":"/spec/template/spec/containers/0/securityContext"}
    ]' 2>/dev/null || true
    kubectl -n "${NAMESPACE}" rollout status deploy/gaia-api --timeout=120s >/dev/null 2>&1 || true
    green "   ✓ api 热重载已关闭（patch 直移）"
  fi
else
  echo "$(bold "▶ 0/6 热重载检测：api 未开启，跳过")"
fi

# 杀本地 vite（dev-hotload-webui.sh 起的），释放 5173
# 用端口占用检测比 pgrep 更准（避免匹配到脚本/工具自身命令行）
VITE_LISTENING=""
if ss -tln 2>/dev/null | grep -q ":${WEB_PORT:-5173} "; then
  VITE_LISTENING=1
fi
if [ -n "${VITE_LISTENING}" ]; then
  echo "$(bold "▶ 0/6 检测到 5173 端口被占（多为本地 vite），关闭以释放")"
  # 精准杀 vite 主进程（匹配 node + vite CLI 路径，避免误杀脚本自身）
  pkill -f 'node.*/vite/bin/vite' 2>/dev/null || true
  for i in $(seq 1 10); do
    ss -tln 2>/dev/null | grep -q ":${WEB_PORT:-5173} " || break
    sleep 1
  done
  green "   ✓ 5173 端口已释放"
else
  echo "$(bold "▶ 0/6 热重载检测：本地 vite 未运行，跳过")"
fi

# ── 1. 构建镜像 ────────────────────────────────────────────

echo "$(bold "▶ 1/6 构建 api 镜像  ${API_IMAGE}")"
cd "${REPO_ROOT}"
docker build --network host -t "${API_IMAGE}" -f Dockerfile .

echo "$(bold "▶ 2/6 构建 web-ui 镜像  ${WEB_IMAGE}")"
# 构建上下文必须为仓库根，Dockerfile 里 COPY deploy/nginx/web-ui.conf 需要；另需 --no-cache 避免旧缓存干扰
docker build --no-cache --network host -t "${WEB_IMAGE}" -f src/web-ui/Dockerfile .

# ── 2. 导入 k3s containerd ────────────────────────────────

echo "$(bold "▶ 3/6 导入镜像到 k3s containerd（需 sudo）")"
# 三种方式提供 sudo 密码（优先级从高到低）：
#   1. 环境变量 SUDO_PASS=yourpassword（适合 CI / 脚本）
#   2. sudo 本身已配 NOPASSWD（sudo -n 成功）
#   3. 交互式输入（需 TTY）
SUDO_CMD="sudo"
if ! sudo -n true 2>/dev/null; then
  if [ -n "${SUDO_PASS:-}" ]; then
    SUDO_CMD="sudo -S"
  elif [ -t 0 ]; then
    read -rsp "sudo 密码: " SUDO_PASS
    echo ""
    SUDO_CMD="sudo -S"
  else
    echo "❌ sudo 需要密码，但当前非 TTY 且未设置 SUDO_PASS 环境变量"
    echo "   请重新运行: SUDO_PASS=yourpass bash scripts/local-update.sh"
    exit 1
  fi
fi
for img in "${API_IMAGE}" "${WEB_IMAGE}"; do
  if [ "${SUDO_CMD}" = "sudo -S" ]; then
    # 先输密码到 sudo（单独 stdin），再 pipe 镜像数据
    echo "${SUDO_PASS}" | sudo -S true 2>/dev/null
    docker save "${img}" | sudo k3s ctr images import -
  else
    docker save "${img}" | sudo k3s ctr images import -
  fi
done

# ── 3. 滚动更新 ────────────────────────────────────────────

echo "$(bold "▶ 4/6 滚动更新 deploy/gaia-api")"
kubectl rollout restart deploy/gaia-api -n "${NAMESPACE}"
kubectl rollout status deploy/gaia-api -n "${NAMESPACE}" --timeout=120s

echo "$(bold "▶ 5/6 滚动更新 deploy/gaia-web-ui")"
kubectl rollout restart deploy/gaia-web-ui -n "${NAMESPACE}"
kubectl rollout status deploy/gaia-web-ui -n "${NAMESPACE}" --timeout=120s

# ── 4. 重启 port-forward ──────────────────────────────────

echo "$(bold "▶ 6/6 重启 port-forward")"

# 杀掉所有 gaia 相关 port-forward 进程（按日志路径匹配，精准）
kill_gaia_pf() {
  local svc="$1"  # gaia-api or gaia-web-ui
  # 找监听目标端口 + gaia ns 的 kubectl port-forward 进程
  pkill -f "kubectl port-forward.*-n ${NAMESPACE}.*svc/${svc}" 2>/dev/null || true
}

# 等待端口可监听
wait_port() {
  local port="$1"
  for i in $(seq 1 15); do
    if ss -tln 2>/dev/null | grep -q ":${port} "; then break; fi
    sleep 1
  done
}

# ── api port-forward ──
kill_gaia_pf "gaia-api"
sleep 2
setsid nohup kubectl port-forward -n "${NAMESPACE}" svc/gaia-api "${API_PORT}:8000" \
  </dev/null >"${API_PF_LOG}" 2>&1 &
disown
wait_port "${API_PORT}"

# ── web-ui port-forward ──
kill_gaia_pf "gaia-web-ui"
sleep 2
setsid nohup kubectl port-forward -n "${NAMESPACE}" svc/gaia-web-ui "${WEB_PORT}:80" \
  </dev/null >"${WEB_PF_LOG}" 2>&1 &
disown
wait_port "${WEB_PORT}"

# ── 5. 验证 ────────────────────────────────────────────────

ok=0
echo ""
echo "$(bold "验证")"

for attempt in 1 2 3; do
  CODE_API="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:${API_PORT}/health" 2>/dev/null || echo "FAIL")"
  CODE_WEB="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:${WEB_PORT}/" 2>/dev/null || echo "FAIL")"
  if [ "${CODE_API}" = "200" ] && [ "${CODE_WEB}" = "200" ]; then
    ok=1
    break
  fi
  sleep 2
done

if [ "${ok}" = "1" ]; then
  green "✅ 全部就绪（tag=${TAG}）"
  echo ""
  echo "   $(bold "API")     http://localhost:${API_PORT}/       → /health /docs"
  echo "   $(bold "Web-UI")  http://localhost:${WEB_PORT}/       → 前端页面"
  echo ""
  echo "   日志：tail -f ${API_PF_LOG}  ${WEB_PF_LOG}"
  echo "   停止：pkill -f 'port-forward.*-n ${NAMESPACE}'"
else
  red "❌ 验证失败（api=${CODE_API} web-ui=${CODE_WEB}）"
  echo ""
  echo "   排查："
  echo "   kubectl -n ${NAMESPACE} logs -l app=gaia-api --tail=20"
  echo "   kubectl -n ${NAMESPACE} logs -l app=gaia-web-ui --tail=20"
  echo "   tail -20 ${API_PF_LOG}"
  echo "   tail -20 ${WEB_PF_LOG}"
  exit 1
fi
