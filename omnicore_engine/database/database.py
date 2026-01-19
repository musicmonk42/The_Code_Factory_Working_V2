# File: omnicore_engine/database.py
from __future__ import annotations

import asyncio
import base64
import collections.abc
import hashlib
import json
import logging
import logging.handlers
import re
import shutil
import sqlite3
import time
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Union

import aiosqlite
import numpy as np
import sqlalchemy
from circuitbreaker import circuit
from cryptography.fernet import Fernet, InvalidToken
from pydantic import SecretStr
from sqlalchemy import delete, insert, select, text, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from omnicore_engine.retry_compat import retry

logger = logging.getLogger(__name__)

# Corrected imports using the new arbiter package and centralized settings
import types


def _create_fallback_settings():
    """Create a minimal settings object for when ArbiterConfig is unavailable."""
    return types.SimpleNamespace(
        log_level="INFO",
        LOG_LEVEL="INFO",
        database_path="sqlite:///./omnicore.db",
        DB_PATH="sqlite:///./omnicore.db",
        plugin_dir="./plugins",
        PLUGIN_DIR="./plugins",
        ENCRYPTION_KEY=None,
        ENCRYPTION_KEY_BYTES=b"",
        DB_POOL_SIZE=50,
        DB_POOL_MAX_OVERFLOW=20,
        DB_RETRY_ATTEMPTS=3,
        DB_RETRY_DELAY=1.0,
        DB_CIRCUIT_THRESHOLD=3,
        DB_CIRCUIT_TIMEOUT=60,
        DB_BATCH_SIZE=100,
    )


def _get_settings():
    """Lazy import + defensive instantiation of settings."""
    ArbiterConfig = None
    try:
        # Try the full canonical path first (preferred)
        from self_fixing_engineer.arbiter.config import ArbiterConfig
    except ImportError:
        try:
            # Fall back to aliased path for backward compatibility
            from arbiter.config import ArbiterConfig
        except ImportError:
            pass
    
    if ArbiterConfig is None:
        logging.debug(
            "arbiter.config not available; using fallback settings."
        )
        return _create_fallback_settings()

    try:
        return ArbiterConfig()
    except Exception as e:
        logging.warning(
            "ArbiterConfig() raised during instantiation; falling back to minimal settings. Error: %s",
            e,
        )
        return _create_fallback_settings()


from omnicore_engine.message_bus.encryption import FernetEncryption

from .metrics_helpers import get_or_create_counter_local, get_or_create_histogram_local

# Local imports from the refactored structure
from .models import (
    AgentState,
    Base,
    ExplainAuditRecord,
    GeneratorAgentState,
    SFEAgentState,
)

# --- optional feedback manager dependency -----------------------------------
_FeedbackManagerClass = None
try:
    from omnicore_engine.feedback_manager import (
        FeedbackManager as _FeedbackManagerClass,
    )
    from omnicore_engine.feedback_manager import FeedbackType
except ImportError:
    _FeedbackManagerClass = None

# If omnicore_engine.feedback_manager not found, provide a compatible mock
# Note: arbiter.feedback.FeedbackManager has different interface (add_feedback vs record_feedback)
if _FeedbackManagerClass is None:
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

else:
    FeedbackManager = _FeedbackManagerClass


settings = _get_settings()

try:
    from arbiter.policy.core import PolicyEngine
except ImportError:
    logger.warning("PolicyEngine module not found. Policy checks will be unavailable.")

    class PolicyEngine:
        def __init__(self, *args, **kwargs):
            pass

        async def should_auto_learn(self, *args, **kwargs):
            return True, "Mock Policy: Always allowed"


try:
    from self_fixing_engineer.arbiter.knowledge_graph.core import KnowledgeGraph
except ImportError:
    try:
        # Fall back to aliased path for backward compatibility
        from arbiter.knowledge_graph.core import KnowledgeGraph
    except ImportError:
        logger.debug(
            "KnowledgeGraph module not found; KnowledgeGraph features unavailable."
        )

        class KnowledgeGraph:
            def __init__(self, *args, **kwargs):
                pass

            async def add_fact(self, *args, **kwargs):
                logger.debug("Mock KnowledgeGraph: add_fact called.")


from omnicore_engine.metrics import (
    AUDIT_DB_ERRORS,
    AUDIT_DB_OPERATIONS,
    DB_ERRORS,
    DB_OPERATIONS,
)

# Local metrics for merged functionalities
DB_OPERATIONS_LOCAL = get_or_create_counter_local(
    "db_operations_total_local", "Total database operations (local)", ["operation"]
)
DB_ERRORS_LOCAL = get_or_create_counter_local(
    "db_errors_total_local", "Total database errors (local)", ["operation"]
)
DB_LATENCY_LOCAL = get_or_create_histogram_local(
    "db_operation_latency_seconds_local",
    "Database operation latency (local)",
    ["operation"],
)

# Context manager for aiosqlite
from contextlib import asynccontextmanager

# Import plugin_registry to avoid circular dependency in Database class
try:
    from omnicore_engine import plugin_registry
except ImportError:
    logger.warning(
        "Plugin registry not available. Database will operate without plugin-related features."
    )
    plugin_registry = None

from omnicore_engine.security_config import get_security_config

# New imports for EnterpriseSecurityUtils
from omnicore_engine.security_utils import EnterpriseSecurityUtils


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
        return base64.b64encode(obj).decode("utf-8")
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, (set, frozenset)):
        return list(obj)
    if isinstance(obj, collections.abc.Mapping):
        return {k: safe_serialize(v, _seen) for k, v in obj.items()}
    if isinstance(obj, collections.abc.Iterable) and not isinstance(obj, str):
        return [safe_serialize(item, _seen) for item in obj]
    if isinstance(obj, object) and hasattr(obj, "__dict__"):
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
    if not re.match(r"^[a-zA-Z0-9_-]{1,255}$", user_id):
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
        "root_merkle_hash": record.root_merkle_hash,
    }


# Default values for agent state initialization
# These can be overridden via configuration if needed in the future
DEFAULT_AGENT_X = 0
DEFAULT_AGENT_Y = 0
DEFAULT_AGENT_ENERGY = 100
DEFAULT_AGENT_WORLD_SIZE = 100

# Whitelist of allowed filter fields for query_agent_states to prevent SQL injection
ALLOWED_FILTER_FIELDS = {"agent_type", "world_size", "energy", "x", "y"}

# Whitelist of allowed filter fields for query_audit_records
ALLOWED_AUDIT_FILTER_FIELDS = {
    "kind",
    "name",
    "sim_id",
    "agent_id",
    "tenant_id",
    "ts_start",
    "ts_end",
}


