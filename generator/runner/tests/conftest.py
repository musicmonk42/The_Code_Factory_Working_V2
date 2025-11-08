# generator/runner/tests/conftest.py
import os
import tempfile
import pathlib

# FIX: Set env vars at the very top before other imports
os.environ.setdefault("TESTING", "1")
tmp_plugins = pathlib.Path(tempfile.gettempdir()) / "plugins"
tmp_plugins.mkdir(exist_ok=True)
os.environ.setdefault("LLM_PLUGIN__PLUGIN_DIR", str(tmp_plugins))
os.environ.setdefault("LLM_PLUGIN_PLUGIN_DIR", str(tmp_plugins))
# optional noise reducers
os.environ.setdefault("DEV_MODE", "1")
os.environ.setdefault("OTEL_SDK_DISABLED", "1")
os.environ.setdefault("AUDIT_CRYPTO_DISABLE_IMPORT_VALIDATE", "1")
# End of patch

from pathlib import Path

# === 1. Set TESTING flags ===
# Make test mode detectable during import
os.environ["TESTING"] = "1"
os.environ["DEV_MODE"] = "1"
# Silence OTEL provider warnings in tests
os.environ["OTEL_SDK_DISABLED"] = "1"
# Ensure audit-crypto factory doesn't validate at import (if still reached somewhere)
os.environ["AUDIT_CRYPTO_DISABLE_IMPORT_VALIDATE"] = "1"

# FIX: Provide a default PLUGIN_DIR using the correct Dynaconf env var prefix
# and create the directory to prevent validation errors.
# Note: This block is now redundant due to the patch above, but harmless to leave.
tmp_plugins = Path(tempfile.gettempdir()) / "plugins"
tmp_plugins.mkdir(exist_ok=True)
# Set both formats as Dynaconf might pick up either
os.environ.setdefault("LLM_PLUGIN__PLUGIN_DIR", str(tmp_plugins))
os.environ.setdefault("LLM_PLUGIN_PLUGIN_DIR", str(tmp_plugins))


# === 2. Set Dynaconf DEVELOPMENT environment variables ===
os.environ["AUDIT_CRYPTO_DEVELOPMENT_PROVIDER_TYPE"] = "software"
os.environ["AUDIT_CRYPTO_DEVELOPMENT_DEFAULT_ALGO"] = "hmac"
os.environ["AUDIT_CRYPTO_DEVELOPMENT_KEY_ROTATION_INTERVAL_SECONDS"] = "86400"
os.environ["AUDIT_CRYPTO_DEVELOPMENT_SOFTWARE_KEY_DIR"] = "/tmp/pytest-keys"
os.environ["AUDIT_CRYPTO_DEVELOPMENT_KMS_KEY_ID"] = "dummy-kms-key"
os.environ["AUDIT_CRYPTO_DEVELOPMENT_AWS_REGION"] = "us-east-1"

# === 3. Add project root to path ===
project_root = Path(__file__).parent.parent.parent
import sys
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# === 4. Pytest config ===
import pytest
def pytest_configure(config):
    config.option.asyncio_mode = "auto"