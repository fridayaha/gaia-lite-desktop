# Manifest Spec v0.1 设计

> 文档编号：validation/06
> 版本：v0.1
> 日期：2026-05-15
> 用途：定义 Agent / Skill / Tool / MCP 四类能力的 manifest 规范，包括通用字段、类型特有字段、校验策略、自动修正和 AI 标注预留

---

## 1. manifest_version

### 1.1 含义

`manifest_version` 是 **Hub 解析规范的版本号**，不是能力的版本号。

| 字段 | 含义 | 示例 |
|------|------|------|
| `manifest_version` | 本 manifest 遵循的 Hub Spec 版本 | `"0.1"` |
| `version` | 能力自身的语义化版本 | `"1.2.0"` |

### 1.2 版本演进策略

| manifest_version | 阶段 | 说明 |
|:---:|------|------|
| `0.1` | PoC → 准生产 | 当前版本，定义基础结构 |
| `0.2` | 后续 | 增加更多校验规则、类型特有字段扩展 |
| `1.0` | 生产 | 稳定规范，向后兼容 0.x |

### 1.3 校验规则

| 场景 | 行为 |
|------|------|
| `manifest_version` 缺失 | 拒绝导入，返回错误："manifest_version is required" |
| `manifest_version` 不是已知版本 | 告警 + 尝试按最接近的已知版本解析。若解析失败则拒绝 |
| `manifest_version` 高于 Hub 支持的最高版本 | 告警："manifest version X.Y is newer than Hub supported Z.W, some fields may be ignored" |

---

## 2. 通用字段（所有类型必须包含）

| 字段 | 类型 | 必填 | 说明 | 校验规则 |
|------|------|:---:|------|----------|
| `manifest_version` | string | ✅ | Hub Spec 版本 | 必须是合法的版本号字符串 |
| `name` | string | ✅ | 能力名称 | 字母/数字/中划线/下划线/中文，最长 200 |
| `type` | string | ✅ | 能力类型 | 必须是 `agent` / `mcp` / `skill` / `tool` |
| `version` | string | ✅ | 能力自身版本号 | 语义化版本号（MAJOR.MINOR.PATCH 或 MAJOR.MINOR） |
| `description` | string | ✅ | 能力描述 | 最长 2000 字符 |
| `category` | string | — | 所属分类 | 与预设 Category 匹配，不匹配时**告警不拒绝** |
| `tags` | string[] | — | 标签列表 | 字符串数组，自动去重 trim |
| `industry` | string | — | 适用行业 | 最长 100 |
| `scenario` | string | — | 适用场景 | 最长 100 |
| `input_schema` | object | — | 输入 JSON Schema | 必须是合法 JSON Schema |
| `output_schema` | object | — | 输出 JSON Schema | 必须是合法 JSON Schema |
| `permission_json` | object | — | 权限声明 | **缺失时告警**，不能静默当作无权限（详见 §7） |
| `runtime_compatibility` | object | — | 运行时兼容性 | 见 §3 |
| `config_json` | object | — | 配置参数 | 合法 JSON，按 type 有不同结构约束 |
| `relations` | array | — | 依赖关系声明 | 见 §8 |

---

## 3. 类型特有字段

### 3.1 Agent 类型

| 字段 | 类型 | 必填 | 说明 |
|------|------|:---:|------|
| `system_prompt` | string | — | 系统提示词 |
| `model_config` | object | — | 模型配置 |
| `model_config.provider` | string | — | 模型提供商（如 `openai` / `anthropic`） |
| `model_config.model` | string | — | 模型名称（如 `gpt-4`） |
| `model_config.temperature` | number | — | 温度参数 (0-2) |
| `model_config.max_tokens` | integer | — | 最大 token 数 |
| `agent_tools` | array | — | Agent 可调用的 Tool 声明（与 relations 互补） |

### 3.2 Skill 类型

| 字段 | 类型 | 必填 | 说明 |
|------|------|:---:|------|
| `task_definition` | object | — | 任务定义 |
| `task_definition.task_type` | string | — | 任务类型（`summarization` / `extraction` / `classification` / `translation` / `custom`） |
| `task_definition.max_input_length` | integer | — | 最大输入长度（字符数） |
| `capability_declaration` | object | — | 能力声明 |
| `constraints` | object | — | 限制条件 |
| `skill_spec` | string | — | Skill 规范版本引用 |
| `entry_point` | string | — | 入口函数/模块名（供 Runtime 调用） |
| `dependencies` | array | — | 包依赖（pip requirements 格式） |

