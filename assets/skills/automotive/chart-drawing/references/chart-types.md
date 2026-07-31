# 图表类型与 JSON Config 示例

`draw_chart.py` 通过 stdin 接收 JSON config，按 `chart_type` 分发渲染。以下为 6 种图表的完整示例。

## 通用参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `chart_type` | string | ✅ | `bar` / `line` / `pie` / `scatter` / `area` / `radar` |
| `title` | string | ❌ | 图表标题（顶部居中） |
| `x_label` | string | ❌ | X 轴标题 |
| `y_label` | string | ❌ | Y 轴标题 |
| `series` | array | ✅ | 数据系列，每项含 `name` / `values` / 可选 `color` |
| `categories` | array | ❌ | X 轴分类标签（bar/line/scatter/area 用；pie 用作扇形标签） |
| `output` | string | ✅ | 输出相对路径（如 `output/chart.png`），相对工作区根 |
| `width` | int | ❌ | 画布宽度（默认 800） |
| `height` | int | ❌ | 画布高度（默认 600） |

### series 字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | ✅ | 系列名称（图例显示） |
| `values` | array[number] | ✅ | 数值列表 |
| `color` | string | ❌ | 十六进制颜色（如 `#FF6B6B`）；不指定则从默认色板取 |

## 1. 柱状图（bar）

分组并排柱子，每组 N 个 series 并排，适合多组数值对比。

```json
{
  "chart_type": "bar",
  "title": "武汉7天天气预报",
  "x_label": "日期",
  "y_label": "温度(°C)",
  "series": [
    {"name": "最高温", "values": [32, 35, 33, 30, 28, 31, 34], "color": "#FF6B6B"},
    {"name": "最低温", "values": [22, 24, 23, 20, 18, 21, 23], "color": "#4ECDC4"}
  ],
  "categories": ["7/14", "7/15", "7/16", "7/17", "7/18", "7/19", "7/20"],
  "output": "output/weather_7days.png"
}
```

## 2. 折线图（line）

每 series 一条折线，适合趋势变化。

```json
{
  "chart_type": "line",
  "title": "客户情绪变化曲线",
  "x_label": "时间",
  "y_label": "情绪值",
  "series": [
    {"name": "情绪值", "values": [55, 85, 88, 90, 92, 80, 68], "color": "#45B7D1"}
  ],
  "categories": ["19:43", "19:50", "19:55", "19:59", "20:07", "20:12", "20:16"],
  "output": "output/emotion_chart.png"
}
```

## 3. 饼图（pie）

取第一个 series 的 values，categories 作扇形标签，适合占比分布。

```json
{
  "chart_type": "pie",
  "title": "客户关注点权重分布",
  "series": [
    {"name": "权重", "values": [95, 85, 80, 70]}
  ],
  "categories": ["智能驾驶", "动力续航", "配置价格", "操控通过性"],
  "output": "output/focus_weights.png"
}
```

## 4. 散点图（scatter）

每 series 一组散点，适合相关性展示。

```json
{
  "chart_type": "scatter",
  "title": "试驾时长与成交概率",
  "x_label": "试驾时长(分钟)",
  "y_label": "成交概率(%)",
  "series": [
    {"name": "已成交", "values": [35, 42, 28, 50, 33], "color": "#4ECDC4"},
    {"name": "未成交", "values": [15, 22, 18, 25, 12], "color": "#FF6B6B"}
  ],
  "categories": ["客户A", "客户B", "客户C", "客户D", "客户E"],
  "output": "output/scatter_duration_vs_deal.png"
}
```

## 5. 面积图（area）

折线下方填充半透明色，适合累计趋势。

```json
{
  "chart_type": "area",
  "title": "月度销售额趋势",
  "x_label": "月份",
  "y_label": "销售额(万元)",
  "series": [
    {"name": "2024年", "values": [120, 150, 180, 200, 190, 220, 250, 230, 210, 240, 260, 300], "color": "#45B7D1"},
    {"name": "2023年", "values": [100, 130, 150, 170, 160, 190, 210, 200, 180, 210, 230, 270], "color": "#FFA07A"}
  ],
  "categories": ["1月", "2月", "3月", "4月", "5月", "6月", "7月", "8月", "9月", "10月", "11月", "12月"],
  "output": "output/sales_trend.png"
}
```

## 6. 雷达图（radar）

polar 投影，每个 series 一条闭合多边形，适合多维度能力对比（如销售四维度得分）。

```json
{
  "chart_type": "radar",
  "title": "销售四维度对比",
  "series": [
    {"name": "销售A", "values": [85, 78, 82, 90]},
    {"name": "销售B", "values": [70, 88, 75, 80]}
  ],
  "categories": ["沟通表达", "需求挖掘", "异议处理", "促单成交"],
  "output": "output/sales_radar.png"
}
```

> radar 用 `categories` 当维度标签，**无 `x_label` / `y_label`**（polar 投影无 cartesian 轴，设了也不显示）。
