<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

# Metrics & Health Alerts (Sources and Triggers)

- **Where are the error rates coming from?**  
  - Plugin executions record an `error_rate` metric (1 on failure, 0 on success) into the plugin performance tracker whenever a plugin call errors (`omnicore_engine/plugin_registry.py:650-709`). These metrics feed later optimization/alert decisions (e.g., thresholds in `plugin_registry.py:1820-1833`).  
  - Message bus guardian health loop increments Prometheus counters/gauges for health states (`omnicore_engine/message_bus/guardian.py:315-352`), setting status flags and failure counters that can drive alerts when repeated failures occur.

- **Is ASE calling the health check API? How does it know what it is?**  
  - Health endpoints are served by the API itself: `/health`, `/api/health`, `/ready`, and `/health/detailed` in `server/main.py:1167-1470`. These are intended for platform monitors (Kubernetes/Railway/liveness probes) and any external checker that knows the base URL. ASE does not auto-discover or poll them; monitors or operators must call these endpoints explicitly.

- **What triggers ASE to parse health/alert signals?**  
  - The message bus guardian runs an internal async loop; failed health checks increment a failure counter and, once a configured threshold is exceeded, invoke the critical-failure handler (`guardian.py:315-410`). That handler sends alerts and performs self-healing. No separate log parser is used—alerts are generated in-process as part of the guardian loop.

- **How are alerts surfaced/stored? What does triggering an alert do?**  
  - On critical failure, the guardian posts a webhook payload to `ALERT_WEBHOOK_URL` with retries (`guardian.py:410-474`).  
  - It also emits metrics (`MESSAGE_BUS_CRITICAL_FAILURES_TOTAL`, `MESSAGE_BUS_HEALTH_STATUS`) and can enqueue the failure report into the message bus DLQ if configured (`guardian.py:382-404`).  
  - Logs are written via the standard logger at warning/critical levels; no persistent alert store beyond the DLQ/message bus and external webhook target.