### 3.3 Tool 类型

| 字段 | 类型 | 必填 | 说明 |
|------|------|:---:|------|
| `function_name` | string | — | 函数名 |
| `parameters` | object | — | 参数 JSON Schema |
| `returns` | object | — | 返回类型 JSON Schema |
| `examples` | array | — | 调用示例 |
| `timeout_seconds` | integer | — | 超时时间（秒） |
| `retry_policy` | object | — | 重试策略 |

### 3.4 MCP 类型

| 字段 | 类型 | 必填 | 说明 |
|------|------|:---:|------|
| `transport` | string | ✅ | 传输协议：`stdio` / `sse` / `http` |
| `protocol_version` | string | — | MCP 协议版本（如 `2024-11-05`） |
| `command` | string | — | 启动命令（stdio 模式） |
| `args` | array | — | 命令参数（stdio 模式） |
| `env` | object | — | 环境变量 |
| `url` | string | — | 服务 URL（sse/http 模式） |
| `auth_config` | object | — | 认证配置 |
| `auth_config.type` | string | — | 认证类型：`none` / `bearer` / `api_key` |
| `auth_config.header_name` | string | — | 认证头名称 |
| `provided_tools` | array | — | 提供的 Tool 列表声明 |
| `server_config` | object | — | 服务器配置 |

**MCP 的 transport 字段要求**：
- `stdio` → 必须提供 `command`；`url` 无效
- `sse` / `http` → 必须提供 `url`；`command` 无效

---

## 4. 必填字段

### 4.1 所有类型必填

| 字段 | 缺失时行为 |
|------|-----------|
| `manifest_version` | **拒绝导入** |
| `name` | **拒绝导入** |
| `type` | **拒绝导入** |
| `version` | **拒绝导入** |
| `description` | **拒绝导入** |

### 4.2 类型特有必填

| 类型 | 必填字段 | 缺失时行为 |
|------|----------|-----------|
| MCP | `transport` | **拒绝导入** |

---

## 5. 自动修正字段

以下字段在拼写或格式有轻微偏差时，Hub 自动修正并记录告警：

| 场景 | 输入 | 修正后 | 告警 |
|------|------|--------|------|
| 拼写错误（常见映射） | `desciption` | `description` | ✅ "field 'desciption' was auto-corrected to 'description'" |
| 拼写错误 | `manfiest_version` | `manifest_version` | ✅ |
| 多余空格 | `name`: `"  my-skill "` | `"my-skill"` | — (静默 trim) |
| 重复 tags | `["pdf", "pdf", "text"]` | `["pdf", "text"]` | — (静默去重) |
| `type` 大小写 | `"AGENT"` | `"agent"` | — (静默 lower) |
| `version` 前导 v | `"v1.2.0"` | `"1.2.0"` | — (静默去 v) |

**自动修正限定**：只修正有极低误判风险的输入。不修正语义字段（如 category 名称）。

---

## 6. Warning 字段

以下情况不拒绝导入，但返回 warning 信息：

| # | 场景 | Warning 消息 |
|---|------|-------------|
| 1 | `manifest_version` 高于 Hub 支持 | `"manifest_version X.Y exceeds Hub spec Z.W; newer fields may be ignored"` |
| 2 | `category` 不在预设列表中 | `"category 'xxx' is not a recognized category; consider using a preset value"` |
| 3 | `permission_json` 缺失 | `"permission_json is missing; the capability has not declared its permission requirements"` |
| 4 | `permission_json` 为空对象 | `"permission_json is empty; the capability declares no permissions"` |
| 5 | `runtime_compatibility` 缺失 | `"runtime_compatibility is missing; the capability has not declared its runtime dependencies"` |
| 6 | `relations` 中有引用不存在的 target_name | `"relation target 'xxx' not found in registry; relation skipped"` |
| 7 | 多余字段（非标准字段） | `"unknown field 'xxx' will be stored in config_json"` |
| 8 | `tags` 中有非字符串元素 | `"tag value '123' is not a string and was skipped"` |
| 9 | `input_schema` / `output_schema` 不是合法 JSON Schema | `"input_schema is not a valid JSON Schema"` |

### Warning 返回格式

```json
{
  "status": "imported_with_warnings",
  "item_id": "uuid",
  "version_id": "uuid",
  "warnings": [
    {
      "field": "permission_json",
      "code": "MISSING_PERMISSION",
      "message": "permission_json is missing; the capability has not declared its permission requirements",
      "severity": "warning"
    },
    {
      "field": "category",
      "code": "UNKNOWN_CATEGORY",
      "message": "category 'xxx' is not a recognized category",
      "severity": "warning"
    }
  ]
}
```

