<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

\# Intent Parser Module

\# Intent Parser Module



\## Overview



The `intent\_parser` module is a configurable, extensible framework for parsing, extracting, and clarifying software requirements and intent from various text and document sources. It is designed for use in modern code generation, requirements engineering, and software analysis workflows. The system leverages both rule-based and LLM-based extraction, supports multiple document formats and languages, and maintains a strong focus on security, configurability, and observability.



---



\## Key Features



\- \*\*Multi-format Parsing:\*\*  

&nbsp; Supports Markdown, reStructuredText (RST), plaintext, YAML, and PDF. Automatically detects format when configured.

\- \*\*Flexible Extraction Strategies:\*\*  

&nbsp; Regex-based and language-specific extraction patterns for features, constraints, and other requirement types.

\- \*\*LLM Integration:\*\*  

&nbsp; Supports LLM-powered ambiguity detection, clarification, and summarization using configurable providers (e.g., OpenAI, Anthropic).

\- \*\*Multi-language Support:\*\*  

&nbsp; Detects input language and applies language-specific extraction logic. Default and fallback language configuration is supported.

\- \*\*Feedback Loop:\*\*  

&nbsp; Collects and stores user feedback for continuous improvement.

\- \*\*Caching \& Performance:\*\*  

&nbsp; Caches expensive LLM calls and temporary artifacts for efficiency.

\- \*\*Security \& Compliance:\*\*  

&nbsp; Encrypted at-rest storage for sensitive data, configurable PII/secret redaction, and audit logging.

\- \*\*Metrics \& Observability:\*\*  

&nbsp; Exposes Prometheus metrics and supports OpenTelemetry tracing for all major operations.

\- \*\*Extensible Configuration:\*\*  

&nbsp; YAML-based configuration allows per-project customizations, custom extraction logic, and security settings.



---



\## Directory Structure



\- `intent\_parser.py` — Main parsing logic, strategy selection, and orchestration.

\- `intent\_parser.yaml` — Central configuration file for parser settings, extraction patterns, LLM settings, feedback, caching, and security.



---



\## How It Works



1\. \*\*Configuration Loading:\*\*  

&nbsp;  Loads and validates the YAML config file (`intent\_parser.yaml`), setting up parsing, extraction, language, and LLM parameters.

2\. \*\*Format Detection \& Parsing:\*\*  

&nbsp;  Detects the document format (if `auto`) and parses the content into logical sections using the appropriate strategy.

3\. \*\*Feature \& Constraint Extraction:\*\*  

&nbsp;  Applies global or language-specific regex patterns to extract features, constraints, and other relevant items.

4\. \*\*Ambiguity Detection:\*\*  

&nbsp;  Uses LLM-based or rule-based logic to identify ambiguous requirements or unclear parts in the text.

5\. \*\*Summarization:\*\*  

&nbsp;  Optionally summarizes extracted requirements using LLMs or truncation strategies.

6\. \*\*Feedback \& Caching:\*\*  

&nbsp;  Stores user feedback and caches results to improve future performance.

7\. \*\*Security \& Logging:\*\*  

&nbsp;  Redacts sensitive information, logs actions for audit, and monitors with metrics and tracing.



---



\## Typical Usage



\*\*Python API:\*\*

```python

from intent\_parser import IntentParser



parser = IntentParser(config\_path='intent\_parser.yaml')

results = await parser.parse(

&nbsp;   content="Your requirements text here",

&nbsp;   format\_hint="markdown",           # Optional: 'auto', 'markdown', 'rst', 'plaintext', 'yaml', 'pdf'

&nbsp;   file\_path="requirements.md",      # Optional: Path to a requirements file

&nbsp;   dry\_run=False,                    # If True, disables LLM calls for ambiguity detection

&nbsp;   user\_id="analyst\_42"              # Used for logging/metrics

)

```



\*\*Configuration:\*\*

\- Edit `intent\_parser.yaml` to set extraction patterns, supported formats, LLM provider/model, feedback and cache file locations, language support, and security/PII redaction rules.



---



\## Configuration (intent\_parser.yaml)



\- \*\*format\*\*: Default or auto-detected document format.

\- \*\*extraction\_patterns\*\*: Regex patterns for extracting features, constraints, etc.

\- \*\*llm\_config\*\*: LLM provider, model, API key environment variable, temperature, and token/seed configuration.

