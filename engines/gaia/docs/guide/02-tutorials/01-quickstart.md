# 快速开始：全新系统使用指导书

> **象限**：Tutorial
> **读者**：刚拿到一套全新部署的 Gaia 的用户（管理员 / 业务建模者 / 数据分析师）
> **目标**：从空系统开始，30 分钟内走完「组织 → 权限 → 用户 → 数据层 → 语义层 → 决策层」全链路，跑出第一个可用结果
> **代码核实**：2026-07-20 对照 `src/ontology/routes/` + `src/web-ui/src/pages/` + `permission_bootstrap.py`

---

## 0. 你拿到的是什么

Gaia 是一个**本体驱动的智能决策平台**（开源版 Palantir Foundry）。它把企业的业务对象、操作规则、安全约束、AI 交互统一成一个"本体"，让数据从"能查"变成"能决策"。

全新部署后，系统已经替你**自动初始化好了**（无需你动手）：

| 已就绪 | 说明 | 在哪看 |
|--------|------|--------|
| 默认组织 `org-default` | 单租户默认组织 | 设置 → 身份管理 |
| 默认 Space `default` + 默认 Ontology `Default` | 1:1 绑定的工作空间 | 本体构建 → 本体建模 |
| 默认 Project `default` | 协作边界 | 设置 → 身份管理 |
| 11 个内置角色 | PLATFORM_ADMIN / OWNER / EDITOR ... | 设置 → 身份管理 |
| 系统标记 `org:org-default` | MAC 主体隔离 | 设置 → 标记管理 |
| 全部数据引擎 | PG / Iceberg / Doris / Trino / Kafka / SeaTunnel | 运营看板 |

> **你真正要做的第一件事**：登录 → 建第一个业务本体。其余都是"按需"。

### 三层架构（理解这张图就理解了 Gaia）

```
决策层  ── 图探索 / Action 执行 / AI Agent 对话   ← "用本体做决策"
  ↑
语义层  ── 本体建模（对象 / 关系 / 动作 / 权限）   ← "定义业务是什么"
  ↑
数据层  ── 数据源接入 / 数据集同步 / 管道编排      ← "数据从哪来"
```

本指导书按这个顺序展开：先把数据接进来（数据层），再定义业务语义（语义层），最后用来决策（决策层）。组织和权限穿插在前，因为每一步都受它约束。

---

## 1. 登录与首次进入

### 1.1 打开前端

浏览器访问 `http://<部署地址>:5173`（开发态）或生产域名。

### 1.2 两种登录情况

- **有登录页**（生产模式，Better Auth 已启用）：注册第一个账号 → 自动登录。第一个注册的用户建议立即按 §2.3 给自己授予 `PLATFORM_ADMIN` 角色。
- **无登录页，直接进应用**（Dev Mode，`VITE_AUTH_ENABLED=false`）：当前以匿名身份浏览。要操作数据，需在请求头带 `X-User-Id`（开发调试用 curl 时加 `-H "X-User-Id: admin" -H "X-User-Roles: PLATFORM_ADMIN"`）。

> 后续步骤假设你已有一个具备 `PLATFORM_ADMIN` 角色的身份（Dev Mode 下用 header 模拟，生产模式用 Better Auth 账号 + 角色授予）。

### 1.3 认识导航

左侧导航栏分 5 组：

| 分组 | 入口 | 对应层 |
|------|------|--------|
| 🏗️ 本体构建 | 本体建模 / 动作管理 / 图探索 | 语义层 + 决策层 |
| 🔗 数据集成 | 数据源 / 数据集 / 管道编排 | 数据层 |
| 📊 运营看板 | 运营看板 | 全局监控 |
| ⚙️ 设置 | 身份管理 / 权限调试 / 标记管理 / 权限申请 / 审计日志 | 组织 + 权限 |

---

## 2. 组织与用户（设置 → 身份管理）

> 全新系统已有 `org-default`，**单租户部署可直接跳到 §2.2 建用户**。多租户才需新建组织。

### 2.1 （可选）新建组织

多租户场景（每个业务线/子公司一个组织，数据自动隔离）：

> 组织的创建当前由部署/迁移层管理（默认 `org-default` 由 Alembic 预置 + bootstrap 防御性 ensure）。多租户需要新增组织时，联系平台管理员通过 DB 或管理脚本添加，系统会**自动**为新组织派生系统标记 `org:<api_name>`（MAC 隔离）。单租户部署跳过本节。