---

## 7. 导入失败字段（拒绝导入）

以下情况**拒绝导入**，返回明确错误：

| # | 场景 | Error Code | 错误消息 |
|---|------|------------|----------|
| 1 | `manifest_version` 缺失 | `MISSING_FIELD` | "manifest_version is required" |
| 2 | `name` 缺失 | `MISSING_FIELD` | "name is required" |
| 3 | `type` 缺失 | `MISSING_FIELD` | "type is required" |
| 4 | `version` 缺失 | `MISSING_FIELD` | "version is required" |
| 5 | `description` 缺失 | `MISSING_FIELD` | "description is required" |
| 6 | `type` 值非法 | `INVALID_TYPE` | "type must be one of: agent, mcp, skill, tool" |
| 7 | `name` 格式非法 | `INVALID_NAME` | "name contains invalid characters" |
| 8 | `version` 格式非法 | `INVALID_VERSION` | "version must follow semver (e.g. 1.0.0)" |
| 9 | MCP `transport` 缺失 | `MCP_MISSING_TRANSPORT` | "MCP manifest must include transport field" |
| 10 | MCP `transport=stdio` 但 `command` 缺失 | `MCP_MISSING_COMMAND` | "stdio transport requires command field" |
| 11 | MCP `transport=http/sse` 但 `url` 缺失 | `MCP_MISSING_URL` | "http/sse transport requires url field" |
| 12 | `name` 超出最大长度 | `FIELD_TOO_LONG` | "name exceeds 200 characters" |
| 13 | `description` 超出最大长度 | `FIELD_TOO_LONG` | "description exceeds 2000 characters" |
| 14 | `manifest_version` 不兼容（无法按任何已知版本解析） | `INCOMPATIBLE_VERSION` | "manifest_version X.Y is not compatible with Hub" |

---

## 8. permission_json 缺失的处理

### 8.1 为什么不能静默

`permission_json` 缺失不等于 "该能力不需要权限"：

- 缺少 permission 声明可能是**遗漏**，而非有意为之
- 如果默认为"无权限"，一旦该能力实际运行时需要权限，会导致：
  - Runtime 错误（权限不足）
  - 安全审计漏报（未知的能力权限）
  - Agent 被意外授予未声明的权限（如果 Runtime 不做额外检查）

### 8.2 处理策略

| 场景 | 行为 |
|------|------|
| `permission_json` 缺失 | Warning + 记录 "未声明权限" |
| `permission_json` 为空对象 `{}` | Warning + 记录 "声明了零权限" |
| `permission_json` 包含完整权限声明 | 正常导入 |
| 权限值不是 boolean | Warning: "permission value should be boolean" |

### 8.3 推荐的 permission_json 结构

```json
{
  "network": false,
  "file_read": true,
  "file_write": false,
  "shell_exec": false,
  "database": false,
  "external_url": false
}
```

---

## 9. relations / category / tags

### 9.1 relations 字段

```json
{
  "relations": [
    {
      "target_name": "pdf-text-extractor",
      "relation_type": "invokes",
      "relation_scope": "runtime",
      "required": true,
      "version_policy": "compatible",
      "description": "调用 PDF 文本抽取工具获取文档内容"
    }
  ]
}
```

| 字段 | 必填 | 说明 |
|------|:---:|------|
| `target_name` | ✅ | 目标能力的 name（导入时解析为 target_item_id） |
| `relation_type` | ✅ | uses / invokes / depends_on / provides |
| `relation_scope` | — | management / runtime（默认 runtime） |
| `required` | — | true / false（默认 true） |
| `version_policy` | — | current / fixed / compatible（默认 compatible） |
| `description` | — | 关系说明 |

### 9.2 category 字段

- 字符串类型，非枚举
- 不匹配预设 Category 时告警不拒绝
- 导入时不做 category 自动创建 — category 由管理员手动维护

### 9.3 tags 字段

- 字符串数组
- 自动去重、trim
- 非字符串元素跳过
- 导入时 tag 如果不存在则自动创建（name 唯一）
- AI 标注预留：导入的 tag 默认为 `imported` 状态

---

## 10. AI 标注预留

为阶段 8（AI 标注 Agent）预留字段，当前 manifest 中不强制要求。

