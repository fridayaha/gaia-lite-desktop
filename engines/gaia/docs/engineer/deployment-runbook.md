# Gaia 部署 Runbook

> **读者**：负责把 Gaia 部署到全新环境的运维 / DevOps 工程师
> **目标**：从一台干净机器开始，把 Gaia 跑起来、初始化完成、并通过部署完成性检查
> **代码核实**：2026-07-20 对照 `docker-compose.yml` / `Dockerfile` / `scripts/` / `src/ontology/main.py`

---

## 0. 部署理念：把复杂留给自己，把简单留给运维

Gaia 由 8 个数据引擎 + 1 个 Python API + 1 个 Node Auth 服务 + 1 个 React 前端组成，
拓扑不简单。本 Runbook 的设计原则：

- **一条命令拉起全部基础设施**：`docker compose up -d`，依赖关系由 compose `depends_on` + healthcheck 自动编排
- **schema 自动迁移**：`migrate` 一次性 init 容器跑 `alembic upgrade head`，API 容器 `depends_on` 它
- **权限默认容器自动 seed**：API 启动时 `permission_bootstrap` 自动建默认组织 / Space / Project / 内置角色 / 系统标记，**无需人工初始化权限**
- **Gravitino metalake / Iceberg namespace / Doris DB** 由后端代码在首次使用时幂等创建（`IcebergStore.ensure_warehouse_bucket` 等），**无需预置脚本**
- **一键健康检查**：`scripts/healthcheck.sh` 一次探活全部 11 个组件 + API

> ⚠️ 现存 `scripts/bootstrap_all.sh` 是早期手写编排脚本，**已被 docker-compose 的 `depends_on` + healthcheck + 后端幂等自举取代**，新环境部署**不需要**再跑它（保留仅作排查参考）。它输出里提到的 `Iceberg REST:8181` 是过时信息，实际是 Gravitino 内置 `9001`。

---

## 1. 环境要求

### 1.1 硬件

| 资源 | 最低 | 推荐 | 说明 |
|------|------|------|------|
| CPU | 4 核 | 8 核 | Doris BE / Trino / Gravitino 是 JVM，吃 CPU |
| 内存 | 10 GB | 16 GB | 见下表分配；Doris BE 单独 3g |
| 磁盘 | 40 GB | 100 GB+ | Iceberg 全量明细 + Doris 索引 + PG 元数据 |
| 网络 | 可访问公网拉镜像 | 或内部镜像仓库 | 国内建议配 Docker 镜像加速 |

### 1.2 内存预算（docker-compose 已配 mem_limit）

| 服务 | mem_limit | 说明 |
|------|-----------|------|
| rustfs | 1g | S3 对象存储 |
| postgres（PostGIS+TimescaleDB） | 1g | 元数据 + 时空 |
| gravitino（主 API + Iceberg REST aux service） | 1g | 单进程内嵌 iceberg-rest（aux service 模式） |
| doris-fe | 1g | JVM |
| doris-be | **3g** | ANN 向量索引，吃内存 |
| trino | 1.5g | JVM |
| kafka | 512m | KRaft 单节点 |
| seatunnel-master / worker | 各 512m | JVM |
| better-auth | （node，轻量） | |
| api | 512m | Python |
| kestra | 1g | JVM（可选，Pipeline Builder） |
| neo4j（profile=graph） | 1g | 可选，图推理 |
| **合计（全开）** | **≈ 12 GB** | 不含 neo4j ≈ 11g |

> 单机内存 < 12 GB 时，**先关掉可选服务**：kestra（Pipeline Builder 可后置）、neo4j（图推理可后置）。核心链路（PG + Gravitino + Iceberg + Doris + Trino + API）≈ 8g。

### 1.3 软件

| 软件 | 版本 | 用途 |
|------|------|------|
| Docker Engine | 24+ | 容器运行时 |
| Docker Compose | v2（`docker compose` 子命令） | 编排 |
| Git | 任意 | 拉代码 |
| curl / jq | 任意 | 健康检查、调试 |
| （可选）uv | 最新 | 仅本地源码开发模式需要 |

### 1.4 国内镜像加速（推荐）

```bash
# /etc/docker/daemon.json
{
  "registry-mirrors": ["https://docker.1ms.run"]
}
# 然后 sudo systemctl restart docker
```

