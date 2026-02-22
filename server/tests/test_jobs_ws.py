# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Tests for the API v2 WebSocket job status endpoint (server/routers/jobs_ws.py).

Uses FastAPI's TestClient.websocket_connect() to exercise the endpoint
without a live server.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# sse_starlette is required by server.routers.__init__ (events router)
pytest.importorskip("sse_starlette", reason="sse_starlette not installed")

# Import directly from the module, bypassing the package __init__ which
# pulls in sse_starlette and other optional dependencies
from server.routers.jobs_ws import (
    _build_message,
    _check_rate_limit,
    _active_connections_by_ip,
    _connection_attempts,
    _all_active_connections,
    router,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_connection_state():
    """Reset global connection-tracking state before each test."""
    _active_connections_by_ip.clear()
    _connection_attempts.clear()
    _all_active_connections.clear()
    yield
    _active_connections_by_ip.clear()
    _connection_attempts.clear()
    _all_active_connections.clear()


@pytest.fixture()
def app():
    """Minimal FastAPI app with only the jobs_ws router included."""
    _app = FastAPI()
    _app.include_router(router, prefix="/api")
    return _app


@pytest.fixture()
def client(app):
    return TestClient(app)


# ---------------------------------------------------------------------------
# _build_message helper
# ---------------------------------------------------------------------------


class TestBuildMessage:
    """Unit tests for the _build_message helper function."""

    def test_complete_topic_maps_to_job_complete(self) -> None:
        payload = {"topic": "job.abc.complete", "status": "complete", "files": []}
        msg = _build_message(payload, "abc")
        assert msg["event"] == "job_complete"
        assert msg["job_id"] == "abc"

    def test_failed_topic_maps_to_job_failed(self) -> None:
        payload = {"topic": "job.abc.failed", "error": "oops"}
        msg = _build_message(payload, "abc")
        assert msg["event"] == "job_failed"
        assert "oops" in msg["error"]

    def test_status_failed_maps_to_job_failed(self) -> None:
        payload = {"status": "failed", "error": "network error"}
        msg = _build_message(payload, "xyz")
        assert msg["event"] == "job_failed"

    def test_generic_payload_maps_to_stage_progress(self) -> None:
        payload = {"stage": "CODEGEN", "percent": 50}
        msg = _build_message(payload, "j1")
        assert msg["event"] == "stage_progress"
        assert msg["stage"] == "CODEGEN"
        assert msg["percent"] == 50

    def test_job_id_injected_when_missing(self) -> None:
        payload = {"stage": "READ_MD", "percent": 10}
        msg = _build_message(payload, "injected-id")
        assert msg["job_id"] == "injected-id"

    def test_existing_job_id_preserved(self) -> None:
        payload = {"job_id": "original-id", "stage": "VALIDATE", "percent": 90}
        msg = _build_message(payload, "other-id")
        assert msg["job_id"] == "original-id"

    def test_timestamp_present(self) -> None:
        payload = {}
        msg = _build_message(payload, "t1")
        assert "timestamp" in msg


# ---------------------------------------------------------------------------
# _check_rate_limit helper
# ---------------------------------------------------------------------------


class TestCheckRateLimit:
    """Unit tests for the _check_rate_limit helper."""

    def test_new_ip_is_allowed(self) -> None:
        allowed, reason = _check_rate_limit("1.2.3.4")
        assert allowed is True

    def test_ip_at_max_connections_is_denied(self) -> None:
        _active_connections_by_ip["5.6.7.8"] = 5
        allowed, reason = _check_rate_limit("5.6.7.8")
        assert allowed is False
        assert "5.6.7.8" in reason

    def test_ip_at_max_rate_is_denied(self) -> None:
        import time
        now = time.time()
        _connection_attempts["9.10.11.12"] = [now] * 10
        allowed, reason = _check_rate_limit("9.10.11.12")
        assert allowed is False


# ---------------------------------------------------------------------------
# WebSocket endpoint via TestClient
# ---------------------------------------------------------------------------


class TestJobStatusWebSocket:
    """Integration tests using FastAPI TestClient.websocket_connect()."""

    def _mock_omnicore(self):
        """Return a mock OmniCoreService with no message bus."""
        svc = MagicMock()
        svc._message_bus = None
        svc._omnicore_components_available = {"message_bus": False}
        return svc

    def test_connection_accepted_and_connected_event_received(self, client) -> None:
        """WebSocket connection must be accepted and emit a 'connected' event."""
        with patch(
            "server.routers.jobs_ws._get_omnicore_service",
            return_value=self._mock_omnicore(),
        ):
            with client.websocket_connect("/api/v2/jobs/test-job-1/ws") as ws:
                msg = ws.receive_json()
                assert msg["event"] == "connected"
                assert msg["job_id"] == "test-job-1"
                assert "connection_id" in msg
                assert "timestamp" in msg

    def test_heartbeat_sent_when_no_events(self, client) -> None:
        """Without message-bus events, the endpoint must eventually send a heartbeat."""
        import asyncio

        # Patch asyncio.wait_for to immediately raise TimeoutError on first call
        original_wait_for = asyncio.wait_for
        call_count = [0]

        async def fast_timeout(coro, timeout):
            call_count[0] += 1
            if call_count[0] <= 1:
                coro.close()
                raise asyncio.TimeoutError()
            return await original_wait_for(coro, timeout)

        with patch(
            "server.routers.jobs_ws._get_omnicore_service",
            return_value=self._mock_omnicore(),
        ), patch("asyncio.wait_for", side_effect=fast_timeout):
            with client.websocket_connect("/api/v2/jobs/hb-job/ws") as ws:
                msg = ws.receive_json()  # connected
                assert msg["event"] == "connected"
                msg2 = ws.receive_json()  # heartbeat
                assert msg2["event"] == "heartbeat"
                assert msg2["job_id"] == "hb-job"

    def test_job_id_reflected_in_messages(self, client) -> None:
        """The job_id path parameter must appear in all server messages."""
        with patch(
            "server.routers.jobs_ws._get_omnicore_service",
            return_value=self._mock_omnicore(),
        ):
            with client.websocket_connect("/api/v2/jobs/specific-job/ws") as ws:
                msg = ws.receive_json()
                assert msg["job_id"] == "specific-job"

    def test_rate_limit_rejects_excess_connections(self, client) -> None:
        """An IP that has reached MAX_CONNECTIONS_PER_IP should be rejected."""
        _active_connections_by_ip["testclient"] = 5  # Saturate the limit

        # TestClient uses host "testclient" for the mock client IP
        with pytest.raises(Exception):
            with client.websocket_connect("/api/v2/jobs/blocked-job/ws") as ws:
                ws.receive_json()
