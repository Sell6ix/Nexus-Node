# syntax=docker/dockerfile:1.7
# Nexus Node web hub: React PWA build + FastAPI runtime, non-root, data on /data.

FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-fund --no-audit
COPY web/ ./
RUN npm run build

FROM python:3.12-slim AS deps
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /venv
ENV PATH="/venv/bin:$PATH"
WORKDIR /src
COPY pyproject.toml README.md main.py ./
COPY core/ core/
COPY tools/ tools/
RUN pip install .

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/venv/bin:$PATH" \
    NEXUS_DATA_DIR=/data \
    NEXUS_WEB_HOST=0.0.0.0 \
    NEXUS_WEB_PORT=8743
RUN groupadd --system --gid 10001 nexus \
    && useradd --system --uid 10001 --gid nexus --home-dir /app --shell /usr/sbin/nologin nexus \
    && mkdir -p /data && chown nexus:nexus /data
WORKDIR /app
COPY --from=deps /venv /venv
COPY --chown=nexus:nexus main.py ./
COPY --chown=nexus:nexus core/ core/
COPY --chown=nexus:nexus tools/ tools/
COPY --from=web --chown=nexus:nexus /web/dist web/dist
USER 10001:10001
VOLUME ["/data"]
EXPOSE 8743
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8743/api/health', timeout=4).status == 200 else 1)"
ENTRYPOINT ["python", "main.py", "--mode", "web"]
