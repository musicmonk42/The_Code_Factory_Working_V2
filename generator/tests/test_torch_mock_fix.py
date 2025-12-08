"""
Test to verify that torch mock provides __version__ as a string.
This prevents TypeError when packaging.version.Version tries to parse it.
"""
import sys
import pytest
from packaging.version import Version


def test_torch_mock_version_is_string():
    """Test that mocked torch.__version__ is a string, not MockCallable."""
    # Check if torch is in sys.modules
    if 'torch' not in sys.modules:
        pytest.skip("torch is not mocked (may be installed)")
    
    torch = sys.modules['torch']
    
    # Verify __version__ exists
    assert hasattr(torch, '__version__'), "torch mock should have __version__ attribute"
    
    # Verify __version__ is a string
    assert isinstance(torch.__version__, str), (
        f"torch.__version__ should be a string, got {type(torch.__version__)}"
    )
    
    # Verify packaging.version.Version can parse it
    try:
        version = Version(torch.__version__)
        assert version is not None, "Version should be parseable"
    except TypeError as e:
        pytest.fail(f"packaging.version.Version failed to parse torch.__version__: {e}")


def test_transformers_mock_version_is_string():
    """Test that mocked transformers.__version__ is a string."""
    if 'transformers' not in sys.modules:
        pytest.skip("transformers is not mocked")
    
    transformers = sys.modules['transformers']
    
    if hasattr(transformers, '__version__'):
        assert isinstance(transformers.__version__, str), (
            f"transformers.__version__ should be a string, got {type(transformers.__version__)}"
        )
        
        # Verify packaging.version.Version can parse it
        try:
            version = Version(transformers.__version__)
            assert version is not None
        except TypeError as e:
            pytest.fail(f"packaging.version.Version failed: {e}")


def test_sentence_transformers_mock_version_is_string():
    """Test that mocked sentence_transformers.__version__ is a string."""
    if 'sentence_transformers' not in sys.modules:
        pytest.skip("sentence_transformers is not mocked")
    
    sentence_transformers = sys.modules['sentence_transformers']
    
    if hasattr(sentence_transformers, '__version__'):
        assert isinstance(sentence_transformers.__version__, str), (
            f"sentence_transformers.__version__ should be a string, got {type(sentence_transformers.__version__)}"
        )
        
        # Verify packaging.version.Version can parse it
        try:
            version = Version(sentence_transformers.__version__)
            assert version is not None
        except TypeError as e:
            pytest.fail(f"packaging.version.Version failed: {e}")


def test_safetensors_torch_import_does_not_fail():
    """
    Test that safetensors.torch can be imported without TypeError.
    This is the specific error that was fixed.
    """
    try:
        # This import chain was causing the original error:
        # safetensors.torch -> checks torch.__version__
        # If torch.__version__ is MockCallable, Version() fails with TypeError
        import safetensors.torch
        # If we reach here, the import succeeded
        assert True
    except TypeError as e:
        if "expected string or bytes-like object, got 'MockCallable'" in str(e):
            pytest.fail(
                f"The original error still exists! safetensors.torch import failed: {e}"
            )
        else:
            # Different TypeError, re-raise
            raise
    except ImportError:
        # safetensors not installed, that's ok
        pytest.skip("safetensors not installed")
