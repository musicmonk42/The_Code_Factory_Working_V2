import logging
import re
import threading
from typing import Any, Dict, List, Optional, Union
from pathlib import Path
import smtplib
from email.message import EmailMessage
import requests
from logging.handlers import RotatingFileHandler
import sys
import queue
import time
import random
import ssl
import json
from collections import deque
from hashlib import sha256
import atexit
from os import path as _ospath
from core_secrets import SecretsManager
from core_audit import AuditLogger

# --- Helper Functions ---
_SENSITIVE_KV = re.compile(
    r'(?i)(?P<k>\b(pass(word)?|token|secret|api[_-]?key|access[_-]?key|private[_-]?key)\b)\s*[:=]\s*(?P<v>[^,\s;\'"]+)'
)
_SENSITIVE_QP = re.compile(r'(?i)([?&](?:token|key|signature|sig|auth)=)([^&]+)')
_JWT = re.compile(r'\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b')
_AUTH = re.compile(r'(?i)\b(authorization:?\s*(?:bearer|token)\s+)[^\s]+')
_SLACK_WEBHOOK = re.compile(r'https://hooks\.slack\.com/[^ \t\r\n]+', re.I)
_API_KEY = re.compile(r'\b(?:sk|pk)_[A-Za-z0-9]{16,}\b')
_AWS_KEY = re.compile(r'\bAKIA[0-9A-Z]{16}\b')
_GCP_KEY = re.compile(r'AIza[0-9A-Za-z\-_]{35}')
_PRIVATE_KEY_BLOCK = re.compile(r'-----BEGIN (?:RSA|EC|OPENSSH|PGP) PRIVATE KEY-----.+?-----END (?:RSA|EC|OPENSSH|PGP) PRIVATE KEY-----', re.S)

def _scrub_str(s: str) -> str:
    """Redacts sensitive patterns from a string."""
    s = re.sub(_PRIVATE_KEY_BLOCK, '***REDACTED***', s)
    s = re.sub(_AUTH, r'\1***REDACTED***', s)
    s = re.sub(_SENSITIVE_QP, r'\1***REDACTED***', s)
    s = re.sub(_SENSITIVE_KV, r'\g<k>=***REDACTED***', s)
    s = _JWT.sub('***REDACTED***', s)
    s = _SLACK_WEBHOOK.sub('***REDACTED***', s)
    s = _API_KEY.sub('***REDACTED***', s)
    s = _AWS_KEY.sub('***REDACTED***', s)
    s = _GCP_KEY.sub('***REDACTED***', s)
    return s

def scrub(obj: Any) -> str:
    """Best-effort redaction for strings and simple containers, returning a string."""
    if obj is None:
        return ""
    if isinstance(obj, str):
        return _scrub_str(obj)
    if isinstance(obj, (dict, list, tuple, set)):
        try:
            return _scrub_str(json.dumps(obj, default=str))
        except Exception:
            return _scrub_str(str(obj))
    return _scrub_str(str(obj))

def safe_err(exc: Exception) -> str:
    """Safely converts an exception to a string with redaction."""
    return scrub(str(exc))

def _truncate(s: str, max_len: int) -> str:
    """Truncates a string to a maximum length, adding ellipsis if truncated."""
    return s if len(s) <= max_len else (s[: max_len - 3] + "...")

def _reject_header_injection(*values: str) -> None:
    """Raises a ValueError if a newline character is found in any of the provided strings."""
    for v in values:
        if v and any(ch in v for ch in ("\r", "\n")):
            raise ValueError("Email header injection detected.")

