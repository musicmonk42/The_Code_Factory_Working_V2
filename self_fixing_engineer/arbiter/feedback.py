import asyncio
import collections
import logging
import json
import os
import sys
import time
import threading
from datetime import datetime, timezone, timedelta
import random
import hashlib
import statistics
import traceback
from typing import Dict, Any, Optional, List, Union, Tuple, Type
from sqlalchemy import Column, String, JSON, DateTime, select
from sqlalchemy.orm import sessionmaker, declarative_base
import aiofiles
from prometheus_client import Counter, Gauge, Histogram, Summary, REGISTRY
from tenacity import retry, stop_after_attempt, wait_exponential
from arbiter.otel_config import get_tracer
import psycopg2
import aiosqlite
from arbiter.arbiter_plugin_registry import register, PlugInKind, registry as arbiter_registry

# Mock/Placeholder imports for a self-contained fix
try:
    from arbiter.postgres_client import PostgresClient
    from arbiter.config import ArbiterConfig
    from arbiter import PermissionManager
    from arbiter_plugin_registry import registry as mock_registry, PlugInKind as MockPlugInKind
    from arbiter.logging_utils import PIIRedactorFilter
    from arbiter.agent_state import Base
    DB_CLIENTS_AVAILABLE = True
except ImportError:
    DB_CLIENTS_AVAILABLE = False
    class PostgresClient:
        def __init__(self, db_url, **kwargs):
            self.db_url = db_url
        async def connect(self): pass
        async def disconnect(self): pass
        async def get_session(self):
            class MockSession:
                def __init__(self): self.logs = []
                async def __aenter__(self): return self
                async def __aexit__(self, exc_type, exc_val, exc_tb): pass
                def add(self, log): self.logs.append(log)
                async def commit(self): pass
                async def execute(self, query):
                    class MockResult:
                        def scalar_one_or_none(self): return None
                    return MockResult()
            return MockSession()
    class ConcreteSQLiteClient:
        pass
    class ArbiterConfig:
        def __init__(self):
            self.DATABASE_URL = "sqlite+aiosqlite:///:memory:"
            self.REPORTS_DIRECTORY = "./reports"
            self.ROLE_MAP = {"admin": 3, "user": 1}
    class PermissionManager:
        def __init__(self, config): self.config = config
        def check_permission(self, role, permission): return True
    class mock_registry:
        @staticmethod
        def register(kind, name, version, author):
            def decorator(cls): return cls
            return decorator
    class MockPlugInKind:
        CORE_SERVICE = "core_service"
    class PIIRedactorFilter(logging.Filter):
        def filter(self, record): return True
    Base = declarative_base()


# Check for psycopg2 and aiosqlite for client-specific functionality
try:
    import psycopg2
    POSTGRES_AVAILABLE = True
except ImportError:
    POSTGRES_AVAILABLE = False
    pass
        
try:
    import aiosqlite
    SQLLITE_AVAILABLE = True
except ImportError:
    SQLLITE_AVAILABLE = False
    pass

# Determine if the application is running in production
IS_PRODUCTION = os.getenv("APP_ENV", "development") == "production"

# OpenTelemetry Setup - Using centralized configuration
tracer = get_tracer(__name__)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter('%(asctime)s | %(levelname)s | %(message)s')
    handler.setFormatter(formatter)
    handler.addFilter(PIIRedactorFilter())
    logger.addHandler(handler)

# Lock for thread-safe metric registration
_metrics_lock = threading.Lock()

def _get_or_create_metric(metric_class: Union[Type[Counter], Type[Gauge], Type[Histogram], Type[Summary]],
                          name: str, documentation: str, labelnames: Tuple[str, ...] = (), buckets: Optional[Tuple[float, ...]] = None):
    """
    Idempotently get or create a Prometheus metric in a thread-safe manner.
    """
    with _metrics_lock:
        if name in REGISTRY._names_to_collectors:
            collector = REGISTRY._names_to_collectors[name]
            if isinstance(collector, metric_class):
                return collector
            logger.warning(f"Metric '{name}' already registered with a different type")
            return collector
        kwargs = {"name": name, "documentation": documentation, "labelnames": labelnames}
        if issubclass(metric_class, (Histogram, Summary)) and buckets is not None:
            kwargs["buckets"] = buckets or (0.001, 0.01, 0.1, 0.5, 1, 2, 5, 10)
        return metric_class(**kwargs)

