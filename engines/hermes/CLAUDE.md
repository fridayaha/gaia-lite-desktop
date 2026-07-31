# Hermes Engine (V2 架构)

## 运行模式

V2 镜像基于 **`python:3.11-slim` + `pip install hermes-agent`**（对齐 main 分支，不带 70+ 预置 skills）+ nginx 多 Profile 路由。
不再用第三方预构建镜像 `nousresearch/hermes-agent`（3.79GB，打包 70+ 预置 skills + s6/Node/Playwright）；不再自建 `orchestrator.py`（V1 已删），改用 `entrypoint-v2.sh` + nginx。
hermes-agent pin `==0.19.0`（2026.7.20 Quicksilver Release）；`HERMES_DISABLE_LAZY_INSTALLS=1` 禁运行时 lazy-install；平台技能由 manager fan-out 到 `/opt/data/profiles/{name}/skills/`，不依赖镜像预置 skills。

## 多 Profile 隔离机制

### V2 隔离方式（当前）

- **目录隔离**：每个 Profile 独占 `$HERMES_DATA/profiles/{profile_name}/` 子目录
- **PVC 挂载**：`HERMES_DATA` 挂在 PVC 上，Profile 子目录由 `hermes profile create` 创建
- **nginx 路由**：gateway 按 `X-Hermes-Profile` 头路由到对应 Profile 的端口（8644+）
- **per-profile UID 隔离**：每个 Profile 分配独立 Linux UID（20000-29999），目录
  `chown -R {uid}:{uid}` + `chmod 0700`，gateway 子进程经 `profile_isolation.py`
  以 `preexec_fn=os.setgid/os.setuid` 降权到该 UID 运行（非 root、非共享 hermes uid）。
  即使引擎进程被攻破，OS 文件权限阻止跨 Profile 读文件。

### per-profile UID 隔离实现（V2，移植自 main）

`profile_isolation.py`（从 main 分支 `orchestrator.py` 的 `234177a`/`a97905c` 移植算法）
由 `entrypoint-v2.sh`（Pod 启动恢复）与 manager（`_do_create_profile`/`ensure`/`delete`）
经 k8s exec 调用：

- `launch <name> <dir> <port>`：以目录属主 UID 为 truth 分配/恢复用户（`useradd -r -M -u`）→
  `chown -R {uid}:{uid}` + `chmod 0700` → 加固 `secrets.enc` 回 `root:root 0640`
  （sidecar 安全模型：gateway 读不到、sidecar root 可读）→
  `Popen(['hermes','gateway','run','--replace'], preexec_fn=_drop_privs)` 降权启动。
- `cleanup <name>`：`userdel` 清理 Linux 用户。
- UID 真相源 = 目录属主（PVC 持久，跨容器重启 `/etc/passwd` 丢失后据此重建用户）。

**为何"第三方镜像无法动态建多 UID 并 su"不再成立**（spike 验证 2026-07-01，节点 101.96.216.141）：
建 UID 用 `useradd`（第三方镜像运行时可用，`/etc/passwd` 在容器可写层）；降权用
`os.setuid`（`preexec_fn`，直接 syscall，非 `su`，无 PAM 依赖，root 进程可切任意 UID）。
唯一前提是 `Dockerfile` 构建期 `chmod -R o+rX /opt/hermes/...`（让非 root UID 能
read/execute hermes）+ `HOME=profile_dir`（hermes 写 `~/.hermes` 到 profile 目录）+
config 模板 `kanban.dispatch_in_gateway: false`（kanban lock 写共享 root 目录，非 root 写不了）。

### 等价性评估

| 隔离维度 | V1 per-profile uid | V2 per-profile uid（当前） |
|---|---|---|
| 文件读隔离 | ✅ uid 不同，OS 强制 | ✅ uid 不同，OS 强制 |
| 进程隔离 | ✅ 不同 uid 进程 | ✅ 不同 uid 进程 |
| 数据分离 | ✅ | ✅ 目录 + PVC |
| 实现位置 | orchestrator.py（V1 进程模型） | profile_isolation.py + entrypoint-v2.sh + manager |

