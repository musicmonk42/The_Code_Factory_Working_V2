"""
test_deploy_response_handler.py
Enterprise-grade test suite for deploy_response_handler.py

Features:
- Comprehensive unit tests with proper isolation
- Integration tests for handler registry and hot-reload
- Performance tests for large configurations
- Security tests for PII/secret scrubbing
- Parametrized tests for multiple format handlers
- Async test support with proper event loop management
- Mock management for external dependencies
- Property-based testing with Hypothesis
- Test fixtures and factories for test data
- Coverage reporting integration
"""

import asyncio
import json
import os
import sys
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, Mock, patch, call

import pytest
import pytest_asyncio
from hypothesis import given, strategies as st, settings, Verbosity
from hypothesis.provisional import urls
import yaml
import hcl2
from ruamel.yaml import YAML
import aiofiles
from freezegun import freeze_time
from faker import Faker

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from deploy_response_handler import (
    scrub_text,
    scan_config_for_findings,
    FormatHandler,
    DockerfileHandler,
    YAMLHandler,
    JSONHandler,
    HCLHandler,
    HandlerRegistry,
    repair_sections,
    enrich_config_output,
    analyze_quality,
    handle_deploy_response,
    DANGEROUS_CONFIG_PATTERNS
)

# Initialize faker for test data generation
fake = Faker()

# Test configuration
TEST_TIMEOUT = 30  # seconds
PERFORMANCE_THRESHOLD = 1.0  # seconds


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_presidio():
    """Mock Presidio analyzer and anonymizer."""
    with patch('deploy_response_handler.AnalyzerEngine') as mock_analyzer_cls, \
         patch('deploy_response_handler.AnonymizerEngine') as mock_anonymizer_cls:
        
        mock_analyzer = MagicMock()
        mock_anonymizer = MagicMock()
        
        mock_analyzer_cls.return_value = mock_analyzer
        mock_anonymizer_cls.return_value = mock_anonymizer
        
        # Configure analyzer to find some PII
        mock_analyzer.analyze.return_value = [
            MagicMock(entity_type='EMAIL_ADDRESS', start=10, end=25),
            MagicMock(entity_type='CREDIT_CARD', start=30, end=46)
        ]
        
        # Configure anonymizer to redact
        mock_anonymizer.anonymize.return_value = MagicMock(
            text="Some text [REDACTED] and [REDACTED]"
        )
        
        yield mock_analyzer, mock_anonymizer


@pytest.fixture
def mock_llm_generate_config():
    """Mock the LLM generate_config function."""
    with patch('deploy_response_handler.generate_config') as mock_gen:
        async def async_generate(*args, **kwargs):
            return {
                "config": {
                    "content": "FROM alpine:3.14\nRUN apk add --no-cache python3\nCMD [\"python3\"]"
                },
                "model": "gpt-4o",
                "provider": "mock",
                "valid": True
            }
        mock_gen.side_effect = async_generate
        yield mock_gen


@pytest.fixture
def mock_summarize_text():
    """Mock the summarize_text utility."""
    with patch('deploy_response_handler.summarize_text') as mock_summarize:
        async def async_summarize(text, max_length=4000):
            return text[:max_length] if len(text) > max_length else text
        mock_summarize.side_effect = async_summarize
        yield mock_summarize


@pytest.fixture
def temp_plugin_dir(tmp_path):
    """Create a temporary plugin directory for testing."""
    plugin_dir = tmp_path / "test_plugins"
    plugin_dir.mkdir()
    return str(plugin_dir)


@pytest.fixture
def sample_dockerfile():
    """Provide a sample valid Dockerfile."""
    return """
FROM python:3.9-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
USER nobody
CMD ["python", "app.py"]
"""


@pytest.fixture
def sample_yaml():
    """Provide a sample valid Kubernetes YAML."""
    return """
apiVersion: apps/v1
kind: Deployment
metadata:
  name: test-app
  labels:
    app: test
spec:
  replicas: 3
  selector:
    matchLabels:
      app: test
  template:
    metadata:
      labels:
        app: test
    spec:
      containers:
      - name: app
        image: test:latest
        resources:
          limits:
            memory: "128Mi"
            cpu: "100m"
        ports:
        - containerPort: 8080
"""


