# 对象存储适配器设计

版本：v0.3 | 日期：2026-05-29 | 状态：**P1 LocalStorageAdapter 已实现**（633 tests baseline）。P2 S3/MinIO 未接入，P3 生命周期待实现。MT-4 tenant prefix 设计见 `docs/24_multi_tenancy_design.md`。

---

## 一、当前存储现状

**结论：Hub 当前完全没有持久化文件存储。** 所有数据均在内存或数据库 JSON 列中。

### 1.1 详细现状

| 场景 | 当前方式 | 持久化？ |
|------|----------|:---:|
| 上传原始包（ZIP/JSON/YAML） | `UploadFile.file.read()` → `bytes`，解析后丢弃 | ❌ |
| OpenAPI 导入原始 spec | `file.file.read()` → `bytes`，解析后丢弃 | ❌ |
| 导出版本包 (.zip) | `io.BytesIO` + `zipfile`，每次动态生成 | ❌ |
| 导出管理包 (.zip) | `io.BytesIO` + `zipfile`，每次动态生成 | ❌ |
| 扫描报告 | DB 表 `scan_reports` + `scan_findings`（JSON 列） | ✅ DB only |
| 扫描证据 | `ScanFinding.evidence` JSON 列（内联文本） | ✅ DB only |
| Manifest/Schema/Config | DB JSON 列（`manifest_json`, `config_json`, ...） | ✅ DB only |
| 临时文件 | 无 — 所有解析/生成均用 `io.BytesIO` | N/A |
| 对象存储 / S3 | 配置、客户端、SDK 均不存在 | N/A |

### 1.2 对当前 API 的影响

| API | 受对象存储影响？ |
|-----|:---:|
| `POST /hub/imports/package` | ✅ 上传原始包需保存 |
| `POST /hub/imports/openapi` | ✅ 原始 spec 需保存 |
| `GET /hub/exports/items/{id}/versions/{vid}/package` | ✅ 可缓存生成的包 |
| `GET /hub/exports/items/{id}` | ✅ 可缓存生成的包 |
| `POST /hub/versions/{version_id}/scan` | ✅ 扫描证据可保存附件 |
| `GET /hub/discover`（Runtime） | ✅ Runtime manifest 可缓存 |
| `GET /hub/resolve/{id}`（Runtime） | ✅ Runtime manifest 可缓存 |

### 1.3 已知风险

- 上传包无原始文件留存，无法审计"当初到底上传了什么"；
- 导出包每次动态生成，无缓存，重复请求浪费 CPU；
- 外部扫描器（Betterleaks / Gitleaks / Semgrep）需要临时文件输入，当前无处存放；
- 扫描证据仅存 DB JSON 列，无法附加大文件（SARIF / SBOM）；
- 后续 PostgreSQL 部署下 JSON 列膨胀会影响查询性能。

---

## 二、对象存储目标

### 2.1 要解决的问题

| # | 目标 | 优先级 |
|:---:|------|:---:|
| 1 | 上传原始包保存（审计/追溯） | P1 |
| 2 | 导出包缓存（避免每次动态生成） | P1 |
| 3 | Runtime manifest 缓存 | P2 |
| 4 | 外部扫描器临时输入材料 | P2 |
| 5 | 扫描证据 / report 附件保存 | P2 |
| 6 | 后续大文件资源（SBOM/SARIF 等） | P3 |
| 7 | presigned URL 下载 | P2（S3 only） |

### 2.2 不是目标

- 不做 CDN 加速；
- 不做对象版本管理（依赖 Hub 自身版本管理）；
- 不做跨 region 复制；
- 不做实时流式上传；
- 不做 Glacier / 冷热分层；
- 不做对象存储替代数据库（主数据仍在 PostgreSQL）。

---

## 三、存储对象分类与 Key 设计

### 3.1 Object Key 命名规范

