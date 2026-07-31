# Agent 引擎外挂 VNC 浏览器 + 人机接管方案

> 状态：方案设计，待执行（RWX 存储解法待实施时验证环境后定 A/B）
> 日期：2026-07-17
> 关联：[hermes-engine.md](hermes-engine.md)、[gateway.md](gateway.md)、[enduser-portal.md](enduser-portal.md)、[skill-secret-sidecar-design.md](skill-secret-sidecar-design.md)

## 1. 背景与目标

### 1.1 需求

给 Hermes Agent 引擎外挂一个带 VNC 能力的浏览器沙箱：

- Agent 可操作该浏览器（导航、点击、填表、读取页面）完成自动化任务。
- 涉及输入账号、密码、验证码等敏感操作时，由**用户接管**浏览器（经 Web VNC 实时操作）。
- 用户操作完成后，Agent **继续**操作，承接最新会话上下文（登录态、DOM）。

### 1.2 约束（来自 CLAUDE.md / 项目架构）

- 引擎跑在 k3s，Pod 化部署，不在本地 Docker 直接启动。
- 终端门户**禁止 iframe** 嵌入外部 UI；VNC 须以 `<canvas>` 直渲染（noVNC/KasmVNC）。
- Gateway **不允许查询 Controller 服务**取 upstream 地址；路由靠 `X-Agent-ID` + DNS 命名约定（直查共享 PG 可接受）。
- SSE 流式经 nginx 必须 `proxy_buffering off`。
- 不对开源软件做侵入式修改，只基于其已有扩展能力扩展。
- 一个引擎 Pod 默认最多 20 个 profile（多租户），浏览器涉及账号密码，**必须 per-profile 隔离**，凭证不得跨用户漂移。

### 1.3 关键发现（调研 Hermes 官方文档）

Hermes 引擎（`hermes-agent` PyPI 包）已有**成熟的一等浏览器工具集**，无需自建 Playwright 驱动：

- `browser_*` 工具（navigate/snapshot/click/type/scroll/press/vision/console/cdp/dialog）通过 `browser.cdp_url` 配置附加到任意 CDP 端点。
- `browser_vision` 让 LLM 能截图 + 视觉分析（可识别验证码）。
- `browser_dialog` 有 `must_respond` 策略 + timeout，是 Agent 暂停等待的先例。
- approval 机制（`approval.request`/`approval.responded` SSE + `POST /v1/runs/{id}/approval`）在 gateway 模式下工作（`surface=gateway`），可复用为接管信号通道。

→ Agent 侧浏览器控制问题已由 Hermes 原生解决，本方案只需：提供浏览器运行环境 + VNC 通道 + 接管协调 + 前端 UI。

## 2. 架构总览

采用 **Option 3：per-profile 浏览器 Pod，动态拉起**。浏览器是独立 Pod（与引擎 Pod 解耦），按 profile（用户）动态创建，天然隔离。

```
┌── engine Pod (engine-hermes-{short}) ──────────────┐   ┌── browser Pod (browser-{short}-{profile_hash}) ──┐
│  Hermes browser_* tools (原生, config 启用)         │   │  kasmweb/chrome:1.18.0                           │
│  browser.cdp_url =                                  │   │  CDP :9222 (127.0.0.1, Pod 内)                  │
│    http://browser-{short}-{ph}.{ns}.svc:9222        │──▶│  VNC :6901 (WS/RFB, KasmVNC)                    │
│  (per-profile config.yaml, manager 动态写入)        │   │  user-data-dir → /config (= browser-data PVC)   │
│  /opt/data/profiles/{name}/browser-data             │◀─┴─── 共享 browser-data PVC (解法 A: RWX) ────────│
│   (引擎经 subPath 挂载，tar /opt/data 时含此目录)   │   └──────────────────────────────────────────────────┘
└──────────────┬───────────────────────────────────────┘                  ▲
               │ X-Hermes-Profile (profile_resolver 算出)                  │
               │ /v1/runs/{id}/events SSE (approval 接管, 现有透传)        │ Gateway WS /v1/browser/{agent}/vnc
               └─────────────────── Gateway ───────────────────────────────┘ (JWT + X-Agent-ID + 访问校验)
                                   │
                                   ▼  终端门户: BrowserView(canvas) + ApprovalCard(复用)
```

### 2.1 三个控制面职责分离

| 控制面 | 端口 | 通道 | 作用 |
|---|---|---|---|
| CDP | 9222（浏览器 Pod 内 127.0.0.1，引擎经集群 DNS 访问） | engine → browser Pod | Agent 自动化驱动浏览器 |
| VNC | 6901（浏览器 Pod，经 Gateway WS） | 终端用户 → browser Pod | 人类接管时键鼠输入 + 实时画面 |
| approval | `/v1/runs/{id}/approval`（现有） | 终端用户 → engine | 接管状态机（暂停/恢复） |

## 3. 与参考方案的关键差异

参考方案（见附录 A）采用"独立 Docker bridge + 双容器 + Redis 状态总线 + iframe + Playwright 轮询"。本方案据项目实际架构做了本质调整：

