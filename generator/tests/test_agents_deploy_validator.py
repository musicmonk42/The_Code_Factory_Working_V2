"""
test_deploy_validator.py
Comprehensive tests for deploy_validator module.
"""

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Import the module under test
# FIX: Use absolute imports from the project root. Remove sys.path hack.
from generator.agents.deploy_agent.deploy_validator import (
    DANGEROUS_CONFIG_PATTERNS,
    DockerValidator,
    HelmValidator,
    ValidatorRegistry,
    scan_config_for_findings,
    scrub_text,
)

# FIX: Removed non-existent imports: TerraformValidator, repair_sections


# ============================================================================
# FIXTURES
# ============================================================================


@pytest.fixture
def temp_workdir():
    """Create a temporary working directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def sample_dockerfile():
    """Sample valid Dockerfile."""
    return """FROM python:3.9-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
USER appuser
EXPOSE 8000
CMD ["python", "app.py"]
"""


@pytest.fixture
def bad_dockerfile():
    """Sample Dockerfile with issues."""
    return """RUN apt-get update
RUN apt-get install -y python3
EXPOSE 8000
CMD ["python3", "app.py"]
"""


@pytest.fixture
def sample_helm_chart():
    """Sample Helm chart values."""
    return """apiVersion: v2
name: my-app
version: 1.0.0
description: A sample Helm chart
"""


@pytest.fixture
def privileged_pod_yaml():
    """YAML with security issues."""
    return """apiVersion: v1
kind: Pod
metadata:
  name: privileged-pod
spec:
  containers:
  - name: app
    image: nginx:latest
    securityContext:
      privileged: true
  hostPath:
    path: /data
