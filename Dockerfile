# syntax=docker/dockerfile:1
FROM node:24-bookworm-slim AS frontend-build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY frontend/ ./frontend/
RUN npm run build

FROM python:3.11-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/tmp \
    GEOAI_ALLOWED_HOSTS=localhost,127.0.0.1
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libstdc++6 libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 geoai \
    && useradd --uid 10001 --gid geoai --no-create-home --home-dir /tmp geoai \
    && mkdir -p /app/data \
    && chown 10001:10001 /app/data
COPY requirements-raster.txt ./
RUN python -m pip install --only-binary=:all: -r requirements-raster.txt
COPY --chown=10001:10001 server.py ./
COPY --chown=10001:10001 backend/ ./backend/
COPY --chown=10001:10001 data_sources/ ./data_sources/
COPY --chown=10001:10001 map_sources/ ./map_sources/
COPY --chown=10001:10001 deploy/docker/entrypoint.py deploy/docker/healthcheck.py ./deploy/docker/
COPY --from=frontend-build --chown=10001:10001 /app/dist/ ./dist/
USER 10001:10001
EXPOSE 8016
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "/app/deploy/docker/healthcheck.py"]
ENTRYPOINT ["python", "/app/deploy/docker/entrypoint.py"]
