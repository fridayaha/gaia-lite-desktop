# 浏览器沙箱 VNC 接管 — 当前状态 + 待修 Bug（2026-07-17）

> 新会话接上用：读完此文件即可了解全部上下文 + 当前阻塞 + 已做的所有改动 + 云上状态。

## 一、功能概述

给 Hermes 引擎外挂带 VNC 的浏览器沙箱：
- Agent 经 CDP（`browser_*` 工具）自动化操作浏览器
- 用户经 Web VNC（noVNC canvas）接管浏览器完成登录/验证码等
- 接管互斥：run 活跃时禁接管（VNC view-only），接管时禁发消息

完整方案在 `docs/features/browser-sandbox-design.md` + `~/.claude/plans/misty-discovering-goblet.md`。

## 二、已完成 + 已验证

### ✅ 后端（manager）
- `AgentInstance.runtime_config` JSON 列（migration 025）+ 实例 CRUD 接口收 `runtime_config.browser_sandbox.enabled` + admin checkbox
- `k8s_manager.py`：`create_browser_pod` / `delete_browser_pod` / `scale_browser_to_zero` / `resume_browser_pod`
  - browser Pod = chrome 容器（kasmweb/chrome:1.18.0，APP_ARGS 注 `--remote-debugging-port=9223 --remote-allow-origins=*`）+ cdp-proxy sidecar（python3 /opt/cdp_proxy.py，CDP 感知代理）
  - initContainer 清 chrome profile SingletonLock（PVC 跨 pod 重建留 stale 锁）
  - RWO PVC（/config，local-path）+ VNC Secret + NetworkPolicy（best-effort，k3s 强制执行）
- `_common.py`：`ensure_browser_pod_for_profile` / `suspend/resume/delete_browser_pods_for_deployment` / `_set_browser_pod_in_port_map`
  - `internal_port_map["browsers"][profile_name] = {"pod": pod_name, "vnc_pw": vnc_pw}`
- `build_profile_config_yaml`：browser_sandbox 启用时注 `platform_toolsets.api_server: [..., browser]` + `browser.cdp_url`
- `profiles.py`：`_do_create_profile` step 8 调 `ensure_browser_pod_for_profile`；`teardown_profile` 删 browser Pod
- `lifecycle_service.py`：suspend/destroy 同步处理 browser Pod
- `engines/browser/cdp_proxy.py`：CDP 感知代理（重写 Host→localhost + webSocketDebuggerUrl→外部地址 + WS 隧道）
- `engines/browser/Dockerfile`：FROM kasmweb/chrome:1.18.0 + COPY cdp_proxy.py
- `engines/hermes/config/config.yaml.tmpl`：加 `$browser_toolset_line` + `$browser_block` 占位符
- `pkg/common/config.py`：`browser_sidecar_image`(browser-v2:1.18.0) / `browser_pvc_storage_class`(local-path) / `browser_pvc_size`(2Gi) / `browser_idle_kill_minutes`(15) / `browser_cdp_proxy_port`(9222) / `browser_cdp_chrome_port`(9223) / `browser_vnc_port`(6901)

### ✅ Gateway
- `app/browser_vnc.py`：`bridge_vnc_ws` — 1:1 WS 桥，上游 `wss://browser-pod:6901/websockify` + Basic auth(kasm_user:VNC_PW) + Origin + ssl verify=False + subprotocols=["binary"]
- `app/profile_resolver.py`：`resolve_browser_target(user_id, agent_id, is_admin)` → (profile_name, browser_pod, vnc_pw)，从 `internal_port_map["browsers"][profile]` 取
- `app/main.py`：
  - `@app.websocket("/api/gateway/v1/browser/{agent_id}/vnc")` — JWT query token 鉴权 + resolve + accept(subprotocol 条件回显) + bridge
  - `@app.get("/api/gateway/v1/browser/{agent_id}/vnc-credentials")` — 返回 vnc_pw（给前端 noVNC RFB auth 用）
- `app/settings.py`：`browser_vnc_port: int = 6901`
- 路由带 `/api/gateway` 前缀（chat-ingress 不剥前缀，同 wecom_bot 模式）

