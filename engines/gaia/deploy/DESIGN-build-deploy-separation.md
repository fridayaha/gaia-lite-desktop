# Gaia 多架构构建与部署分离设计

> 状态：设计稿（待评审）
> 目标：让 Gaia 的构建与部署彻底解耦，支持 x86_64 + arm64 / k3s + 标准 k8s，可复用、可参数化，并预留离线场景扩展点。

---

## 一、现状问题（为什么不能开箱即用）

当前 k3s 部署是在本机一步步踩坑调出来的，存在 5 类"本机特有"硬编码，换机器就断：

| # | 问题 | 现状 | 后果 |
|---|------|------|------|
| 1 | **镜像来源** | 本机 `docker build` + `docker save \| k3s ctr import` 手动灌 containerd | 新机器无镜像、无构建环境（那台 EulerOS docker 18.09 跑不了 buildx） |
| 2 | **构建与部署未分离** | `Makefile` 的 `docker-all` 只 build 不产出制品；`deploy.sh` 假设镜像已在节点 | 无法在 CI 构建、在目标机部署 |
| 3 | **单架构** | 4 个自有镜像只 build amd64；`docker save` 只能存单架构 | arm64 机器无法用 |
| 4 | **硬编码参数** | Pod 网段 `10.42.0.0/16`、StorageClass 隐式依赖 `local-path`、NodePort 30082、镜像 tag `:latest` | 换集群网段/存储类/端口冲突就废 |
| 5 | **官方镜像多架构不一致** | Doris/Kafka/Gravitino 等官方镜像多架构支持参差，需逐一确认 | arm64 节点拉到 amd64 镜像运行崩溃 |

---

## 二、设计目标与边界

### 目标（用户明确）
1. **B + D 组合**：支持 k3s 和标准 k8s；x86_64 和 arm64 都要支持
2. **build + deploy 分离**：构建阶段只把自有服务打成 `.tar.gz`（不含 docker 已有的官方镜像）；特殊 jar 依赖等可放进 tar.gz 或在线下载
3. **尽量参数化**
4. **预留离线场景**（暂不实现，留扩展点）

### 边界与约束

| 维度 | 范围 | 说明 |
|------|------|------|
| 架构 | linux/amd64 + linux/arm64 | 通过 `docker buildx` 多架构构建，产出 OCI fat manifest |
| K8s 发行版 | k3s + 标准 k8s（ACK/EKS/自建） | 清单不依赖 k3s 专有特性（local-path StorageClass 参数化） |
| 网络 | 联网（可拉 docker.io + pypi + npm） | 离线为预留扩展点 |
| 官方镜像 | 直接从公共 registry 拉 | 不打包进 tar.gz，由部署侧拉取 |

### 官方镜像多架构支持矩阵（已核实）

| 镜像 | amd64 | arm64 | 备注 |
|------|:---:|:---:|------|
| `apache/doris:fe-4.0.5` / `be-4.0.5` | ✅ | ✅ | 4.0.4 曾有只发 arm64 的 bug，4.0.5 已修；基于 Ubuntu 22.04 |
| `apache/gravitino:1.3.0` | ✅ | ⚠️ 待核 | 需 `docker manifest inspect` 实测；若无 arm64 则 arm64 集群 Gravitino 不可用 |
| `apache/kafka:4.3.0` | ✅ | ✅ | KRaft 模式，官方多架构 |
| `apache/seatunnel:2.3.13` | ✅ | ⚠️ 待核 | 需实测 |
| `ngosang/timescaledb-postgis:2.24.0-pg16-postgis3.6` | ✅ | ⚠️ 待核 | 社区镜像，需实测 |
| `rustfs/rustfs:latest` | ✅ | ⚠️ 待核 | 需实测 |
| `neo4j:5-community` | ✅ | ✅ | 官方多架构 |
| `postgres:16-alpine` | ✅ | ✅ | 官方多架构 |
| `nginx:alpine` | ✅ | ✅ | 官方多架构 |
| `kestra/kestra:latest` | ✅ | ✅ | Java 应用，多架构 |

**待核实项**（arm64 支持存疑）：构建前用 `docker manifest inspect <img>` 逐个确认；不支持的在 arm64 集群降级或禁用对应组件（如 Gravitino 无 arm64 则 Catalog 层不可用，需文档标注）。

---

## 三、整体架构：build → artifact → deploy

