"""
Prometheus metrics for the Arbiter system.

Metrics:
- policy_decisions_total: Total policy decisions made (allowed, domain, user_type, reason_code)
- policy_file_reloads_total: Total times policy file has been reloaded
- policy_last_reload_timestamp_seconds: Timestamp of the last policy file reload
- feedback_processing_time: Time spent processing feedback
- llm_policy_call_latency_seconds: Latency of LLM calls for policy evaluation (provider)
- compliance_control_actions_total: Total actions processed for a compliance control (control_id, result, action_type)
- compliance_control_status: Current configured status of a compliance control (control_id, status_detail, required)
- compliance_violations_total: Total compliance violations detected (control_id, violation_type)
- compliance_metric_init_errors_total: Total errors during compliance metric initialization (result)
- compliance_control_active_total: Number of active compliance controls
- metric_refresh_state_transitions_total: Total state transitions for metric refresh task (pause/resume)
- metric_refresh_latency_seconds: Latency of compliance metric refresh task

Dependencies:
- Requires ArbiterConfig from config.py for histogram bucket configuration (llm_call_latency_buckets, feedback_processing_buckets), refresh interval (POLICY_REFRESH_INTERVAL_SECONDS), control limit (CIRCUIT_BREAKER_MAX_PROVIDERS), and rate-limiting interval (CIRCUIT_BREAKER_MIN_OPERATION_INTERVAL, CIRCUIT_BREAKER_VALIDATION_ERROR_INTERVAL).
- Requires compliance_mapper.py for dynamic compliance controls.

Initialization:
- Call `start_metric_refresh_task()` during application startup (e.g., in core.py) to enable dynamic metric refresh.
- Call `register_shutdown_handler()` during startup to ensure graceful cleanup.
- Ensure Prometheus endpoint is secured (e.g., with authentication) to prevent unauthorized access.

Environment Variables:
- PAUSE_METRIC_REFRESH_TASKS: Set to 'true' to pause the metric refresh task (default: 'false').
- PYTEST_CURRENT_TEST: When set, skips module-level initialization for testing.

Notes:
- All metric labels are sanitized to alphanumeric, underscore, or hyphen characters to prevent corruption.
"""
from typing import Union, Tuple, Optional, Type, List, Dict, Any
from prometheus_client import Counter, Gauge, Histogram, Summary, REGISTRY
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import os
import sys
import logging
import threading
import asyncio
import re
import atexit
import time

# Use centralized OpenTelemetry configuration
from arbiter.otel_config import get_tracer

# Initialize tracer
tracer = get_tracer(__name__)

# Assume compliance_mapper is in the same parent directory as metrics (e.g., both in 'guardrails')
current_dir = os.path.dirname(__file__)
parent_dir = os.path.abspath(os.path.join(current_dir, os.pardir))

# Define compliance config path
COMPLIANCE_CONFIG_PATH = os.environ.get(
    "CREW_CONFIG_PATH", 
    os.path.join(os.path.dirname(__file__), '..', '..', 'agent_orchestration', 'crew_config.yaml')
)

# Assume ArbiterConfig is available for dynamic configuration
try:
    from .config import get_config
    ArbiterConfig = type(get_config())
except ImportError:
    logger = logging.getLogger(__name__)
    logger.warning("Warning: Could not import ArbiterConfig. Using default configuration.")
    class ArbiterConfig:
        def __init__(self):
            self.DECISION_OPTIMIZER_SETTINGS = {
                "llm_call_latency_buckets": (0.1, 0.5, 1, 2, 5, 10, 30, 60),
                "feedback_processing_buckets": (.001, .01, .1, 1, 10)
            }
            self.POLICY_REFRESH_INTERVAL_SECONDS = 300
            self.CIRCUIT_BREAKER_MAX_PROVIDERS = 1000
            self.CIRCUIT_BREAKER_MIN_OPERATION_INTERVAL = 30.0
            self.CIRCUIT_BREAKER_VALIDATION_ERROR_INTERVAL = 300.0
    
