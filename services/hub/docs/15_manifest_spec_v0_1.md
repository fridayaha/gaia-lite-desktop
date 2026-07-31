# Manifest Spec v0.1

## 概述

Manifest Spec v0.1 是 Hub Stage 2 实现的类型化 Manifest / Schema 校验框架。导入和版本创建时按资产类型对 manifest 进行校验，不再只依赖无约束的 `config_json`。

## 版本

`manifest_version = "0.1"`

`SUPPORTED_MANIFEST_VERSIONS = {"0.1"}`

- 缺失：warning，默认按 "0.1" 处理
- 显式存在但不在支持列表中：error

## 通用字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| manifest_version | str | 否 | Manifest Spec 版本，默认 "0.1" |
| name | str | 是 | 资产名称 |
| type | str | 是 | 资产类型：agent/skill/tool/mcp |
| version | str | 否 | 语义化版本号，默认 "0.1.0" |
| description | str | 否 | 资产描述 |
| input_schema | dict | 否 | 输入 JSON Schema |
| output_schema | dict | 否 | 输出 JSON Schema |
| permission_json | dict | 否 | 权限声明 |
| runtime_compatibility | dict | 否 | 运行时兼容性配置 |
| config_json | dict | 否 | 类型特定配置 |
| relations | list | 否 | 格式预留 |
| metadata | dict | 否 | 扩展元数据 |
| extensions | dict | 否 | 扩展字段 |

`x_*` 前缀字段作为扩展字段，不产生 unknown field warning。

## 类型特有字段

### Agent Manifest

| 字段 | 类型 | 说明 |
|------|------|------|
| scenario | str | 适用场景 |
| dependencies | list | 依赖声明 |

### Skill Package Spec

| 字段 | 类型 | 说明 |
|------|------|------|
| instruction | str | Skill 执行指令文本 |

### Tool Schema

| 字段 | 类型 | 说明 |
|------|------|------|
| invocation | dict | 调用配置 {method, endpoint, timeout_ms, retry} |

### MCP Config

| 字段 | 类型 | 说明 |
|------|------|------|
| transport | str | 传输协议：stdio / sse / streamable_http |
| mcp_server | dict | 服务配置 {command, args, env} |

## 校验规则

### 通用校验（所有类型）

| 校验项 | 等级 | 说明 |
|--------|------|------|
| name 非空 | error | |
| type 不在允许集合中 | error | |
| type ≠ 期望类型 | error | 类型化 validator 校验 |
| manifest_version 显式存在但非支持版本 | error | |
| manifest_version 缺失 | warning | 默认 "0.1" |
| version 缺失 | warning | 默认 "0.1.0" |
| permission_json 缺失 | warning | 所有四种类型 |
| 未知字段 | warning | metadata/extensions/x_* 除外 |

### Skill 特有

| 校验项 | 等级 |
|--------|------|
| input_schema 缺失 | warning |
| output_schema 缺失 | warning |
| instruction 缺失 | warning |

### Tool 特有

| 校验项 | 等级 |
|--------|------|
| input_schema 缺失 | warning |
| output_schema 缺失 | warning |
| invocation 缺失 | warning |

### MCP 特有

| 校验项 | 等级 |
|--------|------|
| transport 非法值 | error |
| mcp_server 缺失 | warning |

## 归一化（Normalization）

| 操作 | 说明 |
|------|------|
| name trim | 前后空格去除 |
| type lower | 统一转小写 |
| manifest_version 填充 | 缺失默认 "0.1" |
| version 填充 | 缺失默认 "0.1.0" |

## 写回策略

校验通过后，规范化 manifest 按以下规则写回 HubItemVersion 各列：

| HubItemVersion 列 | 来源 |
|--------------------|------|
| manifest_json | manifest_version, relations, metadata, extensions, scenario, dependencies, instruction, invocation, transport, mcp_server, 及其他非列字段 |
| config_json | manifest 的 config_json 字段 |
| input_schema | manifest 的 input_schema 字段 |
| output_schema | manifest 的 output_schema 字段 |
| permission_json | manifest 的 permission_json 字段 |
| runtime_compatibility | manifest 的 runtime_compatibility 字段 |

name / type / version 使用 HubItem / HubItemVersion 的权威值，不从 manifest_json 派生。

## 与 API 的集成

### 导入（POST /api/hub/imports/package）

- error 级问题 → 400，响应含 `errors` 列表
- warning 级问题 → 201，响应含 `warnings` 字段

### 版本创建（POST /api/hub/items/{id}/versions）

- error 级问题 → 400
- manifest_json.type 与 item.type 冲突 → 400
- manifest_json.version 与 data.version 冲突 → 400
- manifest_json.name 与 item.name 冲突 → warning（不阻断）
- warnings 静默（不修改 HubItemVersionRead 响应形状）

## 后续阶段

| 能力 | 阶段 |
|------|------|
| 版本级关系校验 | Runtime Resolve |
| schema 缺失从 warning 升级为 error | 规范成熟后 |
| Pydantic model_validate 整体校验 | 规范成熟后 |
| 携带 warnings 的版本创建响应 | 后续按需 |
| manifest 互转（版本升级/降级） | 后续 |
