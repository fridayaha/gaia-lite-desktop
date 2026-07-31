#!/bin/bash
set -e

# Hermes Agent 云服务器部署 - 环境安装脚本
# 用途: 安装 Docker 和 Docker Compose
# 适用系统: Ubuntu 20.04/22.04/24.04, CentOS 7/8, Debian 10/11/12

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

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

# 检测操作系统
detect_os() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        OS=$ID
        VERSION=$VERSION_ID
    else
        log_error "无法检测操作系统类型"
        exit 1
    fi
}

# 安装 Docker (Ubuntu/Debian)
install_docker_debian() {
    log_info "正在为 $OS $VERSION 安装 Docker..."

    # 卸载旧版本
    sudo apt-get remove -y docker docker-engine docker.io containerd runc 2>/dev/null || true

    # 安装依赖
    sudo apt-get update
    sudo apt-get install -y \
        ca-certificates \
        curl \
        gnupg \
        lsb-release

    # 添加 Docker GPG key
    sudo mkdir -p /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/$OS/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg

    # 添加 Docker 仓库
    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/$OS \
      $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

    # 安装 Docker Engine
    sudo apt-get update
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

    log_info "Docker 安装完成"
}

# 安装 Docker (CentOS/RHEL)
install_docker_rhel() {
    log_info "正在为 $OS $VERSION 安装 Docker..."

    # 卸载旧版本
    sudo yum remove -y docker docker-client docker-client-latest docker-common \
        docker-latest docker-latest-logrotate docker-logrotate docker-engine 2>/dev/null || true

    # 安装依赖
    sudo yum install -y yum-utils

    # 添加 Docker 仓库
    sudo yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo

    # 安装 Docker Engine
    sudo yum install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

    log_info "Docker 安装完成"
}

# 启动 Docker 服务
start_docker() {
    log_info "启动 Docker 服务..."
    sudo systemctl start docker
    sudo systemctl enable docker

    # 将当前用户加入 docker 组
    if ! groups | grep -q docker; then
        log_info "将用户 $USER 加入 docker 组..."
        sudo usermod -aG docker $USER
        log_warn "请注销并重新登录以使 docker 组权限生效"
    fi
}

# 验证安装
verify_installation() {
    log_info "验证安装..."

    if command -v docker &> /dev/null; then
        DOCKER_VERSION=$(docker --version | awk '{print $3}' | sed 's/,//')
        log_info "Docker 版本: $DOCKER_VERSION"
    else
        log_error "Docker 未找到"
        exit 1
    fi

    if docker compose version &> /dev/null; then
        COMPOSE_VERSION=$(docker compose version | awk '{print $4}')
        log_info "Docker Compose 版本: $COMPOSE_VERSION"
    else
        log_error "Docker Compose 未找到"
        exit 1
    fi

    log_info "安装验证通过 ✓"
}

# 配置 .env 文件
setup_env() {
    if [ ! -f "$PROJECT_DIR/.env" ]; then
        log_info "从模板创建 .env 文件..."
        cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
        log_warn "请编辑 $PROJECT_DIR/.env 并填写必要的 API Key"
    else
        log_info ".env 文件已存在，跳过创建"
    fi
}

# 主函数
main() {
    log_info "开始安装 Hermes Agent 部署环境..."

    # 检查 root 权限
    if [ "$EUID" -eq 0 ]; then
        log_error "请勿使用 root 用户运行此脚本"
        exit 1
    fi

    # 检测操作系统
    detect_os
    log_info "检测到操作系统: $OS $VERSION"

    # 根据操作系统类型安装 Docker
    case "$OS" in
        ubuntu|debian)
            install_docker_debian
            ;;
        centos|rhel|rocky|almalinux)
            install_docker_rhel
            ;;
        *)
            log_error "不支持的操作系统: $OS"
            log_info "支持的操作系统: Ubuntu, Debian, CentOS, RHEL, Rocky, AlmaLinux"
            exit 1
            ;;
    esac

    # 启动 Docker
    start_docker

    # 验证安装
    verify_installation

    # 配置环境变量
    setup_env

    log_info "安装完成！"
    log_info "下一步: 运行 bash scripts/deploy.sh start 启动服务"
}

main "$@"
