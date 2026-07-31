# Gaia 引擎部署指导书

> **读者**：负责把 Gaia 部署到 k3s 集群的运维 / DevOps 工程师
> **前置**：已有一台或多台可 SSH 登录的 Linux 机器，已装好 k3s 并组成集群
> **适用场景**：虚拟表 / 联邦查询（minimal profile，约 2GB 内存）；也可选 full profile 启用托管表能力

> 📌 **本文使用占位符表示环境相关变量，部署时请替换为你的实际值**：
> - `<VERSION>` — 部署包版本号（如 `0.1.0`），见部署包内的 `VERSION` 文件
> - `<ARCH>` — 目标机器 CPU 架构：`arm64`（aarch64）或 `amd64`（x86_64），用 `uname -m` 确认
> - `<master 公网 IP>` / `<master 内网 IP>` / `<worker 内网 IP>` — 集群各节点 IP
> - `<NODE_PORT>` — 对外暴露的 NodePort（默认 `30082`，需选集群空闲端口）

---

## 一、环境确认（部署前必读）

### 1.1 机器要求

目标机器需具备以下环境（缺失项需先安装）：

| 项目 | 要求 | 说明 |
|------|------|------|
| 操作系统 | Linux（aarch64 或 x86_64） | 与部署包 `<ARCH>` 一致 |
| k3s | v1.28+ | 已组成集群（单节点或多节点均可） |
| containerd | k3s 内置 | 镜像导入用 `k3s ctr` |
| 内存 | ≥ 4GB（每节点，minimal profile） | full profile 需更多 |
| 磁盘 | ≥ 20GB 可用 | 官方镜像约 4GB + 数据 |

### 1.2 集群节点

用 `kubectl get nodes -o wide` 确认集群拓扑，记录每个节点的角色与内网 IP：

```
NAME         ROLES                  INTERNAL-IP
<node-1>     control-plane,master   <master 内网 IP>
<node-2>     <none>                 <worker 内网 IP>
```

- 确认节点都**无 taint**（或已对 Gaia 容忍），均可调度 Pod
- 若集群已运行 UnionAgents 平台（`unionagents` namespace），Gaia 用独立 namespace `gaia`，互不干扰

### 1.3 关键约束（务必遵守）

| 约束 | 值 | 原因 |
|------|-----|------|
| **namespace** | `gaia` | 与 UA 平台的 `unionagents` 隔离 |
| **NodePort** | `<NODE_PORT>`（默认 `30082`） | 需选集群未占用的端口 |
| **Pod CIDR** | 集群实际值 | 用步骤 3 探测命令获取，填入 `.env.local` |
| **StorageClass** | `local-path` | k3s 默认，已存在 |

### 1.4 镜像架构说明

**部署包按 CPU 架构区分**（文件名带 `-arm64` 或 `-amd64` 后缀），内部自有镜像按对应架构单架构构建。请先确认目标机器架构，并索取匹配的部署包：

```bash
uname -m
# aarch64 → 用 gaia-deploy-<VERSION>-arm64.tar.gz
# x86_64  → 用 gaia-deploy-<VERSION>-amd64.tar.gz
```

> ⚠️ 架构必须匹配，不可混用。若拿到的包架构与目标机器不符，请向开发索取对应架构的包。

**官方镜像**（postgres/gravitino/trino 等）从 docker.io 在线拉取，均支持 arm64 与 amd64：

| 镜像 | arm64/amd64 | 体积 | 用途 | profile |
|------|:---:|:---:|------|:---:|
| `ngosang/timescaledb-postgis:2.24.0-pg16-postgis3` | ✅ | ~600M | PostgreSQL + PostGIS + TimescaleDB | minimal |
| `apache/gravitino:1.3.0` | ✅ | ~1.6G | 物理资产编目 + Iceberg REST Catalog | minimal |
| `trinodb/trino:478` | ✅ | ~1.7G | 联邦查询引擎（虚拟表核心） | minimal |
| `nginx:alpine` | ✅ | ~50M | initContainer 等待用 | minimal |
| `rustfs/rustfs:latest` | ✅ | - | S3 兼容存储 | full |
| `apache/doris:fe-4.0.5` / `be-4.0.5` | ✅ | - | 在线读加速 | full |
| `apache/seatunnel:2.3.13` | ✅ | - | 数据搬运 | full |
| `apache/kafka:4.3.0` | ✅ | - | 流式场景 | full |

