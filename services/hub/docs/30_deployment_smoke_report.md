# 能力市场部署验证报告 (Deployment Smoke Report)

## 1. 环境信息
- **管理后台服务器**: http://124.243.186.4:30839 (验证使用 localhost:8003 直连)
- **用户端服务器**: http://124.243.186.4:32744 (验证使用 localhost:8003 直连)
- **项目路径**: `/home/xiaox/projects/union_agent/services/union-agent-hub`
- **服务端口**: 8003
- **Python 版本**: 3.11.x
- **DB 类型**: SQLite (hub_smoke.db) - 用于烟雾验证
- **Storage Backend**: local (`.hub_storage`)
- **Auth Mode**: `dev` (验证通过后可切换为 `header`)
- **Scanner Enabled**: 基础 RuleScanner 启用, External Scanner 禁用 (烟雾验证)

## 2. 启动方式
- **命令**: `uvicorn app.main:app --host 0.0.0.0 --port 8003 --app-dir backend`
- **环境变量**:
  ```bash
  HUB_AUTH_MODE=dev
  HUB_STORAGE_BACKEND=local
  HUB_STORAGE_LOCAL_ROOT=.hub_storage
  DATABASE_URL=sqlite:///./hub_smoke.db
  ```

## 3. 管理态 API 验证结果
| 功能点 | 结果 | 备注 |
| :--- | :--- | :--- |
| Health Check | PASS | `/api/health` 返回 `ok` |
| Presets Init | PASS | 成功创建 4 项预置资产 |
| OpenAPI Import | PASS | 成功导入 PetStore 示例 |
| Submit Review | PASS | 状态流转: draft -> submitted |
| Approve | PASS | 状态流转: submitted -> approved |
| Publish | PASS | 状态流转: approved -> published |
| Scan Report | PASS | 生成低风险报告 (0 findings) |

## 4. Runtime API 验证结果
| 功能点 | 结果 | 备注 |
| :--- | :--- | :--- |
| Discover | PASS | 成功发现已发布的 tool |
| Resolve | PASS | 成功获取完整 Manifest |
| RBAC (dev) | PASS | 默认允许访问 |
| Tenant Context | PASS | Context 注入正常 |

## 5. 存储与安全自检
- **LocalStorage**: `.hub_storage/imports` 目录已生成并包含导入原始文件。
- **Git Status**: 
  - `hub_smoke.db` (EXCLUDED)
  - `.hub_storage` (EXCLUDED)
  - `.venv` (EXCLUDED)
- **Sensitive Info**: 经 `grep` 扫描，无真实 API Key 或 Secret 泄露风险。

## 6. 结论
- **结论**: 能力市场 Hub 服务核心链路已跑通，可以进入网关 (Gateway) 联调阶段。
- **待办项**:
  - 适配管理后台的真实 `X-Actor-ID` / `X-Organization-ID` Header。
  - 正式环境切换为 PostgreSQL。
  - 配置正式的对象存储 (S3) 后端。

---
*Verified by Hermes Agent at 2026-06-15*