logger = logging.getLogger(__name__)

# Lock for thread-safe metric registration
_metrics_lock = threading.Lock()
# Global state for task management
_refresh_task: Optional[asyncio.Task] = None
_refresh_task_lock = threading.Lock()
_last_pause_state: bool = False
_last_refresh_time: float = 0.0
_min_refresh_interval: float = 30.0  # Default value
_last_error_time: float = 0.0
_error_log_interval: float = 300.0  # Default value
_shutdown_handler_registered: bool = False

def _sanitize_label(value: Any) -> str:
    """Sanitizes metric label values to prevent corruption."""
    with tracer.start_as_current_span("sanitize_label", attributes={"raw_value": str(value)}) as span:
        if not isinstance(value, str):
            span.set_attribute("sanitized_value", "invalid")
            return "invalid"
        # Replace any character that is not alphanumeric, underscore, or hyphen with an underscore
        sanitized_value = re.sub(r'[^a-zA-Z0-9_-]', '_', value)
        span.set_attribute("sanitized_value", sanitized_value)
        return sanitized_value[:50]

def _log_error_rate_limited(message: str, error_type: str) -> None:
    """Logs errors with rate-limiting to prevent flooding."""
    global _last_error_time, _error_log_interval
    current_time = time.monotonic()
    if current_time - _last_error_time >= _error_log_interval:
        logger.error(message)
        # Only increment if COMPLIANCE_METRIC_INIT_ERRORS exists
        if 'COMPLIANCE_METRIC_INIT_ERRORS' in globals():
            COMPLIANCE_METRIC_INIT_ERRORS.labels(result=error_type).inc()
        _last_error_time = current_time

def get_or_create_metric(metric_class: Union[Type[Counter], Type[Gauge], Type[Histogram], Type[Summary]],
                        name: str, documentation: str, labelnames: Tuple[str, ...] = (),
                        buckets: Optional[Tuple[float, ...]] = None):
    """
    Thread-safe utility to get an existing Prometheus metric or create a new one.
    This prevents `ValueError` exceptions from re-registering metrics.
    """
    # Get the class name safely - handle Mock objects during testing
    try:
        metric_type_name = metric_class.__name__
    except AttributeError:
        # If metric_class is a Mock or doesn't have __name__, use a default
        metric_type_name = "UnknownMetric"
    
    with tracer.start_as_current_span("get_or_create_metric", attributes={"metric_name": name, "metric_type": metric_type_name}) as span:
        if not re.match(r'^[a-zA-Z_:][a-zA-Z0-9_:]*$', name):
            logger.error(f"Invalid metric name: {name}. Must match [a-zA-Z_:][a-zA-Z0-9_:]*")
            span.record_exception(ValueError(f"Invalid metric name: {name}"))
            raise ValueError(f"Invalid metric name: {name}")
        
        # Check if metric_class is a valid Prometheus metric type
        valid_metric_types = (Counter, Gauge, Histogram, Summary)
        try:
            is_valid_type = issubclass(metric_class, valid_metric_types)
        except TypeError:
            # metric_class is not a class or is a Mock
            logger.warning(f"Invalid metric class type for {name}. Using Counter as fallback.")
            metric_class = Counter
            is_valid_type = True
        
        # Handle the case where metric_class might not be a real class (e.g., Mock during testing)
        try:
            if is_valid_type and issubclass(metric_class, (Histogram, Summary)):
                full_name = name + '_sum'
            else:
                full_name = name
        except TypeError:
            # metric_class is not a class (might be Mock), use default name
            full_name = name
        labelnames = labelnames or ()
        
        if buckets:
            if not all(isinstance(b, (int, float)) and b > 0 for b in buckets) or buckets != tuple(sorted(buckets)):
                logger.error(f"Invalid buckets for metric {name}: {buckets}. Must be positive and sorted.")
                span.record_exception(ValueError(f"Invalid buckets: {buckets}"))
                raise ValueError(f"Invalid buckets: {buckets}")

        with _metrics_lock:
            try:
                if full_name in REGISTRY._names_to_collectors:
                    existing_metric = REGISTRY._names_to_collectors[full_name]
                    # Check if the existing metric is of the expected type
                    try:
                        if isinstance(existing_metric, metric_class):
                            span.set_attribute("metric_status", "reused")
                            return existing_metric
                    except TypeError:
                        # metric_class might be a Mock, just return the existing metric
                        span.set_attribute("metric_status", "reused_unchecked")
                        return existing_metric
                    
                    logger.error(
                        f"Metric '{name}' already registered with a different type "
                        f"({type(existing_metric).__name__}). Reusing existing."
                    )
                    span.set_attribute("metric_status", "type_conflict")
                    return existing_metric
            except Exception as e:
                logger.error(f"Error checking registry for metric '{name}': {e}")
                span.record_exception(e)
                span.set_attribute("metric_status", "error")
            
            # Create the new metric
            try:
                if buckets and issubclass(metric_class, Histogram):
                    metric = metric_class(name, documentation, labelnames=labelnames, buckets=buckets)
                else:
                    metric = metric_class(name, documentation, labelnames=labelnames)
                span.set_attribute("metric_status", "created")
                return metric
            except Exception as e:
                logger.error(f"Failed to create metric '{name}': {e}")
                span.record_exception(e)
                # Return a dummy counter as fallback
                return Counter(f"{name}_fallback", documentation, labelnames=labelnames)

