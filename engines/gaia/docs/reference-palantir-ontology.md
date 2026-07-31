# Palantir Foundry Ontology 设计参考

> 本文档沉淀对话中讨论的 Palantir Foundry Ontology 设计规范，作为 Gaia 前端设计、交互、建模契约对齐的依据。
> 来源：用户提供的 Palantir 官方实践 + Gaia 适配决策。
> 关联：`docs/web-ui/ontology-manager.md`（前端设计）、`docs/architecture/adr-action-mutation-mapping.md`、`docs/handoff-apiname-derivation.md`。

---

## 一、三层语义分层（核心架构约束）

Palantir Ontology 三个名字严格分层、用途隔离，不可交叉复用生成规则。

### 1. backingColumn（绑定数据集列）= 存储层映射
- **职责**：告诉本体去数据集哪一列取数据。仅此一件。
- **来源五花八门**：MySQL 大写、Oracle 混合大小写、多数据源同业务字段不同列名、上游脏命名。
- **平台不能把不稳定的存储字段当作稳定程序标识符**。
- 仅元数据/建模接口存在，**业务查询完全不可见**。

### 2. displayName = 业务语义层（人类可读标签）
- 面向业务分析师、看板使用者，承载业务含义，**是建模时最先定义的业务名词**。
- 支持中文。仅元数据接口可见，**实例读写时不出现**。

### 3. apiName = 程序层唯一标识符
- OQL/OSDK/TypeScript/REST 永久稳定变量名。
- **强制 camelCase**（Palantir 原生）。
- 不能随底层存储变动而变更。

### 为什么不能从 backingColumn 反向生成 apiName
- 底层数据集重构、列改名 → 全量代码/OQL/前端 Workshop 全部失效；
- 同一业务属性切换多数据源（A 库 `user_id`、B 库 `usr_id`）→ 生成两套不同 apiName，业务代码分裂；
- 上游脏命名（全大写、带特殊符号、拼音乱码）直接污染对外 API 规范。

### Gaia 适配决策
- **三层字段已具备**：`api_name`（apiName）、`display_name`（displayName）、`backing_column`（backingColumn）。
- **改名完成**（提交 83da3d4）：`physical_*` → `backing_*`，对齐 Palantir 术语。
- **读路径对齐**：`load_objects` 等业务 API 出口返回 apiName，backingColumn 对上层透明。
- **偏离点**（Gaia 决策）：displayName 不合规（中文）时回退到 backingColumn 推导。Palantir 原生会兜底 `property0`，Gaia 为对 benchmark 友好加了 backingColumn 回退。

---

## 二、apiName 自动生成规则

### Palantir 官方硬规则
> All new object types and properties are automatically assigned API names that are inferred from their display names.

- 新建对象/属性的 API 名称，**仅从 displayName 推导生成**，无其他自动来源。
- backingColumn 字段没有任何分词、转驼峰、自动生成 apiName 的内置逻辑。
- **原生 UI 没有开关、配置项可以切换"从 backingColumn 生成 apiName"**。

### apiName 风格（Palantir 原生 vs Gaia 决策）
| 实体 | Palantir 原生 | Gaia 决策 |
|------|--------------|----------|
| 属性 apiName | camelCase `^[a-z][a-zA-Z0-9]*$` | 同（`^[a-z][a-zA-Z0-9]{0,99}$`） |
| 对象类型 apiName | camelCase | **PascalCase** `^[A-Z][a-zA-Z0-9]{0,99}$`（对外统一大写开头） |
| Link/Action/参数 apiName | camelCase | 同 |
| displayName | 任意（含中文） | `^[A-Za-z][A-Za-z0-9 _-]{0,99}$` 才参与推导 |
| backingColumn | snake_case | `^[A-Za-z][A-Za-z0-9 _-]{0,99}$` 才参与推导 |

> **注意**：Gaia 对象类型 apiName 用 PascalCase 是偏离 Palantir 的决策（用户拍板），物理资源命名（Doris 表 `idx_airline__flight`、SeaTunnel pipeline）内部转全小写 snake_case。

### Gaia 推导优先级（`core/naming.py` `derive_api_name`）
1. displayName 满足 `SOURCE_PATTERN`（ASCII 字母开头）→ 从 displayName 推导
2. backingColumn 满足 `SOURCE_PATTERN` → 从 backingColumn 推导
3. 兜底 `prefixN`（property0/objectType0/actionType0/linkType0）

**用 pattern 校验代替"分词有无"判断**：中文 displayName 首字符非 ASCII 字母，不满足 `SOURCE_PATTERN`，自动回退到 backingColumn。

### 关键边界规则
1. **displayName 修改不会同步更新已保存 apiName**：属性一旦保存，apiName 永久固化；后续改 displayName 不影响 apiName，避免上层代码批量断裂。
2. **绝对无法绕过的校验**：apiName 只能是小写开头（Gaia：对象大写开头）camelCase，不能是 snake/中文/大写开头，无论自动生成还是手动填写都强制校验。
3. **apiName 一旦生成即永久固化**。

