# UnionAgents 标准升级流程

> 本文档是 UnionAgents 全服务升级 SOP。环境信息配置好 + 准备工作就绪后，按步骤执行即可升级。
> 适用于 k3s 集群（101/141/124 等自建环境）。

---

## 一、环境信息（每环境填一次，后续升级复用）

升级前确认环境信息（以 141 为例，其他环境替换对应值）：

| 项 | 值 | 说明 |
|---|---|---|
| 环境名 | 141 客户测试（main） | |
| SSH 地址 | `mhero@101.96.216.141` 端口 2222 | `~/.ssh/config` 别名 `mhero-main` |
| SSH 免密 | ✅ 已配 ssh-pubkey | |
| sudo 免密 | ✅ 已配 NOPASSWD | `/etc/sudoers.d/mhero` |
| 代码目录 | `/home/mhero/union_agent` | develop 分支 |
| git remote | origin=Ascend-SACT（upstream） | `git fetch upstream` 拉最新 |
| docker | ✅ 已装 | 本机 build |
| k3s | ✅ 已装 | containerd（docker save \| k3s ctr import） |
| kubectl | ✅ 已装 | mhero 可用 |
| namespace | `unionagents` | |
| DB | postgres-0 pod，`unionagents` 库 | `postgresql://unionagents:${PG_PASSWORD}@postgres:5432/unionagents` |
| 方舟 ASR Key | `${ASR_VOLC_API_KEY}`（占位） | Secret `asr-volc-api-key` |

### 服务清单（升级前确认）

| 服务 | deploy 名 | 副本 | 说明 |
|---|---|---|---|
| gateway | gateway | x2 | v2 外部 ASR（撤 sidecar） |
| manager | manager | x2 | 含 DB ORM，升级前跑 migration |
| hub | hub | x2 | |
| engine-hermes | engine-hermes-{agent_id} | x1/agent | engine 镜像看是否有新版 |
| console-admin | console-admin | x2 | 前端 |
| enduser-portal | enduser-portal | x2 | 前端 |
| litellm | litellm | x1 | 基础设施，不升级 |
| minio | minio | x1 | 基础设施，不升级 |
| postgres | postgres | x1 | 基础设施，不升级 |

---

## 二、准备工作（一次性，首次升级前做完）

### 2.1 SSH 免密
```bash
# 本机公钥 → 目标环境 authorized_keys
ssh-copy-id -p 2222 mhero@101.96.216.141
# 或手动：cat ~/.ssh/id_rsa.pub | ssh -p 2222 mhero@... 'cat >> ~/.ssh/authorized_keys'

# ~/.ssh/config 加别名
Host mhero-main
    HostName 101.96.216.141
    Port 2222
    User mhero
    IdentityFile ~/.ssh/id_rsa
```

### 2.2 sudo 免密
```bash
# 在目标环境执行（需输一次密码）
echo "mhero ALL=(ALL) NOPASSWD: ALL" | sudo tee /etc/sudoers.d/mhero
sudo chmod 440 /etc/sudoers.d/mhero
# 验证：sudo -n true && echo OK
```

### 2.3 git remote 配置
```bash
cd /home/mhero/union_agent
git remote add upstream https://gitcode.com/Ascend-SACT/union_agent.git  # 如果没有
```

### 2.4 .env.local 配置（如果用 deploy.sh）
```bash
# deploy/ci/.env.local 填敏感配置（DB/JWT/OSS/ASR 等）
# 参考 deploy/ci/.env.local.example
ASR_VOLC_API_KEY=${ASR_VOLC_API_KEY}
# ... 其他 DB_USER/DB_PASSWORD/JWT_SECRET 等
```

---

## 三、升级步骤

### Step 0：确认 PR 已合入

确认要升级的代码已合入 upstream develop（gitcode.com/Ascend-SACT/union_agent develop 分支）。

### Step 1：DB 备份（必做！）

```bash
ssh mhero-main
BACKUP_FILE=/home/mhero/backup_$(date +%Y%m%d_%H%M).sql
kubectl exec postgres-0 -n unionagents -- pg_dump -U unionagents -d unionagents > $BACKUP_FILE
ls -lh $BACKUP_FILE  # 确认非空
echo "备份完成: $BACKUP_FILE"
```

### Step 2：拉最新代码

```bash
cd /home/mhero/union_agent
git fetch upstream
git merge --ff-only upstream/develop
git log --oneline -3  # 确认含目标 PR
```

### Step 3：跑 DB migration（manager 升级前）

```bash
# 查看有哪些 migration
ls services/manager/migrations/

# 逐个跑（幂等，重复无副作用）
for f in services/manager/migrations/*.sql; do
  echo "=== 跑 $f ==="
  kubectl exec -i postgres-0 -n unionagents -- psql -U unionagents -d unionagents < "$f"
done
```

### Step 4：build 镜像

```bash
cd /home/mhero/union_agent

# gateway（含 ASR provider + 提示优化）
DOCKER_BUILDKIT=0 docker build -t unionagents/gateway:latest -f services/gateway/Dockerfile .

# manager
DOCKER_BUILDKIT=0 docker build -t unionagents/manager:latest -f services/manager/Dockerfile .

# hub
DOCKER_BUILDKIT=0 docker build -t unionagents/hub:latest -f services/hub/Dockerfile .

# console-admin（前端）
DOCKER_BUILDKIT=1 docker build -f apps/admin/Dockerfile -t unionagents/console-admin:latest .

# enduser-portal（前端）
DOCKER_BUILDKIT=1 docker build -f apps/enduser/Dockerfile -t unionagents/enduser-portal:latest .

# engine-hermes（如果有新版，看 engines/ 目录有无 Dockerfile）
# DOCKER_BUILDKIT=0 docker build -t unionagents/engine-hermes-v2:latest -f engines/.../Dockerfile .
```

