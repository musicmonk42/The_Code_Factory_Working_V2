# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Dict

# Import pandas for test dataframes
import pandas as pd
import pytest
import pytest_asyncio

# Mark all tests in this module as heavy (requires pandas)
pytestmark = pytest.mark.heavy

# Import metrics directly from the module
# Import the FeatureStoreClient - fix the import path
from self_fixing_engineer.arbiter.models.feature_store_client import (
    FS_CALLS_ERRORS,
    FS_CALLS_TOTAL,
    FS_REDACTIONS_TOTAL,
    ConnectionError,
    FeatureStoreClient,
)

# Import centralized OpenTelemetry configuration for testing
from self_fixing_engineer.arbiter.otel_config import get_tracer
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pytest_mock import MockerFixture

# Configure logging for tests
logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)



# Sample environment variables for tests
SAMPLE_ENV = {
    "FEAST_REPO_PATH": "./test_feature_repo",
    "LOG_LEVEL": "DEBUG",
    "SFE_OTEL_EXPORTER_TYPE": "console",
    "ENV": "test",
    "CLUSTER": "test-cluster",
}


@pytest_asyncio.fixture(autouse=True)
async def setup_env(mocker: MockerFixture):
    """Set up environment variables and clean up after tests."""
    # Ensure environment variables are set for the test run
    for key, value in SAMPLE_ENV.items():
        mocker.patch.dict(os.environ, {key: value})
    yield
    # Clear environment variables after tests
    for key in SAMPLE_ENV:
        os.environ.pop(key, None)


@pytest.fixture(scope="module")
def test_tracer():
    """Create tracer for tests - deferred to fixture to avoid collection overhead."""
    from self_fixing_engineer.arbiter.otel_config import get_tracer, get_tracer_safe
    try:
        return get_tracer(__name__)
    except:
        return get_tracer_safe(__name__)


@pytest.fixture(scope="module")
def in_memory_exporter():
    """Create in-memory exporter for tests - deferred to fixture to avoid collection overhead."""
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    return InMemorySpanExporter()



@pytest_asyncio.fixture
async def feature_client(mocker: MockerFixture):
    """Fixture for FeatureStoreClient with mocked Feast dependencies."""
    try:
        from feast import Entity, FeatureStore, FeatureView
        from feast import Field as FeastField
        from feast import ValueType
        from feast.data_source import DataSource
        from feast.errors import (
            FeastObjectNotFoundException,
            FeastProviderError,
            FeastResourceError,
        )

        # Mock Feast dependencies for controlled testing
        mock_fs = mocker.MagicMock(spec=FeatureStore)

        # Fix: list_entities should return a list of Entity objects
        mock_entity = mocker.MagicMock(spec=Entity)
        mock_entity.name = "mock_entity"
        mock_fs.list_entities.return_value = [mock_entity]

        # Fix: list_feature_views for health check
        mock_fv = mocker.MagicMock(spec=FeatureView)
        mock_fv.name = "mock_fv"
        mock_fs.list_feature_views.return_value = [mock_fv]

        # Fix: get_feature_view should return a FeatureView with schema
        mock_field = mocker.MagicMock(spec=FeastField)
        mock_field.name = "daily_login_count"
        mock_field.dtype = mocker.MagicMock()
        mock_field.dtype.name = "INT64"
        mock_fv.schema = [mock_field]
        mock_fv.entities = ["user_id"]
        mock_fv.ttl = timedelta(days=7)
        mock_fv.source = mocker.MagicMock()
        mock_fv.source.__class__.__name__ = "FileSource"
        mock_fv.source.__dict__ = {
            "path": "test.parquet",
            "timestamp_field": "event_timestamp",
        }
        mock_fv.source.timestamp_field = "event_timestamp"
        mock_fs.get_feature_view.return_value = mock_fv

        mock_fs.apply = mocker.MagicMock()
        mock_fs.ingest = mocker.MagicMock()

        # Fix: get_historical_features should return a job with to_df method
        mock_job = mocker.MagicMock()
        mock_job.to_df.return_value = pd.DataFrame({"feature": [1.0]})
        mock_fs.get_historical_features.return_value = mock_job

        # Fix: get_online_features should return a response with to_dict method
        mock_online_response = mocker.MagicMock()
        mock_online_response.to_dict.return_value = {
            "daily_login_count": [2.0],
            "user_id": [101],
        }
        mock_fs.get_online_features.return_value = mock_online_response

        # Patch FeatureStore at the correct location
        mocker.patch(
            "self_fixing_engineer.arbiter.models.feature_store_client.FeatureStore", return_value=mock_fs
        )

        # Mock audit and postgres clients
        mocker.patch(
            "self_fixing_engineer.arbiter.models.feature_store_client.AUDIT_LEDGER_AVAILABLE", False
        )
        mocker.patch("self_fixing_engineer.arbiter.models.feature_store_client.POSTGRES_AVAILABLE", False)

        client = FeatureStoreClient()
        yield client
    except ImportError:
        # If Feast is unavailable, raise to indicate tests cannot run fully
        pytest.skip("Feast library not installed; skipping FeatureStoreClient tests.")


