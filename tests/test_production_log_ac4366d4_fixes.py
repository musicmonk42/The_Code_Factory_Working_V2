# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Test Suite — production job ac4366d4 post-mortem fixes
=======================================================

Validates the 5 fixes implemented after production job
ac4366d4-2485-4770-ba67-0d564dafbe22 failed due to async database URL
mismatches, missing JWT config defaults, and a stale Gemini model identifier.

Fixes tested:
- Fix 1: ``python.jinja2`` — database_url default uses async driver
  (sqlite+aiosqlite) so ``create_async_engine`` never raises
  ``InvalidRequestError: The asyncio extension requires an async driver``
- Fix 2: ``config_stub.jinja2`` — generated Settings classes include
  ``algorithm`` and ``access_token_expire_minutes`` with safe defaults so
  ``get_settings()`` never raises ``ValidationError`` at import time
- Fix 3: ``codegen_prompt.py`` — JWT example uses ``ACCESS_TOKEN_EXPIRE_MINUTES``
  constant and ``model_config`` dict (Pydantic v2) in all Settings examples
- Fix 4: Gemini model updated from ``gemini-2.0-flash`` to
  ``gemini-2.5-flash`` (via ``gemini-2.0-flash-001``) in ``server/config.py``, ``llm_client.py``,
  and ``get_default_model_for_provider``
- Fix 5: ``config_stub.jinja2`` uses Pydantic v2 ``model_config`` dict instead
  of the deprecated ``class Config:`` inner class
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Project root on sys.path
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Source file paths
# ---------------------------------------------------------------------------
_PYTHON_JINJA2 = PROJECT_ROOT / "generator/agents/codegen_agent/templates/python.jinja2"
_CONFIG_STUB = PROJECT_ROOT / "generator/agents/codegen_agent/templates/stubs/config_stub.jinja2"
_CODEGEN_PROMPT = PROJECT_ROOT / "generator/agents/codegen_agent/codegen_prompt.py"
_LLM_CLIENT = PROJECT_ROOT / "generator/runner/llm_client.py"
_SERVER_CONFIG = PROJECT_ROOT / "server/config.py"


# ===========================================================================
# Fix 1 — python.jinja2: async database URL default
# ===========================================================================

class TestFix1AsyncDatabaseUrl:
    """python.jinja2 CORRECT example must use an async-compatible DB URL."""

    def _read(self) -> str:
        return _PYTHON_JINJA2.read_text(encoding="utf-8")

    def test_aiosqlite_url_present(self):
        """database_url default must include the +aiosqlite async driver."""
        content = self._read()
        # Accept both single and double quotes around the URL value
        assert re.search(r"""sqlite\+aiosqlite""", content), (
            "python.jinja2 must use 'sqlite+aiosqlite' as the database_url "
            "default so create_async_engine does not raise InvalidRequestError"
        )

    def test_sync_sqlite_url_absent_in_correct_example(self):
        """The ✅ CORRECT block must NOT contain a bare sqlite:// URL."""
        content = self._read()
        # Locate the CORRECT example block
        correct_start = content.find("# ✅ CORRECT")
        wrong_start = content.find("# ❌ WRONG", correct_start)
        correct_block = content[correct_start:wrong_start] if wrong_start != -1 else content[correct_start:]
        # Match both single and double quoted sync URLs
        assert not re.search(r"""default\s*=\s*["']sqlite:///""", correct_block), (
            "The CORRECT example in python.jinja2 must not use a sync sqlite:// URL"
        )

    def test_algorithm_field_present(self):
        """The CORRECT Settings example must declare an algorithm field."""
        content = self._read()
        assert "algorithm" in content, (
            "python.jinja2 CORRECT example must include an 'algorithm' field "
            "so LLMs learn to generate it with a default"
        )

    def test_access_token_expire_minutes_field_present(self):
        """The CORRECT Settings example must declare access_token_expire_minutes."""
        content = self._read()
        assert "access_token_expire_minutes" in content, (
            "python.jinja2 CORRECT example must include 'access_token_expire_minutes' "
            "so LLMs learn to generate it with a default"
        )


# ===========================================================================
# Fix 2 — config_stub.jinja2: JWT field defaults
# ===========================================================================

