"""System diagnostics and health-check operations.

Extracted from ``OmniCoreService`` during Phase 2 decomposition.  Covers
LLM status, system status, agent availability checks, system health, and
per-job metrics retrieval.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from server.services.service_context import ServiceContext

logger = logging.getLogger(__name__)


class DiagnosticsService:
    """Read-only diagnostics and health reporting."""

    def __init__(self, ctx: ServiceContext) -> None:
        self._ctx = ctx

    # -- Agent availability --------------------------------------------------

    def check_agent_available(self, agent_name: str) -> Tuple[bool, Optional[str]]:
        """Check whether an agent is available and return an error message if not."""
        if not self._ctx.agents_available.get(agent_name, False):
            error_msg = (
                f"{agent_name.capitalize()} agent is not available. "
                "Check that dependencies are installed"
            )
            if not self._ctx.llm_config or not (
                hasattr(self._ctx.llm_config, "get_available_providers")
                and self._ctx.llm_config.get_available_providers()
            ):
                error_msg += " and LLM provider is configured (set API keys in .env)"
            return False, error_msg
        return True, None

    # -- LLM status ----------------------------------------------------------

    def get_llm_status(self) -> Dict[str, Any]:
        """Get the current LLM provider status."""
        status = self._ctx.llm_status
        return {
            "provider": status.get("provider", "unknown"),
            "configured": status.get("configured", False),
            "validated": status.get("validated", False),
            "error": status.get("error"),
            "available_providers": (
                self._ctx.llm_config.get_available_providers()
                if self._ctx.llm_config
                and hasattr(self._ctx.llm_config, "get_available_providers")
                else []
            ),
        }

    # -- System status -------------------------------------------------------

    def get_system_status(self) -> Dict[str, Any]:
        """Get comprehensive system status including agents and LLM."""
        return {
            "state": "ready_idle",
            "message": "System is ready and waiting for job requests",
            "llm_status": self.get_llm_status(),
            "agents": {
                "available": [k for k, v in self._ctx.agents_available.items() if v],
                "unavailable": [k for k, v in self._ctx.agents_available.items() if not v],
            },
            "components": {
                "available": [k for k, v in self._ctx.omnicore_components_available.items() if v],
                "unavailable": [k for k, v in self._ctx.omnicore_components_available.items() if not v],
            },
            "instructions": {
                "to_generate_code": "POST /api/jobs/ with requirements",
                "to_upload_readme": "POST /api/generator/upload",
                "to_check_status": "GET /api/jobs/{job_id}/progress",
            },
        }

    # -- System health -------------------------------------------------------

    async def get_system_health(self) -> Dict[str, Any]:
        """Get overall system health from OmniCore perspective."""
        logger.debug("Fetching system health")

        health_status: Dict[str, Any] = {
            "status": "healthy",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "components": {},
        }

        # Message bus
        bus = self._ctx.message_bus
        if bus and self._ctx.omnicore_components_available.get("message_bus"):
            try:
                queue_sizes = [q.qsize() for q in bus.queues]
                health_status["components"]["message_bus"] = {
                    "status": "operational",
                    "shards": len(bus.queues),
                    "total_queued": sum(queue_sizes),
                }
            except Exception as e:
                health_status["components"]["message_bus"] = {"status": "degraded", "error": str(e)}
                health_status["status"] = "degraded"
        else:
            health_status["components"]["message_bus"] = {"status": "unavailable"}

        # Plugin registry
        registry = self._ctx.plugin_registry
        if registry and self._ctx.omnicore_components_available.get("plugin_registry"):
            try:
                plugin_count = sum(len(p) for p in registry._plugins.values())
                health_status["components"]["plugin_registry"] = {
                    "status": "operational",
                    "total_plugins": plugin_count,
                }
            except Exception as e:
                health_status["components"]["plugin_registry"] = {"status": "degraded", "error": str(e)}
                health_status["status"] = "degraded"
        else:
            health_status["components"]["plugin_registry"] = {"status": "unavailable"}

        # Metrics
        if self._ctx.metrics_client and self._ctx.omnicore_components_available.get("metrics"):
            health_status["components"]["metrics"] = {"status": "operational"}
        else:
            health_status["components"]["metrics"] = {"status": "unavailable"}

        # Audit
        audit = self._ctx.audit_client
        if audit and self._ctx.omnicore_components_available.get("audit"):
            try:
                buffer_size = len(audit.buffer) if hasattr(audit, "buffer") else 0
                health_status["components"]["audit"] = {"status": "operational", "buffer_size": buffer_size}
            except Exception as e:
                health_status["components"]["audit"] = {"status": "degraded", "error": str(e)}
                health_status["status"] = "degraded"
        else:
            health_status["components"]["audit"] = {"status": "unavailable"}

        # Overall determination
        statuses = [c["status"] for c in health_status["components"].values()]
        if all(s == "operational" for s in statuses):
            health_status["status"] = "healthy"
        elif any(s == "operational" for s in statuses):
            health_status["status"] = "degraded"
        else:
            health_status["status"] = "critical"

        return health_status

    # -- Job metrics ---------------------------------------------------------

    async def get_job_metrics(self, job_id: str) -> Dict[str, Any]:
        """Get metrics for a specific job."""
        logger.debug(f"Fetching metrics for job {job_id}")

        metrics = self._ctx.metrics_client
        if metrics and self._ctx.omnicore_components_available.get("metrics"):
            try:
                metrics_data: Dict[str, Any] = {"job_id": job_id, "source": "actual"}
                try:
                    if hasattr(metrics, "MESSAGE_BUS_DISPATCH_DURATION"):
                        dm = metrics.MESSAGE_BUS_DISPATCH_DURATION
                        if hasattr(dm, "_samples"):
                            metrics_data["dispatch_latency_samples"] = len(dm._samples())
                except Exception:
                    pass
                try:
                    if hasattr(metrics, "API_REQUESTS_TOTAL"):
                        rm = metrics.API_REQUESTS_TOTAL
                        if hasattr(rm, "_value"):
                            metrics_data["api_requests"] = rm._value.get()
                except Exception:
                    pass
                logger.info(f"Retrieved actual metrics for job {job_id}")
                return metrics_data
            except Exception as e:
                logger.error(f"Error querying metrics: {e}", exc_info=True)

        logger.debug(f"Using fallback metrics for job {job_id}")
        return {
            "job_id": job_id,
            "processing_time": 125.5,
            "cpu_usage": 45.2,
            "memory_usage": 512.3,
            "metrics_module": "omnicore_engine.metrics",
            "source": "fallback",
        }


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------

_diagnostics_service_instance: Optional[DiagnosticsService] = None


def get_diagnostics_service(ctx: Optional[ServiceContext] = None) -> DiagnosticsService:
    """Return the singleton ``DiagnosticsService``."""
    global _diagnostics_service_instance
    if _diagnostics_service_instance is None:
        if ctx is None:
            raise RuntimeError("DiagnosticsService not initialised -- pass a ServiceContext on first call")
        _diagnostics_service_instance = DiagnosticsService(ctx)
    return _diagnostics_service_instance
