"""
Standalone test file for Neo4jKnowledgeGraph that avoids import chain issues.
This test file imports the knowledge_graph_db module directly without going through
the main arbiter module to avoid dependency issues.
"""

import asyncio
import json
import logging
import os
import sys
import pytest
import pytest_asyncio
from pytest_mock import MockerFixture

# Add the parent directories to the path to allow direct imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Import OpenTelemetry directly
from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

# Now import the knowledge_graph_db module directly
import knowledge_graph_db
from knowledge_graph_db import (
    Neo4jKnowledgeGraph,
    ConnectionError,
    SchemaValidationError,
    KG_OPS_TOTAL,
    KG_CONNECTIONS,
    KG_ERRORS,
    ImmutableAuditLogger,
)

# Configure logging for tests
logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Setup for OpenTelemetry tracing with in-memory exporter for testing
in_memory_exporter = InMemorySpanExporter()

# Get tracer directly
tracer = trace.get_tracer(__name__)

# Sample environment variables for tests
SAMPLE_ENV = {
    "NEO4J_URL": "bolt://localhost:7687",
    "NEO4J_USER": "neo4j",
    "NEO4J_PASSWORD": "test_password",
    "LOG_LEVEL": "DEBUG",
    "SFE_OTEL_EXPORTER_TYPE": "console",
    "ENV": "test",
}


@pytest_asyncio.fixture(autouse=True)
async def setup_env(mocker: MockerFixture):
    """Set up environment variables and clean up after tests."""
    for key, value in SAMPLE_ENV.items():
        mocker.patch.dict(os.environ, {key: value})
    yield
    # Clear environment variables after tests
    for key in SAMPLE_ENV:
        os.environ.pop(key, None)


@pytest_asyncio.fixture
async def kg_client(mocker: MockerFixture):
    """Fixture for Neo4jKnowledgeGraph with mocked Neo4j dependencies."""
    try:
        from neo4j.async_driver import AsyncGraphDatabase
        from neo4j.exceptions import ServiceUnavailable, SessionExpired
        from neo4j import (
            AsyncManagedTransaction,
            AsyncSession,
            exceptions as neo4j_exceptions,
        )

        # Mock Neo4j driver for controlled testing
        mock_driver = mocker.MagicMock()
        mock_session = mocker.AsyncMock(spec=AsyncSession)
        mock_tx = mocker.AsyncMock(spec=AsyncManagedTransaction)

        # Configure driver methods
        mock_driver.verify_connectivity = mocker.AsyncMock(return_value=True)
        mock_driver.close = mocker.AsyncMock()

        # Make session() return an async context manager
        mock_session_context = mocker.AsyncMock()
        mock_session_context.__aenter__ = mocker.AsyncMock(return_value=mock_session)
        mock_session_context.__aexit__ = mocker.AsyncMock(return_value=None)
        mock_driver.session = mocker.MagicMock(return_value=mock_session_context)

        # Configure transaction execution results
        async def mock_execute_write(func, *args, **kwargs):
            result = await func(mock_tx, *args, **kwargs)
            return result

        async def mock_execute_read(func, *args, **kwargs):
            result = await func(mock_tx, *args, **kwargs)
            return result

        mock_session.execute_write = mock_execute_write
        mock_session.execute_read = mock_execute_read

        # Configure mock_tx.run to return proper results
        mock_result = mocker.AsyncMock()
        mock_result.single = mocker.AsyncMock(
            return_value={"nodeId": "mock_node_id", "relId": "mock_rel_id"}
        )
        mock_result.data = mocker.AsyncMock(return_value=[{"node_count": 1}])
        mock_tx.run = mocker.AsyncMock(return_value=mock_result)

        # Patch AsyncGraphDatabase.driver in the knowledge_graph_db module
        mocker.patch.object(knowledge_graph_db, "AsyncGraphDatabase")
        knowledge_graph_db.AsyncGraphDatabase.driver = mocker.MagicMock(
            return_value=mock_driver
        )

        # Create client with short retry settings for faster tests
        client = Neo4jKnowledgeGraph()
        client.max_retries = 2
        client.retry_delay_sec = 0.1

        yield client
    except ImportError:
        pytest.skip("Neo4j library not installed; skipping Neo4jKnowledgeGraph tests.")


@pytest_asyncio.fixture(autouse=True)
async def clear_metrics_and_traces():
    """Clear Prometheus metrics and OpenTelemetry traces before each test."""
    # Clear traces
    in_memory_exporter.clear()
    yield


