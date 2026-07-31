# Hermes Agent 网关 vs UnionAgents 网关 — IM 通道（企业微信）对比与借鉴方案

> 分析范围：Hermes Agent 源码 (`/Users/friday/workspace/hermes-agent-code/hermes-agent`) vs UnionAgents (`/Users/friday/workspace/union_agent`)
> 重点：企业微信对接、图片/文件上传下载展示

---

## 架构对比总览

| 维度 | Hermes Agent | UnionAgents (当前) |
|------|-------------|-------------------|
| IM 通道抽象 | `BasePlatformAdapter` ABC + 插件注册表，20+ 通道 | `BaseChannelAdapter` ABC + `@register` 装饰器，3 通道 |
| WeCom 模式 | WS Smart Robot (`adapter.py`) + HTTP Callback (`callback_adapter.py`) | HTTP Callback (`wecom.py`) + WS 透明桥 (`wecom_bot.py`) |
| 流式响应 | WeCom **不支持**（`SUPPORTS_MESSAGE_EDITING=False`，直接跳过，等整轮结束发完整消息） | WeCom **chunk-flush**（每满 2048 字节发新消息） |
| 会话管理 | SQLite SessionStore，key = `platform:chat_type:chat_id:user_id` | 确定性 `sha256(agent:channel:chat)[:24]`，30min 缓存 |

### Hermes 关键文件

| 组件 | 路径 |
|------|------|
| WeCom WebSocket 适配器 | `plugins/platforms/wecom/adapter.py` (1889 行) |
| WeCom Callback 适配器 | `plugins/platforms/wecom/callback_adapter.py` (444 行) |
| WeCom 加解密 (BizMsgCrypt) | `plugins/platforms/wecom/wecom_crypto.py` (142 行) |
| 平台适配器基类 | `gateway/platforms/base.py` (5735 行) |
| 平台注册表 | `gateway/platform_registry.py` |
| 网关主运行器 | `gateway/run.py` |
| 流式消费器 | `gateway/stream_consumer.py` |
| 流式事件分发 | `gateway/stream_dispatch.py` |
| 显示/流式分层配置 | `gateway/display_config.py` |

### UnionAgents 关键文件

| 组件 | 路径 |
|------|------|
| 网关入口 | `services/gateway/app/main.py` |
| WeCom Callback 适配器 | `services/gateway/app/channel/wecom.py` (924 行) |
| WeCom Bot WS 桥 | `services/gateway/app/channel/wecom_bot.py` (83 行) |
| 通道适配器基类 | `services/gateway/app/channel/base.py` |
| 消息模型 | `services/gateway/app/channel/models.py` |
| 通道注册表 | `services/gateway/app/channel/registry.py` |
| 卡片 JSON 提取 | `services/gateway/app/channel/card_utils.py` |
| 分发器 | `services/gateway/app/channel/dispatcher.py` |
| 路由器 | `services/gateway/app/channel/router.py` |
| 媒体解析器 | `services/gateway/app/media_resolver.py` |
| Profile 解析器 | `services/gateway/app/profile_resolver.py` |
| 生命周期管理 | `services/gateway/app/lifecycle.py` |
| 反向代理 | `services/gateway/app/proxy.py` |
| ASR 抽象层 | `services/gateway/app/asr/` |

---

## 可借鉴的点（图片/文件/媒体）

### 1. 统一媒体缓存层 + 安全校验（强烈推荐）

Hermes 在 `gateway/platforms/base.py` 实现了一套完整的本地媒体缓存体系，当前 union 缺失：

```
cache_image_from_bytes()   # 校验 magic bytes (PNG/JPEG/GIF/BMP/WEBP)
cache_image_from_url()     # SSRF 防护 (is_safe_url + redirect guard)
cache_document_from_bytes()
cache_media_bytes()        # 统一入口，按 MIME 自动分类
```

