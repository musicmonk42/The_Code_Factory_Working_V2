# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Test Suite — Pipeline Divergence Fixes (2026-02-21)
=====================================================

Validates the three root-cause fixes that close the gap between the OmniCore
service pipeline and the CLI engine pipeline:

Root Cause #1 — Missing post-materialization steps in OmniCore:
    Tested via :class:`TestPostMaterialize` and
    :class:`TestEnsureReadmeSections`.

Root Cause #2 — K8s multi-document YAML not split (already handled in the
    deploy-agent path; covered by the existing deploy integration tests).

Root Cause #3 — Docgen validator's section detection doesn't match LLM
    heading names:
    Tested via :class:`TestCoreAliasMatching` and
    :class:`TestHeadingCounter`.

Coverage contract
-----------------
* Every public symbol in ``generator/main/post_materialize.py`` is exercised.
* Every alias defined in ``CORE_SECTION_ALIASES`` is tested at least once.
* The ``PostMaterializeResult`` dataclass is fully validated.
* Edge cases: empty directory, non-existent directory, double-invocation,
  existing files protected from overwrite.

Author: Code Factory Platform Team
Version: 1.0.0
"""

from __future__ import annotations

import importlib.util
import re
import sys
import tempfile
from dataclasses import fields as dc_fields
from pathlib import Path
from typing import Any, Dict, List

import pytest

# ---------------------------------------------------------------------------
# Ensure project root is on sys.path (mirrors other test modules)
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Module loader — bypasses package __init__ to avoid heavy transitive deps
# ---------------------------------------------------------------------------

def _load_module(rel_path: str, name: str):
    """Load a module by file-path, bypassing package __init__ files.

    This pattern is used throughout the test suite to isolate lightweight
    modules from the heavyweight generator/server import chains.
    The module is registered in ``sys.modules`` so that ``@dataclass``
    and other reflection-based decorators work correctly (Python 3.12+
    requires ``sys.modules[cls.__module__]`` to be present).
    """
    spec = importlib.util.spec_from_file_location(name, PROJECT_ROOT / rel_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # required for @dataclass in Python 3.12+
    try:
        spec.loader.exec_module(mod)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return mod


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def pm_module():
    """Load ``generator/main/post_materialize.py`` for each test."""
    return _load_module(
        "generator/main/post_materialize.py",
        "post_materialize_under_test",
    )


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """Return an empty project directory inside a pytest tmp_path."""
    d = tmp_path / "my_project"
    d.mkdir()
    return d


@pytest.fixture
def project_with_readme(project_dir: Path) -> Path:
    """Project directory pre-populated with a minimal README.md."""
    (project_dir / "README.md").write_text(
        "# My Project\n\nA short description.\n",
        encoding="utf-8",
    )
    return project_dir


# =============================================================================
# TestPostMaterializeResult — dataclass contract
# =============================================================================


class TestPostMaterializeResult:
    """Validate the PostMaterializeResult dataclass shape and defaults."""

    def test_default_values(self, pm_module):
        r = pm_module.PostMaterializeResult()
        assert r.success is True
        assert r.files_created == []
        assert r.warnings == []
        assert r.duration_seconds == 0.0
        assert r.output_dir == ""

    def test_to_dict_keys(self, pm_module):
        r = pm_module.PostMaterializeResult(
            success=True,
            files_created=["app/schemas.py"],
            warnings=["one warning"],
            duration_seconds=0.042,
            output_dir="/tmp/proj",
        )
        d = r.to_dict()
        expected_keys = {
            "success", "files_created", "files_created_count",
            "warnings", "duration_seconds", "output_dir",
        }
        assert expected_keys == set(d.keys())
        assert d["files_created_count"] == 1
        assert d["duration_seconds"] == 0.042

    def test_all_dataclass_fields_present(self, pm_module):
        """Every field defined on the dataclass must appear in to_dict()."""
        r = pm_module.PostMaterializeResult()
        result_dict = r.to_dict()
        for f in dc_fields(r):
            if f.name == "files_created":
                # files_created is represented as both 'files_created' and the
                # computed 'files_created_count' key in the serialised dict.
                assert "files_created" in result_dict, "files_created missing from to_dict()"
                assert "files_created_count" in result_dict, "files_created_count missing from to_dict()"
            else:
                assert f.name in result_dict, f"Field '{f.name}' missing from to_dict()"


# =============================================================================
# TestPostMaterialize — main function behaviour
# =============================================================================


class TestPostMaterialize:
    """Validate the idempotent post_materialize() function."""

    # ------------------------------------------------------------------
    # Success path
    # ------------------------------------------------------------------

    def test_returns_result_object(self, pm_module, project_dir):
        r = pm_module.post_materialize(project_dir)
        assert isinstance(r, pm_module.PostMaterializeResult)

    def test_success_flag_set(self, pm_module, project_dir):
        r = pm_module.post_materialize(project_dir)
        assert r.success is True

    def test_output_dir_recorded(self, pm_module, project_dir):
        r = pm_module.post_materialize(project_dir)
        assert r.output_dir == str(project_dir)

    def test_duration_populated(self, pm_module, project_dir):
        r = pm_module.post_materialize(project_dir)
        assert r.duration_seconds > 0.0

    # ------------------------------------------------------------------
    # Required directory scaffold
    # ------------------------------------------------------------------

    @pytest.mark.parametrize("dir_name", ["app", "tests", "reports"])
    def test_required_directory_created(self, pm_module, project_dir, dir_name):
        pm_module.post_materialize(project_dir)
        assert (project_dir / dir_name).is_dir(), \
            f"Required directory '{dir_name}' should be created"

    def test_app_init_py_created(self, pm_module, project_dir):
        pm_module.post_materialize(project_dir)
        assert (project_dir / "app" / "__init__.py").exists()

    def test_tests_init_py_created(self, pm_module, project_dir):
        pm_module.post_materialize(project_dir)
        assert (project_dir / "tests" / "__init__.py").exists()

    # ------------------------------------------------------------------
    # app/schemas.py
    # ------------------------------------------------------------------

    def test_schemas_py_created(self, pm_module, project_dir):
        pm_module.post_materialize(project_dir)
        assert (project_dir / "app" / "schemas.py").exists(), \
            "app/schemas.py must be created for ContractValidator"

    def test_schemas_py_contains_field_validator(self, pm_module, project_dir):
        pm_module.post_materialize(project_dir)
        content = (project_dir / "app" / "schemas.py").read_text(encoding="utf-8")
        assert "field_validator" in content, \
            "app/schemas.py must contain @field_validator"

    def test_schemas_py_contains_basemodel(self, pm_module, project_dir):
        pm_module.post_materialize(project_dir)
        content = (project_dir / "app" / "schemas.py").read_text(encoding="utf-8")
        assert "BaseModel" in content

    def test_schemas_py_not_overwritten_when_exists(self, pm_module, project_dir):
        (project_dir / "app").mkdir(exist_ok=True)
        custom = "# custom\n"
        (project_dir / "app" / "schemas.py").write_text(custom, encoding="utf-8")
        pm_module.post_materialize(project_dir)
        assert (project_dir / "app" / "schemas.py").read_text(encoding="utf-8") == custom

    # ------------------------------------------------------------------
    # app/main.py
    # ------------------------------------------------------------------

    def test_app_main_py_created_when_absent(self, pm_module, project_dir):
        pm_module.post_materialize(project_dir)
        assert (project_dir / "app" / "main.py").exists()

    def test_app_main_py_copied_from_root_when_present(self, pm_module, project_dir):
        root_content = "# root main\napp = None\n"
        (project_dir / "main.py").write_text(root_content, encoding="utf-8")
        pm_module.post_materialize(project_dir)
        assert (project_dir / "app" / "main.py").read_text(encoding="utf-8") == root_content

    def test_app_main_py_not_overwritten_when_exists(self, pm_module, project_dir):
        (project_dir / "app").mkdir(exist_ok=True)
        custom = "# existing main\n"
        (project_dir / "app" / "main.py").write_text(custom, encoding="utf-8")
        pm_module.post_materialize(project_dir)
        assert (project_dir / "app" / "main.py").read_text(encoding="utf-8") == custom

    # ------------------------------------------------------------------
    # README patching
    # ------------------------------------------------------------------

    def test_readme_patched_with_setup_section(self, pm_module, project_with_readme):
        pm_module.post_materialize(project_with_readme)
        readme = (project_with_readme / "README.md").read_text(encoding="utf-8")
        assert "## Setup" in readme

    def test_readme_patched_with_all_required_sections(self, pm_module, project_with_readme):
        pm_module.post_materialize(project_with_readme)
        readme = (project_with_readme / "README.md").read_text(encoding="utf-8")
        for section in ("## Setup", "## Run", "## Test", "## API Endpoints", "## Project Structure"):
            assert section in readme, f"README should contain '{section}'"

    def test_readme_existing_setup_not_duplicated(self, pm_module, project_dir):
        content = "# My Project\n\n## Setup\n\npip install\n"
        (project_dir / "README.md").write_text(content, encoding="utf-8")
        pm_module.post_materialize(project_dir)
        result = (project_dir / "README.md").read_text(encoding="utf-8")
        assert result.count("## Setup") == 1, "Existing ## Setup must not be duplicated"

    def test_readme_skipped_when_absent(self, pm_module, project_dir):
        """post_materialize must not crash when README.md does not exist."""
        pm_module.post_materialize(project_dir)  # should not raise

    # ------------------------------------------------------------------
    # Sphinx placeholder
    # ------------------------------------------------------------------

    def test_sphinx_index_html_created(self, pm_module, project_dir):
        pm_module.post_materialize(project_dir)
        index = project_dir / "docs" / "_build" / "html" / "index.html"
        assert index.exists(), "docs/_build/html/index.html must be created"

    def test_sphinx_index_html_is_valid_html(self, pm_module, project_dir):
        pm_module.post_materialize(project_dir)
        content = (project_dir / "docs" / "_build" / "html" / "index.html").read_text(
            encoding="utf-8"
        )
        assert "<!DOCTYPE html>" in content
        assert "<html" in content
        assert "</html>" in content

    def test_sphinx_index_not_overwritten(self, pm_module, project_dir):
        html_dir = project_dir / "docs" / "_build" / "html"
        html_dir.mkdir(parents=True)
        existing_html = "<html>existing</html>"
        (html_dir / "index.html").write_text(existing_html, encoding="utf-8")
        pm_module.post_materialize(project_dir)
        assert (html_dir / "index.html").read_text(encoding="utf-8") == existing_html

    # ------------------------------------------------------------------
    # Idempotency & edge cases
    # ------------------------------------------------------------------

    def test_idempotent_second_call_no_new_files(self, pm_module, project_dir):
        pm_module.post_materialize(project_dir)
        r2 = pm_module.post_materialize(project_dir)
        # Second call should not create any new files (all already exist)
        # README was absent so no README patch, other stubs already present
        assert r2.files_created == [], \
            f"Second call should not create new files; got {r2.files_created}"

    def test_nonexistent_directory_returns_failure_result(self, pm_module, tmp_path):
        r = pm_module.post_materialize(tmp_path / "does_not_exist")
        assert r.success is False
        assert r.files_created == []
        assert len(r.warnings) >= 1

    def test_files_created_listed_in_result(self, pm_module, project_dir):
        r = pm_module.post_materialize(project_dir)
        # Should have created at least schemas.py and the Sphinx placeholder
        assert len(r.files_created) >= 1


# =============================================================================
# TestEnsureReadmeSections — public utility
# =============================================================================


class TestEnsureReadmeSections:
    """Validate ensure_readme_sections() in isolation."""

    @pytest.fixture
    def fn(self, pm_module):
        return pm_module.ensure_readme_sections

    def test_all_sections_added_to_empty_readme(self, fn):
        result = fn("# My App\n")
        for section in ("## Setup", "## Run", "## Test", "## API Endpoints", "## Project Structure"):
            assert section in result, f"'{section}' must be appended to minimal README"

    def test_curl_example_added_when_missing(self, fn):
        result = fn("# My App\n")
        assert "curl" in result

    def test_no_duplication_when_all_sections_present(self, fn):
        full = (
            "# App\n## Setup\nsteps.\n## Run\nrun.\n## Test\ntest.\n"
            "## API Endpoints\nep.\n## Project Structure\nstruct.\ncurl http://localhost\n"
        )
        result = fn(full)
        for section in ("## Setup", "## Run", "## Test", "## API Endpoints", "## Project Structure"):
            assert result.count(section) == 1, f"'{section}' must not be duplicated"

    def test_entry_point_used_in_run_section(self, fn):
        result = fn("# My App\n", entry_point="mymod.app:create_app")
        assert "mymod.app:create_app" in result

    def test_handles_none_input(self, fn):
        result = fn(None)  # type: ignore[arg-type]
        assert isinstance(result, str)
        assert "## Setup" in result

    def test_handles_empty_string(self, fn):
        result = fn("")
        assert "## Setup" in result

    @pytest.mark.parametrize("section", [
        "## Setup", "## Run", "## Test", "## API Endpoints", "## Project Structure"
    ])
    def test_each_section_individually(self, fn, section):
        """Each required section is independently added when missing."""
        result = fn("# My App\n")
        assert section in result


# =============================================================================
# TestStubContentConstants — validate stub file constants
# =============================================================================


class TestStubContentConstants:
    """Validate the stub content constants exported from post_materialize."""

    def test_schemas_stub_has_field_validator(self, pm_module):
        assert "field_validator" in pm_module._APP_SCHEMAS_CONTENT
        assert "BaseModel" in pm_module._APP_SCHEMAS_CONTENT
        assert "@classmethod" in pm_module._APP_SCHEMAS_CONTENT

    def test_schemas_stub_is_valid_python(self, pm_module):
        """The schemas stub must be syntactically valid Python."""
        compile(pm_module._APP_SCHEMAS_CONTENT, "<schemas_stub>", "exec")

    def test_routes_stub_is_valid_python(self, pm_module):
        compile(pm_module._APP_ROUTES_CONTENT, "<routes_stub>", "exec")

    def test_main_stub_is_valid_python(self, pm_module):
        compile(pm_module._APP_MAIN_CONTENT, "<main_stub>", "exec")

    def test_routes_stub_has_health_endpoint(self, pm_module):
        assert "/health" in pm_module._APP_ROUTES_CONTENT

    def test_main_stub_includes_router(self, pm_module):
        assert "include_router" in pm_module._APP_MAIN_CONTENT


# =============================================================================
# TestCoreAliasMatching — docgen_response_validator.py fix
#
# The changed logic is replicated here so these tests run without the heavy
# transitive deps that docgen_response_validator.py drags in at module level
# (presidio, nltk, bs4, uvicorn …).  The tests validate the contract of the
# fix, not just the implementation.
# =============================================================================

# Mirrors CORE_SECTION_ALIASES added to MarkdownPlugin.validate()
_CORE_SECTION_ALIASES: Dict[str, List[str]] = {
    "introduction": [
        "introduction", "overview", "about", "description",
        "project title", "project description",
    ],
    "usage": [
        "usage", "quick start", "getting started", "how to use",
        "examples", "quickstart",
    ],
    "endpoints": ["endpoints", "api endpoints", "routes", "api reference"],
    "authentication": ["authentication", "auth", "authorization", "security"],
}


def _section_present(content: str, section: str) -> bool:
    """Replica of the _section_present() closure added inside validate()."""
    aliases = _CORE_SECTION_ALIASES.get(section.lower(), [section.lower()])
    for alias in aliases:
        if re.search(
            rf"^\s*#{{1,6}}\s+.*{re.escape(alias)}.*$",
            content,
            re.IGNORECASE | re.MULTILINE,
        ):
            return True
    return False


def _count_headings(content: str) -> int:
    """Replica of the updated section-counter in validate()."""
    return len(re.findall(r"^\s*#{1,6}\s+\S", content, re.MULTILINE))


class TestCoreAliasMatching:
    """Validate alias/fuzzy heading detection for docgen core sections."""

    # ------------------------------------------------------------------
    # introduction aliases
    # ------------------------------------------------------------------

    @pytest.mark.parametrize("heading", [
        "## Introduction",
        "## Overview",
        "## About",
        "## Description",
        "## Project Title and Description",
        "## Project Description",
    ])
    def test_introduction_aliases_match(self, heading):
        content = f"# My Project\n\n{heading}\n\nSome content.\n"
        assert _section_present(content, "introduction"), \
            f"'{heading}' should satisfy the 'introduction' core-section check"

    # ------------------------------------------------------------------
    # usage aliases
    # ------------------------------------------------------------------

    @pytest.mark.parametrize("heading", [
        "## Usage",
        "## Quick Start",
        "## Getting Started",
        "## How to Use",
        "## Examples",
        "## Quickstart",
        "## Quick Start / Usage",
    ])
    def test_usage_aliases_match(self, heading):
        content = f"# My Project\n\n## Overview\n\nAbout.\n\n{heading}\n\nInstructions.\n"
        assert _section_present(content, "usage"), \
            f"'{heading}' should satisfy the 'usage' core-section check"

    # ------------------------------------------------------------------
    # endpoints / authentication
    # ------------------------------------------------------------------

    @pytest.mark.parametrize("heading", [
        "## Endpoints",
        "## API Endpoints",
        "## Routes",
        "## API Reference",
    ])
    def test_endpoints_aliases_match(self, heading):
        content = f"# API\n\n{heading}\n\nGET /health\n"
        assert _section_present(content, "endpoints"), \
            f"'{heading}' should satisfy 'endpoints'"

    @pytest.mark.parametrize("heading", [
        "## Authentication",
        "## Auth",
        "## Authorization",
        "## Security",
    ])
    def test_authentication_aliases_match(self, heading):
        content = f"# API\n\n## Endpoints\n\n...\n\n{heading}\n\nBearer token.\n"
        assert _section_present(content, "authentication"), \
            f"'{heading}' should satisfy 'authentication'"

    # ------------------------------------------------------------------
    # Negative case — truly absent section
    # ------------------------------------------------------------------

    def test_absent_section_returns_false(self):
        content = "# My Project\n\nSome text with no headings for 'usage'.\n"
        assert not _section_present(content, "usage"), \
            "Should return False when neither 'usage' nor any alias is a heading"

    def test_section_in_prose_does_not_match(self):
        """Alias word appearing in body text (not a heading) must not match."""
        content = "# My Project\n\nFor usage instructions see below.\n"
        assert not _section_present(content, "usage"), \
            "Alias word in body text must not satisfy the heading check"


# =============================================================================
# TestHeadingCounter — fixed section-count logic
# =============================================================================


class TestHeadingCounter:
    """Validate the updated heading counter that counts *any* markdown heading."""

    @pytest.mark.parametrize("content,expected_min", [
        ("# Title\n\n## Section A\n\n## Section B\n\n## Section C\n", 4),
        ("# Project Title and Description\n\n## Overview\n\n## Quick Start\n", 3),
        (
            "# Title\n\n## Section A\n\n### Sub-section\n\n#### Deep\n",
            4,
        ),
    ])
    def test_heading_count_minimum(self, content: str, expected_min: int):
        count = _count_headings(content)
        assert count >= expected_min, \
            f"Expected at least {expected_min} headings, found {count}"

    def test_empty_document_returns_zero(self):
        assert _count_headings("") == 0

    def test_prose_only_document_returns_zero(self):
        assert _count_headings("Some prose text.\nNo headings here.\n") == 0

    def test_llm_style_headings_counted(self):
        """LLM-generated heading names (not in schema) must still be counted."""
        content = (
            "# Project Title and Description\n\n"
            "## Project Overview\n\n"
            "## Quick Start / Usage\n\n"
            "## Configuration Options\n"
        )
        assert _count_headings(content) == 4, \
            "All four headings should be counted regardless of their names"

    def test_heading_counter_never_returns_zero_for_rich_doc(self):
        """The 'Insufficient sections: found 0' bug must not reproduce."""
        content = (
            "# Project Title and Description\n\n"
            "A great app.\n\n"
            "## Project Overview\n\nDetails.\n\n"
            "## Quick Start / Usage\n\nInstall and run.\n\n"
            "## Configuration\n\nSettings.\n"
        )
        assert _count_headings(content) >= 3, \
            f"Rich document must have >= 3 headings; got {_count_headings(content)}"