def get_metric_value(metric, **labels):
    """Helper to get metric value with labels."""
    try:
        return metric.labels(**labels)._value.get()
    except:
        return 0


@pytest.mark.asyncio
async def test_initialization_success(kg_client):
    """Test successful initialization with valid config."""
    assert kg_client.url == "bolt://localhost:7687"
    assert kg_client.user == "neo4j"
    assert kg_client.password == "test_password"
    assert kg_client.max_retries == 2
    assert kg_client.statement_timeout == 60
    assert kg_client.connection_pool_size == 50
    assert kg_client._driver is None
    assert not kg_client._connected


@pytest.mark.asyncio
async def test_initialization_no_password_error(mocker: MockerFixture):
    """Test initialization fails without a password."""
    mocker.patch.dict(os.environ, {"NEO4J_PASSWORD": ""}, clear=False)
    with pytest.raises(ConnectionError, match="A secure password must be provided"):
        Neo4jKnowledgeGraph()


@pytest.mark.asyncio
async def test_initialization_default_password_error(mocker: MockerFixture):
    """Test initialization fails with default password."""
    mocker.patch.dict(os.environ, {"NEO4J_PASSWORD": "password"}, clear=False)
    with pytest.raises(ConnectionError, match="A secure password must be provided"):
        Neo4jKnowledgeGraph()


@pytest.mark.asyncio
async def test_connect_success(kg_client):
    """Test successful connection to Neo4j."""
    await kg_client.connect()
    assert kg_client._connected
    assert kg_client._driver is not None
    assert get_metric_value(KG_OPS_TOTAL, operation="connect", status="success") == 1
    assert get_metric_value(KG_CONNECTIONS) == 1
    # Verify traces
    spans = in_memory_exporter.get_finished_spans()
    assert len(spans) >= 1
    connect_span = next((span for span in spans if span.name == "neo4j_connect"), None)
    assert connect_span is not None
    assert connect_span.status.is_ok


@pytest.mark.asyncio
async def test_connect_idempotent(kg_client):
    """Test connect is idempotent - reconnects if already connected."""
    await kg_client.connect()
    await kg_client.connect()
    # The implementation closes and recreates the driver
    assert kg_client._driver is not None
    assert kg_client._connected
    # Both connects should succeed
    assert get_metric_value(KG_OPS_TOTAL, operation="connect", status="success") == 2


@pytest.mark.asyncio
async def test_connect_failure(mocker: MockerFixture):
    """Test connection failure handling."""
    from neo4j import exceptions as neo4j_exceptions

    mocker.patch.object(knowledge_graph_db, "AsyncGraphDatabase")
    knowledge_graph_db.AsyncGraphDatabase.driver = mocker.MagicMock(
        side_effect=neo4j_exceptions.ServiceUnavailable("Connection failed")
    )

    client = Neo4jKnowledgeGraph()
    with pytest.raises(ConnectionError, match="Failed to establish connection"):
        await client.connect()
    assert (
        get_metric_value(
            KG_ERRORS, operation="connect", error_type="ServiceUnavailable"
        )
        >= 1
    )
    assert get_metric_value(KG_OPS_TOTAL, operation="connect", status="failure") >= 1
    spans = in_memory_exporter.get_finished_spans()
    assert any(span.name == "neo4j_connect" and not span.status.is_ok for span in spans)


@pytest.mark.asyncio
async def test_disconnect_success(kg_client):
    """Test successful disconnection."""
    await kg_client.connect()
    await kg_client.disconnect()
    assert not kg_client._connected
    assert kg_client._driver is None
    assert get_metric_value(KG_OPS_TOTAL, operation="disconnect", status="success") == 1
    assert get_metric_value(KG_CONNECTIONS) == 0
    spans = in_memory_exporter.get_finished_spans()
    assert any(span.name == "neo4j_disconnect" and span.status.is_ok for span in spans)


@pytest.mark.asyncio
async def test_disconnect_idempotent(kg_client, caplog):
    """Test disconnect is idempotent."""
    caplog.set_level(logging.WARNING)
    await kg_client.disconnect()  # Not connected
    assert "Attempted to disconnect a non-connected client" in caplog.text
    assert get_metric_value(KG_OPS_TOTAL, operation="disconnect", status="skipped") == 1

    caplog.clear()
    await kg_client.connect()
    await kg_client.disconnect()
    await kg_client.disconnect()  # Second disconnect
    assert "Attempted to disconnect a non-connected client" in caplog.text
    assert get_metric_value(KG_OPS_TOTAL, operation="disconnect", status="success") == 1
    assert get_metric_value(KG_OPS_TOTAL, operation="disconnect", status="skipped") == 2


