# syntax=docker/dockerfile:1
# linux/amd64 only.

ARG PYTHON_IMAGE=python:3.12-slim
# Phase C3: set WEB_DIST_SOURCE to a stage/image that provides /dist to bundle the web UI.
# Default is an empty stage, so nothing is copied.
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
    uv sync --frozen --no-dev

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
