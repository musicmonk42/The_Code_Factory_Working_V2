# D:\SFE\self_fixing_engineer\arbiter\policy\core.py

import asyncio
import logging
import json
import os
import re
import sys
import time
from typing import Dict, Any, Optional, List, Tuple, Awaitable, Callable
from datetime import datetime, timezone, timedelta
import threading
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import hashlib
import random

# Import the centralized tracer configuration
from arbiter.otel_config import get_tracer

# FIX: Corrected import paths for guardrails and plugins modules
from ..plugins.llm_client import LLMClient
from .config import ArbiterConfig, get_config
from .circuit_breaker import is_llm_policy_circuit_breaker_open, record_llm_policy_api_success, record_llm_policy_api_failure
from .metrics import policy_decision_total, policy_file_reload_count, policy_last_reload_timestamp, LLM_CALL_LATENCY, get_or_create_metric, Histogram, Counter
from guardrails.audit_log import audit_log_event_async as audit_log
from guardrails.compliance_mapper import load_compliance_map

try:
    import aiosqlite
    SQLLITE_AVAILABLE = True
except ImportError:
    SQLLITE_AVAILABLE = False
    logging.warning("aiosqlite not available. SQLiteClient functionality will be limited.")

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter('%(asctime)s | %(levelname)s | %(message)s'))
    logger.addHandler(handler)

# Get tracer using centralized configuration
tracer = get_tracer("arbiter_policy_core")

PolicyRuleCallable = Callable[[str, str, Optional[str], Optional[Any]], Awaitable[Tuple[bool, str]]]

SQLITE_QUERY_LATENCY = get_or_create_metric(
    Histogram, 'sqlite_query_latency_seconds', 'Latency of SQLite database queries',
    labelnames=('operation',), buckets=(0.001, 0.01, 0.1, 0.5, 1, 2, 5)
)

POLICY_REFRESH_STATE_TRANSITIONS = get_or_create_metric(
    Counter, 'policy_refresh_state_transitions_total',
    'Total state transitions for policy refresh task (pause/resume)',
    labelnames=('state',)
)

POLICY_UPDATE_OUTCOMES = get_or_create_metric(
    Counter, 'policy_update_outcomes_total',
    'Total outcomes of policy update attempts',
    labelnames=('result',)
)

SQLITE_CLOSE_ERRORS = get_or_create_metric(
    Counter, 'sqlite_close_errors_total',
    'Total errors during SQLite connection closure',
    labelnames=('error_type',)
)

AUDIT_LOG_ERRORS = get_or_create_metric(
    Counter, 'audit_log_errors_total',
    'Total errors during audit logging',
    labelnames=('error_type',)
)

POLICY_REFRESH_ERRORS = get_or_create_metric(
    Counter, 'policy_refresh_errors_total',
    'Total errors during policy refresh task',
    labelnames=('error_type',)
)

POLICY_ENGINE_INIT_ERRORS = get_or_create_metric(
    Counter, 'policy_engine_init_errors_total',
    'Total errors during PolicyEngine initialization',
    labelnames=('error_type',)
)

POLICY_ENGINE_RESET_ERRORS = get_or_create_metric(
    Counter, 'policy_engine_reset_errors_total',
    'Total errors during PolicyEngine reset',
    labelnames=('error_type',)
)

