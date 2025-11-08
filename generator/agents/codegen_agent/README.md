CodeGen Agent Suite
Overview
The CodeGen Agent Suite is a production-ready, modular, and extensible AI code generation pipeline designed for enterprise-grade software development. It integrates top-tier large language models (LLMs), multi-modal inputs, retrieval-augmented generation (RAG), compliance and security scanning, and comprehensive observability to deliver secure, scalable, and auditable code generation. Built for professional developers and DevOps teams, it seamlessly integrates into the Software Development Life Cycle (SDLC) with robust error handling and extensibility.

Key Features

Pluggable LLM Backends: Supports OpenAI, Gemini, Grok (xAI), Anthropic, with ensemble failover for reliability.
Advanced Prompt Engineering: Hot-reloading Jinja2 templates, RAG with Redis vector search, best-practice injection, multi-modal support (images/diagrams), and internationalization via Google Cloud Translate/NLP.
Security & Compliance:
Syntax validation using async native compilers/linters (e.g., Python, JavaScript, Java, Go).
SAST scanning (Bandit, Semgrep) for vulnerabilities and hardcoded secrets.
Compliance checks for banned functions, imports, and license headers.
Secret masking in audit logs using regex-based detection.


Traceability: Automatic insertion of requirement-linked comments for code traceability.
Observability: OpenTelemetry tracing, Prometheus metrics (e.g., PROMPT_BUILD_LATENCY, RESPONSE_PARSE_LATENCY, CODEGEN_LATENCY), and structured audit logging (console/file/SIEM).
Human-in-the-Loop (HITL): Interactive review with Redis Pub/Sub and feedback storage (SQLite/Redis).
Distributed State: Redis-based caching, rate limiting (100 requests/minute), and circuit breaking with file-based fallbacks.
Scalability: Async HTTP calls (aiohttp) and subprocess execution for high concurrency.
Extensibility: Easily add new LLMs, storage backends, compliance rules, or RAG sources.


Architecture
graph TD
    A[User/DevOps] --> B[Prompt Builder]
    B --> |Generates Prompt| C[LLM Call Layer]
    C --> |Processes Response| D[Response Handler]
    B --> |RAG, Best Practices, Multi-Modal| E[Redis, Google Cloud APIs]
    C --> |Caching, Rate Limiting, Circuit Breaking| E
    D --> |Security, Traceability, Audit| E
    E --> |Metrics, Traces| F[Prometheus, Jaeger]
    E --> |Audit Logs| G[SIEM/File/Console]

Main Modules

agents/codegen_agent.pyOrchestrates the end-to-end code generation flow: prompt building, LLM invocation, HITL review, compliance, security scanning, and audit logging.
agents/codegen_prompt.pyBuilds context-aware prompts using RAG (Redis vector search), best practices, multi-modal inputs (Google Cloud Vision), and internationalization (Google Cloud Translate/NLP).
agents/codegen_llm_call.pyManages distributed LLM calls with caching, rate limiting, circuit breaking, and metrics for multiple backends.
agents/codegen_response_handler.pyPost-processes LLM responses with async syntax validation, security scanning, traceability comments, and audit logging.


Setup
1. Environment

Python 3.10+
Redis Stack (for caching, rate limiting, circuit breaking, and vector search)
Google Cloud Vision (optional, for multi-modal inputs)
Google Cloud Translate/NLP (optional, for internationalization)
SAST tools: bandit, semgrep (optional, for security scanning)

2. Install Dependencies
pip install -r requirements.txt

3. Configure Secrets
Set environment variables or use a secrets manager (e.g., AWS Secrets Manager, HashiCorp Vault):

OPENAI_API_KEY, GEMINI_API_KEY, GROK_API_KEY, ANTHROPIC_API_KEY: LLM API keys.
REDIS_URL: Redis connection URL (e.g., redis://localhost:6379).
GOOGLE_APPLICATION_CREDENTIALS: Google Cloud JSON key file path.
GOOGLE_TRANSLATE_API_KEY, GOOGLE_CLOUD_NLP_API_KEY: Google Cloud Translate/NLP API keys.
SEARCH_API_KEY: Brave Search API key for RAG.
OTEL_EXPORTER_JAEGER_ENDPOINT: Jaeger endpoint for tracing (default: localhost).

For local development, create a .env file:
OPENAI_API_KEY=your_openai_key
GROK_API_KEY=your_grok_key
REDIS_URL=redis://localhost:6379
GOOGLE_APPLICATION_CREDENTIALS=/path/to/credentials.json
GOOGLE_TRANSLATE_API_KEY=your_translate_key
GOOGLE_CLOUD_NLP_API_KEY=your_nlp_key
SEARCH_API_KEY=your_brave_search_key

4. Templates
Store Jinja2 templates in templates/ (e.g., python.jinja2, base.jinja2). Templates support hot-reloading for development.
5. Configure Audit and Feedback Stores
In prod_config.yaml, configure audit logging (console or file) and feedback storage (sqlite or redis):
audit_logger:
  type: console  # or "file" with path, max_bytes, backup_count
feedback_store:
  type: sqlite  # or "redis" with url, ttl
  path: prod_feedback.db


Usage
Full Pipeline Example
Generate a FastAPI-based REST API with multi-modal inputs and internationalization:
import asyncio
from agents.codegen_agent import generate_code

requirements = {
    "features": ["Implementar una API REST para gestionar tareas."],  # Spanish for internationalization
    "constraints": ["Use FastAPI.", "Include type hints."],
    "target_language": "python"
}
multi_modal_inputs = {
    "image_urls": ["https://example.com/diagram.png"]
}
config = {
    "backend": "openai",
    "api_keys": {"openai": "your_openai_key"},
    "model": {"openai": "gpt-4o"},
    "allow_interactive_hitl": True,
    "enable_security_scan": True,
    "feedback_store": {"type": "redis", "url": "redis://localhost:6379"},
    "audit_logger": {"type": "console"},
    "compliance": {"banned_functions": ["eval"], "max_line_length": 120}
}

async def main():
    result = await generate_code(requirements, "Initial state.", config, multi_modal_inputs=multi_modal_inputs)
    for fname, content in result.items():
        print(f"{fname}:\n{content}\n")

if __name__ == "__main__":
    asyncio.run(main())

CLI Example
python agents/codegen_agent.py


Extending/Customizing

Add LLMs: Implement a new backend in codegen_llm_call.py and update CodeGenConfig in codegen_agent.py.
Swap Audit/Feedback Storage: Implement AuditLogger or FeedbackStore interfaces in codegen_agent.py and update config.
Enhance Compliance: Extend SecurityUtils.apply_compliance in codegen_agent.py with new rules.
Improve RAG: Add new sources to knowledge_base in codegen_prompt.py or integrate alternative search APIs.


Security & Ops Notes

Secrets: Use a secrets manager in production; never hard-code API keys.
Audit: Structured logs are compatible with SIEM tools (e.g., Splunk) via AuditLogger.
Observability: Expose /metrics endpoint for Prometheus and OpenTelemetry traces to Jaeger.
Performance: Async subprocess calls and Redis ensure high concurrency.
Dependencies: Install only required SAST tools in production containers.


Support

For issues, questions, or feature requests, open a GitHub Issue.
Contributions welcome!


License
See LICENSE.