# 工程原则与最佳实践

> 本文档记录本项目的工程原则与最佳实践，作为开发者的独立参考手册。不重复架构设计或实现细节。

***

## 一、系统工程原则（中国航天总体设计思想）

### 1.1 总体设计原则

| 原则             | 说明                         | 本项目体现                         |
| -------------- | -------------------------- | ----------------------------- |
| **一切从任务总目标出发** | 先定义"做什么"和"做到什么程度"，再决定"怎么做" | 关键能力等级指标（P95 延迟、QPS、吞吐）作为验收基线 |
| **逐层分解，全局最优**  | 各分系统独立优化不能损害整体性能           | 功能-性能-可靠性三维分解矩阵，每项能力有明确责任组件   |
| **总体架构师统一协调**  | 跨层变更必须经过总体评审               | 接口基线管控，ICD 冻结后任何修改需评审         |

#### 总体架构师职责

1. **接口基线管控**：所有 Layer 类的公开方法签名冻结后，任何修改需通过接口评审
2. **跨层协调**：一层实现变更影响其他层时（如 Iceberg 表结构变更影响 Doris 索引表），协调同步更新
3. **指标监控**：持续跟踪关键能力等级，任何指标劣化触发根因分析
4. **技术债务管理**：维护已知技术债务清单，设定偿还触发条件

### 1.2 综合集成方法

| 原则             | 说明                | 本项目体现                             |
| -------------- | ----------------- | --------------------------------- |
| **重视组网后的涌现行为** | 独立组件正常 ≠ 集成后正常    | FMEA 分析每个组件失效的级联影响                |
| **定义系统运行状态向量** | 关键指标有正常/预警/告警三级阈值 | 运行状态向量表 + Prometheus AlertManager |
| **全链路故障注入验证**  | 不仅验证正常路径，还要验证异常路径 | 阶段 5 故障注入测试（kill 组件、注入网络延迟）       |

#### 故障模式与影响分析（FMEA）标准实践

每个组件失效必须回答三个问题：

1. 对最终用户的影响是什么？
2. 降级措施是什么？
3. 告警规则是什么？

#### 系统运行状态向量

| 指标                | 正常阈值         | 预警阈值           | 告警阈值    |
| ----------------- | ------------ | -------------- | ------- |
| Doris 索引同步延迟      | < 30s        | 30s \~ 60s     | > 60s   |
| Trino 查询 P95 延迟   | < 500ms      | 500ms \~ 1s    | > 1s    |
| 物理对象查询 P95 延迟     | < 200ms      | 200ms \~ 500ms | > 500ms |
| 查询成功率             | > 99.9%      | 99% \~ 99.9%   | < 99%   |
| PostgreSQL 连接池使用率 | < 50%        | 50% \~ 80%     | > 80%   |
| SeaTunnel 主流水线吞吐  | > 10K rows/s | 5K \~ 10K      | < 5K    |

### 1.3 技术状态管理

#### 架构决策记录（ADR）规范

每个关键技术选择必须记录为 ADR，包含以下字段：

| 字段       | 说明            |
| -------- | ------------- |
| **背景**   | 为什么需要做这个决策    |
| **决策**   | 选择了什么方案       |
| **后果**   | 决策带来的正面和负面影响  |
| **替代方案** | 考虑过但未选择的方案及原因 |
| **审批日期** | 决策生效日期        |

#### 接口控制文档（ICD）基线管理

- 每一层的公开方法签名作为接口基线，随代码版本化
- 任何修改需通过测试和评审
- 当前 ICD 基线版本：v1.0（覆盖 5 个 Layer 接口）

#### 配置基线化要求

| 要求           | 说明                                         |
| ------------ | ------------------------------------------ |
| 所有组件配置纳入版本控制 | `config/` 目录全部 Git 管理                      |
| 环境差异通过环境变量覆盖 | `.env.example` 定义模板，`.env` 不入库             |
| 严禁手动热改       | 生产环境配置变更必须走 CI/CD 流水线（Git PR → CI 验证 → 部署） |

