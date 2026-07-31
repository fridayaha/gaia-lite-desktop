# Hermes 引擎容器化

## 架构

```
Portal Chat (Vue 3) → Gateway (:8010) → engine-hermes-{id}:8642 → DeepSeek v4 Flash
                         ↑
                  DNS 命名规范路由 (X-Agent-ID 头)
```

## 容器构建

- **基础镜像**: `unionagents/gateway:latest` (python:3.11-slim + FastAPI)
- **运行时**: `hermes-agent` pip 包
- **入口**: `entrypoint.sh` — 自动配置 provider、启动 API server

## entrypoint.sh 流程

```
1. 写入 ~/.hermes/.env (API_SERVER_ENABLED, API_SERVER_KEY 等)
2. 如果 PROVIDER_NAME 有值, 写入 ~/.hermes/config.yaml
3. 透传 OPENAI_API_KEY / ANTHROPIC_API_KEY / DEEPSEEK_API_KEY 等变量
4. 如果 HERMES_PROFILE != "default", 创建该 profile
5. 配置模型 provider/default/base_url/api_key
6. 启动: hermes -p <profile> gateway run
```

注意：**不创建 "default" profile**（它是 Hermes 内置的，无需创建）。

## Provider 配置

引擎 Pod 的环境变量来自 Agent 的 `config.engine` 字段：

```json
{
  "engine": {
    "PROVIDER_NAME": "deepseek",
    "MODEL_NAME": "deepseek-v4-flash",
    "DEEPSEEK_API_KEY": "sk-..."
  }
}
```

通过 Manager API 设置：

```bash
curl -X PUT /api/agents/{id} \
  -d '{"config": {"engine": {"PROVIDER_NAME": "deepseek", ...}}}'
```

## 环境变量

| 变量 | 用途 | 来源 |
|------|------|------|
| `PROVIDER_NAME` | 提供商名称 (deepseek) | Agent config |
| `MODEL_NAME` | 模型名 (deepseek-v4-flash) | Agent config |
| `DEEPSEEK_API_KEY` | DeepSeek API Key | Agent config |
| `PROVIDER_API_KEY` | 通用 API Key | Agent config |
| `PROVIDER_API_BASE` | API Base URL | Agent config |
| `HERMES_PROFILE` | Profile 名称 | 默认 "default" |
| `API_SERVER_KEY` | 引擎 API Key | K8s secret |
| `API_SERVER_ENABLED` | 启用 API Server | 固定 "true" |
| `API_SERVER_HOST` | 监听地址 | 固定 "0.0.0.0" |
| `API_SERVER_PORT` | 监听端口 | 固定 8642 |
| `GATEWAY_ALLOW_ALL_USERS` | 允许所有用户 | 固定 "true" |

## K8sManager 创建 Pod 时的 env 注入

K8sManager 在 `create_agent_engine()` 中设置的环境变量：

```python
V1EnvVar(name="AGENT_ID", value=agent_id),
V1EnvVar(name="API_SERVER_KEY", value_from=secretRef("api-server-key")),
V1EnvVar(name="API_SERVER_ENABLED", value="true"),
V1EnvVar(name="API_SERVER_HOST", value="0.0.0.0"),
V1EnvVar(name="API_SERVER_PORT", value="8642"),
V1EnvVar(name="GATEWAY_ALLOW_ALL_USERS", value="true"),
# + config.engine 中的键值对
```

## 引擎 API

Hermes Agent API Server 暴露 OpenAI 兼容接口：

| 端点 | 说明 |
|------|------|
| `POST /v1/chat/completions` | 聊天补全（支持 stream=true SSE） |
| `GET /v1/models` | 模型列表 |
| `GET /health` | 健康检查 |

## Gateway 集成注意事项

- Gateway 转发请求前**必须去掉 Origin 和 Referer 头**，否则引擎会返回 403
- 引擎 API 通过 `Authorization: Bearer {API_SERVER_KEY}` 认证
- 引擎 Pod 命名规范：`engine-hermes-{agent_id[:8]}`

## 注意事项
- Hermes Agent 使用 profile 隔离多实例配置
- 本地已安装桌面版 Hermes，所有容器化运行必须在 colima/k3s 中进行
