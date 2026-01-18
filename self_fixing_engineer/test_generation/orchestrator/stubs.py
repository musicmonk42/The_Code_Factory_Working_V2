"""
Stub implementations for test generation orchestrator.

These stub classes are designed for offline/demo mode and testing.
They should NOT be used in production environments.

Environment Variables:
    TEST_GENERATION_OFFLINE_MODE: Set to "true" to explicitly enable stub mode
    ENVIRONMENT: If set to "production", stubs will log warnings
"""

import logging
import os
from test_generation.orchestrator.console import log

# Check if we're running in a test or offline environment
_OFFLINE_MODE = (
    os.environ.get("TEST_GENERATION_OFFLINE_MODE", "false").lower() == "true"
)
_ENVIRONMENT = os.environ.get("ENVIRONMENT", "development").lower()


def _check_stub_usage_safety():
    """
    Check if stub usage is safe and log appropriate warnings.

    Raises:
        RuntimeError: If stubs are being used in a production environment
    """
    if _ENVIRONMENT == "production" and not _OFFLINE_MODE:
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

    WARNING: This stub always allows all operations and should NOT be used in production.

    Activation:
        - Automatically used when PolicyEngine is not available
        - Explicitly enabled by setting TEST_GENERATION_OFFLINE_MODE=true
        - Logs warnings when used in non-test environments
    """

    def __init__(self):
        """Initialize the dummy policy engine with usage logging."""
        log(
            "DummyPolicyEngine initialized. All operations will be allowed (STUB MODE).",
            level="WARNING" if _ENVIRONMENT != "test" else "DEBUG",
        )
        self.usage_count = 0

    async def should_integrate_test(self, *args, **kwargs):
        """
        Stub implementation that always allows test integration.

        Returns:
            tuple: (True, "Stubbed") indicating test can be integrated
        """
        self.usage_count += 1

        if _ENVIRONMENT == "production":
            log(
                "CRITICAL: DummyPolicyEngine.should_integrate_test called in PRODUCTION! "
                "This should never happen.",
                level="ERROR",
            )
        else:
            log(
                "Using DummyPolicyEngine. All tests are allowed to be integrated.",
                level="DEBUG",
            )

        return True, "Stubbed"

    async def requires_pr_for_integration(self, *args, **kwargs):
        """
        Stub implementation that never requires PRs.

        Returns:
            tuple: (False, "Stubbed policy requires no PR")
        """
        self.usage_count += 1
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
            level="WARNING" if _ENVIRONMENT != "test" else "DEBUG",
        )
        self.published_events = []

    async def publish(self, *args, **kwargs):
        """
        Stub implementation that logs but doesn't publish events.

        Args:
            *args: Event arguments (ignored)
            **kwargs: Event keyword arguments (ignored)
        """
        event_info = {"args": args, "kwargs": kwargs}
        self.published_events.append(event_info)

        log(f"Using DummyEventBus. Event published: {args}", level="DEBUG")

        if _ENVIRONMENT == "production":
            log("CRITICAL: DummyEventBus.publish called in PRODUCTION!", level="ERROR")


class DummySecurityScanner:
    """
    A stub for the security scanner.

    WARNING: This stub never finds security issues and should NOT be used in production.

    Activation: Same as DummyPolicyEngine
    """

    def __init__(self):
        """Initialize the dummy security scanner."""
        log(
            "DummySecurityScanner initialized. No security scans will be performed (STUB MODE).",
            level="WARNING" if _ENVIRONMENT != "test" else "DEBUG",
        )

    async def scan_test_file(self, *args, **kwargs):
        """
        Stub implementation that always reports no security issues.

        Returns:
            tuple: (False, [], "NONE") indicating no issues found
        """
        log("Using DummySecurityScanner. No security issues found.", level="DEBUG")

        if _ENVIRONMENT == "production":
            log(
                "CRITICAL: DummySecurityScanner.scan_test_file called in PRODUCTION! "
                "Security vulnerabilities may not be detected!",
                level="ERROR",
            )

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
            level="WARNING" if _ENVIRONMENT != "test" else "DEBUG",
        )
        self.metrics = []

    async def update_module_metrics(self, *args, **kwargs):
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

        if _ENVIRONMENT == "production":
            log(
                "CRITICAL: DummyKnowledgeGraphClient used in PRODUCTION!", level="ERROR"
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
            level="WARNING" if _ENVIRONMENT != "test" else "DEBUG",
        )
        self.created_prs = []
        self.created_tickets = []

    async def create_pr(self, *args, **kwargs):
        """
        Stub implementation that simulates PR creation.

        Returns:
            tuple: (True, "https://github.com/stub-pr-url")
        """
        pr_info = {
            "args": args,
            "kwargs": kwargs,
            "url": "https://github.com/stub-pr-url",
        }
        self.created_prs.append(pr_info)

        log("Using DummyPRCreator. Simulating PR creation.", level="DEBUG")

        if _ENVIRONMENT == "production":
            log(
                "CRITICAL: DummyPRCreator.create_pr called in PRODUCTION! "
                "No actual PR will be created!",
                level="ERROR",
            )

        return True, "https://github.com/stub-pr-url"

    async def create_jira_ticket(self, *args, **kwargs):
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

        if _ENVIRONMENT == "production":
            log(
                "CRITICAL: DummyPRCreator.create_jira_ticket called in PRODUCTION! "
                "No actual ticket will be created!",
                level="ERROR",
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
            level="WARNING" if _ENVIRONMENT != "test" else "DEBUG",
        )

    async def run_mutations(self, *args, **kwargs):
        """
        Stub implementation that simulates 100% mutation score.

        Returns:
            tuple: (True, 100.0, "Stubbed mutation score")
        """
        log("Using DummyMutationTester. Simulating 100% mutation score.", level="DEBUG")

        if _ENVIRONMENT == "production":
            log(
                "CRITICAL: DummyMutationTester.run_mutations called in PRODUCTION! "
                "Mutation testing results are not real!",
                level="ERROR",
            )

        return True, 100.0, "Stubbed mutation score"


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
            level="WARNING" if _ENVIRONMENT != "test" else "DEBUG",
        )

    async def enrich_test(self, content, *args, **kwargs):
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

        if _ENVIRONMENT == "production":
            log(
                "CRITICAL: DummyTestEnricher.enrich_test called in PRODUCTION! "
                "Tests will not be enriched!",
                level="ERROR",
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
