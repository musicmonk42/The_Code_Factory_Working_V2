# intent_capture/tests/conftest.py
"""
Test configuration for intent_capture tests.
Handles both relative and absolute imports.
"""

import logging
import os
import sys
import warnings
from pathlib import Path

# Setup paths
test_dir = Path(__file__).parent
intent_capture_dir = test_dir.parent
project_root = intent_capture_dir.parent

# Add BOTH paths to support different import styles
sys.path.insert(0, str(project_root))  # For "import intent_capture.module"
sys.path.insert(0, str(intent_capture_dir))  # For "from module import ..."

# Set environment variables BEFORE any imports
os.environ["TEST_MODE"] = "true"
os.environ["REDIS_URL"] = "redis://localhost:6379/0"
os.environ["JWT_SECRET"] = "test_secret_key_that_is_at_least_32_characters_long"
os.environ["OPENAI_API_KEY"] = "sk-test-key"
os.environ["ANTHROPIC_API_KEYS"] = "test-anthropic-key"
os.environ["GOOGLE_API_KEYS"] = "test-google-key"
os.environ["LOG_LEVEL"] = "ERROR"
os.environ["LLM_MODEL"] = "gpt-4o-mini"
os.environ["CLI_JWT_SECRET"] = "test_cli_secret"

# Disable Streamlit in tests
os.environ["STREAMLIT_SERVER_HEADLESS"] = "true"

# Configure logging to prevent errors
logging.basicConfig(level=logging.ERROR, force=True)
logging.getLogger("streamlit").setLevel(logging.ERROR)
logging.getLogger("intent_capture").setLevel(logging.ERROR)

# Suppress warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", message=".*pkg_resources.*")
warnings.filterwarnings("ignore", message=".*missing ScriptRunContext.*")
warnings.filterwarnings("ignore", category=RuntimeWarning)

import unittest.mock as mock

import pytest

# Defer Streamlit mocking to fixture to avoid module-level context manager execution
# which can cause issues during test collection
# The mocking is now handled by the mock_streamlit_setup fixture below


@pytest.fixture(scope="session", autouse=True)
def mock_streamlit_setup():
    """Mock Streamlit session state globally to prevent errors during test collection."""
    mock_session_state = mock.MagicMock()
    mock_session_state.get.return_value = "test_user"
    
    with mock.patch.dict(sys.modules, {"streamlit": mock.MagicMock()}):
        sys.modules["streamlit"].session_state = mock_session_state
        yield


@pytest.fixture(autouse=True)
def setup_test_environment():
    """Setup test environment for each test."""
    # Ensure paths are set
    if str(intent_capture_dir) not in sys.path:
        sys.path.insert(0, str(intent_capture_dir))
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    yield

    # Cleanup
    import gc

    gc.collect()


@pytest.fixture(autouse=True)
def mock_streamlit_for_tests(mock_streamlit_setup):
    """Mock Streamlit components that cause issues in tests.
    
    Note: This fixture depends on mock_streamlit_setup (passed as parameter) to ensure
    the session-level Streamlit mock is initialized first. The parameter ensures proper
    fixture ordering. We create a fresh mock_session_state here for test isolation.
    """
    mock_session_state = mock.MagicMock()
    mock_session_state.get.return_value = "test_user"
    
    with mock.patch("streamlit.session_state", mock_session_state):
        with mock.patch(
            "streamlit.runtime.scriptrunner_utils.script_run_context.get_script_run_ctx",
            return_value=None,
        ):
            yield


@pytest.fixture(autouse=True)
def cleanup_logging():
    """Ensure logging doesn't cause issues."""
    yield
    # Close all logging handlers to prevent "I/O operation on closed file" errors
    for handler in logging.root.handlers[:]:
        try:
            handler.close()
        except:
            pass
        logging.root.removeHandler(handler)


# Prevent module-level imports from failing
import atexit


def cleanup_at_exit():
    """Cleanup function to prevent errors at exit."""
    try:
        # Close any remaining file handles
        logging.shutdown()
    except:
        pass


atexit.register(cleanup_at_exit)
