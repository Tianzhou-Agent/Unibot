FROM ghcr.io/astral-sh/uv:0.8.22 AS uv


FROM python:3.12-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_CONCURRENT_DOWNLOADS=8 \
    UV_HTTP_RETRIES=5 \
    UV_HTTP_TIMEOUT=120 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /build
COPY --from=uv /uv /bin/uv
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable \
    && uv pip install --python /opt/venv/bin/python pip


FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PYTHONIOENCODING=utf-8 \
    PATH=/opt/venv/bin:$PATH

RUN apt-get update \
    && apt-get install -y --no-install-recommends bash ca-certificates curl git nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 unibot \
    && mkdir -p /usr/local/lib/node_modules \
    && curl --fail --location --retry 5 --output /tmp/npm.tgz https://registry.npmjs.org/npm/-/npm-10.9.4.tgz \
    && tar -xzf /tmp/npm.tgz -C /usr/local/lib/node_modules \
    && mv /usr/local/lib/node_modules/package /usr/local/lib/node_modules/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && rm /tmp/npm.tgz

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
RUN mkdir -p /data/nas /data/sandboxes \
    && chown -R unibot:unibot /app /data

USER unibot
EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=12 \
    CMD python -c "import json,urllib.request; assert json.load(urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3))['status'] == 'ok'"

CMD ["uvicorn", "tianzhou_agent_platform.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
