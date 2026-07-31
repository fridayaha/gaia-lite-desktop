# 企业 IM 渠道集成

## 概述

AgentGateway 支持通过 Webhook 方式接入企业微信、飞书、钉钉等企业 IM 平台。用户可以在移动办公软件中直接与智能体对话，无需打开 Web 门户。

## 架构

```
IM 用户发消息 → IM 平台 → POST Webhook → nginx-ingress
                                              ↓
                                     Gateway (services/gateway)
                                              ↓
                                ┌──────────────────────────┐
                                │ 1. Router: 校验签名     │
                                │ 2. Router: Challenge 验证 │
                                │ 3. Router: parse_incoming │
                                │ 4. 返回 200 ACK（立即）   │
                                │ 5. Dispatcher (异步):     │
                                │    a. 消息去重             │
                                │    b. 引擎唤醒 (冷/热)    │
                                │    c. 创建 Session        │
                                │    d. SSE 流式转发         │
                                │    e. IM 回复              │
                                └──────────────────────────┘
                                              ↓
                                       Hermes Engine Pod
```

核心设计：
- **异步处理**：Webhook 校验后立即返回 200，处理流程在后台进行
- **动态 Pod 生命周期**：Engine Pod 非常驻，空闲自动休眠。Gateway 收到消息时自动调 Controller 唤醒
- **冷启动**：引擎需启动时（20-60s），发送两条独立消息：启动状态卡 + AI 回复卡
- **热启动**：引擎已在运行，直接发 AI 回复卡，用户无感知

## 配置

### 数据库

`agent_channels` 表存储每个 Agent 的 IM 渠道配置：

| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | 主键 |
| agent_id | UUID(FK) | 关联的智能体 |
| channel_type | string | `wecom` / `feishu` / `dingtalk` |
| config | JSON | 平台凭据 |
| enabled | bool | 是否启用 |
| callback_url | string | 自动生成的回调地址 |

唯一约束：`(agent_id, channel_type)`

### API

```
GET    /api/manager/agents/{agent_id}/channels        → 列表
POST   /api/manager/agents/{agent_id}/channels        → 创建
PUT    /api/manager/agents/{agent_id}/channels/{id}   → 更新
DELETE /api/manager/agents/{agent_id}/channels/{id}   → 删除
```

### Admin 前端

在 Agent 详情页新增「渠道」Tab，支持：
- 查看已配置的渠道列表
- 新建/编辑渠道（根据平台类型显示不同的配置表单）
- 启用/禁用切换
- 删除确认
- Copy Callback URL

## 各平台适配器

### 企业微信 (wecom)

| 元素 | 说明 |
|------|------|
| 签名 | SHA1 (`msg_signature` query 参数) |
| URL 验证 | `GET /callback` → 解密 echostr 返回 |
| 消息接收 | `POST` → 加密 XML Body，AES-256-CBC 解密 |
| 消息发送 | `POST /cgi-bin/message/send?access_token=...` |
| 消息格式 | Text / Markdown |
| 消息编辑 | ❌ 不支持 |
| 流式输出 | ❌ 不支持，每次发送独立消息 |

**处理中提示策略：** 企微不支持编辑，先发"启动中"消息，再追加新消息作为 AI 回复。

### 飞书 (feishu)

| 元素 | 说明 |
|------|------|
| 签名 | HMAC-SHA256 (`X-Lark-Signature` header) |
| Challenge | `POST` → 返回 `{"challenge": "xxx"}` |
| 消息接收 | `POST` → JSON Body，支持 AES 加密事件 |
| 消息发送 | `POST /open-apis/im/v1/messages` (新消息) / `{id}/reply` |
| 消息编辑 | ✅ `PATCH /open-apis/im/v1/messages/{id}` (仅支持卡片消息) |
| 消息格式 | Markdown / 富文本 / 交互式卡片 |
| 流式输出 | ✅ 卡片 PATCH 流式 |

**流式输出策略：** 使用交互式卡片（`msg_type=interactive`）实现流式输出。

冷启动流程：
1. **卡片1（启动卡）**：`🤖 正在启动...` → 引擎就绪后更新为 `✅ 引擎已就绪`
2. **卡片2（回复卡）**：发送新卡片承载 AI 回复，PATCH 流式更新 → 最终清理

热启动流程（引擎已运行）：
- 仅发送**回复卡**，无启动卡，直接流式更新

### 钉钉 (dingtalk)

| 元素 | 说明 |
|------|------|
| 签名 | HMAC-SHA256 (`sign` header, `timestamp + "\n"`) |
| Challenge | `POST` checkUrl 事件 → 返回 `{"message": "success"}` |
| 消息接收 | `POST` → JSON Body |
| 消息发送 | `POST /v1.0/im/messages/send` (OAuth 2.0 token) |
| 消息格式 | Text / Markdown |
| 消息编辑 | ❌ 不支持 |
| 流式输出 | ❌ 不支持

## Channel Adapter 接口

新增渠道只需实现 `BaseChannelAdapter` 抽象基类并用 `@register` 注册：

