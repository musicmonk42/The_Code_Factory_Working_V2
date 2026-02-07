<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

Explainable Reasoner Package README
Overview
The Explainable Reasoner is a Python package for building an AI system that generates explainable responses using large language models (LLMs). It supports multimodal inputs, history management (SQLite/Postgres/Redis), auditing, metrics (Prometheus), prompt strategies, and adapters for providers like OpenAI, Gemini, and Anthropic. Designed for modularity, it's suitable for integration into FastAPI apps or as a standalone plugin.
Key features:

Async LLM inference with fallback to rule-based logic.
Secure handling (PII redaction, JWT RBAC, encryption).
Observability (OpenTelemetry tracing, structlog, Prometheus metrics).
Extensible (prompt factories, backend factories).

Version: 1.2.0 (as of August 20, 2025). No known CVEs in dependencies.
Installation
Prerequisites

Python 3.12.3+
Optional: GPU for local Transformers (CUDA 12.1+)

Via Pip (Recommended for Users)
bashpip install explainable-reasoner
Or from source:
bashgit clone https://github.com/your-repo/explainable-reasoner.git
cd explainable-reasoner
pip install .
Via UV (Recommended for Developers)
For faster installs and virtual envs:
bashpip install uv
uv venv
source .venv/bin/activate  # Or .venv/Scripts/activate on Windows
uv pip sync pyproject.toml  # Assumes pyproject.toml with deps
Dependencies
Core (required):

pydantic >=2.11.7
structlog >=24.4.0
httpx >=0.29.0
cryptography >=46.0.0
prometheus-client >=0.23.0

Optional (conditional imports; warnings if missing):

transformers >=4.55.1 + torch >=2.6.1 (local LLMs)
asyncpg >=0.30.0 (Postgres history)
redis >=5.1.0 (Redis history)
aiosqlite >=0.21.0 (SQLite history)
opentelemetry-api >=1.27.0 (tracing)
pybreaker >=1.1.0 (circuit breakers)
sentry-sdk >=2.14.0 (error tracking)
PyJWT >=2.10.3 (RBAC)
Pillow >=11.3.0 (images)
huggingface_hub >=0.25.1 (models)

Dev tools (for onboarding):

pytest >=8.3.3, pytest-asyncio >=0.24.0, pytest-cov >=5.0.0
hypothesis >=6.115.0 (fuzzing)
locust >=2.31.6 (load testing)
ruff >=0.6.1, black >=24.8.0 (lint/format)

Install all: uv pip sync pyproject.toml (include [dev] extras for tests).
Configuration
Uses environment variables (prefix REASONER_) or files (.env, JSON/YAML).
Key Env Vars

REASONER_MODEL_NAME: Default LLM (e.g., "distilgpt2").
REASONER_DEVICE: GPU/CPU (e.g., "0" or "cpu").
REASONER_MOCK_MODE: True for mocks (dev/testing).
REASONER_STRICT_MODE: True for fail-fast.
REASONER_HISTORY_BACKEND: "sqlite" (default), "postgres", "redis".
REASONER_POSTGRES_DB_URL: Postgres conn string.
REASONER_REDIS_URL: Redis conn string.
REASONER_AUDIT_LOG_ENABLED: True to enable auditing.
REASONER_AUDIT_LEDGER_URL: Audit service URL.
REASONER_JWT_SECRET_KEY: For RBAC (change from default!).
REASONER_ENCRYPTION_KEY: For history encryption.
REASONER_PROMPT_STRATEGY: Override default prompt (e.g., "concise").
PROMETHEUS_MULTIPROC_DIR: For multiprocess metrics.
SENTRY_DSN: For error tracking.

Load from File
pythonfrom explainable_reasoner.reasoner_config import ReasonerConfig

config = ReasonerConfig.from_file("config.yaml")  # YAML/JSON
Example config.yaml:
yamlmodel_name: "distilgpt2"
device: "cpu"
mock_mode: true
history_db_path: "./history.db"
jwt_secret_key: "your-secret-key"
Sensitive values (e.g., keys) are redacted in logs/dumps.
Usage
As a Library
Import and instantiate:
pythonfrom explainable_reasoner import ExplainableReasonerPlugin

plugin = ExplainableReasonerPlugin()
await plugin.initialize()

# Explain a query
result = await plugin.explain("What is AI?", context={"source": "Wikipedia"})
print(result["explanation"])

# Batch
batch_results = await plugin.batch_explain(
    queries=["Query1", "Query2"],
    contexts=[{"ctx1": "val"}, {"ctx2": "val"}]
)

await plugin.shutdown()
As FastAPI App
Create app.py:
pythonfrom fastapi import FastAPI, Request
from explainable_reasoner import ExplainableReasonerPlugin

app = FastAPI()
plugin = ExplainableReasonerPlugin()

@app.on_event("startup")
async def startup():
    await plugin.initialize()

@app.on_event("shutdown")
async def shutdown():
    await plugin.shutdown()

@app.post("/execute")
async def execute_action(request: Request, body: Dict[str, Any]):
    body['client_ip'] = request.client.host  # For IP limiting
    return await plugin.execute(**body)

@app.get("/health")
async def health():
    return await plugin.health_check()

@app.get("/metrics")
async def metrics():
    return Response(content=get_metrics_content(), media_type="text/plain; version=0.0.4")
Run: uvicorn app:app --reload.
Customizing Prompts
Extend PromptStrategy:
pythonfrom explainable_reasoner.prompt_strategies import PromptStrategy, PromptStrategyFactory

class CustomStrategy(PromptStrategy):
    async def create_explanation_prompt(self, context, query, history_str=''):
        return f"Custom: {query}"

PromptStrategyFactory.register_strategy("custom", CustomStrategy)
os.environ["REASONER_PROMPT_STRATEGY"] = "custom"
Onboarding for Developers
Setup Dev Environment

Clone repo: git clone https://github.com/your-repo/explainable-reasoner.git
Install deps: uv sync --dev (includes pytest, ruff, etc.)
Config .env: Copy .env.example; set keys.
Run tests: uv run pytest -v --cov
Lint: uv run ruff check --fix; uv run black .
Load test: uv run locust -f locustfile.py

Project Structure

explainable_reasoner.py: Core plugin/reasoner.
prompt_strategies.py: Prompt templates.
history_manager.py: DB backends.
audit_ledger.py: Logging client.
adapters.py: LLM providers.
utils.py: Helpers (sanitize, rate limit).
metrics.py: Prometheus.
reasoner_config.py: Settings.
reasoner_errors.py: Errors.
__init__.py: Exports.

Contributing

Branch: feature/your-feature
PR: Describe changes, add tests (90% cov).
Style: PEP 8 (ruff/black).
Docs: Update README; docstrings.

Testing

Unit: Pytest for funcs (e.g., prompt gen).
Integration: Asyncio mocks for DB/LLMs.
Fuzz: Hypothesis for inputs.
Load: Locust for /execute.

Deployment

Docker: Build from Dockerfile.
K8s: Deployment with HPA on CPU.
Monitor: Prometheus/Grafana for metrics; Sentry for errors.