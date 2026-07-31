# API 参考

Manager 管理 API 的完整参考文档由 FastAPI 自动生成（Swagger UI），与代码保持同步。

## OpenAPI Swagger

<iframe src="/api/manager/docs" style="width: 100%; min-height: 800px; border: 0;" frameborder="0"></iframe>

> 如上方空白，可能是网络问题，请直接访问 [Swagger UI](/api/manager/docs)。

## 说明

- **认证方式**：大部分端点需要 `Authorization: Bearer <JWT>` 头，JWT 通过 `/api/manager/auth/login` 获取
- **OpenAI 兼容 API**：智能体调用走 `/v1/*` 路径，使用 `sk-` API Key 鉴权（详见 [API 调用指导](./guide/api-usage)）
- **分组**：端点按模块分组（auth / users / roles / agent-definitions / agent-instances / observability 等），可在 Swagger 顶部按 tag 筛选

## 主要模块

| 模块 | 路径前缀 | 用途 |
|---|---|---|
| 认证 | `/api/manager/auth` | 登录、刷新 token、当前用户信息 |
| 用户 | `/api/manager/users` | 用户 CRUD、密码重置 |
| 角色 | `/api/manager/roles` | 角色 CRUD、权限分配 |
| 用户组 | `/api/manager/user-groups` | 用户组（租户隔离单元）管理 |
| 智能体定义 | `/api/manager/agent-definitions` | 智能体模版定义 |
| 智能体实例 | `/api/manager/agent-instances` | 实例化、部署、API Key 管理 |
| 引擎配置 | `/api/manager/engine-configs` | LiteLLM 模型配置 |
| 监控中心 | `/api/manager/observability` | 链路追踪 / 用量 / 告警 |
| 社区 | `/api/manager/community` | 技术文章（公开可读） |
| OpenAI 兼容 | `/v1/*` | 智能体调用（sk- 鉴权） |
