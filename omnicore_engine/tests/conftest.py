# omnicore_engine/tests/conftest.py
"""
Pytest configuration for omnicore_engine tests.
Handles test environment setup and common mocking.
"""

import os
import sys
from pathlib import Path

# Set testing environment variables early
os.environ["TESTING"] = "1"
os.environ.setdefault("OTEL_SDK_DISABLED", "1")

# Add project root to Python path
project_root = Path(__file__).parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# Import pytest for fixtures
import pytest


@pytest.fixture(autouse=True)
def reset_test_environment():
    """Ensure test environment is properly configured for each test."""
    os.environ["TESTING"] = "1"
    yield


@pytest.fixture(scope="function")
def temp_db_path(tmp_path):
    """Provide a temporary database path for tests."""
    return tmp_path / "test.db"


def pytest_configure(config):
    """Configure pytest with custom markers and settings."""
    config.addinivalue_line("markers", "integration: mark test as integration test")
    config.addinivalue_line("markers", "slow: mark test as slow running")
