# ADR-006: 使用 Python + FastAPI 而非 TypeScript / Go

| 字段 | 内容 |
| ---- | ---- |
| **状态** | 已采纳 |
| **决策日期** | 2026-05（架构 v5 终稿） |
| **影响层** | 全栈（`src/ontology/` 全部后端代码） |
| **相关 ICD** | ICD-01 ~ ICD-05（所有 Layer 均用 Python 实现） |
| **关联文档** | `architecture_plan.md` §1.3 组件版本矩阵、`pyproject.toml` |

---

## 背景

后端主语言需与数据栈生态（Iceberg / Trino / S3 / Doris / Gravitino / SeaTunnel）的官方客户端库深度集成。候选语言：

| 方案 | 优势 | 劣势 |
| ---- | ---- | ---- |
| **Python + FastAPI** | 数据/AI 生态最强 | 性能不如 Go |
| **TypeScript (Node.js)** | 前后端同构 | 数据栈官方库少 |
| **Go** | 性能好、并发强 | 数据栈生态弱 |

## 决策

**采用 Python 3.12+ + FastAPI 作为后端主语言。**

### 1. 数据栈官方库支持成熟度（决定性因素）

本项目深度依赖以下开源组件的 Python 客户端，且均有官方或一线维护的库：

| 组件 | Python 库 | 成熟度 |
| ---- | --------- | ------ |
| Iceberg | `pyiceberg`（Apache 官方） | ✅ 官方维护，支持 REST Catalog、schema 演进、snapshot |
| Trino | `trino-python-client`（Trino 官方） | ✅ 官方维护 |
| S3 / RustFS | `aioboto3` / `httpx` | ✅ 成熟 |
| PostgreSQL | `asyncpg` + `sqlalchemy[asyncio]` | ✅ 一线维护 |
| Doris | `aiomysql`（MySQL 协议兼容） | ✅ 成熟 |
| Gravitino | `httpx`（REST API） | ✅ 通用 |
| SeaTunnel | `jinja2` + `httpx`（REST API） | ✅ 通用 |
| Neo4j | `neo4j[async]`（官方） | ✅ 官方维护 |
| LLM / AI | `pydantic-ai` | ✅ Python 是 AI 一等公民 |

TypeScript 在这些组件上缺乏官方客户端（Trino/Iceberg/Gravitino 无官方 TS 库），需自行封装 REST 调用或用第三方不维护的库，风险高。Go 同理。

### 2. AI 原生能力（项目核心定位）

Gaia 定位为「智能 Coding Agent 上下文」+「AI 原生本体建模」，LLM 集成是核心能力（TextQL ADR-012、AG-UI Agent ADR-009/015、BuildWith 脚手架）。Python 是 AI/ML 生态的一等公民：`pydantic-ai`、`ibis-framework`、向量计算库等均 Python 优先。

### 3. FastAPI 契合 API 形态

- **异步优先**（async/await）符合项目编码规范红线 #6
- **pydantic v2 校验**符合红线 #2（API 校验与 ORM 分离）
- **依赖注入**原生支持，匹配 Container 模式
- **OpenAPI 自动生成**，API 契约自文档化
- 性能足够：本项目瓶颈在数据栈（Doris/Iceberg/Trino）IO，而非 Python 应用层

### 4. 类型安全与工程规范

- Python 3.12+ + `mypy --strict` 达到接近静态语言的类型安全（红线 #3）
- `ruff` 格式 + lint（红线 #7）
- `uv` 包管理 + `uv.lock` 可复现构建
- 类型注解全覆盖，禁止 `Any` 泛滥

## 后果

### 正面

- **数据栈集成零摩擦**：所有组件有成熟 Python 库，无需造轮子
- **AI 能力一等公民**：pydantic-ai / ibis / 向量计算原生支持
- **开发效率高**：类型注解 + pydantic + FastAPI 自动文档，迭代快
- **生态丰富**：测试（pytest）、迁移（Alembic）、监控（prometheus-client）工具链完整

### 负面 / 已知限制

- **性能不如 Go**：CPU 密集型场景不如 Go。但本项目是 IO 密集型（数据库/REST/S3 调用为主），异步 IO 已能榨干并发，Python 应用层非瓶颈
- **GIL 限制**：CPU 密集型多线程受限。通过多进程（gunicorn/uvicorn workers）或异步 IO 规避
- **部署体积**：Python 镜像比 Go 静态二进制大。通过多阶段构建 + 国内源加速缓解
- **`uv run` 在本项目不可靠**：实际执行用 `.venv/bin/python` 直调（见 CLAUDE.md 启动开发环境）

## 替代方案（否决）

| 方案 | 否决原因 |
| ---- | -------- |
| **TypeScript (Node.js)** | 前后端同构优势（共享类型）有吸引力，但数据栈（Iceberg/Trino/Gravitino/SeaTunnel）无官方 TS 客户端，需大量自行封装 REST，风险与维护成本高；AI 生态弱于 Python |
| **Go** | 性能更好、并发模型优雅，但数据栈生态弱（无 pyiceberg 等价物）、AI 库少、泛型支持较新；本项目 IO 密集型，Go 性能优势无法兑现 |

## 回归条件

出现以下任一情况，需重新评估主语言：

1. Python 应用层成为性能瓶颈（CPU 密集型逻辑占比上升，异步 IO 无法覆盖），且无法通过横向扩容解决
2. 数据栈核心组件（Iceberg/Trino）停止 Python 库维护，且无替代
3. 项目定位从「AI 原生数据架构」转向「极致低延迟 OLTP」，此时 Go 的性能优势变得关键

## 修订记录

- **2026-05 初始决策**：架构 v5 终稿选定 Python 3.12 + FastAPI
- **2026-07**：后端 1268 测试函数、22 个 Service、8 个 Layer 全部用 Python 实现并稳定运行，本决策持续有效