> 仅使用 `docker.1ms.run`，其他源（hub.rat.dev 等）实测不稳定。
> Python 依赖在 `Dockerfile` 里已配清华 TUNA 源，无需额外设置。

---

## 2. 打包（构建镜像）

Gaia 用 3 个自建镜像，其余 8 个引擎用官方镜像（`docker compose up` 自动拉）。

### 2.1 自建镜像清单

| 镜像 | Dockerfile | 说明 |
|------|-----------|------|
| `gaia-api` | `./Dockerfile` | Python 3.12 多阶段构建，含 Alembic |
| `gaia-better-auth` | `./auth-server/Dockerfile` | Node 22 + Hono，JWT 签发 |
| （前端 web-ui） | 开发态 `vite dev`，生产态 `vite build` 出静态产物由 nginx 托管 | 本 Runbook 用开发态直连，生产部署见 §2.4 |

### 2.2 一键构建

```bash
cd /path/to/gaia

# 构建 API + Auth 两个镜像（docker compose up 时也会自动 build，
# 显式 build 用于预构建推到镜像仓库的场景）
docker compose build api better-auth
```

`Dockerfile` 已内置清华 TUNA 源加速 + 多阶段构建（builder 装 3rd-party deps，runtime 只拷 .venv），首次构建约 3-5 分钟（取决于网络），二次构建命中 layer cache < 1 分钟。

### 2.3 推送到镜像仓库（多机部署 / CI）

```bash
REGISTRY=registry.example.com/gaia
VERSION=$(grep '^version' pyproject.toml | head -1 | sed 's/.*"\(.*\)".*/\1/')  # 0.1.0

docker tag gaia-api:latest       $REGISTRY/api:$VERSION
docker tag gaia-better-auth:latest $REGISTRY/better-auth:$VERSION
docker push $REGISTRY/api:$VERSION
docker push $REGISTRY/better-auth:$VERSION
```

> 多机部署时，修改 `docker-compose.yml` 里 `api` / `better-auth` 的 `build:` 为 `image: $REGISTRY/...:$VERSION`。

### 2.4 前端生产构建（可选）

开发态前端用 `vite dev`（端口 5173）。若要生产部署：

```bash
cd src/web-ui
npm ci
npm run build      # 产物在 src/web-ui/dist/
# 用任意静态服务器托管 dist/，反代 /api → http://<api-host>:8000
```

> 前端构建时需设环境变量 `VITE_AUTH_ENABLED` / `VITE_BETTER_AUTH_URL`（见 `src/web-ui/.env.example`）。

---

## 3. 部署（全新环境拉起）

### 3.1 准备配置文件

```bash
cd /path/to/gaia

# 1. 后端配置（必填：AI_MODEL + 对应 provider key；生产必填：BETTER_AUTH_SECRET）
cp .env.example .env.local
# 编辑 .env.local：
#   AI_MODEL=deepseek:deepseek-chat      # 换成你的模型
#   DEEPSEEK_API_KEY=sk-xxxx             # 对应 provider 的 key
#   AUTHZ_DEV_MODE=true                  # 首次部署建议先 true（见 §6）
#   BETTER_AUTH_SECRET=<openssl rand -base64 32>   # 启用 Better Auth 后必填

# 2. docker-compose 读 .env（compose 默认读 .env，可软链）
ln -sf .env.local .env
```

> `.env.local` 已在 `.gitignore`，不会误提交。`.env.example` 里全是占位符。

### 3.2 一键拉起全部服务

```bash
docker compose up -d
```

这一条命令做了以下所有事（**全部自动**）：

1. 拉取 8 个官方引擎镜像（首次约 5-10 分钟，国内走 `docker.1ms.run`）
2. 构建 `gaia-api` + `gaia-better-auth` 镜像
3. 按 `depends_on` + healthcheck 顺序启动：
   - `postgres`（跑 `init-pg-extensions.sql` 建 PostGIS/TimescaleDB/pgcrypto + `init-pg-schema.sql` 建 gravitino_store schema）
   - `rustfs`（S3）
   - `gravitino`（等 postgres + rustfs healthy）
   - `doris-fe` → `doris-be`
   - `trino`（等 gravitino healthy）
   - `kafka`
   - `seatunnel-master` → `seatunnel-worker`
   - `migrate`（一次性，等 postgres healthy → 跑 `alembic upgrade head` 建 19 个业务表 revision → 退出）
   - `api`（等 postgres healthy + migrate 成功退出）
   - `better-auth`（等 postgres healthy）
   - `kestra`（等 postgres healthy）

