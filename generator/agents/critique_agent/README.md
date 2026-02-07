<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

\# Critique Agent



\## Overview



\*\*Critique Agent\*\* is a production-grade, extensible, and language-agnostic automated code critique pipeline designed for modern software development workflows. Leveraging LLMs, advanced static analysis, dynamic testing, and security scanning, it provides actionable, explainable feedback on code, tests, and configuration artifacts. Critique Agent can be integrated into CI/CD systems, used by developer teams, or operated as a standalone service.



---



\## Key Features



\- \*\*Multi-Language Support\*\*: Python, JavaScript, Go (easily extendable to other languages via plugin interface).

\- \*\*Pluggable Pipeline\*\*: Modular steps including linting, unit/e2e/stress tests, security scanning, semantic/LLM critique, and auto-fix.

\- \*\*Security \& Compliance\*\*: Integrates Bandit, Semgrep, Snyk, Trivy, and OWASP checks. PII/secret scanning with regex and Presidio (if available).

\- \*\*Explainability \& Traceability\*\*: All critiques and auto-fixes are annotated with rationale and provenance metadata. Optional HITL (Human-in-the-Loop) gating for critical steps.

\- \*\*Metrics \& Observability\*\*: Built-in Prometheus and OpenTelemetry instrumentation for every pipeline step.

\- \*\*Self-Healing\*\*: Automated retries and LLM-guided self-healing on low-quality or failed pipeline steps.

\- \*\*Containerization\*\*: All tools and tests can be executed in isolated containers for safety and reproducibility.

\- \*\*Extensible Plugin Architecture\*\*: Language-specific critique plugins, custom hooks, and hot-reloadable registry.



---



\## Pipeline Overview



1\. \*\*Lint\*\*: Static code analysis using language-appropriate linters (e.g., Ruff/Pylint/Pyright for Python, ESLint/Jest for JS, GolangCI-lint for Go).

2\. \*\*Unit Tests\*\*: Runs unit tests in sandboxed environments, collects pass rates and coverage.

3\. \*\*E2E Tests\*\*: (Optional) Executes end-to-end tests (e.g., Playwright, Selenium, etc.).

4\. \*\*Stress Tests\*\*: (Optional) Loads tests using tools like Locust, K6, Vegeta.

5\. \*\*Security Scan\*\*: Runs SAST and vulnerability scanners (Semgrep, Bandit, Snyk, Gosec, etc.).

6\. \*\*Semantic Critique\*\*: LLM-driven review for requirement alignment, hallucinations, ambiguities, and test quality.

7\. \*\*Auto-Fix\*\*: Applies suggested code/test fixes, validates safety and security before committing changes.

8\. \*\*Explainability\*\*: Generates rationale for critiques and fixes, optionally using LLM explanations.

9\. \*\*Human Approval\*\*: (Optional) Presents results to human reviewers for approval or feedback.

10\. \*\*Metrics \& Logging\*\*: Every step logs structured events and exposes metrics for audit, monitoring, and traceability.



---



\## Quickstart



\### Prerequisites



\- Python >= 3.10

\- Docker (for containerized tool execution)