V2 现已与 V1 等价的强隔离（per-profile UID + 0700）。进一步加固（容器级 runAsNonRoot、
优雅 SIGTERM 转发、旧 profile 迁移）见 `~/.claude/plans/resilient-hatching-badger.md` 跟进项。

## 相关文件

- `entrypoint-v2.sh` — V2 启动脚本（base profile 初始化 + PVC 恢复 + nginx 路由表生成；启动时调 `profile_isolation.py launch` 恢复各 profile gateway 并降权）
- `profile_isolation.py` — per-profile UID 隔离脚本（`launch`/`cleanup` CLI，移植自 main `orchestrator.py`）
- `Dockerfile` — 基于 `python:3.11-slim` + `pip install hermes-agent==0.19.0`（不带 70+ 预置 skills）+ nginx + passwd（useradd）+ `HERMES_DISABLE_LAZY_INSTALLS=1` + 拷贝 entrypoint + profile_isolation.py
- `config/config.yaml.tmpl` — profile config 模板（含 `kanban.dispatch_in_gateway: false`，per-profile UID 下关闭 kanban）
- `deploy/k8s/engines/hermes-template.yaml` — Pod 模板（PVC + nginx sidecar）
- `hermes-deploy/` — 独立部署脚本套件（k8s/docker-compose/s6）

## 与 Manager 的契约

- Manager 创建 Pod 时注入 `X-Hermes-Profile` 头（profile_resolver 计算）
- Pod DNS：`engine-hermes-{agent_id[:8]}.{ns}.svc.cluster.local:8642`
- Profile 名：`sha256("USER:{user_uuid}")[:6]`（INDEPENDENT）或 `shared-{instance_id[:8]}`（SHARED）
- Gateway `profile_resolver` 有 60s 正缓存 + 10s 负缓存，避免每请求查 DB

## Langfuse trace 归属

**Gateway + Hermes 双写 trace**，通过 `session_id` + `last_user_message_hash` 软关联。

### 双写模式

- **Gateway 写外层 trace**：`trace_chat()` / `trace_run_start()` 在收到请求时创建 trace，
  metadata 含 `agent_id`(=userId) / `session_id` / `enduser_id` / `channel_type` /
  `last_user_message_hash` / `gateway_request_time`，generation 名 `engine_proxy`，
  后续 SSE chunk 累积为 output + usage。admin 监控中心按 `agent_id` 过滤、按
  `enduser_id` / `channel_type` 等业务维度查询都基于 Gateway trace。
- **Hermes 插件写内层 trace**：`plugins/observability/langfuse` 插件 opt-in，
  在 `pre_llm_call` / `post_llm_call` hook 点写 trace。插件用 `session_id` 作
  trace.sessionId 种子（与 Gateway 一致，因 Gateway 发的 `x-hermes-session-id`
  被 Hermes 存到内部 context，LiteLLM callback 能读到），内层 trace 的 input
  含 Hermes 收到的原始 messages，所以两端从同一份请求体取最后一条 user 消息
  哈希后能匹配上。
- **admin 监控中心关联查询**：trace 详情页用**确定性 trace_id 直取**（主路径）
  + `session_id` 过滤 list（兜底）找 Hermes 内层 trace，再用 Gateway 写入的
  `last_user_message_hash` + `gateway_request_time`（±10s 时间窗口）在子 turn
  observations 里哈希匹配，把 Hermes 的 observations（LLM 调用、tool 调用等）
  挂到 Gateway trace 详情下方展示，用户能看到"Gateway 把请求转给 Hermes 后，
  Hermes 内部具体调了哪些 LLM + 工具"。

### trace 行 sessionId "错位一格"污染（2026-07-22 定位）

**不能依赖 Hermes 插件写的 trace 行 sessionId 字段做关联。** 实测规律
（种子哈希与真实 trace id 三次验证一致）：长寿命 profile 进程里，每个 run
的 trace_id 种子正确编码当前 session（`sha256("{session_id}::{session_id}")[:32]`，
`/v1/runs` 下 `effective_task_id = session_id`），但 Langfuse trace 行的
sessionId 字段 = **该进程里上一个 run 的 session**（trace 行创建那一刻把进程
残留的上一 run 会话上下文写进去，后续 merge 不更正）。后果：除进程重启后的
第一个 run 外，按 sessionId 过滤全部关联不上；且会出现"背着当前 sessionId
的别人的 trace"（反向污染 decoy）。

