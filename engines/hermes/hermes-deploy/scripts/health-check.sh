#!/bin/bash

# Hermes Agent 云服务器部署 - 健康检查脚本
# 用途: 检查服务状态、资源使用、数据卷、网络连接

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
source "${SCRIPT_DIR}/lib/common.sh"
cd "$PROJECT_DIR"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 状态码
STATUS_OK=0
STATUS_WARNING=1
STATUS_ERROR=2

# 检查结果收集
declare -a CHECK_RESULTS
OVERALL_STATUS=$STATUS_OK

log_check() {
    local status=$1
    local message=$2

    case $status in
        $STATUS_OK)
            CHECK_RESULTS+=("${GREEN}✓${NC} $message")
            ;;
        $STATUS_WARNING)
            CHECK_RESULTS+=("${YELLOW}⚠${NC} $message")
            if [ $OVERALL_STATUS -eq $STATUS_OK ]; then
                OVERALL_STATUS=$STATUS_WARNING
            fi
            ;;
        $STATUS_ERROR)
            CHECK_RESULTS+=("${RED}✗${NC} $message")
            OVERALL_STATUS=$STATUS_ERROR
            ;;
    esac
}

# 检查 Docker 服务
check_docker() {
    if ! docker info &> /dev/null; then
        log_check $STATUS_ERROR "Docker 服务未运行"
        return
    fi

    log_check $STATUS_OK "Docker 服务正常运行"
}

# 检查容器状态
check_containers() {
    local gateway_status=$(docker compose ps hermes-gateway --format "{{.Status}}" 2>/dev/null)

    # Gateway 检查 (含 Dashboard，同一容器)
    if echo "$gateway_status" | grep -q "Up"; then
        if echo "$gateway_status" | grep -q "healthy"; then
            log_check $STATUS_OK "Gateway 容器运行正常 (健康, 含 Dashboard)"
        else
            log_check $STATUS_WARNING "Gateway 容器运行中但健康检查未通过"
        fi
    else
        log_check $STATUS_ERROR "Gateway 容器未运行"
    fi
}

# 检查资源使用
check_resources() {
    # CPU 和内存使用
    local stats=$(docker stats --no-stream --format "{{.Name}}: CPU={{.CPUPerc}} MEM={{.MemUsage}} ({{.MemPerc}})")

    if [ -z "$stats" ]; then
        log_check $STATUS_ERROR "无法获取容器资源使用信息"
        return
    fi

    # 检查内存使用率
    local gateway_mem=$(docker stats --no-stream hermes-gateway --format "{{.MemPerc}}" 2>/dev/null | sed 's/%//')

    if [ -n "$gateway_mem" ]; then
        if (( $(_calc_gt "$gateway_mem" 90) )); then
            log_check $STATUS_ERROR "Gateway 内存使用率过高: ${gateway_mem}%"
        elif (( $(_calc_gt "$gateway_mem" 80) )); then
            log_check $STATUS_WARNING "Gateway 内存使用率较高: ${gateway_mem}%"
        else
            log_check $STATUS_OK "Gateway 内存使用正常: ${gateway_mem}%"
        fi
    fi
}

# 检查磁盘空间
check_disk() {
    local disk_usage=$(df -h . | tail -1 | awk '{print $5}' | sed 's/%//')

    if [ "$disk_usage" -gt 90 ]; then
        log_check $STATUS_ERROR "磁盘使用率过高: ${disk_usage}%"
    elif [ "$disk_usage" -gt 80 ]; then
        log_check $STATUS_WARNING "磁盘使用率较高: ${disk_usage}%"
    else
        log_check $STATUS_OK "磁盘空间充足: ${disk_usage}% 已使用"
    fi

    # 检查 Docker 数据卷大小
    local volume_size=$(docker system df -v 2>/dev/null | grep "hermes-data" | awk '{print $3}' || echo "N/A")
    log_check $STATUS_OK "数据卷大小: $volume_size"
}

# 检查日志大小
check_logs() {
    local log_dir="/var/lib/docker/containers"

    if [ ! -d "$log_dir" ]; then
        log_check $STATUS_WARNING "无法访问 Docker 日志目录"
        return
    fi

    # 获取容器 ID
    local gateway_id=$(docker compose ps -q hermes-gateway 2>/dev/null)

    if [ -n "$gateway_id" ]; then
        local log_file="$log_dir/$gateway_id/$gateway_id-json.log"
        if [ -f "$log_file" ]; then
            local log_size=$(du -sh "$log_file" 2>/dev/null | awk '{print $1}' || echo "0")
            log_check $STATUS_OK "Gateway 日志大小: $log_size"
        fi
    fi
}

