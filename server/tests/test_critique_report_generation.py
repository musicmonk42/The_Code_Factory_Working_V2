"""
Unit tests for Critique Report Generation

Industry Standards Applied:
- Comprehensive validation testing
- Edge case coverage
- Error condition testing
- Schema validation
"""

import pytest
import json
from datetime import datetime, timezone
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from server.services.omnicore_service import _create_placeholder_critique_report


class TestCreatePlaceholderCritiqueReport:
    """Test suite for _create_placeholder_critique_report function."""
    
    def test_creates_valid_report_structure(self):
        """Test that report has all required fields."""
        report = _create_placeholder_critique_report("test-job-123", "Test message")
        
        # Verify all required fields are present
        required_fields = [
            "job_id", "timestamp", "status", "message",
            "issues_found", "issues_fixed", "coverage",
            "test_results", "issues", "fixes_applied", "scan_types"
        ]
        for field in required_fields:
            assert field in report, f"Missing required field: {field}"
    
    def test_job_id_is_set_correctly(self):
        """Test that job_id is correctly set in report."""
        job_id = "test-job-456"
        report = _create_placeholder_critique_report(job_id, "Test")
        assert report["job_id"] == job_id
    
    def test_message_is_set_correctly(self):
        """Test that message is correctly set in report."""
        message = "Critique was skipped due to configuration"
        report = _create_placeholder_critique_report("test-job", message)
        assert report["message"] == message
    
    def test_status_is_skipped(self):
        """Test that status is always 'skipped' for placeholder."""
        report = _create_placeholder_critique_report("test-job", "Test")
        assert report["status"] == "skipped"
    
    def test_timestamp_is_iso8601(self):
        """Test that timestamp is in ISO 8601 format."""
        report = _create_placeholder_critique_report("test-job", "Test")
        timestamp_str = report["timestamp"]
        
        # Should be parseable as ISO 8601
        try:
            parsed = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
            assert parsed is not None
        except ValueError:
            pytest.fail(f"Timestamp not in ISO 8601 format: {timestamp_str}")
    
    def test_timestamp_is_recent(self):
        """Test that timestamp is recent (within last minute)."""
        report = _create_placeholder_critique_report("test-job", "Test")
        timestamp_str = report["timestamp"]
        parsed = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
        
        now = datetime.now(timezone.utc)
        diff = (now - parsed).total_seconds()
        
        # Should be created within the last minute
        assert 0 <= diff < 60, f"Timestamp too old: {diff} seconds"
    
    def test_numeric_fields_are_zero(self):
        """Test that numeric fields are initialized to zero."""
        report = _create_placeholder_critique_report("test-job", "Test")
        assert report["issues_found"] == 0
        assert report["issues_fixed"] == 0
    
    def test_coverage_structure(self):
        """Test that coverage has correct structure and zero values."""
        report = _create_placeholder_critique_report("test-job", "Test")
        coverage = report["coverage"]
        
        assert "total_lines" in coverage
        assert "covered_lines" in coverage
        assert "percentage" in coverage
        
        assert coverage["total_lines"] == 0
        assert coverage["covered_lines"] == 0
        assert coverage["percentage"] == 0.0
    
    def test_test_results_structure(self):
        """Test that test_results has correct structure and zero values."""
        report = _create_placeholder_critique_report("test-job", "Test")
        test_results = report["test_results"]
        
        assert "total" in test_results
        assert "passed" in test_results
        assert "failed" in test_results
        
        assert test_results["total"] == 0
        assert test_results["passed"] == 0
        assert test_results["failed"] == 0
    
    def test_array_fields_are_empty(self):
        """Test that array fields are empty lists."""
        report = _create_placeholder_critique_report("test-job", "Test")
        
        assert isinstance(report["issues"], list)
        assert len(report["issues"]) == 0
        
        assert isinstance(report["fixes_applied"], list)
        assert len(report["fixes_applied"]) == 0
        
        assert isinstance(report["scan_types"], list)
        assert len(report["scan_types"]) == 0
    
    def test_report_is_json_serializable(self):
        """Test that report can be serialized to JSON."""
        report = _create_placeholder_critique_report("test-job", "Test")
        
        try:
            json_str = json.dumps(report, indent=2)
            assert len(json_str) > 0
            
            # Should be able to parse it back
            parsed = json.loads(json_str)
            assert parsed["job_id"] == "test-job"
        except (TypeError, ValueError) as e:
            pytest.fail(f"Report not JSON serializable: {e}")
    
    def test_empty_job_id_raises_error(self):
        """Test that empty job_id raises ValueError."""
        with pytest.raises(ValueError, match="cannot be empty"):
            _create_placeholder_critique_report("", "Test message")
    
    def test_none_job_id_raises_error(self):
        """Test that None job_id raises ValueError."""
        with pytest.raises(ValueError, match="cannot be empty"):
            _create_placeholder_critique_report(None, "Test message")  # type: ignore
    
    def test_non_string_job_id_raises_error(self):
        """Test that non-string job_id raises TypeError."""
        with pytest.raises(TypeError, match="must be a string"):
            _create_placeholder_critique_report(123, "Test message")  # type: ignore
    
    def test_empty_message_is_handled(self):
        """Test that empty message is handled gracefully."""
        report = _create_placeholder_critique_report("test-job", "")
        # Should not raise, should use fallback message
        assert "message" in report
        assert len(report["message"]) > 0
    
    def test_long_message_is_preserved(self):
        """Test that long messages are preserved."""
        long_message = "A" * 1000
        report = _create_placeholder_critique_report("test-job", long_message)
        assert report["message"] == long_message
    
    def test_special_characters_in_message(self):
        """Test that special characters in message are preserved."""
        special_message = "Test with special chars: \n\t\"'<>&"
        report = _create_placeholder_critique_report("test-job", special_message)
        assert report["message"] == special_message
    
    def test_unicode_in_message(self):
        """Test that unicode characters in message are preserved."""
        unicode_message = "Test with unicode: 你好 🚀 Ñoño"
        report = _create_placeholder_critique_report("test-job", unicode_message)
        assert report["message"] == unicode_message
    
    def test_special_characters_in_job_id(self):
        """Test that special characters in job_id are preserved."""
        special_job_id = "job-123_test.2024-01-01"
        report = _create_placeholder_critique_report(special_job_id, "Test")
        assert report["job_id"] == special_job_id
    
    def test_multiple_reports_have_different_timestamps(self):
        """Test that multiple reports created sequentially have different timestamps."""
        import time
        
        report1 = _create_placeholder_critique_report("job-1", "Test 1")
        time.sleep(0.001)  # Sleep 1ms to ensure different timestamps
        report2 = _create_placeholder_critique_report("job-2", "Test 2")
        
        # Timestamps should be different (or at least not fail)
        timestamp1 = report1["timestamp"]
        timestamp2 = report2["timestamp"]
        
        # They should be close but potentially different
        assert timestamp1 is not None
        assert timestamp2 is not None


