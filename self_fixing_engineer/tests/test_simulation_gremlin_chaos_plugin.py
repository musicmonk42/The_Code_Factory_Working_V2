# tests/test_gremlin_chaos_plugin.py

import itertools
import os
import sys
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

# --- Best Practice: Add plugins directory to sys.path for direct imports ---
PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "plugins"))
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)

# Import the plugin module
import gremlin_chaos_plugin


@pytest.fixture(autouse=True)
def clear_gremlin_metrics():
    # Clear metrics dictionary to prevent "already registered" warnings
    gremlin_chaos_plugin._METRICS.clear()
    yield


@pytest.fixture()
def mock_gremlin_and_env():
    """
    Mocks the GremlinApiClient, Prometheus metrics, and environment variables.
    """
    with patch.dict(
        os.environ,
        {
            "GREMLIN_TEAM_ID": "mock-team-id",
            "GREMLIN_API_KEY": "mock-api-key",
            "GREMLIN_API_BASE_URL": "https://api.gremlin.com",
        },
    ):
        # Force re-import with mock env vars to set globals correctly
        if "gremlin_chaos_plugin" in sys.modules:
            del sys.modules["gremlin_chaos_plugin"]
        import gremlin_chaos_plugin

        with (
            patch("gremlin_chaos_plugin.GremlinApiClient") as MockGremlinApiClient,
            patch("gremlin_chaos_plugin._get_client") as mock_get_client,
        ):

            # Create a mock instance of the patched client
            mock_gremlin_client = MockGremlinApiClient.return_value
            mock_gremlin_client.quick_check = AsyncMock(return_value=True)
            mock_gremlin_client.create_attack = AsyncMock(return_value="mock-attack-id")
            mock_gremlin_client.get_attack_status = AsyncMock(
                return_value={"state": "SUCCEEDED"}
            )
            mock_gremlin_client.halt_attack = AsyncMock()

            # Have _get_client return our mock instance
            mock_get_client.return_value = mock_gremlin_client

            # Use a generator for time.monotonic to avoid StopIteration
            mock_monotonic = MagicMock(side_effect=itertools.count(start=0, step=10))

            with (
                patch.object(
                    gremlin_chaos_plugin.CHAOS_ATTACKS_TOTAL,
                    "labels",
                    return_value=MagicMock(),
                ),
                patch.object(
                    gremlin_chaos_plugin.CHAOS_ATTACK_ERRORS_TOTAL,
                    "labels",
                    return_value=MagicMock(),
                ),
                patch.object(
                    gremlin_chaos_plugin.CHAOS_ATTACK_DURATION_SECONDS,
                    "labels",
                    return_value=MagicMock(),
                ),
                patch.object(
                    gremlin_chaos_plugin.GREMLIN_INFLIGHT_ATTACKS,
                    "inc",
                    return_value=MagicMock(),
                ),
                patch.object(
                    gremlin_chaos_plugin.GREMLIN_INFLIGHT_ATTACKS,
                    "dec",
                    return_value=MagicMock(),
                ),
                patch.object(
                    gremlin_chaos_plugin.GREMLIN_HTTP_RESPONSES_TOTAL,
                    "labels",
                    return_value=MagicMock(),
                ),
                patch("time.monotonic", new=mock_monotonic),
                patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
            ):
                yield {
                    "plugin": gremlin_chaos_plugin,
                    "mock_gremlin_client": mock_gremlin_client,
                    "mock_sleep": mock_sleep,
                    "mock_monotonic": mock_monotonic,
                }


@pytest.mark.asyncio
async def test_plugin_health_success(mock_gremlin_and_env):
    plugin = mock_gremlin_and_env["plugin"]
    result = await plugin.plugin_health()
    assert result["status"] == "ok"
    assert any("connectivity/authentication OK" in d for d in result["details"])


@pytest.mark.asyncio
async def test_plugin_health_missing_credentials():
    with patch.dict(os.environ, clear=True, GREMLIN_TEAM_ID="", GREMLIN_API_KEY=""):
        # Force re-import with new env vars
        if "gremlin_chaos_plugin" in sys.modules:
            del sys.modules["gremlin_chaos_plugin"]
        import gremlin_chaos_plugin

        result = await gremlin_chaos_plugin.plugin_health()
        assert result["status"] == "error"
        assert any(
            "Missing GREMLIN_TEAM_ID or GREMLIN_API_KEY" in d for d in result["details"]
        )


@pytest.mark.asyncio
async def test_plugin_health_api_error(mock_gremlin_and_env):
    plugin = mock_gremlin_and_env["plugin"]
    mock_gremlin_and_env["mock_gremlin_client"].quick_check.side_effect = (
        plugin.GremlinApiError("Unauthorized", status=401)
    )
    result = await plugin.plugin_health()
    assert result["status"] == "error"
    assert any("status=401" in d for d in result["details"])


@pytest.mark.asyncio
async def test_run_chaos_experiment_cpu_hog_success(mock_gremlin_and_env):
    plugin = mock_gremlin_and_env["plugin"]
    mock_gremlin_and_env["mock_gremlin_client"].get_attack_status.side_effect = [
        {"state": "PENDING"},
        {"state": "RUNNING"},
        {"state": "SUCCEEDED"},
    ]
    res = await plugin.run_chaos_experiment(
        experiment_type="cpu_hog",
        target_type="Host",
        target_value="mock-host-id",
        duration_seconds=10,
        intensity=50,
    )
    assert res["success"] is True
    assert res["final_state"] == "SUCCEEDED"
    assert res["attack_id"] == "mock-attack-id"
    mock_gremlin_and_env["mock_gremlin_client"].create_attack.assert_called_once()

    expected_calls = [
        call(experiment_type="cpu_hog", status="attempt"),
        call(experiment_type="cpu_hog", status="initiated"),
        call(experiment_type="cpu_hog", status="succeeded"),
    ]
    plugin.CHAOS_ATTACKS_TOTAL.labels.assert_has_calls(expected_calls, any_order=False)
    assert plugin.CHAOS_ATTACKS_TOTAL.labels.return_value.inc.call_count == len(
        expected_calls
    )


