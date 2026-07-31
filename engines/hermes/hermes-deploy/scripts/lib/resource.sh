#!/bin/bash
# Hermes Agent 多实例部署 - 资源预算管理
# 提供资源预算检查、实例配额计算、资源使用报告等功能

# 防止重复 source
if [[ -n "${_RESOURCE_SH_LOADED:-}" ]]; then
    return 0
fi
_RESOURCE_SH_LOADED=1

set -euo pipefail

# 导入共享函数
_resource_sh_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${_resource_sh_dir}/common.sh"
unset _resource_sh_dir

# ==================== 资源常量 ====================
# 基于 4GB 内存、4 核 CPU 的服务器，可通过环境变量覆盖
readonly TOTAL_MEMORY_MB=${HERMES_TOTAL_MEMORY_MB:-4096}
readonly OS_DOCKER_MEMORY_MB=${HERMES_OS_OVERHEAD_MB:-1024}  # OS + runtime 预留
readonly SAFETY_MARGIN_MB=${HERMES_SAFETY_MARGIN_MB:-720}    # 安全余量
readonly AVAILABLE_MEMORY_MB=$((TOTAL_MEMORY_MB - OS_DOCKER_MEMORY_MB - SAFETY_MARGIN_MB))

readonly TOTAL_CPU_CORES=${HERMES_TOTAL_CPU:-4}
readonly OS_DOCKER_CPU=${HERMES_OS_CPU_OVERHEAD:-1.5}        # OS + runtime 预留
readonly AVAILABLE_CPU=$(_calc "$TOTAL_CPU_CORES - $OS_DOCKER_CPU")

readonly DEFAULT_INSTANCE_MEMORY_MB=${HERMES_INSTANCE_MEMORY_MB:-768}
readonly DEFAULT_INSTANCE_CPU=${HERMES_INSTANCE_CPU:-0.75}
readonly MAX_INSTANCES=${HERMES_MAX_INSTANCES:-4}

# ==================== 资源检查函数 ====================
get_allocated_memory() {
    # 计算所有实例的内存配额总和
    local registry="$(read_registry)"
    local total=0

    for instance_id in $(get_all_instance_ids); do
        local mem=$(echo "$registry" | jq -r ".instances.\"$instance_id\".memory_limit_mb // 0")
        total=$((total + mem))
    done

    echo "$total"
}

get_allocated_cpu() {
    # 计算所有实例的 CPU 配额总和
    local registry="$(read_registry)"
    local total="0"

    for instance_id in $(get_all_instance_ids); do
        local cpu=$(echo "$registry" | jq -r ".instances.\"$instance_id\".cpu_limit // 0")
        total=$(_calc "$total + $cpu")
    done

    echo "$total"
}

get_running_instance_count() {
    # 计算运行中的实例数量
    local registry="$(read_registry)"
    local count=0

    for instance_id in $(get_all_instance_ids); do
        local status=$(echo "$registry" | jq -r ".instances.\"$instance_id\".status // \"stopped\"")
        if [[ "$status" == "running" ]]; then
            count=$((count + 1))
        fi
    done

    echo "$count"
}

get_available_memory() {
    # 计算剩余可用内存
    local allocated=$(get_allocated_memory)
    echo $((AVAILABLE_MEMORY_MB - allocated))
}

get_available_cpu() {
    # 计算剩余可用 CPU
    local allocated=$(get_allocated_cpu)
    _calc "$AVAILABLE_CPU - $allocated"
}

can_create_instance() {
    local requested_memory="${1:-$DEFAULT_INSTANCE_MEMORY_MB}"
    local requested_cpu="${2:-$DEFAULT_INSTANCE_CPU}"

    # 检查实例数量上限
    local total_instances=$(get_all_instance_ids | wc -l)
    if [[ $total_instances -ge $MAX_INSTANCES ]]; then
        log_error "实例数量已达上限: $total_instances/$MAX_INSTANCES"
        return 1
    fi

    # 检查内存预算
    local available_mem=$(get_available_memory)
    if [[ $requested_memory -gt $available_mem ]]; then
        log_error "内存预算不足: 请求 ${requested_memory}MB, 可用 ${available_mem}MB"
        return 1
    fi

    # 检查 CPU 预算
    local available_cpu=$(get_available_cpu)
    if (( $(_calc_gt "$requested_cpu" "$available_cpu") )); then
        log_error "CPU 预算不足: 请求 ${requested_cpu} 核, 可用 ${available_cpu} 核"
        return 1
    fi

    return 0
}

