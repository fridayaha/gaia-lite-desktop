# IM 与 Channel 对接架构（V3，基于 develop @ v0.8.19）

> 本文刷新 IM（企业微信/飞书/钉钉）与 Channel 对接的关键流程、架构图、模型定义。
> 代码基线：`develop` 分支 @ `v0.8.19`（含 upstream 合并 + profile 端口 port_map.json 单源真相修复）。
> 配套文档：`docs/architecture-v3.md`（整体 V3 架构）、`docs/architecture-profiles.md`（Profile 隔离）。

---

## 1. 架构总览

```
                        ┌─────────────────────────────────────────────────────┐
                        │                    IM 平台                          │
                        │  企业微信(corp app) / 飞书(app) / 钉钉(robot) / 企微Bot │
                        └───────────┬───────────────────────────┬────────────┘
                  inbound webhook   │   outbound (send/card)     │
                                    ▼                           ▲
┌───────────────────────────────────────────────────────────────────────────┐
│                              Gateway Pod                                  │
│                                                                           │
│  /api/gateway/channel/{type}/{agent_id}/callback  ◀── IM webhook 入口     │
│         │                                                                 │
│         ▼                                                                 │
│  channel/router.py → adapter.verify_signature + parse_incoming           │
│         │                                                                 │
│         ▼                                                                 │
│  channel/dispatcher.py（per-agent 串行队列）                                │
│   Step 0   权限闸门 (_check_im_access)                                    │
│   Step 0.5 语音转录 (adapter.transcribe → asr-sidecar)                    │
│   Step 0.6 卡片按钮点击（专用路径）                                          │
│   Step 1   ensure_engine_ready (DNS 命名规范，不查 Controller)             │
│   Step 2   引擎重启 → 失效 session 缓存                                    │
│   Step 3   "处理中"提示（仅首次启动）                                       │
│   Step 4   _get_or_create_session (30min TTL)                             │
│   Step 5   profile_resolver.resolve → profile_name + engine_url           │
│   Step 6   _forward / _stream_from_engine                                 │
│         │                                                                 │
│         ▼                                                                 │
│  proxy.py  注入 X-Agent-ID / X-Hermes-Profile / X-Hermes-Session-ID       │
│         │                                                                 │
│         ▼                                                                 │
│  http://engine-hermes-{agent_id[:8]}.{ns}.svc.cluster.local:8642          │
│  （Pod 内 nginx 按X-Hermes-Profile头路由到 per-profile 端口 8644+）          │
└───────────────────────────┬───────────────────────────────────────────────┘
                            │
              ┌─────────────▼──────────────┐
              │   Engine Pod (Hermes V2)   │
              │  nginx:8642 → profile port │
              │  port_map.json = 唯一真相   │
              │  per-profile UID 隔离       │
              └────────────────────────────┘

  旁路：wecom_bot（企业微信 AI Bot）= WS 透明桥接，不经 dispatcher，
        profile_ws ↔ openws_url 1:1 透传。
```

**核心约束**（CLAUDE.md）：
- Gateway **不查 Controller/其他服务**取 upstream 地址，纯 DNS 命名规范：`engine-hermes-{agent_id[:8]}.{ns}.svc.cluster.local:8642`（`lifecycle.py:resolve_engine_url`）。
- 转发前**去掉 `Origin`/`Referer` 头**（Hermes 带 Origin 返回 403）。
- SSE 流式**不缓冲**（`proxy_buffering off`，逐 chunk yield）。
- `X-Hermes-Profile` 头由 gateway 服务端计算，**忽略客户端传入**（`base.py:_STRIP_HEADERS`）。

---

## 2. 数据模型定义

模型源：`services/manager/app/models/__init__.py`、`services/gateway/app/channel/models.py`。

### 2a. AgentInstanceChannel（渠道绑定）

`agent_instance_channels` 表（`models/__init__.py:425`）。一个 agent 实例可绑多个渠道。

