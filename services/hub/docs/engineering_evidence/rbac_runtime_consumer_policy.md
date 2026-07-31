# Runtime Consumer 权限（RBAC-4）

## 1. 功能名称

RBAC-4：Runtime Consumer role/scope 入口权限 + ScopedCapabilityAccessPolicy 资产级过滤

## 2. 对应能力方向

权限审批治理 / Runtime Discover

## 3. 解决的问题

Runtime API 此前默认 AllowAll，任何调用方（含无认证）均可 discover/resolve。收口后 Runtime API 具备独立的两层权限：入口级 role/scope 检查 + 资产级可见性策略过滤，与管理态 RBAC 完全分离。

## 4. 设计要点

- **两层分离**：入口级 `require_runtime_permission`（role + scope）与资产级 `ScopedCapabilityAccessPolicy` 各司其职
- **Scope 映射**：discover → `capability:discover`，resolve → `capability:resolve`，manifest → `capability:manifest`（兼容 `capability:resolve`），tool-definition → `capability:tool_definition`（兼容 `capability:resolve`）
- **platform_admin 豁免 scope**，`HUB_AUTH_MODE=none` 绕过所有检查
- **Policy deny 行为**：discover 静默排除，resolve/manifest/tool-definition → 404
- **P1 不做 workspace DB 过滤**

## 5. 关键实现

| 模块 | 说明 |
|------|------|
| `backend/app/core/runtime_auth.py` | `require_runtime_permission(scope, fallback_scopes)` — FastAPI Depends |
| `backend/app/policies/capability_access.py` | `ScopedCapabilityAccessPolicy` — `can_discover` / `can_resolve` |
| `backend/app/api/runtime.py` | 4 个 Runtime 端点接入 `require_runtime_permission`，默认 policy 切换为 Scoped |

## 6. API / 使用方式

```
# dev mode（无 Header → dev-admin=platform_admin）:
GET /api/runtime/capabilities/discover → 200

# header mode + runtime_consumer + correct scope:
curl -H "X-Actor-ID: svc1" -H "X-Roles: runtime_consumer" \
     -H "X-Scopes: capability:discover" \
     /api/runtime/capabilities/discover → 200

# header mode + contributor（无 runtime role）:
curl -H "X-Actor-ID: u1" -H "X-Roles: contributor" \
     /api/runtime/capabilities/discover → 403
```

## 7. 测试与结果

- 新增测试数：24
- 总测试数：509
- 通过率：509 passed，0 failed
- 手工验证：dev mode / header mode / none mode 覆盖

## 8. 日志 / 审计 / 安全边界

- access log：✅ 记录 actor_id
- 事件日志：✅ runtime.discover/resolve/tool_definition 已带 actor_id
- 权限检查：✅ require_runtime_permission（入口级） + ScopedCapabilityAccessPolicy（资产级）
- actor_id：✅ 由 AuthMiddleware / Header 注入

## 9. 演示路径

```bash
# 1. dev mode 下 discover（无 Header，platform_admin）
curl http://localhost:8000/api/runtime/capabilities/discover

# 2. header mode 下 runtime_consumer + scope discover
curl -H "X-Actor-ID: svc1" \
     -H "X-Roles: runtime_consumer" \
     -H "X-Scopes: capability:discover" \
     http://localhost:8000/api/runtime/capabilities/discover

# 3. header mode 下 contributor 被拒绝
curl -H "X-Actor-ID: u1" \
     -H "X-Roles: contributor" \
     http://localhost:8000/api/runtime/capabilities/discover
```

## 10. 可量化结果

509 tests passed，24 新增 RBAC-4 测试覆盖入口 role/scope + 资产级 policy deny。

## 11. 一句话价值总结

将 Runtime API 从"开放 allow-all"升级为"两层独立权限管控"，管理态 RBAC 与 Runtime 权限彻底分离，为生产部署的 Runtime Consumer 接入奠定基础。