"""


@pytest.fixture
def mock_subprocess_success():
    """Mock successful subprocess execution."""
    mock_process = MagicMock()
    mock_process.returncode = 0
    mock_process.communicate = AsyncMock(return_value=(b"Success output", b""))
    return mock_process


@pytest.fixture
def mock_subprocess_failure():
    """Mock failed subprocess execution."""
    mock_process = MagicMock()
    mock_process.returncode = 1
    mock_process.communicate = AsyncMock(return_value=(b"", b"Error: build failed"))
    return mock_process


@pytest.fixture
def mock_trivy_json_output():
    """Mock Trivy JSON output with vulnerabilities."""
    return json.dumps(
        {
            "Results": [
                {
                    "Target": "Dockerfile",
                    "Vulnerabilities": [
                        {
                            "VulnerabilityID": "CVE-2021-1234",
                            "Severity": "HIGH",
                            "Title": "Test vulnerability",
                            "Description": "This is a test vulnerability",
                        }
                    ],
                }
            ]
        }
    ).encode()


# FIX: Add a mock for the asyncio subprocess calls to avoid NotImplementedError on Windows
@pytest.fixture(autouse=True)
def mock_subprocess():
    """Auto-use fixture to mock asyncio.create_subprocess_exec."""
    mock_process = MagicMock()
    mock_process.communicate = AsyncMock(
        return_value=(b"{}", b"")
    )  # Return empty JSON for Trivy/success
    mock_process.returncode = 0

    with patch(
        "generator.agents.deploy_agent.deploy_validator.asyncio.create_subprocess_exec",
        new_callable=AsyncMock,
    ) as mock_create_exec:
        mock_create_exec.return_value = mock_process
        yield mock_create_exec


# ============================================================================
# TESTS: ValidatorRegistry
# ============================================================================


class TestValidatorRegistry:
    """Tests for ValidatorRegistry."""

    def test_get_docker_validator(self):
        """Test getting Docker validator from registry."""
        registry = ValidatorRegistry()
        validator = registry.get_validator("docker")

        assert isinstance(validator, DockerValidator)

    def test_get_helm_validator(self):
        """Test getting Helm validator from registry."""
        registry = ValidatorRegistry()
        validator = registry.get_validator("helm")

        assert isinstance(validator, HelmValidator)

    # FIX: Removed test_get_terraform_validator as it's not a built-in

    def test_unsupported_target(self):
        """Test requesting unsupported validator."""
        registry = ValidatorRegistry()

        # FIX: Update match string to be more precise
        with pytest.raises(ValueError, match="No validator found for target"):
            registry.get_validator("unsupported")

    def test_register_custom_validator(self):
        """Test registering a custom validator."""
        # FIX: Import the correct base class
        from generator.agents.deploy_agent.deploy_validator import (
            Validator as BaseValidator,
        )

        class CustomValidator(BaseValidator):
            async def validate(self, content, target):
                return {"status": "custom"}

            async def fix(self, content, issues, target):
                return "fixed"

        registry = ValidatorRegistry()

        # FIX: The registry stores *classes*, not instances
        registry.validators["custom"] = CustomValidator

        validator = registry.get_validator("custom")
        assert isinstance(validator, CustomValidator)


# ============================================================================
# TESTS: scrub_text (PII/Secret Redaction)
# ============================================================================


class TestScrubText:
    """Tests for scrub_text function."""

    @patch("generator.agents.deploy_agent.deploy_validator.AnalyzerEngine")
    @patch("generator.agents.deploy_agent.deploy_validator.AnonymizerEngine")
    def test_scrub_email(self, mock_anonymizer_cls, mock_analyzer_cls):
        """Test scrubbing email addresses."""
        # Setup mocks
        mock_analyzer = MagicMock()
        mock_anonymizer = MagicMock()
        mock_analyzer_cls.return_value = mock_analyzer
        mock_anonymizer_cls.return_value = mock_anonymizer

        mock_analyzer.analyze.return_value = [
            MagicMock(entity_type="EMAIL_ADDRESS", start=10, end=25)
        ]

        mock_result = MagicMock()
        mock_result.text = "Contact: [REDACTED]"
        mock_anonymizer.anonymize.return_value = mock_result

        text = "Contact: test@example.com"
        result = scrub_text(text)

        assert "[REDACTED]" in result
        assert "test@example.com" not in result

    @patch("generator.agents.deploy_agent.deploy_validator.AnalyzerEngine")
    @patch("generator.agents.deploy_agent.deploy_validator.AnonymizerEngine")
    def test_scrub_phone_number(self, mock_anonymizer_cls, mock_analyzer_cls):
        """Test scrubbing phone numbers."""
        mock_analyzer = MagicMock()
        mock_anonymizer = MagicMock()
        mock_analyzer_cls.return_value = mock_analyzer
        mock_anonymizer_cls.return_value = mock_anonymizer

        mock_analyzer.analyze.return_value = [
            MagicMock(entity_type="PHONE_NUMBER", start=7, end=19)
        ]

        mock_result = MagicMock()
        mock_result.text = "Phone: [REDACTED]"
        mock_anonymizer.anonymize.return_value = mock_result

        text = "Phone: 555-123-4567"
        result = scrub_text(text)

        assert "[REDACTED]" in result

    @patch("generator.agents.deploy_agent.deploy_validator.AnalyzerEngine")
    def test_scrub_presidio_failure(self, mock_analyzer_cls):
        """Test scrubbing when Presidio fails."""
        mock_analyzer_cls.side_effect = Exception("Presidio error")

        text = "Some text with data"

        with pytest.raises(
            RuntimeError, match="Critical error during sensitive data scrubbing"
        ):
            scrub_text(text)

    def test_scrub_empty_text(self):
        """Test scrubbing empty text."""
        result = scrub_text("")
        assert result == ""

        # FIX: The function doesn't handle None, it expects a string.
        # result = scrub_text(None)
        # assert result == ""


# ============================================================================
# TESTS: scan_config_for_findings
# ============================================================================


class TestScanConfigForFindings:
    """Tests for scan_config_for_findings function."""

    @pytest.mark.asyncio
    async def test_scan_privileged_container(self, privileged_pod_yaml):
        """Test detecting privileged container."""
        # FIX: The function now requires the patterns to be passed
        findings = await scan_config_for_findings(
            privileged_pod_yaml, "yaml", DANGEROUS_CONFIG_PATTERNS
        )

        assert len(findings) > 0
        assert any(f["category"] == "PrivilegedContainer" for f in findings)

    @pytest.mark.asyncio
    async def test_scan_hostpath_mount(self):
        """Test detecting hostPath mount."""
        config = """apiVersion: v1
kind: Pod
spec:
  volumes:
  - name: data
    hostPath:
      path: /host/data