**关键安全机制**（`base.py:605-704`）：
- **Magic bytes 校验**：`_looks_like_image()` 检查文件头，防止把 HTML 错误页当图片缓存
- **SSRF 防护**：`is_safe_url()` + `_ssrf_redirect_guard`，防止内网地址探测
- **大小上限**：`DEFAULT_INBOUND_MEDIA_MAX_BYTES = 128MB`，按媒体类型细分（图片 10MB / 视频 10MB / 语音 2MB / 文件 20MB）
- **投递路径校验**：`validate_media_delivery_path()`（`base.py:1272`）— 允许列表（仅缓存目录）+ 拒绝列表（`~/.ssh`、`~/.aws`、`~/.hermes/.env` 等凭据路径）+ 可选 recency window

**当前 union 的问题**：`dispatcher._process_attachment`（`dispatcher.py:651-718`）直接下载 WeCom 媒体 → 写入 workspace `uploads/`，中间没有 magic bytes 校验、没有 SSRF 防护（虽然 WeCom URL 相对可信，但 `media_resolver.normalize_path` 缺少投递路径安全校验，理论上 engine 回复中的 `![](../../etc/passwd)` 路径可被解析）。

### 2. 文件大小超限自动降级（推荐）

Hermes 的 `_apply_file_size_limits()`（`adapter.py:986`）：

```python
IMAGE_MAX_BYTES = 10MB
VIDEO_MAX_BYTES = 10MB
VOICE_MAX_BYTES = 2MB
FILE_MAX_BYTES = 20MB
```

当图片超过 10MB 但低于 20MB 时，**自动降级为文件格式发送**，并附用户提示：
> "图片大小 12.34MB 超过 10MB 限制，已转为文件格式发送"

语音只支持 AMR，其他格式自动降级为文件。

**当前 union**：`_send_image` / `_send_file`（`wecom.py:408-448`）没有大小预检，`media/upload` 失败后才报错。WeCom `media/upload` 图片限制 2MB、文件限制 20MB，超限直接失败。

### 3. 媒体投递路径安全校验（强烈推荐）

Hermes 的 `validate_media_delivery_path()`（`base.py:1272`）值得直接移植到 union 的 `media_resolver.py`：

- **允许列表**：仅 `~/.hermes/cache/{images,audio,videos,documents}` + operator 配置的 `HERMES_MEDIA_ALLOW_DIRS`
- **拒绝列表**：凭据文件路径（`.ssh`、`.aws`、`.env`、`.gitconfig` 等）
- **严格模式**：可选 recency window，只允许最近 N 秒内创建的文件

当前 union 的 `normalize_path`（`media_resolver.py:59-81`）只做了前缀剥离和路径锚定，engine 回复中的 `![x](../../../etc/passwd)` 可被 `resolve_file_bytes` 解析读取。

### 4. WS Smart Robot 模式的分块上传协议（按需借鉴）

Hermes 的 WS 模式实现了 WeCom AI Bot 的**三步分块上传**（`adapter.py:1193-1249`）：

```
1. aibot_upload_media_init   → upload_id
2. aibot_upload_media_chunk  → 512KB chunks, base64, 最多 100 块
3. aibot_upload_media_finish → media_id
```

当前 union 的 `wecom_bot.py` 是**透明 WS 桥**，不处理消息内容。如果未来需要在 WS 桥模式下支持引擎主动发图片/文件给用户，需要实现这套协议。当前 callback 模式用 `media/upload` REST API 即可，不需要分块。

### 5. 入站媒体的 AES 解密（WS 模式专用）

Hermes WS 模式的 `_cache_media()`（`adapter.py:751`）处理两种入站媒体格式：
- `base64` 字段 → 直接解码缓存
- `url` 字段 → HTTP 下载（20MB 上限 + SSRF 防护）
- 若带 `aeskey` 字段 → AES-256-CBC 解密（key = base64 解码的 aeskey，IV = key 前 16 字节）

这是 WS Smart Robot 模式特有的，当前 union 透明桥不涉及。若未来改为非透明处理则需要。

### 6. MEDIA 标签协议 vs Markdown 解析（设计思路参考）

| | Hermes | Union |
|---|--------|-------|
| 引擎→平台媒体标记 | `MEDIA:/path/to/file` 文本标签 | `![alt](path)` / `[name](path)` Markdown |
| 提取方式 | 正则 `MEDIA_TAG_CLEANUP_RE` + 路径校验 → 按 extension 分发 `send_image`/`send_document`/`send_voice`/`send_video` | `find_local_image_matches` / `find_local_file_links` 正则 → `resolve_image_to_data_url` / `resolve_file_bytes` |