| 字段 | 类型 | 约束 | 含义 |
|------|------|------|------|
| `id` | UUID | PK | 渠道绑定 ID |
| `instance_id` | UUID | FK→agent_instances.id, CASCADE, NOT NULL, idx | 关联智能体实例 |
| `group_id` | UUID | FK→user_groups.id, CASCADE, NOT NULL, idx | 归属用户组 |
| `channel_type` | String(32) | NOT NULL | `wecom`/`feishu`/`dingtalk`/`http` |
| `scope_type` | String(16) | NOT NULL, default `ALL` | `ALL`/`INDEPENDENT`/`SHARED`/`GROUP`/`USER`/`USER_GROUP`（派生值） |
| `scope_target_id` | UUID | nullable | 作用域目标（user_id / group_id） |
| `profile_type` | String(16) | NOT NULL, default `INDEPENDENT` | `INDEPENDENT`（按用户独立）/ `SHARED`（组内共享） |
| `config` | JSON | NOT NULL, default `{}` | 渠道配置（corp_id/secret、app_id/secret、token 等） |
| `enabled` | Boolean | default True | 是否启用 |
| `callback_url` | String(512) | nullable | 回调 URL（manager 生成） |
| `created_at` / `updated_at` | DateTime(tz) | default utcnow | 时间戳 |

唯一约束：`uq_instance_channel_scope (instance_id, channel_type, scope_type, scope_target_id)`。

### 2b. ImUserBinding（IM 用户绑定）

`im_user_bindings` 表（`models/__init__.py:203`）。IM 平台用户 ID ↔ 平台内部 user_id。

| 字段 | 类型 | 约束 | 含义 |
|------|------|------|------|
| `id` | UUID | PK | |
| `user_id` | UUID | FK→users.id, NOT NULL, idx | UnionAgents 平台用户 ID |
| `channel_type` | String(32) | NOT NULL | `wecom`/`feishu`/`dingtalk` |
| `im_user_id` | String(256) | NOT NULL | IM 平台用户 ID（如企微 FromUserName） |
| `im_user_name` | String(256) | nullable | IM 平台昵称 |
| `created_at` | DateTime(tz) | default utcnow | |

唯一约束：`uq_im_channel_user (channel_type, im_user_id)`。**未绑定的 IM 用户在入站时被权限闸门拒绝**（统一身份模型，避免非 UUID 的 IM 原始 ID 污染 profile）。

### 2c. AgentProfile（Profile）

`agent_profiles` 表（`models/__init__.py:175`）。

| 字段 | 类型 | 约束 | 含义 |
|------|------|------|------|
| `id` | UUID | PK | |
| `instance_id` | UUID | FK→agent_instances.id, NOT NULL | 关联实例 |
| `resource_pool_id` | UUID | FK→resource_pools.id, NOT NULL | 资源池 |
| `deployment_id` | UUID | FK→agent_deployments.id, NOT NULL | 部署记录 |
| `profile_name` | String(256) | NOT NULL | `{agent_id[:8]}-{scope_hash}-{user_id[:8]}` |
| `profile_type` | String(16) | NOT NULL | `INDEPENDENT`/`SHARED` |
| `user_id` | UUID | FK→users.id, nullable | 独立 profile 关联用户 |
| `group_id` | UUID | FK→user_groups.id, NOT NULL | 归属组 |
| `hermes_home` | String(512) | NOT NULL | `/opt/data/profiles/{profile_name}` |
| `internal_port` | Integer | nullable | **port_map.json 镜像**（真实端口在 Pod 内 port_map.json） |
| `is_active` | Boolean | default True | |
| `config_synced_at` | DateTime | nullable | 配置同步时间 |
| `created_at`/`updated_at` | DateTime(tz) | | |

唯一约束：`uq_profile_per_deployment (deployment_id, profile_name)`、`uq_user_profile_per_instance (instance_id, resource_pool_id, user_id)`。

### 2d. AgentDeployment（部署）

`agent_deployments` 表（`models/__init__.py:138`）。