\- \[Prometheus](https://prometheus.io/) (for metrics scraping)

\- \[OpenTelemetry Collector](https://opentelemetry.io/) (optional, for distributed tracing)

\- (Optional) Third-party CLI tools installed in container images (e.g., `bandit`, `semgrep`, `snyk`, `golangci-lint`, `eslint`, etc.)



\### Install dependencies



```bash

pip install -r requirements.txt

\# Ensure Docker is running for containerized steps

```



\### Usage (CLI Example)



```bash

python critique\_agent.py --code-dir ./src --test-dir ./tests --config '{"target\_language": "auto", "enable\_e2e\_tests": true}'

```



\### Usage (as a Python module)



```python

import asyncio

from critique\_agent import orchestrate\_critique\_pipeline



results = asyncio.run(

&nbsp;   orchestrate\_critique\_pipeline(

&nbsp;       code\_files={"main.py": "..."},

&nbsp;       test\_files={"test\_main.py": "..."},

&nbsp;       requirements={"feature1": "description"},

&nbsp;       state\_summary="Initial commit",

&nbsp;       config={

&nbsp;           "target\_language": "python",

&nbsp;           "enable\_e2e\_tests": True,

&nbsp;           "pipeline\_steps": \["lint", "test", "security\_scan", "semantic"]

&nbsp;       }

&nbsp;   )

)

print(results)

```



\### API Integration



Integrate with your CI/CD pipeline to run critiques on pull requests, commits, or as a scheduled job. Metrics are exposed via Prometheus; traces can be exported to your APM/observability stack.



---



\## Configuration



The critique pipeline is fully configurable via the `CritiqueConfig` object or CLI JSON configs. Example options:



```json

{

&nbsp; "languages": \["python", "javascript", "go"],

&nbsp; "target\_language": "auto",

&nbsp; "pipeline\_steps": \["lint", "test", "security\_scan", "semantic", "fix"],

&nbsp; "enable\_e2e\_tests": true,

&nbsp; "enable\_stress\_tests": false,

&nbsp; "enable\_containerization": true,

&nbsp; "vulnerability\_scan\_tools": {

&nbsp;   "python": \["bandit", "semgrep"],

&nbsp;   "javascript": \["npm\_audit", "semgrep"],

&nbsp;   "go": \["gosec", "semgrep"]

&nbsp; },

&nbsp; "tool\_timeout\_seconds": 300,

&nbsp; "explainability": true

}

```



---



\## Extending Critique Agent



\### Adding a New Language Plugin



1\. Subclass `LanguageCritiquePlugin` and implement all abstract methods.

2\. Register your plugin via `register\_plugin('yourlang', YourLangCritiquePlugin())`.



\### Adding a Custom Pipeline Step



\- Add your step to `pipeline\_steps` in the config and provide an implementation/hook for that step.



\### Metrics \& Observability



\- Prometheus metrics are exposed on the default port (8000 by default).

\- OpenTelemetry spans cover all major pipeline operations.

\- All actions and errors are logged with structured context and provenance.



---



\## Security \& Compliance



\- \*\*PII/Secret Scrubbing:\*\* All code and test inputs are scrubbed with regex and/or Presidio before LLM or external tool exposure.

\- \*\*Vulnerability Scanning:\*\* Multiple tools are run in parallel, aggregated, and results are annotated for severity and suppressions.

\- \*\*Audit Logging:\*\* All actions can be traced with provenance IDs and hashes for compliance/audit requirements.



---



\## Troubleshooting



\- \*\*Missing Tools:\*\* Ensure all required CLI tools are installed in the containers used for each language.

\- \*\*Presidio Not Available:\*\* Regex-based scrubbing is used as a fallback, but for compliance, install Presidio.

\- \*\*Metrics Not Updating:\*\* Ensure Prometheus is scraping the correct port and endpoint.

\- \*\*Plugin Errors:\*\* Check logs for plugin load/registration errors. Use the hot-reloadable plugin interface for on-the-fly patching.



---



\## Contributing



1\. Fork and clone the repository.

2\. Add new plugins, pipeline steps, or extend the config schema.

3\. Write unit and integration tests for new features.

4\. Submit a pull request with a clear description of the enhancement.



---



\## License



\[MIT License](LICENSE)



---



\## Acknowledgements



Critique Agent leverages open-source tools including \[Bandit](https://bandit.readthedocs.io/), \[Semgrep](https://semgrep.dev/), \[Snyk](https://snyk.io/), \[GolangCI-Lint](https://golangci-lint.run/), \[ESLint](https://eslint.org/), \[OpenTelemetry](https://opentelemetry.io/), \[Prometheus](https://prometheus.io/), and more.



---



\## Contact



For support, feature requests, or security disclosures, please open an \[issue](https://github.com/yourorg/yourrepo/issues) or contact the maintainers.

