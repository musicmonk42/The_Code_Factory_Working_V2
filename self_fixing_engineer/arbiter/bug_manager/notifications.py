import asyncio
import collections
import functools
import json
import logging
import random
import time
from typing import Any, Callable, Coroutine, Dict, List, Optional

import aiohttp
import tenacity

try:
    from email.mime.text import MIMEText

    import aiosmtplib
except ImportError:
    aiosmtplib = None
    MIMEText = None

try:
    import redis.asyncio as redis
except ImportError:
    redis = None

from prometheus_client import REGISTRY, Counter, Gauge, Histogram

from .utils import (  # or appropriate relative/absolute path
    CircuitBreakerOpenError,
    NotificationError,
    RateLimitExceededError,
    redact_pii,
    validate_input_details,
)

logger = logging.getLogger(__name__)


def get_or_create_metric(metric_class, name, documentation, labelnames=None):
    for collector in list(REGISTRY._collector_to_names.keys()):
        if hasattr(collector, "_name") and collector._name == name:
            return collector
    try:
        if labelnames:
            return metric_class(name, documentation, labelnames)
        else:
            return metric_class(name, documentation)
    except ValueError:
        for collector in list(REGISTRY._collector_to_names.keys()):
            if hasattr(collector, "_name") and collector._name == name:
                return collector
        raise


# Prometheus Metrics
NOTIFICATION_SEND = get_or_create_metric(
    Counter,
    "notification_send",
    "Total number of notification send attempts",
    ["channel"],
)
NOTIFICATION_SEND_SUCCESS = get_or_create_metric(
    Counter,
    "notification_send_success",
    "Total number of successful notifications sent",
    ["channel"],
)
NOTIFICATION_SEND_FAILED = get_or_create_metric(
    Counter,
    "notification_send_failed",
    "Total number of failed notifications sent",
    ["channel", "reason"],
)
NOTIFICATION_CIRCUIT_BREAKER_OPEN = get_or_create_metric(
    Counter,
    "notification_circuit_breaker_open",
    "Total number of times a notification circuit breaker was open",
    ["channel"],
)
NOTIFICATION_RATE_LIMITED = get_or_create_metric(
    Counter,
    "notification_rate_limited",
    "Total number of times a notification was rate-limited",
    ["channel"],
)
NOTIFICATION_CURRENT_FAILURES_GAUGE = get_or_create_metric(
    Gauge,
    "notification_current_failures",
    "Current consecutive failures for a channel",
    ["channel"],
)
NOTIFICATION_SEND_DURATION_SECONDS = get_or_create_metric(
    Histogram, "notification_send_duration_seconds", "Send duration", ["channel"]
)


