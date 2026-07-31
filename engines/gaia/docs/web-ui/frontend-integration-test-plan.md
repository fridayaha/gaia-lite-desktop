# Gaia 前端集成测试策略与用例

> **版本**：v1.0 | **日期**：2026-06-19
> **用途**：交付测试人员执行的完整前端集成测试方案，覆盖端到端数据流可用性 + 数据库正确性 + HCI 规范
> **测试范围**：`src/web-ui`（React 19 + Vite）↔ `src/ontology`（FastAPI）↔ 9 个基础设施容器（PG/Gravitino/Iceberg/Doris/Trino/SeaTunnel/Kafka/RustFS）
> **依据**：[architecture_plan.md](../architecture/architecture_plan.md) · [data-flow-diagrams.md](../design/data-flow-diagrams.md) · [dataset-ontology-binding.md](../design/dataset-ontology-binding.md) · [action-loop-design.md](../architecture/action-loop-design.md) · [index-acceleration-design.md](../architecture/index-acceleration-design.md) · [frontend-hci-review.md](../design/frontend-hci-review.md) · [ontology-manager.md](./ontology-manager.md)

---

## 目录

1. [测试目标与原则](#一测试目标与原则)
2. [测试环境前置准备](#二测试环境前置准备)
3. [测试分层与用例总览](#三测试分层与用例总览)
4. [测试套件 TS-1：数据源接入全链路（流 A）](#四测试套件-ts-1数据源接入全链路流-a)
5. [测试套件 TS-2：托管对象建模（流 A 步骤 5 + 索引加速）](#五测试套件-ts-2托管对象建模流-a-步骤-5--索引加速)
6. [测试套件 TS-3：虚拟对象全链路（流 C + Virtual Table 登记）](#六测试套件-ts-3虚拟对象全链路流-c--virtual-table-登记)
7. [测试套件 TS-4：物理对象查询（流 B + Doris 索引 + 降级）](#七测试套件-ts-4物理对象查询流-b--doris-索引--降级)
8. [测试套件 TS-5：Action 写入闭环（流 E）](#八测试套件-ts-5action-写入闭环流-e)
9. [测试套件 TS-6：时间旅行查询（流 D）](#九测试套件-ts-6时间旅行查询流-d)
10. [测试套件 TS-7：AI 辅助建模（流 F）](#十测试套件-ts-7ai-辅助建模流-f)
11. [测试套件 TS-8：运行洞察与可观测性](#十一测试套件-ts-8运行洞察与可观测性)
12. [测试套件 TS-9：HCI 与无障碍验收](#十二测试套件-ts-9hci-与无障碍验收)
13. [测试套件 TS-10：故障注入与降级验证](#十三测试套件-ts-10故障注入与降级验证)
14. [数据库正确性校验清单](#十四数据库正确性校验清单)
15. [缺陷分级与回归](#十五缺陷分级与回归)
16. [附录 A：自动化执行脚本](#十六附录-a自动化执行脚本)
17. [附录 B：速查表](#十七附录-b速查表)

---

## 一、测试目标与原则

### 1.1 测试目标

对照架构文档验收三大维度：

| 维度 | 验收问题 | 对应文档章节 |
|------|---------|------------|
| **端到端数据流可用性** | 6 条主干流（A 接入 / B 物理查询 / C 虚拟查询 / D 时间旅行 / E Action 闭环 / F AI 建模）是否在真实容器栈上端到端跑通 | data-flow-diagrams.md §1 |
| **数据库正确性** | 每条数据流在 PostgreSQL（本体元数据 + object_state + outbox + execution_log）、Doris（索引表）、Iceberg（主数据 + 快照）落地的数据是否符合架构红线 | architecture_plan.md §十 各层红线 |
| **前端与架构一致性** | 4 个 Rail（业务定义 / 数据对接 / 能力赋予 / 运行洞察）是否完整映射架构能力；HCI 校验清单是否全部满足 | frontend-hci-review.md §十二 |

### 1.2 测试原则（来自工程原则文档）

1. **真实环境优先**：所有用例在真实 Docker Compose 栈上执行，禁止 Mock 后端。参考 `scripts/verify_e2e_full.py` 的"No mocks"原则。
2. **异常路径必覆盖**：每个流验证正常路径 + 至少 1 个降级/失败路径（架构 §13.2 异常覆盖率要求）。
3. **数据库落点必校验**：前端操作完成后，用 SQL 直连 PG/Doris 校验落地数据，不仅看前端 UI 渲染。
4. **数据隔离**：每个用例用唯一前缀（如 `test_<timestamp>_`）创建资源，避免互相污染；用例结束清理。
5. **可重复执行**：用例幂等，重复跑不报错（CREATE IF NOT EXISTS / 先 DELETE 再创建）。
6. **分层断言**：前端可见状态（UI 文本）+ 网络层（HTTP 状态码）+ 数据库层（SQL 查询）三层都要断言。

### 1.3 关键架构红线（测试必验）

| # | 红线 | 验证点 |
|---|------|--------|
| R1 | Iceberg 是唯一写入入口，Doris 不承接写请求 | TS-5：Action 写入只落 PG object_state，Doris 索引由同步管道异步更新 |
| R2 | Doris 仅存主键 + 索引列 + 热点属性，不存全量明细 | TS-2：`SHOW COLUMNS FROM idx_<type>` 仅含 indexed 字段，无 description/大字段 |
| R3 | VIRTUAL 对象只读，不支持 Action 写入 | TS-3/TS-5：VIRTUAL 对象执行 Action 返回 422 |
| R4 | PG 存业务元数据，Gravitino 存物理元数据，单向引用 | TS-2/TS-3：`datasets` 表无 object_type_id 反向外键 |
| R5 | storage_type 取值仅 MANAGED / VIRTUAL（无 PHYSICAL） | TS-2：`SELECT DISTINCT storage_type FROM object_types` |
| R6 | Action 原子提交：object_state + execution_log + outbox 同事务 | TS-5：三者要么全有要么全无 |
| R7 | Read-your-writes：Action 返回 applied 后立即可查 | TS-5：执行后立即查询命中 object_state |

---

## 二、测试环境前置准备

### 2.1 环境检查清单（测试前必做）

```bash
# 1. 启动全栈（9 个基础设施服务 + API）
docker compose up -d
docker compose ps   # 确认 postgres/gravitino/rustfs/doris-fe/doris-be/trino/kafka/seatunnel-master/api 全部 healthy

# 2. 后端健康检查
curl -s http://localhost:8000/health        # 期望 {"status":"ok"}

# 3. 前端 dev server
cd src/web-ui && npm install && npm run dev # 默认 5173
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:5173  # 期望 200

# 4. 数据库连接（用于手工校验）
psql "postgresql://ontology:ontology@localhost:5432/ontology" -c "\dt"

# 5. Doris 连接（用于索引表校验）
mysql -h 127.0.0.1 -P 9030 -u root -e "SHOW DATABASES;"

# 6. Trino 连接（用于主数据/虚拟表校验）
#    或用 API: GET /datasources/{name}/explore 间接验证

# 7. 确认存量数据无 PHYSICAL 残留（R5）
psql "$PG" -c "SELECT DISTINCT storage_type FROM object_types;"
psql "$PG" -c "SELECT DISTINCT kind FROM datasets;"
```

### 2.2 测试浏览器

- **推荐**：Chrome/Chromium，分辨率 1280×720（架构 HCI 文档基准宽度）。
- **辅助工具**：DevTools Network 面板（验证 API 调用）、Console 面板（捕获 JS 错误）。
- **自动化**：可用 chrome-devtools-mcp 扩展（`browser_*` 工具）驱动，或 Playwright/Cypress。

### 2.3 测试数据命名约定

所有测试创建的资源统一前缀，便于清理与识别：

```
本体:        test_<YYYYMMDD>_<suite>
对象类型:    test_<suite>_<entity>      (如 test_ts2_order)
数据源:      test_<suite>_<source>      (如 test_ts1_mysql)
同步任务:    test_<suite>_<sync>
数据集:      test_<suite>_<dataset>
Action:     test_<suite>_<action>
```

### 2.4 通用断言工具

测试人员需准备以下"断言手段"（附录 A 脚本已封装）：

| 断言类型 | 手段 |
|---------|------|
| 前端 UI | 肉眼/快照（`browser_take_snapshot`）/ 等待文本（`browser_wait_for`）|
| HTTP | DevTools Network / `curl` / `httpx` |
| PG 数据 | `psql` 直连查询 |
| Doris 数据 | `mysql -h 127.0.0.1 -P 9030` 查询 |
| 控制台错误 | DevTools Console / `browser_list_console_messages` |

---

## 三、测试分层与用例总览

| 套件 | 主题 | 对应数据流 | 用例数 | 优先级 |
|------|------|----------|--------|--------|
| TS-1 | 数据源接入全链路 | 流 A 步骤 1-4 | 6 | P0 |
| TS-2 | 托管对象建模 + 索引 | 流 A 步骤 5 + 索引加速 | 8 | P0 |
| TS-3 | 虚拟对象全链路 | 流 C + Virtual Table | 6 | P0 |
| TS-4 | 物理对象查询 | 流 B + 降级 | 7 | P0 |
| TS-5 | Action 写入闭环 | 流 E | 9 | P0 |
| TS-6 | 时间旅行查询 | 流 D | 4 | P1 |
| TS-7 | AI 辅助建模 | 流 F | 3 | P1 |
| TS-8 | 运行洞察与可观测性 | /ops + /metrics | 4 | P1 |
| TS-9 | HCI 与无障碍 | frontend-hci-review | 12 | P2 |
| TS-10 | 故障注入与降级 | FMEA §八 | 5 | P1 |
| **合计** | | | **64** | |

每个用例统一格式：

```
TC-<suite>-<n> <标题>
  前置条件: ...
  操作步骤: 1. ... 2. ...
  预期-前端: ...
  预期-网络: ...
  预期-数据库: ...
  预期-降级/异常(如有): ...
```

---

## 四、测试套件 TS-1：数据源接入全链路（流 A）

> **架构对应**：data-flow-diagrams.md §2 流 A 步骤 1-4。外部数据 → DataSource(PG + Gravitino JDBC catalog) → 探索 → SyncTask → SeaTunnel MAIN pipeline → Iceberg 托管表 → 自动产生 `kind=MANAGED` 数据集。

### TC-TS1-1 创建数据源（写 PG + 注册 Gravitino catalog）

**前置**：准备一个可访问的外部 MySQL/PG 测试库（可用 docker 起一个 `mysql:8` 放测试数据），或复用栈内已有的测试数据源。

**步骤**：
1. 前端访问 `http://localhost:5173/data`（Rail ② 数据对接）
2. 点击"新建数据源"按钮，填写：api_name=`test_ts1_mysql`、display_name=`测试MySQL`、connector_type=`mysql`、host/port/database/用户名/密码
3. 点击"测试连接"按钮
4. 点击"保存"

**预期-前端**：
- 测试连接成功显示绿色"连接成功"徽标
- 保存后数据源卡片出现在列表，状态徽标为"已连接"(CONNECTED)
- 无 console error

**预期-网络**：
- `POST /api/datasources/{name}/test-connection` → 200
- `POST /api/datasources` → 201
- `GET /api/datasources` → 200 含新数据源

**预期-数据库**：
```sql
-- PG: data_sources 表有记录，status=CONNECTED，gravitino_catalog_name 非空
SELECT api_name, status, gravitino_catalog_name, connector_type
FROM data_sources WHERE api_name = 'test_ts1_mysql';
-- 期望: test_ts1_mysql | CONNECTED | <catalog名> | mysql

-- credentials 表有对应凭据（secret_data 是 JSONB，不验证明文）
SELECT api_name, credential_type FROM credentials WHERE api_name LIKE '%test_ts1%';
```
- Gravitino：`curl http://localhost:8090/api/metalakes/ontology/catalogs` 含新注册的 JDBC catalog

### TC-TS1-2 探索数据源 schema（Trino SHOW TABLES / DESCRIBE）

**前置**：TC-TS1-1 通过。

**步骤**：
1. 在数据源卡片点击"查看详情"进入 `/data/sources/test_ts1_mysql`
2. 切换到"探索"tab
3. 选择一个 database，查看表列表
4. 点击某张表的"查看列"和"采样"

**预期-前端**：
- 表列表正确显示外部库的真实表名
- 列信息显示列名 + 类型 + 是否可空
- 采样数据以表格展示前 N 行
- 加载过程有骨架屏（非纯"加载中"文字）

**预期-网络**：
- `POST /api/datasources/test_ts1_mysql/explore` → 200 返回表列表
- `GET /api/datasources/test_ts1_mysql/explore/{db}/{table}/sample` → 200 返回采样行

**预期-数据库**：无写入（探索是只读操作）。

### TC-TS1-3 创建同步任务（SyncTask，DRAFT 状态）

**前置**：TC-TS1-1 通过，存在可同步的表。

**步骤**：
1. 在数据源详情页选择一张表，点击"创建同步任务"
2. 填写：api_name=`test_ts1_sync`、sync_mode=`incremental`、transaction_type=`append`、incremental_column=`updated_at`（或对应表的增量列）
3. 保存

**预期-前端**：
- 任务卡片出现在数据源详情页的同步任务区，状态=DRAFT
- 显示 AI 推荐的同步配置（若 AI 可用）

**预期-网络**：
- `POST /api/datasources/test_ts1_mysql/sync-tasks` → 201

**预期-数据库**：
```sql
SELECT api_name, sync_mode, transaction_type, status, target_dataset_api_name, pipeline_name
FROM sync_tasks WHERE api_name = 'test_ts1_sync';
-- 期望: test_ts1_sync | incremental | append | DRAFT | <dataset> | <pipeline>
```

### TC-TS1-4 启动同步任务（SeaTunnel MAIN pipeline → Iceberg）

**前置**：TC-TS1-3 通过。

**步骤**：
1. 在同步任务卡片点击"启动"按钮
2. 等待任务状态变为"运行中"(RUNNING)
3. 等待同步完成（状态变为 COMPLETED 或持续 RUNNING 取决于 sync_mode）

**预期-前端**：
- 启动按钮点击后有 loading 态（防重复点击）
- 状态徽标实时更新：DRAFT → RUNNING → (COMPLETED)
- last_run_at 时间更新

**预期-网络**：
- `POST /api/sync-tasks/test_ts1_sync/start` → 200
- 轮询 `GET /api/sync-tasks/test_ts1_sync` 状态变化

**预期-数据库**：
```sql
-- sync_tasks 状态更新
SELECT status, last_run_at FROM sync_tasks WHERE api_name = 'test_ts1_sync';

-- 自动产生 MANAGED 数据集（架构原则：sync task 同步后自动产生 DatasetGovernance(kind=MANAGED)）
SELECT api_name, kind, data_source_api_name, storage_location
FROM datasets WHERE api_name = '<target_dataset>';
-- 期望: kind=MANAGED, data_source_api_name=test_ts1_mysql, storage_location 指向 Iceberg
```

**预期-Iceberg**：
- Trino 能查到同步落地的数据：`SELECT count(*) FROM iceberg.<schema>.<table>` > 0

### TC-TS1-5 停止同步任务

**前置**：TC-TS1-4 任务 RUNNING。

**步骤**：点击"停止"按钮。

**预期**：状态变为 STOPPED；`POST /api/sync-tasks/test_ts1_sync/stop` → 200；PG `sync_tasks.status='STOPPED'`。

### TC-TS1-6 异常路径：连接信息错误

**步骤**：创建数据源时填写错误的 host/port/密码。

**预期-前端**：测试连接显示红色错误，错误文案可执行（如"无法连接到 1.2.3.4:3306，请检查地址与端口"），不暴露后端堆栈。
**预期-网络**：`POST /test-connection` → 4xx。
**预期-数据库**：`data_sources` 不应有此错误记录（保存前测试连接失败不应落库）。

---

## 五、测试套件 TS-2：托管对象建模（流 A 步骤 5 + 索引加速）

> **架构对应**：data-flow-diagrams.md §2 步骤 5；index-acceleration-design.md §4.1 建模期。创建 ObjectType → PG 写元数据 → Gravitino 注册 → IndexSyncService.provision 建 Doris 索引表。

### TC-TS2-1 创建本体（Ontology）

**步骤**：
1. 访问 `http://localhost:5173/`（Rail ① 业务定义，默认首页）
2. 在侧栏点击"+ 新建本体"
3. 填写 api_name=`test_ts2_hr`、display_name=`测试人力本体`、description
4. 提交

**预期-前端**：
- api_name 失焦时实时校验格式 `^[a-z][a-zA-Z0-9_]*$`，非法显示行内错误
- 提交后按钮 loading，本体出现在侧栏树
- 无 console error

**预期-网络**：`POST /ontologies` → 201。
**预期-数据库**：
```sql
SELECT api_name, display_name FROM ontologies WHERE api_name = 'test_ts2_hr';
```

### TC-TS2-2 创建托管对象（五步向导，MANAGED + 数据集绑定）

**前置**：TC-TS1-4 产生了 `kind=MANAGED` 数据集；TC-TS2-1 本体存在。

**步骤**：
1. 侧栏选中 `test_ts2_hr`，点击"+ 新建对象"
2. **Step 0 存储类型 + 数据集**：选择"托管对象 MANAGED"，数据集列表只显示 `kind=MANAGED` 的数据集（验证 F1 kind 过滤），选中一个
3. **Step 1 配置属性**：点击"从数据集生成属性"按钮，自动生成属性；设置主键字段、标题字段；勾选若干属性的"可搜索"(searchable)
4. **Step 2 设置关系**：（可选）添加一个关系
5. **Step 3 配置操作**：（可选）添加一个 ActionType
6. **Step 4 审核并创建**：确认提交

**预期-前端**：
- Step 0 切换 MANAGED/VIRTUAL 时数据集列表按 kind 过滤（dataset-ontology-binding.md F1）
- "从数据集生成属性"一键生成所有列，类型映射正确（typeMapping.ts）
- 源列下拉选项 = 数据集列
- 提交后对象卡片出现在列表，图谱视图能显示该节点

**预期-网络**：`POST /ontologies/test_ts2_hr/object-types/create` → 201（单事务原子创建对象+属性+关系+操作）。

**预期-数据库**：
```sql
-- object_types: storage_type=MANAGED（R5，无 PHYSICAL）
SELECT api_name, storage_type, primary_key, title_property
FROM object_types WHERE api_name = '<新对象>';

-- properties: physical_mapping 落地（R4 单向引用，dataset 无反向 FK）
SELECT api_name, data_type, is_primary_key, indexed,
       physical_dataset_api_name, physical_catalog, physical_schema, physical_table, physical_column
FROM properties p JOIN object_types o ON p.object_type_id = o.id
WHERE o.api_name = '<新对象>';
-- 期望: searchable=true 的属性 indexed=true；physical_* 列有值

-- datasets 表无 object_type_id 反向外键（R4）：检查 schema 无此列
SELECT column_name FROM information_schema.columns
WHERE table_name='datasets' AND column_name LIKE '%object_type%';
-- 期望: 0 行
```

**预期-Doris（R2 索引红线）**：
```sql
-- Doris 索引表 idx_<api_name> 已建，仅含主键+索引列+热点属性，无全量明细
SHOW TABLES LIKE 'idx_<api_name>';
SHOW COLUMNS FROM idx_<api_name>;
-- 期望: 列 = 主键列 + indexed=true 的列；不含 description/大字段/ARRAY/STRUCT
```

### TC-TS2-3 编辑托管对象（编辑回填不丢 physical_mapping）

**前置**：TC-TS2-2 通过。

**步骤**：
1. 对象卡片点击"编辑"按钮
2. 观察向导 Step 0 是否预选中原数据集，Step 1 各属性源列是否回填
3. 修改某个属性的 searchable，保存

**预期-前端**：Step 0 数据集已选中，Step 1 源列已回填（dataset-ontology-binding.md F3 编辑回填修复）。
**预期-网络**：`PATCH /ontologies/test_ts2_hr/object-types/<type>/batch` → 200。
**预期-数据库**：
```sql
-- physical_mapping 不丢失
SELECT api_name, physical_column FROM properties p JOIN object_types o ON p.object_type_id=o.id
WHERE o.api_name='<对象>' AND p.api_name='<被改属性>';
-- indexed 更新生效
SELECT api_name, indexed FROM properties ... WHERE api_name='<被改属性>';
```
**预期-Doris**：`rebuild` 触发，`SHOW COLUMNS FROM idx_<type>` 反映新索引列（IndexSyncService.rebuild）。

### TC-TS2-4 删除托管对象（级联 + 索引清理）

**前置**：TC-TS2-2 通过。

**步骤**：
1. 对象卡片点击"删除"
2. 在 ConfirmDialog 中输入对象名确认（HIGH 级确认）
3. 确认删除

**预期-前端**：
- 弹出 HIGH 级 ConfirmDialog，需输入对象名才能确认（HCI 6.1）
- 显示级联影响（N 个属性 / M 个关系 / K 个动作）
- 删除后对象从列表和图谱消失

**预期-网络**：`DELETE /ontologies/test_ts2_hr/object-types/<type>` → 204。
**预期-数据库**：
```sql
SELECT * FROM object_types WHERE api_name='<对象>';  -- 0 行
SELECT p.* FROM properties p JOIN object_types o ON p.object_type_id=o.id WHERE o.api_name='<对象>';  -- 0 行（ON DELETE CASCADE）
```
**预期-Doris**：`deprovision` 触发，`SHOW TABLES LIKE 'idx_<type>'` → 0 行。

### TC-TS2-5 索引字段提取红线（拒绝大字段入 Doris）

**步骤**：创建对象时，把一个 `description`(STRING) 或 STRUCT 类型属性标记为 searchable。

**预期**：
- 后端 IndexFieldExtractor 拒绝红线类型入 Doris（index-acceleration-design.md §3.4）
- PG 中 properties.indexed=true，但 Doris idx 表不包含该列
- 日志中有 skipped 记录

**预期-数据库**：
```sql
-- Doris idx 表不含 description 列
SHOW COLUMNS FROM idx_<type> LIKE 'description';  -- 0 行
```

### TC-TS2-6 延迟关联（MANAGED 暂不关联数据集）

**步骤**：创建 MANAGED 对象，Step 0 勾选"暂不关联"。

**预期-前端**：Review 步骤显示 ⚠"未关联数据集"警告；详情页显示"未关联"徽章。
**预期-数据库**：
```sql
SELECT physical_dataset_api_name FROM properties p JOIN object_types o ON p.object_type_id=o.id
WHERE o.api_name='<对象>';
-- 期望: 全部 NULL
```

### TC-TS2-7 异常路径：唯一性约束冲突

**步骤**：创建与已存在对象同 api_name 的对象。

**预期-前端**：错误提示"api_name 已存在"，不创建。
**预期-网络**：`POST /object-types/create` → 409。
**预期-数据库**：原对象不变，无重复记录。

### TC-TS2-8 切换列表/图谱视图

**步骤**：在 OntologyWorkspace 顶部切换"列表"/"图谱"视图。

**预期-前端**：
- 列表视图：卡片网格，每个卡片显示名称·属性数·关系数 + 关联徽章（dataset-ontology-binding.md F4）
- 图谱视图：Cytoscape 画布，节点=对象，边=关系；MANAGED/VIRTUAL 节点颜色区分；右下角有图例
- 图谱工具栏：布局/重排/缩放/自适应/锁定/导出齐全
- hover 节点邻域高亮有 0.2s 过渡动画

---

## 六、测试套件 TS-3：虚拟对象全链路（流 C + Virtual Table 登记）

> **架构对应**：dataset-ontology-binding.md §三 B2 + §四 F0；data-flow-diagrams.md §4 流 C。外部表 → 登记为 VIRTUAL 数据集 → 创建 VIRTUAL 对象 → Trino 联邦查询，全程无 Doris。

### TC-TS3-1 登记虚拟表（POST /datasources/{ds}/virtual-tables）

**前置**：TC-TS1-1 数据源可访问外部表。

**步骤**：
1. 进入数据源详情页"探索"tab
2. 选一张外部表，点击"登记为虚拟表"按钮
3. 在 RegisterVirtualTableDialog 中确认 api_name=`test_ts3_orders_vt`、display_name，提交

**预期-前端**：
- explore tab 每行有"登记为虚拟表"按钮（F0）
- 登记成功 toast 提示
- 错误（409 重名 / 422 不可联）正确展示

**预期-网络**：`POST /api/datasources/test_ts1_mysql/virtual-tables` → 201。
**预期-数据库**：
```sql
SELECT api_name, kind, is_view, data_source_api_name, storage_location
FROM datasets WHERE api_name = 'test_ts3_orders_vt';
-- 期望: kind=VIRTUAL, is_view=false, storage_location='<catalog>.<db>.<table>' 三段式
```

### TC-TS3-2 虚拟表 schema 拉取（B3 分流）

**前置**：TC-TS3-1 通过。

**步骤**：访问 `/data/datasets/test_ts3_orders_vt` 数据集详情页，查看 schema tab。

**预期-前端**：显示外部表的真实列（走 Gravitino 联邦拉列，B3），徽章显示 VIRTUAL。
**预期-网络**：`GET /api/datasets/test_ts3_orders_vt/schema` → 200 返回外部表列。
**预期-数据库**：无写入。

### TC-TS3-3 创建虚拟对象（VIRTUAL + 无"暂不关联"）

**前置**：TC-TS3-1 虚拟表存在；本体存在。

**步骤**：
1. 新建对象，Step 0 选"虚拟对象 VIRTUAL"
2. 数据集列表只显示 `kind=VIRTUAL` 的虚拟表
3. 验证：VIRTUAL 模式下**无**"暂不关联"选项（dataset-ontology-binding.md F1 原则2）
4. 选中虚拟表，配置属性（源列来自外部表列），提交

**预期-前端**：VIRTUAL 无"暂不关联"；源列下拉来自外部表列。
**预期-网络**：`POST /object-types/create` → 201，storage_type=VIRTUAL。
**预期-数据库**：
```sql
SELECT storage_type FROM object_types WHERE api_name='<虚拟对象>';  -- VIRTUAL
-- properties 有 physical_mapping 指向三段式
SELECT physical_catalog, physical_schema, physical_table FROM properties ...;
```
**预期-Doris**：`SHOW TABLES LIKE 'idx_<虚拟对象>'` → **0 行**（VIRTUAL 全程无 Doris，R 红线）。

### TC-TS3-4 虚拟对象查询（Trino 联邦，无 Doris）

**前置**：TC-TS3-3 通过，外部表有数据。

**步骤**：在对象详情/列表点击"查询"或通过查询界面查询该虚拟对象。

**预期-前端**：返回外部表的真实数据。
**预期-网络**：`POST /objects/load`（object_type_api_name 指向虚拟对象）→ 200。
**预期-数据库**：Trino 执行了联邦查询（可通过 Trino UI `/v1/query` 验证有 SQL），Doris 无此对象索引表。

### TC-TS3-5 虚拟对象写入 guard（F5）

**前置**：TC-TS3-3 通过。

**步骤**：
1. 编辑虚拟对象的向导 Step 3（配置操作），尝试添加 CREATE/UPDATE/DELETE 类 action
2. 观察

**预期-前端**：VIRTUAL 对象 Step 3 禁用写操作添加，提示"虚拟对象不支持写操作"（F5 guard）。

### TC-TS3-6 异常路径：外部表不可联时登记

**步骤**：登记一个不存在的表为虚拟表。

**预期-前端**：错误提示，不创建。
**预期-网络**：`POST /virtual-tables` → 422。
**预期-数据库**：`datasets` 表无该记录（422 不写记录，B2 验收）。

---

## 七、测试套件 TS-4：物理对象查询（流 B + Doris 索引 + 降级）

> **架构对应**：index-acceleration-design.md §3.5 §4.3 §4.4；data-flow-diagrams.md §3。两段式：Doris 索引过滤 → Iceberg 点查全量；Doris 不可用降级 Trino 全表扫。

### TC-TS4-1 无 filter 查询（Trino scan）

**前置**：TC-TS2-2 托管对象存在，Iceberg 有数据。

**步骤**：查询该对象，不传 filter，limit=50。

**预期-网络**：`POST /objects/load` 无 filter → 200 返回全量行。
**预期-降级路径**：日志/metrics 显示 `fallback_total{reason="no_filter"}` 增加（index-acceleration-design.md §3.5）。

### TC-TS4-2 带 filter 查询走 Doris 索引

**前置**：TC-TS2-2 对象有 indexed 属性，Doris idx 表有数据（需先 backfill 或等同步）。

**步骤**：查询该对象，filter={status: "active"}（indexed 列）。

**预期-前端**：返回 status=active 的对象。
**预期-网络**：`POST /objects/load` → 200。
**预期-数据库/指标**：
- `ontology_object_query_index_hit_total{object_type=...}` 增加（走 Doris 索引路径）
- 两段式生效：Doris 返回 ID 列表 → Iceberg load_by_ids 返回全量属性

### TC-TS4-3 显式 rids 查询（直接 Iceberg 点查）

**步骤**：查询时传 rids=[id1, id2]。

**预期**：跳过 Doris，直接 Iceberg load_by_ids（index-acceleration-design.md §3.5）。

### TC-TS4-4 Read-your-writes（Action 后立即查询命中 object_state）

> 此用例与 TS-5 联动，详见 TC-TS5-4。

### TC-TS4-5 降级：Doris 不可用时走 Trino

**步骤**：
1. 停掉 Doris：`docker compose stop doris-fe doris-be`
2. 查询带 filter 的托管对象

**预期-前端**：查询仍返回正确结果（延迟可能增加）。
**预期-网络**：`POST /objects/load` → 200。
**预期-指标**：`fallback_total{reason="doris_down"}` 增加。
**预期-日志**：warning "doris_down"。
**清理**：`docker compose start doris-fe doris-be`，等待 healthy。

### TC-TS4-6 降级：索引未建（table_exists=False）

**步骤**：查询一个未 provision 索引的对象（如延迟关联对象，或手动 drop idx 表）。

**预期**：走 Trino scan，指标 `fallback_total{reason="not_built"}` 增加。

### TC-TS4-7 聚合查询

**步骤**：`POST /objects/aggregate`，对某属性做 count/sum/group_by。

**预期-网络**：`POST /objects/aggregate` → 200 返回聚合结果。

---

## 八、测试套件 TS-5：Action 写入闭环（流 E）

> **架构对应**：action-loop-design.md 全文。execute → PG 原子事务(object_state + execution_log + outbox) → applied → read-your-writes → 异步 outbox/CDC。这是最高优先级闭环。

### TC-TS5-1 定义 ActionType

**前置**：托管对象存在。

**步骤**：在 `/actions`（Rail ③ 能力赋予）页面为某托管对象定义一个 ActionType，含参数定义 + 规则（constraint/derivation）。

**预期-网络**：`POST /api/actions/definitions/{ontology}/{action_type}` → 201。
**预期-数据库**：
```sql
SELECT api_name, parameters, rules, status FROM action_types WHERE api_name='<action>';
```

### TC-TS5-2 执行 Action（applied + 原子事务，R6/R7）

**前置**：TC-TS5-1 通过。

**步骤**：
1. 在对象详情或 ActionsOverview 点击"执行"按钮，打开 ExecuteActionDialog
2. 填写参数，提交
3. 立即（<1s）查询该对象

**预期-前端**：
- 执行按钮 loading（useAsyncAction）
- 返回结果展示 status=applied + affected_objects
- 立即查询能看到变更（read-your-writes，object_state 兜底）

**预期-网络**：
- `POST /api/actions/execute/{ontology}/{object_type}/{action}` → 200，body `status=applied`
- 紧接着 `POST /objects/load` → 200 返回更新后数据

**预期-数据库（R6 原子事务，三者同事务）**：
```sql
-- 1. object_state：版本递增，properties 更新
SELECT rid, version, properties, updated_at FROM object_state
WHERE rid='<被改对象>';
-- 期望: version >= 2, properties 含新值

-- 2. action_execution_logs：审计记录，status=COMPLETED
SELECT action_type_api_name, idempotency_key, parameters, mutations, status
FROM action_execution_logs WHERE action_id='<action执行id>';
-- 期望: status=COMPLETED, idempotency_key 唯一

-- 3. outbox：副作用队列
SELECT effect_type, effect_config, status, retry_count
FROM outbox WHERE action_execution_id='<execution_log id>';
-- 期望: status 从 PENDING → COMPLETED（OutboxExecutor 消费后）
```

### TC-TS5-3 幂等性（重复 idempotency_key 返回 accepted）

**步骤**：用相同 idempotency_key 再次执行同一 Action。

**预期-网络**：返回 `status=accepted`（重复请求），不重复写 object_state。
**预期-数据库**：object_state version 不变；action_execution_logs 无新记录（命中已有）。

### TC-TS5-4 Read-your-writes 立即可见（R7）

**步骤**：TC-TS5-2 执行后，**立即**（不等 CDC）查询被改对象。

**预期**：查询返回最新值（object_state 兜底），无需等异步 CDC 同步。
**预期-指标**：`object_query_index_hit_total` 增加（read-your-writes 命中也计 hit）。

### TC-TS5-5 行级 OCC 冲突（409 Conflict）

**步骤**：
1. 查询对象获取 version=5
2. 用另一请求把对象改到 version=6
3. 用旧 version=5 提交 Action

**预期-前端**：显示"数据已被他人修改，请刷新后重试"。
**预期-网络**：`POST /execute` → 409，body 含 `error_type=ConflictError`。
**预期-数据库**：object_state version 仍为 6（不被旧版本覆盖）。

### TC-TS5-6 OutboxExecutor 异步消费（webhook/write-back）

**前置**：ActionType 配置了 webhook 或 write-back effect。

**步骤**：执行 Action，等待 1-2s。

**预期-数据库**：
```sql
SELECT status, retry_count, last_error, updated_at FROM outbox
WHERE action_execution_id='<id>';
-- 期望: status=COMPLETED, retry_count=0（成功消费）
```
- 若配置 webhook：外部 webhook 端点收到请求（含 X-Idempotency-Key）
- 若配置 write-back：外部目标表数据被更新，且行含 `gaia_sync_tx`/`gaia_sync_user` 标记（反馈环防御）

### TC-TS5-7 Outbox 失败重试 + DLQ

**步骤**：配置一个必然失败的 webhook（错误 URL），执行 Action，等待重试耗尽。

**预期-数据库**：
```sql
SELECT status, retry_count, max_retries, last_error FROM outbox WHERE ...;
-- 期望: 经历 PENDING→(重试)→DLQ，retry_count 达 max_retries，last_error 有内容
```
**预期-日志**：warning 级 DLQ 记录。

### TC-TS5-8 VIRTUAL 对象执行 Action 被拒（R3）

**前置**：虚拟对象存在。

**步骤**：对 VIRTUAL 对象执行写 Action。

**预期-网络**：`POST /execute` → 422（后端 Action 校验 + 前端 F5 guard 互补）。
**预期-数据库**：object_state 无写入。

### TC-TS5-9 Action 参数校验失败

**步骤**：执行 Action 时缺必填参数 / 传未知参数 / 规则 constraint 不满足。

**预期-网络**：`POST /execute` → 4xx，`status=validation_failed`。
**预期-数据库**：object_state / execution_log / outbox 均无写入（校验失败不进事务）。

---

## 九、测试套件 TS-6：时间旅行查询（流 D）

> **架构对应**：data-flow-diagrams.md §5；architecture_plan.md §5.3。Trino `FOR VERSION AS OF {snapshot_id}` 读 Iceberg 历史快照。

### TC-TS6-1 获取快照列表

**步骤**：对有数据的托管对象的数据集查询快照。

**预期-网络**：`GET /api/datasets/<dataset>/snapshots` → 200 返回快照列表（snapshot_id, timestamp, operation）。

### TC-TS6-2 时间旅行查询（读历史版本）

**前置**：对象数据有多次变更（多个 Iceberg snapshot）。

**步骤**：用旧 snapshot_id 查询对象。

**预期-网络**：`POST /objects/load` 带 `as_of_snapshot_id=<旧快照>` → 200 返回历史版本数据。
**预期**：返回的是该快照时刻的数据，非当前最新。

### TC-TS6-3 快照过期处理

**步骤**：用一个不存在的/已过期的 snapshot_id 查询。

**预期-网络**：返回 410 Gone + 明确错误 `SnapshotExpiredError`（architecture_plan.md §8.4）。

### TC-TS6-4 FOR VERSION AS OF 语法支持验证

> PoC 验证项（architecture_plan.md §14.1 P0）。

**步骤**：直接在 Trino 执行 `SELECT ... FROM iceberg.<table> FOR VERSION AS OF <snapshot_id>`。

**预期**：若 Gravitino Connector 不透传，则降级使用 `iceberg_catalog` 直连。记录实际行为。

---

## 十、测试套件 TS-7：AI 辅助建模（流 F）

> **架构对应**：data-flow-diagrams.md §7。后端纯代理（不感知业务），前端持有所有 prompt 模板。

### TC-TS7-1 AI 生成对象类型建议（SSE 流式）

**前置**：AI provider 配置可用（`.env` 中 AI API key）。

**步骤**：
1. 在 OntologyWorkspace 打开 AI 面板
2. 输入"汽车制造领域，需要车型、工单、零部件三个对象"
3. 观察流式渲染

**预期-前端**：
- SSE 逐 token 渲染建议卡片（partial 事件）
- 完成后展示可解析的对象类型建议（result 事件）
- 用户可确认后批量创建

**预期-网络**：`POST /ai/stream` → 200，Content-Type `text/event-stream`，含 partial/result 事件。

### TC-TS7-2 AI 建议批量创建

**步骤**：确认 AI 建议后批量创建。

**预期**：对象批量创建，图谱刷新；批量操作有进度反馈"创建中 3/20…"（HCI 7.2 useProgress）。

### TC-TS7-3 AI 不可用降级

**步骤**：临时禁用 AI provider（改 .env 重启），再次调用。

**预期-前端**：友好错误提示，不崩溃。
**预期-网络**：`POST /ai/stream` → 5xx 或流中断。

---

## 十一、测试套件 TS-8：运行洞察与可观测性

> **架构对应**：architecture_plan.md §9.4；frontend-hci-review.md §10.4。`/ops` 页面 + `/metrics` 端点。

### TC-TS8-1 运行洞察页面加载

**步骤**：访问 `http://localhost:5173/ops`（Rail ④ 运行洞察）。

**预期-前端**：
- 显示指标卡片：数据源数 / 同步任务数 / 对象类型数 / 关系数
- 显示同步状态分布、查询 P95、成功率（HCI 10.4 重做后）
- 无 N+1 轮询（并行加载）

### TC-TS8-2 健康检查端点

**步骤**：`curl http://localhost:8000/health`。

**预期**：`{"status":"ok"}`。

### TC-TS8-3 Prometheus 指标端点

**步骤**：`curl http://localhost:8000/metrics`。

**预期**：返回 Prometheus 格式文本，含关键指标：
- `ontology_object_query_index_hit_total`
- `ontology_object_query_fallback_total{reason=...}`
- 各层调用耗时直方图

### TC-TS8-4 trace_id 传递

**步骤**：发起一次查询，查看后端日志。

**预期**：日志含 `trace_id`、`span_id`、`layer`、`method`、`duration_ms`，同一请求在所有 Layer 调用中共享 trace_id（architecture_plan.md §9.4）。

---

## 十二、测试套件 TS-9：HCI 与无障碍验收

> **架构对应**：frontend-hci-review.md §十二 校验清单。对照实施记录逐项回归。

### TC-TS9-1 导航：rail 文字标签 + titlebar 路径（HCI A）

**步骤**：浏览 4 个 Rail，观察 rail 是否有可见文字标签（非纯图标 hover）。

**预期**：rail 常显文字标签；titlebar 中部显示当前 rail label（非写死"本体建模平台"）。

### TC-TS9-2 按钮热区 + loading 态（HCI B）

**步骤**：测量 `.btn-xs`/`.btn-sm` 点击热区；执行写操作观察 loading。

**预期**：最小热区 ≥ 28×28px；写操作按钮有 `.is-loading` 态 + 防重复点击。

### TC-TS9-3 表单实时校验 + 行内错误（HCI C）

**步骤**：新建本体填非法 api_name（如大写开头、含特殊字符）。

**预期**：失焦即时校验，错误行内定位（不弹 Toast），文案可执行。

### TC-TS9-4 弹窗 ESC + focus trap + 遮罩关闭（HCI D）

**步骤**：打开 ConfirmDialog/CreateObjectWizard，按 ESC / 点击遮罩 / Tab 遍历。

**预期**：ESC 关闭；遮罩点击关闭；焦点在弹窗内循环（focus trap）；关闭后焦点回归触发按钮；`aria-modal=true`。

### TC-TS9-5 骨架屏 + 批量进度（HCI E）

**步骤**：刷新数据源/对象列表观察加载态；触发批量创建。

**预期**：加载用骨架屏（非纯"加载中"）；批量操作显示进度条"创建中 N/M…"。

### TC-TS9-6 图谱图例 + 过渡动画（HCI F）

**步骤**：打开图谱视图，hover 节点。

**预期**：右下角有图例（实体=橙/虚拟=青/关系边=灰）；hover 邻域高亮有 0.2s 渐变动画。

### TC-TS9-7 对比度 ≥ 4.5:1（HCI G）

**步骤**：用 DevTools 检查 muted 文字色与背景对比度。

**预期**：暗色主题 muted 提亮到 `#738091`，小字强制用 secondary，对比度 ≥ 4.5:1。

### TC-TS9-8 键盘可达性（HCI G）

**步骤**：纯键盘 Tab 遍历侧栏对象树、按钮。

**预期**：所有可点击元素可 Tab 聚焦（无 `<div onClick>`），焦点有可见高亮边框。

### TC-TS9-9 emoji 不作唯一语义（HCI G）

**步骤**：屏幕阅读器模式（或检查 aria-label）浏览 rail、能力按钮。

**预期**：emoji 有配套 `aria-label` 或可见文字。

### TC-TS9-10 快捷键（HCI B）

**步骤**：按 `/`(聚焦搜索)、`n`(新建)、`g o/d/a`(切 rail)、Workspace `1/2/3`(切视图)。

**预期**：快捷键生效（useHotkeys）。

### TC-TS9-11 状态标记统一（HCI H）

**步骤**：检查全站状态徽章。

**预期**：统一用 `StatusBadge` 组件，无散落 `.status-active/.status-experimental` 旧类（HCI 10.2）。

### TC-TS9-12 术语一致（HCI H）

**步骤**：检查"新建对象"vs"添加数据源"用语。

**预期**：新增=创建业务对象，添加=接入外部资源（constants/terms.ts 统一）。

---

## 十三、测试套件 TS-10：故障注入与降级验证

> **架构对应**：architecture_plan.md §八 FMEA；§13.2 异常路径必覆盖。对照降级策略表逐项验证。

### TC-TS10-1 Doris 不可用 → 物理查询降级 Trino

（同 TC-TS4-5）确认查询不中断，指标 `doris_down` 增加。

### TC-TS10-2 Gravitino 不可用 → 权限校验失败

**步骤**：`docker compose stop gravitino`，执行查询。

**预期**：权限校验失败（无降级路径，architecture_plan.md §0.2）；查询返回明确错误；恢复后正常。

### TC-TS10-3 Iceberg/RustFS 不可用 → 写入失败但 Action 热路径不阻塞

**步骤**：`docker compose stop rustfs`，执行 Action。

**预期**：Action execute 仍返回 applied（PG object_state 兜底，热路径不依赖 Iceberg）；异步 outbox 同步（INDEX/ARCHIVE effect）失败但不阻断（action-loop-design.md §四.4）。

### TC-TS10-4 索引同步延迟（outbox 驱动）

**步骤**：大量写入后立即查询，观察索引同步延迟。

**预期**：outbox INDEX effect → OutboxExecutor → DorisIndexStore.upsert，同步 ≤1s（近实时 SLO）；延迟期间查询降级 Trino 或 read-your-writes 兜底。（原 SeaTunnel INDEX pipeline 路径已于 2026-07 去 SeaTunnel 化删除。）

### TC-TS10-5 Outbox 重试与 DLQ

（同 TC-TS5-7）确认失败 outbox 经重试进 DLQ，不影响 Action applied。

---

## 十四、数据库正确性校验清单

测试执行时，每个套件完成后用以下 SQL 批量校验（连接串见附录 B）。

### 14.1 元数据一致性（PG）

```sql
-- R5: storage_type 仅 MANAGED/VIRTUAL（无 PHYSICAL）
SELECT storage_type, count(*) FROM object_types GROUP BY storage_type;
-- 期望: 仅 MANAGED / VIRTUAL 两行

-- R5: datasets.kind 仅 MANAGED/VIRTUAL
SELECT kind, count(*) FROM datasets GROUP BY kind;

-- R4: datasets 无 object_type 反向外键
SELECT column_name FROM information_schema.columns
WHERE table_name='datasets' AND column_name LIKE 'object_type%';
-- 期望: 0 行

-- 唯一性: api_name 在所属范围内唯一
SELECT api_name, count(*) FROM object_types GROUP BY api_name HAVING count(*)>1;
-- 期望: 0 行（注意 api_name 可能在不同 ontology 下重复，按 ontology+api_name 查）

-- 外键级联: 删除 object_type 后 properties/link_types/action_types 级联删除
-- （在 TS-2-4 删除用例中验证）
```

### 14.2 Action 闭环完整性（PG）

```sql
-- R6: 配了 effects 的 execution 必有对应 outbox（副作用可选，未配 effects 不产生 outbox）
SELECT el.id, el.status, count(o.id) AS outbox_count
FROM action_execution_logs el
JOIN action_types at ON at.api_name = el.action_type_api_name
LEFT JOIN outbox o ON o.action_execution_id = el.id
WHERE el.status='COMPLETED'
  AND jsonb_array_length(COALESCE(at.rules->'effects','[]'::jsonb)) > 0
GROUP BY el.id, el.status
HAVING count(o.id) = 0;
-- 期望: 0 行（配了 effects 的 applied execution 必有 outbox，原子事务保证）

-- outbox 终态分布
SELECT status, count(*) FROM outbox GROUP BY status;
-- 期望: PENDING 不应长期堆积（OutboxExecutor 1s 轮询消费）

-- R7: read-your-writes - object_state version 递增无跳号
SELECT rid, version FROM object_state ORDER BY rid, version;
```

### 14.3 Doris 索引红线（R2）

```sql
-- 每个托管对象的 idx 表仅含主键 + 索引列 + 热点属性
-- 在 mysql -h 127.0.0.1 -P 9030 执行
SHOW TABLES LIKE 'idx_%';
-- 对每张表:
SHOW COLUMNS FROM idx_<type>;
-- 期望: 列 ⊆ {主键列} ∪ {properties.indexed=true 的列}
-- 红线: 不含 description/ARRAY/STRUCT/ATTACHMENT/MEDIA_REFERENCE
```

### 14.4 Iceberg 主数据（Trino）

```sql
-- 唯一写入入口: Doris 无业务写入痕迹（Doris 只有 idx_ 前缀表）
SHOW TABLES;  -- 在 Doris
-- 期望: 仅 idx_* 表

-- Iceberg 有快照历史（时间旅行前提）
-- 通过 GET /api/datasets/<ds>/snapshots 验证
```

---

## 十五、缺陷分级与回归

### 15.1 缺陷分级

| 级别 | 判定标准 | 示例 |
|------|---------|------|
| **P0 阻断** | 违反架构红线（R1-R7）或主干流跑不通 | Action 非原子提交；Doris 存全量明细；VIRTUAL 可写入 |
| **P1 严重** | 核心功能不可用或数据错误 | 查询降级失效；read-your-writes 不生效；physical_mapping 丢失 |
| **P2 一般** | 非核心功能缺陷或 HCI 不达标 | 骨架屏缺失；快捷键失效；对比度不足 |
| **P3 轻微** | 文案/样式小问题 | 术语不一致；徽章颜色偏差 |

### 15.2 回归测试触发条件

每次以下变更必须重跑对应套件（architecture_plan.md §13.3）：

| 变更 | 必跑套件 |
|------|---------|
| 后端 Layer/Service 改动 | TS-1~TS-6 全部 |
| 前端组件改动 | TS-9 + 受影响套件 |
| 组件版本升级（Doris/Gravitino/SeaTunnel 等） | 全部 64 用例 |
| 数据库 schema 迁移 | TS-2/TS-3 + 数据库校验清单 |

### 15.3 测试报告模板

每轮测试输出：

```
测试轮次: <日期>_<版本>
环境: docker compose ps 快照 + git commit hash
执行人: <name>
用例总数: 64  通过: __  失败: __  阻断: __
失败用例明细:
  - TC-<id>: <现象> | <前端/网络/DB 哪层失败> | <缺陷等级> | <截图/日志链接>
红线检查:
  - R1 Iceberg 唯一写入: ✅/❌
  - R2 Doris 索引红线: ✅/❌
  - ...
数据库校验: ✅/❌（附 14 节 SQL 输出）
```

---

## 十六、附录 A：自动化执行脚本

提供两个自动化辅助脚本，测试人员可基于此快速执行与校验：

### A.1 API 层冒烟脚本（基于现有 verify_e2e_full.py 扩展）

```bash
# 运行已有的真实环境 E2E 验证（No mocks）
.venv/bin/python scripts/verify_e2e_full.py
# 覆盖: A1 dataset-link / Action loop / VIRTUAL guard / Doris index / ConflictDetector / IngestionFilter

# 运行 Action 闭环 live 验证（旧 verify_action_loop_live.py 已删，outbox 驱动方案冒烟见 commit 73b1c7f）
# Action 同步链路现由 outbox INDEX/ARCHIVE effect 驱动，详见 docs/design/action-sync-outbox-design.md

# 运行 Doris 索引 live 验证
.venv/bin/python scripts/verify_index_live.py
```

### A.2 数据库校验脚本（一键执行第 14 节 SQL）

将以下保存为 `scripts/verify_db_consistency.sh`：

```bash
#!/usr/bin/env bash
# 用法: ./scripts/verify_db_consistency.sh
set -euo pipefail
PG="postgresql://ontology:ontology@localhost:5432/ontology"

echo "=== R5: storage_type 取值 ==="
psql "$PG" -c "SELECT storage_type, count(*) FROM object_types GROUP BY storage_type;"

echo "=== R5: datasets.kind 取值 ==="
psql "$PG" -c "SELECT kind, count(*) FROM datasets GROUP BY kind;"

echo "=== R4: datasets 无 object_type 反向外键 ==="
psql "$PG" -c "SELECT column_name FROM information_schema.columns WHERE table_name='datasets' AND column_name LIKE 'object_type%';"

echo "=== R6: 配 effects 的 execution 缺 outbox 的（应为 0）==="
psql "$PG" -c "SELECT count(*) FROM (SELECT el.id FROM action_execution_logs el JOIN action_types at ON at.api_name=el.action_type_api_name WHERE el.status='COMPLETED' AND jsonb_array_length(COALESCE(at.rules->'effects','[]'::jsonb))>0 GROUP BY el.id HAVING count((SELECT 1 FROM outbox o WHERE o.action_execution_id=el.id))=0) t;"

echo "=== outbox 终态分布 ==="
psql "$PG" -c "SELECT status, count(*) FROM outbox GROUP BY status;"

echo "=== R2: Doris 索引表清单（库 ontology）==="
mysql -h 127.0.0.1 -P 9030 -u root -e "SHOW TABLES FROM ontology LIKE 'idx_%';" 2>/dev/null || echo "(Doris 不可达或无 idx 表)"
```

> **推荐**：直接运行 `./scripts/verify_db_consistency.sh`（已内置 psql/mysql 客户端自动回退到 docker compose exec，无需本地安装）。

### A.3 浏览器自动化（chrome-devtools-mcp / Playwright）

测试人员可用 chrome-devtools-mcp 扩展的 `browser_*` 工具驱动前端，或迁移为 Playwright 脚本。关键操作映射：

| 手工操作 | 自动化工具 |
|---------|----------|
| 打开页面 | `browser_navigate({url})` |
| 等待文本 | `browser_wait_for({text})` |
| 点击按钮 | `browser_click({selector})` |
| 填表单 | `browser_fill({selector, value})` |
| 查 DOM | `browser_evaluate({expression})` |
| 捕获 console 错误 | `browser_list_console_messages({level:"error"})` |
| 捕获网络请求 | `browser_list_network_requests()` |
| a11y 快照 | `browser_take_snapshot()` |

---

## 十七、附录 B：速查表

### B.1 端口与服务

| 服务 | 端口 | 用途 |
|------|------|------|
| 前端 dev | 5173 | React UI |
| 后端 API | 8000 | FastAPI |
| PostgreSQL | 5432 | 本体元数据（ontology/ontology） |
| Gravitino | 8090 | 物理资产注册 |
| RustFS | 9000 | S3 兼容存储 |
| Iceberg REST | 9001 | Iceberg catalog |
| Doris FE | 9030 (MySQL) / 8030 (HTTP) | 索引加速 |
| Trino | 8080 | 联邦查询 |
| Kafka | 9092 | 实时索引消息 |
| SeaTunnel | 5801 | 数据流水线 |

### B.2 关键 API 端点速查

| 数据流 | 端点 | 方法 |
|--------|------|------|
| 流 A | `/api/datasources`, `/api/datasources/{n}/explore`, `/api/datasources/{n}/sync-tasks`, `/api/sync-tasks/{n}/start` | POST/GET |
| 流 A 建模 | `/ontologies`, `/ontologies/{o}/object-types/create`, `PATCH /ontologies/{o}/object-types/{t}/batch`, `PATCH .../dataset-link` | POST/PATCH |
| 流 A 虚拟表 | `/api/datasources/{n}/virtual-tables` | POST |
| 流 B/C | `/objects/load`, `/objects/aggregate` | POST |
| 流 D | `/api/datasets/{n}/snapshots`, `/objects/load` (as_of_snapshot_id) | GET/POST |
| 流 E | `/api/actions/definitions/{o}/{a}`, `/api/actions/execute/{o}/{t}/{a}` | POST |
| 流 F | `/ai/stream` | POST (SSE) |
| 可观测 | `/health`, `/metrics` | GET |

### B.3 关键 PG 表速查

| 表 | 用途 | 关键列 |
|----|------|--------|
| `ontologies` | 本体容器 | api_name, display_name |
| `object_types` | 对象类型 | api_name, storage_type(MANAGED/VIRTUAL), primary_key |
| `properties` | 属性 | indexed, physical_dataset_api_name, physical_catalog/schema/table/column |
| `link_types` | 关系 | source/target_object_type_id, cardinality |
| `action_types` | 动作定义 | parameters, rules(JSONB) |
| `action_execution_logs` | Action 审计 | idempotency_key(unique), status, mutations |
| `outbox` | 副作用队列 | effect_type(WEBHOOK/WRITE_BACK), status(PENDING/COMPLETED/DLQ), retry_count |
| `object_state` | read-your-writes 兜底 | rid, version(OCC), properties(JSONB) |
| `data_sources` | 数据源 | connector_type, status, gravitino_catalog_name |
| `sync_tasks` | 同步任务 | sync_mode, status, target_dataset_api_name |
| `datasets` | 数据集 | kind(MANAGED/VIRTUAL), storage_location, data_source_api_name |
| `credentials` | 凭据 | secret_data(JSONB) |

### B.4 用例与架构红线映射

| 红线 | 验证用例 |
|------|---------|
| R1 Iceberg 唯一写入 | TC-TS5-2, TC-TS10-3 |
| R2 Doris 索引红线 | TC-TS2-2, TC-TS2-5, TC-TS3-3, §14.3 |
| R3 VIRTUAL 只读 | TC-TS3-5, TC-TS5-8 |
| R4 单向引用 | TC-TS2-2, §14.1 |
| R5 storage_type 取值 | TC-TS2-2, §14.1 |
| R6 Action 原子事务 | TC-TS5-2, §14.2 |
| R7 Read-your-writes | TC-TS5-4, TC-TS5-2 |

---

*关联文档：[architecture_plan.md](../architecture/architecture_plan.md) · [data-flow-diagrams.md](../design/data-flow-diagrams.md) · [dataset-ontology-binding.md](../design/dataset-ontology-binding.md) · [action-loop-design.md](../architecture/action-loop-design.md) · [index-acceleration-design.md](../architecture/index-acceleration-design.md) · [frontend-hci-review.md](../design/frontend-hci-review.md) · [implementation-status.md](../architecture/implementation-status.md)*

*测试执行中如发现与架构文档不一致，按缺陷分级记录，并同步更新 [implementation-status.md](../architecture/implementation-status.md)。*

---

## 附录 C：编写本计划时的基线观察（2026-06-19）

在编写本测试计划并试运行 `scripts/verify_db_consistency.sh` 时，对当前环境（git HEAD as of 2026-06-19）记录到以下基线状态，供测试人员作为对照基线并重点关注：

| 观察项 | 实测值 | 架构预期 | 测试关注点 |
|--------|--------|---------|-----------|
| `object_types.storage_type` 取值 | VIRTUAL=10, MANAGED=57 | 仅 MANAGED/VIRTUAL | ✅ R5 达标，无 PHYSICAL 残留 |
| `datasets.kind` 取值 | MANAGED=6, VIRTUAL=1 | 仅 MANAGED/VIRTUAL | ✅ R5 达标 |
| `datasets` 表 object_type 反向外键 | 0 列 | 0 列（单向引用） | ✅ R4 达标 |
| `properties.physical_*` 列 | 5 列齐全 | physical_catalog/schema/table/column/dataset_api_name | ✅ R4 物理映射落地 |
| Doris `ontology` 库表 | 4 张 `idx_*`（idx_asset/device/sensor/ticket） | 仅 idx_* 前缀 | ✅ R2 红线达标，无业务全量表 |
| 配 effects 的 execution 缺 outbox | 0 行 | 0 行 | ✅ R6 原子事务达标 |
| outbox 终态 | 全部 COMPLETED | PENDING 不堆积 | ✅ OutboxExecutor 消费正常 |
| **object_state version 分布** | 11 行，`max(version)=1` | Action 更新应使 version 递增 | ⚠️ **重点验证 TC-TS5-2**：当前基线所有 object_state 都是 version=1，可能是测试数据特性（每次 Action 建新对象而非更新），也可能提示 OCC 版本递增路径未被真实触发。测试时执行 UPDATE 类 Action 后应确认 `version >= 2`。 |

> 以上基线观察会随测试数据变化，仅作为编写时的快照。测试人员应以实际执行结果为准，但若发现 `object_state` version 始终为 1，应作为 P1 缺陷上报（违反 R7 read-your-writes 版本语义）。
