#!/usr/bin/env bash
set -e

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"

cleanup() {
    echo ""
    echo "正在关闭所有服务..."
    jobs -p | xargs -r kill 2>/dev/null || true
    wait 2>/dev/null
    echo "所有服务已关闭"
}

trap cleanup EXIT INT TERM

# ─── 数据库初始化 ───
echo "[0/3] 初始化数据库表..."
cd "$BACKEND_DIR"
source .venv/bin/activate

# PoC 演示默认使用 SQLite
export DATABASE_URL="${DATABASE_URL:-sqlite:///$BACKEND_DIR/demo.db}"
echo "    数据库: $DATABASE_URL"

if compgen -G "$BACKEND_DIR/alembic/versions/*.py" > /dev/null 2>&1; then
    echo "    使用 Alembic 迁移..."

    # 检测表是否存在
    TABLE_EXISTS=$(python -c "
try:
    from app.db.session import engine
    from sqlalchemy import inspect, text
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    if 'hub_items' in tables:
        print('yes')
    else:
        print('no')
except Exception:
    print('no')
" 2>/dev/null)

    if [ "$TABLE_EXISTS" = "yes" ]; then
        # 检查 alembic_version 是否已存在
        ALEMBIC_STAMP=$(python -c "
try:
    from app.db.session import engine
    from sqlalchemy import inspect
    inspector = inspect(engine)
    if 'alembic_version' in inspector.get_table_names():
        print('yes')
    else:
        print('no')
except Exception:
    print('no')
" 2>/dev/null)

        if [ "$ALEMBIC_STAMP" = "yes" ]; then
            echo "    检测到已有 Alembic 迁移记录，执行 upgrade..."
            alembic upgrade head
        else
            echo "    检测到 legacy demo.db（无 Alembic 记录），标记为 head..."
            alembic stamp head
            echo "    已标记为 Alembic head"
        fi
    else
        echo "    空数据库，执行 Alembic 初始化迁移..."
        alembic upgrade head
    fi
else
    echo "    使用 create_all 初始化（无 migration 文件）..."
    python -c "
import app.models  # noqa: F401
from app.db.base import Base
from app.db.session import engine
Base.metadata.create_all(bind=engine)
print('    数据库表已创建')
"
fi

# ─── 后端 ───
echo "[1/3] 启动后端服务..."
uvicorn app.main:app --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!
sleep 1

if ! curl -s http://localhost:8000/api/health >/dev/null 2>&1; then
    echo "后端启动失败"
    exit 1
fi
echo "    后端就绪: http://localhost:8000"

# ─── 前端 ───
echo "[2/3] 启动前端服务..."
cd "$FRONTEND_DIR"

if ! npx vite --version >/dev/null 2>&1; then
    echo "安装前端依赖..."
    npm install --registry https://registry.npmmirror.com --silent
fi

npx vite --host 0.0.0.0 --port 5173 &
sleep 2

if ! curl -s http://localhost:5173 >/dev/null 2>&1; then
    echo "前端启动失败"
    exit 1
fi
echo "    前端就绪: http://localhost:5173"

# ─── 预置数据（可选） ───
echo "[3/3] 初始化预置数据..."
sleep 1
RESULT=$(curl -s -X POST http://localhost:8000/api/hub/presets/init 2>/dev/null || echo "")
if echo "$RESULT" | grep -q '"created"'; then
    echo "    预置数据已初始化"
else
    echo "    预置数据初始化跳过"
fi

echo ""
echo "═══════════════════════════════════════════"
echo "  后端 API:  http://localhost:8000/api"
echo "  前端页面:  http://localhost:5173"
echo "  按 Ctrl+C 退出并关闭所有服务"
echo "═══════════════════════════════════════════"

wait