\- \*\*multi\_language\_support\*\*: Enable/disable, set default language, and language-specific extraction patterns.

\- \*\*custom\_extraction\_configs\*\*: Per-project overrides for extraction patterns or prompt templates.

\- \*\*security\_config\*\*: Enable/disable custom redaction, define PII/secret redaction regexes, and set detection sensitivity.



---



\## Security \& Compliance



\- \*\*Redaction:\*\* All sensitive or PII data can be redacted using configurable regexes.

\- \*\*Encryption:\*\* At-rest cache and feedback data can be encrypted (see code/config for details).

\- \*\*Audit Logging:\*\* All major actions and events are logged for traceability.

\- \*\*File Permissions:\*\* Ensure that sensitive files (e.g., feedback, cache) are only accessible to the intended user.



---



\## Observability



\- \*\*Metrics:\*\* Prometheus metrics for latency, extraction events, errors, LLM calls, cache hits, feedback, etc.

\- \*\*Tracing:\*\* OpenTelemetry tracing for end-to-end workflow observability.

\- \*\*Logging:\*\* All major actions, errors, and security events are logged with context.



---



\## Extending the System



\- \*\*Add a New Format:\*\* Implement a new `ParserStrategy` subclass.

\- \*\*Custom Extraction:\*\* Edit `intent\_parser.yaml` or add Python modules for project-specific patterns and logic.

\- \*\*Integrate a New LLM:\*\* Implement LLM client logic and update the configuration.

\- \*\*Add Metrics/Tracing:\*\* Instrument new flows as necessary using Prometheus and OpenTelemetry APIs.



---



\## Getting Started



1\. Install Python dependencies:  

&nbsp;  See imports in `intent\_parser.py` and install via `pip` or `requirements.txt`.  

&nbsp;  (e.g., `pip install pydantic prometheus\_client opentelemetry-sdk pdfplumber pytesseract Pillow transformers ...`)

2\. Configure `intent\_parser.yaml` as needed.

3\. Use the Python API or CLI to parse documents and extract requirements/intents.

4\. Monitor logs and metrics for health and results.



---



\## License



\[Specify your license here]



---



\## Contact



For questions or support, please contact the maintainers or open an issue in your repository.



\## Overview



The `intent\_parser` module is a configurable, extensible framework for parsing, extracting, and clarifying software requirements and intent from various text and document sources. It is designed for use in modern code generation, requirements engineering, and software analysis workflows. The system leverages both rule-based and LLM-based extraction, supports multiple document formats and languages, and maintains a strong focus on security, configurability, and observability.



---



\## Key Features



\- \*\*Multi-format Parsing:\*\*  

&nbsp; Supports Markdown, reStructuredText (RST), plaintext, YAML, and PDF. Automatically detects format when configured.

\- \*\*Flexible Extraction Strategies:\*\*  

&nbsp; Regex-based and language-specific extraction patterns for features, constraints, and other requirement types.

\- \*\*LLM Integration:\*\*  

&nbsp; Supports LLM-powered ambiguity detection, clarification, and summarization using configurable providers (e.g., OpenAI, Anthropic).

\- \*\*Multi-language Support:\*\*  

&nbsp; Detects input language and applies language-specific extraction logic. Default and fallback language configuration is supported.

\- \*\*Feedback Loop:\*\*  

&nbsp; Collects and stores user feedback for continuous improvement.

\- \*\*Caching \& Performance:\*\*  

&nbsp; Caches expensive LLM calls and temporary artifacts for efficiency.

\- \*\*Security \& Compliance:\*\*  

&nbsp; Encrypted at-rest storage for sensitive data, configurable PII/secret redaction, and audit logging.

\- \*\*Metrics \& Observability:\*\*  

&nbsp; Exposes Prometheus metrics and supports OpenTelemetry tracing for all major operations.

\- \*\*Extensible Configuration:\*\*  

&nbsp; YAML-based configuration allows per-project customizations, custom extraction logic, and security settings.



---



\## Directory Structure



\- `intent\_parser.py` — Main parsing logic, strategy selection, and orchestration.

\- `intent\_parser.yaml` — Central configuration file for parser settings, extraction patterns, LLM settings, feedback, caching, and security.



---



\## How It Works



1\. \*\*Configuration Loading:\*\*  

&nbsp;  Loads and validates the YAML config file (`intent\_parser.yaml`), setting up parsing, extraction, language, and LLM parameters.

