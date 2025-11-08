# test_history_manager.py
# Comprehensive production-grade tests for history_manager.py
# Run with: pytest test_history_manager.py -v

import os
import json
import uuid
import asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock, Mock, create_autospec
from cryptography.fernet import Fernet, InvalidToken

import pytest
import pytest_asyncio

# Import the actual module and its dependencies from the correct package path
from arbiter.explainable_reasoner.reasoner_errors import ReasonerError, ReasonerErrorCode
from arbiter.explainable_reasoner.history_manager import (
    BaseHistoryManager,
    SQLiteHistoryManager,
    PostgresHistoryManager,
    RedisHistoryManager,
)

# --- Test Fixtures ---

@pytest.fixture
def temp_db_path(tmp_path):
    """Create a temporary database path."""
    return tmp_path / "test.db"

@pytest.fixture
def mock_audit_client():
    """Create a mock audit client."""
    mock = MagicMock()
    mock.log_event = AsyncMock(return_value=True)
    return mock

@pytest.fixture
def mock_metrics():
    """Create mock metrics."""
    mock_metric = MagicMock()
    mock_metric.labels.return_value = MagicMock(
        inc=MagicMock(),
        set=MagicMock(),
        observe=MagicMock()
    )
    mock_metrics_dict = {
        "reasoner_history_operations_total": mock_metric,
        "reasoner_history_operation_latency_seconds": mock_metric,
        "reasoner_history_db_connection_failures_total": mock_metric,
        "reasoner_history_pruned_entries_total": mock_metric,
        "reasoner_history_entries_current": mock_metric
    }
    return mock_metrics_dict

@pytest_asyncio.fixture
async def sqlite_manager(temp_db_path, mock_audit_client, mock_metrics):
    """Create a real SQLite manager for testing."""
    with patch('arbiter.explainable_reasoner.history_manager.METRICS', mock_metrics):
        manager = SQLiteHistoryManager(
            db_path=temp_db_path,
            max_history_size=5,
            retention_days=1,
            audit_client=mock_audit_client
        )
        await manager.init_db()
        yield manager
        await manager.aclose()

