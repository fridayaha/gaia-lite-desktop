# 交接文档：apiName 自动推导完整接入

> 承接提交 `83da3d4`（ADR Action Mutation Mapping + backing 改名 + 读路径对齐）
> 本文档是下次对话的输入。**先读"§三 改名遗漏"**——有 3 处改名残留会导致功能直接损坏，必须先修。

---

## 一、已完成（提交 83da3d4）

### 1. ADR Action Mutation Mapping（声明式 Ontology Rules）
- **Schema**（`core/schemas/action.py`, `ontology.py`）：`ValueSource`（6类）/ `OntologyRule`（6类）/ `WriteBackEffectConfig`；`ActionTypeCreate`/`ActionType` 加 `ontology_rules`；`ActionEffectConfig` 加 `notification` + `trigger`/`condition`
- **Service**（`services/action_service.py`）：`_build_mutations_from_rules` + hydrate（决策C）+ OCC 衔接 + on_missing→404 + 主键不可改（查 ObjectType.primary_key 精确匹配）+ ObjectReference hydrate（决策7）；write_back effect 从 `backing_mapping` 推导 table/primary_key
- **Outbox/WriteBack**（`services/outbox_executor.py`, `write_back_manager.py`）：effect_type 大小写统一 + MySQL `ON DUPLICATE KEY UPDATE` + aiomysql `autocommit=True` + `build_insert_sql` + changes 从 `payload.changes` 取
- **Benchmark**：`airline-ontology.json` 补 ontology_rules + write_back + flightStatusLog OT；`01_setup_ontology.py` 透传 ontology_rules/object_type_ref；`05_run_write_benchmark.py` postcondition 轮询 + conflict 重试
- **前端**：`types/index.ts` 加 OntologyRule/ValueSource/WriteBackEffectConfig；`ActionParameterField.tsx` object-ref 标签；`formatError.ts` OCC 文案；`vite.config.ts` /actions proxy bypass

### 2. 数据层 physical→backing 改名（对标 Palantir 三层语义分层）
全链路改名（DB→ORM→schema→service→route→前端→测试）：
- DB `properties` 表：`physical_catalog/schema/table/column/dataset_api_name` → `backing_*`（已清库重建）
- ORM `PropertyDefModel`：5 字段改名
- pydantic：`PhysicalColumnRef` → `BackingColumnRef`，`physical_mapping` → `backing_mapping`，字段名 `catalog_name/schema_name/table_name/column_name` → `backing_catalog/backing_schema/backing_table/backing_column`

### 3. 读路径对齐（业务 API 只认 apiName）
- `ObjectQueryService.load_objects` 出口映射 `backing_column`→`api_name`（`_map_backing_to_api`）
- `hydrate_by_pk` 主路径（Doris load_by_ids）+ 降级（Trino 联邦源表 `_hydrate_via_source_table`）统一返回 apiName + datetime/Decimal 规范化

### 4. apiName 推导器基础（`core/naming.py`）
- `derive_api_name(display_name, backing_column, fallback_prefix, existing_count, pascal)` —— **已导出到 `__all__`**
- **pattern 校验分层**（靠字符集校验，不靠分词有无）：
  1. displayName 满足 `SOURCE_PATTERN` → 从 displayName 推导
  2. backingColumn 满足 `SOURCE_PATTERN` → 从 backingColumn 推导
  3. 兜底 `prefixN`
- 支持 camelCase（`pascal=False`，属性首词小写）/ PascalCase（`pascal=True`，对象首字母大写）
- pattern 常量已定义（**模块级，未导出到 `__all__`，接入时按需 import**）：
  - `PROPERTY_API_NAME_PATTERN = r"^[a-z][a-zA-Z0-9]{0,99}$"`
  - `OBJECT_TYPE_API_NAME_PATTERN = r"^[A-Z][a-zA-Z0-9]{0,99}$"`
  - `SOURCE_PATTERN = r"^[A-Za-z][A-Za-z0-9 _-]{0,99}$"`
