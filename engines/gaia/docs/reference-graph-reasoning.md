# 图关联推理与时空多维分析 —— 参考资料汇编

> **用途**：本文件汇编本特性设计所依据的全部参考材料（Palantir 官方资料 + 业界技术调研），作为写设计文档、ADR、测试用例的概念源头。设计文档引用本文件而非内嵌材料。
>
> **关联文档**：[graph-reasoning-design.md](./architecture/graph-reasoning-design.md)（特性设计文档）
>
> **汇编日期**：2026-07-02

---

## 一、Palantir 产品谱系与能力定位

Palantir 两大核心产品在图与时空分析上定位明确、底层同源：

| 产品 | 核心场景 | 图分析特征 | 时空分析侧重 |
|------|----------|------------|--------------|
| **Gotham** | 情报、军工、执法、反恐 | 无预设模型的自由线索探索；多跳隐蔽链路挖掘；非结构化情报融合 | 战场态势感知；人员/装备轨迹追踪；多源地理情报叠加 |
| **Foundry** | 金融风控、供应链、制造、能源 | 结构化业务网络分析；风险规则化推演；与业务系统闭环联动 | 工业设备时空序列；物流轨迹；供应链地理分布与时效联动 |

二者共享同一套本体语义层与图存储引擎。Gotham 偏向分析师主导的开放式调查，Foundry 偏向业务流程内嵌的标准化运营分析。

**核心本质**：以本体（Ontology）为语义中枢，将多源异构数据转化为可推理、可溯源、可执行的数字孪生网络，在万亿级实体规模下支持自由探索式分析，同时满足强合规场景的全链路审计要求。

---

## 二、Palantir 底层四层架构（OSv2 多模型混合存储）

Palantir 并未采用单一图数据库，而是构建"存储虚拟化-语义建模-计算引擎-应用交互"的四层架构：

### 1. 存储层：OSv2 多模型混合存储
- **属性图核心存储**：存储本体"骨架"——对象节点、链接关系及属性，支持高效图遍历与模式匹配，针对数十亿级节点/边做了分布式分片优化
- **时序存储引擎**：专门处理时间序列数据（传感器、交易流水、轨迹点），保证时间窗口查询毫秒级响应
- **空间地理存储**：基于空间索引处理多边形、坐标、地理区域数据，支持空间相交、邻近查询
- **非结构化存储**：文档、图像、视频等大文件以对象存储留存，本体中仅保留引用指针与元数据
- **数据虚拟化层**：支持"查询下推"，无需集中迁移数据即可对接外部数据源

核心优势：不同数据类型选择最优存储方案，通过本体层统一语义，避免单一图数据库在时序、空间分析上的性能短板。

### 2. 语义层：本体驱动
- **Object Type**：对应现实实体，每个对象包含属性、唯一标识与安全标签
- **Link Type**：定义实体间业务关系，支持有向、加权、多态关系
- **实体对齐机制**：通过模糊匹配、唯一标识校验、多源冲突解决，将不同数据源的同一实体归一
- **时空属性原生集成**：地点对象自带地理坐标，事件/轨迹对象自带时间戳

### 3. 计算引擎层
- **图遍历引擎**：Search Around、多跳路径查询、共同邻居识别、最短路径计算
- **时空联合计算引擎**：空间范围过滤 + 时间窗口约束 + 图遍历结合
- **增量更新引擎**：新数据接入后 90 秒内完成实体关联与图谱更新
- **物化图视图**：对高频查询路径预计算

### 4. 应用交互层
- **Vertex**：图可视化与推演工具，支持图谱遍历、因果链分析、场景模拟（What-if）
- **Quiver**：时序与时空分析工具，支持轨迹回放、异常检测、多序列对比
- **Gaia**（Palantir 地图组件名，与本项目同名是巧合）：地理空间地图组件，叠加实体位置、轨迹、区域事件
- **Object Explorer**：批量筛选工具，结合图关系过滤做下钻分析

---

## 三、Palantir ObjectSet API（核心查询抽象，对等设计依据）

> **本节是 Gaia `query_with_dataframe` 工具的对标源头**。Palantir 一切查询基于 ObjectSet，Search Around 只是 ObjectSet 上的操作符。

### 1. ObjectSet 数据结构（判别联合）

ObjectSet 是判别联合类型（discriminator union），`type` 字段区分不同操作。来自 `palantir/foundry-platform-python` SDK 的完整类型清单：

