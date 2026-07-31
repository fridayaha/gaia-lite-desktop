# 下载与导出体系设计

> 文档编号：validation/05
> 版本：v0.1
> 日期：2026-05-15
> 用途：设计 Hub 能力市场的下载、导出和包分发体系，区分管理态导出包与运行态能力包，定义 hash 校验和签名预留

---

## 1. 下载场景全景

Hub 提供三类下载，分别面向不同角色和场景：

| # | 下载类型 | 使用者 | 场景 | 阶段 |
|---|----------|--------|------|:---:|
| 1 | Manifest 下载 | 人 / Agent / Runtime | 查看或程序化读取能力清单定义 | P0 |
| 2 | 运行态能力包下载 | Agent / Runtime | Runtime 引入能力时获取完整可部署包 | P0 |
| 3 | 管理态导出包 | 人（管理员/维护者） | 备份、迁移、审计、离线分发 | P1 |

---

## 2. Manifest 下载

### 2.1 接口

```
GET /api/hub/items/{item_id}/versions/{version_id}/manifest
GET /api/hub/items/{item_id}/manifest                  → 取当前 published 版本
```

### 2.2 返回内容

返回该版本的 manifest_json 字段的原始内容，Content-Type: `application/json`。

**不是**返回 HubItemVersion 的全部字段 — 只返回 manifest。Runtime 需要完整配置应调用 Resolve（见 `validation/04`）。

### 2.3 访问控制

| 状态 | 是否可下载 | 说明 |
|------|:---:|------|
| published | ✅ | 正式发布版本，对外可下载 |
| deprecated | ✅ | 已被替代但仍有引用方需要 |
| approved | ✅ | 已审批通过但尚未发布（管理态可下载，运行态不推荐） |
| draft / pending_review / rejected / change_required | ❌ | 未完成审批的版本不可对外 |
| archived | ❌ | 归档版本不可下载 |

### 2.4 manifest_hash

每次 manifest 内容变更时计算 SHA256 摘要，存储在 `HubItemVersion.manifest_hash` 字段中：

```
manifest_hash = SHA256(JSON.stringify(manifest_json, sorted_keys))
```

**用途**：
- 下载时返回 `X-Content-Hash: sha256:abc123...` 响应头
- 客户端可校验下载完整性
- 后续包签名时，manifest_hash 作为签名内容的一部分

---

## 3. 运行态能力包下载

### 3.1 接口

```
GET /api/hub/items/{item_id}/versions/{version_id}/package
```

### 3.2 内容结构（动态构建的 ZIP）

```
{name}-{version}.zip
├── manifest.json          ← 该版本的 manifest_json（完整）
├── config.json            ← 该版本的 config_json
├── input_schema.json      ← 如果存在
├── output_schema.json     ← 如果存在
├── permission.json        ← 如果存在
├── runtime.json           ← 如果存在（runtime_compatibility）
├── README.md              ← 如果附件中有
├── examples/              ← 如果附件中有
│   └── ...
├── prompts/               ← 如果附件中有（Agent 类型）
│   └── system_prompt.txt
└── assets/                ← 如果附件中有
    └── ...
```

### 3.3 包构建流程

```
1. 根据版本 ID 读取 HubItemVersion
2. 读取 manifest_json / config_json → 写入对应文件
3. 读取 input_schema / output_schema / permission_json / runtime_compatibility → 如非空写入
4. 读取该版本的附件文件列表 → 按目录结构写入
5. 动态构建 ZIP → streaming response
6. 设置响应头：
   Content-Type: application/zip
   Content-Disposition: attachment; filename="{name}-{version}.zip"
   X-Package-Hash: sha256:def456...
```

### 3.4 package_hash

构建 ZIP 时计算 SHA256，存储在 `HubItemVersion.package_hash`：

```
package_hash = SHA256(zip_bytes)
```

**注意**：`package_hash` 与 `manifest_hash` 不同：
- `manifest_hash` 只覆盖 manifest.json 内容
- `package_hash` 覆盖整个 zip 包

### 3.5 附件管理

能力包可包含以下附件文件（在导入时一并上传，存储为关联文件）：

