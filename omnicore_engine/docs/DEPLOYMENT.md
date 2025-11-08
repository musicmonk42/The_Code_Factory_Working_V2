# OmniCore Omega Pro Engine Deployment Guide

## 1. Environment Setup

- **Python:** 3.8+
- **Dependencies:** `pip install -r requirements.txt`
- **Database:** SQLite for dev, PostgreSQL/Citus for prod
- **Tools:** alembic (migrations), pytest, black, flake8, mypy

**Example installation:**
```bash
git clone <repository-url>
cd Code_Factory/omnicore_engine
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## 2. Configuration

Edit `.env` (see [CONFIGURATION.md](CONFIGURATION.md)), then:

```bash
alembic init migrations
alembic revision --autogenerate -m "Initial migration"
alembic upgrade head
```

## 3. Running

- **FastAPI:**  
  `python -m omnicore_engine.cli serve`
- **CLI:**  
  See [API_REFERENCE.md](API_REFERENCE.md) for commands.

## 4. Plugin Development

- Place `.py` plugin files in `PLUGIN_DIR`
- Use `@plugin` decorator (see [PLUGINS.md](PLUGINS.md))
- Hot-reload is automatic

## 5. Production

- **Docker:** See included Dockerfile.
- **Kubernetes:** See `omnicore-deployment.yaml` and `omnicore-config.yaml`
- **PostgreSQL:**  
  - Install Citus if needed
  - Run migrations
- **Backup:** Use `pg_dump` for regular DB backups

## 6. Monitoring

- **Prometheus:** Scrape `/metrics` endpoint
- **Grafana:** Import a dashboard for key metrics
- **Logging:** Use `structlog`, tail `omnicore.log`

## 7. Best Practices

- Format: `black .`
- Lint: `flake8 .`
- Type check: `mypy omnicore_engine`
- Secure secrets in production
- Tune bus shards, cache, DLQ
- Test all changes before deployment

**See:**  
- [ARCHITECTURE.md](ARCHITECTURE.md)  
- [PLUGINS.md](PLUGINS.md)  
- [TESTING.md](TESTING.md)  
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md)