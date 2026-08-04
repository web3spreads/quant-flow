# 构建阶段：安装依赖到虚拟环境
FROM python:3.12-slim AS builder

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.10.5 /uv /bin/uv

# --frozen 按 uv.lock 精确复现依赖版本，防止构建时解析到不兼容的新版依赖
COPY pyproject.toml uv.lock .python-version README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# 运行阶段
FROM python:3.12-slim

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY . .

RUN useradd --create-home app \
    && mkdir -p /app/logs /app/data \
    && chown -R app:app /app
USER app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

CMD ["python", "main.py"]
