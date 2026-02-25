# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Stub implementations for test generation orchestrator.

These stub classes are designed for offline/demo mode and testing.
They should NOT be used in production environments.

**PRODUCTION USE IS FORBIDDEN**: Using these stubs in production bypasses real
security scanning, policy enforcement, and event publishing. Any attempt to use
these stubs in a detected production environment will raise a RuntimeError.

Environment Variables:
    TEST_GENERATION_OFFLINE_MODE: Set to "true" to explicitly enable stub mode
    APP_ENV: Primary environment selector ("production", "staging", "development", "test")
    PRODUCTION_MODE: Legacy flag — "true" or "1" also triggers production mode
    FORCE_PRODUCTION_MODE: Override — "true" forces production mode regardless
"""

import os
from typing import Any, List, Optional, Tuple
from test_generation.orchestrator.console import log

# Check if we're running in a test or offline environment
_OFFLINE_MODE = (
    os.environ.get("TEST_GENERATION_OFFLINE_MODE", "false").lower() == "true"
)


def _is_production() -> bool:
    """
    Detect production mode using the same priority order as server/environment.py.

    Priority order (highest to lowest):
        1. FORCE_PRODUCTION_MODE=true
        2. APP_ENV (authoritative when set; any non-production value prevents legacy fallback)
        3. PRODUCTION_MODE=true or PRODUCTION_MODE=1
        4. Legacy ENVIRONMENT=production (only consulted when APP_ENV is absent)

    When ``APP_ENV`` is set, its value is treated as authoritative so that
    ``APP_ENV=development`` cannot be overridden by the legacy ``ENVIRONMENT``
    variable.

    Returns:
        True if the application is running in production mode.
    """
    if os.environ.get("FORCE_PRODUCTION_MODE", "").lower() == "true":
        return True
    app_env = os.environ.get("APP_ENV", "").lower()
    if app_env:
        # APP_ENV is authoritative: only production/prod means production.
        # Any other value (development, staging, test) is explicitly non-production
        # and prevents the legacy ENVIRONMENT fallback from overriding.
        return app_env in ("production", "prod")
    prod_mode = os.environ.get("PRODUCTION_MODE", "")
    if prod_mode.lower() == "true" or prod_mode == "1":
        return True
    # Legacy variable kept for backward compat (lower priority than APP_ENV)
    if os.environ.get("ENVIRONMENT", "").lower() == "production":
        return True
    return False


_PRODUCTION = _is_production()


def _check_stub_usage_safety():
    """
    Check if stub usage is safe and log appropriate warnings.

    Production usage is explicitly forbidden because these stubs return
    false-negative results (e.g., "no vulnerabilities", "approved") which
    would silently hide real security issues in production.

    Raises:
        RuntimeError: If stubs are being used in a production environment
                      without TEST_GENERATION_OFFLINE_MODE=true.
    """
    if _PRODUCTION and not _OFFLINE_MODE:
        error_msg = (
            "Stub classes are being used in PRODUCTION environment! "
            "This is NOT safe for production use. "
            "Set TEST_GENERATION_OFFLINE_MODE=true to acknowledge offline mode, "
            "or configure real implementations."
        )
        log(error_msg, level="ERROR")
        raise RuntimeError(error_msg)

    if not _OFFLINE_MODE:
        log(
            "Using stub implementations in non-offline mode. "
            "Set TEST_GENERATION_OFFLINE_MODE=true to suppress this warning.",
            level="WARNING",
        )


# Check on module import
_check_stub_usage_safety()


class DummyPolicyEngine:
    """
    A stub for the PolicyEngine for offline/demo mode.

    **WARNING — PRODUCTION USE FORBIDDEN**: In production this stub raises
    ``RuntimeError`` from every policy method to prevent silent auto-approval
    of all test integrations.  In non-production/dev/test environments the
    stub auto-approves (allow-by-default) so that CI pipelines are not blocked.

    Activation:
        - Automatically used when PolicyEngine is not available
        - Explicitly enabled by setting TEST_GENERATION_OFFLINE_MODE=true
        - Raises RuntimeError in production to prevent false policy passes
    """

    def __init__(self):
        """Initialize the dummy policy engine with usage logging."""
        if _PRODUCTION:
            log(
                "CRITICAL: DummyPolicyEngine initialized in PRODUCTION MODE! "
                "This is a SECURITY RISK — all policy methods will raise RuntimeError. "
                "Configure a real PolicyEngine immediately.",
                level="ERROR",
            )
        else:
            log(
                "DummyPolicyEngine initialized. All operations will be allowed (STUB MODE).",
                level="WARNING",
            )

        self.usage_count = 0

    async def should_integrate_test(self, *args: Any, **kwargs: Any) -> Tuple[bool, str]:
        """
        Stub implementation — deny-by-default in production, allow in dev/test.

        Raises:
            RuntimeError: Always raised in production to prevent silent approvals.

        Returns:
            tuple: (True, "Stubbed") in non-production environments only.
        """
        self.usage_count += 1

        if _PRODUCTION:
            msg = (
                f"DummyPolicyEngine.should_integrate_test called in PRODUCTION "
                f"(call #{self.usage_count}). Raising RuntimeError to prevent "
                "silent auto-approval of test integrations."
            )
            log(msg, level="CRITICAL")
            raise RuntimeError(msg)

        log(
            "Using DummyPolicyEngine. All tests are allowed to be integrated.",
            level="DEBUG",
        )
        return True, "Stubbed"

    async def requires_pr_for_integration(self, *args: Any, **kwargs: Any) -> Tuple[bool, str]:
        """
        Stub implementation — requires PR in production, skips in dev/test.

        In production a PR review is always required; in non-production the stub
        returns ``(False, ...)`` so that offline CI flows are not blocked.

        Raises:
            RuntimeError: Always raised in production.

        Returns:
            tuple: (False, "Stubbed policy requires no PR") in non-production only.
        """
        self.usage_count += 1

        if _PRODUCTION:
            msg = (
                "DummyPolicyEngine.requires_pr_for_integration called in PRODUCTION. "
                "Raising RuntimeError — a real PolicyEngine must be configured."
            )
            log(msg, level="CRITICAL")
            raise RuntimeError(msg)

        log("Using DummyPolicyEngine. No PRs are required.", level="DEBUG")
        return False, "Stubbed policy requires no PR"

    @property
    def policy_hash(self):
        """Return stub policy hash."""
        return "stub-hash"


class DummyEventBus:
    """
    A simple stub for the event bus that does nothing.

    WARNING: This stub discards all events and should NOT be used in production.

    Activation: Same as DummyPolicyEngine
    """

    def __init__(self):
        """Initialize the dummy event bus."""
        log(
            "DummyEventBus initialized. All events will be discarded (STUB MODE).",
            level="WARNING",
        )
        self.published_events = []

    async def publish(self, *args: Any, **kwargs: Any) -> None:
        """
        Stub implementation that logs but doesn't publish events.

        Args:
            *args: Event arguments (ignored)
            **kwargs: Event keyword arguments (ignored)
        """
        event_info = {"args": args, "kwargs": kwargs}
        self.published_events.append(event_info)

        log(f"Using DummyEventBus. Event published: {args}", level="DEBUG")

        if _PRODUCTION:
            log("CRITICAL: DummyEventBus.publish called in PRODUCTION!", level="CRITICAL")


class DummySecurityScanner:
    """
    A stub for the security scanner.

    **WARNING — PRODUCTION USE FORBIDDEN**: This stub always returns
    ``(False, [], "NONE")`` (i.e., "no vulnerabilities found"), which is a
    false-negative result that would silently hide real security issues.
    In production this method raises ``RuntimeError`` instead of returning
    the misleading safe-looking tuple.

    Activation: Same as DummyPolicyEngine
    """

    def __init__(self):
        """Initialize the dummy security scanner."""
        log(
            "DummySecurityScanner initialized. No security scans will be performed (STUB MODE).",
            level="WARNING",
        )

    async def scan_test_file(self, *args: Any, **kwargs: Any) -> Tuple[bool, List[Any], str]:
        """
        Stub implementation — raises RuntimeError in production, otherwise
        returns a sentinel 'no issues' tuple.

        Production use is forbidden because returning ``(False, [], "NONE")``
        in production would silently mask real vulnerabilities and give a false
        sense of security.

        Raises:
            RuntimeError: Always raised in production to prevent false-negative
                          security results from reaching production pipelines.

        Returns:
            tuple: (False, [], "NONE") in non-production environments only.
        """
        if _PRODUCTION:
            msg = (
                "DummySecurityScanner.scan_test_file called in PRODUCTION. "
                "Raising RuntimeError to prevent false-negative security results. "
                "Configure a real SecurityScanner implementation."
            )
            log(msg, level="CRITICAL")
            raise RuntimeError(msg)

        log("Using DummySecurityScanner. No security issues found.", level="DEBUG")
        return False, [], "NONE"


class DummyKnowledgeGraphClient:
    """
    A stub for the Knowledge Graph client.

    WARNING: This stub discards all metrics and should NOT be used in production.

    Activation: Same as DummyPolicyEngine
    """

    def __init__(self):
        """Initialize the dummy knowledge graph client."""
        log(
            "DummyKnowledgeGraphClient initialized. Metrics will be discarded (STUB MODE).",
            level="WARNING",
        )
        self.metrics = []

    async def update_module_metrics(self, *args: Any, **kwargs: Any) -> None:
        """
        Stub implementation that logs but doesn't update metrics.

        Args:
            *args: Metric arguments (ignored)
            **kwargs: Metric keyword arguments (ignored)
        """
        self.metrics.append({"args": args, "kwargs": kwargs})

        log(
            f"Using DummyKnowledgeGraphClient. Metrics updated: {kwargs}", level="DEBUG"
        )

        if _PRODUCTION:
            log(
                "CRITICAL: DummyKnowledgeGraphClient used in PRODUCTION!", level="CRITICAL"
            )


class DummyPRCreator:
    """
    A stub for the PR creator that simulates success.

    WARNING: This stub doesn't create real PRs and should NOT be used in production.

    Activation: Same as DummyPolicyEngine
    """

    def __init__(self):
        """Initialize the dummy PR creator."""
        log(
            "DummyPRCreator initialized. PR/ticket creation will be simulated (STUB MODE).",
            level="WARNING",
        )
        self.created_prs = []
        self.created_tickets = []

    async def create_pr(self, *args: Any, **kwargs: Any) -> Tuple[bool, str]:
        """
        Stub implementation that returns failure — no real PR will be created.

        Returns:
            tuple: (False, "") indicating failure
        """
        log("Using DummyPRCreator. PR creation unavailable (stub mode).", level="DEBUG")

        if _PRODUCTION:
            log(
                "CRITICAL: DummyPRCreator.create_pr called in PRODUCTION! "
                "No actual PR will be created!",
                level="CRITICAL",
            )

        return False, ""

    async def create_jira_ticket(self, *args: Any, **kwargs: Any) -> Tuple[bool, str]:
        """
        Stub implementation that simulates Jira ticket creation.

        Returns:
            tuple: (True, "https://jira.com/stub-ticket")
        """
        ticket_info = {
            "args": args,
            "kwargs": kwargs,
            "url": "https://jira.com/stub-ticket",
        }
        self.created_tickets.append(ticket_info)

        log("Using DummyPRCreator. Simulating Jira ticket creation.", level="DEBUG")

        if _PRODUCTION:
            log(
                "CRITICAL: DummyPRCreator.create_jira_ticket called in PRODUCTION! "
                "No actual ticket will be created!",
                level="CRITICAL",
            )

        return True, "https://jira.com/stub-ticket"


class DummyMutationTester:
    """
    A stub for the mutation tester.

    WARNING: This stub always reports 100% mutation score and should NOT be used in production.

    Activation: Same as DummyPolicyEngine
    """

    def __init__(self):
        """Initialize the dummy mutation tester."""
        log(
            "DummyMutationTester initialized. Mutation testing will be simulated (STUB MODE).",
            level="WARNING",
        )

    async def run_mutations(
        self, *args: Any, **kwargs: Any
    ) -> Tuple[Optional[float], Optional[float], str]:
        """
        Stub implementation that returns a ``None`` sentinel score.

        Callers must treat ``(None, None, …)`` as "skipped" rather than
        "failed" so that CI quality gates are not triggered by a missing
        mutation-testing tool.

        Returns:
            tuple: (None, None, message) — callers should skip mutation gate
        """
        log("Using DummyMutationTester. Mutation testing unavailable (stub mode).", level="DEBUG")

        if _PRODUCTION:
            log(
                "CRITICAL: DummyMutationTester.run_mutations called in PRODUCTION! "
                "Mutation testing results are not real!",
                level="CRITICAL",
            )

        return None, None, "Mutation testing unavailable — install mutmut or cosmic-ray"


class DummyTestEnricher:
    """
    A pass-through stub for the test enricher.

    WARNING: This stub doesn't enrich tests and should NOT be used in production.

    Activation: Same as DummyPolicyEngine
    """

    def __init__(self):
        """Initialize the dummy test enricher."""
        log(
            "DummyTestEnricher initialized. No test enrichment will be performed (STUB MODE).",
            level="WARNING",
        )

    async def enrich_test(self, content: str, *args: Any, **kwargs: Any) -> str:
        """
        Stub implementation that returns content unchanged.

        Args:
            content: Test content to enrich
            *args: Additional arguments (ignored)
            **kwargs: Additional keyword arguments (ignored)

        Returns:
            str: Original content unchanged
        """
        log("Using DummyTestEnricher. No enrichment applied.", level="DEBUG")

        if _PRODUCTION:
            log(
                "CRITICAL: DummyTestEnricher.enrich_test called in PRODUCTION! "
                "Tests will not be enriched!",
                level="CRITICAL",
            )

        return content


__all__ = [
    "DummyPolicyEngine",
    "DummyEventBus",
    "DummySecurityScanner",
    "DummyKnowledgeGraphClient",
    "DummyPRCreator",
    "DummyMutationTester",
    "DummyTestEnricher",
]
