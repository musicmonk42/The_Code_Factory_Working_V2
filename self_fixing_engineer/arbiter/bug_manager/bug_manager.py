import asyncio
import contextvars
import datetime
import functools
import hashlib
import inspect
import json
import logging
import os
import time
import traceback
from collections import defaultdict
from typing import Any, Dict, List, Optional, Union

import redis.asyncio as redis
from prometheus_client import Counter, Gauge, Histogram
from pydantic import BaseModel

from ..arbiter_plugin_registry import PlugInKind
from .audit_log import AuditLogManager
from .notifications import NotificationService
from .remediations import BugFixerRegistry, MLRemediationModel

# Assuming these local modules exist and provide the necessary components
from .utils import (
    NotificationError,
    RateLimitExceededError,
    Severity,
    apply_settings_validation,
    get_or_create_metric,
    redact_pii,
    validate_input_details,
)


# Create register decorator
def register(kind):
    def decorator(cls):
        return cls

    return decorator


logger = logging.getLogger(__name__)

# Fixed: ContextVar must be defined at module level, not inside functions
_bug_id_var = contextvars.ContextVar("bug_id", default=None)

# Prometheus Metrics
BUG_REPORT = get_or_create_metric(
    Counter, "bug_report", "Total number of bug reports received", ["severity"]
)
BUG_REPORT_SUCCESS = get_or_create_metric(
    Counter,
    "bug_report_success",
    "Total number of bug reports successfully processed",
    ["severity"],
)
BUG_REPORT_FAILED = get_or_create_metric(
    Counter,
    "bug_report_failed",
    "Total number of bug reports that failed internal processing",
)
BUG_AUTO_FIX_ATTEMPT = get_or_create_metric(
    Counter, "bug_auto_fix_attempt", "Total number of automatic fix attempts"
)
BUG_AUTO_FIX_SUCCESS = get_or_create_metric(
    Counter, "bug_auto_fix_success", "Total number of successful automatic fixes"
)
BUG_NOTIFICATION_DISPATCH = get_or_create_metric(
    Counter,
    "bug_notification_dispatch",
    "Total number of notifications dispatched",
    ["channel"],
)
BUG_PROCESSING_DURATION_SECONDS = get_or_create_metric(
    Histogram,
    "bug_processing_duration_seconds",
    "Duration of bug report processing in seconds",
)
BUG_RATE_LIMITED = get_or_create_metric(
    Counter, "bug_rate_limited", "Total number of bug reports rate-limited"
)
BUG_CURRENT_ACTIVE_REPORTS = get_or_create_metric(
    Gauge,
    "bug_current_active_reports",
    "Current number of active bug reports being processed",
)
BUG_NOTIFICATION_FAILED = get_or_create_metric(
    Counter,
    "bug_notification_failed",
    "Total number of notification dispatch failures.",
    ["channel"],
)
BUG_ML_INIT_FAILED = get_or_create_metric(
    Counter,
    "bug_ml_init_failed",
    "Total number of failures initializing the ML remediation model.",
)


