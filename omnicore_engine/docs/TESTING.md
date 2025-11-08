# OmniCore Omega Pro Engine Testing Guide

## Overview

- **Test types:** Unit, integration, end-to-end, concurrency, stress
- **Coverage:** Aim for >85% for all core modules

## Setup

- Install test dependencies: `pytest`, `pytest-asyncio`, `pytest-cov`, `pytest-mock`
- Set `DATABASE_URL=sqlite+aiosqlite:///test.db` in .env

## Running Tests

```bash
pytest tests/ --asyncio-mode=auto --cov=omnicore_engine --cov-report=html
```

- Review `htmlcov/index.html` for coverage

## Writing Tests

- Unit: Test individual modules/classes
- Integration: Test module interactions (e.g., plugin exec via message bus)
- E2E: Test full workflows (e.g., /fix-imports endpoint)
- Concurrency: Multiple plugin/message executions
- Stress: Load testing the message bus

**Mock** external systems for isolation (Kafka, Redis, DB).

## Best Practices

- Use `pytest.mark.asyncio` for async tests
- Mock external dependencies
- Cover normal and error/edge cases
- Use descriptive names
- Maintain coverage for all critical paths

**See:**  
- [PLUGINS.md](PLUGINS.md)  
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md)