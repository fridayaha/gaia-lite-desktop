#!/bin/bash
# Profile 管理 - 共享函数库
# 提供 Profile 注册表读写、端口分配、名称验证等功能

# 防止重复 source
if [[ -n "${_PROFILE_COMMON_SH_LOADED:-}" ]]; then
    return 0
fi
_PROFILE_COMMON_SH_LOADED=1

set -euo pipefail

# Source 通用函数库
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

# ==================== 常量 ====================
readonly PROFILE_BASE_PORT=8643
readonly PROFILE_MAX_PORT=8650
readonly PROFILE_MAX_COUNT=8
readonly PROFILE_UID_BASE=1100
readonly PROFILE_UID_MAX=$((PROFILE_UID_BASE + PROFILE_MAX_COUNT - 1))

# ==================== 路径函数 ====================
get_profiles_dir() {
    local project_dir
    project_dir="$(get_project_dir)"
    echo "${project_dir}/data/profiles"
}

get_profile_registry_file() {
    local profiles_dir
    profiles_dir="$(get_profiles_dir)"
    echo "${profiles_dir}/registry.json"
}

get_profile_dir() {
    local profile_name="$1"
    local profiles_dir
    profiles_dir="$(get_profiles_dir)"
    echo "${profiles_dir}/${profile_name}"
}

# ==================== 验证函数 ====================
validate_profile_name() {
    local name="$1"

    if [[ ! "$name" =~ ^[a-z0-9][a-z0-9-]{2,31}$ ]]; then
        log_error "Profile 名称格式错误: $name"
        log_error "要求: 3-32 位小写字母、数字或连字符，以字母或数字开头"
        return 1
    fi

    return 0
}

# ==================== 注册表函数 ====================
read_profile_registry() {
    local registry_file
    registry_file="$(get_profile_registry_file)"

    if ! pf_file_exists "$registry_file"; then
        echo '{"profiles":{}}'
        return 0
    fi

    pf_read_file "$registry_file"
}

write_profile_registry() {
    local content="$1"
    local registry_file
    registry_file="$(get_profile_registry_file)"

    pf_write_file "$registry_file" "$content"
}

register_profile() {
    local name="$1"
    local port="$2"
    local provider="$3"
    local model="$4"
    local uid="$5"
    local created_at
    created_at="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

    local registry_file
    registry_file="$(get_profile_registry_file)"

    if ! pf_file_exists "$registry_file"; then
        pf_write_file "$registry_file" '{"profiles":{}}'
    fi

    local registry
    registry="$(read_profile_registry)"

    local updated
    updated=$(echo "$registry" | jq --arg name "$name" \
        --argjson port "$port" \
        --arg provider "$provider" \
        --arg model "$model" \
        --argjson uid "$uid" \
        --arg created "$created_at" \
        '.profiles[$name] = {
            "created_at": $created,
            "port": $port,
            "status": "created",
            "provider": $provider,
            "model": $model,
            "uid": $uid
        }')

    write_profile_registry "$updated"
}

unregister_profile() {
    local name="$1"

    local registry
    registry="$(read_profile_registry)"
    local updated
    updated=$(echo "$registry" | jq --arg name "$name" 'del(.profiles[$name])')

    write_profile_registry "$updated"
}

profile_exists() {
    local name="$1"

    local registry
    registry="$(read_profile_registry)"
    local exists
    exists=$(echo "$registry" | jq --arg name "$name" '.profiles | has($name)')

    [[ "$exists" == "true" ]]
}

get_profile_status() {
    local name="$1"

    local registry
    registry="$(read_profile_registry)"
    echo "$registry" | jq -r --arg name "$name" '.profiles[$name].status // "unknown"'
}

get_profile_port() {
    local name="$1"

    local registry
    registry="$(read_profile_registry)"
    echo "$registry" | jq -r --arg name "$name" '.profiles[$name].port // 0'
}

update_profile_status() {
    local name="$1"
    local status="$2"

    local registry
    registry="$(read_profile_registry)"
    local updated
    updated=$(echo "$registry" | jq --arg name "$name" \
        --arg status "$status" \
        '.profiles[$name].status = $status')

    write_profile_registry "$updated"
}

