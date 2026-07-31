#!/bin/bash
set -e

# Hermes Agent 云服务器部署 - 数据备份脚本
# 用途: 备份 Hermes Agent 数据卷和配置文件

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

# 加载环境变量
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

# 默认配置
BACKUP_DIR="${BACKUP_DIR:-./backups}"
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-7}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_NAME="hermes_backup_${TIMESTAMP}"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
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

# 创建备份目录
create_backup_dir() {
    mkdir -p "$BACKUP_DIR"
}

# 备份 Docker 数据卷
backup_volume() {
    log_info "备份 Docker 数据卷..."

    # 检查卷是否存在
    if ! docker volume inspect hermes-data &> /dev/null; then
        log_warn "数据卷 hermes-data 不存在，跳过卷备份"
        return 0
    fi

    # 使用临时容器备份卷
    docker run --rm \
        -v hermes-data:/source:ro \
        -v "$(pwd)/$BACKUP_DIR:/backup" \
        alpine \
        tar czf "/backup/${BACKUP_NAME}_volume.tar.gz" -C /source .

    log_info "数据卷备份完成: ${BACKUP_NAME}_volume.tar.gz"
}

# 备份配置文件
backup_configs() {
    log_info "备份配置文件..."

    if [ -d configs ]; then
        tar czf "$BACKUP_DIR/${BACKUP_NAME}_configs.tar.gz" configs/
        log_info "配置文件备份完成: ${BACKUP_NAME}_configs.tar.gz"
    else
        log_warn "configs 目录不存在，跳过配置备份"
    fi
}

# 备份 .env 文件
backup_env() {
    log_info "备份环境变量配置..."

    if [ -f .env ]; then
        cp .env "$BACKUP_DIR/${BACKUP_NAME}.env"
        log_info "环境变量备份完成: ${BACKUP_NAME}.env"
    else
        log_warn ".env 文件不存在，跳过环境变量备份"
    fi
}

# 备份 docker-compose.yml
backup_compose() {
    log_info "备份 docker-compose.yml..."

    if [ -f docker-compose.yml ]; then
        cp docker-compose.yml "$BACKUP_DIR/${BACKUP_NAME}_docker-compose.yml"
        log_info "docker-compose.yml 备份完成"
    fi
}

# 清理旧备份
cleanup_old_backups() {
    log_info "清理 ${BACKUP_RETENTION_DAYS} 天前的旧备份..."

    local count=$(find "$BACKUP_DIR" -name "hermes_backup_*" -type f -mtime +${BACKUP_RETENTION_DAYS} | wc -l)

    if [ "$count" -gt 0 ]; then
        find "$BACKUP_DIR" -name "hermes_backup_*" -type f -mtime +${BACKUP_RETENTION_DAYS} -delete
        log_info "已删除 $count 个旧备份文件"
    else
        log_info "没有需要清理的旧备份"
    fi
}

# 显示备份统计
show_stats() {
    log_info "备份统计信息..."
    echo ""

    local total_files=$(find "$BACKUP_DIR" -name "hermes_backup_*" -type f | wc -l)
    local total_size=$(du -sh "$BACKUP_DIR" 2>/dev/null | awk '{print $1}' || echo "0")
    local latest_backup=$(ls -t "$BACKUP_DIR"/hermes_backup_* 2>/dev/null | head -1 || echo "无")

    echo "  备份目录: $BACKUP_DIR"
    echo "  备份文件数: $total_files"
    echo "  总大小: $total_size"
    echo "  最新备份: $latest_backup"
    echo "  保留天数: $BACKUP_RETENTION_DAYS"
    echo ""
}

# 列出所有备份
cmd_list() {
    log_info "列出所有备份..."
    echo ""

    if [ ! -d "$BACKUP_DIR" ]; then
        log_warn "备份目录不存在"
        return 0
    fi

    local backups=$(find "$BACKUP_DIR" -name "hermes_backup_*.tar.gz" -type f | sort -r)

    if [ -z "$backups" ]; then
        log_info "没有找到任何备份"
        return 0
    fi

    echo "备份文件列表:"
    echo "$backups" | while read -r file; do
        local size=$(du -h "$file" | awk '{print $1}')
        local date=$(basename "$file" | grep -oP '\d{8}_\d{6}')
        echo "  - $(basename "$file") ($size) - $date"
    done
    echo ""
}

# 恢复备份
cmd_restore() {
    local backup_name=$1

    if [ -z "$backup_name" ]; then
        log_error "请指定要恢复的备份名称"
        echo "用法: bash scripts/backup.sh restore <backup_name>"
        echo "示例: bash scripts/backup.sh restore hermes_backup_20240101_120000"
        exit 1
    fi

    log_warn "即将恢复备份: $backup_name"
    log_warn "这将覆盖当前数据，请确保已备份当前状态"
    read -p "确认恢复? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        log_info "已取消恢复"
        exit 0
    fi

    log_info "开始恢复..."

    # 停止服务
    log_info "停止服务..."
    docker compose down

    # 恢复数据卷
    if [ -f "$BACKUP_DIR/${backup_name}_volume.tar.gz" ]; then
        log_info "恢复数据卷..."
        docker run --rm \
            -v hermes-data:/target \
            -v "$(pwd)/$BACKUP_DIR:/backup" \
            alpine \
            tar xzf "/backup/${backup_name}_volume.tar.gz" -C /target
    fi

    # 恢复配置文件
    if [ -f "$BACKUP_DIR/${backup_name}_configs.tar.gz" ]; then
        log_info "恢复配置文件..."
        tar xzf "$BACKUP_DIR/${backup_name}_configs.tar.gz"
    fi

    # 恢复 .env
    if [ -f "$BACKUP_DIR/${backup_name}.env" ]; then
        log_info "恢复环境变量..."
        cp "$BACKUP_DIR/${backup_name}.env" .env
    fi

    # 启动服务
    log_info "启动服务..."
    docker compose up -d

    log_info "恢复完成 ✓"
}

# 执行完整备份
cmd_backup() {
    log_info "开始完整备份..."
    log_info "备份目录: $BACKUP_DIR"
    log_info "备份名称: $BACKUP_NAME"
    echo ""

    create_backup_dir
    backup_volume
    backup_configs
    backup_env
    backup_compose
    cleanup_old_backups

    echo ""
    log_info "备份完成 ✓"
    show_stats
}

# 显示帮助
cmd_help() {
    echo "Hermes Agent 数据备份工具"
    echo ""
    echo "用法: bash scripts/backup.sh [command]"
    echo ""
    echo "命令:"
    echo "  (无)                执行完整备份"
    echo "  list                列出所有备份"
    echo "  restore <name>      恢复指定备份"
    echo "  help                显示此帮助信息"
    echo ""
    echo "环境变量:"
    echo "  BACKUP_DIR              备份目录 (默认: ./backups)"
    echo "  BACKUP_RETENTION_DAYS   备份保留天数 (默认: 7)"
    echo ""
    echo "示例:"
    echo "  bash scripts/backup.sh"
    echo "  bash scripts/backup.sh list"
    echo "  bash scripts/backup.sh restore hermes_backup_20240101_120000"
}

# 主函数
main() {
    local command=${1:-backup}

    case "$command" in
        backup)
            cmd_backup
            ;;
        list)
            cmd_list
            ;;
        restore)
            cmd_restore "$2"
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