| 参考方案 | 本方案 | 原因 |
|---|---|---|
| 独立 Docker bridge 网络 + 两个对等容器 | k3s **独立 Pod**（Option 3），引擎与浏览器解耦 | 项目引擎跑 k3s；per-profile 隔离要求浏览器按用户动态拉起 |
| Redis 状态总线轮询接管 | 复用 Hermes **approval 流程** | 项目引擎数据面无 Redis；Hermes 是黑盒 PyPI 包，无法植入轮询循环 |
| iframe 嵌入 Web-VNC | **noVNC `<canvas>`** 直渲染（RFB over WS） | CLAUDE.md 禁止 iframe；canvas 不违反 |
| Playwright 跨容器直连 + URL/DOM 启发式判断接管 | **Hermes 原生 browser_* 工具** + **LLM 自主决定接管** | 黑盒 Hermes 无法注入页面检测；LLM 看页面内容（snapshot/vision）自主判断更鲁棒 |
| CDP `--remote-debugging-address=0.0.0.0` | `127.0.0.1`（Pod 内） | sidecar/同 Pod 已废弃；Option 3 跨 Pod 走集群 DNS，CDP 不暴露全段 |
| `/config` 卷宿主机挂载 | browser-data 在 profile 目录下 `/opt/data/profiles/{name}/browser-data/` | 复用现有 hermes-data PVC + SUSPEND 归档 MinIO，登录态跨重启继承 |

## 4. 浏览器沙箱镜像选型

### 4.1 选型结论：`kasmweb/chrome:1.18.0`

经对比（详见附录 B），选用 `kasmweb/chrome:1.18.0`。核心理由：

- **KasmVNC = RFB over WebSocket**：单 WS 连接，与现有 Gateway SSE 透传同一套机制，**经 Gateway 代理最简单**。
- `linuxserver/chromium`（latest）已从 KasmVNC 迁移到 **Selkies（WebRTC）**，媒体走 UDP/SRTP，标准 HTTP 反代/Gateway 几乎无法透传，**不适用**。
- kasmweb/chrome 是"web-native 浏览器"开箱即用，`APP_ARGS` 可注入 CDP 参数，`VNC_PW` 认证，`/home/kasm-user` 持久化。

### 4.2 配置（环境变量注入，不改镜像）

```yaml
APP_ARGS: >-
  --remote-debugging-port=9222
  --remote-debugging-address=127.0.0.1
  --user-data-dir=/config/browser-profile
  --no-first-run
  --disable-default-apps
VNC_PW: <每 Pod 随机生成，存 Secret>
LAUNCH_URL: about:blank
KASM_RESTRICTED_FILE_CHOOSER: "true"
```

端口：6901（HTTPS VNC web，KasmVNC 自带 noVNC 前端）、9222（CDP，仅 Pod 内 127.0.0.1）。

### 4.3 安全加固（相对参考方案的改进）

- CDP 绑 `127.0.0.1`，不暴露 Pod 网络。
- `VNC_PW` 每 Pod 随机 + 经 Gateway 鉴权后才可达，双层防护。
- Kasm 镜像默认带终端 + passwordless sudo —— **必须禁用**，或经 Gateway 只代理 VNC 的 WS 子路径，剥掉 Kasm web 控制台。

### 4.4 v2 演进（可选）

后续可自建 `unionagents/browser-vnc`（`FROM kasmweb/chrome:1.18.0`），固化 entrypoint CDP 9222、内置 `/healthz`、移除多余桌面组件。与 `engine-hermes-v2` 自建模式一致。一期不做。

## 5. 浏览器 Pod 生命周期

浏览器 Pod 耦合 **profile 生命周期**，仿现有 `services/manager/app/worker/k8s_manager.py` 的 engine Pod 创建模式新增：

### 5.1 新增 manager 方法

- `create_browser_pod(agent_id, profile_name)` → 建 `browser-{short}-{profile_hash}` Deployment + Service（kasmweb/chrome，APP_ARGS 注 CDP 9222、VNC_PW 随机、user-data-dir 指 browser-data PVC 挂载点）+ browser-data PVC。
- `delete_browser_pod(...)` → 归档 browser-data → 删 Pod + Service + PVC。
- `suspend_browser_pod` / `resume_browser_pod`。

### 5.2 触发时机

- **profile 创建时**（browser-enabled agent，eager）：`services/manager/app/worker/profiles.py` 创建 profile 后顺带建浏览器 Pod。
- **suspend/destroy**：`lifecycle_service.py` 处理引擎 SUSPEND/DESTROY 时同步处理该 profile 的浏览器 Pod（先归档 browser-data）。
- **resume**：重建浏览器 Pod（恢复 browser-data）。
- **空闲回收**：浏览器 Pod 空闲超时（如 15min，`browser_idle_kill_minutes`）删除省资源，下次用重建（数据在 PVC/归档）。

### 5.3 命名约定

```
browser-{agent_short}-{profile_hash}.{namespace}.svc.cluster.local
  agent_short  = agent_id 去横线前 8 位（同 _engine_name）
  profile_hash = hash(profile_name)[:6]（与 engine scoped 命名一致风格）
```

browser pod_name 写入 `agent_deployments.internal_port_map`（JSON，已有字段）或新增 `browser_pod_name`，供 Gateway **直查 PG**取（符合"不查 Controller 服务、可直查 DB"约束，同 `resolve_engine_url` 模式）。

