# RAGFlow Offline Deployment v0.25.6

RAGFlow 离线部署包，适用于无法访问互联网的 x86_64 Linux 环境。

## 组件清单

| 组件 | 镜像 | 用途 |
|------|------|------|
| OpenSearch | `opensearchproject/opensearch:2.19.1` | 向量检索引擎 |
| MySQL | `mysql:8.0.39` | 元数据存储 |
| MinIO | `pgsty/minio:RELEASE.2026-03-25T00-00-00Z` | 文件对象存储 |
| Redis (Valkey) | `valkey/valkey:8` | 缓存 / 消息队列 |
| TEI | `infiniflow/text-embeddings-inference:cpu-1.8` | 嵌入模型服务 |
| RAGFlow | `infiniflow/ragflow:v0.25.6` | 核心应用 |
| bge-m3 | 模型文件 (~2GB) | 中英双语嵌入模型 |

## 系统要求

- **CPU**: x86_64（不支持 ARM）
- **内存**: 8 GB+（最低 7GB，推荐 16 GB+）
- **磁盘**: 25 GB+ 可用空间
- **内核参数**: `vm.max_map_count >= 262144`（install.sh 会自动设置）
- **OS**: Linux（内核 4.x+，systemd 或 sysvinit）
- **Docker**: Engine 20.10+，含 docker compose v2 插件

## 部署步骤

### 1. 传输 & 验证

```bash
# 复制到目标机器后，验证完整性
sha256sum ragflow-offline-v0.25.6.tar.gz
# 比对 build 时输出的 SHA256 值

# 解压
tar xzf ragflow-offline-v0.25.6.tar.gz
cd ragflow-offline-v0.25.6
```

### 2. 修改密码（推荐）

编辑 `.env` 文件，将默认密码改为强密码：

```bash
# 生成随机密码
openssl rand -hex 32
```

需要修改的项：
- `MYSQL_PASSWORD`
- `OPENSEARCH_PASSWORD`
- `MINIO_PASSWORD`
- `REDIS_PASSWORD`

### 3. 执行安装

```bash
sudo bash install.sh
```

安装脚本会自动完成以下检查：
- ✅ 系统架构（x86_64）
- ✅ 内存（≥ 7GB）
- ✅ 磁盘空间（≥ 25GB）
- ✅ `vm.max_map_count`（OpenSearch 需要 ≥ 262144，自动修复）
- ✅ Docker 版本和运行状态
- ✅ 端口冲突（80/443/1201/6380/9380 等）

安装脚本会依次：
1. 检查 Docker 环境
2. 加载所有镜像
3. 启动全部服务
4. 执行健康检查
5. 输出访问地址

### 4. 访问

浏览器打开 **http://localhost** ，首次访问需注册账号（第一个注册用户自动成为管理员）。

## 部署后配置

### 4.1 嵌入模型（已预配置）

TEI 嵌入服务（bge-m3）已随部署自动启动，BGE-M3 模型由 TEI 容器在 6380 端口提供 OpenAI 兼容 API。

在 RAGFlow UI 中添加模型提供商，嵌入模型选择 "OpenAI-API-Compatible" 后即可自动使用。

**手动配置参考：**

1. 登录后进入 **Model Providers**（模型提供商）页面
2. 添加提供商，选择 **OpenAI-API-Compatible**
3. 配置参数：
   - **Model Name**: `BAAI/bge-m3`
   - **Base URL**: `http://tei:80`
   - **API Key**: `xxx`（任意非空值，TEI 不需要鉴权）
4. 添加具体模型实例
5. 在 **Settings** 中将其设为默认嵌入模型

**验证嵌入服务：**
```bash
curl -X POST http://localhost:6380/embed \
  -H "Content-Type: application/json" \
  -d '{"inputs":"Hello, world!"}'
```

### 4.2 对话模型

RAGFlow 不内置 LLM，需要用户自行配置。支持的方式：

- **本地大模型服务** (vLLM / Ollama / LocalAI 等)
- **外部 API** (OpenAI / 通义千问 / DeepSeek 等，需要联网)

如果部署环境完全离线，建议在另一台有网络的机器上搭建 LLM 代理服务，或将 Ollama 等本地模型同样打包部署。

配置路径：**Model Providers > 添加相应的提供商 > 填入 API 地址和密钥**

### 4.3 其他可选配置

- **重排序模型 (Rerank)**: 如需 bge-reranker，可在 Model Providers 中添加
- **语音识别 (ASR)**: 支持 OpenAI Whisper 等
- **对象存储**: 默认使用内置 MinIO，如需切换外部 S3/OSS 可在 service_conf.yaml 中配置

## 管理命令

