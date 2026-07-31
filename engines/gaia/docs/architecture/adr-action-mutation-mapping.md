# ADR: ActionType Ontology Rules 声明式变更模型(方案 2 v3)

> 状态:**草案待评审 v3** · 2026-06-26
> 关联:范式 B 修复(commit c78d4d6)解决"校验失败该报错却 200";本 ADR 解决
> 剩余 7 个 write benchmark FAIL 的根因 —— ActionType 无法声明"参数→对象变更"映射。
> v3 基于 Palantir Foundry Action 全能力参考重写,前后端一体设计;
> 纠正 v2 对写回链路的误解(见 §2.4)。

---

## 一、要解决的问题

范式 B 修复后 write benchmark 7 个 FAIL,全部归因同一缺口:

| 用例 | 期望 | 当前实际 | 根因 |
|------|------|---------|------|
| write_001/010 | 更新 flight 表字段 | 200 applied 但源表未变 | `_build_mutations` 默认 CREATE_OBJECT,没定位到目标对象 |
| write_004/012 | 不存在对象 → 404 | 200 applied | CREATE_OBJECT 无条件新建,从不校验存在性 |
| write_011 | Maintenance 飞机 → 422 | 200 applied | 无对象引用参数,无目标对象状态校验 |
| write_audit_001/002 | `flight_status_log` 记录 | 日志表空 | 无"创建副表对象"机制 |
| write_conflict_001 | 500 并发同对象 OCC ≥99% | 75% | 各 CREATE 新对象,无共享行 OCC |

**核心缺口**:ActionType 只能声明"参数 + 校验规则",**无法声明 Ontology Rules
(对哪个对象、按什么主键匹配、做 CREATE/UPDATE/DELETE/Upsert、属性值从哪来)**。
`_build_mutations` 靠硬编码猜测(有 `rid` 参数→UPDATE,否则→CREATE)。

---

## 二、背景:Palantir Foundry 模型 + Gaia 差距

### 2.1 Foundry 的 Action 核心构成(5 部分)

```
Parameters(含 ObjectReference<T> 对象引用参数,主键匹配的核心载体)
  → Submission Criteria(执行前前置校验,AND 关系)
  → Ontology Rules(核心变更:CreateObject/ModifyObject/Upsert/DeleteObject/Create link/Delete link)
  → Side Effects(本体变更外的附加:Notification/Webhook/Schedule,异步,失败不回滚)
  → Security(角色权限 + 对象编辑权限双重校验 + 审计 + OCC)
```

**关键设计**:
1. **对象匹配完全基于主键** —— Modify/Delete 的目标对象主键从 `ObjectReference<T>` 参数提取;Upsert 先匹配后创建
2. **属性值来源四类** —— `PARAMETER`(输入参数)/`OBJECT_PROPERTY`(关联对象属性)/`STATIC_VALUE`(常量)/`SYSTEM_CONTEXT`(currentUser/CURRENT_TIMESTAMP)/`SYSTEM_GENERATED`(UUID,主键)
3. **UPDATE 是局部增量** —— 仅改显式声明的属性,未声明保持原值;**主键不可修改**
4. **事务原子性** —— 单次提交所有 Ontology Rules 同事务,失败全回滚;Side Effects 异步
5. **OCC 默认启用** —— 基于对象版本号,提交时版本不匹配 → 冲突拦截
6. **两种声明模式** —— 配置式(低代码,覆盖 80% CRUD)/ 函数式(TypeScript `@OntologyEditFunction`,复杂逻辑);**函数式下不能再配 Ontology Rules**

### 2.2 Gaia 当前能力 vs Foundry 差距

