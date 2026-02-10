# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
test_deploy_response_handler.py
Comprehensive tests for deploy_response_handler module.
"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

# FIX: Use absolute imports from the project root. Remove sys.path hack.
from generator.agents.deploy_agent.deploy_response_handler import (
    enrich_config_output,  # FIX: Renamed from enrich_config_with_badges
)
from generator.agents.deploy_agent.deploy_response_handler import (
    ERROR_FILENAME,
    DockerfileHandler,
    HandlerRegistry,
    JSONHandler,
    YAMLHandler,
    handle_deploy_response,
    parse_llm_response,
    repair_sections,
    scan_config_for_findings,
)

# FIX: Removed imports for non-existent functions: summarize_section, enrich_config_with_badges

# ============================================================================
# FIXTURES
# ============================================================================


@pytest.fixture
def temp_repo():
    """Create a temporary repository for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir)
        (repo_path / ".git").mkdir()
        (repo_path / "README.md").write_text("# Test Repo")
        (repo_path / "main.py").write_text("print('hello')")
        yield repo_path


@pytest.fixture
def mock_llm_response():
    """Mock LLM API response."""
    return {
        "content": 'FROM node:18\nWORKDIR /app\nCOPY . .\nRUN npm install\nCMD ["npm", "start"]',
        "model": "gpt-4",
        "provider": "openai",
        "tokens": 50,
    }


@pytest.fixture
def mock_ensemble_response():
    """Mock ensemble API response."""

    def _response(content):
        return {
            "content": content,
            "model": "claude-3",
            "provider": "anthropic",
            "valid": True,
        }

    return _response


@pytest.fixture
def sample_dockerfile():
    """Sample Dockerfile content."""
    return """FROM python:3.9-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
USER appuser
CMD ["python", "app.py"]
"""


@pytest.fixture
def sample_yaml():
    """Sample YAML configuration."""
    return """apiVersion: v1
kind: Service
metadata:
  name: my-service
spec:
  selector:
    app: MyApp
  ports:
    - protocol: TCP
      port: 80
      targetPort: 9376
"""


@pytest.fixture
def sample_json():
    """Sample JSON configuration."""
    return json.dumps(
        {
            "name": "test-config",
            "version": "1.0.0",
            "settings": {"enabled": True, "timeout": 30},
        }
    )


# FIX: Add a registry fixture to provide to handler functions
@pytest.fixture
def registry():
    """Fixture to create a HandlerRegistry with watchers patched."""
    # Patch observer to prevent file system watchers from starting during tests
    with (
        patch("watchdog.observers.Observer"),
        patch("os.path.exists", return_value=True),
        patch("os.makedirs"),
    ):
        reg = HandlerRegistry()
        yield reg


# FIX: Add a dummy patterns dict for security scanning tests
@pytest.fixture
def dangerous_patterns():
    """Dummy patterns for scan_config_for_findings."""
    return {
        "PrivilegedContainer": r"(?i)privileged:\s*true",
        "HostPathMount": r"(?i)hostpath:\s*.*",
        "RootUserInDockerfile": r"(?i)^USER\s+root",
        "HardcodedCredentials_Pattern": r"(?i)password:\s*\S+|secret:\s*\S+|api_key:\s*\S+",
    }


# ============================================================================
# TESTS: parse_llm_response
# ============================================================================


class TestParseLLMResponse:
    """Tests for parse_llm_response function."""

    def test_parse_single_file_raw(self):
        """Test parsing a single raw text response."""
        response = "FROM alpine:latest\nCMD echo 'hello'"
        result = parse_llm_response(response, lang="raw")

        # FIX: Check for 'response.txt' as per module code
        assert "response.txt" in result
        assert result["response.txt"] == response

    def test_parse_multi_file_json(self):
        """Test parsing multi-file JSON response."""
        response = json.dumps(
            {
                "Dockerfile": "FROM node:18\nWORKDIR /app",
                "docker-compose.yml": "version: '3'\nservices:\n  app:\n    build: .",
            }
        )

        result = parse_llm_response(response, lang="raw")

        assert "Dockerfile" in result
        assert "docker-compose.yml" in result
        assert "FROM node:18" in result["Dockerfile"]

    def test_parse_python_syntax_valid(self):
        """Test parsing valid Python code."""
        response = json.dumps(
            {
                "main.py": "def hello():\n    print('world')\n\nif __name__ == '__main__':\n    hello()"
            }
        )

        result = parse_llm_response(response, lang="python")

        assert "main.py" in result
        assert ERROR_FILENAME not in result

    def test_parse_python_syntax_invalid(self):
        """Test parsing invalid Python code."""
        response = json.dumps(
            {"bad.py": "def hello(\n    print('missing parenthesis')"}
        )

        result = parse_llm_response(response, lang="python")

        assert ERROR_FILENAME in result
        assert "bad.py" in result[ERROR_FILENAME]
        assert "Invalid Python syntax" in result[ERROR_FILENAME]

    def test_parse_invalid_json(self):
        """Test parsing response that's not valid JSON."""
        response = "{not valid json"

        result = parse_llm_response(response, lang="raw")

        # FIX: Check for 'response.txt' as per module code
        assert "response.txt" in result
        assert result["response.txt"] == response