### 2.2 建用户

**页面**：设置 → 身份管理 → 用户 → 新建

填邮箱、subject（身份标识，OIDC sub claim）、归属组织（默认 `org-default`）、属性（JSON，可用于行级安全，如部门/大区）。

**API**：
```bash
curl -X POST http://localhost:8000/identity/users \
  -H "Content-Type: application/json" \
  -H "X-User-Id: admin" -H "X-User-Roles: PLATFORM_ADMIN" \
  -d '{"email":"alice@corp.com","subject":"alice","home_organization":"org-default","attributes":{"region":"east"}}'
```

> **生产模式（Better Auth）下**：用户注册即自动在 Gaia 建记录（JIT 自动开通，需配 `GAIA_PROVISION_TOKEN`），无需手动建。手动建仅用于服务账号 / 内部账号。

### 2.3 给用户授予角色

角色决定用户能做什么。11 个内置角色分三层：

| 层 | 角色 | 典型场景 |
|----|------|---------|
| 全局 | `PLATFORM_ADMIN` | 平台管理员（管权限，默认无业务数据访问） |
| 全局 | `AUDIT_ADMIN` | 审计员（只读审计日志） |
| 全局 | `MARKING_ADMIN` | 数据分级管理员 |
| Space | `SPACE_OWNER` | 一个业务域的负责人 |
| Space | `SPACE_VIEWER` | 只读访问整个 Space |
| **Project** | `OWNER` / `EDITOR` / `VIEWER` | **最常用**，项目级协作 |

**页面**：设置 → 身份管理 → 用户组 → 选组 → 分配角色（选角色 + scope = 哪个 Space/Project）

> **授权铁律：角色授给“组”，不授给个人**。先把用户加进组（§2.4），再给组授角色，组成员自动继承。这样人员变动只调组、不重授角色。

**API**（把 `default-editors` 组设为默认 Project 的 EDITOR）：
```bash
curl -X POST http://localhost:8000/authz/role-assignments \
  -H "Content-Type: application/json" \
  -H "X-User-Id: admin" -H "X-User-Roles: PLATFORM_ADMIN" \
  -d '{"group_id":"<default-editors组id>","role_name":"EDITOR","scope_type":"PROJECT","scope_id":"<default_project_id>"}'
```

> `scope_type`: `GLOBAL`（scope_id 留空）/ `SPACE` / `PROJECT`。`expires_at` 可选，设了就是临时授权（JIT）。

### 2.4 建用户组与加成员

**页面**：设置 → 身份管理 → 用户组 → 新建组（需选归属组织）→ 加成员

角色授给组（§2.3 铁律），所以要先有组。推荐做法：每个 Project 建一个 `editors` 组 + `viewers` 组，角色挂组上，人进出组即可。

```bash
# 建组（必填 organization_id）
curl -X POST http://localhost:8000/identity/groups \
  -H "Content-Type: application/json" -H "X-User-Id: admin" -H "X-User-Roles: PLATFORM_ADMIN" \
  -d '{"name":"default-editors","description":"默认项目编辑组","organization_id":"<org-default的id>"}'
# 加成员
curl -X POST http://localhost:8000/identity/groups/<group_id>/members \
  -H "Content-Type: application/json" -H "X-User-Id: admin" -H "X-User-Roles: PLATFORM_ADMIN" \
  -d '{"user_id":"<alice_id>"}'
```

---

## 3. 数据层：把数据接进来（数据集成 → 数据源）

> 目标：把企业里某个数据库的表接进来，落成 Gaia 可用的"数据集"。

### 3.1 录入数据源凭证

**页面**：数据集成 → 数据源 → 凭证 → 新建

支持 25+ 种连接器（JDBC / Fileset / Lakehouse / Kafka，含国产库 openGauss/金仓/OceanBase/达梦）。凭证由 `credential_type`（如 `MYSQL`/`POSTGRES`）+ `secret_data`（连接信息 dict，加密存储）组成。

