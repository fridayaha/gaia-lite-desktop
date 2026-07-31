# Hermes Agent 云服务器部署指南

一键部署 Hermes Agent 到云服务器，支持 Docker Compose 和 K3s/Kubernetes 两种部署方式。包含 Gateway 对话引擎、Dashboard Web 管理界面，支持单实例、多实例和多 Profile 三种运行模式。

本项目作为**智能体多租管理平台**的基础设施层，采用"租户（Namespace）→ 实例（Pod）→ 用户 Agent（Profile）"的三级架构。详见 [多租管理平台架构分析报告](docs/multi-tenant-architecture-analysis.md)。

---

## 目录

- [1. 项目概述](#1-项目概述)
  - [1.1 Hermes Agent 简介](#11-hermes-agent-简介)
  - [1.2 部署方式对比](#12-部署方式对比)
- [2. 项目文件结构](#2-项目文件结构)
  - [2.1 目录总览](#21-目录总览)
  - [2.2 文件引用关系](#22-文件引用关系)
  - [2.3 配置文件说明](#23-配置文件说明)
- [3. Docker Compose 部署](#3-docker-compose-部署)
  - [3.1 前置条件](#31-前置条件)
  - [3.2 安装](#32-安装)
  - [3.3 验证](#33-验证)
  - [3.4 变更](#34-变更)
  - [3.5 升级](#35-升级)
  - [3.6 卸载](#36-卸载)
  - [3.7 数据管理](#37-数据管理)
- [4. K3s/Kubernetes 部署](#4-k3skubernetes-部署)
  - [4.1 前置条件](#41-前置条件)
  - [4.2 在线安装](#42-在线安装)
  - [4.3 离线安装](#43-离线安装)
  - [4.4 验证](#44-验证)
  - [4.5 变更](#45-变更)
  - [4.6 升级](#46-升级)
  - [4.7 卸载](#47-卸载)
  - [4.8 数据管理](#48-数据管理)
- [5. 安装后使用指南](#5-安装后使用指南)
  - [5.1 交互方式总览](#51-交互方式总览)
  - [5.2 消息平台接入](#52-消息平台接入)
  - [5.3 CLI 常用命令](#53-cli-常用命令)
  - [5.4 HTTP API](#54-http-api)
- [6. 多实例管理（Docker Compose）](#6-多实例管理docker-compose)
  - [6.1 架构概述](#61-架构概述)
  - [6.2 资源规划](#62-资源规划)
  - [6.3 实例生命周期管理](#63-实例生命周期管理)
- [7. 多 Profile 管理（通用）](#7-多-profile-管理通用)
  - [7.1 架构概述](#71-架构概述)
  - [7.2 Profile 生命周期管理](#72-profile-生命周期管理)
  - [7.3 消息平台集成](#73-消息平台集成)
- [8. 日常运维](#8-日常运维)
  - [8.1 健康检查](#81-健康检查)
  - [8.2 日志查看](#82-日志查看)
  - [8.3 资源监控](#83-资源监控)
- [9. Profile 权限隔离](#9-profile-权限隔离)
  - [9.1 问题背景](#91-问题背景)
  - [9.2 多 UID 隔离方案](#92-多-uid-隔离方案)
  - [9.3 权限模型](#93-权限模型)
  - [9.4 改造文件清单](#94-改造文件清单)
  - [9.5 向后兼容](#95-向后兼容)
  - [9.6 验证方法](#96-验证方法)
- [附录 A：脚本详细解析](#附录-a脚本详细解析)
- [附录 B：常见问题排查](#附录-b常见问题排查)
- [附录 C：环境变量参考](#附录-c环境变量参考)
- [附录 D：安全建议](#附录-d安全建议)

---

## 1. 项目概述

### 1.1 Hermes Agent 简介

Hermes Agent 是由 [Nous Research](https://nousresearch.com) 开源的自主 AI Agent 框架（[GitHub](https://github.com/NousResearch/hermes-agent)，MIT 许可证）。其核心定位是**可自我进化的智能代理引擎**——通过内置的学习闭环（Learning Loop）持续从交互经验中生成技能、优化行为、积累知识。

**核心能力：**

| 能力 | 说明 |
|------|------|
| **自学习闭环** | 自动从交互轨迹中生成 Skill（兼容 agentskills.io 开放标准），在使用中持续迭代优化 |
| **多模型适配** | 支持 18+ LLM 提供商（OpenAI、Anthropic、DeepSeek、百炼（DashScope）、OpenRouter 200+ 模型等） |
| **消息网关** | 20 个平台适配器（Telegram、Discord、Slack、飞书、钉钉、企业微信、WhatsApp 等） |
| **跨会话记忆** | SQLite + FTS5 全文搜索，支持会话谱系追踪和用户偏好建模 |
| **工具生态** | 70+ 内置工具，覆盖 28 个工具集，6 种终端后端（Local、Docker、SSH、Singularity、Modal、Daytona） |
| **Dashboard** | Web 管理界面，用于技能管理、模型切换、会话历史查看 |

**适用场景：**

- 企业内部智能化助手（为不同部门提供定制化 Agent 实例）
- 多租户 Agent 平台（每个租户独立实例、技能库和会话数据）
- 研发效能平台（代码生成、审查、测试自动化的 Agent 编排）

### 1.2 部署方式对比

本项目基于官方 Docker 镜像 `nousresearch/hermes-agent:latest`，提供两种部署方式：

**单容器架构（两种方式共用）：**

```
┌─────────────────────────────────────┐
│  Docker Container / K8s Pod         │
│                                     │
│  ┌──────────┐   ┌───────────────┐   │
│  │ Gateway   │   │ Dashboard     │   │
│  │ :8642     │   │ :9119         │   │
│  │           │   │ (HERMES_      │   │
│  │           │   │  DASHBOARD=1) │   │
│  └──────────┘   └───────────────┘   │
│                                     │
│  └── profile-supervisor (s6)        │
│      ├── profile-alice :8643        │
│      ├── profile-bob   :8644        │
│      └── ...          :8645-8650    │
│                                     │
│  s6-overlay (进程管理)              │
└─────────────────────────────────────┘
         │
         ▼
┌──────────────────┐
│  /opt/data       │  ← 持久化存储
└──────────────────┘
```

**两种部署方式对比：**

| 维度 | Docker Compose | K3s/Kubernetes |
|------|---------------|----------------|
| 部署文件 | `docker-compose.yml` + `.env` | `k8s/*.yaml`（Kustomize） |
| 部署脚本 | `deploy.sh` | `k3s-deploy.sh` |
| 镜像来源 | `docker compose` 自动构建 | 需手动 `docker build` + `k3s ctr import` |
| 数据存储 | Bind mount（`./data/`） | PVC（`hermes-data`，10Gi） |
| 端口暴露 | Host 端口映射（`8642:8642`） | NodePort（`8642 → 30642`） |
| 多实例管理 | `instance.sh`（独立容器） | `profile.sh`（同容器多进程） |
| 离线部署 | 需 `docker save` + `docker load` | `offline-pack.sh` 一键打包 |
| 适用场景 | 单机快速验证、开发环境 | 生产部署、多节点、资源隔离 |

---

## 2. 项目文件结构

### 2.1 目录总览

```
hermes-deploy/
├── docker-compose.yml                    # Docker Compose 编排文件 [Docker Compose]
├── Dockerfile.profile                    # 自定义镜像（两种方式共用）[共用]
├── .env.example                          # 环境变量模板 [Docker Compose]
├── .gitattributes                        # Git 换行符规则（强制 LF，防止 CRLF）
├── .gitignore                            # Git 忽略规则
├── README.md                             # 本文档
│
├── configs/                              # 配置文件目录 [共用]
│   ├── hermes-config.yaml                #   Hermes 主配置（终端、记忆、工具等）
│   ├── SOUL.md                           #   Agent 人设定义
│   ├── gateway.json                      #   消息网关配置（平台适配器）
│   └── templates/                        #   模板目录
│       ├── docker-compose.instance.yml.template  # 多实例 Compose 模板 [Docker Compose]
│       ├── profile-config.yaml.template          # Profile 配置模板 [共用]
│       ├── profile-env.template                  # Profile 环境变量模板 [共用]
│       ├── profile-gateway.json.template         # Profile 网关配置模板 [共用]
│       └── profile-SOUL.md.template              # Profile 人设模板 [共用]
│
├── s6/                                   # s6-overlay 服务定义 [共用]
│   └── gateway-profiles/                 #   Profile Gateway 监督服务
│       ├── type                          #     服务类型 (longrun)
│       ├── run                           #     启动脚本
│       └── finish                        #     退出处理
│
├── k8s/                                  # Kubernetes 部署清单 [K3s]
│   ├── kustomization.yaml                #   Kustomize 入口
│   ├── namespace.yaml                    #   Namespace: hermes
│   ├── deployment.yaml                   #   Deployment（1 Pod）
│   ├── service.yaml                      #   Service（NodePort）
│   ├── pvc.yaml                          #   PVC（10Gi ReadWriteOnce）
│   ├── configmap.yaml                    #   ConfigMap（环境变量）
│   ├── configmap-files.yaml              #   ConfigMap（SOUL.md、gateway.json）
│   └── secret.yaml                       #   Secret（API 密钥）
│
├── scripts/                              # 运维脚本目录
│   ├── install.sh                        #   安装 Docker 和 Docker Compose [Docker Compose]
│   ├── deploy.sh                         #   单实例部署管理 [Docker Compose]
│   ├── backup.sh                         #   数据备份与恢复 [Docker Compose]
│   ├── health-check.sh                   #   健康检查 [Docker Compose]
│   ├── container-init.sh                 #   [已废弃] 容器初始化脚本
│   ├── instance.sh                       #   多实例管理器 [Docker Compose]
│   ├── profile.sh                        #   多 Profile 管理器 [共用，支持 Docker/K3s]
│   ├── profile-supervisor.sh             #   容器内 Profile 进程管理器 [共用，由 s6 启动]
│   ├── k3s-deploy.sh                     #   K3s 部署管理 [K3s]
│   ├── offline-pack.sh                   #   离线打包（有网机器）[K3s]
│   ├── offline-load.sh                   #   镜像导入（无网机器）[K3s]
│   └── lib/                              #   共享函数库
│       ├── common.sh                     #     日志、校验、注册表读写 [共用]
│       ├── resource.sh                   #     资源预算检查 [Docker Compose]
│       └── profile-common.sh             #     Profile 注册表、端口分配、文件 I/O [共用]
│
├── data/                                 # 运行时数据（不提交到 Git）
│   ├── profiles/                         #   多 Profile 数据目录
│   │   ├── registry.json                 #     Profile 注册表
│   │   ├── commands.json                 #     主机→容器指令队列
│   │   ├── pid_map.json                  #     进程 ID 映射
│   │   └── <profile-name>/              #     每个 Profile 独立 HERMES_HOME
│   ├── instances/                        #   多实例数据目录 [Docker Compose]
│   │   └── <instance-id>/               #     每个实例独立目录
│   └── registry/                         #   实例注册表 [Docker Compose]
│
└── docs/                                 # 补充文档
    └── k3s-deployment-guide.md           #   K3s 部署指南（已合并到本文档）
```

> 文件末尾的标签说明该文件的用途归属：
> - `[Docker Compose]` — 仅 Docker Compose 部署使用
> - `[K3s]` — 仅 K3s/Kubernetes 部署使用
> - `[共用]` — 两种部署方式都会使用

### 2.2 文件引用关系

**Docker Compose 部署用到的文件：**

| 文件 | 引用方式 | 说明 |
|------|---------|------|
| `docker-compose.yml` | 入口文件 | `docker compose up -d` 的编排定义 |
| `Dockerfile.profile` | 被 `docker-compose.yml` 的 `build` 引用 | 构建自定义镜像 |
| `s6/gateway-profiles/` | 被 `Dockerfile.profile` 的 `COPY` 指令复制到镜像内 | s6 服务定义 |
| `scripts/profile-supervisor.sh` | 被 `Dockerfile.profile` 的 `COPY` 指令复制到镜像内 | Profile 监督器 |
| `.env`（从 `.env.example` 复制） | 被 `docker-compose.yml` 的 `${...}` 变量引用 | 环境变量 |
| `configs/hermes-config.yaml` | 挂载到容器内或被 `instance.sh` 复制 | Hermes 主配置 |
| `configs/SOUL.md` | 挂载到容器内或被 `instance.sh` 复制 | Agent 人设 |
| `configs/gateway.json` | 挂载到容器内或被 `instance.sh` 复制 | 网关配置 |
| `configs/templates/docker-compose.instance.yml.template` | 被 `instance.sh` 渲染生成实例 Compose 文件 | 多实例模板 |
| `configs/templates/profile-*.template` | 被 `profile.sh` 渲染生成 Profile 配置 | Profile 模板 |
| `scripts/install.sh` | 独立执行 | 安装 Docker 环境 |
| `scripts/deploy.sh` | 独立执行，调用 `docker compose` | 服务生命周期管理 |
| `scripts/backup.sh` | 独立执行，操作 Docker Volume | 数据备份恢复 |
| `scripts/health-check.sh` | 独立执行，检查容器状态 | 健康检查 |
| `scripts/instance.sh` | 独立执行，引用 `lib/common.sh` 和 `lib/resource.sh` | 多实例管理 |
| `scripts/profile.sh` | 独立执行，引用 `lib/profile-common.sh` | Profile 管理 |
| `scripts/lib/common.sh` | 被 `instance.sh` 和 `resource.sh` 的 `source` 引用 | 公共函数 |
| `scripts/lib/resource.sh` | 被 `instance.sh` 的 `source` 引用 | 资源预算 |
| `scripts/lib/profile-common.sh` | 被 `profile.sh` 的 `source` 引用 | Profile 公共函数 |

**K3s/Kubernetes 部署用到的文件：**

| 文件 | 引用方式 | 说明 |
|------|---------|------|
| `k8s/kustomization.yaml` | 入口文件 | `kubectl apply -k k8s/` 的 Kustomize 入口 |
| `k8s/namespace.yaml` | 被 `kustomization.yaml` 的 `resources` 引用 | 命名空间定义 |
| `k8s/deployment.yaml` | 被 `kustomization.yaml` 引用 | Deployment 定义（引用 `hermes-profile:latest` 镜像） |
| `k8s/service.yaml` | 被 `kustomization.yaml` 引用 | NodePort Service |
| `k8s/pvc.yaml` | 被 `kustomization.yaml` 引用 | 持久化存储声明 |
| `k8s/configmap.yaml` | 被 `kustomization.yaml` 引用；被 `deployment.yaml` 的 `envFrom` 引用 | 环境变量 |
| `k8s/configmap-files.yaml` | 被 `kustomization.yaml` 引用；被 `deployment.yaml` 的 `volumeMounts` 引用 | 配置文件（SOUL.md、gateway.json） |
| `k8s/secret.yaml` | 被 `kustomization.yaml` 引用；被 `deployment.yaml` 的 `envFrom` 引用 | API 密钥 |
| `Dockerfile.profile` | `docker build -f Dockerfile.profile` 构建镜像 | 自定义镜像（与 Docker Compose 共用） |
| `s6/gateway-profiles/` | 被 `Dockerfile.profile` 复制 | s6 服务定义 |
| `scripts/profile-supervisor.sh` | 被 `Dockerfile.profile` 复制 | Profile 监督器 |
| `scripts/k3s-deploy.sh` | 独立执行 | K3s 部署管理 |
| `scripts/offline-pack.sh` | 独立执行 | 离线打包 |
| `scripts/offline-load.sh` | 被离线包中的 `install.sh` 调用 | 镜像导入 |
| `scripts/profile.sh` | 独立执行，引用 `lib/profile-common.sh` | Profile 管理 |
| `scripts/lib/profile-common.sh` | 被 `profile.sh` 的 `source` 引用 | Profile 公共函数 |
| `configs/templates/profile-*.template` | 被 `profile.sh` 渲染 | Profile 模板 |

### 2.3 配置文件说明

#### hermes-config.yaml（Hermes 主配置）

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `terminal.backend` | `local` | 执行环境：local / docker / ssh / singularity / modal / daytona |
| `terminal.timeout` | `180` | 命令超时时间（秒） |
| `memory.memory_enabled` | `true` | 启用长期记忆 |
| `memory.memory_file` | `MEMORY.md` | 记忆文件路径 |
| `session.db_path` | `state.db` | SQLite 数据库路径 |
| `session.retention_days` | `0` | 会话保留天数（0 = 永久） |
| `agent.max_turns` | `50` | 单次对话最大轮次 |
| `agent.default_model` | `gpt-4o` | 默认模型 |
| `tools.file_read_max_chars` | `100000` | 文件读取最大字符数 |
| `tools.browser_enabled` | `false` | 启用浏览器工具 |
| `security.redact_secrets` | `true` | 自动脱敏敏感信息 |
| `security.confirm_dangerous_ops` | `true` | 危险操作需要确认 |
| `approvals.mode` | `manual` | 审批模式：manual / auto |

#### SOUL.md（Agent 人设）

定义 Agent 的身份、行为准则和交互风格。包括：
- **身份**：Agent 的角色定位
- **核心原则**：准确性优先、安全第一、透明沟通、尊重隐私
- **行为准则**：工具使用、代码执行、信息处理的规范
- **交互风格**：语言风格、上下文提供、分步说明

#### gateway.json（消息网关配置）

配置消息平台适配器（Telegram、Discord、Slack 等）和 HTTP API：
- `platforms.telegram/discord/slack`：各平台的 Bot Token 和访问控制
- `platforms.api`：HTTP API 开关、认证、速率限制
- `session`：最大并发会话数、超时时间、清理间隔
- `logging`：日志级别、文件路径、大小限制

---

## 3. Docker Compose 部署

### 3.1 前置条件

**服务器最低配置：**

| 项目 | 最低要求 | 推荐配置 |
|------|---------|---------|
| CPU | 2 核 | 4 核 |
| 内存 | 2 GB | 4 GB |
| 磁盘 | 20 GB | 40 GB |
| 操作系统 | Ubuntu 20.04+ / CentOS 7+ / Debian 10+ | Ubuntu 22.04 LTS |
| 网络 | 公网 IP，开放端口 8642、9119 | — |

**软件要求：**

- Docker Engine 20.10+
- Docker Compose V2（`docker compose` 命令）
- curl、tar、jq

**网络要求：**

- 服务器可访问 Docker Hub（`registry-1.docker.io`）
- 服务器可访问 LLM 提供商 API（OpenAI、Anthropic、DeepSeek、百炼（DashScope）等）
- 如需消息平台集成，需访问对应平台 API

### 3.2 安装

#### 步骤 1：下载项目

```bash
# 方式 1: Git 克隆（推荐）
git clone <your-repo-url> hermes-deploy
cd hermes-deploy

# 方式 2: 直接解压到目标目录
cd /opt/hermes-deploy
```

#### 步骤 2：安装 Docker 和 Docker Compose

```bash
chmod +x scripts/*.sh
bash scripts/install.sh
```

脚本会自动检测操作系统（Ubuntu/Debian/CentOS/RHEL 等），安装 Docker CE 和 Docker Compose 插件，并将当前用户加入 `docker` 组。

**重要**：安装完成后需**注销并重新登录**以使 `docker` 组权限生效。

#### 步骤 3：配置 Docker 镜像加速（国内服务器）

如果从 Docker Hub 拉取镜像失败或过慢，配置镜像加速器：

```bash
sudo mkdir -p /etc/docker
sudo tee /etc/docker/daemon.json <<'EOF'
{
  "registry-mirrors": [
    "https://your-mirror.example.com",
    "https://your-mirror2.example.com"
  ]
}
EOF

sudo systemctl daemon-reload
sudo systemctl restart docker
```

如果加速器也不可用，手动导入镜像：

```bash
# 在可访问 Docker Hub 的机器上
docker pull nousresearch/hermes-agent:latest
docker save nousresearch/hermes-agent:latest | gzip > hermes-agent.tar.gz

# 传到目标服务器后加载
gunzip -c hermes-agent.tar.gz | docker load
```

#### 步骤 4：配置环境变量

```bash
cp .env.example .env
nano .env
```

**必须配置的变量**（至少配置一个 LLM 提供商的 API Key）：

```bash
# 选择提供商和模型
HERMES_PROVIDER=openai          # openai / anthropic / deepseek / openrouter / alibaba / custom
HERMES_MODEL=gpt-4o

# 配置对应 API Key
OPENAI_API_KEY=sk-your-openai-api-key
# 或 ANTHROPIC_API_KEY=sk-ant-xxx
# 或 DEEPSEEK_API_KEY=your-key
# 或 OPENROUTER_API_KEY=your-key
```

**自定义 LLM 端点**（百炼（DashScope）、自建服务等）：

```bash
HERMES_PROVIDER=alibaba
HERMES_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
HERMES_CUSTOM_API_KEY=sk-xxx
HERMES_MODEL=qwen3.7-max
```

**可选配置：**

```bash
# 端口配置
GATEWAY_PORT=8642
DASHBOARD_PORT=9119

# 备份配置
BACKUP_RETENTION_DAYS=7
BACKUP_DIR=./backups
```

#### 步骤 5：启动服务

```bash
# 初始化部署环境（拉取镜像、构建、创建目录）
bash scripts/deploy.sh init

# 启动服务
bash scripts/deploy.sh start
```

> 首次启动需要 60-120 秒初始化（同步技能、配置模型等），请耐心等待。

### 3.3 验证

```bash
# 查看服务状态
bash scripts/deploy.sh status

# 执行健康检查
bash scripts/health-check.sh
```

**预期输出：**

```
========== 服务状态 ==========
NAME                 STATUS
hermes-gateway       Up (healthy)

========== 资源使用 ==========
NAME                 CPU %    MEM USAGE / LIMIT    MEM %
hermes-gateway       2.50%    800MiB / 1536MiB      52.08%

========== 健康检查 ==========
[INFO] Gateway: 健康 ✓
```

**访问服务：**

- **Dashboard Web UI**: `http://<服务器IP>:9119`
- **Gateway API**: `http://<服务器IP>:8642`

### 3.4 变更

**修改 Agent 配置**（模型、行为等）：

```bash
# 编辑配置文件
nano configs/hermes-config.yaml

# 重启生效
bash scripts/deploy.sh restart
```

**修改端口**：

```bash
# 编辑 .env
nano .env
# 修改 GATEWAY_PORT 或 DASHBOARD_PORT

# 重启生效
bash scripts/deploy.sh restart
```

**修改资源限制**：

```bash
# 编辑 docker-compose.yml 中的 deploy.resources 部分
nano docker-compose.yml

# 重启生效
bash scripts/deploy.sh restart
```

**修改 Agent 人设**：

```bash
# 编辑人设文件
nano configs/SOUL.md

# 重启生效
bash scripts/deploy.sh restart
```

### 3.5 升级

```bash
# 一键更新（自动备份 + 拉取新镜像 + 重建容器）
bash scripts/deploy.sh update
```

此命令会自动执行：
1. 调用 `backup.sh` 备份当前数据
2. 拉取最新镜像（或重新构建 `Dockerfile.profile`）
3. 使用 `--force-recreate` 重建容器

### 3.6 卸载

```bash
# 停止并删除容器、网络、卷（⚠️ 会删除数据卷中的数据）
bash scripts/deploy.sh cleanup

# 仅停止容器（保留数据）
bash scripts/deploy.sh stop
```

> ⚠️ `cleanup` 命令会执行 `docker compose down --volumes --remove-orphans`，**永久删除** Docker Volume 中的数据。请先执行 `backup.sh` 备份。

### 3.7 数据管理

#### 存储位置

使用 **Docker 命名卷** 存储数据：

| 项目 | 路径 |
|------|------|
| 容器内路径 | `/opt/data` |
| 宿主机路径 | `/var/lib/docker/volumes/hermes-data/_data/` |
| Volume 名称 | `hermes-data` |

查看宿主机路径：

```bash
docker volume inspect hermes-data | grep Mountpoint
# 输出: "Mountpoint": "/var/lib/docker/volumes/hermes-data/_data"
```

#### 数据结构

```
/opt/data/
├── .env                            # API Key 和敏感配置
├── config.yaml                     # 模型、Agent 行为配置
├── SOUL.md                         # Agent 人设定义
├── gateway.json                    # 消息网关配置
├── state.db                        # SQLite 数据库（会话索引等）
├── sessions/                       # 会话历史记录
├── memories/                       # 长期记忆
├── skills/                         # 已安装的技能
├── hooks/                          # 事件钩子
├── cron/                           # 定时任务
├── logs/                           # 运行日志
└── profiles/                       # 多 Profile 数据（如有）
```

#### 持久化机制

以下操作**不会丢失数据**：
- ✅ 容器重启（`docker restart`）
- ✅ 容器停止/启动（`docker stop` / `docker start`）
- ✅ 容器删除后重建（`docker rm` + `docker compose up -d`）
- ✅ 镜像更新（`docker compose pull` + `docker compose up -d`）
- ✅ 服务器重启

以下操作会**永久删除数据**：
- ⚠️ `docker compose down -v`（删除 Volume）
- ⚠️ `docker volume rm hermes-data`

#### 备份与恢复

```bash
# 执行完整备份（数据卷 + 配置 + .env + compose 文件）
bash scripts/backup.sh

# 列出所有备份
bash scripts/backup.sh list

# 恢复指定备份（会停止服务 → 恢复数据 → 重启服务）
bash scripts/backup.sh restore hermes_backup_20240101_120000
```

建议配置 cron 定时备份：

```bash
# 每天凌晨 2 点自动备份
crontab -e
0 2 * * * cd /path/to/hermes-deploy && bash scripts/backup.sh >> /var/log/hermes-backup.log 2>&1
```

#### 数据迁移

```bash
# 源服务器：导出 Volume
docker run --rm -v hermes-data:/data -v $(pwd):/backup \
  alpine tar czf /backup/hermes-data.tar.gz -C /data .

# 目标服务器：恢复 Volume
docker volume create hermes-data
docker run --rm -v hermes-data:/data -v $(pwd):/backup \
  alpine tar xzf /backup/hermes-data.tar.gz -C /data
```

---

## 4. K3s/Kubernetes 部署

### 4.1 前置条件

**服务器最低配置：**

| 项目 | 最低要求 | 推荐配置 |
|------|---------|---------|
| CPU | 2 核 | 4 核 |
| 内存 | 2 GB | 4 GB |
| 磁盘 | 20 GB | 40 GB |
| 操作系统 | Ubuntu 20.04+ / CentOS 7+ / Debian 10+ | Ubuntu 22.04 LTS |
| 网络 | 公网 IP，开放端口 30642、30119（NodePort） | — |

**软件要求：**

- K3s 或标准 Kubernetes 集群
- Docker（用于构建自定义镜像，仅在有网机器上需要）
- kubectl（K3s 自带 `k3s kubectl`）
- jq（脚本用于处理 JSON 注册表，`sudo apt-get install -y jq`）

**K3s 架构：**

```
K3s Cluster
├── Namespace: hermes
│   ├── Deployment: hermes-gateway (1 Pod)
│   │   ├── gateway-default    :8642   (s6-overlay 管理)
│   │   ├── dashboard          :9119   (Web 管理面板)
│   │   └── profile-supervisor         (s6-overlay 管理)
│   │       ├── gateway-alice  :8643
│   │       └── gateway-bob    :8644
│   ├── Service: hermes-gateway-svc (NodePort)
│   ├── PVC: hermes-data (10Gi ReadWriteOnce)
│   ├── ConfigMap: hermes-config (环境变量)
│   ├── ConfigMap: hermes-file-configs (配置文件)
│   └── Secret: hermes-api-keys (API 密钥)
```

**端口映射：**

| 用途 | 容器端口 | K3s NodePort | 访问地址 |
|------|---------|-------------|---------|
| Default Gateway | 8642 | 30642 | `http://<节点IP>:30642` |
| Dashboard | 9119 | 30119 | `http://<节点IP>:30119` |
| Profile 1 | 8643 | 30643 | `http://<节点IP>:30643` |
| Profile 2 | 8644 | 30644 | `http://<节点IP>:30644` |
| Profile 3-8 | 8645-8650 | 30645-30650 | `http://<节点IP>:3064x` |

### 4.2 在线安装

适用于服务器可以访问 Docker Hub 和 LLM API 的场景。

#### 步骤 1：构建自定义镜像

```bash
cd hermes-deploy
docker build -t hermes-profile:latest -f Dockerfile.profile .
```

#### 步骤 2：导入镜像到 K3s

K3s 使用 containerd 而非 Docker，需要将镜像导入到 K3s 的容器运行时：

```bash
# 导出镜像
docker save hermes-profile:latest -o hermes-profile.tar

# 导入到 k3s
sudo k3s ctr images import hermes-profile.tar

# 验证
sudo k3s ctr images ls | grep hermes-profile
```

#### 步骤 3：修改 Secret 中的 API Key

编辑 `k8s/secret.yaml`，填入 API Server 密钥和 LLM 提供商的 API Key：

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: hermes-api-keys
  namespace: hermes
type: Opaque
stringData:
  API_SERVER_KEY: "your-random-key"          # ⚠️ 必须设置（openssl rand -hex 16）
  DASHSCOPE_API_KEY: "sk-your-actual-key"    # ← 填入真实 Key
  DASHSCOPE_BASE_URL: "https://dashscope.aliyuncs.com/compatible-mode/v1"
  OPENAI_API_KEY: ""
  ANTHROPIC_API_KEY: ""
  DEEPSEEK_API_KEY: ""
  OPENROUTER_API_KEY: ""
  HERMES_CUSTOM_API_KEY: ""
```

> ⚠️ `API_SERVER_KEY` 是 Gateway API 的认证密钥，**必须设置**，否则 API Server 拒绝启动。生成方式：`openssl rand -hex 16`。

#### 步骤 4：按需修改 ConfigMap

编辑 `k8s/configmap.yaml` 修改默认配置：

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: hermes-config
  namespace: hermes
data:
  HERMES_PROVIDER: "alibaba"           # LLM 提供商
  HERMES_MODEL: "qwen3.7-max"         # 模型
  HERMES_API_MODE: "chat_completions"
  HERMES_DASHBOARD: "1"
  HERMES_DASHBOARD_INSECURE: "1"
  API_SERVER_HOST: "0.0.0.0"           # ⚠️ 必须设为 0.0.0.0
```

> ⚠️ `API_SERVER_HOST` 必须设为 `0.0.0.0`，否则 K8s 的 readiness probe 和 NodePort 都无法访问。`API_SERVER_KEY` 已在步骤 3 的 `secret.yaml` 中配置。

编辑 `k8s/configmap-files.yaml` 可自定义 Agent 人设（SOUL.md）和网关配置（gateway.json）。

> ⚠️ `config.yaml` 不通过 ConfigMap 注入——Hermes Gateway 启动时会自动重新生成此文件，覆盖任何外部注入的内容。Agent 行为配置（provider、model 等）应通过环境变量（`configmap.yaml`）注入。详见 [架构分析报告](docs/multi-tenant-architecture-analysis.md)。

#### 步骤 5：部署

```bash
bash scripts/k3s-deploy.sh apply
```

此命令等价于：

```bash
kubectl create namespace hermes --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -k k8s/
kubectl -n hermes rollout status deployment/hermes-gateway --timeout=180s
```

### 4.3 离线安装

适用于目标机器无法访问公网的场景。在有网机器上打包，传输到目标机器后一键安装。

#### 步骤 1：在有网机器上打包

```bash
cd hermes-deploy
bash scripts/offline-pack.sh
```

打包流程自动执行：
1. 拉取基础镜像 `nousresearch/hermes-agent:latest`
2. 构建自定义镜像 `hermes-profile:latest`
3. `docker save` 导出两个镜像为 tar 文件
4. 复制 `k8s/`、`configs/`、`scripts/` 到离线包
5. 生成 `install.sh` 安装入口
6. 打包为 `hermes-offline-YYYYMMDD.tar.gz`

输出示例：

```
=== Hermes Agent K3s 离线打包 ===
[1/6] 拉取基础镜像...
[2/6] 构建自定义镜像...
[3/6] 导出镜像...
  自定义镜像: 1.3G
  基础镜像:   1.1G
[4/6] 复制部署文件...
[5/6] 创建安装入口...
[6/6] 打包...

✓ 离线包: hermes-offline-20260609.tar.gz (2.1G)
```

#### 步骤 2：传输到目标机器

```bash
scp hermes-offline-20260608.tar.gz user@target-server:/opt/
```

#### 步骤 3：在目标机器上安装

```bash
ssh user@target-server

cd /opt
tar xzf hermes-offline-20260608.tar.gz
cd offline-package

# 修改 API Key（必须!）
vi k8s/secret.yaml

# 一键安装
bash install.sh
```

`install.sh` 自动执行：
1. 检查 K3s 是否已安装
2. 导入基础镜像和自定义镜像到 K3s（`k3s ctr images import`）
3. 部署 K8s 资源（`kubectl apply -k k8s/`）

### 4.4 验证

```bash
# 查看 Pod 状态（需要等待 readiness probe 通过，约 60-180 秒）
bash scripts/k3s-deploy.sh status

# 或直接使用 kubectl
kubectl get pods -n hermes -o wide
kubectl get svc -n hermes
kubectl get pvc -n hermes

# 验证 Gateway 可达
curl -H "Authorization: Bearer <your-API_SERVER_KEY>" http://<节点IP>:30642/v1/models

# 验证 Dashboard 可达
curl http://<节点IP>:30119

# 测试 Chat API
curl -X POST http://<节点IP>:30642/v1/chat/completions \
  -H "Authorization: Bearer <your-API_SERVER_KEY>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "hermes-agent",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

**预期输出：**
- Pod 状态为 `Running 1/1`
- `/v1/models` 返回 JSON 包含 `"id": "hermes-agent"`
- Dashboard 返回 HTTP 200
- Chat API 返回 AI 响应

### 4.5 变更

**修改 ConfigMap（环境变量/配置）：**

```bash
# 编辑 ConfigMap
vi k8s/configmap.yaml

# 重新应用并滚动重启
kubectl apply -k k8s/
kubectl rollout restart deployment/hermes-gateway -n hermes
```

**修改 Secret（API Key）：**

```bash
vi k8s/secret.yaml
kubectl apply -k k8s/
kubectl rollout restart deployment/hermes-gateway -n hermes
```

**修改资源配置（CPU/内存）：**

编辑 `k8s/deployment.yaml` 中的 `resources` 字段：

```yaml
resources:
  limits:
    cpu: "2"
    memory: 2Gi
  requests:
    cpu: "1"
    memory: 1Gi
```

**推荐规格：**

| 规模 | Profile 数 | CPU requests/limits | Memory requests/limits |
|------|-----------|-------------------|----------------------|
| 小型 | 1-2 个 | `1` / `2` | `1Gi` / `2Gi` |
| 中型 | 3-5 个 | `2` / `4` | `2Gi` / `4Gi` |
| 大型 | 6-8 个 | `4` / `6` | `4Gi` / `8Gi` |

每个 Profile gateway 进程约占 100-200MB 内存。

修改后重新部署：

```bash
kubectl apply -k k8s/
kubectl rollout restart deployment/hermes-gateway -n hermes
```

**修改 Agent 人设/网关配置：**

```bash
vi k8s/configmap-files.yaml
kubectl apply -k k8s/
kubectl rollout restart deployment/hermes-gateway -n hermes
```

### 4.6 升级

**镜像更新（Dockerfile 或 s6 配置变更）：**

```bash
# 重新构建镜像
docker build -t hermes-profile:latest -f Dockerfile.profile .

# 导入到 k3s
docker save hermes-profile:latest | sudo k3s ctr images import -

# 滚动重启
kubectl rollout restart deployment/hermes-gateway -n hermes

# 等待完成
kubectl -n hermes rollout status deployment/hermes-gateway --timeout=180s
```

**仅重新应用 K8s 清单（无镜像变更）：**

```bash
bash scripts/k3s-deploy.sh update
```

### 4.7 卸载

```bash
# 删除所有 K8s 资源（Namespace、Deployment、Service、PVC 等）
bash scripts/k3s-deploy.sh delete
```

> ⚠️ 此操作会删除 Namespace 下的所有资源，包括 PVC 中的数据。请先备份。

### 4.8 数据管理

#### 存储位置

K3s 部署使用 **PVC（PersistentVolumeClaim）** 存储数据：

| 项目 | 值 |
|------|-----|
| PVC 名称 | `hermes-data` |
| 容器内路径 | `/opt/data` |
| 存储大小 | 10Gi |
| 访问模式 | ReadWriteOnce |

查看 PVC 状态：

```bash
kubectl get pvc -n hermes
```

#### 备份与恢复

```bash
# 导出 PVC 数据
kubectl exec -n hermes deployment/hermes-gateway -- \
  tar czf /tmp/backup.tar.gz /opt/data
kubectl cp hermes/$(kubectl get pod -n hermes -l app=hermes-gateway \
  -o name | head -1 | cut -d/ -f2):/tmp/backup.tar.gz \
  ./hermes-backup-$(date +%Y%m%d).tar.gz

# 导出 ConfigMap 和 Secret
kubectl get configmap hermes-config -n hermes -o yaml > configmap-backup.yaml
kubectl get configmap hermes-file-configs -n hermes -o yaml > configmap-files-backup.yaml
kubectl get secret hermes-api-keys -n hermes -o yaml > secret-backup.yaml
```

---

## 5. 安装后使用指南

### 5.1 交互方式总览

部署完成后，Hermes Agent 提供 4 种交互方式：

| 方式 | 适用场景 | 说明 |
|------|---------|------|
| **Dashboard Web UI** | 管理、监控 | 浏览器访问，管理技能、查看会话历史、修改配置 |
| **消息平台（推荐）** | 日常使用 | Telegram/Discord/飞书/钉钉等，手机上随时对话 |
| **Gateway HTTP API** | 程序集成 | REST API，供其他系统调用 |
| **CLI 终端** | 调试、高级操作 | 进入容器运行 `hermes` 命令 |

**访问地址：**

| 部署方式 | Dashboard | Gateway API |
|---------|-----------|-------------|
| Docker Compose | `http://<IP>:9119` | `http://<IP>:8642` |
| K3s | `http://<IP>:30119` | `http://<IP>:30642` |

> **注意**：Dashboard 本身是**管理界面**（技能管理、模型切换、会话历史等），不是聊天窗口。聊天功能需使用消息平台或 CLI 终端。

### 5.2 消息平台接入

Hermes 的核心设计是通过消息平台交互，支持 20+ 平台：

| 平台 | 适配器 | 核心配置 |
|------|--------|---------|
| Telegram | `telegram.py` | Bot Token（从 @BotFather 获取） |
| Discord | `discord.py` | Bot Token |
| Slack | `slack.py` | Bot Token + App Token |
| 飞书/Lark | `feishu.py` | App ID + App Secret |
| 钉钉 | `dingtalk.py` | App Credentials |
| 企业微信 | `wecom.py` | Bot ID + Secret |
| 微信 | `weixin.py` | Token + Account ID |
| QQ | `qqbot/` | App ID + Client Secret |
| WhatsApp | `whatsapp.py` | Business API |

**配置步骤（以 Telegram 为例）：**

```bash
# 1. 在 Telegram 找 @BotFather，发送 /newbot 创建机器人，获取 Bot Token

# 2. 进入容器
# Docker Compose:
docker exec -it hermes-gateway bash
# K3s:
kubectl exec -it -n hermes deployment/hermes-gateway -- bash

# 3. 运行交互式配置向导
hermes setup
# 选择 Telegram → 输入 Bot Token → 完成

# 4. 退出容器
exit
```

配置完成后，直接在 Telegram 里给 Bot 发消息，即可与 Agent 对话。

### 5.3 CLI 常用命令

进入容器后（`docker exec -it hermes-gateway bash` 或 `kubectl exec -it -n hermes deployment/hermes-gateway -- bash`）：

```bash
# 与 Agent 对话（交互模式）
hermes

# 技能管理
hermes skills list                # 查看已安装技能
hermes skills install web-search  # 安装技能
hermes skills browse              # 浏览可用技能
hermes skills update              # 更新所有技能

# 模型管理
hermes model list                 # 查看可用模型
hermes model                      # 交互式配置向导

# 工具管理
hermes tools list                 # 查看可用工具

# 完整配置向导
hermes setup                      # 平台、模型、认证等完整配置
```

### 5.4 HTTP API

Gateway 提供 REST API 接口：

```bash
# 检查健康状态
curl http://<IP>:<PORT>/health

# 查看可用模型
curl -H "Authorization: Bearer <API_SERVER_KEY>" http://<IP>:<PORT>/v1/models

# 发送对话请求
curl -X POST http://<IP>:<PORT>/v1/chat/completions \
  -H "Authorization: Bearer <API_SERVER_KEY>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "hermes-agent",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

> K3s 部署需要 `API_SERVER_KEY` 认证。Docker Compose 部署如未设置 `API_SERVER_KEY`，则无需认证。

---

## 6. 多实例管理（通用）

多实例模式下，每个实例都运行独立的 Hermes Agent 服务，拥有完全隔离的数据、网络和资源配置。支持 Docker Compose 和 K3s/Kubernetes 两种运行环境，脚本自动检测当前环境并使用相应的命令。

### 6.1 架构概述

#### Docker Compose 模式

每个实例都是一个独立的 Docker Compose 项目：

- **独立容器**：每个实例运行在自己的容器中
- **独立数据**：每个实例有独立的数据目录（`data/instances/<id>/hermes-data/`）
- **独立网络**：每个实例使用独立的 Docker 网络（`hermes-<id>-net`）
- **独立端口**：Gateway 从 18642 开始递增，Dashboard 从 19119 开始递增

```
┌─────────────────────────┐  ┌─────────────────────────┐
│  Container: user-alice   │  │  Container: user-bob     │
│  Gateway:  :18642        │  │  Gateway:  :18643        │
│  Dashboard: :19119       │  │  Dashboard: :19120       │
│  Data: data/instances/   │  │  Data: data/instances/   │
│        user-alice/       │  │        user-bob/         │
└─────────────────────────┘  └─────────────────────────┘
```

#### K3s/Kubernetes 模式

每个实例在共享 `hermes` Namespace 内拥有独立的 K8s 资源集合：

- **独立 Deployment**: 每个实例 1 个 Pod，Recreate 策略
- **独立 PVC**: 每个实例独立数据卷（5Gi 默认）
- **独立 ConfigMap/Secret**: 实例级 LLM 配置和 API Key
- **独立 Service (NodePort)**: 自动分配 NodePort，避免冲突
- **实例注册表**: 存储在 ConfigMap `hermes-instance-registry` 中，可通过 `kubectl get` 查看

```
K3s Cluster
├── Namespace: hermes
│   ├── Deployment: hermes-gateway-user-alice  (1 Pod)
│   │   ├── gateway :8642 → NodePort 30742
│   │   ├── dashboard :9119 → NodePort 30219
│   │   ├── PVC: hermes-data-user-alice
│   │   ├── ConfigMap: hermes-config-user-alice
│   │   ├── Secret: hermes-api-keys-user-alice
│   │   └── Service: hermes-gateway-user-alice-svc
│   ├── Deployment: hermes-gateway-user-bob  (1 Pod)
│   │   ├── gateway :8642 → NodePort 30743
│   │   ├── ... (同上结构，ID 后缀不同)
│   └── ConfigMap: hermes-instance-registry (实例注册表)
```

**两种模式对比：**

| 维度 | Docker Compose | K3s/Kubernetes |
|------|---------------|----------------|
| 容器/Pod | 独立 Docker 容器 | 独立 K8s Pod |
| 数据存储 | Bind mount (`data/instances/`) | PVC (每个实例独立) |
| 端口暴露 | Host 端口 (18642+) | NodePort (30742+) |
| 注册表 | 本地文件 `instances.json` | ConfigMap `hermes-instance-registry` |
| 重启速度 | ~5s (`docker restart`) | ~120s (`rollout restart`) |
| 网络隔离 | Docker bridge 网络 | K8s Namespace + NetworkPolicy |
| 管理命令 | `instance.sh` (自动检测) | `instance.sh` (自动检测) |

### 6.2 资源规划

基于 4GB 内存、4 核 CPU 服务器的默认预算：

| 项目 | 值 |
|------|-----|
| 服务器总内存 | 4096 MB |
| OS + Docker 预留 | 1024 MB |
| 安全余量 | 720 MB |
| **可用于实例** | **2352 MB** |
| 服务器总 CPU | 4 核 |
| OS + Docker 预留 | 1.5 核 |
| **可用于实例** | **2.5 核** |
| 最大实例数 | 4 个 |
| 默认每实例配额 | 768 MB 内存、0.75 CPU |

创建前检查剩余资源：

```bash
bash scripts/instance.sh resources
```

### 6.3 实例生命周期管理

```bash
# 创建实例
# 格式: instance.sh create <id> [memory_mb] [cpu_limit] [provider] [base_url] [api_key] [model] [skills]
bash scripts/instance.sh create user-alice
bash scripts/instance.sh create user-bob 512 0.5 anthropic "" sk-ant-xxx
bash scripts/instance.sh create user-tzy 2048 2 alibaba "" sk-xxx qwen3.7-max
bash scripts/instance.sh create user-dave 768 0.75 openai "" sk-xxx "" web-search,code-review

# 启动实例
bash scripts/instance.sh start user-alice

# 停止实例
bash scripts/instance.sh stop user-alice

# 重启实例
bash scripts/instance.sh restart user-alice

# 列出所有实例
bash scripts/instance.sh list

# 查看实例详情（含端口分配）
bash scripts/instance.sh status user-alice

# 在实例中执行命令
bash scripts/instance.sh exec user-alice "hermes skills list"
bash scripts/instance.sh exec user-alice "bash"    # 进入容器

# 删除实例（保留数据）
bash scripts/instance.sh delete user-alice

# 删除实例并清理所有数据
bash scripts/instance.sh delete user-alice --purge
```

> **注意**: 命令在 Docker Compose 和 K3s 环境下通用，脚本自动检测运行环境。在 K3s 模式下：
> - `create`: 自动分配 NodePort，渲染 K8s manifests 并 `kubectl apply`
> - `start`/`stop`: `kubectl scale --replicas=1/0`
> - `restart`: `kubectl rollout restart`（约 120s，比 Docker 的 ~5s 慢）
> - `delete --purge`: 删除所有 K8s 资源（Deployment、PVC、ConfigMap、Secret、Service）
> - `exec`: `kubectl exec -it`
> - 注册表存储在 ConfigMap `hermes-instance-registry` 中，可通过 `kubectl get configmap hermes-instance-registry -n hermes -o yaml` 查看

---

## 7. 多 Profile 管理（通用）

### 7.1 架构概述

多 Profile 模式在**单个 Docker 容器**内运行多个独立的 Hermes Gateway 进程，每个 Profile 拥有完全隔离的配置、记忆、会话、技能。支持 Docker Compose 和 K3s 两种运行环境。

```
┌────────────────────────────────────────────────────────┐
│           Docker Container (hermes-gateway)              │
│                                                         │
│  s6-overlay (PID 1)                                     │
│  ├── gateway-default    → hermes gateway run   :8642    │
│  ├── dashboard          → hermes dashboard     :9119    │
│  └── gateway-profiles   → profile-supervisor.sh         │
│       ├── Gateway-alice (HERMES_HOME=.../alice) :8643   │
│       ├── Gateway-bob   (HERMES_HOME=.../bob)   :8644   │
│       └── Gateway-carol (HERMES_HOME=.../carol) :8645   │
│                                                         │
│  /opt/data/profiles/                                    │
│  ├── alice/   ← alice 的 HERMES_HOME (独立隔离)        │
│  ├── bob/     ← bob 的 HERMES_HOME                     │
│  └── carol/   ← carol 的 HERMES_HOME                   │
└────────────────────────────────────────────────────────┘
```

**端口分配：**

| 端口 | 用途 |
|------|------|
| 8642 | Default Profile Gateway（s6 管理） |
| 8643-8650 | Profile Gateway（supervisor 管理，最多 8 个） |
| 9119 | Dashboard（共享） |

**与多实例模式对比：**

| 维度 | 多 Profile 模式 | 多实例模式 |
|------|----------------|-----------|
| 容器数量 | 1 个 | N 个 |
| 资源开销 | 低（共享容器） | 高（每实例 ~300MB） |
| 数据隔离 | Profile 级别 | 容器级别 |
| 故障隔离 | 进程级别 | 容器级别 |
| 最大数量 | 8 个 Profile | 受服务器资源限制 |
| 适用场景 | 多用户共享、轻量隔离 | 强隔离、多租户 |

### 7.2 Profile 生命周期管理

```bash
# 创建 Profile
# 格式: profile.sh create <name> [provider] [model] [api-key] [base-url]
bash scripts/profile.sh create alice alibaba qwen3.7-max sk-xxx
bash scripts/profile.sh create bob openai gpt-4o sk-ant-xxx

# 启动 Profile
bash scripts/profile.sh start alice

# 停止 Profile
bash scripts/profile.sh stop alice

# 重启 Profile
bash scripts/profile.sh restart alice

# 列出所有 Profile
bash scripts/profile.sh list

# 查看 Profile 详情
bash scripts/profile.sh status alice

# 查看 Profile 日志
bash scripts/profile.sh logs alice -f

# 删除 Profile（保留数据）
bash scripts/profile.sh delete alice

# 删除 Profile 并清理所有数据
bash scripts/profile.sh delete alice --purge

# 查看资源使用
bash scripts/profile.sh resources
```

### 7.3 消息平台集成

创建 Profile 后，可以对接微信、飞书、QQ、Telegram 等 IM 平台：

```bash
# 交互式配置 IM 平台（自动 stop → setup 向导 → start）
bash scripts/profile.sh setup alice
```

`setup` 命令会：
1. 自动停止 Profile（如果正在运行）
2. 进入容器启动 Hermes 交互式 setup 向导
3. 终端直通——扫码、输入凭证、选择平台等交互直接在宿主机终端完成
4. 用户完成 setup 后，自动重启 Profile

> **注意**：不要在 Profile 运行时直接运行 `hermes gateway setup`，应使用 `profile.sh setup` 命令，它会自动处理 stop/start 生命周期。

**手动配置（直接编辑 .env）：**

```bash
# Docker Compose 环境：
cat >> data/profiles/alice/.env << EOF

# === 飞书 Bot ===
FEISHU_APP_ID=cli_xxxxxxxxx
FEISHU_APP_SECRET=xxxxxxxx
EOF

# K3s 环境（通过 kubectl exec）：
kubectl exec -n hermes deployment/hermes-gateway -- bash -c '
cat >> /opt/data/profiles/alice/.env << EOF

# === 飞书 Bot ===
FEISHU_APP_ID=cli_xxxxxxxxx
FEISHU_APP_SECRET=xxxxxxxx
EOF'

# 重启生效
bash scripts/profile.sh restart alice
```

---

## 8. 日常运维

### 8.1 健康检查

**Docker Compose：**

```bash
bash scripts/health-check.sh
```

检查项目包括：

| 检查项 | 说明 | 阈值 |
|--------|------|------|
| Docker 状态 | Docker daemon 是否运行 | — |
| 容器状态 | hermes-gateway 是否 healthy | — |
| 内存使用 | 容器内存使用率 | >80% 警告，>90% 错误 |
| 磁盘空间 | 宿主机磁盘使用率 | >85% 警告 |
| 端口监听 | 8642、9119 是否监听 | — |
| 数据卷 | hermes-data Volume 是否存在 | — |
| 配置文件 | .env、configs/ 是否存在 | — |
| 备份时效 | 最近一次备份距今是否超过 7 天 | — |

```bash
# 查看详细资源统计
bash scripts/health-check.sh stats
```

**K3s：**

```bash
# 查看 Pod 详情
kubectl describe pod -n hermes -l app=hermes-gateway

# 查看事件
kubectl get events -n hermes --sort-by='.lastTimestamp'

# 查看资源使用
kubectl top pod -n hermes
```

### 8.2 日志查看

**Docker Compose：**

```bash
# 查看所有服务日志
bash scripts/deploy.sh logs

# 查看 Gateway 日志
bash scripts/deploy.sh logs hermes-gateway

# 查看最近 100 行
docker compose logs --tail=100 hermes-gateway

# 实时跟踪
docker logs -f hermes-gateway
```

**K3s：**

```bash
# 通过脚本
bash scripts/k3s-deploy.sh logs

# 通过 kubectl
kubectl logs -f -n hermes deployment/hermes-gateway

# 查看最近 100 行
kubectl logs --tail=100 -n hermes deployment/hermes-gateway
```

**Profile 日志：**

```bash
# 查看指定 Profile 的日志
bash scripts/profile.sh logs alice -f
```

### 8.3 资源监控

**Docker Compose：**

```bash
# 实时查看资源使用
docker stats

# 查看 Docker 系统资源占用
docker system df
```

**K3s：**

```bash
# 查看 Pod 资源使用
kubectl top pod -n hermes

# 查看 PVC 使用
kubectl get pvc -n hermes
```

**部署管理命令速查：**

| 操作 | Docker Compose | K3s |
|------|---------------|-----|
| 部署 | `deploy.sh start` | `k3s-deploy.sh apply` |
| 停止 | `deploy.sh stop` | `k3s-deploy.sh delete` |
| 状态 | `deploy.sh status` | `k3s-deploy.sh status` |
| 日志 | `deploy.sh logs` | `k3s-deploy.sh logs` |
| 进入容器 | `deploy.sh exec hermes-gateway bash` | `k3s-deploy.sh exec` |
| 重启 | `deploy.sh restart` | `k3s-deploy.sh update` |

---

## 9. Profile 权限隔离

### 9.1 问题背景

多 Profile 模式下，所有 Profile Gateway 进程以**同一个 Linux 用户**（hermes, UID 1000）运行在同一容器内。虽然每个 Profile 有独立的 `HERMES_HOME` 目录，但进程有能力越界访问其他 Profile 的数据：

```
改造前:
/opt/data/profiles/
├── alice/   ← owner: hermes (UID 1000), 所有 Profile 进程可读写
├── bob/     ← owner: hermes (UID 1000), 所有 Profile 进程可读写
└── carol/   ← owner: hermes (UID 1000), 所有 Profile 进程可读写
```

### 9.2 多 UID 隔离方案

为每个 Profile 分配独立的 Linux 用户，利用内核级文件权限实现隔离。

**UID 分配方案：**

| UID | 用户 | 用途 |
|-----|------|------|
| 1000 | `hermes` | default profile、共享资源、supervisor |
| 1100 | `hermes-p-alice` | Profile: alice |
| 1101 | `hermes-p-bob` | Profile: bob |
| ... | `hermes-p-<name>` | 按创建顺序递增 |
| 1107 | `hermes-p-<8th>` | 最多 8 个 Profile |

公式：`PROFILE_UID = 1100 + index`，UID 存储在 `registry.json` 的 `uid` 字段中。

**改造后的效果：**

```
改造后:
/opt/data/profiles/          ← owner: root:hermes, mode: 755 (可遍历不可写)
├── alice/                   ← owner: hermes-p-alice (UID 1100), mode: 700
├── bob/                     ← owner: hermes-p-bob   (UID 1101), mode: 700
└── carol/                   ← owner: hermes-p-carol  (UID 1102), mode: 700

alice 的进程 (UID 1100) → 可读写 alice/ ✗ 无法访问 bob/ carol/
bob 的进程   (UID 1101) → 可读写 bob/   ✗ 无法访问 alice/ carol/
```

**核心流程变化：**

| 操作 | 改造前 | 改造后 |
|------|--------|--------|
| `profile.sh create` | chown 1000:1000 | 分配 UID，chown `<uid>:1000`，chmod 700 |
| `profile.sh start` | `s6-setuidgid hermes` | `s6-setuidgid hermes-p-<name>` |
| `profile.sh stop` | grep 进程名匹配 | `ps -u hermes-p-<name>` 精确匹配 |
| `profile.sh delete --purge` | 删除目录 | 删除目录 + `userdel` 清理用户 |
| `profile.sh setup` | 以 hermes 用户运行 | 以 profile 专属用户运行 |
| supervisor 重启恢复 | root 身份直接启动 | 创建用户 → 修复权限 → s6-setuidgid 启动 |

### 9.3 权限模型

```
/opt/hermes/          owner=root    mode=755  所有用户可读可执行，不可写
/opt/hermes/.venv/    owner=hermes  mode=755  Python 虚拟环境，other+rX
/opt/data/            owner=root    mode=755  父目录可遍历
/opt/data/profiles/   owner=root    mode=755  父目录可遍历
  ├── alice/          owner=1100    mode=700  只有 UID 1100 能读写
  ├── bob/            owner=1101    mode=700  只有 UID 1101 能读写
  ├── registry.json   owner=root    mode=644  supervisor(root) 读写
  └── commands.json   owner=root    mode=644  宿主机写，supervisor 读
```

**共享资源权限**：Hermes 程序目录（`/opt/hermes/`）在 Dockerfile 构建阶段设置 `o+rX`，所有 Profile 用户可以读取和执行，但不能写入。这保证了 Profile 进程只能读共享代码，不能篡改。

### 9.4 改造文件清单

| 文件 | 改造内容 |
|------|---------|
| `scripts/lib/profile-common.sh` | 新增 `PROFILE_UID_BASE/MAX` 常量、`allocate_profile_uid()`、`get_profile_uid()`、`get_profile_username()`、`ensure_profile_user()`、`set_profile_permissions()` 函数；`register_profile()` 增加 uid 参数；`pf_push_dir()` 和 `pf_write_file()` 支持可选 uid 参数 |
| `scripts/profile.sh` | `cmd_create` 分配 UID 并传入注册表；`cmd_start` 创建用户、设置权限、以 profile UID 启动；`cmd_stop` 按用户名匹配进程；`cmd_delete` 清理用户；`cmd_setup` 以 profile 用户运行 |
| `scripts/profile-supervisor.sh` | 新增 `ensure_profile_user()`、`fix_profile_permissions()` 函数；`start_profile` 读取 UID 并以 `s6-setuidgid` 切换用户启动；`load_registry` 和 `reconcile_profiles` 同步 UID |
| `Dockerfile.profile` | 共享资源添加 `o+rX` 权限；Profile 父目录改为 `root:hermes 755` |

### 9.5 向后兼容

`get_profile_uid()` 在读取注册表时使用 `jq '.profiles[$name].uid // 1000'`，确保：

- **旧 Profile**（registry.json 中无 `uid` 字段）自动 fallback 到 UID 1000（hermes 用户），行为与改造前完全一致
- **新 Profile** 从 UID 1100 开始分配，不会与旧的 UID 1000 冲突
- 无需迁移现有数据，平滑过渡

### 9.6 验证方法

部署后可在容器内验证隔离效果：

```bash
# 进入容器
kubectl exec -it -n hermes deployment/hermes-gateway -- bash

# 验证用户已创建
id hermes-p-alice    # uid=1100(hermes-p-alice) gid=1000(hermes)
id hermes-p-bob      # uid=1101(hermes-p-bob)   gid=1000(hermes)

# 验证文件权限
ls -la /opt/data/profiles/
# drwx------ 2 hermes-p-alice hermes alice/
# drwx------ 2 hermes-p-bob   hermes bob/

# 验证隔离: alice 无法读 bob 的数据
su -s /bin/bash hermes-p-alice -c "ls /opt/data/profiles/bob/"
# Permission denied ✓

# 验证 alice 无法写共享程序目录
su -s /bin/bash hermes-p-alice -c "touch /opt/hermes/test"
# Permission denied ✓

# 验证 alice 可以正常读写自己的目录
su -s /bin/bash hermes-p-alice -c "echo ok > /opt/data/profiles/alice/test && cat /opt/data/profiles/alice/test"
# ok ✓

# 验证进程以正确 UID 运行
ps -eo pid,user,cmd | grep hermes
# 1234 hermes-p-alice  hermes -p alice gateway run
# 1235 hermes-p-bob    hermes -p bob gateway run
```

---

## 附录 A：脚本详细解析

### A.1 install.sh — 环境安装

**用途**：在云服务器上安装 Docker 和 Docker Compose。

**使用方式**：

```bash
bash scripts/install.sh
```

**核心流程**：

1. **检测操作系统**（`detect_os`）：读取 `/etc/os-release` 文件，识别是 Ubuntu、Debian、CentOS 还是 RHEL 等
2. **安装 Docker**：
   - Ubuntu/Debian：通过 `apt` 安装，先添加 Docker 官方 GPG 密钥和软件源
   - CentOS/RHEL：通过 `yum` 安装，先添加 Docker CE 的 yum 仓库
3. **启动 Docker**：启动 Docker 服务，设置为开机自启，将当前用户加入 `docker` 组
4. **验证安装**：确认 `docker` 和 `docker compose` 命令可用
5. **初始化 .env**：如果 `.env` 文件不存在，从 `.env.example` 复制一份

**注意事项**：
- 不能以 root 用户运行（脚本会拒绝）
- 安装后需要**注销并重新登录**才能使 `docker` 组权限生效

---

### A.2 deploy.sh — 服务生命周期管理

**用途**：管理 Docker Compose 部署的完整生命周期。

**使用方式**：

```bash
bash scripts/deploy.sh <command>
```

**可用命令**：

| 命令 | 做什么 |
|------|--------|
| `init` | 初始化环境：创建 `backups`、`logs`、`skills` 目录，拉取 Docker 镜像 |
| `start` | 启动服务：`docker compose up -d`，等待 5 秒后显示状态和访问地址 |
| `stop` | 停止服务：`docker compose down` |
| `restart` | 重启服务：先 stop，等 2 秒，再 start |
| `status` | 查看状态：显示容器运行状态、CPU/内存使用率、健康检查结果 |
| `logs [服务名]` | 查看日志：默认显示所有服务，可指定具体服务 |
| `exec <容器> <命令>` | 在容器内执行命令，如 `exec hermes-gateway hermes skills list` |
| `update` | 更新服务：先备份数据，拉取新镜像，用 `--force-recreate` 重建容器 |
| `cleanup` | 清理环境：`docker compose down --volumes --remove-orphans` + `docker system prune` |

---

### A.3 backup.sh — 数据备份与恢复

**用途**：备份和恢复 Hermes 的完整数据（Docker 数据卷 + 配置文件 + 环境变量 + Compose 文件）。

**使用方式**：

```bash
bash scripts/backup.sh              # 执行备份（默认）
bash scripts/backup.sh list         # 列出所有备份
bash scripts/backup.sh restore <备份名>  # 恢复指定备份
bash scripts/backup.sh stats        # 查看备份统计
```

**核心流程**：

1. **备份 Docker 数据卷**（`backup_volume`）：启动一个临时 Alpine 容器，将 `hermes-data` 卷打包为 `.tar.gz`
2. **备份配置文件**（`backup_configs`）：打包 `configs/` 目录
3. **备份环境变量**（`backup_env`）：复制 `.env` 文件
4. **备份 Compose 文件**（`backup_compose`）：复制 `docker-compose.yml`
5. **清理旧备份**（`cleanup_old_backups`）：删除超过 `BACKUP_RETENTION_DAYS`（默认 7 天）的旧备份

**恢复流程**：

1. 停止服务（`docker compose down`）
2. 通过临时 Alpine 容器解压数据卷
3. 恢复配置文件和 `.env`
4. 重启服务

---

### A.4 health-check.sh — 健康检查

**用途**：全面检查 Hermes 部署的健康状态。

**使用方式**：

```bash
bash scripts/health-check.sh        # 执行完整健康检查
bash scripts/health-check.sh stats  # 查看详细资源统计
```

**检查项**：

| 函数 | 检查什么 | 判断标准 |
|------|---------|---------|
| `check_docker` | Docker daemon 是否在运行 | `docker info` 命令是否成功 |
| `check_containers` | hermes-gateway 容器状态 | 区分 healthy / running-but-unhealthy / stopped |
| `check_resources` | 容器内存使用率 | >80% 警告，>90% 错误 |
| `check_disk` | 宿主机磁盘使用率 | >85% 警告 |
| `check_logs` | Docker 日志文件大小 | — |
| `check_ports` | 8642 和 9119 端口是否在监听 | 使用 `netstat` 或 `ss` 检查 |
| `check_volume` | hermes-data Docker Volume | 是否存在并报告大小 |
| `check_configs` | .env 和 configs/ 目录 | 是否存在 |
| `check_backup` | 最近备份时间 | 超过 7 天警告 |

**退出码**：0 = 一切正常，1 = 有警告，2 = 有错误

---

### A.5 instance.sh — 多实例管理

**用途**：在同一服务器上管理多个独立的 Hermes Agent 实例，每个实例拥有独立的容器、数据和端口。

**使用方式**：

```bash
bash scripts/instance.sh <command> [参数]
```

**核心功能**：

- **`create`**：创建新实例。验证实例 ID 格式（3-32 位小写字母、数字、连字符），检查资源预算是否充足，从模板渲染 `.env` 和 `docker-compose.yml`，分配递增端口（从 18642/19119 开始），注册到 `data/registry/instances.json`
- **`start`**：启动实例容器，等待健康检查通过后配置 LLM 模型
- **`stop`**：停止实例容器
- **`delete`**：删除实例（`--purge` 选项会清理数据目录）
- **`list`**：显示所有实例的表格或 JSON
- **`status`**：显示实例详细信息（端口、资源配置、实际使用率）
- **`exec`**：在实例容器内执行命令
- **`resources`**：显示服务器资源使用情况（已分配/可用）

**依赖**：引用 `lib/common.sh`（日志、注册表操作）和 `lib/resource.sh`（资源预算计算）。

---

### A.6 profile.sh — Profile 管理

**用途**：在单个容器内管理多个 Hermes Profile，每个 Profile 运行独立的 Gateway 进程。

**使用方式**：

```bash
bash scripts/profile.sh <command> [参数]
```

**核心功能**：

- **`create`**：创建新 Profile。验证名称，检查是否超过 8 个上限，分配端口（8643-8650），从模板渲染配置文件（config.yaml、SOUL.md、gateway.json、.env），推送到容器内（K3s 通过 `kubectl exec` + base64，Docker 通过直接复制），注册到 `registry.json`
- **`start`**：启动 Profile。写入 `gateway_state.json`，更新注册表，在容器内以 `hermes` 用户启动 `hermes gateway run` 进程，等待端口就绪（最多 60 秒）
- **`stop`**：停止 Profile。在容器内 kill 对应进程，更新状态
- **`setup`**：IM 平台交互式配置。自动停止 Profile → 进入容器运行 `hermes gateway setup` 向导 → 自动重启
- **`delete`**：删除 Profile（`--purge` 清理数据）
- **`list`**：显示所有 Profile（状态、端口、模型）
- **`logs`**：查看 Profile 日志（支持 `-f` 跟踪模式）

**环境自适应**：自动检测运行在 Docker 还是 K3s 环境，使用对应的方式执行容器内操作。

**依赖**：引用 `lib/profile-common.sh`（Profile 注册表、端口分配、文件 I/O 抽象）。

---

### A.7 profile-supervisor.sh — 容器内 Profile 监督器

**用途**：作为 s6-overlay 的 longrun 服务在容器内运行，管理多个 Profile Gateway 进程的生命周期。**不需要用户手动执行**，由 Dockerfile.profile 打包到镜像内，由 s6-overlay 自动启动。

**核心功能**：

- **进程管理**：启动/停止/监控 Profile Gateway 进程
- **崩溃恢复**：每 5 秒检查一次所有 Profile 进程，如果进程意外退出，自动重启（指数退避策略，最大间隔 60 秒，最多重启 10 次）
- **指令队列**：读取 `commands.json`，执行来自宿主机 `profile.sh` 发出的 start/stop/restart 指令
- **状态同步**：容器重启时，读取 `registry.json`，自动恢复标记为 "running" 的 Profile
- **信号处理**：收到 SIGTERM 时，优雅停止所有 Profile 进程

**依赖**：在容器内直接运行，不需要外部依赖。

---

### A.8 k3s-deploy.sh — K3s 部署管理

**用途**：管理 K3s/Kubernetes 上的 Hermes 部署。

**使用方式**：

```bash
bash scripts/k3s-deploy.sh <command>
```

**可用命令**：

| 命令 | 做什么 |
|------|--------|
| `install` | 安装 K3s（如果未安装），然后执行 `apply` |
| `apply` | 创建 `hermes` 命名空间，用 `kubectl apply -k k8s/` 部署所有资源，等待 rollout 完成（180 秒超时） |
| `delete` | 删除所有 K8s 资源（`kubectl delete -k k8s/`） |
| `status` | 显示 Pod、Service、PVC 状态，输出 Gateway 和 Dashboard 的访问地址 |
| `logs` | 查看 Deployment 日志（`kubectl logs -f`） |
| `exec` | 进入 Pod 的 bash shell（`kubectl exec -it`） |
| `update` | 滚动重启（`kubectl rollout restart`），等待完成 |

**自适应**：自动检测 `kubectl` 或 `k3s kubectl` 命令。

---

### A.9 offline-pack.sh — 离线打包

**用途**：在有网机器上，将镜像、配置、脚本打包成一个离线安装包，用于气隙（air-gapped）环境部署。

**使用方式**：

```bash
bash scripts/offline-pack.sh
```

**打包流程**：

1. 拉取基础镜像 `nousresearch/hermes-agent:latest`
2. 构建自定义镜像 `hermes-profile:latest`（使用 `Dockerfile.profile`）
3. 导出两个镜像为 tar 文件（`docker save`）
4. 复制 `k8s/`、`configs/`、关键脚本到包目录
5. 生成 `install.sh` 入口脚本（检查 K3s → 导入镜像 → 部署）
6. 压缩为 `hermes-offline-YYYYMMDD.tar.gz`

**输出**：约 2.1GB 的离线安装包。

---

### A.10 offline-load.sh — 离线镜像导入

**用途**：在气隙环境的 K3s 节点上，从 tar 文件导入 Docker 镜像到 K3s 的 containerd 运行时。**由离线包的 `install.sh` 自动调用**，通常不需要手动执行。

**核心流程**：

1. 导入基础镜像 `hermes-agent-base.tar`（`k3s ctr images import`）
2. 导入自定义镜像 `hermes-profile.tar`（必须存在）
3. 列出已导入的 hermes 相关镜像

**自适应**：支持 `k3s ctr` 和独立的 `ctr -n k8s.io` 两种方式。

---

### A.11 lib/common.sh — 公共函数库

**用途**：提供所有脚本共用的基础函数，被 `instance.sh`、`resource.sh` 等通过 `source` 引入。

**提供的函数**：

| 函数 | 做什么 |
|------|--------|
| `log_info/warn/error/debug` | 彩色日志输出（info=蓝色、warn=黄色、error=红色） |
| `get_project_dir` | 从脚本位置向上查找 `docker-compose.yml`，返回项目根目录 |
| `validate_instance_id` | 验证实例 ID 格式：3-32 位小写字母、数字、连字符 |
| `read_registry` / `write_registry` | 读写 `data/registry/instances.json`（原子写入：先写临时文件再 mv） |
| `register_instance` / `unregister_instance` | 在注册表中添加/删除实例记录 |
| `generate_token` | 生成 64 位十六进制随机令牌（`openssl rand -hex 32`） |
| `wait_for_healthy` | 轮询容器健康状态直到 healthy 或超时 |

---

### A.12 lib/resource.sh — 资源预算管理

**用途**：计算服务器的资源预算，防止创建过多实例导致资源耗尽。被 `instance.sh` 引用。

**核心常量（基于 4GB/4 核服务器）**：

| 常量 | 值 | 说明 |
|------|-----|------|
| `TOTAL_MEMORY_MB` | 4096 | 服务器总内存 |
| `OS_DOCKER_MEMORY_MB` | 1024 | 系统 + Docker 预留 |
| `SAFETY_MARGIN_MB` | 720 | 安全余量 |
| `AVAILABLE_MEMORY_MB` | 2352 | 可用于实例的内存 |
| `MAX_INSTANCES` | 4 | 最大实例数 |

**提供的函数**：

| 函数 | 做什么 |
|------|--------|
| `can_create_instance` | 检查是否还能创建新实例（数量、内存、CPU 三重校验） |
| `calculate_instance_resources` | 计算新实例的建议资源配置（均分剩余资源） |
| `print_resource_summary` | 打印资源使用报告（总量、已分配、可用） |
| `get_instance_actual_memory/cpu` | 获取实例的实际资源使用（通过 `docker stats`） |

---

### A.13 lib/profile-common.sh — Profile 公共函数库

**用途**：提供 Profile 管理所需的所有共享函数，被 `profile.sh` 引用。

**提供的函数**：

| 函数类别 | 函数 | 做什么 |
|---------|------|--------|
| **注册表** | `read/write_profile_registry` | 读写 Profile 注册表 `registry.json` |
| | `register/unregister_profile` | 添加/删除 Profile 记录 |
| | `get_profile_port/status` | 查询 Profile 端口/状态 |
| **端口分配** | `allocate_profile_port` | 在 8643-8650 范围内找第一个未用端口 |
| **环境检测** | `pf_detect_runtime` | 自动检测当前是 Docker、K3s 还是本地环境 |
| **文件 I/O** | `pf_write_file` | 写文件（K3s 通过 base64 + kubectl exec，Docker 直接写） |
| | `pf_read_file` | 读文件（同上） |
| | `pf_push_dir` | 推送整个目录到容器内（K3s 通过 tar + kubectl exec） |
| **容器操作** | `container_exec` | 在容器内执行命令（自动适配 docker/kubectl） |
| | `verify_gateway_port` | 验证容器内端口是否就绪 |
| **s6 管理** | `write_s6_run_script` | 生成 s6 的 run 脚本并写入容器 |
| | `write_gateway_state` | 写入 gateway_state.json |

---

### A.14 Dockerfile.profile — 自定义镜像构建

**用途**：基于官方 `nousresearch/hermes-agent:latest` 镜像，添加 Profile Supervisor 服务和 UID 修复，构建自定义镜像。

**关键步骤**：

1. **修复 UID/GID**（`usermod -u 1000 hermes`）：基础镜像中 hermes 用户 UID=10000，但部署目标 UID=1000。如果不修复，K3s 的 overlayfs 上 `chown -R` 会卡死（详见附录 B 问题 11）
2. **添加 s6 服务定义**：将 `s6/gateway-profiles/` 复制到镜像内，注册到 s6 用户 bundle
3. **添加 Profile Supervisor 脚本**：复制 `scripts/profile-supervisor.sh` 到 `/opt/scripts/`
4. **设置 CMD**：`CMD ["gateway", "run"]`——不覆盖 ENTRYPOINT，保留基础镜像的 `main-wrapper.sh`

**不覆盖 ENTRYPOINT 的原因**：基础镜像的 ENTRYPOINT 是 `["/init", "/opt/hermes/docker/main-wrapper.sh"]`，它负责 s6-overlay 初始化和命令路由。如果覆盖为 `["/init"]`，会丢失 `main-wrapper.sh`，导致 `gateway run` 被当作 shell 命令而非 hermes 子命令。

---

## 附录 B：常见问题排查

### B.1 镜像拉取失败

**症状**：

```
error from registry: unknown error
```

**原因**：国内服务器从 Docker Hub 拉取 1.16GB 镜像时连接中断。

**解决**：

```bash
# 方案 1: 配置 Docker 镜像加速
sudo tee /etc/docker/daemon.json <<'EOF'
{
  "registry-mirrors": [
    "https://your-mirror.example.com",
    "https://your-mirror2.example.com"
  ]
}
EOF
sudo systemctl daemon-reload && sudo systemctl restart docker
docker pull nousresearch/hermes-agent:latest

# 方案 2: 手动导入镜像
# 在可访问 Docker Hub 的机器上：
docker save nousresearch/hermes-agent:latest | gzip > hermes-agent.tar.gz
# 传到目标服务器后：
gunzip -c hermes-agent.tar.gz | docker load
```

---

### B.2 容器不断重启 — s6 权限问题

**症状**：

```
s6-applyuidgid: fatal: unable to set supplementary group list: Operation not permitted
```

**原因**：`cap_drop: ALL` 移除了 s6-overlay 所需的 `CAP_SETUID`/`CAP_SETGID`。

**解决**：

```bash
# 从 docker-compose.yml 中移除 cap_drop: ALL
sed -i '/cap_drop:/,/ALL/d' docker-compose.yml
docker compose down && docker compose up -d
```

---

### B.3 Dashboard 启动报错 — OAuth 问题

**症状**：

```
Refusing to bind dashboard to 0.0.0.0 — the OAuth auth gate engages on
non-loopback binds, but no auth providers are registered
```

**原因**：Dashboard 绑定 `0.0.0.0` 时需要 OAuth 认证或显式 `--insecure`。

**解决**：

```bash
# 在 docker-compose.yml 的 environment 中添加：
- HERMES_DASHBOARD_INSECURE=1

# 快速修复：
sed -i '/HERMES_DASHBOARD=1/a\      - HERMES_DASHBOARD_INSECURE=1' docker-compose.yml
docker compose down && docker compose up -d
```

---

### B.4 Gateway 启动后立刻退出

**症状**：

```
Welcome to Hermes Agent! Type your message or /help for commands.
Warning: Input is not a terminal (fd=0).
Goodbye!
```

**原因**：容器启动命令是 `hermes`（无参数），默认进入交互模式。没有 stdin 时立刻退出。

**解决**：

```yaml
# 在 docker-compose.yml 中确认 command 设置正确：
services:
  hermes-gateway:
    command: ["gateway", "run"]    # 必须指定 gateway run
```

---

### B.5 健康检查超时

**症状**：

```
[WARN] Gateway: 未就绪或异常
```

**原因**：Gateway 启动需要 60-120 秒初始化，或容器内没有 `curl`。

**解决**：

```yaml
# 调整 docker-compose.yml 中的健康检查参数：
healthcheck:
  test: ["CMD-SHELL", "pgrep -f 'hermes' || exit 1"]
  interval: 15s
  timeout: 10s
  retries: 5
  start_period: 120s        # 给足启动时间
```

---

### B.6 权限问题

**症状**：

```
Error: Permission denied
```

**原因**：当前用户未加入 `docker` 组。

**解决**：

```bash
sudo usermod -aG docker $USER
# 注销并重新登录
```

---

### B.7 内存不足（OOMKilled）

**症状**：容器不断重启，`docker compose ps` 显示 "OOMKilled"。

**解决**：

```bash
# 1. 查看当前内存使用
docker stats --no-stream

# 2. 增加内存限制（编辑 docker-compose.yml）
# deploy.resources.limits.memory: 2048M

# 3. 重启
bash scripts/deploy.sh restart
```

---

### B.8 端口被占用

**症状**：

```
Error: Port 8642 is already allocated
```

**解决**：

```bash
# 1. 查看端口占用
sudo lsof -i :8642

# 2. 修改 .env 中的端口
nano .env
GATEWAY_PORT=18642

# 3. 重启
bash scripts/deploy.sh restart
```

---

### B.9 配置文件不生效

**症状**：修改 `configs/` 后，Agent 行为未改变。

**解决**：

```bash
# Docker Compose：修改配置后需重启容器
bash scripts/deploy.sh restart

# K3s：修改 ConfigMap 后需重新应用并滚动重启
kubectl apply -k k8s/
kubectl rollout restart deployment/hermes-gateway -n hermes
```

---

### B.10 磁盘空间不足

**解决**：

```bash
# 1. 查看磁盘使用
df -h

# 2. 清理 Docker 未使用资源
docker system prune -a

# 3. 清理旧备份
find ./backups -name "hermes_backup_*" -mtime +7 -delete
```

---

### B.11 K3s chown 卡死导致 Gateway 无法启动（K3s 专属）

**症状**：
- Pod 状态显示 `Running` 但进程未启动
- 日志停在 `[stage2] Fixing ownership of build trees...`
- 容器内 `ps aux` 显示 chown 进程处于 D 状态（不可中断睡眠）

**根因**：基础镜像中 hermes 用户 UID=10000，而 K8s Deployment 设置 `HERMES_UID=1000`。stage2-hook.sh 检测到 UID 不匹配后执行 `chown -R`，在 K3s 的 overlayfs 上会卡死。

Docker Compose 不受影响是因为 Docker 的 overlay2 存储层能正常处理大规模 chown，而 K3s 的 containerd overlayfs 每个文件 chown 都触发 copy-up，导致 I/O 队列堆积。

**解决**（已在 `Dockerfile.profile` 中实现）：

```dockerfile
RUN usermod -u 1000 hermes && groupmod -g 1000 hermes && \
    chown -R 1000:1000 /opt/hermes/.venv /opt/hermes/ui-tui ...
```

在 Docker build 阶段预设 UID/GID 为 1000，stage2-hook.sh 检测到 owner 匹配后跳过 chown。

**验证**：

```bash
kubectl exec -n hermes deployment/hermes-gateway -- ps aux | grep chown
# 正常情况应该没有 chown 进程

kubectl logs -n hermes deployment/hermes-gateway | grep -A 5 "stage2"
# 应该看到 "[stage2] Setup complete; starting user services"
```

---

### B.12 API Server 绑定 127.0.0.1（K3s 专属）

**症状**：
- Pod 状态 `Running 1/1`
- 容器内 `curl localhost:8642/v1/models` 正常返回
- 从 Pod 外部或节点 IP 访问超时/拒绝连接

**根因**：Hermes API Server 默认绑定 `127.0.0.1`（只监听 localhost），K8s 的 tcpSocket probe 和 NodePort 无法从外部访问。

**解决**：在 `k8s/secret.yaml` 中设置 `API_SERVER_KEY`，在 `k8s/configmap.yaml` 中设置 `API_SERVER_HOST`：

```yaml
# k8s/secret.yaml
stringData:
  API_SERVER_KEY: "your-key"     # 必须设置（openssl rand -hex 16）

# k8s/configmap.yaml
data:
  API_SERVER_HOST: "0.0.0.0"    # 必须设置
```

**验证**：

```bash
# 检查监听地址（00000000:21C2 = 0.0.0.0:8642 为正确）
kubectl exec -n hermes deployment/hermes-gateway -- cat /proc/net/tcp
```

---

### B.13 gateway: not found 错误（K3s 专属）

**症状**：

```
/run/s6/basedir/scripts/rc.init: 91: gateway: not found
```

**根因**：`Dockerfile.profile` 中错误地覆盖了 ENTRYPOINT，丢失了基础镜像的 `main-wrapper.sh`。

**解决**（已在 Dockerfile.profile 中实现）：

```dockerfile
# 不覆盖 ENTRYPOINT，使用基础镜像默认的：
# ENTRYPOINT ["/init", "/opt/hermes/docker/main-wrapper.sh"]
CMD ["gateway", "run"]
```

同时在 `k8s/deployment.yaml` 中**不要**设置 `command` 和 `args`。

---

### B.14 API Server 拒绝启动（K3s 专属）

**症状**：

```
[Api_Server] Refusing to start: API_SERVER_KEY is required
```

**解决**：在 `k8s/secret.yaml` 中设置 `API_SERVER_KEY`：

```yaml
stringData:
  API_SERVER_KEY: "your-random-key"   # 生成方式: openssl rand -hex 16
```

然后重新应用：

```bash
kubectl apply -k k8s/
kubectl rollout restart deployment/hermes-gateway -n hermes
```

---

### B.15 Readiness Probe 长时间失败（K3s 专属）

**症状**：Pod 状态长时间 `0/1 Running`，readiness probe 失败。

**可能原因**：
1. `API_SERVER_HOST` 未设置为 `0.0.0.0`（见 [B.12](#b12-api-server-绑定-127001k3s-专属)）
2. `API_SERVER_KEY` 未设置导致 API Server 未启动（见 [B.14](#b14-api-server-拒绝启动k3s-专属)）
3. Gateway 启动慢（正常情况需要 60-180 秒）

**验证**：

```bash
# 检查 readiness probe 配置
kubectl get deployment hermes-gateway -n hermes -o yaml | grep -A 10 "readinessProbe"

# 手动测试 API Server
kubectl exec -n hermes deployment/hermes-gateway -- \
  curl -H "Authorization: Bearer $API_SERVER_KEY" http://localhost:8642/v1/models

# 查看事件
kubectl describe pod -n hermes -l app=hermes-gateway | grep -A 10 "Events"
```

---

### B.16 云安全组/防火墙阻断

**症状**：服务器本地 `curl localhost:<PORT>` 正常，但从外网访问超时。

**解决**：

**Docker Compose 部署**需放行：

| 端口 | 用途 |
|------|------|
| 8642 | Gateway API |
| 9119 | Dashboard Web UI |
| 18642+ | 多实例 Gateway（递增） |
| 19119+ | 多实例 Dashboard（递增） |

**K3s 部署**需放行：

| 端口 | 用途 |
|------|------|
| 30642 | Gateway API（NodePort） |
| 30119 | Dashboard（NodePort） |
| 30643-30650 | Profile Gateways（NodePort） |

在云控制台的安全组/防火墙中添加对应的 TCP 入站规则。腾讯云轻量服务器需同时配置**轻量防火墙**和**安全组**。

---

## 附录 C：环境变量参考

### Docker Compose 环境变量（.env 文件）

| 变量名 | 默认值 | 必填 | 说明 |
|--------|--------|------|------|
| `HERMES_UID` | `1000` | 否 | 容器内用户 UID |
| `HERMES_GID` | `1000` | 否 | 容器内用户 GID |
| `HERMES_PROVIDER` | `openai` | 否 | LLM 提供商 |
| `HERMES_MODEL` | `gpt-4o` | 否 | 默认模型 |
| `HERMES_BASE_URL` | — | 否 | 自定义 API 端点 |
| `HERMES_CUSTOM_API_KEY` | — | 否 | 自定义端点 API Key |
| `OPENAI_API_KEY` | — | 是* | OpenAI API Key |
| `ANTHROPIC_API_KEY` | — | 是* | Anthropic API Key |
| `DEEPSEEK_API_KEY` | — | 是* | DeepSeek API Key |
| `OPENROUTER_API_KEY` | — | 是* | OpenRouter API Key |
| `DASHSCOPE_API_KEY` | — | 是* | 百炼（DashScope） API Key |
| `HERMES_PRELOAD_SKILLS` | — | 否 | 预装技能（逗号分隔） |
| `HERMES_LOG_FORMAT` | `json` | 否 | 日志格式（json/text） |
| `GATEWAY_PORT` | `8642` | 否 | Gateway HTTP 端口 |
| `DASHBOARD_PORT` | `9119` | 否 | Dashboard Web UI 端口 |
| `BACKUP_RETENTION_DAYS` | `7` | 否 | 备份保留天数 |
| `BACKUP_DIR` | `./backups` | 否 | 备份文件存储路径 |

> \* 至少配置一个 LLM 提供商的 API Key。

### K3s ConfigMap 关键环境变量

| 变量名 | 必需 | 默认值 | 说明 |
|--------|------|--------|------|
| `HERMES_UID` | 是 | 10000 | 运行时用户 UID，**必须与 Dockerfile 中预设的 1000 一致** |
| `HERMES_GID` | 是 | 10000 | 运行时用户 GID，**必须与 Dockerfile 中预设的 1000 一致** |
| `HERMES_GATEWAY_BOOTSTRAP_STATE` | 推荐 | — | 设为 `running` 使 Gateway 首次启动即自动运行 |
| `API_SERVER_HOST` | **是** | 127.0.0.1 | **必须设为 0.0.0.0**，否则 K8s probe 和 NodePort 无法访问 |
| `API_SERVER_KEY` | **是** | — | API 认证密钥，**必须设置**，否则 API Server 拒绝启动 |
| `HERMES_DASHBOARD` | 否 | 0 | 设为 `1` 启用 Dashboard |
| `HERMES_DASHBOARD_INSECURE` | 否 | 0 | 设为 `1` 允许 HTTP 访问 Dashboard |
| `GATEWAY_ALLOW_ALL_USERS` | 否 | false | 设为 `true` 允许所有用户访问 Gateway |

### 支持的 LLM 提供商

`instance.sh` 和 `profile.sh` 内置支持以下提供商（未知名称自动映射为 `custom`）：

openai、anthropic、deepseek、openrouter、alibaba、gemini、zai、kimi-coding、minimax、minimax-cn、novita、arcee、gmi、xiaomi、stepfun、huggingface、nvidia、opencode-zen、opencode-go、kilocode、lmstudio、xai、tencent-tokenhub、qwen-oauth、minimax-oauth、ollama-cloud

---

## 附录 D：安全建议

### 已实施的安全措施

1. **容器安全**：
   - 非 root 运行：s6-overlay 将进程切换到 `hermes` 用户（UID 1000）
   - 禁止提权：`security_opt: no-new-privileges:true`
   - 单容器架构：减少攻击面

2. **网络安全**：
   - 最小端口暴露：仅暴露 8642（Gateway）和 9119（Dashboard）
   - 多实例独立网络：使用 `hermes-<id>-net` bridge 网络隔离

3. **数据安全**：
   - API Key 通过 `.env` / Secret 注入，不硬编码
   - `.gitignore` 排除 `.env`、备份文件等敏感文件

4. **资源限制**：
   - CPU 和内存限制防止单实例占用全部资源
   - 日志轮转防止磁盘撑满（max-size: 10m, max-file: 5）

### 建议的额外安全措施

1. **Dashboard 认证**：当前 `HERMES_DASHBOARD_INSECURE=1` 跳过了 OAuth，生产环境建议：
   - 配置 OAuth（设置 `HERMES_DASHBOARD_OAUTH_CLIENT_ID`）
   - 或在前面加 Nginx + Basic Auth

2. **HTTPS**：使用 Nginx 反向代理 + Let's Encrypt 证书

3. **防火墙**：仅开放必要端口，限制访问 IP 来源

4. **定期备份**：配置 cron 自动备份，定期测试恢复流程

5. **监控告警**：配合 Prometheus/Grafana 监控容器状态

6. **API 认证**：生产环境建议启用 `API_SERVER_KEY` 认证和 `api.auth_enabled`

---

## 许可证

本项目基于 [MIT License](LICENSE) 开源。Hermes Agent 由 [Nous Research](https://nousresearch.com) 开发，详见 [GitHub](https://github.com/NousResearch/hermes-agent)。
