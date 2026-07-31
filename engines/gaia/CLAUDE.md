# Gaia — 开源版 Palantir

> 复刻 Foundry（分层数据架构）+ AIP（本体驱动 Agent 工具体系）。本文件只保留**每次开发都必须遵守的硬约束 + 命令速查**；检索型内容（目录结构、Service 清单、ADR/ICD 索引、实施路线、错误模式库、组件版本/端口表、数据流图、产品哲学详述）按需查 `docs/`，见文末「文档地图」。

- Python 包名 `ontology`（`src/ontology/`）；后端 Python 3.12+ / FastAPI；前端 TypeScript + Vite + React 19 + Tailwind v4.3（`src/web-ui/`）
- 包管理 [uv](https://docs.astral.sh/uv/)；测试 pytest + pytest-asyncio（TDD）；lint ruff + mypy --strict；提交 Conventional Commits + pre-commit；审计 `uv audit`

## Commands

> 在本文件所在目录（`engines/gaia/`）下执行。**后端/Alembic 必须用 `.venv/bin/python` / `.venv/bin/alembic` 直调，不能用 `uv run`——此项目 uv run 不可靠。**

```bash
docker compose up -d              # 基础设施：postgres(5432) trino(8080) gravitino(8090+REST 9001) rustfs(9000) kafka(9092) doris(9030/9050) seatunnel
bash scripts/dev.sh               # 一键启动前后端
.venv/bin/python -m uvicorn ontology.main:app --host 127.0.0.1 --port 8000   # 仅后端
cd src/web-ui && node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5173  # 仅前端
curl :8000/health  # → {"status":"ok"}

uv sync --extra dev
uv run pytest                      # 全部；tests/unit/ 仅单元；-m "not system" 跳过系统测试
uv run ruff check src/ && uv run ruff format --check src/ && uv run mypy --explicit-package-bases src/
uv audit

.venv/bin/alembic upgrade head                       # 应用迁移
.venv/bin/alembic revision --autogenerate -m "描述"  # 生成迁移（必须人工 review）
.venv/bin/alembic check                              # 检测 ORM 与 DB 漂移
```

**AI API Key**：`AI_MODEL` 为 pydantic-ai `provider:model_name`（默认 `openai:gpt-4o`）。`settings.py` 自动把 `.env` 的 `<provider>_api_key` 注入 `os.environ`，无需手动 export。换模型改 `.env` 的 `AI_MODEL` + 对应 key。详见 `.env.example`。

**部署（k3s，独立 namespace `gaia`）**：权威源 [`deploy/README.md`](deploy/README.md)。本地 `make k8s-all`/`make pf-api`/`make pf-web-ui`；CI `make package VERSION=x.y.z` → 目标集群 `bash scripts/deploy.sh`。profile `minimal`（默认）/`full`。

> ⚠️ **反复踩坑**：Iceberg REST Catalog = Gravitino 容器内置 **9001**（端点 `/iceberg`，jdbc 后端）。**没有 8181 端口，从未部署 `tabulario/iceberg-rest`**。任何代码/文档/配置引用 8181 或独立 iceberg-rest 服务均为错误。验证 `curl -o /dev/null -w "%{http_code}" http://localhost:9001/iceberg/v1/config` → 200（带 `?warehouse=` 会 404，已知缺陷）。

## 架构分层与职责

`Routes → 本体工具层 tools/（22 工具 8 toolset + HITL，ADR-009）→ Services（22 个）→ 8 Layer（Catalog/Metadata/Dataset/Index/Pipeline/Engine/Graph/GeoTime）→ Core Models`。Service 编排层间调用，**Layer 间禁止直接互调**；工具层薄包装 Service，不绕过 Service 直接调 Layer。`core/models/`=ORM，`core/schemas/`=pydantic，命名收口 `core/naming.py`。

| 层 | 允许 | 禁止 |
|----|------|------|
| Metadata (PostgreSQL) | 业务本体元数据 + object_state + outbox + datasets 治理记录 | 存物理表元数据、参与查询 |
| Catalog (Gravitino) | 注册托管资产/虚拟表/RBAC/access check | 存业务本体元数据、参与计算 |
| Dataset (Iceberg) | 全量明细 + 历史快照 + 时间旅行 + scan_latest | 在线查询、检索加速 |
| Index (Doris) | 在线读主源：全量结构化属性 + 点查/过滤/聚合 + IVF ANN | 作写入入口（写入经 Iceberg→IndexSync） |
| Pipeline (SeaTunnel) | 外部源采集/写入 Iceberg + CDC + 文件/Kafka/时序同步 | 元数据管理、查询路由、Iceberg→Doris 同步（已改 ObjectIndexFunnel） |
| Engine (Trino) | 联邦查询、全量加载、Virtual Table 执行 | 作主数据存储 |

**降级**：Doris 不可用→Trino 扫 Iceberg（带分区裁剪，TextQL 按目标重编译）；Iceberg 不可用→Trino 按 ID 查；Gravitino 不可用（物理表）→绕过权限；Gravitino 不可用（虚拟表）→**直接失败，无降级**；索引同步延迟→告警降级 Trino 全表扫。

> 组件真实状态（✅/🟡/🔴）、Service 清单、目录结构、数据流图、组件版本表均见 [`docs/architecture/implementation-status.md`](docs/architecture/implementation-status.md)。

## Gravitino Catalog First

Gravitino 是物理元数据唯一权威，各层从它取 schema，不在 PG 重复存。托管表建表走 `IcebergStore.create_managed_table`（pyiceberg，支持 PK/doc/required），不走 `GravitinoRegistry.register_dataset`；已存在表用 `ensure_schema` 演进（加列带 doc/required），**不 drop 重建**；PG `datasets` 只存治理维度，**不存物理 schema**；SeaTunnel 只写数据不建表（`schema_save_mode=IGNORE`）。

## 红线（不可违反）

1. Gravitino 仅管物理资产，不存业务本体元数据
2. PostgreSQL 仅存业务本体元数据（+ object_state/outbox/datasets 治理记录），不存物理表元数据
3. Iceberg 是主数据唯一写入入口（Action 例外：写 PG `object_state`，经 outbox ARCHIVE effect 异步同步 Iceberg，见 [action-architecture.md](docs/architecture/action-architecture.md))
4. Doris 作在线读主源存全量结构化属性；Iceberg/Trino 退为历史快照/批量/容灾（ADR-001）
5. Trino 主查询引擎；Virtual Table 必须走 Trino，**无 Doris 降级**
6. SeaTunnel 职责收窄为「外部源→Iceberg/TimescaleDB 搬运」；Iceberg→Doris/Neo4j/PostGIS 已去 SeaTunnel 化
7. **无 Redis**（用 Doris 缓存 + Iceberg ACID + 分区替代）
8. **Doris 索引表名必须带本体前缀** `idx_{ontology}__{type}`（snake_case，`core/naming.doris_index_table` 生成）——缺本体维度会跨本体互盖/误删
9. **VIRTUAL 目标禁止写入**（`execute_action` 拒绝，前端置灰）；仅可读（Trino 联邦）。图投影例外见 [adr-021](docs/architecture/adr-021-virtual-graph-projection.md)
10. **物理资源命名走 snake_case 保词界**（`_to_snake`）；业务 api_name（PascalCase/camelCase）不得泄漏进物理命名
11. **Ontology API 不吃自然语言（两层正交）**：`/objects/{ont}/*` 只接受结构化 ObjectSet IR，**禁止 NL 端点**；NL 查询走 `/ai/agent`（AG-UI ReAct）或 MCP `query_with_dataframe`。违反会破坏层次分离（ADR-015 D4 + [reference.md](docs/reference.md)）
12. **三入口能力分层**：MCP 对外操作面只暴露"用本体"（查询/推理/执行已定义 Action/即席轻量建模）；"造本体"（ActionType CRUD/数据源/Pipeline/批量运维/权限）仅 REST。MCP 与 AG-UI 操作面能力集**必须对等**。判据见 [ADR-019](docs/architecture/adr-019-three-entry-capability-layering.md)

