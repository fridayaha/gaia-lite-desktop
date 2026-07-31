# Gaia k3s 部署

Gaia 引擎的 Kubernetes（k3s）生产化部署清单。独立 namespace `gaia`，与 UnionAgents 平台（`unionagents` namespace）完全隔离。

## 架构

```
deploy/
├── k8s/
│   ├── namespace.yaml              # gaia namespace
│   ├── infra/                      # 基础设施（11 个服务 + 2 个 init Job）
│   │   ├── secret.yaml             # 本地 dev Secret（占位符值）
│   │   ├── postgres-config.yaml    # PG postgresql.conf + init SQL（ConfigMap）
│   │   ├── postgres.yaml           # StatefulSet（timescaledb-postgis 镜像）
│   │   ├── rustfs.yaml             # StatefulSet（S3 兼容对象存储）
│   │   ├── gravitino.yaml          # Deployment（物理资产注册 + Iceberg REST Catalog）
│   │   ├── gravitino-init.yaml     # Job（创建 metalake "ontology"，Trino 加载前置）
│   │   ├── doris-config.yaml       # fe.conf / be.conf（ConfigMap，FQDN 模式）
│   │   ├── doris.yaml              # FE + BE StatefulSet（BE initContainer 自动注册 FQDN）
│   │   ├── trino-config.yaml       # catalog properties（ConfigMap）
│   │   ├── trino.yaml              # Deployment（initContainer 等 metalake 就绪）
│   │   ├── kafka.yaml              # StatefulSet（KRaft 单节点）
│   │   ├── seatunnel-config.yaml   # seatunnel.yaml + hazelcast.yaml（ConfigMap）
│   │   ├── seatunnel-entrypoint.yaml # 驱动冲突处理 shim（ConfigMap）
│   │   ├── seatunnel.yaml          # Deployment（单节点 Zeta）
│   │   ├── kestra.yaml             # Deployment（Pipeline Builder）
│   │   └── neo4j.yaml              # StatefulSet（可选，图推理）
│   ├── services/
│   │   ├── migrate.yaml            # Job（alembic upgrade head）
│   │   ├── api.yaml                # Deployment（FastAPI 后端）
│   │   └── better-auth.yaml        # Deployment（认证服务，可选）
│   └── apps/
│       └── web-ui.yaml             # Deployment + NodePort:30082
├── ci/
│   ├── deploy.sh                   # CI 一键部署脚本
│   ├── secret.yaml                 # CI Secret 模板（envsubst 占位符）
│   ├── .env.local.example          # 敏感配置模板
│   └── .env.local                  # 实际值（gitignore，不入库）
└── nginx/
    └── web-ui.conf                 # 前端 nginx 配置（SPA + API 反代）
```

## 端口避让

NodePort 是集群全局资源（跨 namespace 也会冲突）。Gaia 对外暴露的 NodePort 已避开 UnionAgents 平台：

| 服务 | NodePort | UA 对应（避让）|
|------|----------|----------------|
| gaia-web-ui | 30082 | UA admin=30080 / enduser=30081 / grafana=30090 / langfuse=30030 |

集群内 Service port 用各自容器原端口（不同 namespace 互不影响）。

## 相比 docker-compose 的优化

| 项 | docker-compose | k3s |
|----|----------------|-----|
| Doris 互发现 | 固定 docker IP（172.18.0.10/0.20）| k8s Service DNS + headless Service 稳定 hostname，BE 自动注册 |
| SeaTunnel | master/worker 拆两份 | 单节点 Zeta（合并）|
| Gravitino 配置 | 挂载 *.conf（aux 模式不生效）| 纯 env（去掉无效挂载）|
| Doris priority_networks | 写死 172.18.0.0/24 | k3s Pod 网段（默认 10.42.0.0/16，可配）|
| migrate | 复用 api 镜像的容器 | Job（alembic upgrade head）|
| 固定子网 | networks 172.18.0.0/16 | k8s Pod 网络 + Service DNS |
| 国产库 JDBC jar | 挂载到 lib | 暂不挂（ADR-014 国产 CDC 在 k3s 暂不可用，核心链路不受影响）|
| Trino 插件 | 挂本地目录 | 自定义镜像烤入（Dockerfile.trino）|

## 构建与部署（build/deploy 分离）

