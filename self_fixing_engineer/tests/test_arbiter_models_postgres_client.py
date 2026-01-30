import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Dict

import pytest
import pytest_asyncio

# Import the centralized tracer configuration
from arbiter.otel_config import get_tracer
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

# Import from the correct module
from postgres_client import (
    DB_CALLS_ERRORS,
    DB_CALLS_TOTAL,
    DB_CONNECTIONS_CURRENT,
    PostgresClient,
    PostgresClientConnectionError,
)
from pytest_mock import MockerFixture

# Configure logging for tests
logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)



# Sample environment variables for tests
SAMPLE_ENV = {
    "DATABASE_URL": "postgresql://test_user:test_pass@localhost:5432/test_db",
    "PG_POOL_MIN_SIZE": "1",
    "PG_POOL_MAX_SIZE": "5",
    "PG_POOL_TIMEOUT": "10",
    "PG_SSL_MODE": "prefer",
    "LOG_LEVEL": "DEBUG",
    "SFE_OTEL_EXPORTER_TYPE": "console",
}

SAMPLE_FEEDBACK_DATA = {
    "id": str(uuid.uuid4()),
    "type": "user_feedback",
    "data": {"comment": "Test comment"},
    "timestamp": datetime.now(timezone.utc).isoformat(),
}

SAMPLE_AGENT_KNOWLEDGE_DATA = {
    "domain": "test_domain",
    "key": "test_key",
    "value": {"info": "Test value"},
    "timestamp": datetime.now(timezone.utc).isoformat(),
}


def get_metric_value(metric, **labels):
    """Helper to get metric value with labels."""
    try:
        if labels:
            return metric.labels(**labels)._value.get()
        else:
            return metric._value.get()
    except:
        return 0


@pytest_asyncio.fixture(autouse=True)
async def setup_env(mocker: MockerFixture):
    """Set up environment variables and clean up after tests."""
    for key, value in SAMPLE_ENV.items():
        mocker.patch.dict(os.environ, {key: value})
    yield
    for key in SAMPLE_ENV:
        os.environ.pop(key, None)


@pytest.fixture(scope="module")
def test_tracer():
    """Create tracer for tests - deferred to fixture to avoid collection overhead."""
    from arbiter.otel_config import get_tracer, get_tracer_safe
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
async def pg_client(mocker: MockerFixture):
    """Fixture for PostgresClient with mocked asyncpg dependencies."""
    try:
        import asyncpg
        from asyncpg.pool import Pool
    except ImportError:
        pytest.skip("asyncpg library not installed; skipping PostgresClient tests.")

    # Mock pool
    mock_pool = mocker.MagicMock(spec=Pool)
    mock_conn = mocker.AsyncMock()

    # Configure connection methods
    mock_conn.execute = mocker.AsyncMock(return_value="INSERT 0 1")
    mock_conn.fetch = mocker.AsyncMock(return_value=[{"id": "mock_id"}])
    mock_conn.fetchrow = mocker.AsyncMock(return_value={"id": "mock_id"})
    mock_conn.fetchval = mocker.AsyncMock(return_value=1)

    # Create proper async context manager for acquire
    class MockAcquireContext:
        async def __aenter__(self):
            return mock_conn

        async def __aexit__(self, *args):
            return None

    mock_pool.acquire.return_value = MockAcquireContext()
    mock_pool.close = mocker.AsyncMock()
    mock_pool.get_size.return_value = 1
    mock_pool.is_closed.return_value = False

    # Mock create_pool
    mocker.patch(
        "postgres_client.asyncpg.create_pool", mocker.AsyncMock(return_value=mock_pool)
    )

    client = PostgresClient()
    client.max_retries = 2  # Reduce for faster tests

    yield client


@pytest_asyncio.fixture(autouse=True)
async def clear_metrics_and_traces(in_memory_exporter):
    """Clear Prometheus metrics and OpenTelemetry traces before each test."""
    in_memory_exporter.clear()
    yield


@pytest.mark.asyncio
async def test_initialization_success(pg_client):
    """Test successful initialization with valid config."""
    assert pg_client.db_url == SAMPLE_ENV["DATABASE_URL"]
    assert pg_client.db_type == "postgresql"
    assert pg_client._pool is None