> ⚠️ **minimal profile 需拉取的官方镜像合计约 4GB**（PG 600M + Gravitino 1.6G + Trino 1.7G + nginx 50M）。国内网络环境下建议先配置镜像加速（见步骤 2.5）。

**自有镜像**（4 个，单架构 OCI tar）已打包在 `images/` 目录，`ctr images import` 后 containerd 直接使用：

| 镜像 | 用途 |
|------|------|
| `gaia-api-<VERSION>-<ARCH>.tar` | 后端 API（FastAPI） |
| `gaia-better-auth-<VERSION>-<ARCH>.tar` | 认证服务 |
| `gaia-trino-plugins-<VERSION>-<ARCH>.tar` | Trino Gravitino 连接器插件 |
| `gaia-web-ui-<VERSION>-<ARCH>.tar` | 前端（nginx 托管 SPA + API 反代） |

---

## 二、部署包内容

解压后的目录结构：

```
gaia-deploy-<VERSION>/
├── manifests/                    # K8s 清单（已参数化，部署时 envsubst 渲染）
│   ├── namespace.yaml
│   ├── infra/
│   │   ├── core/                 # 任何 profile 都部署（minimal 即用）
│   │   │   ├── postgres.yaml + postgres-config.yaml
│   │   │   ├── gravitino.yaml + gravitino-init.yaml
│   │   │   ├── trino.yaml + trino-config.yaml
│   │   │   └── secret.yaml
│   │   └── optional/             # 仅 full profile 部署（虚拟表场景不需要）
│   │       ├── doris.yaml + doris-config.yaml
│   │       ├── rustfs.yaml
│   │       ├── seatunnel.yaml + seatunnel-config.yaml + seatunnel-entrypoint.yaml
│   │       ├── kafka.yaml
│   │       ├── kestra.yaml       # 已 replicas: 0（可选）
│   │       └── neo4j.yaml        # 已 replicas: 0（可选）
│   ├── services/                 # 后端服务
│   │   ├── api.yaml
│   │   ├── better-auth.yaml
│   │   └── migrate.yaml          # 一次性建表 Job
│   └── apps/
│       └── web-ui.yaml
├── scripts/                      # 部署脚本
│   ├── deploy.sh                 # 主部署脚本
│   ├── preflight.sh              # 部署前检查
│   ├── load-images.sh            # 导入自有镜像到 containerd
│   ├── envsubst-all.sh           # 清单渲染
│   └── ...
├── images/                       # 自有镜像 OCI tar（单架构）
│   ├── gaia-api-<VERSION>-<ARCH>.tar
│   ├── gaia-trino-plugins-<VERSION>-<ARCH>.tar
│   ├── gaia-better-auth-<VERSION>-<ARCH>.tar
│   └── gaia-web-ui-<VERSION>-<ARCH>.tar
├── jars/                         # 国产库 JDBC 驱动（可选，ADR-014，当前未启用）
├── .env.local.example            # 环境变量模板（复制为 .env.local 编辑）
└── VERSION
```

---

## 三、部署步骤

### 步骤 1：上传部署包

把部署包上传到目标机器（示例路径 `/root/gaia/`，可自定）：

```bash
ls -lh /root/gaia/
# 应看到：
#   gaia-deploy-<VERSION>-<ARCH>.tar.gz       （部署包）
#   gaia-deploy-<VERSION>-<ARCH>.tar.gz.sha256（校验文件）

# 校验完整性
cd /root/gaia
sha256sum -c gaia-deploy-<VERSION>-<ARCH>.tar.gz.sha256
# 应输出：gaia-deploy-<VERSION>-<ARCH>.tar.gz: OK
```

### 步骤 2：解压

