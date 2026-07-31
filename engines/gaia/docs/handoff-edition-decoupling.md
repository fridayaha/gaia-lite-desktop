# 交接文档：Gaia lite 桌面单机版解耦特性（edition-decoupling）

> 分支 `feat/edition-decoupling`（从 develop 切出，**未 push，不要合 develop**）。
> 阶段 A（解耦基础设施 A1-A5）+ 阶段 B（桌面版功能 B1-B6）已全部完成，共 11 commits。
> 下一阶段 C（桌面打包 C1-C3）。**本文档是下次会话的输入**——先读「§四 验证环境」确认能跑起来，再做 C1 mini-plan。

完整方案：`/Users/friday/.claude/plans/kind-napping-wozniak.md`（架构/体积预算/保留砍掉清单/双版本流水线/16 子任务表/依赖图）。
进度记忆：`/Users/friday/.claude/projects/-Users-friday-workspace-union-agent/memory/gaia-lite-edition-decoupling.md`。

---

## 一、目标与总览

一个代码库同时产出云版（full）和桌面版（lite, Tauri+SQLite+DuckDB, <300MB, mac-arm64/win-x64）。`EDITION` 环境变量贯穿三层：依赖层（pyproject extras）→ 装配层（container/settings）→ 前端层（vite `__EDITION__`）。

**完成标志（阶段 B 已达成）**：`EDITION=lite` 起后端 + 前端，能完成「创建本体 → 连 PG/CSV/SQLite 数据源 → AI 对话建模 → 执行 Action」端到端流程。

---

## 二、已完成 commits

### 阶段 A — 解耦基础设施（A1-A5）

| ID | commit | 内容 |
|---|---|---|
| A1 | `2b4f77fa` | container.py 9 Layer+25 Service 顶层 import → TYPE_CHECKING + lazy import；settings 加 `edition` 字段 |
| A2 | `ceae8274` | pyproject 拆 `[full]`/`[lite]` extras；Dockerfile `ARG EDITION`；9 重依赖移 `[full]`，`[lite]` 加 aiosqlite/duckdb |
| A3 | `d739a857` | container.py 7 Layer property lite 抛 `EditionUnavailableError`（engine 留 B2）；5 Service IcebergStore/TrinoQueryEngine 移 TYPE_CHECKING |
| A4 | `a5ea9915` | main.py lifespan 按 edition 跳过 7 个后台任务 + bootstrap/reconcile |
| A5 | `af5657e4` | database.py lite 切 SQLite DSN；conftest lite `collect_ignore_glob` 跳过 15 cloud-only 模块；cloud-only 运行时测试加 skipif |

### 阶段 B — 桌面版功能（B1-B6）

| ID | commit | 内容 |
|---|---|---|
| B1 | `d69f5c5b` | SQLite 元数据层：lite lifespan `create_all` 建表 + `bootstrap_default_containers`；settings `lite_db_path` |
| B2 | `50c89e31` | DuckDB 联邦引擎：`layers/engine/duckdb_engine.py` + `base.py` QueryEngine Protocol；container.engine lite→DuckDBEngine |
| B3 | `86274a74` | ObjectQueryService 改 DuckDB dialect：sql_compiler 加 `duckdb` dialect；VIRTUAL 联邦查询 + 跨源 JOIN；MANAGED guard |
| B4 | `8c01729d` | 数据源插件：`plugins/connectors/{base,postgres,mysql,csv_file,sqlite}.py` + ConnectorRegistry；DataSourceService lite 路径 |
| B5 | `782377dd` | Action 简化：lite 不产 INDEX/ARCHIVE/EMBEDDING outbox；effect 过滤 write_back/kafka_topic |
| B6 | `f8ae98e6` | 前端 VITE_EDITION：`__EDITION__` define + GraphExplorePage 懒加载条件路由；lite 砍图探索省 1MB |

---

## 三、关键设计决策（不要误改）

### B1：PostgresMetaStore 跨方言兼容，**不做 dialect dispatch**
`postgresql.insert().on_conflict_do_nothing()` / `.with_for_update(skip_locked=True)` / `update().returning()` / `.properties[k].as_string()` 经实测在 SQLite 上**原样工作**（PG+SQLite 都支持 ON CONFLICT，SQLite 3.35+ 支持 RETURNING，SQLAlchemy 自动方言化 JSON，with_for_update 静默忽略）。现有 2124 单测本就跑在 SQLite `db_session` fixture 上。故 `postgres_meta_store.py` 只加了说明注释，**不要改成 sqlite.insert**——无用代码且破坏现有测试。

### B2：QueryEngine Protocol + DuckDB 单连接 asyncio.Lock
`layers/engine/base.py` 定义 `QueryEngine` Protocol（6 方法），Trino/DuckDB 共实现。5 个 Service 的 `engine` 参数从 `TrinoQueryEngine` 改为 `QueryEngine`（按契约依赖）。DuckDB Python 连接非线程安全 → 单连接 + `asyncio.Lock` 串行化 + `asyncio.to_thread`。DuckDB 专属方法（`execute`/`attach`/`detach`/`close`）不在 Protocol。

