# syntax=docker/dockerfile:1

ARG PYTHON_VERSION=3.15

# ---------- 建置階段：以 uv 安裝相依套件 ----------
FROM python:${PYTHON_VERSION}-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# 先只安裝相依套件，讓程式碼變動時能沿用快取
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

# ---------- 執行階段 ----------
FROM python:${PYTHON_VERSION}-slim

RUN groupadd --system --gid 1000 echo \
    && useradd --system --uid 1000 --gid echo --home-dir /app echo

WORKDIR /app

COPY --from=builder --chown=echo:echo /app/.venv /app/.venv
COPY --chown=echo:echo prompt ./prompt
RUN mkdir -p /app/data && chown echo:echo /app/data

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MEMORY_DATABASE_PATH=/app/data/memory.db \
    CHARACTER_STATE_PATH=/app/data/character_state.json \
    PROMPT_DIR=/app/prompt

USER echo
VOLUME ["/app/data"]

# Bot 連線正常時每 30 秒更新心跳檔，超過 2 分鐘未更新即視為異常
HEALTHCHECK --interval=60s --timeout=10s --start-period=90s --retries=3 \
    CMD ["python", "-m", "echo.healthcheck"]

CMD ["python", "-m", "echo.main"]
