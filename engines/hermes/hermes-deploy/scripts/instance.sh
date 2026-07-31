#!/bin/bash
# Hermes Agent 多实例管理器
# 为不同用户创建和管理独立的 Hermes Agent 实例
# 支持 Docker Compose 和 K3s/Kubernetes 两种运行环境
# 用法: bash scripts/instance.sh <command> [args]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/common.sh"
source "${SCRIPT_DIR}/lib/resource.sh"
source "${SCRIPT_DIR}/lib/instance-k8s.sh"

PROJECT_DIR="$(get_project_dir)"
INSTANCES_DIR="${PROJECT_DIR}/data/instances"
TEMPLATES_DIR="${PROJECT_DIR}/configs/templates"

# ==================== 运行环境检测 ====================
detect_runtime() {
    _detect_instance_runtime 2>/dev/null || echo "local"
}

is_k3s() {
    local runtime
    runtime="$(detect_runtime)"
    [[ "$runtime" == "k3s" ]]
}

is_docker() {
    local runtime
    runtime="$(detect_runtime)"
    [[ "$runtime" == "docker" ]]
}

# ==================== 模型配置 ====================
configure_instance_model() {
    local instance_id="$1"
    local instance_dir="${INSTANCES_DIR}/${instance_id}"
    local env_file="${instance_dir}/.env"
    local hermes_data_dir="${instance_dir}/hermes-data"
    local config_file="${hermes_data_dir}/config.yaml"
    local container_name="hermes-gateway-${instance_id}"

    if [[ ! -f "$env_file" ]]; then
        log_warn "实例 .env 文件不存在，跳过模型配置"
        return 0
    fi

    log_info "配置实例模型..."

    # 读取 .env 中的配置 (用临时变量避免污染当前 shell)
    local _provider _base_url _model _api_key _api_key_standard
    _provider=$(grep -E '^HERMES_PROVIDER=' "$env_file" | cut -d= -f2-)
    _base_url=$(grep -E '^HERMES_BASE_URL=' "$env_file" | cut -d= -f2-)
    _model=$(grep -E '^HERMES_MODEL=' "$env_file" | cut -d= -f2-)
    _api_key=$(grep -E '^HERMES_CUSTOM_API_KEY=' "$env_file" | cut -d= -f2-)
    _api_key_standard=$(grep -E '^OPENAI_API_KEY=' "$env_file" | cut -d= -f2-)

    _model="${_model:-gpt-4o}"

    # 确保 config.yaml 存在（s6-overlay 在首次启动时会生成默认配置）
    if [[ ! -f "$config_file" ]]; then
        log_warn "config.yaml 不存在，等待容器生成..."
        sleep 5
    fi

    # 根据 provider 类型选择配置方式
    # 优先级: 已知 provider > base_url 自定义端点 > 标准 provider
    case "$_provider" in
        alibaba|alibaba-coding-plan)
            # === 百炼（DashScope） (内置 alibaba provider) ===
            log_info "配置百炼（DashScope） provider: ${_provider}"

            _inject_model_config "$config_file" "$_provider" "$_model" ""

            # DASHSCOPE_API_KEY + DASHSCOPE_BASE_URL 写入 /opt/data/.env
            local dashscope_key="${_api_key:-${_api_key_standard:-}}"
            if [[ -n "$dashscope_key" ]]; then
                docker exec "$container_name" bash -c "
                    sed -i '/^DASHSCOPE_API_KEY=/d' /opt/data/.env 2>/dev/null || true
                    echo 'DASHSCOPE_API_KEY=${dashscope_key}' >> /opt/data/.env
                    sed -i '/^DASHSCOPE_BASE_URL=/d' /opt/data/.env 2>/dev/null || true
                    echo 'DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1' >> /opt/data/.env
                " || true
                log_info "DASHSCOPE_API_KEY + DASHSCOPE_BASE_URL 已写入 /opt/data/.env"
            fi

            log_info "百炼（DashScope）配置完成: ${_model}"
            ;;

        custom|openai|anthropic|deepseek|openrouter|gemini|zai|kimi-coding|minimax|minimax-cn|novita|arcee|gmi|xiaomi|stepfun|huggingface|nvidia|opencode-zen|opencode-go|kilocode|lmstudio|xai|tencent-tokenhub|qwen-oauth|minimax-oauth|ollama-cloud)
            # === 已知标准 provider (无自定义 base_url) ===
            log_info "配置标准提供商: ${_provider} / ${_model}"

            # 如果同时提供了 base_url，传给 config.yaml
            _inject_model_config "$config_file" "$_provider" "$_model" "$_base_url"

            # 根据 provider 写入对应的 API Key 到 /opt/data/.env
            local effective_key="${_api_key:-${_api_key_standard:-}}"
            if [[ -n "$effective_key" ]]; then
                local env_var_name
                case "$_provider" in
                    openai)     env_var_name="OPENAI_API_KEY" ;;
                    anthropic)  env_var_name="ANTHROPIC_API_KEY" ;;
                    deepseek)   env_var_name="DEEPSEEK_API_KEY" ;;
                    openrouter) env_var_name="OPENROUTER_API_KEY" ;;
                    gemini)     env_var_name="GOOGLE_API_KEY" ;;
                    zai)        env_var_name="GLM_API_KEY" ;;
                    kimi-coding)env_var_name="KIMI_API_KEY" ;;
                    minimax)    env_var_name="MINIMAX_API_KEY" ;;
                    minimax-cn) env_var_name="MINIMAX_CN_API_KEY" ;;
                    novita)     env_var_name="NOVITA_API_KEY" ;;
                    arcee)      env_var_name="ARCEEAI_API_KEY" ;;
                    gmi)        env_var_name="GMI_API_KEY" ;;
                    xiaomi)     env_var_name="XIAOMI_API_KEY" ;;
                    stepfun)    env_var_name="STEPFUN_API_KEY" ;;
                    huggingface)env_var_name="HF_TOKEN" ;;
                    nvidia)     env_var_name="NVIDIA_API_KEY" ;;
                    opencode-zen) env_var_name="OPENCODE_ZEN_API_KEY" ;;
                    opencode-go)  env_var_name="OPENCODE_GO_API_KEY" ;;
                    kilocode)   env_var_name="KILOCODE_API_KEY" ;;
                    xai)        env_var_name="XAI_API_KEY" ;;
                    tencent-tokenhub) env_var_name="TOKENHUB_API_KEY" ;;
                    *)          env_var_name="OPENAI_API_KEY" ;;
                esac

                docker exec "$container_name" bash -c "
                    sed -i '/^${env_var_name}=/d' /opt/data/.env 2>/dev/null || true
                    echo '${env_var_name}=${effective_key}' >> /opt/data/.env
                " || true
                log_info "${env_var_name} 已写入 /opt/data/.env"
            fi

            log_info "标准模型配置完成: ${_provider} / ${_model}"
            ;;

        *)
            # === 其他未知 provider — 按自定义端点处理 ===
            if [[ -n "$_base_url" ]]; then
                log_info "配置自定义端点: ${_base_url}"

                _inject_model_config "$config_file" "custom" "$_model" "$_base_url"

                local effective_key="${_api_key:-${_api_key_standard:-}}"
                if [[ -n "$effective_key" ]]; then
                    docker exec "$container_name" bash -c "
                        sed -i '/^OPENAI_API_KEY=/d' /opt/data/.env 2>/dev/null || true
                        echo 'OPENAI_API_KEY=${effective_key}' >> /opt/data/.env
                    " || true
                    log_info "API Key 已写入 /opt/data/.env"
                fi

                log_info "自定义模型配置完成: ${_model} @ ${_base_url}"
            else
                log_warn "未提供 base_url，无法配置未知 provider '${_provider}'"
            fi
            ;;
    esac

    # 重启容器使配置生效
    log_info "重启容器以应用模型配置..."
    docker restart "$container_name" || true

    sleep 5
    if wait_for_healthy "$container_name" 12 5; then
        log_info "模型配置已生效"
    else
        log_warn "容器重启后健康检查未通过，请手动检查"
    fi
}