@pytest.fixture
def sample_json():
    """Provide a sample valid JSON configuration."""
    return json.dumps({
        "name": "test-config",
        "version": "1.0.0",
        "settings": {
            "debug": False,
            "port": 8080,
            "database": {
                "host": "localhost",
                "port": 5432
            }
        }
    }, indent=2)


@pytest.fixture
def sample_hcl():
    """Provide a sample valid HCL (Terraform) configuration."""
    return """
resource "aws_instance" "example" {
  ami           = "ami-0c55b159cbfafe1f0"
  instance_type = "t2.micro"
  
  tags = {
    Name = "ExampleInstance"
  }
}

variable "region" {
  default = "us-west-2"
}
"""


# ============================================================================
# TEST DATA FACTORIES
# ============================================================================

class ConfigFactory:
    """Factory for generating test configurations."""
    
    @staticmethod
    def create_dockerfile(
        base_image: str = "alpine:latest",
        has_user: bool = True,
        has_healthcheck: bool = False,
        with_secrets: bool = False
    ) -> str:
        lines = [f"FROM {base_image}"]
        
        if with_secrets:
            lines.append('ENV API_KEY="sk-1234567890abcdef"')
        
        lines.extend([
            "WORKDIR /app",
            "COPY . .",
            "RUN apk add --no-cache python3"
        ])
        
        if has_healthcheck:
            lines.append("HEALTHCHECK CMD curl -f http://localhost/ || exit 1")
        
        if has_user:
            lines.append("USER nobody")
        
        lines.append('CMD ["python3", "app.py"]')
        
        return "\n".join(lines)
    
    @staticmethod
    def create_yaml_with_issues(
        missing_metadata: bool = False,
        missing_resources: bool = False,
        with_secrets: bool = False
    ) -> str:
        config = {
            "apiVersion": "v1",
            "kind": "Pod"
        }
        
        if not missing_metadata:
            config["metadata"] = {"name": "test-pod"}
        
        spec = {"containers": [{"name": "app", "image": "test:latest"}]}
        
        if with_secrets:
            spec["containers"][0]["env"] = [
                {"name": "SECRET", "value": "password123"}
            ]
        
        if not missing_resources:
            spec["containers"][0]["resources"] = {
                "limits": {"memory": "128Mi", "cpu": "100m"}
            }
        
        config["spec"] = spec
        
        return yaml.dump(config)


# ============================================================================
# UNIT TESTS - Text Scrubbing
# ============================================================================

class TestTextScrubbing:
    """Test suite for text scrubbing functionality."""
    
    def test_scrub_empty_text(self):
        """Test scrubbing empty or None text."""
        assert scrub_text("") == ""
        assert scrub_text(None) == ""
    
    def test_scrub_text_with_presidio(self, mock_presidio):
        """Test text scrubbing with Presidio available."""
        text = "Contact john@example.com with card 4111111111111111"
        result = scrub_text(text)
        assert "[REDACTED]" in result
        mock_presidio[0].analyze.assert_called_once()
        mock_presidio[1].anonymize.assert_called_once()
    
    def test_scrub_text_presidio_failure(self):
        """Test that Presidio failure raises RuntimeError."""
        with patch('deploy_response_handler.AnalyzerEngine') as mock_analyzer:
            mock_analyzer.side_effect = Exception("Presidio failed")
            
            with pytest.raises(RuntimeError, match="Critical error during sensitive data scrubbing"):
                scrub_text("test text")
    
    @given(st.text(min_size=0, max_size=1000))
    def test_scrub_text_property_based(self, text):
        """Property-based test: scrubbing should never fail on valid text."""
        with patch('deploy_response_handler.AnalyzerEngine'), \
             patch('deploy_response_handler.AnonymizerEngine'):
            result = scrub_text(text)
            assert isinstance(result, str)
            assert len(result) <= len(text) + result.count("[REDACTED]") * 10


# ============================================================================
# UNIT TESTS - Security Scanning
# ============================================================================

