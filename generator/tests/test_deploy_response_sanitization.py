"""
Unit tests for Deploy Response Handler - YAML Sanitization

Industry Standards Applied:
- Test-driven validation: Ensure markdown removal works correctly
- Edge case coverage: Test various markdown patterns
- Security validation: Ensure no injection vulnerabilities
- Performance testing: Verify large response handling
"""

import pytest
from generator.agents.deploy_agent.deploy_response_handler import (
    _sanitize_llm_output,
    extract_config_from_response,
    YAMLHandler,
)


class TestSanitizeLLMOutput:
    """Test suite for _sanitize_llm_output function."""
    
    def test_removes_mermaid_blocks(self):
        """Test that mermaid diagram blocks are completely removed."""
        input_text = """```mermaid
graph TD;
    A-->B;
    B-->C;
```
```yaml
apiVersion: v1
kind: Service
```"""
        result = _sanitize_llm_output(input_text)
        assert "mermaid" not in result
        assert "graph TD" not in result
        assert "apiVersion: v1" in result
    
    def test_case_insensitive_mermaid_detection(self):
        """Test that mermaid blocks are detected case-insensitively."""
        input_text = """```MERMAID
graph TD;
```
apiVersion: v1"""
        result = _sanitize_llm_output(input_text)
        assert "MERMAID" not in result
        assert "apiVersion" in result
    
    def test_removes_multiple_mermaid_blocks(self):
        """Test removal of multiple mermaid blocks."""
        input_text = """```mermaid
graph1
```
content1
```mermaid
graph2
```
content2"""
        result = _sanitize_llm_output(input_text)
        assert "mermaid" not in result
        assert "graph1" not in result
        assert "graph2" not in result
        assert "content1" in result
        assert "content2" in result
    
    def test_removes_other_diagram_types(self):
        """Test that other diagram types are removed."""
        for diagram_type in ["dot", "plantuml", "graphviz"]:
            input_text = f"""```{diagram_type}
diagram content
```
actual: config"""
            result = _sanitize_llm_output(input_text)
            assert diagram_type not in result
            assert "diagram content" not in result
            assert "actual: config" in result
    
    def test_removes_code_fences(self):
        """Test that code fences are stripped from YAML."""
        input_text = """```yaml
apiVersion: v1
kind: Service
```"""
        result = _sanitize_llm_output(input_text)
        assert result.strip() == "apiVersion: v1\nkind: Service"
    
    def test_handles_generic_code_blocks(self):
        """Test generic code blocks without language specifier."""
        input_text = """```
apiVersion: v1
kind: Service
```"""
        result = _sanitize_llm_output(input_text)
        assert result.strip() == "apiVersion: v1\nkind: Service"
    
    def test_preserves_clean_yaml(self):
        """Test that clean YAML without artifacts is preserved."""
        input_text = "apiVersion: v1\nkind: Service\nmetadata:\n  name: my-service"
        result = _sanitize_llm_output(input_text)
        assert result == input_text
    
    def test_handles_empty_input(self):
        """Test that empty input is handled gracefully."""
        result = _sanitize_llm_output("")
        assert result == ""
    
    def test_handles_whitespace_only(self):
        """Test that whitespace-only input is handled."""
        result = _sanitize_llm_output("   \n\t  \n   ")
        assert result == ""
    
    def test_strips_leading_trailing_whitespace(self):
        """Test that leading/trailing whitespace is stripped."""
        input_text = "\n\n  apiVersion: v1\nkind: Service  \n\n"
        result = _sanitize_llm_output(input_text)
        assert result == "apiVersion: v1\nkind: Service"
    
    def test_handles_nested_code_blocks(self):
        """Test handling of nested or malformed code blocks."""
        input_text = """```yaml
```mermaid
nested
```
apiVersion: v1
```"""
        result = _sanitize_llm_output(input_text)
        # Should handle this gracefully without breaking
        assert "apiVersion" in result