# Prometheus Metrics Initialization
feedback_received_total = _get_or_create_metric(Counter, 'feedback_received_total', 'Total feedback received', ('type',))
feedback_errors_total = _get_or_create_metric(Counter, 'feedback_errors_total', 'Total errors logged', ('component',))
feedback_metrics_recorded_total = _get_or_create_metric(Counter, 'feedback_metrics_recorded_total', 'Total metrics recorded', ('metric_name',))
feedback_processing_time = _get_or_create_metric(Histogram, 'feedback_processing_time', 'Time spent processing feedback', buckets=(.001, .01, .1, 1, 10))
human_in_loop_approvals = _get_or_create_metric(Counter, 'human_in_loop_approvals_total', 'Total human approvals')
human_in_loop_denials = _get_or_create_metric(Counter, 'human_in_loop_denials_total', 'Total human denials')
last_feedback_timestamp = _get_or_create_metric(Gauge, 'last_feedback_timestamp', 'Timestamp of the last feedback received')
feedback_ops_total = _get_or_create_metric(Counter, "feedback_ops_total", "Total feedback operations", ["operation"])


class FeedbackLog(Base):
    """SQLAlchemy model for feedback logs."""
    __tablename__ = "feedback_logs"
    decision_id = Column(String, primary_key=True)
    data = Column(JSON)
    timestamp = Column(DateTime, default=datetime.now(timezone.utc))