| 能力 | Foundry | Gaia 现状 | 差距 |
|------|---------|----------|------|
| ObjectReference 参数(承载主键) | ✅ | ❌ 只有裸值参数 | **缺** |
| Ontology Rules(CRUD 声明) | ✅ 5 类规则 | ❌ 无,`_build_mutations` 硬编码猜测 | **缺** |
| 属性值来源(PARAMETER/OBJECT_PROPERTY/STATIC/SYSTEM) | ✅ 4 类 | ❌ 全塞 properties | **缺**(OBJECT_PROPERTY 限读输入参数引用对象,决策 7) |
| Upsert(先匹配后创建) | ✅ | ❌ | **缺** |
| 对象存在性校验(match 失败→404) | ✅ 规则可查 | ❌ CREATE 不查 | **缺** |
| Side Effects(Notification/Webhook) | ✅ | ⚠️ 有 `ActionEffectConfig` 但仅 webhook/write_back/sub_action/kafka,无 Notification | **部分** |
| OCC 版本号 | ✅ 默认 | ✅ 有 `expected_version` 但调用方不传→当 CREATE | **衔接断** |
| 主键不可修改 | ✅ 强制 | ❌ 无约束 | **缺** |
| 函数式 Action | ✅ TypeScript | ❌ | **缺(本期不做)** |
| 链接规则(Create/Delete link) | ✅ 多对多 | ⚠️ mutation 有 RELATE/UNRELATE 类型但无声明入口 | **部分** |

### 2.3 Gaia 已有的有利条件(不需从零建)

- `ObjectType.primary_key`、`Property.physical_mapping`(table_name+column_name)**已存在** —— 主键匹配 + write_back 回写源表有据可依
- `object_state.rid` 是裸字符串,CREATE 用 `ON CONFLICT DO NOTHING` —— **业务键作 rid 可行**(benchmark flight_id 唯一)
- `ActionEffectConfig` 已支持 webhook/write_back/sub_action/kafka —— Side Effects 框架在
- mutation 内部已有 `CREATE_OBJECT/UPDATE_OBJECT/DELETE_OBJECT/RELATE/UNRELATE/CLEAR_LINKS` 类型 —— 执行层 op 齐全,缺的是**声明入口 + 构建逻辑**
- `ActionRuleEngine` 用 simpleeval 沙箱,`derivation` 可算参数值 —— 值来源的"表达式"可复用
- `ObjectQueryService`/`DorisIndexStore.load_by_ids` 已能按主键点查全量对象 —— hydrate 复用此能力
- `WriteBackManager` 已实现 Palantir Write-back 模式(upsert/merge SQL + 反馈环标记)—— 回写 MySQL 源表现成

### 2.4 写回链路的正确理解(v3 纠正 v2 误解)

v2 曾误以为"Action 应直接写 MySQL 源表"并设计了 `auto_write_back` 默认开。
经与项目设计确认,正确的写回链路是:

```
Action 执行(必选):写 PG object_state  →  read-your-writes,客户立即在 Doris 可见最新数据
                  (经 CDC 异步同步 Iceberg→Doris)

回写源系统(可选,用户配置):配 write_back effect  →  Outbox 异步  →  WriteBackManager 回写 MySQL 源表
                  未配 effect 的 Action(如纯通知)不回写源表
```

**两条铁律**:
1. **object_state 永远写**(Action 必选行为),这是 read-your-writes 的基础,无开关
2. **write_back 回写 MySQL 源表是用户显式配置的 effect**,不是默认行为。配了才经 Outbox 回写

因此:
- ❌ 删除 v2 的 `auto_write_back` 默认开 —— write_back 走显式 effect 配置
- benchmark 的 DelayFlight/ReassignAircraft **需显式配 write_back effect** 回写 MySQL flight 表,
  postcondition 查 MySQL 才能通过(符合 BENCHMARK_DESIGN.md §388)
- 纯通知类 Action 只写 object_state + 发通知,不碰源表

### 2.5 object_state 语义 + hydrate(v3 决策 C)

object_state 是"操作态",**只记录被 Action 改过的对象**。benchmark 的 flight 数据在
MySQL→Iceberg→Doris,但 object_state 表初始为空。因此 ModifyObject 命中 object_state
缺失时,采用**决策 C:从 Doris/Iceberg hydrate 全量当前值**:

```
ModifyObject(target=flight_id=100):
  1. get_object_state(100) → None(object_state 无记录)
  2. hydrate: ObjectQueryService 从 Doris load_by_ids(100) → 全量当前属性
  3. 写入 object_state(全量快照, version=1)
  4. apply Modify:UPDATE object_state,合并 rule 声明的属性覆盖 hydrate 值
  → object_state 始终是全量快照,read-your-writes 一致,不会读到半截数据
```