```bash
cd /root/gaia
tar xzf gaia-deploy-<VERSION>-<ARCH>.tar.gz
cd gaia-deploy-<VERSION>
```

### 步骤 2.5：配置 docker.io 镜像加速（国内网络必做）

部署脚本需要从 docker.io 拉取约 4GB 的官方镜像（Gravitino 1.6G、Trino 1.7G、PG 600M）。国内网络直连 docker.io 经常 TLS 握手超时导致 `ImagePullBackOff`，**建议在部署前配置镜像加速**：

```bash
# 创建 k3s containerd mirror 配置
cat > /etc/rancher/k3s/registries.yaml <<'EOF'
mirrors:
  docker.io:
    endpoint:
      - https://docker.1ms.run
EOF

# 重启 k3s 生效
systemctl restart k3s

# 等待 k3s 恢复（约 30s）
sleep 30
kubectl get nodes
# 所有节点都应是 Ready
```

> ⚠️ 部分 OS（如基于 cgroup v1 的旧版 EulerOS/CentOS）在 k3s 重启后 kubelet 可能因 cgroup 问题崩溃，报错 `root container [kubepods] doesn't exist`。若 `kubectl get nodes` 返回 NotReady 或连接失败，按「5.1 k3s 重启后 kubelet 崩溃」中的 cgroup 恢复步骤处理。

### 步骤 2.6：开放安全组 / 防火墙端口（对外服务必做）

Gaia 通过 NodePort `<NODE_PORT>`（默认 `30082`）对外提供服务。**云服务器安全组 / 主机防火墙默认不会放行该端口，需要手动放行**，否则部署完公网无法访问。

**在云控制台或主机防火墙操作**：
1. 找到 master 实例（公网 IP `<master 公网 IP>`）
2. 安全组规则 / 防火墙 → 入方向规则 → 添加规则
3. 协议 TCP，端口 `<NODE_PORT>`，源地址 `0.0.0.0/0`（或限制为办公网 IP 段）
4. 确认保存

> 这一步可以和部署并行做，只要在「步骤 5 验证」前完成即可。

### 步骤 2.7：配置 SSH 免密到 worker 节点（多节点集群必做）

多节点集群下，自有镜像需分发到所有将运行 Pod 的节点。部署脚本通过 SSH 流式传输镜像到 worker 节点，需先配置免密登录。

在 master 上执行（把 `<worker 内网 IP>` 替换为你的 worker 节点 IP）：

```bash
# 1. 生成密钥对（已有可跳过）
[ -f ~/.ssh/id_rsa ] || ssh-keygen -t rsa -b 4096 -N "" -f ~/.ssh/id_rsa

# 2. 分发公钥到 worker（需输入一次 worker 的 root 密码）
ssh-copy-id root@<worker 内网 IP>

# 3. 验证免密登录
ssh root@<worker 内网 IP> "hostname"
# 应直接输出 worker 主机名，不要求密码
```

> 单节点集群跳过此步（不配置 `LOAD_IMAGES_NODES` 即可）。

### 步骤 3：编辑配置

```bash
cp .env.local.example .env.local
vi .env.local
```

**关键配置项**（按你的实际环境填写）：

