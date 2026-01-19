# syntax=docker/dockerfile:1.7
# 
# Build arguments:
# - SKIP_HEAVY_DEPS: Set to 1 to skip installing heavy dependencies (useful for CI/testing)
#   Example: docker build --build-arg SKIP_HEAVY_DEPS=1 -t code-factory:latest .

###############################################
# Builder stage: install Python dependencies
###############################################
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    CURL_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    PIP_CERT=/etc/ssl/certs/ca-certificates.crt

# Install build tools for any packages that need compiling
# Update ca-certificates first to avoid SSL issues with pip
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
 && update-ca-certificates \
 && apt-get install -y --no-install-recommends \
    build-essential git \
 && rm -rf /var/lib/apt/lists/*

# Create virtual environment for dependencies
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

WORKDIR /app

# Copy only requirements first for better layer caching
COPY requirements.txt* master_requirements.txt* ./

# Note: All three modules (generator, omnicore_engine, self_fixing_engineer) are part
# of a single unified platform. Dependencies are installed from the root requirements.txt
# which includes all necessary packages for the entire platform.

# Upgrade packaging tools and install dependencies if found
# Try with SSL verification first; if it fails due to proxy/MITM, retry with trusted hosts
# Note: The || fallback catches any pip failure including SSL errors. This is intentional
# to handle corporate proxies and development environments with SSL inspection.
RUN pip install --upgrade pip setuptools wheel || \
    (echo "WARNING: pip upgrade failed with SSL verification, retrying with --trusted-host" && \
     pip install --upgrade --trusted-host pypi.org --trusted-host files.pythonhosted.org pip setuptools wheel)

# Install unified platform dependencies
# Note: All three modules (generator, omnicore_engine, self_fixing_engineer) share
# the same requirements.txt as part of a unified platform.
# Note: --trusted-host bypasses SSL verification as a fallback for environments with
# SSL inspection/MITM proxies. Production builds with proper SSL should use the primary path.
ARG SKIP_HEAVY_DEPS=0
RUN if [ "$SKIP_HEAVY_DEPS" = "1" ]; then \
        echo "Skipping heavy dependencies for CI build"; \
    elif [ -f requirements.txt ]; then \
        pip install --no-cache-dir -r requirements.txt || \
        (echo "WARNING: requirements install failed with SSL verification, retrying with --trusted-host" && \
         pip install --no-cache-dir --trusted-host pypi.org --trusted-host files.pythonhosted.org -r requirements.txt); \
    elif [ -f pyproject.toml ]; then \
        pip install --no-cache-dir . || \
        (echo "WARNING: requirements install failed with SSL verification, retrying with --trusted-host" && \
         pip install --no-cache-dir --trusted-host pypi.org --trusted-host files.pythonhosted.org .); \
    else \
        echo "No requirements.txt or pyproject.toml found. Skipping dependency install."; \
    fi; \
    # Clean up pip cache, temp files, and package caches to free disk space
    rm -rf /root/.cache/* /tmp/* /var/tmp/* || true; \
    # Remove pip's wheel cache and build artifacts
    find /opt/venv -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true; \
    find /opt/venv -type f -name '*.pyc' -delete 2>/dev/null || true; \
    find /opt/venv -type f -name '*.pyo' -delete 2>/dev/null || true; \
    # Enhanced cleanup to reduce size of files being copied to runtime stage
    find /opt/venv -type d \( -name 'tests' -o -name 'test' \) -prune -exec rm -rf {} + 2>/dev/null || true; \
    find /opt/venv -path '*/pip/_vendor/*' -prune -exec rm -rf {} + 2>/dev/null || true

# Copy the rest of the application
COPY . /app

###############################################
# Runtime stage: minimal image, non-root user
###############################################
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}"

# Optional: curl for debugging and healthchecks
# Install ca-certificates first for SSL support
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
 && update-ca-certificates \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -m -u 10001 appuser

WORKDIR /app

# Create directories and set ownership BEFORE copying files
RUN mkdir -p /opt/venv /app && chown appuser:appuser /opt/venv /app

# Bring in the venv and application source with proper ownership during copy
COPY --from=builder --chown=appuser:appuser /opt/venv /opt/venv
COPY --from=builder --chown=appuser:appuser /app /app

USER appuser

# The FastAPI server runs on port 8000
EXPOSE 8000

# Start the FastAPI server using uvicorn
CMD ["python", "-m", "uvicorn", "generator.main.api:api", "--host", "0.0.0.0", "--port", "8000"]