```
ObjectSet (判别联合，type 字段区分):
├── searchAround              ← 搜索关联（核心图遍历操作）
├── interfaceLinkSearchAround ← 接口链路搜索关联
├── filter / filterObjects    ← 过滤
├── union / intersect / subtract  ← 集合运算
├── nearestNeighbors          ← KNN 向量近邻
├── static                    ← 静态对象列表
├── objectType                ← 从对象类型出发
├── methodInput
```

**关键点**：searchAround 是**顶层 type**，结构为 `{"type":"searchAround","link":"...","objectSet":{...}}`，嵌套 objectSet。

### 2. REST API（Create Temporary Object Set）

```
POST /api/v2/ontologies/{ontology}/objectSets/createTemporaryObjectSet
```

Payload 示例（searchAround 嵌套）：
```json
{
  "objectSet": {
    "type": "searchAround",
    "link": "linkTypeAPI",
    "objectSet": {
      "type": "static",
      "objects": ["..."]
    }
  }
}
```

### 3. OSDK 链式 API（TypeScript）

```typescript
Objects.search()
  .flights()
  .filter(flight => flight.departureAirportCode.exactMatch(airportCode))
  .searchAroundPassengers();   // Search Around 是 ObjectSet 的方法
```

### 4. 官方硬限制（实测值，Gaia 直接采用）

| 限制 | 值 | 来源 |
|---|---|---|
| 单次查询 Search Around 次数 | ≤ 3 次 | Palantir Functions 文档 |
| Search Around 结果集（OSv2） | ≤ 1000 万对象 | Palantir 官方文档 |
| Search Around 结果集（OSv1） | ≤ 10 万对象 | Palantir 官方文档 |
| 加载到内存（.all()） | ≤ 10 万对象 | Palantir Functions 文档 |
| 加载超时阈值 | > 1 万对象可能超时 | Palantir Functions 文档 |
| 聚合 buckets | ≤ 1 万 | Palantir 官方文档 |

### 5. filter 操作符（含空间）

- 属性：`exactMatch` / `range` (lt/lte/gt/gte) / `isTrue`/`isFalse` / `contains` / `hasProperty`
- 字符串：`phrase` / `phrasePrefix` / `matchAnyToken` / `fuzzyMatchAllTokens` 等
- **Geopoint**：`withinDistanceOf` / `withinPolygon` / `withinBoundingBox`
- **GeoShape**：`withinBoundingBox` / `intersectsBoundingBox` / `withinPolygon` / `intersectsPolygon` / `doesNotIntersectPolygon`
- Link 过滤：`isPresent`
- 组合：`Filters.and()` / `Filters.or()` / `Filters.not()`

**关键设计**：空间过滤是 filter 的子算子，与属性过滤平级，不是独立查询 API。

### 6. 集合运算与 KNN

- `.union()` / `.intersect()` / `.subtract()`：同类型对象集合并集/交集/差集
- `.nearestNeighbors()`：KNN 向量近邻搜索（需对象有 embedding 属性，K≤100，维度≤2048）

---

## 四、本体驱动的时空数据存储决策（静态属性 vs 动态 GTS）

> **本节是 Gaia 类型驱动路由（GEOPOINT/GEOSHAPE/GEOTEMPORAL_SERIES/TIME_SERIES）的对标源头**。

Palantir 时空数据严格二分，由本体元数据自动驱动路由：

### 1. 静态空间属性 —— 存入属性索引层
- **判断标准**：位置固定、低频变更、仅区域筛选/距离计算
- **本体声明**：属性类型为 `Geopoint`（单点）或 `Geoshape`（线/多边形）
- **存储**：作为对象原生属性存在 OSv2 属性索引层，自动构建 R 树/GiST 空间索引
- **典型**：供应商/工厂/仓库坐标、厂区围栏、固定运输线路

### 2. 动态时空序列（GTS）—— 存入专用时空序列引擎
- **判断标准**：高频上报（秒级/分钟级）、需轨迹回放/时空共现、体量大
- **本体声明**：对象挂载 **GTSR（Geotemporal Series Reference）** 属性，仅存 Series ID，点位数据独立存储
- **存储**：专用时空序列引擎，支持 Live Streaming（实时）和 Dataset Archive（归档）两种模式
- **强制 Schema 三字段**：`Series ID` + `Timestamp` + `Position`（GeoPoint）
- **典型**：车辆/船舶 GPS 轨迹、库存水位时序、设备状态监测

### 3. 纯时序（无空间）
- 普通时间字段 → 静态属性
- 高频连续时序指标 → Time Series Property，通过引用挂载，机制同 GTS 但无 Position