calculate_instance_resources() {
    # 根据剩余资源计算推荐配置
    local available_mem=$(get_available_memory)
    local available_cpu=$(get_available_cpu)
    local running_count=$(get_running_instance_count)
    local remaining_slots=$((MAX_INSTANCES - running_count))

    if [[ $remaining_slots -le 0 ]]; then
        log_error "无可用实例槽位"
        return 1
    fi

    # 计算每实例推荐配额 (平均分配)
    local recommended_mem=$((available_mem / remaining_slots))
    local recommended_cpu=$(_calc "$available_cpu / $remaining_slots")

    # 限制在合理范围内
    if [[ $recommended_mem -gt $DEFAULT_INSTANCE_MEMORY_MB ]]; then
        recommended_mem=$DEFAULT_INSTANCE_MEMORY_MB
    fi

    if (( $(_calc_gt "$recommended_cpu" "$DEFAULT_INSTANCE_CPU") )); then
        recommended_cpu=$DEFAULT_INSTANCE_CPU
    fi

    # 确保不低于最低要求
    if [[ $recommended_mem -lt 512 ]]; then
        log_warn "推荐内存较低: ${recommended_mem}MB (最低建议 512MB)"
    fi

    echo "${recommended_mem}:${recommended_cpu}"
}

# ==================== 资源报告 ====================
print_resource_summary() {
    echo "========================================"
    echo "  Hermes Agent 多实例资源使用报告"
    echo "========================================"
    echo ""
    echo "服务器总资源:"
    echo "  内存: ${TOTAL_MEMORY_MB} MB"
    echo "  CPU:  ${TOTAL_CPU_CORES} 核"
    echo ""
    echo "固定分配:"
    echo "  OS + Docker:  ${OS_DOCKER_MEMORY_MB} MB / ${OS_DOCKER_CPU} 核"
    echo "  安全余量:     ${SAFETY_MARGIN_MB} MB"
    echo ""
    echo "实例可用预算:"
    echo "  内存: ${AVAILABLE_MEMORY_MB} MB"
    echo "  CPU:  ${AVAILABLE_CPU} 核"
    echo "  槽位: ${MAX_INSTANCES} 个实例"
    echo ""

    local allocated_mem=$(get_allocated_memory)
    local allocated_cpu=$(get_allocated_cpu)
    local available_mem=$(get_available_memory)
    local available_cpu=$(get_available_cpu)
    local total_instances=$(get_all_instance_ids | wc -l)
    local running_count=$(get_running_instance_count)

    echo "当前分配:"
    echo "  内存: ${allocated_mem} MB / ${AVAILABLE_MEMORY_MB} MB"
    echo "  CPU:  ${allocated_cpu} 核 / ${AVAILABLE_CPU} 核"
    echo "  实例: ${total_instances} 个 (${running_count} 个运行中)"
    echo ""

    if [[ $total_instances -gt 0 ]]; then
        echo "实例详情:"
        printf "  %-20s %-10s %-10s %-10s\n" "实例ID" "状态" "内存" "CPU"
        printf "  %-20s %-10s %-10s %-10s\n" "--------" "------" "------" "------"

        local registry="$(read_registry)"
        for instance_id in $(get_all_instance_ids); do
            local status=$(echo "$registry" | jq -r ".instances.\"$instance_id\".status // \"unknown\"")
            local mem=$(echo "$registry" | jq -r ".instances.\"$instance_id\".memory_limit_mb // 0")
            local cpu=$(echo "$registry" | jq -r ".instances.\"$instance_id\".cpu_limit // 0")
            printf "  %-20s %-10s %-10s %-10s\n" "$instance_id" "$status" "${mem}MB" "${cpu}核"
        done
        echo ""
    fi

    echo "剩余可用:"
    echo "  内存: ${available_mem} MB"
    echo "  CPU:  ${available_cpu} 核"
    echo "  槽位: $((MAX_INSTANCES - total_instances)) 个实例"
    echo ""
}

