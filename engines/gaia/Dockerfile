# ── Stage 1: Builder — install 3rd-party dependencies only ──
FROM python:3.12-slim AS builder

# Overridable: --build-arg PIP_INDEX=https://pypi.org/simple
ARG PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
ARG UV_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
# EDITION=full 装全量数据栈重依赖（云版）；lite 装 SQLite+DuckDB 轻量集（桌面版）。
# 见 pyproject.toml [project.optional-dependencies] 的 full / lite extras。
ARG EDITION=full

WORKDIR /app

# Install uv inside builder (discarded after this stage)
RUN pip install --no-cache-dir \
    -i ${PIP_INDEX} \
    uv

ENV UV_INDEX_URL=${UV_INDEX}

# Copy dependency manifests → max layer cache hit
# --no-install-project: skip building ontology itself, only 3rd-party deps
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --extra ${EDITION} && \
    # 清理 .venv 内非运行时文件（省 ~28M，不影响运行）
    cd .venv/lib/python3.12/site-packages && \
    # botocore/data：只保留 s3/sts（RustFS 调用用），删其他 AWS 服务描述 JSON
    find botocore/data -maxdepth 1 -type d ! -name s3 ! -name sts ! -name data -exec rm -rf {} + && \
    # tests 目录：运行时不需要
    find . -type d \( -name tests -o -name test \) -exec rm -rf {} + 2>/dev/null; \
    # *.pyi：类型存根（运行时 Python 不读）
    find . -name "*.pyi" -delete 2>/dev/null; \
    # 注：dist-info 不能删（importlib.metadata.version() 运行时查询包元数据）
    true


# ── Stage 2: Runtime — minimal production image ──
FROM python:3.12-slim

# Non-root user
RUN groupadd -r ontology && useradd -r -g ontology ontology

WORKDIR /app

# Copy only the virtualenv (3rd-party deps, no ontology package)
# --chown 在 COPY 时直接设置属主，避免单独的 RUN chown 层（省 ~400M 重复层）
COPY --chown=ontology:ontology --from=builder /app/.venv .venv

# Copy application source + set PYTHONPATH (ontology not installed as pkg)
COPY --chown=ontology:ontology src/ontology ./src/ontology
ENV PYTHONPATH=/app/src

# Alembic migration 配置与 revision 链（migrate 容器需要）
COPY --chown=ontology:ontology alembic.ini ./
COPY --chown=ontology:ontology alembic ./alembic

USER ontology

EXPOSE 8000

# Direct venv binary — no uv wrapper overhead
CMD [".venv/bin/uvicorn", "ontology.main:app", "--host", "0.0.0.0", "--port", "8000"]