```
UnionAgent-Hub/
  packages/
    {item_id}/
      {version_id}/
        original.{ext}         # 原始上传包（.zip / .json / .yaml）
  exports/
    runtime/
      {item_id}/
        {version_id}/
          runtime.json         # Runtime manifest（缓存）
          tool_definition.json # Tool → Function Calling 定义（缓存）
    package/
      {item_id}/
        {version_id}/
          capability.zip       # 版本能力包（缓存）
    management/
      {export_id}.zip           # 管理态导出包（缓存）
  scans/
    {item_id}/
      {version_id}/
        {scan_report_id}/
          evidence.json         # 扫描证据汇总
          external/             # 外部扫描器报告
  imports/
    openapi/
      {item_id}/
        {import_id}/
          original.{ext}        # 原始 OpenAPI spec
  temp/
    scanner/
      {request_id}/             # 外部扫描器输入材料（临时）

### 3.2 对象生命周期分类

| 类别 | 路径前缀 | 生命周期 | 清理策略 |
|------|----------|:---:|------|
| 上传原始包 | `packages/` | 长期 | 跟随 item/version 生命周期，归档时保留 |
| Runtime manifest | `exports/runtime/` | 缓存 | 版本变更时失效，可 LRU 清理 |
| 导出版本包 | `exports/package/` | 缓存 | 版本变更时失效，可 LRU 清理 |
| 管理导出 | `exports/management/` | 临时 | TTL 24h 后清理（或按需保留） |
| 扫描证据 | `scans/` | 长期 | 跟随 scan_report 生命周期 |
| 外部扫描器输入 | `temp/scanner/` | 临时 | 扫描完成后立即清理 |
| OpenAPI 原始 spec | `imports/openapi/` | 长期 | 跟随 item 生命周期 |

### 3.3 缓存失效规则

| 对象 | 失效条件 |
|------|----------|
| `runtime.json` | version.status 变更或 manifest_json/config_json/permission_json 任一更新 |
| `capability.zip` | version 变更、relations 变更、依赖 version 变更 |
| `{export_id}.zip` | 固定 TTL 或按需人工刷新 |

P1 阶段不实现自动失效，采用：
- 导出版本时检查 DB 数据 hash，不匹配则重新生成；
- Runtime manifest 缓存可由版本发布事件触发 invalidate（P2）。

---

## 四、StorageAdapter 设计

### 4.1 StorageAdapter 接口

```python
class StorageAdapter(Protocol):
    """对象存储适配器接口。"""

    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        """上传字节数据。"""
        ...

    def get_bytes(self, key: str) -> bytes:
        """下载字节数据。不存在时抛出 KeyError。"""
        ...

    def exists(self, key: str) -> bool:
        """检查 key 是否存在。"""
        ...

    def delete(self, key: str) -> None:
        """删除对象。"""
        ...

    def presign_get_url(self, key: str, expires_seconds: int) -> str:
        """生成预签名下载 URL。仅 S3 实现，Local 返回 file:// 或 NotImplementedError。"""
        ...

    def put_file(self, local_path: str, key: str, content_type: str = "application/octet-stream") -> None:
        """从本地文件上传。P2 实现，P1 仅 put_bytes。"""
        ...

    def get_file(self, key: str, local_path: str) -> None:
        """下载到本地文件。P2 实现，P1 仅 get_bytes。"""
        ...
```

### 4.2 实现路线

| 实现 | 阶段 | 说明 |
|------|:---:|------|
| `LocalStorageAdapter` | P1 | 本地文件系统，root 可配置，用于开发环境 |
| `S3StorageAdapter` | P2 | S3/MinIO 兼容，boto3 依赖 |
| `InMemoryStorageAdapter` | P1（测试） | dict 存储，用于单元测试 |

### 4.3 LocalStorageAdapter 约束

```python
class LocalStorageAdapter:
    def __init__(self, root: str):
        self._root = Path(root).resolve()

    def _resolve(self, key: str) -> Path:
        # 路径安全：key 只允许 a-z A-Z 0-9 . _ - / 字符
        # 禁止 .. / 绝对路径 / 符号链接遍历
        # resolve 后必须在 self._root 内
```

**LocalStorageAdapter 关键要求**：
- `root` 默认 `.hub_storage/`（项目根目录下的隐藏目录）；
- `.gitignore` 已包含 `.hub_storage/`；
- 路径必须在 `root` 子树内，拒绝 `..` 穿越；
- key 只允许 ASCII 安全字符；
- 不存在文件时 `get_bytes` 抛出 `KeyError`（不返回 None）。

### 4.4 S3StorageAdapter 约束

- 依赖 `boto3`（P2 阶段引入）；
- 支持 S3 兼容服务（MinIO / Ceph RGW / AWS S3）；
- `presign_get_url` 支持可配置过期时间；
- 不在日志中打印 access key / secret key；
- Connection 失败时抛出明确异常。

### 4.5 InMemoryStorageAdapter

```python
class InMemoryStorageAdapter:
    def __init__(self):
        self._store: dict[str, bytes] = {}
