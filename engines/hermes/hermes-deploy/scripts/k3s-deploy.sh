#!/bin/bash
# K3s 部署管理脚本
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
NAMESPACE="hermes"
DEPLOYMENT="hermes-gateway"

log_info()  { echo -e "\033[0;32m[INFO]\033[0m $*" >&2; }
log_warn()  { echo -e "\033[1;33m[WARN]\033[0m $*" >&2; }
log_error() { echo -e "\033[0;31m[ERROR]\033[0m $*" >&2; }

get_kubectl() {
    if command -v kubectl &>/dev/null; then
        echo "kubectl"
    elif command -v k3s &>/dev/null; then
        echo "k3s kubectl"
    else
        log_error "未找到 kubectl 或 k3s"
        exit 1
    fi
}

KUBECTL="$(get_kubectl)"

cmd_install() {
    log_info "安装 k3s..."
    if command -v k3s &>/dev/null; then
        log_warn "k3s 已安装: $(k3s --version)"
    else
        curl -sfL https://get.k3s.io | sh -
        log_info "k3s 安装完成"
    fi
    cmd_apply
}

cmd_apply() {
    log_info "部署 Hermes 到 K3s..."

    # 先创建 namespace (idempotent)
    $KUBECTL create namespace "$NAMESPACE" --dry-run=client -o yaml | $KUBECTL apply -f -

    # 应用所有资源
    $KUBECTL apply -k "${PROJECT_DIR}/k8s/"

    log_info "等待 Pod 就绪..."
    $KUBECTL -n "$NAMESPACE" rollout status deployment/"$DEPLOYMENT" --timeout=180s

    echo ""
    cmd_status
}

cmd_delete() {
    log_info "删除 Hermes 部署..."
    $KUBECTL delete -k "${PROJECT_DIR}/k8s/" --ignore-not-found=true
    log_info "部署已删除"
}

cmd_status() {
    echo "=== Pods ==="
    $KUBECTL -n "$NAMESPACE" get pods -o wide
    echo ""
    echo "=== Services ==="
    $KUBECTL -n "$NAMESPACE" get svc
    echo ""
    echo "=== PVC ==="
    $KUBECTL -n "$NAMESPACE" get pvc
    echo ""
    echo "=== 访问地址 ==="
    NODE_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "<节点IP>")
    echo "  Gateway:   http://${NODE_IP}:30642"
    echo "  Dashboard: http://${NODE_IP}:30119"
}

cmd_logs() {
    local follow="${1:--f}"
    $KUBECTL -n "$NAMESPACE" logs "$follow" deployment/"$DEPLOYMENT" --all-containers=true
}

cmd_exec() {
    $KUBECTL -n "$NAMESPACE" exec -it deployment/"$DEPLOYMENT" -- bash
}

cmd_update() {
    log_info "滚动更新 Hermes..."
    $KUBECTL -n "$NAMESPACE" rollout restart deployment/"$DEPLOYMENT"
    $KUBECTL -n "$NAMESPACE" rollout status deployment/"$DEPLOYMENT" --timeout=180s
    log_info "更新完成"
}

cmd_help() {
    cat << 'HELP'
K3s 部署管理

用法: k3s-deploy.sh <command>

命令:
  install   安装 k3s 并部署 Hermes
  apply     部署/更新 K8s 资源
  delete    删除部署
  status    查看状态
  logs      查看日志 (支持 -f 参数)
  exec      进入容器
  update    滚动重启
  help      显示帮助
HELP
}

main() {
    local cmd="${1:-help}"
    shift || true
    case "$cmd" in
        install)  cmd_install ;;
        apply)    cmd_apply ;;
        delete)   cmd_delete ;;
        status)   cmd_status ;;
        logs)     cmd_logs "$@" ;;
        exec)     cmd_exec ;;
        update)   cmd_update ;;
        help|-h)  cmd_help ;;
        *)        log_error "未知命令: $cmd"; cmd_help; exit 1 ;;
    esac
}

main "$@"
