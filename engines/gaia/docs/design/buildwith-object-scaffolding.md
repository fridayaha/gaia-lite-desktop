# BuildWith：从数据集脚手架生成对象类型

> **版本**: v1.0
> **日期**: 2026-06-28
> **对标系统**: Palantir Foundry（Ontology Manager "guided step-by-step helper" + 第三方 PalantirOntologyGenerator 的 schema → ontology 自动生成）
> **核心目标**: 落实"把复杂留给自己，把简单留给用户"——用户选一个数据集，AI 流式推导出完整对象类型结构（元数据 + 属性 + 主键 + 标题），用户只需确认/微调。
>
> **前置文档**:
> - [数据集与本体关联设计](./dataset-ontology-binding.md)
> - [前端 HCI 审视报告](./frontend-hci-review.md)
> - [实现状态路标](../architecture/implementation-status.md)
>
> **本文件性质**: 供开发直接实施的设计基准。含接口契约、Prompt 评审稿、流式策略、兜底方案。

---

## 目录

- [〇、背景与问题](#〇背景与问题)
- [一、向导步骤精简](#一向导步骤精简)
- [二、端点设计 `/ai/scaffold`](#二端点设计-aiscaffold)
- [三、输出 Schema](#三输出-schema)
- [四、Prompt 评审稿](#四prompt-评审稿)
- [五、后端校验与失败兜底](#五后端校验与失败兜底)
- [六、流式渐进填充策略](#六流式渐进填充策略)
- [七、实施清单](#七实施清单)

---

## 〇、背景与问题

### 现状

`CreateObjectWizard`（`src/web-ui/src/components/CreateObjectWizard.tsx`）当前 5 步线性向导：

1. 选择数据集（含存储类型）
2. 配置属性 ← **对象元数据（Display name / API name / Description）+ 主键/标题 + 属性表全堆在这一步**
3. 设置关系
4. 配置操作（当前是"稍后配置"空壳占位）
5. 审核并创建

### 问题

1. **第 2 步过载**：元数据、键配置、属性表三类不同语义的内容塞在同一步，违背"渐进式披露"。
2. **元数据位置不当**：对象类型元数据（名称/描述）被埋在"配置属性"里，且在数据集选择之后——用户先选了数据集却还没给对象起名，认知顺序反了。
3. **第 4 步是空壳**：当前就是一段"稍后配置"说明，用户点进去无操作，产生"为什么让我走这步"的困惑，纯负担。
4. **可选步骤强制走**：关系（可选）、动作（可稍后）让所有用户都强制经过，把高级需求强加给普通用户。
5. **属性自动生成过简**：`handleGenerateFromDataset` 只填 `display_name`(=列名)、`data_type`(类型映射)、`source_column`(=列名)，`nullable` 硬编码 true、`searchable` 全 false、主键只靠 `id` 启发式、标题留空——用户仍需大量手动补全。

### 业界参考

- **Palantir Foundry** 官方向导 7 步，但关键设计：元数据独立成步、选 backing datasource 后自动回填元数据 + 自动映射列为属性、Actions/Save location 可跳过。
- **PalantirOntologyGenerator**（第三方）印证"从数据集 schema 推导完整 ontology"的工程实践：自动类型映射、3 策略外键推断、LLM 生成列级 description、PII 检测。

### 设计原则对齐

- CLAUDE.md 第一原则"把复杂留给自己，把简单留给用户"：用户只选数据集，AI 推导完整结构。
- "All-in-AI" 产品方向：主键/标题由 AI 推导，不写固定规则。
- "先定义业务概念，再连接数据"：方案 B（元数据放数据集之后）配合 BuildWith 自动回填，兼顾。

---

## 一、向导步骤精简

### 新步骤结构（3 主步 + 可选延后）

| 步骤 | 内容 | 变化 |
|------|------|------|
| 1. 选择数据集 | 存储类型（MANAGED/VIRTUAL）+ 数据集筛选/选择/暂不关联 | 存储类型从原 Step 0 保留于此；选定后**自动触发 scaffold** 流式填充 |
| 2. 配置属性与键 | 对象元数据（Display name / API name / Description，已被 scaffold 自动填充）+ 属性表 + 主键/标题 | 移除"被埋在属性步"的元数据错位感（元数据置顶于此步，scaffold 已填好，用户确认/微调） |
| 3. 审核并创建 | 配置总览确认 | 不变 |

### 延后/移除

| 原步骤 | 处理 | 理由 |
|--------|------|------|
| 设置关系 | **延后**：折叠在 Step 2 底部"高级"区，或创建后在对象详情面板配置 | 关系需"指向已存在对象"，新建第一个对象时无目标；Palantir 也是对象创建后单独管理 link types |
| 配置操作 | **移出向导**：统一引导到对象详情面板 → 动作 → + 新建动作 | 当前已是"稍后配置"空壳，移除零损失；动作需对象已存在才能定义参数/规则 |

### 负担评估

- **普通用户**：3 步走完——选数据集 → 扫一眼 scaffold 自动生成的配置 → 确认。其中真正动手的只有"选数据集 + 偶尔微调"。
- **需要关系的用户**：Step 2 展开"高级"配置，或创建后补。
- 视觉/导航负担从"点 5 次 Next"降到"点 2 次 Next"。

### 存储类型位置决策

存储类型放 Step 1（数据集步）而非独立成元数据步：
- 它决定后续数据集筛选（MANAGED/VIRTUAL 不同列表）
- 它影响后续约束（VIRTUAL 不可写动作、不可跳过数据集）
- 前置让 Step 2 的 UI 预知约束
- 与原向导 Step 0 一致，减少改动

---

## 二、端点设计 `/ai/scaffold`

### 命名

端点名 **`/ai/scaffold`**。理由：
- 与现有 `/ai/agent`、`/ai/generate`、`/ai/stream` 风格一致（动词、短、描述能力而非业务对象）
- "scaffold"（搭脚手架）精准表达 BuildWith 本质：从数据集搭出对象类型骨架，用户再微调
- 前端函数对应 `scaffoldObjectType(datasetSchema)`，语义连贯

### 两层架构

**层 1：底层结构化流式能力（可复用）** — `src/ontology/services/ai_generate.py` 新增

```python
async def stream_structured(
    output_type: type[BaseModel],
    instructions: str,
    prompt: str,
) -> AsyncIterator[BaseModel]:
    """结构化流式输出。任意 Pydantic schema，流式产出部分对象。
    AI SDK streamObject 的 Python 等价物。"""
    agent = Agent(
        settings.ai_model,
        system_prompt=instructions,
        output_type=output_type,  # pydantic-ai Tool Output 模式，强制结构化
        defer_model_check=True,
    )
    async with agent.run_stream(prompt) as result:
        async for partial in result.stream_output(debounce_by=None):
            yield partial
```

复用场景：后续关系推断、批量生成、语义增强等均共用此底层。

**层 2：专用端点（针对本次场景）** — `src/ontology/routes/ai.py` 新增

```python
@router.post("/scaffold")
async def scaffold(req: ScaffoldRequest) -> Response:
    async def event_source():
        async for partial in stream_structured(
            ScaffoldResult, SCAFFOLD_INSTRUCTIONS, build_prompt(req)
        ):
            yield f"data: {partial.model_dump_json()}\n\n".encode()
        yield b"data: [DONE]\n\n"
    return StreamingResponse(event_source(), media_type="text/event-stream", ...)
```

**为什么不在 `/ai/stream` 加 `output_schema` 参数做成通用端点**：
- schema 是后端领域知识（ObjectType 结构是 Gaia 特有），让前端定义/传递 schema 是职责错位
- 专用端点把 schema + prompt 收在后端，便于评审和迭代（与 apiName prompt 集中管理的既定做法一致）
- 纯文本流 `/ai/stream` 保持不变，不被结构化逻辑污染

### 请求/响应契约

**请求** `ScaffoldRequest`：
```python
class ScaffoldRequest(BaseModel):
    dataset_api_name: str          # 数据集 api_name
    dataset_display_name: str = "" # 数据集展示名（可选，辅助 AI）
    storage_type: Literal["MANAGED", "VIRTUAL"] = "MANAGED"
    columns: list[DatasetSchemaColumn]  # 列 schema：name/type/nullable
```

**响应**：SSE 流，每条 `data:` 为 `ScaffoldResult` 的部分 JSON（渐进式），末尾 `data: [DONE]`。

### pydantic-ai 结构化输出模式选择

用默认 **Tool Output 模式**（`output_type=PydanticModel`）：
- 默认即用 tool calling 强制结构化，最可靠，DeepSeek 支持
- `stream_output(debounce_by=None)` 流式产出部分对象
- Pydantic 校验 + `ModelRetry` 兜底，输出不合 schema 自动重试

不用 NativeOutput（部分模型不支持 tool + structured 同时）、不用 PromptedOutput（最不可靠）。

---

## 三、输出 Schema

```python
class ScaffoldProperty(BaseModel):
    """AI 推导的单个属性建议。"""

    source_column: str = Field(
        description="对应的物理列名，必须与输入的 dataset 列名完全一致"
    )
    display_name: str = Field(
        description="中文友好展示名，由列名语义推导，如 flight_no → 航班号"
    )
    description: str = Field(
        default="",
        description="该属性的业务含义，一句话，供 LLM 语义理解"
    )
    searchable: bool = Field(
        default=False,
        description="是否常用于过滤/搜索。字符串/枚举类→true；主键/时间戳/二进制→false"
    )
    is_primary_key: bool = Field(
        default=False,
        description="是否为主键（唯一标识对象实例，非空唯一）。整个对象有且仅一个 true"
    )
    is_title_property: bool = Field(
        default=False,
        description="是否为标题字段（界面友好展示对象实例，通常是 name/title 类列）。"
        "整个对象最多一个 true；若无可读标题列则全 false（前端用主键兜底）"
    )


class ScaffoldResult(BaseModel):
    """从数据集 schema 推导的完整对象类型结构。"""

    display_name: str = Field(
        description="对象类型中文展示名，由数据集名/列语义推导，如 flight_info → 航班信息"
    )
    api_name: str = Field(
        description="对象类型 PascalCase apiName，首字母大写纯 ASCII 字母数字，如 FlightInfo"
    )
    description: str = Field(
        description="该对象类型的业务领域描述，1-2 句，供 AI 语义理解"
    )
    primary_key_column: str = Field(
        description="主键列名，必须等于某个属性的 source_column"
    )
    title_column: str | None = Field(
        default=None,
        description="标题列名，必须等于某个属性的 source_column；无合适列时为 null"
    )
    properties: list[ScaffoldProperty] = Field(
        description="全部属性，每列一个；有且仅有一个 is_primary_key=true",
        min_length=1,
    )
```

### 设计要点

- **不让 AI 推 `data_type`**：类型由后端 `trinoTypeToDataType` 确定性映射（更准、零幻觉）。AI 只负责语义判断。
- **不让 AI 改写 `source_column`**：必须等于输入列名，schema 描述约束 + 后端校验兜底。
- **不让 AI 推 `nullable`**：由 schema 的 `col.nullable` 提供，确定性。
- **主键/标题用列名表达**（`primary_key_column`/`title_column`）而非 bool 散落属性里：便于前端映射到属性 index，便于后端校验"列名必须存在"。
- `title_column` 可 null：无合适标题列时前端用主键作标题（与现有 `usePkAsTitle` 逻辑一致）。

### 与现有 `schemas/ai.py` 的关系

`src/ontology/core/schemas/ai.py` 现有 `AiObjectTypeSuggestion` 等是为"从自然语言描述生成对象"设计（`AiGenerateRequest.description`），输入不同，且：
- 现有 `AiPropertySuggestion.api_name` 标 snake_case（但 ObjectType api_name 应 PascalCase、property 应 camelCase，现有标注有误）
- 现有 schema 无 `source_column`（从自然语言生成时不绑定数据集）

**本次不直接复用**，新增 `ScaffoldResult`/`ScaffoldProperty` 专用 schema。现有 `schemas/ai.py` 暂时并存，待自然语言生成场景接入时再统一梳理（标 TODO）。

---

## 四、Prompt 评审稿

### System Prompt（instructions）

```
你是企业数据建模专家。给定一个数据集的列 schema（列名、类型、是否可空），
推导出一个对象类型（ObjectType）的完整结构，供用户在建模向导中确认/微调。

推导要求：
1. display_name：从数据集名和列语义推导中文展示名（如 flight_info → 航班信息）。
2. api_name：PascalCase，首字母大写，纯 ASCII 字母数字，≤99 字符，语义对应 display_name。
3. description：1-2 句业务领域描述。
4. properties：每个列生成一个属性。
   - display_name：列名的中文友好名（如 flight_no → 航班号，created_at → 创建时间）。
   - description：该列的业务含义，一句话。
   - searchable：字符串/枚举类用于过滤的列→true；主键/时间戳/数值度量/二进制→false。
   - source_column：必须与输入列名完全一致，不要改写。
5. primary_key_column：选唯一标识对象实例的列。优先非空的 id 类列；避免可空列。
   必须等于某个属性的 source_column。
6. title_column：选界面友好展示的列（通常是 name/title/label 类字符串列）。
   无合适列时返回 null。必须等于某个属性的 source_column 或为 null。

约束：
- 不要输出 data_type、nullable、source_column 之外的字段（类型由系统映射，nullable 由 schema 提供）。
- primary_key_column 必须存在且唯一；title_column 可为 null。
- 全部属性中，主键列对应的 is_primary_key=true，其余 false；title 列对应的 is_title_property=true。
- 只返回结构化结果，不要解释、不要 markdown。
```

### User Prompt（动态拼装）

```
数据集名：flight_info
数据集展示名：航班信息表（若有，否则同 api_name）
存储类型：MANAGED

列 schema：
- id | bigint | nullable=false
- flight_no | varchar | nullable=false
- airline | varchar | nullable=false
- status | varchar | nullable=true
- depart_time | timestamp | nullable=true
- arrive_time | timestamp | nullable=true
- created_at | timestamp | nullable=true
```

### Few-shot 示例（嵌入 system prompt 末尾）

```
示例：
输入：
数据集名：customer
列 schema：
- customer_id | bigint | nullable=false
- name | varchar | nullable=false
- email | varchar | nullable=true
- phone | varchar | nullable=true
- created_at | timestamp | nullable=true

输出：
{
  "display_name": "客户",
  "api_name": "Customer",
  "description": "客户信息，记录客户基本联系方式。",
  "primary_key_column": "customer_id",
  "title_column": "name",
  "properties": [
    {"source_column": "customer_id", "display_name": "客户ID", "description": "客户唯一标识", "searchable": false, "is_primary_key": true, "is_title_property": false},
    {"source_column": "name", "display_name": "姓名", "description": "客户姓名", "searchable": true, "is_primary_key": false, "is_title_property": true},
    {"source_column": "email", "display_name": "邮箱", "description": "客户邮箱地址", "searchable": true, "is_primary_key": false, "is_title_property": false},
    {"source_column": "phone", "display_name": "电话", "description": "客户联系电话", "searchable": true, "is_primary_key": false, "is_title_property": false},
    {"source_column": "created_at", "display_name": "创建时间", "description": "客户记录创建时间", "searchable": false, "is_primary_key": false, "is_title_property": false}
  ]
}
```

### Prompt 设计说明

- 主键/标题**不写固定规则**，完全交给 AI 语义判断（用户明确要求）。
- 明确"不要输出 data_type/nullable"：避免 LLM 越权生成确定性字段。
- `source_column` 强约束"必须与输入列名完全一致"：防幻觉改写列名。
- 一个 few-shot 示例覆盖"有主键+有标题"的正常路径。**待定**：是否补一个"无标题列（title_column=null）"反例，提升边界场景稳定性——视实测决定。

---

## 五、后端校验与失败兜底

### LLM 输出校验（后端收到结构化输出后）

LLM 可能幻觉，后端在转发给前端前做校验/清洗：

| 校验项 | 处理 |
|--------|------|
| `source_column` 不在输入列集合 | 丢弃该属性 |
| `primary_key_column` 不等于任何属性 `source_column` | 前端主键留空，用户手选 |
| `title_column` 不等于任何属性 `source_column` 且非 null | 前端标题留空，用主键兜底 |
| 属性数 ≠ 输入列数 | 按 `source_column` 去重；缺失的列补确定性骨架 |
| 缺失列补骨架 | `data_type`=trinoTypeToDataType、`nullable`=col.nullable、`display_name`=列名、其余默认 |

校验在端点层做（流式转发前对每个 partial 校验，或对最终结果校验）。**待定**：流式 partial 校验 vs 仅最终校验——倾向于仅最终校验（partial 是中间态，过早校验丢弃会破坏渐进体验），最终结果保证一致。

### 失败兜底（AI 整体失败）

AI 失败时（网络/模型/超时/校验全挂），前端用现有 `handleGenerateFromDataset` 生成**确定性骨架**：
- `display_name` = 列名（不中文化）
- `data_type` = trinoTypeToDataType
- `nullable` = col.nullable
- `source_column` = 列名
- `searchable` = false
- 主键/标题**留空**，强制用户手选（必填项，不能猜错）

前端提示："AI 推导失败，已生成基础结构，请手动补充主键和标题"。

**原则**：AI 是增强，确定性是底线。AI 没回来，用户仍有一个可用的属性骨架可编辑。

---

## 六、流式渐进填充策略

### pydantic-ai 流式机制

`stream_output(debounce_by=None)` 产出部分对象（partial）。前端按字段到达顺序合并填充：

1. 先到 `display_name`/`api_name`/`description` → 填元数据区（Step 2 顶部）
2. `properties` 逐个追加 → 属性表逐行浮现
3. `primary_key_column`/`title_column` 到达 → 标记主键/标题选中态

### 部分 JSON 合并策略

partial 可能字段不全（如 properties 先到、primary_key_column 后到）。前端用"已到的字段先填，未到的等"的合并策略：
- 每个 partial 是 `ScaffoldResult` 的子集，前端按字段 patch 到向导 state
- properties 列表按 `source_column` 去重合并（partial 可能重复包含已到属性）
- 主键/标题列名到达后，映射到对应属性 index 设置 `primaryKeyIndex`/`titlePropIndex`

### 渐进 vs 一次性

采用**渐进式**（用户确认）：属性逐个浮现，用户能感知 AI 在工作，体验优于"等几秒一次性填入"。代价是前端要处理 partial 合并，但逻辑可控（按 `source_column` 合并）。

### 同时确定性补全

AI 推导的属性只有 6 个语义字段，`data_type`/`nullable` 由前端在收到每个属性时**立即用确定性规则补全**（不等 AI，AI 不产出这俩字段）：
- 收到 `{source_column, display_name, ...}` → 前端查 dataset schema 补 `data_type`=映射、`nullable`=col.nullable
- 这样属性表每一行浮现时就是完整的，不会缺类型显示

---

## 七、实施清单

### 后端

- [ ] `src/ontology/services/ai_generate.py`：新增 `stream_structured()` 通用结构化流式函数
- [ ] `src/ontology/routes/ai.py`：新增 `ScaffoldRequest`/`ScaffoldResult`/`ScaffoldProperty` schema + `/ai/scaffold` 端点
- [ ] Prompt + few-shot 嵌入（评审稿定稿后）
- [ ] LLM 输出校验（source_column 存在性、主键/标题列名一致性、缺失列补骨架）
- [ ] 后端单测：mock LLM 返回，验证校验/清洗逻辑

### 前端

- [ ] `src/web-ui/src/components/CreateObjectWizard.tsx`：向导步骤精简为 3 主步（STEPS 数组、`canGoNext` 各 case、`activeStep` 边界、review 索引调整）
- [ ] 关系步骤：折叠为 Step 2 底部"高级"区或延后到详情面板
- [ ] 动作步骤：移出向导，统一引导到详情面板
- [ ] `src/web-ui/src/api/ai.ts`：新增 `scaffoldObjectType()` SSE 流式消费客户端
- [ ] Step 1 选定数据集后自动触发 scaffold，流式渐进填充 Step 2 状态
- [ ] 收到每个属性 partial 时，前端用确定性规则补全 `data_type`/`nullable`
- [ ] 主键/标题列名 → 映射属性 index（`primaryKeyIndex`/`titlePropIndex`）
- [ ] AI 失败兜底：回退 `handleGenerateFromDataset` 确定性骨架 + 提示用户补主键/标题
- [ ] 草稿恢复、编辑模式（initialData）逻辑适配新步骤结构
- [ ] 前端单测：mock SSE 流，验证渐进填充与兜底

### 文档与状态

- [ ] 更新 `docs/architecture/implementation-status.md`：前端向导步骤、`/ai/scaffold` 端点状态
- [ ] `docs/design/buildwith-object-scaffolding.md`（本文档）作为实施基准
- [ ] 视实测决定是否补"无标题列"few-shot 反例

### 验收标准

- [ ] 用户选数据集后，无需手动填元数据/属性，AI 流式生成完整结构
- [ ] 主键/标题由 AI 推导，非固定规则
- [ ] AI 失败时仍有可用骨架，用户可手动补全
- [ ] 向导 3 步走完，关系/动作不再强制经过
- [ ] `npm run build` + 前端测试 + 后端测试全绿