### Step 5：import 镜像到 k3s

```bash
for img in gateway manager hub console-admin enduser-portal; do
  echo "=== import $img ==="
  docker save unionagents/$img:latest | sudo k3s ctr images import --all-platforms -
done
```

### Step 6：配 ASR Secret

```bash
kubectl -n unionagents create secret generic unionagents-secret \
  --from-literal=asr-volc-api-key=${ASR_VOLC_API_KEY} \
  --dry-run=client -o yaml | kubectl apply -f -
```

### Step 7：按顺序升级服务

```bash
# 7.1 manager（先升级，DB schema 已迁移）
kubectl -n unionagents set image deploy/manager manager=unionagents/manager:latest
kubectl -n unionagents rollout status deploy/manager --timeout=180s

# 7.2 gateway（v2 架构：撤 sidecar + 外部 ASR）
#     apply base gateway.yaml（含 UA_ASR_PROVIDER + secretKeyRef，撤 sidecar）
kubectl -n unionagents apply -f deploy/k8s/services/gateway.yaml
kubectl -n unionagents set image deploy/gateway gateway=unionagents/gateway:latest
kubectl -n unionagents rollout status deploy/gateway --timeout=180s

# 7.3 hub
kubectl -n unionagents set image deploy/hub hub=unionagents/hub:latest
kubectl -n unionagents rollout status deploy/hub --timeout=180s

# 7.4 engine-hermes（如果有新版）
# kubectl -n unionagents set image deploy/engine-hermes-{agent_id} engine=unionagents/engine-hermes-v2:latest
# kubectl -n unionagents rollout status deploy/engine-hermes-{agent_id}

# 7.5 console-admin
kubectl -n unionagents set image deploy/console-admin admin=unionagents/console-admin:latest
kubectl -n unionagents rollout status deploy/console-admin --timeout=180s

# 7.6 enduser-portal
kubectl -n unionagents set image deploy/enduser-portal portal=unionagents/enduser-portal:latest
kubectl -n unionagents rollout status deploy/enduser-portal --timeout=180s
```

### Step 8：验证

```bash
# 所有 pod 2/2（gateway 1/1，v2 无 sidecar）
kubectl -n unionagents get pods

# gateway ASR env
for p in $(kubectl -n unionagents get pods -l app=gateway -o name | head -1); do
  kubectl -n unionagents exec $p -c gateway -- env | grep UA_ASR
done
# 期望：UA_ASR_PROVIDER=volcengine + UA_ASR_VOLC_API_KEY=${ASR_VOLC_API_KEY} + UA_ASR_VOLC_RESOURCE_ID=volc.seedasr.auc

# Secret
kubectl -n unionagents get secret unionagents-secret -o jsonpath="{.data.asr-volc-api-key}" | base64 -d; echo
# 期望：${ASR_VOLC_API_KEY}
```

### Step 9：功能验证（企微端）

| 测试项 | 操作 | 期望 |
|---|---|---|
| 文本消息 | 企微发"你好" | 正常回复 |
| 语音消息 | 企微发语音"查询试驾报告" | `Voice transcribed: text=查询试驾报告` + 正常回复 |
| 冷启动提示 | 首次消息/profile 冷启动 | "🤖 启动中" / "🕐 准备会话" |
| 长回复提示 | 慢回复（>5s） | "🤔 思考中..." |
| 卡片 | skill 触发卡片 | 卡片正常渲染 |
| 错误提示 | 未绑定用户发消息 | "⚠️ 尚未绑定，请联系管理员开通" |

---

## 四、回滚方案

### 4.1 镜像回滚
```bash
# 回滚到旧镜像 tag
kubectl -n unionagents set image deploy/gateway gateway=docker.io/unionagents/gateway:v0.8.16
kubectl -n unionagents set image deploy/manager manager=docker.io/unionagents/manager:v0.8.16
# ... 其他服务同理
```

### 4.2 DB 回滚（如果 migration 有问题）
```bash
# 用 Step 1 的备份恢复
kubectl exec -i postgres-0 -n unionagents -- psql -U unionagents -d unionagents < /home/mhero/backup_xxx.sql
```

### 4.3 gateway v2→v1 回滚（恢复 sidecar）
```bash
# apply with-asr-sidecar overlay（恢复 sidecar + local provider）
kubectl -n unionagents apply -k deploy/k8s/services/overlays/with-asr-sidecar/
```

---

## 五、注意事项

1. **DB 备份必做**：migration 改表结构，备份是回滚保障。
2. **migration 先于 manager 升级**：manager ORM 匹配新 schema，先迁移 DB。
3. **gateway v1→v2 架构变更**：撤 asr-sidecar sidecar（2/2→1/1），apply base gateway.yaml。
4. **ASR Secret 不被覆盖**：如果环境有 CI 自动部署，确认 Secret 不被 placeholder 覆盖（124 教训）。
5. **engine 镜像**：engine-hermes 镜像看是否有新版（engines/ 目录 Dockerfile）。如果没改，不升级 engine。
6. **滚动升级**：K8s RollingUpdate（x2 副本逐个替换），不中断服务。
7. **升级顺序**：DB migration → manager → gateway → hub → engine → console → enduser。
8. **验证**：升级后必须功能验证（文本/语音/卡片/错误提示），确认无回归。
