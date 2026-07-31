"""社区文章幂等 seed。

启动时自动插入 1-2 篇示例文章（PUBLISHED），用作社区冷启动种子内容。
- 按 slug 去重：已存在的不覆盖
- 若库中无系统管理员，则跳过（无作者可用）
- 失败不阻塞启动（startup 外层已包 try/except）
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Article, ArticleStatus, User, Role, user_roles
from pkg.common.database import async_session

_SEED_ARTICLES = [
    {
        "slug": "unionagents-platform-overview",
        "title": "UnionAgents 平台架构总览",
        "excerpt": "UnionAgents 是面向企业的智能体一体化运营平台，本文介绍其三层架构与核心能力。",
        "content": """# UnionAgents 平台架构总览

UnionAgents 是面向企业的智能体一体化运营平台，覆盖「智能体定义 → 资源调度 → 运行观测」全生命周期。

## 三层架构

1. **定义层（Agent Definition）**：智能体的「蓝图」，包含人设（SOUL.md）、技能配置、模型设置、版本快照。支持克隆、版本发布、灰度切换。
2. **资源层（Resource Pool）**：定义可部署到哪些运行环境（k8s 命名空间、引擎规格、并发上限），按 group 隔离。
3. **实例层（Agent Instance）**：运行中的智能体，由 controller 在 k3s 集群中拉起 Pod，gateway 通过 `X-Agent-ID` 头转发请求到引擎。

## 引擎

当前主力引擎为 **Hermes v2**（开源引擎容器化部署），通过原生 HTTP API 调用。后续会接入 Dify、Coze 等引擎，统一由 controller 调度。

## 监控可观测性

- **Langfuse v3**：链路追踪、token 用量、调用成本
- **Prometheus + Grafana**：基础设施指标（CPU/内存/Pod 状态）
- **Loki**：服务日志聚合
- **Alertmanager**：异常告警路由（飞书/钉钉/企业邮件）

## API 调用

所有智能体对外暴露 **OpenAI 兼容协议**（`/v1/chat/completions`），用户可通过标准 OpenAI SDK + `sk-` 开头的 API Key 直接调用。详见《用 OpenAI SDK 调用你的智能体》。
""",
    },
    {
        "slug": "call-agent-with-openai-sdk",
        "title": "用 OpenAI SDK 调用你的智能体",
        "excerpt": "标准 OpenAI Python/Node SDK + sk- 密钥，三行代码调用 UnionAgents 智能体。",
        "content": """# 用 OpenAI SDK 调用你的智能体

UnionAgents 所有智能体对外暴露 **OpenAI Chat Completions 兼容协议**，可直接用官方 OpenAI SDK 调用。

## 1. 获取 API Key

1. 登录控制台 → 「API Keys」页面
2. 点击「新建 Key」，选择关联的智能体
3. 复制 `sk-` 开头的密钥（仅展示一次）

## 2. Python 调用示例

```python
from openai import OpenAI

client = OpenAI(
    api_key="sk-xxxxxxxxxxxx",
    base_url="https://your-domain/v1"
)

resp = client.chat.completions.create(
    model="agent-<agent-id>",  # 智能体 ID（控制台「实例」页面查看）
    messages=[
        {"role": "user", "content": "你好，介绍一下你自己"}
    ],
    stream=False
)
print(resp.choices[0].message.content)
```

## 3. 流式响应

```python
stream = client.chat.completions.create(
    model="agent-<agent-id>",
    messages=[{"role": "user", "content": "讲个笑话"}],
    stream=True
)
for chunk in stream:
    delta = chunk.choices[0].delta.content
    if delta:
        print(delta, end="", flush=True)
```

## 4. curl 调用

```bash
curl -N https://your-domain/v1/chat/completions \\
  -H "Authorization: Bearer sk-xxxx" \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "agent-<agent-id>",
    "messages": [{"role": "user", "content": "你好"}],
    "stream": true
  }'
```

## 常见错误

| HTTP 状态 | 原因 | 处理 |
|---|---|---|
| 401 | API Key 无效或被吊销 | 重新生成 Key |
| 404 | agent-id 不存在或未部署 | 检查实例状态 |
| 429 | 触发并发限流 | 降低并发或联系管理员提升配额 |
| 502 | 引擎异常 | 等待自动重启或查看监控 |
""",
    },
]


async def _find_admin_user_id(db: AsyncSession) -> str | None:
    """找第一个具有「系统管理员」角色的用户作为种子文章作者。"""
    stmt = (
        select(User.id)
        .join(user_roles, user_roles.c.user_id == User.id)
        .join(Role, Role.id == user_roles.c.role_id)
        .where(Role.name == "系统管理员")
        .order_by(User.created_at)
        .limit(1)
    )
    row = (await db.execute(stmt)).first()
    return str(row[0]) if row else None


async def seed_articles(db: AsyncSession | None = None) -> None:
    """幂等插入社区示例文章。slug 已存在则跳过；无系统管理员则跳过。"""
    own_session = db is None
    if own_session:
        db = async_session()
    try:
        author_id = await _find_admin_user_id(db)
        if not author_id:
            # 无可用作者，跳过（启动时不报错）
            return

        existing = await db.execute(select(Article.slug))
        existing_slugs = {r[0] for r in existing.all()}

        for spec in _SEED_ARTICLES:
            if spec["slug"] in existing_slugs:
                continue
            now = datetime.now(UTC)
            db.add(
                Article(
                    author_id=author_id,  # type: ignore[arg-type]
                    title=spec["title"],
                    slug=spec["slug"],
                    excerpt=spec["excerpt"],
                    content=spec["content"],
                    status=ArticleStatus.PUBLISHED,
                    published_at=now,
                    view_count=0,
                )
            )
        await db.commit()
    finally:
        if own_session:
            await db.close()
