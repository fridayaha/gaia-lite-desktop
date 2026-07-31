# 贡献指南 — UnionAgents (知行)

## 如何提交 PR

### 1. Fork 仓库

1. 打开 [https://gitcode.com/Ascend-SACT/union_agent](https://gitcode.com/Ascend-SACT/union_agent)
2. 点击右上角 **Fork**，Fork 到你的个人命名空间
3. 克隆你的 Fork 到本地：

```bash
git clone https://gitcode.com/<你的用户名>/union_agent.git
cd union_agent
```

### 2. 添加上游 remote

```bash
git remote add upstream https://gitcode.com/Ascend-SACT/union_agent.git
git remote -v
# origin    https://gitcode.com/<你>/union_agent.git (fetch)
# origin    https://gitcode.com/<你>/union_agent.git (push)
# upstream  https://gitcode.com/Ascend-SACT/union_agent.git (fetch)
```

### 3. 创建功能分支

```bash
# 从最新的 upstream main 创建
git fetch upstream
git checkout -b feat/your-feature-name upstream/main
```

分支命名规范：

| 前缀 | 用途 |
|------|------|
| `feat/` | 新功能 |
| `fix/` | Bug 修复 |
| `docs/` | 文档更新 |
| `refactor/` | 重构 |
| `ci/` | CI/CD 相关 |
| `chore/` | 杂项（依赖、构建等） |

> 前端（Admin）开发规范见 [frontend-guidelines.md](frontend-guidelines.md)，新增页面/样式/弹框/菜单/国际化均需遵循。

### 4. 开发 & 提交

```bash
# 本地开发
make fmt        # 代码格式化
make lint       # 代码检查
make test       # 运行测试

# 提交（约定式提交）
git add .
git commit -m "feat(scope): 简短的描述"

# 推送到你的 fork
git push origin feat/your-feature-name
```

Commit 格式：`<type>(<scope>): <description>`

| Type | 说明 |
|------|------|
| `feat` | 新功能 |
| `fix` | Bug 修复 |
| `docs` | 文档 |
| `refactor` | 重构 |
| `ci` | CI/CD |
| `chore` | 杂项 |

### 5. 创建 Pull Request

1. 打开你的 Fork 仓库页面
2. 点击 **Pull Request** → **New Pull Request**
3. 选择：
   - **base repository**: `Ascend-SACT/union_agent`
   - **base**: `main`
   - **head repository**: `<你的用户名>/union_agent`
   - **compare**: `feat/your-feature-name`
4. 填写 PR 模板（见下方）
5. 提交 PR

### PR 模板

```markdown
## 描述

请简要描述此 PR 的内容和目的。

## 关联 Issue

Fixes #(issue-number)

## 变更类型

- [ ] Bug 修复
- [ ] 新功能
- [ ] 文档更新
- [ ] 重构
- [ ] CI/CD 配置

## 测试验证

- [ ] 本地测试通过 (`make test`)
- [ ] 代码格式化 (`make fmt`)
- [ ] Lint 检查通过 (`make lint`)

## 部署影响

- [ ] 需要数据库迁移
- [ ] 需要更新配置
- [ ] 需要重启服务
- [ ] 无部署影响
```

### 提交后

- PR 提交后会自动触发 CI 流水线
- 等待 CI 通过后，管理员会进行 Code Review
- 如果 CI 失败，直接在本地修复后 push 到同一分支即可

---

## CI 流水线说明

### 触发方式

| 触发条件 | 行为 |
|---------|------|
| `git tag v*` push | 全量构建 + 推送镜像 + 打包离线包 |
| `main` 分支 push | 构建 latest 镜像 |
| PR 创建/更新 | 运行 lint + test |

### 流水线产物

tag push 后，CI 会产出两个产物：

1. **Docker 镜像** — 推到容器镜像仓库
2. **离线部署包** — 在 Release 页下载 `unionagents-offline-<版本>.tar.gz`

### 离线包结构

```
unionagents-offline-v1.0.0/
├── VERSION                     # 版本号
├── install-offline.sh          # 一键部署脚本（离线 ECS 使用）
├── images/                     # Docker 镜像 tar.gz
│   ├── manager.tar.gz
│   ├── gateway.tar.gz
│   ├── hub.tar.gz
│   ├── litellm.tar.gz
│   ├── engine-hermes.tar.gz
│   ├── console-admin.tar.gz
│   ├── enduser-portal.tar.gz
│   ├── postgres-16-alpine.tar.gz
│   └── minio-minio-latest.tar.gz
└── manifests/                  # K8s 部署清单
    ├── 00-namespace.yaml
    ├── infra/
    │   ├── 10-secret.yaml
    │   ├── 20-postgres.yaml
    │   ├── 30-minio.yaml
    │   └── 40-manager-rbac.yaml
    ├── services/
    │   ├── 10-manager.yaml
    │   ├── 20-gateway.yaml
    │   ├── 30-gateway-callback.yaml
    │   ├── 40-litellm.yaml
    │   └── 50-hub.yaml
    └── apps/
        ├── 10-admin.yaml
        └── 20-enduser-portal.yaml
```
