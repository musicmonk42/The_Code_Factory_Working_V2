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
# pkg-config is required for libvirt-python build
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
 && update-ca-certificates \
 && apt-get install -y --no-install-recommends \
    build-essential git libmagic1 libvirt-dev pkg-config \
 && rm -rf /var/lib/apt/lists/*

# Create virtual environment for dependencies
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

# Fix pip to ensure pip._vendor.packaging is available
RUN python -m ensurepip --upgrade && python -m pip install --upgrade pip

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
     pip install --upgrade --trusted-host pypi.org --trusted-host files.pythonhosted.org pip setuptools wheel) || \
    (echo "ERROR: Failed to upgrade pip, setuptools, and wheel" && exit 1)

# Install unified platform dependencies
# Note: All three modules (generator, omnicore_engine, self_fixing_engineer) share
# the same requirements.txt as part of a unified platform.
# Note: --trusted-host bypasses SSL verification as a fallback for environments with
# SSL inspection/MITM proxies. Production builds with proper SSL should use the primary path.
ARG SKIP_HEAVY_DEPS=0
RUN set -e; \
    if [ "$SKIP_HEAVY_DEPS" = "1" ]; then \
        echo "Skipping heavy dependencies for CI build"; \
    elif [ -f requirements.txt ]; then \
        echo "Installing dependencies from requirements.txt..."; \
        if ! pip install --no-cache-dir -r requirements.txt; then \
            echo "WARNING: requirements install failed with SSL verification, retrying with --trusted-host"; \
            if ! pip install --no-cache-dir --trusted-host pypi.org --trusted-host files.pythonhosted.org --trusted-host github.com -r requirements.txt; then \
                echo "ERROR: Failed to install dependencies from requirements.txt"; \
                exit 1; \
            fi; \
        fi; \
        echo "Dependencies installed successfully"; \
    elif [ -f pyproject.toml ]; then \
        echo "Installing dependencies from pyproject.toml..."; \
        if ! pip install --no-cache-dir .; then \
            echo "WARNING: requirements install failed with SSL verification, retrying with --trusted-host"; \
            if ! pip install --no-cache-dir --trusted-host pypi.org --trusted-host files.pythonhosted.org --trusted-host github.com .; then \
                echo "ERROR: Failed to install dependencies from pyproject.toml"; \
                exit 1; \
            fi; \
        fi; \
        echo "Dependencies installed successfully"; \
    else \
        echo "WARNING: No requirements.txt or pyproject.toml found. Skipping dependency install."; \
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

# Verify critical dependencies are installed and importable
# This ensures the container will actually start successfully
# Following fail-fast principle: catch dependency issues at build time, not runtime
RUN if [ "$SKIP_HEAVY_DEPS" != "1" ]; then \
        echo "========================================"; \
        echo "Verifying critical dependencies..."; \
        echo "========================================"; \
        python -c "import uvicorn; print(f'✓ uvicorn {uvicorn.__version__} is installed')" || \
        (echo "ERROR: uvicorn is not importable. Dependencies were not installed correctly." && exit 1); \
        python -c "import fastapi; print(f'✓ fastapi {fastapi.__version__} is installed')" || \
        (echo "ERROR: fastapi is not importable. Dependencies were not installed correctly." && exit 1); \
        python -c "import asyncpg; print(f'✓ asyncpg {asyncpg.__version__} is installed')" || \
        (echo "ERROR: asyncpg is not importable. Check SSL/network during pip install." && exit 1); \
        python -c "import defusedxml; print('✓ defusedxml is installed')" || \
        (echo "ERROR: defusedxml is not importable. Check SSL/network during pip install." && exit 1); \
        python -c "import web3; print(f'✓ web3 {web3.__version__} is installed')" || \
        (echo "ERROR: web3 is not importable. Check SSL/network during pip install." && exit 1); \
        python -c "import aiohttp; print(f'✓ aiohttp {aiohttp.__version__} is installed')" || \
        (echo "ERROR: aiohttp is not importable. Check SSL/network during pip install." && exit 1); \
        python -c "import redis; print(f'✓ redis {redis.__version__} is installed')" || \
        (echo "ERROR: redis is not importable. Check SSL/network during pip install." && exit 1); \
        python -c "import sqlalchemy; print(f'✓ sqlalchemy {sqlalchemy.__version__} is installed')" || \
        (echo "ERROR: sqlalchemy is not importable. Check SSL/network during pip install." && exit 1); \
        echo "========================================"; \
        echo "✓ All critical dependencies verified successfully"; \
        echo "========================================"; \
    else \
        echo "Skipping dependency verification for CI build"; \
    fi

# Download SpaCy models to prevent runtime download issues
# Using both sm (small) and lg (large) for flexibility
RUN if [ "$SKIP_HEAVY_DEPS" != "1" ]; then \
        echo "========================================"; \
        echo "Downloading SpaCy models..."; \
        echo "========================================"; \
        # Download small model first (required for graceful degradation)
        python -m spacy download en_core_web_sm || \
        (echo "WARNING: Failed to download en_core_web_sm model"); \
        # Download large model (optional, for better accuracy)
        python -m spacy download en_core_web_lg || \
        (echo "WARNING: Failed to download en_core_web_lg model, testgen agent may not work properly"); \
        echo "✓ SpaCy model downloads complete"; \
    fi

# Pre-download NLTK data to prevent runtime download issues
RUN if [ "$SKIP_HEAVY_DEPS" != "1" ]; then \
        echo "========================================"; \
        echo "Downloading NLTK data..."; \
        echo "========================================"; \
        python -c "import nltk; \
            nltk.download('punkt', quiet=True); \
            nltk.download('stopwords', quiet=True); \
            nltk.download('vader_lexicon', quiet=True); \
            nltk.download('punkt_tab', quiet=True)" || \
        (echo "WARNING: Failed to download some NLTK data"); \
        echo "✓ NLTK data downloads complete"; \
    fi

# Copy the rest of the application
COPY . /app

###############################################
# Runtime stage: minimal image, non-root user
###############################################
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}" \
    APP_STARTUP=1 \
    SKIP_IMPORT_TIME_VALIDATION=1 \
    SPACY_WARNING_IGNORE=W007 \
    AWS_REGION="" \
    FALLBACK_ENCRYPTION_KEY="dGVzdC1rZXktZm9yLXB5dGVzdC0zMi1ieXRlczEyMzQ="

# Optional: curl for debugging and healthchecks
# Install ca-certificates first for SSL support
# Add graphviz for PlantUML diagram generation support
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
 && update-ca-certificates \
 && apt-get install -y --no-install-recommends curl git libmagic1 graphviz \
 && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -m -u 10001 appuser

WORKDIR /app

# Create directories and set ownership BEFORE copying files
# Create audit log directory for regulatory compliance logging
# Note: /app/logs is needed for default audit_log.jsonl path
# Note: /app/uploads is needed for job file uploads
RUN mkdir -p /opt/venv /app /var/log/analyzer_audit /app/logs /app/logs/analyzer_audit /app/uploads && \
    chown -R appuser:appuser /opt/venv /app /var/log/analyzer_audit /app/logs /app/uploads

# Bring in the venv and application source with proper ownership during copy
COPY --from=builder --chown=appuser:appuser /opt/venv /opt/venv
COPY --from=builder --chown=appuser:appuser /app /app

USER appuser

# The FastAPI server runs on port 8000 (or PORT env var), Prometheus metrics on port 9090
EXPOSE 8000 9090

# Start the unified platform API server
# Use PORT environment variable if set (Railway, Heroku, etc.), otherwise default to 8000
# Single worker mode for Railway deployment to ensure fast startup and reliable healthchecks
CMD ["python", "-m", "uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
