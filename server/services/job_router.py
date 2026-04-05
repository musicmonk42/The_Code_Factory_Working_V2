"""Decomposed job-routing logic extracted from ``OmniCoreService.route_job``.

Replaces the 235-line method with four focused functions eliminating
8 duplicated response dicts and 2 duplicated dispatch blocks.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, Optional

from server.services.service_context import ServiceContext

logger = logging.getLogger(__name__)
DispatchFn = Callable[..., Awaitable[Dict[str, Any]]]


def _make_route_result(
    job_id: str, source: str, target: str, transport: str, *,
    routed: bool = True, data: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None, extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a standardised routing result dict."""
    result: Dict[str, Any] = {
        "job_id": job_id, "routed": routed, "source": source,
        "target": target, "transport": transport,
    }
    if data is not None:
        result["data"] = data
    if error is not None:
        result["error"] = error
        result.setdefault("data", {"status": "error", "message": error})
    if extra:
        result.update(extra)
    return result


async def _dispatch_and_wrap(
    dispatch_fn: DispatchFn, job_id: str, action: Optional[str],
    payload: Dict[str, Any], source: str, target: str, transport: str, *,
    log_status: bool = False, label: str = "",
) -> Dict[str, Any]:
    """Call *dispatch_fn* and wrap the outcome in a route-result dict."""
    tag = label or target
    try:
        result = await dispatch_fn(job_id, action, payload)
        if log_status:
            status = result.get("status", "unknown")
            if status in ("completed", "success", "acknowledged"):
                logger.info(f"Task Completed: Job {job_id} {tag} action {action} finished successfully")
            elif status in ("failed", "error"):
                logger.error(f"Task Failed: Job {job_id} {tag} action {action} failed: {result.get('message', 'Unknown error')}")
            else:
                logger.warning(f"Task Status: Job {job_id} {tag} action {action} finished with status: {status}")
        return _make_route_result(job_id, source, target, transport, data=result)
    except Exception as exc:
        logger.error(f"Direct dispatch failed for {tag} job {job_id}: {exc}", exc_info=True)
        return _make_route_result(job_id, source, target, transport, routed=False, error=str(exc))


async def _route_via_message_bus(
    ctx: ServiceContext, job_id: str, source: str, target: str,
    payload: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Publish to message bus and audit-log the event.  Returns ``None`` on failure."""
    if not (ctx.message_bus and ctx.omnicore_components_available.get("message_bus")):
        return None
    try:
        topic = f"{target}.job_request"
        priority = payload.get("priority", 5)
        enriched = {
            **payload, "job_id": job_id, "source_module": source,
            "target_module": target, "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        success = await ctx.message_bus.publish(topic=topic, payload=enriched, priority=priority)
        if not success:
            logger.warning(f"Failed to publish job {job_id} to message bus")
            return None
        logger.info(f"Job {job_id} published to message bus topic: {topic}")
        # Best-effort audit log
        if ctx.audit_client and ctx.omnicore_components_available.get("audit"):
            try:
                await ctx.audit_client.add_entry_async(
                    kind="job_routed", name=f"job_{job_id}",
                    detail={"source": source, "target": target, "topic": topic, "priority": priority},
                    sim_id=None, agent_id=None, error=None, context=None,
                    custom_attributes=None,
                    rationale=f"Routing job {job_id} from {source} to {target}",
                    simulation_outcomes=None, tenant_id=None, explanation_id=None,
                )
            except Exception as audit_err:
                logger.warning(f"Audit logging failed: {audit_err}")
        return _make_route_result(
            job_id, source, target, "message_bus",
            extra={"topic": topic, "message_bus": "ShardedMessageBus"},
        )
    except Exception as exc:
        logger.error(f"Message bus routing error: {exc}", exc_info=True)
        return None


async def route_job(
    ctx: ServiceContext, job_id: str, source_module: str,
    target_module: str, payload: Dict[str, Any],
) -> Dict[str, Any]:
    """Route a job between modules, preserving original OmniCoreService behaviour."""
    action = payload.get("action", "unknown")
    logger.info(f"Intent Parsed: Job {job_id} received from {source_module} targeting {target_module}")
    logger.info(f"Job Received: {job_id} with action: {action}")
    logger.info(f"Routing job {job_id} from {source_module} to {target_module}")
    engine = ctx.omnicore_engine

    # Fast-path: generator always uses direct dispatch
    if target_module == "generator":
        logger.info(f"Using direct dispatch for generator job {job_id} action: {action}")
        return await _dispatch_and_wrap(
            engine._dispatch_generator_action, job_id, action, payload,
            source_module, target_module, "direct_dispatch")

    # Audit-log queries need a synchronous response (bus is fire-and-forget)
    if action == "query_audit_logs":
        logger.info(f"Using direct dispatch for audit query job {job_id} targeting {target_module}")
        return await _dispatch_and_wrap(
            engine._dispatch_sfe_action, job_id, "query_audit_logs", payload,
            source_module, target_module, "direct_dispatch")

    # Try message bus first
    bus_result = await _route_via_message_bus(ctx, job_id, source_module, target_module, payload)
    if bus_result is not None:
        return bus_result

    # Fallback: direct dispatch
    logger.info(f"Using direct dispatch for job {job_id} (message bus not available)")
    dispatch_map: Dict[str, tuple[DispatchFn, str]] = {
        "generator": (engine._dispatch_generator_action, "generator"),
        "sfe": (engine._dispatch_sfe_action, "SFE"),
    }
    if target_module in dispatch_map:
        fn, label = dispatch_map[target_module]
        logger.info(f"Task Dispatched: Job {job_id} dispatching {label} action: {action}")
        return await _dispatch_and_wrap(
            fn, job_id, action, payload, source_module, target_module,
            "direct_dispatch_fallback", log_status=True, label=label)

    return _make_route_result(
        job_id, source_module, target_module, "direct_dispatch_fallback",
        extra={"note": "Message bus not available, job queued for direct processing"})


def get_job_router():
    """Return this module as a singleton service handle."""
    return sys.modules[__name__]