这保证 object_state 语义对标 Palantir object storage(全量镜像),且复用成熟 read 路径。
`on_missing="raise_not_found"` 仍用于"Doris 里也不存在该对象"的真 404 场景(write_004/012)。

---

## 三、方案设计(对标 Foundry,适配 Gaia)

### 3.1 范围界定:本期做配置式,不做函数式

Foundry 两种模式中,**函数式(TypeScript `@OntologyEditFunction`)需要 Functions 运行时,过重**。本期只做**配置式声明**(低代码 Ontology Rules),覆盖 benchmark 全部场景(标准 CRUD + 副表日志 + 条件更新)。函数式留作未来迭代(可用一个受限的"表达式规则"过渡,见 §3.6)。

### 3.2 核心扩展:ObjectReference 参数 + Ontology Rules + 值来源

#### 3.2.1 参数增加对象引用语义

`ActionTypeParameter` 已有 `object_type_ref: str | None`(P1 已加但未用)。本期激活:

```python
# ActionTypeParameter(已有字段,本期启用语义)
object_type_ref: str | None = None   # 非 None 即 ObjectReference<该对象类型>
is_object_set: bool = False          # True = 多值(批量)
# 执行时:该参数值 = 对象主键值(benchmark 传 flight_id 数字即可)
# 前端:渲染为对象选择器(可加 filter)
```

**不需要新字段**,只需在 `_build_mutations` 和前端识别 `object_type_ref`。这是最小侵入。

#### 3.2.2 新增 Ontology Rules 声明(核心)

`ActionTypeCreate` / ORM / ActionType 增加 `ontology_rules: list[OntologyRule]`:

```python
class ValueSource(BaseModel):
    """属性值来源(对标 Foundry 4 类 + 表达式扩展)。"""
    source: Literal["PARAMETER", "OBJECT_PROPERTY", "STATIC_VALUE",
                    "SYSTEM_CONTEXT", "SYSTEM_GENERATED", "EXPRESSION"]
    value: str | None = None
    # PARAMETER:        value=参数api_name,如 "delay_minutes"
    # OBJECT_PROPERTY:  value="workOrder.equipment.equipmentId" 路径
    # STATIC_VALUE:     value="Delayed" 字面量
    # SYSTEM_CONTEXT:   value="CURRENT_USER_ID"|"CURRENT_TIMESTAMP"
    # SYSTEM_GENERATED: value="uuid"(主键自动生成)
    # EXPRESSION:       value="delay_minutes * 2" simpleeval 表达式(过渡函数式能力)

class OntologyRule(BaseModel):
    """对标 Foundry Ontology Rules 的一类声明式变更。"""
    type: Literal["CreateObject", "ModifyObject", "UpsertObject",
                  "DeleteObject", "CreateLink", "DeleteLink"]
    # 目标对象定位:
    # - Modify/Upsert/Delete: target_parameter = ObjectReference 参数名,
    #   执行时取其值作主键,匹配 ObjectType.primary_key
    # - Create: target_object_type = 显式对象类型
    target_parameter: str | None = None     # 引用参数(含主键)
    target_object_type: str | None = None   # Create/Upsert 时显式指定
    # 注:本期不支持 target_path 跨对象路径(决策 7);字段保留但执行期忽略
    target_path: str | None = None
    # 属性赋值:{"属性api_name": ValueSource};主键不可出现在 Modify 的 properties
    properties: dict[str, ValueSource] = Field(default_factory=dict)
    # 链接规则专用
    link_type: str | None = None
    source_parameter: str | None = None
    target_link_parameter: str | None = None
    # 条件执行(simpleeval,如 "$isUrgent = true");None=无条件
    condition: str | None = None
    # Upsert 命中 0 行时:raise_not_found(默认,与 Modify 一致) | create
    on_missing: Literal["raise_not_found", "create"] = "raise_not_found"
    description: str = ""
```

#### 3.2.3 Side Effects 补 Notification

`ActionEffectConfig.type` 增加 `"notification"`(对标 Foundry):

