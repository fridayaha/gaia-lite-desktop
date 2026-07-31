> ⚠️ **历史文档**：V2 重构经验总结。V3 三层重构（2026-06-23）已在此基础上完成，当前架构见 [`architecture-v3.md`](./architecture-v3.md)。本文经验仍有参考价值。

# V2 架构重构经验总结

> **日期**: 2026-06-13  
> **版本**: 0.5.1 → 0.6.0  
> **变更**: 40 files, +4139/-510 lines

---

## 一、重构动机

旧架构「1 Pod = 1 Agent = 1 用户」无法支撑东风猛士门店场景：
- 100 名销售 × 3 个智能体 × 3 个门店 → 需要独立的配置、会话、记忆隔离
- 无法按门店维度管理 K8s 资源（CPU/内存/副本数/回收策略）
- Agent 定义与引擎部署参数耦合，无法独立复用

## 二、核心设计决策

### 2.1 Entity 解耦

```
EngineInstance (多租户资源池)  ←1:N→  Agent (智能体定义)  ←1:N→  AgentChannel (渠道+Profile策略)
       ↓ 1:N                                                            ↓ 1:N
AgentDeployment (K8s Pod)                                    AgentProfile (Hermes Profile映射)
```

- **EngineInstance**: 独立的资源规格定义，多 Agent 共享，支持 Clone 复制
- **Agent**: 纯业务定义（模型/技能/记忆），不包含部署参数
- **AgentChannel**: 渠道配置 + scope 决定 Profile 策略（INDEPENDENT/SHARED）

### 2.2 Hermes Profile 集成

**踩坑点**: Hermes 官方 Docker 镜像使用 s6-overlay 管理多 Profile Gateway，但 API Server 模式下 `hermes -p X gateway start` 会替换默认 gateway 进程（而非共存）。

**最终方案**: 手动设置 `HERMES_HOME` + `API_SERVER_PORT`，直接启动多个 `hermes gateway run` 进程，每进程一个端口，nginx 做 `X-Hermes-Profile → port` 路由。

验证结果：
```
docker run unionagents/engine-hermes:v2 \
  -e PROFILES_JSON='[{"name":"emp001","port":8644},{"name":"emp002","port":8645}]'

nginx /health:                HTTP 200
emp001 via X-Hermes-Profile:  HTTP 200
emp002 via X-Hermes-Profile:  HTTP 200
```

### 2.3 Pod 不共享存储

Hermes 官方文档明确警告："Never run two Hermes gateway containers against the same data directory simultaneously." 因此每个 Pod 使用独立的 emptyDir 存储，Profile 通过 Controller 调度到特定 Pod，而非所有 Pod 共享 PVC。

### 2.4 Pod 数量计算

```
Pods_needed = ceil(Total_Profiles / Max_Profiles_Per_Pod)

其中 Max_Profiles_Per_Pod = floor((Pod_Memory × 0.7 - Overhead) / Memory_Per_Profile)
Memory_Per_Profile = Active_Pct × 250MB + (1-Active_Pct) × 80MB
```

光谷 100 名销售 + 4Gi/Pod → 23 Profile/Pod → 5 Pods。

---

## 三、踩坑记录

### 3.1 Pydantic 保留字段名冲突

`model_config` 是 Pydantic v2 的保留字段名，不能作为 model field。解决方案：API schema 使用 `model_settings`，DB 列保持 `model_config`，在 service 层做映射。

### 3.2 Python 3.9 类型语法限制

`str | None` 语法只在 Python 3.10+ 支持。测试代码需使用 `Optional[str]`。

### 3.3 SQLAlchemy Enum 定义顺序

`EngineType` enum 必须定义在 `EngineInstance` model 之前，否则 NameError。重构时将全部 Enum 移到文件顶部。

### 3.4 nginx `map` 指令陷阱

- Python f-string 会吃掉 `$backend` 等 nginx 变量 → 需转义或避免 f-string
- `map` 必须在 `http` block 内，不能放在 `server` block 内
- nginx `if` 块内 `set` 指令不支持变量插值 → 改用 `map`

### 3.5 Controller config/apply 未适配新字段

`config/apply` 和 `config/sync` 仍然 SELECT 旧的 `config` 列，导致 MODEL_PROVIDERS_JSON 未注入 Deployment env vars。修复：全部改为 SELECT `model_config`。

### 3.6 前端 API 响应 `.data` 访问差异

项目使用自定义 `http` wrapper（基于 Axios），响应已自动 unwrap（直接返回 response.data），无需再 `.data`。

---

## 四、安全设计经验

1. **Profile 名服务端计算**: 客户端传入的 `X-Hermes-Profile` 头被 Gateway 强制忽略，Profile 名由 `ProfileResolver` 根据 JWT user_id + agent_id 计算。
2. **双层权限验证**: (a) Agent.access_scope 验证用户是否有权访问智能体；(b) AgentProfile 验证用户是否有权使用该 Profile。
3. **Pod 内 nginx 路由**: `X-Hermes-Profile` header 由 Gateway 注入，Pod 内 nginx 只信任该来源。

---

## 五、性能基准

| 指标 | 值 | 备注 |
|------|-----|------|
| 串行延迟 | 1.58s avg | DeepSeek API, 单 Pod |
| 串行吞吐 | 21.2 tok/s | per request |
| 并发 3 请求 | 1.61s wall time | 68.4 tok/s combined |
| SSE TTFT | 0.01s | 首字节延迟 |
| 首请求预热 | 5.88s | 冷启动 prompt cache miss |

---

## 六、后续计划

| 优先级 | 任务 |
|--------|------|
| 高 | Admin 前端: EngineInstance 管理页 + Agent 表单在 k3s 环境 prod build 部署验证 |
| 中 | Controller ProfileScheduler: 自动 Pod 选择 + Profile 迁移 |
| 中 | Profile 级闲置回收: 单个 Profile gateway stop，保留数据 |
| 低 | pod-per-profile 模式: 高安全场景下每 Profile 独立 Pod |