class DecryptionError(Exception):
    """Raised when decryption fails, indicating data corruption or invalid key."""

    pass


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
        # EnterpriseSecurityUtils uses keyword-only args with defaults
        self.security_utils = EnterpriseSecurityUtils()

        # Ensure async driver is used for SQLite
        if db_path.startswith("sqlite:///") and not db_path.startswith(
            "sqlite+aiosqlite://"
        ):
            db_path = db_path.replace("sqlite:///", "sqlite+aiosqlite:///")
            logger.info("Converted SQLite URL to use aiosqlite async driver")

        self.db_path = db_path

        db_echo = settings.LOG_LEVEL.upper() == "DEBUG"

        engine_params = {
            "echo": db_echo,
            "pool_pre_ping": True,
            "json_serializer": lambda obj: json.dumps(obj, default=safe_serialize),
            "future": True,  # Use future=True for SQLAlchemy 2.0 style
        }

        # Set connect_args conditionally per database type (Issue #7 fix)
        if self.db_path.startswith("postgresql://"):
            engine_params["connect_args"] = {
                "timeout": 30,
                "options": "-c statement_timeout=30000",
            }
            self.engine: AsyncEngine = create_async_engine(
                self.db_path, **engine_params
            )
            self.is_postgres = True
            logger.info(
                f"Database initialized with PostgreSQL engine: {self.db_path.split('@')[-1]}"
            )
        elif self.db_path.startswith("sqlite+aiosqlite://"):
            sqlite_db_file = self.db_path.replace("sqlite+aiosqlite:///", "")
            db_dir = Path(sqlite_db_file).parent
            try:
                db_dir.mkdir(parents=True, exist_ok=True)
                logger.info(f"Ensured database file directory exists: {db_dir}")
            except Exception as e:
                logger.critical(
                    f"Failed to create database file directory {db_dir}: {e}"
                )
                raise RuntimeError(
                    f"Failed to create database directory {db_dir}: {e}"
                ) from e

            engine_params["connect_args"] = {
                "timeout": 30,
                "check_same_thread": False,
            }
            self.engine: AsyncEngine = create_async_engine(
                self.db_path, **engine_params
            )
            self.is_postgres = False
            logger.info(
                f"Database initialized with SQLite (aiosqlite) engine: {self.db_path}"
            )
        elif self.db_path.startswith("sqlite:///"):
            sqlite_db_file = self.db_path.replace("sqlite:///", "")
            db_dir = Path(sqlite_db_file).parent
            try:
                db_dir.mkdir(parents=True, exist_ok=True)
                logger.info(f"Ensured database file directory exists: {db_dir}")
            except Exception as e:
                logger.critical(
                    f"Failed to create database file directory {db_dir}: {e}"
                )
                raise RuntimeError(
                    f"Failed to create database directory {db_dir}: {e}"
                ) from e
            engine_params["connect_args"] = {
                "timeout": 30,
                "check_same_thread": False,
            }
            self.engine: AsyncEngine = create_async_engine(
                self.db_path, **engine_params
            )
            self.is_postgres = False
            logger.info(f"Database initialized with SQLite engine: {self.db_path}")
        else:
            # Fallback for other database types, or if no specific prefix is given
            engine_params["pool_size"] = settings.DB_POOL_SIZE
            engine_params["max_overflow"] = settings.DB_POOL_MAX_OVERFLOW
            engine_params["connect_args"] = {}
            self.engine: AsyncEngine = create_async_engine(
                self.db_path, **engine_params
            )
            self.is_postgres = False
            logger.info(f"Database initialized with generic engine: {self.db_path}")

        self.AsyncSessionLocal = async_sessionmaker(
            bind=self.engine, class_=AsyncSession, expire_on_commit=False
        )

        # Replace existing Fernet initialization with enterprise encryption
        self.encrypter = self.security_utils  # Use security_utils encryption methods

        # Initialize FeedbackManager if available
        if FeedbackManager is not None:
            try:
                self.feedback_manager = FeedbackManager(config=settings)
            except Exception as e:
                logger.warning(
                    f"Failed to initialize FeedbackManager in Database: {e}. "
                    f"Feedback features will be unavailable."
                )
                self.feedback_manager = None
        else:
            self.feedback_manager = None
            logger.warning("FeedbackManager not available for Database.")

        # Initialize PolicyEngine if available
        if PolicyEngine is not None:
            try:
                # Get the config settings for PolicyEngine
                config = _get_settings()
                # PolicyEngine expects arbiter_instance and config parameters
                # Try to initialize, but handle gracefully if config type is wrong
                self.policy_engine = PolicyEngine(arbiter_instance=None, config=config)
            except (TypeError, ValueError, AttributeError) as e:
                # Config type mismatch or initialization error - create mock
                logger.warning(f"Failed to initialize PolicyEngine: {e}. Using mock.")
                self.policy_engine = self._create_mock_policy_engine()
            except Exception as e:
                logger.warning(f"Failed to initialize PolicyEngine: {e}. Using mock.")
                self.policy_engine = self._create_mock_policy_engine()
        else:
            self.policy_engine = None

        # Initialize KnowledgeGraph if available
        if KnowledgeGraph is not None:
            try:
                self.knowledge_graph = KnowledgeGraph()
            except Exception as e:
                logger.warning(f"Failed to initialize KnowledgeGraph: {e}")
                self.knowledge_graph = None
        else:
            self.knowledge_graph = None

        self.plugin_registry = (
            plugin_registry.PLUGIN_REGISTRY if plugin_registry else None
        )

        self.retry_attempts = settings.DB_RETRY_ATTEMPTS
        self.retry_delay = settings.DB_RETRY_DELAY
        self.circuit_threshold = settings.DB_CIRCUIT_THRESHOLD
        self.circuit_timeout = settings.DB_CIRCUIT_TIMEOUT

        self.system_audit_merkle_tree = system_audit_merkle_tree

        logger.info(
            f"Database initialized with async engine. Pool size: {getattr(settings, 'DB_POOL_SIZE', 'N/A')}, max overflow: {getattr(settings, 'DB_POOL_MAX_OVERFLOW', 'N/A')}"
        )

        if self.db_path.startswith("sqlite:///"):
            self.sqlite_db_file_path = Path(self.db_path.replace("sqlite:///", ""))
        elif self.db_path.startswith("sqlite+aiosqlite:///"):
            self.sqlite_db_file_path = Path(
                self.db_path.replace("sqlite+aiosqlite:///", "")
            )
        else:
            self.sqlite_db_file_path = None

        if self.sqlite_db_file_path and self.sqlite_db_file_path.parent:
            try:
                self.sqlite_db_file_path.parent.mkdir(parents=True, exist_ok=True)
                logger.info(
                    f"Ensured database file directory exists (for local SQLite): {self.sqlite_db_file_path.parent}"
                )
            except Exception as e:
                logger.critical(
                    f"Failed to create database file directory (for local SQLite) {self.sqlite_db_file_path.parent}: {e}"
                )
                raise RuntimeError(f"Failed to create database directory: {e}") from e

        base_data_dir = (
            self.sqlite_db_file_path.parent
            if self.sqlite_db_file_path
            else Path("./data")
        )
        if not base_data_dir.as_posix():
            base_data_dir = Path("./data")

        max_backups_val = getattr(settings, "MAX_BACKUPS", 10)

        self.CONFIG = {
            "db_dir": base_data_dir,
            "db_file": self.sqlite_db_file_path,
            "backup_dir": base_data_dir / "backups",
            "encryption_key": settings.ENCRYPTION_KEY.get_secret_value(),
            "max_backups": int(max_backups_val),
            "connection_pool_size": int(getattr(settings, "DB_POOL_SIZE", 5)),
        }

        # Validate encryption key (Issue #16 fix)
        encryption_key = settings.ENCRYPTION_KEY.get_secret_value()
        if not validate_fernet_key(
            encryption_key.encode()
            if isinstance(encryption_key, str)
            else encryption_key
        ):
            logger.warning("ENCRYPTION_KEY is not a valid Fernet key format")

        try:
            self.CONFIG["backup_dir"].mkdir(parents=True, exist_ok=True)
            logger.info(f"Ensured backup directory exists: {self.CONFIG['backup_dir']}")
        except Exception as e:
            logger.critical(
                f"Failed to create backup directory {self.CONFIG['backup_dir']}: {e}"
            )
            raise RuntimeError(f"Failed to create backup directory: {e}") from e

        self._serializers: Dict[type, Callable[[Any], Any]] = {}

        # Lock for key rotation to prevent race conditions (Issue #8 fix)
        self._rotation_lock = asyncio.Lock()

    async def initialize(self) -> None:
        """
        Initialize the database by creating tables and running migrations.

        This method delegates to create_tables() to avoid code duplication (Issue #17 fix).
        """
        try:
            logger.info("Database component: Starting async initialization...")

            if not self.is_postgres:
                await self._initialize_legacy_tables_async()

            await self.create_tables()

            if self.is_postgres:
                await self.migrate_to_citus()
                logger.info(
                    "For PostgreSQL, ensure data migration from SQLite (if any) is handled externally."
                )

            logger.info(
                "Database component: Async initialization completed successfully."
            )
        except sqlalchemy.exc.SQLAlchemyError as e:
            logger.critical(
                f"Database initialization failed due to SQLAlchemyError: {e}",
                exc_info=True,
            )
            raise
        except Exception as e:
            logger.critical(
                f"Database initialization failed due to unexpected error: {e}",
                exc_info=True,
            )
            raise

    def _create_mock_policy_engine(self):
        """
        Create a mock policy engine that always allows operations.
        
        WARNING: This is a fallback for development/testing only.
        In production, ensure PolicyEngine is properly initialized with ArbiterConfig.
        Mock usage is logged for security audit purposes.
        """
        logger.warning(
            "MockPolicyEngine is in use. All policy checks will be bypassed. "
            "This is acceptable for development/testing but should be avoided in production. "
            "Ensure ARBITER configuration is properly set in production environments."
        )
        
        class MockPolicyEngine:
            async def should_auto_learn(self, *args, **kwargs):
                # Log each call for audit purposes
                logger.debug(
                    f"MockPolicyEngine: Allowing operation. Args: {args[0:2] if args else 'none'}"
                )
                return True, "Mock Policy: Always allowed (development/testing mode)"
        
        return MockPolicyEngine()

    async def create_tables(self):
        DB_OPERATIONS.labels(operation="create_tables").inc()
        try:
            # Run DDL operations first (Issue #9 fix - run migrations separately)
            async with self.engine.begin() as conn:
                # Use checkfirst=True to avoid "already exists" errors
                await conn.run_sync(
                    lambda sync_conn: Base.metadata.create_all(
                        sync_conn, checkfirst=True
                    )
                )

            # Run migrations separately outside DDL transaction
            try:
                from alembic import command, config
                from alembic.util.exc import CommandError

                alembic_cfg = config.Config()
                Path(__file__).parent
                project_root = Path(__file__).parent.parent
                migrations_path = project_root / "migrations"
                
                # Check if migrations directory exists before attempting to run migrations
                if not migrations_path.exists():
                    logger.warning(
                        f"Migrations directory not found at {migrations_path}. "
                        "Skipping Alembic migrations. Tables will be created from models."
                    )
                else:
                    alembic_cfg.set_main_option(
                        "script_location", str(migrations_path)
                    )
                    alembic_cfg.set_main_option("sqlalchemy.url", self.db_path)
                    command.upgrade(alembic_cfg, "head")
                    logger.info("Schema migrations applied successfully (via Alembic).")
            except ImportError:
                logger.warning("Alembic is not installed. Skipping schema migrations.")
            except CommandError as e:
                # CommandError includes issues like missing migrations directory
                logger.warning(
                    f"Alembic CommandError encountered: {e}. "
                    "Skipping migrations. Tables will be created from models."
                )
            except Exception as e:
                # Log other migration errors as warnings but don't fail startup
                logger.warning(
                    f"Failed to apply migrations: {e}. "
                    "Continuing with table creation from models.",
                    exc_info=True
                )

            logger.info("Database tables ensured (created/verified asynchronously).")
        except sqlalchemy.exc.SQLAlchemyError as e:
            # Fix Issue #1: Use .inc() for Counter instead of .observe()
            DB_ERRORS.labels(operation="create_tables").inc()
            await self.feedback_manager.record_feedback(
                user_id="system",
                feedback_type=FeedbackType.BUG_REPORT,
                details={
                    "type": "db_error",
                    "operation": "create_tables",
                    "error": str(e),
                },
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
                    raise ValueError(
                        "SQLite database path is not set for legacy table initialization."
                    )

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
                logger.info(
                    "Legacy/non-ORM tables ensured (preferences, simulations, plugins, feedback, audit_snapshots, world_snapshots) asynchronously."
                )
                DB_OPERATIONS_LOCAL.labels(
                    operation="initialize_legacy_tables_async"
                ).inc()
            except Exception as e:
                DB_ERRORS_LOCAL.labels(operation="initialize_legacy_tables_async").inc()
                logger.error(
                    f"Failed to create legacy/non-ORM tables asynchronously: {e}",
                    exc_info=True,
                )
                raise RuntimeError(f"Failed to create legacy tables: {e}") from e

    def register_serializer(
        self, type_: type, serializer: Callable[[Any], Any]
    ) -> None:
        logger.warning(
            f"Serializer registration for {type_} is not directly supported by current safe_serialize implementation."
        )

    def safe_serialize_wrapper(self, obj: Any, _seen: Optional[Set[int]] = None) -> Any:
        return safe_serialize(obj, _seen)

    @staticmethod
    def safe_encode(value: Union[str, bytes]) -> bytes:
        """
        Safely encode a value to bytes.
        
        Industry-standard type-safe encoding that handles both str and bytes inputs.
        
        Args:
            value: String or bytes to encode
            
        Returns:
            bytes: Encoded value
        """
        if isinstance(value, bytes):
            return value
        return value.encode('utf-8')

    @staticmethod
    def safe_decode(value: Union[str, bytes]) -> str:
        """
        Safely decode a value to string.
        
        Industry-standard type-safe decoding that handles both str and bytes inputs.
        
        Args:
            value: String or bytes to decode
            
        Returns:
            str: Decoded string
        """
        if isinstance(value, str):
            return value
        return value.decode('utf-8')

    def _validate_json(self, data: Any, encrypt: bool = False) -> str:
        try:
            serialized_data = self.safe_serialize_wrapper(data)
            json_str = json.dumps(serialized_data)
            if encrypt:
                # encrypt() already returns a string, no need to decode
                json_str = self.encrypter.encrypt(json_str.encode())
            return json_str
        except (TypeError, ValueError) as e:
            logger.error(f"Failed to serialize data to JSON: {e}", exc_info=True)
            raise ValueError(f"Data is not JSON-serializable: {e}")

    def _decrypt_json(self, data: Union[str, bytes], encrypted: bool) -> Any:
        """
        Decrypt and deserialize JSON data.

        Args:
            data: Encrypted or plain JSON data
            encrypted: Whether the data is encrypted

        Returns:
            Deserialized data

        Raises:
            DecryptionError: If decryption or deserialization fails (Issue #9 fix)
        """
        try:
            if encrypted:
                if isinstance(data, str):
                    data_bytes = data.encode("utf-8")
                else:
                    data_bytes = data
                return json.loads(self.encrypter.decrypt(data_bytes).decode())
            if isinstance(data, bytes):
                return json.loads(data.decode("utf-8"))
            return json.loads(data)
        except InvalidToken as e:
            logger.error(f"Failed to decrypt data: {e}", exc_info=True)
            raise DecryptionError(f"Failed to decrypt data: {e}") from e
        except json.JSONDecodeError as e:
            logger.error(f"Failed to deserialize JSON data: {e}", exc_info=True)
            raise DecryptionError(f"Invalid JSON after decryption: {e}") from e
        except Exception as e:
            logger.error(f"Unexpected error during decryption: {e}", exc_info=True)
            raise DecryptionError(f"Unexpected decryption error: {e}") from e

    @asynccontextmanager
    async def _get_aiosqlite_connection(self):
        """Provides an aiosqlite connection, only for SQLite databases."""
        if self.is_postgres or self.sqlite_db_file_path is None:
            raise RuntimeError(
                "Attempted to get aiosqlite connection for a non-SQLite or non-file-based database."
            )

        conn = None
        try:
            conn = await aiosqlite.connect(self.sqlite_db_file_path)
            conn.row_factory = sqlite3.Row
            yield conn
        except Exception as e:
            logger.error(
                f"Failed to get aiosqlite connection to {self.CONFIG['db_file']}: {e}",
                exc_info=True,
            )
            DB_ERRORS_LOCAL.labels(operation="aiosqlite_connect").inc()
            raise
        finally:
            if conn:
                await conn.close()

    async def get_feedback_entries(self, query=None) -> List[Dict]:
        DB_OPERATIONS_LOCAL.labels(operation="get_feedback_entries").inc()

        try:
            if self.is_postgres:
                # PostgreSQL implementation
                async with self._get_asyncpg_connection() as conn:
                    sql = "SELECT id, type, message, timestamp FROM feedback"
                    params = []
                    if query and "type" in query:
                        sql += " WHERE type = $1"
                        params.append(query["type"])
                    rows = await conn.fetch(sql, *params)
                    return [dict(row) for row in rows]
            else:
                # SQLite implementation
                query_str = "SELECT id, type, message, timestamp FROM feedback"
                params = []
                if query and "type" in query:
                    query_str += " WHERE type = ?"
                    params.append(query["type"])
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

        # Validate and sanitize input
        feedback_type = entry.get("type", "")
        message = entry.get("message", "")
        timestamp = entry.get("timestamp", datetime.now().isoformat())

        if not feedback_type or not message:
            raise ValueError("Feedback entry must contain 'type' and 'message' fields")

        try:
            if self.is_postgres:
                # PostgreSQL implementation with proper parameter binding
                async with self._get_asyncpg_connection() as conn:
                    await conn.execute(
                        """
                        INSERT INTO feedback (type, message, timestamp) 
                        VALUES ($1, $2, $3)
                        """,
                        feedback_type,
                        message,
                        timestamp,
                    )
                    logger.debug(
                        f"Feedback entry saved to PostgreSQL: type={feedback_type}"
                    )
            else:
                # SQLite implementation
                async with self._get_aiosqlite_connection() as conn:
                    await conn.execute(
                        "INSERT INTO feedback (type, message, timestamp) VALUES (?, ?, ?)",
                        (feedback_type, message, timestamp),
                    )
                    await conn.commit()
                    logger.debug(
                        f"Feedback entry saved to SQLite: type={feedback_type}"
                    )
        except Exception as e:
            DB_ERRORS_LOCAL.labels(operation="save_feedback_entry").inc()
            logger.error(f"Error saving feedback entry: {e}", exc_info=True)
            raise

    @DB_LATENCY_LOCAL.labels(operation="get_preferences").time()
    async def get_preferences(
        self, user_id: str, decrypt: bool = False
    ) -> Optional[Dict]:
        DB_OPERATIONS_LOCAL.labels(operation="get_preferences").inc()

        # Validate and sanitize user_id
        user_id = validate_user_id(user_id)

        try:
            if self.is_postgres:
                # PostgreSQL implementation with proper parameter binding
                async with self._get_asyncpg_connection() as conn:
                    row = await conn.fetchrow(
                        """
                        SELECT data, encrypted 
                        FROM preferences 
                        WHERE user_id = $1 AND deleted = 0
                        """,
                        user_id,
                    )
                    if row:
                        return self._decrypt_json(
                            row["data"], row["encrypted"] and decrypt
                        )
                    return None
            else:
                # SQLite implementation
                query = "SELECT data, encrypted FROM preferences WHERE user_id = ? AND deleted=0"
                async with self._get_aiosqlite_connection() as conn:
                    cur = await conn.execute(query, (user_id,))
                    row = await cur.fetchone()
                if row:
                    return self._decrypt_json(row["data"], row["encrypted"] and decrypt)
                return None
        except Exception as e:
            DB_ERRORS_LOCAL.labels(operation="get_preferences").inc()
            logger.error(
                f"Error retrieving preferences for {user_id}: {e}", exc_info=True
            )
            return None

    @DB_LATENCY_LOCAL.labels(operation="save_preferences").time()
    async def save_preferences(
        self, user_id: str, prefs: Dict, encrypt: bool = False
    ) -> None:
        DB_OPERATIONS_LOCAL.labels(operation="save_preferences").inc()

        # Validate and sanitize inputs
        user_id = validate_user_id(user_id)
        data = self._validate_json(prefs, encrypt)

        try:
            if self.is_postgres:
                # PostgreSQL implementation with proper UPSERT
                async with self._get_asyncpg_connection() as conn:
                    await conn.execute(
                        """
                        INSERT INTO preferences (user_id, data, encrypted, deleted, updated_at)
                        VALUES ($1, $2, $3, 0, CURRENT_TIMESTAMP)
                        ON CONFLICT(user_id) DO UPDATE SET 
                            data = EXCLUDED.data,
                            encrypted = EXCLUDED.encrypted,
                            deleted = 0,
                            updated_at = CURRENT_TIMESTAMP
                        """,
                        user_id,
                        data,
                        encrypt,
                    )
                    logger.debug(f"Preferences saved to PostgreSQL for user: {user_id}")
            else:
                # SQLite implementation
                query = """
                    INSERT INTO preferences (user_id, data, encrypted, deleted, updated_at)
                    VALUES (?, ?, ?, 0, CURRENT_TIMESTAMP)
                    ON CONFLICT(user_id) DO UPDATE SET data=excluded.data, encrypted=excluded.encrypted,
                    deleted=0, updated_at=CURRENT_TIMESTAMP
                """
                async with self._get_aiosqlite_connection() as conn:
                    await conn.execute(query, (user_id, data, encrypt))
                    await conn.commit()
                    logger.debug(f"Preferences saved to SQLite for user: {user_id}")
        except Exception as e:
            DB_ERRORS_LOCAL.labels(operation="save_preferences").inc()
            logger.error(f"Error saving preferences for {user_id}: {e}", exc_info=True)
            raise

    @DB_LATENCY_LOCAL.labels(operation="save_simulation_legacy").time()
    async def save_simulation_legacy(
        self,
        sim_id: str,
        request_data: Dict,
        result: Dict,
        status: str,
        user_id: Optional[str] = None,
        encrypt: bool = False,
    ) -> None:
        """
        Save a simulation record to the database (legacy table format).

        Args:
            sim_id: Unique simulation identifier
            request_data: Request data dictionary
            result: Result data dictionary
            status: Simulation status
            user_id: Optional user identifier
            encrypt: Whether to encrypt the JSON data

        Raises:
            ValueError: If data validation fails
        """
        DB_OPERATIONS_LOCAL.labels(operation="save_simulation_legacy").inc()
        if user_id:
            user_id = validate_user_id(user_id)
        request_data_json = self._validate_json(request_data, encrypt)
        result_json = self._validate_json(result, encrypt)

        try:
            if self.is_postgres:
                # PostgreSQL implementation with UPSERT using ON CONFLICT
                query = """
                    INSERT INTO simulations (sim_id, user_id, request_data, result, status, encrypted, deleted, updated_at)
                    VALUES ($1, $2, $3, $4, $5, $6, 0, NOW())
                    ON CONFLICT (sim_id) 
                    DO UPDATE SET
                        user_id = EXCLUDED.user_id,
                        request_data = EXCLUDED.request_data,
                        result = EXCLUDED.result,
                        status = EXCLUDED.status,
                        encrypted = EXCLUDED.encrypted,
                        updated_at = NOW()
                """
                async with self.AsyncSessionLocal() as session:
                    await session.execute(
                        text(query),
                        {
                            "sim_id": sim_id,
                            "user_id": user_id,
                            "request_data": request_data_json,
                            "result": result_json,
                            "status": status,
                            "encrypted": encrypt,
                        },
                    )
                    await session.commit()
            else:
                # SQLite implementation
                query = """
                    INSERT OR REPLACE INTO simulations (sim_id, user_id, request_data, result, status, encrypted, deleted, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, 0, CURRENT_TIMESTAMP)
                """
                async with self._get_aiosqlite_connection() as conn:
                    await conn.execute(
                        query,
                        (
                            sim_id,
                            user_id,
                            request_data_json,
                            result_json,
                            status,
                            int(encrypt),
                        ),
                    )
                    await conn.commit()
        except Exception as e:
            DB_ERRORS_LOCAL.labels(operation="save_simulation_legacy").inc()
            logger.error(f"Error saving legacy simulation {sim_id}: {e}", exc_info=True)
            raise

    @DB_LATENCY_LOCAL.labels(operation="get_simulation_legacy").time()
    async def get_simulation_legacy(
        self, sim_id: str, decrypt: bool = False
    ) -> Optional[Dict]:
        """
        Retrieve a simulation record from the database (legacy table format).

        Args:
            sim_id: Simulation identifier
            decrypt: Whether to decrypt encrypted JSON data

        Returns:
            Dictionary with simulation data or None if not found
        """
        DB_OPERATIONS_LOCAL.labels(operation="get_simulation_legacy").inc()

        try:
            if self.is_postgres:
                # PostgreSQL implementation
                query = """
                    SELECT sim_id, user_id, request_data, result, status, encrypted, updated_at 
                    FROM simulations 
                    WHERE sim_id = $1 AND deleted = 0
                """
                async with self.AsyncSessionLocal() as session:
                    result = await session.execute(text(query), {"sim_id": sim_id})
                    row = result.fetchone()

                if row:
                    return {
                        "sim_id": row[0],
                        "user_id": row[1],
                        "request_data": self._decrypt_json(row[2], row[5] and decrypt),
                        "result": self._decrypt_json(row[3], row[5] and decrypt),
                        "status": row[4],
                        "updated_at": row[6],
                    }
                return None
            else:
                # SQLite implementation
                query = "SELECT sim_id, user_id, request_data, result, status, encrypted, updated_at FROM simulations WHERE sim_id = ? AND deleted=0"
                async with self._get_aiosqlite_connection() as conn:
                    cur = await conn.execute(query, (sim_id,))
                    row = await cur.fetchone()

                if row:
                    return {
                        "sim_id": row["sim_id"],
                        "user_id": row["user_id"],
                        "request_data": self._decrypt_json(
                            row["request_data"], row["encrypted"] and decrypt
                        ),
                        "result": self._decrypt_json(
                            row["result"], row["encrypted"] and decrypt
                        ),
                        "status": row["status"],
                        "updated_at": row["updated_at"],
                    }
                return None
        except Exception as e:
            DB_ERRORS_LOCAL.labels(operation="get_simulation_legacy").inc()
            logger.error(
                f"Error retrieving legacy simulation {sim_id}: {e}", exc_info=True
            )
            return None

    @DB_LATENCY_LOCAL.labels(operation="delete_simulation_legacy").time()
    async def delete_simulation_legacy(self, sim_id: str) -> None:
        """
        Soft delete a simulation record (legacy table format).

        Args:
            sim_id: Simulation identifier

        Raises:
            Exception: If database operation fails
        """
        DB_OPERATIONS_LOCAL.labels(operation="delete_simulation_legacy").inc()

        try:
            if self.is_postgres:
                # PostgreSQL implementation
                query = """
                    UPDATE simulations 
                    SET deleted = 1, updated_at = NOW() 
                    WHERE sim_id = $1
                """
                async with self.AsyncSessionLocal() as session:
                    await session.execute(text(query), {"sim_id": sim_id})
                    await session.commit()
            else:
                # SQLite implementation
                query = "UPDATE simulations SET deleted=1, updated_at=CURRENT_TIMESTAMP WHERE sim_id = ?"
                async with self._get_aiosqlite_connection() as conn:
                    await conn.execute(query, (sim_id,))
                    await conn.commit()
        except Exception as e:
            DB_ERRORS_LOCAL.labels(operation="delete_simulation_legacy").inc()
            logger.error(
                f"Error deleting legacy simulation {sim_id}: {e}", exc_info=True
            )
            raise

    @DB_LATENCY_LOCAL.labels(operation="save_plugin_legacy").time()
    async def save_plugin_legacy(
        self, plugin_meta: Dict, encrypt: bool = False
    ) -> None:
        """
        Save a plugin metadata record to the database (legacy table format).

        Args:
            plugin_meta: Plugin metadata dictionary with 'kind' and 'name' required
            encrypt: Whether to encrypt the metadata JSON

        Raises:
            ValueError: If kind or name is missing
        """
        DB_OPERATIONS_LOCAL.labels(operation="save_plugin_legacy").inc()
        kind = plugin_meta.get("kind")
        name = plugin_meta.get("name")
        if not kind or not name:
            raise ValueError("Plugin kind and name are required")
        meta_json = self._validate_json(plugin_meta, encrypt)

        try:
            if self.is_postgres:
                # PostgreSQL implementation with UPSERT using ON CONFLICT
                query = """
                    INSERT INTO plugins (kind, name, meta, encrypted, deleted, updated_at)
                    VALUES ($1, $2, $3, $4, 0, NOW())
                    ON CONFLICT (kind, name) 
                    DO UPDATE SET
                        meta = EXCLUDED.meta,
                        encrypted = EXCLUDED.encrypted,
                        updated_at = NOW()
                """
                async with self.AsyncSessionLocal() as session:
                    await session.execute(
                        text(query),
                        {
                            "kind": kind,
                            "name": name,
                            "meta": meta_json,
                            "encrypted": encrypt,
                        },
                    )
                    await session.commit()
            else:
                # SQLite implementation
                query = """
                    INSERT OR REPLACE INTO plugins (kind, name, meta, encrypted, deleted, updated_at)
                    VALUES (?, ?, ?, ?, 0, CURRENT_TIMESTAMP)
                """
                async with self._get_aiosqlite_connection() as conn:
                    await conn.execute(query, (kind, name, meta_json, int(encrypt)))
                    await conn.commit()
        except Exception as e:
            DB_ERRORS_LOCAL.labels(operation="save_plugin_legacy").inc()
            logger.error(
                f"Error saving legacy plugin {kind}/{name}: {e}", exc_info=True
            )
            raise

    @DB_LATENCY_LOCAL.labels(operation="get_plugin_legacy").time()
    async def get_plugin_legacy(
        self, kind: str, name: str, decrypt: bool = False
    ) -> Optional[Dict]:
        """
        Retrieve a legacy plugin by kind and name.

        Industry-standard implementation with:
        - PostgreSQL and SQLite support
        - Optional decryption of plugin metadata
        - Soft delete filtering (deleted=0)

        Args:
            kind: Plugin kind/type
            name: Plugin name
            decrypt: Whether to decrypt encrypted metadata

        Returns:
            Plugin metadata dictionary or None if not found
        """
        DB_OPERATIONS_LOCAL.labels(operation="get_plugin_legacy").inc()

        try:
            if self.is_postgres:
                # PostgreSQL implementation
                async with self.AsyncSessionLocal() as session:
                    query = text("""
                        SELECT meta, encrypted FROM plugins 
                        WHERE kind = :kind AND name = :name AND deleted = 0
                    """)
                    result = await session.execute(query, {"kind": kind, "name": name})
                    row = result.fetchone()
            else:
                # SQLite implementation
                query = "SELECT meta, encrypted FROM plugins WHERE kind = ? AND name = ? AND deleted=0"
                async with self._get_aiosqlite_connection() as conn:
                    cur = await conn.execute(query, (kind, name))
                    row = await cur.fetchone()

            if row:
                meta = row["meta"] if isinstance(row, dict) else row[0]
                encrypted = row["encrypted"] if isinstance(row, dict) else row[1]
                return self._decrypt_json(meta, encrypted and decrypt)
            return None

        except Exception as e:
            DB_ERRORS_LOCAL.labels(operation="get_plugin_legacy").inc()
            logger.error(
                f"Error retrieving legacy plugin {kind}/{name}: {e}", exc_info=True
            )
            return None

    @DB_LATENCY_LOCAL.labels(operation="list_plugins_legacy").time()
    async def list_plugins_legacy(self, decrypt: bool = False) -> List[Dict]:
        """
        List all non-deleted legacy plugins.

        Industry-standard implementation with:
        - PostgreSQL and SQLite support
        - Optional bulk decryption
        - Soft delete filtering
        - Performance timing metrics

        Args:
            decrypt: Whether to decrypt encrypted metadata

        Returns:
            List of plugin metadata dictionaries
        """
        DB_OPERATIONS_LOCAL.labels(operation="list_plugins_legacy").inc()

        try:
            if self.is_postgres:
                # PostgreSQL implementation
                async with self.AsyncSessionLocal() as session:
                    query = text("""
                        SELECT kind, name, meta, encrypted FROM plugins 
                        WHERE deleted = 0
                    """)
                    result = await session.execute(query)
                    rows = result.fetchall()
            else:
                # SQLite implementation
                query = (
                    "SELECT kind, name, meta, encrypted FROM plugins WHERE deleted=0"
                )
                async with self._get_aiosqlite_connection() as conn:
                    cur = await conn.execute(query)
                    rows = await cur.fetchall()

            # Process and return results
            return [
                {
                    "kind": row["kind"] if isinstance(row, dict) else row[0],
                    "name": row["name"] if isinstance(row, dict) else row[1],
                    "meta": self._decrypt_json(
                        row["meta"] if isinstance(row, dict) else row[2],
                        (row["encrypted"] if isinstance(row, dict) else row[3])
                        and decrypt,
                    ),
                }
                for row in rows
            ]

        except Exception as e:
            DB_ERRORS_LOCAL.labels(operation="list_plugins_legacy").inc()
            logger.error(f"Error listing legacy plugins: {e}", exc_info=True)
            return []

    @DB_LATENCY_LOCAL.labels(operation="delete_plugin_legacy").time()
    async def delete_plugin_legacy(self, kind: str, name: str) -> None:
        """
        Soft delete a legacy plugin.

        Industry-standard implementation with:
        - PostgreSQL and SQLite support
        - Soft delete (sets deleted=1)
        - Automatic timestamp update
        - Performance timing metrics

        Args:
            kind: Plugin kind/type
            name: Plugin name

        Raises:
            Exception: If deletion fails
        """
        DB_OPERATIONS_LOCAL.labels(operation="delete_plugin_legacy").inc()

        try:
            if self.is_postgres:
                # PostgreSQL implementation
                async with self.AsyncSessionLocal() as session:
                    query = text("""
                        UPDATE plugins 
                        SET deleted = 1, updated_at = CURRENT_TIMESTAMP 
                        WHERE kind = :kind AND name = :name
                    """)
                    await session.execute(query, {"kind": kind, "name": name})
                    await session.commit()
            else:
                # SQLite implementation
                query = "UPDATE plugins SET deleted=1, updated_at=CURRENT_TIMESTAMP WHERE kind = ? AND name = ?"
                async with self._get_aiosqlite_connection() as conn:
                    await conn.execute(query, (kind, name))
                    await conn.commit()

        except Exception as e:
            DB_ERRORS_LOCAL.labels(operation="delete_plugin_legacy").inc()
            logger.error(
                f"Error deleting legacy plugin {kind}/{name}: {e}", exc_info=True
            )
            raise

    def backup(self, max_backups: int = None) -> None:
        if max_backups is None:
            max_backups = self.CONFIG["max_backups"]
        with DB_LATENCY_LOCAL.labels(operation="backup").time():
            try:
                if self.is_postgres or self.sqlite_db_file_path is None:
                    logger.warning(
                        "Database backup not applicable for PostgreSQL or non-file-based databases."
                    )
                    return

                timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
                backup_path = self.CONFIG["backup_dir"] / f"backup_{timestamp}.db"
                shutil.copy2(self.CONFIG["db_file"], backup_path)
                logger.info(f"Database backed up to {backup_path}")
                backups = sorted(
                    self.CONFIG["backup_dir"].glob("backup_*.db"),
                    key=lambda x: x.stat().st_mtime,
                )
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
                    logger.info(
                        "Database integrity check (PRAGMA) not applicable for PostgreSQL."
                    )
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
        for key in ["user_id", "name", "agent_id"]:
            if key in anonymized_data and isinstance(anonymized_data[key], str):
                anonymized_data[key] = hashlib.sha256(
                    anonymized_data[key].encode()
                ).hexdigest()
        return anonymized_data

    async def _log_audit(
        self, event: str, sim_id: str, user_id: str, details: Dict[str, Any]
    ):
        logger.info(
            f"Database logging audit event: {event} for {user_id} (sim_id: {sim_id})"
        )

    @circuit(failure_threshold=5, recovery_timeout=60)
    @retry(tries=3, delay=1, backoff=2)
    async def save_simulation(
        self,
        sim_id: str,
        req: Dict,
        res: Dict,
        status: str,
        user_id: Optional[str] = None,
    ):
        DB_OPERATIONS.labels(operation="save_simulation").inc()

        async with self.AsyncSessionLocal() as session:
            try:
                allowed, reason = await self.policy_engine.should_auto_learn(
                    "Database",
                    "save_simulation",
                    user_id or "system",
                    {"sim_id": sim_id, "status": status},
                )
                if not allowed:
                    raise ValueError(f"Policy denied: {reason}")

                if user_id:
                    user_id = validate_user_id(user_id)

                anonymized_req = await self._anonymize_data(req)
                anonymized_res = await self._anonymize_data(res)

                # encrypt() already returns a string, no need to decode
                req_json_encrypted = self.encrypter.encrypt(
                    json.dumps(anonymized_req, default=safe_serialize).encode("utf-8")
                )
                res_json_encrypted = self.encrypter.encrypt(
                    json.dumps(anonymized_res, default=safe_serialize).encode("utf-8")
                )

                now = datetime.utcnow().isoformat()

                await session.execute(
                    text(
                        "INSERT OR REPLACE INTO simulations (sim_id, request_data, result, status, updated_at, user_id) VALUES (:sim_id, :req, :res, :status, :u_at, :uid)"
                    ),
                    {
                        "sim_id": sim_id,
                        "req": req_json_encrypted,
                        "res": res_json_encrypted,
                        "status": status,
                        "u_at": now,
                        "uid": user_id,
                    },
                )
                await session.commit()

                await self._log_audit(
                    "save_simulation", sim_id, user_id or "system", {"status": status}
                )
            except Exception as e:
                DB_ERRORS.labels(operation="save_simulation").inc()
                await session.rollback()
                await self.feedback_manager.record_feedback(
                    user_id=user_id or "system",
                    feedback_type=FeedbackType.BUG_REPORT,
                    details={
                        "type": "db_error",
                        "operation": "save_simulation",
                        "error": str(e),
                    },
                )
                raise

    @circuit(failure_threshold=5, recovery_timeout=60)
    async def get_simulation(self, sim_id: str) -> Optional[Dict[str, Any]]:
        DB_OPERATIONS.labels(operation="get_simulation").inc()
        async with self.AsyncSessionLocal() as session:
            try:
                result = await session.execute(
                    text(
                        "SELECT sim_id, request_data, result, status, updated_at, user_id FROM simulations WHERE sim_id = :sim_id"
                    ),
                    {"sim_id": sim_id},
                )
                row = result.fetchone()
                if row:
                    request_data = {}
                    result_data = {}
                    try:
                        request_data = json.loads(
                            self.encrypter.decrypt(row[1].encode("utf-8")).decode(
                                "utf-8"
                            )
                        )
                        result_data = json.loads(
                            self.encrypter.decrypt(row[2].encode("utf-8")).decode(
                                "utf-8"
                            )
                        )
                    except Exception as e:
                        logger.error(
                            f"Failed to decrypt simulation data for {sim_id}: {e}",
                            exc_info=True,
                        )

                    await self._log_audit(
                        "get_simulation", sim_id, row[5] or "system", {"status": row[3]}
                    )

                    return {
                        "sim_id": row[0],
                        "request": request_data,
                        "result": result_data,
                        "status": row[3],
                        "updated_at": row[4],
                        "user_id": row[5],
                    }
                return None
            except Exception as e:
                DB_ERRORS.labels(operation="get_simulation").inc()
                await self.feedback_manager.record_feedback(
                    user_id="system",
                    feedback_type=FeedbackType.BUG_REPORT,
                    details={
                        "type": "db_error",
                        "operation": "get_simulation",
                        "error": str(e),
                    },
                )
                raise

    @circuit(failure_threshold=5, recovery_timeout=60)
    @retry(tries=3, delay=1, backoff=2)
    async def save_agent_state(self, agent: Any):
        DB_OPERATIONS.labels(operation="save_agent_state").inc()
        async with self.AsyncSessionLocal() as session:
            try:
                allowed, reason = await self.policy_engine.should_auto_learn(
                    "Database", "save_agent_state", agent.id, {"agent_id": agent.id}
                )
                if not allowed:
                    raise ValueError(f"Policy denied: {reason}")

                agent_name_hashed = hashlib.sha256(agent.id.encode()).hexdigest()

                if getattr(settings, "EXPERIMENTAL_FEATURES_ENABLED", False):
                    # encrypt() already returns a string, no need to decode
                    encrypted_inventory = self.encrypter.encrypt(
                        json.dumps(
                            agent.metadata.get("inventory", {}), default=safe_serialize
                        ).encode("utf-8")
                    )
                    encrypted_language = self.encrypter.encrypt(
                        json.dumps(
                            agent.metadata.get("language", {}), default=safe_serialize
                        ).encode("utf-8")
                    )
                    encrypted_memory = self.encrypter.encrypt(
                        json.dumps(
                            agent.metadata.get("memory", {}), default=safe_serialize
                        ).encode("utf-8")
                    )
                    encrypted_personality = self.encrypter.encrypt(
                        json.dumps(
                            agent.metadata.get("personality", {}),
                            default=safe_serialize,
                        ).encode("utf-8")
                    )
                    encrypted_custom_attributes = self.encrypter.encrypt(
                        json.dumps(
                            agent.metadata.get("custom_attributes", {}),
                            default=safe_serialize,
                        ).encode("utf-8")
                    )

                    state = AgentState(
                        name=agent_name_hashed,
                        x=agent.metadata.get("x", 0),
                        y=agent.metadata.get("y", 0),
                        energy=int(agent.energy),
                        world_size=agent.metadata.get("world_size", 100),
                        agent_type=agent.metadata.get("agent_type", "generic"),
                        inventory_v2=encrypted_inventory,
                        language_v2=encrypted_language,
                        memory_v2=encrypted_memory,
                        personality_v2=encrypted_personality,
                        custom_attributes_v2=encrypted_custom_attributes,
                    )
                else:
                    state = AgentState(
                        name=agent_name_hashed,
                        x=agent.metadata.get("x", 0),
                        y=agent.metadata.get("y", 0),
                        energy=int(agent.energy),
                        world_size=agent.metadata.get("world_size", 100),
                        agent_type=agent.metadata.get("agent_type", "generic"),
                        inventory=agent.metadata.get("inventory", {}),
                        language=agent.metadata.get("language", {}),
                        memory=agent.metadata.get("memory", {}),
                        personality=agent.metadata.get("personality", {}),
                        custom_attributes=agent.metadata.get("custom_attributes", {}),
                    )

                await session.merge(state)
                await session.commit()

                await self.knowledge_graph.add_fact(
                    "AgentState",
                    agent.id,
                    {
                        "type": state.agent_type,
                        "attributes": safe_serialize(
                            agent.metadata.get("custom_attributes")
                        ),
                    },
                    source="database",
                    timestamp=datetime.utcnow().isoformat(),
                )
                await self._log_audit(
                    "save_agent_state",
                    agent.id,
                    agent.id,
                    {"name": state.name, "type": state.agent_type},
                )
            except Exception as e:
                DB_ERRORS.labels(operation="save_agent_state").inc()
                await session.rollback()
                await self.feedback_manager.record_feedback(
                    user_id=agent.id,
                    feedback_type=FeedbackType.BUG_REPORT,
                    details={
                        "type": "db_error",
                        "operation": "save_agent_state",
                        "error": str(e),
                    },
                )
                raise

    async def save_arbiter_state(self, agent_data):
        async with AsyncSession(self.engine) as session:
            state = AgentState(**agent_data)
            session.add(state)
            await session.commit()

    @circuit(failure_threshold=5, recovery_timeout=60)
    async def get_agent_state(self, agent_id: str) -> Optional[Dict[str, Any]]:
        DB_OPERATIONS.labels(operation="get_agent_state").inc()
        start_time = time.time()
        async with self.AsyncSessionLocal() as session:
            try:
                agent_name_hashed = hashlib.sha256(agent_id.encode()).hexdigest()
                result = await session.execute(
                    select(AgentState).filter_by(name=agent_name_hashed)
                )
                state = result.scalars().first()
                if state:
                    result_data = {
                        "id": agent_id,
                        "name": state.name,
                        "x": state.x,
                        "y": state.y,
                        "energy": state.energy,
                        "world_size": state.world_size,
                        "agent_type": state.agent_type,
                        "inventory": (
                            self._decrypt_json(state.inventory_v2, True)
                            if getattr(settings, "EXPERIMENTAL_FEATURES_ENABLED", False)
                            else state.inventory
                        ),
                        "language": (
                            self._decrypt_json(state.language_v2, True)
                            if getattr(settings, "EXPERIMENTAL_FEATURES_ENABLED", False)
                            else state.language
                        ),
                        "memory": (
                            self._decrypt_json(state.memory_v2, True)
                            if getattr(settings, "EXPERIMENTAL_FEATURES_ENABLED", False)
                            else state.memory
                        ),
                        "personality": (
                            self._decrypt_json(state.personality_v2, True)
                            if getattr(settings, "EXPERIMENTAL_FEATURES_ENABLED", False)
                            else state.personality
                        ),
                        "custom_attributes": (
                            self._decrypt_json(state.custom_attributes_v2, True)
                            if getattr(settings, "EXPERIMENTAL_FEATURES_ENABLED", False)
                            else state.custom_attributes
                        ),
                    }
                    await self._log_audit(
                        "get_agent_state",
                        agent_id,
                        agent_id,
                        {"type": state.agent_type},
                    )
                    return result_data
                return None
            except Exception as e:
                # Track error count
                DB_ERRORS.labels(operation="get_agent_state").inc()
                # Track error timing (operation duration before failure)
                error_duration = time.time() - start_time
                DB_LATENCY_LOCAL.labels(operation="get_agent_state_error").observe(
                    error_duration
                )
                await self.feedback_manager.record_feedback(
                    user_id=agent_id,
                    feedback_type=FeedbackType.BUG_REPORT,
                    details={
                        "type": "db_error",
                        "operation": "get_agent_state",
                        "error": str(e),
                        "duration_seconds": error_duration,
                    },
                )
                raise

    @circuit(failure_threshold=5, recovery_timeout=60)
    async def query_agent_states(
        self, filters: Dict[str, Any] = None, limit: int = 100, offset: int = 0
    ) -> List[Dict[str, Any]]:
        DB_OPERATIONS.labels(operation="query_agent_states").inc()
        async with self.AsyncSessionLocal() as session:
            try:
                query = select(AgentState)
                if filters:
                    # Issue #6 fix: Whitelist allowed filter fields to prevent SQL injection
                    for key, value in filters.items():
                        if key in ALLOWED_FILTER_FIELDS and hasattr(AgentState, key):
                            query = query.filter(getattr(AgentState, key) == value)
                        elif key not in ALLOWED_FILTER_FIELDS:
                            logger.warning(f"Ignoring unsupported filter field: {key}")
                query = query.limit(limit).offset(offset)
                result = await session.execute(query)
                states = result.scalars().all()
                result_data = [
                    {
                        "id": state.name,
                        "x": state.x,
                        "y": state.y,
                        "energy": state.energy,
                        "world_size": state.world_size,
                        "agent_type": state.agent_type,
                    }
                    for state in states
                ]
                await self._log_audit(
                    "query_agent_states",
                    "system",
                    "system",
                    {"filters": filters, "count": len(result_data)},
                )
                return result_data
            except Exception as e:
                DB_ERRORS.labels(operation="query_agent_states").inc()
                await self.feedback_manager.record_feedback(
                    user_id="system",
                    feedback_type=FeedbackType.BUG_REPORT,
                    details={
                        "type": "db_error",
                        "operation": "query_agent_states",
                        "error": str(e),
                    },
                )
                raise

    @circuit(failure_threshold=5, recovery_timeout=60)
    @retry(tries=3, delay=1, backoff=2)
    async def save_audit_record(self, record: Dict[str, Any]):
        AUDIT_DB_OPERATIONS.labels(operation="save_audit_record").inc()

        async with self.AsyncSessionLocal() as session:
            try:
                record_data = record.copy()

                if getattr(settings, "EXPERIMENTAL_FEATURES_ENABLED", False):
                    if "agent_id" in record_data and record_data["agent_id"]:
                        record_data["agent_id"] = hashlib.sha256(
                            record_data["agent_id"].encode()
                        ).hexdigest()
                    if "tenant_id" in record_data and record_data["tenant_id"]:
                        record_data["tenant_id"] = hashlib.sha256(
                            record_data["tenant_id"].encode()
                        ).hexdigest()

                def encrypt_field(field_name: str):
                    if (
                        field_name in record_data
                        and record_data[field_name] is not None
                    ):
                        json_str = json.dumps(
                            record_data[field_name], default=safe_serialize
                        )
                        # encrypt() already returns a string, no need to decode
                        record_data[field_name] = self.encrypter.encrypt(
                            json_str.encode("utf-8")
                        )

                encrypt_field("detail")
                encrypt_field("context")
                encrypt_field("custom_attributes")
                encrypt_field("rationale")
                encrypt_field("simulation_outcomes")

                audit_record = ExplainAuditRecord(**record_data)

                session.add(audit_record)
                await session.commit()

                AUDIT_DB_OPERATIONS.labels(operation="save_audit_record_success").inc()
            except Exception as e:
                AUDIT_DB_ERRORS.labels(operation="save_audit_record").inc()
                await session.rollback()
                await self.feedback_manager.record_feedback(
                    user_id="system",
                    feedback_type=FeedbackType.BUG_REPORT,
                    details={
                        "type": "db_error",
                        "operation": "save_audit_record",
                        "error": str(e),
                    },
                )
                raise

    @circuit(failure_threshold=5, recovery_timeout=60)
    async def query_audit_records(
        self,
        filters: Optional[Dict[str, Any]] = None,
        use_dream_mode: bool = False,
        decrypt: bool = False,
    ) -> List[Dict]:
        """
        Query audit records with optional filtering and decryption.

        Args:
            filters: Optional dictionary of field-value pairs to filter records
            use_dream_mode: Reserved for future use
            decrypt: If True, decrypt encrypted fields in the results (Issue #20 fix)

        Returns:
            List of audit record dictionaries
        """
        AUDIT_DB_OPERATIONS.labels(operation="query_audit_records").inc()
        try:
            async with self.AsyncSessionLocal() as session:
                query = select(ExplainAuditRecord)

                if filters:
                    # Whitelist filter fields for audit records
                    for key, value in filters.items():
                        if value is not None:
                            if key == "ts_start":
                                query = query.filter(ExplainAuditRecord.ts >= value)
                            elif key == "ts_end":
                                query = query.filter(ExplainAuditRecord.ts <= value)
                            elif key in ALLOWED_AUDIT_FILTER_FIELDS and hasattr(
                                ExplainAuditRecord, key
                            ):
                                query = query.filter(
                                    getattr(ExplainAuditRecord, key) == value
                                )
                            else:
                                logger.warning(
                                    f"Ignoring unsupported audit filter field: {key}"
                                )

                result = await session.execute(query)
                records = result.scalars().all()

                # Serialize SQLAlchemy objects to dictionaries
                serialized_records = [serialize_audit_record(r) for r in records]

                # Issue #20 fix: Decrypt sensitive fields if requested
                if decrypt and getattr(settings, "EXPERIMENTAL_FEATURES_ENABLED", False):
                    decrypted_records = []
                    for record_dict in serialized_records:
                        for field in [
                            "detail",
                            "context",
                            "custom_attributes",
                            "rationale",
                            "simulation_outcomes",
                        ]:
                            if record_dict.get(field):
                                try:
                                    record_dict[field] = self._decrypt_json(
                                        record_dict[field], encrypted=True
                                    )
                                except DecryptionError as e:
                                    logger.warning(f"Failed to decrypt {field}: {e}")
                                    # Keep original encrypted value
                        decrypted_records.append(record_dict)
                    return decrypted_records

                return serialized_records
        except Exception as e:
            AUDIT_DB_ERRORS.labels(operation="query_audit_records").inc()
            await self.feedback_manager.record_feedback(
                user_id="system",
                feedback_type=FeedbackType.BUG_REPORT,
                details={
                    "type": "db_error",
                    "operation": "query_audit_records",
                    "error": str(e),
                },
            )
            raise

    @circuit(failure_threshold=5, recovery_timeout=60)
    async def get_audit_snapshot(self, snapshot_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve an audit snapshot by ID.

        Industry-standard implementation with:
        - PostgreSQL and SQLite support
        - Encrypted state storage
        - Circuit breaker pattern for resilience
        - Comprehensive error handling

        Args:
            snapshot_id: Unique identifier for the snapshot

        Returns:
            Dictionary containing snapshot data or None if not found
        """
        AUDIT_DB_OPERATIONS.labels(operation="get_audit_snapshot").inc()
        try:
            if self.is_postgres:
                # PostgreSQL implementation using async session
                async with self.AsyncSessionLocal() as session:
                    query = text(
                        "SELECT state, user_id, timestamp FROM audit_snapshots "
                        "WHERE snapshot_id = :snapshot_id"
                    )
                    result = await session.execute(query, {"snapshot_id": snapshot_id})
                    row = result.fetchone()
            else:
                # SQLite implementation
                query = "SELECT state, user_id, timestamp FROM audit_snapshots WHERE snapshot_id = ?"
                async with self._get_aiosqlite_connection() as conn:
                    cur = await conn.execute(query, (snapshot_id,))
                    row = await cur.fetchone()

            if row:
                try:
                    # Decrypt the encrypted state
                    decrypted_state_str = self.encrypter.decrypt(
                        row["state"].encode("utf-8")
                        if isinstance(row["state"], str)
                        else row[0].encode("utf-8")
                    ).decode("utf-8")
                    state = json.loads(decrypted_state_str)

                    # Return structured snapshot data
                    return {
                        "snapshot_id": snapshot_id,
                        "state": state,
                        "user_id": row["user_id"] if isinstance(row, dict) else row[1],
                        "timestamp": (
                            row["timestamp"] if isinstance(row, dict) else row[2]
                        ),
                    }
                except (InvalidToken, json.JSONDecodeError) as e:
                    logger.error(
                        f"Failed to decrypt or decode audit snapshot {snapshot_id}: {e}"
                    )
                    return None
            return None
        except Exception as e:
            AUDIT_DB_ERRORS.labels(operation="get_audit_snapshot").inc()
            logger.error(
                f"Error retrieving audit snapshot {snapshot_id}: {e}", exc_info=True
            )
            raise

    @circuit(failure_threshold=5, recovery_timeout=60)
    @retry(tries=3, delay=1, backoff=2)
    async def snapshot_audit_state(
        self, snapshot_id: str, encrypted_state: str, user_id: str
    ):
        """
        Save an audit snapshot with encrypted state.

        Industry-standard implementation with:
        - PostgreSQL and SQLite support
        - Upsert semantics (INSERT or UPDATE)
        - Retry logic with exponential backoff
        - Circuit breaker for fault tolerance

        Args:
            snapshot_id: Unique identifier for the snapshot
            encrypted_state: Encrypted state data
            user_id: User who created the snapshot
        """
        AUDIT_DB_OPERATIONS.labels(operation="snapshot_audit_state").inc()
        user_id = validate_user_id(user_id)
        timestamp = datetime.utcnow().isoformat()

        try:
            if self.is_postgres:
                # PostgreSQL implementation with UPSERT (INSERT ... ON CONFLICT)
                async with self.AsyncSessionLocal() as session:
                    query = text("""
                        INSERT INTO audit_snapshots (snapshot_id, state, user_id, timestamp)
                        VALUES (:snapshot_id, :state, :user_id, :timestamp)
                        ON CONFLICT (snapshot_id) 
                        DO UPDATE SET 
                            state = EXCLUDED.state,
                            user_id = EXCLUDED.user_id,
                            timestamp = EXCLUDED.timestamp
                    """)
                    await session.execute(
                        query,
                        {
                            "snapshot_id": snapshot_id,
                            "state": encrypted_state,
                            "user_id": user_id,
                            "timestamp": timestamp,
                        },
                    )
                    await session.commit()
            else:
                # SQLite implementation with INSERT OR REPLACE
                query = "INSERT OR REPLACE INTO audit_snapshots (snapshot_id, state, user_id, timestamp) VALUES (?, ?, ?, ?)"
                async with self._get_aiosqlite_connection() as conn:
                    await conn.execute(
                        query,
                        (
                            snapshot_id,
                            encrypted_state,
                            user_id,
                            timestamp,
                        ),
                    )
                    await conn.commit()
            AUDIT_DB_OPERATIONS.labels(operation="snapshot_audit_state_success").inc()
        except Exception as e:
            AUDIT_DB_ERRORS.labels(operation="snapshot_audit_state").inc()
            logger.error(
                f"Error saving audit snapshot {snapshot_id}: {e}", exc_info=True
            )
            await self.feedback_manager.record_feedback(
                user_id="system",
                feedback_type=FeedbackType.BUG_REPORT,
                details={
                    "type": "db_error",
                    "operation": "snapshot_audit_state",
                    "error": str(e),
                },
            )
            raise

    @circuit(failure_threshold=5, recovery_timeout=60)
    @retry(tries=3, delay=1, backoff=2)
    async def snapshot_world_state(self, user_id: str) -> str:
        """
        Create a snapshot of all agent states in the world.

        Industry-standard implementation with:
        - PostgreSQL and SQLite support
        - Data anonymization before storage
        - Encryption of sensitive data
        - Retry logic and comprehensive error handling

        Args:
            user_id: User creating the snapshot

        Returns:
            Unique snapshot ID
        """
        DB_OPERATIONS.labels(operation="snapshot_world_state").inc()
        user_id = validate_user_id(user_id)
        snapshot_id = str(uuid.uuid4())

        try:
            # Retrieve all agent states using ORM
            async with self.AsyncSessionLocal() as session:
                agent_states = await session.execute(select(AgentState))
                states_list = [state.__dict__ for state in agent_states.scalars().all()]

            # Anonymize and encrypt data
            anonymized_states = [
                await self._anonymize_data(state) for state in states_list
            ]
            json_str = json.dumps(anonymized_states, default=safe_serialize)
            # encrypt() already returns a string, no need to decode
            encrypted_state = self.encrypter.encrypt(json_str.encode("utf-8"))

            # Save to database
            timestamp = datetime.utcnow().isoformat()
            if self.is_postgres:
                # PostgreSQL implementation
                async with self.AsyncSessionLocal() as session:
                    query = text("""
                        INSERT INTO world_snapshots (snapshot_id, state, user_id, timestamp)
                        VALUES (:snapshot_id, :state, :user_id, :timestamp)
                    """)
                    await session.execute(
                        query,
                        {
                            "snapshot_id": snapshot_id,
                            "state": encrypted_state,
                            "user_id": user_id,
                            "timestamp": timestamp,
                        },
                    )
                    await session.commit()
            else:
                # SQLite implementation
                query = "INSERT INTO world_snapshots (snapshot_id, state, user_id, timestamp) VALUES (?, ?, ?, ?)"
                async with self._get_aiosqlite_connection() as conn:
                    await conn.execute(
                        query,
                        (
                            snapshot_id,
                            encrypted_state,
                            user_id,
                            timestamp,
                        ),
                    )
                    await conn.commit()

            # Log audit trail
            await self._log_audit(
                "snapshot_world_state",
                snapshot_id,
                user_id,
                {"agent_count": len(states_list)},
            )
            return snapshot_id
        except Exception as e:
            DB_ERRORS.labels(operation="snapshot_world_state").inc()
            logger.error(f"Error creating world state snapshot: {e}", exc_info=True)
            await self.feedback_manager.record_feedback(
                user_id="system",
                feedback_type=FeedbackType.BUG_REPORT,
                details={
                    "type": "db_error",
                    "operation": "snapshot_world_state",
                    "error": str(e),
                },
            )
            raise

    @circuit(failure_threshold=5, recovery_timeout=60)
    @retry(tries=3, delay=1, backoff=2)
    async def restore_world_state(self, snapshot_id: str, user_id: str):
        """
        Restore world state from a snapshot.

        Industry-standard implementation with:
        - PostgreSQL and SQLite support
        - Transactional restore (all-or-nothing)
        - State decryption and validation
        - Circuit breaker and retry logic

        Args:
            snapshot_id: ID of snapshot to restore
            user_id: User performing the restore
        """
        DB_OPERATIONS.labels(operation="restore_world_state").inc()
        user_id = validate_user_id(user_id)

        try:
            # Retrieve snapshot
            if self.is_postgres:
                # PostgreSQL implementation
                async with self.AsyncSessionLocal() as session:
                    query = text(
                        "SELECT state FROM world_snapshots WHERE snapshot_id = :snapshot_id"
                    )
                    result = await session.execute(query, {"snapshot_id": snapshot_id})
                    row = result.fetchone()
            else:
                # SQLite implementation
                query = "SELECT state FROM world_snapshots WHERE snapshot_id = ?"
                async with self._get_aiosqlite_connection() as conn:
                    cur = await conn.execute(query, (snapshot_id,))
                    row = await cur.fetchone()

            if not row:
                raise ValueError(f"World snapshot '{snapshot_id}' not found.")

            # Decrypt and deserialize state
            encrypted_state = row["state"] if isinstance(row, dict) else row[0]
            decrypted_state_str = self.encrypter.decrypt(
                encrypted_state.encode("utf-8")
                if isinstance(encrypted_state, str)
                else encrypted_state
            ).decode("utf-8")
            states_list = json.loads(decrypted_state_str)

            # Restore states in a transaction (all-or-nothing)
            async with self.AsyncSessionLocal() as session:
                # Clear existing states
                await session.execute(delete(AgentState))

                # Restore states from snapshot
                for state_data in states_list:
                    # Hash the ID to maintain anonymization
                    state_data["name"] = hashlib.sha256(
                        state_data["id"].encode()
                    ).hexdigest()
                    del state_data["id"]
                    state = AgentState(**state_data)
                    session.add(state)

                # Commit transaction
                await session.commit()

            # Log audit trail
            await self._log_audit(
                "restore_world_state",
                snapshot_id,
                user_id,
                {"agent_count": len(states_list)},
            )
        except Exception as e:
            DB_ERRORS.labels(operation="restore_world_state").inc()
            logger.error(
                f"Error restoring world state snapshot {snapshot_id}: {e}",
                exc_info=True,
            )
            await self.feedback_manager.record_feedback(
                user_id="system",
                feedback_type=FeedbackType.BUG_REPORT,
                details={
                    "type": "db_error",
                    "operation": "restore_world_state",
                    "error": str(e),
                },
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
                # Issue #12 fix: Reference model tablenames instead of hardcoded strings
                agent_state_table = AgentState.__tablename__
                explain_audit_table = ExplainAuditRecord.__tablename__
                await session.execute(
                    text(
                        f"SELECT create_distributed_table('{agent_state_table}', 'name');"
                    )
                )
                await session.execute(
                    text(
                        f"SELECT create_distributed_table('{explain_audit_table}', 'uuid');"
                    )
                )
                await session.commit()
                logger.info("Migrated to Citus with distribution keys.")
            except sqlalchemy.exc.SQLAlchemyError as e:
                logger.error(
                    f"Failed to create distributed tables for Citus: {e}", exc_info=True
                )
                await session.rollback()
                raise

    async def rotate_keys(self, new_key: bytes):
        """
        Rotate encryption keys by prepending new key and re-encrypting existing data.

        Args:
            new_key: The new encryption key as bytes

        Note: This method temporarily switches from EnterpriseSecurityUtils to FernetEncryption
        for key rotation operations. Both provide compatible encrypt/decrypt interfaces.

        Thread-safety: Uses asyncio.Lock to prevent concurrent encryption operations (Issue #8 fix).
        """
        # Validate input
        if not isinstance(new_key, bytes):
            raise TypeError("new_key must be bytes")

        # Issue #16 fix: Validate the new key
        if not validate_fernet_key(new_key):
            raise ValueError("Invalid Fernet key format")

        # Issue #8 fix: Use lock to prevent race conditions
        async with self._rotation_lock:
            old_encrypter = self.encrypter

            try:
                new_key_str = new_key.decode("utf-8")
            except UnicodeDecodeError as e:
                raise ValueError(f"new_key must contain valid UTF-8 bytes: {e}")

            all_keys = [new_key_str] + settings.FERNET_KEYS.get_secret_value().split(
                ","
            )

            # Temporarily switch to FernetEncryption for multi-key support during rotation
            # Note: Atomic swap within the lock prevents race conditions
            self.encrypter = FernetEncryption([k.encode("utf-8") for k in all_keys])

            # Update global settings after encrypter swap
            settings.FERNET_KEYS = SecretStr(",".join(all_keys))

            logger.info("Starting key rotation and re-encryption of existing data...")

            async with self.AsyncSessionLocal() as session:
                try:
                    # Re-encrypt AgentState records
                    results = await session.execute(select(AgentState))
                    agents = results.scalars().all()
                    for agent in agents:
                        if agent.inventory_v2:
                            try:
                                decrypted = old_encrypter.decrypt(
                                    agent.inventory_v2.encode("utf-8")
                                )
                                # encrypt() already returns a string, no need to decode
                                agent.inventory_v2 = self.encrypter.encrypt(
                                    decrypted
                                )
                            except InvalidToken:
                                logger.error(
                                    f"Failed to decrypt inventory for agent {agent.name}. Skipping re-encryption."
                                )

                        if agent.language_v2:
                            try:
                                decrypted = old_encrypter.decrypt(
                                    agent.language_v2.encode("utf-8")
                                )
                                # encrypt() already returns a string, no need to decode
                                agent.language_v2 = self.encrypter.encrypt(
                                    decrypted
                                )
                            except InvalidToken:
                                logger.error(
                                    f"Failed to decrypt language for agent {agent.name}. Skipping re-encryption."
                                )

                        if agent.memory_v2:
                            try:
                                decrypted = old_encrypter.decrypt(
                                    agent.memory_v2.encode("utf-8")
                                )
                                # encrypt() already returns a string, no need to decode
                                agent.memory_v2 = self.encrypter.encrypt(
                                    decrypted
                                )
                            except InvalidToken:
                                logger.error(
                                    f"Failed to decrypt memory for agent {agent.name}. Skipping re-encryption."
                                )

                        if agent.personality_v2:
                            try:
                                decrypted = old_encrypter.decrypt(
                                    agent.personality_v2.encode("utf-8")
                                )
                                # encrypt() already returns a string, no need to decode
                                agent.personality_v2 = self.encrypter.encrypt(
                                    decrypted
                                )
                            except InvalidToken:
                                logger.error(
                                    f"Failed to decrypt personality for agent {agent.name}. Skipping re-encryption."
                                )

                        if agent.custom_attributes_v2:
                            try:
                                decrypted = old_encrypter.decrypt(
                                    agent.custom_attributes_v2.encode("utf-8")
                                )
                                # encrypt() already returns a string, no need to decode
                                agent.custom_attributes_v2 = self.encrypter.encrypt(
                                    decrypted
                                )
                            except InvalidToken:
                                logger.error(
                                    f"Failed to decrypt custom attributes for agent {agent.name}. Skipping re-encryption."
                                )

                    await session.commit()
                    logger.info(
                        f"Re-encrypted {len(agents)} AgentState records with the new key."
                    )

                except Exception as e:
                    logger.error(
                        f"Error during key rotation re-encryption: {e}", exc_info=True
                    )
                    await session.rollback()
                    raise

            logger.info("Key rotation complete.")

    async def save_generator_state(self, agent_id: str, data: Dict[str, Any]):
        """
        Save or update state for a generator agent using UPSERT logic.

        FIXED: Previous implementation always inserted new rows with default coordinates,
        resetting agent positions on every save. Now uses SQLite's ON CONFLICT clause
        to update existing records or insert new ones.

        Also creates an audit record for state changes (Bug C fix).

        Uses module-level constants for default values: DEFAULT_AGENT_X, DEFAULT_AGENT_Y,
        DEFAULT_AGENT_ENERGY, and DEFAULT_AGENT_WORLD_SIZE.
        """
        async with self.AsyncSessionLocal() as session:
            # First, check if agent exists to determine if this is create or update
            result = await session.execute(
                select(GeneratorAgentState).where(GeneratorAgentState.id == agent_id)
            )
            existing_agent = result.scalar_one_or_none()
            is_update = existing_agent is not None

            # Use SQLite's INSERT ... ON CONFLICT for upsert
            # This preserves existing coordinates and only updates changed fields
            stmt = sqlite_insert(GeneratorAgentState).values(
                id=agent_id,
                name=data.get("name", "generator"),
                x=data.get("x", DEFAULT_AGENT_X),
                y=data.get("y", DEFAULT_AGENT_Y),
                energy=data.get("energy", DEFAULT_AGENT_ENERGY),
                world_size=data.get("world_size", DEFAULT_AGENT_WORLD_SIZE),
                agent_type="generator",
                generated_code=data.get("code"),
                test_results=data.get("tests"),
                deployment_config=data.get("deployment"),
                docs=data.get("docs"),
            )

            # On conflict (duplicate id), update only the fields that changed
            # Preserve x, y, energy unless explicitly provided in data
            stmt = stmt.on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "name": stmt.excluded.name,
                    "x": stmt.excluded.x if "x" in data else GeneratorAgentState.x,
                    "y": stmt.excluded.y if "y" in data else GeneratorAgentState.y,
                    "energy": (
                        stmt.excluded.energy
                        if "energy" in data
                        else GeneratorAgentState.energy
                    ),
                    "world_size": (
                        stmt.excluded.world_size
                        if "world_size" in data
                        else GeneratorAgentState.world_size
                    ),
                    "generated_code": stmt.excluded.generated_code,
                    "test_results": stmt.excluded.test_results,
                    "deployment_config": stmt.excluded.deployment_config,
                    "docs": stmt.excluded.docs,
                },
            )

            await session.execute(stmt)
            await session.commit()

            # BUG C FIX: Create audit record for state change
            try:
                audit_record = {
                    "uuid": str(uuid.uuid4()),
                    "kind": "agent_state_change",
                    "name": f"generator_agent_{agent_id}",
                    "detail": json.dumps(
                        {
                            "action": "update" if is_update else "create",
                            "agent_id": agent_id,
                            "agent_type": "generator",
                            "changed_fields": list(data.keys()),
                        }
                    ),
                    "ts": time.time(),
                    "hash": hashlib.sha256(
                        f"{agent_id}_{time.time()}".encode()
                    ).hexdigest(),
                    "agent_id": agent_id,
                    "context": json.dumps({"operation": "save_generator_state"}),
                }
                await self.save_audit_record(audit_record)
            except Exception as e:
                logger.warning(
                    f"Failed to create audit record for generator state change: {e}"
                )
                # Don't fail the state save if audit fails

    async def save_sfe_state(self, agent_id: str, data: Dict[str, Any]):
        """
        Save or update state for a self-fixing engineer agent using UPSERT logic.

        FIXED: Previous implementation always inserted new rows with default coordinates,
        resetting agent positions on every save. Now uses SQLite's ON CONFLICT clause
        to update existing records or insert new ones.

        Also creates an audit record for state changes (Bug C fix).

        Uses module-level constants for default values: DEFAULT_AGENT_X, DEFAULT_AGENT_Y,
        DEFAULT_AGENT_ENERGY, and DEFAULT_AGENT_WORLD_SIZE.
        """
        async with self.AsyncSessionLocal() as session:
            # First, check if agent exists to determine if this is create or update
            result = await session.execute(
                select(SFEAgentState).where(SFEAgentState.id == agent_id)
            )
            existing_agent = result.scalar_one_or_none()
            is_update = existing_agent is not None

            # Use SQLite's INSERT ... ON CONFLICT for upsert
            stmt = sqlite_insert(SFEAgentState).values(
                id=agent_id,
                name=data.get("name", "sfe"),
                x=data.get("x", DEFAULT_AGENT_X),
                y=data.get("y", DEFAULT_AGENT_Y),
                energy=data.get("energy", DEFAULT_AGENT_ENERGY),
                world_size=data.get("world_size", DEFAULT_AGENT_WORLD_SIZE),
                agent_type="sfe",
                fixed_code=data.get("fixed_code"),
                analysis_report=data.get("analysis"),
                trust_score=data.get("trust_score"),
            )

            # On conflict (duplicate id), update only the fields that changed
            stmt = stmt.on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "name": stmt.excluded.name,
                    "x": stmt.excluded.x if "x" in data else SFEAgentState.x,
                    "y": stmt.excluded.y if "y" in data else SFEAgentState.y,
                    "energy": (
                        stmt.excluded.energy
                        if "energy" in data
                        else SFEAgentState.energy
                    ),
                    "world_size": (
                        stmt.excluded.world_size
                        if "world_size" in data
                        else SFEAgentState.world_size
                    ),
                    "fixed_code": stmt.excluded.fixed_code,
                    "analysis_report": stmt.excluded.analysis_report,
                    "trust_score": stmt.excluded.trust_score,
                },
            )

            await session.execute(stmt)
            await session.commit()

            # BUG C FIX: Create audit record for state change
            try:
                audit_record = {
                    "uuid": str(uuid.uuid4()),
                    "kind": "agent_state_change",
                    "name": f"sfe_agent_{agent_id}",
                    "detail": json.dumps(
                        {
                            "action": "update" if is_update else "create",
                            "agent_id": agent_id,
                            "agent_type": "sfe",
                            "changed_fields": list(data.keys()),
                        }
                    ),
                    "ts": time.time(),
                    "hash": hashlib.sha256(
                        f"{agent_id}_{time.time()}".encode()
                    ).hexdigest(),
                    "agent_id": agent_id,
                    "context": json.dumps({"operation": "save_sfe_state"}),
                }
                await self.save_audit_record(audit_record)
            except Exception as e:
                logger.warning(
                    f"Failed to create audit record for SFE state change: {e}"
                )
                # Don't fail the state save if audit fails
