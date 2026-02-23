# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# test_generation/gen_agent/gen_plugins.py
"""
Module for generating unit tests for Python and JavaScript code.

This module provides a deterministic, multi-language test generation system with
tight guardrails and configurable options. It supports generating tests for
top-level functions and includes logic for creating various test cases.

Configuration Options:
    - test_framework (str): Testing framework to use (e.g., 'pytest', 'unittest', 'jest', 'mocha').
    - max_tests_per_function (int): Maximum number of tests per function (default: 2).
    - module_path (str): Path to the module containing the functions to test.
    - use_ai (bool): Whether to use AI for test generation (default: True).
    - strict_source_filter (bool): Enable strict filtering of dangerous code constructs (default: False).
    - cache_size (int): Max size for the LRU cache (default: 100).
    - module_system (str): For JavaScript, specifies the module system ('esm' or 'cjs', default: 'cjs').
    - pytest_options (dict): Dictionary of pytest-specific options, e.g., {'enable_coverage': True}.
"""

import ast
import hashlib
import json
import logging
import os
import threading
import time
from collections import defaultdict
from functools import lru_cache
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

SUPPORTED_LANGUAGES = frozenset({"python", "javascript", "typescript"})
SUPPORTED_FRAMEWORKS = {
    "python": frozenset({"unittest", "pytest"}),
    "javascript": frozenset({"jest", "mocha"}),
    "typescript": frozenset({"jest", "mocha"}),
}
DEFAULT_PYTHON_TEST_FRAMEWORK = os.getenv("PYTHON_TEST_FRAMEWORK", "pytest")
DEFAULT_JS_TEST_FRAMEWORK = os.getenv("JS_TEST_FRAMEWORK", "jest")


class BaseTestGenerator:
    """Base class for test generators. Subclasses must implement generate()."""

    def __init__(self):
        self._parsed_trees = {}

    def generate(self, code: str, config: dict) -> List[str]:
        """Generates a list of test file bodies for the given source code."""
        raise NotImplementedError("Subclasses must implement this method.")


def _sanitize_identifier(name: str) -> str:
    """
    Sanitizes a string to be a valid Python or JavaScript identifier.

    Args:
        name (str): The input string to sanitize.

    Returns:
        str: A sanitized string suitable for use as a class or function name.
    """
    s = "".join(c for c in name if c.isalnum() or c == "_")
    s = s.lstrip("0123456789_")
    return s or "func"


def _limit_tests_per_function(
    blocks: Dict[str, List[str]], max_per_fn: int
) -> List[str]:
    """
    Limits the number of generated tests per function by selecting a deterministic subset of blocks.

    Args:
        blocks (Dict[str, List[str]]): A dictionary mapping function names to lists of test blocks.
        max_per_fn (int): The maximum number of test blocks to keep per function.

    Returns:
        List[str]: A flattened list of the selected test blocks.
    """
    out: List[str] = []
    if max_per_fn is None or int(max_per_fn) <= 0:
        for cases in blocks.values():
            out.extend(cases)
        return out

    for fname, cases in blocks.items():
        if len(cases) <= max_per_fn:
            out.extend(cases)
            continue

        # Use a stable hash to ensure deterministic selection across runs
        ranked = sorted(
            cases, key=lambda s: hashlib.sha256(f"{fname}::{s}".encode()).hexdigest()
        )
        out.extend(ranked[:max_per_fn])
    return out


def _assemble_file(header: str, blocks: List[str]) -> str:
    """
    Assembles the final test file body from a header and a list of test blocks.

    Args:
        header (str): The import and setup header for the test file.
        blocks (List[str]): A list of complete test block strings.

    Returns:
        str: The full body of the test file.
    """
    if not blocks:
        return ""
    return header + "\n\n" + "\n\n".join(blocks)


def _normalize_cfg(cfg: dict) -> dict:
    """
    Converts a config dict to a JSON-serializable format for caching.

    Args:
        cfg (dict): The configuration dictionary.

    Returns:
        dict: A normalized version of the dictionary.
    """
    out = {}
    for k, v in cfg.items():
        if isinstance(v, (set, frozenset)):
            out[k] = sorted(list(v))
        elif isinstance(v, (list, tuple)):
            out[k] = list(v)
        else:
            out[k] = v
    return out