class TestSecurityScanning:
    """Test suite for security scanning functionality."""
    
    @pytest.mark.asyncio
    async def test_scan_empty_config(self):
        """Test scanning empty configuration."""
        findings = await scan_config_for_findings("", "dockerfile")
        assert isinstance(findings, list)
        assert len(findings) == 0
    
    @pytest.mark.asyncio
    async def test_scan_dangerous_patterns(self):
        """Test detection of dangerous configuration patterns."""
        dangerous_config = """
        privileged: true
        hostPath: /etc/sensitive
        USER root
        EXPOSE 1-65535
        resources: {}
        password: supersecret123
        """
        
        findings = await scan_config_for_findings(dangerous_config, "yaml")
        
        # Check that dangerous patterns are detected
        finding_categories = {f["category"] for f in findings}
        assert "PrivilegedContainer" in finding_categories
        assert "HostPathMount" in finding_categories
        assert "RootUserInDockerfile" in finding_categories
    
    @pytest.mark.asyncio
    async def test_scan_with_trivy(self, tmp_path):
        """Test integration with Trivy scanner."""
        config = "FROM alpine:latest\nRUN apk add curl"
        
        with patch('asyncio.create_subprocess_exec') as mock_subprocess:
            mock_process = AsyncMock()
            mock_process.returncode = 1  # Trivy found issues
            mock_process.communicate.return_value = (
                json.dumps({
                    "Results": [{
                        "Misconfigurations": [{
                            "Type": "Dockerfile",
                            "ID": "DS002",
                            "Title": "Image user not set",
                            "Description": "Running as root",
                            "Severity": "HIGH"
                        }]
                    }]
                }).encode(),
                b""
            )
            mock_subprocess.return_value = mock_process
            
            findings = await scan_config_for_findings(config, "dockerfile")
            
            assert len(findings) > 0
            trivy_findings = [f for f in findings if f["type"] == "Misconfiguration_Trivy"]
            assert len(trivy_findings) > 0


# ============================================================================
# UNIT TESTS - Format Handlers
# ============================================================================

class TestDockerfileHandler:
    """Test suite for Dockerfile handler."""
    
    def test_normalize_valid_dockerfile(self, sample_dockerfile):
        """Test normalization of valid Dockerfile."""
        handler = DockerfileHandler()
        result = handler.normalize(sample_dockerfile)
        
        assert isinstance(result, list)
        assert len(result) > 0
        assert any(line.startswith("FROM") for line in result)
    
    def test_normalize_invalid_input(self):
        """Test normalization with invalid input."""
        handler = DockerfileHandler()
        
        with pytest.raises(ValueError):
            handler.normalize(None)
        
        with pytest.raises(ValueError):
            handler.normalize("")
    
    def test_extract_sections(self):
        """Test section extraction from Dockerfile."""
        handler = DockerfileHandler()
        dockerfile = handler.normalize(ConfigFactory.create_dockerfile())
        sections = handler.extract_sections(dockerfile)
        
        assert "FROM" in sections
        assert "CMD" in sections
        assert sections["FROM"].startswith("FROM")
    
    def test_lint_dockerfile(self):
        """Test Dockerfile linting."""
        handler = DockerfileHandler()
        
        # Test with issues
        bad_dockerfile = ["RUN apt-get update", "CMD sleep infinity"]
        issues = handler.lint(bad_dockerfile)
        assert len(issues) > 0
        assert any("FROM" in issue for issue in issues)
        
        # Test without issues
        good_dockerfile = handler.normalize(ConfigFactory.create_dockerfile())
        issues = handler.lint(good_dockerfile)
        assert len(issues) == 0 or all("Consider" in i for i in issues)
    
    @pytest.mark.parametrize("to_format,expected_type", [
        ("dockerfile", str),
        ("yaml", str),
    ])
    def test_convert_formats(self, to_format, expected_type):
        """Test format conversion."""
        handler = DockerfileHandler()
        data = ["FROM alpine", "CMD echo hello"]
        
        if to_format in ["dockerfile", "yaml"]:
            result = handler.convert(data, to_format)
            assert isinstance(result, expected_type)
        else:
            with pytest.raises(ValueError):
                handler.convert(data, "unsupported")


