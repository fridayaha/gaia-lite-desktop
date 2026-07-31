# RAGFlow 离线部署包 (`engines/ragflow/`)

## 目录定位

本目录是 **UnionAgents 项目** 中 RAGFlow 引擎的离线部署方案，位于 `engines/ragflow/`。

本目录下的文件用于在 **无法访问互联网的 x86_64 Linux 目标机器** 上部署 RAGFlow v0.25.6，包含构建脚本、安装脚本、compose 配置和文档。

## 文件说明

| 文件 | 用途 |
|------|------|
| `build_offline_package.sh` | 在**有网络的构建机**上执行，pull 镜像 + 下载 bge-m3 模型，打包为 `.tar.gz` |
| `install.sh` | 在**离线目标机**上执行，检查系统要求 → 加载镜像 → 启动全部服务 → 健康检查 |
| `uninstall.sh` | 安全卸载，精确清理 RAGFlow 容器/volume/network（不误删宿主机其他 Docker 资源） |
| `docker-compose.offline.yml` | 自包含 compose 文件，定义全部服务（OpenSearch、MySQL、MinIO、Redis、TEI、RAGFlow） |
| `README.md` | 面向用户的完整部署文档 |

## RAGFlow 服务栈（6 个容器）

| 服务 | 镜像 | 端口 |
|------|------|------|
| OpenSearch | `opensearchproject/opensearch:2.19.1` | 1201 |
| MySQL | `mysql:8.0.39` | 3306（内部） |
| MinIO | `pgsty/minio:RELEASE.2026-03-25T00-00-00Z` | 9000/9001 |
| Redis (Valkey) | `valkey/valkey:8` | 6379（内部） |
| TEI | `infiniflow/text-embeddings-inference:cpu-1.8` | 6380 |
| RAGFlow | `infiniflow/ragflow:v0.25.6` | 80/443/9380 |

## 构建与部署流程

```
[有网构建机]                          [离线目标机]
build_offline_package.sh
  → 下载所有镜像 + bge-m3 模型
  → 输出 ragflow-offline-v0.25.6.tar.gz
  ─────────────────────────────────→  copy tar.gz
                                      install.sh
                                        → 解压 → load 镜像 → docker compose up
                                        → 健康检查 → 输出访问地址
```

## 约束与注意事项

### 不在 k3s/colima 中运行
此 RAGFlow 离线部署包使用独立的 `docker compose`（非 k3s），与 UnionAgents 主项目的 k3s 集群**解耦**。部署到独立的目标机器，不占用开发集群资源。

### 引擎架构约定
UnionAgents 的引擎（Hermes、RAGFlow 等）都遵循 **容器化 + HTTP API 调用** 原则，不修改开源代码，通过原生 API 集成。

### 修改时的注意事项
- `build_offline_package.sh` 是幂等的（跳过已 pull/download 的资源）
- `install.sh` 会自动修正 `vm.max_map_count`（OpenSearch 需要 ≥ 262144）
- `uninstall.sh` 使用 compose label 精确定位容器，不会误删
- `.env` 中所有密码默认为弱密码，README 中已标注生产环境务必修改

### 离线部署特殊性
- 所有镜像通过 `docker save/load` 传输，不依赖 registry
- bge-m3 模型文件 (~2GB) 通过 HuggingFace CDN / hf-mirror 下载后打包
- 构建脚本支持 `--china-mirrors` 参数使用国内镜像加速
