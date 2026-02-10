<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

# Test Result Delivery to ASE

- **What triggers sending the results?**  
  - Inside the ASE pipelines, agents emit results themselves. Generator workflows publish `"test_results"` events after test generation/execution completes (`generator/main/engine.py:1297-1310`) and similarly from the plugin wrapper (`generator/agents/generator_plugin_wrapper.py:646-659`). The Meta Supervisor inspects results inline (no external trigger) when deciding follow-up actions (`omnicore_engine/meta_supervisor.py:1138-1144`). There is no background watcher; the sending is part of the task flow.

- **How is the destination known (API/webhook/event)?**  
  - Current flows use the Arbiter bridge event bus, not raw POST/webhook uploads: `ArbiterBridge.publish_event("test_results", payload)` sends via the configured message bus; the bridge is constructed with in-repo defaults and stubs (`generator/arbiter_bridge.py`). No generic “send wherever tests are stored” configuration exists—callers decide to publish via the bridge at the callsite. If a different endpoint is required, it must be provided explicitly to the agent logic; there is no auto-discovery of API URLs or file paths.

- **Handling large results (e.g., Vulcan-scale):**  
  - Existing code paths send compact JSON summaries (counts, status, a few file names) on the event bus (`generator/main/engine.py:1301-1307`, `generator/agents/generator_plugin_wrapper.py:650-656`). Large artifacts are not pushed. To handle very large outputs, the pattern would be: store artifacts externally (object storage / file path) and send only a reference plus a summary; chunked POST/webhook uploads are not implemented today.

- **Webhook vs API vs event bus—what’s active now?**  
  - Webhooks are not used for test results.  
  - Direct POST uploads are not implemented; no endpoint consumes bulk test-result files.  
  - Event messages via the Arbiter bridge are the active mechanism, assuming the message bus is enabled (the bridge falls back to stubs when Arbiter/bus is unavailable). If the bus is disabled, the publish call is caught/logged but effectively a no-op.