### 5.4 资源

浏览器 Pod 独立 CPU/mem 限额（与引擎解耦，不争抢）：

```yaml
requests: { cpu: "500m", memory: "512Mi" }
limits:   { cpu: "2",    memory: "2Gi" }
```

## 6. 存储方案（RWX 遗留，待实施验证）

profile 目录 `/opt/data/profiles/{name}/` 在引擎 Pod 的 PVC 上，该 PVC 当前为 **`ReadWriteOnce`**（`k8s_manager.py:create_pvc`，单 Pod 独占）。Option 3 浏览器是独立 Pod，无法同时挂载 RWO PVC。要让"浏览器数据在 profile 目录下"成立，引擎 Pod 与浏览器 Pod 须共享存储 → 需 **RWX（ReadWriteMany）存储类**。

### 6.1 两条解法（实施时验证环境后定）

| 解法 | 浏览器数据路径 | 存储 | 代价 |
|---|---|---|---|
| **A（推荐，贴合"在 profile 目录下"）** | 专用 RWX browser-data PVC；引擎 Pod 经 subPath 挂到 `/opt/data/profiles/{name}/browser-data/`，浏览器 Pod 挂到 kasm `/config`。`exec_tar_data` tar `/opt/data` 时默认跨挂载点自动包含 → 随 profile 归档 | 需 RWX 存储类（云：NAS/CFS/EFS；本地 k3s：加 nfs-subdir provisioner） | 一次性 infra 依赖 |
| **B（RWX 不可用时兜底）** | 浏览器 Pod 独立 per-profile RWO PVC，挂到 kasm `/config`；归档时 manager 额外 tar 该 PVC 到同一 MinIO 归档 | 纯 RWO，local-path 即可 | 数据不在 profile 目录字面路径下（逻辑上仍 per-profile、随 profile 归档） |

### 6.2 决策状态

**遗留**：实际实施时验证云上与本地 k3s 环境是否满足 RWX。满足 → A；不满足 → B。默认按 A 设计，降级 B 时调整挂载与归档逻辑。

### 6.3 归档

无论 A/B，browser-data 随 profile SUSPEND 归档 MinIO，RESUME 恢复，登录态（cookies/localStorage）跨重启继承。

## 7. Engine ↔ 浏览器通信（CDP via DNS）

### 7.1 per-profile 动态 `browser.cdp_url`

浏览器 Pod 建好后，manager 把 `browser.cdp_url` 写入该 profile 的 `config.yaml`（`/opt/data/profiles/{name}/config.yaml`）：

```yaml
toolsets:
  - hermes-api-server
  - terminal
  - browser                       # 启用内置 browser_* 工具集
browser:
  cdp_url: "http://browser-{short}-{ph}.{ns}.svc:9222"   # per-profile 动态
  dialog_policy: must_respond     # 原生 JS 对话框暂停
  dialog_timeout_s: 300
  record_sessions: true           # 审计录像(可选)
```

热加载或下次 `browser_*` 调用生效。Hermes `browser_*` 工具经此 URL 连 CDP（跨 Pod，集群内 DNS 可达，无 NetworkPolicy 阻塞——已确认全仓零 NetworkPolicy）。

### 7.2 隔离

per-profile 独立浏览器 Pod + 独立 user-data-dir + 独立 UID（kasm 容器内非 root）→ 用户 A 的 cookies 不会漂到 B。

## 8. 人机接管机制（复用 Hermes approval）

### 8.1 设计

让 **LLM 自主决定接管时机**（非参考方案的 URL/DOM 启发式）。Agent 在 SOUL.md / 技能文档中被指示：遇到登录/验证码 → 调 `terminal("takeover-browser --reason \"...\"")`。Hermes 命令安全机制将 `takeover-browser` 匹配到配置的 approval pattern → `approval.request` SSE（`surface=gateway`）。

在 Hermes 配置注册 pattern（schema 待 spike 确认）：

```yaml
approvals:
  patterns:
    - key: human-takeover
      match: "^takeover-browser"
      description: "请求人工接管浏览器"
      choices: [once]              # 只提供"完成"选项
      timeout_s: 1800              # 给用户充足时间(密码/验证码)
```

`takeover-browser` 是引擎镜像 / skill scripts 里的 no-op 脚本（`#!/bin/sh; exit 0`），审批通过后执行无副作用。

### 8.2 端到端流程（全部复用现有代码）

| 步骤 | 现有代码 | 改动 |
|---|---|---|
| Agent 调 `terminal("takeover-browser ...")` | terminal 工具 | 无（指令引导） |
| Hermes 发 `approval.request` SSE | `apps/enduser/src/composables/useChat.ts:915` `handleHermesEvent` | 加分支：`command` 以 `takeover-browser` 开头 → 设 `browserTakeoverActive=true` + 开 VNC 面板 |
| 前端显示卡片 + VNC | `ApprovalCard.vue`（`ChatPage.vue:384`） | ApprovalCard 复用；按钮文案按 takeover 分支改"允许继续"；新增 `BrowserView.vue` |
| 用户 VNC 操作 | 新增 BrowserView | 见 §10 |
| 用户点"允许继续" | `submitApproval(choice="once")` `useChat.ts:1156` → `POST /v1/runs/{id}/approval` | 无 |
| `approval.responded` SSE | `useChat.ts:926` | 清 `browserTakeoverActive`，VNC 切回 view-only |
| 哨兵命令执行(no-op)，Agent 继续 | terminal 工具 | 无 |
| Agent `browser_snapshot` 重新读 DOM 继续 | browser_* | 无 |

