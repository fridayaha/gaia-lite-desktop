# 待办：Benchmark 检出的后端缺陷（读/写/安全路径）

**记录时间**: 2026-06-29
**来源**: benchmark 端到端验证报告 §7.2（benchmark 目录已删除，此处独立留存，避免缺陷信息丢失）
**状态**: 🟡 待修复（benchmark 检出但尚未治理）
**关联**: `docs/bugfix/db-connection-leak-and-point-lookup-perf.md`（连接泄漏已单独记录）

---

## 背景

航空运营 benchmark 端到端验证（golden 数据集，1 万航班）检出以下后端真实缺陷。
benchmark 框架本身已删除替换，但这些缺陷属于后端技术债，独立于此保留。
每条缺陷附 benchmark 中的表现与初步根因判断，供后续修复参考。

> 注：缺陷描述中引用的用例 ID（如 `read_l2_001`、`write_010`、`sec_001`）来自已删除的
> 航空 benchmark，仅作溯源用，不影响缺陷本身的独立性。

---

## 缺陷 1：读路径 order_by 字段映射失效

- **表现**: `read_l2_001` / `read_l2_002` / `read_l2_004` jaccard < 0.1（延误 top100 仅 9/100 与预期重叠）
- **根因**: `order_by delayMinutes`（camelCase）未映射到物理列 `delay_minutes`（snake_case），排序未生效，返回顺序随机
- **影响**: 任何带 `order_by` 的查询结果不可信，性能/正确性双失真
- **修复方向**: ObjectQueryService / Doris 查询构建器需做 camelCase→snake_case 字段映射，与 assertion 引擎的 `_snake()` 逻辑对齐

## 缺陷 2：VIRTUAL 联邦查询 filter range 语义错误

- **表现**: `read_l2_010`（CrewDetail VIRTUAL 走 Trino 联邦）count=1，expected=5
- **根因**: VIRTUAL 对象的 range filter 语义错误（实际还伴随 catalog not found，部分随 backing_catalog 修复缓解）
- **影响**: VIRTUAL 对象的范围过滤不可用
- **修复方向**: TrinoQueryEngine 的 filter range 处理 + VIRTUAL 对象的 backing_catalog 解析

## 缺陷 3：Action 规则引擎类型转换

- **表现**: `write_010`（ReassignAircraft）返回 422 `'>' not supported between str and int`
- **根因**: `object_type_ref` 参数（如 `aircraftId`）的属性表达式（`newAircraft.aircraftId > 0`）未将值转为 int 就比较
- **影响**: 含 ObjectReference 参数属性比较的 Action 规则全部失效
- **修复方向**: ActionRuleEngine / ActionValidator 在求值前按参数 data_type 做类型转换

## 缺陷 4：write-back changes 不完整

- **表现**: `write_001` / `write_audit_*` / `write_outbox_*` 的 postcondition 失败
- **根因**: upsert/insert 用 Action 修改的部分列，但 MySQL 表的 NOT NULL 列（如 `arr_time`、`old_status`）缺失，导致 INSERT 失败
- **影响**: Action 触发的 write-back 写不回源端，反馈环断裂
- **修复方向**: WriteBackManager 构建 upsert/insert 时需补齐 NOT NULL 列（从现有对象读取或用合理默认值），或在 schema 层放宽约束

## 缺陷 5：OCC 冲突重试成功率低

- **表现**: `conflict_001`（500 并发同 flight_id DelayFlight）成功率 60%；`conflict_002`（ReassignAircraft）成功率 0%
- **目标**: > 99%
- **根因**: OCC 重试/串行化实现缺陷（高并发下乐观锁冲突后未正确重试或重试耗尽）
- **影响**: 高并发写场景数据丢失/部分成功
- **修复方向**: ActionService 的 OCC 重试逻辑——冲突后重新读取版本、重新应用变更、重试上限与退避策略

## 缺陷 6：对象类型级权限未生效

- **表现**: `sec_001` / `sec_002`（无 read 权限访问）expected 403 got 200；漏沙率 66.7%
- **根因**: 权限校验未注入查询路径（ObjectQueryService / ActionService 未调 `GravitinoRegistry.check_access`）
- **影响**: 安全边界形同虚设，任何角色可读任何 ObjectType
- **修复方向**: 在 ObjectQueryService.load_objects / aggregate、ActionService.execute_action 入口注入权限校验；benchmark 的 principal 注入机制需后端配合（当前 principal=anonymous）

## 缺陷 7：连接池泄漏（已单独记录）

- **表现**: 长时间运行后 QueuePool 耗尽（30 idle-in-transaction）
- **根因**: Action/query 路径 session 未完全释放（agent benchmark 跑 5h 后触发）
- **详见**: `docs/bugfix/db-connection-leak-and-point-lookup-perf.md`

---

## 修复优先级建议

| 优先级 | 缺陷 | 理由 |
|--------|------|------|
| P0 | #6 对象类型级权限未生效 | 安全边界缺失，影响数据安全 |
| P0 | #5 OCC 冲突成功率低 | 数据丢失风险 |
| P1 | #1 order_by 字段映射失效 | 影响所有排序查询正确性 |
| P1 | #3 Action 规则引擎类型转换 | 含 ObjectReference 的 Action 失效 |
| P1 | #4 write-back changes 不完整 | 反馈环断裂 |
| P2 | #2 VIRTUAL filter range | 影响面较窄（仅 VIRTUAL 对象 range 过滤） |

> 这些缺陷在新 benchmark 设计时应作为"已知后端限制"参考，避免新 benchmark 误把它们当作框架问题。
> 新 benchmark 还应针对每条缺陷设计回归用例，修复后验证不复发。
