# Demo Sample Files

用于负责人演示的样例文件。

## 文件清单与预期结果

| 文件 | 类型 | Manifest 校验 | 安全扫描 | 演示价值 |
|------|------|--------------|----------|----------|
| `agent_valid_manifest.json` | agent | ✅ valid，0 errors | low（无危险内容） | 演示 Agent Manifest 规范（含 input_schema/output_schema/dependencies/permission） |
| `skill_valid_manifest.json` | skill | ✅ valid，0 errors | low | 演示 Skill Package Spec（含 instruction/input/output/permission） |
| `tool_valid_manifest.json` | tool | ✅ valid，0 errors | low | 演示 Tool Schema（含 invocation/input/output/permission） |
| `mcp_valid_manifest.json` | mcp | ✅ valid，0 errors | low | 演示 MCP Config（含 transport/mcp_server/permission/权限边界） |
| `skill_warning_manifest.json` | skill | ✅ valid（3 warnings） | low | 演示 warning 放行：缺少 permission_json/input_schema/instruction 不阻断导入 |
| `mcp_blocking_manifest.json` | mcp | ✅ valid（1 warning） | critical → blocking | 演示格式合法 ≠ 安全可发布：submit-review 自动扫描阻断 |

## Runtime Resolve 与能力契约完整性

Runtime Resolve 返回的是能力契约。如果资产本身缺少 `permission_json`、`output_schema`、`runtime_compatibility` 等字段，Resolve 会如实返回 `null`，不会生成缺失内容。

**重要**：更新 demo_samples 后，已有数据库中的旧资产不会自动变完整。要看到完整 Resolve 结果，需要：
- 重新导入新的样例文件（会创建新 item），或
- 为旧资产创建新版本并填入完整契约内容

MCP 的主要契约是连接配置（transport/mcp_server）、暴露能力和权限边界；Tool / Skill 更依赖 input_schema / output_schema。

## 各文件说明

### agent_valid_manifest.json
- 完整的 Agent Manifest（含 input_schema、output_schema、scenario、dependencies、permission_json、runtime_compatibility）
- 适合演示四种类型各自有独立规范，Agent 不是无约束描述

### skill_valid_manifest.json
- 完整的 Skill Package Spec（含 instruction、input_schema、output_schema、permission_json）
- 校验通过，0 errors，0 warnings

### tool_valid_manifest.json
- 完整的 Tool Schema（含 invocation、input_schema、output_schema、permission_json）
- invocation.endpoint 使用 https，权限声明完整
- 校验通过，0 errors，0 warnings

### mcp_valid_manifest.json
- 完整的 MCP Config（含 transport="stdio"、mcp_server、permission_json、allowed_paths/access_mode 权限边界）
- 校验通过，0 errors，0 warnings

### skill_warning_manifest.json
- 缺少 permission_json、input_schema、instruction
- Manifest 校验 valid=True，产生 3 条 warning
- warning 不阻断导入，但提醒后续需要补全

### mcp_blocking_manifest.json
- config_json 含 `"rm -rf /tmp/logs"`（命中 COMMAND_RULES）
- Manifest 校验 valid=True（1 warning：permission_json 缺失）
- 安全扫描命中 critical → blocking
- **提交审核时自动触发扫描，blocking 被 400 阻断**
- 演示"格式合法代表合规能入库，但不代表安全可发布"

## 使用方式

### 前端导入
1. 打开前端 http://localhost:5173/items
2. 点击"导入能力包"
3. 选择文件上传

### curl 导入
```bash
curl -X POST http://localhost:8000/api/hub/imports/package \
  -F "file=@docs/demo_samples/agent_valid_manifest.json"
```

## 完整演示流程
详见 `docs/09_demo_guide.md`（后续补充）。
