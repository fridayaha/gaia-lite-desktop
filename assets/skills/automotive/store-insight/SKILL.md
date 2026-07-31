---
name: store-insight
description: >-
  门店销售数据自然语言查询与可视化。用户以店长身份提问，Skill 自动理解意图、生成 SQL、执行查询、输出图表和报告。
  当用户提到门店/门店销售/销售数据/试驾/转化率/业绩/日报/销量排行/月度目标等词汇时触发此 Skill。
  即使没说"查询"或"分析"，只要涉及门店经营指标，就应启用此 Skill。
---

# store-insight — 门店销售数据查询与可视化

## 概述

连接 PostgreSQL 数据库 `store_insight`，让用户用自然语言查询门店销售数据。Skill 负责：
1. 理解用户问题意图
2. 动态生成 SQL
3. 执行查询
4. 生成图表和报告

## 安全约束

- **数据库连接强制只读**：`query_store.py` 在连接级别设置 `readonly=True` + `SET transaction_read_only = on`，LLM 生成的 SQL 无法执行 INSERT/UPDATE/DELETE/DROP 等写操作
- **查询超时保护**：默认 30 秒超时（`statement_timeout`），防止恶劣 SQL 挂死连接
- **密码不硬编码**：所有连接凭据从环境变量读取

## 数据库 Schema

4 张表，10 名员工（店长/销售主管/销售顾问/试驾专员），约 1 年数据（2025-07 ~ 2026-07）：

### employees
| 字段 | 类型 | 说明 |
|------|------|------|
| id | SERIAL PK | 员工ID |
| name | VARCHAR(50) | 姓名 |
| role | VARCHAR(30) | 角色 |
| hire_date | DATE | 入职日期 |
| status | VARCHAR(10) | active/inactive |

### daily_sales
| 字段 | 类型 | 说明 |
|------|------|------|
| employee_id | INT FK | 员工ID |
| date | DATE | 日期 |
| sales_count | INT | 成交量 |
| test_drive_count | INT | 试驾量 |
| customer_count | INT | 接待客户数 |
| revenue | DECIMAL(12,2) | 销售额 |
UNIQUE(employee_id, date)

### store_daily_summary
| 字段 | 类型 | 说明 |
|------|------|------|
| date | DATE UNIQUE | 日期 |
| total_visitors | INT | 到店客流 |
| total_sales | INT | 总销量 |
| total_test_drives | INT | 总试驾量 |
| total_revenue | DECIMAL(14,2) | 总营收 |

### monthly_targets
| 字段 | 类型 | 说明 |
|------|------|------|
| year_month | VARCHAR(7) UNIQUE | 如 2026-07 |
| sales_target | INT | 销售目标 |
| revenue_target | DECIMAL(14,2) | 营收目标 |
| test_drive_target | INT | 试驾目标 |

## 连接配置

所有密码从环境变量读取，不硬编码。连接参数说明见 `scripts/query_store.py` 的 --help。

## 工作流程

针对用户每个提问，按以下步骤执行：

### Step 1: 解析意图

从用户问题中提取以下要素。每个要素可能明确给出，也可能需要推断。

**时间范围**：
| 用户说法 | SQL 转换 |
|----------|----------|
| "这个月" | CURRENT_DATE 所在月的第一天到最后一天 |
| "本月" | 同上 |
| "上周" | 上周一到上周日 |
| "本周" | 本周一到本周日 |
| "本季度" | 当前季度第一天到当前日期 |
| "今年" | 今年1月1日到当前日期 |
| "去年同期" | 去年同月 |
| "上个季度" | 上个季度的第一天到最后一天 |
| "7月份" / "2025年7月" | 指定月份 |
| "最近N天" / "近30天" | 当前日期往前 N 天 |
| "今年以来" | 今年1月1日到当前日期 |
| "环比" | 本期 vs 上期（如本月 vs 上个月） |
| "同比" | 本期 vs 去年同期的同时段 |
| 无明确时间 | 默认本月 |

