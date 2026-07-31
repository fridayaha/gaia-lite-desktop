# Gaia 系统对外演示清单

> **演示定位**：开源 Palantir Foundry 风格的分层数据架构 + AI 原生本体平台
> **目标**：在 30–45 分钟内讲清「我们做了什么、凭什么能做、和别人差在哪」
> **原则**：每个演示项都标注真实可跑的入口（脚本 / 路由 / 页面）与数据背书，不演示未跑通的环节
> **数据来源**：以 [`docs/architecture/implementation-status.md`](architecture/implementation-status.md) 为唯一真相源

---

## 一句话定位（开场必讲）

> **Gaia 把企业异构数据 + 业务语义 + AI 决策，统一收敛到一个「本体驱动」的平台上——不靠堆人力，靠架构分层让大模型和企业业务对齐。**

对标对象：Palantir Foundry 的 Ontology + AIP 工具体系（见 `docs/reference.md`）。Gaia 用 8 层开源组件复刻其核心能力，AI 能力对齐其 ObjectSet / AIP Agent 范式。

---

## 核心优势（演示全程反复强调这 4 条）

| # | 优势 | 我们做到了什么 | 对手/常规做法的痛点 |
|---|------|---------------|---------------------|
| **A1** | **分层解耦，每层可替换** | 8 个 Layer（Metadata/Catalog/Dataset/Index/Pipeline/Engine/**Graph**/GeoTime）各司其职，接口契约 ICD 化。任一组件可替换升级不影响上层 | 传统数仓/BI「数据-语义-查询」糅在一起，换一个引擎全链路返工 |
| **A2** | **AI 与业务语义对齐，不是套壳** | 本体是 LLM 的「语义大脑 + 安全手脚」：22 个本体工具（MCP/AG-UI/REST 三入口）让 LLM 只能在本体约束内决策，杜绝幻觉越权 | 普通 LangChain/RAG 直接让 LLM 写 SQL，列名/JOIN 一错就崩，无业务护栏 |
| **A3** | **多源异构一把梭，开源不魔改** | 25 种连接器（关系库/湖仓/文件/消息/NoSQL/云数仓）+ 国产库驱动避冲突，**只基于开源原生扩展能力**，不做侵入式改造 | 自研 Connector 框架 / 深度魔改开源 → 升级即地狱 |
| **A4** | **真跑通，不是 PPT** | 真实组件全 live 验证：CDC / Iceberg→Doris 同步 / 图探索 / AG-UI Agent 自动编排 全部端到端跑通；1268 个后端测试 + 169 个前端测试 | 多数「数据平台」demo 跑的是 mock 数据 + 假流式 |

---

## 演示模块清单（建议按此顺序）

> 每个模块包含：**核心卖点 → 演示动作 → 真实背书**。
> 演示前请先 `bash scripts/dev.sh` 启动前后端 + `docker compose up -d` 起基础设施。

### 模块 0：基础设施全景（2 分钟，建立信任）

**卖点**：不是玩具，是一套真实跑起来的开源数据栈。

**演示动作**：
```bash
docker compose ps              # 11 个服务全 Green
curl http://127.0.0.1:8000/health      # {"status":"ok"}
curl http://127.0.0.1:5173/            # 前端 HTML
curl http://127.0.0.1:8000/metrics     # Prometheus 指标
```

**真实背书**：
- PostgreSQL 16（PostGIS 3.6.1 + TimescaleDB 2.24.0 一体镜像）/ Gravitino 1.3.0（含内置 Iceberg REST Catalog）/ Iceberg 1.11.0 / Doris 4.0.5 / Trino 478 / SeaTunnel 2.3.13 / RustFS / Kafka 4.3.0 / **Neo4j 5-community**
- **关键澄清点（显差异化）**：Iceberg REST Catalog 由 Gravitino 容器内置 9001 提供，**没有独立 tabulario/iceberg-rest 服务、没有 8181 端口**——这是社区常见误配，我们做对了

