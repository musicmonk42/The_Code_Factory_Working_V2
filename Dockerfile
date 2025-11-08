# syntax=docker/dockerfile:1.7

###############################################
# Builder stage: install Python dependencies
###############################################
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install build tools for any packages that need compiling
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Create virtual environment for dependencies
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

WORKDIR /app
# Copy the whole repository (simplifies layout differences)
COPY . /app

# Upgrade packaging tools and install dependencies if found
RUN pip install --upgrade pip setuptools wheel \
 && if [ -f requirements.txt ]; then \
        pip install --no-cache-dir -r requirements.txt; \
    elif [ -f Generator/requirements.txt ]; then \
        pip install --no-cache-dir -r Generator/requirements.txt; \
    elif [ -f pyproject.toml ]; then \
        pip install --no-cache-dir .; \
    else \
        echo "No requirements.txt or pyproject.toml found. Skipping dependency install."; \
    fi

###############################################
# Runtime stage: minimal image, non-root user
###############################################
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}"

# Optional: curl for debugging and healthchecks
RUN apt-get update && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -m -u 10001 appuser

WORKDIR /app

# Bring in the venv and application source
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /app /app

# Ensure permissions
RUN chown -R appuser:appuser /app /opt/venv
USER appuser

# The Generator README indicates a FastAPI server at :8000 via deploy_llm_call
EXPOSE 8000

# Start the API server if present; otherwise provide a helpful fallback
# Tries (in order): Generator.deploy_llm_call, deploy_llm_call, or prints help.
CMD ["/bin/sh", "-c", "\
  if [ -f Generator/deploy_llm_call.py ]; then \
    python -m Generator.deploy_llm_call --server; \
  elif [ -f deploy_llm_call.py ]; then \
    python -m deploy_llm_call --server; \
  else \
    echo 'No server entrypoint found (deploy_llm_call). Override CMD or adjust paths.' && \
    python -c 'import sys; print(\"Repo mounted at /app. Try: python -m Generator.deploy_llm_call --server\")'; \
  fi \
"]
