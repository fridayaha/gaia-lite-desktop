#!/bin/bash
# Profile 管理脚本
# 在单容器内管理多个 Hermes Profile (每个 Profile 独立 Gateway 进程)
# 支持 Docker 和 K3s 两种运行环境

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/profile-common.sh"

# ==================== 命令: create ====================
cmd_create() {
    local name="$1"
    local provider="${2:-openai}"
    local model="${3:-gpt-4o}"
    local api_key="${4:-}"
    local base_url="${5:-}"

    validate_profile_name "$name"

    if profile_exists "$name"; then
        log_error "Profile 已存在: $name"
        exit 1
    fi

    if ! can_create_profile; then
        exit 1
    fi

    log_info "创建 Profile: $name (provider: $provider, model: $model)"

    local port
    port=$(allocate_profile_port)
    local uid
    uid=$(allocate_profile_uid)
    log_info "分配端口: $port, UID: $uid"

    local profile_dir
    profile_dir="$(get_profile_dir "$name")"
    pf_mkdir "$profile_dir"

    # 在本地临时目录生成配置文件，然后统一推送到容器 (K3s) 或直接复制 (Docker)
    local project_dir
    project_dir="$(get_project_dir)"
    local templates_dir="${project_dir}/configs/templates"
    local tmp_dir
    tmp_dir=$(mktemp -d)
    trap "rm -rf '$tmp_dir'" EXIT

    if [[ -f "${templates_dir}/profile-config.yaml.template" ]]; then
        sed -e "s/{{PROFILE_NAME}}/${name}/g" \
            -e "s/{{PROVIDER}}/${provider}/g" \
            -e "s/{{MODEL}}/${model}/g" \
            "${templates_dir}/profile-config.yaml.template" > "${tmp_dir}/config.yaml"
    fi

    if [[ -f "${templates_dir}/profile-SOUL.md.template" ]]; then
        sed "s/{{PROFILE_NAME}}/${name}/g" \
            "${templates_dir}/profile-SOUL.md.template" > "${tmp_dir}/SOUL.md"
    fi

    if [[ -f "${templates_dir}/profile-gateway.json.template" ]]; then
        cp "${templates_dir}/profile-gateway.json.template" "${tmp_dir}/gateway.json"
    fi

    # .env
    local api_key_line=""
    case "$provider" in
        openai)     api_key_line="OPENAI_API_KEY=${api_key}" ;;
        anthropic)  api_key_line="ANTHROPIC_API_KEY=${api_key}" ;;
        alibaba)    api_key_line="DASHSCOPE_API_KEY=${api_key}" ;;
        deepseek)   api_key_line="DEEPSEEK_API_KEY=${api_key}" ;;
        openrouter) api_key_line="OPENROUTER_API_KEY=${api_key}" ;;
        *)          api_key_line="HERMES_CUSTOM_API_KEY=${api_key}" ;;
    esac

    if [[ -f "${templates_dir}/profile-env.template" ]]; then
        sed -e "s/{{PROFILE_NAME}}/${name}/g" \
            -e "s/{{PROVIDER}}/${provider}/g" \
            -e "s/{{MODEL}}/${model}/g" \
            -e "s|{{BASE_URL}}|${base_url}|g" \
            -e "s/{{API_MODE}}/chat_completions/g" \
            -e "s|{{API_KEY_LINE}}|${api_key_line}|g" \
            -e "s/{{SKILLS}}//g" \
            "${templates_dir}/profile-env.template" > "${tmp_dir}/.env"
    fi

    # 推送到目标环境 (K3s: kubectl exec + tar; Docker: 直接复制)
    local runtime
    runtime=$(pf_detect_runtime)
    if [[ "$runtime" == "k3s" ]]; then
        # 将宿主机路径转换为容器路径
        local container_profile_dir="${profile_dir/#$(get_profiles_dir)/$CONTAINER_PROFILES_DIR}"
        pf_push_dir "$tmp_dir" "$container_profile_dir" "$uid"
    else
        # Docker/local: bind mount，直接复制
        for f in "${tmp_dir}/"*; do
            [[ -e "$f" ]] && cp "$f" "${profile_dir}/"
        done
        # .env 是隐藏文件，单独处理
        [[ -f "${tmp_dir}/.env" ]] && cp "${tmp_dir}/.env" "${profile_dir}/"
        set_data_permissions "$profile_dir"
    fi

    # 清理临时目录
    rm -rf "$tmp_dir"
    trap - EXIT

    register_profile "$name" "$port" "$provider" "$model" "$uid"

    log_info "Profile 创建成功: $name"
    log_info "  端口: $port"
    log_info "  目录: $profile_dir"
    log_info "  使用 'profile.sh start $name' 启动 Gateway"
}