| 字段 | 类型 | 约束 | 含义 |
|------|------|------|------|
| `id` | UUID | PK | |
| `instance_id` | UUID | FK, NOT NULL, idx | 关联实例 |
| `group_id` | UUID | FK, NOT NULL, idx | 归属组 |
| `resource_pool_id` | UUID | FK, nullable | 资源池 |
| `status` | DeploymentStatus | default PENDING | `PENDING`/`DEPLOYING`/`RUNNING`/`SUSPENDED`/`FAILED`/`ARCHIVED` |
| `scope_type` | String(16) | NOT NULL, default ALL | 作用域 |
| `scope_target_id` | UUID | nullable | |
| `pod_name` | String(256) | nullable | Pod 名 |
| `namespace` | String(128) | default `unionagents` | |
| `engine_url` | String(512) | nullable | 引擎 URL（外部 Dify 实例时用） |
| `internal_port_map` | JSON | default `{}` | **镜像 `{"profiles": {name: port}}`**（无 next_port，真相在 Pod 内 port_map.json） |
| `deployed_at`/`last_active_at`/`backup_at`/`archived_at` | DateTime | nullable | 生命周期时间戳 |
| `archive_path` | String(1024) | nullable | 归档路径 |
| `node_name` | String(256) | nullable | 节点名 |
| `error_message` | Text | nullable | |

唯一约束：`uq_agent_deployment_scope (instance_id, scope_type, scope_target_id)`。1 实例 : 1 部署（UNIQUE）。

### 2e. AgentInstance / AgentDefinition（摘要）

- **AgentInstance**（`models/__init__.py:382`）：`id, group_id, name, definition_id, version_id, resource_pool_id, status(DRAFT/PUBLISHED/OFFLINE), litellm_config, created_by, created_at, updated_at, published_at`。
- **AgentDefinition**（`models/__init__.py:300`）：`id, group_id, name, description, avatar_color, engine_type(HERMES/OPENCLAW/DIFY), status, current_version_id, marketplace_status, persona_config, model_config, skill_config, memory_config, created_by, ...`。

### 2f. MessageEvent（dispatcher 内部消息结构）

`services/gateway/app/channel/models.py`。dispatcher 处理的统一消息对象：

| 字段 | 含义 |
|------|------|
| `message_type` | TEXT/VOICE/IMAGE/CARD_ACTION（`MessageType` 枚举） |
| `agent_id` | 目标智能体实例 ID |
| `channel_type` | 入站渠道 |
| `chat_id` | IM 会话 ID（企微群/飞书 chat/钉钉 conversationId） |
| `user_id` | **IM 平台原始用户 ID**（未转换，dispatcher 内用；profile_resolver 再转内部 UUID） |
| `user_name` | IM 昵称 |
| `platform_message_id` | 平台消息 ID（去重用） |
| `media_urls` | 媒体 URL 列表（语音/图片） |
| `raw_message` | 原始回调 payload |
| `text` | 文本内容（语音转录后回填） |

### 2g. ER 关系图

```
user_groups 1───∗ agent_instances 1───∗ agent_instance_channels
                       │                       │ (channel_type/scope/profile_type)
                       │ 1
                       │
                       1───1 agent_deployments 1───∗ agent_profiles
                       │       │  (internal_port_map     (profile_name, internal_port,
                       │       │   = port_map.json镜像)    deployment_id, user_id)
                       │
users 1───∗ im_user_bindings         agent_profiles.profile_name 含 user_id[:8]
  (channel_type, im_user_id)         → 与 im_user_bindings 解析出的内部 user_id 关联
```

**引用链（企微为例）**：企微 FromUserName → `im_user_bindings(channel_type=wecom, im_user_id=FromUserName)` → 内部 `user_id` → `agent_instance_channels(profile_type)` 派生 scope → `_build_profile_name(agent_id, scope, user_id)` → `agent_profiles.profile_name` → `agent_deployments.internal_port_map.profiles[name]` → Pod 内 nginx 路由到 profile 端口。