### 1.4 验证与确认（V\&V）

#### V 模型分层测试策略

| 测试层级       | 范围                             | 工具                      | 覆盖率目标                 | 执行频率        |
| ---------- | ------------------------------ | ----------------------- | --------------------- | ----------- |
| **单元测试**   | 每个 Layer 类的方法，Mock 底层客户端       | pytest + pytest-asyncio | 行覆盖率 > 80%，异常路径 100%  | 每次提交        |
| **接口集成测试** | Service 调用真实 Layer，Mock 底层     | pytest + testcontainers | 每个 Service 方法至少 2 个场景 | 每次提交        |
| **系统测试**   | 全真实组件 docker-compose，4 个场景 E2E | pytest + docker-compose | 4 个核心场景全覆盖            | 每次 PR / 每日  |
| **性能测试**   | 单独环境，用具体负载测试指标                 | locust / k6             | 全部关键能力等级指标            | 每次组件升级 / 每周 |

#### 异常覆盖率要求

每个方法的异常路径必须有明确断言：

| 异常场景  | 预期行为           |
| ----- | -------------- |
| 连接超时  | 返回明确错误码，不崩溃    |
| 权限拒绝  | 返回 403，不泄露内部信息 |
| 数据冲突  | 返回 409，附带冲突详情  |
| 依赖不可用 | 触发降级逻辑，记录告警日志  |

#### 回归测试自动化

每一次组件升级（SeaTunnel 镜像更新、Doris 版本升级、Gravitino 版本升级），必须触发完整的接口集成测试 + 系统测试套件。

### 1.5 并行工程

| 原则            | 说明                                                     |
| ------------- | ------------------------------------------------------ |
| **接口先于实现**    | 先冻结 ICD 基线，再并行开发各层                                     |
| **持续集成要求**    | 每次提交触发单元测试 + 接口集成测试                                    |
| **风险驱动的迭代顺序** | Sprint 0 优先验证 P0 风险（RustFS + Iceberg 兼容性、Trino 时间旅行语法） |

### 1.6 全生命周期思维

#### 可观测性设计要求

| 层次               | 实现方式                              | 内容                                                        |
| ---------------- | --------------------------------- | --------------------------------------------------------- |
| **结构化日志**        | Python `logging` + JSON 格式        | 每条日志含 `trace_id`、`span_id`、`layer`、`method`、`duration_ms` |
| **trace\_id 传递** | FastAPI 中间件生成，通过 contextvars 跨层传递 | 一次请求在所有 Layer 调用中共享同一 trace\_id                           |
| **Metrics**      | Prometheus `prometheus_client`    | 每层对外调用的耗时直方图、状态码计数器、错误率                                   |
| **Grafana 面板**   | 预置 dashboard JSON                 | 各层健康度总览、查询延迟分布、同步延迟趋势、错误率热力图                              |

#### 运维红线与自愈策略

| 组件           | 健康检查                      | 重启策略                      | 备份策略                 |
| ------------ | ------------------------- | ------------------------- | -------------------- |
| PostgreSQL   | `pg_isready`              | `restart: unless-stopped` | 每日 pg\_dump + WAL 归档 |
| Gravitino    | HTTP `/api/health`        | `restart: unless-stopped` | 定期备份 Gravitino 存储目录  |
| RustFS       | HTTP `/minio/health/live` | `restart: unless-stopped` | 依赖对象存储自身冗余           |
| Iceberg REST | HTTP `/v1/config`         | `restart: unless-stopped` | 元数据在 RustFS，无需额外备份   |
| SeaTunnel    | Zeta 集群自愈                 | `restart: unless-stopped` | 配置在 Git，状态在 Zeta     |
| Doris FE/BE  | HTTP `/api/health`        | `restart: unless-stopped` | 定期备份 FE 元数据          |
| Trino        | HTTP `/v1/info`           | `restart: unless-stopped` | 无状态，无需备份             |