**API**：
```bash
curl -X POST http://localhost:8000/api/credentials \
  -H "Content-Type: application/json" -H "X-User-Id: admin" -H "X-User-Roles: PLATFORM_ADMIN" \
  -d '{
    "api_name":"mysql-prod-cred",
    "credential_type":"MYSQL",
    "secret_data":{"host":"192.168.1.10","port":3306,"username":"readonly","password":"****"}
  }'
```
> 不同 `credential_type` 的 `secret_data` 字段不同，页面表单会根据连接器类型动态生成。完整字段见 `http://localhost:8000/docs`。

### 3.2 注册数据源

**页面**：数据集成 → 数据源 → 新建（选凭证 + 填连接器配置）

**API**（数据源 = 凭证 + 连接器配置；`connector_type` 决定走哪个连接器，`connector_config` 是该连接器的参数，`credential_id` 引用上一步凭证的 id）：
```bash
curl -X POST http://localhost:8000/api/datasources \
  -H "Content-Type: application/json" -H "X-User-Id: admin" -H "X-User-Roles: PLATFORM_ADMIN" \
  -d '{
    "api_name":"sales-mysql",
    "display_name":"销售 MySQL",
    "connector_type":"jdbc-mysql",
    "connector_config":{"database":"sales_db"},
    "credential_id":"<mysql-prod-cred的id>"
  }'
```

### 3.3 测试连接 + 探索表

```bash
# 测试连接
curl -X POST http://localhost:8000/api/datasources/sales-mysql/test-connection -H "X-User-Id: admin" -H "X-User-Roles: PLATFORM_ADMIN"
# 探索有哪些表
curl -X POST http://localhost:8000/api/datasources/sales-mysql/explore -H "X-User-Id: admin" -H "X-User-Roles: PLATFORM_ADMIN"
# 看某张表的样本数据（GET）
curl "http://localhost:8000/api/datasources/sales-mysql/explore/sales_db/orders/sample" -H "X-User-Id: admin" -H "X-User-Roles: PLATFORM_ADMIN"
```

**页面**：数据集成 → 数据源 → 点进去 → “测试连接” / “探索” 按钮，可视化看表结构 + 样本。

### 3.4 创建同步任务（把表同步成数据集）

这一步决定数据“怎么落地”。两条路线，按需选：

| 路线 | 适用 | 落地位置 | 操作 |
|------|------|---------|------|
| **托管表（MANAGED）** | 要在 Gaia 内加速查询 / 跑 Action 写回 | Iceberg（全量明细）+ Doris（索引加速） | 同步任务 → 全量/增量 |
| **虚拟表（VIRTUAL）** | 只读联邦，不想搬数据 | 不落地，Trino 直查源表 | 登记虚拟表 |

> 💡 **部署可裁剪**：若你的场景只用虚拟表（只读联邦），部署时可省掉 Doris / SeaTunnel / Kafka / Kestra，省约 6.5g 内存（见 `docs/engineer/deployment-runbook.md` §3.5）。虚拟表查询只走 Trino，不依赖这些服务。

**页面**：数据集成 → 数据源 → 详情 → 选表 → “同步为托管表” / “登记为虚拟表”

**API**（托管表同步：`sync_type=table`，`source_config` 指定源表，`target_dataset_api_name` 指定目标数据集，`sync_mode` 全量/增量）：
```bash
curl -X POST http://localhost:8000/api/datasources/sales-mysql/sync-tasks \
  -H "Content-Type: application/json" -H "X-User-Id: admin" -H "X-User-Roles: PLATFORM_ADMIN" \
  -d '{
    "api_name":"sync_orders",
    "sync_type":"table",
    "source_config":{"table":"orders"},
    "target_dataset_api_name":"orders_ds",
    "sync_mode":"full_snapshot"
  }'
# 启动同步
curl -X POST http://localhost:8000/api/sync-tasks/sync_orders/start -H "X-User-Id: admin" -H "X-User-Roles: PLATFORM_ADMIN"
```

**CDC 实时同步**（源表变更实时进 Gaia，走 `/cdc-sync` 端点）：
```bash
curl -X POST http://localhost:8000/api/datasources/sales-mysql/cdc-sync \
  -H "Content-Type: application/json" -H "X-User-Id: admin" -H "X-User-Roles: PLATFORM_ADMIN" \
  -d '{"api_name":"cdc_orders","source_config":{"table":"orders"},"target_dataset_api_name":"orders_ds"}'
```

### 3.5 查看数据集

**页面**：数据集成 → 数据集 → 看到所有已落地的数据集（托管/虚拟）