# Get config for bucket settings and update global variables
try:
    from .config import get_config
    config_instance = get_config()
    _min_refresh_interval = getattr(config_instance, 'CIRCUIT_BREAKER_MIN_OPERATION_INTERVAL', 30.0)
    _error_log_interval = getattr(config_instance, 'CIRCUIT_BREAKER_VALIDATION_ERROR_INTERVAL', 300.0)
except ImportError:
    config_instance = ArbiterConfig()

# Create metrics with error handling
policy_decision_total = get_or_create_metric(
    Counter, 'policy_decisions_total', 'Total policy decisions made', ('allowed', 'domain', 'user_type', 'reason_code')
)
policy_file_reload_count = get_or_create_metric(
    Counter, 'policy_file_reloads_total', 'Total times policy file has been reloaded'
)
policy_last_reload_timestamp = get_or_create_metric(
    Gauge, 'policy_last_reload_timestamp_seconds', 'Timestamp of the last policy file reload'
)
# Get settings safely with defaults
decision_optimizer_settings = getattr(config_instance, 'DECISION_OPTIMIZER_SETTINGS', None)
if decision_optimizer_settings is None:
    decision_optimizer_settings = {}

feedback_buckets = decision_optimizer_settings.get("feedback_processing_buckets", (.001, .01, .1, 1, 10)) if isinstance(decision_optimizer_settings, dict) else (.001, .01, .1, 1, 10)
llm_buckets = decision_optimizer_settings.get("llm_call_latency_buckets", (0.1, 0.5, 1, 2, 5, 10, 30, 60)) if isinstance(decision_optimizer_settings, dict) else (0.1, 0.5, 1, 2, 5, 10, 30, 60)

feedback_processing_time = get_or_create_metric(
    Histogram,
    "feedback_processing_time",
    "Time spent processing feedback",
    labelnames=(),
    buckets=tuple(feedback_buckets)
)
LLM_CALL_LATENCY = get_or_create_metric(
    Histogram, 'llm_policy_call_latency_seconds', 'Latency of LLM calls for policy evaluation', ('provider',),
    buckets=tuple(llm_buckets)
)

# --- Dynamic Compliance Metrics ---
# Centralized metric for all compliance actions to avoid metric bloat
COMPLIANCE_CONTROL_ACTIONS_TOTAL = get_or_create_metric(
    Counter, 'compliance_control_actions_total',
    'Total actions processed for a compliance control',
    ('control_id', 'result', 'action_type') # e.g., control_id='NIST_AC-1', result='passed', action_type='auto_learn'
)