get_all_profile_names() {
    local registry
    registry="$(read_profile_registry)"
    echo "$registry" | jq -r '.profiles | keys[]' 2>/dev/null || true
}

# ==================== 端口分配 ====================
allocate_profile_port() {
    local registry
    registry="$(read_profile_registry)"

    local used_ports
    used_ports=$(echo "$registry" | jq -r '.profiles[].port' 2>/dev/null | sort -n)

    local port=$PROFILE_BASE_PORT
    while [[ $port -le $PROFILE_MAX_PORT ]]; do
        if ! echo "$used_ports" | grep -q "^${port}$"; then
            echo "$port"
            return 0
        fi
        port=$((port + 1))
    done

    log_error "无法分配端口: 所有 Profile 端口 ($PROFILE_BASE_PORT-$PROFILE_MAX_PORT) 已被占用"
    return 1
}

# ==================== UID 管理 ====================
allocate_profile_uid() {
    local registry
    registry="$(read_profile_registry)"

    local used_uids
    used_uids=$(echo "$registry" | jq -r '.profiles[].uid // empty' 2>/dev/null | sort -n)

    local uid=$PROFILE_UID_BASE
    while [[ $uid -le $PROFILE_UID_MAX ]]; do
        if ! echo "$used_uids" | grep -q "^${uid}$"; then
            echo "$uid"
            return 0
        fi
        uid=$((uid + 1))
    done

    log_error "无法分配 UID: 所有 Profile UID ($PROFILE_UID_BASE-$PROFILE_UID_MAX) 已被占用"
    return 1
}

get_profile_uid() {
    local name="$1"
    local registry
    registry="$(read_profile_registry)"
    echo "$registry" | jq -r --arg name "$name" '.profiles[$name].uid // 1000'
}

get_profile_username() {
    local name="$1"
    echo "hermes-p-${name}"
}

ensure_profile_user() {
    local name="$1"
    local uid="$2"
    local username
    username=$(get_profile_username "$name")

    container_exec "
        if ! id -u '${username}' >/dev/null 2>&1; then
            useradd -u ${uid} -g hermes -d /opt/data/profiles/${name} \
                -s /bin/bash -M '${username}'
        fi
    "
}

set_profile_permissions() {
    local name="$1"
    local uid="$2"

    container_exec "
        chown -R ${uid}:1000 /opt/data/profiles/${name}
        chmod 700 /opt/data/profiles/${name}
    "
}

# ==================== 资源检查 ====================
can_create_profile() {
    local registry
    registry="$(read_profile_registry)"

    local count
    count=$(echo "$registry" | jq '.profiles | length')

    if [[ $count -ge $PROFILE_MAX_COUNT ]]; then
        log_error "无法创建 Profile: 已达到最大数量 ($PROFILE_MAX_COUNT)"
        return 1
    fi

    return 0
}

# ==================== 指令队列 ====================
write_profile_command() {
    local action="$1"
    local name="$2"
    local timestamp
    timestamp="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

    local commands_file
    commands_file="$(get_profiles_dir)/commands.json"

    local commands
    if pf_file_exists "$commands_file"; then
        commands=$(pf_read_file "$commands_file" 2>/dev/null || echo '{"commands":[]}')
    else
        commands='{"commands":[]}'
    fi

    local updated
    updated=$(echo "$commands" | jq --arg action "$action" \
        --arg name "$name" \
        --arg ts "$timestamp" \
        '.commands += [{"action": $action, "profile": $name, "timestamp": $ts}]')

    pf_write_file "$commands_file" "$updated"
}

