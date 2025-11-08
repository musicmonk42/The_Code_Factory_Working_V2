LLM Plugin Manager Technical Documentation

Proprietary Software of Unexpected Innovations Inc.

This document provides detailed technical specifications for the LLM Plugin Manager, a production-grade system for managing multiple Large Language Model (LLM) providers with dynamic loading, hot-reloading, security, observability, and robust error handling. This software is proprietary technology owned by Unexpected Innovations Inc. Unauthorized use, reproduction, distribution, or modification is strictly prohibited without prior written consent.

Intended Audience: Internal developers and system operators at Unexpected Innovations Inc.Contact: For inquiries, contact \[insert contact information].



Table of Contents



Overview

Architecture

Provider Specifications

Configuration

Security

Observability

Error Handling and Recovery

Testing

Operational Procedures

Development Guide

License





Overview

The LLM Plugin Manager is a Python-based system designed to orchestrate multiple LLM providers (e.g., OpenAI, Claude, Gemini, Grok, local models like Ollama) through a centralized manager. It supports dynamic plugin discovery, hot-reloading, SHA-256 integrity checks, Prometheus metrics, OpenTelemetry tracing, and robust error isolation. The system is built for production environments, ensuring scalability, security, and observability.

Key Features:



Dynamic Plugin Discovery: Loads provider plugins from a configurable directory (PLUGIN\_DIR) without requiring restarts.

Hot Reloading: Automatically reloads plugins on file changes (create, modify, delete, move) using watchdog.

Security: Enforces SHA-256 hash verification and PII scrubbing for inputs/outputs.

Observability: Instruments operations with Prometheus metrics and OpenTelemetry traces.

Concurrency Safety: Uses asyncio.Lock for thread-safe plugin loading and registry updates.

Error Handling: Isolates plugin errors, tracks health, and sends alerts for critical failures.

Extensibility: Supports advanced provider features like streaming, custom endpoints, and hooks.





Architecture

Core Components



LLMPluginManager (llm\_plugin\_manager.py):



Role: Central orchestrator that scans PLUGIN\_DIR, validates plugins, maintains a provider registry, and handles hot-reloading.

Key Methods:

\_\_init\_\_: Initializes the manager, sets up the registry, and starts the file watcher if AUTO\_RELOAD is enabled.

\_scan\_and\_load\_plugins: Scans PLUGIN\_DIR for .py files, verifies hashes, and loads providers.

reload: Triggers an atomic reload of all plugins.

get\_provider(name): Retrieves a provider by name from the registry.

list\_providers: Returns a list of loaded provider names.

close: Stops the file watcher and cleans up resources.





Concurrency: Uses asyncio.Lock to ensure thread-safe registry updates.

File Watching: Leverages watchdog.observers.Observer and a custom PluginEventHandler to monitor PLUGIN\_DIR.





Provider Plugins:



Python modules (ai\_provider.py, claude\_provider.py, gemini\_provider.py, grok\_provider.py, local\_provider.py) implementing the LLMProvider interface.

Each defines a get\_provider() function returning an instance of the provider class.





Registry:



A dictionary mapping provider names (e.g., openai, claude) to their instances, updated atomically during loads/reloads.







Lifecycle



Initialization:



Scans PLUGIN\_DIR for .py files.

Validates each file’s SHA-256 hash against plugin\_hash\_manifest.json (if HASH\_MANIFEST is set).

Loads valid plugins via importlib.util and registers their providers.





Runtime:



Applications access providers via manager.get\_provider(name).call(prompt, model, stream).

Providers handle API calls, streaming, and health checks.





Hot-Reload:



File changes trigger PluginEventHandler, scheduling an async reload.

The registry is updated atomically, preserving active calls.





Shutdown:



Calls manager.close() to stop the file watcher and release resources.







Diagram

+-------------------+

| LLMPluginManager  |

| - Registry        |

| - File Watcher    |

| - Lock            |

+-------------------+