@pytest.mark.asyncio
async def test_health_check_success(kg_client):
    """Test successful health check."""
    await kg_client.connect()
    is_healthy = await kg_client.health_check()
    assert is_healthy
    assert (
        get_metric_value(KG_OPS_TOTAL, operation="health_check", status="success") == 1
    )
    spans = in_memory_exporter.get_finished_spans()
    assert any(
        span.name == "neo4j_health_check" and span.status.is_ok for span in spans
    )


@pytest.mark.asyncio
async def test_health_check_not_connected(kg_client):
    """Test health check when not connected."""
    is_healthy = await kg_client.health_check()
    assert not is_healthy
    assert (
        get_metric_value(KG_OPS_TOTAL, operation="health_check", status="failure") == 1
    )


@pytest.mark.asyncio
async def test_health_check_failure(kg_client, mocker: MockerFixture):
    """Test health check failure."""
    await kg_client.connect()
    from neo4j import exceptions as neo4j_exceptions

    mocker.patch.object(
        kg_client._driver,
        "verify_connectivity",
        side_effect=neo4j_exceptions.ServiceUnavailable("Unavailable"),
    )
    is_healthy = await kg_client.health_check()
    assert not is_healthy
    assert (
        get_metric_value(
            KG_ERRORS, operation="health_check", error_type="ServiceUnavailable"
        )
        >= 1
    )
    assert (
        get_metric_value(KG_OPS_TOTAL, operation="health_check", status="failure") >= 1
    )


@pytest.mark.asyncio
async def test_add_node_success(kg_client):
    """Test successful node addition."""
    await kg_client.connect()
    node_id = await kg_client.add_node("TestLabel", {"prop": "value"})
    assert isinstance(node_id, str)
    assert node_id == "mock_node_id"
    assert get_metric_value(KG_OPS_TOTAL, operation="add_node", status="success") == 1
    spans = in_memory_exporter.get_finished_spans()
    add_span = next((span for span in spans if span.name == "neo4j_add_node"), None)
    assert add_span is not None
    assert add_span.attributes["db.node_id"] == node_id
    assert add_span.status.is_ok


@pytest.mark.asyncio
async def test_add_node_validation_failure(kg_client):
    """Test node addition with validation failure."""
    await kg_client.connect()
    with pytest.raises(SchemaValidationError):
        await kg_client.add_node("TestLabel", [])  # Invalid properties type
    assert (
        get_metric_value(KG_ERRORS, operation="add_node", error_type="ValidationError")
        == 1
    )
    assert get_metric_value(KG_OPS_TOTAL, operation="add_node", status="failure") == 1


@pytest.mark.asyncio
async def test_add_node_hashes_pii(kg_client, mocker: MockerFixture):
    """Test that PII fields are hashed in node properties."""
    await kg_client.connect()

    # Spy on the audit logger to capture what was logged
    spy_log_event = mocker.spy(kg_client.audit_logger, "log_event")

    properties = {"user_id": "12345", "email": "test@example.com", "name": "John"}
    await kg_client.add_node("User", properties)

    # Check that the audit log contains hashed values
    spy_log_event.assert_called()
    call_args = spy_log_event.call_args_list[-1]
    logged_properties = call_args[0][1]["properties"]

    # user_id and email should be hashed
    assert logged_properties["user_id"] != "12345"
    assert logged_properties["email"] != "test@example.com"
    assert len(logged_properties["user_id"]) == 64  # SHA256 hex length
    assert len(logged_properties["email"]) == 64


@pytest.mark.asyncio
async def test_add_relationship_success(kg_client):
    """Test successful relationship addition."""
    await kg_client.connect()
    from_id = "node1"
    to_id = "node2"
    rel_id = await kg_client.add_relationship(
        from_id, to_id, "TEST_REL", {"prop": "value"}
    )
    assert isinstance(rel_id, str)
    assert rel_id == "mock_rel_id"
    assert (
        get_metric_value(KG_OPS_TOTAL, operation="add_relationship", status="success")
        == 1
    )
    spans = in_memory_exporter.get_finished_spans()
    rel_span = next(
        (span for span in spans if span.name == "neo4j_add_relationship"), None
    )
    assert rel_span is not None
    assert rel_span.attributes["db.relationship_id"] == rel_id
    assert rel_span.status.is_ok


