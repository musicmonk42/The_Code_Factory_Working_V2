# File: omnicore_engine/database.py
from __future__ import annotations

import json
import sqlalchemy
from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy import text, func, Column, Integer, String, JSON, DateTime, select, insert, delete
from sqlalchemy.orm import sessionmaker
from typing import Dict, Optional, Any, List, Coroutine, Set, TypeVar, Callable, Union
from pydantic import SecretStr
from circuitbreaker import circuit
from omnicore_engine.retry_compat import retry
import hashlib
import uuid
from cryptography.fernet import Fernet, InvalidToken
import logging
from datetime import datetime, date
import time
import re
from pathlib import Path
import threading
import sqlite3
import base64
import collections.abc
import numpy as np
import aiosqlite
import asyncio
import concurrent.futures
from dataclasses import dataclass
from prometheus_client import Counter, Histogram, Gauge, REGISTRY
import logging.handlers
import sys
import shutil

logger = logging.getLogger(__name__)

# Local imports from the refactored structure
from .models import Base, AgentState, ExplainAuditRecord, GeneratorAgentState, SFEAgentState
from .metrics_helpers import get_or_create_counter_local, get_or_create_gauge_local, get_or_create_histogram_local
from omnicore_engine.message_bus.encryption import FernetEncryption

# Corrected imports using the new arbiter package and centralized settings
from arbiter.config import ArbiterConfig

# --- optional feedback manager dependency -----------------------------------
try:
    from omnicore_engine.feedback_manager import FeedbackManager, FeedbackType
except ImportError:
    try:
        from arbiter.feedback import FeedbackManager, FeedbackType
    except ImportError:
        # Provide a no-op shim so imports don't fail in environments/tests that
        # don't ship the feedback manager module.
        class FeedbackType:
            BUG_REPORT = "BUG_REPORT"
            INFO = "INFO"
            WARNING = "WARNING"
            ERROR = "ERROR"

        class FeedbackManager:
            def __init__(self, *args, **kwargs):
                pass
            async def record_feedback(self, **kwargs):
                return None

settings = ArbiterConfig()

try:
    from arbiter.policy.core import PolicyEngine
except ImportError:
    logger.warning("PolicyEngine module not found. Policy checks will be unavailable.")
    class PolicyEngine:
        def __init__(self, *args, **kwargs): pass
        async def should_auto_learn(self, *args, **kwargs): return True, "Mock Policy: Always allowed"

try:
    from arbiter.knowledge_graph.core import KnowledgeGraph
except ImportError:
    logger.warning("KnowledgeGraph module not found. KnowledgeGraph features will be unavailable.")
    class KnowledgeGraph:
        def __init__(self, *args, **kwargs): pass
        async def add_fact(self, *args, **kwargs): logger.warning("Mock KnowledgeGraph: add_fact called.")

from omnicore_engine.metrics import DB_OPERATIONS, DB_ERRORS, AUDIT_DB_OPERATIONS, AUDIT_DB_ERRORS

# Local metrics for merged functionalities
DB_OPERATIONS_LOCAL = get_or_create_counter_local("db_operations_total_local", "Total database operations (local)", ["operation"])
DB_ERRORS_LOCAL = get_or_create_counter_local("db_errors_total_local", "Total database errors (local)", ["operation"])
DB_LATENCY_LOCAL = get_or_create_histogram_local("db_operation_latency_seconds_local", "Database operation latency (local)", ["operation"])

# Context manager for aiosqlite
from contextlib import asynccontextmanager

# Import plugin_registry to avoid circular dependency in Database class
try:
    from omnicore_engine import plugin_registry
except ImportError:
    logger.warning("Plugin registry not available. Database will operate without plugin-related features.")
    plugin_registry = None

# New imports for EnterpriseSecurityUtils
from omnicore_engine.security_utils import EnterpriseSecurityUtils
from omnicore_engine.security_config import get_security_config


# This function should be moved to a separate utils.py file to avoid circular imports.
def safe_serialize(obj: Any, _seen: Optional[Set[int]] = None) -> Any:
    """Safely serializes objects, handling non-JSON-serializable types and circular references."""
    if _seen is None:
        _seen = set()
    obj_id = id(obj)
    if obj_id in _seen:
        return f"[Circular Reference: {type(obj).__name__}]"
    _seen.add(obj_id)

    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, (bytes, bytearray)):
        return base64.b64encode(obj).decode('utf-8')
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, (set, frozenset)):
        return list(obj)
    if isinstance(obj, collections.abc.Mapping):
        return {k: safe_serialize(v, _seen) for k, v in obj.items()}
    if isinstance(obj, collections.abc.Iterable) and not isinstance(obj, str):
        return [safe_serialize(item, _seen) for item in obj]
    if isinstance(obj, object) and hasattr(obj, '__dict__'):
        return safe_serialize(obj.__dict__, _seen)
    return obj


def validate_fernet_key(key: bytes) -> bool:
    """Validates a Fernet key."""
    try:
        Fernet(key)
        return True
    except Exception:
        return False

def validate_user_id(user_id: str) -> str:
    """Validates user_id format."""
    if not re.match(r'^[a-zA-Z0-9_-]{1,255}$', user_id):
        raise ValueError("Invalid user_id format")
    return user_id

def serialize_audit_record(record: Any) -> Dict[str, Any]:
    """
    Serialize an ExplainAuditRecord to a dictionary.
    
    This function provides explicit control over which fields are serialized,
    making it clear what data is being exposed via the API.
    """
    return {
        "uuid": record.uuid,
        "kind": record.kind,
        "name": record.name,
        "detail": record.detail,
        "ts": record.ts,
        "hash": record.hash,
        "sim_id": record.sim_id,
        "error": record.error,
        "agent_id": record.agent_id,
        "context": record.context,
        "custom_attributes": record.custom_attributes,
        "rationale": record.rationale,
        "simulation_outcomes": record.simulation_outcomes,
        "tenant_id": record.tenant_id,
        "explanation_id": record.explanation_id,
        "root_merkle_hash": record.root_merkle_hash
    }

# Default values for agent state initialization
# These can be overridden via configuration if needed in the future
DEFAULT_AGENT_X = 0
DEFAULT_AGENT_Y = 0
DEFAULT_AGENT_ENERGY = 100
DEFAULT_AGENT_WORLD_SIZE = 100

