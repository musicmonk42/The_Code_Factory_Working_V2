<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

# Test Runner Integration (Post-Test Result Delivery)

- **Is there a local agent triggered after tests?**  
  - No. There is no background agent or live interception watching test executions. Results are sent only when the CI job or the test-running code explicitly posts them.

- **How are results sent (API/webhook/event)?**  
  - Existing in-repo flows use event publishing via `ArbiterBridge.publish_event("test_results", ...)` inside the generator pipeline (`generator/main/engine.py:1297-1310`, `generator/agents/generator_plugin_wrapper.py:646-659`). These are inline sends, not background watchers.  
  - Webhooks and direct POST uploads for test results are not implemented today. If needed, CI can add an extra step (e.g., curl POST) to a chosen endpoint, but that endpoint must be provided/configured externally.  
  - Event messages assume the Arbiter/message bus is available; the bridge falls back to stubs if not, meaning the call becomes a no-op.

- **What triggers sending in CI?**  
  - CI would add a dedicated step after tests finish (e.g., “publish results”) that calls the API or emits an event. This repo does not auto-run that step for you; it must be wired into the CI workflow explicitly.

- **What triggers sending in local test runners? Which runners/languages?**  
  - Any runner can send results by adding a post-run hook that invokes the same publish step (API/event). The repository already has adapters/parsers for Python `pytest` outputs in generator runner utilities (`generator/runner/runner_core.py` and parsers), but nothing enforces or auto-discovers other languages’ runners.  
  - For non-Python runners, integrate by emitting a normalized summary (pass/fail counts, minimal metadata) and sending it through the same publish mechanism. There is no automatic multi-language interception; you must wire the hook per runner.

- **Large result handling:**  
  - Current code paths send compact summaries only. For very large outputs, store artifacts externally (e.g., object storage) and send a reference plus summary. Chunking/streaming uploads are not implemented in the existing flows.
