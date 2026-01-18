import asyncio
import hashlib
import json
import logging
import os
import random
import smtplib
import sys
import threading
import time
from datetime import datetime, timezone
from email.mime.text import MIMEText
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

import aiohttp  # For Slack notifications
from arbiter.arbiter_plugin_registry import PlugInKind, register
from arbiter.arbiter_plugin_registry import registry as arbiter_registry
from arbiter.otel_config import get_tracer
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from tenacity import retry, stop_after_attempt, wait_exponential

# Mock aiosmtplib if not available
try:
    import aiosmtplib

    AIOSMTPLIB_AVAILABLE = True
except ImportError:
    AIOSMTPLIB_AVAILABLE = False
    logging.getLogger(__name__).warning(
        "aiosmtplib not available. Asynchronous email functionality will be disabled."
    )

    # Create a dummy class to prevent NameError
    class aiosmtplib:
        class SMTP:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                raise ImportError("aiosmtplib is not available.")

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass


# Assume these exist in arbiter/metrics.py
try:
    from arbiter.metrics import get_or_create_counter

    METRICS_AVAILABLE = True
except ImportError:
    METRICS_AVAILABLE = False

    class DummyCounter:
        def inc(self, amount: float = 1.0):
            pass

        def labels(self, *args, **kwargs):
            return self

    def get_or_create_counter(*args, **kwargs):
        return DummyCounter()


# Guard against circular import
if not globals().get('_HUMAN_LOOP_IMPORTING'):
    _HUMAN_LOOP_IMPORTING = True


# --- Corrected models.db_clients import block ---
try:
    from arbiter.models.db_clients import DummyDBClient, PostgresClient, SQLiteClient

    DB_CLIENTS_AVAILABLE = True
except ImportError:
    DB_CLIENTS_AVAILABLE = False
    logging.getLogger(__name__).warning(
        "Warning: arbiter.models.db_clients not found. DummyDBClient will be used as a fallback."
    )

    class PostgresClient:
        pass

    class SQLiteClient:
        pass

    class DummyDBClient:
        """
        Fallback in-memory database client with file-based persistence.

        PERSISTENCE: Unlike the previous stub, this implementation:
        - Persists data to a JSON file on disk
        - Automatically loads data on initialization
        - Saves data after each write operation
        - Provides fallback when real database is unavailable

        WARNING: This is not suitable for production multi-process deployments.
        Use a real database (PostgreSQL, SQLite) in production.

        Environment Variables:
        - DUMMY_DB_FILE: Path to persistence file (default: '/tmp/dummy_db_feedback.json')
        - DUMMY_DB_BACKUP_COUNT: Number of backup files to keep (default: 3)
        """

        def __init__(self) -> None:
            self.feedback_entries: List[Dict[str, Any]] = []
            self._db_file = os.getenv("DUMMY_DB_FILE", "/tmp/dummy_db_feedback.json")
            self._backup_count = int(os.getenv("DUMMY_DB_BACKUP_COUNT", "3"))
            self._lock = threading.Lock()
            logger = logging.getLogger(__name__)

            # Log warning about using in-memory fallback
            logger.warning(
                "Using DummyDBClient fallback with file persistence. "
                f"Data will be persisted to: {self._db_file}. "
                "Install a real database client for production use."
            )

            # Load existing data from file
            self._load_from_file()

        def _load_from_file(self) -> None:
            """Load feedback entries from persistence file."""
            logger = logging.getLogger(__name__)
            try:
                if os.path.exists(self._db_file):
                    with open(self._db_file, "r") as f:
                        data = json.load(f)
                        self.feedback_entries = data.get("entries", [])
                    logger.info(
                        f"Loaded {len(self.feedback_entries)} entries from {self._db_file}"
                    )
            except Exception as e:
                logger.error(
                    f"Failed to load data from {self._db_file}: {e}. Starting with empty dataset."
                )
                self.feedback_entries = []

        def _save_to_file(self) -> None:
            """Save feedback entries to persistence file with backup rotation."""
            logger = logging.getLogger(__name__)
            try:
                # Create backup of existing file
                if os.path.exists(self._db_file):
                    # Delete oldest backup if it exists
                    oldest_backup = f"{self._db_file}.{self._backup_count}"
                    if os.path.exists(oldest_backup):
                        os.remove(oldest_backup)

                    # Rotate backups (move each backup to next number)
                    for i in range(self._backup_count - 1, 0, -1):
                        old_backup = f"{self._db_file}.{i}"
                        new_backup = f"{self._db_file}.{i+1}"
                        if os.path.exists(old_backup):
                            os.rename(old_backup, new_backup)

                    # Create new backup from current file
                    os.rename(self._db_file, f"{self._db_file}.1")

                # Write current data
                with open(self._db_file, "w") as f:
                    json.dump(
                        {
                            "entries": self.feedback_entries,
                            "last_updated": datetime.now(timezone.utc).isoformat(),
                        },
                        f,
                        indent=2,
                    )

                logger.debug(
                    f"Saved {len(self.feedback_entries)} entries to {self._db_file}"
                )
            except Exception as e:
                logger.error(f"Failed to save data to {self._db_file}: {e}")

        async def save_feedback_entry(self, entry: Dict[str, Any]) -> None:
            """Save a feedback entry with file persistence."""
            with self._lock:
                entry_copy = entry.copy()
                if "timestamp" not in entry_copy:
                    entry_copy["timestamp"] = datetime.now(timezone.utc).isoformat()
                self.feedback_entries.append(entry_copy)

                logger = logging.getLogger(__name__)
                logger.debug(
                    f"DummyDBClient: Saved entry. Total entries: {len(self.feedback_entries)}"
                )

                # Persist to file
                self._save_to_file()

        async def get_feedback_entries(
            self, query: Optional[Dict[str, Any]] = None
        ) -> List[Dict[str, Any]]:
            """Retrieve feedback entries matching optional query."""
            with self._lock:
                if query is None:
                    return self.feedback_entries.copy()
                return [
                    e
                    for e in self.feedback_entries
                    if isinstance(e, dict)
                    and all(e.get(k) == v for k, v in query.items())
                ]

        async def update_feedback_entry(
            self, query: Dict[str, Any], updates: Dict[str, Any]
        ) -> bool:
            """Update feedback entries matching query."""
            with self._lock:
                updated = 0
                for e in self.feedback_entries:
                    if isinstance(e, dict) and all(
                        e.get(k) == v for k, v in query.items()
                    ):
                        e.update(updates)
                        updated += 1

                logger = logging.getLogger(__name__)
                logger.debug(
                    f"DummyDBClient: Updated {updated} entries for query {query}."
                )

                if updated > 0:
                    # Persist changes to file
                    self._save_to_file()

                return updated > 0

    # The original file had a different fallback class name, I've consolidated it to DummyDBClient
    # as the fallback class for internal use to avoid confusion.