class PythonTestGenerator(BaseTestGenerator):
    def generate(self, code: str, config: dict) -> List[str]:
        """
        Generates pytest or unittest style tests for a given Python code string.
        """
        cache_size = int(config.get("cache_size", 100))
        code_hash = hashlib.sha256(code.encode()).hexdigest()

        @lru_cache(maxsize=cache_size)
        def _generate_cached(code: str, cfg_key: str) -> Dict[str, List[str]]:
            cfg = json.loads(cfg_key)
            framework = cfg.get("test_framework", DEFAULT_PYTHON_TEST_FRAMEWORK)

            try:
                if code not in self._parsed_trees:
                    self._parsed_trees[code] = ast.parse(code)
                tree = self._parsed_trees[code]
            except SyntaxError as e:
                details = {
                    "line": e.lineno,
                    "offset": e.offset,
                    "snippet": code.splitlines()[e.lineno - 1] if e.lineno else "",
                }
                logger.error("Invalid Python syntax", extra={"details": details})
                raise ValueError("Invalid Python syntax", details)

            # Build parent mapping to identify top-level functions
            parent: Dict[ast.AST, ast.AST] = {}
            for node in ast.walk(tree):
                for child in ast.iter_child_nodes(node):
                    parent[child] = node

            test_blocks: Dict[str, List[str]] = defaultdict(list)

            dependencies_to_mock = config.get("dependencies_to_mock", ["requests.get"])
            needs_mocking = any(dep in code for dep in dependencies_to_mock)

            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and isinstance(
                    parent.get(node), ast.Module
                ):
                    fname = node.name
                    sanitized_fname = _sanitize_identifier(fname)

                    class_name = "Test" + "".join(
                        part.capitalize() for part in sanitized_fname.split("_")
                    )

                    def _guess_default(name: str) -> str:
                        n = name.lower()
                        if n in {"n", "num", "count", "size", "length", "idx", "index"}:
                            return "0"
                        if any(k in n for k in ["text", "str", "name", "path", "file"]):
                            return "''"
                        return "None"

                    arg_values = [_guess_default(arg.arg) for arg in node.args.args]

                    if framework == "unittest":
                        body = []
                        body.append(f"class {class_name}(unittest.TestCase):")
                        body.append(f"    def test_{sanitized_fname}_basic(self):")
                        body.append(
                            f"        self.assertIsNotNone({fname}({', '.join(arg_values)}))"
                        )

                        if node.args.args:
                            body.append(
                                f"    def test_{sanitized_fname}_edge_case(self):"
                            )
                            body.append("        with self.assertRaises(TypeError):")
                            body.append(
                                f"            {fname}({', '.join(['None'] * len(node.args.args))})"
                            )

                        if any(a.arg == "num" for a in node.args.args):
                            logger.info(
                                f"Generating numeric boundary tests for {fname}",
                                extra={"code_hash": code_hash},
                            )
                            body.append(
                                f"    def test_{sanitized_fname}_boundary_numeric(self):"
                            )
                            body.append(f"        self.assertIsNotNone({fname}(0))")
                            body.append(f"        self.assertIsNotNone({fname}(1))")
                            body.append(f"        self.assertIsNotNone({fname}(-1))")
                            body.append(
                                f"        self.assertIsNotNone({fname}(10000000))"
                            )
                        if any(a.arg == "text" for a in node.args.args):
                            logger.info(
                                f"Generating string boundary tests for {fname}",
                                extra={"code_hash": code_hash},
                            )
                            body.append(
                                f"    def test_{sanitized_fname}_boundary_string(self):"
                            )
                            body.append(f"        self.assertIsNotNone({fname}(''))")
                            body.append(
                                f"        self.assertIsNotNone({fname}(' ' * 100))"
                            )
                            body.append(
                                f"        self.assertIsNotNone({fname}('!@#$%^&*()'))"
                            )

                        if needs_mocking:
                            body.append(
                                f"    def test_{sanitized_fname}_mocked_api(self):"
                            )
                            body.append(
                                "        with patch('requests.get', return_value=MockResponse()):"
                            )
                            body.append(f"            self.assertIsNotNone({fname}())")

                        test_blocks[fname].append("\n".join(body))
                    else:  # pytest
                        test_blocks[fname].append(
                            f"def test_{sanitized_fname}_basic():\n"
                            f"    assert {fname}({', '.join(arg_values)}) is not None"
                        )
                        if node.args.args:
                            test_blocks[fname].append(
                                f"def test_{sanitized_fname}_edge_case():\n"
                                f"    with pytest.raises(TypeError):\n"
                                f"        {fname}({', '.join(['None'] * len(node.args.args))})"
                            )

                        if any(a.arg == "num" for a in node.args.args):
                            logger.info(
                                f"Generating numeric boundary tests for {fname}",
                                extra={"code_hash": code_hash},
                            )
                            test_blocks[fname].append(
                                f"def test_{sanitized_fname}_boundary_numeric():\n"
                                f"    assert {fname}(0) is not None\n"
                                f"    assert {fname}(1) is not None\n"
                                f"    assert {fname}(-1) is not None\n"
                                f"    assert {fname}(10000000) is not None"
                            )
                        if any(a.arg == "text" for a in node.args.args):
                            logger.info(
                                f"Generating string boundary tests for {fname}",
                                extra={"code_hash": code_hash},
                            )
                            test_blocks[fname].append(
                                f"def test_{sanitized_fname}_boundary_string():\n"
                                f"    assert {fname}('') is not None\n"
                                f"    assert {fname}(' ' * 100) is not None\n"
                                f"    assert {fname}('!@#$%^&*()') is not None"
                            )

                        if needs_mocking:
                            test_blocks[fname].append(
                                f"def test_{sanitized_fname}_mocked_api(monkeypatch):\n"
                                f"    import requests\n"
                                f"    monkeypatch.setattr(requests, 'get', lambda *a, **k: MockResponse())\n"
                                f"    assert {fname}() is not None"
                            )

                        if config.get("pytest_options", {}).get("enable_coverage"):
                            test_blocks[fname].append(
                                f"def test_{sanitized_fname}_coverage():\n"
                                f"    # Ensure coverage for {fname}\n"
                                f"    assert {fname}({', '.join(arg_values)}) is not None"
                            )
            return test_blocks

        cfg_key = json.dumps(_normalize_cfg(config), sort_keys=True)
        blocks = _generate_cached(code, cfg_key)

        max_per_fn = int(config.get("max_tests_per_function", 2))
        limited_blocks = _limit_tests_per_function(blocks, max_per_fn)

        function_names = sorted(blocks.keys())
        if not function_names:
            return []

        header_template = (
            "import pytest"
            if config.get("test_framework", DEFAULT_PYTHON_TEST_FRAMEWORK) == "pytest"
            else "import unittest"
        )

        pytest_options = config.get("pytest_options", {})
        if pytest_options.get("enable_coverage"):
            header_template += f"\n# Run with: pytest --cov={config.get('module_path')} --cov-report=xml"

        needs_mocking = any(
            dep in code for dep in config.get("dependencies_to_mock", ["requests.get"])
        )
        if (
            needs_mocking
            and config.get("test_framework", DEFAULT_PYTHON_TEST_FRAMEWORK)
            == "unittest"
        ):
            header_template += "\nfrom unittest.mock import patch"

        if needs_mocking:
            limited_blocks.append(
                "class MockResponse:\n    def json(self): return {}\n    def raise_for_status(self): pass"
            )

        module_path = config.get("module_path")
        if not module_path:
            return [
                "# Test generation requires 'module_path' in config to import functions."
            ]

        header = f"{header_template}\nfrom {module_path} import {', '.join(function_names)}\n"

        file_body = _assemble_file(header, limited_blocks)

        if file_body.strip():
            return [file_body]
        return []