#### 架构演进触发条件

| 扩展点                    | 当前方案                      | 触发条件                           | 目标方案                                     |
| ---------------------- | ------------------------- | ------------------------------ | ---------------------------------------- |
| properties JSONB → 关系表 | JSONB 存储                  | 对象类型数量 > 100 且属性变更频繁           | 拆分为独立 properties 表                       |
| SeaTunnel → Flink 替换   | SeaTunnel 2.3.13          | 无法满足复杂流处理需求                    | 替换 Pipeline 层为 Flink                     |
| Doris 索引自动更新           | 手动触发                      | ObjectType 属性变更频率 > 每周 10 次    | OntologyService 自动触发                     |
| Trino 同步 → 异步          | trino-python-client dbapi | 虚拟表(Virtual Table)查询并发 > 50 QPS 且 P95 > 500ms | 替换为 `trino.async_client`                 |
| 单机 → 集群                | 开发环境单节点                   | 生产环境上线                         | PostgreSQL HA、Gravitino HA、Doris FE 3 节点 |

***

## 二、Pythonic 编码规范（强制）

| # | 规范                           | 说明                                                     | 违反后果                                     |
| - | ---------------------------- | ------------------------------------------------------ | ---------------------------------------- |
| 1 | **SQLAlchemy 2.0 async ORM** | 用 `DeclarativeBase` 定义表映射，`select()` / `insert()` 构建查询 | 禁止裸写 SQL 字符串                             |
| 2 | **pydantic v2 API 校验**       | 请求/响应校验、序列化，与 ORM 模型分离                                 | 禁止在 API 层直接暴露 ORM 对象                     |
| 3 | **类型注解全覆盖**                  | 所有函数签名带类型注解，通过 `mypy --strict`                         | 禁止 `Any` 泛滥                              |
| 4 | **`datetime.now(UTC)`**      | 统一使用 `datetime.now(UTC)`                               | 禁止 `datetime.utcnow()`（Python 3.12+ 已废弃） |
| 5 | **`uuid`** **生成主键**          | 使用 `uuid.uuid4().hex` 生成 32 字符主键                       | 禁止数据库自增 ID                               |
| 6 | **`async`** **全链路**          | 数据库、HTTP、文件 IO 全部异步                                    | 禁止阻塞事件循环的同步调用                            |
| 7 | **`ruff`** **格式化 + lint**    | 统一代码风格，替代 black + isort + flake8                       | 禁止未通过 ruff 检查的代码合入                       |

### 2.1 代码风格配置

```toml
[tool.ruff]
line-length = 120
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "N", "W", "UP"]

[tool.ruff.format]
quote-style = "double"

[tool.mypy]
python_version = "3.12"
strict = true
ignore_missing_imports = true
```

### 2.2 关键代码模式

```python
# 主键生成
import uuid

def new_uuid() -> str:
    return uuid.uuid4().hex

# 时间戳
from datetime import UTC, datetime

def utcnow() -> datetime:
    return datetime.now(UTC)

# SQLAlchemy 查询（禁止裸 SQL）
from sqlalchemy import select

stmt = select(OntologyModel).where(OntologyModel.api_name == api_name)
result = await session.execute(stmt)
return result.scalar_one_or_none()
```

#### 事务管理（unit-of-work 模式）

多步写入必须用 Service 层 `transaction()` 包裹，低层方法传 `auto_commit=False`。
详见 [`transaction-management-best-practices.md`](./transaction-management-best-practices.md)。

```python
# ✅ ActionType 写入 + 版本快照原子提交
async with self.transaction():
    created = await self._metadata.create_action_type(at, auto_commit=False)
    await self._publish_version_snapshot(created)  # 内部 auto_commit=False
```

