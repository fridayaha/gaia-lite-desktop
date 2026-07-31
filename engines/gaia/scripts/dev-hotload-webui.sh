#!/usr/bin/env bash
# ============================================================
# 前端热重载（开发调试用）—— Vite HMR，改前端代码 <1s 生效
#
# 原理：
#   前端不需要跑在 k3s 里。本地直接 vite dev server，享受 HMR（模块热替换）。
#   API 请求经 vite proxy 转发到 k3s 里的 gaia-api（通过 port-forward 8000）。
#   完全不动 web-ui Pod——它只是给「非开发访问」用的镜像版。
#
# 与 local-update.sh 的关系：
#   - 改前端代码 → 本脚本（<1s HMR）
#   - 验收前端构建产物/部署形态 → local-update.sh（慢但真实）
#
# 用法：
#   bash scripts/dev-hotload-webui.sh          # 起 vite dev (5173) + 确保 api port-forward
#   bash scripts/dev-hotload-webui.sh --api    # 同时确保后端也开热重载（调 dev-hotload-api.sh）
#
# 前置：
#   - src/web-ui 已 pnpm install（本地 node_modules）
#   - k3s 里 gaia-api 已部署（port-forward 8000 可达）
# ============================================================
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WEB_DIR="${ROOT}/src/web-ui"
API_PORT="${API_PORT:-8000}"
WEB_PORT="${WEB_PORT:-5173}"
NAMESPACE="gaia"
PF_DIR="/tmp/gaia-pf"
API_PF_LOG="${PF_DIR}/api-pf.log"
LOG_DIR="${ROOT}/.run-logs"
WEB_LOG="${LOG_DIR}/vite-dev.log"
mkdir -p "${PF_DIR}" "${LOG_DIR}"

red()   { echo -e "\033[31m$*\033[0m"; }
green() { echo -e "\033[32m$*\033[0m"; }
yellow() { echo -e "\033[33m$*\033[0m"; }
bold()  { echo -e "\033[1m$*\033[0m"; }

cd "${WEB_DIR}"

# ── 校验 node_modules ──────────────────────────────────────
if [ ! -d node_modules ]; then
  echo "$(bold "▶ 首次运行，pnpm install")"
  pnpm install --frozen-lockfile
fi

# ── 可选：连带开后端热重载 ──────────────────────────────────
if [ "${1:-}" = "--api" ]; then
  echo "$(bold "▶ 同时启用后端热重载")"
  bash "${ROOT}/scripts/dev-hotload-api.sh"
  echo ""
fi

# ── 确保 api port-forward（vite proxy 目标）────────────────
ensure_api_pf() {
  # 先验证现有 port-forward 是否真的通（rollout 后可能底层断连）
  if ss -tln 2>/dev/null | grep -q ":${API_PORT} " \
     && curl -s --max-time 2 "http://127.0.0.1:${API_PORT}/health" >/dev/null 2>&1; then
    return 0
  fi
  if ! kubectl -n "${NAMESPACE}" get svc gaia-api >/dev/null 2>&1; then
    red "❌ k3s 里没有 svc/gaia-api，先跑 local-update.sh 部署后端"
    exit 1
  fi
  echo "$(bold "▶ 启动 port-forward ${API_PORT} → svc/gaia-api")"
  pkill -f "kubectl port-forward.*-n ${NAMESPACE}.*svc/gaia-api" 2>/dev/null || true
  sleep 2
  setsid nohup kubectl port-forward -n "${NAMESPACE}" svc/gaia-api "${API_PORT}:8000" \
    </dev/null >"${API_PF_LOG}" 2>&1 &
  disown
  for i in $(seq 1 15); do
    ss -tln 2>/dev/null | grep -q ":${API_PORT} " && break
    sleep 1
  done
  # 等后端就绪
  for i in $(seq 1 20); do
    curl -s --max-time 2 "http://127.0.0.1:${API_PORT}/health" >/dev/null 2>&1 && return 0
    sleep 1
  done
  red "❌ 后端 ${API_PORT} 未就绪，看 ${API_PF_LOG}"
  exit 1
}
ensure_api_pf