Gaia 采用**构建与部署分离**设计：构建阶段产出多架构镜像 + 部署制品（tar.gz），部署阶段只需 kubectl 即可部署到任意 k8s 集群。详见 [`DESIGN-build-deploy-separation.md`](DESIGN-build-deploy-separation.md)。

### 自定义镜像（4 个）

| 镜像 | Dockerfile | 说明 |
|------|-----------|------|
| `unionagents/gaia-api` | `Dockerfile` | FastAPI 后端（含 alembic，migrate Job 复用）|
| `unionagents/gaia-trino` | `Dockerfile.trino` | Trino + gravitino connector 插件 |
| `unionagents/gaia-better-auth` | `auth-server/Dockerfile` | Better Auth 认证服务（容器内 npm ci，自包含）|
| `unionagents/gaia-web-ui` | `src/web-ui/Dockerfile` | 前端（两阶段构建：容器内 pnpm install + vite build → nginx 托管）|

### 构建阶段（CI / 发布机）

**多架构构建**（amd64 + arm64），导出 OCI tar（含 fat manifest），**不推镜像仓库**：

```bash
# 一键打包：多架构构建 + 收集清单/脚本/jar → tar.gz
make package VERSION=0.1.0
# 产物：dist/gaia-deploy-0.1.0.tar.gz（3.2GB，自包含）
```

**构建环境要求**：
- Docker 29+ + containerd image store（`/etc/docker/daemon.json` 的 `features.containerd-snapshotter: true`）
- `docker-buildx`（Ubuntu: `apt install docker-buildx`）
- QEMU 多架构模拟（`docker run --privileged --rm tonistiigi/binfmt --install all`）
- 首次运行 `make buildx-builder` 创建多架构 builder（含 registry mirror 加速）

> **本地开发调试**（单架构，快）：`make docker-all` + 手动 `k3s ctr images import`，不走 buildx。

### 部署阶段（目标集群）

部署机**不需要 docker、不需要源码**，只需 kubectl + 联网拉官方镜像：

```bash
# 1. 解压制品
tar xzf gaia-deploy-0.1.0.tar.gz
cd gaia-deploy-0.1.0

# 2. 填配置
cp .env.local.example .env.local
vi .env.local   # 填业务配置 + 集群参数（POD_CIDR/STORAGE_CLASS/NodePort 等）

# 3. 部署（会自动 preflight → load images → envsubst → kubectl apply）
bash scripts/deploy.sh 0.1.0
```

**部署流程**：
1. `preflight.sh`：预检集群环境（Pod 网段 / StorageClass / 端口占用 / 架构 / 官方镜像 arm64 支持）
2. `load-images.sh`：导入 4 个自有镜像 OCI tar 到 containerd（k3s 用 `k3s ctr`，标准 k8s 用 `ctr -n k8s.io`，需 root）
3. `envsubst-all.sh`：渲染清单模板（`${VAR}` 占位符 → 实际值）
4. `kubectl apply`：infra → 等 PG → 等 gravitino-init → services → 等 migrate → apps

**参数化**（全部通过 `.env.local` + envsubst 注入）：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `DEPLOY_PROFILE` | `minimal` | `minimal`（虚拟表/联邦查询，7 个 Pod ≈2GB）或 `full`（全量 ≈8GB）|
| `POD_CIDR` | `10.42.0.0/16` | Pod 网段（仅 full profile 部署 Doris 时用；preflight 自动探测）|
| `STORAGE_CLASS` | `local-path` | PVC 存储类（k3s 自带；标准 k8s 用集群已有 SC）|
| `NODE_PORT_WEB_UI` | `30082` | web-ui NodePort |
| `IMAGE_PULL_POLICY_INFRA` | `IfNotPresent` | 官方镜像拉取策略（在线；离线改 `Never`）|
| `IMAGE_PULL_POLICY_SERVICES` | `Never` | 自有镜像拉取策略（已 ctr import 到本地）|
| `K8S_RUNTIME` | `k3s` | `k3s`（用 k3s ctr）或 `k8s`（用 ctr -n k8s.io）|

### 部署 profile

Gaia 支持两种部署 profile，按场景选择：

