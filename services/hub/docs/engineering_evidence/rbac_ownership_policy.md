# 对象级 Ownership 策略（RBAC-3D-2）

## 1. 功能名称

RBAC-3D-2：对象级 ownership 策略实现

## 2. 对应能力方向

权限审批治理

## 3. 解决的问题

管理态 RBAC 此前仅有角色级权限（如 `asset_owner` 可操作所有资产），无法区分 own/other 资产。实现后 `asset_owner` 只能操作 `created_by == ctx.actor_id` 的资产，非 owner 操作返回 403。

## 4. 设计要点

- P1 复用 `created_by` 作为 owner 来源（`created_by` 已在 RBAC-3D-1 修复为 AuthContext.actor_id 优先）
- 单独 `OwnershipPolicy` 文件，与角色级 RBAC 分层
- platform_admin 豁免所有 ownership 检查
- 历史数据（`created_by=None` / `unknown`）fail-open，记录 `ownership.missing_owner` 事件
- Runtime API 不受 management ownership 影响

## 5. 关键实现

| 模块 | 说明 |
|------|------|
| `backend/app/policies/ownership_policy.py` | `require_asset_ownership` / `require_asset_ownership_from_version` / `check_relation_*_ownership` |
| API handlers | 12 个管理态端点接入 ownership 检查 |

## 6. 受影响 API

| API | 权限 | ownership |
|-----|------|:---:|
| `PUT /api/hub/items/{id}` | `asset:update` | ✅ |
| `GET /api/hub/items/{id}/versions` | `asset:read` | ✅ |
| `POST /api/hub/items/{id}/versions` | `version:create` | ✅ |
| `GET /api/hub/versions/{id}` | `asset:read` | ✅ |
| `POST /api/hub/items/{id}/submit` | `review:submit` | ✅ |
| `POST /api/hub/versions/{id}/submit-review` | `review:submit` | ✅ |
| `POST /api/hub/versions/{id}/approve` | `review:approve` | ✅ |
| `POST /api/hub/versions/{id}/reject` | `review:reject` | ✅ |
| `POST /api/hub/versions/{id}/request-change` | `review:request_change` | ✅ |
| `POST /api/hub/versions/{id}/publish` | `lifecycle:publish` | ✅ |
| `POST /api/hub/items/{id}/disable` | `lifecycle:disable` | ✅ |
| `POST /api/hub/items/{id}/rollback` | `lifecycle:rollback` | ✅ |

## 7. 测试与结果

- 新增测试数：13
- 总测试数：471 passed（RBAC-3D-2 完成时）
- 日志事件：`ownership.missing_owner`（fail-open）+ `ownership.policy_denied`（denied）

## 8. 边界

未实现：
- `owner_id` 独立字段（P1 复用 `created_by`）
- owner 转移
- group ownership
- workspace ownership

## 9. 一句话价值总结

将管理态 RBAC 从角色级全局权限升级为对象级权限，资产所有者只能操作自有资产，填补了 PoC 阶段的最大安全缺口。