class SQLiteClient:
    """Manages SQLite database interactions for feedback storage."""
    def __init__(self, db_file: str = "feedback.db"):
        if not SQLLITE_AVAILABLE:
            raise ImportError("aiosqlite is required for SQLiteClient.")
        self.db_file = db_file
        self._conn: Optional[aiosqlite.Connection] = None
        self._last_query_time: float = 0.0
        try:
            self._query_interval: float = get_config().CIRCUIT_BREAKER_MIN_OPERATION_INTERVAL
            if not isinstance(self._query_interval, (int, float)) or self._query_interval <= 0:
                raise ValueError("CIRCUIT_BREAKER_MIN_OPERATION_INTERVAL must be a positive number")
        except AttributeError:
            logger.warning("CIRCUIT_BREAKER_MIN_OPERATION_INTERVAL not found in ArbiterConfig. Using default: 30.0")
            self._query_interval = 30.0
        
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(Exception),
        before_sleep=lambda retry_state: logger.debug(f"Retrying SQLite connection: attempt {retry_state.attempt_number}")
    )
    async def connect(self):
        """Connects to SQLite database with retry logic."""
        with tracer.start_as_current_span("sqlite_connect", attributes={"db_file": self.db_file}) as span:
            if not os.path.isabs(self.db_file):
                raise ValueError(f"Invalid db_file: {self.db_file} must be an absolute path")
            if self._conn is None:
                self._conn = await aiosqlite.connect(self.db_file)
                await self._init_db()
                span.set_attribute("connection_status", "success")
            logger.info(f"SQLiteClient connected to database: {self.db_file}")

    async def _init_db(self):
        if self._conn:
            await self._conn.execute("""
                CREATE TABLE IF NOT EXISTS feedback (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    data JSON NOT NULL,
                    timestamp TEXT NOT NULL
                )
            """)
            await self._conn.commit()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(Exception),
        before_sleep=lambda retry_state: logger.debug(f"Retrying save_feedback_entry: attempt {retry_state.attempt_number}")
    )
    async def save_feedback_entry(self, entry: Dict[str, Any]):
        """Saves a feedback entry with rate-limiting and JSON sanitization."""
        with tracer.start_as_current_span("save_feedback_entry", attributes={"entry_id": entry.get("id")}) as span:
            start_time = time.monotonic()
            current_time = time.monotonic()
            if current_time - self._last_query_time < self._query_interval:
                await asyncio.sleep(self._query_interval - (current_time - self._last_query_time))
            entry_copy = entry.copy()
            if not isinstance(entry_copy, dict):
                span.record_exception(ValueError("Entry must be a dictionary"))
                raise ValueError("Entry must be a dictionary")
            if "timestamp" not in entry_copy:
                entry_copy["timestamp"] = datetime.now(timezone.utc).isoformat()
            if "id" not in entry_copy:
                entry_copy["id"] = hashlib.md5(json.dumps(entry_copy, sort_keys=True).encode('utf-8') + str(random.random()).encode('utf-8')).hexdigest()
            if "type" not in entry_copy or not isinstance(entry_copy["type"], str):
                entry_copy["type"] = "unknown"
                span.set_attribute("type_status", "defaulted")
            if self._conn:
                await self._conn.execute("""
                    INSERT OR REPLACE INTO feedback (id, type, data, timestamp)
                    VALUES (?, ?, ?, ?)
                """, (entry_copy["id"], entry_copy["type"], json.dumps(entry_copy, default=str), entry_copy["timestamp"]))
                await self._conn.commit()
                self._last_query_time = time.monotonic()
                span.set_attribute("save_status", "success")
            logger.debug(f"SQLiteClient: Saved feedback entry {entry_copy.get('id')}")
            SQLITE_QUERY_LATENCY.labels(operation='save').observe(time.monotonic() - start_time)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(Exception),
        before_sleep=lambda retry_state: logger.debug(f"Retrying get_feedback_entries: attempt {retry_state.attempt_number}")
    )
    async def get_feedback_entries(self, query: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Retrieves feedback entries with validated query parameters."""
        with tracer.start_as_current_span("get_feedback_entries", attributes={"query": str(query)}) as span:
            if query is not None and not isinstance(query, dict):
                span.record_exception(ValueError("Query must be a dictionary"))
                raise ValueError("Query must be a dictionary")
            start_time = time.monotonic()
            if self._conn:
                if not query:
                    cursor = await self._conn.execute("SELECT data FROM feedback")
                else:
                    where_clauses = []
                    params = []
                    for k, v in query.items():
                        if v is not None and isinstance(k, str) and isinstance(v, (str, int, float, bool)):
                            where_clauses.append(f"json_extract(data, '$.{k}') = ?")
                            params.append(v)
                        else:
                            span.record_exception(ValueError(f"Invalid query key or value: {k}={v}"))
                            raise ValueError(f"Invalid query key or value: {k}={v}")
                    where_sql = " AND ".join(where_clauses)
                    cursor = await self._conn.execute(f"SELECT data FROM feedback WHERE {where_sql}", params)
                rows = await cursor.fetchall()
                SQLITE_QUERY_LATENCY.labels(operation='get').observe(time.monotonic() - start_time)
                return [json.loads(row[0]) for row in rows]
            return []

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(Exception),
        before_sleep=lambda retry_state: logger.debug(f"Retrying update_feedback_entry: attempt {retry_state.attempt_number}")
    )
    async def update_feedback_entry(self, query: Dict[str, Any], updates: Dict[str, Any]) -> bool:
        """Updates feedback entries with validated query and update parameters."""
        with tracer.start_as_current_span("update_feedback_entry", attributes={"query": str(query)}) as span:
            if not isinstance(query, dict) or not isinstance(updates, dict):
                span.record_exception(ValueError("Query and updates must be dictionaries"))
                raise ValueError("Query and updates must be dictionaries")
            start_time = time.monotonic()
            if self._conn:
                where_clauses = []
                params = []
                for k, v in query.items():
                    if v is not None and isinstance(k, str) and isinstance(v, (str, int, float, bool)):
                        where_clauses.append(f"json_extract(data, '$.{k}') = ?")
                        params.append(v)
                    else:
                        span.record_exception(ValueError(f"Invalid query key or value: {k}={v}"))
                        raise ValueError(f"Invalid query key or value: {k}={v}")
                where_sql = " AND ".join(where_clauses)
                cursor = await self._conn.execute(f"SELECT id, data FROM feedback WHERE {where_sql}", params)
                rows = await cursor.fetchall()
                if not rows: return False
                updated = False
                for row in rows:
                    entry_id, data_str = row
                    data = json.loads(data_str)
                    for k, v in updates.items():
                        if not isinstance(k, str) or not isinstance(v, (str, int, float, bool, dict, list)):
                            span.record_exception(ValueError(f"Invalid update key or value: {k}={v}"))
                            raise ValueError(f"Invalid update key or value: {k}={v}")
                    data.update(updates)
                    await self._conn.execute("""
                        UPDATE feedback SET data = ? WHERE id = ?
                    """, (json.dumps(data), entry_id))
                    updated = True
                await self._conn.commit()
                SQLITE_QUERY_LATENCY.labels(operation='update').observe(time.monotonic() - start_time)
                return updated
            return False

    async def close(self):
        """Closes the SQLite database connection."""
        with tracer.start_as_current_span("sqlite_close", attributes={"db_file": self.db_file}) as span:
            try:
                if self._conn:
                    await self._conn.close()
                    self._conn = None
                    logger.info(f"SQLiteClient closed connection to {self.db_file}")
                    span.set_attribute("close_status", "success")
            except Exception as e:
                logger.error(f"Error closing SQLite connection: {e}")
                SQLITE_CLOSE_ERRORS.labels(error_type='close_failed').inc()
                span.record_exception(e)
                span.set_attribute("close_status", "failed")

class BasicDecisionOptimizer:
    """Fallback decision optimizer for trust score computation."""
    def __init__(self, settings: Optional[Dict] = None):
        self.settings = settings or {}
        self.score_rules = self.settings.get("score_rules", {
            "login_attempts_penalty": -0.2,
            "device_trusted_bonus": 0.3,
            "recent_login_bonus": 0.1,
            "admin_user_bonus": 0.2,
            "default_score": 0.5
        })
        required_keys = ["login_attempts_penalty", "device_trusted_bonus", "recent_login_bonus", "admin_user_bonus", "default_score"]
        for key in required_keys:
            if key not in self.score_rules or not isinstance(self.score_rules[key], (int, float)):
                logger.error(f"Invalid score_rules: missing or invalid {key}")
                raise ValueError(f"Invalid score_rules: {key} must be a number")
        if not 0.0 <= self.score_rules["default_score"] <= 1.0:
            logger.error(f"Invalid default_score: {self.score_rules['default_score']} must be between 0.0 and 1.0")
            raise ValueError(f"Invalid default_score: {self.score_rules['default_score']}")
        self._last_score_time: float = 0.0
        self._score_interval: float = get_config().CIRCUIT_BREAKER_MIN_OPERATION_INTERVAL

    async def compute_trust_score(self, auth_context: Dict, user_id: Optional[str]) -> float:
        """Computes trust score based on configurable rules with rate-limiting."""
        with tracer.start_as_current_span("compute_trust_score", attributes={"user_id": user_id}) as span:
            current_time = time.monotonic()
            if current_time - self._last_score_time < self._score_interval:
                await asyncio.sleep(self._score_interval - (current_time - self._last_score_time))
            if not isinstance(auth_context, dict):
                span.record_exception(ValueError("auth_context must be a dictionary"))
                raise ValueError("auth_context must be a dictionary")
            if "login_attempts" in auth_context and not isinstance(auth_context["login_attempts"], int):
                span.record_exception(ValueError("login_attempts must be an integer"))
                raise ValueError("login_attempts must be an integer")
            if "device_trusted" in auth_context and not isinstance(auth_context["device_trusted"], bool):
                span.record_exception(ValueError("device_trusted must be a boolean"))
                raise ValueError("device_trusted must be a boolean")
            score = self.score_rules["default_score"]
            if "login_attempts" in auth_context and auth_context["login_attempts"] > 3:
                score += self.score_rules["login_attempts_penalty"]
            if "device_trusted" in auth_context and auth_context["device_trusted"]:
                score += self.score_rules["device_trusted_bonus"]
            if "last_login" in auth_context:
                try:
                    last_login = datetime.fromisoformat(auth_context["last_login"])
                    if (datetime.now(timezone.utc) - last_login) < timedelta(days=7):
                        score += self.score_rules["recent_login_bonus"]
                except ValueError:
                    logger.warning(f"Invalid 'last_login' timestamp: {auth_context['last_login']}")
                    span.record_exception(ValueError("Invalid last_login timestamp"))
            if user_id and user_id.startswith("admin"):
                score += self.score_rules["admin_user_bonus"]
            self._last_score_time = time.monotonic()
            span.set_attribute("trust_score", score)
            return max(0.0, min(1.0, score))

class PolicyEngine:
    def __init__(self, arbiter_instance: Any, config: ArbiterConfig):
        with tracer.start_as_current_span("policy_engine_init") as span:
            if not isinstance(config, ArbiterConfig):
                raise ValueError("Config must be an instance of ArbiterConfig")
            if not hasattr(arbiter_instance, 'plugin_registry'):
                logger.warning("Arbiter instance lacks plugin_registry; some functionality may be limited")
                span.set_attribute("arbiter_status", "incomplete")
            required_config_keys = ["POLICY_REFRESH_INTERVAL_SECONDS", "LLM_PROVIDER", "LLM_MODEL", "DECISION_OPTIMIZER_SETTINGS", "CIRCUIT_BREAKER_MIN_OPERATION_INTERVAL", "VALID_DOMAIN_PATTERN", "POLICY_CONFIG_FILE_PATH", "POLICY_PAUSE_POLLING_INTERVAL"]
            for key in required_config_keys:
                if not hasattr(config, key):
                    raise ValueError(f"Missing required config key: {key}")
            if not isinstance(config.POLICY_REFRESH_INTERVAL_SECONDS, (int, float)) or config.POLICY_REFRESH_INTERVAL_SECONDS <= 0:
                raise ValueError("POLICY_REFRESH_INTERVAL_SECONDS must be a positive number")
            if not isinstance(config.LLM_PROVIDER, str) or config.LLM_PROVIDER not in {"openai", "anthropic", "gemini"}:
                raise ValueError("LLM_PROVIDER must be one of 'openai', 'anthropic', 'gemini'")
            if not isinstance(config.CIRCUIT_BREAKER_MIN_OPERATION_INTERVAL, (int, float)) or config.CIRCUIT_BREAKER_MIN_OPERATION_INTERVAL <= 0:
                raise ValueError("CIRCUIT_BREAKER_MIN_OPERATION_INTERVAL must be a positive number")
            if not isinstance(config.POLICY_CONFIG_FILE_PATH, str) or not os.path.isabs(config.POLICY_CONFIG_FILE_PATH):
                raise ValueError("POLICY_CONFIG_FILE_PATH must be an absolute path")
            if not isinstance(config.POLICY_PAUSE_POLLING_INTERVAL, (int, float)) or config.POLICY_PAUSE_POLLING_INTERVAL <= 0:
                raise ValueError("POLICY_PAUSE_POLLING_INTERVAL must be a positive number")
            
            self.arbiter = arbiter_instance
            self.config = config
            self._policies: Dict[str, Any] = {}
            self._compliance_controls: Dict[str, Any] = {}
            self._custom_rules: List[PolicyRuleCallable] = []
            self._policy_refresh_task: Optional[asyncio.Task] = None
            self._stop_event = asyncio.Event()
            self._lock = threading.Lock()
            self._pause_policy_refresh: bool = False
            self._last_pause_state: bool = False
            self._last_llm_call_time: float = 0.0
            self._llm_call_interval: float = config.CIRCUIT_BREAKER_MIN_OPERATION_INTERVAL
            self._last_trust_score_time: float = 0.0
            self._trust_score_interval: float = config.CIRCUIT_BREAKER_MIN_OPERATION_INTERVAL
            
            self._load_policies_from_file()
            self._load_compliance_controls()
            self.register_custom_rule(self.trust_score_rule)
            span.set_attribute("init_status", "success")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((ConnectionError, TimeoutError, ImportError)),
        before_sleep=lambda retry_state: logger.debug(f"Retrying LLMClient creation: attempt {retry_state.attempt_number}")
    )
    def _create_llm_client(self):
        """Creates an LLMClient instance with retry logic."""
        return LLMClient(
            provider=self.config.LLM_PROVIDER,
            api_key=get_config().get_api_key_for_provider(self.config.LLM_PROVIDER),
            model=self.config.LLM_MODEL
        )

    async def _call_llm_for_policy_evaluation(self, prompt: str) -> Tuple[str, str, float]:
        """Evaluates a policy using an LLM client with circuit breaker, auditing, and rate-limiting."""
        with tracer.start_as_current_span("call_llm_for_policy_evaluation", attributes={"prompt": self._sanitize_prompt(prompt)}) as span:
            if not isinstance(prompt, str) or not prompt:
                span.record_exception(ValueError("Prompt must be a non-empty string"))
                return "NO", "Invalid prompt: must be a non-empty string.", 0.0
            if len(prompt) > 10000:
                span.record_exception(ValueError("Prompt exceeds maximum length of 10000 characters"))
                return "NO", "Prompt too long.", 0.0
            current_time = time.monotonic()
            if current_time - self._last_llm_call_time < self._llm_call_interval:
                await asyncio.sleep(self._llm_call_interval - (current_time - self._last_llm_call_time))
            if await is_llm_policy_circuit_breaker_open():
                span.set_attribute("circuit_breaker_status", "open")
                return "NO", "Circuit breaker open for LLM provider.", 0.0
            
            llm_provider = self.config.LLM_PROVIDER
            if not get_config().get_api_key_for_provider(llm_provider):
                reason = f"LLM evaluation skipped: API key missing for '{llm_provider}'."
                span.set_attribute("api_key_status", "missing")
                return "NO", reason, 0.0
            
            start_time = time.monotonic()
            try:
                client = self._create_llm_client()
                llm_response = await asyncio.wait_for(
                    client.generate_text(prompt),
                    timeout=self.config.LLM_API_TIMEOUT_SECONDS
                )
                self._last_llm_call_time = time.monotonic()
                LLM_CALL_LATENCY.labels(provider=llm_provider).observe(time.monotonic() - start_time)
                await record_llm_policy_api_success()
                decision, reason, trust_score = self._validate_llm_policy_output(
                    llm_response, self._policies.get("llm_rules", {}).get("valid_responses", ["YES", "NO"])
                )
                span.set_attribute("llm_response", llm_response)
                span.set_attribute("llm.decision", decision)
                span.set_attribute("llm.reason", reason)
                span.set_attribute("llm.trust_score", trust_score)
                return decision, reason, trust_score
            except asyncio.TimeoutError:
                await record_llm_policy_api_failure()
                logger.error(f"LLM call timed out after {self.config.LLM_API_TIMEOUT_SECONDS} seconds")
                span.record_exception(asyncio.TimeoutError("LLM call timed out"))
                return "NO", f"LLM call timed out after {self.config.LLM_API_TIMEOUT_SECONDS} seconds", 0.0
            except Exception as e:
                await record_llm_policy_api_failure()
                LLM_CALL_LATENCY.labels(provider=llm_provider).observe(time.monotonic() - start_time)
                logger.error(f"Error calling LLM for policy evaluation: {e}")
                span.record_exception(e)
                raise Exception(f"LLM API call failed: {e}")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(Exception),
        before_sleep=lambda retry_state: logger.debug(f"Retrying policy load: attempt {retry_state.attempt_number}")
    )
    def _load_policies_from_file(self):
        """Loads policies from file with retry logic."""
        with tracer.start_as_current_span("load_policies_from_file", attributes={"file_path": self.config.POLICY_CONFIG_FILE_PATH}) as span:
            try:
                policy_dir = os.path.dirname(self.config.POLICY_CONFIG_FILE_PATH)
                if policy_dir and not os.path.exists(policy_dir): os.makedirs(policy_dir, exist_ok=True)
                if not os.path.exists(self.config.POLICY_CONFIG_FILE_PATH):
                    self._policies = self._get_default_policies()
                    with open(self.config.POLICY_CONFIG_FILE_PATH, 'w', encoding='utf-8') as f: json.dump(self._policies, f, indent=4)
                    logger.info(f"Created default policy file at {self.config.POLICY_CONFIG_FILE_PATH}")
                    return
                with open(self.config.POLICY_CONFIG_FILE_PATH, 'r', encoding='utf-8') as f:
                    loaded_policies = json.load(f)
                    old_policies = self._policies.copy()
                    if not self.validate_policies(loaded_policies):
                        logger.warning("Invalid policy file. Loading default policies.")
                        self._policies = self._get_default_policies()
                        span.set_attribute("load_status", "invalid_file")
                    else:
                        self._policies = loaded_policies
                        span.set_attribute("load_status", "success")
                    if old_policies and self._policies != old_policies: asyncio.create_task(self._audit_policy_changes(old_policies, self._policies))
                    policy_file_reload_count.inc()
                    policy_last_reload_timestamp.set(datetime.now().timestamp())
                    logger.info(f"Policies reloaded from {self.config.POLICY_CONFIG_FILE_PATH}.")
            except FileNotFoundError:
                logger.warning("Policy file not found. Loading default policies.")
                self._policies = self._get_default_policies()
                span.set_attribute("load_status", "file_not_found")
            except json.JSONDecodeError as e:
                logger.error(f"Error parsing policy file: {e}. Loading default policies.")
                self._policies = self._get_default_policies()
                span.record_exception(e)
                span.set_attribute("load_status", "json_decode_error")
            except Exception as e:
                logger.error(f"Unexpected error loading policies: {e}. Loading default policies.")
                self._policies = self._get_default_policies()
                span.record_exception(e)
                span.set_attribute("load_status", "unexpected_error")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(Exception),
        before_sleep=lambda retry_state: logger.debug(f"Retrying compliance controls load: attempt {retry_state.attempt_number}")
    )
    def _load_compliance_controls(self):
        """Loads and validates compliance controls with retry logic."""
        with tracer.start_as_current_span("load_compliance_controls") as span:
            try:
                self._compliance_controls = load_compliance_map(self.config.POLICY_CONFIG_FILE_PATH)
                for control_id, control_info in self._compliance_controls.items():
                    if not isinstance(control_id, str) or not isinstance(control_info, dict):
                        raise ValueError(f"Invalid compliance control: {control_id}")
                    if not all(key in control_info for key in ["name", "status", "required"]):
                        raise ValueError(f"Missing required keys in compliance control: {control_id}")
                    if control_info["status"] not in {"enforced", "partially_enforced", "not_implemented"}:
                        raise ValueError(f"Invalid status for compliance control: {control_id}")
                logger.info(f"Loaded {len(self._compliance_controls)} compliance controls via compliance_mapper.")
                span.set_attribute("load_status", "success")
                span.set_attribute("control_count", len(self._compliance_controls))
            except Exception as e:
                logger.error(f"Error loading compliance controls via compliance_mapper: {e}", exc_info=True)
                self._compliance_controls = {}
                span.record_exception(e)
                span.set_attribute("load_status", "error")
                raise

    def _get_default_policies(self) -> Dict[str, Any]:
        """Returns default policy configuration."""
        # Define default policies for domains, users, LLM, trust rules, and custom rules
        return {
            "domain_rules": {
                # Restrict sensitive domains by default
                "compliance": {"allow": False, "reason": "Restricted by default"},
                "regulatory": {"allow": False, "reason": "Restricted by default"},
                # Allow financial transactions with role restrictions
                "financial_transactions": {
                    "allow": True,
                    "required_roles": ["admin", "auditor"],
                    "reason": "Requires specific roles"
                },
                # Allow user data with size and sensitive key restrictions
                "user_data": {
                    "allow": True,
                    "max_size_kb": 100,
                    "sensitive_keys": ["password", "ssn"],
                    "reason": "Size limited, sensitive keys trigger manual review"
                },
                # Allow authentication with trust scoring
                "authentication": {
                    "allow": True,
                    "required_roles": ["user", "admin", "explorer_user"],
                    "reason": "Trust scoring required",
                    "trust_score_threshold": 0.6,
                    "temporal_window_seconds": 86400
                },
                # Default rule for unspecified domains
                "*": {"allow": True, "reason": "Default policy for unspecified domains"}
            },
            "user_rules": {
                # Restrict guest users
                "guest": {"allow": False, "reason": "Guest users cannot auto-learn"},
                # Restrict explorer users from certain domains
                "explorer_user": {
                    "allow": True,
                    "restricted_domains": ["financial_transactions"],
                    "reason": "Explorer users have domain restrictions"
                },
                # Default rule for unspecified users
                "*": {"allow": True, "reason": "Default policy for unspecified users"}
            },
            # Configure LLM-based policy evaluation
            "llm_rules": {
                "enabled": True,
                "threshold": 0.8,
                "min_trust_score": 0.5,
                "prompt_template": "Given the new fact about domain '{domain}' with key '{key}' and value '{value}', is it safe and appropriate for automated learning without human review? Respond ONLY with 'YES' if safe and 'NO' if not, followed by a brief, concise reason (max 10 words). Example: YES, data is public. Or NO, sensitive data.",
                "valid_responses": ["YES", "NO"]
            },
            # Configure trust score rules
            "trust_rules": {
                "enabled": True,
                "threshold": 0.6,
                "reason": "Minimum trust score required",
                "temporal_window_seconds": 86400
            },
            # Enable custom Python rules by default
            "custom_python_rules_enabled": True
        }

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(Exception),
        before_sleep=lambda retry_state: logger.debug(f"Retrying custom rule registration: attempt {retry_state.attempt_number}")
    )
    def register_custom_rule(self, rule_func: PolicyRuleCallable):
        """Registers a custom policy rule with retry logic."""
        with self._lock:
            if not asyncio.iscoroutinefunction(rule_func): raise TypeError("Custom policy rule function must be an async coroutine.")
            self._custom_rules.append(rule_func)
            logger.info(f"Registered custom policy rule: {rule_func.__name__}")

    def reload_policies(self):
        with self._lock:
            self._load_policies_from_file()
            self._load_compliance_controls()

    async def apply_policy_update_from_evolution(self, proposed_policies: Dict[str, Any]) -> Tuple[bool, str]:
        """Applies policy updates from evolution strategy with auditing and metrics."""
        with self._lock:
            # Log update attempt
            logger.info("Attempting to apply policy update from evolution strategy.")
            # Validate proposed policies
            if not self.validate_policies(proposed_policies):
                reason = "Proposed policies are invalid. Update rejected."
                await self._audit_policy_decision("policy_adaptation", "N/A", "N/A", "evolution_strategy", proposed_policies, False, reason, control_tag="PC-1", is_compliant=False)
                policy_decision_total.labels(allowed='false', domain='policy_adaptation', user_type='evolution_strategy', reason_code='invalid_proposed_policies').inc()
                POLICY_UPDATE_OUTCOMES.labels(result='invalid_policies').inc()
                logger.warning(reason)
                return False, reason
            # Backup current policies
            old_policies = self._policies.copy()
            try:
                # Audit policy changes
                await self._audit_policy_changes(old_policies, proposed_policies, path="policy_adaptation")
                logger.info("Policy changes audited successfully.")
            except Exception as e:
                reason = f"Failed to audit policy changes: {e}. Update rejected to prevent unrecorded changes."
                await self._audit_policy_decision("policy_adaptation", "N/A", "N/A", "evolution_strategy", proposed_policies, False, reason, control_tag="PC-1", is_compliant=False)
                policy_decision_total.labels(allowed='false', domain='policy_adaptation', user_type='evolution_strategy', reason_code='audit_failure').inc()
                POLICY_UPDATE_OUTCOMES.labels(result='audit_failure').inc()
                logger.error(reason, exc_info=True)
                return False, reason
            # Apply and audit successful update
            self._policies = proposed_policies
            policy_file_reload_count.inc()
            policy_last_reload_timestamp.set(datetime.now().timestamp())
            logger.info("Policy update from evolution strategy applied successfully.")
            await self._audit_policy_decision("policy_adaptation", "N/A", "N/A", "evolution_strategy", proposed_policies, True, "Policy update applied successfully from evolution strategy.", control_tag="PC-1", is_compliant=True)
            policy_decision_total.labels(allowed='true', domain='policy_adaptation', user_type='evolution_strategy', reason_code='policy_applied').inc()
            POLICY_UPDATE_OUTCOMES.labels(result='success').inc()
            return True, "Policy update applied successfully."

    @staticmethod
    def validate_policies(policies: Dict[str, Any]) -> bool:
        """Validates policy structure and content."""
        if not isinstance(policies, dict):
            return False
        required_keys = ["domain_rules", "user_rules", "llm_rules", "custom_python_rules_enabled", "trust_rules"]
        for key in required_keys:
            if key not in policies:
                return False
            if not isinstance(policies[key], dict) and key != "custom_python_rules_enabled":
                return False
            if key == "custom_python_rules_enabled" and not isinstance(policies[key], bool):
                return False
        for domain, rule in policies["domain_rules"].items():
            if not isinstance(rule, dict) or "allow" not in rule or not isinstance(rule["allow"], bool):
                return False
        for user, rule in policies["user_rules"].items():
            if not isinstance(rule, dict) or "allow" not in rule or not isinstance(rule["allow"], bool):
                return False
        llm_rules = policies["llm_rules"]
        if not isinstance(llm_rules.get("enabled", False), bool) or not isinstance(llm_rules.get("prompt_template", ""), str):
            return False
        trust_rules = policies["trust_rules"]
        if not isinstance(trust_rules.get("enabled", False), bool) or not isinstance(trust_rules.get("threshold", 0.0), (int, float)):
            return False
        return True

    async def _audit_policy_changes(self, old_policies: Dict[str, Any], new_policies: Dict[str, Any], path: str = ""):
        for k, v in new_policies.items():
            current_path = f"{path}.{k}" if path else k
            if k not in old_policies:
                await self._audit_policy_decision("policy_change", "N/A", current_path, "system", None, True, f"Policy added: {current_path} = {json.dumps(v, default=str)}")
            elif isinstance(v, dict) and isinstance(old_policies.get(k), dict):
                await self._audit_policy_changes(old_policies[k], v, current_path)
            elif v != old_policies[k]:
                await self._audit_policy_decision("policy_change", "N/A", current_path, "system", None, True, f"Policy changed: {current_path} from {json.dumps(old_policies[k], default=str)} to {json.dumps(v, default=str)}")
        for k, v in old_policies.items():
            if k not in new_policies:
                removed_path = f"{path}.{k}" if path else k
                await self._audit_policy_decision("policy_change", "N/A", removed_path, "system", None, True, f"Policy removed: {removed_path} = {json.dumps(v, default=str)}")

    async def _enforce_compliance(self, action_name: str, control_tag: Optional[str]) -> Tuple[bool, str]:
        """Enforces compliance controls for a given action."""
        with tracer.start_as_current_span("enforce_compliance", attributes={"action_name": action_name, "control_tag": control_tag}) as span:
            # Check if action has a mapped compliance control
            if not control_tag:
                span.set_attribute("compliance_status", "undefined")
                return False, f"Action '{action_name}' is not mapped to any compliance control. Blocking due to undefined compliance."
            # Retrieve control information
            control_info = self._compliance_controls.get(control_tag)
            if not control_info:
                span.set_attribute("compliance_status", "unknown")
                return False, f"Compliance control '{control_tag}' for action '{action_name}' is not defined. Blocking due to unknown control."
            status = control_info.get("status")
            required = control_info.get("required", True)
            # Enforce required controls
            if required and status == "not_implemented":
                span.set_attribute("compliance_status", "not_implemented")
                return False, f"Compliance control '{control_tag}' for action '{action_name}' is required but marked 'not_implemented'."
            if required and status == "partially_enforced":
                span.set_attribute("compliance_status", "partially_enforced")
                return False, f"Compliance control '{control_tag}' for action '{action_name}' is required but only 'partially_enforced'."
            if required and status != "enforced":
                span.set_attribute("compliance_status", status)
                return False, f"Compliance control '{control_tag}' for action '{action_name}' is required but not 'enforced'. Current status: '{status}'"
            # Allow optional controls with warnings
            if not required and status in ["not_implemented", "partially_enforced"]:
                logger.warning(f"Optional compliance control '{control_tag}' for action '{action_name}' is '{status}'. Proceeding but compliance may be incomplete.")
                span.set_attribute("compliance_status", status)
                return True, f"Optional control '{control_tag}' is '{status}' (allowed to proceed)."
            span.set_attribute("compliance_status", "enforced")
            return True, f"Compliance control '{control_tag}' is 'enforced' for action '{action_name}'."

    async def should_auto_learn(self, domain: str, key: str, user_id: Optional[str], value: Optional[Any] = None) -> Tuple[bool, str]:
        with tracer.start_as_current_span("should_auto_learn", attributes={"domain": domain, "key": key, "user_id": user_id}) as span:
            if not isinstance(domain, str) or not re.match(self.config.VALID_DOMAIN_PATTERN, domain):
                reason = f"Invalid domain name format: '{domain}'."
                span.record_exception(ValueError(reason))
                await self._audit_policy_decision("should_auto_learn", domain, key, user_id, value, False, reason, control_tag=None)
                policy_decision_total.labels(allowed='false', domain=domain, user_type=user_id, reason_code='invalid_domain_format').inc()
                return False, reason
            
            if not isinstance(key, str) or not key:
                reason = f"Invalid key: {key}."
                span.record_exception(ValueError(reason))
                await self._audit_policy_decision("should_auto_learn", domain, key, user_id, value, False, reason, control_tag=None)
                policy_decision_total.labels(allowed='false', domain=domain, user_type=user_id, reason_code='invalid_key').inc()
                return False, reason
            
            if user_id is not None and not isinstance(user_id, str):
                reason = f"Invalid user_id: {user_id}."
                span.record_exception(ValueError(reason))
                await self._audit_policy_decision("should_auto_learn", domain, key, user_id, value, False, reason, control_tag=None)
                policy_decision_total.labels(allowed='false', domain=domain, user_type=user_id, reason_code='invalid_user_id').inc()
                return False, reason
            
            effective_user_id_str = user_id if user_id is not None else "anonymous"

            with self._lock:
                domain_rule = self._policies["domain_rules"].get(domain, self._policies["domain_rules"].get("*", {"allow": self.config.DEFAULT_AUTO_LEARN_POLICY, "reason": "Default"}))
                domain_control_tag = domain_rule.get("control_tag")
                if not domain_rule["allow"]:
                    reason = f"Domain rule denied: {domain_rule['reason']}"
                    await self._audit_policy_decision("should_auto_learn", domain, key, effective_user_id_str, value, False, reason, control_tag=domain_control_tag)
                    policy_decision_total.labels(allowed='false', domain=domain, user_type=effective_user_id_str, reason_code='domain_rule_denied').inc()
                    return False, reason
                
                if "required_roles" in domain_rule and effective_user_id_str != "anonymous":
                    user_roles = await self._get_user_roles(effective_user_id_str)
                    if not any(role in user_roles for role in domain_rule["required_roles"]):
                        reason = f"Domain '{domain}' requires roles {domain_rule['required_roles']}, but user has {user_roles}."
                        await self._audit_policy_decision("should_auto_learn", domain, key, effective_user_id_str, value, False, reason, control_tag=domain_control_tag)
                        policy_decision_total.labels(allowed='false', domain=domain, user_type=effective_user_id_str, reason_code='insufficient_roles').inc()
                        return False, reason
                
                if domain_rule.get("max_size_kb") is not None and value is not None:
                    try:
                        value_size_kb = len(json.dumps(value, default=str).encode('utf-8')) / 1024
                        if value_size_kb > domain_rule["max_size_kb"]:
                            reason = f"Value size ({value_size_kb:.2f} KB) exceeds max for domain '{domain}'."
                            await self._audit_policy_decision("should_auto_learn", domain, key, effective_user_id_str, value, False, reason, control_tag=domain_control_tag)
                            policy_decision_total.labels(allowed='false', domain=domain, user_type=effective_user_id_str, reason_code='size_exceeded').inc()
                            return False, reason
                    except TypeError:
                        reason = f"Value for domain '{domain}' is not JSON serializable."
                        await self._audit_policy_decision("should_auto_learn", domain, key, effective_user_id_str, value, False, reason, control_tag=domain_control_tag)
                        policy_decision_total.labels(allowed='false', domain=domain, user_type=effective_user_id_str, reason_code='serialization_error').inc()
                        return False, reason
                
                if "sensitive_keys" in domain_rule and value is not None and isinstance(value, dict):
                    if any(skey in value for skey in domain_rule["sensitive_keys"]):
                        reason = f"Value contains sensitive key(s) {', '.join(k for k in domain_rule['sensitive_keys'] if k in value)}."
                        await self._audit_policy_decision("should_auto_learn", domain, key, effective_user_id_str, value, False, reason, control_tag=domain_control_tag)
                        policy_decision_total.labels(allowed='false', domain=domain, user_type=effective_user_id_str, reason_code='sensitive_content').inc()
                        return False, reason
                
                user_rule = self._policies["user_rules"].get(effective_user_id_str, self._policies["user_rules"].get("*", {"allow": True, "reason": "Default"}))
                user_control_tag = user_rule.get("control_tag")
                if not user_rule["allow"]:
                    reason = f"User rule denied: {user_rule['reason']}"
                    await self._audit_policy_decision("should_auto_learn", domain, key, effective_user_id_str, value, False, reason, control_tag=user_control_tag)
                    policy_decision_total.labels(allowed='false', domain=domain, user_type=effective_user_id_str, reason_code='user_rule_denied').inc()
                    return False, reason
                
                if "restricted_domains" in user_rule and domain in user_rule["restricted_domains"]:
                    reason = f"User '{effective_user_id_str}' is restricted from domain '{domain}'."
                    await self._audit_policy_decision("should_auto_learn", domain, key, effective_user_id_str, value, False, reason, control_tag=user_control_tag)
                    policy_decision_total.labels(allowed='false', domain=domain, user_type=effective_user_id_str, reason_code='user_restricted_domain').inc()
                    return False, reason
                
                llm_rules = self._policies.get("llm_rules", {})
                llm_control_tag = llm_rules.get("control_tag")
                if llm_rules.get("enabled", False) and self.config.LLM_POLICY_EVALUATION_ENABLED:
                    try:
                        llm_prompt = llm_rules["prompt_template"].format(domain=domain, key=key, value=value)
                        sanitized_prompt = self._sanitize_prompt(llm_prompt)
                        
                        llm_response_decision, llm_reason, llm_trust_score = await self._call_llm_for_policy_evaluation(prompt=sanitized_prompt)
                        
                        if llm_trust_score < self.config.LLM_POLICY_MIN_TRUST_SCORE:
                            reason = f"LLM trust score {llm_trust_score:.2f} too low. Reason: {llm_reason}"
                            await self._audit_policy_decision("should_auto_learn", domain, key, effective_user_id_str, value, False, reason, control_tag=llm_control_tag)
                            policy_decision_total.labels(allowed='false', domain=domain, user_type=effective_user_id_str, reason_code='llm_low_trust_score').inc()
                            return False, reason
                        
                        if llm_response_decision == "YES":
                            logger.debug(f"LLM allowed for {domain}/{key}. Reason: {llm_reason}")
                        else:
                            reason = f"LLM denied: {llm_reason}"
                            await self._audit_policy_decision("should_auto_learn", domain, key, effective_user_id_str, value, False, reason, control_tag=llm_control_tag)
                            policy_decision_total.labels(allowed='false', domain=domain, user_type=effective_user_id_str, reason_code='llm_denied').inc()
                            return False, reason
                    except Exception as e:
                        reason = f"LLM evaluation error: {e}"
                        await self._audit_policy_decision("should_auto_learn", domain, key, effective_user_id_str, value, False, reason, control_tag=llm_control_tag)
                        policy_decision_total.labels(allowed='false', domain=domain, user_type=effective_user_id_str, reason_code='llm_error').inc()
                        return False, reason

            if self._policies.get("custom_python_rules_enabled", True):
                for custom_rule_func in self._custom_rules:
                    try:
                        allowed, reason = await custom_rule_func(domain, key, user_id, value)
                        if not allowed:
                            await self._audit_policy_decision("should_auto_learn", domain, key, effective_user_id_str, value, False, f"Custom rule '{custom_rule_func.__name__}' denied: {reason}", control_tag=None)
                            policy_decision_total.labels(allowed='false', domain=domain, user_type=effective_user_id_str, reason_code='custom_rule_denied').inc()
                            return False, f"Custom rule '{custom_rule_func.__name__}' denied: {reason}"
                    except Exception as e:
                        reason = f"Error in custom rule '{custom_rule_func.__name__}': {e}"
                        await self._audit_policy_decision("should_auto_learn", domain, key, effective_user_id_str, value, False, reason, control_tag=None)
                        policy_decision_total.labels(allowed='false', domain=domain, user_type=effective_user_id_str, reason_code='custom_rule_error').inc()
                        return False, reason
            
            final_control_tag_for_action = domain_control_tag or user_control_tag or llm_control_tag
            action_id_for_compliance_check = f"{domain}:{key}"

            compliance_check_passed, compliance_reason = await self._enforce_compliance(
                action_id_for_compliance_check,
                final_control_tag_for_action
            )
            
            if not compliance_check_passed:
                final_reason = f"Compliance enforcement blocked action: {compliance_reason}"
                await self._audit_policy_decision(
                    "should_auto_learn", domain, key, effective_user_id_str, value, False,
                    final_reason,
                    control_tag=final_control_tag_for_action,
                    is_compliant=False
                )
                policy_decision_total.labels(allowed='false', domain=domain, user_type=effective_user_id_str, reason_code='compliance_enforcement_blocked').inc()
                return False, final_reason
            
            await self._audit_policy_decision(
                "should_auto_learn", domain, key, effective_user_id_str, value, True,
                "Allowed by all policies and compliance checks.",
                control_tag=final_control_tag_for_action,
                is_compliant=True
            )
            policy_decision_total.labels(allowed='true', domain=domain, user_type=effective_user_id_str, reason_code='allowed').inc()
            return True, "Allowed by policies and compliance."

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(Exception),
        before_sleep=lambda retry_state: logger.debug(f"Retrying audit_policy_decision: attempt {retry_state.attempt_number}")
    )
    async def _audit_policy_decision(self, decision_type: str, domain: str, key: str, user_id: Optional[str], value: Optional[Any], allowed: bool, reason: str, control_tag: Optional[str] = None, is_compliant: Optional[bool] = None):
        """Logs policy decisions to audit log with error tracking and retry logic."""
        audit_entry_details = {
            "decision_type": decision_type,
            "domain": domain,
            "key": key,
            "user_id": user_id,
            "allowed": allowed,
            "reason": reason,
            "value_summary": str(value)[:200] + "..." if value is not None and len(str(value)) > 200 else str(value)
        }
        if control_tag: audit_entry_details["control_tag"] = control_tag
        if is_compliant is not None: audit_entry_details["is_compliant"] = is_compliant

        try:
            await audit_log(
                event_type=f"policy:{decision_type}",
                message=reason,
                data=audit_entry_details,
                agent_id="policy_engine",
                correlation_id=None,
                compliance_control_id=control_tag,
                is_compliant=is_compliant
            )
            logger.debug(f"Audited: {decision_type} - Allowed: {allowed} - Reason: {reason} - Control Tag: {control_tag} - Compliant: {is_compliant}")
        except Exception as e:
            logger.error(f"Failed to write audit entry via audit_log: {e}")
            AUDIT_LOG_ERRORS.labels(error_type='audit_failed').inc()
            raise

    async def _get_user_roles(self, user_id: str) -> List[str]:
        """Retrieves roles for a given user ID from ArbiterConfig."""
        with tracer.start_as_current_span("get_user_roles", attributes={"user_id": user_id}) as span:
            try:
                role_mappings = self.config.ROLE_MAPPINGS or {
                    "admin": ["admin", "user", "explorer_user"],
                    "auditor": ["auditor", "user"],
                    "explorer_user": ["explorer_user", "user"],
                    "guest": ["guest"],
                    "*": ["user"]
                }
            except AttributeError:
                logger.warning("ROLE_MAPPINGS not found in ArbiterConfig. Using default mappings.")
                role_mappings = {
                    "admin": ["admin", "user", "explorer_user"],
                    "auditor": ["auditor", "user"],
                    "explorer_user": ["explorer_user", "user"],
                    "guest": ["guest"],
                    "*": ["user"]
                }
            roles = role_mappings.get(user_id, role_mappings.get("*", ["user"]))
            span.set_attribute("roles", roles)
            return roles

    def _sanitize_prompt(self, prompt: str) -> str:
        sanitized = re.sub(r"[\n\r\"'`]", " ", prompt)
        sanitized = re.sub(r"[\t\b\f\v]", " ", sanitized)
        return sanitized[:2000]

    def _validate_llm_policy_output(self, llm_response_text: str, valid_responses: List[str]) -> Tuple[str, str, float]:
        """Validates LLM policy output and extracts decision, reason, and trust score."""
        try:
            valid_responses = self.config.LLM_VALID_RESPONSES or valid_responses
        except AttributeError:
            logger.warning("LLM_VALID_RESPONSES not found in ArbiterConfig. Using provided valid_responses.")
        decision, reason, trust_score = "NO", "LLM response format invalid or ambiguous.", 0.0
        try:
            # Parse response into decision and reason
            parts = llm_response_text.strip().upper().split(',', 1)
            if len(parts) > 0:
                parsed_decision = parts[0].strip()
                if parsed_decision in valid_responses:
                    decision = parsed_decision
                    reason = parts[1].strip() if len(parts) > 1 else f"LLM decided {decision}."
                    trust_score = 1.0
                else:
                    reason, trust_score = f"LLM decision '{parsed_decision}' not in valid responses.", 0.2
            else:
                reason, trust_score = "LLM response text empty or malformed.", 0.1
            # Sanitize reason to prevent injection
            reason = self._sanitize_prompt(reason)
        except Exception as e:
            logger.error(f"Error validating LLM policy output: {e}. Raw response: {llm_response_text}")
            reason, trust_score = f"Error processing LLM response: {e}", 0.0
        return decision, reason, trust_score

    async def trust_score_rule(self, domain: str, key: str, user_id: Optional[str], value: Optional[Dict]) -> Tuple[bool, str]:
        """Evaluates trust score rule with input validation and rate-limiting."""
        with tracer.start_as_current_span("trust_score_rule", attributes={"domain": domain, "key": key, "user_id": user_id}) as span:
            current_time = time.monotonic()
            if current_time - self._last_trust_score_time < self._trust_score_interval:
                await asyncio.sleep(self._trust_score_interval - (current_time - self._last_trust_score_time))
            
            if not isinstance(domain, str) or not re.match(self.config.VALID_DOMAIN_PATTERN, domain):
                span.record_exception(ValueError(f"Invalid domain: {domain}"))
                return False, f"Invalid domain: {domain}"
            if not isinstance(key, str) or not key:
                span.record_exception(ValueError(f"Invalid key: {key}"))
                return False, f"Invalid key: {key}"
            if user_id is not None and not isinstance(user_id, str):
                span.record_exception(ValueError(f"Invalid user_id: {user_id}"))
                return False, f"Invalid user_id: {user_id}"
            
            trust_rules = self._policies.get("trust_rules", {})
            trust_control_tag = trust_rules.get("control_tag")
            if not trust_rules.get("enabled", False):
                return True, "Trust score rule is disabled."
            if domain != "authentication":
                return True, "Trust score rule applies only to 'authentication' domain."
            if not value:
                return False, "Missing authentication context for trust score evaluation."
            
            effective_user_id_for_stats = user_id if user_id is not None else "anonymous"
            try:
                from app.ai_assistant.decision_optimizer import DecisionOptimizer
                if not isinstance(self.config.DECISION_OPTIMIZER_SETTINGS, dict):
                    raise ValueError("DECISION_OPTIMIZER_SETTINGS must be a dictionary")
                optimizer = DecisionOptimizer(
                    plugin_registry=getattr(self.arbiter, "plugin_registry", None),
                    settings=self.config.DECISION_OPTIMIZER_SETTINGS,
                    logger=logger,
                    safe_serialize=lambda x: json.dumps(x, default=str)
                )
                score = await optimizer.compute_trust_score(value, user_id)
                self._last_trust_score_time = time.monotonic()
                threshold = trust_rules.get("threshold", 0.6)
                if score < threshold:
                    reason = f"Trust score {score:.2f} below threshold {threshold}."
                    await self._audit_policy_decision("trust_score_rule", domain, key, effective_user_id_for_stats, value, False, reason, control_tag=trust_control_tag, is_compliant=False)
                    policy_decision_total.labels(allowed='false', domain=domain, user_type=effective_user_id_for_stats, reason_code='trust_score_low').inc()
                    span.set_attribute("trust_score", score)
                    return False, reason
                reason = f"Trust score {score:.2f} sufficient."
                await self._audit_policy_decision("trust_score_rule", domain, key, effective_user_id_for_stats, value, True, reason, control_tag=trust_control_tag, is_compliant=True)
                policy_decision_total.labels(allowed='true', domain=domain, user_type=effective_user_id_for_stats, reason_code='trust_score_sufficient').inc()
                span.set_attribute("trust_score", score)
                return True, reason
            except ImportError:
                logger.warning("DecisionOptimizer not available. Using basic trust score.", exc_info=True)
                optimizer = BasicDecisionOptimizer(settings=self.config.DECISION_OPTIMIZER_SETTINGS)
                score = await optimizer.compute_trust_score(value, user_id)
                self._last_trust_score_time = time.monotonic()
                threshold = trust_rules.get("threshold", 0.6)
                if score < threshold:
                    reason = f"Trust score {score:.2f} below threshold {threshold} (fallback)."
                    await self._audit_policy_decision("trust_score_rule", domain, key, effective_user_id_for_stats, value, False, reason, control_tag=trust_control_tag, is_compliant=False)
                    policy_decision_total.labels(allowed='false', domain=domain, user_type=effective_user_id_for_stats, reason_code='trust_score_low_fallback').inc()
                    span.set_attribute("trust_score", score)
                    return False, reason
                reason = f"Trust score {score:.2f} sufficient (fallback)."
                await self._audit_policy_decision("trust_score_rule", domain, key, effective_user_id_for_stats, value, True, reason, control_tag=trust_control_tag, is_compliant=True)
                policy_decision_total.labels(allowed='true', domain=domain, user_type=effective_user_id_for_stats, reason_code='trust_score_sufficient_fallback').inc()
                span.set_attribute("trust_score", score)
                return True, reason
            except Exception as e:
                logger.error(f"Error computing trust score: {e}", exc_info=True)
                span.record_exception(e)
                return False, f"Error computing trust score: {e}"

    async def start_policy_refresher(self):
        """Starts the policy refresh task."""
        with tracer.start_as_current_span("start_policy_refresher") as span:
            with self._lock:
                if not self._policy_refresh_task or self._policy_refresh_task.done():
                    self._policy_refresh_task = asyncio.create_task(self._periodic_policy_refresh())
                    logger.info("Policy refresher started.")
                    span.set_attribute("task_status", "started")

    async def _periodic_policy_refresh(self):
        """Periodically refreshes policies with pause/resume and rate-limiting."""
        with tracer.start_as_current_span("periodic_policy_refresh") as span:
            while not self._stop_event.is_set():
                pause_value = os.getenv('PAUSE_POLICY_REFRESH_TASKS', 'false').lower()
                is_paused = self._pause_policy_refresh or pause_value == 'true'
                if is_paused != self._last_pause_state:
                    logger.info(f"Policy refresh task {'paused' if is_paused else 'resumed'}")
                    POLICY_REFRESH_STATE_TRANSITIONS.labels(state='paused' if is_paused else 'resumed').inc()
                    span.set_attribute("task_state", "paused" if is_paused else "resumed")
                    self._last_pause_state = is_paused
                if is_paused:
                    await asyncio.sleep(self.config.POLICY_PAUSE_POLLING_INTERVAL or 60)
                    continue
                try:
                    await asyncio.sleep(self.config.POLICY_REFRESH_INTERVAL_SECONDS)
                    self.reload_policies()
                    policy_file_reload_count.inc()
                    policy_last_reload_timestamp.set(time.time())
                    span.set_attribute("refresh_status", "success")
                    logger.info("Policies refreshed from disk.")
                except asyncio.CancelledError:
                    logger.info("Policy refresh task cancelled.")
                    break
                except Exception as e:
                    logger.error(f"Error during periodic policy refresh: {e}", exc_info=True)
                    POLICY_REFRESH_ERRORS.labels(error_type='refresh_failed').inc()
                    span.record_exception(e)
                    await asyncio.sleep(60)
            logger.info("Periodic policy refresher stopped.")

    async def stop(self):
        """Stops the policy refresh task."""
        with tracer.start_as_current_span("stop_policy_engine") as span:
            self._stop_event.set()
            if self._policy_refresh_task:
                self._policy_refresh_task.cancel()
                await asyncio.gather(self._policy_refresh_task, return_exceptions=True)
                span.set_attribute("task_status", "stopped")

_policy_engine_instance: Optional[PolicyEngine] = None
_policy_engine_lock = threading.Lock()

def initialize_policy_engine(arbiter_instance: Any):
    """Initializes the global PolicyEngine instance."""
    global _policy_engine_instance
    with tracer.start_as_current_span("initialize_policy_engine") as span:
        with _policy_engine_lock:
            try:
                # Ensure single instance of PolicyEngine
                if _policy_engine_instance is None:
                    logger.info("Initializing global PolicyEngine instance...")
                    # Create PolicyEngine with provided arbiter instance and config
                    _policy_engine_instance = PolicyEngine(arbiter_instance, get_config())
                    # Start policy refresher task
                    asyncio.create_task(_policy_engine_instance.start_policy_refresher())
                    logger.info("Global PolicyEngine initialized and refresher started.")
                    span.set_attribute("init_status", "success")
            except Exception as e:
                logger.error(f"Failed to initialize PolicyEngine: {e}")
                POLICY_ENGINE_INIT_ERRORS.labels(error_type='init_failed').inc()
                span.record_exception(e)
                raise

async def should_auto_learn(domain: str, key: str, user_id: Optional[str], value: Optional[Any] = None) -> Tuple[bool, str]:
    global _policy_engine_instance
    if _policy_engine_instance is None:
        with _policy_engine_lock:
            if _policy_engine_instance is None:
                logger.warning("PolicyEngine not initialized. Using MinimalMockArbiter.")
                class MinimalMockArbiter:
                    name = "AutoInitMockArbiter"
                    bug_manager = None
                    knowledge_graph = None
                    plugin_registry = None
                _policy_engine_instance = PolicyEngine(MinimalMockArbiter(), get_config())
                asyncio.create_task(_policy_engine_instance.start_policy_refresher())
    return await _policy_engine_instance.should_auto_learn(domain, key, user_id, value)

def get_policy_engine_instance() -> Optional[PolicyEngine]:
    with _policy_engine_lock:
        return _policy_engine_instance

async def reset_policy_engine():
    """Resets the global PolicyEngine instance."""
    global _policy_engine_instance
    with tracer.start_as_current_span("reset_policy_engine") as span:
        with _policy_engine_lock:
            try:
                if _policy_engine_instance:
                    logger.info("Resetting global PolicyEngine instance.")
                    await _policy_engine_instance.stop()
                    _policy_engine_instance = None
                    logger.info("Global PolicyEngine instance reset.")
                    span.set_attribute("reset_status", "success")
            except Exception as e:
                logger.error(f"Failed to reset PolicyEngine: {e}")
                POLICY_ENGINE_RESET_ERRORS.labels(error_type='reset_failed').inc()
                span.record_exception(e)
                raise