@pytest.mark.asyncio
async def test_find_related_facts_success(kg_client, mocker: MockerFixture):
    """Test successful related facts query."""
    await kg_client.connect()

    # Update mock to return proper data
    mock_data = [{"n": {"name": "test"}, "r": None, "m": None}]
    mocker.patch.object(kg_client, "_execute_read", return_value=mock_data)

    facts = await kg_client.find_related_facts("TestDomain", "key", "value")
    assert isinstance(facts, list)
    assert len(facts) == 1
    assert (
        get_metric_value(KG_OPS_TOTAL, operation="find_related_facts", status="success")
        == 1
    )


@pytest.mark.asyncio
async def test_check_consistency_success(kg_client, mocker: MockerFixture):
    """Test successful consistency check."""
    await kg_client.connect()

    # Mock returns 1 node found
    mocker.patch.object(kg_client, "_execute_read", return_value=[{"node_count": 1}])

    status = await kg_client.check_consistency("TestDomain", "key", "value")
    assert status is None  # No inconsistency
    assert (
        get_metric_value(KG_OPS_TOTAL, operation="check_consistency", status="success")
        == 1
    )


@pytest.mark.asyncio
async def test_check_consistency_no_node(kg_client, mocker: MockerFixture):
    """Test consistency check when no node found."""
    await kg_client.connect()

    # Mock returns 0 nodes found
    mocker.patch.object(kg_client, "_execute_read", return_value=[{"node_count": 0}])

    status = await kg_client.check_consistency("NonExistent", "key", "value")
    assert "No nodes found" in status
    assert (
        get_metric_value(KG_OPS_TOTAL, operation="check_consistency", status="success")
        == 1
    )


@pytest.mark.asyncio
async def test_export_graph_success(kg_client, tmp_path, mocker: MockerFixture):
    """Test successful graph export."""
    await kg_client.connect()
    export_file = tmp_path / "export_graph"

    # Mock the queries to return counts and data
    mock_node = {"eid": "node1", "labels": ["Test"], "props": {"prop": "value"}}
    mock_rel = {
        "rid": "rel1",
        "type": "TEST_REL",
        "start_eid": "node1",
        "end_eid": "node2",
        "props": {},
    }

    call_count = 0

    def side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:  # First call for node count
            return [{"count": 1}]
        elif call_count == 2:  # Second call for relationship count
            return [{"count": 1}]
        elif call_count == 3:  # Third call for nodes export
            return [mock_node]
        elif call_count == 4:  # Fourth call for relationships export
            return [mock_rel]
        return []

    mocker.patch.object(kg_client, "_execute_read", side_effect=side_effect)

    await kg_client.export_graph(str(export_file))
    assert os.path.exists(f"{export_file}.nodes.jsonl.gz")
    assert os.path.exists(f"{export_file}.rels.jsonl.gz")
    assert (
        get_metric_value(KG_OPS_TOTAL, operation="export_graph", status="success") == 1
    )


@pytest.mark.asyncio
async def test_import_graph_success(kg_client, tmp_path, mocker: MockerFixture):
    """Test successful graph import."""
    await kg_client.connect()
    import_file = tmp_path / "import_graph"
    import gzip

    # Create dummy files for import
    with gzip.open(f"{import_file}.nodes.jsonl.gz", "wt") as f:
        f.write(
            json.dumps({"eid": "node1", "labels": ["Test"], "props": {"prop": "value"}})
            + "\n"
        )
    with gzip.open(f"{import_file}.rels.jsonl.gz", "wt") as f:
        f.write(
            json.dumps(
                {
                    "rid": "rel1",
                    "type": "TEST_REL",
                    "start_eid": "node1",
                    "end_eid": "node2",
                    "props": {},
                }
            )
            + "\n"
        )

    # Mock execute_write to succeed
    mocker.patch.object(kg_client, "_execute_write", return_value=None)

    await kg_client.import_graph(str(import_file))
    assert (
        get_metric_value(KG_OPS_TOTAL, operation="import_graph", status="success") == 1
    )