**接管状态机 = approval 状态机**，Hermes 原生管理阻塞/唤醒，**无需 Redis、无需 control sidecar、无需自定义工具、无需 clarify**。

### 8.3 为何不用其他机制

- **approval 直接触发**：文档显示 approval 是命令安全门控（`pre_approval_request`/`post_approval_response` 为 observer-only hook，无法主动 raise），故用哨兵 terminal 命令触发 pattern 命中。
- **clarify**：是"Agent 提问等回复"，语义偏问答；接管是"用户操作共享资源后信号完成"，approval 的 once 选择更贴切，且复用现有 ApprovalCard UI/transport。
- **control sidecar**：v2 备选，若 approval pattern 配置不可行则降级为 ~60 行 FastAPI sidecar + `request_human_takeover` 插件工具。

## 9. Gateway 路由

### 9.1 新增 VNC WS 路由

- `@app.websocket("/v1/browser/{agent_id}/vnc")` → 桥接到浏览器 Pod:6901（仿 `services/gateway/app/main.py:128` `wecom_bot` WS 桥模式）。
- Gateway 用 `profile_resolver`（`services/gateway/app/profile_resolver.py:82`）算出 profile_name → 查 `agent_deployments` 取 browser_pod_name（直查 PG）→ 桥接 `browser-{pod}.{ns}.svc:6901`。
- token 经 `?token=<jwt>` query（浏览器原生 WS 不能设 header，项目先例 `mockApi.ts:79`）。

### 9.2 DNS / 端口

`services/gateway/app/adapter/base.py:18` 的 `ENGINE_PORTS` 旁新增 `BROWSER_PORTS = {"vnc": 6901}`，构造 `browser-{short}-{ph}.{ns}.svc:6901`。纯 DNS / 直查 PG，无 Manager 服务调用，符合反向依赖约束。

### 9.3 Origin/Referer

VNC WS 握手**需 Origin**，不能套现有 `_STRIP_HEADERS`（`base.py:24`）。browser 路由单独 header 处理，与 SSE 剥头逻辑分离。

### 9.4 鉴权

复用 JWT + X-Agent-ID + `profile_resolver` 访问校验（`profile_resolver.py:372`），确保只有授权用户连该 profile 的 VNC。

### 9.5 接管信号

**不走新路由**，走现有 `/v1/runs/{id}/approval`。

## 10. 前端布局适配（Enduser Portal）

### 10.1 布局（复用 workspace 模式）

`.browserpanel` 克隆 `.rightpanel`（`apps/enduser/src/style.css:1648` + `initResizeHandle` `ChatPage.vue:910` + `toggleWorkspacePanel` `ChatPage.vue:745` + `html[data-workspace-panel]` `style.css:1661`）：

```
默认（浏览器关）:
┌──────┬──────────┬─────────────────────────────┐
│ rail │ sidebar  │        main (聊天全宽)        │
│ 48   │ 280      │  ChatMessages + Composer     │
└──────┴──────────┴─────────────────────────────┘

浏览器开（rail 点"浏览器"tab 或接管时自动展开）:
┌──────┬──────────┬──────────────┬──────────────────────┐
│ rail │ sidebar  │  main 聊天   │   browserpanel(VNC)   │
│ 48   │ 280      │  (可拖拽↔)   │   noVNC canvas        │
│      │          │  ~40%        │   ~55%，可折叠到 0     │
└──────┴──────────┴──────────────┴──────────────────────┘

浏览器最大化（可选，再点 tab 全屏 VNC）:
┌──────┬──────────┬─────────────────────────────────────┐
│ rail │ sidebar  │        browserpanel (VNC 全宽)       │
└──────┴──────────┴─────────────────────────────────────┘
```

### 10.2 交互（复用 workspace 机制）

- **开关**：rail 加"浏览器"图标 tab，点击 toggle `browserpanel` 展开/折叠（折叠=宽度 0，聊天全宽）。接管 `approval.request` 触发时**自动展开**。
- **拖拽**：分隔条 `initResizeHandle` 调整聊天/浏览器宽度比。
- **互斥**：browserpanel 与 workspace rightpanel **互斥**（同时只开一个右侧大面板，避免 5 列过宽）；开浏览器自动收 workspace。
- **VNC 模式**：Agent 工作时 view-only + 半透明 mask（可观看到 Agent 操作，拦输入）；takeover 时去 mask、read-write（用户键鼠透传）；resume 后回 view-only。
- **折叠态保留连接**：折叠不断 VNC WS（仅 UI 隐藏），完全离开 agent 才断。

### 10.3 改动文件

