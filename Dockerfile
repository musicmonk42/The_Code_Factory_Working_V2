# syntax=docker/dockerfile:1.7
# 
# ==============================================================================
# Code Factory Platform - Production-Grade Dockerfile
# ==============================================================================
#
# This Dockerfile follows industry best practices and security standards:
# - Multi-stage build for minimal image size
# - Non-root user execution
# - Security scanning compatible (Trivy, Snyk, Clair)
# - CIS Docker Benchmark compliant
# - OWASP Container Security best practices
#
# Build arguments:
# - SKIP_HEAVY_DEPS: Set to 1 to skip installing heavy dependencies (useful for CI/testing)
#   Example: docker build --build-arg SKIP_HEAVY_DEPS=1 -t code-factory:latest .
#
# Security Scanning:
#   trivy image code-factory:latest
#   docker scan code-factory:latest
#   snyk container test code-factory:latest
#
# ==============================================================================

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

# Upgrade pip, setuptools, and wheel in one step to avoid conflicts
# Using python -m pip for reliability in virtual environments
# SECURITY: SSL verification is mandatory - no fallback to --trusted-host
# If builds fail due to SSL issues, fix the underlying CA certificate configuration
RUN python -m pip install --no-cache-dir --upgrade pip setuptools wheel

WORKDIR /app

# Copy only requirements first for better layer caching
COPY requirements.txt* master_requirements.txt* ./

# Note: All three modules (generator, omnicore_engine, self_fixing_engineer) are part
# of a single unified platform. Dependencies are installed from the root requirements.txt
# which includes all necessary packages for the entire platform.

# Install unified platform dependencies
# Note: All three modules (generator, omnicore_engine, self_fixing_engineer) share
# the same requirements.txt as part of a unified platform.
# SECURITY: SSL verification is enforced - builds will fail if certificates are invalid
ARG SKIP_HEAVY_DEPS=0
RUN set -e; \
    if [ "$SKIP_HEAVY_DEPS" = "1" ]; then \
        echo "Skipping heavy dependencies for CI build"; \
    elif [ -f requirements.txt ]; then \
        echo "Installing dependencies from requirements.txt..."; \
        python -m pip install --no-cache-dir -r requirements.txt; \
        echo "Dependencies installed successfully"; \
    elif [ -f pyproject.toml ]; then \
        echo "Installing dependencies from pyproject.toml..."; \
        python -m pip install --no-cache-dir .; \
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
    find /opt/venv -type d \( -name 'tests' -o -name 'test' \) -prune -exec rm -rf {} + 2>/dev/null || true

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
        python -c "import pydantic; print(f'✓ pydantic {pydantic.__version__} is installed')" || \
        (echo "ERROR: pydantic is not importable. Dependencies were not installed correctly." && exit 1); \
        python -c "import starlette; print(f'✓ starlette {starlette.__version__} is installed')" || \
        (echo "ERROR: starlette is not importable. Dependencies were not installed correctly." && exit 1); \
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

# Pre-download SpaCy models to prevent runtime download issues
# Using both sm (small) and lg (large) for flexibility
# Upgrade pip first to ensure we have the latest version for model downloads
RUN if [ "$SKIP_HEAVY_DEPS" != "1" ]; then \
        echo "========================================"; \
        echo "Upgrading pip and downloading SpaCy models..."; \
        echo "========================================"; \
        python -m pip install --no-cache-dir --upgrade pip setuptools wheel && \
        # Download small model first (required for graceful degradation)
        python -m spacy download en_core_web_sm && \
        # Download large model (required for docgen agent)
        python -m spacy download en_core_web_lg && \
        # Verify the large model loads successfully
        python -c "import spacy; nlp = spacy.load('en_core_web_lg'); print('✓ SpaCy model en_core_web_lg loaded successfully')" && \
        echo "✓ SpaCy model downloads complete"; \
    fi

# Pre-download NLTK data to prevent runtime download issues
# After this step, we clean up pip vendor files since pip is no longer needed
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
        # Clean up pip vendor files now that all pip operations are complete
        # This reduces image size - pip is not needed at runtime
        find /opt/venv -path '*/pip/_vendor/*' -prune -exec rm -rf {} + 2>/dev/null || true; \
    fi

# Copy the rest of the application
COPY . /app

###############################################
# Runtime stage: minimal image, non-root user
###############################################
FROM python:3.11-slim AS runtime