---

### 模块 1：本体建模 —— 把业务语言变成可执行模型（8 分钟，核心）

> 对应 ADR-009/010 + BuildWith 脚手架。这是 Gaia 的「语义大脑」。

**卖点**：用户用业务语言描述，系统生成结构化本体；不是填表配置，是对话式 + AI 辅助。

**演示动作（前端 `/` OntologyWorkspace）**：
1. 新建 Ontology（如 `Airline`），PascalCase apiName 自动校验
2. **BuildWith 从数据集脚手架生成 ObjectType**：
   - 选一个已接入的数据集 → 调 `POST /ai/scaffold`（**SSE 结构化流式**，pydantic-ai Tool Output）
   - AI 自动推导：对象类型名 + 属性 + 主键 + 标题属性；确定性补 data_type/nullable
   - 用户在 3 步向导里微调（数据集 → 属性与键 → 审核）
3. 看图谱画布：Cytoscape 渲染 ObjectType + Property + LinkType 关系图
   - 右键菜单 / 鸟瞰图 / 周边聚焦 / SVG 导出（图谱第二梯队能力）
4. 属性 apiName 由后端 `core/naming.derive_api_name` 纯规则推导（camelCase），ObjectType/Action 由 LLM 推导 + 用户可改 + 后端只校验

**真实背书**：
- benchmark 验证：`Airline` 本体 + 9 ObjectType + 8 LinkType + 2 ActionType 全部创建成功，apiName 推导正确（`tests/benchmark/dvp/data/ontology/`）
- `/ai/generate` 真实 LLM 验证：「航班」→`Flight`（PascalCase）、「延误航班」→`delayedFlight`（camelCase）
- 三字段模型对标 Palantir（rid / apiName / displayName），见 implementation-status §十

**话术**：「用户永远不需要理解 Gravitino、Iceberg、Doris，他们只需要知道『我的客户数据在这里，订单数据在那里』——技术复杂度全封装在本体之下。」

---

### 模块 2：数据接入与多源融合（8 分钟，差异化王牌）

> 对应 ADR-014。这是 A3 优势的主战场。

**卖点**：25 种连接器，6 大品类一刀切 VIRTUAL 边界，CDC 真实跑通，国产库驱动避冲突——**全靠开源原生扩展能力，零侵入魔改**。