### 10.1 预留字段（在 HubItem / HubItemVersion 层，非 manifest）

| 字段 | 说明 | 当前状态 |
|------|------|:---:|
| `tags[].status` | suggested / verified / imported | 预留 |
| `tags[].suggested_by` | 标注来源（AI model 名称 或 username） | 预留 |
| `tags[].suggested_at` | 标注时间 | 预留 |
| `category_confidence` | AI 推荐分类的置信度（0.0-1.0） | 预留 |

### 10.2 标注流程（阶段 8）

```
能力导入
  │
  ├──→ AI 分析 name/description → 建议 category (suggested)
  │                              → 建议 tags (suggested)
  │                              → 建议 relations (suggested)
  │
  └──→ 人工审核
        │
        ├── 确认 → status = verified
        ├── 修改 → status = verified（以人工修改为准）
        └── 拒绝 → 删除 suggested 标记
```

---

## 11. Manifest Spec v0.1 完整示例

### 11.1 Agent Manifest

```json
{
  "manifest_version": "0.1",
  "name": "compliance-review-agent",
  "type": "agent",
  "version": "0.1.0",
  "description": "招投标合规检查 Agent，自动审阅标书合规性",
  "category": "安全合规",
  "tags": ["合规", "招投标", "自动化"],
  "industry": "政府与公共部门",
  "scenario": "标书审查",

  "system_prompt": "你是一个专业的招投标合规审查员...",
  "model_config": {
    "provider": "openai",
    "model": "gpt-4",
    "temperature": 0.1,
    "max_tokens": 4096
  },

  "input_schema": {
    "type": "object",
    "properties": {
      "bid_document": { "type": "string", "description": "标书全文" },
      "compliance_checklist": { "type": "array", "items": { "type": "string" } }
    },
    "required": ["bid_document"]
  },
  "output_schema": {
    "type": "object",
    "properties": {
      "compliance_score": { "type": "number", "minimum": 0, "maximum": 100 },
      "issues": { "type": "array", "items": { "type": "object" } }
    }
  },

  "permission_json": {
    "network": false,
    "file_read": true,
    "file_write": false,
    "shell_exec": false
  },
  "runtime_compatibility": {
    "python": ">=3.10",
    "memory_mb": 512,
    "timeout_seconds": 300
  },

  "config_json": {
    "max_document_pages": 500,
    "language": "zh",
    "strict_mode": true
  },

  "relations": [
    {
      "target_name": "long-doc-summarizer",
      "relation_type": "uses",
      "relation_scope": "runtime",
      "required": true,
      "version_policy": "compatible",
      "description": "使用长文档摘要 Skill 对长标书做预处理"
    },
    {
      "target_name": "pdf-text-extractor",
      "relation_type": "invokes",
      "relation_scope": "runtime",
      "required": true,
      "version_policy": "current",
      "description": "从 PDF 标书中提取文本"
    }
  ]
}
```

### 11.2 Skill Manifest

```json
{
  "manifest_version": "0.1",
  "name": "long-doc-summarizer",
  "type": "skill",
  "version": "0.1.0",
  "description": "长文档摘要 Skill，支持 50 页以上 PDF 的结构化摘要",
  "category": "文档处理",
  "tags": ["摘要", "PDF", "长文本"],

  "task_definition": {
    "task_type": "summarization",
    "max_input_length": 100000
  },
  "capability_declaration": {
    "supported_formats": ["pdf", "docx", "txt"],
    "output_formats": ["structured_json", "markdown"],
    "max_pages": 200
  },
  "constraints": {
    "required_memory_mb": 256,
    "estimated_duration_seconds": 30
  },
  "skill_spec": "hub-skill-spec-v0.1",
  "entry_point": "skill.summarize",
  "dependencies": ["pymupdf>=1.23.0"],

  "input_schema": {
    "type": "object",
    "properties": {
      "document": { "type": "string" },
      "max_length": { "type": "integer", "default": 1000 }
    },
    "required": ["document"]
  },
  "output_schema": {
    "type": "object",
    "properties": {
      "summary": { "type": "string" },
      "key_points": { "type": "array" }
    }
  },

  "permission_json": {
    "network": false,
    "file_read": true,
    "file_write": false,
    "shell_exec": false
  },
  "runtime_compatibility": {
    "python": ">=3.10",
    "memory_mb": 256
  },
  "config_json": {
    "summary_style": "structured",
    "max_output_tokens": 2048
  },

  "relations": [
    {
      "target_name": "pdf-text-extractor",
      "relation_type": "invokes",
      "relation_scope": "runtime",
      "required": true,
      "version_policy": "compatible"
    }
  ]
}
```