@pytest.mark.asyncio
async def test_connect_success(pg_client):
    """Test successful connection to PostgreSQL."""
    await pg_client.connect()
    assert pg_client._pool is not None
    assert not pg_client._is_closed
    assert (
        get_metric_value(
            DB_CALLS_TOTAL,
            db_type="postgresql",
            operation="connect",
            table="n/a",
            status="success",
        )
        == 1
    )
    assert get_metric_value(DB_CONNECTIONS_CURRENT, db_type="postgresql") == 1

    spans = in_memory_exporter.get_finished_spans()
    connect_span = next((span for span in spans if span.name == "db_connect"), None)
    assert connect_span is not None
    assert connect_span.status.is_ok


@pytest.mark.asyncio
async def test_connect_idempotent(pg_client, caplog):
    """Test connect is idempotent."""
    caplog.set_level(logging.INFO)
    await pg_client.connect()
    await pg_client.connect()  # Second call should return early
    assert "PostgreSQL client already connected" in caplog.text
    # Only one successful connection should be recorded
    assert (
        get_metric_value(
            DB_CALLS_TOTAL,
            db_type="postgresql",
            operation="connect",
            table="n/a",
            status="success",
        )
        == 1
    )


@pytest.mark.asyncio
async def test_connect_failure(mocker: MockerFixture):
    """Test connection failure handling."""
    from asyncpg import exceptions as asyncpg_exceptions

    mocker.patch(
        "postgres_client.asyncpg.create_pool",
        side_effect=asyncpg_exceptions.PostgresError("Connection failed"),
    )

    client = PostgresClient()
    with pytest.raises(
        PostgresClientConnectionError, match="Failed to connect to PostgreSQL"
    ):
        await client.connect()

    assert (
        get_metric_value(
            DB_CALLS_ERRORS,
            db_type="postgresql",
            operation="connect",
            table="n/a",
            error_type="PostgresError",
        )
        >= 1
    )
    assert (
        get_metric_value(
            DB_CALLS_TOTAL,
            db_type="postgresql",
            operation="connect",
            table="n/a",
            status="failure",
        )
        >= 1
    )


@pytest.mark.asyncio
async def test_disconnect_success(pg_client):
    """Test successful disconnection."""
    await pg_client.connect()
    await pg_client.disconnect()
    assert pg_client._pool is None
    assert pg_client._is_closed
    assert (
        get_metric_value(
            DB_CALLS_TOTAL,
            db_type="postgresql",
            operation="disconnect",
            table="n/a",
            status="success",
        )
        == 1
    )
    assert get_metric_value(DB_CONNECTIONS_CURRENT, db_type="postgresql") == 0

    spans = in_memory_exporter.get_finished_spans()
    disconnect_span = next(
        (span for span in spans if span.name == "db_disconnect"), None
    )
    assert disconnect_span is not None
    assert disconnect_span.status.is_ok


@pytest.mark.asyncio
async def test_disconnect_idempotent(pg_client, caplog):
    """Test disconnect is idempotent."""
    caplog.set_level(logging.INFO)
    await pg_client.disconnect()  # Not connected
    assert "PostgreSQL client already disconnected" in caplog.text

    caplog.clear()
    await pg_client.connect()
    await pg_client.disconnect()
    await pg_client.disconnect()  # Second call
    assert "PostgreSQL client already disconnected" in caplog.text
    # Only one successful disconnect should be recorded
    assert (
        get_metric_value(
            DB_CALLS_TOTAL,
            db_type="postgresql",
            operation="disconnect",
            table="n/a",
            status="success",
        )
        == 1
    )


@pytest.mark.asyncio
async def test_ensure_table_exists(pg_client):
    """Test ensuring table exists."""
    await pg_client.connect()
    # The method doesn't directly call execute_query, so no metrics are recorded
    await pg_client._ensure_table_exists("feedback")
    # Since AUTO_MIGRATE is not set to "1", this is essentially a no-op


@pytest.mark.asyncio
async def test_save_success(pg_client, mocker: MockerFixture):
    """Test successful save (UPSERT)."""
    await pg_client.connect()

    # Mock fetch to return the saved ID
    mock_conn = pg_client._pool.acquire.return_value.__aenter__.return_value
    mock_conn.fetch = mocker.AsyncMock(
        return_value=[{"id": SAMPLE_FEEDBACK_DATA["id"]}]
    )

    saved_id = await pg_client.save("feedback", SAMPLE_FEEDBACK_DATA)
    assert saved_id == SAMPLE_FEEDBACK_DATA["id"]
    assert (
        get_metric_value(
            DB_CALLS_TOTAL,
            db_type="postgresql",
            operation="save",
            table="feedback",
            status="success",
        )
        == 1
    )