class TestYAMLHandler:
    """Test suite for YAML handler."""
    
    def test_normalize_valid_yaml(self, sample_yaml):
        """Test normalization of valid YAML."""
        handler = YAMLHandler()
        result = handler.normalize(sample_yaml)
        
        assert isinstance(result, dict)
        assert "apiVersion" in result
        assert "kind" in result
    
    def test_normalize_invalid_yaml(self):
        """Test normalization with invalid YAML."""
        handler = YAMLHandler()
        
        with pytest.raises(ValueError, match="Invalid YAML format"):
            handler.normalize("{{invalid yaml")
    
    def test_lint_kubernetes_yaml(self):
        """Test linting of Kubernetes YAML."""
        handler = YAMLHandler()
        
        # Missing metadata.name
        bad_yaml = {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {},
            "spec": {"containers": []}
        }
        issues = handler.lint(bad_yaml)
        assert len(issues) > 0
        assert any("metadata.name" in issue for issue in issues)
        
        # Missing resources
        bad_yaml = {
            "apiVersion": "v1",
            "kind": "Deployment",
            "metadata": {"name": "test"},
            "spec": {
                "template": {
                    "spec": {
                        "containers": [{"name": "app", "image": "test"}]
                    }
                }
            }
        }
        issues = handler.lint(bad_yaml)
        assert any("resource limits" in issue for issue in issues)


class TestJSONHandler:
    """Test suite for JSON handler."""
    
    def test_normalize_valid_json(self, sample_json):
        """Test normalization of valid JSON."""
        handler = JSONHandler()
        result = handler.normalize(sample_json)
        
        assert isinstance(result, dict)
        assert "name" in result
        assert result["name"] == "test-config"
    
    @given(st.dictionaries(
        st.text(min_size=1, max_size=20),
        st.one_of(st.text(), st.integers(), st.booleans()),
        min_size=0,
        max_size=10
    ))
    def test_json_round_trip(self, data):
        """Property test: JSON should round-trip correctly."""
        handler = JSONHandler()
        json_str = json.dumps(data)
        normalized = handler.normalize(json_str)
        converted = handler.convert(normalized, "json")
        final = json.loads(converted)
        assert final == data


class TestHCLHandler:
    """Test suite for HCL handler."""
    
    def test_normalize_valid_hcl(self, sample_hcl):
        """Test normalization of valid HCL."""
        handler = HCLHandler()
        result = handler.normalize(sample_hcl)
        
        assert isinstance(result, dict)
        assert "resource" in result
    
    def test_lint_hcl(self):
        """Test HCL linting."""
        handler = HCLHandler()
        
        # Empty configuration
        issues = handler.lint({})
        assert any("empty" in issue.lower() for issue in issues)
        
        # Empty resource block
        issues = handler.lint({"resource": {}})
        assert any("resource" in issue.lower() for issue in issues)


# ============================================================================
# INTEGRATION TESTS - Handler Registry
# ============================================================================

