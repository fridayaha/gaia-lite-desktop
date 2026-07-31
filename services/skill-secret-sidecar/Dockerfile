FROM docker.m.daocloud.io/library/python:3.11-slim

RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ \
    --trusted-host mirrors.aliyun.com fastapi uvicorn cryptography

WORKDIR /app
COPY sidecar.py .

EXPOSE 8004
CMD ["uvicorn", "sidecar:app", "--host", "0.0.0.0", "--port", "8004"]