@pytest.mark.asyncio
async def test_save_many_success(pg_client, mocker: MockerFixture):
    """Test successful batch save."""
    await pg_client.connect()

    batch_data = [SAMPLE_FEEDBACK_DATA.copy() for _ in range(3)]
    batch_ids = []
    for i, data in enumerate(batch_data):
        data["id"] = str(uuid.uuid4())
        batch_ids.append(data["id"])

    # Mock the transaction and fetch to return the batch IDs
    mock_conn = pg_client._pool.acquire.return_value.__aenter__.return_value
    mock_conn.fetch = mocker.AsyncMock(return_value=[{"id": bid} for bid in batch_ids])

    # Mock transaction context manager
    class MockTransaction:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    mock_conn.transaction.return_value = MockTransaction()

    saved_ids = await pg_client.save_many("feedback", batch_data)
    assert len(saved_ids) == 3
    assert all(isinstance(sid, str) for sid in saved_ids)
    assert (
        get_metric_value(
            DB_CALLS_TOTAL,
            db_type="postgresql",
            operation="save_many",
            table="feedback",
            status="success",
        )
        == 1
    )


@pytest.mark.asyncio
async def test_load_success(pg_client, mocker: MockerFixture):
    """Test successful load of a record."""
    await pg_client.connect()

    # Mock fetch to return the sample data
    mock_conn = pg_client._pool.acquire.return_value.__aenter__.return_value
    mock_conn.fetch = mocker.AsyncMock(return_value=[SAMPLE_FEEDBACK_DATA])

    record = await pg_client.load("feedback", SAMPLE_FEEDBACK_DATA["id"])
    assert record is not None
    assert record["id"] == SAMPLE_FEEDBACK_DATA["id"]
    assert (
        get_metric_value(
            DB_CALLS_TOTAL,
            db_type="postgresql",
            operation="load",
            table="feedback",
            status="success",
        )
        == 1
    )


@pytest.mark.asyncio
async def test_load_all_success(pg_client, mocker: MockerFixture):
    """Test successful load_all with filters."""
    await pg_client.connect()

    # Mock fetch to return a list of sample data
    mock_conn = pg_client._pool.acquire.return_value.__aenter__.return_value
    mock_conn.fetch = mocker.AsyncMock(return_value=[SAMPLE_FEEDBACK_DATA])

    records = await pg_client.load_all("feedback", filters={"type": "user_feedback"})
    assert len(records) >= 1
    assert records[0]["type"] == "user_feedback"
    assert (
        get_metric_value(
            DB_CALLS_TOTAL,
            db_type="postgresql",
            operation="load_all",
            table="feedback",
            status="success",
        )
        == 1
    )


@pytest.mark.asyncio
async def test_update_success(pg_client, mocker: MockerFixture):
    """Test successful update of a record."""
    await pg_client.connect()

    # Mock fetch to return the updated ID
    mock_conn = pg_client._pool.acquire.return_value.__aenter__.return_value
    mock_conn.fetch = mocker.AsyncMock(
        return_value=[{"id": SAMPLE_FEEDBACK_DATA["id"]}]
    )

    updated = await pg_client.update(
        "feedback",
        {"id": SAMPLE_FEEDBACK_DATA["id"]},
        {"data": {"new_comment": "Updated"}},
    )
    assert updated
    assert (
        get_metric_value(
            DB_CALLS_TOTAL,
            db_type="postgresql",
            operation="update",
            table="feedback",
            status="success",
        )
        == 1
    )


@pytest.mark.asyncio
async def test_delete_success(pg_client, mocker: MockerFixture):
    """Test successful deletion of a record."""
    await pg_client.connect()

    # Mock fetch to return the deleted ID
    mock_conn = pg_client._pool.acquire.return_value.__aenter__.return_value
    mock_conn.fetch = mocker.AsyncMock(
        return_value=[{"id": SAMPLE_FEEDBACK_DATA["id"]}]
    )

    deleted = await pg_client.delete("feedback", SAMPLE_FEEDBACK_DATA["id"])
    assert deleted
    assert (
        get_metric_value(
            DB_CALLS_TOTAL,
            db_type="postgresql",
            operation="delete",
            table="feedback",
            status="success",
        )
        == 1
    )


