# 权限治理体系 —— 详细设计文档

> **范围**：基于 [ADR-016](../architecture/adr-016-permission-governance.md)（架构决策）与 [ADR-017](../architecture/adr-017-permission-tech-stack.md)（技术选型），细化 Gaia 权限治理体系的数据模型、Service 层、API 路由、查询下推、前端交互、数据库迁移、测试策略，支撑直接编码实现。
> **关联**：[ADR-016](../architecture/adr-016-permission-governance.md)（架构决策）· [ADR-017](../architecture/adr-017-permission-tech-stack.md)（技术选型：Cedar + cashews + Better Auth + SqlGlot）· [评估报告](../architecture/permission-governance-landing-assessment.md)（评估依据）· [ADR-011](../architecture/adr-011-action-p1.md)（Action 权限雏形，internals 切换）· [ADR-009](../architecture/adr-009-ontology-tool-layer.md)（工具层权限切面）· [技术选型研究](../research/permission-tech-stack-deep-dive.md)（深度选型依据）
> **日期**：2026-07-08
> **实施**：按 [评估报告 §四](../architecture/permission-governance-landing-assessment.md#四分期实施路线评估建议) Phase 0-5 分期，每期 TDD

---

## 目录

- [〇、设计哲学与核心概念](#〇设计哲学与核心概念)
- [一、数据模型设计（完整 ORM）](#一数据模型设计完整-orm)
- [二、Service 层设计](#二service-层设计)
- [三、AuthMiddleware 与 Principal 注入](#三authmiddleware-与-principal-注入)
- [四、查询层权限下推（多引擎）](#四查询层权限下推多引擎)
- [五、ActionService 改造（ADR-011 协同）](#五actionservice-改造adr-011-协同)
- [六、工具层改造（ADR-009 协同）](#六工具层改造adr-009-协同)
- [七、API 路由设计](#七api-路由设计)
- [八、前端交互设计](#八前端交互设计)
- [九、数据库迁移](#九数据库迁移)
- [十、测试策略](#十测试策略)

---

## 〇、设计哲学与核心概念

> 本章沉淀设计背后的**为什么**——核心原则、关键概念、模型选型理由、分层思想、演进考量。后续章节（§一-§十）是「怎么做」的实现细节，本章是「为什么这样做」的指导思想。开发者遇到本文档未覆盖的新场景时，应回溯本章原则自行推导，而非机械套用实现。

### 0.1 核心设计原则

以下原则是权限体系的所有设计的出发点，任何实现细节都不得违背。

#### 原则 1：做正确的事，不逃避架构复杂度

完整引入 Palantir 五层隔离模型（Organization + Space + Project + Marking + 行/列级），而非裁剪为简化版。企业级安全基座没有捷径——逃避复杂度只会把问题推迟到生产事故时爆发。单租户场景下默认一个 Organization + 一个 Space，通过渐进式披露不让复杂度传导给用户，但底层能力必须完整。

#### 原则 2：把复杂留给自己，把简单留给用户

Gaia 第一原则（见 CLAUDE.md）。权限体系天然复杂，但用户侧应只表达业务意图（「这是 PII 数据」「销售只能看本区域」），系统内部承担血缘传播、策略下推、缓存一致性、多引擎适配等技术复杂度。具体落地手法见 §0.7。

#### 原则 3：组授权铁律

100% 权限授予 Group，零直接个人授权。人员异动（入职/转岗/离职）只调 Group 成员，不改资源权限。这是权限可治理、可审计、可运维的基础。违反此原则会导致僵尸权限泛滥、审计困难、规模化后管理成本爆炸。

#### 原则 4：默认拒绝 + 不可见即安全

未显式授权的访问一律拒绝（白名单机制）。无权限资源在前端/搜索/API/SQL 完全隐藏，不提示「无权限」，防枚举探测（攻击者无法判断资源是否存在）。但用户主动尝试访问被拒时，须提供可读的拒绝原因 + 申请权限入口（Check Access）——平衡「不可见即安全」与「用户能理解为什么」。

#### 原则 5：权限只收紧不放宽

ABAC/Tag policy 不授予权限，只增加限制。基础访问权限通过 RBAC 角色授予，行/列级 policy 在此基础上进一步收紧。多层校验叠加取最严结果，任何一层的安全约束不会被其他层抵消。这保证了配置错误不会意外放宽权限。

#### 原则 6：权责分离

四权分立（Marking Admin / Space Owner / Project Owner / Audit Admin），互不兼任：
- Marking Admin 管数据密级定义与授权，不管项目
- Space Owner 管业务域容器，不管数据密级
- Project Owner 管协作权限，不管全局配置
- Audit Admin 仅看审计日志，无任何操作权限

防止单一角色即可完全放开数据权限（Palantir 的核心安全设计）。

#### 原则 7：权限下推到存储层

行/列级过滤必须下推到存储引擎，不在应用层后过滤（查完数据再 Python 过滤，可被绕过——抓包/改参数/直接 API，是虚假安全）。业界共识（Databricks/Snowflake/Immuta/Ranger 都下推）。Gaia 采用 **SqlGlot AST 注入统一机制**——谓词在查询发给引擎前注入 SQL WHERE，引擎在 scan 节点执行过滤，无权数据不离开引擎（详见 [ADR-017 D4](../architecture/adr-017-permission-tech-stack.md#d4-行级下推--sqlglot-ast-注入统一机制放弃-doris-原生-row-policy)）。

#### 原则 8：策略即数据 + 前后端共享

权限规则序列化（YAML/JSON），一处定义处处可用。后端返回 `allowedActions` + `disabledReasons`，前端只渲染不推导。避免前后端各写一遍权限逻辑导致 drift（[Ship the Policy](https://www.jayfreestone.com/writing/share-the-policy-not-the-code/)）。

### 0.2 关键概念释义

以下概念是理解权限体系的前提，容易混淆，务必区分清楚。

#### Organization / Space / Project / Ontology 四者的本质区别

| 概念 | 本质 | 管什么 | 一句话 |
|------|------|--------|--------|
| **Organization** | 主体隔离层（MAC） | 谁的人（哪家公司/主体） | 管主体租户隔离 |
| **Space** | 业务域容器 + 本体生命周期载体 | 哪个业务域的数据 | 管业务域语义隔离 |
| **Project** | 协作权限边界（DAC，原子单位） | 哪个协作单元（谁能干什么） | 管协作权限 |
| **Ontology** | 业务语义模型 | 业务对象定义（ObjectType/ActionType...） | 管业务语义 |

四者是不同维度的概念，不能互相顶替。层级关系：Organization → Space → (Ontology 1:1 + Project)。

#### Project ≠ Ontology（关键澄清）

这是设计中最容易犯的错误。Project 是**协作单元**（给一组人授权，管「谁能在这个工作空间干什么」），Ontology 是**语义模型**（管「这个业务域有哪些对象类型定义」）。两者正交：
- 一个业务域可拆多个 Project（数据团队/本体团队/应用团队权限分离），共享一个 Ontology
- 一个 Ontology 的定义可放不同 Project（选项 A），但 Ontology 本身归属 Space
- 用 Ontology 顶替 Project 会丢失「同本体、不同协作边界」的表达能力

#### Ontology 定义 vs 实例的双重归属

Ontology 的资源有**双重身份**（对齐 Palantir）：
- **定义**（ObjectType/ActionType/LinkType schema）—— 权限归 Project 管（选项 A）或 Ontology（选项 B fallback）
- **实例**（object_state/object_links 实际数据）—— 权限跟 backing dataset 走（实例数据在 dataset 里）

即「定义谁能改 schema」和「实例谁能看数据」是两套权限，不要混淆。

#### ObjectTypeGroup vs Project 正交

- **ObjectTypeGroup**（Gaia 已有空壳模型）：Ontology 内的语义分组（如「应收对象组」），无权限语义，纯分类，帮用户搜索浏览本体。对齐 Palantir [Object type groups](https://palantir.com/docs/foundry/object-link-types/type-groups/)
- **Project**：协作权限边界，跨资源（含 Ontology + Dataset + 代码）

两者正交，不能互相替代。引入 Project 不破坏 ObjectTypeGroup 的语义。

#### Space↔Ontology 1:1 的哲学

1:1 强绑定（创建 Space 自动建同名 Ontology，Space 删除 Ontology 不可恢复，跨 Space 不可复用本体）是 Palantir 核心哲学——保证**业务域语义统一**，防止「同业务对象在多个本体重复定义」的数据孤岛。这约束很硬，但价值在于长期防止语义碎片化。实践中本体膨胀靠 ObjectTypeGroup 解决，权限细分靠 Project + Marking，不靠拆 Ontology。

### 0.3 权限模型选型理由

#### 为什么 RBAC + ABAC(Tag) 混合

业界共识（Databricks/Snowflake/Immuta/Ranger 都是此模式）：
- **RBAC 管协作授权**（谁能进 Project、能做什么操作）—— 直观，易管理
- **ABAC/Tag 管数据访问**（能看哪些行/列）—— 细粒度，可扩展，避免 role explosion

#### 为什么不用纯 RBAC

Role Explosion：N 部门 × M 角色 = 组合爆炸。50 ObjectType × 4 角色 = 200 角色组合，管理成本指数上升（[GigaOm 报告](https://www.immuta.com/resources/gigaom-report-immuta-vs-apache-ranger/)：ABAC 比 RBAC 减少 75x 策略变更）。

#### 为什么不用纯 ABAC

协作授权场景 RBAC 更直观。纯 ABAC 表达「张三能编辑财务 Project」不如「张三在财务-editor 组」清晰。

#### 为什么不用 ReBAC（关系图授权）

ReBAC（Google Zanzibar）适合层级继承/共享场景（文件系统、文档协作），但**行/列级安全不是 ReBAC 强项**（行级是属性过滤，非关系遍历）。ObjectType 间 Link 关系权限可考虑 ReBAC（二期评估），但一期行/列级用 ABAC/Tag。

### 0.4 权限校验分层思想

#### 五层串行 vs 行/列下推

权限校验分两部分：
- **上四层（身份/Org/Space/Project/Marking）**：在 `AuthorizationService`（PDP）集中求值，决定「能不能看这个资源」。任一层拒即终止
- **第五层（行/列级）**：下推到存储引擎，决定「能看到哪些行/列」

这是 XACML 的 PDP/PEP 分离思想：决策（PDP）与执行（PEP）解耦。PDP 集中求值保证一致性，PEP 下推保证不可绕过。

#### PDP / PIP / PEP 架构

| 角色 | 职责 | Gaia 实现 |
|------|------|----------|
| **PDP**（决策点） | 求值策略，返回允许/拒绝 | `AuthorizationService` |
| **PIP**（信息点） | 提供求值数据（principal/resource 属性） | `PrincipalService` + meta_store |
| **PEP**（执行点） | 拦截请求调用 PDP | 各 Service + Doris/PG/Trino/Neo4j |

### 0.5 资源归属策略的演进考量

#### 为什么选项 B（简化）而非选项 A（完整）

选项 A（Ontology 定义可放 Project）是为「大型企业多团队权限细分」设计的（本体团队/数据团队/前端团队各管各的 Project）。Gaia 当前无 Spark/Transform/Workshop 应用体系，多团队细分需求不强。选项 B 避免该拆分的复杂度，但**数据模型预留 `project_id`** 确保 B→A 可平滑迁移。

#### B→A 迁移路径

Palantir 自己也是从简单模型（Ontology Roles）演进到 Project-based 权限的（有[官方迁移工具](https://palantir.com/docs/foundry/ontology-manager/migrate-to-project-based-permissions/)）。Gaia 的迁移：填充 `project_id` + 切换权限查询逻辑（`project_id` 优先 fallback Ontology），可渐进迁移，不需一次性全切。关键是**一期不要把「定义归 Ontology」做死**，预留迁移空间。

#### 方案 3（Ontology 版本/分支）作为待设计项

Space↔Ontology 1:1 在 DEV/PROD 隔离上有局限（Gaia 无 Global Branching）。短期用多实例部署解决，长期设计 Ontology 版本/分支机制（DEV/PROD 是同一 Ontology 的不同版本）。**触发时机**：出现真实 DEV/PROD 需求且多实例成本不可接受时。设计时须厘清与 Scenario overlay 的边界。

### 0.6 与现有特性的协同边界

#### 与 Scenario 协同：权限独立于 scenario_id

权限校验在 scenario overlay 求值**之前**执行，scenario 内 overlay 数据继承 base 权限。scenario_id 与权限字段正交。Scenario 不应绕过 base 权限——它是「假设推演」不是「权限逃逸通道」。

#### 与 Action 协同：契约不变，internals 切换

ADR-011 的 `ActionAuthorizer` 三层契约不变（返回 forbidden set），只换 internals（从 JSON permissions 切换到 AuthorizationService 五层校验）。这样旧 ActionType 定义不破坏，调用方无感知。

#### 与工具层协同：Principal 注入

工具层（ADR-009）的 `ToolExecutor` 从 request.state 取 Principal，每个工具声明所需权限。AG-UI Agent 以 Principal 身份执行（继承人类用户或 Service User），不绕过权限。

### 0.7 简化设计的落地哲学

基于 [研究 §3](../research/palantir-permission-review-and-industry-comparison.md#三复杂留给自己简单留给用户设计哲学) 的 10 条简化原则，关键落地手法：

#### 从动作推断意图（HP 原则）

HP 实验室《Making Policy Decisions Disappear into the User's Workflow》核心：从用户的指代动作推断授权意图，而非打断询问。落地：创建 Space 自动创建 Ontology+Project+三层 Owner，用户无需单独配权限。少弹窗，避免 Just-Say-Yes 条件反射。

#### 渐进式披露

默认视图匹配日常工作（查看数据、查询、执行已授权 Action），单租户默认 Organization 不暴露三层管理。高级面板（标记/策略/角色/三层容器管理）需明确进入「管理」模式。最安全最常用的控件先显示，强大/危险的控件后揭示。

#### LLM 辅助策略生成（Gaia 差异化机会）

用户说自然语言（「销售只能看本区域客户」），LLM 转成结构化 `RowSecurityPolicy.expression`（`principal.attributes['region'] == row['region']`）。对齐 Gaia AI 原生定位（已有 /ai/generate /ai/scaffold）。业界尚无成熟实践，是 Gaia 差异化机会（二期）。

#### 系统承担复杂，用户只表达意图

血缘传播、策略下推、缓存一致性、多引擎适配——系统内部复杂。用户侧只表达业务意图。Cedar 策略语言 + 标记体系封装底层复杂。

---

## 一、数据模型设计

> 新建 `core/models/permission.py`。遵循 CLAUDE.md 编码规范：SQLAlchemy 2.0 async ORM，UUID hex 主键，`datetime.now(UTC)` 时间戳，ORM 与 pydantic schema 分离。本节先讲**设计意图与约束**，再给关键字段；完整字段见代码。

### 1.0 数据模型总览：四组表的职责划分

权限数据模型分四组，每组职责单一，组间通过外键关联，不互相嵌套：

```
第一组：三层容器（资源归属的骨架）
  Organization → Space → Project
  Space ↔ Ontology 1:1 强绑定

第二组：身份层（谁）
  Principal (抽象基类) ← User / Group / ServiceUser
  Group ↔ User 通过 GroupMembership

第三组：权限规则（能做什么）
  Role + RoleAssignment     → 协作授权（RBAC）
  Marking + MarkingGrant + MarkingAssignment → 数据强制访问（MAC）
  RowSecurityPolicy + PropertyMaskingPolicy  → 行/列级细粒度（ABAC）

第四组：治理凭证（事后追溯）
  AuditLog（追加写入）
  AccessRequest（JIT 申请）
```

**设计约束**：四组表只通过 ID 外键关联，不跨组做 JOIN 级联（避免权限求值时多表 JOIN 拖慢）。权限求值（AuthorizationService）按需分步查这四组表，结果缓存。

### 1.1 三层容器（Organization / Space / Project）

#### 为什么是这三层，不是两层或四层

这三层对应三个正交的隔离维度，缺一不可，也不可合并：

- **Organization** 回答「这是谁的数据」——主体隔离。集团子公司、供应商、客户必须在主体层强隔离，否则一旦上层配置错误就横向泄露。这是 MAC（强制访问控制），用户不可自行绕过。
- **Space** 回答「这是哪个业务域的数据」——语义隔离。一个 Space 绑定一个 Ontology，保证同业务域语义统一（防止 `Invoice` 在多个本体重复定义导致数据孤岛）。这是 Palantir 最硬的约束之一。
- **Project** 回答「谁能在这组资源上协作」——权限边界。Project 是权限的原子单位（继承单位 + 缓存单位），同 Project 内资源权限统一，跨 Project 必须显式引用。

**为什么不能合并**：Organization 是主体维度（跨业务域），Space 是业务域维度（同主体内），Project 是协作维度（同业务域内不同团队）。合并任两个都会丢失一个维度的隔离能力。例如把 Space 合并进 Project，就失去「同业务域语义统一」的保证（多个 Project 可能各定义一套本体）。

**为什么不再多加一层**：Palantir 没有第四层，三层已覆盖主体/业务/协作三个维度。多加只会增加认知负担，无新隔离价值。

#### Organization：系统级 MAC 标记的载体

Organization 的核心不是它本身是个「分组」，而是它**派生一个系统级内置 Marking**——所有归属该组织的用户自动持有该标记，所有归属该组织的资源自动打该标记。这是主体强隔离的底层实现（对齐 Palantir：Organization 是 access requirement 的一种，与 Marking 并列）。

```python
class OrganizationModel(Base):
    __tablename__ = "organizations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    api_name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    org_type: Mapped[str] = mapped_column(String(20), default="INTERNAL")  # INTERNAL | EXTERNAL
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    # 创建时自动派生系统 Marking（见 §1.4）；删除时级联删除该 Marking
```

**字段含义**：
- `org_type`：INTERNAL（内部员工主组织）/ EXTERNAL（供应商/客户）。影响默认可见性策略（外部组织默认完全隐藏）。
- `status`：DISABLED 时该组织所有用户权限即时失效（离职批量处理）。

**关键约束**：
- 单租户部署默认创建一个 `org-default`，用户无感（渐进式披露）
- Organization 之间默认完全隔离，跨组织协作只能通过共享 Space（双方加白名单）
- Organization 不可手动删除其系统标记（系统维护，对齐 Palantir）

#### Space：与 Ontology 1:1 强绑定（最硬的约束）

Space 的核心约束是 `ontology_id` 的 `unique=True`——**一个 Space 只能有一个 Ontology**，创建 Space 自动创建同名 Ontology，Space 删除则 Ontology 不可恢复。这是整个设计中最不可妥协的约束。

```python
class SpaceModel(Base):
    __tablename__ = "spaces"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    api_name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    ontology_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("ontologies.id", ondelete="RESTRICT"),
        unique=True, nullable=False,  # 1:1 强绑定
    )
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
```

**为什么 1:1 而非 1:N**：1:N 会让同一业务对象在多个本体重复定义，血缘断裂，无法 JOIN。1:1 强制「一个业务域一套语义」，本体膨胀靠 ObjectTypeGroup（语义分组）解决，权限细分靠 Project + Marking，不靠拆 Ontology。

**为什么 ondelete=RESTRICT 而非 CASCADE**：Ontology 是核心业务资产，删除不可逆。RESTRICT 强制先解绑 Space 再删 Ontology，防止误删。Space 删除时 Ontology 应先迁移或显式确认。

**舍弃的 Palantir 能力**：Palantir 的 Space 还绑定基础设施（Spark 队列/存储/加密/计费，创建后不可改）。Gaia 无 Spark，存储多引擎统一管，故舍弃——只保留「容器 + 组织白名单 + 本体绑定」语义。

**跨组织协作通道**：Space 通过 `SpaceOrganizationModel` 白名单关联多个 Organization。只有白名单内组织的用户才能访问该 Space。这是跨组织协作的唯一合法通路（无其他旁路）。

#### Project：权限原子单位（不是 Ontology）

Project 是**权限的继承单位 + 缓存单位**，不是语义单位。这是与 Ontology 的根本区别——Ontology 管语义，Project 管协作权限。一个 Space 下可有多个 Project（数据团队/本体团队/应用团队权限分离），共享一个 Ontology。

```python
class ProjectModel(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    api_name: Mapped[str] = mapped_column(String(255), nullable=False)  # space 内唯一
    space_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("spaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    __table_args__ = (UniqueConstraint("space_id", "api_name", name="uq_projects_space_api_name"),)
```

**关键约束**：
- `api_name` 在 Space 内唯一（非全局），因为 Project 属于 Space
- 资源（Dataset/SyncTask/Datasource/Credential）通过各自表的 `project_id` 归属 Project
- 定义类资源（ObjectType/ActionType 等）一期归属 Ontology，预留 `project_id`（选项 B，见 §0.5）
- Project 是权限缓存单位：用户对某 Project 的角色计算一次后缓存，Project 内资源复用（对齐 Palantir）

### 1.2 身份层：Principal 抽象 + 三类主体

#### 为什么用 Principal 抽象基类

权限的授予对象可能是 User、Group、ServiceUser 三类主体。用 `PrincipalModel` 作为抽象基类（多态），让 RoleAssignment/MarkingGrant 等表统一引用 `principal_id`，不必为每类主体建独立的授权表。

```python
class PrincipalModel(Base):
    __tablename__ = "principals"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    principal_type: Mapped[str] = mapped_column(String(20), nullable=False)  # USER | GROUP | SERVICE_USER
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")  # ACTIVE | DISABLED
```

**设计权衡**：也可不用多态基类，直接在 RoleAssignment 里加 `principal_type + principal_id`。但多态基类让主体有统一的 status/display_name 管理，且未来加新主体类型（如机器组）不破坏授权表。

#### User：自然人，attributes 是行级安全的数据源

User 的关键字段是 `attributes`（JSONB）——存部门/区域/职级等从 OIDC 同步的属性。这些属性是 RowSecurityPolicy 表达式的求值依据（如 `principal.attributes['region'] == row['region']`）。

```python
class UserModel(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)  # = principal_id
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    subject: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)  # OIDC sub
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONBType, default=dict)
    home_organization: Mapped[str | None] = mapped_column(String(32), ForeignKey("organizations.id"))
```

**字段含义**：
- `subject`：OIDC IDP 侧的唯一标识（sub claim），用于 token 验证时映射到 Gaia User。不直接用 email（email 可改，sub 不可改）。
- `attributes`：从 OIDC claims 同步（部门/区域/职级）。**这是行级安全的关键**——行级策略表达式引用这些属性做过滤。属性变更（转岗）自动联动行级权限。
- `home_organization`：主组织（唯一）。单租户可空；多租户时决定默认可见范围。

**为什么 attributes 用 JSONB 而非关系表**：属性是扁平 key-value，查询模式简单（按 key 取值），JSONB 足够且灵活（IDP claims 结构可变）。关系表会过度设计。

#### Group：权限唯一载体（铁律）

**组授权铁律**：100% 权限授 Group，不授个人。User 通过加入 Group 获权。这是可治理的基础——人员异动只调 Group 成员，不改资源权限。

```python
class GroupModel(Base):
    __tablename__ = "groups"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    organization_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )  # 组归属唯一组织（跨组织不能复用组）
    parent_group_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("groups.id", ondelete="CASCADE"), nullable=True
    )  # 嵌套（建议 ≤ 2 层）
```

**关键约束**：
- `organization_id` 必填：组归属唯一组织，跨组织不能复用（对齐 Palantir）。这是组织隔离的完整性保证——组不会成为跨组织权限渗透的通道。
- `parent_group_id`：支持嵌套（子组继承父组权限），但建议 ≤ 2 层。深层嵌套导致权限来源不透明、意外越权。
- 成员关系通过 `GroupMembershipModel`（group_id + user_id 联合主键）。

**为什么不直接授个人**：人员变动后权限清理不及时→僵尸权限泛滥；无法批量管理；审计困难。组授权让人力异动与权限配置解耦。

#### ServiceUser：Agent/API 专用，scoped 限制

ServiceUser 是非自然人主体，供 Agent/API/流水线集成用。关键设计是 `scopes`——限定可访问的 Project/ObjectType/API，即使密钥泄露也只能在限定范围操作。

```python
class ServiceUserModel(Base):
    __tablename__ = "service_users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    scopes: Mapped[list] = mapped_column(JSONBType, default=list)  # 作用域限制
    owner: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), nullable=False)  # 负责人
```

**关键约束**：
- 零默认权限：创建时不继承组织默认权限，须手动授予（对齐 Palantir）
- 一用一号：每个集成场景独立 ServiceUser（便于审计 + 权限回收）
- `owner` 必填：明确负责人，密钥轮换/权限评审有人跟进
- 实践建议（非硬约束）：默认 Viewer，确需 Editor 才授，严禁 Owner

### 1.3 角色层：Role = Operations 的集合

#### 角色的本质：操作的打包

对齐 Palantir：「Roles are sets of operations」。角色不是抽象概念，而是**一组原子操作的集合**。授角色 = 授一组操作 + 子资源继承。一期不单独建 Operation 表，存 `RoleModel.permissions` JSONB（操作列表）。

```python
class RoleModel(Base):
    __tablename__ = "roles"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    # OWNER | EDITOR | VIEWER | DISCOVERER | SPACE_OWNER | SPACE_EDITOR |
    # PLATFORM_ADMIN | AUDIT_ADMIN | MARKING_ADMIN
    scope_type: Mapped[str] = mapped_column(String(20), nullable=False)  # GLOBAL | SPACE | PROJECT
    permissions: Mapped[list] = mapped_column(JSONBType, default=list)  # 原子操作列表
```

**角色分层与权责分离**：

| 层级 | 角色 | scope_type | 权责分离 |
|------|------|:---:|------|
| 全局 | PLATFORM_ADMIN | GLOBAL | 平台管理，默认无数据权限 |
| 全局 | AUDIT_ADMIN | GLOBAL | 仅看审计，无操作权限 |
| 全局 | MARKING_ADMIN | GLOBAL | 管标记定义/授权，不管项目 |
| Space | SPACE_OWNER/EDITOR | SPACE | 业务域容器管理，继承到所有 Project |
| Project | OWNER/EDITOR/VIEWER/DISCOVERER | PROJECT | 协作权限边界（最常用） |

**权责分离是安全底线**：MARKING_ADMIN 管数据密级但不管项目；PROJECT_OWNER 管协作但不管密级。防止单一角色即可完全放开数据权限。PLATFORM_ADMIN 默认无数据访问权限（管权限的不看数据，看数据的不改权限）。

#### RoleAssignment：授 Group，scope 指向 Space 或 Project

```python
class RoleAssignmentModel(Base):
    __tablename__ = "role_assignments"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    principal_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)  # 通常 group_id
    role_id: Mapped[str] = mapped_column(String(32), ForeignKey("roles.id"), nullable=False)
    scope_type: Mapped[str] = mapped_column(String(20), nullable=False)  # SPACE | PROJECT
    scope_id: Mapped[str] = mapped_column(String(32), nullable=False)  # space_id 或 project_id
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # 临时权限到期
```

**关键设计**：
- `principal_id` 通常指向 Group（铁律：授 Group 不授个人）
- `scope_type + scope_id`：角色的作用域。SPACE 级角色继承到所有 Project；PROJECT 级角色只在该 Project 生效
- `expires_at`：JIT 权限到期自动回收（避免僵尸权限）
- 选项 B fallback：定义类资源 `project_id` 为空时，权限查 Ontology 所属 Space 的默认 Project

### 1.4 标记层：MAC 合取校验（AND）

#### Marking 的核心机制：布尔合取

Marking 是数据级强制访问控制（MAC），高于 RBAC 角色权限的「硬门槛」。核心是**布尔合取**：资源带 N 个标记，用户必须**同时持有全部 N 个**才能访问，缺一不可。哪怕是 Project Owner，缺标记也看不到数据。

这与 RBAC 的「或」逻辑不同——RBAC 是「持有任一角色即可」，Marking 是「全部满足才可」。这种差异是设计核心：RBAC 管协作灵活性，Marking 管数据强制兜底。

```python
class MarkingCategoryModel(Base):  # 标记分类（数据密级/敏感类型/业务分区）
    __tablename__ = "marking_categories"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)  # Organization 派生的系统分类

class MarkingModel(Base):  # 标记值（机密/PII/华东）
    __tablename__ = "markings"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    category_id: Mapped[str] = mapped_column(String(32), ForeignKey("marking_categories.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    source_organization_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("organizations.id", ondelete="CASCADE"))
```

**Organization ↔ Marking 联动**：
- 创建 Organization → 自动派生系统 Marking（`is_system=True`, `source_organization_id` 指向该 Org）
- 该 Org 的用户自动持有该 Marking，该 Org 的资源自动打该 Marking
- 系统标记不可手动移除（系统维护，对齐 Palantir）
- 这是主体强隔离的底层实现

#### MarkingGrant：授 Group 标记权限

```python
class MarkingGrantModel(Base):
    __tablename__ = "marking_grants"
    group_id: Mapped[str] = mapped_column(String(32), ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True)
    marking_id: Mapped[str] = mapped_column(String(32), ForeignKey("markings.id", ondelete="CASCADE"), primary_key=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
```

**权责分离**：MarkingGrant 由 MARKING_ADMIN 授予（管密级的人），不由 PROJECT_OWNER 授予（管项目的人不能放开密级）。这是 MAC 与 DAC 的分权设计。

#### MarkingAssignment：资源打标

```python
class MarkingAssignmentModel(Base):
    __tablename__ = "marking_assignments"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    resource_type: Mapped[str] = mapped_column(String(20), nullable=False)  # OBJECT_TYPE | PROPERTY | ONTOLOGY | DATASET
    resource_id: Mapped[str] = mapped_column(String(32), nullable=False)
    marking_id: Mapped[str] = mapped_column(String(32), ForeignKey("markings.id", ondelete="CASCADE"), nullable=False)
    is_directly_applied: Mapped[bool] = mapped_column(Boolean, default=True)
```

**关键约束**：
- `resource_type + resource_id`：多态引用（ObjectType/Property/Ontology/Dataset 都可打标）
- `is_directly_applied`：对齐 Palantir，区分直接打标 vs 继承打标（一期不做血缘传播，全直接打标）
- 一期手动打标，不做血缘自动传播（二期视血缘引擎成熟度）
- **治理红线**：全局标记 ≤ 20 个，分类 ≤ 3（防标记爆炸；细粒度部门级隔离用 RowSecurityPolicy 不用 Marking）

### 1.5 行/列级安全：ABAC 表达式 + 多引擎下推

#### 行级与列级是两层，组合成 cell 级

- **RowSecurityPolicy**（ObjectType 级）：行级过滤，表达式引用 `principal.attributes`，决定「能看到哪些行」
- **PropertyMaskingPolicy**（Property 级）：列级脱敏，不满足表达式返回 null，决定「能看到哪些列」
- 两者组合 = **cell 级**（行×列交叉，对齐 Palantir Object + Property Security Policy）

这是 ABAC（属性驱动），不是 RBAC——一条策略覆盖海量资源，避免 role explosion。

```python
class RowSecurityPolicyModel(Base):
    __tablename__ = "row_security_policies"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    object_type_id: Mapped[str] = mapped_column(String(32), ForeignKey("object_types.id", ondelete="CASCADE"), nullable=False)
    expression: Mapped[str] = mapped_column(Text, nullable=False)
    # Cedar 策略表达式，例：principal.attributes.region == resource.region

class PropertyMaskingPolicyModel(Base):
    __tablename__ = "property_masking_policies"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    property_id: Mapped[str] = mapped_column(String(32), ForeignKey("properties.id", ondelete="CASCADE"), nullable=False)
    expression: Mapped[str] = mapped_column(Text, nullable=False)
    # 不满足则返回 null，例：'PII' in principal.markings
```

**表达式引擎**：用 [Cedar](https://github.com/cedar-policy/cedar)（cedarpy），**不用 simpleeval**。Cedar 是非图灵完备专用策略语言，类型安全 + schema 验证 + TPE 残差下推（详见 [ADR-017 D1](../architecture/adr-017-permission-tech-stack.md#d1-策略求值与表达式引擎--cedarcedarpy)）。表达式引用 `principal.attributes`（行级）或 `principal.markings`（列级）。

**安全约束**（Cedar 语言级保证，对齐 CLAUDE.md 红线 8）：
- 非图灵完备，无任意函数调用/属性反射/循环/递归（语言设计层面排除，非黑名单过滤）
- 类型系统 + schema 验证，策略加载时校验标识符与类型（部署前拦截错误）
- 不支持动态脚本/外部调用（确定性策略）

**下推机制**（详见 §四）：
- Doris/Trino/PG：Cedar TPE 产生残差 → 翻译为 SQL 谓词 → SqlGlot AST 注入 WHERE（统一机制）
- PG object_state 写入：RLS WITH CHECK（PG 独特写入校验能力）
- Neo4j：Cypher WHERE 属性驱动过滤

### 1.6 审计层：追加写入，不可篡改

```python
class AuditLogModel(Base):
    __tablename__ = "audit_logs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    principal_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    resource_type: Mapped[str] = mapped_column(String(50), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    result: Mapped[str] = mapped_column(String(10), nullable=False)  # ALLOW | DENY
    reason: Mapped[str] = mapped_column(Text, default="")  # 哪一层拦截
    layer: Mapped[str | None] = mapped_column(String(20), nullable=True)  # ORG|SPACE|PROJECT|MARKING|ROW
```

**不可篡改的实现**：
- 应用层不提供 UPDATE/DELETE 接口
- DB 角色权限限制（audit_logs 表只授予 INSERT + SELECT，不授 UPDATE/DELETE）
- 追加写入模式（只新增不改历史）

**`layer` 字段的价值**：记录哪一层拦截（ORG/SPACE/PROJECT/MARKING/ROW），便于 Check Access 可解释性 + 审计分析（哪层拒绝最多→配置问题排查）。

**AccessRequest**：JIT 权限申请（PENDING→APPROVED/REJECTED/EXPIRED），支持临时权限到期回收。

### 1.7 现有模型改造：资源归属字段

引入三层后，现有资源要加归属字段。这是 Phase 0 的 schema 变更核心。

```python
# OntologyModel 加 space_id（1:1 强绑定）
space_id: Mapped[str | None] = mapped_column(
    String(32), ForeignKey("spaces.id", ondelete="RESTRICT"), nullable=True, unique=True
)
# nullable=True 先迁移，后改 NOT NULL（见 §九迁移）

# DataSource/Dataset/SyncTask/Credential 加 project_id（资源归属 Project）
project_id: Mapped[str | None] = mapped_column(
    String(32), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True
)

# 定义类资源（ObjectType/ActionType/LinkType/InterfaceType/SharedPropertyType）加 project_id（nullable，选项 B 预留）
project_id: Mapped[str | None] = mapped_column(
    String(32), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
)
```

**为什么先 nullable 后 NOT NULL**：现有 Ontology/DataSource 已有数据，直接加 NOT NULL 会失败。先 nullable + 数据迁移（填默认 Space/Project），再改 NOT NULL。

**为什么 ondelete=SET NULL 而非 CASCADE**：资源（Dataset/ObjectType）是业务资产，Project 删除不应级联删资源，而是解绑（project_id 置空，资源变成「无归属」需重新分配）。

**不改动**：
- ObjectTypeGroupModel：保留（Ontology 内语义分组，与 Project 正交），未来接线
- BranchModel：与权限正交（Scenario 用）
- object_state：不改 schema，加 RLS policy（PG 行级安全）

### 1.8 表关系总览

```
Organization ─┬─ Space (白名单 SpaceOrganization)
              └─ 派生系统 Marking

Space ─┬─ 1:1 Ontology ─┬─ ObjectType ─┬─ Property
       │                │              └─ (project_id 预留)
       │                ├─ ActionType / LinkType / InterfaceType
       │                └─ ObjectTypeGroup（语义分组）
       └─ Project ─┬─ Dataset / DataSource / SyncTask / Credential（归属）
                  └─ RoleAssignment (scope=PROJECT)

Principal ─┬─ User (attributes 行级用)
           ├─ Group (parent_group 嵌套) ─ GroupMembership ─ User
           └─ ServiceUser (scopes 限制)

Role ─ RoleAssignment (principal_id→Group, scope→Space/Project)
MarkingCategory ─ Marking ─┬─ MarkingGrant (→Group)
                            └─ MarkingAssignment (→资源)
RowSecurityPolicy (→ObjectType)
PropertyMaskingPolicy (→Property)
AuditLog（追加，关联 principal/resource）
AccessRequest（JIT 申请）
```

---

## 二、Service 层设计

> Service 层是权限体系的运行时核心。本节讲清楚每个 Service 的职责边界、为什么这么划分、关键求值逻辑、与现有 Service 的集成点。

### 2.1 AuthorizationService：权限决策中枢（PDP）

#### 为什么需要中心化 PDP

权限校验如果散落在各 Service 里（OntologyService 查角色、ObjectQueryService 查标记、ActionService 查行级），会导致三问题：①逻辑重复且易 drift；②无法统一缓存；③无法统一审计。中心化 PDP（Policy Decision Point）把所有权限决策收口到一个服务，各 Service 只调用不实现，保证一致性与可审计性。

这是 XACML 的 PDP/PEP 分离思想：**决策（PDP）与执行（PEP）解耦**。AuthorizationService 是 PDP（求值策略返回允许/拒绝），各 Service + 各引擎是 PEP（执行点，拦截请求调 PDP）。

#### 五层校验的求值顺序为什么重要

五层校验**串行**（非并行），任一层拒即终止，不进入下一层。这个顺序有讲究——**从粗到细、从便宜到昂贵**：

```
Layer 1 身份认证  → 最便宜（查 Principal 状态），最先做
Layer 2 Organization → 便宜（集合交集判断），资源完全不可见
Layer 3 Space     → 便宜（查白名单 + 准入角色）
Layer 4 Project RBAC → 中等（查 RoleAssignment，可缓存）
Layer 5 Marking MAC  → 中等（查 MarkingGrant + 合取校验）
行/列级下推      → 最昂贵（求值表达式 + 引擎下推），最后做
```

**为什么 Organization 在 Project 之前**：Organization 是主体强隔离（硬门槛），配置错误概率低且校验便宜。先拦主体不符的，避免后续无意义求值。且 Organization 不满足时资源「完全不可见」（连名称都不返回），而 Project 不满足时可能仍可见名称（Discoverer）。

**为什么 Marking 在 Project 之后**：Marking 校验需先知道资源的全部标记（查 MarkingAssignment），比 Project 角色查询略贵。且 Marking 是数据级硬门槛，须在确认用户能进 Project 后再校验数据密级。

#### 三个核心方法与职责边界

```python
class AuthorizationService:
    def __init__(self, metadata: PostgresMetaStore, cache: TTLCache) -> None:
        self._metadata = metadata
        self._cache = cache  # 进程内 LRU + 短 TTL

    async def check_access(
        self, principal: Principal, resource_type: str, resource_id: str, action: str
    ) -> AccessResult:
        """通用五层校验。任一层拒即 DENY。用于单资源访问决策。"""
        # Layer 1-5 串行，全过 ALLOW，审计落盘

    async def evaluate_query_scope(
        self, principal: Principal, object_type: ObjectType
    ) -> QueryScope:
        """查询专用：返回可见对象集 + 须脱敏属性。用于查询下推。"""
        # 先五层校验 ObjectType 可见性（forbidden 则返回空）
        # 再求值 RowSecurityPolicy → visible_rids
        # 再求值 PropertyMaskingPolicy → masked_properties
        # 返回 QueryScope(visible_rids, masked_properties, forbidden, project_scope)

    async def check_action_permission(
        self, principal: Principal, action_type: ActionType, context: ActionContext
    ) -> set[str]:
        """Action 专用：返回 forbidden rids（ADR-011 契约不变）。"""
        # 复用 check_access 校验 affected_object_type
        # 返回无权限的 rids 集合
```

**三个方法的区别**：`check_access` 是单资源单动作决策（能否访问这个资源）；`evaluate_query_scope` 是批量预计算（这个 ObjectType 里哪些对象可见、哪些属性须脱敏），供查询下推用；`check_action_permission` 是 Action 专用（哪些 rids 不能写），保持 ADR-011 契约不变。

#### 缓存策略：cashews + 主动失效 + TTL 兜底

权限求值有性能开销（多表查询 + Cedar 求值），用 [cashews](https://github.com/Krukov/cashews)（async-first 多 backend 缓存框架）实现三级缓存（对齐 Palantir，详见 [ADR-017 D2](../architecture/adr-017-permission-tech-stack.md#d2-缓存层--cashews)）：
- **用户属性缓存**（principal 的 groups/roles/markings/attributes）：登录会话级 + 短 TTL。组/角色/标记变更时 `delete_tags` 主动失效
- **资源属性缓存**（资源的 org/space/project/marking 配置）：长 TTL。资源权限/标记变更时 `delete_tags` 主动失效
- **授权结果缓存**（principal + resource + action → ALLOW/DENY）：短 TTL。任一方变更时 `delete_tags` 主动失效

**cashews 的关键能力**：tag 失效（精准批量）、分布式锁（防 stampede）、URL 驱动 backend 切换（`mem://` 开发 ↔ `redis://` 生产，代码不改）、client-side caching（Redis 6+ 多实例跨进程一致性）。

**高敏操作不走缓存**：权限授予/角色变更/标记移除/数据删除强制实时校验（防缓存导致安全漏洞）。

**失效保障**：主动失效（主）+ TTL 兜底（次）。即使主动失效失败，最多几分钟自动同步。

#### 选项 B fallback 逻辑

定义类资源（ObjectType 等）的 `project_id` 为空时，权限查其所属 Ontology 的默认 Project 的角色。这个 fallback 逻辑集中在 AuthorizationService Layer 4，调用方无感知。未来选项 B→A 迁移时，只需填充 project_id，fallback 逻辑自动跳过。

### 2.2 三层容器 Service：CRUD + 自动授权

#### SpaceService 的核心：创建即自动配齐

SpaceService 的 `create_space` 不是简单建表，而是**原子地完成一组关联操作**（对齐「从动作推断意图」原则）：

```python
class SpaceService:
    async def create_space(self, space: SpaceCreate, creator: Principal) -> Space:
        # 1. 创建 Space
        # 2. 自动创建同名 Ontology（1:1 强绑定）
        # 3. 创建默认 Project
        # 4. creator 自动成为 Space Owner + Ontology Owner + Project Owner
        #    （推断意图：用户创建 Space = 要用，自动配齐权限，不弹窗）
```

**为什么自动配齐三层 Owner**：避免用户创建 Space 后还要手动去三个地方配权限。HP 原则——从动作推断意图，少弹窗。创建者要能用自己创建的东西，这是最自然的预期。

**为什么是原子操作**：如果创建 Space 后崩溃，没建 Ontology，会留下孤儿 Space。用 `async with self.transaction():` 包裹（遵循 CLAUDE.md 事务最佳实践）。

#### ProjectService：协作边界管理

ProjectService 的核心是资源归属管理——资源放入 Project 时更新其 `project_id`。Project 删除时资源 `project_id` 置空（SET NULL，不级联删资源，业务资产保护）。

### 2.3 身份 Service：Principal 解析与组管理

#### PrincipalService：从请求到 Principal

PrincipalService 的 `resolve_principal` 是每个请求的入口——把 HTTP 请求转换为 Principal 对象：

```python
class PrincipalService:
    async def resolve_principal(self, request: Request) -> Principal:
        # OIDC token 优先，fallback X-User-Id 请求头（开发模式）
        # 加载 user.attributes / groups / roles / markings
```

**两种模式**：
- **生产模式**：Better Auth 签发 OIDC JWT（Authorization: Bearer <jwt>），Authlib 验证签名 + 过期，从 sub claim 映射到 User。Better Auth 管用户/会话/认证/联邦，Gaia 只做 JWT 验证 + claims→Principal 映射（详见 [ADR-017 D3](../architecture/adr-017-permission-tech-stack.md#d3-身份认证--better-auth双场景authlib-应用层-jwt-验证)）
- **开发模式**：`X-User-Id` / `X-User-Roles` 请求头（无 Better Auth 时本地开发用）

**Principal 对象包含**：身份信息 + attributes（行级用）+ groups（组继承）+ roles（角色集）+ markings（标记集）。这些是五层校验的输入。属性变更（转岗）自动联动——因为每次请求都重新解析（或缓存失效）。

#### GroupService：组授权铁律的执行者

GroupService 管组成员关系。**所有权限授 Group 不授个人**的铁律在 RoleAssignment 层面强制（principal_id 通常是 group_id）。GroupService 提供成员管理，人员异动只调这里。

### 2.4 MarkingService：权责分离的执行者

MarkingService 的设计核心是**权责分离**——两类操作由不同角色执行：

```python
class MarkingService:
    async def create_marking(self, marking: MarkingCreate, admin: Principal) -> Marking: ...
        # 仅 MARKING_ADMIN（管定义）
    async def grant_marking(self, marking_id: str, group_id: str) -> None: ...
        # 仅 MARKING_ADMIN（管授权）
    async def assign_marking(self, resource_type: str, resource_id: str, marking_id: str) -> None: ...
        # PROJECT_OWNER/EDITOR（管打标，用已有标记）
    async def check_markings(self, principal: Principal, resource_type: str, resource_id: str) -> bool: ...
        # 合取校验：resource 全部 marking ⊆ principal.markings
```

**权责分离**：MARKING_ADMIN 管标记定义与授权（给哪些 Group），但不能给资源打标；PROJECT_OWNER 能给资源打标（用已有标记），但不能创建标记或授权。这防止项目管理员自行放开密级数据权限（MAC 的核心安全设计）。

**合取校验**：`check_markings` 是 Layer 5 的核心——资源带 N 个标记，用户须持有全部 N 个。这是布尔 AND，不是 OR（与 RBAC 的「任一角色即可」相反）。

---

## 三、AuthMiddleware 与 Principal 注入

### 3.1 中间件的职责：认证 + 上下文注入

AuthMiddleware 是每个请求的第一道关卡，做两件事：
1. **认证**：从请求提取 Principal（生产模式：Authlib 验证 Better Auth 签发的 JWT → claims→Principal 映射；开发模式：X-User-Id 请求头），注入 `request.state.principal`，供后续 Service 使用
2. **PG RLS 上下文注入**：设置 PG session 变量，供 object_state 表 RLS policy 引用

```python
class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        principal = await self._principal_service.resolve_principal(request)
        request.state.principal = principal
        await self._set_pg_session_context(principal)  # PG RLS 上下文
        response = await call_next(request)
        return response
```

### 3.2 PG RLS 上下文注入（关键且易错）

`_set_pg_session_context` 设置 PG session 变量，让 object_state 的 RLS policy 能拿到当前 principal 的组织/标记信息：

```python
async def _set_pg_session_context(self, principal: Principal):
    # SET LOCAL app.principal_organization = '<org_id>'
    # SET LOCAL app.principal_markings = '{pii,confidential}'
    # SET LOCAL app.principal_id = '<user_id>'
```

**为什么用 SET LOCAL 而非 SET**：SET LOCAL 是事务级，事务结束自动清理。SET 是 session 级，会残留到连接关闭。用 SET LOCAL 配合 PgBouncer transaction pooling 时安全（每事务独立）。

**⚠️ 避坑（生产级经验）**：
- **PgBouncer transaction pooling 陷阱**：session 级变量会跨请求泄露（A 用户的 org_id 残留，B 用户看到 A 的数据）。必须用 `SET LOCAL`（事务级）+ 每事务开头重置。若用 PgBouncer session pooling 则无此问题但牺牲连接复用。
- **superuser BYPASSRLS**：应用连接不能用 superuser（会绕过所有 RLS）。用普通角色 + `FORCE ROW LEVEL SECURITY`。

### 3.3 ActionContext 改造：principal 替换 current_user

```python
class ActionContext(BaseModel):
    principal: Principal  # 替换 current_user: str = "anonymous"
    current_timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    workspace_id: str = ""
    ontology_snapshot_version: int | None = None
    selected_object: dict[str, Any] | None = None
    # 向后兼容：current_user 属性 = principal.display_name
```

**为什么用 Principal 对象而非字符串**：`current_user="anonymous"` 只是用户名，无法携带 attributes/groups/roles/markings。Principal 对象是权限求值的完整输入，替换后 ActionAuthorizer 可直接用 principal 做五层校验。

**向后兼容**：保留 `current_user` 属性（= `principal.display_name`），旧代码引用 `ctx.current_user` 不破坏。

---

## 四、查询层权限下推（多引擎）

> 查询层是权限下推的核心改造区。本节讲清楚：为什么必须下推到存储层（而非应用层过滤）、各引擎的原生能力与约束、Gaia 的分层适配策略、生产级避坑。

### 4.0 为什么必须下推到存储层

**应用层后过滤是虚假安全**——在 ObjectQueryService 查询后用 Python 过滤，可被绕过（抓包改参数、直接调 API、写 SQL）。业界共识（Databricks/Snowflake/Immuta/Ranger 都下推）：行/列级过滤必须下推到存储引擎或查询引擎的计划层，让引擎在返回数据前就过滤，应用拿不到无权数据。

Gaia 采用 **SqlGlot AST 注入统一机制**（详见 [ADR-017 D4](../architecture/adr-017-permission-tech-stack.md#d4-行级下推--sqlglot-ast-注入统一机制放弃-doris-原生-row-policy)）：Cedar TPE 产生行级过滤残差 → 翻译为 SQL 谓词 → SqlGlot AST 递归注入 WHERE（子查询/CTE/UNION/JOIN 全覆盖）→ 引擎在 scan 节点执行过滤。这是「应用层构造谓词，引擎执行过滤」，谓词在 SQL 发给引擎前注入，无权数据不离开引擎，**非后过滤**。

各引擎分工：
- **Doris/Trino/PG 读路径**：统一走 SqlGlot AST 注入（方言切换）
- **PG object_state 写路径**：RLS WITH CHECK（PG 独特写入校验能力，SqlGlot 注入只管读路径）
- **Neo4j**：Cypher WHERE 属性驱动过滤（Community 无 FGAC）
- **Iceberg**：不直接下推（写入入口，非查询主源）

### 4.1 ObjectQueryService 改造：QueryScope 模式

所有查询方法加 `principal` 参数，查询前调 `evaluate_query_scope` 预计算可见范围，注入查询：

```python
class ObjectQueryService:
    async def load_objects(self, request: LoadObjectsRequest, principal: Principal) -> list[Object]:
        scope = await self._authz.evaluate_query_scope(principal, request.object_type)
        if scope.forbidden:
            return []  # 不可见即安全（不报错，返回空）
        # scope.residual 是 Cedar TPE 残差，由下层 store 翻译为 SQL 谓词注入
        # 序列化时 scope.masked_properties 返回 null
```

**QueryScope 的字段**：
- `residual`：Cedar TPE 残差（已求值 principal 部分，只剩 resource 属性条件），供下层 store 翻译为 SQL 谓词注入 WHERE
- `masked_properties`：须脱敏的属性集（序列化时返回 null）
- `forbidden`：整个 ObjectType 无权限（上四层任一拒，返回空）

**为什么 forbidden 返回空而非报错**：「不可见即安全」——无权资源不提示无权限（防枚举探测），直接返回空，用户感知不到资源存在。

### 4.2 Doris 下推（主力，在线读主源）—— SqlGlot AST 注入

#### 放弃 Doris 原生 Row Policy 的理由

Doris 4.0.5 虽提供 Row Policy（行级过滤，自动追加 WHERE），但有三个架构约束使其不适合 Gaia（详见 [ADR-017 D4](../architecture/adr-017-permission-tech-stack.md#d4-行级下推--sqlglot-ast-注入统一机制放弃-doris-原生-row-policy)）：
1. **Row Policy 是静态谓词**——USING 不能引用 session 变量/UDF 运行时求值，过滤值必须创建策略时写死（与 PG RLS 的 `current_setting` 本质不同）
2. **root/admin 不受 Row Policy 约束**——而 Gaia 当前用单一 Doris 用户连接池（`settings.doris_user`，通常 root/admin），策略完全不生效
3. **改 per-user/per-group 连接池代价过大**——连接池爆炸 + 违反组授权铁律

#### Gaia Doris 下推方案：SqlGlot AST 注入

```python
class DorisIndexStore:
    async def query(self, query: IndexQuery, scope: QueryScope) -> IndexResult:
        # 基础查询 SQL（现有逻辑不变）
        base_sql = f"SELECT {pk} FROM {table} WHERE {business_filters}"
        # Cedar TPE 残差 → SQL 谓词（已求值 principal，只剩 resource 属性条件）
        permission_predicates = self._residual_to_sql(scope.residual)
        # SqlGlot AST 注入（递归处理子查询/CTE/UNION，AskTable 成熟方案，<10ms）
        final_sql = self._sql_injector.inject(base_sql, permission_predicates, dialect="doris")
        await cursor.execute(final_sql)  # 仍用单一连接池，无 Doris 身份管理
```

**安全等价性**：SqlGlot 注入是「应用层构造谓词，引擎执行过滤」——谓词在 SQL 发给 Doris 前注入，Doris 在 scan 节点执行 WHERE 过滤无权行，无权数据不返回应用层。与「下推到存储层」要求一致，非后过滤。Gaia 的 Doris 不对外暴露直连（只通过 ObjectQueryService 访问），应用层注入已足够。

**列脱敏（保留原生 MASK 函数 + VIEW）**：Doris 4.0 原生提供 `MASK()` / `MASK_SHOW_LAST_4()` 等[脱敏函数](https://doris.apache.org/docs/dev/sql-manual/sql-functions/scalar-functions/string-functions/mask/)。用 CREATE VIEW 包裹原表，视图中对敏感列调用 MASK 函数，用户查视图而非原表——**数据在 Doris 存储层脱敏，不传应用层**。例：`CREATE VIEW idx_<ont>__<type>_masked AS SELECT id, mask(phone) AS phone, name FROM idx_<ont>__<type>`。AuthorizationService 根据 PropertyMaskingPolicy 决定用户查原表还是脱敏视图。这是存储层列脱敏，与行级下推（SqlGlot 注入）是两个独立机制。

**性能考量**：SqlGlot 注入的谓词下推到 Doris scan 节点，配合 partition pruning + zoneMap + inverted index 数据裁剪。**等值匹配性能最佳**（region='east'）。AST 注入开销 <10ms（AskTable 实测）。

> **Doris 原生 Row Policy 保留为二期可选纵深防御层**：若未来 Doris 对外暴露直连，需评估 per-group Doris Role + Row Policy 作为应用层注入之外的二级防护。一期不引入。

### 4.3 PG RLS 下推（object_state，唯一支持写入校验）

#### PG RLS 的独特价值：WITH CHECK

PG RLS 是唯一原生支持**写入路径校验**的引擎——`USING`（读过滤）+ `WITH CHECK`（写校验）。Action 写 object_state 时，RLS WITH CHECK 双重保障：写入的行必须满足条件，否则拒绝。其他引擎写入只靠应用层 ActionAuthorizer。

```sql
ALTER TABLE object_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE object_state FORCE ROW LEVEL SECURITY;  -- 强制（含 owner 也受 RLS）

CREATE POLICY object_state_org_isolation ON object_state
FOR ALL  -- ALL | SELECT | INSERT | UPDATE | DELETE
USING (  -- 读过滤
    ontology_id IN (
        SELECT o.id FROM ontologies o
        JOIN spaces s ON o.space_id = s.id
        JOIN space_organizations so ON s.id = so.space_id
        WHERE so.organization_id = current_setting('app.principal_organization')::text
    )
)
WITH CHECK (  -- 写校验（写入的行必须满足）
    ontology_id IN (
        SELECT o.id FROM ontologies o
        JOIN spaces s ON o.space_id = s.id
        JOIN space_organizations so ON s.id = so.space_id
        WHERE so.organization_id = current_setting('app.principal_organization')::text
    )
);
```

**USING vs WITH CHECK**：USING 是 SELECT/UPDATE/DELETE 的行可见性过滤；WITH CHECK 是 INSERT/UPDATE 的行写入校验。两者都用，读写在存储层双重保障。

**FORCE ROW LEVEL SECURITY**：强制表 owner 也受 RLS（默认 owner 绕过）。确保即使 owner 权限配置错误也受保护。

#### ⚠️ PG RLS 避坑指南（生产级）

| 坑 | 后果 | 解法 |
|------|------|------|
| **PgBouncer transaction pooling** | `SET LOCAL`/`current_setting` session state 跨请求泄露（A 用户 org_id 残留→B 用户看 A 的数据） | 用 `SET LOCAL`（事务级）+ 每事务开头重置；不用 session 级 SET |
| **superuser BYPASSRLS** | 应用用 superuser 连接绕过所有 RLS | 应用连接用普通角色（非 superuser）+ FORCE RLS |
| **policy 函数 volatility** | VOLATILE 函数不能下推索引，全表扫描 | policy 表达式用 IMMUTABLE 函数 + 等值匹配 |
| **性能开销** | 无索引的 org 列全表扫描 | 对 organization_id 列建索引；基准 2-4% 开销（indexed 列） |

### 4.4 Trino 下推（联邦/降级）—— SqlGlot AST 注入（与 Doris 同机制）

Trino 降级时行级下推与 Doris 走**同一套 SqlGlot AST 注入机制**，只需方言切换（`dialect="trino"`）。Cedar TPE 残差翻译为 SQL 谓词后，SqlGlot 递归注入 WHERE，Trino 优化器通过 predicate pushdown 下推到数据源 connector（Iceberg/PG 等），性能与手写 WHERE 一样。

```python
# TrinoStore 与 DorisIndexStore 同一注入逻辑，仅方言不同
class TrinoQueryEngine:
    async def query(self, sql: str, scope: QueryScope) -> list[dict]:
        permission_predicates = self._residual_to_sql(scope.residual)
        final_sql = self._sql_injector.inject(sql, permission_predicates, dialect="trino")
        return await self._execute(final_sql)
```

**二期可选增强（OPA/Ranger 插件）**：若未来需要 Trino 原生计划改写（非应用层注入），可部署 OPA 插件——OPA 返回行过滤/列脱敏表达式，Trino 计划器自动注入。但这非必需，SqlGlot 注入已能下推到 connector。OPA 插件的价值在于「多消费者共享策略」（Trino 直连场景），Gaia 的 Trino 只通过 ObjectQueryService 访问，应用层注入已足够。

**列脱敏**：一期在序列化层处理（scope.masked_properties 返回 null）；二期可评估 OPA 插件的列替换表达式。

### 4.5 Neo4j 下推（图遍历）

#### Neo4j Community Edition 的权限限制

Gaia 用 `neo4j:5-community`（开源版）。Neo4j 的原生细粒度访问控制（FGAC：`GRANT TRAVERSE` / Property-based access control / ABAC `CREATE AUTH RULE`）**都是 Enterprise Edition 专属**，Community 不可用。因此 Neo4j 权限过滤只能在应用层做（Cypher 查询改写）。

#### 方案：属性驱动过滤（非大列表 IN）

**核心思路**：不用 `WHERE m.id IN $visible_ids`（大列表性能差），而是**把权限相关属性作为节点属性投影到 Neo4j**，查询时 Cypher WHERE 直接按属性过滤（与 PG RLS / SqlGlot AST 注入思路一致——属性驱动，非 ID 列表）。

GraphProjector 投影时（已有机制，ADR-015 M1），把 object 的权限相关属性（region/department 等 RowSecurityPolicy 引用的属性）作为节点属性写入。查询时：

```python
class Neo4jGraphStore:
    async def search_around(self, source_ids: list[str], principal: Principal) -> list[Node]:
        # 从 principal.attributes 构造过滤参数
        region = principal.attributes.get('region')
        # Cypher WHERE 直接按节点属性过滤（属性驱动，非 ID 列表）
        cypher = """
            MATCH (n)-[*1..3]->(m)
            WHERE m.region = $region
            RETURN m
        """
        result = await self._session.run(cypher, region=region)
```

**三种过滤模式**（根据 RowSecurityPolicy 类型选择）：
1. **属性匹配**（最常用）：`WHERE m.region = $principal_region`——节点有 region 属性，按 principal 属性过滤
2. **标记过滤**：`WHERE NOT 'VIP' IN m.markings OR 'VIP' IN $principal_markings`——节点带 markings 列表，principal 须持有才可见
3. **全可见**（无 RowSecurityPolicy）：不加 WHERE，principal 全可见

**visible_ids 仅作兑底**：当权限规则无法用属性表达（复杂表达式）时，退化为预计算 visible_ids + `WHERE m.id IN $visible_ids`，但这是例外非主流。大列表场景用 APOC `apoc.coll.contains` 或临时节点集优化。

**性能优势**：属性过滤用 Neo4j 索引（对 region/department 属性建索引），性能远优于大列表 IN。且过滤在遍历阶段做（非后过滤），减少中间结果。

**indexed 属性的 Marking 校验**：投影到 Neo4j 的 indexed 属性也须过 Marking 校验——无标记权限的属性不返回（在序列化层处理，与 Doris 列脱敏同理）。

### 4.6 Iceberg 与写入路径

**Iceberg 权限在 catalog 层**（[Iceberg Access Control Patterns](https://iceberglakehouse.com/iceberg/iceberg-access-control/)）——格式层不做权限（设计选择），靠 Gravitino REST Catalog RBAC + credential vending。Gaia 已用 Gravitino（ADR-014），一期 Iceberg 不直接做行/列级（写入入口，非查询主源），二期 Gravitino RBAC 管理表级权限。

**写入路径权限保障**：

| 路径 | 机制 | 引擎 |
|------|------|------|
| Action 写 object_state | ① ActionAuthorizer 五层校验（写入前） ② PG RLS WITH CHECK（写入时双重） | PG |
| Action outbox | ActionAuthorizer 校验 + 同事务 | PG |
| SeaTunnel 写 Iceberg | Gravitino RBAC（catalog 层） | Iceberg |

### 4.7 下推策略总结

```
查询路径（读）：
  ObjectQueryService → AuthorizationService 五层校验（PDP）+ Cedar TPE 残差
    → Doris（主）：SqlGlot AST 注入谓词（引擎 scan 节点过滤）+ 原生 MASK 函数/VIEW 列脱敏
    → PG（object_state）：SqlGlot AST 注入谓词（读路径）
    → Trino（联邦/降级）：SqlGlot AST 注入谓词（谓词下推到 connector，与 Doris 同机制方言切换）
    → Neo4j：Cypher WHERE 属性驱动过滤

写入路径（写）：
  ActionService → ActionAuthorizer 五层校验（写入前）
    → PG object_state：RLS WITH CHECK（写入时双重保障，PG 独特能力）
    → Iceberg：Gravitino RBAC（catalog 层）
```

**统一机制**：Doris/Trino/PG 读路径统一走 SqlGlot AST 注入（Cedar TPE 残差 → SQL 谓词 → 递归注入 WHERE），方言切换即可。PG 写路径用 RLS WITH CHECK（SqlGlot 注入只管读）。

**避坑总则**：①PG 应用连接不用 superuser ②PgBouncer 须 SET LOCAL ③Doris 列脱敏用原生 MASK 函数 + VIEW（存储层脱敏，无需 Ranger）④SqlGlot 注入是「应用构造谓词引擎执行过滤」，非后过滤（安全等价存储层下推）⑤Iceberg 权限在 catalog 层 ⑥policy 用等值匹配 + indexed 列 ⑦Neo4j Community 无 FGAC，用属性驱动 WHERE 过滤（非大列表 IN），对权限属性建索引。

---

## 五、ActionService 改造（ADR-011 协同）

### 5.1 核心原则：契约不变，internals 切换

ADR-011 的 `ActionAuthorizer` 定义了三层权限契约（执行/行级写/参数级），返回 forbidden set。**这个契约不变**——调用方（ActionService）的代码不动，只换 ActionAuthorizer 的 internals（从 JSON permissions 切换到 AuthorizationService 五层校验）。

**为什么契约不变**：ADR-011 已落地且被 22 工具/AG-UI/MCP 消费，改契约会破坏所有消费者。换 internals 让权限体系升级不影响调用方，是平滑演进的关键。

### 5.2 三层 internals 的切换映射

```python
class ActionService:
    async def execute_action(self, request: ActionExecutionRequest, principal: Principal) -> ActionResult:
        ctx = ActionContext(principal=principal, ...)
        forbidden = await self._authorizer.check_row_write_permission(
            request.object_type, request.rids, ctx
        )
        if forbidden:
            raise ForbiddenError(...)
        # 后续 Step 4-12 不变（CDL/OCC/outbox/CDC/投影）
```

ADR-011 三层 → AuthorizationService 的映射：

| ADR-011 Layer | 旧 internals | 新 internals（本设计） |
|--------------|-------------|---------------------|
| Layer 1 执行权限 | `parameters.permissions.roles` JSON | 查 RoleAssignment（Project scope，五层校验 Layer 4） |
| Layer 2 行级写 | `catalog.check_access`（fail-open） | `evaluate_query_scope` 返回 forbidden set（五层全校验） |
| Layer 3 参数级 | `sensitive_params` 角色白名单 | 保留（JSON 配置不变） |

**Layer 2 的关键改进**：旧版 `catalog.check_access` 是 fail-open permissive（Gravitino RBAC 未接线，默认放行）。新版调 `evaluate_query_scope` 做完整五层校验，返回真正无权限的 rids，从 fail-open 变为 fail-closed（安全默认）。

### 5.3 向后兼容：JSON fallback

`ActionType.parameters.permissions` JSON 保留。AuthorizationService 读取时**优先用结构化 RoleAssignment**，若 ActionType 无结构化角色配置则 fallback 到 JSON（旧 ActionType 定义不破坏）。新创建的 ActionType 鼓励用结构化配置，旧的定义渐进迁移。

---

## 六、工具层改造（ADR-009 协同）

### 6.1 工具权限声明模式

ADR-009 的 22 工具目前 `principal=anonymous`，无权限校验。改造后每个工具**声明所需权限**（resource_type + action + resource_id_param），ToolExecutor 调用前校验。声明用代码内 `ToolPermission` 注册表（类型安全，不用 YAML），Principal 透传用 pydantic-ai `RunContext[GaiaDeps]` 原生依赖注入（详见 [ADR-017 D5](../architecture/adr-017-permission-tech-stack.md#d5-工具层权限声明--pydantic-ai-runcontext-原生-di)）：

```python
@dataclass
class ToolPermission:
    resource_type: str          # ONTOLOGY / OBJECT_TYPE / DATASET ...
    action: str                 # VIEW / EDIT / EXECUTE
    resource_id_param: str | None  # 运行时参数名（如 "object_type_id"），None=静态

TOOL_PERMISSIONS: dict[str, ToolPermission] = {
    "define_object_type": ToolPermission("ONTOLOGY", "EDIT", None),  # 静态
    "query_with_dataframe": ToolPermission("OBJECT_TYPE", "VIEW", "object_type_id"),  # 动态
}

class ToolExecutor:
    async def execute_gated(self, tool_name: str, params: dict, principal: Principal) -> ToolResult:
        perm = TOOL_PERMISSIONS[tool_name]
        resource_id = params[perm.resource_id_param] if perm.resource_id_param else "*"
        result = await self._authz.check_access(principal, perm.resource_type, resource_id, perm.action)
        if not result.allowed:
            await self._audit.log(...)  # 审计
            return ToolResult(error="FORBIDDEN", reason=result.reason)
        return await self._execute(tool_name, params)
```

**动态 resource_id**：工具参数动态决定 resource_id 的情况（如 `query_with_dataframe(object_type_id=...)`），声明里记 `resource_id_param`（参数名），运行时从工具参数取值校验。

### 6.2 Agent 权限模型

- **AG-UI Agent**：基于 pydantic-ai，Principal 作为 `GaiaDeps` 注入 `RunContext`，`@agent.tool` 装饰器自动拿到 context。Agent 以人类用户 Principal 身份执行（继承用户权限），用户无权的操作 Agent 也无权
- **MCP 工具**：用 Service User（scoped 限制含 Project 维度——可限定访问哪些 Project 的资源），同样走 ToolExecutor 校验
- **FORBIDDEN 返回 reason**：工具被拒时返回可读原因（哪层拦截、缺什么），让 Agent 能理解为何不能执行，而非静默失败

---

## 七、API 路由设计

> **实现注记（2026-07-10）**：实际实现中路由前缀调整为：
> - 三层容器：`/containers/organizations`、`/containers/spaces`、`/containers/projects`（非 `/organizations`、`/spaces`）
> - 身份管理：`/identity/users`、`/identity/groups`、`/identity/groups/{id}/members`（非 `/users`、`/groups`）
> - 角色授予：`/authz/role-assignments`（与设计一致）
> - 标记管理：`/marking-categories`、`/markings`、`/resources/{type}/{id}/markings`（与设计一致）
> - 新增：`GET /containers/roles`（列出内置角色）、`GET /identity/users/{id}/groups`（用户所属组）
> - JIT auto-provisioning：`POST /identity/users` 支持 `X-Provision-Token` 头（Better Auth databaseHooks 内部调用，bypass role:manage）
>
> 下方为原始设计路由（保留参考）。

### 7.0 路由设计原则

API 路由不只是端点清单，其设计遵循以下原则：

1. **RESTful 资源模型**：路由对应资源（Organization/Space/Project/Group/Marking），HTTP 动词对应操作（GET 查/POST 建/PATCH 改/DELETE 删）
2. **就近原则**：权限授予路由就近资源（`/resources/{type}/{id}/markings` 打标，不跳 `/marking-assignments`）
3. **层级嵌套表达归属**：Project 在 Space 下（`/spaces/{space_id}/projects`），体现归属关系
4. **allowedActions 返回**：资源响应含 `allowedActions` + `disabledReasons`，前端据此渲染（Ship the Policy）
5. **统一错误模型**：403 返回 `{detail, layer, reason, missing}`，前端能解释哪层拦截 + 缺什么

### 7.1 认证与权限校验路由

```
POST /auth/login              # OIDC 登录回调
POST /auth/logout
GET  /auth/me                 # 当前 Principal 信息（attributes/groups/roles/markings）

# Check Access（可解释性）
GET  /authz/check?principal_id=&resource_type=&resource_id=&action=
                              # 返回每层校验状态 + 权限来源 + 缺失权限

# 自助申请（JIT 授权）
POST /authz/access-requests   # 提交权限申请
GET  /authz/access-requests   # 列我的申请
POST /authz/access-requests/{id}/approve   # 审批（Owner/Admin）
POST /authz/access-requests/{id}/reject
```

**Check Access 是可解释性的核心 API**：输入任意 principal + 资源 + 动作，返回五层校验状态 + 权限来源（哪个 Group→哪个 Role→用户）+ 缺失权限 + 模拟授权。供前端调试面板 + Agent 主动探测 + 审计追溯用。

### 7.2 三层容器管理路由

```
# Organization（Platform Admin）
POST   /organizations
GET    /organizations
GET    /organizations/{id}
PATCH  /organizations/{id}
DELETE /organizations/{id}

# Space（Space Owner / Platform Admin）
POST   /spaces                 # 自动创建同名 Ontology + 默认 Project（推断意图）
GET    /spaces
GET    /spaces/{id}
PATCH  /spaces/{id}
POST   /spaces/{id}/organizations    # 加组织白名单（跨组织协作通道）
DELETE /spaces/{id}/organizations/{org_id}

# Project（Space Owner / Project Owner）
POST   /spaces/{space_id}/projects   # 嵌套路由表达归属
GET    /spaces/{space_id}/projects
GET    /projects/{id}
PATCH  /projects/{id}
DELETE /projects/{id}
```

**Space 创建的特殊性**：`POST /spaces` 不是简单建表，而是原子地创建 Space + Ontology + 默认 Project + 三层 Owner（见 §2.2）。返回 Space 含关联的 ontology_id 和 default_project_id。

### 7.3 身份与角色路由

```
# User / Group / ServiceUser
POST   /users          GET /users          GET /users/{id}
POST   /groups         GET /groups         GET /groups/{id}
POST   /groups/{id}/members    DELETE /groups/{id}/members/{user_id}
POST   /service-users  GET /service-users

# Role Assignment（授 Group，不授个人）
POST   /role-assignments        # 授角色（scope_type=SPACE|PROJECT, scope_id=...）
GET    /role-assignments?scope_id=
DELETE /role-assignments/{id}
```

### 7.4 标记路由（权责分离体现在路由分组）

```
# Marking 管理（Marking Admin — 管定义与授权）
POST   /marking-categories     GET /marking-categories
POST   /markings               GET /markings
POST   /markings/{id}/grants   # 授 Group 标记权限
DELETE /markings/{id}/grants/{group_id}

# 资源打标（Project Owner/Editor — 管打标，用已有标记）
POST   /resources/{type}/{id}/markings     # 打标（就近资源）
DELETE /resources/{type}/{id}/markings/{marking_id}
```

**权责分离的路由体现**：Marking 定义/授权在 `/markings/*`（Marking Admin），资源打标在 `/resources/{type}/{id}/markings`（Project Owner）。两组路由不同角色调用，强制权责分离。

### 7.5 行/列级策略路由

```
# RowSecurityPolicy（ObjectType 级）
POST   /object-types/{id}/row-policies
GET    /object-types/{id}/row-policies
PATCH  /row-policies/{id}
DELETE /row-policies/{id}

# PropertyMaskingPolicy（Property 级）
POST   /properties/{id}/masking-policies
GET    /properties/{id}/masking-policies
DELETE /masking-policies/{id}
```

### 7.6 审计路由

```
GET /audit-logs?principal_id=&resource_type=&from=&to=   # 查询审计（按权限范围）
GET /audit-logs/export                                    # 导出（对接 SIEM）
```

**审计查询的权限范围**：Project Owner 看本项目，Organization Admin 看本组织，Audit Admin 看全平台（对齐 Palantir 审计权限分级）。

---

## 八、前端交互设计

> 遵循 [ADR-013](../architecture/adr-013-react-aria-components.md)（React Aria Components + Tailwind v4）与 CLAUDE.md 前端规范。前端是权限的**体验优化层**，不是安全边界——真正的授权在后端 Cedar 五层校验，前端只做「显隐/置灰/不请求数据」以提升 UX。核心原则：[Ship the policy, not the code](https://www.jayfreestone.com/writing/share-the-policy-not-the-code/)——后端返回 `allowedActions` + `disabledReasons`，前端渲染状态而非重新推导规则。

### 8.1 渐进式披露（核心 UX 原则）

- **默认视图**：日常用例（查看数据、查询、执行已授权 Action）。单租户默认 Organization 不暴露三层管理
- **高级面板**：Organization/Space/Project 管理、标记管理、行级策略、角色配置（需明确进入「管理」模式，从侧边栏「设置」入口进入）
- **权限不可见即安全**：无权限资源隐藏，不报错；主动访问被拒时显示拒绝原因 + 申请入口（Check Access）

### 8.2 前端权限控制架构：三道闸门

前端授权不是「隐藏按钮」一件事，而是三道闸门（[参考](https://dev.to/nwosaemeka/hiding-the-button-isnt-authorization-why-you-must-gate-the-request-156k)）：

| 闸门 | 职责 | 实现 |
|------|------|------|
| **Render Gate** | 用户能看到这个按钮/页面/菜单吗？ | `PermissionGate` 组件 + `useAllowedActions` hook |
| **Data Gate** | 应用该请求这份数据吗？ | API 层根据权限决定是否发起请求（无权资源不 fetch） |
| **Backend Gate** | 服务端能验证权限无论客户端做什么？ | Cedar 五层校验（前端被绕过也 403） |

**关键**：前端只做前两道闸门（UX 优化），第三道由后端 Cedar 保证。用户绕过前端直接调 API，后端仍拒绝。

#### Ship the decision 模式（allowedActions + disabledReasons）

后端在资源响应中返回 `allowedActions` + `disabledReasons`（[Ship the policy](https://www.jayfreestone.com/writing/share-the-policy-not-the-code/) 的「Ship the decision」模式），前端渲染状态而非重新推导规则：

```typescript
// 后端响应（资源对象附带权限决策）
interface ResourceWithPermissions<T> {
  data: T;
  allowedActions: string[];           // ["VIEW", "EDIT", "DELETE", "EXECUTE"]
  disabledReasons: Record<string, string>;  // {"DELETE": "需要 Editor 角色"}
}

// 前端 hook：消费 allowedActions
function useAllowedActions(resourceType: string, resourceId: string) {
  // 从最近一次资源响应缓存中取 allowedActions（ship the decision）
  // 或调 /authz/check 获取单资源权限（Check Access API）
  return { allowedActions, disabledReasons };
}
```

#### PermissionGate 组件（声明式权限门控）

类似 CASL `<Can passThrough>`（[参考](https://www.npmjs.com/package/@casl/react)）与 Backstage `usePermission`（[参考](https://github.com/backstage/backstage/blob/master/plugins/permission-react/src/hooks/usePermission.ts)），提供声明式权限门控组件：

```tsx
// src/web-ui/src/components/permission/PermissionGate.tsx
import { useAllowedActions } from "../../hooks/useAllowedActions";

interface PermissionGateProps {
  resourceType: string;
  resourceId?: string;          // 省略时用上下文资源
  action: string;               // VIEW / EDIT / DELETE / EXECUTE
  mode?: "hide" | "disable";    // hide=隐藏（默认），disable=置灰显示原因
  children: React.ReactNode;
  fallback?: React.ReactNode;   // hide 模式下的替代内容
}

export function PermissionGate({
  resourceType, resourceId, action, mode = "hide", children, fallback
}: PermissionGateProps) {
  const { allowedActions, disabledReasons } = useAllowedActions(resourceType, resourceId);
  const allowed = allowedActions.includes(action);

  if (allowed) return <>{children}</>;

  if (mode === "disable") {
    // 置灰 + Tooltip 显示原因（对齐「不可见即安全」但主动操作时给原因）
    const reason = disabledReasons[action] || "无权限";
    return (
      <TooltipTrigger>
        <div aria-disabled className="opacity-50 pointer-events-none">{children}</div>
        <Tooltip>{reason}</Tooltip>
      </TooltipTrigger>
    );
  }

  return <>{fallback}</>;  // hide 模式
}
```

**使用示例**：
```tsx
// 按钮置灰（mutative action 用 disable，让用户知道存在但无权）
<PermissionGate resourceType="OBJECT_TYPE" resourceId={ot.id} action="EDIT" mode="disable">
  <Button onPress={handleEdit}>编辑对象类型</Button>
</PermissionGate>

// 菜单项隐藏（导航用 hide，无权资源不感知存在）
<PermissionGate resourceType="SPACE" resourceId={space.id} action="ADMIN" mode="hide">
  <NavItem href={`/spaces/${space.id}/settings`}>Space 设置</NavItem>
</PermissionGate>
```

#### 路由级保护（PermissionedRoute）

类似 Backstage [PermissionedRoute](https://github.com/backstage/backstage/blob/master/plugins/permission-react/src/components/PermissionedRoute.tsx)，无权限路由不渲染（访问时重定向到 403 页或登录）：

```tsx
// 路由守卫：进入页面前检查权限
<PermissionedRoute
  path="/admin/markings"
  permission={{ resourceType: "MARKING", action: "ADMIN" }}
  element={<MarkingManagement />}
  fallback={<ForbiddenPage />}
/>
```

### 8.3 Better Auth 前端集成

Gaia 前端用 Better Auth 的 React 客户端 SDK（[better-auth/react](https://better-auth.com/docs/concepts/client)）管会话、登录、SSO、组织切换。

#### 客户端初始化

```typescript
// src/web-ui/src/lib/auth-client.ts
import { createAuthClient } from "better-auth/react";
import { organizationClient } from "better-auth/plugins";

export const authClient = createAuthClient({
  baseURL: import.meta.env.VITE_BETTER_AUTH_URL,  // Better Auth Server 地址
  plugins: [organizationClient()],
});

// 导出 hook
export const { useSession, signIn, signOut } = authClient;
```

> **实现说明（2026-07 Phase 5 落地）**：实际实现 (`src/lib/auth-client.ts`) 在
> 上述设计基础上做了三点细化：
> 1. 客户端需加载 `jwtClient()` 插件（匹配服务端 `jwt()` 插件）才能调用
>    `authClient.token()` 获取 Gaia 验证用的 JWT。
> 2. JWT 注入采用 **token provider 注册模式**：`useAuth` 在启动时调用
>    `registerTokenProvider(getJwt, clearJwt)` 把 JWT getter 注册给 API 客户端
>    (`src/api/client.ts`)，所有 `request()`/`authFetch()` 调用自动附加
>    `Authorization: Bearer <jwt>`。这避免了循环导入（client.ts ← auth-client.ts）。
> 3. `RequireAuth` 路由守卫同时等待 **session + JWT** 就绪才渲染子路由，
>    防止子页挂载时的首请求与 JWT 拉取竞态（竞态会导致首请求匿名）。
> 4. 双模式开关：`VITE_AUTH_ENABLED=true` 启用 Better Auth 流程；unset/false
>    走 dev fallback（无登录页，后端用 X-User-Id 头）。`VITE_BETTER_AUTH_URL`
>    空表示同源（dev，经 Vite 代理 `/api/auth/*` → better-auth 容器）；
>    填全 URL 表示生产跨域直连。

#### 会话与登录流程

```tsx
// useSession hook 获取当前会话（reactive，自动刷新）
function App() {
  const { data: session, isPending } = useSession();

  if (isPending) return <LoadingSpinner />;
  if (!session) return <LoginPage />;  // 未登录

  return <AuthProvider session={session}><Router /></AuthProvider>;
}

// LoginPage：邮箱密码（场景1） + SSO（场景2）
function LoginPage() {
  return (
    <>
      <EmailPasswordForm onSubmit={(e, p) => signIn.email({ email: e, password: p })} />
      <Divider>或通过企业 SSO 登录</Divider>
      <SSOButtons />
    </>
  );
}

// SSO 登录（邮箱域名自动路由或选 provider）
function SSOButtons() {
  return (
    <>
      {/* 邮箱域名匹配自动路由：user@acme.com → acme 的 SAML provider */}
      <TextField label="企业邮箱" />
      <Button onPress={() => signIn.sso({ email, callbackURL: "/dashboard" })}>
        企业 SSO 登录
      </Button>
    </>
  );
}
```

#### 组织切换（Organization context）

Better Auth 的 organization 插件提供 `useActiveOrganization` + `useSetActiveOrganization`（[文档](https://better-auth-ui.com/docs/react/queries/active-organization)），切换组织后权限上下文随之变化：

```tsx
function OrganizationSwitcher() {
  const { data: activeOrg } = useActiveOrganization();
  const { mutate: setActive } = useSetActiveOrganization();

  return (
    <Select selectedKey={activeOrg?.id} onSelectionChange={setActive}>
      {/* 用户所属的组织列表 */}
    </Select>
  );
}
```

**SSO 与组织的关联**：企业 SSO 登录后自动设置 `activeOrganizationId`（[PR #9024](https://github.com/better-auth/better-auth/pull/9024)），确保 SSO 用户的会话 scope 到正确的组织。

### 8.4 关键页面

所有页面复用 ADR-013 的 `components/ui/` 原语（TextInput/Select/DataTable/ComboBox/Modal，对齐 CLAUDE.md 组件复用原则）。

| 页面 | 功能 | 角色 | 核心组件 |
|------|------|------|----------|
| LoginPage | 邮箱密码 + SSO 登录 | 公开 | Better Auth client |
| OrganizationManagement | 组织 CRUD + 可见性配置 | Platform Admin | DataTable + Modal 表单 |
| SpaceManagement | Space CRUD + 组织白名单 + 角色 | Space Owner | DataTable + MultiSelect |
| ProjectManagement | Project CRUD + 角色授权 | Project Owner | DataTable + RoleAssignmentForm |
| UserGroupManagement | 用户/组/服务账号管理 | User Access Admin | DataTable + GroupTree + MemberList |
| MarkingManagement | 标记分类/值/授权 | Marking Admin | DataTable + GrantForm |
| PolicyEditor | 行级/列级 Cedar 策略编辑器（带预览） | Project Owner | CodeMirror/Monaco + dry-run |
| CheckAccessPanel | 权限调试（五层校验状态可视化） | 所有用户 | Stepper + TreeView |
| AccessRequestFlow | 权限自助申请 + 审批 | 所有用户 | Form + ApprovalList |
| AuditLogViewer | 审计日志查询 | Audit Admin / Project Owner | DataTable + FilterBar |

#### DataTable CRUD 模式（权限管理页面通用）

React Aria 官方有 [Filterable CRUD Table](https://react-aria.adobe.com/examples/crud) 完整示例（搜索/筛选/排序/列调整/CRUD 表单验证），权限管理页面复用此模式：

```tsx
// UserGroupManagement 页面（复用 React Aria CRUD Table 模式）
function UserGroupManagement() {
  return (
    <main className="p-6">
      <div className="flex justify-between mb-4">
        <Heading level={1}>用户与组管理</Heading>
        {/* 仅 User Access Admin 可见创建按钮（PermissionGate hide 模式） */}
        <PermissionGate resourceType="USER" action="CREATE">
          <Button onPress={openCreateDialog}>创建用户</Button>
        </PermissionGate>
      </div>
      {/* 复用 React Aria Table：搜索 + 筛选 + 排序 + 分页 */}
      <UserTable
        items={users}
        columns={["name", "email", "groups", "status", "actions"]}
        onEdit={(user) => (
          <PermissionGate resourceType="USER" resourceId={user.id} action="EDIT" mode="disable">
            <Button onPress={() => openEditDialog(user)}>编辑</Button>
          </PermissionGate>
        )}
        onDelete={(user) => (
          <PermissionGate resourceType="USER" resourceId={user.id} action="DELETE" mode="disable">
            <Button onPress={() => openDeleteDialog(user)}>删除</Button>
          </PermissionGate>
        )}
      />
    </main>
  );
}
```

### 8.5 Check Access 调试面板（五层校验可视化）

Check Access 是可解释性的核心（§7.1 的 `/authz/check` API），前端用 Stepper + TreeView 可视化五层校验状态：

```tsx
function CheckAccessPanel() {
  const [principalId, setPrincipalId] = useState("");
  const [resourceType, setResourceType] = useState("");
  const [resourceId, setResourceId] = useState("");
  const [action, setAction] = useState("");
  const { result } = useCheckAccess(principalId, resourceType, resourceId, action);

  return (
    <main className="p-6">
      <Heading level={1}>权限调试</Heading>
      {/* 输入：principal + 资源 + 动作 */}
      <CheckAccessForm {...} />
      {/* 输出：五层校验状态 Stepper */}
      {result && (
        <LayerStepper layers={[
          { name: "身份认证", status: result.layers.identity, detail: result.layers.identity.reason },
          { name: "Organization", status: result.layers.organization, detail: "..." },
          { name: "Space", status: result.layers.space, detail: "..." },
          { name: "Project RBAC", status: result.layers.project, detail: "权限来源：viewers 组 → Viewer 角色" },
          { name: "Marking MAC", status: result.layers.marking, detail: "缺失标记：PII" },
        ]} />
      )}
      {/* 最终决策 */}
      <DecisionBadge decision={result.decision} reason={result.reason} />
      {/* 缺失权限 + 申请入口 */}
      {result.missing.length > 0 && (
        <Card>
          <Heading level={3}>缺失权限</Heading>
          <ul>{result.missing.map(m => <li key={m}>{m}</li>)}</ul>
          <Button onPress={() => openAccessRequest(result.missing)}>申请权限</Button>
        </Card>
      )}
    </main>
  );
}
```

**五层 Stepper 可视化**：每层显示通过/拒绝状态 + 详情（哪层拒、缺什么、权限来源链路），帮助管理员和用户理解「为什么不能访问」。

### 8.6 策略编辑器（Cedar LLM 辅助，二期）

- 用户输入自然语言：「销售只能看本区域客户」
- LLM 转成 Cedar 策略表达式：`principal.attributes.region == resource.region`（复用 Gaia /ai/generate + pydantic-ai structured output）
- cedarpy `validate_policies(policy, schema)` dry-run 校验（类型/语法，部署前拦截错误）
- 前端编辑器用 CodeMirror/Monaco + Cedar 语法规则（参考 [vscode-cedar](https://github.com/cedar-policy/vscode-cedar) 官方扩展的 TextMate 语法）
- 生态参考：[AutoCedar](https://github.com/neselab/cedar-synthesis-engine)（verifier-guided 合成）、AWS Bedrock AgentCore 用 Cedar 保护 agentic workflows（详见 [ADR-017 D6](../architecture/adr-017-permission-tech-stack.md#d6-cedar-策略-llm-辅助生成二期-生态完整)）
- 对齐 Gaia AI 原生定位（复用 /ai/generate）

### 8.7 前端权限控制避坑指南

| 坑 | 后果 | 避坑 |
|------|------|------|
| **前端当安全边界** | 用户绕过前端直接调 API 获取数据 | 前端只是体验优化，后端 Cedar 五层校验是唯一安全边界（三道闸门的 Backend Gate） |
| **前端镜像后端权限规则** | 规则 drift，前后端不一致 | Ship the decision——后端返回 allowedActions，前端渲染状态不推导（不写 `if (user.role === 'admin')`） |
| **无权资源报错 404/403** | 泄露资源存在（枚举探测） | 不可见即安全——无权资源返回空/隐藏，不报错 |
| **隐藏按钮后不拦截请求** | 用户手动构造请求绕过 | Data Gate——无权资源不发起 fetch；Backend Gate——后端兜底 |
| **权限检查散落组件** | 维护困难，新增角色要改 20 处 | 统一 PermissionGate 组件 + useAllowedActions hook，声明式门控 |
| **SSO 登录后组织 scope 错误** | 用户能访问其他组织数据 | SSO 登录自动设置 activeOrganizationId（Better Auth PR #9024） |
| **allowedActions 未带 disabledReasons** | 用户不知道为什么按钮置灰 | 后端必返回 disabledReasons，Tooltip 显示原因 |

---

## 九、数据库迁移

> 遵循 CLAUDE.md：Alembic 单一真相源，autogenerate + 人工 review，`alembic check` 无漂移。本节讲迁移策略与关键决策，代码是骨架。

### 9.1 迁移策略：分阶段 + nullable 先行 + 默认兑底

权限体系规模大（四组表 + 现有模型改造），不能一个 migration 干完。策略：

1. **分阶段**：每个 Phase 一个 revision（Phase 0 三层容器+身份+归属字段；Phase 1 角色；Phase 2 标记；Phase 3 行/列级+RLS；Phase 4 审计）。每阶段可独立回滚
2. **nullable 先行**：现有资源加归属字段时先 nullable（不破坏现有数据），数据迁移后改 NOT NULL
3. **默认初始化兑底**：单租户部署自动创建默认 Organization + 默认 Space + 默认 Project，现有资源迁到默认值，保证向后兼容

### 9.2 Phase 0 迁移（三层容器 + 身份 + 归属字段）

```python
def upgrade():
    # 1. 新建三层容器表
    op.create_table("organizations", ...)   # OrganizationModel
    op.create_table("spaces", ...)          # SpaceModel (ontology_id unique=True 1:1)
    op.create_table("space_organizations", ...)  # 白名单关联
    op.create_table("projects", ...)        # ProjectModel

    # 2. 新建身份层表
    op.create_table("principals", ...)
    op.create_table("users", ...)
    op.create_table("groups", ...)
    op.create_table("group_memberships", ...)
    op.create_table("service_users", ...)

    # 3. 现有模型加归属字段（nullable，先迁移数据后改 NOT NULL）
    op.add_column("ontologies", sa.Column("space_id", sa.String(32), nullable=True))
    op.add_column("data_sources", sa.Column("project_id", sa.String(32), nullable=True))
    # ... datasets/sync_tasks/credentials/object_types/action_types/link_types 等
    op.create_foreign_key("fk_ontologies_space", "ontologies", "spaces", ["space_id"], ["id"])

    # 4. 默认初始化（单租户兼容）
    op.execute("""
        INSERT INTO organizations (id, api_name, display_name, org_type, status)
        VALUES ('00000000000000000000000000000001', 'org-default', 'Default Organization', 'INTERNAL', 'ACTIVE')
    """)
    # 默认 Space + 默认 Project 在应用启动时 bootstrap（lifespan）
```

**关键决策：为什么 nullable 先行**：现有 Ontology/DataSource 已有数据，直接加 NOT NULL 约束会失败（已有行的 space_id 为空）。先 nullable + 数据迁移填默认值，再单独 revision 改 NOT NULL。

**关键决策：默认初始化在 migration 还是 lifespan**：Organization 在 migration 插入（必须先有，否则 FK 无效）；默认 Space/Project 在应用 lifespan bootstrap（因为 Space 创建要同步建 Ontology + Project，涉及 Service 逻辑，不宜在 migration 做）。

### 9.3 Phase 1-4 迁移

各 Phase 分别 revision，互不依赖：
- Phase 1：`roles` + `role_assignments`
- Phase 2：`marking_categories` + `markings` + `marking_grants` + `marking_assignments`
- Phase 3：`row_security_policies` + `property_masking_policies` + object_state RLS policy
- Phase 4：`audit_logs` + `access_requests`

### 9.4 数据迁移：现有资源归属默认 Space/Project

```python
def upgrade():
    # 现有 Ontology 迁移到默认 Space
    op.execute("""
        UPDATE ontologies SET space_id = (
            SELECT id FROM spaces WHERE api_name = 'default' LIMIT 1
        ) WHERE space_id IS NULL
    """)
    # 现有 DataSource/Dataset 等迁移到默认 Project
    op.execute("""
        UPDATE data_sources SET project_id = (
            SELECT id FROM projects WHERE api_name = 'default' LIMIT 1
        ) WHERE project_id IS NULL
    """)
    # ... 其他资源同理

    # 迁移完成后改 NOT NULL（单独 revision 或同 revision 后段）
    op.alter_column("ontologies", "space_id", nullable=False)
```

### 9.5 Space↔Ontology 1:1 的迁移考量

现有 Ontology 是顶层独立实体，引入 Space 后要加 `space_id` 并保证 1:1。迁移顺序：
1. 先建默认 Space（bootstrap 时自动建默认 Ontology + 默认 Project）
2. 现有 Ontology 的 `space_id` 指向默认 Space——**但这违反 1:1**（默认 Space 已有自己的 Ontology）

**解法**：默认 Space 的 Ontology 即现有 Ontology 的容器。迁移时把现有 Ontology 逐个「领养」——为每个现有 Ontology 创建独立 Space（Space.api_name = Ontology.api_name），而非全塞默认 Space。这样保持 1:1。单 Ontology 部署则用默认 Space。

### 9.6 RLS policy 迁移（Phase 3）

object_state 启用 RLS 不能在 Phase 0（那时还没有 Organization 数据）。Phase 3 启用：
```sql
ALTER TABLE object_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE object_state FORCE ROW LEVEL SECURITY;
CREATE POLICY object_state_org_isolation ON object_state USING (...) WITH CHECK (...);
```

**⚠️ RLS 启用后须确保应用连接不用 superuser**（否则 BYPASSRLS），且 AuthMiddleware 已实现 PG session context 注入（否则 current_setting 为空，RLS 拒绝所有行）。

---

## 十、测试策略

> **实现状态（2026-07-10）**：单元测试 1524 个全绿，E2E 测试 46 用例全绿，前端测试 253 个全绿。测试策略详见 [`docs/engineer/permission-e2e-test-strategy.md`](../engineer/permission-e2e-test-strategy.md)，避坑要点见 [`docs/engineer/permission-roadmap-and-principles.md`](../engineer/permission-roadmap-and-principles.md) §四。

> 遵循 CLAUDE.md：TDD 先行，单元测试覆盖率 ≥ 90%，异常路径 100%，DB 写入逻辑用真 DB 不全 mock。

### 10.1 单元测试（必须）

| 模块 | 测试重点 |
|------|---------|
| AuthorizationService | 五层校验每层的通过/拒绝；选项 B fallback 逻辑；缓存命中/失效 |
| SpaceService | 1:1 Ontology 绑定；创建 Space 自动建 Ontology+Project+Owner |
| MarkingService | 合取校验（多标记 AND）；权责分离（Marking Admin vs Project Owner） |
| RowSecurityPolicy | Cedar 表达式求值（principal.attributes 匹配）；schema 验证 |
| PropertyMaskingPolicy | 脱敏返回 null；cell 级（行×列交叉） |
| AuthMiddleware | Better Auth JWT 验证（~~Authlib~~ `fastapi-betterauth`）；JWT 模式从 DB 加载 groups；PG session context 设置 |
| Cedar 下推 | TPE 残差生成；残差→SQL 谓词翻译；SqlGlot AST 注入（子查询/CTE/UNION） |
| PG RLS | object_state 行过滤；Organization 维度 |
| ActionAuthorizer | ADR-011 契约不变；internals 切换；JSON fallback 兼容 |
| ToolExecutor | 工具权限声明；Principal 注入；FORBIDDEN 返回 |

### 10.2 异常路径（100% 覆盖）

- 无权限资源访问 → 不可见（返回空，不报错）
- 五层校验每层拒绝 → 返回 403 + 可读原因
- OIDC token 无效/过期 → 401
- Doris 不可用 → Trino 降级 + 权限补偿
- PG RLS 失败 → fail-closed（拒绝，不 fail-open）
- 策略表达式注入 → 白名单函数拦截
- Marking 未授权 → 数据不可见

### 10.3 集成测试（真 DB + 真引擎）

- 五层校验端到端（创建 Org→Space→Project→Group→Role→User，验证访问）
- 多引擎权限一致性（Doris vs Trino 降级，结果一致）
- Scenario 协同（权限前置校验，overlay 继承 base 权限）
- Action 闭环（五层校验 + 写入 + 审计）
- 跨 Project Reference 二次校验

### 10.4 验证脚本（scripts/verify_*.py）

```bash
# 真实环境验证
scripts/verify_permission_live.py    # 五层校验 + 多引擎下推端到端
scripts/verify_rls_live.py           # PG RLS + SqlGlot AST 注入下推
```

---

## 附录：与评估报告/ADR 的映射

| 本文档章节 | ADR-016 决策 | 评估报告章节 |
|-----------|-------------|-------------|
| §一 数据模型 | D1/D2/D3/D4/D5 | §五 |
| §二 Service | D4/D6 | §三 决策4/6 |
| §三 AuthMiddleware | D9 | §三 决策9 |
| §四 查询下推 | D5/D8 | §六 |
| §五 Action 改造 | D1/D4 | §七 7.2 |
| §六 工具层改造 | D1/D9 | §七 7.3 |
| §七 API 路由 | — | — |
| §八 前端 | — | §八 |
| §九 迁移 | D1/D2/D3 | §四 Phase 0 |
| §十 测试 | — | — |
