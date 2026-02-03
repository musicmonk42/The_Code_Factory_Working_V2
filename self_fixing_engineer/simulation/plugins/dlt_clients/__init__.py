# simulation/plugins/dlt_clients/__init__.py

import logging
import sys
from logging.handlers import QueueHandler, QueueListener
from queue import Queue

try:
    from pythonjsonlogger import jsonlogger
except ImportError:
    _base_logger = logging.getLogger("dlt_clients")
    _base_logger.warning(
        "python-json-logger not found. JSON logging will be disabled. Install with `pip install python-json-logger`."
    )
    jsonlogger = None

# Add custom audit log level
AUDIT = 25
logging.addLevelName(AUDIT, "AUDIT")


class DLTClientLoggerAdapter(logging.LoggerAdapter):
    """
    A LoggerAdapter that injects client_type and correlation_id into log records.
    """

    def process(self, msg, kwargs):
        extra = kwargs.get("extra", {})
        extra["client_type"] = self.extra.get("client_type", "N/A")
        extra["correlation_id"] = self.extra.get("correlation_id", "N/A")
        kwargs["extra"] = extra
        return msg, kwargs

    def audit(self, msg, *args, **kwargs):
        """Custom audit log level for compliance-grade events."""
        if self.logger.isEnabledFor(AUDIT):
            self.logger._log(AUDIT, msg, args, **kwargs)


# Configure base logger with thread-safe QueueHandler
_base_logger = logging.getLogger("dlt_clients")
_base_logger.setLevel(logging.DEBUG)

# Check if we're in test mode to avoid starting threads during collection
# Note: pytest presence always enables test mode for safety during test collection
import os
_IN_TEST_MODE = os.environ.get("TEST_MODE", "").lower() == "true" or "pytest" in sys.modules

if not _base_logger.handlers:
    if _IN_TEST_MODE:
        # In test mode, use a simple handler to avoid thread issues during test collection
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter(
            "%(asctime)s - [%(levelname)s] - %(name)s - client:%(client_type)s - cid:%(correlation_id)s - %(message)s"
        )
        handler.setFormatter(formatter)
        _base_logger.addHandler(handler)
    else:
        # In production, use thread-safe QueueHandler
        log_queue = Queue(-1)
        queue_handler = QueueHandler(log_queue)
        handler = logging.StreamHandler(sys.stdout)
        formatter = (
            jsonlogger.JsonFormatter(
                "%(asctime)s %(levelname)s %(name)s %(client_type)s %(correlation_id)s %(message)s"
            )
            if jsonlogger
            else logging.Formatter(
                "%(asctime)s - [%(levelname)s] - %(name)s - client:%(client_type)s - cid:%(correlation_id)s - %(message)s"
            )
        )
        handler.setFormatter(formatter)
        try:
            listener = QueueListener(log_queue, handler)
            listener.start()
            _base_logger.addHandler(queue_handler)
        except RuntimeError:
            # Fallback: Thread creation failed (e.g., resource constraints, certain test environments)
            # Using direct handler attachment - note this is not thread-safe for concurrent logging
            _base_logger.warning("QueueListener thread creation failed, using direct handler (not thread-safe)")
            _base_logger.addHandler(handler)