| 附件类型 | 目录 | 说明 |
|----------|------|------|
| README | `/` | 能力说明文档 |
| 示例 | `examples/` | 使用示例 |
| Prompt 模板 | `prompts/` | Agent 类型的 system prompt 等 |
| Schema 文件 | `schemas/` | 额外的 JSON Schema |
| 静态资源 | `assets/` | 图标、截图等 |
| 参考文档 | `docs/` | 详细参考文档 |

**存储策略**：
- PoC 阶段：附件内容存储在 JSON 字段中（简单直接）
- 准生产阶段：接入 MinIO / S3，附件以文件形式存储，HubItemVersion 存储文件路径引用

---

## 4. 管理态导出包

### 4.1 接口

```
GET /api/hub/items/{item_id}/export
```

### 4.2 内容结构（完整的 Item 管理数据）

```
{name}-export-{timestamp}.zip
├── item.json                   ← HubItem 全部字段
├── versions/                   ← 所有版本
│   ├── v0.1.0/
│   │   ├── version.json        ← HubItemVersion 全部字段
│   │   ├── manifest.json
│   │   ├── config.json
│   │   ├── scan_report.json    ← 该版本的扫描报告
│   │   └── ...
│   ├── v0.2.0/
│   │   └── ...
│   └── v1.0.0/
│       └── ...
├── approvals.json              ← 所有审批记录
├── lifecycle_events.json       ← 所有生命周期事件
├── relations.json              ← 所有关系记录（incoming + outgoing）
├── tags.json                   ← 关联的标签
└── category.json               ← 关联的分类
```

### 4.3 与运行态能力包的区别

| 维度 | 运行态能力包 | 管理态导出包 |
|------|-------------|-------------|
| **目的** | Runtime 引入并使用能力 | 备份、迁移、审计、离线分发 |
| **范围** | 单个版本 | 完整 Item（所有版本 + 所有审计数据） |
| **格式** | 能力部署格式（manifest/config/schema/附件） | 管理数据格式（item.json + versions + approvals + events） |
| **使用者** | Agent / Runtime | 人（管理员/维护者） |
| **可重新导入** | 是（作为新版本或更新） | 是（作为完整 Item 恢复） |
| **包含审批记录** | 否 | 是 |
| **包含生命周期事件** | 否 | 是 |
| **包含历史版本** | 否 | 是 |
| **P0 / P1** | P0 | P1（暂缓） |

---

## 5. Hash 体系

### 5.1 Hash 字段对照

| Hash 字段 | 覆盖范围 | 存储位置 | 计算时机 |
|-----------|----------|----------|----------|
| `manifest_hash` | manifest_json 内容（sorted keys JSON → SHA256） | HubItemVersion | manifest 创建/更新时 |
| `package_hash` | 运行态能力包 ZIP → SHA256 | HubItemVersion | 首次下载时计算并缓存（或导入时预计算） |
| `export_hash` | 管理态导出包 ZIP → SHA256 | 不持久化（按需计算） | 导出时动态计算 |

### 5.2 下载时的 Hash 校验

```
客户端下载 → 读取 X-Package-Hash 响应头
           → 本地计算下载内容的 SHA256
           → 比对 → 一致则完整，不一致则重新下载
```

### 5.3 HubItemVersion 新增字段（迁移）

```sql
ALTER TABLE hub_item_versions ADD COLUMN manifest_hash VARCHAR(128);
ALTER TABLE hub_item_versions ADD COLUMN package_hash VARCHAR(128);
```

两个字段均可空，后续在 manifest 变更和下载时异步填充。

---

## 6. 签名预留

### 6.1 签名场景（当前不做，结构预留）

| 签名对象 | 签名内容 | 用途 |
|----------|----------|------|
| 能力包签名 | sign(manifest_hash + package_hash + publisher_id + version) | 验证能力包来源可信、内容未被篡改 |
| 导出包签名 | sign(export_hash + export_timestamp + operator_id) | 验证导出包在传输中未被篡改 |

### 6.2 预留字段

在 `HubItemVersion` 中预留：