```python
type: Literal["webhook", "write_back", "sub_action", "kafka_topic", "notification"]
# notification config: {recipients:[...], title, content_template, channel:[IN_APP,EMAIL]}
trigger: Literal["BEFORE_ONTOLOGY_CHANGE", "AFTER_ONTOLOGY_CHANGE"] = "AFTER_ONTOLOGY_CHANGE"
condition: str | None = None   # 条件触发
```

`write_back` 用于副表(如 `flight_status_log`)—— 实际上副表日志更接近"创建一条记录",见 §3.4 的取舍。

### 3.3 执行流程改造(`ActionService.execute_action`)

对标 Foundry 执行时序,在现有流程插入 Ontology Rules 构建 + hydrate(决策 C):

```
1. 参数格式校验(现有 _validator.validate)            [unchanged]
2. 双重权限校验(现有 check_execute_permission + check_access)  [unchanged]
3. 提交校验(现有 submission_criteria)                [unchanged]
4. 事务内 Ontology Rules 构建 + 执行:                [重写 _build_mutations → _build_mutations_from_rules]
   for rule in ontology_rules(按声明顺序):
     if rule.condition 且 eval 为假: skip
     解析 target:主键值 = parameters[rule.target_parameter]
     解析 properties:每个 ValueSource 求值(见下)
     按规则类型生成 mutation:
       ModifyObject:
         existing = get_object_state(主键)
         if existing is None:                       # 决策 C: hydrate
           hydrated = ObjectQueryService.load_by_ids(主键)  # 走读路径(含 Doris→Trino 降级)
           if hydrated 为空: raise NotFoundError → 404  (on_missing=raise_not_found)
           else: 写入 object_state(全量快照, version=1); expected_version=1
         else: expected_version = existing.version
         合并属性:hydrated/existing 全量 + rule.properties 覆盖 → UPDATE_OBJECT
       UpsertObject:
         同 Modify 的 hydrate 逻辑;0 行且 on_missing=create → CREATE_OBJECT(SYSTEM_GENERATED 主键)
       CreateObject  → CREATE_OBJECT(主键=SYSTEM_GENERATED 或参数)
       DeleteObject  → 先 hydrate 校验存在→DELETE_OBJECT
       CreateLink/DeleteLink → RELATE/UNRELATE
5. OCC 应用 mutation 到 object_state(现有 upsert_object_state)  [unchanged]
   · expected_version 衔接:hydrate 写入 v1 后,并发 Modify 用读出的 version
     → benchmark 500 并发同对象真正测到 OCC(解决 write_conflict_001)
6. 事务提交(object_state + execution_log + outbox)    [unchanged]
7. 异步 Side Effects(OutboxExecutor):                 [unchanged]
   · write_back effect → WriteBackManager 回写 MySQL 源表(若配了)
   · notification/webhook effect → 各自处理
```

**ValueSource 求值规则**(决策 7 调整后):
- `PARAMETER`:取 `parameters[value]`
- `OBJECT_PROPERTY`:`value` 是 "参数名.属性名",如 `newAircraft.status` ——
  读 ObjectReference 参数引用的对象的属性(通过 hydrate/load 读)。**不允许关系链路径**(如 `a.b.c`)
- `STATIC_VALUE`:字面量 `value`
- `SYSTEM_CONTEXT`:`value` ∈ {CURRENT_USER_ID, CURRENT_TIMESTAMP},从 ActionContext 取
- `SYSTEM_GENERATED`:`value="uuid"` → 生成主键
- `EXPRESSION`:`value` 是 simpleeval 表达式,命名空间 = 所有参数 + 所有 ObjectReference 参数的属性

**关键改动**:
- **hydrate(决策 C)**:Modify/Upsert 时 object_state 缺失从读路径(Doris,含 Trino 降级)读全量补建,
  保证 object_state 全量快照 + read-your-writes 一致。Doris/Trino 都查不到 → 404(write_004/012)。
- **OCC 衔接**:hydrate 写 v1 后,并发用读出的 version 做 expected_version → 真 OCC(write_conflict_001)。
  冲突(409)不自动重试,客户端决定刷新重试。
- **主键不可修改**:见 §3.8 校验时机(定义期 + 执行期双重)。

### 3.4 副表日志:`flight_status_log` 怎么处理

这是 benchmark 的 audit 用例关键。两个候选:

**选项 A —— 建模为 ObjectType + CreateObject 规则**(推荐,对标 Foundry):
- 把 `flight_status_log` 也注册成 ObjectType(`storage_type=MANAGED`,主键 log_id 自生成)
- DelayFlight 的 Ontology Rules 加一条 `CreateObject(target_object_type="flightStatusLog", properties={...})`
- 它写 object_state(同事务),再经 write_back effect 回写 MySQL `flight_status_log` 表
- 优点:完全对标 Foundry(日志即对象);复用 object_state + write_back 链路;read-your-writes 可查
- 代价:要给 flight_status_log 建一个 ObjectType + 配 write_back effect 回写源表

**选项 B —— 裸表 write_back effect(不建 ObjectType)**:
- `effects: [{type:"write_back", config:{table:"flight_status_log", op:"insert", columns:{...}}}]`
- 但 outbox 异步,write_audit_001 紧查会 FAIL(除非加轮询);且不进 object_state,无 read-your-writes
- 优点:不污染本体
- 代价:异步一致性 + 要给 WriteBackManager 加 insert SQL

**倾向 A**:Foundry 就是把日志/审计记录当对象建模。本体的 `flight_status_log` 本来就是业务实体,
注册成 ObjectType 名正言顺,且与主对象 flight 同写 object_state(同事务)。回写 MySQL 源表走
它的 write_back effect(与 §2.4 一致:用户显式配,异步经 Outbox)。benchmark 侧靠轮询等最终一致(决策 2)。

### 3.5 object_state → Doris(必选) + MySQL 源表(可选 write_back)

**v3 纠正 v2 误解**(详见 §2.4):

- **object_state → Doris(必选)**:Action 写 object_state 后,经 ActionSyncService 的 CDC 管道
  异步同步到 Iceberg→Doris。这是 Action 的必选行为,无开关。read-your-writes 在 CDC 完成前
  由 object_state 直读覆盖,CDC 完成后由 Doris 接管。
- **MySQL 源表回写(可选)**:仅当 ActionType 显式配 `write_back` effect 时,OutboxExecutor
  调 WriteBackManager 回写 MySQL 源表。用 ObjectType 的 `physical_mapping`(table_name+列映射)
  生成 upsert/merge SQL。**无 auto_write_back 默认开** —— 回写源表是用户决策。
  · DelayFlight/ReassignAircraft 需配 write_back effect 回写 flight 表(benchmark postcondition 查 MySQL)
  · flightStatusLog ObjectType 也配 write_back effect 回写 flight_status_log 表
  · 回写经 IngestionFilter(gaia_sync_tx 标记)防反馈环

### 3.6 函数式能力的过渡:EXPRESSION 值来源

纯声明式无法做"escalationCount + 1"这类自增(Foundry 需切函数式)。本期用
`ValueSource.source="EXPRESSION"` 过渡:值是 simpleeval 表达式,可引用任意参数 +
hydrate 读出的对象当前值(`OBJECT_PROPERTY`)。表达力覆盖 benchmark 全部场景,不必引入
TypeScript 运行时。复杂级联逻辑留未来函数式迭代。

### 3.7 DelayFlight 完整声明(示例)

```yaml
actionType: delayFlight
displayName: 航班延误处理
parameters:
  - {api_name: flight, object_type_ref: flight, required: true}        # ObjectReference<flight>
  - {api_name: delay_minutes, data_type: INTEGER, required: true}
  - {api_name: reason, data_type: STRING, required: true}
  - {api_name: operator, data_type: STRING, required: true}
rules:                                         # 校验规则(现有,不变)
  - {type: validation, target: delay_minutes, expression: "delay_minutes > 0"}
  - {type: validation, target: delay_minutes, expression: "delay_minutes <= 600"}
ontology_rules:                                # 新增
  - type: ModifyObject
    target_parameter: flight                    # 主键从 flight 参数提取
    properties:
      status:        {source: STATIC_VALUE, value: "Delayed"}
      delayMinutes:  {source: PARAMETER, value: "delay_minutes"}
    on_missing: raise_not_found
  - type: CreateObject
    target_object_type: flightStatusLog
    properties:
      logId:       {source: SYSTEM_GENERATED, value: "uuid"}
      flightId:    {source: PARAMETER, value: "flight"}
      newStatus:   {source: STATIC_VALUE, value: "Delayed"}
      operator:    {source: PARAMETER, value: "operator"}
      reason:      {source: PARAMETER, value: "reason"}
      operateTime: {source: SYSTEM_CONTEXT, value: "CURRENT_TIMESTAMP"}
effects:                                       # write_back 回写源表(用户显式配)
  - type: write_back
    config: {target_object_type: flight, op: upsert}
  - type: write_back
    config: {target_object_type: flightStatusLog, op: insert}
# benchmark 调用:parameters={flight:100, delay_minutes:60, reason:"weather", operator:"bench"}
# (flight 参数值=主键 flight_id=100)
```

