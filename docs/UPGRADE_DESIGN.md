# UnionAgents (知行) 升级方案设计文档

> 本文档描述 UnionAgents 的离线升级机制，包括升级包结构、打包流程、升级执行流程、版本基线管理及回滚策略。

## 一、设计背景

UnionAgent 部署在完全离线的 ECS 环境（EulerOS 2.0 / CentOS 7+），无法使用在线镜像仓库或 Helm 远程更新。升级需要通过离线升级包完成：在构建机（有网络）上打包变更的镜像和迁移脚本，传到目标 ECS 上执行升级。

## 二、版本基线

### 2.1 基线 Tag

当前升级基线为 `v20260709`（对应 VERSION `0.8.84`）。

- **基线 Tag 的含义**：安装包基于此 Tag 构建，升级包从此 Tag 到当前版本计算增量。
- **Tag 命名规则**：`v` + 日期（`YYYYMMDD`），如 `v20260709`。
- **何时更新基线**：当发生大版本升级或安装包重新发布时，打新的日期 Tag 作为新基线。

### 2.2 版本号

- 仓库 `VERSION` 文件存裸版本号（如 `0.8.98`），遵循 SemVer。
- 所有服务镜像统一使用同一版本号 tag。
- 修改版本号使用 `scripts/bump-version.sh <版本号>`，同步更新所有引用点。

### 2.3 当前状态

| 项目       | 值          |
| ---------- | ----------- |
| 基线 Tag   | v20260709   |
| 基线版本   | 0.8.84      |
| 当前版本   | 0.8.98      |
| 变更提交数 | 53 commits  |
| 变更文件数 | 510 files   |
| 新增迁移   | 5 个 SQL    |

## 三、升级包设计

### 3.1 升级包结构

```
unionagents-upgrade-v20260709-to-0.8.98-arm64/
├── images/              # 变更的 Docker 镜像 (.tar.gz)
│   ├── manager.tar.gz
│   ├── console-admin.tar.gz
│   └── ...
├── migrations/          # 数据库迁移脚本 (.sql)
│   ├── 013_community_articles.sql
│   ├── 014_engine_rollout.sql
│   └── ...
├── upgrade.sh           # 自动升级执行脚本
├── IMAGES.txt           # 镜像清单（名称 tag）
├── VERSION              # 版本信息（FROM_TAG, TO_VERSION, ARCH, BUILD_DATE, GIT_COMMIT）
├── CHANGELOG.txt        # Git 提交记录摘要
├── RELEASE_NOTES.md     # 版本发布说明
└── UPGRADE_GUIDE.md     # 升级操作指导
```

### 3.2 增量镜像检测

升级包不包含全部镜像，只打包自基线 Tag 以来发生变化的镜像。检测逻辑：

```bash
# 对每个服务目录执行 git diff 检测
git diff --quiet ${FROM_TAG}..HEAD -- services/manager/  # manager
git diff --quiet ${FROM_TAG}..HEAD -- services/controller/  # controller
git diff --quiet ${FROM_TAG}..HEAD -- engines/hermes/  # engine-hermes
git diff --quiet ${FROM_TAG}..HEAD -- services/hub/  # hub
git diff --quiet ${FROM_TAG}..HEAD -- services/channel-gateway/  # channel-gateway
git diff --quiet ${FROM_TAG}..HEAD -- services/llm-gateway/  # llm-gateway
git diff --quiet ${FROM_TAG}..HEAD -- apps/admin/ apps/docs/  # console-admin
git diff --quiet ${FROM_TAG}..HEAD -- apps/enduser/  # console-enduser
```

特殊规则：
- `enduser-portal`（nginx 反代）随 `console-enduser` 变更而变。
- `engine-hermes` 镜像打包但升级时跳过 rollout（由 Manager 动态管理引擎 Pod）。

### 3.3 多架构支持

升级包按架构分别构建：

| 架构         | ARCH_TAG | 构建机器           |
| ------------ | -------- | ------------------ |
| aarch64/arm64 | arm64    | ARM 构建机（本机） |
| x86_64/amd64 | x86      | X86 构建机（SSH）  |

打包脚本 `package-upgrade.sh` 在当前机器上构建本架构镜像，`daily_build_archive.sh` 通过 SSH 在 X86 机器上远程构建。

### 3.4 前端预编译

前端（admin/enduser/docs）在打包前预编译：

1. 将源码 rsync 到 `/tmp`（原生 ext4 文件系统，避免 overlayfs 导致的 pnpm build 失败）。
2. 执行 `pnpm install` + `pnpm build`。
3. 将 `dist/` 目录复制回源码目录。
4. 使用 `Dockerfile.prebuilt`（跳过前端构建阶段）构建 Docker 镜像。

## 四、升级执行流程

### 4.1 升级脚本（upgrade-offline.sh）

升级脚本按 5 个步骤执行：

```
[0/5] 检查 k3s 环境
      - 验证 k3s 已安装
      - 验证 kubeconfig 存在
      - 验证节点就绪

[1/5] 导入容器镜像
      - 解压 .tar.gz 到 k3s 镜像目录
      - 通过 k3s ctr images import 导入
      - 清理临时 .tar 文件

[2/5] 执行数据库迁移
      - 找到 PostgreSQL Pod
      - 按文件名顺序执行 migrations/*.sql
      - 通过 kubectl exec + psql -f 执行

[3/5] 更新 K8s 部署镜像标签
      - 读取 IMAGES.txt 镜像清单
      - 对每个镜像执行 kubectl set image
      - engine-hermes 跳过（Manager 动态管理）
      - llm-gateway 映射为 litellm-custom

[4/5] 等待 Rollout 完成
      - 对每个更新的 Deployment 执行 kubectl rollout status
      - 超时时间 180s

[5/5] 输出状态
      - 显示主命名空间 Pod 状态
      - 显示 Hub 命名空间 Pod 状态
```