### 4. 反模式
- ❌ 车辆实时位置存对象普通属性（无法回溯、拖慢查询）→ 必须建模为 GTS
- ❌ 工厂固定地址建成 GTS（冗余）→ 用静态空间属性
- ❌ 全量时空数据走实时模式（成本高）→ Live/Archive 分层

---

## 五、Search Around 与关联推理执行逻辑

### 1. 统一执行流程（对标 Palantir OSS）
1. 权限校验：拼接 security_marking 过滤条件，前置过滤不可见节点/边
2. 属性/时空过滤：先筛起始对象集，缩小遍历范围
3. 定向图遍历：沿指定 Link Type 多跳遍历，持续剪枝，默认 3 跳
4. 结果封装：返回标准化对象集，含实体属性、路径证据、权限标记

### 2. 隐藏链路挖掘
- 多跳遍历 + 共同邻居识别：发现无直接关联实体通过中间节点形成的隐蔽网络
- 模式匹配规则：自定义网络模式（环形资金流、多层嵌套公司、团伙通讯网络）
- Search Around：从任意节点出发沿关系逐层扩展，探索中发现未知关联

### 3. 高级推理能力
| 能力 | Neo4j | NebulaGraph |
|------|-------|-------------|
| 最短路径 | `shortestPath()` | `FIND SHORTEST PATH` |
| 共同邻居 | `apoc.algo.commonNeighbors` | 多跳交集查询 |
| 环形/嵌套模式 | Cypher 模式匹配 + APOC | nGQL 循环路径匹配 |
| 风险评分传导 | 节点属性 + 关系权重迭代 | 图算法 + 批量更新 |

---

## 六、路径推理、异常检测、风险评分推演

### 1. 路径推理
- 最短路径、全路径、关键节点识别
- 路径结果保留每跳证据：数据来源、时间戳、关系权重

### 2. 异常检测
- 规则型：本体 Function 定义规则（如"单笔转账超100万且流向高风险地区"）
- 统计型：时序偏离度、网络结构异常度（节点度数突增、关系模式异变）

### 3. 风险评分推演
- 关联风险：与高风险实体跳数越近、权重越高，得分越高
- 行为风险：交易频率/金额/模式偏离历史基线
- 外部风险：制裁名单、负面舆情、监管标签
- 评分挂载对象，输入变更自动重算，可追溯

---

## 七、独有优势：本体绑定与全链路溯源

1. **一键溯源原始数据**：图谱任意节点/关系/评分/告警，可沿数据血缘反查原始数据源、接入时间、转换逻辑
2. **决策血缘完整留存**：从数据接入、实体合并、规则计算、人工判断到 Action 执行，全链路日志不可篡改
3. **合规审计原生支持**：满足金融/军工/医疗监管，可直接生成审计包，含完整证据链与操作记录

---

## 八、双引擎落地（Neo4j 开发 + NebulaGraph 生产）

### 1. 本体元数据双引擎 Schema 映射
| 本体概念 | Neo4j | NebulaGraph |
|---|---|---|
| Object Type | 节点 Label | Tag |
| Link Type | Relationship Type | Edge Type |
| Property | 节点/关系属性 | Tag/Edge 属性 |
| 主键 | 节点属性 + 唯一约束 | Tag 属性 + 唯一索引；VID 由主键哈希生成 |
| 安全标签 | security_marking 属性 | security_marking 属性 |
| 时态属性 | 关系 start_time/end_time | Edge start_time/end_time |

### 2. 元数据管理规范
- 版本化管理：本体变更走审批，支持分支/回滚，发布生成新版本号
- 强约束校验：Link Type 必须明确源/目标，禁止泛化"关联"关系
- 索引自动生成：元数据标记索引字段 → 自动在 Neo4j 建 B-Tree/空间索引，NebulaGraph 建原生/GEO 索引

### 3. 存储设计
- **属性分层**：核心过滤属性存图节点（剪枝），大体积属性外挂 PG
- **边属性轻量化**：仅权重 + 时效 + 有效期 + security_marking
- **双向遍历**：Neo4j 原生 OUTGOING/INCOMING/BOTH，NebulaGraph `GO REVERSELY`，不冗余存反向边
- **VID 设计（NebulaGraph）**：业务主键哈希 + 类型前缀，64 位整型，同类型分布均衡

### 4. 性能熔断（对齐 Palantir 生产约束）
- 单次查询跳数上限默认 3 跳，超过需审批
- 单轮结果集上限 1000 万节点，超出截断报错
- 起始对象集 > 10 万转批量离线计算

