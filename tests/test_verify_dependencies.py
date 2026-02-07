# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Tests for the enterprise-grade dependency verification module.

These tests verify that the DependencyVerifier class:
1. Correctly identifies installed vs missing dependencies
2. Produces structured output compatible with monitoring systems
3. Follows the singleton pattern
4. Provides accurate installation commands for missing dependencies
"""

import sys
from unittest.mock import MagicMock, patch
from pathlib import Path

# Add server to path
sys.path.insert(0, str(Path(__file__).parent.parent))


class TestDependencyVerifier:
    """Test cases for DependencyVerifier class."""
    
    def test_singleton_pattern(self):
        """Verify DependencyVerifier implements singleton pattern."""
        from server.verify_dependencies import DependencyVerifier
        
        verifier1 = DependencyVerifier()
        verifier2 = DependencyVerifier()
        
        assert verifier1 is verifier2, "DependencyVerifier should be a singleton"
    
    def test_dependency_info_to_dict(self):
        """Verify DependencyInfo serializes correctly."""
        from server.verify_dependencies import DependencyInfo, DependencyStatus
        
        info = DependencyInfo(
            module_name="test_module",
            package_name="test-package",
            description="Test description",
            status=DependencyStatus.INSTALLED,
            version="1.0.0",
            required=True,
            load_time_ms=5.5,
        )
        
        result = info.to_dict()
        
        assert result["module_name"] == "test_module"
        assert result["package_name"] == "test-package"
        assert result["status"] == "installed"
        assert result["version"] == "1.0.0"
        assert result["required"] is True
        assert result["load_time_ms"] == 5.5
    
    def test_verification_result_to_json(self):
        """Verify VerificationResult produces valid JSON."""
        import json
        from server.verify_dependencies import VerificationResult
        
        result = VerificationResult(
            success=True,
            critical_passed=True,
            total_checked=5,
            passed_count=5,
            failed_count=0,
        )
        
        json_str = result.to_json()
        parsed = json.loads(json_str)
        
        assert parsed["success"] is True
        assert parsed["critical_passed"] is True
        assert parsed["total_checked"] == 5
    
    def test_check_single_dependency_installed(self):
        """Verify installed dependencies are correctly detected."""
        from server.verify_dependencies import DependencyVerifier, DependencyStatus
        
        verifier = DependencyVerifier()
        
        # Test with a module that should always be present (sys is built-in)
        info = verifier._check_single_dependency(
            module_name="sys",
            package_name="python-stdlib",
            description="Python standard library",
            required=True,
        )
        
        assert info.status == DependencyStatus.INSTALLED
        assert info.error is None
    
    def test_check_single_dependency_missing(self):
        """Verify missing dependencies are correctly detected."""
        from server.verify_dependencies import DependencyVerifier, DependencyStatus
        
        verifier = DependencyVerifier()
        
        # Test with a module that definitely doesn't exist
        info = verifier._check_single_dependency(
            module_name="nonexistent_module_12345",
            package_name="nonexistent-package",
            description="Test package",
            required=True,
        )
        
        assert info.status == DependencyStatus.MISSING
        assert info.error is not None
        assert "nonexistent_module_12345" in info.error
    
    def test_get_installation_command(self):
        """Verify installation command generation."""
        from server.verify_dependencies import (
            DependencyVerifier, 
            VerificationResult, 
            DependencyInfo,
            DependencyStatus,
        )
        
        verifier = DependencyVerifier()
        
        # Create a result with missing dependencies
        result = VerificationResult(
            success=False,
            dependencies=[
                DependencyInfo(
                    module_name="missing1",
                    package_name="missing-pkg-1",
                    description="Test 1",
                    status=DependencyStatus.MISSING,
                    required=True,
                ),
                DependencyInfo(
                    module_name="missing2",
                    package_name="missing-pkg-2",
                    description="Test 2",
                    status=DependencyStatus.MISSING,
                    required=True,
                ),
                DependencyInfo(
                    module_name="optional",
                    package_name="optional-pkg",
                    description="Optional",
                    status=DependencyStatus.MISSING,
                    required=False,  # Not required
                ),
            ]
        )
        
        cmd = verifier.get_installation_command(result)
        
        # Should include required packages but not optional
        assert "missing-pkg-1" in cmd
        assert "missing-pkg-2" in cmd
        assert "optional-pkg" not in cmd
    
    def test_verify_all_returns_verification_result(self):
        """Verify verify_all returns a VerificationResult."""
        from server.verify_dependencies import DependencyVerifier, VerificationResult
        
        verifier = DependencyVerifier()
        result = verifier.verify_all(use_cache=False)
        
        assert isinstance(result, VerificationResult)
        assert result.timestamp != ""
        assert result.total_checked > 0
    
    def test_format_report(self):
        """Verify format_report produces readable output."""
        from server.verify_dependencies import DependencyVerifier
        
        verifier = DependencyVerifier()
        result = verifier.verify_all(use_cache=False)
        report = verifier.format_report(result, verbose=True)
        
        assert "CODE FACTORY PLATFORM" in report
        assert "DEPENDENCY VERIFICATION REPORT" in report
        assert "CRITICAL DEPENDENCIES" in report


class TestGetDependencyVerifier:
    """Test the get_dependency_verifier helper function."""
    
    def test_returns_singleton(self):
        """Verify get_dependency_verifier returns the singleton."""
        from server.verify_dependencies import get_dependency_verifier, DependencyVerifier
        
        verifier = get_dependency_verifier()
        
        assert isinstance(verifier, DependencyVerifier)
        assert verifier is DependencyVerifier()


class TestVerifyDependenciesQuick:
    """Test the quick verification function."""
    
    def test_returns_bool(self):
        """Verify verify_dependencies_quick returns a boolean."""
        from server.verify_dependencies import verify_dependencies_quick
        
        result = verify_dependencies_quick()
        
        assert isinstance(result, bool)


if __name__ == "__main__":
    # Run basic tests
    import traceback
    
    test_classes = [
        TestDependencyVerifier,
        TestGetDependencyVerifier,
        TestVerifyDependenciesQuick,
    ]
    
    passed = 0
    failed = 0
    
    for test_class in test_classes:
        instance = test_class()
        for method_name in dir(instance):
            if method_name.startswith("test_"):
                try:
                    method = getattr(instance, method_name)
                    method()
                    print(f"✓ {test_class.__name__}.{method_name}")
                    passed += 1
                except Exception as e:
                    print(f"✗ {test_class.__name__}.{method_name}: {e}")
                    traceback.print_exc()
                    failed += 1
    
    print(f"\nResults: {passed} passed, {failed} failed")