class TestHandlerRegistry:
    """Test suite for handler registry with hot-reload."""
    
    def test_registry_initialization(self, temp_plugin_dir):
        """Test registry initializes with built-in handlers."""
        registry = HandlerRegistry(temp_plugin_dir)
        
        assert "dockerfile" in registry.handlers
        assert "yaml" in registry.handlers
        assert "json" in registry.handlers
        assert "hcl" in registry.handlers
    
    def test_get_handler_success(self, temp_plugin_dir):
        """Test successful handler retrieval."""
        registry = HandlerRegistry(temp_plugin_dir)
        
        handler = registry.get_handler("dockerfile")
        assert isinstance(handler, DockerfileHandler)
        
        handler = registry.get_handler("YAML")  # Case insensitive
        assert isinstance(handler, YAMLHandler)
    
    def test_get_handler_strict_failure(self, temp_plugin_dir):
        """Test strict failure for missing handler."""
        registry = HandlerRegistry(temp_plugin_dir)
        
        with pytest.raises(ValueError, match="No handler found for output format"):
            registry.get_handler("nonexistent")
    
    @pytest.mark.asyncio
    async def test_custom_handler_loading(self, temp_plugin_dir):
        """Test loading custom handler from plugin directory."""
        # Create a custom handler file
        handler_file = Path(temp_plugin_dir) / "custom_handler.py"
        handler_file.write_text("""
from deploy_response_handler import FormatHandler
from typing import Dict, Any, List

class CustomHandler(FormatHandler):
    __version__ = "2.0"
    __source__ = "custom"
    
    def normalize(self, raw: str) -> str:
        return f"CUSTOM: {raw}"
    
    def convert(self, data: Any, to_format: str) -> str:
        return str(data)
    
    def extract_sections(self, data: Any) -> Dict[str, str]:
        return {"custom": str(data)}
    
    def lint(self, data: Any) -> List[str]:
        return []
""")
        
        registry = HandlerRegistry(temp_plugin_dir)
        
        # Give the watcher time to detect the file
        await asyncio.sleep(0.5)
        
        assert "custom" in registry.handlers
        handler = registry.get_handler("custom")
        assert handler.__version__ == "2.0"
    
    @pytest.mark.asyncio
    async def test_hot_reload(self, temp_plugin_dir):
        """Test hot-reload functionality."""
        # Create initial handler
        handler_file = Path(temp_plugin_dir) / "reload_handler.py"
        handler_file.write_text("""
from deploy_response_handler import FormatHandler
from typing import Dict, Any, List

class ReloadHandler(FormatHandler):
    __version__ = "1.0"
    __source__ = "test"
    
    def normalize(self, raw: str) -> str:
        return raw
    
    def convert(self, data: Any, to_format: str) -> str:
        return str(data)
    
    def extract_sections(self, data: Any) -> Dict[str, str]:
        return {}
    
    def lint(self, data: Any) -> List[str]:
        return []
""")
        
        registry = HandlerRegistry(temp_plugin_dir)
        await asyncio.sleep(0.5)
        
        handler_v1 = registry.get_handler("reload")
        assert handler_v1.__version__ == "1.0"
        
        # Modify the handler
        handler_file.write_text(handler_file.read_text().replace('"1.0"', '"2.0"'))
        
        # Trigger reload
        registry.reload_plugins()
        
        handler_v2 = registry.get_handler("reload")
        assert handler_v2.__version__ == "2.0"


# ============================================================================
# INTEGRATION TESTS - LLM Repair
# ============================================================================

class TestLLMRepair:
    """Test suite for LLM-based configuration repair."""
    
    @pytest.mark.asyncio
    async def test_repair_missing_sections(self, mock_llm_generate_config):
        """Test repairing missing configuration sections."""
        current_data = ["RUN echo hello", "CMD echo world"]
        
        result = await repair_sections(
            ["FROM instruction"],
            current_data,
            "dockerfile"
        )
        
        assert "FROM" in result
        mock_llm_generate_config.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_repair_empty_response(self):
        """Test handling of empty LLM response."""
        with patch('deploy_response_handler.generate_config') as mock_gen:
            mock_gen.return_value = {"config": {"content": ""}}
            
            with pytest.raises(ValueError, match="LLM repair .* returned empty content"):
                await repair_sections(["test"], {}, "json")
    
    @pytest.mark.asyncio
    async def test_repair_invalid_format(self):
        """Test handling of invalid repair format."""
        with patch('deploy_response_handler.generate_config') as mock_gen:
            mock_gen.return_value = {
                "config": {"content": "invalid json {"}
            }
            
            with pytest.raises(ValueError, match="Invalid JSON format"):
                await repair_sections(["test"], {}, "json")


# ============================================================================
# INTEGRATION TESTS - Config Enrichment
# ============================================================================

class TestConfigEnrichment:
    """Test suite for configuration enrichment."""
    
    @pytest.mark.asyncio
    async def test_enrich_config_output(self, tmp_path):
        """Test configuration enrichment with badges and documentation."""
        # Create a git repo
        repo_path = tmp_path / "test_repo"
        repo_path.mkdir()
        
        with patch('deploy_response_handler.get_commits') as mock_commits:
            mock_commits.return_value = "abc123 Initial commit\ndef456 Add feature"
            
            result = await enrich_config_output(
                {"test": "data"},
                "json",
                "test-run-123",
                str(repo_path)
            )
            
            assert "![Compliance Status]" in result
            assert "## Configuration Diagram" in result
            assert "## Related Documentation" in result
            assert "## Recent Change Log" in result
            assert '{"test": "data"}' in result