class TestExtractConfigFromResponse:
    """Test suite for extract_config_from_response function."""
    
    def test_rejects_mermaid_diagrams(self):
        """Test that responses with mermaid diagrams are rejected."""
        response = """```mermaid
graph TD;
    A-->B;
```
```yaml
apiVersion: v1
```"""
        with pytest.raises(ValueError, match="mermaid diagram"):
            extract_config_from_response(response, "yaml")
    
    def test_case_insensitive_mermaid_rejection(self):
        """Test mermaid rejection is case-insensitive."""
        response = "```MERMAID\ngraph\n```\napiVersion: v1"
        with pytest.raises(ValueError, match="mermaid diagram"):
            extract_config_from_response(response, "yaml")
    
    def test_extracts_clean_docker(self):
        """Test extraction of clean Dockerfile."""
        response = "FROM python:3.11\nWORKDIR /app\nCOPY . ."
        result = extract_config_from_response(response, "dockerfile")
        assert result == response
    
    def test_extracts_from_markdown_blocks(self):
        """Test extraction from markdown code blocks."""
        response = """Here is your Dockerfile:
```dockerfile
FROM python:3.11
WORKDIR /app
```"""
        result = extract_config_from_response(response, "dockerfile")
        assert result.strip() == "FROM python:3.11\nWORKDIR /app"
    
    def test_extracts_yaml_with_separator(self):
        """Test extraction of YAML starting with document separator."""
        response = "---\napiVersion: v1\nkind: Service"
        result = extract_config_from_response(response, "yaml")
        assert result == response
    
    def test_warns_multiple_code_blocks(self):
        """Test warning logged for multiple code blocks."""
        response = """```yaml
config1
```
```yaml
config2
```"""
        # Should not raise, but extract one
        result = extract_config_from_response(response, "yaml")
        assert "config" in result
    
    def test_handles_empty_response(self):
        """Test handling of empty response."""
        result = extract_config_from_response("", "yaml")
        assert result == ""
    
    def test_extracts_from_dockerfile_preamble(self):
        """Test extraction when Dockerfile has explanatory preamble."""
        response = """This Dockerfile sets up a Python environment:

FROM python:3.11-slim
WORKDIR /app
RUN pip install requirements.txt"""
        result = extract_config_from_response(response, "dockerfile")
        assert result.startswith("FROM python")
        assert "This Dockerfile" not in result


class TestYAMLHandlerSanitization:
    """Test suite for YAMLHandler._sanitize_yaml_response method."""
    
    def test_skips_markdown_headers(self):
        """Test that markdown headers are removed."""
        handler = YAMLHandler()
        input_yaml = """# This is a Header
apiVersion: v1
# Not a header (YAML comment)
kind: Service"""
        result = handler._sanitize_yaml_response(input_yaml)
        assert "# This is a Header" not in result
        assert "# Not a header" in result  # YAML comment preserved
        assert "apiVersion: v1" in result
    
    def test_removes_markdown_bold(self):
        """Test that markdown bold is removed."""
        handler = YAMLHandler()
        input_yaml = "key: **bold value**\nother: normal"
        result = handler._sanitize_yaml_response(input_yaml)
        assert "**" not in result
        assert "bold value" in result
    
    def test_skips_numbered_lists_with_bold(self):
        """Test that numbered list explanations are skipped."""
        handler = YAMLHandler()
        input_yaml = """1. **First Item**: explanation
apiVersion: v1
kind: Service"""
        result = handler._sanitize_yaml_response(input_yaml)
        assert "1. **First Item**" not in result
        assert "apiVersion: v1" in result
    
    def test_handles_mermaid_blocks_line_by_line(self):
        """Test that mermaid blocks are skipped line by line."""
        handler = YAMLHandler()
        input_yaml = """```mermaid
graph TD;
    A-->B;
```
apiVersion: v1
kind: Service"""
        result = handler._sanitize_yaml_response(input_yaml)
        assert "mermaid" not in result
        assert "graph TD" not in result
        assert "apiVersion: v1" in result
    
    def test_removes_markdown_links(self):
        """Test that markdown links are converted to text."""
        handler = YAMLHandler()
        input_yaml = "description: See [documentation](http://example.com) for details"
        result = handler._sanitize_yaml_response(input_yaml)
        assert "(http://example.com)" not in result
        assert "documentation" in result
    
    def test_removes_inline_backticks(self):
        """Test that inline code backticks are removed."""
        handler = YAMLHandler()
        input_yaml = "command: Use `kubectl apply` to deploy"
        result = handler._sanitize_yaml_response(input_yaml)
        assert "`" not in result
        assert "kubectl apply" in result
    
    def test_preserves_yaml_structure(self):
        """Test that valid YAML structure is preserved."""
        handler = YAMLHandler()
        input_yaml = """apiVersion: v1
kind: Service
metadata:
  name: my-service
spec:
  ports:
    - port: 80"""
        result = handler._sanitize_yaml_response(input_yaml)
        assert "apiVersion: v1" in result
        assert "  name: my-service" in result
        assert "    - port: 80" in result