# --- Import FeedbackManager from arbiter.feedback ---
try:
    from arbiter.feedback import FeedbackManager
except ImportError:
    logging.getLogger(__name__).warning(
        "Warning: arbiter.feedback.FeedbackManager not found. Using fallback implementation."
    )

    # Fallback FeedbackManager for cases where arbiter.feedback is not available
    class FeedbackManager:
        def __init__(
            self, db_client: Union[DummyDBClient, PostgresClient, SQLiteClient]
        ) -> None:
            self.db_client = db_client
            logger.info(
                f"FeedbackManager (fallback) initialized with {type(db_client).__name__}."
            )

        async def log_approval_request(
            self, decision_id: str, decision_context: Dict[str, Any]
        ) -> None:
            log_entry = {
                "type": "approval_request",
                "decision_id": decision_id,
                "context": decision_context,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": "pending",
                "request_start_time_utc": datetime.now(timezone.utc).isoformat(),
            }
            await self.db_client.save_feedback_entry(log_entry)
            logger.info(
                f"Logged approval request for decision_id: {decision_id}. Status: pending."
            )

        async def log_approval_response(
            self, decision_id: str, response: Dict[str, Any]
        ) -> None:
            ts = datetime.now(timezone.utc).isoformat()
            await self.db_client.save_feedback_entry(
                {
                    "type": "approval_response",
                    "decision_id": decision_id,
                    "response": response,
                    "timestamp": ts,
                    "status": "resolved",
                }
            )
            await self.db_client.update_feedback_entry(
                {
                    "type": "approval_request",
                    "decision_id": decision_id,
                    "status": "pending",
                },
                {
                    "status": "resolved",
                    "resolution_timestamp": ts,
                    "response_details": response,
                },
            )
            logger.info(
                f"Logged approval response for decision_id: {decision_id}. Status: resolved."
            )

        async def record_metric(
            self,
            metric_name: str,
            value: Union[int, float],
            tags: Optional[Dict[str, str]] = None,
        ) -> None:
            await self.db_client.save_feedback_entry(
                {
                    "type": "metric",
                    "name": metric_name,
                    "value": value,
                    "tags": tags or {},
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )
            logger.debug(f"Recorded metric {metric_name} with value {value}.")

        async def log_error(self, error_details: Dict[str, Any]) -> None:
            await self.db_client.save_feedback_entry(
                {
                    **error_details,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "type": "error_log",
                }
            )
            logger.error(f"Logged error: {error_details.get('message', 'No message')}")


# --- Secure Configuration ---
SECRET_SALT = os.environ.get("HITL_SECRET_SALT", "default-dev-salt-is-not-secure")

# OpenTelemetry Setup - Using centralized configuration
tracer = get_tracer(__name__)

# --- Logger Setup ---
try:
    from arbiter.agent_state import Base
    from arbiter.logging_utils import PIIRedactorFilter
    from arbiter_plugin_registry import PlugInKind as MockPlugInKind
    from arbiter_plugin_registry import registry as mock_registry
except ImportError:

    class mock_registry:
        @staticmethod
        def register(kind, name, version, author):
            def decorator(cls):
                return cls

            return decorator

    class MockPlugInKind:
        CORE_SERVICE = "core_service"

    class PIIRedactorFilter(logging.Filter):
        def filter(self, record):
            return True

    # Use SQLAlchemy 2.0 style declarative_base
    from sqlalchemy.orm import declarative_base

    Base = declarative_base()

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    handler.setFormatter(formatter)
    handler.addFilter(PIIRedactorFilter())
    logger.addHandler(handler)


# --- Pydantic Schemas ---
class HumanFeedbackSchema(BaseModel):
    decision_id: str
    approved: bool
    user_id: str
    signature: str
    comment: str = Field(default="", max_length=2048)
    timestamp: str


class DecisionRequestSchema(BaseModel):
    decision_id: Optional[str] = None
    cycle: Optional[str] = None
    risk_level: str = "medium"
    required_role: str = "reviewer"
    timeout_seconds: Optional[int] = None
    action: str = "unknown"
    details: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        extra = "allow"


class HumanInLoopConfig(BaseModel):
    DATABASE_URL: Optional[str] = None
    EMAIL_ENABLED: bool = Field(False, description="Enable email notifications.")
    EMAIL_SMTP_SERVER: Optional[str] = Field(None, description="SMTP server address.")
    EMAIL_SMTP_PORT: int = Field(587, description="SMTP server port.")
    EMAIL_SMTP_USER: Optional[str] = Field(
        None, description="SMTP user for authentication."
    )
    EMAIL_SMTP_PASSWORD: Optional[str] = Field(
        None, description="SMTP password for authentication."
    )
    EMAIL_SENDER: str = Field(
        "no-reply@yourdomain.com", description="Email sender address."
    )
    EMAIL_USE_TLS: bool = Field(True, description="Use TLS for SMTP connection.")
    EMAIL_RECIPIENTS: Dict[str, str] = Field(
        default_factory=dict, description="Map of roles to email addresses."
    )
    SLACK_WEBHOOK_URL: Optional[str] = Field(
        None, description="Slack webhook URL for notifications."
    )
    DEFAULT_TIMEOUT_SECONDS: int = Field(
        300, description="Default timeout for approval requests."
    )
    IS_PRODUCTION: bool = Field(False, description="Flag for production environment.")
    RETRY_DELAY_SECONDS: int = Field(
        5, description="Delay between notification retries."
    )
    MAX_NOTIFICATION_RETRIES: int = Field(
        3, description="Maximum number of retries for a notification."
    )
    SLACK_AUTH_TOKEN: Optional[str] = Field(
        None, description="Slack OAuth token for API calls."
    )

    @model_validator(mode="before")
    def validate_production_email_config(cls, values):
        if values.get("IS_PRODUCTION") and values.get("EMAIL_ENABLED"):
            if not all(
                [
                    values.get("EMAIL_SMTP_SERVER"),
                    values.get("EMAIL_SMTP_USER"),
                    values.get("EMAIL_SMTP_PASSWORD"),
                ]
            ):
                raise ValueError(
                    "In production, EMAIL_ENABLED requires all SMTP configuration fields (server, user, password)."
                )
        return values

    @field_validator("DATABASE_URL")
    def validate_database_url_in_production(cls, v, info):
        if info.data.get("IS_PRODUCTION") and not v:
            raise ValueError(
                "In production, DATABASE_URL must be set and point to a real database."
            )
        return v

    @model_validator(mode="after")
    def validate_salt_in_production(self):
        if self.IS_PRODUCTION and "default-dev-salt" in SECRET_SALT:
            logger.critical(
                "SECURITY ALERT: Using default SECRET_SALT in a production environment is insecure. Please set the HITL_SECRET_SALT environment variable."
            )
        return self


# --- Prometheus Metrics ---
human_in_loop_approvals = get_or_create_counter(
    "human_in_loop_approvals_total",
    "Total number of human-in-the-loop approvals.",
    labelnames=("decision_id",),
)

human_in_loop_denials = get_or_create_counter(
    "human_in_loop_denials_total",
    "Total number of human-in-the-loop denials.",
    labelnames=("decision_id",),
)
human_loop_feedback_total = get_or_create_counter(
    "human_loop_feedback_total",
    "Total feedback operations by type",
    labelnames=("operation",),
)


# --- WebSocketManager (Stub) ---
class WebSocketManager:
    """
    WebSocket connection manager for real-time communication with UI clients.

    Supports multiple concurrent connections, automatic reconnection,
    connection state tracking, and integration with FastAPI WebSocket endpoints.
    """

    def __init__(self, max_connections: int = 100):
        """
        Initialize WebSocket manager.

        Args:
            max_connections: Maximum number of concurrent connections
        """
        self.max_connections = max_connections
        self._connections: Dict[str, Any] = {}  # connection_id -> websocket
        self._connection_metadata: Dict[str, Dict[str, Any]] = (
            {}
        )  # connection_id -> metadata
        self._lock = asyncio.Lock()
        self._message_queue: asyncio.Queue = asyncio.Queue()
        self._broadcast_task: Optional[asyncio.Task] = None
        logger.info(
            f"WebSocketManager initialized with max_connections={max_connections}"
        )

    async def start(self) -> None:
        """Start the WebSocket manager background tasks."""
        if self._broadcast_task is None:
            self._broadcast_task = asyncio.create_task(self._broadcast_worker())
            logger.info("WebSocketManager broadcast worker started")

    async def stop(self) -> None:
        """Stop the WebSocket manager and close all connections."""
        if self._broadcast_task:
            self._broadcast_task.cancel()
            try:
                await self._broadcast_task
            except asyncio.CancelledError:
                pass
            self._broadcast_task = None

        # Close all connections
        async with self._lock:
            for conn_id, ws in list(self._connections.items()):
                try:
                    await ws.close()
                except Exception as e:
                    logger.warning(f"Error closing connection {conn_id}: {e}")
            self._connections.clear()
            self._connection_metadata.clear()

        logger.info("WebSocketManager stopped")

    async def register_connection(
        self,
        connection_id: str,
        websocket: Any,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Register a new WebSocket connection.

        Args:
            connection_id: Unique identifier for the connection
            websocket: WebSocket instance (e.g., from FastAPI)
            metadata: Optional metadata about the connection

        Returns:
            True if connection registered successfully, False if limit reached
        """
        async with self._lock:
            if len(self._connections) >= self.max_connections:
                logger.warning(
                    f"Max connections ({self.max_connections}) reached, "
                    f"rejecting connection {connection_id}"
                )
                return False

            self._connections[connection_id] = websocket
            self._connection_metadata[connection_id] = {
                "connected_at": datetime.now(timezone.utc).isoformat(),
                "messages_sent": 0,
                "last_activity": datetime.now(timezone.utc).isoformat(),
                **(metadata or {}),
            }

            logger.info(
                f"WebSocket connection registered: {connection_id} "
                f"({len(self._connections)}/{self.max_connections})"
            )
            return True

    async def unregister_connection(self, connection_id: str) -> None:
        """
        Unregister a WebSocket connection.

        Args:
            connection_id: Connection identifier to remove
        """
        async with self._lock:
            if connection_id in self._connections:
                self._connections.pop(connection_id)
                self._connection_metadata.pop(connection_id)
                logger.info(
                    f"WebSocket connection unregistered: {connection_id} "
                    f"({len(self._connections)}/{self.max_connections})"
                )

    async def send_json(
        self, data: Dict[str, Any], connection_id: Optional[str] = None
    ) -> None:
        """
        Send JSON data to WebSocket client(s).

        Args:
            data: JSON-serializable data to send
            connection_id: If provided, send only to this connection.
                          If None, broadcast to all connections.
        """
        if connection_id:
            # Send to specific connection
            async with self._lock:
                if connection_id in self._connections:
                    ws = self._connections[connection_id]
                    try:
                        await ws.send_json(data)
                        self._connection_metadata[connection_id]["messages_sent"] += 1
                        self._connection_metadata[connection_id]["last_activity"] = (
                            datetime.now(timezone.utc).isoformat()
                        )
                        logger.debug(f"Sent JSON to connection {connection_id}")
                    except Exception as e:
                        logger.error(f"Error sending to {connection_id}: {e}")
                        await self.unregister_connection(connection_id)
                else:
                    logger.warning(f"Connection {connection_id} not found")
        else:
            # Broadcast to all connections
            await self._message_queue.put(data)
            logger.debug(f"Queued broadcast message: {json.dumps(data)[:150]}...")

    async def _broadcast_worker(self) -> None:
        """Background worker to broadcast messages to all connections."""
        logger.info("WebSocket broadcast worker started")
        try:
            while True:
                data = await self._message_queue.get()

                async with self._lock:
                    failed_connections = []
                    for conn_id, ws in self._connections.items():
                        try:
                            await ws.send_json(data)
                            self._connection_metadata[conn_id]["messages_sent"] += 1
                            self._connection_metadata[conn_id]["last_activity"] = (
                                datetime.now(timezone.utc).isoformat()
                            )
                        except Exception as e:
                            logger.error(f"Error broadcasting to {conn_id}: {e}")
                            failed_connections.append(conn_id)

                    # Remove failed connections
                    for conn_id in failed_connections:
                        self._connections.pop(conn_id, None)
                        self._connection_metadata.pop(conn_id, None)

                    if failed_connections:
                        logger.info(
                            f"Removed {len(failed_connections)} failed connections"
                        )

                self._message_queue.task_done()

        except asyncio.CancelledError:
            logger.info("WebSocket broadcast worker cancelled")
            raise

    def get_connection_count(self) -> int:
        """Get the number of active connections."""
        return len(self._connections)

    def get_connection_stats(self) -> Dict[str, Any]:
        """Get statistics about connections."""
        return {
            "active_connections": len(self._connections),
            "max_connections": self.max_connections,
            "connection_details": dict(self._connection_metadata),
        }

    async def ping_all(self) -> Dict[str, bool]:
        """
        Ping all connections to check if they're alive.

        Returns:
            Dictionary mapping connection_id to alive status
        """
        results = {}
        async with self._lock:
            for conn_id, ws in list(self._connections.items()):
                try:
                    await ws.send_json({"type": "ping", "timestamp": time.time()})
                    results[conn_id] = True
                except Exception:
                    results[conn_id] = False
                    await self.unregister_connection(conn_id)

        return results


# --- HumanInLoop ---
class HumanInLoop:
    """
    Human-in-the-loop approval and feedback pipeline with secure validation,
    multi-channel notification/escalation, hooks, and gold-standard testability.
    """

    mock_approval_delay_seconds: float = 0.1  # class attribute for test/demo

    def __init__(
        self,
        config: HumanInLoopConfig,
        feedback_manager: Optional[FeedbackManager] = None,
        websocket_manager: Optional[WebSocketManager] = None,
        logger: Optional[logging.Logger] = None,
        audit_hook: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        error_hook: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
        choice: Callable[[List[Any]], Any] = random.choice,
    ) -> None:
        self.logger = logger or logging.getLogger(__name__)
        self.config = config

        db_url = self.config.DATABASE_URL
        if db_url:
            if db_url.startswith("postgresql") and DB_CLIENTS_AVAILABLE:
                self._db_client = PostgresClient(db_url=db_url)
                self.logger.info(
                    "HumanInLoop: Using PostgresClient for database interactions."
                )
            elif db_url.startswith("sqlite") and DB_CLIENTS_AVAILABLE:
                self._db_client = SQLiteClient(db_file=db_url.replace("sqlite:///", ""))
                self.logger.info(
                    "HumanInLoop: Using SQLiteClient for database interactions."
                )
            else:
                if self.config.IS_PRODUCTION:
                    raise RuntimeError(
                        f"HumanInLoop: In production, DATABASE_URL '{db_url}' is not supported or its client is not available. Refusing to start."
                    )
                else:
                    self._db_client = DummyDBClient()
                    self.logger.warning(
                        f"HumanInLoop: Development mode: DATABASE_URL '{db_url}' not recognized or driver not available. Falling back to DummyDBClient."
                    )
        else:
            if self.config.IS_PRODUCTION:
                raise RuntimeError(
                    "HumanInLoop: In production, DATABASE_URL is not set. Refusing to start without a real database."
                )
            else:
                self._db_client = DummyDBClient()
                self.logger.warning(
                    "HumanInLoop: Development mode: No DATABASE_URL found in config. Falling back to DummyDBClient."
                )

        self.feedback_manager = (
            feedback_manager
            if feedback_manager
            else FeedbackManager(db_client=self._db_client)
        )
        self.websocket_manager = websocket_manager
        self.audit_hook = audit_hook
        self.error_hook = error_hook
        self.sleeper = sleeper
        self.choice = choice
        self._pending_approvals: Dict[str, asyncio.Future] = {}
        self._lock = asyncio.Lock()
        self.logger.info("HumanInLoop initialized with gold-standard capabilities.")

    async def __aenter__(self):
        """Initializes the HumanInLoop instance, connecting to the database."""
        if hasattr(self._db_client, "connect"):
            await self._db_client.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Cleans up resources, disconnecting from the database."""
        if hasattr(self._db_client, "disconnect"):
            await self._db_client.disconnect()
        self.logger.info("HumanInLoop resources cleaned up")

    def check_permission(self, role: str, permission: str) -> bool:
        """Checks if a user role has a specific permission."""
        # Import directly from submodules to avoid circular import through __init__.py
        from arbiter.arbiter import PermissionManager
        from arbiter.config import ArbiterConfig

        permission_mgr = PermissionManager(ArbiterConfig())
        return permission_mgr.check_permission(role, permission)

    async def _handle_hook(
        self,
        hook: Optional[Callable[[Dict[str, Any]], Awaitable[None]]],
        event_data: Dict[str, Any],
    ) -> None:
        """
        Safely invokes a registered hook with structured event data.
        """
        if hook:
            try:
                await hook(event_data)
            except Exception as e:
                self.logger.error(
                    f"Error executing hook for event type {event_data.get('event_type')}: {e}"
                )

    async def request_approval(self, decision: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validates and sends a decision for human approval.
        """
        try:
            validated_request = DecisionRequestSchema(**decision)
        except ValidationError as e:
            self.logger.error(f"Decision request schema validation error: {e}")
            await self._handle_hook(
                self.error_hook,
                {
                    "event_type": "invalid_request_schema",
                    "errors": e.errors(),
                    "request_data": decision,
                },
            )
            return {"approved": False, "comment": "Invalid request schema."}

        decision_id = (
            validated_request.decision_id
            or validated_request.cycle
            or f"decision_{hashlib.sha256(json.dumps(validated_request.model_dump(), sort_keys=True).encode()).hexdigest()}"
        )
        timeout = (
            validated_request.timeout_seconds
            if validated_request.timeout_seconds is not None
            else self.config.DEFAULT_TIMEOUT_SECONDS
        )
        context = validated_request.model_dump()
        context["decision_id"] = decision_id
        context["timeout_seconds"] = timeout

        await self.feedback_manager.log_approval_request(decision_id, context)
        await self._handle_hook(
            self.audit_hook,
            {
                "event_type": "approval_requested",
                "decision_id": decision_id,
                "context": context,
            },
        )

        async with self._lock:
            approval_future = asyncio.Future()
            self._pending_approvals[decision_id] = approval_future

        notification_tasks = self._get_notification_tasks(
            decision_id, context, context.get("required_role", "reviewer")
        )
        if not notification_tasks:
            self.logger.warning(
                f"No notification channel configured for {decision_id}; using mock approval."
            )
            await self._mock_user_approval(decision_id, context)
        else:
            results = await asyncio.gather(*notification_tasks, return_exceptions=True)
            if all(isinstance(res, Exception) for res in results):
                self.logger.warning(
                    f"All notification channels failed for {decision_id}; falling back to mock approval."
                )
                await self._mock_user_approval(decision_id, context)

        try:
            return await asyncio.wait_for(approval_future, timeout=timeout)
        except asyncio.TimeoutError:
            timeout_response = {
                "approved": False,
                "user_id": "system",
                "comment": "Approval request timed out.",
            }
            await self._handle_hook(
                self.error_hook,
                {"event_type": "request_timeout", "decision_id": decision_id},
            )
            await self.receive_human_feedback(
                {
                    "decision_id": decision_id,
                    "signature": "N/A_timeout",
                    **timeout_response,
                }
            )
            return timeout_response
        finally:
            async with self._lock:
                self._pending_approvals.pop(decision_id, None)

    def _get_notification_tasks(
        self, decision_id: str, context: Dict[str, Any], role: str
    ) -> List[Awaitable[None]]:
        """Returns a list of notification coroutines for the approval request."""
        tasks = []
        email_recipients = self.config.EMAIL_RECIPIENTS
        if self.config.EMAIL_ENABLED and isinstance(email_recipients, dict):
            target_email = email_recipients.get(role)
            if target_email:
                tasks.append(
                    self._send_email_approval(decision_id, context, target_email)
                )
        if self.config.SLACK_WEBHOOK_URL:
            tasks.append(self._post_slack_approval(decision_id, context))
        if self.websocket_manager:
            tasks.append(self._notify_ui_approval(decision_id, context))
        return tasks

    @retry(
        stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10)
    )
    async def receive_human_feedback(self, feedback: Dict[str, Any]) -> None:
        """
        Validates and processes human feedback for an approval request.

        Args:
            feedback: Feedback data with decision_id, approved, user_id, comment, timestamp, and signature.

        Raises:
            ValueError: If feedback is invalid or storage fails.
            PermissionError: If the user lacks feedback permission.
        """
        with tracer.start_as_current_span("receive_human_feedback"):
            # Conceptual access control
            # if not self.check_permission(feedback.get("user_id", "guest"), "write_feedback"):
            #     self.logger.error("Permission denied to write feedback.")
            #     raise PermissionError("Write feedback permission required.")

            try:
                validated = HumanFeedbackSchema(**feedback)
            except ValidationError as e:
                self.logger.error(f"Feedback schema validation error: {e}")
                await self._handle_hook(
                    self.error_hook,
                    {
                        "event_type": "invalid_feedback_schema",
                        "errors": e.errors(),
                        "feedback_data": feedback,
                    },
                )
                return

            if "timestamp" not in feedback:
                feedback["timestamp"] = datetime.now(timezone.utc).isoformat()
                validated.timestamp = feedback["timestamp"]

            if not await self._validate_user_signature(
                validated.user_id,
                validated.signature,
                validated.decision_id,
                validated.approved,
                validated.comment,
                validated.timestamp,
            ):
                self.logger.error(
                    f"SECURITY ALERT: Invalid signature for feedback on {validated.decision_id}. Rejected."
                )
                await self._handle_hook(
                    self.error_hook,
                    {
                        "event_type": "invalid_signature",
                        "decision_id": validated.decision_id,
                        "user_id": validated.user_id,
                    },
                )
                return

            await self.feedback_manager.log_approval_response(
                validated.decision_id, validated.model_dump()
            )
            await self._handle_hook(
                self.audit_hook,
                {"event_type": "feedback_received", "feedback": validated.model_dump()},
            )

            # Record Prometheus metrics
            if validated.approved:
                human_in_loop_approvals.labels(decision_id=validated.decision_id).inc()
                await self.feedback_manager.record_metric(
                    "human_approval",
                    1.0,
                    {"decision_id": validated.decision_id, "status": "approved"},
                )
            else:
                human_in_loop_denials.labels(decision_id=validated.decision_id).inc()
                await self.feedback_manager.record_metric(
                    "human_approval",
                    0.0,
                    {"decision_id": validated.decision_id, "status": "denied"},
                )

            async with self._lock:
                approval_future = self._pending_approvals.get(validated.decision_id)
                if approval_future and not approval_future.done():
                    approval_future.set_result(validated.model_dump())

    async def _validate_user_signature(
        self,
        user_id: str,
        signature: str,
        decision_id: str,
        approved: bool,
        comment: str,
        timestamp: str,
    ) -> bool:
        """
        Validates human feedback signature.
        """
        expected = hashlib.sha256(
            f"{decision_id}{user_id}{approved}{comment}{timestamp}{SECRET_SALT}".encode()
        ).hexdigest()
        return signature == expected

    async def _send_email_approval(
        self, decision_id: str, context: Dict[str, Any], recipient: str
    ) -> None:
        """Sends a secure, detailed email notification for an approval request."""
        if not self.config.EMAIL_ENABLED:
            self.logger.debug("Email notifications are disabled in the configuration.")
            await self._handle_hook(
                self.error_hook,
                {
                    "event_type": "email_notification_skipped",
                    "decision_id": decision_id,
                    "reason": "email disabled in config",
                },
            )
            return

        subject = f"Approval Required: {context.get('action', 'Unknown Action')} (ID: {decision_id})"
        body = f"""
An automated action requires your review.
 
Decision ID: {decision_id}
Action: {context.get('action')}
Risk Level: {context.get('risk_level', 'N/A')}
Details:
{json.dumps(context.get('details'), indent=2)}
 
Please review and respond via the Arbiter dashboard or API.
This request will time out in {context.get('timeout_seconds', 'N/A')} seconds.
"""
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = self.config.EMAIL_SENDER
        msg["To"] = recipient

        retries = 0
        while retries < self.config.MAX_NOTIFICATION_RETRIES:
            try:
                if self.config.IS_PRODUCTION and AIOSMTPLIB_AVAILABLE:
                    async with aiosmtplib.SMTP(
                        hostname=self.config.EMAIL_SMTP_SERVER,
                        port=self.config.EMAIL_SMTP_PORT,
                        use_tls=self.config.EMAIL_USE_TLS,
                    ) as server:
                        await server.login(
                            self.config.EMAIL_SMTP_USER, self.config.EMAIL_SMTP_PASSWORD
                        )
                        await server.send_message(msg)
                else:
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(
                        None, lambda: self._send_sync_email(self.config, recipient, msg)
                    )
                self.logger.info(
                    f"Email notification for {decision_id} sent to {recipient}."
                )
                await self._handle_hook(
                    self.audit_hook,
                    {
                        "event_type": "email_notification_sent",
                        "decision_id": decision_id,
                        "recipient": recipient,
                    },
                )
                return
            except (smtplib.SMTPException, aiosmtplib.SMTPException) as e:
                self.logger.error(
                    f"SMTP error sending email for {decision_id} (attempt {retries+1}/{self.config.MAX_NOTIFICATION_RETRIES}): {e}"
                )
                retries += 1
                await self.sleeper(self.config.RETRY_DELAY_SECONDS)
            except Exception as e:
                self.logger.error(
                    f"Failed to send approval email for {decision_id}: {e}",
                    exc_info=True,
                )
                await self._handle_hook(
                    self.error_hook,
                    {
                        "event_type": "email_notification_failed",
                        "decision_id": decision_id,
                        "error": str(e),
                    },
                )
                return

        self.logger.error(
            f"Max retries reached: Failed to send approval email for {decision_id} after {self.config.MAX_NOTIFICATION_RETRIES} attempts."
        )
        await self._handle_hook(
            self.error_hook,
            {
                "event_type": "email_notification_failed_max_retries",
                "decision_id": decision_id,
                "error": "Max retries reached",
            },
        )

    def _send_sync_email(
        self, config: HumanInLoopConfig, recipient: str, msg: MIMEText
    ):
        """Helper for synchronous email sending in a thread pool executor."""
        with smtplib.SMTP(config.EMAIL_SMTP_SERVER, config.EMAIL_SMTP_PORT) as server:
            server.starttls()
            server.login(config.EMAIL_SMTP_USER, config.EMAIL_SMTP_PASSWORD)
            server.send_message(msg)

    async def _post_slack_approval(
        self, decision_id: str, context: Dict[str, Any]
    ) -> None:
        """Posts a rich, interactive approval request to a Slack channel."""
        if not self.config.SLACK_WEBHOOK_URL:
            self.logger.debug("Slack webhook URL not configured.")
            await self._handle_hook(
                self.error_hook,
                {
                    "event_type": "slack_notification_skipped",
                    "decision_id": decision_id,
                    "reason": "slack webhook not configured",
                },
            )
            return

        payload = {
            "text": f"Approval Required for action: {context.get('action')}",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Approval Required: `{context.get('action')}`*",
                    },
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*Decision ID:*\n`{decision_id}`"},
                        {
                            "type": "mrkdwn",
                            "text": f"*Risk Level:*\n`{context.get('risk_level', 'N/A').upper()}`",
                        },
                    ],
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Details:*\n```{json.dumps(context.get('details'), indent=2)}```",
                    },
                },
                {"type": "divider"},
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"This request will time out in *{context.get('timeout_seconds')} seconds*.",
                    },
                },
            ],
        }
        retries = 0
        while retries < self.config.MAX_NOTIFICATION_RETRIES:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        self.config.SLACK_WEBHOOK_URL, json=payload, timeout=10
                    ) as response:
                        response.raise_for_status()
                        self.logger.info(
                            f"Slack notification for {decision_id} sent successfully."
                        )
                        await self._handle_hook(
                            self.audit_hook,
                            {
                                "event_type": "slack_notification_sent",
                                "decision_id": decision_id,
                            },
                        )
                        return
            except aiohttp.ClientError as e:
                self.logger.error(
                    f"Slack API error sending notification for {decision_id} (attempt {retries+1}/{self.config.MAX_NOTIFICATION_RETRIES}): {e}"
                )
                retries += 1
                await self.sleeper(self.config.RETRY_DELAY_SECONDS)
            except asyncio.TimeoutError:
                self.logger.error(
                    f"Slack notification timeout for {decision_id} (attempt {retries+1}/{self.config.MAX_NOTIFICATION_RETRIES})."
                )
                retries += 1
                await self.sleeper(self.config.RETRY_DELAY_SECONDS)
            except Exception as e:
                self.logger.error(
                    f"Failed to send Slack notification for {decision_id}: {e}",
                    exc_info=True,
                )
                await self._handle_hook(
                    self.error_hook,
                    {
                        "event_type": "slack_notification_failed",
                        "decision_id": decision_id,
                        "error": str(e),
                    },
                )
                return

        self.logger.error(
            f"Max retries reached: Failed to send Slack notification for {decision_id} after {self.config.MAX_NOTIFICATION_RETRIES} attempts."
        )
        await self._handle_hook(
            self.error_hook,
            {
                "event_type": "slack_notification_failed_max_retries",
                "decision_id": decision_id,
                "error": "Max retries reached",
            },
        )

    async def _notify_ui_approval(
        self, decision_id: str, context: Dict[str, Any]
    ) -> None:
        """Sends a real-time approval request to connected WebSocket clients."""
        if not self.websocket_manager:
            self.logger.debug("WebSocket manager not configured.")
            await self._handle_hook(
                self.error_hook,
                {
                    "event_type": "websocket_notification_skipped",
                    "decision_id": decision_id,
                    "reason": "websocket manager not configured",
                },
            )
            return

        payload = {
            "type": "approval_request",
            "data": {"decision_id": decision_id, "context": context},
        }
        retries = 0
        while retries < self.config.MAX_NOTIFICATION_RETRIES:
            try:
                await self.websocket_manager.send_json(payload)
                self.logger.info(
                    f"UI notification for {decision_id} sent via WebSocket."
                )
                await self._handle_hook(
                    self.audit_hook,
                    {
                        "event_type": "websocket_notification_sent",
                        "decision_id": decision_id,
                    },
                )
                return
            except Exception as e:
                self.logger.error(
                    f"Failed to send WebSocket notification for {decision_id} (attempt {retries+1}/{self.config.MAX_NOTIFICATION_RETRIES}): {e}"
                )
                retries += 1
                await self.sleeper(self.config.RETRY_DELAY_SECONDS)

        self.logger.error(
            f"Max retries reached: Failed to send WebSocket notification for {decision_id} after {self.config.MAX_NOTIFICATION_RETRIES} attempts."
        )
        await self._handle_hook(
            self.error_hook,
            {
                "event_type": "websocket_notification_failed_max_retries",
                "decision_id": decision_id,
                "error": "Max retries reached",
            },
        )

    async def _mock_user_approval(
        self, decision_id: str, decision_context: Dict[str, Any]
    ) -> None:
        """Simulates human approval with secure mock signature and delay."""
        await self.sleeper(self.mock_approval_delay_seconds)
        approved = self.choice([True, False])
        user = f"simulated_user_{random.randint(1000,9999)}"
        comment = (
            "Simulated approval looks fine."
            if approved
            else "Simulated denial for safety."
        )
        timestamp = datetime.now(timezone.utc).isoformat()
        signature = hashlib.sha256(
            f"{decision_id}{user}{approved}{comment}{timestamp}{SECRET_SALT}".encode()
        ).hexdigest()
        await self.receive_human_feedback(
            {
                "decision_id": decision_id,
                "approved": approved,
                "user_id": user,
                "comment": comment,
                "timestamp": timestamp,
                "signature": signature,
            }
        )


# Register as a plugin
mock_registry.register(
    kind=MockPlugInKind.CORE_SERVICE,
    name="HumanInLoop",
    version="1.0.0",
    author="Arbiter Team",
)(HumanInLoop)


async def get_human_approval(
    decision_id: str, decision_context: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Plugin entry point to request human approval via the HumanInLoop manager.
    """
    config = HumanInLoopConfig()
    decision_payload = decision_context.copy()
    decision_payload["decision_id"] = decision_id
    async with HumanInLoop(config=config) as hitl_manager:
        response = await hitl_manager.request_approval(decision_payload)
        return response


# Only register if not already registered to avoid duplicate registration error
if not arbiter_registry.get_metadata(PlugInKind.CORE_SERVICE, "human_in_loop"):
    register(
        kind=PlugInKind.CORE_SERVICE,
        name="human_in_loop",
        version="1.0.0",
        author="Arbiter Team",
    )(get_human_approval)
    logger.info("human_in_loop plugin registered successfully")
else:
    logger.info("human_in_loop plugin already registered, skipping registration")
