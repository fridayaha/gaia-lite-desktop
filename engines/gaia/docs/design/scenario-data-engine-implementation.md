# Scenario 数据引擎层：Palantir 实现 + Gaia 建议实现

> **范围**：把 Scenario 的数据写入/读取**先从 Palantir 数据引擎层讲透**（它怎么实现 overlay、用什么存储、查询怎么走、可靠性如何保证），**再讲 Gaia 当前代码仓建议如何实现**（映射到 PG/Doris/Iceberg 技术栈，性能/可靠性要求）。
> **前置**：[`scenario-and-decision-exhaust-design.md`](./scenario-and-decision-exhaust-design.md) §1.2（what-if 逻辑）+ §2-§6（数据模型/Service/API）。本文是那些设计的**数据引擎层深度补充**。
> **日期**：2026-07-06

---

## 目录
- [第一部分：Palantir 数据引擎层如何实现 Scenario](#第一部分palantir-数据引擎层如何实现-scenario)
  - [一、Ontology 后端的微服务架构（Scenario 的运行环境）](#一ontology-后端的微服务架构scenario-的运行环境)
  - [二、OSv2 的存储与索引分层（Scenario overlay 的物理基础）](#二osv2-的存储与索引分层scenario-overlay-的物理基础)
  - [三、用户编辑（User Edits）的写入与持久化](#三用户编辑user-edits的写入与持久化)
  - [四、Scenario 在这个架构里落在哪一层](#四scenario-在这个架构里落在哪一层)
  - [五、Palantir Scenario 的读取路径](#五palantir-scenario-的读取路径)
  - [六、Palantir 的可靠性与一致性保证](#六palantir-的可靠性与一致性保证)
  - [七、Palantir 的性能特性与限制](#七palantir-的性能特性与限制)
- [第二部分：Gaia 当前代码仓建议如何实现](#第二部分gaia-当前代码仓建议如何实现)
  - [八、Gaia 技术栈与 Palantir 的映射](#八gaia-技术栈与-palantir-的映射)
  - [九、写入路径的工程实现](#九写入路径的工程实现)
  - [十、读取路径的工程实现](#十读取路径的工程实现)
  - [十一、索引设计与查询计划](#十一索引设计与查询计划)
  - [十二、事务与并发控制](#十二事务与并发控制)
  - [十三、可靠性与故障恢复](#十三可靠性与故障恢复)
  - [十四、性能边界与容量规划](#十四性能边界与容量规划)
  - [十五、对底层引擎的要求清单](#十五对底层引擎的要求清单)

---

# 第一部分：Palantir 数据引擎层如何实现 Scenario

> 这一部分回答的核心问题：**Palantir 的 Scenario overlay 物理上存在哪里？用什么引擎？查询怎么走？为什么这么设计？**
> 理解这一部分，才能理解第二部分 Gaia 实现的设计依据。

## 一、Ontology 后端的微服务架构（Scenario 的运行环境）

Palantir 官方明确（[Object backend overview](https://palantir.com/docs/foundry/object-backend/overview/)）：

> "The Foundry platform uses a microservices architecture in which multiple services together comprise the Ontology backend."

Ontology **不是一个数据库**，而是一组微服务。核心服务有六个：

| 服务 | 职责 | 写时路径 | 读时路径 |
|------|------|---------|---------|
| **OMS**（Ontology Metadata Service） | 元数据与类型契约：ObjectType/LinkType/ActionType/Function/权限 | 校验写入符合类型定义与权限 | 提供对象结构定义与权限规则 |
| **Object databases**（OSv2） | 对象事实存储 + 索引 + 查询运行时 | 接收 Funnel 构建的索引与状态更新 | 为 OSS 提供查询/检索/关系遍历 |
| **Funnel**（Object Data Funnel） | 写入编排与索引构建 | **主写路径**：数据标准化/映射/索引 | 驱动读路径索引的持续更新 |
| **OSS**（Object Set Service） | 对象查询网关与编排 | 不直接参与 | **主读路径**：search/filter/aggregate/searchAround |
| **Actions Service** | 业务写入编排：Action 执行/参数校验/权限/副作用 | 写路径业务入口 | 不直接参与 |
| **Functions** | 无副作用计算 | 提供校验与计算 | 提供派生属性/聚合 |

**关键洞察**：读写职责严格分离——**所有写经 Funnel，所有读经 OSS**。OSv2（Object databases）是两者之间的存储与索引层。这是 OSv2 相比 OSv1（Phonograph）最根本的架构变化。

### 1.1 OSv2 不是单一数据库

官方原文：
> "Object Storage V2 architecture syncs objects through the Object Data Funnel service into **specialized object databases**"

即 OSv2 是"多种专用对象库并行"的体系，针对不同访问模式用不同底层存储。**官方未公开具体用了哪些数据库**（Cassandra/ES/Neo4j 等均未确认），但通过架构图、服务职责和开源组件可推断其分层结构（见下节）。

### 1.2 OSv1（Phonograph）的教训与 V2 的改进

OSv1（Phonograph）是 Foundry 原始对象库，**2026-06-30 后弃用**。它的核心问题：
- 紧耦合底层分布式文档库与搜索引擎，API 暴露过多低层数据库功能
- 读写职责混在一起，难水平扩展

OSv2 的改进（官方明确列出）：
- **分离索引与查询职责**，各自水平扩展
- 增量索引（默认，所有 object type）
- 单 object type 支持数百亿对象
- 用户编辑吞吐量提升（单 Action 可编辑 10000 对象）
- 用户编辑延迟降低、可更快观察到
- 支持 streaming datasource 低延迟入库
- Search Around 默认 100000 对象上限

> **对 Scenario 的意义**：Scenario 的 overlay 机制就建立在这个"读写分离 + edit layer"的架构上。OSv1 时代 Scenario 实现复杂且受限，OSv2 的 edit layer 让 Scenario 成为自然的 overlay。

---

## 二、OSv2 的存储与索引分层（Scenario overlay 的物理基础）

> ⚠ 本节基于官方架构图 + 服务职责 + 开源组件推断。官方未公开 OSv2 内部物理实现细节。推断依据来自 [第三方深度拆解](https://jishuzhan.net/article/2058456659199574018) + 官方 [Funnel batch pipelines](https://palantir.com/docs/foundry/object-indexing/funnel-batch-pipelines/) + [How user edits are applied](https://palantir.com/docs/foundry/object-edits/how-edits-applied/)。

### 2.1 OSv2 的四层职责

| 层 | 核心职责 | 推测的内部组件 |
|----|---------|--------------|
| **Storage Tier（存储层）** | 持久化对象事实与变更日志 | S1 对象事实 KV、S2 路由目录、S3 持久日志 |
| **Index Tier（索引层）** | 构建对象/关系/搜索索引 | I1 倒排属性索引、I2 关系图谱索引、I3 集合代数索引 |
| **Query Runtime（查询运行时）** | 接收 OSS 请求，查询编排与路由 | R1 OSS Router、R2 Search Around Engine、R3 Projection Engine |
| **Object Projection（对象投影层）** | 按权限与 schema 生成对象视图 | 动态物化结果 |

### 2.2 Storage Tier：三层存储

| 存储 | 作用 | 推测实现 | 对 Scenario 的意义 |
|------|------|---------|------------------|
| **S1 对象事实 KV** | 存对象最新内容（"V123 是红色、属于张三"） | Cassandra + AtlasDB（Palantir 自家 MVCC 事务层） | Scenario 的 base 数据 + overlay 都最终落在这里 |
| **S2 路由目录** | 存物理分片/路由/节点位置 | etcd/ZooKeeper | Scenario 的 overlay 行的路由信息 |
| **S3 持久日志** | Funnel 拥有的 Foundry Dataset，记录变更流水 | Parquet on S3 + Iceberg-style 目录 | **Scenario 的 overlay 持久化在这里**，故障恢复源 |

**关键**：S3 是 OSv2 的"灾备底"。官方明确（[How user edits are applied](https://palantir.com/docs/foundry/object-edits/how-edits-applied/)）：
> "All indexed data in object databases are considered **ephemeral**, requiring persistent storing of all Ontology data in other ways."

即 OSv2 的内存/磁盘索引（S1+I1/I2/I3）是**临时的**，真正的耐久性来自 S3（Funnel 拥有的持久 Dataset）。这与传统数据库"存盘即持久"截然不同。

### 2.3 Index Tier：三类专用索引

| 索引 | 解决的查询 | 推测实现 |
|------|-----------|---------|
| **I1 倒排属性索引** | 属性 filter（"所有 status=IDLE 的 Vehicle"） | Elasticsearch/OpenSearch（OSv1 已确认用 ES，OSv2 大概率沿用） |
| **I2 关系图谱索引** | Search Around（沿 LinkType 跳跃） | JanusGraph / 自研邻接表 |
| **I3 集合代数索引** | Object Set 交并差 + Aggregation | RoaringBitmap + 列存 |

**三类查询的 compute 成本**（官方计费表）：
- Base Query（属性过滤/加载/分页）：最低 2 compute-seconds
- Search Around（关系跳跃）：最低 5 compute-seconds
- Aggregation（sum/avg/groupBy）：最低 5 compute-seconds
- Action（写）：18 + 1/对象

### 2.4 一次写入的物理旅程（官方四阶段 + 推断细节）

以 `Vehicle.status` 从 `WELDING` 改为 `PAINTED` 为例：

```
① 源系统写入          MES 表一行 (vin=V123, status=PAINTED)
② Foundry 接入        Parquet 列写入 backing dataset 的一个 transaction
③ Changelog           Funnel 比对，产出只含变化行的中间 dataset
④ Merge Changes       变更 + Action 用户编辑按主键合并 → 全字段行
⑤ Indexing            转成索引格式（倒排 doc / 属性宽表行）→ 索引 dataset
⑥ Hydration           索引文件下载到 search node 内存/本地盘（可查）
⑦ 持久化 flush        与上次 flush 比对的增量，落入 Funnel-owned Dataset (S3)
⑧ OSS 查询返回        JSON 对象
```

**关键区分**：步骤 ⑦（Funnel 持久化 Dataset）≠ 步骤 ②（backing dataset）。前者是 Funnel 拥有的"合并后已生效的对象快照"，后者是上游系统的"原始来料"。重建索引时从前者 rehydrate。

---

## 三、用户编辑（User Edits）的写入与持久化

> 这是理解 Scenario overlay 的关键——**Scenario 的 overlay 本质上就是一组"挂在某个 scenarioRid 下的 user edits"**。

### 3.1 Action 写路径时序（官方机制）

官方原文（[How user edits are applied](https://palantir.com/docs/foundry/object-edits/how-edits-applied/) + [Apply Action API](https://palantir.com/docs/foundry/api/ontologies-v2-resources/actions/apply-action/)）：

```
客户端(Workshop/OSDK/Agent)
  → Actions Service：取 ActionType 契约 + 参数校验 + 权限 + Submission Criteria
  → Funnel：投递 modification instruction 到 offset-tracked 队列
  → 立即应用到 live index（S1 + I1/I2/I3）   ← 读后写一致的物理基础
  → 异步 flush 到 Funnel-owned 持久 Dataset（S3）  ← 耐久性
  → 写审计（who/what/when/why）
  → 触发副作用（Webhook/Notification，先审计后副作用）
  → 返回 ApplyActionResponse
```

### 3.2 Funnel 队列与 offset 追踪（核心机制）

官方原文（[How user edits are applied](https://palantir.com/docs/foundry/object-edits/how-edits-applied/)）：
> "When an Action is triggered, the Actions service sends a modification instruction to the Funnel service. This instruction is stored in a **Funnel-managed queue that has offset tracking** to support simultaneous user edits. Object Storage V2 tracks these offsets for any object type and any many-to-many link type with join tables. The offsets are applied to the **live indexed data** in the object database."

**工程含义**：
1. Action **不直接 UPDATE 对象库**，而是投递"修改指令"到 Funnel 队列
2. 队列通过 offset 顺序保证：多个并发编辑不乱序覆盖——**每个 object type 与每个多对多 link type 各有 offset**
3. 应用到 live index 是**即时的**——instruction 落 offset 后，读路径立即看到新值（这就是 read-after-write 一致性）
4. 之后异步 flush 到 S3——这一步不再影响读一致性，只保证"重启/重建时不丢"

### 3.3 Edit Layer（编辑层）—— Scenario overlay 的物理形态

社区与官方文档交叉印证（[Object edits conflict resolution](https://community.palantir.com/t/object-edits-conflict-resolution-strategy-not-working-as-expected/6479)）揭示了关键概念：

> "When an object is created via an action, the **edit layer** records the entire object — not just the properties explicitly set in the action... The edit layer used to be a key-value pair store in Cassandra back in the days but unsure of the Foundry backend with OSv2."

**即 Palantir 有一个独立的 "edit layer"**，它：
- 记录用户编辑（Action 产生的修改）的完整对象状态
- 覆盖在 base index（来自 datasource）之上
- 读取时：edit layer 有该对象 → 返回 edit layer 版本；没有 → 返回 base index 版本
- 这就是 **overlay 语义**——与 Scenario 的 "fork contains only the edits" 完全一致

**Scenario 的本质**：Scenario 就是"挂了特定 scenarioRid 标签的 edit layer"。普通用户编辑的 scenarioRid = main；Scenario 编辑的 scenarioRid = 该 Scenario 的 RID。读取时带 scenarioRid 参数，Funnel/OSS 就只应用该标签的 edits。

这解释了 Apply Action API 的 `scenarioRid` 参数（[官方 API](https://github.com/palantir/foundry-platform-python/blob/develop/docs/v2/Ontologies/Action.md)）：
> `scenarioRid`: The resource identifier of an ontology scenario to apply the action against.

即 Action 投递到 Funnel 队列时带 scenarioRid，edit layer 给该指令打上对应标签。

### 3.4 持久化的两个触发器

官方明确 flush 触发规则（[How user edits are applied](https://palantir.com/docs/foundry/object-edits/how-edits-applied/)）：
1. **数据源新 transaction**：backing datasource 有新事务时，把累计的用户编辑一并合并落盘
2. **每 6 小时**：即便数据源静止，只要队列有未落盘的编辑，触发一次合并

**关键**："6h flush"不是性能调优，而是**耐久性边界**——防止大量内存中的用户编辑随节点崩溃丢失。两次 flush 之间崩溃，理论上仍可能损失 6h 内未刷新的编辑。

### 3.5 冲突解决策略（datasource vs user edit）

官方（[Conflict resolution strategies](https://community.palantir.com/t/introducing-conflict-resolution-strategies-for-ontology-user-edits-and-datasource-updates-ga/346)）：当同一对象同时被 datasource 和 user edit 改了，需透明解决：
- **Apply user edits**（默认）：user edit 优先
- **Apply most recent value**：按时间戳比较，取最新
- **Retain user edits on conflict**：保留 user edit 但标记冲突

---

## 四、Scenario 在这个架构里落在哪一层

综合以上，Scenario 在 Palantir 架构里的定位：

```
┌─────────────────────────────────────────────────────────┐
│  Workshop / OSDK / Agent（客户端，传 scenarioRid）        │
└────────────────────────┬────────────────────────────────┘
                         │ POST /actions/{action}/apply?scenarioRid=X
                         ▼
┌─────────────────────────────────────────────────────────┐
│  Actions Service（校验 + 权限 + Submission Criteria）     │
│  → 投递 modification instruction 到 Funnel，带 scenarioRid│
└────────────────────────┬────────────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────────────┐
│  Funnel（offset-tracked 队列）                            │
│  → instruction 打上 scenarioRid 标签                      │
│  → 应用到 edit layer（S1，按 scenarioRid 分区）            │
└────────────────────────┬────────────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────────────┐
│  OSv2 Object Database（live index）                       │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐    │
│  │ base index   │  │ edit layer   │  │ edit layer   │    │
│  │ (scenarioRid │  │ (scenarioRid │  │ (scenarioRid │    │
│  │  = main)     │  │  = scn_A)    │  │  = scn_B)    │    │
│  │  datasource  │  │  Action edits│  │  Action edits│    │
│  └──────────────┘  └──────────────┘  └──────────────┘    │
│  读取时：按请求的 scenarioRid，edit layer 覆盖 base        │
└────────────────────────┬────────────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────────────┐
│  OSS（读路径，带 scenarioRid 路由到对应 edit layer）       │
└─────────────────────────────────────────────────────────┘
```

**核心结论**：
1. **Scenario 不引入新的存储引擎**——它复用 edit layer 机制，只是给 edits 打 scenarioRid 标签
2. **base index 是所有 Scenario 共享的**——Scenario 只存"相对 base 的 edits"（overlay），不复制 base
3. **读取时 OSS 按 scenarioRid 路由**：请求带 scn_A → 合并 base + scn_A 的 edits；请求带 scn_B → 合并 base + scn_B 的 edits
4. **多 Scenario 并排对比**：OSS 一次请求带多个 scenarioRid，对每个 edit layer 各做一次覆盖合并，返回多列

### 4.1 Scenario vs Foundry Branch（两个不同的"分支"）

深挖发现 Palantir 有两个易混概念：

| 概念 | 层次 | API 参数 | 状态 | 作用 |
|------|------|---------|:---:|------|
| **Scenario** | Ontology 数据层（edit layer 标签） | `scenarioRid` | Stable（Workshop Scenarios） | 运营决策 what-if 推演 |
| **Foundry Branch** | Dataset/环境层（dataset transaction 分支） | `branch` | **Experimental**（"not all workflows supported"） | 数据管道/本体 schema 隔离开发 |

官方 Apply Action API 文档明确：
> `branch`: Branches are an **experimental feature** and not all workflows are supported.
> `scenarioRid`: The resource identifier of an ontology scenario to apply the action against.

**两者关系**：Scenario 是 Workshop 级别的成熟功能（基于 edit layer）；Foundry Branch 是底层 dataset 分支（实验性）。**Scenario 不依赖 Foundry Branch**——它直接用 edit layer 的 scenarioRid 标签实现。

> **对 Gaia 的启示**：Gaia 的 `branches` 表（已存在但未接线）应定位为 **Scenario 载体**（edit layer 标签），而非 Foundry Branch 式的环境分支。这与之前的 §1.2 设计一致。

### 4.2 Scenario 不可变性的物理实现

官方（[Scenarios core concepts](https://palantir.com/docs/foundry/workshop/scenarios-concepts/)）：
> "A Scenario is immutable once created. To 'modify' a Scenario, create a new Scenario with a modified set of Actions or Models."

**物理实现**：edit layer 的某个 scenarioRid 标签一旦冻结（status=IMMUTABLE），Funnel 队列拒绝接受该 scenarioRid 的新 instruction。已存在的 edits 仍可读，但不能追加。duplicate Scenario = 复制该 scenarioRid 的所有 edits 到新 scenarioRid。

---

## 五、Palantir Scenario 的读取路径

### 5.1 读取时序

```
客户端（带 scenarioRid）
  → OSS：接收查询请求，识别 scenarioRid
  → OSS 路由：
      - 属性 filter → I1 倒排索引（base + scenario edit layer 合并视图）
      - searchAround → I2 关系图谱索引（同上合并）
      - aggregation → I3 集合代数索引（同上合并）
  → Projection Engine：按权限 + schema 物化对象视图
  → 返回
```

### 5.2 edit layer + base 合并的物理实现

edit layer 存的是"完整对象状态"（官方：edit layer records the entire object, not just changed properties）。所以合并是**对象级覆盖**，不是字段级合并：

- edit layer 有 rid X → 返回 edit layer 的 X（整个对象）
- edit layer 没有 X → 返回 base index 的 X
- edit layer 标记 X deleted → X 不可见

**这解释了 Scenario 的"累积叠加"**：Scenario 内连续 Action 改同一对象，edit layer 的该对象记录被整体覆盖更新（version 递增），不是每个 Action 一条记录。

### 5.3 读路径的一致性

官方两类语义（[List Objects API](https://palantir.com/docs/foundry/api/ontology-resources/objects/list-objects) + [How edits applied](https://palantir.com/docs/foundry/object-edits/how-edits-applied/)）：

**(a) 列表分页扫描——最终一致**：
> "This endpoint does not guarantee consistency. Changes to the data could result in missing or repeated objects in the response pages."

**(b) 单对象 read-after-write——强一致**：
> "If an object read occurring as part of an ontology query happens after a user modification is sent, the object read is guaranteed to contain the user edits."

**对 Scenario**：Scenario 内 Action 后立即查询该对象，保证看到新值（edit layer 已应用）。但扫描 Scenario 内全部对象时分页可能不严格一致。这是可接受的——what-if 场景用户不会在扫描中并发改对象。

### 5.4 多 Scenario 并排对比的读取

OSS 一次查询带多个 scenarioRid（Workshop 的 "Compare against Scenarios"）。物理实现：
- 对每个 scenarioRid 的 edit layer 各做一次 base+overlay 合并
- Projection Engine 把多个合并结果按 rid 对齐，返回多列
- **微妙规则**（官方社区澄清）：
  - `load_from`（单 Scenario）决定**对象列表**——Scenario 新建的对象只有作为 load_from 才出现
  - `compare_against`（多 Scenario）决定**并排列**——只对比属性值，不影响对象列表

---

## 六、Palantir 的可靠性与一致性保证

### 6.1 耐久性模型（两层）

| 层 | 作用 | 失败影响 |
|----|------|---------|
| **live index（S1+I1/I2/I3）** | 即时读写，ephemeral | search node 宕机 → 该节点数据不可查，需从 S3 rehydrate |
| **持久 Dataset（S3，Funnel 拥有）** | 耐久性底，定期 flush | 两次 flush 间崩溃可能损失未刷新的 edits（≤6h） |

**关键**：OSv2 的对象库索引是**临时的**，真正的耐久性来自 S3。这与传统数据库"存盘即持久"不同——Palantir 牺牲了一点耐久性（6h 窗口）换来了 live index 的高吞吐低延迟。

### 6.2 故障恢复

- **search node 宕机**：从 S3（Funnel 持久 Dataset）rehydrate 重建索引
- **Funnel 队列丢失**：offset 追踪保证已应用的 edit 不丢（已 flush 到 S3）；未 flush 的可能丢
- **Action 幂等**：客户端用 idempotency key 重试，Funnel 队列去重

### 6.3 Scenario 的可靠性

Scenario 的 edits 与普通 user edits 走同一套机制（edit layer + S3 持久化）。因此：
- Scenario edits 也有 6h flush 的耐久性窗口
- Scenario discard = 删除该 scenarioRid 的所有 edits（从 edit layer + S3）
- Scenario apply 到 main = 把该 scenarioRid 的 edits 重放为 scenarioRid=main 的新 instruction

---

## 七、Palantir 的性能特性与限制

### 7.1 性能特性

| 操作 | 特性 |
|------|------|
| 单对象 read-after-write | 即时（live index 已应用 offset） |
| 属性 filter（有索引） | 快（I1 倒排索引） |
| searchAround | 快但随集合大小消耗 compute（I2） |
| aggregation | 快但随集合大小消耗 compute（I3） |
| 列表分页扫描 | 最终一致，OSv2 无上限（OSv1 限 10000） |
| Action 写 | 18 + 1/对象 compute-seconds，即时可见 |

### 7.2 Scenario 的限制（官方明确）

| 限制 | 值 | 工程依据 |
|------|:---:|------|
| 单 Scenario edits | ≤30000 | edit layer 单 scenarioRid 的 instruction 数上限 |
| 单 Scenario Actions | ≤50 | 防止过多 Action 累积 |
| Scenario 内加载对象 | ≤10000 | 单次查询内存限制 |
| Scenario 内 Function 调用 | 受 Functions 限制 | 复杂逻辑可能超时 |

这些限制是**性能保护**，而非架构硬约束——edit layer 本身可支持更多，但为保证交互响应而设软限制。

### 7.3 增量索引（默认）

官方（[Funnel batch pipelines](https://palantir.com/docs/foundry/object-indexing/funnel-batch-pipelines/)）：OSv2 默认增量索引——只 reindex 变化的行。全量重建仅在：单次事务 >80% 行变化，或 schema 变更时触发。

---

# 第二部分：Gaia 当前代码仓建议如何实现

> 上一部分讲清了 Palantir 怎么做。这一部分讲 Gaia 在自己的技术栈（PG + Doris + Iceberg + Trino + Neo4j）里**如何用对等机制实现 Scenario**，以及性能/可靠性的具体要求。

## 八、Gaia 技术栈与 Palantir 的映射

### 8.1 架构映射

| Palantir 组件 | Gaia 对应 | 说明 |
|--------------|----------|------|
| OMS（元数据） | PostgreSQL `object_types`/`link_types`/`action_types` 表 | 已有 |
| OSv2 Object Database（live index） | **PG `object_state`（Scenario 用）+ Doris（main 用）** | Scenario 只用 PG |
| Funnel（写入编排 + offset 队列） | **ActionService + PG 事务**（无独立队列） | Gaia 用 PG 事务替代 offset 队列 |
| edit layer（覆盖在 base 上） | **PG `object_state` 的 `scenario_id` 列** | overlay 行覆盖 base 行 |
| S3 持久日志 | **PG WAL + object_state 本身**（PG 已持久） | Gaia 无 ephemeral 层 |
| OSS（读路径） | ObjectQueryService | Scenario 分支只读 PG |
| Actions Service | ActionService | 已有，加 scenario_id |

### 8.2 关键架构差异（设计依据）

| 维度 | Palantir OSv2 | Gaia | 对 Scenario 的影响 |
|------|--------------|------|------------------|
| live index 持久性 | **ephemeral**（内存/盘，靠 S3 flush） | **持久**（PG 已存盘） | Gaia 无 6h flush 窗口，更可靠 |
| 写入编排 | Funnel offset 队列（异步） | PG 事务（同步） | Gaia 写入即持久，无队列丢失风险 |
| 读后写一致 | offset 应用到 live index 后可见 | PG 事务提交后可见 | Gaia 天然强一致 |
| edit layer 存储 | 独立 KV（Cassandra 推测） | PG `object_state` 的 `scenario_id` 维度 | 复用现有表，无新引擎 |
| 查询引擎 | I1/I2/I3 专用索引 | PG JSONB GIN + B-tree | Gaia 用 PG 统一处理 |

**核心结论**：Gaia 用 **PG 单引擎**实现 Palantir 的"live index + edit layer + 持久化"三件事（因为 PG 本身就是持久的）。代价是 Gaia 的 Scenario 规模受限于单 PG 节点（但 Palantir 的 30000/10000 限制内 PG 完全够用）。

### 8.3 各引擎在 Scenario 下的角色

| 引擎 | Scenario 写入 | Scenario 查询 | 要求 |
|------|:---:|:---:|------|
| **PostgreSQL** | ✅ 唯一写入点 | ✅ 唯一查询点 | 复合主键 + JSONB GIN + 事务 |
| **Doris** | ❌ 不参与 | ❌ 不参与 | 无新增（main 路径不变） |
| **Iceberg** | ❌ 不参与 | ❌ 不参与（决策回放除外） | 无新增 |
| **Trino/Neo4j/Kafka** | ❌ 不参与 | ❌ 不参与 | 无新增 |

**为什么 Scenario 只用 PG？** 三个原因（与 Palantir edit layer 对齐）：
1. **隔离性**：Scenario 是假设世界，不能污染 Doris/Iceberg（main 的生产数据）。对应 Palantir 的 edit layer 与 base index 分离
2. **即时性**：Scenario 要求 Action 后立即可见（read-after-write）。PG 同步事务天然满足；Doris 靠 CDC 异步（秒级延迟）不行。对应 Palantir 的 offset 即时应用到 live index
3. **规模可控**：Scenario 限 30000 edits / 10000 对象查询，PG 单表轻松处理。对应 Palantir 的 Scenario 限制

---

## 九、写入路径的工程实现

### 9.1 overlay 写入的 SQL（精确到语句）

> 对应 Palantir 的 "Action 投递 instruction 到 Funnel 队列 → 应用到 edit layer"。Gaia 用 PG 事务直接写 object_state 的 scenario_id 维度。

#### CREATE_OBJECT in Scenario

```sql
-- 对应 Palantir：edit layer 新增对象记录
INSERT INTO object_state (rid, scenario_id, object_type_api_name, ontology_id, version, properties, modified_by, created_at, updated_at)
VALUES (:rid, :scenario_id, :ot_api, :ontology_id, 1, :properties, :modified_by, now(), now())
ON CONFLICT (rid, scenario_id) DO NOTHING;
-- ON CONFLICT = OCC 的 CREATE 语义（并发同对象 CREATE 不报错，rowcount=0 → 调用方判 conflict）
```

**要点**：复合主键 `(rid, scenario_id)` 让同一对象在 main（scenario_id=NULL）和不同 Scenario 各有独立行——对应 Palantir 的 edit layer 按 scenarioRid 分区。

#### UPDATE_PROPERTY in Scenario（累积叠加，最复杂）

> 对应 Palantir：edit layer 的对象记录被整体覆盖更新（version 递增）。Palantir edit layer 存完整对象，Gaia 的 overlay 行也存合并后的完整 properties。

```sql
-- 1. 读 base 行 version（OCC 校验基底，对应 Palantir 的 expected_version 语义）
SELECT version, properties FROM object_state
WHERE rid = :rid AND scenario_id IS NULL;

-- 2. 读当前 overlay 行（若存在，用于累积合并）
SELECT version, properties FROM object_state
WHERE rid = :rid AND scenario_id = :scenario_id;

-- 3a. 首次 UPDATE（无 overlay 行）：以 base properties 为基底，INSERT overlay
INSERT INTO object_state (rid, scenario_id, object_type_api_name, ontology_id, version, properties, modified_by, created_at, updated_at)
VALUES (:rid, :scenario_id, :ot_api, :ontology_id, 1,
        :base_properties || :changed_properties,  -- PG JSONB || 浅合并
        :modified_by, now(), now());

-- 3b. 后续 UPDATE（有 overlay 行）：以 overlay properties 为基底，UPDATE overlay
UPDATE object_state
SET properties = properties || :changed_properties,  -- 在现有 overlay 上覆盖
    version = version + 1,
    modified_by = :modified_by,
    updated_at = now()
WHERE rid = :rid AND scenario_id = :scenario_id
  AND version = :current_overlay_version;  -- overlay 行 OCC
```

**关键工程细节**：

1. **JSONB `||` 合并**：PG 的 `jsonb || jsonb` 做顶层键合并（后者覆盖前者）。对应 Palantir edit layer 的"整体覆盖"——但 Gaia 用 `||` 做字段级覆盖更精细。**注意**：`||` 是浅合并，嵌套对象整体替换。若需深度合并要用 `jsonb_set`（MVP 用浅合并，标注限制）

2. **两层 OCC**（对应 Palantir 的 offset 顺序保证）：
   - **base 层 OCC**：Step 1 校验 base version = expected_version。不匹配 → ConflictError（"你基于的 base 过期了"）
   - **overlay 层 OCC**：Step 3b 的 `WHERE version = X` 防同 Scenario 并发冲突。对应 Palantir 的 offset 队列顺序

3. **非原子性窗口**：Step 1-3 是三条独立 SQL。并发下两 Action 同读 base v5，都校验通过——但 overlay 层 OCC 保证只有一个成功（后者 WHERE version 失败）。对应 Palantir 的 offset 串行化

4. **性能**：单对象 = 2 SELECT + 1 INSERT/UPDATE，全走索引，< 2ms。50 Action × 10 对象 = 500 次写入 < 1s

#### DELETE_OBJECT in Scenario（软删除）

```sql
-- 对应 Palantir：edit layer 标记对象 deleted，读取时不可见
INSERT INTO object_state (rid, scenario_id, ..., properties, ...)
VALUES (:rid, :scenario_id, ...,
        jsonb_build_object('__deleted', true, '__deleted_at', now_text), ...)
ON CONFLICT (rid, scenario_id) DO UPDATE
SET properties = object_state.properties || jsonb_build_object('__deleted', true, ...),
    version = object_state.version + 1, updated_at = now();
```

**为什么软删除**：Palantir edit layer 用"标记 deleted"区分"base 有但 Scenario 删了"vs"base 没有"。Gaia 同理——真删 overlay 行会让查询无法区分，误显示 base 的对象。

### 9.2 写入事务边界

```python
# 对应 Palantir：Actions Service 校验 → Funnel 队列 → 应用 live index（原子）
# Gaia 用 PG transaction() 上下文器统一提交
async with self._metadata.transaction():  # PG 事务单元
    for mutation in mutations:
        await self._metadata.upsert_object_state_scenario(...)  # Step 8
    execution = await self._metadata.create_execution_log(scenario_id=..., ...)  # Step 9
    # Step 9.5 决策物化（若启用）
    # transaction() 退出时自动 commit
```

**事务保证**：所有 overlay + execution_log + outbox + Decision 同一 PG 事务，原子提交。复用现有 `transaction()` 上下文器（`postgres_meta_store.py:2069`），零改造。

---

## 十、读取路径的工程实现

### 10.1 单对象读取（base + overlay 合并）

> 对应 Palantir：OSS 按 scenarioRid 路由，edit layer 覆盖 base index。

```sql
SELECT
  COALESCE(s.properties, b.properties) AS properties,
  CASE
    WHEN s.rid IS NOT NULL AND (s.properties->>'__deleted')::bool THEN 'DELETED'
    WHEN s.rid IS NOT NULL AND b.rid IS NULL THEN 'CREATED'
    WHEN s.rid IS NOT NULL THEN 'UPDATED'
    ELSE 'BASE'
  END AS source
FROM object_state b
LEFT JOIN object_state s
  ON s.rid = b.rid AND s.scenario_id = :scenario_id
WHERE b.rid = :rid AND b.scenario_id IS NULL
  AND (s.properties->>'__deleted' IS NULL OR (s.properties->>'__deleted')::bool = false)
UNION ALL
-- Scenario 新建对象（base 没有）
SELECT s.properties, s.version, 'CREATED' FROM object_state s
WHERE s.scenario_id = :scenario_id AND s.rid = :rid
  AND (NOT EXISTS (SELECT 1 FROM object_state b WHERE b.rid = s.rid AND b.scenario_id IS NULL))
  AND (s.properties->>'__deleted' IS NULL OR (s.properties->>'__deleted')::bool = false);
```

**执行计划**：base 行 + scenario 行各走复合 PK 索引，两行精确查找，< 1ms。

### 10.2 批量 filter（Scenario 查询主路径）

> 对应 Palantir：OSS 在 base+edit layer 合并视图上做 filter（走 I1 倒排索引）。Gaia 用 PG JSONB GIN 索引。

```sql
WITH base_objs AS (
  SELECT b.rid, COALESCE(s.properties, b.properties) AS properties,
    CASE WHEN s.rid IS NOT NULL AND (s.properties->>'__deleted')::bool THEN 'DELETED'
         WHEN s.rid IS NOT NULL THEN 'UPDATED' ELSE 'BASE' END AS source
  FROM object_state b
  LEFT JOIN object_state s ON s.rid = b.rid AND s.scenario_id = :scenario_id
  WHERE b.ontology_id = :ontology_id AND b.object_type_api_name = :ot_api AND b.scenario_id IS NULL
),
scenario_created AS (
  SELECT s.rid, s.properties, 'CREATED' FROM object_state s
  WHERE s.scenario_id = :scenario_id AND s.ontology_id = :ontology_id AND s.object_type_api_name = :ot_api
    AND (s.properties->>'__deleted' IS NULL OR (s.properties->>'__deleted')::bool = false)
    AND NOT EXISTS (SELECT 1 FROM object_state b WHERE b.rid = s.rid AND b.scenario_id IS NULL)
),
merged AS (
  SELECT * FROM base_objs WHERE source != 'DELETED'
  UNION ALL SELECT * FROM scenario_created
)
SELECT rid, properties, source FROM merged
WHERE properties @> :filter_jsonb  -- GIN 索引加速
ORDER BY rid LIMIT :limit OFFSET :offset;
```

### 10.3 多 Scenario 并排对比

> 对应 Palantir：OSS 一次查询带多 scenarioRid，Projection Engine 对齐多列。

```sql
WITH base AS (
  SELECT rid, properties FROM object_state
  WHERE ontology_id = :ontology_id AND object_type_api_name = :ot_api AND scenario_id IS NULL
),
scn_a AS (SELECT rid, properties FROM object_state WHERE scenario_id = :scn_a
  AND (properties->>'__deleted' IS NULL OR (properties->>'__deleted')::bool = false)),
scn_b AS (SELECT rid, properties FROM object_state WHERE scenario_id = :scn_b
  AND (properties->>'__deleted' IS NULL OR (properties->>'__deleted')::bool = false))
SELECT b.rid, b.properties AS base_props, a.properties AS scn_a_props, c.properties AS scn_b_props
FROM base b
LEFT JOIN scn_a a ON a.rid = b.rid
LEFT JOIN scn_b c ON c.rid = b.rid
WHERE b.properties @> :filter_jsonb
  AND (a.rid IS NOT NULL OR c.rid IS NOT NULL)  -- 至少一个 Scenario 有改动
LIMIT :limit;
```

---

## 十一、索引设计与查询计划

### 11.1 必需索引（migration 必建）

```sql
-- 1. 复合主键（overlay 行与 base 行共存的基础）
ALTER TABLE object_state DROP CONSTRAINT object_state_pkey;
ALTER TABLE object_state ADD PRIMARY KEY (rid, scenario_id);

-- 2. 按类型 + scenario 查询（批量查询主路径）—— 部分索引分离 base/overlay
CREATE INDEX ix_object_state_type_base ON object_state (object_type_api_name) WHERE scenario_id IS NULL;
CREATE INDEX ix_object_state_type_scenario ON object_state (object_type_api_name, scenario_id) WHERE scenario_id IS NOT NULL;

-- 3. JSONB GIN 索引（filter 性能关键！）
CREATE INDEX ix_object_state_properties_gin ON object_state USING GIN (properties jsonb_path_ops);
-- jsonb_path_ops 比默认 jsonb_ops 小 30%，只支持 @> 但 Scenario filter 够用

-- 4. 高频 filter 字段表达式索引（按需）
CREATE INDEX ix_object_state_status ON object_state ((properties->>'status')) WHERE scenario_id IS NOT NULL;

-- 5. object_links 的 scenario 索引
CREATE INDEX ix_object_links_scenario ON object_links (scenario_id, link_type_api_name, source_rid) WHERE scenario_id IS NOT NULL;
```

### 11.2 filter 操作符到 JSONB 的映射（核心工程难点）

> 对应 Palantir I1 倒排索引支持各种 filter。Gaia 需把现有 `_filter_dict_to_sql` 的操作符映射到 PG JSONB。

| 操作符 | PG JSONB 表达 | GIN 可加速 |
|--------|--------------|:---:|
| eq | `properties @> '{"status":"DELAYED"}'` | ✅ |
| isNull | `NOT properties ? 'field'` | ✅（`?` 用 GIN） |
| gt/lt/gte/lte | `(properties->>'cost')::numeric > 1000` | ❌（表达式索引可） |
| in/notIn | `properties->>'status' IN (...)` | ❌（表达式索引可） |
| contains/startsWith | `properties->>'name' LIKE '%kw%'` | ❌（pg_trgm 可） |
| and/or/not | SQL AND/OR/NOT 组合 | 视子操作符 |

**工程决策**：eq/isNull 优先用 `@>`/`?`（GIN 加速）；其余用 `->>` 提取（可能顺序扫，但 10000 行内 < 100ms 可接受）；高频字段建表达式索引。

### 11.3 查询计划分析

预期 `list_objects_with_overlay`（10000 base + 50 overlay）：
- base 扫描：`ix_object_state_type_base` Index Scan，10000 行 ~10ms
- scenario 扫描：`ix_object_state_type_scenario` Bitmap Index Scan，50 行 ~1ms
- Hash Join + filter + Sort + Limit ~5ms
- **总计 < 20ms**，满足交互体验

---

## 十二、事务与并发控制

### 12.1 事务隔离级别

Gaia PG 用默认 **Read Committed**。

**对 Scenario 的影响**：
- Scenario 内连续 Action 是**独立事务**，Action 2 能看到 Action 1 的 overlay（已提交）——这正是"累积叠加"需要的行为
- 对应 Palantir：offset 串行应用，后一个 instruction 看到前一个的效果

### 12.2 并发冲突场景

| 场景 | 机制 | 对应 Palantir |
|------|------|--------------|
| 两用户同改同 Scenario 同对象 | overlay 行 OCC（`WHERE version=X`） | offset 串行化 |
| 两用户同改不同 Scenario 同 base 对象 | 各写各自 overlay，互不影响 | edit layer 按 scenarioRid 隔离 |
| Scenario Action 执行中 base 被改 | base 行 OCC 校验 | expected_version 语义 |
| apply_scenario 时 base 已变 | 逐个 Action 重放，main OCC 校验 | 重放冲突 |

### 12.3 死锁防护

PG UPDATE 自动行级锁。若 Action 1 改 A→B，Action 2 改 B→A（相反顺序），可能死锁。**缓解**：ActionService 内部对 mutations 按 rid 排序后写入，保证加锁顺序一致。对应 Palantir offset 队列天然串行无死锁。

---

## 十三、可靠性与故障恢复

### 13.1 Gaia vs Palantir 的可靠性对比

| 维度 | Palantir OSv2 | Gaia | Gaia 优势 |
|------|--------------|------|----------|
| live index 持久性 | ephemeral（靠 S3 flush） | **PG 已存盘** | 无 6h flush 窗口 |
| 写入即持久 | 否（队列 + 异步 flush） | **是（PG 事务提交即持久）** | 无队列丢失风险 |
| 读后写一致 | offset 应用后可见 | **事务提交后可见** | 天然强一致 |
| 故障恢复 | S3 rehydrate | **PG WAL 重放** | 成熟机制 |

**关键**：Gaia 用 PG 单引擎，省去了 Palantir 的"ephemeral index + S3 持久化"两层，可靠性反而更高（无 6h 窗口）。代价是规模受限于单 PG 节点。

### 13.2 故障场景与恢复

| 故障 | 影响 | 恢复 |
|------|------|------|
| PG 宕机 | Scenario 读写全不可用 | PG 重启 + WAL 重放，数据不丢 |
| Action 写 overlay 后 crash（未 commit） | overlay 未写入 | 事务原子性：未 commit 丢失，Scenario 一致 |
| Action 写 overlay 后 crash（已 commit 未响应） | overlay 已写入，客户端不知道 | idempotency_key 重试，`ON CONFLICT` 不重复 |
| apply_scenario 中断 | 部分 Action 已重放 | idempotency_key 防重复，可重试 |
| Scenario discard crash | overlay 部分删除 | CASCADE 删除原子（`DELETE FROM branches WHERE id=X`） |

### 13.3 与 ConflictDetector 的关系

现有 `ConflictDetector` 审计 PG object_state vs Iceberg 版本一致性。**Scenario 行不参与**（本就不该同步 Iceberg）。改造：`get_object_states_by_type` 加 `WHERE scenario_id IS NULL`，否则 Scenario overlay 行误报不一致。

### 13.4 outbox 在 Scenario 下的行为

对应 Palantir：Scenario 内 Action 不应触发外部副作用（假设世界不能影响真实系统）。所有 effect 类型（WEBHOOK_WRITEBACK/SIDE_EFFECT/WRITE_BACK/SUB_ACTION/KAFKA_TOPIC）在 `scenario_id is not None` 时跳过创建。

---

## 十四、性能边界与容量规划

### 14.1 性能基线（估算，需实测）

| 操作 | 数据量 | 预期延迟 | 对应 Palantir |
|------|--------|---------|--------------|
| 单对象 overlay 写 | - | < 2ms | offset 应用到 live index |
| 单对象 overlay 读 | - | < 1ms | OSS 单对象 read-after-write |
| 批量 filter（有 GIN） | 10000 base + 50 overlay | < 20ms | OSS Base Query（2 compute-sec） |
| 批量 filter（无索引） | 10000 base | 50-100ms | 顺序扫，可接受 |
| 聚合 SUM | 10000 对象 | < 100ms | OSS Aggregation（5 compute-sec） |
| 多 Scenario 并排 | 2 Scenario × 50 overlay | < 10ms | OSS 多 scenarioRid 合并 |
| apply_scenario | 50 Action | < 250ms | 逐个 Action 重放 |

### 14.2 Palantir 限制的 Gaia 对应

| Palantir 限制 | Gaia 对应 | 工程依据 |
|--------------|----------|---------|
| ≤30000 edits/Scenario | 同 | PG 单表 30000 行 overlay，查询 < 500ms |
| ≤50 Actions/Scenario | 同 | 50 Action 串行 < 1s，apply < 250ms |
| ≤10000 对象查询 | 同 | PG 10000 行扫描 + JSONB 解析 < 100ms |

### 14.3 容量规划

- 单本体 100 Scenario × 50 overlay = 5000 行 + base 10000 = 15000 行，PG 轻松
- GIN 索引约表大小 30%，150000 行 × 1KB × 30% ≈ 45MB
- 扩容信号：单 Scenario 查询 > 500ms → 加表达式索引；object_state > 100 万行 → 按 ontology_id 分区

---

## 十五、对底层引擎的要求清单

### 15.1 PostgreSQL 要求（Scenario 唯一依赖）

| # | 要求 | 用途 | 对应 Palantir | 必须 |
|---|------|------|--------------|:---:|
| 1 | 复合主键 `(rid, scenario_id)` | overlay 与 base 共存 | edit layer 按 scenarioRid 分区 | ✅ |
| 2 | JSONB 类型 + `||` 合并 | overlay 累积叠加 | edit layer 整体覆盖 | ✅ |
| 3 | JSONB GIN 索引（`jsonb_path_ops`） | filter 性能 | I1 倒排索引 | ✅ |
| 4 | JSONB 表达式索引 | 高频 filter 加速 | I1 专用索引 | 🟡 |
| 5 | 部分索引 `WHERE scenario_id IS [NOT] NULL` | 分离 base/overlay | edit layer 与 base 分离 | ✅ |
| 6 | ON CONFLICT 子句 | OCC CREATE 语义 | offset 去重 | ✅ |
| 7 | 事务 + 行级锁 | 原子性 + 并发 | Funnel offset 串行 | ✅ |
| 8 | CASCADE 外键 | discard 清理 | Scenario 删除 | ✅ |
| 9 | CTE（WITH） | 复杂合并查询 | OSS 查询编排 | 🟡 |
| 10 | PG 14+（`||` JSONB 稳定） | JSONB 成熟 | - | ✅（Gaia 用 PG 16） |

### 15.2 其他引擎要求

**无新增要求**。Doris/Iceberg/Trino/Neo4j/Kafka 在 Scenario 路径中完全不参与，现有 main 路径不变。

### 15.3 连接池

Scenario 查询全走 PG，复用现有 `PostgresMetaStore` AsyncSession。批量查询（10000 对象）可能占连接 100ms，需确保连接池够大。现有 async 模式已保证不阻塞。

---

## 十六、参考

### Palantir 官方（第一手）
- Object backend overview（微服务架构）: https://palantir.com/docs/foundry/object-backend/overview/
- How user edits are applied（edit layer + offset + 持久化）: https://palantir.com/docs/foundry/object-edits/how-edits-applied/
- Funnel batch pipelines（四阶段索引）: https://palantir.com/docs/foundry/object-indexing/funnel-batch-pipelines/
- OSv1 vs OSv2 breaking changes: https://palantir.com/docs/foundry/object-backend/object-storage-v2-breaking-changes/
- Object indexing overview: https://palantir.com/docs/foundry/object-indexing/overview/
- Indexing FAQ: https://palantir.com/docs/foundry/object-indexing/faq/
- Materializations: https://palantir.com/docs/foundry/object-edits/materializations/
- Conflict resolution strategies: https://community.palantir.com/t/introducing-conflict-resolution-strategies-for-ontology-user-edits-and-datasource-updates-ga/346
- Apply Action API（scenarioRid 参数）: https://palantir.com/docs/foundry/api/ontologies-v2-resources/actions/apply-action/
- Scenario core concepts: https://palantir.com/docs/foundry/workshop/scenarios-concepts/
- Object edits conflict resolution（edit layer 揭示）: https://community.palantir.com/t/object-edits-conflict-resolution-strategy-not-working-as-expected/6479
- On dataset versioning: https://blog.palantir.com/on-dataset-versioning-in-palantir-foundry-8f23de22cc4c

### 第三方深度拆解
- Palantir Ontology 存储结构与读写机制原理深入剖析: https://jishuzhan.net/article/2058456659199574018
- Palantir Ontology: Architecture & Benefits（puppygraph）: https://www.puppygraph.com/blog/palantir-ontology
- Inside FDP part 3（Computer Weekly）: https://www.computerweekly.com/opinion/Inside-FDP-part-3-The-data-architecture-that-makes-it-work

### Gaia 内部代码
- `src/ontology/layers/metadata/postgres_meta_store.py:1171` — `upsert_object_state`（OCC 原型）
- `src/ontology/layers/metadata/postgres_meta_store.py:2069` — `transaction()` 上下文器
- `src/ontology/services/action_service.py:385` — `execute_action` Step 1-12
- `src/ontology/services/object_query_service.py:488` — `_resolve_query_target`（路由）
- `src/ontology/services/conflict_detector.py` — 一致性审计（Scenario 需过滤）
- `src/ontology/core/models/ontology.py:315` — `BranchModel`（已存在未接线）
- `src/ontology/core/models/ontology.py:400` — `ObjectStateModel`（加 scenario_id）