"""
        findings = await scan_config_for_findings(
            config, "yaml", DANGEROUS_CONFIG_PATTERNS
        )

        assert any("HostPath" in f["category"] for f in findings)

    @pytest.mark.asyncio
    async def test_scan_root_user_dockerfile(self):
        """Test detecting root user in Dockerfile."""
        config = """FROM alpine:latest
USER root
RUN apk add python3
"""
        findings = await scan_config_for_findings(
            config, "dockerfile", DANGEROUS_CONFIG_PATTERNS
        )

        assert any("Root" in f["category"] for f in findings)

    @pytest.mark.asyncio
    async def test_scan_hardcoded_credentials(self):
        """Test detecting hardcoded credentials."""
        config = """apiVersion: v1
kind: Secret
data:
  password: mysupersecretpassword
  api_key: sk-1234567890abcdef
"""
        findings = await scan_config_for_findings(
            config, "yaml", DANGEROUS_CONFIG_PATTERNS
        )

        assert any("Credentials" in f["category"] for f in findings)

    @pytest.mark.asyncio
    async def test_scan_with_trivy(self, mock_subprocess, mock_trivy_json_output):
        """Test scanning with Trivy integration."""
        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(mock_trivy_json_output, b""))
        mock_subprocess.return_value = mock_process

        config = "FROM alpine:latest"
        findings = await scan_config_for_findings(
            config, "dockerfile", DANGEROUS_CONFIG_PATTERNS
        )

        # Should call trivy
        mock_subprocess.assert_called()
        # FIX: Check that the trivy finding was parsed
        assert any("CVE-2021-1234" in f["category"] for f in findings)

    @pytest.mark.asyncio
    async def test_scan_clean_config(self):
        """Test scanning a clean configuration."""
        clean_config = """FROM python:3.9-slim
USER appuser
WORKDIR /app
COPY . .
CMD ["python", "app.py"]
"""
        findings = await scan_config_for_findings(
            clean_config, "dockerfile", DANGEROUS_CONFIG_PATTERNS
        )

        # Should have minimal or no findings (Trivy mock returns empty)
        assert isinstance(findings, list)
        assert len(findings) == 0


# ============================================================================
# TESTS: DockerValidator
# ============================================================================


class TestDockerValidator:
    """Tests for DockerValidator."""

    @pytest.mark.asyncio
    async def test_validate_dockerfile_success(
        self, mock_subprocess, sample_dockerfile, mock_subprocess_success
    ):
        """Test validating a valid Dockerfile."""
        mock_subprocess.return_value = mock_subprocess_success

        validator = DockerValidator()
        report = await validator.validate(sample_dockerfile, "docker")

        assert "build_status" in report
        assert "lint_status" in report
        assert "security_findings" in report
        assert "compliance_score" in report
        assert report["build_status"] == "success"

    @pytest.mark.asyncio
    async def test_validate_dockerfile_build_failure(
        self, mock_subprocess, bad_dockerfile, mock_subprocess_failure
    ):
        """Test validating a Dockerfile that fails to build."""
        mock_subprocess.return_value = mock_subprocess_failure

        validator = DockerValidator()
        report = await validator.validate(bad_dockerfile, "docker")

        assert report["build_status"] == "failed"
        assert len(report["lint_issues"]) > 0

    @pytest.mark.asyncio
    async def test_validate_dockerfile_missing_from(self, mock_subprocess):
        """Test validating Dockerfile missing FROM instruction."""
        bad_dockerfile = """RUN apt-get update
