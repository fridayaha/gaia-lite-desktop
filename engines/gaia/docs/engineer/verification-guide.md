# Gaia 前后端验证指南

> 最后更新: 2026-06-15 | 版本: v0.2.0

---

## 1. 环境准备

### 1.1 需求

- Python 3.12+ with `uv`
- Node.js 20+ with `npm`
- Docker Compose (后端基础设施)
- 浏览器 (Chrome / Firefox / Edge)

### 1.2 启动后端基础设施

```bash
cd /home/jason/code/gaia

# 启动所有后端服务 (PostgreSQL, Gravitino, Iceberg, Doris, etc.)
docker compose up -d postgres gravitino rustfs doris-fe doris-be trino

# 等待所有服务就绪
sleep 15
docker compose ps
```

### 1.3 启动 API (注意:需本地运行以使用新路由)

Docker 镜像尚未包含最新路由变更，需本地启动 API:

```bash
cd /home/jason/code/gaia

# 先停掉 Docker 中的 API 容器以避免端口冲突
docker compose stop api

# 本地启动 API (热重载模式)
uv run uvicorn ontology.main:app --host 0.0.0.0 --port 8000 --reload
```

### 1.4 验证后端健康

```bash
# 健康检查
curl http://localhost:8000/health
# 预期: {"status":"ok"}

# 列出现有本Ť
curl http://localhost:8000/ontologies
# 预期: []
```

---

## 2. 后端 API 验证

### 2.1 本体 CRUD

```bash
# 创建本体
curl -X POST http://localhost:8000/ontologies \
  -H "Content-Type: application/json" \
  -d '{"api_name":"test_ontology","display_name":"测试本体","description":"端到端验证"}'
# 预期: 返回创建的本体 JSON，status_code=201

# 列出所有本体
curl http://localhost:8000/ontologies
# 预期: 至少包含 "test_ontology"

# 获取单个本体
curl http://localhost:8000/ontologies/test_ontology
# 预期: 返回该本体的详细信息
```

### 2.2 对象类型 CRUD

```bash
# 创建物理对象
curl -X POST http://localhost:8000/ontologies/test_ontology/object-types \
  -H "Content-Type: application/json" \
  -d '{"api_name":"work_order","display_name":"工单","primary_key":"id","title_property":"display_name","storage_type":"MANAGED"}'
# 预期: 返回创建的对象 JSON，status_code=201

# 创建虚拟对象
curl -X POST http://localhost:8000/ontologies/test_ontology/object-types \
  -H "Content-Type: application/json" \
  -d '{"api_name":"ref_table","display_name":"引用表","primary_key":"id","title_property":"name","storage_type":"VIRTUAL"}'
# 预期: 返回创建的对象 (VIRTUAL 类型)，status_code=201

# 列出对象类型
curl http://localhost:8000/ontologies/test_ontology/object-types
# 预期: 返回 2 个对象 (工单 + 引用表)
```

### 2.3 属性管理

```bash
# 添加属性
curl -X POST http://localhost:8000/ontologies/test_ontology/object-types/work_order/properties \
  -H "Content-Type: application/json" \
  -d '{"api_name":"status","display_name":"状态","data_type":"STRING"}'
# 预期: 返回属性 JSON，status_code=201

curl -X POST http://localhost:8000/ontologies/test_ontology/object-types/work_order/properties \
  -H "Content-Type: application/json" \
  -d '{"api_name":"priority","display_name":"优先级","data_type":"STRING"}'
# 预期: status_code=201

# 列出属性
curl http://localhost:8000/ontologies/test_ontology/object-types/work_order/properties
# 预期: 返回 2 个属性 (状态 + 优先级)
```

### 2.4 关系定义

```bash
# 创建生产线对象
curl -X POST http://localhost:8000/ontologies/test_ontology/object-types \
  -H "Content-Type: application/json" \
  -d '{"api_name":"prod_line","display_name":"产线","primary_key":"id","title_property":"name","storage_type":"MANAGED"}'

# 获取对象 ID
WORK_ORDER_ID=$(curl -s http://localhost:8000/ontologies/test_ontology/object-types/work_order | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
PROD_LINE_ID=$(curl -s http://localhost:8000/ontologies/test_ontology/object-types/prod_line | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")

# 创建关系
curl -X POST http://localhost:8000/ontologies/test_ontology/link-types \
  -H "Content-Type: application/json" \
  -d "{\"api_name\":\"belongs_to_line\",\"display_name\":\"所属产线\",\"source_object_type_id\":\"$WORK_ORDER_ID\",\"target_object_type_id\":\"$PROD_LINE_ID\",\"cardinality\":\"MANY\",\"direction\":\"OUTGOING\"}"
# 预期: status_code=201

# 列出关系
curl http://localhost:8000/ontologies/test_ontology/link-types
# 预期: 返回 1 个关系
```

