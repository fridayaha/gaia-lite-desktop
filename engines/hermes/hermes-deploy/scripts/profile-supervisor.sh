#!/bin/bash
# Profile Gateway Supervisor
# 在容器内管理多个 Profile 的 Gateway 进程
# 由 s6-overlay 作为 longrun 服务运行

set -uo pipefail

PROFILES_DIR="/opt/data/profiles"
REGISTRY_FILE="${PROFILES_DIR}/registry.json"
COMMANDS_FILE="${PROFILES_DIR}/commands.json"
PID_MAP_FILE="${PROFILES_DIR}/pid_map.json"
HERMES_BIN="hermes"
POLL_INTERVAL=5
MAX_RESTART_COUNT=10
MAX_BACKOFF=60

declare -A PROFILE_PIDS
declare -A PROFILE_PORTS
declare -A PROFILE_UIDS
declare -A PROFILE_RESTART_COUNT
declare -A PROFILE_LAST_START

log() {
    echo "[profile-supervisor] $(date '+%Y-%m-%d %H:%M:%S') $*"
}

# ==================== 信号处理 ====================
handle_sigterm() {
    log "SIGTERM received, stopping all profile gateways..."
    for name in "${!PROFILE_PIDS[@]}"; do
        stop_profile "$name"
    done
    wait
    log "All profiles stopped, exiting."
    exit 0
}

handle_sighup() {
    log "SIGHUP received, reloading..."
    process_commands
    reconcile_profiles
}

trap 'handle_sigterm' SIGTERM
trap 'handle_sighup' SIGHUP

# ==================== UID 管理 ====================
ensure_profile_user() {
    local name="$1"
    local uid="$2"
    local username="hermes-p-${name}"

    if ! id -u "$username" >/dev/null 2>&1; then
        log "Creating user $username (UID $uid)..."
        useradd -u "$uid" -g hermes -d "/opt/data/profiles/${name}" \
            -s /bin/bash -M "$username" 2>/dev/null || true
    fi
}

fix_profile_permissions() {
    local name="$1"
    local uid="$2"
    local home="${PROFILES_DIR}/${name}"

    if [[ -d "$home" ]]; then
        chown -R "${uid}:1000" "$home"
        chmod 700 "$home"
    fi
}

# ==================== 注册表读取 ====================
load_registry() {
    if [[ ! -f "$REGISTRY_FILE" ]]; then
        log "No registry file found, waiting for profiles to be created..."
        return 0
    fi

    local registry
    registry=$(cat "$REGISTRY_FILE" 2>/dev/null || echo '{"profiles":{}}')

    local names
    names=$(echo "$registry" | jq -r '.profiles // {} | keys[]' 2>/dev/null || true)

    for name in $names; do
        local port status uid
        port=$(echo "$registry" | jq -r --arg n "$name" '.profiles[$n].port // 0')
        status=$(echo "$registry" | jq -r --arg n "$name" '.profiles[$n].status // "created"')
        uid=$(echo "$registry" | jq -r --arg n "$name" '.profiles[$n].uid // 1000')
        PROFILE_PORTS[$name]=$port
        PROFILE_UIDS[$name]=$uid

        if [[ "$status" == "running" ]]; then
            if [[ -z "${PROFILE_PIDS[$name]:-}" ]]; then
                start_profile "$name" "$port"
            fi
        fi
    done
}

update_registry_status() {
    local name="$1"
    local status="$2"

    if [[ ! -f "$REGISTRY_FILE" ]]; then
        return 0
    fi

    local registry
    registry=$(cat "$REGISTRY_FILE" 2>/dev/null || echo '{"profiles":{}}')
    local updated
    updated=$(echo "$registry" | jq --arg n "$name" --arg s "$status" '.profiles[$n].status = $s')

    local tmp="${REGISTRY_FILE}.tmp"
    echo "$updated" > "$tmp" && mv "$tmp" "$REGISTRY_FILE"
}

write_pid_map() {
    local map="{"
    local first=true
    for name in "${!PROFILE_PIDS[@]}"; do
        if [[ "$first" == "true" ]]; then
            first=false
        else
            map+=","
        fi
        map+="\"$name\":${PROFILE_PIDS[$name]}"
    done
    map+="}"
    echo "$map" > "$PID_MAP_FILE" 2>/dev/null || true
}

