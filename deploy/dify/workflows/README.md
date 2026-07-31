# Dify 多节点 workflow 测试用例

## 用途

用于测试 UnionAgents gateway 的 workflow node 跟踪能力（`node_started` / `node_finished` 事件 → Langfuse SPAN observation）。

当前 ECS 上 Dify-Workflow 实例只有单 LLM 节点（只发 `text_chunk`，不发 node 事件），无法触发 gateway 的 `create_or_update_workflow_node_span` 逻辑。这个多节点 workflow 包含 4 个节点：

```
Start → LLM → Code → End
```

每个节点都会发 `node_started` + `node_finished` 事件，gateway 会在 Langfuse trace 上创建 4 个 SPAN observation，记录每个节点的 input / output / elapsed_time。

## 导入方法

### 方式 A：DSL YAML 导入（推荐）

1. 登录 Dify 控制台（ECS: http://190.92.230.115:8080，账号 admin@unionagents.local）
2. 点击"创建空白应用" → 选择"Workflow" → 填写应用名
3. 进入应用后，左上角点"…"菜单 → "导入 DSL"
4. 选择 `multi-node-test.yml` 文件 → 确认导入
5. **关键**：导入后必须打开"LLM 节点"，检查 Model 配置——DSL 里的 `gpt-4o-mini` 是占位，需要选择 ECS Dify 实例上实际配置的模型（如 OpenAI-API-compatible 模型）
6. 点"运行"测试一次，确认能跑通
7. 在 UnionAgents admin 里把对应 agent 的 `dify.app_type` 设为 `workflow`，`engine_url` 指向这个 Dify 应用的实例

### 方式 B：UI 手动创建（DSL 导入失败时的兜底）

1. 登录 Dify 控制台 → 创建 Workflow 应用
2. 依次添加 4 个节点：
   - **Start**：添加 `text-input` 变量 `query`（必填）
   - **LLM**：System prompt="你是一个简洁的助手，用一句话回答用户的问题"，User prompt=`{{#start.query#}}`，Model 选实际可用模型
   - **Code**：输入变量 `llm_output` 映射到 `{{#llm.text#}}`，代码：
     ```python
     def main(arg1: str) -> dict:
         return {"result": "[Processed] " + arg1}
     ```
     输出变量 `result` 类型 string
   - **End**：输出变量 `result` 映射到 `{{#code.result#}}`
3. 连接 4 个节点的边：Start → LLM → Code → End
4. 点"运行"测试，输入"北京天气怎么样" → 应输出 `[Processed] 北京...一句话回答`
5. 发布应用

## 验证 gateway node 跟踪

1. 重建 gateway 镜像部署（0.8.10+，包含 `create_or_update_workflow_node_span`）
2. 通过 gateway 调用该 workflow agent：
   ```bash
   curl -N -X POST "http://<gateway>/v1/chat/completions" \
     -H "Authorization: Bearer <JWT>" \
     -H "X-Agent-ID: <workflow-agent-id>" \
     -H "X-Engine-Type: DIFY" \
     -H "Content-Type: application/json" \
     -d '{"messages":[{"role":"user","content":"北京天气怎么样"}],"stream":true}'
   ```
3. 打开 Langfuse UI（http://190.92.230.115:30030）→ 找到这次调用的 trace
4. 详情页应该看到：
   - 1 个 GENERATION observation（gateway 主响应）
   - 3 个 SPAN observation（Start 不发 node 事件，LLM/Code/End 各发一次 node_started+node_finished）
   - 每个 SPAN 的 metadata 含 `node_type` / `elapsed_time` / `status`
   - LLM SPAN 的 input 是 `{"query":"北京天气怎么样"}`，output 是 LLM 生成的文本
   - Code SPAN 的 input 是 `{"llm_output":"..."}`，output 是 `{"result":"[Processed] ..."}`
5. 时间戳应精确到毫秒（如 `14:35:28.048`）

## 故障排查

- **DSL 导入失败**：Dify 版本差异可能导致 DSL 格式不兼容。改用方式 B 手动创建。
- **看不到 SPAN**：检查 gateway 镜像版本 ≥ 0.8.10，且 `X-Engine-Type: DIFY` 头正确传入（缺省走 Hermes 不会触发 workflow 路径）。
- **只看到 1 个 GENERATION 无 SPAN**：Dify 实例只发了 `text_chunk` 没发 `node_*` 事件。说明 workflow 是单节点或 Dify 版本不支持节点事件。需用此处的多节点 workflow 验证。
- **session_id 为空**：workflow 模式 Dify 不发 `conversation_id`，session_id 需客户端通过 `X-Session-Id` 头传入。chat/agent 模式 Dify 会自动发 conversation_id。