### ✅ 前端（enduser）
- `BrowserView.vue`：noVNC RFB，连 `wss://host/api/gateway/v1/browser/{agent}/vnc?token=JWT`，viewOnly 由接管态驱动，showDotCursor，重试上限 2 次（不死循环刷 refresh），fetchVncPassword 取 RFB 密码，credentialsrequired 送 `{username:"kasm_user", password:vncPassword}`
- `ChatPage.vue`：browserpanel（接管/全屏/关闭按钮）+ 互斥（与 rightpanel）+ ChatComposer 接管时禁用
- `useChat.ts`：`browserTakeoverActive` ref + 导出
- `style.css`：`.browserpanel` + 全屏 + 边缘按钮
- `lucide.ts`：补 monitor/mouse-pointer/hand/maximize-2/minimize-2 图标
- `vite.config.ts`：`build.target: es2022`（noVNC 顶层 await）+ `esbuild.target` + `optimizeDeps.esbuildOptions.target` + `/api/gateway` proxy `ws:true`
- `env.d.ts`：noVNC RFB 默认导出类型声明
- `nginx.conf`：`/api/gateway/v1/browser/` WS location（实际不用，ingress 直连 gateway）

### ✅ 云端验证通过
- browser Pod 2/2 Running（chrome + cdp-proxy）
- **CDP 命令驱动 chrome**：WS 连 `/devtools/page/...` → `Page.navigate https://example.com` → `Runtime.evaluate document.title` → "Example Domain" ✓
- **gateway VNC WS 路由**：101 + RFB greeting `RFB 003.008\n` ✓（WS 层 + Basic auth + Origin 都通）
- resolve_browser_target 对 admin(c246cea4-254e) → browser Pod ✓
- VNC_PW 三处一致（credentials 端点 = chrome env = Secret = `1cJbKRVqKIfFn-lZTm-PDqx5`）

### ✅ P2
- `preset_skills/browser-takeover/SKILL.md`：接管流程指引文档

## 三、✅ 已修复：VNC RFB auth 失败（2026-07-17）

### 根因
KasmVNC 的 Xvnc 进程挂了**两个独立的认证文件**（`ps aux | grep Xvnc` 可见）：

- `-KasmPasswordFile /home/kasm-user/.kasmpasswd` —— 多用户 crypt-SHA256 哈希
  （`kasm_user:$5$kasm$<hash>:wo`，由 `vnc_startup.sh:632` 的 `kasmvncpasswd -u kasm_user -wo` 从
  `VNC_PW` 生成）。驱动 **WS 升级层 Basic auth**（`kasm_user:VNC_PW` → 一直正常）。
- `-rfbauth /home/kasm-user/.vnc/passwd` —— 传统 8 字节 VNC passwd 文件，驱动 **RFB VNCAuth (type 2)**。

关键：`vnc_startup.sh` 只写 `~/.kasmpasswd`，**从不重新生成 `~/.vnc/passwd`**。后者是 kasm 镜像
**内置的默认文件**，经标准 VNC 固定 key 解密后是**全零**——即 RFB 层期待的是**空密码**，不是
`VNC_PW`。noVNC 标准 VncAuth 送 `VNC_PW`（非空）→ DES 响应不匹配 → `Authentication failure`。

WS Basic auth 用 `VNC_PW`（kasmpasswd）正常，掩盖了 RFB 层用的是另一个（空）密码的事实。

### 排除过程
- 流式读 RFB 握手（persistent buffer 跨 WS 帧）：kasm 只通告 `security type [2] = VNCAuth`（标准 type 2）✓
- 纯 Python DES（自测向量 `85e813540f0ab405` 通过）做完整 VncAuth：用 `VNC_PW` → `RESULT=1` 失败；
  标准 VNC 固定 key 解密内置 `~/.vnc/passwd`(`5ab2cdc0badcaf13`) → 全零 → 证实是空密码默认文件。
- 镜像里的 `vncpasswd` 实为 `kasmvncpasswd`（只支持 `-u` 多用户），无标准 vncpasswd 工具，
  故无法在 Pod 内用工具从 `VNC_PW` 重生成 8 字节 `~/.vnc/passwd`。

### 修复（Option B：RFB 层 NoAuth，WS Basic auth 保留）
`-SecurityTypes None` 关掉 RFB VncAuth，noVNC 走 NoAuth（type 1）无需 RFB 密码。WS 升级层 Basic auth
（`~/.kasmpasswd` = `VNC_PW`）仍由 kasm 强制——**无/错 Basic auth → HTTP 401**（实测）。叠加 gateway
JWT 鉴权，安全性不降（RFB VncAuth 与 WS Basic auth 本就用同一个 `VNC_PW`，RFB 层冗余）。