# ==================== 进程管理 ====================
start_profile() {
    local name="$1"
    local port="$2"
    local home="${PROFILES_DIR}/${name}"

    if [[ ! -d "$home" ]]; then
        log "ERROR: Profile directory not found: $home"
        return 1
    fi

    local uid="${PROFILE_UIDS[$name]:-1000}"
    local username="hermes-p-${name}"

    ensure_profile_user "$name" "$uid"
    fix_profile_permissions "$name" "$uid"

    log "Starting profile '$name' on port $port (UID $uid)..."

    local -a env_arr=()
    env_arr+=(HERMES_HOME="$home")
    env_arr+=(API_SERVER_PORT="$port")
    env_arr+=(API_SERVER_MODEL_NAME="$name")
    env_arr+=(HERMES_DASHBOARD=0)
    env_arr+=(HOME="$home")

    if [[ -f "$home/.env" ]]; then
        while IFS='=' read -r key value || [[ -n "$key" ]]; do
            [[ "$key" =~ ^[[:space:]]*# ]] && continue
            [[ -z "$key" ]] && continue
            key="${key#"${key%%[![:space:]]*}"}"
            key="${key%"${key##*[![:space:]]}"}"
            [[ -z "$key" ]] && continue
            env_arr+=("$key=$value")
        done < "$home/.env"
    fi

    if [[ "$uid" != "1000" ]]; then
        s6-setuidgid "$username" env "${env_arr[@]}" $HERMES_BIN gateway run &
    else
        env "${env_arr[@]}" $HERMES_BIN gateway run &
    fi
    local pid=$!

    PROFILE_PIDS[$name]=$pid
    PROFILE_RESTART_COUNT[$name]=0
    PROFILE_LAST_START[$name]=$(date +%s)

    update_registry_status "$name" "running"
    write_pid_map
    log "Started profile '$name' (PID $pid, UID $uid, port $port)"
}

stop_profile() {
    local name="$1"
    local pid="${PROFILE_PIDS[$name]:-}"

    if [[ -z "$pid" ]]; then
        return 0
    fi

    log "Stopping profile '$name' (PID $pid)..."

    # 发送 SIGTERM
    kill -TERM "$pid" 2>/dev/null || true

    # 等待最多 10 秒
    local waited=0
    while kill -0 "$pid" 2>/dev/null && [[ $waited -lt 10 ]]; do
        sleep 1
        waited=$((waited + 1))
    done

    # 强制杀死
    if kill -0 "$pid" 2>/dev/null; then
        log "WARN: Force killing profile '$name' (PID $pid)"
        kill -9 "$pid" 2>/dev/null || true
    fi

    unset PROFILE_PIDS[$name]
    unset PROFILE_RESTART_COUNT[$name]
    unset PROFILE_LAST_START[$name]

    update_registry_status "$name" "stopped"
    write_pid_map
    log "Stopped profile '$name'"
}

# ==================== 指令处理 ====================
process_commands() {
    if [[ ! -f "$COMMANDS_FILE" ]]; then
        return 0
    fi

    local commands
    commands=$(cat "$COMMANDS_FILE" 2>/dev/null || echo '{"commands":[]}')

    local count
    count=$(echo "$commands" | jq '.commands | length' 2>/dev/null || echo "0")

    for ((i=0; i<count; i++)); do
        local action name
        action=$(echo "$commands" | jq -r ".commands[$i].action // empty")
        name=$(echo "$commands" | jq -r ".commands[$i].profile // empty")

        if [[ -z "$action" || -z "$name" ]]; then
            continue
        fi

        log "Processing command: $action $name"

        case "$action" in
            start)
                local port="${PROFILE_PORTS[$name]:-0}"
                if [[ "$port" -gt 0 ]]; then
                    start_profile "$name" "$port"
                else
                    log "ERROR: No port allocated for profile '$name'"
                fi
                ;;
            stop)
                stop_profile "$name"
                ;;
            restart)
                stop_profile "$name"
                sleep 1
                local port="${PROFILE_PORTS[$name]:-0}"
                if [[ "$port" -gt 0 ]]; then
                    start_profile "$name" "$port"
                fi
                ;;
            *)
                log "WARN: Unknown command action: $action"
                ;;
        esac
    done

    # 清空指令文件
    rm -f "$COMMANDS_FILE"
}