同步完成后，数据集可被本体对象类型绑定（§4.2）、被查询、被 Action 写回。

> **系统已自动做的**：源数据搬运落地、资产注册、索引建好全部自动完成，你只管看数据集列表。

---

## 4. 语义层：定义业务本体（本体构建 → 本体建模）

> 目标：用业务语言定义"有哪些对象、对象之间什么关系、能做什么操作"。这是 Gaia 的核心。

### 4.1 创建本体

**页面**：本体构建 → 本体建模 → 新建本体

> 全新系统已有一个空的 `Default` 本体，可直接用它，或新建业务本体。

**API**：
```bash
curl -X POST http://localhost:8000/ontologies \
  -H "Content-Type: application/json" -H "X-User-Id: admin" -H "X-User-Roles: PLATFORM_ADMIN" \
  -d '{"api_name":"Sales","display_name":"销售本体","description":"销售域业务本体"}'
```

### 4.2 定义对象类型（ObjectType）并绑定数据集

对象类型 = 业务实体（客户、订单、工单）。每个对象类型绑定一个数据集（§3.5 的产物）。

**页面**：本体构建 → 本体建模 → 进本体 → 新建对象类型 → 填属性 → 绑定数据集（选列映射）

**API**（创建 Order 对象类型，绑定 orders_ds 数据集）：
```bash
curl -X POST http://localhost:8000/ontologies/Sales/object-types/create \
  -H "Content-Type: application/json" -H "X-User-Id: admin" -H "X-User-Roles: PLATFORM_ADMIN" \
  -d '{
    "api_name":"Order",
    "display_name":"订单",
    "primary_key":"order_id",
    "title_property":"order_no",
    "storage_type":"MANAGED",
    "properties":[
      {"api_name":"order_id","display_name":"订单ID","data_type":"STRING","is_primary_key":true,"searchable":true},
      {"api_name":"order_no","display_name":"单号","data_type":"STRING","is_title_property":true,"searchable":true},
      {"api_name":"amount","display_name":"金额","data_type":"DECIMAL","searchable":true},
      {"api_name":"status","display_name":"状态","data_type":"STRING","searchable":true}
    ]
  }'

# 绑定数据集 + 字段映射
curl -X PATCH http://localhost:8000/ontologies/Sales/object-types/Order/dataset-link \
  -H "Content-Type: application/json" -H "X-User-Id: admin" -H "X-User-Roles: PLATFORM_ADMIN" \
  -d '{
    "dataset_api_name":"orders_ds",
    "column_mappings":[
      {"property_api_name":"order_id","column_name":"order_id"},
      {"property_api_name":"order_no","column_name":"order_no"},
      {"property_api_name":"amount","column_name":"amount"},
      {"property_api_name":"status","column_name":"status"}
    ]
  }'
```

> 属性字段用 `searchable`（是否可检索，默认 true）而非 `indexed`。`data_type` 支持 STRING/INTEGER/LONG/DOUBLE/DECIMAL/BOOLEAN/DATE/TIMESTAMP/GEOPOINT/GEOSHAPE/TIME_SERIES 等（见 `http://localhost:8000/docs` 的 `DataType` 枚举）。

> `storage_type` 两种：`MANAGED`（数据在 Gaia，可查可写可索引加速）/ `VIRTUAL`（数据在外部源，只读联邦，禁止写）。
> **系统自动做的**：绑定后 Doris 索引表自动 provision + 首次数据回填（Iceberg→Doris），无需手动建索引。

### 4.3 定义关系（LinkType）

**页面**：本体建模 → 关系 → 新建（选源/目标对象类型 + 基数 + 方向）

**API**（需先拿到两个对象类型的 id；`cardinality` 是 ONE/MANY，`direction` 必填 OUTGOING/INCOMING）：
```bash
# 先查 Order / Customer 的 id（这里假设已建好 Customer 对象类型）
ORDER_ID=$(curl -s http://localhost:8000/ontologies/Sales/object-types/Order -H "..." | jq -r .id)
CUSTOMER_ID=$(curl -s http://localhost:8000/ontologies/Sales/object-types/Customer -H "..." | jq -r .id)

curl -X POST http://localhost:8000/ontologies/Sales/link-types \
  -H "Content-Type: application/json" -H "X-User-Id: admin" -H "X-User-Roles: PLATFORM_ADMIN" \
  -d '{
    "api_name":"placedBy",
    "display_name":"下单方",
    "source_object_type_id":"'"$ORDER_ID"'",
    "target_object_type_id":"'"$CUSTOMER_ID"'",
    "cardinality":"MANY",
    "direction":"OUTGOING"
  }'
```