### 3.3 可选服务：图推理（Neo4j）

Neo4j 用 `profiles: ["graph"]` 标记，默认不启动。需要图关联推理时：

```bash
docker compose --profile graph up -d neo4j
```

首次启动后需在 `.env.local` 加 `NEO4J_PASSWORD`（默认 `change-me`，生产必改）。
API 通过 `capabilities` 自动探测 Neo4j 是否在线，离线时图相关功能降级（不报错）。

### 3.4 服务拓扑与依赖关系图

这是部署时最该先看的一张图。docker compose 的 `depends_on` + healthcheck 会**按此图自动编排启动顺序**，你无需手动控制，但理解分层和依赖有助于：排障定位、按场景裁剪服务（§3.5）、滚动升级微服务（§5.4）。

```
┌─────────────────────────────────────────────────────────────────────┐
│  接入层（用户 / Agent 触达）                                          │
│  ┌──────────────┐   ┌──────────────┐                                 │
│  │  web-ui:5173 │   │ better-auth  │ 🔧build  [生产鉴权可选]           │
│  │  (前端/静态)  │   │    :3000     │                                 │
│  └──────┬───────┘   └──────┬───────┘                                 │
│         │ :8000 API         │ JWT                                    │
└─────────┼───────────────────┼────────────────────────────────────────┘
          ▼                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│  应用层（Gaia 自建，升级频繁，见 §5.4）                                │
│  ┌────────────────┐   ┌────────────────┐                             │
│  │  api 🔧build   │   │ migrate 🔧build│ 一次性 init（alembic upgrade)  │
│  │  :8000 后端    │◀──│ 跑完即退       │ depends: postgres healthy     │
│  └────────────────┘   └────────────────┘                             │
└─────────┬───────────────────────────────────────────────────────────┘
          ▼ 读写元数据 / 调各 Layer（懒加载）
┌─────────────────────────────────────────────────────────────────────┐
│  数据层（持久化，升级少，官方镜像）                                     │
│                                                                       │
│  ┌────────────┐  ┌──────────┐                                        │
│  │ postgres   │  │  rustfs  │  基础存储（无依赖，最先起）                │
│  │ :5432      │  │  :9000   │  PostGIS+TimescaleDB / S3              │
│  │ PostGIS+TS │  │  RustFS  │                                        │
│  └─────┬──────┘  └────┬─────┘                                        │
│        │              │                                              │
│        ├──────────────┼─→ gravitino :8090/:9001 (主API + Iceberg REST)│
│        │              │     ├─→ trino :8080        (联邦查询)         │
│        │              │     └─→ seatunnel-master ─→ seatunnel-worker  │
│        │              │                   :5801      (数据搬运)        │
│        ├─→ doris-fe :8030/9030 ─→ doris-be :9050  (索引加速)          │
│        ├─→ kafka :9092        (实时同步总线，仅托管表 CDC 用)          │
│        ├─→ kestra :28080      (Pipeline Builder，可选)                │
│        └─→ neo4j :7474/7687   [profile=graph]  (图推理，可选)         │
└─────────────────────────────────────────────────────────────────────┘
```

**读图要点：**

| 要点 | 说明 |
|------|------|
| 🔧build vs 📦image | 🔧 = Gaia 自建镜像（api / better-auth / migrate），升级频繁；📦 = 官方镜像，几乎不动 |
| 分层 | 接入层 → 应用层 → 数据层。**上层依赖下层**，升级时下层不动、上层滚动即可 |
| 启动顺序 | postgres + rustfs 先起 → migrate 跑完退出 → api 才起；gravitino 等 pg+rustfs healthy；trino 等 gravitino healthy |
| 必选 vs 可选 | 必选：postgres / rustfs / gravitino / trino / api / migrate。可选：见 §3.5 场景裁剪表 |
| 依赖键 | `postgres` 是中枢（8 个服务依赖它）；`rustfs` 次之（gravitino 依赖） |

**docker compose 实际 depends_on 矩阵（编排引擎据此排启动序）：**