# ============================================================================
# TESTS: HandlerRegistry and Format Handlers
# ============================================================================


class TestHandlerRegistry:
    """Tests for HandlerRegistry."""

    def test_registry_get_dockerfile_handler(
        self, registry
    ):  # FIX: Use registry fixture
        """Test getting Dockerfile handler."""
        handler = registry.get_handler("dockerfile")
        assert isinstance(handler, DockerfileHandler)

    def test_registry_get_yaml_handler(self, registry):  # FIX: Use registry fixture
        """Test getting YAML handler."""
        handler = registry.get_handler("yaml")
        assert isinstance(handler, YAMLHandler)

    def test_registry_get_json_handler(self, registry):  # FIX: Use registry fixture
        """Test getting JSON handler."""
        handler = registry.get_handler("json")
        assert isinstance(handler, JSONHandler)

    def test_registry_unsupported_format(self, registry):  # FIX: Use registry fixture
        """Test getting handler for unsupported format."""
        # FIX: Update match string to match module's error
        with pytest.raises(
            ValueError, match="No handler found for output format 'unsupported_format'"
        ):
            registry.get_handler("unsupported_format")


class TestDockerfileHandler:
    """Tests for DockerfileHandler."""

    def test_normalize_valid_dockerfile(self, sample_dockerfile):
        """Test normalizing a valid Dockerfile."""
        handler = DockerfileHandler()
        result = handler.normalize(sample_dockerfile)

        assert isinstance(result, list)
        assert "FROM python:3.9-slim" in result
        assert "WORKDIR /app" in result

    def test_extract_sections(self, sample_dockerfile):
        """Test extracting sections from Dockerfile."""
        handler = DockerfileHandler()
        normalized = handler.normalize(sample_dockerfile)
        sections = handler.extract_sections(normalized)

        # FIX: Check for correct keys based on module code
        assert "FROM" in sections
        assert "RUN_commands" in sections
        assert "CMD" in sections
        assert "FROM python:3.9-slim" in sections["FROM"]

    def test_convert_to_string(self, sample_dockerfile):
        """Test converting Dockerfile back to string."""
        handler = DockerfileHandler()
        normalized = handler.normalize(sample_dockerfile)
        # FIX: Convert to 'dockerfile', not 'string'
        output = handler.convert(normalized, "dockerfile")

        assert "FROM python:3.9-slim" in output
        assert isinstance(output, str)

    def test_validate_dockerfile_with_invalid_start(self):
        """Test that Dockerfile with invalid start character is rejected."""
        handler = DockerfileHandler()
        invalid_dockerfile = "! Invalid start\nFROM python:3.9"
        
        # The normalize method should raise ValueError due to validate_dockerfile
        with pytest.raises(ValueError, match="Invalid Dockerfile: First instruction must be FROM or ARG"):
            handler.normalize(invalid_dockerfile)

    def test_validate_dockerfile_with_valid_from(self):
        """Test that Dockerfile starting with FROM is accepted."""
        handler = DockerfileHandler()
        valid_dockerfile = "FROM python:3.9\nWORKDIR /app"
        
        result = handler.normalize(valid_dockerfile)
        assert isinstance(result, list)
        assert "FROM python:3.9" in result

    def test_validate_dockerfile_with_valid_arg(self):
        """Test that Dockerfile starting with ARG is accepted."""
        handler = DockerfileHandler()
        valid_dockerfile = "ARG BASE_IMAGE=python:3.9\nFROM ${BASE_IMAGE}\nWORKDIR /app"
        
        result = handler.normalize(valid_dockerfile)
        assert isinstance(result, list)
        assert "ARG BASE_IMAGE=python:3.9" in result