```
┌─────────────────────────────────────────────────────────────────┐
│  构建阶段（CI runner / 开发机，联网，有 docker buildx + QEMU）     │
│  make package VERSION=0.1.0                                     │
│                                                                 │
│  1. docker buildx build --platform linux/amd64,linux/arm64      │
│       --output type=oci,dest=images/gaia-<svc>.tar              │
│       4 个自有镜像 → 导出 OCI tar（含 fat manifest，双架构）       │
│  2. 打包部署制品 gaia-deploy-0.1.0.tar.gz：                       │
│       ├── manifests/        (k8s 清单模板，${VAR} 占位符)         │
│       ├── scripts/          (deploy.sh / preflight.sh / load-images.sh)
│       ├── images/           (4 个自有镜像的 OCI tar，多架构)       │
│       ├── jars/             (特殊 jar 依赖，如国产库 JDBC)        │
│       └── VERSION / SHA256SUMS                                  │
└────────────────────────┬────────────────────────────────────────┘
                         │  gaia-deploy-0.1.0.tar.gz（可 scp / 下载）
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  部署阶段（目标集群，有 kubectl，可联网拉官方镜像）                │
│  tar xzf gaia-deploy-0.1.0.tar.gz && cd gaia-deploy-0.1.0       │
│  cp .env.local.example .env.local && vi .env.local              │
│  bash scripts/deploy.sh 0.1.0                                   │
│                                                                 │
│  1. load-images.sh：ctr images import images/*.tar（自有镜像）    │
│  2. preflight：检查 kubectl / 集群 / StorageClass / 端口 / 网段   │
│  3. envsubst 清单模板（注入 VERSION/网段/StorageClass 等）        │
│  4. kubectl apply（infra → 等就绪 → migrate → services → apps）  │
│  5. wait 就绪                                                    │
│  官方镜像（postgres/doris/...）由 containerd 在线拉取              │
└─────────────────────────────────────────────────────────────────┘
```

**核心原则**：
- 构建机与部署机**完全解耦**——部署机不需要 docker、不需要源码、不需要 buildx
- 制品 `gaia-deploy-<ver>.tar.gz` 是**自包含的部署单元**（自有镜像在包内，官方镜像在线拉）
- 同一制品可部署到任意架构的任意 k8s 集群（containerd 按 fat manifest 自动选架构）
- **不推镜像仓库**：自有镜像走 OCI tar 导入，不依赖任何 registry

---

## 四、构建阶段设计

### 4.1 多架构构建 + OCI tar 导出

用 `docker buildx` 一次构建双架构，**导出 OCI tar**（含 fat manifest，不推 registry）：

```bash
# Makefile 核心目标（每个自有镜像导出一个多架构 OCI tar）
docker buildx build \
  --builder gaia-multiarch \
  --platform linux/amd64,linux/arm64 \
  --output type=oci,dest=dist/images/gaia-api-$(VERSION).tar \
  -t unionagents/gaia-api:$(VERSION) \
  -f Dockerfile .
```

**已验证的技术链路**（2026-07-21 本机实测）：
- `--output type=oci,dest=x.tar` 产出含 `application/vnd.oci.image.index.v1+json`（fat manifest）的 OCI layout tar
- `k3s ctr images import x.tar` 导入后 containerd 显示 `linux/amd64,linux/arm64`，按节点 arch 自动选
- **不能用 `docker save`**：它只存单架构，多架构会丢

**构建环境要求**（本机已就绪）：
- Docker 29+ + containerd image store（`/etc/docker/daemon.json` 的 `features.containerd-snapshotter: true`）
- `docker-buildx` 包（Ubuntu 24.04: `apt install docker-buildx`）
- QEMU 多架构模拟（`docker run --privileged --rm tonistiigi/binfmt --install all`）
- builder 用 `docker-container` driver + `--driver-opt network=host` + BuildKit registry mirror 配置（解决 docker.io 访问）

**4 个自有镜像的构建上下文**（注意不一致，需统一）：

| 镜像 | Dockerfile | 当前构建上下文 | 统一为 |
|------|-----------|--------------|--------|
| gaia-api | `Dockerfile` | `.`（仓库根） | `.` |
| gaia-trino | `Dockerfile.trino` | `.`（仓库根） | `.` |
| gaia-better-auth | `auth-server/Dockerfile` | `auth-server/`（子目录）⚠️ | `.`（改 Dockerfile 内 COPY 路径） |
| gaia-web-ui | `src/web-ui/Dockerfile` | `.`（仓库根） | `.` |

> **改造点**：`auth-server/Dockerfile` 当前 `COPY package.json` 假设上下文是 `auth-server/`，但 P1-D 改造后已写成 `COPY auth-server/package.json`（上下文是仓库根）。需确认 4 个 Dockerfile 的 COPY 路径都以仓库根为上下文，统一 `docker buildx build -f <Dockerfile> .`。

