# expected_sql — 黄金真值推导器输入

本目录存放**改写后的物理 SQL**,作为黄金真值推导器的输入(见 DESIGN.md §5.2)。

## 来源与改写

- 来源:`MySQL查询脚本.md` 的 17 个业务查询 + 泛化场景。
- 改写依据:DESIGN.md §3.2 的 4 处映射修正:
  - 修正 1:移除 `test_drive_consultant_id` 相关 join(L3-bis)。
  - 修正 2:`lead_follow_record` 列名统一 snake_case。
  - 修正 3:`recording` 用合成表 join(L5)。
  - 修正 4:`user.phone_brand`/`phone_device_model` 无源,expected = null(L8)。

## 参数约定

所有 `:param` 用预编译占位符绑定,**禁止字符串拼接**(防 SQL 注入,见 MySQL查询脚本.md §6.3)。

| 参数 | 类型 | 说明 |
|---|---|---|
| `:lead_id` | str | 线索主键 |
| `:test_drive_id` | str | 试驾主键 |
| `:user_id` | str | 用户主键 |
| `:sales_phone` | str | 销售顾问手机号 |
| `:store_code` | str | 门店编码 |
| `:date` | str | 日期 `YYYY-MM-DD`(前缀匹配) |
| `:date_pattern` | str | 同 `:date`,强调前缀匹配语义 |
| `:formatted_time` | str | 增量同步时间下界 `YYYY-MM-DD HH:MM:SS` |
| `:confidence_min` | decimal | 置信度下界(L7-bis) |

## 用例对应

| SQL 文件 | 读路径用例 | 断言 kind | Tier |
|---|---|---|---|
| L1.sql | L1 单实体点查 | set_eq | 1 |
| L2.sql | L2 过滤+排序 | ordered_list + jaccard≥0.9 | 1(回归缺陷#1) |
| L3.sql | L3 多表反查 | set_eq | 1 |
| L3-bis.sql | L3-bis 试驾→销售反查 | set_eq | 1(修正1回归) |
| L4.sql | L4 聚合统计 | count_eq | 1 |
| L5.sql | L5 LEFT JOIN 可选关联 | set_eq + null_allowed | 1 |
| L6.sql | L6 增量同步 | set_eq | 1 |
| L7.sql | L7 跨门店过滤 | set_eq | 1(安全 S3 复用) |
| L7-bis.sql | L7-bis VIRTUAL filter range | count_eq | 2(回归缺陷#2) |
| L8.sql | L8 无源字段查询 | all_null | 1(修正4回归) |

## 推导流程

1. fixture seeding 用固定种子生成源端物理数据(含 recording 合成表)。
2. 推导器连 MySQL,对每个 SQL 用绑定参数执行,得 expected。
3. 被测本体 API 用同样语义查询,得 actual。
4. 断言引擎按 kind 对比 expected vs actual。