- 推导器验证通过：`derive_api_name('航班编号', backing_column='flight_id')` → `flightId`；`derive_api_name('航班', backing_column='flight', pascal=True)` → `Flight`

### 5. SeaTunnel 集群修复（`config/seatunnel/hazelcast-master.yaml`）
- 根因：member-list 用 `localhost:5802` 找 worker，跨容器不通，7 sync job 全 FAILED，Doris 0 行
- 修复：→ `seatunnel-worker:5802`。集群组建成功，sync job FINISHED，Iceberg 10000 行，`sync_now` 同步到 Doris

### 6. .gitignore 修复
- `data/` → `/data/`（原规则误忽略 `benchmark/data/`，导致 ontology JSON/testcase YAML 从未入库）
- `benchmark/data/*` 源文件已纳入版本控制

### 7. 验证状态
- ruff + mypy 通过；747 单测 passed（1 预存 iceberg 失败无关）
- write benchmark 14/14 PASS
- 前端 Actions 页面 CDP 测试通过

---

## 二、🔥 改名遗漏（必须先修，否则功能损坏）

提交 83da3d4 的后端 schema 已改成 `backing_mapping`/`backing_catalog` 等，但有 **3 处非后端代码没跟着改**，会导致 benchmark setup 失败：

### 遗漏 1：`benchmark/scripts/01_setup_ontology.py`（第 55-60 行）
```python
# 当前（错误）:
prop["physical_mapping"] = {
    "catalog_name": "airline_mysql",
    "schema_name": "airline_benchmark",
    "table_name": p["physical_table"],
    "column_name": p["physical_column"],
}
# 应改为:
prop["backing_mapping"] = {
    "backing_catalog": "airline_mysql",
    "backing_schema": "airline_benchmark",
    "backing_table": p["physical_table"],   # ← 见遗漏 2，JSON 字段也要改
    "backing_column": p["physical_column"],
}
```

### 遗漏 2：`benchmark/data/ontology/airline-ontology.json`（122 处）
JSON 里属性仍用 `physical_table`/`physical_column` 字段名（122 处）。setup 脚本读这些字段构造 backing_mapping。两个选择：
- (a) JSON 字段改名为 `backing_table`/`backing_column`（与后端术语一致，推荐）
- (b) JSON 保留 `physical_table`/`physical_column`，setup 脚本读时映射（JSON 是 benchmark 私有格式，可不动）

### 遗漏 3：`benchmark/scripts/01_setup_ontology.py` 的 `_normalize_action_api_name`（第 97 行）
```python
def _normalize_action_api_name(name: str) -> str:
    # 当前: PascalCase → camelCase（DelayFlight → delayFlight）
    # 新规则: Action apiName 是 camelCase（小写开头），逻辑不变，但 pattern 要更新
```
Action apiName 仍是 camelCase（小写开头），这个函数逻辑正确，但 docstring 里的 pattern `^[a-z][a-zA-Z0-9_]*$` 要改成 `^[a-z][a-zA-Z0-9]{0,99}$`。

---

## 三、剩余工作：apiName 自动推导完整接入

推导器和 pattern 常量已就绪（`core/naming.py`），**前端接入已完成**（2026-06-28），benchmark 数据调整待做。

### ✅ 已完成（前端接入）
- 新增 `src/web-ui/src/lib/deriveApiName.ts`：前端镜像后端 `derive_api_name`（PascalCase/camelCase + 优先级 + 兑底）
- `src/api/ai.ts` 新增 `suggestObjectTypeApiName` / `suggestPropertyApiName`（本地推导失败时调 `/ai/generate`）
- `CreateObjectWizard.tsx` 重写：
  - 对象元数据区：displayName 可编辑 + apiName **可编辑预览**（实时推导，用户改过则停止覆盖）+ ✨AI 按钮（仅本地推导失败时启用）+ description autosize
  - 属性区：列表 4 列（显示名称/类型/源列/键徽章）+ 行内展开详情（含 description autosize + 源列同步 + 完整字段）
  - 主键/标题：独立 card，下拉用 displayName，提交转 `is_primary_key`/`is_title_property` 标志
  - Review 步骤修复 snake_case 错误
