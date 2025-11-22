"""Bug Manager module"""

# Import submodules first
from . import notifications
from . import remediations
from . import utils
from . import audit_log

# Import classes
from .bug_manager import BugManager
from .audit_log import AuditLogManager
from .notifications import NotificationService

# Fixed: Removed non-existent exports and corrected class names
__all__ = [
    "BugManager",
    "AuditLogManager",  # Fixed: was 'AuditLog'
    "NotificationService",  # Fixed: removed 'NotificationManager' and 'RemediationEngine'
]