### 2.5 删除验证 (含确认)

```bash
# 删除属性
curl -X DELETE http://localhost:8000/ontologies/test_ontology/object-types/work_order/properties/priority
# 预期: status_code=204

# 删除对象 (级联)
curl -X DELETE http://localhost:8000/ontologies/test_ontology/object-types/prod_line
# 预期: status_code=204

# 删除本体 (级联所有)
curl -X DELETE http://localhost:8000/ontologies/test_ontology
# 预期: status_code=204

# 验证已删除
curl http://localhost:8000/ontologies
# 预期: 空列表 []
```

---

## 3. 前端验证

### 3.1 启动前端

```bash
cd /home/jason/code/gaia/src/web-ui
npm run dev
# 默认地址: http://localhost:5173
```

### 3.2 主流程验证

打开浏览器访问 `http://localhost:5173`

#### Step 1: 创建本体

1. 页面显示 Gaia 欢迎界面，点击 **"创建第一个本?"**
2. 填写表单:
   - API 名称: `demo`
   - 显示名称: `演示本体`
3. 点击 **"创建"**
4. **预期**: 本体创建成功，侧栏出现 "演示本体"，页面进入空状态

#### Step 2: 创建对象

1. 在侧栏选中 "演示本体"，点击 **"+ 新建对象"**
2. 填写:
   - API 名称: `customer`
   - 显示名称: `客户`
   - 主键: `id`
   - 标题字段: `name`
   - 存储类: `托管对象`
3. 点击 **"创建"**
4. **预期**: 对象出现Ŝ卡片网格和一栏树中

#### Step 3: 添加属性

1. 点击 "客户" 对象卡片
2. 在下方详情面板点击 **"+ 添加"** (属性管理)
3. 添加属性 `name` (STRING)、`level` (STRING)、`created` (TIMESTAMP)
4. **预期**: 属性列表实时s更新，侧栏完成指示变绿

#### Step 4: 图谱视图

1. 点击视图工具栏的 **"🕸 图谱"**
2. 重复 Step 2 创建 "订单" 对象
3. 创建关系: 客户 → 订单 (Step 2.4)
4. **预期**: 
   - 画布显示节点
   - 悬停节踂突邻节踂高亮，非关联淡
   - 滚轮缩放画布
   - 拖拽平移画布

#### Step 5: 数据对接

1. 点击左侧 Rail 中的 **"🔗 数据对接"**
2. **预期**: 显示所有对象的连接状态表
   - 托管对象: "⚠️ 未连接"
   - 虚拟对象: "⚪ 虚拟"

#### Step 6: 能力赋予

1. 点击 Rail 中的 **"⚡ 能力赋予"**
2. **预期**: 显示"还没有定义操作"的空状态

#### Step 7: 运行洞察

1. 点击 Rail 中的 **"📊 运行洞察"**
2. **预期**: 显示计量卡片 (本体数量/对象/关系/API健康)

#### Step 8: 删除确认

1. 回到 ① 业务定义
2. 点击 "订单" 卡片上的 **"删除"** 按钮
3. **预期**: 弹出确认对话框, 列出级联影响, 需输入对象名称才可确认
4. 输入 "订单" 并确认
5. **预期**: 对象被删除

---

## 4. 故障排查

| 问题 | 检查 | 解决 |
|------|------|------|
| `curl` 返回 `connection refused` | API 是否已启动 | `uv run uvicorn ...` |
| 前端白屏 | Vite 是否启 | `npm run dev` |
| 前端 `404` 或 `CORS` | Vite proxy 配置 | 检查 `vite.config.ts` 的 proxy 段 |
| 创建本体失败 | PostgreSQL | `docker compose ps` 确认 postgres 运行 |
| 图谱不显示 | cytoscape 是°安装 | `ls node_modules/cytoscape` |

## 5. 技术栈回顾

| 层 | 技术 | 端口 |
|----|------|------|
| 前端 | React 18 + TypeScript + Vite | 5173 |
| API | Python FastAPI + Uvicorn | 8000 |
| PostgreSQL | PostgreSQL 16 (Alpine) | 5432 |
| 图谱引擎 | Cytoscape.js 3.x (Canvas) | N/A |

## 6. 前端项目命令

```bash
cd src/web-ui
npm run dev          # 开发模式 (热重载)
npm run build        # 生产构建
npm run preview      # 预览生产构建
npx tsc --noEmit     # TypeScript 类型检查
npx eslint .         # ESLint 检查
npx prettier --check src/  # 格式检查
```