# 注入 model 配置到 config.yaml
# 用法: _inject_model_config <config_file> <provider> <model> <base_url>
_inject_model_config() {
    local config_file="$1"
    local provider="$2"
    local model="$3"
    local base_url="${4:-}"

    # 如果 config.yaml 不存在，创建一个最小的
    if [[ ! -f "$config_file" ]]; then
        cat > "$config_file" <<EOF
# Hermes Agent 配置 (自动生成)
model:
  provider: "${provider}"
  default: "${model}"
EOF
        if [[ -n "$base_url" ]]; then
            echo "  base_url: \"${base_url}\"" >> "$config_file"
        fi
        return 0
    fi

    # 用 sed 修改现有的 config.yaml
    # 处理注释行（如 `  # provider: xxx`）和未注释行

    # 先备份
    cp "$config_file" "${config_file}.bak.$(date +%s)" 2>/dev/null || true

    # 替换或添加 provider (匹配注释和未注释的 provider 行)
    if grep -qE '^\s*#?\s*provider:' "$config_file"; then
        sed -i -E "s|^(\s*)#?\s*provider:.*|\1provider: \"${provider}\"|" "$config_file"
    elif grep -q '^model:' "$config_file"; then
        sed -i "/^model:/a\  provider: \"${provider}\"" "$config_file"
    else
        sed -i "1i\model:\n  provider: \"${provider}\"" "$config_file"
    fi

    # 替换或添加 default (匹配注释和未注释的 default 行)
    if grep -qE '^\s*#?\s*default:' "$config_file"; then
        sed -i -E "s|^(\s*)#?\s*default:.*|\1default: \"${model}\"|" "$config_file"
    elif grep -q '^model:' "$config_file"; then
        sed -i "/^model:/a\  default: \"${model}\"" "$config_file"
    fi

    # 替换或添加 base_url (仅自定义端点)
    if [[ -n "$base_url" ]]; then
        if grep -qE '^\s*#?\s*base_url:' "$config_file"; then
            sed -i -E "s|^(\s*)#?\s*base_url:.*|\1base_url: \"${base_url}\"|" "$config_file"
        elif grep -q '^model:' "$config_file"; then
            sed -i "/^model:/a\  base_url: \"${base_url}\"" "$config_file"
        fi
    fi

    log_info "config.yaml 已更新"
}