> 页面操作会自动处理 id 查找，比手拼 curl 省事。

### 4.4 对话式建模（推荐，最省事）

不想手填属性？用 AI 对话建模：

**页面**：本体建模 → 右下角 AI 助手 → 输入"帮我定义一个订单对象，包含单号、金额、状态，状态有 pending/paid/shipped"

AI 自动生成 ObjectType 草稿（含属性 + 枚举 + apiName 推导），你确认即落库。

**API**（AG-UI Agent，SSE 流）：
```bash
curl -N -X POST http://localhost:8000/ai/agent \
  -H "Content-Type: application/json" -H "X-User-Id: admin" -H "X-User-Roles: PLATFORM_ADMIN" \
  -d '{"message":"帮我定义一个订单对象，包含单号、金额、状态"}'
```

> 需要 `.env` 配好 `AI_MODEL` + provider key。对话式建模涉及"造本体"操作时，系统会发起 HITL 审批（AG-UI interrupt / MCP elicit），你点确认才落库。

### 4.5 查询对象（验证语义层）

定义完对象类型 + 绑定数据集后，就能用业务语言查了：

**结构化查询（ObjectSet IR，确定性，无 LLM）**——`/objects/*` 路由吃 ObjectSet IR（对齐 Palantir ObjectSet），不接纳自然语言：
```bash
# 查所有状态为 paid 的订单（IR：objectType 起始集 + filters 简写 AND）
curl -X POST http://localhost:8000/objects/Sales/query-dataframe \
  -H "Content-Type: application/json" -H "X-User-Id: admin" -H "X-User-Roles: PLATFORM_ADMIN" \
  -d '{
    "type":"objectType",
    "object_type":"Order",
    "filters":[{"field":"status","op":"exactMatch","value":"paid"}]
  }'

# 复杂查询：金额 > 1000 且状态在 [paid, shipped] 的订单（嵌套 where 的 and）
curl -X POST http://localhost:8000/objects/Sales/query-dataframe -H "..." -d '{
  "type":"objectType",
  "object_type":"Order",
  "where":{"type":"and","filters":[
    {"field":"amount","op":"greaterThan","value":1000},
    {"field":"status","op":"in","value":["paid","shipped"]}
  ]}
}'
```
> IR 的 filter 用 `op`（如 `exactMatch` / `in` / `greaterThan` / `range` / `withinDistance`），完整算子见 `http://localhost:8000/docs` 的 `ObjectSetIR` schema。空间过滤用 `withinDistance`/`withinPolygon`，时序用 `timeRange`。

**自然语言查询（走 AI Agent）**：
```bash
curl -N -X POST http://localhost:8000/ai/agent \
  -H "Content-Type: application/json" -H "X-User-Id: admin" -H "X-User-Roles: PLATFORM_ADMIN" \
  -d '{"message":"帮我找出金额大于 1000 的已支付订单"}'
```

> 两层正交：`/objects/*` 路由只吃结构化 IR（确定性）；`/ai/agent` 吃自然语言（LLM 驱动，内部调结构化工具）。脚本/外部 Agent 走 MCP `query_with_dataframe` 工具。

---

## 5. 决策层一：定义并执行动作（Action）

> 目标：把"业务操作"变成可执行、受权限管控、原子落库的 Action（如"关闭订单""审批工单"）。

### 5.1 定义动作类型（ActionType）

**页面**：本体构建 → 动作管理 → 新建动作

定义参数、提交校验规则（`submission_criteria`，simpleeval 表达式）、本体规则（`ontology_rules`，声明式参数→对象变更映射）、影响的对象类型。Action 主要几类：
- **写回型**：修改对象属性（status: pending→paid），写 object_state + 经 outbox 同步 Iceberg/Doris
- **关系型**：建立/解除对象间关系（RELADE/UNRELATE）

> Action 定义字段较多（`parameters` / `rules` / `submission_criteria` / `ontology_rules` / `effects` / `risk_level`），**强烈建议用页面表单或 AI 对话建模**，避免手拼 JSON 出错。完整字段见 `http://localhost:8000/docs` 的 `ActionTypeCreate` schema。