&nbsp;        |

&nbsp;        v

\[Scans PLUGIN\_DIR]

&nbsp;        |

&nbsp;        v

+------------------+    ...    +------------------+

|  ai\_provider.py  |  ... ...  | local\_provider.py |

|  (OpenAI)        |           | (Ollama)         |

+------------------+           +------------------+

&nbsp;        |                             |

&nbsp;        v                             v

&nbsp;  \[get\_provider()]             \[get\_provider()]

&nbsp;        |                             |

&nbsp;        v                             v

+------------------+           +------------------+

| OpenAIProvider   |           | LocalProvider    |

| - call()         |           | - call()         |

| - health\_check() |           | - health\_check() |

+------------------+           +------------------+

&nbsp;        |                             |

&nbsp;        v                             v

&nbsp;      Registry of Providers





Provider Specifications

Each provider implements the LLMProvider interface (from docgen\_llm\_call.py) and supports specific models, APIs, and features.

Common Features



Interface:



name: str: Unique identifier (e.g., openai, claude).

async call(prompt: str, model: str, stream: bool = False) -> Union\[Dict\[str, Any], AsyncGenerator\[str, None]]: Executes LLM calls, returning a dict (non-streaming) or generator (streaming).

async health\_check() -> bool: Verifies provider availability.

add\_pre\_hook, add\_post\_hook: Adds pre/post-processing hooks for prompts/responses.

register\_custom\_endpoint, register\_custom\_headers: Configures custom API endpoints/headers.





Security: Scrubs inputs/outputs for PII (API keys, emails, credit cards, SSNs) using regex patterns.



Observability: Records Prometheus metrics (llm\_calls\_total, llm\_errors\_total, llm\_latency\_seconds, llm\_tokens\_input/output, llm\_cost\_total, llm\_provider\_health) and OpenTelemetry traces.



Reliability: Uses a CircuitBreaker (5 failures, 60s recovery) to disable calls during repeated failures.



Cost Tracking: Estimates costs based on token usage and model-specific pricing.





Provider Details







Provider

File

Models

API Endpoint

Cost (Input/Output per 1K Tokens)

Token Counting







OpenAI

ai\_provider.py

gpt-3.5-turbo, gpt-4, gpt-4o

https://api.openai.com/v1

$0.0005/$0.0015 (gpt-3.5), $0.005/$0.015 (gpt-4o), $0.03/$0.06 (gpt-4)

tiktoken (cl100k\_base)





Claude

claude\_provider.py

claude-3-opus, claude-3-sonnet, claude-3-haiku, claude-3.5-sonnet, claude-3.5-haiku

https://api.anthropic.com/v1

$0.015/$0.075 (opus), $0.003/$0.015 (sonnet), $0.00025/$0.00125 (haiku)

Anthropic’s count\_tokens or word count





Gemini

gemini\_provider.py

gemini-2.5-pro, gemini-2.5-flash

Google Gemini API

$1.25e-6/$10e-6 (pro), $0.3e-6/$2.5e-6 (flash)

Gemini’s count\_tokens\_async or char-based





Grok

grok\_provider.py

grok-4, grok-3, grok-3-mini

xAI Grok API

$3.00/$15.00 per 1M tokens

tiktoken (cl100k\_base)





Local

local\_provider.py

llama2, mistral (Ollama)

http://localhost:11434/api/generate

$0 (configurable)

len(text) // 4





Notes:



All providers support streaming, health checks, hooks, and custom endpoints.

API keys are required (OPENAI\_API\_KEY, CLAUDE\_API\_KEY, GEMINI\_API\_KEY, GROK\_API\_KEY) and must be stored securely.



Example Provider Call

provider = manager.get\_provider("openai")

result = await provider.call("Hello, world!", "gpt-3.5-turbo")

\# Returns: {"content": "Response", "model": "gpt-3.5-turbo", "run\_id": "<uuid>", ...}





Configuration

