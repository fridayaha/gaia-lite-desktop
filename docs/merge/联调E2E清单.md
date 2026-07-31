# 融合联调 E2E 清单（k3s 开发环境执行）

> 集成已完成并推到 `origin/develop`（tag `merge-integrated-v3`，commit `098a9c9`）。
> 本清单是 k3s 全链路 E2E，需在带 colima/k3s + Hermes 引擎的开发机执行。
> 本地已验证：manager 231 / gateway 221 / hub 815 单测绿；admin+enduser typecheck+build 绿；三服务 boot smoke `/health` 200。

## 0. 前置：重新部署合并代码 + 补 secret

```bash
# 0.1 补 k3s secret（A 报告：unionagents-secret 缺 litellm-master-key/litellm-salt-key）
make k8s-infra            # 重新 apply deploy/k8s/infra/，含 secret.yaml

# 0.2 构建并部署合并后的三服务镜像（替换运行中的旧 pod）
make docker-build         # 或各自 Dockerfile.local 宿主预构建 dist
make k8s-services         # apply deploy/k8s/services/（manager/controller 并入 manager、gateway、hub）
kubectl -n unionagents rollout restart deploy/manager deploy/gateway deploy/hub
kubectl -n unionagents get pods -w   # 等三服务 Running

# 0.3 port-forward 本地访问（或直连 Ingress）
make pf-all
```

预期：manager(8002)/gateway(8010)/hub(8003) 三 pod Running；`/health` 三服务均 200。

## 1. 基座三服务 /health + seed 幂等
```bash
curl -s localhost:8002/health   # {"status":"ok","service":"unionagents-manager"}
curl -s localhost:8010/health   # {"status":"ok","service":"unionagents-gateway",...}
curl -s localhost:8003/api/hub/health  # {"status":"ok"}
# seed 幂等：重启 manager pod 两次，permissions/roles 行数不变
kubectl -n unionagents exec deploy/manager -- python -c "import asyncio;from app.core.seed import seed_roles;from app.core.database import async_session;..." # 或观察启动日志无新增
```

## 2. 三层链路 + deploy SSE（A+C）
Admin（:8848 或 console-admin）：建智能体定义 → 发布版本 → 建资源池 → 建实例 → 上线 → 触发 deploy。
- 预期：实例详情页 deploy/events SSE 进度面板可见（步骤/百分比/完成）；`kubectl get pods` 出现 `engine-hermes-{id[:8]}` Pod 并 Running。
- **核对项**：deploy/events 事件 schema（C1 用 `{type,step,status,progress,message}`，A 转发 worker 原样输出）——若面板不渲染，A/C 对齐 schema。

## 3. chat 代理三引擎（A+B+C）
Enduser（:3000）：选 Hermes 实例发消息 → 流式回复。
- Hermes：`/v1/chat/completions` 流式回。
- Dify：切到 Dify 实例，验证 OpenAI↔Dify 请求体/SSE 转换（DifyAdapter）。
- OpenClaw：同 Hermes（OpenAI 兼容）。
- 预期：三引擎前端无感知差异；gateway adapter 按 `X-Engine-Type` 分派。

## 4. 生命周期 suspend/resume/destroy（A）
Admin 实例详情：suspend → 等 30min（或手动触发）→ MinIO 存档 → resume 恢复 → destroy 归档。
- 预期：suspend 后 Pod scale=0、MinIO `groups/{group_code}/backups/{agent_id}/latest.tar.gz` 存在；resume 后数据恢复；destroy 后 `archives/{agent_id}/{ts}.tar.gz` 存在、K8s 资源清理。
- dashboard 指标可见（1h/6h/24h/7d 聚合）。

## 5. IM 三渠道（B）
企微/飞书/钉钉回调 → 权限闸门 → 流式回复。
- 飞书：PATCH 卡片实时编辑；企微：2048 字节分段；钉钉：OAuth + HMAC。
- 越权用户：返回明确 IM 提示（不吞 AccessDenied）。
- 回调路径：`POST /api/gateway/channel/{channel_type}/{agent_id}/callback`。

## 6. hub 全链路 + 组隔离（B+C）
Admin hub 管理页：导入 → 审批（pending_review→approved→published）→ 安全扫描（scan_report/finding）→ 发布 → 发现。
- 预期：hub_item 版本/审批/扫描全链路通；组隔离（跨组不可见）。
- API：`/api/hub/items`、`/api/hub/versions/{id}/approve`、`/api/hub/imports/package` 等。

## 7. 知识库占位页
Admin /knowledge：占位页可访问（本次不做真实功能）。

## 8. 审批工作流（C 回退 + B catch-all + Hermes）
Enduser 选 Hermes 实例，触发需审批操作（如执行命令类技能）。
- 预期：流内 `approval.request` 事件 → ApprovalCard 弹出 → 选 once/session/always/deny → `POST /v1/runs/{id}/approval` → 流内 `approval.responded` → 继续执行。
- 中断恢复：审批 pending 时刷新页面 → `resumePendingRuns` 经 `GET /v1/runs/{id}` 查状态为 `waiting_for_approval` → 重开 `/v1/runs/{id}/events` 流 → ApprovalCard 恢复。
- **关键**：B gateway 不需新增端点（catch-all 透传 /v1/runs/* 到 Hermes）；若 404，检查 gateway catch-all 是否生效 + Hermes 引擎是否支持 /v1/runs。

## 9. V1→V3 数据迁移演练（A）
```bash
# 对 V1 旧库（Repo1 main 的 V1 schema）跑迁移脚本（dry-run 先行）
python scripts/migrate_v1_to_v3.py --dry-run
python scripts/migrate_v1_to_v3.py
```
- 预期：Agent→Definition+Version+Instance、AgentChannel→AgentInstanceChannel、ApiKey→LiteLLM virtual key 映射正确；幂等（跑两次结果一致）；数据一致性核对（定义/版本/实例数对齐）。

## 10. 特性核对
逐项打勾 `docs/features/full-feature-catalog.md` 147 项 + Repo1 细化（hub/Dify/审批/adapter/端体验）。

## 联调中发现问题的处理
- 契约类（字段/路径/schema）：改 `docs/merge/01-接口契约.md` + 通知相关团队，再改代码。
- 代码类：直接在 develop 上修，commit message 带 `联调修复`。
- 重大问题：回退到 `merge-integrated-v3` tag，修复后重打 tag。

## 已知待 E2E 核对项（联调中重点确认）
1. deploy/events SSE schema（A 转发 vs C 面板期望）——§2。
2. role code/name（A 返回 role.name vs C welcome 中文名分支）——§1/§2 登录后。
3. 审批 /v1/runs 端到端（C 回退 + B catch-all + Hermes 原生）——§8。
4. k3s secret 补齐（litellm-master-key/litellm-salt-key）——§0.1。
