"""
Global pytest configuration for self_fixing_engineer tests.

This module:
- Sets test environment variables to disable heavy components
- Mocks HuggingFace transformers pipeline to prevent model loading
- Mocks Pinecone to prevent vector store initialization
- Mocks HuggingFaceEmbeddings to prevent model downloads
- Implements aggressive memory cleanup after each test
"""

import gc
import os
import sys
from unittest.mock import MagicMock

import pytest

# ---- Set environment variables BEFORE any imports ----
os.environ["TEST_MODE"] = "true"
os.environ["TESTING"] = "1"
os.environ["USE_VECTOR_MEMORY"] = "false"
os.environ["DISABLE_SENTRY"] = "1"
os.environ["OTEL_SDK_DISABLED"] = "1"
os.environ["SKIP_AUDIT_INIT"] = "1"
os.environ["SKIP_BACKGROUND_TASKS"] = "1"
os.environ["NO_MONITORING"] = "1"
os.environ["DISABLE_TELEMETRY"] = "1"
os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
os.environ["SKIP_IMPORT_TIME_VALIDATION"] = "1"

# ---- Mock HuggingFace Transformers Pipeline ----
try:
    if "transformers" in sys.modules:
        def mock_pipeline(*args, **kwargs):
            mock_pipe = MagicMock()
            mock_pipe.return_value = [{"label": "SAFE", "score": 0.99}]
            return mock_pipe
        sys.modules["transformers"].pipeline = mock_pipeline
except (ImportError, KeyError):
    pass


# ---- Mock Pinecone ----
try:
    if "pinecone" not in sys.modules:
        mock_pinecone = MagicMock()
        mock_pinecone.Pinecone = MagicMock()
        sys.modules["pinecone"] = mock_pinecone
    else:
        sys.modules["pinecone"].Pinecone = MagicMock()
except Exception:
    pass

# ---- Mock langchain_pinecone ----
try:
    if "langchain_pinecone" not in sys.modules:
        sys.modules["langchain_pinecone"] = MagicMock()
except Exception:
    pass


# ---- Mock HuggingFaceEmbeddings ----
try:
    if "langchain_community.embeddings" in sys.modules:
        class MockHuggingFaceEmbeddings:
            def __init__(self, *args, **kwargs):
                self.model_name = kwargs.get("model_name", "mock-model")
            
            def embed_documents(self, texts):
                return [[0.0] * 384 for _ in texts]
            
            def embed_query(self, text):
                return [0.0] * 384
        
        sys.modules["langchain_community.embeddings"].HuggingFaceEmbeddings = MockHuggingFaceEmbeddings
except (ImportError, KeyError):
    pass


# ---- Pytest Configuration ----

def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "heavy: mark test as heavy/resource-intensive (skipped by default)"
    )


@pytest.fixture(scope="function", autouse=True)
def aggressive_memory_cleanup():
    """
    Ultra-aggressive memory cleanup after each test.
    
    Runs at function scope to ensure complete isolation between tests.
    This is critical for preventing OOM failures in memory-constrained CI.
    """
    # Before test: collect garbage
    gc.collect()
    
    yield
    
    # After test: aggressive cleanup
    
    # 1. Clear LLM caches
    try:
        from intent_capture.agent_core import LLMProviderFactory
        if hasattr(LLMProviderFactory, '_llm_instance_cache'):
            LLMProviderFactory._llm_instance_cache.clear()
    except (ImportError, AttributeError):
        pass
    
    # 2. Clear Prometheus registry
    try:
        from prometheus_client import REGISTRY
        collectors = list(REGISTRY._collector_to_names.keys())
        for collector in collectors:
            try:
                REGISTRY.unregister(collector)
            except Exception:
                pass
    except (ImportError, AttributeError):
        pass
    
    # 3. Clear module-level caches from heavy dependencies
    heavy_module_prefixes = [
        'langchain', 'openai', 'anthropic', 'transformers',
        'boto3', 'botocore', 'aiokafka', 'kafka', 'redis',
        'torch', 'tensorflow', 'spacy', 'presidio'
    ]
    
    for module_name in list(sys.modules.keys()):
        if any(module_name.startswith(prefix) for prefix in heavy_module_prefixes):
            module = sys.modules.get(module_name)
            if module and hasattr(module, '__dict__'):
                # Clear cached attributes
                cache_prefixes = ['_cache', '_instance', '_pool', '_client', '_connection']
                for attr in list(vars(module).keys()):
                    if any(attr.startswith(prefix) for prefix in cache_prefixes):
                        try:
                            delattr(module, attr)
                        except:
                            pass
    
    # 4. Force multiple garbage collection passes
    # Multiple passes ensure circular references are cleaned up
    for _ in range(3):
        gc.collect()


@pytest.fixture(scope="session")
def session_cleanup():
    """Final cleanup at session end."""
    yield
    
    # Final aggressive cleanup
    gc.collect()
    gc.collect()
    gc.collect()

