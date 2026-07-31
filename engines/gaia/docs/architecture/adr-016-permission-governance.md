# ADR-016: 权限治理体系（Organization + Space + Project 三层 + RBAC×MAC + 多引擎下推）

| 字段 | 值 |
|------|-----|
| 状态 | Accepted（2026-07-08 评审） |
| 日期 | 2026-07-08 |
| 决策者 | 开发者 + 评审 |
| 影响 | 新建 `core/models/permission.py`（Organization/Space/Project/Principal/User/Group/Role/Marking/Policy/AuditLog）、`services/authorization_service.py`、`middleware/auth.py`；改造 `OntologyService`/`ObjectQueryService`/`ActionService`/`ToolExecutor`；OntologyModel + DataSource/Dataset/SyncTask/Credential 加归属字段；object_state 启用 PG RLS；Doris Row Policy 下推；前端新增权限管理 UI |
| 关联文档 | [adr-017-permission-tech-stack.md](./adr-017-permission-tech-stack.md)（技术选型：Cedar + cashews + Better Auth + SqlGlot，细化本 ADR D6/D8/D9）、[permission-governance-landing-assessment.md](./permission-governance-landing-assessment.md)（评估报告，本 ADR 的依据）、[adr-011-action-p1.md](./adr-011-action-p1.md)（Action 三层权限雏形，本 ADR 内部切换其 internals）、[adr-009-ontology-tool-layer.md](./adr-009-ontology-tool-layer.md)（工具层权限切面）、[adr-014-multi-source-data-fusion-connectors.md](./adr-014-multi-source-data-fusion-connectors.md)（VIRTUAL 目标权限走 Trino 联邦）、../research/palantir-permission-review-and-industry-comparison.md（Palantir 评审+业界对照）、../research/palantir-permission-isolation-reference.md（材料归档） |
| 取代 | ADR-011 的 `ActionAuthorizer` internals（契约不变，内部从 JSON permissions 切换到 AuthorizationService）；`principal=anonymous` 现状；`GravitinoRegistry.check_access` 的 fail-open permissive 行为 |

## 背景