**铁律**：低层 helper 不 commit（只 flush）；事务边界在 Service；一个 use-case 一个提交点。

***

## 三、架构红线（不可违反）

以下规则在任何情况下不得违反。违反即阻塞 PR 合入。

| # | 红线                                    | 说明                                                              |
| - | ------------------------------------- | --------------------------------------------------------------- |
| 1 | **Gravitino 仅管理物理数据资产**               | 注册 Iceberg 表、Doris 外表、View 定义、RBAC 和血缘。不存储业务本体元数据               |
| 2 | **PostgreSQL 存储业务本体元数据**              | Ontology、ObjectType、PropertyDef、LinkType 等全部领域模型。不存物理表元数据、不参与查询 |
| 3 | **主数据统一在 RustFS/S3 + Iceberg**        | Iceberg REST Catalog 指向 S3，SeaTunnel 主流水线唯一写入路径                 |
| 4 | **Doris 严格作为索引加速层**                   | 仅存主键 + 索引列 + 常用热点属性，不存全量明细、大字段、二进制                              |
| 5 | **Trino 作为主要查询引擎**                    | 通过 Gravitino Connector 联邦查询 Iceberg，承载全量数据读取                    |
| 6 | **SeaTunnel 承担 PipelineBuilder（外部源→Iceberg 搬运）** | SeaTunnel 只负责把外部数据源搬进 Iceberg/TimescaleDB；Iceberg→Doris/Neo4j/PostGIS 的写入不走 SeaTunnel，由 ObjectIndexFunnel + OutboxExecutor 直连各引擎（2026-07 去 SeaTunnel 化） |
| 7 | **移除 Redis**                          | 用 Doris 自带缓存 + Iceberg ACID + 分区策略替代                            |

### 3.1 各层职责红线

| 层                         | 允许                       | 禁止              |
| ------------------------- | ------------------------ | --------------- |
| **Metadata (PostgreSQL)** | 存业务本体元数据                 | 存物理表元数据、参与查询    |
| **Catalog (Gravitino)**   | 注册托管资产、虚拟表(Virtual Table)、RBAC、血缘       | 存业务本体元数据、参与数据计算 |
| **Dataset (Iceberg)**     | 存全量明细 + 历史快照、时间旅行        | 做在线查询、做检索加速     |
| **Index (Doris)**         | 存主键 + 索引列 + 热点属性、全文/向量检索 | 存全量明细、作为写入入口    |
| **Pipeline (SeaTunnel)**  | 外部数据源采集/清洗/写入 Iceberg + CDC + 文件/Kafka/时序同步          | 做元数据管理、做查询路由、做 Iceberg→Doris 同步（已改 Python 直连）    |
| **Engine (Trino)**        | 联邦查询、全量数据加载、View 执行      | 做主数据存储          |

***

## 四、分层隔离原则

### 4.1 核心原则

| 原则                       | 说明                                                                                                         |
| ------------------------ | ---------------------------------------------------------------------------------------------------------- |
| **每层独立目录，职责单一**          | `layers/catalog/`、`layers/metadata/`、`layers/dataset/`、`layers/index/`、`layers/pipeline/`、`layers/engine/` |
| **层间通过明确的类方法调用**         | 不引入 interface 抽象，直接依赖具体类                                                                                   |
| **替换组件 = 替换整个 layer 目录** | 换目录 + 改 services 的 import 路径                                                                               |
| **每层只做一件事**              | Catalog 管物理资产，Metadata 管业务元数据，Dataset 管持久化，Index 管加速，Pipeline 管流转，Engine 管查询                               |

### 4.2 层间依赖方向

```
Routes（HTTP 薄层）
    ↓
Services（业务编排层）
    ↓
Layer Implementations（层实现，可并行替换）
    ↓
Core Models（领域模型，纯类型，零外部依赖）
```

- Services 直接依赖 layers/ 中的具体类，通过构造函数注入
- 层与层之间不直接互相调用（如 Index 层不调 Dataset 层）
- 跨层协调由 Service 层编排