# ============================================================================
# INTEGRATION TESTS - Quality Analysis
# ============================================================================

class TestQualityAnalysis:
    """Test suite for quality analysis."""
    
    def test_analyze_quality_dockerfile(self):
        """Test quality analysis for Dockerfile."""
        handler = DockerfileHandler()
        data = ["FROM alpine", "RUN apt-get update", "CMD sleep infinity"]
        
        result = analyze_quality(data, handler)
        
        assert "lint_issues" in result
        assert len(result["lint_issues"]) > 0
        assert "readability_score" in result
        assert 0 <= result["readability_score"] <= 1
        assert "compliance_score" in result
        assert result["compliance_score"] < 1.0  # Has issues
    
    def test_analyze_quality_clean_config(self):
        """Test quality analysis for clean configuration."""
        handler = YAMLHandler()
        data = {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": "test"},
            "spec": {"port": 80}
        }
        
        result = analyze_quality(data, handler)
        
        assert len(result["lint_issues"]) == 0
        assert result["compliance_score"] == 1.0


# ============================================================================
# END-TO-END TESTS
# ============================================================================

class TestEndToEnd:
    """End-to-end integration tests."""
    
    @pytest.mark.asyncio
    async def test_handle_deploy_response_dockerfile(self, sample_dockerfile):
        """Test complete processing of Dockerfile response."""
        with patch('deploy_response_handler.scan_config_for_findings') as mock_scan:
            mock_scan.return_value = []
            
            result = await handle_deploy_response(
                sample_dockerfile,
                "dockerfile",
                run_id="test-123"
            )
            
            assert "final_config_output" in result
            assert "structured_data" in result
            assert "provenance" in result
            
            assert isinstance(result["structured_data"], list)
            assert result["provenance"]["run_id"] == "test-123"
            assert result["provenance"]["handler_class"] == "DockerfileHandler"
    
    @pytest.mark.asyncio
    async def test_handle_deploy_response_with_conversion(self, sample_yaml):
        """Test processing with format conversion."""
        with patch('deploy_response_handler.scan_config_for_findings') as mock_scan:
            mock_scan.return_value = []
            
            result = await handle_deploy_response(
                sample_yaml,
                "yaml",
                to_format="json"
            )
            
            assert "apiVersion" in result["final_config_output"]
            assert result["provenance"]["converted_to_format"] == "json"
    
    @pytest.mark.asyncio
    async def test_handle_deploy_response_with_repair(self):
        """Test processing with automatic repair."""
        incomplete_dockerfile = "RUN echo hello\nCMD echo world"
        
        with patch('deploy_response_handler.repair_sections') as mock_repair:
            mock_repair.return_value = [
                "FROM alpine:latest",
                "RUN echo hello",
                "CMD echo world"
            ]
            
            result = await handle_deploy_response(
                incomplete_dockerfile,
                "dockerfile"
            )
            
            mock_repair.assert_called_once()
            assert result["provenance"]["initial_format"] == "dockerfile"
    
    @pytest.mark.asyncio
    async def test_handle_deploy_response_security_findings(self):
        """Test processing with security findings."""
        dangerous_config = """
        apiVersion: v1
        kind: Pod
        metadata:
          name: dangerous
        spec:
          containers:
          - name: app
            image: test
            securityContext:
              privileged: true
            env:
            - name: PASSWORD
              value: secretpass123
        """
        
        result = await handle_deploy_response(
            dangerous_config,
            "yaml"
        )
        
        findings = result["provenance"]["security_findings"]
        assert len(findings) > 0
        assert any(f["category"] == "PrivilegedContainer" for f in findings)


# ============================================================================
# PERFORMANCE TESTS
# ============================================================================