class JavaScriptTestGenerator(BaseTestGenerator):
    # Note: Requires 'esprima' package for JavaScript parsing. Install via `pip install esprima`.
    def generate(self, code: str, config: dict) -> List[str]:
        """
        Generates Jest or Mocha style tests for a given JavaScript code string.
        """
        cache_size = int(config.get("cache_size", 100))
        code_hash = hashlib.sha256(code.encode()).hexdigest()

        @lru_cache(maxsize=cache_size)
        def _generate_cached(code: str, cfg_key: str) -> Dict[str, List[str]]:
            cfg = json.loads(cfg_key)
            framework = cfg.get("test_framework", DEFAULT_JS_TEST_FRAMEWORK)
            module_system = cfg.get("module_system", "cjs")

            test_blocks: Dict[str, List[str]] = defaultdict(list)

            try:
                from esprima import parseScript
            except ImportError:
                logger.warning(
                    "esprima library not found. Skipping JavaScript test generation."
                )
                return {}

            try:
                if code not in self._parsed_trees:
                    self._parsed_trees[code] = parseScript(
                        code,
                        loc=True,
                        tolerant=True,
                        sourceType="module" if module_system == "esm" else "script",
                    )
                tree = self._parsed_trees[code]

                dependencies_to_mock = config.get(
                    "dependencies_to_mock", ["fetch", "axios"]
                )

                for node in getattr(tree, "body", []):
                    if getattr(node, "type", "") == "FunctionDeclaration":
                        fname = node.id.name

                        if framework == "jest":
                            test_blocks[fname].append(
                                f'describe("{fname}", () => {{\n'
                                f'  test("basic function test", () => {{\n'
                                f"    expect({fname}()).not.toBeNull();\n"
                                f"  }});\n"
                                f"}});"
                            )
                            if (
                                any(dep in code for dep in dependencies_to_mock)
                                and "fetch" in dependencies_to_mock
                            ):
                                test_blocks[fname].append(
                                    f'describe("{fname}", () => {{\n'
                                    f'  test("mocked fetch", () => {{\n'
                                    f"    global.fetch = jest.fn().mockResolvedValue({{ json: () => ({{}}) }});\n"
                                    f"    expect({fname}()).not.toBeNull();\n"
                                    f"  }});\n"
                                    f"}});"
                                )
                            if (
                                any(dep in code for dep in dependencies_to_mock)
                                and "axios" in dependencies_to_mock
                            ):
                                test_blocks[fname].append(
                                    f'describe("{fname}", () => {{\n'
                                    f'  test("mocked axios", () => {{\n'
                                    f'    const axios = require("axios");\n'
                                    f'    jest.spyOn(axios, "get").mockResolvedValue({{ data: {{}} }});\n'
                                    f"    expect({fname}()).not.toBeNull();\n"
                                    f"  }});\n"
                                    f"}});"
                                )
                        else:  # mocha
                            test_blocks[fname].append(
                                f'describe("{fname}", () => {{\n'
                                f'  it("should return a non-null value", () => {{\n'
                                f"    assert.notEqual({fname}(), null);\n"
                                f"  }});\n"
                                f"}});"
                            )
                            if (
                                any(dep in code for dep in dependencies_to_mock)
                                and "fetch" in dependencies_to_mock
                            ):
                                test_blocks[fname].append(
                                    f'describe("{fname}", () => {{\n'
                                    f'  it("mocked fetch", () => {{\n'
                                    f'    const sinon = require("sinon");\n'
                                    f"    global.fetch = sinon.stub().resolves({{ json: () => ({{}}) }});\n"
                                    f"    assert.notEqual({fname}(), null);\n"
                                    f"  }});\n"
                                    f"}});"
                                )
                            if (
                                any(dep in code for dep in dependencies_to_mock)
                                and "axios" in dependencies_to_mock
                            ):
                                test_blocks[fname].append(
                                    f'describe("{fname}", () => {{\n'
                                    f'  it("mocked axios", () => {{\n'
                                    f'    const sinon = require("sinon");\n'
                                    f'    const axios = require("axios");\n'
                                    f'    sinon.stub(axios, "get").resolves({{ data: {{}} }});\n'
                                    f"    assert.notEqual({fname}(), null);\n"
                                    f"  }});\n"
                                    f"}});"
                                )

                        if (
                            len(node.params) > 0
                            and getattr(node.params[0], "name", "") == "num"
                        ):
                            logger.info(
                                f"Generating numeric boundary tests for {fname}",
                                extra={"code_hash": code_hash},
                            )
                            test_blocks[fname].append(
                                f'describe("{fname}", () => {{\n'
                                f'  test("numeric boundary cases", () => {{\n'
                                f"    expect({fname}(0)).not.toBeNull();\n"
                                f"    expect({fname}(Number.MAX_SAFE_INTEGER)).not.toBeNull();\n"
                                f"  }});\n"
                                f"}});"
                            )
                        if (
                            len(node.params) > 0
                            and getattr(node.params[0], "name", "") == "text"
                        ):
                            logger.info(
                                f"Generating string boundary tests for {fname}",
                                extra={"code_hash": code_hash},
                            )
                            test_blocks[fname].append(
                                f'describe("{fname}", () => {{\n'
                                f'  test("string boundary cases", () => {{\n'
                                f'    expect({fname}("")).not.toBeNull();\n'
                                f'    expect({fname}(" ")).not.toBeNull();\n'
                                f'    expect({fname}("!@#$%^&*()")).not.toBeNull();\n'
                                f"  }});\n"
                                f"}});"
                            )
                    elif getattr(node, "type", "") == "VariableDeclaration":
                        for decl in node.declarations:
                            if getattr(
                                decl, "type", ""
                            ) == "VariableDeclarator" and getattr(
                                getattr(decl, "init", None), "type", ""
                            ) in (
                                "FunctionExpression",
                                "ArrowFunctionExpression",
                            ):
                                fname = decl.id.name
                                if fname:
                                    if framework == "jest":
                                        test_blocks[fname].append(
                                            f'describe("{fname}", () => {{\n'
                                            f'  test("basic function test", () => {{\n'
                                            f"    expect({fname}()).not.toBeNull();\n"
                                            f"  }});\n"
                                        )
                                    else:
                                        test_blocks[fname].append(
                                            f'describe("{fname}", () => {{\n'
                                            f'  it("should return a non-null value", () => {{\n'
                                            f"    assert.notEqual({fname}(), null);\n"
                                            f"  }});\n"
                                        )
            except Exception as e:
                logger.error(f"JS parse failed: {e}")
                raise ValueError(f"JS parsing failed: {e}")

            return test_blocks

        cfg_key = json.dumps(_normalize_cfg(config), sort_keys=True)
        try:
            blocks = _generate_cached(code, cfg_key)
        except (ImportError, ValueError) as e:
            return [f"// Error: {e}"]

        max_per_fn = int(config.get("max_tests_per_function", 2))
        limited_blocks = _limit_tests_per_function(blocks, max_per_fn)

        function_names = sorted(blocks.keys())
        if not function_names:
            return []

        framework = config.get("test_framework", DEFAULT_JS_TEST_FRAMEWORK)
        module_system = config.get("module_system", "cjs")

        header = ""
        if framework == "jest":
            if module_system == "esm":
                header += 'import { describe, test, expect } from "@jest/globals";\n'
            else:
                header += (
                    'const { describe, test, expect } = require("@jest/globals");\n'
                )
        else:  # mocha
            if module_system == "esm":
                header += 'import { strict as assert } from "node:assert";\nimport { describe, it } from "mocha";\n'
            else:
                header += 'const assert = require("assert");\nconst { describe, it } = require("mocha");\n'

        module_path = config.get("module_path")
        if not module_path:
            return [
                "// Test generation requires 'module_path' in config to import functions."
            ]

        if module_system == "esm":
            header += (
                f'import {{ {", ".join(function_names)} }} from "{module_path}";\n'
            )
        else:
            header += (
                f'const {{ {", ".join(function_names)} }} = require("{module_path}");\n'
            )

        file_body = _assemble_file(header, limited_blocks)

        if file_body.strip():
            return [file_body]
        return []


