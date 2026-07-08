# syntax=docker/dockerfile:1

# Build:
#   docker build -t seer .
#
# Browse (config mounted read-only, databases persisted in a named volume):
#   docker run -it --rm -v seer-data:/data -v ~/.config/seer:/config/seer:ro seer
#
# Sync (kubeconfigs must be mounted too, and the kubeconfig_path entries in
# config.yaml must use the *container* paths, e.g. /kube/prod.yaml):
#   docker run -it --rm -v seer-data:/data -v ~/.config/seer:/config/seer:ro \
#       -v ~/.kube:/kube:ro seer seer-sync
#
# AI explanations: add -e GEMINI_API_KEY to the browse command.

# uv only exists in this build stage; the final image gets a plain venv.
FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Dependencies first, so they cache independently of source changes.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project

COPY README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-editable

FROM python:3.14-slim-bookworm

# seer reads config from $XDG_CONFIG_HOME/seer and keeps its databases in
# $XDG_DATA_HOME/seer; point both at top-level dirs that are easy to mount.
# COLORTERM opts Textual into 24-bit color.
ENV PATH="/app/.venv/bin:$PATH" \
    XDG_CONFIG_HOME=/config \
    XDG_DATA_HOME=/data \
    COLORTERM=truecolor

COPY --from=builder /app/.venv /app/.venv

CMD ["seer"]