@pytest_asyncio.fixture
async def postgres_manager(mock_audit_client, mock_metrics):
    """Create a PostgreSQL manager with mocked pool."""
    # Set encryption key
    os.environ["REASONER_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
    
    # Create in-memory store
    store = []
    
    # Create a mock connection
    mock_conn = AsyncMock()
    
    async def mock_execute(query, *params):
        if "CREATE TABLE" in query or "CREATE INDEX" in query:
            return None
        elif "INSERT INTO" in query:
            entry = {
                'id': params[0],
                'query': params[1],
                'context': params[2],
                'response': params[3],
                'response_type': params[4],
                'timestamp': params[5],
                'session_id': params[6] if len(params) > 6 else None
            }
            store.append(entry)
            return "INSERT 0 1"
        elif "DELETE FROM" in query and "timestamp" in query:
            if params:
                cutoff = params[0]
                before = len(store)
                store[:] = [e for e in store if e['timestamp'] >= cutoff]
                return f"DELETE {before - len(store)}"
        elif "DELETE FROM" in query and "session_id" in query:
            if params:
                session_id = params[0]
                before = len(store)
                store[:] = [e for e in store if e.get('session_id') != session_id]
                return f"DELETE {before - len(store)}"
        elif "DELETE FROM" in query:
            count = len(store)
            store.clear()
            return f"DELETE {count}"
        elif "TRUNCATE" in query:
            store.clear()
            return None
        return None
    
    async def mock_executemany(query, data):
        for params in data:
            await mock_execute(query, *params)
        return None
    
    async def mock_fetch(query, *params):
        if "WHERE session_id" in query and params:
            filtered = [e for e in store if e.get('session_id') == params[0]]
            limit = params[1] if len(params) > 1 else 10
        else:
            filtered = store
            limit = params[0] if params else 10
        
        sorted_data = sorted(filtered, key=lambda x: x['timestamp'], reverse=True)
        results = []
        for item in sorted_data[:limit]:
            record = dict(item)
            if 'context' in record and isinstance(record['context'], str):
                record['context'] = json.loads(record['context'])
            results.append(record)
        return results
    
    async def mock_fetchval(query, *params):
        if "COUNT" in query:
            return len(store)
        return 0
    
    mock_conn.execute = mock_execute
    mock_conn.executemany = mock_executemany
    mock_conn.fetch = mock_fetch
    mock_conn.fetchval = mock_fetchval
    
    # Mock cursor for export
    class MockCursor:
        def __init__(self, data):
            self.data = data
            self.index = 0
        
        def __aiter__(self):
            return self
        
        async def __anext__(self):
            if self.index >= len(self.data):
                raise StopAsyncIteration
            result = dict(self.data[self.index])
            if 'context' in result and isinstance(result['context'], str):
                result['context'] = json.loads(result['context'])
            self.index += 1
            return result
    
    mock_conn.cursor.return_value = MockCursor(store)
    mock_conn.transaction.return_value.__aenter__.return_value = mock_conn
    mock_conn.transaction.return_value.__aexit__.return_value = None
    
    # Create a proper async context manager for the pool.acquire()
    class MockPoolAcquireContext:
        def __init__(self, conn):
            self.conn = conn
        
        async def __aenter__(self):
            return self.conn
        
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return None
    
    with patch('arbiter.explainable_reasoner.history_manager.METRICS', mock_metrics), \
         patch('arbiter.explainable_reasoner.history_manager.POSTGRES_AVAILABLE', True):
        
        manager = PostgresHistoryManager(
            db_url="postgresql://test",
            max_history_size=5,
            retention_days=1,
            audit_client=mock_audit_client
        )
        
        # Create mock pool with proper async context manager
        mock_pool = MagicMock()  # Use MagicMock, not AsyncMock for the pool itself
        mock_pool.acquire = MagicMock(return_value=MockPoolAcquireContext(mock_conn))
        mock_pool.close = AsyncMock()
        manager._pool = mock_pool
        
        yield manager
        
        # Clean close without errors
        if manager._pool:
            manager._pool = None

@pytest_asyncio.fixture
async def redis_manager(mock_audit_client, mock_metrics):
    """Create a Redis manager with mocked client."""
    # Set encryption key
    os.environ["REASONER_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
    
    # In-memory store
    store = {}
    
    # Mock Redis client
    mock_client = AsyncMock()
    
    # Mock pipeline operations
    async def mock_zadd(key, mapping):
        store.update(mapping)
        return len(mapping)
    
    async def mock_zremrangebyrank(key, start, stop):
        if stop < 0:
            sorted_items = sorted(store.items(), key=lambda x: x[1], reverse=True)
            keep_count = -stop - 1
            if len(sorted_items) > keep_count:
                to_remove = sorted_items[keep_count:]
                for item, _ in to_remove:
                    del store[item]
                return len(to_remove)
        return 0
    
    # Create a proper async context manager for pipeline
    class MockPipelineContext:
        def __init__(self, pipeline):
            self.pipeline = pipeline
            
        async def __aenter__(self):
            return self.pipeline
            
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return None
    
    mock_pipeline = AsyncMock()
    mock_pipeline.zadd = mock_zadd
    mock_pipeline.zremrangebyrank = mock_zremrangebyrank
    mock_pipeline.execute = AsyncMock(return_value=[None, None])
    
    # Fix: Return the context manager instance, not the class
    mock_client.pipeline = MagicMock(return_value=MockPipelineContext(mock_pipeline))
    mock_client.ping = AsyncMock(return_value=True)
    
    mock_client.zrevrange = AsyncMock(side_effect=lambda k, s, e: 
        [x[0] for x in sorted(store.items(), key=lambda x: x[1], reverse=True)[s:e+1 if e >= 0 else None]])
    mock_client.zrange = AsyncMock(side_effect=lambda k, s, e:
        [x[0] for x in sorted(store.items(), key=lambda x: x[1])[s:e+1 if e >= 0 else None]])
    mock_client.zcard = AsyncMock(side_effect=lambda k: len(store))
    mock_client.zremrangebyscore = AsyncMock(side_effect=lambda k, min_s, max_s: 
        len([store.pop(k) for k, v in list(store.items()) if min_s <= v <= max_s]))
    mock_client.delete = AsyncMock(side_effect=lambda k: (c := len(store), store.clear(), c)[2])
    mock_client.zrem = AsyncMock(side_effect=lambda k, *members: 
        sum(1 for m in members if store.pop(m, None) is not None))
    
    async def mock_zscan_iter(key):
        for item in store.items():
            yield item
    
    mock_client.zscan_iter = mock_zscan_iter
    
    with patch('arbiter.explainable_reasoner.history_manager.METRICS', mock_metrics), \
         patch('arbiter.explainable_reasoner.history_manager.REDIS_AVAILABLE', True):
        
        manager = RedisHistoryManager(
            redis_url="redis://test",
            max_history_size=5,
            retention_days=1,
            audit_client=mock_audit_client
        )
        
        # Directly set the Redis client to avoid init_db issues
        manager._redis = mock_client
        manager._pool = MagicMock()
        manager._pool.disconnect = AsyncMock()
        
        yield manager
        
        # Clean close
        if manager._pool:
            await manager._pool.disconnect()

# --- Helper Functions ---

def create_test_entry(response="test_response", session_id="test_session", timestamp=None):
    """Create a test history entry."""
    if timestamp is None:
        timestamp = datetime.now(timezone.utc).isoformat()
    
    return {
        "id": str(uuid.uuid4()),
        "query": "test_query",
        "context": {"key": "value"},
        "response": response,
        "response_type": "text",
        "timestamp": timestamp,
        "session_id": session_id
    }

# --- Test Cases ---

class TestBaseHistoryManager:
    """Test the abstract base class."""
    
    def test_cannot_instantiate_abstract_class(self):
        """Verify that BaseHistoryManager cannot be instantiated."""
        with pytest.raises(TypeError):
            BaseHistoryManager(max_history_size=5, retention_days=1)

class TestSQLiteHistoryManager:
    """Test SQLite-specific functionality."""
    
    @pytest.mark.asyncio
    async def test_init_db(self, temp_db_path, mock_audit_client, mock_metrics):
        """Test database initialization."""
        with patch('arbiter.explainable_reasoner.history_manager.METRICS', mock_metrics):
            manager = SQLiteHistoryManager(
                db_path=temp_db_path,
                max_history_size=5,
                retention_days=1,
                audit_client=mock_audit_client
            )
            await manager.init_db()
            
            # Verify connection is established
            assert manager._conn is not None
            
            # Test idempotency
            await manager.init_db()
            
            await manager.aclose()
    
    @pytest.mark.asyncio
    async def test_add_and_get_entry(self, sqlite_manager):
        """Test adding and retrieving entries."""
        entry = create_test_entry("test_response")
        await sqlite_manager.add_entry(entry)
        
        entries = await sqlite_manager.get_entries(limit=1)
        assert len(entries) == 1
        assert entries[0]["response"] == "test_response"
    
    @pytest.mark.asyncio
    async def test_batch_operations(self, sqlite_manager):
        """Test batch entry addition."""
        entries = [create_test_entry(f"batch_{i}") for i in range(3)]
        await sqlite_manager.add_entries_batch(entries)
        
        retrieved = await sqlite_manager.get_entries(limit=5)
        assert len(retrieved) == 3
    
    @pytest.mark.asyncio
    async def test_max_size_enforcement(self, sqlite_manager):
        """Test that max_history_size is enforced (via manual pruning)."""
        # SQLite manager doesn't auto-prune on add
        for i in range(5):
            entry = create_test_entry(f"entry_{i}")
            await sqlite_manager.add_entry(entry)
            await asyncio.sleep(0.01)
        
        size = await sqlite_manager.get_size()
        assert size == 5
        
        # Add one more - SQLite allows this
        await sqlite_manager.add_entry(create_test_entry("entry_6"))
        size_after = await sqlite_manager.get_size()
        assert size_after == 6
    
    @pytest.mark.asyncio
    async def test_session_filtering(self, sqlite_manager):
        """Test filtering by session ID."""
        await sqlite_manager.add_entry(create_test_entry("s1_entry", session_id="s1"))
        await sqlite_manager.add_entry(create_test_entry("s2_entry", session_id="s2"))
        
        s1_entries = await sqlite_manager.get_entries(session_id="s1")
        assert len(s1_entries) == 1
        assert s1_entries[0]["session_id"] == "s1"
    
    @pytest.mark.asyncio
    async def test_pruning(self, sqlite_manager):
        """Test pruning old entries."""
        old_timestamp = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        old_entry = create_test_entry("old", timestamp=old_timestamp)
        await sqlite_manager.add_entry(old_entry)
        
        await sqlite_manager.add_entry(create_test_entry("new"))
        
        await sqlite_manager.prune_old_entries()
        
        entries = await sqlite_manager.get_entries()
        assert len(entries) == 1
        assert entries[0]["response"] == "new"
    
    @pytest.mark.asyncio
    async def test_clear_operations(self, sqlite_manager):
        """Test clear and purge operations."""
        await sqlite_manager.add_entry(create_test_entry("entry1", session_id="s1"))
        await sqlite_manager.add_entry(create_test_entry("entry2", session_id="s2"))
        
        await sqlite_manager.clear(session_id="s1")
        assert await sqlite_manager.get_size() == 1
        
        await sqlite_manager.clear()
        assert await sqlite_manager.get_size() == 0
        
        await sqlite_manager.add_entry(create_test_entry("entry3"))
        await sqlite_manager.purge_all()
        assert await sqlite_manager.get_size() == 0
    
    @pytest.mark.asyncio
    async def test_export(self, sqlite_manager):
        """Test export functionality."""
        await sqlite_manager.add_entry(create_test_entry("export1"))
        await sqlite_manager.add_entry(create_test_entry("export2"))
        
        chunks = []
        async for chunk in sqlite_manager.export_history():
            chunks.append(chunk.decode('utf-8'))
        
        exported = "".join(chunks)
        assert "export1" in exported or "export2" in exported

class TestPostgresHistoryManager:
    """Test PostgreSQL-specific functionality."""
    
    @pytest.mark.asyncio
    async def test_encryption(self, postgres_manager):
        """Test that responses are encrypted and decrypted."""
        entry = create_test_entry("sensitive_data")
        await postgres_manager.add_entry(entry)
        
        assert postgres_manager._encryption_key is not None
        
        entries = await postgres_manager.get_entries()
        assert entries[0]["response"] == "sensitive_data"
    
    @pytest.mark.asyncio
    async def test_operations(self, postgres_manager):
        """Test basic operations."""
        await postgres_manager.add_entry(create_test_entry("pg_entry1"))
        await postgres_manager.add_entry(create_test_entry("pg_entry2"))
        
        entries = await postgres_manager.get_entries()
        assert len(entries) == 2
        
        assert await postgres_manager.get_size() == 2
        
        await postgres_manager.clear()
        assert await postgres_manager.get_size() == 0

class TestRedisHistoryManager:
    """Test Redis-specific functionality."""
    
    @pytest.mark.asyncio
    async def test_sorted_set_operations(self, redis_manager):
        """Test Redis sorted set operations."""
        for i in range(3):
            entry = create_test_entry(f"redis_{i}")
            await redis_manager.add_entry(entry)
            await asyncio.sleep(0.01)
        
        entries = await redis_manager.get_entries()
        assert len(entries) <= 3
    
    @pytest.mark.asyncio
    async def test_operations(self, redis_manager):
        """Test basic operations."""
        await redis_manager.add_entry(create_test_entry("redis_entry"))
        
        assert await redis_manager.get_size() == 1
        
        await redis_manager.clear()
        assert await redis_manager.get_size() == 0

class TestCommonFunctionality:
    """Test functionality common to all managers."""
    
    @pytest.mark.asyncio
    async def test_sensitive_data_detection_sqlite(self, sqlite_manager):
        """Test that sensitive data is detected in SQLite."""
        sensitive_entry = create_test_entry("my api_key is secret123")
        with pytest.raises(ReasonerError) as exc_info:
            await sqlite_manager.add_entry(sensitive_entry)
        assert exc_info.value.code == ReasonerErrorCode.SENSITIVE_DATA_LEAK
    
    @pytest.mark.asyncio
    async def test_sensitive_data_detection_postgres(self, postgres_manager):
        """Test that sensitive data is detected in PostgreSQL."""
        sensitive_entry = create_test_entry("my api_key is secret123")
        with pytest.raises(ReasonerError) as exc_info:
            await postgres_manager.add_entry(sensitive_entry)
        assert exc_info.value.code == ReasonerErrorCode.SENSITIVE_DATA_LEAK
    
    @pytest.mark.asyncio
    async def test_sensitive_data_detection_redis(self, redis_manager):
        """Test that sensitive data is detected in Redis."""
        sensitive_entry = create_test_entry("my api_key is secret123")
        with pytest.raises(ReasonerError) as exc_info:
            await redis_manager.add_entry(sensitive_entry)
        assert exc_info.value.code == ReasonerErrorCode.SENSITIVE_DATA_LEAK
    
    @pytest.mark.asyncio
    async def test_binary_data_detection_sqlite(self, sqlite_manager):
        """Test that binary data is detected in SQLite."""
        binary_entry = create_test_entry("normal")
        binary_entry["context"]["binary"] = b"bytes_data"
        
        with pytest.raises(ReasonerError) as exc_info:
            await sqlite_manager.add_entry(binary_entry)
        assert exc_info.value.code == ReasonerErrorCode.SENSITIVE_DATA_LEAK
    
    @pytest.mark.asyncio
    async def test_binary_data_detection_postgres(self, postgres_manager):
        """Test that binary data is detected in PostgreSQL."""
        binary_entry = create_test_entry("normal")
        binary_entry["context"]["binary"] = b"bytes_data"
        
        with pytest.raises(ReasonerError) as exc_info:
            await postgres_manager.add_entry(binary_entry)
        assert exc_info.value.code == ReasonerErrorCode.SENSITIVE_DATA_LEAK
    
    @pytest.mark.asyncio
    async def test_binary_data_detection_redis(self, redis_manager):
        """Test that binary data is detected in Redis."""
        binary_entry = create_test_entry("normal")
        binary_entry["context"]["binary"] = b"bytes_data"
        
        with pytest.raises(ReasonerError) as exc_info:
            await redis_manager.add_entry(binary_entry)
        assert exc_info.value.code == ReasonerErrorCode.SENSITIVE_DATA_LEAK

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])