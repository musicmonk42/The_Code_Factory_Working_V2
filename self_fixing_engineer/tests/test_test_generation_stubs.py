# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# Fix: Added missing import
import logging

import pytest
from test_generation.orchestrator.stubs import (
    DummyEventBus,
    DummyMutationTester,
    DummyPolicyEngine,
    DummyPRCreator,
    DummySecurityScanner,
    DummyTestEnricher,
)


@pytest.mark.asyncio
async def test_dummy_policy_engine(caplog):
    caplog.set_level(logging.DEBUG)
    policy = DummyPolicyEngine()
    result, reason = await policy.should_integrate_test("test")
    assert result is True
    # The return value from stubs.py was fixed in a previous step to return "Stubbed"
    assert reason == "Stubbed"
    assert "DummyPolicyEngine" in caplog.text
    assert policy.policy_hash == "stub-hash"


@pytest.mark.asyncio
async def test_dummy_event_bus(caplog):
    caplog.set_level(logging.DEBUG)
    bus = DummyEventBus()
    await bus.publish("event")
    assert "DummyEventBus" in caplog.text


@pytest.mark.asyncio
async def test_dummy_security_scanner(caplog):
    caplog.set_level(logging.DEBUG)
    scanner = DummySecurityScanner()
    result, issues, severity = await scanner.scan_test_file("test.py")
    assert result is False
    assert issues == []
    assert severity == "NONE"
    assert "DummySecurityScanner" in caplog.text


@pytest.mark.asyncio
async def test_dummy_pr_creator(caplog):
    caplog.set_level(logging.DEBUG)
    pr = DummyPRCreator()
    success, url = await pr.create_pr("test.py")
    assert success is True
    assert url == "https://github.com/stub-pr-url"
    success, url = await pr.create_jira_ticket("test")
    assert success is True
    assert url == "https://jira.com/stub-ticket"
    assert "DummyPRCreator" in caplog.text


@pytest.mark.asyncio
async def test_dummy_mutation_tester(caplog):
    caplog.set_level(logging.DEBUG)
    tester = DummyMutationTester()
    success, score, log_msg = await tester.run_mutations("test.py")
    assert success is True
    assert score == 100.0
    assert log_msg == "Stubbed mutation score"
    assert "DummyMutationTester" in caplog.text


@pytest.mark.asyncio
async def test_dummy_test_enricher(caplog):
    caplog.set_level(logging.DEBUG)
    enricher = DummyTestEnricher()
    content = "def test(): pass"
    result = await enricher.enrich_test(content)
    assert result == content
    assert "DummyTestEnricher" in caplog.text


@pytest.mark.asyncio
async def test_dummy_policy():
    """
    Tests that the dummy policy engine returns a truthy value for integration.
    This test was added as a user request.
    """
    policy = DummyPolicyEngine()
    result, _ = await policy.should_integrate_test("test")
    assert result


def test_dummy_policy_import():
    """
    Tests that the DummyPolicyEngine class and its should_integrate_test method
    can be correctly imported and are callable.
    """
    from test_generation.orchestrator.stubs import DummyPolicyEngine

    assert callable(DummyPolicyEngine.should_integrate_test)
