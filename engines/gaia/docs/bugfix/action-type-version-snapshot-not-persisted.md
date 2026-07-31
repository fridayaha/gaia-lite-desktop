# 待修复：ActionType 版本快照在 define/update 路径未持久化

**发现时间**: 2026-06-27
**影响范围**: ADR-011 ActionType 版本管理（审计 / 回滚）
**状态**: ✅ 已修复（方案 A + 方案 B 事务单元）

---

## 现象

ActionType 的 `version` 字段在 define/update 后正确递增（v1 → v2 → …），
但 `action_type_versions` 历史快照表始终为空：

```
GET /actions/definitions/Airline/reassignAircraft         → version: 1
GET /actions/definitions/Airline/reassignAircraft/versions → []   ← 应有 v1
```

导致前端「版本历史」抽屉永远显示「无历史版本」，回滚功能无数据可用。

---

## 修复（已实施）

### 方案 A：让快照方法自包含提交 + 可观测

`publish_action_type_version` 加 `auto_commit` 参数（默认 True），与同类方法一致；
`_publish_version_snapshot` 的 `except Exception: pass` 改为 `logger.warning(...) + raise`，
快照失败不再静默，且让外层事务回滚（原子性）。

### 方案 B：Service 层事务单元抽象（对标业界最佳实践）

调研 SQLAlchemy 2.0 / FastAPI 最佳实践（item 10 "Keep Transaction Boundaries
Explicit"、item 12 "Do Not Commit Inside Low-Level Helpers"）后落地：

1. **低层 metadata 方法加 `auto_commit` 参数**（`create_action_type` / `update_action_type` /
   `publish_action_type_version`）：默认 True 向后兼容，事务内传 False 只 flush 不 commit ——
   对齐 item 12"低层 helper 只 flush，commit 由 use-case 决定"。
2. **`PostgresMetaStore.transaction()` asynccontextmanager**：service 层 unit-of-work 入口，
   正常退出 commit、异常 rollback。用 try/commit/except/rollback 模式（非 `session.begin()`，
   因 AsyncSession autobegin 在只读查询后已有事务，`begin()` 会报 InvalidRequestError）。
3. **`MetadataOwnerMixin.transaction()`**：service 编排入口，委托给 metadata（保持分层，
   service 不直接操作 session）。
4. **define/update/rollback 用 `async with self.transaction():` 包裹**，内部 `auto_commit=False`，
   ActionType 写入 + 版本快照原子提交。

### 顺带修复：快照序列化 bug

`_publish_version_snapshot` 用 `action_type.model_dump(mode="json")` 替代 `model_dump()`，
避免 datetime 对象无法写入 JSON 列（此前被 best-effort 吞掉，现在事务内会 raise）。

### 验证

- define → version=1，versions 表有 v1 ✓
- update → version=2，versions 表有 [1,2] ✓
- rollback 到 v1 → version=3，versions 表有 [1,2,3] ✓
- 快照失败 → 整个事务回滚（不再静默丢失）✓
- 新增单测：`test_define_action_type_publishes_v1_snapshot`、
  `test_update_snapshot_failure_rolls_back_whole_transaction` ✓
- 现有 50 个 action 单测 + 集成测试全过 ✓

---

## 根因（架构层）

问题不是单点 bug，而是 **Service / Layer 事务边界设计与"best-effort"异常吞噬** 两个架构决策叠加。

### 1. 事务边界错位：快照写入游离在 commit 之外

`ActionService` 的 define/update 流程：

```
ActionService.define_action_type / update_action_type
  ├─ self._metadata.create_action_type / update_action_type
  │    └─ _flush_and_commit()   ← ① 提交 ActionTypeModel 变更，事务关闭
  └─ self._publish_version_snapshot(updated)
       └─ self._metadata.publish_action_type_version(...)
            └─ self._session.add(model)   ← ② 仅 add，无 commit
                                              且事务已在 ① 关闭，此 add 进入新事务
```

`publish_action_type_version` 的 docstring 明确写着：

> Does NOT auto-commit — caller manages the transaction.

但 **caller（`_publish_version_snapshot`）并没有 commit**。Service 层在调完 metadata 后
直接返回，请求结束时 `service.aclose()` → `meta.close()` → `session.close()`，
未 commit 的 `add` 被静默回滚。

对比同文件其他 metadata 方法（`create_action_type`/`update_action_type`/`upsert_object_state`…）
都自带 `_flush_and_commit()`，唯独 `publish_action_type_version` 遵循"caller manages transaction"
约定却无人履约 —— **约定与实现脱节**。

### 2. "best-effort" 吞噬掩盖了故障

`_publish_version_snapshot` 用 `except Exception: pass` 吞掉所有异常：

```python
async def _publish_version_snapshot(self, action_type, published_by):
    try:
        ...
        await self._metadata.publish_action_type_version(...)
    except Exception:
        pass  # version snapshot failures must not break action type creation
```

