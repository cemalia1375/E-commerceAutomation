FROM python:3.11-slim

# 构建阶段不走宿主机 HTTP 代理（172.17.0.1:7890 会导致 apt 502）
ARG HTTP_PROXY=
ARG HTTPS_PROXY=
ARG http_proxy=
ARG https_proxy=
ARG NO_PROXY=*
ARG no_proxy=*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g; s|security.debian.org|mirrors.aliyun.com|g' \
        /etc/apt/sources.list.d/debian.sources 2>/dev/null || true \
    && env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \
        apt-get update \
    && env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \
        apt-get install -y --no-install-recommends curl ca-certificates redis-server \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt

COPY Mojing ./Mojing
COPY simpleclaw ./simpleclaw
COPY admin ./admin
COPY script ./script
COPY .env ./.env
COPY entrypoint.sh ./entrypoint.sh
RUN chmod +x /app/entrypoint.sh

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["/app/entrypoint.sh"]