# Gauge to directly reflect the configured status of a control
COMPLIANCE_CONTROL_STATUS = get_or_create_metric(
    Gauge, 'compliance_control_status',
    'Current configured status of a compliance control (0=non-compliant, 1=compliant)',
    ('control_id', 'status_detail', 'required')
)

# Other compliance metrics
COMPLIANCE_VIOLATIONS_TOTAL = get_or_create_metric(
    Counter, 'compliance_violations_total',
    'Total compliance violations detected',
    ('control_id', 'violation_type')
)

# Metric to track initialization and refresh failures
COMPLIANCE_METRIC_INIT_ERRORS = get_or_create_metric(
    Counter, 'compliance_metric_init_errors_total',
    'Total errors during compliance metric initialization',
    ('result',)
)

# Metric to track active compliance controls
COMPLIANCE_CONTROL_ACTIVE = get_or_create_metric(
    Gauge, 'compliance_control_active_total',
    'Number of active compliance controls',
    labelnames=()
)

# Metric to track task state changes
METRIC_REFRESH_STATE_TRANSITIONS = get_or_create_metric(
    Counter, 'metric_refresh_state_transitions_total',
    'Total state transitions for metric refresh task (pause/resume)',
    ('state',)
)

# Metric to track refresh task latency
METRIC_REFRESH_LATENCY = get_or_create_metric(
    Histogram, 'metric_refresh_latency_seconds', 'Latency of compliance metric refresh task',
    labelnames=('operation',), buckets=(0.1, 0.5, 1, 2, 5, 10)
)

# Enhanced fallback for compliance_mapper import
try:
    from guardrails.compliance_mapper import load_compliance_map
except ImportError:
    logger.critical("Failed to import compliance_mapper. Using fallback compliance map.")
    def load_compliance_map(config_path: str = None) -> Dict[str, Any]:
        return {
            "NIST_AC-1": {"name": "Access Control Policy", "status": "enforced", "required": True},
            "NIST_AC-2": {"name": "Account Management", "status": "enforced", "required": True},
            "NIST_AC-3": {"name": "Access Enforcement", "status": "enforced", "required": True},
            "NIST_AC-6": {"name": "Least Privilege", "status": "enforced", "required": True}
        }

# Helper functions for recording metrics with sanitized and validated labels
VALID_ALLOWED_VALUES = {"true", "false"}
VALID_USER_TYPES = {"user", "admin", "service"}
VALID_VIOLATION_TYPES = {"access_denied", "policy_violation", "unauthorized"}
VALID_ACTION_TYPES = {"auto_learn", "enforce", "audit"}

def record_policy_decision(allowed: str, domain: str, user_type: str, reason_code: str) -> None:
    """Records a policy decision with sanitized and validated labels."""
    with tracer.start_as_current_span("record_policy_decision", attributes={"allowed": allowed, "domain": domain, "user_type": user_type, "reason_code": reason_code}) as span:
        sanitized_allowed = _sanitize_label(allowed) if allowed in VALID_ALLOWED_VALUES else "invalid"
        if sanitized_allowed == "invalid":
            _log_error_rate_limited(f"Invalid allowed value: {allowed}. Must be one of {VALID_ALLOWED_VALUES}", "invalid_allowed")
            span.record_exception(ValueError(f"Invalid allowed: {allowed}"))
        sanitized_user_type = _sanitize_label(user_type) if user_type in VALID_USER_TYPES else "invalid"
        if sanitized_user_type == "invalid":
            _log_error_rate_limited(f"Invalid user_type: {user_type}. Must be one of {VALID_USER_TYPES}", "invalid_user_type")
            span.record_exception(ValueError(f"Invalid user_type: {user_type}"))
        policy_decision_total.labels(
            allowed=sanitized_allowed,
            domain=_sanitize_label(domain),
            user_type=sanitized_user_type,
            reason_code=_sanitize_label(reason_code)
        ).inc()
        span.set_attribute("status", "success")

