# Intent Agent Module – Self-Fixing Engineer (SFE) 🚀

**Intent Agent v2.0.0 – The _Golden Dialogue_ Edition**  
*Proprietary Technology by Unexpected Innovations Inc.*

---

Transform requirements into reality with Intent Agent’s intelligent orchestration.

> **Note:** This technology is proprietary and not open-sourced. All rights reserved by Unexpected Innovations Inc.  
> Unauthorized use, distribution, or reverse engineering is strictly prohibited.  
> For licensing inquiries, contact [licensing@unexpectedinnovations.com](mailto:licensing@unexpectedinnovations.com).

---

## Overview

The **Intent Agent module** is the conversational core of the Self-Fixing Engineer (SFE) platform, designed for enterprise-grade requirements capture and management. It leverages AI-driven agents, multi-modal processing, collaborative workflows, and robust orchestration.

- **Conversational AI:** Capture, refine, and review requirements with intelligent agents.
- **Multi-Modal:** Process images, audio, and video.
- **Enterprise-Ready:** Secure, observable, extensible, and scalable.

Crafted with precision in Fairhope, Alabama, USA.

---

## Table of Contents

- [Features](#features)
- [Architecture](#architecture)
- [Getting Started](#getting-started)
  - [Prerequisites](#prerequisites)
  - [Installation](#installation)
  - [Configuration](#configuration)
- [Usage](#usage)
  - [CLI Interface](#cli-interface)
  - [REST API](#rest-api)
  - [Web UI](#web-ui)
  - [Multi-Modal Processing](#multi-modal-processing)
- [Extending Intent Agent](#extending-intent-agent)
- [Key Components](#key-components)
- [Tests](#tests)
- [Troubleshooting](#troubleshooting)
- [Best Practices](#best-practices)
- [Contribution Guidelines](#contribution-guidelines)
- [Roadmap](#roadmap)
- [Support](#support)
- [License](#license)

---

## Features

- **Conversational AI**
  - Collaborative agents with self-correction (`reflect`, `critique`, `correct`)
  - Multi-modal processing for images, audio, and video
  - Dynamic personas and language support

- **Interfaces**
  - Interactive CLI with autocomplete/macros
  - Streamlit-based web UI for real-time collaboration
  - FastAPI REST API (JWT auth, rate limiting)

- **State Management**
  - Encrypted, prunable sessions (GDPR/CCPA)
  - Redis/in-memory backends for scalability

- **Requirements & Specs**
  - Dynamic checklists and coverage reports
  - Automated spec generation, refinement, and review

- **Security & Observability**
  - PII masking, encryption, and safety guardrails
  - Prometheus metrics, OTEL tracing, Sentry, JSON logging

- **Reliability & Scalability**
  - Circuit breakers, retries, queuing
  - Async operations, container-ready

- **Compliance**
  - Audit logging, data pruning, consent-based features

> All features are production-hardened for 2025: Vault secrets, K8s-ready, CI/CD examples.

---

## Architecture

**Layered, modular design for extensibility:**

- **Core Layer:**  
  `agent_core.py` – Agents, LLM providers, RAG

- **Utils Layer:**  
  `config.py`, `io_utils.py`, `session.py`, `spec_utils.py`, `requirements.py`

- **Interface Layer:**  
  `cli.py`, `autocomplete.py`, `web_app.py`, `api.py`

- **Plugins & Backends:**  
  Extend with plugins/hooks, Redis/Pinecone for state/RAG, Vault/AWS for secrets

**Data Flow:**  
User input → Sanitize → Agent (with history/RAG) → Output (specs/responses) → Audit/Metrics

**Scalability & Security:**  
- Horizontal scaling (K8s pods, load-balanced API/UI)
- Zero-trust (JWT, RBAC), strict input validation

_For diagrams, see PlantUML examples in code or contact support for Visio files._

---

## Getting Started

### Prerequisites

- Python 3.12+
- Redis 7+ (state/caching)
- Docker/K8s for deployment
- LLM keys (OpenAI, Anthropic, Google)
- Optional: RabbitMQ, Vault, Pinecone, Sentry, Prometheus/Grafana

**Hardware:**  
Min 16GB RAM, GPU for ML tasks

### Installation

```bash
# Clone (enterprise access required)
git clone <repo-url>
# Install dependencies
pip install -r requirements.txt
# Download NLTK data
python -c "import nltk; nltk.download('punkt stopwords')"
# Build Docker image
docker build -t intent-agent .
```

_For air-gapped environments: Provide dependencies via Artifactory._

### Configuration

`.env` template:
```
OPENAI_API_KEY=sk-...
REDIS_URL=redis://localhost:6379/0
JWT_SECRET=strong_secret
PROD_MODE=true
ENCRYPTION_KEY=fernet_key
SENTRY_DSN=https://...
OTEL_EXPORTER_OTLP_ENDPOINT=localhost:4317
```

- `config.py` supports Vault/AWS and hot-reloads.
- Validate config: `python config.py --validate`
- For enterprises: Use K8s ConfigMap/Secrets.

---

## Usage

### CLI Interface

```bash
python cli.py
```

- **Commands:** `ai: query`, `generate spec`, `collab start` (websockets)
- **Autocomplete:** TAB for AI/fuzzy suggestions
- **Example:** `ai: Capital of France?` → Response panel
- **Logs:** JSON to `cli.log` (rotatable)

### REST API

```bash
uvicorn api:create_app --factory --port=8000
```

**Endpoints:**

- `POST /token` – JWT auth
- `POST /predict` – Predict with session
- `POST /prune_sessions` – Prune old data

- **Headers:** `Authorization: Bearer <token>`
- **Docs:** `/docs` (Swagger)
- **Security:** CORS, TrustedHost, rate limiting

### Web UI

```bash
streamlit run web_app.py
```

- **Pages:** Home, Refine, Review, Collab, Plugins, Health
- **Auth:** Bcrypt login
- **Real-time:** Redis pub/sub, auto-refresh

_For SSO: Integrate OAuth in `web_app.py`._

### Multi-Modal Processing

- Upload image/audio/video via Web/API
- Processing: `io_utils.py` handles, plugins analyze (OCR/transcribe/summarize)
- Example: `POST /predict` with file URL → multi-modal trace in response

---

## Extending Intent Agent

- **Adding Agents:** Inherit `CollaborativeAgent` in `agent_core.py`, override `predict`, register via `get_or_create_agent(custom_id)`
- **Custom Plugins:** Place in `plugins/`, use `plugin.json`. Hooks: `on_predict`, `on_save`
- **New Spec Formats:** Register in `spec_utils.py`, validate with JSONSchema/Pydantic
- **Custom Checklists:** Register in `requirements.py`, persist as needed

_See `extension_points.md` for details (Sphinx-generated)._

---

## Key Components

- `__init__.py`: Package exports
- `agent_core.py`: Agents, LLM factory, self-correction
- `api.py`: FastAPI app, middleware
- `autocomplete.py`: CLI completion/macros
- `cli.py`: Main CLI loop
- `config.py`: Pydantic settings, reload
- `io_utils.py`: Secure file I/O
- `requirements.py`: Checklists, coverage
- `session.py`: Encrypted state/pruning
- `spec_utils.py`: Spec gen/refine/diff
- `web_app.py`: Streamlit UI

**Dir Structure:**

```
intent_capture/      # Core modules
plugins/             # Extensions
tests/               # Pytest suite
simulation_results/  # Outputs
```

**Coding Standards:** Ruff lint, MyPy types, Black format  
**CI:** GitHub Actions (ruff/mypy/pip-audit)  
**Error Handling:** Sentry, circuit breakers  
**Async:** `asyncio.run`, `AsyncMock` in tests  
**Deps:** Pin in `requirements.txt`, vuln scanning (Trivy)  
**Docs:** Sphinx (type hints/docstrings)

---

## API Endpoints and Schemas

- **Models:** Pydantic (e.g., `PredictRequest: user_input, session_token`)
- **Full list:** `/docs` (Swagger)
- **Schemas:** `PredictResponse: response, trace`

---

## CLI Commands Reference

- `help`: List commands
- `set provider openai`: Switch LLM
- `collab start`: Start websockets
- **Full list:** See `COMMAND_HELP` in `cli.py`

---

## Extension Points for Developers

- **Hooks:** `PluginManager` in `config.py`
- **Events:** Emit via RabbitMQ in `api.py`
- **Custom LLM:** Implement `LLMProvider`
- **Tests:** Add to `tests/` with mocks

---

## Performance Optimization

- **Caching:** TTLCache/Redis
- **Async:** All I/O/network calls
- **Profiling:** `cProfile` toggle via env

---

## Security and Compliance

- **Auth:** JWT, RBAC
- **Encryption:** Fernet for sessions
- **Audits:** S3 logs, prune via cron
- **Vuln Scans:** Snyk in CI

---

## Observability and Monitoring

- **Metrics:** Prometheus (e.g., `prediction_latency_seconds`)
- **Tracing:** OTEL spans
- **Alerts:** Prometheus rules (see code comments)
- **Logs:** JSON, ELK integration

---

## Scalability Considerations

- **Horizontal:** K8s autoscaling
- **DB:** Sharded Redis/Postgres
- **Load:** Rate limits, queues

---

## Tests

- **Unit/Integration:** `pytest tests/ --cov` (90%+ target)
- **E2E:** `test_e2e.py` (Playwright/httpx/pexpect)
- **Vuln:** `pip-audit`, `trivy fs ..`
- **Load:** Locust scripts in `tests/load/`

---

## Troubleshooting

- **LLM Errors:** Set `OPENAI_API_KEY` in `.env`. Run `python -m agent_core selftest`
- **Redis Issues:** Verify `REDIS_URL`, health-check with `python -m autocomplete --health redis://localhost:6379/0`
- **API Auth Errors:** Update `auth_config.yaml` or OIDC in `api.py`. Test with `curl`
- **Web UI Errors:** Ensure Streamlit installed, run `streamlit run web_app.py -- --debug`
- **SIEM Logging:** Check plugin config, run health-check
- **Encryption Failures:** Generate Fernet key:
  ```python
  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  ```
- **Performance:** Enable profiling (`PROFILE_ENABLED=true`). Analyze with Snakeviz
- **Compliance Pruning:** Test with `python session.py --prune 90`, verify S3 logs

_For logs: `tail -f cli.log | jq`_

---

## Best Practices

- **Secrets:** Use Vault/AWS SSM, never commit secrets
- **Observability:** Use Grafana dashboards
- **Deployment:** Docker/K8s, Helm charts (contact support)
- **Testing:** CI matrix (Python versions, deps)
- **Code Reviews:** Enforce via PR templates
- **Backup:** Redis snapshots, session exports
- **Audits:** Quarterly SAST/DAST

---

## Contribution Guidelines

- **Contributions require NDA.** Contact [contrib@unexpectedinnovations.com](mailto:contrib@unexpectedinnovations.com)
- **Style:** PEP8, Ruff/Black
- **Tests:** 100% coverage for new code
- **Docs:** Update Sphinx, commit examples
- **PRs:** Squash commits, sign CLA

---

## Roadmap

- **Grok 3 Integration:** xAI API for LLMs
- **Async Enhancements:** Full async Redis/file
- **Custom ML:** Fine-tune embeddings
- **Collab:** WebSocket scaling
- **Multi-Modal:** More formats (PDF/3D)

*Q4 2025*: Federated learning for on-prem

---

## Support

- **Enterprise support:** [support@unexpectedinnovations.com](mailto:support@unexpectedinnovations.com) (24/7 SLA)
- **Docs:** Internal wiki (portal access)
- **Issues:** Jira (`<jira-url>`)

---

## License

**Proprietary and Confidential © 2025 Unexpected Innovations Inc. All rights reserved.**  
Intent Agent and Self-Fixing Engineer™ are proprietary technologies.

Unauthorized copying, distribution, reverse engineering, or use is strictly prohibited.

For commercial licensing or evaluation, contact [commercial@unexpectedinnovations.com](mailto:commercial@unexpectedinnovations.com).

_Capture intent with precision using Intent Agent’s AI-driven orchestration._