# --- Classes ---
class AlertDispatcher(threading.Thread):
    """
    A worker thread to asynchronously dispatch alerts to external sinks like Slack and email.
    """
    def __init__(self, operator: 'AlertOperator'):
        super().__init__(daemon=True, name="alert-dispatcher")
        self.operator = operator
        self.queue = queue.Queue(
            maxsize=self.operator.secrets_manager.get_int("ALERT_QUEUE_MAX_SIZE", default=1000)
        )
        self._stop_event = threading.Event()
        self._accepting = True
        self._session = requests.Session()
        self.operator.logger.info("AlertDispatcher initialized.")

    def run(self):
        """The main loop for the worker thread."""
        while True:
            try:
                item = self.queue.get(timeout=1)
            except queue.Empty:
                if self._stop_event.is_set() and self.queue.empty():
                    break
                continue

            try:
                if item is None:
                    # mark the sentinel as "done" to not block join()
                    self.queue.task_done()
                    break

                sink_type, data = item
                if sink_type == "slack":
                    self._dispatch_slack(data)
                elif sink_type == "email":
                    self._dispatch_email(data)
                else:
                    self.operator.logger.error(f"Unknown sink type: {sink_type!r}")
            except Exception as e:
                self.operator.logger.error(f"Unhandled error in AlertDispatcher worker: {safe_err(e)}")
                self.operator.audit_logger.log_event(
                    event_type="alert_worker_error", message=safe_err(e), severity="ERROR"
                )
            finally:
                if item is not None:
                    self.queue.task_done()

        self.operator.logger.info("AlertDispatcher worker has stopped.")

    def stop(self, timeout: int = 10, drain: bool = True):
        """Gracefully shuts down the dispatcher."""
        self.operator.logger.info("AlertDispatcher shutdown initiated...")
        self._accepting = False
    
        if drain:
            self.queue.join()
    
        self._stop_event.set()
        try:
            self.queue.put_nowait(None)
        except queue.Full:
            pass

        super().join(timeout)
        if self.is_alive():
            self.operator.logger.warning("AlertDispatcher did not stop within timeout.")
        try:
            self._session.close()
        except Exception:
            pass
    
    def enqueue(self, sink_type: str, data: Dict[str, Any]):
        """Adds an alert to the queue for processing."""
        if not self._accepting:
            self.operator.logger.debug("Dispatcher not accepting new alerts; dropping.")
            return
        
        try:
            self.queue.put_nowait((sink_type, data))
        except queue.Full:
            self.operator.logger.warning("Alert queue is full. Dropping message.")
            self.operator._log_rate_limited_alert("queue_full")

    def _post_with_retry(self, url: str, payload: dict, timeout: int, attempts: int) -> None:
        """Handles HTTP POST requests with retries and exponential backoff."""
        last_exc = None
        connect_timeout = timeout[0] if isinstance(timeout, tuple) else timeout
        read_timeout = timeout[1] if isinstance(timeout, tuple) else timeout
        
        for i in range(attempts):
            try:
                r = self._session.post(url, json=payload, timeout=(connect_timeout, read_timeout))
                if r.status_code in (429, 500, 502, 503, 504):
                    jitter = random.uniform(0, 1.0)
                    if r.status_code == 429:
                        retry_after = r.headers.get("Retry-After")
                        base = float(retry_after) if retry_after and retry_after.isdigit() else (2 ** i)
                        sleep = min(60, max(0, base)) + jitter
                    else:
                        sleep = min(30, (2 ** i)) + jitter
                    self.operator.logger.warning(
                        f"POST {url} failed with {r.status_code}; retrying in {sleep:.2f}s"
                    )
                    time.sleep(sleep)
                    continue
                r.raise_for_status()
                return
            except requests.RequestException as e:
                last_exc = e
                backoff = min(30, (2 ** i)) + random.uniform(0, 1)
                self.operator.logger.warning(
                    f"Request error (attempt {i+1}/{attempts}); retrying in {backoff:.2f}s: {safe_err(e)}"
                )
                time.sleep(backoff)
        raise last_exc

    def _dispatch_slack(self, data: Dict[str, Any]):
        """Sends an alert to Slack."""
        url = self.operator.secrets_manager.get_secret("SLACK_WEBHOOK_URL")
        if not url:
            return
        
        if not url.startswith("https://hooks.slack.com/"):
            self.operator.logger.error("Invalid Slack webhook URL configured.")
            return
            
        timeout = self.operator.secrets_manager.get_int("SLACK_TIMEOUT", default=5)
        attempts = self.operator.secrets_manager.get_int("SLACK_RETRY_ATTEMPTS", default=3)
        
        payload = {
            "text": f"[{data['level']}] {data['message']}",
            "attachments": [{
                "text": json.dumps(data, indent=2, default=str),
                "mrkdwn_in": ["text"]
            }]
        }
        
        try:
            self._post_with_retry(url, payload, timeout, attempts)
        except Exception as e:
            self.operator.logger.error(f"Slack alert failed: {safe_err(e)}")
            raise

    def _smtp_client(self) -> smtplib.SMTP:
        """Configures and returns an SMTP client with TLS support."""
        host = self.operator.secrets_manager.get_secret("ALERT_SMTP_SERVER")
        port = self.operator.secrets_manager.get_int("ALERT_SMTP_PORT", default=587)
        timeout = self.operator.secrets_manager.get_int("ALERT_SMTP_TIMEOUT", default=10)
        use_ssl = self.operator.secrets_manager.get_bool("ALERT_SMTP_SSL", default=False)
        use_starttls = self.operator.secrets_manager.get_bool("ALERT_SMTP_STARTTLS", default=True)

        ctx = ssl.create_default_context()
        if use_ssl:
            s = smtplib.SMTP_SSL(host=host, port=port, timeout=timeout, context=ctx)
            s.ehlo()
            return s

        s = smtplib.SMTP(host=host, port=port, timeout=timeout)
        s.ehlo()
        if use_starttls and s.has_extn("starttls"):
            s.starttls(context=ctx)
            s.ehlo()
        return s

    def _dispatch_email(self, data: Dict[str, Any]):
        """Sends an alert via email."""
        email_to = self.operator.secrets_manager.get_secret("ALERT_EMAIL_TO")
        email_from = self.operator.secrets_manager.get_secret("ALERT_EMAIL_FROM")
        smtp_server = self.operator.secrets_manager.get_secret("ALERT_SMTP_SERVER")

        if not (email_to and email_from and smtp_server):
            return
        
        subject_data = data.get("app_name", "unknown_app")
        _reject_header_injection(
            email_from or "",
            email_to or "",
            f'[ALERT][{data["level"]}] {subject_data}'
        )

        smtp_user = self.operator.secrets_manager.get_secret("ALERT_SMTP_USER")
        smtp_pass = self.operator.secrets_manager.get_secret("ALERT_SMTP_PASS")
        
        recipients = [recip.strip() for recip in re.split(r'[,;]', email_to) if recip.strip()]
        if not recipients:
            self.operator.logger.error("No valid email recipients found.")
            return

        message = _truncate(data['message'], self.operator.secrets_manager.get_int("ALERT_MAX_MESSAGE_LEN", default=3500))
        subject = _truncate(
            f'[ALERT][{data["level"]}] {subject_data}',
            self.operator.secrets_manager.get_int("ALERT_EMAIL_SUBJECT_MAX", default=200)
        )
        
        msg = EmailMessage()
        msg['Subject'] = subject
        msg['From'] = email_from
        msg['To'] = ", ".join(recipients)
        msg.set_content(message)
        
        try:
            with self._smtp_client() as server:
                if smtp_user and smtp_pass:
                    server.login(smtp_user, smtp_pass)
                server.send_message(msg)
        except Exception as e:
            self.operator.logger.error(f"Email alert failed: {safe_err(e)}")
            raise

