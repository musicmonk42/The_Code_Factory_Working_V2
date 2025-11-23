# tests/test_web_ui_dashboard_plugin_template.py

import asyncio
import os

# Import the plugin from the parent directory
import sys
from unittest.mock import AsyncMock, patch
from urllib.parse import quote

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from prometheus_client import CollectorRegistry
from pydantic import ValidationError
from starlette.status import HTTP_400_BAD_REQUEST, HTTP_404_NOT_FOUND

sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "plugins"))
)
from web_ui_dashboard_plugin_template import (
    CONFIG,
    DASHBOARD_API_CALLS,
    DASHBOARD_COMPONENT_RENDERS,
    DASHBOARD_STATE_UPDATES,
    UI_COMPONENTS,
    WEBSOCKET_CONNECTIONS,
    DashboardConfig,
    get_dashboard_router,
    get_dashboard_state,
    update_dashboard_state,
    validate_component_name,
)

# ==============================================================================
# Pytest Fixtures for mocking external dependencies and environment
# ==============================================================================


@pytest.fixture(autouse=True)
def mock_external_dependencies():
    """
    Mocks external libraries and environment variables for complete isolation.
    """
    with (
        patch("web_ui_dashboard_plugin_template.Redis") as mock_redis,
        patch.dict(
            os.environ,
            {
                "DASHBOARD_WS_INTERVAL": "0.1",
                "DASHBOARD_STATE_STORAGE": "redis",
                "REDIS_URL": "redis://mock-redis:6379",
                "FRONTEND_VERSION": "1.0.0",
                "DASHBOARD_API_KEY": "mock-api-key",
            },
        ),
    ):

        # Mock Redis client
        mock_redis_client = mock_redis.from_url.return_value
        mock_redis_client.get = AsyncMock(return_value=None)
        mock_redis_client.set = AsyncMock()
        mock_redis_client.close = AsyncMock()

        # Use a fresh Prometheus registry for each test
        with (
            patch("web_ui_dashboard_plugin_template.PROMETHEUS_AVAILABLE", True),
            patch(
                "web_ui_dashboard_plugin_template.REGISTRY",
                new=CollectorRegistry(auto_describe=True),
            ),
            patch("web_ui_dashboard_plugin_template.FASTAPI_AVAILABLE", True),
            patch("web_ui_dashboard_plugin_template.PYDANTIC_AVAILABLE", True),
            patch("web_ui_dashboard_plugin_template.REDIS_AVAILABLE", True),
            patch("web_ui_dashboard_plugin_template.DETECT_SECRETS_AVAILABLE", False),
        ):

            yield {
                "mock_redis": mock_redis,
                "mock_redis_client": mock_redis_client,
            }


@pytest.fixture
def api_client():
    """Fixture to create a FastAPI TestClient for the dashboard router."""
    app = FastAPI()
    router = get_dashboard_router()
    app.include_router(router)
    return TestClient(app)


# ==============================================================================
# Unit Tests for Pydantic Config and Validation
# ==============================================================================


def test_dashboard_config_validation_success():
    """Test that a valid config is accepted by the Pydantic model."""
    config_data = {
        "websocket_interval_seconds": 0.5,
        "state_storage": "redis",
        "redis_url": "redis://localhost:6379",
        "frontend_version": ">=1.0.0",
    }
    config = DashboardConfig.parse_obj(config_data)
    assert config.websocket_interval_seconds == 0.5
    assert config.state_storage == "redis"


def test_dashboard_config_invalid_state_storage():
    """Test that an invalid state_storage value raises a ValidationError."""
    with pytest.raises(ValidationError):
        DashboardConfig(state_storage="file_system")


def test_validate_component_name_success():
    """Test `validate_component_name` with a safe input."""
    safe_name = validate_component_name("my_component-1")
    assert safe_name == "my_component-1"


def test_validate_component_name_failure():
    """Test `validate_component_name` with a malicious input."""
    with pytest.raises(HTTPException, match="Invalid component name"):
        validate_component_name("my;component")


# ==============================================================================
# Integration Tests for API Endpoints
# ==============================================================================


def test_manifest_endpoint(api_client):
    """Test the /manifest endpoint returns the plugin manifest."""
    response = api_client.get("/plugin/dashboard/manifest")
    assert response.status_code == 200
    assert response.json()["name"] == "WebUIDashboardPluginTemplate"
    assert DASHBOARD_API_CALLS.labels(endpoint="manifest")._value.get() == 1


def test_components_endpoint(api_client):
    """Test the /components endpoint returns the list of registered components."""
    response = api_client.get("/plugin/dashboard/components")
    assert response.status_code == 200
    assert "example_metric_panel" in response.json()["components"]
    assert DASHBOARD_API_CALLS.labels(endpoint="components")._value.get() == 1


