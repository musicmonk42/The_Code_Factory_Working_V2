<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

LLM Plugin Manager
A production-grade, secure, hot-reloadable plugin architecture for managing multiple Large Language Model (LLM) providers in real time, offering full observability, contract enforcement, and robust error handling.

Table of Contents

Features
Architecture
Security & Integrity
Plugin Authoring
Metrics & Observability
Hot Reload & Recovery
Error Handling & Health
Provider Capabilities
Production Best Practices
Test Coverage
Operational Procedures
Development Guide
License


Features

Dynamic Plugin Discovery: Automatically detects and loads provider plugins from a specified directory without requiring a system restart.
Hot Reloading: Monitors the plugin directory for changes (add, edit, delete, move) and reloads plugins seamlessly with zero downtime.
Comprehensive Observability: Instruments all operations with Prometheus metrics, OpenTelemetry traces, and structured logging for full visibility.
Security-First Design: Enforces SHA-256 integrity checks, prompt/response scrubbing for PII, and supports sandboxing for untrusted plugins.
Concurrency-Safe: Uses asyncio.Lock to ensure thread-safe plugin loading and registry updates.
Robust Error Handling: Isolates plugin errors, tracks health per provider, and sends alerts for critical failures.
Extensible Contract: Supports advanced provider features like streaming, custom endpoints, hooks, and cost tracking.
Production-Ready: Integrates with webhook alerting, YAML configuration, and a comprehensive test suite for CI/CD pipelines.


Architecture
Overview
The LLM Plugin Manager is designed to orchestrate multiple LLM providers (e.g., OpenAI, Claude, Gemini, Grok, local models like Ollama) through a central LLMPluginManager class. It dynamically loads provider plugins, manages their lifecycle, and exposes them via a registry for runtime use.

Central Manager (LLMPluginManager): 

Scans PLUGIN_DIR for .py files, validates their integrity, and loads providers via the get_provider() function.
Maintains a registry of loaded providers, accessible via get_provider(name) and list_providers().
Handles hot-reloading using watchdog for file system events.
Ensures concurrency safety with asyncio.Lock.


Plugins: 

Each plugin is a Python module (e.g., ai_provider.py, claude_provider.py) implementing the LLMProvider interface.
Providers expose methods like call, health_check, and support advanced features (streaming, hooks, custom endpoints).


Observability: 

Prometheus metrics track plugin loads, reloads, errors, and health.
OpenTelemetry traces capture detailed execution paths for debugging and performance analysis.


Lifecycle:

Initialization: Manager scans PLUGIN_DIR, validates hashes, and loads providers.
Runtime: Applications call providers through the manager's registry.
Hot-Reload: File changes trigger atomic reloads, updating the registry without disrupting active calls.
Shutdown: Manager stops the file watcher and cleans up resources.




Architecture Diagram

+-------------------+
| LLMPluginManager  |
| - Registry        |
| - File Watcher    |
| - Lock            |
+-------------------+
         |
         v
[Scans PLUGIN_DIR]
         |
         v
+------------------+    ...    +------------------+
|  ai_provider.py  |  ... ...  | local_provider.py |
|  (OpenAI)        |           | (Ollama)         |
+------------------+           +------------------+
         |                             |
         v                             v
   [get_provider()]             [get_provider()]
         |                             |
         v                             v
+------------------+           +------------------+
| OpenAIProvider   |           | LocalProvider    |
| - call()         |           | - call()         |
| - health_check() |           | - health_check() |
+------------------+           +------------------+
         |                             |
         v                             v
       Registry of Providers




Security & Integrity

Hash Verification: 

Plugins are validated against a SHA-256 hash manifest (plugin_hash_manifest.json) to prevent tampering.
Configure HASH_MANIFEST in llm_plugin_config.yaml or environment variables.
Store manifests in a secure location (e.g., AWS KMS, Vault) for production.


Prompt/Response Scrubbing: 

All providers scrub inputs/outputs for sensitive data (API keys, emails, credit cards, SSNs) using regex patterns before logging or tracing.


Error Isolation: 

Faulty plugins are marked unhealthy, logged, and excluded from the registry without affecting others.


Sandboxing: 

Plugins run in-process by default. For untrusted plugins, deploy in containers or subprocesses using tools like Docker or multiprocessing.
Example: Run each plugin in a Docker container with restricted permissions and network access.




Plugin Authoring
Contract (Minimum Requirements)
A provider plugin must:

Define a get_provider() function returning an instance of a class implementing the LLMProvider interface.
Implement the call method with signature: async def call(self, prompt: str, model: str, stream: bool = False) -> Union[Dict[str, Any], AsyncGenerator[str, None]].
Set a name attribute (string) to identify the provider in the registry.