@pytest_asyncio.fixture(autouse=True)
async def clear_metrics_and_traces(in_memory_exporter):
    """Clear Prometheus metrics and OpenTelemetry traces before each test."""
    # Clear in-memory traces
    in_memory_exporter.clear()
    yield


def get_metric_value(metric, **labels):
    """Helper to get metric value with full label set."""
    # Add default test environment labels
    full_labels = {"env": "test", "cluster": "test-cluster"}
    full_labels.update(labels)
    try:
        return metric.labels(**full_labels)._value.get()
    except:
        return 0


@pytest.mark.asyncio
async def test_initialization_success(feature_client):
    """Test successful initialization with valid config."""
    assert feature_client.repo_path == "./test_feature_repo"
    assert feature_client._fs is None
    assert feature_client.metric_labels == {"env": "test", "cluster": "test-cluster"}


@pytest.mark.asyncio
async def test_initialization_missing_repo_path(mocker: MockerFixture):
    """Test initialization with missing repo path."""
    mocker.patch.dict(os.environ, {"FEAST_REPO_PATH": ""}, clear=True)
    with pytest.raises(ValueError, match="Feast repo path.*required"):
        FeatureStoreClient()


@pytest.mark.asyncio
async def test_connect_success(feature_client):
    """Test successful connection to Feast."""
    await feature_client.connect()
    assert feature_client._fs is not None
    assert feature_client._is_connected is True
    # Check metrics with full label set
    assert get_metric_value(FS_CALLS_TOTAL, operation="connect", status="success") == 1
    # Verify traces
    spans = in_memory_exporter.get_finished_spans()
    assert len(spans) >= 1
    assert spans[0].name == "feast_connect"
    assert spans[0].status.is_ok


@pytest.mark.asyncio
async def test_connect_idempotent(feature_client, caplog):
    """Test connect is idempotent."""
    caplog.set_level(logging.INFO)
    await feature_client.connect()
    await feature_client.connect()  # Second call should return early
    assert "Feast FeatureStore already connected" in caplog.text
    assert get_metric_value(FS_CALLS_TOTAL, operation="connect", status="success") == 1


@pytest.mark.asyncio
async def test_connect_failure(mocker: MockerFixture):
    """Test connection failure handling."""
    from feast.errors import FeastProviderError

    mocker.patch(
        "self_fixing_engineer.arbiter.models.feature_store_client.FeatureStore",
        side_effect=FeastProviderError("Connection failed"),
    )
    mocker.patch("self_fixing_engineer.arbiter.models.feature_store_client.AUDIT_LEDGER_AVAILABLE", False)
    mocker.patch("self_fixing_engineer.arbiter.models.feature_store_client.POSTGRES_AVAILABLE", False)

    client = FeatureStoreClient()
    with pytest.raises(ConnectionError, match="Failed to connect to Feast"):
        await client.connect()
    assert (
        get_metric_value(
            FS_CALLS_ERRORS, operation="connect", error_type="FeastProviderError"
        )
        >= 1
    )
    spans = in_memory_exporter.get_finished_spans()
    assert any(span.name == "feast_connect" and not span.status.is_ok for span in spans)


@pytest.mark.asyncio
async def test_disconnect_success(feature_client):
    """Test successful disconnection."""
    await feature_client.connect()
    await feature_client.disconnect()
    assert feature_client._fs is None
    assert feature_client._is_connected is False