| 服务 | 依赖（condition） |
|------|------------------|
| postgres | — |
| rustfs | — |
| gravitino | postgres(healthy), rustfs(healthy) |
| trino | gravitino(healthy) |
| doris-fe | — |
| doris-be | doris-fe(healthy) |
| kafka | — |
| seatunnel-master | gravitino(healthy) |
| seatunnel-worker | seatunnel-master(started) |
| migrate | postgres(healthy) |
| better-auth | postgres(healthy) |
| api | postgres(healthy), migrate(completed_successfully) |
| kestra | postgres(healthy) |
| neo4j | — (profile=graph) |

> ⚠️ `migrate` 是一次性容器：`alembic upgrade head` 跑完正常退出（exit 0），`api` 用 `condition: service_completed_successfully` 等它。升级 Gaia 时若 schema 有新 revision，重跑 migrate 即可（见 §5.4）。

---

### 3.5 按场景裁剪服务（最小化部署）

并非所有场景都需要全量 12 个服务。按你的业务形态选最小集，省内存、省运维：

| 场景 | 必选服务 | 可省略 | 省后内存 |
|------|---------|--------|---------|
| **全功能**（托管+虚拟+Action+图推理） | 全部 12 个 + neo4j | — | — |
| **托管表 + Action 写回**（典型生产） | postgres, rustfs, gravitino, trino, doris-fe, doris-be, kafka, seatunnel-master, seatunnel-worker, api, migrate | kestra, neo4j, better-auth(Dev Mode) | -2g |
| **虚拟表只读联邦**（数据不搬进来） | postgres, rustfs, gravitino, trino, api, migrate | doris-fe, doris-be, kafka, seatunnel-*, kestra, neo4j | **-6.5g** |
| **纯本地开发 POC** | postgres, rustfs, gravitino, trino, api, migrate | 其余全部 | -6g+ |

**虚拟表只读联邦场景**（最常见的轻量场景）说明：

- 虚拟表（`storage_type=VIRTUAL`）查询走 **Trino 联邦直查外部源表**，不落地、不经 Doris/SeaTunnel/Kafka
- 省掉 Doris（-4g：fe 1g + be 3g）、SeaTunnel master+worker（-1g）、Kafka（-0.5g）、Kestra（-1g）
- 启动命令：
  ```bash
  docker compose up -d postgres rustfs gravitino trino api migrate
  # Dev Mode 免 better-auth；无 Action 写回免 doris/kafka/seatunnel
  ```
- API 启动后，OutboxExecutor 等后台任务会尝试连 Doris/Iceberg，连不上只 `warning` 不崩 app（container 懒加载 + 后台任务异常隔离）；且只读场景不产生 outbox 记录，不会刷错误日志
- **限制**：不能跑 Action 写回（VIRTUAL 目标本就禁止写）、不能用托管表加速、不能用 CDC 实时同步、不能用 Pipeline Builder

> 裁剪后用 `bash scripts/healthcheck.sh --no-optional` 检查（自动跳过 kestra/neo4j）。

---

## 4. 初始化（系统自动完成的部分）

**重要**：以下初始化全部由系统在启动时自动完成，**运维无需手动执行任何初始化脚本**。本节是说明"系统替你做了什么"，便于排查。

### 4.1 数据库 schema（Alembic 自动）

- `migrate` 容器跑 `alembic upgrade head`，应用 `alembic/versions/` 下 19 个 revision
- 业务表（public schema）的 DDL 单一真相源 = ORM 模型 + Alembic revision 链
- Gravitino 的 `gravitino_store` schema 由 `infra/gravitino-pg-schema.sql` 初始化（Gravitino 自管，不归 Alembic）

### 4.2 权限默认容器（API 启动时自动）

API 的 `lifespan` 启动钩子调用 `permission_bootstrap.bootstrap_default_containers()`，幂等地：

| 自动创建 | api_name | 说明 |
|---------|----------|------|
| 默认组织 | `org-default` | 单租户默认组织（Alembic migration 预置，bootstrap 防御性再 ensure） |
| 默认 Space | `default` | 1:1 绑定一个默认 Ontology `Default` |
| 默认 Project | `default` | 挂在默认 Space 下 |
| 11 个内置角色 | 见下表 | `is_builtin=True`，不可删 |
| 系统标记 | `org:org-default` | MAC 主体隔离标记，org 用户自动持有 |