class TestFix2ConfigStubJwtDefaults:
    """config_stub.jinja2 must include algorithm and access_token_expire_minutes."""

    def _read(self) -> str:
        return _CONFIG_STUB.read_text(encoding="utf-8")

    def test_algorithm_field_in_named_class_block(self):
        """Named-class template block must declare algorithm with HS256 default."""
        content = self._read()
        # Named-class block is in the {% for cls in class_names %} section
        for_block_end = content.find("{% if not class_names %}")
        named_block = content[:for_block_end]
        assert 'algorithm' in named_block, (
            "config_stub.jinja2 named-class block must include 'algorithm' field"
        )
        assert '"HS256"' in named_block, (
            "config_stub.jinja2 named-class block must default algorithm to 'HS256'"
        )

    def test_access_token_expire_minutes_in_named_class_block(self):
        """Named-class template block must declare access_token_expire_minutes."""
        content = self._read()
        for_block_end = content.find("{% if not class_names %}")
        named_block = content[:for_block_end]
        assert 'access_token_expire_minutes' in named_block, (
            "config_stub.jinja2 named-class block must include 'access_token_expire_minutes'"
        )

    def test_algorithm_field_in_fallback_settings(self):
        """Fallback Settings class must declare algorithm with HS256 default."""
        content = self._read()
        fallback_start = content.find("{% if not class_names %}")
        fallback_block = content[fallback_start:]
        assert 'algorithm' in fallback_block, (
            "config_stub.jinja2 fallback Settings must include 'algorithm' field"
        )
        assert '"HS256"' in fallback_block, (
            "config_stub.jinja2 fallback Settings must default algorithm to 'HS256'"
        )

    def test_access_token_expire_minutes_in_fallback_settings(self):
        """Fallback Settings class must declare access_token_expire_minutes."""
        content = self._read()
        fallback_start = content.find("{% if not class_names %}")
        fallback_block = content[fallback_start:]
        assert 'access_token_expire_minutes' in fallback_block, (
            "config_stub.jinja2 fallback Settings must include 'access_token_expire_minutes'"
        )

    def test_async_database_url_in_stub(self):
        """config_stub.jinja2 must use an async-compatible database_url default."""
        content = self._read()
        assert "sqlite+aiosqlite" in content, (
            "config_stub.jinja2 must use 'sqlite+aiosqlite' as the database_url default"
        )


# ===========================================================================
# Fix 3 — codegen_prompt.py: ACCESS_TOKEN_EXPIRE_MINUTES constant + model_config
# ===========================================================================

class TestFix3CodegenPromptJwtExample:
    """codegen_prompt.py JWT example must use the constant and Pydantic v2 style."""

    def _read(self) -> str:
        return _CODEGEN_PROMPT.read_text(encoding="utf-8")

    def test_access_token_expire_minutes_constant_defined(self):
        """The JWT example must define ACCESS_TOKEN_EXPIRE_MINUTES as a constant."""
        content = self._read()
        assert "ACCESS_TOKEN_EXPIRE_MINUTES" in content, (
            "codegen_prompt.py JWT example must define ACCESS_TOKEN_EXPIRE_MINUTES constant"
        )

    def test_create_access_token_uses_constant(self):
        """create_access_token must use ACCESS_TOKEN_EXPIRE_MINUTES, not a hardcoded 30."""
        content = self._read()
        assert "ACCESS_TOKEN_EXPIRE_MINUTES" in content, "ACCESS_TOKEN_EXPIRE_MINUTES not found"

        # Extract just the signature line to avoid cross-definition matching
        sig_line = next(
            (ln for ln in content.splitlines() if "def create_access_token(" in ln),
            None,
        )
        assert sig_line is not None, "create_access_token definition not found"
        assert "ACCESS_TOKEN_EXPIRE_MINUTES" in sig_line, (
            "create_access_token default parameter must use "
            "timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES), not a bare integer. "
            f"Found: {sig_line.strip()}"
        )

    def test_no_hardcoded_30_in_create_access_token_signature(self):
        """create_access_token must not use a bare timedelta(minutes=30) as the default."""
        content = self._read()
        sig_line = next(
            (ln for ln in content.splitlines() if "def create_access_token(" in ln),
            None,
        )
        # If there is no definition at all, the test above already catches it
        if sig_line is not None:
            assert not re.search(r"timedelta\(minutes=30\)", sig_line), (
                "create_access_token must not hardcode timedelta(minutes=30); "
                f"use ACCESS_TOKEN_EXPIRE_MINUTES instead. Found: {sig_line.strip()}"
            )


# ===========================================================================
# Fix 4 — Gemini model identifier: gemini-2.0-flash → gemini-2.5-flash
# (previously updated to gemini-2.0-flash-001; now deprecated entirely)
# ===========================================================================

def _strip_inline_comment(line: str) -> str:
    """Return the code portion of a line, stripping any trailing inline # comment."""
    # Split only on '#' that appears outside string literals (simplified heuristic:
    # strip from the first '#' that is not inside a quoted span).
    in_single = False
    in_double = False
    for i, ch in enumerate(line):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            return line[:i]
    return line


EXPECTED_GEMINI_MODEL = "gemini-2.5-flash"
STALE_GEMINI_MODEL = "gemini-2.0-flash"