class TestPerformance:
    """Performance and scalability tests."""
    
    @pytest.mark.asyncio
    async def test_large_dockerfile_performance(self):
        """Test performance with large Dockerfile."""
        # Generate large Dockerfile
        lines = ["FROM alpine:latest"]
        for i in range(1000):
            lines.append(f"RUN echo 'Command {i}'")
        lines.append("CMD echo done")
        
        large_dockerfile = "\n".join(lines)
        
        start_time = time.time()
        
        with patch('deploy_response_handler.scan_config_for_findings') as mock_scan:
            mock_scan.return_value = []
            
            result = await handle_deploy_response(
                large_dockerfile,
                "dockerfile"
            )
        
        elapsed = time.time() - start_time
        
        assert elapsed < PERFORMANCE_THRESHOLD * 10  # Allow 10x threshold for large files
        assert len(result["structured_data"]) == 1001
    
    @pytest.mark.asyncio
    async def test_concurrent_processing(self):
        """Test concurrent processing of multiple configurations."""
        configs = [
            (ConfigFactory.create_dockerfile(), "dockerfile"),
            (ConfigFactory.create_yaml_with_issues(), "yaml"),
            (json.dumps({"test": i}), "json")
            for i in range(10)
        ]
        
        with patch('deploy_response_handler.scan_config_for_findings') as mock_scan:
            mock_scan.return_value = []
            
            tasks = [
                handle_deploy_response(content, fmt, run_id=f"test-{i}")
                for i, (content, fmt) in enumerate(configs[:10])
            ]
            
            start_time = time.time()
            results = await asyncio.gather(*tasks)
            elapsed = time.time() - start_time
            
            assert len(results) == 10
            assert all("final_config_output" in r for r in results)
            assert elapsed < PERFORMANCE_THRESHOLD * 15  # Should benefit from concurrency


# ============================================================================
# SECURITY TESTS
# ============================================================================

class TestSecurity:
    """Security-focused tests."""
    
    @pytest.mark.parametrize("sensitive_data,expected_redacted", [
        ("API_KEY=sk-1234567890abcdef", "[REDACTED]"),
        ("password: mysecretpass", "[REDACTED]"),
        ("email: user@example.com", "[REDACTED]"),
        ("SSN: 123-45-6789", "[REDACTED]"),
        ("card: 4111111111111111", "[REDACTED]"),
    ])
    def test_sensitive_data_scrubbing(self, sensitive_data, expected_redacted, mock_presidio):
        """Test scrubbing of various sensitive data types."""
        mock_presidio[1].anonymize.return_value = MagicMock(
            text=sensitive_data.replace(
                sensitive_data.split(":")[1].strip() if ":" in sensitive_data else sensitive_data,
                expected_redacted
            )
        )
        
        result = scrub_text(sensitive_data)
        assert expected_redacted in result
    
    @pytest.mark.asyncio
    async def test_dangerous_config_detection(self):
        """Test detection of all dangerous configuration patterns."""
        for pattern_name, pattern_regex in DANGEROUS_CONFIG_PATTERNS.items():
            # Create config that matches the pattern
            if pattern_name == "PrivilegedContainer":
                config = "privileged: true"
            elif pattern_name == "HostPathMount":
                config = "hostPath: /etc/passwd"
            elif pattern_name == "RootUserInDockerfile":
                config = "USER root"
            elif pattern_name == "ExposeAllPorts":
                config = "EXPOSE 1-65535"
            elif pattern_name == "NoResourceLimits":
                config = "resources: {}"
            else:
                config = "password: test123"
            
            findings = await scan_config_for_findings(config, "yaml")
            
            assert len(findings) > 0
            assert any(f["category"] == pattern_name for f in findings)


# ============================================================================
# ERROR HANDLING TESTS
# ============================================================================

class TestErrorHandling:
    """Test error handling and edge cases."""
    
    @pytest.mark.asyncio
    async def test_handle_invalid_format(self):
        """Test handling of invalid configuration format."""
        with pytest.raises(ValueError, match="Invalid JSON format"):
            await handle_deploy_response(
                "{invalid json",
                "json"
            )
    
    @pytest.mark.asyncio
    async def test_handle_missing_handler(self):
        """Test handling of missing format handler."""
        with pytest.raises(ValueError, match="No handler found"):
            await handle_deploy_response(
                "some content",
                "unsupported_format"
            )
    
    @pytest.mark.asyncio
    async def test_handle_empty_response(self):
        """Test handling of empty response."""
        with pytest.raises(ValueError):
            await handle_deploy_response(
                "",
                "dockerfile"
            )


