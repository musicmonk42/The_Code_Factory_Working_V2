<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

# OmniCore Omega Pro Engine Developer Guide

## 1. Environment Setup

- Python 3.8+
- `pip install -r requirements.txt`
- Set up `.env` (see [CONFIGURATION.md](CONFIGURATION.md))
- Initialize DB with alembic

## 2. Plugin Development

- Place plugin files in `PLUGIN_DIR`
- Use `@plugin` decorator (see [PLUGINS.md](PLUGINS.md))
- Hot reload is automatic

## 3. Component Extension

- Inherit from `core.Base`
- Register in `omnicore_engine.components`

## 4. Message Bus Integration

- Integrate custom bridges by subclassing and assigning to message bus

## 5. Debugging

- Set `LOG_LEVEL=DEBUG`
- Use `metrics-status`, `audit-query` CLI commands
- Tail logs: `tail -f omnicore.log`

## 6. Testing

- Run full suite: `pytest tests/ --asyncio-mode=auto --cov=omnicore_engine --cov-report=html`
- Target >85% coverage for core components
- Use mocks for external services

## 7. Best Practices

- Format: `black .`
- Lint: `flake8 .`
- Type check: `mypy omnicore_engine`
- Use `structlog` for logging
- Add Prometheus metrics for components
- Mark plugins as `safe=True` for isolation
- Store secrets out of source control

**See:**  
- [ARCHITECTURE.md](ARCHITECTURE.md)  
- [PLUGINS.md](PLUGINS.md)  
- [TESTING.md](TESTING.md)