**聚合粒度**：
| 用户说法 | GROUP BY |
|----------|----------|
| "按天" / "每天" / "每日" | date |
| "按周" / "每周" | DATE_TRUNC('week', date) |
| "按月" / "每月" | TO_CHAR(date, 'YYYY-MM') |
| "按季度" | CONCAT(EXTRACT(year), 'Q', EXTRACT(quarter)) |
| "按员工" / "每人" / "全员" | employee_id |
| "按角色" / "按岗位" | role |
| "按门店整体" | 不分组，直接 SUM/AVG |
| 无明确粒度 | 根据指标推断：排行→按员工，趋势→按月 |

**查询指标**（可组合多个）：
- 销售量（count）：SUM(sales_count)
- 试驾量：SUM(test_drive_count)
- 转化率：SUM(sales_count) / NULLIF(SUM(test_drive_count), 0) — 成交转化率；SUM(test_drive_count) / NULLIF(total_visitors, 0) — 试驾转化率
- 营收：SUM(revenue)
- 客流：SUM(total_visitors) 
- 目标完成率：已完成 / 目标
- 排名：RANK() OVER (ORDER BY ...)
- 趋势：时间序列数据
- 人均产出：SUM / COUNT(DISTINCT employee_id)

**比较类型**：
- 排行 — ORDER BY + RANK()
- 环比 — 本期 vs 上期，用 LAG 或两个子查询
- 同比 — 本期 vs 去年同期的 date_trunc 偏移
- 目标对比 — 实际值 JOIN monthly_targets
- 占比 — 部分 / 总体

**筛选条件**：
- "李销售" → WHERE name = '李销售'（去 employees 表查 name）
- "销售顾问" → WHERE role = '销售顾问'
- "试驾专员" → WHERE role = '试驾专员'
- "排除店长" → WHERE role != '店长'

### Step 2: 组合 SQL

根据解析结果，动态拼接 SQL。**只生成 SELECT 语句**——连接层已强制只读，但仍应避免生成写操作语句。参考模式：

```sql
-- 排行查询
SELECT e.name, e.role,
  SUM(ds.sales_count) AS total_sales,
  SUM(ds.test_drive_count) AS total_test_drives
FROM daily_sales ds
JOIN employees e ON e.id = ds.employee_id
WHERE ds.date BETWEEN '<start>' AND '<end>'
<filter>
GROUP BY e.id, e.name, e.role
ORDER BY <metric> DESC;

-- 时间趋势
SELECT date, total_sales, total_test_drives, total_visitors
FROM store_daily_summary
WHERE date BETWEEN '<start>' AND '<end>'
ORDER BY date;

-- 转化率
SELECT date,
  ROUND(100.0 * total_test_drives / NULLIF(total_visitors, 0), 1) AS test_drive_conv,
  ROUND(100.0 * total_sales / NULLIF(total_test_drives, 0), 1) AS sales_conv
FROM store_daily_summary
WHERE date BETWEEN '<start>' AND '<end>'
ORDER BY date;

-- 目标对比
SELECT mt.sales_target,
  SUM(sds.total_sales) AS achieved,
  ROUND(100.0 * SUM(sds.total_sales) / mt.sales_target, 1) AS pct
FROM monthly_targets mt
CROSS JOIN store_daily_summary sds
WHERE mt.year_month = TO_CHAR(DATE '<date>', 'YYYY-MM')
  AND sds.date BETWEEN '<month_start>' AND '<month_end>'
GROUP BY mt.sales_target;

-- 同比
SELECT TO_CHAR(date, 'YYYY-MM') AS month,
  SUM(total_sales) AS total_sales
FROM store_daily_summary
WHERE date BETWEEN '<this_year_start>' AND '<this_year_end>'
   OR date BETWEEN '<last_year_start>' AND '<last_year_end>'
GROUP BY month ORDER BY month;
```

### Step 3: 执行查询

```bash
python3 scripts/query_store.py --sql "<生成的SQL>"
```