代码改动：
- `services/manager/app/worker/k8s_manager.py`：chrome 容器 env 加 `VNCOPTIONS=-SecurityTypes None`
  （`vnc_startup.sh` 只往 `VNCOPTIONS` 追加 `-select-de manual` 等，不重置，故 env 值透传给 Xvnc）。
- `apps/enduser/src/components/chat/BrowserView.vue`：删 `fetchVncPassword` + `credentialsrequired`
  （NoAuth 下不再触发，死代码）。
- `services/gateway/app/main.py`：删 `GET /api/gateway/v1/browser/{agent_id}/vnc-credentials` 端点
  （NoAuth 下 noVNC 不需要 RFB 密码，端点无用）。`bridge_vnc_ws` 仍注入 WS Basic auth（必需）。

### 云端实测（admin browser Pod `browser-1d515bfc-f825b0`）
设 `VNCOPTIONS=-SecurityTypes None` 后 `kubectl set env` + rollout：
- Xvnc args 含 `-SecurityTypes None`（仍带 `-KasmPasswordFile`）✓
- 正确 Basic auth → WS 101 → RFB `security types [1]=None` → `RESULT=0` → **FRAMEBUFFER OK** ✓
- 无 Basic auth → **HTTP 401** ✓（WS Basic auth 仍强制）
- 错 Basic auth → **HTTP 401** ✓

### 未采用的备选（Option A：重生成 ~/.vnc/passwd）
用标准 VNC 固定 key 把 `VNC_PW` 混淆成 8 字节写回 `~/.vnc/passwd`（实测 `obfuscate(VNC_PW)=07258dab889166d2`，
写入后 `RESULT=0` 通过）。但镜像无标准 vncpasswd，需 manager 内置纯 Python DES + Secret 存混淆字节 +
initContainer 写文件 + subPath 挂载（ perms 0600 uid 1000），moving parts 多；且 RFB VncAuth 与
WS Basic auth 同用 `VNC_PW` 冗余。故选 Option B。

## 三-bis、✅ 已修复：接管后黑屏 / 输入不响应 / 坐标偏移（2026-07-17）

RFB auth 修好后，接管又连续踩了 5 个坑，逐个定位 + 修复：

### 坑 1：接管后首个 PointerEvent → kasm "invalid pixel format" 断连（黑屏）
- 现象：noVNC 连上、framebuffer 流 9s，用户移光标进画面 → kasm 关连接、黑屏。
- 定位：gateway 加 `UA_VNC_DEBUG` 抓 noVNC↔kasm 全部 RFB 消息首字节。kasm 日志
  `invalid pixel format` = kasm 把 PointerEvent 的 mask 字节(0x00) 当成 SetPixelFormat
  类型读 19B 垃圾 → 流错位。直连 kasm bisect：stock noVNC 的 Fence + ContinuousUpdates
  扩展与 KasmVNC 解析器交互会错位（需 CU + Fence 响应同时存在才复现）。
- 根因：**stock `@novnc/novnc` 1.7 与 KasmVNC 服务端 RFB 扩展不兼容**（kasmtech 官方已知）。
- 修复：前端 VNC 客户端换成 **KasmVNC 官方 noVNC fork**（`@kasmtech/novnc` v1.3.0，
  vendor 进 `apps/enduser/src/lib/kasm-novnc/`，从 kasmweb/chrome:1.18.0 镜像同源）。
  fork 与 KasmVNC 服务端配套，Fence/CU/QEMU key event 等扩展正确处理。

### 坑 2：kasm noVNC fork 的宿主注入项
kasm noVNC fork 设计为由 kasm web app 驱动，当库用需补 2 个它期望宿主注入的配置：
- `touchInput`：RFB 构造签名是 `(target, touchInput, url, options)`（与 stock 的
  `(target, url, options)` 不同），`Keyboard` 用它做 IME/触摸锚点（`_keyboardInputReset`
  写 `.value`）。BrowserView 命令式 `document.createElement("input")` 创建、内联样式
  藏到屏外（命令式元素不受 Vue scoped 样式作用）。