```

仅用于单元测试，不用于生产。每次 put 覆盖，delete 移除。

---

## 五、配置项设计

### 5.1 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `HUB_STORAGE_BACKEND` | `local` | `local` / `s3` / `memory` |
| `HUB_STORAGE_LOCAL_ROOT` | `.hub_storage` | Local 模式根目录 |
| `HUB_S3_ENDPOINT` | `` | S3 endpoint URL |
| `HUB_S3_BUCKET` | `hub-storage` | S3 bucket 名 |
| `HUB_S3_REGION` | `us-east-1` | S3 region |
| `HUB_S3_ACCESS_KEY_ID` | `` | S3 access key |
| `HUB_S3_SECRET_ACCESS_KEY` | `` | S3 secret key |
| `HUB_S3_FORCE_PATH_STYLE` | `false` | MinIO 必须设为 `true` |
| `HUB_STORAGE_PRESIGN_EXPIRES_SECONDS` | `3600` | pre-signed URL 有效期（秒） |

### 5.2 Settings 类

```python
class Settings(BaseSettings):
    # ... 现有字段 ...

    # Storage
    storage_backend: str = Field(default="local", alias="HUB_STORAGE_BACKEND")
    storage_local_root: str = Field(default=".hub_storage", alias="HUB_STORAGE_LOCAL_ROOT")
    s3_endpoint: str | None = Field(default=None, alias="HUB_S3_ENDPOINT")
    s3_bucket: str = Field(default="hub-storage", alias="HUB_S3_BUCKET")
    s3_region: str = Field(default="us-east-1", alias="HUB_S3_REGION")
    s3_access_key_id: str | None = Field(default=None, alias="HUB_S3_ACCESS_KEY_ID")
    s3_secret_access_key: str | None = Field(default=None, alias="HUB_S3_SECRET_ACCESS_KEY")
    s3_force_path_style: bool = Field(default=False, alias="HUB_S3_FORCE_PATH_STYLE")
    storage_presign_expires_seconds: int = Field(default=3600, alias="HUB_STORAGE_PRESIGN_EXPIRES_SECONDS")
