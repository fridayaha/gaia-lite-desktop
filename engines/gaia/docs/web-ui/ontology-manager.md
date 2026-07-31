# 本体管理器 — 前端设计

> 功能范围：本体的创建、对象类型的增删改查。对应 Rail ① 业务定义。
> 最后更新: 2026-06-15

---

## 1. 信息架构

### 1.1 页面焦点

本体管理器是 Gaia 的默认首页。用户在这里完成"定义业务"的核心任务。

### 1.2 界面分区

```
┌─ 侧栏 (280px) ─────────────┬─ 主内容区 ────────────────────────┐
│                              │                                    │
│  本体树                       │  [📋 列表] [🕸 图谱]              │
│  ├─ 本体A                    │  对象卡片网格 / 图谱画布           │
│  │  ├─ 对象1                 │  [+ 新建对象]                      │
│  │  ├─ 对象2                 │                                    │
│  │  └─ 对象3                 │  每个卡片: 名称 · 属性数 · 关系数  │
│  ├─ 本体B                    │  [编辑] [删除]                     │
│  └─ [+ 新建本体]             │                                    │
└──────────────────────────────┴────────────────────────────────────┘
```

### 1.3 视图模式

| 模式 | 说明 | 切换方式 |
|------|------|----------|
| 📋 列表 | 卡片网格，默认视图 | 顶部视图切换按钮 |
| 🕸 图谱 | Cytoscape.js 画布，节点=对象、边=关系 | 顶部视图切换按钮 |

---

## 2. 创建流程：五步向导

### 2.1 入口

侧栏或主区域的"+ 新建对象"按钮。

### 2.2 向导布局

```
┌─ 左侧步骤栏 (220px) ───┬─ 右侧内容区 ─────────────────────────┐
│                          │                                       │
│  ◀ Object Types         │  当前步骤的标题和说明                  │
│  ─────────────────────  │  ──────────────────────────────────── │
│                          │                                       │
│  ● Step 1 选择数据源      │  步骤内容（随选中步骤动态切换）       │
│  ○ Step 2 配置属性        │                                       │
│  ○ Step 3 设置关系        │                                       │
│  ○ Step 4 配置操作        │                                       │
│  ○ Step 5 审核并创建      │                                       │
│                          │                                       │
│  ─────────────────────  │  ──────────────────────────────────── │
│  [Cancel]                │              [← Back]  [Next →]      │
└──────────────────────────┴──────────────────────────────────────┘
```

**导航规则**：
- 左侧步骤栏可**随时点击跳转**，不锁定
- "Back" 和 "Next" 始终可点击
- 点击 "Next" 时若当前步骤校验未通过，**不跳转，显示红色边框和错误提示**

### 2.3 Step 1：选择数据源

用户决定这个对象的数据从哪来。

- 数据集列表（接 `listDatasets()`，按 `kind` 过滤）
- 托管对象(MANAGED)选 `kind=MANAGED` 托管表；可勾"暂不关联"延迟关联
- 虚拟对象(VIRTUAL)选 `kind=VIRTUAL` 虚拟表（必选，无"暂不关联"）
- 虚拟表需先在数据源详情页「登记为虚拟表」

> 术语与交互细节见 [dataset-ontology-binding.md](../design/dataset-ontology-binding.md) §一、§四。已废弃：mock 数据、`PHYSICAL` 叫法、"无数据源继续 + Action 写入"描述。

### 2.4 Step 2：配置属性

这是核心步骤，分两部分：

**上部：主键/标题设置**

```
┌─ 主键设置 ──────────────────────────────────────┐
│ 主键字段 *        [alert_id ▼]                  │
│ ↳ 用于唯一标识每个对象实例，不可重复、不可为空   │
│                                                  │
│ ☑ 使用主键作为默认标题                           │
│ └─ 若不勾选，选择其他字段: [flight_number ▼]     │
└──────────────────────────────────────────────────┘
```

- **PK 选择**：下拉框（radio 改为 select，减少认知负担）
- **Title**：默认跟随 PK；取消勾选后从剩余属性中选择
- 两个字段都是必填，未选择时 Next 触发红色提示

**下部：属性列表（列表 + 行内展开详情）**

行内只保留 4 列（轻量概览）：显示名称 / 类型 / 源列 / 键徽章 + 展开按钮。点击「▼」展开行内详情区，可编辑完整字段：

```
显示名称       类型      源列          键
─────────────────────────────────────────────
 PK  航班编号  String▼   flight_id    ▼ ✕
     航班号    String▼   flight_no    ▼ ✕
─────────────────────────────────────────────
[显示名称 ___] [类型▼] [源列▼] [+ 添加]
```

展开详情区字段：显示名称、API 名称（只读预览 camelCase + ✨AI）、**描述**（autosize textarea）、源列(backingColumn)、类型、可搜索、可为空、主键、标题。

- PK/Title 用轻量标签标记（`PK` `Title` 徽章），不占独立列
- **属性 apiName 由后端推导**，前端只读预览，不手填
- **描述**：autosize textarea（`react-textarea-autosize`），供 LLM 语义理解
- **Searchable**：控制 Doris 倒排索引。`true` → `indexed=true`。默认勾选

### 2.5 Step 3：设置关系

定义本对象与其他对象的关系。

- 已定义关系的列表（可删除）
- 添加表单：选择目标对象、基数（1:1 / N:1）、显示名称、API 名称
- 目标对象从当前本体已有对象列表中选择

### 2.6 Step 4：配置操作（可选）

定义可对此对象执行的动作。可跳过。

- 已定义操作的列表（可删除）
- 添加表单：API 名称、显示名称、描述

### 2.7 Step 5：审核并创建

汇总所有配置，用户确认后一次性提交。

