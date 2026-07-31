---
name: chart-drawing
description: 当用户期望返回图表/可视化/画图（"画图/图表/趋势图/对比图/饼图/柱状图/折线图/散点图/面积图/热力图/heatmap/雷达图/分布图/走势图/数据图/可视化"），或模型判断数据适合图表展示（多组数值对比、趋势变化、占比分布、情绪曲线、时间线）时触发。调 draw_chart.py（matplotlib）生成 PNG，用 markdown 相对路径引用。不触发：纯文字问答、用户没要求图表且数据不适合可视化。
---

# 图表绘制

当用户要求图表，或你判断数据适合可视化时，调用 `draw_chart.py` 生成 PNG 并用 markdown 相对路径引用。

## 触发场景

- **用户明确要求**："画个图/做个图表/可视化/趋势图/对比图/饼图/柱状图/折线图"
- **模型判断需要**：回复含多组数值对比、趋势变化、占比分布，文字描述不如图表直观时

## 工作流程

### 步骤 1：提取图表需求

从用户消息 + 对话上下文提取：
- **chart_type**：bar（柱状图）/ line（折线图）/ pie（饼图）/ scatter（散点图）/ area（面积图）/ radar（雷达图）
- **title**：图表标题
- **series**：数据系列（每系列含 name + values + 可选 color）
- **categories**：X 轴分类标签
- **x_label / y_label**：轴标题
- **output**：输出路径（如 `output/weather_7days.png`）

### 步骤 2：构造 JSON config + 执行 draw_chart.py

用 `terminal` 工具执行脚本（不用 `execute_code`）：

```bash
mkdir -p "${HERMES_HOME:-$HOME}/output" && \
echo '<JSON config>' | python3 {{profile_skills_dir}}/chart-drawing/scripts/draw_chart.py
```

**JSON config 示例**（柱状图）：
```json
{
  "chart_type": "bar",
  "title": "7月销量对比",
  "x_label": "车型",
  "y_label": "销量(台)",
  "series": [
    {"name": "M817", "values": [45, 52, 38, 61, 48]},
    {"name": "M9", "values": [23, 31, 28, 35, 30]}
  ],
  "categories": ["周一", "周二", "周三", "周四", "周五"],
  "output": "output/sales_comparison.png"
}
```

脚本 stdout = 相对路径（如 `output/sales_comparison.png`），失败输出 `{"error":"..."}`。

### 步骤 3：引用图片

读 stdout 路径，用 markdown 图片语法引用：
```
![7月销量对比](output/sales_comparison.png)
```

## 文件引用规范（最高优先级，违反则图片不展示）

### 图片：使用 Markdown 图片语法

引用图片时，使用 `![简短描述](相对路径)` 格式，路径为**相对于工作区根目录的相对路径**。

### 路径格式要求

1. **使用相对路径**：如 `output/chart.png`，不要用绝对路径（`/opt/data/...`）、不要用 `file://` 协议、不要加 `./` 前缀
2. **路径分隔符用正斜杠 `/`**：如 `output/sub/chart.png`
3. **文件名不含括号或空格**：用下划线或连字符连接，如 `chart_v2.png` 而非 `chart (2).png`
4. **路径中不含 `..`**：不要引用工作区外的文件
5. **图片用标准扩展名**：.png .jpg .jpeg .gif .svg .webp
6. **先创建文件再引用**：确保引用时文件已实际写入磁盘，路径与写入路径完全一致

### 非图片文件：使用 Markdown 链接语法

引用非图片文件（PDF、CSV、JSON 等）时，使用 `[文件名](相对路径)` 链接格式：

```
[分析报告](output/report.pdf)
[数据文件](data/results.csv)
```

### 禁止的做法

- ❌ 不要用绝对路径：`![图](/opt/data/profiles/base/output/chart.png)`
- ❌ 不要用 file:// 协议：`![图](file:///opt/data/.../chart.png)`
- ❌ 不要用 ./ 前缀：`![图](./output/chart.png)`
- ❌ 不要用 .. 引用上级目录：`![图](../shared/chart.png)`
- ❌ 不要文件名含括号：`![图](output/chart(1).png)`
- ❌ 不要用 HTTP URL 指向本地文件：`![图](http://localhost:8642/file.png)`
- ❌ 不要只描述路径不用 markdown 语法：「图片在 output/chart.png」← 客户端无法解析

## 图表类型选择指南

| 数据场景 | 推荐类型 | 示例 |
|---------|---------|------|
| 多组数值对比 | **bar** | 各车型周销量对比 |
| 趋势变化 | **line** | 7天温度趋势 |
| 占比分布 | **pie** | 客户来源渠道占比 |
| 相关性分布 | **scatter** | 价格与销量关系 |
| 累积趋势 | **area** | 月度累积销售额 |
| 多维度能力对比 | **radar** | 销售四维度得分对比 |

完整 JSON config 示例见 `references/chart-types.md`。

## 约束

- 用 `terminal` 执行 `scripts/draw_chart.py`，**绝对禁止用 `execute_code` 自己写 matplotlib/Python 画图代码**。自己写的代码没有 CJK 字体加载逻辑，中文会乱码。
- **所有标签用中文**：title / x_label / y_label / series.name / categories 全用中文（如"情绪值"、"时间"、"情绪曲线"、"一月"），不用英文。
- draw_chart.py 内置 CJK 字体加载（fontManager.addfont 加载 WenQuanYi Micro Hei），只有通过 draw_chart.py 画图才能正确渲染中文。
- 脚本用 matplotlib（引擎镜像需含 matplotlib + numpy）。
- 输出目录 `output/`（相对 HERMES_HOME）。文件名用下划线/连字符，不含括号/空格。
- 多张图表分别调脚本，每次一个 `output` 路径。