# ==================== 命令: start ====================
cmd_start() {
    local name="$1"

    validate_profile_name "$name"

    if ! profile_exists "$name"; then
        log_error "Profile 不存在: $name"
        exit 1
    fi

    local port
    port=$(get_profile_port "$name")
    local uid
    uid=$(get_profile_uid "$name")
    local username
    username=$(get_profile_username "$name")

    local status
    status=$(get_profile_status "$name")

    if [[ "$status" == "running" ]]; then
        if verify_gateway_port "$port" 3; then
            log_warn "Profile 已在运行: $name (端口 $port)"
            return 0
        fi
        log_warn "状态显示 running 但端口未监听，重新启动..."
    fi

    log_info "启动 Profile: $name (端口 $port, UID $uid)"

    # 1. 更新注册表为 running (supervisor 通过 registry.json 恢复)
    update_profile_status "$name" "running"

    # 3. 确保用户存在并设置权限
    ensure_profile_user "$name" "$uid"
    set_profile_permissions "$name" "$uid"

    # 4. 确保 logs 目录存在
    local profile_home="${CONTAINER_PROFILES_DIR}/${name}"
    container_exec "
        mkdir -p '${profile_home}/logs'
        chown ${uid}:1000 '${profile_home}/logs'
    " 2>/dev/null

    # 5. 以 Profile 专属 UID 启动 gateway 进程
    container_exec "
        cd /opt/data
        . /opt/hermes/.venv/bin/activate
        export HOME='${profile_home}'
        export HERMES_HOME=\"${profile_home}\"
        export API_SERVER_HOST=\"0.0.0.0\"
        export API_SERVER_PORT=\"${port}\"
        export API_SERVER_MODEL_NAME=\"${name}\"
        nohup /package/admin/s6-2.15.0.0/command/s6-setuidgid ${username} hermes gateway run > \"${profile_home}/logs/gateway.out\" 2>&1 &
        echo \$!
    " 2>/dev/null
    log_info "已启动 gateway 进程 (UID=$uid, HERMES_HOME=${profile_home}, port=$port)"

    # 5. 等待端口就绪 (30 次 x 2 秒 = 60 秒)
    log_info "等待端口 $port 就绪..."
    if verify_gateway_port "$port" 30; then
        log_info "Profile 启动成功: $name (端口 $port)"
    else
        log_warn "端口 $port 未在 60 秒内就绪，Profile 可能仍在启动中"
    fi
}

