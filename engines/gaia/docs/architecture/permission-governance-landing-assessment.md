# Gaia 权限治理特性 —— 落地评估报告

> **用途**：本文是 Gaia 项目落地"权限与隔离体系"大特性的**评估报告**，连接前期研究（[`palantir-permission-review-and-industry-comparison.md`](../research/palantir-permission-review-and-industry-comparison.md)）与后续详细设计（ADR + 设计文档）。
> **评估方法**：基于 Gaia 真实代码核查（101 个后端 .py 源文件 / 27,756 行）+ Palantir/业界模型引入映射 + Scenario/Action 等已就绪设计的协同分析。
> **评估日期**：2026-07-08
> **关联文档**：
> - 研究：[`palantir-permission-isolation-reference.md`](../research/palantir-permission-isolation-reference.md)（材料归档）· [`palantir-permission-review-and-industry-comparison.md`](../research/palantir-permission-review-and-industry-comparison.md)（评审+业界对照+简化哲学）· [`palantir-capability-gap-analysis.md`](../research/palantir-capability-gap-analysis.md) §三 P0-B
> - 现状：[`implementation-status.md`](./implementation-status.md) 路标 #4 · [`adr-011-action-p1.md`](./adr-011-action-p1.md)（Action 三层权限雏形）
> - 协同：[`scenario-and-decision-exhaust-design.md`](../design/scenario-and-decision-exhaust-design.md)（object_state 加 scenario_id，权限需协同）
>
> **⚠️ 本文是评估报告，不是最终设计**。落地决策需经评审后写入 ADR-016（权限治理体系）+ 配套设计文档。本文给出的是**评估结论、引入方案、分期路线、待决策问题**，为评审提供依据。

---

## 目录

