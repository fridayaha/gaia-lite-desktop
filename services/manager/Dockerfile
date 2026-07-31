# ============================================================
# UnionAgents Manager — 多阶段构建
# builder 阶段编译需要 gcc/libffi 的 wheel（bcrypt/cryptography），
# runtime 阶段只装预编译 wheel，不含 gcc，镜像更小、攻击面更小。
# ============================================================

# ── builder：编译 wheel ──
FROM docker.m.daocloud.io/library/python:3.11-slim AS builder

WORKDIR /build
RUN sed -i 's|http://deb.debian.org/debian|https://mirrors.aliyun.com/debian|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
    sed -i 's|http://deb.debian.org/debian|https://mirrors.aliyun.com/debian|g' /etc/apt/sources.list 2>/dev/null; \
    apt-get update && apt-get install -y --no-install-recommends gcc libffi-dev && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# 先 COPY requirements.txt 单独一层：源码变动不使依赖层失效
COPY services/manager/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com \
    -r requirements.txt

# ── runtime：仅拷贝已安装的 site-packages 与源码 ──
FROM docker.m.daocloud.io/library/python:3.11-slim AS runtime

WORKDIR /app

# JDK 21（apksigner 依赖 JDK 17+；Debian Trixie 已移除 openjdk-17，21 是当前 stable）
# + Android build-tools（zipalign + apksigner）
# 预估镜像增量 ~240MB（JRE ~190MB + build-tools ~50MB）。
# 用 Aliyun Debian 镜像加速 apt；build-tools 仅 Google 官方源，CI 环境需可访问 dl.google.com。
RUN sed -i 's|http://deb.debian.org/debian|https://mirrors.aliyun.com/debian|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
    sed -i 's|http://deb.debian.org/debian|https://mirrors.aliyun.com/debian|g' /etc/apt/sources.list 2>/dev/null; \
    apt-get update && apt-get install -y --no-install-recommends \
        openjdk-21-jre-headless \
        ca-certificates \
        unzip \
        curl \
    && apt-get clean && rm -rf /var/lib/apt/lists/* && \
    mkdir -p /opt/android-sdk/build-tools && \
    curl -fsSL https://dl.google.com/android/repository/build-tools_r35_linux.zip -o /tmp/bt.zip && \
    unzip -q /tmp/bt.zip -d /tmp/bt && \
    mv /tmp/bt/* /opt/android-sdk/build-tools/35.0.0 && \
    rm -rf /tmp/bt /tmp/bt.zip && \
    ls /opt/android-sdk/build-tools/35.0.0/ | head -20
ENV PATH="/opt/android-sdk/build-tools/35.0.0:${PATH}"
ENV ANDROID_HOME="/opt/android-sdk"

# 拷贝 builder 已装好的依赖（含预编译 wheel，无需 gcc）
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# 应用源码（每次构建都变）
COPY pkg/ ./pkg/
COPY services/manager/app/ ./app/
COPY scripts/seed.py ./scripts/seed.py
# Hermes config.yaml 模板（_build_profile_config_yaml 渲染时读取）
COPY engines/hermes/config/ ./engines/hermes/config/

# base APK 模板目录（dev 本地构建后 cp 进来，gitignored；目录通过 .gitkeep 占位入库）
COPY services/manager/base-apks/ ./base-apks/

ENV PYTHONPATH=/app
EXPOSE 8002

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8002"]