两种方案各有优劣：
- **Hermes 的 MEDIA 标签**更显式，不依赖 Markdown 语法，不会被 Markdown 渲染器误处理
- **Union 的 Markdown 解析**对引擎提示词要求更低（引擎天然会输出 Markdown），但解析容易出边界 case

不需要改，但 Hermes 的 `validate_media_delivery_path` + extension-based dispatch 逻辑可以加强现有 Markdown 解析的安全性。

### 7. 文本分片策略对比

| | Hermes | Union |
|---|--------|-------|
| 最大长度 | 4000 字符（WS 模式） | 2048 字节（callback 模式） |
| 分片方式 | 字符级 | UTF-8 字节级，不切断多字节字符，优先行边界 |
| 长消息批处理 | 客户端 4000 字符分片 → 0.6s/2.0s 延迟批处理合并 | 无 |

Hermes 的**客户端分片批处理**（`_enqueue_text_event`，`adapter.py:584`）值得借鉴：WeCom 客户端会将长消息按 ~4000 字符分割发送，Hermes 用延迟缓冲（0.6s 静默期 flush，接近 3900 字符阈值时延长到 2.0s）合并碎片，避免一条用户消息被拆成多次处理。当前 union 没有这个机制，可能存在长消息被拆分处理的问题。

---

## 企业微信功能成熟度对比

| 功能 | Hermes (WS 模式) | Hermes (Callback 模式) | Union (Callback 模式) | Union (WS 桥) |
|------|------------------|----------------------|----------------------|---------------|
| 加密 XML 回调 (AES) | — | ✅ AES-128-CBC | ✅ AES-256-CBC | — |
| SHA1 签名校验 | — | ✅ | ✅ | — |
| URL 验证 (GET echostr) | — | ✅ | ✅ | — |
| 文本发送 | ✅ markdown | ✅ markdown | ✅ markdown | 透明桥 |
| 卡片消息 | ❌ | ❌ | ✅ 6 种 msgtype | 透明桥 |
| 卡片原地更新 | ❌ | ❌ | ✅ update_template_card | — |
| 菜单点击事件 | ❌ | ❌ | ✅ | — |
| 语音 ASR | ✅ 仅 AMR | ❌ | ✅ 5 个 provider | — |
| 入站图片/文件 | ✅ base64/URL+AES | ❌ 仅 text/event | ✅ media/get → workspace | — |
| 出站图片 | ✅ 分块上传 | ❌ | ✅ media/upload | — |
| 出站文件 | ✅ 分块上传 | ❌ | ✅ media/upload | — |
| 流式响应 | ❌ 不支持 | ❌ 不支持 | ✅ chunk-flush | — |
| 主动推送 | ✅ aibot_send_msg | ✅ message/send | ✅ send endpoint + preset | — |
| access_token 管理 | ❌ WS 无需 | ✅ 7200s 缓存 | ✅ 7200s 缓存 | — |
| 多租户隔离 | ❌ | ✅ corp_id 作用域 | ✅ Profile + IM 用户绑定 | — |

---

## UnionAgents 已有优势（无需借鉴）

- **ASR 多供应商**：5 个 ASR provider（火山/阿里/腾讯/华为/本地），Hermes 仅支持 AMR
- **流式 chunk-flush**：WeCom 实时分段推送，Hermes 完全不流式
- **卡片消息**：template_card / textcard / news / mpnews 透传 + 原地更新，Hermes WS 模式无卡片
- **确定性 session_id**：跨重启稳定，Hermes 依赖 SQLite 持久化
- **多租户 Profile 隔离**：IM 用户绑定 + scope 推导，Hermes 无此层

---

## 建议优先级