- 显示：对象名、存储类型、主键、属性数、关系数、操作数
- 点击"Create Object" → 单次 API 调用原子提交

---

## 3. 编辑流程

### 3.1 入口

对象卡片上的 **[编辑]** 按钮。

### 3.2 复用创建向导

编辑完全复用 `CreateObjectWizard`，通过 `editing={true}` + `initialData` props 进入编辑模式：

| 差异 | 创建 | 编辑 |
|------|------|------|
| 标题 | "创建对象" | "编辑对象" |
| 按钮 | "Create Object" | "Save Changes" |
| 初始数据 | 空 | 预填现有对象数据 |
| API | POST /create | PATCH /batch |

**预填逻辑**：点击编辑时，先异步加载对象的属性列表（`listProperties`），然后以 `initialData` 传入向导。

---

## 4. 删除流程

### 4.1 确认机制

点击 **[删除]** 弹出 `ConfirmDialog`：

```
┌─ 确认删除 ──────────────────────────────────┐
│                                               │
│  删除"工单"                                   │
│  此操作不可撤销。                              │
│                                               │
│  将同时删除:                                  │
│  · 8 个属性                                   │
│  · 2 个关系                                   │
│  · 4 个动作                                   │
│                                               │
│  输入「工单」确认删除: [_____________]        │
│                                               │
│                [取消]    [确认删除]            │
└───────────────────────────────────────────────┘
```

- 安全级别：**高危** — 需输入对象名确认
- 显示级联影响（属性/关系/动作数量）
- 确认按钮在输入匹配后才可点击

---

## 5. 图谱视图

### 5.1 渲染

Cytoscape.js Canvas 渲染器，支持千级节点。

### 5.2 交互

| 操作 | 行为 |
|------|------|
| 悬停节点 | 关联节点+边高亮，非关联元素透明度降至 0.3 |
| 移出节点 | 恢复全部正常显示 |
| 单击节点 | 选中对象，右侧可选显示详情 |
| 双击节点 | (预留) 打开编辑 |
| 滚轮 | 缩放画布 |
| 拖拽空白 | 平移画布 |
| 拖拽节点 | 手动调整布局 |

---

## 6. 组件清单

| 组件 | 文件 | 复用 |
|------|------|------|
| `OntologySidebar` | `components/OntologySidebar.tsx` | 本体树 |
| `CreateObjectWizard` | `components/CreateObjectWizard.tsx` | 创建 + 编辑 |
| `ConfirmDialog` | `components/ConfirmDialog.tsx` | 删除确认 |

### 6.1 CreateObjectWizard Props

```typescript
interface CreateObjectWizardProps {
  objectTypes?: { id: string; display_name: string }[];  // 已有对象列表（用于关系选择）
  initialData?: Partial<ObjectWizardData>;                 // 编辑时预填
  editing?: boolean;                                       // 编辑模式
  onComplete: (data) => void;                              // 完成回调
  onCancel: () => void;                                    // 取消回调
}
```

### 6.2 ConfirmDialog Props

```typescript
interface ConfirmDialogProps {
  title: string;
  message: string;
  details?: string[];       // 级联影响列表
  requireName?: string;     // 需输入匹配的名称才能确认
  onConfirm: () => void;
  onCancel: () => void;
}
```

---

## 7. 校验规则

### 7.1 必填校验（Step 2）

| 字段 | 规则 | 触发时机 | 提示方式 |
|------|------|----------|----------|
| Display name | 非空 | 点击 Next | 红色边框 + "请输入显示名称" |
| 主键字段 | 已选择 | 点击 Next | 红色边框 + "请选择主键字段" |
| 标题字段 | 未勾选"使用主键作标题"时必选 | 点击 Next | 红色边框 + "请选择标题字段" |
| 属性 | 至少 1 个 | 点击 Next | 计数变红 + "至少需要添加一个属性" |

### 7.2 API name 生成规则

API name 由系统从 displayName 自动推导，用户可修改（修改后不再自动覆盖）。推导优先级：displayName（ASCII 开头）> backingColumn > 兜底 `prefixN`。中文 displayName 走 backingColumn 或点「✨ AI」推导。

| 实体 | 风格 | pattern | 推导者 |
|------|------|---------|--------|
| 对象类型 | **PascalCase**（首字母大写） | `^[A-Z][a-zA-Z0-9]{0,99}$` | 前端 `lib/deriveApiName.ts` 预览 + 后端校验 |
| 属性 / Link / Action | **camelCase**（首词小写） | `^[a-z][a-zA-Z0-9]{0,99}$` | **后端** `core/naming.derive_api_name`（前端不传 api_name） |

- 对象 apiName：前端实时预览（可编辑），提交时传给后端
- 属性 apiName：前端只读预览，**不提交**，后端从 display_name/backing_column 推导
- 本地推导失败（返回 `prefixN`）时显示「✨ AI」按钮，调 `/ai/generate` 推导
- 详见 `docs/reference-palantir-ontology.md` §二、`docs/handoff-apiname-derivation.md`

---

## 8. 后端对应

### 8.1 批量原子操作

```
POST   /ontologies/{onto}/object-types/create    # 创建（对象 + 属性 + 关系，单事务）
PATCH  /ontologies/{onto}/object-types/{type}/batch  # 编辑（替换属性，单事务）
```

### 8.2 唯一性约束

- 数据库层：`UNIQUE(ontology_id, api_name)` 约束
- Python 服务层：`define_object_type` + `define_object_type_batch` 创建前先查重

### 8.3 Searchable → indexed 映射

前端 `searchable: boolean` → 后端 `PropertyInput.searchable` → ORM `PropertyDefModel.indexed`

---

*关联: `CLAUDE.md` 第四原则（前端开发质量守则）*