### 4.2 镜像名称映射

| 镜像名           | Deployment 名    | 命名空间          |
| ---------------- | ---------------- | ----------------- |
| manager          | manager          | unionagents       |
| controller       | controller       | unionagents       |
| hub              | hub              | unionagents       |
| console-admin    | console-admin    | unionagents       |
| console-enduser  | console-enduser  | unionagents       |
| enduser-portal   | enduser-portal   | unionagents       |
| llm-gateway      | llm-gateway      | unionagents       |
| engine-hermes    | （跳过 rollout） | Manager 动态管理   |
| console-admin-hub| console-admin-hub| unionagents-hub   |

### 4.3 命名空间

- `unionagents` — 主命名空间（manager, gateway, hub, postgres, minio, 前端等）
- `unionagents-hub` — Hub 命名空间（console-admin-hub）

## 五、每日构建归档

### 5.1 流程

`daily_build_archive.sh` 每日自动执行：

1. 在 ARM 机器上构建安装包 + 升级包（arm64）
2. SSH 到 X86 机器构建安装包 + 升级包（x86）
3. 归档到统一的 `builds/YYYYMMDD/` 目录
4. 生成 Release Notes
5. 同步到镜像仓库

### 5.2 归档目录结构

```
builds/20260714/
├── install/
│   ├── unionagents-offline-0.8.98-arm64.tar.gz
│   └── unionagents-offline-0.8.98-x86.tar.gz
├── upgrade/
│   ├── unionagents-upgrade-v20260709-to-0.8.98-arm64.tar.gz
│   └── unionagents-upgrade-v20260709-to-0.8.98-x86.tar.gz
└── release-notes.md
```

### 5.3 Crontab 配置

```cron
# 每日构建归档（02:00）
0 2 * * * /root/union_agent/scripts/daily_build_archive.sh >> /root/union_agent/logs/cron-daily-archive-$(date +\%Y\%m\%d).log 2>&1

# 每日构建验证（23:00）
0 23 * * * /root/union_agent/scripts/daily_build_verify.sh >> /root/union_agent/logs/daily-build-$(date +\%Y\%m\%d).log 2>&1
```

## 六、数据库迁移

### 6.1 迁移文件管理

- 迁移文件位于 `services/manager/migrations/`
- 命名规则：`序号_描述.sql`（如 `013_community_articles.sql`）
- 升级包只包含基线 Tag 之后新增的迁移文件（`git diff --diff-filter=A`）

### 6.2 当前迁移清单（v20260709 → 0.8.98）

| 序号 | 文件名                              | 说明                    |
| ---- | ----------------------------------- | ----------------------- |
| 013  | 013_community_articles.sql          | 社区文章表              |
| 014a | 014_engine_rollout.sql              | 引擎灰度发布配置        |
| 014b | 014_user_avatar_url.sql             | 用户头像 URL 字段       |
| 015a | 015_avatar_url_to_relative_path.sql | 头像路径转相对路径      |
| 015b | 015_operation_log_user_agent.sql    | 操作日志 User-Agent 字段 |

### 6.3 迁移执行

迁移通过 PostgreSQL Pod 直接执行：

```bash
PG_POD=$(k3s kubectl get pod -l app=postgres -n unionagents -o jsonpath='{.items[0].metadata.name}')
k3s kubectl exec -n unionagents $PG_POD -- psql -U unionagents -d unionagents -f - < migrations/XXX.sql
```

迁移脚本设计为幂等的（使用 `IF NOT EXISTS` / `ADD COLUMN IF NOT EXISTS`），重复执行不会报错。

## 七、回滚策略

### 7.1 镜像回滚

```bash
# K8s Deployment 回滚到上一版本
k3s kubectl rollout undo deployment/manager -n unionagents
k3s kubectl rollout undo deployment/console-admin -n unionagents
```

### 7.2 数据库回滚

数据库迁移不可自动回滚。如需回滚：

1. 恢复升级前备份的数据库
2. 或手动编写反向 SQL 执行

### 7.3 备份建议

升级前强烈建议备份数据库：

```bash
PG_POD=$(k3s kubectl get pod -l app=postgres -n unionagents -o jsonpath='{.items[0].metadata.name}')
k3s kubectl exec -n unionagents $PG_POD -- pg_dump -U unionagents unionagents > backup_$(date +%Y%m%d).sql
```

## 八、脚本清单

| 脚本                     | 用途                           | 位置               |
| ------------------------ | ------------------------------ | ------------------ |
| package-upgrade.sh       | 打包升级包（增量镜像+迁移）    | scripts/           |
| upgrade-offline.sh       | 执行离线升级（包内脚本）       | scripts/ → 升级包内 |
| install-offline.sh       | 离线安装脚本                   | scripts/           |
| package-offline.sh       | 打包安装包                     | scripts/           |
| daily_build_archive.sh   | 每日构建归档（ARM+X86 双架构） | scripts/           |
| daily_build_verify.sh    | 每日构建验证（lint+docker build） | scripts/        |
| bump-version.sh          | 版本号同步                     | scripts/           |
| sync_to_registry.sh      | 镜像推送到容器仓库             | scripts/           |
| gen_release_notes.sh     | 生成发布说明                   | scripts/           |

## 九、升级操作指引

详见 [UPGRADE_GUIDE.md](./UPGRADE_GUIDE.md)。