## 编码规范

| # | 规范 | 禁止 |
|---|------|------|
| 1 | SQLAlchemy 2.0 async ORM（`select()` 风格） | 裸 SQL 字符串 |
| 2 | pydantic v2 校验，与 ORM 分离 | 直接暴露 ORM 对象 |
| 3 | 类型注解全覆盖，`mypy --strict` 通过 | `Any` 泛滥 |
| 4 | `datetime.now(UTC)` | `datetime.utcnow()` |
| 5 | `uuid.uuid4().hex` 主键 | 自增 ID |
| 6 | `async` 全链路 | 阻塞事件循环 |
| 7 | ruff 格式 + lint | 未通过检查合入 |
| 8 | 联邦 SQL：操作符映射表查表 + 值参数化绑定 + 标识符白名单校验 | 手写 if-elif 链/字面量转义/正则防注入（[ontology-tool-layer.md §7.2](docs/architecture/ontology-tool-layer.md)） |
| 9 | 物理命名统一走 `core/naming.py`（`doris_index_table`/`iceberg_s3_location`/`managed_dataset_api_name`/`sync_pipeline`） | 手拼 `.lower()`（丢词界） |
| 10 | apiName 推导分层：Property/Link 后端规则推导；ObjectType/Action/Ontology 前端 LLM+用户改+后端校验 pattern+唯一 | — |