**演示动作（前端 `/data/sources` DataSourcesPage）**：
1. 展示连接器目录：6 大品类分组 + 搜索 + 能力过滤 + 成熟度徽章 + 避坑提示面板
   - 关系库（MySQL/PG/OpenGauss/GaussDB/TiDB/OceanBase/达梦/金仓/**StarRocks**）
   - 湖仓格式（Hive/Delta/Hudi/Paimon）
   - 文件存储（S3/MinIO/OSS/HDFS）
   - 消息队列（Kafka：VIRTUAL 联邦 + 落地双通道）
   - NoSQL / 云数仓
2. **live 验证过的链路当场点一遍**：
   - S3File → Iceberg：`create_file_sync_pipeline`（RustFS + Parquet + CSV 端到端）
   - Kafka → Iceberg：`create_kafka_ingestion_pipeline`（实时流式 + earliest 历史消费）
   - MySQL-CDC → Iceberg：`POST /datasources/{ds}/cdc-sync`（全量 + 增量 upsert）
3. 接入后自动登记虚拟表（VIRTUAL，不落地，Trino 联邦查询）

**真实背书**（live 验证全绿，见 implementation-status §十一）：
- CDC spike：全量 + 增量 CDC upsert + SeaTunnel #10747 规避 + worker 稳定 + schema 演进，端到端跑通（`docs/engineer/cdc-spike-report.md`）
- 国产库驱动双侧加载 live 验证（opengaussjdbc / kingbase8 / oceanbase / 达梦）
- StarRocks 走 Gravitino `jdbc-starrocks` 原生 provider，catalog 注册 + dialect dry-run 验证（`docs/engineer/starrocks-seatunnel-dryrun.md`）

**话术（讲透 A3）**：「国产库 JDBC 驱动同名类冲突是个深坑——openGauss 旧驱动内含完整 `org.postgresql.Driver.class`，和官方驱动打架，SeaTunnel 直接报 `Protocol error`。我们的解法是用独立类名驱动包（`com.huawei.opengauss.jdbc.Driver`），**不动开源一行代码**就解决了冲突。这是『只基于开源原生扩展能力』原则的典型落地。」

---

### 模块 3：托管对象查询 + 读写分离架构（6 分钟，架构红线）

> 对应 ADR-001 + 场景 2/3。

**卖点**：Doris 在线读主源（全量属性 + 倒排/向量索引）+ Iceberg 历史快照 + Trino 联邦虚拟表，**读写分离、降级完备**。

**演示动作**：
1. 查询托管对象（MANAGED）：`POST /objects/{ont}/load` → Doris 全量直出
2. 查询虚拟对象（VIRTUAL）：走 Trino 联邦跨 catalog JOIN（无 Doris 参与）
3. **故意停掉 Doris** → 演示降级：Trino 直接扫描 Iceberg（带分区裁剪），查询不中断
4. 时间旅行：`TimeTravelService` → Trino `FOR VERSION AS OF {snapshot_id}`

**真实背书**：
- IndexSyncService 编排接通：`scripts/verify_index_live.py` 9 项全过 + Doris 真实建表/upsert/query 全链路
- Iceberg→Doris 同步去 SeaTunnel 化（2026-07 T1.10）：Doris 写入统一收口到 ObjectIndexFunnel（外部接入，从 Iceberg scan_latest 读 → DorisIndexStore.upsert）+ OutboxExecutor（Action 写入，outbox INDEX effect ≤1s）。旧的 SeaTunnel backfill/stream 双模板已删除
- Prometheus 指标：`object_query_index_hit_total` / `object_query_fallback_total{reason=not_built|doris_down}`
- Doris 表名带本体前缀 `idx_{ont}__{type}`——**修复过跨本体数据互盖/误删的严重隐患**（implementation-status §七-bis）

**话术（讲架构红线）**：「Doris 只做在线读，绝不作写入入口；Iceberg 是唯一写入入口。这样写入吞吐（Iceberg ACID）和读延迟（Doris 索引）各自优化，互不拖累。这是和『一个库又读又写』的根本差异。」

---

### 模块 4：Action 闭环 —— 让 AI 决策真正落地（8 分钟，杀手锏）

> 对应 ADR-011 + adr-action-mutation-mapping + action-loop-design。

**卖点**：不是「AI 给建议」就完了，是 **AI 决策 → 原子写入 → 异步同步 → 读即所见** 的完整闭环。

**演示动作（前端 `/actions` ActionsOverview）**：
1. 展示 ActionTypeEditor（对标 Palantir 三机制）：
   - 属性映射自动派生参数（机制 A）
   - 值来源自适应 ValueSource（PARAMETER/OBJECT_PROPERTY/STATIC/SYSTEM_CONTEXT/EXPRESSION，机制 B）
   - 规则 + 副作用统一入口（机制 C：CreateObject/ModifyObject/UpsertObject/DeleteObject/CreateLink/DeleteLink）
2. 执行单个 Action：`POST /actions/execute`
   - PG `object_state` + `execution_log` + `outbox` **原子提交**
   - 立即返回 `applied`（read-your-writes，点查/filter 立即可见）
3. **P2 批量 Action**：`POST /actions/execute-batch`
   - 分片调度（shard_size 默认 100 / 最大 1000 / 上限 10000 项）
   - 逐项原子事务（单项失败不中断整批 → partial）+ fail_fast 选项 + 派生幂等键
4. 后台 OutboxExecutor 消费 → INDEX(→Doris≤1s) / Webhook / WriteBackManager 反馈环
5. SyncFlushScheduler 消费 ARCHIVE outbox → IcebergStore.merge（微批 ≤5min，主数据归档）
6. 三层权限演示（执行 / 行级 / 参数级，ActionAuthorizer）

**真实背书**：
- outbox 驱动同步冒烟通过（commit 73b1c7f）：INDEX 1s 内 Doris upsert + ARCHIVE 微批 Iceberg MERGE + 后台任务真实运行
- OCC 乐观并发控制 + expected_version 版本管控 + 版本快照回滚
- VIRTUAL 目标写入 guard：后端 `ValidationError` 拒绝 + 前端 ActionsOverview 卡片置灰禁用
- 2026-07-08 去 SeaTunnel 化：object_state 同步改 outbox 驱动（详见 `docs/design/action-sync-outbox-design.md`）

**话术（讲 A2 的「安全手脚」）**：「LLM 不是直接写库，它只能调 invoke_action 工具，工具走 ActionType 声明式规则 + 三层权限校验 + CDL 前后快照审计。AI 想越权？门都没有。这就是『本体是 LLM 的安全手脚』。」

---

### 模块 5：图关联推理与时空多维分析（10 分钟，2026 旗舰特性）

> 对应 ADR-015。这是 2026-07 新增的重大特性，265 个提交。**演示重头戏**。

**卖点**：对标 Palantir Vertex / Foundry ObjectSet。ObjectSet IR 对齐 Palantir 87%（13/15 type）。AG-UI ReAct Agent 驱动画布——**不是 LLM 编计划硬执行，是每步基于画布状态决策**。

**演示动作（前端 `/explore` GraphExplorePage）**：

**5.1 图探索画布（Phase 2a-2h）**
- Cytoscape 画布：增量 diff + fcose/dagre/circle/grid 多布局切换
- 右键 cxtmenu + 鸟瞰图 + 周边聚焦
- 侧栏三 tab：选中 / 图层（着色+节点大小+localStorage 持久化）/ 分布（HistogramPanel 属性分布筛选）
- 全局时间轴 TimeScrubber：双滑块 + 预设 1h/24h/48h/7d + 播放 + 仅活跃实体

**5.2 空间时空分析（Phase 2b）**
- MapLibre GL 地图 + marker + 框选过滤
- TrajectoryPlayer 轨迹回放
- 视图切换：图谱 / 地图 / 分屏（headless 无 WebGL 降级列表已验证）

**5.3 路径推理（Phase 2d）**
- PathFinder：源/目标下拉 + max_depth + 路径序列展示
- 底层：Neo4j `allShortestPaths` Cypher（max_depth + limit 防爆炸）
- 工具 `find_paths`（第 22 个工具）

**5.4 多步 Search Around（Phase 2f）**
- useSearchAroundConfig：链式嵌套 IR 构建 + 预览防星爆
- SearchAroundConfigPanel

**5.5 ⭐ AG-UI ReAct Agent 自动编排（Phase 3a-3c，ADR-015 灵魂）**
- 进入 `/explore`，中央对话框 + 4 场景卡片
- 输入：「分析供应链中断风险」
- **看 Agent ReAct 多步执行**：
  - 调 `query_with_dataframe` 加载对象 → 读 CanvasSnapshot 看 object_count
  - 0 对象？**自然终止，不编结论**（这是 ADR-015 转向的核心修复）
  - 有对象？继续 `traverse_link` / `switch_view` / `color_by`
  - 工具返回 ToolReturn 双职分离（return_value 给 Agent + StateSnapshotEvent 驱动画布）
- URL 预填充：`/explore/:ont?objects=&view=&question=`

**真实背书**：
- 后端 ~1094 测试全过（含图/时空/ObjectSet + P2 field 白名单）
- 前端 22 文件 169 测试全过（+21 图探索测试）
- 真实端到端验证：图探索 / Search Around / 证据链 / 地图降级 / 三 tab / 路径推理 / Action 闭环 / 多步 Search Around / **AG-UI ReAct Agent 自动编排** / 场景模板 / URL 预填充 全部验证通过
- 8 Layer 新增 Graph（Neo4j）+ GeoTime（PostGIS + TimescaleDB）
- ObjectSet IR 13/15 type 对齐 Palantir

**话术（讲 ADR-015 为什么是架构胜利）**：
> 「我们一开始让 LLM 在空画布上一口气编 5 步计划硬执行——结果 0 个对象它还空转到底、最后编一句『重点关注退市车型』的假结论。这是反 Agent 范式的。我们转向 ReAct：每步基于画布真实状态决策，0 对象就停。同时废弃了 `should_route_to_object_set` 关键词路由——硬编码关键词永远枚举不全，违反我们自己定的红线 8（禁手写 if-elif 链）。**这个转向本身就是一个工程教训的体现**：我们发现错了就改，不硬撑。」

---

### 模块 6：本体驱动自然语言查询 TextQL（5 分钟，讲清边界）

> 对应 ADR-012。

**卖点**：五步流水线（意图解析 → 双引擎召回 → Schema 注入 → Tool Use → SqlGlot 编译）+ 三大护栏。**关键：Ontology API 层不吃自然语言，两层正交**——这是对标 Palantir 的范式正确性。

**演示动作**：
1. `/ai/agent` 输入自然语言问题
2. 看 Agent 调 `query_with_sql` 工具（LLM Tool Use，不是直出 SQL）
3. SqlGlot 编译：CTE 支持 + `SELECT *` 多表消歧 + 方言感知物理名
4. 三大护栏：白名单校验 / 标识符注入防御 / 字面量类型保留

**真实背书**：
- 端到端验证（Airline + Marketing 本体）：单表过滤 / 聚合 / CTE / 4 表 JOIN（销售外呼统计）真实跑通；跨 MANAGED+VIRTUAL 联邦 JOIN 路由 Trino
- 引擎 B 向量召回兜底：ONNX CPU 推理 MiniLM-L12-v2（384 维，~15ms/句），Airline 本体 78 元素索引，口语化查询精准命中
- Doris ANN 环境调优已解决（BE 内存 1g→3g + `ALTER ADD INDEX` 低内存路径）

**⚠️ 演示边界（诚实披露，建立信任）**：
- benchmark agent 维度（DVP 数据集，多跳 JOIN）：单实体点查 + 单表过滤排序可通过，**多表 JOIN/聚合的 NL→SQL 准确率仍低**（见 `tests/benchmark/dvp/reports/latest.md` A3-A7）。这是行业共性瓶颈，我们用「本体约束 + 工具化」而非「让 LLM 直出 SQL」来缓解——但不会夸大说已解决。
- **话术**：「我们不假装 NL→SQL 100% 准确。我们的做法是让 LLM 调本体工具（query_with_sql），而不是裸写 SQL——本体 Schema 注入 + 白名单护栏把错误控制在『列不可用就报错』，而不是静默返回错答案。这是工程诚实。」

---

### 模块 7：本体工具层 + HITL 审批（5 分钟，企业级落地的关键）

> 对应 ADR-009/010。

**卖点**：22 个本体工具，三入口（MCP / AG-UI / REST）统一暴露；写/执行类工具走分级 HITL 审批——**企业级 AI 落地不敢全自动，必须有人类审批闸门**。

**演示动作**：
1. MCP 入口：`protocols/mcp_server.py` 暴露 19 工具（13 只读 + 6 写/执行）
   - 只读工具直接 `add_tool(tool.function)`
   - 写/执行工具专用 `@mcp.tool` + `MCPApprovalHandler(Context.elicit)` 弹窗确认
2. AG-UI 入口：`/ai/agent`，`AGUIApprovalHandler` raise `NeedsApprovalError` → 前端 `ApprovalDialog` 弹窗 → `POST /ai/action/confirm` 恢复
3. REST 入口：脚本/外部系统直调
4. 分级确认演示：
   - 低危 → 弹窗是/否
   - 中危 → 列出影响
   - 高危 → 输入名称确认（CLAUDE.md 要求，前端待补输入框，后端校验就绪）
5. `ActionType.risk_level` 字段驱动 `invoke_action` 是否走审批

**真实背书**：
- 22 工具全可用：元数据 4 + 对象查询 1 + 写 4 + 动作 2 + 关系族 3 + 路径 1 + 推理线 1 + 审批/画布辅助 6
- 双协议 HITL 闭环（AG-UI NEED_APPROVAL→confirm / MCP elicit）端到端测试 6 项
- 前端 `thread.tsx` NEED_APPROVAL 渲染 + `ApprovalDialog` + `confirmAiAction`

**话术**：「企业敢不敢用 AI，关键看敢不敢让它写库。我们用 HITL 审批闸门：低危弹窗、中危列影响、高危输名称。MCP 走 elicit、AG-UI 走 interrupt/resume——**同一套工具，三入口复用，审批语义一致**。」

---

### 模块 8：工程基线（3 分钟，收尾建立专业信任）

**卖点**：不是 demo-ware，是工程级项目。

**演示动作**：
```bash
.venv/bin/python -m pytest tests/unit/ -q          # 1268 测试
cd src/web-ui && pnpm run build                    # 前端 build 通过
uv run ruff check src/ && uv run mypy --strict src/  # 静态检查零容忍
.venv/bin/alembic check                            # schema 无漂移
uv audit                                           # 依赖安全扫描
```

**真实背书**：
- 后端：101 个源文件 / 27,756 行；130 个测试文件 / 1268 个测试函数
- 前端：79 个组件 / 169 测试
- 8 Layer / 22 Service / 22 工具 / 11 ADR 实体
- CI 流水线：lint → {test, audit} 三 job
- Alembic 业务表 schema 单一真相源（不手写 SQL）
- pre-commit hooks（ruff + mypy + uv-lock-check + uv-audit）
- Conventional Commits + SemVer

**话术**：「TDD 先行、类型注解全覆盖、mypy --strict 零容忍、Alembic 管 schema、CI 跑安全审计。这不是『能跑的 demo』，是『能交付的工程』。」

---

## 演示节奏建议（45 分钟版）

| 时段 | 模块 | 时长 | 目的 |
|------|------|------|------|
| 0-2 | 模块 0 基础设施全景 | 2' | 建立信任：真实跑起来的栈 |
| 2-10 | 模块 1 本体建模 | 8' | 核心概念：业务语言→可执行模型 |
| 10-18 | 模块 2 多源融合 | 8' | 差异化王牌 A3 |
| 18-24 | 模块 3 读写分离查询 | 6' | 架构红线 A1 |
| 24-32 | 模块 4 Action 闭环 | 8' | 杀手锏：AI 决策落地 |
| 32-42 | 模块 5 图关联推理 | 10' | 旗舰特性 ADR-015 |
| 42-45 | 模块 8 工程基线 | 3' | 收尾：专业信任 |

> 模块 6（TextQL）和模块 7（HITL）作为**备选深挖**，看观众兴趣和时间灵活插入。模块 6 务必带「诚实边界」话术。

---

## 观众画像 × 重点取舍

| 观众类型 | 必讲 | 可略 |
|---------|------|------|
| **技术决策者（CTO/架构师）** | 模块 0/3/5/8 + ADR-015 转向叙事 | 模块 1 细节、模块 6 |
| **业务/产品负责人** | 模块 1/2/4/5 + A2/A4 优势 | 模块 0 端口、模块 8 |
| **数据工程师** | 模块 2/3 + ADR-014 连接器细节 + 国产库驱动避冲突 | 模块 1 对话式建模 |
| **AI 应用负责人** | 模块 4/5/6/7 + ADR-009/010/015 | 模块 2 连接器目录 |
| **投资人/高层** | 一句话定位 + A1-A4 四优势 + 模块 5 旗舰 | 所有技术细节 |

---

## 演示前 Checklist（执行人必过）

- [ ] `docker compose up -d` 11 服务全 Green（含 `--profile graph` 起 Neo4j）
- [ ] `bash scripts/dev.sh` 前后端启动，`/health` + `/` 200
- [ ] `.venv/bin/alembic upgrade head` 已执行（migrate init 容器或手动）
- [ ] `.env` 配好 `AI_MODEL` + 对应 provider key（TextQL / scaffold / AG-UI Agent 依赖）
- [ ] 预置演示本体：`python scripts/seed_flight_dataset.py` + `python scripts/setup_explore_demo.py`（图探索 demo 数据）
- [ ] 预跑一遍 `scripts/verify_e2e_full.py` 确认全链路绿
- [ ] Doris BE 内存已调到 3g（ANN/大表同步需要，否则 OOM）
- [ ] PG `wal_level=logical` + REPLICA IDENTITY FULL 已设（Action CDC 需要）
- [ ] 浏览器开好两个 tab：`/`（建模）+ `/explore`（图探索）
- [ ] 准备好 fallback：万一 live 挂了，用 `tests/benchmark/*/reports/*.md` 截图兜底

---

## 禁忌（演示中不要做的事）

1. **不要演示未 live 验证的链路**：
   - ❌ OpenGauss/TiDB CDC 真实源库（代码就绪，未 live）
   - ❌ ES / Hive Metastore live（代码就绪，待外部容器）
   - ✅ 对话式本体建模多轮（AG-UI Thread 多轮 + 写工具 HITL 批量审批 + Capability 方法论，commit 584af2c）—— 可演示
   - ❌ Interface CRUD REST 路由（metadata 层已实现，无端点暴露）
2. **不要夸大 NL→SQL 准确率**：DVP benchmark 多跳 JOIN 仍低，诚实说边界
3. **不要演示 `_filter_dict_to_sql` 技术债**：参数化绑定 + 白名单是待办，别现场被问到细节
4. **不要现场改代码**：项目规范要求 commit 前跑 `make test` + `pnpm build` + 本地冒烟，现场改容易翻车
5. **不要提未补的 ADR-002~006 / ICD / CHANGELOG**：被问到就说「索引已建，实体文件在补，不影响功能」

---

## 一页纸总结（可打印分发）

```
Gaia — 开源 Palantir Foundry 风格的 AI 原生本体平台

4 大优势：
  A1 分层解耦 8 Layer 可替换（ICD 契约化）
  A2 AI 与业务语义对齐（本体=LLM 语义大脑+安全手脚，22 工具三入口）
  A3 多源异构 25 连接器（开源原生扩展，零魔改）
  A4 真跑通（1268 后端测试 + live 端到端验证）

旗舰特性（2026-07）：
  图关联推理与时空多维分析（ADR-015）
  - 8 Layer 新增 Graph(Neo4j) + GeoTime(PostGIS+TimescaleDB)
  - ObjectSet IR 对齐 Palantir 87%（13/15 type）
  - AG-UI ReAct Agent 驱动画布（Controlled Gen UI + Shared State）

技术栈：Python 3.12 / FastAPI / React 19 / Tailwind v4 / Cytoscape / React Aria
数据栈：PG16(PostGIS+Timescale) / Gravitino 1.3 / Iceberg 1.11 / Doris 4.0 / Trino 478 / SeaTunnel 2.3.13 / Neo4j 5

工程基线：TDD / mypy --strict / Alembic / CI lint→{test,audit} / pre-commit
```