- `mouseButtonMapper`：RFB 构造里 `=null`，鼠标事件 `.get(ev.button)` 报错。复刻 kasm
  web app `initMouseButtonMapper` 默认映射（左/中/右/侧键）注入。
- 其余 qualityLevel/compression/clipboard 等都是可选调优，RFB 构造有默认值，不必注入。

### 坑 3：vue-tsc 云构建报 TS7016 / 类型推断差异
- `import RFB from "./core/rfb.js"`（vendored 纯 JS）云构建 vue-tsc 报「找不到声明」，
  本地不报（同 TS/vue-tsc 版本，环境差异未明）。tsconfig 加 `allowJs: true`（`checkJs`
  仍 false）让 TS 能解析 .js 模块；用 `index.ts` 包装（`@ts-nocheck`）收口类型为 any。

### 坑 4：VNC 接管后 ~40s 断连（kasm 不回 WS pong）
- 现象：接管后 ~40s 连接断（kasm 日志 `Clean disconnection`）。
- 根因：gateway `websockets.connect` 默认 20s ping + 20s timeout；KasmVNC 的 websockify
  不回 pong → gateway 40s 误关。
- 修复：`bridge_vnc_ws` 设 `ping_interval=None, ping_timeout=None`，靠 RFB 流量保活，
  连接生命周期由 noVNC 客户端控制。

### 坑 5：连接打架 → "Server is already in use" → 握手卡死 → 输入不响应
- 现象：接管后键盘不能输入、鼠标点击无反应。gateway 诊断：握手只到 ClientInit（无
  SetEncodings），RFB 没 reach "connected" → input handler 没挂上。
- 根因：browser 重连竞态开 2 条 WS（负载均衡到 2 个 gateway pod），kasm VNC 单会话 +
  `-DisconnectClients 0`（默认）→ 新连接被 "Server is already in use" 拒，握手卡死。
- 修复：① manager `VNCOPTIONS` 加 `-DisconnectClients 1`（新连接踢旧，新连接必胜）。
  ② BrowserView 加 `connecting` 守卫防并发 `connect()`。

### 坑 6：鼠标坐标偏移几十 px（高度）
- 根因：BrowserView CSS `.bv-screen canvas { width:100%!important; height:100%!important;
  object-fit:contain }` 覆盖了 noVNC `scaleViewport` —— canvas 元素被强拉满铺、画面靠
  object-fit letterbox，但 noVNC 点击坐标按满铺元素算 → 偏移。
- 修复：去掉 `!important` 强拉 + object-fit，`.bv-screen` 用 flex 居中，canvas 尺寸交给
  noVNC `scaleViewport`（保持 1024x768 比例，元素=画面，坐标映射准确）。

### 附：PWA service worker 导致登录 404
换 noVNC 库后 chunk hash 大变，浏览器缓存旧 `index.html`（引用旧 chunk hash）→
"Failed to fetch dynamically imported module"。根因：enduser 有 PWA sw.js，其 `fetch`
走 HTTP 缓存，nginx 没给 `index.html` 设 no-cache。修复：① nginx `index.html` →
`no-cache,no-store,must-revalidate`，`/assets/` → `immutable`；② sw.js 升 v2（清旧缓存），
导航请求 `cache:'no-cache'` 强制再校验，`/assets/` cache-first。

### chrome DevTools (CDP) 卡死（已知风险，未根治）
Agent 的 `browser_*` 走 cdp-proxy:9222 → chrome:9223。发生 2 次 chrome DevTools HTTP
server 卡死（`/json/version` 超时，0 活跃连接，chrome 进程正常）→ Agent 调不通。重启
browser Pod 恢复。根因未明（疑似 chrome DevTools 线程卡死，可能与 VNC 接管交互有关）。
**待办**：加 CDP 健康探针 + 自动恢复（重启 chrome/pod），或监控告警。

## 四、云上当前状态

### 镜像（ACR VPC：crpi-x1lxt7dogr41s0b4-vpc.cn-hangzhou.personal.cr.aliyuncs.com/unionagents/）
- manager: 0.8.144-browser3 — VNCOPTIONS=`-SecurityTypes None -DisconnectClients 1`
- gateway: 0.8.144-browser9 — 干净版（ping 修复 + 删 vnc-credentials 端点，去诊断日志）
- enduser-portal: 0.8.144-browser9 — KasmVNC noVNC fork + 坐标修复 + sw.js v2 + nginx no-cache + allowJs
- browser-v2: 1.18.0（kasmweb/chrome + cdp_proxy.py）
- 注：browser Pod 的 `VNCOPTIONS` env 已对 admin/store_mgr 两个 deploy 手动 set（manager 代码也已带，新建 Pod 自动）