### 4.3 目录结构

```
src/ontology/
├── core/
│   ├── models/          # SQLAlchemy ORM 模型（表映射）
│   └── schemas/         # pydantic Schema（API 校验/序列化）
├── layers/
│   ├── catalog/         # GravitinoRegistry
│   ├── metadata/        # PostgresMetaStore
│   ├── dataset/         # IcebergStore
│   ├── index/           # DorisIndexStore
│   ├── pipeline/        # SeaTunnelEngine
│   └── engine/          # TrinoQueryEngine
├── services/            # 5 个业务编排 Service
├── routes/              # FastAPI 路由（薄层）
├── config/              # 依赖注入容器 + 配置
├── middleware/           # 日志、错误处理、trace_id
└── main.py
```

***

## 五、代码审查检查清单

PR Reviewer 必须逐项确认以下清单。任一项不通过即阻塞合入。

### 5.1 架构红线检查

- [ ] **是否违反架构红线**：确认未违反第三节中任何一条红线
- [ ] **Gravitino 未存储业务本体元数据**
- [ ] **PostgreSQL 未存储物理表元数据**
- [ ] **Doris 未存储全量明细字段**
- [ ] **未引入 Redis 依赖**

### 5.2 编码规范检查

- [ ] **是否使用 SQLAlchemy ORM 而非裸 SQL**：所有数据库操作通过 `select()` / `insert()` 等 ORM 方法
- [ ] **是否有类型注解**：所有函数签名带完整类型注解
- [ ] **是否通过 ruff 检查**：`ruff check src/` 零错误
- [ ] **是否通过 mypy 检查**：`mypy src/` 零错误
- [ ] **时间戳使用** **`datetime.now(UTC)`**：未出现 `utcnow()`
- [ ] **主键使用** **`uuid`** **生成**：未使用自增 ID

### 5.3 异常路径检查

- [ ] **是否处理了异常路径**：连接超时、权限拒绝、数据冲突、依赖不可用
- [ ] **异常是否返回明确错误码**：不泄露内部信息
- [ ] **降级逻辑是否正确触发**：Doris 不可用时降级 Trino 扫描
- [ ] **多步写入是否原子化**：用 `async with self.transaction():` 包裹，低层方法 `auto_commit=False`（详见 [事务管理最佳实践](./transaction-management-best-practices.md)）
- [ ] **best-effort 操作是否可观测**：事务内不吞异常（raise），事务外记日志，禁止 `except Exception: pass`

### 5.4 测试检查

- [ ] **是否有对应的单元测试**：覆盖正常路径和异常路径
- [ ] **异常路径覆盖率 100%**
- [ ] **行覆盖率 > 80%**

### 5.5 文档检查

- [ ] **是否更新了 ICD**（如接口变更）：接口签名变更需同步更新 ICD 文档
- [ ] **是否记录了 ADR**（如技术决策变更）：新增或修改技术选型需记录 ADR
- [ ] **是否更新了配置基线**：新增配置项需同步更新 `.env.example`

***

## 六、运维检查清单

### 6.1 上线前检查

- [ ] **健康检查是否配置**：所有组件在 docker-compose.yml 中配置了 healthcheck
- [ ] **重启策略是否配置**：所有组件配置了 `restart: unless-stopped`
- [ ] **日志是否结构化 + 含 trace\_id**：每条日志含 `trace_id`、`span_id`、`layer`、`method`、`duration_ms`
- [ ] **Metrics 是否覆盖关键路径**：
  - 每层对外调用（SQL、REST）的耗时直方图
  - 状态码计数器
  - 错误率
- [ ] **降级策略是否可触发**：
  - Doris 不可用 → Trino 扫描 Iceberg
  - IcebergStore 不可用 → Trino 按 ID 查询
  - Gravitino 不可用 → 物理表查询绕过权限（缓存表路由）