@pytest.mark.asyncio
async def test_retry_on_connect_failure(mocker: MockerFixture):
    """Test retry mechanism on connection failure."""
    from asyncpg import exceptions as asyncpg_exceptions
    from asyncpg.pool import Pool

    # Create a mock pool for successful connection
    mock_pool = mocker.MagicMock(spec=Pool)
    mock_pool.close = mocker.AsyncMock()
    mock_pool.get_size.return_value = 1
    mock_pool.is_closed.return_value = False

    # Mock connection for pool verification
    mock_conn = mocker.AsyncMock()
    mock_conn.fetchval = mocker.AsyncMock(return_value=1)

    class MockAcquireContext:
        async def __aenter__(self):
            return mock_conn

        async def __aexit__(self, *args):
            return None

    mock_pool.acquire.return_value = MockAcquireContext()

    # Fail twice, then succeed
    mocker.patch(
        "postgres_client.asyncpg.create_pool",
        side_effect=[
            asyncpg_exceptions.PostgresError("Failed"),
            asyncpg_exceptions.PostgresError("Failed"),
            mock_pool,
        ],
    )

    client = PostgresClient()
    client.max_retries = 3

    await client.connect()
    assert client._pool is not None
    assert (
        get_metric_value(
            DB_CALLS_ERRORS,
            db_type="postgresql",
            operation="connect",
            table="n/a",
            error_type="PostgresError",
        )
        == 2
    )
    assert (
        get_metric_value(
            DB_CALLS_TOTAL,
            db_type="postgresql",
            operation="connect",
            table="n/a",
            status="success",
        )
        == 1
    )


@pytest.mark.asyncio
async def test_concurrent_save(pg_client, mocker: MockerFixture):
    """Test concurrent save operations."""
    await pg_client.connect()

    # Mock fetch to return different IDs for each save
    saved_ids = [str(uuid.uuid4()) for _ in range(5)]
    mock_conn = pg_client._pool.acquire.return_value.__aenter__.return_value
    mock_conn.fetch = mocker.AsyncMock(side_effect=[[{"id": sid}] for sid in saved_ids])

    async def save_task(table: str, data: Dict):
        return await pg_client.save(table, data)

    tasks = []
    for i in range(5):
        data = SAMPLE_FEEDBACK_DATA.copy()
        data["id"] = saved_ids[i]
        tasks.append(save_task("feedback", data))

    results = await asyncio.gather(*tasks)
    assert len(results) == 5
    assert all(isinstance(sid, str) for sid in results)
    assert (
        get_metric_value(
            DB_CALLS_TOTAL,
            db_type="postgresql",
            operation="save",
            table="feedback",
            status="success",
        )
        == 5
    )