def record_llm_call_latency(provider: str, latency: float) -> None:
    """Records LLM call latency with sanitized provider label."""
    with tracer.start_as_current_span("record_llm_call_latency", attributes={"provider": provider, "latency": latency}) as span:
        LLM_CALL_LATENCY.labels(provider=_sanitize_label(provider)).observe(latency)
        span.set_attribute("status", "success")

def record_compliance_violation(control_id: str, violation_type: str) -> None:
    """Records a compliance violation with sanitized and validated labels."""
    with tracer.start_as_current_span("record_compliance_violation", attributes={"control_id": control_id, "violation_type": violation_type}) as span:
        sanitized_violation_type = _sanitize_label(violation_type) if violation_type in VALID_VIOLATION_TYPES else "invalid"
        if sanitized_violation_type == "invalid":
            _log_error_rate_limited(f"Invalid violation_type: {violation_type}. Must be one of {VALID_VIOLATION_TYPES}", "invalid_violation_type")
            span.record_exception(ValueError(f"Invalid violation_type: {violation_type}"))
        COMPLIANCE_VIOLATIONS_TOTAL.labels(
            control_id=_sanitize_label(control_id),
            violation_type=sanitized_violation_type
        ).inc()
        span.set_attribute("status", "success")

def record_compliance_action(control_id: str, result: str, action_type: str) -> None:
    """Records a compliance action with sanitized and validated labels."""
    with tracer.start_as_current_span("record_compliance_action", attributes={"control_id": control_id, "result": result, "action_type": action_type}) as span:
        sanitized_control_id = _sanitize_label(control_id)
        sanitized_result = _sanitize_label(result)
        sanitized_action_type = _sanitize_label(action_type) if action_type in VALID_ACTION_TYPES else "invalid"
        if sanitized_action_type == "invalid":
            _log_error_rate_limited(f"Invalid action_type: {action_type}. Must be one of {VALID_ACTION_TYPES}", 'invalid_action_type')
            span.record_exception(ValueError(f"Invalid action_type: {action_type}"))
        COMPLIANCE_CONTROL_ACTIONS_TOTAL.labels(
            control_id=sanitized_control_id,
            result=sanitized_result,
            action_type=sanitized_action_type
        ).inc()
        span.set_attribute("status", "success")


# Shutdown handler for task and resource cleanup
def register_shutdown_handler() -> None:
    """Registers a shutdown handler to clean up the metric refresh task, Redis pool, and OpenTelemetry resources."""
    global _shutdown_handler_registered
    with _refresh_task_lock:
        if _shutdown_handler_registered:
            logger.debug("Metrics shutdown handler already registered.")
            return
        def shutdown():
            if _refresh_task is not None and not _refresh_task.done():
                _refresh_task.cancel()
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_closed():
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                    loop.run_until_complete(_refresh_task)
                except (asyncio.CancelledError, RuntimeError) as e:
                    logger.debug(f"Metric refresh task cleanup failed: {e}")
                except Exception as e:
                    logger.error(f"Error during metric refresh task cleanup: {e}")
                finally:
                    if 'loop' in locals() and not loop.is_closed():
                        loop.close()

            if hasattr(load_compliance_map, '_redis_pool') and load_compliance_map._redis_pool:
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_closed():
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                    loop.run_until_complete(load_compliance_map._redis_pool.disconnect())
                    logger.debug("Redis connection pool cleaned up during shutdown.")
                except (RuntimeError, Exception) as e:
                    logger.error(f"Error cleaning up Redis pool during shutdown: {e}")
                finally:
                    if 'loop' in locals() and not loop.is_closed():
                        loop.close()

            # Note: OpenTelemetry shutdown is now handled by the centralized configuration
            logger.debug("Metrics shutdown handler completed.")
        atexit.register(shutdown)
        _shutdown_handler_registered = True
        logger.debug("Registered metrics shutdown handler.")

