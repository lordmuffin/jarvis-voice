# syntax=docker/dockerfile:1
# jarvis-dispatch runner: one image for the three steps of a dispatch Job (runner.py).
# Built by the Server Image workflow as ghcr.io/lordmuffin/jarvis-dispatch-runner:sha-<7>,
# the same commit as the server that starts it (JARVIS_LIVE_GIT_SHA). linux/amd64 only.
#
# Carries what Claude Code needs to work on the allowlisted repos: git, Node 22, Python 3
# with uv (the server's toolchain; uv fetches the Python a project pins), and the pinned
# Claude Code CLI. Runs as uid 1000 (the Job's runAsUser).
FROM node:22-bookworm-slim

ARG CLAUDE_CODE_VERSION=2.1.283
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates python3 python3-pip curl \
    && rm -rf /var/lib/apt/lists/* \
    && pip3 install --no-cache-dir --break-system-packages "uv>=0.8,<0.9" \
    && npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
    && npm cache clean --force \
    && mkdir -p /home/runner && chown 1000:1000 /home/runner

COPY runner.py /usr/local/bin/jarvis-dispatch
ENV HOME=/home/runner \
    DISABLE_AUTOUPDATER=1 \
    PYTHONUNBUFFERED=1
USER 1000:1000
WORKDIR /home/runner
ENTRYPOINT ["python3", "/usr/local/bin/jarvis-dispatch"]