### 11.3 Tool Manifest

```json
{
  "manifest_version": "0.1",
  "name": "pdf-text-extractor",
  "type": "tool",
  "version": "0.1.0",
  "description": "从 PDF 文档中抽取纯文本内容",
  "category": "文档处理",
  "tags": ["PDF", "文本", "抽取"],

  "function_name": "extract_text",
  "parameters": {
    "type": "object",
    "properties": {
      "file_path": { "type": "string", "description": "PDF 文件路径" },
      "pages": { "type": "array", "items": { "type": "integer" }, "description": "指定页码范围，空表示全部" }
    },
    "required": ["file_path"]
  },
  "returns": {
    "type": "object",
    "properties": {
      "text": { "type": "string" },
      "page_count": { "type": "integer" }
    }
  },
  "examples": [
    {
      "input": { "file_path": "/data/report.pdf", "pages": [1, 2, 3] },
      "output": { "text": "...", "page_count": 3 }
    }
  ],
  "timeout_seconds": 60,
  "retry_policy": {
    "max_retries": 3,
    "backoff_seconds": 5
  },

  "input_schema": {
    "type": "object",
    "properties": {
      "file_path": { "type": "string" },
      "pages": { "type": "array", "items": { "type": "integer" } }
    },
    "required": ["file_path"]
  },
  "output_schema": {
    "type": "object",
    "properties": {
      "text": { "type": "string" },
      "page_count": { "type": "integer" }
    }
  },

  "permission_json": {
    "network": false,
    "file_read": true,
    "file_write": false,
    "shell_exec": false
  },
  "runtime_compatibility": {
    "python": ">=3.10"
  },
  "config_json": {
    "ocr_enabled": false,
    "max_file_size_mb": 50
  }
}
```

### 11.4 MCP Manifest

```json
{
  "manifest_version": "0.1",
  "name": "filesystem-mcp",
  "type": "mcp",
  "version": "0.1.0",
  "description": "文件系统 MCP Server，提供文件读写和目录操作能力",
  "category": "外部集成",
  "tags": ["文件系统", "MCP", "基础设施"],

  "transport": "stdio",
  "protocol_version": "2024-11-05",
  "command": "npx",
  "args": ["-y", "@modelcontextprotocol/server-filesystem", "/data"],
  "env": {
    "LOG_LEVEL": "info"
  },
  "auth_config": {
    "type": "none"
  },
  "provided_tools": [
    {
      "name": "read_file",
      "description": "读取文件内容"
    },
    {
      "name": "write_file",
      "description": "写入文件"
    },
    {
      "name": "list_directory",
      "description": "列出目录内容"
    }
  ],

  "input_schema": {},
  "output_schema": {},
  "permission_json": {
    "network": false,
    "file_read": true,
    "file_write": true,
    "shell_exec": true
  },
  "runtime_compatibility": {
    "node": ">=18.0.0"
  },
  "config_json": {
    "root_directory": "/data",
    "max_file_size_mb": 100
  }
}
```

---

## 12. P0 实施清单（阶段 3）

| # | 任务 | 依赖 |
|---|------|------|
| 1 | Manifest Spec v0.1 规范文档（本文档） | 无 |
| 2 | 通用字段校验器（manifest_version/name/type/version/description 必填检查） | #1 |
| 3 | 类型特有字段校验器（Agent/Skill/Tool/MCP 各自的 schema 定义） | #1 #2 |
| 4 | MCP transport 关联校验（stdio→command, http/sse→url） | #3 |
| 5 | 自动修正引擎（拼写修正/tags 去重/type lower/version 去 v） | #2 |
| 6 | Warning 生成引擎（category 不匹配/permission 缺失/runtime 缺失/多余字段） | #2 |
| 7 | Error 生成引擎（必填缺失/type 非法/name 格式/version 格式） | #2 |
| 8 | 导入流程集成 — 导入时调用 Manifest Spec 校验 | #2-#7 |
| 9 | tests: 合法 manifest + warning manifest + error manifest + 自动修正 + 类型特有字段 | #1-#8 |

---

> 配套文档：
> - `docs/validation/02_unified_vs_separate_management.md` — 统一治理面 + 类型化 Manifest 原则
> - `docs/validation/03_item_relation_design.md` — relations 字段的关系类型定义
> - `docs/validation/05_download_export_design.md` — manifest 下载接口