# ==================== 创建实例 ====================
cmd_create() {
    local instance_id="$1"
    local memory_mb="${2:-768}"
    local cpu_limit="${3:-0.75}"
    local llm_provider="${4:-openai}"
    local base_url="${5:-}"
    local llm_api_key="${6:-}"
    local model="${7:-}"
    local skills="${8:-}"

    # 验证 ID 格式
    validate_instance_id "$instance_id" || return 1

    # 检查是否已存在
    if instance_exists "$instance_id"; then
        log_error "实例已存在: $instance_id"
        return 1
    fi

    # 资源预算检查
    if ! can_create_instance "$memory_mb" "$cpu_limit"; then
        return 1
    fi

    # Provider 映射: 非标准名称自动归为 custom
    local effective_provider="$llm_provider"
    case "$llm_provider" in
        openai|anthropic|deepseek|openrouter|custom|alibaba|alibaba-coding-plan|gemini|zai|kimi-coding|minimax|minimax-cn|novita|arcee|gmi|xiaomi|stepfun|huggingface|nvidia|opencode-zen|opencode-go|kilocode|lmstudio|xai|tencent-tokenhub|qwen-oauth|minimax-oauth|ollama-cloud)
            ;;
        *)
            log_info "非标准 provider '$llm_provider' → 映射为 custom"
            effective_provider="custom"
            ;;
    esac

    log_info "创建实例: $instance_id (内存: ${memory_mb}MB, CPU: ${cpu_limit}, provider: ${effective_provider})"

    # 检测运行环境并路由到对应的创建逻辑
    if is_k3s; then
        cmd_create_k3s "$instance_id" "$memory_mb" "$cpu_limit" "$effective_provider" "$base_url" "$llm_api_key" "$model" "$skills"
        return $?
    fi

    # 以下为 Docker Compose 创建逻辑
    local gateway_token=$(generate_token)
    local created_at=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

    # 创建实例目录
    local instance_dir="${INSTANCES_DIR}/${instance_id}"
    mkdir -p "${instance_dir}/hermes-data/skills"
    mkdir -p "${instance_dir}/hermes-data/sessions"
    mkdir -p "${instance_dir}/hermes-data/memories"

    # 渲染环境变量文件
    local env_file="${instance_dir}/.env"

    # 根据 effective_provider 分发 API Key
    # 每个 provider 写入其专用的环境变量名
    local provider_key_name="" provider_key_value=""
    case "$effective_provider" in
        openai)           provider_key_name="OPENAI_API_KEY" ;;
        anthropic)        provider_key_name="ANTHROPIC_API_KEY" ;;
        deepseek)         provider_key_name="DEEPSEEK_API_KEY" ;;
        openrouter)       provider_key_name="OPENROUTER_API_KEY" ;;
        alibaba|alibaba-coding-plan) provider_key_name="DASHSCOPE_API_KEY" ;;
        gemini)           provider_key_name="GOOGLE_API_KEY" ;;
        zai)              provider_key_name="GLM_API_KEY" ;;
        kimi-coding)      provider_key_name="KIMI_API_KEY" ;;
        minimax)          provider_key_name="MINIMAX_API_KEY" ;;
        minimax-cn)       provider_key_name="MINIMAX_CN_API_KEY" ;;
        novita)           provider_key_name="NOVITA_API_KEY" ;;
        arcee)            provider_key_name="ARCEEAI_API_KEY" ;;
        gmi)              provider_key_name="GMI_API_KEY" ;;
        xiaomi)           provider_key_name="XIAOMI_API_KEY" ;;
        stepfun)          provider_key_name="STEPFUN_API_KEY" ;;
        huggingface)      provider_key_name="HF_TOKEN" ;;
        nvidia)           provider_key_name="NVIDIA_API_KEY" ;;
        opencode-zen)     provider_key_name="OPENCODE_ZEN_API_KEY" ;;
        opencode-go)      provider_key_name="OPENCODE_GO_API_KEY" ;;
        kilocode)         provider_key_name="KILOCODE_API_KEY" ;;
        xai)              provider_key_name="XAI_API_KEY" ;;
        tencent-tokenhub) provider_key_name="TOKENHUB_API_KEY" ;;
        *)                provider_key_name="OPENAI_API_KEY" ;;  # custom 等用 OPENAI_API_KEY
    esac
    provider_key_value="$llm_api_key"

    # 百炼（DashScope）需要额外的 base_url
    local dashscope_base_url=""
    if [[ "$effective_provider" == "alibaba" || "$effective_provider" == "alibaba-coding-plan" ]]; then
        dashscope_base_url="${base_url:-https://dashscope.aliyuncs.com/compatible-mode/v1}"
    fi

    # 模型默认值
    local effective_model="${model:-gpt-4o}"

    # 生成所有 API Key 变量（未使用的为空）
    local all_keys=""
    for key_name in OPENAI_API_KEY ANTHROPIC_API_KEY DEEPSEEK_API_KEY OPENROUTER_API_KEY \
        DASHSCOPE_API_KEY GOOGLE_API_KEY GLM_API_KEY KIMI_API_KEY MINIMAX_API_KEY MINIMAX_CN_API_KEY \
        NOVITA_API_KEY ARCEEAI_API_KEY GMI_API_KEY XIAOMI_API_KEY STEPFUN_API_KEY HF_TOKEN \
        NVIDIA_API_KEY OPENCODE_ZEN_API_KEY OPENCODE_GO_API_KEY KILOCODE_API_KEY XAI_API_KEY TOKENHUB_API_KEY; do
        if [[ "$key_name" == "$provider_key_name" ]]; then
            all_keys+="${key_name}=${provider_key_value}"$'\n'
        else
            all_keys+="${key_name}="$'\n'
        fi
    done

    cat > "$env_file" <<EOF