async def cleanup_compliance_metrics() -> None:
    """Cleans up stale compliance metrics and potential Redis connection pool."""
    with tracer.start_as_current_span("cleanup_compliance_metrics") as span:
        try:
            current_controls = set(load_compliance_map(COMPLIANCE_CONFIG_PATH).keys())
            registered_controls = set(
                labels['control_id'] for labels in COMPLIANCE_CONTROL_STATUS._metrics.keys()
            )
            stale_controls = registered_controls - current_controls
            for control_id in stale_controls:
                sanitized_control_id = _sanitize_label(control_id)
                COMPLIANCE_CONTROL_STATUS.remove(sanitized_control_id, ".*", ".*")
                COMPLIANCE_CONTROL_ACTIONS_TOTAL.remove(sanitized_control_id, ".*", ".*")
                COMPLIANCE_VIOLATIONS_TOTAL.remove(sanitized_control_id, ".*")
                logger.debug(f"Removed stale compliance metric for {sanitized_control_id}")
                span.set_attribute(f"control.{sanitized_control_id}.removed", True)
            span.set_attribute("stale_controls_removed", len(stale_controls))
            
            # Clean up Redis connection pool if used
            if hasattr(load_compliance_map, '_redis_pool'):
                try:
                    await load_compliance_map._redis_pool.disconnect()
                    logger.debug("Redis connection pool for compliance map cleaned up.")
                    span.set_attribute("redis_pool_cleanup", "success")
                except Exception as e:
                    _log_error_rate_limited(f"Error cleaning up Redis pool: {e}", 'redis_cleanup_failed')
                    span.record_exception(e)
                    span.set_attribute("redis_pool_cleanup", "failed")
            logger.info("Stale compliance metrics cleaned up.")
        except Exception as e:
            _log_error_rate_limited(f"Error cleaning up compliance metrics: {e}", 'cleanup_failed')
            span.record_exception(e)

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type(Exception),
    before_sleep=lambda retry_state: logger.debug(f"Retrying compliance metric initialization: attempt {retry_state.attempt_number}")
)
def initialize_compliance_metrics():
    """Initializes dynamic compliance control metrics with retry logic."""
    with tracer.start_as_current_span("initialize_compliance_metrics") as span:
        # Load and validate compliance controls from the map
        ALL_COMPLIANCE_CONTROLS = load_compliance_map(COMPLIANCE_CONFIG_PATH)
        span.set_attribute("control_count", len(ALL_COMPLIANCE_CONTROLS))
        
        # Get max_controls from config at runtime, not module load time
        max_controls = 1000  # default
        try:
            from .config import get_config
            config = get_config()
            max_controls_value = getattr(config, 'CIRCUIT_BREAKER_MAX_PROVIDERS', None)
            # Ensure it's an integer, not a MagicMock or other non-integer type
            if max_controls_value is not None and isinstance(max_controls_value, int):
                max_controls = max_controls_value
        except (ImportError, AttributeError, TypeError):
            pass  # use default
            
        if len(ALL_COMPLIANCE_CONTROLS) > max_controls:
            _log_error_rate_limited(f"Compliance control limit ({max_controls}) exceeded", 'limit_exceeded')
            span.record_exception(ValueError(f"Compliance control limit ({max_controls}) exceeded"))
            raise ValueError(f"Compliance control limit ({max_controls}) exceeded")
            
        for control_id, control_info in ALL_COMPLIANCE_CONTROLS.items():
            sanitized_control_id = _sanitize_label(control_id)
            # Validate the structure of each control entry
            if not isinstance(control_id, str) or not control_info.get("name") or not isinstance(control_info.get("status"), str):
                _log_error_rate_limited(f"Invalid compliance control data for {sanitized_control_id}", 'invalid_data')
                span.record_exception(ValueError(f"Invalid compliance control data for {sanitized_control_id}"))
                continue
                
            # Update the Gauge to reflect the configured status from the file
            control_status_value = 1.0 if control_info.get('status') == 'enforced' else 0.0
            COMPLIANCE_CONTROL_STATUS.labels(
                control_id=sanitized_control_id,
                status_detail=_sanitize_label(control_info.get('status', 'not_specified')),
                required=str(control_info.get('required', True)).lower()
            ).set(control_status_value)
            span.set_attribute(f"control.{sanitized_control_id}.status", control_status_value)
            
        COMPLIANCE_CONTROL_ACTIVE.set(len(ALL_COMPLIANCE_CONTROLS))
        logger.info("Dynamic compliance control metrics initialized.")

