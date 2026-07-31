# B 团队任务书 — 网关、渠道、Dify、hub

> 强依赖 A 的 `01-接口契约.md`。A 落地前可按契约 mock 并行开工。
> 工作目录：Repo1 `develop` 分支。

## 职责边界
- gateway chat 代理（**Repo1 adapter 机制**：Hermes/Dify/OpenClaw 三引擎协议归一化）
- SSE 流式 + IM 三渠道（feishu/wecom/dingtalk）+ 权限闸门 + 健康检查降级
- **hub 服务整体迁入 + 接入**（K8s/CI/nginx/RBAC 对齐）

不做：manager 三层业务（A）、前端（C）。hub 业务代码来自 Repo1，B 负责迁入与平台接入。

## 任务清单

### B1. adapter 机制落地 gateway（W2，核心）
- 移植 Repo1 `services/controller/app/controller/adapters/{base,registry,hermes}.py` → `services/gateway/app/adapter/`。
- Repo1 adapter 主流程（`services/controller/app/main.py` 的 `/v1/chat/completions`、`/v1/models`、`/v1/sessions` CRUD、`/v1/sessions/{id}/messages`、`/v1/files`、catch-all）迁入 gateway，替换 Repo2 gateway 写死的 OpenAI 假设代理。
- 新增 `OpenClawAdapter`（OpenAI 兼容，参考 HermesAdapter）。
- `register_adapter("HERMES"|"OPENCLAW"|"DIFY", ...)`；运行时按 `X-Engine-Type` 选 adapter。
- **验收**：三引擎 chat 请求按 engine_type 走对应 adapter；catch-all 兜底代理通。

### B2. DifyAdapter 完整移植（W2）
- Repo1 `adapters/dify.py` 直接迁。含：
  - 路径映射 `_PATH_MAP`：`chat/completions`→`chat-messages`、`sessions`→`conversations`、`models`→`parameters`
  - 请求体转换：OpenAI messages → Dify `{inputs,query,response_mode,conversation_id,user}`（取最后一条 user 消息）
  - SSE 转换 `_transform_dify_sse_to_openai`：`event:message/message_end`→OpenAI `chat.completion.chunk`+[DONE]
  - session URL 钩子（conversations API）+ DNS `engine-dify-{short_id}.{ns}:8080`
- **验收**：Dify 实例发消息，OpenAI↔Dify 请求体/SSE 转换正确，前端无感知差异。

### B3. gateway 路由/鉴权/缓存保留（W2）
- Repo2 F-GW-001~004：Profile 感知路由 + DNS 路由 + Origin/Referer 过滤 + 安全头。
- 与 adapter 协同分工：adapter 负责协议转换，gateway 负责路由解析/鉴权/缓存（60s 正 + 10s 负）。
- `transform_headers` 必须去 Origin/Referer + 注入 `X-Hermes-Profile` + `authorization: Bearer {api_server_key}`（Hermes 收 Origin 返 403）。
- **验收**：浏览器 SSE 不再 `Failed to fetch`；Profile 路由 6 层问题链路不回归。

### B4. SSE 流式 + IM 分段（W2~3）
- F-GW-010~012：`proxy_buffering off`；企业微信 2048 字节 chunk-flush（`_split_by_bytes` UTF-8 字节级分段）；飞书 PATCH 卡片流式编辑。
- **验收**：企微长回复正确分段不切多字节字符；飞书卡片实时编辑无残留。

### B5. IM 渠道分发 + 权限闸门（W3）
- F-GW-020~024：统一消息分发器（去重 60s TTL + per-agent 队列 + session 30min TTL）；权限闸门 `check_access()`（NotBound/AccessDenied/ProfileNotFound，不可吞 AccessDenied 当 V1 fallback）。
- 三适配器：飞书(AES-256-CBC + HMAC + 卡片)、企微(SHA1 + AES + 2048 分段)、钉钉(checkUrl + HMAC + OAuth)。
- 合并 Repo1 `channel-gateway` 的 WeCom 细化（若有 Repo1 独有打磨）。
- **验收**：三渠道回调→权限校验→流式回复端到端通；越权返回明确 IM 提示。

### B6. 健康检查/降级/启动 UX（W3）
- F-GW-030~032：`check_engine_health()` / `ensure_engine_ready()`(最长 300s 轮询) / 冷启动 "🤖 正在启动..." 占位 / 指数退避 3 次 [1s,2s,4s] / Profile 创建失败降级 V1。
- **验收**：冷启动有占位提示；引擎异常自动恢复或友好降级。

### B7. 会话/模型代理/DB 缓存（W3）
- F-GW-040,041,050：模型 API 代理（`agent_instances.litellm_config`）；会话确定性 ID `SHA256(agent_id+channel_type+chat_id)[:24]`；DB 配置缓存 60s + 主动失效。
- **验收**：会话跨消息连续；渠道配置变更后缓存正确失效。

### B8. hub 服务接入（W3~4）
- Repo1 `services/hub/` 整体迁入 develop（含 backend + frontend + Dockerfile）。
- 补齐接入项（`/api/hub/health` **已存在**，`api/health.py` 返回 `{"status":"ok"}`，无需补；SERVICE.md 描述过时）：
  - K8s manifests（`deploy/k8s/services/hub.*`）
  - 接入根目录构建（Makefile/package.json）
  - nginx `/api/hub/` → hub:8003 路由
  - 统一 CI
- 与 manager RBAC/UserGroup 对齐：hub_item.group_id 组隔离；JWT 复用 manager 签发（或独立校验，二选一，W1 与 A 对齐）。
- **验收**：hub 独立可启；`/api/hub/health` 200；导入→审批→安全扫描→发布→发现 全链路通；组隔离。

## 交付物
- gateway adapter 机制 + 三引擎代理 + IM 三渠道 + hub 接入
- hub K8s/CI/nginx/health 补齐

## 关键依赖文件
- Repo1 adapter（迁 B1/B2）：`services/controller/app/controller/adapters/{base,registry,hermes,dify}.py`
- Repo1 controller 主流程（迁 B1 代理路由）：`services/controller/app/main.py`
- Repo1 controller session_store（迁 B7 兜底）：`services/controller/app/controller/session_store.py`
- Repo2 gateway（基座）：`services/gateway/app/{router,channel,engine_client,middleware}/`
- Repo1 hub（迁 B8）：`services/hub/`（`backend/app/models/*.py`、`SERVICE.md`）
- 契约：`docs/merge/01-接口契约.md` §4(gateway)、§5(hub)
