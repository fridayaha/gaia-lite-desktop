# API 调用指导

UnionAgents 为每个智能体实例提供 OpenAI 兼容的 API。你可以在智能体详情页创建 `sk-` 风格 API Key，用标准 OpenAI SDK 直接调用，无需改造客户端代码。

## 准备工作

1. 已部署一个智能体实例（状态为 `RUNNING`）
2. 在智能体详情页「API Keys」tab 创建一个 API Key，**复制明文 key 妥善保存**（创建后不再显示）

## 基础调用

### Endpoint

```
POST http://<服务器IP>:30080/v1/chat/completions
Authorization: Bearer sk-xxxxxxxxxxxxxxxxxxxx
Content-Type: application/json
```

> `<服务器IP>:30080` 为 admin 门户地址，nginx 已反代 `/v1/*` 到 gateway。

### curl 示例

```bash
curl -X POST http://190.92.230.115:30080/v1/chat/completions \
  -H "Authorization: Bearer sk-xxxxxxxxxxxxxxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "hermes-default",
    "messages": [
      {"role": "user", "content": "你好，介绍一下自己"}
    ],
    "stream": false
  }'
```

响应（非流式）：

```json
{
  "id": "chatcmpl-xxx",
  "object": "chat.completion",
  "model": "hermes-default",
  "choices": [
    {
      "index": 0,
      "message": {"role": "assistant", "content": "你好！我是..."},
      "finish_reason": "stop"
    }
  ],
  "usage": {"prompt_tokens": 12, "completion_tokens": 30, "total_tokens": 42}
}
```

### Python SDK 示例

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://190.92.230.115:30080/v1",
    api_key="sk-xxxxxxxxxxxxxxxxxxxx",
)

# 非流式
resp = client.chat.completions.create(
    model="hermes-default",
    messages=[{"role": "user", "content": "你好"}],
    stream=False,
)
print(resp.choices[0].message.content)

# 流式
for chunk in client.chat.completions.create(
    model="hermes-default",
    messages=[{"role": "user", "content": "写一首短诗"}],
    stream=True,
):
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="", flush=True)
```

### Node.js SDK 示例

```javascript
import OpenAI from "openai";

const client = new OpenAI({
  baseURL: "http://190.92.230.115:30080/v1",
  apiKey: "sk-xxxxxxxxxxxxxxxxxxxx",
});

const resp = await client.chat.completions.create({
  model: "hermes-default",
  messages: [{ role: "user", content: "你好" }],
});

console.log(resp.choices[0].message.content);
```

## 引擎差异

不同引擎类型的智能体，调用行为略有差异：

| 引擎 | model 字段 | 流式支持 | 备注 |
|---|---|---|---|
| Hermes | `hermes-default` 或自定义 | ✅ 流式 + 非流式 | 走 Profile 默认 Pod |
| Dify-Chat | 任意（仅占位） | ✅ 流式 + 非流式 | 走 Dify `chat-messages` API |
| Dify-Workflow | 任意 | ✅ 流式 | 走 Dify `workflows/run` API |
| Dify-Agent | 任意 | ⚠️ 仅流式 | Dify 平台限制，必须 `stream: true` |

> API Key 决定路由：`sk-` Key 绑定单个智能体实例，`model` 字段不会切换实例。

## 会话管理

OpenAI 兼容 API 支持会话 CRUD：

```bash
# 创建会话
curl -X POST http://190.92.230.115:30080/v1/sessions \
  -H "Authorization: Bearer sk-xxx" \
  -H "Content-Type: application/json" \
  -d '{"title": "我的会话"}'

# 列出会话
curl http://190.92.230.115:30080/v1/sessions \
  -H "Authorization: Bearer sk-xxx"

# 列出会话消息
curl http://190.92.230.115:30080/v1/sessions/<session_id>/messages \
  -H "Authorization: Bearer sk-xxx"
```

## 文件管理

```bash
# 列出文件
curl http://190.92.230.115:30080/v1/files \
  -H "Authorization: Bearer sk-xxx"
```

## 常见错误

| HTTP | 原因 | 处理 |
|---|---|---|
| 401 | API Key 无效 / 已删除 | 重新创建 Key；注意删除后最长 60s 内可能仍生效 |
| 400 | 请求体格式错误 | 检查 `messages` 字段是否合法 |
| 503 | 引擎 Pod 未就绪 | 在实例详情页确认状态为 `RUNNING` |
| 502 | 网关无法连接引擎 | 检查实例是否已部署、Pod 是否健康 |

## 安全建议

- **不要把 API Key 提交到代码仓库**，使用环境变量或密钥管理服务
- **每个场景独立 Key**：生产/测试/开发各用不同 Key，方便撤销与追踪
- **定期轮换**：删除旧 Key 创建新 Key，60s 内切换
- **设置用量告警**：在监控中心配置异常告警，发现异常调用及时处理

## API 参考文档

完整的 Manager 管理 API（智能体定义/实例管理/用户/角色等）请查看 [API 参考](../api-reference)（Swagger UI 自动生成）。