```

### 5.3 安全要求

- `HUB_S3_SECRET_ACCESS_KEY` 不写入日志、不写入导出包、不写入 DB；
- `.hub_storage/` 在 `.gitignore` 中已添加或本次一并添加；
- pre-signed URL 过期时间可配置，默认 1 小时；
- Local adapter 禁止通过 key 访问 `root` 之外的路径。

---

## 六、接入阶段设计

### 6.1 P1：LocalStorageAdapter（当前建议优先实施）

| 任务 | 说明 |
|------|------|
| StorageAdapter Protocol + 配置 | `backend/app/adapters/storage.py` |
| LocalStorageAdapter | 本地文件存储，路径安全 |
| InMemoryStorageAdapter | 测试用 |
| `.gitignore` 添加 `.hub_storage/` | 禁止提交本地存储 |
| 上传原始包保存 | import service 调用 `storage.put_bytes` |
| 导出包缓存 | export service 缓存 capability.zip / management export |
| OpenAPI 原始 spec 保存 | openapi import service 保存原始文件 |
| 日志记录 storage key | event log 记录 key（不记录内容） |
| 测试：10 项核心测试 | 见第九章 |
| **不改 DB** | 使用 summary / evidence 临时记录 key |

**P1 输出**：
- `backend/app/adapters/storage.py`：接口 + 实现；
- `backend/app/core/storage.py`：依赖注入 / get_storage；
- 现有 import/export/openapi 服务接入；
- 对外 API 无变化；
- 测试基线 ≥ 566 passed。

### 6.2 P2：S3 / MinIO Adapter

| 任务 | 说明 |
|------|------|
| 引入 `boto3` 依赖 | `pyproject.toml` 添加可选依赖 |
| S3StorageAdapter | 完整 S3 适配器 |
| pre-signed URL 下载 | replace direct download with redirect |
| 扫描证据附件保存 | scan service 写入 storage |
| 外部 scanner 临时文件 | 写入 temp/scanner/ |
| 部署文档 | MinIO 部署 + 配置说明 |

### 6.3 P3：生命周期管理

| 任务 | 说明 |
|------|------|
| temp 清理 | TTL 扫描清理 |
| retention policy | 导出包缓存过期清理 |
| object hash 校验 | SHA-256 内容校验 |
| SBOM / SARIF 附件 | 扫描报告附件保存 |

---

## 七、与当前功能的关系

### 7.1 import package（`POST /hub/imports/package`）

**当前**：UploadFile → `bytes` → parse → DB insert → discard

**P1 改动**：
1. 解析成功后，`storage.put_bytes(key, original_bytes, content_type)` 保存原始包；
2. key：`packages/{item_id}/{version_id}/original.{json/yaml/zip}`；
3. key 写入 version 的 `summary` 或后续 `storage_objects` 表；
4. 解析失败时不保存；
5. API 响应不变。

### 7.2 OpenAPI import（`POST /hub/imports/openapi`）

**当前**：UploadFile → `bytes` → parse → for each operation: DB insert → discard

**P1 改动**：
1. 解析成功后，保存原始 spec 文件：`imports/openapi/{item_id}/{import_id}/original.{json/yaml}`；
2. key 写入 import summary；
3. 仅 parse 成功时保存。

### 7.3 export runtime manifest（already via Runtime API）

**当前**：每次从 DB 读取 manifest_json / input_schema / output_schema / permission_json → 组装 dict → return

**P1 改动**：
1. 导出时写入 `exports/runtime/{item_id}/{version_id}/runtime.json`；
2. 下次请求检查缓存是否有效（version hash 比对）；
3. 命中则直接从 storage 返回（P2 用 pre-signed URL）。

### 7.4 export version package（`GET /hub/exports/items/{id}/versions/{vid}/package`）

**当前**：每次从 DB 拼装 ZIP → `io.BytesIO` → HTTP response

**P1 改动**：
1. 生成 ZIP 后 `storage.put_bytes("exports/package/{item_id}/{version_id}/capability.zip", zip_bytes)`；
2. 版本不变时直接返回缓存的 ZIP；
3. 版本变更（relations 变化、依赖更新）时 invalidate。

### 7.5 export management package（`GET /hub/exports/items/{id}`）

**当前**：每次从 DB 拼装 ZIP → `io.BytesIO` → HTTP response

**P1 改动**：
1. 生成 ZIP 后 `storage.put_bytes("exports/management/{export_id}.zip", zip_bytes)`；
2. TTL 24h，或按需人工 refresh；
3. 可配置 `HUB_EXPORT_CACHE_TTL`（后续）。

### 7.6 scan report / external scanner

**当前**：scan 结果全部存入 DB 的 `scan_reports` + `scan_findings` 表

**P1 改动**：
- 暂不接入 storage（scan 证据仍存 DB JSON 列）；
- 为后续 P2 预留 key 设计。

**P2 改动**：
1. 外部 scanner 输入材料写入 `temp/scanner/{request_id}/`；
2. 扫描完成后清理 temp；
3. 外部 scanner JSON report 可选保存到 `scans/{item_id}/{version_id}/{scan_report_id}/external/`；
4. 证据文件（SARIF / SBOM）保存到 storage，evidence 中记录 key。

### 7.7 Runtime resolve / discover

**当前**：每次从 DB 查询 + 组装

**P1 改动**：暂不接入 storage。

**P2 改动**：
1. `runtime.json` 缓存到 storage；
2. `tool_definition.json` 缓存到 storage；
3. version 变更时 invalidate。

---

## 八、数据库是否需要扩展

### 8.1 方案 A：不改 DB（P1 推荐）

**策略**：
- key 写入现有的 `summary`（ScanReport）、`evidence`（ScanFinding）、version 的 JSON 字段；
- 结构性弱但快速落地；
- 无 migration 负担。

**临时 key 存放位置**：

| 对象 | 记录位置 |
|------|----------|
| 上传原始包 key | `HubItemVersion.change_log` → `{"original_package_key": "..."}` |
| OpenAPI 原始 spec key | import API response `summary` |
| 导出版本包 key | export API response（不持久化，仅检测存在/失效） |
| 扫描证据 key | `ScanFinding.evidence` → `{"storage_key": "..."}` |

### 8.2 方案 B：新增对象表（P2 推荐）

```sql
CREATE TABLE storage_objects (
    id UUID PRIMARY KEY,
    object_key VARCHAR(1024) NOT NULL,
    content_type VARCHAR(255),
    object_size BIGINT,
    object_hash VARCHAR(128),    -- SHA-256
    storage_backend VARCHAR(50),  -- "local" / "s3"
    item_id UUID REFERENCES hub_items(id),
    version_id UUID REFERENCES hub_item_versions(id),
    scan_report_id UUID REFERENCES scan_reports(id),
    category VARCHAR(50),        -- "package" / "export" / "scan" / "import"
    created_at TIMESTAMP,
    expires_at TIMESTAMP,        -- NULL = permanent
    UNIQUE(object_key)
);
```

**优势**：结构化，可查询，可做生命周期管理
**劣势**：需要 Alembic migration，P1 落地慢

### 8.3 推荐决策

**P1 (LocalStorageAdapter)：方案 A，不改 DB。**
- key 放入现有 JSON 字段；
- 等 P2 S3 接入时再设计 storage_objects 表。

---

## 九、测试计划

P1 至少以下测试：

| # | 测试项 | 说明 |
|:---:|------|------|
| 1 | `LocalStorageAdapter.put/get` | put bytes → get bytes 相等 |
| 2 | `LocalStorageAdapter.exists` | 存在返回 True，不存在返回 False |
| 3 | `LocalStorageAdapter.delete` | put → delete → exists=False |
| 4 | 路径防穿越 | key 含 `../` 抛出异常 |
| 5 | 根外路径拒绝 | key 解析后不在 root 内，抛出异常 |
| 6 | `InMemoryStorageAdapter` 基本操作 | put/get/exists/delete |
| 7 | import package 保存 original.zip | 导入后 storage 中有原始包 |
| 8 | export package 缓存 | 第一次生成，第二次命中缓存 |
| 9 | OpenAPI import 保存 spec | 导入后 storage 中有原始 spec |
| 10 | storage failure 不影响业务 | storage put 失败时 import 仍成功（降级） |
| 11 | 现有 556 tests 继续通过 | 无回归 |

P2 补充：
- `S3StorageAdapter` 集成测试（需 MinIO）；
- pre-signed URL 测试；
- scanner temp 文件测试；
- 并发安全（可选）。

---

## 十、文档计划

| 文档 | 操作 | 内容 |
|------|:---:|------|
| `docs/19_storage_adapter_design.md` | 新增 | 本文档 |
| `docs/02_solution_design.md` | 更新 | 测试基线、架构补充、后续路线 |
| `docs/03_platform_integration.md` | 更新 | 对象存储状态更新（底座要求） |
| `docs/08_roadmap_workload.md` | 更新 | 新增 P1/P2/P3 存储任务 |
| `README.md` | 更新 | 对象存储状态、项目结构补充 |
| `docs/engineering_evidence/storage_adapter.md` | 新增 | P1 实施后补充能力证据 |

---

## 十一、风险点

| # | 风险 | 严重度 | 缓解措施 |
|:---:|------|:---:|------|
| 1 | LocalStorageAdapter 路径穿越 | high | 强制 resolve + 前缀检查 + key 白名单字符 |
| 2 | storage 不可用时 import/export 500 | medium | 降级策略：storage put 失败不影响业务，只记录 warn log |
| 3 | `.hub_storage/` 目录膨胀 | low | 限制导出缓存 TTL，后续 P3 清理 |
| 4 | `io.BytesIO` 大文件 OOM | low | 后续 P2 put_file/get_file 流式传输 |
| 5 | 缓存失效判断不准 | medium | 使用 DB 数据 hash 比对，不依赖时间戳 |
| 6 | S3 credentials 泄露 | high | 不在日志/导出包/DB 中记录 secret key |
| 7 | pre-signed URL 过期后仍被缓存 | low | 缓存 TTL < URL 过期时间 |
| 8 | S3 / MinIO 网络超时 | medium | subprocess 超时 + retry（P2 实现） |
| 9 | key 冲突（同 item 同 version 重复上传） | low | key 使用 UUID 或递增 import_id |
| 10 | storage 和 DB 数据不一致 | medium | 写入顺序：先 storage → 后 DB；读取顺序：先 DB → 后 storage fallback |

---

## 十二、AGENTS.md 需补充

在 AGENTS.md "技术栈" 表格增加：

| 对象存储 Adapter | LocalStorageAdapter（P1）/ S3StorageAdapter（P2） |

在 "项目结构" 补充：

```
├── adapters/
│   └── storage.py       # StorageAdapter Protocol + 实现
├── core/
│   └── storage.py       # get_storage 依赖注入
```

---

## 十三、不做事项（本轮设计阶段）

- 不写代码；
- 不接真实 S3 / MinIO；
- 不引入 boto3 / minio SDK；
- 不改 DB；
- 不改前端；
- 不修改 demo worktree；
- 不实现生命周期清理；
- 不接对象签名；
- 不做大文件压测；
- 不做多 bucket 支持；
- 不实现 CDN / 加速域名。