class CircuitBreaker:
    """
    Implements a circuit breaker pattern to prevent repeated failures against a service.

    This class can operate using an in-memory state or, for distributed systems,
    a shared state via Redis.
    """

    def __init__(
        self,
        failure_threshold: int,
        recovery_timeout: int,
        half_open_attempts: int = 1,
        redis_url: Optional[str] = None,
        escalation_handler: Optional[Callable] = None,
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_attempts = half_open_attempts
        self.redis_url = redis_url
        self.redis: Optional[redis.Redis] = None
        self._local_lock = asyncio.Lock()
        self._state: Dict[str, tuple] = (
            {}
        )  # In-memory state: {channel: (state, failures, last_failure, attempts)}
        self.escalation_handler = escalation_handler

    async def initialize(self):
        """Initializes the Redis connection if a URL is provided."""
        if self.redis_url and redis:
            try:
                pool = redis.ConnectionPool.from_url(self.redis_url, max_connections=50)
                self.redis = redis.Redis(connection_pool=pool)
                await self.redis.ping()
                logger.info("CircuitBreaker initialized with a valid Redis connection.")
            except Exception as e:
                logger.error(
                    f"Failed to connect to Redis for CircuitBreaker: {e}. Falling back to in-memory state."
                )
                self.redis = None

    async def _get_state(self, channel: str) -> tuple:
        """Retrieves the current state of the circuit breaker for a channel."""
        if self.redis:
            state_data = await self.redis.hgetall(f"circuit_breaker:{channel}")
            return (
                state_data.get(b"state", b"CLOSED").decode(),
                int(state_data.get(b"failures", 0)),
                float(state_data.get(b"last_failure", 0.0)),
                int(state_data.get(b"attempts", 0)),
            )
        return self._state.get(channel, ("CLOSED", 0, 0.0, 0))

    async def _set_state(
        self,
        channel: str,
        state: str,
        failures: int,
        last_failure: float,
        attempts: int,
    ):
        """Sets the state of the circuit breaker for a channel."""
        if self.redis:
            await self.redis.hset(
                f"circuit_breaker:{channel}",
                mapping={
                    "state": state,
                    "failures": failures,
                    "last_failure": last_failure,
                    "attempts": attempts,
                },
            )
        self._state[channel] = (state, failures, last_failure, attempts)

    def __call__(self, channel: str):
        """Decorator to apply the circuit breaker logic to a function."""

        def decorator(func: Callable[..., Coroutine]) -> Callable[..., Coroutine]:
            @functools.wraps(func)
            async def wrapper(*args, **kwargs):
                async with self._local_lock:
                    state, failures, last_failure, attempts = await self._get_state(channel)
                    now = time.time()

                    if state == "OPEN":
                        if now - last_failure > self.recovery_timeout:
                            state, attempts = "HALF_OPEN", 0
                            logger.info(
                                json.dumps(
                                    {
                                        "event": "circuit_breaker_state_change",
                                        "channel": channel,
                                        "old_state": "OPEN",
                                        "new_state": "HALF_OPEN",
                                    }
                                )
                            )
                            await self._set_state(channel, state, failures, last_failure, attempts)
                        else:
                            NOTIFICATION_CIRCUIT_BREAKER_OPEN.labels(channel=channel).inc()
                            logger.warning(
                                json.dumps(
                                    {
                                        "event": "circuit_breaker_open",
                                        "channel": channel,
                                    }
                                )
                            )
                            raise CircuitBreakerOpenError(f"Circuit breaker for {channel} is OPEN.")

                    if state == "HALF_OPEN":
                        if attempts >= self.half_open_attempts:
                            state, last_failure = "OPEN", now
                            logger.warning(
                                json.dumps(
                                    {
                                        "event": "circuit_breaker_state_change",
                                        "channel": channel,
                                        "old_state": "HALF_OPEN",
                                        "new_state": "OPEN",
                                        "reason": "failed_half_open_attempts",
                                    }
                                )
                            )
                            await self._set_state(channel, state, failures, last_failure, attempts)
                            NOTIFICATION_CIRCUIT_BREAKER_OPEN.labels(channel=channel).inc()
                            raise CircuitBreakerOpenError(f"Circuit breaker for {channel} is OPEN.")
                        else:
                            attempts += 1
                            logger.info(
                                json.dumps(
                                    {
                                        "event": "circuit_breaker_half_open_attempt",
                                        "channel": channel,
                                        "attempt": attempts,
                                        "max_attempts": self.half_open_attempts,
                                    }
                                )
                            )
                            await self._set_state(channel, state, failures, last_failure, attempts)

                try:
                    result = await func(*args, **kwargs)
                    async with self._local_lock:
                        # Re-fetch state in case it changed during the call
                        current_state, _, _, _ = await self._get_state(channel)
                        if current_state != "CLOSED":
                            logger.info(
                                json.dumps(
                                    {
                                        "event": "circuit_breaker_state_change",
                                        "channel": channel,
                                        "old_state": current_state,
                                        "new_state": "CLOSED",
                                        "reason": "successful_call",
                                    }
                                )
                            )
                            await self._set_state(channel, "CLOSED", 0, 0.0, 0)
                            NOTIFICATION_CURRENT_FAILURES_GAUGE.labels(channel=channel).set(0)
                    return result
                except Exception as e:
                    async with self._local_lock:
                        # Re-fetch state before modification
                        state, failures, last_failure, attempts = await self._get_state(channel)
                        failures += 1
                        last_failure = time.time()
                        NOTIFICATION_CURRENT_FAILURES_GAUGE.labels(channel=channel).set(failures)
                        if state == "HALF_OPEN" or failures >= self.failure_threshold:
                            state = "OPEN"
                            logger.error(
                                json.dumps(
                                    {
                                        "event": "circuit_breaker_state_change",
                                        "channel": channel,
                                        "old_state": state,
                                        "new_state": "OPEN",
                                        "reason": "failure_threshold_exceeded",
                                        "failures": failures,
                                    }
                                )
                            )
                            if self.escalation_handler:
                                try:
                                    await self.escalation_handler(channel, failures)
                                except Exception as handler_e:
                                    logger.error(
                                        json.dumps(
                                            {
                                                "event": "escalation_handler_failed",
                                                "channel": channel,
                                                "error": str(handler_e),
                                            }
                                        )
                                    )
                        await self._set_state(channel, state, failures, last_failure, attempts)
                    raise e

            return wrapper

        return decorator


class RateLimiter:
    """
    Implements a rate limiter using a sliding window.

    This class can operate using an in-memory state or, for distributed systems,
    a shared state via Redis.
    """

    def __init__(self, redis_url: Optional[str] = None):
        self._call_timestamps = collections.defaultdict(collections.deque)
        self._local_lock = asyncio.Lock()
        self.redis_url = redis_url
        self.redis: Optional[redis.Redis] = None
        self._max_size = 10000

    async def initialize(self):
        """Initializes the Redis connection if a URL is provided."""
        if self.redis_url and redis:
            try:
                pool = redis.ConnectionPool.from_url(self.redis_url, max_connections=50)
                self.redis = redis.Redis(connection_pool=pool)
                await self.redis.ping()
                logger.info("RateLimiter initialized with a valid Redis connection.")
            except Exception as e:
                logger.error(
                    f"Failed to connect to Redis for RateLimiter: {e}. Falling back to in-memory state."
                )
                self.redis = None

    def rate_limit(self, channel: str, max_calls: int, period: int):
        """Decorator to apply rate limiting logic."""

        def decorator(func: Callable[..., Coroutine]) -> Callable[..., Coroutine]:
            @functools.wraps(func)
            async def wrapper(*args, **kwargs):
                now = time.time()
                if self.redis:
                    key = f"rate_limit:{channel}"
                    async with self.redis.pipeline(transaction=True) as pipe:
                        pipe.zremrangebyscore(key, 0, now - period)
                        pipe.zcard(key)
                        pipe.zadd(key, {str(now): now})
                        pipe.expire(key, period)
                        results = await pipe.execute()

                    count = results[1]
                    if count >= max_calls:
                        logger.warning(
                            json.dumps(
                                {
                                    "event": "rate_limit_exceeded",
                                    "channel": channel,
                                    "strategy": "redis",
                                    "count": count,
                                    "limit": max_calls,
                                }
                            )
                        )
                        NOTIFICATION_RATE_LIMITED.labels(channel=channel).inc()
                        raise RateLimitExceededError(f"Rate limit exceeded for channel {channel}.")
                else:  # In-memory fallback
                    async with self._local_lock:
                        timestamps = self._call_timestamps[channel]
                        while timestamps and timestamps[0] <= now - period:
                            timestamps.popleft()

                        if len(self._call_timestamps) > self._max_size:
                            oldest_key = next(iter(self._call_timestamps))
                            del self._call_timestamps[oldest_key]
                            logger.warning(
                                json.dumps(
                                    {
                                        "event": "rate_limiter_cache_evicted",
                                        "oldest_key": oldest_key,
                                    }
                                )
                            )

                        if len(timestamps) >= max_calls:
                            logger.warning(
                                json.dumps(
                                    {
                                        "event": "rate_limit_exceeded",
                                        "channel": channel,
                                        "strategy": "in_memory",
                                        "count": len(timestamps),
                                        "limit": max_calls,
                                    }
                                )
                            )
                            NOTIFICATION_RATE_LIMITED.labels(channel=channel).inc()
                            raise RateLimitExceededError(
                                f"Rate limit exceeded for channel {channel}."
                            )

                        timestamps.append(now)

                return await func(*args, **kwargs)

            return wrapper

        return decorator


def default_escalation_handler(channel: str, failures: int) -> None:
    """A default handler for circuit breaker escalations."""
    logger.critical(
        f"Escalating: Circuit breaker for channel '{channel}' is OPEN after {failures} failures."
    )


class NotificationService:
    """
    A service for sending notifications via different channels (Slack, Email, PagerDuty).

    It incorporates advanced resilience patterns like Circuit Breakers and Rate Limiters.
    """

    _critical_notification_handler: Optional[
        Callable[[str, int, str], Coroutine[Any, Any, None]]
    ] = None

    @classmethod
    def register_critical_notification_handler(
        cls, handler: Callable[[str, int, str], Coroutine[Any, Any, None]]
    ):
        """
        Registers a handler for critical notifications, such as when an escalation is needed.
        """
        if (
            cls._critical_notification_handler is not None
            and cls._critical_notification_handler != handler
        ):
            logger.warning("Critical notification handler is being replaced.")
        if not asyncio.iscoroutinefunction(handler):
            logger.warning("Critical notification handler should be an async function.")
        cls._critical_notification_handler = handler
        logger.info(f"Critical notification handler '{handler.__name__}' registered.")

    def __init__(self, settings: Any):
        self.settings = settings
        self._notification_failures: Dict[str, collections.deque] = collections.defaultdict(
            collections.deque
        )
        self._last_escalation_time: Dict[str, float] = collections.defaultdict(float)
        self._escalation_lock = asyncio.Lock()
        self._session: Optional[aiohttp.ClientSession] = None

        # Initialize circuit breaker and rate limiter instances from settings
        self.circuit_breaker = CircuitBreaker(
            failure_threshold=getattr(settings, "NOTIFICATION_FAILURE_THRESHOLD", 5),
            recovery_timeout=getattr(settings, "NOTIFICATION_RECOVERY_TIMEOUT_SECONDS", 300),
            half_open_attempts=getattr(settings, "NOTIFICATION_HALF_OPEN_ATTEMPTS", 1),
            redis_url=getattr(settings, "NOTIFICATION_REDIS_URL", None),
            escalation_handler=getattr(
                settings,
                "NOTIFICATION_CIRCUIT_BREAKER_ESCALATION_HANDLER",
                default_escalation_handler,
            ),
        )
        self.rate_limiter = RateLimiter(redis_url=getattr(settings, "NOTIFICATION_REDIS_URL", None))

        # Settings for retries
        self.retry_attempts = getattr(settings, "NOTIFICATION_RETRY_ATTEMPTS", 3)
        self.retry_wait_seconds = getattr(settings, "NOTIFICATION_RETRY_WAIT_SECONDS", 2)

        # Validate settings
        self._validate_settings()

        asyncio.create_task(self._initialize())

    def _validate_settings(self):
        """Validates critical settings on startup."""
        enabled_channels = getattr(self.settings, "ENABLED_NOTIFICATION_CHANNELS", [])
        if "slack" in enabled_channels and not self.settings.SLACK_WEBHOOK_URL:
            raise ValueError("Slack URL missing but channel is enabled.")
        if "email" in enabled_channels:
            if not self.settings.EMAIL_ENABLED:
                raise ValueError("Email channel is enabled but EMAIL_ENABLED is False.")
            if not all(
                [
                    self.settings.EMAIL_SMTP_SERVER,
                    self.settings.EMAIL_SENDER,
                    self.settings.EMAIL_SMTP_USERNAME,
                    self.settings.EMAIL_SMTP_PASSWORD,
                ]
            ):
                raise ValueError("Email SMTP settings incomplete but channel is enabled.")
        if "pagerduty" in enabled_channels and not getattr(
            self.settings, "PAGERDUTY_ROUTING_KEY", None
        ):
            raise ValueError("PagerDuty routing key missing but channel is enabled.")

    async def _initialize(self):
        """
        Performs asynchronous initialization tasks, including setting up
        client sessions and Redis connections.
        """
        connector = aiohttp.TCPConnector(limit=getattr(self.settings, "AIOHTTP_CONN_LIMIT", 100))
        self._session = aiohttp.ClientSession(connector=connector)
        await self.circuit_breaker.initialize()
        await self.rate_limiter.initialize()

    async def shutdown(self):
        """Gracefully shuts down the service, closing all open connections."""
        if self._session and not self._session.closed:
            await self._session.close()
        if self.circuit_breaker.redis:
            await self.circuit_breaker.redis.close()
        if self.rate_limiter.redis:
            await self.rate_limiter.redis.close()

    async def _check_and_escalate(self, channel: str, message: str) -> None:
        """
        Checks if consecutive failures for a channel have reached the threshold
        and triggers an escalation handler if registered.
        """
        now = time.time()
        async with self._escalation_lock:
            timestamps = self._notification_failures[channel]
            while (
                timestamps
                and timestamps[0] <= now - self.settings.NOTIFICATION_FAILURE_WINDOW_SECONDS
            ):
                timestamps.popleft()

            consecutive_failures = len(timestamps)
            NOTIFICATION_CURRENT_FAILURES_GAUGE.labels(channel=channel).set(consecutive_failures)

            if consecutive_failures >= self.settings.NOTIFICATION_FAILURE_THRESHOLD:
                if (
                    now - self._last_escalation_time[channel]
                    > self.settings.NOTIFICATION_FAILURE_WINDOW_SECONDS * 2
                ):
                    if NotificationService._critical_notification_handler:
                        logger.critical(
                            json.dumps(
                                {
                                    "event": "notification_escalation_triggered",
                                    "channel": channel,
                                    "failures": consecutive_failures,
                                    "window_seconds": self.settings.NOTIFICATION_FAILURE_WINDOW_SECONDS,
                                }
                            )
                        )
                        try:
                            await NotificationService._critical_notification_handler(
                                channel, consecutive_failures, message
                            )
                            self._last_escalation_time[channel] = now
                        except Exception as e:
                            logger.error(
                                f"Critical notification handler failed: {e}",
                                exc_info=True,
                            )
                    else:
                        logger.critical(
                            json.dumps(
                                {
                                    "event": "notification_escalation_suppressed",
                                    "channel": channel,
                                    "reason": "no_handler_registered",
                                }
                            )
                        )
                else:
                    logger.warning(
                        json.dumps(
                            {
                                "event": "notification_escalation_suppressed",
                                "channel": channel,
                                "reason": "recent_escalation",
                            }
                        )
                    )

    async def _record_notification_failure(
        self, channel: str, message: str, error_code: str
    ) -> None:
        """Records a failed notification attempt and triggers an escalation check."""
        self._notification_failures[channel].append(time.time())
        NOTIFICATION_SEND_FAILED.labels(channel=channel, reason=error_code).inc()
        await self._check_and_escalate(channel, message)

    async def _record_notification_success(self, channel: str) -> None:
        """Records a successful notification attempt and resets the failure counter."""
        self._notification_failures[channel].clear()
        NOTIFICATION_CURRENT_FAILURES_GAUGE.labels(channel=channel).set(0)
        NOTIFICATION_SEND_SUCCESS.labels(channel=channel).inc()
        logger.info(json.dumps({"event": "send_success", "channel": channel}))

    def _simulate_failure(self, channel: str) -> None:
        """Simulates a failure based on a configured rate."""
        failure_rate = getattr(self.settings, f"{channel.upper()}_FAILURE_RATE", 0.0)
        if random.random() < failure_rate:
            logger.warning(
                json.dumps(
                    {
                        "event": "simulated_failure",
                        "channel": channel,
                        "rate": failure_rate,
                    }
                )
            )
            raise NotificationError(f"Simulated {channel} failure.", channel, "SIMULATED_FAILURE")

    @property
    def _notify_slack_with_decorators(self):
        """Dynamically applies decorators for the Slack notification method."""

        @self.circuit_breaker(channel="slack")
        @self.rate_limiter.rate_limit(
            channel="slack",
            max_calls=getattr(self.settings, "SLACK_RATE_LIMIT_MAX_CALLS", 10),
            period=getattr(self.settings, "SLACK_RATE_LIMIT_PERIOD", 60),
        )
        async def _notify_slack(self, message: str, timeout: float) -> bool:
            """Sends a notification to Slack."""
            if not self.settings.SLACK_WEBHOOK_URL:
                logger.warning("Slack webhook URL not configured.")
                await self._record_notification_failure(
                    "slack", "Slack webhook URL not configured.", "CONFIG_MISSING"
                )
                raise NotificationError(
                    "Slack webhook URL not configured.", "slack", "CONFIG_MISSING"
                )

            NOTIFICATION_SEND.labels(channel="slack").inc()
            self._simulate_failure("slack")

            redacted_message = redact_pii({"message": message}).get("message")
            payload = {"text": redacted_message}

            headers = {}
            if hasattr(self.settings, "SLACK_AUTH_TOKEN"):
                headers["Authorization"] = (
                    f"Bearer {self.settings.SLACK_AUTH_TOKEN.get_secret_value()}"
                )

            with NOTIFICATION_SEND_DURATION_SECONDS.labels(channel="slack").time():
                try:
                    async with self._session.post(
                        self.settings.SLACK_WEBHOOK_URL,
                        json=payload,
                        timeout=timeout,
                        headers=headers,
                        ssl=True,
                    ) as response:
                        response.raise_for_status()
                        await self._record_notification_success("slack")
                        return True
                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    error_code = "TIMEOUT" if isinstance(e, asyncio.TimeoutError) else "API_ERROR"
                    error_message = f"Slack notification failed: {e}"
                    logger.error(
                        json.dumps(
                            {
                                "event": "send_failure",
                                "channel": "slack",
                                "error_code": error_code,
                                "error_message": error_message,
                            }
                        )
                    )
                    await self._record_notification_failure("slack", error_message, error_code)
                    raise NotificationError(error_message, "slack", error_code) from e
                except Exception as e:
                    await self._record_notification_failure(
                        "slack", f"Unexpected Slack error: {e}", "UNEXPECTED_ERROR"
                    )
                    raise NotificationError(
                        f"Unexpected error sending Slack notification: {e}",
                        "slack",
                        "UNEXPECTED_ERROR",
                    ) from e

        return functools.partial(_notify_slack, self)

    @property
    def _notify_email_with_decorators(self):
        """Dynamically applies decorators for the Email notification method."""

        @self.circuit_breaker(channel="email")
        @self.rate_limiter.rate_limit(
            channel="email",
            max_calls=getattr(self.settings, "EMAIL_RATE_LIMIT_MAX_CALLS", 5),
            period=getattr(self.settings, "EMAIL_RATE_LIMIT_PERIOD", 300),
        )
        @tenacity.retry(
            stop=tenacity.stop_after_attempt(self.retry_attempts),
            wait=tenacity.wait_exponential(multiplier=1, min=1, max=10),
            retry=(
                tenacity.retry_if_exception_type(aiohttp.ClientError)
                | tenacity.retry_if_exception_type(asyncio.TimeoutError)
                | tenacity.retry_if_exception_type(aiosmtplib.SMTPException)
            ),
            before_sleep=tenacity.before_sleep_log(logger, logging.WARNING),
            reraise=True,
        )
        async def _notify_email(
            self, subject: str, body: str, recipients: List[str], timeout: float
        ) -> bool:
            """Sends an email notification."""
            NOTIFICATION_SEND.labels(channel="email").inc()
            self._simulate_failure("email")

            # Pre-flight checks
            if (
                not self.settings.EMAIL_ENABLED
                or "email" not in self.settings.ENABLED_NOTIFICATION_CHANNELS
            ):
                logger.debug("Email notifications disabled.")
                return False
            if not all(
                [
                    self.settings.EMAIL_SMTP_SERVER,
                    self.settings.EMAIL_SENDER,
                    self.settings.EMAIL_SMTP_USERNAME,
                    self.settings.EMAIL_SMTP_PASSWORD,
                ]
            ):
                await self._record_notification_failure(
                    "email", "Email SMTP settings incomplete.", "CONFIG_MISSING"
                )
                raise NotificationError(
                    "Email SMTP settings incomplete.", "email", "CONFIG_MISSING"
                )

            with NOTIFICATION_SEND_DURATION_SECONDS.labels(channel="email").time():
                try:
                    redacted_body = redact_pii({"body": body}).get("body")
                    msg = MIMEText(redacted_body)
                    msg["Subject"] = subject
                    msg["From"] = self.settings.EMAIL_SENDER
                    msg["To"] = ", ".join(recipients)

                    smtp = aiosmtplib.SMTP(
                        hostname=self.settings.EMAIL_SMTP_SERVER,
                        port=self.settings.EMAIL_SMTP_PORT,
                        use_tls=False,
                        timeout=timeout,
                    )
                    await smtp.connect()

                    if self.settings.EMAIL_USE_STARTTLS:
                        await smtp.starttls()
                    else:
                        logger.warning("STARTTLS disabled - insecure; forcing enable if possible.")
                        try:
                            await smtp.starttls()
                        except Exception:
                            logger.warning("Failed to enable STARTTLS. Continuing without it.")

                    await smtp.login(
                        self.settings.EMAIL_SMTP_USERNAME,
                        self.settings.EMAIL_SMTP_PASSWORD.get_secret_value(),
                    )
                    await smtp.send_message(msg)
                    await smtp.quit()

                    redacted_recipients = redact_pii({"recipients": ", ".join(recipients)})[
                        "recipients"
                    ]
                    logger.info(
                        json.dumps(
                            {
                                "event": "send_success",
                                "channel": "email",
                                "recipients": redacted_recipients,
                            }
                        )
                    )
                    await self._record_notification_success("email")
                    return True
                except aiosmtplib.SMTPException as e:
                    await self._record_notification_failure(
                        "email", f"SMTP error: {e}", "SMTP_ERROR"
                    )
                    raise NotificationError(
                        f"Failed to send email via SMTP: {e}", "email", "SMTP_ERROR"
                    ) from e
                except Exception as e:
                    error_code = (
                        "TIMEOUT" if isinstance(e, asyncio.TimeoutError) else "UNEXPECTED_ERROR"
                    )
                    await self._record_notification_failure(
                        "email", f"Unexpected email error: {e}", error_code
                    )
                    raise NotificationError(
                        f"Unexpected error sending email: {e}", "email", error_code
                    ) from e

        return functools.partial(_notify_email, self)

    @property
    def _notify_pagerduty_with_decorators(self):
        """Dynamically applies decorators for the PagerDuty notification method."""

        @self.circuit_breaker(channel="pagerduty")
        @self.rate_limiter.rate_limit(
            channel="pagerduty",
            max_calls=getattr(self.settings, "PAGERDUTY_RATE_LIMIT_MAX_CALLS", 15),
            period=getattr(self.settings, "PAGERDUTY_RATE_LIMIT_PERIOD", 60),
        )
        async def _notify_pagerduty(
            self,
            event_type: str,
            description: str,
            timeout: float,
            details: Optional[Dict[str, Any]] = None,
        ) -> bool:
            """Triggers an event in PagerDuty."""
            if (
                not self.settings.PAGERDUTY_ROUTING_KEY
                or not self.settings.PAGERDUTY_ROUTING_KEY.get_secret_value()
            ):
                await self._record_notification_failure(
                    "pagerduty",
                    "PagerDuty routing key not configured.",
                    "CONFIG_MISSING",
                )
                raise NotificationError(
                    "PagerDuty routing key not configured.",
                    "pagerduty",
                    "CONFIG_MISSING",
                )

            NOTIFICATION_SEND.labels(channel="pagerduty").inc()
            self._simulate_failure("pagerduty")

            redacted_details = redact_pii(details) if details else {}
            payload = {
                "routing_key": self.settings.PAGERDUTY_ROUTING_KEY.get_secret_value(),
                "event_action": event_type,
                "payload": {
                    "summary": description,
                    "source": "ArbiterAI.BugManager",
                    "severity": "critical",  # Default to critical for PD
                    "custom_details": (
                        validate_input_details(redacted_details) if redacted_details else {}
                    ),
                },
            }

            headers = {}
            if getattr(self.settings, "PAGERDUTY_API_KEY", None):
                headers["Authorization"] = (
                    f"Token token={self.settings.PAGERDUTY_API_KEY.get_secret_value()}"
                )

            with NOTIFICATION_SEND_DURATION_SECONDS.labels(channel="pagerduty").time():
                try:
                    async with self._session.post(
                        "https://events.pagerduty.com/v2/enqueue",
                        json=payload,
                        timeout=timeout,
                        headers=headers,
                        ssl=True,
                    ) as response:
                        response.raise_for_status()
                        await self._record_notification_success("pagerduty")
                        return True
                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    error_code = "TIMEOUT" if isinstance(e, asyncio.TimeoutError) else "API_ERROR"
                    error_message = f"PagerDuty API failed: {e}"
                    logger.error(
                        json.dumps(
                            {
                                "event": "send_failure",
                                "channel": "pagerduty",
                                "error_code": error_code,
                                "error_message": error_message,
                            }
                        )
                    )
                    await self._record_notification_failure("pagerduty", error_message, error_code)
                    raise NotificationError(error_message, "pagerduty", error_code) from e
                except Exception as e:
                    await self._record_notification_failure(
                        "pagerduty",
                        f"Unexpected PagerDuty error: {e}",
                        "UNEXPECTED_ERROR",
                    )
                    raise NotificationError(
                        f"Unexpected error triggering PagerDuty event: {e}",
                        "pagerduty",
                        "UNEXPECTED_ERROR",
                    ) from e

        return functools.partial(_notify_pagerduty, self)

    async def notify_batch(self, notifications: List[Dict[str, Any]]) -> List[Any]:
        """
        Sends multiple notifications concurrently, limiting the number of
        simultaneous tasks with a semaphore.

        Args:
            notifications (List[Dict]): A list of dictionaries, where each dict
                                        represents a notification for a specific
                                        channel with its payload.

        Returns:
            List[Any]: A list containing the results of each notification task,
                       including raised exceptions.
        """
        sem = asyncio.Semaphore(getattr(self.settings, "NOTIFICATION_BATCH_CONCURRENCY", 10))
        tasks = []

        async def bounded_task(task):
            async with sem:
                return await task

        for notif in notifications:
            channel = notif.get("channel")
            if channel == "slack":
                tasks.append(
                    self._notify_slack_with_decorators(
                        notif.get("message", ""),
                        self.settings.SLACK_API_TIMEOUT_SECONDS,
                    )
                )
            elif channel == "email":
                tasks.append(
                    self._notify_email_with_decorators(
                        notif.get("subject", ""),
                        notif.get("body", ""),
                        notif.get("recipients", self.settings.EMAIL_RECIPIENTS),
                        self.settings.EMAIL_API_TIMEOUT_SECONDS,
                    )
                )
            elif channel == "pagerduty":
                tasks.append(
                    self._notify_pagerduty_with_decorators(
                        notif.get("event_type", "trigger"),
                        notif.get("description", ""),
                        self.settings.PAGERDUTY_API_TIMEOUT_SECONDS,
                        notif.get("details"),
                    )
                )
            else:
                logger.error(json.dumps({"event": "unknown_channel_in_batch", "channel": channel}))

                # Create a failed task for unknown channels
                async def failed_task():
                    raise NotificationError(
                        f"Unknown channel: {channel}", str(channel), "INVALID_CHANNEL"
                    )

                tasks.append(failed_task())

        tasks = [bounded_task(t) for t in tasks]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return results