async def refresh_compliance_metrics() -> None:
    """
    Periodically refreshes compliance metrics to reflect updated compliance map.
    Includes pause/resume logic and rate-limiting.
    """
    global _last_pause_state, _last_refresh_time
    while True:
        with tracer.start_as_current_span("refresh_compliance_metrics") as span:
            start_time = time.monotonic()
            pause_value = os.getenv('PAUSE_METRIC_REFRESH_TASKS', 'false').lower()
            if pause_value not in ('true', 'false'):
                _log_error_rate_limited(f"Invalid PAUSE_METRIC_REFRESH_TASKS: {pause_value}", 'invalid_config')
                span.record_exception(ValueError(f"Invalid PAUSE_METRIC_REFRESH_TASKS: {pause_value}"))
                pause_value = 'false'
            
            is_paused = pause_value == 'true'
            if is_paused != _last_pause_state:
                logger.info(f"Compliance metric refresh task {'paused' if is_paused else 'resumed'}")
                METRIC_REFRESH_STATE_TRANSITIONS.labels(state='paused' if is_paused else 'resumed').inc()
                _last_pause_state = is_paused
                span.set_attribute("task_state", "paused" if is_paused else "resumed")
            
            if is_paused:
                logger.debug("Compliance metric refresh task paused.")
                await asyncio.sleep(60) # A short interval to check for unpause
                continue
            
            # Rate-limiting to prevent excessive refreshes
            current_time = time.monotonic()
            if current_time - _last_refresh_time < _min_refresh_interval:
                await asyncio.sleep(_min_refresh_interval - (current_time - _last_refresh_time))
            
            try:
                await cleanup_compliance_metrics()
                initialize_compliance_metrics()
                span.set_attribute("refresh_status", "success")
                logger.debug("Compliance metrics refreshed successfully.")
            except Exception as e:
                _log_error_rate_limited(f"Error refreshing compliance metrics: {e}", 'refresh_failed')
                span.record_exception(e)
                span.set_attribute("refresh_status", "failed")
            
            _last_refresh_time = time.monotonic()
            METRIC_REFRESH_LATENCY.labels(operation='refresh').observe(time.monotonic() - start_time)
            
            try:
                from .config import get_config
                refresh_interval = get_config().POLICY_REFRESH_INTERVAL_SECONDS
            except ImportError:
                refresh_interval = 300
            await asyncio.sleep(refresh_interval)

def start_metric_refresh_task() -> None:
    """Starts the compliance metric refresh task in the background if not already running."""
    global _refresh_task
    with _refresh_task_lock:
        if _refresh_task is None or _refresh_task.done():
            _refresh_task = asyncio.create_task(refresh_compliance_metrics())
            logger.info("Started compliance metric refresh task.")
        else:
            logger.debug("Compliance metric refresh task already running.")

# Initialize compliance metrics and register shutdown handler
# Skip module-level initialization if we're in a test environment
if os.getenv('PYTEST_CURRENT_TEST') is None:
    try:
        initialize_compliance_metrics()
        register_shutdown_handler()
    except Exception as e:
        logger.error(f"Error initializing dynamic compliance control metrics: {e}")
        logger.error("Dynamic compliance metrics will not be available.")
else:
    logger.debug("Skipping module-level initialization during test run")