Example Minimal Provider
# my_provider.py
class MyProvider:
    def __init__(self):
        self.name = "myprovider"
    
    async def call(self, prompt: str, model: str, stream: bool = False) -> Union[Dict[str, Any], AsyncGenerator[str, None]]:
        if stream:
            async def stream_response():
                yield "chunk1"
                yield "chunk2"
            return stream_response()
        return {"content": f"Response to {prompt}", "model": model}
    
    async def health_check(self) -> bool:
        return True

def get_provider():
    return MyProvider()

Advanced Features

Streaming: Return an AsyncGenerator for streaming responses.
Health Checks: Implement health_check for provider availability.
Hooks: Add pre/post-processing hooks via add_pre_hook and add_post_hook.
Custom Endpoints: Support custom APIs with register_custom_endpoint and register_custom_headers.
Cost Tracking: Implement token counting and cost estimation.

Place the .py file in PLUGIN_DIR, and it will be auto-loaded (with hash verification if enabled).

Metrics & Observability
Prometheus Metrics

Plugin Metrics (from llm_plugin_manager.py):

llm_plugin_loads_total: Tracks plugin load attempts (plugin_name).
llm_plugin_reloads_total: Tracks reload events (plugin_name).
llm_plugin_errors_total: Tracks errors (plugin_name, error_type).
llm_plugin_health: Tracks health status (1=healthy, 0=unhealthy, plugin_name).
llm_plugin_load_latency_seconds: Measures load latency (plugin_name).


Provider Metrics (e.g., from ai_provider.py, claude_provider.py):

llm_calls_total: Tracks API calls (model).
llm_errors_total: Tracks errors (model, error_type).
llm_latency_seconds: Measures call latency (model).
llm_tokens_input/output: Tracks token usage (model).
llm_cost_total: Tracks estimated costs (model).
llm_provider_health: Tracks provider health (1=healthy, 0=unhealthy, provider).



Sample Prometheus Query:
rate(llm_plugin_loads_total{plugin_name="ai_provider"}[5m])

Queries the rate of plugin loads for ai_provider over 5 minutes.
OpenTelemetry Tracing

Traces are generated for plugin loads, reloads, provider calls, and health checks.
Attributes include run_id, provider_name, model, status, and error (if applicable).
Export traces to an OTLP endpoint (e.g., Jaeger, Zipkin) or console for development.

Sample Trace Structure:
Span: openai_call
  Attributes:
    - provider: openai
    - model: gpt-3.5-turbo
    - run_id: <uuid>
    - status: success
    - input_tokens: 10
    - output_tokens: 20

Alerting

Critical failures (e.g., hash mismatches, contract violations) trigger alerts to ALERT_ENDPOINT.
Example alert payload:{
  "message": "Plugin ai_provider.py failed integrity check",
  "severity": "error"
}




Hot Reload & Recovery

File Watching: Uses watchdog to monitor PLUGIN_DIR for file changes (create, modify, delete, move).
Atomic Reloads: Reloads update the registry atomically, skipping unhealthy plugins.
Zero Downtime: Active calls continue using the old registry until the reload completes.
Recovery: Unhealthy plugins are marked as such (PLUGIN_HEALTH=0) and skipped on reload.


Error Handling & Health

Error Tracking: Errors are logged with structured metadata (run_id, provider_name, error_type) and increment llm_plugin_errors_total or llm_errors_total.
Health Checks: Providers implement health_check to verify API/server availability. Results update llm_provider_health.
Circuit Breaker: Providers use a circuit breaker to disable calls after repeated failures (configurable threshold, e.g., 5 failures, 60s recovery).
Alerts: Critical errors (e.g., integrity failures) trigger webhook alerts.


Provider Capabilities
Supported providers and their capabilities:



Provider
Models
Streaming
Health Check
Cost Tracking
Hooks
Custom Endpoints



OpenAI
gpt-3.5-turbo, gpt-4, gpt-4o
Yes
Yes
Yes
Yes
Yes


Claude
claude-3-opus, claude-3-sonnet, claude-3-haiku, claude-3.5-sonnet, claude-3.5-haiku
Yes
Yes
Yes
Yes
Yes


Gemini
gemini-2.5-pro, gemini-2.5-flash
Yes
Yes
Yes
Yes
Yes


Grok
grok-4, grok-3, grok-3-mini
Yes
Yes
Yes
Yes
Yes


Local
llama2, mistral (Ollama)
Yes
Yes
Optional
Yes
Yes


All providers implement the LLMProvider interface and support async calls, PII scrubbing, and observability.

Production Best Practices