class TestGeneratorRegistry:
    def __init__(self):
        self.generators: Dict[str, BaseTestGenerator] = {}

    def register(self, language: str, generator: BaseTestGenerator) -> None:
        self.generators[language.lower()] = generator

    def get(self, language: str) -> Optional[BaseTestGenerator]:
        return self.generators.get(language.lower())


LANGUAGE_GENERATORS = TestGeneratorRegistry()
LANGUAGE_GENERATORS.register("python", PythonTestGenerator())
LANGUAGE_GENERATORS.register("javascript", JavaScriptTestGenerator())


class TypeScriptTestGenerator(JavaScriptTestGenerator):
    """TypeScript test generator.
    
    Currently inherits all behavior from JavaScriptTestGenerator since TypeScript
    and JavaScript use the same test frameworks (jest, mocha). TypeScript-specific
    handling (if needed) can be added in the future.
    """
    pass


LANGUAGE_GENERATORS.register("typescript", TypeScriptTestGenerator())


# ---------------------------------------------------------------------------
# Optional heavy dependencies — all imported lazily with no-op fallbacks
# so the module remains importable in every environment.
# ---------------------------------------------------------------------------

# LLMClient — shared transport layer for all LLM providers on the platform.
try:
    from self_fixing_engineer.arbiter.plugins.llm_client import LLMClient as _LLMClient

    _LLM_CLIENT_AVAILABLE = True
except ImportError:  # pragma: no cover
    _LLMClient = None  # type: ignore[assignment,misc]
    _LLM_CLIENT_AVAILABLE = False

# Tenacity — retry with exponential back-off (same pattern as LLMClient).
try:
    from tenacity import (
        retry as _tenacity_retry,
        retry_if_exception_type as _retry_if_exc_type,
        stop_after_attempt as _stop_after_attempt,
        wait_exponential as _wait_exponential,
    )

    _TENACITY_AVAILABLE = True
except ImportError:  # pragma: no cover
    _TENACITY_AVAILABLE = False

# Prometheus metrics via shared metrics_utils helper (thread-safe, no-op fallback).
try:
    from omnicore_engine.metrics_utils import get_or_create_metric as _get_or_create_metric
    from prometheus_client import Counter as _PCounter, Histogram as _PHistogram

    _XAI_CALL_TOTAL: Any = _get_or_create_metric(
        _PCounter,
        "xai_testgen_calls_total",
        "Total XAITestGenerationAPI.generate_tests() invocations",
        ("provider", "language", "status"),
    )
    _XAI_CALL_LATENCY: Any = _get_or_create_metric(
        _PHistogram,
        "xai_testgen_call_latency_seconds",
        "Latency of XAITestGenerationAPI LLM calls",
        ("provider", "language"),
        buckets=(0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0),
    )
    _XAI_PARSE_ERRORS: Any = _get_or_create_metric(
        _PCounter,
        "xai_testgen_parse_errors_total",
        "Total failures to parse the LLM JSON response in XAITestGenerationAPI",
        ("provider",),
    )
except Exception:  # pragma: no cover — metrics are best-effort
    class _DummyMetric:  # type: ignore[no-redef]
        def labels(self, **_: Any) -> "_DummyMetric": return self
        def inc(self, *_: Any) -> None: pass
        def observe(self, *_: Any) -> None: pass

    _XAI_CALL_TOTAL = _DummyMetric()
    _XAI_CALL_LATENCY = _DummyMetric()
    _XAI_PARSE_ERRORS = _DummyMetric()

# OpenTelemetry tracer — graceful no-op when the SDK is absent.
try:
    from self_fixing_engineer.arbiter.otel_config import (
        get_tracer_safe as _get_tracer_safe,
    )

    _xai_tracer: Any = _get_tracer_safe(__name__)