**内置角色**（`src/ontology/core/permission_roles.py`）：

| 角色 | scope | 用途 |
|------|-------|------|
| `PLATFORM_ADMIN` | GLOBAL | 平台管理（`*`，但默认无数据访问） |
| `AUDIT_ADMIN` | GLOBAL | 只读审计日志 |
| `MARKING_ADMIN` | GLOBAL | 管理数据分级标记 |
| `SPACE_OWNER` / `SPACE_EDITOR` / `SPACE_VIEWER` / `SPACE_DISCOVERER` | SPACE | Space 级协作 |
| `OWNER` / `EDITOR` / `VIEWER` / `DISCOVERER` | PROJECT | 项目级协作（最常用） |

### 4.3 物理资源自举（首次使用时自动）

后端代码在首次调用时幂等创建（无需预置脚本）：

- **RustFS bucket** `ontology-warehouse`：`IcebergStore.ensure_warehouse_bucket`
- **Iceberg namespace** `ontology`：`IcebergStore.ensure_namespace`
- **Gravitino metalake** `ontology` + JDBC catalog `pg`：`GravitinoRegistry` 首次注册时
- **Doris 数据库**：`DorisIndexStore` 首次建索引表时

### 4.4 后台任务（API 启动时自动）

`lifespan` 还启动了 6 个后台循环（异常不崩 app，仅 warning 日志）：

- `OutboxExecutor`：消费 outbox → Doris 近实时同步 / 图投影 / webhook
- `SyncFlushScheduler`：ARCHIVE outbox 微批 → Iceberg MERGE
- `IcebergMaintenanceService`：周期 optimize / expire_snapshots
- `ConflictDetector`：审计 Doris 漏写
- `PipelineBuildReconciler`：对齐 PG 与 Kestra 状态
- outbox 7 天清理

---

## 5. 部署完成性检查

### 5.1 一键健康检查脚本

```bash
bash scripts/healthcheck.sh
```

输出示例（全绿即部署完成）：

```
[1/12] PostgreSQL        ✓ 5432  pg_isready
[2/12] RustFS (S3)       ✓ 9000  /health
[3/12] Gravitino API     ✓ 8090  /api/health
[4/12] Iceberg REST      ✓ 9001  /iceberg/v1/config   (Gravitino 内置，无 8181)
[5/12] Doris FE          ✓ 8030  /api/health
[6/12] Doris BE          ✓ 8040  /api/health
[7/12] Trino             ✓ 8080  /v1/info
[8/12] Kafka             ✓ 9092  topic list
[9/12] SeaTunnel Master  ✓ 5801  cluster members >= 2
[10/12] Better Auth      ✓ 3000  /health
[11/12] Kestra           ✓ 28080 /api/v1/configs
[12/12] Gaia API         ✓ 8000  /health
──────────────────────────────────
部署完成 ✓  12/12 组件健康
默认登录: http://localhost:5173 (前端) / http://localhost:8000/docs (API 文档)
```

> 该脚本是本 Runbook 配套新增的（见 §7 优化点）。Neo4j 若启用会单独探活，未启用跳过。

### 5.2 手动逐项检查（脚本失败时排查）

```bash
# 1. 容器状态（应全部 Up / healthy，migrate 应 Exited 0）
docker compose ps

# 2. migrate 容器日志（应看到 "Running upgrade" 链 + 无 ERROR）
docker compose logs migrate | tail -30

# 3. API 健康
curl -s http://localhost:8000/health        # → {"status":"ok"}

# 4. API 启动日志（应看到 "Permission default containers bootstrapped"）
docker compose logs api | grep -E "bootstrapped|ERROR|started"

# 5. 关键引擎探活
curl -s http://localhost:8090/api/health                     # Gravitino
curl -s http://localhost:9001/iceberg/v1/config              # Iceberg REST（不带 ?warehouse=，会 404）
curl -s http://localhost:8030/api/health                     # Doris FE
curl -s http://localhost:8080/v1/info | jq .starting         # Trino（应 false）

# 6. DB schema 已迁移
docker compose exec postgres psql -U ontology -d ontology -c "\dt public.*" | head -30
# 应看到 ontologies / object_types / object_state / outbox / users / roles ... 等业务表

# 7. 默认容器已 seed
docker compose exec postgres psql -U ontology -d ontology -c \
  "SELECT api_name FROM organizations; SELECT name FROM roles WHERE is_builtin;"
# 应看到 org-default + 11 个内置角色
```