```bash
# 集群参数
POD_CIDR="<集群实际 Pod CIDR>"              # ← 用下方探测命令获取
STORAGE_CLASS="local-path"
NODE_PORT_WEB_UI="30082"                   # ← 集群空闲端口，默认 30082
K8S_RUNTIME="k3s"

# 镜像拉取策略
IMAGE_PULL_POLICY_INFRA="IfNotPresent"    # 官方镜像在线拉取
IMAGE_PULL_POLICY_SERVICES="Never"        # 自有镜像已 import，不拉

# 部署 profile（虚拟表场景用 minimal）
DEPLOY_PROFILE="minimal"

# 多节点镜像分发（集群部署必填）
# 自有镜像通过 ctr import 加载到节点 containerd，多节点集群需分发到所有将运行 Pod 的节点。
# 逗号分隔的 SSH 可达节点列表（user@host[:port]），留空=只导入本机（单节点集群）。
# 示例（多节点集群填 worker 的 SSH 地址）：
LOAD_IMAGES_NODES="root@<worker 内网 IP>"
# SSH 私钥路径（确保当前用户能免密 ssh 到上述节点）
SSH_PRIVATE_KEY="$HOME/.ssh/id_rsa"
# SSH 端口（被 LOAD_IMAGES_NODES 中的 :port 覆盖）
SSH_PORT="22"

# 数据库凭据
GAIA_PG_USER="ontology"
GAIA_PG_PASSWORD="<生产环境请改强密码>"
GAIA_PG_DATABASE="ontology"

# S3（RustFS，minimal profile 不部署，但 Secret 仍需填）
GAIA_S3_ACCESS_KEY="minioadmin"
GAIA_S3_SECRET_KEY="<生产环境请改强密码>"

# Doris（minimal profile 不部署，Secret 仍需填）
GAIA_DORIS_USER="root"
GAIA_DORIS_PASSWORD=""

# Neo4j（minimal profile 不部署）
GAIA_NEO4J_PASSWORD="change-me"

# better-auth 密钥（必须 ≥32 字符，建议用下面命令生成随机密钥）
GAIA_BETTER_AUTH_SECRET="<用下面的命令生成>"
# Better Auth 信任的 Origin（CORS 白名单）
# 必须包含用户访问系统的地址，否则登录会被 CORS 拒绝
# 对外服务填公网 NodePort 地址
GAIA_TRUSTED_ORIGINS="http://<master 公网 IP>:<NODE_PORT>"

# AI API Key（按需填写，虚拟表场景可不填）
GAIA_PROVISION_TOKEN=""
GAIA_OPENAI_API_KEY=""
GAIA_DEEPSEEK_API_KEY=""
GAIA_ANTHROPIC_API_KEY=""
GAIA_GOOGLE_API_KEY=""
GAIA_MOONSHOT_API_KEY=""
GAIA_ALIBABA_API_KEY=""
```

**POD_CIDR 探测**（确认集群实际值）：

```bash
kubectl get nodes -o jsonpath='{.items[0].spec.podCIDR}'
# 输出集群实际 Pod CIDR（如 10.42.0.0/16 或 10.42.0.0/24）
# 填到 .env.local 的 POD_CIDR
```

> `.env.local.example` 默认值是 `10.42.0.0/16`（k3s 默认），若你的集群实际值不同必须改。

**GAIA_BETTER_AUTH_SECRET 生成**（必须 ≥32 字符）：

```bash
python3 -c "import secrets;print(secrets.token_urlsafe(32))"
# 输出类似: aBcDeFgH..._xYz1234567890
# 复制到 .env.local 的 GAIA_BETTER_AUTH_SECRET
```

> **重要**：`DEPLOY_PROFILE="minimal"` 是虚拟表场景的正确选择，只部署 PG/Gravitino/Trino/API/better-auth/web-ui 共 6 个 Pod，约 2GB 内存。如需全量能力（Doris/RustFS/SeaTunnel/Kafka）改为 `full`。

### 步骤 4：执行部署

```bash
bash scripts/deploy.sh <VERSION>
```

部署脚本会自动完成以下 7 步：

1. **preflight**：检查 k3s/ctr/kubectl 可用性、NodePort 占用、StorageClass 存在、多节点 LOAD_IMAGES_NODES 配置、官方镜像架构支持
2. **导入镜像**：把 `images/` 下的 4 个 OCI tar 导入 containerd（需 root）
   - 本机导入 + 补短名称 tag（消除 ErrImageNeverPull）
   - 若配了 `LOAD_IMAGES_NODES`，自动 SSH 流式分发到其他节点（可重入，已存在的镜像跳过）
3. **渲染清单**：envsubst 把 `${VAR}` 占位符替换为 `.env.local` 的值
4. **创建 namespace + Secret**（Secret 幂等：已存在则保留，首次创建才用 .env.local 的值；校验 BETTER_AUTH_SECRET 长度）
5. **部署基础设施**（core/ 目录：PG + Gravitino + Trino + gravitino-init Job）
   - 等 PostgreSQL 就绪（180s 超时）
   - 等 Gravitino metalake 初始化 Job 完成（300s 超时，镜像约 1.6GB 首次拉取较慢）