### 4.2 制品打包（gaia-deploy-<ver>.tar.gz）

**只打自有制品，不含官方镜像**（用户明确要求）。tar.gz 内容：

```
gaia-deploy-0.1.0.tar.gz
├── VERSION                          # 版本号
├── SHA256SUMS                       # 所有文件校验和
├── manifests/                       # k8s 清单模板（含 ${VAR} 占位符）
│   ├── namespace.yaml
│   ├── infra/
│   │   ├── postgres-config.yaml
│   │   ├── postgres.yaml
│   │   ├── rustfs.yaml
│   │   ├── gravitino.yaml
│   │   ├── gravitino-init.yaml
│   │   ├── doris-config.yaml        # ${POD_CIDR} 占位
│   │   ├── doris.yaml
│   │   ├── trino-config.yaml
│   │   ├── trino.yaml
│   │   ├── kafka.yaml
│   │   ├── seatunnel-config.yaml
│   │   ├── seatunnel-entrypoint.yaml
│   │   ├── seatunnel.yaml
│   │   ├── kestra.yaml              # 默认 replicas: 0
│   │   └── neo4j.yaml               # 默认 replicas: 0
│   ├── services/
│   │   ├── migrate.yaml
│   │   ├── api.yaml
│   │   └── better-auth.yaml
│   └── apps/
│       └── web-ui.yaml
├── scripts/
│   ├── deploy.sh                    # 部署主脚本
│   ├── preflight.sh                 # 预检脚本
│   ├── envsubst-all.sh              # 清单模板渲染
│   └── load-images.sh               # 离线镜像导入（预留，默认空操作）
├── config/
│   ├── nginx/web-ui.conf            # web-ui nginx 配置（已烤进镜像，此处仅备份）
│   └── trino/gravitino/             # Trino 插件（已烤进镜像，此处仅备份）
├── jars/                            # 特殊 jar 依赖（ADR-014 国产库 JDBC，可选）
│   └── README.md                    # 说明：放到 SeaTunnel Pod 的挂载路径
├── secret.yaml.template             # Secret 模板（envsubst）
└── .env.local.example               # 配置模板
```

**不打进 tar.gz 的**：
- 官方镜像（postgres/doris/gravitino/kafka/...）— 部署时在线拉
- 源码（src/、auth-server/、tests/）— 已打进镜像
- docker 构建产物（dist/、node_modules/、.venv/）— 已打进镜像

### 4.3 Makefile 改造

新增 `package` 目标，串联 build + 打包：

```makefile
VERSION ?= 0.1.0
PLATFORMS ?= linux/amd64,linux/arm64

# 多架构构建 + OCI tar 导出（不推 registry）
.PHONY: docker-buildx
docker-buildx:
	@mkdir -p dist/images
	@for svc in api trino better-auth web-ui; do \
	  docker buildx build --builder gaia-multiarch \
	    --platform $(PLATFORMS) \
	    --output type=oci,dest=dist/images/gaia-$$svc-$(VERSION).tar \
	    -t unionagents/gaia-$$svc:$(VERSION) \
	    -f $$(dfpath $$svc) . ; \
	done

# 打包部署制品
.PHONY: package
package: docker-buildx
	bash scripts/package.sh $(VERSION)
	# 产出 dist/gaia-deploy-$(VERSION).tar.gz
```

> **保留 `docker-all`（单架构本地构建）**：本地开发调试仍用 `make docker-all` + `k3s ctr import`，快速迭代不走 buildx。

---

## 五、部署阶段设计

### 5.1 参数化清单模板

所有集群差异通过 `${VAR}` 占位符 + `envsubst` 注入。占位符清单：

| 占位符 | 默认值 | 说明 | 来源 |
|--------|--------|------|------|
| `${VERSION}` | `0.1.0` | 镜像 tag（自有镜像已导入本地，官方镜像用 `imagePullPolicy: IfNotPresent` 避免重拉） | deploy.sh 参数 |
| `${POD_CIDR}` | `10.42.0.0/16` | Pod 网段（Doris priority_networks） | .env.local（preflight 自动探测） |
| `${STORAGE_CLASS}` | `local-path` | PVC 存储类 | .env.local（preflight 自动探测） |
| `${NODE_PORT_WEB_UI}` | `30082` | web-ui NodePort | .env.local |
| `${IMAGE_PULL_POLICY_INFRA}` | `IfNotPresent` | 官方镜像拉取策略（在线场景 IfNotPresent，离线场景 Never） | .env.local |
| `${IMAGE_PULL_POLICY_SERVICES}` | `Never` | 自有镜像拉取策略（已 ctr import 到本地，必须 Never） | .env.local |

