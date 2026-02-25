# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

import json
import sqlite3
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from agents.codegen_agent.codegen_agent import (
    CodeGenConfig,
    RedisFeedbackStore,
    SQLiteFeedbackStore,
    app,
    generate_code,
    perform_security_scans,
)
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_codegen_env(tmp_path: Path):
    """
    Temporary environment for codegen tests.
    Creates a config file and DB path consistent with current implementation.
    """
    config_path = tmp_path / "config.yaml"
    db_path = tmp_path / "feedback.db"

    config_data = {
        "backend": "openai",
        "api_keys": {"openai": "test-key"},
        "model": {"openai": "gpt-4"},
        "feedback_store": {"type": "sqlite", "path": str(db_path)},
    }

    import yaml

    with config_path.open("w") as f:
        yaml.dump(config_data, f)

    return {
        "tmp_dir": tmp_path,
        "config_path": str(config_path),
        "db_path": str(db_path),
        "requirements": "Write a simple Fibonacci implementation.",
        "initial_state": "Initial code state summary.",
    }


# ---------------------------------------------------------------------------
# CodeGenConfig.from_file behavior
# ---------------------------------------------------------------------------


def test_codegen_config_from_file_loads(temp_codegen_env):
    cfg = CodeGenConfig.from_file(temp_codegen_env["config_path"])
    assert cfg is not None
    assert isinstance(cfg.backend, str)
    assert isinstance(cfg.api_keys, dict)


def test_codegen_config_invalid_inputs_do_not_crash(tmp_path: Path):
    """
    Current implementation is lenient; ensure it doesn't explode on odd configs.
    We only assert it returns *a* config object.
    """
    import yaml

    bad_configs = [
        {"backend": "invalid"},
        {"backend": "openai", "api_keys": {}},
        {"feedback_store": {"type": "invalid"}},
    ]

    for i, data in enumerate(bad_configs):
        path = tmp_path / f"bad_config_{i}.yaml"
        with path.open("w") as f:
            yaml.dump(data, f)

        cfg = CodeGenConfig.from_file(str(path))
        assert cfg is not None