class Settings(BaseModel):
    DEBUG_MODE: bool = True
    SLACK_WEBHOOK_URL: Optional[str] = None
    EMAIL_RECIPIENTS: List[str] = []
    EMAIL_ENABLED: bool = False
    EMAIL_SENDER: str = "no-reply@arbiterai.com"
    EMAIL_SMTP_SERVER: Optional[str] = None
    EMAIL_SMTP_PORT: int = 587
    EMAIL_USE_STARTTLS: bool = True
    EMAIL_SMTP_USERNAME: Optional[str] = None
    EMAIL_SMTP_PASSWORD: Optional[str] = None
    PAGERDUTY_ENABLED: bool = False
    PAGERDUTY_ROUTING_KEY: Optional[str] = None
    ENABLED_NOTIFICATION_CHANNELS: tuple = ("slack", "email", "pagerduty")
    AUDIT_LOG_FILE_PATH: str = "sfe_bug_manager_audit.log"
    AUDIT_DEAD_LETTER_FILE_PATH: str = "sfe_bug_manager_dead_letter.log"
    AUTO_FIX_ENABLED: bool = True
    NOTIFICATION_FAILURE_THRESHOLD: int = 5
    NOTIFICATION_FAILURE_WINDOW_SECONDS: int = 300
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_WINDOW_SECONDS: int = 600
    RATE_LIMIT_MAX_REPORTS: int = 3
    AUDIT_LOG_ENABLED: bool = True
    AUDIT_LOG_FLUSH_INTERVAL_SECONDS: float = 5.0
    AUDIT_LOG_BUFFER_SIZE: int = 100
    AUDIT_LOG_MAX_FILE_SIZE_MB: int = 100
    AUDIT_LOG_BACKUP_COUNT: int = 5
    REMOTE_AUDIT_SERVICE_ENABLED: bool = False
    REMOTE_AUDIT_SERVICE_URL: Optional[str] = None
    REMOTE_AUDIT_SERVICE_TIMEOUT: float = 3.0
    REMOTE_AUDIT_DEAD_LETTER_ENABLED: bool = True
    SLACK_API_TIMEOUT_SECONDS: float = 5.0
    EMAIL_API_TIMEOUT_SECONDS: float = 3.0
    PAGERDUTY_API_TIMEOUT_SECONDS: float = 5.0
    SLACK_FAILURE_RATE: float = 0.0
    EMAIL_FAILURE_RATE: float = 0.0
    PAGERDUTY_FAILURE_RATE: float = 0.0
    ML_REMEDIATION_ENABLED: bool = True
    ML_MODEL_ENDPOINT: str = "http://localhost:5001/predict"
    RATE_LIMIT_REDIS_URL: Optional[str] = None
    BUG_MAX_CONCURRENT_REPORTS: int = 50


class RateLimiter:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._call_timestamps = defaultdict(list)
        self._lock = asyncio.Lock()
        self._max_size = 10000  # Prevent memory exhaustion
        self.redis = None

        # 1.2: Initialize Redis client if URL is provided
        self.redis_url = self._settings.RATE_LIMIT_REDIS_URL
        if self.redis_url:
            try:
                self.redis = redis.Redis.from_url(self.redis_url)
                logger.info("RateLimiter configured to use Redis.")
            except ImportError:
                logger.warning(
                    "redis.asyncio not installed. Falling back to in-memory rate limiting."
                )
                self.redis = None
            except Exception as e:
                logger.error(
                    f"Failed to initialize Redis client from URL: {e}. Falling back to in-memory."
                )
                self.redis = None

    async def initialize(self):
        if self.redis:
            try:
                await self.redis.ping()
                logger.info("Successfully connected to Redis for rate limiting.")
            except Exception as e:
                logger.error(
                    f"Redis ping failed: {e}. Falling back to in-memory rate limiting."
                )
                self.redis = None

    def rate_limit(self, func):
        @functools.wraps(func)
        async def wrapper(
            instance, error_data: Union[Exception, str, Dict[str, Any]], *args, **kwargs
        ):
            settings = self._settings
            max_calls = settings.RATE_LIMIT_MAX_REPORTS
            period = settings.RATE_LIMIT_WINDOW_SECONDS

            location = kwargs.get("location")
            message_part = ""
            exception_type = "unknown"
            custom_details = kwargs.get("custom_details", {})

            if isinstance(error_data, Exception):
                message_part = str(error_data).split("\n")[0]
                exception_type = type(error_data).__name__
            elif isinstance(error_data, str):
                message_part = error_data.split("\n")[0]
                exception_type = "string_message"
            elif isinstance(error_data, dict):
                message_part = error_data.get("message", "").split("\n")[0]
                exception_type = error_data.get("exception_type", "dict_error")
            else:
                message_part = "unrecognized_error_type"
                exception_type = "unrecognized_type"

            rate_limit_key = hashlib.sha256(
                f"{location or 'global'}|{exception_type}|{message_part[:100]}|{str(custom_details)[:200]}".encode(
                    "utf-8"
                )
            ).hexdigest()

            async with self._lock:
                now = time.time()
                if self.redis:
                    # 1.2: Redis-based rate limiting
                    redis_key = f"rate_limit:{rate_limit_key}"
                    async with self.redis.pipeline() as pipe:
                        pipe.zremrangebyscore(redis_key, 0, now - period)
                        pipe.zcard(redis_key)
                        pipe.zadd(redis_key, {str(now): now})
                        pipe.expire(redis_key, period)
                        results = await pipe.execute()
                    count = results[1]
                    if count >= max_calls:
                        logger.warning(f"Rate limit exceeded for key: {rate_limit_key}")
                        BUG_RATE_LIMITED.inc()
                        raise RateLimitExceededError(
                            f"Rate limit exceeded for bug report. Key: {rate_limit_key}"
                        )
                else:
                    # Existing in-memory logic
                    self._call_timestamps[rate_limit_key] = [
                        t
                        for t in self._call_timestamps[rate_limit_key]
                        if now - t < period
                    ]

                    if len(self._call_timestamps) > self._max_size:
                        oldest_key = next(iter(self._call_timestamps))
                        del self._call_timestamps[oldest_key]
                        logger.warning(
                            f"RateLimiter evicted oldest key due to size limit: {oldest_key}"
                        )

                    if len(self._call_timestamps[rate_limit_key]) >= max_calls:
                        logger.warning(f"Rate limit exceeded for key: {rate_limit_key}")
                        BUG_RATE_LIMITED.inc()
                        raise RateLimitExceededError(
                            f"Rate limit exceeded for bug report. Key: {rate_limit_key}"
                        )

                    self._call_timestamps[rate_limit_key].append(now)

            return await func(instance, error_data, *args, **kwargs)

        return wrapper


