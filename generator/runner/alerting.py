# runner/alerting.py
"""
Alerting module for the runner package.
Re-exports alerting functions from runner_logging for convenience.
"""

from .runner_logging import send_alert

__all__ = ["send_alert"]