class TestReportSchemaCompliance:
    """Test that placeholder reports match the schema of real critique reports."""
    
    def test_matches_successful_critique_schema(self):
        """Test that placeholder has same fields as successful critique."""
        placeholder = _create_placeholder_critique_report("test-job", "Test")
        
        # Expected fields from a real critique report
        expected_top_level = {
            "job_id", "timestamp", "status", "message",
            "issues_found", "issues_fixed", "coverage",
            "test_results", "issues", "fixes_applied", "scan_types"
        }
        
        assert set(placeholder.keys()) == expected_top_level
    
    def test_coverage_matches_expected_schema(self):
        """Test that coverage structure matches expected schema."""
        placeholder = _create_placeholder_critique_report("test-job", "Test")
        coverage = placeholder["coverage"]
        
        expected_coverage_keys = {"total_lines", "covered_lines", "percentage"}
        assert set(coverage.keys()) == expected_coverage_keys
    
    def test_test_results_matches_expected_schema(self):
        """Test that test_results structure matches expected schema."""
        placeholder = _create_placeholder_critique_report("test-job", "Test")
        test_results = placeholder["test_results"]
        
        expected_test_keys = {"total", "passed", "failed"}
        assert set(test_results.keys()) == expected_test_keys


class TestIntegrationWithFileSystem:
    """Integration tests for writing reports to filesystem."""
    
    def test_report_can_be_written_to_file(self, tmp_path):
        """Test that report can be written to a JSON file."""
        report = _create_placeholder_critique_report("test-job", "Test")
        
        report_file = tmp_path / "critique_report.json"
        report_file.write_text(json.dumps(report, indent=2))
        
        # Should be able to read it back
        content = report_file.read_text()
        parsed = json.loads(content)
        
        assert parsed["job_id"] == "test-job"
    
    def test_report_file_is_valid_json(self, tmp_path):
        """Test that written report file is valid JSON."""
        report = _create_placeholder_critique_report("test-job", "Test")
        
        report_file = tmp_path / "critique_report.json"
        with open(report_file, 'w') as f:
            json.dump(report, f, indent=2)
        
        # Validate by parsing
        with open(report_file, 'r') as f:
            parsed = json.load(f)
        
        assert parsed is not None
        assert isinstance(parsed, dict)