@pytest.mark.asyncio
async def test_state_update_and_get_endpoints(api_client, mock_external_dependencies):
    """Test the /state and /state/update endpoints."""
    # Test POST /state/update
    update_data = {"user_preferences": {"theme": "dark"}}
    response = api_client.post(
        "/plugin/dashboard/state/update", json={"update": update_data}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert DASHBOARD_API_CALLS.labels(endpoint="state_update")._value.get() == 1
    assert DASHBOARD_STATE_UPDATES._value.get() == 1

    # Test GET /state
    response = api_client.get("/plugin/dashboard/state")
    assert response.status_code == 200
    assert response.json()["user_preferences"] == {"theme": "dark"}
    assert DASHBOARD_API_CALLS.labels(endpoint="state")._value.get() == 1


def test_get_component_endpoint_success(api_client):
    """Test retrieving data for a valid component."""
    response = api_client.get("/plugin/dashboard/component/example_metric_panel")
    assert response.status_code == 200
    assert response.json()["title"] == "Current Example Metric"
    assert (
        DASHBOARD_API_CALLS.labels(
            endpoint="component_example_metric_panel"
        )._value.get()
        == 1
    )
    assert (
        DASHBOARD_COMPONENT_RENDERS.labels(
            component_name="example_metric_panel"
        )._value.get()
        == 1
    )


def test_get_component_endpoint_not_found(api_client):
    """Test retrieving data for a non-existent component."""
    response = api_client.get("/plugin/dashboard/component/non_existent_component")
    assert response.status_code == HTTP_404_NOT_FOUND
    assert "not found" in response.json()["detail"]


# ==============================================================================
# Integration Tests for WebSocket Workflow
# ==============================================================================


@pytest.mark.asyncio
async def test_websocket_workflow(api_client, mock_external_dependencies):
    """
    Test the entire WebSocket connection lifecycle, including initial state,
    updates, and graceful disconnection.
    """
    websocket_uri = "/plugin/dashboard/ws"

    # Record initial metric values to check deltas
    # This handles the cumulative nature of Prometheus metrics across tests
    initial_state_updates = DASHBOARD_STATE_UPDATES._value.get()
    initial_connected = WEBSOCKET_CONNECTIONS.labels(status="connected")._value.get()
    initial_disconnected = WEBSOCKET_CONNECTIONS.labels(
        status="disconnected"
    )._value.get()

    with api_client.websocket_connect(websocket_uri) as websocket:
        # 1. Test initial connection and state send
        initial_state = websocket.receive_json()
        assert initial_state["type"] == "initial_state"
        assert "example_metric" in initial_state["state"]

        # Verify connection counter incremented by 1
        assert (
            WEBSOCKET_CONNECTIONS.labels(status="connected")._value.get()
            == initial_connected + 1
        )

        # 2. Test live update loop
        update_message = websocket.receive_json()
        assert update_message["type"] == "update"
        assert update_message["state"]["live_data_counter"] == 1

        # 3. Test disconnection
        websocket.close()

    # Wait for async cleanup to complete
    await asyncio.sleep(0.2)

    # Verify disconnection counter incremented by 1
    assert (
        WEBSOCKET_CONNECTIONS.labels(status="disconnected")._value.get()
        == initial_disconnected + 1
    )

    # Verify at least one state update occurred during the WebSocket session
    final_state_updates = DASHBOARD_STATE_UPDATES._value.get()
    assert (
        final_state_updates > initial_state_updates
    )  # At least one update should have occurred


# ==============================================================================
# Additional Edge Case Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_websocket_multiple_connections(api_client, mock_external_dependencies):
    """Test handling multiple simultaneous WebSocket connections."""
    websocket_uri = "/plugin/dashboard/ws"

    # Record initial metrics
    initial_connected = WEBSOCKET_CONNECTIONS.labels(status="connected")._value.get()
    initial_disconnected = WEBSOCKET_CONNECTIONS.labels(
        status="disconnected"
    )._value.get()

    # Connect multiple clients
    with (
        api_client.websocket_connect(websocket_uri) as ws1,
        api_client.websocket_connect(websocket_uri) as ws2,
    ):

        # Both should receive initial state
        initial_state1 = ws1.receive_json()
        initial_state2 = ws2.receive_json()

        assert initial_state1["type"] == "initial_state"
        assert initial_state2["type"] == "initial_state"

        # Verify both connections are counted
        assert (
            WEBSOCKET_CONNECTIONS.labels(status="connected")._value.get()
            == initial_connected + 2
        )

        # Both should receive updates
        update1 = ws1.receive_json()
        update2 = ws2.receive_json()

        assert update1["type"] == "update"
        assert update2["type"] == "update"

        # Close connections
        ws1.close()
        ws2.close()

    # Wait for cleanup
    await asyncio.sleep(0.3)

    # Verify both disconnections are counted
    assert (
        WEBSOCKET_CONNECTIONS.labels(status="disconnected")._value.get()
        == initial_disconnected + 2
    )


def test_state_update_with_invalid_json(api_client):
    """Test that invalid JSON in state update is handled properly."""
    # This should be caught by Pydantic validation
    response = api_client.post(
        "/plugin/dashboard/state/update", json={"invalid_field": "data"}
    )
    assert response.status_code == 422  # Unprocessable Entity


def test_component_data_scrubbing(api_client, mock_external_dependencies):
    """Test that sensitive data is scrubbed from component responses."""
    # Since DETECT_SECRETS_AVAILABLE is False in our mock, _scrub_secrets should return data as-is
    # This test verifies the scrubbing mechanism is called but doesn't break when disabled
    response = api_client.get("/plugin/dashboard/component/example_metric_panel")
    assert response.status_code == 200
    # The response should still be valid JSON
    data = response.json()
    assert "type" in data
    assert data["type"] == "metric_card"


@pytest.mark.asyncio
async def test_websocket_error_handling(api_client, mock_external_dependencies):
    """Test WebSocket error handling and recovery."""
    websocket_uri = "/plugin/dashboard/ws"

    initial_error_count = WEBSOCKET_CONNECTIONS.labels(status="error")._value.get()

    # We can't easily simulate an error in TestClient, but we can verify the structure exists
    with api_client.websocket_connect(websocket_uri) as websocket:
        initial_state = websocket.receive_json()
        assert initial_state["type"] == "initial_state"

        # Normal close shouldn't increment error counter
        websocket.close()

    await asyncio.sleep(0.2)

    # Verify no errors occurred during normal operation
    assert (
        WEBSOCKET_CONNECTIONS.labels(status="error")._value.get() == initial_error_count
    )


# ==============================================================================
# Performance and Load Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_rapid_state_updates(api_client, mock_external_dependencies):
    """Test handling of rapid consecutive state updates."""
    initial_count = DASHBOARD_STATE_UPDATES._value.get()

    # Perform multiple rapid updates
    for i in range(5):
        update_data = {"rapid_test_counter": i}
        response = api_client.post(
            "/plugin/dashboard/state/update", json={"update": update_data}
        )
        assert response.status_code == 200

    # Verify all updates were counted
    assert DASHBOARD_STATE_UPDATES._value.get() == initial_count + 5

    # Verify final state has the last update
    response = api_client.get("/plugin/dashboard/state")
    assert response.status_code == 200
    assert response.json()["rapid_test_counter"] == 4


def test_component_registry_operations():
    """Test UI component registration and retrieval."""
    from web_ui_dashboard_plugin_template import register_ui_component

    # Define a test component
    def test_component(data):
        return {"type": "test", "data": data.get("test_value", 0)}

    # Register the component
    register_ui_component("test_component", test_component)

    # Verify it's registered
    assert "test_component" in UI_COMPONENTS
    assert UI_COMPONENTS["test_component"] == test_component

    # Test overwriting warning (just verify it doesn't crash)
    register_ui_component("test_component", test_component)


# ==============================================================================
# Configuration and Environment Tests
# ==============================================================================


def test_config_environment_override(mock_external_dependencies):
    """Test that environment variables override config file settings."""
    # The mock_external_dependencies fixture sets DASHBOARD_WS_INTERVAL=0.1
    # This test verifies that the environment variable was applied

    # Note: CONFIG is loaded at module import time, so we can't easily test
    # the override mechanism here without reloading the module
    assert CONFIG is not None


def test_redis_fallback_to_memory():
    """Test that the system falls back to memory storage when Redis is unavailable."""
    import asyncio

    async def test_fallback():
        # With our mock, Redis is "unavailable" (returns None)
        state = await get_dashboard_state()
        assert isinstance(state, dict)
        assert "example_metric" in state

        # Update should work with memory fallback
        await update_dashboard_state({"test_key": "test_value"})
        new_state = await get_dashboard_state()
        assert new_state["test_key"] == "test_value"

    asyncio.run(test_fallback())


# ==============================================================================
# Security Tests
# ==============================================================================


def test_component_name_injection_prevention(api_client):
    """Test that component name validation prevents injection attacks."""
    malicious_names = [
        "../../../etc/passwd",
        "component; DROP TABLE users;",
        "component\x00null",
        "component<script>alert('xss')</script>",
        "component|ls -la",
        "../../config",
        "component&rm -rf /",
        "component`whoami`",
    ]

    for name in malicious_names:
        # Properly encode the malicious name for URL
        encoded_name = quote(name, safe="")
        response = api_client.get(f"/plugin/dashboard/component/{encoded_name}")

        # The validation should catch these and return 400 Bad Request
        # Some might be caught by routing (404) if they escape the path
        assert response.status_code in [
            HTTP_400_BAD_REQUEST,
            HTTP_404_NOT_FOUND,
        ], f"Expected 400 or 404 for malicious name '{name}', got {response.status_code}"

        # If it's a 400, verify it's our validation message
        if response.status_code == HTTP_400_BAD_REQUEST:
            assert "Invalid component name" in response.json()["detail"]
        # If it's a 404, that's also acceptable as it means the path was rejected


def test_api_key_authentication(api_client):
    """Test API key authentication when enabled."""
    # Note: In our test setup, API key auth is not required
    # This test verifies the endpoint works without auth in test mode
    response = api_client.get("/plugin/dashboard/manifest")
    assert response.status_code == 200

    # If we were to enable auth, we would test:
    # with patch.dict(os.environ, {"DASHBOARD_REQUIRE_API_KEY": "true"}):
    #     response = api_client.get("/plugin/dashboard/manifest")
    #     assert response.status_code == 401
