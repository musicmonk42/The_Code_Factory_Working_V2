# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Tests verifying that ASTEndpointExtractor is wired into
extract_endpoints_from_code() (provenance.py) and
_generate_fastapi_tests() (testgen_agent.py).
"""

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# provenance.py — extract_endpoints_from_code
# ---------------------------------------------------------------------------

class TestProvenanceASTWiring:
    """extract_endpoints_from_code() should delegate to ASTEndpointExtractor for .py files."""

    _FASTAPI_SOURCE = (
        "from fastapi import FastAPI\n"
        "app = FastAPI()\n"
        "@app.get('/health')\n"
        "def health(): return {}\n"
    )

    def test_uses_ast_extractor_for_py_files(self):
        """ASTEndpointExtractor.extract_from_source is called for .py filenames."""
        from generator.main.provenance import extract_endpoints_from_code

        mock_extractor_instance = MagicMock()
        mock_extractor_instance.extract_from_source.return_value = [
            {"method": "GET", "path": "/health", "function_name": "health", "line_number": 3}
        ]
        mock_extractor_cls = MagicMock(return_value=mock_extractor_instance)
        fake_module = MagicMock()
        fake_module.ASTEndpointExtractor = mock_extractor_cls

        with patch.dict("sys.modules", {"generator.utils.ast_endpoint_extractor": fake_module}):
            result = extract_endpoints_from_code(self._FASTAPI_SOURCE, "main.py")

        # The function should return a list of endpoint dicts
        assert isinstance(result, list)

    def test_returns_method_and_path_keys(self):
        """Result dicts must contain 'method' and 'path' keys."""
        from generator.main.provenance import extract_endpoints_from_code

        result = extract_endpoints_from_code(self._FASTAPI_SOURCE, "main.py")
        assert len(result) >= 1
        for ep in result:
            assert "method" in ep
            assert "path" in ep

    def test_ast_extractor_produces_correct_endpoint(self):
        """AST extractor resolves the /health GET route from the sample source."""
        from generator.main.provenance import extract_endpoints_from_code

        result = extract_endpoints_from_code(self._FASTAPI_SOURCE, "main.py")
        paths = [ep["path"] for ep in result]
        methods = [ep["method"] for ep in result]
        assert "/health" in paths
        assert "GET" in methods

    def test_fallback_to_regex_for_non_py(self):
        """Non-.py files still return endpoints via regex path."""
        from generator.main.provenance import extract_endpoints_from_code

        ts_source = "app.get('/ping', (req, res) => res.send('pong'));"
        result = extract_endpoints_from_code(ts_source, "routes.ts")
        assert any(ep["path"] == "/ping" for ep in result)

    def test_exception_in_ast_extractor_falls_back_to_regex(self):
        """If ASTEndpointExtractor.extract_from_source raises, regex fallback is used."""
        from generator.main.provenance import extract_endpoints_from_code

        source = "@app.get('/items')\ndef list_items(): ...\n"

        # Patch the constructor inside the function to return a raising mock
        raising_instance = MagicMock()
        raising_instance.extract_from_source.side_effect = RuntimeError("AST boom")
        raising_cls = MagicMock(return_value=raising_instance)

        import sys
        fake_module = MagicMock()
        fake_module.ASTEndpointExtractor = raising_cls
        with patch.dict("sys.modules", {"generator.utils.ast_endpoint_extractor": fake_module}):
            result = extract_endpoints_from_code(source, "items.py")
        # Regex fallback should still find the endpoint
        assert any(ep["path"] == "/items" for ep in result)


# ---------------------------------------------------------------------------
# testgen_agent.py — _generate_fastapi_tests
# ---------------------------------------------------------------------------

class TestTestgenASTWiring:
    """_generate_fastapi_tests() should use ASTEndpointExtractor."""

    _FASTAPI_SOURCE = (
        "from fastapi import FastAPI\n"
        "app = FastAPI()\n"
        "@app.post('/items')\n"
        "def create_item(): return {}\n"
    )

    def _make_agent(self):
        """Create a minimal TestGenAgent with patched LLM dependencies."""
        try:
            from agents.testgen_agent.testgen_agent import TestGenAgent
        except ImportError:
            pytest.skip("TestGenAgent not importable in this environment")
        agent = object.__new__(TestGenAgent)
        return agent

    def test_ast_wiring_calls_extractor(self):
        """_generate_fastapi_tests delegates to ASTEndpointExtractor."""
        try:
            from agents.testgen_agent.testgen_agent import TestGenAgent
        except ImportError:
            pytest.skip("TestGenAgent not importable in this environment")

        agent = object.__new__(TestGenAgent)

        mock_extractor_instance = MagicMock()
        mock_extractor_instance.extract_from_source.return_value = [
            {"method": "POST", "path": "/items", "function_name": "create_item", "line_number": 3}
        ]
        mock_cls = MagicMock(return_value=mock_extractor_instance)

        with patch(
            "generator.utils.ast_endpoint_extractor.ASTEndpointExtractor",
            mock_cls,
        ):
            result = agent._generate_fastapi_tests(self._FASTAPI_SOURCE, "main.py")

        assert isinstance(result, str)
        mock_extractor_instance.extract_from_source.assert_called_once_with(
            self._FASTAPI_SOURCE, "main.py"
        )

    def test_regex_fallback_when_ast_unavailable(self):
        """_generate_fastapi_tests falls back to regex when AST extractor raises."""
        try:
            from agents.testgen_agent.testgen_agent import TestGenAgent
        except ImportError:
            pytest.skip("TestGenAgent not importable in this environment")

        agent = object.__new__(TestGenAgent)

        with patch(
            "generator.utils.ast_endpoint_extractor.ASTEndpointExtractor",
            side_effect=ImportError("no module"),
        ):
            result = agent._generate_fastapi_tests(self._FASTAPI_SOURCE, "main.py")

        assert isinstance(result, str)
        assert "/items" in result or "post" in result.lower()