class TestYAMLHandler:
    """Tests for YAMLHandler."""

    def test_normalize_valid_yaml(self, sample_yaml):
        """Test normalizing valid YAML."""
        handler = YAMLHandler()
        result = handler.normalize(sample_yaml)

        assert isinstance(result, dict)
        assert result["apiVersion"] == "v1"
        assert result["kind"] == "Service"

    def test_normalize_invalid_yaml(self):
        """Test normalizing invalid YAML."""
        handler = YAMLHandler()
        invalid_yaml = "key: value\n  bad: indentation"

        # FIX: Check for specific ValueError
        with pytest.raises(ValueError, match="Invalid YAML format"):
            handler.normalize(invalid_yaml)

    def test_extract_sections(self, sample_yaml):
        """Test extracting sections from YAML."""
        handler = YAMLHandler()
        normalized = handler.normalize(sample_yaml)
        sections = handler.extract_sections(normalized)

        assert "metadata" in sections
        assert "spec" in sections
        assert '"name": "my-service"' in sections["metadata"]

    def test_convert_to_yaml(self, sample_yaml):
        """Test converting dict back to YAML."""
        handler = YAMLHandler()
        normalized = handler.normalize(sample_yaml)
        output = handler.convert(normalized, "yaml")

        assert "apiVersion: v1" in output
        assert "kind: Service" in output

    def test_normalize_yaml_with_markdown_formatting(self):
        """Test that YAML with markdown formatting is rejected."""
        handler = YAMLHandler()
        yaml_with_markdown = """apiVersion: v1
kind: Service
metadata:
  name: my-service
  annotations:
    description: "- **Purpose:** Provide default configuration"
"""
        
        # Should raise ValueError due to markdown pattern detection
        with pytest.raises(ValueError, match="Invalid output: Response contains Markdown formatting"):
            handler.normalize(yaml_with_markdown)

    def test_normalize_yaml_with_markdown_bold(self):
        """Test that YAML containing ** (bold markdown) is rejected."""
        handler = YAMLHandler()
        yaml_with_bold = """apiVersion: v1
kind: Deployment
**metadata**:
  name: test
"""
        
        with pytest.raises(ValueError, match="Invalid output: Response contains Markdown formatting"):
            handler.normalize(yaml_with_bold)

    def test_normalize_yaml_strips_code_fences(self):
        """Test that YAML with markdown code fences is stripped."""
        handler = YAMLHandler()
        yaml_with_fences = """```yaml
apiVersion: v1
kind: Service
metadata:
  name: my-service
```"""
        
        result = handler.normalize(yaml_with_fences)
        assert isinstance(result, dict)
        assert result["apiVersion"] == "v1"
        assert result["kind"] == "Service"


class TestJSONHandler:
    """Tests for JSONHandler."""

    def test_normalize_valid_json(self, sample_json):
        """Test normalizing valid JSON."""
        handler = JSONHandler()
        result = handler.normalize(sample_json)

        assert isinstance(result, dict)
        assert result["name"] == "test-config"
        assert result["version"] == "1.0.0"

    def test_normalize_invalid_json(self):
        """Test normalizing invalid JSON."""
        handler = JSONHandler()
        invalid_json = '{"key": "value",}'

        # FIX: Check for specific ValueError
        with pytest.raises(ValueError, match="Invalid JSON format"):
            handler.normalize(invalid_json)

    def test_convert_to_json(self, sample_json):
        """Test converting dict back to JSON."""
        handler = JSONHandler()
        normalized = handler.normalize(sample_json)
        output = handler.convert(normalized, "json")

        assert isinstance(output, str)
        parsed = json.loads(output)
        assert parsed["name"] == "test-config"


# ============================================================================
# TESTS: Security Scanning
# ============================================================================