# 检查网络端口
check_ports() {
    local gateway_port="${GATEWAY_PORT:-8642}"
    local dashboard_port="${DASHBOARD_PORT:-9119}"

    # 检查 Gateway 端口
    if netstat -tuln 2>/dev/null | grep -q ":${gateway_port} " || \
       ss -tuln 2>/dev/null | grep -q ":${gateway_port} "; then
        log_check $STATUS_OK "Gateway 端口 $gateway_port 已监听"
    else
        log_check $STATUS_WARNING "Gateway 端口 $gateway_port 未监听"
    fi

    # 检查 Dashboard 端口
    if netstat -tuln 2>/dev/null | grep -q ":${dashboard_port} " || \
       ss -tuln 2>/dev/null | grep -q ":${dashboard_port} "; then
        log_check $STATUS_OK "Dashboard 端口 $dashboard_port 已监听"
    else
        log_check $STATUS_WARNING "Dashboard 端口 $dashboard_port 未监听 (可选)"
    fi
}

# 检查数据卷
check_volume() {
    if docker volume inspect hermes-data &> /dev/null; then
        local mountpoint=$(docker volume inspect hermes-data --format '{{.Mountpoint}}')
        local data_size=$(du -sh "$mountpoint" 2>/dev/null | awk '{print $1}' || echo "N/A")
        log_check $STATUS_OK "数据卷存在，大小: $data_size"
    else
        log_check $STATUS_ERROR "数据卷 hermes-data 不存在"
    fi
}

# 检查配置文件
check_configs() {
    if [ -f .env ]; then
        log_check $STATUS_OK ".env 配置文件存在"
    else
        log_check $STATUS_ERROR ".env 配置文件缺失"
    fi

    if [ -d configs ] && [ "$(ls -A configs 2>/dev/null)" ]; then
        log_check $STATUS_OK "configs 目录存在且非空"
    else
        log_check $STATUS_WARNING "configs 目录不存在或为空"
    fi
}

# 检查最近备份
check_backup() {
    local backup_dir="${BACKUP_DIR:-./backups}"

    if [ ! -d "$backup_dir" ]; then
        log_check $STATUS_WARNING "备份目录不存在"
        return
    fi

    local latest_backup=$(find "$backup_dir" -name "hermes_backup_*.tar.gz" -type f -printf '%T@ %p\n' 2>/dev/null | \
                           sort -n | tail -1 | cut -f2- -d" ")

    if [ -z "$latest_backup" ]; then
        log_check $STATUS_WARNING "没有找到任何备份文件"
        return
    fi

    local backup_age=$(( ($(date +%s) - $(stat -c %Y "$latest_backup")) / 86400 ))

    if [ "$backup_age" -gt 7 ]; then
        log_check $STATUS_WARNING "最新备份已超过 7 天 (${backup_age} 天前)"
    else
        log_check $STATUS_OK "最新备份: ${backup_age} 天前"
    fi
}

# 显示详细资源统计
show_stats() {
    echo ""
    echo -e "${BLUE}========== 资源使用详情 ==========${NC}"
    docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.NetIO}}\t{{.BlockIO}}"
    echo ""

    echo -e "${BLUE}========== 磁盘使用 ==========${NC}"
    df -h .
    echo ""

    echo -e "${BLUE}========== Docker 系统资源 ==========${NC}"
    docker system df
    echo ""
}

# 显示帮助
cmd_help() {
    echo "Hermes Agent 健康检查工具"
    echo ""
    echo "用法: bash scripts/health-check.sh [command]"
    echo ""
    echo "命令:"
    echo "  (无)                执行完整健康检查"
    echo "  stats               显示详细资源统计"
    echo "  help                显示此帮助信息"
    echo ""
}

# 主函数
main() {
    local command=${1:-check}

    case "$command" in
        check)
            echo -e "${BLUE}========== Hermes Agent 健康检查 ==========${NC}"
            echo ""

            # 执行所有检查
            check_docker
            check_containers
            check_resources
            check_disk
            check_logs
            check_ports
            check_volume
            check_configs
            check_backup

            # 显示结果
            echo -e "${BLUE}检查结果:${NC}"
            for result in "${CHECK_RESULTS[@]}"; do
                echo -e "  $result"
            done
            echo ""

            # 显示总结
            case $OVERALL_STATUS in
                $STATUS_OK)
                    echo -e "${GREEN}总体状态: 健康 ✓${NC}"
                    ;;
                $STATUS_WARNING)
                    echo -e "${YELLOW}总体状态: 警告 ⚠${NC}"
                    ;;
                $STATUS_ERROR)
                    echo -e "${RED}总体状态: 异常 ✗${NC}"
                    ;;
            esac
            echo ""

            exit $OVERALL_STATUS
            ;;
        stats)
            show_stats
            ;;
        help|--help|-h)
            cmd_help
            ;;
        *)
            echo "未知命令: $command"
            cmd_help
            exit 1
            ;;
    esac
}

main "$@"
