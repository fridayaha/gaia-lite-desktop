#!/bin/bash
# Hermes Agent 多实例部署 - 共享函数库
# 提供日志、校验、注册表读写、权限管理等通用功能

# 防止重复 source
if [[ -n "${_COMMON_SH_LOADED:-}" ]]; then
    return 0
fi
_COMMON_SH_LOADED=1

set -euo pipefail

# ==================== 依赖检查 ====================
# jq 是项目核心依赖（注册表 JSON 读写），但并非所有 Linux 默认安装
if ! command -v jq &>/dev/null; then
    echo -e "\033[0;31m[ERROR] 缺少必要依赖: jq\033[0m" >&2
    echo "" >&2
    echo "jq 用于处理 JSON 格式的注册表和配置文件，请先安装：" >&2
    echo "" >&2
    echo "  Ubuntu/Debian:  sudo apt-get install -y jq" >&2
    echo "  CentOS/RHEL:    sudo yum install -y jq" >&2
    echo "  macOS:          brew install jq" >&2
    echo "" >&2
    exit 1
fi

# ==================== 颜色定义 ====================
readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[1;33m'
readonly BLUE='\033[0;34m'
readonly NC='\033[0m' # No Color

# ==================== 日志函数 ====================
log_info() {
    echo -e "${GREEN}[INFO]${NC} $*" >&2
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $*" >&2
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $*" >&2
}

log_debug() {
    if [[ "${DEBUG:-false}" == "true" ]]; then
        echo -e "${BLUE}[DEBUG]${NC} $*" >&2
    fi
}

# ==================== 路径函数 ====================
get_project_dir() {
    # 返回项目根目录 (hermes-deploy)
    # 从调用者位置向上搜索 docker-compose.yml 作为项目根标记
    local script_path="${BASH_SOURCE[${#BASH_SOURCE[@]}-1]}"
    local dir="$(cd "$(dirname "$script_path")" && pwd)"

    while [[ "$dir" != "/" ]]; do
        if [[ -f "$dir/docker-compose.yml" ]]; then
            echo "$dir"
            return 0
        fi
        dir="$(dirname "$dir")"
    done

    # 回退: 从 BASH_SOURCE 链中尝试
    log_error "无法定位项目根目录 (未找到 docker-compose.yml)"
    return 1
}

get_registry_file() {
    local project_dir="$(get_project_dir)"
    echo "${project_dir}/data/registry/instances.json"
}

get_instance_dir() {
    local instance_id="$1"
    local project_dir="$(get_project_dir)"
    echo "${project_dir}/data/instances/${instance_id}"
}

# ==================== 校验函数 ====================
validate_instance_id() {
    local instance_id="$1"

    # 检查格式: [a-z0-9][a-z0-9-]{2,31}
    if [[ ! "$instance_id" =~ ^[a-z0-9][a-z0-9-]{2,31}$ ]]; then
        log_error "实例 ID 格式错误: $instance_id"
        log_error "要求: 3-32 位小写字母、数字或连字符，以字母或数字开头"
        return 1
    fi

    return 0
}

# ==================== Kubectl Helper ====================
_common_kubectl=""  # cached

_common_get_kubectl() {
    if [[ -n "$_common_kubectl" ]]; then
        echo "$_common_kubectl"
        return 0
    fi
    if command -v kubectl &>/dev/null; then
        _common_kubectl="kubectl"
    elif command -v k3s &>/dev/null; then
        _common_kubectl="k3s kubectl"
    else
        return 1
    fi
    echo "$_common_kubectl"
}

# ==================== 运行环境检测 ====================
_detect_instance_runtime() {
    if [[ -n "${_INSTANCE_RUNTIME:-}" ]]; then
        echo "$_INSTANCE_RUNTIME"
        return 0
    fi

    local kubectl
    kubectl="$(_common_get_kubectl 2>/dev/null)" || true

    if [[ -n "$kubectl" ]] && \
       { $kubectl get namespace hermes &>/dev/null || \
         $kubectl -n hermes get deployment -l app=hermes-gateway &>/dev/null; }; then
        _INSTANCE_RUNTIME="k3s"
    elif command -v docker &>/dev/null && \
         docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^hermes-gateway$"; then
        _INSTANCE_RUNTIME="docker"
    else
        _INSTANCE_RUNTIME="local"
    fi
    echo "$_INSTANCE_RUNTIME"
}

# ==================== 注册表函数 ====================
read_registry() {
    local runtime
    runtime="$(_detect_instance_runtime 2>/dev/null || echo local)"

    if [[ "$runtime" == "k3s" ]]; then
        # K3s: read from ConfigMap
        local kubectl content
        kubectl="$(_common_get_kubectl)" || { echo '{"instances":{}}'; return 0; }
        content=$($kubectl -n hermes get configmap hermes-instance-registry \
            -o jsonpath='{.data.registry\.json}' 2>/dev/null)
        if [[ -z "$content" ]]; then
            echo '{"instances":{}}'
        else
            echo "$content"
        fi
        return 0
    fi

    # Docker/local: read from file
    local registry_file="$(get_registry_file)"

    if [[ ! -f "$registry_file" ]]; then
        echo '{"instances":{}}'
        return 0
    fi

    cat "$registry_file"
}

write_registry() {
    local content="$1"
    local runtime
    runtime="$(_detect_instance_runtime 2>/dev/null || echo local)"

    if [[ "$runtime" == "k3s" ]]; then
        # K3s: write to ConfigMap
        local kubectl
        kubectl="$(_common_get_kubectl)" || return 1
        $kubectl -n hermes create configmap hermes-instance-registry \
            --from-literal=registry.json="$content" \
            --dry-run=client -o yaml | $kubectl apply -f -
        return 0
    fi

    # Docker/local: write to file
    local registry_file="$(get_registry_file)"
    local tmp_file="${registry_file}.tmp"

    # 确保目录存在
    mkdir -p "$(dirname "$registry_file")"

    # 原子写入
    echo "$content" > "$tmp_file"
    mv "$tmp_file" "$registry_file"
}

register_instance() {
    local instance_id="$1"
    local created_at="$2"
    local memory_limit_mb="$3"
    local cpu_limit="$4"
    local gateway_token="$5"
    local container_name="$6"
    local status="$7"
    local gateway_port="${8:-0}"
    local dashboard_port="${9:-0}"
    local runtime="${10:-$(_detect_instance_runtime 2>/dev/null || echo unknown)}"

    # 初始化注册表（如果不存在且为文件模式）
    local rmode
    rmode="$(_detect_instance_runtime 2>/dev/null || echo local)"
    if [[ "$rmode" != "k3s" ]]; then
        local registry_file="$(get_registry_file)"
        if [[ ! -f "$registry_file" ]]; then
            mkdir -p "$(dirname "$registry_file")"
            echo '{"instances":{}}' > "$registry_file"
        fi
    fi

    local registry=$(read_registry)

    # 添加实例
    local updated=$(echo "$registry" | jq --arg id "$instance_id" \
        --arg created "$created_at" \
        --argjson mem "$memory_limit_mb" \
        --argjson cpu "$cpu_limit" \
        --arg token "$gateway_token" \
        --arg container "$container_name" \
        --arg status "$status" \
        --argjson gw_port "$gateway_port" \
        --argjson dash_port "$dashboard_port" \
        --arg rt "$runtime" \
        '.instances[$id] = {
            "created_at": $created,
            "memory_limit_mb": $mem,
            "cpu_limit": $cpu,
            "gateway_token": $token,
            "container_name": $container,
            "status": $status,
            "gateway_port": $gw_port,
            "dashboard_port": $dash_port,
            "runtime": $rt
        }')

    write_registry "$updated"
}

unregister_instance() {
    local instance_id="$1"

    local registry=$(read_registry)
    local updated=$(echo "$registry" | jq --arg id "$instance_id" 'del(.instances[$id])')

    write_registry "$updated"
}

instance_exists() {
    local instance_id="$1"

    local registry=$(read_registry)
    local exists=$(echo "$registry" | jq --arg id "$instance_id" '.instances | has($id)')

    [[ "$exists" == "true" ]]
}

get_instance_status() {
    local instance_id="$1"

    local registry=$(read_registry)
    echo "$registry" | jq -r --arg id "$instance_id" '.instances[$id].status // "unknown"'
}

update_instance_status() {
    local instance_id="$1"
    local status="$2"

    local registry=$(read_registry)
    local updated=$(echo "$registry" | jq --arg id "$instance_id" \
        --arg status "$status" \
        '.instances[$id].status = $status')

    write_registry "$updated"
}

get_all_instance_ids() {
    local registry=$(read_registry)
    echo "$registry" | jq -r '.instances | keys[]'
}

# ==================== 浮点运算（替代 bc） ====================
_calc()    { awk "BEGIN {print $*}"; }
_calc_int(){ awk "BEGIN {printf \"%d\", $*}"; }
_calc_gt() { awk "BEGIN {print ($1 > $2)}"; }

# ==================== 工具函数 ====================
generate_token() {
    # 生成 32 字节随机 token
    openssl rand -hex 32
}

set_data_permissions() {
    local dir="$1"

    if [[ -d "$dir" ]]; then
        # 设置目录权限 (用户可读写执行，组和其他用户只读)
        chmod -R u+rwX,g+rX,o+rX "$dir"
    fi
}

wait_for_healthy() {
    local container_name="$1"
    local max_attempts="${2:-30}"
    local sleep_seconds="${3:-2}"

    local attempt=1
    while [[ $attempt -le $max_attempts ]]; do
        local health=$(docker inspect --format='{{.State.Health.Status}}' "$container_name" 2>/dev/null || echo "not_found")

        if [[ "$health" == "healthy" ]]; then
            return 0
        elif [[ "$health" == "not_found" ]]; then
            log_error "容器不存在: $container_name"
            return 1
        fi

        log_debug "等待健康检查... (尝试 $attempt/$max_attempts, 状态: $health)"
        sleep "$sleep_seconds"
        attempt=$((attempt + 1))
    done

    return 1
}