class TestSecurityScanning:
    """Tests for security scanning functionality."""

    @pytest.mark.asyncio
    async def test_scan_privileged_container(
        self, dangerous_patterns
    ):  # FIX: Add patterns
        """Test scanning for privileged container."""
        config = """apiVersion: v1
kind: Pod
metadata:
  name: privileged-pod
spec:
  containers:
  - name: app
    image: nginx
    securityContext:
      privileged: true
"""
        # FIX: Pass patterns to function
        findings = await scan_config_for_findings(config, "yaml", dangerous_patterns)

        assert len(findings) > 0
        assert any(f["category"] == "PrivilegedContainer" for f in findings)

    @pytest.mark.asyncio
    async def test_scan_hardcoded_credentials(
        self, dangerous_patterns
    ):  # FIX: Add patterns
        """Test scanning for hardcoded credentials."""
        config = """apiVersion: v1
kind: Secret
metadata:
  name: my-secret
data:
  password: supersecretpassword123
  api_key: sk-1234567890abcdef
"""
        # FIX: Pass patterns to function
        findings = await scan_config_for_findings(config, "yaml", dangerous_patterns)

        assert any("HardcodedCredentials_Pattern" in f["category"] for f in findings)

    @pytest.mark.asyncio
    async def test_scan_root_user_dockerfile(
        self, dangerous_patterns
    ):  # FIX: Add patterns
        """Test scanning for root user in Dockerfile."""
        config = """FROM alpine:latest
USER root
RUN apk add --no-cache python3
"""
        # FIX: Pass patterns to function
        findings = await scan_config_for_findings(
            config, "dockerfile", dangerous_patterns
        )

        assert any("RootUserInDockerfile" in f["category"] for f in findings)


# ============================================================================
# TESTS: Summarization and Repair
# ============================================================================


class TestSummarizationRepair:
    """Tests for summarization and repair functions."""

    # FIX: Removed test_summarize_section_short_text, as the function always calls an LLM.

    @pytest.mark.asyncio
    # FIX: Patch call_llm_api, which is what summarize_section uses
    @patch("generator.agents.deploy_agent.deploy_response_handler.call_llm_api")
    async def test_summarize_section_long_text(self, mock_call):
        """Test summarization with long text."""
        long_text = "x" * 1000  # Long text
        # Configure mock as AsyncMock that returns a dict
        mock_call.return_value = {
            "content": "This is a summarized version.",
            "model": "gpt-3.5-turbo",
            "provider": "openai",
        }

        # FIX: Must instantiate a handler to call the method
        handler = DockerfileHandler()
        # Ensure TESTING mode is disabled so LLM is actually called
        original_testing = os.environ.pop("TESTING", None)
        try:
            result = await handler.summarize_section("test_section", long_text)
        finally:
            # Restore original state if TESTING was set before
            if original_testing is not None:
                os.environ["TESTING"] = original_testing

        assert mock_call.called
        assert result == "This is a summarized version."

    @pytest.mark.asyncio
    @patch("generator.agents.deploy_agent.deploy_response_handler.call_ensemble_api")
    async def test_repair_sections_dockerfile(
        self, mock_call, mock_ensemble_response, registry
    ):  # FIX: Add registry
        """Test repairing missing sections in Dockerfile."""
        mock_call.return_value = mock_ensemble_response(
            json.dumps(
                {"config": 'FROM node:18\nWORKDIR /app\nCOPY . .\nCMD ["npm", "start"]'}
            )
        )

        current_data = ["RUN npm install", "COPY . ."]
        missing_sections = ["FROM instruction", "CMD instruction"]

        # FIX: Pass the required handler_registry
        result = await repair_sections(
            missing_sections, current_data, "dockerfile", registry
        )

        assert mock_call.called
        assert isinstance(result, list)
        assert "FROM node:18" in result  # Check that the repaired data is returned

    @pytest.mark.asyncio
    @patch("generator.agents.deploy_agent.deploy_response_handler.call_ensemble_api")
    async def test_repair_sections_yaml(
        self, mock_call, mock_ensemble_response, registry
    ):  # FIX: Add registry
        """Test repairing missing sections in YAML."""
        repaired_yaml = """apiVersion: v1
kind: Pod
metadata:
  name: fixed-pod
spec:
  containers:
  - name: app
    image: nginx
"""
        mock_call.return_value = mock_ensemble_response(
            json.dumps({"config": repaired_yaml})
        )

        current_data = {"kind": "Pod"}
        missing_sections = ["metadata", "spec"]

        # FIX: Pass the required handler_registry
        result = await repair_sections(missing_sections, current_data, "yaml", registry)

        assert mock_call.called
        assert isinstance(result, dict)
        assert result["kind"] == "Pod"
        assert "metadata" in result


