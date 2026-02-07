# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""Set up test environment variables for the self_fixing_engineer test suite"""

import os
import sys
from pathlib import Path

# Add project root to Python path
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

# Neo4j Database Configuration
os.environ.setdefault("NEO4J_URL", "bolt://localhost:7687")
os.environ.setdefault("NEO4J_USER", "neo4j")
os.environ.setdefault("NEO4J_PASSWORD", "test_password")

# API Keys
os.environ.setdefault("LLM_API_KEY", "test_key")
os.environ.setdefault("OPENAI_API_KEY", "test_openai_key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test_anthropic_key")

# Encryption Keys (32 bytes for Fernet encryption)
os.environ.setdefault("CHECKPOINT_ENCRYPTION_KEY", "test_encryption_key_32_bytes_long!")
os.environ.setdefault("EVENT_BUS_ENCRYPTION_KEY", "test_encryption_key_32_bytes_long!")
os.environ.setdefault("MESH_ENCRYPTION_KEY", "test_encryption_key_32_bytes_long!")
os.environ.setdefault("POLICY_ENCRYPTION_KEY", "test_encryption_key_32_bytes_long!")

# HMAC and Security Keys
os.environ.setdefault("CHECKPOINT_HMAC_SECRET_KEY", "test_hmac_key")
os.environ.setdefault("AGENTIC_AUDIT_HMAC_KEY_ENV", "test_audit_hmac_key")

# Test Configuration
os.environ.setdefault("ENV", "test")
os.environ.setdefault("TENANT", "test_tenant")
os.environ.setdefault("CHECKPOINT_MAX_RETRIES", "1")
os.environ.setdefault("CHECKPOINT_RETRY_DELAY", "0.01")

# Directory Configuration
os.environ.setdefault("CHECKPOINT_DIR", "./test_checkpoints")
os.environ.setdefault("CHECKPOINT_LOAD_CACHE_MAXSIZE", "50")

# Backend URLs
os.environ.setdefault("MESH_BACKEND_URL", "rediss://localhost:6379/0")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

# Feature Flags
os.environ.setdefault("PRODUCTION_MODE", "false")
os.environ.setdefault("DEBUG", "true")

# Additional Service Configuration
os.environ.setdefault("CLUSTER_NAME", "test-cluster")
os.environ.setdefault("ENVIRONMENT", "test")


def setup_test_environment():
    """
    Function to be called at the beginning of test runs
    to ensure all environment variables are properly set
    """
    print("Test environment variables configured")
    print(f"Project root: {PROJECT_ROOT}")
    print(f"Python path includes: {sys.path[0]}")


if __name__ == "__main__":
    setup_test_environment()