**API**（简化示例，关闭订单：参数 reason + 把 status 改成 closed + 仅当当前状态非 closed 才允许）：
```bash
curl -X POST http://localhost:8000/actions/definitions/Sales/closeOrder \
  -H "Content-Type: application/json" -H "X-User-Id: admin" -H "X-User-Roles: PLATFORM_ADMIN" \
  -d '{
    "api_name":"closeOrder",
    "display_name":"关闭订单",
    "affected_object_type_api_name":"Order",
    "operation_kind":"update",
    "parameters":[{"api_name":"reason","display_name":"关闭原因","data_type":"STRING","required":true}],
    "submission_criteria":[{"expression":"status != \"closed\"","error_message":"订单已关闭，不可重复关闭"}],
    "ontology_rules":[{"property":"status","value":"closed"}],
    "risk_level":"low"
  }'
```

### 5.2 执行动作

**页面**：图探索 → 选中对象 → 动作菜单 → 执行

**API**（`rid` 作为 parameters 的一个字段传入；幂等键可选）：
```bash
curl -X POST http://localhost:8000/actions/execute/Sales/Order/closeOrder \
  -H "Content-Type: application/json" -H "X-User-Id: alice" -H "X-User-Roles: EDITOR" \
  -d '{"parameters":{"rid":"ORD-001","reason":"客户取消"}}'
```

执行后系统**自动**完成（你无需关心）：
1. PG 原子提交（object_state + outbox）
2. 参数校验 + `submission_criteria` 求值（不满足返回 422）
3. read-your-writes：立即能查到新状态
4. outbox 驱动：≤1s 同步 Doris 索引 / ≤5min 归档 Iceberg / 图边投影（若开 Neo4j）
5. 审计日志记录

> **VIRTUAL 目标的 Action 会被拒绝**（422）——虚拟表只读，不可写。

### 5.3 批量动作

> ActionType 定义时需设 `batch_enabled: true` 才能批量执行。

```bash
curl -X POST http://localhost:8000/actions/execute-batch/Sales/Order/closeOrder \
  -H "Content-Type: application/json" -H "X-User-Id: alice" -H "X-User-Roles: EDITOR" \
  -d '{"items":[{"rid":"ORD-001"},{"rid":"ORD-002"}],"fail_fast":false}'
```

分片调度 + 逐项原子事务，部分失败不影响其他项（`fail_fast=false`，返回 `status=partial` + 每项结果）。

---

## 6. 决策层二：图探索与关联推理（图探索）

> 目标：从某个对象出发，探索它的关联关系，做多跳推理和决策分析。

### 6.1 打开图探索

**页面**：本体构建 → 图探索 → 选本体（如 Sales）

画布以图谱形式展示对象节点 + 关系边。支持：邻接高亮、zoom/pan、千级节点。

### 6.2 对话式探索（推荐）

**页面**：图探索 → 右侧 AI 助手 → 输入自然语言

示例：
- "找出订单 ORD-001 的供应商，以及该供应商的其他订单"
- "哪些客户的订单金额超过 1 万且状态是 paid"
- "显示所有 7 天内未发货的订单及其客户"

AI Agent（AG-UI ReAct）自动：解析意图 → 构造 ObjectSet IR → 调多引擎（PG 属性 / Neo4j 图遍历 / PostGIS 空间 / TimescaleDB 时序）→ 证据链追溯 → 在画布上高亮结果 + 文字解释。

**API**：
```bash
curl -N -X POST http://localhost:8000/ai/agent \
  -H "Content-Type: application/json" -H "X-User-Id: alice" -H "X-User-Roles: EDITOR" \
  -d '{"message":"找出订单 ORD-001 的供应商的其他订单","ontology":"Sales"}'
```

### 6.3 结构化关联查询（脚本/外部 Agent 用）