### 3.7b ReassignAircraft 声明(体现决策 7)

```yaml
actionType: reassignAircraft
parameters:
  - {api_name: flight, object_type_ref: flight, required: true}
  - {api_name: newAircraft, object_type_ref: aircraft, required: true}   # 新飞机作 ObjectReference
  - {api_name: operator, data_type: STRING, required: true}
rules:
  - {type: validation, target: newAircraft, expression: "newAircraft.status != 'Maintenance'"}
      # 决策 7:读输入参数 newAircraft 引用的对象属性,不违反"不做关系遍历"
ontology_rules:
  - type: ModifyObject
    target_parameter: flight
    properties:
      aircraftId: {source: PARAMETER, value: "newAircraft"}   # 用新飞机主键
    on_missing: raise_not_found
effects:
  - {type: write_back, config: {target_object_type: flight, op: upsert}}
```

### 3.8 校验时机 + 规则顺序约束 + 规模限制

**校验时机(对标 Foundry 保存发布前强制校验)**:
- **定义期**(ActionTypeCreate/Update 时):主键不可出现在 Modify 的 properties;
  规则引用的参数/属性存在;属性类型可编辑(本期不校验类型限制,见下)。
- **执行期**(execute_action 时):再次校验主键不可改(防绕过);on_missing;OCC。

**规则顺序约束(对标 Foundry 强制约束)**:
- Ontology Rules 按声明顺序执行,同事务
- **不允许"先删后建/先改后建"同一对象** —— 同一 Action 内对同一对象只能有一个 op
- **新建对象不可被同 Action 内后续规则引用修改/删除**(尚未持久化,无有效主键)
- 违反 → 定义期 `ValidationError`(422)

**规模限制(本期采纳 Foundry 上限)**:
- 单次提交最大编辑对象数:10000
- 多值参数(ObjectReference multiple)数组长度:1000
- 本期**不实现批量执行运行时**(is_object_set 字段保留但执行层不展开批量),
  仅留 schema 入口,等 Batch Action(P2)再做。benchmark 不涉及批量。

**属性类型限制**:本期**不校验** Foundry 的不可编辑类型(浮点/字节/时间序列/Decimal)。
  Gaia 的数据类型集不同,待真实需求触发再定黑名单。

### 3.9 write_back effect config schema

`write_back` effect 的 config 约定(OutboxExecutor 消费):

```python
class WriteBackEffectConfig(BaseModel):
    """回写源表的 effect 配置。用 ObjectType.physical_mapping 生成 SQL。"""
    target_object_type: str          # 回写哪个 ObjectType 对应的源表
    op: Literal["upsert", "insert"]  # upsert=ModifyObject 回写;insert=CreateObject 回写
    # 不需手写 table/columns —— 从 ObjectType 的 physical_mapping(table_name+列映射)自动推导
    # WriteBackManager.build_upsert_sql / 新增 build_insert_sql 生成参数化 SQL
```

回写 payload = 该 mutation 的最终 properties(含主键),经 gaia_sync_tx 标记防反馈环。

### 3.10 hydrate 的降级路径

决策 C 的 hydrate 走**读路径**,自动继承现有降级机制:
- Doris 可用 → `DorisIndexStore.load_by_ids`(主路径,全量直出)
- Doris 不可用 → `DorisUnavailableError` → Trino 扫 Iceberg(项目已有降级)
- 都查不到 → 对象不存在 → `NotFoundError` → 404

即 hydrate 不需新写降级逻辑,复用 ObjectQueryService 的 load_objects 路径即可。

