"""
Comprehensive test suite for omnicore_engine/database/database.py
"""

import asyncio
import base64
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest
from cryptography.fernet import Fernet

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from omnicore_engine.database import (
    Database,
    safe_serialize,
    validate_fernet_key,
    validate_user_id,
)


@pytest.fixture
def mock_settings():
    """Mock ArbiterConfig settings."""
    settings = Mock()
    settings.LOG_LEVEL = "INFO"
    settings.DB_POOL_SIZE = 5
    settings.DB_POOL_MAX_OVERFLOW = 10
    settings.database_path = "sqlite+aiosqlite:///:memory:"
    settings.redis_url = "redis://localhost:6379"
    settings.ENCRYPTION_KEY = Mock(
        get_secret_value=lambda: Fernet.generate_key().decode()
    )
    settings.FERNET_KEYS = Mock(get_secret_value=lambda: Fernet.generate_key().decode())
    settings.DB_RETRY_ATTEMPTS = 3
    settings.DB_RETRY_DELAY = 1
    settings.DB_CIRCUIT_THRESHOLD = 5
    settings.DB_CIRCUIT_TIMEOUT = 60
    settings.EXPERIMENTAL_FEATURES_ENABLED = False
    settings.MAX_BACKUPS = 10
    return settings


@pytest.fixture
def mock_security_config():
    """Mock security configuration."""
    config = Mock()
    config.dict = Mock(
        return_value={
            "encryption_key": Fernet.generate_key().decode(),
            "key_rotation_interval": 30,
            "audit_enabled": True,
        }
    )
    return config