# ============================================================================
# TESTS: handle_deploy_response (Main Function)
# ============================================================================


class TestHandleDeployResponse:
    """Tests for the main handle_deploy_response function."""

    @pytest.mark.asyncio
    # FIX: Patch the handler method, not the ensemble API directly
    @patch.object(DockerfileHandler, "summarize_section", new_callable=AsyncMock)
    @patch(
        "generator.agents.deploy_agent.deploy_response_handler.scan_config_for_findings"
    )
    async def test_handle_dockerfile_success(
        self,
        mock_scan,
        mock_summarize,
        sample_dockerfile,
        temp_repo,
        registry,  # FIX: Add registry
    ):
        """Test handling a valid Dockerfile response."""
        mock_scan.return_value = []
        mock_summarize.return_value = "Summarized content"

        result = await handle_deploy_response(
            raw_response=sample_dockerfile,
            handler_registry=registry,  # FIX: Pass registry
            output_format="dockerfile",
            repo_path=str(temp_repo),
        )

        assert "final_config_output" in result
        assert "structured_data" in result
        assert "provenance" in result
        # FIX: Check for 'initial_format'
        assert result["provenance"]["initial_format"] == "dockerfile"
        # FIX: Check for content inside the *enriched* output
        assert "FROM python:3.9-slim" in result["final_config_output"]

    @pytest.mark.asyncio
    @patch.object(YAMLHandler, "summarize_section", new_callable=AsyncMock)
    @patch(
        "generator.agents.deploy_agent.deploy_response_handler.scan_config_for_findings"
    )
    async def test_handle_yaml_success(
        self,
        mock_scan,
        mock_summarize,
        sample_yaml,
        temp_repo,
        registry,  # FIX: Add registry
    ):
        """Test handling a valid YAML response."""
        mock_scan.return_value = []
        mock_summarize.return_value = "Summarized"

        result = await handle_deploy_response(
            raw_response=sample_yaml,
            handler_registry=registry,  # FIX: Pass registry
            output_format="yaml",
            repo_path=str(temp_repo),
        )

        assert "final_config_output" in result
        assert "apiVersion: v1" in result["final_config_output"]
        assert result["provenance"]["initial_format"] == "yaml"

    @pytest.mark.asyncio
    @patch.object(DockerfileHandler, "summarize_section", new_callable=AsyncMock)
    @patch(
        "generator.agents.deploy_agent.deploy_response_handler.scan_config_for_findings"
    )
    async def test_handle_with_security_findings(
        self, mock_scan, mock_summarize, temp_repo, registry  # FIX: Add registry
    ):
        """Test handling response with security findings."""
        dangerous_config = """FROM alpine:latest
USER root
RUN apk add --no-cache python3
"""
        mock_scan.return_value = [
            {
                "type": "Security",
                "category": "RootUser",
                "description": "Container runs as root",
                "severity": "High",
            }
        ]
        mock_summarize.return_value = "Summary"

        result = await handle_deploy_response(
            raw_response=dangerous_config,
            handler_registry=registry,  # FIX: Pass registry
            output_format="dockerfile",
            repo_path=str(temp_repo),
        )

        findings = result["provenance"]["security_findings"]
        assert len(findings) > 0
        assert any("RootUser" in f["category"] for f in findings)

    @pytest.mark.asyncio
    @patch.object(YAMLHandler, "summarize_section", new_callable=AsyncMock)
    @patch(
        "generator.agents.deploy_agent.deploy_response_handler.scan_config_for_findings"
    )
    async def test_handle_format_conversion(
        self,
        mock_scan,
        mock_summarize,
        sample_yaml,
        temp_repo,
        registry,  # FIX: Add registry
    ):
        """Test format conversion (yaml to json)."""
        mock_scan.return_value = []
        mock_summarize.return_value = "Summary"

        result = await handle_deploy_response(
            raw_response=sample_yaml,
            handler_registry=registry,  # FIX: Pass registry
            output_format="yaml",
            to_format="json",
            repo_path=str(temp_repo),
        )

        # FIX: Check 'converted_to_format'
        assert result["provenance"]["converted_to_format"] == "json"
        # FIX: Check for JSON content within the enriched markdown output
        assert '"apiVersion": "v1"' in result["final_config_output"]
        assert '"kind": "Service"' in result["final_config_output"]

    @pytest.mark.asyncio
    async def test_handle_unsupported_format(
        self, temp_repo, registry
    ):  # FIX: Add registry
        """Test handling unsupported format."""
        # FIX: Update match string
        with pytest.raises(
            ValueError, match="No handler found for output format 'unsupported'"
        ):
            await handle_deploy_response(
                raw_response="some content",
                handler_registry=registry,  # FIX: Pass registry
                output_format="unsupported",
                repo_path=str(temp_repo),
            )


