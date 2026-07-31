# Gaia 本地开发工作流

> **读者**：在本地开发 Gaia 后端(API)或前端(web-ui)的开发者
> **前置**：k3s 单节点已部署 Gaia（`make k8s-all`），源码在 `engines/gaia/`
> **代码核实**：2026-07-23 对照 `scripts/local-update.sh` / `scripts/dev-hotload-*.sh` / `Makefile`

---

## 0. 核心理念：改代码 ≠ 重建镜像

`scripts/local-update.sh` 每次「docker build + ctr import + rollout restart」要几十秒到几分钟，适合**改依赖/Dockerfile/部署清单**。但**只改业务代码时用它太慢**——k3s 单节点和源码同文件系统（WSL2 内），可直接热重载，秒级生效。

| 改动类型 | 用什么 | 速度 |
|---------|--------|------|
| `src/ontology/**` Python 代码 | `make dev-api`（热重载） | ~1-2s |
| `src/web-ui/src/**` 前端代码 | `make dev-web`（Vite HMR） | <1s |
| `pyproject.toml`/`uv.lock`/`package.json`/Dockerfile/部署清单 | `bash scripts/local-update.sh`（重建镜像） | 慢但正确 |

> 💡 `local-update.sh` 开头会**自动检测并关闭热重载残留**（api 的 hostPath patch + 本地 vite），无需手动 `make dev-api-off`。两者可混用，不冲突。

---

## 1. 所有命令一览

> **所有命令都在 `engines/gaia/` 根目录下执行**（即 `cd /home/jason/code/union_agent/engines/gaia`）。`make` target 和 `scripts/*.sh` 内部都会自己 `cd` 到仓库根，不用手动切。

### 1.1 日常开发（秒级热重载，最常用）

| 场景 | 命令 | 说明 |
|------|------|------|
| 改 Python 代码 | `make dev-api` | hostPath 挂源码 + uvicorn --reload，保存即重载 |
| 改前端代码 | `make dev-web` | 本地 Vite HMR，API 经 proxy 转发 k8s |
| 前后端都改 | `make dev` | = `make dev-web --api`，一条命令起两个热重载 |
| 关后端热重载 | `make dev-api-off` | 回到镜像版（保留 port-forward） |

访问地址（热重载开启后）：
- API: `http://localhost:8000/`（/health /docs）
- Web-UI: `http://localhost:5173/`（Vite dev server，HMR）

### 1.2 重建镜像（改了依赖/Dockerfile/部署清单）

| 场景 | 命令 | 说明 |
|------|------|------|
| 重建 api + web-ui 镜像并部署 | `bash scripts/local-update.sh` | 开头自动关热重载残留 |
| 指定版本 tag | `bash scripts/local-update.sh 0.1.0` | |

### 1.3 临时端口转发（不跑热重载，只看 k8s 里的服务）

| 场景 | 命令 |
|------|------|
| 访问 API | `make pf-api`（8000） |
| 访问 web-ui 镜像版 | `make pf-web-ui`（5173→80） |
| 访问 PostgreSQL | `make pf-postgres`（5432） |
| 访问 Trino | `make pf-trino`（8080） |
| 访问 Gravitino | `make pf-gravitino`（8090+9001） |
| 全部转发 | `make pf-all` |

> `make pf-*` 默认前台运行（适合临时调试）。需常驻（关终端不死）用 `setsid nohup kubectl port-forward ... </dev/null >log 2>&1 & disown`。

### 1.4 首次部署 / 全新环境

| 场景 | 命令 | 说明 |
|------|------|------|
| 本地 k3s 一键部署 | `make k8s-all` | infra + services + apps |
| 分步部署 | `make k8s-infra` → `make k8s-services` → `make k8s-apps` | |
| 构建本地镜像 | `make docker-all` | 4 个镜像构建到本地 docker |

### 1.5 纯本地开发（不用 k3s，docker-compose）

| 场景 | 命令 | 说明 |
|------|------|------|
| 起 docker-compose 基础设施 + 本地前后端 | `bash scripts/dev.sh` | 后端 8000 + 前端 5173，都跑在宿主 |
| 仅后端 | `bash scripts/dev.sh backend` | |
| 仅前端 | `bash scripts/dev.sh frontend` | |

---

## 2. 最常用的三条命令

