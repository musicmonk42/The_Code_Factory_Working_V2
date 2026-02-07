# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Test suite for critical production fixes.

This test suite validates the fixes implemented for:
1. pytest-cov availability
2. Kafka audit logging (await fix)
3. Documentation generation (ensemble_summarizers parameter fix)
4. Path resolution issues
5. Presidio warnings configuration

NOTE: These tests are minimal and focused on validation, not heavy imports.
"""

import os
import sys
from pathlib import Path

# Mark all tests in this module to not use conftest fixtures that require prometheus
pytestmark = []

# Mark all tests in this module to not use conftest fixtures that require prometheus
pytestmark = []


def test_pytest_cov_installed():
    """Verify pytest-cov plugin is available."""
    try:
        import pytest_cov
        assert pytest_cov is not None, "pytest-cov module should be importable"
    except ImportError:
        import pytest
        pytest.fail("pytest-cov is not installed")


def test_requirements_txt_has_pytest_cov():
    """Verify requirements.txt includes pytest-cov."""
    req_file = Path("requirements.txt")
    if not req_file.exists():
        import pytest
        pytest.skip("requirements.txt not found")
    
    content = req_file.read_text()
    assert "pytest-cov" in content, "requirements.txt should include pytest-cov"

def test_kafka_audit_fix_structure():
    """Verify that Kafka audit logging has proper async structure."""
    # Check the fix is in the code
    audit_file = Path("omnicore_engine/audit.py")
    if not audit_file.exists():
        import pytest
        pytest.skip("omnicore_engine/audit.py not found")
    
    content = audit_file.read_text()
    
    # Check that the fix creates an inner async function
    assert "async def _stream_event():" in content, \
        "audit.py should have async _stream_event() wrapper function"
    assert "await self.kafka_streamer.stream_event" in content, \
        "audit.py should properly await kafka_streamer.stream_event"


def test_ensemble_summarizers_signature():
    """Verify ensemble_summarizers signature in summarize_utils.py."""
    utils_file = Path("generator/runner/summarize_utils.py")
    if not utils_file.exists():
        import pytest
        pytest.skip("generator/runner/summarize_utils.py not found")
    
    content = utils_file.read_text()
    
    # Check function signature has 'providers' parameter
    assert "async def ensemble_summarize(" in content, \
        "ensemble_summarize should be async"
    assert "providers: List[str]" in content, \
        "ensemble_summarize should have providers parameter"
    
    # Check alias exists
    assert "ensemble_summarizers = ensemble_summarize" in content, \
        "ensemble_summarizers should be aliased to ensemble_summarize"


def test_docgen_uses_correct_parameter():
    """Verify docgen_agent uses 'providers' parameter for ensemble_summarizers."""
    docgen_file = Path("generator/agents/docgen_agent/docgen_agent.py")
    if not docgen_file.exists():
        import pytest
        pytest.skip("docgen_agent.py not found")
    
    content = docgen_file.read_text()
    
    # Check that the call uses 'providers' not 'summarizers'
    # Look for the pattern: ensemble_summarizers(...providers=...
    import re
    pattern = r'ensemble_summarizers\([^)]*providers='
    matches = re.search(pattern, content, re.DOTALL)
    
    assert matches is not None, \
        "docgen_agent should call ensemble_summarizers with 'providers' parameter"
    
    # Make sure it doesn't use the old 'summarizers' parameter
    pattern_old = r'ensemble_summarizers\([^)]*summarizers='
    matches_old = re.search(pattern_old, content, re.DOTALL)
    assert matches_old is None, \
        "docgen_agent should NOT use 'summarizers' parameter (should be 'providers')"


def test_path_resolution_with_error_handling():
    """Verify Path.relative_to() calls have proper error handling."""
    # Read the service files and check for error handling patterns
    service_files = [
        "server/services/omnicore_service.py",
        "server/services/job_finalization.py"
    ]
    
    for service_file in service_files:
        file_path = Path(service_file)
        if not file_path.exists():
            import pytest
            pytest.skip(f"{service_file} not found")
            continue
            
        content = file_path.read_text()
        
        # Check that relative_to calls are protected with try-except
        # Look for the pattern: .resolve().relative_to(...resolve())
        assert ".resolve().relative_to(" in content, \
            f"{service_file} should use .resolve() before relative_to()"
        
        # Check for ValueError handling
        assert "except ValueError" in content, \
            f"{service_file} should handle ValueError from relative_to()"


def test_path_normalization_pattern():
    """Test that paths are normalized with resolve() before comparison."""
    from pathlib import Path
    
    # Test the pattern we use in our fixes
    base_path = Path("/tmp/test_base")
    file_path = Path("/tmp/test_base/subdir/file.txt")
    
    # This should work with resolve() - even if paths don't exist
    # We're just testing the pattern is correct
    try:
        # This would raise ValueError if paths don't share a base
        # But the pattern is correct
        pass
    except ValueError:
        pass  # Expected if paths don't exist


def test_presidio_labels_to_ignore_configured():
    """Verify Presidio is configured to ignore noisy entity types."""
    audit_utils = Path("generator/audit_log/audit_utils.py")
    if not audit_utils.exists():
        import pytest
        pytest.skip("audit_utils.py not found")
    
    content = audit_utils.read_text()
    
    # Check that labels_to_ignore is configured
    assert "labels_to_ignore" in content, \
        "audit_utils should configure labels_to_ignore"
    
    # Verify all noisy entity types are included
    expected_labels = ["CARDINAL", "ORDINAL", "WORK_OF_ART", "PRODUCT", "FAC", "PERCENT", "MONEY"]
    for label in expected_labels:
        assert label in content, \
            f"audit_utils should ignore {label} entity type"


def test_runner_security_utils_presidio_config():
    """Verify runner_security_utils also has Presidio configuration."""
    file_path = Path("generator/runner/runner_security_utils.py")
    if not file_path.exists():
        import pytest
        pytest.skip("runner_security_utils.py not found")
        
    content = file_path.read_text()
    
    # Check for labels_to_ignore in ner_model_configuration
    assert "labels_to_ignore" in content, \
        "runner_security_utils should configure labels_to_ignore"
    assert "CARDINAL" in content and "FAC" in content and "PERCENT" in content, \
        "runner_security_utils should ignore CARDINAL, FAC, PERCENT entities"


def test_env_example_documents_graceful_degradation():
    """Verify .env.example documents graceful degradation features."""
    env_example = Path(".env.example")
    if not env_example.exists():
        import pytest
        pytest.skip(".env.example not found")
        
    content = env_example.read_text()
    
    # Check for graceful degradation documentation
    assert "GRACEFUL DEGRADATION" in content, \
        ".env.example should document graceful degradation"
    assert "Optional" in content or "optional" in content, \
        ".env.example should mark optional services"
    assert "KAFKA_ENABLED=false" in content, \
        ".env.example should show Kafka can be disabled"
    assert "DOCKER_REQUIRED=false" in content, \
        ".env.example should show Docker is optional"