- [ ] **备份脚本是否就绪**：
  - PostgreSQL：每日 pg\_dump + WAL 归档
  - Gravitino：定期备份存储目录
  - Doris FE：定期备份元数据

### 6.2 告警规则检查

| 告警项                  | 触发条件                                        | 严重级别 |
| -------------------- | ------------------------------------------- | ---- |
| SeaTunnel 主流水线崩溃     | `seatunnel_main_status != RUNNING` 持续 > 60s | P0   |
| RustFS 不可用           | `rustfs_health != ok`                       | P0   |
| PostgreSQL 不可用       | `pg_health != ok`                           | P0   |
| Trino 不可用            | `trino_health != ok`                        | P0   |
| Gravitino 不可用        | `gravitino_health != ok`                    | P0   |
| Doris 不可用            | `doris_fe_health != ok`                     | P1   |
| 索引同步延迟 > 60s         | `doris_sync_lag > 60s`                      | P1   |
| 查询成功率 < 99%          | `query_success_rate < 0.99`                 | P1   |
| PostgreSQL 连接池 > 80% | `pg_pool_usage > 0.8`                       | P2   |

### 6.3 组件升级检查

每次组件升级（镜像版本变更）必须执行：

- [ ] 完整接口集成测试套件通过
- [ ] 系统测试（4 个核心场景）通过
- [ ] 性能测试关键指标未劣化
- [ ] 更新组件版本矩阵文档

***

## 附录：ADR 索引

| ADR #   | 决策                              | 背景                                 | 替代方案                         |
| ------- | ------------------------------- | ---------------------------------- | ---------------------------- |
| ADR-001 | 使用 Doris 作索引加速层                 | Trino 全表扫延迟不可控                     | 直接用 Trino 计算；用 Elasticsearch |
| ADR-002 | 使用 SeaTunnel 而非 Flink           | 配置驱动、与 Gravitino 深度集成、部署轻量         | Flink；Spark Streaming        |
| ADR-003 | 使用 RustFS 而非 MinIO              | MinIO 开源版 2025.12 进入维护模式           | MinIO（已弃用）；Ceph；直接 S3        |
| ADR-004 | 使用 PostgreSQL 存储业务本体元数据         | 需要事务性、强一致性、成熟生态                    | MySQL；etcd                   |
| ADR-005 | ObjectType.properties 当前用 JSONB | 初期灵活迭代，后期按需拆分                      | 直接建关系表；纯文档数据库                |
| ADR-006 | 使用 Python + FastAPI             | Python 在 Iceberg/Trino/S3 官方库支持更成熟 | TypeScript；Go                |
| ADR-007 | Iceberg REST 经 pyiceberg 子类化访问 + 数据读写走 Trino | Gravitino memory backend 下 pyiceberg 标准用法 401/400 三关全失效、REST `/scan` 500 | 裸 httpx；Service 层降级编排；全走 REST；换 Gravitino backend。详 [adr-007](../architecture/adr-007-iceberg-rest-catalog-access.md) |

***

## 附录：ICD 基线索引

| ICD #  | 接口                | 版本   | 关键方法                                                                        |
| ------ | ----------------- | ---- | --------------------------------------------------------------------------- |
| ICD-01 | PostgresMetaStore | v1.0 | `create_ontology`, `get_object_type`, `create_object_type`                  |
| ICD-02 | GravitinoRegistry | v1.0 | `register_dataset`, `is_view`, `check_access`, `resolve_physical_table`, `get_table_columns`（`create_view`/`get_view` 已删除） |
| ICD-03 | IcebergStore      | v1.0 | `load_by_ids`, `append`, `get_snapshots`                                    |
| ICD-04 | DorisIndexStore   | v1.0 | `query`, `create_index_table`, `upsert`                                     |
| ICD-05 | TrinoQueryEngine  | v1.0 | `query`                                                                     |


