# Gaia 开发环境启动记录

## 快速启动

```bash
cd /home/jason/code/gaia

# 一键启动后端 + 前端
bash scripts/dev.sh

# 或分开启动
make dev-backend   # 仅后端 (port 8000)
make dev-frontend  # 仅前端 (port 5173)
```

## 启动原理

### 后端

```bash
cd /home/jason/code/gaia

# 关键：不能直接用 `uv run uvicorn`（项目里 uv run 不稳定/慢），
# 必须用 .venv/bin/python 直调
.venv/bin/python -m uvicorn ontology.main:app \
  --host 127.0.0.1 --port 8000 \
  > .run-logs/backend.log 2>&1 &
```

### 前端

```bash
cd /home/jason/code/gaia/src/web-ui

node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5173 \
  > ../../.run-logs/frontend.log 2>&1 &
```

## 验证

```bash
# 后端健康检查
curl http://127.0.0.1:8000/health
# → {"status":"ok"}

# 前端
curl http://127.0.0.1:5173/
# → 返回 HTML 页面
```

## 基础设施依赖

启动后端需要这些容器在运行（docker compose up）：

| 服务 | 端口 | 用途 |
|------|------|------|
| ontology-postgres | 5432 | 元数据存储 (PG) |
| ontology-trino | 8080 | 查询引擎 |
| ontology-gravitino | 8090 | Iceberg 目录管理 |
| ontology-rustfs | 9000 | S3 兼容存储 |
| ontology-kafka | 9092 | 消息队列 |
| ontology-doris-fe | 8030/9030 | 索引加速层 |

```bash
# 启动基础设施
docker compose up -d
```

## AI API Key

`.env` 里的 API key 会被 `settings.py` 在模块加载时自动注入 `os.environ`，
所以 pydantic-ai 的 `os.getenv('DEEPSEEK_API_KEY')` 能直接拿到。

**不需要** `export` 或 `source .env` —— settings 模块回灌机制自动处理。

### 换 LLM provider

编辑 `.env`，改 `AI_MODEL` 和对应的 key：

```
AI_MODEL=openai:gpt-4o
OPENAI_API_KEY=sk-xxx
```

settings.py 已声明了 10 个 provider 的 key 字段，切换只需改两行 `.env`。

**国产 OpenAI 兼容端点（GLM/Pangu/ZhipuAI/vLLM/LiteLLM 网关等）**：用 `openai-chat:` 前缀 + `AI_OPENAI_BASE_URL` 指向兼容网关（`openai:` 走 Responses API，国产端点大多只支持 Chat Completions，必须用 `openai-chat:`）：

```
AI_MODEL=openai-chat:glm-4-plus
AI_OPENAI_BASE_URL=https://open.bigmodel.cn/api/paas/v4
OPENAI_API_KEY=<智谱 API Key>
```

`AI_OPENAI_BASE_URL` 为空时走 OpenAI 默认端点；非空时 settings.py 自动 re-export 成 `OPENAI_BASE_URL` 环境变量，pydantic-ai 的 `OpenAIProvider` 会自动读取（无需改代码）。

k3s 部署同理：在 `deploy/ci/.env.local` 配 `GAIA_AI_MODEL` + `GAIA_OPENAI_BASE_URL` + `GAIA_OPENAI_API_KEY`，重跑 `deploy.sh` 即可。