class Database:
    """
    Database class for managing agent states, simulations, and audit records.
    
    Note on agent_id handling:
    - AgentState.id in the models is defined as Integer (inherited from ArbiterAgentState)
    - Throughout this class, agent_id parameters are treated as strings and hashed using SHA256
    - The hashed value is stored in AgentState.name field (which is a String)
    - This design provides privacy by not storing raw agent IDs directly
    - When querying agents, the agent_id string is first hashed to match the stored name
    """
    def __init__(self, db_path: str, system_audit_merkle_tree: Optional[Any] = None):
        if not db_path or not isinstance(db_path, str):
            raise ValueError("db_path must be a non-empty string")
        
        # Load security configuration
        self.security_config = get_security_config()
        self.security_utils = EnterpriseSecurityUtils(self.security_config.dict())
        
        self.db_path = db_path
        
        db_echo = settings.LOG_LEVEL.upper() == 'DEBUG'

        engine_params = {
            "echo": db_echo,
            "pool_pre_ping": True,
            "json_serializer": lambda obj: json.dumps(obj, default=safe_serialize),
            "future": True # Use future=True for SQLAlchemy 2.0 style
        }
        
        # Add timeout parameters for all engines
        engine_params["connect_args"] = {
            "timeout": 30,
            "options": "-c statement_timeout=30000"
        }

        if self.db_path.startswith('postgresql://'):
            self.engine: AsyncEngine = create_async_engine(self.db_path, **engine_params)
            self.is_postgres = True
            logger.info(f"Database initialized with PostgreSQL engine: {self.db_path.split('@')[-1]}")
        elif self.db_path.startswith("sqlite+aiosqlite://"):
            sqlite_db_file = self.db_path.replace("sqlite+aiosqlite:///", "")
            db_dir = Path(sqlite_db_file).parent
            try:
                db_dir.mkdir(parents=True, exist_ok=True)
                logger.info(f"Ensured database file directory exists: {db_dir}")
            except Exception as e:
                logger.critical(f"Failed to create database file directory {db_dir}: {e}")
                sys.exit(1)
            
            engine_params["connect_args"]["check_same_thread"] = False
            self.engine: AsyncEngine = create_async_engine(self.db_path, **engine_params)
            self.is_postgres = False
            logger.info(f"Database initialized with SQLite (aiosqlite) engine: {self.db_path}")
        elif self.db_path.startswith("sqlite:///"):
            sqlite_db_file = self.db_path.replace("sqlite:///", "")
            db_dir = Path(sqlite_db_file).parent
            try:
                db_dir.mkdir(parents=True, exist_ok=True)
                logger.info(f"Ensured database file directory exists: {db_dir}")
            except Exception as e:
                logger.critical(f"Failed to create database file directory {db_dir}: {e}")
                sys.exit(1)
            engine_params["connect_args"]["check_same_thread"] = False
            self.engine: AsyncEngine = create_async_engine(self.db_path, **engine_params)
            self.is_postgres = False
            logger.info(f"Database initialized with SQLite engine: {self.db_path}")
        else:
            # Fallback for other database types, or if no specific prefix is given
            engine_params["pool_size"] = settings.DB_POOL_SIZE
            engine_params["max_overflow"] = settings.DB_POOL_MAX_OVERFLOW
            engine_params["connect_args"] = {}
            self.engine: AsyncEngine = create_async_engine(self.db_path, **engine_params)
            self.is_postgres = False
            logger.info(f"Database initialized with generic engine: {self.db_path}")


        self.AsyncSessionLocal = async_sessionmaker(
            bind=self.engine,
            class_=AsyncSession,
            expire_on_commit=False
        )
        
        # Replace existing Fernet initialization with enterprise encryption
        self.encrypter = self.security_utils  # Use security_utils encryption methods
        
        self.feedback_manager = FeedbackManager(
            db_dsn=settings.database_path,
            redis_url=settings.redis_url,
            encryption_key=settings.ENCRYPTION_KEY.get_secret_value()
        )
        self.policy_engine = PolicyEngine(arbiter_instance=None)
        self.knowledge_graph = KnowledgeGraph()
        
        self.plugin_registry = plugin_registry.PLUGIN_REGISTRY if plugin_registry else None

        self.retry_attempts = settings.DB_RETRY_ATTEMPTS
        self.retry_delay = settings.DB_RETRY_DELAY
        self.circuit_threshold = settings.DB_CIRCUIT_THRESHOLD
        self.circuit_timeout = settings.DB_CIRCUIT_TIMEOUT
        
        self.system_audit_merkle_tree = system_audit_merkle_tree
        
        logger.info(f"Database initialized with async engine. Pool size: {getattr(settings, 'DB_POOL_SIZE', 'N/A')}, max overflow: {getattr(settings, 'DB_POOL_MAX_OVERFLOW', 'N/A')}")

        if self.db_path.startswith("sqlite:///"):
            self.sqlite_db_file_path = Path(self.db_path.replace("sqlite:///", ""))
        elif self.db_path.startswith("sqlite+aiosqlite:///"):
            self.sqlite_db_file_path = Path(self.db_path.replace("sqlite+aiosqlite:///", ""))
        else:
            self.sqlite_db_file_path = None

        if self.sqlite_db_file_path and self.sqlite_db_file_path.parent:
            try:
                self.sqlite_db_file_path.parent.mkdir(parents=True, exist_ok=True)
                logger.info(f"Ensured database file directory exists (for local SQLite): {self.sqlite_db_file_path.parent}")
            except Exception as e:
                logger.critical(f"Failed to create database file directory (for local SQLite) {self.sqlite_db_file_path.parent}: {e}")
                sys.exit(1)

        base_data_dir = self.sqlite_db_file_path.parent if self.sqlite_db_file_path else Path("./data")
        if not base_data_dir.as_posix():
            base_data_dir = Path("./data")

        max_backups_val = getattr(settings, 'MAX_BACKUPS', 10)

        self.CONFIG = {
            "db_dir": base_data_dir,
            "db_file": self.sqlite_db_file_path,
            "backup_dir": base_data_dir / "backups",
            "encryption_key": settings.ENCRYPTION_KEY.get_secret_value(),
            "max_backups": int(max_backups_val),
            "connection_pool_size": int(getattr(settings, 'DB_POOL_SIZE', 5)),
        }

        try:
            self.CONFIG["backup_dir"].mkdir(parents=True, exist_ok=True)
            logger.info(f"Ensured backup directory exists: {self.CONFIG['backup_dir']}")
        except Exception as e:
            logger.critical(f"Failed to create backup directory {self.CONFIG['backup_dir']}: {e}")
            sys.exit(1)

        self._serializers: Dict[type, Callable[[Any], Any]] = {}

    async def initialize(self) -> None:
        try:
            logger.info("Database component: Starting async initialization...")
            
            if not self.is_postgres:
                await self._initialize_legacy_tables_async()

            async with self.engine.execution_options(isolation_level="SERIALIZABLE").begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
                
                try:
                    from alembic import config, command
                    alembic_cfg = config.Config()
                    current_dir = Path(__file__).parent
                    project_root = Path(__file__).parent.parent
                    alembic_cfg.set_main_option("script_location", str(project_root / "migrations"))
                    alembic_cfg.set_main_option("sqlalchemy.url", self.db_path)
                    command.upgrade(alembic_cfg, "head")
                    logger.info("Schema migrations applied successfully (via Alembic).")
                except ImportError:
                    logger.warning("Alembic is not installed. Skipping schema migrations.")
                except Exception as e:
                    logger.critical(f"Failed to apply migrations: {e}", exc_info=True)
                    sys.exit(1)

            if self.is_postgres:
                await self.migrate_to_citus()
                logger.info("For PostgreSQL, ensure data migration from SQLite (if any) is handled externally.")

            logger.info("Database tables ensured (created/verified asynchronously).")
            logger.info("Database component: Async initialization completed successfully.")
        except sqlalchemy.exc.SQLAlchemyError as e:
            logger.critical(f"Database initialization failed due to SQLAlchemyError: {e}", exc_info=True)
            raise
        except Exception as e:
            logger.critical(f"Database initialization failed due to unexpected error: {e}", exc_info=True)
            raise

    async def create_tables(self):
        DB_OPERATIONS.labels(operation='create_tables').inc()
        start_time = time.time()
        try:
            async with self.engine.execution_options(isolation_level="SERIALIZABLE").begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
                
                try:
                    from alembic import config, command
                    alembic_cfg = config.Config()
                    current_dir = Path(__file__).parent
                    project_root = Path(__file__).parent.parent
                    alembic_cfg.set_main_option("script_location", str(project_root / "migrations"))
                    alembic_cfg.set_main_option("sqlalchemy.url", self.db_path)
                    command.upgrade(alembic_cfg, "head")
                    logger.info("Schema migrations applied successfully (via Alembic).")
                except ImportError:
                    logger.warning("Alembic is not installed. Skipping schema migrations.")
                except Exception as e:
                    logger.critical(f"Failed to apply migrations: {e}", exc_info=True)
                    sys.exit(1)

                logger.info("Database tables ensured (created/verified asynchronously).")
        except sqlalchemy.exc.SQLAlchemyError as e:
            DB_ERRORS.labels(operation='create_tables').observe(time.time() - start_time)
            await self.feedback_manager.record_feedback(
                user_id="system", feedback_type=FeedbackType.BUG_REPORT,
                details={'type': 'db_error', 'operation': 'create_tables', 'error': str(e)}
            )
            raise

    async def _initialize_legacy_tables_async(self) -> None:
        """Initializes legacy SQLite tables if the current database is SQLite."""
        if self.is_postgres:
            logger.info("Skipping legacy table initialization for PostgreSQL database.")
            return

        with DB_LATENCY_LOCAL.labels(operation="initialize_legacy_tables_async").time():
            try:
                if self.sqlite_db_file_path is None:
                    raise ValueError("SQLite database path is not set for legacy table initialization.")

                async with aiosqlite.connect(self.sqlite_db_file_path) as conn:
                    await conn.execute("""
                        CREATE TABLE IF NOT EXISTS preferences (
                            user_id TEXT PRIMARY KEY,
                            data TEXT NOT NULL,
                            encrypted INTEGER DEFAULT 0,
                            deleted INTEGER DEFAULT 0,
                            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                        )
                    """)
                    await conn.execute("""
                        CREATE TABLE IF NOT EXISTS simulations (
                            sim_id TEXT PRIMARY KEY,
                            user_id TEXT,
                            request_data TEXT,
                            result TEXT,
                            status TEXT,
                            encrypted INTEGER DEFAULT 0,
                            deleted INTEGER DEFAULT 0,
                            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                        )
                    """)
                    await conn.execute("""
                        CREATE TABLE IF NOT EXISTS plugins (
                            kind TEXT,
                            name TEXT,
                            meta TEXT,
                            encrypted INTEGER DEFAULT 0,
                            deleted INTEGER DEFAULT 0,
                            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                            PRIMARY KEY (kind, name)
                        )
                    """)
                    await conn.execute("""
                        CREATE TABLE IF NOT EXISTS feedback (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            type TEXT,
                            message TEXT,
                            timestamp TEXT
                        )
                    """)
                    await conn.execute("""
                        CREATE TABLE IF NOT EXISTS audit_snapshots (
                            snapshot_id TEXT PRIMARY KEY,
                            timestamp TEXT NOT NULL,
                            state TEXT NOT NULL,
                            user_id TEXT NOT NULL
                        )
                    """)
                    await conn.execute("""
                        CREATE TABLE IF NOT EXISTS world_snapshots (
                            snapshot_id TEXT PRIMARY KEY,
                            timestamp TEXT NOT NULL,
                            state TEXT NOT NULL,
                            user_id TEXT NOT NULL
                        )
                    """)
                    await conn.commit()
                logger.info("Legacy/non-ORM tables ensured (preferences, simulations, plugins, feedback, audit_snapshots, world_snapshots) asynchronously.")
                DB_OPERATIONS_LOCAL.labels(operation="initialize_legacy_tables_async").inc()
            except Exception as e:
                DB_ERRORS_LOCAL.labels(operation="initialize_legacy_tables_async").inc()
                logger.error(f"Failed to create legacy/non-ORM tables asynchronously: {e}", exc_info=True)
                sys.exit(1)

    def register_serializer(self, type_: type, serializer: Callable[[Any], Any]) -> None:
        logger.warning(f"Serializer registration for {type_} is not directly supported by current safe_serialize implementation.")


    def safe_serialize_wrapper(self, obj: Any, _seen: Optional[Set[int]] = None) -> Any:
        return safe_serialize(obj, _seen)

    def _validate_json(self, data: Any, encrypt: bool = False) -> str:
        try:
            serialized_data = self.safe_serialize_wrapper(data)
            json_str = json.dumps(serialized_data)
            if encrypt:
                json_str = self.encrypter.encrypt(json_str.encode()).decode("utf-8")
            return json_str
        except (TypeError, ValueError) as e:
            logger.error(f"Failed to serialize data to JSON: {e}", exc_info=True)
            raise ValueError(f"Data is not JSON-serializable: {e}")

    def _decrypt_json(self, data: Union[str, bytes], encrypted: bool) -> Any:
        try:
            if encrypted:
                if isinstance(data, str):
                    data_bytes = data.encode('utf-8')
                else:
                    data_bytes = data
                return json.loads(self.encrypter.decrypt(data_bytes).decode())
            if isinstance(data, bytes):
                return json.loads(data.decode('utf-8'))
            return json.loads(data)
        except (InvalidToken, Exception) as e:
            logger.error(f"Failed to decrypt or deserialize JSON data: {e}", exc_info=True)
            return {}

    @asynccontextmanager
    async def _get_aiosqlite_connection(self):
        """Provides an aiosqlite connection, only for SQLite databases."""
        if self.is_postgres or self.sqlite_db_file_path is None:
            raise RuntimeError("Attempted to get aiosqlite connection for a non-SQLite or non-file-based database.")

        conn = None
        try:
            conn = await aiosqlite.connect(self.sqlite_db_file_path)
            conn.row_factory = sqlite3.Row
            yield conn
        except Exception as e:
            logger.error(f"Failed to get aiosqlite connection to {self.CONFIG['db_file']}: {e}", exc_info=True)
            DB_ERRORS_LOCAL.labels(operation="aiosqlite_connect").inc()
            raise
        finally:
            if conn:
                await conn.close()

    async def get_feedback_entries(self, query=None) -> List[Dict]:
        DB_OPERATIONS_LOCAL.labels(operation="get_feedback_entries").inc()
        query_str = "SELECT id, type, message, timestamp FROM feedback"
        params = []
        if query and "type" in query:
            query_str += " WHERE type = ?"
            params.append(query["type"])
        
        try:
            if self.is_postgres:
                raise NotImplementedError("Feedback entries retrieval not implemented for PostgreSQL directly.")
            async with self._get_aiosqlite_connection() as conn:
                cursor = await conn.execute(query_str, params)
                rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            DB_ERRORS_LOCAL.labels(operation="get_feedback_entries").inc()
            logger.error(f"Error retrieving feedback entries: {e}", exc_info=True)
            return []

    async def save_feedback_entry(self, entry: Dict[str, Any]) -> None:
        DB_OPERATIONS_LOCAL.labels(operation="save_feedback_entry").inc()
        try:
            if self.is_postgres:
                raise NotImplementedError("Feedback entries saving not implemented for PostgreSQL directly.")
            async with self._get_aiosqlite_connection() as conn:
                await conn.execute(
                    "INSERT INTO feedback (type, message, timestamp) VALUES (?, ?, ?)",
                    (entry.get("type", ""), entry.get("message", ""), entry.get("timestamp", datetime.now().isoformat()))
                )
                await conn.commit()
        except Exception as e:
            DB_ERRORS_LOCAL.labels(operation="save_feedback_entry").inc()
            logger.error(f"Error saving feedback entry: {e}", exc_info=True)
            raise

    @DB_LATENCY_LOCAL.labels(operation="get_preferences").time()
    async def get_preferences(self, user_id: str, decrypt: bool = False) -> Optional[Dict]:
        DB_OPERATIONS_LOCAL.labels(operation="get_preferences").inc()
        user_id = validate_user_id(user_id)
        query = "SELECT data, encrypted FROM preferences WHERE user_id = ? AND deleted=0"
        try:
            if self.is_postgres:
                raise NotImplementedError("Preferences retrieval not implemented for PostgreSQL directly.")
            async with self._get_aiosqlite_connection() as conn:
                cur = await conn.execute(query, (user_id,))
                row = await cur.fetchone()
            if row:
                return self._decrypt_json(row["data"], row["encrypted"] and decrypt) 
            return None
        except Exception as e:
            DB_ERRORS_LOCAL.labels(operation="get_preferences").inc()
            logger.error(f"Error retrieving preferences for {user_id}: {e}", exc_info=True)
            return None

    @DB_LATENCY_LOCAL.labels(operation="save_preferences").time()
    async def save_preferences(self, user_id: str, prefs: Dict, encrypt: bool = False) -> None:
        DB_OPERATIONS_LOCAL.labels(operation="save_preferences").inc()
        user_id = validate_user_id(user_id)
        data = self._validate_json(prefs, encrypt)
        query = """
            INSERT INTO preferences (user_id, data, encrypted, deleted, updated_at)
            VALUES (?, ?, ?, 0, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET data=excluded.data, encrypted=excluded.encrypted,
            deleted=0, updated_at=CURRENT_TIMESTAMP
        """
        try:
            if self.is_postgres:
                raise NotImplementedError("Preferences saving not implemented for PostgreSQL directly.")
            async with self._get_aiosqlite_connection() as conn:
                await conn.execute(query, (user_id, data, int(encrypt)))
                await conn.commit()
        except Exception as e:
            DB_ERRORS_LOCAL.labels(operation="save_preferences").inc()
            logger.error(f"Error saving preferences for {user_id}: {e}", exc_info=True)
            raise

    @DB_LATENCY_LOCAL.labels(operation="save_simulation_legacy").time()
    async def save_simulation_legacy(self, sim_id: str, request_data: Dict, result: Dict, status: str, user_id: Optional[str] = None, encrypt: bool = False) -> None:
        DB_OPERATIONS_LOCAL.labels(operation="save_simulation_legacy").inc()
        if user_id:
            user_id = validate_user_id(user_id)
        request_data_json = self._validate_json(request_data, encrypt)
        result_json = self._validate_json(result, encrypt)
        query = """
            INSERT OR REPLACE INTO simulations (sim_id, user_id, request_data, result, status, encrypted, deleted, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 0, CURRENT_TIMESTAMP)
        """
        try:
            if self.is_postgres:
                raise NotImplementedError("Legacy simulation saving not implemented for PostgreSQL directly.")
            async with self._get_aiosqlite_connection() as conn:
                await conn.execute(query, (sim_id, user_id, request_data_json, result_json, status, int(encrypt)))
                await conn.commit()
        except Exception as e:
            DB_ERRORS_LOCAL.labels(operation="save_simulation_legacy").inc()
            logger.error(f"Error saving legacy simulation {sim_id}: {e}", exc_info=True)
            raise

    @DB_LATENCY_LOCAL.labels(operation="get_simulation_legacy").time()
    async def get_simulation_legacy(self, sim_id: str, decrypt: bool = False) -> Optional[Dict]:
        DB_OPERATIONS_LOCAL.labels(operation="get_simulation_legacy").inc()
        query = "SELECT sim_id, user_id, request_data, result, status, encrypted, updated_at FROM simulations WHERE sim_id = ? AND deleted=0"
        try:
            if self.is_postgres:
                raise NotImplementedError("Legacy simulation retrieval not implemented for PostgreSQL directly.")
            async with self._get_aiosqlite_connection() as conn:
                cur = await conn.execute(query, (sim_id,))
                row = await cur.fetchone()

            if row:
                return {
                    "sim_id": row["sim_id"],
                    "user_id": row["user_id"],
                    "request_data": self._decrypt_json(row["request_data"], row["encrypted"] and decrypt), 
                    "result": self._decrypt_json(row["result"], row["encrypted"] and decrypt), 
                    "status": row["status"],
                    "updated_at": row["updated_at"]
                }
            return None
        except Exception as e:
            DB_ERRORS_LOCAL.labels(operation="get_simulation_legacy").inc()
            logger.error(f"Error retrieving legacy simulation {sim_id}: {e}", exc_info=True)
            return None

    @DB_LATENCY_LOCAL.labels(operation="delete_simulation_legacy").time()
    async def delete_simulation_legacy(self, sim_id: str) -> None:
        DB_OPERATIONS_LOCAL.labels(operation="delete_simulation_legacy").inc()
        query = "UPDATE simulations SET deleted=1, updated_at=CURRENT_TIMESTAMP WHERE sim_id = ?"
        try:
            if self.is_postgres:
                raise NotImplementedError("Legacy simulation deletion not implemented for PostgreSQL directly.")
            async with self._get_aiosqlite_connection() as conn:
                await conn.execute(query, (sim_id,))
                await conn.commit()
        except Exception as e:
            DB_ERRORS_LOCAL.labels(operation="delete_simulation_legacy").inc()
            logger.error(f"Error deleting legacy simulation {sim_id}: {e}", exc_info=True)
            raise

    @DB_LATENCY_LOCAL.labels(operation="save_plugin_legacy").time()
    async def save_plugin_legacy(self, plugin_meta: Dict, encrypt: bool = False) -> None:
        DB_OPERATIONS_LOCAL.labels(operation="save_plugin_legacy").inc()
        kind = plugin_meta.get("kind")
        name = plugin_meta.get("name")
        if not kind or not name:
            raise ValueError("Plugin kind and name are required")
        meta_json = self._validate_json(plugin_meta, encrypt)
        query = """
            INSERT OR REPLACE INTO plugins (kind, name, meta, encrypted, deleted, updated_at)
            VALUES (?, ?, ?, ?, 0, CURRENT_TIMESTAMP)
        """
        try:
            if self.is_postgres:
                raise NotImplementedError("Legacy plugin saving not implemented for PostgreSQL directly.")
            async with self._get_aiosqlite_connection() as conn:
                await conn.execute(query, (kind, name, meta_json, int(encrypt)))
                await conn.commit()
        except Exception as e:
            DB_ERRORS_LOCAL.labels(operation="save_plugin_legacy").inc()
            logger.error(f"Error saving legacy plugin {kind}/{name}: {e}", exc_info=True)
            raise

    @DB_LATENCY_LOCAL.labels(operation="get_plugin_legacy").time()
    async def get_plugin_legacy(self, kind: str, name: str, decrypt: bool = False) -> Optional[Dict]:
        DB_OPERATIONS_LOCAL.labels(operation="get_plugin_legacy").inc()
        query = "SELECT meta, encrypted FROM plugins WHERE kind = ? AND name = ? AND deleted=0"
        try:
            if self.is_postgres:
                raise NotImplementedError("Legacy plugin retrieval not implemented for PostgreSQL directly.")
            async with self._get_aiosqlite_connection() as conn:
                cur = await conn.execute(query, (kind, name))
                row = await cur.fetchone()
            if row:
                return self._decrypt_json(row["meta"], row["encrypted"] and decrypt) 
            return None
        except Exception as e:
            DB_ERRORS_LOCAL.labels(operation="get_plugin_legacy").inc()
            logger.error(f"Error retrieving legacy plugin {kind}/{name}: {e}", exc_info=True)
            return None

    @DB_LATENCY_LOCAL.labels(operation="list_plugins_legacy").time()
    async def list_plugins_legacy(self, decrypt: bool = False) -> List[Dict]:
        DB_OPERATIONS_LOCAL.labels(operation="list_plugins_legacy").inc()
        query = "SELECT kind, name, meta, encrypted FROM plugins WHERE deleted=0"
        try:
            if self.is_postgres:
                raise NotImplementedError("Legacy plugin listing not implemented for PostgreSQL directly.")
            async with self._get_aiosqlite_connection() as conn:
                cur = await conn.execute(query)
                rows = await cur.fetchall()
            return [
                {
                    "kind": row["kind"],
                    "name": row["name"],
                    "meta": self._decrypt_json(row["meta"], row["encrypted"] and decrypt)
                }
                for row in rows
            ]
        except Exception as e:
            DB_ERRORS_LOCAL.labels(operation="list_plugins_legacy").inc()
            logger.error(f"Error listing legacy plugins: {e}", exc_info=True)
            return []

    @DB_LATENCY_LOCAL.labels(operation="delete_plugin_legacy").time()
    async def delete_plugin_legacy(self, kind: str, name: str) -> None:
        DB_OPERATIONS_LOCAL.labels(operation="delete_plugin_legacy").inc()
        query = "UPDATE plugins SET deleted=1, updated_at=CURRENT_TIMESTAMP WHERE kind = ? AND name = ?"
        try:
            if self.is_postgres:
                raise NotImplementedError("Legacy plugin deletion not implemented for PostgreSQL directly.")
            async with self._get_aiosqlite_connection() as conn:
                await conn.execute(query, (kind, name))
                await conn.commit()
        except Exception as e:
            DB_ERRORS_LOCAL.labels(operation="delete_plugin_legacy").inc()
            logger.error(f"Error deleting legacy plugin {kind}/{name}: {e}", exc_info=True)
            raise

    def backup(self, max_backups: int = None) -> None:
        if max_backups is None:
            max_backups = self.CONFIG["max_backups"]
        with DB_LATENCY_LOCAL.labels(operation="backup").time():
            try:
                if self.is_postgres or self.sqlite_db_file_path is None:
                    logger.warning("Database backup not applicable for PostgreSQL or non-file-based databases.")
                    return

                timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
                backup_path = self.CONFIG["backup_dir"] / f"backup_{timestamp}.db"
                shutil.copy2(self.CONFIG["db_file"], backup_path)
                logger.info(f"Database backed up to {backup_path}")
                backups = sorted(self.CONFIG["backup_dir"].glob("backup_*.db"), key=lambda x: x.stat().st_mtime)
                while len(backups) > max_backups:
                    old_backup = backups.pop(0)
                    old_backup.unlink()
                    logger.info(f"Deleted old backup: {old_backup}")
                DB_OPERATIONS_LOCAL.labels(operation="backup").inc()
            except (OSError, shutil.Error) as e:
                DB_ERRORS_LOCAL.labels(operation="backup").inc()
                logger.error(f"Backup failed: {e}", exc_info=True)
                raise

    async def check_integrity_legacy(self) -> bool:
        with DB_LATENCY_LOCAL.labels(operation="check_integrity_legacy").time():
            try:
                if self.is_postgres:
                    logger.info("Database integrity check (PRAGMA) not applicable for PostgreSQL.")
                    return True

                async with self._get_aiosqlite_connection() as conn:
                    cur = await conn.execute("PRAGMA integrity_check")
                    result = await cur.fetchone()
                is_ok = result[0] == "ok"
                if not is_ok:
                    logger.error(f"Database integrity check failed: {result}")
                DB_OPERATIONS_LOCAL.labels(operation="check_integrity_legacy").inc()
                return is_ok
            except Exception as e:
                DB_ERRORS_LOCAL.labels(operation="check_integrity_legacy").inc()
                logger.error(f"Integrity check failed: {e}", exc_info=True)
                return False

    async def _anonymize_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        anonymized_data = data.copy()
        for key in ['user_id', 'name', 'agent_id']:
            if key in anonymized_data and isinstance(anonymized_data[key], str):
                anonymized_data[key] = hashlib.sha256(anonymized_data[key].encode()).hexdigest()
        return anonymized_data

    async def _log_audit(self, event: str, sim_id: str, user_id: str, details: Dict[str, Any]):
        logger.info(f"Database logging audit event: {event} for {user_id} (sim_id: {sim_id})")
        
    @circuit(failure_threshold=5, recovery_timeout=60)
    @retry(tries=3, delay=1, backoff=2)
    async def save_simulation(self, sim_id: str, req: Dict, res: Dict, status: str, user_id: Optional[str] = None):
        DB_OPERATIONS.labels(operation='save_simulation').inc()
        start_time = time.time()
        
        async with self.AsyncSessionLocal() as session:
            try:
                allowed, reason = await self.policy_engine.should_auto_learn('Database', 'save_simulation', user_id or 'system', {'sim_id': sim_id, 'status': status})
                if not allowed:
                    raise ValueError(f"Policy denied: {reason}")
                
                if user_id:
                    user_id = validate_user_id(user_id)
                
                anonymized_req = await self._anonymize_data(req)
                anonymized_res = await self._anonymize_data(res)
                
                req_json_encrypted = self.encrypter.encrypt(json.dumps(anonymized_req, default=safe_serialize).encode('utf-8')).decode('utf-8')
                res_json_encrypted = self.encrypter.encrypt(json.dumps(anonymized_res, default=safe_serialize).encode('utf-8')).decode('utf-8')
                
                now = datetime.utcnow().isoformat()
                
                await session.execute(
                    text("INSERT OR REPLACE INTO simulations (sim_id, request_data, result, status, updated_at, user_id) VALUES (:sim_id, :req, :res, :status, :u_at, :uid)"),
                    {"sim_id": sim_id, "req": req_json_encrypted, "res": res_json_encrypted, "status": status, "u_at": now, "uid": user_id}
                )
                await session.commit()
                
                await self._log_audit('save_simulation', sim_id, user_id or 'system', {'status': status})
            except Exception as e:
                DB_ERRORS.labels(operation='save_simulation').observe(time.time() - start_time)
                await session.rollback()
                await self.feedback_manager.record_feedback(
                    user_id=user_id or "system", feedback_type=FeedbackType.BUG_REPORT,
                    details={'type': 'db_error', 'operation': 'save_simulation', 'error': str(e)}
                )
                raise

    @circuit(failure_threshold=5, recovery_timeout=60)
    async def get_simulation(self, sim_id: str) -> Optional[Dict[str, Any]]:
        DB_OPERATIONS.labels(operation='get_simulation').inc()
        start_time = time.time()
        async with self.AsyncSessionLocal() as session:
            try:
                result = await session.execute(
                    text("SELECT sim_id, request_data, result, status, updated_at, user_id FROM simulations WHERE sim_id = :sim_id"),
                    {"sim_id": sim_id}
                )
                row = result.fetchone()
                if row:
                    request_data = {}
                    result_data = {}
                    try:
                        request_data = json.loads(self.encrypter.decrypt(row[1].encode('utf-8')).decode('utf-8'))
                        result_data = json.loads(self.encrypter.decrypt(row[2].encode('utf-8')).decode('utf-8'))
                    except Exception as e:
                        logger.error(f"Failed to decrypt simulation data for {sim_id}: {e}", exc_info=True)
                        
                    await self._log_audit('get_simulation', sim_id, row[5] or 'system', {'status': row[3]})
                    
                    return {
                        "sim_id": row[0], "request": request_data, "result": result_data,
                        "status": row[3], "updated_at": row[4], "user_id": row[5]
                    }
                return None
            except Exception as e:
                DB_ERRORS.labels(operation='get_simulation').observe(time.time() - start_time)
                await self.feedback_manager.record_feedback(
                    user_id="system", feedback_type=FeedbackType.BUG_REPORT,
                    details={'type': 'db_error', 'operation': 'get_simulation', 'error': str(e)}
                )
                raise

    @circuit(failure_threshold=5, recovery_timeout=60)
    @retry(tries=3, delay=1, backoff=2)
    async def save_agent_state(self, agent: Any):
        DB_OPERATIONS.labels(operation='save_agent_state').inc()
        start_time = time.time()
        async with self.AsyncSessionLocal() as session:
            try:
                allowed, reason = await self.policy_engine.should_auto_learn('Database', 'save_agent_state', agent.id, {'agent_id': agent.id})
                if not allowed:
                    raise ValueError(f"Policy denied: {reason}")
                
                agent_name_hashed = hashlib.sha256(agent.id.encode()).hexdigest()

                if settings.EXPERIMENTAL_FEATURES_ENABLED:
                    encrypted_inventory = self.encrypter.encrypt(json.dumps(agent.metadata.get('inventory', {}), default=safe_serialize).encode('utf-8')).decode('utf-8')
                    encrypted_language = self.encrypter.encrypt(json.dumps(agent.metadata.get('language', {}), default=safe_serialize).encode('utf-8')).decode('utf-8')
                    encrypted_memory = self.encrypter.encrypt(json.dumps(agent.metadata.get('memory', {}), default=safe_serialize).encode('utf-8')).decode('utf-8')
                    encrypted_personality = self.encrypter.encrypt(json.dumps(agent.metadata.get('personality', {}), default=safe_serialize).encode('utf-8')).decode('utf-8')
                    encrypted_custom_attributes = self.encrypter.encrypt(json.dumps(agent.metadata.get('custom_attributes', {}), default=safe_serialize).encode('utf-8')).decode('utf-8')
                    
                    state = AgentState(
                        name=agent_name_hashed, x=agent.metadata.get('x', 0), y=agent.metadata.get('y', 0),
                        energy=int(agent.energy), world_size=agent.metadata.get('world_size', 100),
                        agent_type=agent.metadata.get('agent_type', 'generic'),
                        inventory_v2=encrypted_inventory, language_v2=encrypted_language, memory_v2=encrypted_memory,
                        personality_v2=encrypted_personality, custom_attributes_v2=encrypted_custom_attributes
                    )
                else:
                    state = AgentState(
                        name=agent_name_hashed, x=agent.metadata.get('x', 0), y=agent.metadata.get('y', 0),
                        energy=int(agent.energy), world_size=agent.metadata.get('world_size', 100),
                        agent_type=agent.metadata.get('agent_type', 'generic'),
                        inventory=agent.metadata.get('inventory', {}), language=agent.metadata.get('language', {}),
                        memory=agent.metadata.get('memory', {}), personality=agent.metadata.get('personality', {}),
                        custom_attributes=agent.metadata.get('custom_attributes', {})
                    )

                await session.merge(state)
                await session.commit()
                
                await self.knowledge_graph.add_fact(
                    'AgentState', agent.id, {'type': state.agent_type, 'attributes': safe_serialize(agent.metadata.get('custom_attributes'))},
                    source='database', timestamp=datetime.utcnow().isoformat()
                )
                await self._log_audit('save_agent_state', agent.id, agent.id, {'name': state.name, 'type': state.agent_type})
            except Exception as e:
                DB_ERRORS.labels(operation='save_agent_state').observe(time.time() - start_time)
                await session.rollback()
                await self.feedback_manager.record_feedback(
                    user_id=agent.id, feedback_type=FeedbackType.BUG_REPORT,
                    details={'type': 'db_error', 'operation': 'save_agent_state', 'error': str(e)}
                )
                raise
    
    async def save_arbiter_state(self, agent_data):
        async with AsyncSession(self.engine) as session:
            state = AgentState(**agent_data)
            session.add(state)
            await session.commit()

    @circuit(failure_threshold=5, recovery_timeout=60)
    async def get_agent_state(self, agent_id: str) -> Optional[Dict[str, Any]]:
        DB_OPERATIONS.labels(operation='get_agent_state').inc()
        start_time = time.time()
        async with self.AsyncSessionLocal() as session:
            try:
                agent_name_hashed = hashlib.sha256(agent_id.encode()).hexdigest()
                result = await session.execute(select(AgentState).filter_by(name=agent_name_hashed))
                state = result.scalars().first()
                if state:
                    result_data = {
                        'id': agent_id,
                        'name': state.name,
                        'x': state.x,
                        'y': state.y,
                        'energy': state.energy,
                        'world_size': state.world_size,
                        'agent_type': state.agent_type,
                        'inventory': self._decrypt_json(state.inventory_v2, True) if settings.EXPERIMENTAL_FEATURES_ENABLED else state.inventory,
                        'language': self._decrypt_json(state.language_v2, True) if settings.EXPERIMENTAL_FEATURES_ENABLED else state.language,
                        'memory': self._decrypt_json(state.memory_v2, True) if settings.EXPERIMENTAL_FEATURES_ENABLED else state.memory,
                        'personality': self._decrypt_json(state.personality_v2, True) if settings.EXPERIMENTAL_FEATURES_ENABLED else state.personality,
                        'custom_attributes': self._decrypt_json(state.custom_attributes_v2, True) if settings.EXPERIMENTAL_FEATURES_ENABLED else state.custom_attributes,
                    }
                    await self._log_audit('get_agent_state', agent_id, agent_id, {'type': state.agent_type})
                    return result_data
                return None
            except Exception as e:
                DB_ERRORS.labels(operation='get_agent_state').observe(time.time() - start_time)
                await self.feedback_manager.record_feedback(
                    user_id=agent_id, feedback_type=FeedbackType.BUG_REPORT,
                    details={'type': 'db_error', 'operation': 'get_agent_state', 'error': str(e)}
                )
                raise

    @circuit(failure_threshold=5, recovery_timeout=60)
    async def query_agent_states(self, filters: Dict[str, Any] = None, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        DB_OPERATIONS.labels(operation='query_agent_states').inc()
        start_time = time.time()
        async with self.AsyncSessionLocal() as session:
            try:
                query = select(AgentState)
                if filters:
                    for key, value in filters.items():
                        if hasattr(AgentState, key):
                            query = query.filter(getattr(AgentState, key) == value)
                query = query.limit(limit).offset(offset)
                result = await session.execute(query)
                states = result.scalars().all()
                result_data = [
                    {
                        'id': state.name,
                        'x': state.x,
                        'y': state.y,
                        'energy': state.energy,
                        'world_size': state.world_size,
                        'agent_type': state.agent_type
                    }
                    for state in states
                ]
                await self._log_audit('query_agent_states', 'system', 'system', {'filters': filters, 'count': len(result_data)})
                return result_data
            except Exception as e:
                DB_ERRORS.labels(operation='query_agent_states').observe(time.time() - start_time)
                await self.feedback_manager.record_feedback(
                    user_id='system', feedback_type=FeedbackType.BUG_REPORT,
                    details={'type': 'db_error', 'operation': 'query_agent_states', 'error': str(e)}
                )
                raise

    @circuit(failure_threshold=5, recovery_timeout=60)
    @retry(tries=3, delay=1, backoff=2)
    async def save_audit_record(self, record: Dict[str, Any]):
        AUDIT_DB_OPERATIONS.labels(operation='save_audit_record').inc()
        start_time = time.time()
        
        async with self.AsyncSessionLocal() as session:
            try:
                record_data = record.copy()
                
                if settings.EXPERIMENTAL_FEATURES_ENABLED:
                    if 'agent_id' in record_data and record_data['agent_id']:
                        record_data['agent_id'] = hashlib.sha256(record_data['agent_id'].encode()).hexdigest()
                    if 'tenant_id' in record_data and record_data['tenant_id']:
                        record_data['tenant_id'] = hashlib.sha256(record_data['tenant_id'].encode()).hexdigest()

                def encrypt_field(field_name: str):
                    if field_name in record_data and record_data[field_name] is not None:
                        json_str = json.dumps(record_data[field_name], default=safe_serialize)
                        record_data[field_name] = self.encrypter.encrypt(json_str.encode('utf-8')).decode('utf-8')
                
                encrypt_field('detail')
                encrypt_field('context')
                encrypt_field('custom_attributes')
                encrypt_field('rationale')
                encrypt_field('simulation_outcomes')

                audit_record = ExplainAuditRecord(**record_data)
                
                session.add(audit_record)
                await session.commit()
                
                AUDIT_DB_OPERATIONS.labels(operation='save_audit_record_success').inc()
                AUDIT_DB_OPERATIONS.labels(operation='save_audit_record').observe(time.time() - start_time)
            except Exception as e:
                AUDIT_DB_ERRORS.labels(operation='save_audit_record').observe(time.time() - start_time)
                await session.rollback()
                await self.feedback_manager.record_feedback(
                    user_id="system", feedback_type=FeedbackType.BUG_REPORT,
                    details={'type': 'db_error', 'operation': 'save_audit_record', 'error': str(e)}
                )
                raise
    
    @circuit(failure_threshold=5, recovery_timeout=60)
    async def query_audit_records(self, filters: Optional[Dict[str, Any]] = None, use_dream_mode: bool = False) -> List[Dict]:
        AUDIT_DB_OPERATIONS.labels(operation='query_audit_records').inc()
        start_time = time.time()
        try:
            async with self.AsyncSessionLocal() as session:
                query = select(ExplainAuditRecord)
                
                if filters:
                    for key, value in filters.items():
                        if value is not None and hasattr(ExplainAuditRecord, key):
                            if key == 'ts_start':
                                query = query.filter(ExplainAuditRecord.ts >= value)
                            elif key == 'ts_end':
                                query = query.filter(ExplainAuditRecord.ts <= value)
                            else:
                                query = query.filter(getattr(ExplainAuditRecord, key) == value)
                
                result = await session.execute(query)
                records = result.scalars().all()
                # Serialize SQLAlchemy objects to dictionaries using helper function
                return [serialize_audit_record(r) for r in records]
        except Exception as e:
            AUDIT_DB_ERRORS.labels(operation='query_audit_records').observe(time.time() - start_time)
            await self.feedback_manager.record_feedback(
                user_id="system", feedback_type=FeedbackType.BUG_REPORT,
                details={'type': 'db_error', 'operation': 'query_audit_records', 'error': str(e)}
            )
            raise

    @circuit(failure_threshold=5, recovery_timeout=60)
    async def get_audit_snapshot(self, snapshot_id: str) -> Optional[Dict[str, Any]]:
        AUDIT_DB_OPERATIONS.labels(operation='get_audit_snapshot').inc()
        start_time = time.time()
        query = "SELECT state, user_id, timestamp FROM audit_snapshots WHERE snapshot_id = ?"
        try:
            if self.is_postgres:
                raise NotImplementedError("Audit snapshot retrieval not implemented for PostgreSQL directly.")
            async with self._get_aiosqlite_connection() as conn:
                cur = await conn.execute(query, (snapshot_id,))
                row = await cur.fetchone()

            if row:
                try:
                    decrypted_state_str = self.encrypter.decrypt(row["state"].encode('utf-8')).decode('utf-8')
                    state = json.loads(decrypted_state_str)
                    return {
                        "snapshot_id": snapshot_id,
                        "state": state,
                        "user_id": row["user_id"],
                        "timestamp": row["timestamp"]
                    }
                except (InvalidToken, json.JSONDecodeError) as e:
                    logger.error(f"Failed to decrypt or decode audit snapshot {snapshot_id}: {e}")
                    return None
            return None
        except Exception as e:
            AUDIT_DB_ERRORS.labels(operation='get_audit_snapshot').observe(time.time() - start_time)
            logger.error(f"Error retrieving audit snapshot {snapshot_id}: {e}", exc_info=True)
            raise

    @circuit(failure_threshold=5, recovery_timeout=60)
    @retry(tries=3, delay=1, backoff=2)
    async def snapshot_audit_state(self, snapshot_id: str, encrypted_state: str, user_id: str):
        AUDIT_DB_OPERATIONS.labels(operation='snapshot_audit_state').inc()
        start_time = time.time()
        user_id = validate_user_id(user_id)
        query = "INSERT OR REPLACE INTO audit_snapshots (snapshot_id, state, user_id, timestamp) VALUES (?, ?, ?, ?)"
        try:
            if self.is_postgres:
                raise NotImplementedError("Audit snapshot saving not implemented for PostgreSQL directly.")
            async with self._get_aiosqlite_connection() as conn:
                await conn.execute(query, (snapshot_id, encrypted_state, user_id, datetime.utcnow().isoformat()))
                await conn.commit()
            AUDIT_DB_OPERATIONS.labels(operation='snapshot_audit_state_success').inc()
        except Exception as e:
            AUDIT_DB_ERRORS.labels(operation='snapshot_audit_state').observe(time.time() - start_time)
            logger.error(f"Error saving audit snapshot {snapshot_id}: {e}", exc_info=True)
            await self.feedback_manager.record_feedback(
                user_id="system", feedback_type=FeedbackType.BUG_REPORT,
                details={'type': 'db_error', 'operation': 'snapshot_audit_state', 'error': str(e)}
            )
            raise

    @circuit(failure_threshold=5, recovery_timeout=60)
    @retry(tries=3, delay=1, backoff=2)
    async def snapshot_world_state(self, user_id: str) -> str:
        DB_OPERATIONS.labels(operation='snapshot_world_state').inc()
        start_time = time.time()
        user_id = validate_user_id(user_id)
        snapshot_id = str(uuid.uuid4())
        try:
            async with self.AsyncSessionLocal() as session:
                agent_states = await session.execute(select(AgentState))
                states_list = [state.__dict__ for state in agent_states.scalars().all()]
            
            anonymized_states = [await self._anonymize_data(state) for state in states_list]
            json_str = json.dumps(anonymized_states, default=safe_serialize)
            encrypted_state = self.encrypter.encrypt(json_str.encode('utf-8')).decode('utf-8')

            if self.is_postgres:
                raise NotImplementedError("World snapshot saving not implemented for PostgreSQL directly.")
            query = "INSERT INTO world_snapshots (snapshot_id, state, user_id, timestamp) VALUES (?, ?, ?, ?)"
            async with self._get_aiosqlite_connection() as conn:
                await conn.execute(query, (snapshot_id, encrypted_state, user_id, datetime.utcnow().isoformat()))
                await conn.commit()
            
            await self._log_audit('snapshot_world_state', snapshot_id, user_id, {'agent_count': len(states_list)})
            return snapshot_id
        except Exception as e:
            DB_ERRORS.labels(operation='snapshot_world_state').observe(time.time() - start_time)
            logger.error(f"Error creating world state snapshot: {e}", exc_info=True)
            await self.feedback_manager.record_feedback(
                user_id="system", feedback_type=FeedbackType.BUG_REPORT,
                details={'type': 'db_error', 'operation': 'snapshot_world_state', 'error': str(e)}
            )
            raise

    @circuit(failure_threshold=5, recovery_timeout=60)
    @retry(tries=3, delay=1, backoff=2)
    async def restore_world_state(self, snapshot_id: str, user_id: str):
        DB_OPERATIONS.labels(operation='restore_world_state').inc()
        start_time = time.time()
        user_id = validate_user_id(user_id)
        try:
            if self.is_postgres:
                raise NotImplementedError("World snapshot restoration not implemented for PostgreSQL directly.")
            query = "SELECT state FROM world_snapshots WHERE snapshot_id = ?"
            async with self._get_aiosqlite_connection() as conn:
                cur = await conn.execute(query, (snapshot_id,))
                row = await cur.fetchone()
            
            if not row:
                raise ValueError(f"World snapshot '{snapshot_id}' not found.")
            
            decrypted_state_str = self.encrypter.decrypt(row["state"].encode('utf-8')).decode('utf-8')
            states_list = json.loads(decrypted_state_str)
            
            async with self.AsyncSessionLocal() as session:
                await session.execute(delete(AgentState))
                for state_data in states_list:
                    state_data['name'] = hashlib.sha256(state_data['id'].encode()).hexdigest()
                    del state_data['id']
                    state = AgentState(**state_data)
                    session.add(state)
                await session.commit()
            
            await self._log_audit('restore_world_state', snapshot_id, user_id, {'agent_count': len(states_list)})
        except Exception as e:
            DB_ERRORS.labels(operation='restore_world_state').observe(time.time() - start_time)
            logger.error(f"Error restoring world state snapshot {snapshot_id}: {e}", exc_info=True)
            await self.feedback_manager.record_feedback(
                user_id="system", feedback_type=FeedbackType.BUG_REPORT,
                details={'type': 'db_error', 'operation': 'restore_world_state', 'error': str(e)}
            )
            raise

    async def migrate_to_citus(self):
        """Migrates schema to Citus by adding distribution keys."""
        async with self.AsyncSessionLocal() as session:
            try:
                await session.execute(text("CREATE EXTENSION IF NOT EXISTS citus;"))
                await session.commit()
                logger.info("Citus extension ensured.")
            except sqlalchemy.exc.SQLAlchemyError as e:
                logger.error(f"Failed to ensure Citus extension: {e}", exc_info=True)
                await session.rollback()
                raise
            
            try:
                # This assumes 'agent_state' and 'explain_audit' are the table names
                # and 'name' and 'uuid' are the column names.
                await session.execute(text("SELECT create_distributed_table('agent_state', 'name');"))
                await session.execute(text("SELECT create_distributed_table('explain_audit', 'uuid');"))
                await session.commit()
                logger.info("Migrated to Citus with distribution keys.")
            except sqlalchemy.exc.SQLAlchemyError as e:
                logger.error(f"Failed to create distributed tables for Citus: {e}", exc_info=True)
                await session.rollback()
                raise

    async def rotate_keys(self, new_key: bytes):
        """
        Rotate encryption keys by prepending new key and re-encrypting existing data.
        
        Args:
            new_key: The new encryption key as bytes
        
        Note: This method temporarily switches from EnterpriseSecurityUtils to FernetEncryption
        for key rotation operations. Both provide compatible encrypt/decrypt interfaces.
        Thread-safety: This operation should be performed during maintenance windows or with
        application-level coordination to prevent concurrent encryption operations.
        """
        # Validate input
        if not isinstance(new_key, bytes):
            raise TypeError("new_key must be bytes")
        
        # TODO: Add proper locking mechanism for production use
        logger.warning("Key rotation modifies global settings without locking. Ensure no concurrent operations.")
        
        old_encrypter = self.encrypter
        
        try:
            new_key_str = new_key.decode('utf-8')
        except UnicodeDecodeError as e:
            raise ValueError(f"new_key must contain valid UTF-8 bytes: {e}")
        
        all_keys = [new_key_str] + settings.FERNET_KEYS.get_secret_value().split(',')
        
        # Update settings (Note: This modifies global state without proper locking)
        settings.FERNET_KEYS = SecretStr(','.join(all_keys))

        # Temporarily switch to FernetEncryption for multi-key support during rotation
        self.encrypter = FernetEncryption([k.encode('utf-8') for k in all_keys])
        
        logger.info("Starting key rotation and re-encryption of existing data...")
        
        async with self.AsyncSessionLocal() as session:
            try:
                # Re-encrypt AgentState records
                results = await session.execute(select(AgentState))
                agents = results.scalars().all()
                for agent in agents:
                    if agent.inventory_v2:
                        try:
                            decrypted = old_encrypter.decrypt(agent.inventory_v2.encode('utf-8'))
                            agent.inventory_v2 = self.encrypter.encrypt(decrypted).decode('utf-8')
                        except InvalidToken:
                            logger.error(f"Failed to decrypt inventory for agent {agent.name}. Skipping re-encryption.")
                            
                    if agent.language_v2:
                        try:
                            decrypted = old_encrypter.decrypt(agent.language_v2.encode('utf-8'))
                            agent.language_v2 = self.encrypter.encrypt(decrypted).decode('utf-8')
                        except InvalidToken:
                            logger.error(f"Failed to decrypt language for agent {agent.name}. Skipping re-encryption.")
                            
                    if agent.memory_v2:
                        try:
                            decrypted = old_encrypter.decrypt(agent.memory_v2.encode('utf-8'))
                            agent.memory_v2 = self.encrypter.encrypt(decrypted).decode('utf-8')
                        except InvalidToken:
                            logger.error(f"Failed to decrypt memory for agent {agent.name}. Skipping re-encryption.")

                    if agent.personality_v2:
                        try:
                            decrypted = old_encrypter.decrypt(agent.personality_v2.encode('utf-8'))
                            agent.personality_v2 = self.encrypter.encrypt(decrypted).decode('utf-8')
                        except InvalidToken:
                            logger.error(f"Failed to decrypt personality for agent {agent.name}. Skipping re-encryption.")

                    if agent.custom_attributes_v2:
                        try:
                            decrypted = old_encrypter.decrypt(agent.custom_attributes_v2.encode('utf-8'))
                            agent.custom_attributes_v2 = self.encrypter.encrypt(decrypted).decode('utf-8')
                        except InvalidToken:
                            logger.error(f"Failed to decrypt custom attributes for agent {agent.name}. Skipping re-encryption.")
                            
                await session.commit()
                logger.info(f"Re-encrypted {len(agents)} AgentState records with the new key.")
                
            except Exception as e:
                logger.error(f"Error during key rotation re-encryption: {e}", exc_info=True)
                await session.rollback()
                raise
        
        logger.info("Key rotation complete.")

    async def save_generator_state(self, agent_id: str, data: Dict[str, Any]):
        """
        Save state for a generator agent.
        
        Uses module-level constants for default values: DEFAULT_AGENT_X, DEFAULT_AGENT_Y,
        DEFAULT_AGENT_ENERGY, and DEFAULT_AGENT_WORLD_SIZE.
        """
        async with self.AsyncSessionLocal() as session:
            stmt = insert(GeneratorAgentState).values(
                id=agent_id, name="generator", 
                x=DEFAULT_AGENT_X, y=DEFAULT_AGENT_Y, 
                energy=DEFAULT_AGENT_ENERGY, world_size=DEFAULT_AGENT_WORLD_SIZE, 
                agent_type="generator",
                generated_code=data.get("code"), test_results=data.get("tests"),
                deployment_config=data.get("deployment"), docs=data.get("docs")
            )
            await session.execute(stmt)
            await session.commit()
    
    async def save_sfe_state(self, agent_id: str, data: Dict[str, Any]):
        """
        Save state for a self-fixing engineer agent.
        
        Uses module-level constants for default values: DEFAULT_AGENT_X, DEFAULT_AGENT_Y,
        DEFAULT_AGENT_ENERGY, and DEFAULT_AGENT_WORLD_SIZE.
        """
        async with self.AsyncSessionLocal() as session:
            stmt = insert(SFEAgentState).values(
                id=agent_id, name="sfe", 
                x=DEFAULT_AGENT_X, y=DEFAULT_AGENT_Y, 
                energy=DEFAULT_AGENT_ENERGY, world_size=DEFAULT_AGENT_WORLD_SIZE, 
                agent_type="sfe",
                fixed_code=data.get("fixed_code"), analysis_report=data.get("analysis"),
                trust_score=data.get("trust_score")
            )
            await session.execute(stmt)
            await session.commit()