Enable Hash Verification: Always set HASH_MANIFEST to enforce integrity checks. Store the manifest in a secure system (e.g., AWS KMS, HashiCorp Vault).
Sandbox Plugins: Run untrusted plugins in isolated containers or subprocesses to prevent malicious code execution.
Centralized Configuration: Use llm_plugin_config.yaml or environment variables (LLM_PLUGIN_*) for consistent settings.
Secure API Keys: Store keys in a secrets manager (e.g., AWS Secrets Manager) and scrub all logs/traces.
Monitoring Setup:
Deploy Prometheus and Grafana for metrics visualization.
Use Jaeger or Zipkin for OpenTelemetry traces.
Configure Alertmanager for webhook alerts.


Backup Strategy: Regularly back up PLUGIN_DIR and the hash manifest to recover from accidental deletions.
Audit Logging: Enable structured logging with run_id and provider details for debugging and compliance.


Test Coverage
The system includes comprehensive unit, integration, and E2E tests:

Unit Tests (test_*_provider.py):

Validate provider contract (get_provider, call, health_check).
Test success/failure cases, streaming, circuit breakers, PII scrubbing, and cost estimation.
Use property-based fuzzing (hypothesis) to ensure robustness against malformed inputs.


Integration Tests (test_llm_plugin_manager.py):

Test plugin loading, reloading, integrity checks, and concurrency safety.
Simulate file system events and error conditions.


E2E Integration Tests (test_e2e_integration.py):

Test full workflow: plugin loading, provider calls (streaming/non-streaming), integrity failures, hot-reloading, circuit breakers, and observability.
Mock API responses to simulate real-world interactions.



Run tests with:
python -m unittest discover tests


Operational Procedures

Reload Plugins:

Add, edit, or remove .py files in PLUGIN_DIR. The manager auto-reloads if AUTO_RELOAD=true.
Manually trigger reload: await manager.reload().


Health Monitoring:

Scrape Prometheus endpoint for llm_plugin_health and llm_provider_health.
Query manager.get_provider(name).health_check() for provider status.


Debugging Errors:

Check logs for run_id, provider_name, and error_type.
Inspect llm_plugin_errors_total and llm_errors_total metrics.
Review OpenTelemetry traces for detailed execution paths.


Recovery:

Reset circuit breakers: manager.get_provider(name).reset_circuit().
Restore corrupted plugins from backups and update the hash manifest.
Restart the manager if persistent issues occur.




Development Guide
Running Locally

Clone the Repository:
git clone <repository-url>
cd llm-plugin-manager


Install Dependencies:
pip install -r requirements.txt

Required: dynaconf, prometheus_client, opentelemetry-sdk, opentelemetry-exporter-otlp-proto-grpc, watchdog, aiohttp, anthropic, google-generativeai, tiktoken.

Configure Environment:

Create llm_plugin_config.yaml:PLUGIN_DIR: "llm_providers"
AUTO_RELOAD: true
ALERT_ENDPOINT: "http://alertmanager:9093/api/v2/alerts"
OTLP_ENDPOINT: "http://otel-collector:4317"
HASH_MANIFEST: "plugin_hash_manifest.json"


Or set environment variables:export LLM_PLUGIN_PLUGIN_DIR="llm_providers"
export LLM_PLUGIN_AUTO_RELOAD="true"
export LLM_PLUGIN_ALERT_ENDPOINT="http://alertmanager:9093/api/v2/alerts"
export LLM_PLUGIN_OTLP_ENDPOINT="http://otel-collector:4317"
export LLM_PLUGIN_HASH_MANIFEST="plugin_hash_manifest.json"
export OPENAI_API_KEY="your-openai-key"
export CLAUDE_API_KEY="your-claude-key"
export GEMINI_API_KEY="your-gemini-key"
export GROK_API_KEY="your-grok-key"




Run the Manager:
from main.llm_plugin_manager import LLMPluginManager
import asyncio

async def main():
    manager = LLMPluginManager()
    await manager._scan_and_load_plugins_on_init()
    print(manager.list_providers())
    provider = manager.get_provider("openai")
    result = await provider.call("Hello, world!", "gpt-3.5-turbo")
    print(result["content"])

asyncio.run(main())


Run Tests:
python -m unittest discover tests


Local Observability Stack:

Use Docker Compose to set up Prometheus, Grafana, and Jaeger:version: '3'
services:
  prometheus:
    image: prom/prometheus:latest
    ports:
      - "9090:9090"
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml
  grafana:
    image: grafana/grafana:latest
    ports:
      - "3000:3000"
  jaeger:
    image: jaegertracing/all-in-one:latest
    ports:
      - "16686:16686"
      - "4317:4317"


Configure prometheus.yml to scrape metrics from your app (default port: 8000 for FastAPI providers).




License
Proprietary Software
This software is proprietary technology owned by Novatrax Labs LLC All rights reserved. Unauthorized use, reproduction, distribution, or modification of this software, in whole or in part, is strictly prohibited without prior written consent from Novatrax Labs LLC
For licensing inquiries, please contact Novatrax Labs LLC at [insert contact information].