---

## 3. 枚举值

| 枚举 | 取值 | 定义位置 |
|------|------|----------|
| `ChannelType` | `wecom` / `feishu` / `dingtalk`（`http` 为终端门户直连，不经 webhook） | `models/__init__.py:24` |
| `EngineType` | `HERMES` / `OPENCLAW` / `DIFY` | `models/__init__.py:30` |
| `ProfileType` | `INDEPENDENT` / `SHARED` | `models/__init__.py:51` |
| `DeploymentStatus` | `PENDING`/`DEPLOYING`/`RUNNING`/`SUSPENDED`/`FAILED`/`ARCHIVED` | `models/__init__.py` |
| `MessageType` | `TEXT`/`VOICE`/`IMAGE`/`CARD_ACTION` | `channel/models.py` |
| scope_type（派生） | `INDEPENDENT` → `USER`；`SHARED` → `USER_GROUP` | `profile_resolver._derive_scope` |

---

## 4. 入站流程（IM → 引擎）

### 4a. Webhook 入口

`services/gateway/app/channel/router.py`：

```
POST /api/gateway/channel/{channel_type}/{agent_id}/callback   # IM 消息回调
GET  /api/gateway/channel/{channel_type}/{agent_id}/callback   # URL 验证（企微/钉钉）
POST /api/gateway/channel/{channel_type}/{agent_id}/menu       # 自建应用菜单（wecom）
```

router 处理：`adapter.verify_signature` → `adapter.handle_verification`（URL 校验直接返回）→ `adapter.parse_incoming` → `dispatcher.dispatch(event)`。

### 4b. Dispatcher 处理序列（`dispatcher.py:_process_one`）

| 步骤 | 逻辑 | 文件:行 |
|------|------|---------|
| 去重 | `(agent_id, platform_message_id)` 去重，TTL 清理；per-agent 串行队列 | `dispatcher.py:64` |
| Step 0 权限闸门 | `_check_im_access`：IM 用户绑定 + channel 存在性 + 访问权限；未绑定回 IM 提示终止 | `dispatcher.py:126` |
| Step 0.5 语音转录 | VOICE → `adapter.transcribe(event)`（调 asr-sidecar faster-whisper，gateway Pod sidecar localhost:9100）→ 回填 `event.text` | `dispatcher.py:131` |
| Step 0.6 卡片按钮 | CARD_ACTION 走专用路径（合成消息转发，回复按卡片更新） | `dispatcher.py:146` |
| Step 1 引擎就绪 | `ensure_engine_ready(agent_id)`：按 DNS 命名规范定位 Pod，必要时唤醒（SUSPEND→RUNNING） | `dispatcher.py:152` |
| Step 2 session 失效 | 引擎刚启动 → 清该 agent 所有 session 缓存 | `dispatcher.py:160` |
| Step 3 处理中提示 | 仅引擎首次启动时发"处理中"IM 提示 | `dispatcher.py:164` |
| Step 4 session | `_get_or_create_session`：`{channel_type:chat_id → session_id}`，30min TTL | `dispatcher.py:170` |
| Step 5 profile 解析 | `profile_resolver.resolve(user_id, agent_id, channel_type)` | `dispatcher.py:172` |
| Step 6 转发 | `_forward_message`（同步）或 `_stream_from_engine`（SSE 流式） | `dispatcher.py:631+` |

### 4c. Profile 解析（`profile_resolver.py:resolve`）

1. **IM 用户 ID → 内部 UUID**（`_resolve_im_user`，`profile_resolver.py:320`）：查 `im_user_bindings`，未绑定抛 `NotBound`（被 Step 0 权限闸门拦截，但 resolve 兜底再查）。`http` 渠道 user_id 已是 UUID，跳过。
2. **Agent 存在性 + 用户访问权限**（`_check_access`）。
3. **Channel 匹配**（`_match_channel`）：按 agent_id + channel_type 查 `agent_instance_channels`。
4. **Scope 派生**（`_derive_scope`，`profile_resolver.py:391`）：
   - `profile_type == INDEPENDENT` → `(scope_type=USER, scope_target_id=user_id, INDEPENDENT)`
   - 否则（SHARED）→ `(scope_type=USER_GROUP, scope_target_id=group_id, SHARED)`
   - 模型默认 `profile_type=INDEPENDENT`，故默认按用户独立 profile。
