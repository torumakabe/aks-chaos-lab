# uv version pinned - keep in sync with .uv-version
# Run 'make check-uv-version' to verify consistency
FROM ghcr.io/astral-sh/uv:0.9.16 AS uv

FROM python:3.13-slim

# Copy uv binary from the uv image
COPY --from=uv /uv /uvx /bin/

WORKDIR /app

# Copy dependency files first (cache optimization)
COPY pyproject.toml uv.lock ./

# Install dependencies only (not the project)
ENV UV_LINK_MODE=copy
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project --compile-bytecode

# Copy application code
COPY app ./app

# Create non-root user and set permissions
RUN groupadd -g 10001 app && \
    useradd -r -u 10001 -g app app && \
    chown -R app:app /app

ENV PORT=8000
EXPOSE 8000

USER app:app

# Add virtual environment to PATH
ENV PATH="/app/.venv/bin:$PATH"

# Run with uvicorn directly (not uv run)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "30"]