@pytest.mark.asyncio
async def test_disconnect_idempotent(feature_client, caplog):
    """Test disconnect is idempotent."""
    caplog.set_level(logging.INFO)
    await feature_client.disconnect()  # Not connected
    assert "Disconnected from Feast Feature Store" in caplog.text
    caplog.clear()
    await feature_client.connect()
    await feature_client.disconnect()
    await feature_client.disconnect()  # Second call
    # Both disconnects should complete without error


@pytest.mark.asyncio
async def test_apply_feature_definitions_success(feature_client):
    """Test successful application of feature definitions."""
    await feature_client.connect()

    # Create mock definitions
    from feast import Entity, ValueType

    mock_entity = Entity(name="test_entity", value_type=ValueType.INT64)
    mock_definitions = [mock_entity]

    await feature_client.apply_feature_definitions(mock_definitions)
    assert feature_client._fs.apply.call_count == 1
    assert (
        get_metric_value(
            FS_CALLS_TOTAL, operation="apply_definitions", status="success"
        )
        == 1
    )

    spans = in_memory_exporter.get_finished_spans()
    apply_span = next(
        (span for span in spans if span.name == "feast_apply_definitions"), None
    )
    assert apply_span is not None
    assert apply_span.attributes["feast.num_definitions"] == 1
    assert apply_span.status.is_ok


@pytest.mark.asyncio
async def test_apply_feature_definitions_not_connected(feature_client):
    """Test apply definitions when not connected."""
    with pytest.raises(RuntimeError, match="Feast FeatureStore not connected"):
        await feature_client.apply_feature_definitions([])


@pytest.mark.asyncio
async def test_apply_feature_definitions_failure(feature_client, mocker: MockerFixture):
    """Test apply definitions failure."""
    await feature_client.connect()
    from feast.errors import FeastProviderError

    mocker.patch.object(
        feature_client._fs, "apply", side_effect=FeastProviderError("Apply failed")
    )

    from feast import Entity, ValueType

    mock_entity = Entity(name="test_entity", value_type=ValueType.INT64)

    with pytest.raises(ValueError, match="Failed to apply definitions"):
        await feature_client.apply_feature_definitions([mock_entity])
    assert (
        get_metric_value(
            FS_CALLS_ERRORS,
            operation="apply_definitions",
            error_type="FeastProviderError",
        )
        >= 1
    )


@pytest.mark.asyncio
async def test_ingest_features_success(feature_client):
    """Test successful feature ingestion."""
    await feature_client.connect()
    mock_df = pd.DataFrame({"col": [1]})

    # Mock wait_for_ingestion to complete immediately
    async def mock_wait(*args, **kwargs):
        return

    feature_client.wait_for_ingestion = mock_wait

    await feature_client.ingest_features("mock_fv", mock_df)
    assert feature_client._fs.ingest.call_count >= 1  # May be batched
    assert (
        get_metric_value(FS_CALLS_TOTAL, operation="ingest_features", status="success")
        == 1
    )

    spans = in_memory_exporter.get_finished_spans()
    ingest_span = next(
        (span for span in spans if span.name == "feast_ingest_features"), None
    )
    assert ingest_span is not None
    assert ingest_span.attributes["feast.feature_view"] == "mock_fv"
    assert ingest_span.attributes["feast.data_rows"] == 1
    assert ingest_span.status.is_ok


@pytest.mark.asyncio
async def test_ingest_features_invalid_data(feature_client):
    """Test ingestion with invalid data_df type."""
    await feature_client.connect()
    with pytest.raises(ValueError, match="Expected a DataFrame-like object"):
        await feature_client.ingest_features("mock_fv", "invalid_data")


@pytest.mark.asyncio
async def test_ingest_features_failure(feature_client, mocker: MockerFixture):
    """Test ingestion failure."""
    await feature_client.connect()
    mock_df = pd.DataFrame({"col": [1]})
    from feast.errors import FeastResourceError

    mocker.patch.object(
        feature_client._fs, "ingest", side_effect=FeastResourceError("Ingest failed")
    )

    with pytest.raises(FeastResourceError):
        await feature_client.ingest_features("mock_fv", mock_df)
    assert (
        get_metric_value(
            FS_CALLS_ERRORS,
            operation="ingest_features",
            error_type="FeastResourceError",
        )
        >= 1
    )