class AlertOperator:
    """
    A thread-safe utility for sending alerts via logging, Slack, or email.
    """
    _instance = None
    _lock = threading.Lock()
    _log_file_warning_issued = False

    def __new__(cls, *args, **kwargs):
        """Implements thread-safe singleton pattern."""
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(AlertOperator, cls).__new__(cls)
                cls._instance._initialized = False
        return cls._instance

    def __init__(self, secrets_manager: SecretsManager = None, audit_logger: AuditLogger = None):
        """
        Initialize the AlertOperator with configuration from SecretsManager and AuditLogger.
        """
        if not self._initialized:
            self.secrets_manager = secrets_manager or SecretsManager()
            self.audit_logger = audit_logger or AuditLogger(secrets_manager=self.secrets_manager)
            self._lock = threading.Lock()
            self.logger = logging.getLogger("alert_operator")
            self._configure_logger()
            self._context = self._load_context()
            self._dispatcher = AlertDispatcher(self)
            self._dispatcher.start()
            self._rl: Dict[str, deque] = {}
            self._rl_lock = threading.Lock()
            self._initialized = True
            atexit.register(self._on_exit)

    def _on_exit(self):
        """Graceful shutdown hook for the dispatcher."""
        try:
            if getattr(self, "_dispatcher", None) and self._dispatcher.is_alive():
                self._dispatcher.stop(drain=True)
        except Exception:
            pass

    def _configure_logger(self) -> None:
        """Configures the logger with a rotating file handler or stdout handler."""
        if self.logger.handlers:
            for handler in self.logger.handlers[:]:
                self.logger.removeHandler(handler)

        self.logger.propagate = False

        log_file_path = self.secrets_manager.get_secret("ALERT_LOG_FILE", default=None)

        if log_file_path:
            log_file = Path(_ospath.expanduser(_ospath.expandvars(log_file_path))).resolve()
            log_file.parent.mkdir(parents=True, exist_ok=True)
            try:
                log_file.touch(exist_ok=True)
                log_file.chmod(0o600)
            except OSError as e:
                if not AlertOperator._log_file_warning_issued:
                    self.logger.warning(f"Failed to set secure permissions on log file '{log_file}': {e}")
                    AlertOperator._log_file_warning_issued = True

            handler = RotatingFileHandler(
                filename=str(log_file),
                maxBytes=self.secrets_manager.get_int("ALERT_LOG_MAX_BYTES", default=10 * 1024 * 1024),
                backupCount=self.secrets_manager.get_int("ALERT_LOG_BACKUP_COUNT", default=5),
                encoding="utf-8",
                delay=True,
            )
            fmt = logging.Formatter('%(asctime)s - [%(levelname)s] - %(message)s')
        else:
            handler = logging.StreamHandler(sys.stdout)
            fmt = logging.Formatter(
                '{"timestamp":"%(asctime)s","level":"%(levelname)s","message":"%(message)s"}'
            )

        handler.setFormatter(fmt)
        self.logger.addHandler(handler)
        
        level = self.secrets_manager.get_secret("ALERT_LOG_LEVEL", default="INFO").upper()
        self.logger.setLevel(getattr(logging, level, logging.INFO))

    def _load_context(self) -> Dict[str, Any]:
        """Load global context metadata from SecretsManager."""
        return {
            "app_name": self.secrets_manager.get_secret("APP_NAME", default="unknown_app"),
            "environment": self.secrets_manager.get_secret("ENVIRONMENT", default="unknown")
        }

    def _get_signature(self, message: str) -> str:
        """Generates a short, consistent signature for a message."""
        return sha256(message.encode('utf-8')).hexdigest()[:16]

    def _log_rate_limited_alert(self, key: str):
        """Logs a single message for a rate-limited event."""
        with self._rl_lock:
            rl_key = f"rl_{key}"
            now = time.time()
            if rl_key not in self._rl:
                self._rl[rl_key] = deque()
            
            q = self._rl[rl_key]
            while q and now - q[0] > self.secrets_manager.get_int("ALERT_RL_WINDOW_SEC", default=60):
                q.popleft()
            
            if not q:
                self.logger.warning(f"Alerts for '{key}' are being rate-limited.")
                q.append(now)

    def _allow_event(self, key: str) -> bool:
        """Token bucket implementation for rate limiting."""
        with self._rl_lock:
            now = time.time()
            q = self._rl.get(key)
            if q is None:
                if len(self._rl) >= self.secrets_manager.get_int("ALERT_RL_MAX_KEYS", default=2000):
                    self._rl.pop(next(iter(self._rl)))
                q = self._rl[key] = deque()
            
            while q and now - q[0] > self.secrets_manager.get_int("ALERT_RL_WINDOW_SEC", default=60):
                q.popleft()
            
            if len(q) >= self.secrets_manager.get_int("ALERT_RL_MAX_EVENTS", default=5):
                return False
            
            q.append(now)
            return True

    def alert(self, message: str, level: str = "CRITICAL") -> None:
        """
        Send an alert via logging, Slack, email, or other channels, and log as an audit event.

        Args:
            message (str): The alert message.
            level (str): The alert level (e.g., 'CRITICAL', 'ERROR', 'WARNING').

        Raises:
            ValueError: If message or level is invalid.
        """
        if not message or not isinstance(message, str):
            self.logger.error(f"Invalid alert message: '{message}'")
            raise ValueError("Alert message must be a non-empty string")
        if not level or not isinstance(level, str):
            self.logger.error(f"Invalid alert level: '{level}'")
            raise ValueError("Alert level must be a non-empty string")

        level = level.upper()
        
        safe_message = scrub(message)
        message_signature = self._get_signature(safe_message)
        
        if not self._allow_event(f"{level}_{message_signature}"):
            self.logger.info(f"Rate-limiting alert with signature {message_signature}.")
            return
            
        with self._lock:
            ctx_copy = dict(self._context)
        
        try:
            log_level_val = getattr(logging, level, logging.CRITICAL)
            self.logger.log(log_level_val, safe_message)
        except Exception as e:
            self.logger.error(f"Failed to log to file/stdout: {e}")

        try:
            self.audit_logger.log_event(
                event_type=f"alert_{level.lower()}",
                message=safe_message,
                alert_level=level,
                message_signature=message_signature,
                **ctx_copy
            )
        except Exception as e:
            self.logger.error(f"Audit logging failed: {e}")

        if self.secrets_manager.get_secret("SLACK_WEBHOOK_URL"):
            self._dispatcher.enqueue("slack", {
                "level": level,
                "message": safe_message,
                "app_name": ctx_copy.get("app_name"),
                "environment": ctx_copy.get("environment")
            })

        if self.secrets_manager.get_secret("ALERT_EMAIL_TO") and \
           self.secrets_manager.get_secret("ALERT_EMAIL_FROM") and \
           self.secrets_manager.get_secret("ALERT_SMTP_SERVER"):
            self._dispatcher.enqueue("email", {
                "level": level,
                "message": safe_message,
                "app_name": ctx_copy.get("app_name"),
                "environment": ctx_copy.get("environment")
            })

    def update_context(self, **kwargs: Any) -> None:
        """
        Update global context metadata for alerts.
        """
        with self._lock:
            self._context.update(kwargs)
            self.logger.debug(f"Updated alert context: {scrub(self._context)}")

    def reload(self) -> None:
        """Reload configuration from SecretsManager and reconfigure the operator."""
        with self._lock:
            self.secrets_manager.reload()
            self.audit_logger.reload()
            
            if self._dispatcher and self._dispatcher.is_alive():
                self._dispatcher.stop(drain=True)
            
            self._configure_logger()
            self._context = self._load_context()
            self._dispatcher = AlertDispatcher(self)
            self._dispatcher.start()
            
            self.logger.info("AlertOperator configuration reloaded")

# Global singleton factory
_alert_operator_singleton: Optional[AlertOperator] = None

def get_alert_operator() -> AlertOperator:
    """Returns the thread-safe singleton instance of AlertOperator."""
    global _alert_operator_singleton
    if _alert_operator_singleton is None:
        _alert_operator_singleton = AlertOperator()
    return _alert_operator_singleton

def send_alert(*args, **kwargs):
    """Stub: send an alert/notification."""
    return None