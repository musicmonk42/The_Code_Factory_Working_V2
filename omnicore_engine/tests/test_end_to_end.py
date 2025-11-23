import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from omnicore_engine.cli import command_handlers, parse_args
from omnicore_engine.fastapi_app import app
from omnicore_engine.plugin_registry import PlugInKind, plugin


@pytest.mark.asyncio
async def test_end_to_end_plugin_api(tmp_path):
    # Define a plugin for the end-to-end test
    @plugin(kind=PlugInKind.FIX, name="e2e_plugin", version="1.0.0")
    async def e2e_plugin(data: str) -> dict:
        return {"result": data}

    client = TestClient(app)
    test_file = tmp_path / "test.py"
    test_file.write_text("data")

    # The fix-imports endpoint is expected to use a FIX kind plugin internally.
    with open(test_file, "rb") as f:
        # Note: This test assumes the FastAPI endpoint routes the fix-imports logic
        # through the plugin registry, which will find and execute `e2e_plugin`.
        response = client.post("/fix-imports/", files={"file": ("test.py", f)})

    assert response.status_code == 200
    assert response.json()["suggestion"]["result"] == "data"


@pytest.mark.asyncio
async def test_end_to_end_plugin_cli(tmp_path):
    # Mock the plugin registry's execute method to simulate the CLI calling a plugin
    with patch(
        "omnicore_engine.cli.PLUGIN_REGISTRY.execute",
        AsyncMock(return_value={"result": "test"}),
    ):
        test_file = tmp_path / "test.py"
        test_file.write_text("data")
        args = parse_args(["fix-imports", str(test_file)])
        result = await command_handlers["fix-imports"](args)
        assert result == {"suggestion": {"result": "test"}}


@pytest.mark.asyncio
async def test_end_to_end_audit_workflow(tmp_path):
    # Patch the audit client's methods to prevent real database interactions
    with patch(
        "omnicore_engine.fastapi_app.ExplainAudit.add_entry_async", AsyncMock()
    ), patch(
        "omnicore_engine.fastapi_app.ExplainAudit.export_proof_bundle",
        AsyncMock(return_value={"proof": "merkle_proof"}),
    ):

        client = TestClient(app)

        # This tests the full API path for exporting an audit bundle.
        # It assumes the `add_entry_async` call happens implicitly.
        response = client.get("/admin/audit/export-proof-bundle?user_id=test_user")

        assert response.status_code == 200
        assert "proof" in response.json()["data"]


# --- Test Concurrent Plugin Execution ---


@pytest.mark.asyncio
async def test_concurrent_plugin_execution(tmp_path):
    # Define a plugin to be executed concurrently.
    @plugin(kind=PlugInKind.FIX, name="concurrent_plugin", version="1.0.0")
    async def concurrent_plugin(data: str) -> dict:
        # Adding a small delay to simulate real work and ensure true concurrency.
        await asyncio.sleep(0.01)
        return {"result": data}

    client = TestClient(app)
    test_file = tmp_path / "test.py"
    test_file.write_text("data")

    # Define an async function to make a single API request.
    async def make_request():
        with open(test_file, "rb") as f:
            return client.post("/fix-imports/", files={"file": ("test.py", f)})

    # Create multiple tasks to make concurrent API requests.
    tasks = [make_request() for _ in range(5)]

    # Run all tasks concurrently and wait for them to complete.
    responses = await asyncio.gather(*tasks)

    # Assert that all responses were successful.
    assert all(resp.status_code == 200 for resp in responses)

    # Assert that the content of the responses is as expected.
    for resp in responses:
        assert resp.json()["suggestion"]["result"] == "data"