except ImportError:  # pragma: no cover
    class _NoOpSpan:
        def __enter__(self) -> "_NoOpSpan": return self
        def __exit__(self, *_: Any) -> None: pass
        def set_attribute(self, *_: Any, **__: Any) -> None: pass

    class _NoOpTracer:  # type: ignore[no-redef]
        def start_as_current_span(self, _: str, **__: Any) -> "_NoOpSpan":
            return _NoOpSpan()

    _xai_tracer = _NoOpTracer()  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Hard cap on code sent to the LLM to avoid prompt-injection and token overflow.
_XAI_MAX_CODE_CHARS: int = 8_000
# Secret patterns filtered from code before dispatch (case-insensitive).
_XAI_SECRET_PATTERNS: tuple = (
    "secret_key",
    "password",
    "api_key",
    "access_token",
    "private_key",
    "auth_token",
    "file://",
)

_XAI_TEST_GEN_PROMPT_TEMPLATE: str = """\
You are an expert {language} software engineer specialising in test coverage.

Your task: write comprehensive unit tests for the code below using {framework}.

Rules:
1. Return ONLY a JSON object: {{"tests": ["<test_source_1>", "<test_source_2>", ...]}}
2. Each element of "tests" must be a self-contained, runnable test function or block.
3. Cover the happy path, boundary conditions, and at least one error/exception case.
4. Do NOT include any explanation, markdown, or code fences outside the JSON.

Code ({language}):
```
{code}
```
"""

# Lock protecting lazy client initialisation.
_xai_init_lock = threading.Lock()


# ---------------------------------------------------------------------------
# XAITestGenerationAPI
# ---------------------------------------------------------------------------