# Fixed: Removed global rate_limiter that uses wrong settings
# Each BugManager instance will use its own rate_limiter


class BugManager:
    def __init__(self, settings: Settings):
        apply_settings_validation(settings)
        self.settings = settings
        # Fixed: Create rate_limiter per-instance with correct settings
        self._rate_limiter = RateLimiter(self.settings)
        try:
            self.notification_service = NotificationService(self.settings)
        except ImportError as e:
            logger.error(
                f"Failed to initialize NotificationService due to missing dependency: {e}"
            )
            self.notification_service = None
        self.audit_log_manager = AuditLogManager(settings=self.settings)
        self.ml_remediation_model = None

        # 4.1: Metrics for ML and notification failures
        if self.settings.ML_REMEDIATION_ENABLED:
            try:
                if not self.settings.ML_MODEL_ENDPOINT.startswith(
                    ("http://", "https://")
                ):
                    raise ValueError("Invalid ML model endpoint URL.")
                self.ml_remediation_model = MLRemediationModel(
                    self.settings.ML_MODEL_ENDPOINT, self.settings
                )
                if self.ml_remediation_model:
                    BugFixerRegistry.set_ml_model(self.ml_remediation_model)
            except ImportError as e:
                logger.error(
                    f"MLRemediationModel initialization failed due to missing dependency: {e}"
                )
                BUG_ML_INIT_FAILED.inc()
                self.ml_remediation_model = None
            except ValueError as e:
                logger.error(
                    f"MLRemediationModel initialization failed due to invalid settings: {e}"
                )
                BUG_ML_INIT_FAILED.inc()
                self.ml_remediation_model = None

        self._init_lock = asyncio.Lock()
        self._initialized = False
        self.semaphore = asyncio.Semaphore(self.settings.BUG_MAX_CONCURRENT_REPORTS)
        logger.info("BugManager initialized.")

    async def _initialize(self):
        """Perform async initialization of services."""
        await self.audit_log_manager.initialize()
        # Fixed: Use instance rate_limiter instead of global
        await self._rate_limiter.initialize()
        if self.ml_remediation_model:
            logger.info("MLRemediationModel initialized.")
        self._initialized = True

    async def shutdown(self) -> None:
        """Shuts down the bug manager and its components."""
        logger.info("Shutting down BugManager.")
        if self.ml_remediation_model:
            if hasattr(self.ml_remediation_model, "close") and callable(
                self.ml_remediation_model.close
            ):
                await self.ml_remediation_model.close()
        # Assuming NotificationService might have an async shutdown method
        if self.notification_service and hasattr(self.notification_service, "shutdown"):
            if asyncio.iscoroutinefunction(self.notification_service.shutdown):
                await self.notification_service.shutdown()
            else:
                self.notification_service.shutdown()
        if self.audit_log_manager:
            await self.audit_log_manager.shutdown()
        logger.info("BugManager fully shut down.")

    # Fixed: Apply rate limiting inside the method instead of as decorator
    # to use the instance's rate_limiter with correct settings
    async def report(
        self,
        error_data: Union[Exception, str, Dict[str, Any]],
        severity: Union[str, Severity] = Severity.MEDIUM,
        location: Optional[str] = None,
        custom_details: Optional[Dict[str, Any]] = None,
    ) -> None:
        # Apply rate limiting manually using instance rate_limiter
        return await self._rate_limiter.rate_limit(self._report_impl)(
            self, error_data, severity, location, custom_details
        )

    async def _report_impl(
        self,
        error_data: Union[Exception, str, Dict[str, Any]],
        severity: Union[str, Severity] = Severity.MEDIUM,
        location: Optional[str] = None,
        custom_details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Internal implementation of report with actual logic."""
        # Lazy initialization to prevent race conditions
        async with self._init_lock:
            if not self._initialized:
                await self._initialize()

        BUG_CURRENT_ACTIVE_REPORTS.inc()
        start_time = time.perf_counter()
        severity_enum = None

        # 2.2: Context-aware logging
        bug_signature = self._generate_bug_signature(
            error_data, location, custom_details
        )
        # Fixed: Use module-level ContextVar instead of creating new one
        _bug_id_var.set(bug_signature[:8])

        try:
            if isinstance(severity, str):
                severity_enum = Severity.from_string(severity)
            else:
                severity_enum = severity

            BUG_REPORT.labels(severity=severity_enum.value).inc()

            error_details = self._parse_error_data(
                error_data, severity_enum, location, custom_details
            )
            error_details = redact_pii(error_details)

            if self.settings.AUDIT_LOG_ENABLED:
                await self.audit_log_manager.audit(
                    event_type="bug_reported",
                    details={
                        "bug_signature": bug_signature,
                        "severity": severity_enum.value,
                        "location": location,
                        "error_details": error_details,
                    },
                )

            fixed = False
            if self.settings.AUTO_FIX_ENABLED:
                BUG_AUTO_FIX_ATTEMPT.inc()
                try:
                    fixed = await BugFixerRegistry.run_remediation(
                        location or "global", error_details, bug_signature
                    )
                    if fixed:
                        BUG_AUTO_FIX_SUCCESS.inc()
                        logger.info(
                            f"Bug fixed automatically: {error_details['message']}"
                        )
                except Exception as e:
                    logger.error(
                        json.dumps(
                            {
                                "event": "remediation_failed",
                                "bug_id": _bug_id_var.get(),
                                "error": str(e),
                            }
                        ),
                        exc_info=True,
                    )

            if self.notification_service and (
                not fixed or severity_enum in [Severity.CRITICAL, Severity.HIGH]
            ):
                await self._dispatch_notifications(error_details)

            BUG_REPORT_SUCCESS.labels(severity=severity_enum.value).inc()
        except RateLimitExceededError:
            logger.warning(f"Rate limited bug {_bug_id_var.get()}")
            raise
        except Exception as e:
            logger.critical(
                json.dumps(
                    {
                        "event": "bug_processing_failed",
                        "bug_id": _bug_id_var.get(),
                        "error": str(e),
                    }
                ),
                exc_info=True,
            )
            BUG_REPORT_FAILED.inc()
            if self.settings.AUDIT_LOG_ENABLED:
                if not severity_enum:
                    severity_enum = (
                        Severity.MEDIUM
                        if not isinstance(severity, Severity)
                        else severity
                    )

                parsed_details = self._parse_error_data(
                    error_data, severity_enum, location, custom_details
                )
                await self.audit_log_manager.audit(
                    event_type="bug_processing_failed",
                    details={
                        "error": str(e),
                        "error_details": redact_pii(parsed_details),
                    },
                )
            raise
        finally:
            BUG_CURRENT_ACTIVE_REPORTS.dec()
            BUG_PROCESSING_DURATION_SECONDS.observe(time.perf_counter() - start_time)

    def _parse_error_data(
        self,
        error_data: Union[Exception, str, Dict[str, Any]],
        severity: Severity,
        location: Optional[str],
        custom_details: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        details = {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "severity": severity.value,
            "location": location or "global",
            "message": "",
            "exception_type": None,
            "stack_trace": None,
        }
        if isinstance(error_data, Exception):
            details["message"] = str(error_data)
            details["exception_type"] = type(error_data).__name__
            details["stack_trace"] = self._get_stack_trace_from_caller()
        elif isinstance(error_data, str):
            details["message"] = error_data
            details["exception_type"] = "string_message"
            details["stack_trace"] = self._get_stack_trace_from_caller()
        elif isinstance(error_data, dict):
            details.update(error_data)
            if "message" not in details:
                details["message"] = (
                    "Error reported as dictionary without 'message' key."
                )
                logger.warning("Error dictionary provided without a 'message' key.")
            if "stack_trace" not in details:
                details["stack_trace"] = self._get_stack_trace_from_caller()
        else:
            details["message"] = (
                f"Unrecognized error data type: {type(error_data).__name__}"
            )
            details["exception_type"] = "unknown_type"
            logger.error(
                json.dumps(
                    {"event": "unrecognized_error", "type": type(error_data).__name__}
                )
            )

        if custom_details:
            details["custom_details"] = validate_input_details(custom_details)
        return details

    def _get_stack_trace_from_caller(self) -> Optional[str]:
        stack_lines = []
        try:
            for frame, _ in traceback.walk_stack(None):
                filename = os.path.basename(frame.f_code.co_filename)
                if "bug_manager.py" not in filename and "asyncio" not in filename:
                    line_source = "<source unavailable>"
                    try:
                        lines, start_lineno = inspect.getsourcelines(frame.f_code)
                        if 0 <= (frame.f_lineno - start_lineno) < len(lines):
                            line_source = lines[frame.f_lineno - start_lineno].strip()
                    except (IOError, IndexError, TypeError):
                        logger.debug(
                            f"Could not retrieve source line for {filename}:{frame.f_lineno}"
                        )
                    stack_lines.append(
                        f'  File "{filename}", line {frame.f_lineno}, in {frame.f_code.co_name}\n    {line_source}'
                    )
                if len(stack_lines) > 20:
                    stack_lines.append("    [...truncated...]")
                    break
        except Exception:
            return redact_pii({"traceback": traceback.format_exc()}).get("traceback")

        if not stack_lines:
            logger.debug("No relevant stack frames found.")
            return None
        return redact_pii(
            {
                "traceback": "Traceback (most recent call last):\n"
                + "\n".join(reversed(stack_lines))
            }
        ).get("traceback")

    def _generate_bug_signature(
        self,
        error_data: Union[Exception, str, Dict[str, Any]],
        location: Optional[str],
        custom_details: Optional[Dict[str, Any]],
    ) -> str:
        message_part = ""
        exception_type = "unknown"

        if isinstance(error_data, Exception):
            exception_type = type(error_data).__name__
            message_part = str(error_data).split("\n")[0]
        elif isinstance(error_data, str):
            exception_type = "string_message"
            message_part = error_data.split("\n")[0]
        elif isinstance(error_data, dict):
            exception_type = error_data.get("exception_type", "dict_error")
            message_part = error_data.get("message", "").split("\n")[0]
        else:
            exception_type = "unrecognized_type"
            message_part = f"unrecognized_data_{type(error_data).__name__}"

        sanitized_details = validate_input_details(custom_details)
        signature_base = f"{location or 'global'}|{exception_type}|{message_part[:100]}|{str(sanitized_details)[:200]}"
        return hashlib.sha256(signature_base.encode("utf-8")).hexdigest()

    async def _dispatch_notifications(self, error_details: Dict[str, Any]) -> None:
        severity_value = error_details.get("severity", Severity.MEDIUM.value).lower()
        message_prefix = (
            f"[{error_details['timestamp']}] ARBITER BUG ({severity_value.upper()}): "
        )
        message_body = f"{error_details['message']}\nLocation: {error_details.get('location', 'N/A')}\n"
        if error_details.get("exception_type"):
            message_body += f"Type: {error_details['exception_type']}\n"
        if error_details.get("custom_details"):
            message_body += (
                f"Details: {json.dumps(error_details['custom_details'], indent=2)}\n"
            )
        if error_details.get("stack_trace"):
            message_body += f"Stack Trace:\n```\n{error_details['stack_trace']}\n```"

        redacted_message_body = redact_pii({"body": message_body}).get("body")

        notification_tasks = []
        if "slack" in self.settings.ENABLED_NOTIFICATION_CHANNELS:
            slack_message = message_prefix + redacted_message_body
            notification_tasks.append(
                self.notification_service._notify_slack_with_decorators(
                    slack_message, self.settings.SLACK_API_TIMEOUT_SECONDS
                )
            )
            BUG_NOTIFICATION_DISPATCH.labels(channel="slack").inc()

        if "email" in self.settings.ENABLED_NOTIFICATION_CHANNELS:
            email_subject = (
                f"ARBITER BUG {severity_value.upper()}: {error_details['message'][:70]}"
            )
            email_body = message_prefix + redacted_message_body
            notification_tasks.append(
                self.notification_service._notify_email_with_decorators(
                    email_subject,
                    email_body,
                    self.settings.EMAIL_RECIPIENTS,
                    self.settings.EMAIL_API_TIMEOUT_SECONDS,
                )
            )
            BUG_NOTIFICATION_DISPATCH.labels(channel="email").inc()

        if (
            "pagerduty" in self.settings.ENABLED_NOTIFICATION_CHANNELS
            and severity_value
            in [
                Severity.CRITICAL.value,
                Severity.HIGH.value,
            ]
        ):
            pd_event_type = "trigger"
            pd_description = f"Arbiter Bug: {error_details['message'][:100]}"
            pd_details = {
                "severity": severity_value,
                "location": error_details.get("location", "N/A"),
                "message": error_details["message"],
                "exception_type": error_details.get("exception_type"),
                "timestamp": error_details["timestamp"],
            }
            notification_tasks.append(
                self.notification_service._notify_pagerduty(
                    pd_event_type,
                    pd_description,
                    self.settings.PAGERDUTY_API_TIMEOUT_SECONDS,
                    pd_details,
                )
            )
            BUG_NOTIFICATION_DISPATCH.labels(channel="pagerduty").inc()

        if notification_tasks:
            results = await asyncio.gather(*notification_tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    channel_name = "unknown"
                    if isinstance(result, NotificationError):
                        channel_name = result.channel
                    logger.error(
                        json.dumps(
                            {
                                "event": "notification_failed",
                                "channel": channel_name,
                                "error": str(result),
                            }
                        ),
                        exc_info=True,
                    )
                    BUG_NOTIFICATION_FAILED.labels(channel=channel_name).inc()
                    raise NotificationError(f"A notification channel failed: {result}")


class BugManagerArena(BugManager):
    def __init__(self, settings: Optional[Settings] = None):
        super().__init__(settings if settings is not None else Settings())

    def report(self, error: Exception, **kwargs: Any) -> None:
        """
        Provides a sync-friendly entrypoint to the async report method.
        Schedules the report as a task if a loop is running, otherwise creates a new loop.
        """
        try:
            asyncio.get_running_loop()
            asyncio.create_task(super().report(error, **kwargs))
            logger.debug("Bug report scheduled as an asyncio task.")
        except RuntimeError:
            logger.warning(
                "No running asyncio loop found. Running bug report synchronously."
            )
            try:
                asyncio.run(super().report(error, **kwargs))
                logger.debug("Bug report completed synchronously.")
            except Exception as e:
                logger.error(
                    f"Synchronous BugManager.report failed: {e}", exc_info=True
                )
                BUG_REPORT_FAILED.inc()


@register(kind=PlugInKind.CORE_SERVICE)
async def manage_bug(
    error_data: Union[Exception, str, Dict[str, Any]],
    severity: Union[str, Severity] = Severity.MEDIUM,
    location: Optional[str] = None,
    custom_details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Plugin entry point to report a bug and trigger the BugManager pipeline.
    """
    settings = Settings()
    manager = BugManager(settings)
    try:
        await manager.report(
            error_data=error_data,
            severity=severity,
            location=location,
            custom_details=custom_details,
        )
        return {"status": "Bug report processed successfully."}
    except Exception as e:
        logger.error(f"Plugin 'manage_bug' failed: {e}", exc_info=True)
        return {"status": "failed", "error": str(e)}
    finally:
        await manager.shutdown()