| 文件 | 改动 |
|---|---|
| **新** `apps/enduser/src/components/chat/BrowserView.vue` | noVNC `<canvas>`，mount 连 `wss://.../api/gateway/v1/browser/{agent}/vnc?token=`；takeover 时 read-write，否则 view-only + mask |
| `useChat.ts:915` approval.request 分支 | 检测 `command` 以 `takeover-browser` 开头 → 设 `browserTakeoverActive` ref → ChatPage 开 browser 面板 |
| `ChatPage.vue:536` 旁 | 新增 `<aside class="browserpanel">`（克隆 `.rightpanel` 样式 + `initResizeHandle`）；rail 加"浏览器"tab |
| `ApprovalCard.vue` | 按 `browserTakeoverActive` 分支调文案（"接管浏览器"/"允许继续"），按钮 → `submitApproval(once)`（现有） |
| `apps/enduser/src/components/icons/lucide.ts` | 加 monitor/mouse-pointer 图标 |
| `nginx.conf` | 单独 `location /api/gateway/v1/browser/`，`proxy_buffering off; Connection "upgrade"` |

VNC 连接 token 过期走 `refreshAccessToken`（`apps/enduser/src/api/auth.ts:78`）重连。

### 10.4 文案合规（CLAUDE.md）

按钮/提示只写行为不写实现：用「接管浏览器」「已完成，继续」而非「释放 CDP 控制权」「切换 control API 状态」。

## 11. 数据模型与配置

### 11.1 DB schema（需 migration）

`AgentInstance`（`services/manager/app/models/__init__.py:428`）新增 `runtime_config` JSON 列（未来承载更多运行时开关）：

```python
runtime_config: Mapped[dict] = mapped_column(JSON, default=dict)
# {"browser_sandbox": {"enabled": true}}
```

比单独加 bool 列更可扩展。migration 脚本按 CLAUDE.md 规范配，本地 DB + 云 DB 同步执行。

### 11.2 新增 settings（`pkg/common/config.py`）

- `browser_sidecar_image`（env `UA_BROWSER_SIDECAR_IMAGE`，默认 `kasmweb/chrome:1.18.0`）
- `browser_pvc_storage_class`（按解法 A: RWX 类 / B: RWO 类）
- `browser_pvc_size`（默认 `2Gi`）
- `browser_idle_kill_minutes`（默认 15）

### 11.3 部署清单同步

- `deploy/k8s/services/manager.yaml`：加上述 env
- `deploy/ci/deployment.yaml`：同步（避免 CLAUDE.md 警告的 cloud 本地漂移）

### 11.4 Admin UI

agent 实例页加"启用浏览器沙箱"开关（写 `runtime_config.browser_sandbox.enabled`）。

## 12. 安全

- **CDP**：绑 127.0.0.1，仅浏览器 Pod 内；引擎跨 Pod 经集群 DNS 访问 9222（集群内可信）。
- **VNC**：`VNC_PW` 每 Pod 随机（Secret）+ Gateway JWT 鉴权双层；Gateway 只透传 VNC WS 子路径，剥 Kasm web 控制台。
- **多租户隔离**：per-profile 独立浏览器 Pod + 独立 user-data-dir + 独立 UID，凭证不漂移。
- **Kasm 终端**：禁用（防用户提权）。
- **敏感信息**：`VNC_PW`、Secret 经 k8s Secret / `.env.local`（已 gitignore）管理，不入仓库（CLAUDE.md 安全约束）。

## 13. 待验证 Spikes（实施前必做）

| # | 项 | 验证方式 | 决定 |
|---|---|---|---|
| 1 | 目标环境 RWX 可用性（云存储类 + 本地 k3s nfs provisioner） | 查云盘存储类 / 本地 k3s 部署 nfs-subdir | 定存储解法 A/B |
| 2 | hermes-agent 0.17.0 用 `browser.cdp_url`+`toolsets:[browser]` 还是 legacy `tools.browser_enabled` | 容器内 `hermes config` / 查 schema | config 写法 |
| 3 | 自定义 approval pattern 配置 schema + timeout | 查 hermes `approvals:` 文档/源码 | 接管触发可行性 |
| 4 | kasmweb/chrome APP_ARGS 是否真启用 CDP 9222 + user-data-dir | `docker run kasmweb/chrome` + APP_ARGS，`curl localhost:9222/json/version` | 镜像可用性 |
| 5 | KasmVNC WS 经 Gateway 代理（Origin/握手） | 本地 kasm + Gateway WS 路由 + 浏览器 noVNC 连接 | VNC 通道可行性 |

## 14. 实施分阶段

| 阶段 | 内容 | 验收 |
|---|---|---|
| **P0 Spike** | 跑 §13 五项验证 | 前 5 项绿，定存储 A/B |
| **P1 后端** | `k8s_manager.py` 加 browser Pod 创建/删除/PVC；`runtime_config` migration；profile 生命周期挂浏览器 Pod | kubectl get browser pod，curl healthz 绿 |
| **P2 Agent 侧** | config.yaml 注 browser 块 + cdp_url；approval pattern；takeover-browser 哨兵脚本 | Agent 经 CDP 打开页面、读 DOM；触发 approval |
| **P3 Gateway** | `/v1/browser/{agent}/vnc` WS 路由 + browser DNS + Origin 处理 | noVNC 经 Gateway 连上浏览器 Pod |
| **P4 前端** | BrowserView.vue + browserpanel + useChat approval 分支 + ApprovalCard 文案 | 本地 vite：Agent 触发 takeover → 用户 VNC 输入 → 释放 → Agent 继续 |
| **P5 生命周期** | browser-data 挂载（A/B）+ SUSPEND/RESUME 登录态继承 | 归档恢复后 cookies 还在 |
| **P6 测试** | 单测（k8s_manager browser Pod 构造、Gateway WS 路由、approval 分支）+ 本地 curl 冒烟 + 资料中心同步 | `make test` 全绿 |

