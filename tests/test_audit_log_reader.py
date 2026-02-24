# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Tests for the unified audit log router in server/routers/audit.py.

Validates:
- All module queries are routed through OmniCoreService.route_job()
  (no direct module imports, no direct file reads)
- _query_via_omnicore() extracts the logs list from OmniCore's response
- query_all_audit_logs() returns the correct aggregated structure
- Graceful degradation when OmniCore is unavailable
"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Direct module loader – avoids executing server/routers/__init__.py and
# pulling in every router dependency (fastapi, aiofiles, sse_starlette, …)
# ---------------------------------------------------------------------------

def _load_audit_module():
    """Load server/routers/audit.py by file path, bypassing __init__.py."""
    import types

    # Stub fastapi if not present
    if "fastapi" not in sys.modules:
        fastapi_stub = types.ModuleType("fastapi")
        # Use a pass-through router stub so decorated functions remain callable
        class _RouterStub:
            def __init__(self, *a, **kw):
                pass
            def get(self, *a, **kw):
                return lambda f: f
            def post(self, *a, **kw):
                return lambda f: f
        fastapi_stub.APIRouter = _RouterStub
        fastapi_stub.Depends = lambda f: f
        fastapi_stub.HTTPException = Exception
        fastapi_stub.Query = lambda *a, **kw: None
        sys.modules["fastapi"] = fastapi_stub

    # Stub server.services if not present
    for mod_name in (
        "server",
        "server.services",
        "server.services.omnicore_service",
    ):
        if mod_name not in sys.modules:
            sys.modules[mod_name] = types.ModuleType(mod_name)

    omni = sys.modules["server.services.omnicore_service"]
    if not hasattr(omni, "OmniCoreService"):
        omni.OmniCoreService = MagicMock
        omni.get_omnicore_service = MagicMock()

    # Force a fresh load so module-level state (_ingest_store etc.) is reset.
    if "server.routers.audit" in sys.modules:
        del sys.modules["server.routers.audit"]

    project_root = Path(__file__).parent.parent
    audit_path = project_root / "server" / "routers" / "audit.py"
    spec = importlib.util.spec_from_file_location("server.routers.audit", audit_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["server.routers.audit"] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_omnicore_result(logs=None):
    """Return a dict that mimics omnicore_service.route_job() response structure.

    OmniCore wraps audit logs under ``result["data"]["logs"]``.
    _query_via_omnicore() extracts that nested list, so tests must supply
    this exact shape to exercise the extraction logic correctly.
    """
    return {
        "job_id": "audit_query",
        "routed": True,
        "data": {"logs": logs or []},
    }


# ---------------------------------------------------------------------------
# _query_via_omnicore tests
# ---------------------------------------------------------------------------

class TestQueryViaOmnicore:
    """Verify that _query_via_omnicore delegates to omnicore_service.route_job."""

    @pytest.mark.asyncio
    async def test_routes_generator_to_generator_target(self):
        """generator module should use target_module='generator'."""
        audit = _load_audit_module()
        mock_svc = MagicMock()
        mock_svc.route_job = AsyncMock(return_value=_make_omnicore_result())

        await audit._query_via_omnicore(
            omnicore_service=mock_svc,
            module="generator",
            start_time=None, end_time=None,
            event_type=None, job_id=None, limit=10,
        )

        call_kwargs = mock_svc.route_job.call_args[1]
        assert call_kwargs["target_module"] == "generator"

    @pytest.mark.asyncio
    async def test_routes_sfe_modules_to_sfe_target(self):
        """arbiter, testgen, simulation, guardrails → target_module='sfe'."""
        audit = _load_audit_module()
        for mod in ("arbiter", "testgen", "simulation", "guardrails"):
            mock_svc = MagicMock()
            mock_svc.route_job = AsyncMock(return_value=_make_omnicore_result())

            await audit._query_via_omnicore(
                omnicore_service=mock_svc,
                module=mod,
                start_time=None, end_time=None,
                event_type=None, job_id=None, limit=10,
            )

            call_kwargs = mock_svc.route_job.call_args[1]
            assert call_kwargs["target_module"] == "sfe", f"Wrong target for module={mod}"

    @pytest.mark.asyncio
    async def test_payload_contains_expected_fields(self):
        """route_job payload should carry action, module, and filter parameters."""
        audit = _load_audit_module()
        mock_svc = MagicMock()
        mock_svc.route_job = AsyncMock(return_value=_make_omnicore_result())

        await audit._query_via_omnicore(
            omnicore_service=mock_svc,
            module="arbiter",
            start_time="2026-01-01T00:00:00Z",
            end_time="2026-12-31T00:00:00Z",
            event_type="bug_detection",
            job_id="job-999",
            limit=50,
        )

        payload = mock_svc.route_job.call_args[1]["payload"]
        assert payload["action"] == "query_audit_logs"
        assert payload["module"] == "arbiter"
        assert payload["start_time"] == "2026-01-01T00:00:00Z"
        assert payload["end_time"] == "2026-12-31T00:00:00Z"
        assert payload["event_type"] == "bug_detection"
        assert payload["job_id"] == "job-999"
        assert payload["limit"] == 50

    @pytest.mark.asyncio
    async def test_returns_logs_list_from_data(self):
        """Extracts the logs list from result['data']['logs']."""
        audit = _load_audit_module()
        sample_logs = [{"timestamp": "2026-01-01T00:00:00Z", "event_type": "test"}]
        mock_svc = MagicMock()
        mock_svc.route_job = AsyncMock(return_value=_make_omnicore_result(logs=sample_logs))

        result = await audit._query_via_omnicore(
            omnicore_service=mock_svc,
            module="generator",
            start_time=None, end_time=None,
            event_type=None, job_id=None, limit=10,
        )

        assert result == sample_logs

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_data_has_no_logs(self):
        """Returns [] when OmniCore returns data without a logs key."""
        audit = _load_audit_module()
        mock_svc = MagicMock()
        mock_svc.route_job = AsyncMock(return_value={"job_id": "x", "data": {}})

        result = await audit._query_via_omnicore(
            omnicore_service=mock_svc,
            module="testgen",
            start_time=None, end_time=None,
            event_type=None, job_id=None, limit=10,
        )

        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_omnicore_service_is_none(self):
        """Returns [] without calling route_job when omnicore_service is None."""
        audit = _load_audit_module()

        result = await audit._query_via_omnicore(
            omnicore_service=None,
            module="simulation",
            start_time=None, end_time=None,
            event_type=None, job_id=None, limit=10,
        )

        assert result == []

    @pytest.mark.asyncio
    async def test_uses_job_id_as_route_job_job_id(self):
        """When job_id is provided it is passed as the route_job job_id."""
        audit = _load_audit_module()
        mock_svc = MagicMock()
        mock_svc.route_job = AsyncMock(return_value=_make_omnicore_result())

        await audit._query_via_omnicore(
            omnicore_service=mock_svc,
            module="generator",
            start_time=None, end_time=None,
            event_type=None, job_id="my-job",
            limit=10,
        )

        assert mock_svc.route_job.call_args[1]["job_id"] == "my-job"

    @pytest.mark.asyncio
    async def test_falls_back_to_audit_query_when_no_job_id(self):
        """When job_id is None, route_job is called with job_id='audit_query'."""
        audit = _load_audit_module()
        mock_svc = MagicMock()
        mock_svc.route_job = AsyncMock(return_value=_make_omnicore_result())

        await audit._query_via_omnicore(
            omnicore_service=mock_svc,
            module="guardrails",
            start_time=None, end_time=None,
            event_type=None, job_id=None,
            limit=10,
        )

        assert mock_svc.route_job.call_args[1]["job_id"] == "audit_query"


# ---------------------------------------------------------------------------
# query_all_audit_logs aggregation tests
# ---------------------------------------------------------------------------

class TestQueryAllAuditLogsAggregation:
    """Verify end-to-end aggregation in query_all_audit_logs."""

    def _make_service(self, logs_per_module=None):
        """Create a mock OmniCoreService that returns logs for each module."""
        mock_svc = MagicMock()
        mock_svc.route_job = AsyncMock(
            return_value=_make_omnicore_result(logs=logs_per_module or [])
        )
        mock_svc.get_audit_trail = AsyncMock(return_value=[])
        return mock_svc

    @pytest.mark.asyncio
    async def test_all_six_modules_queried_by_default(self):
        """Without a module filter all six modules appear in modules_queried."""
        audit = _load_audit_module()
        mock_svc = self._make_service()

        result = await audit.query_all_audit_logs(
            module=None, event_type=None, job_id=None,
            start_time=None, end_time=None, limit=10,
            omnicore_service=mock_svc,
        )

        assert set(result["modules_queried"]) == {
            "generator", "arbiter", "testgen", "simulation", "omnicore", "guardrails"
        }

    @pytest.mark.asyncio
    async def test_single_module_filter(self):
        """When module='generator' only generator is queried."""
        audit = _load_audit_module()
        mock_svc = self._make_service()

        result = await audit.query_all_audit_logs(
            module="generator", event_type=None, job_id=None,
            start_time=None, end_time=None, limit=10,
            omnicore_service=mock_svc,
        )

        assert result["modules_queried"] == ["generator"]

    @pytest.mark.asyncio
    async def test_logs_are_tagged_with_module_name(self):
        """Each returned log entry carries a 'module' field."""
        audit = _load_audit_module()
        sample_log = {"timestamp": "2026-01-01T00:00:00Z", "event_type": "x"}
        mock_svc = self._make_service(logs_per_module=[sample_log])

        result = await audit.query_all_audit_logs(
            module="arbiter", event_type=None, job_id=None,
            start_time=None, end_time=None, limit=10,
            omnicore_service=mock_svc,
        )

        assert result["aggregated_logs"][0]["module"] == "arbiter"

    @pytest.mark.asyncio
    async def test_graceful_degradation_when_omnicore_none(self):
        """Returns a well-formed error response when omnicore_service is None."""
        audit = _load_audit_module()

        result = await audit.query_all_audit_logs(
            module=None, event_type=None, job_id=None,
            start_time=None, end_time=None, limit=10,
            omnicore_service=None,
        )

        assert result["aggregated_logs"] == []
        assert result["total_count"] == 0
        assert result["errors"] is not None

    @pytest.mark.asyncio
    async def test_response_shape_preserved(self):
        """Response always contains the documented keys."""
        audit = _load_audit_module()
        mock_svc = self._make_service()

        result = await audit.query_all_audit_logs(
            module=None, event_type=None, job_id=None,
            start_time=None, end_time=None, limit=10,
            omnicore_service=mock_svc,
        )

        for key in ("aggregated_logs", "total_count", "modules_queried", "metadata"):
            assert key in result, f"Missing key: {key}"
        for meta_key in ("query_timestamp", "module_filter", "event_type_filter",
                         "job_id_filter", "start_time", "end_time", "limit"):
            assert meta_key in result["metadata"], f"Missing metadata key: {meta_key}"

    @pytest.mark.asyncio
    async def test_no_direct_module_imports_in_route_job_calls(self):
        """route_job source_module is always 'api'."""
        audit = _load_audit_module()
        mock_svc = self._make_service()

        await audit.query_all_audit_logs(
            module="simulation", event_type=None, job_id=None,
            start_time=None, end_time=None, limit=10,
            omnicore_service=mock_svc,
        )

        for call in mock_svc.route_job.call_args_list:
            assert call[1]["source_module"] == "api"


# ---------------------------------------------------------------------------
# POST /audit/ingest endpoint tests
# ---------------------------------------------------------------------------

class TestIngestAuditEvent:
    """Verify the POST /audit/ingest endpoint stores events and returns the right shape."""

    @pytest.mark.asyncio
    async def test_ingest_stores_event_and_returns_ingested_true(self):
        """A valid ingest request stores the event and returns ingested=True."""
        audit = _load_audit_module()

        request = audit.AuditIngestRequest(
            module="generator",
            event_type="code_generated",
            job_id="job-abc",
        )
        result = await audit.ingest_audit_event(request)

        assert result["ingested"] is True
        assert result["module"] == "generator"
        assert "timestamp" in result

    @pytest.mark.asyncio
    async def test_ingest_auto_sets_timestamp_when_omitted(self):
        """When timestamp is omitted the endpoint provides a UTC timestamp."""
        audit = _load_audit_module()

        request = audit.AuditIngestRequest(module="arbiter", event_type="bug_detection")
        result = await audit.ingest_audit_event(request)

        assert result["timestamp"]  # non-empty string
        stored = audit._ingest_store["arbiter"][0]
        assert stored["timestamp"] == result["timestamp"]

    @pytest.mark.asyncio
    async def test_ingest_preserves_explicit_timestamp(self):
        """A caller-supplied timestamp must be preserved unchanged."""
        audit = _load_audit_module()
        ts = "2026-01-15T12:00:00Z"

        request = audit.AuditIngestRequest(
            module="testgen", event_type="test_run", timestamp=ts
        )
        result = await audit.ingest_audit_event(request)

        assert result["timestamp"] == ts
        assert audit._ingest_store["testgen"][0]["timestamp"] == ts

    @pytest.mark.asyncio
    async def test_ingest_does_not_mutate_request_model(self):
        """The endpoint must not alter the Pydantic model passed to it."""
        audit = _load_audit_module()

        request = audit.AuditIngestRequest(module="simulation", event_type="agent_decision")
        original_ts = request.timestamp  # None before ingest

        await audit.ingest_audit_event(request)

        # The model's timestamp field must still be None; mutation would change it
        assert request.timestamp == original_ts

    @pytest.mark.asyncio
    async def test_ingest_enforces_per_module_cap(self):
        """When the store is full, the oldest entry is dropped to stay within the cap."""
        audit = _load_audit_module()
        cap = audit._MAX_INGEST_PER_MODULE

        # Pre-fill to exactly the cap using a fresh list so we don't pay the cost
        # of calling the endpoint cap times during the test.
        audit._ingest_store["guardrails"] = [
            {"module": "guardrails", "event_type": "x", "timestamp": f"2026-01-{i:02d}T00:00:00Z"}
            for i in range(1, cap + 1)
        ]
        oldest_ts = audit._ingest_store["guardrails"][0]["timestamp"]

        # Ingest one more event – the oldest should be evicted
        request = audit.AuditIngestRequest(
            module="guardrails", event_type="compliance_check", timestamp="2026-12-01T00:00:00Z"
        )
        await audit.ingest_audit_event(request)

        store = audit._ingest_store["guardrails"]
        assert len(store) == cap, "Store must remain at the cap after eviction"
        timestamps = [e["timestamp"] for e in store]
        assert oldest_ts not in timestamps, "Oldest entry must have been evicted"


# ---------------------------------------------------------------------------
# Ingest store filtering in _query_via_omnicore
# ---------------------------------------------------------------------------

class TestQueryViaOmnicoreIngestStoreIntegration:
    """Verify that _query_via_omnicore merges ingested events with OmniCore results."""

    @pytest.mark.asyncio
    async def test_ingested_events_appear_in_query_result(self):
        """Events pushed via ingest appear when querying the same module."""
        audit = _load_audit_module()
        # Seed the ingest store directly
        audit._ingest_store["arbiter"] = [
            {"module": "arbiter", "event_type": "bug_detection", "timestamp": "2026-06-01T10:00:00Z", "job_id": "j1"},
        ]
        mock_svc = MagicMock()
        mock_svc.route_job = AsyncMock(return_value={"job_id": "audit_query", "data": {"logs": []}})

        result = await audit._query_via_omnicore(
            omnicore_service=mock_svc,
            module="arbiter",
            start_time=None, end_time=None, event_type=None, job_id=None, limit=10,
        )

        assert len(result) == 1
        assert result[0]["event_type"] == "bug_detection"

    @pytest.mark.asyncio
    async def test_ingested_events_filtered_by_event_type(self):
        """event_type filter applies to ingested events."""
        audit = _load_audit_module()
        audit._ingest_store["generator"] = [
            {"module": "generator", "event_type": "code_generated", "timestamp": "2026-06-01T10:00:00Z"},
            {"module": "generator", "event_type": "test_generated", "timestamp": "2026-06-01T11:00:00Z"},
        ]
        mock_svc = MagicMock()
        mock_svc.route_job = AsyncMock(return_value={"job_id": "x", "data": {"logs": []}})

        result = await audit._query_via_omnicore(
            omnicore_service=mock_svc,
            module="generator",
            start_time=None, end_time=None,
            event_type="code_generated",
            job_id=None, limit=10,
        )

        assert len(result) == 1
        assert result[0]["event_type"] == "code_generated"

    @pytest.mark.asyncio
    async def test_ingested_events_filtered_by_time_range(self):
        """start_time / end_time filters apply to ingested events."""
        audit = _load_audit_module()
        audit._ingest_store["testgen"] = [
            {"module": "testgen", "event_type": "test_run", "timestamp": "2026-01-01T00:00:00Z"},
            {"module": "testgen", "event_type": "test_run", "timestamp": "2026-06-01T00:00:00Z"},
            {"module": "testgen", "event_type": "test_run", "timestamp": "2026-12-01T00:00:00Z"},
        ]
        mock_svc = MagicMock()
        mock_svc.route_job = AsyncMock(return_value={"job_id": "x", "data": {"logs": []}})

        result = await audit._query_via_omnicore(
            omnicore_service=mock_svc,
            module="testgen",
            start_time="2026-02-01T00:00:00Z",
            end_time="2026-11-01T00:00:00Z",
            event_type=None, job_id=None, limit=10,
        )

        assert len(result) == 1
        assert result[0]["timestamp"] == "2026-06-01T00:00:00Z"

    @pytest.mark.asyncio
    async def test_ingested_events_combined_with_omnicore_logs(self):
        """route_job logs and ingest store logs are merged together."""
        audit = _load_audit_module()
        omnicore_log = {"event_type": "workflow_start", "timestamp": "2026-06-01T09:00:00Z"}
        ingested_log = {"module": "simulation", "event_type": "agent_decision", "timestamp": "2026-06-01T10:00:00Z"}
        audit._ingest_store["simulation"] = [ingested_log]

        mock_svc = MagicMock()
        mock_svc.route_job = AsyncMock(
            return_value={"job_id": "x", "data": {"logs": [omnicore_log]}}
        )

        result = await audit._query_via_omnicore(
            omnicore_service=mock_svc,
            module="simulation",
            start_time=None, end_time=None, event_type=None, job_id=None, limit=10,
        )

        event_types = {e["event_type"] for e in result}
        assert "workflow_start" in event_types
        assert "agent_decision" in event_types

    @pytest.mark.asyncio
    async def test_ingest_store_limit_honoured(self):
        """Combined log count never exceeds the requested limit."""
        audit = _load_audit_module()
        audit._ingest_store["guardrails"] = [
            {"module": "guardrails", "event_type": "compliance_check", "timestamp": f"2026-06-{i:02d}T00:00:00Z"}
            for i in range(1, 20)
        ]
        mock_svc = MagicMock()
        mock_svc.route_job = AsyncMock(return_value={"job_id": "x", "data": {"logs": []}})

        result = await audit._query_via_omnicore(
            omnicore_service=mock_svc,
            module="guardrails",
            start_time=None, end_time=None, event_type=None, job_id=None, limit=5,
        )

        assert len(result) <= 5