关联查询因此改为：`pkg/common/langfuse_correlation.hermes_session_trace_id()`
本地复算确定性 trace_id 直取（主路径，对污染免疫），list_traces(sessionId=...)
留作兜底（覆盖 task_id != session_id 的 seed 及未污染场景）；子 turn input
哈希匹配保证 decoy 不会误关联。根因在插件/引擎侧会话上下文跨 run 残留
（未闭环 root span + 进程级状态），如需彻底修复须上游插件层面清理。

### 为什么需要双写

之前 Gateway 独家写 trace，但 Gateway 只能看到 Gateway ↔ Hermes 的边界
（一次请求 + 一次响应），看不到 Hermes 内部的多轮 LLM 调用、tool 调用、
reasoning 步骤。需要看清 Hermes 内部调用链时，必须让 Hermes 也写 trace
并通过 session_id 关联起来。

### Hermes 插件 env 注入

`deploy/k8s/engines/hermes-template.yaml` 通过 k8s Secret 注入三个 env：
`HERMES_LANGFUSE_PUBLIC_KEY` / `HERMES_LANGFUSE_SECRET_KEY` /
`HERMES_LANGFUSE_BASE_URL`，指向与 Gateway 相同的 Langfuse 实例。
Secret 用占位符值（`CHANGE_ME`），真实凭据部署时由 `deploy/ci/.env.local`
注入；本地 k3s 用 `deploy/k8s/infra/secret.yaml`（仅本地）。

### entrypoint-v2.sh 不再 disable 插件

之前 Step 1.5 强制 `hermes plugins disable observability/langfuse` 防误启用，
现已删除——允许 Hermes 启动时加载插件，通过 env 控制是否实际上报。
没注入 LANGFUSE env 时插件 fail open（hooks 静默 no-op），不影响生产。

### 插件激活状态在 per-profile config.yaml（2026-07-22 事故修复）

插件 opt-in 的"开关"写在**每个 profile 自己的 config.yaml** 的
`plugins.enabled: [observability/langfuse]` 段；env 只提供凭据。
profile 由 `hermes profile create --clone-from base` 创建后，manager 的
`build_profile_config_yaml` 会**整体覆盖**其 config.yaml（heal/sync/regen 路径），
克隆继承来的 plugins 段随之丢失 → 该 profile 不再写 "Hermes turn" 内层 trace
（2026-07-22 profile 方案切换后全量新建 USER profile 踩中，链路追踪关联全灭）。

修复：模板 `config/config.yaml.tmpl` 新增 `${plugins_block}` 占位符，
manager 渲染时只要自身配了 `langfuse_public_key`/`langfuse_secret_key`
（`pkg/common/config.py` settings）就为每个 profile 输出 plugins 段。
entrypoint 的 `hermes plugins enable` 仍保留（管 base profile），
用户 profile 以模板渲染为准。

### 相关分析

- Hermes 不透传 OpenAI 标准 `metadata` 字段（handler 只提取 `messages`/`stream`/`model`），
  所以 trace_id 无法通过 metadata 传给 LiteLLM——但这不影响本方案，本方案靠
  session_id + 请求体内容做软关联，不依赖 metadata 透传
- Hermes `openai-api` provider profile 没覆写 `build_extra_body`，session_id
  不会进 `extra_body`——但 Hermes 从 `x-hermes-session-id` header 读取 session_id
  存到内部 context，LiteLLM callback hook 能从 context 拿到，所以 Hermes
  langfuse 插件 trace.sessionId 与 Gateway trace.sessionId 一致
- Hermes langfuse 插件的 hooks 是只读的，不会把 trace_id 注入到 api_kwargs——
  所以无法做 trace_id 精确透传，只能做 session_id + message_hash 软关联

## 相关记忆

- [[monitoring-wiring-status]] — 监控栈 P1/P2 部署状态
- [[langfuse-v3-architecture]] — Langfuse v3 自建架构