### 偷懒技巧（Palantir UI 新建临时过渡，Gaia 可参考）
1. 新建时先临时填英文 displayName `User ID` → 自动生成 `userId`
2. 保存前修改 displayName 为中文 `用户编号`
3. apiName 不会变回 `property0`，永久保留 `userId`

---

## 三、无数据源先建模（Object Storage V2）

### 核心结论
**不能完全不绑定任何 Dataset 就创建 Object Type**，但分两种兼容方案实现"先建模、后对接真实业务数据"（仅支持 Object Storage V2，V1 无此能力）：
1. 新建对象时选 `Continue without datasource` → 平台自动生成**空结构占位数据集**（必须有底层 dataset 载体，只是无业务数据）；
2. 已建好本体后，新增**仅编辑属性（Edit-only Property）**，这类属性暂时不需要绑定 dataset 列，后期再映射。

### 占位空数据集两种生成方式
1. UI 向导一键生成（选 `Continue without datasource` 自动创建）；
2. 代码预制空 schema 数据集（Transform 写空表定义固定字段）。

### 属性分层：两种"暂时不绑定 dataset 列"的属性
1. **标准映射属性（普通 Property）**：对象必须有 backing dataset，每个标准属性最终必须绑定该数据集的某一列；建模初期可先建好属性（定义 displayName、apiName、类型、中文展示），**先不配置 Column Mapping**，等数据集就绪后再下拉选择 snake 列完成绑定。
2. **Edit-only Property（仅编辑属性，V2 专属）**：创建时无需映射 backing dataset 列，数据独立存储在本体底层占位数据集；用于先建模下游 ETL 未产出对应字段、纯前端录入、Action 回写的临时字段；后期关闭 `Edit only property` 开关，选择对应列完成映射。

### 关键限制
- **不存在完全脱离 Dataset 的 Object Type**：本体本身不独立存储数据，权限、实例加载、主键校验全部依赖 backing dataset，哪怕是空占位表也必须绑定一张数据集。
- **Link Type 不受数据集限制**：可提前定义实体间关联，不需要等两边数据集就绪，仅要求两端 Object Type 存在。

### 业务先行建模模式（推荐）
1. 新建对象 → `Continue without datasource` 创建空占位数据集（预留 snake 字段）；
2. 完整定义所有属性（中文 displayName、规范 camel apiName）、主键、关联、Workshop 页面；
3. 数仓并行开发 ETL，输出同 schema snake 数据集；
4. 替换 backing dataset，批量映射 backingColumn；
5. 全流程 apiName 不改动，前端、OQL、OSDK 无需修改。

### 对 Gaia 的指导
- Gaia 当前 ObjectType 必须有 backing_mapping（MANAGED 走 Iceberg，VIRTUAL 走外部表联邦）。
- "先建模后对接"场景：Gaia 可支持 backing_mapping 留空的属性（建模期不绑定列），后续补映射。对应 `OntologyService.link_dataset` 的 column_mappings 可分批补。
- Edit-only Property：Gaia 暂未实现，作为未来迭代（对标 Palantir V2）。

---

## 四、三类字段在四大 API 体系的位置

### 三者定义边界（API 层面隔离）
| 字段 | 元数据 API/OAC 可见 | 实例读写 API 可见 | SDK 实例取值 | 作用层级 |
|------|-------------------|----------------|-------------|----------|
| apiName | ✅ 顶层核心 key | ✅ 唯一键 | ✅ 点访问 | 业务编程层（OQL/接口/代码） |
| displayName | ✅ 属性元数据 | ❌ 不出现 | ❌ 实例无此字段 | UI 展示层（中文业务名） |
| backingColumn | ✅ mapping 子节点 | ❌ 完全隐藏 | ❌ 实例无此字段 | 底层存储映射层（Dataset snake 列） |

### 1. Ontology 元数据 REST API（`GET /objectTypes/{apiName}`）
返回 JSON 中 `properties` 节点完整包含三者：
```json
{
  "apiName": "Aircraft",
  "displayName": "飞机",
  "properties": {
    "aircraftStatus": {
      "apiName": "aircraftStatus",
      "displayName": "飞机运行状态",
      "baseType": "string",
      "mapping": {
        "type": "column",
        "backingColumn": "aircraft_status",
        "backingDatasetRid": "ri.foundry.main.dataset.xxx"
      }
    }
  }
}
```
- `apiName`：properties 外层 Map 键 + 内部 `apiName` 字段
- `displayName`：属性顶层直接字段
- `backingColumn`：仅在 `mapping.column.backingColumn` 内部

**Edit-only 属性无 mapping.backingColumn**（手动编辑字段不绑定数据集，`mapping` 节点不存在）。

### 2. 业务实例读写 API（Search / Get Object / Modify Object）
**只有 apiName，完全看不到 displayName、backingColumn**：
```json
{
  "objectType": "Aircraft",
  "primaryKey": "A001",
  "properties": {
    "aircraftStatus": "正常运营"
  }
}
```
请求筛选、返回字段、修改入参，全部只用 apiName。