- `OntologyWorkspace.tsx`：`handleWizardComplete` payload 加属性 description、用标志取代 api_name 字符串引用；编辑回填适配
- `PropertyDraft` 类型：删 api_name（后端推导），加 description/is_title_property/nullable
- 后端 `ontology_service.py`：`_DerivedProp` 加 description，`PropertyDefModel` 构造传 description（batch create + update）
- 后端 `PropertyInput` schema 加 description 字段
- 修复预存 `OntologySidebar.test.tsx` 缺失 props

### ⏳ 待做（benchmark 数据调整）

> 2026-06-28 更新：JSON 对象名已是 PascalCase、Action 名已是 camelCase、属性 description 已补齐并接入 setup 脚本，setup 全流程跑通。剩下的是存量 DB 重建（可选）。

#### ✅ benchmark JSON 已调整
- ✅ 对象类型 apiName 已是 PascalCase（`Aircraft`/`Flight`/`FlightStatusLog` 等 9 个）
- ✅ Action apiName 已是 camelCase（`delayFlight`/`reassignAircraft`）
- ✅ 属性 displayName 纯中文（"航班编号"等），走 backingColumn 推导 camelCase apiName
- ✅ 属性/对象/link 补齐 description 字段
- ✅ `01_setup_ontology.py`：`to_batch_create` 传属性 description

#### ⏳ 存量 DB 重建（可选）
- 旧 Airline ontology 的属性 description 为空（旧 setup 未传）。若要补齐，删除 Airline 重建即可：`DELETE /ontologies/Airline` → `python -m benchmark.scripts.01_setup_ontology`

### 决策规则（已和用户确认）

| 字段 | pattern | 说明 |
|------|---------|------|
| 属性 apiName | `^[a-z][a-zA-Z0-9]{0,99}$` | camelCase，首词小写，纯 ASCII 无下划线 |
| 对象 apiName | `^[A-Z][a-zA-Z0-9]{0,99}$` | PascalCase，首字母大写，**对外统一大写开头** |
| displayName | `^[A-Za-z][A-Za-z0-9 _-]{0,99}$` | ASCII 才参与推导（中文不合规→走 backingColumn） |
| backingColumn | `^[A-Za-z][A-Za-z0-9 _-]{0,99}$` | ASCII 才参与推导 |

**推导优先级**：displayName（pattern 合规）> backingColumn（pattern 合规）> 兜底 `prefixN`

**物理资源命名**：对象 apiName 内部转全小写 snake_case（Doris 表 `idx_airline__flight`、SeaTunnel pipeline）。`naming.doris_index_table` 已用小写，确认传入的对象 apiName 会被小写化。

### benchmark 当前状态（需改）

```
ontology api_name: airline          (camelCase，Ontology 本身风格待定)
object_types: aircraft/crew/flight/maintenanceTask/crewDetail/...
              ↑ 全是 camelCase（小写开头），要改 PascalCase: Aircraft/Crew/Flight/...
action_types: DelayFlight/ReassignAircraft
              ↑ 已是 PascalCase，但 Action 应是 camelCase: delayFlight/reassignAircraft
flight props: flightId/flightNo/...  (camelCase ✓，但 displayName 是"航班ID"含英文→会推导成 id)
flight displayName: 航班ID/航班号/出发机场/...  (含英文"ID"→推导出 id，要改纯中文"航班编号")
```

### 待办清单

#### 1. ✅ 改名遗漏（已不存在）
- ✅ `01_setup_ontology.py`：已用 `backing_mapping`/`backing_catalog`（v6 改名完成）
- ✅ `airline-ontology.json`：已用 `backing_table`/`backing_column` 字段名
- ✅ `_normalize_action_api_name`：已不在 setup 脚本中（handoff 旧版描述过时）