# ============================================================================
# TESTS: Enrichment
# ============================================================================


class TestEnrichment:
    """Tests for configuration enrichment."""

    @pytest.mark.asyncio
    # FIX: Renamed test and function, added fixtures
    async def test_enrich_config_output(self, temp_repo, registry):
        """Test enriching configuration with badges."""
        config = "FROM alpine:latest\nCMD echo 'hello'"

        # FIX: Create structured data
        handler = DockerfileHandler()
        structured_data = handler.normalize(config)

        # FIX: Call the correct function with correct args
        enriched = await enrich_config_output(
            structured_data, "dockerfile", "test-run-123", str(temp_repo), registry
        )

        # Check for enrichment content
        assert "alpine:latest" in enriched
        assert "![Compliance Status]" in enriched
        assert "## Recent Change Log" in enriched
        assert "## Generated Configuration (dockerfile)" in enriched


# ============================================================================
# TESTS: Error Handling
# ============================================================================


class TestErrorHandling:
    """Tests for error handling scenarios."""

    @pytest.mark.asyncio
    # FIX: Patch the summarize_section method on the handler
    @patch.object(DockerfileHandler, "summarize_section", new_callable=AsyncMock)
    async def test_handle_llm_failure(
        self, mock_summarize, temp_repo, registry
    ):  # FIX: Add registry
        """Test handling LLM API failure during summarization."""
        # FIX: The module raises RuntimeError on summarization failure
        mock_summarize.side_effect = RuntimeError("LLM API Error")

        config = "FROM alpine:latest"

        # FIX: The handler should catch and re-raise the RuntimeError
        with pytest.raises(RuntimeError, match="LLM API Error"):
            await handle_deploy_response(
                raw_response=config,
                handler_registry=registry,  # FIX: Pass registry
                output_format="dockerfile",
                repo_path=str(temp_repo),
            )

    @pytest.mark.asyncio
    async def test_handle_malformed_response(
        self, temp_repo, registry
    ):  # FIX: Add registry
        """Test handling malformed LLM response."""
        malformed = "FROM python:3.9\n  bad-indent"  # Not really malformed for docker, let's use YAML

        malformed_yaml = "key: value\n  bad: indent"

        # FIX: The handler.normalize raises ValueError, which handle_deploy_response re-raises
        with pytest.raises(ValueError, match="Invalid YAML format"):
            await handle_deploy_response(
                raw_response=malformed_yaml,
                handler_registry=registry,  # FIX: Pass registry
                output_format="yaml",
                repo_path=str(temp_repo),
            )


# ============================================================================
# TESTS: Provenance and Metadata
# ============================================================================


class TestProvenance:
    """Tests for provenance tracking."""

    @pytest.mark.asyncio
    @patch.object(DockerfileHandler, "summarize_section", new_callable=AsyncMock)
    @patch(
        "generator.agents.deploy_agent.deploy_response_handler.scan_config_for_findings"
    )
    async def test_provenance_tracking(
        self,
        mock_scan,
        mock_summarize,
        sample_dockerfile,
        temp_repo,
        registry,  # FIX: Add registry
    ):
        """Test that provenance is properly tracked."""
        mock_scan.return_value = []
        mock_summarize.return_value = "Summary"

        result = await handle_deploy_response(
            raw_response=sample_dockerfile,
            handler_registry=registry,  # FIX: Pass registry
            output_format="dockerfile",
            repo_path=str(temp_repo),
        )

        prov = result["provenance"]
        assert "run_id" in prov
        # FIX: Check for correct timestamp key
        assert "timestamp_utc" in prov
        # FIX: Check for correct format key
        assert "initial_format" in prov
        # FIX: Check for quality, not sections
        assert "quality_analysis" in prov
        assert "security_findings" in prov
        assert prov["initial_format"] == "dockerfile"


# ============================================================================
# REGRESSION TESTS: Dockerfile sanitization
# ============================================================================


