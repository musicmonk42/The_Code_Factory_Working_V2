arbiter.learner – Autonomous, Auditable Learning Core
Overview
arbiter.learner is the core AI learning engine for the Self-Fixing Engineer (SFE) platform. It provides secure, validated, and auditable ingestion, transformation, and persistence of knowledge, supporting both structured and unstructured data. Designed for production environments, it ensures end-to-end observability, resilience, compliance, and cryptographic integrity. The module integrates with Redis for caching, PostgreSQL for persistence, Neo4j for knowledge graph relationships, and LLMs for generating human-readable explanations.
Key Features

Structured and Unstructured Data: Supports single-fact learning, batch processing, and fuzzy parsing of unstructured text via pluggable parsers.
Validation & Governance: Enforces JSON schema validation and custom hooks for data integrity before storage.
Auditability & Integrity: Logs all operations with Merkle tree proofs for forensic verification, protected by circuit breakers to prevent cascading failures.
Encryption: Encrypts sensitive domains (e.g., FinancialData, PersonalData) with rotating Fernet keys managed via AWS SSM or fallback in-memory keys.
Explanations: Generates human-readable explanations for learning events using LLMs, with caching and quality feedback tracking.
Observability: Exports comprehensive Prometheus metrics and OpenTelemetry traces for monitoring learning, forgetting, validation, and auditing.
Self-Auditing: Periodically verifies audit log integrity and knowledge base consistency in the background.
Extensibility: Pluggable architecture for custom databases, LLMs, audit backends, and fuzzy parsers.
Concurrency Control: Uses semaphores for concurrent learning and parsing, ensuring scalability without overloading resources.

Directory Structure
arbiter/learner/
├── __init__.py           # Module initialization, logging setup, and environment validation
├── core.py               # Learner and Arbiter classes; learning, forgetting, and retrieval logic
├── audit.py              # CircuitBreaker, MerkleTree, persistence, and audit event management
├── encryption.py         # Encryption, key management, and configuration
├── explanations.py       # LLM-based explanations, caching, and quality feedback
├── fuzzy.py              # Fuzzy parsing for unstructured data with pluggable parsers
├── metrics.py            # Prometheus metrics for learning, auditing, validation, and more
├── validation.py         # JSON schema validation and custom hook registration

Configuration
The module relies on environment variables for configuration, defined in .env or Kubernetes secrets:



Variable
Description
Default



ENVIRONMENT
Deployment environment (e.g., production, test)
production


INSTANCE_NAME
Instance identifier for metrics
learner-instance-1


NEO4J_URL
Neo4j knowledge graph URL
bolt://localhost:7687


LLM_API_KEY
LLM provider API key
dummy_key


ENCRYPTION_KEY_VERSIONS
Comma-separated key versions (e.g., v1,v2)
v1


ENCRYPTED_DOMAINS
JSON list of domains to encrypt
["FinancialData", "PersonalData", "SecretProject"]


KNOWLEDGE_REDIS_TTL_SECONDS
Redis cache TTL
3600


MAX_CONCURRENT_LEARNS
Max concurrent learn operations
50


SCHEMA_RELOAD_RETRIES
Schema reload retry attempts
3


SCHEMA_CACHE_TTL_SECONDS
Schema cache TTL in Redis
3600


CB_FAILURE_THRESHOLD
Circuit breaker failure threshold
5


EXPLANATION_LLM_TIMEOUT_SECONDS
LLM timeout
30.0


FUZZY_PARSER_MAX_CONCURRENT
Max concurrent fuzzy parsers
10


See encryption.py for full configuration details.
Core API
Initialization
from arbiter.learner.core import Arbiter, Learner
from redis.asyncio import Redis

arbiter = Arbiter()
redis = Redis(host='localhost', port=6379, decode_responses=True)
learner = Learner(arbiter=arbiter, redis=redis, db_url="postgresql://user:pass@localhost/db")
await learner.start()  # Initialize async components

Learning a Fact
result = await learner.learn_new_thing(
    domain="FinancialData",
    key="AAPL_2023_Q2",
    value={"revenue": 500_000_000, "profit": 120_000_000},
    user_id="alice",
    source="sec_filing",
    explanation_quality_score=4
)
# Returns: {"status": "learned", "version": 1, "explanation": "...", ...}

Batch Learning
facts = [
    {"domain": "FinancialData", "key": "AAPL_2023_Q2", "value": {"revenue": 500_000_000}},
    {"domain": "PersonalData", "key": "user123_profile", "value": {"name": "John Doe"}}
]
results = await learner.learn_batch(facts, user_id="bob", source="batch_import")
# Returns: [{"status": "learned", ...}, ...]

