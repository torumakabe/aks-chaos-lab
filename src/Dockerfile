FROM python:3.13-slim

WORKDIR /app

# Copy only requirements first and install dependencies (optimize build cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app ./app

# Create non-root user and set permissions
RUN groupadd -g 10001 app && \
    useradd -r -u 10001 -g app app && \
    mkdir -p /app && chown -R app:app /app

ENV PORT=8000
EXPOSE 8000

USER app:app

# Configure uvicorn with graceful shutdown timeout
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "30"]