### k3s 部署
- manager/gateway/enduser-portal 已 set image 到上述版本
- engine 镜像未改（0.8.143，browser toolset 由 manager 渲染 config.yaml 注入，不需改引擎镜像）
- DB migration 025 已执行（runtime_config 列）
- manager Role 已 patch 加 secrets/configmaps/networkpolicies（需落 deploy 清单持久化）
- manager env `UA_BROWSER_PVC_STORAGE_CLASS=local-path`（需落 deploy 清单）
- NetworkPolicy 已手动删除（k3s 强制执行，9222 规则不匹配旧 engine pod label）

### 测试实例
- 智能体：「测试智能体」(1d515bfc-0a5a-4704-9b85-91960baef51b)，runtime_config.browser_sandbox.enabled=true
- 测试用户：store_mgr (40767032) + admin (c246cea4-254e-46d1-97a2-03b5a947f3cd)
- browser Pod：
  - `browser-1d515bfc-ed7873`（store_mgr 的，profile=1d515bfc-628959-40767032，vnc_pw=oxTcuLTyHsCjqfJhf87Tv96X）
  - `browser-1d515bfc-f825b0`（admin 的，profile=1d515bfc-cfd2a9-c246cea4，vnc_pw=1cJbKRVqKIfFn-lZTm-PDqx5）
- internal_port_map["browsers"] 有两个 profile 的 {pod, vnc_pw}
- portal: https://chat.pow8.cn（chat-ingress → gateway:8010 for /api/gateway, → enduser-portal:80 for /）
- admin 登录后进「测试智能体」→ 点「云桌面」→ 点「接管云桌面」→ 触发 VNC WS → RFB auth 失败

### 重要操作命令
```bash
# SSH 到云
ssh ubuntu@47.96.121.165

# 查看 browser pod
kubectl -n unionagents get pod -l unionagents.io/component=browser

# 查看 gateway 日志（VNC WS 请求）
kubectl -n unionagents logs deploy/gateway --since=300s | grep -iE 'browser|vnc|403|101'

# 查看 kasm 日志（chrome 容器）
kubectl -n unionagents logs deploy/browser-1d515bfc-f825b0 -c chrome --tail=20 | grep -iE 'vnc|rfb|auth|connection'

# 从 gateway pod 测 kasm VNC WS
cat /tmp/vnc_route_test.py | kubectl -n unionagents exec -i deploy/gateway -- python -

# 从 engine pod 测 CDP
cat /tmp/cdp_ws_test.py | kubectl -n unionagents exec -i deploy/manager -- python -

# 重建镜像（在云上 ~/union_agent）
DOCKER_BUILDKIT=0 docker build -f <Dockerfile> -t <REG>/<image>:<tag> .
docker push <REG>/<image>:<tag>
# 按 digest 部署（IfNotPresent + 同 tag 不拉新 digest）
DIG=$(docker inspect --format='{{index .RepoDigests 0}}' <REG>/<image>:<tag> | sed 's/.*@//')
kubectl -n unionagents set image deploy/<deploy> <container>=<REG>/<image>@${DIG}
# 或 crictl rmi 强拉
sudo k3s crictl rmi <REG>/<image>:<tag>
kubectl -n unionagents delete pod -l app=<deploy>
```

## 五、所有改动文件清单

### 后端（manager）
- `services/manager/app/worker/k8s_manager.py` — browser Pod CRUD + CDP proxy sidecar + initContainer + NetworkPolicy
- `services/manager/app/worker/_common.py` — ensure/suspend/delete/resume browser pod helpers + load_instance_config 加 runtime_config + build_profile_config_yaml 加 browser 块
- `services/manager/app/worker/profiles.py` — _do_create_profile step 8 + teardown_profile 删 browser Pod
- `services/manager/app/worker/lifecycle_service.py` — suspend/destroy 同步 browser Pod
- `services/manager/app/worker/lifecycle.py` — resume 同步 browser Pod
- `services/manager/app/models/__init__.py` — AgentInstance.runtime_config 列
- `services/manager/app/schemas/__init__.py` — AgentInstanceCreate/Update/Response 加 runtime_config
- `services/manager/app/services/instance_service.py` — create/update/clone 持久化 runtime_config
- `services/manager/app/api/agent_instances.py` — _to_response 加 runtime_config
- `services/manager/migrations/025_instance_runtime_config.sql`
- `services/manager/app/data/preset_skills/browser-takeover/SKILL.md`
- `pkg/common/config.py` — browser_* settings
- `engines/hermes/config/config.yaml.tmpl` — browser 占位符

