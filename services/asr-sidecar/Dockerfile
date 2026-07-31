# 基础镜像：默认 docker.io；国内/被墙网络构建时传
#   --build-arg PYTHON_BASE=docker.m.daocloud.io/library/python:3.11-slim
ARG PYTHON_BASE=python:3.11-slim
FROM ${PYTHON_BASE}

# whisper 模型大小：默认 base（~150M，运行 ~600M，适配内存紧张的联调节点）。
# 可 build-arg 覆盖：--build-arg WHISPER_MODEL=small（更准，但 ~1.2G 运行内存）
ARG WHISPER_MODEL=base
# HuggingFace 端点：默认空=直连 huggingface.co；国内构建传
#   --build-arg HF_ENDPOINT=https://hf-mirror.com（注意 huggingface_hub ≥1.24 经 hf-mirror 取不到元数据，
#   实测该镜像站 /api 已 308 失效；ECS 直建建议直连）。
# 空值时必须落回官方域名：huggingface_hub 读到空串会拼出无协议 URL（UnsupportedProtocol）。
ARG HF_ENDPOINT=
ENV HF_ENDPOINT=${HF_ENDPOINT:-https://huggingface.co}

WORKDIR /app

COPY requirements.txt /tmp/requirements.txt
# ctranslate2/av 轮子 35MB+，网络抖动时整层重跑代价高；ECS docker 老版本不支持 BuildKit cache mount，
# 构建侧用外层重试循环兜底（一旦该层成功即被 layer cache 复用）。
RUN pip install --no-cache-dir --timeout 120 --retries 10 -i https://mirrors.aliyun.com/pypi/simple/ -r /tmp/requirements.txt

# 预下载 whisper 模型（build 阶段，运行时不依赖外网）。
# 默认直连 huggingface.co；国内通过 --build-arg HF_ENDPOINT=https://hf-mirror.com 走镜像。
# HF_HUB_DISABLE_XET=1：huggingface_hub ≥1.x 默认走 Xet 存储，hf-mirror 不代理 Xet，必须回退普通 CDN。
RUN HF_HUB_DISABLE_XET=1 WHISPER_MODEL=${WHISPER_MODEL} python -c "import os; from faster_whisper import WhisperModel; WhisperModel(os.environ['WHISPER_MODEL'], device='cpu', compute_type='int8')"

COPY app/ /app/app/

ENV PORT=9100
ENV WHISPER_MODEL=${WHISPER_MODEL}
ENV WHISPER_DEVICE=cpu
ENV WHISPER_COMPUTE_TYPE=int8
# 运行时只用缓存（build 阶段已预下载），不连 HuggingFace（部分网络不可达）
ENV HF_HUB_OFFLINE=1

CMD ["python3", "-m", "app.server"]