# Image metadata for better maintainability and security scanning
# Following OCI Image Format Specification
# https://github.com/opencontainers/image-spec/blob/main/annotations.md
LABEL org.opencontainers.image.title="Code Factory Platform"
LABEL org.opencontainers.image.description="Unified AI-driven platform for automated software development and maintenance"
LABEL org.opencontainers.image.vendor="Novatrax Labs"
LABEL org.opencontainers.image.version="1.0.0"
LABEL org.opencontainers.image.licenses="Proprietary"
LABEL org.opencontainers.image.source="https://github.com/musicmonk42/The_Code_Factory_Working_V2"
LABEL org.opencontainers.image.documentation="https://github.com/musicmonk42/The_Code_Factory_Working_V2/blob/main/README.md"
LABEL org.opencontainers.image.created="2024"
LABEL maintainer="support@novatraxlabs.com"
LABEL security.scan="true"
LABEL security.trivy="enabled"

# Environment variables for the runtime stage
# SECURITY: No hardcoded encryption keys - must be provided at runtime
# AUDIT CRYPTO: Set AUDIT_CRYPTO_MODE to "full" when AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64 is configured
# FEATURE FLAGS: Set to "1" to enable, "0" to disable, "auto" for auto-detection
# PARALLEL AGENT LOADING: Enabled by default for faster startup
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}" \
    APP_STARTUP=1 \
    SKIP_IMPORT_TIME_VALIDATION=1 \
    SPACY_WARNING_IGNORE=W007 \
    AWS_REGION="" \
    AUDIT_CRYPTO_MODE="disabled" \
    AUDIT_CRYPTO_ALLOW_INIT_FAILURE="1" \
    ENABLE_DATABASE="1" \
    ENABLE_FEATURE_STORE="auto" \
    ENABLE_HSM="auto" \
    ENABLE_LIBVIRT="auto" \
    PARALLEL_AGENT_LOADING="1" \
    LAZY_LOAD_ML="1"

# Optional: curl for debugging and healthchecks
# Install ca-certificates first for SSL support
# Add graphviz for PlantUML diagram generation support
# Add libvirt-dev and pkg-config for virtualization support (optional)
# Add wget for Trivy installation
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
 && update-ca-certificates \
 && apt-get install -y --no-install-recommends curl git libmagic1 graphviz libvirt-dev pkg-config wget \
 && rm -rf /var/lib/apt/lists/*

# Install Trivy for security scanning (deployment validation)
# Trivy is required for deploy agent security scanning functionality
RUN wget -qO - https://aquasecurity.github.io/trivy-repo/deb/public.key | apt-key add - && \
    echo "deb https://aquasecurity.github.io/trivy-repo/deb $(lsb_release -sc) main" | tee -a /etc/apt/sources.list.d/trivy.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends trivy && \
    rm -rf /var/lib/apt/lists/* && \
    trivy --version

# Create non-root user with restricted shell for security
# Using specific UID/GID to prevent privilege escalation attacks
# Following CIS Docker Benchmark 4.1 - Create a user for the container
# Note: /bin/false prevents interactive login but allows direct command execution (e.g., python)
RUN groupadd -g 10001 appgroup && \
    useradd -m -u 10001 -g appgroup -s /bin/false appuser && \
    # Lock the account to prevent password login
    passwd -l appuser

WORKDIR /app

# Create directories and set ownership BEFORE copying files
# Create audit log directory for regulatory compliance logging
# Note: /app/logs is needed for default audit_log.jsonl path
# Note: /app/uploads is needed for job file uploads
RUN mkdir -p /opt/venv /app /var/log/analyzer_audit /app/logs /app/logs/analyzer_audit /app/uploads && \
    chown -R appuser:appgroup /opt/venv /app /var/log/analyzer_audit /app/logs /app/uploads

# Bring in the venv and application source with proper ownership during copy
COPY --from=builder --chown=appuser:appgroup /opt/venv /opt/venv
COPY --from=builder --chown=appuser:appgroup /app /app

USER appuser

# The FastAPI server runs on port 8080 (Railway) or PORT env var, Prometheus metrics on port 9090
EXPOSE 8080 9090

# Docker healthcheck to verify the container is running properly
# Checks the /health endpoint which returns 200 if the API is up
# Uses PORT env var if set (Railway sets it to 8080), otherwise defaults to 8080
# Starts checking after 60 seconds to allow startup time (agents load in background)
# Times out after 10 seconds, retries 3 times before marking unhealthy
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8080}/health || exit 1

# Start the unified platform API server
# Single worker mode for Railway deployment to ensure fast startup and reliable healthchecks
# Using server/run.py which respects PORT environment variable (defaults to 8000, Railway sets to 8080)
CMD ["python", "server/run.py", "--host", "0.0.0.0", "--workers", "1"]
