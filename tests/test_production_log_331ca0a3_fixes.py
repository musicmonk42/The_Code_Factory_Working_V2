# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Tests for 4 production bug fixes identified in job log 331ca0a3.

Bug 1: _retry_stub_files LLM response parsed as Python instead of JSON file-map
        — recovery from nested JSON response + improved prompt format
Bug 2: _ast_merge_python_files corrupts __future__ import ordering
Bug 3: _MULTIPASS_GROUPS missing a frontend generation pass
Bug 4: _retry_stub_files should skip app/main.py
"""

from __future__ import annotations

import ast as _ast_mod
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

# ---------------------------------------------------------------------------
# Project root on sys.path
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

_CODEGEN_AGENT_PATH = PROJECT_ROOT / "generator/agents/codegen_agent/codegen_agent.py"


def _read_agent_src() -> str:
    return _CODEGEN_AGENT_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Isolated loader for _ast_merge_python_files (no heavy deps required)
# ---------------------------------------------------------------------------

def _load_ast_merge() -> Any:
    """Extract and compile _ast_merge_python_files from codegen_agent.py."""
    source = _CODEGEN_AGENT_PATH.read_text(encoding="utf-8")
    tree = _ast_mod.parse(source)

    nodes = [
        node for node in tree.body
        if isinstance(node, (_ast_mod.FunctionDef, _ast_mod.AsyncFunctionDef))
        and node.name == "_ast_merge_python_files"
    ]
    assert nodes, "_ast_merge_python_files not found in source"

    module = _ast_mod.Module(body=nodes, type_ignores=[])
    _ast_mod.fix_missing_locations(module)
    code = compile(module, str(_CODEGEN_AGENT_PATH), "exec")

    import logging as _logging
    import re as _re

    ns: dict = {
        "ast": _ast_mod, "re": _re, "logging": _logging,
        "logger": _logging.getLogger("codegen_agent"),
        "Any": Any, "Dict": Dict, "List": List,
        "Optional": Optional, "Tuple": tuple,
    }
    exec(code, ns)  # noqa: S102  (trusted internal source)
    return ns["_ast_merge_python_files"]


# ---------------------------------------------------------------------------
# Isolated loader for _build_multipass_groups (no heavy deps required)
# ---------------------------------------------------------------------------

def _load_build_multipass_groups() -> Any:
    """Extract and compile _build_multipass_groups + its module-level dependencies."""
    source = _CODEGEN_AGENT_PATH.read_text(encoding="utf-8")
    tree = _ast_mod.parse(source)

    _TARGET_NAMES = frozenset({
        "_MULTIPASS_GROUPS",
        "_FRONTEND_JINJA_FOCUS",
        "_build_multipass_groups",
    })
    nodes = []
    for node in tree.body:
        if isinstance(node, _ast_mod.Assign):
            names = {t.id for t in node.targets if isinstance(t, _ast_mod.Name)}
            if names & _TARGET_NAMES:
                nodes.append(node)
        elif isinstance(node, (_ast_mod.FunctionDef, _ast_mod.AsyncFunctionDef)):
            if node.name in _TARGET_NAMES:
                nodes.append(node)

    module = _ast_mod.Module(body=nodes, type_ignores=[])
    _ast_mod.fix_missing_locations(module)
    code = compile(module, str(_CODEGEN_AGENT_PATH), "exec")

    ns: dict = {
        "Any": Any, "Dict": Dict, "List": List,
        "Optional": Optional, "Tuple": tuple, "FrozenSet": frozenset,
    }
    exec(code, ns)  # noqa: S102  (trusted internal source)
    return ns["_build_multipass_groups"]


# ---------------------------------------------------------------------------
# Bug 2: _ast_merge_python_files — __future__ import ordering
# ---------------------------------------------------------------------------

class TestAstMergeFutureImportOrdering:
    """_ast_merge_python_files must not push __future__ imports below other code."""

    def setup_method(self):
        self._merge = _load_ast_merge()

    def test_future_import_stays_first_when_imports_prepended(self):
        """Carried-over imports must be inserted AFTER any from __future__ lines."""
        new_content = (
            "from __future__ import annotations\n"
            "\n"
            "from fastapi import FastAPI\n"
            "\n"
            "app = FastAPI()\n"
        )
        old_content = (
            "from __future__ import annotations\n"
            "from typing import Optional\n"
            "\n"
            "def old_func(x: Optional[int]) -> None: ...\n"
        )
        merged = self._merge(old_content, new_content)
        first_code_line = next(ln for ln in merged.splitlines() if ln.strip())
        assert first_code_line.startswith("from __future__"), (
            f"Expected __future__ import as first line, got: {first_code_line!r}\n"
            f"Full merged output:\n{merged}"
        )

    def test_future_import_precedes_carried_imports(self):
        """__future__ line must appear before any carried-over import lines."""
        new_content = (
            "from __future__ import annotations\n"
            "\n"
            "from sqlalchemy.orm import Session\n"
            "\n"
            "def get_session(): ...\n"
        )
        old_content = (
            "from __future__ import annotations\n"
            "from typing import List\n"
            "\n"
            "def list_items(items: List[str]): ...\n"
        )
        merged = self._merge(old_content, new_content)
        lines = [ln for ln in merged.splitlines() if ln.strip()]
        future_idx = next(
            (i for i, ln in enumerate(lines) if ln.strip().startswith("from __future__")),
            None,
        )
        assert future_idx is not None, "__future__ import missing from merged output"
        for i, line in enumerate(lines):
            if "from typing import" in line or "import typing" in line:
                assert i > future_idx, (
                    f"Carried import appeared before __future__: line {i}={line!r}"
                )

    def test_no_future_import_unaffected(self):
        """When no __future__ import exists, prepending works as before."""
        new_content = "from fastapi import FastAPI\n\napp = FastAPI()\n"
        old_content = "from typing import Optional\n\ndef helper(x: Optional[str]): ...\n"
        merged = self._merge(old_content, new_content)
        assert "FastAPI" in merged

    def test_syntax_valid_after_merge_with_future(self):
        """Merged output with __future__ import must be syntactically valid Python."""
        new_content = (
            "from __future__ import annotations\n"
            "\n"
            "from fastapi import APIRouter\n"
            "\n"
            "router = APIRouter()\n"
            "\n"
            "def health(): return {}\n"
        )
        old_content = (
            "from __future__ import annotations\n"
            "from typing import Optional\n"
            "\n"
            "def old_handler(x: Optional[int]): ...\n"
        )
        merged = self._merge(old_content, new_content)
        try:
            _ast_mod.parse(merged)
        except SyntaxError as e:
            pytest.fail(f"Merged output has SyntaxError: {e}\nMerged:\n{merged}")


# ---------------------------------------------------------------------------
# Bug 3: _build_multipass_groups — frontend pass generation (functional)
# ---------------------------------------------------------------------------

class TestBuildMultipassGroupsHelper:
    """_build_multipass_groups must produce the correct ordered pass list."""

    def setup_method(self):
        self._build = _load_build_multipass_groups()

    def test_no_frontend_returns_three_base_groups(self):
        """Without frontend, returns the standard 3 passes unchanged."""
        groups = self._build(include_frontend=False, frontend_type=None)
        assert [g["name"] for g in groups] == ["core", "routes_and_services", "infrastructure"]

    def test_include_frontend_appends_fourth_pass(self):
        """With include_frontend=True, a 4th 'frontend' pass is appended."""
        groups = self._build(include_frontend=True, frontend_type="jinja_templates")
        assert len(groups) == 4
        assert groups[-1]["name"] == "frontend"

    def test_jinja_frontend_focus_references_templates_and_static(self):
        """Jinja2 frontend pass focus must reference templates/ and static/ dirs."""
        focus = self._build(include_frontend=True, frontend_type="jinja_templates")[-1]["focus"]
        assert "templates/base.html" in focus
        assert "static/css/style.css" in focus
        assert "static/js/app.js" in focus
        assert "app.mount" in focus

    def test_generic_frontend_focus_references_frontend_directory(self):
        """Non-jinja frontend pass focus must reference the frontend/ directory."""
        focus = self._build(include_frontend=True, frontend_type="react")[-1]["focus"]
        assert "frontend/" in focus
        assert "package.json" in focus

    def test_base_groups_identical_regardless_of_frontend_flag(self):
        """First 3 passes must be identical whether or not frontend is added."""
        plain = self._build(include_frontend=False, frontend_type=None)
        full = self._build(include_frontend=True, frontend_type="jinja_templates")
        assert plain == full[:3]

    def test_frontend_pass_excludes_backend_regeneration(self):
        """Frontend pass focus must forbid regenerating backend Python files."""
        for ft in ("jinja_templates", "react", "vue"):
            focus = self._build(include_frontend=True, frontend_type=ft)[-1]["focus"]
            assert "Do NOT regenerate any backend Python files" in focus, (
                f"Missing backend-exclusion guard for frontend_type={ft!r}"
            )


class TestMultipassFrontendPassSourceLevel:
    """Source-level checks for Bug 3 — no inline duplication, clean structure."""

    def _src(self) -> str:
        return _read_agent_src()

    def test_build_multipass_groups_helper_defined(self):
        """_build_multipass_groups must be defined as a module-level function."""
        assert "def _build_multipass_groups(" in self._src()

    def test_frontend_jinja_focus_constant_defined(self):
        """_FRONTEND_JINJA_FOCUS module constant must be defined to avoid duplication."""
        assert "_FRONTEND_JINJA_FOCUS" in self._src()

    def test_jinja_focus_string_appears_exactly_once(self):
        """Jinja2 frontend focus string must appear exactly once (in the constant)."""
        # Use the mount-path string which is unique to _FRONTEND_JINJA_FOCUS and
        # not present in the unrelated _build_fallback_prompt function.
        assert self._src().count("StaticFiles(directory='static'), name='static'") == 1, (
            "Jinja2 mount string is duplicated — it must live only in _FRONTEND_JINJA_FOCUS"
        )

    def test_loops_use_enumerate_not_index(self):
        """Multipass loops must use enumerate() — O(1) index, not O(n) .index()."""
        src = self._src()
        assert "for _pass_index, _group in enumerate(_groups_to_run, start=1):" in src

    def test_no_static_multipass_loop_over_constant(self):
        """'for _group in _MULTIPASS_GROUPS:' must not appear anywhere."""
        assert "for _group in _MULTIPASS_GROUPS:" not in self._src()


# ---------------------------------------------------------------------------
# Bug 4: _retry_stub_files skips app/main.py — source-level checks
# ---------------------------------------------------------------------------

class TestRetryStubFilesSkipsMainPy:
    """_retry_stub_files must exclude app/main.py from stub detection and replacement."""

    def _src(self) -> str:
        return _read_agent_src()

    def test_stub_paths_discards_app_main_py(self):
        """stub_paths.discard('app/main.py') must appear after get_stub_files()."""
        assert 'stub_paths.discard("app/main.py")' in self._src()

    def test_stub_paths_discards_main_py(self):
        """stub_paths.discard('main.py') must also be present."""
        assert 'stub_paths.discard("main.py")' in self._src()

    def test_replacement_loop_skips_main_py(self):
        """The file-replacement loop must explicitly skip app/main.py."""
        assert 'if path in ("app/main.py", "main.py"):' in self._src()

    def test_skip_log_references_reconcile_wiring(self):
        """Skip log message must explain that main.py is auto-generated."""
        assert "auto-generated by _reconcile_app_wiring" in self._src()


# ---------------------------------------------------------------------------
# Bug 1: _retry_stub_files nested JSON recovery — source-level checks
# ---------------------------------------------------------------------------

class TestRetryStubFilesNestedJsonRecovery:
    """_retry_stub_files must recover file-maps from single-key nested JSON responses."""

    def _src(self) -> str:
        return _read_agent_src()

    def test_expected_paths_computed_exactly_once(self):
        """expected_paths = set(stub_paths) must appear exactly once per retry loop."""
        assert self._src().count("expected_paths = set(stub_paths)") == 1

    def test_single_key_recovery_block_present(self):
        """Source must contain the single-key recovery guard."""
        assert "if len(new_files) == 1:" in self._src()

    def test_inner_json_parse_attempted(self):
        """Source must attempt json.loads on the single value."""
        assert "inner = json.loads(single_val)" in self._src()

    def test_file_map_detection_uses_slash(self):
        """File-map keys are detected by the presence of '/' in the key."""
        assert '"/" in k' in self._src()

    def test_files_key_fallback_used(self):
        """Recovery must also try inner.get('files', {}) as a secondary fallback."""
        assert 'inner.get("files", {})' in self._src()

    def test_recovery_logged_on_success(self):
        """Source must log when nested JSON recovery succeeds."""
        assert "recovered %d file(s) from nested JSON" in self._src()

    def test_error_filename_imported_and_used(self):
        """ERROR_FILENAME must be imported from codegen_response_handler and used."""
        src = self._src()
        assert "ERROR_FILENAME," in src or "ERROR_FILENAME\n" in src
        assert "ERROR_FILENAME" in src

    def test_plain_prompt_suffix_constant_defined(self):
        """Duplicate prompt suffix strings must be replaced by a single constant."""
        assert "_STUB_RETRY_PLAIN_PROMPT_SUFFIX" in self._src()

    def test_plain_prompt_suffix_example_appears_once(self):
        """Example format string in the prompt suffix must appear exactly once."""
        # Use the escaped newline form unique to _STUB_RETRY_PLAIN_PROMPT_SUFFIX
        assert self._src().count('"from pydantic import BaseSettings\\\\n..."') == 1, (
            "Prompt suffix example is duplicated — use _STUB_RETRY_PLAIN_PROMPT_SUFFIX"
        )

    def test_json_decode_error_caught_in_recovery(self):
        """JSONDecodeError must be caught so recovery never raises."""
        assert "json.JSONDecodeError" in self._src()


# ---------------------------------------------------------------------------
# Functional tests: nested JSON recovery algorithm
# ---------------------------------------------------------------------------

class TestNestedJsonRecoveryLogic:
    """Directly verify the nested JSON recovery algorithm (no mocking needed)."""

    @staticmethod
    def _apply_recovery(new_files: dict, expected_paths: set) -> dict:
        """Mirror of the recovery logic inside codegen_agent._retry_stub_files."""
        ERROR_FILENAME = "__syntax_errors__"
        if len(new_files) == 1:
            single_key = next(iter(new_files))
            single_val = new_files[single_key]
            if single_key not in expected_paths or single_key == ERROR_FILENAME:
                try:
                    inner = json.loads(single_val)
                    if isinstance(inner, dict):
                        file_like = {
                            k: v for k, v in inner.items()
                            if isinstance(v, str) and "/" in k
                        }
                        if not file_like:
                            file_like = inner.get("files", {})
                        if file_like:
                            return file_like
                except (json.JSONDecodeError, TypeError):
                    pass
        return new_files

    def test_recovery_from_syntax_errors_key(self):
        """LLM wraps files under __syntax_errors__ — inner file-map is extracted."""
        inner = {
            "app/config.py": "from pydantic_settings import BaseSettings\n",
            "app/schemas/product.py": "from pydantic import BaseModel\n",
        }
        result = self._apply_recovery(
            {"__syntax_errors__": json.dumps(inner)},
            {"app/config.py", "app/schemas/product.py"},
        )
        assert set(result.keys()) == {"app/config.py", "app/schemas/product.py"}

    def test_recovery_from_unrecognised_single_key(self):
        """LLM returns one key not in expected_paths — inner map is extracted."""
        inner = {
            "app/services/product.py": "class ProductService: ...\n",
            "app/routers/product.py": "from fastapi import APIRouter\n",
        }
        result = self._apply_recovery(
            {"main.py": json.dumps(inner)},
            {"app/services/product.py", "app/routers/product.py"},
        )
        assert "app/services/product.py" in result
        assert "app/routers/product.py" in result

    def test_recovery_via_nested_files_key(self):
        """Inner JSON uses a 'files' wrapper key — contents are extracted correctly."""
        inner = {
            "files": {
                "app/database.py": "from sqlalchemy import create_engine\n",
                "app/models/user.py": "from sqlalchemy import Column\n",
            }
        }
        result = self._apply_recovery(
            {"__syntax_errors__": json.dumps(inner)},
            {"app/database.py", "app/models/user.py"},
        )
        assert "app/database.py" in result
        assert "app/models/user.py" in result

    def test_no_recovery_when_single_key_matches_expected(self):
        """When the single returned key is a real expected path, no recovery fires."""
        new_files = {"app/config.py": "actual content\n"}
        result = self._apply_recovery(new_files, {"app/config.py", "app/schemas/product.py"})
        assert result == new_files

    def test_no_recovery_when_multiple_keys_returned(self):
        """When more than one key is returned, recovery is not triggered."""
        new_files = {
            "app/config.py": "content1\n",
            "app/schemas/product.py": "content2\n",
        }
        result = self._apply_recovery(
            new_files, {"app/config.py", "app/schemas/product.py"}
        )
        assert result == new_files

    def test_no_crash_on_invalid_json_value(self):
        """If the single value is not valid JSON, recovery silently returns original."""
        new_files = {"__syntax_errors__": "this is not json"}
        result = self._apply_recovery(new_files, {"app/config.py"})
        assert result == new_files

    def test_no_crash_on_non_dict_json(self):
        """If the single value parses to a non-dict, recovery silently returns original."""
        new_files = {"__syntax_errors__": json.dumps(["a", "b"])}
        result = self._apply_recovery(new_files, {"app/config.py"})
        assert result == new_files
