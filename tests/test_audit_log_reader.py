# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Tests for the unified audit log reader in server/routers/audit.py.

Validates:
- Simulation log path normalization (agentic_audit.jsonl included)
- Arbiter encrypted-entry decryption and graceful error handling
"""

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Direct module loader – avoids executing server/routers/__init__.py and
# pulling in every router dependency (fastapi, aiofiles, sse_starlette, …)
# ---------------------------------------------------------------------------

def _load_audit_module():
    """Load server/routers/audit.py by file path, bypassing __init__.py.

    Pre-stubs the heavyweight top-level dependencies (fastapi, server.services)
    so the module can be imported in lightweight test environments without the
    full dependency stack installed.
    """
    import types

    # Stub fastapi if not present
    if "fastapi" not in sys.modules:
        fastapi_stub = types.ModuleType("fastapi")
        fastapi_stub.APIRouter = MagicMock
        fastapi_stub.Depends = lambda f: f
        fastapi_stub.HTTPException = Exception
        fastapi_stub.Query = lambda *a, **kw: None
        sys.modules["fastapi"] = fastapi_stub

    # Stub server.services if not present
    for mod_name in (
        "server",
        "server.services",
        "server.services.generator_service",
        "server.services.omnicore_service",
    ):
        if mod_name not in sys.modules:
            sys.modules[mod_name] = types.ModuleType(mod_name)

    svc = sys.modules["server.services.generator_service"]
    if not hasattr(svc, "GeneratorService"):
        svc.GeneratorService = MagicMock
        svc.get_generator_service = MagicMock()

    omni = sys.modules["server.services.omnicore_service"]
    if not hasattr(omni, "OmniCoreService"):
        omni.OmniCoreService = MagicMock
        omni.get_omnicore_service = MagicMock()

    project_root = Path(__file__).parent.parent
    audit_path = project_root / "server" / "routers" / "audit.py"
    spec = importlib.util.spec_from_file_location("server.routers.audit", audit_path)
    mod = importlib.util.module_from_spec(spec)
    # Register under its full dotted name so relative imports work
    sys.modules["server.routers.audit"] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_simulation_entry(event_type: str = "agent_decision", event_id: str = "evt-1") -> str:
    """Return a JSONL line matching the format written by the simulation/agentic logger."""
    entry = {
        "event": {
            "event_type": event_type,
            "event_id": event_id,
            "timestamp": "2026-01-01T00:00:00Z",
            "payload": {"action": "decide"},
        },
        "signature": "sig-abc",
    }
    return json.dumps(entry)


def _make_arbiter_entry(job_id: str = "job-123", encrypt: bool = False) -> dict:
    """Return a dict matching the format written by TamperEvidentLogger."""
    details = {"job_id": job_id, "info": "some detail"}
    if encrypt:
        # Simulate what _encrypt_entry produces: details becomes a string token
        details = "gAAAAABencryptedtoken=="
    return {
        "event_type": "bug_detected",
        "timestamp": "2026-01-01T00:00:00Z",
        "user_id": "arbiter_system",
        "details": details,
        "current_hash": "abc123",
        "signature": None,
    }


# ---------------------------------------------------------------------------
# Simulation log path tests
# ---------------------------------------------------------------------------

class TestSimulationLogPaths:
    """Verify that _query_simulation_audit_logs reads agentic_audit.jsonl."""

    @pytest.mark.asyncio
    async def test_reads_agentic_audit_jsonl(self, tmp_path, monkeypatch):
        """agentic_audit.jsonl should be picked up as a valid simulation log."""
        audit = _load_audit_module()
        _query_simulation_audit_logs = audit._query_simulation_audit_logs

        log_file = tmp_path / "agentic_audit.jsonl"
        log_file.write_text(_make_simulation_entry() + "\n")

        # Change working directory so relative paths resolve correctly
        monkeypatch.chdir(tmp_path)

        logs = await _query_simulation_audit_logs(
            start_time=None, end_time=None,
            event_type=None, job_id=None, limit=10,
        )

        assert len(logs) == 1
        assert logs[0]["event_type"] == "agent_decision"

    @pytest.mark.asyncio
    async def test_jsonl_preferred_over_log(self, tmp_path, monkeypatch):
        """agentic_audit.jsonl should be chosen before agentic_audit.log."""
        audit = _load_audit_module()
        _query_simulation_audit_logs = audit._query_simulation_audit_logs

        jsonl_file = tmp_path / "agentic_audit.jsonl"
        jsonl_file.write_text(_make_simulation_entry(event_type="from_jsonl") + "\n")

        log_file = tmp_path / "agentic_audit.log"
        log_file.write_text(_make_simulation_entry(event_type="from_log") + "\n")

        monkeypatch.chdir(tmp_path)

        logs = await _query_simulation_audit_logs(
            start_time=None, end_time=None,
            event_type=None, job_id=None, limit=10,
        )

        assert len(logs) == 1
        assert logs[0]["event_type"] == "from_jsonl"

    @pytest.mark.asyncio
    async def test_falls_back_to_legacy_log(self, tmp_path, monkeypatch):
        """Falls back to agentic_audit.log when .jsonl is absent."""
        audit = _load_audit_module()
        _query_simulation_audit_logs = audit._query_simulation_audit_logs

        log_file = tmp_path / "agentic_audit.log"
        log_file.write_text(_make_simulation_entry(event_type="legacy_event") + "\n")

        monkeypatch.chdir(tmp_path)

        logs = await _query_simulation_audit_logs(
            start_time=None, end_time=None,
            event_type=None, job_id=None, limit=10,
        )

        assert len(logs) == 1
        assert logs[0]["event_type"] == "legacy_event"

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_log_files(self, tmp_path, monkeypatch):
        """Returns empty list when no simulation log files are found."""
        audit = _load_audit_module()
        _query_simulation_audit_logs = audit._query_simulation_audit_logs

        monkeypatch.chdir(tmp_path)

        logs = await _query_simulation_audit_logs(
            start_time=None, end_time=None,
            event_type=None, job_id=None, limit=10,
        )

        assert logs == []


# ---------------------------------------------------------------------------
# Arbiter decryption tests
# ---------------------------------------------------------------------------

class TestArbiterDecryption:
    """Verify that _query_arbiter_audit_logs decrypts entries and handles errors."""

    def _setup_arbiter_mock(self, audit_module, mock_logger):
        """Inject mock TamperEvidentLogger into sys.modules for inner imports."""
        import types
        arbiter_audit_mod = types.ModuleType("self_fixing_engineer.arbiter.audit_log")
        arbiter_audit_mod.TamperEvidentLogger = MagicMock()
        arbiter_audit_mod.TamperEvidentLogger.get_instance.return_value = mock_logger
        arbiter_audit_mod.AuditLoggerConfig = MagicMock()
        sys.modules["self_fixing_engineer.arbiter.audit_log"] = arbiter_audit_mod
        return arbiter_audit_mod

    @pytest.mark.asyncio
    async def test_reads_plaintext_entries(self, tmp_path, monkeypatch):
        """Plain-text (non-encrypted) arbiter entries are parsed correctly."""
        audit = _load_audit_module()
        _query_arbiter_audit_logs = audit._query_arbiter_audit_logs

        log_file = tmp_path / "audit_log.jsonl"
        log_file.write_text(json.dumps(_make_arbiter_entry(job_id="job-999")) + "\n")

        mock_logger = MagicMock()
        mock_config = MagicMock()
        mock_config.log_path = str(log_file)
        mock_logger.config = mock_config
        mock_logger._decrypt_entry = lambda e: e

        self._setup_arbiter_mock(audit, mock_logger)

        monkeypatch.chdir(tmp_path)
        logs = await _query_arbiter_audit_logs(
            start_time=None, end_time=None,
            event_type=None, job_id=None, limit=10,
        )

        assert len(logs) == 1
        assert logs[0]["job_id"] == "job-999"
        assert logs[0]["event_type"] == "bug_detected"

    @pytest.mark.asyncio
    async def test_decryption_called_on_each_entry(self, tmp_path, monkeypatch):
        """_decrypt_entry is called for every log entry read."""
        audit = _load_audit_module()
        _query_arbiter_audit_logs = audit._query_arbiter_audit_logs

        entries = [_make_arbiter_entry(job_id=f"job-{i}") for i in range(3)]
        log_file = tmp_path / "audit_log.jsonl"
        log_file.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

        mock_logger = MagicMock()
        mock_config = MagicMock()
        mock_config.log_path = str(log_file)
        mock_logger.config = mock_config
        decrypt_calls = []

        def fake_decrypt(entry):
            decrypt_calls.append(entry)
            return entry

        mock_logger._decrypt_entry = fake_decrypt

        self._setup_arbiter_mock(audit, mock_logger)

        monkeypatch.chdir(tmp_path)
        logs = await _query_arbiter_audit_logs(
            start_time=None, end_time=None,
            event_type=None, job_id=None, limit=10,
        )

        assert len(decrypt_calls) == 3
        assert len(logs) == 3

    @pytest.mark.asyncio
    async def test_decryption_error_preserved_and_processing_continues(
        self, tmp_path, monkeypatch
    ):
        """When _decrypt_entry raises, the entry is preserved and remaining entries still processed."""
        audit = _load_audit_module()
        _query_arbiter_audit_logs = audit._query_arbiter_audit_logs

        entry1 = _make_arbiter_entry(job_id="job-fail", encrypt=True)
        entry2 = _make_arbiter_entry(job_id="job-ok")

        log_file = tmp_path / "audit_log.jsonl"
        log_file.write_text(json.dumps(entry1) + "\n" + json.dumps(entry2) + "\n")

        call_count = {"n": 0}

        def flaky_decrypt(entry):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise ValueError("Simulated decryption failure")
            return entry

        mock_logger = MagicMock()
        mock_config = MagicMock()
        mock_config.log_path = str(log_file)
        mock_logger.config = mock_config
        mock_logger._decrypt_entry = flaky_decrypt

        self._setup_arbiter_mock(audit, mock_logger)

        monkeypatch.chdir(tmp_path)
        logs = await _query_arbiter_audit_logs(
            start_time=None, end_time=None,
            event_type=None, job_id=None, limit=10,
        )

        # Both entries should still be present (first one uses original entry dict)
        assert len(logs) == 2
        job_ids = {log["job_id"] for log in logs}
        assert "job-ok" in job_ids

    @pytest.mark.asyncio
    async def test_encrypted_details_field_is_handled_gracefully(
        self, tmp_path, monkeypatch
    ):
        """Entries with a string details field (encrypted but not decryptable) don't crash."""
        audit = _load_audit_module()
        _query_arbiter_audit_logs = audit._query_arbiter_audit_logs

        entry = _make_arbiter_entry(encrypt=True)  # details is a string
        log_file = tmp_path / "audit_log.jsonl"
        log_file.write_text(json.dumps(entry) + "\n")

        mock_logger = MagicMock()
        mock_config = MagicMock()
        mock_config.log_path = str(log_file)
        mock_logger.config = mock_config
        mock_logger._decrypt_entry = lambda e: e

        self._setup_arbiter_mock(audit, mock_logger)

        monkeypatch.chdir(tmp_path)
        logs = await _query_arbiter_audit_logs(
            start_time=None, end_time=None,
            event_type=None, job_id=None, limit=10,
        )

        # Entry is returned; details falls back to empty dict since it's a string
        assert len(logs) == 1
        assert logs[0]["details"] == {}
        assert logs[0]["job_id"] is None

    @pytest.mark.asyncio
    async def test_job_id_filter_works_after_decryption(self, tmp_path, monkeypatch):
        """job_id filter correctly applies to decrypted details."""
        audit = _load_audit_module()
        _query_arbiter_audit_logs = audit._query_arbiter_audit_logs

        entries = [
            _make_arbiter_entry(job_id="job-match"),
            _make_arbiter_entry(job_id="job-other"),
        ]
        log_file = tmp_path / "audit_log.jsonl"
        log_file.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

        mock_logger = MagicMock()
        mock_config = MagicMock()
        mock_config.log_path = str(log_file)
        mock_logger.config = mock_config
        mock_logger._decrypt_entry = lambda e: e

        self._setup_arbiter_mock(audit, mock_logger)

        monkeypatch.chdir(tmp_path)
        logs = await _query_arbiter_audit_logs(
            start_time=None, end_time=None,
            event_type=None, job_id="job-match", limit=10,
        )

        assert len(logs) == 1
        assert logs[0]["job_id"] == "job-match"
