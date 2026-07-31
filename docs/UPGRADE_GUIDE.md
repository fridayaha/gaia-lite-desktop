# UnionAgents (知行) 升级指导

> 本文档适用于已通过离线安装包部署 UnionAgents 的环境，指导如何通过升级包完成版本升级。

## 一、升级前准备

### 1. 确认当前版本

```bash
# 查看当前运行的镜像版本
k3s kubectl get deployments -n unionagents -o jsonpath='{range .items[*]}{.metadata.name}: {.spec.template.spec.containers[*].image}{"\n"}{end}'

# 查看管理后台版本（登录后右上角或页面底部）
```

### 2. 确认升级包

升级包命名规则：`unionagents-upgrade-<基线tag>-to-<目标版本>-<架构>.tar.gz`

```
unionagents-upgrade-v20260709-to-0.8.98-arm64.tar.gz   # ARM 环境
unionagents-upgrade-v20260709-to-0.8.98-x86.tar.gz     # X86 环境
```

解压查看内容：

```bash
tar -xzf unionagents-upgrade-v20260709-to-0.8.98-arm64.tar.gz
cd unionagents-upgrade-v20260709-to-0.8.98-arm64

ls -la
# images/          — 变更的 Docker 镜像 (.tar.gz)
# migrations/      — 数据库迁移脚本 (.sql)
# upgrade.sh       — 自动升级脚本
# CHANGELOG.txt    — 变更记录
# IMAGES.txt       — 镜像清单
# VERSION          — 版本信息
# RELEASE_NOTES.md — 版本说明
```

### 3. 确认架构匹配

```bash
# 确认服务器架构与升级包架构一致
uname -m
# aarch64 / arm64 → 使用 arm64 包
# x86_64          → 使用 x86 包
```

### 4. 备份（强烈建议）

```bash
# 备份数据库
PG_POD=$(k3s kubectl get pod -l app=postgres -n unionagents -o jsonpath='{.items[0].metadata.name}')
k3s kubectl exec -n unionagents $PG_POD -- pg_dump -U unionagents unionagents > backup_$(date +%Y%m%d).sql

# 备份 MinIO 数据（可选，按需）
# k3s kubectl exec -n unionagents <minio-pod> -- mc mirror /data /backup/...
```

## 二、执行升级

### 方式一：一键升级（推荐）

```bash
cd unionagents-upgrade-v20260709-to-0.8.98-arm64
bash upgrade.sh
```

升级脚本会自动完成以下步骤：

1. 导入变更的容器镜像到 k3s
2. 执行数据库迁移脚本（如有）
3. 更新 K8s Deployment 镜像标签
4. 等待 Rollout 完成
5. 输出最终 Pod 状态

### 方式二：手动逐步升级

如需手动控制升级过程，按以下步骤操作：

#### 步骤 1：导入镜像

```bash
K3S_IMAGES_DIR="/var/lib/rancher/k3s/agent/images"
mkdir -p "$K3S_IMAGES_DIR"

for img_gz in images/*.tar.gz; do
    name=$(basename "$img_gz")
    echo "导入 $name ..."
    gunzip -c "$img_gz" > "$K3S_IMAGES_DIR/${name%.gz}"
    k3s ctr -n k8s.io images import "$K3S_IMAGES_DIR/${name%.gz}"
    rm -f "$K3S_IMAGES_DIR/${name%.gz}"
done
```

#### 步骤 2：执行数据库迁移

```bash
PG_POD=$(k3s kubectl get pod -l app=postgres -n unionagents -o jsonpath='{.items[0].metadata.name}')

for sql in migrations/*.sql; do
    [ -f "$sql" ] || continue
    echo "执行 $(basename "$sql") ..."
    k3s kubectl exec -n unionagents $PG_POD -- \
        psql -U unionagents -d unionagents -f - < "$sql"
done
```

#### 步骤 3：更新镜像标签并重启

```bash
# 逐个更新 Deployment 镜像（根据 IMAGES.txt 中的清单）
# 示例：更新 manager
k3s kubectl set image deployment/manager manager=unionagents/manager:0.8.98 -n unionagents

# 示例：更新 console-admin
k3s kubectl set image deployment/console-admin console-admin=unionagents/console-admin:0.8.98 -n unionagents

# 等待 rollout 完成
k3s kubectl rollout status deployment/manager -n unionagents --timeout=180s
k3s kubectl rollout status deployment/console-admin -n unionagents --timeout=180s
```

#### 步骤 4：验证

```bash
# 查看 Pod 状态
k3s kubectl get pods -n unionagents
k3s kubectl get pods -n unionagents-hub

# 查看服务状态
k3s kubectl get svc -n unionagents

# 验证管理后台可访问
curl -s http://localhost:3000 | head -5
```

## 三、升级后验证

### 1. 功能验证清单

- [ ] 管理后台 (http://<IP>:3000) 可正常登录
- [ ] 用户门户 (http://<IP>:3001) 可正常访问
- [ ] 智能体列表正常显示
- [ ] 创建/编辑智能体功能正常
- [ ] 对话功能正常（SSE 流式）
- [ ] 日志监控页面正常

### 2. 查看升级日志

```bash
# 查看 manager 日志
k3s kubectl logs -n unionagents deployment/manager --tail=50

# 查看 controller 日志
k3s kubectl logs -n unionagents deployment/controller --tail=50

# 查看前端 nginx 日志
k3s kubectl logs -n unionagents deployment/console-admin --tail=20
```

## 四、回滚

如升级后出现问题，可回滚到之前的版本：

```bash
# 回滚 Deployment（会恢复到上一个镜像版本）
k3s kubectl rollout undo deployment/manager -n unionagents
k3s kubectl rollout undo deployment/console-admin -n unionagents
k3s kubectl rollout undo deployment/controller -n unionagents
# ... 其他服务同理

# 如需回滚数据库迁移，需手动执行反向 SQL
# 参考 migrations/ 目录下的脚本，编写对应的回滚 SQL
```

## 五、常见问题

### Q: 升级后 Pod 一直 ImagePullBackOff

镜像未正确导入。手动导入：

```bash
k3s kubectl describe pod <pod-name> -n unionagents | grep -A5 "Events:"
# 查看缺失的镜像名，然后手动导入
k3s ctr -n k8s.io images import images/<image>.tar.gz
```

### Q: 数据库迁移失败

```bash
# 查看迁移脚本报错
k3s kubectl exec -n unionagents $PG_POD -- psql -U unionagents -d unionagents -f - < migrations/<failed>.sql

# 如果是 "already exists" 类错误（迁移已部分执行过），可跳过
```

### Q: 升级后前端页面白屏

```bash
# 检查前端 Pod 是否正常
k3s kubectl get pods -n unionagents -l app=console-admin
# 检查镜像版本是否正确
k3s kubectl get deployment console-admin -n unionagents -o jsonpath='{.spec.template.spec.containers[*].image}'
```

### Q: 升级后引擎无法启动

引擎镜像由 Manager 动态管理。升级 engine-hermes 镜像后，需在管理后台重启引擎实例，或等待 Manager 自动 reconcile。
