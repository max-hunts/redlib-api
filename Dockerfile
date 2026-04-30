FROM python:3.12-slim

# Copy uv binary from the official image
COPY --from=ghcr.io/astral-sh/uv:0.11.8@sha256:3b7b60a81d3c57ef471703e5c83fd4aaa33abcd403596fb22ab07db85ae91347 /uv /usr/local/bin/uv

WORKDIR /app

# Install dependencies first (layer-cached when source changes but deps don't)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

# Copy source
COPY redlib_api/ ./redlib_api/
COPY templates/ ./templates/

# Non-root user — create data dir before switching so the volume mount inherits appuser ownership
RUN useradd --system --no-create-home appuser && mkdir -p /app/data && chown -R appuser:appuser /app/data
USER appuser

EXPOSE 5001

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${REDLIB_API_PORT:-5001}/health')" || exit 1

CMD ["sh", "-c", "exec uvicorn redlib_api.server:app --host \"${REDLIB_API_HOST:-0.0.0.0}\" --port \"${REDLIB_API_PORT:-5001}\""]