```python
from app.channel.base import BaseChannelAdapter
from app.channel.registry import register

@register("my_platform")
class MyPlatformAdapter(BaseChannelAdapter):
    channel_type = "my_platform"

    async def verify_signature(self, request) -> bool: ...
    async def parse_incoming(self, request) -> list[MessageEvent]: ...
    async def send_message(self, chat_id, text, reply_to="") -> bool: ...
```

可选方法（用于优化用户体验）：
- `send_processing(chat_id) → msg_id` — 发送"处理中"提示 (卡片)
- `send_processing_done(chat_id, msg_id) → bool` — 更新启动卡为"已完成"
- `send_initial_response(chat_id, text) → msg_id` — 发送新卡片承载 AI 回复
- `send_streaming_update(chat_id, msg_id, text, show_status=False) → bool` — 流式更新
- `replace_with_response(chat_id, msg_id, response)` — 替换为正式回复
- `replace_with_error(chat_id, msg_id, error_msg)` — 替换为错误信息
- `handle_verification(request)` — 处理平台 Challenge/URL 验证
- `verify_url(request) → Response` — 处理 GET URL 验证（通用化）

## 生命周期管理

当 Webhook 到达时，Engine Pod 可能处于不同状态。Gateway 自动处理：

| 引擎状态 | 用户体验 |
|---------|---------|
| RUNNING | 直接转发，用户无感知等待 |
| SUSPENDED | 看到"启动中" → 等待后收到回复 |
| 从未部署 | 看到"启动中" → 等待后收到回复 |
| FAILED | 看到"启动失败"错误消息 |

Gateway 调用 Controller `POST /api/controller/agents/{id}/deploy` 进行部署管理。

## 启动提示优化

**冷启动 vs 热启动分离。** 引擎未运行时发送两条独立消息（启动卡 + 回复卡），引擎已运行则仅发回复卡。

```python
# dispatcher.py 中的执行路径
ready, was_already_running = await ensure_engine_ready(agent_id)

if not was_already_running:
    # 冷启动：发送启动状态卡
    processing_msg_id = await adapter.send_processing(chat_id)
    # ... 引擎就绪后 ...
    # 启动卡更新为 "✅ 引擎已就绪"
    # 另发一条新消息作为回复卡（流式更新）

if was_already_running:
    # 热启动：直接发回复卡，无启动卡
```

## E2E 测试经验

### 2026-06-10 E2E 验证（飞书）

完整全链路验证通过，关键发现：

1. **飞书 Challenge 必须在 Gateway 启动完成后才能保存** — 配置回调 URL 时飞书会发 Challenge 请求，Gateway 必须处于运行状态
2. **multi-service 数据一致性** — 本地测试 Gateway + Controller 必须使用同一数据库，否则 Controller 找不到 Agent
3. **飞书 API msg_type 必须** — 回复/发送消息时 body 必须同时包含 `msg_type` 和 `content`，缺 `msg_type` 返回 99992402
4. **k3s colima 本地开发环境** — 用 `colima` context 操作本地 k3s，用 `docker build` + `kubectl set image` 快速迭代
5. **引擎配置在 Agent 的 `config` JSON 中** — 需配置 `model_providers` 数组，否则 Hermes 返回 "No inference provider configured"

### 本地 E2E 测试环境搭建

```bash
# 1. 确认本地 k3s
kubectl config use-context colima
kubectl get pods -n unionagents  # postgres, controller, gateway 等应在运行

# 2. 构建 Gateway 镜像并部署
docker build -t gateway:channel-v1 -f services/gateway/Dockerfile .
kubectl set image deployment/gateway -n unionagents gateway=gateway:channel-v1

# 3. 创建渠道配置（直接写入 k3s PG）
kubectl exec pod/postgres-0 -n unionagents -- psql -U unionagents \
  -c "INSERT INTO agent_channels ..."

# 4. 启动 ngrok 隧道
ngrok http 8010

# 5. 在飞书开发者后台配置回调 URL
```

## 相关代码

| 路径 | 说明 |
|------|------|
| `services/gateway/app/proxy.py` | 原有反向代理逻辑 |
| `services/gateway/app/lifecycle.py` | 引擎生命周期管理 |
| `services/gateway/app/channel/base.py` | 渠道适配器基类 |
| `services/gateway/app/channel/models.py` | 消息模型 |
| `services/gateway/app/channel/registry.py` | 适配器注册表 |
| `services/gateway/app/channel/dispatcher.py` | 消息调度器 |
| `services/gateway/app/channel/router.py` | Webhook 路由 |
| `services/gateway/app/channel/wecom.py` | 企业微信适配器 |
| `services/gateway/app/channel/feishu.py` | 飞书适配器（卡片流式） |
| `services/gateway/app/channel/dingtalk.py` | 钉钉适配器 |
| `services/gateway/app/lifecycle.py` | 引擎健康检查 / 部署 |
| `services/manager/app/models/__init__.py` | AgentChannel 模型 |
| `services/manager/app/api/channels.py` | 渠道配置 API |
| `services/manager/app/services/channel_service.py` | 渠道 CRUD 服务 |
| `apps/admin/src/views/agents/detail/ChannelsTab.vue` | 渠道配置界面 |
| `services/gateway/Dockerfile` | 需包含 sqlalchemy[asyncio] asyncpg cryptography 依赖 |