**清单改造**：
- 所有 `image: unionagents/gaia-api:latest` → `image: unionagents/gaia-api:${VERSION}`（不带 registry 前缀，用本地导入的镜像）
- 所有 `imagePullPolicy`（services/apps）→ `imagePullPolicy: ${IMAGE_PULL_POLICY_SERVICES}`（**默认 Never**，因为自有镜像已 import 到 containerd）
- 所有 `imagePullPolicy`（infra）→ `imagePullPolicy: ${IMAGE_PULL_POLICY_INFRA}`（默认 IfNotPresent，在线拉官方镜像）
- `doris-config.yaml` 的 `priority_networks: 10.42.0.0/16` → `priority_networks: ${POD_CIDR}`
- 所有 PVC 的 `storageClassName: local-path` → `storageClassName: ${STORAGE_CLASS}`
- `web-ui.yaml` Service 的 `nodePort: 30082` → `nodePort: ${NODE_PORT_WEB_UI}`

### 5.2 preflight 预检

部署前自动探测集群环境，减少踩坑：

```bash
scripts/preflight.sh
  ✅/❌ kubectl 可用 + 当前 context
  ✅/❌ 目标 namespace 可创建
  ⚠️ 探测 Pod CIDR（kubectl get nodes -o jsonpath；与 ${POD_CIDR} 比对，不符则提示）
  ⚠️ 探测 StorageClass（kubectl get sc；若 ${STORAGE_CLASS} 不存在则提示可用列表）
  ⚠️ 探测 NodePort ${NODE_PORT_WEB_UI} 是否被占
  ✅/❌ .env.local 必填项齐全
  ℹ️  集群架构（amd64/arm64/mixed）— 提示哪些组件可能不可用
```

preflight **只警告不阻断**（除必填项缺失），让用户知情后继续。

### 5.3 deploy.sh 流程

```bash
bash scripts/deploy.sh <VERSION>

[0] 加载 .env.local + preflight
[1] load-images.sh：ctr images import images/*.tar（自有镜像导入 containerd）
[2] namespace + registry-secret（在线拉官方镜像可能需要；离线场景空操作）
[3] envsubst secret.yaml → apply
[4] envsubst infra/*.yaml → apply（含 doris-config 网段注入）
[5] wait PG ready → wait gravitino-init Job complete
[6] envsubst services/*.yaml → apply（migrate + api + better-auth）
[7] wait migrate Job complete
[8] envsubst apps/*.yaml → apply（web-ui）
[9] wait api + web-ui ready
```

**与当前 deploy.sh 的差异**：
- **新增 load-images 步骤**：部署前先把自有镜像 OCI tar 导入 containerd（k3s: `k3s ctr images import`；标准 k8s: `sudo ctr -n k8s.io images import`）
- 清单从 tar.gz 内的 `manifests/` 读取（不再依赖仓库源码路径）
- 所有占位符统一 envsubst（不再 sed 逐文件替换）
- 加 preflight
- imagePullPolicy 参数化（自有镜像默认 Never，官方镜像默认 IfNotPresent）
- **load-images.sh 需 root 权限**（ctr 操作 containerd），deploy.sh 检测非 root 时提示用 sudo

### 5.4 .env.local.example 改造

```bash
# --- 集群参数（preflight 可自动探测，也可手动指定）---
POD_CIDR="10.42.0.0/16"              # k3s 默认；标准 k8s 视实际
STORAGE_CLASS="local-path"           # k3s 自带；标准 k8s 用集群已有 SC
NODE_PORT_WEB_UI="30082"
IMAGE_PULL_POLICY_INFRA="IfNotPresent"    # 官方镜像（在线拉）
IMAGE_PULL_POLICY_SERVICES="Never"        # 自有镜像（已 ctr import 到本地）
# 离线场景：IMAGE_PULL_POLICY_INFRA 也改 Never，且需把官方镜像也导入

# --- Gaia 业务配置（同当前）---
GAIA_PG_USER / GAIA_PG_PASSWORD / GAIA_PG_DATABASE
GAIA_S3_ACCESS_KEY / GAIA_S3_SECRET_KEY
GAIA_BETTER_AUTH_SECRET / GAIA_PROVISION_TOKEN
GAIA_DORIS_USER / GAIA_DORIS_PASSWORD
GAIA_NEO4J_PASSWORD
GAIA_*_API_KEY（AI provider，可选）

# --- KubeConfig ---
KUBECONFIG_PATH="$HOME/.kube/config"
```