```bash
# 邻居探索（单跳遍历：某订单的下单方是谁）
curl -X POST http://localhost:8000/objects/Sales/traverse \
  -H "Content-Type: application/json" -H "X-User-Id: alice" -H "X-User-Roles: EDITOR" \
  -d '{"link_type":"placedBy","source_keys":["ORD-001"],"direction":"forward"}'

# 路径查找（ORD-001 到 CUST-100 的最短路径）
curl -X POST http://localhost:8000/objects/Sales/find-paths \
  -H "..." -d '{"source_key":"ORD-001","target_key":"CUST-100","max_depth":4}'

# 是否存在关联（ORD-001 是否关联了 CUST-100）
curl -X POST http://localhost:8000/objects/Sales/exists-link \
  -H "..." -d '{"link_type":"placedBy","source_key":"ORD-001","target_key":"CUST-100"}'
```

> 图推理需 Neo4j（`--profile graph`）。未启 Neo4j 时，图遍历自动降级到 PG 关联表（功能在，性能略低）。

### 6.4 时空分析（可选，需 PostGIS/TimescaleDB 已随 PG 启用）

若对象有空间属性（经纬度）或时序属性，可做空间过滤与轨迹回放。这两个端点面向前端图探索画布的 MapPanel / TrajectoryPlayer，输入需要候选 `rid` 列表 / `series_id`，**建议直接在图探索页面操作**（画布框选、轨迹播放按钮），而非手拼 curl。完整请求体见 `http://localhost:8000/docs`：

- `POST /objects/{ont}/spatial-filter`：从候选 rid 中返回命中空间条件（`withinDistance` / `withinPolygon` / `withinBoundingBox`）的 rid（PostGIS GiST 索引）
- `POST /objects/{ont}/series-query`：按 `series_property` + `series_ids` + 时间窗口返回轨迹点（TimescaleDB 超表）

> 也可以用 `query-dataframe` 的 ObjectSet IR 直接带空间/时序 filter（`withinDistance`/`timeRange` 算子），一次调用完成“过滤 + 水合”，比拆分调用更简便。

---

## 7. 权限与治理（设置）

前三层做完，回过头看权限治理——它贯穿每一步，但通常在有了业务对象后再精调。

### 7.1 权限模型四层

Gaia 权限是四层叠加（任一层拒绝即拒绝）：

| 层 | 机制 | 在哪配 |
|----|------|--------|
| 1 认证 | 登录身份（Better Auth JWT / Dev header） | Better Auth / header |
| 2 RBAC | 角色 × 资源权限矩阵（§2.3） | 身份管理 → 角色分配 |
| 3 MAC | 数据分级标记（涉密/内部/公开） | 标记管理 |
| 4 行级 | Row Security Policy（按属性过滤行） | 权限调试 → 行级策略 |

### 7.2 标记管理（数据分级）

**页面**：设置 → 标记管理

给数据集/对象类型打标记（如"机密""内部"），用户持有对应标记才能看。

```bash
# 建标记分类 + 标记（页面操作更直观）
# 系统已自动为每个组织派生 org:<name> 系统标记（主体隔离）
```

### 7.3 行级安全策略

按用户属性过滤可见行。例：销售只能看自己大区的订单（用户 attributes 里的 `region` 要等于资源的 `region`）。

**页面**：设置 → 权限调试 → 行级策略 → 新建

```bash
curl -X POST http://localhost:8000/authz/row-security-policies \
  -H "Content-Type: application/json" -H "X-User-Id: admin" -H "X-User-Roles: PLATFORM_ADMIN" \
  -d '{
    "object_type_id":"<Order的id>",
    "expression":"principal.attributes.region == resource.region",
    "description":"销售只能看本大区订单"
  }'
```

> `expression` 是 Cedar 条件表达式，引用 `principal.attributes.*`（用户属性，§2.2 建用户时填的 attributes）和 `resource.*`（对象属性）。也可用 `/authz/generate-policy` 让 LLM 辅助生成。

### 7.4 权限调试与申请

- **权限调试**：设置 → 权限调试 → 输入用户 + 资源 + 操作，看是否放行 + 命中哪层
- **权限申请**：用户没有权限时，可发起申请（设置 → 权限申请），审批人批准后自动授权
- **审计日志**：所有敏感操作（Action 执行 / 权限变更 / 数据访问）自动记录，AUDIT_ADMIN 可查

```bash
# 检查权限（GET + query 参数，返回逐层状态 + 缺什么）
curl "http://localhost:8000/authz/check?resource_type=ACTION_TYPE&resource_id=closeOrder&action=action_type:execute" \
  -H "X-User-Id: alice" -H "X-User-Roles: EDITOR"

# 看审计日志
curl http://localhost:8000/authz/audit-logs -H "X-User-Id: admin" -H "X-User-Roles: AUDIT_ADMIN"
```

