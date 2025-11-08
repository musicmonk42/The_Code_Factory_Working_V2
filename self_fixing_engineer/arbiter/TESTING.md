# Testing – Arbiter (Self-Fixing Engineer)

## Overview

The `tests` directories (root and submodules) contain unit, integration, and end-to-end tests for Arbiter. The test suite uses `pytest-asyncio` for async code and mocks dependencies (e.g., databases, LLMs). **Coverage target:** >95%.  
Tests ensure resilience (e.g., breaker trips), security (e.g., encryption rotation), and functionality (e.g., policy enforcement).

---

## Setup

### Prerequisites

- Install dependencies:
    ```bash
    pip install pytest pytest-asyncio pytest-cov
    ```
- **Mocks:** Use `unittest.mock` for external services (e.g., Redis/DB).

---

## Running Tests

```bash
pytest arbiter/tests/ -v --cov=arbiter --cov-report=html
# Submodule: pytest arbiter/policy/tests/
```

---

## Structure

- **Root `tests/`:** Core (e.g., `test_agent_state.py`, `test_arbiter_knowledge.py`)
- **Submodule `tests/`:** Specific (e.g., `policy/tests/` for breakers/policies, `models/tests/` for backends)

### Types

- **Unit:** Isolated functions/classes (e.g., config validation)
- **Integration:** Interactions (e.g., policy + LLM, with mocks)
- **E2E:** Full flows (e.g., agent simulation; use test DBs)
- **AI-Specific:** Adversarial inputs, bias checks, failure injections

---

## Guidelines

- **Add Tests:** For new features, cover happy/error paths and edge cases (e.g., timeouts, invalid inputs)
- **Mocks:** Use `pytest.fixture` for setups (e.g., mock Redis)
- **Coverage:** Aim for branch/function coverage; exclude generated code
- **CI:** Tests run in GitHub Actions; fail on <95% coverage

---

## Known Gaps

- Expand for multimodal (e.g., image processing mocks)
- Add load tests (e.g., Locust for concurrency)

---

## Contributing to Tests

See root `CONTRIBUTING.md`; PRs must include and/or extend tests.