### 5.3 功能冒烟（端到端验证）

```bash
# 创建一个本体（验证 API + PG + 权限链路）
curl -s -X POST http://localhost:8000/ontologies \
  -H "Content-Type: application/json" \
  -d '{"api_name":"smoke","display_name":"冒烟测试本体"}' | jq .

# 列出本体
curl -s http://localhost:8000/ontologies | jq '.[].api_name'
# 应包含 smoke + Default

# 清理
curl -s -X DELETE http://localhost:8000/ontologies/smoke
```

更完整的 E2E（含 Action 闭环 / Doris 索引 / 图推理）：

```bash
.venv/bin/python scripts/verify_e2e_full.py    # 需先本地装好 .venv（见 §8）
```

### 5.4 微服务滚动升级（前后端频繁发版）

Gaia 自建的 🔧 镜像（`api` / `better-auth` / `migrate`）和前端发版频繁，而 📦 数据引擎镜像几乎不动。升级原则：**只动上层，不动下层**，数据引擎（postgres/rustfs/gravitino/doris/trino/...）保持运行，避免数据卷重建和长时间不可用。

#### 5.4.1 后端 API 升级（最常见）

```bash
# 1. 拉新代码 + 重建 api 镜像（多阶段构建，只变上层 .venv，2-3 分钟）
git pull
docker compose build api

# 2. 若有新 Alembic revision，先跑 migrate（幂等，跳过已应用的）
docker compose run --rm migrate alembic upgrade head

# 3. 滚动重启 api（postgres/gravitino/doris 等不动，连接重连）
docker compose up -d api

# 4. 验证
curl -s http://localhost:8000/health        # 应 {"status":"ok"}
docker compose logs api --tail 30 | grep -iE "error|bootstrapped|started"
```

> **零停机要点**：`docker compose up -d api` 会先起新容器再停旧的（默认 `restart` 策略），API 有 healthcheck，期间请求会短暂 502（几百 ms）。需要真正零停机可在前面加 nginx 反代 + `docker compose up -d --no-deps --scale api=2 api` 灰度切流。

#### 5.4.2 Better Auth 升级

```bash
git pull
docker compose build better-auth
docker compose up -d better-auth
# 若 auth.ts 有 schema 变更，重跑迁移
docker compose exec better-auth npx @better-auth/cli migrate
```

#### 5.4.3 前端升级（web-ui）

开发态（`vite dev`）：`git pull` 后 Vite HMR 自动热更新，无需重启。

生产态（静态产物 + nginx）：
```bash
cd src/web-ui && npm ci && npm run build     # 重新出 dist/
# 把 dist/ 部署到 nginx 静态目录，或重建前端镜像
```

#### 5.4.4 版本一致性检查

升级前确认镜像 tag 与 `pyproject.toml` 的 version 一致，避免 schema/代码错配：
```bash
VERSION=$(grep '^version' pyproject.toml | head -1 | sed 's/.*"\(.*\)".*/\1/')
echo "代码版本: $VERSION"
docker compose images api | grep api         # 镜像 tag 应匹配
```

#### 5.4.5 升级 Checklist

```
[ ] git pull 拉新代码
[ ] docker compose build api (better-auth 若改)
[ ] docker compose run --rm migrate alembic upgrade head  (有新 migration 才需)
[ ] docker compose up -d api (better-auth)
[ ] curl :8000/health → ok
[ ] 冒烟：创建本体 → 查询 → 删除
[ ] 前端：刷新页面验证新功能
```

> ⚠️ **不要**用 `docker compose down && up` 做升级——那会重建所有容器（含数据引擎），耗时 5-10 分钟且 Doris/Iceberg 重启后需重新预热。只升级自建镜像用上面的定向 `up -d <服务>`。

---

## 6. 鉴权模式选择（关键决策点）

Gaia 支持两种鉴权模式，**首次部署建议先用 Dev Mode 跑通，再切生产**：

### 6.1 Dev Mode（默认，开箱即用）

