# 权限治理 —— 数据层下推与 Python 开源组件选型研究

> **用途**：本文是 ADR-016 + 设计文档的两项深度补充研究的合集：① 数据层计算与查询的权限下推如何落地（各引擎机制 + 避坑指南）；② Python 技术栈中可直接复用的开源组件选型（最大化外部权限对接通用性）。
> **研究方法**：以各引擎官方文档（Doris/PG/Trino/Iceberg）+ 组件官方仓库/文档为第一手来源，辅以生产级避坑博文。
> **研究日期**：2026-07-08
> **关联**：[ADR-016](../architecture/adr-016-permission-governance.md) D5/D8（行/列级 + 多引擎下推）· [设计文档 §四](../design/permission-governance-design.md#四查询层权限下推多引擎) · [评估报告 §六](../architecture/permission-governance-landing-assessment.md#六查询层权限下推方案评估建议)

---

## 目录

- [第一部分：数据层计算与查询的权限下推落地](#第一部分数据层计算与查询的权限下推落地)
- [第二部分：Python 开源组件选型](#第二部分python-开源组件选型)

---

# 第一部分：数据层计算与查询的权限下推落地

## 1.1 各引擎原生权限能力对照

| 引擎 | 行级过滤 | 列级控制 | 数据脱敏 | 统一策略引擎 | 写入路径校验 |
|------|---------|---------|---------|:---:|------------|
| **Doris 4.0.5** | ✅ Row Policy（RESTRICTIVE=AND / PERMISSIVE=OR） | ✅ Column Permission（仅 Select_priv） | ✅ **原生 MASK 函数 + VIEW**（无需 Ranger）/ Ranger（二期） | ✅ Ranger 插件 | ❌ 无（靠应用层） |
| **PostgreSQL 16** | ✅ RLS（USING + WITH CHECK） | ✅ Column Grants | 🟡 视图/函数模拟 | ❌ 无 | ✅ WITH CHECK（INSERT/UPDATE） |
| **Trino 478** | ✅ Ranger/OPA 插件 | ✅ Ranger/OPA 插件 | ✅ Ranger/OPA 插件 | ✅ Ranger/OPA/File | ❌ 无（查询引擎，靠 catalog） |
| **Iceberg** | ❌ 格式层无 | ❌ 格式层无 | ❌ 格式层无 | ❌ 无 | ❌ 无（靠 catalog 层） |
| **Neo4j 5** | 🟡 Cypher WHERE 属性驱动（Community 无 FGAC） | 🟡 序列化层脱敏 | ❌ 无 | ❌ 无 | ❌ 无 |

**关键结论**：
1. **Doris 列脱敏有原生方案**——Doris 4.0 原生提供 `MASK()` / `MASK_SHOW_LAST_4()` 等[脱敏函数](https://doris.apache.org/docs/dev/sql-manual/sql-functions/scalar-functions/string-functions/mask/)，配合 CREATE VIEW 可在存储层脱敏（数据不传应用层），**无需 Ranger**。Ranger Data Masking 是二期更细粒度管理的可选增强，非一期必需
2. **PG RLS 是唯一原生支持写入校验的**（`WITH CHECK`），Action 写入 object_state 可用 RLS 双重保障
3. **Iceberg 权限在 catalog 层**（Gravitino REST Catalog RBAC + credential vending），非格式层非引擎层——这是设计选择（[Iceberg Access Control Patterns](https://iceberglakehouse.com/iceberg/iceberg-access-control/)）
4. **Trino 三种 access control 都支持 row-filter + column-masking**，且有 `hide-inaccessible-columns` 全局属性

## 1.2 Doris 下推落地

### 1.2.1 Row Policy 机制

[官方文档](https://doris.apache.org/docs/4.x/admin-manual/auth/authorization/data/)：

```sql
CREATE ROW POLICY [IF NOT EXISTS] <policy_name> ON <table_name>
AS { RESTRICTIVE | PERMISSIVE }
TO { <user_name> | ROLE <role_name> }
USING (<filter>);
```

- **RESTRICTIVE**：多 policy 用 AND 组合（收紧）
- **PERMISSIVE**：多 policy 用 OR 组合（放宽）
- Doris 自动把 filter 谓词追加到查询（等价于自动加 WHERE）
- **限制**：root/admin 用户不生效（验证时须用普通业务用户）

### 1.2.2 Gaia Doris 下推方案

**Gaia Group → Doris Role 映射**：
```sql
-- Gaia 创建 Group 时同步创建 Doris Role
CREATE ROLE gaia_group_<group_id>;

-- RowSecurityPolicy 编译为 Doris Row Policy
CREATE ROW POLICY policy_region_<ont>_<type> ON idx_<ont>__<type>
AS RESTRICTIVE
TO ROLE gaia_group_<group_id>
USING (region = current_user_region());
-- 注意：Doris 无 session 变量机制，region 须通过用户属性或子查询获取
```

**列脱敏（一期无 Ranger）**：Doris 原生 `MASK()` 函数 + CREATE VIEW，存储层脱敏（数据不传应用层），无需 Ranger。AuthorizationService 根据 PropertyMaskingPolicy 决定用户查原表还是脱敏视图
**列脱敏（二期 Ranger）**：Ranger Data Masking policy（MASK_REDACT / MASK_SHOW_LAST_4 等内置策略）

### 1.2.3 性能考量

- Row Policy filter 下推到 scan 节点，配合 partition pruning + zoneMap + inverted index 数据裁剪
- **等值匹配性能最佳**（region = 'east'），范围/函数表达式次之
- 建议对 filter 列建 inverted index 或 bloom filter（Gaia Doris 表已有 indexed 字段机制）
- 避免复杂 UDF 在 USING 表达式里（影响下推）

## 1.3 PostgreSQL RLS 下推落地

### 1.3.1 RLS 机制

[官方文档](https://www.postgresql.org/docs/18/ddl-rowsecurity.html)：

```sql
-- 启用 RLS
ALTER TABLE object_state ENABLE ROW LEVEL SECURITY;

-- 创建 policy（USING=读过滤，WITH CHECK=写校验）
CREATE POLICY tenant_isolation ON object_state
FOR ALL  -- ALL | SELECT | INSERT | UPDATE | DELETE
TO role_name
USING (organization_id = current_setting('app.principal_organization')::text)   -- 读
WITH CHECK (organization_id = current_setting('app.principal_organization')::text);  -- 写
```

- **USING**：SELECT/UPDATE/DELETE 的行可见性过滤
- **WITH CHECK**：INSERT/UPDATE 的行写入校验（写入的行必须满足条件，否则拒绝）—— **这是 PG 独有的写入路径权限保障**
- **PERMISSIVE**（默认）：多 policy OR；**RESTRICTIVE**：AND

### 1.3.2 Gaia PG RLS 方案

```sql
-- object_state 启用 RLS（Organization + Marking 维度）
ALTER TABLE object_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE object_state FORCE ROW LEVEL SECURITY;  -- 强制（含 owner 也受 RLS）

CREATE POLICY object_state_org_isolation ON object_state
USING (
    ontology_id IN (
        SELECT o.id FROM ontologies o
        JOIN spaces s ON o.space_id = s.id
        JOIN space_organizations so ON s.id = so.space_id
        WHERE so.organization_id = current_setting('app.principal_organization')::text
    )
);

-- 中间件每请求设置上下文
-- SET LOCAL app.principal_organization = '<org_id>';
-- SET LOCAL app.principal_markings = '{pii,confidential}';
```

### 1.3.3 ⚠️ 避坑指南（生产级经验）

| 坑 | 后果 | 解法 |
|----|------|------|
| **PgBouncer transaction pooling** | `SET LOCAL`/`current_setting` session state 跨请求泄露（A 用户的 tenant_id 残留，B 用户看到 A 的数据） | 每事务开头 `SET LOCAL` + 事务结束自动清理；或用 `SET LOCAL` 而非 session 级；或 PgBouncer 用 session pooling（牺牲连接复用） |
| **superuser BYPASSRLS** | 应用用 superuser 连接会绕过所有 RLS | 应用连接用普通角色（非 superuser），加 `FORCE ROW LEVEL SECURITY` |
| **owner 绕过 RLS** | 表 owner 默认绕过 RLS | `ALTER TABLE ... FORCE ROW LEVEL SECURITY` 强制 owner 也受 RLS |
| **`auth.jwt()` 慢** | Supabase 模式下 JWT 解析慢（JSON parsing 每行） | 用 `current_setting()` 传上下文，不用 `auth.jwt()` |
| **policy 函数 volatility** | IMMUTABLE/STABLE 函数可下推索引，VOLATILE 不行 | policy 表达式尽量用 IMMUTABLE 函数 + 等值匹配 |
| **性能开销** | 无索引的 tenant 列全表扫描 | 对 tenant/organization_id 列建索引；基准测试 2-4% 开销（indexed 列） |
| **policy 不可见** | 新客户端不知道有 RLS，查询返回少不报错 | 文档明确；Check Access 工具解释 |

## 1.4 Trino 下推落地

### 1.4.1 三种 system access control

[官方文档](https://trino.io/docs/current/security/overview.html)：

| 方式 | 机制 | row-filter | column-masking | 适用 |
|------|------|:---:|:---:|------|
| **File-based** | JSON 配置文件 | ❌ | ❌ | 简单 catalog/schema/table 级控制 |
| **OPA 插件** | OPA 策略求值 | ✅ | ✅ | 策略外部化，灵活 |
| **Ranger 插件** | Ranger 策略 + audit | ✅ | ✅ | 企业级，统一策略管理 |

**关键特性**：`hide-inaccessible-columns=true`（全局属性）—— `SELECT *` 时无权限列静默隐藏而非报错（对齐"不可见即安全"）。

### 1.4.2 Gaia Trino 方案

#### Gaia Trino 方案

**关键澄清**：Trino 的行级安全不是「应用层后过滤」，而是**计划改写 + 谓词下推**。OPA/Ranger 插件返回行过滤表达式，Trino 在查询计划阶段注入为 WHERE，再通过 predicate pushdown 下推到 connector。性能与原生 WHERE 一样。

- **一期（无 OPA/Ranger）**：ObjectQueryService 把权限 filter（visible_rids）拼进 SQL WHERE，Trino 谓词下推到 connector。已能下推，不是后过滤，但须应用层预计算
- **二期（OPA 插件）**：部署 OPA，行过滤/列脱敏表达式由 OPA 返回，Trino 计划器自动注入。与 Gaia AuthorizationService 策略同步。推荐 OPA（比 Ranger 轻，策略即代码）

## 1.5 Iceberg 下推落地

### 1.5.1 权限在 catalog 层

[Iceberg Access Control Patterns](https://iceberglakehouse.com/iceberg/iceberg-access-control/)：

> 「Apache Iceberg's access control model is enforced at the **catalog layer**: not the storage layer and not the query engine layer.」

Iceberg 格式本身**不做权限**（设计选择——安全属于 catalog 和引擎，不属于文件格式）。权限靠：
- **Catalog RBAC**（Gravitino REST Catalog 的 namespace/table/column 级权限）
- **Credential vending**（catalog 发放短期凭证，引擎只能访问授权数据）

### 1.5.2 Gaia Iceberg 方案

- Gaia 已用 Gravitino 作为 Iceberg REST Catalog（ADR-014）
- **一期**：Iceberg 不直接做行/列级（写入入口，非查询主源）。查询走 Doris（Row Policy 下推）/Trino（谓词下推到 connector）
- **二期**：Gravitino RBAC 管理 Iceberg 表级权限 + credential vending 控制引擎访问

## 1.6 Neo4j 下推落地

### Neo4j Community Edition 的限制

Gaia 用 `neo4j:5-community`（开源版）。Neo4j 的原生细粒度访问控制（FGAC）——`GRANT TRAVERSE` / Property-based access control / ABAC `CREATE AUTH RULE`——**都是 Enterprise Edition 专属**，Community 不可用。因此 Neo4j 权限过滤只能在应用层做（Cypher 查询改写）。

> 学术参考：[Rewriting Graph-DB-Queries for ABAC](https://research.daho.at/assets/papers/rewriting_graph-db-queries_for_abac/Rewriting_Graph_DB_Queries_for_Attribute_based_Access_Control.pdf)——不依赖数据库原生 FGAC，通过查询改写注入过滤条件，Community 可用。

### 方案：属性驱动过滤（非大列表 IN）

**核心思路**：不用 `WHERE m.id IN $visible_ids`（大列表性能差），而是**把权限相关属性作为节点属性投影到 Neo4j**，查询时 Cypher WHERE 直接按属性过滤（与 PG RLS / Doris Row Policy 思路一致——属性驱动，非 ID 列表）。

GraphProjector 投影时（ADR-015 M1 已有机制），把 object 的权限相关属性（region/department 等 RowSecurityPolicy 引用的属性）作为节点属性写入。查询时 WHERE 直接按这些属性过滤，引用 principal 属性（参数传入）。

**三种过滤模式**：
1. **属性匹配**（最常用）：`WHERE m.region = $principal_region`——节点有 region 属性，按 principal 属性过滤
2. **标记过滤**：`WHERE NOT 'VIP' IN m.markings OR 'VIP' IN $principal_markings`——节点带 markings 列表
3. **全可见**（无 RowSecurityPolicy）：不加 WHERE

**visible_ids 仅作兑底**：复杂表达式无法用属性表达时，退化为预计算 visible_ids + `WHERE m.id IN $visible_ids`，但这是例外非主流。

**性能优势**：属性过滤用 Neo4j 索引（对 region/department 属性建索引），性能远优于大列表 IN。且过滤在遍历阶段做（非后过滤）。

**indexed 属性的 Marking 校验**：投影到 Neo4j 的 indexed 属性也须过 Marking 校验（无标记权限的属性不返回，序列化层处理）。

## 1.7 写入路径权限保障

| 路径 | 机制 | 引擎 |
|------|------|------|
| Action 写 object_state | ① ActionAuthorizer 五层校验（写入前） ② PG RLS `WITH CHECK`（写入时双重保障） | PG |
| Action outbox | ActionAuthorizer 校验 + 同事务 | PG |
| SeaTunnel 写 Iceberg | Gravitino RBAC（catalog 层） | Iceberg |
| CDC 同步 PG→Doris | 源端 RLS + 目标端 Doris Row Policy | PG→Doris |

**关键**：PG RLS `WITH CHECK` 是写入路径的唯一存储层保障，其他引擎写入靠应用层 ActionAuthorizer。

## 1.8 数据层下推总结：Gaia 分层适配策略

```
查询路径（读）：
  ObjectQueryService → AuthorizationService 五层校验（PDP）
    → Doris（主）：Row Policy 下推 + 原生 MASK 函数/VIEW 列脱敏（一期）/ Ranger（二期）
    → PG（object_state）：RLS USING 过滤
    → Trino（联邦/降级）：一期 SQL 注入 filter（谓词下推到 connector）/ 二期 OPA 插件（计划改写）
    → Neo4j：Cypher WHERE 属性驱动过滤（非大列表 IN）

写入路径（写）：
  ActionService → ActionAuthorizer 五层校验（写入前）
    → PG object_state：RLS WITH CHECK（写入时双重保障）
    → Iceberg：Gravitino RBAC（catalog 层）
```

**避坑总则**：
1. 应用连接不用 superuser（PG BYPASSRLS）
2. PgBouncer transaction pooling 须每事务 SET LOCAL（防 session state 泄露）
3. Doris root/admin 不受 Row Policy（验证用普通用户）
4. Doris 列脱敏用原生 MASK 函数 + VIEW（一期即存储层脱敏，无需 Ranger）
5. Neo4j Community 无 FGAC（Enterprise 专属），用属性驱动 WHERE 过滤
6. Neo4j 避免大列表 IN（用属性过滤 + 索引；复杂表达式退化为 visible_ids）
7. Iceberg 权限在 catalog 层，别指望格式层
8. policy 表达式用等值匹配 + indexed 列（性能）

---

# 第二部分：Python 开源组件选型

> 选型原则：① 成熟稳定（生产级）② 与 FastAPI/SQLAlchemy 生态契合 ③ 最大化外部权限对接（OIDC/SCIM/LDAP）通用性 ④ 不引入重型依赖。

## 2.1 身份认证（OIDC）

### 2.1.1 候选组件

| 组件 | 机制 | 成熟度 | 外部对接 | FastAPI 集成 |
|------|------|:---:|---------|:---:|
| **Authlib** | 通用 OAuth2/OIDC 客户端，底层 | ★★★★★ | 任意 OIDC | ✅ 官方支持 |
| **fastapi-oidc** | OIDC token 验证中间件 | ★★★★ | Okta/Auth0/任意 | ✅ |
| **fastapi-authlib-keycloak** | Keycloak 专用（JWT/introspection） | ★★★ Beta | Keycloak | ✅ |
| **fastapi-azure-auth** | Azure Entra ID 专用 | ★★★★★ | Azure AD | ✅ |

### 2.1.2 推荐：Authlib + fastapi-oidc

**理由**：
- **Authlib** 是 Python OIDC 事实标准（[官方 FastAPI 集成](https://docs.authlib.org/en/latest/oauth2/client/web/fastapi.html)），不绑定特定 IDP，最大化通用性
- **fastapi-oidc** 在 Authlib 之上提供 OIDC token 验证中间件，任意 OIDC IDP（Okta/Auth0/Keycloak/Authelia）通用
- **不推荐 fastapi-authlib-keycloak / fastapi-azure-auth**：绑定特定 IDP，降低通用性。Gaia 开源本地优先，IDP 应可选可替换

**对接模式**：
- 用户认证：IDP 登录 → OIDC token → Authlib 验证 → Principal
- 属性同步：OIDC claims（department/region/level）→ User.attributes（行级安全用）
- 组同步：OIDC groups claim → Group/GroupMembership

## 2.2 授权引擎（策略求值）

### 2.2.1 候选组件

| 组件 | 模型 | 策略语言 | 状态 | 外部化 | FastAPI | SQLAlchemy |
|------|------|---------|------|:---:|:---:|:---:|
| **Cerbos** | ABAC/PBAC | YAML（可读） | 生产级 | ✅ 独立服务 | ✅ | ✅ 官方教程 |
| **Casbin (pycasbin)** | RBAC/ABAC/ReBAC | 模型+策略文件 | 成熟 | ✅ 嵌入式 | ✅ fastapi-authz | 🟡 |
| **Cedar (cedar_py)** | ABAC | Cedar（AWS） | 早期 | ✅ Rust 绑定 | ✅ | ❌ |
| **OPA** | 通用 | Rego | 生产级 | ✅ 独立服务 | ✅ fastapi-opa | ❌ |
| **自建（simpleeval）** | ABAC | Python 表达式 | — | ❌ 嵌入式 | ✅ | ✅ |

### 2.2.2 推荐：一期自建 simpleeval，二期评估 Cerbos

**一期自建理由**（ADR-016 D6）：
- 一期策略简单（角色 + 标记 + 行级表达式），simpleeval 够用
- 与 ADR-011 ActionRuleEngine 复用同一表达式引擎，降低学习成本
- 无外部依赖，不增加部署复杂度

**二期 Cerbos 推荐理由**：
- **YAML 策略非工程师可读**（[案例](https://www.cerbos.dev/blog/can-non-engineers-manage-authorization-policies-with-cerbos)），对齐「策略即数据」原则
- **官方 SQLAlchemy + FastAPI 集成教程**（[Tutorial](https://docs.cerbos.dev/cerbos/0.50.0/recipes/orm/sqlalchemy/)），与 Gaia 技术栈契合
- **PlanResources API**：返回用户可见资源集（类似 Gaia QueryScope.visible_rids），天然适配查询下推
- **CheckResources API**：批量校验，适配行级过滤
- **独立服务**：策略外部化，多消费者（FastAPI/Trino OPA 也可对接）共享
- 策略入 Git，版本管理，可 diff/审查/回滚

**不推荐 Casbin**：模型文件 + 策略文件双文件，学习曲线陡；嵌入式（非独立服务），多消费者共享难
**不推荐 Cedar**：cedar_py 早期阶段，Rust 绑定增加构建复杂度
**不推荐 OPA（一期）**：Rego 学习曲线陡；独立服务部署重。二期若 Trino 需 OPA 插件可引入

### 2.2.3 Cerbos 集成方案（二期参考）

```yaml
# Cerbos 策略示例（非工程师可读）
apiVersion: "api.cerbos.dev/v1"
resourcePolicy:
  resource: "object_type"
  rules:
    - actions: ["view"]
      effect: EFFECT_ALLOW
      roles: ["viewer"]
      condition:
        match:
          expr: R.attr.region == P.attr.region  # 行级：用户区域匹配数据区域

    - actions: ["view"]
      effect: EFFECT_ALLOW
      roles: ["viewer"]
      condition:
        match:
          expr: "'PII' in P.markings"  # 列级：持 PII 标记才能看 PII 列
```

```python
# Gaia 集成（二期）
from cerbos.sdk.client import CerbosClient

class AuthorizationService:
    def __init__(self, cerbos: CerbosClient):
        self._cerbos = cerbos

    async def evaluate_query_scope(self, principal, object_type) -> QueryScope:
        # PlanResources：返回可见资源过滤器
        plan = await self._cerbos.plan_resources("view", principal, resource)
        # CheckResources：批量校验具体对象
        ...
```

## 2.3 用户同步（SCIM）

### 2.3.1 为什么需要 SCIM

OIDC 解决认证（登录），但**用户/组的生命周期管理**（入职/转岗/离职自动同步）需要 SCIM（System for Cross-domain Identity Management，RFC 7643/7644）。IDP（Okta/Azure AD/Keycloak）通过 SCIM 把用户/组变更推送到 Gaia，实现：
- 入职：IDP 创建用户 → SCIM 推送 → Gaia 自动建 User + 加默认 Group
- 转岗：IDP 改部门 → SCIM 推送 → Gaia 更新 attributes（行级安全联动）
- 离职：IDP 禁用 → SCIM 推送 → Gaia 禁用 User + 移出所有 Group

### 2.3.2 候选组件

| 组件 | 角色 | 成熟度 | 说明 |
|------|------|:---:|------|
| **scim2-models** | SCIM 资源模型（Pydantic） | ★★★★ | RFC 7643/7644 合规，Pydantic 模型 |
| **scim2-client** | SCIM 客户端 | ★★★★ | 主动拉取 IDP 用户/组 |
| **scimpler** | SCIM Server 实现 | ★★★ | Gaia 作为 SCIM ServiceProvider 接收推送 |
| **django-scim2-server** | Django SCIM Server | ★★★ | Django 专用（Gaia 用 FastAPI 不适用） |

### 2.3.3 推荐：scim2-models + 自建 FastAPI SCIM endpoint

**理由**：
- **scim2-models** 提供 RFC 合规的 Pydantic 模型（User/Group/EnterpriseUser），与 Gaia pydantic v2 schema 一致
- Gaia 作为 SCIM ServiceProvider，自建 FastAPI endpoint 接收 IDP 推送（`/scim/v2/Users` `/scim/v2/Groups`）
- 不用 django-scim2-server（框架不匹配）
- scimpler 可参考但不直接用（自建更可控）

**对接模式**：
```
IDP（Okta/Azure/Keycloak） --SCIM推送--> Gaia /scim/v2/*
  → scim2-models 解析 → PrincipalService 同步 → User/Group/attributes 更新
  → 行级安全自动联动（attributes 变 → RLS/Row Policy 上下文变）
```

## 2.4 组件选型总结与集成架构

### 2.4.1 推荐选型

| 层 | 一期 | 二期 |
|----|------|------|
| **OIDC 认证** | Authlib + fastapi-oidc（任意 IDP） | 同 |
| **用户同步** | scim2-models + 自建 SCIM endpoint | 同 |
| **授权引擎** | 自建 simpleeval（与 ADR-011 复用） | **Cerbos**（YAML 策略 + FastAPI/SQLAlchemy 集成） |
| **Trino access control** | 一期 SQL 注入 filter（谓词下推） | **OPA 插件**（计划改写 + 谓词下推，与 Cerbos 策略同步）或 Ranger |
| **Doris 列脱敏** | 应用层（序列化层） | **Ranger**（Data Masking） |

### 2.4.2 集成架构

```
外部身份体系
  IDP（Okta/Azure/Keycloak/Authelia）
    ├── OIDC 登录 → Authlib 验证 token → Principal
    └── SCIM 推送 → scim2-models 解析 → User/Group/attributes 同步
                                        ↓
Gaia 后端（FastAPI）
  AuthMiddleware（OIDC token → Principal → request.state + PG session context）
    ↓
  AuthorizationService（PDP）
    一期：simpleeval 表达式求值（角色 + 标记 + 行级）
    二期：Cerbos 策略求值（YAML 策略，独立服务）
    ↓
  各引擎下推（PEP）
    Doris：Row Policy（一期）+ Ranger Data Masking（二期）
    PG：RLS USING + WITH CHECK
    Trino：一期 SQL 注入 filter（谓词下推）+ OPA（二期，计划改写）
    Neo4j：Cypher WHERE 属性驱动过滤（非大列表 IN）
    Iceberg：Gravitino RBAC（catalog 层）
```

### 2.4.3 通用性设计（最大化外部对接）

1. **IDP 无关**：Authlib + OIDC 标准，任意 IDP 可对接（Okta/Auth0/Keycloak/Authelia/Azure AD）
2. **SCIM 标准**：RFC 7643/7644，主流 IDP 都支持 SCIM 推送
3. **策略外部化**：二期 Cerbos 独立服务，策略可被 Trino OPA/其他系统共享
4. **Principal 抽象**：Gaia Principal 不绑定特定 IDP，OIDC claims → attributes 通用映射
5. **Group 标准化**：SCIM Group + OIDC groups claim 双通道同步

## 2.5 不推荐的方案及原因

| 方案 | 不推荐原因 |
|------|-----------|
| 绑定特定 IDP（fastapi-azure-auth/keycloak 专用） | 降低通用性，Gaia 开源本地优先 IDP 应可选 |
| Casbin | 双文件学习曲线陡，嵌入式多消费者共享难 |
| Cedar (cedar_py) | 早期阶段，Rust 绑定构建复杂 |
| OPA（一期） | Rego 学习曲线陡，独立服务部署重 |
| 自建 SCIM（不用 scim2-models） | RFC 合规成本高，重复造轮子 |
| LDAP 直连 | LDAP 是遗留，OIDC+SCIM 是现代标准（Palantir 也推荐 OIDC） |

---

## 附录：研究来源

### 数据层下推（官方文档）
- Doris Data Access Control: https://doris.apache.org/docs/4.x/admin-manual/auth/authorization/data/
- Doris CREATE ROW POLICY: https://doris.apache.org/docs/3.x/sql-manual/sql-statements/data-governance/CREATE-ROW-POLICY/
- Doris Ranger Authorization: https://doris.apache.org/docs/4.x/admin-manual/auth/authorization/ranger/
- PostgreSQL Row Security Policies: https://www.postgresql.org/docs/18/ddl-rowsecurity.html
- PostgreSQL CREATE POLICY: https://www.postgresql.org/docs/19/sql-createpolicy.html
- Trino Security overview: https://trino.io/docs/current/security/overview.html
- Trino OPA access control: https://trino.io/docs/current/security/opa-access-control.html
- Trino Ranger access control: https://trino.io/docs/current/security/ranger-access-control.html
- Iceberg Access Control Patterns: https://iceberglakehouse.com/iceberg/iceberg-access-control/
- Securing Apache Iceberg Tables: https://iceberglakehouse.com/posts/iceberg-row-column-access-control/

### 避坑指南（生产经验）
- PG RLS + PgBouncer 陷阱: https://mvpfactory.io/blog/row-level-security-in-postgresql-multi-tenant-data-isolation-for-your-saas/
- PG RLS Complete Guide: https://rivestack.io/blog/postgresql-row-level-security
- PG RLS in Practice: https://queryplane.com/blog/postgres-row-level-security-in-practice/
- Supabase RLS 性能: https://jakeinsight.com/tech/2026-03-11-supabase-row-level-security-policy-performance-cos/
- django-rls-tenants 连接池: https://dvoraj75.github.io/django-rls-tenants/guides/connection-pooling/
- Doris Data Pruning: https://doris.apache.org/docs/dev/key-features/data-pruning/
- Ranger + Trino Engineering: https://zenodo.org/records/19473036

### Python 开源组件
- Authlib FastAPI: https://docs.authlib.org/en/latest/oauth2/client/web/fastapi.html
- fastapi-oidc: https://pypi.org/project/fastapi-oidc/
- Cerbos FastAPI: https://www.cerbos.dev/ecosystem/fastapi
- Cerbos SQLAlchemy Tutorial: https://docs.cerbos.dev/cerbos/0.50.0/recipes/orm/sqlalchemy/
- pycasbin fastapi-authz: https://github.com/pycasbin/fastapi-authz/
- cedar_py: https://github.com/burdettadam/cedar_py
- fastapi-opa: https://github.com/busykoala/fastapi-opa
- scim2-models: https://github.com/python-scim/scim2-models
- scim2-client: https://github.com/python-scim/scim2-client
- scimpler: https://github.com/Pagerous/pyscim