**领域模型**：主键 UUID v4；`api_name` 范围内唯一；枚举 VARCHAR+pydantic `Literal`；灵活字段 JSONB；内部 FK 用 UUID，业务接口用 `api_name`；外键 `ON DELETE CASCADE`（`ActionType.affected_object_type_id` 例外 `SET NULL`）。

**异常层级**：`OntologyError` → `NotFoundError`(404)/`ConflictError`(409)/`ForbiddenError`(403)/`DorisUnavailableError`(触发降级)。

**ORM 坑**：`_flush_and_commit()` 后 session 已提交，relationship 无法懒加载（`MissingGreenlet`）——创建/更新后**直接构造 pydantic 对象**，别 `model_validate(orm)`。

## Schema 变更（Alembic，单一真相源）

业务表 schema 变更**必须**走 Alembic，禁手写 SQL / 禁手改 `init-pg-schema.sql`：改 ORM → `.venv/bin/alembic revision --autogenerate -m "..."` → **人工 review**（autogenerate 检测不出重命名）→ `upgrade head` → `check` 无漂移 → 提交 ORM + migration。Gravitino 的 `gravitino_store` schema 走 `infra/gravitino-pg-schema.sql`（Gravitino 自管，不归 Alembic）。

## 依赖红线

`uv.lock` 必须入 Git；`uv lock` 后检查 diff 同步。**禁本地路径依赖**（`path="../..."` 在 Docker 上下文外致 `uv sync --frozen` 失败）——需特定 commit 用 `git="...", rev="..."`。新依赖必须 `docker compose build api` 通过后才能提交。

## 测试

静态分析 CI 必过；单元测试行覆盖 > 90%、异常路径 100%；系统测试 PR 手动触发。异常路径必覆盖：连接超时、权限拒绝、数据冲突、依赖不可用、参数校验失败。

**DB 写入测试不能只断言 `commit.assert_awaited()`**——必须验证实际写入字段/SQL 行为（真 DB 或断言 model 属性 + `flag_modified`，mock commit 测不出 schema 漂移和 JSONB 丢失）。多步写入用 `async with self.transaction():` + `auto_commit=False`；best-effort 操作记日志后 raise，禁 `except Exception: pass` 吞异常（[transaction-management-best-practices.md](docs/engineer/transaction-management-best-practices.md)）。

## 提交前高频自检（已多次造成运行时故障）

- **import 完整**：方法内用到的异常/函数都 import 了（`ruff check`）
- **commit 后别 `model_validate(orm)`**：直接构造 pydantic（`MissingGreenlet`）
- **唯一约束 + 查重**：`(ontology_id, api_name)` 有 UNIQUE + service 层先查再插；`get_object_type` 加 `DISTINCT ON`
- **前端必须跑 `npm run build`**：`tsc` 允许 `async onClick` 但 oxc 不允许，不等 pre-commit
- **多步写入原子性**：batch endpoint 单次提交，后端一个事务，禁客户端逐个调用
- **auto-save 回调只做两件事**：`markClean()` + 更新版本号；先 markClean 再 setState；**永不调全量状态重建函数**（[pipeline-builder-autosave-loop.md](docs/bugfix/pipeline-builder-autosave-loop-and-config-panel-dismiss.md)）
- **只读展示表不用 React Aria `Table`**：频繁卸载/重挂载的只读表用原生 `<table>`（ADR-013 R1，[datasource-schema-browse-table-crash.md](docs/bugfix/datasource-schema-browse-table-crash.md)）
- **新 connector 必须 live dry-run**：提交配置到 SeaTunnel 验证字段识别，不能只靠文档（[cdc-spike-report.md](docs/engineer/cdc-spike-report.md)）
- **外部系统按需 reconcile**：列表立即渲染 PG 存的 status（终态即真相），只 reconcile 非终态项；并行外部调用 `p-limit` 限流；详情页 mount 只加载主实体（[external-system-fetching.md](docs/engineer/external-system-data-fetching-best-practices.md)）