class SQLiteClient:
    """
    Asynchronous client for SQLite database interactions.
    This client uses aiosqlite and is designed to be used with async context managers.
    """
    def __init__(self, db_file: str = "feedback.db"):
        if not SQLLITE_AVAILABLE:
            raise ImportError("aiosqlite is required for SQLiteClient. Install with 'pip install aiosqlite'")
        self.db_file = db_file
        logger.info(f"SQLiteClient initialized for database: {self.db_file}")

    async def connect(self):
        """Initializes the database schema if it doesn't exist."""
        async with aiosqlite.connect(self.db_file) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS feedback (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    data TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                )
            """)
            await db.commit()
        logger.info(f"SQLite database schema ensured for {self.db_file}")

    async def disconnect(self):
        """aiosqlite's context manager handles connection closing, so this is a no-op."""
        logger.debug(f"SQLiteClient disconnected for {self.db_file}")

    async def save_feedback_entry(self, entry: Dict[str, Any]):
        """Saves a single feedback entry into the database."""
        entry_copy = entry.copy()
        if "timestamp" not in entry_copy:
            entry_copy["timestamp"] = datetime.now(timezone.utc).isoformat()
        if "id" not in entry_copy:
            # Generate a unique ID using a hash of the content to prevent duplicates
            entry_copy["id"] = hashlib.md5(json.dumps(entry_copy, sort_keys=True).encode('utf-8')).hexdigest()
            
        async with aiosqlite.connect(self.db_file) as db:
            await db.execute("""
                INSERT OR REPLACE INTO feedback (id, type, data, timestamp)
                VALUES (?, ?, ?, ?)
            """, (entry_copy["id"], entry_copy.get("type"), json.dumps(entry_copy), entry_copy["timestamp"]))
            await db.commit()
        logger.debug(f"SQLiteClient: Saved feedback entry {entry_copy.get('id')}")

    async def get_feedback_entries(self, query: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Retrieves feedback entries from the database based on a query."""
        async with aiosqlite.connect(self.db_file) as db:
            if not query:
                cursor = await db.execute("SELECT data FROM feedback ORDER BY timestamp ASC")
            else:
                where_clauses = []
                params = []
                for k, v in query.items():
                    where_clauses.append(f"json_extract(data, '$.{k}') = ?")
                    params.append(v)
                where_sql = " AND ".join(where_clauses)
                cursor = await db.execute(f"SELECT data FROM feedback WHERE {where_sql} ORDER BY timestamp ASC", params)
            
            rows = await cursor.fetchall()
            return [json.loads(row[0]) for row in rows]

    async def update_feedback_entry(self, query: Dict[str, Any], updates: Dict[str, Any]) -> bool:
        """
        Updates one or more feedback entries matching the query with new data.
        Returns True if any entry was updated, False otherwise.
        """
        async with aiosqlite.connect(self.db_file) as db:
            where_clauses = []
            params = []
            for k, v in query.items():
                where_clauses.append(f"json_extract(data, '$.{k}') = ?")
                params.append(v)
            where_sql = " AND ".join(where_clauses)
            
            # Use json_patch or a similar approach for atomic updates if possible.
            # For now, we perform a select-then-update to be safe.
            cursor = await db.execute(f"SELECT id, data FROM feedback WHERE {where_sql}", params)
            rows = await cursor.fetchall()
            if not rows:
                return False
            
            for row_id, data_str in rows:
                data = json.loads(data_str)
                data.update(updates)
                
                await db.execute("""
                    UPDATE feedback SET data = ? WHERE id = ?
                """, (json.dumps(data), row_id))
            
            await db.commit()
            return True


class FeedbackManager:
    """
    Collects and summarizes metrics, error logs, and user feedback for any agent/arena.
    Designed to be used as a utility in Arbiter, Arena, or globally.
    """

    def __init__(self, db_client: Optional[Union[SQLiteClient, PostgresClient]] = None, config: Optional[Any] = None, log_file: str = "feedback_log.json", max_log_size: int = 1000):
        self.log_file = log_file
        self.max_log_size = max_log_size
        self._write_lock = asyncio.Lock()
        self.config = config or ArbiterConfig()

        self.db_client: Union[SQLiteClient, PostgresClient]
        db_url = getattr(self.config, 'DATABASE_URL', None) if self.config else None

        if db_client:
            self.db_client = db_client
            logger.info(f"FeedbackManager initialized with provided DB client: {type(db_client).__name__}.")
        elif db_url:
            if db_url.startswith("postgresql") and DB_CLIENTS_AVAILABLE and POSTGRES_AVAILABLE:
                self.db_client = PostgresClient(db_url=db_url, pool_size=5, max_overflow=10)
                logger.info("FeedbackManager: Using PostgresClient for database interactions.")
            elif db_url.startswith("sqlite") and DB_CLIENTS_AVAILABLE and SQLLITE_AVAILABLE:
                self.db_client = ConcreteSQLiteClient(db_file=db_url.replace("sqlite:///", ""))
                logger.info("FeedbackManager: Using SQLiteClient for database interactions.")
            else:
                if IS_PRODUCTION:
                    raise RuntimeError(f"FeedbackManager: In production, DATABASE_URL '{db_url}' is not supported or its client/driver is not available. Refusing to start.")
                else:
                    self.db_client = SQLiteClient(db_file="feedback.db")
                    logger.warning(f"FeedbackManager: Development mode: DATABASE_URL '{db_url}' not recognized or driver not available. Falling back to SQLiteClient (feedback.db).")
        else:
            if IS_PRODUCTION:
                raise RuntimeError("FeedbackManager: In production, DATABASE_URL is not set. Refusing to start without a real database.")
            else:
                self.db_client = SQLiteClient(db_file="feedback.db")
                logger.warning("FeedbackManager: Development mode: No DATABASE_URL found in config. Falling back to SQLiteClient (feedback.db).")


        self._ensure_log_file_exists()
        self._purge_task = None
        logger.info(f"FeedbackManager initialized. Log file: {self.log_file}")

    async def connect_db(self):
        """Connects to the underlying database client."""
        if hasattr(self.db_client, 'connect') and callable(self.db_client.connect):
            await self.db_client.connect()

    async def disconnect_db(self):
        """Disconnects from the underlying database client."""
        if hasattr(self.db_client, 'disconnect') and callable(self.db_client.disconnect):
            await self.db_client.disconnect()

    def _ensure_log_file_exists(self):
        """Ensures the flat JSON log file and its directory exist."""
        log_dir = os.path.dirname(self.log_file)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir)
        if not os.path.exists(self.log_file):
            with open(self.log_file, 'w', encoding='utf-8') as f:
                json.dump([], f)
            logger.info(f"Created empty feedback log file: {self.log_file}")

    async def _sync_and_filter_to_logfile(self):
        """
        Periodically filters and synchronizes all current entries from the database
        to the flat JSON log file.
        """
        try:
            all_entries = await self.db_client.get_feedback_entries()
            
            # If the log size limit is exceeded, keep only the most recent entries
            if len(all_entries) > self.max_log_size:
                all_entries.sort(key=lambda x: x.get('timestamp', ''))
                all_entries = all_entries[-self.max_log_size:]
            
            async with self._write_lock:
                async with aiofiles.open(self.log_file, 'w', encoding='utf-8') as f:
                    await f.write(json.dumps(all_entries, indent=2, default=str))
            logger.debug(f"All buffered data (from DB) written to {self.log_file}")
        except Exception as e:
            logger.error(f"Failed to write all data to feedback log file {self.log_file} from DB: {e}", exc_info=True)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def add_feedback(self, decision_id: str, feedback: Dict[str, Any]) -> None:
        """Stores feedback for a decision."""
        with tracer.start_as_current_span("add_feedback"):
            # if not self.check_permission("user", "write_feedback"):
            #     raise PermissionError("Write feedback permission required")
            try:
                async with self._write_lock:
                    feedback["decision_id"] = decision_id
                    feedback["timestamp"] = datetime.now(timezone.utc).isoformat()
                    async with self.db_client.get_session() as session:
                        session.add(FeedbackLog(decision_id=decision_id, data=feedback, timestamp=datetime.now(timezone.utc)))
                        await session.commit()
                    feedback_ops_total.labels(operation="add_feedback").inc()
                    logger.info(f"Feedback stored for decision {decision_id}")
            except Exception as e:
                logger.error(f"Failed to store feedback for decision {decision_id}: {e}", exc_info=True)
                feedback_errors_total.labels(component="add_feedback").inc()
                raise ValueError(f"Feedback storage failed: {e}") from e


    async def record_metric(self, name: str, value: float, tags: Optional[Dict[str, str]] = None):
        """Records a single metric."""
        if not isinstance(name, str) or not name.strip():
            logger.error(f"Invalid metric name: {name}")
            feedback_errors_total.labels(component='record_metric_invalid_name').inc()
            return
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            logger.error(f"Invalid metric value: {value} (type: {type(value)}) for metric {name}. Must be int or float, not bool.")
            feedback_errors_total.labels(component='record_metric_invalid_value').inc()
            return
            
        metric_entry = {
            "id": hashlib.md5(f"{name}-{value}-{datetime.now(timezone.utc).isoformat()}-{random.randint(0, 100000)}".encode('utf-8')).hexdigest(),
            "type": "metric",
            "name": name,
            "value": float(value),
            "tags": tags if tags else {},
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        try:
            await self.db_client.save_feedback_entry(metric_entry)
            feedback_metrics_recorded_total.labels(metric_name=name).inc()
            last_feedback_timestamp.set(datetime.now(timezone.utc).timestamp())
            logger.debug(f"Recorded metric: {name} = {value}")
        except Exception as e:
            logger.error(f"Failed to save metric {name}: {e}", exc_info=True)
            feedback_errors_total.labels(component='save_metric_db_error').inc()

    async def log_error(self, error_info: Dict[str, Any]):
        """Logs an error with detailed information."""
        error_info["timestamp"] = datetime.now(timezone.utc).isoformat()
        error_info["type"] = "error_log"
        if 'error' not in error_info:
            error_info['error'] = 'Unknown error'
        if 'traceback' not in error_info:
            error_info['traceback'] = traceback.format_exc()

        try:
            await self.db_client.save_feedback_entry(error_info)
            feedback_errors_total.labels(component=error_info.get('component', 'general')).inc()
            last_feedback_timestamp.set(datetime.now(timezone.utc).timestamp())
            logger.error(f"Error logged by FeedbackManager: {error_info.get('error')}")
        except Exception as e:
            logger.error(f"Failed to log error: {e}", exc_info=True)
            feedback_errors_total.labels(component='log_error_db_error').inc()

    async def add_user_feedback(self, feedback: Dict[str, Any]):
        """Adds user feedback, including approvals/denials."""
        feedback['timestamp'] = datetime.now(timezone.utc).isoformat()
        feedback['type'] = 'user_feedback'
        if feedback.get('approved') is True:
            human_in_loop_approvals.inc()
            feedback_received_total.labels(type='approval').inc()
        elif feedback.get('approved') is False:
            human_in_loop_denials.inc()
            feedback_received_total.labels(type='denial').inc()
        else:
            feedback_received_total.labels(type='other').inc()

        try:
            await self.db_client.save_feedback_entry(feedback)
            last_feedback_timestamp.set(datetime.now(timezone.utc).timestamp())
            logger.info(f"User feedback added: Decision ID={feedback.get('decision_id', 'N/A')}, Approved={feedback.get('approved', 'N/A')}")
        except Exception as e:
            logger.error(f"Failed to add user feedback: {e}", exc_info=True)
            feedback_errors_total.labels(component='add_user_feedback_db_error').inc()

    async def _purge_metrics_and_sync_loop(self):
        """
        Background task to periodically sync data to the flat log file and purge old metrics.
        """
        while True:
            try:
                # Purge old metrics
                if hasattr(self.db_client, 'get_session'):
                    async with self._write_lock:
                        async with self.db_client.get_session() as session:
                            await session.execute(
                                "DELETE FROM feedback_logs WHERE timestamp < :threshold",
                                {"threshold": datetime.now(timezone.utc) - timedelta(days=30)}
                            )
                            await session.commit()
                        feedback_ops_total.labels(operation="purge_metrics").inc()
                        logger.info("Old feedback metrics purged.")

                # Sync data to flat log file
                await self._sync_and_filter_to_logfile()

            except asyncio.CancelledError:
                logger.info("Metric purge task cancelled.")
                raise
            except Exception as e:
                logger.error(f"Metric purge or sync loop failed: {e}", exc_info=True)
                feedback_errors_total.labels(component="purge_loop").inc()
                # Avoid a tight loop by waiting
                await asyncio.sleep(60)

            await asyncio.sleep(3600)  # Run every hour

    async def get_summary(self) -> Dict[str, Any]:
        """Generates a summary report of all feedback data."""
        with feedback_processing_time.time():
            try:
                all_metrics_entries = await self.db_client.get_feedback_entries({"type": "metric"})
                all_error_logs = await self.db_client.get_feedback_entries({"type": "error_log"})
                all_user_feedback = await self.db_client.get_feedback_entries({"type": "user_feedback"})
                all_approval_requests = await self.db_client.get_feedback_entries({"type": "approval_request"})
                all_approval_responses = await self.db_client.get_feedback_entries({"type": "approval_response"})

                summary = {
                    "metrics_summary": {},
                    "recent_errors": all_error_logs[-5:] if all_error_logs else [],
                    "recent_user_feedback": all_user_feedback[-5:] if all_user_feedback else [],
                    "approval_requests_summary": {
                        "total_requests": len(all_approval_requests),
                        "total_responses": len(all_approval_responses),
                        "pending": sum(1 for r in all_approval_requests if r.get("status") == "pending"),
                        "approved_count": sum(1 for r in all_approval_responses if r.get("response", {}).get("approved") is True),
                        "denied_count": sum(1 for r in all_approval_responses if r.get("response", {}).get("approved") is False),
                    }
                }

                metrics_by_name = collections.defaultdict(list)
                for entry in all_metrics_entries:
                    if (isinstance(entry, dict) and "name" in entry and "value" in entry and
                        isinstance(entry["value"], (int, float)) and not isinstance(entry["value"], bool)):
                        metrics_by_name[entry["name"]].append(entry["value"])
                    else:
                        logger.warning(f"Skipping malformed metric entry in summary: {entry}")

                for name, values in metrics_by_name.items():
                    if values:
                        summary["metrics_summary"][name] = {
                            "count": len(values),
                            "mean": statistics.mean(values),
                            "min": min(values),
                            "max": max(values)
                        }
                return summary
            except Exception as e:
                logger.error(f"Error in get_summary: {e}", exc_info=True)
                feedback_errors_total.labels(component='get_summary_error').inc()
                return {"metrics_summary": {}, "recent_errors": [], "recent_user_feedback": [], "approval_requests_summary": {}}

    async def log_approval_request(self, decision_id: str, decision_context: Dict[str, Any]):
        """Logs a request for human approval."""
        log_entry = {
            "type": "approval_request",
            "decision_id": decision_id,
            "context": decision_context,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "pending",
            "request_start_time_utc": datetime.now(timezone.utc).isoformat()
        }
        try:
            await self.db_client.save_feedback_entry(log_entry)
            logger.info(f"Approval request logged as pending: {decision_id}")
        except Exception as e:
            logger.error(f"Failed to log approval request: {e}", exc_info=True)
            feedback_errors_total.labels(component='log_approval_request').inc()

    async def log_approval_response(self, decision_id: str, response: Dict[str, Any]):
        """Logs a response to a human approval request and updates the original request."""
        response_timestamp = datetime.now(timezone.utc).isoformat()
        log_entry = {
            "type": "approval_response",
            "decision_id": decision_id,
            "response": response,
            "timestamp": response_timestamp,
            "status": "resolved"
        }
        try:
            await self.db_client.save_feedback_entry(log_entry)
            await self.db_client.update_feedback_entry(
                {"type": "approval_request", "decision_id": decision_id, "status": "pending"},
                {"status": "resolved", "resolution_timestamp": response_timestamp, "response_details": response}
            )
            logger.info(f"Approval response logged for {decision_id}: Approved={response.get('approved')}, User={response.get('user_id', 'N/A')}")
            feedback_received_total.labels(type='approval_response').inc()
        except Exception as e:
            logger.error(f"Failed to log approval response for {decision_id}: {e}", exc_info=True)
            feedback_errors_total.labels(component='log_approval_response').inc()

    async def get_pending_approvals(self) -> List[Dict[str, Any]]:
        """Retrieves all pending approval requests."""
        try:
            return await self.db_client.get_feedback_entries({"type": "approval_request", "status": "pending"})
        except Exception as e:
            logger.error(f"Failed to get pending approvals: {e}", exc_info=True)
            feedback_errors_total.labels(component='get_pending_approvals').inc()
            return []

    async def get_feedback_by_decision_id(self, decision_id: str) -> List[Dict[str, Any]]:
        """Retrieves all feedback entries associated with a specific decision ID."""
        try:
            return await self.db_client.get_feedback_entries({"decision_id": decision_id})
        except Exception as e:
            logger.error(f"Failed to get feedback by decision ID {decision_id}: {e}", exc_info=True)
            feedback_errors_total.labels(component='get_feedback_by_decision_id').inc()
            return []

    async def get_approval_stats(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        group_by_reviewer: bool = False,
        group_by_decision_type: bool = False
    ) -> Dict[str, Any]:
        """
        Calculates statistics on human approval responses,
        optionally filtered by date and grouped by reviewer or decision type.
        """
        try:
            # The original code had a SyntaxError due to unmatched braces.
            # It seems like a simple oversight in a dictionary or a function call.
            # Assuming a list for the query was the intent to search for multiple types.
            responses_and_requests = await self.db_client.get_feedback_entries({"type": "approval_response"})
            all_requests = await self.db_client.get_feedback_entries({"type": "approval_request"})
            requests_map = {r.get('decision_id'): r for r in all_requests}

            filtered_responses = []
            for res in responses_and_requests:
                res_time_str = res.get("timestamp")
                if not res_time_str:
                    logger.warning(f"Skipping approval response with missing timestamp: {res}")
                    continue
                try:
                    res_time = datetime.fromisoformat(res_time_str)
                    if (start_date is None or res_time >= start_date) and \
                       (end_date is None or res_time <= end_date):
                        filtered_responses.append(res)
                except ValueError as ve:
                    logger.warning(f"Skipping approval time calculation due to invalid timestamp format for response {res.get('decision_id')}: {ve}")
                    feedback_errors_total.labels(component='approval_stats_bad_timestamp').inc()

            stats: Dict[str, Any] = {}
            if group_by_reviewer:
                reviewer_stats: Dict[str, Dict[str, int]] = collections.defaultdict(lambda: {"approved": 0, "denied": 0})
                for res in filtered_responses:
                    reviewer = res["response"].get("user_id", "unknown")
                    if res["response"].get("approved") is True:
                        reviewer_stats[reviewer]["approved"] += 1
                    else:
                        reviewer_stats[reviewer]["denied"] += 1
                stats["by_reviewer"] = dict(reviewer_stats)

            if group_by_decision_type:
                decision_type_stats: Dict[str, Dict[str, int]] = collections.defaultdict(lambda: {"approved": 0, "denied": 0})
                for res in filtered_responses:
                    decision_id = res.get("decision_id")
                    request = requests_map.get(decision_id, {})
                    decision_action = request.get("context", {}).get("action", "unknown_action")
                    
                    if res["response"].get("approved") is True:
                        decision_type_stats[decision_action]["approved"] += 1
                    else:
                        decision_type_stats[decision_action]["denied"] += 1
                stats["by_decision_type"] = dict(decision_type_stats)

            approval_times: List[float] = []
            for req in requests_map.values():
                req_time_str = req.get("request_start_time_utc")
                res_time_str = req.get("resolution_timestamp")
                
                if req_time_str and res_time_str:
                    try:
                        req_dt = datetime.fromisoformat(req_time_str)
                        res_dt = datetime.fromisoformat(res_time_str)
                        
                        if (start_date is None or req_dt >= start_date) and \
                           (end_date is None or req_dt <= end_date):
                            approval_times.append((res_dt - req_dt).total_seconds())
                    except ValueError as ve:
                        logger.warning(f"Skipping approval time calculation due to invalid timestamp format for decision_id {req.get('decision_id')}: {ve}")
                        feedback_errors_total.labels(component='approval_stats_bad_time_calc').inc()

            if approval_times:
                stats["approval_times"] = {
                    "median_seconds": statistics.median(approval_times),
                    "mean_seconds": statistics.mean(approval_times),
                    "total_resolved": len(approval_times)
                }
            else:
                stats["approval_times"] = {"median_seconds": 0.0, "mean_seconds": 0.0, "total_resolved": 0}
            
            return stats
        except Exception as e:
            logger.error(f"Failed to get approval stats: {e}", exc_info=True)
            feedback_errors_total.labels(component='get_approval_stats_general_error').inc()
            return {"by_reviewer": {}, "by_decision_type": {}, "approval_times": {"median_seconds": 0.0, "mean_seconds": 0.0, "total_resolved": 0}}

    async def start_async_services(self):
        """Starts background tasks like metric purging."""
        logger.info("FeedbackManager starting async services...")
        self._purge_task = asyncio.create_task(self._purge_metrics_and_sync_loop())
        logger.info("FeedbackManager async services started.")

    async def stop_async_services(self):
        """Stops background tasks gracefully."""
        logger.info("FeedbackManager stopping async services.")
        if self._purge_task and not self._purge_task.done():
            self._purge_task.cancel()
            try:
                await self._purge_task
            except asyncio.CancelledError:
                logger.info("FeedbackManager purge task cancelled successfully.")
            except Exception as e:
                logger.error(f"Error while waiting for purge task to stop: {e}", exc_info=True)
        logger.info("FeedbackManager async services stopped.")

def check_permission(role: str, permission: str):
    from arbiter import PermissionManager
    from arbiter.config import ArbiterConfig
    permission_mgr = PermissionManager(ArbiterConfig())
    return permission_mgr.check_permission(role, permission)


# Register as a plugin
mock_registry.register(kind=MockPlugInKind.CORE_SERVICE, name="FeedbackManager", version="1.0.0", author="Arbiter Team")(FeedbackManager)

async def receive_human_feedback(feedback: Dict[str, Any]) -> None:
    """
    Plugin entry point for receiving human feedback.
    """
    config = ArbiterConfig()
    manager = FeedbackManager(config=config)
    await manager.connect_db()
    try:
        await manager.add_user_feedback(feedback)
    finally:
        await manager.disconnect_db()

# Only register if not already registered to avoid duplicate registration error
if not arbiter_registry.get_metadata(PlugInKind.CORE_SERVICE, "feedback_manager"):
    register(kind=PlugInKind.CORE_SERVICE, name="feedback_manager", version="1.0.0", author="Arbiter Team")(receive_human_feedback)
    logger.info("feedback_manager plugin registered successfully")
else:
    logger.info("feedback_manager plugin already registered, skipping registration")