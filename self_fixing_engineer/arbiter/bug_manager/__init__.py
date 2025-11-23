"""Bug Manager module"""

# Import submodules first
from . import audit_log, notifications, remediations, utils
from .audit_log import AuditLogManager

# Import classes
from .bug_manager import BugManager
from .notifications import NotificationService

# Fixed: Removed non-existent exports and corrected class names
__all__ = [
    "BugManager",
    "AuditLogManager",  # Fixed: was 'AuditLog'
    "NotificationService",  # Fixed: removed 'NotificationManager' and 'RemediationEngine'
]
