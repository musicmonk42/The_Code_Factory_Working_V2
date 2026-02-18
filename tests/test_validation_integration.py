# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Tests for validation integration.
"""

import tempfile
from pathlib import Path

import pytest
from generator.main.validation import (
    ValidationReport,
    validate_generated_code,
    validate_spec_compliance,
)


def test_validation_report_creation():
    """Test creating a validation report."""
    report = ValidationReport()
    
    assert report.is_valid()
    assert len(report.errors) == 0
    assert len(report.warnings) == 0


def test_validation_report_add_error():
    """Test adding errors to report."""
    report = ValidationReport()
    
    report.add_error("Test Check", "This is an error")
    
    assert not report.is_valid()
    assert len(report.errors) == 1
    assert "Test Check" in report.checks_failed


def test_validation_report_add_warning():
    """Test adding warnings to report."""
    report = ValidationReport()
    
    report.add_warning("Test Check", "This is a warning")
    
    # Warnings don't affect validity
    assert report.is_valid()
    assert len(report.warnings) == 1


def test_validation_report_mark_passed():
    """Test marking checks as passed."""
    report = ValidationReport()
    
    report.checks_run.append("Test Check")
    report.mark_passed("Test Check")
    
    assert "Test Check" in report.checks_passed
    assert report.is_valid()


def test_validation_report_to_dict():
    """Test converting report to dictionary."""
    report = ValidationReport()
    report.checks_run.append("Check 1")
    report.mark_passed("Check 1")
    report.add_error("Check 2", "Failed")
    
    data = report.to_dict()
    
    assert data["valid"] == False
    assert data["passed_count"] == 1
    assert data["failed_count"] == 1
    assert len(data["errors"]) == 1


def test_validation_report_to_text():
    """Test generating text report."""
    report = ValidationReport()
    report.checks_run.extend(["Check 1", "Check 2"])
    report.mark_passed("Check 1")
    report.add_error("Check 2", "This failed")
    
    text = report.to_text()
    
    assert "CONTRACT VALIDATION REPORT" in text
    assert "❌ FAIL" in text
    assert "Check 1" in text
    assert "Check 2" in text


def test_validate_spec_compliance_basic():
    """Test basic spec compliance validation."""
    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir) / "test_app"
        output_dir.mkdir()
        
        # Create minimal structure
        (output_dir / "app").mkdir()
        (output_dir / "requirements.txt").write_text("fastapi>=0.100.0\n")
        
        spec_block = {
            "package_name": "test_app",
            "dependencies": ["fastapi>=0.100.0"]
        }
        
        report = ValidationReport()
        
        # Should not raise
        validate_spec_compliance(output_dir, spec_block, report)


def test_validate_spec_compliance_missing_dependency():
    """Test spec compliance fails for missing dependencies."""
    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir) / "test_app"
        output_dir.mkdir()
        
        # Create minimal structure without all dependencies
        (output_dir / "requirements.txt").write_text("fastapi>=0.100.0\n")
        
        spec_block = {
            "dependencies": ["fastapi>=0.100.0", "pydantic>=2.0.0"]
        }
        
        report = ValidationReport()
        
        # Should raise for missing pydantic
        with pytest.raises(AssertionError, match="Missing dependencies"):
            validate_spec_compliance(output_dir, spec_block, report)


def test_validate_spec_compliance_output_dir_mismatch():
    """Test spec compliance fails for output_dir mismatch."""
    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir) / "actual_app"
        output_dir.mkdir()
        
        spec_block = {
            "output_dir": "generated/expected_app"
        }
        
        report = ValidationReport()
        
        # Should raise for directory name mismatch
        with pytest.raises(AssertionError, match="Output directory mismatch"):
            validate_spec_compliance(output_dir, spec_block, report)


def test_validate_spec_compliance_missing_package():
    """Test spec compliance fails when package directory missing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir) / "test_app"
        output_dir.mkdir()
        
        # Don't create the package directory
        
        spec_block = {
            "package_name": "my_package"
        }
        
        report = ValidationReport()
        
        # Should raise for missing package directory
        with pytest.raises(AssertionError, match="Package directory"):
            validate_spec_compliance(output_dir, spec_block, report)


def test_validate_spec_compliance_http_endpoints_warning():
    """Test spec compliance checks HTTP endpoints."""
    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir) / "test_app"
        output_dir.mkdir()
        (output_dir / "app").mkdir()
        
        # Create routes file with some endpoints
        routes_content = """
@app.get("/health")
def health():
    return {"status": "ok"}
"""
        (output_dir / "app" / "routes.py").write_text(routes_content)
        
        spec_block = {
            "interfaces": {
                "http": [
                    "GET /health",
                    "POST /items"  # This endpoint is missing
                ]
            }
        }
        
        report = ValidationReport()
        
        # Should not raise but add warning
        validate_spec_compliance(output_dir, spec_block, report)
        
        # Check warning was added
        assert len(report.warnings) > 0
        assert any("POST /items" in w for w in report.warnings)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