| 优先级 | 借鉴项 | 工作量 | 理由 |
|--------|--------|--------|------|
| P0 | 媒体投递路径安全校验（防路径穿越） | 小 | 安全漏洞，`media_resolver.normalize_path` 缺凭据路径拒绝列表 |
| P0 | 入站媒体 magic bytes 校验 | 小 | 防止错误页/恶意内容被当图片写入 workspace |
| P1 | 文件大小超限自动降级 | 小 | 提升 UX，避免 `media/upload` 失败后才报错 |
| P1 | 客户端长消息分片批处理 | 中 | 解决 WeCom 客户端 ~4000 字符分片导致的消息拆分问题 |
| P2 | SSRF 防护（`is_safe_url`） | 小 | WeCom URL 可信度高，但防御性编程值得加 |
| P3 | WS 模式分块上传协议 | 大 | 仅在 WS 桥改为非透明处理时需要 |

---

## 关键源码引用

### Hermes Agent

| 功能 | 文件:行号 |
|------|----------|
| 媒体缓存基类 | `gateway/platforms/base.py:704-1659` |
| Magic bytes 校验 | `gateway/platforms/base.py:687` (`_looks_like_image`) |
| SSRF 防护 | `gateway/platforms/base.py:549` (`is_safe_url`) |
| 投递路径校验 | `gateway/platforms/base.py:1272` (`validate_media_delivery_path`) |
| 大小上限常量 | `gateway/platforms/base.py:605` (`DEFAULT_INBOUND_MEDIA_MAX_BYTES`) |
| 文件大小降级 | `plugins/platforms/wecom/adapter.py:986` (`_apply_file_size_limits`) |
| 分块上传协议 | `plugins/platforms/wecom/adapter.py:1193-1249` (`_upload_media_bytes`) |
| 入站媒体缓存 | `plugins/platforms/wecom/adapter.py:751` (`_cache_media`) |
| AES 解密 | `plugins/platforms/wecom/adapter.py:1063` (`_decrypt_file_bytes`) |
| 文本分片批处理 | `plugins/platforms/wecom/adapter.py:584` (`_enqueue_text_event`) |
| Callback access_token | `plugins/platforms/wecom/callback_adapter.py:421` (`_refresh_access_token`) |
| Callback 消息发送 | `plugins/platforms/wecom/callback_adapter.py:203` (`send`) |
| 流式跳过逻辑 | `gateway/run.py:18517-18524` |

### UnionAgents

| 功能 | 文件:行号 |
|------|----------|
| WeCom 签名校验 | `services/gateway/app/channel/wecom.py:161-175` |
| WeCom 解密 | `services/gateway/app/channel/wecom.py:97-107` |
| WeCom 消息解析 | `services/gateway/app/channel/wecom.py:201-337` |
| WeCom 文本发送 | `services/gateway/app/channel/wecom.py:631-660` |
| WeCom 卡片发送 | `services/gateway/app/channel/wecom.py:781-839` |
| WeCom 图片发送 | `services/gateway/app/channel/wecom.py:408-428` |
| WeCom 文件发送 | `services/gateway/app/channel/wecom.py:430-448` |
| WeCom 媒体上传 | `services/gateway/app/channel/wecom.py:385-406` |
| WeCom 媒体下载 | `services/gateway/app/channel/wecom.py:364-383` |
| WeCom access_token | `services/gateway/app/channel/wecom.py:341-362` |
| WeCom 流式 chunk-flush | `services/gateway/app/channel/wecom.py:744-777` |
| WeCom 语音转写 | `services/gateway/app/channel/wecom.py:506-529` |
| WeCom WS 桥 | `services/gateway/app/channel/wecom_bot.py:28-83` |
| 入站附件处理 | `services/gateway/app/channel/dispatcher.py:651-718` |
| 媒体路径解析 | `services/gateway/app/media_resolver.py:59-81` (`normalize_path`) |
| 图片解析 | `services/gateway/app/media_resolver.py:123-128` (`resolve_image_to_data_url`) |
| 文件解析 | `services/gateway/app/media_resolver.py:131-163` (`resolve_file_bytes`) |
| 确定性 session_id | `services/gateway/app/channel/dispatcher.py:478-500` |
| Profile 解析 | `services/gateway/app/profile_resolver.py:82-214` |
| 引擎 DNS 路由 | `services/gateway/app/adapter/base.py:34-42` (`build_engine_dns`) |
