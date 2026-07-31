# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Gaia lite backend (桌面单机版后端, C1).

产出 `dist/gaia-lite-backend/gaia-lite-backend`（--onedir 模式）。onedir 先行：
启动快、解压目录可检视、hiddenimports 漏项好排查。跨平台 mac-x64/win-x64 +
体积达标（<300MB）+ onefile vs onedir 取舍归 C3。

入口：scripts/gaia_lite_backend.py（在 import ontology 前锁 EDITION=lite）。

构建：`.venv/bin/pyinstaller gaia-lite.spec --noconfirm`
运行：`PORT=8765 ./dist/gaia-lite-backend/gaia-lite-backend`
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

# ── runtime_hooks：collect_* 展开成 (binaries, datas, hiddenimports) 三元组 ──
# duckdb：原生库（libduckdb ~40MB），collect_all 收 binaries + data + hiddenimports。
duckdb_binaries, duckdb_datas, duckdb_hidden = collect_all("duckdb")
# pydantic_ai：Agent(model_str) 按 provider prefix 动态 import 对应 models.<provider>
# 模块（defer_model_check=True 延迟到首次 run 才解析）。PyInstaller 静态分析扫不到，
# 全收子模块兜底（用户默认 openai: provider，但 lite 也支持 deepseek/glm 等云端 LLM）。
# 排除 durable_exec.temporal（依赖 temporalio，lite 不用，collect 时 import 会告警）。
pydantic_ai_hidden = [
    m for m in collect_submodules("pydantic_ai")
    if not m.startswith("pydantic_ai.durable_exec")
]
# pydantic_ai.ui.ag_ui：AG-UI 流式适配器（routes/ai.py 用 AGUIAdapter），动态 import 风险面。
ag_ui_hidden = collect_submodules("pydantic_ai.ui")

# SQLAlchemy dialects：sqlalchemy.dialects.sqlite / aiosqlite 是 lite 元数据层命脉；
# psycopg（lite 装了 psycopg[binary] 给 PG 数据源插件）的 libpq native 须 collect_all。
sa_hidden = collect_submodules("sqlalchemy.dialects")
psycopg_binaries, psycopg_datas, psycopg_hidden = collect_all("psycopg")

# fastmcp：MCP server（protocols/mcp_server.py），按 entry_points 动态装配工具，
# 子模块 PyInstaller 漏扫。
fastmcp_hidden = collect_submodules("fastmcp")

# genai_prices：pydantic-ai 2.0 的 token 计价依赖，__init__ 调
# importlib.metadata.version("genai_prices") 查自身版本——PyInstaller 默认不收
# dist-info 元数据，运行时 PackageNotFoundError。collect_all 收模块 + data。
genai_prices_binaries, genai_prices_datas, genai_prices_hidden = collect_all("genai_prices")

# importlib.metadata.version() 兜底：pydantic-ai 链上多个包在 __init__ 查自身版本
# （genai_prices / pydantic_ai_slim / pydantic_graph / logfire_api / ag_ui_protocol
# / openapi_pydantic / pydantic_core ...），缺 dist-info 即 PackageNotFoundError，
# 启动即炸。逐个补漏太被动，直接收 site-packages 下全部 dist-info——每个仅几 KB JSON，
# 全量代价可接受，一劳永逸覆盖所有 importlib.metadata.version() 查询。
import site

metadata_datas = []
for _sp_dir in site.getsitepackages() + [site.getusersitepackages()]:
    _sp = Path(_sp_dir)
    if not _sp.is_dir():
        continue
    for _di in _sp.glob("*.dist-info"):
        # copy_metadata 按 import name 找，这里直接收目录（含 METADATA/RECORD）。
        # 用 (源路径, 目标相对路径) 二元组让 PyInstaller 复制整个 dist-info 目录。
        _rel = _di.name  # 保留 dist-info 目录名，落到 bundle 根 site-packages 层
        metadata_datas.append((str(_di), _rel))

