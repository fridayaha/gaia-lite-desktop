# Gateway — 纯透传代理

## 设计原则
- **纯透传**：不解析/修改 request body
- **不修改响应**：原样转发上游响应
- **仅替换 Authorization**：用户 JWT → 引擎 API Key
- **URL 透明**：保留路径和查询参数

## 实现
```python
@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy(request, path):
    upstream = f"{engine_upstream_url}/{path}?{query}"
    # 转发 body、headers（替换 Authorization）
    # 根据 content-type 判断流式/非流式
```

## 环境变量
| 变量 | 默认值 | 说明 |
|------|--------|------|
| `UA_ENGINE_UPSTREAM_URL` | `http://engine-hermes-test:8642` | 上游引擎地址 |
| `UA_API_SERVER_KEY` | `change-me`（占位） | 引擎认证 Key |
| `UA_JWT_SECRET` | (开发密钥) | 保留字段 |

## 流式处理
- SSE (`text/event-stream`) → StreamingResponse
- 常规 JSON → 标准 Response
- 捕获 `ReadError`/`RemoteProtocolError` 优雅处理上游断连
