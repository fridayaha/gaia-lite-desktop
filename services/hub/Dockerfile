FROM docker.m.daocloud.io/library/python:3.11-slim

WORKDIR /app

# Install deps (use tsinghua mirror)
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple \
    fastapi \
    "uvicorn[standard]" \
    "sqlalchemy[asyncio]" \
    "psycopg[binary]" \
    pydantic \
    pydantic-settings \
    alembic \
    python-multipart \
    pyyaml

# Copy application code
COPY services/hub/backend/ ./

CMD sh -c "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8003"