2\. \*\*Format Detection \& Parsing:\*\*  

&nbsp;  Detects the document format (if `auto`) and parses the content into logical sections using the appropriate strategy.

3\. \*\*Feature \& Constraint Extraction:\*\*  

&nbsp;  Applies global or language-specific regex patterns to extract features, constraints, and other relevant items.

4\. \*\*Ambiguity Detection:\*\*  

&nbsp;  Uses LLM-based or rule-based logic to identify ambiguous requirements or unclear parts in the text.

5\. \*\*Summarization:\*\*  

&nbsp;  Optionally summarizes extracted requirements using LLMs or truncation strategies.

6\. \*\*Feedback \& Caching:\*\*  

&nbsp;  Stores user feedback and caches results to improve future performance.

7\. \*\*Security \& Logging:\*\*  

&nbsp;  Redacts sensitive information, logs actions for audit, and monitors with metrics and tracing.



---



\## Typical Usage



\*\*Python API:\*\*

```python

from intent\_parser import IntentParser



parser = IntentParser(config\_path='intent\_parser.yaml')

results = await parser.parse(

&nbsp;   content="Your requirements text here",

&nbsp;   format\_hint="markdown",           # Optional: 'auto', 'markdown', 'rst', 'plaintext', 'yaml', 'pdf'

&nbsp;   file\_path="requirements.md",      # Optional: Path to a requirements file

&nbsp;   dry\_run=False,                    # If True, disables LLM calls for ambiguity detection

&nbsp;   user\_id="analyst\_42"              # Used for logging/metrics

)

```



\*\*Configuration:\*\*

\- Edit `intent\_parser.yaml` to set extraction patterns, supported formats, LLM provider/model, feedback and cache file locations, language support, and security/PII redaction rules.



---



\## Configuration (intent\_parser.yaml)



\- \*\*format\*\*: Default or auto-detected document format.

\- \*\*extraction\_patterns\*\*: Regex patterns for extracting features, constraints, etc.

\- \*\*llm\_config\*\*: LLM provider, model, API key environment variable, temperature, and token/seed configuration.

\- \*\*multi\_language\_support\*\*: Enable/disable, set default language, and language-specific extraction patterns.

\- \*\*custom\_extraction\_configs\*\*: Per-project overrides for extraction patterns or prompt templates.

\- \*\*security\_config\*\*: Enable/disable custom redaction, define PII/secret redaction regexes, and set detection sensitivity.



---



\## Security \& Compliance



\- \*\*Redaction:\*\* All sensitive or PII data can be redacted using configurable regexes.

\- \*\*Encryption:\*\* At-rest cache and feedback data can be encrypted (see code/config for details).

\- \*\*Audit Logging:\*\* All major actions and events are logged for traceability.

\- \*\*File Permissions:\*\* Ensure that sensitive files (e.g., feedback, cache) are only accessible to the intended user.



---



\## Observability



\- \*\*Metrics:\*\* Prometheus metrics for latency, extraction events, errors, LLM calls, cache hits, feedback, etc.

\- \*\*Tracing:\*\* OpenTelemetry tracing for end-to-end workflow observability.

\- \*\*Logging:\*\* All major actions, errors, and security events are logged with context.



---



\## Extending the System



\- \*\*Add a New Format:\*\* Implement a new `ParserStrategy` subclass.

\- \*\*Custom Extraction:\*\* Edit `intent\_parser.yaml` or add Python modules for project-specific patterns and logic.

\- \*\*Integrate a New LLM:\*\* Implement LLM client logic and update the configuration.

\- \*\*Add Metrics/Tracing:\*\* Instrument new flows as necessary using Prometheus and OpenTelemetry APIs.



---



\## Getting Started



1\. Install Python dependencies:  

&nbsp;  See imports in `intent\_parser.py` and install via `pip` or `requirements.txt`.  

&nbsp;  (e.g., `pip install pydantic prometheus\_client opentelemetry-sdk pdfplumber pytesseract Pillow transformers ...`)

2\. Configure `intent\_parser.yaml` as needed.

3\. Use the Python API or CLI to parse documents and extract requirements/intents.

4\. Monitor logs and metrics for health and results.



---



\## License



\[Specify your license here]



---



\## Contact



For questions or support, please contact the maintainers or open an issue in your repository.