- `AUTHZ_DEV_MODE=true`（后端）+ `VITE_AUTH_ENABLED=false`（前端）
- 后端从请求头 `X-User-Id` / `X-User-Roles` 解析 Principal
- 前端无登录页，直接进应用
- **用途**：本地开发、POC、内部受信网络快速验证

### 6.2 生产模式（Better Auth + JWT）

- `AUTHZ_DEV_MODE=false` + `BETTER_AUTH_URL=http://better-auth:3000`（后端）
- `VITE_AUTH_ENABLED=true` + `VITE_BETTER_AUTH_URL=...`（前端）
- Better Auth 签发 EdDSA/Ed25519 JWT，后端通过 JWKS 验证
- 首次启动后需建 Better Auth 表（一次性）：

```bash
docker compose exec better-auth npx @better-auth/cli migrate
```

- **JIT 自动开通**（可选）：Better Auth 注册用户时自动调 `POST /identity/users` 在 Gaia 建用户记录，需在 `.env.local` 设 `GAIA_PROVISION_TOKEN`（前后端一致）
- **`BETTER_AUTH_SECRET`**：生产必填，`openssl rand -base64 32`，**设后不可改**（改了所有已签发 JWT 失效 + JWKS 私钥不可解密），须冷备

> 切换模式只需改 `.env.local` 的 `AUTHZ_DEV_MODE` / `BETTER_AUTH_URL` / `VITE_AUTH_ENABLED`，重启 `api` + 前端即可，无需迁移数据。

---

## 7. 配套优化（本次部署新增）

为降低全新环境部署成本，本次随 Runbook 落地以下优化：

| # | 优化 | 文件 | 收益 |
|---|------|------|------|
| 1 | 一键健康检查脚本（智能裁剪适配） | `scripts/healthcheck.sh`（新增） | 替代手工逐个 curl，自动检测已部署服务，裁剪场景不误报 |
| 2 | 修正 `bootstrap_all.sh` 过时端口信息 | `scripts/bootstrap_all.sh` | 删除误导性的 `Iceberg REST:8181`（实际 9001） |
| 3 | 重写 `docs/guide/02-tutorials/01-quickstart.md` | 全新系统使用指导书 | 从空壳变完整 onboarding（组织→权限→用户→数据层→语义层→决策层） |
| 4 | 服务拓扑与依赖关系图 | `deployment-runbook.md` §3.4 | 一图看全分层/依赖/build vs image/启动顺序，排障+裁剪+升级都用得上 |
| 5 | 按场景裁剪服务表 | `deployment-runbook.md` §3.5 | 虚拟表只读场景省 6.5g 内存，给最小服务集 |
| 6 | 微服务滚动升级指南 | `deployment-runbook.md` §5.4 | 前后端频繁发版只动上层镜像，数据引擎不重建 |

> 这些优化遵循"系统能自动做的绝不让人做"原则：健康检查脚本化、默认容器自举、物理资源首用自建。

---

## 8. 本地源码开发模式（非容器部署，供参考）

若要在本机直接跑源码（改代码热重载）：

```bash
# 前置：装 uv（curl -LsSf https://astral.sh/install.sh | sh）

cd /path/to/gaia
uv sync --extra dev                 # 建 .venv（用 TUNA 源）

# 只起基础设施容器（API/前端跑源码）
docker compose up -d postgres rustfs gravitino doris-fe doris-be trino kafka seatunnel-master seatunnel-worker

cp .env.example .env                # 编辑 AI_MODEL + key
make dev                            # 跑 alembic upgrade head + 后端 :8000 + 前端 :5173
```

> ⚠️ 本项目 `uv run` 不可靠，**必须用 `.venv/bin/python` / `.venv/bin/alembic` 直调**（见 `Makefile`）。

---

## 9. 常见部署问题排查