## 15. 改动面汇总

**3 处新增 + 配置/migration**：

1. `services/manager/app/worker/k8s_manager.py` 新增 browser Pod 创建/删除/PVC（仿 engine Pod 模式）+ profile 生命周期挂接
2. `services/gateway` 新增 `/v1/browser/{agent}/vnc` WS 路由 + browser DNS/port + Origin 处理
3. `apps/enduser` 新增 `BrowserView.vue` + `ChatPage.vue` browserpanel（复用 workspace）+ `useChat.ts` approval 分支 + `ApprovalCard` 文案
4. `AgentInstance.runtime_config` migration + config.yaml browser 块 + Admin 开关 + settings env + 部署清单同步

---

## 附录 A：参考技术文档（用户提供原始方案）

> 来源：用户提供的《云端托管浏览器与 AI Agent 协同接管系统》技术设计文档。本方案据此适配项目实际架构，差异见 §3。

### A.1 原始架构

采用双容器解耦架构，业务控制逻辑与底层渲染环境隔离，通过状态总线实现人机协同：

- **AI Agent 容器**：运行独立控制逻辑（Hermes 或 Playwright 驱动），通过 CDP 协议对浏览器发指令。
- **Browser 容器**：轻量化 Alpine Linux，内置 `linuxserver/chromium` 镜像，集成 Chromium + KasmVNC 渲染引擎。
- **Orchestrator & State Bus**：Redis 充当信号中转，管理 Agent 运行状态 + 协调前端用户接管权限。

系统拓扑：两个容器在 Docker 自定义 Bridge 网络内，Agent 经 `browser-env:9222`（CDP 转发）连浏览器。

### A.2 原始容器配置

```bash
docker network create task-net-001

docker run -d \
  --name browser-env \
  --network task-net-001 \
  -p 3000:3000 \
  -p 9222:9222 \
  -v /data/chrome_sessions/user_001:/config \
  -e CHROMIUM_FLAGS="--remote-debugging-port=9222 --remote-debugging-address=0.0.0.0" \
  --shm-size=2g \
  linuxserver/chromium:latest
```

核心参数检查项：
- `--shm-size=2g`：必须显式分配共享内存，否则 Chromium 渲染复杂页面时 `Aw, Snap!` 内存不足崩溃。
- `--remote-debugging-address=0.0.0.0`：必须全网段监听，否则跨容器 Agent 被安全策略拦截。
- `/config` 卷持久化：用户接管期间登录态（Cookies、LocalStorage）落盘，容器重启后继承。

### A.3 原始 Agent 连接与状态感知（Python/Playwright）

```python
import os, asyncio, requests, redis
from playwright.async_api import async_playwright

r = redis.Redis(host='state-bus-redis', port=6379, db=0)

async def check_intervention_rules(page):
    """检查页面是否进入敏感域名或遇到人机挑战"""
    url = page.url
    if "login" in url or "signin" in url:
        return True
    captcha_element = await page.query_selector('.geetest_radar_btn, iframe[src*="recaptcha"]')
    if captcha_element:
        return True
    return False

async def run_coordinated_agent(session_id):
    browser_base_url = os.getenv("BROWSER_WS_ENDPOINT", "http://browser-env:9222")
    meta_response = requests.get(f"{browser_base_url}/json/version", timeout=5)
    web_socket_url = meta_response.json()['webSocketDebuggerUrl']

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(web_socket_url)
        context = browser.contexts[0] if browser.contexts else browser
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto("https://tencent.com")

        while True:
            if await check_intervention_rules(page):
                # 发布状态事件，通知前端解锁画布
                r.set(f"session:{session_id}:status", "NEED_HUMAN")
                # Agent 阻塞，期间禁止向 CDP 发键鼠操作
                while r.get(f"session:{session_id}:status") == b"NEED_HUMAN":
                    await asyncio.sleep(1)
            # 正常自动化逻辑
            await asyncio.sleep(2)
```

### A.4 原始前端中转（iframe + 状态轮询）