@pytest.mark.asyncio
async def test_run_chaos_experiment_network_latency_kubernetes_success(
    mock_gremlin_and_env,
):
    plugin = mock_gremlin_and_env["plugin"]
    mock_gremlin_and_env["mock_gremlin_client"].get_attack_status.side_effect = [
        {"state": "PENDING"},
        {"state": "RUNNING"},
        {"state": "SUCCEEDED"},
    ]
    k8s_target_value = {"namespace": "test-ns", "labels": {"app": "web-app"}}
    res = await plugin.run_chaos_experiment(
        experiment_type="network_latency",
        target_type="Kubernetes",
        target_value=k8s_target_value,
        duration_seconds=10,
        intensity=100,
        delay_milliseconds=100,
        protocol="tcp",
    )
    assert res["success"] is True
    assert res["final_state"] == "SUCCEEDED"
    assert res["attack_id"] == "mock-attack-id"
    mock_gremlin_and_env["mock_gremlin_client"].create_attack.assert_called_once()

    expected_calls = [
        call(experiment_type="network_latency", status="attempt"),
        call(experiment_type="network_latency", status="initiated"),
        call(experiment_type="network_latency", status="succeeded"),
    ]
    plugin.CHAOS_ATTACKS_TOTAL.labels.assert_has_calls(expected_calls, any_order=False)
    assert plugin.CHAOS_ATTACKS_TOTAL.labels.return_value.inc.call_count == len(
        expected_calls
    )


@pytest.mark.asyncio
async def test_run_chaos_experiment_unsupported_type(mock_gremlin_and_env):
    plugin = mock_gremlin_and_env["plugin"]
    res = await plugin.run_chaos_experiment(
        experiment_type="invalid_attack",
        target_type="Host",
        target_value="mock-host",
        duration_seconds=10,
    )
    assert res["success"] is False
    assert res["final_state"] == "VALIDATION_ERROR"
    assert "attack validation error" in res["reason"].lower()
    assert "literal_error" in res["error"].lower()


@pytest.mark.asyncio
async def test_run_chaos_experiment_api_error_on_initiation(mock_gremlin_and_env):
    plugin = mock_gremlin_and_env["plugin"]
    mock_gremlin_and_env["mock_gremlin_client"].create_attack.side_effect = (
        plugin.GremlinApiError("Bad Request", status=400)
    )
    res = await plugin.run_chaos_experiment(
        experiment_type="cpu_hog",
        target_type="Host",
        target_value="mock-host",
        duration_seconds=10,
    )
    assert res["success"] is False
    assert "Gremlin API error" in res["reason"]

    expected_labels_calls = [
        call(experiment_type="cpu_hog", status="attempt"),
        call(experiment_type="cpu_hog", status="api_error"),
    ]
    plugin.CHAOS_ATTACKS_TOTAL.labels.assert_has_calls(
        expected_labels_calls, any_order=False
    )
    plugin.CHAOS_ATTACK_ERRORS_TOTAL.labels.assert_called_with(
        experiment_type="cpu_hog", error_type="api_error"
    )
    assert plugin.CHAOS_ATTACKS_TOTAL.labels.return_value.inc.call_count == len(
        expected_labels_calls
    )
    plugin.CHAOS_ATTACK_ERRORS_TOTAL.labels.return_value.inc.assert_called_once()


@pytest.mark.asyncio
async def test_run_chaos_experiment_monitoring_timeout(mock_gremlin_and_env):
    plugin = mock_gremlin_and_env["plugin"]
    # The first call to get_attack_status returns "PENDING", then all subsequent calls return "RUNNING"
    mock_gremlin_and_env["mock_gremlin_client"].get_attack_status.side_effect = [
        {"state": "PENDING"},
        {"state": "RUNNING"},
        {"state": "RUNNING"},
        {"state": "RUNNING"},
        {"state": "RUNNING"},
        {"state": "RUNNING"},
        {"state": "RUNNING"},
        {"state": "RUNNING"},
        {"state": "RUNNING"},
        {"state": "RUNNING"},
        {"state": "RUNNING"},
    ]
    with patch.dict(
        os.environ,
        {"GREMLIN_POLL_INTERVAL_SECONDS": "5", "GREMLIN_TIMEOUT_SECONDS": "10"},
    ):
        res = await plugin.run_chaos_experiment(
            experiment_type="cpu_hog",
            target_type="Host",
            target_value="mock-host",
            duration_seconds=10,
        )
    assert res["success"] is False
    assert res["final_state"] == "MONITORING_TIMED_OUT"
    assert "timed out" in res["reason"].replace("_", " ").lower()
    mock_gremlin_and_env["mock_gremlin_client"].halt_attack.assert_called_once()

    expected_labels_calls = [
        call(experiment_type="cpu_hog", status="attempt"),
        call(experiment_type="cpu_hog", status="initiated"),
        call(experiment_type="cpu_hog", status="monitoring_timeout"),
        call(experiment_type="cpu_hog", status="failed"),
    ]
    plugin.CHAOS_ATTACKS_TOTAL.labels.assert_has_calls(
        expected_labels_calls, any_order=False
    )
    assert plugin.CHAOS_ATTACKS_TOTAL.labels.return_value.inc.call_count == len(
        expected_labels_calls
    )