| 现象 | 根因 | 解决 |
|------|------|------|
| `migrate` 容器反复重启 | postgres 未 healthy 就跑了 alembic | 检查 `docker compose logs postgres`，等 `pg_isready` 通过 |
| `api` 启动报 `relation does not exist` | migrate 没跑成功 | `docker compose logs migrate`，修复后 `docker compose up -d migrate` 再 `up -d api` |
| Gravitino 建表 5xx `Unable to load AWS credentials` | `.env` 的 S3 凭证没传进 Gravitino | 确认 `docker-compose.yml` 里 `GRAVITINO_ICEBERG_REST_S3_ACCESS_KEY/SECRET_KEY`（注意是 `S3_ACCESS_KEY` 非 `S3_ACCESS_KEY_ID`） |
| Iceberg REST `?warehouse=` 返回 404 | Gravitino 1.3.0 已知缺陷 | 不带 `?warehouse=`，直接 `curl :9001/iceberg/v1/config` |
| SeaTunnel job 报 `NoEnoughResourceException` | worker 没加入集群 | `curl :5801/hazelcast/rest/maps/cluster-info` 应有 ≥2 members，等 worker 启动 |
| Doris BE 不健康 / OOM | mem 不够 3g | 调大宿主内存，或在 `be.conf` 降 `mem_limit` |
| 前端登录页白屏 | `VITE_AUTH_ENABLED=true` 但 Better Auth 没起 | 先 `docker compose up -d better-auth`，或临时设 `VITE_AUTH_ENABLED=false` |
| 国产库 CDC `Protocol error. Session setup failed` | JDBC 驱动同名类冲突 | 用独立类名驱动包（`infra/jars/` 已备 openGauss/金仓/OceanBase），详见 ADR-014 |

更多引擎互操作踩坑见 `docs/engineer/seatunnel-iceberg-rest-interop-postmortem.md`、`docs/bugfix/gravitino-1.3.0-upgrade.md`。

---

## 10. 卸载 / 重置

```bash
# 停全部容器（保留数据卷）
docker compose down

# 彻底清数据（不可逆！）
docker compose down -v   # 删 postgres_data / rustfs_data / doris_*_data / kafka_data / kestra_data / neo4j_data
```

---

## 11. 端口速查

| 端口 | 服务 | 用途 |
|------|------|------|
| 5432 | PostgreSQL | 元数据 + 时空 |
| 9000 | RustFS | S3 API |
| 9002 | RustFS console | （已禁用 web） |
| 8090 | Gravitino 主 API | 物理资产注册 |
| **9001** | **Gravitino 内置 Iceberg REST** | REST Catalog（**无 8181**） |
| 8030 / 9030 | Doris FE | HTTP / MySQL 协议 |
| 9050 | Doris BE | |
| 8080 | Trino | 联邦查询 |
| 9092 | Kafka | 索引同步总线 |
| 5801 | SeaTunnel Master | REST |
| 3000 | Better Auth | 鉴权 |
| 28080 | Kestra | Pipeline Builder UI |
| 7474 / 7687 | Neo4j | HTTP / Bolt（profile=graph） |
| 8000 | Gaia API | 后端 |
| 5173 | web-ui | 前端（开发态） |

---

## 12. 部署 Checklist（复制使用）

```
─── 准备 ───
[ ] 1. 硬件达标（全功能≥12g；虚拟表只读场景≥6g，见 §3.5）
[ ] 2. Docker + Compose v2 已装，国内镜像加速已配
[ ] 3. git clone 代码到部署机
[ ] 4. 确定部署场景（全功能/托管+Action/虚拟表只读），记下要起的服务集（§3.5）
[ ] 5. cp .env.example .env.local，填 AI_MODEL + provider key
[ ] 6. （生产）填 BETTER_AUTH_SECRET + AUTHZ_DEV_MODE=false

─── 部署 ───
[ ] 7. docker compose up -d <服务集>（裁剪场景只列必选服务）
[ ] 8. docker compose ps 全部 healthy，migrate Exited 0
[ ] 9. bash scripts/healthcheck.sh 全绿（裁剪服务自动 ⊘ skipped）
[ ] 10. curl :8000/health → {"status":"ok"}
[ ] 11. docker compose exec postgres psql ... 看到 org-default + 11 内置角色

─── 验证 ───
[ ] 12. 冒烟：创建本体 → 列出 → 删除 成功
[ ] 13. （生产）docker compose exec better-auth npx @better-auth/cli migrate
[ ] 14. 交付使用指导书给最终用户（docs/guide/02-tutorials/01-quickstart.md）

─── 升级时（见 §5.4）───
[ ] 15. git pull → docker compose build api (better-auth)
[ ] 16. docker compose run --rm migrate alembic upgrade head（有新 migration 才需）
[ ] 17. docker compose up -d api (better-auth)（数据引擎不动）
[ ] 18. curl :8000/health 验证
```