```html
<div class="layout">
  <div class="vnc-container">
    <div id="ui-mask" class="interaction-mask">🤖 Agent 正常作业中，前台已被锁定...</div>
    <iframe id="browser-view"
            src="http://your-server-ip:3000/?autoconnect=1&resize=remote&hide_header=1"
            width="100%" height="100%" frameborder="0">
    </iframe>
  </div>
  <div class="side-panel">
    <h3>会话状态: <span id="lbl-status">AGENT 控制</span></h3>
    <button id="btn-release" disabled>🙋‍♂️ 我已手动处理完毕，交还控制权</button>
  </div>
</div>
<script>
  // 状态机轮询（建议生产换 WebSocket 推送）
  setInterval(async () => {
    const res = await fetch(`/api/session/status?id=${sessionID}`);
    const state = await res.json();
    if (state.status === "NEED_HUMAN") {
      // 移除遮罩，解锁 iframe 鼠标/键盘
      document.getElementById("ui-mask").style.display = "none";
      document.getElementById("btn-release").disabled = false;
    } else {
      document.getElementById("ui-mask").style.display = "flex";
      document.getElementById("btn-release").disabled = true;
    }
  }, 1000);

  document.getElementById("btn-release").onclick = async () => {
    await fetch(`/api/session/resume?id=${sessionID}`, { method: 'POST' });
  };
</script>
```

### A.5 原始实现优先级

1. 网络通信连通性：建 task-net 桥接网络，跨容器请求 `browser-env:9222/json/version` 验证元数据。
2. Session 状态切换：测 Redis 信号键，模拟 `NEED_HUMAN` 写入验证 Agent 停指令、状态切回 `WORKING` 时 Agent 不刷新页面继承 DOM 继续执行。

---

## 附录 B：浏览器镜像选型对比

候选：A. linuxserver/chromium (latest) / B. kasmweb/chrome / C. selenium/standalone-chromium / D. 自建镜像（基于 KasmVNC）。

| 维度 | A. linuxserver/chromium | B. kasmweb/chrome ✅ | C. selenium/standalone-chromium | D. 自建(KasmVNC) |
|---|---|---|---|---|
| 渲染/传输协议 | Selkies = **WebRTC**+GStreamer（视频 UDP/SRTP，信令 WS 8082） | **KasmVNC** = RFB over **WebSocket**（单 WS 6901） | x11vnc + noVNC = RFB over **WebSocket**（7900） | KasmVNC = RFB over **WebSocket** |
| 镜像大小 | ~1.1 GB | **1.3 GB** | ~1.1 GB | ~0.7–0.9 GB |
| 空闲内存 | ~450–600 MB | ~400–550 MB | ~350–500 MB | ~300–450 MB |
| 开源协议/成熟度 | GPL-3.0，社区极活跃，周更 | KasmVNC Apache-2.0；企业级 10M+ pulls | Apache-2.0，自动化标准，极成熟 | 自控，基于 Apache-2.0 KasmVNC |
| CDP 暴露 | `CHROME_CLI` 注入 | `APP_ARGS` 注入 | 原生支持（主接口 WebDriver） | entrypoint 写死 9222 |
| Web 接入 | HTTPS 3001，默认无认证 | HTTPS 6901，`VNC_PW` 密码认证 | HTTP 7900，可选密码 | HTTPS 6901，自定义 |
| 登录态持久化 | `/config`（PUID/PGID） | `/home/kasm-user` | 默认无（session 级） | `/config` |
| K8s sidecar 友好 | 好，但 Wayland 需 AVX2 | 好，纯 CPU，无特殊硬件 | 好，纯 CPU | 好，纯 CPU |
| **🔴 经 Gateway 代理难度** | **难** — WebRTC 媒体走 UDP，需 STUN/TURN，HTTP 反代无法透传 | **易** — 单 WebSocket，与现有 SSE 透传同机制 | **易** — 单 WebSocket | **易** — 单 WebSocket |
| 人类接管 UX | 优（WebRTC 低延迟） | 优（剪贴板同步、音频、4:4:4 文本清晰） | 一般（调试用，无音频、剪贴板弱） | 优（同 B） |
| 实现难度 | 中（WebRTC 穿透 Gateway） | **低**（开箱 + env 配置） | 中（VNC 面向调试） | 中高（自建维护） |

### B.1 关键判断

传输协议能否经 Gateway 代理是决定性因素：浏览器 Pod 只能集群内 ClusterIP 访问，终端用户浏览器在外网，VNC 流量**必须**经 Gateway 中转（否则绕过 Gateway 鉴权是安全倒退）。

- **WebSocket（KasmVNC/noVNC）** = Gateway 已有 SSE 透传机制可复用，加一条 WS 路由即可。
- **WebRTC（Selkies）** = 媒体走 UDP/SRTP + ICE 协商，标准 HTTP 反代/Gateway 几乎无法透传，需额外 STUN/TURN + 开 UDP 端口。

故 **A（linuxserver/chromium latest）不推荐** —— 用户参考文档里的"linuxserver/chromium + KasmVNC"是旧版，新版已切 Selkies，不再适合经 Gateway 代理。

### B.2 选型结论

**B：`kasmweb/chrome:1.18.0`**（开箱即用，KasmVNC/WebSocket，与项目 Gateway 架构天然契合）。后续要极致控制可演进到 D。

### B.3 安全改进（相对参考文档）

sidecar/同 Pod 已废弃（Option 3 跨 Pod），CDP 用 `--remote-debugging-address=127.0.0.1` 即可（Pod 内），**不需要**参考文档的 `0.0.0.0`（后者会把调试端口暴露到 Pod 网络全段）。

---

## 附录 C：Hermes 官方 browser 文档要点

> 来源：[NousResearch/hermes-agent · browser.md](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/browser.md)

### C.1 多后端浏览器工具集