@pytest.mark.asyncio
async def test_get_historical_features_success(feature_client):
    """Test successful historical feature retrieval."""
    await feature_client.connect()
    mock_entity_df = pd.DataFrame({"entity": [1]})
    historical_df = await feature_client.get_historical_features(
        mock_entity_df, ["mock:feature"]
    )

    assert isinstance(historical_df, pd.DataFrame)
    assert not historical_df.empty
    assert (
        get_metric_value(
            FS_CALLS_TOTAL, operation="get_historical_features", status="success"
        )
        == 1
    )

    spans = in_memory_exporter.get_finished_spans()
    hist_span = next(
        (span for span in spans if span.name == "feast_get_historical_features"), None
    )
    assert hist_span is not None
    assert hist_span.attributes["feast.num_entities"] == 1
    assert hist_span.attributes["feast.feature_refs"] == "['mock:feature']"
    assert hist_span.status.is_ok


@pytest.mark.asyncio
async def test_get_historical_features_failure(feature_client, mocker: MockerFixture):
    """Test historical features failure."""
    await feature_client.connect()
    mock_entity_df = pd.DataFrame({"entity": [1]})
    from feast.errors import FeastObjectNotFoundException

    mocker.patch.object(
        feature_client._fs,
        "get_historical_features",
        side_effect=FeastObjectNotFoundException("FV not found"),
    )

    with pytest.raises(FeastObjectNotFoundException):
        await feature_client.get_historical_features(mock_entity_df, ["mock:feature"])
    assert (
        get_metric_value(
            FS_CALLS_ERRORS,
            operation="get_historical_features",
            error_type="FeastObjectNotFoundException",
        )
        >= 1
    )


@pytest.mark.asyncio
async def test_get_online_features_success(feature_client):
    """Test successful online feature retrieval."""
    await feature_client.connect()
    entity_rows = [{"user_id": i} for i in range(5)]

    online_results = await feature_client.get_online_features(
        ["mock:feature"], entity_rows
    )
    assert len(online_results) == 5
    assert isinstance(online_results[0], Dict)
    assert (
        get_metric_value(
            FS_CALLS_TOTAL, operation="get_online_features", status="success"
        )
        == 1
    )

    spans = in_memory_exporter.get_finished_spans()
    online_span = next(
        (span for span in spans if span.name == "feast_get_online_features"), None
    )
    assert online_span is not None
    assert online_span.attributes["feast.num_entity_rows"] == 5
    assert online_span.attributes["feast.feature_refs"] == "['mock:feature']"
    assert online_span.status.is_ok


@pytest.mark.asyncio
async def test_get_online_features_failure(feature_client, mocker: MockerFixture):
    """Test online features failure."""
    await feature_client.connect()
    entity_rows = [{"entity": 1}]
    from feast.errors import FeastProviderError

    mocker.patch.object(
        feature_client._fs,
        "get_online_features",
        side_effect=FeastProviderError("Online store error"),
    )

    with pytest.raises(FeastProviderError):
        await feature_client.get_online_features(["mock:feature"], entity_rows)
    assert (
        get_metric_value(
            FS_CALLS_ERRORS,
            operation="get_online_features",
            error_type="FeastProviderError",
        )
        >= 1
    )


@pytest.mark.asyncio
async def test_context_manager(feature_client):
    """Test async context manager for connect/disconnect."""
    async with feature_client:
        assert feature_client._fs is not None
        assert feature_client._is_connected is True
    assert feature_client._fs is None
    assert feature_client._is_connected is False
    assert get_metric_value(FS_CALLS_TOTAL, operation="connect", status="success") == 1


@pytest.mark.asyncio
async def test_retry_on_connect_failure(mocker: MockerFixture):
    """Test retry mechanism on connection failure."""
    from feast import FeatureStore
    from feast.errors import FeastProviderError

    # Create a mock that fails twice then succeeds
    mock_fs = mocker.MagicMock(spec=FeatureStore)
    mock_fs.list_feature_views.return_value = []

    mocker.patch(
        "self_fixing_engineer.arbiter.models.feature_store_client.FeatureStore",
        side_effect=[
            FeastProviderError("Connect failed"),
            FeastProviderError("Connect failed"),
            mock_fs,
        ],
    )
    mocker.patch("self_fixing_engineer.arbiter.models.feature_store_client.AUDIT_LEDGER_AVAILABLE", False)
    mocker.patch("self_fixing_engineer.arbiter.models.feature_store_client.POSTGRES_AVAILABLE", False)

    client = FeatureStoreClient()
    await client.connect()
    assert client._fs is not None
    assert (
        get_metric_value(
            FS_CALLS_ERRORS, operation="connect", error_type="FeastProviderError"
        )
        == 2
    )
    assert get_metric_value(FS_CALLS_TOTAL, operation="connect", status="success") == 1