# Hermes Agent 实例环境变量
# 实例 ID: ${instance_id}
# 创建时间: ${created_at}

HERMES_UID=1000
HERMES_GID=1000

# LLM 提供商配置
HERMES_PROVIDER=${effective_provider}
HERMES_BASE_URL=${base_url}
HERMES_MODEL=${effective_model}

# API Key (仅对应 provider 有值)
${all_keys}DASHSCOPE_BASE_URL=${dashscope_base_url}

# 技能预装 (由 container-init.sh 在容器启动时安装)
HERMES_PRELOAD_SKILLS=${skills}

# 日志
HERMES_LOG_FORMAT=json
EOF

    # 分配端口 (基于已有实例数量，避免冲突)
    local base_gateway_port=18642
    local base_dashboard_port=19119
    local instance_count=$(read_registry | jq '.instances | length')
    local gateway_port=$((base_gateway_port + instance_count))
    local dashboard_port=$((base_dashboard_port + instance_count))

    # 渲染 docker-compose.yml
    local compose_file="${instance_dir}/docker-compose.yml"
    sed -e "s/{{INSTANCE_ID}}/${instance_id}/g" \
        -e "s/{{MEMORY_LIMIT_MB}}/${memory_mb}/g" \
        -e "s/{{CPU_LIMIT}}/${cpu_limit}/g" \
        -e "s/{{GATEWAY_PORT}}/${gateway_port}/g" \
        -e "s/{{DASHBOARD_PORT}}/${dashboard_port}/g" \
        "${TEMPLATES_DIR}/docker-compose.instance.yml.template" > "$compose_file"

    # 复制基础配置文件 (可选覆盖，s6-overlay 会自动初始化默认配置)
    # 如需自定义，取消以下注释：
    # cp "${PROJECT_DIR}/configs/hermes-config.yaml" "${instance_dir}/hermes-data/config.yaml"
    # cp "${PROJECT_DIR}/configs/SOUL.md" "${instance_dir}/hermes-data/SOUL.md"
    # cp "${PROJECT_DIR}/configs/gateway.json" "${instance_dir}/hermes-data/gateway.json"

    # 设置权限
    chmod 600 "$env_file"
    set_data_permissions "${instance_dir}/hermes-data"

    # 注册实例
    local container_name="hermes-gateway-${instance_id}"
    register_instance "$instance_id" "$created_at" "$memory_mb" "$cpu_limit" \
        "$gateway_token" "$container_name" "created" "$gateway_port" "$dashboard_port"

    log_info "实例创建完成: $instance_id"
    log_info "  Gateway Token: ${gateway_token:0:16}..."
    log_info "  Gateway 端口: ${gateway_port}"
    log_info "  Dashboard 端口: ${dashboard_port}"

    # Skills 通过 HERMES_PRELOAD_SKILLS 环境变量传递
    # 官方镜像的 s6-overlay 在容器启动时自动安装
    if [[ -n "$skills" ]]; then
        log_info "预装技能已写入配置，将在容器启动时自动安装: $skills"
    fi

    echo "{\"instance_id\":\"$instance_id\",\"status\":\"created\",\"gateway_token\":\"$gateway_token\"}"
}