> 完整 15 条错误模式库见 [`docs/bugfix/`](docs/bugfix/)。修 bug 后必查：根因会不会在其他地方重复出现？是→立即修同类 + 写测试。

## 前端硬约束

- headless 行为层 React Aria Components（ADR-013）；样式 Tailwind v4.3（`frontend-standards.md` S4「暂不引入 Tailwind」已推翻）
- **组件复用最大化**：对象/属性/关系/动作在列表、图谱画布、详情面板、编辑表单四种上下文用同一组件；组件只管渲染交互，容器管布局，props 传数据回调不依赖全局状态
- **四态覆盖**：每个组件 loading/empty/error/正常；任何操作 100ms 无响应就反馈；键盘可达（Tab/Enter/Esc）
- **产品哲学**：把复杂留给自己，用户永远不需要理解 Gravitino/Iceberg/Doris；用业务语言建模；先走通再完美（功能 ≤1 周，超时拆分）；确认分级（低危弹窗/中危列影响/高危输名称）。详见 [`docs/engineer/frontend-best-practices.md`](docs/engineer/frontend-best-practices.md)

## 运维红线

| 组件 | 健康检查 | 内存 |
|------|---------|------|
| PostgreSQL | `pg_isready` | 512m/256m |
| Gravitino（含内置 Iceberg REST） | `/api/health`(8090) + `/iceberg/v1/config`(9001) | 1g/512m, -Xmx512m |
| RustFS | `/health`(9000) | 1g/512m |
| SeaTunnel | Zeta 自愈 | 512m/256m, -Xmx256m |
| Doris FE/BE | `/api/health` | 1g/512m, -Xmx512m |
| Trino (478) | `/v1/info` | 1g/512m, -Xmx512m |
| API | `/health` | 512m/256m |

**DuckDB Iceberg 扩展**（`~18MB`）超 gitcode 10MB 限制不入 git，部署时不依赖外网下载：主路径 Kestra 镜像预装（`infra/Dockerfile.kestra`）→ ACR 分发；备路径 `infra/extensions/`（gitignore）；兜底在线下载（不推荐）。见 [ADR-018 D1](docs/architecture/adr-018-pipeline-builder.md)。组件版本表/镜像表/端口表/升级指南见 [verification-guide.md](docs/engineer/verification-guide.md) + [deploy/README.md](deploy/README.md)。

## AI 助手工作指引

1. **实现状态优先** — 先查 [`docs/architecture/implementation-status.md`](docs/architecture/implementation-status.md)（✅/🟡/🔴），「关键断裂链路」「后续路标」是开发优先级权威
2. **文档优先** — 改动前读相关文档（见下「文档地图」）
3. **TDD 先行** — Red → Green → Refactor
4. **接口变更 = 修改 ICD**（`docs/architecture/icd-0N-*.md`，ICD-01~05）；技术决策变更 = 记 ADR（`docs/architecture/adr-0NN-*.md`，ADR-001~021 + adr-action-mutation-mapping）
5. **接线新组件** — 更新 `implementation-status.md`（🟡→✅）；引入新红线/命名规则则同步本文件

## 文档地图

- **架构/状态**：`architecture_plan.md`、`implementation-status.md`（**Service 清单/目录结构/实施路线/仍待联调 单一真相源**）、`action-architecture.md`、`ontology-tool-layer.md`、`textql-design.md`、`graph-reasoning-design.md`
- **设计**：`dataset-ontology-binding.md`（术语基准）、`multi-source-data-fusion-design.md`（ADR-014）、`action-sync-outbox-design.md`
- **工程**：`frontend-standards.md`、`frontend-best-practices.md`、`transaction-management-best-practices.md`、`ai-integration-guide.md`、`dev-workflow.md`、`verification-guide.md`
- **复盘**：`docs/bugfix/*`（15 条错误模式库实体）、`seatunnel-iceberg-rest-interop-postmortem.md`、`cdc-spike-report.md`
- **ADR/ICD**：`docs/architecture/adr-0NN-*.md`（001~021）、`docs/architecture/icd-0N-*.md`（01~05）、`docs/reference.md`（Palantir 范式源头）

> 以上路径相对本文件所在目录 `engines/gaia/`。