```sql
ALTER TABLE hub_item_versions ADD COLUMN signature TEXT;        -- 数字签名
ALTER TABLE hub_item_versions ADD COLUMN sign_algorithm VARCHAR(20);  -- 签名算法（ed25519 / ecdsa）
ALTER TABLE hub_item_versions ADD COLUMN sign_key_id VARCHAR(100);    -- 签名密钥标识
```

### 6.3 签名验证流程（预留）

```
1. 本地计算 manifest_hash + package_hash
2. 使用 Hub 公钥验证签名
3. 验证通过 → 能力包来源可信
4. 验证失败 → 拒绝加载，记录安全事件
```

**当前阶段不实现**签名验证，字段仅预留。原因是 PoC/准生产阶段的能力包在内部可信环境中分发，签名价值在跨组织分发时才体现。

---

## 7. 下载相关文件管理

### 7.1 文件清单

一个能力包除核心配置外可包含以下文件。它们由**上传者**在导入或创建版本时提供，存储在版本关联的文件列表中。

| 文件 | 用途 | 适用类型 | 阶段 |
|------|------|:---:|:---:|
| `README.md` | 能力说明文档 | 全部 | P0 |
| `examples/` | 使用示例（JSON/代码片段） | 全部 | P1 |
| `prompts/` | Prompt 模板 | Agent | P1 |
| `schemas/` | 额外 JSON Schema 文件 | Tool / Skill | P1 |
| `assets/` | 图标/截图 | 全部 | P1 |
| `docs/` | 详细参考文档 | 全部 | P1 |

### 7.2 文件存储模型（预留）

```
version_attachments:
  id: UUID
  hub_item_version_id: UUID FK
  file_name: VARCHAR(500)      -- 如 "README.md"
  file_path: VARCHAR(1000)     -- 如 "/examples/sample.py"
  content_type: VARCHAR(100)   -- 如 "text/markdown"
  size_bytes: INTEGER
  storage_key: VARCHAR(1000)   -- MinIO/S3 object key
  sha256: VARCHAR(128)
  created_at: TIMESTAMPTZ
```

---

## 8. P0 / P1 实现建议

### 8.1 P0（阶段 4 实现）

| # | 任务 | 依赖 |
|---|------|------|
| 1 | Manifest 下载 API（by version + by current） | 无 |
| 2 | manifest_hash 计算与存储 | 在 manifest 变更时更新 |
| 3 | 运行态能力包下载（动态构建 ZIP） | 无 |
| 4 | package_hash 计算与存储 | 首次下载时缓存 |
| 5 | 下载响应头（Content-Type + Content-Disposition + Hash） | #1-#4 |
| 6 | 下载访问控制（published/deprecated/approved 可下载） | 无 |
| 7 | tests: manifest 下载 + 能力包下载 + hash 校验 | #1-#6 |

### 8.2 P1（后续阶段）

| # | 任务 | 说明 |
|---|------|------|
| 1 | 管理态导出包（完整 Item + 所有版本 + 审计数据 + ZIP） | 备份/迁移需求出现时 |
| 2 | 版本附件管理模型 + 上传/存储/下载 | 准生产阶段，接入 MinIO/S3 |
| 3 | 下载令牌（短时效 token，供 Agent 下载） | 接入 IAM 后 |
| 4 | 签名字段迁移 + 签名验证预留接口 | 跨组织分发前 |

---

## 9. 与其他模块的关系

```
03_item_relation_design.md
  └── HubItemRelation
        ↓
04_runtime_discover_design.md
  └── Resolve API → 返回 manifest + config + dependencies
        ↓
05_download_export_design.md (本文档)
  └── 下载 API → manifest + 能力包 + 导出包
        ↓
          HubItemVersion
          ├── manifest_hash   ← 本文档定义
          ├── package_hash    ← 本文档定义
          ├── signature       ← 预留给 06
          ├── sign_algorithm  ← 预留给 06
          └── sign_key_id     ← 预留给 06
```

---

> 配套文档：
> - `docs/validation/04_runtime_discover_design.md` — Resolve 接口返回配置，下载接口返回包
> - `docs/validation/06_manifest_spec_design.md` — manifest 结构定义是下载 manifest.json 的内容规范
> - `docs/14_hub_capability_market_solution_design.md` — 整体方案设计（§8 下载与导出）