@pytest.mark.asyncio
async def test_retry_on_ingest_failure(feature_client, mocker: MockerFixture):
    """Test retry mechanism on ingest_features failure."""
    await feature_client.connect()
    mock_df = pd.DataFrame({"col": [1]})
    from feast.errors import FeastResourceError

    # Mock to fail twice then succeed
    mocker.patch.object(
        feature_client._fs,
        "ingest",
        side_effect=[
            FeastResourceError("Ingest failed"),
            FeastResourceError("Ingest failed"),
            None,  # Success
        ],
    )

    # Mock wait_for_ingestion to complete immediately
    async def mock_wait(*args, **kwargs):
        return

    feature_client.wait_for_ingestion = mock_wait

    await feature_client.ingest_features("mock_fv", mock_df)
    assert (
        get_metric_value(
            FS_CALLS_ERRORS,
            operation="ingest_features",
            error_type="FeastResourceError",
        )
        == 2
    )
    assert (
        get_metric_value(FS_CALLS_TOTAL, operation="ingest_features", status="success")
        == 1
    )


@pytest.mark.asyncio
async def test_concurrent_ingest(feature_client):
    """Test concurrent feature ingestion."""
    await feature_client.connect()

    # Mock wait_for_ingestion to complete immediately
    async def mock_wait(*args, **kwargs):
        return

    feature_client.wait_for_ingestion = mock_wait

    async def ingest_task(fv_name: str, df: pd.DataFrame):
        await feature_client.ingest_features(fv_name, df)

    mock_df = pd.DataFrame({"col": [1]})
    tasks = [ingest_task(f"mock_fv_{i}", mock_df) for i in range(5)]
    await asyncio.gather(*tasks)
    assert (
        get_metric_value(FS_CALLS_TOTAL, operation="ingest_features", status="success")
        == 5
    )


@pytest.mark.asyncio
async def test_health_check_success(feature_client):
    """Test successful health check."""
    await feature_client.connect()
    result = await feature_client.health_check()
    assert result is True
    assert (
        get_metric_value(FS_CALLS_TOTAL, operation="health_check", status="success")
        == 1
    )


@pytest.mark.asyncio
async def test_health_check_failure(feature_client, mocker: MockerFixture):
    """Test health check when not connected."""
    result = await feature_client.health_check()
    assert result is False


@pytest.mark.asyncio
async def test_validate_features_basic(feature_client):
    """Test basic feature validation without GX."""
    await feature_client.connect()

    # Mock get_online_features to return test data
    async def mock_get_online(refs, entities):
        return [
            {
                "user_id": 101,
                "daily_login_count": 5,
                "event_timestamp": datetime.now(timezone.utc),
            }
        ]

    feature_client.get_online_features = mock_get_online

    # Mock get_historical_features for drift detection
    async def mock_get_historical(entity_df, refs):
        return pd.DataFrame({"user_id": [101], "daily_login_count": [5]})

    feature_client.get_historical_features = mock_get_historical

    result = await feature_client.validate_features("user_daily_logins")
    assert "freshness_ok" in result
    assert "drift_detected" in result
    assert (
        get_metric_value(
            FS_CALLS_TOTAL, operation="validate_features", status="success"
        )
        == 1
    )


@pytest.mark.asyncio
async def test_flag_for_redaction(feature_client):
    """Test flagging feature view for redaction."""
    await feature_client.connect()

    # This should work even without Postgres (falls back to audit logging)
    await feature_client.flag_for_redaction(
        "user_daily_logins", "GDPR Right to be Forgotten"
    )
    assert get_metric_value(FS_REDACTIONS_TOTAL, feature_view="user_daily_logins") == 1
    assert (
        get_metric_value(
            FS_CALLS_TOTAL, operation="flag_for_redaction", status="success"
        )
        == 1
    )