@pytest.fixture
def temp_db_path():
    """Create a temporary database path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir) / "test.db"


@pytest.fixture
async def database(mock_settings, mock_security_config, temp_db_path):
    """Create a Database instance for testing."""
    with patch("omnicore_engine.database.database.ArbiterConfig", return_value=mock_settings):
        with patch("omnicore_engine.database.database.get_security_config", return_value=mock_security_config):
            with patch("omnicore_engine.database.database.EnterpriseSecurityUtils") as mock_security:
                mock_security_instance = Mock()
                mock_security_instance.encrypt = lambda x: base64.b64encode(x).decode()
                mock_security_instance.decrypt = lambda x: base64.b64decode(x.encode())
                mock_security.return_value = mock_security_instance

                db = Database(f"sqlite+aiosqlite:///{temp_db_path}")
                await db.initialize()
                yield db
                await db.engine.dispose()


class TestSafeSerialize:
    """Test the safe_serialize utility function."""

    def test_serialize_datetime(self):
        dt = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        result = safe_serialize(dt)
        assert result == "2025-01-01T12:00:00+00:00"

    def test_serialize_bytes(self):
        data = b"test_data"
        result = safe_serialize(data)
        assert result == base64.b64encode(data).decode("utf-8")

    def test_serialize_set(self):
        data = {1, 2, 3}
        result = safe_serialize(data)
        assert result == [1, 2, 3]

    def test_serialize_circular_reference(self):
        data = {"key": "value"}
        data["self"] = data
        result = safe_serialize(data)
        assert result["self"] == "[Circular Reference: dict]"

    def test_serialize_nested_dict(self):
        data = {
            "level1": {"level2": {"datetime": datetime(2025, 1, 1), "bytes": b"data"}}
        }
        result = safe_serialize(data)
        assert isinstance(result["level1"]["level2"]["datetime"], str)
        assert isinstance(result["level1"]["level2"]["bytes"], str)


class TestValidation:
    """Test validation functions."""

    def test_validate_fernet_key_valid(self):
        key = Fernet.generate_key()
        assert validate_fernet_key(key) is True

    def test_validate_fernet_key_invalid(self):
        assert validate_fernet_key(b"invalid_key") is False

    def test_validate_user_id_valid(self):
        assert validate_user_id("user123") == "user123"
        assert validate_user_id("user_name-123") == "user_name-123"

    def test_validate_user_id_invalid(self):
        with pytest.raises(ValueError, match="Invalid user_id format"):
            validate_user_id("user@123")
        with pytest.raises(ValueError, match="Invalid user_id format"):
            validate_user_id("")
        with pytest.raises(ValueError, match="Invalid user_id format"):
            validate_user_id("a" * 256)


class TestDatabaseInit:
    """Test Database initialization."""

    @pytest.mark.asyncio
    async def test_init_sqlite(self, mock_settings, mock_security_config, temp_db_path):
        with patch("omnicore_engine.database.database.ArbiterConfig", return_value=mock_settings):
            with patch(
                "omnicore_engine.database.database.get_security_config", return_value=mock_security_config
            ):
                with patch("omnicore_engine.database.database.EnterpriseSecurityUtils"):
                    db = Database(f"sqlite+aiosqlite:///{temp_db_path}")
                    assert db.db_path == f"sqlite+aiosqlite:///{temp_db_path}"
                    assert db.is_postgres is False
                    assert db.sqlite_db_file_path == temp_db_path

    @pytest.mark.asyncio
    async def test_init_postgresql(self, mock_settings, mock_security_config):
        mock_settings.database_path = "postgresql://user:pass@localhost/db"
        with patch("omnicore_engine.database.database.ArbiterConfig", return_value=mock_settings):
            with patch(
                "omnicore_engine.database.database.get_security_config", return_value=mock_security_config
            ):
                with patch("omnicore_engine.database.database.EnterpriseSecurityUtils"):
                    db = Database("postgresql://user:pass@localhost/db")
                    assert db.is_postgres is True
                    assert db.sqlite_db_file_path is None

    def test_init_invalid_path(self, mock_settings, mock_security_config):
        with patch("omnicore_engine.database.database.ArbiterConfig", return_value=mock_settings):
            with patch(
                "omnicore_engine.database.database.get_security_config", return_value=mock_security_config
            ):
                with patch("omnicore_engine.database.database.EnterpriseSecurityUtils"):
                    with pytest.raises(
                        ValueError, match="db_path must be a non-empty string"
                    ):
                        Database("")
                    with pytest.raises(
                        ValueError, match="db_path must be a non-empty string"
                    ):
                        Database(None)


class TestDatabaseOperations:
    """Test database CRUD operations."""

    @pytest.mark.asyncio
    async def test_save_and_get_preferences(self, database):
        user_id = "test_user"
        prefs = {"theme": "dark", "language": "en"}

        await database.save_preferences(user_id, prefs)
        result = await database.get_preferences(user_id)

        assert result == prefs

    @pytest.mark.asyncio
    async def test_save_and_get_preferences_encrypted(self, database):
        user_id = "test_user"
        prefs = {"theme": "dark", "language": "en"}

        await database.save_preferences(user_id, prefs, encrypt=True)
        result = await database.get_preferences(user_id, decrypt=True)

        assert result == prefs

    @pytest.mark.asyncio
    async def test_get_preferences_nonexistent(self, database):
        result = await database.get_preferences("nonexistent_user")
        assert result is None

    @pytest.mark.asyncio
    async def test_save_and_get_simulation_legacy(self, database):
        sim_id = "sim123"
        request_data = {"input": "test"}
        result_data = {"output": "result"}
        status = "completed"
        user_id = "test_user"

        await database.save_simulation_legacy(
            sim_id, request_data, result_data, status, user_id
        )
        result = await database.get_simulation_legacy(sim_id)

        assert result is not None
        assert result["sim_id"] == sim_id
        assert result["request_data"] == request_data
        assert result["result"] == result_data
        assert result["status"] == status
        assert result["user_id"] == user_id

    @pytest.mark.asyncio
    async def test_delete_simulation_legacy(self, database):
        sim_id = "sim123"
        request_data = {"input": "test"}
        result_data = {"output": "result"}
        status = "completed"

        await database.save_simulation_legacy(sim_id, request_data, result_data, status)
        await database.delete_simulation_legacy(sim_id)
        result = await database.get_simulation_legacy(sim_id)

        assert result is None

    @pytest.mark.asyncio
    async def test_save_and_get_plugin_legacy(self, database):
        plugin_meta = {
            "kind": "analyzer",
            "name": "test_plugin",
            "version": "1.0.0",
            "config": {"enabled": True},
        }

        await database.save_plugin_legacy(plugin_meta)
        result = await database.get_plugin_legacy("analyzer", "test_plugin")

        assert result == plugin_meta

    @pytest.mark.asyncio
    async def test_list_plugins_legacy(self, database):
        plugins = [
            {"kind": "analyzer", "name": "plugin1", "version": "1.0"},
            {"kind": "processor", "name": "plugin2", "version": "2.0"},
        ]

        for plugin in plugins:
            await database.save_plugin_legacy(plugin)

        result = await database.list_plugins_legacy()
        assert len(result) == 2
        assert all(p["kind"] in ["analyzer", "processor"] for p in result)

    @pytest.mark.asyncio
    async def test_delete_plugin_legacy(self, database):
        plugin_meta = {"kind": "analyzer", "name": "test_plugin"}

        await database.save_plugin_legacy(plugin_meta)
        await database.delete_plugin_legacy("analyzer", "test_plugin")
        result = await database.get_plugin_legacy("analyzer", "test_plugin")

        assert result is None


class TestAgentStateOperations:
    """Test agent state operations."""

    @pytest.mark.asyncio
    async def test_save_agent_state(self, database):
        agent = Mock()
        agent.id = "agent123"
        agent.energy = 100
        agent.metadata = {
            "x": 10,
            "y": 20,
            "world_size": 100,
            "agent_type": "explorer",
            "inventory": {"item1": 1},
            "language": {"en": True},
            "memory": ["event1"],
            "personality": {"trait": 0.5},
            "custom_attributes": {"special": True},
        }

        with patch.object(
            database.policy_engine, "should_auto_learn", return_value=(True, "Allowed")
        ):
            with patch.object(
                database.knowledge_graph, "add_fact", new_callable=AsyncMock
            ):
                await database.save_agent_state(agent)

    @pytest.mark.asyncio
    async def test_get_agent_state(self, database):
        # First save an agent
        agent = Mock()
        agent.id = "agent123"
        agent.energy = 100
        agent.metadata = {
            "x": 10,
            "y": 20,
            "world_size": 100,
            "agent_type": "explorer",
            "inventory": {},
            "language": {},
            "memory": [],
            "personality": {},
            "custom_attributes": {},
        }

        with patch.object(
            database.policy_engine, "should_auto_learn", return_value=(True, "Allowed")
        ):
            with patch.object(
                database.knowledge_graph, "add_fact", new_callable=AsyncMock
            ):
                await database.save_agent_state(agent)

        # Now retrieve it
        result = await database.get_agent_state("agent123")
        assert result is not None
        assert result["id"] == "agent123"
        assert result["energy"] == 100
        assert result["x"] == 10
        assert result["y"] == 20

    @pytest.mark.asyncio
    async def test_query_agent_states(self, database):
        # Save multiple agents
        for i in range(3):
            agent = Mock()
            agent.id = f"agent{i}"
            agent.energy = 100 - (i * 10)
            agent.metadata = {
                "x": i * 10,
                "y": i * 20,
                "world_size": 100,
                "agent_type": "explorer",
                "inventory": {},
                "language": {},
                "memory": [],
                "personality": {},
                "custom_attributes": {},
            }

            with patch.object(
                database.policy_engine,
                "should_auto_learn",
                return_value=(True, "Allowed"),
            ):
                with patch.object(
                    database.knowledge_graph, "add_fact", new_callable=AsyncMock
                ):
                    await database.save_agent_state(agent)

        # Query all agents
        results = await database.query_agent_states()
        assert len(results) >= 3

        # Query with filter
        results = await database.query_agent_states({"agent_type": "explorer"})
        assert all(r["agent_type"] == "explorer" for r in results)


class TestAuditOperations:
    """Test audit record operations."""

    @pytest.mark.asyncio
    async def test_save_audit_record(self, database):
        record = {
            "uuid": "audit123",
            "kind": "action",
            "name": "test_action",
            "detail": {"action": "performed"},
            "ts": datetime.utcnow().timestamp(),
            "hash": hashlib.sha256(b"test").hexdigest(),
            "sim_id": "sim123",
            "agent_id": "agent123",
            "context": {"env": "test"},
            "custom_attributes": {"custom": True},
        }

        await database.save_audit_record(record)

    @pytest.mark.asyncio
    async def test_query_audit_records(self, database):
        # Save some records first
        for i in range(3):
            record = {
                "uuid": f"audit{i}",
                "kind": "action",
                "name": f"action_{i}",
                "detail": {"index": i},
                "ts": datetime.utcnow().timestamp(),
                "hash": hashlib.sha256(f"test{i}".encode()).hexdigest(),
            }
            await database.save_audit_record(record)

        # Query all records
        results = await database.query_audit_records()
        assert len(results) >= 3

        # Query with filters
        results = await database.query_audit_records({"kind": "action"})
        assert all(r["kind"] == "action" for r in results)


class TestSnapshotOperations:
    """Test snapshot operations."""

    @pytest.mark.asyncio
    async def test_snapshot_and_get_audit_state(self, database):
        snapshot_id = "snapshot123"
        state = {
            "agents": ["agent1", "agent2"],
            "timestamp": datetime.utcnow().isoformat(),
        }
        encrypted_state = database.encrypter.encrypt(
            json.dumps(state).encode()
        ).decode()
        user_id = "test_user"

        await database.snapshot_audit_state(snapshot_id, encrypted_state, user_id)
        result = await database.get_audit_snapshot(snapshot_id)

        assert result is not None
        assert result["snapshot_id"] == snapshot_id
        assert result["user_id"] == user_id
        # State should be decrypted
        assert "agents" in result["state"]

    @pytest.mark.asyncio
    async def test_snapshot_world_state(self, database):
        # Create some agents first
        for i in range(2):
            agent = Mock()
            agent.id = f"agent{i}"
            agent.energy = 100
            agent.metadata = {
                "x": i * 10,
                "y": i * 20,
                "world_size": 100,
                "agent_type": "explorer",
                "inventory": {},
                "language": {},
                "memory": [],
                "personality": {},
                "custom_attributes": {},
            }

            with patch.object(
                database.policy_engine,
                "should_auto_learn",
                return_value=(True, "Allowed"),
            ):
                with patch.object(
                    database.knowledge_graph, "add_fact", new_callable=AsyncMock
                ):
                    await database.save_agent_state(agent)

        # Create snapshot
        snapshot_id = await database.snapshot_world_state("test_user")
        assert snapshot_id is not None
        assert len(snapshot_id) > 0


class TestBackupOperations:
    """Test backup operations."""

    def test_backup(self, database):
        if database.sqlite_db_file_path:
            # Create the database file first
            database.sqlite_db_file_path.touch()

            # Perform backup
            database.backup(max_backups=2)

            # Check backup was created
            backups = list(database.CONFIG["backup_dir"].glob("backup_*.db"))
            assert len(backups) > 0

    @pytest.mark.asyncio
    async def test_check_integrity(self, database):
        result = await database.check_integrity_legacy()
        assert isinstance(result, bool)


class TestErrorHandling:
    """Test error handling in database operations."""

    @pytest.mark.asyncio
    async def test_save_preferences_invalid_user(self, database):
        with pytest.raises(ValueError, match="Invalid user_id format"):
            await database.save_preferences("invalid@user", {"pref": "value"})

    @pytest.mark.asyncio
    async def test_save_plugin_missing_fields(self, database):
        with pytest.raises(ValueError, match="Plugin kind and name are required"):
            await database.save_plugin_legacy({"version": "1.0"})

    @pytest.mark.asyncio
    async def test_circuit_breaker_simulation(self, database):
        # Mock the circuit breaker to be open
        with patch("database.circuit") as mock_circuit:
            mock_circuit.side_effect = Exception("Circuit breaker is open")

            with pytest.raises(Exception, match="Circuit breaker is open"):
                await database.save_simulation("sim123", {}, {}, "failed")


class TestEncryption:
    """Test encryption/decryption functionality."""

    def test_validate_json_plain(self, database):
        data = {"key": "value", "number": 123}
        result = database._validate_json(data)
        assert json.loads(result) == data

    def test_validate_json_encrypted(self, database):
        data = {"key": "value", "number": 123}
        result = database._validate_json(data, encrypt=True)
        # Result should be encrypted (base64 encoded in our mock)
        assert result != json.dumps(data)

    def test_decrypt_json_plain(self, database):
        data = {"key": "value"}
        json_str = json.dumps(data)
        result = database._decrypt_json(json_str, encrypted=False)
        assert result == data

    def test_decrypt_json_encrypted(self, database):
        data = {"key": "value"}
        json_str = json.dumps(data)
        encrypted = database.encrypter.encrypt(json_str.encode()).decode()
        result = database._decrypt_json(encrypted, encrypted=True)
        assert result == data


class TestConcurrency:
    """Test concurrent database operations."""

    @pytest.mark.asyncio
    async def test_concurrent_saves(self, database):
        async def save_agent(agent_id):
            agent = Mock()
            agent.id = agent_id
            agent.energy = 100
            agent.metadata = {
                "x": 0,
                "y": 0,
                "world_size": 100,
                "agent_type": "test",
                "inventory": {},
                "language": {},
                "memory": [],
                "personality": {},
                "custom_attributes": {},
            }

            with patch.object(
                database.policy_engine,
                "should_auto_learn",
                return_value=(True, "Allowed"),
            ):
                with patch.object(
                    database.knowledge_graph, "add_fact", new_callable=AsyncMock
                ):
                    await database.save_agent_state(agent)

        # Run multiple saves concurrently
        tasks = [save_agent(f"agent{i}") for i in range(5)]
        await asyncio.gather(*tasks)

        # Verify all were saved
        results = await database.query_agent_states()
        assert len(results) >= 5
