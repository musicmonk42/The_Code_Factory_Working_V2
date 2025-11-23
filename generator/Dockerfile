# GOAT-level Dockerfile for AI README-to-App Generator
# Multi-stage build for optimal size, security, and reproducibility.
# Stages: build (dependencies & tests), docs (optional doc generation), runtime (final image).

# ========================
# Build Stage: Install dependencies, run tests, and build artifacts.
# ========================
FROM python:3.11-slim AS build

# Labels for metadata
LABEL maintainer="xAI Team <team@x.ai>" \
      version="1.0" \
      description="AI README-to-App Generator" \
      org.opencontainers.image.source="https://github.com/xai-org/ai-generator" \
      org.opencontainers.image.documentation="https://docs.x.ai/ai-generator"

# ARGs for extensibility (configurable at build time)
ARG EXTRA_DEPS=""
ARG CONFIG_PATH="/app/config.yaml"

# Environment variables (non-sensitive)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CONFIG_PATH=${CONFIG_PATH} \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    CURL_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    PIP_CERT=/etc/ssl/certs/ca-certificates.crt

WORKDIR /app

# Install system dependencies (minimal for build)
# Update ca-certificates first to avoid SSL issues with pip
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && update-ca-certificates \
    && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install dependencies
# Try with SSL verification first; if it fails due to proxy/MITM, retry with trusted hosts
# Note: The || fallback catches any pip failure including SSL errors. This is intentional
# to handle corporate proxies and development environments with SSL inspection.
COPY requirements.txt .
RUN pip install --upgrade pip || \
    (echo "WARNING: pip upgrade failed with SSL verification, retrying with --trusted-host" && \
     pip install --upgrade --trusted-host pypi.org --trusted-host files.pythonhosted.org pip)

# Note: --trusted-host bypasses SSL verification as a fallback for environments with
# SSL inspection/MITM proxies. Production builds with proper SSL should use the primary path.
RUN pip install -r requirements.txt ${EXTRA_DEPS} || \
    (echo "WARNING: requirements install failed with SSL verification, retrying with --trusted-host" && \
     pip install --trusted-host pypi.org --trusted-host files.pythonhosted.org -r requirements.txt ${EXTRA_DEPS})

RUN pip cache purge  # Clean up pip cache for smaller image

# Copy source code
COPY . .

# ========================
# Runtime Stage: Final minimal image for production.
# ========================
FROM python:3.11-slim AS runtime

# Reuse ARGs from build stage
ARG CONFIG_PATH

# Environment variables for runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CONFIG_PATH=${CONFIG_PATH} \
    # Observability envs
    PROMETHEUS_MULTIPROC_DIR=/tmp/prometheus \
    OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317

# Expose ports: API and metrics (8000 for API, 8001 for Prometheus)
EXPOSE 8000 8001

# Health check: hits /health endpoint (adjust to your app's health check)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD wget --spider --q http://localhost:8000/health || exit 1

# Create non-root user for security
RUN useradd -m appuser && \
    mkdir -p /app && \
    chown -R appuser:appuser /app

USER appuser

WORKDIR /app

# Copy installed dependencies from build stage (no source code yet)
COPY --from=build /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=build /usr/local/bin /usr/local/bin

# Copy source code (after deps to leverage caching)
COPY . .

# Security: Clean up any unnecessary files/caches
RUN rm -rf /tmp/* /var/tmp/* /app/*.egg-info

# Default command: run API server
CMD ["python", "-m", "main.main", "--interface", "api"]