- [一、Gaia 现状核查（真实代码基线）](#一gaia-现状核查真实代码基线)
- [二、Palantir/业界模型 → Gaia 引入映射](#二palantir业界模型--gaia-引入映射)
- [三、核心架构决策（评估建议）](#三核心架构决策评估建议)
- [四、分期实施路线（评估建议）](#四分期实施路线评估建议)
- [五、数据模型设计草案（评估建议）](#五数据模型设计草案评估建议)
- [六、查询层权限下推方案（评估建议）](#六查询层权限下推方案评估建议)
- [七、与 Scenario / Action / 工具层的协同](#七与-scenario--action--工具层的协同)
- [八、简化设计落地（"复杂留给自己，简单留给用户"）](#八简化设计落地复杂留给自己简单留给用户)
- [九、风险、依赖与未决问题](#九风险依赖与未决问题)
- [十、评估结论与下一步](#十评估结论与下一步)

---

## 一、Gaia 现状核查（真实代码基线）

> 基于真实代码核查，非文档转述。本节是引入映射的事实基线。

### 1.1 身份层：完全缺失（principal=anonymous）

| 项 | 现状 | 代码位置 |
|----|------|---------|
| Principal 模型 | ❌ 无 | — |
| User ORM 表 | ❌ 无 | `core/models/` 仅有 ontology.py + datasource.py |
| Group ORM 表 | ❌ 无 | — |
| Service User | ❌ 无 | — |
| 认证中间件 | ❌ 无 | `middleware/` 仅 TraceID + error_handler |
| Principal 来源 | `ActionContext.current_user="anonymous"` + routes 从 `X-User-Id`/`X-User-Roles` 请求头读 | `routes/action/__init__.py:44-51` |
| OIDC/LDAP 对接 | ❌ 无 | — |

**关键代码**：
```python
# routes/action/__init__.py:44-51 — MVP principal 来源
user = request.headers.get("X-User-Id", "anonymous")
roles_header = request.headers.get("X-User-Roles", "")
# ActionContext(current_user=user, workspace_id=workspace, user_roles=roles)
```

**评估**：身份层是权限体系的**前置依赖**，必须先建。但 Gaia 是开源本地优先，不必照搬 Palantir 的 Organization 多租户层——可降级为单租户 + Principal + Group。

### 1.2 权限层：仅 Action 三层权限雏形（ADR-011）

| 项 | 现状 | 代码位置 |
|----|------|---------|
| ActionAuthorizer | ✅ 三层权限（执行/行级写/参数级） | `services/action_auth.py` |
| 权限配置存储 | `ActionType.parameters.permissions` JSONB（roles/condition/sensitive_params） | `action_auth.py:_extract_permissions` |
| ObjectType 级权限 | ❌ 无 | — |
| Property 级权限 | ❌ 无 | — |
| Marking/Tag 体系 | ❌ 无 | — |
| check_access | fail-open permissive（Gravitino RBAC 未接线） | `layers/catalog/gravitino_registry.py:275` |
| visibility 字段 | ObjectType 有 `visibility` 字段（default="NORMAL"）但**未使用** | `core/models/ontology.py:70` |

**关键代码**（ActionAuthorizer 三层）：
```python
# services/action_auth.py — Layer 1/2/3
# Layer 1: roles allowlist + dynamic condition (simpleeval)
# Layer 2: catalog.check_access type-level, per-object no-op (Sprint 3)
# Layer 3: sensitive_params role whitelist
```

**评估**：ADR-011 的设计已为权限体系预留了**契约**（三层返回 forbidden set），Sprint 3 替换 internals 不影响调用方。但 ObjectType/Property 级权限完全空白，是主要建设内容。

### 1.3 查询层：完全不感知 principal（权限下推的关键缺口）

| 项 | 现状 | 代码位置 |
|----|------|---------|
| ObjectQueryService | ✅ Doris 主 / Trino 降级，filter/load/aggregate | `services/object_query_service.py` |
| 查询入口感知 principal | ❌ 无 | `_resolve_query_target` 无权限参数 |
| filter SQL 注入权限条件 | ❌ 无 | `_filter_dict_to_sql` 仅业务 filter |
| Doris 行级策略下推 | ❌ 未用（Doris 4.0.5 原生支持 Row Policy/Column Masking） | `layers/index/doris_index_store.py` |
| Trino Ranger 接入 | ❌ 未用 | `layers/engine/trino_query_engine.py` |
| PG RLS | ❌ 未用 | — |

**评估**：查询层是权限下推的**核心改造区**。业界共识是「权限下推到存储层」（避免应用层过滤被绕过），Gaia 多引擎（PG/Doris/Iceberg/Trino/Neo4j）需分别适配。

### 1.4 模型层：资源平铺无归属，BranchModel/ObjectTypeGroup 空壳未接线

| 项 | 现状 | 代码位置 |
|----|------|---------|
| OntologyModel | ✅ 顶层实体，无父级 FK（平铺，不归属任何容器） | `core/models/ontology.py:28` |
| ObjectTypeModel | ✅ 归属 Ontology（ontology_id FK），无 project_id | `core/models/ontology.py:57` |
| ActionTypeModel / LinkTypeModel / InterfaceTypeModel / SharedPropertyModel | ✅ 均归属 Ontology，无 project_id | `core/models/ontology.py:153/120/...` |
| DataSourceModel / DatasetGovernanceModel / SyncTaskModel / CredentialModel | ✅ 平铺，**无 space_id / project_id 归属** | `core/models/datasource.py:49/106/74/...` |
| ObjectTypeGroupModel | ✅ 空壳（ORM + schema + meta_store 有，无 Service/Route，前端未用），注释"Palantir ObjectTypeGroup equivalent" | `core/models/ontology.py:298` |
| BranchModel | ✅ 空壳已建表（branches），`PostgresMetaStore.create_branch` 已写，无 Service/Route | `core/models/ontology.py:314` |
| object_state | ✅ OCC（version + properties JSONB），无 scenario_id | `core/models/ontology.py:400` |
| Organization / Space / Project 实体 | ❌ 完全无 | — |
| Marking 字段 | ❌ ObjectType/Property 无 marking/tag 字段 | — |
| 权限相关 ORM 表 | ❌ 无（principal/group/role/policy/marking 全无） | — |

**评估**：
- **资源平铺无归属**是引入三层（Organization/Space/Project）的主要改造点——所有资源（Ontology/DataSource/Dataset/SyncTask/Credential）需加归属字段，是大规模 schema 变更
- **ObjectTypeGroup 空壳**与 Project 正交（语义分组 vs 权限边界），引入 Project 不冲突，ObjectTypeGroup 保留未来接线
- **BranchModel 空壳**是 Scenario 载体（scenario-*.md 已设计 object_state 加 scenario_id），与权限正交
- **定义类资源（ObjectType/ActionType 等）已有 ontology_id**，选项 B 一期归属 Ontology 不需改 schema，只需预留 `project_id`（nullable）
- 权限需要新建一组 ORM 表（见 §五）

### 1.5 引擎层权限能力（下推可行性）

| 引擎 | 原生权限能力 | Gaia 用途 | 下推可行性 |
|------|-------------|----------|:---:|
| **Doris 4.0.5** | Row Policy / Column Permission / Data Masking | 在线读主源（ObjectType 全量属性） | ✅ 高（[官方文档](https://doris.apache.org/docs/4.x/admin-manual/auth/authorization/data/)） |
| **PostgreSQL 16** | Row Level Security (RLS) + Column Grants | 元数据 + object_state + outbox | ✅ 高（成熟） |
| **Trino 478** | Ranger 插件 / System access control | 联邦查询（VIRTUAL + 降级） | 🟡 中（需部署 Ranger 或写 system access control） |
| **Iceberg** | 无原生（依赖引擎） | 全量明细（写入入口，非查询主源） | ⚪ 不直接下推 |
| **Neo4j 5** | 角色权限模型 | 图遍历（searchAround/find_paths） | 🟡 中（可在 Cypher 层过滤） |

**评估**：Doris + PG 是权限下推的主力（覆盖场景 2 托管查询 + Action 写入），Trino 谓词下推到 connector，Neo4j 用 Cypher WHERE。无需全引擎统一方案，可分层适配。

### 1.6 中间件 / Route 层

| 项 | 现状 |
|----|------|
| 中间件 | TraceID + error_handler，**无认证中间件** |
| Route 层 | 6 个路由组（ontology/query/objects/action/datasource/ai），均无权限校验 |
| 认证切入点 | 需新增 AuthMiddleware，从请求提取 Principal 注入 context |

### 1.7 工具层（ADR-009，Agent 消费者）

| 项 | 现状 |
|----|------|
| ToolExecutor | ✅ 治理切面，MVP 仅审计（principal=anonymous） |
| 22 工具 8 toolset | ✅ MCP/AG-UI/REST 三入口 |
| 权限校验 | ❌ `tools/executor.py` 明确注释 "until Sprint 3" |

**评估**：工具层是 Agent 消费权限的入口，权限体系须覆盖（Agent 以 Principal 身份执行）。

---

## 二、Palantir/业界模型 → Gaia 引入映射

> 基于 [`palantir-permission-review-and-industry-comparison.md`](../research/palantir-permission-review-and-industry-comparison.md) 的研究结论，将 Palantir 五层隔离模型**完整引入** Gaia。做正确的事，不逃避架构复杂度——上三层（Organization / Space / Project）完整建设，对齐 Palantir 企业级安全模型。

### 2.1 Palantir 上三层概念释义（引入前必读）

> Palantir 五层隔离模型的上三层（Organization / Space / Project）是其**企业级多租户 SaaS**定位的产物，概念较重。引入前先讲清三者的确切含义、Palantir 为何需要、Gaia 为何也要完整引入。

#### Organization（组织）—— 主体身份的强隔离边界

**一句话**：管"这是哪家公司/哪个主体的人"，做**租户级硬隔离**。

- **本质**：系统级强制访问控制（MAC）边界 + 身份主权域。每个 Organization 对应一个系统级内置标记，所有归属该组织的用户和资源自动绑定，用户/资源不可手动移除
- **核心规则**：每用户唯一主组织（Home Org，永久不可变），可作为 Guest 加入多个外部组织；组织间数据/用户/权限完全隔离，跨组织访问必须显式配置共享空间
- **Palantir 为何需要**：作为企业 SaaS，多主体（集团子公司/供应商/客户/外包）共享一套平台，必须从主体层面强隔离——内部员工主组织、供应商独立外部组织、客户独立客户组织、法务并购等绝密业务独立子组织
- **典型场景**：集团子公司隔离、甲乙双方数据隔离、外包员工权限隔离、B2B 联合分析（双方私有组织 + 共建共享空间）

#### Space（空间，原名 Namespace）—— 业务域容器 + 本体生命周期载体

**一句话**：管"这是哪个业务域的数据"，做**业务域级语义隔离 + 跨组织协作的合法通道**。

- **本质**：Organization 之下、Project 之上的中间层容器。核心特点是**与 Ontology 一一强绑定、同生命周期**：创建 Space 自动建同名本体，Space 删除本体永久删除不可迁移，跨 Space 不能复用本体对象
- **核心规则**：Space 绑定 Organization 白名单（只有白名单内组织的用户能访问）；Space 绑定基础设施（存储/计算/加密/计费，创建后不可改）；Space 内所有 Project 共享一套本体 + 一套角色集
- **Palantir 为何需要**：一个 enrollment 要管多个业务域，每域一套本体且语义必须统一（防止同业务对象多套定义的数据孤岛）；同时需要跨组织协作的合法载体（共享空间双方加白名单）
- **典型场景**：财务空间（财务本体）、供应链空间（供应链本体）、跨公司协作的共享空间（双方组织都加白名单，仅放加工后最小必要数据）

#### Project（项目）—— 协作安全边界（Palantir 的核心层）

**一句话**：管"哪个协作单元"，做**自主访问控制（DAC）的最小单元 + 权限继承单位**。

- **本质**：官方定义"Projects are the primary security boundary in Foundry"。项目负责人可自主分配成员角色；权限全量继承到项目内所有资源（数据集/代码/应用/本体），项目内不可阻断继承
- **核心规则**：**代码数据同栖**（Transform 代码与输出数据集必须同 Project，跨项目写入内核拦截）；跨项目复用只读 Reference（二次校验源项目权限）；四角色 Owner/Editor/Viewer/Discoverer
- **Palantir 为何需要**：依赖 Spark Transform 体系——数据转换逻辑与产出数据必须同边界，保证权限边界与数据生产边界对齐，数据出项目必须显式引用可审计

#### 三者在权限校验中的位置（自上而下串行）

```
Organization（谁的人）   ← 主体隔离（MAC）
    ↓ 约束
Space（哪个业务域）       ← 业务域容器 + 本体绑定
    ↓ 约束
Project（哪个协作单元）   ← 安全边界（DAC，核心层）
    ↓ 约束
Marking（数据多机密）     ← 数据强制兜底（MAC）
    ↓ 约束
行/列级（能看到哪些行）   ← 千人千面
```

#### Gaia 为何要完整引入这三层？（决策依据）

| Palantir 层 | Gaia 引入决策 | 引入理由 |
|------------|:---:|------|
| Organization | ✅ **完整引入** | 即使 Gaia 开源本地优先，主体隔离能力是企业落地的硬门槛。集团多子公司、供应商、客户同平台场景需要租户级强隔离。引入 Organization 的 MAC 机制（系统标记 + 主体强隔离），为多主体协作提供安全基座。单租户部署时默认一个主 Organization，不影响易用性 |
| Space | ✅ **完整引入** | Space↔Ontology 1:1 强绑定保证业务域语义统一（防止同业务对象多套定义的数据孤岛），这是 Palantir 核心设计哲学。Gaia 当前 Ontology 平铺无层级，引入 Space 提供业务域容器 + 跨组织协作通道。**基础设施绑定（Spark/存储/加密/计费）舍弃**——Gaia 无 Spark，存储多引擎统一管 |
| Project | ✅ **完整引入（新增实体）** | Project 是协作权限边界（权限原子单位），**不能用 Ontology 顶替**（Ontology 是业务语义模型，Project 是协作单元，两者正交）。Gaia 当前缺这层，需新建 Project 实体作为资源归属 + 权限继承单位。Ontology/ObjectTypeGroup 留在 Ontology 内（语义层），Project 管协作权限 |

**关键判断**：做正确的事，不逃避。完整引入三层对齐 Palantir 企业级安全模型，为 Gaia 企业落地提供与 Palantir 同等的安全基座。单租户场景下默认一个 Organization + 一个 Space，不增加用户认知负担（渐进式披露：默认视图不暴露三层管理）。**Ontology 仍为业务语义核心，但权限边界由 Project 承载**，两者职责分离（Palantir 范式）。

### 2.2 五层隔离模型引入

| Palantir 层 | Gaia 引入 | 关键调整 |
|------------|:---:|------|
| **Organization（主体隔离）** | ✅ **完整引入** | 系统级 MAC 标记 + 主体强隔离。单租户部署默认一个主 Organization；多主体场景支持子公司/供应商/客户独立 Organization。**Organizations 之间完全隔离，跨组织协作走共享 Space** |
| **Space（业务域+本体绑定）** | ✅ **完整引入** | **Space↔Ontology 1:1 强绑定**（照搬 Palantir，防止本体碎片化）。Space 绑定 Organization 白名单。**基础设施绑定舍弃**（Gaia 无 Spark，存储多引擎统一管，只保留容器 + 组织白名单 + 本体绑定语义） |
| **Project（协作安全边界）** | ✅ **完整引入（新建实体）** | Project 是权限原子单位（对齐 Palantir）。资源（Dataset/SyncTask/Datasource）归属 Project。**ObjectType/ActionType/LinkType 等定义类资源一期归属 Ontology（选项 B 简化），预留 `project_id` 字段未来可迁到 Project（选项 A）**。详见 §三 决策 9 |
| **Marking（MAC 强制兜底）** | ✅ **引入（核心）** | 业界共识（ABAC/Tag-driven）。**给 ObjectType/Property 打标记，标记校验合取 AND**。但**不做血缘自动传播**（一期手动打标 + 校验，二期视血缘引擎成熟度再补） |
| **行/列级安全** | ✅ **引入（核心）** | 业界标准。**ObjectType 配 row_security_policy（表达式）→ 查询下推；Property 配 property_masking → 序列化脱敏**。对齐 Palantir Object/Property Security Policy（非过时的 Restricted View） |

### 2.3 身份体系引入

| Palantir 主体 | Gaia 取舍 | 理由 |
|--------------|:---:|------|
| **User** | ✅ 引入 | 必需。对接 OIDC（一期）或自建（MVP） |
| **Group** | ✅ 引入（核心） | 权限唯一载体，组授权铁律 |
| **Service User** | ✅ 引入 | Agent/API 集成需要（Gaia 有 AG-UI/MCP/REST 三入口） |
| **Organization/Guest** | ✅ 引入 | Organization 完整引入（§2.2），Guest 作为跨组织协作机制保留（用户可 Guest 加入多个外部 Organization） |
| **动态组** | 🟡 二期 | 基于属性自动入组，一期手动 |

### 2.4 角色体系引入

| Palantir 层级 | Gaia 取舍 | 理由 |
|--------------|:---:|------|
| **全局平台角色** | 🟡 简化 | Gaia 开源本地优先，无需 Platform Admin/User Access Admin/Marking Admin 细分。**简化为：Platform Admin + Audit Admin 两角色**（权责分离底线） |
| **空间/本体域角色** | 🟡 映射 | Gaia 无 Space。**Ontology Owner/Editor/Viewer/Discoverer**（Ontology 即业务域） |
| **项目基础角色** | ✅ 引入 | Ontology 级 Owner/Editor/Viewer/Discoverer（复用 Palantir 四角色语义） |
| **应用操作角色** | ✅ 复用 ADR-011 | Action 三层权限已有（roles/condition/sensitive_params），扩展为完整 Action 角色 |
| **自定义角色集** | ❌ 舍弃（一期） | 一期用默认角色集。~~Palantir 冻结机制维护成本高~~（此说法已修正：复制自 Project default 的 role set 会自动同步平台更新，非冻结）。舍弃理由调整为：一期无需自定义，默认四角色够用；二期按需评估 |

### 2.5 授权引擎引入

| Palantir 能力 | Gaia 引入 | 关键调整 |
|--------------|:---:|------|
| **中心化授权引擎** | ✅ 引入（核心） | 新建 `AuthorizationService`（PDP），所有 Service 调用 |
| **七层串行校验** | 🟡 调整为五层 | 完整引入上三层后，**五层：身份认证 → Organization → Space → Project RBAC → Marking MAC → 行/列级** |
| **默认拒绝 + 不可见即安全** | ✅ 引入 | 业界基线 |
| **三级缓存** | 🟡 简化 | 一期用 PG 实时查 + 进程内 LRU 缓存（短 TTL），二期评估 Redis |
| **Check Access 可解释性** | ✅ 引入 | 高价值，`GET /authz/check` 端点 |

### 2.6 审计治理引入

| Palantir 能力 | Gaia 取舍 | 理由 |
|--------------|:---:|------|
| **审计追加写入不可篡改** | ✅ 引入 | 复用 `action_execution_logs` + 新建 `audit_logs` 表 |
| **六大治理工具** | 🟡 一期做 2 个 | 一期：权限自助申请+审批 / Check Access。二期：标记影响分析 / 过度权限分析 |
| **PBAC** | ❌ 二期+ | 强合规场景可选，一期不做 |
| **生命周期自动化** | 🟡 一期对接 OIDC | 入职/离职通过 OIDC 同步，转岗手动 |

---

## 三、核心架构决策（评估建议）

> 以下决策是**评估建议**，需评审确认后写入 ADR-016。

### 决策 1：模型选型 —— RBAC + ABAC(Tag) 混合

**建议**：RBAC 管协作授权（Ontology/Action 角色授予 Group），ABAC/Tag 管数据访问（ObjectType/Property 打标记 + 行级策略表达式）。

**理由**：
- 业界共识（Databricks/Snowflake/Immuta/Ranger 都是此模式，见研究 §2.3）
- 避免 role explosion（ABAC 一条策略覆盖海量资源，见研究 §3.2.4）
- 与 ADR-011 Action 三层权限的 `roles` + `condition` 模式一致（condition 即轻量 ABAC）

**不选纯 RBAC**：role explosion（50 ObjectType × 4 角色 = 200 角色组合）
**不选纯 ABAC**：协作授权场景 RBAC 更直观
**不选 ReBAC**：行/列级安全不是 ReBAC 强项（研究 §2.2），但 ObjectType 间 Link 关系权限可考虑（二期）

### 决策 2：权限边界 —— 完整引入 Organization + Space + Project 三层

**建议**：完整引入 Palantir 上三层，对齐企业级安全模型。Project 是协作权限边界（权限原子单位），Ontology 是业务语义核心，两者职责分离（Palantir 范式，不能用 Ontology 顶替 Project）。

**层级关系**：
```
Organization（主体隔离，MAC）
  └── Space（业务域容器，1:1 绑定 Ontology，组织白名单）
       └── Ontology（业务语义核心，1:1 归属 Space）
            └── ObjectType/ActionType/LinkType（定义，归属 Ontology）
            └── ObjectTypeGroup（语义分组，无权限）
       └── Project（协作权限边界，原子单位）
            └── Dataset/SyncTask/Datasource（资源，权限归 Project）
            └── Group 角色：Owner/Editor/Viewer/Discoverer
```

**理由**：
- 做正确的事，不逃避架构复杂度。完整三层为企业落地提供与 Palantir 同等的安全基座
- Project ≠ Ontology：Project 是协作单元（给人授权），Ontology 是语义模型（管对象定义）。一个业务域可拆多个 Project（数据团队/本体团队/应用团队权限分离），共享一个 Ontology
- Space↔Ontology 1:1 强绑定保证业务域语义统一（防止本体碎片化，Palantir 核心哲学）
- 单租户部署默认一个 Organization + 一个 Space，渐进式披露不增加用户认知负担

**Space 基础设施绑定舍弃**：Palantir 的 Space 绑定 Spark/存储/加密/计费，Gaia 无 Spark 且存储多引擎统一管，只保留「容器 + 组织白名单 + 本体绑定」语义。

**Space↔Ontology 1:1 强绑定的实践考量**：

1:1 是 Palantir 核心哲学——保证业务域语义统一，防止「同业务对象多 Ontology 重复定义」的数据孤岛。但实践中有以下场景需关注：

| 场景 | 问题 | 解法 |
|------|------|------|
| 本体膨胀（几十上百 ObjectType） | 难以管理 | **ObjectTypeGroup**（Ontology 内语义分组，无权限）— Gaia 已有空壳模型，未来接线 |
| 跨团队权限细分（应收/应付团队） | 不能拆 Ontology 做权限 | **Project + Marking**（定义放不同 Project，或打标记）— 选项 A 未来支持 |
| 跨业务域共享对象（供应链+财务共享 Supplier） | 不能在两个 Space 各定义 | **Reference + Interface**（引用 + 接口契约）— Gaia 已有 InterfaceTypeModel |
| **DEV/PROD 环境隔离** | Gaia 无 Global Branching | **短期：多实例部署**（开源本地优先，成本低）；**长期：Ontology 版本/分支机制（待设计，见下）** |

**待设计项：Ontology 版本/分支机制（方案 3）**

短期照搬 1:1，但记录方案 3 作为未来待设计项，解决 DEV/PROD 隔离与环境演进问题：

- **思路**：Space↔Ontology 保持 1:1，但 Ontology 支持版本或分支（类似 Scenario 的 overlay 思路）。DEV/PROD 是同一 Ontology 的不同版本/分支，不是不同 Ontology
- **价值**：语义统一 + 环境隔离两全
- **成本**：需自建 Ontology 版本机制（Gaia 无 Global Branching）；与已设计的 Scenario（object_state overlay）可能概念重叠，需厘清边界
- **触发时机**：当 Gaia 出现真实的 DEV/PROD 隔离需求，且多实例部署成本不可接受时启动设计
- **记录位置**：本评估 §9.3 未决问题 #9

### 决策 3：行/列级安全 —— Object/Property Security Policy（非 Restricted View）

**建议**：对齐 Palantir 最新推荐（非过时的 Restricted View，见研究 §1.1 错误2）：
- **ObjectType 配 `row_security_policy`**（表达式，引用 principal 属性）→ 查询时下推 Doris Row Policy / PG RLS
- **Property 配 `property_masking_policy`** → Doris 原生 MASK+VIEW（存储层）/ 其他引擎序列化层返回 null（脱敏）
- **两者组合 = cell 级**（对齐 Palantir Object + Property Security Policy）

**理由**：
- Palantir 官方明确推荐 Object Security Policy over Restricted View（支持 streaming/branching/近实时更新）
- 业界标准（Databricks ABAC / Snowflake tag-based masking 同此模式）
- 动态脱敏不落盘（Snowflake/Databricks 最佳实践）

### 决策 4：策略引擎 —— 自建轻量策略层（一期），评估 OPA/Cerbos（二期）

**建议**：一期自建轻量 `AuthorizationService`（Python 原生，表达式用 simpleeval——与 ADR-011 ActionRuleEngine 一致）。二期若策略复杂度上升，评估引入 OPA/Cerbos。

**理由**：
- 一期策略简单（角色 + 标记 + 行级表达式），simpleeval 够用，无需引入外部依赖
- 与 ADR-011 ActionRuleEngine 复用同一表达式引擎，降低学习成本
- OPA/Cerbos 增加部署复杂度（额外服务），一期不值得
- 策略外部化（Policy-as-Code）是趋势，但一期可先用 ORM + JSONB 存储，二期迁移 YAML/DSL

### 决策 5：标记传播 —— 一期手动打标 + 校验，二期血缘传播

**建议**：
- **一期**：ObjectType/Property 手动打标记，查询时合取校验（AND）。不做血缘传播。
- **二期**：视血缘引擎成熟度，评估标记沿 Iceberg→Doris→ObjectType 传播

**理由**：
- 血缘自动传播价值高但实现成本高（需血缘引擎实时追踪，研究 §2.3 标注 Palantir 独有）
- 一期手动打标 + 校验已能覆盖核心场景（PII 标记 → 无权限用户看不到）
- Gaia 已有 `physical_mapping`（backing_catalog/schema/table/column），血缘基础存在，二期可扩展

### 决策 6：身份对接 —— OIDC 优先，自建兜底

**建议**：
- **一期**：新建 Principal/User/Group ORM 表 + OIDC 对接（Keycloak/Authelia 等开源 IDP）+ 认证中间件
- **MVP 兜底**：保留 `X-User-Id` 请求头模式（开发/测试用），生产用 OIDC
- **不对接 LDAP**：OIDC 是现代标准，LDAP 是遗留（Palantir 也推荐 OIDC）

**理由**：
- OIDC 是业界主流（Auth0/Keycloak/Authelia），开源生态成熟
- Gaia 开源本地优先，IDP 应可选可替换
- 自建用户系统作为 OIDC 不可用时的兜底

### 决策 7：权限下推分层 —— Doris/PG 主力，Trino 谓词下推，Neo4j Cypher

**建议**：
- **Doris**（在线读主源）：用原生 Row Policy + Column Masking（[官方支持](https://doris.apache.org/docs/4.x/admin-manual/auth/authorization/data/)），AuthorizationService 编译策略为 Doris SQL 注入
- **PG**（object_state + 元数据）：用 RLS（Row Level Security），AuthorizationService 生成 PG policy
- **Trino**（联邦/降级）：一期 SQL 注入 filter（谓词下推到 connector），二期评估 OPA/Ranger 插件（计划改写）
- **Neo4j**（图遍历）：Cypher 层过滤（searchAround/find_paths 加 WHERE 条件）
- **Iceberg**：不直接下推（写入入口，非查询主源）

**理由**：多引擎统一方案成本高且不必要，分层适配更务实。

### 决策 8：与 Scenario 协同 —— 权限独立于 scenario_id

**建议**：权限校验在 scenario overlay 求值**之前**执行（base 数据权限），scenario 内的 overlay 数据继承 base 权限。即：用户须先有 base 数据访问权限，才能在 scenario 内查看/修改 overlay。

**理由**：
- Scenario 是"假设推演"，不应绕过 base 权限
- scenario-*.md 设计的 object_state 加 scenario_id 维度，与权限字段（marking/policy）正交
- 权限校验先行，overlay 求值在后，层次清晰

### 决策 9：资源归属策略 —— 选项 B 简化 + 预留 project_id 平滑迁移

**建议**：一期走**选项 B（简化模型）**——Ontology 定义类资源归属 Ontology，不单独放 Project；但数据模型**预留 `project_id` 字段**，确保未来可平滑迁移到**选项 A（完整模型，定义可放 Project）**。

**选项 B 一期归属**：
```
Organization
  └── Space（1:1 绑定 Ontology）
       ├── Ontology（1:1 归属 Space）
       │    └── ObjectType/ActionType/LinkType/InterfaceType/SharedPropertyType
       │        （定义归 Ontology，预留 project_id 字段，一期为空或指向默认 Project）
       │    └── ObjectTypeGroup（语义分组，归 Ontology，无权限）
       └── Project（协作权限边界）
            └── Dataset/SyncTask/Datasource/Credential（资源归 Project）
```

**选项 A 未来归属（B→A 迁移后）**：
```
Organization
  └── Space（1:1 绑定 Ontology）
       ├── Ontology（逻辑宿主）
       └── Project（协作权限边界）
            ├── ObjectType/ActionType/LinkType（定义迁入 Project，project_id 填充）
            └── Dataset/SyncTask/Datasource（资源归 Project）
```

**关键设计原则（确保 B→A 平滑）**：
1. 一期定义类资源表（object_types/action_types/link_types 等）加 `project_id` 列（nullable）
2. 权限查询逻辑用「**project_id 优先，fallback Ontology**」——project_id 为空时查 Ontology 角色，非空时查 Project 角色
3. 不要把"定义归 Ontology"做死，预留迁移空间
4. 实例权限（object_state/object_links）跟 backing dataset 走（对齐 Palantir：定义归 Project，实例跟数据源）

**理由**：
- Palantir 自己也是从简单模型（Ontology Roles）演进到 Project-based 权限的，有官方迁移工具（[Migrate to project-based permissions](https://palantir.com/docs/foundry/ontology-manager/migrate-to-project-based-permissions/)）
- 选项 B 一期避免"Ontology Project vs Data Integration Project"拆分的复杂度（Gaia 当前无多团队细分需求）
- 预留 project_id 确保未来切换只是「填充 project_id + 切换权限查询逻辑」，不是重构
- 迁移可渐进（逐个资源迁移，不需一次性全切）

**可放 Project 的 Ontology 定义资源**（Palantir 官方确认，对应 Gaia 5 类 ORM）：ObjectType / ActionType / LinkType / InterfaceType / SharedPropertyType。**实例权限跟 backing dataset**（不归定义的 Project）。

---

## 四、分期实施路线（评估建议）

> **实现状态（2026-07-10）**：Phase 0-5 已全部完成实现。后端 1524 单元测试 + 前端 253 测试 + E2E 46 用例全绿。OIDC 对接用 Better Auth（非 Keycloak/Authelia）；JWT 验证用 `fastapi-betterauth`（非 Authlib）；JIT auto-provisioning 用 Better Auth `databaseHooks`。后续待办见 [`docs/engineer/permission-roadmap-and-principles.md`](../engineer/permission-roadmap-and-principles.md) §二。

> 参照研究 §4.5 的待决策问题 + §三 决策（完整三层 + 选项 B），给出分期路线。每期可独立交付价值。

### Phase 0：身份基石 + 三层容器（2-3 天）

**目标**：建立 Principal 模型 + 认证中间件 + Organization/Space/Project 三层容器骨架，替换 anonymous。

**交付**：
- **身份层**：`Principal` / `User` / `Group` / `GroupMembership` / `ServiceUser` ORM 表 + Alembic migration
- **三层容器**：`Organization` / `Space` / `Project` ORM 表（Space↔Ontology 1:1 绑定逻辑）
- `AuthMiddleware`：从 OIDC token 或 `X-User-Id` 请求头提取 Principal，注入 request.state
- `ActionContext` 扩展：`principal: Principal`（替换 current_user 字符串）
- **资源归属字段**：Ontology 加 `space_id`；DataSource/Dataset/SyncTask/Credential 加 `project_id`；定义类资源（ObjectType/ActionType 等）加 `project_id`（nullable，选项 B 预留）
- **默认初始化**：单租户部署自动创建默认 Organization + 默认 Space，保证向后兼容
- OIDC 对接（Keycloak/Authelia），自建用户表兜底
- Organization/Space/Project CRUD Service + Route

**价值**：解锁多用户场景 + 建立三层容器骨架，为后续权限奠定身份与边界基础。

**依赖**：无

### Phase 1：Project RBAC + 角色体系（2-3 天）

**目标**：Project 级角色授权（权限原子单位），替换 ADR-011 的 `parameters.permissions` JSON。

**交付**：
- `Role` / `RoleAssignment` ORM 表（principal_id, role, scope=project_id）
- 四角色：Project Owner/Editor/Viewer/Discoverer（对齐 Palantir）
- 全局角色：Platform Admin / Audit Admin（权责分离）
- `AuthorizationService`（PDP）：五层校验（身份 → Organization → Space → Project RBAC → Marking → 行/列级，后两层先 stub）
- `OntologyService` / `ObjectQueryService` / `ActionService` 接入 AuthorizationService
- 组授权：RoleAssignment 授 Group，User 通过 Group 获角色
- ADR-011 ActionAuthorizer 内部切换到 AuthorizationService（契约不变）
- **选项 B 权限查询逻辑**：定义类资源 project_id 为空时查 Ontology 角色（fallback），非空时查 Project 角色

**价值**：实现 Project 级协作授权，权责分离。

**依赖**：Phase 0

### Phase 2：Marking MAC + 标记校验（2-3 天）

**目标**：数据级强制访问控制，硬门槛。

**交付**：
- `Marking` / `MarkingCategory` ORM 表 + `MarkingAssignment`（resource_type, resource_id, marking_id）
- ObjectType/Property 可打标记
- 标记合取校验（AND）：用户须持有资源全部标记
- `AuthorizationService` Layer 4（Marking）：查询时过滤无标记权限的对象/属性
- Marking Admin 角色（权责分离：标记管理 vs 标记使用）
- 手动打标（不做血缘传播）

**价值**：数据分类分级强制兜底，PII/机密数据保护。

**依赖**：Phase 1

### Phase 3：行/列级安全 + 权限下推（3-4 天，核心难点）

**目标**：千人千面，权限下推到存储层。

**交付**：
- `RowSecurityPolicy` ORM 表（object_type_id, expression）—— 表达式引用 principal 属性
- `PropertyMaskingPolicy` ORM 表（property_id, expression）
- **Doris 下推**：AuthorizationService 编译 row_security_policy 为 Doris Row Policy SQL
- **PG RLS 下推**：object_state 表加 RLS policy
- **Trino 下推**：ObjectQueryService 注入 filter 到 SQL（一期，谓词下推到 connector），二期评估 OPA/Ranger
- **Neo4j Cypher 过滤**：searchAround/find_paths 加 WHERE
- Property 脱敏：Doris 原生 MASK 函数 + VIEW（存储层）；其他引擎序列化层返回 null（一期）
- cell 级（行×列交叉）

**价值**：同一份数据不同用户看不同子集，满足多租户/区域隔离。

**依赖**：Phase 2

### Phase 4：审计 + Check Access + 自助申请（2-3 天）

**目标**：审计可追溯 + 权限可解释 + 自助申请流程。

**交付**：
- `AuditLog` ORM 表（追加写入，不可篡改）
- 所有权限决策落审计（principal, resource, action, result, reason）
- `GET /authz/check` 端点（Check Access：输入用户+资源，返回每层校验状态 + 权限来源）
- 权限自助申请 + 审批工作流（`AccessRequest` 表）
- Audit Admin 角色（仅看审计，无操作权限）

**价值**：合规可审计，权限可解释，降低管理沟通成本。

**依赖**：Phase 3

### Phase 5：前端 + 治理工具（3-4 天，可与后端并行）

**目标**：权限管理 UI + 渐进式披露。

**交付**：
- Organization/Space/Project 管理 UI（三层容器）
- Principal/Group/Role 管理 UI
- Project 角色授权 UI
- 标记管理 UI（Marking Admin）
- 行级策略编辑器（表达式 + 预览）
- Check Access 调试面板
- 权限申请流程 UI
- 渐进式披露：默认视图（日常用例，单租户默认 Organization 不暴露三层管理）+ 高级面板（标记/策略/角色/三层容器）

**价值**：用户可自助管理权限，无需工程师介入。

**依赖**：Phase 1-4 后端就绪

### Phase 6（二期）：高级能力

- 标记血缘自动传播（依赖血缘引擎）
- PBAC（Purpose-Based，面向强合规）
- 动态组（基于属性自动入组）
- 过度权限分析 + 僵尸权限清理
- OPA/Cerbos 策略引擎评估
- Trino Ranger 插件接入
- LLM 辅助策略生成（自然语言 → 结构化策略，Gaia 差异化机会）
- **选项 B → 选项 A 迁移**（定义类资源迁入 Project，填充 project_id）
- **Ontology 版本/分支机制**（方案 3，DEV/PROD 隔离，见 §三 决策 2 待设计项）

**总工期估算**：Phase 0-5 约 14-20 天（后端 11-15 天 + 前端 3-4 天并行）。Phase 0 因新增三层容器比原计划多 1 天。

---

## 五、数据模型设计草案（评估建议）

> 以下 ORM 表设计是**评估草案**，详细设计在 ADR-016 + 设计文档中定稿。

### 5.1 三层容器（Organization / Space / Project）

> 对齐 Palantir 上三层（§二引入映射 + §三决策2）。Space↔Ontology 1:1 强绑定；Project 是权限原子单位。

```python
class OrganizationModel(Base):  # 主体隔离层（MAC）
    __tablename__ = "organizations"
    id: Mapped[str]  # UUID hex
    api_name: Mapped[str]  # unique，如 org-internal / org-vendor-xxx
    display_name: Mapped[str]
    description: Mapped[str] = ""
    org_type: Mapped[str]  # INTERNAL | EXTERNAL
    status: Mapped[str]  # ACTIVE | DISABLED
    # 系统级内置标记由 Organization 自动派生（与 Marking 体系联动，见 §5.4）
    created_at / updated_at
    # 关联：spaces（通过 Space.organization_ids 白名单）

class SpaceModel(Base):  # 业务域容器 + 本体生命周期载体
    __tablename__ = "spaces"
    id: Mapped[str]
    api_name: Mapped[str]  # unique，如 finance-core / supply-chain-shared
    display_name: Mapped[str]
    description: Mapped[str] = ""
    status: Mapped[str]  # ACTIVE | ARCHIVED
    # ⚠️ Space↔Ontology 1:1 强绑定：创建 Space 自动创建同名 Ontology，
    #     Space 删除则 Ontology 不可恢复（对齐 Palantir）
    ontology_id: Mapped[str]  # FK ontologies.id，1:1 unique
    # 组织白名单（多组织共享 Space 用于跨组织协作）
    # 通过 SpaceOrganization 关联表实现多对多
    created_at / updated_at
    # ❌ 不绑定基础设施（Gaia 舍弃 Palantir 的 Spark/存储/加密/计费绑定）

class SpaceOrganizationModel(Base):  # Space↔Organization 白名单关联
    __tablename__ = "space_organizations"
    space_id: Mapped[str]  # FK spaces
    organization_id: Mapped[str]  # FK organizations
    # PK (space_id, organization_id)

class ProjectModel(Base):  # 协作权限边界（权限原子单位）
    __tablename__ = "projects"
    id: Mapped[str]
    api_name: Mapped[str]  # unique within space
    display_name: Mapped[str]
    description: Mapped[str] = ""
    space_id: Mapped[str]  # FK spaces（Project 归属 Space）
    status: Mapped[str]  # ACTIVE | ARCHIVED
    created_at / updated_at
    # 资源（Dataset/SyncTask/Datasource/Credential）通过各自表的 project_id 归属
    # 角色授予通过 RoleAssignment（scope=project_id）
```

### 5.2 身份层

```python
class PrincipalModel(Base):  # 主体抽象基类
    __tablename__ = "principals"
    id: Mapped[str]  # UUID hex
    principal_type: Mapped[str]  # USER | GROUP | SERVICE_USER
    display_name: Mapped[str]
    status: Mapped[str]  # ACTIVE | DISABLED
    # 多态：User/Group/ServiceUser 继承或关联
    created_at / updated_at

class UserModel(Base):  # 自然人
    __tablename__ = "users"
    id: Mapped[str]  # = principal_id
    email: Mapped[str]  # unique
    subject: Mapped[str]  # OIDC sub（IDP 侧唯一标识）
    attributes: Mapped[dict]  # JSONB: department/region/level（从 OIDC 同步，行级安全用）
    home_organization: Mapped[str | None]  # 一期可空（单租户）

class GroupModel(Base):  # 用户组（权限唯一载体）
    __tablename__ = "groups"
    id: Mapped[str]
    name: Mapped[str]
    description: Mapped[str]
    # 组成员通过 GroupMembership 关联

class GroupMembershipModel(Base):  # 用户-组关系
    __tablename__ = "group_memberships"
    group_id: Mapped[str]  # FK groups
    user_id: Mapped[str]  # FK users
    # 嵌套：parent_group_id（自引用，建议 ≤ 2 层）

class ServiceUserModel(Base):  # 服务账号
    __tablename__ = "service_users"
    id: Mapped[str]  # = principal_id
    name: Mapped[str]
    scopes: Mapped[list]  # JSONB: 作用域限制（可访问的 Ontology/ObjectType/API）
    owner: Mapped[str]  # 负责人 user_id
```

### 5.3 角色层

```python
class RoleModel(Base):  # 角色定义
    __tablename__ = "roles"
    id: Mapped[str]
    name: Mapped[str]  # OWNER | EDITOR | VIEWER | DISCOVERER | PLATFORM_ADMIN | AUDIT_ADMIN | MARKING_ADMIN
    scope_type: Mapped[str]  # GLOBAL | PROJECT | ACTION
    permissions: Mapped[list]  # JSONB: 原子权限列表

class RoleAssignmentModel(Base):  # 角色授予（授 Group，不授个人）
    __tablename__ = "role_assignments"
    principal_id: Mapped[str]  # 通常为 group_id
    role_id: Mapped[str]
    scope_id: Mapped[str | None]  # project_id（PROJECT scope 时）
    expires_at: Mapped[datetime | None]  # 临时权限到期
```

> **选项 B 权限查询逻辑**：定义类资源（ObjectType 等）project_id 为空时，权限查其所属 Ontology→Space→Project 的角色（fallback）；project_id 非空时直接查该 Project 角色。详见 §三 决策 9。

### 5.4 标记层（MAC）

```python
class MarkingCategoryModel(Base):  # 标记分类
    __tablename__ = "marking_categories"
    id: Mapped[str]
    name: Mapped[str]  # 数据密级 | 敏感类型 | 业务分区
    description: Mapped[str]

class MarkingModel(Base):  # 标记值
    __tablename__ = "markings"
    id: Mapped[str]
    category_id: Mapped[str]
    name: Mapped[str]  # 机密 | PII | 华东
    # 授权通过 MarkingGrant

class MarkingGrantModel(Base):  # 标记权限授予（授 Group）
    __tablename__ = "marking_grants"
    group_id: Mapped[str]
    marking_id: Mapped[str]

class MarkingAssignmentModel(Base):  # 资源打标
    __tablename__ = "marking_assignments"
    resource_type: Mapped[str]  # OBJECT_TYPE | PROPERTY | ONTOLOGY
    resource_id: Mapped[str]
    marking_id: Mapped[str]
    is_directly_applied: Mapped[bool]  # 对齐 Palantir
```

### 5.5 行/列级安全

```python
class RowSecurityPolicyModel(Base):  # 行级策略
    __tablename__ = "row_security_policies"
    id: Mapped[str]
    object_type_id: Mapped[str]
    expression: Mapped[str]  # simpleeval 表达式，引用 principal.attributes
    # 例：principal.attributes['region'] == row['region']
    description: Mapped[str]

class PropertyMaskingPolicyModel(Base):  # 列级脱敏
    __tablename__ = "property_masking_policies"
    id: Mapped[str]
    property_id: Mapped[str]
    expression: Mapped[str]  # 不满足则返回 null
    # 例：'PII' in principal.markings
```

### 5.6 审计层

```python
class AuditLogModel(Base):  # 审计日志（追加写入）
    __tablename__ = "audit_logs"
    id: Mapped[str]
    timestamp: Mapped[datetime]
    principal_id: Mapped[str | None]
    resource_type: Mapped[str]
    resource_id: Mapped[str]
    action: Mapped[str]
    result: Mapped[str]  # ALLOW | DENY
    reason: Mapped[str]  # 哪一层拦截
    ip: Mapped[str | None]
    request_id: Mapped[str | None]
    # 不可更新/删除（应用层强制 + DB 权限）
```

### 5.7 与现有模型的协同

**新增归属字段**（Phase 0 schema 变更）：
- **OntologyModel**：加 `space_id`（FK spaces，1:1 强绑定；现有 Ontology 需迁移到默认 Space）
- **DataSourceModel / DatasetGovernanceModel / SyncTaskModel / CredentialModel**：加 `project_id`（FK projects，现有资源迁移到默认 Project）
- **ObjectTypeModel / ActionTypeModel / LinkTypeModel / InterfaceTypeModel / SharedPropertyModel**：加 `project_id`（nullable，选项 B 预留；为空时归属 Ontology，非空时归属 Project）

**不改动**：
- **ObjectTypeGroupModel**：保留（Ontology 内语义分组，与 Project 正交），未来接线
- **PropertyDefModel**：标记/行级策略通过关联表，不污染 Property（但可加 `project_id` 跟随 ObjectType）
- **ActionTypeModel.parameters.permissions**：JSONB 保留（ADR-011 兼容），内部切换到 AuthorizationService
- **BranchModel**：与权限正交（Scenario 用），不动
- **object_state**：加 RLS policy（PG 行级安全），不改 schema

**Organization↔Marking 联动**：Organization 创建时自动派生系统级内置 Marking（对齐 Palantir：每个 Organization 对应一个系统标记，用户/资源自动绑定）。Organization 删除则该标记级联。详见 §5.1 OrganizationModel 注释。

---

## 六、查询层权限下推方案（评估建议）

> 查询层是权限下推的核心改造区。本节给出多引擎下推的评估方案。引入完整三层后，查询下推发生在**五层校验通过后**（身份 → Organization → Space → Project RBAC → Marking），只负责最内层的行/列级过滤。

### 6.1 权限校验与下推的分层（五层校验 + 行/列下推）

**关键原则**：上四层（身份/Org/Space/Project/Marking）在 `AuthorizationService`（PDP）集中求值，决定"能不能看这个资源"；第五层（行/列级）下推到存储引擎，决定"能看到哪些行/列"。

```
请求 → AuthMiddleware 提取 Principal
     → AuthorizationService.check_access(principal, resource, action)
         Layer 1: 身份认证（Principal 有效性）
         Layer 2: Organization 校验（principal.home_organization ∈ resource.space.organizations）
         Layer 3: Space 校验（principal 有 Space 准入角色）
         Layer 4: Project RBAC（principal 有 Project Owner/Editor/Viewer/Discoverer）
                  ↑ 选项 B：定义类资源 project_id 为空时 fallback 到 Ontology 所属 Project
         Layer 5: Marking MAC（resource 全部 marking ⊆ principal.markings）
     → 若 Layer 1-5 全过，进入行/列级下推（本节）
     → 若任一层拒，返回 403 或资源不可见（不可见即安全）
```

### 6.2 ObjectQueryService 改造

**现状**：`_resolve_query_target` 无权限参数，filter 不感知 principal。

**改造**：
1. 所有查询方法加 `principal: Principal` 参数
2. 查询前调 `AuthorizationService.evaluate_query_scope(principal, object_type)` → 返回 `QueryScope`：
   - `visible_rids: set[str] | None`（None = 全可见，set = 仅这些 ID）
   - `masked_properties: set[str]`（须脱敏的属性）
   - `forbidden: bool`（整个 ObjectType 无权限——上四层任一拒）
   - `project_scope: str | None`（选项 B：定义类资源的 project_id，None 时用 Ontology 所属 Project）
3. filter SQL 注入权限条件（`WHERE id IN (visible_rids)` 或 Doris Row Policy）
4. 序列化时 masked_properties 返回 null

### 6.3 Doris 下推（主力）

**Doris 4.0.5 原生支持**（[官方文档](https://doris.apache.org/docs/4.x/admin-manual/auth/authorization/data/)）：
- **Row Policy**：`CREATE ROW POLICY ... AS condition TO role` —— 自动追加 WHERE
- **Column Permission**：列级 GRANT
- **Data Masking**：列脱敏

**方案**：
- AuthorizationService 编译 `RowSecurityPolicy.expression` 为 Doris Row Policy
- **Doris 角色映射 Gaia Group**：Gaia Group → Doris Role（同步），Row Policy 授予 Doris Role
- 查询时 Doris 自动注入过滤，无需应用层改 SQL
- Doris 连接用 Service User（scoped 到 Project 可访问的 Doris 表）

**优势**：权限下推到存储层，不可绕过（研究 §2.3 业界共识）

### 6.4 PG RLS 下推（object_state）

**PG 16 原生 RLS**：
- `CREATE POLICY ... ON object_state USING (expression)`
- expression 引用 `current_setting('app.principal_attributes')`（从中间件设置）

**方案**：
- object_state 表启用 RLS
- AuthMiddleware 设置 `SET LOCAL app.principal_attributes = '...'`（含 organization/space/project/marking 上下文）
- PG 自动过滤，Action 写入也受 RLS 保护（写入前校验 + RLS 读校验双重）
- **Organization/Space/Project 维度也在 RLS**：object_state 通过 ontology_id→space→organization 关联，RLS 可过滤跨组织数据

### 6.5 Trino 下推（一期）

**现状**：Trino 无 Ranger/OPA，查询走 `TrinoQueryEngine.query`。

**关键澄清**：Trino 的行级安全不是「应用层后过滤」。一期方案是 ObjectQueryService 把权限 filter（visible_rids）拼进 SQL WHERE，Trino 通过 predicate pushdown 下推到 connector（Iceberg/PG 等）。这已能下推，不是全量拉取再过滤。

**一期方案**：ObjectQueryService 注入 filter 到 SQL（谓词下推到 connector）
- 权限 filter 须应用层预计算（visible_rids）拼进 SQL WHERE
- Trino 谓词下推到 connector，性能与手写 WHERE 一样
- **Organization/Space/Project 维度**：Trino 联邦查询跨 catalog，上层 AuthorizationService 先校验 principal 对目标 Ontology（→Space→Project）的权限

**二期方案**：评估 OPA/Ranger 插件（行过滤/列脱敏表达式由插件返回，Trino 计划器自动注入，无需应用层预计算；含 Organization/Space/Project 维度）

### 6.6 Neo4j Cypher 过滤

**现状**：`Neo4jGraphStore.search_around` / `find_paths` 用 Cypher。

**方案**：Cypher 查询加 WHERE 条件过滤无权限节点
- `MATCH (n)-[*1..3]->(m) WHERE m.id IN $visible_ids RETURN m`
- visible_ids 由 AuthorizationService 预计算（含 Organization/Space/Project/Marking 四层过滤后的对象集）
- Neo4j 节点的 indexed 属性投影也须过 Marking 校验（无标记权限的属性不返回）

### 6.7 跨 Project Reference 的二次校验（引入 Project 后的新场景）

**场景**：Project B 的 ObjectType 引用 Project A 的 backing dataset（选项 A 未来场景，或跨 Project 资源引用）。

**校验逻辑**（对齐 Palantir Reference 二次校验）：
1. 先校验 principal 对 Project B（引用所在 Project）的访问权限
2. 再校验 principal 对 Project A（源数据所在 Project）的权限（Organization + Space + Project + Marking 四层）
3. 两次全过才返回数据，任一拒则引用处返回空
4. 数据安全属性以源 Project A 为准（不因被引用而降级）

**一期（选项 B）影响**：定义类资源归 Ontology，跨 Project 引用主要是 backing dataset 级。一期 Dataset 归 Project，跨 Project Dataset 引用需二次校验。

---

## 七、与 Scenario / Action / 工具层 / 三层容器的协同

### 7.1 与 Scenario 协同（scenario-*.md）

**Scenario 设计**：object_state 加 `scenario_id` 维度，overlay 读写。Scenario 挂在 Ontology 下，Ontology 归属 Space。

**权限协同**（决策 8）：
- 权限校验在 scenario overlay 求值**之前**执行（五层校验：身份→Org→Space→Project→Marking）
- 用户须先有 base 数据访问权限，才能查看/修改 scenario overlay
- scenario 内 overlay 数据继承 base 权限（不因 scenario 而放宽）
- **scenario_id 与权限字段正交**：object_state 的 marking/policy 不因 scenario_id 改变
- **Scenario 可见性受 Space 约束**：Scenario 属 Ontology→Space，用户须先通过 Space 准入才能看到该 Ontology 的 Scenario

**实现**：`DataFrameQueryService.execute` 入口先调 AuthorizationService 校验 base 权限（五层），再做 overlay 求值。

### 7.2 与 Action 协同（ADR-011）

**Action 现状**：三层权限存 `parameters.permissions` JSON，ActionAuthorizer 实现。ActionType 归属 Ontology（选项 B 预留 project_id）。

**权限协同**：
- ADR-011 ActionAuthorizer **契约不变**（三层返回 forbidden set）
- **内部切换**：Layer 1（执行权限）从 JSON roles 改为查 RoleAssignment（Project scope）；Layer 2（行级写）从 catalog.check_access 改为 AuthorizationService（五层校验）；Layer 3（参数级）保留
- `ActionContext.principal` 替换 `current_user` 字符串
- **Action 执行前的五层校验**：principal 须对 affected_object_type 所属的 Ontology→Space→Project 有权限，且持有 ActionType 的执行角色

**向后兼容**：`parameters.permissions` JSON 保留（旧 ActionType 定义不破坏），AuthorizationService 读取时优先用结构化 RoleAssignment，fallback 到 JSON。

### 7.3 与工具层协同（ADR-009）

**工具层现状**：ToolExecutor 治理切面，principal=anonymous。

**权限协同**：
- ToolExecutor 从 request.state 取 Principal（Phase 0 AuthMiddleware 注入）
- 22 工具调用前过 AuthorizationService（每个工具声明所需权限 + 目标资源所属 Project）
- AG-UI Agent 以 Principal 身份执行（继承人类用户或 Service User 权限）
- MCP 工具用 Service User（scoped 限制，含 Project 维度——可限定访问哪些 Project 的资源）
- **工具调用的资源归属校验**：工具操作 ObjectType/Dataset 时，AuthorizationService 根据 project_id（选项 B fallback Ontology→Space→Project）校验

### 7.4 与 Organization / Space / Project 三层容器的协同（新增）

**资源归属变更后的连带影响**：

| 资源 | 归属变更 | 权限协同 |
|------|---------|----------|
| Ontology | 顶层独立 → 归属 Space（1:1） | Ontology 可见性受 Space 准入约束；现有 Ontology 需迁移到默认 Space |
| ObjectType/ActionType/LinkType 等 | 归属 Ontology → 加 project_id（nullable，选项 B） | 权限查询 project_id 优先 fallback Ontology 所属 Project |
| DataSource/Dataset/SyncTask/Credential | 平铺 → 归属 Project | 权限直接查 Project 角色；现有资源迁移到默认 Project |
| object_state/object_links | 间接归属（通过 Ontology→Space→Project） | RLS 过滤含 Organization/Space/Project 维度 |
| Scenario | 挂 Ontology → 间接归属 Space | Scenario 可见性受 Space 准入约束 |

**默认初始化（单租户兼容）**：
- 首次启动自动创建默认 Organization（如 `org-default`）+ 默认 Space（如 `default`，自动绑定默认 Ontology）+ 默认 Project（如 `default`）
- 现有 Ontology 迁移到默认 Space，现有资源迁移到默认 Project
- 单租户场景下用户无感知三层（渐进式披露，默认视图不暴露）

**跨组织协作**（未来多租户场景）：
- 共享 Space 双方 Organization 加白名单
- 共享 Project 内资源可跨组织授权（授对方 Organization 的 Group）
- 跨组织访问全量审计（标记为「外部访问」）

---

## 八、简化设计落地（"复杂留给自己，简单留给用户"）

> 基于研究 §3.3 的 10 条简化原则，给出 Gaia 具体落地手法。

### 8.1 从动作推断意图（HP 原则 1）

- 用户「把对象加入看板」= 授权查看该对象，无需弹窗
- 用户「分享 Scenario 给同事」= 授权该同事访问 Scenario
- 用户「创建 Space」= 自动创建同名 Ontology（1:1）+ 默认 Project，创建者自动成为 Space Owner + Ontology Owner + Project Owner，无需单独配
- 用户「创建 Ontology」→ 一期需先有 Space（1:1 约束），系统引导「在 Space 内创建」，不单独弹窗配权限
- **少弹窗，多推断**——避免 Just-Say-Yes 条件反射

### 8.2 策略即数据 + 前后端共享（Ship the Policy）

- 后端返回 `allowedActions` + `disabledReasons`，前端只渲染不推导
- 行级策略表达式序列化（JSON），前端可预览（不评估）
- 避免前后端各写一遍权限逻辑（研究 §3.2.2）

### 8.3 ABAC/Tag 优先，RBAC 兜底（避免 role explosion）

- 数据访问用 tag/属性驱动（一条策略覆盖海量资源）
- 协作授权用 RBAC（角色数严格控制）
- 行级用表达式（ABAC），不用「N 部门 × M 角色」

### 8.4 打标即保护（一期手动，二期自动传播）

- 给 ObjectType 打 PII 标记 → 无 PII 权限用户看不到
- 给 Property 打标记 → 序列化脱敏
- 一期手动打标 + 校验，二期血缘传播

### 8.5 渐进式披露（Progressive Disclosure）

- 默认视图：日常用例（查看数据、查询、执行已授权 Action），**单租户默认 Organization 不暴露三层管理**
- 高级面板：Organization/Space/Project 管理、标记管理、行级策略、角色配置（需明确进入「管理」模式）
- 默认安全 + 默认最简：新用户默认 Viewer，按需升级
- **三层容器的渐进披露**：单租户部署默认隐藏 Organization/Space 管理（只有一个默认值），用户主要在 Project 层操作；多租户场景才展开三层管理 UI

### 8.6 Just-in-Time 权限

- 临时需求走自助申请 + 自动审批 + 到期回收
- 不为临时场景预授常驻高权限
- 与 OIDC 联动，入职自动授权、离职自动失效

### 8.7 默认安全 + 不可见即安全，但提供「为什么」

- 无权限资源默认隐藏（不提示「无权限」，防枚举）
- 但当用户主动尝试访问被拒时，提供**可读的拒绝原因** + **申请权限入口**
- Check Access 工具：任意用户+资源，展示每层校验状态 + 权限来源

### 8.8 LLM 辅助策略生成（Gaia 差异化机会）

- 用户说自然语言：「销售只能看本区域客户」
- LLM 转成结构化 `RowSecurityPolicy.expression`：`principal.attributes['region'] == row['region']`
- 对齐 Gaia AI 原生定位（已有 /ai/generate /ai/scaffold）
- 业界尚无成熟实践，是 Gaia 差异化机会（二期）

### 8.9 系统承担复杂，用户只表达意图

- 血缘传播、策略下推、缓存一致性、多引擎适配——系统内部复杂
- 用户侧只表达业务意图：「这是 PII 数据」「销售只能看本区域」
- 表达式引擎（simpleeval）+ 标记体系封装底层复杂

### 8.10 分权治理 + 流程自动化

- Marking Admin / Space Owner / Project Owner / Audit Admin 四权分立（安全底线，对齐完整三层）
- 申请-审批-授权-回收全流程自动化（减少人工瓶颈）
- 治理左移：数据接入时建议打标（LLM 辅助），而非事后补标
- **三层管理权责分离**：Organization Admin 管主体隔离、Space Owner 管业务域容器、Project Owner 管协作权限、Marking Admin 管数据密级——互不兼任（权责分离底线）

---

## 九、风险、依赖与未决问题

### 9.1 风险

| 风险 | 等级 | 缓解 |
|------|:---:|------|
| Doris Row Policy 性能（复杂表达式下推） | 🟡 中 | 基准测试；表达式限制为等值匹配（研究 §3.2.5） |
| **Neo4j Community 无 FGAC** | 🟡 中 | 属性驱动 WHERE 过滤（非大列表 IN），对权限属性建索引；复杂表达式退化为 visible_ids |
| PG RLS 与 object_state OCC 冲突 | 🟡 中 | RLS 仅读过滤，写入走 ActionService（已有 OCC） |
| 多引擎权限一致性（Doris vs Trino 降级） | 🟡 中 | AuthorizationService 统一求值，各引擎下推策略一致 |
| 策略表达式安全（simpleeval 注入） | 🟡 中 | 表达式白名单函数 + 标识符校验（对齐 CLAUDE.md 红线 8） |
| 标记未传播导致下游数据失管 | 🟡 中 | 一期手动打标 + 校验；二期血缘传播；文档强调手动补标 |
| **资源归属迁移（现有资源加 space_id/project_id）** | 🟡 中 | Alembic migration + 默认 Space/Project 兜底；现有 Ontology/DataSource 迁移到默认值，向后兼容 |
| **Organization/Space/Project 默认初始化** | 🟢 低 | 首次启动自动创建默认 Org+Space+Project；单租户用户无感知 |
| **选项 B fallback 逻辑复杂度** | 🟢 低 | project_id 优先 + Ontology 所属 Project fallback，逻辑集中 AuthorizationService；二期选项 A 迁移后简化 |
| **Space↔Ontology 1:1 破坏性变更** | 🟡 中 | Ontology 加 space_id（nullable 先迁移，后 NOT NULL）；DEV/PROD 靠多实例（方案 3 待设计） |
| 性能：权限求值开销 | 🟢 低 | 进程内 LRU 缓存（短 TTL）+ 主动失效 |
| 向后兼容（ADR-011 JSON 权限） | 🟢 低 | 契约不变，内部切换，fallback 兼容 |

### 9.2 依赖

| 依赖 | 说明 |
|------|------|
| **OIDC IDP** | 需部署 Keycloak/Authelia（或对接现有 IDP） |
| **Doris 4.0.5** | 已有，原生支持 Row Policy/Column Masking |
| **PG 16** | 已有，原生 RLS |
| **Alembic** | 已有，schema 变更走 migration（CLAUDE.md 规范） |
| **simpleeval** | 已有（ADR-011 ActionRuleEngine 用） |

### 9.3 未决问题（需评审决策）

1. **是否一期就做 Organization 多租户层？**
   - 评估建议：不做（开源本地优先，单租户为主）
   - 但若 Gaia 有 SaaS 化计划，需提前预留

2. **策略引擎自建还是引入 OPA/Cerbos？**
   - 评估建议：一期自建（simpleeval），二期评估
   - 取决于策略复杂度增长

3. **PBAC 是否纳入？**
   - 评估建议：二期+（强合规场景可选）
   - 取决于目标行业（金融/政务/医疗）

4. **LLM 辅助策略生成的边界？**
   - 评估建议：二期探索（Gaia 差异化机会）
   - 哪些策略用 LLM 生成（自然语言→结构化），哪些保持确定性配置

5. **Trino 是否接入 Ranger？**
   - 评估建议：一期查询层补偿，二期评估 Ranger
   - 取决于 Trino 查询频率与性能要求

6. **标记血缘传播何时做？**
   - 评估建议：二期（依赖血缘引擎成熟度）
   - Gaia 已有 physical_mapping 基础，可扩展

7. **身份体系：OIDC 还是自建？**
   - 评估建议：OIDC 优先 + 自建兜底
   - 取决于部署环境（云/本地/气隙）

8. **Service User 作用域如何实现？**
   - 评估建议：scoped 限制（可访问的 Ontology/ObjectType/API）
   - 对齐 Palantir Scoped Service User（研究 §2.2）

9. **Space↔Ontology 1:1 的 DEV/PROD 隔离方案（待设计）**
   - 现状：短期照搬 Palantir 1:1，DEV/PROD 靠多实例部署
   - 待设计：Ontology 版本/分支机制（方案 3，见 §三 决策 2），让 DEV/PROD 是同一 Ontology 的不同版本/分支
   - 触发时机：出现真实 DEV/PROD 隔离需求且多实例成本不可接受时
   - 风险：与 Scenario（object_state overlay）概念可能重叠，设计时需厘清边界

## 十、评估结论与下一步

### 10.1 评估结论

1. **可行性**：✅ **高**。Gaia 现有架构（object_state OCC + outbox + ActionAuthorizer + 多引擎）对权限体系支撑度高，与 Palantir 契合度评估为「高」（研究 §4.4）。无需颠覆性改造，增量建设即可。

2. **引入合理性**：Palantir 五层模型**完整引入** Gaia（Organization → Space → Project → Marking → 行/列级），对齐企业级安全模型。资源归属走选项 B 简化（定义归 Ontology，预留 project_id），保留 Marking/行级安全/组授权/审计治理等核心。Space 基础设施绑定舍弃（Gaia 无 Spark）。

3. **分期合理性**：Phase 0-5 共 6 期，每期独立交付价值，总工期 14-20 天。身份基石+三层容器 → Project RBAC → Marking → 行/列级下推 → 审计治理 → 前端。

4. **简化落地**：10 条简化原则有具体 Gaia 落地手法，LLM 辅助策略生成是差异化机会。

5. **风险可控**：主要风险在多引擎权限一致性与性能，均有缓解方案。

### 10.2 下一步

1. **评审本评估报告**，确认 §三 的 9 个核心决策与 §9.3 的 9 个未决问题
2. 评审通过后，撰写 **ADR-016（权限治理体系）**，固化决策
3. 撰写 **权限治理设计文档**（`docs/design/permission-governance-design.md`），细化数据模型、查询下推、API 路由、前端交互
4. 按 Phase 0-5 分期实施，每期 TDD（先测试后实现，CLAUDE.md 规范）
5. 每期更新 `implementation-status.md` 对应章节

### 10.3 与现有特性的协同清单

| 现有特性 | 协同点 | 影响 |
|---------|--------|------|
| ADR-011 Action 三层权限 | ActionAuthorizer 内部切换到 AuthorizationService | 契约不变，内部替换 |
| ADR-009 工具层（22 工具） | ToolExecutor 注入 Principal，工具声明权限 | 增加权限校验切面 |
| ADR-015 图推理（ObjectSet IR） | DataFrameQueryService 入口校验 base 权限 | 增加权限前置校验 |
| Scenario 沙箱（scenario-*.md） | 权限独立于 scenario_id，base 权限先行 | 正交，无冲突 |
| ADR-014 多源融合 | VIRTUAL 目标权限走 Trino 联邦 | SQL 注入 filter 谓词下推 |
| TextQL（ADR-012） | NL 查询经 Agent，Agent 以 Principal 身份 | 工具层权限覆盖 |

---

> **本文结束**。评估结论与建议供评审，落地决策写入 ADR-016 + 设计文档。