# ==================== K3s 创建实例 ====================
cmd_create_k3s() {
    local instance_id="$1"
    local memory_mb="${2:-768}"
    local cpu_limit="${3:-0.75}"
    local effective_provider="$4"
    local base_url="${5:-}"
    local llm_api_key="${6:-}"
    local model="${7:-}"
    local skills="${8:-}"

    log_info "创建 K3s 实例: $instance_id"

    # 生成 Token
    local gateway_token=$(generate_token)
    local api_server_key=$(generate_token)
    local created_at=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

    # 映射 provider 到 API key 环境变量名
    local provider_key_name=""
    case "$effective_provider" in
        openai)           provider_key_name="OPENAI_API_KEY" ;;
        anthropic)        provider_key_name="ANTHROPIC_API_KEY" ;;
        deepseek)         provider_key_name="DEEPSEEK_API_KEY" ;;
        openrouter)       provider_key_name="OPENROUTER_API_KEY" ;;
        alibaba|alibaba-coding-plan) provider_key_name="DASHSCOPE_API_KEY" ;;
        gemini)           provider_key_name="GOOGLE_API_KEY" ;;
        zai)              provider_key_name="GLM_API_KEY" ;;
        kimi-coding)      provider_key_name="KIMI_API_KEY" ;;
        minimax)          provider_key_name="MINIMAX_API_KEY" ;;
        minimax-cn)       provider_key_name="MINIMAX_CN_API_KEY" ;;
        novita)           provider_key_name="NOVITA_API_KEY" ;;
        arcee)            provider_key_name="ARCEEAI_API_KEY" ;;
        gmi)              provider_key_name="GMI_API_KEY" ;;
        xiaomi)           provider_key_name="XIAOMI_API_KEY" ;;
        stepfun)          provider_key_name="STEPFUN_API_KEY" ;;
        huggingface)      provider_key_name="HF_TOKEN" ;;
        nvidia)           provider_key_name="NVIDIA_API_KEY" ;;
        xai)              provider_key_name="XAI_API_KEY" ;;
        tencent-tokenhub) provider_key_name="TOKENHUB_API_KEY" ;;
        *)                provider_key_name="OPENAI_API_KEY" ;;
    esac

    local effective_model="${model:-gpt-4o}"

    # 分配 NodePort
    local gateway_node_port
    gateway_node_port=$(allocate_instance_nodeport)
    local dashboard_node_port
    dashboard_node_port=$(allocate_instance_dashboard_nodeport)
    log_info "分配 NodePort: Gateway=${gateway_node_port}, Dashboard=${dashboard_node_port}"

    # 计算 K8s 资源限制
    local memory_limit="${memory_mb}Mi"
    local memory_request="$(_calc_int "$memory_mb * 0.8")Mi"
    local cpu_limit_str="${cpu_limit}"
    local cpu_request="0.5"

    # 渲染并应用 K8s manifests
    k8s_render_and_apply_manifests "$instance_id" "$effective_provider" "$effective_model" \
        "$base_url" "$provider_key_name" "$llm_api_key" "$api_server_key" \
        "$memory_limit" "$memory_request" "$cpu_limit_str" "$cpu_request" \
        "$gateway_node_port" "$dashboard_node_port" "$skills"

    # 注册实例 (K3s 模式写入 ConfigMap)
    local container_name="hermes-gateway-${instance_id}"
    register_instance "$instance_id" "$created_at" "$memory_mb" "$cpu_limit" \
        "$gateway_token" "$container_name" "created" "$gateway_node_port" "$dashboard_node_port"

    log_info "K3s 实例创建完成: $instance_id"
    log_info "  Gateway Token: ${gateway_token:0:16}..."
    log_info "  Gateway NodePort: ${gateway_node_port}"
    log_info "  Dashboard NodePort: ${dashboard_node_port}"

    echo "{\"instance_id\":\"$instance_id\",\"status\":\"created\",\"gateway_token\":\"$gateway_token\",\"gateway_port\":$gateway_node_port,\"dashboard_port\":$dashboard_node_port}"
}

