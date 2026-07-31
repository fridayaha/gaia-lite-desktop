#!/bin/bash
# [DEPRECATED] 此脚本不再使用
# 官方镜像 nousresearch/hermes-agent 内置 s6-overlay 处理启动初始化
# (模型配置、技能预装等通过环境变量自动完成)
# 保留此文件作为参考，不再挂载到容器中
#
# 以下功能已由官方镜像的 /etc/cont-init.d/ 自动处理：
#   - hermes config set provider/model
#   - hermes skills install (通过 HERMES_PRELOAD_SKILLS)
#   - exec hermes gateway run
#
# 如需自定义初始化逻辑，可通过 Docker Compose 的 entrypoint 覆盖

exit 0

# ==================== 以下为原始代码 (保留作参考) ====================

set -e

# 颜色输出
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INIT]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[INIT]${NC} $1"
}

# ==================== 配置模型提供商 ====================
configure_model() {
    log_info "配置模型提供商: ${HERMES_PROVIDER:-openai}"

    # 如果有自定义 Base URL，优先使用
    if [ -n "$HERMES_BASE_URL" ]; then
        log_info "使用自定义端点: $HERMES_BASE_URL"

        # 确定使用哪个 API Key
        local api_key="${HERMES_CUSTOM_API_KEY:-$OPENAI_API_KEY}"

        if [ -n "$api_key" ]; then
            hermes config set provider custom
            hermes config set api_base "$HERMES_BASE_URL"
            hermes config set api_mode "${HERMES_API_MODE:-chat_completions}"
            hermes config set model "${HERMES_MODEL:-custom-model}"
            log_info "模型配置完成: custom @ $HERMES_BASE_URL"
        else
            log_warn "未提供自定义 API Key，将回退到标准提供商"
        fi
    else
        # 使用标准提供商
        local provider="${HERMES_PROVIDER:-openai}"
        local model="${HERMES_MODEL:-gpt-4o}"

        hermes config set provider "$provider"
        hermes config set model "$model"

        log_info "模型配置完成: $provider / $model"
    fi
}

# ==================== 预装技能 ====================
preload_skills() {
    if [ -z "$HERMES_PRELOAD_SKILLS" ]; then
        log_info "未指定预装技能，跳过"
        return
    fi

    log_info "预装技能: $HERMES_PRELOAD_SKILLS"

    # 将逗号分隔的列表转为数组
    IFS=',' read -ra SKILLS <<< "$HERMES_PRELOAD_SKILLS"

    for skill in "${SKILLS[@]}"; do
        # 去除空格
        skill=$(echo "$skill" | xargs)

        if [ -n "$skill" ]; then
            log_info "安装技能: $skill"
            hermes skills install "$skill" || log_warn "技能安装失败: $skill"
        fi
    done

    log_info "技能预装完成"
}

# ==================== 验证配置 ====================
verify_config() {
    log_info "验证配置..."

    # 检查是否至少有一个 API Key
    if [ -z "$OPENAI_API_KEY" ] && [ -z "$ANTHROPIC_API_KEY" ] && [ -z "$DEEPSEEK_API_KEY" ] && [ -z "$OPENROUTER_API_KEY" ] && [ -z "$HERMES_CUSTOM_API_KEY" ]; then
        log_warn "警告: 未配置任何 API Key，Agent 可能无法正常工作"
    fi

    # 显示当前配置
    hermes config get provider || true
    hermes config get model || true
}

# ==================== 主流程 ====================
main() {
    log_info "Hermes Agent 容器初始化开始..."

    # 1. 配置模型
    configure_model

    # 2. 预装技能
    preload_skills

    # 3. 验证配置
    verify_config

    log_info "初始化完成，启动 Gateway..."

    # 4. 启动 Gateway (替换当前进程)
    exec hermes gateway run
}

main "$@"