### B3：lite 只支持 VIRTUAL 本体，MANAGED guard 拦截
DuckDB 物理引用与 Trino VIRTUAL 路径同构（`catalog.schema.table`），catalog = `src_<datasource api_name>`（DuckDB ATTACH 别名）。MANAGED 托管表 lite 不做（红线下砍），`_compile_and_run` lite 分支对 MANAGED 抛 `OntologyError(EDITION_UNAVAILABLE)`。

### B4：CSV 走主库表，其余 ATTACH；不删 CAPABILITY_MAP
CSV connector 把文件导入主库一张表（`CREATE TABLE AS SELECT FROM read_csv_auto`，catalog=main），PG/MySQL/SQLite 走 `ATTACH ... (TYPE <scanner>)`。`LITE_CAPABILITY_MAP` 新建四类（不删原表，full 国产库/湖仓全保留）。DataSourceService `_duckdb` property cast 解 QueryEngine Protocol 的 attach/execute/detach 限制。

### B6：方案 B（不彻底砍 cytoscape）
OntologyGraph（本体建模画布，纯前端 lite 有用）保留 → cytoscape.esm + 4 扩展仍打包；maplibre + GraphExplorePage chunk 消失（tree-shake）。lite build 4MB vs full 5MB。

---

## 四、验证环境与命令

### Python 后端
- **.venv 位置**：`engines/gaia/.venv`（不是仓库根）。当前 sync 了 `dev+full+lite` 三 extras（duckdb + asyncpg 都在）。
- **跑 lite 测试**：`EDITION=lite .venv/bin/python -m pytest tests/unit --no-cov -q`（env var 触发 lite 代码路径，不依赖是否装 asyncpg）。
- **跑 full 测试**：`.venv/bin/python -m pytest tests/unit --no-cov -q`。
- **真 lite venv 验证 ImportError**：`uv sync --extra lite`（A5 验过；当前 .venv 是三 extras）。
- **后端/Alembic 必须用 `.venv/bin/python` / `.venv/bin/alembic` 直调，不能用 `uv run`**（此项目 uv run 不可靠）。
- **lint**：`.venv/bin/ruff check src/ && .venv/bin/ruff format --check src/ && .venv/bin/mypy src/`（mypy 基线 51 错误，全预存）。

### 前端（web-ui）
- 目录 `engines/gaia/src/web-ui`。
- **本地装依赖坑**：`pnpm install` 因上层 workspace prefix 误判 "Already up to date" 不装 → 用 `npm install --legacy-peer-deps`（peer 冲突 assistant-ui）。**package-lock.json 勿提交**（项目用 pnpm-lock.yaml，已 gitignore 或手动删）。
- **构建**：`EDITION=full npx vite build` / `EDITION=lite npx vite build`（vite 8.2.0）。
- **typecheck**：`npx tsc -b`（4 个预存 auth-client/rxjs 错误，Dockerfile 构建 skip tsc -b 规避）。
- **测试**：`npx vitest run`（4 文件 25 测试 fail 全预存 localStorage.clear 环境性）。

### 真实 lite 端到端冒烟（手动）
```bash
EDITION=lite LITE_DB_PATH=/tmp/smoke.db .venv/bin/python -c "
import asyncio; from unittest.mock import MagicMock
from ontology.main import lifespan
async def main():
    async with lifespan(MagicMock()): pass
asyncio.run(main())
"  # 应输出 49 表建好 + 默认 org/space/project 落库
```

---

## 五、已知问题（非本次引入，勿重复定位）

| 问题 | 表现 | 根因 |
|---|---|---|
| full 3 测试 fail | `test_datasource_multi_source`（2）+ `test_ontology_service_batch`（1） | HEAD 预存 CDC/Gravitano 环境性（外部服务未启） |
| web-ui 4 文件 25 测试 fail | `localStorage.clear is not a function` | HEAD 预存 jsdom localStorage mock 环境性 |
| mypy 51 错误 | action_service 1920/1929 source_expression、routes/authz 等 | HEAD 预存 |
| ruff 2 错误 | ai.py / ai_agent.py import-sort | HEAD 预存（A1 前就有） |

---

## 六、下一阶段 C — 桌面打包（C1-C3）

> 目标：lite 版打成 mac-arm64 / win-x64 安装包，控 300MB。依赖 B 全部完成。

| ID | 子任务 | 改动文件 | 验证标准 |
|---|---|---|---|
| **C1** | PyInstaller 后端打包 | 新建 `gaia-lite.spec` | 打出单可执行 `gaia-lite-backend`；本地起 `/health` 通；hiddenimports 含 duckdb/SQLAlchemy dialects |
| **C2** | Tauri 壳 + sidecar + 反代 | 新建 `tauri/` 工程 | webview 加载前端 + 反代 backend；SSE 流式通；端到端桌面体验 |
| **C3** | 体积优化 + 跨平台构建 | `gaia-lite.spec`、Tauri config | mac-arm64/win-x64 两包均 <300MB；UPX 压缩 |