6. **部署后端服务**（migrate Job + api + better-auth）
   - migrate Job 跑 `alembic upgrade head`，一次建好业务表 + better_auth 认证表（9 表）
   - 等 migrate Job 完成（300s 超时）
7. **部署前端**（web-ui）+ rollout restart 自有镜像 Deployment + 等待就绪 + Pod 调度状态检查
   - 镜像 tag 不变时 `kubectl apply` 不会触发滚动更新，脚本显式 `rollout restart` 确保新镜像生效

### 步骤 5：验证

```bash
# 1. 查看 Pod 状态（minimal profile 应有 6 个 Running + 2 个 Completed Job）
kubectl get pods -n gaia

# 2. 先在集群内验证（不等安全组放行）
kubectl port-forward -n gaia svc/gaia-web-ui 8088:80 &
curl http://127.0.0.1:8088/health
# 应返回 {"status":"ok"}
# 若返回 502 或 nginx 默认页，说明 web-ui 的 nginx 反代尚未就绪，等 30s 后重试
kill %1  # 停掉 port-forward

# 3. 放行安全组 <NODE_PORT> 后，公网验证（见 4.1 节）
curl http://<master 公网 IP>:<NODE_PORT>/health
# 应返回 {"status":"ok"}

# 4. 本体列表
curl http://<master 公网 IP>:<NODE_PORT>/ontologies
# 应返回 JSON 数组
```

> 步骤 2 用 port-forward 在集群内快速验证服务是否正常；步骤 3 验证公网访问是否打通。两者都通过才算部署成功。

**预期 Pod 列表**（minimal profile）：

```
NAME                                READY   STATUS      RESTARTS   AGE
gaia-api-xxxxx-xxxxx                1/1     Running     0          1m
gaia-better-auth-xxxxx-xxxxx        1/1     Running     0          1m
gaia-gravitino-xxxxx-xxxxx          1/1     Running     0          2m
gaia-gravitino-init-xxxxx           0/1     Completed   0          2m
gaia-migrate-xxxxx                  0/1     Completed   0          1m
gaia-postgres-0                     1/1     Running     0          2m
gaia-trino-xxxxx-xxxxx              1/1     Running     0          2m
gaia-web-ui-xxxxx-xxxxx             1/1     Running     0          1m
```

### 步骤 6：创建初始管理员（首次部署必做）

系统部署后没有任何管理员账号，第一个注册的用户默认是普通用户（无权限）。需要先注册再提权：

**1. 注册第一个用户**

先放行安全组 <NODE_PORT>（见 4.1 节），然后浏览器访问公网地址：

```
http://<master 公网 IP>:<NODE_PORT>
```

点击「注册」→ 填写邮箱密码 → 完成注册

> 若安全组未放行，也可在目标机器上用 port-forward 临时访问：
> `kubectl port-forward -n gaia svc/gaia-web-ui 8088:80`，然后本机浏览器访问 http://127.0.0.1:8088

**2. 提升为 admin**

```bash
# 回到部署目录，运行提权脚本
bash scripts/make-first-admin.sh <你注册的邮箱>
# 例如: bash scripts/make-first-admin.sh admin@gaia.local
```

脚本会把该用户在 Better Auth 数据库中的 role 改为 `admin`。

**3. 重新登录**

退出登录后重新登录，新的 JWT 会携带 `roles=["admin"]`，即可访问「身份管理」页面管理其他用户/群组/权限。

> ⚠️ **必须重新登录**：JWT 是登录时签发的，提权前的旧 token 不含 admin 角色。退出再登录才会拿到新 token。

---

## 四、部署后操作

### 4.1 访问方式

安全组已按步骤 2.6 放行 <NODE_PORT> 后，可直接公网访问。

**方式 A：公网 NodePort（推荐，对外服务）**