@pytest.mark.asyncio
async def test_jsonb_handling(pg_client, mocker: MockerFixture):
    """Test JSONB field handling and parsing."""
    await pg_client.connect()

    data_with_jsonb = {
        "id": str(uuid.uuid4()),
        "type": "test_jsonb",
        "data": {"nested": {"key": "value"}},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # Mock fetch to return the saved data
    mock_conn = pg_client._pool.acquire.return_value.__aenter__.return_value
    mock_conn.fetch = mocker.AsyncMock(
        side_effect=[
            [{"id": data_with_jsonb["id"]}],  # For save
            [data_with_jsonb],  # For load
        ]
    )

    saved_id = await pg_client.save("feedback", data_with_jsonb)
    record = await pg_client.load("feedback", saved_id)

    assert isinstance(record["data"], dict)
    assert record["data"]["nested"]["key"] == "value"
    assert (
        get_metric_value(
            DB_CALLS_TOTAL,
            db_type="postgresql",
            operation="save",
            table="feedback",
            status="success",
        )
        == 1
    )
    assert (
        get_metric_value(
            DB_CALLS_TOTAL,
            db_type="postgresql",
            operation="load",
            table="feedback",
            status="success",
        )
        == 1
    )


@pytest.mark.asyncio
async def test_ssl_mode(mocker: MockerFixture):
    """Test SSL mode configuration."""
    from asyncpg.pool import Pool

    mocker.patch.dict(os.environ, {"PG_SSL_MODE": "require"})

    # Create mock pool
    mock_pool = mocker.MagicMock(spec=Pool)
    mock_conn = mocker.AsyncMock()
    mock_conn.fetchval = mocker.AsyncMock(return_value=1)

    class MockAcquireContext:
        async def __aenter__(self):
            return mock_conn

        async def __aexit__(self, *args):
            return None

    mock_pool.acquire.return_value = MockAcquireContext()
    mock_pool.close = mocker.AsyncMock()
    mock_pool.get_size.return_value = 1
    mock_pool.is_closed.return_value = False

    # Capture the create_pool call
    create_pool_mock = mocker.patch(
        "postgres_client.asyncpg.create_pool", mocker.AsyncMock(return_value=mock_pool)
    )

    # Create a new client instance
    client_with_ssl = PostgresClient()
    await client_with_ssl.connect()

    assert client_with_ssl._pool is not None
    # Verify create_pool was called with ssl parameter
    create_pool_mock.assert_called_once()
    call_kwargs = create_pool_mock.call_args[1]
    assert "ssl" in call_kwargs  # SSL context should be configured


@pytest.mark.asyncio
async def test_context_manager(pg_client, mocker: MockerFixture):
    """Test async context manager for connect/disconnect."""
    # Mock fetch to return saved ID
    mock_conn = pg_client._pool.acquire.return_value.__aenter__.return_value
    mock_conn.fetch = mocker.AsyncMock(
        return_value=[{"id": SAMPLE_FEEDBACK_DATA["id"]}]
    )

    async with pg_client:
        assert pg_client._pool is not None
        assert not pg_client._is_closed
        saved_id = await pg_client.save("feedback", SAMPLE_FEEDBACK_DATA)
        assert isinstance(saved_id, str)

    assert pg_client._pool is None
    assert pg_client._is_closed
    assert (
        get_metric_value(
            DB_CALLS_TOTAL,
            db_type="postgresql",
            operation="connect",
            table="n/a",
            status="success",
        )
        == 1
    )
    assert (
        get_metric_value(
            DB_CALLS_TOTAL,
            db_type="postgresql",
            operation="disconnect",
            table="n/a",
            status="success",
        )
        == 1
    )


@pytest.mark.asyncio
async def test_ping_success(pg_client):
    """Test successful ping."""
    await pg_client.connect()
    result = await pg_client.ping()
    assert result is True


@pytest.mark.asyncio
async def test_ping_no_pool(pg_client):
    """Test ping when pool is not initialized."""
    result = await pg_client.ping()
    assert result is False


@pytest.mark.asyncio
async def test_agent_knowledge_operations(pg_client, mocker: MockerFixture):
    """Test operations on agent_knowledge table with composite primary key."""
    await pg_client.connect()

    knowledge_data = SAMPLE_AGENT_KNOWLEDGE_DATA.copy()

    # Mock for save
    mock_conn = pg_client._pool.acquire.return_value.__aenter__.return_value
    mock_conn.fetch = mocker.AsyncMock(
        return_value=[
            {"domain": knowledge_data["domain"], "key": knowledge_data["key"]}
        ]
    )

    # Test save
    saved_key = await pg_client.save("agent_knowledge", knowledge_data)
    assert saved_key == f"{knowledge_data['domain']}:{knowledge_data['key']}"

    # Mock for load
    mock_conn.fetch = mocker.AsyncMock(return_value=[knowledge_data])

    # Test load with composite key
    loaded = await pg_client.load(
        "agent_knowledge",
        f"{knowledge_data['domain']}:{knowledge_data['key']}",
        query_field="domain_key",
    )
    assert loaded is not None
    assert loaded["domain"] == knowledge_data["domain"]
    assert loaded["key"] == knowledge_data["key"]

    # Mock for delete
    mock_conn.fetch = mocker.AsyncMock(
        return_value=[
            {"domain": knowledge_data["domain"], "key": knowledge_data["key"]}
        ]
    )

    # Test delete with composite key
    deleted = await pg_client.delete(
        "agent_knowledge",
        f"{knowledge_data['domain']}:{knowledge_data['key']}",
        query_field="domain_key",
    )
    assert deleted is True


@pytest.mark.asyncio
async def test_update_jsonb_operations(pg_client, mocker: MockerFixture):
    """Test JSONB update operations (merge, unset, replace)."""
    await pg_client.connect()

    # Mock fetch to return success
    mock_conn = pg_client._pool.acquire.return_value.__aenter__.return_value
    mock_conn.fetch = mocker.AsyncMock(return_value=[{"id": "test_id"}])

    # Test JSONB merge (default behavior)
    updated = await pg_client.update(
        "feedback", {"id": "test_id"}, {"data": {"new_field": "new_value"}}
    )
    assert updated is True

    # Test JSONB unset
    updated = await pg_client.update(
        "feedback", {"id": "test_id"}, {"data": {"$unset": ["field_to_remove"]}}
    )
    assert updated is True

    # Test JSONB replace
    updated = await pg_client.update(
        "feedback",
        {"id": "test_id"},
        {"data": {"$replace": {"completely": "new_data"}}},
    )
    assert updated is True

    # Test setting JSONB to NULL
    updated = await pg_client.update("feedback", {"id": "test_id"}, {"data": None})
    assert updated is True