| Profile | 组件 | 内存 | 适用场景 |
|---------|------|------|------|
| **minimal**（默认）| PG + Gravitino + Trino + API + better-auth + web-ui | ≈2GB | 虚拟表 / 联邦查询（不落地，Trino 直查外部数据源）|
| **full** | minimal + Doris + RustFS + SeaTunnel + Kafka + Kestra + Neo4j | ≈8GB | 托管表 / 全量能力（Iceberg 落地 + Doris 加速 + CDC + 流式）|

**虚拟表场景只需 minimal**：虚拟表不落地，Trino 通过 Gravitino 注册的外部 catalog 直接联邦查询源表，不需要 Doris/RustFS/SeaTunnel。

清单按目录组织：
```
manifests/infra/
├── core/      # 任何 profile 都部署（PG/Gravitino/Trino/secret）
└── optional/  # 仅 full profile 部署（Doris/RustFS/SeaTunnel/Kafka/Kestra/Neo4j）
```

切换 profile 只需改 `.env.local` 的 `DEPLOY_PROFILE` 后重新跑 `deploy.sh`。已部署 minimal 后想升级到 full，直接改 `DEPLOY_PROFILE=full` 重新部署即可（optional 组件会增量 apply，core 组件不变）。

> **离线场景**：与在线统一，预留 `OFFLINE=1` 扩展点（额外导官方镜像 tar + `IMAGE_PULL_POLICY_INFRA=Never`）。

## 本地 k3s 部署（开发调试快捷路径）

开发调试时无需走完整制品流程，可直接用 Makefile + 源码部署（需在仓库根目录）：

```bash
# 1. 复制敏感配置模板并填值
cp deploy/ci/.env.local.example deploy/ci/.env.local
# 编辑 deploy/ci/.env.local 填入实际值

# 2. 本地单架构构建镜像（不走 buildx，快）
make docker-all
# 导入 k3s containerd（需 root）
for img in api trino better-auth web-ui; do
  docker save unionagents/gaia-$img:latest | sudo k3s ctr images import -
done

# 3. 部署（Makefile 直读 deploy/k8s/ 清单，不走 envsubst，用本地最新镜像）
make k8s-all
```

> 注意：本地快捷路径用 `:latest` tag + `IfNotPresent`，镜像已在 containerd 里。
> 生产/跨集群部署请用上面的制品化流程（`make package` → `deploy.sh`）。

### 分步部署（开发调试）

```bash
make k8s-infra      # 基础设施
make k8s-services   # 后端（migrate Job + api + better-auth）
make k8s-apps       # 前端
```

### 本地开发访问

```bash
make pf-web-ui      # 前端 5173 → web-ui:80
make pf-api         # API 8000 → gaia-api:8000
make pf-postgres    # PG 5432 → gaia-postgres:5432
```

### 开发热重载（秒级生效，不重建镜像）

`scripts/local-update.sh` 每次都「docker build + ctr import + rollout」，适合改依赖/Dockerfile/部署清单。但**只改代码时用它太慢**——k3s 单节点和源码同文件系统，可直接热重载：

| 改动 | 命令 | 速度 | 原理 |
|------|------|------|------|
| Python 代码 | `make dev-api` | ~1s | hostPath 挂源码 + uvicorn `--reload`（watchfiles 监听） |
| 前端代码 | `make dev-web` | <1s | 本地 Vite dev server + HMR，API 经 vite proxy 转发 k8s |
| 两者都要 | `make dev` | — | `dev-web --api`，前后端同时热重载 |

```bash
make dev-api        # 启用后端热重载（patch deploy 加 hostPath volume + 改启动命令）
# ...改 Python 代码，保存即自动 reload，看日志：
# kubectl -n gaia logs -f deploy/gaia-api --tail=20
make dev-api-off    # 关闭热重载，回到镜像版

make dev-web        # 启用前端 HMR（本地 vite，不动 Pod）
# ...改前端代码，浏览器自动热更新
```

**与 local-update.sh 的分工**：
- 改 `src/ontology/**` 或 `src/web-ui/src/**` 代码 → `make dev-*`（秒级）
- 改 `pyproject.toml`/`uv.lock`/`package.json`/Dockerfile/部署清单 → `bash scripts/local-update.sh`（慢但重建镜像）
- 改完依赖后想再开热重载 → 重新 `make dev-api`（会重新 patch）

> 💡 `local-update.sh` 开头会**自动检测并关闭热重载残留**（api 的 hostPath patch + 本地 vite），无需手动 `make dev-api-off`。两者可混用，不冲突。