@pytest.mark.asyncio
async def test_retry_on_connect_failure(mocker: MockerFixture):
    """Test retry mechanism on connection failure."""
    from neo4j import exceptions as neo4j_exceptions

    # Create mock driver that succeeds on third attempt
    mock_driver = mocker.MagicMock()
    mock_driver.verify_connectivity = mocker.AsyncMock(return_value=True)
    mock_driver.close = mocker.AsyncMock()

    call_count = [0]

    def side_effect(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] < 3:
            raise neo4j_exceptions.ServiceUnavailable("Unavailable")
        return mock_driver

    mocker.patch.object(knowledge_graph_db, "AsyncGraphDatabase")
    knowledge_graph_db.AsyncGraphDatabase.driver = mocker.MagicMock(
        side_effect=side_effect
    )

    client = Neo4jKnowledgeGraph()
    client.max_retries = 3
    client.retry_delay_sec = 0.01

    await client.connect()
    assert client._connected
    assert (
        get_metric_value(
            KG_ERRORS, operation="connect", error_type="ServiceUnavailable"
        )
        == 2
    )
    assert get_metric_value(KG_OPS_TOTAL, operation="connect", status="success") == 1


@pytest.mark.asyncio
async def test_concurrent_add_node(kg_client):
    """Test concurrent node addition."""
    await kg_client.connect()

    async def add_node_task(i: int):
        return await kg_client.add_node(f"TestLabel_{i}", {"prop": f"value_{i}"})

    tasks = [add_node_task(i) for i in range(5)]
    node_ids = await asyncio.gather(*tasks)
    assert len(node_ids) == 5
    assert all(isinstance(nid, str) for nid in node_ids)
    assert get_metric_value(KG_OPS_TOTAL, operation="add_node", status="success") == 5


@pytest.mark.asyncio
async def test_audit_logging(kg_client, tmp_path):
    """Test audit logging during operations."""
    audit_file = tmp_path / "test_audit_log.jsonl"
    audit_logger = ImmutableAuditLogger(file_path=str(audit_file))

    client = Neo4jKnowledgeGraph(audit_logger=audit_logger)
    await client.connect()
    await client.add_node("AuditTest", {"prop": "value"})
    await client.disconnect()

    # Wait for audit logger to flush
    await audit_logger.close()

    # Check audit log file
    assert audit_file.exists()
    with open(audit_file, "r") as f:
        lines = f.readlines()
        assert len(lines) >= 3  # connect, add_node, disconnect

        events = [json.loads(line) for line in lines]
        event_types = [e["event"] for e in events]
        assert "connect" in event_types
        assert "add_node" in event_types
        assert "disconnect" in event_types


@pytest.mark.asyncio
async def test_context_manager(kg_client):
    """Test async context manager for connect/disconnect."""
    async with kg_client:
        assert kg_client._connected
        node_id = await kg_client.add_node("ContextTest", {"prop": "value"})
        assert isinstance(node_id, str)
    assert not kg_client._connected
    assert get_metric_value(KG_OPS_TOTAL, operation="connect", status="success") == 1
    assert get_metric_value(KG_OPS_TOTAL, operation="disconnect", status="success") == 1


@pytest.mark.asyncio
async def test_no_password_leak(kg_client, caplog):
    """Test that password is not logged in plain text."""
    caplog.set_level(logging.DEBUG)
    await kg_client.connect()
    # Check that the actual password is not in logs
    assert "test_password" not in caplog.text
    assert get_metric_value(KG_OPS_TOTAL, operation="connect", status="success") == 1


@pytest.mark.asyncio
async def test_execute_tx_sanitizes_sensitive_params(kg_client, caplog):
    """Test that sensitive parameters are sanitized in logs."""
    await kg_client.connect()
    caplog.set_level(logging.DEBUG)

    # Mock transaction
    from neo4j import AsyncManagedTransaction

    mock_tx = MockerFixture().AsyncMock(spec=AsyncManagedTransaction)
    mock_result = MockerFixture().AsyncMock()
    mock_result.single = MockerFixture().AsyncMock(return_value={"result": "value"})
    mock_tx.run = MockerFixture().AsyncMock(return_value=mock_result)

    # Execute with sensitive params
    params = {"user": "test", "password": "secret", "api_token": "token123"}
    await kg_client._execute_tx(mock_tx, "MATCH (n) RETURN n", params, write=False)

    # Check logs don't contain actual sensitive values
    assert "secret" not in caplog.text
    assert "token123" not in caplog.text
    assert "<REDACTED>" in caplog.text