> **不再需要 REGISTRY / REGISTRY_USERNAME / REGISTRY_PASSWORD**：自有镜像走 OCI tar 导入，不推不拉仓库。官方镜像从公共 registry 拉取（可通过 containerd mirror 加速，不需认证）。

---

## 六、离线场景预留（暂不实现，但与在线方案统一）

在线方案的自有镜像已走 OCI tar 导入，离线场景只需**额外把官方镜像也导入**，逻辑完全一致：

1. **构建侧**：`make package OFFLINE=1` 额外执行 `docker pull` + `docker buildx --output type=oci` 把官方镜像也导出进 `images/official-*.tar`
2. **部署侧**：`scripts/load-images.sh` 检测 `OFFLINE=1` 时连官方镜像 tar 一起 `ctr import`
3. **清单侧**：`.env.local` 设 `IMAGE_PULL_POLICY_INFRA=Never`（官方镜像也不拉）

> 关键技术点（已验证）：多架构镜像必须用 `docker buildx --output type=oci,dest=x.tar` 导出 OCI layout（含 fat manifest），**不能用 `docker save`**（只存单架构）。导入用 `ctr images import`（支持多架构 fat manifest，按节点 arch 自动选）。

---

## 七、改造任务清单

### 构建侧
- [ ] B1. 统一 4 个 Dockerfile 的构建上下文为仓库根（核对 `auth-server/Dockerfile` 的 COPY 路径）
- [ ] B2. Makefile 新增 `docker-buildx`（多架构构建+推 registry）+ `package`（打包 tar.gz）
- [ ] B3. 新建 `scripts/package.sh`（收集清单/脚本/jar → tar.gz）
- [ ] B4. 核实官方镜像 arm64 支持（`docker manifest inspect` 逐个验证）

### 清单侧（参数化）
- [ ] M1. 所有清单 `image:` 改 `${REGISTRY}/...:${VERSION}` 占位
- [ ] M2. `imagePullPolicy` 改 `${IMAGE_PULL_POLICY_*}` 占位（infra/services 分离）
- [ ] M3. `doris-config.yaml` 网段改 `${POD_CIDR}`
- [ ] M4. 所有 PVC `storageClassName` 改 `${STORAGE_CLASS}`
- [ ] M5. `web-ui.yaml` NodePort 改 `${NODE_PORT_WEB_UI}`
- [ ] M6. `deploy/ci/secret.yaml` 改 envsubst 模板（已是，确认占位符一致）

### 部署侧
- [ ] D1. 新建 `scripts/preflight.sh`（预检）
- [ ] D2. 重写 `scripts/deploy.sh`（读 tar.gz 内清单 + envsubst + preflight）
- [ ] D3. 更新 `.env.local.example`（加集群参数）
- [ ] D4. 新建 `scripts/envsubst-all.sh`（统一渲染）
- [ ] D5. 预留 `scripts/load-images.sh`（空实现 + 注释）

### 文档
- [ ] DOC1. 更新 `deploy/README.md`（build/deploy 分离流程 + 多架构说明）
- [ ] DOC2. 本设计文档定稿后纳入 `docs/architecture/`

---

## 八、构建环境就绪状态（2026-07-21 本机验证）

| 项 | 状态 | 说明 |
|---|---|---|
| Docker Engine | ✅ 29.1.3 | containerd image store 已启用（`features.containerd-snapshotter: true`）|
| buildx | ✅ 0.30.1 | `apt install docker-buildx` |
| QEMU arm64 模拟 | ✅ | `tonistiigi/binfmt --install all` |
| multiarch builder | ✅ | `docker-container` driver + `network=host` + docker.1ms.run mirror |
| 双架构构建+OCI导出 | ✅ 已验证 | gaia-better-auth 双架构 OCI tar 218MB，含 fat manifest |
| ctr import 多架构 | ✅ 已验证 | `k3s ctr images import` 后显示 `linux/amd64,linux/arm64` |

**待用户确认**：无（镜像仓库问题已通过 OCI tar 方案解决，不再需要 registry）
**待实测**：4 个官方镜像（Gravitino/SeaTunnel/RustFS/timescaledb-postgis）的 arm64 支持，在 package 流程里自动 `docker manifest inspect` 检测，无 arm64 的组件 preflight 警告 + replicas 设 0。