# ==================== 进程监控 ====================
check_processes() {
    local now
    now=$(date +%s)

    local -a names_to_remove=()

    for name in "${!PROFILE_PIDS[@]}"; do
        local pid="${PROFILE_PIDS[$name]}"

        if ! kill -0 "$pid" 2>/dev/null; then
            local last_start="${PROFILE_LAST_START[$name]:-$now}"
            local uptime=$((now - last_start))

            # 运行超过 60 秒则重置重启计数
            if [[ $uptime -gt 60 ]]; then
                PROFILE_RESTART_COUNT[$name]=0
            fi

            local count="${PROFILE_RESTART_COUNT[$name]:-0}"
            PROFILE_RESTART_COUNT[$name]=$((count + 1))

            if [[ $count -ge $MAX_RESTART_COUNT ]]; then
                log "ERROR: Profile '$name' exceeded max restarts ($MAX_RESTART_COUNT), marking error"
                update_registry_status "$name" "error"
                names_to_remove+=("$name")
                continue
            fi

            # 指数退避
            local delay=$((1 << count))
            if [[ $delay -gt $MAX_BACKOFF ]]; then
                delay=$MAX_BACKOFF
            fi

            log "Profile '$name' crashed, restart in ${delay}s ($((count+1))/$MAX_RESTART_COUNT)..."
            sleep "$delay"

            local port="${PROFILE_PORTS[$name]:-0}"
            if [[ "$port" -gt 0 ]]; then
                start_profile "$name" "$port"
            fi
        fi
    done

    for name in "${names_to_remove[@]}"; do
        unset PROFILE_PIDS[$name]
    done
    if [[ ${#names_to_remove[@]} -gt 0 ]]; then
        write_pid_map
    fi
}

reconcile_profiles() {
    if [[ ! -f "$REGISTRY_FILE" ]]; then
        return 0
    fi

    local registry
    registry=$(cat "$REGISTRY_FILE" 2>/dev/null || echo '{"profiles":{}}')

    local names
    names=$(echo "$registry" | jq -r '.profiles // {} | keys[]' 2>/dev/null || true)

    for name in $names; do
        local port status uid
        port=$(echo "$registry" | jq -r --arg n "$name" '.profiles[$n].port // 0')
        status=$(echo "$registry" | jq -r --arg n "$name" '.profiles[$n].status // "created"')
        uid=$(echo "$registry" | jq -r --arg n "$name" '.profiles[$n].uid // 1000')

        PROFILE_PORTS[$name]=$port
        PROFILE_UIDS[$name]=$uid

        if [[ "$status" == "running" && -z "${PROFILE_PIDS[$name]:-}" ]]; then
            start_profile "$name" "$port"
        elif [[ "$status" == "stopped" && -n "${PROFILE_PIDS[$name]:-}" ]]; then
            stop_profile "$name"
        fi
    done

    # 停止注册表中已删除的 Profile
    for name in "${!PROFILE_PIDS[@]}"; do
        local in_registry
        in_registry=$(echo "$registry" | jq -r --arg n "$name" '.profiles | has($n)')
        if [[ "$in_registry" != "true" ]]; then
            log "Profile '$name' removed from registry, stopping..."
            stop_profile "$name"
        fi
    done
}

# ==================== 主循环 ====================
main() {
    log "Profile supervisor starting (PID $$)..."
    log "Profiles directory: $PROFILES_DIR"
    log "Poll interval: ${POLL_INTERVAL}s"

    # 修复 PVC 挂载后 Hermes 主进程可能覆盖的目录权限
    # /opt/data 和 /opt/data/profiles 需要对 Profile 用户可穿越
    # Profile 用户属于 hermes 组 (gid 1000)，需要同时设置 g+x 和 o+x
    # 需要在主循环中持续修复，因为 s6 并发启动可能导致主进程在 supervisor 之后修改权限
    chmod ug+rwx,o+x /opt/data 2>/dev/null || true
    chmod ug+rwx,o+x "$PROFILES_DIR" 2>/dev/null || true

    load_registry

    while true; do
        # 自愈: 确保父目录对 Profile 用户可穿越
        chmod g+x,o+x /opt/data 2>/dev/null || true
        process_commands
        check_processes
        sleep "$POLL_INTERVAL"
    done
}

main