Gaia 当前权限能力几乎空白（见 [评估报告 §一](./permission-governance-landing-assessment.md#一gaia-现状核查真实代码基线)）：
- **身份层**：`principal=anonymous`，无 User/Group/Principal 模型，routes 从 `X-User-Id` 请求头读
- **权限层**：仅 ADR-011 的 Action 三层权限雏形（存 `ActionType.parameters.permissions` JSON），`check_access` fail-open permissive
- **查询层**：`ObjectQueryService` 完全不感知 principal，无权限下推
- **模型层**：Ontology 顶层平铺无归属，DataSource/Dataset 平铺，无 Organization/Space/Project 容器

对照 Palantir Foundry 权限体系（[研究文档](../research/palantir-permission-review-and-industry-comparison.md)）与业界主流方案（Databricks Unity Catalog / Snowflake Horizon / Immuta / Apache Ranger / Google Zanzibar），Gaia 缺失企业级安全基座，是 toB 落地的硬阻断。

## 决策

完整引入 Palantir 五层隔离模型 + 业界共识的 RBAC×ABAC(Tag) 混合 + 多引擎权限下推。做正确的事，不逃避架构复杂度。共 9 项核心决策。

### D1: 完整引入 Organization + Space + Project 三层容器

**决策**：对齐 Palantir 上三层，完整建设（非裁剪）。

```
Organization（主体隔离，MAC）
  └── Space（业务域容器，1:1 绑定 Ontology，组织白名单）
       └── Ontology（业务语义核心，1:1 归属 Space）
            └── ObjectType/ActionType/LinkType/InterfaceType/SharedPropertyType（定义）
            └── ObjectTypeGroup（语义分组，无权限，正交于 Project）
       └── Project（协作权限边界，原子单位）
            └── Dataset/SyncTask/Datasource/Credential（资源，权限归 Project）
            └── Group 角色：Owner/Editor/Viewer/Discoverer
```

**理由**：
- Project ≠ Ontology：Project 是协作单元（给人授权），Ontology 是语义模型（管对象定义）。一个业务域可拆多个 Project（数据/本体/应用团队权限分离），共享一个 Ontology。不能用 Ontology 顶替 Project（评估报告 §二 概念释义）
- Space↔Ontology 1:1 保证业务域语义统一（防止本体碎片化，Palantir 核心哲学）
- Organization 提供主体强隔离（多主体企业协作的硬门槛）
- 单租户部署默认一个 Organization + 一个 Space，渐进式披露不增加认知负担

**Space 基础设施绑定舍弃**：Palantir 的 Space 绑定 Spark/存储/加密/计费，Gaia 无 Spark 且存储多引擎统一管，只保留「容器 + 组织白名单 + 本体绑定」语义。

### D2: Space↔Ontology 1:1 强绑定

**决策**：照搬 Palantir 1:1（创建 Space 自动创建同名 Ontology，Space 删除 Ontology 不可恢复，跨 Space 不可复用本体）。

**实践考量**（评估报告 §三 决策 2）：
- 本体膨胀 → ObjectTypeGroup（Ontology 内语义分组）
- 跨团队权限细分 → Project + Marking
- 跨业务域共享对象 → Reference + Interface
- **DEV/PROD 隔离**：短期多实例部署；长期 Ontology 版本/分支机制（方案 3，待设计项，见 D10）

### D3: 资源归属策略 —— 选项 B 简化 + 预留 project_id 平滑迁移

**决策**：一期定义类资源（ObjectType/ActionType/LinkType 等）归属 Ontology，不单独放 Project；数据模型预留 `project_id`（nullable），未来可平滑迁移到选项 A（定义可放 Project）。

**选项 B 一期归属**：
- Ontology → 归属 Space（加 `space_id`）
- DataSource/Dataset/SyncTask/Credential → 归属 Project（加 `project_id`）
- ObjectType/ActionType/LinkType/InterfaceType/SharedPropertyType → 归属 Ontology，预留 `project_id`（nullable）

**权限查询逻辑**：`project_id` 为空时 fallback 到 Ontology 所属 Project 的角色；非空时直接查该 Project 角色。集中在 `AuthorizationService`，调用方不变。

**B→A 迁移路径**（Palantir 自己也是从 Ontology Roles 演进到 Project-based 的，有[官方迁移工具](https://palantir.com/docs/foundry/ontology-manager/migrate-to-project-based-permissions/)）：填充 project_id + 切换权限查询逻辑，可渐进迁移。

**实例权限跟 backing dataset**（对齐 Palantir）：object_state/object_links 的权限跟数据源，不归定义的 Project。

### D4: 模型选型 —— RBAC + ABAC(Tag) 混合

**决策**：RBAC 管协作授权（Project/Ontology 角色授予 Group），ABAC/Tag 管数据访问（ObjectType/Property 打标记 + 行级策略表达式）。

**理由**：业界共识（Databricks/Snowflake/Immuta/Ranger 都是此模式，见研究 §2.3）。避免 role explosion（ABAC 一条策略覆盖海量资源）。与 ADR-011 的 `roles` + `condition` 模式一致（condition 即轻量 ABAC）。

### D5: 行/列级安全 —— Object/Property Security Policy（非 Restricted View）

**决策**：对齐 Palantir 最新推荐（非过时的 Restricted View，见研究 §1.1 错误2）：
- ObjectType 配 `row_security_policy`（表达式，引用 principal 属性）→ 查询下推 Doris Row Policy / PG RLS
- Property 配 `property_masking_policy` → Doris 原生 MASK+VIEW（存储层）/ 其他引擎序列化层返回 null（脱敏）
- 两者组合 = cell 级（对齐 Palantir Object + Property Security Policy）

动态脱敏不落盘（Snowflake/Databricks 最佳实践）。

### D6: 策略引擎 —— Cedar（详见 ADR-017）

**决策**：采用 Cedar（cedarpy）作为策略求值引擎与行/列级表达式引擎，**放弃 simpleeval**。

**理由**：simpleeval 有五项根本缺陷（黑名单 AST 过滤/无类型系统/无法下推/无 partial evaluation/无安全审计），不应用于安全策略。Cedar 是非图灵完备专用策略语言，类型安全 + TPE 残差下推 + 进程内嵌 + 独立安全基准背书。详见 [ADR-017 D1](./adr-017-permission-tech-stack.md#d1-策略求值与表达式引擎--cedarcedarpy)。

### D7: 标记传播 —— 一期手动打标 + 校验，二期血缘传播

**决策**：一期 ObjectType/Property 手动打标记，查询时合取校验（AND），不做血缘传播。二期视血缘引擎成熟度评估。

**理由**：血缘自动传播价值高但成本高（需血缘引擎实时追踪）。一期手动打标 + 校验已能覆盖核心场景。Gaia 已有 `physical_mapping` 血缘基础，二期可扩展。

### D8: 权限下推 —— SqlGlot AST 注入统一机制（详见 ADR-017）

**决策**：
- **Doris/Trino/PG**（SQL 引擎）：统一走 SqlGlot AST 注入，Cedar TPE 残差翻译为 SQL 谓词注入 WHERE。**放弃 Doris 原生 Row Policy**（静态谓词不支持运行时上下文 + root/admin 不受约束 + 单用户连接池不兼容）
- **PG**（object_state 写入）：RLS WITH CHECK（PG 独特能力，SqlGlot 注入只管读路径）
- **Neo4j**（图遍历）：Cypher WHERE 属性驱动过滤
- **Iceberg**：不直接下推（写入入口，非查询主源）

详见 [ADR-017 D4](./adr-017-permission-tech-stack.md#d4-行级下推--sqlglot-ast-注入统一机制放弃-doris-原生-row-policy)。

### D9: 身份对接 —— Better Auth 双场景 + Authlib JWT 验证（详见 ADR-017）

**决策**：采用 Better Auth（TypeScript/Node.js 独立服务）作为认证服务，满足双场景（本地用户管理 + 企业 SSO 联邦），Gaia FastAPI 用 Authlib 验证 JWT。MVP 保留 `X-User-Id` 请求头模式（开发/测试），生产用 Better Auth。

详见 [ADR-017 D3](./adr-017-permission-tech-stack.md#d3-身份认证--better-auth双场景authlib-应用层-jwt-验证)。

### D10: 与 Scenario 协同 —— 权限独立于 scenario_id

**决策**：权限校验在 scenario overlay 求值之前执行（五层校验），scenario 内 overlay 数据继承 base 权限。scenario_id 与权限字段正交。Scenario 可见性受 Space 约束。

**待设计项（非本 ADR 范围）**：Ontology 版本/分支机制（方案 3，解决 DEV/PROD 隔离，见评估报告 §三 决策 2 + §9.3 #9）。当出现真实 DEV/PROD 需求且多实例成本不可接受时启动设计，需厘清与 Scenario overlay 的边界。

## 权限校验流程（五层串行）

```
请求 → AuthMiddleware 提取 Principal
     → AuthorizationService.check_access(principal, resource, action)
         Layer 1: 身份认证（Principal 有效性）
         Layer 2: Organization 校验（principal.home_organization ∈ resource.space.organizations）
         Layer 3: Space 校验（principal 有 Space 准入角色）
         Layer 4: Project RBAC（principal 有 Project Owner/Editor/Viewer/Discoverer）
                  ↑ 选项 B：定义类资源 project_id 为空时 fallback Ontology 所属 Project
         Layer 5: Marking MAC（resource 全部 marking ⊆ principal.markings）
     → 若全过，进入行/列级下推（Doris Row Policy / PG RLS / Trino 谓词下推 / Neo4j Cypher）
     → 若任一层拒，返回 403 或资源不可见（不可见即安全）
```

**默认拒绝 + 不可见即安全**：无权限资源在前端/搜索/API/SQL 完全隐藏，不提示「无权限」，防枚举探测。但用户主动尝试被拒时，提供可读拒绝原因 + 申请权限入口（Check Access）。

## 角色体系

### 四级角色（对齐 Palantir，简化全局角色）

| 层级 | 角色 | 权限范围 |
|------|------|---------|
| 全局平台 | Platform Admin / Audit Admin | 平台管理 / 仅看审计（权责分离，默认无数据权限） |
| Space 域 | Space Owner / Editor / Viewer / Discoverer | Space 级，继承到所有 Project |
| Project 基础 | Owner / Editor / Viewer / Discoverer | 协作权限边界（最常用），授 Group |
| Marking 管理 | Marking Admin | 标记定义与授权（权责分离，不管项目） |

角色 = 操作的集合（对齐 Palantir：Roles are sets of operations）。一期用默认角色集，不自定义（二期按需评估）。

### 组授权铁律

权限 100% 授 Group，不授个人。User 通过加入 Group 获权。人员异动只调 Group 成员。

## 分期实施（6 期）

详见 [评估报告 §四](./permission-governance-landing-assessment.md#四分期实施路线评估建议)。摘要：

| Phase | 目标 | 工期 |
|-------|------|:---:|
| 0 | 身份基石 + 三层容器（Principal/Group + Organization/Space/Project + 资源归属字段 + 默认初始化） | 2-3 天 |
| 1 | Project RBAC + 角色体系（AuthorizationService 五层校验，后两层 stub） | 2-3 天 |
| 2 | Marking MAC + 标记校验 | 2-3 天 |
| 3 | 行/列级安全 + 多引擎下推（核心难点） | 3-4 天 |
| 4 | 审计 + Check Access + 自助申请 | 2-3 天 |
| 5 | 前端 + 治理工具（渐进式披露） | 3-4 天 |
| 6（二期） | 标记血缘传播 / PBAC / OPA 评估 / 选项 B→A 迁移 / Ontology 版本分支 | — |

总工期 14-20 天（后端 11-15 + 前端 3-4 并行）。

## 与现有特性的协同

| 现有特性 | 协同 | 影响 |
|---------|------|------|
| ADR-011 Action 三层权限 | ActionAuthorizer 契约不变，internals 切换到 AuthorizationService | Layer 1 查 RoleAssignment，Layer 2 五层校验，Layer 3 保留 |
| ADR-009 工具层（22 工具） | ToolExecutor 注入 Principal，工具声明权限 + 目标 Project | 增加权限校验切面 |
| ADR-015 图推理 | DataFrameQueryService 入口五层校验 | 增加权限前置校验 |
| Scenario 沙箱 | 权限独立于 scenario_id，base 权限先行 | 正交，无冲突 |
| ADR-014 多源融合 | VIRTUAL 目标权限走 Trino 联邦 | SQL 注入 filter 谓词下推 |
| TextQL（ADR-012） | NL 查询经 Agent，Agent 以 Principal 身份 | 工具层权限覆盖 |

## 简化设计（"复杂留给自己，简单留给用户"）

基于 [研究 §3.3](../research/palantir-permission-review-and-industry-comparison.md#三复杂留给自己简单留给用户设计哲学) 10 条原则，关键落地：
- **从动作推断意图**（HP 原则）：创建 Space 自动创建 Ontology+Project+三层 Owner，少弹窗
- **策略即数据 + 前后端共享**：后端返回 `allowedActions` + `disabledReasons`，前端只渲染
- **ABAC/Tag 优先，RBAC 兜底**：避免 role explosion
- **打标即保护**：一期手动，二期血缘传播
- **渐进式披露**：单租户默认 Organization 不暴露三层管理
- **Just-in-Time 权限**：自助申请 + 自动审批 + 到期回收
- **LLM 辅助策略生成**（二期，Gaia 差异化机会）：自然语言 → 结构化策略

## 未决问题

详见 [评估报告 §9.3](./permission-governance-landing-assessment.md#93-未决问题需评审决策)（9 项），关键：
1. 策略引擎自建 vs OPA/Cerbos（一期自建，二期评估）
2. PBAC 是否纳入（二期+，强合规可选）
3. LLM 辅助策略生成边界（二期探索）
4. Trino 是否接入 OPA/Ranger（一期 SQL 注入 filter，二期评估插件）
5. 标记血缘传播时机（二期）
6. Ontology 版本/分支机制（方案 3，待设计）

## 参考

- 评估报告：[permission-governance-landing-assessment.md](./permission-governance-landing-assessment.md)
- Palantir 研究：[palantir-permission-review-and-industry-comparison.md](../research/palantir-permission-review-and-industry-comparison.md) · [palantir-permission-isolation-reference.md](../research/palantir-permission-isolation-reference.md)
- 能力差距：[palantir-capability-gap-analysis.md](../research/palantir-capability-gap-analysis.md) §三 P0-B
- Palantir 官方：[Organizations and spaces](https://palantir.com/docs/foundry/security/orgs-and-spaces/) · [Projects and roles](https://palantir.com/docs/foundry/security/projects-and-roles/) · [Markings](https://palantir.com/docs/foundry/security/markings/) · [Object security policies](https://palantir.com/docs/foundry/object-permissioning/object-security-policies/) · [Manage roles](https://palantir.com/docs/foundry/platform-security-management/manage-roles/)
- 业界：[NIST SP 800-162 ABAC](https://nvlpubs.nist.gov/nistpubs/specialpublications/nist.sp.800-162.pdf) · [Google Zanzibar](https://www.usenix.org/system/files/atc19-pang.pdf) · [Databricks Unity Catalog ABAC](https://docs.databricks.com/aws/en/data-governance/unity-catalog/abac/) · [Apache Doris Data Access Control](https://doris.apache.org/docs/4.x/admin-manual/auth/authorization/data/)
- 可用安全：[HP Making Policy Decisions Disappear](https://shiftleft.com/mirrors/www.hpl.hp.com/techreports/2009/HPL-2009-341.pdf)
