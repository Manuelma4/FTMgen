# syntax=docker/dockerfile:1.7

FROM node:22-bookworm-slim AS frontend-builder

WORKDIR /build/frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY frontend/ ./
RUN npm run build


FROM python:3.11-slim-bookworm AS runtime

ARG FTMGEN_UID=10001
ARG FTMGEN_GID=10001

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    FTM_PORT=8060

RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        ca-certificates \
        libglib2.0-0 \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid "${FTMGEN_GID}" ftmgen \
    && useradd --uid "${FTMGEN_UID}" --gid "${FTMGEN_GID}" \
        --create-home --home-dir /home/ftmgen --shell /usr/sbin/nologin ftmgen

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY --chown=ftmgen:ftmgen app/ ./app/
COPY --chown=ftmgen:ftmgen web/ ./web/
COPY --from=frontend-builder --chown=ftmgen:ftmgen /build/frontend/dist/ ./frontend/dist/

RUN mkdir -p /app/output/uploads \
    && chown -R ftmgen:ftmgen /app/output /home/ftmgen

USER ftmgen

EXPOSE 8060

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8060/api/health', timeout=4).read()"]

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8060", "--proxy-headers", "--forwarded-allow-ips=*"]