### 3. TypeScript / Python OSDK
```typescript
// 模型元数据（可读取三字段）
Object.values(Aircraft.properties).forEach(prop => {
  console.log(prop.apiName);                    // aircraftStatus
  console.log(prop.displayName);                // 飞机运行状态
  console.log(prop.mapping?.backingColumn);     // aircraft_status
});
// 业务对象实例（仅 apiName 可访问）
const plane = await Aircraft.get("A001");
console.log(plane.aircraftStatus);  // apiName 访问
```

### 4. Ontology as Code（OAC YAML）
```yaml
objectTypes:
  Aircraft:
    displayName: "飞机"
    backingDatasetRid: "ri.foundry.main.dataset.aircraft_ds"
    properties:
      aircraftStatus:        # 顶层 key = apiName（强制 camelCase）
        displayName: "飞机运行状态"
        baseType: string
        mapping:
          column:
            backingColumn: aircraft_status
```

### UI 界面和 API 字段对应
1. **General Tab**：Name 输入框 = `displayName`；API NAME 只读/编辑框 = `apiName`
2. **Column Mapping Tab**：下拉选择的数据集列 = `backingColumn`，对应 API 中 `mapping.backingColumn`

### 对 Gaia 的指导
- Gaia 元数据 API（`GET /ontologies/{o}/object-types/{t}`）应返回 `backing_mapping`（已实现，对齐 `mapping.column.backingColumn`）。
- Gaia 实例读写 API（`/objects/load`）应只返回 apiName（已对齐，提交 83da3d4）。
- **前端 ObjectTypeEditor**：General 区只填 displayName（apiName 只读显示推导结果）；Column Mapping 区下拉选 backingColumn。
- **前端对象详情/列表**：实例数据只用 apiName 访问，displayName 仅作列标题展示。

---

## 五、标准化落地模板（企业统一规范）

### 流水线
1. 数据层 Dataset：统一 `lower_snake_case` 标准列名
2. 本体建模（OAC 优先）：
   - apiName：snake 自动转 camelCase（代码批量生成）
   - displayName：纯中文业务名称，面向业务看板
   - backingColumn：和 Dataset snake 列一一绑定
3. 上层应用 OQL/OSDK/TS：统一使用 camelCase apiName，完全隔离底层存储与前端中文展示

### 示例对照
| Dataset snake 列 | apiName（对外程序） | displayName（前端展示） |
|------------------|--------------------|------------------------|
| aircraft_status | aircraftStatus | 飞机运行状态 |
| order_create_dt | orderCreateDt | 订单创建日期 |
| device_sn | deviceSn | 设备序列号 |

### Gaia benchmark 对照（目标态）
| backingColumn | apiName | displayName |
|---------------|---------|-------------|
| flight_id | flightId | 航班编号 |
| aircraft_id | aircraftId | 飞机编号 |
| delay_minutes | delayMinutes | 延误分钟 |

> benchmark 当前 displayName 含英文（"航班ID"），会推导成 `id`，需改纯中文"航班编号"走 backingColumn 推导（见 handoff 文档）。

---

## 六、对前端设计的具体指导

### 1. ObjectTypeEditor / 创建向导
- **General 区**：只填 `displayName`（Name 输入框）；`apiName` 只读显示，实时预览推导结果（不可手填）。
- **Column Mapping 区**：下拉选 `backingColumn`（数据集列），对应 `mapping.backingColumn`。
- **新建属性流程**：先填 displayName → 系统推导 apiName 预览 → 绑定 backingColumn（可留空，后续补）→ 保存。
- **apiName 不可编辑**：保存后 apiName 永久固化，UI 不提供修改入口（防上层代码断裂）。

### 2. 对象列表 / 详情
- 列标题用 `displayName`（中文），实例数据用 `apiName` 访问。
- backingColumn 不在任何业务界面出现，仅在"数据集绑定"配置面板可见。

### 3. ActionsOverview / ExecuteActionDialog
- Action 参数表单：参数名用 `displayName`，提交时用 `apiName` 作 key。
- ObjectReference 参数（`object_type_ref`）：渲染为对象选择器，值是对象主键（apiName 对应的属性值）。
- 执行结果：`affected_objects` 等 dict 的 key 是对象 apiName。

### 4. 错误反馈
- 422 校验失败：`formatError` → "输入参数有误，请检查表单填写"。
- 404 对象不存在：→ "资源不存在或已被删除"。
- 409 OCC 冲突：→ "对象已被他人修改，请刷新后重试"（已实现）。

---

## 七、关联文档
- `docs/web-ui/ontology-manager.md`：本体管理器前端设计（需按本文档更新 apiName 生成逻辑）
- `docs/design/frontend-hci-review.md`：前端 HCI 评审
- `docs/architecture/adr-action-mutation-mapping.md`：Action Mutation Mapping ADR
- `docs/handoff-apiname-derivation.md`：apiName 推导完整接入交接
- `docs/reference.md`：Palantir 本体层向 Agent 层交付工具的技术原理
- `docs/architecture/architecture_plan.md`：架构设计（PhysicalColumnRef 等需同步改名）