# ==================== 命令: stop ====================
cmd_stop() {
    local name="$1"

    validate_profile_name "$name"

    if ! profile_exists "$name"; then
        log_error "Profile 不存在: $name"
        exit 1
    fi

    local status
    status=$(get_profile_status "$name")

    if [[ "$status" == "stopped" || "$status" == "created" ]]; then
        log_warn "Profile 未在运行: $name"
        return 0
    fi

    local port
    port=$(get_profile_port "$name")

    log_info "停止 Profile: $name"

    local username
    username=$(get_profile_username "$name")

    # 1. 在容器内 kill profile gateway 进程
    container_exec "
        pid=\$(ps -u '${username}' -o pid= 2>/dev/null | head -1)
        if [ -z \"\$pid\" ]; then
            pid=\$(ps aux | grep '/opt/hermes/.venv/bin/python3' | grep 'hermes -p ${name} gateway' | grep -v 'grep\|bash -c' | awk '{print \$2}' | head -1)
        fi
        if [ -n \"\$pid\" ]; then
            kill -TERM \$pid 2>/dev/null
            sleep 2
            kill -9 \$pid 2>/dev/null || true
        fi
    " 2>/dev/null

    # 2. 写入 stopped 状态 (供 reconcile)
    write_gateway_state "$name" "$port" "stopped"

    # 3. 更新注册表
    update_profile_status "$name" "stopped"
    log_info "Profile 停止成功: $name"
}

# ==================== 命令: restart ====================
cmd_restart() {
    local name="$1"

    validate_profile_name "$name"

    if ! profile_exists "$name"; then
        log_error "Profile 不存在: $name"
        exit 1
    fi

    log_info "重启 Profile: $name"
    cmd_stop "$name"
    sleep 1
    cmd_start "$name"
}

# ==================== 命令: setup ====================
cmd_setup() {
    local name="$1"

    validate_profile_name "$name"

    if ! profile_exists "$name"; then
        log_error "Profile 不存在: $name"
        exit 1
    fi

    local status
    status=$(get_profile_status "$name")

    # 1. 如果正在运行，先停掉
    if [[ "$status" == "running" ]]; then
        log_info "停止运行中的 Profile..."
        cmd_stop "$name"
        sleep 2
    fi

    log_info "启动交互式 setup 向导..."
    log_info "  - setup 完成后，退出向导会自动重启 Profile"
    log_info "  - 如需扫码等交互操作，终端已直通到容器内"

    local uid
    uid=$(get_profile_uid "$name")
    local username
    username=$(get_profile_username "$name")

    # 2. 交互式进入容器运行 hermes gateway setup
    local runtime
    runtime=$(pf_detect_runtime)

    if [[ "$runtime" == "k3s" ]]; then
        local kubectl pod
        kubectl="$(_pf_get_kubectl)" || { log_error "无法获取 kubectl"; exit 1; }
        pod="$(_pf_get_pod_name)" || { log_error "无法获取 Pod"; exit 1; }
        $kubectl -n hermes exec -it "$pod" -- bash -c "
            export HERMES_HOME=/opt/data/profiles/${name}
            s6-setuidgid ${username} hermes -p '${name}' gateway setup
        "
    elif [[ "$runtime" == "docker" ]]; then
        docker exec -it hermes-gateway bash -c "
            export HERMES_HOME=/opt/data/profiles/${name}
            s6-setuidgid ${username} hermes -p '${name}' gateway setup
        "
    else
        HERMES_HOME="/opt/data/profiles/${name}" hermes -p "${name}" gateway setup
    fi

    # 3. setup 完成后自动启动
    log_info "Setup 完成，重新启动 Profile..."
    sleep 1
    cmd_start "$name"
}

# ==================== 命令: delete ====================
cmd_delete() {
    local name="$1"
    local purge="${2:-}"

    validate_profile_name "$name"

    if ! profile_exists "$name"; then
        log_error "Profile 不存在: $name"
        exit 1
    fi

    local status
    status=$(get_profile_status "$name")

    if [[ "$status" == "running" ]]; then
        log_info "停止运行中的 Profile..."
        cmd_stop "$name"
    fi

    log_info "删除 Profile: $name"

    if [[ "$purge" == "--purge" ]]; then
        local profile_dir
        profile_dir="$(get_profile_dir "$name")"
        if pf_dir_exists "$profile_dir"; then
            pf_remove_dir "$profile_dir"
            log_info "已删除数据目录: $profile_dir"
        fi

        local username
        username=$(get_profile_username "$name")
        container_exec "userdel '${username}' 2>/dev/null || true"
        log_info "已删除用户: $username"
    else
        log_info "保留数据目录 (使用 --purge 完全删除)"
    fi

    unregister_profile "$name"
    log_info "Profile 删除成功: $name"
}

# ==================== 命令: list ====================
cmd_list() {
    local format="${1:-table}"

    local registry
    registry="$(read_profile_registry)"

    local count
    count=$(echo "$registry" | jq '.profiles | length')

    if [[ $count -eq 0 ]]; then
        log_info "没有 Profile (使用 'profile.sh create' 创建)"
        return 0
    fi

    if [[ "$format" == "json" ]]; then
        echo "$registry" | jq '.'
        return 0
    fi

    printf "%-20s %-8s %-8s %-15s %-20s\n" "NAME" "STATUS" "PORT" "PROVIDER" "MODEL"
    printf "%-20s %-8s %-8s %-15s %-20s\n" "----" "------" "----" "--------" "-----"

    local names
    names=$(echo "$registry" | jq -r '.profiles | keys[]')

    for name in $names; do
        local status port provider model
        status=$(echo "$registry" | jq -r ".profiles.\"$name\".status")
        port=$(echo "$registry" | jq -r ".profiles.\"$name\".port")
        provider=$(echo "$registry" | jq -r ".profiles.\"$name\".provider")
        model=$(echo "$registry" | jq -r ".profiles.\"$name\".model")

        printf "%-20s %-8s %-8s %-15s %-20s\n" "$name" "$status" "$port" "$provider" "$model"
    done
}

# ==================== 命令: status ====================
cmd_status() {
    local name="$1"

    validate_profile_name "$name"

    if ! profile_exists "$name"; then
        log_error "Profile 不存在: $name"
        exit 1
    fi

    local registry
    registry="$(read_profile_registry)"

    local profile_info
    profile_info=$(echo "$registry" | jq ".profiles.\"$name\"")

    local status port provider model created_at
    status=$(echo "$profile_info" | jq -r '.status')
    port=$(echo "$profile_info" | jq -r '.port')
    provider=$(echo "$profile_info" | jq -r '.provider')
    model=$(echo "$profile_info" | jq -r '.model')
    created_at=$(echo "$profile_info" | jq -r '.created_at')

    echo "Profile: $name"
    echo "  Status:   $status"
    echo "  Port:     $port"
    echo "  Provider: $provider"
    echo "  Model:    $model"
    echo "  Created:  $created_at"

    # 检测运行环境并显示进程信息
    local runtime
    runtime=$(pf_detect_runtime)

    if [[ "$runtime" == "k3s" ]]; then
        local kubectl pod
        kubectl="$(_pf_get_kubectl)" || true
        pod="$(_pf_get_pod_name)" || true
        if [[ -n "$kubectl" && -n "$pod" ]]; then
            echo ""
            echo "K3s Pod Process:"
            $kubectl -n hermes exec "$pod" -- bash -c "ps aux | grep \"HERMES_HOME=/opt/data/profiles/$name\" | grep -v grep" 2>/dev/null || echo "  (not running)"
        fi
    elif [[ "$runtime" == "docker" ]]; then
        echo ""
        echo "Container Process:"
        docker exec hermes-gateway bash -c "ps aux | grep \"HERMES_HOME=/opt/data/profiles/$name\" | grep -v grep" 2>/dev/null || echo "  (not running)"
    fi
}

# ==================== 命令: logs ====================
cmd_logs() {
    local name="$1"
    local follow="${2:-}"

    validate_profile_name "$name"

    if ! profile_exists "$name"; then
        log_error "Profile 不存在: $name"
        exit 1
    fi

    local profile_dir
    profile_dir="$(get_profile_dir "$name")"
    local log_file="${profile_dir}/gateway.log"

    if ! pf_file_exists "$log_file"; then
        log_warn "日志文件不存在: $log_file"
        log_info "Profile 可能尚未运行"
        return 0
    fi

    if [[ "$follow" == "-f" || "$follow" == "--follow" ]]; then
        pf_tail_file "$log_file" 50 "true"
    else
        pf_tail_file "$log_file" 50
    fi
}

# ==================== 命令: resources ====================
cmd_resources() {
    local registry
    registry="$(read_profile_registry)"

    local count
    count=$(echo "$registry" | jq '.profiles | length')

    echo "Profile Resources:"
    echo "  Total:   $count / $PROFILE_MAX_COUNT"
    echo "  Ports:   $PROFILE_BASE_PORT - $PROFILE_MAX_PORT"
    echo ""

    if [[ $count -gt 0 ]]; then
        echo "Allocated Ports:"
        echo "$registry" | jq -r '.profiles | to_entries[] | "  \(.key): \(.value.port)"'
    fi
}

# ==================== 帮助 ====================
show_help() {
    cat << 'EOF'
Profile Management - 单容器多 Profile 管理 (支持 Docker 和 K3s)

Usage: profile.sh <command> [options]

Commands:
  create <name> [provider] [model] [api-key] [base-url]
      创建新 Profile
      Example: profile.sh create alice alibaba qwen3.7-max sk-xxx

  start <name>
      启动 Profile Gateway

  stop <name>
      停止 Profile Gateway

  restart <name>
      重启 Profile Gateway

  setup <name>
      交互式配置 IM 平台 (自动 stop → setup 向导 → start)
      支持微信、飞书、QQ、Telegram 等平台的扫码/凭证配置

  delete <name> [--purge]
      删除 Profile (--purge 同时删除数据目录)

  list [table|json]
      列出所有 Profile

  status <name>
      查看 Profile 详细信息

  logs <name> [-f]
      查看 Profile 日志 (-f 持续输出)

  resources
      查看资源使用情况

  help
      显示此帮助

Profile 端口分配:
  Default Gateway: 8642 (Docker) / 30642 (K3s NodePort)
  Profile Gateway: 8643-8650 (Docker) / 30643-30650 (K3s NodePort)
  Dashboard:       9119 (Docker) / 30119 (K3s NodePort)

运行环境自动检测:
  脚本会自动检测 Docker 或 K3s 环境并使用相应的命令:
  - Docker: docker exec, docker kill
  - K3s:    kubectl exec
EOF
}

# ==================== 主入口 ====================
main() {
    local command="${1:-help}"
    shift || true

    case "$command" in
        create)
            [[ $# -lt 1 ]] && { log_error "用法: profile.sh create <name> [provider] [model] [api-key] [base-url]"; exit 1; }
            cmd_create "$@"
            ;;
        start)
            [[ $# -lt 1 ]] && { log_error "用法: profile.sh start <name>"; exit 1; }
            cmd_start "$1"
            ;;
        stop)
            [[ $# -lt 1 ]] && { log_error "用法: profile.sh stop <name>"; exit 1; }
            cmd_stop "$1"
            ;;
        restart)
            [[ $# -lt 1 ]] && { log_error "用法: profile.sh restart <name>"; exit 1; }
            cmd_restart "$1"
            ;;
        setup)
            [[ $# -lt 1 ]] && { log_error "用法: profile.sh setup <name>"; exit 1; }
            cmd_setup "$1"
            ;;
        delete)
            [[ $# -lt 1 ]] && { log_error "用法: profile.sh delete <name> [--purge]"; exit 1; }
            cmd_delete "$@"
            ;;
        list|ls)
            cmd_list "${1:-table}"
            ;;
        status)
            [[ $# -lt 1 ]] && { log_error "用法: profile.sh status <name>"; exit 1; }
            cmd_status "$1"
            ;;
        logs)
            [[ $# -lt 1 ]] && { log_error "用法: profile.sh logs <name> [-f]"; exit 1; }
            cmd_logs "$@"
            ;;
        resources)
            cmd_resources
            ;;
        help|--help|-h)
            show_help
            ;;
        *)
            log_error "未知命令: $command"
            show_help
            exit 1
            ;;
    esac
}

main "$@"