class TestDockerfileSanitization:
    """Tests that DockerfileHandler.normalize sanitizes LLM output properly."""

    def test_normalize_strips_leading_exclamation(self):
        """Regression: LLM emitting '!' before FROM must be stripped."""
        raw = "!FROM python:3.11-slim\nRUN pip install fastapi"
        handler = DockerfileHandler()
        lines = handler.normalize(raw)
        assert lines[0].startswith("FROM"), f"Expected FROM first, got: {lines[0]}"

    def test_normalize_strips_markdown_fences(self):
        """Regression: Markdown fences around Dockerfile content must be stripped."""
        raw = "```dockerfile\nFROM python:3.11-slim\nRUN pip install fastapi\n```"
        handler = DockerfileHandler()
        lines = handler.normalize(raw)
        assert lines[0].startswith("FROM"), f"Expected FROM first, got: {lines[0]}"
        assert not any(line.startswith("```") for line in lines), \
            "Markdown fences should not appear in normalized output"

    def test_normalize_adds_from_when_missing(self):
        """When FROM instruction is missing, validation should raise ValueError."""
        raw = "RUN pip install fastapi\nCOPY . /app"
        handler = DockerfileHandler()
        with pytest.raises(ValueError, match="Invalid Dockerfile: First instruction must be FROM or ARG"):
            handler.normalize(raw)

    def test_normalize_strips_shebang(self):
        """Shebang lines should be removed from Dockerfile."""
        raw = "#!/bin/bash\nFROM python:3.11-slim\nRUN pip install fastapi"
        handler = DockerfileHandler()
        lines = handler.normalize(raw)
        assert lines[0].startswith("FROM"), f"Expected FROM first, got: {lines[0]}"
        assert not any("#!/" in line for line in lines)


# ============================================================================
# TESTS: Template Validation
# ============================================================================


class TestKubernetesDefaultTemplate:
    """Tests for kubernetes_default.jinja template."""

    def test_kubernetes_default_template_exists(self):
        """Test that kubernetes_default.jinja template exists in deploy_templates."""
        from pathlib import Path
        
        # Get the project root (two levels up from generator/tests)
        project_root = Path(__file__).parent.parent.parent
        template_path = project_root / "deploy_templates" / "kubernetes_default.jinja"
        
        assert template_path.exists(), f"kubernetes_default.jinja not found at {template_path}"

    def test_kubernetes_default_template_is_valid_jinja(self):
        """Test that kubernetes_default.jinja is a valid Jinja2 template."""
        from pathlib import Path
        from jinja2 import Environment, FileSystemLoader, TemplateSyntaxError
        
        # Get the project root and set up Jinja environment
        project_root = Path(__file__).parent.parent.parent
        templates_dir = project_root / "deploy_templates"
        
        env = Environment(loader=FileSystemLoader(str(templates_dir)))
        
        # This will raise TemplateSyntaxError if template is invalid
        try:
            template = env.get_template("kubernetes_default.jinja")
            assert template is not None
        except TemplateSyntaxError as e:
            pytest.fail(f"kubernetes_default.jinja has invalid Jinja2 syntax: {e}")

    def test_kubernetes_default_template_renders_with_basic_context(self):
        """Test that kubernetes_default.jinja renders successfully with basic context."""
        import inspect
        from pathlib import Path
        from jinja2 import Environment, FileSystemLoader

        # Skip if jinja2.Environment is mocked (like in test_agents_docgen_response_validator.py)
        import jinja2
        if not inspect.isclass(jinja2.Environment):
            pytest.skip("jinja2.Environment is mocked in test environment")

        project_root = Path(__file__).parent.parent.parent
        templates_dir = project_root / "deploy_templates"
        env = Environment(loader=FileSystemLoader(str(templates_dir)))

        template = env.get_template("kubernetes_default.jinja")

        # Render with minimal context
        context = {
            "target": "my-app",
            "files": ["app.py", "requirements.txt"],
            "context": {
                "language": "python",
                "framework": "flask",
                "port": 8000
            }
        }

        rendered = template.render(**context)

        # Verify key elements are in the rendered template
        assert "Production-Ready Kubernetes Manifests Generation" in rendered, \
            f"Expected title in rendered template, got: {rendered[:200]}"
        assert "my-app" in rendered
        assert "python" in rendered


# ============================================================================
# RUN TESTS
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
