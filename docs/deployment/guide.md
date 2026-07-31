# UnionAgents 部署指南

## 环境要求
- macOS (本机开发)
- Docker + Colima (k3s)
- kubectl

## 基础设施部署

```bash
# 创建命名空间
kubectl apply -f deploy/k8s/namespace.yaml

# 部署基础设施 (PostgreSQL + MinIO)
kubectl apply -n unionagents -f deploy/k8s/infra/

# 创建 Secret
kubectl apply -n unionagents -f deploy/k8s/infra/secret.yaml
```

## 服务部署

```bash
# 构建 Manager 镜像
docker build -t unionagents/manager:latest -f services/manager/Dockerfile .
# 载入 k3s
docker save unionagents/manager:latest | colima ssh -- sudo ctr -n k8s.io images import -

# 部署所有服务
kubectl apply -n unionagents -f deploy/k8s/services/

# 初始化数据库种子数据
kubectl exec -n unionagents deployment/manager -- python /app/scripts/seed.py
```

## 引擎部署

```bash
# 构建引擎镜像 (基于 gateway 基础镜像)
docker build --no-cache -t unionagents/engine-hermes:latest -f engines/hermes/Dockerfile .
docker save unionagents/engine-hermes:latest | colima ssh -- sudo ctr -n k8s.io images import -

# 创建 DeepSeek API Key Secret
kubectl create secret generic -n unionagents deepseek-key \
  --from-literal=deepseek-api-key=sk-your-key-here

# 部署引擎 (示例: engine-hermes-test)
kubectl apply -n unionagents -f deploy/k8s/engines/hermes-template.yaml
# (需替换模板中的 AGENT_ID 占位符)
```

## 端用户门户部署

```bash
# 构建镜像（apps/enduser/Dockerfile）
docker build -t unionagents/enduser-portal:latest -f apps/enduser/Dockerfile apps/enduser/
kubectl rollout restart -n unionagents deploy/enduser-portal
```

## 端口转发 (本地开发)

```bash
kubectl port-forward -n unionagents svc/manager 8002:8002       # Manager API
kubectl port-forward -n unionagents svc/gateway 8010:8010       # Gateway
kubectl port-forward -n unionagents svc/enduser-portal 3000:80     # 终端门户
```

## 验证端到端流程

```bash
# 1. 登录获取 Token
curl -s http://localhost:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin123"}'

# 2. 创建并发布 Agent
TOKEN=<access_token>
curl -s -X POST http://localhost:8000/api/agents \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"name":"助手","engine_type":"HERMES"}'
curl -s -X POST http://localhost:8000/api/agents/<id>/publish \
  -H "Authorization: Bearer $TOKEN"

# 3. 测试 Gateway 透传 (非流式)
curl -s -X POST http://localhost:8010/v1/chat/completions \
  -H "Authorization: Bearer <engine_api_key>" \
  -H "Content-Type: application/json" \
  -d '{"model":"agent-test","messages":[{"role":"user","content":"你好"}]}'

# 4. 打开浏览器访问端用户门户
open http://localhost:3000
```

## 镜像构建顺序 (依赖链)

```
unionagents/gateway:latest
  └── unionagents/engine-hermes:latest (FROM gateway)
unionagents/manager:latest
```

## 清理

```bash
# 删除命名空间下的所有资源
kubectl delete namespace unionagents
```
