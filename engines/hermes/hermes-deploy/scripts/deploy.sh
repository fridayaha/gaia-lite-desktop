#!/bin/bash
set -e

# Hermes Agent 云服务器部署 - 服务部署脚本
# 用途: 启动、停止、重启、查看 Hermes Agent 服务

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 检查 .env 文件
check_env() {
    if [ ! -f .env ]; then
        log_error ".env 文件不存在"
        log_info "请先运行: bash scripts/install.sh"
        exit 1
    fi
}

# 初始化部署
cmd_init() {
    log_info "初始化 Hermes Agent 部署环境..."

    check_env

    # 创建必要的目录
    mkdir -p backups logs skills

    # 拉取镜像
    log_info "拉取 Docker 镜像..."
    docker compose pull

    log_info "初始化完成 ✓"
}

# 启动服务
cmd_start() {
    log_info "启动 Hermes Agent 服务..."

    check_env

    # 检查服务是否已在运行
    if docker compose ps | grep -q "Up"; then
        log_warn "服务已在运行，如需重启请使用 restart 命令"
        exit 0
    fi

    # 启动服务
    docker compose up -d

    log_info "等待服务启动..."
    sleep 5

    # 显示状态
    cmd_status

    log_info "服务启动完成 ✓"
    log_info "Gateway API: http://localhost:${GATEWAY_PORT:-8642}"
    log_info "Dashboard UI: http://localhost:${DASHBOARD_PORT:-9119}"
}

# 停止服务
cmd_stop() {
    log_info "停止 Hermes Agent 服务..."

    docker compose down

    log_info "服务已停止 ✓"
}

# 重启服务
cmd_restart() {
    log_info "重启 Hermes Agent 服务..."

    cmd_stop
    sleep 2
    cmd_start
}

# 查看服务状态
cmd_status() {
    echo -e "${BLUE}========== 服务状态 ==========${NC}"
    docker compose ps
    echo ""

    echo -e "${BLUE}========== 资源使用 ==========${NC}"
    docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}"
    echo ""

    # 检查健康状态
    echo -e "${BLUE}========== 健康检查 ==========${NC}"
    if docker compose ps | grep "hermes-gateway" | grep -q "healthy"; then
        log_info "Gateway: 健康 ✓"
    else
        log_warn "Gateway: 未就绪或异常"
    fi
}

# 查看日志
cmd_logs() {
    local service=${1:-all}

    if [ "$service" = "all" ]; then
        log_info "查看所有服务日志 (Ctrl+C 退出)..."
        docker compose logs -f
    else
        log_info "查看 $service 日志 (Ctrl+C 退出)..."
        docker compose logs -f "$service"
    fi
}

# 执行命令
cmd_exec() {
    local service=${1:-hermes-gateway}
    shift

    if [ $# -eq 0 ]; then
        log_error "请指定要执行的命令"
        echo "用法: bash scripts/deploy.sh exec <service> <command>"
        echo "示例: bash scripts/deploy.sh exec hermes-gateway hermes skills list"
        exit 1
    fi

    log_info "在 $service 中执行命令..."
    docker compose exec "$service" "$@"
}

# 更新服务
cmd_update() {
    log_info "更新 Hermes Agent 服务..."

    # 备份数据
    log_info "备份数据..."
    bash scripts/backup.sh

    # 拉取最新镜像
    log_info "拉取最新镜像..."
    docker compose pull

    # 重建容器
    log_info "重建容器..."
    docker compose up -d --force-recreate

    log_info "更新完成 ✓"
}

# 清理服务
cmd_cleanup() {
    log_info "清理未使用的 Docker 资源..."

    docker compose down --volumes --remove-orphans
    docker system prune -f

    log_info "清理完成 ✓"
}

# 显示帮助
cmd_help() {
    echo "Hermes Agent 部署管理工具"
    echo ""
    echo "用法: bash scripts/deploy.sh <command> [args]"
    echo ""
    echo "命令:"
    echo "  init                初始化部署环境"
    echo "  start               启动服务"
    echo "  stop                停止服务"
    echo "  restart             重启服务"
    echo "  status              查看服务状态"
    echo "  logs [service]      查看日志 (默认查看所有)"
    echo "  exec <service> <cmd> 在服务中执行命令"
    echo "  update              更新服务到最新版本"
    echo "  cleanup             清理未使用的资源"
    echo "  help                显示此帮助信息"
    echo ""
    echo "示例:"
    echo "  bash scripts/deploy.sh start"
    echo "  bash scripts/deploy.sh logs hermes-gateway"
    echo "  bash scripts/deploy.sh exec hermes-gateway hermes skills list"
}

# 主函数
main() {
    local command=${1:-help}

    case "$command" in
        init)
            cmd_init
            ;;
        start)
            cmd_start
            ;;
        stop)
            cmd_stop
            ;;
        restart)
            cmd_restart
            ;;
        status)
            cmd_status
            ;;
        logs)
            cmd_logs "${2:-all}"
            ;;
        exec)
            shift
            cmd_exec "$@"
            ;;
        update)
            cmd_update
            ;;
        cleanup)
            cmd_cleanup
            ;;
        help|--help|-h)
            cmd_help
            ;;
        *)
            log_error "未知命令: $command"
            cmd_help
            exit 1
            ;;
    esac
}

main "$@"
