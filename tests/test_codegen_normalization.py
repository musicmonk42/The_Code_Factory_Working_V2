# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Regression tests for codegen response parsing, content normalization,
materialization, and deploy artifact sanitization.

These tests verify fixes for:
1. LLM responses with escaped newlines/tabs being rejected by syntax validation
2. JSON file-map bundles written as a single main.py instead of separate files
3. Dockerfile content starting with markdown/image tokens instead of FROM
4. Content wrapped in markdown fences being treated as invalid syntax
"""

import json
import pytest
import shutil
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch


# ---------------------------------------------------------------------------
# A) Parser regression: escaped content, "json\n{...}", fenced blocks
# ---------------------------------------------------------------------------

class TestCodegenResponseParsing:
    """Verify parse_llm_response handles various LLM response formats."""

    def _parse(self, response, lang="python"):
        from generator.agents.codegen_agent.codegen_response_handler import (
            parse_llm_response,
        )
        return parse_llm_response(response, lang)

    def test_json_prefix_file_map(self):
        """Input: 'json\\n{ \"files\": { \"app/main.py\": \"print(\\'x\\')\\n\" } }'
        Expect: file map with key app/main.py and content with real newline."""
        raw = 'json\n{ "files": { "app/main.py": "print(\'x\')\\n" } }'
        result = self._parse(raw)
        assert "app/main.py" in result, f"Expected app/main.py in result, got {list(result.keys())}"
        content = result["app/main.py"]
        # Content should have a real newline (not literal \\n)
        assert "\n" in content or content == "print('x')", \
            f"Content should have real newline, got: {repr(content)}"

    def test_fenced_json_file_map(self):
        """Fenced ```json ... ``` block containing file map."""
        raw = '```json\n{"files": {"main.py": "x = 1"}}\n```'
        result = self._parse(raw)
        assert "main.py" in result, f"Expected main.py in result, got {list(result.keys())}"

    def test_plain_json_file_map(self):
        """Plain JSON object with files key."""
        raw = json.dumps({"files": {"app/main.py": "import os\n", "app/routes.py": "pass\n"}})
        result = self._parse(raw)
        assert "app/main.py" in result
        assert "app/routes.py" in result

    def test_escaped_newlines_in_content(self):
        """Content with literal \\n should be normalized to real newlines."""
        file_content = "from fastapi import FastAPI\\napp = FastAPI()\\n"
        raw = json.dumps({"files": {"main.py": file_content}})
        result = self._parse(raw)
        assert "main.py" in result
        content = result["main.py"]
        # The normalized content should compile as valid Python
        compile(content, "main.py", "exec")


# ---------------------------------------------------------------------------
# B) Normalization regression
# ---------------------------------------------------------------------------

class TestContentNormalization:
    """Verify _normalize_file_content handles escaped content correctly."""

    def _normalize(self, content):
        from generator.agents.codegen_agent.codegen_response_handler import (
            _normalize_file_content,
        )
        return _normalize_file_content(content)

    def test_escaped_newlines(self):
        result = self._normalize("print('hello')\\nprint('world')")
        assert result == "print('hello')\nprint('world')"

    def test_escaped_tabs(self):
        result = self._normalize("if True:\\n\\tpass")
        assert result == "if True:\n\tpass"

    def test_escaped_crlf(self):
        result = self._normalize("line1\\r\\nline2")
        assert result == "line1\nline2"

    def test_markdown_fences_stripped(self):
        result = self._normalize("```python\nprint('hello')\n```")
        assert result.strip() == "print('hello')"

    def test_bom_stripped(self):
        result = self._normalize("\ufeffprint('hello')")
        assert result == "print('hello')"

    def test_empty_content(self):
        result = self._normalize("")
        assert result == ""

    def test_none_passthrough(self):
        result = self._normalize(None)
        assert result is None

    def test_normalized_content_compiles(self):
        """Escaped Python code should be compilable after normalization."""
        raw = "from fastapi import FastAPI\\napp = FastAPI()\\n\\n@app.get('/health')\\ndef health():\\n    return {'status': 'ok'}\\n"
        normalized = self._normalize(raw)
        compile(normalized, "test.py", "exec")


# ---------------------------------------------------------------------------
# C) Materialization regression
# ---------------------------------------------------------------------------

class TestMaterialization:
    """Verify materialize_file_map writes files correctly with normalization."""

    @pytest.fixture
    def output_dir(self):
        d = tempfile.mkdtemp(prefix="mat_test_")
        yield Path(d)
        shutil.rmtree(d, ignore_errors=True)

    @pytest.mark.asyncio
    async def test_materialize_multi_file(self, output_dir):
        from generator.runner.runner_file_utils import materialize_file_map

        file_map = {
            "app/main.py": "from fastapi import FastAPI\napp = FastAPI()\n",
            "app/routes.py": "pass\n",
            "requirements.txt": "fastapi\nuvicorn\n",
        }
        result = await materialize_file_map(file_map, output_dir)
        assert result["success"], f"Materialization failed: {result['errors']}"
        assert (output_dir / "app" / "main.py").exists()
        assert (output_dir / "app" / "routes.py").exists()
        assert (output_dir / "requirements.txt").exists()

    @pytest.mark.asyncio
    async def test_materialize_normalizes_escaped_content(self, output_dir):
        """Files with literal \\n should be written with real newlines."""
        from generator.runner.runner_file_utils import materialize_file_map

        file_map = {
            "main.py": "print('hello')\\nprint('world')",
        }
        result = await materialize_file_map(file_map, output_dir)
        assert result["success"]
        content = (output_dir / "main.py").read_text()
        assert "print('hello')\nprint('world')" in content


# ---------------------------------------------------------------------------
# G) Deploy artifact sanitization regression
# ---------------------------------------------------------------------------

class TestDockerfileSanitization:
    """Verify Dockerfile content is sanitized before writing."""

    def _sanitize(self, content):
        from server.services.omnicore_service import OmniCoreService
        return OmniCoreService._sanitize_dockerfile_content(content)

    def test_strips_markdown_image_badge(self):
        content = "![Compliance Status](badge.svg)\nFROM python:3.11-slim\nCOPY . /app\n"
        result = self._sanitize(content)
        lines = [l for l in result.splitlines() if l.strip()]
        assert lines[0].startswith("FROM"), f"First line should be FROM, got: {lines[0]}"

    def test_strips_markdown_fences(self):
        content = "```dockerfile\nFROM python:3.11-slim\nCOPY . /app\n```"
        result = self._sanitize(content)
        assert "```" not in result
        lines = [l for l in result.splitlines() if l.strip()]
        assert lines[0].startswith("FROM")

    def test_adds_from_if_missing(self):
        content = "COPY . /app\nRUN pip install -r requirements.txt\n"
        result = self._sanitize(content)
        lines = [l for l in result.splitlines() if l.strip()]
        assert lines[0].startswith("FROM")

    def test_preserves_valid_dockerfile(self):
        content = "FROM python:3.11-slim\nWORKDIR /app\nCOPY . .\n"
        result = self._sanitize(content)
        lines = [l for l in result.splitlines() if l.strip()]
        assert lines[0] == "FROM python:3.11-slim"

    def test_strips_exclamation_lines(self):
        content = "!invalid line\nFROM python:3.11-slim\n"
        result = self._sanitize(content)
        assert "!invalid" not in result
        lines = [l for l in result.splitlines() if l.strip()]
        assert lines[0].startswith("FROM")
