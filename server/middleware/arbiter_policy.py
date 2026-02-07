# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Arbiter Policy Middleware for FastAPI routes.

This middleware provides policy enforcement for API routes using the Arbiter's PolicyEngine.
It can be used as a dependency in FastAPI routes to check if operations are allowed.

[GAP #9 FIX] Adds policy checks to sensitive API routes while gracefully degrading
if Arbiter services are unavailable.
"""

import logging
import time
from typing import Optional, Tuple

from fastapi import Depends, HTTPException, Request, status
from prometheus_client import Counter, Histogram

logger = logging.getLogger(__name__)

# Prometheus metrics for policy middleware
try:
    POLICY_CHECK_TOTAL = Counter(
        'arbiter_policy_middleware_checks_total',
        'Total policy checks performed',
        ['route', 'method', 'result']
    )
    POLICY_CHECK_LATENCY = Histogram(
        'arbiter_policy_middleware_latency_seconds',
        'Latency of policy checks',
        ['route']
    )
    METRICS_AVAILABLE = True
except Exception:
    METRICS_AVAILABLE = False
    logger.debug("Prometheus metrics not available for policy middleware")


class ArbiterPolicyMiddleware:
    """
    FastAPI dependency for Arbiter policy checks.
    
    Usage:
        @app.post("/generate")
        async def generate_code(
            request: Request,
            policy: dict = Depends(arbiter_policy_check("generate"))
        ):
            # Route logic here
            pass
    
    The middleware will:
    1. Check if the action is allowed by PolicyEngine
    2. Log policy decisions
    3. Track metrics
    4. Gracefully degrade if Arbiter unavailable (allow by default)
    5. Raise HTTPException if policy explicitly denies
    """
    
    def __init__(self):
        """Initialize the policy middleware."""
        self.policy_engine = None
        self._init_policy_engine()
    
    def _init_policy_engine(self):
        """Try to initialize PolicyEngine with graceful fallback."""
        try:
            from self_fixing_engineer.arbiter.policy import PolicyEngine
            self.policy_engine = PolicyEngine()
            logger.info("ArbiterPolicyMiddleware: PolicyEngine initialized")
        except ImportError as e:
            logger.warning(
                f"ArbiterPolicyMiddleware: PolicyEngine not available ({e}). "
                "Policy checks will be bypassed."
            )
            self.policy_engine = None
        except Exception as e:
            logger.error(
                f"ArbiterPolicyMiddleware: Failed to initialize PolicyEngine: {e}"
            )
            self.policy_engine = None
    
    async def check_policy(
        self,
        action: str,
        request: Request,
        context: Optional[dict] = None
    ) -> Tuple[bool, str]:
        """
        Check if an action is allowed by policy.
        
        Args:
            action: The action to check (e.g., "generate", "deploy", "critique")
            request: FastAPI request object
            context: Additional context for policy decision
        
        Returns:
            Tuple of (allowed: bool, reason: str)
        """
        start_time = time.time()
        route = request.url.path
        method = request.method
        
        # Build context
        policy_context = {
            "route": route,
            "method": method,
            "client_host": request.client.host if request.client else "unknown",
            "user_agent": request.headers.get("user-agent", "unknown"),
        }
        if context:
            policy_context.update(context)
        
        # If PolicyEngine not available, allow by default (fail-open)
        if not self.policy_engine:
            logger.debug(
                f"PolicyEngine unavailable, allowing {action} on {route} (fail-open)"
            )
            if METRICS_AVAILABLE:
                POLICY_CHECK_TOTAL.labels(
                    route=route, method=method, result="allowed_no_engine"
                ).inc()
            return True, "Policy check bypassed (PolicyEngine unavailable)"
        
        # Check policy
        try:
            allowed, reason = await self.policy_engine.should_auto_learn(
                "API",
                action,
                route,
                policy_context
            )
            
            # Track metrics
            if METRICS_AVAILABLE:
                POLICY_CHECK_TOTAL.labels(
                    route=route,
                    method=method,
                    result="allowed" if allowed else "denied"
                ).inc()
                POLICY_CHECK_LATENCY.labels(route=route).observe(
                    time.time() - start_time
                )
            
            # Log decision
            if allowed:
                logger.info(
                    f"Policy ALLOWED: {action} on {route} - {reason}"
                )
            else:
                logger.warning(
                    f"Policy DENIED: {action} on {route} - {reason}"
                )
            
            return allowed, reason
            
        except Exception as e:
            logger.error(
                f"Policy check failed for {action} on {route}: {e}",
                exc_info=True
            )
            # Fail-open on error
            if METRICS_AVAILABLE:
                POLICY_CHECK_TOTAL.labels(
                    route=route, method=method, result="error_allowed"
                ).inc()
            return True, f"Policy check error (fail-open): {str(e)}"


# Global middleware instance
_policy_middleware = ArbiterPolicyMiddleware()


def arbiter_policy_check(action: str, context: Optional[dict] = None):
    """
    FastAPI dependency factory for policy checks.
    
    Args:
        action: The action to check (e.g., "generate", "deploy")
        context: Additional context for policy decision
    
    Returns:
        A FastAPI dependency function
    
    Usage:
        @app.post("/generate")
        async def generate_code(
            request: Request,
            policy: dict = Depends(arbiter_policy_check("generate"))
        ):
            # policy dict contains: {"allowed": bool, "reason": str}
            pass
    """
    async def dependency(request: Request) -> dict:
        """Dependency function that performs the policy check."""
        allowed, reason = await _policy_middleware.check_policy(
            action, request, context
        )
        
        if not allowed:
            # Raise HTTPException to block the request
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "Policy violation",
                    "action": action,
                    "reason": reason,
                    "route": request.url.path
                }
            )
        
        return {
            "allowed": allowed,
            "reason": reason,
            "action": action,
            "checked_at": time.time()
        }
    
    return dependency


def optional_arbiter_policy_check(action: str, context: Optional[dict] = None):
    """
    Optional policy check that doesn't block on denial.
    
    Returns policy decision info but doesn't raise HTTPException.
    Useful for logging/auditing without enforcing.
    
    Usage:
        @app.get("/stats")
        async def get_stats(
            request: Request,
            policy: dict = Depends(optional_arbiter_policy_check("view_stats"))
        ):
            # policy dict contains decision info, but request proceeds regardless
            pass
    """
    async def dependency(request: Request) -> dict:
        """Dependency function that performs non-blocking policy check."""
        allowed, reason = await _policy_middleware.check_policy(
            action, request, context
        )
        
        return {
            "allowed": allowed,
            "reason": reason,
            "action": action,
            "checked_at": time.time()
        }
    
    return dependency
