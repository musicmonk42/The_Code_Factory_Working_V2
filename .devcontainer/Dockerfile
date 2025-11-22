FROM mcr.microsoft.com/devcontainers/python:3.11

# Install system dependencies
RUN apt-get update && export DEBIAN_FRONTEND=noninteractive \
    && apt-get -y install --no-install-recommends \
        build-essential \
        curl \
        git \
        vim \
        postgresql-client \
        redis-tools \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install Python development tools
RUN pip install --no-cache-dir \
    black \
    ruff \
    mypy \
    pytest \
    pytest-cov \
    pytest-asyncio \
    bandit \
    safety \
    pip-audit \
    ipython \
    ipdb

# Set up workspace
WORKDIR /workspace

# Configure git
RUN git config --global --add safe.directory /workspace