# ==================== 启动实例 ====================
cmd_start() {
    local instance_id="$1"

    validate_instance_id "$instance_id" || return 1

    if ! instance_exists "$instance_id"; then
        log_error "实例不存在: $instance_id"
        return 1
    fi

    local status=$(get_instance_status "$instance_id")
    if [[ "$status" == "running" ]]; then
        log_warn "实例已在运行: $instance_id"
        return 0
    fi

    log_info "启动实例: $instance_id"

    if is_k3s; then
        # K3s: scale deployment to 1 replica
        scale_instance "$instance_id" 1
        if k8s_wait_for_pod_ready "$instance_id" 180; then
            update_instance_status "$instance_id" "running"
            log_info "实例启动成功: $instance_id"
            echo "{\"instance_id\":\"$instance_id\",\"status\":\"running\"}"
        else
            log_error "实例启动失败: Pod 未就绪"
            update_instance_status "$instance_id" "error"
            return 1
        fi
        return 0
    fi

    # Docker Compose 启动逻辑
    docker compose -f "$compose_file" -p "hermes-${instance_id}" up -d

    # 等待健康检查
    local container_name="hermes-gateway-${instance_id}"
    if wait_for_healthy "$container_name" 24 5; then
        # 更新状态
        update_instance_status "$instance_id" "running"

        # 配置模型（如果提供了环境变量）
        configure_instance_model "$instance_id"

        log_info "实例启动成功: $instance_id"
        echo "{\"instance_id\":\"$instance_id\",\"status\":\"running\"}"
    else
        log_error "实例启动失败: 健康检查超时"
        update_instance_status "$instance_id" "error"
        return 1
    fi
}

# ==================== 停止实例 ====================
cmd_stop() {
    local instance_id="$1"

    validate_instance_id "$instance_id" || return 1

    if ! instance_exists "$instance_id"; then
        log_error "实例不存在: $instance_id"
        return 1
    fi

    local status=$(get_instance_status "$instance_id")
    if [[ "$status" != "running" ]]; then
        log_warn "实例未在运行: $instance_id"
        return 0
    fi

    log_info "停止实例: $instance_id"

    if is_k3s; then
        # K3s: scale deployment to 0 replicas
        scale_instance "$instance_id" 0
        update_instance_status "$instance_id" "stopped"
        log_info "实例已停止: $instance_id"
        echo "{\"instance_id\":\"$instance_id\",\"status\":\"stopped\"}"
        return 0
    fi

    local instance_dir="${INSTANCES_DIR}/${instance_id}"
    local compose_file="${instance_dir}/docker-compose.yml"

    # Docker 停止容器
    docker compose -f "$compose_file" -p "hermes-${instance_id}" down

    # 更新状态
    update_instance_status "$instance_id" "stopped"

    log_info "实例已停止: $instance_id"
    echo "{\"instance_id\":\"$instance_id\",\"status\":\"stopped\"}"
}

# ==================== 重启实例 ====================
cmd_restart() {
    local instance_id="$1"

    validate_instance_id "$instance_id" || return 1

    if ! instance_exists "$instance_id"; then
        log_error "实例不存在: $instance_id"
        return 1
    fi

    local status=$(get_instance_status "$instance_id")
    if [[ "$status" == "running" ]]; then
        if is_k3s; then
            # K3s: rollout restart (no need to stop first)
            restart_instance_container "$instance_id"
            update_instance_status "$instance_id" "running"
            echo "{\"instance_id\":\"$instance_id\",\"status\":\"running\"}"
            return 0
        fi
        cmd_stop "$instance_id" > /dev/null
    fi

    cmd_start "$instance_id"
}

