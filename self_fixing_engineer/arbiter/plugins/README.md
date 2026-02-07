<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

\# Self-Fixing Engineer: Multimodal Plugin System



\## Overview

\# Self-Fixing Engineer: Multimodal Plugin System



\## Overview



This project provides a \*\*robust, extensible, and production-ready multimodal plugin framework\*\* supporting image, audio, video, and text processing. It powers AI-driven analysis pipelines with strong observability, security, and compliance features.



\- \*\*Adapters included:\*\* OpenAI, Anthropic, Gemini, Ollama (local), and default/mock providers.

\- \*\*Features:\*\* Plugin registry, Pydantic config, Prometheus metrics, audit logging, circuit breaker, async-first processing, sandboxing (optional), and more.

\- \*\*Use Cases:\*\* AI-powered document analysis, media pipelines, LLM orchestration, and research/demos.



---



\## Table of Contents



\- \[Quickstart](#quickstart)

\- \[Architecture](#architecture)

\- \[Configuration](#configuration)

\- \[Extending the System](#extending-the-system)

\- \[Adapters](#adapters)

\- \[Sandboxing \& Security](#sandboxing--security)

\- \[Observability (Metrics/Audit)](#observability-metricsaudit)

\- \[Testing](#testing)

\- \[Productionizing](#productionizing)

\- \[Demo Usage](#demo-usage)

\- \[Troubleshooting](#troubleshooting)

\- \[Contributing](#contributing)



---



\## Quickstart



\### 1. Prerequisites



\- Python 3.9+ recommended.

\- \[Poetry](https://python-poetry.org/) or `pip` for dependency management.

\- (Optional) \[Docker](https://www.docker.com/) if using sandboxing.

\- (Optional) \[Redis](https://redis.io/) for caching.

\- (Optional) Prometheus for metrics.



\### 2. Installation



```bash

git clone https://github.com/YOUR\_ORG/self\_fixing\_engineer.git

cd self\_fixing\_engineer

poetry install

\# Or use pip:

pip install -r requirements.txt

```



\### 3. Configuration



\- Copy and edit the sample config:



```bash

cp arbiter/plugins/multi\_modal\_config.sample.yaml arbiter/plugins/multi\_modal\_config.yaml

\# Or set environment variables as documented below.

```



\### 4. Run Example



```bash

\# Start a Python shell or use the provided script

python -m arbiter.plugins.multi\_modal\_plugin

```



---



\## Architecture



```

arbiter/plugins/

├── multimodal/

│   ├── interface.py           # Abstract interfaces for processors

│   ├── providers/

│   │   ├── default\_multimodal\_providers.py  # Mock/default providers

│   │   └── ...                # Place custom providers here

│   └── ...

├── llm\_client.py              # Unified LLM async client (OpenAI, Anthropic, Gemini, Ollama)

├── openai\_adapter.py

├── anthropic\_adapter.py

├── gemini\_adapter.py

├── ollama\_adapter.py

├── multi\_modal\_config.py      # Pydantic config models

├── multi\_modal\_plugin.py      # Main orchestration logic

└── tests/                     # Unit and integration tests

```



\- \*\*PluginRegistry\*\*: Central registry for all modality providers.

\- \*\*ProcessingResult\*\*: Standard return type for all processors.

\- \*\*Async-first\*\*: Most processing is async, with sync fallback via thread pools.



---



\## Configuration



\- The system uses a Pydantic model (`MultiModalConfig`) for strict validation.

\- \*\*YAML config\*\* and \*\*environment variables\*\* are supported.

\- Example YAML:



```yaml

image\_processing:

&nbsp; enabled: true

&nbsp; default\_provider: default

&nbsp; provider\_config: {}

audio\_processing:

&nbsp; enabled: true

&nbsp; default\_provider: default

&nbsp; provider\_config: {}

text\_processing:

&nbsp; enabled: true

&nbsp; default\_provider: default

&nbsp; provider\_config: {}

video\_processing:

&nbsp; enabled: false

&nbsp; default\_provider: default

&nbsp; provider\_config: {}

security\_config:

&nbsp; sandbox\_enabled: false

&nbsp; mask\_pii\_in\_logs: true

&nbsp; input\_validation\_rules: {}

&nbsp; output\_validation\_rules: {}

&nbsp; pii\_patterns: \[]

audit\_log\_config:

&nbsp; enabled: false

metrics\_config:

&nbsp; enabled: false

cache\_config:

&nbsp; enabled: false

circuit\_breaker\_config:

&nbsp; enabled: true

&nbsp; threshold: 3

&nbsp; timeout\_seconds: 30

&nbsp; modalities: \["image", "audio", "text"]

user\_id\_for\_auditing: "dev\_demo"

```



\- \*\*Env vars\*\* override YAML. See `multi\_modal\_config.py` for all supported variables.



---



\## Extending the System



\### Adding a New Provider



1\. \*\*Implement the interface\*\* (e.g., `ImageProcessor`) in `arbiter/plugins/multimodal/providers/your\_provider.py`:



```python

from arbiter.plugins.multimodal.interface import ImageProcessor, ProcessingResult



class MyCustomImageProcessor(ImageProcessor):

&nbsp;   def \_\_init\_\_(self, config):

&nbsp;       # Validate config as needed

&nbsp;       ...



&nbsp;   async def process(self, image\_data: bytes, \*\*kwargs) -> ProcessingResult:

&nbsp;       # Your image processing logic here

&nbsp;       ...

```



2\. \*\*Register the provider\*\* (usually at startup):



```python

from arbiter.plugins.multimodal.providers.default\_multimodal\_providers import PluginRegistry

PluginRegistry.register\_processor("image", "my\_custom\_provider", MyCustomImageProcessor)

```



3\. \*\*Update config\*\* to select your provider:



```yaml

image\_processing:

&nbsp; enabled: true

&nbsp; default\_provider: my\_custom\_provider

&nbsp; provider\_config: {...}

```



---



\## Adapters



\- \*\*OpenAIAdapter:\*\* For GPT-4o, GPT-3.5, etc. Needs `OPENAI\_API\_KEY`.

\- \*\*AnthropicAdapter:\*\* For Claude models. Needs `ANTHROPIC\_API\_KEY`.

\- \*\*GeminiAPIAdapter:\*\* For Google Gemini. Needs `GEMINI\_API\_KEY`.

\- \*\*OllamaAdapter:\*\* For local LLMs via Ollama.

\- \*\*Default Providers:\*\* Mock implementations for safe demos or local dev.



Config example for OpenAI:



```yaml

text\_processing:

&nbsp; enabled: true

&nbsp; default\_provider: openai

&nbsp; provider\_config:

&nbsp;   OPENAI\_API\_KEY: sk-...

&nbsp;   LLM\_MODEL: gpt-4o-mini

```



---



\## Sandboxing \& Security



\- \*\*Sandboxing:\*\* Optional Docker-based execution for untrusted code.  

&nbsp; - Enable via `security\_config.sandbox\_enabled: true`.

&nbsp; - Requires Docker and properly configured images.

\- \*\*PII Masking:\*\* Regex-based PII masking for logs and inputs.

\- \*\*Compliance:\*\* Map modalities to compliance controls in `compliance\_config`.



---



\## Observability (Metrics/Audit)



\- \*\*Prometheus metrics\*\*: Exposed when enabled in config (default port 9090).

\- \*\*Audit logs\*\*: Configurable to console or file, with PII masking.

\- \*\*Circuit breaker\*\*: Monitors provider failures to prevent cascading outages.



---



\## Testing



\- All core components are testable and mockable.

\- \*\*Run tests:\*\*



```bash

pytest arbiter/plugins/tests/

```



\- \*\*Add tests\*\* for new providers in the `tests/` directory.



---



\## Productionizing



\- \*\*Harden Sandboxing\*\*: Use seccomp/AppArmor and non-root containers.

\- \*\*Secure Secrets\*\*: Never check API keys into git.

\- \*\*Resource Cleanup\*\*: Ensure all async contexts are exited and resources released.

\- \*\*Monitoring\*\*: Point Prometheus and log collectors to the correct endpoints.

\- \*\*Scaling\*\*: Use async workers/process pools for high throughput.



---



\## Demo Usage



\- Enable only mock/default providers for safe, fast demos.

\- Use small, example inputs to show the pipeline.

\- Provide a minimal demo config (see above).

\- Disable or mock cache, metrics, and audit if not required for demo.



---



\## Troubleshooting



\- \*\*Config validation errors\*\*: Check YAML and env var types.

\- \*\*Provider not found\*\*: Confirm provider is registered and config matches.

\- \*\*Circuit breaker trips\*\*: Check provider logs and increase thresholds cautiously.

\- \*\*Sandbox errors\*\*: Ensure Docker is installed and configuration is correct.



---



\## Contributing



\- Fork, branch, and submit PRs with clear descriptions.

\- Add/extend tests for new features.

\- Follow PEP8 and use type hints.

\- See \[CONTRIBUTING.md](CONTRIBUTING.md) for details.



---



\## License



MIT (or your project license here)



---



\## Contact / Support



\- Slack: #multimodal-dev

\- Issues: \[GitHub Issues](https://github.com/YOUR\_ORG/self\_fixing\_engineer/issues)

\- Maintainers: @yourteam



This project provides a \*\*robust, extensible, and production-ready multimodal plugin framework\*\* supporting image, audio, video, and text processing. It powers AI-driven analysis pipelines with strong observability, security, and compliance features.



\- \*\*Adapters included:\*\* OpenAI, Anthropic, Gemini, Ollama (local), and default/mock providers.

\- \*\*Features:\*\* Plugin registry, Pydantic config, Prometheus metrics, audit logging, circuit breaker, async-first processing, sandboxing (optional), and more.

\- \*\*Use Cases:\*\* AI-powered document analysis, media pipelines, LLM orchestration, and research/demos.



---



\## Table of Contents



\- \[Quickstart](#quickstart)

\- \[Architecture](#architecture)

\- \[Configuration](#configuration)

\- \[Extending the System](#extending-the-system)

\- \[Adapters](#adapters)

\- \[Sandboxing \& Security](#sandboxing--security)

\- \[Observability (Metrics/Audit)](#observability-metricsaudit)

\- \[Testing](#testing)

\- \[Productionizing](#productionizing)

\- \[Demo Usage](#demo-usage)

\- \[Troubleshooting](#troubleshooting)

\- \[Contributing](#contributing)



---



\## Quickstart



\### 1. Prerequisites



\- Python 3.9+ recommended.

\- \[Poetry](https://python-poetry.org/) or `pip` for dependency management.

\- (Optional) \[Docker](https://www.docker.com/) if using sandboxing.

\- (Optional) \[Redis](https://redis.io/) for caching.

\- (Optional) Prometheus for metrics.



\### 2. Installation



```bash

git clone https://github.com/YOUR\_ORG/self\_fixing\_engineer.git

cd self\_fixing\_engineer

poetry install

\# Or use pip:

pip install -r requirements.txt

```



\### 3. Configuration



\- Copy and edit the sample config:



```bash

cp arbiter/plugins/multi\_modal\_config.sample.yaml arbiter/plugins/multi\_modal\_config.yaml

\# Or set environment variables as documented below.

```



\### 4. Run Example



```bash

\# Start a Python shell or use the provided script

python -m arbiter.plugins.multi\_modal\_plugin

```



---



\## Architecture



```

arbiter/plugins/

├── multimodal/

│   ├── interface.py           # Abstract interfaces for processors

│   ├── providers/

│   │   ├── default\_multimodal\_providers.py  # Mock/default providers

│   │   └── ...                # Place custom providers here

│   └── ...

├── llm\_client.py              # Unified LLM async client (OpenAI, Anthropic, Gemini, Ollama)

├── openai\_adapter.py

├── anthropic\_adapter.py

├── gemini\_adapter.py

├── ollama\_adapter.py

├── multi\_modal\_config.py      # Pydantic config models

├── multi\_modal\_plugin.py      # Main orchestration logic

└── tests/                     # Unit and integration tests

```



\- \*\*PluginRegistry\*\*: Central registry for all modality providers.

\- \*\*ProcessingResult\*\*: Standard return type for all processors.

\- \*\*Async-first\*\*: Most processing is async, with sync fallback via thread pools.



---



\## Configuration



\- The system uses a Pydantic model (`MultiModalConfig`) for strict validation.

\- \*\*YAML config\*\* and \*\*environment variables\*\* are supported.

\- Example YAML:



```yaml

image\_processing:

&nbsp; enabled: true

&nbsp; default\_provider: default

&nbsp; provider\_config: {}

audio\_processing:

&nbsp; enabled: true

&nbsp; default\_provider: default

&nbsp; provider\_config: {}

text\_processing:

&nbsp; enabled: true

&nbsp; default\_provider: default

&nbsp; provider\_config: {}

video\_processing:

&nbsp; enabled: false

&nbsp; default\_provider: default

&nbsp; provider\_config: {}

security\_config:

&nbsp; sandbox\_enabled: false

&nbsp; mask\_pii\_in\_logs: true

&nbsp; input\_validation\_rules: {}

&nbsp; output\_validation\_rules: {}

&nbsp; pii\_patterns: \[]

audit\_log\_config:

&nbsp; enabled: false

metrics\_config:

&nbsp; enabled: false

cache\_config:

&nbsp; enabled: false

circuit\_breaker\_config:

&nbsp; enabled: true

&nbsp; threshold: 3

&nbsp; timeout\_seconds: 30

&nbsp; modalities: \["image", "audio", "text"]

user\_id\_for\_auditing: "dev\_demo"

```



\- \*\*Env vars\*\* override YAML. See `multi\_modal\_config.py` for all supported variables.



---



\## Extending the System



\### Adding a New Provider



1\. \*\*Implement the interface\*\* (e.g., `ImageProcessor`) in `arbiter/plugins/multimodal/providers/your\_provider.py`:



```python

from arbiter.plugins.multimodal.interface import ImageProcessor, ProcessingResult



class MyCustomImageProcessor(ImageProcessor):

&nbsp;   def \_\_init\_\_(self, config):

&nbsp;       # Validate config as needed

&nbsp;       ...



&nbsp;   async def process(self, image\_data: bytes, \*\*kwargs) -> ProcessingResult:

&nbsp;       # Your image processing logic here

&nbsp;       ...

```



2\. \*\*Register the provider\*\* (usually at startup):



```python

from arbiter.plugins.multimodal.providers.default\_multimodal\_providers import PluginRegistry

PluginRegistry.register\_processor("image", "my\_custom\_provider", MyCustomImageProcessor)

```



3\. \*\*Update config\*\* to select your provider:



```yaml

image\_processing:

&nbsp; enabled: true

&nbsp; default\_provider: my\_custom\_provider

&nbsp; provider\_config: {...}

```



---



\## Adapters



\- \*\*OpenAIAdapter:\*\* For GPT-4o, GPT-3.5, etc. Needs `OPENAI\_API\_KEY`.

\- \*\*AnthropicAdapter:\*\* For Claude models. Needs `ANTHROPIC\_API\_KEY`.

\- \*\*GeminiAPIAdapter:\*\* For Google Gemini. Needs `GEMINI\_API\_KEY`.

\- \*\*OllamaAdapter:\*\* For local LLMs via Ollama.

\- \*\*Default Providers:\*\* Mock implementations for safe demos or local dev.



Config example for OpenAI:



```yaml

text\_processing:

&nbsp; enabled: true

&nbsp; default\_provider: openai

&nbsp; provider\_config:

&nbsp;   OPENAI\_API\_KEY: sk-...

&nbsp;   LLM\_MODEL: gpt-4o-mini

```



---



\## Sandboxing \& Security



\- \*\*Sandboxing:\*\* Optional Docker-based execution for untrusted code.  

&nbsp; - Enable via `security\_config.sandbox\_enabled: true`.

&nbsp; - Requires Docker and properly configured images.

\- \*\*PII Masking:\*\* Regex-based PII masking for logs and inputs.

\- \*\*Compliance:\*\* Map modalities to compliance controls in `compliance\_config`.



---



\## Observability (Metrics/Audit)



\- \*\*Prometheus metrics\*\*: Exposed when enabled in config (default port 9090).

\- \*\*Audit logs\*\*: Configurable to console or file, with PII masking.

\- \*\*Circuit breaker\*\*: Monitors provider failures to prevent cascading outages.



---



\## Testing



\- All core components are testable and mockable.

\- \*\*Run tests:\*\*



```bash

pytest arbiter/plugins/tests/

```



\- \*\*Add tests\*\* for new providers in the `tests/` directory.



---



\## Productionizing



\- \*\*Harden Sandboxing\*\*: Use seccomp/AppArmor and non-root containers.

\- \*\*Secure Secrets\*\*: Never check API keys into git.

\- \*\*Resource Cleanup\*\*: Ensure all async contexts are exited and resources released.

\- \*\*Monitoring\*\*: Point Prometheus and log collectors to the correct endpoints.

\- \*\*Scaling\*\*: Use async workers/process pools for high throughput.



---



\## Demo Usage



\- Enable only mock/default providers for safe, fast demos.

\- Use small, example inputs to show the pipeline.

\- Provide a minimal demo config (see above).

\- Disable or mock cache, metrics, and audit if not required for demo.



---



\## Troubleshooting



\- \*\*Config validation errors\*\*: Check YAML and env var types.

\- \*\*Provider not found\*\*: Confirm provider is registered and config matches.

\- \*\*Circuit breaker trips\*\*: Check provider logs and increase thresholds cautiously.

\- \*\*Sandbox errors\*\*: Ensure Docker is installed and configuration is correct.



---



\## Contributing



\- Fork, branch, and submit PRs with clear descriptions.

\- Add/extend tests for new features.

\- Follow PEP8 and use type hints.

\- See \[CONTRIBUTING.md](CONTRIBUTING.md) for details.



---



\## License



MIT (or your project license here)



---



\## Contact / Support



\- Slack: #multimodal-dev

\- Issues: \[GitHub Issues](https://github.com/YOUR\_ORG/self\_fixing\_engineer/issues)

\- Maintainers: @yourteam