```bash
# 健康检查
curl http://<master 公网 IP>:<NODE_PORT>/health
# 应返回 {"status":"ok"}

# 本体列表
curl http://<master 公网 IP>:<NODE_PORT>/ontologies
```

浏览器访问 `http://<master 公网 IP>:<NODE_PORT>` 即可打开前端。

**方式 B：端口转发（调试用，不对外暴露）**

在目标机器上 SSH 会话中执行，把 Service 端口转发到本机回环地址：

```bash
# 前端
kubectl port-forward -n gaia svc/gaia-web-ui 8088:80
# 浏览器访问 http://127.0.0.1:8088（需 SSH 端口转发或本机浏览器）

# API 直连
kubectl port-forward -n gaia svc/gaia-api 8000:8000
# curl http://127.0.0.1:8000/health
```

> port-forward 只在执行命令的机器上监听 127.0.0.1，适合临时调试，不适合对外服务。

### 4.2 虚拟表使用流程

部署成功后，使用虚拟表的步骤：

1. **登录 web-ui**（`http://<master 公网 IP>:<NODE_PORT>`，公网访问）
2. **连接外部数据源**：在数据源页面添加 JDBC 数据源（MySQL/PostgreSQL 等），系统会通过 Gravitino 注册 catalog，并通过 Trino 加载为可查询 catalog
3. **登记虚拟表**：选择数据源 → 选择库表 → 登记为 VIRTUAL 虚拟表（不落地，Trino 联邦查询）
4. **查询**：虚拟表对象通过 Trino 直查外部源表，无需 Doris/Iceberg

### 4.3 重新部署 / 升级镜像

当发布了新的部署包（如修复了 web-ui nginx 配置、更新了 gaia-api 代码），升级流程：

```bash
cd /root/gaia
# 1. 备份旧目录（可选）
mv gaia-deploy-<VERSION> gaia-deploy-<VERSION>.old
# 2. 解压新包
tar xzf gaia-deploy-<VERSION>-<ARCH>.tar.gz
# 3. 拷贝旧 .env.local（保留密钥和配置）
cp gaia-deploy-<VERSION>.old/.env.local gaia-deploy-<VERSION>/
# 4. 重新部署
bash scripts/deploy.sh <VERSION>
```

**重新部署时的行为**：
- **Secret 保留**：BETTER_AUTH_SECRET 等密钥不会覆盖，现有用户 session 不失效
- **migrate 重跑**：alembic upgrade head 幂等执行，保证 schema 最新
- **镜像自动重启**：4 个自有镜像 Deployment 会 `rollout restart`，新镜像生效（中断约 5-15 秒/服务）
- **官方镜像不动**：PG/Gravitino/Trino 等 StatefulSet/Deployment 不重启（除非清单变了）

> ⚠️ 如需更新 BETTER_AUTH_SECRET（会导致所有用户被登出）：
> ```bash
> kubectl delete secret gaia-secret -n gaia
> # 修改 .env.local 的 GAIA_BETTER_AUTH_SECRET，重跑 deploy.sh
> ```

### 4.4 升级到 full profile（可选）

如果后续需要托管表（MANAGED，数据落地 Iceberg + Doris 加速）或 CDC 能力：

```bash
cd /root/gaia/gaia-deploy-<VERSION>
vi .env.local
# 改：DEPLOY_PROFILE="full"
bash scripts/deploy.sh <VERSION>
```

会增量部署 Doris/RustFS/SeaTunnel/Kafka 等 optional 组件，core 组件保持不动。

---

## 五、常见问题排查

### 5.1 k3s 重启后 kubelet 崩溃（cgroup 问题）

**现象**：配置镜像加速（步骤 2.5）后 `systemctl restart k3s`，节点变 NotReady，kubelet 日志报：

```
E0721 12:48:49.464096 kubelet.go:1511] "Failed to start ContainerManager"
  err="failed to initialize top level QOS containers: root container
  [kubepods] doesn't exist"
```

**根因**：部分 OS（如基于 cgroup v1 的旧版 EulerOS/CentOS）的 cgroup v1 层次结构中，`kubepods` cgroup 在 k3s 重启后被清理且无法自动重建。