5. **构造 profile_name**（`_build_profile_name`）：`{agent_id[:8]}-{scope_hash(scope_type, scope_target_id)}-{user_id[:8]}`。
6. **Deployment 查询**（`_get_deployment`）：agent_id → 唯一 deployment。
7. **Ensure profile 记录** + **确保 Pod 上 profile 就绪**：
   - 读 `deployment.internal_port_map.profiles[profile_name]` 作 `cached_port`（fast path，跳过 controller ensure）。
   - 无 cached_port 或 502 自愈标记 → 调 Controller `/api/controller/profiles/ensure`（`profile_resolver._call_controller_ensure_profile`）分配端口 + 启 gateway + 更新 nginx。
8. **构造 engine_url**：`http://{pod_name}.{ns}.svc.cluster.local:8642`（Pod 内 nginx，按 `X-Hermes-Profile` 头路由到 per-profile 端口）。
9. 60s 正缓存 + 10s 负缓存 + pod_name 变化检测（Pod 重启缓存失效）。

### 4d. 转发到引擎（`proxy.py`）

- `base_url = target.engine_url`（Pod:8642 nginx）。
- 注入头：`X-Agent-ID`（agent_id）、`X-Hermes-Profile`（profile_name，服务端计算，客户端传入被剥离）、`X-Hermes-Session-ID`。
- 去头：`Origin`/`Referer`/hop-by-hop/客户端 `x-hermes-profile`。
- POST `/v1/chat/completions`（同步）或 `/v1/runs`（异步 run，SSE）。

---

## 5. 出站流程（引擎 → IM）

### 5a. 引擎 SSE 接收（`dispatcher._stream_from_engine`）

- `POST /v1/chat/completions` with `stream=True`。
- 逐行解析 `data: {"choices":[{"delta":{"content":"..."}}]}`，**不缓冲**直接 yield 给 adapter。

### 5b. Channel Adapter 差异

| Adapter | 注册 | 流式回执 | 卡片 | 编辑 | 语音 | 文件 |
|---------|------|----------|------|------|------|------|
| **wecom** (`wecom.py:109`) | `@register("wecom")` | chunk 累积满 2048 字节发新消息；卡片保护不 chunk | `send_card_message`（`card_utils.extract_card_json` 括号配平容错提取） | `update_template_card` | `transcribe`→asr-sidecar | `send_message` |
| **feishu** (`feishu.py:33`) | `@register("feishu")` | `send_streaming_update`→`update_streaming_card` | 卡片 | `edit_message`/`_patch_card` | `transcribe` | `send_message` |
| **dingtalk** (`dingtalk.py:32`) | `@register("dingtalk")` | 不支持流式/编辑，分段 `send_markdown` | 无 | 不支持 | `transcribe` | `send_message` |
| **wecom_bot** (`wecom_bot.py`) | 不注册（独立 WS 桥） | WS 透传 | — | — | — | — |

**wecom_bot** 是企业微信 AI Bot 的 WS 透明桥接（`bridge_bot_ws`），`profile_ws ↔ openws_url` 1:1 透传，**不经 dispatcher**，与上面三个注册 adapter 是不同链路。

### 5c. 卡片 JSON 容错提取（企微）

`channel/card_utils.py:extract_card_json`：引擎回复"文本 + 卡片 JSON"共存时，用括号配平算法提取卡片 JSON 部分，文本部分单独发送。仅企微用。修过 `send_card_message` bool/dict 降级 bug（见记忆 `wecom-card-json-extraction`）。

