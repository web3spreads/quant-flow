# Multi-stage build for optimized image size
FROM python:3.13-slim AS builder

# Set working directory
WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Copy dependency files
COPY pyproject.toml uv.lock* .python-version README.md ./

# Install Python dependencies via uv
RUN uv sync --frozen --no-dev --no-install-project

# Final stage
FROM python:3.13-slim

# Set working directory
WORKDIR /app

# Create non-root user for security
RUN useradd -m -u 1000 -s /bin/bash quantflow && \
    mkdir -p /app/logs/decisions /app/logs/trades && \
    chown -R quantflow:quantflow /app

# Copy uv binary and virtual environment from builder
COPY --from=builder /bin/uv /bin/uv
COPY --from=builder /app/.venv /app/.venv

# Copy application code
COPY --chown=quantflow:quantflow . .

# Copy and set permissions for entrypoint script
COPY --chown=quantflow:quantflow docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Switch to non-root user
USER quantflow

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LOG_LEVEL=INFO \
    PATH="/app/.venv/bin:$PATH"

# Health check (checks if the process is running)
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD pgrep -f "python main.py" || exit 1

# Use entrypoint script to handle permissions and startup
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
