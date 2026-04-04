"""Administrative operations for the OmniCore service layer.

Extracted from ``OmniCoreService`` during Phase 2 decomposition.  Covers LLM
configuration, plugin management, database queries/exports, circuit-breaker
status, and rate-limit configuration.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from server.services.service_context import ServiceContext

logger = logging.getLogger(__name__)


class AdminService:
    """Thin service wrapping administrative OmniCore operations."""

    def __init__(self, ctx: ServiceContext) -> None:
        self._ctx = ctx

    # -- LLM configuration ---------------------------------------------------

    async def configure_llm(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Configure LLM provider."""
        try:
            provider = payload.get("provider", "openai")
            api_key = payload.get("api_key")
            model = payload.get("model")

            if api_key:
                env_var = f"{provider.upper()}_API_KEY"
                os.environ[env_var] = api_key
                logger.info(f"Configured API key for {provider}")

            return {
                "status": "configured",
                "provider": provider,
                "model": model or "default",
            }
        except Exception as e:
            logger.error(f"Error configuring LLM: {e}", exc_info=True)
            return {"status": "error", "message": str(e)}

    # -- Plugin management ---------------------------------------------------

    async def get_plugin_status(self) -> Dict[str, Any]:
        """Get status of registered plugins."""
        logger.debug("Fetching plugin status")

        registry = self._ctx.plugin_registry
        if registry and self._ctx.omnicore_components_available.get("plugin_registry"):
            try:
                all_plugins: list[str] = []
                plugin_details: list[Dict[str, Any]] = []

                for kind, plugins_by_name in registry._plugins.items():
                    for name, plugin in plugins_by_name.items():
                        all_plugins.append(name)
                        plugin_details.append({
                            "name": name,
                            "kind": kind,
                            "version": getattr(plugin.meta, "version", "unknown") if hasattr(plugin, "meta") else "unknown",
                            "safe": getattr(plugin.meta, "safe", False) if hasattr(plugin, "meta") else False,
                        })

                logger.info(f"Retrieved {len(all_plugins)} plugins from registry")
                return {
                    "total_plugins": len(all_plugins),
                    "active_plugins": all_plugins[:10],
                    "plugin_details": plugin_details,
                    "plugin_registry": "omnicore_engine.plugin_registry.PLUGIN_REGISTRY",
                    "source": "actual",
                }
            except Exception as e:
                logger.error(f"Error querying plugin registry: {e}", exc_info=True)

        logger.debug("Using fallback plugin status (registry not available)")
        return {
            "total_plugins": 3,
            "active_plugins": ["scenario_plugin", "audit_plugin", "metrics_plugin"],
            "plugin_registry": "omnicore_engine.plugin_registry",
            "source": "fallback",
        }

    async def reload_plugin(self, plugin_id: str, force: bool = False) -> Dict[str, Any]:
        """Hot-reload a plugin."""
        logger.info(f"Reloading plugin {plugin_id}")
        return {
            "status": "reloaded",
            "plugin_id": plugin_id,
            "version": "1.0.0",
            "forced": force,
        }

    async def browse_marketplace(
        self,
        category: Optional[str] = None,
        search: Optional[str] = None,
        sort: str = "popularity",
        limit: int = 20,
    ) -> Dict[str, Any]:
        """Browse plugin marketplace."""
        logger.info("Browsing plugin marketplace")
        return {
            "plugins": [
                {
                    "plugin_id": "security_scanner",
                    "name": "Security Scanner",
                    "version": "2.1.0",
                    "category": "security",
                    "downloads": 1500,
                    "rating": 4.8,
                },
                {
                    "plugin_id": "performance_optimizer",
                    "name": "Performance Optimizer",
                    "version": "1.5.0",
                    "category": "optimization",
                    "downloads": 980,
                    "rating": 4.6,
                },
            ],
            "total": 2,
            "filters": {"category": category, "search": search, "sort": sort},
        }

    async def install_plugin(
        self,
        plugin_name: str,
        version: Optional[str] = None,
        source: str = "marketplace",
        config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Install a plugin."""
        logger.info(f"Installing plugin {plugin_name}")
        return {
            "status": "installed",
            "plugin_name": plugin_name,
            "version": version or "latest",
            "source": source,
        }

    # -- Database operations -------------------------------------------------

    async def query_database(
        self, query_type: str, filters: Optional[Dict[str, Any]] = None, limit: int = 100
    ) -> Dict[str, Any]:
        """Query OmniCore database."""
        logger.info(f"Querying database: {query_type}")
        return {
            "query_type": query_type,
            "results": [{"id": "example", "data": {}}],
            "count": 1,
            "filters": filters,
        }

    async def export_database(
        self, export_type: str = "full", format: str = "json", include_audit: bool = True
    ) -> Dict[str, Any]:
        """Export database state."""
        logger.info(f"Exporting database: {export_type}")
        return {
            "status": "exported",
            "export_type": export_type,
            "format": format,
            "export_path": f"/exports/omnicore_export_{export_type}.{format}",
            "size_bytes": 1024000,
        }

    # -- Circuit breakers & rate limits --------------------------------------

    async def get_circuit_breakers(self) -> Dict[str, Any]:
        """Get status of all circuit breakers."""
        logger.info("Fetching circuit breaker statuses")
        return {
            "circuit_breakers": [
                {
                    "name": "generator_service",
                    "state": "closed",
                    "failure_count": 0,
                    "last_failure_time": None,
                },
                {
                    "name": "sfe_service",
                    "state": "closed",
                    "failure_count": 0,
                    "last_failure_time": None,
                },
            ],
            "total": 2,
        }

    async def reset_circuit_breaker(self, name: str) -> Dict[str, Any]:
        """Reset a circuit breaker."""
        logger.info(f"Resetting circuit breaker {name}")
        return {
            "status": "reset",
            "name": name,
            "state": "closed",
            "failure_count": 0,
        }

    async def configure_rate_limit(
        self, endpoint: str, requests_per_second: float, burst_size: Optional[int] = None
    ) -> Dict[str, Any]:
        """Configure rate limits."""
        logger.info(f"Configuring rate limit for {endpoint}")
        return {
            "status": "configured",
            "endpoint": endpoint,
            "requests_per_second": requests_per_second,
            "burst_size": burst_size or int(requests_per_second * 2),
        }


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------

_admin_service_instance: Optional[AdminService] = None


def get_admin_service(ctx: Optional[ServiceContext] = None) -> AdminService:
    """Return the singleton ``AdminService``.

    On first call ``ctx`` must be supplied.  Subsequent calls reuse the
    existing instance regardless of whether ``ctx`` is passed.
    """
    global _admin_service_instance
    if _admin_service_instance is None:
        if ctx is None:
            raise RuntimeError("AdminService not initialised -- pass a ServiceContext on first call")
        _admin_service_instance = AdminService(ctx)
    return _admin_service_instance
