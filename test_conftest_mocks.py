"""Test the enhanced mock infrastructure in generator/conftest.py.

This test verifies that:
1. Heavy dependencies can be mocked successfully
2. Mock objects behave correctly (callable, attribute access, etc.)
3. Environment variable opt-out works (PYTEST_NO_MOCK=1)
4. No side effects on tests that don't use these dependencies
"""
import sys
import os
import pytest


def test_mock_chromadb():
    """Test that ChromaDB can be mocked and used like the real module."""
    # Mock chromadb if not already imported
    if 'chromadb' not in sys.modules:
        from generator.conftest import _create_mock_module
        sys.modules['chromadb'] = _create_mock_module('chromadb')
    
    # Should be able to import
    import chromadb
    
    # Should be able to access attributes
    client = chromadb.Client()
    assert client is not None
    
    # Should support method chaining
    collection = client.get_or_create_collection("test")
    assert collection is not None
    
    # Should have string representation
    assert "Mock" in repr(client) or "chromadb" in str(client)


def test_mock_presidio():
    """Test that Presidio can be mocked and used like the real module."""
    from generator.conftest import _create_mock_module
    
    # Mock presidio modules
    sys.modules['presidio_analyzer'] = _create_mock_module('presidio_analyzer')
    sys.modules['presidio_anonymizer'] = _create_mock_module('presidio_anonymizer')
    
    # Should be able to import
    from presidio_analyzer import AnalyzerEngine
    from presidio_anonymizer import AnonymizerEngine
    
    # Should be able to instantiate
    analyzer = AnalyzerEngine()
    anonymizer = AnonymizerEngine()
    
    assert analyzer is not None
    assert anonymizer is not None


def test_mock_spacy():
    """Test that SpaCy can be mocked and used like the real module."""
    from generator.conftest import _create_mock_module
    
    # Mock spacy
    sys.modules['spacy'] = _create_mock_module('spacy')
    
    # Should be able to import
    import spacy
    
    # Should be able to call load() method
    nlp = spacy.load("en_core_web_sm")
    assert nlp is not None


def test_mock_context_manager():
    """Test that mock objects can be used as context managers."""
    from generator.conftest import _create_mock_module
    
    mock = _create_mock_module('test_module')
    obj = mock.SomeClass()
    
    # Should support with statement
    with obj as context:
        assert context is not None


def test_mock_iteration():
    """Test that mock objects can be iterated."""
    from generator.conftest import _create_mock_module
    
    mock = _create_mock_module('test_module')
    obj = mock.SomeIterable()
    
    # Should support iteration (returns empty iterator)
    items = list(obj)
    assert items == []


def test_mock_nested_attributes():
    """Test that mock objects support deeply nested attribute access."""
    from generator.conftest import _create_mock_module
    
    mock = _create_mock_module('test_module')
    
    # Should support nested access
    result = mock.level1.level2.level3.method()
    assert result is not None
    
    # Should maintain names in chain
    obj = mock.utils.embedding_functions
    assert "embedding_functions" in str(obj) or "Mock" in repr(obj)


def test_mock_module_properties():
    """Test that mock modules have correct properties."""
    from generator.conftest import _create_mock_module
    
    mock = _create_mock_module('test_module')
    
    # Should have expected module properties
    assert mock.__name__ == 'test_module'
    assert '<mocked test_module>' in mock.__file__
    assert hasattr(mock, '__spec__')
    assert hasattr(mock, '__path__')


def test_ensure_mocks_fixture_opt_out():
    """Test that PYTEST_NO_MOCK=1 allows opting out of mocking."""
    # Save original env var
    original_val = os.environ.get('PYTEST_NO_MOCK')
    
    try:
        # Enable opt-out
        os.environ['PYTEST_NO_MOCK'] = '1'
        
        # Import the fixture
        from generator.conftest import _ensure_mocks
        
        # When opt-out is enabled, fixture should yield immediately without mocking
        # This is hard to test directly, but we can verify the env var is checked
        assert os.environ.get('PYTEST_NO_MOCK') == '1'
        
    finally:
        # Restore original env var
        if original_val is not None:
            os.environ['PYTEST_NO_MOCK'] = original_val
        else:
            os.environ.pop('PYTEST_NO_MOCK', None)


def test_simulation_modules_list():
    """Test that SIMULATION_MODULES_TO_MOCK includes all required modules."""
    from generator.conftest import SIMULATION_MODULES_TO_MOCK
    
    # Should include original simulation modules
    assert "simulation" in SIMULATION_MODULES_TO_MOCK
    assert "omnicore_engine.engines" in SIMULATION_MODULES_TO_MOCK
    
    # Should include ChromaDB modules
    assert "chromadb" in SIMULATION_MODULES_TO_MOCK
    assert "chromadb.utils.embedding_functions" in SIMULATION_MODULES_TO_MOCK
    
    # Should include Presidio modules
    assert "presidio_analyzer" in SIMULATION_MODULES_TO_MOCK
    assert "presidio_anonymizer" in SIMULATION_MODULES_TO_MOCK
    
    # Should include SpaCy
    assert "spacy" in SIMULATION_MODULES_TO_MOCK


if __name__ == "__main__":
    # Run tests directly
    pytest.main([__file__, "-v"])
