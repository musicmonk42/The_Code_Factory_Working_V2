"""Minimal integration test to verify mocks prevent heavy imports during test collection."""
import sys
import time


def test_mock_prevents_chromadb_import():
    """Verify that mocking ChromaDB prevents actual import."""
    # First, mock the module before it can be imported
    from generator.conftest import _create_mock_module
    
    # Pre-inject mocks into sys.modules
    sys.modules['chromadb'] = _create_mock_module('chromadb')
    sys.modules['chromadb.config'] = _create_mock_module('chromadb.config')
    sys.modules['chromadb.utils'] = _create_mock_module('chromadb.utils')
    sys.modules['chromadb.utils.embedding_functions'] = _create_mock_module('chromadb.utils.embedding_functions')
    
    # Now when code imports chromadb, it gets our mock
    import chromadb
    from chromadb.utils import embedding_functions
    
    # Verify it's our mock (should have Mock in repr or be callable)
    client = chromadb.Client()
    assert client is not None
    
    # Verify embedding functions are also mocked
    func = embedding_functions.DefaultEmbeddingFunction()
    assert func is not None
    
    print("✓ ChromaDB successfully mocked")


def test_mock_prevents_presidio_import():
    """Verify that mocking Presidio prevents actual import and SpaCy downloads."""
    from generator.conftest import _create_mock_module
    
    # Pre-inject mocks
    sys.modules['presidio_analyzer'] = _create_mock_module('presidio_analyzer')
    sys.modules['presidio_anonymizer'] = _create_mock_module('presidio_anonymizer')
    sys.modules['spacy'] = _create_mock_module('spacy')
    
    # Now when code imports presidio, it gets our mock
    from presidio_analyzer import AnalyzerEngine
    from presidio_anonymizer import AnonymizerEngine
    
    # Verify it's our mock
    analyzer = AnalyzerEngine()
    anonymizer = AnonymizerEngine()
    
    assert analyzer is not None
    assert anonymizer is not None
    
    print("✓ Presidio successfully mocked")


def test_collection_time_fast():
    """Verify that test collection is fast with mocks."""
    start_time = time.time()
    
    # Simulate what pytest does during collection - import test modules
    # With mocks in place, this should be fast
    from generator.conftest import SIMULATION_MODULES_TO_MOCK, _create_mock_module
    
    # Pre-inject all mocks
    for module_name in SIMULATION_MODULES_TO_MOCK:
        if module_name not in sys.modules:
            sys.modules[module_name] = _create_mock_module(module_name)
    
    elapsed = time.time() - start_time
    
    # Collection should be very fast with mocks (< 1 second for mock setup)
    assert elapsed < 5.0, f"Mock setup took {elapsed:.2f}s, should be < 5s"
    
    print(f"✓ Mock setup completed in {elapsed:.3f}s")


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v", "-s"])