```bash
# 进入部署目录
cd ragflow-offline-v0.25.6

# 查看服务状态
docker compose -f docker-compose.offline.yml ps

# 查看日志
docker compose -f docker-compose.offline.yml logs -f [服务名]

# 查看特定服务日志
docker compose -f docker-compose.offline.yml logs -f ragflow
docker compose -f docker-compose.offline.yml logs -f tei

# 重启服务
docker compose -f docker-compose.offline.yml restart

# 停止所有服务
docker compose -f docker-compose.offline.yml down

# 停止并清除数据（危险！）
docker compose -f docker-compose.offline.yml down -v
```

## 端口说明

| 端口 | 服务 | 说明 |
|------|------|------|
| 80 | RAGFlow Web UI | 前端界面 |
| 443 | RAGFlow HTTPS | SSL（需配置证书） |
| 1201 | OpenSearch | 向量引擎 API |
| 3306 | MySQL | 数据库（仅内部） |
| 6380 | TEI | 嵌入模型 API |
| 6379 | Redis | 缓存（仅内部） |
| 9000 | MinIO | 对象存储 API |
| 9001 | MinIO Console | 存储管理界面 |
| 9380 | RAGFlow API | 后端 API |
| 9381 | Admin API | 管理后台 API |

## 卸载 / 回退

部署失败或需要重新部署时：

```bash
# 标准卸载（保留镜像，方便重试）
sudo bash uninstall.sh

# 完全清理（包括镜像，需重新 load）
sudo bash uninstall.sh --images
```

卸载脚本使用安全策略，只清理 RAGFlow 相关资源：
- ✅ 通过 compose label 精确识别 RAGFlow 容器
- ✅ 只在无其他容器使用时才删除 network
- ✅ 不触碰宿主机上其他 Docker 资源
- ✅ 默认保留镜像（避免重复 load）

## 构建（在线机器）

在有互联网的 x86_64 Linux 机器上构建离线包：

```bash
# 修改 .env 中密码/端口（可选）
# 执行构建
bash build_offline_package.sh

# 输出：ragflow-offline-v0.25.6.tar.gz
# 记录输出的 SHA256 值，部署时用于校验
```

构建脚本特点：
- **幂等**：跳过已 pull 的镜像和已下载的模型
- **校验**：gzip 完整性检查 + sha256sum + 文件大小验证
- **并行**：镜像保存和模型下载并行执行

## 故障排查

### 服务未启动
```bash
docker compose -f docker-compose.offline.yml ps
# 查看状态为 "exited" 的服务日志
docker compose -f docker-compose.offline.yml logs <service-name>
```

### OpenSearch 无法连接
```bash
# 检查 OpenSearch 状态
curl http://localhost:1201
# 应返回 JSON 响应（可能包含认证错误，说明服务在运行）
```

### TEI 模型加载失败
TEI 加载 bge-m3 需要约 2-4 GB 内存，确保系统内存充足：
```bash
docker compose -f docker-compose.offline.yml logs tei
# 查看是否有 "Downloading" 或内存不足错误
```

### 端口冲突
修改 `.env` 中的端口配置后重新部署：
```bash
docker compose -f docker-compose.offline.yml down
# 编辑 .env 修改端口
docker compose -f docker-compose.offline.yml up -d
```

### 嵌入模型不工作
1. 检查 TEI 是否正常：`curl -X POST http://localhost:6380/embed -H "Content-Type: application/json" -d '{"inputs":"test"}'`
2. 在 RAGFlow UI 中确认 Model Providers 配置是否正确
3. Base URL 在 RAGFlow 容器内部应使用 `http://tei:80`（Docker 内部网络地址）

## 网络安全

默认所有密码均为弱密码（方便初次部署），**生产环境务必修改** `.env` 中所有密码项。

Docker 内部服务通过 `ragflow` bridge 网络通信，不暴露到宿主机外。只有带端口映射的服务可以从宿主机访问。

## 目录结构

```
ragflow-offline-v0.25.6/
├── images/                        # Docker 镜像
├── models/                        # 嵌入模型 (bge-m3)
├── .env                           # 环境变量
├── docker-compose.offline.yml     # Compose 配置
├── service_conf.yaml.template     # 服务配置模板
├── install.sh                     # 安装脚本
├── init.sql                       # MySQL 初始化
├── entrypoint.sh                  # RAGFlow 启动脚本
├── infinity_conf.toml             # Infinity 配置（保留）
├── conf/                          # RSA 密钥（由 install.sh 在部署时自动生成）
├── nginx/                         # Nginx 配置
├── ragflow-logs/                  # 日志目录
└── README.md                      # 本文件
```