hiddenimports = [
    # ── 入口可达但 lazy import 的本体模块 ──
    # container.py 各 property / main.py lifespan 内 lazy import（A1/A3/B1 改造）：
    # PyInstaller 从 scripts/gaia_lite_backend.py → ontology.main → container 顶层
    # 能扫到 TYPE_CHECKING 下的 import（被 collect），但运行时真正执行的是 property
    # 内的 import，须显式列 lite 路径会触达的 Service/Layer。
    "ontology.config.database",
    "ontology.config.container",
    "ontology.config.settings",
    "ontology.core.models",  # __init__ 显式 import 全 49 表，触发 Base.metadata 注册
    "ontology.core.models.datasource",
    "ontology.core.models.ontology",
    "ontology.core.models.permission",
    "ontology.core.models.pipeline",
    "ontology.layers.metadata.postgres_meta_store",
    "ontology.layers.engine.duckdb_engine",
    "ontology.layers.engine.base",
    # lite 数据源插件四件套（B4）——ConnectorRegistry 顶层 import 已能扫到，保险显式列。
    "ontology.plugins.connectors",
    "ontology.plugins.connectors.base",
    "ontology.plugins.connectors.postgres",
    "ontology.plugins.connectors.mysql",
    "ontology.plugins.connectors.csv_file",
    "ontology.plugins.connectors.sqlite",
    # lite 端到端会触达的 Service（container property lazy import）。
    "ontology.services.ontology_service",
    "ontology.services.datasource_service",
    "ontology.services.object_query_service",
    "ontology.services.action_service",
    "ontology.services.authorization_service",
    "ontology.services.permission_bootstrap",
    "ontology.services.ai_agent",
    # TextQL：sqlglot dialect=duckdb，编译器 + schema provider（B3）。
    "ontology.services.textql.sql_compiler",
    "ontology.services.textql.schema_provider",
    # AG-UI 适配器（routes/ai.py 用 pydantic_ai.ui.ag_ui.AGUIAdapter，已收入 ag_ui_hidden）。
    "ontology.tools.toolsets",
    "ontology.tools.toolsets.metadata",
    "ontology.tools.toolsets.write",
    "ontology.tools.toolsets.action",
    "ontology.tools.toolsets.object_query",
    "ontology.tools.toolsets.link_traversal",
    "ontology.tools.toolsets.reasoning",
    "ontology.tools.toolsets.canvas_control",
    "ontology.tools.toolsets.approval",
    "ontology.tools.toolsets.pipeline_builder",
    "ontology.tools.toolsets.impact_builder",
    "ontology.tools.executor",
    "ontology.tools.state",
    "ontology.tools.pipeline_state",
    # ── 第三方动态加载 ──
    *duckdb_hidden,
    *pydantic_ai_hidden,
    *ag_ui_hidden,
    *sa_hidden,
    *psycopg_hidden,
    *fastmcp_hidden,
    *genai_prices_hidden,
    # uvicorn[standard] 的可选 native：uvloop/httptools，漏则自动退 asyncio loop，
    # 但显式列避免运行时降级告警。pydantic-ai 内部用 anyio。
    "uvloop",
    "httptools",
    "anyio",
    "aiosqlite",
    "sqlalchemy.dialects.sqlite",
    # pydantic-ai 的 openai provider 底层走 openai 库；openai 可能懒加载子模块。
    *collect_submodules("openai"),
]

# ── excludes：省体积 + 防 .venv 三 extras 致重依赖被打进 ──
# 本地 .venv 装了 dev+full+lite 三 extras（asyncpg/pyiceberg/trino 等都在），
# PyInstaller 会扫到这些包并尝试打进。lite 运行时绝不触达，explicit exclude 防漏。
excludes = [
    # lite 砍掉的云版重依赖（[full] extras）。
    "asyncpg",
    "pyiceberg",
    "trino",
    "neo4j",
    "onnxruntime",
    "tokenizers",
    "aiobotocore",
    "aiomysql",
    "botocore",  # aiobotocore 的同步底层，lite 无 S3
    "temporalio",  # pydantic_ai.durable_exec 依赖，lite 不用
    # 标准库省体积（运行时不需要）。
    # 注意：不能 exclude unittest——logfire_api（pydantic-ai 依赖）import unittest。
    "tkinter",
    "pydoc",
    # 测试/基准依赖（[dev] extras，不应进发行包）。
    "pytest",
    "pytest_asyncio",
    "pytest_cov",
    "testcontainers",
    "locust",
    "faker",
    "mypy",
    "ruff",
]

binaries = [*duckdb_binaries, *psycopg_binaries, *genai_prices_binaries]
datas = [*duckdb_datas, *psycopg_datas, *genai_prices_datas, *metadata_datas]

# src/ontology 是应用源码（pyproject 用 hatchling，包在 src/ontology 下）。
# PyInstaller 通过入口脚本的 import 链自动收集 ontology.*，但需确保 src 在 path。
# spec 同级目录是 engines/gaia/，src 在 ./src。
ontology_src = Path("src/ontology")
# 不显式 datas ontology 源码——PyInstaller 的 modulefinder 会跟随 import 收 .pyc。
# 仅收非 .py 资源（如 .sql/.yaml 模板，若有）。
extra_datas: list[tuple[str, str]] = []
for resource in ontology_src.rglob("*"):
    if resource.suffix in {".sql", ".yaml", ".yml", ".json", ".j2", ".jinja2"}:
        if "__pycache__" in resource.parts:
            continue
        rel = resource.relative_to(ontology_src)
        extra_datas.append((str(resource), str(rel.parent)))
datas.extend(extra_datas)


a = Analysis(
    ["scripts/gaia_lite_backend.py"],
    pathex=["src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="gaia-lite-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # C1 不压缩（C3 再 UPX），便于排查
    console=True,  # 桌面后端 sidecar，保留 stdout/stderr 给 Tauri 捕获日志
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="gaia-lite-backend",
)