---

## 8. 管道编排（可选，数据集成 → 管道编排）

> 复杂 ETL 场景（多源 join / 定时调度 / 失败重试）用 Pipeline Builder（基于 Kestra）。

**页面**：数据集成 → 管道编排 → 新建

可视化拖拽编排（Source → Transform → Sink），支持 DuckDB 内联转换。执行状态自动同步回 Gaia。

> 仅当部署了 Kestra 服务（`docker compose` 默认含）可用。轻量同步走 §3.4 的同步任务即可，无需 Pipeline Builder。

---

## 9. 运营看板（运营看板）

**页面**：运营看板

一屏看全局：数据引擎健康状态、同步任务运行情况、Action 执行统计、资源用量。部署异常或同步失败会在这里红色提示。

---

## 10. 三种使用入口（给不同角色）

Gaia 能力通过三个入口暴露，按你的角色选：

| 入口 | 适合谁 | 怎么用 |
|------|--------|--------|
| **Web UI** | 业务建模者、数据分析师、管理员 | 浏览器，可视化操作（本指导书主线） |
| **AI Agent**（`/ai/agent`） | 想用自然语言完成复杂任务的人 | 页面右下角助手 / curl SSE |
| **MCP / 脚本** | 外部 Agent 开发者、自动化脚本 | MCP 客户端连 Gaia MCP server，13 个工具（查询/推理/执行已定义 Action/即席建模） |

> 三入口能力分层：MCP 是对外操作面（用本体），REST 是全功能管理面（造本体/管数据源）。详见 ADR-019。

---

## 11. 全新系统 30 分钟上手 Checklist

```
[ ] 1. 登录（生产：注册账号；Dev：带 X-User-Id header）
[ ] 2. 确认自己有 PLATFORM_ADMIN（生产首次注册用户需手动授）
[ ] 3. （多租户）建组织 + 建用户组 + 加成员
[ ] 4. 数据层：录凭证 → 建数据源 → 测试连接 → 探索表
[ ] 5. 数据层：同步一张表为托管数据集（或登记虚拟表）
[ ] 6. 语义层：建本体 → 建对象类型 → 绑定数据集
[ ] 7. 语义层：（可选）用 AI 对话建模加速
[ ] 8. 语义层：查对象，验证数据能取到
[ ] 9. 决策层：定义一个 Action（如关闭订单）
[ ] 10. 决策层：执行 Action，验证状态变更 + 审计日志
[ ] 11. 决策层：图探索，从一个对象出发看关联
[ ] 12. 治理：按需配标记 / 行级策略 / 权限申请流程
```

---

## 12. 常见问题

| 问题 | 答案 |
|------|------|
| 第一次进来该干啥？ | 直接去 §4 建本体（默认组织/Space/Project/角色系统已建好） |
| 必须先建组织吗？ | 单租户不用，已有 `org-default`。多租户才建 |
| 数据必须搬进来吗？ | 不必。只读场景用虚拟表（VIRTUAL），不落地，Trino 联邦直查 |
| Action 能改外部数据吗？ | VIRTUAL 目标禁止写。MANAGED 目标可写，经 outbox 同步 |
| 图探索必须开 Neo4j 吗？ | 不必。没 Neo4j 自动降级到 PG 关联表，功能在、性能略低 |
| 自然语言查询准吗？ | `/objects/*` 走结构化 IR 100% 确定；`/ai/agent` 走 LLM，复杂查询建议人工确认 |
| 怎么把权限授给一群人？ | 建用户组 → 角色挂组上 → 人进出组（§2.4） |
| 部署出问题看哪？ | 运营看板看红绿灯；详细排查见 `docs/engineer/deployment-runbook.md` |

---

## 深入

- **部署**：`docs/engineer/deployment-runbook.md`
- **架构**：`docs/guide/01-overview/04-data-flow.md`（6 种数据流场景）
- **概念**：`docs/guide/04-concepts/`（本体建模 / Action 闭环 / 图推理 / 权限）
- **API 参考**：`http://localhost:8000/docs`（OpenAPI 交互文档）
- **操作指南**：`docs/guide/03-how-to/`（建模 / 数据接入 / Action / 查询 / 权限 / 运维）