### C1 关键风险与要点
- **hiddenimports**：SQLAlchemy dialects（sqlite/duckdb）、duckdb native、插件 connector。
- **`--collect-binaries duckdb`**：确保原生库打进（duckdb ~40MB，分平台各一）。
- **`--exclude-module`**：tkinter/unittest/test 省体积；砍 onnxruntime/pyiceberg/aiobotocore/neo4j/trino/asyncpg/aiomysql。
- **`--onefile` vs `--onedir`**：onedir 启动更快、体积略大。
- **AI_MODEL**：lite 用云端 LLM（DeepSeek/GLM），不打包本地 LLM。

### C2 关键风险
- **SSE 反代缓冲**：Tauri 反代 `/ai/agent` 必须禁缓冲（等价 nginx `proxy_buffering off`）；前端用 fetch+ReadableStream（不依赖 EventSource）。
- **同源反代**：Tauri 侧把 webview origin 下 `/ontologies`/`/api`/`/ai`/`/objects`/`/actions`/`/health` 转发到 Python sidecar 端口（规则照搬 `vite.config.ts` proxy + `deploy/nginx/web-ui.conf`）。
- **sidecar 启动**：PyInstaller bundle 作 `bundle.externalBin`，启动时 `Command::sidecar` spawn，监听 `127.0.0.1:动态端口`。
- **macOS 签名/公证**：需 Apple Developer ID（未签名可先出未签名版）。

### C3 体积预算（方案文件详）
| 部分 | 大小 |
|---|---|
| Tauri 壳 | ~8 MB |
| 前端 dist（lite） | ~4 MB |
| Python 运行时 + 核心依赖 | ~90 MB |
| DuckDB 原生库 | ~40 MB |
| 数据源驱动 | ~15 MB |
| 合计（打包前） | ~170 MB |
| PyInstaller + 压缩后 | ~220-260 MB |

超预算首要瘦身点：onnxruntime（已砍）、cytoscape+maplibre（前端已砍 maplibre，cytoscape 保留给 OntologyGraph）。

---

## 七、关键文件清单

### 后端新增
- `src/ontology/layers/engine/{base,duckdb_engine}.py` — QueryEngine Protocol + DuckDB 引擎（B2）
- `src/ontology/plugins/connectors/{base,postgres,mysql,csv_file,sqlite}.py` + `__init__.py`（ConnectorRegistry）— B4
- `src/ontology/layers/engine/base.py` — QueryEngine Protocol

### 后端修改
- `src/ontology/config/{settings,database,container}.py` — edition 字段 / SQLite DSN / 条件装配
- `src/ontology/main.py` — lifespan lite create_all+bootstrap / skip 后台任务
- `src/ontology/services/{object_query_service,datasource_service,action_service}.py` — lite 路由分支
- `src/ontology/services/textql/{sql_compiler,schema_provider}.py` — duckdb dialect + duckdb_table_refs
- `src/ontology/core/schemas/datasource.py` — LITE_CAPABILITY_MAP
- `src/ontology/layers/metadata/postgres_meta_store.py` — 跨方言说明注释（无代码改）

### 前端修改
- `src/web-ui/vite.config.ts` — `define __EDITION__`
- `src/web-ui/src/vite-env.d.ts` — 类型声明
- `src/web-ui/src/App.tsx` — GraphExplorePage 懒加载条件路由
- `src/web-ui/src/components/Layout.tsx` — explore 菜单 lite 隐藏
- `src/web-ui/Dockerfile` — `ARG EDITION`
- `src/web-ui/.env.example` — EDITION 说明

### 测试新增
- `tests/unit/layers/test_postgres_meta_store_sqlite_compat.py`（B1, 9 测试）
- `tests/unit/layers/test_duckdb_engine.py`（B2, 18+1 env-gated）
- `tests/unit/services/test_object_query_duckdb.py`（B3, 10 测试）
- `tests/unit/services/test_datasource_connectors.py`（B4, 24 测试）
- `tests/unit/services/test_action_service_lite.py`（B5, 3 测试）
- `tests/unit/test_lifespan_edition.py`（A4, 更新 B1）
- `tests/unit/config/test_container_edition.py`（A3, 更新 B2）

### 测试修改（加 lite skipif）
- `tests/conftest.py`（A5 collect_ignore_glob）
- `tests/unit/services/test_object_query_sql_inference.py`（B3 模块级 skipif）
- `tests/unit/services/test_action_sync_outbox.py`（B5 模块级 skipif）

---

## 八、工作流规则（用户明确要求）

1. **每个子任务启动前先做 mini-plan**（读相关代码 + 设计 + 验证标准）→ 跟用户确认 → 再编码。
2. **单会话只做 1 个子任务**（大子任务如 C2 可拆多次），完成后 commit + 报告 + 说明下一子任务。
3. **提交前自检**：ruff/mypy 干净 + lite/full 测试零新增回归 + 真实冒烟（DB 写入测试不能只断言 commit，须验证实际落库）。
4. **分支未 push，不合 develop**。

C1 启动时：先读 `gaia-lite.spec` 是否已存在 + 探查 `pyproject.toml` `[lite]` extras 依赖树 → 产出 C1 mini-plan → 确认 → 编码。