# ── 释放 WEB_PORT 端口 ──────────────────────────────────────
# local-update.sh 会建 5173 → svc/gaia-web-ui 的 port-forward，占用 5173。
# 开发用本地 vite，不需要转发 k8s 的 web-ui Pod，杀掉它。
pkill -f "vite.*--port ${WEB_PORT}" 2>/dev/null || true
pkill -f "kubectl port-forward.*-n ${NAMESPACE}.*svc/gaia-web-ui" 2>/dev/null || true
# 端口释放需要时间，轮询等到 LISTEN 消失
for i in $(seq 1 10); do
  ss -tln 2>/dev/null | grep -q ":${WEB_PORT} " || break
  sleep 1
done

# ── 选定一个真正可用的端口（被占则往后找）──────────────────
# 注意：WSL2 mirrored 模式下 Windows 侧占用的端口会透传到 WSL，
# ss/proc/net/tcp 看不到占用者但 bind 仍失败；且可能是连续一段端口被占
# （如 5173~5200 全被占）。所以用真实 bind 探测而非 ss，并往后找足够远。
# 最多往后试 100 个端口（5173 → 5273），仍无可用则退出。
port_is_free() {
  python3 - "$1" <<'PY' 2>/dev/null
import socket, sys
port = int(sys.argv[1])
for fam, addr in ((socket.AF_INET, ('0.0.0.0', port)), (socket.AF_INET6, ('::', port))):
    s = socket.socket(fam, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(addr)
    except OSError:
        sys.exit(1)
    finally:
        s.close()
sys.exit(0)
PY
}

ACTUAL_PORT=""
for (( p=WEB_PORT; p<=WEB_PORT+100; p++ )); do
  if port_is_free "$p"; then
    ACTUAL_PORT="$p"
    break
  fi
done

if [ -z "${ACTUAL_PORT}" ]; then
  red "❌ 端口 ${WEB_PORT}~$((WEB_PORT+100)) 全部被占用，无法启动 vite"
  echo "   可手动清理后重试：pkill -f 'vite.*--port ${WEB_PORT}' 或换端口：WEB_PORT=5300 bash $0"
  exit 1
fi

if [ "${ACTUAL_PORT}" != "${WEB_PORT}" ]; then
  yellow "⚠️  端口 ${WEB_PORT} 被占用，改用 ${ACTUAL_PORT}"
fi

# ── 起 vite dev server ────────────────────────────────────
echo "$(bold "▶ 启动 Vite dev server（HMR，port ${ACTUAL_PORT}）")"
# --host 0.0.0.0 让 WSL2 下 Windows 宿主机也能访问（mirrored 模式直连）
# --strictPort: 端口被占直接失败（端口已由脚本探测确认可用，避免 vite 自行 +1 导致输出不一致）
setsid nohup npx vite --host 0.0.0.0 --port "${ACTUAL_PORT}" --strictPort \
  </dev/null >"${WEB_LOG}" 2>&1 &
disown

# ── 等 vite 就绪 ───────────────────────────────────────────
for i in $(seq 1 30); do
  if curl -s --max-time 2 "http://127.0.0.1:${ACTUAL_PORT}/" >/dev/null 2>&1; then
    green "✅ Vite dev server 已就绪"
    echo ""
    echo "   $(bold "Web-UI")  http://localhost:${ACTUAL_PORT}/    ← 改代码自动 HMR"
    echo "   $(bold "API")     http://localhost:${API_PORT}/    ← 经 vite proxy 转发到 k8s"
    echo ""
    echo "   vite 日志:  tail -f ${WEB_LOG}"
    echo "   api  日志:  kubectl -n ${NAMESPACE} logs -f deploy/gaia-api --tail=20"
    echo "   停止 vite:  pkill -f 'vite.*${WEB_DIR##*/}'   # 按目录名匹配，兼容端口变化"
    exit 0
  fi
  sleep 1
done

red "❌ Vite 启动失败"
echo "   排查: tail -30 ${WEB_LOG}"
exit 1