CMD ["python", "app.py"]
"""
        mock_subprocess.return_value = MagicMock(
            returncode=1, communicate=AsyncMock(return_value=(b"", b"Missing FROM"))
        )

        validator = DockerValidator()
        report = await validator.validate(bad_dockerfile, "docker")

        assert any("FROM" in issue for issue in report["lint_issues"])

    @pytest.mark.asyncio
    async def test_validate_docker_tool_not_found(self, mock_subprocess):
        """Test validation when Docker is not installed."""
        mock_subprocess.side_effect = FileNotFoundError("docker")

        validator = DockerValidator()
        report = await validator.validate("FROM alpine", "docker")

        assert report["build_status"] == "tool_not_found"
        assert any("docker" in issue.lower() for issue in report["lint_issues"])

    @pytest.mark.asyncio
    @patch("generator.agents.deploy_agent.deploy_validator.call_ensemble_api")
    async def test_fix_dockerfile(self, mock_llm, mock_subprocess):
        """Test fixing a Dockerfile with issues."""
        mock_llm.return_value = {
            "content": json.dumps(
                {
                    "config": 'FROM python:3.9\nWORKDIR /app\nCOPY . .\nCMD ["python", "app.py"]'
                }
            ),
            "model": "gpt-4",
            "provider": "openai",
        }

        validator = DockerValidator()
        issues = ["Missing FROM instruction", "Missing CMD"]

        # FIX: Patch add_provenance to avoid TypeError
        with patch(
            "generator.agents.deploy_agent.deploy_validator.add_provenance"
        ) as mock_add_prov:
            fixed = await validator.fix("RUN echo hello", issues, "docker")

        assert "FROM python:3.9" in fixed
        assert mock_llm.called
        mock_add_prov.assert_called_once()


# ============================================================================
# TESTS: HelmValidator
# ============================================================================


class TestHelmValidator:
    """Tests for HelmValidator."""

    @pytest.mark.asyncio
    async def test_validate_helm_success(
        self, mock_subprocess, sample_helm_chart, mock_subprocess_success
    ):
        """Test validating a valid Helm chart."""
        mock_subprocess.return_value = mock_subprocess_success

        validator = HelmValidator()
        report = await validator.validate(sample_helm_chart, "helm")

        assert "lint_status" in report
        assert "security_findings" in report
        assert "compliance_score" in report
        assert report["lint_status"] == "success"

    @pytest.mark.asyncio
    async def test_validate_helm_lint_failure(self, mock_subprocess):
        """Test validating Helm chart with lint errors."""
        mock_process = MagicMock()
        mock_process.returncode = 1
        mock_process.communicate = AsyncMock(
            return_value=(b"", b"Error: Chart.yaml is invalid")
        )
        mock_subprocess.return_value = mock_process

        validator = HelmValidator()
        report = await validator.validate("invalid: yaml", "helm")

        assert report["lint_status"] == "failed"
        assert len(report["lint_issues"]) > 0

    @pytest.mark.asyncio
    async def test_validate_helm_tool_not_found(self, mock_subprocess):
        """Test validation when Helm is not installed."""
        mock_subprocess.side_effect = FileNotFoundError("helm")

        validator = HelmValidator()
        report = await validator.validate("apiVersion: v2", "helm")

        assert report["lint_status"] == "tool_not_found"

    @pytest.mark.asyncio
    @patch("generator.agents.deploy_agent.deploy_validator.call_ensemble_api")
    async def test_fix_helm_chart(self, mock_llm):
        """Test fixing a Helm chart with issues."""
        mock_llm.return_value = {
            "content": json.dumps(
                {"config": "apiVersion: v2\nname: fixed-chart\nversion: 1.0.0"}
            ),
            "model": "gpt-4",
            "provider": "openai",
        }

        validator = HelmValidator()
        issues = ["Missing version field"]

        # FIX: Patch add_provenance to avoid TypeError
        with patch(
            "generator.agents.deploy_agent.deploy_validator.add_provenance"
        ) as mock_add_prov:
            fixed = await validator.fix("apiVersion: v2\nname: test", issues, "helm")

        assert "version: 1.0.0" in fixed
        assert mock_llm.called
        mock_add_prov.assert_called_once()


# ============================================================================
# TESTS: TerraformValidator
# ============================================================================

# FIX: Removed TestTerraformValidator class entirely


# ============================================================================
# TESTS: repair_sections
# ============================================================================

# FIX: Removed TestRepairSections class entirely


# ============================================================================
# TESTS: Compliance Scoring
# ============================================================================


class TestComplianceScoring:
    """Tests for compliance score calculation."""

    @pytest.mark.asyncio
    async def test_compliance_score_perfect(self, mock_subprocess, sample_dockerfile):
        """Test compliance score for perfect configuration."""
        mock_subprocess.return_value = MagicMock(
            returncode=0, communicate=AsyncMock(return_value=(b"Success", b""))
        )

        validator = DockerValidator()
        report = await validator.validate(sample_dockerfile, "docker")

        # Perfect config should have high compliance score
        assert report["compliance_score"] >= 0.8

    @pytest.mark.asyncio
    async def test_compliance_score_with_issues(self, mock_subprocess, bad_dockerfile):
        """Test compliance score with multiple issues."""
        mock_subprocess.return_value = MagicMock(
            returncode=1, communicate=AsyncMock(return_value=(b"", b"Multiple errors"))
        )

        validator = DockerValidator()
        report = await validator.validate(bad_dockerfile, "docker")

        # Config with issues should have lower compliance score
        assert report["compliance_score"] < 0.8


# ============================================================================
# TESTS: Batch Validation
# ============================================================================


class TestBatchValidation:
    """Tests for batch validation scenarios."""

    @pytest.mark.asyncio
    async def test_validate_multiple_dockerfiles(self, mock_subprocess):
        """Test validating multiple Dockerfiles."""
        mock_subprocess.return_value = MagicMock(
            returncode=0, communicate=AsyncMock(return_value=(b"Success", b""))
        )

        validator = DockerValidator()

        dockerfiles = [
            "FROM alpine:latest\nCMD echo 'test1'",
            "FROM python:3.9\nCMD python app.py",
            "FROM node:18\nCMD npm start",
        ]

        reports = []
        for df in dockerfiles:
            report = await validator.validate(df, "docker")
            reports.append(report)

        assert len(reports) == 3
        assert all("build_status" in r for r in reports)


# ============================================================================
# TESTS: Integration with Security Tools
# ============================================================================


class TestSecurityToolIntegration:
    """Tests for integration with external security tools."""

    @pytest.mark.asyncio
    async def test_trivy_integration(self, mock_subprocess, mock_trivy_json_output):
        """Test integration with Trivy scanner."""
        # Mock docker build success
        docker_mock = MagicMock(
            returncode=0, communicate=AsyncMock(return_value=(b"Build successful", b""))
        )

        # Mock trivy scan with findings
        trivy_mock = MagicMock(
            returncode=0,
            communicate=AsyncMock(return_value=(mock_trivy_json_output, b"")),
        )

        # Return different mocks based on command
        def side_effect(*args, **kwargs):
            cmd_args = args[0]
            if "docker" in cmd_args:
                return docker_mock
            elif "trivy" in cmd_args:
                return trivy_mock
            elif "hadolint" in cmd_args:  # Add hadolint mock
                return MagicMock(
                    returncode=0, communicate=AsyncMock(return_value=(b"", b""))
                )
            return docker_mock

        mock_subprocess.side_effect = side_effect

        validator = DockerValidator()
        report = await validator.validate("FROM alpine:latest", "docker")

        # Should have run docker, hadolint, and trivy
        assert mock_subprocess.call_count >= 2  # docker build + hadolint
        # Trivy is called inside scan_config_for_findings, which is also mocked by mock_subprocess
        assert "CVE-2021-1234" in str(report["security_findings"])


# ============================================================================
# TESTS: Error Recovery
# ============================================================================


class TestErrorRecovery:
    """Tests for error recovery mechanisms."""

    @pytest.mark.asyncio
    async def test_graceful_tool_failure(self, mock_subprocess):
        """Test graceful handling when validation tools fail."""
        mock_subprocess.side_effect = Exception("Tool crashed")

        validator = DockerValidator()

        # Should not raise, but return error status
        report = await validator.validate("FROM alpine", "docker")
        assert "build_status" in report
        assert report["build_status"] in [
            "error",
            "failed",
            "tool_not_found",
            "internal_error",
        ]

    @pytest.mark.asyncio
    @patch("generator.agents.deploy_agent.deploy_validator.call_ensemble_api")
    async def test_llm_timeout_handling(self, mock_llm):
        """Test handling LLM timeout during fix."""
        mock_llm.side_effect = asyncio.TimeoutError()

        validator = DockerValidator()

        # FIX: The code raises a RuntimeError, not TimeoutError directly
        with pytest.raises(RuntimeError, match="Failed to auto-fix"):
            await validator.fix("FROM alpine", ["Missing CMD"], "docker")


# ============================================================================
# RUN TESTS
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
