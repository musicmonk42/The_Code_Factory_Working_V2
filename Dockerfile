# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

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
# - INSTALL_AI_DEPS: Set to 1 to install optional Tier-1 AI capabilities (qiskit, nengo, opencv)
#   Example: docker build --build-arg INSTALL_AI_DEPS=1 -t code-factory:ai-full .
#   This installs requirements-ai.txt AFTER the main requirements.txt.
#   Omit or set to 0 (default) to keep the base image lean.
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
# Audio libraries required for SpeechRecognition/PyAudio (PR #963):
#   - portaudio19-dev: PortAudio development files for PyAudio compilation
#   - libasound2-dev: ALSA sound system development files
#   - flac, libflac-dev: FLAC audio format support
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
 && update-ca-certificates \
 && apt-get install -y --no-install-recommends \
    build-essential git libmagic1 libvirt-dev pkg-config \
    portaudio19-dev libasound2-dev flac libflac-dev \
 && rm -rf /var/lib/apt/lists/*

# Create virtual environment for dependencies
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

# Upgrade pip, setuptools, and wheel in one step to avoid conflicts
# Using python -m pip for reliability in virtual environments
# SECURITY: SSL verification is mandatory - no fallback to --trusted-host
# If builds fail due to SSL issues, fix the underlying CA certificate configuration
RUN python -m pip install --no-cache-dir --upgrade pip setuptools wheel

# Create directories for ML models and data (will be populated if SKIP_HEAVY_DEPS != 1)
# These directories must exist for COPY commands in runtime stage
RUN mkdir -p /opt/nltk_data /opt/huggingface_cache /opt/chroma_cache

WORKDIR /app

# Copy only requirements first for better layer caching
COPY requirements.txt* master_requirements.txt* requirements-ai.txt* ./

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

# Install optional Tier-1 AI capability dependencies (qiskit, nengo, opencv-python-headless).
# These are disabled by default to keep the base image lean.
# Enable at build time: docker build --build-arg INSTALL_AI_DEPS=1 ...
ARG INSTALL_AI_DEPS=0
RUN set -e; \
    if [ "$INSTALL_AI_DEPS" = "1" ] && [ "$SKIP_HEAVY_DEPS" != "1" ]; then \
        if [ -f requirements-ai.txt ]; then \
            echo "========================================"; \
            echo "Installing optional AI dependencies (Tier-1 capabilities)..."; \
            echo "========================================"; \
            python -m pip install --no-cache-dir -r requirements-ai.txt; \
            echo "✓ Optional AI dependencies installed successfully"; \
        else \
            echo "WARNING: requirements-ai.txt not found, skipping optional AI deps"; \
        fi; \
    else \
        echo "Skipping optional AI dependencies (set INSTALL_AI_DEPS=1 to enable)"; \
    fi

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
        python -c "import pylint; print(f'✓ pylint {pylint.__version__} is installed')" || \
        (echo "ERROR: pylint is not importable. Required for critique agent linting." && exit 1); \
        echo "========================================"; \
        echo "✓ All critical dependencies verified successfully"; \
        echo "========================================"; \
    else \
        echo "Skipping dependency verification for CI build"; \
    fi

# Pre-download SpaCy models to prevent runtime download issues
# FIX: Only download English models since Presidio is configured for English-only
# Multilingual models (es, it, pl) are not needed and waste ~600MB+ of image space
# Using both sm (small) and lg (large) for flexibility
# Upgrade pip first to ensure we have the latest version for model downloads
RUN if [ "$SKIP_HEAVY_DEPS" != "1" ]; then \
        echo "========================================"; \
        echo "Upgrading pip and downloading SpaCy models..."; \
        echo "========================================"; \
        python -m pip install --no-cache-dir --upgrade pip setuptools wheel && \
        # Download English models (required for PII detection)
        python -m spacy download en_core_web_sm && \
        python -m spacy download en_core_web_lg && \
        # Verify the large model loads successfully
        python -c "import spacy; nlp = spacy.load('en_core_web_lg'); print('✓ SpaCy model en_core_web_lg loaded successfully')" && \
        echo "✓ SpaCy model downloads complete (English only)"; \
    fi

# Pre-download NLTK data to prevent runtime download issues
# Download to /opt/nltk_data (accessible by non-root user) instead of /root/nltk_data
# After this step, we clean up pip vendor files since pip is no longer needed
RUN if [ "$SKIP_HEAVY_DEPS" != "1" ]; then \
        echo "========================================"; \
        echo "Downloading NLTK data..."; \
        echo "========================================"; \
        NLTK_DATA=/opt/nltk_data python -c "import nltk; nltk.download('punkt', download_dir='/opt/nltk_data')" \
            && echo "✓ Downloaded punkt" || echo "✗ Failed to download punkt"; \
        NLTK_DATA=/opt/nltk_data python -c "import nltk; nltk.download('stopwords', download_dir='/opt/nltk_data')" \
            && echo "✓ Downloaded stopwords" || echo "✗ Failed to download stopwords"; \
        NLTK_DATA=/opt/nltk_data python -c "import nltk; nltk.download('vader_lexicon', download_dir='/opt/nltk_data')" \
            && echo "✓ Downloaded vader_lexicon" || echo "✗ Failed to download vader_lexicon"; \
        NLTK_DATA=/opt/nltk_data python -c "import nltk; nltk.download('punkt_tab', download_dir='/opt/nltk_data')" \
            && echo "✓ Downloaded punkt_tab" || echo "✗ Failed to download punkt_tab"; \
        echo "Verifying NLTK data..."; \
        NLTK_DATA=/opt/nltk_data python -c "import nltk; nltk.data.path.insert(0, '/opt/nltk_data'); nltk.data.find('tokenizers/punkt'); print('✓ Verified punkt')" \
            || echo "✗ MISSING: punkt at tokenizers/punkt"; \
        NLTK_DATA=/opt/nltk_data python -c "import nltk; nltk.data.path.insert(0, '/opt/nltk_data'); nltk.data.find('corpora/stopwords'); print('✓ Verified stopwords')" \
            || echo "✗ MISSING: stopwords at corpora/stopwords"; \
        NLTK_DATA=/opt/nltk_data python -c "import nltk; nltk.data.path.insert(0, '/opt/nltk_data'); nltk.data.find('sentiment/vader_lexicon'); print('✓ Verified vader_lexicon')" \
            || echo "✗ MISSING: vader_lexicon at sentiment/vader_lexicon"; \
        NLTK_DATA=/opt/nltk_data python -c "import nltk; nltk.data.path.insert(0, '/opt/nltk_data'); nltk.data.find('tokenizers/punkt_tab'); print('✓ Verified punkt_tab')" \
            || echo "✗ MISSING: punkt_tab at tokenizers/punkt_tab"; \
        echo "✓ NLTK data downloads complete"; \
        # Clean up pip vendor files now that all pip operations are complete
        # This reduces image size - pip is not needed at runtime
        find /opt/venv -path '*/pip/_vendor/*' -prune -exec rm -rf {} + 2>/dev/null || true; \
    fi

# Pre-download HuggingFace transformer models to prevent runtime downloads
# Download to /opt/huggingface_cache (accessible by non-root user)
# The docgen agent uses facebook/bart-large-cnn for summarization
# Note: Using HF_HOME instead of deprecated TRANSFORMERS_CACHE
RUN if [ "$SKIP_HEAVY_DEPS" != "1" ]; then \
        echo "========================================"; \
        echo "Downloading HuggingFace models..."; \
        echo "========================================"; \
        HF_HOME=/opt/huggingface_cache \
        python -c "from transformers import pipeline; \
            print('Downloading facebook/bart-large-cnn model...'); \
            pipeline('summarization', model='facebook/bart-large-cnn'); \
            print('✓ Model download complete')" && \
        echo "✓ HuggingFace model downloads complete" || \
        echo "WARNING: Failed to download HuggingFace model"; \
    fi

# Pre-cache ChromaDB ONNX embedding model to avoid runtime downloads
# The testgen agent uses ChromaDB which requires the all-MiniLM-L6-v2 ONNX model (~79MB)
# Downloading at runtime adds significant time to the testgen timeout budget
RUN if [ "$SKIP_HEAVY_DEPS" != "1" ]; then \
        echo "========================================"; \
        echo "Pre-caching ChromaDB ONNX embedding model..."; \
        echo "========================================"; \
        CHROMA_CACHE_DIR=/opt/chroma_cache \
        python -c "import chromadb; \
            from chromadb.config import Settings; \
            client = chromadb.Client(Settings(anonymized_telemetry=False, is_persistent=False)); \
            col = client.get_or_create_collection('warmup'); \
            col.add(documents=['warmup text'], ids=['warmup']); \
            print('✓ ChromaDB ONNX model cached successfully')" && \
        echo "✓ ChromaDB ONNX model pre-cached"; \
    fi

# Copy the rest of the application
# This includes all configuration files:
#   - generator/config.yaml (default runner configuration)
#   - generator/runner/runner_config.yaml (documentation/reference format)
#   - self_fixing_engineer/crew_config.yaml
#   - deploy_templates/ (Jinja2 templates for deployment generation)
#   - deploy_templates/few_shot_examples/ (pre-seeded K8s/Docker/Helm few-shot JSON examples)
#   - audit configurations
# Use RUNNER_CONFIG_PATH environment variable at runtime to specify custom config location
COPY . /app

###############################################
# Runtime stage: minimal image, non-root user
###############################################
FROM python:3.11-slim AS runtime

# Build arguments for reproducible builds and OCI metadata
ARG BUILD_DATE
ARG HELM_VERSION=3.16.4
ARG HELM_SHA256=fc307327959aa38ed8f9f7e66d45492bb022a66c3e5da6063958254b9767d179
ARG KUBECTL_VERSION=1.32.3
ARG KUBECTL_SHA256=ab209d0c5134b61486a0486585604a616a5bb2fc07df46d304b3c95817b2d79f

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
LABEL org.opencontainers.image.created=${BUILD_DATE:-2025}
LABEL maintainer="support@novatraxlabs.com"
LABEL security.scan="true"
LABEL security.trivy="enabled"

# Set shell to use pipefail for better error handling in RUN commands
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Environment variables for the runtime stage
# SECURITY: No hardcoded encryption keys - must be provided at runtime
# AUDIT CRYPTO: "software" mode requires AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64 at runtime.
#               AUDIT_CRYPTO_ALLOW_INIT_FAILURE=1 lets the container start without the key so that
#               operators can inject it via K8s Secret / Railway variable without a rebuild.
# FEATURE FLAGS: Set to "1" to enable, "0" to disable. "auto" is NEVER used — explicit values
#               prevent accidental exposure of powerful subsystems (CIS Docker Benchmark 4.6).
# PARALLEL AGENT LOADING: Enabled by default for faster startup.
# NLTK_DATA: /opt/nltk_data (pre-downloaded; accessible by appuser, not /root/nltk_data).
# HF_HOME: /opt/huggingface_cache for pre-downloaded models (replaces deprecated TRANSFORMERS_CACHE).
# MPLCONFIGDIR: /tmp/matplotlib to prevent permission errors.
# KAFKA: Multiple variables for backward-compatibility across components:
#   - KAFKA_ENABLED: Primary flag checked by ArbiterConfig (via Pydantic validation_alias).
#   - ENABLE_KAFKA: Legacy alias checked by older components.
#   - USE_KAFKA_INGESTION / USE_KAFKA_AUDIT: Fine-grained Kafka routing controls.
# PIPELINE_CODEGEN_TIMEOUT_SECONDS: Per-job outer budget for multi-pass code generation (900 s / 15 min).
# ENSEMBLE_PROVIDER_TIMEOUT_SECONDS: Per-provider timeout for ensemble LLM calls (300 s / 5 min).
# ENCRYPTION_MODE: Encryption backend — local | aws_kms | azure_keyvault (see SECURITY_CONFIGURATION.md).
# SUPPRESS_SECURITY_WARNINGS: Set to "1" in development to silence security-posture log warnings.
# PLUGIN_INTEGRITY_CHECK_ENABLED: Enable plugin hash verification via HASH_MANIFEST.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}" \
    APP_STARTUP=1 \
    SKIP_IMPORT_TIME_VALIDATION=1 \
    SPACY_WARNING_IGNORE=W007 \
    AWS_REGION="" \
    AUDIT_CRYPTO_MODE="software" \
    AUDIT_CRYPTO_ALLOW_INIT_FAILURE="1" \
    ENABLE_DATABASE="1" \
    ENABLE_FEATURE_STORE="0" \
    ENABLE_HSM="0" \
    ENABLE_LIBVIRT="0" \
    KAFKA_ENABLED="true" \
    ENABLE_KAFKA="true" \
    USE_KAFKA_INGESTION="true" \
    USE_KAFKA_AUDIT="true" \
    PARALLEL_AGENT_LOADING="1" \
    LAZY_LOAD_ML="1" \
    TOKENIZERS_PARALLELISM="false" \
    NLTK_DATA="/opt/nltk_data" \
    HF_HOME="/opt/huggingface_cache" \
    CHROMA_CACHE_DIR="/opt/chroma_cache" \
    MPLCONFIGDIR="/tmp/matplotlib" \
    TLDEXTRACT_CACHE="/tmp/tldextract_cache" \
    ARBITER_WORLD_SIZE="10" \
    ARBITER_ROLE="admin" \
    POLICY_CONFIG_FILE_PATH="/app/data/policies.json" \
    PIPELINE_CODEGEN_TIMEOUT_SECONDS="900" \
    ENSEMBLE_PROVIDER_TIMEOUT_SECONDS="300" \
    CODEGEN_MULTIPASS_ENDPOINT_THRESHOLD="25" \
    ENCRYPTION_MODE="local" \
    SUPPRESS_SECURITY_WARNINGS="0" \
    PLUGIN_INTEGRITY_CHECK_ENABLED="false"
    # SENTRY_DSN / SENTRY_ENVIRONMENT: Set at deployment time for error tracking.
    # Example: SENTRY_DSN=https://<key>@<org>.ingest.sentry.io/<project>

# Optional: curl for debugging and healthchecks
# Install ca-certificates first for SSL support
# Add graphviz for PlantUML diagram generation support
# Add libvirt-dev and pkg-config for virtualization support (optional)
# Add libportaudio2 for SpeechRecognition runtime audio support (PR #963)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
 && update-ca-certificates \
 && apt-get install -y --no-install-recommends \
    curl git libmagic1 graphviz libvirt-dev pkg-config libportaudio2 \
 && rm -rf /var/lib/apt/lists/*

# Install Trivy for security scanning (deployment validation)
# Trivy is required for deploy agent security scanning functionality
# Using direct binary download for better cross-platform compatibility
# (APT repository may not support all Debian versions like 'trixie')
# TRIVY_VERSION can be overridden at build time to pin a specific version
# When changing TRIVY_VERSION, update TRIVY_SHA256 from trivy_<version>_checksums.txt
ARG TRIVY_VERSION=0.69.0
ARG TRIVY_SHA256=fff5813d6888fa6f8bd40042a08c4f072b3e65aec9f13dd9ab1d7b26146ad046
RUN curl -sfL --retry 3 --retry-delay 5 --retry-all-errors -o /tmp/trivy.tar.gz "https://github.com/aquasecurity/trivy/releases/download/v${TRIVY_VERSION}/trivy_${TRIVY_VERSION}_Linux-64bit.tar.gz" && \
    echo "${TRIVY_SHA256}  /tmp/trivy.tar.gz" | sha256sum -c - && \
    tar xzf /tmp/trivy.tar.gz -C /usr/local/bin trivy && \
    rm /tmp/trivy.tar.gz && \
    trivy --version

# Install Hadolint for Dockerfile linting (deployment validation)
# Hadolint is optional for deploy agent linting functionality
# Using direct binary download for simplicity
ARG HADOLINT_VERSION=2.12.0
ARG HADOLINT_SHA256=56de6d5e5ec427e17b74fa48d51271c7fc0d61244bf5c90e828aab8362d55010
RUN curl -sfL --retry 3 --retry-delay 5 --retry-all-errors -o /usr/local/bin/hadolint "https://github.com/hadolint/hadolint/releases/download/v${HADOLINT_VERSION}/hadolint-Linux-x86_64" && \
    echo "${HADOLINT_SHA256}  /usr/local/bin/hadolint" | sha256sum -c - && \
    chmod +x /usr/local/bin/hadolint && \
    hadolint --version

# Install Node.js for TypeScript syntax validation
# Using NodeSource setup script for latest stable version (Node.js 20.x LTS)
# Required for deploy agent to validate TypeScript projects
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs && \
    node --version && \
    npm --version

# Install Helm for Kubernetes chart validation
# Helm is required for deploy agent to validate Helm charts
# Version and SHA256 are pinned via build ARGs for reproducibility
RUN curl -sfL --retry 3 --retry-delay 5 --retry-all-errors -o /tmp/helm.tar.gz \
      "https://get.helm.sh/helm-v${HELM_VERSION}-linux-amd64.tar.gz" && \
    echo "${HELM_SHA256}  /tmp/helm.tar.gz" | sha256sum -c - && \
    tar xzf /tmp/helm.tar.gz -C /tmp linux-amd64/helm && \
    mv /tmp/linux-amd64/helm /usr/local/bin/helm && \
    rm -rf /tmp/helm.tar.gz /tmp/linux-amd64 && \
    helm version

# Install kubectl for Kubernetes manifest validation
# kubectl is required for deploy agent to validate K8s manifests
# Version and SHA256 are pinned via build ARGs for reproducible builds
RUN curl -sfL --retry 3 --retry-delay 5 --retry-all-errors \
      -o /usr/local/bin/kubectl \
      "https://dl.k8s.io/release/v${KUBECTL_VERSION}/bin/linux/amd64/kubectl" && \
    echo "${KUBECTL_SHA256}  /usr/local/bin/kubectl" | sha256sum -c - && \
    chmod +x /usr/local/bin/kubectl && \
    kubectl version --client

# Install Docker CLI so Dockerfile validation tools can run `docker build` checks.
# Only the CLI is installed (not the Docker daemon) for security reasons.
# When `Dockerfile` validation requires running a full build, mount the host socket:
#   docker run -v /var/run/docker.sock:/var/run/docker.sock ...
# Add the official Docker APT repository for the latest stable CLI release.
RUN apt-get update && apt-get install -y --no-install-recommends \
        gnupg lsb-release \
 && curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg \
 && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/debian $(lsb_release -cs) stable" \
        > /etc/apt/sources.list.d/docker.list \
 && apt-get update && apt-get install -y --no-install-recommends docker-ce-cli \
 && docker --version \
 && rm -rf /var/lib/apt/lists/*

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
# Note: /app/logs/checkpoint is needed for SFE checkpoint audit logs and DLQ
# Note: /app/uploads is needed for job file uploads
# Note: /app/data is needed for clarifier context database and history files
# Note: /var/lib/clarifier is needed for clarifier persistent storage
# Create NLTK data directory (/opt/nltk_data) and HuggingFace cache directory (/opt/huggingface_cache)
# for pre-downloaded ML resources accessible by appuser
# Create ChromaDB cache directory (/opt/chroma_cache) for pre-downloaded ONNX models
# Also create the default ChromaDB cache path at /home/appuser/.cache/chroma/ since
# ChromaDB may not respect CHROMA_CACHE_DIR and defaults to ~/.cache/chroma/onnx_models/
# Create matplotlib cache directory at /tmp/matplotlib (set via MPLCONFIGDIR)
RUN mkdir -p /opt/venv /app /app/data /var/log/analyzer_audit /app/logs /app/logs/analyzer_audit /app/logs/checkpoint /app/uploads /var/lib/clarifier /opt/nltk_data /opt/huggingface_cache /opt/chroma_cache /home/appuser/.cache/chroma /home/appuser/.cache/pylint /tmp/matplotlib && \
    chown -R appuser:appgroup /opt/venv /app /app/data /var/log/analyzer_audit /app/logs /app/uploads /var/lib/clarifier /opt/nltk_data /opt/huggingface_cache /opt/chroma_cache /home/appuser/.cache/chroma /home/appuser/.cache/pylint /tmp/matplotlib

# Bring in the venv and application source with proper ownership during copy
COPY --from=builder --chown=appuser:appgroup /opt/venv /opt/venv
COPY --from=builder --chown=appuser:appgroup /app /app
# Copy NLTK data and HuggingFace models from builder stage to avoid runtime downloads
COPY --from=builder --chown=appuser:appgroup /opt/nltk_data /opt/nltk_data
COPY --from=builder --chown=appuser:appgroup /opt/huggingface_cache /opt/huggingface_cache
# Copy ChromaDB ONNX models from builder stage to avoid runtime downloads
COPY --from=builder --chown=appuser:appgroup /opt/chroma_cache /opt/chroma_cache
# Symlink ChromaDB cache to appuser's default cache path so ChromaDB finds pre-downloaded models
# ChromaDB defaults to ~/.cache/chroma/onnx_models/ which may not respect CHROMA_CACHE_DIR
RUN ln -sf /opt/chroma_cache /home/appuser/.cache/chroma/onnx_models

USER appuser

# The FastAPI server runs on port 8000 (default) or PORT env var, Prometheus metrics on port 9090
# Note: Railway sets PORT=8080; pass --build-arg PORT_HINT=8080 for Railway-specific images.
EXPOSE 8000 9090

# Docker healthcheck for container liveness
# CRITICAL: Uses /health endpoint for liveness checks, NOT /ready
# 
# Why /health instead of /ready?
# - /health: Returns 200 immediately when HTTP server is up (liveness check)
# - /ready: Returns 503 until ALL agents are loaded (~45-55 seconds) (readiness check)
# 
# Using /ready caused premature SIGTERM during agent loading on Railway:
# - Container starts, begins loading agents
# - Docker HEALTHCHECK queries /ready endpoint
# - /ready returns 503 (agents still loading)
# - Railway kills container before agents finish loading
# 
# Solution: Use /health for Docker HEALTHCHECK (liveness)
# - Load balancers/orchestrators should use /ready for traffic routing (readiness)
# - This aligns with Railway's healthcheckPath="/health" in railway.json
# 
# Uses PORT env var if set (Railway sets it to 8080), otherwise defaults to 8080
# Starts checking after 120 seconds to allow startup time (agents load in background)
# Times out after 10 seconds, retries 5 times before marking unhealthy
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=5 \
    CMD curl -f http://localhost:${PORT:-8000}/health || exit 1

# Explicitly set SIGTERM as the stop signal
# This ensures proper signal propagation for graceful shutdown
STOPSIGNAL SIGTERM

# Start the unified platform API server
# Single worker mode (1 worker) for production deployment
# FastAPI is fully async and doesn't benefit from multiple workers
# Multiple workers cause issues: each has its own in-memory jobs_db, leading to
# job synchronization problems (jobs not found, deleted jobs reappearing)
# Using server/run.py which respects PORT environment variable (defaults to 8000, Railway sets to 8080)
CMD ["python", "server/run.py", "--host", "0.0.0.0", "--workers", "1"]
