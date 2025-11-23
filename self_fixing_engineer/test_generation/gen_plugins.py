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
import time
from collections import defaultdict
from functools import lru_cache
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

SUPPORTED_LANGUAGES = frozenset({"python", "javascript"})
SUPPORTED_FRAMEWORKS = {
    "python": frozenset({"unittest", "pytest"}),
    "javascript": frozenset({"jest", "mocha"}),
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


# Placeholder stub for AI API - to be implemented
class _XAIAPIStub:
    """Stub for external AI API - not yet implemented."""

    def generate_tests(
        self, code: str, language: str, test_framework: Optional[str] = None
    ) -> Dict[str, Any]:
        """Stub method for AI-based test generation."""
        raise NotImplementedError("AI-based test generation is not yet implemented")


xai_api = _XAIAPIStub()


def _call_ai_for_tests(code: str, language: str, config: dict) -> List[str]:
    retries = config.get("retries", 0)
    if retries > 2:
        logger.warning("Max retries exceeded for AI generation.")
        return []
    if "secret_key" in code.lower() or "file://" in code or "file path" in code.lower():
        logger.warning("Input code filtered for sensitive content.")
        return []
    try:
        # Placeholder for actual API call
        # You'll need to define `xai_api` and its `generate_tests` method
        response = xai_api.generate_tests(code, language, config.get("test_framework"))
        logger.info(
            "Successfully generated tests via AI.",
            extra={"test_count": len(response.get("tests", []))},
        )
        return response.get("tests", [])
    except Exception as e:
        code_hash = hashlib.sha256(code.encode()).hexdigest()
        logger.error(f"AI generation failed: {e}", extra={"code_hash": code_hash})
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
    "generate_tests",
    "_call_ai_for_tests",
    "LANGUAGE_GENERATORS",
    "SUPPORTED_LANGUAGES",
    "SUPPORTED_FRAMEWORKS",
    "logger",
    "_limit_tests_per_function",
]