#### 2. ✅ 入参 schema 去 api_name（属性/Link 推导；对象/Ontology/Action/参数 caller-supplied）
属性/Link 的 `*Create`/`*Input` 不传 api_name，service 层调 `derive_api_name`：
- ✅ `PropertyDefCreate` / `PropertyInput`：不传 api_name，后端推导 camelCase
- ✅ `LinkInput`：不传 api_name，后端推导
- ✅ `SharedPropertyCreate`：service `add_shared_property` 已调 `_derive_unique_api_name`（接收 display_name，不接收 api_name）
- ✅ `ObjectTypeCreate`/`ObjectTypeBatchCreate`：**对象级 api_name 保留**（PascalCase，caller-supplied，前端推导预览）
- ✅ `OntologyCreate`：**Ontology apiName = PascalCase**（与 ObjectType 同风格，对外统一大写开头）。新增 `ONTOLOGY_API_NAME_PATTERN` 常量（= `OBJECT_TYPE_API_NAME_PATTERN`）
- ✅ `ActionTypeCreate` / `ActionTypeParameter`：**caller-supplied**（camelCase，前端 `suggestActionApiName` AI 辅助推导供用户确认）。参数名是契约的一部分，不推导

**ObjectTypeCreate 的 `primary_key`/`title_property` 字段**：已用属性级 `is_primary_key`/`is_title_property` 标志取代（后端 `_resolve_pk_title_from_properties` 从标志解析）。`primary_key`/`title_property` 字段可选，作为 api_name/display_name 引用匹配（向后兼容）。前端已改为只传标志。

#### 3. ✅ pattern 收紧 + 导出
- 属性/Link/Action/参数 apiName：`^[a-z][a-zA-Z0-9]{0,99}$`（`PROPERTY_API_NAME_PATTERN`）
- 对象类型 / Ontology apiName：`^[A-Z][a-zA-Z0-9]{0,99}$`（`OBJECT_TYPE_API_NAME_PATTERN` / `ONTOLOGY_API_NAME_PATTERN`）
- ✅ pattern 常量已导出到 `naming.__all__`（含 `SOURCE_PATTERN`）
- 存量数据已清空（DB 重建过），无迁移负担

#### 4. ✅ 对象 apiName 改 PascalCase（已完成）
- ✅ `airline-ontology.json`：对象类型 apiName 已是 PascalCase（`Flight`/`Aircraft`/`FlightStatusLog` 等 9 个）
- ✅ 测试用例 YAML：对象引用已是 PascalCase
- ✅ API 路由 path：已支持 PascalCase 对象名（`/ontologies/{o}/object-types/{type}`）
- ✅ Doris 表名 `idx_airline__flight`：`doris_index_table(ont, type)` 内部小写化，对象 apiName 传入后转小写
- ✅ `affected_object_type_api_name`（ActionTypeCreate）：引用 PascalCase 对象名

#### 5. ✅ service 层推导接入（属性/Link/SharedProperty）
- ✅ `OntologyService.define_object_type_batch` / `update_object_type_batch`：属性/Link 调 `_derive_unique_api_name` 推导
- ✅ 属性 description 落库：`_DerivedProp` 加 description，`PropertyDefModel` 构造传 description
- ✅ `add_shared_property`：已调 `_derive_unique_api_name`（camelCase）
- ℹ️ `ActionService`：**不推导**（Action apiName 是 caller-supplied + AI 辅助，与对象类型同模式；参数名是契约不推导）