class XAITestGenerationAPI:
    """AI-powered test generation using the platform's existing LLMClient.

    Reads configuration from environment variables at first use so the object
    can be instantiated at module load time without triggering network activity:

    * ``XAI_API_PROVIDER``    — LLM provider name (default: ``"openai"``).
    * ``XAI_API_KEY``         — API key for the provider.
    * ``XAI_API_MODEL``       — Model identifier (default: ``"gpt-4o"``).
    * ``XAI_RETRY_ATTEMPTS``  — Max tenacity retry attempts (default: ``3``).
    * ``XAI_CALL_TIMEOUT``    — Per-call timeout in seconds (default: ``60``).

    Operational features
    --------------------
    * **Thread-safe lazy initialisation** — the internal ``LLMClient`` is
      created at most once, protected by a module-level ``threading.Lock``.
    * **Circuit breaker** — mirrors the LLMClient pattern; consecutive failures
      trip the breaker and fast-fail subsequent calls to protect the provider.
    * **Tenacity retry** — transient network errors are retried with exponential
      back-off when ``tenacity`` is installed.
    * **Prometheus metrics** — call counts, latency, and parse errors are
      emitted to the shared registry.
    * **OpenTelemetry tracing** — each ``generate_tests()`` invocation creates
      a span annotated with provider, language, and outcome.
    * **Input sanitisation** — code is truncated to :data:`_XAI_MAX_CODE_CHARS`
      and screened for secret/credential patterns before dispatch.
    * **Graceful degradation** — when ``LLMClient`` is unavailable or no API
      key is configured, :meth:`generate_tests` raises ``NotImplementedError``
      so that :func:`_call_ai_for_tests` can skip silently.

    Circuit Breaker Parameters
    --------------------------
    The breaker trips after ``cb_threshold`` consecutive failures and re-opens
    after ``cb_timeout`` seconds.  Both are configurable at construction time.
    """

    def __init__(
        self,
        cb_threshold: int = 5,
        cb_timeout: float = 300.0,
    ) -> None:
        self._provider: str = os.environ.get("XAI_API_PROVIDER", "openai")
        self._api_key: Optional[str] = os.environ.get("XAI_API_KEY")
        self._model: str = os.environ.get("XAI_API_MODEL", "gpt-4o")
        self._retry_attempts: int = int(os.environ.get("XAI_RETRY_ATTEMPTS", "3"))
        self._call_timeout: int = int(os.environ.get("XAI_CALL_TIMEOUT", "60"))

        # Lazy client state
        self._client: Optional[Any] = None
        self._init_error: Optional[str] = None
        self._initialized: bool = False

        # Circuit breaker
        self._cb_threshold = cb_threshold
        self._cb_timeout = cb_timeout
        self._cb_failures: int = 0
        self._cb_state: str = "closed"  # closed | open | half-open
        self._cb_last_failure: Optional[float] = None
        self._cb_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Circuit breaker helpers
    # ------------------------------------------------------------------

    def _check_circuit_breaker(self) -> None:
        with self._cb_lock:
            if self._cb_state == "open":
                elapsed = time.time() - (self._cb_last_failure or 0.0)
                if elapsed >= self._cb_timeout:
                    self._cb_state = "half-open"
                    logger.info(
                        "XAITestGenerationAPI: circuit breaker → half-open.",
                        extra={"elapsed_s": round(elapsed, 1), "provider": self._provider},
                    )
                else:
                    raise RuntimeError(
                        f"XAITestGenerationAPI circuit breaker is OPEN "
                        f"(retry after {self._cb_timeout - elapsed:.0f}s)."
                    )

    def _record_cb_success(self) -> None:
        with self._cb_lock:
            if self._cb_state in ("half-open", "open"):
                logger.info(
                    "XAITestGenerationAPI: circuit breaker → closed.",
                    extra={"provider": self._provider},
                )
            self._cb_failures = 0
            self._cb_state = "closed"

    def _record_cb_failure(self) -> None:
        with self._cb_lock:
            self._cb_failures += 1
            self._cb_last_failure = time.time()
            if self._cb_failures >= self._cb_threshold:
                self._cb_state = "open"
                logger.warning(
                    "XAITestGenerationAPI: circuit breaker tripped → OPEN.",
                    extra={
                        "consecutive_failures": self._cb_failures,
                        "provider": self._provider,
                    },
                )

    # ------------------------------------------------------------------
    # Lazy initialisation (thread-safe, one-shot)
    # ------------------------------------------------------------------

    def _ensure_client(self) -> None:
        """Initialise LLMClient at most once; re-raise stored errors on retry."""
        # Fast-path: already initialised (success or permanent failure).
        if self._initialized:
            if self._init_error:
                raise NotImplementedError(self._init_error)
            return

        with _xai_init_lock:
            # Double-checked locking: re-test after acquiring the lock.
            if self._initialized:
                if self._init_error:
                    raise NotImplementedError(self._init_error)
                return

            try:
                self._do_init()
            finally:
                self._initialized = True

    def _do_init(self) -> None:
        """Perform the actual initialisation (called exactly once)."""
        if not _LLM_CLIENT_AVAILABLE:
            self._init_error = (
                "LLMClient is unavailable — ensure self_fixing_engineer dependencies "
                "are installed (aiohttp, openai, anthropic, …)."
            )
            return

        if not self._api_key:
            self._init_error = (
                "No API key configured for AI test generation. "
                "Set the XAI_API_KEY environment variable."
            )
            return

        try:
            self._client = _LLMClient(  # type: ignore[call-arg]
                provider=self._provider,
                api_key=self._api_key,
                model=self._model,
                timeout=self._call_timeout,
                retry_attempts=self._retry_attempts,
            )
            logger.info(
                "XAITestGenerationAPI: LLMClient initialised.",
                extra={"provider": self._provider, "model": self._model},
            )
        except Exception as exc:
            self._init_error = (
                f"Failed to initialise LLMClient for provider={self._provider!r}: {exc}"
            )
            logger.error(
                "XAITestGenerationAPI: initialisation failed.",
                exc_info=True,
                extra={"provider": self._provider, "error": str(exc)},
            )

    # ------------------------------------------------------------------
    # Input sanitisation
    # ------------------------------------------------------------------

    @staticmethod
    def _sanitize_code(code: str) -> str:
        """Truncate oversized snippets and screen for credential patterns.

        Returns the sanitised code string.  Raises ``ValueError`` if a
        secret pattern is detected so callers can log and skip the call
        rather than transmitting sensitive data to an external API.
        """
        code_lower = code.lower()
        for pattern in _XAI_SECRET_PATTERNS:
            if pattern in code_lower:
                raise ValueError(
                    f"Code contains a potential secret pattern ({pattern!r}); "
                    "refusing to dispatch to external LLM API."
                )
        if len(code) > _XAI_MAX_CODE_CHARS:
            code = code[:_XAI_MAX_CODE_CHARS] + "\n# ... (truncated for LLM dispatch)"
        return code

    # ------------------------------------------------------------------
    # Core async invocation
    # ------------------------------------------------------------------

    async def _invoke_llm(self, prompt: str) -> str:
        """Dispatch *prompt* to the configured LLM and return the raw text response.

        Applies tenacity retry when the library is available.
        """
        if self._client is None:
            raise NotImplementedError("LLMClient not initialised.")

        if _TENACITY_AVAILABLE:
            @_tenacity_retry(
                stop=_stop_after_attempt(self._retry_attempts),
                wait=_wait_exponential(multiplier=1.5, min=1, max=30),
                retry=_retry_if_exc_type((OSError, TimeoutError)),
                reraise=True,
            )
            async def _with_retry() -> str:
                return await self._client.generate(prompt)  # type: ignore[union-attr]

            return await _with_retry()
        else:
            return await self._client.generate(prompt)  # type: ignore[union-attr]

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_response(raw: str, provider: str) -> Dict[str, Any]:
        """Extract a ``{"tests": [...]}`` dict from a raw LLM response string.

        Attempts strict JSON parsing first; falls back to locating the first
        complete JSON object via regex before giving up and returning an empty
        list.
        """
        # Strict parse
        try:
            parsed = json.loads(raw)
            return {"tests": parsed.get("tests", [])}
        except (json.JSONDecodeError, TypeError):
            pass

        # Lenient extraction — handles models that wrap JSON in prose
        import re as _re

        match = _re.search(r"\{.*\}", raw, _re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group())
                return {"tests": parsed.get("tests", [])}
            except (json.JSONDecodeError, TypeError):
                pass

        # Give up
        _XAI_PARSE_ERRORS.labels(provider=provider).inc()
        logger.warning(
            "XAITestGenerationAPI: LLM response could not be parsed as JSON; "
            "returning empty test list.",
            extra={"provider": provider, "response_preview": raw[:200]},
        )
        return {"tests": []}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_tests(
        self,
        code: str,
        language: str,
        test_framework: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate unit tests for *code* via the configured LLM provider.

        Parameters
        ----------
        code:
            Source code to generate tests for.  Must not exceed
            :data:`_XAI_MAX_CODE_CHARS` characters after sanitisation.
        language:
            Programming language of *code* (e.g. ``"python"``,
            ``"javascript"``).
        test_framework:
            Target testing framework.  Defaults to ``"pytest"`` for Python
            and ``"jest"`` for all other languages.

        Returns
        -------
        dict
            ``{"tests": [<test_source_string>, ...]}``

        Raises
        ------
        NotImplementedError
            When ``LLMClient`` is unavailable or no API key is configured.
        ValueError
            When *code* contains a detected secret pattern.
        RuntimeError
            When the circuit breaker is open.
        """
        self._ensure_client()
        self._check_circuit_breaker()

        framework = test_framework or (
            "pytest" if language == "python" else "jest"
        )

        sanitized = self._sanitize_code(code)
        prompt_hash = hashlib.sha256(sanitized.encode()).hexdigest()[:12]

        prompt = _XAI_TEST_GEN_PROMPT_TEMPLATE.format(
            language=language,
            framework=framework,
            code=sanitized,
        )

        with _xai_tracer.start_as_current_span("xai_testgen.generate_tests") as span:
            span.set_attribute("xai.provider", self._provider)
            span.set_attribute("xai.model", self._model)
            span.set_attribute("xai.language", language)
            span.set_attribute("xai.framework", framework)
            span.set_attribute("xai.prompt_hash", prompt_hash)

            t0 = time.time()
            try:
                raw_response = self._run_async(self._invoke_llm(prompt))
                elapsed = time.time() - t0

                _XAI_CALL_LATENCY.labels(
                    provider=self._provider, language=language
                ).observe(elapsed)
                _XAI_CALL_TOTAL.labels(
                    provider=self._provider, language=language, status="success"
                ).inc()

                result = self._parse_response(raw_response, self._provider)
                self._record_cb_success()

                span.set_attribute("xai.status", "success")
                span.set_attribute("xai.test_count", len(result.get("tests", [])))
                logger.info(
                    "XAITestGenerationAPI: tests generated.",
                    extra={
                        "provider": self._provider,
                        "language": language,
                        "test_count": len(result.get("tests", [])),
                        "latency_s": round(elapsed, 3),
                        "prompt_hash": prompt_hash,
                    },
                )
                return result

            except (NotImplementedError, ValueError, RuntimeError):
                # Non-retryable / configuration errors — do not trip breaker.
                _XAI_CALL_TOTAL.labels(
                    provider=self._provider, language=language, status="config_error"
                ).inc()
                span.set_attribute("xai.status", "config_error")
                raise

            except Exception as exc:
                elapsed = time.time() - t0
                _XAI_CALL_TOTAL.labels(
                    provider=self._provider, language=language, status="error"
                ).inc()
                self._record_cb_failure()
                span.set_attribute("xai.status", "error")
                span.set_attribute("xai.error", str(exc))
                logger.error(
                    "XAITestGenerationAPI: LLM call failed.",
                    exc_info=True,
                    extra={
                        "provider": self._provider,
                        "language": language,
                        "latency_s": round(elapsed, 3),
                        "prompt_hash": prompt_hash,
                    },
                )
                raise

    # ------------------------------------------------------------------
    # Async/sync bridge
    # ------------------------------------------------------------------

    @staticmethod
    def _run_async(coro: Any) -> str:
        """Bridge between the sync public API and the async LLMClient.

        Selects the appropriate execution strategy based on whether an event
        loop is already running (e.g. inside an async web handler).
        """
        import asyncio
        import concurrent.futures

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        if loop.is_running():
            # We are inside an async context (FastAPI, pytest-asyncio, etc.).
            # Dispatch to a private thread pool to avoid blocking the loop.
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result(timeout=120)
        else:
            return loop.run_until_complete(coro)


# Module-level singleton — lazily configured from env vars at first call.
xai_api: Any = XAITestGenerationAPI()


def _call_ai_for_tests(code: str, language: str, config: dict) -> List[str]:
    """Attempt AI-assisted test generation; return ``[]`` on any failure.

    This function is the sole consumer of :data:`xai_api` in this module and
    is the authoritative place where AI generation errors are absorbed so that
    the caller (:func:`generate_tests`) can degrade to rule-based generation.

    Parameters
    ----------
    code:
        Source code to generate tests for.
    language:
        Programming language of *code*.
    config:
        Generation configuration dict.  Relevant keys:

        * ``"retries"`` — if already > 2, skip to avoid infinite recursion.
        * ``"test_framework"`` — forwarded to ``generate_tests()``.

    Returns
    -------
    list[str]
        Generated test source strings, or ``[]`` when generation is
        unavailable, blocked by circuit breaker, or fails.
    """
    retries = config.get("retries", 0)
    if retries > 2:
        logger.warning(
            "_call_ai_for_tests: max retries exceeded; skipping AI generation.",
            extra={"language": language, "retries": retries},
        )
        return []

    code_hash = hashlib.sha256(code.encode()).hexdigest()[:12]

    try:
        response = xai_api.generate_tests(code, language, config.get("test_framework"))
        tests = response.get("tests", [])
        logger.info(
            "_call_ai_for_tests: AI generation succeeded.",
            extra={
                "test_count": len(tests),
                "language": language,
                "code_hash": code_hash,
            },
        )
        return tests

    except NotImplementedError:
        logger.debug(
            "_call_ai_for_tests: AI test generation not configured; skipping.",
            extra={"language": language},
        )
        return []

    except ValueError as exc:
        # Code contained a secret pattern — never retry.
        logger.warning(
            "_call_ai_for_tests: code blocked by sanitisation filter.",
            extra={"language": language, "reason": str(exc), "code_hash": code_hash},
        )
        return []

    except RuntimeError as exc:
        # Circuit breaker open.
        logger.warning(
            "_call_ai_for_tests: circuit breaker is open; skipping AI generation.",
            extra={"language": language, "reason": str(exc)},
        )
        return []

    except Exception as exc:
        logger.error(
            "_call_ai_for_tests: unexpected error during AI generation.",
            exc_info=True,
            extra={"language": language, "code_hash": code_hash, "error": str(exc)},
        )
        return []


def generate_tests(
    code: str, language: str = "python", config: dict | None = None
) -> Dict[str, Union[str, List[str], int, Dict[str, Any]]]:
    """
    Generates tests for a given code string using a specified language and configuration.
    This function acts as the main entry point for the module.

    Args:
        code (str): The source code to generate tests for.
        language (str): The programming language of the source code.
        config (Optional[dict]): A dictionary of configuration options.

    Returns:
        Dict[str, Union[str, List[str], int, Dict[str, Any]]]: A dictionary
        containing the generation status, tests, and metrics.
    """
    start = time.time()
    config = config or {}
    language = (language or "").lower()

    if len(code) > 10_000:
        return {
            "status": "error",
            "tests": [],
            "count": 0,
            "error": "Input code too large",
            "metrics": {"generation_time": time.time() - start},
        }

    code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()

    dangerous_constructs = [
        "__import__",
        "eval",
        "exec",
        "open(",
        "os.system",
        "subprocess.run",
    ]
    if config.get("strict_source_filter", False):
        if any(x in code for x in dangerous_constructs):
            logger.warning(
                "Code contains potentially dangerous constructs.",
                extra={"code_hash": code_hash},
            )
            return {
                "status": "error",
                "tests": [],
                "count": 0,
                "error": "Code contains potentially dangerous constructs",
                "metrics": {"generation_time": time.time() - start},
            }

    if language not in SUPPORTED_LANGUAGES:
        return {
            "status": "error",
            "tests": [],
            "count": 0,
            "error": f"Language '{language}' is not supported. Supported: {list(SUPPORTED_LANGUAGES)}.",
            "metrics": {"generation_time": time.time() - start},
        }

    framework = config.get(
        "test_framework",
        (
            DEFAULT_PYTHON_TEST_FRAMEWORK
            if language == "python"
            else DEFAULT_JS_TEST_FRAMEWORK
        ),
    )
    if framework not in SUPPORTED_FRAMEWORKS.get(language, frozenset()):
        return {
            "status": "error",
            "tests": [],
            "count": 0,
            "error": f"Unsupported test framework '{framework}' for language '{language}'.",
            "metrics": {"generation_time": time.time() - start},
        }

    tests: List[str] = []
    error_reason: Optional[str] = None

    module_path = config.get("module_path")
    if module_path:
        if config.get("strict_source_filter", False) and any(
            x in module_path for x in dangerous_constructs
        ):
            logger.error(
                "Invalid module_path due to dangerous constructs.",
                extra={"code_hash": code_hash, "module_path": module_path},
            )
            return {
                "status": "error",
                "tests": [],
                "count": 0,
                "error": "Invalid module_path due to dangerous constructs",
                "metrics": {"generation_time": time.time() - start},
            }
        safe_name = module_path.replace(".", "_").replace("/", "_")
        if not safe_name.isidentifier():
            logger.error(
                "Invalid module_path due to non-identifier characters.",
                extra={"code_hash": code_hash, "module_path": module_path},
            )
            return {
                "status": "error",
                "tests": [],
                "count": 0,
                "error": "Invalid module_path due to non-identifier characters",
                "metrics": {"generation_time": time.time() - start},
            }
        try:
            resolved_path = os.path.realpath(module_path)
            # Security: Verify resolved_path is within project boundaries
            project_root = config.get("project_root", os.getcwd())
            project_root_resolved = os.path.realpath(project_root)
            if not resolved_path.startswith(project_root_resolved):
                logger.error(
                    "Module path is outside project boundaries.",
                    extra={
                        "code_hash": code_hash,
                        "module_path": module_path,
                        "resolved": resolved_path,
                    },
                )
                return {
                    "status": "error",
                    "tests": [],
                    "count": 0,
                    "error": "Module path must be within project boundaries",
                    "metrics": {"generation_time": time.time() - start},
                }
            if not os.path.exists(resolved_path):
                logger.error(
                    "Module path does not exist.",
                    extra={"code_hash": code_hash, "module_path": module_path},
                )
                return {
                    "status": "error",
                    "tests": [],
                    "count": 0,
                    "error": "Module path does not exist",
                    "metrics": {"generation_time": time.time() - start},
                }
        except OSError:
            logger.error(
                "Invalid module path.",
                extra={"code_hash": code_hash, "module_path": module_path},
            )
            return {
                "status": "error",
                "tests": [],
                "count": 0,
                "error": "Invalid module path",
                "metrics": {"generation_time": time.time() - start},
            }

    ai_tests = []
    if config.get("use_ai", True):
        try:
            ai_tests = _call_ai_for_tests(code, language, config)
            if ai_tests:
                logger.info("Successfully generated tests via AI.")
        except Exception as e:
            logger.error(f"AI generation failed: {e}", extra={"code_hash": code_hash})

    if ai_tests:
        tests.extend(ai_tests)

    if not tests and config.get("fallback_to_internal", True):
        gen = LANGUAGE_GENERATORS.get(language)
        try:
            internal_tests = gen.generate(code, config) if gen else []
            tests.extend(internal_tests)
        except ValueError as e:
            details = (
                e.args[1] if len(e.args) > 1 and isinstance(e.args[1], dict) else {}
            )
            error_reason = f"Internal generator error: {e.args[0]}"
            logger.error(
                error_reason, extra={"code_hash": code_hash, "details": details}
            )
            return {
                "status": "error",
                "tests": [],
                "count": 0,
                "error": error_reason,
                "error_details": details,
                "metrics": {"generation_time": time.time() - start},
            }
        except Exception:
            error_reason = "An unexpected internal error occurred."
            logger.exception(error_reason)
            return {
                "status": "error",
                "tests": [],
                "count": 0,
                "error": error_reason,
                "metrics": {"generation_time": time.time() - start},
            }

    valid_tests = [
        t for t in tests if t.strip() and not t.strip().startswith(("//", "#"))
    ]
    status = "success" if valid_tests else "error"
    if not valid_tests:
        error_reason = error_reason or "No valid tests were generated."

    dur = time.time() - start

    logger.info(
        f"Test generation request processed. Source hash: {code_hash}, Test count: {len(valid_tests)}, Status: {status}"
    )

    metrics = {
        "generation_time": dur,
        "test_count": len(valid_tests),
        "gen_plugins_generated_tests_total": {
            "lang": language,
            "status": status,
            "value": len(valid_tests),
        },
        "gen_plugins_latency_seconds": {"lang": language, "value": dur},
    }

    response = {
        "status": status,
        "tests": tests,
        "count": len(valid_tests),
        "metrics": metrics,
    }
    if status == "error":
        response["error"] = error_reason

    return response


__all__ = [
    "PythonTestGenerator",
    "JavaScriptTestGenerator",
    "XAITestGenerationAPI",
    "generate_tests",
    "_call_ai_for_tests",
    "LANGUAGE_GENERATORS",
    "SUPPORTED_LANGUAGES",
    "SUPPORTED_FRAMEWORKS",
    "logger",
    "_limit_tests_per_function",
]
