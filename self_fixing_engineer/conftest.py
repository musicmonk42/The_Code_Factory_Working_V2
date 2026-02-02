"""
Global pytest configuration for self_fixing_engineer tests.

This module:
- Sets test environment variables to disable heavy components
- Mocks HuggingFace transformers pipeline to prevent model loading
- Mocks Pinecone to prevent vector store initialization
- Mocks HuggingFaceEmbeddings to prevent model downloads
- Forces garbage collection after each test
"""

import gc
import os
import sys
from unittest.mock import MagicMock, Mock, patch

import pytest

# ---- Set environment variables BEFORE any imports ----
# This prevents heavy initialization during test collection and execution
os.environ["TEST_MODE"] = "true"
os.environ["TESTING"] = "1"
os.environ["USE_VECTOR_MEMORY"] = "false"
os.environ["DISABLE_SENTRY"] = "1"
os.environ["OTEL_SDK_DISABLED"] = "1"
os.environ["SKIP_AUDIT_INIT"] = "1"
os.environ["SKIP_BACKGROUND_TASKS"] = "1"
os.environ["NO_MONITORING"] = "1"
os.environ["DISABLE_TELEMETRY"] = "1"

# ---- Mock HuggingFace Transformers Pipeline ----
# Prevents loading of heavy models like toxic-bert (~400MB+)
try:
    from transformers import pipeline as _real_pipeline
    
    def mock_pipeline(*args, **kwargs):
        """Mock HuggingFace pipeline to prevent model loading."""
        mock_pipe = MagicMock()
        mock_pipe.return_value = [{"label": "SAFE", "score": 0.99}]
        return mock_pipe
    
    # Patch the pipeline function at module level
    sys.modules["transformers"].pipeline = mock_pipeline
except ImportError:
    # transformers not installed, no need to mock
    pass


# ---- Mock Pinecone ----
# Prevents vector store initialization and API calls
try:
    # Create a mock Pinecone module if it doesn't exist
    if "pinecone" not in sys.modules:
        mock_pinecone = MagicMock()
        mock_pinecone.Pinecone = MagicMock()
        sys.modules["pinecone"] = mock_pinecone
    else:
        # Patch existing module
        sys.modules["pinecone"].Pinecone = MagicMock()
except Exception:
    pass

# ---- Mock langchain_pinecone ----
try:
    if "langchain_pinecone" not in sys.modules:
        mock_langchain_pinecone = MagicMock()
        sys.modules["langchain_pinecone"] = mock_langchain_pinecone
except Exception:
    pass


# ---- Mock HuggingFaceEmbeddings ----
# Prevents downloading and loading embedding models like all-MiniLM-L6-v2 (~90MB)
try:
    from langchain_community.embeddings import HuggingFaceEmbeddings as _real_embeddings
    
    class MockHuggingFaceEmbeddings:
        """Mock HuggingFaceEmbeddings to prevent model downloads."""
        
        def __init__(self, *args, **kwargs):
            """Initialize without loading any models."""
            self.model_name = kwargs.get("model_name", "mock-model")
        
        def embed_documents(self, texts):
            """Return mock embeddings."""
            return [[0.0] * 384 for _ in texts]
        
        def embed_query(self, text):
            """Return mock embedding."""
            return [0.0] * 384
    
    # Patch at module level
    sys.modules["langchain_community.embeddings"].HuggingFaceEmbeddings = MockHuggingFaceEmbeddings
except ImportError:
    pass


# ---- Pytest Fixtures ----

@pytest.fixture(autouse=True)
def cleanup_memory():
    """
    Autouse fixture to clean up memory after each test.
    
    This fixture:
    - Runs after every test automatically
    - Clears LLM provider cache if it exists
    - Forces garbage collection
    - Helps prevent memory accumulation across tests
    """
    yield
    
    # Clear LLM instance cache if it exists
    try:
        from intent_capture.agent_core import LLMProviderFactory
        if hasattr(LLMProviderFactory, '_llm_instance_cache'):
            LLMProviderFactory._llm_instance_cache.clear()
    except (ImportError, AttributeError):
        pass
    
    # Clear Prometheus registry to prevent duplicate metric registration
    try:
        from prometheus_client import REGISTRY
        # Get all collectors except the default ones
        collectors = list(REGISTRY._collector_to_names.keys())
        for collector in collectors:
            try:
                REGISTRY.unregister(collector)
            except Exception:
                pass  # Ignore errors during cleanup
    except (ImportError, AttributeError):
        pass
    
    # Force garbage collection
    gc.collect()


@pytest.fixture(autouse=True)
def mock_heavy_components():
    """
    Autouse fixture to mock heavy components for all tests.
    
    This ensures that even if environment variables are not checked,
    the heavy components are still mocked.
    """
    with patch("transformers.pipeline") as mock_pipe:
        mock_pipe.return_value = MagicMock(return_value=[{"label": "SAFE", "score": 0.99}])
        yield mock_pipe