**修复**：给 k3s service 增加两个 kubelet 参数：

```bash
# 编辑 k3s service 文件
vi /etc/systemd/system/k3s.service

# 在 ExecStart 行末尾追加（注意同一行，或用 \ 续行）：
#   --kubelet-arg=cgroups-per-qos=false --kubelet-arg=enforce-node-allocatable=

# 完整示例:
# ExecStart=/usr/local/bin/k3s server \
#     --kubelet-arg=cgroups-per-qos=false \
#     --kubelet-arg=enforce-node-allocatable=

systemctl daemon-reload
systemctl restart k3s
sleep 30
kubectl get nodes
# 所有节点都应恢复 Ready
```

### 5.2 部署脚本卡住

**现象**：`deploy.sh` 卡在 "等待 PostgreSQL 就绪" 或 "等待 Gravitino metalake 初始化"

**排查**：

```bash
# 看 Pod 事件（最常见是镜像拉取中或失败）
kubectl describe pod -n gaia -l app=gaia-postgres
kubectl describe pod -n gaia -l app=gaia-gravitino

# 看 Pod 日志
kubectl logs -n gaia job/gaia-migrate
kubectl logs -n gaia -l app=gaia-postgres --tail=50
```

**常见原因**：
- 镜像拉取中：Gravitino 1.6GB / Trino 1.7GB，国内 mirror 拉取约 3~5 分钟，`kubectl describe pod` 会看到 `pulling image`，耐心等待
- 镜像拉取失败：`ImagePullBackOff` → 检查步骤 2.5 的 mirror 配置是否生效（`cat /etc/rancher/k3s/registries.yaml`）
- PVC 绑定失败：`PodInitializing` 卡住 → 检查 local-path StorageClass 是否正常
- migrate 失败：DB 连接问题 → 看 migrate Job 日志

### 5.3 镜像拉取失败

**现象**：Pod 状态 `ImagePullBackOff`、`ErrImagePull` 或 `ErrImageNeverPull`

**排查**：

```bash
kubectl get pods -n gaia -o wide   # 看 Pod 调度在哪个节点
kubectl describe pod -n gaia <pod-name> | grep -A5 "Events:"
```

**区分三种情况**：

**① 官方镜像拉取失败**（postgres/gravitino/trino）：
从 docker.io 拉取，失败说明网络问题，确认步骤 2.5 mirror 配置已生效：
```bash
cat /etc/rancher/k3s/registries.yaml   # 应有 docker.1ms.run mirror
```

**② 自有镜像 ErrImageNeverPull**（gaia-api/gaia-trino-plugins/gaia-better-auth/gaia-web-ui）：
`IMAGE_PULL_POLICY_SERVICES=Never` 要求镜像已在节点 containerd。检查：
```bash
# 查哪些节点已导入镜像
kubectl get nodes -l gaia-images=loaded

# 在 Pod 所在节点检查镜像
ssh root@<节点IP> "k3s ctr images ls | grep unionagents/gaia"
# 应看到 4 个镜像，且同时有短名称（unionagents/gaia-xxx）和长名称（docker.io/unionagents/gaia-xxx）
```

常见原因：
- **镜像只在 master 导入，Pod 调度到 worker**：在 `.env.local` 配置 `LOAD_IMAGES_NODES="root@<worker-IP>"`，重跑部署；或手动在 worker 执行 `k3s ctr images import`
- **缺少短名称 tag**：load-images.sh 会自动补，旧版本可能漏，手动补：
  ```bash
  k3s ctr images tag docker.io/unionagents/gaia-api:<VERSION> unionagents/gaia-api:<VERSION>
  ```

**③ 手动导入镜像**（load-images.sh 失败时备用）：
```bash
cd /root/gaia/gaia-deploy-<VERSION>
# 本机导入
sudo bash scripts/load-images.sh images k3s
# 或手动单个导入
sudo k3s ctr images import images/gaia-api-<VERSION>-<ARCH>.tar
sudo k3s ctr images tag docker.io/unionagents/gaia-api:<VERSION> unionagents/gaia-api:<VERSION>
```