### 5. 反模式
| 反模式 | 规避 |
|---|---|
| 单一 RELATED_TO 万能边 | 按业务语义拆分细分边类型 |
| 边存大量业务属性 | 边仅存权重与标识，属性放节点或外部 |
| 无前置过滤全图多跳 | 强制前置属性/时空过滤，限制跳数 |
| 实体状态变迁新建节点 | 单实体 + 历史属性/时序存储 |
| 所有属性全塞图数据库 | 核心过滤属性存图，其余外挂 |

---

## 九、多模型协同（Doris + PostGIS + TimescaleDB）

### 1. 精简双存（核心原则）
不全量双存，只把空间计算必需的核心字段同步到 PostGIS，全量明细留 Doris：

| 引擎 | 存储内容 | 规模 | 职责 |
|---|---|---|---|
| Doris | 全量业务数据 | 亿~百亿级 | 全量底座、粗筛、批量统计、报表 |
| PostGIS | 核心空间主数据（主键+坐标+过滤字段） | 万~百万级 | 精确空间计算、拓扑、距离排序 |
| TimescaleDB | 核心在线时序数据 | 千万~十亿级 | 实时轨迹、区域风险告警、时空联合 |

### 2. 数据同步方向
- 单一可信源（上游业务库 / Doris），单向同步到 PostGIS/TimescaleDB
- 禁止双向更新
- 同源分发：从上游统一分发，不在引擎间互相同步

### 3. 三层联动（供应链中断分析示例）
1. 图推理层（NebulaGraph/Neo4j）：3 跳遍历受影响实体 ID 集
2. 数仓层（Doris）：粗筛候选集 + 批量统计
3. 时空计算层（PostGIS + TimescaleDB）：精确空间距离 + 区域风险 + 时效测算
4. 应用层：聚合三层结果 + 证据链

### 4. 关键避坑
- 禁止跨引擎复杂 Join（应用层编排）
- Doris 无法下推 PostGIS 空间函数（geometry 转字符串），复杂空间计算必须拉到 PostGIS
- 空间索引只建在 PostGIS GiST，Doris S2 仅粗筛

---

## 十、行业最佳实践

### 1. 情报/执法：开放式线索调查
- 从已知实体 → 多轮 Search Around → 时空筛选 → 发现隐蔽团伙 → 证据链报告
- 先定义最小本体，随调查扩展；安全标签按权限过滤；全操作审计

### 2. 金融风控：反洗钱/欺诈网络
- "客户-账户-交易-公司-IP-地址"全维度图谱，3-5 跳识别 UBO 与壳公司
- 风险评分与交易监控结合，高关联风险降阈值
- 每笔告警附完整证据链

### 3. 供应链：时空图联动韧性管理
- "供应商-零件-工厂-订单-物流"多层级图谱 + 地理 + 时效
- 中断时秒级定位受影响产线/订单
- 模拟物流改道/替代供应商，对比多方案
- 案例：空客将中断响应从数周缩至数小时

### 4. 性能与治理
- 高频路径预物化，冷数据分层存储
- 实体类型宁少勿滥，关系贴合业务语义
- 对象级/属性级/关系级三级权限
- Git for Data：图谱与数据时间回溯

---

## 附录 A：Neo4j → NebulaGraph 迁移成本调研

### 方言差异
| 维度 | Neo4j Cypher | NebulaGraph nGQL |
|---|---|---|
| 术语 | node/label/relationship | vertex/tag/edge |
| ID | 内部自动生成 | 强制 rid（string/int） |
| schema | 可选 | 强 schema |
| 边模型 | 边无独立 id | 边 = (src, dst, rank) |
| 遍历语法 | `MATCH (n)-[r*1..3]->(m)` | `GO 1 TO 3 STEPS FROM ... OVER ... YIELD` |
| 相等运算符 | `=` | `==` |
| openCypher | 原生 | 3.0+ 部分兼容 |

### 迁移成本来源（按严重度）
1. 查询语言不兼容（最重）
2. ID 策略差异
3. schema 强弱差异
4. 边语义差异
5. 驱动 API 差异

### 降低迁移成本对策
- 方言隔离层（GraphQueryIR + GraphDialect，Gaia 本期不抽象但留口子）
- rid 用稳定主键（object_state.rid）
- 强 schema 建模先行
- 驱动抽象（GraphStore Protocol，本期 Neo4jGraphStore 收口）

---

## 附录 B：Doris 空间能力调研

