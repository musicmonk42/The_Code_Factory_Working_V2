\# TestGen Agent



\## Overview



\*\*TestGen Agent\*\* is a production-grade, agentic orchestrator for automated test generation using LLMs, context-aware prompt engineering, rigorous validation, and compliance enforcement. Designed for engineering teams and enterprises with high standards for security, reliability, and auditability, it drives the entire lifecycle from code context gathering through test generation, validation, critique, refinement, and explainability reporting.



---



\## Features



\- \*\*Agentic Loop\*\*: Multi-step generate → validate → critique → refine until quality thresholds are met.

\- \*\*Multi-Language Support\*\*: Out-of-the-box for Python, JavaScript, Java, Go, Rust, and extensible via plugins.

\- \*\*Security \& Compliance\*\*: Strict PII/secret scrubbing with Presidio at every stage; fails hard if scrubbing is unavailable.

\- \*\*LLM Orchestration\*\*: Provider-agnostic support (OpenAI, Anthropic Claude, Gemini, Grok, Local/Ollama) with streaming, fallback, and ensemble mode.

\- \*\*Rich Validation\*\*: Coverage, mutation, property-based, and stress testing with sandboxed execution and human-in-the-loop options.

\- \*\*Self-Healing\*\*: Automated recovery for malformed LLM output using LLM-powered repair.

\- \*\*Observability\*\*: Full Prometheus metrics, OpenTelemetry tracing, and Sentry error reporting.

\- \*\*Audit Logging\*\*: Every action is logged for traceability and compliance.

\- \*\*Extensible\*\*: Plugin registry for validators, prompt builders, and response parsers with hot-reload.

\- \*\*Explainability\*\*: Markdown summary reports with badges, PlantUML diagrams, and git changelogs.

\- \*\*Strict Failure Enforcement\*\*: No dummy modules, silent fallback, or incomplete dependency handling in production.



---



\## Architecture



1\. \*\*Context Gathering\*\*: Secure async file loading, secret scrubbing, and context initialization for codebase and RAG.

2\. \*\*Prompt Generation\*\*: Context-rich, agentic prompt building with adaptive chains and template versioning.

3\. \*\*LLM Orchestration\*\*: Robust routing and retries, streaming support, quota checks, and cost tracking.

4\. \*\*Response Parsing\*\*: Multi-format parsing, static analysis, AST validation, and auto-healing.

5\. \*\*Validation\*\*: Parallel test quality validation (coverage, mutation, property, stress) with results aggregation.

6\. \*\*Critique and Refinement\*\*: LLM-driven test critique and iterative improvement.

7\. \*\*Reporting\*\*: Rich Markdown reports, provenance, and visual workflow diagrams.

8\. \*\*Audit and Monitoring\*\*: Structured audit logs, Prometheus metrics, and OpenTelemetry traces at every step.



---



\## Quickstart



\### Prerequisites



\- Python 3.10+

\- All required Python dependencies (see `requirements.txt`)

\- Environment variables for LLM API keys (e.g., `OPENAI\_API\_KEY`, `CLAUDE\_API\_KEY`, etc.)

\- \[Presidio](https://microsoft.github.io/presidio/) for PII scrubbing

\- (Optional) \[Sentry](https://sentry.io/) DSN for error reporting

\- (Optional) PlantUML, Git, and required linters/scanners for your language

\- (Optional) PostgreSQL for quota/cost tracking and logging



\### Install



```bash

pip install -r requirements.txt

\# Ensure Presidio and all external tools are available in PATH

```



\### Usage (CLI)



```bash

python testgen\_agent.py \\

&nbsp;   my\_module.py another\_module.py \\

&nbsp;   --language python \\

&nbsp;   --repo-path ./my\_project \\

&nbsp;   --quality-threshold 85 \\

&nbsp;   --max-refinements 2 \\

&nbsp;   --output-file testgen\_results.json

```



\- For advanced policy overrides, see `--help` or use a JSON policy file with `--config`.



\### Outputs



\- Markdown explainability report with test run summary, metrics, and workflow diagram.

\- JSON file with all outputs, validation reports, and agentic history (if `--output-file` is specified).



---



\## Configuration



You can configure agent behavior via CLI flags or a JSON config file (`--config`). Key parameters:



| Parameter              | Description                                    | Example                   |

|------------------------|------------------------------------------------|---------------------------|

| `target\_files`         | Code files to generate tests for               | `main.py utils.py`        |

| `language`             | Programming language                           | `python`                  |

| `repo\_path`            | Root of code repository                        | `./myrepo`                |

| `quality\_threshold`    | Target metric for success (e.g., coverage)     | `90.0`                    |

| `max\_refinements`      | Max refinement attempts                        | `3`                       |

| `primary\_metric`       | Main metric to optimize                        | `coverage\_percentage`     |

| `validation\_suite`     | Types of validation to run                     | `coverage mutation`       |

| `generation\_llm\_model` | LLM for initial generation                     | `gpt-4o`                  |

| `critique\_llm\_model`   | LLM for critique step                          | `claude-3-5-sonnet`       |

| ...                    | ...                                            | ...                       |



See the module docstring and CLI `--help` for full details.



---



\## Extensibility



\- \*\*Validators, Parsers, Prompt Builders\*\*: Add custom plugins in the relevant plugin directories. Plugins are discovered and hot-reloaded automatically.

\- \*\*Human-in-the-Loop\*\*: Register async/sync callbacks for approval at prompt generation or validation steps.

\- \*\*Policy\*\*: Define custom policies for agent behavior, quality gates, and retry strategies.



---



\## Security \& Compliance



\- \*\*Strict PII/Secret Scrubbing\*\*: Presidio is mandatory. If unavailable or fails, the agent aborts.

\- \*\*No Silent Fallback\*\*: All missing dependencies or failures result in immediate error.

\- \*\*Audit Logging\*\*: Every run is logged with provenance and trace IDs for compliance.

\- \*\*Compliance Mode\*\*: Set `COMPLIANCE\_MODE=true` for enhanced logging and reporting for regulated environments.



---



\## Observability



\- \*\*Prometheus\*\*: All major stages and error cases.

\- \*\*OpenTelemetry\*\*: Spans for all long-running operations.

\- \*\*Sentry\*\*: Production error reporting (if DSN is set).



---



\## Output Artifacts



\- \*\*Markdown Report\*\*: Run summary, status badge, PlantUML diagram, validation results, and changelog.

\- \*\*JSON Results\*\*: Full agentic history, metrics, and generated tests.

\- \*\*Audit Log\*\*: Structured records for all critical events (via `audit\_log`).



---



\## Troubleshooting



\- \*\*Dependency Failures\*\*: If the agent fails to start, check that all required modules (Presidio, LLM provider SDKs, audit\_log, etc.) are installed and available.

\- \*\*Missing LLM Keys\*\*: Ensure all required API keys are set as environment variables.

\- \*\*PlantUML/Git/External Tools\*\*: These are optional but required for full reporting and validation. Install or disable as needed.

\- \*\*Quota/Cost Tracking\*\*: Requires a running PostgreSQL instance if enabled.



---



\## Contributing



1\. Fork and clone the repository.

2\. Add or extend validators, prompt builders, or response parsers as needed.

3\. Write tests and ensure all new code is covered.

4\. Submit a pull request with a clear description and test results.



---



\## License



\[MIT License](LICENSE)



---



\## Support



\- For bugs, feature requests, or support, open a GitHub issue or contact the maintainers directly.