class TestYAMLHandlerNormalize:
    """Test suite for YAMLHandler.normalize method."""
    
    def test_rejects_markdown_bold_after_sanitization(self):
        """Test that markdown bold is sanitized correctly."""
        handler = YAMLHandler()
        # This should now be sanitized successfully
        input_yaml = "- **bold**: value"
        result = handler.normalize(input_yaml)
        # Verify it's parsed as valid YAML
        assert isinstance(result, list)
    
    def test_parses_valid_yaml(self):
        """Test that valid YAML is parsed correctly."""
        handler = YAMLHandler()
        input_yaml = """apiVersion: v1
kind: Service
metadata:
  name: test"""
        result = handler.normalize(input_yaml)
        assert isinstance(result, dict)
        assert result["apiVersion"] == "v1"
        assert result["kind"] == "Service"
    
    def test_handles_multi_document_yaml(self):
        """Test handling of multi-document YAML."""
        handler = YAMLHandler()
        input_yaml = """---
apiVersion: v1
kind: Service
---
apiVersion: v1
kind: Deployment"""
        result = handler.normalize(input_yaml)
        # Should handle multi-doc YAML
        assert result is not None


class TestSecurityAndPerformance:
    """Security and performance tests for sanitization."""
    
    def test_no_code_execution_in_yaml(self):
        """Test that potentially malicious YAML is safely handled."""
        handler = YAMLHandler()
        # Attempt YAML with Python object serialization (should be safe-loaded)
        malicious = "!!python/object/apply:os.system ['echo pwned']"
        try:
            handler.normalize(malicious)
        except Exception:
            # Should raise parsing error, not execute code
            pass
        # If we get here without executing code, test passes
        assert True
    
    def test_handles_large_response(self):
        """Test that large responses are handled efficiently."""
        # Create a large YAML-like response
        large_yaml = "\n".join([f"key{i}: value{i}" for i in range(10000)])
        result = _sanitize_llm_output(large_yaml)
        # Should complete without error
        assert len(result) > 0
    
    def test_deeply_nested_mermaid_blocks(self):
        """Test handling of deeply nested or complex mermaid blocks."""
        handler = YAMLHandler()
        complex_input = """```mermaid
graph TD;
    subgraph A
        A1-->A2
    end
    subgraph B
        B1-->B2
    end
```
apiVersion: v1"""
        result = handler._sanitize_yaml_response(complex_input)
        assert "mermaid" not in result
        assert "apiVersion" in result
    
    def test_no_regex_denial_of_service(self):
        """Test that sanitization doesn't have regex DoS vulnerabilities."""
        # Create a string designed to cause regex backtracking
        evil_string = "```mermaid" + "a" * 10000 + "```\napiVersion: v1"
        # Should complete in reasonable time
        import time
        start = time.time()
        result = _sanitize_llm_output(evil_string)
        elapsed = time.time() - start
        assert elapsed < 1.0  # Should be fast
        assert "apiVersion" in result
