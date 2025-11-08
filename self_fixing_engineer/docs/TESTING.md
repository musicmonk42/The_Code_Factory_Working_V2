# 🧪 TESTING.md — Self Fixing Engineer™

---

## Table of Contents

1. [Testing Philosophy & Objectives](#testing-philosophy--objectives)
2. [Supported Test Types & Coverage Policy](#supported-test-types--coverage-policy)
3. [Directory Structure & Naming Conventions](#directory-structure--naming-conventions)
4. [Running All Tests (Local, CI/CD, Cloud)](#running-all-tests-local-cicd-cloud)
5. [Writing & Registering New Tests](#writing--registering-new-tests)
6. [Async, Integration, and Property-Based Testing](#async-integration-and-property-based-testing)
7. [Mocks, Fakes, and Dependency Injection](#mocks-fakes-and-dependency-injection)
8. [Determinism, Isolation, and State Management](#determinism-isolation-and-state-management)
9. [Regression, Performance, and Chaos Testing](#regression-performance-and-chaos-testing)
10. [Security & Compliance Test Automation](#security--compliance-test-automation)
11. [Required Coverage Gates & Quality Gates](#required-coverage-gates--quality-gates)
12. [Test Failures, Triage, and Escalation](#test-failures-triage-and-escalation)
13. [Reproducibility, Artifacts, and Audit](#reproducibility-artifacts-and-audit)
14. [Reference Examples](#reference-examples)
15. [Test Data, Secrets, and Privacy](#test-data-secrets-and-privacy)
16. [Troubleshooting, FAQ, and Continuous Improvement](#troubleshooting-faq-and-continuous-improvement)
17. [Contacts, Ownership, and Contribution](#contacts-ownership-and-contribution)
18. [Appendix: Sample CI/CD Test Workflow](#appendix-sample-cicd-test-workflow)
19. [Further Reading](#further-reading)

---

## 1. Testing Philosophy & Objectives

Testing in Self Fixing Engineer™ is not optional—it is core to the platform’s integrity, security, and value.

Tests must:
- Prevent regressions at every code change
- Validate both “happy path” and edge/failure modes
- Support autonomous, CI-driven, and human-in-the-loop workflows
- Enable full audit, reproducibility, and explainability of test outcomes
- Cover the real user journey, not just “unit happy paths”

---

## 2. Supported Test Types & Coverage Policy

| Type           | Goal                                         | Minimum Coverage                    |
|----------------|----------------------------------------------|-------------------------------------|
| Unit Tests     | Verify atomic logic/functions                | 95% lines/branches (core)           |
| Integration    | Verify multi-module, plugin, and external flows| 90% of major workflows              |
| End-to-End     | Full-stack, user-level simulation            | All critical paths                  |
| Async/Concurrency| Validate parallel/async logic              | All async code                      |
| Property-Based/Fuzz| Surface edge-cases, invariant violations | All exposed APIs                    |
| Security/Compliance| Enforce policy, sandbox, permission boundaries| All policy surfaces               |
| Performance    | Verify throughput, resource, scaling         | Critical ops/flows                  |
| Regression     | Catch new bugs from old test sets            | Historical defects                  |
| Chaos/Resilience| Test failure, restart, and partial outage   | Crew/simulation core                |

---

## 3. Directory Structure & Naming Conventions

```
arbiter/
  ├── arbiter.py
  └── tests/
        └── test_arbiter_knowledge.py

intent_capture/
  ├── api.py
  └── tests/
        └── test_intent.py

simulation/
  ├── code_health_env.py
  └── tests/
        └── test_sim.py

tests/           # Platform-wide, e2e, or smoke tests
  ├── test_integration.py
  └── test_security.py
```

- Test files: `test_<module_or_feature>.py`
- Test classes: `Test<ClassOrFeature>`
- Test functions: `test_<behavior>_when_<condition>`

---

## 4. Running All Tests (Local, CI/CD, Cloud)

**All tests:**
```bash
pytest -v
```

**Subsystem:**
```bash
pytest simulation/tests/test_sim.py -v
```

**Fail fast after first/third failure:**
```bash
pytest -x       # stop after 1st failure
pytest --maxfail=3
```

**CI/CD:**
- All branches and PRs run tests automatically; merges blocked on failure or coverage drop.
- Recommended: Use coverage tools (`pytest-cov`) and coverage badge in README.

---

## 5. Writing & Registering New Tests

- Every new module/feature = new test file
- Use pytest for all test logic.
- Async code: use `@pytest.mark.asyncio`.
- Always test error paths, permission errors, and edge-cases.
- Document “why this test matters” with docstrings.

**Async Example:**
```python
@pytest.mark.asyncio
async def test_intent_capture():
    from intent_capture.api import handle_intent
    result = await handle_intent({"query": "Generate a function"})
    assert "def " in result["response"]
```

---

## 6. Async, Integration, and Property-Based Testing

- **Async:** All async/await logic must be directly tested using pytest-asyncio.
- **Integration:** Cross-module and plugin/adapter boundaries require integration tests; use real and mocked endpoints.
- **Property-Based/Fuzz:** Use hypothesis or pytest-randomly to generate edge-case inputs.
- **Stateful/Long-Running:** Simulate extended/realistic agent crew sessions and plugin lifecycles.

---

## 7. Mocks, Fakes, and Dependency Injection

- Mock all cloud, DB, network, and external APIs to ensure fast, deterministic tests.
- Use unittest.mock, pytest-mock, or your own fakes.
- Always test both “mocked” (unit) and “real” (integration) scenarios.

---

## 8. Determinism, Isolation, and State Management

- Each test must be repeatable—set seeds, clear caches/state between runs.
- No test may depend on previous state or order of execution.
- Use pytest fixtures for setup/teardown and temporary directories or data.

---

## 9. Regression, Performance, and Chaos Testing

- **Regression:** Maintain test suites for every previously fixed bug. Add new tests for every outage or incident (root cause).
- **Performance:** Use pytest-benchmark or similar. Track time/memory for critical agent, plugin, and simulation flows.
- **Chaos:** Simulate failure, partial outage, restart, or API latency. Crew/simulation must “fail safe,” and audits must remain unbroken.

---

## 10. Security & Compliance Test Automation

- Test all permission, sandbox, and plugin boundaries.
- Simulate policy violations and attempted privilege escalation.
- Verify all audit, logging, and rollback functions.
- All security tests must run in CI on every commit.

---

## 11. Required Coverage Gates & Quality Gates

**Minimum:**
- 95%+ line/branch for core modules, 90%+ for plugins/adapters
- No skipped/xfail tests in main
- All tests must pass for PR/merge
- No “test debt” allowed for critical code

**Block merge on any:**
- New uncovered lines/branches
- Failed or flaky tests
- Coverage drop from main

---

## 12. Test Failures, Triage, and Escalation

- All failures immediately alert dev/QA/SRE (Slack/email/SIEM).
- Failures triaged:
  - P0 (critical path down): fix/block release within 24h.
  - P1 (major regression): fix in next sprint.
  - P2 (non-critical): log and fix in regular cycle.
- All root cause analyses linked to corresponding test cases.

---

## 13. Reproducibility, Artifacts, and Audit

- Store all failed test artifacts (logs, traces, screenshots) in `artifacts/` (with retention policy).
- Every test run is hashed, signed, and auditable (integration with audit mesh).
- CI/CD artifacts include coverage, logs, and performance traces.

---

## 14. Reference Examples

**Integration:**
```python
def test_full_simulation_run():
    from simulation.main_sim_runner import main
    result = main(config_path="sim_config.yaml")
    assert result["success"]
```

**Property-based:**
```python
from hypothesis import given, strategies as st

@given(st.text())
def test_handle_any_input(text):
    from intent_capture.api import handle_intent
    result = handle_intent({"query": text})
    assert isinstance(result, dict)
```

---

## 15. Test Data, Secrets, and Privacy

- Use only synthetic, anonymized, or mock data in all tests.
- Never commit or log real secrets, keys, or customer data.
- Use test fixtures or factories for user/token generation.

---

## 16. Troubleshooting, FAQ, and Continuous Improvement

- **Test fails locally, not in CI?** Check for local state, dependency mismatch, or OS difference.
- **Flaky tests?** Run with `--lf` (last-failed) and inspect logs for nondeterminism.
- **Slow tests?** Profile, parallelize, and mock slow dependencies.
- **How do I propose new coverage/quality policy?** PR to this file, tagged for test-lead review.

---

## 17. Contacts, Ownership, and Contribution

- Test infrastructure owner: [qa-lead@yourcompany.com]
- Escalation (test failures, security): [sre-oncall@yourcompany.com]
- For contributing new test types, see [CONTRIBUTING.md]

---

## 18. Appendix: Sample CI/CD Test Workflow

```yaml
name: CI Test
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: "3.11"
      - name: Install dependencies
        run: pip install -r requirements.txt
      - name: Run pytest with coverage
        run: pytest --cov=./ --cov-report=xml --cov-fail-under=95
      - name: Store test artifacts
        uses: actions/upload-artifact@v4
        with:
          name: test-results
          path: artifacts/
```

---

## 19. Further Reading

- pytest documentation
- pytest-asyncio
- Hypothesis property-based testing
- CI/CD Best Practices
- Python security testing (Bandit)

---