---

## 四、前端设计(一体)

对标 Foundry "Object View 挂载 Action 按钮 + Workshop 表格选中行执行 + 执行表单"。

### 4.1 现状

- `ExecuteActionDialog.tsx` 已存在:渲染参数表单、调用 `/actions/execute`、展示结果
- `ActionsOverview.tsx`:Action 列表总览
- `ObjectDetailPanel.tsx`:对象详情,已有"动作"区块和"执行"按钮
- 当前参数渲染:纯类型驱动(text/checkbox/select/date),**不识别 ObjectReference**

### 4.2 改动点

1. **ObjectReference 参数渲染**:`ExecuteActionDialog` 中,`object_type_ref` 非 None 的参数 → 渲染为**对象选择器**(下拉/搜索,可配 filter),而非裸 text 输入。从对象详情页打开时自动预填当前对象主键并隐藏该参数。
2. **Ontology Rules 可视化编辑**:`ActionsOverview` 或新增 `ActionTypeEditor` —— 可视化配置 Ontology Rules(选规则类型→选目标对象/参数→映射属性→选值来源)。对标 Foundry Ontology Manager 的 Rules 标签。本期可先做**只读展示 + JSON 编辑**(低代码编辑器作为后续迭代),保证定义能被前端消费和展示。
3. **执行入口**:对象详情页"动作"区块已有"执行"按钮 → 打开 `ExecuteActionDialog`,自动把当前对象作为 ObjectReference 参数传入。无需大改。
4. **Side Effects 展示**:Action 详情展示已配置的 Notification/Webhook(只读),让用户知晓副作用。
5. **冲突反馈**:范式 B 已让 OCC 冲突返回 409;前端 `ExecuteActionDialog` 需识别 409 → 提示"对象已被他人修改,请刷新后重试"(现前端按 `status:"conflict"` 解析,需适配 409 + error body)。

### 4.3 前端类型同步

`src/web-ui/src/types/` 增加 `OntologyRule`/`ValueSource` 类型;`ActionType` 增加 `ontology_rules` 字段;`ActionTypeParameter` 的 `object_type_ref` 已在类型里(确认)。

---

## 五、影响面与改动清单

| 层 | 文件 | 改动 |
|----|------|------|
| **Schema** | `core/schemas/action.py` | 新增 `ValueSource`/`OntologyRule`;`ActionTypeCreate`/ORM/ActionType 加 `ontology_rules`;`ActionEffectConfig` 加 `notification` 类型 + `trigger`/`condition` |
| **Schema** | `core/schemas/ontology.py` | (确认 `physical_mapping` 可用,无需改) |
| **Service** | `services/action_service.py` | 重写 `_build_mutations` → `_build_mutations_from_rules`;**hydrate(决策 C)**:object_state 缺失从 Doris 读全量补建;OCC expected_version 衔接;主键不可改校验;on_missing→404;**注入 ObjectQueryService 依赖** |
| **Service** | `services/object_query_service.py` | (复用现有 load_by_ids,供 hydrate 调用,可能提一个按主键点查的便捷方法) |
| **Config** | `config/container.py` | ActionService 装配增加 `object_query_service` 依赖注入 |
| **Service** | `services/outbox_executor.py` | notification effect 处理(本期可先记日志,真实通知后续);write_back effect 消费 WriteBackEffectConfig |
| **Metadata** | `layers/metadata/postgres_meta_store.py` | (可能加按主键+类型查 object_state 的便捷方法,现有 get_object_state 已够) |
| **Setup** | `benchmark/scripts/01_setup_ontology.py` | `to_action_create` 透传 `ontology_rules`/`object_type_ref` |
| **Data** | `benchmark/data/ontology/airline-ontology.json` | DelayFlight/ReassignAircraft 补 ontology_rules + write_back effects;新增 flightStatusLog ObjectType |
| **Benchmark** | `benchmark/scripts/05_run_write_benchmark.py` | postcondition 校验前加**等待最终一致轮询**(查到预期值或超时,决策 2);ReassignAircraft 参数改 ObjectReference |
| **Service** | `services/write_back_manager.py` | 复用 upsert/merge;**新增 build_insert_sql**(CreateObject 回写) |
| **Schema** | `core/schemas/action.py` | 新增 `WriteBackEffectConfig`(§3.9) |
| **前端** | `web-ui/src/types/` | 新增 OntologyRule/ValueSource 类型 |
| **前端** | `web-ui/src/components/ExecuteActionDialog.tsx` | ObjectReference 参数渲染 + 409 冲突反馈 |
| **前端** | `web-ui/src/pages/ActionsOverview.tsx`(或新 ActionTypeEditor) | Ontology Rules 只读展示 + JSON 编辑 |
| **测试** | `tests/unit/services/test_action_service.py` | 新增 rules 解析、hydrate、OCC 衔接、on_missing、主键校验用例 |
| **测试** | `tests/integration/test_action_routes.py` | 端到端 Modify/Upsert/Create 副表 |
| **测试** | `src/web-ui/.../__tests__/ExecuteActionDialog.test.tsx` | ObjectReference 渲染 + 409 |
| **文档** | 本 ADR + `implementation-status.md` | 记录新能力 |

