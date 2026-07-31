FROM docker.m.daocloud.io/library/python:3.11-slim

WORKDIR /app

# 先 COPY requirements.txt 单独一层：源码变动不使依赖层失效
COPY services/gateway/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ \
    --trusted-host mirrors.aliyun.com -r requirements.txt

# Copy shared library
COPY pkg/ ./pkg/

# Copy gateway app
COPY services/gateway/app/ ./app/

ENV PYTHONPATH=/app
EXPOSE 8010

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8010"]