#### 6. ✅ benchmark 数据调整（已完成）
- ✅ `airline-ontology.json`：对象类型 apiName 已是 PascalCase、Action apiName 已是 camelCase、属性 displayName 纯中文、属性/对象/link 补齐 description
- ✅ `01_setup_ontology.py`：`to_batch_create` 传属性 description；属性/Link 不传 api_name（后端推导）；对象级 api_name 保留（PascalCase caller-supplied）
- ✅ setup 全流程跑通（对象 409 跳过 + links/actions 201 创建，payload schema 兼容）
- ⏳ 存量 DB 重建（可选）：旧 Airline 属性 description 为空，删除重建即可补齐

#### 7. ✅ 前端对齐（已完成）
- ✅ `types/wizard.ts`：`PropertyDraft` 删 api_name，加 description/is_title_property/nullable；对象 apiName 保留（PascalCase 可编辑预览）
- ✅ `CreateObjectWizard`：对象 apiName 改为**可编辑预览**（非手填输入框），实时从 displayName 推导，用户改过停止覆盖；属性 apiName 只读预览（后端推导）
- ✅ `lib/deriveApiName.ts`：前端镜像后端推导逻辑 + pattern 常量
- ✅ `api/ai.ts`：`suggestObjectTypeApiName` / `suggestPropertyApiName`（本地推导失败时启用）
- ⏳ 路由 path 用 PascalCase 对象名：待确认（当前 API 路由已支持 PascalCase 对象名）

#### 8. ✅ 文档同步（已完成）
- ✅ `docs/web-ui/ontology-manager.md` §2.4 属性表设计 + §7.2 API name 生成规则已更新
- ✅ 本文档（handoff）待办清单已勾掉完成项
- ⏳ 文档例子的 snake_case api_name（`order_no`、`order_item`）改成 camelCase/PascalCase：低优先级，逐文档排查

---

## 四、待用户确认的开放问题（已全部确认）

1. ✅ **Ontology 本身 apiName 风格**：**PascalCase**（`Airline`），与 ObjectType 同风格，对外统一大写开头。新增 `ONTOLOGY_API_NAME_PATTERN` 常量
2. ✅ **ObjectTypeCreate 的 primary_key/title_property 如何引用**：改用属性级 `is_primary_key`/`is_title_property` 标志，后端 `_resolve_pk_title_from_properties` 解析；`primary_key`/`title_property` 字段可选作向后兼容
3. ✅ **前端路由 path 对象名**：PascalCase 直接用，API 路由已支持
4. ✅ **benchmark JSON 字段名**：已用 `backing_table`/`backing_column`（v6 改名完成）

---

## 五、关键参考

- **`docs/reference-palantir-ontology.md`**（本次新增）：Palantir Foundry Ontology 设计参考,沉淀了对话中讨论的三层语义分层、apiName 自动生成规则、无数据源先建模、三类字段在四大 API 体系的位置、标准化落地模板、对前端设计的具体指导。**前端设计和交互以此为依据**。
- ADR：`docs/architecture/adr-action-mutation-mapping.md`
- 推导器：`src/ontology/core/naming.py`（`derive_api_name` + pattern 常量）
- 实现状态：`docs/architecture/implementation-status.md`
- `docs/web-ui/ontology-manager.md`：前端设计（第 113/257 行旧设计需按参考文档更新）
- `docs/reference.md`：Palantir 本体层向 Agent 层交付工具的技术原理

## 六、环境状态（下次对话可复用）
- Docker 服务全在运行（postgres/trino/gravitino/rustfs/kafka/doris/seatunnel）
- SeaTunnel 集群已修复（master+worker 双节点，sync job 能 FINISHED）
- Doris 有 flight 10000 行（`sync_now` 已同步）
- 后端启动方式：`.venv/bin/python scripts/start_backend_detached.py`（前台运行，内部 subprocess detach；bash 工具的 `&`/`nohup` 不可靠，必须用这个脚本或 Python subprocess + start_new_session）
- DB 已清库重建（backing_* 列），ontology 需重新 setup（但 setup 脚本有改名遗漏，先修 §二）
- `sync_now` 触发方式：`.venv/bin/python -c "import asyncio; from ontology.config.container import Container; ..."`（见对话记录）