设计意图是"快照失败不影响主流程"（合理），但副作用是：**连"快照根本没写入"这种
静默丢失也被一并吞掉**。没有日志、没有指标、没有告警 —— 故障完全不可观测。
`except Exception: pass` 让任何根因（事务未提交、字段不匹配、连接断开）都消失在沉默里。

### 3. 两路径都受影响（非 update 独有）

define 路径同样漏 v1 快照（`reassignAircraft` define 后 version=1 但 versions 表 0 条）。
因为 define 和 update 用同一套 `_publish_version_snapshot`，同样的"add 后无 commit + 吞异常"。

---

## 因果链

```
metadata.create/update_action_type 内部 _flush_and_commit() 提交并关闭事务
  → service._publish_version_snapshot 调 publish_action_type_version (只 add)
    → add 进入新事务，但 service 层无后续 commit
      → 请求结束 session.close()，未 commit 的 add 回滚
        → versions 表空
          → except Exception: pass 吞掉（本例无异常，但即便有也吞）
            → 故障不可观测，审计/回滚能力静默失效
```

---

## 架构层面的反思

### A. "caller manages transaction" 约定需要强制机制而非文档

当前靠 docstring 约定"caller 管理事务"，但：
- Service 层调多个 metadata 方法时，**无法从签名看出哪个会 commit、哪个不会**
- `create_action_type` 自带 commit，`publish_action_type_version` 不带 —— 行为不一致
- 这种不一致只能靠读实现才能发现，违反最小惊讶原则

**改进方向**：metadata 方法应统一行为 —— 要么全部"自包含提交"，要么全部"不提交由
caller 统一提交"，不应混合。当前是混合态（CRUD 自提交，version 不提交），是 bug 温床。

### B. "best-effort" 必须可观测

`except Exception: pass` 用于"不影响主流程"是合理的，但必须配套：
- **结构化日志**（warning 级，含 action_type_id / version / 异常类型）
- **指标**（`action_version_snapshot_publish_failures_total` counter）
- **不静默**：至少在开发/测试环境 raise，生产降级为 log

否则"best-effort"退化为"no-effort"，故障只在被用户撞到时才暴露。

### C. Service 层缺乏"原子操作单元"抽象

define/update 是多步操作（写 ActionType + 写 version 快照），应在一个事务内原子完成。
当前 Service 没有事务边界抽象，每个 metadata 方法各自 commit，导致跨方法操作无法原子化。

**改进方向**：Service 层引入 `async with self._metadata.transaction():` 单元，
内部所有 metadata 调用共享一个事务，单元结束时统一 commit/rollback。这样：
- define/update + 快照写入原子化（任一失败全回滚）
- 消除"谁该 commit"的歧义

---

## 修复方案（待实施）

> 已实施，见上方「修复（已实施）」节。原计划保留如下供参考。

### 方案 A（最小改动，推荐先做）

让 `publish_action_type_version` 自包含提交，与同类方法一致：

```python
# postgres_meta_store.py
async def publish_action_type_version(self, ...):
    existing = ...
    if existing is not None:
        return existing
    model = ActionTypeVersionModel(...)
    self._session.add(model)
    await self._flush_and_commit()   # ← 新增，与 create/update_action_type 一致
    return model
```

并把 `_publish_version_snapshot` 的 `except Exception: pass` 改为 `except Exception: logger.warning(...)`，
保留"不影响主流程"语义但可观测。

### 方案 B（架构层，后续）

Service 层引入事务单元抽象（`_metadata_owner.transaction()`），define/update/rollback
等跨方法操作收敛到一个事务，消除 commit 责任不清。这同时能修复之前
`update_action_type` 写入后 model_validate 失败导致数据损坏的 pre-existing 风险
（写入与校验在同一事务，校验失败则整体回滚）。

### 一次性数据修复

已手动给 `delayFlight` 补了 v1/v2 快照。修复代码上线后，对历史 ActionType
执行一次回填：遍历所有 ActionType，为缺失的当前 version 补一条快照。

---

## 验收标准

- [x] define ActionType 后 `GET .../versions` 返回 1 条（v1）
- [x] update ActionType 后 `GET .../versions` 返回 N 条（v1..vN）
- [x] rollback 后 `GET .../versions` 返回 N+1 条（回滚本身作为新版本审计）
- [x] 快照写入失败时日志可见（不再静默）
- [x] 现有 action 单测 + 集成测试全过
- [x] 新增单测：`test_define_action_type_publishes_v1_snapshot` / `test_update_snapshot_failure_rolls_back_whole_transaction`

---

## 备注

- 前端「版本历史」抽屉（P1）已正确实现，后端修复后前端自动可用（无需改前端）。
- `rollback_action_type` 也调 `_publish_version_snapshot`，同样修复（回滚后产生新快照）。
- 事务单元模式（`transaction()` + `auto_commit`）可推广到其他需要原子多步操作的 service
  （如 `define_object_type_batch` 的属性+关系+动作同事务），消除 commit 责任不清。