Forgetting a Fact
result = await learner.forget_fact(
    domain="FinancialData",
    key="AAPL_2023_Q2",
    user_id="alice",
    reason="data_obsolete"
)
# Returns: {"status": "forgotten", "reason": "success"}

Retrieving Knowledge
fact = await learner.retrieve_knowledge(domain="FinancialData", key="AAPL_2023_Q2")
# Returns: {"value": {"revenue": 500_000_000}, "version": 1, ...} or None

Fuzzy Parsing
from arbiter.learner.fuzzy import register_fuzzy_parser_hook, FuzzyParser

class CustomParser(FuzzyParser):
    async def parse(self, text: str, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [{"domain": "Contracts", "key": "contract_001", "value": {"text": text}}]

register_fuzzy_parser_hook(learner, CustomParser(), priority=10)
results = await learner.process_unstructured_data(
    text="Contract signed for $1M",
    domain_hint="Contracts",
    user_id="alice"
)
# Returns: [{"status": "learned", ...}]

Validation and Schema Reload
from arbiter.learner.validation import register_validation_hook

async def custom_hook(value):
    return isinstance(value.get("revenue"), int)

register_validation_hook(learner, "FinancialData", custom_hook)
await learner.reload_schemas(directory="/path/to/schemas")

Observability & Metrics
All operations are instrumented with Prometheus metrics and OpenTelemetry traces, defined in metrics.py. Key metrics include:

arbiter_learner_learn_total: Learning events (domain, source).
arbiter_learner_learn_errors_total: Learning errors (domain, error_type).
arbiter_learner_forget_total: Forgetting events (domain).
arbiter_learner_retrieve_cache_status: Cache hits/misses (domain, cache_status).
arbiter_learner_explanation_llm_latency_seconds: LLM explanation latency (domain).
arbiter_learner_fuzzy_parser_success_total: Successful parser runs (parser_name).
arbiter_learner_validation_success_total: Successful validations (domain).
arbiter_learner_circuit_breaker_state: Circuit breaker state (name).

Start a Prometheus HTTP server in your application:
from prometheus_client import start_http_server
start_http_server(8000)

Configure Grafana dashboards for these metrics and set alerts for high error rates or open circuit breakers.
Audit & Integrity

Every learn/forget operation is audited with Merkle tree proofs (audit.py).
Circuit breakers (CircuitBreaker) prevent DB/audit overload.
Self-audit runs every SELF_AUDIT_INTERVAL_SECONDS to verify audit log integrity and knowledge consistency.

Encryption & Security

Sensitive domains (ENCRYPTED_DOMAINS) are encrypted with Fernet keys (encryption.py).
Keys are loaded from AWS SSM or fallback to in-memory keys.
Rotate keys via ArbiterConfig.rotate_keys("new_version").

Testing & CI

Unit Tests: Cover individual components (tests/test_*.py).
E2E Tests: Simulate full workflows with mocked DB, Redis, and LLM.
Coverage: Aim for >95% coverage using pytest-cov.
CI Pipeline:name: Test arbiter.learner
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.10'
      - run: pip install -r requirements.txt
      - run: pytest tests/ --cov=arbiter.learner --cov-report=xml



Security & Compliance

Encryption: Use ENCRYPTED_DOMAINS for PII/sensitive data.
Audit Logs: Store in append-only storage (e.g., WORM) for compliance.
Key Rotation: Schedule regular key rotations via ArbiterConfig.rotate_keys.
Redaction: Ensure logs redact sensitive data (configure structlog processors).

Troubleshooting

Circuit Breaker Open: Check logs for "Circuit breaker ... opened". Verify DB/audit backend health.
Validation Errors: Ensure schemas in DEFAULT_SCHEMA_DIR match expected data.
Encryption Errors: Verify ENCRYPTION_KEYS and key versions in SSM.
Metric Issues: Confirm metrics are defined only in metrics.py.
LLM Failures: Check explanation_llm_failure_total for timeouts or client errors.

Contributor Guidelines

PRs must include tests for all public APIs and critical paths.
Use black, ruff, and mypy for linting and type checking.
Add Prometheus metrics and OpenTelemetry traces for new features.
Mock external dependencies (DB, Redis, LLM, Neo4j) in tests.
Document new APIs in this README.

References

Prometheus Python Client
OpenTelemetry Python
cryptography – Fernet
Merkle Tree
Circuit Breaker Pattern
structlog

License
© 2025 Novatrax Labs LLC All rights reserved.Proprietary software for internal or licensed use only.Contact legal@novatraxlabs.com for details.
Contact
For questions, bugs, or features:engineering@unexpectedinnovations.com