# ==================== 实际资源使用 ====================
get_instance_actual_memory() {
    local instance_id="$1"
    local runtime
    runtime="$(_detect_instance_runtime 2>/dev/null || echo local)"

    if [[ "$runtime" == "k3s" ]]; then
        local kubectl pod
        kubectl="$(_common_get_kubectl)" || { echo "0"; return 0; }
        pod=$($kubectl -n hermes get pods -l "app=hermes-gateway,instance=${instance_id}" \
            -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
        if [[ -z "$pod" ]]; then
            echo "0"
            return 0
        fi
        local mem_usage
        mem_usage=$($kubectl top pod "$pod" -n hermes --no-headers 2>/dev/null | awk '{print $3}' || echo "0Mi")
        if [[ "$mem_usage" == *"Gi"* ]]; then
            _calc_int "${mem_usage%%Gi} * 1024"
        elif [[ "$mem_usage" == *"Mi"* ]]; then
            echo "${mem_usage%%Mi}"
        else
            echo "0"
        fi
        return 0
    fi

    local container_name="hermes-gateway-${instance_id}"

    local mem_usage=$(docker stats --no-stream --format "{{.MemUsage}}" "$container_name" 2>/dev/null || echo "0MiB / 0MiB")
    local mem_mb=$(echo "$mem_usage" | awk -F'/' '{print $1}' | sed 's/[^0-9.]//g')

    if [[ "$mem_usage" == *"GiB"* ]]; then
        _calc_int "$mem_mb * 1024"
    else
        echo $(echo "$mem_mb" | cut -d'.' -f1)
    fi
}

get_instance_actual_cpu() {
    local instance_id="$1"
    local runtime
    runtime="$(_detect_instance_runtime 2>/dev/null || echo local)"

    if [[ "$runtime" == "k3s" ]]; then
        local kubectl pod
        kubectl="$(_common_get_kubectl)" || { echo "0"; return 0; }
        pod=$($kubectl -n hermes get pods -l "app=hermes-gateway,instance=${instance_id}" \
            -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
        if [[ -z "$pod" ]]; then
            echo "0"
            return 0
        fi
        local cpu_usage
        cpu_usage=$($kubectl top pod "$pod" -n hermes --no-headers 2>/dev/null | awk '{print $2}' || echo "0m")
        if [[ "$cpu_usage" == *"m"* ]]; then
            local millicores="${cpu_usage%%m}"
            _calc "$millicores / 10"
        else
            _calc "${cpu_usage} * 100"
        fi
        return 0
    fi

    local container_name="hermes-gateway-${instance_id}"

    local cpu_usage=$(docker stats --no-stream --format "{{.CPUPerc}}" "$container_name" 2>/dev/null || echo "0.00%")
    echo "$cpu_usage" | sed 's/%//'
}

print_instance_actual_usage() {
    local instance_id="$1"
    local actual_mem=$(get_instance_actual_memory "$instance_id")
    local actual_cpu=$(get_instance_actual_cpu "$instance_id")

    echo "实例 $instance_id 实际使用:"
    echo "  内存: ${actual_mem} MB"
    echo "  CPU:  ${actual_cpu}%"
}

# ==================== K8s 节点容量检测 ====================
detect_k8s_node_capacity() {
    local runtime
    runtime="$(_detect_instance_runtime 2>/dev/null || echo local)"

    if [[ "$runtime" != "k3s" ]]; then
        return 0
    fi

    local kubectl
    kubectl="$(_common_get_kubectl)" || return 1

    local node_mem_ki
    node_mem_ki=$($kubectl get nodes -o jsonpath='{.items[0].status.allocatable.memory}' 2>/dev/null || echo "")
    local node_cpu
    node_cpu=$($kubectl get nodes -o jsonpath='{.items[0].status.allocatable.cpu}' 2>/dev/null || echo "")

    if [[ -n "$node_mem_ki" ]]; then
        local node_mem_mb=$(( ${node_mem_ki%%Ki} / 1024 ))
        if [[ $node_mem_mb -gt 0 ]]; then
            log_info "K8s 节点实际可用内存: ${node_mem_mb} MB (默认 ${TOTAL_MEMORY_MB} MB，可通过 HERMES_TOTAL_MEMORY_MB 调整)"
        fi
    fi

    if [[ -n "$node_cpu" ]]; then
        log_info "K8s 节点实际可用 CPU: ${node_cpu} 核 (默认 ${TOTAL_CPU_CORES} 核，可通过 HERMES_TOTAL_CPU 调整)"
    fi
}