class TestFix4GeminiModelUpdate:
    """All Gemini model references must use the updated gemini-2.5-flash identifier."""

    def test_llm_client_provider_default_models(self):
        """llm_client.py _PROVIDER_DEFAULT_MODELS must reference gemini-2.5-flash."""
        content = _LLM_CLIENT.read_text(encoding="utf-8")
        assert EXPECTED_GEMINI_MODEL in content, (
            f"llm_client.py must reference {EXPECTED_GEMINI_MODEL} in _PROVIDER_DEFAULT_MODELS"
        )

    def test_llm_client_no_stale_model(self):
        """llm_client.py must not assign the stale gemini-2.0-flash identifier in code."""
        content = _LLM_CLIENT.read_text(encoding="utf-8")
        # Strip full-line and inline comments to avoid false positives from historical notes
        code_lines = [
            _strip_inline_comment(line)
            for line in content.splitlines()
            if not line.strip().startswith("#")
        ]
        stale_in_code = [
            line for line in code_lines
            if re.search(r'["\']gemini-2\.0-flash["\']', line)
        ]
        assert stale_in_code == [], (
            f"llm_client.py code still assigns stale '{STALE_GEMINI_MODEL}': "
            f"{stale_in_code}"
        )

    def test_server_config_google_model_field(self):
        """server/config.py google_model Field default must be gemini-2.5-flash."""
        content = _SERVER_CONFIG.read_text(encoding="utf-8")
        # Accept both single and double-quoted string values
        match = re.search(r'google_model.*?default\s*=\s*["\']([^"\']+)["\']', content, re.DOTALL)
        assert match is not None, "google_model field default not found in server/config.py"
        assert match.group(1) == EXPECTED_GEMINI_MODEL, (
            f"server/config.py google_model default is '{match.group(1)}', "
            f"expected '{EXPECTED_GEMINI_MODEL}'"
        )

    def test_server_config_get_default_model_for_provider(self):
        """get_default_model_for_provider('google') must return gemini-2.5-flash."""
        content = _SERVER_CONFIG.read_text(encoding="utf-8")
        # Use the ast module to reliably extract the function body
        import ast

        fn_start = content.find("def get_default_model_for_provider")
        assert fn_start != -1, "get_default_model_for_provider not found in server/config.py"
        # Find the end of the function by locating the next top-level definition
        # (either 'def ' or 'class ' at column 0)
        next_top = re.search(r"^\n(?:def |class )", content[fn_start + 1:], re.MULTILINE)
        fn_block = (
            content[fn_start: fn_start + 1 + next_top.start()]
            if next_top
            else content[fn_start:]
        )
        assert EXPECTED_GEMINI_MODEL in fn_block, (
            f"get_default_model_for_provider in server/config.py must map "
            f"'google' to '{EXPECTED_GEMINI_MODEL}'"
        )

    def test_server_config_no_stale_model(self):
        """server/config.py must not assign gemini-2.0-flash in code (non-comment) lines."""
        content = _SERVER_CONFIG.read_text(encoding="utf-8")
        # Strip full-line and inline comments to avoid false positives from historical notes
        code_lines = [
            _strip_inline_comment(line)
            for line in content.splitlines()
            if not line.strip().startswith("#")
        ]
        stale_in_code = [
            line for line in code_lines
            if re.search(r'["\']gemini-2\.0-flash["\']', line)
        ]
        assert stale_in_code == [], (
            f"server/config.py code still assigns stale '{STALE_GEMINI_MODEL}': "
            f"{stale_in_code}"
        )


# ===========================================================================
# Fix 5 — config_stub.jinja2: Pydantic v2 model_config (no class Config:)
# ===========================================================================

class TestFix5ModelConfigPydanticV2:
    """config_stub.jinja2 must use model_config dict, not the deprecated class Config:."""

    def _read(self) -> str:
        return _CONFIG_STUB.read_text(encoding="utf-8")

    def test_no_class_config_inner_class(self):
        """config_stub.jinja2 must not contain a deprecated 'class Config:' inner class."""
        content = self._read()
        assert "class Config:" not in content, (
            "config_stub.jinja2 uses deprecated Pydantic v1 'class Config:' inner class; "
            "replace with 'model_config = {...}' (Pydantic v2)"
        )

    def test_model_config_dict_present_in_named_block(self):
        """Named-class block must use model_config dict for Pydantic v2 compatibility."""
        content = self._read()
        for_block_end = content.find("{% if not class_names %}")
        named_block = content[:for_block_end]
        assert "model_config" in named_block, (
            "config_stub.jinja2 named-class block must use 'model_config' (Pydantic v2 style)"
        )

    def test_model_config_dict_present_in_fallback(self):
        """Fallback Settings class must use model_config dict for Pydantic v2 compatibility."""
        content = self._read()
        fallback_start = content.find("{% if not class_names %}")
        fallback_block = content[fallback_start:]
        assert "model_config" in fallback_block, (
            "config_stub.jinja2 fallback Settings must use 'model_config' (Pydantic v2 style)"
        )

    def test_env_file_setting_preserved(self):
        """model_config dict must still declare env_file for dotenv loading."""
        content = self._read()
        assert '"env_file"' in content or "'env_file'" in content, (
            "config_stub.jinja2 model_config must include the env_file setting"
        )