The system uses Dynaconf for configuration via llm\_plugin\_config.yaml or environment variables (LLM\_PLUGIN\_\*).

Configuration Options







Key

Type

Default

Description







PLUGIN\_DIR

str

Required

Directory containing provider .py files.





AUTO\_RELOAD

bool

false

Enables hot-reloading of plugins on file changes.





ALERT\_ENDPOINT

str

""

Webhook URL for critical alerts (e.g., integrity failures).





OTLP\_ENDPOINT

str

http://otel-collector:4317

OpenTelemetry endpoint for traces.





HASH\_MANIFEST

str

""

Path to plugin\_hash\_manifest.json for integrity checks.





Example llm\_plugin\_config.yaml

PLUGIN\_DIR: "llm\_providers"

AUTO\_RELOAD: true

ALERT\_ENDPOINT: "http://alertmanager:9093/api/v2/alerts"

OTLP\_ENDPOINT: "http://otel-collector:4317"

HASH\_MANIFEST: "plugin\_hash\_manifest.json"



Environment Variables

export LLM\_PLUGIN\_PLUGIN\_DIR="llm\_providers"

export LLM\_PLUGIN\_AUTO\_RELOAD="true"

export LLM\_PLUGIN\_ALERT\_ENDPOINT="http://alertmanager:9093/api/v2/alerts"

export LLM\_PLUGIN\_OTLP\_ENDPOINT="http://otel-collector:4317"

export LLM\_PLUGIN\_HASH\_MANIFEST="plugin\_hash\_manifest.json"

export OPENAI\_API\_KEY="your-openai-key"

export CLAUDE\_API\_KEY="your-claude-key"

export GEMINI\_API\_KEY="your-gemini-key"

export GROK\_API\_KEY="your-grok-key"





Security

SHA-256 Integrity Checks



Plugins are validated against a plugin\_hash\_manifest.json file containing SHA-256 hashes:{

&nbsp; "ai\_provider.py": "a1b2c3d4...",

&nbsp; "claude\_provider.py": "e5f6a7b8..."

}





If a plugin’s hash mismatches or is missing, it’s rejected, and an alert is sent to ALERT\_ENDPOINT.

Recommendation: Store the manifest in a secure system (e.g., AWS KMS, HashiCorp Vault).



PII Scrubbing



All providers scrub inputs/outputs using regex patterns:

API keys/tokens: r'(?i)(api\[-\_]?key|secret|token)\\s\*\[:=]\\s\*\["\\']?\[a-zA-Z0-9\_\\-]{20,}\["\\']?'

Passwords: r'(?i)password\\s\*\[:=]\\s\*\["\\']?.+?\["\\']?'

Emails: r'\\b\[A-Za-z0-9.\_%+-]+@\[A-Za-z0-9.-]+\\.\[A-Z|a-z]{2,}\\b'

SSNs: r'\\b(?:\\d{3}-?\\d{2}-?\\d{4})\\b'

Credit cards: Matches common formats (Visa, Mastercard, etc.).





Scrubbed data is replaced with \[REDACTED] in logs and traces.



Sandboxing



Plugins run in-process by default, posing a risk for untrusted code.

Recommendation: Deploy untrusted plugins in Docker containers or subprocesses with restricted permissions:# docker-compose.yml

services:

&nbsp; plugin:

&nbsp;   image: python:3.11-slim

&nbsp;   volumes:

&nbsp;     - ./llm\_providers:/app/llm\_providers

&nbsp;   network\_mode: none









Observability

Prometheus Metrics



Plugin Metrics (llm\_plugin\_manager.py):



llm\_plugin\_loads\_total{plugin\_name}: Plugin load attempts.

llm\_plugin\_reloads\_total{plugin\_name}: Reload events.

llm\_plugin\_errors\_total{plugin\_name, error\_type}: Errors (e.g., integrity\_failure, contract\_violation).

llm\_plugin\_health{plugin\_name}: Health status (1=healthy, 0=unhealthy).

llm\_plugin\_load\_latency\_seconds{plugin\_name}: Load latency.





Provider Metrics (e.g., ai\_provider.py):



llm\_calls\_total{model}: API calls.

llm\_errors\_total{model, error\_type}: Errors (e.g., ClientResponseError).

llm\_latency\_seconds{model}: Call latency.

llm\_tokens\_input/output{model}: Token usage.

llm\_cost\_total{model}: Estimated costs.

llm\_provider\_health{provider}: Provider health.







Sample Query:

rate(llm\_calls\_total{model="gpt-3.5-turbo"}\[5m])



OpenTelemetry Tracing



Traces cover plugin loads, reloads, provider calls, and health checks.

Attributes: run\_id, provider\_name, model, status, error, input\_tokens, output\_tokens.

Export to OTLP\_ENDPOINT (e.g., Jaeger at http://otel-collector:4317).



Sample Trace:

Span: claude\_call

&nbsp; Attributes:

&nbsp;   - provider: claude

&nbsp;   - model: claude-3.5-sonnet

&nbsp;   - run\_id: <uuid>

&nbsp;   - status: success

&nbsp;   - input\_tokens: 15

&nbsp;   - output\_tokens: 25



Alerting



Critical failures (e.g., hash mismatches, contract violations) send alerts to ALERT\_ENDPOINT.

Payload:{

&nbsp; "message": "Plugin grok\_provider.py failed integrity check",

&nbsp; "severity": "error"

}









Error Handling and Recovery



Error Isolation: Faulty plugins are marked unhealthy (PLUGIN\_HEALTH=0) and excluded from the registry without affecting others.

Circuit Breaker: Providers disable calls after 5 failures, recovering after 60s or manual reset (reset\_circuit()).

Structured Logging: Errors include run\_id, provider\_name, and error\_type for traceability.

Recovery:

Reset circuit breakers: manager.get\_provider(name).reset\_circuit().

Restore plugins from backups and update the hash manifest.

Restart the manager for persistent issues.









Testing

The system includes unit, integration, and E2E tests to ensure reliability and robustness.

Test Suite



Unit Tests (test\_\*\_provider.py):



Validate provider contract (get\_provider, call, health\_check).

Test success/failure cases, streaming, PII scrubbing, cost estimation, and circuit breakers.

Use hypothesis for fuzz testing against malformed inputs.





Integration Tests (test\_llm\_plugin\_manager.py):



Test plugin loading, reloading, integrity checks, and concurrency safety.

Simulate file system events and errors.





E2E Integration Tests (test\_e2e\_integration.py):



Test full workflow: plugin loading, provider calls (streaming/non-streaming), integrity failures, hot-reloading, circuit breakers, and observability.

Mock API responses for realistic simulation.







Running Tests

python -m unittest discover tests



Dependencies: unittest, hypothesis, prometheus\_client, opentelemetry-sdk.



Operational Procedures

Deployment



Set Up Environment:



Configure llm\_plugin\_config.yaml or environment variables.

Store API keys in a secrets manager (e.g., AWS Secrets Manager).

Place provider .py files in PLUGIN\_DIR.





Run the Manager:

from main.llm\_plugin\_manager import LLMPluginManager

import asyncio



async def main():

&nbsp;   manager = LLMPluginManager()

&nbsp;   await manager.\_scan\_and\_load\_plugins\_on\_init()

&nbsp;   provider = manager.get\_provider("claude")

&nbsp;   result = await provider.call("Test prompt", "claude-3.5-sonnet")

&nbsp;   print(result\["content"])



asyncio.run(main())





Observability Stack:



Deploy Prometheus, Grafana, and Jaeger:version: '3'

services:

&nbsp; prometheus:

&nbsp;   image: prom/prometheus:latest

&nbsp;   ports:

&nbsp;     - "9090:9090"

&nbsp;   volumes:

&nbsp;     - ./prometheus.yml:/etc/prometheus/prometheus.yml

&nbsp; grafana:

&nbsp;   image: grafana/grafana:latest

&nbsp;   ports:

&nbsp;     - "3000:3000"

&nbsp; jaeger:

&nbsp;   image: jaegertracing/all-in-one:latest

&nbsp;   ports:

&nbsp;     - "16686:16686"

&nbsp;     - "4317:4317"





Configure prometheus.yml:scrape\_configs:

&nbsp; - job\_name: 'llm\_plugin\_manager'

&nbsp;   static\_configs:

&nbsp;     - targets: \['host.docker.internal:8000']











Monitoring



Health Checks:



Query llm\_plugin\_health and llm\_provider\_health via Prometheus.

Call manager.get\_provider(name).health\_check() for real-time status.





Metrics:



Monitor llm\_plugin\_errors\_total and llm\_errors\_total for error rates.

Track llm\_latency\_seconds for performance.





Traces:



Use Jaeger (port 16686) to inspect traces with run\_id and provider\_name.







Debugging



Logs: Check structured logs for run\_id, provider\_name, and error\_type.

Alerts: Monitor ALERT\_ENDPOINT for critical failures.

Traces: Filter by run\_id in Jaeger to trace specific calls.



Recovery



Circuit Breaker Reset: manager.get\_provider(name).reset\_circuit().

Plugin Restore: Copy backup .py files to PLUGIN\_DIR and update plugin\_hash\_manifest.json.

Manager Restart: Stop and restart the manager process.





Development Guide

Setup



Clone Repository:

git clone <repository-url>

cd llm-plugin-manager





Install Dependencies:

pip install -r requirements.txt



Required: dynaconf, prometheus\_client, opentelemetry-sdk, opentelemetry-exporter-otlp-proto-grpc, watchdog, aiohttp, anthropic, google-generativeai, tiktoken.



Configure:



Create llm\_plugin\_config.yaml (see Configuration).

Set API keys in a secrets manager or environment variables.





Run Tests:

python -m unittest discover tests







Creating a Provider



Create a .py file in PLUGIN\_DIR (e.g., my\_provider.py):

from typing import Union, Dict, Any, AsyncGenerator



class MyProvider:

&nbsp;   def \_\_init\_\_(self):

&nbsp;       self.name = "myprovider"

&nbsp;   

&nbsp;   async def call(self, prompt: str, model: str, stream: bool = False) -> Union\[Dict\[str, Any], AsyncGenerator\[str, None]]:

&nbsp;       if stream:

&nbsp;           async def stream\_response():

&nbsp;               yield "chunk1"

&nbsp;               yield "chunk2"

&nbsp;           return stream\_response()

&nbsp;       return {"content": f"Response to {prompt}", "model": model}

&nbsp;   

&nbsp;   async def health\_check(self) -> bool:

&nbsp;       return True



def get\_provider():

&nbsp;   return MyProvider()





Update plugin\_hash\_manifest.json with the file’s SHA-256 hash:

python -c "import hashlib; print(hashlib.sha256(open('llm\_providers/my\_provider.py', 'rb').read()).hexdigest())"





Test the provider:

from main.llm\_plugin\_manager import LLMPluginManager

import asyncio



async def main():

&nbsp;   manager = LLMPluginManager()

&nbsp;   await manager.\_scan\_and\_load\_plugins\_on\_init()

&nbsp;   provider = manager.get\_provider("myprovider")

&nbsp;   result = await provider.call("Test", "mymodel")

&nbsp;   print(result\["content"])



asyncio.run(main())









License

Proprietary Software

This software is proprietary technology owned by Unexpected Innovations Inc. All rights reserved. Unauthorized use, reproduction, distribution, or modification of this software, in whole or in part, is strictly prohibited without prior written consent from Unexpected Innovations Inc.

For licensing inquiries, please contact Unexpected Innovations Inc. at \[insert contact information].

