from test_generation.orchestrator.console import log


class DummyPolicyEngine:
    """A stub for the PolicyEngine for offline/demo mode."""

    async def should_integrate_test(self, *args, **kwargs):
        log(
            "Using DummyPolicyEngine. All tests are allowed to be integrated.",
            level="DEBUG",
        )
        # FIX: Ensure should_integrate_test returns the specified stub value
        return True, "Stubbed"

    async def requires_pr_for_integration(self, *args, **kwargs):
        log("Using DummyPolicyEngine. No PRs are required.", level="DEBUG")
        return False, "Stubbed policy requires no PR"

    @property
    def policy_hash(self):
        return "stub-hash"


class DummyEventBus:
    """A simple stub for the event bus that does nothing."""

    async def publish(self, *args, **kwargs):
        log(f"Using DummyEventBus. Event published: {args}", level="DEBUG")
        pass


class DummySecurityScanner:
    """A stub for the security scanner."""

    async def scan_test_file(self, *args, **kwargs):
        log("Using DummySecurityScanner. No security issues found.", level="DEBUG")
        return False, [], "NONE"


class DummyKnowledgeGraphClient:
    """A stub for the Knowledge Graph client."""

    async def update_module_metrics(self, *args, **kwargs):
        log(f"Using DummyKnowledgeGraphClient. Metrics updated: {kwargs}", level="DEBUG")
        pass


class DummyPRCreator:
    """A stub for the PR creator that simulates success."""

    async def create_pr(self, *args, **kwargs):
        log("Using DummyPRCreator. Simulating PR creation.", level="DEBUG")
        return True, "https://github.com/stub-pr-url"

    async def create_jira_ticket(self, *args, **kwargs):
        log("Using DummyPRCreator. Simulating Jira ticket creation.", level="DEBUG")
        return True, "https://jira.com/stub-ticket"


class DummyMutationTester:
    """A stub for the mutation tester."""

    async def run_mutations(self, *args, **kwargs):
        log("Using DummyMutationTester. Simulating 100% mutation score.", level="DEBUG")
        return True, 100.0, "Stubbed mutation score"


class DummyTestEnricher:
    """A pass-through stub for the test enricher."""

    async def enrich_test(self, content, *args, **kwargs):
        log("Using DummyTestEnricher. No enrichment applied.", level="DEBUG")
        return content
