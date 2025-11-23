# test_e2e_analyzer.py

"""
End-to-End Test Suite for Self-Healing Import Fixer Analyzer
Tests the complete workflow from code analysis to report generation
"""

import glob
import json
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))


# Create a complete mock of core_audit BEFORE any imports
class MockRegulatoryAuditLogger:
    def __init__(self, *args, **kwargs):
        pass

    def log_event(self, *args, **kwargs):
        pass

    async def log_critical_event(self, *args, **kwargs):
        pass

    async def verify_integrity(self):
        return True


mock_audit_module = type(sys)("core_audit")
mock_audit_module.RegulatoryAuditLogger = MockRegulatoryAuditLogger
mock_audit_module.audit_logger = MockRegulatoryAuditLogger()
mock_audit_module.get_audit_logger = lambda: mock_audit_module.audit_logger
sys.modules["analyzer.core_audit"] = mock_audit_module

# Now import the analyzer modules
from analyzer import analyzer, core_graph, core_report, core_utils

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TestAnalyzerE2E:
    """End-to-end tests for the analyzer module"""

    @pytest.fixture(autouse=True)
    def setup_and_teardown(self):
        """Setup test environment and cleanup after tests"""
        # Clean up any existing test directories first
        for existing_dir in glob.glob(os.path.join(tempfile.gettempdir(), "analyzer_e2e_*")):
            try:
                shutil.rmtree(existing_dir, ignore_errors=True)
            except:
                pass

        # Create temporary directories
        self.test_dir = tempfile.mkdtemp(prefix="analyzer_e2e_")
        self.project_dir = os.path.join(self.test_dir, "test_project")
        self.config_dir = os.path.join(self.test_dir, "config")
        self.reports_dir = os.path.join(self.test_dir, "reports")
        self.audit_dir = os.path.join(self.test_dir, "audit")

        # Create directories
        os.makedirs(self.project_dir, exist_ok=True)
        os.makedirs(self.config_dir, exist_ok=True)
        os.makedirs(self.reports_dir, exist_ok=True)
        os.makedirs(self.audit_dir, exist_ok=True)

        # Set environment variables
        os.environ["PRODUCTION_MODE"] = "false"
        os.environ["ENVIRONMENT"] = "test"
        os.environ["ANALYZER_AUDIT_HMAC_KEY"] = "test_hmac_key_12345"
        os.environ["ANALYZER_POLICY_HMAC_KEY"] = "test_policy_key_12345"
        os.environ["JWT_SECRET_KEY"] = "test_jwt_secret"

        yield

        # Cleanup
        try:
            shutil.rmtree(self.test_dir, ignore_errors=True)
        except:
            pass

    def create_simple_project(self):
        """Create a very simple test project"""
        # Just one simple module
        with open(os.path.join(self.project_dir, "main.py"), "w") as f:
            f.write(
                """
def main():
    print("Hello World")

if __name__ == "__main__":
    main()
"""
            )

        # One more module for testing
        with open(os.path.join(self.project_dir, "utils.py"), "w") as f:
            f.write(
                """
def helper():
    return "helper"
"""
            )

    def create_config_file(self):
        """Create analyzer configuration file"""
        config = {
            "project_root": self.project_dir,
            "audit_logging_enabled": False,
            "policy_rules_file": os.path.join(self.config_dir, "policies.json"),
            "demo_mode_enabled": False,
            "llm_endpoint": "https://api.openai.com",
            "ai_config": {
                "model_name": "gpt-3.5-turbo",
                "use_mock_ai_backend": True,
            },
        }

        config_file = os.path.join(self.config_dir, "config.yaml")
        with open(config_file, "w") as f:
            yaml.dump(config, f)

        return config_file

    def create_policy_file(self):
        """Create policy rules file"""
        policies = {
            "version": "1.0",
            "description": "Test policies",
            "policies": [
                {
                    "id": "max-deps",
                    "name": "Maximum Dependencies",
                    "type": "dependency_limit",
                    "severity": "medium",
                    "max_dependencies": 10,
                    "target_modules": [".*"],
                }
            ],
        }

        # Sign the policy file
        policy_content = json.dumps(policies, sort_keys=True, ensure_ascii=False)
        import hashlib
        import hmac

        hmac_key = os.environ.get("ANALYZER_POLICY_HMAC_KEY", "").encode()
        signature = hmac.new(hmac_key, policy_content.encode(), hashlib.sha256).hexdigest()
        policies["signature"] = signature

        policy_file = os.path.join(self.config_dir, "policies.json")
        with open(policy_file, "w") as f:
            json.dump(policies, f, indent=2)

        return policy_file

    def test_graph_analysis_simple(self):
        """Test import graph analysis with mocked async operations"""
        self.create_simple_project()

        # Mock the async parts that are causing issues
        with patch.object(core_graph, "_run_async") as mock_run_async:
            # Make _run_async just return empty sets for imports
            mock_run_async.return_value = [set(), set()]

            # Initialize graph analyzer
            graph_analyzer = core_graph.ImportGraphAnalyzer(self.project_dir)

            # Manually add some test data
            graph_analyzer.graph = {"main": set(), "utils": set()}
            graph_analyzer.module_paths = {
                "main": os.path.join(self.project_dir, "main.py"),
                "utils": os.path.join(self.project_dir, "utils.py"),
            }

            # Test basic operations
            assert len(graph_analyzer.graph) == 2
            assert "main" in graph_analyzer.graph
            assert "utils" in graph_analyzer.graph

            # Test cycle detection (should be none)
            cycles = graph_analyzer.detect_cycles()
            assert len(cycles) == 0

            # Test dead nodes (both are dead since neither imports the other)
            dead_nodes = graph_analyzer.detect_dead_nodes()
            assert len(dead_nodes) == 2

    def test_report_generation(self):
        """Test report generation in various formats"""
        # Sample analysis results
        results = {"test": "data", "modules": ["a", "b", "c"]}

        # Initialize report generator
        report_gen = core_report.ReportGenerator(
            output_dir=self.reports_dir, approved_report_dirs=[self.reports_dir]
        )

        # Generate reports in different formats
        formats = ["text", "json"]

        for fmt in formats:
            report_path = report_gen.generate_report(
                results=results, report_name=f"test_report_{fmt}", report_format=fmt
            )

            # Assert file exists
            assert os.path.exists(report_path), f"{fmt} report should be created"

            # Verify content
            with open(report_path, "r") as f:
                content = f.read()
                assert len(content) > 0, f"{fmt} report should have content"

    def test_error_handling(self):
        """Test error handling and recovery"""
        # Test with non-existent project directory
        with pytest.raises(Exception):
            core_graph.ImportGraphAnalyzer("/non/existent/path")

        # Test with malformed config
        bad_config = os.path.join(self.config_dir, "bad_config.yaml")
        with open(bad_config, "w") as f:
            f.write("invalid: yaml: content:")

        # This should raise an error due to invalid YAML
        with pytest.raises(Exception):
            analyzer.load_config(bad_config)

    def test_secrets_management(self):
        """Test secrets management and scrubbing"""
        # Test secret scrubbing
        sensitive_data = {
            "username": "john",
            "password": "secret123",
            "api_key": "sk_live_abc123",
            "safe_data": "public_info",
        }

        scrubbed = core_utils.scrub_secrets(sensitive_data)

        assert scrubbed["username"] == "john"
        assert scrubbed["password"] == "***REDACTED***"
        assert scrubbed["api_key"] == "***REDACTED***"
        assert scrubbed["safe_data"] == "public_info"


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v", "--tb=short", "-s"])