# ==================== 容器信号 ====================
signal_container() {
    local container_name="${1:-hermes-gateway}"
    local runtime
    runtime=$(pf_detect_runtime)

    if [[ "$runtime" == "k3s" ]]; then
        local kubectl pod
        kubectl="$(_pf_get_kubectl)" || return 1
        pod="$(_pf_get_pod_name)" || return 1
        $kubectl -n hermes exec "$pod" -- bash -c "
            pid=\$(pgrep -f 'profile-supervisor' 2>/dev/null | head -1)
            if [ -n \"\$pid\" ]; then
                kill -HUP \$pid
            else
                kill -HUP 1
            fi
        " 2>/dev/null
        return $?
    fi

    if docker ps --format '{{.Names}}' | grep -q "^${container_name}$"; then
        docker kill --signal=SIGHUP "$container_name" >/dev/null 2>&1 || true
        return 0
    else
        log_warn "容器未运行: $container_name"
        return 1
    fi
}

# ==================== 运行环境检测与文件 I/O 抽象 ====================
# K3s 模式下 profiles 存储在容器内 PVC 上，宿主机无法直接读写。
# 以下函数提供统一的文件 I/O 接口，自动路由到正确的后端。

_K3S_KUBECTL=""
_K3S_POD_NAME=""

pf_detect_runtime() {
    if [[ -n "${_PF_RUNTIME:-}" ]]; then
        echo "$_PF_RUNTIME"
        return 0
    fi

    if (command -v kubectl &>/dev/null || command -v k3s &>/dev/null) && \
       _pf_get_kubectl &>/dev/null && \
       _pf_get_kubectl -n hermes get deployment hermes-gateway &>/dev/null 2>&1; then
        _PF_RUNTIME="k3s"
    elif command -v docker &>/dev/null && \
         docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^hermes-gateway$"; then
        _PF_RUNTIME="docker"
    else
        _PF_RUNTIME="local"
    fi
    echo "$_PF_RUNTIME"
}

_pf_get_kubectl() {
    if [[ -n "$_K3S_KUBECTL" ]]; then
        echo "$_K3S_KUBECTL"
        return 0
    fi
    if command -v kubectl &>/dev/null; then
        _K3S_KUBECTL="kubectl"
    elif command -v k3s &>/dev/null; then
        _K3S_KUBECTL="k3s kubectl"
    else
        return 1
    fi
    echo "$_K3S_KUBECTL"
}

_pf_get_pod_name() {
    if [[ -n "$_K3S_POD_NAME" ]]; then
        echo "$_K3S_POD_NAME"
        return 0
    fi
    local kubectl
    kubectl="$(_pf_get_kubectl)" || return 1
    _K3S_POD_NAME=$($kubectl -n hermes get pods -l app=hermes-gateway -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
    if [[ -z "$_K3S_POD_NAME" ]]; then
        return 1
    fi
    echo "$_K3S_POD_NAME"
}

# 容器内 profiles 目录（K3s PVC 路径）
readonly CONTAINER_PROFILES_DIR="/opt/data/profiles"

pf_get_container_registry_file() {
    echo "${CONTAINER_PROFILES_DIR}/registry.json"
}

# --- 文件 I/O 抽象函数 ---

pf_read_file() {
    local path="$1"
    local runtime
    runtime=$(pf_detect_runtime)

    if [[ "$runtime" == "k3s" ]]; then
        local kubectl pod container_path
        kubectl="$(_pf_get_kubectl)" || return 1
        pod="$(_pf_get_pod_name)" || return 1
        container_path="${path/#$(get_profiles_dir)/$CONTAINER_PROFILES_DIR}"
        $kubectl -n hermes exec "$pod" -- cat "$container_path" 2>/dev/null
    else
        cat "$path" 2>/dev/null
    fi
}

pf_write_file() {
    local path="$1"
    local content="$2"
    local uid="${3:-1000}"
    local runtime
    runtime=$(pf_detect_runtime)

    if [[ "$runtime" == "k3s" ]]; then
        local kubectl pod container_path
        kubectl="$(_pf_get_kubectl)" || return 1
        pod="$(_pf_get_pod_name)" || return 1
        container_path="${path/#$(get_profiles_dir)/$CONTAINER_PROFILES_DIR}"
        local b64
        b64=$(echo "$content" | base64 -w0 2>/dev/null || echo "$content" | base64)
        $kubectl -n hermes exec "$pod" -- bash -c "
            mkdir -p \"\$(dirname '$container_path')\"
            echo '$b64' | base64 -d > '$container_path'
            chown ${uid}:1000 '$container_path'
        " 2>/dev/null
    else
        mkdir -p "$(dirname "$path")"
        echo "$content" > "$path"
    fi
}

pf_file_exists() {
    local path="$1"
    local runtime
    runtime=$(pf_detect_runtime)

    if [[ "$runtime" == "k3s" ]]; then
        local kubectl pod container_path
        kubectl="$(_pf_get_kubectl)" || return 1
        pod="$(_pf_get_pod_name)" || return 1
        container_path="${path/#$(get_profiles_dir)/$CONTAINER_PROFILES_DIR}"
        $kubectl -n hermes exec "$pod" -- test -f "$container_path" 2>/dev/null
    else
        [[ -f "$path" ]]
    fi
}

pf_dir_exists() {
    local path="$1"
    local runtime
    runtime=$(pf_detect_runtime)

    if [[ "$runtime" == "k3s" ]]; then
        local kubectl pod container_path
        kubectl="$(_pf_get_kubectl)" || return 1
        pod="$(_pf_get_pod_name)" || return 1
        container_path="${path/#$(get_profiles_dir)/$CONTAINER_PROFILES_DIR}"
        $kubectl -n hermes exec "$pod" -- test -d "$container_path" 2>/dev/null
    else
        [[ -d "$path" ]]
    fi
}

pf_remove_dir() {
    local path="$1"
    local runtime
    runtime=$(pf_detect_runtime)

    if [[ "$runtime" == "k3s" ]]; then
        local kubectl pod container_path
        kubectl="$(_pf_get_kubectl)" || return 1
        pod="$(_pf_get_pod_name)" || return 1
        container_path="${path/#$(get_profiles_dir)/$CONTAINER_PROFILES_DIR}"
        $kubectl -n hermes exec "$pod" -- rm -rf "$container_path" 2>/dev/null
    else
        rm -rf "$path"
    fi
}

pf_mkdir() {
    local path="$1"
    local runtime
    runtime=$(pf_detect_runtime)

    if [[ "$runtime" == "k3s" ]]; then
        local kubectl pod container_path
        kubectl="$(_pf_get_kubectl)" || return 1
        pod="$(_pf_get_pod_name)" || return 1
        container_path="${path/#$(get_profiles_dir)/$CONTAINER_PROFILES_DIR}"
        $kubectl -n hermes exec "$pod" -- mkdir -p "$container_path" 2>/dev/null
    else
        mkdir -p "$path"
    fi
}

# 将本地临时目录推送到容器内 (K3s 模式专用)
pf_push_dir() {
    local local_dir="$1"
    local container_target="$2"
    local uid="${3:-1000}"
    local runtime
    runtime=$(pf_detect_runtime)

    if [[ "$runtime" != "k3s" ]]; then
        return 0
    fi

    local kubectl pod
    kubectl="$(_pf_get_kubectl)" || return 1
    pod="$(_pf_get_pod_name)" || return 1

    tar -cf - -C "$local_dir" . | \
        $kubectl -n hermes exec -i "$pod" -- bash -c "
            mkdir -p '$container_target'
            cd '$container_target' && tar -xf -
            chown -R ${uid}:1000 '$container_target'
            chmod 700 '$container_target'
        " 2>/dev/null
}

# 从容器读取文件到 stdout (用于 logs 等命令)
pf_cat_file() {
    local path="$1"
    local runtime
    runtime=$(pf_detect_runtime)

    if [[ "$runtime" == "k3s" ]]; then
        local kubectl pod container_path
        kubectl="$(_pf_get_kubectl)" || return 1
        pod="$(_pf_get_pod_name)" || return 1
        container_path="${path/#$(get_profiles_dir)/$CONTAINER_PROFILES_DIR}"
        $kubectl -n hermes exec "$pod" -- cat "$container_path" 2>/dev/null
    else
        cat "$path"
    fi
}

pf_tail_file() {
    local path="$1"
    local lines="${2:-50}"
    local follow="${3:-}"
    local runtime
    runtime=$(pf_detect_runtime)

    if [[ "$runtime" == "k3s" ]]; then
        local kubectl pod container_path
        kubectl="$(_pf_get_kubectl)" || return 1
        pod="$(_pf_get_pod_name)" || return 1
        container_path="${path/#$(get_profiles_dir)/$CONTAINER_PROFILES_DIR}"
        if [[ "$follow" == "true" ]]; then
            $kubectl -n hermes exec "$pod" -- tail -f -n "$lines" "$container_path" 2>/dev/null
        else
            $kubectl -n hermes exec "$pod" -- tail -n "$lines" "$container_path" 2>/dev/null
        fi
    else
        if [[ "$follow" == "true" ]]; then
            tail -f -n "$lines" "$path"
        else
            tail -n "$lines" "$path"
        fi
    fi
}

# ==================== 容器内命令执行 ====================

container_exec() {
    # 在容器内执行命令 (自动路由 k3s/docker)
    # Usage: container_exec "command string"
    local cmd="$1"
    local runtime
    runtime=$(pf_detect_runtime)

    if [[ "$runtime" == "k3s" ]]; then
        local kubectl pod
        kubectl="$(_pf_get_kubectl)" || return 1
        pod="$(_pf_get_pod_name)" || return 1
        $kubectl -n hermes exec "$pod" -- bash -c "$cmd" 2>/dev/null
    elif [[ "$runtime" == "docker" ]]; then
        docker exec hermes-gateway bash -c "$cmd" 2>/dev/null
    else
        eval "$cmd"
    fi
}

# ==================== s6 服务管理 ====================

s6_restart_service() {
    local name="$1"
    local svc_path="/run/service/gateway-${name}"
    container_exec "
        export PATH=\$PATH:/package/admin/s6-2.15.0.0/command
        s6-svc -r '${svc_path}'
    "
}

s6_stop_service() {
    local name="$1"
    local svc_path="/run/service/gateway-${name}"
    container_exec "
        export PATH=\$PATH:/package/admin/s6-2.15.0.0/command
        s6-svc -d '${svc_path}' 2>/dev/null || true
    "
}

# ==================== Gateway 验证 ====================

verify_gateway_port() {
    local port="$1"
    local max_attempts="${2:-15}"
    local attempt=1
    while [[ $attempt -le $max_attempts ]]; do
        if container_exec "bash -c '(echo > /dev/tcp/localhost/${port}) 2>/dev/null'" 2>/dev/null; then
            return 0
        fi
        sleep 2
        attempt=$((attempt + 1))
    done
    return 1
}

# ==================== s6 Run Script 生成 ====================

write_s6_run_script() {
    local name="$1"
    local port="$2"
    local svc_dir="/run/service/gateway-${name}"
    local run_script_content
    run_script_content=$(cat <<RUNEOF
#!/command/execlineb -P
with-contenv
s6-notifyoncheck -d -n 300 -w 1000 -c "nc -z localhost ${port}"
export HERMES_HOME "/opt/data/profiles/${name}"
export API_SERVER_HOST "0.0.0.0"
export API_SERVER_PORT "${port}"
/opt/hermes/bin/hermes -p ${name} gateway run
RUNEOF
)

    local runtime
    runtime=$(pf_detect_runtime)

    if [[ "$runtime" == "k3s" ]]; then
        local kubectl pod b64
        kubectl="$(_pf_get_kubectl)" || return 1
        pod="$(_pf_get_pod_name)" || return 1
        b64=$(printf '%s' "$run_script_content" | base64 -w0 2>/dev/null || printf '%s' "$run_script_content" | base64)
        $kubectl -n hermes exec "$pod" -- bash -c "
            mkdir -p '${svc_dir}'
            echo '${b64}' | base64 -d > '${svc_dir}/run'
            chmod +x '${svc_dir}/run'
        " 2>/dev/null
    elif [[ "$runtime" == "docker" ]]; then
        local b64
        b64=$(printf '%s' "$run_script_content" | base64 -w0 2>/dev/null || printf '%s' "$run_script_content" | base64)
        docker exec hermes-gateway bash -c "
            mkdir -p '${svc_dir}'
            echo '${b64}' | base64 -d > '${svc_dir}/run'
            chmod +x '${svc_dir}/run'
        " 2>/dev/null
    fi
}

write_gateway_state() {
    local name="$1"
    local port="$2"
    local state="${3:-running}"
    local timestamp
    timestamp="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

    local state_content
    state_content=$(jq -n \
        --arg state "$state" \
        --argjson port "$port" \
        --arg ts "$timestamp" \
        '{
            "gateway_state": $state,
            "api_server": {
                "host": "0.0.0.0",
                "port": $port,
                "state": "connected"
            },
            "updated_at": $ts
        }')

    local state_file
    state_file="$(get_profile_dir "$name")/gateway_state.json"
    pf_write_file "$state_file" "$state_content"
}
