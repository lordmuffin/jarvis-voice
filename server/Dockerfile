# syntax=docker/dockerfile:1
# linux/amd64 only.

ARG PYTHON_IMAGE=python:3.12-slim
ARG NODE_IMAGE=node:22-slim
# Web dashboard (web/). The build context is server/, so the sources come in as a named
# context and the web-build stage is selected explicitly:
#   docker build -t jarvis-live:web \
#     --build-context webctx=web --build-arg WEB_SRC=webctx \
#     --build-arg WEB_DIST_SOURCE=web-build -f server/Dockerfile server
# Without those args the image is built with no dashboard (and without pulling node).
ARG WEB_SRC=web-src-empty
# A stage/image that provides /dist. Default is an empty stage, so nothing is copied.
ARG WEB_DIST_SOURCE=web-dist-empty

FROM ${PYTHON_IMAGE} AS builder
RUN pip install --no-cache-dir "uv>=0.8,<0.9"
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app
# Dependencies first for layer caching, then the project itself.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

FROM scratch AS web-src-empty

FROM ${WEB_SRC} AS web-src

FROM ${NODE_IMAGE} AS web-build
WORKDIR /web
COPY --from=web-src . ./
RUN npm ci && npm run build && mv dist /dist

FROM ${PYTHON_IMAGE} AS web-dist-empty
RUN mkdir /dist

FROM ${WEB_DIST_SOURCE} AS web-dist

FROM ${PYTHON_IMAGE} AS runtime
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    JARVIS_LIVE_DATA_DIR=/data \
    JARVIS_LIVE_INDEX_DIR=/index
# Only /data, /index and /tmp are writable; the rest of the root fs can be read-only.
RUN groupadd --gid 10001 jarvis \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin jarvis \
    && mkdir -p /data /index \
    && chown 10001:10001 /data /index \
    && chmod 1777 /tmp
COPY --from=builder /app/.venv /app/.venv
COPY --from=web-dist /dist /app/web/dist
WORKDIR /app
USER 10001:10001
EXPOSE 8080
CMD ["uvicorn","jarvis_live.app:app","--host","0.0.0.0","--port","8080"]
