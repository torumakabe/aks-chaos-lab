# uv version pinned - keep in sync with the root pyproject.toml [tool.uv] required-version
# Run 'uv run scripts/tasks.py check-uv-version' from the repository root to verify consistency
#
# Build context MUST be the repository root because the workspace lockfile lives at the
# repository root. Build with:
#   docker build -f src/api/Dockerfile -t aks-chaos-lab:local .
FROM ghcr.io/astral-sh/uv:0.10.6 AS uv

FROM python:3.14-slim

# Copy uv binary from the uv image
COPY --from=uv /uv /uvx /bin/

WORKDIR /app

# Copy workspace metadata required to resolve from the workspace lock.
# Members are listed individually so Docker layer caching invalidates only when
# the relevant manifest changes.
COPY pyproject.toml uv.lock ./
COPY src/api/pyproject.toml ./src/api/
COPY src/external-sli-publisher/pyproject.toml ./src/external-sli-publisher/

# Install only the API package's locked dependencies (no dev, no project install).
# --package selects the workspace member; --no-install-project skips installing
# aks-chaos-lab-api itself so that we can copy the source tree later.
ENV UV_LINK_MODE=copy
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --package aks-chaos-lab-api --no-install-project --no-dev --compile-bytecode

# Copy application code
COPY src/api/app ./app

# Create non-root user and set permissions
RUN groupadd -g 10001 app && \
    useradd -r -u 10001 -g app app && \
    chown -R app:app /app

ENV PORT=8000
EXPOSE 8000

USER app:app

# Add virtual environment to PATH (uv places workspace .venv at the workspace root)
ENV PATH="/app/.venv/bin:$PATH"

# Run with uvicorn directly (not uv run)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--loop", "uvloop", "--http", "httptools", "--timeout-graceful-shutdown", "30"]
