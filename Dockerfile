# Multi-stage build for optimized image size
FROM python:3.12-slim AS builder

# Set working directory
WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:0.10.5 /uv /uvx /bin/

# Copy dependency files
COPY pyproject.toml .python-version README.md ./

# Install Python dependencies via uv（不使用 lock 文件，因本地 uv 版本较旧）
RUN uv sync --no-dev --no-install-project

# Final stage
FROM python:3.12-slim

# Set working directory
WORKDIR /app

# 安装 gosu（用于以指定 UID:GID 安全降权运行）
RUN apt-get update && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/* \
    && gosu nobody true

# 预创建可写目录（entrypoint 会根据 PUID/PGID 修复权限）
RUN mkdir -p /app/logs/decisions /app/logs/trades \
    /app/data/market_info /app/experiments

# Copy uv binary and virtual environment from builder
COPY --from=builder /bin/uv /bin/uv
COPY --from=builder /app/.venv /app/.venv

# Copy application code
COPY . .

# Copy and set permissions for entrypoint script
COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# 不设置 USER —— entrypoint 以 root 启动，动态匹配 PUID/PGID 后降权运行
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LOG_LEVEL=INFO \
    PATH="/app/.venv/bin:$PATH"

# Health check：检测 supervisord 进程存在（slim 镜像无 pgrep，用 /proc 扫描）
HEALTHCHECK --interval=60s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import os,sys; pids=[p for p in os.listdir('/proc') if p.isdigit()]; cmds=[open(f'/proc/{p}/cmdline').read() for p in pids if os.path.exists(f'/proc/{p}/cmdline')]; sys.exit(0 if any('supervisord' in c for c in cmds) else 1)"

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