> ⚠️ `make dev-api` 会以 root 运行 api 容器（绕过 hostPath 文件权限），仅限开发环境。`off` 会还原。

**WSL2 mirrored 模式下 vite 5173 启动报 "Port already in use"**：多半是 Windows 侧有过期的 `netsh portproxy` 规则占着 5173（`ss`/`lsof` 在 WSL 内看不到）。查并删：
```powershell
# Windows PowerShell（管理员）
netsh interface portproxy show all
netsh interface portproxy delete v4tov4 listenport=5173 listenaddress=127.0.0.1
```

### WSL2 + Windows 宿主机访问（mirrored 网络模式）

若 k3s 跑在 WSL2 内、且 WSL2 用 **mirrored 网络模式**（特征：`/etc/resolv.conf` 的 nameserver 是 `10.255.255.254`，`eth` 网卡 IP 与 Windows 物理网卡同段），Windows 浏览器**无法直接访问 k3s 的 NodePort**（如 `localhost:30082`）。原因：k3s NodePort 经 kube-proxy 的 iptables/nftables DNAT 转发，不是普通 `listen`，mirrored 模式的端口自动转发机制对这类 DNAT 端口支持不可靠。

**解决：用 `kubectl port-forward` 起一个真实 `listen` 的转发**，mirrored 模式会把它可靠地暴露给 Windows localhost。注意 `port-forward` 必须脱离当前 shell 会话常驻，否则终端关闭即断：

```bash
# 前端：Windows 浏览器访问 http://localhost:8080
setsid nohup kubectl port-forward -n gaia svc/gaia-web-ui 8080:80 </dev/null >/tmp/gaia-webui-pf.log 2>&1 & disown

# 后端 API：Windows 访问 http://localhost:8000/docs（Swagger）或 /health
setsid nohup kubectl port-forward -n gaia svc/gaia-api 8000:8000 </dev/null >/tmp/gaia-api-pf.log 2>&1 & disown

# 验证（WSL 内测通即代表 Windows 侧也可达）
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/   # 期望 200
ss -tlnp | grep 8080                                    # 确认 listen
```

> `setsid` + `nohup` + `disown` 三重保障让 port-forward 脱离会话常驻，关闭 WSL 终端不会被杀；但**重启 WSL / 重启 Windows 会断**，需重新执行。`make pf-*` 默认前台运行（适合临时调试），需常驻请用上面的 `setsid nohup` 写法。

**若 Windows 浏览器仍访问不了 `localhost:8080`**，多半是 Windows 防火墙拦截。在管理员 PowerShell 放行：
```powershell
New-NetFirewallRule -DisplayName "WSL Gaia" -Direction Inbound -LocalPort 8080,8000 -Protocol TCP -Action Allow
```

> 注：k3s NodePort（如 `30082`）对 WSL 内部访问正常（`curl 127.0.0.1:30082` 返回 200），只是对 Windows 宿主机不可达，所以才需要 port-forward 中转。若 WSL2 是传统 NAT 模式（非 mirrored），Windows 侧用 WSL 的 eth0 IP 访问 NodePort 通常即可，无需此节。

## 启动顺序

部署清单通过 initContainer + init Job 保证启动顺序，无需手动操作：

```
postgres (就绪) → migrate Job (alembic upgrade head) → api (就绪)
                ↘ gravitino (就绪) → gravitino-init Job (创建 metalake) → trino (initContainer 等 metalake)
                ↘ rustfs (就绪) ↗                                     ↘ seatunnel
doris-fe (就绪) → doris-be (initContainer 等 FE → 幂等 ADD BACKEND FQDN → start_be)
```

**关键自动化**：
- **Doris BE 自动注册**：BE 的 initContainer 等 FE 就绪后，幂等执行 `ALTER SYSTEM ADD BACKEND 'gaia-doris-be-0.gaia-doris-be:9050'`（FQDN 模式，Pod 重建 IP 变化不影响）。已注册则跳过。
- **Gravitino metalake 自动创建**：`gravitino-init` Job 等 Gravitino 健康后，幂等创建 metalake `ontology`（代码写死的平台级常量）。Trino 的 initContainer 等 metalake 存在后才启动，避免 `Metalake ontology not exists` 错误。

## 清理

```bash
make k8s-delete      # 删除整个 gaia namespace（含 PVC，不可恢复）
```