### 5d. 同步 vs 异步 run

| 特性 | `POST /v1/chat/completions`（同步） | `POST /v1/runs`（异步 run） |
|------|-------------------------------------|------------------------------|
| 响应 | 等完整响应 | 流式 SSE |
| 处理中提示 | 无 | 状态卡（飞书/企微） |
| 错误处理 | 直接返回错误文本 | 错误替换状态卡 |
| Session | 一次性 | 状态保持，`run_id` 绑定 trace |
| Langfuse trace | input→output 同请求 | trace + generation，`run_id` 绑定 |

---

## 6. Manager 侧 Channel 管理 API

`services/manager/app/api/agent_instances.py:584+`：

| Method | Path | 用途 |
|--------|------|------|
| GET | `/api/manager/agent-instances/{instance_id}/channels` | 列渠道 |
| POST | `/api/manager/agent-instances/{instance_id}/channels` | 创建渠道（生成 callback_url） |
| PUT | `/api/manager/agent-instances/{instance_id}/channels/{channel_id}` | 更新 |
| DELETE | `/api/manager/agent-instances/{instance_id}/channels/{channel_id}` | 删除 |

`callback_url` 生成：`{callback_base_url}/api/gateway/channel/{channel_type}/{instance_id}/callback`。各渠道 token 由 config 里的凭据换取（企微 access_token 7200s / 飞书 tenant_access_token / 钉钉 accessToken）。

---

## 7. Profile 端口路由（v0.8.19 单源真相）

> 这是 v0.8.19 修复串号 bug 的关键改动，直接影响 channel→profile 路由正确性。

**路由模型**：gateway 连 Pod 内 nginx(8642) + 发 `X-Hermes-Profile` 头；nginx `map` 块按头路由到 per-profile 端口（8644+）。**DB `internal_port` 不参与 TCP 路由**，只作 gateway 是否调 controller ensure 的缓存标志。

**唯一真相**：Pod 内 `/opt/data/port_map.json`（PVC 持久化），由 `engines/hermes/port_map.py`（flock + os.replace 原子写，扫描法 alloc 无 next_port）管理。entrypoint-v2.sh 重启时 `reconcile` 从 PVC 目录重建；manager 经 k8s exec 调 `port_map.py alloc/get/remove/all`。

**核心保证**：每个 profile 唯一端口 ⇒ gateway 启动失败仅 502，**绝不串到别的 profile**（旧 bug：多 profile .env 缺端口 → 塌缩 8644 → 串号）。

DB `internal_port_map` 降为镜像 `{"profiles": {name: port}}`（无 next_port），`agent_profiles.internal_port` 镜像 port_map.json 端口值。详见 `docs/`（记忆 `profile-port-single-source`）。

---

## 8. 关键文件索引

| 关注点 | 文件 |
|--------|------|
| Channel webhook 路由 | `services/gateway/app/channel/router.py` |
| 消息调度（去重/队列/步骤） | `services/gateway/app/channel/dispatcher.py` |
| Adapter 注册表 | `services/gateway/app/channel/registry.py` |
| Adapter 基类 | `services/gateway/app/channel/base.py` |
| 企微/飞书/钉钉/企微Bot | `channel/wecom.py` / `feishu.py` / `dingtalk.py` / `wecom_bot.py` |
| 卡片 JSON 容错 | `channel/card_utils.py` |
| Profile 解析 + IM 用户绑定 | `services/gateway/app/profile_resolver.py` |
| 引擎转发 + 头注入 | `services/gateway/app/proxy.py` |
| 引擎就绪 + DNS 解析 | `services/gateway/app/lifecycle.py` |
| MessageEvent 模型 | `services/gateway/app/channel/models.py` |
| DB 模型 | `services/manager/app/models/__init__.py` |
| Channel 管理 API | `services/manager/app/api/agent_instances.py:584+` |
| 端口单源真相 | `engines/hermes/port_map.py`、`engines/hermes/entrypoint-v2.sh` |
