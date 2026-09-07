# uv is pinned for reproducible builds and must equal the minimum version allowed
# by the root pyproject.toml [tool.uv] required-version range.
# Run 'uv run --no-project scripts/tasks.py check-uv-version' to verify the policy.
#
# Build context MUST be the repository root because the workspace lockfile lives at the
# repository root. Build with:
#   docker build -f src/api/Dockerfile -t aks-chaos-lab:local .
FROM ghcr.io/astral-sh/uv:0.12.10@sha256:2bb3ebca0a796a155094a27773d290c4b074572e6107f171d88d086682fd2500 AS uv

FROM python:3.14-slim@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6

# Copy uv binary from the uv image
COPY --from=uv /uv /uvx /bin/

WORKDIR /app

# Copy workspace metadata required to resolve from the workspace lock.
# Members are listed individually so Docker layer caching invalidates only when
# the relevant manifest changes.
COPY pyproject.toml uv.lock ./
COPY src/api/pyproject.toml ./src/api/
COPY src/external-sli-publisher/pyproject.toml ./src/external-sli-publisher/
COPY scripts/approved_index_config.py ./scripts/
COPY scripts/public_lock.py ./scripts/

# Export only the API package's locked dependencies, then resolve the artifacts
# through the package index configured for this build environment.
ENV UV_LINK_MODE=copy
ARG UV_INDEX_MODE=public
ARG UV_INDEX_CONFIG_SHA256=public
RUN --mount=type=cache,id=uv-${UV_INDEX_MODE}-${UV_INDEX_CONFIG_SHA256},target=/root/.cache/uv \
    --mount=type=secret,id=uv-config,target=/root/.config/uv/uv.toml,required=false \
    approved_index_path=; \
    case "$UV_INDEX_MODE" in \
      public) \
        test "$UV_INDEX_CONFIG_SHA256" = public \
        && test ! -e /root/.config/uv/uv.toml \
        ;; \
      approved-index) \
        test -e /root/.config/uv/uv.toml \
        && printf '%s  %s\n' "$UV_INDEX_CONFIG_SHA256" /root/.config/uv/uv.toml \
          | sha256sum -c - \
        && python scripts/approved_index_config.py /root/.config/uv/uv.toml \
        && approved_index_path=/root/.config/uv/uv.toml \
        ;; \
      *) \
        echo "error: UV_INDEX_MODE must be public or approved-index" >&2; \
        exit 1 \
        ;; \
    esac \
    && if [ -n "$approved_index_path" ]; then export UV_CONFIG_FILE="$approved_index_path"; fi \
    && python scripts/public_lock.py lock pyproject.toml uv.lock \
    && uv export --quiet --frozen --package aks-chaos-lab-api --no-dev \
      --no-emit-workspace --no-emit-index-url \
      --output-file /tmp/requirements.txt \
    && python scripts/public_lock.py requirements /tmp/requirements.txt \
    && uv venv /app/.venv \
    && uv pip sync --require-hashes /tmp/requirements.txt \
      --python /app/.venv/bin/python --compile-bytecode \
    && rm -f /tmp/requirements.txt /tmp/uv.toml

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
