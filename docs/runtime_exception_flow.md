<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

# Runtime Exception Handling Deep Dive

- **What catches exceptions?**  
  - FastAPI app-wide handler `global_exception_handler` catches any uncaught API error (`server/main.py:1150-1164`).  
  - SFE guards wrap critical methods with `critical_alert_decorator`, catching runtime crashes in `DecisionOptimizer` (`self_fixing_engineer/arbiter/decision_optimizer.py:850-894`).  
  - The runtime tracer sets a `sys.settrace` hook (`_subprocess_trace_calls`) so subprocessed code reports `exception` events as they occur (`self_fixing_engineer/simulation/plugins/runtime_tracer_plugin.py:500-527`).

- **What logs the exceptions?**  
  - The FastAPI handler logs with the `server` logger configured by `server.logging_config.configure_logging` (`server/main.py:1150-1156`).  
  - `critical_alert_decorator` logs with `self.logger.exception`, emitting stack traces for SFE failures (`decision_optimizer.py:857-870`).  
  - The tracer writes structured exception records to its JSON trace buffer via `_safe_append`/`_subprocess_flush_trace_buffer`, then surfaces them through `analyze_runtime_behavior` (`runtime_tracer_plugin.py:500-527`, `900-969`).

- **Who sends them to BugManager?**  
  - Generator workflows forward failures through `ArbiterBridge.report_bug`, invoked from the workflow catch-all in `generator/main/engine.py:1434-1466`; `ArbiterBridge` hands off to `BugManager.report_bug`.  
  - SFE’s decorator escalates critical crashes with `bug_manager.bug_detected` when a BugManager instance is attached (`decision_optimizer.py:857-870`).  
  - OmniCore’s `PluginService` subscribes to `arbiter:bug_detected` events and relays payloads to `BugManager.report_bug` (`omnicore_engine/engines.py:408-441`).

- **Caught in code or parsed from logs?**  
  - All paths capture exceptions directly in code (try/except wrappers or `sys.settrace` hooks). The tracer reads the structured trace log it produced during execution, not existing application log files (`runtime_tracer_plugin.py:900-969`).