Hermes 内置完整浏览器自动化工具集，多后端：

- **Browserbase / Browser Use / Firecrawl** 云模式
- **Camofox** 本地反检测（Firefox 指纹伪装），自带 VNC(6080/5901)、原生持久化、externally-managed sessions
- **Local Chromium-family CDP** — `browser.cdp_url` 配置附加到运行中的 Chrome/Brave/Chromium/Edge（非交互式 gateway/WebUI 用此路径，`/browser connect` 仅 CLI）
- **Local browser mode** — `agent-browser` CLI 驱动本地 Chromium

本方案用 **Local CDP attach**（`browser.cdp_url` 指向浏览器 Pod），因 kasmweb/chrome 已选型。Camofox 为备选（原生持久化 + VNC 发现 + 反检测，但 Firefox 引擎、镜像较 niche）。

### C.2 可用工具

`browser_navigate / snapshot / click / type / scroll / press / back / get_images / vision / console / cdp / dialog`。

- 页面以**无障碍树**（文本快照）表示，交互元素得 ref ID（`@e1`、`@e2`）供 click/type。
- **`browser_vision`**：截图 + AI 视觉分析，**可识别验证码** → LLM 据此决定接管。
- **`browser_dialog`**：原生 JS 对话框处理，`must_respond` 策略 + `dialog_timeout_s`（默认 300s）安全自动 dismiss —— Agent 暂停等待的先例。
- **`browser_cdp`**：原始 CDP 透传（逃生舱），仅 CDP 端点可达时可用。

### C.3 配置（`browser:` 块 + `toolsets`）

```yaml
toolsets: ["browser"]            # 启用浏览器工具集
browser:
  cdp_url: "http://127.0.0.1:9222"   # 附加到运行中的 Chromium
  cloud_provider: ...            # 云后端
  camofox: ...                   # Camofox 后端
  dialog_policy: must_respond    # must_respond / auto_dismiss / auto_accept
  dialog_timeout_s: 300
  record_sessions: false         # WebM 录像
```

注：旧版 `tools.browser_enabled` 可能已废弃，以 `browser:` + `toolsets` 为准（spike 确认 0.17.0 schema）。

### C.4 持久化与会话

- Camofox：`browser.camofox.managed_persistence: true` → profile-scoped `userId`，cookies/登录跨重启。
- CDP attach：附加到用户自有浏览器，Hermes 不做破坏性清理，登录态由浏览器自身 user-data-dir 维护。
- 会话隔离 + 空闲自动清理（`BROWSER_INACTIVITY_TIMEOUT` 默认 120s）。

### C.5 approval 机制（接管复用基础）

> 来源：[hooks.md](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/hooks.md)

- approval 由**命令安全 pattern** 触发（terminal/execute_code 命令匹配危险模式如 `rm_rf`/`sudo`）。
- `pre_approval_request` / `post_approval_response` 为 **observer-only hook**，无法主动 raise approval。
- approval 在 **gateway 模式下工作**（`surface: "cli" | "gateway" | "smart"`），`surface=gateway` 即异步平台审批。
- 故本方案用**哨兵 terminal 命令** `takeover-browser` 触发 pattern 命中 → 复用 `approval.request` SSE + `/v1/runs/{id}/approval` + ApprovalCard。

### C.6 插件 / 钩子扩展点

- `ctx.register_tool()`：加自定义工具（备选 `request_human_takeover` 插件工具）。
- `pre_tool_call` 可 `{"action":"block","message":...}` 否决工具（返回错误，非暂停等待）。
- `ctx.inject_message()` **仅 CLI 可用**，gateway 模式返回 False → 不能用注入消息做接管恢复信号。
- 故 approval 复用是最贴合的"gateway 模式下暂停等用户"机制。

---

## 附录 D：与参考方案关键差异表（汇总）

| 维度 | 参考方案 | 本方案 |
|---|---|---|
| 部署形态 | Docker bridge + 双对等容器 | k3s 独立 Pod（per-profile 动态） |
| 隔离 | 共享浏览器（task-net 内） | per-profile 独立 Pod + UID + user-data-dir |
| 状态总线 | Redis 轮询 | Hermes approval（无 Redis） |
| 前端嵌入 | iframe | noVNC canvas（禁 iframe） |
| Agent 驱动 | Playwright connect_over_cdp | Hermes 原生 browser_*（browser.cdp_url） |
| 接管判定 | URL/DOM 启发式 | LLM 自主（snapshot/vision） |
| 接管信号 | Redis 键 `NEED_HUMAN` | approval.request SSE + `/v1/runs/{id}/approval` |
| 接管恢复 | `POST /api/session/resume` | `submitApproval(once)`（现有） |
| CDP 监听 | `0.0.0.0:9222` | `127.0.0.1:9222`（Pod 内） |
| 数据持久化 | `/config` 宿主机卷 | `/opt/data/profiles/{name}/browser-data/`（PVC + MinIO 归档） |
| 镜像 | linuxserver/chromium（旧版 KasmVNC） | kasmweb/chrome:1.18.0（KasmVNC/WebSocket） |
| 传输 | KasmVNC WS（旧版） | KasmVNC WS（经 Gateway 代理） |