```bash
cd /home/jason/code/union_agent/engines/gaia

make dev              # 日常开发：前后端热重载
make dev-api-off      # 关后端热重载（或直接跑下面的，会自动关）
bash scripts/local-update.sh   # 改了依赖/部署清单，重建镜像
```

---

## 3. 热重载原理

### 3.1 后端热重载（`make dev-api` → `scripts/dev-hotload-api.sh`）

```
宿主源码 src/ontology/  ──hostPath──▶  Pod /app/src/ontology
                                         │
                        uvicorn --reload  + watchfiles 监听
                                         │
                            改 .py 保存 → 自动重启 worker（~1-2s）
```

- `kubectl patch` 给 `deploy/gaia-api` 加 `hostPath` volume（挂宿主 `src/ontology`）+ 改启动命令带 `--reload`
- 容器以 root 运行（绕过 hostPath 文件权限，仅开发环境）
- `off` 模式移除 patch，回到 Dockerfile 默认 CMD

**验证 reload 生效**：
```bash
kubectl -n gaia logs -f deploy/gaia-api --tail=20
# 看到 "WatchFiles detected changes in 'src/ontology/xxx.py'. Reloading..."
```

### 3.2 前端热重载（`make dev-web` → `scripts/dev-hotload-webui.sh`）

```
本地 vite dev server (5173, HMR)  ──proxy──▶  k8s gaia-api (port-forward 8000)
                                                │
                                    改 .tsx 保存 → 浏览器自动热更新（<1s）
```

- 前端不跑在 k3s 里，本地直接 `vite dev server`，享受 HMR（React Fast Refresh）
- API 请求经 `vite.config.ts` 的 proxy 转发到 k8s 里的 `gaia-api`（通过 port-forward 8000）
- 完全不动 web-ui Pod——它只是给「非开发访问」用的镜像版

---

## 4. 常见问题

### 4.1 WSL2 下 vite 5173 报 "Port already in use" 但 `ss`/`lsof` 看不到占用

多半是 Windows 侧有过期的 `netsh portproxy` 规则占着 5173（WSL2 mirrored 模式下 Windows 和 WSL 共享 loopback，Windows 占的端口 WSL 内 `ss` 看不到）。

```powershell
# Windows PowerShell（管理员）
netsh interface portproxy show all
netsh interface portproxy delete v4tov4 listenport=5173 listenaddress=127.0.0.1
```

### 4.2 热重载开后跑 `local-update.sh` 提示构建完还是旧代码

不会发生。`local-update.sh` 开头（0/6 步）会自动检测 `deploy/gaia-api` 是否有 `ontology-src` volume 或 `--reload` 命令，检测到就调 `dev-hotload-api.sh off` 关闭，再构建。无需手动 `make dev-api-off`。

### 4.3 port-forward 频繁断连

`rollout restart` 后旧 Pod 死、新 Pod 起，port-forward 底层会断连。`dev-hotload-api.sh` 和 `local-update.sh` 都会在 rollout 后**重启 port-forward** 并等 health 通。如果手动 `kubectl rollout restart` 了，需手动重起 port-forward：
```bash
pkill -f "kubectl port-forward.*svc/gaia-api"
kubectl port-forward -n gaia svc/gaia-api 8000:8000 &
```

### 4.4 改了依赖（pyproject.toml / package.json）后热重载不生效

热重载只挂源码，不碰 `.venv` / `node_modules`。改依赖必须重建镜像：
```bash
bash scripts/local-update.sh        # 重建镜像（会自动关热重载）
make dev-api                        # 再重新开热重载
```

---

## 5. 相关文件

| 文件 | 作用 |
|------|------|
| `scripts/dev-hotload-api.sh` | 后端热重载（hostPath + uvicorn --reload） |
| `scripts/dev-hotload-webui.sh` | 前端热重载（本地 Vite + HMR） |
| `scripts/local-update.sh` | 重建镜像 + 部署（开头自动关热重载残留） |
| `scripts/dev.sh` | 纯本地 docker-compose 开发（不用 k3s） |
| `Makefile` | `dev-api` / `dev-web` / `dev` / `pf-*` / `k8s-*` 等 target |
| `deploy/README.md` | k3s 部署详解（build/deploy 分离、profile、WSL2 访问） |