# ==================== 删除实例 ====================
cmd_delete() {
    local instance_id="$1"
    local purge_data="${2:-false}"

    validate_instance_id "$instance_id" || return 1

    if ! instance_exists "$instance_id"; then
        log_error "实例不存在: $instance_id"
        return 1
    fi

    log_info "删除实例: $instance_id (清理数据: $purge_data)"

    if is_k3s; then
        # K3s: delete all K8s resources labeled with this instance ID
        k8s_delete_instance_manifests "$instance_id"
        # 从注册表删除
        unregister_instance "$instance_id"
        log_info "K3s 实例已删除: $instance_id"
        echo "{\"instance_id\":\"$instance_id\",\"status\":\"deleted\"}"
        return 0
    fi

    # Docker Compose 删除逻辑
    local instance_dir="${INSTANCES_DIR}/${instance_id}"
    local compose_file="${instance_dir}/docker-compose.yml"

    # 停止并移除容器
    if [[ -f "$compose_file" ]]; then
        docker compose -f "$compose_file" -p "hermes-${instance_id}" down 2>/dev/null || true
    fi

    # 清理残留容器
    local container_name="hermes-gateway-${instance_id}"
    if docker ps -a --format '{{.Names}}' | grep -q "^${container_name}$"; then
        log_warn "清理残留容器: ${container_name}"
        docker rm -f "$container_name" 2>/dev/null || true
    fi

    # 清理残留网络
    local net_name="hermes-${instance_id}-net"
    if docker network ls --format '{{.Name}}' | grep -q "^${net_name}$"; then
        docker network rm "$net_name" 2>/dev/null || true
    fi

    # 清理数据（可选）
    if [[ "$purge_data" == "true" ]]; then
        rm -rf "$instance_dir"
        log_info "已清理实例数据: $instance_dir"
    fi

    # 从注册表删除
    unregister_instance "$instance_id"

    log_info "实例已删除: $instance_id"
    echo "{\"instance_id\":\"$instance_id\",\"status\":\"deleted\"}"
}

# ==================== 列出所有实例 ====================
cmd_list() {
    local format="${1:-table}"

    local registry=$(read_registry)
    local count=$(echo "$registry" | jq '.instances | length')

    if [[ "$format" == "json" ]]; then
        echo "$registry"
        return
    fi

    if [[ $count -eq 0 ]]; then
        log_info "暂无实例"
        return
    fi

    echo "========================================"
    echo "  Hermes Agent 实例列表 ($(detect_runtime))"
    echo "========================================"
    echo ""
    printf "%-20s %-10s %-10s %-10s %-12s %-15s\n" "实例ID" "状态" "内存" "CPU" "运行环境" "创建时间"
    printf "%-20s %-10s %-10s %-10s %-12s %-15s\n" "--------" "------" "------" "------" "--------" "----------"

    for instance_id in $(get_all_instance_ids); do
        local status=$(echo "$registry" | jq -r ".instances.\"$instance_id\".status // \"unknown\"")
        local mem=$(echo "$registry" | jq -r ".instances.\"$instance_id\".memory_limit_mb // 0")
        local cpu=$(echo "$registry" | jq -r ".instances.\"$instance_id\".cpu_limit // 0")
        local rt=$(echo "$registry" | jq -r ".instances.\"$instance_id\".runtime // \"unknown\"")
        local created=$(echo "$registry" | jq -r ".instances.\"$instance_id\".created_at // \"\"")
        printf "%-20s %-10s %-10s %-10s %-12s %-15s\n" "$instance_id" "$status" "${mem}MB" "${cpu}核" "$rt" "$created"
    done
    echo ""
}

# ==================== 查看实例详情 ====================
cmd_status() {
    local instance_id="$1"

    validate_instance_id "$instance_id" || return 1

    if ! instance_exists "$instance_id"; then
        log_error "实例不存在: $instance_id"
        return 1
    fi

    local registry=$(read_registry)
    local instance_info=$(echo "$registry" | jq ".instances.\"$instance_id\"")

    echo "========================================"
    echo "  实例详情: $instance_id ($(detect_runtime))"
    echo "========================================"
    echo ""
    echo "$instance_info" | jq -r '
        "状态: \(.status)",
        "创建时间: \(.created_at)",
        "内存限制: \(.memory_limit_mb)MB",
        "CPU限制: \(.cpu_limit)核",
        "容器名称: \(.container_name)",
        "运行环境: \(.runtime // "unknown")",
        "Gateway 端口: \(.gateway_port)",
        "Dashboard 端口: \(.dashboard_port)",
        "Gateway Token: \(.gateway_token)"
    '
    echo ""

    # 显示实际资源使用（如果运行中）
    local status=$(echo "$instance_info" | jq -r '.status')
    if [[ "$status" == "running" ]]; then
        print_instance_actual_usage "$instance_id"
        if is_k3s; then
            echo ""
            echo "K3s Pod:"
            local kubectl pod
            kubectl="$(_common_get_kubectl)" || true
            pod=$(k8s_get_instance_pod_name "$instance_id") || true
            if [[ -n "$kubectl" && -n "$pod" ]]; then
                $kubectl -n hermes get pod "$pod" -o wide 2>/dev/null || echo "  (pod info unavailable)"
            fi
        fi
    fi
}

