"""
Routers package for API endpoints.
"""

from .api_keys import router as api_keys_router
from .diagnostics import router as diagnostics_router
from .events import router as events_router
from .fixes import router as fixes_router
from .generator import router as generator_router
from .jobs import router as jobs_router
from .omnicore import router as omnicore_router
from .sfe import router as sfe_router

__all__ = [
    "api_keys_router",
    "diagnostics_router",
    "events_router",
    "fixes_router",
    "generator_router",
    "jobs_router",
    "omnicore_router",
    "sfe_router",
]
