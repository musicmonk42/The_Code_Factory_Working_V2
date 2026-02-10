<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

# Log/Error Ingestion into ASE

- **What feeds logs/errors to ASE?**  
  - The app logs locally (stdout/stderr) via the centralized logging config (`server/logging_config.py`). To reach ASE, an external forwarder must ship those logs to a collector (e.g., Sentry if `SENTRY_DSN` is set in `server/main.py:752-763`, or any log shipping sidecar/agent). ASE itself does not scrape local files.  
  - Message-bus health/metrics are exported via Prometheus counters/gauges inside the Guardian and bridges (`omnicore_engine/message_bus/guardian.py:315-352`). External monitoring scrapes these; ASE does not auto-pull them.

- **Is every log line processed by an LLM?**  
  - No. There is no default LLM-on-every-log pipeline. Analysis is done by internal logic (e.g., health/critical-failure checks in the Guardian, bug/event handling in ArbiterBridge consumers). If you want LLM analysis, you must build a consumer that pulls from the central log store or event bus and invokes an LLM explicitly.

- **How does ASE “listen”?**  
  - ASE reacts to structured events it already emits/receives (e.g., bug events, health events on the message bus). It does not tail arbitrary log files. To enable log-based reactions, ship logs to your central system and have a consumer publish actionable events to the bus; ASE stubs are present, but no automatic ingestion loop exists.

- **Exceptions caught/logged?**  
  - API unhandled exceptions are caught by the FastAPI global handler and logged (`server/main.py:1150-1156`).  
  - Internal subsystems log and emit metrics/alerts (e.g., Guardian critical failure alerts via webhook in `omnicore_engine/message_bus/guardian.py:380-474`). These are code-driven, not log-parsers.