成功则返回 JSON。失败则读取错误信息，修正 SQL 后重试一次。

**超时处理**：查询超过 30 秒会自动终止并返回超时错误。收到超时错误时应缩小时间范围或简化查询后重试。

### Step 4: 生成图表

根据查询结果和问题意图选择图表类型：

```bash
python3 scripts/chart_store.py \
  --data <query_result.json> \
  --title "图表标题" \
  --type bar|line|pie|scatter \
  --output output/chart.png
```

图表类型映射：
| 数据结构 | 推荐图表 |
|----------|----------|
| 排行榜（人/角色 → 数值） | bar（柱状图） |
| 时间序列 → 数值 | line（折线图） |
| 分类 → 占比 | pie（饼图） |
| 两个数值维度关联 | scatter（散点图） |
| 目标 vs 实际 | bar 加目标线 |
| 多指标综合 | 多子图组合 |

### Step 5: 输出报告

以 Markdown 输出分析报告，包含：
1. **摘要句** — 一句话总结关键发现
2. **数据表格** — 核心数据的 Markdown 表格
3. **图表** — 使用 `![图表](output/chart.png)` 相对路径引用
4. **洞察** — 基于数据的解读和建议

图表输出目录统一为 `output/`（相对于 workspace 根目录），引用时使用 `output/chart.png` 格式。

## 脚本使用说明

### scripts/query_store.py

```bash
# 查看帮助
python3 scripts/query_store.py --help

# 执行 SQL 查询（只读模式）
python3 scripts/query_store.py --sql "SELECT * FROM employees;"

# 只打印 SQL，不执行
python3 scripts/query_store.py --sql "SELECT * FROM daily_sales LIMIT 5;" --dry-run

# 保存结果到文件
python3 scripts/query_store.py --sql "..." --output output/query_result.json

# 打印表结构
python3 scripts/query_store.py --schema
```

### scripts/chart_store.py

```bash
# 查看帮助
python3 scripts/chart_store.py --help

# 生成柱状图
python3 scripts/chart_store.py \
  --data output/query_result.json \
  --title "本月员工销售排行" \
  --type bar \
  --output output/chart.png

# 生成折线图
python3 scripts/chart_store.py \
  --data output/trend.json \
  --title "每日试驾转化率趋势" \
  --type line \
  --output output/trend_chart.png
```

## 容错处理

1. **SQL 执行失败**：读取 stderr，判断错误类型。常见错误和处理：
   - 表不存在 → 检查表名大小写，用 `--schema` 确认
   - 除零 → 添加 NULLIF
   - 字段名不对 → 用 information_schema.columns 查正确字段名
   - 时间格式不对 → 检查日期字符串格式
   - 查询超时 → 缩小时间范围或简化查询
   - 只读违规 → 不应出现（只生成 SELECT），如果出现说明 SQL 有误
2. **数据库连接失败**：检查环境变量是否设置
3. **图表生成失败**：检查中文字体，如果不是 PingFang SC，fallback 到 SimHei → DejaVu Sans
4. **如果多次失败**：输出原始错误给用户，建议手动执行 SQL

## 常见陷阱

1. **PostgreSQL 表名和字段名默认大小写不敏感**，但如果在 SQL 中用了双引号则大小写敏感。避免用双引号括表名/字段名。
2. **转化率查询必须用 NULLIF 防除零**，否则有空值或零的天会报错。
3. **日期范围要精确**，"这个月"要算到当前日期，不要多算到月底。
4. **员工姓名是中文**，SQL 中字符串用单引号括起来。
5. **跨天数据** store_daily_summary 每天一条汇总，daily_sales 每人每天一条。问题问"门店整体"走 summary，问"按员工"走 daily_sales + employees JOIN。
6. **如果用户问题模糊不清**（如"看看数据"），反问用户具体想看什么指标和时间范围。
7. **排序默认 DESC**，除非用户明确说"最少""最低""倒数"。
8. **只生成 SELECT 语句**，连接层已强制只读，但仍应避免生成写操作语句。