### Gateway
- `services/gateway/app/browser_vnc.py`（新）— bridge_vnc_ws（ping_interval=None 防 40s 误断）
- `services/gateway/app/profile_resolver.py` — resolve_browser_target
- `services/gateway/app/main.py` — VNC WS 路由（vnc-credentials 端点已删，NoAuth 不需要 RFB 密码）
- `services/gateway/app/settings.py` — browser_vnc_port

### 前端（enduser）
- `apps/enduser/src/components/chat/BrowserView.vue`（新）— KasmVNC noVNC fork 驱动 + touchInput/mouseButtonMapper 注入 + connecting 守卫 + 坐标修复
- `apps/enduser/src/lib/kasm-novnc/`（新，vendored）— `@kasmtech/novnc` v1.3.0 的 core/ + vendor/pako + index.ts 包装（替代 stock @novnc/novnc，修 RFB 扩展不兼容）
- `apps/enduser/src/components/chat/ChatPage.vue` — browserpanel + 接管/全屏按钮 + 互斥
- `apps/enduser/src/composables/useChat.ts` — browserTakeoverActive
- `apps/enduser/src/style.css` — .browserpanel
- `apps/enduser/src/components/icons/lucide.ts` — 补图标
- `apps/enduser/src/env.d.ts` — 移除 @novnc/novnc 声明
- `apps/enduser/vite.config.ts` — es2022 + WS proxy
- `apps/enduser/nginx.conf` — index.html no-cache + /assets/ immutable（修 SW 缓存旧 chunk hash）
- `apps/enduser/public/sw.js` — v2：导航 cache:'no-cache' + /assets/ cache-first + 清旧缓存
- `apps/enduser/tsconfig.json` — allowJs:true（让 vue-tsc 解析 vendored kasm noVNC .js）
- `apps/enduser/package.json` / `pnpm-workspace.yaml` — 移除 @novnc/novnc 依赖

### Admin
- `apps/admin/src/views/agent-instances/form.vue` — browser_sandbox checkbox
- `apps/admin/src/api/manager/agentInstances.ts` — runtime_config 类型
- `apps/admin/locales/zh-CN.yaml` + `en.yaml` — browserSandbox 文案

### Browser 镜像
- `engines/browser/Dockerfile` — FROM kasmweb/chrome + COPY cdp_proxy.py
- `engines/browser/cdp_proxy.py` — CDP 感知代理

## 六、部署 follow-up（已落 deploy 清单 ✅）
1. ✅ manager Role `controller-engine-manager` 加 secrets/configmaps/networkpolicies ——
   `deploy/ci/deployment.yaml` + `deploy/k8s/infra/manager-rbac.yaml` 已改，云已 `kubectl apply` 持久化。
2. ✅ manager env `UA_BROWSER_PVC_STORAGE_CLASS=local-path` —— `deploy/ci/deployment.yaml` manager 容器 env 已加，云已 set。
3. ✅ NetworkPolicy 9222 + engine label —— browser Pod 的 NP 由 manager 运行时创建（best-effort，
   k3s 不强制时 noop；云上 NP 已删）。engine pod label `unionagents.io/component=engine` 已在
   k8s_manager.create_engine 的 pod_labels 里（line ~493），新建 engine Pod 自带，旧 Pod 重建后生效。
4. ❌ `--remote-allow-origins=*` **不需要** —— cdp-proxy 走 127.0.0.1 loopback 连 chrome DevTools，
   chrome 141 对 loopback 放行，实测无此 flag CDP 也通（原 follow-up 误判）。
5. IfNotPresent + 复用 tag → 按_digest set image 或 crictl rmi（部署流程注意，未变）
6. ✅ browser-v2 镜像地址作为 browser_sidecar_image 默认值（已改 config.py）
