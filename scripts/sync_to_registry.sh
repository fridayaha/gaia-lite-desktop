#!/bin/bash
# ============================================================
# UnionAgents (知行) 镜像仓库归档脚本
# 将所有构建和部署需要的镜像推送到 104 上的 Docker Registry
#
# 用法:
#   bash scripts/sync_to_registry.sh              # 推送本机已构建的应用镜像 + 拉取并推送基础镜像
#   bash scripts/sync_to_registry.sh --infra-only  # 只推送基础镜像
#   bash scripts/sync_to_registry.sh --app-only    # 只推送应用镜像
#
# 镜像仓库地址: 通过环境变量 REGISTRY_HOST 和 REGISTRY_PORT 配置
# 目录结构: <REGISTRY_HOST>:/root/projects/image (registry 数据卷)
# 命名规范:
#   基础镜像: <REGISTRY>:<PORT>/<原始名>:<原始tag>
#   应用镜像: <REGISTRY>:<PORT>/unionagents/<名称>:<版本>-<架构>
# ============================================================
set -euo pipefail

: "${REGISTRY_HOST:?REGISTRY_HOST 未设置，请 export REGISTRY_HOST=<镜像仓主机>}"
: "${REGISTRY_PORT:?REGISTRY_PORT 未设置，请 export REGISTRY_PORT=<端口>}"
REGISTRY="${REGISTRY_HOST}:${REGISTRY_PORT}"
ARCH_TAG="$(uname -m | sed 's/aarch64/arm64/;s/x86_64/amd64/')"

MODE="${1:---all}"

# ── 颜色 ──
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✅${NC} $1"; }
info() { echo -e "   $1"; }
warn() { echo -e "${YELLOW}⚠️${NC} $1"; }

echo "=========================================="
echo " UnionAgents 镜像仓库归档"
echo "  Registry: ${REGISTRY}"
echo "  架构: ${ARCH_TAG}"
echo "  模式: ${MODE}"
echo "=========================================="
echo ""

# ── 基础镜像列表（从 k8s manifests 中提取的全部外部镜像）──
INFRA_IMAGES=(
    "postgres:16-alpine"
    "minio/minio:latest"
    "redis:7-alpine"
    "ghcr.io/berriai/litellm-database:main-stable"
    # 监控栈
    "prom/prometheus:v3.2.1"
    "prom/alertmanager:v0.27.0"
    "prom/node-exporter:v1.9.1"
    "prom/blackbox-exporter:v0.25.0"
    "grafana/grafana:11.5.0"
    "grafana/loki:3.2.1"
    "grafana/promtail:3.2.1"
    "clickhouse/clickhouse-server:24.3"
    "gcr.io/cadvisor/cadvisor:v0.51.0"
    "registry.k8s.io/kube-state-metrics/kube-state-metrics:v2.14.0"
    # Langfuse
    "langfuse/langfuse:3"
    "langfuse/langfuse-worker:3"
    # Nginx（前端构建用）
    "nginx:stable-alpine"
    # Docker Registry 自身
    "registry:2"
)

# ── 应用镜像列表（unionagents/ 前缀）──
# 版本号优先用环境变量 APP_VERSION 传入（每日构建传 1.1.0-YYYYMMDD），
# 否则回退到 VERSION 文件（保留 dash 后缀，不截断）
APP_VERSION="${APP_VERSION:-$(cat VERSION 2>/dev/null || echo 'latest')}"
APP_IMAGES=(
    "unionagents/manager:${APP_VERSION}"
    "unionagents/engine-hermes:${APP_VERSION}"
    "unionagents/gateway:${APP_VERSION}"
    "unionagents/hub:${APP_VERSION}"
    "unionagents/litellm-custom:${APP_VERSION}"
    "unionagents/console-admin:${APP_VERSION}"
    "unionagents/console-enduser:${APP_VERSION}"
    "unionagents/enduser-portal:${APP_VERSION}"
    "unionagents/skill-secret-sidecar:${APP_VERSION}"
)

push_infra() {
    echo "[1/2] 推送基础镜像到 Registry ..."
    echo ""
    local count=0
    local failed=0
    for img in "${INFRA_IMAGES[@]}"; do
        local target="${REGISTRY}/${img}"
        # 检查 registry 中是否已有
        local name="${img%%:*}"
        local tag="${img##*:}"
        local encoded_name
        encoded_name=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$name', safe=''))" 2>/dev/null || echo "$name")
        local exists
        exists=$(curl -s "http://${REGISTRY}/v2/${name}/tags/list" 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print('$tag' in d.get('tags',[]))" 2>/dev/null || echo "False")
        if [ "$exists" = "True" ]; then
            info "⏭️  ${img} (已存在)"
            count=$((count + 1))
            continue
        fi

        echo "  拉取 ${img} ..."
        if docker pull "$img" 2>&1 | tail -1; then
            docker tag "$img" "$target"
            if docker push "$target" 2>&1 | tail -3; then
                ok "$img"
            else
                warn "$img 推送失败"
                failed=$((failed + 1))
            fi
            docker rmi "$target" 2>/dev/null || true
            count=$((count + 1))
        else
            warn "$img 拉取失败（可能需要配置镜像加速器）"
            failed=$((failed + 1))
        fi
    done
    echo ""
    echo "  基础镜像: ${count} 个成功, ${failed} 个失败"
    echo ""
}

push_app() {
    echo "[2/2] 推送应用镜像到 Registry ..."
    echo ""
    local count=0
    local failed=0
    for img in "${APP_IMAGES[@]}"; do
        # 检查本地是否有该镜像
        if ! docker image inspect "$img" &>/dev/null; then
            info "⏭️  ${img} (本地不存在，跳过)"
            continue
        fi

        local target="${REGISTRY}/${img}"
        echo "  推送 ${img} ..."
        docker tag "$img" "$target"
        if docker push "$target" 2>&1 | tail -3; then
            ok "$img"
            count=$((count + 1))
        else
            warn "$img 推送失败"
            failed=$((failed + 1))
        fi
        docker rmi "$target" 2>/dev/null || true
    done
    echo ""
    echo "  应用镜像: ${count} 个成功, ${failed} 个失败"
    echo ""
}

case "$MODE" in
    --infra-only) push_infra ;;
    --app-only)   push_app ;;
    --all|-all|*)
        push_infra
        push_app
        ;;
esac

# ── 汇总 ──
echo "=========================================="
echo " 镜像仓库归档完成"
echo "  Registry: http://${REGISTRY}"
echo ""
echo "  查看所有镜像:"
echo "    curl -s http://${REGISTRY}/v2/_catalog | python3 -m json.tool"
echo ""
echo "  查看某镜像的 tags:"
echo "    curl -s http://${REGISTRY}/v2/<image>/tags/list | python3 -m json.tool"
echo ""
echo "  数据目录: ${REGISTRY_HOST}:/root/projects/image"
echo "=========================================="