### 5.4 端口冲突

**现象**：web-ui Service 创建失败，提示 NodePort 冲突

**处理**：检查集群已占用的 NodePort，选一个空闲端口改 `.env.local`：

```bash
# 查看已分配的 NodePort
kubectl get svc -A -o jsonpath='{range .items[*]}{.metadata.namespace}/{.metadata.name}:{.spec.ports[*].nodePort}{"\n"}{end}' | grep -v ':$'

# 改为空闲端口
NODE_PORT_WEB_UI="<其他空闲端口>"   # 例如 30083
```

### 5.5 资源不足

**现象**：Pod `Pending`，事件提示 `Insufficient memory` 或 `Insufficient cpu`

**排查**：

```bash
kubectl describe pod -n gaia <pod-name> | tail -10
kubectl top nodes   # 需 metrics-server
```

**处理**：
- minimal profile 约 2GB，确认集群总可用内存充足
- 若同集群其他平台占用过多，考虑先停非必要服务，或缩 Gaia 的资源请求

### 5.6 namespace 删除卡住

**现象**：`kubectl delete ns gaia` 卡在 `Terminating`

**原因**：通常是 metrics-server API discovery 失败导致 namespace controller 卡住

**处理**：

```bash
# 1. 先确认所有资源已删
kubectl api-resources --verbs=list --namespaced -o name | xargs -n1 kubectl get -n gaia --ignore-not-found

# 2. 残留资源清完后，patch 掉 finalizer
kubectl patch namespace gaia -p '{"metadata":{"finalizers":[]}}' --type=merge

# 3. 若仍卡住，等 metrics-server 恢复或重启 k3s（注意 5.1 的 cgroup 问题）
systemctl restart k3s
```

### 5.7 完全卸载重来

```bash
kubectl delete namespace gaia
# 等删除完成（参考 5.6 处理卡住问题）
# PVC 会随 namespace 自动删（local-path reclaim policy=Delete）
# 重新部署
cd /root/gaia/gaia-deploy-<VERSION>
bash scripts/deploy.sh <VERSION>
```

---

## 六、运维命令速查

```bash
# 查看 Pod
kubectl get pods -n gaia
kubectl get pods -n gaia -o wide

# 查看日志
kubectl logs -n gaia -l app=gaia-api --tail=100 -f
kubectl logs -n gaia -l app=gaia-trino --tail=50

# 进入 Pod
kubectl exec -n gaia -it deploy/gaia-api -- bash
kubectl exec -n gaia -it gaia-postgres-0 -- psql -U ontology -d ontology

# 端口转发
kubectl port-forward -n gaia svc/gaia-web-ui 8088:80     # 前端
kubectl port-forward -n gaia svc/gaia-api 8000:8000       # API
kubectl port-forward -n gaia svc/gaia-trino 8080:8080     # Trino UI
kubectl port-forward -n gaia svc/gaia-gravitino 8090:8090 # Gravitino UI

# 重启服务
kubectl rollout restart -n gaia deploy/gaia-api
kubectl rollout restart -n gaia deploy/gaia-web-ui

# 查看 Trino catalog（验证虚拟表数据源注册）
kubectl exec -n gaia -it deploy/gaia-trino -- trino --execute "SHOW CATALOGS"

# 查看 Gravitino metalake
kubectl exec -n gaia -it deploy/gaia-gravitino -- curl -s localhost:8090/api/metalakes | python3 -m json.tool
```

---

## 七、联系支持

部署遇到问题，请收集以下信息反馈：

1. `kubectl get pods -n gaia -o wide` 的完整输出
2. 失败 Pod 的 `kubectl describe pod -n gaia <pod-name>`
3. 失败 Pod 的 `kubectl logs -n gaia <pod-name> --tail=100`
4. `deploy.sh` 的完整输出（重定向到文件：`bash scripts/deploy.sh <VERSION> 2>&1 | tee deploy.log`）

将以上信息发回，由开发迭代修复。