**不改动**:范式 B 异常映射(已稳定)、rule engine 的 derivation 语义、harness client 契约。

---

## 六、已确认决策(v3)

1. ✅ **副表日志走选项 A**:`flight_status_log` 建为 ObjectType + CreateObject 规则,同事务写 object_state,经 write_back effect 回写 MySQL 源表。对标 Foundry"日志即对象"。
2. ✅ **写回链路**:object_state→Doris 必选(无开关);MySQL 源表回写走显式 write_back effect(用户配置),**无 auto_write_back 默认开**。DelayFlight/ReassignAircraft/flightStatusLog 各配 write_back effect。
3. ✅ **object_state 缺失 → 决策 C:hydrate**:ModifyObject 对 MANAGED 对象,object_state 缺失时从 Doris `load_by_ids` 读全量当前值补建(v1),再 apply Modify。保证 object_state 全量快照语义 + read-your-writes 一致。Doris 里也不存在 → `NotFoundError` → 404(write_004/012)。
4. ✅ **OCC 衔接**:hydrate 写入 v1 后,并发 Modify 用读出的 version 做 expected_version,500 并发真 OCC(write_conflict_001)。OCC 冲突(409)不自动重试,由客户端决定刷新重试;benchmark 冲突用例的成功率指"含重试/最终成功",客户端需实现重试逻辑。
5. ✅ **函数式 Action 不做**:本期配置式 + EXPRESSION 值来源过渡,不引入 TypeScript 运行时。
6. ✅ **前端编辑器**:本期 Ontology Rules 只读展示 + JSON 编辑,不做可视化拖拽编辑器。
7. ✅ **对象属性读取边界(决策 1 调整)**:Action **可读所有输入参数(ObjectReference)引用的对象的属性**(如 ReassignAircraft 读 newAircraft.status);但**不支持沿关系链从一个对象跳到另一个未作为参数传入的对象**(如 `$工单.设备.设备编号`,需 LinkTraversalService)。区别:用户直接传入的对象是 Action 的直接输入,读其属性不是关系遍历。

## 七、验收标准(对齐 write benchmark)

- [ ] write_001/010:ModifyObject → flight 表字段变更,postcondition 通过
- [ ] write_004/012:不存在对象 → 404
- [ ] write_011:ReassignAircraft 参数 newAircraft(ObjectReference),校验 newAircraft.status≠Maintenance → 422
- [ ] write_audit_001/002:CreateObject(flightStatusLog)同事务 → 日志正确
- [ ] write_conflict_001:500 并发同对象 OCC,expected_version 衔接 → 成功率 ≥99%(含客户端重试)
- [ ] write_005 幂等、write_002/003/013 校验 422 不回归
- [ ] 现有 69 action 单测 + 12 集成测试全过(适配 hydrate/OCC 改动)
- [ ] 前端 ObjectReference 参数渲染 + 409 冲突反馈
- [ ] 范式 B 422/409 不回归

预期最终 write benchmark:14/14 PASS(含 2 冲突)。

## 八、待你评审

§六 决策 1-7 均已确认。按 TDD:阶段①先 schema → service 单测(含 hydrate/OCC)→ 集成。