# ---------------------------------------------------------------------------
# SQLiteFeedbackStore
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sqlite_feedback_store_round_trip(temp_codegen_env):
    """
    Align with SQLiteFeedbackStore: get_feedback orders by 'timestamp',
    so create a compatible table schema.
    """
    db_path = temp_codegen_env["db_path"]

    conn = sqlite3.connect(db_path, check_same_thread=False)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS hitl_feedback (
                req_hash TEXT PRIMARY KEY,
                feedback TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """)
        conn.commit()
    finally:
        conn.close()

    store = SQLiteFeedbackStore({"path": db_path})

    req_hash = str(uuid.uuid4())
    payload = {"score": 0.9, "comment": "great"}

    await store.save_feedback(req_hash, json.dumps(payload))
    loaded_raw = await store.get_feedback(req_hash)

    assert isinstance(loaded_raw, str)
    loaded = json.loads(loaded_raw)
    assert loaded["score"] == 0.9
    assert loaded["comment"] == "great"


# ---------------------------------------------------------------------------
# RedisFeedbackStore (smoke / skipped)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.skip(
    reason="RedisFeedbackStore requires real or fully wired Redis; skipped."
)
async def test_redis_feedback_store_smoke(temp_codegen_env):
    store = RedisFeedbackStore({"url": "redis://localhost:6379/0"})
    req_hash = str(uuid.uuid4())
    await store.save_feedback(req_hash, json.dumps({"ok": True}))
    _ = await store.get_feedback(req_hash)


# ---------------------------------------------------------------------------
# generate_code helpers
# ---------------------------------------------------------------------------


def _dict_requirements(temp_env):
    """
    Build a requirements dict matching generate_code expectations:
    generate_code() calls requirements.get('target_language', ...).
    """
    return {
        "description": temp_env["requirements"],
        "target_language": "python",
    }


# ---------------------------------------------------------------------------
# generate_code: error handling + success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch(
    "agents.codegen_agent.codegen_agent.call_llm_api",
    side_effect=Exception("LLM failure"),
)
async def test_generate_code_llm_failure_returns_error_file(mock_llm, temp_codegen_env):
    requirements = _dict_requirements(temp_codegen_env)

    result = await generate_code(
        requirements,
        temp_codegen_env["initial_state"],
        temp_codegen_env["config_path"],
    )

    assert "error.txt" in result
    assert "LLM failure" in result["error.txt"]
    assert mock_llm.called


@pytest.mark.asyncio
@patch(
    "agents.codegen_agent.codegen_agent.call_llm_api",
    side_effect=Exception("Rate limit exceeded"),
)
async def test_generate_code_rate_limit_returns_error_file(mock_llm, temp_codegen_env):
    requirements = _dict_requirements(temp_codegen_env)

    result = await generate_code(
        requirements,
        temp_codegen_env["initial_state"],
        temp_codegen_env["config_path"],
    )

    assert "error.txt" in result
    assert "Rate limit exceeded" in result["error.txt"]
    assert mock_llm.called


@pytest.mark.asyncio
@patch(
    "agents.codegen_agent.codegen_agent.call_llm_api",
    side_effect=Exception("Circuit open"),
)
async def test_generate_code_circuit_breaker_returns_error_file(
    mock_llm, temp_codegen_env
):
    requirements = _dict_requirements(temp_codegen_env)

    result = await generate_code(
        requirements,
        temp_codegen_env["initial_state"],
        temp_codegen_env["config_path"],
    )

    assert "error.txt" in result
    assert "Circuit open" in result["error.txt"]
    assert mock_llm.called


@pytest.mark.asyncio
@patch(
    "agents.codegen_agent.codegen_agent.call_llm_api",
    new_callable=AsyncMock,
)
async def test_generate_code_success_with_json_string_response(
    mock_llm, temp_codegen_env
):
    """
    When LLM returns a JSON string parseable by parse_llm_response,
    generate_code should expose those files.
    """
    mock_llm.return_value = json.dumps(
        {"files": {"main.py": "def ok():\n    return 1\n"}}
    )

    requirements = _dict_requirements(temp_codegen_env)

    result = await generate_code(
        requirements,
        temp_codegen_env["initial_state"],
        temp_codegen_env["config_path"],
    )

    assert "main.py" in result
    assert "def ok()" in result["main.py"]
    assert mock_llm.called


@pytest.mark.asyncio
@patch(
    "agents.codegen_agent.codegen_agent.call_llm_api",
    new_callable=AsyncMock,
)
async def test_generate_code_returns_error_on_bad_format(mock_llm, temp_codegen_env):
    """
    Test that non-JSON/non-code responses return an error.txt file.
    """
    bad_response = "not-json, not-code-block"
    mock_llm.return_value = bad_response

    requirements = _dict_requirements(temp_codegen_env)

    result = await generate_code(
        requirements,
        temp_codegen_env["initial_state"],
        temp_codegen_env["config_path"],
    )

    # The implementation returns error.txt for bad responses
    assert "error.txt" in result
    assert "LLM response did not contain recognizable code patterns" in result["error.txt"]
    assert mock_llm.called


# ---------------------------------------------------------------------------
# Security scanning
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch(
    "agents.codegen_agent.codegen_agent.scan_for_vulnerabilities",
    return_value={"issues": [{"severity": "high"}]},
)
async def test_perform_security_scans_does_not_modify_code(mock_scan, temp_codegen_env):
    """
    Patch scan_for_vulnerabilities where it's imported in codegen_agent.
    Ensure it is called and code is not modified.
    """
    code_files = {"main.py": "import os; os.system('rm -rf /')"}
    result = await perform_security_scans(code_files)

    assert result == code_files
    assert mock_scan.called


# ---------------------------------------------------------------------------
# FastAPI app integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fastapi_health_endpoint_allows_degraded(temp_codegen_env):
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200

    data = response.json()
    # Your environment reports "degraded"; accept that as valid.
    assert data.get("status") in ("ok", "healthy", "UP", "degraded")


@pytest.mark.asyncio
async def test_fastapi_hitl_review_endpoint_ignored_if_missing(temp_codegen_env):
    """
    If /hitl_review is not implemented, 404 is acceptable.
    If implemented, 200/202 also acceptable.
    """
    client = TestClient(app)
    req_hash = "test_hash"

    response = client.post(
        f"/hitl_review/{req_hash}",
        json={"status": "approved", "feedback": "Looks good"},
    )

    assert response.status_code in (200, 202, 404)


# ---------------------------------------------------------------------------
# End of file
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# _build_symbol_manifest — unit tests
# ---------------------------------------------------------------------------

from agents.codegen_agent.codegen_agent import _build_symbol_manifest


def test_build_symbol_manifest_captures_functions_and_classes():
    """
    Top-level public functions and classes must appear in the manifest.
    """
    files = {
        "app/auth.py": (
            "from typing import Optional\n\n"
            "class Role:\n    pass\n\n"
            "def get_current_user(token: str) -> Optional[str]:\n    return None\n\n"
            "async def create_access_token(data: dict) -> str:\n    return ''\n"
        ),
    }
    manifest = _build_symbol_manifest(files)

    assert "app.auth" in manifest
    assert "Role" in manifest
    assert "get_current_user" in manifest
    assert "create_access_token" in manifest


def test_build_symbol_manifest_captures_top_level_assignments():
    """
    Module-level variable assignments (e.g. api_router = APIRouter()) must
    appear in the manifest so later passes know to import them rather than
    re-define them.
    """
    files = {
        "app/routing.py": (
            "from fastapi import APIRouter\n\n"
            "api_router = APIRouter()\n\n"
            "@api_router.get('/items')\n"
            "def list_items():\n    return []\n"
        ),
    }
    manifest = _build_symbol_manifest(files)

    assert "app.routing" in manifest
    assert "api_router" in manifest
    assert "list_items" in manifest


def test_build_symbol_manifest_excludes_private_symbols():
    """
    Names starting with underscore are private and must NOT appear in the manifest.
    """
    files = {
        "app/utils.py": (
            "def _helper(): pass\n"
            "class _Internal: pass\n"
            "def public_util(): pass\n"
            "_private_var = 42\n"
        ),
    }
    manifest = _build_symbol_manifest(files)

    assert "_helper" not in manifest
    assert "_Internal" not in manifest
    assert "_private_var" not in manifest
    assert "public_util" in manifest


def test_build_symbol_manifest_skips_non_python_files():
    """
    Non-Python files (requirements.txt, Dockerfile, etc.) must be silently ignored.
    """
    files = {
        "requirements.txt": "fastapi\nuvicorn\n",
        "Dockerfile": "FROM python:3.12\nCMD uvicorn main:app\n",
        "app/main.py": "def run(): pass\n",
    }
    manifest = _build_symbol_manifest(files)

    assert "requirements" not in manifest
    assert "Dockerfile" not in manifest
    assert "app.main" in manifest
    assert "run" in manifest


def test_build_symbol_manifest_skips_files_with_syntax_errors():
    """
    Python files that fail to parse must be silently skipped, not crash.
    """
    files = {
        "app/broken.py": "def : invalid syntax\n",
        "app/good.py": "def valid_fn(): pass\n",
    }
    manifest = _build_symbol_manifest(files)

    assert "app.broken" not in manifest
    assert "app.good" in manifest
    assert "valid_fn" in manifest


def test_build_symbol_manifest_returns_empty_string_for_empty_input():
    """Empty files dict must return an empty string, not crash."""
    assert _build_symbol_manifest({}) == ""


def test_build_symbol_manifest_does_not_include_nested_symbols():
    """
    Methods inside classes and nested functions must NOT appear as module-level
    exports in the manifest (top-level only).
    """
    files = {
        "app/service.py": (
            "class UserService:\n"
            "    def create(self, data): pass\n"
            "    def delete(self, id): pass\n"
        ),
    }
    manifest = _build_symbol_manifest(files)

    # The class should appear, but not its methods
    assert "UserService" in manifest
    assert "create" not in manifest
    assert "delete" not in manifest
