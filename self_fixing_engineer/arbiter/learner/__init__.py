# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# arbiter/learner/__init__.py

import importlib.metadata
import logging
import os
import warnings

import structlog  # Install: pip install structlog

__version__ = "0.0.1"  # Requires pyproject.toml with [tool.poetry] or setup.py

# Structured logging setup
structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    logger_factory=structlog.PrintLoggerFactory(),
)
logger = structlog.get_logger(__name__)

# Validate critical env vars at import
required_envs = [
    "NEO4J_URL",
    "NEO4J_USER",
    "NEO4J_PASSWORD",
    "LLM_API_KEY",
]  # Add more as needed
missing = [var for var in required_envs if not os.getenv(var)]
if missing:
    warnings.warn(
        f"Missing env vars: {', '.join(missing)}. Using defaults for testing."
    )
    for var in missing:
        os.environ[var] = "test_default"


def setup_module():
    """Module-wide setup (call once at app start)."""
    logger.info("self_fixing_engineer.arbiter.learner module initialized", version=__version__)


from .audit import CircuitBreaker
from .core import Learner, should_auto_learn
from .encryption import ArbiterConfig

__all__ = [
    "Learner",
    "should_auto_learn",
    "CircuitBreaker",
    "ArbiterConfig",
    # Add metrics here if needed, but they're already imported in submodules
]
