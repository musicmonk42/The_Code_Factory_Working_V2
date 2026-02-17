# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Test suite for server/services/sfe_utils.py

Tests enterprise-grade SFE utility functions including pipeline-to-frontend
transformation, input validation, error handling, and deterministic ID generation.
"""

import hashlib
import logging
import pytest

from server.services.sfe_utils import (
    transform_pipeline_issues_to_frontend_errors,
    MAX_ISSUES_PER_BATCH,
    ERROR_ID_PREFIX,
    DEFAULT_SEVERITY,
)


class TestConstants:
    """Test module constants are properly defined"""

    def test_constants_exist(self):
        """Test that all expected constants are defined"""
        assert MAX_ISSUES_PER_BATCH > 0
        assert ERROR_ID_PREFIX == "err-"
        assert DEFAULT_SEVERITY in ["critical", "high", "medium", "low"]

    def test_constants_types(self):
        """Test constant types are correct"""
        assert isinstance(MAX_ISSUES_PER_BATCH, int)
        assert isinstance(ERROR_ID_PREFIX, str)
        assert isinstance(DEFAULT_SEVERITY, str)


class TestTransformPipelineIssues:
    """Test transform_pipeline_issues_to_frontend_errors function"""

    def test_basic_transformation(self):
        """Test basic issue transformation"""
        pipeline_issues = [{
            "type": "ImportError",
            "risk_level": "high",
            "file": "main.py",
            "details": {"message": "Module 'requests' not found", "line": 15}
        }]
        
        errors = transform_pipeline_issues_to_frontend_errors(pipeline_issues, "job-123")
        
        assert len(errors) == 1
        error = errors[0]
        
        # Check all required fields are present
        assert "error_id" in error
        assert "job_id" in error
        assert "severity" in error
        assert "message" in error
        assert "file" in error
        assert "line" in error
        assert "type" in error
        
        # Check field values
        assert error["job_id"] == "job-123"
        assert error["severity"] == "high"
        assert error["message"] == "Module 'requests' not found"
        assert error["file"] == "main.py"
        assert error["line"] == 15
        assert error["type"] == "ImportError"
        assert error["error_id"].startswith(ERROR_ID_PREFIX)

    def test_deterministic_error_ids(self):
        """Test that same issue generates same error_id"""
        pipeline_issues = [{
            "type": "TypeError",
            "risk_level": "critical",
            "file": "utils.py",
            "details": {"message": "Expected int, got str", "line": 42}
        }]
        
        errors1 = transform_pipeline_issues_to_frontend_errors(pipeline_issues, "job-456")
        errors2 = transform_pipeline_issues_to_frontend_errors(pipeline_issues, "job-456")
        
        assert errors1[0]["error_id"] == errors2[0]["error_id"]

    def test_different_issues_different_ids(self):
        """Test that different issues get different error_ids"""
        issue1 = [{
            "type": "TypeError",
            "risk_level": "high",
            "details": {"message": "Type mismatch", "line": 10, "file": "a.py"}
        }]
        
        issue2 = [{
            "type": "SyntaxError",
            "risk_level": "critical",
            "details": {"message": "Invalid syntax", "line": 20, "file": "b.py"}
        }]
        
        errors1 = transform_pipeline_issues_to_frontend_errors(issue1, "job-001")
        errors2 = transform_pipeline_issues_to_frontend_errors(issue2, "job-001")
        
        assert errors1[0]["error_id"] != errors2[0]["error_id"]

    def test_empty_issues_list(self):
        """Test handling of empty issues list"""
        errors = transform_pipeline_issues_to_frontend_errors([], "job-789")
        assert errors == []

    def test_multiple_issues(self):
        """Test transformation of multiple issues"""
        pipeline_issues = [
            {
                "type": "ImportError",
                "risk_level": "high",
                "details": {"message": "Module not found", "line": 5, "file": "a.py"}
            },
            {
                "type": "SyntaxError",
                "risk_level": "critical",
                "details": {"message": "Invalid syntax", "line": 10, "file": "b.py"}
            },
            {
                "type": "NameError",
                "risk_level": "medium",
                "details": {"message": "Undefined variable", "line": 15, "file": "c.py"}
            }
        ]
        
        errors = transform_pipeline_issues_to_frontend_errors(pipeline_issues, "job-multi")
        
        assert len(errors) == 3
        assert errors[0]["type"] == "ImportError"
        assert errors[1]["type"] == "SyntaxError"
        assert errors[2]["type"] == "NameError"
        
        # All should have unique IDs
        error_ids = [e["error_id"] for e in errors]
        assert len(set(error_ids)) == 3

    def test_missing_file_field(self):
        """Test handling when file is missing"""
        pipeline_issues = [{
            "type": "RuntimeError",
            "risk_level": "high",
            "details": {"message": "Runtime error", "line": 25}
        }]
        
        errors = transform_pipeline_issues_to_frontend_errors(pipeline_issues, "job-nofile")
        
        assert errors[0]["file"] == "unknown"

    def test_file_in_details(self):
        """Test file path extracted from details if not at top level"""
        pipeline_issues = [{
            "type": "ValueError",
            "risk_level": "medium",
            "details": {
                "message": "Invalid value",
                "line": 30,
                "file": "nested/path.py"
            }
        }]
        
        errors = transform_pipeline_issues_to_frontend_errors(pipeline_issues, "job-nested")
        
        assert errors[0]["file"] == "nested/path.py"

    def test_file_precedence(self):
        """Test top-level file takes precedence over details.file"""
        pipeline_issues = [{
            "type": "KeyError",
            "risk_level": "low",
            "file": "top_level.py",
            "details": {
                "message": "Key not found",
                "line": 35,
                "file": "details_level.py"
            }
        }]
        
        errors = transform_pipeline_issues_to_frontend_errors(pipeline_issues, "job-precedence")
        
        assert errors[0]["file"] == "top_level.py"

    def test_default_severity(self):
        """Test default severity is applied when risk_level missing"""
        pipeline_issues = [{
            "type": "Warning",
            "details": {"message": "Deprecated function", "line": 40, "file": "old.py"}
        }]
        
        errors = transform_pipeline_issues_to_frontend_errors(pipeline_issues, "job-default")
        
        assert errors[0]["severity"] == DEFAULT_SEVERITY

    def test_default_line_number(self):
        """Test default line number is 0 when missing"""
        pipeline_issues = [{
            "type": "ConfigError",
            "risk_level": "medium",
            "file": "config.py",
            "details": {"message": "Invalid config"}
        }]
        
        errors = transform_pipeline_issues_to_frontend_errors(pipeline_issues, "job-noline")
        
        assert errors[0]["line"] == 0

    def test_invalid_line_number_conversion(self):
        """Test invalid line number is converted to 0"""
        pipeline_issues = [{
            "type": "Error",
            "risk_level": "high",
            "file": "test.py",
            "details": {"message": "Error", "line": "invalid"}
        }]
        
        errors = transform_pipeline_issues_to_frontend_errors(pipeline_issues, "job-badline")
        
        assert errors[0]["line"] == 0

    def test_default_type(self):
        """Test default type is 'unknown' when missing"""
        pipeline_issues = [{
            "risk_level": "medium",
            "file": "unknown.py",
            "details": {"message": "Something went wrong", "line": 50}
        }]
        
        errors = transform_pipeline_issues_to_frontend_errors(pipeline_issues, "job-notype")
        
        assert errors[0]["type"] == "unknown"

    def test_message_fallback(self):
        """Test message falls back to string representation of issue"""
        pipeline_issues = [{
            "type": "CustomError",
            "risk_level": "low",
            "file": "custom.py",
            "details": {"line": 60}  # No message field
        }]
        
        errors = transform_pipeline_issues_to_frontend_errors(pipeline_issues, "job-nomsg")
        
        # Should have some message (string representation of issue)
        assert errors[0]["message"] != ""
        assert isinstance(errors[0]["message"], str)

    def test_error_id_length(self):
        """Test error_id has correct format and length"""
        pipeline_issues = [{
            "type": "TestError",
            "risk_level": "high",
            "details": {"message": "Test", "line": 1, "file": "test.py"}
        }]
        
        errors = transform_pipeline_issues_to_frontend_errors(pipeline_issues, "job-id")
        
        error_id = errors[0]["error_id"]
        assert error_id.startswith(ERROR_ID_PREFIX)
        # err- (4) + 16 hex chars = 20 total
        assert len(error_id) == 20

    def test_error_id_is_hex(self):
        """Test error_id hash portion is valid hexadecimal"""
        pipeline_issues = [{
            "type": "TestError",
            "risk_level": "high",
            "details": {"message": "Test", "line": 1, "file": "test.py"}
        }]
        
        errors = transform_pipeline_issues_to_frontend_errors(pipeline_issues, "job-hex")
        
        error_id = errors[0]["error_id"]
        hash_part = error_id[len(ERROR_ID_PREFIX):]
        
        # Should be valid hexadecimal
        try:
            int(hash_part, 16)
            is_hex = True
        except ValueError:
            is_hex = False
        
        assert is_hex

    def test_invalid_severity_fallback(self):
        """Test invalid severity falls back to default"""
        pipeline_issues = [{
            "type": "Error",
            "risk_level": "super_ultra_critical",  # Invalid
            "details": {"message": "Error", "line": 70, "file": "bad.py"}
        }]
        
        errors = transform_pipeline_issues_to_frontend_errors(pipeline_issues, "job-badsev")
        
        assert errors[0]["severity"] == DEFAULT_SEVERITY


class TestInputValidation:
    """Test input validation and error handling"""

    def test_none_issues_raises_error(self):
        """Test that None issues raises ValueError"""
        with pytest.raises(ValueError, match="issues must be a list"):
            transform_pipeline_issues_to_frontend_errors(None, "job-123")

    def test_non_list_issues_raises_error(self):
        """Test that non-list issues raises ValueError"""
        with pytest.raises(ValueError, match="issues must be a list"):
            transform_pipeline_issues_to_frontend_errors("not a list", "job-123")

    def test_dict_issues_raises_error(self):
        """Test that dict issues raises ValueError"""
        with pytest.raises(ValueError, match="issues must be a list"):
            transform_pipeline_issues_to_frontend_errors({"issue": "data"}, "job-123")

    def test_empty_job_id_raises_error(self):
        """Test that empty job_id raises ValueError"""
        with pytest.raises(ValueError, match="job_id must be a non-empty string"):
            transform_pipeline_issues_to_frontend_errors([], "")

    def test_none_job_id_raises_error(self):
        """Test that None job_id raises ValueError"""
        with pytest.raises(ValueError, match="job_id must be a non-empty string"):
            transform_pipeline_issues_to_frontend_errors([], None)

    def test_whitespace_job_id_raises_error(self):
        """Test that whitespace-only job_id raises ValueError"""
        with pytest.raises(ValueError, match="job_id cannot be empty"):
            transform_pipeline_issues_to_frontend_errors([], "   ")

    def test_non_dict_issue_is_skipped(self):
        """Test that non-dict issues are skipped with logging"""
        pipeline_issues = [
            {"type": "Valid", "risk_level": "high", "details": {"message": "OK", "line": 1, "file": "a.py"}},
            "invalid issue",  # This should be skipped
            {"type": "Valid2", "risk_level": "medium", "details": {"message": "OK", "line": 2, "file": "b.py"}},
        ]
        
        # Should process valid issues and skip invalid
        errors = transform_pipeline_issues_to_frontend_errors(pipeline_issues, "job-mixed")
        
        assert len(errors) == 2
        assert errors[0]["type"] == "Valid"
        assert errors[1]["type"] == "Valid2"

    def test_large_batch_warning(self, caplog):
        """Test warning is logged for large batches"""
        # Create a large batch
        large_batch = [
            {
                "type": "Error",
                "risk_level": "medium",
                "details": {"message": f"Error {i}", "line": i, "file": "test.py"}
            }
            for i in range(MAX_ISSUES_PER_BATCH + 100)
        ]
        
        with caplog.at_level(logging.WARNING):
            errors = transform_pipeline_issues_to_frontend_errors(large_batch, "job-large")
        
        # Should still process all issues
        assert len(errors) == MAX_ISSUES_PER_BATCH + 100
        
        # Should have warning in logs
        assert any("Large issue batch" in record.message for record in caplog.records)


class TestEdgeCases:
    """Test edge cases and boundary conditions"""

    def test_unicode_in_message(self):
        """Test handling of Unicode characters in message"""
        pipeline_issues = [{
            "type": "UnicodeError",
            "risk_level": "high",
            "details": {"message": "Error: 日本語 αβγ 🚀", "line": 10, "file": "unicode.py"}
        }]
        
        errors = transform_pipeline_issues_to_frontend_errors(pipeline_issues, "job-unicode")
        
        assert "日本語" in errors[0]["message"]
        assert "🚀" in errors[0]["message"]

    def test_very_long_message(self):
        """Test handling of very long messages"""
        long_message = "x" * 10000
        pipeline_issues = [{
            "type": "LongError",
            "risk_level": "medium",
            "details": {"message": long_message, "line": 100, "file": "long.py"}
        }]
        
        errors = transform_pipeline_issues_to_frontend_errors(pipeline_issues, "job-long")
        
        assert errors[0]["message"] == long_message

    def test_special_characters_in_file_path(self):
        """Test handling of special characters in file paths"""
        pipeline_issues = [{
            "type": "PathError",
            "risk_level": "low",
            "file": "path/with spaces/and-special_chars.py",
            "details": {"message": "Path issue", "line": 5}
        }]
        
        errors = transform_pipeline_issues_to_frontend_errors(pipeline_issues, "job-special")
        
        assert errors[0]["file"] == "path/with spaces/and-special_chars.py"

    def test_negative_line_number(self):
        """Test handling of negative line numbers"""
        pipeline_issues = [{
            "type": "NegativeError",
            "risk_level": "high",
            "details": {"message": "Negative line", "line": -5, "file": "neg.py"}
        }]
        
        errors = transform_pipeline_issues_to_frontend_errors(pipeline_issues, "job-neg")
        
        # Should accept negative line numbers (might be valid in some contexts)
        assert errors[0]["line"] == -5

    def test_very_large_line_number(self):
        """Test handling of very large line numbers"""
        pipeline_issues = [{
            "type": "LargeLineError",
            "risk_level": "medium",
            "details": {"message": "Large line", "line": 999999999, "file": "huge.py"}
        }]
        
        errors = transform_pipeline_issues_to_frontend_errors(pipeline_issues, "job-hugeline")
        
        assert errors[0]["line"] == 999999999

    def test_empty_details_dict(self):
        """Test handling when details is empty"""
        pipeline_issues = [{
            "type": "EmptyDetails",
            "risk_level": "low",
            "file": "empty.py",
            "details": {}
        }]
        
        errors = transform_pipeline_issues_to_frontend_errors(pipeline_issues, "job-empty")
        
        assert errors[0]["line"] == 0
        assert errors[0]["message"] != ""  # Should have fallback

    def test_missing_details_key(self):
        """Test handling when details key is missing entirely"""
        pipeline_issues = [{
            "type": "NoDetails",
            "risk_level": "high",
            "file": "nodetails.py"
        }]
        
        errors = transform_pipeline_issues_to_frontend_errors(pipeline_issues, "job-nodetails")
        
        assert errors[0]["line"] == 0
        assert errors[0]["message"] != ""


class TestLogging:
    """Test logging behavior"""

    def test_debug_logging_on_transformation(self, caplog):
        """Test debug logs are created during transformation"""
        pipeline_issues = [{
            "type": "TestError",
            "risk_level": "high",
            "details": {"message": "Test", "line": 1, "file": "test.py"}
        }]
        
        with caplog.at_level(logging.DEBUG):
            transform_pipeline_issues_to_frontend_errors(pipeline_issues, "job-log")
        
        # Should have debug logs
        assert any("Transforming" in record.message for record in caplog.records)
        assert any("Transformation complete" in record.message for record in caplog.records)

    def test_error_logging_on_invalid_issue(self, caplog):
        """Test error is logged for invalid issue"""
        pipeline_issues = [
            "this is not a dict"
        ]
        
        with caplog.at_level(logging.ERROR):
            errors = transform_pipeline_issues_to_frontend_errors(pipeline_issues, "job-err")
        
        # Should have error log
        assert any("not a dict" in record.message for record in caplog.records)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