| 能力 | Doris 4.x |
|---|---|
| GEO 类型 | 伪类型（基于 String/Varchar） |
| ST_Distance / ST_Contains / ST_Within | ✅ 支持 |
| WKT 格式 | ✅ |
| **空间索引** | ❌ 无原生 R-tree/GiST，靠 S2 编码 + 倒排粗筛 |
| 大规模空间过滤性能 | ❌ 无空间索引，大数据量退化 |

**结论**：Doris 空间能力薄弱，精确空间分析走 PostGIS（GiST 索引）。Doris 仅做属性全量存储与粗筛。

---

## 附录 C：Neo4j 资源治理调研

- `dbms.memory.transaction.database_max_size` 可设事务内存上限
- **APOC `apoc.path.expand` 不被内存追踪器检测**，可能突破上限 OOM（issue #56）
- 监控：`dbms.listTransactions()` 查 `estimatedUsedHeapHeapMemory`

**结论**：多跳遍历用原生 Cypher `MATCH (n)-[*1..3]->(m)` + LIMIT，不用 APOC path.expand。APOC 仅用于辅助（如 `apoc.coll` 集合运算）。

---

## 附录 D：Ibis 查询抽象层调研

### Ibis 定位
- Voltron Data 主导，工业级成熟，对接 DuckDB/Postgres/Spark/BigQuery 等二十余引擎
- `TableExpr` 是判别联合式查询 AST（与 Palantir ObjectSet 同构）
- 不可变链式：`.filter().join()` 返回全新 TableExpr
- 空间能力原生（GeoPoint/within_bbox/intersects/distance）
- lazy 求值：构建 AST 不执行，`execute()` 才编译

### 关键能力
- **AST → SQL**：各后端内置编译器，一行 `q.compile(backend="postgres")` 生成可执行 SQL
- **memtable**：`ibis.memtable` 把内存 DataFrame 转临时表，衔接图遍历结果（rid 集）回 Ibis 链式
- **多后端**：Postgres 后端成熟（PostGIS/TimescaleDB 同 PG 生态可用）

### 局限
- 无 Ontology 本体语义层（需上层封装）
- AST → JSON 序列化未正式支持（issue #1267 open）
- 无 Neo4j 后端（图遍历在 Ibis 之外）
- 无原生时序算子（基础时间窗口可用，复杂时序自补）
- 无分布式执行引擎（仅查询编译）

### Gaia 适配结论
- **Ibis 只管 PG 侧**（PostGIS 空间 filter + TimescaleDB 时序 filter），不碰 Doris
- Doris 仍是 `query_with_sql` 的领地
- Neo4j 图遍历在 Ibis 之外，结果用 memtable 灌回
- 两层 IR：自研 pydantic ObjectSet IR（LLM 产 JSON）→ 翻译 → Ibis TableExpr（执行）

---

## 附录 E：PostGIS + TimescaleDB 共存调研

### 共存可行性
- **官方支持**：Timescale 官方文档有 PostGIS 集成章节，hypertable 可当普通表做 PostGIS 查询
- **PG16 兼容**：TimescaleDB 2.13+ 支持 PG16，PostGIS 3.4+ 支持 PG16
- **shared_preload_libraries**：TimescaleDB 需预加载（必须第一位），PostGIS 不需预加载，不冲突

### 现成镜像
- `ngosang/timescaledb-postgis:2.13.0-pg16-postgis3.4`（社区维护，PG16+TimescaleDB2.13+PostGIS3.4 一体）
- 多版本标签可选

### 结论
PostGIS + TimescaleDB 同 PG 实例共存是成熟方案，直接用现成镜像。

---

## 附录 F：LLM 产 ObjectSet JSON 的稳定性保障

### 为什么 JSON 优于 Python 代码
- 语法约束极强（固定字段名、判别联合 type 枚举）
- 树形递归模板，Few-Shot 易复用
- Pydantic 一键校验
- 无代码注入风险

### 四层稳定保障
1. **Prompt 标准化**：强制规则 + Few-Shot 样例 + 本体元数据上下文注入
2. **输出清洗**：正则截取 ```json``` 块 + 去末尾逗号
3. **Pydantic 强校验**：判别联合 + 枚举约束 + 白名单字段 + 嵌套深度 ≤ 3
4. **自动纠错闭环**：结构化报错 + 原始 JSON 回灌 LLM 重试（≤ 2 次）

### 执行链路
```
自然语言 → LLM 产 ObjectSet JSON（Prompt 标准化 + Few-Shot + 本体上下文）
→ 输出清洗 → Pydantic 强校验 → 失败则自动纠错闭环
→ ObjectSetExecutor: IR 翻译 → Ibis/Neo4j 执行 → 阈值校验 → 返回
```