# ==================== 执行命令 ====================
cmd_exec() {
    local instance_id="$1"
    shift
    local cmd="$@"

    validate_instance_id "$instance_id" || return 1

    if ! instance_exists "$instance_id"; then
        log_error "实例不存在: $instance_id"
        return 1
    fi

    local status=$(get_instance_status "$instance_id")
    if [[ "$status" != "running" ]]; then
        log_error "实例未在运行: $instance_id"
        return 1
    fi

    if is_k3s; then
        container_exec_for_instance_it "$instance_id" "$cmd"
        return $?
    fi

    local container_name="hermes-gateway-${instance_id}"
    docker exec -it "$container_name" bash -c "$cmd"
}

# ==================== 帮助信息 ====================
cmd_help() {
    echo "Hermes Agent 多实例管理器 (支持 Docker Compose 和 K3s/Kubernetes)"
    echo ""
    echo "用法: bash scripts/instance.sh <command> [args]"
    echo ""
    echo "运行环境自动检测: 脚本会自动检测 Docker 或 K3s 环境并使用相应的命令"
    echo "  Docker: docker compose up/down, docker exec"
    echo "  K3s:    kubectl apply/delete, kubectl exec"
    echo ""
    echo "命令:"
    echo "  create <id> [mem] [cpu] [provider] [url] [key] [model] [skills]  创建实例"
    echo "  start <id>                     启动实例"
    echo "  stop <id>                      停止实例"
    echo "  restart <id>                   重启实例"
    echo "  delete <id> [--purge]          删除实例 (可选清理数据)"
    echo "  list [table|json]              列出所有实例"
    echo "  status <id>                    查看实例详情"
    echo "  exec <id> <cmd>                在实例中执行命令"
    echo "  resources                      查看资源使用情况"
    echo "  help                           显示此帮助信息"
    echo ""
    echo "LLM Provider:"
    echo "  openai       - OpenAI GPT 系列"
    echo "  anthropic    - Anthropic Claude 系列"
    echo "  deepseek     - DeepSeek 系列"
    echo "  openrouter   - OpenRouter 聚合"
    echo "  custom       - 自定义 OpenAI 兼容 API"
    echo "  <其他名称>   - 自动映射为 custom (如 alibaba, zhipu 等)"
    echo ""
    echo "示例:"
    echo "  # 创建实例 (默认 768MB, 0.75 CPU, OpenAI)"
    echo "  bash scripts/instance.sh create user-alice"
    echo ""
    echo "  # 创建实例 (Anthropic)"
    echo "  bash scripts/instance.sh create user-bob 512 0.5 anthropic \"\" sk-ant-xxx"
    echo ""
    echo "  # 创建实例 (百炼（DashScope） — 自动映射为 custom)"
    echo "  bash scripts/instance.sh create user-tzy 2048 2 alibaba https://dashscope.aliyuncs.com/compatible-mode/v1 sk-xxx qwen3.7-max"
    echo ""
    echo "  # 创建实例 (自定义端点 + 预装技能)"
    echo "  bash scripts/instance.sh create user-charlie 768 0.75 custom http://api.example.com/v1 token-123 gpt-4o web-search,code-review"
    echo ""
    echo "  # 启动/停止/重启实例"
    echo "  bash scripts/instance.sh start user-alice"
    echo "  bash scripts/instance.sh stop user-alice"
    echo "  bash scripts/instance.sh restart user-alice"
    echo ""
    echo "  # 查看实例列表"
    echo "  bash scripts/instance.sh list"
    echo ""
    echo "  # 在实例中执行 Hermes CLI 命令"
    echo "  bash scripts/instance.sh exec user-alice \"hermes skills list\""
    echo ""
    echo "  # 查看资源使用情况"
    echo "  bash scripts/instance.sh resources"
}

# ==================== 资源报告 ====================
cmd_resources() {
    print_resource_summary
}

# ==================== 主函数 ====================
main() {
    local command="${1:-help}"
    shift || true

    case "$command" in
        create)
            cmd_create "$@"
            ;;
        start)
            cmd_start "$@"
            ;;
        stop)
            cmd_stop "$@"
            ;;
        restart)
            cmd_restart "$@"
            ;;
        delete)
            cmd_delete "$@"
            ;;
        list)
            cmd_list "$@"
            ;;
        status)
            cmd_status "$@"
            ;;
        exec)
            cmd_exec "$@"
            ;;
        resources)
            cmd_resources
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