# ============================================================================
# HYPOTHESIS PROPERTY-BASED TESTS
# ============================================================================

class TestPropertyBased:
    """Property-based tests using Hypothesis."""
    
    @given(st.lists(
        st.text(min_size=1, max_size=100),
        min_size=1,
        max_size=50
    ))
    def test_dockerfile_handler_normalize_inverse(self, lines):
        """Test that Dockerfile normalization is consistent."""
        handler = DockerfileHandler()
        dockerfile = "\n".join(lines)
        
        try:
            normalized = handler.normalize(dockerfile)
            # Should be able to convert back
            converted = handler.convert(normalized, "dockerfile")
            # Re-normalize should give same result
            renormalized = handler.normalize(converted)
            assert renormalized == normalized
        except ValueError:
            # Invalid format is acceptable
            pass
    
    @given(st.dictionaries(
        st.text(min_size=1, max_size=20),
        st.recursive(
            st.one_of(
                st.text(),
                st.integers(),
                st.booleans(),
                st.none()
            ),
            lambda children: st.lists(children) | st.dictionaries(
                st.text(min_size=1, max_size=10),
                children
            ),
            max_leaves=10
        ),
        min_size=1,
        max_size=20
    ))
    def test_yaml_json_conversion_preserves_structure(self, data):
        """Test that YAML/JSON conversion preserves data structure."""
        yaml_handler = YAMLHandler()
        json_handler = JSONHandler()
        
        # Convert to YAML then JSON
        yaml_str = yaml.dump(data)
        yaml_data = yaml_handler.normalize(yaml_str)
        json_str = yaml_handler.convert(yaml_data, "json")
        json_data = json_handler.normalize(json_str)
        
        # Data should be preserved
        assert json_data == data


# ============================================================================
# REGRESSION TESTS
# ============================================================================

class TestRegression:
    """Regression tests for specific bug fixes."""
    
    @pytest.mark.asyncio
    async def test_multiline_run_commands(self):
        """Test handling of multi-line RUN commands in Dockerfile."""
        dockerfile = """
FROM alpine
RUN apk add --no-cache \\
    python3 \\
    py3-pip \\
    && pip install flask
CMD ["python3", "app.py"]
"""
        
        handler = DockerfileHandler()
        normalized = handler.normalize(dockerfile)
        sections = handler.extract_sections(normalized)
        
        assert "RUN_commands" in sections
        assert "apk add" in sections["RUN_commands"]
        assert "pip install" in sections["RUN_commands"]
    
    @pytest.mark.asyncio
    async def test_unicode_handling(self):
        """Test handling of Unicode characters in configurations."""
        config = {
            "name": "测试配置",
            "description": "Configuration with émojis 🚀",
            "settings": {"locale": "zh_CN.UTF-8"}
        }
        
        handler = JSONHandler()
        json_str = json.dumps(config, ensure_ascii=False)
        normalized = handler.normalize(json_str)
        
        assert normalized["name"] == "测试配置"
        assert "🚀" in normalized["description"]


# ============================================================================
# MOCK MANAGEMENT TESTS
# ============================================================================

class TestMockManagement:
    """Test proper mock setup and teardown."""
    
    @pytest.mark.asyncio
    async def test_mock_isolation(self):
        """Test that mocks are properly isolated between tests."""
        with patch('deploy_response_handler.generate_config') as mock1:
            mock1.return_value = {"config": {"content": "test1"}}
            
            # First call
            from deploy_response_handler import generate_config
            result1 = await generate_config("prompt1", "model1")
            
        with patch('deploy_response_handler.generate_config') as mock2:
            mock2.return_value = {"config": {"content": "test2"}}
            
            # Second call with different mock
            result2 = await generate_config("prompt2", "model2")
            
        # Mocks should be independent
        assert mock1.call_count == 1
        assert mock2.call_count == 1


if __name__ == "__main__":
    # Run tests with coverage
    pytest.main([
        __file__,
        "-v",
        "--cov=deploy_response_handler",
        "--cov-report=term-missing",
        "--cov-report=html",
        "--asyncio-mode=auto",
        "-W", "ignore::DeprecationWarning",
        "--tb=short"
    ])
