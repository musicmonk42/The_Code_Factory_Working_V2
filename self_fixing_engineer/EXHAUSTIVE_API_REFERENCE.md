# SELF-FIXING ENGINEER - EXHAUSTIVE API REFERENCE

## STATISTICS

| Module | Files | Lines | Classes | Functions | Methods |
|--------|-------|-------|---------|-----------|---------|
| **__init__.py** | 1 | 163 | 1 | 2 | 2 |
| **agent_orchestration** | 2 | 1,722 | 6 | 2 | 41 |
| **arbiter** | 106 | 80,672 | 410 | 284 | 1665 |
| **auto_fix_tests.py** | 1 | 36 | 0 | 1 | 0 |
| **cli.py** | 1 | 332 | 1 | 5 | 2 |
| **config.py** | 1 | 273 | 2 | 1 | 5 |
| **conftest.py** | 1 | 214 | 0 | 5 | 0 |
| **envs** | 3 | 2,176 | 9 | 4 | 48 |
| **exceptions.py** | 1 | 29 | 2 | 0 | 0 |
| **guardrails** | 3 | 2,623 | 3 | 30 | 14 |
| **intent_capture** | 11 | 8,534 | 47 | 123 | 115 |
| **main.py** | 1 | 1,175 | 2 | 29 | 4 |
| **mesh** | 9 | 9,485 | 24 | 66 | 109 |
| **plugins** | 26 | 16,012 | 97 | 72 | 364 |
| **run_sfe.py** | 1 | 200 | 0 | 7 | 0 |
| **run_tests.py** | 1 | 17 | 0 | 0 | 0 |
| **run_tests_timeout.py** | 1 | 30 | 0 | 0 | 0 |
| **run_working_tests.py** | 1 | 83 | 0 | 1 | 0 |
| **security_audit.py** | 1 | 374 | 1 | 0 | 6 |
| **self_healing_import_fixer** | 21 | 18,327 | 84 | 177 | 235 |
| **simulation** | 57 | 64,594 | 246 | 552 | 781 |
| **test_engine_integration.py** | 1 | 466 | 10 | 0 | 33 |
| **test_env.py** | 1 | 67 | 0 | 1 | 0 |
| **test_generation** | 27 | 20,847 | 92 | 244 | 209 |
| **tests** | 252 | 109,919 | 706 | 3411 | 2252 |
| **TOTAL** | **531** | **338,370** | **1743** | **5017** | **5885** |

**Grand Total Callable Items: 12645**

---

# MODULE: __INIT__.PY

## __init__.py
**Lines:** 163

### `_LazyModuleLoader`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, module_aliases |
| `__call__` |  | self, name |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_setup_module_alias` |  | module_name |
| `__getattr__` |  | name |

**Constants:** `_init_logger`, `__version__`, `__all__`, `_MODULE_ALIASES`, `_lazy_loader`

---

# MODULE: AGENT_ORCHESTRATION

## agent_orchestration/crew_manager.py
**Lines:** 1670

### `CrewAgentBase`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, name, config, tags, metadata |
| `health` | ✓ | self |

### `ResourceError` (Exception)

### `CrewPermissionError` (Exception)

### `AgentError` (Exception)

### `CrewManager`
**Attributes:** _thread_lock

| Method | Async | Args |
|--------|-------|------|
| `register_agent_class` |  | cls |
| `get_agent_class_by_name` |  | name |
| `__init__` |  | self, policy, metrics_hook, audit_hook, auto_restart, ...+8 |
| `_check_rbac` | ✓ | self, operation, caller_role |
| `add_hook` |  | self, event, cb |
| `_emit` | ✓ | self, event |
| `_maybe_audit` | ✓ | self, event, details |
| `add_agent` | ✓ | self, name, agent_class, config, tags, ...+3 |
| `sync_add_agent` |  | self |
| `remove_agent` | ✓ | self, name, caller_role |
| `_start_sandbox_with_retries` | ✓ | self, agent_info |
| `_start_sandbox` | ✓ | self, agent_info |
| `start_agent` | ✓ | self, name, caller_role |
| `_monitor_agent_sandbox` | ✓ | self, name |
| `stop_agent` | ✓ | self, name, timeout, force, caller_role |
| `_stop_agent_sandbox` | ✓ | self, name, timeout |
| `terminate_all` | ✓ | self, timeout, caller_role |
| `shutdown` | ✓ | self, timeout, caller_role |
| `reload_agent` | ✓ | self, name, config, caller_role |
| `start_all` | ✓ | self, tags, caller_role |
| `stop_all` | ✓ | self, tags, timeout, force, caller_role |
| `reload_all` | ✓ | self, configs, tags, caller_role |
| `_filter_agents` |  | self, tags |
| `_throttled_bulk_op` | ✓ | self, coros, op, chunk |
| `health` | ✓ | self |
| `monitor_heartbeats` | ✓ | self |
| `scale` | ✓ | self, count, agent_class, config, tags, ...+1 |
| `enforce_policy` | ✓ | self, rule |
| `metrics` | ✓ | self |
| `list_agents` |  | self, tags |
| `__aenter__` | ✓ | self |
| `__aexit__` | ✓ | self, exc_type, exc, tb |
| `close` | ✓ | self |
| `status` | ✓ | self |
| `lint` | ✓ | self |
| `describe` | ✓ | self |
| `save_state_redis` | ✓ | self |
| `load_state_redis` | ✓ | self |
| `_cleanup_orphaned_sandboxes` | ✓ | self |

### `MyWorkerAgent` (CrewAgentBase)

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `sanitize_dict` |  | data |
| `structured_log` |  | event |

**Constants:** `logger`, `NAME_REGEX`, `MAX_CONFIG_SIZE`, `MAX_AGENTS`

---

# MODULE: ARBITER

## arbiter/__init__.py
**Lines:** 179

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_load_components` |  |  |
| `_get_human_loop` |  |  |
| `_get_human_loop_config` |  |  |
| `get_component_status` |  |  |
| `__getattr__` |  | name |

**Constants:** `PYTEST_COLLECTING`, `arbiter`, `Arbiter`, `ArbiterArena`, `FeedbackManager`, `ArbiterConfig`, `_components_loaded`, `_LAZY_COMPONENT_NAMES`, `__version__`, `__all__`

---

## arbiter/agent_state.py
**Lines:** 563

### `AgentState` (Base)
**Attributes:** __tablename__, __table_args__, id, name, x, y, energy, inventory, language, memory, personality, world_size, agent_type, role

| Method | Async | Args |
|--------|-------|------|
| `__repr__` |  | self |
| `_parse_json_field` |  | self, field_value |
| `_validate_inventory` |  | self, inventory |
| `_validate_language` |  | self, language |
| `_validate_memory` |  | self, memory |
| `_validate_personality` |  | self, personality |
| `validate_inventory` |  | self, key, value |
| `validate_language` |  | self, key, value |
| `validate_memory` |  | self, key, value |
| `validate_personality` |  | self, key, value |
| `validate_energy` |  | self, key, value |
| `validate_world_size` |  | self, key, value |
| `_validate_json_fields_sync` |  | mapper, connection, target |
| `_validate_json_fields` | ✓ | mapper, connection, target |
| `_validate_fields_sync` |  | target |
| `_validate_fields` | ✓ | target |

### `AgentMetadata` (Base)
**Attributes:** __tablename__, __table_args__, id, key, value, created_at, updated_at

| Method | Async | Args |
|--------|-------|------|
| `__repr__` |  | self |
| `_parse_json_field` |  | self, field_value |
| `_validate_value` |  | self, value |
| `validate_value` |  | self, key, value |
| `_validate_json_fields_sync` |  | mapper, connection, target |
| `_validate_json_fields` | ✓ | mapper, connection, target |
| `_validate_fields_sync` |  | target |
| `_validate_fields` | ✓ | target |

**Constants:** `logger`, `tracer`, `Base`, `SCHEMA_VALIDATION_ERRORS`

---

## arbiter/arbiter.py
**Lines:** 3924

### `MyArbiterConfig` (BaseSettings)
**Attributes:** model_config

| Method | Async | Args |
|--------|-------|------|
| `customise_sources` |  | cls, init_settings, env_settings, file_secret_settings |
| `ensure_https_in_prod` |  | cls, v |
| `validate_api_key` |  | cls, v |
| `handle_none_or_empty` |  | cls, v |

### `AuditLogModel` (Base)
**Attributes:** __tablename__, __table_args__, id, agent_name, action, timestamp, details

### `ErrorLogModel` (Base)
**Attributes:** __tablename__, __table_args__, id, agent_name, timestamp, error_type, error_message, stack_trace

### `EventLogModel` (Base)
**Attributes:** __tablename__, __table_args__, id, agent_name, event_type, timestamp, description

### `Monitor`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, log_file, db_client |
| `log_action` | ✓ | self, event |
| `get_recent_events` |  | self, limit |
| `generate_reports` |  | self |

### `Explorer`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, sandbox_env |
| `execute` | ✓ | self, action |
| `get_status` | ✓ | self |
| `discover_urls` | ✓ | self, html_discovery_dir |
| `crawl_urls` | ✓ | self, urls |
| `explore_and_fix` | ✓ | self, arbiter, fix_paths |
| `close` | ✓ | self |

### `IntentCaptureEngine`

| Method | Async | Args |
|--------|-------|------|
| `generate_report` | ✓ | self, agent_name |

### `AuditLogManager`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, db_client |
| `log_audit` | ✓ | self, entry |

### `ExplainableReasoner` (PluginBase)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `initialize` | ✓ | self |
| `start` | ✓ | self |
| `stop` | ✓ | self |
| `get_capabilities` | ✓ | self |
| `health_check` | ✓ | self |
| `execute` | ✓ | self, action |

### `ArbiterGrowthManager` (PluginBase)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `initialize` | ✓ | self |
| `start` | ✓ | self |
| `stop` | ✓ | self |
| `health_check` | ✓ | self |
| `get_capabilities` | ✓ | self |
| `acquire_skill` | ✓ | self, skill_name, context |

### `BenchmarkingEngine`

| Method | Async | Args |
|--------|-------|------|
| `execute` | ✓ | self, action |

### `CompanyDataPlugin`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, settings |
| `execute` | ✓ | self, ticker |

### `PermissionManager`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config |
| `check_permission` |  | self, role, permission |

### `AgentStateManager`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, db_client, name, settings |
| `load_state` | ✓ | self |
| `save_state` | ✓ | self |
| `batch_save_state` | ✓ | self |
| `process_state_queue` | ✓ | self |
| `_initialize_default_state_in_memory` |  | self |

### `Arbiter`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, name, db_engine, settings, world_size, ...+18 |
| `orchestrate` | ✓ | self, task |
| `health_check` | ✓ | self |
| `register_plugin` | ✓ | self, kind, name, plugin |
| `publish_to_omnicore` | ✓ | self, event_type, data |
| `run_test_generation` | ✓ | self, code, language, config |
| `run_test_generation_in_process` | ✓ | self, code, language, config |
| `is_alive` |  | self |
| `log_event` |  | self, event_description, event_type |
| `evolve` | ✓ | self, arena |
| `choose_action_from_policy` |  | self, observation |
| `observe_environment` | ✓ | self, arena |
| `plan_decision` | ✓ | self, observation |
| `_build_observation` |  | self, obs_dict |
| `execute_action` | ✓ | self, decision |
| `reflect` | ✓ | self |
| `answer_why` | ✓ | self, query |
| `log_social_event` | ✓ | self, event, with_whom, round_n |
| `sync_with_explorer` | ✓ | self, explorer_knowledge |
| `start_async_services` | ✓ | self |
| `work_cycle` | ✓ | self |
| `explore_and_fix` | ✓ | self, fix_paths |
| `learn_from_data` | ✓ | self |
| `auto_optimize` | ✓ | self |
| `report_findings` | ✓ | self |
| `self_debug` | ✓ | self |
| `suggest_feature` | ✓ | self |
| `filter_companies` | ✓ | self, preferences |
| `stop_async_services` | ✓ | self |
| `get_status` | ✓ | self |
| `run_benchmark` | ✓ | self |
| `explain` | ✓ | self |
| `push_metrics` | ✓ | self |
| `alert_critical_issue` | ✓ | self, issue |
| `coordinate_with_peers` | ✓ | self, message |
| `listen_for_peers` | ✓ | self |
| `setup_event_receiver` | ✓ | self |
| `_handle_incoming_event_http` | ✓ | self, request |
| `_handle_incoming_event` | ✓ | self, event_type, data |
| `_sanitize_event_data` |  | self, data |
| `_on_bug_detected` | ✓ | self, data |
| `_on_policy_violation` | ✓ | self, data |
| `_on_analysis_complete` | ✓ | self, data |
| `_on_generator_output` | ✓ | self, data |
| `_on_test_results` | ✓ | self, data |
| `_calculate_failure_priority` |  | self, failure |
| `_on_workflow_completed` | ✓ | self, data |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_init_sentry` |  |  |
| `_get_plugin_registry` |  |  |
| `_init_metrics` |  |  |
| `_init_additional_metrics` |  |  |
| `require_permission` |  | permission |
| `save_rl_model` |  | model, path |
| `load_rl_model` |  | path, env |
| `_register_default_plugins` |  |  |
| `main` |  |  |

**Constants:** `logger`, `_sentry_initialized`, `_metrics_initialized`, `event_counter`, `plugin_execution_time`, `_additional_metrics_initialized`, `action_counter`, `energy_gauge`, `memory_gauge`, `db_health_gauge`, `rl_reward_gauge`, `_plugins_registered`

---

## arbiter/arbiter_array_backend.py
**Lines:** 1107

### `ArrayBackendError` (Exception)

### `StorageError` (ArrayBackendError)

### `ArraySizeLimitError` (ArrayBackendError)

### `ArrayMeta`

### `ArrayBackend` (PluginBase)

| Method | Async | Args |
|--------|-------|------|
| `initialize` | ✓ | self |
| `append` | ✓ | self, item |
| `get` | ✓ | self, index |
| `update` | ✓ | self, index, item |
| `delete` | ✓ | self, index |
| `query` | ✓ | self, condition |
| `meta` |  | self |
| `health_check` | ✓ | self |
| `rotate_encryption_key` | ✓ | self, new_key |

### `PermissionManager`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config |
| `check_permission` |  | self, role, permission |

### `ConcreteArrayBackend` (ArrayBackend)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, name, storage_path, storage_type, config |
| `__del__` |  | self |
| `__aenter__` | ✓ | self |
| `__aexit__` | ✓ | self, exc_type, exc_val, exc_tb |
| `initialize` | ✓ | self |
| `close` | ✓ | self |
| `array` |  | self, data |
| `asnumpy` |  | self, data |
| `_save_to_storage` | ✓ | self |
| `_load_from_storage` | ✓ | self |
| `append` | ✓ | self, item |
| `get` | ✓ | self, index |
| `update` | ✓ | self, index, item |
| `delete` | ✓ | self, index |
| `query` | ✓ | self, condition |
| `meta` |  | self |
| `rotate_encryption_key` | ✓ | self, new_key |
| `_load_raw_from_storage` | ✓ | self |
| `health_check` | ✓ | self |
| `on_reload` |  | self |
| `start` | ✓ | self |
| `stop` |  | self |
| `get_capabilities` |  | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `get_or_create_metric` |  | metric_cls, name, desc, labelnames, buckets |

**Constants:** `logger`, `tracer`, `_metrics_lock`, `VALID_METRIC_TYPES`, `array_ops_total`, `array_op_time`, `array_size`, `array_errors_total`

---

## arbiter/arbiter_constitution.py
**Lines:** 288

### `ConstitutionViolation` (Exception)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, violated_principle |
| `__str__` |  | self |

### `ArbiterConstitution`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `_parse_constitution` |  | self, text |
| `get_purpose` |  | self |
| `get_powers` |  | self |
| `get_principles` |  | self |
| `get_evolution` |  | self |
| `get_aim` |  | self |
| `check_action` | ✓ | self, action, context |
| `enforce` | ✓ | self, action, context |
| `__str__` |  | self |
| `__repr__` |  | self |

**Constants:** `ARB_CONSTITUTION`, `logger`

---

## arbiter/arbiter_growth/arbiter_growth_manager.py
**Lines:** 789

### `HealthStatus` (Enum)
**Attributes:** INITIALIZING, HEALTHY, DEGRADED, STOPPED, ERROR

### `PluginHook` (Protocol)

| Method | Async | Args |
|--------|-------|------|
| `on_growth_event` | ✓ | self, event, state |

### `CircuitBreakerListener`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, arbiter_name |
| `before_call` |  | self, cb, func |
| `success` |  | self, cb |
| `failure` |  | self, cb, exc |
| `state_change` |  | self, cb, old_state, new_state |

### `Neo4jKnowledgeGraph`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config_store |
| `add_fact` | ✓ | self, arbiter_id, event_type, event_details |

### `LoggingFeedbackManager`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config_store |
| `record_feedback` | ✓ | self, arbiter_id, event_type, event_details |

### `ContextAwareCallable`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, coro, context_carrier, arbiter_id |
| `__call__` | ✓ | self |

### `ArbiterGrowthManager`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, arbiter_name, storage_backend, knowledge_graph, feedback_manager, ...+3 |
| `_add_breaker_listeners` |  | self |
| `start` | ✓ | self |
| `stop` | ✓ | self |
| `_process_pending_operations` | ✓ | self |
| `_periodic_flush` | ✓ | self |
| `_periodic_evolution_cycle` | ✓ | self |
| `_run_evolution_cycle` | ✓ | self |
| `_validate_audit_chain` | ✓ | self |
| `_recalculate_log_hash` |  | self, log |
| `_load_state_and_replay_events` | ✓ | self |
| `_apply_event` | ✓ | self, event, is_replay |
| `_is_event_valid` |  | self, event |
| `_save_if_dirty` | ✓ | self, force |
| `_save_snapshot_to_db` | ✓ | self |
| `_audit_log` | ✓ | self, operation, details |
| `_generate_idempotency_key` |  | self, event, service_name |
| `register_hook` |  | self, hook, stage |
| `_push_events` | ✓ | self, events |
| `_queue_operation` | ✓ | self, operation_coro |
| `record_growth_event` | ✓ | self, event_type, details |
| `improve_skill` | ✓ | self, skill_name, improvement_amount |
| `level_up` | ✓ | self |
| `get_health_status` | ✓ | self |
| `liveness_probe` |  | self |
| `readiness_probe` | ✓ | self |

**Constants:** `logger`, `tracer`

---

## arbiter/arbiter_growth/config_store.py
**Lines:** 437

### `ConfigStore`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, etcd_host, etcd_port, fallback_path, cache_ttl_seconds, ...+5 |
| `get` |  | self, key, default |
| `start_watcher` | ✓ | self |
| `stop_watcher` | ✓ | self |
| `_watch_for_changes` | ✓ | self |
| `_watch_etcd_updates` | ✓ | self |
| `_is_cache_valid` |  | self, key |
| `_load_from_fallback` | ✓ | self |
| `_parse_value` |  | self, value_str |
| `_get_from_etcd_with_retry` | ✓ | self, key |
| `_get_from_etcd` | ✓ | self, key |
| `get_config` | ✓ | self, key, default |
| `get_all` |  | self |

### `TokenBucketRateLimiter`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config_store |
| `acquire` | ✓ | self, timeout |

**Constants:** `logger`

---

## arbiter/arbiter_growth/exceptions.py
**Lines:** 119

### `ArbiterGrowthError` (Exception)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, details |
| `__str__` |  | self |

### `OperationQueueFullError` (ArbiterGrowthError)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, details |

### `RateLimitError` (ArbiterGrowthError)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, details |

### `CircuitBreakerOpenError` (ArbiterGrowthError)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, details |

### `AuditChainTamperedError` (ArbiterGrowthError)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, details |

**Constants:** `logger`

---

## arbiter/arbiter_growth/idempotency.py
**Lines:** 220

### `IdempotencyStoreError` (Exception)

### `IdempotencyStore`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `check_and_set` | ✓ | self, key, ttl |
| `start` | ✓ | self |
| `stop` | ✓ | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_get_or_create_metric` |  | metric_class, name, doc, labelnames |

**Constants:** `logger`, `tracer`, `IDEMPOTENCY_HITS_TOTAL`

---

## arbiter/arbiter_growth/metrics.py
**Lines:** 205

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `get_or_create_metric` |  | metric_class, name, documentation, labelnames, config_store, ...+1 |

**Constants:** `logger`, `GROWTH_EVENTS`, `GROWTH_SAVE_ERRORS`, `GROWTH_PENDING_QUEUE`, `GROWTH_SKILL_IMPROVEMENT`, `GROWTH_SNAPSHOTS`, `GROWTH_CIRCUIT_BREAKER_TRIPS`, `GROWTH_ANOMALY_SCORE`, `GROWTH_EVENT_PUSH_LATENCY`, `GROWTH_OPERATION_QUEUE_LATENCY`, `GROWTH_OPERATION_EXECUTION_LATENCY`, `STORAGE_LATENCY_SECONDS`, `AUDIT_VALIDATION_ERRORS_TOTAL`, `GROWTH_AUDIT_ANCHORS_TOTAL`, `IDEMPOTENCY_HITS_TOTAL`, ...+2 more

---

## arbiter/arbiter_growth/models.py
**Lines:** 263

### `GrowthEvent` (BaseModel)

| Method | Async | Args |
|--------|-------|------|
| `type_must_not_be_whitespace` |  | cls, v |
| `validate_timestamp` |  | cls, v |

### `ArbiterState` (BaseModel)

| Method | Async | Args |
|--------|-------|------|
| `convert_event_offset` |  | cls, v |
| `validate_skill_scores` |  | cls, v |
| `set_skill_score` |  | self, skill_name, score |
| `model_dump` |  | self |

### `GrowthSnapshot` (Base)
**Attributes:** __tablename__, arbiter_id, level, skills_encrypted, user_preferences_encrypted, experience_points, schema_version, event_offset, timestamp, __table_args__

### `GrowthEventRecord` (Base)
**Attributes:** __tablename__, id, arbiter_id, event_type, timestamp, details_encrypted, event_version, __table_args__

### `AuditLog` (Base)
**Attributes:** __tablename__, id, arbiter_id, operation, timestamp, details, previous_log_hash, log_hash, __table_args__

---

## arbiter/arbiter_growth/plugins.py
**Lines:** 113

### `PluginHook` (ABC)

| Method | Async | Args |
|--------|-------|------|
| `on_start` | ✓ | self, arbiter_name |
| `on_stop` | ✓ | self, arbiter_name |
| `on_error` | ✓ | self, arbiter_name, error |
| `on_growth_event` | ✓ | self, event, state |

---

## arbiter/arbiter_growth/storage_backends.py
**Lines:** 988

### `StorageBackend` (Protocol)

| Method | Async | Args |
|--------|-------|------|
| `start` | ✓ | self |
| `stop` | ✓ | self |
| `__aenter__` | ✓ | self |
| `__aexit__` | ✓ | self, exc_type, exc_val, exc_tb |
| `load_snapshot` | ✓ | self, arbiter_id |
| `save_snapshot` | ✓ | self, arbiter_id, data |
| `save_event` | ✓ | self, arbiter_id, event |
| `load_events` | ✓ | self, arbiter_id, from_offset |
| `save_audit_log` | ✓ | self, arbiter_id, operation, details, previous_hash |
| `get_last_audit_hash` | ✓ | self, arbiter_id |
| `load_all_audit_logs` | ✓ | self, arbiter_id |

### `SQLiteStorageBackend`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config |
| `_get_session` | ✓ | self |
| `start` | ✓ | self |
| `stop` | ✓ | self |
| `__aenter__` | ✓ | self |
| `__aexit__` | ✓ | self, exc_type, exc_val, exc_tb |
| `load_snapshot` | ✓ | self, arbiter_id |
| `save_snapshot` | ✓ | self, arbiter_id, data |
| `save_event` | ✓ | self, arbiter_id, event |
| `load_events` | ✓ | self, arbiter_id, from_offset |
| `save_audit_log` | ✓ | self, arbiter_id, operation, details, previous_hash |
| `get_last_audit_hash` | ✓ | self, arbiter_id |
| `load_all_audit_logs` | ✓ | self, arbiter_id |

### `RedisStreamsStorageBackend`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config |
| `_key` |  | self, arbiter_id, key_type |
| `start` | ✓ | self |
| `stop` | ✓ | self |
| `__aenter__` | ✓ | self |
| `__aexit__` | ✓ | self, exc_type, exc_val, exc_tb |
| `load_snapshot` | ✓ | self, arbiter_id |
| `save_snapshot` | ✓ | self, arbiter_id, data |
| `save_event` | ✓ | self, arbiter_id, event |
| `load_events` | ✓ | self, arbiter_id, from_offset |
| `save_audit_log` | ✓ | self, arbiter_id, operation, details, previous_hash |
| `get_last_audit_hash` | ✓ | self, arbiter_id |
| `load_all_audit_logs` | ✓ | self, arbiter_id |

### `KafkaStorageBackend`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config |
| `_topic` |  | self, arbiter_id, topic_type |
| `start` | ✓ | self |
| `stop` | ✓ | self |
| `__aenter__` | ✓ | self |
| `__aexit__` | ✓ | self, exc_type, exc_val, exc_tb |
| `load_snapshot` | ✓ | self, arbiter_id |
| `save_snapshot` | ✓ | self, arbiter_id, data |
| `save_event` | ✓ | self, arbiter_id, event |
| `load_events` | ✓ | self, arbiter_id, from_offset |
| `save_audit_log` | ✓ | self, arbiter_id, operation, details, previous_hash |
| `get_last_audit_hash` | ✓ | self, arbiter_id |
| `load_all_audit_logs` | ✓ | self, arbiter_id |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_get_encryption_key_from_env` |  |  |
| `_create_hmac_hash` |  | key |
| `_wrap_exception` |  | backend_name |
| `_normalize_event_offset` |  | offset |
| `storage_backend_factory` |  | config |

**Constants:** `logger`, `tracer`, `CACHE_TIMEOUT_SECONDS`, `STORAGE_LATENCY_SECONDS`, `REDIS_BREAKER`, `KAFKA_BREAKER`, `SQL_BREAKER`

---

## arbiter/arbiter_growth.py
**Lines:** 2782

### `ArbiterGrowthError` (Exception)

### `OperationQueueFullError` (ArbiterGrowthError)

### `RateLimitError` (ArbiterGrowthError)

### `CircuitBreakerOpenError` (ArbiterGrowthError)

### `AuditChainTamperedError` (ArbiterGrowthError)

### `ConfigStore`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, etcd_host, etcd_port, fallback_path |
| `_load_from_fallback` | ✓ | self |
| `get_config` | ✓ | self, key |
| `ping` | ✓ | self |

### `TokenBucketRateLimiter`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config_store |
| `acquire` | ✓ | self, timeout |

### `ContextAwareCallable`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, coro, context_carrier, arbiter_id |
| `__call__` | ✓ | self |

### `IdempotencyStore`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, redis_url |
| `check_and_set` | ✓ | self, key, ttl |
| `start` | ✓ | self |
| `ping` | ✓ | self |
| `stop` | ✓ | self |
| `remember` | ✓ | self, key, ttl |

### `StorageBackend` (Protocol)

| Method | Async | Args |
|--------|-------|------|
| `load` | ✓ | self, arbiter_id |
| `save` | ✓ | self, arbiter_id, data |
| `save_event` | ✓ | self, arbiter_id, event |
| `load_events` | ✓ | self, arbiter_id, from_offset |
| `start` | ✓ | self |
| `stop` | ✓ | self |
| `ping` | ✓ | self |
| `save_audit_log` | ✓ | self, arbiter_id, operation, details, previous_hash |
| `get_last_audit_hash` | ✓ | self, arbiter_id |
| `load_all_audit_logs` | ✓ | self, arbiter_id |

### `SQLiteStorageBackend`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, session_factory, encryption_key |
| `start` | ✓ | self |
| `stop` | ✓ | self |
| `ping` | ✓ | self |
| `load` | ✓ | self, arbiter_id |
| `save` | ✓ | self, arbiter_id, data |
| `save_event` | ✓ | self, arbiter_id, event |
| `load_events` | ✓ | self, arbiter_id, from_offset |
| `save_audit_log` | ✓ | self, arbiter_id, operation, details, previous_hash |
| `get_last_audit_hash` | ✓ | self, arbiter_id |
| `load_all_audit_logs` | ✓ | self, arbiter_id |

### `RedisStreamsStorageBackend`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, redis_url, encryption_key, config_store |
| `start` | ✓ | self |
| `stop` | ✓ | self |
| `ping` | ✓ | self |
| `_get_stream_key` | ✓ | self, arbiter_id |
| `_get_snapshot_key` | ✓ | self, arbiter_id |
| `_get_audit_key` | ✓ | self, arbiter_id |
| `load` | ✓ | self, arbiter_id |
| `save` | ✓ | self, arbiter_id, data |
| `save_event` | ✓ | self, arbiter_id, event |
| `load_events` | ✓ | self, arbiter_id, from_offset |
| `save_audit_log` | ✓ | self, arbiter_id, operation, details, previous_hash |
| `get_last_audit_hash` | ✓ | self, arbiter_id |
| `load_all_audit_logs` | ✓ | self, arbiter_id |

### `KafkaStorageBackend`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, bootstrap_servers, schema_registry_url, zookeeper_hosts, encryption_key |
| `start` | ✓ | self |
| `stop` | ✓ | self |
| `ping` | ✓ | self |
| `load` | ✓ | self, arbiter_id |
| `save` | ✓ | self, arbiter_id, data |
| `save_event` | ✓ | self, arbiter_id, event |
| `load_events` | ✓ | self, arbiter_id, from_offset |
| `save_audit_log` | ✓ | self, arbiter_id, operation, details, previous_hash |
| `get_last_audit_hash` | ✓ | self, arbiter_id |
| `load_all_audit_logs` | ✓ | self, arbiter_id |

### `GrowthEvent` (BaseModel)

### `ArbiterState` (BaseModel)

| Method | Async | Args |
|--------|-------|------|
| `set_skill_score` |  | self, skill_name, score |

### `GrowthSnapshot` (Base)
**Attributes:** __tablename__, arbiter_id, level, skills_encrypted, user_preferences_encrypted, schema_version, event_offset

### `GrowthEventRecord` (Base)
**Attributes:** __tablename__, id, arbiter_id, event_type, timestamp, details_encrypted, event_version

### `AuditLog` (Base)
**Attributes:** __tablename__, id, arbiter_id, operation, timestamp, details, previous_log_hash, log_hash

### `KnowledgeGraph`

| Method | Async | Args |
|--------|-------|------|
| `add_fact` | ✓ | self |

### `FeedbackManager`

| Method | Async | Args |
|--------|-------|------|
| `record_feedback` | ✓ | self |

### `PluginHook` (ABC)

| Method | Async | Args |
|--------|-------|------|
| `on_growth_event` | ✓ | self, event, state |

### `ArbiterGrowthManager`
**Attributes:** MAX_PENDING_OPERATIONS, SCHEMA_VERSION

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, arbiter_name, storage_backend, knowledge_graph, feedback_manager, ...+3 |
| `_call_maybe_async` | ✓ | fn |
| `start` | ✓ | self |
| `_periodic_evolution_cycle` | ✓ | self |
| `_run_evolution_cycle` | ✓ | self |
| `_validate_audit_chain` | ✓ | self |
| `anchor_audit_chain_periodically` | ✓ | self, external_ledger_api |
| `_on_load_done` |  | self, fut |
| `_periodic_flush` | ✓ | self |
| `shutdown` | ✓ | self |
| `_load_state_and_replay_events` | ✓ | self |
| `_apply_event` | ✓ | self, event |
| `_save_snapshot_to_db` | ✓ | self |
| `__do_save_snapshot` | ✓ | self |
| `_save_if_dirty` | ✓ | self, force |
| `_audit_log` | ✓ | self, operation, details |
| `_generate_idempotency_key` |  | self, event, service_name |
| `register_hook` |  | self, hook, stage |
| `_push_event` | ✓ | self, event |
| `__do_push_event` | ✓ | self, event |
| `_queue_operation` | ✓ | self, operation_coro |
| `record_growth_event` | ✓ | self, event_type, details |
| `acquire_skill` | ✓ | self, skill_name, initial_score, context |
| `improve_skill` | ✓ | self, skill_name, improvement_amount, context |
| `level_up` | ✓ | self |
| `gain_experience` | ✓ | self, amount, context |
| `update_user_preference` | ✓ | self, key, value, context |
| `_record_event_now` | ✓ | self, event |
| `get_growth_summary` | ✓ | self |
| `health` | ✓ | self |
| `liveness_probe` | ✓ | self |
| `readiness_probe` | ✓ | self |
| `rotate_encryption_key` | ✓ | self, new_key |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `get_or_create_metric` |  | metric_class, name, documentation, labelnames, buckets |

**Constants:** `tracer`, `propagator`, `logger`, `VALID_METRIC_TYPES`, `GROWTH_EVENTS`, `GROWTH_SAVE_ERRORS`, `GROWTH_PENDING_QUEUE`, `GROWTH_SKILL_IMPROVEMENT`, `GROWTH_SNAPSHOTS`, `GROWTH_EVENT_PUSH_LATENCY`, `GROWTH_OPERATION_QUEUE_LATENCY`, `GROWTH_OPERATION_EXECUTION_LATENCY`, `GROWTH_CIRCUIT_BREAKER_TRIPS`, `GROWTH_ANOMALY_SCORE`, `CONFIG_FALLBACK_USED`, ...+2 more

---

## arbiter/arbiter_plugin_registry.py
**Lines:** 1375

### `PlugInKind` (Enum)
**Attributes:** WORKFLOW, VALIDATOR, REPORTER, GROWTH_MANAGER, CORE_SERVICE, ANALYTICS, STRATEGY, TRANSFORMER, AI_ASSISTANT

### `PluginError` (Exception)

### `PluginDependencyError` (PluginError)

### `PluginMeta`

### `PluginBase` (ABC)

| Method | Async | Args |
|--------|-------|------|
| `initialize` | ✓ | self |
| `start` | ✓ | self |
| `stop` | ✓ | self |
| `health_check` | ✓ | self |
| `get_capabilities` | ✓ | self |
| `on_reload` |  | self |

### `PluginRegistry`
**Attributes:** _lock

| Method | Async | Args |
|--------|-------|------|
| `__new__` |  | cls, persist_path |
| `set_event_hook` |  | self, hook |
| `_trigger_event` |  | self, event_dict |
| `_meta_to_dict` |  | self, meta |
| `_load_persisted_plugins` |  | self |
| `_persist_plugins` |  | self |
| `_verify_signature` |  | self, plugin, meta |
| `_validate_name` |  | self, name |
| `_validate_version` |  | self, version_str |
| `_validate_dependencies` |  | self, kind, name, dependencies |
| `_satisfies_version` |  | self, current, required |
| `register_with_omnicore` | ✓ | self, kind, name, plugin, version, ...+1 |
| `_validate_plugin_class` |  | self, plugin, meta |
| `register` |  | self, kind, name, version, dependencies |
| `register_instance` |  | self, kind, name, instance, version, ...+1 |
| `get` |  | self, kind, name |
| `get_metadata` |  | self, kind, name |
| `list_plugins` |  | self, kind |
| `export_registry` |  | self |
| `unregister` | ✓ | self, kind, name |
| `reload` | ✓ | self, kind, name |
| `health_check` | ✓ | self, kind, name |
| `health_check_all` | ✓ | self |
| `discover` |  | self, package, kind |
| `load_from_package` | ✓ | self, package_url, signature |
| `sandboxed_plugin` |  | self, kind, name |
| `initialize_all` | ✓ | self |
| `_initialize_plugin` | ✓ | self, kind, name, plugin |
| `start_all` | ✓ | self |
| `_start_plugin` | ✓ | self, kind, name, plugin |
| `stop_all` | ✓ | self |
| `_stop_plugin` | ✓ | self, kind, name, plugin |
| `__aenter__` | ✓ | self |
| `__aexit__` | ✓ | self, exc_type, exc_val, exc_tb |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `register` |  | kind, name, version, author |
| `get_registry` |  |  |
| `__getattr__` |  | name |

**Constants:** `logger`, `_IMPORT_IN_PROGRESS`, `logger`, `_registry_lock`, `_IMPORT_IN_PROGRESS`

---

## arbiter/arena.py
**Lines:** 1390

### `ArbiterArena`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, settings, port, name, db_engine, ...+1 |
| `__aenter__` | ✓ | self |
| `__aexit__` | ✓ | self, exc_type, exc_val, exc_tb |
| `_send_webhook` | ✓ | self, event_type, data |
| `_setup_error_handlers` |  | self |
| `_update_and_persist_map` | ✓ | self, new_map_data, source |
| `_create_initial_scan_coro` | ✓ | self |
| `_create_periodic_scan_coro` | ✓ | self |
| `_initialize_arbiters` |  | self |
| `register` | ✓ | self, arbiter |
| `remove` | ✓ | self, arbiter |
| `get_random_arbiter` | ✓ | self |
| `distribute_task` | ✓ | self, task_coro |
| `_setup_routes` |  | self |
| `start_arena_services` | ✓ | self, http_port |
| `handle_status` | ✓ | self, request |
| `run_all` | ✓ | self, max_cycles |
| `run_arena_rounds` | ✓ | self |
| `stop_all` | ✓ | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_get_registry` |  |  |
| `_get_plugin_registry_dict` |  |  |
| `_get_arbiter_class` |  |  |
| `get_or_create_prom_counter` |  | name, documentation, labelnames |
| `get_or_create_prom_gauge` |  | name, documentation, labelnames |
| `require_auth` |  | func |
| `_handle_shutdown` |  | loop, arena |
| `_extract_sqlite_db_file` |  | db_url |
| `run_arena_async` | ✓ | settings |
| `run_arena` |  |  |

**Constants:** `__all__`, `logger`, `_Arbiter_class`, `tracer`, `JWT_SECRET_FALLBACK`, `_metrics_lock`, `scan_repair_cycles_total`, `defects_found_total`, `repairs_attempted_total`, `repairs_successful_total`, `agent_evolutions_total`, `active_arbiters`, `arena_ops_total`, `arena_errors_total`

---

## arbiter/audit_log.py
**Lines:** 1123

### `RotationType` (str, Enum)
**Attributes:** SIZE, SECOND, MINUTE, HOUR, DAY, MIDNIGHT, WEEKDAY

### `CompressionType` (str, Enum)
**Attributes:** NONE, GZIP

### `AuditLoggerConfig`

| Method | Async | Args |
|--------|-------|------|
| `__post_init__` |  | self |

### `SizedTimedRotatingFileHandler` (TimedRotatingFileHandler)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, filename, when, interval, backupCount, ...+2 |
| `shouldRollover` |  | self, record |
| `doRollover` |  | self |
| `_compress_rotated_file` |  | self |

### `TamperEvidentLogger`

| Method | Async | Args |
|--------|-------|------|
| `__new__` |  | cls, config |
| `__init__` |  | self, config |
| `get_instance` |  | cls, config |
| `_setup_file_logger` |  | self |
| `_setup_dlt_client` |  | self |
| `_setup_metrics` |  | self |
| `_setup_encryption` |  | self |
| `_get_trace_ids` |  |  |
| `_get_agent_info` |  |  |
| `_hash_entry` |  | prev_hash, entry_dict |
| `_sanitize_dict` |  | d, max_size |
| `_encrypt_entry` |  | self, entry |
| `_decrypt_entry` |  | self, entry |
| `_log_to_file_async` | ✓ | self, entries |
| `_log_to_file_sync` |  | self, entries |
| `_process_batch_loop` | ✓ | self |
| `_anchor_to_dlt` | ✓ | self, entries |
| `log_event` | ✓ | self, event_type, details, user_id, critical, ...+1 |
| `_sanitize_details` |  | self, details |
| `_compute_hmac` |  | self, event_id, event_type, details, user_id |
| `emit_audit_event` | ✓ | self, event_type, details, user_id, critical, ...+1 |
| `_get_log_files` |  | self, log_path |
| `verify_log_integrity` | ✓ | self, log_path |
| `load_audit_trail` |  | self, log_path, event_type, start_time, end_time, ...+1 |
| `_filter_log_entries` |  | self, file_handle, event_type, start_time, end_time, ...+1 |
| `_filter_entry_logic` |  | entry, event_type, start_time, end_time, user_id |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `log_event` | ✓ | event_type, details, critical, user_id, extra |
| `verify_log_integrity` | ✓ | log_path |
| `load_audit_trail` | ✓ | log_path, event_type, start_time, end_time, user_id |
| `emit_audit_event` | ✓ | event_type, details, user_id, critical, omnicore_url |

**Constants:** `audit_logger`

---

## arbiter/audit_schema.py
**Lines:** 419

### `AuditEventType` (str, Enum)
**Attributes:** CODE_GENERATION_STARTED, CODE_GENERATION_COMPLETED, CODE_GENERATION_FAILED, CRITIQUE_COMPLETED, TEST_GENERATION_COMPLETED, DEPLOYMENT_STARTED, DEPLOYMENT_COMPLETED, POLICY_CHECK, POLICY_VIOLATION, CONSTITUTION_CHECK, CONSTITUTION_VIOLATION, BUG_DETECTED, BUG_FIXED, LEARNING_EVENT, EVOLUTION_CYCLE

### `AuditSeverity` (str, Enum)
**Attributes:** DEBUG, INFO, WARNING, ERROR, CRITICAL

### `AuditModule` (str, Enum)
**Attributes:** GENERATOR, ARBITER, TEST_GENERATION, SIMULATION, OMNICORE, GUARDRAILS, SERVER, MESH

### `AuditRouter`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `register_backend` |  | self, backend, name |
| `route_event` | ✓ | self, event |
| `route_event_sync` |  | self, event |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `create_audit_event` |  | event_type, module, message |

**Constants:** `logger`

---

## arbiter/bug_manager/audit_log.py
**Lines:** 620

### `AuditLogManager`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, log_path, dead_letter_log_path, enabled, settings, ...+1 |
| `initialize` | ✓ | self |
| `shutdown` | ✓ | self |
| `_write_to_dead_letter_queue` | ✓ | self, entry, reason |
| `_sync_write_to_dead_letter` |  | self, data |
| `_periodic_flush` | ✓ | self |
| `_flush_buffer` | ✓ | self, final_flush |
| `_rotate_logs` | ✓ | self |
| `_sync_rotate_logs` |  | self |
| `_sync_atomic_write_with_retry` |  | self, entries |
| `_send_to_remote_audit_service` | ✓ | self, entries |
| `_handle_remote_send_failure` | ✓ | self, entries, reason |
| `audit` | ✓ | self, event_type, details |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `get_or_create_metric` |  | metric_class, name, documentation, labelnames |

**Constants:** `logger`, `AUDIT_LOG_FLUSH`, `AUDIT_LOG_WRITE_SUCCESS`, `AUDIT_LOG_WRITE_FAILED`, `AUDIT_LOG_DEAD_LETTER`, `AUDIT_LOG_ROTATION`, `AUDIT_LOG_BUFFER_SIZE_GAUGE`, `AUDIT_LOG_REMOTE_SEND_SUCCESS`, `AUDIT_LOG_REMOTE_SEND_FAILED`, `AUDIT_LOG_DISK_CHECK_FAILED`, `AUDIT_LOG_FLUSH_DURATION_SECONDS`, `AUDIT_LOG_DROPPED`

---

## arbiter/bug_manager/bug_manager.py
**Lines:** 746

### `Settings` (BaseModel)

### `RateLimiter`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, settings |
| `initialize` | ✓ | self |
| `rate_limit` |  | self, func |

### `BugManager`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, settings |
| `_initialize` | ✓ | self |
| `shutdown` | ✓ | self |
| `report` | ✓ | self, error_data, severity, location, custom_details |
| `_report_impl` | ✓ | self, error_data, severity, location, custom_details |
| `_parse_error_data` |  | self, error_data, severity, location, custom_details |
| `_get_stack_trace_from_caller` |  | self |
| `_generate_bug_signature` |  | self, error_data, location, custom_details |
| `_dispatch_notifications` | ✓ | self, error_details |

### `BugManagerArena` (BugManager)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, settings |
| `report` |  | self, error |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `register` |  | kind |
| `manage_bug` | ✓ | error_data, severity, location, custom_details |

**Constants:** `logger`, `_bug_id_var`, `BUG_REPORT`, `BUG_REPORT_SUCCESS`, `BUG_REPORT_FAILED`, `BUG_AUTO_FIX_ATTEMPT`, `BUG_AUTO_FIX_SUCCESS`, `BUG_NOTIFICATION_DISPATCH`, `BUG_PROCESSING_DURATION_SECONDS`, `BUG_RATE_LIMITED`, `BUG_CURRENT_ACTIVE_REPORTS`, `BUG_NOTIFICATION_FAILED`, `BUG_ML_INIT_FAILED`

---

## arbiter/bug_manager/notifications.py
**Lines:** 1023

### `CircuitBreaker`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, failure_threshold, recovery_timeout, half_open_attempts, redis_url, ...+1 |
| `initialize` | ✓ | self |
| `_get_state` | ✓ | self, channel |
| `_set_state` | ✓ | self, channel, state, failures, last_failure, ...+1 |
| `__call__` |  | self, channel |

### `RateLimiter`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, redis_url |
| `initialize` | ✓ | self |
| `rate_limit` |  | self, channel, max_calls, period |

### `NotificationService`

| Method | Async | Args |
|--------|-------|------|
| `register_critical_notification_handler` |  | cls, handler |
| `__init__` |  | self, settings |
| `_validate_settings` |  | self |
| `_initialize` | ✓ | self |
| `shutdown` | ✓ | self |
| `_check_and_escalate` | ✓ | self, channel, message |
| `_record_notification_failure` | ✓ | self, channel, message, error_code |
| `_record_notification_success` | ✓ | self, channel |
| `_simulate_failure` |  | self, channel |
| `_notify_slack_with_decorators` |  | self |
| `_notify_email_with_decorators` |  | self |
| `_notify_pagerduty_with_decorators` |  | self |
| `notify_batch` | ✓ | self, notifications |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `get_or_create_metric` |  | metric_class, name, documentation, labelnames |
| `default_escalation_handler` |  | channel, failures |

**Constants:** `logger`, `NOTIFICATION_SEND`, `NOTIFICATION_SEND_SUCCESS`, `NOTIFICATION_SEND_FAILED`, `NOTIFICATION_CIRCUIT_BREAKER_OPEN`, `NOTIFICATION_RATE_LIMITED`, `NOTIFICATION_CURRENT_FAILURES_GAUGE`, `NOTIFICATION_SEND_DURATION_SECONDS`

---

## arbiter/bug_manager/remediations.py
**Lines:** 880

### `MLRemediationModel`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, model_endpoint, settings |
| `_get_session` | ✓ | self |
| `close` | ✓ | self |
| `predict_remediation_strategy` | ✓ | self, bug_details |
| `record_remediation_outcome` | ✓ | self, bug_details, playbook_name, outcome |

### `RemediationStep`

| Method | Async | Args |
|--------|-------|------|
| `register_action` |  | cls, name, action |
| `__init__` |  | self, name, action_name, pre_condition, on_success, ...+6 |
| `execute` | ✓ | self, bug_details, playbook_name |

### `RemediationPlaybook`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, name, steps, description |
| `execute` | ✓ | self, location, bug_details |

### `BugFixerRegistry`

| Method | Async | Args |
|--------|-------|------|
| `set_settings` |  | cls, settings |
| `set_ml_model` |  | cls, model |
| `register_playbook` |  | cls, playbook, location, bug_signature_prefix |
| `run_remediation` | ✓ | cls, location, bug_details, bug_signature |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `get_or_create_metric` |  | metric_class, name, documentation, labelnames |
| `restart_service` | ✓ | bug_details |
| `clear_cache` | ✓ | bug_details |

**Constants:** `logger`, `REMEDIATION_PLAYBOOK_EXECUTION`, `REMEDIATION_STEP_EXECUTION`, `REMEDIATION_STEP_DURATION_SECONDS`, `REMEDIATION_SUCCESS`, `REMEDIATION_FAILURE`, `ML_REMEDIATION_PREDICTION`, `ML_REMEDIATION_PREDICTION_SUCCESS`, `ML_REMEDIATION_PREDICTION_FAILED`, `ML_REMEDIATION_FEEDBACK`, `ML_REMEDIATION_FEEDBACK_FAILED`, `restart_service_playbook`

---

## arbiter/bug_manager/utils.py
**Lines:** 538

### `Severity` (str, Enum)
**Attributes:** CRITICAL, HIGH, MEDIUM, LOW

| Method | Async | Args |
|--------|-------|------|
| `from_string` |  | cls, value |

### `SecretStr` (SecretStrBase)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, value |

### `BugManagerError` (Exception)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, error_id, timestamp |
| `__str__` |  | self |

### `NotificationError` (BugManagerError)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, channel, error_code |

### `CircuitBreakerOpenError` (BugManagerError)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, channel |

### `RateLimitExceededError` (BugManagerError)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, key |

### `AuditLogError` (BugManagerError)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, log_path |

### `RemediationError` (BugManagerError)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, step_name, playbook_name, original_exception |

### `MLRemediationError` (BugManagerError)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, model_endpoint |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `get_or_create_metric` |  | metric_class, name, documentation, labelnames |
| `parse_env` |  | var, default, type_hint |
| `parse_bool_env` |  | var, default |
| `redact_pii` |  | details, settings |
| `validate_settings` |  | settings_obj, required_fields |
| `apply_settings_validation` |  | settings_obj |
| `validate_input_details` |  | details |

**Constants:** `logger`, `PII_REDACTION_COUNT`, `SETTINGS_VALIDATION_ERRORS`

---

## arbiter/codebase_analyzer.py
**Lines:** 1454

### `AnalyzerError` (Exception)

### `ConfigurationError` (AnalyzerError)

### `AnalysisError` (AnalyzerError)

### `Defect` (TypedDict)

### `Dependency` (TypedDict)

### `ToolInfo` (TypedDict)

### `ComplexityInfo` (TypedDict)

### `FileSummary` (TypedDict)

### `Plugin`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, name, type |
| `run` | ✓ | self, file_path, source |
| `metadata` |  | self |

### `CodebaseAnalyzer`
**Attributes:** DEFAULT_IGNORE_PATTERNS, CONFIG_FILES, BASELINE_FILE

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, root_dir, ignore_patterns, config_file, max_workers |
| `__aenter__` | ✓ | self |
| `__aexit__` | ✓ | self, exc_type, exc_val, exc_tb |
| `_load_config` |  | self, config_file |
| `_parse_config_file` |  | self, config_path |
| `_load_baseline` |  | self |
| `_save_baseline` |  | self, defects |
| `_load_plugins` |  | self |
| `_should_ignore` |  | self, path |
| `_collect_py_files` | ✓ | self, path |
| `_read_file` |  | self, file_path |
| `_analyze_file_defects_and_complexity_blocking` |  | self, file_path |
| `_run_linters_sync` |  | self, file_path, source, tree |
| `_run_plugins_sync` |  | self, file_path, source |
| `_analyze_complexity_sync` |  | self, file_path, source |
| `_analyze_coverage_sync` |  | self, path |
| `scan_codebase` | ✓ | self, path, use_baseline |
| `analyze_and_propose` | ✓ | self, path |
| `_analyze_and_propose_sync` |  | self, path |
| `audit_repair_tools` | ✓ | self |
| `_audit_repair_tools_sync` |  | self |
| `map_dependencies` | ✓ | self, path |
| `_extract_dependencies_from_file` | ✓ | self, file_path |
| `generate_report` | ✓ | self, output_format, output_path, use_baseline |
| `_generate_markdown_report` |  | self, summary |
| `_generate_junit_xml_report` |  | self, summary |
| `analyze_file` | ✓ | self, file_path |
| `discover_files` |  | self |
| `_filter_baseline` |  | self, defects |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_create_dummy_metric` |  |  |
| `_get_or_create_metric` |  | metric_class, name, description, labelnames |
| `analyze_codebase` | ✓ | root_dir, config_file, output_format, output_path, use_baseline |
| `_run_async` |  | coro |
| `scan` |  | root_dir, config_file, output_format, output_path, use_baseline |
| `tools` |  | root_dir |

**Constants:** `tracer`, `logger`, `analyzer_ops_total`, `analyzer_errors_total`, `app`

---

## arbiter/config.py
**Lines:** 1162

### `ConfigError` (Exception)

### `LLMSettings` (BaseSettings)
**Attributes:** model_config

### `ArbiterConfig` (BaseSettings)
**Attributes:** model_config

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `ensure_https_in_prod` |  | cls, v |
| `validate_api_key` |  | cls, v |
| `validate_email_recipients` |  | cls, v |
| `initialize` |  | cls |
| `load_from_file` |  | cls, file_path |
| `load_from_env` |  | cls |
| `decrypt_sensitive_fields` |  | self |
| `encrypt_sensitive_fields` |  | self |
| `_validate_custom_settings` |  | self |
| `validate_file` |  | cls, file_path |
| `reload` |  | cls |
| `refresh` | ✓ | self |
| `stream_config_change` | ✓ | cls, key, value |
| `model_dump` |  | self |
| `to_dict` |  | self |
| `rotate_encryption_key` | ✓ | self |
| `health_check` |  | self |
| `DATABASE_URL` |  | self |
| `database_path` |  | self |
| `plugin_dir` |  | self |
| `log_level` |  | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_get_tracer` |  |  |
| `get_or_create_counter` |  | name, documentation, labelnames |
| `get_or_create_gauge` |  | name, documentation, labelnames |
| `get_or_create_histogram` |  | name, documentation, labelnames, buckets |
| `load_persona_dict` |  |  |

**Constants:** `_tracer_cache`, `logger`, `_metrics_lock`, `CONFIG_ACCESS`, `CONFIG_ERRORS`, `CONFIG_OPS_TOTAL`

---

## arbiter/decision_optimizer.py
**Lines:** 2044

### `SFECoreEngine`

### `MetaLearningService`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, logger |
| `get_latest_prioritization_weights` | ✓ | self |
| `get_latest_policy_rules` | ✓ | self |
| `get_latest_plugin_version` | ✓ | self, kind, name |
| `get_plugin_code` | ✓ | self, kind, name, version |

### `Task`

| Method | Async | Args |
|--------|-------|------|
| `__post_init__` |  | self |

### `Agent`

| Method | Async | Args |
|--------|-------|------|
| `__post_init__` |  | self |

### `DecisionOptimizer`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, plugin_registry, settings, logger, arbiter, ...+7 |
| `__aenter__` | ✓ | self |
| `__aexit__` | ✓ | self, exc_type, exc_val, exc_tb |
| `_periodic_strategy_refresh` | ✓ | self |
| `refresh_strategies` | ✓ | self |
| `prioritize_and_allocate` | ✓ | self, agents, tasks |
| `shutdown` |  | self |
| `process_remediation_proposal` | ✓ | self, proposal |
| `_execute_fix` | ✓ | self, proposal |
| `_log_event` | ✓ | self, event_type, details |
| `critical_alert_decorator` |  | method |
| `load_strategy_plugin` | ✓ | self, kind, name, strategy_type |
| `safe_execute` | ✓ | self, callable |
| `anonymize_task` | ✓ | self, task |
| `_handle_failed_task` | ✓ | self, task, error |
| `_redis_publish` | ✓ | self, channel, message |
| `prioritize_tasks` | ✓ | self, agent_pool, task_queue, criteria |
| `_default_prioritize` | ✓ | self, task_queue, criteria, agent_pool, explorer_context |
| `_get_knowledge_graph_context_score` | ✓ | self, task |
| `allocate_resources` | ✓ | self, agent_pool, task_queue, resource_limits |
| `_default_allocate` | ✓ | self, agent_pool, task_queue, resource_limits |
| `coordinate_arbiters` | ✓ | self, agent_pool, shared_context |
| `_default_coordinate` | ✓ | self, agent_pool, encrypted_context |
| `share_learning` | ✓ | self, agent, strategy |
| `rollback_allocation` | ✓ | self, assignments, agent_pool |
| `get_metrics` | ✓ | self |
| `explain_decision` | ✓ | self, decision_id |
| `stream_events` | ✓ | self, websocket |
| `compute_trust_score` | ✓ | self, context, user_id |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `safe_serialize` |  | obj |

**Constants:** `logger`, `TASK_PRIORITIZATION_COUNT`, `ALLOCATION_LATENCY`, `COORDINATION_SUCCESS`, `AGENT_ACTIVE_GAUGE`, `EXPLANATION_EVENTS`, `ERRORS_CRITICAL`, `PLUGIN_EXECUTION_LATENCY`, `DB_OPERATION_LATENCY`, `STRATEGY_REFRESH_COUNT`, `STRATEGY_REFRESH_SUCCESS`

---

## arbiter/event_bus_bridge.py
**Lines:** 369

### `EventBusBridge`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, mesh_to_arbiter_events, arbiter_to_mesh_events |
| `_init_mesh_bus` |  | self |
| `_init_arbiter_mqs` |  | self |
| `start` | ✓ | self |
| `stop` | ✓ | self |
| `_bridge_mesh_to_arbiter` | ✓ | self |
| `_bridge_arbiter_to_mesh` | ✓ | self |
| `_subscribe_and_forward` | ✓ | self, event_type, direction |
| `_forward_mesh_to_arbiter` | ✓ | self, event_type, data |
| `_forward_arbiter_to_mesh` | ✓ | self, event_type, data |
| `get_stats` |  | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `get_bridge` | ✓ |  |
| `stop_bridge` | ✓ |  |

**Constants:** `logger`

---

## arbiter/explainable_reasoner/adapters.py
**Lines:** 1476

### `LLMAdapter` (ABC)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, model_name, api_key, base_url, timeout, ...+2 |
| `_get_client` | ✓ | self |
| `rotate_key` | ✓ | self, new_key |
| `generate` | ✓ | self, prompt, multi_modal_data |
| `stream_generate` | ✓ | self, prompt, multi_modal_data |
| `health_check` | ✓ | self |
| `aclose` | ✓ | self |

### `OpenAIGPTAdapter` (LLMAdapter)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `_build_openai_messages` |  | self, prompt, multi_modal_data |
| `generate` | ✓ | self, prompt, multi_modal_data |
| `stream_generate` | ✓ | self, prompt, multi_modal_data |
| `health_check` | ✓ | self |

### `GeminiAPIAdapter` (LLMAdapter)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `_build_gemini_parts` |  | self, prompt, multi_modal_data |
| `generate` | ✓ | self, prompt, multi_modal_data |
| `stream_generate` | ✓ | self, prompt, multi_modal_data |
| `health_check` | ✓ | self |

### `AnthropicAdapter` (LLMAdapter)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `_build_anthropic_messages` |  | self, prompt, multi_modal_data |
| `generate` | ✓ | self, prompt, multi_modal_data |
| `stream_generate` | ✓ | self, prompt, multi_modal_data |
| `health_check` | ✓ | self |

### `LLMAdapterFactory`
**Attributes:** _default_base_urls

| Method | Async | Args |
|--------|-------|------|
| `register_adapter` |  | cls, name, adapter_class |
| `get_adapter` |  | cls, model_config_json |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `retry` |  | max_retries, initial_backoff_delay, exceptions_to_catch |

**Constants:** `_logger`, `STREAM_CHUNKS`, `HEALTH_CHECK_ERRORS`, `__all__`

---

## arbiter/explainable_reasoner/audit_ledger.py
**Lines:** 651

### `AuditLedgerClient`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, ledger_url, api_key, max_retries, initial_backoff_delay, ...+2 |
| `_get_client` | ✓ | self |
| `_send_event_with_retries` | ✓ | self, audit_record |
| `log_event` | ✓ | self, event_type, details, operator |
| `log_batch_events` | ✓ | self, events |
| `health_check` | ✓ | self |
| `rotate_key` | ✓ | self, new_key |
| `close` | ✓ | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `stop_after_attempt_from_self` |  | retry_state |

**Constants:** `_logger`, `AUDIT_SEND_LATENCY`, `AUDIT_ERRORS`, `AUDIT_BATCH_SIZE`, `AUDIT_RATE_LIMIT_HITS`

---

## arbiter/explainable_reasoner/explainable_reasoner.py
**Lines:** 2374

### `SensitiveValue`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, value |
| `get_actual_value` |  | self |
| `__str__` |  | self |
| `__repr__` |  | self |

### `ReasonerConfig` (BaseModel)
**Attributes:** model_config

| Method | Async | Args |
|--------|-------|------|
| `from_env` |  | cls |
| `get_public_config` |  | self |

### `ExplainableReasoner`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config, settings, prompt_strategy_name |
| `_initialize_history_manager` |  | self |
| `async_init` | ✓ | self |
| `_init_redis` | ✓ | self |
| `_init_history_db` | ✓ | self |
| `_run_model_readiness_test` | ✓ | self |
| `_run_history_pruner` | ✓ | self |
| `_initialize_models_async` | ✓ | self |
| `_load_single_model` | ✓ | self, model_cfg, is_reload |
| `_load_hf_pipeline_sync` |  | self, model_cfg |
| `_execute_in_thread` | ✓ | self, fn |
| `_get_next_pipeline` | ✓ | self |
| `_unload_model` | ✓ | self, model_key |
| `_attempt_reload_model` | ✓ | self, model_info, initial_delay, new_config |
| `_reload_model_with_retries` | ✓ | self, model_info, initial_delay, new_config |
| `_truncate_prompt_if_needed` | ✓ | self, prompt, tokenizer, max_new_tokens |
| `_rate_key_extractor` |  | self |
| `_async_generate_text` | ✓ | self, prompt, max_length, temperature, multi_modal_data, ...+1 |
| `_generate_text_sync` |  | self, pipeline_info, prompt, max_new_tokens, temperature |
| `_prepare_prompt_with_history` | ✓ | self, prompt_type, context, query, session_id |
| `_validate_session` | ✓ | self, session_id |
| `_validate_request_inputs` | ✓ | self, query, context, session_id |
| `_perform_inference_with_fallback` | ✓ | self, task_type, prompt, sanitized_query, sanitized_context, ...+2 |
| `_finalize_request` | ✓ | self, task_type, sanitized_query, sanitized_context, response_text, ...+5 |
| `_handle_request` | ✓ | self, task_type, query, context, session_id, ...+2 |
| `_build_history_string` |  | self, history_entries, session_id |
| `explain` | ✓ | self, query, context |
| `reason` | ✓ | self, query, context |
| `batch_explain` | ✓ | self, queries, contexts |
| `health_check` | ✓ | self |
| `get_history` | ✓ | self, limit, session_id |
| `clear_history` | ✓ | self, session_id |
| `purge_history` | ✓ | self, operator_id |
| `export_history` | ✓ | self, output_format, operator_id |
| `shutdown` | ✓ | self |

### `PlugInKind`
**Attributes:** AI_ASSISTANT, FIX

### `ExecuteInput` (BaseModel)

| Method | Async | Args |
|--------|-------|------|
| `validate_for_action` |  | cls, data |

### `ExplainableReasonerPlugin` (ExplainableReasoner)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, settings |
| `initialize` | ✓ | self |
| `execute` | ✓ | self, action |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `plugin` |  | kind, name, description, version |

**Constants:** `__version__`, `TRANSFORMERS_AVAILABLE`, `log_file_path`, `log_handler`, `_logger`, `logger`

---

## arbiter/explainable_reasoner/history_manager.py
**Lines:** 1432

### `BaseHistoryManager` (ABC)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, max_history_size, retention_days, audit_client |
| `_pre_add_entry_checks` | ✓ | self, entry |
| `_encrypt` |  | self, text |
| `_decrypt` |  | self, encrypted_text |
| `_record_op_success` |  | self, operation, start_time |
| `_record_op_error` |  | self, operation, start_time, e |
| `_log_audit_event` | ✓ | self, event_type, details, operator |
| `init_db` | ✓ | self |
| `add_entry` | ✓ | self, entry |
| `add_entries_batch` | ✓ | self, entries |
| `get_entries` | ✓ | self, limit, session_id |
| `get_size` | ✓ | self |
| `prune_old_entries` | ✓ | self |
| `clear` | ✓ | self, session_id |
| `purge_all` | ✓ | self, operator_id |
| `export_history` | ✓ | self, output_format, operator_id |
| `aclose` | ✓ | self |

### `SQLiteHistoryManager` (BaseHistoryManager)
**Attributes:** _backend_name

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, db_path, max_history_size, retention_days, audit_client |
| `init_db` | ✓ | self |
| `add_entry` | ✓ | self, entry |
| `add_entries_batch` | ✓ | self, entries |
| `get_entries` | ✓ | self, limit, session_id |
| `get_size` | ✓ | self |
| `prune_old_entries` | ✓ | self |
| `clear` | ✓ | self, session_id |
| `purge_all` | ✓ | self, operator_id |
| `export_history` | ✓ | self, output_format, operator_id |
| `aclose` | ✓ | self |

### `PostgresHistoryManager` (BaseHistoryManager)
**Attributes:** _backend_name

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, db_url, max_history_size, retention_days, audit_client |
| `init_db` | ✓ | self |
| `add_entry` | ✓ | self, entry |
| `add_entries_batch` | ✓ | self, entries |
| `get_entries` | ✓ | self, limit, session_id |
| `get_size` | ✓ | self |
| `prune_old_entries` | ✓ | self |
| `clear` | ✓ | self, session_id |
| `purge_all` | ✓ | self, operator_id |
| `export_history` | ✓ | self, output_format, operator_id |
| `aclose` | ✓ | self |

### `RedisHistoryManager` (BaseHistoryManager)
**Attributes:** _backend_name

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, redis_url, max_history_size, retention_days, audit_client |
| `init_db` | ✓ | self |
| `add_entry` | ✓ | self, entry |
| `add_entries_batch` | ✓ | self, entries |
| `get_entries` | ✓ | self, limit, session_id |
| `get_size` | ✓ | self |
| `prune_old_entries` | ✓ | self |
| `clear` | ✓ | self, session_id |
| `purge_all` | ✓ | self, operator_id |
| `export_history` | ✓ | self, output_format, operator_id |
| `aclose` | ✓ | self |

**Constants:** `logger`

---

## arbiter/explainable_reasoner/metrics.py
**Lines:** 232

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `get_or_create_metric` |  | metric_type, name, description, labelnames, buckets |
| `initialize_metrics` |  |  |
| `get_metrics_content` |  |  |

**Constants:** `_metrics_logger`, `_metrics_logger`, `METRICS_NAMESPACE`, `PROMETHEUS_MULTIPROC_DIR`

---

## arbiter/explainable_reasoner/prompt_strategies.py
**Lines:** 528

### `PromptStrategy` (ABC)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, logger_instance |
| `create_explanation_prompt` | ✓ | self, context, goal, history_str |
| `create_reasoning_prompt` | ✓ | self, context, goal, history_str |

### `DefaultPromptStrategy` (PromptStrategy)

| Method | Async | Args |
|--------|-------|------|
| `create_explanation_prompt` | ✓ | self, context, goal, history_str |
| `create_reasoning_prompt` | ✓ | self, context, goal, history_str |

### `ConcisePromptStrategy` (PromptStrategy)

| Method | Async | Args |
|--------|-------|------|
| `create_explanation_prompt` | ✓ | self, context, goal, history_str |
| `create_reasoning_prompt` | ✓ | self, context, goal, history_str |

### `VerbosePromptStrategy` (PromptStrategy)

| Method | Async | Args |
|--------|-------|------|
| `create_explanation_prompt` | ✓ | self, context, goal, history_str |
| `create_reasoning_prompt` | ✓ | self, context, goal, history_str |

### `StructuredPromptStrategy` (PromptStrategy)

| Method | Async | Args |
|--------|-------|------|
| `create_explanation_prompt` | ✓ | self, context, goal, history_str |
| `create_reasoning_prompt` | ✓ | self, context, goal, history_str |

### `PromptStrategyFactory`

| Method | Async | Args |
|--------|-------|------|
| `register_strategy` |  | cls, name, strategy_class |
| `get_strategy` |  | cls, name, logger_instance |
| `list_strategies` |  | cls |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_truncate_context` |  | context, max_len |

**Constants:** `_prompt_strategy_logger`

---

## arbiter/explainable_reasoner/reasoner_config.py
**Lines:** 172

### `SensitiveValue`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, value |
| `get_actual_value` |  | self |
| `__str__` |  | self |
| `__repr__` |  | self |

### `ReasonerConfig` (BaseModel)
**Attributes:** model_config

| Method | Async | Args |
|--------|-------|------|
| `from_env` |  | cls |
| `validate_dependencies` |  | self |
| `get_public_config` |  | self |
| `from_file` |  | cls, file_path |

**Constants:** `logger`

---

## arbiter/explainable_reasoner/reasoner_errors.py
**Lines:** 255

### `ReasonerErrorCode`
**Attributes:** GENERIC_ERROR, UNEXPECTED_ERROR, INVALID_INPUT, TIMEOUT, SERVICE_UNAVAILABLE, CONFIGURATION_ERROR, RATE_LIMIT_EXCEEDED, CUDA_OOM, MODEL_NOT_INITIALIZED, CONTEXT_SIZE_EXCEEDED, MODEL_INFERENCE_FAILED, MODEL_LOAD_FAILED, MODEL_OUTPUT_INVALID, PROMPT_VALIDATION_FAILED, CONTEXT_SANITIZATION_FAILED

### `ReasonerError` (Exception)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, code, original_exception |
| `__repr__` |  | self |
| `to_api_response` |  | self, include_traceback |
| `to_json` |  | self, indent |

**Constants:** `logger`

---

## arbiter/explainable_reasoner/utils.py
**Lines:** 496

### `DummyCounter`

| Method | Async | Args |
|--------|-------|------|
| `labels` |  | self |
| `inc` |  | self, value |

### `DummyHistogram`

| Method | Async | Args |
|--------|-------|------|
| `labels` |  | self |
| `observe` |  | self, value |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_format_multimodal_for_prompt` |  | data |
| `_sanitize_context` | ✓ | context, config |
| `_simple_text_sanitize` |  | text, max_length |
| `_rule_based_fallback` |  | query, context, mode |
| `rate_limited` |  | calls_per_second, key_extractor |
| `redact_pii` |  | data |

**Constants:** `METRICS`, `_utils_logger`, `P`

---

## arbiter/explorer.py
**Lines:** 1459

### `ExperimentExecutionError` (Exception)

### `ExperimentLog` (Base)
**Attributes:** __tablename__, id, data, timestamp

### `LogDB`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config |
| `save_experiment_log` | ✓ | self, log_entry |
| `get_experiment_log` | ✓ | self, experiment_id |
| `find_experiments` | ✓ | self, query |
| `health_check` | ✓ | self |

### `MutatedAgent`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, original_name, generation |
| `test_in_sandbox` | ✓ | self |

### `MySandboxEnv`

| Method | Async | Args |
|--------|-------|------|
| `evaluate` | ✓ | self, variant, metric |
| `test_agent` | ✓ | self, agent |

### `RealSandboxAdapter`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, backend, workdir |
| `evaluate` | ✓ | self, variant, metric |
| `test_agent` | ✓ | self, agent |
| `get_stats` |  | self |

### `Explorer`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, sandbox_env, log_db, config |
| `__aenter__` | ✓ | self |
| `__aexit__` | ✓ | self, exc_type, exc_val, exc_tb |
| `check_permission` |  | self, role, permission |
| `execute` | ✓ | self, action |
| `run_experiment` | ✓ | self, experiment_config |
| `get_status` | ✓ | self |
| `discover_urls` | ✓ | self, html_discovery_dir |
| `crawl_urls` | ✓ | self, urls |
| `explore_and_fix` | ✓ | self, arbiter, fix_paths |
| `replay_experiment` | ✓ | self, experiment_id, new_sandbox_env |
| `_run_ab_test` | ✓ | self, experiment_id, variant_a_agent, variant_b_agent, runs, ...+1 |
| `_run_evolution_experiment` | ✓ | self, experiment_id, initial_population, generations |
| `_create_mutated_agent` |  | self, base_agent, generation |
| `_generate_experiment_id` |  | self, kind |
| `_calculate_metrics` |  | self, runs_data, metrics |
| `_compare_variants` |  | self, metrics_a, metrics_b |

### `MockLogDB`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `save_experiment_log` | ✓ | self, log_entry |
| `get_experiment_log` | ✓ | self, experiment_id |
| `find_experiments` | ✓ | self, query |
| `health_check` | ✓ | self |

### `ArbiterExplorer`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, sandbox_env, log_db |
| `run_ab_test` | ✓ | self, experiment_name, variant_a, variant_b, num_runs, ...+1 |
| `run_evolutionary_experiment` | ✓ | self, experiment_name, initial_agent, num_generations, population_size, ...+1 |
| `_run_experiment` | ✓ | self, name, experiment_func |
| `_log_experiment` | ✓ | self, entry |
| `_calculate_metrics` |  | self, results |
| `_compare_variants` |  | self, metrics_a, metrics_b |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_serialize_random_state` |  | state |
| `_deserialize_random_state` |  | state_str |

**Constants:** `Base`, `tracer`, `logger`, `explorer_ops_total`, `explorer_errors_total`

---

## arbiter/feedback.py
**Lines:** 1043

### `FeedbackType`
**Attributes:** BUG_REPORT, FEATURE_REQUEST, GENERAL, APPROVAL, DENIAL, IMPROVEMENT, ISSUE

### `FeedbackLog` (Base)
**Attributes:** __tablename__, decision_id, data, timestamp

### `SQLiteClient`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, db_file |
| `connect` | ✓ | self |
| `disconnect` | ✓ | self |
| `save_feedback_entry` | ✓ | self, entry |
| `get_feedback_entries` | ✓ | self, query |
| `update_feedback_entry` | ✓ | self, query, updates |

### `FeedbackManager`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, db_client, config, log_file, max_log_size |
| `connect_db` | ✓ | self |
| `disconnect_db` | ✓ | self |
| `_ensure_log_file_exists` |  | self |
| `_sync_and_filter_to_logfile` | ✓ | self |
| `add_feedback` | ✓ | self, decision_id, feedback |
| `record_metric` | ✓ | self, name, value, tags |
| `log_error` | ✓ | self, error_info |
| `add_user_feedback` | ✓ | self, feedback |
| `record_feedback` | ✓ | self, user_id, feedback_type, details |
| `_purge_metrics_and_sync_loop` | ✓ | self |
| `get_summary` | ✓ | self |
| `log_approval_request` | ✓ | self, decision_id, decision_context |
| `log_approval_response` | ✓ | self, decision_id, response |
| `get_pending_approvals` | ✓ | self |
| `get_feedback_by_decision_id` | ✓ | self, decision_id |
| `get_approval_stats` | ✓ | self, start_date, end_date, group_by_reviewer, group_by_decision_type |
| `start_async_services` | ✓ | self |
| `stop_async_services` | ✓ | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_get_arbiter_registry` |  |  |
| `_get_or_create_metric` |  | metric_class, name, documentation, labelnames, buckets |
| `check_permission` |  | role, permission |
| `receive_human_feedback` | ✓ | feedback |
| `_ensure_feedback_plugin_registered` |  |  |

**Constants:** `POSTGRES_AVAILABLE`, `IS_PRODUCTION`, `tracer`, `logger`, `_metrics_lock`, `feedback_received_total`, `feedback_errors_total`, `feedback_metrics_recorded_total`, `feedback_processing_time`, `human_in_loop_approvals`, `human_in_loop_denials`, `last_feedback_timestamp`, `feedback_ops_total`, `_feedback_plugin_registered`

---

## arbiter/file_watcher.py
**Lines:** 1584

### `SMTPConfig` (BaseModel)

### `AlerterConfig` (BaseModel)

### `AWSConfig` (BaseModel)

### `LLMConfig` (BaseModel)

| Method | Async | Args |
|--------|-------|------|
| `validate_provider` |  | cls, v |

### `DeployConfig` (BaseModel)

### `ReportingConfig` (BaseModel)

### `CacheConfig` (BaseModel)

### `MetricsConfig` (BaseModel)

### `HealthConfig` (BaseModel)

### `WatchConfig` (BaseModel)

### `ApiConfig` (BaseModel)

### `Config` (BaseModel)

### `CodeChangeHandler` (FileSystemEventHandler)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, semaphore |
| `process_file` | ✓ | self, filepath |
| `on_modified` |  | self, event |
| `on_created` |  | self, event |
| `on_deleted` |  | self, event |
| `on_moved` |  | self, event |

### `MetricsAndHealthServer`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config |
| `start` | ✓ | self |
| `stop` | ✓ | self |
| `prometheus_metrics_handler` | ✓ | self, request |
| `health_check_handler` | ✓ | self, request |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_get_or_create_metric` |  | metric_class, name, doc, labelnames, buckets |
| `_safe_int` |  | value, default |
| `_safe_float` |  | value, default |
| `load_config_with_env` |  | config_path |
| `is_valid_file` |  | filename |
| `read_file` | ✓ | filepath |
| `get_cached_summary` | ✓ | filename, content |
| `cache_summary` | ✓ | filename, content, summary |
| `log_audit` | ✓ | event, details |
| `summarize_code` | ✓ | filename, code |
| `send_to_api` | ✓ | filename, content, summary |
| `send_email_alert` | ✓ | subject, body |
| `upload_to_s3` | ✓ | filename, content |
| `send_notification` | ✓ | filename, status, summary |
| `trigger_deployment` | ✓ | filename, content |
| `write_changelog` | ✓ | filename, summary, old_content, new_content |
| `summarize_code_changes` | ✓ | diff, prompt_template |
| `compare_diffs` |  | old, new |
| `batch_process` | ✓ | semaphore |
| `start_watch` | ✓ | config_path |
| `watch` | ✓ | config_path |
| `register_plugin` |  |  |
| `run` |  | config_path |
| `batch` |  | config_path |
| `send_slack_alert` | ✓ | message, webhook_url |
| `send_pagerduty_alert` | ✓ | message, routing_key |
| `deploy_code` | ✓ | cmd |
| `notify_changes` | ✓ | filename, diff, summary, deploy_result |
| `process_file` | ✓ | path |

**Constants:** `logger`, `logger`, `processed_files`, `errors`, `deployments`, `notifications`, `emails_sent`, `SUMMARY_LATENCY`, `_METRICS_LOCK`, `lock_file`, `app`, `start_time`

---

## arbiter/human_loop.py
**Lines:** 1357

### `FeedbackManager`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, db_client |
| `log_approval_request` | ✓ | self, decision_id, decision_context |
| `log_approval_response` | ✓ | self, decision_id, response |
| `record_metric` | ✓ | self, metric_name, value, tags |
| `log_error` | ✓ | self, error_details |

### `HumanFeedbackSchema` (BaseModel)

### `DecisionRequestSchema` (BaseModel)
**Attributes:** model_config

### `HumanInLoopConfig` (BaseModel)

| Method | Async | Args |
|--------|-------|------|
| `validate_production_email_config` |  | cls, values |
| `validate_database_url_in_production` |  | cls, v, info |
| `validate_salt_in_production` |  | self |

### `WebSocketManager`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, max_connections |
| `start` | ✓ | self |
| `stop` | ✓ | self |
| `register_connection` | ✓ | self, connection_id, websocket, metadata |
| `unregister_connection` | ✓ | self, connection_id |
| `send_json` | ✓ | self, data, connection_id |
| `_broadcast_worker` | ✓ | self |
| `get_connection_count` |  | self |
| `get_connection_stats` |  | self |
| `ping_all` | ✓ | self |

### `HumanInLoop`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config, feedback_manager, websocket_manager, logger, ...+4 |
| `__aenter__` | ✓ | self |
| `__aexit__` | ✓ | self, exc_type, exc_val, exc_tb |
| `check_permission` |  | self, role, permission |
| `_handle_hook` | ✓ | self, hook, event_data |
| `request_approval` | ✓ | self, decision |
| `_get_notification_tasks` |  | self, decision_id, context, role |
| `receive_human_feedback` | ✓ | self, feedback |
| `_validate_user_signature` | ✓ | self, user_id, signature, decision_id, approved, ...+2 |
| `_send_email_approval` | ✓ | self, decision_id, context, recipient |
| `_send_sync_email` |  | self, config, recipient, msg |
| `_post_slack_approval` | ✓ | self, decision_id, context |
| `_notify_ui_approval` | ✓ | self, decision_id, context |
| `_mock_user_approval` | ✓ | self, decision_id, decision_context |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `get_human_approval` | ✓ | decision_id, decision_context |

**Constants:** `SECRET_SALT`, `tracer`, `logger`, `human_in_loop_approvals`, `human_in_loop_denials`, `human_loop_feedback_total`

---

## arbiter/knowledge_graph/config.py
**Lines:** 554

### `SensitiveValue` (RootModel[str])

| Method | Async | Args |
|--------|-------|------|
| `__str__` |  | self |
| `__repr__` |  | self |
| `get_actual_value` |  | self |
| `__hash__` |  | self |
| `__eq__` |  | self, other |
| `__get_pydantic_json_schema__` |  | cls, core_schema, handler |
| `model_dump` |  | self |
| `model_dump_json` |  | self |

### `MetaLearningConfig` (BaseSettings)
**Attributes:** model_config

| Method | Async | Args |
|--------|-------|------|
| `validate_file_paths` |  | cls, v, info |
| `handle_sensitive_values` |  | cls, v |
| `validate_kafka_settings` |  | self |
| `validate_redis_url` |  | cls, v |
| `validate_http_endpoints` |  | cls, v |
| `reload_config` |  | self |
| `_reload_from_etcd` |  | self |

### `MultiModalData` (BaseModel)
**Attributes:** model_config

| Method | Async | Args |
|--------|-------|------|
| `model_dump_for_log` |  | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `load_persona_dict` |  |  |

**Constants:** `logger`, `env`, `SensitiveString`

---

## arbiter/knowledge_graph/core.py
**Lines:** 1636

### `StateBackend` (ABC)

| Method | Async | Args |
|--------|-------|------|
| `load_state` | ✓ | self, session_id |
| `save_state` | ✓ | self, session_id, state |

### `RedisStateBackend` (StateBackend)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, redis_url |
| `init_client` | ✓ | self |
| `save_state` | ✓ | self, session_id, state |
| `load_state` | ✓ | self, session_id |

### `PostgresStateBackend` (StateBackend)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, db_url |
| `init_client` | ✓ | self |
| `save_state` | ✓ | self, session_id, state |
| `load_state` | ✓ | self, session_id |

### `InMemoryStateBackend` (StateBackend)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `load_state` | ✓ | self, session_id |
| `save_state` | ✓ | self, session_id, state |

### `MetaLearning`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `log_correction` |  | self, input_text, initial_response, corrected_response |
| `train_model` |  | self |
| `apply_correction` |  | self, response, input_text |
| `persist` |  | self |
| `load` |  | self |

### `CollaborativeAgent`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, agent_id, session_id, llm_config, persona, ...+4 |
| `_get_llm` |  | self |
| `_get_fallback_llm` |  | self |
| `load_state` | ✓ | self, operator_id |
| `save_state` | ✓ | self, operator_id |
| `set_persona` | ✓ | self, persona, operator_id |
| `_call_llm_with_retries` | ✓ | self, llm_instance, messages, provider, model |
| `predict` | ✓ | self, user_input, context, timeout, operator_id |

### `AgentTeam`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, session_id, llm_config, state_backend, meta_learning |
| `delegate_task` | ✓ | self, initial_input, context, timeout, operator_id |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `get_transcript` |  | memory |
| `get_or_create_agent` | ✓ | session_id, llm_config, state_backend, meta_learning, prompt_strategy, ...+2 |
| `setup_conversation` | ✓ | llm, persona, language |

**Constants:** `AuditLedgerClient`, `RedisClient`, `tracer`

---

## arbiter/knowledge_graph/multimodal.py
**Lines:** 503

### `MultiModalProcessor` (ABC)

| Method | Async | Args |
|--------|-------|------|
| `summarize` | ✓ | self, item |

### `DefaultMultiModalProcessor` (MultiModalProcessor)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, logger |
| `_ensure_models_initialized` | ✓ | self |
| `summarize` | ✓ | self, item |
| `_process_image` | ✓ | self, item |
| `_process_audio` | ✓ | self, item |
| `_process_video` | ✓ | self, item |
| `_process_text_file` | ✓ | self, item |
| `_process_pdf_file` | ✓ | self, item |

---

## arbiter/knowledge_graph/prompt_strategies.py
**Lines:** 242

### `PromptStrategy` (ABC)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, logger |
| `get_history_transcript` |  | self |
| `create_agent_prompt` | ✓ | self, base_template, history, user_input, persona, ...+2 |

### `DefaultPromptStrategy` (PromptStrategy)

| Method | Async | Args |
|--------|-------|------|
| `create_agent_prompt` | ✓ | self, base_template, history, user_input, persona, ...+2 |

### `ConcisePromptStrategy` (PromptStrategy)

| Method | Async | Args |
|--------|-------|------|
| `create_agent_prompt` | ✓ | self, base_template, history, user_input, persona, ...+2 |
| `_truncate_history` |  | self, history, max_chars |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_load_templates` |  |  |

**Constants:** `logger`, `PROMPT_TEMPLATES`, `PROMPT_TEMPLATE_FILE`, `PROMPT_TEMPLATES_FALLBACK`, `BASE_AGENT_PROMPT_TEMPLATE`, `REFLECTION_PROMPT_TEMPLATE`, `CRITIQUE_PROMPT_TEMPLATE`, `SELF_CORRECT_PROMPT_TEMPLATE`

---

## arbiter/knowledge_graph/utils.py
**Lines:** 593

### `ContextVarFormatter` (logging.Formatter)

| Method | Async | Args |
|--------|-------|------|
| `format` |  | self, record |

### `AgentErrorCode` (str, Enum)
**Attributes:** UNEXPECTED_ERROR, TIMEOUT, INVALID_INPUT, UNSUPPORTED_PERSONA, STATE_LOAD_FAILED, STATE_SAVE_FAILED, LLM_INIT_FAILED, LLM_CALL_FAILED, LLM_RATE_LIMITED, LLM_KEY_INVALID, LLM_UNSUPPORTED_PROVIDER, LLM_BAD_RESPONSE, LIB_IMPORT_FAILED, MM_PROCESSING_FAILED, MM_UNSUPPORTED_DATA

### `AgentCoreException` (Exception)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, code, original_exception |

### `AuditLedgerClient`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, ledger_url |
| `log_event` | ✓ | self, event_type, details, operator |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `get_or_create_metric` |  | metric_type, name, documentation, labelnames, buckets |
| `datetime_now` |  |  |
| `async_with_retry` | ✓ | func, retries, delay, backoff, log_context |
| `_get_pii_sensitive_keys` |  |  |
| `_get_pii_sensitive_patterns` |  |  |
| `_redact_sensitive_pii` |  | key, value |
| `_sanitize_context` | ✓ | context, max_size_bytes, redact_keys, redact_patterns, max_nesting_depth, ...+2 |
| `_sanitize_user_input` |  | user_input |

**Constants:** `tracer`, `trace_id_var`, `logger`, `AGENT_METRICS`, `_PII_SENSITIVE_KEYS`, `_PII_SENSITIVE_PATTERNS`, `audit_ledger_client`

---

## arbiter/knowledge_loader.py
**Lines:** 391

### `KnowledgeLoader`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, knowledge_data_path, master_knowledge_file |
| `get_knowledge` |  | self |
| `save_current_knowledge` |  | self |
| `load_all` |  | self |
| `inject_to_arbiter` |  | self, arbiter_instance |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `merge_dict` |  | orig, new |
| `save_knowledge_atomic` |  | filename, knowledge_data |
| `_load_knowledge_sync` |  | filename |
| `load_knowledge` | ✓ | filename |

**Constants:** `logger`

---

## arbiter/learner/__init__.py
**Lines:** 58

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `setup_module` |  |  |

**Constants:** `__version__`, `logger`, `required_envs`, `missing`, `__all__`

---

## arbiter/learner/audit.py
**Lines:** 326

### `CircuitBreaker`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, failure_threshold, cooldown_seconds, name |
| `record_failure` | ✓ | self |
| `record_success` | ✓ | self |
| `can_proceed` | ✓ | self |

### `MerkleTree`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, leaves |
| `_hash` |  | self, data |
| `_build_tree_levels` |  | self, leaves |
| `get_root` |  | self |
| `get_proof` |  | self, index |
| `serialize` |  | self |
| `deserialize` |  | data |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_get_circuit_breaker_metric` |  |  |
| `_persist_knowledge_inner` | ✓ | db, circuit_breaker, domain, key, value_with_metadata, ...+4 |
| `persist_knowledge` | ✓ | db, circuit_breaker, domain, key, value_with_metadata, ...+4 |
| `_persist_knowledge_batch_inner` | ✓ | db, circuit_breaker, entries, user_id |
| `persist_knowledge_batch` | ✓ | db, circuit_breaker, entries, user_id |

**Constants:** `logger`, `circuit_breaker_state`

---

## arbiter/learner/core.py
**Lines:** 1640

### `LearningRecord` (BaseModel)

### `LearnerArbiterHelper`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |

### `Learner`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, arbiter, redis, db_url, merkle_tree_class |
| `start` | ✓ | self |
| `_run_self_audit` | ✓ | self |
| `start_self_audit` | ✓ | self |
| `stop_self_audit` | ✓ | self |
| `learn_new_thing` | ✓ | self, domain, key, value, user_id, ...+7 |
| `_process_learn` | ✓ | self, domain, key, value, user_id, ...+2 |
| `_get_previous_value` | ✓ | self, previous_entry, domain |
| `learn_batch` | ✓ | self, facts, user_id, source, write_to_disk, ...+3 |
| `_prepare_and_process_single_fact_for_batch` | ✓ | self, domain, key, value, user_id, ...+2 |
| `forget_fact` | ✓ | self, domain, key, user_id, reason, ...+4 |
| `retrieve_knowledge` | ✓ | self, domain, key, decrypt |
| `_process_retrieved_data` | ✓ | self, data, domain, decrypt |
| `_compute_diff` |  | self, old_value, new_value |

**Constants:** `logger`, `tracer`, `meter`

---

## arbiter/learner/encryption.py
**Lines:** 334

### `ArbiterConfig`
**Attributes:** ENCRYPTION_KEYS, VALID_DOMAIN_PATTERN, DEFAULT_SCHEMA_DIR, ENCRYPTED_DOMAINS, KNOWLEDGE_REDIS_TTL_SECONDS, MAX_LEARN_RETRIES, SELF_AUDIT_INTERVAL_SECONDS

| Method | Async | Args |
|--------|-------|------|
| `load_keys` |  | cls |
| `rotate_keys` | ✓ | cls, new_version |
| `_persist_key_to_ssm` |  | cls, version, key |
| `_delete_key_from_ssm` |  | cls, version |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `encrypt_value` | ✓ | value, cipher, key_id |
| `decrypt_value` | ✓ | encrypted, ciphers |

**Constants:** `logger`, `key_rotation_counter`, `learn_error_counter`

---

## arbiter/learner/explanations.py
**Lines:** 445

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_load_prompt_templates` |  |  |
| `_generate_text_with_retry` | ✓ | client, prompt |
| `generate_explanation` | ✓ | learner, domain, key, new_value, old_value, ...+1 |
| `record_explanation_quality` | ✓ | learner, domain, key, version, score |
| `get_explanation_quality_report` |  | learner, domain |

**Constants:** `logger`, `tracer`, `explanation_llm_latency_seconds`, `explanation_llm_failure_total`, `EXPLANATION_CACHE_REDIS_TTL`, `EXPLANATION_PROMPT_TEMPLATE_PATH`, `EXPLANATION_LLM_TIMEOUT_SECONDS`, `EXPLANATION_PROMPT_TEMPLATES`

---

## arbiter/learner/fuzzy.py
**Lines:** 417

### `FuzzyParser` (Protocol)

| Method | Async | Args |
|--------|-------|------|
| `parse` | ✓ | self, text, context |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `load_parser_priorities` |  |  |
| `_learn_batch_with_retry` | ✓ | learner, facts, user_id, source |
| `process_unstructured_data` | ✓ | learner, text, domain_hint, user_id, source, ...+1 |
| `register_fuzzy_parser_hook` |  | learner, parser, priority |
| `register_fuzzy_parser_hook_async` | ✓ | learner, parser, priority |

**Constants:** `logger`, `tracer`, `fuzzy_parser_success_total`, `fuzzy_parser_failure_total`, `fuzzy_parser_latency_seconds`, `PARSER_TIMEOUT_SECONDS`, `PARSER_MAX_CONCURRENT`, `PARSER_PRIORITIES`

---

## arbiter/learner/metrics.py
**Lines:** 246

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_get_or_create_metric` |  | metric_class, name, documentation, labelnames, buckets |
| `get_labels` |  |  |

**Constants:** `logger`, `learner_info`, `learn_counter`, `learn_error_counter`, `learn_duration_seconds`, `learn_duration_summary`, `forget_counter`, `forget_duration_seconds`, `forget_duration_summary`, `retrieve_hit_miss`, `audit_events_total`, `circuit_breaker_state`, `audit_failure_total`, `explanation_llm_latency_seconds`, `explanation_llm_failure_total`, ...+5 more

---

## arbiter/learner/validation.py
**Lines:** 365

### `DomainNotFoundError` (Exception)

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `validate_data` | ✓ | learner, domain, value |
| `register_validation_hook` |  | learner, domain, hook_func |
| `reload_schemas` | ✓ | learner, directory |

**Constants:** `logger`, `tracer`, `validation_success_total`, `validation_failure_total`, `validation_latency_seconds`, `schema_reload_total`, `schema_reload_latency_seconds`, `SCHEMA_RELOAD_RETRIES`, `SCHEMA_CACHE_TTL_SECONDS`, `SCHEMA_DIR_PERMISSION_CHECK`

---

## arbiter/logging_utils.py
**Lines:** 615

### `LogLevel` (Enum)
**Attributes:** DEBUG, INFO, WARNING, ERROR, CRITICAL, AUDIT, SECURITY

### `PIIRedactorFilter` (logging.Filter)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, patterns, redaction_callback, enable_metrics, enable_audit, ...+2 |
| `filter` |  | self, record |
| `_redact_text` |  | self, text |
| `_redact_args` |  | self, args |
| `_redact_value` |  | self, value |
| `_update_metrics` |  | self, pii_type |
| `_audit_redaction` |  | self, record, original, redacted |
| `get_metrics` |  | self |
| `clear_cache` |  | self |

### `StructuredFormatter` (logging.Formatter)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, include_traceback |
| `format` |  | self, record |

### `AuditLogger`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, name, log_file |
| `log_event` |  | self, event_type |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `get_logger` |  | name, level, enable_pii_filter, structured |
| `logging_context` |  |  |
| `configure_logging` |  | level, log_file, structured, enable_pii_filter, audit_file |
| `get_redaction_patterns` |  |  |
| `redact_text` |  | text |

**Constants:** `_context`, `__all__`

---

## arbiter/message_queue_service.py
**Lines:** 1166

### `MessageQueueServiceError` (Exception)

### `BackendNotAvailableError` (MessageQueueServiceError)

### `SerializationError` (MessageQueueServiceError)

### `DecryptionError` (MessageQueueServiceError)

### `PermissionError` (MessageQueueServiceError)

### `MessageQueueService`
**Attributes:** SUPPORTED_BACKENDS

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, backend_type, redis_url, kafka_bootstrap_servers, encryption_key, ...+16 |
| `__aenter__` | ✓ | self |
| `__aexit__` | ✓ | self, exc_type, exc_val, exc_tb |
| `connect` | ✓ | self |
| `disconnect` | ✓ | self |
| `healthcheck` | ✓ | self |
| `_get_topic_name` |  | self, event_type, is_dlq |
| `_encrypt_payload` |  | self, payload |
| `_decrypt_payload` |  | self, encrypted_payload |
| `_serialize_message` |  | self, data |
| `_deserialize_message` |  | self, data_bytes |
| `check_permission` |  | self, role, permission |
| `rotate_encryption_key` | ✓ | self, new_key |
| `publish` | ✓ | self, event_type, data, is_critical, omnicore |
| `subscribe` | ✓ | self, event_type, handler |
| `_memory_consumer` | ✓ | self, event_type, handler |
| `_redis_stream_consumer` | ✓ | self, stream_name, handler |
| `_kafka_consumer` | ✓ | self, topic_name, handler |
| `_send_to_dlq` | ✓ | self, event_type, original_data, reason |
| `replay_dlq` | ✓ | self, event_type |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_get_or_create_metric` |  | metric_type, name, documentation, labelnames, buckets |

**Constants:** `_metrics_lock`, `MQ_PUBLISH_TOTAL`, `MQ_CONSUME_TOTAL`, `MQ_PUBLISH_LATENCY`, `MQ_CONSUME_LATENCY`, `MQ_DLQ_TOTAL`, `MQ_ENCRYPTION_ERRORS`, `MQ_CONNECTION_STATUS`, `logger`

---

## arbiter/meta_learning_orchestrator/audit_utils.py
**Lines:** 629

### `AuditEvent` (BaseModel)

### `AuditUtils`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, log_path, rotation_size_mb, max_files |
| `__aenter__` | ✓ | self |
| `__aexit__` | ✓ | self, exc_type, exc_val, exc_tb |
| `_setup_log_file` |  | self |
| `_rotate_log` | ✓ | self |
| `_write_audit_event` | ✓ | self, event |
| `_send_to_kafka` | ✓ | self, event |
| `_get_last_hash` | ✓ | self |
| `hash_event` |  | self, event_data, prev_hash |
| `_sign_hash` |  | self, digest |
| `_verify_signature` |  | self, digest, signature |
| `get_current_timestamp` |  | self |
| `add_audit_event` | ✓ | self, event_type, details |
| `validate_audit_chain` | ✓ | self |
| `_validate_file_chain` | ✓ | self |
| `_validate_kafka_chain` | ✓ | self |

**Constants:** `AUDIT_LOG_PATH`, `AUDIT_ENCRYPTION_KEY`, `AUDIT_LOG_ROTATION_SIZE_MB`, `AUDIT_LOG_MAX_FILES`, `AUDIT_RETENTION_DAYS`, `USE_KAFKA_AUDIT`, `KAFKA_BROKERS`, `KAFKA_TOPIC`, `logger`, `ML_AUDIT_HASH_MISMATCH`, `ML_AUDIT_EVENTS_TOTAL`, `ML_AUDIT_SIGNATURE_MISMATCH`, `ML_AUDIT_ROTATIONS_TOTAL`, `ML_AUDIT_CRYPTO_ERRORS`, `AUDIT_VALIDATION_LATENCY`

---

## arbiter/meta_learning_orchestrator/clients.py
**Lines:** 543

### `_BaseHTTPClient`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, endpoint, session |
| `__aenter__` | ✓ | self |
| `__aexit__` | ✓ | self, exc_type, exc_val, exc_tb |
| `close` | ✓ | self |
| `_request_with_redaction` | ✓ | self, method, url, data |

### `MLPlatformClient` (_BaseHTTPClient)

| Method | Async | Args |
|--------|-------|------|
| `trigger_training_job` | ✓ | self, training_data_path, params |
| `get_training_job_status` | ✓ | self, job_id |
| `train_model` | ✓ | self, training_data |
| `get_training_status` | ✓ | self, job_id |
| `evaluate_model` | ✓ | self, model_id, eval_data |
| `deploy_model` | ✓ | self, model_id, version |
| `delete_model` | ✓ | self, model_id |
| `get_evaluation_metrics` | ✓ | self, model_id |

### `AgentConfigurationService` (_BaseHTTPClient)

| Method | Async | Args |
|--------|-------|------|
| `update_prioritization_weights` | ✓ | self, weights, version |
| `update_policy_rules` | ✓ | self, rules, version |
| `update_rl_policy` | ✓ | self, policy_model_id, version |
| `delete_config` | ✓ | self, config_type, config_id |
| `rollback_config` | ✓ | self, config_type, config_id, version |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_with_client_logging_and_metrics` |  | span_name, span_attributes |

**Constants:** `_pii_filter`, `logger`, `HTTP_CALLS_TOTAL`, `HTTP_CALL_LATENCY_SECONDS`

---

## arbiter/meta_learning_orchestrator/config.py
**Lines:** 471

### `MetaLearningConfig` (BaseSettings)
**Attributes:** model_config

| Method | Async | Args |
|--------|-------|------|
| `validate_file_paths` |  | cls, v |
| `validate_security_keys` |  | cls, v, info |
| `validate_kafka_brokers` |  | cls, v, info |
| `validate_redis_url` |  | cls, v |
| `validate_endpoints` |  | cls, v, info |
| `validate_retention` |  | cls, v, info |
| `reload_config` |  | self |
| `_reload_from_file` |  | self |
| `start_watcher` | ✓ | self |
| `_load_from_etcd` |  | self, client |
| `is_healthy` | ✓ | self |

**Constants:** `logger`

---

## arbiter/meta_learning_orchestrator/logging_utils.py
**Lines:** 301

### `LogCorrelationFilter` (logging.Filter)

| Method | Async | Args |
|--------|-------|------|
| `filter` |  | self, record |
| `_set_no_trace_fields` |  | self, record |

### `JSONFormatter` (logging.Formatter)

| Method | Async | Args |
|--------|-------|------|
| `format` |  | self, record |

### `PIIRedactorFilter` (logging.Filter)
**Attributes:** REDACTION_STRING, MAX_RECURSION_DEPTH, BASE_PII_REGEX_PATTERNS, DEFAULT_SENSITIVE_KEYS

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `_load_config` |  | self |
| `filter` |  | self, record |
| `_redact_value` |  | self, value, seen, depth |
| `_redact_dict` |  | self, data, seen, depth |
| `_redact_string_with_regex` |  | self, text |

**Constants:** `REDACTION_ENABLED`

---

## arbiter/meta_learning_orchestrator/metrics.py
**Lines:** 323

### `LabeledMetricWrapper`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, metric, global_labels, extra_labelnames |
| `labels` |  | self |
| `inc` |  | self, amount |
| `dec` |  | self, amount |
| `set` |  | self, value |
| `observe` |  | self, amount |
| `time` |  | self |
| `__getattr__` |  | self, name |

### `MetricRegistry`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `get_or_create` |  | self, metric_class, name, documentation, labelnames, ...+1 |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_get_or_create_metric_internal` |  | metric_class, name, documentation, labelnames, buckets |
| `get_or_create_metric` |  | metric_type, name, documentation, labelnames |

**Constants:** `logger`, `multiproc_dir`, `GLOBAL_LABELS`, `registry`, `METRIC_CONFLICTS`, `ML_INGESTION_COUNT`, `ML_TRAINING_TRIGGER_COUNT`, `ML_TRAINING_SUCCESS_COUNT`, `ML_TRAINING_FAILURE_COUNT`, `ML_EVALUATION_COUNT`, `ML_DEPLOYMENT_TRIGGER_COUNT`, `ML_DEPLOYMENT_SUCCESS_COUNT`, `ML_DEPLOYMENT_FAILURE_COUNT`, `ML_ORCHESTRATOR_ERRORS`, `ML_TRAINING_LATENCY`, ...+9 more

---

## arbiter/meta_learning_orchestrator/models.py
**Lines:** 259

### `EventType` (str, Enum)
**Attributes:** DECISION_MADE, FEEDBACK_RECEIVED, ACTION_TAKEN

### `DeploymentStatus` (str, Enum)
**Attributes:** PENDING, DEPLOYED, FAILED, ROLLED_BACK

### `LearningRecord` (BaseModel)
**Attributes:** model_config

| Method | Async | Args |
|--------|-------|------|
| `serialize_datetime` |  | self, value |

### `ModelVersion` (BaseModel)
**Attributes:** model_config

| Method | Async | Args |
|--------|-------|------|
| `serialize_datetime` |  | self, value |
| `validate_metrics_and_status` |  | self |

### `DataIngestionError` (Exception)

### `ModelDeploymentError` (Exception)

### `LeaderElectionError` (Exception)

---

## arbiter/meta_learning_orchestrator/orchestrator.py
**Lines:** 1305

### `Ingestor`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config, kafka_producer |
| `initialize` | ✓ | self |
| `shutdown` | ✓ | self |
| `ingest_learning_record` | ✓ | self, record_data |

### `Trainer`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config, ml_platform_client, agent_config_service |
| `_evaluate_model` | ✓ | self, model_version |
| `_deploy_model` | ✓ | self, model_version |
| `trigger_model_training_and_deployment` | ✓ | self, data_location |

### `MetaLearningOrchestrator`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config, ml_platform_client, agent_config_service, kafka_producer, ...+1 |
| `_validate_local_dir` |  | self, path |
| `start` | ✓ | self |
| `stop` | ✓ | self |
| `_run_periodic_leader_task` | ✓ | self, coro, task_name, interval_seconds |
| `_run_leader_election` | ✓ | self |
| `_become_leader` | ✓ | self, lock_info |
| `_step_down_leadership` | ✓ | self, reason |
| `_acquire_leader_lock` | ✓ | self |
| `_verify_leadership_and_fencing` | ✓ | self |
| `_get_local_file_records_count` | ✓ | self |
| `_get_kafka_new_records_count` | ✓ | self |
| `ingest_learning_record` | ✓ | self, record_data |
| `_training_check_core` | ✓ | self |
| `_data_cleanup_core` | ✓ | self |
| `_cleanup_s3_data_lake` | ✓ | self |
| `_cleanup_local_data_lake` | ✓ | self |
| `get_health_status` | ✓ | self |
| `is_ready` | ✓ | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_log_structured` |  | level, message |
| `create_task_with_supervision` | ✓ | coro, task_name, restart_on_error, restart_delay |
| `setup_signal_handlers` |  | orchestrator |

**Constants:** `logger`

---

## arbiter/metrics.py
**Lines:** 622

### `MetricsService` (PluginBase)

| Method | Async | Args |
|--------|-------|------|
| `initialize` | ✓ | self |
| `start` | ✓ | self |
| `stop` | ✓ | self |
| `health_check` | ✓ | self |
| `get_capabilities` | ✓ | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_idempotent_metric` |  | metric_class, name, documentation, labelnames, buckets |
| `get_or_create_metric` |  | metric_type, name, documentation, labelnames, buckets, ...+1 |
| `get_or_create_counter` |  | name, documentation, labelnames |
| `get_or_create_gauge` |  | name, documentation, labelnames, initial_value |
| `get_or_create_histogram` |  | name, documentation, labelnames, buckets |
| `get_or_create_summary` |  | name, documentation, labelnames |
| `metrics_handler` |  | auth |
| `register_dynamic_metric` |  | metric_type, name, documentation, labelnames |
| `health_check` |  |  |
| `clear_stale_metrics` |  |  |
| `rotate_metrics_auth_token` |  |  |

**Constants:** `tracer`, `_metrics_logger`, `_METRICS_LOCK`, `METRIC_REGISTRATIONS_TOTAL`, `METRIC_REGISTRATION_ERRORS`, `METRIC_REGISTRATION_TIME`, `HTTP_REQUESTS_TOTAL`, `HTTP_REQUESTS_LATENCY_SECONDS`, `ERRORS_TOTAL`, `security`, `CONFIG_FALLBACK_USED`, `__all__`

---

## arbiter/metrics_helper.py
**Lines:** 64

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `get_or_create_metric` |  | metric_type, name, description, labelnames |
| `clear_all_metrics` |  |  |

**Constants:** `logger`

---

## arbiter/models/audit_ledger_client.py
**Lines:** 1938

### `DLTError` (Exception)

### `DLTConnectionError` (DLTError)

### `DLTContractError` (DLTError)

### `DLTTransactionError` (DLTError)

### `DLTUnsupportedError` (DLTError)

### `SecretScrubber` (logging.Filter)

| Method | Async | Args |
|--------|-------|------|
| `filter` |  | self, record |

### `AuditEvent` (BaseModel)

| Method | Async | Args |
|--------|-------|------|
| `validate_details_size` |  | cls, v |
| `hash_pii` |  | cls, data |

### `AuditLedgerClient`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, dlt_type, extra_metric_labels |
| `_get_private_key` |  | self |
| `rotate_private_key` |  | self |
| `__aenter__` | ✓ | self |
| `__aexit__` | ✓ | self, exc_type, exc_val, exc_tb |
| `connect` | ✓ | self |
| `disconnect` | ✓ | self |
| `log_event` | ✓ | self, event_type, details, operator, correlation_id, ...+1 |
| `batch_log_events` | ✓ | self, events |
| `get_event` | ✓ | self, tx_hash |
| `get_events_by_type` | ✓ | self, event_type, start_block, end_block, chunk_size |
| `verify_event` | ✓ | self, tx_hash, expected_details |
| `flag_for_redaction` | ✓ | self, tx_hash, reason |
| `wait_for_confirmations` | ✓ | self, tx_hash |
| `is_connected` | ✓ | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_get_or_create_metric` |  | metric_class, name, documentation, labelnames |
| `main` | ✓ |  |

**Constants:** `logger`, `tracer`

---

## arbiter/models/common.py
**Lines:** 63

### `Severity` (str, Enum)
**Attributes:** DEBUG, INFO, LOW, MEDIUM, HIGH, WARN, ERROR, CRITICAL

| Method | Async | Args |
|--------|-------|------|
| `from_string` |  | cls, s |

**Constants:** `logger`

---

## arbiter/models/db_clients.py
**Lines:** 1395

### `DBClientError` (Exception)

### `DBClientConnectionError` (DBClientError)

### `DBClientQueryError` (DBClientError)

### `DBClientTimeoutError` (DBClientError)

### `DBClientIntegrityError` (DBClientError)

### `DummyDBClient`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `__aenter__` | ✓ | self |
| `__aexit__` | ✓ | self, exc_type, exc_val, exc_tb |
| `feedback_entries` |  | self |
| `connect` | ✓ | self |
| `disconnect` | ✓ | self |
| `save_feedback_entry` | ✓ | self, entry |
| `get_feedback_entries` | ✓ | self, query |
| `update_feedback_entry` | ✓ | self, query, updates |
| `delete_feedback_entry` | ✓ | self, query |
| `health_check` | ✓ | self |
| `clear` |  | self |

### `SQLiteClient`
**Attributes:** _SCHEMA_VERSION, _CREATE_TABLE_SQL

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, db_file, timeout, wal_mode |
| `_get_connection` |  | self |
| `__aenter__` | ✓ | self |
| `__aexit__` | ✓ | self, exc_type, exc_val, exc_tb |
| `connect` | ✓ | self |
| `disconnect` | ✓ | self |
| `save_feedback_entry` | ✓ | self, entry |
| `get_feedback_entries` | ✓ | self, query |
| `update_feedback_entry` | ✓ | self, query, updates |
| `delete_feedback_entry` | ✓ | self, query |
| `health_check` | ✓ | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_get_or_create_metric` |  | metric_class, name, documentation, labelnames, buckets |

**Constants:** `logger`, `DB_CLIENT_OPS_TOTAL`, `DB_CLIENT_OPS_LATENCY`, `DB_CLIENT_ENTRIES`, `DB_CLIENT_ERRORS`, `__all__`

---

## arbiter/models/feature_store_client.py
**Lines:** 1627

### `FeatureEntityModel` (BaseModel)
**Attributes:** model_config

| Method | Async | Args |
|--------|-------|------|
| `validate_value_type` |  | cls, v |

### `FeatureViewModel` (BaseModel)
**Attributes:** model_config

| Method | Async | Args |
|--------|-------|------|
| `validate_feature_schema` |  | cls, v |
| `validate_ttl` |  | cls, v |

### `FeatureSourceModel` (BaseModel)
**Attributes:** model_config

| Method | Async | Args |
|--------|-------|------|
| `validate_source_type` |  | cls, v |

### `FeatureStoreClient`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, repo_path |
| `_get_credentials` |  | self, key |
| `__aenter__` | ✓ | self |
| `__aexit__` | ✓ | self, exc_type, exc_val, exc_tb |
| `connect` | ✓ | self |
| `disconnect` | ✓ | self |
| `health_check` | ✓ | self |
| `log_operation` | ✓ | self, operation, details |
| `apply_feature_definitions` | ✓ | self, definitions |
| `wait_for_ingestion` | ✓ | self, feature_view_name, timeout |
| `ingest_features` | ✓ | self, feature_view_name, data_df |
| `get_online_features` | ✓ | self, feature_refs, entity_rows |
| `get_historical_features` | ✓ | self, entity_df, feature_refs |
| `validate_features` | ✓ | self, feature_view_name |
| `flag_for_redaction` | ✓ | self, feature_view_name, reason |

### `ConnectionError` (Exception)

### `SchemaValidationError` (Exception)

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_get_or_create_metric` |  | metric_class, name, documentation, labelnames, buckets |
| `main` | ✓ |  |

**Constants:** `logger`, `tracer`, `FS_CALLS_TOTAL`, `FS_CALLS_ERRORS`, `FS_CALL_LATENCY_SECONDS`, `FS_FEATURE_FRESHNESS_SECONDS`, `FS_REDACTIONS_TOTAL`, `FS_AUDIT_LOGS_TOTAL`

---

## arbiter/models/knowledge_graph_db.py
**Lines:** 1508

### `KnowledgeGraphError` (Exception)

### `ConnectionError` (KnowledgeGraphError)

### `QueryError` (KnowledgeGraphError)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, original_error |

### `SchemaValidationError` (KnowledgeGraphError)

### `NodeNotFoundError` (QueryError)

### `ImmutableAuditLogger`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, file_path, max_bytes, backup_count |
| `_worker` | ✓ | self |
| `_rotate_log` | ✓ | self |
| `log_event` | ✓ | self, event, details |
| `close` | ✓ | self |

### `KGNode` (BaseModel)
**Attributes:** model_config

### `KGRelationship` (BaseModel)
**Attributes:** model_config

| Method | Async | Args |
|--------|-------|------|
| `validate_properties` |  | cls, v |

### `Neo4jKnowledgeGraph`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, url, user, password, audit_logger, ...+7 |
| `_get_password` |  | self |
| `__aenter__` | ✓ | self |
| `__aexit__` | ✓ | self, exc_type, exc_val, exc_tb |
| `_with_retry` | ✓ | self, func |
| `_do_connect` | ✓ | self |
| `health_check` | ✓ | self |
| `connect` | ✓ | self |
| `disconnect` | ✓ | self |
| `_execute_tx` | ✓ | self, tx, query, params, write |
| `add_node` | ✓ | self, label, properties |
| `add_relationship` | ✓ | self, from_node_id, to_node_id, rel_type, properties |
| `add_fact` | ✓ | self, domain, key, data, source, ...+1 |
| `find_related_facts` | ✓ | self, domain, key, value |
| `check_consistency` | ✓ | self, domain, key, value |
| `_export_nodes` | ✓ | self, session, filename, chunk_size, node_total |
| `_export_relationships` | ✓ | self, session, filename, chunk_size, rel_total |
| `export_graph` | ✓ | self, filename, chunk_size |
| `_import_nodes` | ✓ | self, session, filename, chunk_size, validate |
| `_import_relationships` | ✓ | self, session, filename, chunk_size, validate |
| `import_graph` | ✓ | self, filename, chunk_size, validate |
| `_import_nodes_batch` | ✓ | self, session, nodes |
| `_import_relationships_batch` | ✓ | self, session, relationships |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_get_or_create_metric` |  | metric_class, name, documentation, labelnames, buckets |
| `_safe_name` |  | name |

**Constants:** `logger`, `_is_test_environment`, `KG_REGISTRY`, `KG_OPS_TOTAL`, `KG_OPS_LATENCY`, `KG_CONNECTIONS`, `KG_ERRORS`, `_NAME_RX`

---

## arbiter/models/merkle_tree.py
**Lines:** 904

### `MerkleTreeError` (Exception)

### `MerkleTreeEmptyError` (MerkleTreeError)

### `MerkleProofError` (MerkleTreeError)

### `MerkleTree`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, leaves, store_raw |
| `_hash_leaf` |  | self, leaf_bytes |
| `_hashed_leaves` |  | self |
| `_update_tree` | ✓ | self |
| `_update_metrics` |  | self |
| `size` |  | self |
| `approx_depth` |  | self |
| `_root_bytes` |  | self |
| `_proof_for_index` |  | self, idx |
| `add_leaf` | ✓ | self, data |
| `add_leaves` | ✓ | self, data_list |
| `get_root` |  | self |
| `get_proof` |  | self, index |
| `verify_proof` |  | root, leaf_data, proof |
| `save` | ✓ | self, filepath |
| `load` | ✓ | cls, filepath |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_get_or_create_metric` |  | metric_class, name, documentation, labelnames, buckets |
| `_write_compressed_json` |  | path, data |
| `_read_compressed_json` |  | path |
| `main` | ✓ |  |

**Constants:** `logger`, `tracer`, `METRICS_REGISTRY`, `HASH_OFFLOAD_THRESHOLD`, `MERKLE_OPS_TOTAL`, `MERKLE_OPS_LATENCY_SECONDS`, `MERKLE_TREE_SIZE`, `MERKLE_TREE_DEPTH`

---

## arbiter/models/meta_learning_data_store.py
**Lines:** 1240

### `MetaLearningDataStoreConfig` (BaseModel)

### `MetaLearningRecord` (BaseModel)
**Attributes:** model_config

| Method | Async | Args |
|--------|-------|------|
| `validate_tags` |  | cls, v |
| `serialize_timestamp` |  | self, v |

### `MetaLearningDataStoreError` (Exception)

### `MetaLearningRecordNotFound` (MetaLearningDataStoreError)

### `MetaLearningRecordValidationError` (MetaLearningDataStoreError)

### `MetaLearningBackendError` (MetaLearningDataStoreError)

### `MetaLearningEncryptionError` (MetaLearningDataStoreError)

### `BaseMetaLearningDataStore`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `__aenter__` | ✓ | self |
| `__aexit__` | ✓ | self, exc_type, exc, tb |
| `add_record` | ✓ | self, record |
| `get_record` | ✓ | self, experiment_id |
| `list_records` | ✓ | self, filter_by |
| `update_record` | ✓ | self, experiment_id, updates |
| `delete_record` | ✓ | self, experiment_id |
| `_encrypt_field` | ✓ | self, data |
| `_decrypt_field` | ✓ | self, data |

### `InMemoryMetaLearningDataStore` (BaseMetaLearningDataStore)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `add_record` | ✓ | self, record |
| `get_record` | ✓ | self, experiment_id |
| `list_records` | ✓ | self, filter_by |
| `update_record` | ✓ | self, experiment_id, updates |
| `delete_record` | ✓ | self, experiment_id |

### `RedisMetaLearningDataStore` (BaseMetaLearningDataStore)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `__aenter__` | ✓ | self |
| `__aexit__` | ✓ | self, exc_type, exc_val, exc_tb |
| `_check_connection` | ✓ | self |
| `add_record` | ✓ | self, record |
| `get_record` | ✓ | self, experiment_id |
| `list_records` | ✓ | self, filter_by |
| `update_record` | ✓ | self, experiment_id, updates |
| `delete_record` | ✓ | self, experiment_id |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_get_or_create_metric` |  | metric_class, name, documentation, labelnames, buckets |
| `get_meta_learning_data_store` |  | backend |
| `main` | ✓ |  |

**Constants:** `logger`, `tracer`, `MLDS_OPS_TOTAL`, `MLDS_OPS_LATENCY`, `MLDS_DATA_SIZE`, `TRANSIENT_ERRORS`

---

## arbiter/models/multi_modal_schemas.py
**Lines:** 519

### `Sentiment` (str, Enum)
**Attributes:** POSITIVE, NEUTRAL, NEGATIVE, MIXED, UNKNOWN

### `BaseConfig` (BaseModel)
**Attributes:** model_config

| Method | Async | Args |
|--------|-------|------|
| `serialize_datetime` |  | self, value |
| `sanitize_text_fields` |  | cls, v, info |

### `ImageOCRResult` (BaseConfig)

### `ImageCaptioningResult` (BaseConfig)

### `ImageAnalysisResult` (BaseConfig)

| Method | Async | Args |
|--------|-------|------|
| `ensure_utc_timestamp` |  | cls, v |

### `AudioTranscriptionResult` (BaseConfig)

### `AudioAnalysisResult` (BaseConfig)

| Method | Async | Args |
|--------|-------|------|
| `check_speaker_count` |  | self |
| `ensure_utc_timestamp` |  | cls, v |

### `VideoSummaryResult` (BaseConfig)

| Method | Async | Args |
|--------|-------|------|
| `non_negative` |  | cls, v |

### `VideoAnalysisResult` (BaseConfig)

| Method | Async | Args |
|--------|-------|------|
| `ensure_utc_timestamp` |  | cls, v |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `to_camel` |  | string |

**Constants:** `logger`, `MultiModalAnalysisResult`

---

## arbiter/models/postgres_client.py
**Lines:** 1838

### `ConnectionError` (Exception)

### `QueryError` (Exception)

### `PostgresClientError` (Exception)

### `PostgresClientConnectionError` (PostgresClientError)

### `PostgresClientSchemaError` (PostgresClientError)

### `PostgresClientQueryError` (PostgresClientError)

### `PostgresClientTimeoutError` (PostgresClientError)

### `PostgresClient`
**Attributes:** _TABLE_SCHEMAS

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, db_url |
| `__aenter__` | ✓ | self |
| `__aexit__` | ✓ | self, exc_type, exc_val, exc_tb |
| `ping` | ✓ | self |
| `reconnect` | ✓ | self |
| `_start_health_check` | ✓ | self, interval |
| `update_table_row_counts` | ✓ | self |
| `_init_conn` | ✓ | self, conn |
| `connect` | ✓ | self |
| `disconnect` | ✓ | self |
| `_validate_table_and_columns` |  | self, table, cols |
| `_normalize_row` |  | self, table, row, normalize_datetimes |
| `_ensure_table_exists` | ✓ | self, table_name |
| `_execute_query` | ✓ | self, operation, table, query |
| `_scrub_secrets` |  | self, value |
| `_get_insert_update_sql_and_values` | ✓ | self, table, data |
| `_save_many_copy` | ✓ | self, table, data_list |
| `save` | ✓ | self, table, data |
| `save_many` | ✓ | self, table, data_list |
| `load` | ✓ | self, table, query_value, query_field, normalize_datetimes |
| `load_all` | ✓ | self, table, filters, order_by, limit, ...+1 |
| `update` | ✓ | self, table, query, updates |
| `delete` | ✓ | self, table, query_value, query_field |

### `SchemaValidationError` (Exception)

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_get_or_create_metric` |  | metric_class, name, documentation, labelnames, buckets |
| `_sanitize_dsn` |  | dsn |
| `main` | ✓ |  |

**Constants:** `logger`, `tracer`, `DB_CALLS_TOTAL`, `DB_CALLS_ERRORS`, `DB_CALL_LATENCY_SECONDS`, `DB_CONNECTIONS_CURRENT`, `DB_CONNECTIONS_IN_USE`, `DB_TABLE_ROWS`, `FATAL_EXC`, `TRANSIENT_EXC`

---

## arbiter/models/redis_client.py
**Lines:** 886

### `RedisClient`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, redis_url |
| `__aenter__` | ✓ | self |
| `__aexit__` | ✓ | self, exc_type, exc_val, exc_tb |
| `connect` | ✓ | self |
| `reconnect` | ✓ | self |
| `ping` | ✓ | self |
| `_start_health_check` | ✓ | self, interval |
| `update_redis_stats` | ✓ | self |
| `disconnect` | ✓ | self |
| `_execute_operation` | ✓ | self, operation, key, func |
| `set` | ✓ | self, key, value, ex, px |
| `mset` | ✓ | self, mapping |
| `get` | ✓ | self, key |
| `mget` | ✓ | self, keys |
| `delete` | ✓ | self |
| `setex` | ✓ | self, key, time, value |
| `lock` |  | self, name, timeout, blocking_timeout |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_get_or_create_metric` |  | metric_class, name, documentation, labelnames, buckets |
| `_redact_key` |  | key |
| `main` | ✓ |  |

**Constants:** `logger`, `tracer`, `REDIS_CALLS_TOTAL`, `REDIS_CALLS_ERRORS`, `REDIS_CALL_LATENCY_SECONDS`, `REDIS_CONNECTIONS_CURRENT`, `REDIS_LOCK_ACQUIRED_TOTAL`, `REDIS_LOCK_RELEASED_TOTAL`, `REDIS_LOCK_FAILED_TOTAL`, `REDIS_MEMORY_USAGE`, `REDIS_KEYSPACE_SIZE`

---

## arbiter/monitoring.py
**Lines:** 639

### `LogFormat` (Enum)
**Attributes:** JSONL, JSON, PLAINTEXT

### `ActionLog` (Base)
**Attributes:** __tablename__, id, data, timestamp

### `Monitor`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, log_file, logger, max_file_size, max_actions_in_memory, ...+4 |
| `check_permission` |  | self, role, permission |
| `__aenter__` | ✓ | self |
| `__aexit__` | ✓ | self, exc_type, exc_val, exc_tb |
| `_default_logger` |  |  |
| `_compute_hash` |  | self, action |
| `log_action` |  | self, action |
| `_write_log` |  | self, action |
| `log_to_database` | ✓ | self, action |
| `detect_anomalies` | ✓ | self, window_minutes |
| `generate_reports` |  | self |
| `get_recent_events` |  | self, count |
| `explain_decision` |  | self, decision_id |
| `search` |  | self, filter_fn |
| `export_log` | ✓ | self, file_path, format |
| `health_check` | ✓ | self |

**Constants:** `MAX_IN_MEMORY_LOG_SIZE_MB`, `JSON_LOG_WRITE_LIMIT`, `logger`, `monitor_ops_total`, `monitor_errors_total`

---

## arbiter/otel_config.py
**Lines:** 961

### `NoOpTracer`

| Method | Async | Args |
|--------|-------|------|
| `start_as_current_span` |  | self, name |
| `start_span` |  | self, name |

### `Environment` (Enum)
**Attributes:** DEVELOPMENT, STAGING, PRODUCTION, TESTING

| Method | Async | Args |
|--------|-------|------|
| `current` |  | cls |

### `CollectorEndpoint`

| Method | Async | Args |
|--------|-------|------|
| `is_reachable` |  | self |

### `SamplingStrategy`

| Method | Async | Args |
|--------|-------|------|
| `should_sample` |  | self, span_name, service_name, attributes |
| `_should_sample_rate` |  | self, rate |

### `OpenTelemetryConfig`
**Attributes:** _lock, _initialized

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `get_instance` |  | cls |
| `_initialize` |  | self |
| `_discover_endpoints` |  | self |
| `_discover_from_consul` |  | self |
| `_discover_from_etcd` |  | self |
| `_endpoints_from_env` |  | self |
| `_validate_endpoint` |  | self, endpoint |
| `_create_resource` |  | self |
| `_create_tracer_provider` |  | self, resource |
| `_create_sampler` |  | self |
| `_create_span_processor` |  | self, endpoint |
| `_create_exporter` |  | self, endpoint |
| `_create_credentials` |  | self, endpoint |
| `_configure_propagators` |  | self |
| `_initialize_metrics` |  | self, resource |
| `_initialize_logging` |  | self, resource |
| `_parse_headers` |  | self, headers_str |
| `trace_context` |  | self, operation_name |
| `get_tracer` |  | self, name |
| `shutdown` |  | self |

### `NoOpSpan`

| Method | Async | Args |
|--------|-------|------|
| `__enter__` |  | self |
| `__exit__` |  | self |
| `set_attribute` |  | self, key, value |
| `add_event` |  | self, name, attributes |
| `set_status` |  | self, status |
| `record_exception` |  | self, exception |
| `get_span_context` |  | self |

### `NoOpTracer`

| Method | Async | Args |
|--------|-------|------|
| `start_as_current_span` |  | self, name |
| `start_span` |  | self, name |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `get_tracer_safe` |  | name, version |
| `get_tracer` |  | name |
| `trace_operation` |  | operation_name |

**Constants:** `logger`, `__all__`

---

## arbiter/plugin_config.py
**Lines:** 306

### `ImmutableDict` (dict)

| Method | Async | Args |
|--------|-------|------|
| `__setitem__` |  | self, key, value |
| `__delitem__` |  | self, key |
| `clear` |  | self |
| `pop` |  | self |
| `popitem` |  | self |
| `setdefault` |  | self, key, default |
| `update` |  | self |

### `PluginRegistryMeta` (type)

| Method | Async | Args |
|--------|-------|------|
| `__setattr__` |  | cls, name, value |

### `PluginRegistry`
**Attributes:** __ORIGINAL_PLUGINS, _PLUGINS, _REGISTRY_LOCK

| Method | Async | Args |
|--------|-------|------|
| `get_plugins` |  | cls |
| `check_permission` |  | cls, role, permission |
| `validate` |  | cls |
| `register_plugin` | ✓ | cls, name, import_path |
| `health_check` | ✓ | cls |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_get_or_create_metric` |  | metric_class, name, doc, labelnames |

**Constants:** `tracer`, `logger`, `plugin_config_ops_total`, `plugin_config_errors_total`, `SANDBOXED_PLUGINS`

---

## arbiter/plugins/anthropic_adapter.py
**Lines:** 370

### `AnthropicAdapter`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, settings |
| `__aenter__` | ✓ | self |
| `__aexit__` | ✓ | self, exc_type, exc, tb |
| `_update_circuit_breaker` |  | self, success |
| `_sanitize_prompt` |  | self, prompt |
| `generate` | ✓ | self, prompt, max_tokens, temperature, correlation_id |

**Constants:** `logger`, `LLM_PROVIDER_NAME`, `anthropic_call_latency_seconds`, `anthropic_call_success_total`, `anthropic_call_errors_total`

---

## arbiter/plugins/gemini_adapter.py
**Lines:** 407

### `GeminiAdapter`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, settings |
| `__aenter__` | ✓ | self |
| `__aexit__` | ✓ | self, exc_type, exc_val, exc_tb |
| `generate` | ✓ | self, prompt, max_tokens, temperature, correlation_id |
| `_sanitize_prompt` |  | self, prompt |
| `_update_circuit_breaker` |  | self, success |

**Constants:** `logger`, `gemini_call_latency_seconds`, `gemini_call_success_total`, `gemini_call_errors_total`

---

## arbiter/plugins/llm_client.py
**Lines:** 1466

### `LLMClientError` (Exception)

### `AuthError` (LLMClientError)

### `RateLimitError` (LLMClientError)

### `TimeoutError` (LLMClientError)

### `APIError` (LLMClientError)

### `InputValidationError` (LLMClientError)

### `CircuitBreakerOpenError` (LLMClientError)

### `LLMClient`
**Attributes:** _session_lock

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, provider, api_key, model, base_url, ...+3 |
| `_update_circuit_breaker` |  | self, success |
| `_check_circuit_breaker` |  | self |
| `_get_ollama_session` | ✓ | cls |
| `_close_ollama_session_atexit` | ✓ | cls |
| `aclose_session` | ✓ | self |
| `_handle_llm_call` | ✓ | self, coro_producer, prompt, is_streaming, correlation_id |
| `_llm_type` |  | self |
| `_sanitize_prompt` |  | self, prompt |
| `_generate_prompt` |  | self, messages |
| `generate_text` | ✓ | self, prompt, max_tokens, temperature, correlation_id |
| `_generate_core` | ✓ | self, messages, max_tokens, temperature |
| `async_stream_text` | ✓ | self, prompt, max_tokens, temperature, correlation_id |
| `_stream_core` | ✓ | self, messages, max_tokens, temperature |

### `LoadBalancedLLMClient`
**Attributes:** FAILURE_QUARANTINE_THRESHOLD, QUARANTINE_DURATION_SECONDS, RETRYABLE_FAILURE_PENALTY_TIME

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, providers_config |
| `_select_provider` |  | self |
| `_update_provider_status` |  | self, provider_name, success, is_retryable_error |
| `generate_text` | ✓ | self, prompt, max_tokens, temperature, correlation_id |
| `async_stream_text` | ✓ | self, prompt, max_tokens, temperature, correlation_id |
| `close_all_sessions` | ✓ | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `get_or_create_metric` |  | metric_class, name, documentation, labelnames, buckets |
| `main` | ✓ |  |

**Constants:** `logger`, `tracer`, `_metrics_lock`, `LLM_CALL_LATENCY`, `LLM_CALL_ERRORS`, `LLM_CALL_SUCCESS`, `LLM_PROVIDER_FAILOVERS_TOTAL`

---

## arbiter/plugins/multi_modal_config.py
**Lines:** 387

### `CircuitBreakerConfig` (BaseModel)

### `ProcessorConfig` (BaseModel)

### `SecurityConfig` (BaseModel)

| Method | Async | Args |
|--------|-------|------|
| `validate_validation_rules` |  | cls, v |
| `validate_pii_patterns` |  | cls, v |

### `AuditLogConfig` (BaseModel)

| Method | Async | Args |
|--------|-------|------|
| `validate_log_level` |  | cls, v |
| `validate_destination` |  | cls, v |

### `MetricsConfig` (BaseModel)

### `CacheConfig` (BaseModel)

### `ComplianceConfig` (BaseModel)

| Method | Async | Args |
|--------|-------|------|
| `validate_compliance_mapping` |  | cls, v |

### `MultiModalConfig` (BaseModel)

| Method | Async | Args |
|--------|-------|------|
| `load_config` |  | cls, config_file |

---

## arbiter/plugins/multi_modal_plugin.py
**Lines:** 1215

### `AuditLogger`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config |
| `log_event` |  | self, user_id, event_type, timestamp, success, ...+7 |

### `MetricsCollector`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config |
| `increment_successful_requests` |  | self, modality |
| `increment_failed_requests` |  | self, modality |
| `observe_latency` |  | self, modality, latency_ms |
| `increment_cache_hits` |  | self, modality |
| `increment_cache_misses` |  | self, modality |

### `CacheManager`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config |
| `connect` | ✓ | self |
| `disconnect` | ✓ | self |
| `get` | ✓ | self, key |
| `set` | ✓ | self, key, value, ttl_seconds |

### `InputValidator`

| Method | Async | Args |
|--------|-------|------|
| `validate` |  | modality, data, security_config |

### `OutputValidator`

| Method | Async | Args |
|--------|-------|------|
| `validate` |  | modality, result, security_config |

### `SandboxExecutor`

| Method | Async | Args |
|--------|-------|------|
| `execute` | ✓ | func |

### `MultiModalProcessor`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, providers |
| `process` | ✓ | self, modality, data |

### `MultiModalPlugin`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config |
| `initialize` | ✓ | self |
| `start` | ✓ | self |
| `stop` | ✓ | self |
| `health_check` | ✓ | self |
| `get_capabilities` | ✓ | self |
| `_load_processors` |  | self |
| `_setup_hooks` |  | self |
| `add_hook` |  | self, modality, hook_fn, hook_type |
| `_execute_hooks` | ✓ | self, modality, data, hook_type |
| `_check_circuit_breaker` |  | self, modality |
| `_update_circuit_breaker` |  | self, modality, success |
| `_process_data` | ✓ | self, modality, data, processor |
| `process_image` | ✓ | self, image_data |
| `process_audio` | ✓ | self, audio_data |
| `process_video` | ✓ | self, video_data |
| `process_text` | ✓ | self, text |
| `get_supported_providers` |  | self, modality |
| `set_default_provider` |  | self, modality, provider_name |
| `update_model_version` |  | self, modality, version |
| `__aenter__` | ✓ | self |
| `__aexit__` | ✓ | self, exc_type, exc_val, exc_tb |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `get_or_create_counter` |  | name, description, labels |
| `get_or_create_histogram` |  | name, description, labels, buckets |
| `main` | ✓ |  |

**Constants:** `logger`

---

## arbiter/plugins/multimodal/interface.py
**Lines:** 1085

### `MultiModalException` (Exception)

### `InvalidInputError` (MultiModalException)

### `ConfigurationError` (MultiModalException)

### `ProviderNotAvailableError` (MultiModalException)

### `ProcessingError` (MultiModalException)

### `ProcessingResult` (BaseModel, Generic[T])
**Attributes:** model_config

### `ImageProcessor` (ABC)

| Method | Async | Args |
|--------|-------|------|
| `process` | ✓ | self, image_data |

### `AudioProcessor` (ABC)

| Method | Async | Args |
|--------|-------|------|
| `process` | ✓ | self, audio_data |

### `VideoProcessor` (ABC)

| Method | Async | Args |
|--------|-------|------|
| `process` | ✓ | self, video_data |

### `TextProcessor` (ABC)

| Method | Async | Args |
|--------|-------|------|
| `process` | ✓ | self, text_data |

### `AnalysisResultType` (str, Enum)
**Attributes:** IMAGE, AUDIO, VIDEO, TEXT, GENERIC

### `MultiModalAnalysisResult` (BaseModel, Generic[T], ABC)
**Attributes:** model_config

| Method | Async | Args |
|--------|-------|------|
| `summary` |  | self |
| `is_valid` |  | self |
| `get_provenance_info` |  | self |
| `__str__` |  | self |
| `__repr__` |  | self |

### `ImageAnalysisResult` (MultiModalAnalysisResult[Union[Dict[str, Any], List[Dict[str, Any]]]])

| Method | Async | Args |
|--------|-------|------|
| `summary` |  | self |

### `AudioAnalysisResult` (MultiModalAnalysisResult[Union[str, Dict[str, Any]]])

| Method | Async | Args |
|--------|-------|------|
| `summary` |  | self |

### `VideoAnalysisResult` (MultiModalAnalysisResult[Union[Dict[str, Any], List[Dict[str, Any]]]])

| Method | Async | Args |
|--------|-------|------|
| `summary` |  | self |

### `TextAnalysisResult` (MultiModalAnalysisResult[str])

| Method | Async | Args |
|--------|-------|------|
| `summary` |  | self |

### `MultiModalPluginInterface` (ABC)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config |
| `analyze_image` |  | self, image_data |
| `analyze_audio` |  | self, audio_data |
| `analyze_video` |  | self, video_data |
| `analyze_text` |  | self, text_data |
| `supported_modalities` |  | self |
| `analyze_image_async` | ✓ | self, image_data |
| `analyze_audio_async` | ✓ | self, audio_data |
| `analyze_video_async` | ✓ | self, video_data |
| `analyze_text_async` | ✓ | self, text_data |
| `model_info` |  | self |
| `__enter__` |  | self |
| `__exit__` |  | self, exc_type, exc_val, exc_tb |
| `__aenter__` | ✓ | self |
| `__aexit__` | ✓ | self, exc_type, exc_val, exc_tb |
| `shutdown` |  | self |
| `shutdown_async` | ✓ | self |

### `DummyMultiModalPlugin` (MultiModalPluginInterface)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config |
| `analyze_image` |  | self, image_data |
| `analyze_audio` |  | self, audio_data |
| `analyze_video` |  | self, video_data |
| `analyze_text` |  | self, text_data |
| `supported_modalities` |  | self |
| `model_info` |  | self |
| `shutdown` |  | self |
| `shutdown_async` | ✓ | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `get_or_create` |  | metric |

**Constants:** `T`

---

## arbiter/plugins/multimodal/providers/default_multimodal_providers.py
**Lines:** 934

### `PluginRegistry`

| Method | Async | Args |
|--------|-------|------|
| `register_processor` |  | cls, modality, name, processor_class |
| `unregister_processor` |  | cls, modality, name |
| `get_processor` |  | cls, modality, name, config |
| `get_supported_providers` |  | cls, modality |

### `DefaultImageProcessorConfig` (BaseModel)

### `DefaultAudioProcessorConfig` (BaseModel)

### `DefaultVideoProcessorConfig` (BaseModel)

### `DefaultTextProcessorConfig` (BaseModel)

### `DefaultImageProcessor` (ImageProcessor)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config |
| `process` | ✓ | self, image_data, operation_id |
| `_decode_data` |  | self, data |
| `health_check` | ✓ | self |

### `DefaultAudioProcessor` (AudioProcessor)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config |
| `process` | ✓ | self, audio_data, operation_id |
| `_decode_data` |  | self, data |
| `health_check` | ✓ | self |

### `DefaultVideoProcessor` (VideoProcessor)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config |
| `process` | ✓ | self, video_data, operation_id |
| `_decode_data` |  | self, data |
| `health_check` | ✓ | self |

### `DefaultTextProcessor` (TextProcessor)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config |
| `process` | ✓ | self, text_data, operation_id |
| `health_check` | ✓ | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `get_or_create` |  | metric |
| `demonstrate_provider_usage` | ✓ |  |

**Constants:** `logger`, `__all__`

---

## arbiter/plugins/ollama_adapter.py
**Lines:** 308

### `AuthError` (Exception)

### `RateLimitError` (Exception)

### `OllamaAdapter`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, settings |
| `__aenter__` | ✓ | self |
| `__aexit__` | ✓ | self, exc_type, exc_val, exc_tb |
| `_check_circuit_breaker` |  | self |
| `_update_circuit_breaker` |  | self, success |
| `health_check` | ✓ | self |
| `generate` | ✓ | self, prompt, max_tokens, temperature, correlation_id |

**Constants:** `logger`

---

## arbiter/plugins/openai_adapter.py
**Lines:** 292

### `OpenAIAdapter`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, settings |
| `_check_circuit_breaker` |  | self |
| `_update_circuit_breaker` |  | self, success |
| `__aenter__` | ✓ | self |
| `__aexit__` | ✓ | self, exc_type, exc_val, exc_tb |
| `health_check` | ✓ | self |
| `generate` | ✓ | self, prompt, max_tokens, temperature, correlation_id |

**Constants:** `logger`

---

## arbiter/policy/circuit_breaker.py
**Lines:** 1201

### `InMemoryBreakerStateManager`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, provider |
| `get_state` | ✓ | self |
| `set_state` | ✓ | self, state |
| `state_lock` |  | self |
| `close` | ✓ | self |

### `CircuitBreakerState`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, provider, config |
| `initialize` | ✓ | self |
| `close` | ✓ | self |
| `state_lock` |  | self |
| `_rate_limit` | ✓ | self |
| `_check_redis_health` | ✓ | self |
| `get_state` | ✓ | self |
| `set_state` | ✓ | self, state |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `get_or_create_metric` |  | metric_class, name, documentation, labelnames, initial_value, ...+1 |
| `sanitize_log_message` |  | message |
| `_sanitize_provider` |  | provider |
| `_log_validation_error` |  | message, error_type |
| `get_global_connection_pool` |  | config |
| `validate_config` |  | config |
| `get_breaker_state` | ✓ | provider, config |
| `close_all_breaker_states` | ✓ |  |
| `register_shutdown_handler` |  |  |
| `cleanup_breaker_states` | ✓ |  |
| `start_cleanup_task` |  |  |
| `periodic_config_refresh` | ✓ |  |
| `start_config_refresh_task` |  |  |
| `refresh_breaker_states` | ✓ |  |
| `is_llm_policy_circuit_breaker_open` | ✓ | provider, config |
| `record_llm_policy_api_success` | ✓ | provider, config |
| `record_llm_policy_api_failure` | ✓ | provider, error_message, config |

**Constants:** `ArbiterConfig`, `logger`, `_metrics_lock`, `LLM_API_FAILURE_COUNT`, `LLM_CIRCUIT_BREAKER_STATE`, `LLM_CIRCUIT_BREAKER_TRIPS`, `LLM_CIRCUIT_BREAKER_ERRORS`, `LLM_CIRCUIT_BREAKER_TRANSITIONS`, `CIRCUIT_BREAKER_CLEANUP_OPERATIONS`, `REDIS_OPERATION_LATENCY`, `CONFIG_REFRESH_OPERATIONS`, `TASK_STATE_TRANSITIONS`, `tracer`, `_connection_pool_lock`, `BreakerStateManager`, ...+5 more

---

## arbiter/policy/config.py
**Lines:** 669

### `ArbiterConfig` (BaseSettings)
**Attributes:** model_config

| Method | Async | Args |
|--------|-------|------|
| `parse_optimizer_settings` |  | cls, v |
| `get_redis_pool` |  | self, redis_url |
| `validate_secrets` |  | cls, values |
| `validate_redis_url` |  | self |
| `reload_config` | ✓ | self |
| `to_dict` |  | self |
| `get_api_key_for_provider` |  | provider |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_get_or_create_metric` |  | metric_class, name, doc, labelnames, buckets |
| `get_config` |  |  |

**Constants:** `logger`, `CONFIG_ERRORS`, `CONFIG_INITIALIZATIONS`, `CONFIG_RELOAD_FREQUENCY`, `CONFIG_VALIDATION_DURATION`, `CONFIG_TO_DICT_CACHE_HITS`, `CONFIG_REDIS_VALIDATION_DURATION`, `tracer`, `_instance`, `_lock`

---

## arbiter/policy/core.py
**Lines:** 1998

### `SQLiteClient`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, db_file |
| `connect` | ✓ | self |
| `_init_db` | ✓ | self |
| `save_feedback_entry` | ✓ | self, entry |
| `get_feedback_entries` | ✓ | self, query |
| `update_feedback_entry` | ✓ | self, query, updates |
| `close` | ✓ | self |

### `BasicDecisionOptimizer`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, settings |
| `compute_trust_score` | ✓ | self, auth_context, user_id |

### `PolicyEngine`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, arbiter_instance, config |
| `_create_llm_client` |  | self |
| `_call_llm_for_policy_evaluation` | ✓ | self, prompt |
| `_load_policies_from_file` |  | self |
| `_load_compliance_controls` |  | self |
| `_get_default_policies` |  | self |
| `register_custom_rule` |  | self, rule_func |
| `reload_policies` |  | self |
| `apply_policy_update_from_evolution` | ✓ | self, proposed_policies |
| `validate_policies` |  | policies |
| `_audit_policy_changes` | ✓ | self, old_policies, new_policies, path |
| `_enforce_compliance` | ✓ | self, action_name, control_tag |
| `should_auto_learn` | ✓ | self, domain, key, user_id, value |
| `_audit_policy_decision` | ✓ | self, decision_type, domain, key, user_id, ...+5 |
| `_get_user_roles` | ✓ | self, user_id |
| `_sanitize_prompt` |  | self, prompt |
| `_validate_llm_policy_output` |  | self, llm_response_text, valid_responses |
| `trust_score_rule` | ✓ | self, domain, key, user_id, value |
| `start_policy_refresher` | ✓ | self |
| `_periodic_policy_refresh` | ✓ | self |
| `stop` | ✓ | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `initialize_policy_engine` |  | arbiter_instance |
| `should_auto_learn` | ✓ | domain, key, user_id, value |
| `get_policy_engine_instance` |  |  |
| `reset_policy_engine` | ✓ |  |

**Constants:** `logger`, `tracer`, `PolicyRuleCallable`, `SQLITE_QUERY_LATENCY`, `POLICY_REFRESH_STATE_TRANSITIONS`, `POLICY_UPDATE_OUTCOMES`, `SQLITE_CLOSE_ERRORS`, `AUDIT_LOG_ERRORS`, `POLICY_REFRESH_ERRORS`, `POLICY_ENGINE_INIT_ERRORS`, `POLICY_ENGINE_RESET_ERRORS`, `_policy_engine_lock`

---

## arbiter/policy/metrics.py
**Lines:** 784

### `_FallbackArbiterConfig`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_sanitize_label` |  | value |
| `_log_error_rate_limited` |  | message, error_type |
| `get_or_create_metric` |  | metric_class, name, documentation, labelnames, buckets |
| `_get_config` |  |  |
| `record_policy_decision` |  | allowed, domain, user_type, reason_code |
| `record_llm_call_latency` |  | provider, latency |
| `record_compliance_violation` |  | control_id, violation_type |
| `record_compliance_action` |  | control_id, result, action_type |
| `register_shutdown_handler` |  |  |
| `cleanup_compliance_metrics` | ✓ |  |
| `initialize_compliance_metrics` |  |  |
| `refresh_compliance_metrics` | ✓ |  |
| `start_metric_refresh_task` |  |  |

**Constants:** `tracer`, `current_dir`, `parent_dir`, `COMPLIANCE_CONFIG_PATH`, `ArbiterConfig`, `logger`, `_metrics_lock`, `_refresh_task_lock`, `policy_decision_total`, `policy_file_reload_count`, `policy_last_reload_timestamp`, `_default_decision_optimizer_settings`, `decision_optimizer_settings`, `feedback_buckets`, `llm_buckets`, ...+13 more

---

## arbiter/policy/policy_manager.py
**Lines:** 649

### `DomainRule` (BaseModel)

| Method | Async | Args |
|--------|-------|------|
| `_trust_score_range` |  | cls, v |

### `UserRule` (BaseModel)

### `LLMRules` (BaseModel)

| Method | Async | Args |
|--------|-------|------|
| `_threshold_range` |  | cls, v |

### `TrustRules` (BaseModel)

| Method | Async | Args |
|--------|-------|------|
| `_trust_threshold` |  | cls, v |

### `PolicyConfig` (BaseModel)

| Method | Async | Args |
|--------|-------|------|
| `_check_versions_and_globals` |  | self |
| `default` |  |  |

### `PolicyManager`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config |
| `_build_fernet_from_config` |  | self |
| `_get_old_fernet` |  | self |
| `_read_encrypted_json` | ✓ | self |
| `_write_encrypted_json` | ✓ | self, payload |
| `load_policies` | ✓ | self |
| `save_policies` | ✓ | self |
| `load_from_database` | ✓ | self |
| `save_to_database` | ✓ | self |
| `get_policies` |  | self |
| `set_policies` |  | self, cfg |
| `rotate_encryption_key` | ✓ | self, new_key_b64 |
| `health_check` | ✓ | self |
| `check_permission` | ✓ | self, role, permission |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_get_or_create_metric` |  | metric_class, name, doc, labelnames, buckets |
| `_sanitize_label` |  | value |

**Constants:** `tracer`, `logger`, `policy_ops_total`, `policy_errors_total`, `policy_file_read_latency`, `policy_file_write_latency`, `policy_db_upsert_latency`

---

## arbiter/queue_consumer_worker.py
**Lines:** 746

### `QueueConsumerWorker`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, settings |
| `__aenter__` | ✓ | self |
| `__aexit__` | ✓ | self, exc_type, exc_val, exc_tb |
| `run` | ✓ | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_get_metric` |  | mt, name, doc, labels, buckets |
| `redact_sensitive` |  | data |
| `initialize_handlers` | ✓ |  |
| `send_to_external_notifier` | ✓ | event_type, data |
| `process_event` | ✓ | event_type, data, mq_service, audit_logger |
| `handle_message` | ✓ | event_type, data, mq_service, audit_logger |
| `health_check_handler` | ✓ | request |
| `consumer_main_loop` | ✓ |  |

**Constants:** `logger`, `SFE_CORE_AVAILABLE`, `_settings_instance`, `CONSUMER_MESSAGES_PROCESSED_TOTAL`, `CONSUMER_DELIVERY_ATTEMPTS_TOTAL`, `CONSUMER_DELIVERY_SUCCESS_TOTAL`, `CONSUMER_DELIVERY_FAILURE_TOTAL`, `CONSUMER_DELIVERY_LATENCY_SECONDS`, `CONSUMER_POISON_MESSAGES_TOTAL`, `CONSUMER_OPS_TOTAL`, `CONSUMER_ERRORS_TOTAL`, `shutdown_event`, `_settings_for_attrs`, `POISON_MESSAGE_THRESHOLD`, `POISON_MESSAGE_KEY_PREFIX`, ...+3 more

---

## arbiter/run_exploration.py
**Lines:** 681

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `setup_logging` |  | log_file |
| `load_config` | ✓ | path |
| `load_plugins` |  | plugin_folder |
| `notify_critical_error` |  | message, error |
| `run_agent_task` | ✓ | arbiter, agent_task, output_dir, arbiter_id, results |
| `run_agentic_workflow` | ✓ | config |
| `main` | ✓ |  |
| `start_health_server` | ✓ | config |
| `health_handler` | ✓ | request |

**Constants:** `logger`, `workflow_ops_total`, `workflow_errors_total`

---

## arbiter/stubs.py
**Lines:** 538

### `ArbiterStub`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `start_async_services` | ✓ | self |
| `stop_async_services` | ✓ | self |
| `respond` | ✓ | self |
| `plan_decision` | ✓ | self |
| `evolve` | ✓ | self |

### `PolicyEngineStub`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `should_auto_learn` | ✓ | self, component, action |
| `evaluate_policy` | ✓ | self, action, context |
| `check_circuit_breaker` | ✓ | self |

### `BugManagerStub`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `report_bug` | ✓ | self, bug_data |
| `get_bug` | ✓ | self, bug_id |
| `update_bug` | ✓ | self, bug_id, updates |

### `KnowledgeGraphStub`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `add_fact` | ✓ | self, domain, key, data |
| `find_related_facts` | ✓ | self, domain, key, value |
| `add_node` | ✓ | self, node_id, properties |
| `add_relationship` | ✓ | self, from_node, to_node, relationship_type |
| `query` | ✓ | self, query |
| `connect` | ✓ | self |
| `close` | ✓ | self |

### `HumanInLoopStub`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `request_approval` | ✓ | self, action, context, timeout |
| `notify` | ✓ | self, message, severity |

### `MessageQueueServiceStub`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `publish` | ✓ | self, topic, message |
| `subscribe` | ✓ | self, topic, handler |
| `start` | ✓ | self |
| `stop` | ✓ | self |

### `FeedbackManagerStub`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `record_feedback` | ✓ | self, component, feedback_type, data |

### `ArbiterArenaStub`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `coordinate` | ✓ | self, arbiters |

### `KnowledgeLoaderStub`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `load_knowledge` | ✓ | self, domain |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_log_stub_usage` |  | component, method |
| `is_using_stubs` |  |  |

**Constants:** `logger`, `_production_mode`, `_test_mode`, `_stub_warnings_shown`, `_stub_lock`, `__all__`

---

## arbiter/utils.py
**Lines:** 366

### `UtilsPlugin` (PluginBase)

| Method | Async | Args |
|--------|-------|------|
| `initialize` | ✓ | self |
| `start` | ✓ | self |
| `stop` | ✓ | self |
| `health_check` | ✓ | self |
| `get_capabilities` | ✓ | self |
| `random_chance` |  |  |
| `get_system_metrics` |  |  |
| `get_system_metrics_async` | ✓ |  |
| `get_health_session` | ✓ |  |
| `close_health_session` | ✓ |  |
| `check_service_health` | ✓ |  |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_get_or_create_metric` |  | metric_class, name, doc, labelnames |
| `is_valid_directory_path` |  | path |
| `safe_makedirs` |  | path, fallback |
| `random_chance` |  | probability |
| `get_system_metrics` |  |  |
| `get_system_metrics_async` | ✓ |  |
| `get_health_session` | ✓ |  |
| `close_health_session` | ✓ |  |
| `check_service_health` | ✓ | url |

**Constants:** `tracer`, `HEALTH_CHECK_TIMEOUT`, `HEALTH_CHECK_RATE_LIMIT_MAX_RATE`, `HEALTH_CHECK_RATE_LIMIT_TIME_PERIOD`, `logger`, `utils_ops_total`, `utils_errors_total`, `_HEALTH_SESSION`, `_HEALTH_SESSION_LOCK`, `_HEALTH_CHECK_LIMITER`

---

# MODULE: AUTO_FIX_TESTS.PY

## auto_fix_tests.py
**Lines:** 36

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `fix_async_tests` |  |  |

---

# MODULE: CLI.PY

## cli.py
**Lines:** 332

### `SFEPlatform`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `start` | ✓ | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `main_cli_loop` | ✓ |  |
| `check_status` | ✓ |  |
| `simple_scan` | ✓ |  |
| `repair_issues` | ✓ |  |
| `launch_arena_subprocess` | ✓ |  |

**Constants:** `ARBITER_DIR_NAME`

---

# MODULE: CONFIG.PY

## config.py
**Lines:** 273

### `ConfigWrapper`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, arbiter_config |
| `__getattr__` |  | self, name |
| `__repr__` |  | self |

### `GlobalConfigManager`

| Method | Async | Args |
|--------|-------|------|
| `get_config` |  | cls |
| `_load_config` |  | cls |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `setup_logging` |  |  |

**Constants:** `logger`

---

# MODULE: CONFTEST.PY

## conftest.py
**Lines:** 214

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `pytest_configure` |  | config |
| `pytest_collection_finish` |  | session |
| `aggressive_memory_cleanup` |  |  |
| `session_cleanup` |  |  |
| `setup_opentelemetry_tracer` |  |  |

---

# MODULE: ENVS

## envs/code_health_env.py
**Lines:** 1206

### `ActionType` (Enum)
**Attributes:** NOOP, RESTART, ROLLBACK, APPLY_PATCH, RUN_LINTER, RUN_TESTS, RUN_FORMATTER

### `EnvironmentConfig`

| Method | Async | Args |
|--------|-------|------|
| `validate` |  | self |

### `SystemMetrics`

| Method | Async | Args |
|--------|-------|------|
| `__post_init__` |  | self |
| `to_array` |  | self, keys |
| `to_dict` |  | self |

### `AsyncActionExecutor`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `_start_loop` |  | self |
| `_run_until_stopped` | ✓ | self |
| `execute` |  | self, coro |
| `close` |  | self |

### `CodeHealthEnv` (gym.Env)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, get_metrics, apply_action, audit_logger, session_id, ...+6 |
| `_get_current_metrics` |  | self |
| `_apply_action_wrapper` |  | self, action |
| `_check_action_cooldown` |  | self, action |
| `step` |  | self, action |
| `_check_and_handle_rollback` |  | self, current_action |
| `_check_termination` |  | self |
| `_compute_reward` |  | self, state, action, result |
| `_record_step` |  | self, action, action_name, reward, info |
| `_handle_episode_end` |  | self |
| `reset` |  | self, seed, options |
| `render` |  | self, mode |
| `_render_rgb_array` |  | self |
| `_render_ansi` |  | self |
| `get_training_data` |  | self |
| `get_metrics_summary` |  | self |
| `update_generator_metrics` |  | self, generation_success, critique_score, test_coverage_delta |
| `close` |  | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `run_code_health_simulation` |  |  |

**Constants:** `logger`

---

## envs/evolution.py
**Lines:** 875

### `ConfigurationSpace`

| Method | Async | Args |
|--------|-------|------|
| `gene_count` |  | self |
| `validate` |  | self |

### `EvolutionConfig`

| Method | Async | Args |
|--------|-------|------|
| `validate` |  | self |

### `FitnessEvaluator`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config, test_function |
| `_start_worker_pool` |  | self |
| `_evaluation_worker` |  | self |
| `evaluate_single` |  | self, individual |
| `_get_cache_key` |  | self, individual |
| `_evaluate_with_function` |  | self, individual |
| `_evaluate_sandboxed` |  | self, individual |
| `_evaluate_heuristic` |  | self, individual |
| `_map_genes_to_config` |  | self, individual |
| `_calculate_fitness` |  | self, metrics |
| `cleanup` |  | self |

### `GeneticOptimizer`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config_space, evolution_config |
| `_setup_deap` |  | self |
| `evolve` |  | self, test_function, audit_logger, verbose |
| `get_evolution_summary` |  | self |
| `save_checkpoint` |  | self, filepath |
| `load_checkpoint` |  | self, filepath |
| `__del__` |  | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `evolve_configs` |  | configs |
| `run_test_evaluation` |  | config |
| `run_evolution_demonstration` |  |  |

**Constants:** `logger`

---

# MODULE: EXCEPTIONS.PY

## exceptions.py
**Lines:** 29

### `AnalyzerCriticalError` (RuntimeError)

### `NonCriticalError` (Exception)

---

# MODULE: GUARDRAILS

## guardrails/audit_log.py
**Lines:** 1832

### `MockConfig`
**Attributes:** AUDIT_LOG_PATH, PRIVATE_KEY_PASSWORD, KAFKA_BOOTSTRAP_SERVERS, KAFKA_AUDIT_TOPIC, DLT_BACKEND_CONFIG

### `AuditLogger`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, log_path, signers, hash_algo, dlt_backend_enabled, ...+1 |
| `_initialize_dlt_backend_on_startup` | ✓ | self |
| `_load_last_hashes` |  | self |
| `_read_last_line` |  | self, filepath |
| `from_environment` |  | cls |
| `add_entry` | ✓ | self, kind, name, detail, agent_id, ...+3 |
| `_async_file_write` | ✓ | self, filepath, entry, log_context |
| `_sync_file_write` |  | self, filepath, entry, log_context |
| `log_event` |  | self, event_type, details, agent_id, correlation_id |
| `get_last_audit_hash` | ✓ | self, arbiter_id |
| `health_check` | ✓ | self |
| `log` |  | self, message, level |
| `close` | ✓ | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_lazy_load_dlt_backend` |  |  |
| `initialize_dlt_backend_clients` | ✓ | config |
| `get_EVMDLTClient` |  |  |
| `get_SimpleDLTClient` |  |  |
| `get_ProductionDLTClient` |  |  |
| `get_ProductionOffChainClient` |  |  |
| `sanitize_log` |  | msg |
| `validate_dependencies` |  |  |
| `validate_sensitive_env_vars` |  |  |
| `load_public_keys` |  |  |
| `current_utc_iso` |  |  |
| `_strip_signatures` |  | entry |
| `hash_entry` |  | entry, algo |
| `append_distributed_log` |  | entry, correlation_id |
| `load_private_key` |  |  |
| `key_rotation` | ✓ | audit_logger_instance, correlation_id |
| `key_revocation` |  | key_id, correlation_id |
| `verify_audit_chain` |  | log_path |
| `audit_log_event_async` | ✓ | event_type, message, data, agent_id, correlation_id, ...+2 |
| `main_cli` |  |  |

**Constants:** `logger`, `_base_logger`, `DLT_BACKEND_AVAILABLE`, `_dlt_client_instance`, `config`, `AUDIT_LOCK`, `DEFAULT_HASH_ALGO`, `REVOKED_KEYS`, `_initialized_dlt_backend`, `PUBLIC_KEY_STORE`

---

## guardrails/compliance_mapper.py
**Lines:** 789

### `ComplianceEnforcementError` (Exception)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, action_name, control_tag, message |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `sanitize_log` |  | msg |
| `_log_to_central_audit` | ✓ | event_name, details |
| `load_compliance_map` |  | config_path |
| `check_coverage` |  | compliance_map |
| `_audit_log_gap` |  | message, details |
| `_publish_compliance_to_arbiter` |  | compliance_map, coverage_gaps |
| `generate_report` |  | config_path |
| `health_check` |  |  |
| `write_dummy_config` |  | path, content |
| `main_cli` |  |  |

**Constants:** `logger`, `DEFAULT_CREW_CONFIG_PATH`, `CONFIG_PATH`

---

# MODULE: INTENT_CAPTURE

## intent_capture/agent_core.py
**Lines:** 1135

### `AgentError` (Exception)

### `LLMInitializationError` (AgentError)

### `StateManagementError` (AgentError)

### `InvalidSessionError` (AgentError)

### `ConfigurationError` (Exception)

### `SafetyViolationError` (AgentError)

### `MockLLM`

| Method | Async | Args |
|--------|-------|------|
| `ainvoke` | ✓ | self |

### `FallbackLLM`

| Method | Async | Args |
|--------|-------|------|
| `ainvoke` | ✓ | self |

### `LLMProviderFactory`
**Attributes:** _available_llm_classes, _llm_instance_cache

| Method | Async | Args |
|--------|-------|------|
| `get_usable_keys` | ✓ | provider |
| `get_llm` | ✓ | provider, model, temperature, retry_providers |

### `StateBackend` (ABC)

| Method | Async | Args |
|--------|-------|------|
| `load_state` | ✓ | self, session_id |
| `save_state` | ✓ | self, session_id, state |

### `RedisStateBackend` (StateBackend)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, client |
| `create` | ✓ | cls, redis_url |
| `load_state` | ✓ | self, session_id |
| `save_state` | ✓ | self, session_id, state |

### `AgentResponse` (BaseModel)

### `SafetyGuard`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `moderate` |  | self, text |

### `CollaborativeAgent`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, agent_id, session_id, llm, state_backend |
| `create` | ✓ | cls, agent_id, session_id, llm_config, state_backend |
| `_setup_runnable` |  | self |
| `_get_rag_context` |  | self, input_data |
| `save_state` | ✓ | self |
| `load_state` | ✓ | self |
| `_run_self_correction_cycle` | ✓ | self, user_input, timeout |
| `predict` | ✓ | self, user_input, timeout |

### `VaultSecretManager`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `get_secret` | ✓ | self, path, key |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_metric_timer` |  | metric |
| `sanitize_input` |  | input_text |
| `anonymize_pii` |  | text |
| `validate_session_token` | ✓ | token |
| `get_or_create_agent` | ✓ | session_token |
| `validate_environment` |  |  |
| `main` | ✓ |  |

**Constants:** `audit_logger`, `logger`, `llm_breaker`, `AGENT_CYCLE_COUNT`, `LLM_RESPONSE_LATENCY_SECONDS`, `RAG_QUERY_LATENCY_SECONDS`, `AGENT_PREDICTION_ERRORS_TOTAL`, `tracer`, `AGENT_CREATION_SEMAPHORE`

---

## intent_capture/api.py
**Lines:** 536

### `AppConfig` (BaseSettings)

| Method | Async | Args |
|--------|-------|------|
| `_get_secret` |  | self, key, vault_path, default |
| `__init__` |  | self |

### `PredictRequest` (BaseModel)

| Method | Async | Args |
|--------|-------|------|
| `validate_and_sanitize_input` |  | cls, v |
| `validate_token_format` |  | cls, v |

### `PredictResponse` (BaseModel)

### `SafetyViolationError` (HTTPException)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, detail |

### `AuditLoggingMiddleware` (BaseHTTPMiddleware)

| Method | Async | Args |
|--------|-------|------|
| `dispatch` | ✓ | self, request, call_next |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `global_exception_handler` | ✓ | request, exc |
| `agent_error_handler` | ✓ | request, exc |
| `get_redis_client` | ✓ |  |
| `get_current_user` | ✓ | token, redis_client |
| `set_user_state_for_limiter` | ✓ | request, current_user |
| `anonymize_pii` |  | text |
| `lifespan` | ✓ | app |
| `dynamic_rate_limiter` | ✓ | request |
| `create_app` |  |  |

**Constants:** `config`, `limiter`, `logger`, `SAFETY_VIOLATIONS_TOTAL`, `oauth2_scheme`, `app`

---

## intent_capture/autocomplete.py
**Lines:** 666

### `JsonFormatter` (logging.Formatter)

| Method | Async | Args |
|--------|-------|------|
| `format` |  | self, record |
| `_mask_pii` |  | self, message |

### `FernetEncryptor`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, key |
| `encrypt` |  | self, data |
| `decrypt` |  | self, token |

### `CommandRegistry`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `update_all_commands` |  | self |

### `AutocompleteState`
**Attributes:** _instance, _lock

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `instance` | ✓ | cls |
| `_initialize_dependencies` | ✓ | self |
| `_initialize_redis` | ✓ | self |
| `_fetch_key_from_vault` | ✓ | self |
| `_initialize_encryptor` | ✓ | self |

### `CommandCompleter`

| Method | Async | Args |
|--------|-------|------|
| `complete` |  | self, text, state_index |
| `_async_complete` | ✓ | self, text, state_index |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_moderation_pipeline` |  |  |
| `anonymize_pii` |  | text |
| `setup_logging` |  |  |
| `is_toxic` |  | text |
| `add_to_history` |  | line |
| `handle_command_not_found` |  | line, state |
| `get_ai_suggestions` | ✓ | text, state |
| `fuzzy_matches` | ✓ | text, candidates, state |
| `execute_macro` |  | input_text |
| `log_audit_event` |  | line, result |
| `setup_autocomplete` |  | llm |
| `prune_history` |  |  |
| `startup_validation` |  |  |

**Constants:** `__version__`, `logger`, `llm_breaker`, `tracer`, `COMPLETION_LATENCY_SECONDS`, `REDIS_OPS_TOTAL`, `AI_SUGGESTIONS_TOTAL`, `ACTIVE_PLUGINS`, `SAFETY_VIOLATIONS_TOTAL`, `KEY_REFRESH_SUCCESS_TIMESTAMP`, `TOKEN_USAGE`

---

## intent_capture/cli.py
**Lines:** 806

### `SessionState`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `get` | ✓ | self, key, default |
| `set` | ✓ | self, key, value |
| `get_agent` | ✓ | self |

### `CollabServer`
**Attributes:** MAX_CLIENTS

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, host, port |
| `_validate_token` | ✓ | self, token |
| `_network_guard` | ✓ | self |
| `handle_client` | ✓ | self, websocket, path |
| `start` | ✓ | self |
| `stop` | ✓ | self |

### `CLIInput` (BaseModel)

### `JsonFormatter` (logging.Formatter)

| Method | Async | Args |
|--------|-------|------|
| `format` |  | self, record |

### `CommandDispatcher`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, session_state |
| `dispatch` | ✓ | self, command, args |
| `_handle_help` | ✓ | self, args |
| `_handle_clear` | ✓ | self, args |
| `_handle_exit` | ✓ | self, args |
| `_handle_collab_start` | ✓ | self, args |
| `_collab_client_listener` | ✓ | self, uri, queue |
| `_handle_collab_stop` | ✓ | self, args |
| `_handle_security` | ✓ | self, args |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_get_or_create_metric` |  | metric_class, name, documentation, labelnames |
| `fetch_jwt_from_vault` |  |  |
| `validate_environment` |  |  |
| `setup_logging` |  |  |
| `shutdown_handler` |  | signum, frame |
| `global_exception_handler` |  | exc_type, exc_value, exc_traceback |
| `resource_guard` |  |  |
| `_local_input_worker` |  | loop, queue |
| `refresh_secrets_loop` | ✓ |  |
| `_moderation_pipeline` |  |  |
| `log_audit_event` |  | event_type, data |
| `main_cli_loop` | ✓ |  |

**Constants:** `PROD_ENV`, `logger`, `CONSOLE`, `tracer`, `_shutdown_event`, `agent_breaker`, `COMMAND_EXECUTION_TOTAL`, `COMMAND_LATENCY_SECONDS`, `CLI_RESOURCE_USAGE`, `ACTIVE_COLLAB_CLIENTS`, `SAFETY_VIOLATIONS_TOTAL`, `TOKEN_USAGE`, `command_cache`, `JWT_SECRET`

---

## intent_capture/config.py
**Lines:** 565

### `PiiMaskingFormatter` (logging.Formatter)

| Method | Async | Args |
|--------|-------|------|
| `format` |  | self, record |
| `_mask_pii` |  | self, message |

### `PluginManager`

| Method | Async | Args |
|--------|-------|------|
| `_verify_plugin_signature` |  | plugin_name, config_path |
| `discover_and_apply_plugins` |  | cls, config |

### `ConfigEncryptor`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, key |
| `encrypt_config` |  | self, file_path, data |
| `decrypt_config` |  | self, file_path |

### `Config` (BaseSettings)
**Attributes:** model_config

| Method | Async | Args |
|--------|-------|------|
| `validate_redis_url` |  | cls, v |
| `validate_log_level` |  | cls, v |

### `ConfigChangeHandler` (FileSystemEventHandler)

| Method | Async | Args |
|--------|-------|------|
| `on_modified` |  | self, event |

### `GlobalConfigManager`
**Attributes:** _lock, _observer, _reload_failure_count, _last_reload_time

| Method | Async | Args |
|--------|-------|------|
| `get_config` |  | cls |
| `_load_initial_config` |  | cls |
| `reload_config` |  | cls |
| `_start_watcher` |  | cls |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `setup_logging` |  |  |
| `fetch_from_vault` |  | path |
| `_fetch_config_from_service` |  |  |
| `log_audit_event` |  | event_type, data |
| `prune_audit_logs` |  | retention_days |
| `startup_validation` |  |  |

**Constants:** `PROD_MODE`, `CUSTOM_CONFIG_PATH`, `config_logger`, `tracer`, `service_breaker`

---

## intent_capture/io_utils.py
**Lines:** 575

### `FileManager`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, workspace |
| `validate_path` |  | self, path |
| `safe_open` |  | self, path, mode |

### `ScalableProvenanceLogger`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `log_event` |  | self, event |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `get_redis_client` | ✓ |  |
| `hash_file_distributed_cache` | ✓ | path, file_manager |
| `download_file_to_temp` | ✓ | url, file_manager |
| `_do_actual_download` | ✓ | url, file_manager, span |
| `log_audit_event` |  | event_type, data |
| `prune_audit_logs` |  | retention_days |
| `startup_validation` |  |  |

**Constants:** `PROD_MODE`, `PROVENANCE_SALT`, `WORKSPACE_DIR`, `utils_logger`, `telemetry_tracer`, `download_breaker`, `redis_breaker`, `last_download_time`

---

## intent_capture/requirements.py
**Lines:** 1416

### `RateLimitError` (Exception)

### `NullContext`

| Method | Async | Args |
|--------|-------|------|
| `__enter__` |  | self |
| `__exit__` |  | self, exc_type, exc_val, exc_tb |
| `__aenter__` | ✓ | self |
| `__aexit__` | ✓ | self, exc_type, exc_val, exc_tb |

### `RequirementsManager`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `get_embedding_model` | ✓ | self |
| `get_db_conn_pool` | ✓ | self |
| `db_get_custom_checklists` | ✓ | self, project |
| `db_save_custom_checklists` | ✓ | self, customs |
| `get_global_custom_checklists` | ✓ | self |
| `set_global_custom_checklists` | ✓ | self, customs |
| `get_checklist` | ✓ | self, domain, project |
| `add_item` | ✓ | self, domain, item_name, weight, description, ...+1 |
| `update_item_status` | ✓ | self, item_id, status, project, domain |
| `_generate_novel_requirements` | ✓ | self, context, llm |
| `_suggest_via_embeddings` | ✓ | self, domain, transcript_snippet, existing_checklist |
| `suggest_requirements` | ✓ | self, domain, transcript_snippet, existing_checklist, llm |
| `propose_checklist_updates` | ✓ | self, transcript, existing_checklist, llm |
| `log_coverage_snapshot` | ✓ | self, project, domain, coverage_percent, covered_items, ...+1 |
| `get_coverage_history` | ✓ | self, project |
| `generate_coverage_report` | ✓ | self, project |
| `compute_coverage` | ✓ | self, gaps_table_markdown, llm |
| `register_plugin_requirements` |  | self, domain_name, requirements |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `get_tracing_context` |  | span_name |
| `sanitize_text` |  | text, allow_punct, max_length |
| `validate_uuid` |  | uuid_str |
| `_load_json_file` | ✓ | file_path |
| `_save_json_file` | ✓ | file_path, data |
| `shutdown_cleanup` |  |  |
| `get_embedding_model` | ✓ |  |
| `get_db_conn_pool` | ✓ |  |
| `db_get_custom_checklists` | ✓ | project |
| `db_save_custom_checklists` | ✓ | customs |
| `get_global_custom_checklists` | ✓ |  |
| `set_global_custom_checklists` | ✓ | customs |
| `get_checklist` | ✓ | domain, project |
| `add_item` | ✓ | domain, item_name, weight, description, project |
| `update_item_status` | ✓ | item_id, status, project, domain |
| `_generate_novel_requirements` | ✓ | context, llm |
| `suggest_requirements` | ✓ | domain, transcript_snippet, existing_checklist, llm |
| `propose_checklist_updates` | ✓ | transcript, existing_checklist, llm |
| `log_coverage_snapshot` | ✓ | project, domain, coverage_percent, covered_items, total_items |
| `get_coverage_history` | ✓ | project |
| `generate_coverage_report` | ✓ | project |
| `compute_coverage` | ✓ | gaps_table_markdown, llm |
| `register_plugin_requirements` |  | domain_name, requirements |

**Constants:** `__version__`, `logger`, `COSINE_SIM_THRESHOLD`, `CACHE_TTL_SECONDS`, `LLM_GEN_TIMEOUT_SECONDS`, `LLM_PARSE_TIMEOUT_SECONDS`, `CUSTOM_CHECKLISTS_FILE`, `COVERAGE_HISTORY_FILE`, `manager`, `_file_lock`, `_model_lock`, `_EMBEDDING_MODEL`, `_db_pool`

---

## intent_capture/session.py
**Lines:** 847

### `AgentMemory` (BaseModel)

### `SessionMetadata` (BaseModel)

### `SessionState` (BaseModel)

| Method | Async | Args |
|--------|-------|------|
| `check_valid_id` |  | cls, v |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_get_config` |  |  |
| `_validate_path` |  | path, base_dir |
| `_get_session_path` |  | session_name |
| `_get_history_path` |  | session_name |
| `_atomic_write_json` | ✓ | filepath, data |
| `_read_json_file` | ✓ | filepath |
| `save_session` | ✓ | session_name, session_data |
| `load_session` | ✓ | session_name |
| `list_sessions` | ✓ |  |
| `export_spec` | ✓ | spec_content, file_format, output_path |
| `save_session_history` | ✓ | session_name, history_data |
| `load_session_history` | ✓ | session_name |
| `delete_session` | ✓ | session_name |
| `get_session_metadata` | ✓ | session_name |
| `prune_old_sessions` | ✓ | max_age_days |

**Constants:** `logger`, `_config_lock`

---

## intent_capture/spec_utils.py
**Lines:** 996

### `NullContext`

| Method | Async | Args |
|--------|-------|------|
| `__enter__` |  | self |
| `__exit__` |  | self, exc_type, exc_val, exc_tb |
| `__aenter__` | ✓ | self |
| `__aexit__` | ✓ | self, exc_type, exc_val, exc_tb |

### `TraceableArtifact`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, content, artifact_type, source_spec_id, generation_prompt |
| `update` |  | self, new_content, notes |
| `persist_metadata` |  | self |
| `to_dict` |  | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `get_tracing_context` |  | span_name |
| `with_retry` | ✓ | func, retries, delay |
| `_load_locales` |  |  |
| `get_localized_prompt` |  | key, lang |
| `load_ambiguous_words` |  | lang |
| `register_spec_handler` |  | format_name, validator, generator |
| `validate_spec` |  | spec, format, version, schema |
| `migrate_spec` |  | spec, format, from_version, to_version |
| `detect_ambiguity` |  | text, language |
| `auto_fix_spec` | ✓ | spec, llm, format, issues, language |
| `_generate_downstream_artifact` | ✓ | spec_id, spec_content, llm, prompt_template_key, artifact_type, ...+1 |
| `generate_code_stub` | ✓ | spec_id, spec_content, llm, language |
| `generate_test_stub` | ✓ | spec_id, spec_content, llm, framework, language |
| `generate_security_review` | ✓ | spec_id, spec_content, llm, language |
| `generate_spec_from_memory` | ✓ | memory, llm, format, persona, language, ...+2 |
| `generate_gaps` | ✓ | spec_content, transcript, llm, domain, project, ...+1 |
| `refine_spec` | ✓ | last_spec, instruction, llm, language |
| `review_spec` | ✓ | spec_content, llm, language |
| `diff_specs` |  | spec1, spec2 |

**Constants:** `logger`, `LOCALES_FILE`

---

## intent_capture/web_app.py
**Lines:** 982

### `JsonFormatter` (logging.Formatter)

| Method | Async | Args |
|--------|-------|------|
| `format` |  | self, record |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `load_locales` |  |  |
| `t` |  | key |
| `run_async` |  | coroutine |
| `init_session_state` |  |  |
| `generate_captcha` |  |  |
| `get_redis_client` |  |  |
| `render_chat_page` |  |  |
| `render_dashboard_page` |  |  |
| `render_specs_page` |  |  |
| `render_collab_page` |  |  |
| `render_plugins_page` |  |  |
| `render_health_page` |  |  |

**Constants:** `handler`, `logger`, `PROMETHEUS_AVAILABLE`, `LOCALES`, `redis_client`, `COLLAB_CHANNEL`

---

# MODULE: MAIN.PY

## main.py
**Lines:** 1175

### `_JsonFormatter` (logging.Formatter)

| Method | Async | Args |
|--------|-------|------|
| `format` |  | self, record |

### `_DummyMetric`

| Method | Async | Args |
|--------|-------|------|
| `labels` |  | self |
| `inc` |  | self |
| `observe` |  | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_maybe_enable_uvloop` |  |  |
| `_init_logging` |  | log_json |
| `_init_metrics` |  |  |
| `_init_sentry` |  |  |
| `_init_audit_logger` |  |  |
| `_init_simulation_module` |  |  |
| `_initialize_simulation_module` | ✓ |  |
| `_shutdown_simulation_module` | ✓ |  |
| `_simulation_health_check` | ✓ |  |
| `_init_test_generation` |  |  |
| `_initialize_test_generation` | ✓ |  |
| `_shutdown_test_generation` | ✓ |  |
| `_test_generation_health_check` | ✓ |  |
| `_init_arbiter` |  |  |
| `_initialize_arbiter` | ✓ |  |
| `_shutdown_arbiter` | ✓ |  |
| `_arbiter_health_check` | ✓ |  |
| `_windows_event_loop_policy_fix` |  |  |
| `_env_bool` |  | name, default |
| `_maybe_await` | ✓ | fn |
| `_quick_redis_check` | ✓ | redis_url, timeout_s |
| `startup_validation` | ✓ |  |
| `start_metrics_server` |  | metrics_port |
| `_retry_decorator` |  |  |
| `run_cli` | ✓ |  |
| `run_api` | ✓ | host, port, reload, root_path |
| `run_web` | ✓ |  |
| `_install_signal_handlers` |  | cancel |
| `main` | ✓ |  |

**Constants:** `VERSION`, `_pre`, `logger`, `_sentry`, `audit_logger`, `_simulation_module`, `_test_generation_orchestrator`, `_arbiter_instance`

---

# MODULE: MESH

## mesh/checkpoint/checkpoint_backends.py
**Lines:** 1461

### `Config`
**Attributes:** PROD_MODE, ENV, TENANT, REGION, ENCRYPTION_KEYS, HMAC_KEY, MAX_RETRIES, RETRY_DELAY, RETRY_MAX_DELAY, S3_BUCKET, S3_PREFIX, S3_REGION, S3_ENDPOINT, S3_USE_SSL, S3_STORAGE_CLASS

| Method | Async | Args |
|--------|-------|------|
| `validate_backend` |  | cls, backend |

### `EncryptionManager`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `_init_encryption` |  | self |
| `encrypt` |  | self, data |
| `decrypt` |  | self, data |
| `rotate_needed` |  | self, encrypted_data |

### `BackendRegistry`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `register` |  | self, name, handler |
| `get` |  | self, name |
| `get_client` | ✓ | self, backend, manager |
| `_initialize_backend` | ✓ | self, backend, manager |
| `_init_s3` | ✓ | self, manager |
| `_init_redis` | ✓ | self, manager |
| `_init_postgres` | ✓ | self, manager |
| `_init_gcs` | ✓ | self, manager |
| `_init_azure` | ✓ | self, manager |
| `_init_minio` | ✓ | self, manager |
| `_init_etcd` | ✓ | self, manager |
| `close` | ✓ | self, backend |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_generate_version_id` |  |  |
| `_sign_payload` |  | payload |
| `_verify_signature` |  | payload, signature |
| `_write_to_dlq` | ✓ | operation, backend, name, error, context |
| `backend_operation` |  | operation |
| `s3_save` | ✓ | manager, name, state, metadata |
| `s3_load` | ✓ | manager, name, version |
| `_s3_cleanup_versions` | ✓ | client, name, shard, keep_versions |
| `_s3_rotate_key` | ✓ | client, s3_key, data_bytes |
| `redis_save` | ✓ | manager, name, state, metadata |
| `redis_load` | ✓ | manager, name, version |
| `postgres_save` | ✓ | manager, name, state, metadata |
| `postgres_load` | ✓ | manager, name, version |
| `get_backend_handler` | ✓ | backend, operation |
| `_validate_environment` |  |  |

**Constants:** `__version__`, `__author__`, `__classification__`, `logger`, `audit_logger`, `executor`, `encryption_mgr`, `registry`

---

## mesh/checkpoint/checkpoint_exceptions.py
**Lines:** 478

### `CheckpointErrorCode` (Enum)
**Attributes:** GENERIC_ERROR, HASH_MISMATCH, HMAC_MISMATCH, AUDIT_FAILURE, BACKEND_UNAVAILABLE, PERMISSION_DENIED, VALIDATION_FAILURE, CIRCUIT_OPEN

### `CheckpointError` (Exception)
**Attributes:** MAX_CONTEXT_SIZE

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, context, error_code, severity |
| `__str__` |  | self |
| `sign_context` |  | self, secret |
| `raise_with_alert` | ✓ | cls, message, context, error_code |

### `CheckpointAuditError` (CheckpointError)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, context |

### `CheckpointBackendError` (CheckpointError)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, context, error_code |

### `CheckpointRetryableError` (CheckpointBackendError)

### `CheckpointValidationError` (CheckpointError)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, context |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `set_alert_callback` |  | callback |
| `_mask_long_string_values` |  | data |
| `retry_on_exception` |  | max_attempts, max_delay_seconds |

**Constants:** `__version__`, `MIN_VERSIONS`, `log_handler`, `root_logger`, `logger`, `audit_logger`, `EXCEPTION_COUNT`, `BREAKER`, `ALERT_CACHE`

---

## mesh/checkpoint/checkpoint_manager.py
**Lines:** 1822

### `Environment`
**Attributes:** PROD_MODE, ENV, TENANT, REGION, CHECKPOINT_DIR, AUDIT_LOG_PATH, DLQ_PATH, ENCRYPTION_KEYS, HMAC_KEY, REQUIRE_MFA, MAX_RETRIES, RETRY_DELAY, CACHE_TTL, CACHE_SIZE, LOG_LEVEL

| Method | Async | Args |
|--------|-------|------|
| `validate` |  | cls |

### `AuditLogger`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, log_path |
| `log` |  | self, event, context, level |

### `CheckpointManager`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, backend_type, keep_versions, audit_hook, state_schema, ...+4 |
| `_init_encryption` |  | self |
| `_init_caching` |  | self |
| `_init_backend_registry` |  | self |
| `initialize` | ✓ | self |
| `_init_backend` | ✓ | self |
| `_load_metadata` | ✓ | self |
| `_background_maintenance` | ✓ | self |
| `save` | ✓ | self, name, state, metadata, user |
| `load` | ✓ | self, name, version, user, auto_heal |
| `rollback` | ✓ | self, name, version, user, dry_run, ...+1 |
| `list_versions` | ✓ | self, name |
| `diff` | ✓ | self, name, version1, version2 |
| `status` | ✓ | self, name |
| `available` | ✓ | self |
| `healthcheck` | ✓ | self |
| `close` | ✓ | self |
| `_prepare_checkpoint` | ✓ | self, name, state, metadata, user |
| `_get_checkpoint_metadata` | ✓ | self, name |
| `_write_to_dlq` | ✓ | self, entry |
| `_process_dlq` | ✓ | self |
| `_local_backend_operations` | ✓ | self, operation |
| `_local_save` | ✓ | self, name, checkpoint_data |
| `_local_load` | ✓ | self, name, version, auto_heal |
| `_local_list_versions` | ✓ | self, name |
| `_local_available` | ✓ | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `get_checkpoint_manager` |  |  |
| `checkpoint_session` | ✓ |  |

**Constants:** `__version__`, `__author__`, `__classification__`, `audit_logger`, `logger`

---

## mesh/checkpoint/checkpoint_utils.py
**Lines:** 1365

### `SecurityConfig`
**Attributes:** PROD_MODE, FIPS_MODE, ENCRYPTION_ALGORITHM, KEY_DERIVATION, KDF_ITERATIONS, HASH_ALGORITHM, HMAC_ALGORITHM, COMPRESSION_ALGORITHM, COMPRESSION_LEVEL, MIN_KEY_LENGTH, KEY_ROTATION_DAYS, SECURE_DELETE, DATA_CLASSIFICATION, REQUIRE_ENCRYPTION

| Method | Async | Args |
|--------|-------|------|
| `validate` |  | cls |

### `CryptoProvider`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `_init_crypto` |  | self |
| `generate_key` |  | self, length, key_type |
| `derive_key` |  | self, password, salt, length, iterations |
| `encrypt_aes_gcm` |  | self, plaintext, key |
| `decrypt_aes_gcm` |  | self, ciphertext, key, nonce, tag |
| `secure_compare` |  | self, a, b |
| `secure_erase` |  | self, data |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `hash_data` |  | data, algorithm, encoding |
| `hash_dict` |  | data, prev_hash, algorithm |
| `compute_hmac` |  | data, key, algorithm |
| `verify_hmac` |  | data, key, expected_hmac, algorithm |
| `compress_data` |  | data, algorithm, level |
| `decompress_data` |  | data, algorithm |
| `compress_json` |  | data, algorithm, level |
| `decompress_json` |  | data, algorithm |
| `scrub_data` |  | data, patterns, replacement |
| `anonymize_data` |  | data, fields_to_anonymize, method |
| `deep_diff` |  | old_data, new_data, ignore_keys, track_type_changes |
| `create_fernet_key` |  | passphrase |
| `rotate_fernet_keys` |  | current_keys, new_key |
| `validate_checkpoint_data` |  | data, schema, max_size |
| `generate_checkpoint_id` |  |  |
| `format_size` |  | size_bytes |
| `parse_duration` |  | duration_str |
| `is_valid_identifier` |  | identifier |
| `_run_self_test` |  |  |

**Constants:** `__version__`, `__author__`, `__classification__`, `logger`, `audit_logger`, `SENSITIVE_PATTERNS`, `SENSITIVE_FIELD_PATTERNS`, `crypto`

---

## mesh/event_bus.py
**Lines:** 1388

### `AsyncSafeLogger`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, name, level |
| `_worker` |  | self |
| `start` |  | self |
| `stop` |  | self |
| `_log` |  | self, level, msg |
| `debug` |  | self, msg |
| `info` |  | self, msg |
| `warning` |  | self, msg |
| `error` |  | self, msg, exc_info |
| `critical` |  | self, msg, exc_info |

### `CircuitBreaker`

| Method | Async | Args |
|--------|-------|------|
| `record_failure` |  | self |
| `can_proceed` |  | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_get_or_create_metric` |  | metric_class, name, documentation, labelnames |
| `_enforce_prod_requirements` |  |  |
| `_get_fernet` |  |  |
| `_sign_payload` |  | payload |
| `_verify_signature` |  | payload, signature |
| `_inc_counter` | ✓ | counter |
| `_set_gauge` | ✓ | gauge, value |
| `_observe_histogram` |  | histogram, value |
| `_setup_bus` |  |  |
| `get_redis_client` |  |  |
| `_write_to_dlq` | ✓ | event_type, payload, error, original_id |
| `replay_dlq` | ✓ |  |
| `_prepare_payload` | ✓ | event_type, data, schema |
| `_process_received_payload` | ✓ | message |
| `publish_event` | ✓ | event_type, data, schema, is_replay |
| `publish_events` | ✓ | events |
| `subscribe_event` | ✓ | event_type, handler, consumer_group, consumer_name |
| `cleanup` |  |  |

**Constants:** `__version__`, `PROD_MODE`, `MAX_RETRIES`, `RETRY_DELAY`, `ENCRYPTION_KEY`, `HMAC_KEY`, `REDIS_USER`, `REDIS_PASSWORD`, `REDIS_URL`, `USE_REDIS_STREAMS`, `ENV`, `TENANT`, `DLQ_STREAM_NAME`, `MAX_STREAM_LENGTH`, `PUBLISH_RATE_LIMIT_RPS`, ...+4 more

---

## mesh/mesh_adapter.py
**Lines:** 1873

### `CircuitBreakerWrapper`

### `MeshPubSub`
**Attributes:** _SUPPORTED, SENSITIVE_KEYS, MAX_REDELIVERIES

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, backend_url, event_schema, dead_letter_path, log_payloads, ...+11 |
| `detect_backend` |  | url |
| `_sign_payload` |  | self, payload |
| `_prepare_payload` |  | self, message |
| `_process_incoming_payload` |  | self, data |
| `connect` | ✓ | self |
| `_scrub_payload` |  | self, payload |
| `_write_to_dlq` | ✓ | self, payload, native |
| `_write_to_dlq_native` | ✓ | self, payload |
| `publish` | ✓ | self, channel, message |
| `subscribe` | ✓ | self, channel |
| `replay_dlq` | ✓ | self |
| `close` | ✓ | self |
| `healthcheck` | ✓ | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `async_retry` |  | retries, delay, backoff |
| `_enforce_prod_requirements` |  |  |
| `get_tracing_context` |  |  |
| `_cap_label` |  | value, top_values |

**Constants:** `PROD_MODE`, `RETRIES`, `RETRY_DELAY`, `ENCRYPTION_KEY`, `HMAC_KEY`, `ENV`, `TENANT`, `KAFKA_USER`, `KAFKA_PASSWORD`, `RABBITMQ_USER`, `RABBITMQ_PASSWORD`, `ETCD_USER`, `ETCD_PASSWORD`, `RATE_LIMIT_RPS`, `TOP_CHANNELS`, ...+2 more

---

## mesh/mesh_policy.py
**Lines:** 1031

### `PolicyBackendError` (Exception)

### `CircuitBreakerConfig`

| Method | Async | Args |
|--------|-------|------|
| `get_or_create_breaker` |  | self |
| `reset_breaker` |  | self |

### `PolicySchema` (BaseModel)

### `MeshPolicyBackend`
**Attributes:** SENSITIVE_KEYS

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, backend_type, policy_schema |
| `healthcheck` | ✓ | self |
| `_validate_policy_schema` |  | self, policy_data |
| `_scrub_policy_data` |  | self, policy_data |
| `_generate_version` |  | self |
| `_do_save` | ✓ | self, policy_id, policy_data |
| `save` | ✓ | self, policy_id, policy_data, version |
| `load` | ✓ | self, policy_id, version |
| `_process_incoming_data` |  | self, data |
| `batch_save` | ✓ | self, policies |
| `rollback` | ✓ | self, policy_id, version |
| `replay_policy_dlq` | ✓ | self |

### `Policy`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, data |
| `check` | ✓ | self, rule |

### `MeshPolicyEnforcer`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, policy_id, backend |
| `load_policy` | ✓ | self, version |
| `enforce_policy` | ✓ | self, rule |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_enforce_prod_requirements` |  |  |
| `_sign_data` |  | data |
| `run_sync_in_executor` |  | func |
| `with_async_retry` | ✓ | async_func, max_retries, log_context, delay, backoff |
| `_dlq_policy_op` | ✓ | op, policy_id, error |

**Constants:** `PROD_MODE`, `MAX_RETRIES`, `RETRY_DELAY`, `ENCRYPTION_KEY`, `HMAC_KEY`, `JWT_SECRET`, `logger`, `audit_logger`, `multi_fernet`, `failure_cache`, `_version_counter`, `breakers`

---

# MODULE: PLUGINS

## plugins/analyzer_test_fixtures.py
**Lines:** 132

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `valid_config_yaml_path` |  | tmp_path |
| `valid_config_json_path` |  | tmp_path |
| `malformed_config_path` |  | tmp_path |
| `invalid_schema_config_path` |  | tmp_path |
| `mock_alert_operator` |  |  |
| `mock_audit_logger` |  |  |
| `tmp_config_file` |  | tmp_path |
| `mock_sys_exit` |  |  |
| `mock_os_env` |  |  |

---

## plugins/azure_eventgrid_plugin/azure_eventgrid_plugin.py
**Lines:** 708

### `AnalyzerCriticalError` (Exception)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, alert_level |

### `NonCriticalError` (Exception)

### `EventGridError` (Exception)

### `EventGridPermanentError` (EventGridError)

### `EventGridRetriableError` (EventGridError)

### `AzureEventGridAuditHook`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, endpoint_url, subject, data_version, retries, ...+7 |
| `_validate_endpoint` |  | self, url |
| `_sign_event` |  | self, event |
| `_ensure_session` | ✓ | self |
| `close` | ✓ | self |
| `audit_hook` | ✓ | self, event, details, event_id |
| `_send_batch` | ✓ | self, batch |
| `_batch_sender` | ✓ | self |
| `__aenter__` | ✓ | self |
| `__aexit__` | ✓ | self, exc_type, exc, tb |

**Constants:** `PRODUCTION_MODE`, `logger`, `PLUGIN_MANIFEST`

---

## plugins/core_audit.py
**Lines:** 722

### `_DropOnFullQueueHandler` (QueueHandler)

| Method | Async | Args |
|--------|-------|------|
| `enqueue` |  | self, record |

### `_SafeHandler` (logging.Handler)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, inner, strict, name |
| `setFormatter` |  | self, fmt |
| `emit` |  | self, record |
| `flush` |  | self |
| `close` |  | self |
| `_mirror` |  | self, event_type, err |

### `AuditLogger`
**Attributes:** _singleton_lock

| Method | Async | Args |
|--------|-------|------|
| `__new__` |  | cls |
| `__init__` |  | self, secrets_manager |
| `health_check` |  | self |
| `_get_config_value` |  | self, key, default, type_cast, bounds |
| `_load_context` |  | self |
| `_configure_handlers` |  | self |
| `_configure_logger_locked` |  | self |
| `log_event` |  | self, event_type, level |
| `log_exception` |  | self, event_type, exc |
| `update_context` |  | self |
| `reload` |  | self |
| `close` |  | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_json_fallback` |  | o |
| `_prod` |  |  |

**Constants:** `LogLevel`, `SCHEMA_VERSION`, `_INIT_ONCE_LOCK`, `audit_logger`

---

## plugins/core_secrets.py
**Lines:** 382

### `SecretsManager`
**Attributes:** _instance, _class_lock

| Method | Async | Args |
|--------|-------|------|
| `__new__` |  | cls |
| `__init__` |  | self, env_file, logger, allow_dotenv |
| `_load_env` |  | self |
| `_validate_name` |  | self, name |
| `get_secret` |  | self, name, required, default, type_cast, ...+2 |
| `get_required` |  | self, name |
| `get_with_fallback` |  | self, names |
| `reload` |  | self |
| `set_secret` |  | self, name, value |
| `clear_cache` |  | self |
| `clear_cache_key` |  | self, name |
| `get_choice` |  | self, name, choices |
| `get_json` |  | self, name |
| `get_list` |  | self, name, sep |
| `get_int` |  | self, name |
| `get_float` |  | self, name |
| `get_bool` |  | self, name |
| `get_path` |  | self, name |
| `get_int_in_range` |  | self, name |
| `get_bytes` |  | self, name |
| `get_duration` |  | self, name |
| `snapshot` |  | self, keys |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_cast_to_bool` |  | value |
| `cast_bool_strict` |  | value |

**Constants:** `__all__`, `_ENV_NAME_RE`, `SECRETS_MANAGER`

---

## plugins/core_utils.py
**Lines:** 614

### `AlertDispatcher` (threading.Thread)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, operator |
| `run` |  | self |
| `stop` |  | self, timeout, drain |
| `enqueue` |  | self, sink_type, data |
| `_post_with_retry` |  | self, url, payload, timeout, attempts |
| `_dispatch_slack` |  | self, data |
| `_smtp_client` |  | self |
| `_dispatch_email` |  | self, data |

### `AlertOperator`
**Attributes:** _instance, _lock, _log_file_warning_issued

| Method | Async | Args |
|--------|-------|------|
| `__new__` |  | cls |
| `__init__` |  | self, secrets_manager, audit_logger |
| `_on_exit` |  | self |
| `_configure_logger` |  | self |
| `_load_context` |  | self |
| `_get_signature` |  | self, message |
| `_log_rate_limited_alert` |  | self, key |
| `_allow_event` |  | self, key |
| `alert` |  | self, message, level |
| `update_context` |  | self |
| `reload` |  | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_scrub_str` |  | s |
| `scrub` |  | obj |
| `safe_err` |  | exc |
| `_truncate` |  | s, max_len |
| `_reject_header_injection` |  |  |
| `get_alert_operator` |  |  |
| `send_alert` |  |  |

**Constants:** `_SENSITIVE_KV`, `_SENSITIVE_QP`, `_JWT`, `_AUTH`, `_SLACK_WEBHOOK`, `_API_KEY`, `_AWS_KEY`, `_GCP_KEY`, `_PRIVATE_KEY_BLOCK`

---

## plugins/demo_python_plugin.py
**Lines:** 241

### `NonCriticalError` (Exception)

### `PLUGIN_API`

| Method | Async | Args |
|--------|-------|------|
| `hello` |  | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `plugin_health` |  |  |

**Constants:** `PRODUCTION_MODE`, `logger`, `PLUGIN_MANIFEST`

---

## plugins/dlt_backend/dlt_backend.py
**Lines:** 1170

### `AnalyzerCriticalError` (Exception)

### `NonCriticalError` (Exception)

### `HashChainError` (Exception)

### `CheckpointManager`
**Attributes:** _backends, enable_hash_chain, state_schema

| Method | Async | Args |
|--------|-------|------|
| `register_backend` |  | cls, name |
| `__init__` |  | self, backend, enable_hash_chain, state_schema, encrypt_key |
| `save` | ✓ | self, name, state, metadata |
| `load` | ✓ | self, name, version |
| `rollback` | ✓ | self, name, version |
| `diff` | ✓ | self, name, v1, v2 |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_should_alert` | ✓ | key, ttl |
| `async_retry` |  | retries, delay, backoff |
| `_calculate_hash` |  | data |
| `_verify_hash_chain` |  | expected_prev_hash, actual_prev_hash, checkpoint_name, version |
| `_maybe_sign_checkpoint` |  | checkpoint_data |
| `compress_json` |  | data |
| `decompress_json` |  | data |
| `encrypt` |  | plaintext, key |
| `decrypt` |  | ciphertext, key |
| `initialize_dlt_backend` | ✓ | config |
| `_lock_factory` |  |  |
| `_maybe_dist_lock` | ✓ | name |
| `dlt_backend` | ✓ | self, op, name |
| `_dlt_backend_impl` | ✓ | self, op, name |
| `_deep_diff` |  | a, b |
| `_run_initialization_and_test` | ✓ |  |

**Constants:** `HAVE_AESGCM`, `PRODUCTION_MODE`, `logger`, `REDIS_CLIENT`, `NAME_RE`, `_save_locks`, `DIST_TTL`, `CACHE_TTL`

---

## plugins/grpc_runner.py
**Lines:** 933

### `AnalyzerCriticalError` (Exception)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, alert_level |

### `NonCriticalError` (Exception)

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_noop_plugin` |  |  |
| `_get_plugin_decorator` |  |  |
| `_get_plugin_kind` |  | kind_name |
| `_get_tls_credentials` |  |  |
| `_is_endpoint_allowed` |  | address |
| `plugin_health` | ✓ | channel, plugin_name, health_check_method_name |
| `connect` | ✓ | address, retries, backoff_sec, max_backoff_sec |
| `run_method` | ✓ | stub, method_name, request, timeout |
| `emit_metric` |  | name, value, labels, metric_type |
| `validate_manifest` |  | manifest_data |
| `list_plugins` |  | directory_path |
| `generate_plugin_docs` |  | manifest, output_path |
| `start_prometheus_exporter` | ✓ | address, port |

**Constants:** `_PLUGIN_REGISTRY_AVAILABLE`, `PlugInKind`, `plugin`, `PRODUCTION_MODE`, `logger`, `MISSING_DEPS`, `_metrics_registry`, `PLUGIN_HEALTH_GAUGE`, `PLUGIN_OPERATION_COUNTER`, `GRPC_TLS_CERT_PATH_SECRET`, `GRPC_TLS_KEY_PATH_SECRET`, `GRPC_TLS_CA_PATH_SECRET`, `GRPC_ENDPOINT_ALLOWLIST_SECRET`

---

## plugins/kafka/kafka_plugin.py
**Lines:** 1028

### `StartupDependencyMissing` (RuntimeError)

### `QueueDrainTimeout` (RuntimeError)

### `PermanentSendError` (RuntimeError)

### `MisconfigurationError` (RuntimeError)

### `KafkaConfig`

| Method | Async | Args |
|--------|-------|------|
| `from_env_and_secrets` |  |  |
| `_validate` |  | self |

### `KafkaAuditPlugin`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config |
| `initialize` | ✓ | self |
| `start` | ✓ | self |
| `stop` | ✓ | self, drain_timeout |
| `enqueue_event` | ✓ | self, event_type, details |
| `_sender_loop` | ✓ | self |
| `_flush_batch` | ✓ | self, batch |
| `_send_chunk` | ✓ | self, chunk, span |
| `_send_with_retry` | ✓ | self, topic, key, value, headers, ...+2 |
| `_send_to_dlq` | ✓ | self, dlq_topic, payload, reason, headers |
| `health` | ✓ | self |
| `_is_retryable` |  | exc |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_utc_now_iso` |  |  |
| `_sign_event` |  | hmac_key, payload |
| `_jittered_backoff_ms` |  | base_ms, attempt, cap_ms |
| `_serialize_event` |  | event |
| `build_plugin_from_env` |  |  |

**Constants:** `logger`, `PRODUCTION_MODE`, `_AIOKAFKA_AVAILABLE`, `_kafka_errors`, `_kafka_sent`, `_kafka_retried`, `_kafka_dropped`, `_kafka_dlq`, `_kafka_queue_depth`, `_kafka_latency_seconds`

---

## plugins/pagerduty_plugin/pagerduty_plugin.py
**Lines:** 1023

### `StartupCriticalError` (Exception)

### `PagerDutyEventError` (Exception)

### `PagerDutyJsonFormatter` (logging.Formatter)

| Method | Async | Args |
|--------|-------|------|
| `format` |  | self, record |

### `PagerDutySettings` (BaseSettings)
**Attributes:** model_config

| Method | Async | Args |
|--------|-------|------|
| `validate_production_mode_settings` |  | self |

### `PagerDutyMetrics`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, registry |

### `PagerDutyEventPayload` (BaseModel)

| Method | Async | Args |
|--------|-------|------|
| `validate_timestamp` |  | cls, v |
| `validate_and_scrub_pii` |  | cls, data |

### `PagerDutyAPIRequest` (BaseModel)

| Method | Async | Args |
|--------|-------|------|
| `payload_required_for_trigger` |  | cls, v, info |
| `_sign_request` |  | self |

### `PagerDutyGateway`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, settings, metrics |
| `startup` | ✓ | self |
| `shutdown` | ✓ | self |
| `_get_session` | ✓ | self |
| `_check_circuit_breaker` | ✓ | self |
| `_handle_success` |  | self |
| `_handle_failure` |  | self, event_action, dedup_key |
| `_send_request` | ✓ | self, request |
| `_event_processor_task` | ✓ | self, worker_id |
| `_enqueue_request` | ✓ | self, request |
| `trigger` | ✓ | self, event_name, details, severity, source, ...+1 |
| `acknowledge` | ✓ | self, dedup_key |
| `resolve` | ✓ | self, dedup_key |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `get_redis_client` | ✓ |  |

**Constants:** `PRODUCTION_MODE`, `logger`, `_ISO8601_Z_REGEX`, `pd_settings`, `pd_metrics`, `pagerduty_gateway`

---

## plugins/pubsub_plugin/pubsub_plugin.py
**Lines:** 1038

### `AnalyzerCriticalError` (Exception)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, alert_level |

### `NonCriticalError` (Exception)

### `PubSubSettings` (BaseSettings)
**Attributes:** model_config

| Method | Async | Args |
|--------|-------|------|
| `validate_project_id_in_prod` |  | cls, v, values |
| `validate_topic_id_in_prod` |  | cls, v, values |
| `validate_gcp_credentials_source` |  | cls, v |
| `validate_dry_run_in_prod` |  | cls, v |

### `PubSubMetrics`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, registry |

### `AuditEvent` (BaseModel)

| Method | Async | Args |
|--------|-------|------|
| `validate_details_for_pii` |  | cls, v |

### `CircuitBreaker`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, threshold, reset_seconds, metrics |
| `check` |  | self |
| `record_failure` |  | self |
| `record_success` |  | self |

### `PubSubGateway`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, settings, metrics |
| `_load_gcp_credentials` | ✓ | self |
| `startup` | ✓ | self |
| `shutdown` | ✓ | self |
| `publish` |  | self, event_name, service_name, details |
| `_publish_batch` | ✓ | self, batch |
| `_worker` | ✓ | self |
| `__aenter__` | ✓ | self |
| `__aexit__` | ✓ | self, exc_type, exc, tb |

**Constants:** `PRODUCTION_MODE`, `logger`, `_metrics_registry_instance`, `pubsub_gateway`

---

## plugins/rabbitmq_plugin/rabbitmq_plugin.py
**Lines:** 1087

### `AnalyzerCriticalError` (Exception)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, alert_level |

### `NonCriticalError` (Exception)

### `RabbitMQSettings` (BaseSettings)
**Attributes:** model_config

| Method | Async | Args |
|--------|-------|------|
| `validate_url_in_prod` |  | cls, v, values |
| `_get_allowed_exchange_names` | ✓ | cls |
| `validate_exchange_name_in_prod` |  | cls, v, values |
| `validate_allowed_routing_keys_regex` |  | cls, v |
| `validate_dry_run_in_prod` |  | cls, v |

### `RabbitMQMetrics`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, registry |

### `AuditEvent` (BaseModel)

| Method | Async | Args |
|--------|-------|------|
| `validate_details_for_pii` |  | cls, v |
| `_sign_event` |  | self |

### `CircuitBreaker`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, threshold, reset_seconds, metrics |
| `check` |  | self |
| `record_failure` |  | self |
| `record_success` |  | self |

### `RabbitMQGateway`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, settings, metrics |
| `_connect_with_retry` | ✓ | self |
| `startup` | ✓ | self |
| `shutdown` | ✓ | self |
| `publish` |  | self, event_name, service_name, details, routing_key |
| `_publish_batch` | ✓ | self, batch |
| `_worker` | ✓ | self |
| `__aenter__` | ✓ | self |
| `__aexit__` | ✓ | self, exc_type, exc, tb |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `run_health_check_server` | ✓ |  |

**Constants:** `PRODUCTION_MODE`, `logger`, `_metrics_registry_instance`, `rabbitmq_gateway`

---

## plugins/siem_plugin/siem_plugin.py
**Lines:** 1794

### `AuditJsonFormatter` (jsonlogger.JsonFormatter)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `add_fields` |  | self, log_record, message_dict |

### `SIEMTarget` (BaseSettings)

| Method | Async | Args |
|--------|-------|------|
| `validate_url_protocol` |  | cls, v |

### `SIEMGatewaySettings` (BaseSettings)
**Attributes:** model_config

| Method | Async | Args |
|--------|-------|------|
| `__setattr__` |  | self, name, value |
| `validate_secrets` |  | cls, v, info |
| `validate_admin_api_host` |  | cls, v |
| `load_from_secure_vault` |  | cls |

### `SIEMMetrics`
**Attributes:** EVENTS_QUEUED, EVENTS_DROPPED, EVENTS_SENT_SUCCESS, EVENTS_FAILED_PERMANENTLY, DEAD_LETTER_EVENTS, SEND_LATENCY, CIRCUIT_BREAKER_STATUS, RATE_LIMIT_THROTTLED_SECONDS, ACTIVE_WORKERS, QUEUE_SIZE, QUEUE_LATENCY, SYSTEM_CPU_USAGE, SYSTEM_MEMORY_USAGE

| Method | Async | Args |
|--------|-------|------|
| `update_system_metrics` |  | self |

### `SIEMEvent` (BaseModel)
**Attributes:** SENSITIVE_KEYS, SENSITIVE_PATTERNS

| Method | Async | Args |
|--------|-------|------|
| `scrub_sensitive_details` |  | cls, v |

### `Serializer` (Protocol)

| Method | Async | Args |
|--------|-------|------|
| `encode_batch` |  | self, batch, hostname, default_index |
| `content_type` |  | self |

### `JsonHecSerializer`

| Method | Async | Args |
|--------|-------|------|
| `content_type` |  | self |
| `encode_batch` |  | self, batch, hostname, default_index |

### `GzipJsonHecSerializer` (JsonHecSerializer)

| Method | Async | Args |
|--------|-------|------|
| `content_type` |  | self |
| `encode_batch` |  | self, batch, hostname, default_index |

### `EventQueue` (Protocol)

| Method | Async | Args |
|--------|-------|------|
| `startup` | ✓ | self |
| `shutdown` | ✓ | self |
| `put` | ✓ | self, item |
| `get` | ✓ | self |
| `qsize` |  | self |
| `task_done` | ✓ | self |
| `flush` | ✓ | self |

### `PersistentWALQueue` (EventQueue)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, target_name, persistence_dir, max_in_memory_size, encryption_key |
| `startup` | ✓ | self |
| `_open_next_log_segment` | ✓ | self |
| `put` | ✓ | self, item |
| `get` | ✓ | self |
| `qsize` |  | self |
| `task_done` | ✓ | self |
| `flush` | ✓ | self, timeout |
| `shutdown` | ✓ | self |

### `CircuitBreaker`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, threshold, reset_seconds, metrics, target_name |
| `check` |  | self |
| `record_failure` |  | self |
| `record_success` |  | self |

### `TokenBucket`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, rate, capacity, metrics, target_name |
| `acquire` | ✓ | self |
| `_refill` |  | self |
| `record_status` |  | self, status |

### `SIEMGateway`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, target_config, global_settings, metrics, serializer, ...+1 |
| `startup` | ✓ | self |
| `shutdown` | ✓ | self |
| `pause` |  | self |
| `resume` |  | self |
| `_get_session` | ✓ | self |
| `_handle_dead_letter` | ✓ | self, event, reason |
| `publish` | ✓ | self, event |
| `_publish_batch` | ✓ | self, batch |
| `_worker` | ✓ | self, worker_id |
| `_worker_manager` | ✓ | self |
| `_heartbeat` | ✓ | self |

### `SIEMGatewayManager`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, settings, metrics, dead_letter_hook |
| `startup` | ✓ | self |
| `_run_system_metrics_collector` | ✓ | self |
| `shutdown` | ✓ | self |
| `load_serializers_from_plugins` |  | self, group |
| `register_serializer` |  | self, name, serializer |
| `reload_config` | ✓ | self, new_settings |
| `publish` |  | self, target_name, event_name, details |
| `health_check` | ✓ | self |
| `_run_admin_api_server` | ✓ | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `dead_letter_to_file` | ✓ | event, reason |
| `app_lifecycle` | ✓ |  |

**Constants:** `PROD_MODE`, `AUDIT_LOG_PATH`, `main_logger`, `OPENTELEMETRY_AVAILABLE`, `tracer`, `TraceContextTextMapPropagator`, `DeadLetterHook`, `DEAD_LETTER_DIR`

---

## plugins/slack_plugin/slack_plugin.py
**Lines:** 2011

### `AnalyzerCriticalError` (Exception)

### `AuditJsonFormatter` (jsonlogger.JsonFormatter)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `add_fields` |  | self, log_record, message_dict |

### `SlackTarget` (BaseSettings)

| Method | Async | Args |
|--------|-------|------|
| `validate_url_protocol` |  | cls, v, info |

### `SlackGatewaySettings` (BaseSettings)
**Attributes:** model_config

| Method | Async | Args |
|--------|-------|------|
| `__setattr__` |  | self, name, value |
| `validate_secrets` |  | cls, v, info |
| `validate_admin_api_host` |  | cls, v, info |
| `load_from_secure_vault` |  | cls |

### `SlackMetrics`
**Attributes:** NOTIFICATIONS_QUEUED, NOTIFICATIONS_DROPPED, NOTIFICATIONS_SENT_SUCCESS, NOTIFICATIONS_FAILED_PERMANENTLY, DEAD_LETTER_NOTIFICATIONS, SEND_LATENCY, CIRCUIT_BREAKER_STATUS, RATE_LIMIT_THROTTLED_SECONDS, ACTIVE_WORKERS, NON_TRACED_NOTIFICATIONS, QUEUE_SIZE, QUEUE_LATENCY, RETRY_ATTEMPTS, SYSTEM_CPU_USAGE, SYSTEM_MEMORY_USAGE

| Method | Async | Args |
|--------|-------|------|
| `update_system_metrics` |  | self |

### `SlackEvent` (BaseModel)

| Method | Async | Args |
|--------|-------|------|
| `scrub_sensitive_details` |  | cls, v, info |

### `Serializer` (Protocol)

| Method | Async | Args |
|--------|-------|------|
| `encode_payload` |  | self, event, target_config, hostname |

### `SlackBlockKitSerializer`
**Attributes:** SEVERITY_COLORS

| Method | Async | Args |
|--------|-------|------|
| `encode_payload` |  | self, event, target_config, hostname |

### `EventQueue` (Protocol)

| Method | Async | Args |
|--------|-------|------|
| `startup` | ✓ | self |
| `shutdown` | ✓ | self |
| `put` | ✓ | self, item |
| `get` | ✓ | self |
| `qsize` |  | self |
| `task_done` | ✓ | self |
| `flush` | ✓ | self, timeout |

### `PriorityEventQueue` (EventQueue)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, maxsize |
| `startup` | ✓ | self |
| `shutdown` | ✓ | self |
| `put` | ✓ | self, item |
| `get` | ✓ | self |
| `qsize` |  | self |
| `task_done` | ✓ | self |
| `flush` | ✓ | self, timeout |

### `PersistentWALQueue` (EventQueue)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, target_name, persistence_dir, max_in_memory_size, encryption_key |
| `startup` | ✓ | self |
| `_create_signature` |  | self, event |
| `_open_next_log_segment` | ✓ | self |
| `put` | ✓ | self, item |
| `get` | ✓ | self |
| `qsize` |  | self |
| `task_done` | ✓ | self |
| `flush` | ✓ | self, timeout |
| `_wal_compactor` | ✓ | self |
| `shutdown` | ✓ | self |

### `CircuitBreaker`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, threshold, reset_seconds, metrics, target_name |
| `check` |  | self |
| `record_failure` |  | self |
| `record_success` |  | self |

### `TokenBucket`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, rate, capacity, metrics, target_name |
| `acquire` | ✓ | self |
| `_refill` |  | self |
| `record_status` |  | self, status |

### `SlackGateway`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, target_config, global_settings, metrics, serializer, ...+2 |
| `startup` | ✓ | self |
| `shutdown` | ✓ | self |
| `pause` |  | self |
| `resume` |  | self |
| `_get_session` | ✓ | self |
| `_handle_dead_letter` | ✓ | self, event, reason |
| `publish` | ✓ | self, event |
| `_send_event` | ✓ | self, event |
| `_worker` | ✓ | self, worker_id |
| `_worker_manager` | ✓ | self |
| `_heartbeat` | ✓ | self |

### `SlackGatewayManager`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, settings, metrics, dead_letter_hook |
| `startup` | ✓ | self |
| `_run_system_metrics_collector` | ✓ | self |
| `shutdown` | ✓ | self |
| `_load_sequence_counters` | ✓ | self |
| `_save_sequence_counter` | ✓ | self, target_name, seq_id |
| `load_serializers_from_plugins` |  | self, group |
| `register_serializer` |  | self, name, serializer |
| `reload_config` | ✓ | self, new_settings |
| `publish` | ✓ | self, target_name, event_name, details |
| `health_check` | ✓ | self |
| `_run_admin_api_server` | ✓ | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `load_template` |  | event_name |
| `dead_letter_to_file` | ✓ | event, reason |
| `app_lifecycle` | ✓ | main_func |

**Constants:** `PROD_MODE`, `AUDIT_LOG_PATH`, `audit_logger`, `main_logger`, `log_handler`, `log_formatter`, `OPENTELEMETRY_AVAILABLE`, `tracer`, `TraceContextTextMapPropagator`, `DeadLetterHook`, `DEAD_LETTER_DIR`

---

## plugins/sns_plugin/sns_plugin.py
**Lines:** 2048

### `AuditJsonFormatter` (jsonlogger.JsonFormatter)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `add_fields` |  | self, log_record, message_dict |

### `SNSTarget` (BaseSettings)

| Method | Async | Args |
|--------|-------|------|
| `validate_topic_arn` |  | cls, v |

### `SNSGatewaySettings` (BaseSettings)
**Attributes:** model_config

| Method | Async | Args |
|--------|-------|------|
| `__setattr__` |  | self, name, value |
| `validate_secrets` |  | cls, v, info |
| `validate_admin_api_host` |  | cls, v |
| `load_from_secure_vault` |  | cls |

### `SNSMetrics`
**Attributes:** NOTIFICATIONS_QUEUED, NOTIFICATIONS_DROPPED, NOTIFICATIONS_SENT_SUCCESS, NOTIFICATIONS_FAILED_PERMANENTLY, DEAD_LETTER_NOTIFICATIONS, SEND_LATENCY, CIRCUIT_BREAKER_STATUS, RATE_LIMIT_THROTTLED_SECONDS, ACTIVE_WORKERS, NON_TRACED_NOTIFICATIONS, QUEUE_SIZE, WAL_COMPACTIONS, QUEUE_LATENCY, RETRY_ATTEMPTS, SYSTEM_CPU_USAGE

| Method | Async | Args |
|--------|-------|------|
| `update_system_metrics` |  | self |

### `SNSEvent` (BaseModel)
**Attributes:** SENSITIVE_KEYS, SENSITIVE_PATTERNS

| Method | Async | Args |
|--------|-------|------|
| `scrub_sensitive_details` |  | cls, v |

### `Serializer` (Protocol)

| Method | Async | Args |
|--------|-------|------|
| `encode_payload` |  | self, event |

### `JsonSerializer`

| Method | Async | Args |
|--------|-------|------|
| `encode_payload` |  | self, event |

### `EventQueue` (Protocol)

| Method | Async | Args |
|--------|-------|------|
| `startup` | ✓ | self |
| `shutdown` | ✓ | self |
| `put` | ✓ | self, item |
| `get` | ✓ | self |
| `qsize` |  | self |
| `task_done` | ✓ | self |
| `flush` | ✓ | self, timeout |

### `PersistentWALQueue` (EventQueue)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, target_name, persistence_dir, max_in_memory_size, metrics, ...+2 |
| `startup` | ✓ | self |
| `_create_signature` |  | self, event |
| `_open_next_log_segment` | ✓ | self |
| `put` | ✓ | self, item |
| `get` | ✓ | self |
| `qsize` |  | self |
| `task_done` | ✓ | self |
| `flush` | ✓ | self, timeout |
| `_wal_compactor` | ✓ | self |
| `shutdown` | ✓ | self |

### `CircuitBreaker`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, threshold, reset_seconds, metrics, target_name |
| `check` |  | self |
| `record_failure` |  | self |
| `record_success` |  | self |

### `TokenBucket`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, rate, capacity, metrics, target_name |
| `acquire` | ✓ | self |
| `_refill` |  | self |
| `record_status` |  | self, status |

### `SNSGateway`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, target_config, global_settings, metrics, serializer, ...+2 |
| `startup` | ✓ | self |
| `shutdown` | ✓ | self |
| `pause` |  | self |
| `resume` |  | self |
| `_load_sequence_counter` | ✓ | self |
| `_save_sequence_counter` | ✓ | self, seq_id |
| `_get_session` | ✓ | self |
| `_handle_dead_letter` | ✓ | self, event, reason |
| `publish` | ✓ | self, event |
| `_send_batch` | ✓ | self, batch |
| `_worker` | ✓ | self, worker_id |
| `_worker_manager` | ✓ | self |
| `_heartbeat` | ✓ | self |

### `SNSGatewayManager`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, settings, metrics, dead_letter_hook |
| `startup` | ✓ | self |
| `_run_system_metrics_collector` | ✓ | self |
| `shutdown` | ✓ | self |
| `_log_admin_action` | ✓ | self, action, details |
| `load_serializers_from_plugins` |  | self, group |
| `register_serializer` |  | self, name, serializer |
| `reload_config` | ✓ | self, new_settings |
| `publish` | ✓ | self, target_name, event_name, details |
| `health_check` | ✓ | self |
| `_run_admin_api_server` | ✓ | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `dead_letter_to_file` | ✓ | event, reason |
| `app_lifecycle` | ✓ | main_func |

**Constants:** `PROD_MODE`, `AUDIT_LOG_PATH`, `audit_logger`, `main_logger`, `log_handler`, `log_formatter`, `OPENTELEMETRY_AVAILABLE`, `tracer`, `TraceContextTextMapPropagator`, `DeadLetterHook`, `DEAD_LETTER_DIR`

---

## plugins/wasm_runner.py
**Lines:** 1061

### `WasmRunnerError` (Exception)

### `WasmStartupError` (WasmRunnerError)

### `WasmExecutionError` (WasmRunnerError)

### `AnalyzerCriticalError` (Exception)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, alert_level |

### `NonCriticalError` (Exception)

### `WasmManifestModel` (BaseModel)
**Attributes:** model_config

| Method | Async | Args |
|--------|-------|------|
| `check_no_dummy_fields` |  | cls, v |
| `validate_sandbox_config` |  | cls, v |

### `WasmRunner`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, plugin_name, manifest, plugins_dir, whitelisted_plugin_dirs, ...+1 |
| `_validate_manifest_signature` |  | self, manifest |
| `create` | ✓ | cls, plugin_name, manifest, plugins_dir, whitelisted_plugin_dirs |
| `_setup_resource_limits_config` |  | self |
| `_define_host_functions` |  | self |
| `_validate_module_memory_constraints` |  | self, module |
| `_load_module_async` | ✓ | self |
| `_instantiate_module` |  | self |
| `_write_bytes` |  | self, payload |
| `_read_bytes` |  | self, ptr, length, cap |
| `run_function` | ✓ | self, func_name |
| `plugin_health` | ✓ | self |
| `reload_if_changed` | ✓ | self, operator_approved |
| `close` |  | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_is_in_allowlist` |  | path, allowed_dirs |
| `_safe_exports_get` |  | store, instance, name, typ |
| `host_log_closure` |  | runner_instance |
| `_validate_manifest_signature_dict` |  | manifest |
| `list_plugins` |  | plugins_dir, whitelisted_plugin_dirs |
| `_allow_out_path` |  | out_file, allowed_dirs |
| `generate_plugin_docs` |  | plugins_dir, whitelisted_plugin_dirs, out_file |
| `main_test` | ✓ |  |

**Constants:** `PRODUCTION_MODE`, `logger`, `CORE_DEPENDENCIES_AVAILABLE`, `WASMTIME_AVAILABLE`, `PYDANTIC_AVAILABLE`, `WASM_RUNNER_AVAILABLE`

---

# MODULE: RUN_SFE.PY

## run_sfe.py
**Lines:** 200

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `set_environment` |  |  |
| `run_arena` |  |  |
| `run_cli` |  |  |
| `run_api` |  |  |
| `run_full_platform` |  |  |
| `run_component` |  | component |
| `main` |  |  |

---

# MODULE: RUN_TESTS.PY

# MODULE: RUN_TESTS_TIMEOUT.PY

# MODULE: RUN_WORKING_TESTS.PY

## run_working_tests.py
**Lines:** 83

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `main` |  |  |

---

# MODULE: SECURITY_AUDIT.PY

## security_audit.py
**Lines:** 374

### `SecurityAuditor`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, base_path |
| `audit_file` |  | self, filepath |
| `audit_dependencies` |  | self |
| `audit_authentication` |  | self |
| `run_audit` |  | self |
| `generate_report` |  | self |

---

# MODULE: SELF_HEALING_IMPORT_FIXER

## self_healing_import_fixer/__init__.py
**Lines:** 345

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_setup_paths` |  |  |
| `get_shif_root` |  |  |
| `validate_shif_components` |  |  |
| `get_path_setup_status` |  |  |

**Constants:** `_logger`, `__version__`, `__author__`, `__all__`

---

## self_healing_import_fixer/analyzer/analyzer.py
**Lines:** 665

### `NonCriticalError` (Exception)

### `AnalyzerCriticalError` (RuntimeError)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, alert_level |

### `AnalyzerConfig` (BaseModel)

| Method | Async | Args |
|--------|-------|------|
| `validate_paths` |  | cls, config_data |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `load_config` |  | config_path |
| `validate_output_dir` |  | output_dir, project_root |
| `async_wrap` |  | func |
| `_handle_analyze` |  | project_root, app_config, output_dir |
| `_handle_check_policy` |  | project_root, app_config, output_dir |
| `_handle_security_scan` |  | project_root, app_config, output_dir |
| `_handle_suggest_patch` |  | project_root, app_config, output_dir, dry_run |
| `_handle_health_check` | ✓ | project_root, app_config |
| `_shutdown` |  |  |
| `main` |  | action, path, config, output_dir, verbose, ...+2 |

**Constants:** `SERVICE_NAME`, `VERSION`, `MIN_PYTHON`, `PRODUCTION_MODE`, `logger`, `AI_MANAGER`

---

## self_healing_import_fixer/analyzer/core_ai.py
**Lines:** 716

### `NonCriticalError` (Exception)

### `AIManager`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config, trace_id |
| `aclose` | ✓ | self |
| `_sanitize_prompt` |  | self, prompt |
| `_estimate_tokens` |  | self, text |
| `_enforce_token_quota` | ✓ | self, tokens_to_use, timeout |
| `_call_llm_api` | ✓ | self, prompt, trace_id |
| `get_refactoring_suggestion` | ✓ | self, context, trace_id |
| `get_cycle_breaking_suggestion` | ✓ | self, cycle_path, relevant_code_snippets, trace_id |
| `health_check` | ✓ | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_run_async` |  | coro |
| `_generate_trace_id` |  |  |
| `_default_trace_id_from_env` |  |  |
| `get_ai_manager_instance` | ✓ | config, trace_id, tenant_id |
| `get_ai_suggestions` | ✓ | codebase_context, config, trace_id, tenant_id |
| `get_ai_patch` | ✓ | problem_description, relevant_code, suggestions, config, trace_id, ...+1 |
| `ai_health_check` | ✓ | config, trace_id, tenant_id |
| `get_ai_suggestions_sync` |  | codebase_context, config, trace_id, tenant_id |
| `get_ai_patch_sync` |  | problem_description, relevant_code, suggestions, config, trace_id, ...+1 |
| `ai_health_check_sync` |  | config, trace_id, tenant_id |
| `main_test` | ✓ |  |

**Constants:** `PRODUCTION_MODE`, `logger`, `REDIS_CLIENT`, `_instance_lock`

---

## self_healing_import_fixer/analyzer/core_audit.py
**Lines:** 900

### `AnalyzerCriticalError` (RuntimeError)

### `RegulatoryAuditLogger`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config |
| `_determine_audit_directory` |  | self |
| `log_event` |  | self, event_type |
| `_initialize_audit_filesystem` |  | self |
| `_write_initial_log_entry` |  | self |
| `_initialize_integrity_file` |  | self |
| `log_startup` | ✓ | self |
| `_initialize_splunk` |  | self |
| `log_critical_event` | ✓ | self, event_type |
| `verify_integrity` | ✓ | self, full_scan |
| `_get_next_sequence_number` | ✓ | self |
| `_get_previous_hash` | ✓ | self |
| `_start_integrity_monitor` |  | self |
| `_write_integrity_violation` |  | self, violations |
| `_update_integrity_metadata` | ✓ | self, lines_verified |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_get_audit_hmac_key` |  |  |
| `get_audit_logger` |  |  |
| `audit_log` | ✓ | event_type |
| `verify_audit_integrity` | ✓ |  |
| `_cleanup_audit_system` |  |  |

**Constants:** `logger`, `PRODUCTION_MODE`, `TESTING_MODE`, `REGULATORY_MODE`, `AUDIT_VERIFY_ON_STARTUP`, `_audit_logger_instance`, `_initialization_lock`, `_background_tasks`, `audit_logger`, `__all__`

---

## self_healing_import_fixer/analyzer/core_graph.py
**Lines:** 803

### `AnalyzerCriticalError` (RuntimeError)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, alert_level |

### `NonCriticalError` (Exception)

### `ImportGraphAnalyzer`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, project_root, config |
| `_set_memory_limit` |  | self |
| `_find_python_files` |  | self |
| `_get_module_name` |  | self, file_path |
| `_parse_imports_async` | ✓ | self, file_path |
| `build_graph` |  | self |
| `detect_cycles` |  | self, graph |
| `detect_dead_nodes` |  | self, graph |
| `visualize_graph` |  | self, output_file, format |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_get_redis_client` |  |  |
| `_run_async` |  | coro |

**Constants:** `PRODUCTION_MODE`, `logger`, `REDIS_CLIENT`, `REDIS_INITIALIZED`

---

## self_healing_import_fixer/analyzer/core_policy.py
**Lines:** 805

### `AnalyzerCriticalError` (RuntimeError)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, alert_level |

### `PolicyRule` (BaseModel)

| Method | Async | Args |
|--------|-------|------|
| `validate_regex_patterns` |  | cls, v |

### `ArchitecturalPolicy` (BaseModel)

| Method | Async | Args |
|--------|-------|------|
| `ensure_policy_rules` |  | cls, v |

### `PolicyViolation` (BaseModel)

### `PolicyManager`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, policy_file_path, enable_hot_reload, reload_poll_interval |
| `shutdown` |  | self |
| `_load_policies_sync` |  | self |
| `_load_policies_async` | ✓ | self |
| `check_architectural_policies` |  | self, code_graph, module_paths, detected_cycles, dead_nodes |
| `_get_compiled_patterns` |  | self, rule_id |
| `_enforce_import_restriction` |  | self, rule, code_graph |
| `_enforce_dependency_limit` |  | self, rule, code_graph |
| `_enforce_cycle_prevention` |  | self, rule, detected_cycles |
| `_enforce_naming_convention` |  | self, rule, module_paths |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_run_async` |  | coro |
| `_get_policy_hmac_key` |  |  |
| `_get_redis_client` |  |  |
| `_validate_and_apply` |  | policy_data_bytes, expect_hmac |
| `_watch_loop` |  | policy_file_path, poll_seconds |
| `start_policy_watcher` |  | policy_file_path, poll_interval |
| `stop_policy_watcher` |  |  |

**Constants:** `PRODUCTION_MODE`, `VERSION`, `logger`, `POLICY_HMAC_KEY_ENV`, `REDIS_CLIENT`, `REDIS_INITIALIZED`, `_policy_lock`, `_watcher_stop_event`

---

## self_healing_import_fixer/analyzer/core_report.py
**Lines:** 790

### `AnalyzerCriticalError` (RuntimeError)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, alert_level |

### `ReportGenerator`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, output_dir, approved_report_dirs |
| `_is_approved_dir` |  | self, directory |
| `_format_text_report` |  | self, results |
| `_format_markdown_report` |  | self, results |
| `_format_html_report` |  | self, results |
| `_format_json_report` |  | self, results |
| `_format_pdf_report` |  | self, results |
| `generate_report` |  | self, results, report_name, report_format, user_id |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `scrub_secrets` |  | data |
| `_atomic_write_bytes` |  | path, data |
| `_atomic_write_text` |  | path, data |
| `get_reports_dir` |  |  |
| `get_kms_client` |  |  |
| `encrypt_report_content` |  | content |
| `decrypt_report_content` |  | encrypted_content |
| `get_reports_dir` |  |  |
| `start_dashboard` |  | host, port |
| `generate_report` |  | results, report_name, report_format, user_id |
| `start_dashboard_server` |  | host, port |

**Constants:** `PRODUCTION_MODE`, `logger`, `_kms_client`, `REPORT_KMS_KEY_ALIAS`, `__all__`

---

## self_healing_import_fixer/analyzer/core_secrets.py
**Lines:** 682

### `SecurityAnalysisError` (Exception)

### `SecretProvider` (Enum)
**Attributes:** ENV_VARS, AWS_SECRETS_MANAGER, AWS_SSM, HASHICORP_VAULT, AZURE_KEY_VAULT, GCP_SECRET_MANAGER, LOCAL_ENCRYPTED

### `SecretConfig`

### `SecretsManager`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config |
| `_initialize_providers` |  | self |
| `_setup_encryption` |  | self |
| `get_secret` |  | self, secret_name, version, required |
| `_get_from_provider` |  | self, secret_name, version |
| `set_secret` |  | self, secret_name, secret_value, description |
| `delete_secret` |  | self, secret_name, force |
| `_clear_secret_from_cache` |  | self, secret_name |
| `rotate_secret` |  | self, secret_name |
| `_get_local_encrypted_secret` |  | self, secret_name |
| `_set_local_encrypted_secret` |  | self, secret_name, secret_value |
| `_delete_local_encrypted_secret` |  | self, secret_name |
| `list_secrets` |  | self, prefix |
| `validate_secret_policy` |  | self, secret_value |
| `clear_cache` |  | self |
| `get_stats` |  | self |

### `_SecretsManager`

| Method | Async | Args |
|--------|-------|------|
| `get_secret` |  | self, key, required |

**Constants:** `logger`, `PRODUCTION_MODE`, `ENVIRONMENT`, `_default_config`, `SECRETS_MANAGER`, `__all__`, `SECRETS_MANAGER`

---

## self_healing_import_fixer/analyzer/core_security.py
**Lines:** 875

### `SecurityAnalysisError` (RuntimeError)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, tool, original_exception |

### `AnalyzerCriticalError` (RuntimeError)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, alert_level |

### `SecurityAnalyzer`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, project_root |
| `_check_tool_availability_on_init` |  | self |
| `_run_subprocess_safely` |  | self, command, description |
| `_run_tools_in_parallel` | ✓ | self |
| `_run_bandit` |  | self |
| `_run_pip_audit` |  | self |
| `_run_snyk` |  | self |
| `perform_security_scan` | ✓ | self |
| `security_health_check` |  | self, check_only |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_run_cmd` |  | argv, timeout |
| `_tool_path` |  | tool_name |
| `_deep_scrub` |  | data |
| `_norm_result` |  | tool, severity, message, path, line, ...+1 |
| `security_health_check` | ✓ | project_root, check_only |

**Constants:** `PRODUCTION_MODE`, `logger`, `REDIS_CLIENT`

---

## self_healing_import_fixer/analyzer/core_utils.py
**Lines:** 1149

### `AlertLevel` (Enum)
**Attributes:** DEBUG, INFO, WARNING, ERROR, CRITICAL, EMERGENCY

### `AlertChannel` (Enum)
**Attributes:** LOG, SLACK, PAGERDUTY, EMAIL, SNS, DATADOG, OPSGENIE, WEBHOOK

### `AlertConfig`

### `CircuitBreaker`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, name, failure_threshold, recovery_timeout, expected_exception |
| `call` |  | self, func |
| `_should_attempt_reset` |  | self |
| `_on_success` |  | self |
| `_on_failure` |  | self |

### `RateLimiter`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, max_calls, window |
| `is_allowed` |  | self |
| `wait_if_needed` |  | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_get_or_create_metric` |  | metric_class, name, description, labelnames |
| `_create_dummy_metric` |  |  |
| `get_circuit_breaker` |  | name |
| `get_rate_limiter` |  | name |
| `timing_context` |  | operation |
| `retry_with_backoff` |  | max_retries, initial_backoff, max_backoff, backoff_multiplier, exceptions |
| `cached` |  | ttl |
| `alert_operator_async` | ✓ | message, level, channels, metadata |
| `_send_slack_alert_async` | ✓ | message, level, metadata |
| `_send_pagerduty_alert_async` | ✓ | message, level, metadata |
| `_send_sns_alert_async` | ✓ | message, level, metadata |
| `_send_datadog_alert_async` | ✓ | message, level, metadata |
| `_send_opsgenie_alert_async` | ✓ | message, level, metadata |
| `_send_webhook_alert_async` | ✓ | url, payload |
| `scrub_secrets` |  | data |
| `validate_input` |  | data, schema |
| `generate_correlation_id` |  |  |
| `get_system_health` |  |  |
| `secure_hash` |  | data, salt |
| `verify_hash` |  | data, hashed |
| `sanitize_path` |  | path |
| `distributed_lock` |  | lock_name, timeout |
| `encode_for_logging` |  | obj |
| `_initialize` |  |  |
| `alert_operator` |  | msg, level |
| `scrub_secrets` |  | obj |

**Constants:** `logger`, `PRODUCTION_MODE`, `SERVICE_NAME`, `ENVIRONMENT`, `REGION`, `HOSTNAME`, `INSTANCE_ID`, `RATE_LIMIT_WINDOW`, `RATE_LIMIT_MAX_CALLS`, `MAX_RETRIES`, `INITIAL_BACKOFF`, `MAX_BACKOFF`, `BACKOFF_MULTIPLIER`, `CIRCUIT_BREAKER_FAILURE_THRESHOLD`, `CIRCUIT_BREAKER_RECOVERY_TIMEOUT`, ...+3 more

---

## self_healing_import_fixer/cli.py
**Lines:** 1366

### `JsonFormatter` (logging.Formatter)

| Method | Async | Args |
|--------|-------|------|
| `format` |  | self, record |

### `AnalyzerCriticalError` (Exception)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, alert_level |

### `NonCriticalError` (Exception)

### `PluginManager`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, plugin_dirs, approved_plugins |
| `_load_plugin_file_async` | ✓ | self, full_plugin_path, parser |
| `discover_and_load` | ✓ | self, parser |
| `run_hook` |  | self, hook_name |
| `list_plugins` |  | self, log_format |

### `CustomArgumentParser` (argparse.ArgumentParser)

| Method | Async | Args |
|--------|-------|------|
| `error` |  | self, message |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_bootstrap_import_paths` |  |  |
| `is_ci_environment` |  |  |
| `setup_logging` |  | verbose, log_format |
| `_validate_output_path` |  | path |
| `_get_plugin_signature_key` |  |  |
| `_verify_plugin_integrity` |  | file_path, expected_signature |
| `load_analyzer` |  |  |
| `load_fixer` |  |  |
| `load_requests` |  |  |
| `load_config` |  | config_path |
| `_validate_path_argument` |  | path, arg_name, is_dir, allow_symlink, allowlist |
| `create_parser` |  | plugin_manager |
| `main_async` | ✓ |  |
| `main` |  |  |

**Constants:** `REQUIRED_PYTHON`, `__version__`, `PRODUCTION_MODE`, `logger`, `cli_audit_logger`, `PLUGIN_SIGNATURE_KEY_ENV`

---

## self_healing_import_fixer/import_fixer/cache_layer.py
**Lines:** 665

### `_BaseCache`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, backend_name |
| `get` | ✓ | self, key |
| `setex` | ✓ | self, key, ttl, val |
| `incr` | ✓ | self, key |
| `_get_impl` | ✓ | self, key |
| `_setex_impl` | ✓ | self, key, ttl, val |
| `_incr_impl` | ✓ | self, key |

### `_InMemoryCache` (_BaseCache)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `_get_impl` | ✓ | self, key |
| `_setex_impl` | ✓ | self, key, ttl, val |
| `_incr_impl` | ✓ | self, key |

### `_FileCache` (_BaseCache)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, root, secrets_manager |
| `_p` |  | self, key |
| `_sign_payload` |  | self, payload |
| `_verify_payload` |  | self, payload |
| `_get_impl` | ✓ | self, key |
| `_setex_impl` | ✓ | self, key, ttl, val |
| `_incr_impl` | ✓ | self, key |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_safe_metric` |  | ctor |
| `_connect_redis` | ✓ |  |
| `_check_fallback_usage` | ✓ | backend_name, message |
| `get_cache` | ✓ | project_root |

**Constants:** `tracer`, `metrics`, `audit_logger`, `json_logger`, `cache_hits`, `cache_misses`, `cache_op_latency`, `redis_connection_failures`, `file_hmac_failures`, `_retry_on_redis`, `_last_fallback_alert_time`

---

## self_healing_import_fixer/import_fixer/compat_core.py
**Lines:** 1715

### `_NoOpSpan`
**Attributes:** __slots__

| Method | Async | Args |
|--------|-------|------|
| `__enter__` |  | self |
| `__exit__` |  | self |
| `set_attribute` |  | self |
| `add_event` |  | self |
| `record_exception` |  | self |

### `_NoOpTracer`
**Attributes:** __slots__

| Method | Async | Args |
|--------|-------|------|
| `start_as_current_span` |  | self, name |

### `JSONFormatter` (logging.Formatter)

| Method | Async | Args |
|--------|-------|------|
| `format` |  | self, record |

### `_NoopTimer`

| Method | Async | Args |
|--------|-------|------|
| `__enter__` |  | self |
| `__exit__` |  | self, exc_type, exc, tb |

### `_NoopMetric`

| Method | Async | Args |
|--------|-------|------|
| `labels` |  | self |
| `time` |  | self |
| `inc` |  | self |
| `set` |  | self |
| `observe` |  | self |

### `CoreModuleStatus`

### `S3RotatingFileHandler` (RotatingFileHandler)

| Method | Async | Args |
|--------|-------|------|
| `doRollover` |  | self |

### `_FallbackAuditLogger`

| Method | Async | Args |
|--------|-------|------|
| `_emit` |  | self, level, message |
| `log_event` |  | self, event_name |
| `info` |  | self, message |
| `warning` |  | self, message |
| `error` |  | self, message |
| `debug` |  | self, message |

### `_FallbackSecretsManager`

| Method | Async | Args |
|--------|-------|------|
| `get_secret` |  | self, key, required |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `get_tracer` |  | name |
| `_validate_env_var` |  | var_name, value, pattern |
| `_truthy` |  | v |
| `_get_metrics` |  |  |
| `_get_tracer` |  |  |
| `get_prometheus_metrics` |  |  |
| `get_telemetry_tracer` |  | _name |
| `get_audit_logger` |  |  |
| `get_json_logger` |  |  |
| `_get_redis_client` |  |  |
| `get_redis_connection_status` |  |  |
| `_should_log_warning` |  | key, interval_seconds, max_per_hour |
| `_ensure_s3_lifecycle_policy` |  |  |
| `_offload_audit_log_to_s3` |  | filename |
| `_sign_log_entry` |  | entry |
| `_check_fallback_usage` |  | component |
| `_fallback_alert_operator` |  | msg, level |
| `_get_alert_operator` |  |  |
| `_initialize_core_modules` |  |  |
| `get_core_health` |  |  |
| `verify_audit_log` |  | log_entry, secret |
| `get_core_dependencies` |  |  |
| `load_analyzer` |  | module_path |

**Constants:** `_self_healing_import_fixer_dir`, `_USE_REAL_TRACER`, `_real_get_tracer`, `ENVIRONMENT`, `PRODUCTION_MODE`, `ALLOW_FALLBACKS`, `AUDIT_LOG_ENABLED`, `AUDIT_SIGNING_ENABLED`, `_LOG_LEVEL_STR`, `LOG_LEVEL`, `logger`, `_observability_lock`, `_tracer`, `_json_logger`, `_init_lock`, ...+14 more

---

## self_healing_import_fixer/import_fixer/fixer_ai.py
**Lines:** 744

### `AnalyzerCriticalError` (RuntimeError)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, alert_level |

### `NonCriticalError` (Exception)

### `AIManager`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config |
| `aclose` | ✓ | self |
| `_estimate_tokens` |  | self, text |
| `_enforce_token_quota` | ✓ | self, tokens_to_use |
| `_call_llm_api` | ✓ | self, prompt |
| `get_refactoring_suggestion` | ✓ | self, context |
| `get_cycle_breaking_suggestion` | ✓ | self, cycle_path, relevant_code_snippets |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_get_cache_client` | ✓ |  |
| `_redis_alert_on_failure` |  | e |
| `_reset_redis_failure_counter` |  |  |
| `_sanitize_prompt` |  | prompt |
| `_sanitize_response` |  | response |
| `_get_ai_manager_instance` | ✓ | config |
| `_run_async_in_sync` |  | coro |
| `get_ai_suggestions` |  | codebase_context, config |
| `get_ai_patch` |  | problem_description, relevant_code, suggestions, config |
| `main_test` | ✓ |  |

**Constants:** `PRODUCTION_MODE`, `logger`, `_redis_failure_count`, `_redis_failure_alerted`, `REDIS_ALERT_THRESHOLD`, `_cache_client`, `_instance_lock`

---

## self_healing_import_fixer/import_fixer/fixer_ast.py
**Lines:** 1127

### `AnalyzerCriticalError` (RuntimeError)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, alert_level |

### `NonCriticalError` (Exception)

### `ImportResolver` (NodeTransformer)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, current_module_path, project_root, whitelisted_paths, root_package_names |
| `visit_ImportFrom` |  | self, node |

### `CycleHealer`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, file_path, cycle, graph, project_root, ...+1 |
| `_parse_ast_and_cache` | ✓ | self |
| `_get_module_name_from_path` |  | self, file_path |
| `find_problematic_import` | ✓ | self |
| `heal` | ✓ | self |
| `extract_interface` |  | self |
| `split_module` |  | self |

### `DynamicImportHealer`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, file_path, project_root, whitelisted_paths |
| `_parse_ast_and_cache` | ✓ | self |
| `heal` |  | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_shutdown_background_loop` |  |  |
| `_ensure_background_loop` |  |  |
| `get_ai_refactoring_suggestion` |  | context |
| `_run_async_in_sync` |  | coro |

**Constants:** `PRODUCTION_MODE`, `logger`, `_BG_LOOP`, `_BG_THREAD`, `_BG_LOOP_READY`

---

## self_healing_import_fixer/import_fixer/fixer_dep.py
**Lines:** 1547

### `HealerError` (RuntimeError)

### `ConfigError` (HealerError)

### `SecurityViolationError` (HealerError)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, path, whitelist |

### `FilesystemAccessError` (HealerError)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, path |

### `HealerNonCriticalError` (HealerError)

### `ImportCollector` (ast.NodeVisitor)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, file_imports, file_path |
| `visit_If` |  | self, node |
| `visit_Import` |  | self, node |
| `visit_ImportFrom` |  | self, node |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_alert_operator_or_log` |  | message, level |
| `_atomic_write_text` |  | path, data |
| `_get_stdlib_set` |  | python_version |
| `_get_cache_client` | ✓ |  |
| `_within_whitelist` |  | path, wl |
| `init_dependency_healing_module` |  | whitelisted_paths |
| `_get_parse_sem` |  | workers |
| `_skip_dirs` |  |  |
| `_get_py_files` |  | roots |
| `_get_module_map_sync` |  | roots |
| `_get_module_map` | ✓ | roots |
| `_discover_local_top_levels` |  | roots, file_to_mod |
| `_is_type_checking_test` |  | test |
| `_parse_file_imports_cached` |  | path, mtime |
| `_parse_file_imports` |  | file_path |
| `_get_all_imports_async` | ✓ | py_files, workers |
| `_normalize_dep_name` |  | name |
| `_import_to_distribution` |  | name |
| `_get_pyproject_deps` |  | pyproject_data |
| `_is_test_path` |  | p |
| `heal_dependencies` | ✓ | project_roots, dry_run, python_version, prune_unused, fail_on_diff, ...+3 |
| `main` |  |  |

**Constants:** `PRODUCTION_MODE`, `HEAL_METRICS`, `logger`, `_core_utils_loaded`, `AnalyzerCriticalError`, `_redis_client_instance`, `_file_cache_dir`, `_parse_concurrency_sem`, `_IMPORT_TO_DIST`

---

## self_healing_import_fixer/import_fixer/fixer_plugins.py
**Lines:** 814

### `AnalyzerCriticalError` (RuntimeError)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, alert_level |

### `NonCriticalError` (Exception)

### `PluginLoadError` (NonCriticalError)

### `PluginValidationError` (AnalyzerCriticalError)

### `Plugin` (ABC)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, name |
| `register` |  | self, manager |

### `PluginManager`
**Attributes:** _SAFE_MOD_RE, _HEX_DIGEST_RE

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config |
| `_validate_approved_plugins` |  | self, approved_plugins |
| `_begin_plugin_registration` |  | self |
| `_end_plugin_registration` |  | self |
| `register_hook` |  | self, hook_name, func |
| `register_healer` |  | self, healer |
| `register_validator` |  | self, validator |
| `register_diff_viewer` |  | self, viewer |
| `run_hook` |  | self, hook_name |
| `load_plugin` | ✓ | self, module_name |
| `unload_plugin` |  | self, module_name |
| `_load_plugin_file_async` | ✓ | self, module_name, full_plugin_path |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_get_plugin_cache` |  |  |
| `_callable_module_name` |  | fn |
| `_get_plugin_signature_key` |  | production_mode |
| `_verify_plugin_signature_async` | ✓ | file_path, expected_signature, production_mode |
| `make_plugin_manager` |  | config |
| `_reset_plugin_key_for_tests` |  |  |

**Constants:** `logger`, `PLUGIN_SIGNATURE_KEY_ENV`

---

## self_healing_import_fixer/import_fixer/fixer_validate.py
**Lines:** 1579

### `AnalyzerCriticalError` (RuntimeError)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, alert_level |

### `NonCriticalError` (Exception)

### `Issue`

### `StageResult`

### `ValidationReport`

### `CodeValidator`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, project_root, whitelisted_paths, parallelism, tests_dir, ...+1 |
| `_get_cache` | ✓ | self |
| `_is_under` |  | self, child |
| `_assert_whitelisted` |  | self, p |
| `_tool_config_files` |  | self, tool |
| `_get_tool_version` | ✓ | self, tool |
| `_cache_key` |  | self, stage, files, extra |
| `_subprocess_env` |  | self |
| `_run_command_async` | ✓ | self, command, cwd, description, timeout_s, ...+1 |
| `_atomic_write` |  | self, path, data |
| `compile_file` |  | self, file_path |
| `_run_tool_stage` | ✓ | self, stage_name, files, tool, command_func, ...+1 |
| `run_linting` | ✓ | self, file_paths |
| `_parse_ruff_output` |  | self, stdout, stderr, files |
| `_parse_flake8_output` |  | self, stdout, stderr, files |
| `run_type_checking` | ✓ | self, file_paths |
| `_parse_mypy_output` |  | self, stdout, stderr, files |
| `run_static_analysis` | ✓ | self, file_paths |
| `_parse_bandit_output` |  | self, stdout, stderr, files |
| `run_tests` | ✓ | self, test_paths, full_suite |
| `show_diff` |  | self, file_path, new_code, interactive |
| `validate_and_commit_file` | ✓ | self, file_path, new_code, original_code, run_tests, ...+2 |
| `validate_and_commit_batch` | ✓ | self, files_to_validate, original_contents, new_contents, run_tests, ...+1 |
| `rollback_change` |  | self, file_path, original_code, is_critical_failure |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `make_validator` |  | project_root, whitelisted_paths, parallelism, tests_dir, timeouts |
| `main` | ✓ |  |

**Constants:** `PRODUCTION_MODE`, `logger`

---

## self_healing_import_fixer/import_fixer/import_fixer_engine.py
**Lines:** 1007

### `Settings`

### `_DummyMetric`

| Method | Async | Args |
|--------|-------|------|
| `labels` |  | self |
| `inc` |  | self |
| `set` |  | self |
| `observe` |  | self |

### `_AssertableCall`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `__call__` |  | self |
| `assert_called_with` |  | self |

### `_HealthGauge`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |

### `Message`

### `MessageFilter`

### `SandboxPolicy`

### `AgentConfig` (dict)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |

### `SwarmConfig` (dict)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |

### `UnifiedSimulationModule`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config, db, message_bus |
| `initialize` | ✓ | self |
| `shutdown` | ✓ | self |
| `health_check` | ✓ | self, fail_on_error |
| `execute_simulation` | ✓ | self, sim_config |
| `perform_quantum_op` | ✓ | self, op_type, params |
| `explain_result` | ✓ | self, result |
| `run_in_secure_sandbox` | ✓ | self, code, inputs, policy |
| `handle_simulation_request` | ✓ | self, message |
| `register_message_handlers` | ✓ | self |

### `ImportFixerEngine`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config |
| `initialize` | ✓ | self |
| `shutdown` | ✓ | self |
| `fix_code` |  | self, code |
| `fix_code_async` | ✓ | self, code |
| `heal_project` | ✓ | self, project_root |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_get_or_create_metric` |  | _cls |
| `run_in_sandbox` |  | code, inputs, policy |
| `run_agent` | ✓ | _config |
| `run_simulation_swarm` | ✓ | _config |
| `run_parallel_simulations` | ✓ | _func, _tasks |
| `safe_serialize` |  | obj |
| `async_retry` |  | max_retries, backoff_factor |
| `create_simulation_module` | ✓ | config, db, message_bus |
| `run_simulation` | ✓ | config, db, message_bus |
| `run_import_healer` | ✓ | project_root, whitelisted_paths, max_workers, dry_run, auto_add_deps, ...+2 |
| `create_import_fixer_engine` |  | config |

**Constants:** `settings`, `logger`, `db_circuit_breaker`

---

# MODULE: SIMULATION

## simulation/agent_core.py
**Lines:** 752

### `LearningInsight`

### `MetaLearningBase` (ABC)

| Method | Async | Args |
|--------|-------|------|
| `learn` |  | self, experiences |
| `get_insights` |  | self |

### `MetaLearning` (MetaLearningBase)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `learn` |  | self, experiences |
| `get_insights` |  | self |
| `_analyze_patterns` |  | self, experiences |
| `_generate_insights` |  | self, patterns |

### `Policy`

### `PolicyEngineBase` (ABC)

| Method | Async | Args |
|--------|-------|------|
| `evaluate` |  | self, context |
| `add_policy` |  | self, name, policy |

### `PolicyEngine` (PolicyEngineBase)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `evaluate` |  | self, context |
| `add_policy` |  | self, name, policy |
| `get_stats` |  | self |

### `LLMBase` (ABC)

| Method | Async | Args |
|--------|-------|------|
| `generate` |  | self, prompt |
| `__call__` |  | self |

### `MockLLM` (LLMBase)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, provider |
| `generate` |  | self, prompt |

### `OpenAILLM` (LLMBase)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `generate` |  | self, prompt |

### `AnthropicLLM` (LLMBase)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `generate` |  | self, prompt |

### `GeminiLLM` (LLMBase)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `generate` |  | self, prompt |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `init_llm` |  | provider |
| `get_meta_learning_instance` |  |  |
| `get_policy_engine_instance` |  |  |

**Constants:** `logger`, `PRODUCTION_MODE`, `_meta_learning_instance`, `_policy_engine_instance`, `__all__`

---

## simulation/agentic.py
**Lines:** 1465

### `SecretsManager`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `get_secret` |  | self, key, default, required |

### `AuditLogger`
**Attributes:** DLQ_PATH, AUDIT_LOG_PATH, AUDIT_INTEGRITY_FILE

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `shutdown` | ✓ | self |
| `_await_tasks` | ✓ | self, tasks |
| `_sync_shutdown` |  | self |
| `_send_to_backend` | ✓ | self, event |
| `_send_with_retries` | ✓ | self, event |
| `write_to_dlq` | ✓ | self, event |
| `replay_dlq` | ✓ | self |
| `verify_audit_log_integrity` | ✓ | self, max_age_hours |
| `_periodic_audit_integrity_check` | ✓ | self, interval_seconds |
| `log_event` | ✓ | self, event_type |

### `ObjectStorageClient`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `save_object` | ✓ | self, key, data |
| `load_object` | ✓ | self, key |

### `MeshNotifier`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `_send_notification` | ✓ | self, url, headers, json |
| `notify` | ✓ | self, msg, channel, urgency |

### `EventBus`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `connect` | ✓ | self |
| `disconnect` | ✓ | self |
| `publish` | ✓ | self, topic, msg |
| `subscribe` | ✓ | self, topic, handler |

### `PolicyManager`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `has_permission` |  | self, agent, action, resource |

### `BaseWorkloadAdapter`

| Method | Async | Args |
|--------|-------|------|
| `evaluate` | ✓ | self, individual |

### `GAOptimizer`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, n_params |
| `evolve` | ✓ | self, workload_adapter |

### `OPERATOR_API`

| Method | Async | Args |
|--------|-------|------|
| `get_health_status` | ✓ |  |
| `inspect_dlq` | ✓ |  |
| `clear_dlq` | ✓ |  |

### `ImportFixerAutoTuningAdapter` (BaseWorkloadAdapter)

| Method | Async | Args |
|--------|-------|------|
| `evaluate` | ✓ | self, individual |

### `SelfEvolutionEngine`

| Method | Async | Args |
|--------|-------|------|
| `start` | ✓ | self, cycles |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `alert_operator` |  | message, level |
| `_is_test_environment` |  |  |
| `check_and_import` |  | package_name, module_name, critical |
| `async_span` |  | name |
| `log_exception_with_sentry` |  | exc |
| `_get_audit_hmac_key_agentic` |  |  |
| `get_object_storage` |  |  |
| `rbac_enforce` |  | agent, action, resource |
| `run_simulation_swarm` | ✓ | config |
| `demo_run` | ✓ |  |
| `main_async` | ✓ |  |

**Constants:** `agentic_logger`, `httpx`, `boto3`, `minio`, `google_cloud_storage`, `web3`, `gym`, `sentry_sdk`, `stable_baselines3`, `deap`, `ray`, `aioredis`, `nats`, `aiokafka`, `opentelemetry`, ...+7 more

---

## simulation/core.py
**Lines:** 1057

### `CorrelationIdFilter` (logging.Filter)

| Method | Async | Args |
|--------|-------|------|
| `filter` |  | self, record |

### `CircuitBreaker`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, failure_threshold, recovery_timeout, channel_name, ops_channel_notifier |
| `_open` |  | self |
| `_half_open` |  | self |
| `_close` |  | self |
| `_permanent_failure` |  | self |
| `attempt_operation` |  | self, func |

### `NotificationManager`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config |
| `_init_ops_channel_notifier` |  | self |
| `_send_slack_notification` |  | self, message |
| `_send_email_notification` |  | self, subject, body |
| `notify` |  | self, channel, message, subject |

### `RedactingFormatter` (logging.Formatter)

| Method | Async | Args |
|--------|-------|------|
| `format` |  | self, record |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `load_config` |  | config_path |
| `load_rbac_policy` |  | rbac_path |
| `_resolve_config_paths` |  |  |
| `get_user_roles` |  | username |
| `get_role_permissions` |  | role_name |
| `_matches` |  | pattern, value |
| `check_permission` |  | action, resource |
| `generate_correlation_id` |  |  |
| `set_correlation_id` |  | cid |
| `clear_correlation_id` |  |  |
| `correlated` |  | func |
| `execute_remotely` |  | job_config, backend |
| `run_job` |  | job_config |
| `watch_mode` |  | files_to_watch, callback |
| `main` | ✓ | args |
| `validate_file` |  | file_path |

**Constants:** `UNDER_PYTEST`, `BASE_DIR`, `CONFIG_DIR`, `LOG_DIR`, `RESULTS_DIR`, `LOG_FILE`, `logger`, `correlation_filter`, `CONFIG_FILE`, `RBAC_POLICY_FILE`, `CURRENT_USER`, `NOTIFICATION_MANAGER`, `dlt_logger_instance`, `REDACT_KEYWORDS`

---

## simulation/dashboard.py
**Lines:** 357

### `Config`

| Method | Async | Args |
|--------|-------|------|
| `ensure_dirs` |  | cls |

### `MeshPubSub`

### `CheckpointManager`

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `get_registered_dashboard_panels` |  |  |
| `get_registered_sidebar_components` |  |  |
| `get_registered_main_components` |  |  |
| `_clear_registries` |  |  |
| `register_dashboard_panel` |  | panel_id, title, render_func, live_data_supported |
| `register_sidebar_component` |  | component_func |
| `register_main_component` |  | component_func |
| `sanitize_plugin_name` |  | name |
| `is_version_compatible` |  | version_str, min_version, max_version |
| `validate_plugin_manifest` |  | plugin_path |
| `load_plugin_dashboard_panels_cached` |  |  |
| `display_onboarding_wizard` |  |  |
| `_run_health_checks_gui` | ✓ | config |
| `load_all_simulation_results` |  | results_dir |
| `t` |  | key |
| `render` |  |  |

**Constants:** `ONBOARDING_BACKENDS_AVAILABLE`, `logger`, `_translations`

---

## simulation/explain.py
**Lines:** 1770

### `ArbiterConfig`
**Attributes:** LLM_API_URL, LLM_API_KEY, LLM_API_TIMEOUT_SECONDS, LLM_ENHANCEMENT_ENABLED, LLM_RELATION_DISCOVERY_ENABLED, LLM_CONSISTENCY_CHECK_ENABLED, LLM_ENHANCE_MODEL, LLM_RELATED_MODEL, LLM_CONSISTENCY_MODEL, ML_MODEL_PATH, ML_LEARNING_RATE, ML_TRAINING_EPOCHS, MIN_SCALER_SAMPLES, QUANTUM_ENABLED, QUANTUM_DOMAINS

### `ExplanationResult`

### `ReasoningResult`

### `ReasoningHistory`

### `ReasonerConfig`

### `DummyMetric`
**Attributes:** DEFAULT_BUCKETS

| Method | Async | Args |
|--------|-------|------|
| `labels` |  | self |
| `observe` |  | self |
| `inc` |  | self |
| `set` |  | self |

### `ReasonerError` (Exception)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, code, original_exception |

### `HistoryManager`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, db_path, max_size |
| `init_db` | ✓ | self |
| `add_entry` | ✓ | self, entry |
| `get_entries` | ✓ | self, limit |
| `clear` | ✓ | self |
| `get_size` | ✓ | self |

### `ExplainableReasoner`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, settings, config |
| `async_init` | ✓ | self |
| `_initialize_models_async` | ✓ | self |
| `shutdown` | ✓ | self |
| `_get_next_pipeline` | ✓ | self |
| `_generate_text_sync` |  | self, pipeline_info, prompt, max_new_tokens, temperature |
| `_async_generate_text` | ✓ | self, prompt, max_length, temperature |
| `_create_explanation_prompt` |  | self, query, context |
| `_create_reasoning_prompt` |  | self, query, context |
| `explain` | ✓ | self, query, context |
| `reason` | ✓ | self, query, context |
| `get_history` | ✓ | self, limit |
| `clear_history` | ✓ | self |
| `_perform_health_check` | ✓ | self |
| `_periodic_health_check` | ✓ | self, interval_seconds |

### `ExplainableReasonerPlugin` (ExplainableReasoner)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, settings |
| `initialize` | ✓ | self |
| `explain_result` | ✓ | self, result |
| `execute` | ✓ | self, action |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `get_or_create_metric` |  | metric_type, name, documentation, labelnames, buckets |
| `get_executor_async` | ✓ | max_workers |
| `shutdown_executor_async` | ✓ |  |
| `_run_in_thread` |  | fn |
| `_sanitize_input` |  | text, max_length |
| `_sanitize_context` |  | context, max_size_bytes |
| `_rule_based_fallback` |  | query, context, mode |
| `_process_prompt` |  | prompt |
| `_format_output` |  | text |
| `_analyze_sentiment` | ✓ | text |
| `_placeholder_utility` |  | data |
| `_placeholder_async` | ✓ | data |
| `_validate_response` |  | text |

**Constants:** `TRANSFORMERS_AVAILABLE`, `_metrics_lock`, `METRICS`, `logger`, `_executor_lock`, `_executor_shutdown_event`

---

## simulation/parallel.py
**Lines:** 1381

### `RLTunerConfig`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, learning_rate, batch_size, episodes |

### `RayRLlibConcurrencyTuner`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config |
| `_init_policy` |  | self |
| `_start_training_loop` |  | self |
| `_collect_experiences` |  | self |
| `check_liveness` |  | self |
| `get_optimal_concurrency` |  | self, resources, num_tasks |
| `record_feedback` |  | self, throughput, failures |
| `stop` |  | self |

### `ProgressReporter`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, total_tasks, job_id |
| `task_completed` |  | self, success, job_latency |
| `finish` |  | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `get_or_create_metric` |  | metric_type, name, documentation, labelnames, buckets |
| `load_secret` |  | secret_name |
| `register_backend` |  | name, is_available |
| `alert_ops` |  | message, level |
| `get_available_resources` |  |  |
| `auto_tune_concurrency` |  | num_tasks |
| `auto_tune_concurrency_heuristic` |  | num_tasks |
| `execute_local_asyncio` | ✓ | simulation_function, configurations |
| `execute_kubernetes` | ✓ | simulation_function, configurations, config |
| `execute_aws_batch` | ✓ | simulation_function, configurations, config |
| `run_parallel_simulations` | ✓ | simulation_function, configurations, parallel_backend |

**Constants:** `parallel_logger`, `_metrics_lock`, `PARALLEL_CONFIG_FILE`, `GLOBAL_PARALLEL_CONFIG`, `DLT_LOGGER_INSTANCE`

---

## simulation/plugins/aws_batch_runner_plugin.py
**Lines:** 729

### `JobConfig` (BaseModel)

| Method | Async | Args |
|--------|-------|------|
| `validate_identifier_or_arn` |  | cls, v |
| `validate_bucket_name` |  | cls, v |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_async_retry` | ✓ | func |
| `_maybe_await` | ✓ | v |
| `_load_credentials_from_vault` | ✓ | vault_url |
| `_should_include_file` |  | path, root, include_patterns, exclude_patterns |
| `_create_filtered_archive` | ✓ | project_root, dest_tar_path, include_patterns, exclude_patterns |
| `_s3_extra_args_for_encryption` |  | sse_enabled, sse_kms_key_id |
| `_session_has_real_creds` |  | session |
| `plugin_health` | ✓ | vault_url |
| `_has_path_traversal` |  | p |
| `run_batch_job` | ✓ | job_config, project_root, output_dir |
| `register_plugin_entrypoints` |  | register_func |

**Constants:** `logger`, `CONFIG_FILE`, `JOB_SUBMISSIONS_TOTAL`, `JOB_DURATION_SECONDS`, `S3_OPERATION_LATENCY`, `RetryableNetworkErrors`

---

## simulation/plugins/cloud_logging_integrations.py
**Lines:** 680

### `CloudLoggingError` (Exception)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, cloud_type, original_exception, details |

### `CloudLoggingConfigurationError` (CloudLoggingError)

### `CloudLoggingConnectivityError` (CloudLoggingError)

### `CloudLoggingAuthError` (CloudLoggingError)

### `CloudLoggingResponseError` (CloudLoggingError)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, cloud_type, status_code, response_text, ...+2 |

### `CloudLoggingQueryError` (CloudLoggingError)

### `BaseCloudLogger`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config |
| `__aenter__` | ✓ | self |
| `__aexit__` | ✓ | self, exc_type, exc_val, exc_tb |
| `_to_thread` | ✓ | self, func |
| `log_event` |  | self, event |
| `flush` | ✓ | self |
| `health_check` | ✓ | self |
| `query_logs` | ✓ | self, query_string, time_range, limit |
| `_parse_relative_time_range_to_ms` |  | self, time_range_str |
| `_parse_relative_time_range_to_timedelta` |  | self, time_range_str |

### `CloudWatchLogger` (BaseCloudLogger)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config |
| `log_event` |  | self, event |
| `_get_aws_client` | ✓ | self |
| `flush` | ✓ | self |
| `_get_latest_sequence_token` | ✓ | self, client |
| `health_check` | ✓ | self |
| `query_logs` | ✓ | self, query_string, time_range, limit |

### `GCPLogger` (BaseCloudLogger)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config |
| `_get_gcp_client` | ✓ | self |
| `flush` | ✓ | self |
| `health_check` | ✓ | self |
| `query_logs` | ✓ | self, query_string, time_range, limit |

### `AzureMonitorLogger` (BaseCloudLogger)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config |
| `__aexit__` | ✓ | self, exc_type, exc_val, exc_tb |
| `_get_credential` | ✓ | self |
| `_get_ingestion_client` | ✓ | self |
| `_get_query_client` | ✓ | self |
| `flush` | ✓ | self |
| `health_check` | ✓ | self |
| `query_logs` | ✓ | self, query_string, time_range, limit |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_async_retry` | ✓ | func |
| `get_cloud_logger` |  | cloud_type, config |

**Constants:** `AWS_AVAILABLE`, `GCP_AVAILABLE`, `AZURE_MONITOR_QUERY_AVAILABLE`, `AZURE_IDENTITY_AVAILABLE`, `AZURE_MONITOR_INGESTION_AVAILABLE`, `logger`

---

## simulation/plugins/cross_repo_refactor_plugin.py
**Lines:** 1348

### `GitRepoManager`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, repo_url, temp_clone_path, credentials, refactor_id |
| `_get_repo` | ✓ | self |
| `clone_repo` | ✓ | self |
| `prepare_branch` | ✓ | self, base_branch, refactor_branch |
| `add_and_commit` | ✓ | self, file_paths, commit_message |
| `push_branch` | ✓ | self, branch_name, remote_name |
| `create_pull_request` | ✓ | self, title, body, head_branch, base_branch |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_env_bool` |  | name, default |
| `_audit_event` | ✓ | event_type, details |
| `_temp_environ` |  | env_updates |
| `_build_https_push_url` |  | repo_url, username, token |
| `_extract_owner_repo` |  | repo_url |
| `_mask_token_in_url` |  | url |
| `_is_safe_path` |  | base, path |
| `_path_has_symlink` |  | base, target_path |
| `_to_thread_timeout` | ✓ | func |
| `plugin_health` | ✓ |  |
| `_validate_refactor_plan` |  | refactor_plan |
| `_success_status` |  | status |
| `_process_repo` | ✓ | repo_plan, git_credentials, refactor_id, temp_dirs_to_clean, dry_run |
| `perform_cross_repo_refactor` | ✓ | refactor_plan, git_credentials, dry_run, cleanup_on_success, cleanup_on_failure |
| `register_plugin_entrypoints` |  | register_func |

**Constants:** `logger`, `PLUGIN_MANIFEST`, `GIT_CONFIG`

---

## simulation/plugins/custom_llm_provider_plugin.py
**Lines:** 1769

### `_NoopCounter`

| Method | Async | Args |
|--------|-------|------|
| `labels` |  | self |
| `inc` |  | self, value |

### `_NoopHistogram`

| Method | Async | Args |
|--------|-------|------|
| `labels` |  | self |
| `observe` |  | self, value |

### `_NoopGauge`

| Method | Async | Args |
|--------|-------|------|
| `labels` |  | self |
| `set` |  | self, value |

### `TokenBucketRateLimiter`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, rate_per_minute, burst |
| `acquire` | ✓ | self |

### `AsyncCircuitBreaker`
**Attributes:** CLOSED, OPEN, HALF_OPEN

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, failures_threshold, cooldown_seconds, name |
| `__aenter__` | ✓ | self |
| `__aexit__` | ✓ | self, exc_type, exc_val, exc_tb |
| `acquire` | ✓ | self |
| `record_success` | ✓ | self |
| `record_failure` | ✓ | self |

### `CircuitBreakerError` (Exception)

### `LLMConfig`

| Method | Async | Args |
|--------|-------|------|
| `validate` |  | self |

### `CustomLLMProvider`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config |
| `_generate_prompt` |  | self, messages |
| `_cache_key` |  | self, prompt, model_name, stop |
| `_get_cached_response` | ✓ | self, cache_key, model_name |
| `_set_cached_response` | ✓ | self, cache_key, model_name, response |
| `_make_request` | ✓ | self, messages |
| `_make_streaming_request` | ✓ | self, messages |
| `_get_fallback_provider` | ✓ | self |
| `_should_retry` |  | self, status |
| `_acall` | ✓ | self, messages |
| `_astream` | ✓ | self, messages |
| `_get_cached_vault_key` | ✓ | cls, key_name, ttl_seconds |
| `shutdown` |  | self |

### `CustomLLMChatModel` (BaseChatModel)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `_llm_type` |  | self |
| `_generate` | ✓ | self, messages, stop |
| `_get_client_session` | ✓ | self |
| `aclose_session` | ✓ | self |
| `_generate_prompt` |  | self, messages |
| `_build_messages_payload` |  | self, messages |
| `_cache_key` |  | self, prompt, model_name, stop |
| `_get_cached_response` | ✓ | self, cache_key, model_name |
| `_set_cached_response` | ✓ | self, cache_key, model_name, response |
| `shutdown` |  | self |
| `_is_transient_err` |  | self, e |
| `_acall_with_retry` | ✓ | self, model_to_use, request_id, cache_key, session, ...+2 |
| `_acall` | ✓ | self, messages, stop, run_manager, allow_fallback |
| `_astream` | ✓ | self, messages, stop, run_manager, allow_fallback |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_safe_counter` |  | name, doc, labelnames |
| `_safe_histogram` |  | name, doc, labelnames, buckets |
| `_safe_gauge` |  | name, doc, labelnames |
| `_record_rate_limit_wait` |  | model_name, wait_time |
| `_get_redis_client` | ✓ |  |
| `_close_redis_client` | ✓ |  |
| `enhanced_scrub_secrets` |  | data |
| `_is_production` |  |  |
| `_get_allowed_hosts` |  |  |
| `get_vault_key` | ✓ | key_name |
| `_get_api_key_with_cache` | ✓ |  |
| `_normalize_text_chunk` |  | data |
| `plugin_health` | ✓ | session, url |
| `generate_custom_llm_response` | ✓ | provider, messages |
| `_post_as_async_cm` | ✓ | obj |
| `_maybe_await` | ✓ | func |
| `_extract_delta_texts` |  | fragment |
| `register_plugin_entrypoints` | ✓ | register_func |

**Constants:** `__all__`, `logger`, `CONFIG_FILE`, `env_hosts`, `CUSTOM_LLM_API_CALLS_TOTAL`, `CUSTOM_LLM_API_LATENCY_SECONDS`, `CUSTOM_LLM_ERROR_TOTAL`, `CUSTOM_LLM_CACHE_HIT_TOTAL`, `CUSTOM_LLM_CACHE_MISS_TOTAL`, `CUSTOM_LLM_TOKEN_USAGE`, `CUSTOM_LLM_RESPONSE_LENGTH`, `CUSTOM_LLM_STREAMING_PERFORMANCE`, `CUSTOM_LLM_RETRY_EVENTS_TOTAL`, `CUSTOM_LLM_FALLBACK_USED_TOTAL`, `CUSTOM_LLM_RATE_LIMIT_WAIT_SECONDS`, ...+4 more

---

## simulation/plugins/dashboard.py
**Lines:** 2600

### `Config`
**Attributes:** PLUGINS_DIR, CONFIG_DIR, RESULTS_DIR

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_is_uvloop_policy` |  |  |
| `plugin_callback_handler` |  | func |
| `register_dashboard_panel` |  | panel_id, title, render_function, description, roles, ...+1 |
| `get_registered_dashboard_panels` |  |  |
| `get_registered_sidebar_components` |  |  |
| `get_registered_main_components` |  |  |
| `load_plugin_dashboard_panels_cached` |  |  |
| `is_version_compatible` |  | current_version, min_version, max_version |
| `authenticate_user` |  |  |
| `load_all_simulation_results` |  | results_dir |
| `get_live_data` |  | job_id |
| `listen_for_live_updates` |  | job_id, update_callback |
| `display_core_metrics` |  | selected_result |
| `_display_summary_and_details` |  | result |
| `display_plugin_gallery` |  | plugin_manager, user_role |
| `_generate_config_gui` |  | config_data, filename |
| `sanitize_plugin_name` |  | plugin_name |
| `_generate_plugin_manifest_gui` |  | plugin_type, plugin_name, plugins_dir |
| `run_async_streamlit` |  | coroutine |
| `_run_health_checks_gui` | ✓ | config, test_all_plugins |
| `display_onboarding_wizard` |  |  |
| `t` |  | key |
| `display_simulation_dashboard` |  |  |

**Constants:** `DASHBOARD_CORE_VERSION`, `logger`, `PLUGIN_MANAGER_AVAILABLE`, `temp_sys_path_added_plugin_manager`, `current_plugins_dir`, `ONBOARDING_BACKENDS_AVAILABLE`, `temp_sys_path_added_onboarding`, `current_dir_added`, `parent_dir_added`, `parent_dir_path`, `STREAMLIT_AVAILABLE`, `PLOTLY_AVAILABLE`, `REDIS_AVAILABLE`, `redis_client`, `DANGEROUS_NAMES`, ...+3 more

---

## simulation/plugins/dlt_clients/__init__.py
**Lines:** 83

### `DLTClientLoggerAdapter` (logging.LoggerAdapter)

| Method | Async | Args |
|--------|-------|------|
| `process` |  | self, msg, kwargs |
| `audit` |  | self, msg |

**Constants:** `AUDIT`, `_base_logger`, `_IN_TEST_MODE`

---

## simulation/plugins/dlt_clients/dlt_base.py
**Lines:** 1777

### `DLTClientLoggerAdapter` (logging.LoggerAdapter)

| Method | Async | Args |
|--------|-------|------|
| `process` |  | self, msg, kwargs |

### `SecretsManager`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `get_secret` |  | self, key, default, required |

### `BaseDLTConfig` (BaseModel)

### `BaseOffChainConfig` (BaseModel)

### `CircuitBreaker`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, client_type, failure_threshold, reset_timeout |
| `execute` | ✓ | self, operation |

### `DLTClientError` (Exception)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, client_type, original_exception, details, ...+1 |

### `DLTClientConfigurationError` (DLTClientError)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, client_type, original_exception, details, ...+1 |

### `DLTClientConnectivityError` (DLTClientError)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, client_type, original_exception, details, ...+1 |

### `DLTClientAuthError` (DLTClientError)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, client_type, original_exception, details, ...+1 |

### `DLTClientTransactionError` (DLTClientError)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, client_type, original_exception, details, ...+1 |

### `DLTClientQueryError` (DLTClientError)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, client_type, original_exception, details, ...+1 |

### `DLTClientResourceError` (DLTClientError)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, client_type, original_exception, details, ...+1 |

### `DLTClientTimeoutError` (DLTClientError)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, client_type, original_exception, details, ...+1 |

### `DLTClientValidationError` (DLTClientError)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, client_type, original_exception, details, ...+1 |

### `DLTClientCircuitBreakerError` (DLTClientError)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, client_type, original_exception, details, ...+1 |

### `AuditManager`
**Attributes:** _instance, _is_initialized, _lock

| Method | Async | Args |
|--------|-------|------|
| `__new__` |  | cls |
| `__init__` |  | self |
| `log_event` | ✓ | self, event_type |
| `shutdown` | ✓ | self |
| `_await_tasks` | ✓ | self, tasks |
| `_sync_shutdown` |  | self |
| `verify_integrity` | ✓ | self, max_age_hours |
| `_periodic_integrity_check` | ✓ | self, interval_seconds |

### `BaseOffChainClient` (ABC)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config |
| `_run_blocking_in_executor` | ✓ | self, func |
| `save_blob` | ✓ | self, key_prefix, payload_blob, correlation_id |
| `get_blob` | ✓ | self, off_chain_id, correlation_id |
| `close` | ✓ | self |

### `BaseDLTClient` (ABC)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config, off_chain_client |
| `__aenter__` | ✓ | self |
| `__aexit__` | ✓ | self, exc_type, exc_val, exc_tb |
| `_run_blocking_in_executor` | ✓ | self, func |
| `health_check` | ✓ | self, correlation_id |
| `write_checkpoint` | ✓ | self, checkpoint_name, hash, prev_hash, metadata, ...+2 |
| `read_checkpoint` | ✓ | self, name, version, correlation_id |
| `get_version_tx` | ✓ | self, name, version, correlation_id |
| `rollback_checkpoint` | ✓ | self, name, rollback_hash, correlation_id |
| `close` | ✓ | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `alert_operator` | ✓ | message, level |
| `_schedule_alert` |  | message, level |
| `async_retry` |  | catch_exceptions |
| `scrub_secrets` |  | data, patterns |
| `_get_dlt_audit_hmac_key` |  |  |
| `register_plugin_entrypoints` |  | register_func |
| `create_dlt_client` |  | client_type, dlt_config, off_chain_config |
| `initialize_dlt_backend_clients` | ✓ | config |
| `__getattr__` |  | name |

**Constants:** `_base_logger`, `PRODUCTION_MODE`, `TESTING_MODE`, `SECRETS_MANAGER`, `AIOHTTP_AVAILABLE`, `TENACITY_AVAILABLE`, `PYDANTIC_AVAILABLE`, `PROMETHEUS_AVAILABLE`, `FABRIC_AVAILABLE`, `WEB3_AVAILABLE`, `S3_AVAILABLE`, `GCS_AVAILABLE`, `AZURE_BLOB_AVAILABLE`, `IPFS_AVAILABLE`, `OTEL_AVAILABLE`, ...+9 more

---

## simulation/plugins/dlt_clients/dlt_corda_clients.py
**Lines:** 888

### `CordaConfig` (BaseModel)
**Attributes:** model_config

| Method | Async | Args |
|--------|-------|------|
| `validate_rpc_url_scheme` |  | cls, v |
| `_post_validate` |  | self |

### `CordaClientWrapper` (BaseDLTClient)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config, off_chain_client |
| `_format_log` |  | self, level, message, extra |
| `_get_session` | ✓ | self |
| `_rate_limit` | ✓ | self |
| `health_check` | ✓ | self, correlation_id |
| `write_checkpoint` | ✓ | self, checkpoint_name, hash, prev_hash, metadata, ...+2 |
| `read_checkpoint` | ✓ | self, name, version, correlation_id |
| `get_version_tx` | ✓ | self, name, version, correlation_id |
| `rollback_checkpoint` | ✓ | self, name, rollback_hash, correlation_id |
| `close` | ✓ | self |
| `__del__` |  | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `register_plugin_entrypoints` |  | register_func |
| `create_corda_client` |  | config, off_chain_client |

**Constants:** `PLUGIN_MANIFEST`

---

## simulation/plugins/dlt_clients/dlt_evm_clients.py
**Lines:** 1748

### `SecretsBackend`

| Method | Async | Args |
|--------|-------|------|
| `get_secret` | ✓ | self, secret_id |

### `EVMConfig` (BaseModel)

| Method | Async | Args |
|--------|-------|------|
| `validate_rpc_url_scheme` |  | cls, v, values |
| `validate_private_key_presence` |  | cls, v, values |
| `validate_secrets_provider_type` |  | cls, v, values |
| `validate_rpc_url_not_mock` |  | cls, v |

### `EthereumClientWrapper` (BaseDLTClient)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config, off_chain_client |
| `_format_log` |  | self, level, message, extra |
| `_safe_copy_dict` |  | self, obj, visited |
| `_rate_limit` | ✓ | self |
| `_exec_w3` | ✓ | self, func |
| `_exec_w3_prop` | ✓ | self, getter |
| `_ensure_initialized` | ✓ | self |
| `health_check` | ✓ | self, correlation_id |
| `_build_and_send_tx` | ✓ | self, tx_builder_method, gas_limit, gas_price_gwei, max_fee_per_gas_gwei, ...+2 |
| `write_checkpoint` | ✓ | self, checkpoint_name, hash, prev_hash, metadata, ...+2 |
| `read_checkpoint` | ✓ | self, name, version, correlation_id |
| `get_version_tx` | ✓ | self, name, version, correlation_id |
| `_rotate_credentials` | ✓ | self, new_private_key_hex, correlation_id |
| `rollback_checkpoint` | ✓ | self, name, rollback_hash, correlation_id |
| `close` | ✓ | self |
| `__del__` |  | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `register_plugin_entrypoints` |  | register_func |
| `create_ethereum_client` |  | config, off_chain_client |

**Constants:** `AWS_SECRETS_AVAILABLE`, `AZURE_KEYVAULT_AVAILABLE`, `GCP_SECRET_MANAGER_AVAILABLE`, `PLUGIN_MANIFEST`

---

## simulation/plugins/dlt_clients/dlt_fabric_clients.py
**Lines:** 1520

### `GrpcUrl` (AnyUrl)
**Attributes:** allowed_schemes

### `FabricPeerConfig` (BaseModel)

| Method | Async | Args |
|--------|-------|------|
| `validate_url` |  | cls, v |

### `FabricChannelConfig` (BaseModel)

### `FabricOrdererConfig` (BaseModel)

| Method | Async | Args |
|--------|-------|------|
| `validate_url` |  | cls, v |

### `FabricConfig` (BaseModel)
**Attributes:** model_config

| Method | Async | Args |
|--------|-------|------|
| `validate_mode_dependencies` |  | self |
| `validate_paths` |  | cls, v, values |

### `FabricClientWrapper` (BaseDLTClient)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config, off_chain_client |
| `_format_log` |  | self, level, message, extra |
| `_safe_copy_dict` |  | self, obj, visited |
| `_rate_limit` | ✓ | self |
| `_get_session` | ✓ | self |
| `_init_sdk_client` | ✓ | self |
| `_create_fabric_sdk_client` |  | self |
| `_create_user` |  | self, client |
| `health_check` | ✓ | self, correlation_id |
| `write_checkpoint` | ✓ | self, checkpoint_name, hash, prev_hash, metadata, ...+2 |
| `read_checkpoint` | ✓ | self, name, version, correlation_id |
| `get_version_tx` | ✓ | self, name, version, correlation_id |
| `rollback_checkpoint` | ✓ | self, name, rollback_hash, correlation_id |
| `close` | ✓ | self |
| `__del__` |  | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `register_plugin_entrypoints` |  | register_func |
| `create_fabric_client` |  | config, off_chain_client |

**Constants:** `FABRIC_NATIVE_AVAILABLE`, `PLUGIN_MANIFEST`

---

## simulation/plugins/dlt_clients/dlt_factory.py
**Lines:** 622

### `FactoryConfig` (BaseModel)

| Method | Async | Args |
|--------|-------|------|
| `validate_secrets_providers_list` |  | cls, v, values |
| `validate_config_version_value` |  | cls, v |
| `validate_off_chain_storage_type_in_prod` |  | cls, v |

### `DLTFactory`
**Attributes:** _logger, _manager

| Method | Async | Args |
|--------|-------|------|
| `_metrics_inc` |  | cls, name, labels |
| `_metrics_observe` |  | cls, name, labels, value |
| `_initialize_temp_files_manager` |  | cls, use_multiprocessing |
| `cleanup_temp_files` | ✓ | cls |
| `_schedule_audit` |  | cls, event_type |
| `get_config_schema` |  | cls |
| `get_dlt_client` | ✓ | cls, dlt_type, config, off_chain_client_instance, correlation_id |
| `_format_log` |  | cls, level, message, extra |
| `list_available_dlt_clients` |  | cls |
| `list_available_off_chain_clients` |  | cls |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_cleanup_at_exit` |  |  |

---

## simulation/plugins/dlt_clients/dlt_main.py
**Lines:** 480

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_run_async` |  | coro |
| `_load_json_file` |  | path |
| `cli` |  | verbose |
| `health_check_command` |  | dlt_type, config_file, correlation_id |
| `write_checkpoint_command` |  | dlt_type, config_file, checkpoint_name, hash_val, prev_hash, ...+3 |
| `read_checkpoint_command` |  | dlt_type, config_file, checkpoint_name, version, output_file, ...+1 |
| `rollback_checkpoint_command` |  | dlt_type, config_file, checkpoint_name, rollback_hash, correlation_id |
| `main` |  |  |

**Constants:** `CLI_LOGGER`

---

## simulation/plugins/dlt_clients/dlt_offchain_clients.py
**Lines:** 2452

### `SecretsBackend` (ABC)

| Method | Async | Args |
|--------|-------|------|
| `get_secret` | ✓ | self, secret_id |

### `AWSSecretsBackend` (SecretsBackend)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `get_secret` | ✓ | self, secret_id |

### `AzureKeyVaultBackend` (SecretsBackend)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, vault_url |
| `get_secret` | ✓ | self, secret_id |

### `GCPSecretManagerBackend` (SecretsBackend)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, project_id |
| `get_secret` | ✓ | self, secret_id |

### `S3Config` (BaseModel)

| Method | Async | Args |
|--------|-------|------|
| `validate_aws_credentials_source` |  | cls, v, values |
| `validate_secrets_providers_list` |  | cls, v, values |

### `GCSConfig` (BaseModel)

| Method | Async | Args |
|--------|-------|------|
| `validate_gcs_credentials_source` |  | cls, v, values |
| `validate_secrets_providers_list` |  | cls, v, values |

### `AzureBlobConfig` (BaseModel)

| Method | Async | Args |
|--------|-------|------|
| `validate_azure_connection_string_source` |  | cls, v, values |
| `validate_secrets_providers_list` |  | cls, v, values |

### `IPFSConfig` (BaseModel)

### `InMemoryConfig` (BaseModel)

### `S3OffChainClient` (BaseOffChainClient)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config |
| `initialize` | ✓ | self |
| `_get_secrets_backend` | ✓ | self, provider |
| `_format_log` |  | self, level, message, extra |
| `health_check` | ✓ | self, correlation_id |
| `save_blob` | ✓ | self, key_prefix, payload_blob, correlation_id |
| `get_blob` | ✓ | self, off_chain_id, correlation_id |
| `close` | ✓ | self |

### `GcsOffChainClient` (BaseOffChainClient)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config |
| `initialize` | ✓ | self |
| `_get_secrets_backend` | ✓ | self, provider |
| `_format_log` |  | self, level, message, extra |
| `health_check` | ✓ | self, correlation_id |
| `save_blob` | ✓ | self, key_prefix, payload_blob, correlation_id |
| `get_blob` | ✓ | self, off_chain_id, correlation_id |
| `close` | ✓ | self |

### `AzureBlobOffChainClient` (BaseOffChainClient)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config |
| `initialize` | ✓ | self |
| `_get_secrets_backend` | ✓ | self, provider |
| `_format_log` |  | self, level, message, extra |
| `health_check` | ✓ | self, correlation_id |
| `save_blob` | ✓ | self, key_prefix, payload_blob, correlation_id |
| `get_blob` | ✓ | self, off_chain_id, correlation_id |
| `close` | ✓ | self |

### `IPFSClient` (BaseOffChainClient)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config |
| `initialize` | ✓ | self |
| `_format_log` |  | self, level, message, extra |
| `health_check` | ✓ | self, correlation_id |
| `save_blob` | ✓ | self, key_prefix, payload_blob, correlation_id |
| `get_blob` | ✓ | self, off_chain_id, correlation_id |
| `close` | ✓ | self |

### `InMemoryOffChainClient` (BaseOffChainClient)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config |
| `_format_log` |  | self, level, message, extra |
| `health_check` | ✓ | self, correlation_id |
| `save_blob` | ✓ | self, key_prefix, payload_blob, correlation_id |
| `get_blob` | ✓ | self, off_chain_id, correlation_id |
| `close` | ✓ | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `cleanup_temp_files` |  |  |
| `create_temp_file` |  | content, ttl |

**Constants:** `S3_AVAILABLE`, `GCS_AVAILABLE`, `AZURE_BLOB_AVAILABLE`, `IPFS_AVAILABLE`

---

## simulation/plugins/dlt_clients/dlt_quorum_clients.py
**Lines:** 1526

### `SecretsBackend` (ABC)

| Method | Async | Args |
|--------|-------|------|
| `get_secret` | ✓ | self, secret_id |

### `AWSSecretsBackend` (SecretsBackend)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `get_secret` | ✓ | self, secret_id |

### `AzureKeyVaultBackend` (SecretsBackend)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, vault_url |
| `get_secret` | ✓ | self, secret_id |

### `GCPSecretManagerBackend` (SecretsBackend)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, project_id |
| `get_secret` | ✓ | self, secret_id |

### `QuorumConfig` (BaseModel)

| Method | Async | Args |
|--------|-------|------|
| `validate_rpc_url_scheme` |  | cls, v |
| `validate_contract_abi_source` |  | cls, v, values |
| `validate_private_key_source` |  | cls, v, values |
| `validate_privacy_settings_completeness` |  | cls, v, values |
| `validate_secrets_providers_list` |  | cls, v, values |

### `QuorumClientWrapper` (EthereumClientWrapper)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config, off_chain_client |
| `initialize` | ✓ | self |
| `_load_contract_abi` | ✓ | self, config |
| `_load_private_key_quorum` | ✓ | self, config |
| `_initial_startup_health_check` | ✓ | self |
| `_format_log` |  | self, level, message, extra |
| `_cleanup_temp_files_periodic` | ✓ | self |
| `_rotate_credentials` | ✓ | self, new_private_key, correlation_id |
| `health_check` | ✓ | self, correlation_id |
| `_send_transaction` | ✓ | self, tx_builder_method, gas_limit, gas_price, max_fee_per_gas, ...+2 |
| `close` | ✓ | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `cleanup_temp_files` |  |  |
| `temp_file` |  | content, ttl |

**Constants:** `AWS_SECRETS_AVAILABLE`, `AZURE_KEYVAULT_AVAILABLE`, `GCP_SECRET_MANAGER_AVAILABLE`

---

## simulation/plugins/dlt_clients/dlt_simple_clients.py
**Lines:** 1489

### `SimpleDLTConfig` (BaseModel)

| Method | Async | Args |
|--------|-------|------|
| `enforce_chain_state_path_in_prod` |  | cls, v |

### `SimpleDLTClient` (BaseDLTClient)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config, off_chain_client |
| `initialize` | ✓ | self |
| `_format_log` |  | self, level, message, extra |
| `_cleanup_temp_files_periodic` | ✓ | self |
| `_calculate_chain_checksum` |  | self, chain_data |
| `load_chain` | ✓ | self, path, correlation_id |
| `dump_chain` | ✓ | self, path, correlation_id |
| `_rotate_credentials` | ✓ | self, correlation_id |
| `health_check` | ✓ | self, correlation_id |
| `write_checkpoint` | ✓ | self, checkpoint_name, hash, prev_hash, metadata, ...+2 |
| `read_checkpoint` | ✓ | self, name, version, correlation_id |
| `get_version_tx` | ✓ | self, name, version, correlation_id |
| `rollback_checkpoint` | ✓ | self, name, rollback_hash, correlation_id |
| `close` | ✓ | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `create_simple_dlt_client` |  | config, off_chain_client |
| `register_plugin_entrypoints` |  | manager |

---

## simulation/plugins/dlt_network_config_manager.py
**Lines:** 1137

### `ConfigManagerLoggerAdapter` (logging.LoggerAdapter)

| Method | Async | Args |
|--------|-------|------|
| `process` |  | self, msg, kwargs |

### `DLTClientConfigurationError` (Exception)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, component |

### `S3OffChainConfig` (BaseModel)
**Attributes:** model_config

### `GcsOffChainConfig` (BaseModel)
**Attributes:** model_config

### `AzureBlobOffChainConfig` (BaseModel)
**Attributes:** model_config

### `IpfsOffChainConfig` (BaseModel)
**Attributes:** model_config

### `FabricDLTConfig` (BaseModel)
**Attributes:** model_config

| Method | Async | Args |
|--------|-------|------|
| `_validate_paths` |  | self |

### `EvmDLTConfig` (BaseModel)
**Attributes:** model_config

| Method | Async | Args |
|--------|-------|------|
| `validate_rpc_url_https` |  | cls, v |
| `validate_contract_address` |  | cls, v |
| `enforce_private_key_presence` |  | self |

### `CordaDLTConfig` (BaseModel)
**Attributes:** model_config

| Method | Async | Args |
|--------|-------|------|
| `validate_rpc_url_https` |  | cls, v |
| `enforce_password_presence` |  | self |

### `SimpleDLTConfig` (BaseModel)
**Attributes:** model_config

### `DLTNetworkConfig` (BaseModel)
**Attributes:** _CONFIG_SCHEMA_VERSION, model_config

| Method | Async | Args |
|--------|-------|------|
| `validate_dlt_type` |  | cls, v |
| `validate_off_chain_storage_type` |  | cls, v |
| `validate_off_chain_config` |  | self |
| `load_and_validate` |  | cls, config_data |
| `_migrate_schema` |  | cls, config_data |

### `DLTNetworkConfigManager`

| Method | Async | Args |
|--------|-------|------|
| `__new__` |  | cls, config_refresh_interval |
| `_collect_raw_configs_from_env` |  | self |
| `_load_all_configs_from_env` |  | self |
| `_add_config` |  | self, config_data, target_dict |
| `refresh_configs_if_changed` | ✓ | self |
| `start_background_refresh` |  | self, stop_event, jitter |
| `get_config` |  | self, name |
| `get_all_configs` |  | self |
| `get_default_config` |  | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_noop_metric` |  | metric_type |
| `get_or_create_metric` |  | metric_type, name, documentation, labelnames, buckets |
| `_get_metric_factory` |  |  |
| `_scrub_string_value` |  | s |
| `scrub_secrets` |  | data |
| `_load_secret_from_aws_secrets_manager` |  | secret_name |
| `_strip_sensitive_fields` |  | obj |
| `_normalize_config_for_hash` |  | raw_cfg |
| `_compute_raw_configs_hash` |  | name_to_cfg |
| `get_dlt_network_config_manager` |  | config_refresh_interval |

**Constants:** `_base_logger`, `logger`, `PRODUCTION_MODE`, `VALIDATE_PATHS`, `ENFORCE_PATHS_IN_PROD`, `AZURE_CONN_RE`, `GENERIC_SECRET_KV_RE`, `AWS_ACCESS_KEY_RE`, `AWS_SECRET_KEY_RE`, `CLIENT_SECRET_RE`, `GENERIC_SECRET_VALUE_RE`, `_SENSITIVE_KEYS`

---

## simulation/plugins/example_plugin.py
**Lines:** 719

### `StructuredLogFormatter` (logging.Formatter)

| Method | Async | Args |
|--------|-------|------|
| `format` |  | self, record |

### `ChaosExperimentParams` (BaseModel)

### `SecurityAuditParams` (BaseModel)

| Method | Async | Args |
|--------|-------|------|
| `prevent_path_traversal` |  | cls, v |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_scrub_secrets` |  | content |
| `_audit_cache_key` |  | path |
| `_noop_counter` |  |  |
| `_noop_histogram` |  |  |
| `_safe_counter` |  | name, doc, labelnames |
| `_safe_histogram` |  | name, doc, labelnames, buckets |
| `_get_env_float` |  | name, default |
| `_get_env_bool` |  | name, default |
| `plugin_health` |  |  |
| `run_custom_chaos_experiment` |  | target_id, intensity |
| `perform_custom_security_audit` |  | code_path |
| `example_plugin_dashboard_panel` |  | st_dash_obj, current_result |
| `register_my_dashboard_panels` |  | register_func |
| `check_compatibility` |  | core_version |

**Constants:** `_boot_logger`, `CONFIG_PATH`, `PLUGIN_MANIFEST`, `plugin_logger`, `SECRET_PATTERNS`, `_audit_cache`, `_audit_cache_lock`, `PROMETHEUS_AVAILABLE`, `CHAOS_EXPERIMENT_TOTAL`, `SECURITY_FINDINGS_TOTAL`, `SECURITY_AUDIT_DURATION`

---

## simulation/plugins/gcp_cloud_run_runner_plugin.py
**Lines:** 1004

### `EnvVar` (BaseModel)

### `JobConfig` (BaseModel)

| Method | Async | Args |
|--------|-------|------|
| `validate_image_url` |  | cls, v |
| `validate_project_id` |  | cls, v |
| `validate_location` |  | cls, v |
| `validate_bucket` |  | cls, v |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_noop_counter` |  |  |
| `_noop_hist` |  |  |
| `_safe_counter` |  | name, doc, labelnames |
| `_safe_hist` |  | name, doc, labelnames, buckets |
| `_bucket_valid` |  | name |
| `_load_credentials_from_vault` | ✓ |  |
| `_load_credentials_local` |  |  |
| `_get_credentials` | ✓ |  |
| `plugin_health` | ✓ |  |
| `_abspath` |  | path |
| `_tar_directory_to_temp` |  | project_root |
| `run_cloud_run_job` | ✓ | job_config, project_root, output_dir |
| `register_plugin_entrypoints` |  | register_func |

**Constants:** `logger`, `CONFIG_FILE`, `PROMETHEUS_AVAILABLE`, `JOB_SUBMISSIONS_TOTAL`, `JOB_DURATION_SECONDS`, `GCS_OPERATION_LATENCY`, `CREDENTIAL_SOURCE_TOTAL`, `_BUCKET_RE`, `_LOCATION_RE`

---

## simulation/plugins/gremlin_chaos_plugin.py
**Lines:** 1253

### `TargetSpec` (BaseModel)

### `AttackSpec` (BaseModel)

### `_AuthHeaders`

### `GremlinApiError` (Exception)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, status, body |

### `GremlinApiRetryableError` (GremlinApiError)

### `GremlinApiClient`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, base_url, team_id, api_key, timeout, ...+1 |
| `_ensure_session` | ✓ | self |
| `_headers` |  | self |
| `_build_attack_payload` |  | attack, target |
| `_retry_decorator` |  | self, op_name |
| `close` | ✓ | self |
| `_request` | ✓ | self, op, method, path, json_body |
| `create_attack` | ✓ | self, attack, target |
| `get_attack_status` | ✓ | self, attack_id |
| `halt_attack` | ✓ | self, attack_id |
| `quick_check` | ✓ | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_noop_counter` |  |  |
| `_noop_hist` |  |  |
| `_safe_counter` |  | name, doc, labelnames |
| `_safe_hist` |  | name, doc, labelnames, buckets |
| `_safe_gauge` |  | name, doc, labelnames |
| `_scrub` |  | s |
| `_audit_event` | ✓ | event_type, details |
| `_hostname_from_url` |  | u |
| `_get_client` | ✓ |  |
| `plugin_health` | ✓ |  |
| `run_chaos_experiment` | ✓ | experiment_type, target_type, target_value, duration_seconds, intensity, ...+2 |
| `register_plugin_entrypoints` |  | register_func |
| `shutdown_plugin` | ✓ |  |

**Constants:** `logger`, `PROMETHEUS_AVAILABLE`, `CHAOS_ATTACKS_TOTAL`, `CHAOS_ATTACK_ERRORS_TOTAL`, `CHAOS_ATTACK_DURATION_SECONDS`, `GREMLIN_API_LATENCY_SECONDS`, `GREMLIN_API_RETRIES_TOTAL`, `GREMLIN_HALTS_TOTAL`, `GREMLIN_INFLIGHT_ATTACKS`, `GREMLIN_CREATE_TOTAL`, `GREMLIN_STATUS_POLLS_TOTAL`, `GREMLIN_HTTP_RESPONSES_TOTAL`, `GREMLIN_SUBMISSION_SUCCESS_TOTAL`, `PLUGIN_MANIFEST`, `GREMLIN_BASE_URL`, ...+25 more

---

## simulation/plugins/java_test_runner_plugin.py
**Lines:** 963

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_shutil_which` |  | cmd |
| `_which` | ✓ | cmd |
| `_get_maven_version` | ✓ | mvn_path |
| `_hostname` |  |  |
| `_cap_text_tail` |  | s, max_bytes |
| `_is_path_under` |  | base, child |
| `_find_nearest_pom` |  | start_at, stop_at |
| `_detect_maven_exec` |  | maven_root |
| `_junit_patterns_for_target` |  | target_identifier |
| `_parse_junit_dir` |  | dir_path |
| `_parse_junit_xml` |  | xml_path |
| `_parse_jacoco_xml` |  | xml_path |
| `_create_minimal_pom_xml` |  | group_id, artifact_id, version, java_release |
| `_java_class_name_to_path` |  | class_name |
| `_copytree_compat` |  | src, dst |
| `_filter_maven_args` |  | args, strict |
| `plugin_health` | ✓ |  |
| `run_java_tests` | ✓ | test_file_path, target_identifier, project_root, temp_coverage_report_path_relative |
| `register_plugin_entrypoints` |  | register_func |

**Constants:** `logger`, `PLUGIN_MANIFEST`

---

## simulation/plugins/jest_runner_plugin.py
**Lines:** 1004

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_shutil_which` |  | cmd |
| `_which` | ✓ | cmd |
| `_is_path_under` |  | base, child |
| `_bound_search_for_package_json` |  | start_dir, stop_at |
| `_copytree_compat` |  | src, dst |
| `_detect_package_manager` | ✓ |  |
| `_get_package_version` | ✓ | cwd, package |
| `_read_package_json_field` |  | cwd, field |
| `_cap_text_tail` |  | s, max_bytes |
| `_install_packages` | ✓ | cwd, packages, npm_path, yarn_path |
| `plugin_health` | ✓ |  |
| `run_jest_tests` | ✓ | test_file_path, target_identifier, project_root, temp_coverage_report_path_relative |
| `register_plugin_entrypoints` |  | register_func |

**Constants:** `logger`, `PLUGIN_MANIFEST`

---

## simulation/plugins/main_sim_runner.py
**Lines:** 1596

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `register_entrypoint` |  |  |
| `_synthesize_kwargs_for_runner` |  | rf, module_name, language_or_framework, args |
| `_plugin_register_adapter` |  | module_name |
| `verify_plugin_signature` |  | code_path, sig_path |
| `discover_and_register_plugin_entrypoints` |  |  |
| `validate_deployment_or_exit` |  | remote |
| `send_notification` |  | event_type, message, dry_run |
| `check_rbac_permission` |  | actor, action, resource |
| `enforce_kernel_sandboxing` |  | profile_path, cgroup, apparmor_profile |
| `load_meta_learner` |  |  |
| `retry_op` |  | op, max_retries, backoff_base |
| `_tar_filter` |  | exclude_prefixes |
| `execute_remotely` |  | job_config, simulation_package_dir, notify_func |
| `_execute_remotely` |  | job_config, simulation_package_dir, notify_func |
| `run_plugin_in_sandbox` |  | plugin_name, args, sandbox |
| `aggregate_simulation_results` |  | core_result, plugin_results |
| `parse_plugin_kv_args` |  | args_list |
| `main` |  |  |

**Constants:** `tracer`, `current_dir`, `main_runner_logger`

---

## simulation/plugins/model_deployment_plugin.py
**Lines:** 1199

### `DeploymentError` (Exception)

### `ModelDeploymentStrategy` (ABC)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config, correlation_id |
| `deploy` | ✓ | self, model_path, model_version |
| `undeploy` | ✓ | self, deployment_id |
| `_validate_config` |  | self, required |

### `LocalAPIDeploymentStrategy` (ModelDeploymentStrategy)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config, correlation_id |
| `deploy` | ✓ | self, model_path, model_version |
| `undeploy` | ✓ | self, deployment_id |

### `CloudServiceDeploymentStrategy` (ModelDeploymentStrategy)
**Attributes:** _allowed_services

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config, correlation_id |
| `deploy` | ✓ | self, model_path, model_version |
| `undeploy` | ✓ | self, deployment_id |

### `ModelDeploymentPlugin`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, global_config_path |
| `_lock_key` |  | self, strategy_type, specific_config |
| `_get_lock` |  | self, key |
| `get_strategy` |  | self, strategy_type, specific_config, correlation_id |
| `deploy_model` | ✓ | self, strategy_type, model_path, model_version, specific_config, ...+1 |
| `undeploy_model` | ✓ | self, strategy_type, deployment_id, specific_config, correlation_id |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_redact` |  | obj |
| `_now_iso` |  |  |
| `_stable_deployment_id` |  | strategy, model_path, model_version, target_hint |
| `_deep_merge` |  | a, b |
| `_validate_semver` |  | version |
| `_validate_url` |  | u |
| `_sleep_with_timeout` | ✓ | seconds, timeout |
| `_async_retry` | ✓ | coro_factory, retries, backoff_base |
| `_start_span` |  | name |
| `_result` |  |  |
| `plugin_health` | ✓ |  |
| `_get_plugin_singleton` |  |  |
| `register_plugin_entrypoints` |  | register_func |
| `_install_safe_log_record_factory` |  |  |
| `_demo_main` | ✓ |  |

**Constants:** `logger`, `_SENSITIVE_KEYS`, `PLUGIN_MANIFEST`

---

## simulation/plugins/onboard.py
**Lines:** 1928

### `OnboardConfig` (BaseModel)

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `setup_logging` |  | verbose, quiet, json_format |
| `print_status` |  | msg, level |
| `_non_interactive` |  |  |
| `_get_user_input` |  | prompt, options, default, secret |
| `_generate_readme` | ✓ | target_dir, title, description |
| `_generate_config` | ✓ | config_data, filename |
| `_read_or_create_key` |  |  |
| `_generate_secure_config` |  | secrets, secrets_manager |
| `_load_secure_config` |  |  |
| `_send_telemetry` |  | data |
| `_load_secrets_from_vault` |  | vault_url, vault_token |
| `_validate_plugin_syntax` |  | plugin_file_path |
| `_auto_format_plugin` |  | plugin_file_path |
| `_generate_plugin_manifest` | ✓ | plugin_type, plugin_name, plugins_dir |
| `_run_health_checks` | ✓ | config |
| `_safe_mode_profile` | ✓ |  |
| `_reset_to_safe_mode` | ✓ |  |
| `_run_basic_onboarding_tests` |  |  |
| `_generate_ci_yaml` |  | ci_env |
| `_print_help` |  |  |
| `_check_existing_configs` |  |  |
| `_detect_venv` |  |  |
| `_detect_ci` |  |  |
| `_print_security_checklist` |  | config |
| `_show_examples` |  |  |
| `_auto_open_docs` |  |  |
| `_print_support_links` |  |  |
| `_cleanup_partial_onboard` |  |  |
| `_test_connection` | ✓ | backend, config |
| `onboard` | ✓ | args |

**Constants:** `_TESTING_MODE`, `script_dir`, `parent_dir`, `ONBOARD_CONFIG_FILE`, `LOG_FORMAT`, `logger`, `CONFIG_DIR`, `PLUGINS_DIR`, `RESULTS_DIR`, `CI_DIR`, `SECURE_CONFIG_PATH`, `SECURE_KEY_PATH`, `CORE_VERSION`

---

## simulation/plugins/pip_audit_plugin.py
**Lines:** 1121

### `TransientScanError` (Exception)

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_load_config` |  |  |
| `_scrub_secrets` |  | data |
| `_audit_event` | ✓ | event_type, details |
| `_which` | ✓ | cmd |
| `_get_pip_audit_version` | ✓ | base_cmd |
| `_pip_freeze_hash` | ✓ | python_executable |
| `plugin_health` | ✓ | python_executable |
| `_parse_severity_from_description` |  | description |
| `_validate_safe_args` |  | args |
| `_get_cached_result` | ✓ | cache_key |
| `_cache_scan_result` | ✓ | cache_key, result |
| `_build_cache_key` |  | payload |
| `_trim_and_optionally_scrub` |  | stdout_data, stderr_data |
| `scan_dependencies` | ✓ | target_path, scan_method, pip_audit_args, python_executable |
| `register_plugin_entrypoints` |  | register_func |

**Constants:** `logger`, `PIP_AUDIT_CONFIG`, `PLUGIN_MANIFEST`

---

## simulation/plugins/plugin_manager.py
**Lines:** 1636

### `PythonSubprocessProxy`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, plugin_path, manifest, health_timeout |
| `health` | ✓ | self |
| `close` | ✓ | self |

### `PluginManager`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, plugins_dir |
| `_ensure_background_loop` |  | self |
| `stop_background_loop` |  | self |
| `discover_plugins` | ✓ | self |
| `load_manifest` |  | self, plugin_path |
| `_health_timeout_for` |  | self, manifest |
| `_validate_manifest_schema` |  | self, plugin_path, manifest |
| `_import_python_module_inproc` |  | self, plugin_name, plugin_path, manifest |
| `_get_python_instance` |  | self, plugin_name, plugin_path, manifest |
| `load_plugin` |  | self, plugin_path, check_health |
| `reload_plugin` |  | self, name |
| `enable_plugin` |  | self, name |
| `disable_plugin` |  | self, name |
| `list_plugins` |  | self |
| `get_plugin` |  | self, name |
| `health` | ✓ | self, name |
| `load_all` | ✓ | self, check_health |
| `close_all_plugins` | ✓ | self |
| `_audit_log_event` |  | self, plugin_name, event_type, details |
| `_run_coro_blocking` |  | self, coro |
| `get_plugin_api_methods` |  | self, name |
| `summary` |  | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `retry_decorator` |  | exc_type |
| `_extract_manifest_from_python_file` |  | py_file |
| `_minimal_manifest_validate` |  | manifest |
| `main` | ✓ |  |

**Constants:** `_this_module`, `PYTHON_ISOLATION_MODE`, `HEALTH_TIMEOUT_SEC`, `plugin_logger`, `__all__`, `MIN_MANIFEST_VERSION`, `DANGEROUS_PYTHON_MODULES`

---

## simulation/plugins/runtime_tracer_plugin.py
**Lines:** 1120

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_str2bool` |  | val, default |
| `_truncate` |  | s, max_len |
| `_deny_weakening_docker_args` |  | extra_args |
| `_audit_event` | ✓ | event_type, details |
| `plugin_health` | ✓ |  |
| `_build_docker_run_command` |  | docker_cmd, temp_script_dir, target_code_path, test_script_path, trace_log_file, ...+2 |
| `_run_target_code_in_subprocess` | ✓ | target_code_path, trace_log_file, analysis_duration_seconds, test_script_path, execution_args, ...+1 |
| `analyze_runtime_behavior` | ✓ | target_code_path, analysis_duration_seconds, test_script_path, execution_args |
| `register_plugin_entrypoints` |  | register_func |

**Constants:** `PLUGIN_MANIFEST`, `logger`, `HAS_FCNTL`, `TRACE_ANALYSIS_ATTEMPTS`, `TRACE_ANALYSIS_SUCCESS`, `TRACE_ANALYSIS_ERRORS`, `TRACE_EXECUTION_LATENCY_SECONDS`, `DYNAMIC_CALLS_DETECTED`, `RUNTIME_EXCEPTIONS_CAPTURED`, `RUNTIME_TRACER_RUNS_TOTAL`, `TRACER_CONFIG`, `_SUBPROCESS_RUNNER_SCRIPT_TEMPLATE`

---

## simulation/plugins/scala_test_runner_plugin.py
**Lines:** 752

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_which` |  | cmd |
| `_get_sbt_version` | ✓ | sbt_path |
| `plugin_health` | ✓ |  |
| `_parse_junit_xml` |  | xml_path |
| `_parse_scoverage_xml` |  | xml_path |
| `_sanitize_identifier` |  | identifier |
| `_create_minimal_build_sbt` |  | scala_version, scalatest_version |
| `_create_plugins_sbt` |  | scoverage_version |
| `_copy_tree_limited` |  | src_dir, dest_dir, max_mb, max_files |
| `_find_scoverage_xml` |  | sbt_project_root, override_dir |
| `run_scala_tests` | ✓ | test_file_path, target_identifier, project_root, temp_coverage_report_path_relative |
| `register_plugin_entrypoints` |  | register_func |

**Constants:** `PLUGIN_MANIFEST`, `logger`, `SCALA_RUNNER_TIMEOUT_SEC`, `SCALA_TEMP_COPY_LIMIT_MB`, `SCALA_TEMP_COPY_MAX_FILES`, `SCALA_DEFAULT_SCALA_VERSION`, `SCALATEST_VERSION`, `SCOVERAGE_PLUGIN_VERSION`, `SBT_FLAGS`

---

## simulation/plugins/security_patch_generator_plugin.py
**Lines:** 1224

### `LLMClientWrapper`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, llm_backend |
| `generate_text` | ✓ | self, messages |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_load_config` |  |  |
| `_get_llm_client` | ✓ |  |
| `plugin_health` | ✓ |  |
| `_looks_like_unified_diff` |  | text |
| `_parse_llm_output` |  | generated_content, code_language |
| `_validate_patch_syntax` |  | proposed_patch, language |
| `_validate_vuln_details` |  | details |
| `_basic_scrub` |  | text |
| `_scrub_secrets` |  | data |
| `_get_cached_patch` | ✓ | cache_key |
| `_cache_patch_result` | ✓ | cache_key, result |
| `generate_security_patch` | ✓ | vulnerability_details, vulnerable_code_snippet, context, llm_params |
| `register_plugin_entrypoints` |  | register_func |

**Constants:** `PLUGIN_MANIFEST`, `logger`, `LLM_PATCH_GEN_CONFIG`, `PATCH_GENERATION_ATTEMPTS`, `PATCH_GENERATION_SUCCESS`, `PATCH_GENERATION_ERRORS`, `LLM_PATCH_GEN_LATENCY_SECONDS`, `PATCH_COMPLEXITY`, `LLM_TOKEN_USAGE`, `DIFF_PATTERN`, `CODE_BLOCK_PATTERN`, `EXPLANATION_DELIMITERS`, `_HIGH_CONF_SECRET_REGEXES`, `_SECRET_REGEXES`, `_retry_decorator`

---

## simulation/plugins/self_evolution_plugin.py
**Lines:** 1915

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_load_config` |  |  |
| `_get_meta_learning` | ✓ |  |
| `_get_policy_engine` | ✓ |  |
| `_get_core_llm` | ✓ |  |
| `_scrub_secrets` |  | data |
| `_audit_event` | ✓ | event_type, details |
| `_check_content_safety` | ✓ | content |
| `cache_performance_data` | ✓ | data |
| `get_cached_performance_data` | ✓ |  |
| `validate_agents` |  | agents |
| `validate_evolution_strategy` |  | strategy |
| `validate_strategy_params` |  | params |
| `plugin_health` | ✓ |  |
| `_strategy_prompt_optimization` | ✓ | meta_learning, core_llm, target_agents |
| `with_enhanced_error_handling` |  | func |
| `_setup_retry` |  | func |
| `initiate_evolution_cycle` | ✓ | target_agents, evolution_strategy, strategy_params |
| `register_plugin_entrypoints` |  | register_func |

**Constants:** `__version__`, `logger`, `T`, `AsyncFunc`, `EVOLUTION_CONFIG`, `PLUGIN_MANIFEST`, `SFE_CORE_AVAILABLE`, `SECRET_PATTERNS`

---

## simulation/plugins/siem_clients/__init__.py
**Lines:** 508

### `SIEMType` (Enum)
**Attributes:** SPLUNK, CLOUDWATCH, AZURE_SENTINEL, MOCK

### `SIEMClientBase` (ABC)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config |
| `connect` | ✓ | self |
| `send_event` | ✓ | self, event |
| `send_events_batch` | ✓ | self, events |
| `disconnect` | ✓ | self |
| `format_event` |  | self, event |

### `MockSIEMClient` (SIEMClientBase)

| Method | Async | Args |
|--------|-------|------|
| `connect` | ✓ | self |
| `send_event` | ✓ | self, event |
| `send_events_batch` | ✓ | self, events |
| `disconnect` | ✓ | self |

### `SplunkSIEMClient` (SIEMClientBase)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config |
| `connect` | ✓ | self |
| `send_event` | ✓ | self, event |
| `send_events_batch` | ✓ | self, events |
| `disconnect` | ✓ | self |

### `CloudWatchSIEMClient` (SIEMClientBase)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config |
| `connect` | ✓ | self |
| `send_event` | ✓ | self, event |
| `send_events_batch` | ✓ | self, events |
| `disconnect` | ✓ | self |

### `AzureSentinelSIEMClient` (SIEMClientBase)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config |
| `connect` | ✓ | self |
| `send_event` | ✓ | self, event |
| `send_events_batch` | ✓ | self, events |
| `disconnect` | ✓ | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `get_siem_client` |  | siem_type, config |

**Constants:** `logger`, `__all__`

---

## simulation/plugins/siem_clients/siem_aws_clients.py
**Lines:** 984

### `AwsCloudWatchConfig` (BaseModel)

| Method | Async | Args |
|--------|-------|------|
| `validate_non_empty` |  | cls, v |
| `validate_arn_compliance` |  | cls, v, field |
| `validate_auto_create_in_prod` |  | cls, v, field |
| `validate_aws_credentials_source` |  | cls, v, values |
| `validate_secrets_providers_list` |  | cls, v, values |

### `SecretsBackend` (ABC)

| Method | Async | Args |
|--------|-------|------|
| `get_secret` | ✓ | self, secret_id |

### `AWSSecretsBackend` (SecretsBackend)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, region_name |
| `get_secret` | ✓ | self, secret_id |

### `AwsKmsBackend` (SecretsBackend)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, region_name |
| `get_secret` | ✓ | self, secret_id |

### `AwsCloudWatchClient` (BaseSIEMClient)
**Attributes:** MAX_BATCH_SIZE, MAX_BATCH_BYTES

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config, metrics_hook, paranoid_mode |
| `_get_aws_client` | ✓ | self |
| `_perform_health_check_logic` | ✓ | self |
| `_ensure_log_group_and_stream` | ✓ | self |
| `_perform_send_log_logic` | ✓ | self, log_entry |
| `_perform_send_logs_batch_logic` | ✓ | self, log_entries |
| `_perform_query_logs_logic` | ✓ | self, query_string, time_range, limit |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `alert_operator_http` | ✓ | message, level |

**Constants:** `_TESTING_MODE`, `AWS_AVAILABLE`

---

## simulation/plugins/siem_clients/siem_azure_clients.py
**Lines:** 1386

### `SecretsBackend` (ABC)

| Method | Async | Args |
|--------|-------|------|
| `get_secret` | ✓ | self, secret_id |

### `AzureKeyVaultBackend` (SecretsBackend)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, vault_url |
| `get_secret` | ✓ | self, secret_id |
| `close` | ✓ | self |

### `AzureSentinelConfig` (BaseModel)

| Method | Async | Args |
|--------|-------|------|
| `validate_workspace_id_not_dummy` |  | cls, v |
| `validate_log_type_format` |  | cls, v |
| `validate_shared_key_source` |  | cls, v, values |
| `validate_secrets_providers_list` |  | cls, v, values |

### `AzureEventGridConfig` (BaseModel)

| Method | Async | Args |
|--------|-------|------|
| `validate_endpoint_not_dummy` |  | cls, v |
| `validate_key_source` |  | cls, v, values |
| `validate_secrets_providers_list` |  | cls, v, values |
| `validate_topic_name_format` |  | cls, v |

### `AzureServiceBusConfig` (BaseModel)

| Method | Async | Args |
|--------|-------|------|
| `validate_connection_string_source` |  | cls, v, values |
| `validate_queue_or_topic` |  | cls, v, values |
| `validate_namespace_fqdn_not_dummy` |  | cls, v |
| `validate_name_format` |  | cls, v, field |

### `AzureSentinelClient` (AiohttpClientMixin, BaseSIEMClient)
**Attributes:** MAX_BATCH_BYTES

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config, metrics_hook, paranoid_mode |
| `_ensure_shared_key_loaded` | ✓ | self |
| `_get_logs_query_client` | ✓ | self |
| `_perform_health_check_logic` | ✓ | self |
| `_perform_send_log_logic` | ✓ | self, log_entry |
| `_perform_send_logs_batch_logic` | ✓ | self, log_entries |
| `_perform_query_logs_logic` | ✓ | self, query_string, time_range, limit |
| `close` | ✓ | self |

### `AzureEventGridClient` (BaseSIEMClient)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config, metrics_hook, paranoid_mode |
| `_ensure_key_loaded` | ✓ | self |
| `_perform_health_check_logic` | ✓ | self |
| `_perform_send_log_logic` | ✓ | self, log_entry |
| `_perform_send_logs_batch_logic` | ✓ | self, log_entries |
| `query_logs` | ✓ | self, query_string, time_range, limit, correlation_id |

### `AzureServiceBusClient` (BaseSIEMClient)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config, metrics_hook, paranoid_mode |
| `_ensure_connection_string_loaded` | ✓ | self |
| `_get_servicebus_client` | ✓ | self |
| `_perform_health_check_logic` | ✓ | self |
| `_perform_send_log_logic` | ✓ | self, log_entry |
| `_perform_send_logs_batch_logic` | ✓ | self, log_entries |
| `query_logs` | ✓ | self, query_string, time_range, limit, correlation_id |
| `close` | ✓ | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_alert_operator_http` | ✓ | message, level |
| `_notify_ops` |  | message, level |

**Constants:** `AZURE_EVENTGRID_AVAILABLE`, `AZURE_SERVICEBUS_AVAILABLE`, `AZURE_MONITOR_QUERY_AVAILABLE`

---

## simulation/plugins/siem_clients/siem_base.py
**Lines:** 439

### `SIEMClientLoggerAdapter` (logging.LoggerAdapter)

| Method | Async | Args |
|--------|-------|------|
| `process` |  | self, msg, kwargs |

### `JsonFormatter` (logging.Formatter)

| Method | Async | Args |
|--------|-------|------|
| `format` |  | self, record |

### `AuditLogger`

| Method | Async | Args |
|--------|-------|------|
| `log_event` | ✓ | self, event_type |

### `SIEMClientError` (Exception)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, client_type, details |

### `SIEMClientConfigurationError` (SIEMClientError)

### `SIEMClientAuthError` (SIEMClientError)

### `SIEMClientConnectivityError` (SIEMClientError)

### `SIEMClientQueryError` (SIEMClientError)

### `SIEMClientPublishError` (SIEMClientError)

### `SIEMClientResponseError` (SIEMClientError)

### `SecretsManager`

| Method | Async | Args |
|--------|-------|------|
| `get_secret` |  | self, key, required, default |

### `GenericLogEvent` (BaseModel)
**Attributes:** model_config

### `AiohttpClientMixin`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `_get_session` | ✓ | self |
| `_close_session` | ✓ | self |

### `BaseSIEMClient` (ABC)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config, client_type |
| `_run_blocking_in_executor` |  | self, func |
| `_parse_relative_time_range_to_ms` |  | self, time_range |
| `_parse_relative_time_range_to_timedelta` |  | self, time_range |
| `health_check` | ✓ | self |
| `send_log` | ✓ | self, log_data |
| `query_logs` | ✓ | self, query, time_range, limit |
| `close` | ✓ | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `alert_operator` |  | message, level |
| `scrub_secrets` |  | data, patterns |
| `_check_and_import_critical` |  | package_name, module_name |

**Constants:** `PRODUCTION_MODE`, `_base_logger`, `AUDIT`, `_global_secret_patterns`, `_compiled_global_secret_patterns`, `_env_secret_patterns_on_init`, `_compiled_env_secret_patterns`, `aiohttp`, `tenacity`, `pydantic`, `opentelemetry`, `PYDANTIC_AVAILABLE`, `SECRETS_MANAGER`

---

## simulation/plugins/siem_clients/siem_factory.py
**Lines:** 223

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `get_siem_client` |  | siem_type, config, metrics_hook |
| `list_available_siem_clients` |  |  |

**Constants:** `SplunkClient`, `ElasticClient`, `DatadogClient`, `AwsCloudWatchClient`, `GcpLoggingClient`, `AzureSentinelClient`, `AzureEventGridClient`, `AzureServiceBusClient`

---

## simulation/plugins/siem_clients/siem_gcp_clients.py
**Lines:** 643

### `SecretsBackend` (ABC)

| Method | Async | Args |
|--------|-------|------|
| `get_secret` | ✓ | self, secret_id |

### `GCPSecretManagerBackend` (SecretsBackend)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, project_id |
| `get_secret` | ✓ | self, secret_id |
| `close` | ✓ | self |

### `GcpLoggingConfig` (BaseModel)

| Method | Async | Args |
|--------|-------|------|
| `validate_project_id_format` |  | cls, v |
| `validate_log_name_format` |  | cls, v |
| `validate_credentials_source` |  | cls, v, values |
| `validate_secrets_providers_list` |  | cls, v, values |

### `GcpLoggingClient` (BaseSIEMClient)
**Attributes:** MAX_BATCH_SIZE

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config, metrics_hook, paranoid_mode |
| `_ensure_credentials_loaded` | ✓ | self |
| `_encoded_log_id` |  | self |
| `_get_gcp_client` | ✓ | self |
| `_perform_health_check_logic` | ✓ | self |
| `_perform_send_log_logic` | ✓ | self, log_entry |
| `_perform_send_logs_batch_logic` | ✓ | self, log_entries |
| `_perform_query_logs_logic` | ✓ | self, query_string, time_range, limit |
| `close` | ✓ | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_is_transient_gcp_error` |  | exc |

**Constants:** `GCP_AVAILABLE`, `_TRANSIENT_GCP_ERROR_NAMES`

---

## simulation/plugins/siem_clients/siem_generic_clients.py
**Lines:** 1310

### `SplunkConfig` (BaseModel)

| Method | Async | Args |
|--------|-------|------|
| `validate_url_security_and_dummy` |  | cls, v |
| `validate_token_not_dummy` |  | cls, v |

### `ElasticConfig` (BaseModel)

| Method | Async | Args |
|--------|-------|------|
| `validate_url_security_and_dummy` |  | cls, v |
| `validate_credentials_not_dummy` |  | cls, v, field |
| `validate_auth_method_presence` |  | cls, v, values |

### `DatadogConfig` (BaseModel)

| Method | Async | Args |
|--------|-------|------|
| `validate_urls_security_and_dummy` |  | cls, v |
| `validate_keys_not_dummy` |  | cls, v, field |

### `SplunkClient` (AiohttpClientMixin, BaseSIEMClient)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config, metrics_hook, paranoid_mode |
| `_ensure_config_loaded` | ✓ | self |
| `_hec_health_url` |  | self |
| `_perform_health_check_logic` | ✓ | self |
| `_perform_send_log_logic` | ✓ | self, log_entry |
| `_perform_send_logs_batch_logic` | ✓ | self, log_entries |
| `_perform_query_logs_logic` | ✓ | self, query_string, time_range, limit |

### `ElasticClient` (AiohttpClientMixin, BaseSIEMClient)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config, metrics_hook, paranoid_mode |
| `_ensure_config_loaded` | ✓ | self |
| `_perform_health_check_logic` | ✓ | self |
| `_perform_send_log_logic` | ✓ | self, log_entry |
| `_perform_send_logs_batch_logic` | ✓ | self, log_entries |
| `_perform_query_logs_logic` | ✓ | self, query_string, time_range, limit |

### `DatadogClient` (AiohttpClientMixin, BaseSIEMClient)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config, metrics_hook, paranoid_mode |
| `_ensure_config_loaded` | ✓ | self |
| `_perform_health_check_logic` | ✓ | self |
| `_perform_send_log_logic` | ✓ | self, log_entry |
| `_perform_send_logs_batch_logic` | ✓ | self, log_entries |
| `_perform_query_logs_logic` | ✓ | self, query_string, time_range, limit |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_is_transient_status` |  | status_code |
| `_maybe_await` | ✓ | value |
| `_get_secret` | ✓ | key, default |

---

## simulation/plugins/siem_clients/siem_main.py
**Lines:** 656

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_maybe_await` | ✓ | value |
| `_get_secret` | ✓ | key, default |
| `_scrub_obj` |  | obj |
| `_scrub_and_dump` |  | obj |
| `run_tests` | ✓ |  |
| `main` | ✓ |  |

**Constants:** `_SCRUB_KEY_REGEX`

---

## simulation/plugins/siem_integration_plugin.py
**Lines:** 2549

### `PolicyEnforcer`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, policy_config |
| `_evaluate_condition` |  | self, event, condition, rule_idx, cond_idx |
| `_get_field_value` |  | self, data, field_path |
| `_set_field_value` |  | self, data, field_path, value |
| `enforce` |  | self, event |

### `RedisQueuePersistence`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, redis_url |
| `enqueue` | ✓ | self, item |
| `dequeue` | ✓ | self |
| `size` | ✓ | self |
| `flush` | ✓ | self |

### `SelfHealingManager`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config |
| `record_failure` | ✓ | self, siem_type, error |
| `record_success` |  | self, siem_type |
| `is_backend_disabled` |  | self, siem_type |
| `enqueue_for_retry` | ✓ | self, event_data |
| `dequeue_for_retry` | ✓ | self |
| `get_queue_size` | ✓ | self |
| `process_retry_queue` | ✓ | self, siem_plugin_instance |

### `GenericSIEMIntegrationPlugin`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config |
| `_get_config_for_client` |  | self, siem_type |
| `_init_active_backends` |  | self |
| `_run_retry_loop` | ✓ | self |
| `start_retry_task` |  | self |
| `stop_retry_task` |  | self |
| `plugin_health` | ✓ | self |
| `_check_backend_health` | ✓ | self, siem_type, backend_instance |
| `send_siem_event` | ✓ | self, event_type, event_details, siem_type_override, metadata |
| `validate_event_type` |  | event_type |
| `query_siem_logs` | ✓ | self, query_string, siem_type_override, time_range, limit, ...+1 |
| `close_all_backends` | ✓ | self |
| `_close_backend_safely` | ✓ | self, siem_type, backend_instance |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_filter_none` |  | d |
| `_load_raw_config_from_env` |  |  |
| `_scrub_secrets` |  | data |
| `_audit_event` | ✓ | kind, name, details |
| `get_plugin_manifest` |  |  |
| `_monitor_config_changes` | ✓ |  |
| `register_plugin_entrypoints` |  | register_func |
| `shutdown_plugin` |  |  |

**Constants:** `logger`, `PLUGIN_MANIFEST`

---

## simulation/plugins/viz.py
**Lines:** 1013

### `DashboardInterface`

| Method | Async | Args |
|--------|-------|------|
| `markdown` |  | self, text |
| `warning` |  | self, text |
| `plotly_chart` |  | self, fig |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_load_config` |  |  |
| `register_pre_plot_hook` |  | fn |
| `register_post_plot_hook` |  | fn |
| `validate_panel_id` |  | panel_id |
| `register_viz_panel` |  | panel_id, title, description, plot_type, roles, ...+4 |
| `unregister_viz_panel` |  | panel_id |
| `get_registered_viz_panels` |  |  |
| `list_panels_metadata` |  |  |
| `get_panels_for_role` |  | role |
| `pre_plot_hook` |  | plot_type, data |
| `post_plot_hook` |  | plot_type, plot_object, metadata |
| `_scrub_metadata` |  | metadata |
| `_scrub_secrets` |  | data |
| `plot_flakiness_trend` |  | runs, test_file_name |
| `plot_coverage_history` |  | coverage_data, label |
| `plot_metric_trend` |  | metrics, metric_name, unit, filename_suffix |
| `render_example_plugin_custom_metric_trend` |  | st_dash_obj, current_result |
| `_get_cached_plot_data` | ✓ | cache_key |
| `_cache_plot_data` | ✓ | cache_key, data |
| `batch_export_panels` | ✓ | panel_ids, format |
| `check_dashboard_interface` |  | dash_obj |

**Constants:** `logger`, `CONFIG`, `RESULTS_DIR`

---

## simulation/plugins/web_ui_dashboard_plugin_template.py
**Lines:** 733

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_load_config` |  |  |
| `get_dashboard_state` | ✓ |  |
| `update_dashboard_state` | ✓ | update_data |
| `_scrub_secrets` |  | data |
| `register_ui_component` |  | name, component_func |
| `get_example_metric_panel` |  | data |
| `get_example_chart` |  | data |
| `get_example_table` |  | data |

**Constants:** `logger`, `CONFIG`, `PLUGIN_MANIFEST`

---

## simulation/plugins/workflow_viz.py
**Lines:** 694

### `DashboardAPI`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, backend |
| `markdown` |  | self, text |
| `warning` |  | self, text |
| `plotly_chart` |  | self, fig, use_container_width |
| `expander` |  | self, label, expanded |
| `subheader` |  | self, text |
| `info` |  | self, text |
| `pyplot` |  | self, fig |
| `caption` |  | self, text |
| `button` |  | self, label, key |
| `download_button` |  | self, label, data, file_name, mime |

### `WorkflowPhase` (Enum)
**Attributes:** LOAD_SPEC, PLAN_TESTS, GENERATE_CODE, SECURITY_TESTS, PERFORMANCE_SCRIPT, JUDGE_REVIEW, REFINE, EXECUTE_TESTS, OUTPUT_RESULTS

| Method | Async | Args |
|--------|-------|------|
| `label` |  | self |
| `color` |  | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `detect_dashboard_backend` |  |  |
| `validate_custom_phases` |  | phases |
| `validate_export_path` |  | path |
| `_scrub_secrets` |  | data |
| `get_graphviz_layout` |  | edges |
| `render_workflow_viz` |  | result, prefer_plotly, summary_callback, dashboard_api, custom_phases, ...+1 |
| `_default_summary_and_details` |  | result, dashboard_api |

**Constants:** `viz_logger`, `CONFIG_FILE`, `DEFAULT_CONFIG`, `CONFIG`, `RESULTS_DIR`

---

## simulation/quantum.py
**Lines:** 2001

### `CredentialProvider` (ABC)

| Method | Async | Args |
|--------|-------|------|
| `get_credentials` | ✓ | self, key |

### `AWSCredentialProvider` (CredentialProvider)

| Method | Async | Args |
|--------|-------|------|
| `get_credentials` | ✓ | self, key |

### `VaultCredentialProvider` (CredentialProvider)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `_get_client` | ✓ | self |
| `get_credentials` | ✓ | self, key |
| `invalidate_cache` | ✓ | self, key |
| `close` | ✓ | self |

### `EnvCredentialProvider` (CredentialProvider)

| Method | Async | Args |
|--------|-------|------|
| `get_credentials` | ✓ | self, key |

### `FileCredentialProvider` (CredentialProvider)

| Method | Async | Args |
|--------|-------|------|
| `get_credentials` | ✓ | self, key |

### `CredentialManager`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `_register_providers` |  | self |
| `get_credentials` | ✓ | self, key |

### `BackendClientPool`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `initialize` | ✓ | self |
| `_periodic_cleanup` | ✓ | self |
| `_cleanup_unused_clients` | ✓ | self |
| `_close_client` | ✓ | self, key, client |
| `get_client` | ✓ | self, backend |
| `_create_client` | ✓ | self, backend |
| `_create_qiskit_client` |  | self |
| `_create_dwave_client` |  | self, token |
| `_hash_kwargs` |  | self, kwargs |
| `close` | ✓ | self |

### `AuditLogger`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `initialize` | ✓ | self |
| `log_event` | ✓ | self, kind, name, details, correlation_id |

### `QuantumRLAgent` (nn.Module if TORCH_RL_AVAILABLE else object)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, state_dim, action_dim |
| `_build_quantum_circuit` |  | self |
| `_fallback_to_classical` |  | self |
| `_execute_quantum_circuit` |  | self, x |
| `forward` |  | self, x |

### `QuantumPluginAPI`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `initialize` | ✓ | self |
| `shutdown` | ✓ | self |
| `get_available_backends` |  | self |
| `perform_quantum_operation` | ✓ | self, operation_type, params |
| `check_all_backends_health` | ✓ | self |
| `execute_benchmark` | ✓ | self, backend, comprehensive |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `send_pagerduty_alert` | ✓ | message, level |
| `send_slack_alert` | ✓ | message, level |
| `alert_operator` | ✓ | message, level |
| `load_quantum_credentials` | ✓ | backend |
| `check_any_backend_available` |  |  |
| `check_backend_health` | ✓ | backend |
| `_validate_secure_path_logic` |  | v |
| `optimize_quantum_circuit` |  | circuit, optimization_level |
| `_execute_qiskit_job` |  | qc, backend_sim, shots |
| `_execute_dwave_sampler` |  | bqm, sampler, num_reads |
| `run_quantum_mutation` | ✓ | code_file, backend, config |
| `quantum_forecast_failure` | ✓ | trend_data |
| `initialize_quantum_module` | ✓ |  |
| `shutdown_quantum_module` | ✓ |  |

**Constants:** `dual_annealing`, `DLTLogger`, `PROMETHEUS_AVAILABLE`, `PYDANTIC_AVAILABLE`, `AIOFILES_AVAILABLE`, `quantum_logger`, `_metrics_registry`, `_metrics_lock`, `QISKIT_AVAILABLE`, `DWAVE_AVAILABLE`, `SCIPY_AVAILABLE`, `DEAP_AVAILABLE`, `TORCH_RL_AVAILABLE`, `credential_manager`, `backend_client_pool`, ...+1 more

---

## simulation/registry.py
**Lines:** 718

### `AuditLogger`

| Method | Async | Args |
|--------|-------|------|
| `emit_audit_event` | ✓ | self, kind, details, severity |

### `FallbackAuditLogger` (AuditLogger)

| Method | Async | Args |
|--------|-------|------|
| `emit_audit_event` | ✓ | self, kind, details, severity |

### `DltAuditLogger` (AuditLogger)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, emit_audit_event |
| `emit_audit_event` | ✓ | self, kind, details, severity |

### `MetricsProvider`

| Method | Async | Args |
|--------|-------|------|
| `observe_load_duration` |  | self, duration |
| `increment_error` |  | self, operation |
| `set_success_rate` |  | self, plugin, value |

### `DummyMetricsProvider` (MetricsProvider)

### `PrometheusMetricsProvider` (MetricsProvider)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `observe_load_duration` |  | self, duration |
| `increment_error` |  | self, operation |
| `set_success_rate` |  | self, plugin, value |

### `OutputRefiner`

| Method | Async | Args |
|--------|-------|------|
| `refine` | ✓ | self, plugin_name, output |

### `NoOpOutputRefiner` (OutputRefiner)

| Method | Async | Args |
|--------|-------|------|
| `refine` | ✓ | self, plugin_name, output |

### `LangChainOutputRefiner` (OutputRefiner)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, chat |
| `refine` | ✓ | self, plugin_name, output |

### `RunnerPlugin` (Protocol)

| Method | Async | Args |
|--------|-------|------|
| `run` | ✓ | self, target, params |

### `DltClientPlugin` (Protocol)

| Method | Async | Args |
|--------|-------|------|
| `some_dlt_method` |  | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `get_audit_logger` |  |  |
| `get_metrics_provider` |  |  |
| `get_output_refiner` |  |  |
| `generate_file_hash` |  | file_path |
| `sanitize_path` |  | path, root_dir |
| `redact_sensitive` |  | text |
| `validate_manifest` |  | manifest, module_name |
| `check_plugin_dependencies` | ✓ | manifest, module_name |
| `get_registry` |  |  |
| `_is_allowed` | ✓ | module_name, module_path |
| `register_plugin` | ✓ | module, module_name, file_path |
| `discover_and_register_all` | ✓ |  |
| `refine_plugin_output` | ✓ | plugin_name, output |
| `run_plugin` | ✓ | plugin_name, target, params |

**Constants:** `SIMULATION_PACKAGE`, `IS_DEMO_MODE`, `PLUGIN_TIMEOUT_SECONDS`, `REGISTRY_PLUGINS_PATH`, `logger`, `audit_logger`, `metrics_provider`, `output_refiner`

---

## simulation/runners.py
**Lines:** 1003

### `MyBetterRunner`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `run` |  | self, config |

### `MyCustomRunner`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `run` |  | self, config |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `alert_operator` |  | message, level |
| `register_runner` |  | name, dependencies |
| `check_runner_dependencies` |  | runner_name |
| `time_metric` |  | metric, labels |
| `_load_docker_credentials` |  |  |
| `_execute_subprocess_safely` |  | command, env, timeout, user, resource_limits, ...+4 |
| `_perform_integrity_check` |  | file_path, expected_mime_type |
| `run_python_script` |  | config, job_id, user_id |
| `run_container` |  | config, job_id, user_id |
| `run_agent` |  | agent_config |

**Constants:** `PRODUCTION_MODE`, `ASYNC_EXECUTION_TIMEOUT_SECONDS`

---

## simulation/sandbox.py
**Lines:** 2215

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `alert_operator` |  | message, level |
| `_get_audit_hmac_key` |  |  |
| `log_audit` |  | event |
| `_validate_container_config` |  | image, command |
| `_sign_event` |  | event |
| `_verify_log_file` |  | log_path |
| `verify_audit_log_integrity` |  |  |
| `_periodic_audit_log_verification` | ✓ | interval_seconds |
| `register_sandbox_backend` |  | name |
| `load_plugins_for_sandbox` |  |  |
| `dlt_operation` |  | op_name |
| `check_external_services_async` | ✓ |  |
| `_periodic_external_service_check` | ✓ | interval_seconds |
| `drop_capabilities` |  |  |
| `_validate_and_bind_workdir` |  | workdir, allow_write |
| `_apply_kernel_sandboxing_preexec` |  | policy, resource_limits |
| `_monitor_sandbox_health` | ✓ | sandbox_id |
| `cleanup_sandbox` | ✓ | sandbox_id |
| `_create_network_policy_for_pod` |  | pod_name, namespace |
| `_validate_pod_manifest_internal` |  | manifest |
| `run_in_docker_sandbox` | ✓ | command, workdir, image, policy, resource_limits |
| `run_in_podman_sandbox` | ✓ | command, workdir, image, policy, resource_limits |
| `deploy_to_kubernetes` | ✓ | command, workdir, policy, kubernetes_pod_manifest |
| `run_in_local_process_sandbox` | ✓ | command, workdir, policy |
| `burst_to_cloud` | ✓ | job_config, cloud_provider |
| `run_chaos_experiment` | ✓ | app, experiment_type |
| `run_in_sandbox` | ✓ | backend, command, workdir, image, policy, ...+2 |
| `_cleanup_all_active_sandboxes` | ✓ |  |
| `_run_async_cleanup_on_exit` |  |  |
| `_start_background_tasks` | ✓ |  |
| `_initial_external_service_check` | ✓ |  |
| `check_rate_limit` | ✓ |  |
| `_periodic_security_scan` | ✓ | interval_seconds |
| `load_secrets_from_secure_store` |  |  |
| `initialize_sandbox_system` | ✓ |  |
| `get_available_backends` |  |  |
| `shutdown_sandbox_system` | ✓ |  |

**Constants:** `sandbox_logger`, `AUDIT_LOG_FILE`, `AUDIT_LOG_INTEGRITY_FILE`, `AUDIT_HMAC_KEY_ENV`, `PRODUCTION_MODE`, `_AUDIT_LOG_FILE_ENV`, `_AUDIT_LOG_INTEGRITY_ENV`, `BASE_DIR`, `PROFILES_DIR`, `DOCKER_AVAILABLE`, `PODMAN_AVAILABLE`, `AWS_AVAILABLE`, `GCP_AVAILABLE`, `AZURE_AVAILABLE`, `SECCOMP_AVAILABLE`, ...+15 more

---

## simulation/simulation_module.py
**Lines:** 1292

### `Settings`

### `_DummyMetric`

| Method | Async | Args |
|--------|-------|------|
| `labels` |  | self |
| `inc` |  | self |
| `set` |  | self |
| `observe` |  | self |

### `_AssertableCall`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `__call__` |  | self |
| `assert_called_with` |  | self |

### `_HealthGauge`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |

### `Message`

### `MessageFilter`

### `Database`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, db_path |
| `_init_real_db` |  | self, db_path |
| `health_check` | ✓ | self |
| `save_audit_record` | ✓ | self, record |
| `close` | ✓ | self |

### `ShardedMessageBus`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, enable_dlq |
| `_matches_pattern` |  | self, topic, pattern |
| `_apply_filter` |  | self, message, msg_filter |
| `health_check` | ✓ | self |
| `publish` | ✓ | self, topic, message |
| `subscribe` | ✓ | self, topic, handler |
| `close` | ✓ | self |

### `RetryPolicy`

### `CircuitBreaker`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |

### `ReasonerError` (Exception)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message |

### `ExplanationInput`

### `ExplainableReasonerPlugin`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `async_init` | ✓ | self |
| `execute` | ✓ | self, action |
| `explain_result` | ✓ | self, _inp |
| `shutdown` | ✓ | self |

### `QuantumPluginAPI`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `perform_quantum_operation` | ✓ | self |
| `get_available_backends` |  | self |

### `SandboxPolicy`

### `AgentConfig` (dict)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |

### `SwarmConfig` (dict)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |

### `UnifiedSimulationModule`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config, db, message_bus |
| `initialize` | ✓ | self |
| `shutdown` | ✓ | self |
| `health_check` | ✓ | self, fail_on_error |
| `execute_simulation` | ✓ | self, sim_config |
| `perform_quantum_op` | ✓ | self, op_type, params |
| `explain_result` | ✓ | self, result |
| `run_in_secure_sandbox` | ✓ | self, code, inputs, policy |
| `handle_simulation_request` | ✓ | self, message |
| `register_message_handlers` | ✓ | self |

### `SimulationEngine`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `_ensure_initialized` | ✓ | self |
| `get_tools` |  |  |
| `is_available` |  |  |
| `run_simulation` | ✓ | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_get_or_create_metric` |  | _cls |
| `_with_labels` |  | metric |
| `_create_metrics_dict` |  | use_real_metrics |
| `run_in_sandbox` |  | code, inputs, policy |
| `run_agent` | ✓ | _config |
| `run_simulation_swarm` | ✓ | _config |
| `run_parallel_simulations` | ✓ | _func, _tasks |
| `safe_serialize` |  | obj |
| `async_retry` |  | max_retries, backoff_factor |
| `create_simulation_module` | ✓ | config, db, message_bus |
| `run_simulation` | ✓ | config, db, message_bus |

**Constants:** `PRODUCTION_MODE`, `PYTEST_COLLECTING`, `settings`, `logger`, `db_circuit_breaker`

---

## simulation/utils.py
**Lines:** 907

### `ScalableProvenanceLogger`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, storage_path |
| `log` |  | self, event |

### `PluginAPI`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, plugin_name |
| `get_logger` |  | self |
| `create_temp_dir` |  | self, prefix |
| `cleanup_temp_dirs` |  | self |
| `temp_dir_context` |  | self, prefix |
| `get_core_version` |  | self |
| `check_core_compatibility` |  | self, min_version, max_version |
| `report_result` |  | self, result_type, data |
| `handle_error` |  | self, message, exception, fatal |
| `warn_sandbox_limitations` |  | self, manifest |

### `SecretStr` (str)

| Method | Async | Args |
|--------|-------|------|
| `__new__` |  | cls, value |
| `__repr__` |  | self |
| `get_secret_value` |  | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_load_config` |  |  |
| `_canonical_metric_name` |  | name |
| `_existing_metric_from_registry` |  | name, registry |
| `get_or_create_metric` |  | metric_type, name, documentation, labelnames, buckets, ...+1 |
| `sanitize_path` |  | path, base_dir |
| `validate_safe_path` |  | path, base_dir |
| `redact_sensitive` |  | text |
| `_scrub_secrets` |  | data |
| `_fire_and_forget` |  | coro |
| `_hash_key` |  | path |
| `_compute_hash_cached` |  | path, algo, chunk_size, mtime_ns, size |
| `_compute_hash` |  | path, algo, chunk_size |
| `hash_file` |  | path, algos, chunk_size |
| `find_files_by_pattern` |  | root, pattern |
| `load_artifact` |  | path, max_bytes |
| `print_file_diff` |  | a, b, diff_format, custom_formatter |
| `save_sim_result` | ✓ | data, out_path |
| `summarize_result` |  | result, detail_level |

**Constants:** `logger`, `config`, `config_file`, `CONFIG`, `CORE_SIM_RUNNER_VERSION`, `BASE_DIR`, `RESULTS_DIR`, `SAFE_BASES`, `_metrics_lock`, `hash_counter`, `diff_counter`, `save_counter`, `FILE_OPERATIONS`, `PROVENANCE_LOGS`, `provenance_logger`, ...+1 more

---

# MODULE: TEST_ENGINE_INTEGRATION.PY

## test_engine_integration.py
**Lines:** 466

### `TestEngineIntegration`

| Method | Async | Args |
|--------|-------|------|
| `test_arbiter_module_exists` |  | self |
| `test_arbiter_class_exists` |  | self |
| `test_simulation_module_exists` |  | self |
| `test_test_generation_module_exists` |  | self |
| `test_mesh_event_bus_exists` |  | self |
| `test_guardrails_module_exists` |  | self |
| `test_self_healing_fixer_exists` |  | self |
| `test_agent_orchestration_exists` |  | self |

### `TestEngineConfiguration`

| Method | Async | Args |
|--------|-------|------|
| `test_arbiter_config_can_load` |  | self |

### `TestArbiterIntegration`

| Method | Async | Args |
|--------|-------|------|
| `test_arbiter_has_message_queue_service_param` |  | self |
| `test_arbiter_has_event_handlers` |  | self |
| `test_arbiter_has_event_receiver_setup` |  | self |
| `test_event_handler_accepts_data` | ✓ | self |

### `TestArenaIntegration`

| Method | Async | Args |
|--------|-------|------|
| `test_arena_has_event_distribution_route` |  | self |
| `test_arena_injects_dependencies` |  | self |

### `TestMessageQueueServiceIntegration`

| Method | Async | Args |
|--------|-------|------|
| `test_message_queue_service_can_be_imported` |  | self |
| `test_message_queue_service_has_subscribe` |  | self |

### `TestDecisionOptimizerIntegration`

| Method | Async | Args |
|--------|-------|------|
| `test_decision_optimizer_can_be_imported` |  | self |
| `test_decision_optimizer_accepts_arena` |  | self |

### `TestGeneratorIntegration`

| Method | Async | Args |
|--------|-------|------|
| `test_arbiter_has_generator_engine` |  | self |
| `test_generator_output_handler_has_direct_integration` |  | self |
| `test_arena_creates_generator_engine` |  | self |
| `test_generator_runner_can_be_imported` |  | self |
| `test_simulation_settings_exist` |  | self |

### `TestEngineMetrics`

| Method | Async | Args |
|--------|-------|------|
| `test_arbiter_metrics_configured` |  | self |
| `test_simulation_metrics_exist` |  | self |

### `TestEngineArchitecture`

| Method | Async | Args |
|--------|-------|------|
| `test_engines_use_async` |  | self |
| `test_engines_have_error_handling` |  | self |

### `TestEnginesDependencies`

| Method | Async | Args |
|--------|-------|------|
| `test_prometheus_client_available` |  | self |
| `test_opentelemetry_available` |  | self |
| `test_sqlalchemy_available` |  | self |
| `test_fastapi_available` |  | self |
| `test_pydantic_available` |  | self |

---

# MODULE: TEST_ENV.PY

## test_env.py
**Lines:** 67

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `setup_test_environment` |  |  |

**Constants:** `PROJECT_ROOT`

---

# MODULE: TEST_GENERATION

## test_generation/__init__.py
**Lines:** 364

### `PathError` (ValueError)

### `BackendRegistry`

### `PolicyEngine`

### `EventBus`

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `validate_project_root` |  | project_root_str |
| `_ensure_project_root_validated` |  |  |
| `get_validated_project_root` |  |  |
| `_get_onboard_module` |  |  |
| `__getattr__` |  | name |

**Constants:** `__all__`, `logger`, `main_runner_logger`, `_project_root_path`, `_project_root_validated`, `_pkg_dir`, `_src_normal`, `_src_weird`, `_target_mod`, `_loaded`, `_onboard_module_loaded`

---

## test_generation/backends.py
**Lines:** 1414

### `BackendTimeouts`

### `ATCOBackendsConfig`

| Method | Async | Args |
|--------|-------|------|
| `__post_init__` |  | self |

### `BackendRegistry`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `_register_builtin_defaults` |  | self |
| `register_backend` |  | self, language, backend_class |
| `get_backend` |  | self, language |
| `list_backends` |  | self |
| `load_backends_from_config` |  | self, config |
| `_verify_module_integrity` |  | self, module_path, module_file, reference_hashes |

### `TestGenerationBackend` (Protocol)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config, project_root |
| `generate_tests` | ✓ | self, target_identifier, output_path, params |
| `reload_config` |  | self, new_config |

### `GenerationTimeout` (Exception)

### `GenerationRetriableError` (Exception)

### `GenerationPermanentError` (Exception)

### `PynguinBackend`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config, project_root |
| `reload_config` |  | self, new_config |
| `generate_tests` | ✓ | self, target_module, output_path_relative, params |

### `LLMClient` (Protocol)

| Method | Async | Args |
|--------|-------|------|
| `ainvoke` | ✓ | self, prompt |

### `OpenAILLMClient`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, model |
| `ainvoke` | ✓ | self, prompt |

### `StubLLMClient`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, model |
| `ainvoke` | ✓ | self, prompt |

### `_LLMOutputSanitizer`

| Method | Async | Args |
|--------|-------|------|
| `sanitize` |  | output, max_bytes |

### `JestLLMBackend`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config, project_root |
| `llm` |  | self |
| `reload_config` |  | self, new_config |
| `_invoke_llm` | ✓ | self, prompt, timeout |
| `generate_tests` | ✓ | self, target_file_path, output_path_relative, params |

### `DiffblueBackend`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config, project_root |
| `reload_config` |  | self, new_config |
| `_deterministic_chance` |  | self, key |
| `generate_tests` | ✓ | self, target_class_name, output_path_relative, params |

### `CargoBackend`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config, project_root |
| `reload_config` |  | self, new_config |
| `_build_test_prompt` |  | self, functions |
| `_invoke_llm` | ✓ | self, prompt, timeout |
| `generate_tests` | ✓ | self, target_file_path, output_path_relative, params |

### `GoBackend`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config, project_root |
| `reload_config` |  | self, new_config |
| `_build_test_prompt` |  | self, functions |
| `_invoke_llm` | ✓ | self, prompt, timeout |
| `generate_tests` | ✓ | self, target_file_path, output_path_relative, params |

### `MyBackend`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config, project_root |
| `reload_config` |  | self, new_config |
| `generate_tests` | ✓ | self, target, output_path_relative, params |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_log_event_safe` | ✓ | event_type, details |
| `build_default_registry` |  |  |
| `_validate_inputs` |  | target_id, output_path, params, project_root |
| `_get_timeout` |  | cfg, key, default |
| `build_llm_client` |  | config |

**Constants:** `logger`, `TENACITY_AVAILABLE`, `RESOURCE_AVAILABLE`, `ALLOWED_BACKEND_MODULES`, `_VALID_TARGET_ID`

---

## test_generation/compliance_mapper.py
**Lines:** 1285

### `JSONFormatter` (logging.Formatter)

| Method | Async | Args |
|--------|-------|------|
| `format` |  | self, record |

### `ComplianceFramework` (Enum)
**Attributes:** GDPR, SOC2, HIPAA, PCI, CUSTOM

### `ComplianceConfig`

| Method | Async | Args |
|--------|-------|------|
| `__post_init__` |  | self |

### `ComplianceReport`

| Method | Async | Args |
|--------|-------|------|
| `to_json` |  | self |

### `ComplianceRule`

| Method | Async | Args |
|--------|-------|------|
| `check` | ✓ | self, file_path, content, config |

### `GDPRDataProtectionRule` (ComplianceRule)
**Attributes:** frameworks

| Method | Async | Args |
|--------|-------|------|
| `_sync_check` |  | self, file_path, content, config |
| `check` | ✓ | self, file_path, content, config |

### `_BuiltinGDPRRule` (ComplianceRule)
**Attributes:** frameworks

| Method | Async | Args |
|--------|-------|------|
| `check` | ✓ | self, file_path, content, config |

### `CustomComplianceRule` (ComplianceRule)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, frameworks, patterns, description, severity |
| `from_config` |  | cls, config |
| `_sync_check` |  | self, file_path, content, config |
| `check` | ✓ | self, file_path, content, config |

### `ComplianceRuleRegistry`
**Attributes:** _lock

| Method | Async | Args |
|--------|-------|------|
| `__new__` |  | cls |
| `ensure_discovered` | ✓ | self |
| `_discover_rules` | ✓ | self |
| `register_rule` | ✓ | self, rule |
| `get_rules` | ✓ | self, framework |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `audit_event` | ✓ | event_type, details, critical |
| `_ensure_default_rules` | ✓ |  |
| `generate_report_async` | ✓ | project_root, user_id, custom_config |
| `generate_report` | ✓ | project_root, user_id, custom_config |
| `generate_report_sync` |  | project_root |
| `_load_custom_framework` | ✓ | config_path |
| `_process_file` | ✓ | file_path, config, executor |
| `_run_rule_check` | ✓ | rule, file_path, content, framework, config, ...+1 |

**Constants:** `logger`, `handler`, `EMAIL_RE`, `ENCRYPTION_KEYWORDS`, `RULE_REGISTRY`

---

## test_generation/fix_tests.py
**Lines:** 43

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `fix_file` |  | path |
| `main` |  |  |

**Constants:** `ROOT`, `PKG`, `BASE`, `REL_FROM`

---

## test_generation/gen_agent/agents.py
**Lines:** 1803

### `_NoopTimerCtx`

| Method | Async | Args |
|--------|-------|------|
| `__enter__` |  | self |
| `__exit__` |  | self, exc_type, exc, tb |

### `_NoopLabels`

| Method | Async | Args |
|--------|-------|------|
| `inc` |  | self |
| `time` |  | self |

### `_NoopMetric`

| Method | Async | Args |
|--------|-------|------|
| `labels` |  | self |

### `ReviewData` (TypedDict)

### `ExecutionResultsData` (TypedDict)

### `TestAgentState` (TypedDict)

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_make_counter` |  | name, desc, labelnames |
| `_make_histogram` |  | name, desc, labelnames |
| `_metric_labels` |  | metric |
| `_metric_time` |  | metric |
| `_param_profile` |  | func |
| `_with_timing` |  | agent_name |
| `_sanitize_input` |  | text |
| `_is_llm_available` |  | llm |
| `_truncate_log` |  | text, limit |
| `_strip_code_fences` |  | text |
| `_get_test_run_timeout` |  | config |
| `_get_llm_response_content` |  | resp |
| `_get_node_binary` |  | node_name |
| `_pytest_cov_available` |  |  |
| `_call_llm` | ✓ | llm, prompt |
| `_run_bandit` | ✓ | code |
| `_run_locust` | ✓ | locust_script, language |
| `planner_agent` | ✓ | state, llm, config |
| `generator_agent` | ✓ | state, llm, config |
| `refiner_agent` | ✓ | state, llm, config |
| `judge_agent` | ✓ | state, llm, config |
| `adaptive_test_executor_agent` | ✓ | state, _llm, config |
| `run_pytest` | ✓ | code, code_path, timeout, language |
| `run_jest` | ✓ | code, code_path, language, timeout |
| `run_cargo_test` | ✓ | code, code_path, timeout |
| `security_agent` | ✓ | state, llm, audit_logger |
| `performance_agent` | ✓ | state, _llm, config |

**Constants:** `_BLEACH_WARNED`, `agent_runs_total`, `agent_execution_duration`, `_AIOFILES_OK`, `env`, `logger`, `RETRY_CONFIG`

---

## test_generation/gen_agent/api.py
**Lines:** 644

### `GenerateTestsRequest` (BaseModel)
**Attributes:** model_config

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `jwt_required` |  |  |
| `create_access_token` |  |  |
| `get_remote_address` |  |  |
| `with_jwt_required` |  | func |
| `_run_async` | ✓ | coro, timeout |
| `_generate_tests_logic` | ✓ | data |
| `create_app` |  | config |
| `serve_api` |  | host, port |

**Constants:** `CORS`, `Limiter`, `JWTManager`, `PrometheusMetrics`, `get_swaggerui_blueprint`, `LIMITER_AVAILABLE`, `JWT_AVAILABLE`, `logger`, `TestAgentState`

---

## test_generation/gen_agent/atco_signal.py
**Lines:** 955

### `SignalHandlerContext`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, on_interrupt, on_reload |
| `__enter__` |  | self |
| `__exit__` |  | self, exc_type, exc_val, exc_tb |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_log` |  | level, msg |
| `_flush_logging` |  |  |
| `_get_scheduler` |  | loop |
| `_invoke` |  | handler |
| `_run_maybe_async` |  | handler, scheduler |
| `_dump_threads_once` |  |  |
| `_dump_threads` |  |  |
| `_start_force_timer` |  |  |
| `_forward_children` |  |  |
| `_forward_signal_to_children` |  | signum |
| `_setup_faulthandler` |  |  |
| `get_signal_status` |  |  |
| `wait_for_shutdown_started` |  |  |
| `_normalize_signal_names` |  | names |
| `install_default_handlers` |  | on_interrupt, on_reload, signals |
| `install_signal_handlers` |  | handler |
| `uninstall_handlers` |  |  |
| `reconfigure_signals` |  | names |
| `temporarily_uninstall` |  |  |
| `_reset_for_tests` |  |  |
| `graceful_shutdown_coro` | ✓ | signum |

**Constants:** `_shutting_down`, `_signal_count`, `_last_signal_time`, `_installed`, `_on_interrupt_callback`, `_on_reload_callback`, `_active_signals`, `_win_ctrl_handler`, `_auto_set_shutdown_event`, `_fault_dump_fp`, `SIGNAL_DEBOUNCE_MS`, `SHUTDOWN_GRACE_SEC`, `SHUTDOWN_FORCE_SEC`, `ENABLE_FAULTHANDLER`, `DUMP_THREADS_ON_SIGNAL`, ...+7 more

---

## test_generation/gen_agent/cli.py
**Lines:** 582

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `summarize_feedback` | ✓ |  |
| `_default_feedback_path` |  |  |
| `_make_run_id` |  |  |
| `_atomic_write_text` |  | path, data, encoding |
| `_maybe_install_rich` |  | debug |
| `_load_config_from_yaml` |  | path |
| `run_coro_sync` |  | coro |
| `_run_async_command` | ✓ | coro |
| `cli` |  | ctx, config_file, project_root, debug |
| `_generate_async` | ✓ | session, output, ci, project_root |
| `generate` |  | ctx, session, output, ci |
| `serve` |  | host, port |
| `feedback` |  | action, log_file, json_out |
| `status` |  | ctx, json_out |

**Constants:** `console`, `err_console`, `DIST_NAME`, `version`, `no_color`, `logger`, `FEEDBACK_LOG_FILE`

---

## test_generation/gen_agent/graph.py
**Lines:** 456

### `FallbackGraph`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, steps |
| `ainvoke` | ✓ | self, initial_state, config |

### `TestAgentState` (TypedDict)

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_get_float_env` |  | name, default |
| `_get_int_env` |  | name, default |
| `_step` | ✓ | coro, timeout |
| `_decide_to_refine` |  | state, config |
| `build_graph` |  | llm, checkpointer |
| `invoke_graph` | ✓ | graph, initial_state, config, progress_callback |

**Constants:** `logger`, `FORCE_FALLBACK`, `graph_retry`

---

## test_generation/gen_agent/io_utils.py
**Lines:** 663

### `_NoopMetric`

| Method | Async | Args |
|--------|-------|------|
| `labels` |  | self |
| `observe` |  | self |
| `inc` |  | self |
| `time` |  | self |

### `JSONFormatter` (logging.Formatter)

| Method | Async | Args |
|--------|-------|------|
| `format` |  | self, record |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_register_metric` |  | factory |
| `_noop_lock` |  |  |
| `validate_and_resolve_path` |  | path |
| `_canonical_lock_path` |  | path |
| `_active_log_path` |  | resolved_path |
| `validate_relative_path` |  | path |
| `append_to_feedback_log` | ✓ | feedback_log_path, feedback_data, config |
| `async_read_file` | ✓ | path |
| `async_write_file` | ✓ | path, content |
| `summarize_feedback` | ✓ | feedback_log_path |
| `_summarize_feedback_cached` |  | feedback_log_path, cache_token |
| `test_io_utils` | ✓ |  |

**Constants:** `logger`, `io_write_duration`, `io_read_duration`, `io_write_bytes`, `FEEDBACK_COMPRESS_BYTES`

---

## test_generation/gen_agent/runtime.py
**Lines:** 924

### `TestAgentState` (TypedDict)

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_coerce` |  | v |
| `_parse_kv_string` |  | s |
| `_load_config` |  | config_file |
| `_check_module` |  | name |
| `_load_and_check_deps` |  |  |
| `redact_sensitive` |  | data, extra_keys |
| `setup_logging` |  | is_ci, log_level, log_file_path, enable_file |
| `is_ci_environment` |  |  |
| `install_package` |  | package_name |
| `_normalize_pkg_name` |  | pkg_name |
| `run_dependency_check` | ✓ | provider, is_ci |
| `ensure_package` | ✓ | package_name, is_ci |
| `ensure_package_sync` |  | package_name, is_ci |
| `run_dependency_check_async` | ✓ | is_ci |
| `health_check` |  |  |
| `interactive_session_creator` | ✓ | session_name |
| `ensure_session_file` | ✓ | session_name, is_ci |
| `ensure_session_file_sync` |  | session_name, is_ci |
| `load_or_create_session_spec` | ✓ | session_path, language, environment |
| `validate_session_inputs` |  | session_name, language, framework |
| `_validate_llm_session_inputs` |  | provider, model |
| `init_llm` |  | provider, model |
| `model_defaults_to_env` |  | model_cls |

**Constants:** `logger`, `Dynaconf`, `BaseSettings`, `AIOFILES_AVAILABLE`, `FILELOCK_AVAILABLE`, `FLASK_AVAILABLE`, `PYTEST_AVAILABLE`, `COVERAGE_AVAILABLE`, `BANDIT_AVAILABLE`, `LOCUST_AVAILABLE`, `AUDIT_LOGGER_AVAILABLE`, `PSUTIL_AVAILABLE`, `_default_sessions_dir`, `_default_tests_output_dir`, `SESSIONS_DIR`, ...+6 more

---

## test_generation/gen_plugins.py
**Lines:** 884

### `BaseTestGenerator`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `generate` |  | self, code, config |

### `PythonTestGenerator` (BaseTestGenerator)

| Method | Async | Args |
|--------|-------|------|
| `generate` |  | self, code, config |

### `JavaScriptTestGenerator` (BaseTestGenerator)

| Method | Async | Args |
|--------|-------|------|
| `generate` |  | self, code, config |

### `TestGeneratorRegistry`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `register` |  | self, language, generator |
| `get` |  | self, language |

### `_XAIAPIStub`

| Method | Async | Args |
|--------|-------|------|
| `generate_tests` |  | self, code, language, test_framework |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_sanitize_identifier` |  | name |
| `_limit_tests_per_function` |  | blocks, max_per_fn |
| `_assemble_file` |  | header, blocks |
| `_normalize_cfg` |  | cfg |
| `_call_ai_for_tests` |  | code, language, config |
| `generate_tests` |  | code, language, config |

**Constants:** `logger`, `SUPPORTED_LANGUAGES`, `SUPPORTED_FRAMEWORKS`, `DEFAULT_PYTHON_TEST_FRAMEWORK`, `DEFAULT_JS_TEST_FRAMEWORK`, `LANGUAGE_GENERATORS`, `xai_api`, `__all__`

---

## test_generation/onboard.py
**Lines:** 456

### `OnboardConfigFallback` (BaseModel if PYDANTIC_AVAILABLE else object)

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_get_or_create_metric` |  | metric_class, name, documentation, labelnames |
| `_attempt_import` |  |  |
| `_initialize_fallbacks` |  |  |
| `_onboard_fallback` | ✓ | args |
| `get_module_status` |  |  |
| `ensure_initialized` |  |  |

**Constants:** `logger`, `ONBOARD_IMPORT_TOTAL`, `ONBOARD_OPS_TOTAL`, `ONBOARD_OPS_LATENCY`, `_MODULE_STATE`, `OnboardConfig`, `ONBOARD_DEFAULTS`, `CORE_VERSION`, `onboard`, `__all__`

---

## test_generation/orchestrator/audit.py
**Lines:** 181

### `AuditLogger`

| Method | Async | Args |
|--------|-------|------|
| `from_environment` |  |  |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_get_audit_log_file` |  |  |
| `_json_serializable_default` |  | obj |
| `audit_event` | ✓ | event_type, details, critical |
| `append_to_feedback_log` | ✓ | feedback_log_path, feedback_data, config |

**Constants:** `__all__`, `RUN_ID`, `LOGGER_NAME`, `_logger`, `_audit`, `FEEDBACK_LOG_FILE`

---

## test_generation/orchestrator/cli.py
**Lines:** 451

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_is_unittest_mock` |  | obj |
| `_is_async_test_context` |  |  |
| `_make_run_id` |  |  |
| `graceful_shutdown` |  | signum, frame |
| `_check_disk_space` |  | path, min_mb |
| `_check_writable` |  | path |
| `normalize_results` |  | obj |
| `_build_parser` |  |  |
| `_amain` | ✓ | argv |
| `main` |  | argv |

**Constants:** `EXIT_SUCCESS`, `EXIT_FATAL_ERROR`, `EXIT_QUARANTINE_REQUIRED`, `EXIT_PR_REQUIRED`, `EXIT_PR_CREATION_FAILED`, `_check_permissions`, `cli`

---

## test_generation/orchestrator/config.py
**Lines:** 758

### `TestConfigLoading` (unittest.TestCase)

| Method | Async | Args |
|--------|-------|------|
| `setUp` |  | self |
| `tearDown` |  | self |
| `_write_config` |  | self, content |
| `test_deep_merge_and_defaults` |  | self |
| `test_path_sanitization_rejection` |  | self |
| `test_invalid_config_schema_falls_back` |  | self |
| `test_immutability` |  | self |
| `test_deep_merge_with_non_dict_override` |  | self |
| `test_missing_config_file` |  | self |
| `test_empty_config_file` |  | self |
| `test_corrupted_json_falls_back` |  | self |
| `test_invalid_top_level_config_type` |  | self |
| `test_non_dict_with_additional_properties_true` |  | self |
| `test_invalid_sub_field_reverts_top_level_field_only` |  | self |
| `test_deep_merge_partial_override` |  | self |
| `test_deep_merge_with_invalid_subfield` |  | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `sanitize_path` |  | base, rel |
| `_mkdir_parent_first` |  | path |
| `_ensure_dir` |  | root, rel |
| `_ensure_artifact_dirs` |  | project_root, config |
| `_deep_freeze` |  | obj |
| `_deep_merge` |  | dst, src, parent_key |
| `load_config` |  | project_root, config_file |

**Constants:** `QUARANTINE_DIR`, `GENERATED_OUTPUT_DIR`, `SARIF_EXPORT_DIR`, `AUDIT_LOG_FILE`, `COVERAGE_REPORTS_DIR`, `HTML_REPORTS_DIR`, `VENV_TEMP_DIR`, `LOGGING_CONFIG`, `CONFIG_SCHEMA`, `__all__`

---

## test_generation/orchestrator/console.py
**Lines:** 434

### `_ProgressTask`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, progress, task_id |
| `update` |  | self, advance |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_logger_success` |  | self, msg |
| `init_console_and_styles` |  | cfg |
| `_format_msg` |  | msg, level |
| `_ensure_console` |  |  |
| `_log_print_backend` |  | message, level, style |
| `_log_rich_backend` |  | message, level, style |
| `set_plain_logging` |  | force |
| `set_rich_logging` |  | force |
| `rich_enabled` |  |  |
| `log` |  | message, level, style |
| `get_logger` |  | name |
| `fallback_to_basic_logging` |  |  |
| `configure_logging` |  | logging_config, audit_log_file, audit_handler_name, error_log_path |
| `log_progress_bars` |  | title, tasks |

**Constants:** `RICH_AVAILABLE`, `logger`, `audit_logger_instance`, `_log_lock`, `_FORCE_PLAIN`, `SUCCESS`, `_LEVEL_MAP`

---

## test_generation/orchestrator/metrics.py
**Lines:** 249

### `_DummyTimerCtx`

| Method | Async | Args |
|--------|-------|------|
| `__enter__` |  | self |
| `__exit__` |  | self, exc_type, exc, tb |
| `__aenter__` | ✓ | self |
| `__aexit__` | ✓ | self, exc_type, exc, tb |

### `_NoopTimer`

| Method | Async | Args |
|--------|-------|------|
| `__enter__` |  | self |
| `__exit__` |  | self |

### `_NoopMetric`

| Method | Async | Args |
|--------|-------|------|
| `labels` |  | self |
| `inc` |  | self |
| `observe` |  | self |
| `time` |  | self |

### `_MetricProxy`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, factory, label_names |
| `_ensure` |  | self |
| `labels` |  | self |
| `inc` |  | self |
| `observe` |  | self, value |
| `time` |  | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_histogram_factory` |  |  |
| `_counter_success_factory` |  |  |
| `_counter_failure_factory` |  |  |
| `_gauge_state_factory` |  |  |
| `_counter_repair_factory` |  |  |

**Constants:** `logger`, `METRICS_AVAILABLE`, `METRICS_ENABLED`, `_WARNED_DISABLED`, `generation_duration`, `integration_success`, `integration_failure`, `agent_state`, `repair_attempts`

---

## test_generation/orchestrator/orchestrator.py
**Lines:** 1891

### `OrchestratorError` (Exception)

### `InitializationError` (OrchestratorError)

### `OrchestrationPipelineError` (OrchestratorError)

### `GenerationOrchestrator`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config, project_root, suite_dir |
| `run_pipeline` | ✓ | self, coverage_xml |
| `_load_component` |  | self, component_name, config_key |
| `_load_test_enricher` |  | self |
| `generate_tests_for_targets` | ✓ | self, targets, output_base_relative |
| `integrate_and_validate_generated_tests` | ✓ | self, generation_summary |
| `_calculate_test_quality_score` |  | self, test_passed, coverage_increase, mutation_score |
| `_handle_single_test_integration` | ✓ | self, src_test_path_relative, target_identifier, language |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `run_pytest_and_coverage` | ✓ |  |
| `_append_local_audit_log` |  | project_root, config, event_name, detail |
| `_maybe_await` | ✓ | val |
| `_execute_test_command` | ✓ | language, project_root, test_path_relative, target_identifier, coverage_report_path, ...+2 |

**Constants:** `compare_files`, `backup_existing_test`, `generate_file_hash`

---

## test_generation/orchestrator/pipeline.py
**Lines:** 360

### `_JsonFormatter` (logging.Formatter)

| Method | Async | Args |
|--------|-------|------|
| `format` |  | self, record |

### `PipelineConfig`

| Method | Async | Args |
|--------|-------|------|
| `from_dict` |  | cls, data |
| `artifact_dirs` |  | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_is_awaitable` |  | val |
| `_maybe_await` | ✓ | val |
| `_mkdirs_resilient` |  | base_root, rel_path |
| `_load_config` |  | project_root, config_file |
| `_ensure_artifact_dirs` |  | project_root, cfg |
| `_process_target` | ✓ |  |
| `_run_reporting` | ✓ |  |
| `main` | ✓ | args |

---

## test_generation/orchestrator/reporting.py
**Lines:** 846

### `DummyMetric`
**Attributes:** DEFAULT_BUCKETS

| Method | Async | Args |
|--------|-------|------|
| `labels` |  | self |
| `observe` |  | self |
| `inc` |  | self |
| `time` |  | self |
| `__enter__` |  | self |
| `__exit__` |  | self, exc_type, exc, tb |

### `ReportValidationError` (ValueError)

### `HTMLReporter`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, project_root, report_dir, sarif_dir |
| `_sanitize_and_create_dir` |  | self, path_relative |
| `_validate_report_schema` |  | self, overall_results |
| `_sanitize_data_recursively` |  | self, data |
| `generate_markdown_report` | ✓ | self, overall_results |
| `generate_html_report` | ✓ | self, overall_results, policy_engine |
| `build` |  | self, overall_results, policy_engine |
| `_sanitize_path` |  | self, path |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_write_sarif_atomically` | ✓ | path, data |
| `cleanup_old_temp_files` | ✓ | path |

**Constants:** `__all__`, `_ENABLE_PROM`, `logger`

---

## test_generation/orchestrator/stubs.py
**Lines:** 400

### `DummyPolicyEngine`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `should_integrate_test` | ✓ | self |
| `requires_pr_for_integration` | ✓ | self |
| `policy_hash` |  | self |

### `DummyEventBus`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `publish` | ✓ | self |

### `DummySecurityScanner`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `scan_test_file` | ✓ | self |

### `DummyKnowledgeGraphClient`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `update_module_metrics` | ✓ | self |

### `DummyPRCreator`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `create_pr` | ✓ | self |
| `create_jira_ticket` | ✓ | self |

### `DummyMutationTester`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `run_mutations` | ✓ | self |

### `DummyTestEnricher`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `enrich_test` | ✓ | self, content |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_check_stub_usage_safety` |  |  |

**Constants:** `_OFFLINE_MODE`, `_ENVIRONMENT`, `__all__`

---

## test_generation/orchestrator/venvs.py
**Lines:** 684

### `EnvHandle` (NamedTuple)

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `sanitize_path` |  | path, project_root |
| `_validate_deps` |  | deps |
| `_cfg_int` |  | key, default |
| `_cfg_float` |  | key, default |
| `_cfg_bool` |  | key, default |
| `create_and_install_venv` | ✓ | project_root, deps |
| `temporary_env` | ✓ | project_root, language, required_deps |
| `_create_and_manage_python_env` | ✓ | project_root, required_deps, persist, keep_on_failure, env_subdir |
| `_create_and_manage_npm_env` | ✓ | project_root, required_deps, persist, keep_on_failure, env_subdir |
| `_create_and_manage_java_env` | ✓ | project_root, required_deps, persist, keep_on_failure, env_subdir |
| `_create_and_manage_rust_env` | ✓ | project_root, required_deps, persist, keep_on_failure, env_subdir |
| `_create_and_manage_go_env` | ✓ | project_root, required_deps, persist, keep_on_failure, env_subdir |

---

## test_generation/policy_and_audit.py
**Lines:** 1745

### `Constants`
**Attributes:** DEFAULT_POLICIES, SECURITY_LEVELS, OPA_POLICY_PATHS, SENSITIVE_KEYS, AUDIT_EVENT_TYPES

### `Configuration`

| Method | Async | Args |
|--------|-------|------|
| `from_env` |  | cls, project_root |

### `_NoOpMetric`

| Method | Async | Args |
|--------|-------|------|
| `labels` |  | self |
| `inc` |  | self |
| `observe` |  | self |
| `time` | ✓ | self |

### `MetricsClient`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |

### `AuditLogger`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, log_file |
| `from_environment` |  | cls |
| `log_event` | ✓ | self, event_type, details, correlation_id, critical |

### `FileSystem` (ABC)

| Method | Async | Args |
|--------|-------|------|
| `read_json` |  | self, path |
| `file_exists` |  | self, path |
| `generate_file_hash` |  | self, path, project_root |
| `cleanup_temp_dir` |  | self, path |

### `LocalFileSystem` (FileSystem)

| Method | Async | Args |
|--------|-------|------|
| `read_json` |  | self, path |
| `file_exists` |  | self, path |
| `generate_file_hash` |  | self, path, project_root |
| `cleanup_temp_dir` | ✓ | self, path |

### `PolicyClient` (ABC)

| Method | Async | Args |
|--------|-------|------|
| `evaluate_policy` | ✓ | self, policy_path, input_data |

### `OPAPolicyClient` (PolicyClient)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config, audit_logger, metrics_client |
| `evaluate_policy` | ✓ | self, policy_path, input_data |
| `_parse_opa_response` |  | self, opa_response |

### `PolicyEngine`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, policy_config_path, config_or_project_root, audit_logger, metrics_client, ...+2 |
| `create` | ✓ | cls, policy_config_path, config, audit_logger, metrics_client, ...+2 |
| `_load_policies_sync` |  | self |
| `_load_policies` | ✓ | self |
| `_validate_policy_schema` |  | self, policies |
| `reload_policies` | ✓ | self |
| `_deny` | ✓ | self, rule, input_data, reason, critical |
| `_allow` | ✓ | self, rule, input_data, reason |
| `should_generate_tests` | ✓ | self, module_identifier, language |
| `should_integrate_test` | ✓ | self, module_identifier, test_quality_score, language, has_security_issues, ...+1 |
| `requires_pr_for_integration` | ✓ | self, module_identifier, language, test_quality_score |

### `EventBus`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config, audit_logger, metrics_client, message_queue_service, ...+1 |
| `publish` | ✓ | self, event_name, data |
| `_publish_to_slack` | ✓ | self, event_name, data, slack_url, slack_events |
| `_publish_to_webhook` | ✓ | self, event_name, data, webhook_hooks, webhook_events |
| `_send_notification_with_retry` | ✓ | self, url, payload, service_name |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_get_real_audit_event` |  |  |
| `audit_event` | ✓ | event_type, details, critical |
| `redact_sensitive` |  | obj |

**Constants:** `logger`, `AUDIT_LOGGER_AVAILABLE`, `_real_audit_event`, `METRICS_AVAILABLE`, `_metrics_singleton`, `metrics_client`, `AIOHTTP_AVAILABLE`, `TENACITY_AVAILABLE`, `RETRY_STOP`, `RETRY_WAIT`, `__all__`

---

## test_generation/utils.py
**Lines:** 2288

### `PathError` (ValueError)

### `ATCOConfig`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config, project_root |

### `SecurityScanner`
**Attributes:** SEV_RANK

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, project_root, config |
| `scan_test_file` | ✓ | self, file_path_relative, language |

### `KnowledgeGraphClient`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, project_root, config |
| `update_module_metrics` | ✓ | self, module_identifier, metrics |

### `PRCreator`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, project_root, config |
| `create_pr` | ✓ | self, branch_name, title, description, files_to_add |
| `create_jira_ticket` | ✓ | self, title, description, project_key |

### `MutationTester`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, project_root, config |
| `run_mutations` | ✓ | self, source_file_relative, test_file_relative, language |

### `CodeEnricher`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, plugins |
| `enrich_test` | ✓ | self, test_code, language, project_root |

### `_RobustEnvBuilder` (venv.EnvBuilder)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `executable_to_symlink` |  | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `validate_and_resolve_path` |  | base_path, user_input_path, allow_outside_base |
| `_is_rel_to` |  | child, parent |
| `_fire_and_forget` |  | coro |
| `_fsync_file` |  | path |
| `_fsync_dir` |  | path |
| `_observe_duration` | ✓ | metric |
| `log` |  | msg, level, style |
| `zero_trust_guard` |  | func |
| `maybe_await` | ✓ | coro |
| `_is_path_allowed_for_write` |  | target_path, config |
| `atomic_write` |  | file_path, content |
| `monitor_and_prioritize_uncovered_code` | ✓ | coverage_file, policy_engine, project_root, config |
| `generate_file_hash` |  | filepath_relative, project_root, hash_algorithm |
| `secure_write_file` | ✓ | filepath_relative, project_root, content, mode, permissions |
| `backup_existing_test` | ✓ | dst_relative_path, project_root |
| `compare_files` |  | file1_full_path, file2_full_path |
| `cleanup_temp_dir` | ✓ | path |
| `cleanup_path_safe` |  | path |
| `add_atco_header` |  | test_code, language, project_root |
| `add_mocking_framework_import` |  | test_code, language, project_root |
| `llm_refine_test_plugin` | ✓ | test_code, language, project_root |
| `create_and_install_venv` | ✓ | venv_rel_or_root, project_root_or_deps, deps, config |
| `run_pytest_and_coverage` | ✓ | venv_python_full_path, test_path_relative, target_module_identifier, project_root, coverage_report_path_relative, ...+1 |
| `run_jest_and_coverage` | ✓ | project_root, test_path_relative, target_file_path_relative, coverage_report_path_relative, config |
| `run_junit_and_coverage` | ✓ | project_root, test_path_relative, target_class_identifier, coverage_report_path_relative, config |
| `parse_coverage_delta` | ✓ | coverage_report_full_path, target_identifier, language |
| `scan_for_uncovered_code_from_xml` |  | coverage_xml_relative_path, project_root |
| `scan_for_uncovered_code_rust` |  | lcov_report_relative_path, project_root |
| `prioritize_test_targets` | ✓ | coverage_report_path, project_root, uncovered_python_modules, policy_engine |
| `check_and_install_dependencies` | ✓ | dependencies, project_root |
| `init_llm` |  | model_name, temperature, api_key, backend |

**Constants:** `__version__`, `logger`, `console`

---

# MODULE: TESTS

## tests/test_agent_orchestration_crew_config.py
**Lines:** 115

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `temp_yaml` |  | tmp_path |
| `test_yaml_schema_validation` |  | temp_yaml |
| `test_yaml_load_no_file` |  |  |
| `test_yaml_invalid_structure` |  | temp_yaml |
| `test_integration_crew_manager_with_config` | ✓ | temp_yaml, monkeypatch |

---

## tests/test_agent_orchestration_crew_manager.py
**Lines:** 404

### `MockCrewAgent` (CrewAgentBase)

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_policy` |  |  |
| `mock_metrics_hook` |  |  |
| `mock_audit_hook` |  |  |
| `mock_sandbox_runner` |  |  |
| `mock_agent_health_poller` |  |  |
| `mock_agent_stop_commander` |  |  |
| `crew_manager` | ✓ | mock_policy, mock_metrics_hook, mock_audit_hook, mock_sandbox_runner, mock_agent_health_poller, ...+2 |
| `caplog` |  | caplog |
| `test_register_and_get_agent_class` | ✓ |  |
| `test_get_agent_class_not_registered` | ✓ |  |
| `test_sanitize_dict` | ✓ |  |
| `test_structured_log` | ✓ | caplog |
| `test_add_agent_success` | ✓ | crew_manager |
| `test_add_agent_invalid_name` | ✓ | crew_manager |
| `test_add_agent_invalid_config` | ✓ | crew_manager |
| `test_add_agent_rbac_failure` | ✓ | crew_manager |
| `test_add_agent_max_agents` | ✓ | crew_manager, monkeypatch |
| `test_sync_add_agent` | ✓ |  |
| `test_remove_agent` | ✓ | crew_manager |
| `test_start_agent_success` | ✓ | crew_manager |
| `test_start_agent_resource_error` | ✓ | crew_manager, monkeypatch |
| `test_stop_agent_success` | ✓ | crew_manager |
| `test_reload_agent` | ✓ | crew_manager |
| `test_scale_agents` | ✓ | crew_manager |
| `test_heartbeat_monitor` | ✓ |  |
| `test_health_report` | ✓ | crew_manager |
| `test_lint` | ✓ | crew_manager |
| `test_describe` | ✓ | crew_manager |
| `test_save_load_redis` | ✓ | crew_manager |
| `test_shutdown` | ✓ | crew_manager |

---

## tests/test_agent_orchestration_integration.py
**Lines:** 148

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `temp_config` |  | tmp_path |
| `mock_sandbox_runner` |  |  |
| `mock_agent_health_poller` |  |  |
| `test_integration_load_config_and_manage_agents` | ✓ | temp_config, mock_sandbox_runner, mock_agent_health_poller |
| `test_integration_scale_with_config` | ✓ | temp_config, mock_sandbox_runner, mock_agent_health_poller |

---

## tests/test_arbiter_agent_state.py
**Lines:** 424

### `TestValidation`

| Method | Async | Args |
|--------|-------|------|
| `test_agent_state_validate_success` | ✓ | self, metrics |
| `test_agent_state_validate_failures` | ✓ | self, field, value, label, metrics |
| `test_agent_metadata_validate_success` | ✓ | self, metrics |

### `TestDatabaseSync`

| Method | Async | Args |
|--------|-------|------|
| `test_db_insert_valid_agent_state` |  | self, session |
| `test_db_check_constraint_energy` |  | self, session |
| `test_json_field_serialization` |  | self, session |

### `TestAsyncModels`

| Method | Async | Args |
|--------|-------|------|
| `test_agent_state_init` | ✓ | self, async_session |
| `test_agent_state_defaults` | ✓ | self, async_session |
| `test_agent_state_unique_name` | ✓ | self, async_session |
| `test_concurrent_operations` | ✓ | self, async_session |

### `TestRepresentations`

| Method | Async | Args |
|--------|-------|------|
| `test_agent_state_repr` |  | self |
| `test_agent_metadata_repr` |  | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_imports` |  |  |
| `load_agent_state_module` |  |  |
| `engine` |  |  |
| `session` |  | engine |
| `async_engine` | ✓ |  |
| `async_session` | ✓ | async_engine |
| `metrics` |  |  |

**Constants:** `agent_state_module`, `AgentState`, `AgentMetadata`, `Base`, `SCHEMA_VALIDATION_ERRORS`

---

## tests/test_arbiter_arbiter.py
**Lines:** 342

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `get_arbiter_module` |  |  |
| `lazy_load_arbiter` |  |  |
| `generate_fernet_key` |  |  |
| `test_config` |  |  |
| `mock_db_client` |  |  |
| `mock_engine` |  | tmp_path |
| `test_arbiter_module_loaded` |  |  |
| `test_available_classes` |  |  |
| `test_minimal_arbiter_creation` | ✓ | test_config, mock_engine |
| `test_monitor_class_exists` |  | tmp_path |
| `test_simulation_engine_exists` |  |  |
| `test_simulation_run_if_exists` | ✓ |  |
| `test_agent_state_manager_exists` |  |  |
| `test_arbiter_with_mocked_dependencies` | ✓ | test_config, mock_engine, mock_db_client |
| `test_monitor_log_action_if_exists` | ✓ | tmp_path |
| `test_list_all_arbiter_attributes` |  |  |

**Constants:** `current_dir`, `arbiter_dir`, `parent_dir`, `Base`, `arbiter`

---

## tests/test_arbiter_arbiter_growth_arbiter_growth_integration.py
**Lines:** 526

### `HealthStatus` (Enum)
**Attributes:** INITIALIZING, HEALTHY, DEGRADED, STOPPED

### `BreakerListener` (CircuitBreakerListener)

| Method | Async | Args |
|--------|-------|------|
| `before_call` |  | self, cb, func |
| `success` |  | self, cb |
| `failure` |  | self, cb, exc |
| `state_change` |  | self, cb, old_state, new_state |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_etcd_client` |  |  |
| `mock_config_store` | ✓ |  |
| `mock_idempotency_store` | ✓ |  |
| `mock_rate_limiter` |  |  |
| `mock_storage_backend` | ✓ | tmp_path |
| `mock_knowledge_graph` |  | mock_config_store |
| `mock_feedback_manager` |  | mock_config_store |
| `mock_plugin` |  |  |
| `arbiter_manager_factory` | ✓ | mock_config_store, mock_idempotency_store, mock_storage_backend, mock_knowledge_graph, mock_feedback_manager |
| `test_integration_full_event_flow` | ✓ | arbiter_manager_factory, mock_plugin, caplog |
| `test_integration_rate_limit_rejection` | ✓ | arbiter_manager_factory |
| `test_integration_circuit_breaker_open` | ✓ | arbiter_manager_factory, mock_storage_backend |
| `test_integration_audit_tamper_detection` | ✓ | arbiter_manager_factory, mock_storage_backend |
| `test_integration_config_fallback` | ✓ | mock_config_store |
| `test_integration_plugin_call` | ✓ | arbiter_manager_factory, mock_plugin |
| `test_integration_shutdown_cleanup` | ✓ | mock_config_store, mock_idempotency_store, mock_storage_backend, mock_knowledge_graph, mock_feedback_manager |
| `test_integration_concurrent_events` | ✓ | arbiter_manager_factory |
| `test_integration_anomaly_detection` | ✓ | arbiter_manager_factory |
| `test_integration_health_monitoring` | ✓ | arbiter_manager_factory |
| `test_integration_snapshot_recovery` | ✓ | mock_config_store, mock_idempotency_store, mock_storage_backend, mock_knowledge_graph, mock_feedback_manager |
| `test_multi_plugin_execution` | ✓ | arbiter_manager_factory |
| `test_kafka_backend_integration` | ✓ | arbiter_manager_factory, mock_config_store |

---

## tests/test_arbiter_arbiter_growth_arbiter_growth_manager.py
**Lines:** 623

### `HealthStatus` (Enum)
**Attributes:** INITIALIZING, HEALTHY, DEGRADED, STOPPED

### `BreakerListener` (CircuitBreakerListener)

| Method | Async | Args |
|--------|-------|------|
| `before_call` |  | self, cb, func |
| `success` |  | self, cb |
| `failure` |  | self, cb, exc |
| `state_change` |  | self, cb, old_state, new_state |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_config_store` |  |  |
| `mock_storage_backend` |  |  |
| `mock_knowledge_graph` |  | mock_config_store |
| `mock_feedback_manager` |  | mock_config_store |
| `mock_idempotency_store` |  |  |
| `mock_clock` |  |  |
| `manager_factory` | ✓ | mock_config_store, mock_storage_backend, mock_knowledge_graph, mock_feedback_manager, mock_idempotency_store, ...+1 |
| `test_init` | ✓ | manager_factory, mock_storage_backend, mock_idempotency_store |
| `test_start_and_stop` | ✓ | manager_factory, mock_storage_backend, caplog |
| `test_record_growth_event_happy_path` | ✓ | manager_factory, mock_storage_backend, mock_knowledge_graph |
| `test_queue_full_error` | ✓ | manager_factory |
| `test_circuit_breaker_opens_and_rejects` | ✓ | manager_factory |
| `test_audit_chain_tampered_on_hash_mismatch` | ✓ | manager_factory, mock_storage_backend |
| `test_health_status_reports_correctly` | ✓ | manager_factory |
| `test_readiness_probe_succeeds` | ✓ | manager_factory |
| `test_concurrent_operations_are_processed` | ✓ | manager_factory |
| `test_save_errors_increment_metric` | ✓ | manager_factory, mock_storage_backend |
| `test_anomaly_detection_sets_metric` | ✓ | manager_factory |
| `test_snapshot_persistence` | ✓ | manager_factory, mock_storage_backend |
| `test_rate_limiting` | ✓ | manager_factory |
| `test_graceful_shutdown_saves_pending` | ✓ | manager_factory, mock_storage_backend |
| `test_plugin_hooks` | ✓ | manager_factory |
| `test_level_up` | ✓ | manager_factory |
| `test_idempotency_key_generation` | ✓ | manager_factory |
| `test_snapshot_interval` | ✓ | manager_factory, mock_storage_backend, mock_clock, mock_config_store |
| `test_audit_chaining` | ✓ | manager_factory, mock_storage_backend, caplog |

---

## tests/test_arbiter_arbiter_growth_config_store.py
**Lines:** 431

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_etcd_client` | ✓ |  |
| `config_store_defaults` | ✓ |  |
| `config_store_with_fallback` | ✓ | tmp_path, mocker |
| `config_store_with_etcd` | ✓ | mock_etcd_client |
| `rate_limiter` | ✓ | config_store_defaults |
| `test_init_no_etcd` | ✓ | caplog |
| `test_get_config_uses_default_when_all_fails` | ✓ | config_store_defaults, caplog |
| `test_get_config_from_etcd_successfully` | ✓ | config_store_with_etcd, mock_etcd_client |
| `test_get_config_etcd_fails_then_uses_fallback` | ✓ | config_store_with_fallback, caplog |
| `test_get_config_uses_cache_on_second_call` | ✓ | config_store_with_etcd, mock_etcd_client |
| `test_get_config_refetches_after_cache_expires` | ✓ | config_store_with_etcd, mock_etcd_client |
| `test_get_config_raises_key_error_if_not_found` | ✓ | config_store_defaults |
| `test_etcd_retry_logic_succeeds` | ✓ | config_store_with_etcd, mock_etcd_client |
| `test_rate_limiter_acquire_immediately` | ✓ | rate_limiter |
| `test_rate_limiter_blocks_then_succeeds` | ✓ | config_store_defaults |
| `test_rate_limiter_times_out` | ✓ | config_store_defaults |
| `test_rate_limiter_concurrent_acquires` | ✓ | rate_limiter |
| `test_negative_cache_ttl` | ✓ | config_store_with_etcd |
| `test_fallback_corrupted` | ✓ | caplog, mocker |
| `test_fallback_corrupted` | ✓ | tmp_path, mocker, caplog |
| `test_concurrent_config_fetches` | ✓ | config_store_with_etcd, mock_etcd_client |

---

## tests/test_arbiter_arbiter_growth_exceptions.py
**Lines:** 198

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `caplog` |  | caplog |
| `test_arbiter_growth_error_init_no_details` |  | caplog |
| `test_arbiter_growth_error_init_with_details` |  | caplog |
| `test_arbiter_growth_error_subclassing` |  |  |
| `test_operation_queue_full_error_init` |  | caplog |
| `test_rate_limit_error_init` |  | caplog |
| `test_circuit_breaker_open_error_init` |  | caplog |
| `test_audit_chain_tampered_error_init` |  | caplog |
| `test_arbiter_growth_error_none_details` |  | caplog |
| `test_arbiter_growth_error_serialization` |  |  |
| `test_exception_hierarchy` |  |  |
| `test_logging_stack_trace` |  | caplog |
| `test_catch_arbiter_growth_error` |  |  |
| `test_arbiter_growth_error_complex_details` |  | caplog |
| `test_rate_limit_error_metrics` |  | caplog |

**Constants:** `logger`

---

## tests/test_arbiter_arbiter_growth_idempotency.py
**Lines:** 338

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_opentelemetry_context` |  |  |
| `tracer` |  |  |
| `mock_redis` | ✓ |  |
| `set_env_redis_url` |  | monkeypatch |
| `idempotency_store` | ✓ | mock_redis, set_env_redis_url |
| `test_init_no_redis_url` |  | monkeypatch |
| `test_init_with_custom_params` |  | set_env_redis_url |
| `test_check_and_set_miss` | ✓ | idempotency_store, mock_redis |
| `test_check_and_set_hit` | ✓ | idempotency_store, mock_redis |
| `test_check_and_set_redis_error` | ✓ | idempotency_store, mock_redis |
| `test_check_and_set_empty_key` | ✓ | idempotency_store |
| `test_check_and_set_no_redis` | ✓ | set_env_redis_url |
| `test_start_success` | ✓ | set_env_redis_url, caplog |
| `test_start_idempotent` | ✓ | set_env_redis_url |
| `test_start_retry_logic` | ✓ | set_env_redis_url |
| `test_start_fails_after_max_retries` | ✓ | set_env_redis_url, caplog |
| `test_stop_success` | ✓ | idempotency_store, mock_redis |
| `test_stop_handles_error_gracefully` | ✓ | idempotency_store, mock_redis, caplog |
| `test_stop_when_not_started` | ✓ | set_env_redis_url |
| `test_concurrent_check_and_set` | ✓ | idempotency_store, mock_redis |
| `test_check_and_set_with_custom_ttl` | ✓ | idempotency_store, mock_redis |
| `test_cluster_mode_initialization` | ✓ | set_env_redis_url |

---

## tests/test_arbiter_arbiter_growth_metrics.py
**Lines:** 411

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `isolated_registry` |  |  |
| `mock_config_store` |  |  |
| `test_get_or_create_counter_new` |  | isolated_registry |
| `test_get_or_create_gauge_new` |  | isolated_registry |
| `test_get_or_create_histogram_new` |  | isolated_registry |
| `test_get_or_create_existing_metric_same_type` |  | isolated_registry |
| `test_get_or_create_handles_conflicting_metric_type` |  | isolated_registry |
| `test_get_or_create_uses_custom_labels_from_config` |  | mock_config_store, isolated_registry |
| `test_get_or_create_uses_custom_buckets_from_config` |  | mock_config_store, isolated_registry |
| `test_get_or_create_handles_unregister_failure` |  | isolated_registry |
| `test_concurrent_metric_creation` |  | isolated_registry |
| `test_metric_usage_integration` |  | isolated_registry |
| `test_histogram_with_observations` |  | isolated_registry |
| `test_gauge_set_and_inc_dec` |  | isolated_registry |
| `test_metric_with_no_labels` |  | isolated_registry |
| `test_invalid_metric_type` |  | isolated_registry |
| `test_metric_documentation_preserved` |  | isolated_registry |
| `test_get_or_create_with_config_override` |  | mock_config_store, isolated_registry |
| `test_metric_labels` |  | isolated_registry |
| `test_concurrent_metric_updates` | ✓ | isolated_registry |

---

## tests/test_arbiter_arbiter_growth_models.py
**Lines:** 502

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `engine` |  |  |
| `session` |  | engine |
| `test_growth_event_valid` |  |  |
| `test_growth_event_invalid_type_empty` |  |  |
| `test_growth_event_invalid_type_whitespace` |  |  |
| `test_growth_event_missing_required_fields` |  |  |
| `test_growth_event_serialization` |  |  |
| `test_growth_event_deserialization` |  |  |
| `test_arbiter_state_valid` |  |  |
| `test_arbiter_state_level_min` |  |  |
| `test_arbiter_state_event_offset_int` |  |  |
| `test_arbiter_state_set_skill_score` |  |  |
| `test_arbiter_state_serialization` |  |  |
| `test_growth_snapshot_columns` |  | engine |
| `test_growth_event_record_columns` |  | engine |
| `test_audit_log_columns` |  | engine |
| `test_create_growth_snapshot` |  | session |
| `test_create_growth_event_record` |  | session |
| `test_create_audit_log` |  | session |
| `test_arbiter_state_invalid_skill_score` |  |  |
| `test_arbiter_state_large_skills` |  |  |
| `test_schema_version_defaults` |  | engine |
| `test_growth_event_with_large_metadata` |  |  |
| `test_timestamp_handling` |  | session |
| `test_cascade_delete_behavior` |  | session |
| `test_arbiter_state_skill_clamping` |  |  |
| `test_growth_event_record_encryption` |  | session |

---

## tests/test_arbiter_arbiter_growth_plugins.py
**Lines:** 337

### `TestLoggingPlugin` (PluginHook)
**Attributes:** __test__

| Method | Async | Args |
|--------|-------|------|
| `on_start` | ✓ | self, arbiter_name |
| `on_stop` | ✓ | self, arbiter_name |
| `on_error` | ✓ | self, arbiter_name, error |
| `on_growth_event` | ✓ | self, event, state |

### `TestAsyncMockPlugin` (PluginHook)
**Attributes:** __test__

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `on_growth_event` | ✓ | self, event, state |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `caplog` |  | caplog |
| `growth_event` |  |  |
| `arbiter_state` |  |  |
| `test_plugin_hook_is_abstract` |  |  |
| `test_plugin_hook_must_implement_on_growth_event` |  |  |
| `test_logging_plugin_on_start` | ✓ | caplog |
| `test_logging_plugin_on_stop` | ✓ | caplog |
| `test_logging_plugin_on_error` | ✓ | caplog |
| `test_logging_plugin_on_growth_event` | ✓ | growth_event, arbiter_state, caplog |
| `test_mock_plugin_on_start` | ✓ |  |
| `test_mock_plugin_on_growth_event` | ✓ | growth_event, arbiter_state |
| `test_multiple_plugins_execution` | ✓ | growth_event, arbiter_state |
| `test_plugin_error_handling` | ✓ |  |
| `test_concurrent_plugin_execution` | ✓ | growth_event, arbiter_state |
| `test_example_plugin_from_docstring` | ✓ | caplog, growth_event, arbiter_state |
| `test_multiple_hooks_execution` | ✓ | growth_event, arbiter_state, caplog |
| `test_plugin_error_propagation` | ✓ |  |

---

## tests/test_arbiter_arbiter_growth_storage_backends.py
**Lines:** 579

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_config_store` |  |  |
| `sqlite_backend` | ✓ | mock_config_store |
| `mock_redis_client` |  |  |
| `redis_backend` | ✓ | mock_config_store, mock_redis_client |
| `mock_kafka_producer` |  |  |
| `kafka_backend` | ✓ | mock_config_store, mock_kafka_producer |
| `test_sqlite_load_snapshot_returns_none_if_not_found` | ✓ | sqlite_backend |
| `test_sqlite_save_and_load_snapshot` | ✓ | sqlite_backend |
| `test_sqlite_save_and_load_events` | ✓ | sqlite_backend |
| `test_sqlite_audit_log_chaining` | ✓ | sqlite_backend |
| `test_sqlite_handles_decryption_failure` | ✓ | sqlite_backend |
| `test_redis_load_snapshot_returns_none_if_not_found` | ✓ | redis_backend, mock_redis_client |
| `test_redis_save_and_load_snapshot` | ✓ | redis_backend, mock_redis_client |
| `test_redis_circuit_breaker_opens` | ✓ | redis_backend, caplog |
| `test_kafka_save_snapshot` | ✓ | kafka_backend, mock_kafka_producer |
| `test_storage_backend_factory_sqlite` |  | mock_config_store |
| `test_storage_backend_factory_redis` |  | mock_config_store |
| `test_storage_backend_factory_kafka` |  | mock_config_store |
| `test_storage_backend_factory_unknown` |  | mock_config_store |
| `test_sqlite_batch_operations` | ✓ | sqlite_backend |
| `test_redis_stream_operations` | ✓ | redis_backend, mock_redis_client |
| `test_storage_backend_error_handling` | ✓ | sqlite_backend, caplog |
| `test_encryption_decryption` | ✓ | sqlite_backend |
| `test_concurrent_writes` | ✓ | sqlite_backend |
| `test_redis_consumer_group` | ✓ | redis_backend, mock_redis_client |
| `test_kafka_offset_management` | ✓ | kafka_backend, mock_kafka_producer |

---

## tests/test_arbiter_arena.py
**Lines:** 280

### `TestArbiterArena`

| Method | Async | Args |
|--------|-------|------|
| `test_arena_initialization` |  | self, mock_config, mock_db_engine |
| `test_arena_context_manager` | ✓ | self, mock_config, mock_db_engine |
| `test_register_and_remove_arbiter` | ✓ | self, mock_config, mock_db_engine |
| `test_get_random_arbiter` | ✓ | self, mock_config, mock_db_engine |
| `test_webhook_sending` | ✓ | self, mock_config, mock_db_engine |
| `test_setup_routes` |  | self, mock_config, mock_db_engine |

### `TestExtractSqliteDbFile`

| Method | Async | Args |
|--------|-------|------|
| `test_relative_path_with_dot_slash` |  | self |
| `test_relative_path_without_dot_slash` |  | self |
| `test_absolute_path` |  | self |
| `test_absolute_path_nested` |  | self |
| `test_sqlite_aiosqlite_dialect` |  | self |
| `test_non_sqlite_url_unchanged` |  | self |
| `test_mysql_url_unchanged` |  | self |
| `test_relative_path_in_subdirectory` |  | self |
| `test_simple_filename` |  | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `temp_dir` |  |  |
| `mock_config` |  | temp_dir |
| `mock_db_engine` |  |  |

---

## tests/test_arbiter_array_backend.py
**Lines:** 128

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_reload_backend_with_real_aiofiles` |  |  |
| `ensure_real_aiofiles` |  |  |
| `test_fixture_loads_successfully` |  |  |

**Constants:** `_backend`, `ConcreteArrayBackend`, `ArrayBackendError`, `ArraySizeLimitError`, `StorageError`, `ArrayMeta`

---

## tests/test_arbiter_audit_log.py
**Lines:** 868

### `TestAuditLoggerConfig`

| Method | Async | Args |
|--------|-------|------|
| `test_valid_config_initialization` |  | self, temp_log_dir |
| `test_invalid_rotation_type` |  | self, temp_log_dir |
| `test_invalid_compression_type` |  | self, temp_log_dir |
| `test_negative_retention_count` |  | self, temp_log_dir |
| `test_encryption_without_key_generates_key` |  | self, temp_log_dir |

### `TestLoggerInitialization`

| Method | Async | Args |
|--------|-------|------|
| `test_singleton_pattern` |  | self, basic_config |
| `test_logger_creates_log_directory` |  | self, temp_log_dir |
| `test_file_handler_setup` |  | self, mock_handler, basic_config |

### `TestBasicLogging`

| Method | Async | Args |
|--------|-------|------|
| `test_log_single_event` | ✓ | self, logger_instance |
| `test_log_multiple_events` | ✓ | self, logger_instance |
| `test_critical_event_triggers_immediate_flush` | ✓ | self, logger_instance |
| `test_batch_processing` | ✓ | self, logger_instance |
| `test_invalid_event_type` | ✓ | self, logger_instance |

### `TestEncryption`

| Method | Async | Args |
|--------|-------|------|
| `test_encrypt_sensitive_fields` | ✓ | self, encrypted_logger |
| `test_decrypt_sensitive_fields` | ✓ | self, encrypted_logger |

### `TestHashChainIntegrity`

| Method | Async | Args |
|--------|-------|------|
| `test_hash_calculation` |  | self |
| `test_hash_chain_different_with_different_data` |  | self |
| `test_verify_log_integrity_valid` | ✓ | self, logger_instance, temp_log_dir |
| `test_verify_log_integrity_tampered` | ✓ | self, logger_instance, temp_log_dir |

### `TestDataSanitization`

| Method | Async | Args |
|--------|-------|------|
| `test_sanitize_dict_normal` |  | self |
| `test_sanitize_dict_truncates_large_strings` |  | self |
| `test_sanitize_dict_raises_on_oversized` |  | self |

### `TestAuditTrailLoading`

| Method | Async | Args |
|--------|-------|------|
| `test_load_audit_trail_basic` | ✓ | self, logger_instance, temp_log_dir |
| `test_load_audit_trail_with_event_filter` | ✓ | self, logger_instance, temp_log_dir |
| `test_load_audit_trail_with_user_filter` | ✓ | self, logger_instance, temp_log_dir |
| `test_load_audit_trail_with_time_filter` | ✓ | self, logger_instance, temp_log_dir |

### `TestDLTIntegration`

| Method | Async | Args |
|--------|-------|------|
| `test_dlt_anchoring_critical_events` | ✓ | self, logger_instance |
| `test_dlt_retry_on_failure` | ✓ | self, logger_instance |

### `TestRotationAndCompression`

| Method | Async | Args |
|--------|-------|------|
| `test_sized_rotating_handler_size_check` |  | self, temp_log_dir |
| `test_compression_on_rotation` |  | self, mock_remove, mock_exists, mock_open, mock_gzip, ...+1 |

### `TestMetrics`

| Method | Async | Args |
|--------|-------|------|
| `test_metrics_collection` | ✓ | self, logger_instance |

### `TestErrorHandling`

| Method | Async | Args |
|--------|-------|------|
| `test_alert_callback_on_error` | ✓ | self, logger_instance |
| `test_malformed_json_handling` | ✓ | self, logger_instance, temp_log_dir |

### `TestAsyncOperations`

| Method | Async | Args |
|--------|-------|------|
| `test_concurrent_logging` | ✓ | self, logger_instance |
| `test_batch_timeout_processing` | ✓ | self, logger_instance |

### `TestGlobalAPI`

| Method | Async | Args |
|--------|-------|------|
| `test_global_log_event` | ✓ | self, basic_config |
| `test_global_verify_integrity` | ✓ | self, basic_config |

### `TestIntegration`

| Method | Async | Args |
|--------|-------|------|
| `test_end_to_end_logging_and_verification` | ✓ | self, temp_log_dir |
| `test_rotation_and_compression_integration` | ✓ | self, temp_log_dir |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_create_stub_module` |  | name |
| `_restore_original_modules` |  |  |
| `cleanup_mocked_modules` |  |  |
| `temp_log_dir` |  |  |
| `basic_config` |  | temp_log_dir |
| `encrypted_config` |  | temp_log_dir |
| `logger_instance` | ✓ | basic_config |
| `encrypted_logger` | ✓ | encrypted_config |

**Constants:** `_ORIGINAL_MODULES`, `_MODULES_TO_MOCK`, `_sfe_dir`

---

## tests/test_arbiter_bug_manager_audit_log.py
**Lines:** 293

### `TestInitializationAndShutdown`

| Method | Async | Args |
|--------|-------|------|
| `test_initialization_success` | ✓ | self, manager |
| `test_initialization_disabled` | ✓ | self, mock_settings |
| `test_shutdown_cleans_up_resources` | ✓ | self, manager |

### `TestAuditingAndFlushing`

| Method | Async | Args |
|--------|-------|------|
| `test_audit_adds_to_buffer` | ✓ | self, manager |
| `test_periodic_flush_works` | ✓ | self, manager, mock_settings |
| `test_flush_on_full_buffer` | ✓ | self, manager |
| `test_hash_chaining_integrity` | ✓ | self, manager |

### `TestFileOperations`

| Method | Async | Args |
|--------|-------|------|
| `test_log_rotation_on_size_exceeded` | ✓ | self, mock_settings |
| `test_rotation_skips_on_low_disk_space` | ✓ | self, manager |

### `TestErrorHandling`

| Method | Async | Args |
|--------|-------|------|
| `test_write_failure_sends_to_dead_letter_queue` | ✓ | self, manager |
| `test_lock_exception_rebuffers_logs` | ✓ | self, manager |

### `TestRemoteIntegration`

| Method | Async | Args |
|--------|-------|------|
| `test_remote_send_success` | ✓ | self, mock_settings |
| `test_remote_send_http_error_sends_to_dlq` | ✓ | self, mock_settings |
| `test_remote_send_network_error_sends_to_dlq` | ✓ | self, mock_settings |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_settings` |  | tmp_path |
| `manager` | ✓ | mock_settings |

---

## tests/test_arbiter_bug_manager_bug_manager.py
**Lines:** 428

### `TestRateLimiter`

| Method | Async | Args |
|--------|-------|------|
| `test_allows_calls_below_limit` | ✓ | self |
| `test_raises_when_exceeded` | ✓ | self |
| `test_time_window_resets_limit` | ✓ | self |

### `TestBugManager`

| Method | Async | Args |
|--------|-------|------|
| `test_initialization_and_shutdown` | ✓ | self, manager, mock_dependencies |
| `test_report_happy_path_with_notification` | ✓ | self, manager, mock_dependencies |
| `test_report_autofix_success_skips_notification` | ✓ | self, manager, mock_dependencies |
| `test_report_autofix_critical_still_sends_notification` | ✓ | self, manager, mock_dependencies |
| `test_report_handles_internal_failure` | ✓ | self, manager, mock_dependencies, mock_settings |
| `test_report_is_rate_limited` | ✓ | self, manager, mock_settings |

### `TestBugManagerArena`

| Method | Async | Args |
|--------|-------|------|
| `test_report_with_running_loop` | ✓ | self |
| `test_report_with_no_loop` | ✓ | self |

### `TestBugSignatureGeneration`

| Method | Async | Args |
|--------|-------|------|
| `manager_for_signature` | ✓ | self, mock_dependencies |
| `test_signature_with_500_status_code_in_error_data` |  | self, manager_for_signature |
| `test_signature_with_500_in_message` |  | self, manager_for_signature |
| `test_signature_with_http_status_in_custom_details` |  | self, manager_for_signature |
| `test_signature_with_502_status_code` |  | self, manager_for_signature |
| `test_signature_without_http_error_has_no_prefix` |  | self, manager_for_signature |
| `test_signature_uniqueness_preserved` |  | self, manager_for_signature |
| `test_signature_no_false_positive_for_similar_numbers` |  | self, manager_for_signature |
| `test_signature_no_prefix_for_4xx_status_codes` |  | self, manager_for_signature |
| `test_signature_true_positive_for_http_500` |  | self, manager_for_signature |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_settings` |  |  |
| `mock_dependencies` | ✓ |  |
| `manager` | ✓ | mock_settings, mock_dependencies |

---

## tests/test_arbiter_bug_manager_bug_manager_e2e.py
**Lines:** 257

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `clean_action_registry` |  |  |
| `clean_bug_fixer_registry` |  |  |
| `test_e2e_bug_report_with_successful_fix` | ✓ | tmp_path |
| `test_e2e_bug_report_with_failed_fix_and_notifications` | ✓ | tmp_path |
| `test_e2e_rate_limiting` | ✓ | tmp_path |

---

## tests/test_arbiter_bug_manager_notifications.py
**Lines:** 260

### `TestCircuitBreaker`

| Method | Async | Args |
|--------|-------|------|
| `test_state_transitions` | ✓ | self |

### `TestRateLimiter`

| Method | Async | Args |
|--------|-------|------|
| `test_in_memory_rate_limiting` | ✓ | self |

### `TestNotificationService`

| Method | Async | Args |
|--------|-------|------|
| `test_notify_slack_success` | ✓ | self, notification_service, mock_aiohttp_session |
| `test_notify_slack_api_error` | ✓ | self, notification_service, mock_aiohttp_session |
| `test_notify_email_with_tenacity_retry` | ✓ | self, notification_service |
| `test_escalation_after_threshold` | ✓ | self, notification_service, mock_settings |
| `test_notify_batch_concurrently` | ✓ | self, notification_service |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_settings` |  |  |
| `mock_aiohttp_session` |  |  |
| `notification_service` | ✓ | mock_settings, mock_aiohttp_session |

---

## tests/test_arbiter_bug_manager_remediations.py
**Lines:** 381

### `TestMLRemediationModel`

| Method | Async | Args |
|--------|-------|------|
| `test_predict_success` | ✓ | self, ml_model, bug_details, mock_aiohttp_session |
| `test_predict_api_error_raises_custom_exception` | ✓ | self, ml_model, bug_details, mock_aiohttp_session |
| `test_record_feedback_success` | ✓ | self, ml_model, bug_details, mock_aiohttp_session |

### `TestRemediationStep`

| Method | Async | Args |
|--------|-------|------|
| `test_execute_success` | ✓ | self, bug_details |
| `test_execute_with_retries_on_failure` | ✓ | self, bug_details |
| `test_execute_exhausts_retries` | ✓ | self, bug_details |
| `test_execute_exception_exhausts_retries` | ✓ | self, bug_details |
| `test_precondition_skip` | ✓ | self, bug_details |

### `TestRemediationPlaybook`

| Method | Async | Args |
|--------|-------|------|
| `test_successful_run` | ✓ | self, bug_details |
| `test_run_with_step_failure` | ✓ | self, bug_details |

### `TestBugFixerRegistry`

| Method | Async | Args |
|--------|-------|------|
| `clean_registry` |  | self |
| `test_rule_based_selection_priority` | ✓ | self, bug_details |
| `test_ml_selection_override` | ✓ | self, bug_details, ml_model |
| `test_feedback_is_skipped_when_no_playbook_found` | ✓ | self, bug_details, ml_model |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `clean_action_registry` |  |  |
| `mock_aiohttp_session` |  |  |
| `ml_model` | ✓ | mock_aiohttp_session |
| `bug_details` |  |  |

---

## tests/test_arbiter_bug_manager_utils.py
**Lines:** 261

### `TestSecretStr`

| Method | Async | Args |
|--------|-------|------|
| `test_creation_and_retrieval` |  | self |
| `test_redaction_on_str_and_repr` |  | self |
| `test_coerces_non_string_input` |  | self |

### `TestParseBoolEnv`

| Method | Async | Args |
|--------|-------|------|
| `test_parses_true_values` |  | self, monkeypatch, value |
| `test_parses_false_values` |  | self, monkeypatch, value |
| `test_uses_default_when_var_not_set` |  | self |

### `TestRedactPII`

| Method | Async | Args |
|--------|-------|------|
| `test_redacts_by_sensitive_keyword` |  | self |
| `test_redacts_by_pattern` |  | self |
| `test_recursive_redaction` |  | self |
| `test_ignores_non_sensitive_data` |  | self |

### `TestSettingsValidation`

| Method | Async | Args |
|--------|-------|------|
| `test_apply_validation_succeeds_on_valid_settings` |  | self, valid_settings |
| `test_apply_validation_fails_on_missing_field` |  | self, valid_settings |
| `test_apply_validation_fails_on_incorrect_type` |  | self, valid_settings |
| `test_apply_validation_handles_optional_fields` |  | self, valid_settings |

### `TestValidateInputDetails`

| Method | Async | Args |
|--------|-------|------|
| `test_valid_dict_is_sanitized` |  | self |
| `test_none_returns_empty_dict` |  | self |
| `test_invalid_type_raises_error` |  | self, mock_logger |
| `test_max_depth_exceeded_raises_error` |  | self |

### `TestErrorClasses`

| Method | Async | Args |
|--------|-------|------|
| `test_bug_manager_error_properties` |  | self |
| `test_subclass_properties` |  | self |

### `TestSeverityEnum`

| Method | Async | Args |
|--------|-------|------|
| `test_from_string_valid` |  | self, input_str, expected |
| `test_from_string_invalid_defaults_and_warns` |  | self, mock_logger |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_logger` |  |  |
| `valid_settings` |  |  |

---

## tests/test_arbiter_codebase_analyzer.py
**Lines:** 543

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `ensure_real_aiofiles_for_codebase_analyzer` |  |  |
| `temp_dir` |  | tmp_path |
| `mock_logger` |  |  |
| `mock_config_file` |  | temp_dir |
| `mock_metrics` |  |  |
| `test_conditional_imports` |  | dep, flag, caplog |
| `test_codebase_analyzer_init` |  | temp_dir, mock_config_file |
| `test_load_config` |  | mock_config_file |
| `test_load_config_no_file` |  |  |
| `test_discover_files` |  | temp_dir |
| `test_analyze_file_radon` | ✓ | temp_dir |
| `test_analyze_file_mypy` | ✓ | temp_dir |
| `test_analyze_file_error` | ✓ | temp_dir, mock_logger |
| `test_generate_report_markdown` | ✓ | temp_dir |
| `test_generate_report_json` | ✓ | temp_dir |
| `test_generate_report_junit` | ✓ | temp_dir |
| `test_audit_repair_tools` | ✓ |  |
| `test_analyze_and_propose` | ✓ | temp_dir |
| `test_generate_junit_xml_report` |  |  |
| `test_cli_scan` |  |  |
| `test_cli_tools` |  |  |
| `test_discover_files_thread_safety` |  | temp_dir |
| `test_filter_baseline` |  | temp_dir |
| `test_idempotent_metric_registration` |  |  |
| `test_create_dummy_metric` |  |  |
| `test_get_or_create_metric` |  |  |

**Constants:** `tracer`

---

## tests/test_arbiter_config.py
**Lines:** 176

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `clear_globals` |  |  |
| `test_arbiter_config_init` |  |  |
| `test_initialize_singleton` |  | mock_errors, mock_access |
| `test_load_from_env` |  |  |
| `test_email_recipients_list` |  |  |
| `test_decrypt_sensitive_fields` |  |  |
| `test_to_dict` |  | mock_access |
| `test_required_fields_validation` |  |  |
| `test_config_singleton_thread_safe` |  | mock_errors, mock_access |
| `test_load_from_file_invalid` |  |  |
| `test_encrypt_sensitive_fields` |  |  |
| `test_invalid_email_recipients_list` |  |  |
| `test_get_or_create_counter` |  |  |
| `test_get_or_create_gauge` |  |  |
| `test_get_or_create_histogram` |  |  |

---

## tests/test_arbiter_constitution.py
**Lines:** 156

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_import_constitution` |  |  |
| `test_init_and_parse_counts` |  |  |
| `test_getters_match_rules` |  |  |
| `test_str_and_repr` |  |  |
| `test_logger_called_on_init` |  |  |
| `test_malformed_text_graceful` |  |  |
| `test_empty_text_graceful` |  |  |
| `test_semantic_assertions_in_text` |  |  |

**Constants:** `mod`, `ArbiterConstitution`, `ARB_CONSTITUTION`, `logger`

---

## tests/test_arbiter_critical_fixes.py
**Lines:** 543

### `TestExplainableReasonerExport`

| Method | Async | Args |
|--------|-------|------|
| `test_explainable_reasoner_import` |  | self |

### `TestPermissionManagerSecurity`

| Method | Async | Args |
|--------|-------|------|
| `test_permission_manager_defaults_deny` |  | self |

### `TestVaultSecretManager`

| Method | Async | Args |
|--------|-------|------|
| `test_vault_fallback_mode` | ✓ | self |
| `test_vault_production_mode_enforcement` | ✓ | self |

### `TestBiasDetection`

| Method | Async | Args |
|--------|-------|------|
| `test_bias_detection_implemented` |  | self |

### `TestSecretScrubbing`

| Method | Async | Args |
|--------|-------|------|
| `test_secret_scrubbing_basic` |  | self |

### `TestDummyDBClientPersistence`

| Method | Async | Args |
|--------|-------|------|
| `test_dummy_db_persistence` | ✓ | self |

### `TestProductionModeEnforcement`

| Method | Async | Args |
|--------|-------|------|
| `test_production_mode_environment` |  | self |

### `TestMetricsFallback`

| Method | Async | Args |
|--------|-------|------|
| `test_metrics_file_logging` |  | self |

### `TestTraceFallback`

| Method | Async | Args |
|--------|-------|------|
| `test_trace_file_logging` |  | self |

### `TestAuditLoggerHealthCheck`

| Method | Async | Args |
|--------|-------|------|
| `test_health_check_structure` |  | self |

### `TestSeverityEnumConsolidation`

| Method | Async | Args |
|--------|-------|------|
| `test_severity_enum_exists` |  | self |
| `test_severity_from_string` |  | self |

### `TestThreadingLockFix`

| Method | Async | Args |
|--------|-------|------|
| `test_plugin_registry_uses_threading_lock` |  | self |

### `TestRedisStreamFix`

| Method | Async | Args |
|--------|-------|------|
| `test_stream_id_increment_logic` |  | self |

### `TestDepthLimitFix`

| Method | Async | Args |
|--------|-------|------|
| `test_find_path_depth_check` | ✓ | self |

### `TestVideoFileClipFix`

| Method | Async | Args |
|--------|-------|------|
| `test_video_processing_uses_temp_file` |  | self |

### `TestRedisClientFix`

| Method | Async | Args |
|--------|-------|------|
| `test_redis_client_initialization` | ✓ | self |

### `TestRaceConditionFix`

| Method | Async | Args |
|--------|-------|------|
| `test_deep_copy_prevents_race_condition` | ✓ | self |

### `TestThreadingLockFix`

| Method | Async | Args |
|--------|-------|------|
| `test_plugin_registry_uses_threading_lock` |  | self |

### `TestRedisStreamFix`

| Method | Async | Args |
|--------|-------|------|
| `test_stream_id_increment_logic` |  | self |

### `TestDepthLimitFix`

| Method | Async | Args |
|--------|-------|------|
| `test_find_path_depth_check` | ✓ | self |

### `TestVideoFileClipFix`

| Method | Async | Args |
|--------|-------|------|
| `test_video_processing_uses_temp_file` |  | self |

### `TestRedisClientFix`

| Method | Async | Args |
|--------|-------|------|
| `test_redis_client_initialization` | ✓ | self |

### `TestRaceConditionFix`

| Method | Async | Args |
|--------|-------|------|
| `test_deep_copy_prevents_race_condition` | ✓ | self |

---

## tests/test_arbiter_decision_optimizer.py
**Lines:** 258

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_create_stub_module` |  | name |
| `_restore_original_modules` |  |  |
| `cleanup_mocked_modules` |  |  |
| `mock_dependencies` |  |  |
| `optimizer` |  | mock_dependencies |
| `test_initialization` |  | optimizer |
| `test_task_creation` |  |  |
| `test_agent_creation` |  |  |
| `test_safe_serialize` |  |  |
| `test_prioritize_and_allocate` | ✓ | optimizer |
| `test_prioritize_tasks_simple` | ✓ | optimizer |
| `test_allocate_resources_simple` | ✓ | optimizer |
| `test_compute_trust_score` | ✓ | optimizer |
| `test_process_remediation_low_risk` | ✓ | optimizer |
| `test_get_metrics` | ✓ | optimizer |
| `test_coordinate_arbiters_basic` | ✓ | optimizer |

**Constants:** `pytestmark`, `_ORIGINAL_MODULES`, `_MOCKED_MODULE_NAMES`

---

## tests/test_arbiter_explainable_reasoner_adapters.py
**Lines:** 490

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_httpx_client` |  |  |
| `mock_sensitive_value` |  |  |
| `dummy_multimodal_data` |  |  |
| `mock_metrics` |  |  |
| `test_retry_success_on_first_try` | ✓ |  |
| `test_retry_success_after_failures` | ✓ |  |
| `test_retry_failure_after_max_retries` | ✓ |  |
| `test_retry_with_rate_limit` | ✓ |  |
| `test_llm_adapter_abstract_cannot_instantiate` |  |  |
| `test_factory_register_and_get` |  |  |
| `test_factory_missing_api_key` |  |  |
| `test_factory_unknown_adapter` |  |  |
| `test_openai_adapter_init` | ✓ | mock_sensitive_value |
| `test_openai_adapter_get_client` | ✓ | mock_sensitive_value |
| `test_openai_adapter_generate_success` | ✓ | mock_httpx_client, mock_sensitive_value, mock_metrics |
| `test_openai_adapter_generate_with_multimodal` | ✓ | mock_httpx_client, mock_sensitive_value, dummy_multimodal_data |
| `test_openai_adapter_generate_error_handling` | ✓ | mock_httpx_client, mock_sensitive_value, mock_metrics |
| `test_openai_adapter_stream_generate` | ✓ | mock_httpx_client, mock_sensitive_value |
| `test_openai_adapter_health_check` | ✓ | mock_httpx_client, mock_sensitive_value |
| `test_gemini_adapter_generate_success` | ✓ | mock_httpx_client, mock_sensitive_value, mock_metrics |
| `test_anthropic_adapter_generate_success` | ✓ | mock_httpx_client, mock_sensitive_value, mock_metrics |
| `test_adapter_rotate_key` | ✓ | mock_sensitive_value |
| `test_adapter_aclose` | ✓ | mock_httpx_client, mock_sensitive_value |
| `test_adapter_custom_base_url` | ✓ | mock_sensitive_value |
| `test_adapter_timeout_handling` | ✓ | mock_httpx_client, mock_sensitive_value |

**Constants:** `logger`

---

## tests/test_arbiter_explainable_reasoner_audit_ledger.py
**Lines:** 520

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_structlog` |  |  |
| `mock_httpx_client` |  |  |
| `mock_metrics` |  |  |
| `audit_client` |  | mock_structlog |
| `test_init_success` |  | mock_structlog |
| `test_init_invalid_url` |  |  |
| `test_init_https_enforcement` |  | url |
| `test_send_event_with_retries_success` | ✓ | audit_client, mock_httpx_client |
| `test_send_event_with_retries_failure_after_retries` | ✓ | audit_client, mock_httpx_client, mock_metrics |
| `test_send_event_with_retries_timeout` | ✓ | audit_client, mock_httpx_client |
| `test_send_event_with_retries_unexpected_error` | ✓ | audit_client, mock_httpx_client |
| `test_log_event_success` | ✓ | audit_client, mock_httpx_client, mock_structlog |
| `test_log_event_failure_returns_false` | ✓ | audit_client, mock_structlog |
| `test_log_event_unhandled_exception` | ✓ | audit_client, mock_structlog |
| `test_log_event_invalid_params` | ✓ |  |
| `test_log_batch_events_success` | ✓ | audit_client |
| `test_log_batch_events_partial_failure` | ✓ | audit_client |
| `test_log_batch_events_empty` | ✓ | audit_client |
| `test_health_check_success` | ✓ | audit_client, mock_httpx_client |
| `test_health_check_failure` | ✓ | audit_client, mock_httpx_client |
| `test_health_check_with_endpoint` | ✓ |  |
| `test_rotate_key` | ✓ | audit_client, mock_httpx_client |
| `test_rotate_key_invalid` | ✓ |  |
| `test_close_with_client` | ✓ | audit_client, mock_httpx_client |
| `test_close_without_client` | ✓ | audit_client |
| `test_init_default_values` |  |  |
| `test_log_event_with_pii_redaction` | ✓ | audit_client |
| `test_rate_limit_handling` | ✓ | audit_client, mock_httpx_client, mock_metrics |

**Constants:** `test_logger`

---

## tests/test_arbiter_explainable_reasoner_e2e_explainable_reasoner.py
**Lines:** 563

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_external_deps` |  |  |
| `prod_config` |  | tmp_path |
| `reasoner` | ✓ | prod_config |
| `plugin` | ✓ | prod_config |
| `test_e2e_full_lifecycle` | ✓ | reasoner |
| `test_e2e_batch_processing` | ✓ | reasoner |
| `test_e2e_error_handling_for_invalid_input` | ✓ | reasoner |
| `test_e2e_plugin_workflow` | ✓ | plugin |
| `test_e2e_plugin_rbac_and_admin_tasks` | ✓ | plugin |
| `test_e2e_history_pruning` | ✓ | reasoner |
| `test_e2e_performance_under_load` | ✓ | reasoner |
| `test_e2e_metrics_exposition` |  |  |
| `test_e2e_session_filtering` | ✓ | reasoner |

---

## tests/test_arbiter_explainable_reasoner_explainable_reasoner.py
**Lines:** 667

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_dependencies` |  |  |
| `mock_config` |  |  |
| `reasoner_instance` | ✓ | mock_config |
| `test_config_from_env` |  | monkeypatch |
| `test_config_validation_error` |  |  |
| `test_config_sensitive_redaction` |  |  |
| `test_init_success` | ✓ | mock_config |
| `test_init_with_invalid_jwt_secret` | ✓ |  |
| `test_explain_success` | ✓ | reasoner_instance |
| `test_reason_success` | ✓ | reasoner_instance |
| `test_batch_explain_success` | ✓ | reasoner_instance |
| `test_batch_explain_with_exceptions` | ✓ | reasoner_instance |
| `test_handle_request_invalid_input` | ✓ | reasoner_instance |
| `test_handle_request_context_too_large` | ✓ | reasoner_instance |
| `test_get_history` | ✓ | reasoner_instance |
| `test_clear_history` | ✓ | reasoner_instance |
| `test_health_check_healthy` | ✓ | reasoner_instance |
| `test_health_check_degraded_no_models` | ✓ | reasoner_instance |
| `test_shutdown_success` | ✓ | reasoner_instance |
| `test_plugin_initialize` | ✓ |  |
| `test_plugin_execute_explain` | ✓ |  |
| `test_plugin_execute_invalid_action` | ✓ |  |
| `test_plugin_execute_rbac_success` | ✓ |  |
| `test_plugin_execute_rbac_failure` | ✓ |  |
| `test_plugin_execute_rbac_invalid_token` | ✓ |  |

---

## tests/test_arbiter_explainable_reasoner_history_manager.py
**Lines:** 588

### `TestBaseHistoryManager`

| Method | Async | Args |
|--------|-------|------|
| `test_cannot_instantiate_abstract_class` |  | self |

### `TestSQLiteHistoryManager`

| Method | Async | Args |
|--------|-------|------|
| `test_init_db` | ✓ | self, temp_db_path, mock_audit_client, mock_metrics |
| `test_add_and_get_entry` | ✓ | self, sqlite_manager |
| `test_batch_operations` | ✓ | self, sqlite_manager |
| `test_max_size_enforcement` | ✓ | self, sqlite_manager |
| `test_session_filtering` | ✓ | self, sqlite_manager |
| `test_pruning` | ✓ | self, sqlite_manager |
| `test_clear_operations` | ✓ | self, sqlite_manager |
| `test_export` | ✓ | self, sqlite_manager |

### `TestPostgresHistoryManager`

| Method | Async | Args |
|--------|-------|------|
| `test_encryption` | ✓ | self, postgres_manager |
| `test_operations` | ✓ | self, postgres_manager |

### `TestRedisHistoryManager`

| Method | Async | Args |
|--------|-------|------|
| `test_sorted_set_operations` | ✓ | self, redis_manager |
| `test_operations` | ✓ | self, redis_manager |

### `TestCommonFunctionality`

| Method | Async | Args |
|--------|-------|------|
| `test_sensitive_data_detection_sqlite` | ✓ | self, sqlite_manager |
| `test_sensitive_data_detection_postgres` | ✓ | self, postgres_manager |
| `test_sensitive_data_detection_redis` | ✓ | self, redis_manager |
| `test_binary_data_detection_sqlite` | ✓ | self, sqlite_manager |
| `test_binary_data_detection_postgres` | ✓ | self, postgres_manager |
| `test_binary_data_detection_redis` | ✓ | self, redis_manager |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `temp_db_path` |  | tmp_path |
| `mock_audit_client` |  |  |
| `mock_metrics` |  |  |
| `sqlite_manager` | ✓ | temp_db_path, mock_audit_client, mock_metrics |
| `postgres_manager` | ✓ | mock_audit_client, mock_metrics |
| `redis_manager` | ✓ | mock_audit_client, mock_metrics |
| `create_test_entry` |  | response, session_id, timestamp |

---

## tests/test_arbiter_explainable_reasoner_metrics.py
**Lines:** 322

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_structlog` |  |  |
| `clean_registry` |  |  |
| `clean_metrics_cache` |  |  |
| `test_initialize_with_multiproc_success_real_dir` |  | mock_structlog |
| `test_initialize_with_multiproc_permission_error` |  | mock_structlog |
| `test_initialize_with_multiproc_mkdir_exception` |  | mock_structlog |
| `test_initialize_no_multiproc_dir` |  | mock_structlog |
| `test_get_or_create_metric_success` |  | metric_type, name, doc, labelnames, buckets, ...+2 |
| `test_get_or_create_metric_caching` |  |  |
| `test_get_or_create_metric_invalid_type` |  |  |
| `test_get_or_create_metric_reuse_existing` |  |  |
| `test_metrics_dict_behavior` |  |  |
| `test_get_metrics_content_success` |  | clean_registry |
| `test_get_metrics_content_failure` |  | mock_structlog |
| `test_metric_usage_produces_output` |  |  |
| `test_high_cardinality_metric` |  |  |
| `test_metrics_thread_safety` |  |  |
| `test_default_metrics_exist` |  |  |
| `test_duplicate_metric_handling` |  |  |

**Constants:** `test_logger`

---

## tests/test_arbiter_explainable_reasoner_prompt_strategies.py
**Lines:** 446

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_dependencies` |  |  |
| `mock_logger` |  |  |
| `dummy_context` |  |  |
| `dummy_multimodal` |  |  |
| `clean_factory` |  |  |
| `test_prompt_strategy_abstract` |  |  |
| `test_strategy_prompt_generation` | ✓ | strategy_class, expected_explain_contains, expected_reason_contains, mock_logger, dummy_context, ...+1 |
| `test_strategy_multimodal_context` | ✓ | mock_logger, dummy_context, dummy_multimodal |
| `test_strategy_with_history` | ✓ | mock_logger, dummy_context |
| `test_tracing_in_prompt_generation` | ✓ | mock_logger, mock_dependencies |
| `test_factory_register_strategy` |  | clean_factory, mock_logger |
| `test_factory_get_with_env_override` |  | clean_factory, monkeypatch, mock_logger |
| `test_factory_invalid_strategy` |  | clean_factory, mock_logger |
| `test_factory_list_strategies` |  | clean_factory |
| `test_factory_register_non_subclass` |  | clean_factory |
| `test_factory_re_registration_warning` |  | clean_factory, mock_dependencies |
| `test_prompt_with_empty_context_goal` | ✓ | mock_logger |
| `test_prompt_with_invalid_context_type` | ✓ | mock_logger |
| `test_structured_strategy_json_output` | ✓ | mock_logger, dummy_context |
| `test_structured_strategy_with_multimodal` | ✓ | mock_logger, dummy_context, dummy_multimodal |
| `test_truncate_context_function` |  |  |
| `test_prompt_size_limits` | ✓ | mock_logger |
| `test_custom_prompt_template` | ✓ | mock_logger |
| `test_error_handling_in_prompt_generation` | ✓ | mock_logger |
| `test_prompt_caching` | ✓ | mock_logger, dummy_context |
| `test_prompt_strategy_with_special_characters` | ✓ | mock_logger |

---

## tests/test_arbiter_explainable_reasoner_reasoner_errors.py
**Lines:** 343

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_logger` |  |  |
| `mock_sentry` |  |  |
| `test_reasoner_error_code_attributes` |  |  |
| `test_error_code_values` |  |  |
| `test_reasoner_error_init_and_log` |  | mock_logger, message, code, original_exception, extra_kwargs |
| `test_reasoner_error_sentry_capture` |  | mock_sentry, mock_logger |
| `test_sentry_not_captured_no_dsn` |  | mock_logger |
| `test_sentry_not_available` |  | mock_logger |
| `test_to_api_response_without_traceback` |  |  |
| `test_to_api_response_with_traceback` |  |  |
| `test_to_api_response_no_original_exc` |  |  |
| `test_to_json` |  |  |
| `test_error_wrapping_and_representation` |  |  |
| `test_error_inheritance` |  |  |
| `test_error_with_extra_kwargs` |  |  |
| `test_reasoner_error_no_message_or_code` |  |  |
| `test_reasoner_error_empty_message` |  |  |
| `test_reasoner_error_none_values` |  |  |
| `test_known_error_codes_exist` |  |  |

---

## tests/test_arbiter_explainable_reasoner_utils.py
**Lines:** 415

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_dependencies` |  |  |
| `mock_redis` |  |  |
| `dummy_multimodal_obj` |  |  |
| `test_sanitize_context_features` | ✓ | context, options, expected |
| `test_sanitize_context_max_depth` | ✓ |  |
| `test_sanitize_context_multimodal` | ✓ | dummy_multimodal_obj |
| `test_sanitize_context_errors` | ✓ | mock_dependencies |
| `test_sanitize_context_redaction_count` | ✓ | mock_dependencies |
| `test_sanitize_context_edge_cases` | ✓ |  |
| `test_sanitize_primitive_types` | ✓ |  |
| `test_simple_text_sanitize` |  | text, max_len, expected |
| `test_rule_based_fallback` |  |  |
| `test_format_multimodal_for_prompt` |  | dummy_multimodal_obj |
| `test_rate_limited_local_delay` | ✓ |  |
| `test_rate_limited_with_redis` | ✓ | mock_redis |
| `test_rate_limited_redis_delay` | ✓ |  |
| `test_rate_limited_no_redis` | ✓ |  |
| `test_rate_limited_error_handling` | ✓ |  |
| `test_format_multimodal_edge_cases` |  |  |
| `test_sanitize_context_with_none_values` | ✓ |  |
| `test_simple_text_sanitize_unicode` |  |  |
| `test_sanitize_context_performance` | ✓ |  |

---

## tests/test_arbiter_explorer.py
**Lines:** 280

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_logger` |  |  |
| `mock_log_db` |  |  |
| `explorer` |  | mock_log_db |
| `test_mock_log_db_init` |  | mock_logger |
| `test_mock_log_db_save` | ✓ | mock_log_db |
| `test_mock_log_db_get_found` | ✓ | mock_log_db |
| `test_mock_log_db_get_not_found` | ✓ | mock_log_db, mock_logger |
| `test_mock_log_db_find` | ✓ | mock_log_db |
| `test_mock_log_db_thread_safety` |  | mock_log_db |
| `test_arbiter_explorer_init` |  | explorer |
| `test_run_ab_test_success` | ✓ | explorer |
| `test_run_ab_test_failure` | ✓ | explorer, mock_logger |
| `test_run_evolutionary_experiment_success` | ✓ | explorer |
| `test_run_evolutionary_experiment_failure` | ✓ | explorer |
| `test_run_experiment` | ✓ | explorer |
| `test_log_experiment` | ✓ | explorer |
| `test_calculate_metrics` |  | explorer |
| `test_compare_variants` |  | explorer |
| `test_ab_test_zero_runs` | ✓ | explorer |
| `test_calculate_metrics_non_numeric` | ✓ | explorer |
| `test_experiment_id_unique` | ✓ | explorer |
| `test_concurrent_experiments` | ✓ | explorer |
| `test_logging_failure` | ✓ | mock_save, explorer, mock_logger |

---

## tests/test_arbiter_feedback.py
**Lines:** 326

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_logger` |  |  |
| `clear_registry` |  |  |
| `test_conditional_imports` |  | caplog |
| `test_get_or_create_metric_thread_safe` |  |  |
| `test_init_default_sqlite` |  | mock_sqlite_class |
| `test_init_with_sqlite_client` |  |  |
| `test_init_with_postgres_url` |  | mock_pg_class |
| `test_init_production_no_db_url` |  |  |
| `test_record_metric_success` | ✓ | mock_logger |
| `test_record_metric_invalid_name` | ✓ | mock_logger |
| `test_record_metric_invalid_value` | ✓ | mock_logger |
| `test_log_error` | ✓ | mock_logger |
| `test_add_user_feedback_approval` | ✓ |  |
| `test_add_user_feedback_denial` | ✓ |  |
| `test_get_summary` | ✓ |  |
| `test_get_pending_approvals` | ✓ |  |
| `test_get_approval_stats` | ✓ |  |
| `test_start_async_services` | ✓ |  |
| `test_stop_async_services` | ✓ | mock_logger |
| `test_record_metric_thread_safety` | ✓ |  |

---

## tests/test_arbiter_file_watcher.py
**Lines:** 432

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `temp_dir` |  | tmp_path |
| `mock_yaml_config` |  |  |
| `mock_env` |  | monkeypatch |
| `valid_config` |  |  |
| `test_load_config_with_env` |  | mock_yaml_config, temp_dir, mock_env |
| `test_load_config_no_file` |  |  |
| `test_load_config_invalid_yaml` |  | temp_dir |
| `test_send_email_alert` | ✓ | valid_config |
| `test_send_email_alert_failure` | ✓ | valid_config, caplog |
| `test_send_slack_alert` | ✓ |  |
| `test_send_pagerduty_alert` | ✓ |  |
| `test_summarize_code_changes` | ✓ |  |
| `test_summarize_code_changes_no_llm` | ✓ | caplog |
| `test_compare_diffs` |  |  |
| `test_deploy_code_success` | ✓ |  |
| `test_deploy_code_failure` | ✓ |  |
| `test_notify_changes` | ✓ | valid_config |
| `test_process_file` | ✓ | valid_config, temp_dir |
| `test_code_change_handler_on_modified` | ✓ |  |
| `test_metrics_health_server` | ✓ | valid_config |
| `test_cli_run` |  | temp_dir, mock_yaml_config |
| `test_cli_batch` |  | temp_dir, mock_yaml_config |

---

## tests/test_arbiter_growth.py
**Lines:** 961

### `MockCircuitBreaker`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `__aenter__` | ✓ | self |
| `__aexit__` | ✓ | self |

### `MockStorageBackend`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, initial_state |
| `start` | ✓ | self |
| `stop` | ✓ | self |
| `ping` | ✓ | self |
| `load` | ✓ | self, arbiter_id |
| `save` | ✓ | self, arbiter_id, data |
| `save_event` | ✓ | self, arbiter_id, event |
| `load_events` | ✓ | self, arbiter_id, from_offset |
| `save_audit_log` | ✓ | self, arbiter_id, operation, details, previous_hash |
| `get_last_audit_hash` | ✓ | self, arbiter_id |
| `load_all_audit_logs` | ✓ | self, arbiter_id |

### `MockIdempotencyStore`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `start` | ✓ | self |
| `stop` | ✓ | self |
| `ping` | ✓ | self |
| `check_and_set` | ✓ | self, key, ttl |
| `remember` | ✓ | self, key, ttl |

### `MockConfigStore`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, overrides |
| `get_config` | ✓ | self, key |
| `ping` | ✓ | self |

### `MockKnowledgeGraph`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `add_fact` | ✓ | self, fact_data |

### `MockFeedbackManager`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `record_feedback` | ✓ | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `create_module_stub` |  | name, attributes |
| `_restore_original_modules` |  |  |
| `cleanup_mocked_modules` |  |  |
| `wait_for_manager_ready` | ✓ | manager, timeout |
| `create_manager_with_proper_breakers` |  |  |
| `basic_manager` | ✓ |  |
| `manager_with_state` | ✓ |  |
| `test_module_imports` |  |  |
| `test_manager_lifecycle` | ✓ |  |
| `test_skill_acquisition` | ✓ | basic_manager |
| `test_skill_improvement` | ✓ | manager_with_state |
| `test_level_up` | ✓ | manager_with_state |
| `test_experience_gain` | ✓ | basic_manager |
| `test_user_preference_update` | ✓ | basic_manager |
| `test_custom_growth_event` | ✓ | basic_manager |
| `test_operation_queue_behavior` | ✓ |  |
| `test_rate_limiting` | ✓ |  |
| `test_event_persistence_and_replay` | ✓ |  |
| `test_snapshot_creation` | ✓ |  |
| `test_audit_logging` | ✓ |  |
| `test_audit_chain_validation_detects_tampering` | ✓ |  |
| `test_idempotency` | ✓ |  |
| `test_concurrent_operations` | ✓ |  |
| `test_high_event_volume` | ✓ |  |

**Constants:** `pytestmark`, `_ORIGINAL_MODULES`, `_MOCKED_MODULE_NAMES`, `current_dir`, `arbiter_dir`, `arbiter_growth_file`, `spec`, `arbiter_growth`, `ArbiterGrowthManager`, `ArbiterGrowthError`, `OperationQueueFullError`, `RateLimitError`, `CircuitBreakerOpenError`, `AuditChainTamperedError`, `ConfigStore`, ...+4 more

---

## tests/test_arbiter_human_loop.py
**Lines:** 490

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `default_config` |  |  |
| `production_config` |  |  |
| `hil` |  | default_config |
| `mock_db_client` |  |  |
| `feedback_manager` |  | mock_db_client |
| `test_config_validation_production_requires_database` |  |  |
| `test_config_validation_production_email_requirements` |  |  |
| `test_human_in_loop_init_development` |  | default_config |
| `test_human_in_loop_init_production` |  | mock_pg_class, production_config |
| `test_request_approval_valid_decision` | ✓ | hil |
| `test_request_approval_invalid_schema` | ✓ | hil |
| `test_request_approval_timeout` | ✓ | hil |
| `test_send_email_notification` | ✓ | hil |
| `test_send_slack_notification` | ✓ | hil |
| `test_websocket_notification` | ✓ | hil |
| `test_receive_human_feedback_valid` | ✓ | hil |
| `test_receive_human_feedback_invalid_signature` | ✓ | hil |
| `test_feedback_manager_log_approval_request` | ✓ | feedback_manager |
| `test_feedback_manager_log_approval_response` | ✓ | feedback_manager |
| `test_feedback_manager_record_metric` | ✓ | feedback_manager |
| `test_mock_user_approval` | ✓ | hil |
| `test_concurrent_approvals` | ✓ | hil |
| `test_audit_hook_called` | ✓ | default_config |
| `test_error_hook_called` | ✓ | default_config |
| `test_context_manager` | ✓ |  |
| `test_dummy_db_client` | ✓ |  |

---

## tests/test_arbiter_knowledge_graph_config.py
**Lines:** 431

### `TestSensitiveValue`

| Method | Async | Args |
|--------|-------|------|
| `test_sensitive_value_creation` |  | self |
| `test_sensitive_value_string_representation` |  | self |
| `test_sensitive_value_json_serialization` |  | self |
| `test_sensitive_value_equality` |  | self |
| `test_sensitive_value_hash` |  | self |

### `TestMetaLearningConfig`

| Method | Async | Args |
|--------|-------|------|
| `test_default_config_creation` |  | self, temp_data_dir |
| `test_config_from_env_variables` |  | self, temp_data_dir |
| `test_config_file_path_validation` |  | self, temp_data_dir |
| `test_sensitive_value_handling` |  | self, temp_data_dir |
| `test_kafka_validation` |  | self, temp_data_dir |
| `test_redis_url_validation` |  | self, temp_data_dir |
| `test_http_endpoint_validation` |  | self, temp_data_dir |
| `test_config_reload_from_file` |  | self, temp_data_dir |
| `test_numeric_field_constraints` |  | self, temp_data_dir |

### `TestMultiModalData`

| Method | Async | Args |
|--------|-------|------|
| `test_multimodal_data_creation` |  | self |
| `test_multimodal_data_types` |  | self |
| `test_invalid_data_type` |  | self |
| `test_model_dump_for_log` |  | self |
| `test_model_dump_for_log_empty_data` |  | self |

### `TestLoadPersonaDict`

| Method | Async | Args |
|--------|-------|------|
| `test_load_default_persona` |  | self |
| `test_load_persona_from_file` |  | self |
| `test_invalid_persona_file_format` |  | self |
| `test_persona_file_read_error` |  | self |
| `test_invalid_json_in_persona_file` |  | self |

### `TestConfigIntegration`

| Method | Async | Args |
|--------|-------|------|
| `test_full_config_with_all_features` |  | self, temp_data_dir |
| `test_config_persistence` |  | self, temp_data_dir |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `temp_env_file` |  |  |
| `temp_data_dir` |  |  |

---

## tests/test_arbiter_knowledge_graph_core.py
**Lines:** 784

### `TestStateBackends`

| Method | Async | Args |
|--------|-------|------|
| `test_inmemory_state_backend_save_and_load` | ✓ | self |
| `test_redis_state_backend_initialization` | ✓ | self, mock_all_external_services |
| `test_redis_state_backend_save_state` | ✓ | self, mock_all_external_services |
| `test_redis_state_backend_load_state` | ✓ | self, mock_all_external_services |
| `test_postgres_state_backend_initialization` | ✓ | self, mock_all_external_services |

### `TestMetaLearning`

| Method | Async | Args |
|--------|-------|------|
| `test_meta_learning_initialization` |  | self |
| `test_log_correction` |  | self |
| `test_log_correction_size_limit` |  | self |
| `test_train_model` |  | self |
| `test_apply_correction_no_model` |  | self |
| `test_persist_and_load` |  | self, tmp_path |

### `TestCollaborativeAgent`

| Method | Async | Args |
|--------|-------|------|
| `mock_llm_config` |  | self |
| `test_agent_initialization` |  | self, mock_llm_config, mock_all_external_services |
| `test_agent_invalid_api_key` |  | self, mock_all_external_services |
| `test_agent_load_state` | ✓ | self, mock_llm_config, mock_all_external_services |
| `test_agent_save_state` | ✓ | self, mock_llm_config, mock_all_external_services |
| `test_agent_set_persona` | ✓ | self, mock_llm_config, mock_all_external_services |
| `test_agent_predict_timeout` | ✓ | self, mock_llm_config, mock_all_external_services |

### `TestAgentTeam`

| Method | Async | Args |
|--------|-------|------|
| `mock_team_config` |  | self |
| `test_team_initialization` |  | self, mock_team_config, mock_all_external_services |
| `test_team_initialization_missing_dependencies` |  | self |
| `test_team_delegate_task` | ✓ | self, mock_team_config, mock_all_external_services |

### `TestFactoryFunctions`

| Method | Async | Args |
|--------|-------|------|
| `test_get_or_create_agent_default` | ✓ | self, mock_all_external_services |
| `test_get_or_create_agent_with_redis` | ✓ | self, mock_all_external_services |
| `test_get_or_create_agent_with_postgres` | ✓ | self, mock_all_external_services |
| `test_setup_conversation_legacy` | ✓ | self, mock_all_external_services |

### `TestUtilityFunctions`

| Method | Async | Args |
|--------|-------|------|
| `test_get_transcript` |  | self |

### `TestErrorHandling`

| Method | Async | Args |
|--------|-------|------|
| `test_agent_core_exception` |  | self |
| `test_state_backend_error_handling` | ✓ | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `setup_langchain_mocks` |  |  |
| `mock_all_external_services` |  |  |

---

## tests/test_arbiter_knowledge_graph_e2e_knowledge_graph.py
**Lines:** 680

### `TestKnowledgeGraphE2EWorkflow`

| Method | Async | Args |
|--------|-------|------|
| `setup_environment` |  | self, tmp_path |
| `test_complete_agent_lifecycle` | ✓ | self, setup_environment |
| `test_multimodal_processing_pipeline` | ✓ | self, setup_environment |
| `test_agent_team_collaboration` | ✓ | self, setup_environment |
| `test_prompt_strategies_integration` | ✓ | self, setup_environment |
| `test_state_persistence_workflow` | ✓ | self, setup_environment |
| `test_meta_learning_integration` | ✓ | self, setup_environment |
| `test_audit_logging_workflow` | ✓ | self, setup_environment |
| `test_error_handling_and_recovery` | ✓ | self, setup_environment |
| `test_full_prediction_pipeline` | ✓ | self, setup_environment |
| `test_config_validation_and_loading` | ✓ | self, setup_environment |

### `TestKnowledgeGraphPerformance`

| Method | Async | Args |
|--------|-------|------|
| `test_concurrent_agent_operations` | ✓ | self |
| `test_large_context_handling` | ✓ | self |
| `test_memory_cleanup` | ✓ | self |

### `TestKnowledgeGraphSecurity`

| Method | Async | Args |
|--------|-------|------|
| `test_input_sanitization` |  | self |
| `test_pii_redaction` | ✓ | self |

---

## tests/test_arbiter_knowledge_graph_multimodal.py
**Lines:** 595

### `TestMultiModalProcessor`

| Method | Async | Args |
|--------|-------|------|
| `test_abstract_base_class` |  | self |
| `test_abstract_method_required` | ✓ | self |

### `TestDefaultMultiModalProcessor`

| Method | Async | Args |
|--------|-------|------|
| `mock_logger` |  | self |
| `mock_config` |  | self |
| `mock_multimodal_data` |  | self |
| `mock_redis` |  | self |
| `mock_metrics` |  | self |
| `mock_audit` |  | self |
| `test_processor_initialization_no_libraries` |  | self, mock_logger, mock_config |
| `test_processor_initialization_with_redis` |  | self, mock_logger |
| `test_processor_initialization_with_transformers` |  | self, mock_logger, mock_config |
| `test_summarize_with_cache_hit` | ✓ | self, mock_logger, mock_config, mock_multimodal_data, mock_metrics, ...+1 |
| `test_summarize_data_too_large` | ✓ | self, mock_logger, mock_config, mock_multimodal_data, mock_metrics, ...+1 |
| `test_summarize_unsupported_type` | ✓ | self, mock_logger, mock_config, mock_multimodal_data, mock_metrics, ...+1 |
| `test_summarize_timeout` | ✓ | self, mock_logger, mock_config, mock_multimodal_data, mock_metrics, ...+1 |
| `test_process_image_success` | ✓ | self, mock_logger, mock_config, mock_multimodal_data |
| `test_process_image_not_available` | ✓ | self, mock_logger, mock_config, mock_multimodal_data, mock_audit |
| `test_process_audio_success` | ✓ | self, mock_logger, mock_config, mock_multimodal_data |
| `test_process_video_success` | ✓ | self, mock_logger, mock_config, mock_multimodal_data |
| `test_process_text_file_success` | ✓ | self, mock_logger, mock_config, mock_multimodal_data |
| `test_process_text_file_decode_error` | ✓ | self, mock_logger, mock_config, mock_multimodal_data |
| `test_process_pdf_file_success` | ✓ | self, mock_logger, mock_config, mock_multimodal_data |
| `test_caching_successful_result` | ✓ | self, mock_logger, mock_config, mock_multimodal_data, mock_metrics |
| `test_exception_handling` | ✓ | self, mock_logger, mock_config, mock_multimodal_data, mock_metrics, ...+1 |

### `TestIntegration`

| Method | Async | Args |
|--------|-------|------|
| `test_full_processing_pipeline` | ✓ | self, tmp_path |
| `test_concurrent_processing` | ✓ | self |

### `TestErrorCases`

| Method | Async | Args |
|--------|-------|------|
| `test_redis_connection_failure` | ✓ | self |
| `test_audit_logging_failure` | ✓ | self |

---

## tests/test_arbiter_knowledge_graph_prompt_strategies.py
**Lines:** 544

### `TestPromptTemplateLoading`

| Method | Async | Args |
|--------|-------|------|
| `test_load_templates_from_file_success` |  | self |
| `test_load_templates_file_not_found` |  | self |
| `test_load_templates_json_decode_error` |  | self |
| `test_load_templates_unexpected_error` |  | self |
| `test_load_templates_with_custom_file_path` |  | self |
| `test_template_constants_are_set` |  | self |

### `TestPromptStrategy`

| Method | Async | Args |
|--------|-------|------|
| `test_abstract_base_class` |  | self |
| `test_concrete_implementation_required` |  | self |
| `test_get_history_transcript_empty` |  | self |
| `test_get_history_transcript_with_data` |  | self |

### `TestDefaultPromptStrategy`

| Method | Async | Args |
|--------|-------|------|
| `mock_logger` |  | self |
| `mock_multimodal_data` |  | self |
| `test_create_agent_prompt_basic` | ✓ | self, mock_logger |
| `test_create_agent_prompt_with_multimodal` | ✓ | self, mock_logger, mock_multimodal_data |
| `test_create_agent_prompt_empty_multimodal` | ✓ | self, mock_logger |
| `test_create_agent_prompt_with_real_template` | ✓ | self, mock_logger |

### `TestConcisePromptStrategy`

| Method | Async | Args |
|--------|-------|------|
| `mock_logger` |  | self |
| `mock_multimodal_data` |  | self |
| `test_create_agent_prompt_basic` | ✓ | self, mock_logger |
| `test_create_agent_prompt_with_truncation` | ✓ | self, mock_logger |
| `test_truncate_history_short` |  | self, mock_logger |
| `test_truncate_history_long` |  | self, mock_logger |
| `test_create_agent_prompt_with_multimodal` | ✓ | self, mock_logger, mock_multimodal_data |

### `TestIntegration`

| Method | Async | Args |
|--------|-------|------|
| `test_strategy_comparison` | ✓ | self |
| `test_with_actual_templates` | ✓ | self |
| `test_custom_strategy_implementation` | ✓ | self |

### `TestErrorHandling`

| Method | Async | Args |
|--------|-------|------|
| `test_missing_template_keys` | ✓ | self |
| `test_none_values_handling` | ✓ | self |

---

## tests/test_arbiter_knowledge_graph_utils.py
**Lines:** 618

### `TestContextVarFormatter`

| Method | Async | Args |
|--------|-------|------|
| `test_formatter_with_trace_id` |  | self |
| `test_formatter_without_trace_id` |  | self |

### `TestPrometheusMetrics`

| Method | Async | Args |
|--------|-------|------|
| `test_get_or_create_metric_counter` |  | self |
| `test_get_or_create_metric_histogram` |  | self |
| `test_get_or_create_metric_gauge` |  | self |
| `test_get_existing_metric` |  | self |
| `test_unsupported_metric_type` |  | self |
| `test_agent_metrics_exist` |  | self |

### `TestAgentErrorCode`

| Method | Async | Args |
|--------|-------|------|
| `test_error_codes_exist` |  | self |
| `test_error_code_is_string_enum` |  | self |

### `TestAgentCoreException`

| Method | Async | Args |
|--------|-------|------|
| `test_exception_creation` |  | self |
| `test_exception_without_original` |  | self |

### `TestUtilityFunctions`

| Method | Async | Args |
|--------|-------|------|
| `test_datetime_now` |  | self |
| `test_async_with_retry_success` | ✓ | self |
| `test_async_with_retry_failure_then_success` | ✓ | self |
| `test_async_with_retry_all_failures` | ✓ | self |

### `TestPIIRedaction`

| Method | Async | Args |
|--------|-------|------|
| `test_redact_sensitive_pii_key_match` |  | self |
| `test_redact_sensitive_pii_pattern_email` |  | self |
| `test_redact_sensitive_pii_pattern_phone` |  | self |
| `test_redact_sensitive_pii_pattern_credit_card` |  | self |
| `test_redact_sensitive_pii_no_match` |  | self |

### `TestSanitizeContext`

| Method | Async | Args |
|--------|-------|------|
| `test_sanitize_simple_context` | ✓ | self |
| `test_sanitize_with_sensitive_keys` | ✓ | self |
| `test_sanitize_with_pattern_detection` | ✓ | self |
| `test_sanitize_nested_context` | ✓ | self |
| `test_sanitize_max_depth_exceeded` | ✓ | self |
| `test_sanitize_context_too_large` | ✓ | self |
| `test_sanitize_with_datetime` | ✓ | self |

### `TestSanitizeUserInput`

| Method | Async | Args |
|--------|-------|------|
| `test_sanitize_normal_input` |  | self |
| `test_sanitize_prompt_injection` |  | self |
| `test_sanitize_sql_injection` |  | self |
| `test_sanitize_command_injection` |  | self |
| `test_sanitize_code_blocks` |  | self |
| `test_sanitize_multiple_patterns` |  | self |

### `TestAuditLedgerClient`

| Method | Async | Args |
|--------|-------|------|
| `test_client_initialization` |  | self |
| `test_log_event_success` | ✓ | self |
| `test_log_event_with_trace_id` | ✓ | self |
| `test_log_event_failure` | ✓ | self |

### `TestIntegration`

| Method | Async | Args |
|--------|-------|------|
| `test_full_context_sanitization_flow` | ✓ | self |
| `test_retry_with_metrics` | ✓ | self |

---

## tests/test_arbiter_knowledge_loader.py
**Lines:** 291

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `temp_dir` |  |  |
| `test_merge_dict` |  | orig, new, expected |
| `test_save_knowledge_atomic_success` |  | temp_dir |
| `test_save_knowledge_atomic_creates_directory` |  | temp_dir |
| `test_load_knowledge_sync_success` |  | temp_dir |
| `test_load_knowledge_sync_not_found` |  | temp_dir |
| `test_load_knowledge_sync_invalid_json` |  | temp_dir |
| `test_load_knowledge_async_success` | ✓ | temp_dir |
| `test_load_knowledge_async_not_found` | ✓ | temp_dir |
| `test_knowledge_loader_init` |  | temp_dir |
| `test_knowledge_loader_load_all_with_master` |  | temp_dir |
| `test_knowledge_loader_load_all_without_master` |  | temp_dir |
| `test_knowledge_loader_get_knowledge_returns_copy` |  | temp_dir |
| `test_knowledge_loader_save_current_knowledge` |  | temp_dir |
| `test_inject_to_arbiter_success` |  |  |
| `test_inject_to_arbiter_invalid_state` |  |  |
| `test_inject_to_arbiter_no_state` |  |  |
| `test_inject_to_arbiter_thread_safety` |  |  |
| `test_knowledge_loader_nonexistent_path` |  | temp_dir |

---

## tests/test_arbiter_learner_audit.py
**Lines:** 477

### `TestCircuitBreaker`

| Method | Async | Args |
|--------|-------|------|
| `circuit_breaker` |  | self |
| `test_initial_state` | ✓ | self, circuit_breaker |
| `test_record_failure_below_threshold` | ✓ | self, circuit_breaker |
| `test_circuit_opens_at_threshold` | ✓ | self, circuit_breaker |
| `test_record_success_resets_circuit` | ✓ | self, circuit_breaker |
| `test_cooldown_period` | ✓ | self, circuit_breaker |

### `TestMerkleTree`

| Method | Async | Args |
|--------|-------|------|
| `test_empty_tree` |  | self |
| `test_single_leaf` |  | self |
| `test_multiple_leaves` |  | self |
| `test_odd_number_of_leaves` |  | self |
| `test_invalid_leaf_type` |  | self |
| `test_proof_index_out_of_range` |  | self |
| `test_serialize_deserialize` |  | self |

### `TestPersistKnowledge`

| Method | Async | Args |
|--------|-------|------|
| `mock_db` |  | self |
| `mock_circuit_breaker` |  | self |
| `test_persist_knowledge_success` | ✓ | self, mock_db, mock_circuit_breaker |
| `test_persist_knowledge_circuit_open` | ✓ | self, mock_db, mock_circuit_breaker |
| `test_persist_knowledge_db_failure` | ✓ | self, mock_db, mock_circuit_breaker |
| `test_persist_knowledge_retry_success` | ✓ | self, mock_db, mock_circuit_breaker |

### `TestPersistKnowledgeBatch`

| Method | Async | Args |
|--------|-------|------|
| `mock_db` |  | self |
| `mock_circuit_breaker` |  | self |
| `sample_entries` |  | self |
| `test_persist_batch_success` | ✓ | self, mock_db, mock_circuit_breaker, sample_entries |
| `test_persist_batch_circuit_open` | ✓ | self, mock_db, mock_circuit_breaker, sample_entries |

---

## tests/test_arbiter_learner_config_integration.py
**Lines:** 179

### `TestConfigIntegration`

| Method | Async | Args |
|--------|-------|------|
| `mock_learner` |  | self |
| `test_all_parsers_loadable` | ✓ | self |
| `test_all_templates_usable` | ✓ | self, mock_learner |
| `_mock_generate_explanation` | ✓ | self, learner, domain, key, new_value, ...+2 |
| `test_parser_config_integration` | ✓ | self |
| `test_template_config_integration` | ✓ | self |

---

## tests/test_arbiter_learner_core.py
**Lines:** 562

### `TestLearnerArbiterHelper`

| Method | Async | Args |
|--------|-------|------|
| `test_arbiter_helper_initialization` |  | self |

### `TestLearner`

| Method | Async | Args |
|--------|-------|------|
| `mock_redis` |  | self |
| `mock_arbiter` |  | self |
| `mock_db` |  | self |
| `learner` | ✓ | self, mock_arbiter, mock_redis, mock_db |
| `test_learner_initialization` |  | self, mock_arbiter, mock_redis |
| `test_learn_new_thing_success` | ✓ | self, learner |
| `test_learn_new_thing_invalid_domain` | ✓ | self, learner |
| `test_learn_new_thing_policy_blocked` | ✓ | self, learner |
| `test_learn_new_thing_validation_failed` | ✓ | self, learner |
| `test_learn_batch_success` | ✓ | self, learner |
| `test_learn_batch_mixed_results` | ✓ | self, learner |
| `test_forget_fact_success` | ✓ | self, learner |
| `test_forget_fact_not_found` | ✓ | self, learner |
| `test_retrieve_knowledge_from_memory` | ✓ | self, learner |
| `test_retrieve_knowledge_from_redis` | ✓ | self, learner |
| `test_retrieve_knowledge_from_database` | ✓ | self, learner |
| `test_retrieve_knowledge_not_found` | ✓ | self, learner |
| `test_compute_diff` |  | self, learner |
| `test_encryption_for_encrypted_domain` | ✓ | self, learner |
| `test_self_audit_task` | ✓ | self, learner |
| `test_event_hooks_execution` | ✓ | self, learner |
| `test_circuit_breaker_integration` | ✓ | self, learner |

---

## tests/test_arbiter_learner_e2e_learner.py
**Lines:** 499

### `TestEndToEndLearner`

| Method | Async | Args |
|--------|-------|------|
| `clean_environment` |  | self |
| `patch_audit_log_module` |  | self |
| `patch_prometheus_metrics` |  | self |
| `patch_time_functions` |  | self |
| `patch_arbiter_config` |  | self |
| `setup_learner_environment` |  | self |
| `test_complete_learning_cycle` | ✓ | self, setup_learner_environment |
| `test_batch_learning_with_validation` | ✓ | self, setup_learner_environment |
| `test_encryption_for_sensitive_domains` | ✓ | self, setup_learner_environment |

---

## tests/test_arbiter_learner_encryption.py
**Lines:** 407

### `TestArbiterConfig`

| Method | Async | Args |
|--------|-------|------|
| `test_default_configuration` |  | self |
| `test_environment_variable_defaults` |  | self |
| `test_load_keys_from_ssm_success` |  | self, mock_boto_client |
| `test_load_keys_from_ssm_failure_fallback` |  | self, mock_boto_client |
| `test_load_keys_no_ssm_paths` |  | self, mock_boto_client |
| `test_rotate_keys` | ✓ | self |

### `TestEncryptValue`

| Method | Async | Args |
|--------|-------|------|
| `test_encrypt_simple_value` | ✓ | self |
| `test_encrypt_with_different_key_ids` | ✓ | self |
| `test_encrypt_complex_types` | ✓ | self |
| `test_encrypt_failure` | ✓ | self |

### `TestDecryptValue`

| Method | Async | Args |
|--------|-------|------|
| `test_decrypt_simple_value` | ✓ | self |
| `test_decrypt_with_multiple_keys` | ✓ | self |
| `test_decrypt_without_key_id` | ✓ | self |
| `test_decrypt_invalid_input_type` | ✓ | self |
| `test_decrypt_unknown_key_id` | ✓ | self |
| `test_decrypt_invalid_token` | ✓ | self |
| `test_decrypt_deserialization_failure` | ✓ | self |

### `TestIntegration`

| Method | Async | Args |
|--------|-------|------|
| `test_encrypt_decrypt_roundtrip` | ✓ | self |
| `test_key_rotation_scenario` | ✓ | self |

---

## tests/test_arbiter_learner_env_pollution.py
**Lines:** 51

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `test_decision_optimizer_settings_env_pollution` |  | monkeypatch |

---

## tests/test_arbiter_learner_explanation_prompt_config.py
**Lines:** 106

### `TestExplanationPromptConfig`

| Method | Async | Args |
|--------|-------|------|
| `template_data` |  | self |
| `test_all_templates_present` |  | self, template_data |
| `test_template_structure` |  | self, template_data |
| `test_template_variables_in_text` |  | self, template_data |
| `test_temperature_ranges` |  | self, template_data |
| `test_max_tokens_reasonable` |  | self, template_data |
| `test_security_templates` |  | self, template_data |

---

## tests/test_arbiter_learner_explanations.py
**Lines:** 483

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `ensure_templates_loaded` |  |  |
| `test_tracer` |  |  |
| `in_memory_exporter` |  |  |
| `setup_opentelemetry` |  | mocker, in_memory_exporter |
| `setup_env` |  | mocker, tmp_path |
| `mock_arbiter_config` |  | mocker |
| `mock_learner` |  | mocker |
| `test_load_prompt_templates_success` | ✓ | tmp_path |
| `test_load_prompt_templates_file_not_found` | ✓ | mocker, tmp_path |
| `test_load_prompt_templates_invalid_json` | ✓ | tmp_path |
| `test_generate_text_with_retry_success` | ✓ | mock_learner |
| `test_generate_text_with_retry_failure` | ✓ | mock_learner |
| `test_generate_explanation_success` | ✓ | mock_learner |
| `test_generate_explanation_cache_hit` | ✓ | mock_learner |
| `test_generate_explanation_kg_insights` | ✓ | mock_learner |
| `test_generate_explanation_kg_error` | ✓ | mock_learner, capsys |
| `test_generate_explanation_retry_exhausted` | ✓ | mock_learner |
| `test_generate_explanation_unexpected_error` | ✓ | mock_learner |
| `test_record_explanation_quality_success` | ✓ | mock_learner |
| `test_get_explanation_quality_report_all` | ✓ | mock_learner |
| `test_get_explanation_quality_report_filtered` | ✓ | mock_learner |
| `test_concurrent_generate_explanation` | ✓ | mock_learner |
| `test_tracing_generate_explanation` | ✓ | mock_learner |
| `test_metrics_in_generate_explanation` | ✓ | mock_learner |
| `test_record_explanation_quality_tracing` | ✓ | mock_learner |
| `test_get_explanation_quality_report_tracing` | ✓ | mock_learner |

---

## tests/test_arbiter_learner_fuzzy.py
**Lines:** 514

### `MockFuzzyParser`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, facts_to_return, should_fail, delay |
| `parse` | ✓ | self, text, context |

### `TestLoadParserPriorities`

| Method | Async | Args |
|--------|-------|------|
| `test_load_priorities_from_file` |  | self |
| `test_load_priorities_file_not_found` |  | self |
| `test_load_priorities_invalid_json` |  | self |

### `TestLearnBatchWithRetry`

| Method | Async | Args |
|--------|-------|------|
| `test_successful_learn_batch` | ✓ | self |
| `test_learn_batch_retry_on_failure` | ✓ | self |

### `TestProcessUnstructuredData`

| Method | Async | Args |
|--------|-------|------|
| `mock_learner` |  | self |
| `test_process_with_single_parser` | ✓ | self, mock_learner |
| `test_process_with_multiple_parsers` | ✓ | self, mock_learner |
| `test_process_with_parser_priority` | ✓ | self, mock_learner |
| `test_process_invalid_text` | ✓ | self, mock_learner |
| `test_process_invalid_context` | ✓ | self, mock_learner |
| `test_process_no_parsers_registered` | ✓ | self, mock_learner |
| `test_process_parser_timeout` | ✓ | self, mock_learner |
| `test_process_parser_exception` | ✓ | self, mock_learner |
| `test_process_no_facts_extracted` | ✓ | self, mock_learner |
| `test_process_learn_batch_failure` | ✓ | self, mock_learner |

### `TestRegisterFuzzyParserHook`

| Method | Async | Args |
|--------|-------|------|
| `test_register_valid_parser` |  | self |
| `test_register_invalid_parser_no_parse` |  | self |
| `test_register_invalid_parser_sync_parse` |  | self |

### `TestIntegration`

| Method | Async | Args |
|--------|-------|------|
| `test_end_to_end_fuzzy_parsing` | ✓ | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `clean_parser_priorities` |  |  |

---

## tests/test_arbiter_learner_learner_metrics.py
**Lines:** 389

### `TestGlobalLabels`

| Method | Async | Args |
|--------|-------|------|
| `test_default_global_labels` |  | self |
| `test_custom_global_labels` |  | self |
| `test_get_labels_helper` |  | self |

### `TestGetOrCreateMetric`

| Method | Async | Args |
|--------|-------|------|
| `test_create_new_counter` |  | self |
| `test_create_histogram_with_buckets` |  | self |
| `test_retrieve_existing_metric` |  | self |
| `test_replace_metric_with_different_type` |  | self |
| `test_handle_registry_error` |  | self |

### `TestLearningMetrics`

| Method | Async | Args |
|--------|-------|------|
| `test_learn_counter_structure` |  | self |
| `test_learn_error_counter_structure` |  | self |
| `test_learn_duration_histogram` |  | self |
| `test_learn_duration_summary` |  | self |

### `TestForgettingMetrics`

| Method | Async | Args |
|--------|-------|------|
| `test_forget_counter_structure` |  | self |
| `test_forget_duration_histogram` |  | self |
| `test_forget_duration_summary` |  | self |

### `TestRetrievalMetrics`

| Method | Async | Args |
|--------|-------|------|
| `test_retrieve_hit_miss_structure` |  | self |

### `TestAuditMetrics`

| Method | Async | Args |
|--------|-------|------|
| `test_audit_events_total_structure` |  | self |
| `test_circuit_breaker_state_gauge` |  | self |
| `test_audit_failure_total_structure` |  | self |

### `TestExplanationMetrics`

| Method | Async | Args |
|--------|-------|------|
| `test_explanation_llm_latency_histogram` |  | self |
| `test_explanation_llm_failure_counter` |  | self |

### `TestFuzzyParserMetrics`

| Method | Async | Args |
|--------|-------|------|
| `test_fuzzy_parser_success_counter` |  | self |
| `test_fuzzy_parser_failure_counter` |  | self |
| `test_fuzzy_parser_latency_histogram` |  | self |

### `TestSelfAuditMetrics`

| Method | Async | Args |
|--------|-------|------|
| `test_self_audit_duration_histogram` |  | self |
| `test_self_audit_failure_counter` |  | self |

### `TestModuleInfoMetric`

| Method | Async | Args |
|--------|-------|------|
| `test_learner_info_structure` |  | self |

### `TestMetricUsage`

| Method | Async | Args |
|--------|-------|------|
| `test_counter_increment` |  | self |
| `test_counter_increment_with_helper` |  | self |
| `test_histogram_observe` |  | self |
| `test_gauge_set` |  | self |

### `TestMetricLabelCombinations`

| Method | Async | Args |
|--------|-------|------|
| `test_consistent_global_labels` |  | self |
| `test_domain_specific_metrics` |  | self |

---

## tests/test_arbiter_learner_parser_priorities_config.py
**Lines:** 171

### `TestParserPrioritiesConfig`

| Method | Async | Args |
|--------|-------|------|
| `config_data` |  | self |
| `test_config_structure` |  | self, config_data |
| `test_parser_priority_schema` |  | self, config_data |
| `test_priority_ordering` |  | self, config_data |
| `test_timeout_overrides` |  | self, config_data |
| `test_domain_specific_overrides` |  | self, config_data |
| `test_parser_chains` |  | self, config_data |
| `test_performance_thresholds` |  | self, config_data |
| `test_critical_parser_properties` |  | self, config_data |

---

## tests/test_arbiter_learner_validation.py
**Lines:** 477

### `TestValidateData`

| Method | Async | Args |
|--------|-------|------|
| `mock_learner` |  | self |
| `sample_schema` |  | self |
| `test_validate_with_schema_success` | ✓ | self, mock_learner, sample_schema |
| `test_validate_with_schema_failure` | ✓ | self, mock_learner, sample_schema |
| `test_validate_with_sync_hook` | ✓ | self, mock_learner |
| `test_validate_with_async_hook` | ✓ | self, mock_learner |
| `test_validate_with_both_schema_and_hook` | ✓ | self, mock_learner, sample_schema |
| `test_validate_invalid_domain` | ✓ | self, mock_learner |
| `test_validate_null_value` | ✓ | self, mock_learner |
| `test_validate_domain_not_found` | ✓ | self, mock_learner |
| `test_validate_invalid_schema_error` | ✓ | self, mock_learner |
| `test_validate_hook_exception` | ✓ | self, mock_learner |

### `TestRegisterValidationHook`

| Method | Async | Args |
|--------|-------|------|
| `mock_learner` |  | self |
| `test_register_sync_hook` |  | self, mock_learner |
| `test_register_async_hook` |  | self, mock_learner |
| `test_register_non_callable` |  | self, mock_learner |
| `test_register_sync_hook_wrong_signature` |  | self, mock_learner |
| `test_register_async_hook_wrong_signature` |  | self, mock_learner |
| `test_register_lambda_hook` |  | self, mock_learner |

### `TestReloadSchemas`

| Method | Async | Args |
|--------|-------|------|
| `mock_learner` |  | self |
| `temp_schema_dir` |  | self |
| `test_reload_schemas_success` | ✓ | self, mock_learner, temp_schema_dir |
| `test_reload_schemas_invalid_json` | ✓ | self, mock_learner |
| `test_reload_schemas_invalid_schema_structure` | ✓ | self, mock_learner |
| `test_reload_schemas_directory_not_found` | ✓ | self, mock_learner |
| `test_reload_schemas_permission_denied` | ✓ | self, mock_learner |
| `test_reload_schemas_with_hooks` | ✓ | self, mock_learner, temp_schema_dir |
| `test_reload_schemas_redis_failure` | ✓ | self, mock_learner, temp_schema_dir |
| `test_reload_schemas_retry_on_failure` | ✓ | self, mock_learner |

---

## tests/test_arbiter_logging_utils.py
**Lines:** 942

### `TestPIIRedactorFilter` (unittest.TestCase)

| Method | Async | Args |
|--------|-------|------|
| `setUp` |  | self |
| `test_email_redaction` |  | self |
| `test_ssn_redaction` |  | self |
| `test_credit_card_redaction` |  | self |
| `test_phone_number_redaction` |  | self |
| `test_ip_address_redaction` |  | self |
| `test_api_key_redaction` |  | self |
| `test_aws_credentials_redaction` |  | self |
| `test_jwt_token_redaction` |  | self |
| `test_database_connection_redaction` |  | self |
| `test_file_path_redaction` |  | self |
| `test_mixed_pii_redaction` |  | self |
| `test_hash_pii_option` |  | self |
| `test_custom_patterns` |  | self |
| `test_redaction_callback` |  | self |
| `test_custom_redactor_function` |  | self |
| `test_cache_functionality` |  | self |
| `test_metrics_tracking` |  | self |
| `test_filter_with_log_record` |  | self |
| `test_filter_with_args` |  | self |
| `test_filter_with_dict_args` |  | self |
| `test_filter_with_exception` |  | self |

### `TestStructuredFormatter` (unittest.TestCase)

| Method | Async | Args |
|--------|-------|------|
| `setUp` |  | self |
| `test_basic_formatting` |  | self |
| `test_exception_formatting` |  | self |
| `test_custom_fields` |  | self |
| `test_without_traceback` |  | self |

### `TestAuditLogger` (unittest.TestCase)

| Method | Async | Args |
|--------|-------|------|
| `test_audit_logger_creation` |  | self |
| `test_audit_logger_without_file` |  | self |

### `TestLogLevel` (unittest.TestCase)

| Method | Async | Args |
|--------|-------|------|
| `test_custom_log_levels` |  | self |

### `TestGetLogger` (unittest.TestCase)

| Method | Async | Args |
|--------|-------|------|
| `test_get_logger_basic` |  | self |
| `test_get_logger_with_level` |  | self |
| `test_get_logger_with_pii_filter` |  | self |
| `test_get_logger_structured` |  | self |
| `test_get_logger_no_duplicate_handlers` |  | self |

### `TestLoggingContext` (unittest.TestCase)

| Method | Async | Args |
|--------|-------|------|
| `test_logging_context_basic` |  | self |
| `test_nested_logging_context` |  | self |
| `test_logging_context_with_exception` |  | self |

### `TestConfigureLogging` (unittest.TestCase)

| Method | Async | Args |
|--------|-------|------|
| `setUp` |  | self |
| `tearDown` |  | self |
| `test_configure_logging_basic` |  | self |
| `test_configure_logging_with_file` |  | self |
| `test_configure_logging_structured` |  | self |
| `test_configure_logging_with_pii_filter` |  | self |
| `test_configure_logging_with_audit` |  | self |

### `TestRedactText` (unittest.TestCase)

| Method | Async | Args |
|--------|-------|------|
| `test_redact_text_function` |  | self |
| `test_get_redaction_patterns` |  | self |

### `TestThreadSafety` (unittest.TestCase)

| Method | Async | Args |
|--------|-------|------|
| `test_pii_filter_thread_safety` |  | self |

### `TestEdgeCases` (unittest.TestCase)

| Method | Async | Args |
|--------|-------|------|
| `test_empty_text_redaction` |  | self |
| `test_filter_with_invalid_record` |  | self |
| `test_redaction_callback_error` |  | self |
| `test_large_text_redaction` |  | self |
| `test_cache_size_limit` |  | self |

### `TestIntegration` (unittest.TestCase)

| Method | Async | Args |
|--------|-------|------|
| `test_complete_logging_pipeline` |  | self |

---

## tests/test_arbiter_message_queue_service.py
**Lines:** 443

### `MessageQueueServiceError` (Exception)

### `SerializationError` (Exception)

### `DecryptionError` (Exception)

### `MockSettings`
**Attributes:** MQ_TOPIC_PREFIX, MQ_DLQ_TOPIC_SUFFIX, MQ_CONSUMER_GROUP_ID, MQ_POISON_MESSAGE_THRESHOLD, ENCRYPTION_KEY_BYTES

### `MockFernet`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, key |
| `encrypt` |  | self, data |
| `decrypt` |  | self, data |

### `MockRedisClient`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |

### `MockAIOKafkaProducer`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |

### `MessageQueueService`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, backend_type, settings |
| `publish` | ✓ | self, event_type, data, is_critical |
| `subscribe` | ✓ | self, event_type, handler |
| `_consume_loop` | ✓ | self |
| `_process_message` | ✓ | self, message, event_type |
| `send_to_dlq` | ✓ | self, event_type, message, reason |
| `replay_dlq` | ✓ | self, event_type |
| `disconnect` | ✓ | self |
| `_serialize_message` |  | self, data |
| `_deserialize_message` |  | self, serialized |
| `_encrypt_payload` |  | self, payload |
| `_decrypt_payload` |  | self, encrypted |
| `_reconnect_if_needed` | ✓ | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_get_or_create_metric` |  | metric_type, name, documentation, labelnames, buckets |
| `clear_registry` |  |  |
| `test_init_redis` | ✓ |  |
| `test_publish_redis` | ✓ |  |
| `test_subscribe` | ✓ |  |
| `test_process_message_success` | ✓ |  |
| `test_process_message_failure` | ✓ |  |
| `test_poison_message` | ✓ |  |
| `test_send_to_dlq` | ✓ |  |
| `test_replay_dlq` | ✓ |  |
| `test_disconnect` | ✓ |  |
| `test_encrypt_decrypt` | ✓ |  |
| `test_decrypt_invalid` | ✓ |  |
| `test_serialize_deserialize` | ✓ |  |
| `test_deserialize_invalid` | ✓ |  |
| `test_metric_thread_safe` |  |  |
| `test_publish_error` | ✓ |  |
| `test_reconnect` | ✓ |  |
| `test_invalid_backend` |  |  |

---

## tests/test_arbiter_meta_learning_orchestrator_audit_utils.py
**Lines:** 451

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `setup_env` | ✓ | mocker, tmp_path |
| `audit_utils` | ✓ | setup_env, tmp_path |
| `clear_metrics_and_traces` | ✓ |  |
| `test_initialization_success` | ✓ | audit_utils |
| `test_initialization_no_encryption_key` | ✓ | mocker, tmp_path |
| `test_initialization_no_signing_keys` | ✓ | mocker, caplog, tmp_path |
| `test_hash_event_consistency` | ✓ | audit_utils |
| `test_sign_verify_hash` | ✓ | audit_utils |
| `test_add_audit_event_file` | ✓ | audit_utils |
| `test_add_audit_event_kafka` | ✓ | mocker, tmp_path |
| `test_add_audit_event_kafka_fallback` | ✓ | mocker, tmp_path, caplog |
| `test_add_audit_event_no_encryption` | ✓ | mocker, tmp_path |
| `test_validate_audit_chain_valid` | ✓ | audit_utils, tmp_path |
| `test_validate_audit_chain_tampered` | ✓ | audit_utils, tmp_path |
| `test_validate_audit_chain_invalid_signature` | ✓ | audit_utils, tmp_path |
| `test_validate_audit_chain_malformed_json` | ✓ | audit_utils, tmp_path |
| `test_validate_audit_chain_missing_file` | ✓ | audit_utils, tmp_path |
| `test_log_rotation` | ✓ | setup_env, tmp_path |
| `test_concurrent_add_audit_event` | ✓ | audit_utils, tmp_path |
| `test_write_audit_event_retry` | ✓ | audit_utils, mocker, tmp_path, caplog |

**Constants:** `logger`, `tracer`, `private_key`, `public_key`, `ENCRYPTION_KEY`, `SAMPLE_ENV`, `SAMPLE_EVENT`

---

## tests/test_arbiter_meta_learning_orchestrator_clients.py
**Lines:** 486

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `setup_env` | ✓ | mocker |
| `mock_response` | ✓ | mocker |
| `mock_session` | ✓ | mocker, mock_response |
| `ml_client` | ✓ | mock_session |
| `agent_client` | ✓ | mock_session |
| `clear_metrics_and_traces` | ✓ |  |
| `test_base_http_client_initialization` | ✓ | ml_client |
| `test_ml_client_train_model_success` | ✓ | ml_client, mock_session |
| `test_ml_client_train_model_failure` | ✓ | ml_client, mocker, mock_session |
| `test_ml_client_get_training_status_success` | ✓ | ml_client, mock_session |
| `test_ml_client_evaluate_model_success` | ✓ | ml_client, mock_session |
| `test_ml_client_deploy_model_success` | ✓ | ml_client, mock_session |
| `test_agent_client_update_prioritization_weights_success` | ✓ | agent_client, mock_session |
| `test_agent_client_update_policy_rules_success` | ✓ | agent_client, mock_session |
| `test_agent_client_update_rl_policy_success` | ✓ | agent_client, mock_session |
| `test_agent_client_delete_config_success` | ✓ | agent_client, mock_session |
| `test_agent_client_rollback_config_success` | ✓ | agent_client, mock_session |
| `test_pii_redaction` | ✓ | ml_client, mocker |
| `test_timeout_handling` | ✓ | ml_client, mocker, caplog |
| `test_http_error_handling` | ✓ | ml_client, mocker, caplog |
| `test_concurrent_requests` | ✓ | ml_client, mock_session |
| `test_session_close` | ✓ | mocker |
| `test_no_api_key_warning` | ✓ | mocker, caplog |

**Constants:** `logger`, `tracer`, `SAMPLE_ENV`

---

## tests/test_arbiter_meta_learning_orchestrator_config.py
**Lines:** 415

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `setup_env_sync` |  | mocker, tmp_path |
| `config_instance` |  | tmp_path |
| `test_config_initialization_success` | ✓ | config_instance, tmp_path |
| `test_config_default_values` | ✓ | mocker, tmp_path |
| `test_config_missing_keys_warn` | ✓ | mocker, caplog, tmp_path |
| `test_config_secure_mode_enforces_keys` | ✓ | mocker, tmp_path |
| `test_config_kafka_validation_with_empty_string` | ✓ | mocker, tmp_path |
| `test_config_kafka_brokers_required_when_enabled` | ✓ | mocker, tmp_path |
| `test_config_kafka_audit_enabled_empty_brokers` | ✓ | mocker, tmp_path |
| `test_config_kafka_broker_format_validation` | ✓ | mocker, tmp_path |
| `test_config_kafka_valid_brokers` | ✓ | mocker, tmp_path |
| `test_config_kafka_broker_with_spaces` | ✓ | mocker, tmp_path |
| `test_config_invalid_redis_url` | ✓ | mocker, tmp_path |
| `test_config_valid_redis_urls` | ✓ | mocker, tmp_path |
| `test_config_invalid_endpoint_scheme` | ✓ | mocker, tmp_path |
| `test_config_valid_endpoints` | ✓ | mocker, tmp_path |
| `test_config_file_path_validation` | ✓ | mocker, tmp_path |
| `test_config_file_path_access_denied` | ✓ | mocker, tmp_path |

**Constants:** `logger`, `SAMPLE_ENV`

---

## tests/test_arbiter_meta_learning_orchestrator_logging_utils.py
**Lines:** 388

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `clear_traces` | ✓ |  |
| `mock_span_context` |  | mocker |
| `logger_with_filters` |  | caplog, mocker |
| `setup_env` |  | mocker |
| `test_log_correlation_filter_with_span` | ✓ | logger_with_filters, mock_span_context, caplog |
| `test_log_correlation_filter_no_span` | ✓ | logger_with_filters, mocker, caplog |
| `test_log_correlation_filter_invalid_span` | ✓ | logger_with_filters, mocker, caplog |
| `test_pii_redaction_filter_msg_string` | ✓ | logger_with_filters, caplog |
| `test_pii_redaction_filter_msg_json` | ✓ | logger_with_filters, caplog |
| `test_pii_redaction_filter_details_dict` | ✓ | logger_with_filters, caplog |
| `test_pii_redaction_filter_args_tuple` | ✓ | logger_with_filters, caplog |
| `test_pii_redaction_filter_env_sensitive_keys` | ✓ | mocker, caplog |
| `test_pii_redaction_filter_nested_lists` | ✓ | logger_with_filters, caplog |
| `test_pii_redaction_filter_regex_performance` | ✓ | logger_with_filters, caplog |
| `test_filters_combined` | ✓ | logger_with_filters, mock_span_context, caplog |
| `test_pii_redaction_filter_non_string_non_dict` | ✓ | logger_with_filters, caplog |
| `test_pii_redaction_filter_empty_keys` | ✓ | mocker, caplog |
| `test_log_correlation_filter_no_trace` | ✓ | logger_with_filters, mocker, caplog |

**Constants:** `logger`, `tracer`

---

## tests/test_arbiter_meta_learning_orchestrator_meta_learning_orchestrator_e2e.py
**Lines:** 462

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `setup_e2e_env` | ✓ | mocker, tmp_path |
| `orchestrator` | ✓ | mocker, tmp_path |
| `get_metric_value` |  | metric, labels |
| `test_e2e_full_lifecycle` | ✓ | orchestrator, mocker, caplog, tmp_path |
| `test_e2e_error_handling` | ✓ | orchestrator, mocker, caplog |
| `test_e2e_leader_election` | ✓ | orchestrator, mocker, caplog |
| `test_e2e_data_cleanup_local` | ✓ | orchestrator, mocker, tmp_path |
| `test_e2e_health_and_readiness` | ✓ | orchestrator, mocker |
| `test_e2e_logging_pii_redaction` | ✓ | caplog, orchestrator |

**Constants:** `test_logger`, `handler`, `tracer`, `SAMPLE_ENV`, `SAMPLE_RECORD`

---

## tests/test_arbiter_meta_learning_orchestrator_models.py
**Lines:** 358

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `learning_record_data` |  |  |
| `model_version_data` |  |  |
| `test_learning_record_initialization_success` |  | learning_record_data |
| `test_learning_record_missing_required_fields` |  | learning_record_data |
| `test_learning_record_extra_fields` |  | learning_record_data |
| `test_learning_record_immutability` |  | learning_record_data |
| `test_learning_record_json_serialization` |  | learning_record_data |
| `test_learning_record_default_timestamp` |  | learning_record_data |
| `test_learning_record_invalid_event_type` |  |  |
| `test_model_version_initialization_success` |  | model_version_data |
| `test_model_version_missing_required_fields` |  | model_version_data |
| `test_model_version_extra_fields` |  | model_version_data |
| `test_model_version_immutability` |  | model_version_data |
| `test_model_version_json_serialization` |  | model_version_data |
| `test_model_version_deployed_no_accuracy` |  | model_version_data |
| `test_model_version_low_accuracy` |  | model_version_data |
| `test_model_version_deployed_not_active` |  | model_version_data |
| `test_model_version_valid_deployed` |  | model_version_data |
| `test_model_version_retry_count_validation` |  | model_version_data |
| `test_model_version_valid_timestamp` |  | model_version_data |
| `test_learning_record_valid_timestamp` |  | learning_record_data |
| `test_data_ingestion_error` |  |  |
| `test_model_deployment_error` |  |  |
| `test_leader_election_error` |  |  |
| `test_invalid_deployment_status` |  |  |
| `test_model_version_failed_status` |  | model_version_data |
| `test_model_version_rolled_back_status` |  | model_version_data |
| `test_learning_record_all_event_types` |  |  |
| `test_model_version_empty_metadata` |  | model_version_data |
| `test_model_version_complex_metadata` |  | model_version_data |
| `test_learning_record_without_optional_fields` |  |  |
| `test_model_version_high_accuracy_not_deployed` |  | model_version_data |

**Constants:** `SAMPLE_LEARNING_RECORD`, `SAMPLE_MODEL_VERSION`

---

## tests/test_arbiter_meta_learning_orchestrator_orchestrator.py
**Lines:** 539

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `setup_env` | ✓ | mocker, tmp_path |
| `mock_config` | ✓ | mocker, tmp_path |
| `mock_ml_platform_client` | ✓ | mocker |
| `mock_agent_config_service` | ✓ | mocker |
| `mock_kafka_producer` | ✓ | mocker |
| `mock_redis_client` | ✓ | mocker |
| `mock_audit_utils` | ✓ | mocker |
| `orchestrator` | ✓ | mock_config, mock_ml_platform_client, mock_agent_config_service, mock_kafka_producer, mock_redis_client, ...+2 |
| `clear_metrics_and_traces` | ✓ |  |
| `test_orchestrator_initialization_success` | ✓ | orchestrator |
| `test_orchestrator_start_stop` | ✓ | orchestrator, caplog |
| `test_ingest_learning_record_success` | ✓ | orchestrator, mocker |
| `test_ingest_learning_record_validation_error` | ✓ | orchestrator, mocker, caplog |
| `test_leader_election_success` | ✓ | orchestrator |
| `test_leader_step_down` | ✓ | orchestrator, mocker |
| `test_training_check_core` | ✓ | orchestrator, mocker |
| `test_data_cleanup_core_local` | ✓ | orchestrator, tmp_path |
| `test_health_status` | ✓ | orchestrator, mocker |
| `test_is_ready` | ✓ | orchestrator, mocker |

**Constants:** `logger`, `tracer`, `SAMPLE_ENV`, `SAMPLE_RECORD`

---

## tests/test_arbiter_meta_learning_orchestrator_orchestrator_metrics.py
**Lines:** 400

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `setup_env` |  | mocker |
| `metric_registry` |  |  |
| `parse_metrics_output` |  | metrics_text |
| `test_get_or_create_metric_internal` |  | metric_class, name, doc, labelnames, buckets, ...+1 |
| `test_get_or_create_metric_type_mismatch` |  | metric_class, name, doc, caplog |
| `test_metric_registry_get_or_create` |  | metric_registry |
| `test_metric_registry_global_labels` |  | metric_registry |
| `get_metrics_for_test` |  |  |
| `test_metric_operations` |  | metric_idx |
| `test_histogram_metrics` |  | metric_registry |
| `test_histogram_buckets` |  | metric_name |
| `test_metrics_with_no_env_vars` |  | mocker |
| `test_metric_registry_thread_safety` |  | metric_registry, mocker |
| `test_invalid_label_names` |  | metric_registry, caplog |
| `test_metrics_exposition_format` |  |  |

**Constants:** `logger`, `SAMPLE_ENV`

---

## tests/test_arbiter_metrics.py
**Lines:** 340

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `clear_registry` |  |  |
| `mock_logger` |  |  |
| `test_multiprocess_mode` |  | mock_logger |
| `test_get_or_create_counter` |  | name, doc, labels |
| `test_get_or_create_gauge` |  | name, doc, labels |
| `test_get_or_create_histogram` |  | name, doc, labels, buckets |
| `test_get_or_create_summary` |  | name, doc, labels |
| `test_thread_safe_creation` |  |  |
| `test_get_or_create_metric` |  | metric_type, kwargs |
| `test_get_or_create_metric_unsupported` |  |  |
| `test_metrics_handler_success` |  |  |
| `test_metrics_handler_unauthorized` |  |  |
| `test_metrics_handler_no_token` |  |  |
| `test_register_dynamic_metric` |  | metric_type, kwargs |
| `test_register_dynamic_metric_unsupported` |  |  |
| `test_register_dynamic_metric_error` |  | mock_create, mock_logger |
| `test_metric_registration_time` |  | mock_time |
| `test_metric_registration_time_alternative` |  |  |
| `test_get_or_create_wrong_type` |  | mock_logger |

---

## tests/test_arbiter_models_audit_ledger_client.py
**Lines:** 713

### `MockReceipt` (dict)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |

### `TestAuditLedgerClientInit`

| Method | Async | Args |
|--------|-------|------|
| `test_init_with_valid_config` |  | self, mock_web3_dependencies |
| `test_init_with_invalid_abi` |  | self, mock_web3_dependencies, mocker |
| `test_init_with_invalid_abi_production` |  | self, mock_web3_dependencies, mocker |
| `test_init_missing_required_env_vars` |  | self, mock_web3_dependencies, mocker |
| `test_init_with_production_env_requires_secrets_manager` |  | self, mock_web3_dependencies, mocker |

### `TestAuditLedgerClientConnection`

| Method | Async | Args |
|--------|-------|------|
| `test_connect_success` | ✓ | self, audit_client, mock_web3_dependencies |
| `test_connect_idempotent` | ✓ | self, audit_client |
| `test_connect_failure` | ✓ | self, audit_client, mock_web3_dependencies |
| `test_disconnect_success` | ✓ | self, audit_client, mock_web3_dependencies |
| `test_disconnect_idempotent` | ✓ | self, audit_client |
| `test_context_manager` | ✓ | self, audit_client |

### `TestAuditLedgerClientEventLogging`

| Method | Async | Args |
|--------|-------|------|
| `test_log_event_success` | ✓ | self, audit_client, mock_web3_dependencies |
| `test_log_event_not_connected` | ✓ | self, audit_client |
| `test_log_event_validation_error` | ✓ | self, audit_client |
| `test_log_event_idempotency` | ✓ | self, audit_client |
| `test_log_event_transaction_failure` | ✓ | self, audit_client, mock_web3_dependencies |
| `test_log_event_pii_hashing` | ✓ | self, audit_client |

### `TestAuditLedgerClientBatchOperations`

| Method | Async | Args |
|--------|-------|------|
| `test_batch_log_events_not_supported` | ✓ | self, audit_client, mock_web3_dependencies |

### `TestAuditLedgerClientHealthCheck`

| Method | Async | Args |
|--------|-------|------|
| `test_is_connected_when_connected` | ✓ | self, audit_client, mock_web3_dependencies |
| `test_is_connected_when_not_connected` | ✓ | self, audit_client |
| `test_is_connected_updates_state_on_disconnect` | ✓ | self, audit_client, mock_web3_dependencies |

### `TestAuditLedgerClientRetryMechanism`

| Method | Async | Args |
|--------|-------|------|
| `test_connect_retries_on_failure` | ✓ | self, audit_client, mock_web3_dependencies |

### `TestAuditLedgerClientUnsupportedDLT`

| Method | Async | Args |
|--------|-------|------|
| `test_hyperledger_fabric_not_supported` |  | self, mock_web3_dependencies, mocker |

### `TestAuditLedgerClientGasManagement`

| Method | Async | Args |
|--------|-------|------|
| `test_gas_estimation_failure_uses_default` | ✓ | self, audit_client, mock_web3_dependencies |
| `test_gas_cap_enforcement` | ✓ | self, audit_client, mock_web3_dependencies |

### `TestAuditLedgerClientConcurrency`

| Method | Async | Args |
|--------|-------|------|
| `test_concurrent_event_logging` | ✓ | self, audit_client, mock_web3_dependencies |

### `TestAuditLedgerClientSecretManagement`

| Method | Async | Args |
|--------|-------|------|
| `test_get_private_key_from_env` | ✓ | self, audit_client, mocker |
| `test_get_private_key_from_secrets_manager` | ✓ | self, audit_client, mocker |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `setup_environment` |  | mocker |
| `mock_web3_dependencies` |  | mocker |
| `audit_client` | ✓ | mock_web3_dependencies, mocker |

**Constants:** `logger`, `SAMPLE_ABI`, `TEST_ENV`

---

## tests/test_arbiter_models_feature_store_client.py
**Lines:** 667

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `setup_env` | ✓ | mocker |
| `test_tracer` |  |  |
| `in_memory_exporter` |  |  |
| `feature_client` | ✓ | mocker |
| `clear_metrics_and_traces` | ✓ | in_memory_exporter |
| `get_metric_value` |  | metric |
| `test_initialization_success` | ✓ | feature_client |
| `test_initialization_missing_repo_path` | ✓ | mocker |
| `test_connect_success` | ✓ | feature_client |
| `test_connect_idempotent` | ✓ | feature_client, caplog |
| `test_connect_failure` | ✓ | mocker |
| `test_disconnect_success` | ✓ | feature_client |
| `test_disconnect_idempotent` | ✓ | feature_client, caplog |
| `test_apply_feature_definitions_success` | ✓ | feature_client |
| `test_apply_feature_definitions_not_connected` | ✓ | feature_client |
| `test_apply_feature_definitions_failure` | ✓ | feature_client, mocker |
| `test_ingest_features_success` | ✓ | feature_client |
| `test_ingest_features_invalid_data` | ✓ | feature_client |
| `test_ingest_features_failure` | ✓ | feature_client, mocker |
| `test_get_historical_features_success` | ✓ | feature_client |
| `test_get_historical_features_failure` | ✓ | feature_client, mocker |
| `test_get_online_features_success` | ✓ | feature_client |
| `test_get_online_features_failure` | ✓ | feature_client, mocker |
| `test_context_manager` | ✓ | feature_client |
| `test_retry_on_connect_failure` | ✓ | mocker |
| `test_retry_on_ingest_failure` | ✓ | feature_client, mocker |
| `test_concurrent_ingest` | ✓ | feature_client |
| `test_health_check_success` | ✓ | feature_client |
| `test_health_check_failure` | ✓ | feature_client, mocker |
| `test_validate_features_basic` | ✓ | feature_client |
| `test_flag_for_redaction` | ✓ | feature_client |

**Constants:** `pytestmark`, `logger`, `SAMPLE_ENV`

---

## tests/test_arbiter_models_knowledge_graph_db.py
**Lines:** 687

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `setup_env` | ✓ | mocker |
| `test_tracer` |  |  |
| `in_memory_exporter` |  |  |
| `kg_client` | ✓ | mocker |
| `clear_metrics_and_traces` | ✓ | in_memory_exporter |
| `get_metric_value` |  | metric |
| `test_initialization_success` | ✓ | kg_client |
| `test_initialization_no_password_error` | ✓ | mocker |
| `test_initialization_default_password_error` | ✓ | mocker |
| `test_initialization_dev_mode_no_password_warning` | ✓ | mocker, caplog |
| `test_initialization_dev_mode_default_password_warning` | ✓ | mocker, caplog |
| `test_connect_success` | ✓ | kg_client |
| `test_connect_idempotent` | ✓ | kg_client |
| `test_connect_failure` | ✓ | mocker |
| `test_disconnect_success` | ✓ | kg_client |
| `test_disconnect_idempotent` | ✓ | kg_client, caplog |
| `test_health_check_success` | ✓ | kg_client |
| `test_health_check_not_connected` | ✓ | kg_client |
| `test_health_check_failure` | ✓ | kg_client, mocker |
| `test_add_node_success` | ✓ | kg_client |
| `test_add_node_validation_failure` | ✓ | kg_client |
| `test_add_node_hashes_pii` | ✓ | kg_client, mocker |
| `test_add_relationship_success` | ✓ | kg_client |
| `test_find_related_facts_success` | ✓ | kg_client, mocker |
| `test_check_consistency_success` | ✓ | kg_client, mocker |
| `test_check_consistency_no_node` | ✓ | kg_client, mocker |
| `test_export_graph_success` | ✓ | kg_client, tmp_path, mocker |
| `test_import_graph_success` | ✓ | kg_client, tmp_path, mocker |
| `test_retry_on_connect_failure` | ✓ | mocker |
| `test_concurrent_add_node` | ✓ | kg_client |
| `test_audit_logging` | ✓ | kg_client, tmp_path |
| `test_context_manager` | ✓ | kg_client |
| `test_no_password_leak` | ✓ | kg_client, caplog |
| `test_execute_tx_sanitizes_sensitive_params` | ✓ | kg_client, caplog |

**Constants:** `logger`, `SAMPLE_ENV`

---

## tests/test_arbiter_models_merkle_tree.py
**Lines:** 573

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `test_tracer` |  |  |
| `in_memory_exporter` |  |  |
| `clear_metrics_and_traces` | ✓ | in_memory_exporter |
| `merkle_tree` | ✓ |  |
| `get_metric_value` |  | metric |
| `test_initialization_success` | ✓ | merkle_tree |
| `test_initialization_with_leaves` | ✓ |  |
| `test_initialization_with_store_raw` | ✓ |  |
| `test_add_leaf_success` | ✓ | merkle_tree |
| `test_add_leaf_bytes` | ✓ | merkle_tree |
| `test_add_leaves_success` | ✓ | merkle_tree |
| `test_get_root_success` | ✓ | merkle_tree |
| `test_get_root_empty_tree` | ✓ | merkle_tree |
| `test_get_proof_success` | ✓ | merkle_tree |
| `test_get_proof_invalid_index` | ✓ | merkle_tree |
| `test_get_proof_negative_index` | ✓ | merkle_tree |
| `test_get_proof_empty_tree` | ✓ | merkle_tree |
| `test_verify_proof_success` | ✓ | merkle_tree |
| `test_verify_proof_with_bytes` | ✓ | merkle_tree |
| `test_verify_proof_tampered` | ✓ | merkle_tree |
| `test_verify_proof_malformed` | ✓ |  |
| `test_verify_proof_invalid_position` | ✓ |  |
| `test_verify_proof_missing_fields` | ✓ |  |
| `test_save_success` | ✓ | merkle_tree, tmp_path |
| `test_save_with_store_raw` | ✓ | tmp_path |
| `test_load_success` | ✓ | tmp_path |
| `test_load_legacy_format` | ✓ | tmp_path |
| `test_load_file_not_found` | ✓ | tmp_path, caplog |
| `test_load_corrupted_file` | ✓ | tmp_path, caplog |
| `test_concurrent_add_leaves` | ✓ | merkle_tree |
| `test_retry_on_save_file_error` | ✓ | merkle_tree, tmp_path, mocker |
| `test_retry_on_load_file_error` | ✓ | tmp_path, mocker |
| `test_save_load_roundtrip` | ✓ | merkle_tree, tmp_path |
| `test_large_batch_with_offload_threshold` | ✓ | mocker |
| `test_properties` | ✓ | merkle_tree |

**Constants:** `logger`

---

## tests/test_arbiter_models_meta_learning_data_store.py
**Lines:** 626

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `get_metric_value` |  | metric |
| `setup_env` | ✓ | mocker |
| `test_tracer` |  |  |
| `in_memory_exporter` |  |  |
| `inmemory_store` | ✓ |  |
| `redis_store` | ✓ | mocker |
| `clear_metrics_and_traces` | ✓ | in_memory_exporter |
| `test_initialization_success` | ✓ | store_type, inmemory_store, redis_store |
| `test_add_record_success` | ✓ | store_type, inmemory_store, redis_store |
| `test_add_record_duplicate` | ✓ | store_type, inmemory_store, redis_store |
| `test_add_record_validation_failure` | ✓ | store_type, inmemory_store, redis_store |
| `test_get_record_success` | ✓ | store_type, inmemory_store, redis_store |
| `test_get_record_not_found` | ✓ | store_type, inmemory_store, redis_store |
| `test_list_records_success` | ✓ | store_type, inmemory_store, redis_store |
| `test_update_record_success` | ✓ | store_type, inmemory_store, redis_store |
| `test_update_record_not_found` | ✓ | store_type, inmemory_store, redis_store |
| `test_delete_record_success` | ✓ | store_type, inmemory_store, redis_store |
| `test_delete_record_not_found` | ✓ | store_type, inmemory_store, redis_store |
| `test_max_records_limit` | ✓ | store_type, inmemory_store, redis_store |
| `test_encryption_decryption` | ✓ | store_type, inmemory_store, redis_store |
| `test_redis_connection_failure` | ✓ | redis_store, mocker |
| `test_concurrent_add_records` | ✓ | store_type, inmemory_store, redis_store |
| `test_redis_retry_on_connection_error` | ✓ | redis_store, mocker |
| `test_context_manager` | ✓ | store_type, inmemory_store, redis_store |
| `test_invalid_tag_validation` | ✓ | store_type, inmemory_store, redis_store |
| `test_filter_by_tags` | ✓ | store_type, inmemory_store, redis_store |

**Constants:** `logger`, `SAMPLE_ENV`, `SAMPLE_RECORD`

---

## tests/test_arbiter_models_models_e2e.py
**Lines:** 565

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `setup_e2e_env` | ✓ | mocker, tmp_path |
| `clear_metrics_and_traces` | ✓ |  |
| `test_e2e_multi_modal_workflow` | ✓ | setup_e2e_env, mocker |
| `test_e2e_error_handling` | ✓ | setup_e2e_env, mocker |
| `test_e2e_concurrency` | ✓ | setup_e2e_env, mocker |
| `test_e2e_invalid_data` | ✓ | setup_e2e_env |
| `test_e2e_data_integrity_flow` | ✓ | setup_e2e_env |
| `test_e2e_cross_component_transaction` | ✓ | setup_e2e_env |

**Constants:** `logger`, `SAMPLE_ENV`, `SAMPLE_IMAGE_ANALYSIS`, `SAMPLE_AUDIT_EVENT`

---

## tests/test_arbiter_models_multi_modal_schemas.py
**Lines:** 447

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `clear_logging` |  |  |
| `test_to_camel_function` |  |  |
| `test_base_config_sanitization` |  |  |
| `test_base_config_timestamp_utc` |  | caplog |
| `test_image_ocr_result_validation` |  |  |
| `test_image_captioning_result_validation` |  |  |
| `test_image_analysis_result_validation` |  |  |
| `test_audio_transcription_result_validation` |  |  |
| `test_audio_analysis_result_validation` |  |  |
| `test_video_summary_result_validation` |  |  |
| `test_video_analysis_result_validation` |  |  |
| `test_multi_modal_analysis_result` |  |  |
| `test_camel_case_serialization` |  |  |
| `test_camel_case_deserialization` |  |  |
| `test_sanitization_xss_protection` |  |  |
| `test_enum_usage` |  |  |
| `test_timestamp_default` |  |  |
| `test_invalid_field_extra` |  |  |
| `test_field_types_and_constraints` |  |  |
| `test_speaker_count_validation` |  |  |
| `test_id_pattern_validation` |  |  |
| `test_list_max_length` |  |  |

**Constants:** `logger`, `SAMPLE_IMAGE_ANALYSIS`, `SAMPLE_AUDIO_ANALYSIS`, `SAMPLE_VIDEO_ANALYSIS`

---

## tests/test_arbiter_models_postgres_client.py
**Lines:** 785

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `get_metric_value` |  | metric |
| `setup_env` | ✓ | mocker |
| `test_tracer` |  |  |
| `in_memory_exporter` |  |  |
| `pg_client` | ✓ | mocker |
| `clear_metrics_and_traces` | ✓ | in_memory_exporter |
| `test_initialization_success` | ✓ | pg_client |
| `test_connect_success` | ✓ | pg_client |
| `test_connect_idempotent` | ✓ | pg_client, caplog |
| `test_connect_failure` | ✓ | mocker |
| `test_disconnect_success` | ✓ | pg_client |
| `test_disconnect_idempotent` | ✓ | pg_client, caplog |
| `test_ensure_table_exists` | ✓ | pg_client |
| `test_save_success` | ✓ | pg_client, mocker |
| `test_save_many_success` | ✓ | pg_client, mocker |
| `test_load_success` | ✓ | pg_client, mocker |
| `test_load_all_success` | ✓ | pg_client, mocker |
| `test_update_success` | ✓ | pg_client, mocker |
| `test_delete_success` | ✓ | pg_client, mocker |
| `test_retry_on_connect_failure` | ✓ | mocker |
| `test_concurrent_save` | ✓ | pg_client, mocker |
| `test_jsonb_handling` | ✓ | pg_client, mocker |
| `test_ssl_mode` | ✓ | mocker |
| `test_context_manager` | ✓ | pg_client, mocker |
| `test_ping_success` | ✓ | pg_client |
| `test_ping_no_pool` | ✓ | pg_client |
| `test_agent_knowledge_operations` | ✓ | pg_client, mocker |
| `test_update_jsonb_operations` | ✓ | pg_client, mocker |

**Constants:** `logger`, `SAMPLE_ENV`, `SAMPLE_FEEDBACK_DATA`, `SAMPLE_AGENT_KNOWLEDGE_DATA`

---

## tests/test_arbiter_models_redis_client.py
**Lines:** 674

### `TestInitialization`

| Method | Async | Args |
|--------|-------|------|
| `test_initialization_with_defaults` |  | self, mocker |
| `test_initialization_with_custom_url` |  | self, mocker |
| `test_initialization_with_ssl_url` |  | self, mocker |
| `test_initialization_with_ssl_env` |  | self, mocker |
| `test_initialization_invalid_url` |  | self, mocker |
| `test_initialization_prod_requires_ssl` |  | self, mocker |

### `TestConnection`

| Method | Async | Args |
|--------|-------|------|
| `test_connect_success` | ✓ | self, redis_client |
| `test_connect_idempotent` | ✓ | self, redis_client, caplog |
| `test_connect_failure` | ✓ | self, redis_client, mocker |
| `test_disconnect_success` | ✓ | self, redis_client |
| `test_disconnect_idempotent` | ✓ | self, redis_client, caplog |
| `test_context_manager` | ✓ | self, redis_client |
| `test_ping_success` | ✓ | self, redis_client |
| `test_ping_when_disconnected` | ✓ | self, redis_client |
| `test_reconnect` | ✓ | self, redis_client |

### `TestCRUDOperations`

| Method | Async | Args |
|--------|-------|------|
| `test_set_success` | ✓ | self, redis_client |
| `test_set_with_json_value` | ✓ | self, redis_client |
| `test_set_with_expiration` | ✓ | self, redis_client |
| `test_set_invalid_key` | ✓ | self, redis_client |
| `test_set_invalid_expiration` | ✓ | self, redis_client |
| `test_set_oversized_value` | ✓ | self, redis_client |
| `test_set_non_serializable_value` | ✓ | self, redis_client |
| `test_get_success` | ✓ | self, redis_client |
| `test_get_json_value` | ✓ | self, redis_client |
| `test_get_nonexistent_key` | ✓ | self, redis_client |
| `test_delete_success` | ✓ | self, redis_client |
| `test_delete_no_keys` | ✓ | self, redis_client |
| `test_setex_success` | ✓ | self, redis_client |

### `TestBatchOperations`

| Method | Async | Args |
|--------|-------|------|
| `test_mset_success` | ✓ | self, redis_client |
| `test_mset_empty_mapping` | ✓ | self, redis_client |
| `test_mset_invalid_key` | ✓ | self, redis_client |
| `test_mget_success` | ✓ | self, redis_client |
| `test_mget_empty_keys` | ✓ | self, redis_client |

### `TestDistributedLocking`

| Method | Async | Args |
|--------|-------|------|
| `test_lock_creation` | ✓ | self, redis_client |
| `test_lock_invalid_name` | ✓ | self, redis_client |
| `test_lock_invalid_timeout` | ✓ | self, redis_client |
| `test_lock_acquisition_success` | ✓ | self, redis_client |
| `test_lock_context_manager` | ✓ | self, redis_client |
| `test_lock_not_connected` | ✓ | self |

### `TestRetryAndErrorHandling`

| Method | Async | Args |
|--------|-------|------|
| `test_retry_on_connection_error` | ✓ | self, redis_client, mocker |
| `test_retry_exhaustion` | ✓ | self, redis_client, mocker |
| `test_operation_without_connection` | ✓ | self, redis_client |

### `TestHealthCheckAndStats`

| Method | Async | Args |
|--------|-------|------|
| `test_health_check_task_creation` | ✓ | self, redis_client |
| `test_health_check_task_cancellation` | ✓ | self, redis_client |
| `test_update_redis_stats` | ✓ | self, redis_client |

### `TestSecurityAndValidation`

| Method | Async | Args |
|--------|-------|------|
| `test_key_length_validation` | ✓ | self, redis_client |
| `test_value_size_validation` | ✓ | self, redis_client |
| `test_key_redaction_in_logs` |  | self, caplog |

### `TestIntegration`

| Method | Async | Args |
|--------|-------|------|
| `test_complete_crud_workflow` | ✓ | self, redis_client |
| `test_batch_operations_workflow` | ✓ | self, redis_client |
| `test_expiration_workflow` | ✓ | self, redis_client |

### `TestPerformance`

| Method | Async | Args |
|--------|-------|------|
| `test_connection_pooling` | ✓ | self, redis_client, mocker |
| `test_concurrent_operations` | ✓ | self, redis_client |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `setup_env` | ✓ | mocker |
| `test_tracer` |  |  |
| `in_memory_exporter` |  |  |
| `redis_client` | ✓ | mocker |
| `clear_metrics_and_traces` | ✓ | in_memory_exporter |

**Constants:** `logger`, `SAMPLE_ENV`, `SAMPLE_KEY`, `SAMPLE_VALUE`, `SAMPLE_JSON_VALUE`, `SAMPLE_LOCK_NAME`

---

## tests/test_arbiter_monitoring.py
**Lines:** 332

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `tmp_log_file` |  | tmp_path |
| `mock_logger` |  |  |
| `monitor` |  | tmp_log_file, mock_logger |
| `test_initialization_defaults` |  |  |
| `test_initialization_with_params` |  | tmp_log_file, mock_logger |
| `test_log_action_basic` |  | monitor |
| `test_log_action_with_metadata` |  | monitor |
| `test_log_action_global_metadata` |  | monitor |
| `test_log_action_tamper_evident` |  | monitor |
| `test_log_action_observers` |  | monitor |
| `test_thread_safety` |  | monitor, num_threads |
| `test_prune_old_logs` |  | monitor |
| `test_write_to_file_jsonl` |  | monitor, tmp_log_file |
| `test_write_to_file_json` |  | monitor, tmp_log_file |
| `test_write_to_file_plaintext` |  | monitor, tmp_log_file |
| `test_log_rotation` |  | monitor, tmp_log_file |
| `test_json_write_limit` |  | monitor, caplog |
| `test_search_all` |  | monitor |
| `test_search_filtered` |  | monitor |
| `test_export_log_jsonl` | ✓ | monitor, tmp_path |
| `test_export_log_invalid_format` | ✓ | monitor, tmp_path |
| `test_export_log_error` | ✓ | monitor, tmp_path, caplog |
| `test_high_volume_logging` |  | monitor |
| `test_get_recent_events` |  | monitor |
| `test_explain_decision` |  | monitor |
| `test_explain_decision_not_found` |  | monitor |
| `test_detect_anomalies` | ✓ | monitor |
| `test_generate_reports` |  | monitor |
| `test_health_check` | ✓ | monitor, tmp_log_file |

---

## tests/test_arbiter_otel_config.py
**Lines:** 534

### `TestEnvironment`

| Method | Async | Args |
|--------|-------|------|
| `test_environment_values` |  | self |
| `test_current_detects_testing` |  | self |
| `test_current_detects_pytest_module` |  | self |

### `TestCollectorEndpoint`

| Method | Async | Args |
|--------|-------|------|
| `test_endpoint_initialization` |  | self |
| `test_endpoint_with_custom_values` |  | self |
| `test_is_reachable_success` |  | self, mock_socket_class |
| `test_is_reachable_failure` |  | self, mock_socket_class |
| `test_is_reachable_with_custom_port` |  | self, mock_socket_class |
| `test_is_reachable_http_default_port` |  | self, mock_socket_class |

### `TestSamplingStrategy`

| Method | Async | Args |
|--------|-------|------|
| `test_default_initialization` |  | self |
| `test_should_sample_error` |  | self |
| `test_should_sample_high_latency` |  | self |
| `test_should_sample_operation_override` |  | self |
| `test_should_sample_service_override` |  | self |

### `TestOpenTelemetryConfig`

| Method | Async | Args |
|--------|-------|------|
| `setup_method` |  | self |
| `test_singleton_pattern` |  | self |
| `test_direct_instantiation_raises_error` |  | self |
| `test_service_configuration_from_env` |  | self |
| `test_testing_environment_uses_noop_tracer` |  | self |
| `test_missing_opentelemetry_uses_noop_tracer` |  | self |
| `test_endpoints_from_env` |  | self |
| `test_parse_headers` |  | self |
| `test_production_requires_tls` |  | self, mock_env |
| `test_discover_from_consul` |  | self |
| `test_shutdown` |  | self |

### `TestNoOpImplementations`

| Method | Async | Args |
|--------|-------|------|
| `test_noop_span_interface` |  | self |
| `test_noop_tracer_interface` |  | self |

### `TestModuleFunctions`

| Method | Async | Args |
|--------|-------|------|
| `setup_method` |  | self |
| `test_get_tracer_initializes_config` |  | self |
| `test_get_tracer_with_name` |  | self |
| `test_trace_operation_decorator_async` | ✓ | self |
| `test_trace_operation_decorator_sync` |  | self |
| `test_trace_operation_decorator_with_exception` |  | self |
| `test_trace_operation_decorator_async_with_exception` | ✓ | self |

### `TestResourceCreation`

| Method | Async | Args |
|--------|-------|------|
| `test_create_resource_with_aws_metadata` |  | self, mock_pid, mock_hostname |
| `test_create_resource_with_k8s_metadata` |  | self |

### `TestTraceContext`

| Method | Async | Args |
|--------|-------|------|
| `test_trace_context_with_tracer` |  | self |
| `test_trace_context_without_tracer` |  | self |

### `TestThreadSafety`

| Method | Async | Args |
|--------|-------|------|
| `setup_method` |  | self |
| `test_concurrent_initialization` |  | self |

---

## tests/test_arbiter_plugin_config.py
**Lines:** 159

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `expected_plugins` |  |  |
| `test_get_plugins_returns_copy` |  | expected_plugins |
| `test_sandboxed_plugins_is_copy` |  | expected_plugins |
| `test_validate_valid` |  |  |
| `test_validate_invalid_key_type` |  |  |
| `test_validate_invalid_value_type` |  |  |
| `test_validate_at_import_time` |  |  |
| `test_plugin_registry_immutability` |  |  |
| `test_snake_case_keys` |  |  |
| `test_valid_dotted_paths` |  |  |
| `test_expected_plugins_present` |  | expected_plugins |
| `test_no_duplicate_keys_insensitive` |  |  |
| `test_empty_registry` |  |  |
| `test_get_plugins_independent_copies` |  |  |

---

## tests/test_arbiter_plugin_registry.py
**Lines:** 766

### `MockPlugin` (PluginBase)

| Method | Async | Args |
|--------|-------|------|
| `initialize` | ✓ | self |
| `start` | ✓ | self |
| `stop` | ✓ | self |
| `health_check` | ✓ | self |
| `get_capabilities` | ✓ | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `reset_registry` |  | tmp_path |
| `mock_anthropic` |  |  |
| `mock_app` |  |  |
| `mock_test_generation_onboard` |  |  |
| `mock_stable_baselines3` |  |  |
| `mock_omnicore_plugin_registry` |  |  |
| `mock_test_generation_utils` |  |  |
| `mock_logger` |  |  |
| `mock_plugin_class` |  |  |
| `test_plugin_meta` |  |  |
| `test_plugin_base_abstract` |  |  |
| `test_plugin_registry_singleton` |  |  |
| `test_register_decorator` | ✓ | reset_registry |
| `test_register_dependencies` | ✓ | reset_registry |
| `test_register_duplicate_lower_version` |  | reset_registry |
| `test_unregister` | ✓ | reset_registry, mock_plugin_class |
| `test_get_unregistered` |  | reset_registry |
| `test_list_plugins` |  | reset_registry |
| `test_list_plugins_by_kind` |  | reset_registry |
| `test_export_registry` |  | reset_registry, mock_plugin_class |
| `test_health_check` | ✓ | reset_registry |
| `test_health_check_unhealthy` | ✓ | reset_registry |
| `test_health_check_all` | ✓ | reset_registry |
| `test_initialize_all` | ✓ | reset_registry |
| `test_start_all` | ✓ | reset_registry |
| `test_stop_all` | ✓ | reset_registry |
| `test_dependency_validation_missing` |  | reset_registry |
| `test_dependency_version_conflict` |  | reset_registry |
| `test_circular_dependency` |  | reset_registry |
| `test_reload` | ✓ | reset_registry |
| `test_sandboxed_plugin` |  | reset_registry |
| `test_async_context_manager` | ✓ | reset_registry |
| `test_event_hook` |  | reset_registry |
| `test_persist_and_load` |  | reset_registry, mocker, mock_plugin_class |
| `test_validate_name` |  | reset_registry |
| `test_validate_version` |  | reset_registry |
| `test_quarantined_plugin_health_check` | ✓ | reset_registry |
| `test_health_check_nonexistent` | ✓ | reset_registry |
| `test_plugin_registry_constant` |  |  |
| `test_register_instance` |  | reset_registry |

---

## tests/test_arbiter_plugins_anthropic_adapter.py
**Lines:** 366

### `TestAnthropicAdapter`

| Method | Async | Args |
|--------|-------|------|
| `valid_settings` |  | self |
| `adapter` | ✓ | self, valid_settings |
| `test_init_with_valid_settings` |  | self, valid_settings |
| `test_init_missing_api_key` |  | self |
| `test_init_with_none_client` |  | self, valid_settings |
| `test_generate_empty_prompt` | ✓ | self, adapter |
| `test_generate_none_prompt` | ✓ | self, adapter |
| `test_generate_prompt_too_long` | ✓ | self, adapter |
| `test_generate_invalid_max_tokens` | ✓ | self, adapter |
| `test_generate_invalid_temperature` | ✓ | self, adapter |
| `test_generate_success` | ✓ | self, adapter |
| `test_generate_retry_error` | ✓ | self, adapter |
| `test_generate_timeout_error` | ✓ | self, adapter |
| `test_generate_auth_error` | ✓ | self, adapter |
| `test_generate_rate_limit_error` | ✓ | self, adapter |
| `test_generate_generic_api_error` | ✓ | self, adapter |
| `test_generate_unexpected_error` | ✓ | self, adapter |
| `test_circuit_breaker_opens_after_threshold` | ✓ | self, adapter |
| `test_circuit_breaker_half_open_after_timeout` | ✓ | self, adapter |
| `test_circuit_breaker_resets_on_success` | ✓ | self, adapter |
| `test_sanitize_prompt_removes_email` |  | self, adapter |
| `test_sanitize_prompt_removes_phone` |  | self, adapter |
| `test_sanitize_prompt_removes_ssn` |  | self, adapter |
| `test_sanitize_prompt_removes_control_chars` |  | self, adapter |
| `test_async_context_manager` | ✓ | self, valid_settings |
| `test_context_manager_handles_close_error` | ✓ | self, valid_settings |
| `test_metrics_recorded_on_success` | ✓ | self, adapter |
| `test_metrics_recorded_on_failure` | ✓ | self, adapter |

---

## tests/test_arbiter_plugins_default_multimodal_providers.py
**Lines:** 471

### `TestPluginRegistry`

| Method | Async | Args |
|--------|-------|------|
| `setup_method` |  | self |
| `test_register_processor_success` |  | self |
| `test_register_processor_invalid_modality` |  | self |
| `test_register_processor_duplicate` |  | self |
| `test_unregister_processor_success` |  | self |
| `test_unregister_processor_not_registered` |  | self |
| `test_get_processor_success` |  | self |
| `test_get_processor_not_registered` |  | self |
| `test_get_processor_config_validation_error` |  | self |
| `test_get_supported_providers` |  | self |
| `test_get_supported_providers_invalid_modality` |  | self |

### `TestConfigSchemas`

| Method | Async | Args |
|--------|-------|------|
| `test_default_image_processor_config_valid` |  | self |
| `test_default_image_processor_config_defaults` |  | self |
| `test_default_image_processor_config_invalid` |  | self |
| `test_default_audio_processor_config` |  | self |
| `test_default_video_processor_config` |  | self |
| `test_default_text_processor_config` |  | self |

### `TestDefaultImageProcessor`

| Method | Async | Args |
|--------|-------|------|
| `processor` |  | self |
| `test_process_success_with_bytes` | ✓ | self, processor |
| `test_process_success_with_base64` | ✓ | self, processor |
| `test_process_invalid_none_input` | ✓ | self, processor |
| `test_process_invalid_file_path` | ✓ | self, processor |
| `test_process_exceeds_max_size` | ✓ | self, processor |
| `test_process_unsupported_format` | ✓ | self, processor |
| `test_process_with_operation_id` | ✓ | self, processor |
| `test_health_check` | ✓ | self, processor |

### `TestDefaultAudioProcessor`

| Method | Async | Args |
|--------|-------|------|
| `processor` |  | self |
| `test_process_success_with_wav` | ✓ | self, processor |
| `test_process_success_with_mp3` | ✓ | self, processor |
| `test_process_unsupported_format` | ✓ | self, processor |
| `test_health_check` | ✓ | self, processor |

### `TestDefaultVideoProcessor`

| Method | Async | Args |
|--------|-------|------|
| `processor` |  | self |
| `test_process_success_with_mp4` | ✓ | self, processor |
| `test_process_success_with_avi` | ✓ | self, processor |
| `test_process_unsupported_format` | ✓ | self, processor |
| `test_health_check` | ✓ | self, processor |

### `TestDefaultTextProcessor`

| Method | Async | Args |
|--------|-------|------|
| `processor` |  | self |
| `test_process_success` | ✓ | self, processor |
| `test_process_empty_text` | ✓ | self, processor |
| `test_process_invalid_type` | ✓ | self, processor |
| `test_process_exceeds_max_length` | ✓ | self, processor |
| `test_health_check` | ✓ | self, processor |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `get_or_create` |  | metric |
| `mock_metrics` |  |  |

---

## tests/test_arbiter_plugins_e2e_multimodal.py
**Lines:** 490

### `TestE2EMultiModalSystem`

| Method | Async | Args |
|--------|-------|------|
| `plugin_with_config` | ✓ | self |
| `test_complete_workflow_all_modalities` | ✓ | self, plugin_with_config |
| `test_pii_masking_e2e` | ✓ | self, plugin_with_config |
| `test_circuit_breaker_e2e` | ✓ | self, plugin_with_config |
| `test_hooks_e2e` | ✓ | self, plugin_with_config |
| `test_parallel_processing` | ✓ | self, plugin_with_config |
| `test_config_from_yaml_e2e` | ✓ | self |
| `test_custom_provider_registration_e2e` | ✓ | self |
| `test_error_propagation_e2e` | ✓ | self, plugin_with_config |
| `test_context_manager_e2e` | ✓ | self |
| `test_health_check_e2e` | ✓ | self, plugin_with_config |
| `test_capabilities_e2e` | ✓ | self, plugin_with_config |
| `test_provider_switching_e2e` | ✓ | self, plugin_with_config |
| `test_model_version_tracking_e2e` | ✓ | self, plugin_with_config |
| `test_complete_dummy_plugin_e2e` | ✓ | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `get_or_create` |  | metric |
| `mock_metrics` |  |  |

---

## tests/test_arbiter_plugins_gemini_adapter.py
**Lines:** 499

### `TestGeminiAdapter`

| Method | Async | Args |
|--------|-------|------|
| `valid_settings` |  | self |
| `adapter` | ✓ | self, valid_settings |
| `test_init_with_valid_settings` |  | self, valid_settings |
| `test_init_missing_api_key` |  | self |
| `test_init_with_llm_client_failure` |  | self, valid_settings |
| `test_init_with_none_client` |  | self, valid_settings |
| `test_init_with_default_values` |  | self |
| `test_generate_empty_prompt` | ✓ | self, adapter |
| `test_generate_none_prompt` | ✓ | self, adapter |
| `test_generate_prompt_too_long` | ✓ | self, adapter |
| `test_generate_invalid_max_tokens` | ✓ | self, adapter |
| `test_generate_invalid_temperature` | ✓ | self, adapter |
| `test_generate_success` | ✓ | self, adapter |
| `test_generate_with_high_temperature` | ✓ | self, adapter |
| `test_generate_retry_error` | ✓ | self, adapter |
| `test_generate_timeout_error` | ✓ | self, adapter |
| `test_generate_google_auth_error` | ✓ | self, adapter |
| `test_generate_google_rate_limit_error` | ✓ | self, adapter |
| `test_generate_google_api_error_with_status` | ✓ | self, adapter |
| `test_generate_google_api_error_no_code` | ✓ | self, adapter |
| `test_generate_unexpected_llm_error` | ✓ | self, adapter |
| `test_generate_critical_error` | ✓ | self, adapter |
| `test_circuit_breaker_opens_after_threshold` | ✓ | self, adapter |
| `test_circuit_breaker_half_open_after_timeout` | ✓ | self, adapter |
| `test_circuit_breaker_stays_closed_on_success` | ✓ | self, adapter |
| `test_circuit_breaker_half_open_to_open_on_failure` | ✓ | self, adapter |
| `test_sanitize_prompt_masks_email` |  | self, adapter |
| `test_sanitize_prompt_masks_phone` |  | self, adapter |
| `test_sanitize_prompt_masks_ssn` |  | self, adapter |
| `test_sanitize_prompt_masks_credit_card` |  | self, adapter |
| `test_sanitize_prompt_masks_address` |  | self, adapter |
| `test_sanitize_prompt_custom_pii_pattern` |  | self, valid_settings |
| `test_sanitize_prompt_removes_control_chars` |  | self, adapter |
| `test_async_context_manager` | ✓ | self, valid_settings |
| `test_context_manager_handles_close_error` | ✓ | self, valid_settings |
| `test_metrics_recorded_on_success` | ✓ | self, adapter |
| `test_metrics_recorded_on_failure` | ✓ | self, adapter |
| `test_metrics_circuit_breaker_error` | ✓ | self, adapter |

---

## tests/test_arbiter_plugins_interface.py
**Lines:** 418

### `TestProcessingResult`

| Method | Async | Args |
|--------|-------|------|
| `test_processing_result_success` |  | self |
| `test_processing_result_failure` |  | self |
| `test_processing_result_confidence_validation` |  | self |
| `test_processing_result_extra_fields_allowed` |  | self |

### `TestAnalysisResults`

| Method | Async | Args |
|--------|-------|------|
| `test_image_analysis_result` |  | self |
| `test_audio_analysis_result` |  | self |
| `test_video_analysis_result` |  | self |
| `test_text_analysis_result` |  | self |
| `test_analysis_result_invalid_state` |  | self |
| `test_analysis_result_repr` |  | self |

### `TestDummyMultiModalPlugin`

| Method | Async | Args |
|--------|-------|------|
| `plugin` |  | self |
| `test_plugin_initialization` |  | self, plugin |
| `test_analyze_image` |  | self, plugin |
| `test_analyze_image_invalid_input` |  | self, plugin |
| `test_analyze_audio` |  | self, plugin |
| `test_analyze_video` |  | self, plugin |
| `test_analyze_text` |  | self, plugin |
| `test_analyze_text_invalid_input` |  | self, plugin |
| `test_supported_modalities` |  | self, plugin |
| `test_model_info` |  | self, plugin |
| `test_context_manager` |  | self, plugin |
| `test_async_context_manager` | ✓ | self, plugin |
| `test_async_methods_not_implemented` | ✓ | self, plugin |
| `test_metrics_tracking` |  | self, mock_sleep, plugin |

### `TestExceptionClasses`

| Method | Async | Args |
|--------|-------|------|
| `test_multimodal_exception` |  | self |
| `test_invalid_input_error` |  | self |
| `test_configuration_error` |  | self |
| `test_provider_not_available_error` |  | self |
| `test_processing_error` |  | self |

### `TestAbstractInterfaces`

| Method | Async | Args |
|--------|-------|------|
| `test_image_processor_abstract` |  | self |
| `test_audio_processor_abstract` |  | self |
| `test_video_processor_abstract` |  | self |
| `test_text_processor_abstract` |  | self |
| `test_multimodal_plugin_interface_abstract` |  | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `get_or_create` |  | metric |
| `mock_metrics` |  |  |

---

## tests/test_arbiter_plugins_llm_client.py
**Lines:** 519

### `TestLLMClient`

| Method | Async | Args |
|--------|-------|------|
| `valid_openai_config` |  | self |
| `valid_anthropic_config` |  | self |
| `valid_gemini_config` |  | self |
| `valid_ollama_config` |  | self |
| `test_init_invalid_provider` |  | self |
| `test_init_invalid_model` |  | self |
| `test_init_invalid_timeout` |  | self |
| `test_init_invalid_retry_attempts` |  | self |
| `test_init_invalid_retry_backoff` |  | self |
| `test_init_missing_api_key` |  | self |
| `test_init_unsupported_provider` |  | self |
| `test_init_openai_success` |  | self, mock_openai |
| `test_init_anthropic_success` |  | self, mock_anthropic |
| `test_init_gemini_success` |  | self, mock_model, mock_configure |
| `test_init_ollama_success` |  | self |
| `test_generate_text_invalid_prompt` | ✓ | self |
| `test_generate_text_invalid_max_tokens` | ✓ | self |
| `test_generate_text_invalid_temperature` | ✓ | self |
| `test_sanitize_prompt` |  | self |
| `test_circuit_breaker_opens_after_threshold` | ✓ | self |
| `test_circuit_breaker_half_open_after_timeout` |  | self |
| `test_openai_generate_success` | ✓ | self |
| `test_anthropic_generate_success` | ✓ | self |
| `test_ollama_generate_success` | ✓ | self |
| `test_aclose_session_openai` | ✓ | self |
| `test_aclose_session_anthropic` | ✓ | self |

### `TestLoadBalancedLLMClient`

| Method | Async | Args |
|--------|-------|------|
| `providers_config` |  | self |
| `mock_providers` |  | self |
| `test_init_no_providers` |  | self |
| `test_init_invalid_provider_config` |  | self, mock_providers |
| `test_init_success` |  | self, providers_config, mock_providers |
| `test_weighted_distribution` |  | self, providers_config, mock_providers |
| `test_generate_text_success` | ✓ | self, providers_config, mock_providers |
| `test_failover_to_next_provider` | ✓ | self, providers_config, mock_providers |
| `test_provider_quarantine` |  | self, providers_config, mock_providers |
| `test_provider_recovery_from_quarantine` |  | self, providers_config, mock_providers |
| `test_all_providers_fail` | ✓ | self, providers_config, mock_providers |
| `test_close_all_sessions` | ✓ | self, providers_config, mock_providers |

---

## tests/test_arbiter_plugins_multi_modal_config.py
**Lines:** 411

### `TestCircuitBreakerConfig`

| Method | Async | Args |
|--------|-------|------|
| `test_default_values` |  | self |
| `test_custom_values` |  | self |

### `TestProcessorConfig`

| Method | Async | Args |
|--------|-------|------|
| `test_default_values` |  | self |
| `test_custom_values` |  | self |

### `TestSecurityConfig`

| Method | Async | Args |
|--------|-------|------|
| `test_default_values` |  | self |
| `test_input_validation_rules_valid` |  | self |
| `test_input_validation_rules_invalid_max_size` |  | self |
| `test_input_validation_rules_invalid_max_length` |  | self |
| `test_output_validation_rules_invalid_confidence` |  | self |
| `test_valid_pii_patterns` |  | self |
| `test_invalid_pii_patterns` |  | self |

### `TestAuditLogConfig`

| Method | Async | Args |
|--------|-------|------|
| `test_default_values` |  | self |
| `test_valid_log_levels` |  | self |
| `test_invalid_log_level` |  | self |
| `test_valid_destinations` |  | self |
| `test_invalid_destination` |  | self |
| `test_case_insensitive_log_level` |  | self |

### `TestMetricsConfig`

| Method | Async | Args |
|--------|-------|------|
| `test_default_values` |  | self |
| `test_custom_port` |  | self |

### `TestCacheConfig`

| Method | Async | Args |
|--------|-------|------|
| `test_default_values` |  | self |
| `test_custom_values` |  | self |

### `TestComplianceConfig`

| Method | Async | Args |
|--------|-------|------|
| `test_default_values` |  | self |
| `test_valid_compliance_mapping` |  | self |
| `test_invalid_compliance_control_id` |  | self |

### `TestMultiModalConfig`

| Method | Async | Args |
|--------|-------|------|
| `test_default_values` |  | self |
| `test_custom_values` |  | self |
| `test_nested_configuration` |  | self |
| `test_load_from_yaml_file` |  | self |
| `test_environment_variable_override` |  | self |
| `test_yaml_and_env_precedence` |  | self |
| `test_invalid_yaml_file` |  | self |
| `test_nonexistent_config_file` |  | self |
| `test_invalid_env_var_conversion` |  | self |
| `test_complex_nested_validation` |  | self |

---

## tests/test_arbiter_plugins_multi_modal_plugin.py
**Lines:** 463

### `TestAuditLogger`

| Method | Async | Args |
|--------|-------|------|
| `test_audit_logger_initialization` |  | self |
| `test_log_event` |  | self |

### `TestMetricsCollector`

| Method | Async | Args |
|--------|-------|------|
| `test_metrics_collector_disabled` |  | self |
| `test_metrics_collector_enabled` |  | self, mock_histogram, mock_counter |

### `TestCacheManager`

| Method | Async | Args |
|--------|-------|------|
| `test_cache_disabled` | ✓ | self |
| `test_cache_connection_failure` | ✓ | self |
| `test_cache_operations` | ✓ | self |

### `TestInputValidator`

| Method | Async | Args |
|--------|-------|------|
| `test_validate_text_input` |  | self |
| `test_validate_binary_input` |  | self |
| `test_pii_masking` |  | self |

### `TestOutputValidator`

| Method | Async | Args |
|--------|-------|------|
| `test_validate_output_success` |  | self |
| `test_validate_output_confidence_too_low` |  | self |

### `TestMultiModalPlugin`

| Method | Async | Args |
|--------|-------|------|
| `base_config` |  | self |
| `plugin` | ✓ | self, base_config |
| `test_plugin_initialization` | ✓ | self, base_config |
| `test_process_image_success` | ✓ | self, plugin |
| `test_process_disabled_modality` | ✓ | self, plugin |
| `test_circuit_breaker_functionality` | ✓ | self, plugin |
| `test_hooks_execution` | ✓ | self, plugin |
| `test_caching_functionality` | ✓ | self, base_config |
| `test_get_supported_providers` |  | self, plugin |
| `test_set_default_provider` |  | self, plugin |
| `test_update_model_version` |  | self, plugin |
| `test_context_manager` | ✓ | self, base_config |
| `test_health_check` | ✓ | self, plugin |
| `test_get_capabilities` | ✓ | self, plugin |

### `TestSandboxExecutor`

| Method | Async | Args |
|--------|-------|------|
| `test_sandbox_disabled` | ✓ | self |
| `test_sandbox_sync_function` | ✓ | self |

---

## tests/test_arbiter_plugins_ollama_adapter.py
**Lines:** 398

### `TestOllamaAdapter`

| Method | Async | Args |
|--------|-------|------|
| `valid_settings` |  | self |
| `adapter` | ✓ | self, valid_settings |
| `test_init_with_valid_settings` |  | self, valid_settings |
| `test_init_with_default_model` |  | self |
| `test_init_with_empty_model_name` |  | self |
| `test_init_with_minimal_settings` |  | self |
| `test_circuit_breaker_check_when_closed` |  | self, adapter |
| `test_circuit_breaker_check_when_open_and_timeout_not_reached` |  | self, adapter |
| `test_circuit_breaker_check_transitions_to_half_open` |  | self, adapter |
| `test_circuit_breaker_update_on_success` |  | self, adapter |
| `test_circuit_breaker_opens_after_threshold` |  | self, adapter |
| `test_circuit_breaker_half_open_to_open_on_failure` |  | self, adapter |
| `test_health_check_success` | ✓ | self, adapter |
| `test_health_check_failure` | ✓ | self, adapter |
| `test_generate_success` | ✓ | self, adapter |
| `test_generate_with_pii_masking` | ✓ | self, adapter |
| `test_generate_without_pii_masking` | ✓ | self, adapter |
| `test_generate_timeout_error` | ✓ | self, adapter |
| `test_generate_connection_error` | ✓ | self, adapter |
| `test_generate_auth_error` | ✓ | self, adapter |
| `test_generate_rate_limit_error` | ✓ | self, adapter |
| `test_generate_generic_api_error` | ✓ | self, adapter |
| `test_generate_unexpected_error` | ✓ | self, adapter |
| `test_async_context_manager` | ✓ | self, valid_settings |
| `test_context_manager_with_exception` | ✓ | self, valid_settings |
| `test_metrics_recorded_on_success` | ✓ | self, adapter |
| `test_metrics_recorded_on_failure` | ✓ | self, adapter |

---

## tests/test_arbiter_plugins_openai_adapter.py
**Lines:** 395

### `TestOpenAIAdapter`

| Method | Async | Args |
|--------|-------|------|
| `valid_settings` |  | self |
| `adapter` | ✓ | self, valid_settings |
| `test_init_with_valid_settings` |  | self, valid_settings |
| `test_init_missing_api_key` |  | self |
| `test_init_with_default_values` |  | self |
| `test_circuit_breaker_closed_allows_requests` |  | self, adapter |
| `test_circuit_breaker_open_blocks_requests` |  | self, adapter |
| `test_circuit_breaker_transitions_to_half_open` |  | self, adapter |
| `test_circuit_breaker_closes_on_success` |  | self, adapter |
| `test_circuit_breaker_opens_after_threshold` |  | self, adapter |
| `test_health_check_success` | ✓ | self, adapter |
| `test_health_check_failure` | ✓ | self, adapter |
| `test_generate_success` | ✓ | self, adapter |
| `test_generate_with_pii_masking` | ✓ | self, adapter |
| `test_generate_without_pii_masking` | ✓ | self, adapter |
| `test_generate_timeout_error` | ✓ | self, adapter |
| `test_generate_auth_error` | ✓ | self, adapter |
| `test_generate_rate_limit_error` | ✓ | self, adapter |
| `test_generate_generic_api_error` | ✓ | self, adapter |
| `test_generate_unexpected_llm_client_error` | ✓ | self, adapter |
| `test_generate_critical_error` | ✓ | self, adapter |
| `test_async_context_manager` | ✓ | self, valid_settings |
| `test_context_manager_with_exception` | ✓ | self, valid_settings |
| `test_metrics_recorded_on_success` | ✓ | self, adapter |
| `test_metrics_recorded_on_failure` | ✓ | self, adapter |

---

## tests/test_arbiter_policy_circuit_breaker.py
**Lines:** 408

### `TestSanitizationFunctions`

| Method | Async | Args |
|--------|-------|------|
| `test_sanitize_log_message_none` |  | self |
| `test_sanitize_log_message_empty` |  | self |
| `test_sanitize_log_message_normal` |  | self |
| `test_sanitize_log_message_control_characters` |  | self |
| `test_sanitize_log_message_truncation` |  | self |
| `test_sanitize_provider_valid` |  | self |
| `test_sanitize_provider_invalid_characters` |  | self |
| `test_sanitize_provider_truncation` |  | self |

### `TestInMemoryBreakerStateManager`

| Method | Async | Args |
|--------|-------|------|
| `test_initial_state` | ✓ | self |
| `test_set_and_get_state` | ✓ | self |
| `test_state_lock` | ✓ | self |
| `test_close_method` | ✓ | self |

### `TestConfigValidation`

| Method | Async | Args |
|--------|-------|------|
| `test_validate_config_with_valid_config` |  | self, mock_config |
| `test_validate_config_with_invalid_types` |  | self |

### `TestBreakerStateManagement`

| Method | Async | Args |
|--------|-------|------|
| `test_get_breaker_state_creates_new` | ✓ | self, mock_config, cleanup_states |
| `test_get_breaker_state_returns_existing` | ✓ | self, mock_config, cleanup_states |
| `test_invalid_provider_names` | ✓ | self, mock_config, cleanup_states |
| `test_provider_limit` | ✓ | self, mock_config, cleanup_states |

### `TestCircuitBreakerLogic`

| Method | Async | Args |
|--------|-------|------|
| `test_breaker_closed_initially` | ✓ | self, mock_config, cleanup_states |
| `test_breaker_opens_after_threshold` | ✓ | self, mock_config, cleanup_states |
| `test_breaker_half_open_after_timeout` | ✓ | self, mock_config, cleanup_states |
| `test_breaker_resets_on_success` | ✓ | self, mock_config, cleanup_states |
| `test_exponential_backoff` | ✓ | self, mock_config, cleanup_states |
| `test_failure_count_cap` | ✓ | self, mock_config, cleanup_states |

### `TestCircuitBreakerState`

| Method | Async | Args |
|--------|-------|------|
| `test_initialization_without_redis` | ✓ | self, mock_config |
| `test_initialization_with_invalid_redis_url` | ✓ | self, mock_config |
| `test_state_validation` | ✓ | self, mock_config |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_create_task` |  | coro |
| `cleanup_at_exit` |  |  |
| `mock_config` |  |  |
| `mock_config_with_redis` |  | mock_config |
| `cleanup_states` | ✓ |  |

**Constants:** `original_create_task`

---

## tests/test_arbiter_policy_core.py
**Lines:** 963

### `TestSQLiteClient`

| Method | Async | Args |
|--------|-------|------|
| `test_full_lifecycle` | ✓ | self, sqlite_client |
| `test_concurrent_operations` | ✓ | self, sqlite_client |
| `test_invalid_inputs` | ✓ | self, sqlite_client |
| `test_sql_injection_protection` | ✓ | self, sqlite_client |

### `TestBasicDecisionOptimizer`

| Method | Async | Args |
|--------|-------|------|
| `test_trust_score_scenarios` | ✓ | self, context, user_id, expected_range, arbiter_config |
| `test_invalid_context_types` | ✓ | self, arbiter_config |

### `TestPolicyEngine`

| Method | Async | Args |
|--------|-------|------|
| `test_initialization` | ✓ | self, policy_engine |
| `test_invalid_domains` | ✓ | self, policy_engine, domain, reason_fragment |
| `test_invalid_keys` | ✓ | self, policy_engine, key, reason_fragment |
| `test_invalid_user_ids` | ✓ | self, policy_engine, user_id, reason_fragment |
| `test_domain_restrictions` | ✓ | self, policy_engine |
| `test_user_restrictions` | ✓ | self, policy_engine |
| `test_size_limits` | ✓ | self, policy_engine |
| `test_sensitive_keys` | ✓ | self, policy_engine |
| `test_custom_rules` | ✓ | self, policy_engine |
| `test_llm_integration` | ✓ | self, policy_engine, monkeypatch |
| `test_policy_reload` | ✓ | self, policy_engine |
| `test_concurrent_requests` | ✓ | self, policy_engine |
| `test_policy_evolution_update` | ✓ | self, policy_engine |

### `TestPerformance`

| Method | Async | Args |
|--------|-------|------|
| `test_throughput` | ✓ | self, policy_engine |
| `test_memory_usage` | ✓ | self, policy_engine |

### `TestSecurity`

| Method | Async | Args |
|--------|-------|------|
| `test_path_traversal` | ✓ | self, policy_engine |
| `test_injection_attacks` | ✓ | self, policy_engine |

### `TestIntegration`

| Method | Async | Args |
|--------|-------|------|
| `test_full_workflow` | ✓ | self, minimal_arbiter, arbiter_config |
| `test_audit_integration` | ✓ | self, policy_engine, monkeypatch |

### `TestStress`

| Method | Async | Args |
|--------|-------|------|
| `test_rapid_policy_reloads` | ✓ | self, policy_engine |
| `test_resource_cleanup` | ✓ | self, tmp_path |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `create_mock_enforce_compliance` |  |  |
| `cleanup` | ✓ |  |
| `mock_circuit_breaker` |  | monkeypatch |
| `valid_policy_content` |  |  |
| `tmp_policy_file` |  | valid_policy_content |
| `arbiter_config` |  | tmp_policy_file |
| `minimal_arbiter` |  |  |
| `policy_engine` | ✓ | minimal_arbiter, arbiter_config, monkeypatch |
| `sqlite_client` | ✓ | tmp_path |
| `test_all_public_apis_exported` |  |  |
| `test_metrics_registered` |  |  |

**Constants:** `guardrails_audit_log_mock`, `guardrails_compliance_mapper_mock`, `INVALID_DOMAINS`, `INVALID_KEYS`, `INVALID_USER_IDS`

---

## tests/test_arbiter_policy_policy_config.py
**Lines:** 451

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `test_config_defaults_and_field_types` |  | monkeypatch |
| `test_env_loading_and_override` |  | monkeypatch, tmp_path |
| `test_secret_redaction_to_dict` |  |  |
| `test_get_api_key_for_provider` |  | monkeypatch |
| `test_model_validator_enforces_secrets` |  | monkeypatch |
| `test_singleton_thread_safety` |  | monkeypatch |
| `test_assignment_validation` |  | monkeypatch |
| `test_invalid_field_type` |  | monkeypatch |
| `test_public_api_symbols` |  |  |
| `test_no_secrets_in_repr` |  |  |
| `test_bad_env_file` |  | monkeypatch, tmp_path |
| `test_model_reload_and_mutation` |  | tmp_path |
| `test_to_dict_all_branches` |  |  |
| `test_all_public_fields_present` |  |  |
| `test_new_config_fields_present` |  |  |
| `test_branch_coverage` |  | monkeypatch |

---

## tests/test_arbiter_policy_policy_e2e.py
**Lines:** 227

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_config` |  | monkeypatch |
| `test_end_to_end_policy_lifecycle` | ✓ | monkeypatch, tmp_path, mock_config |

---

## tests/test_arbiter_policy_policy_manager.py
**Lines:** 539

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_config` |  |  |
| `temp_policy_file` |  |  |
| `policy_manager` | ✓ | mock_config, temp_policy_file |
| `test_domain_rule_validation` |  |  |
| `test_llm_rules_validation` |  |  |
| `test_trust_rules_validation` |  |  |
| `test_policy_config_default` |  |  |
| `test_policy_config_version_validation` |  |  |
| `test_policy_manager_init_invalid_config` |  |  |
| `test_policy_manager_encryption_key_setup` |  | mock_config |
| `test_load_policies_file_not_found` | ✓ | policy_manager |
| `test_save_and_load_policies` | ✓ | policy_manager |
| `test_save_policies_without_policies_set` | ✓ |  |
| `test_encryption_key_rotation` | ✓ | policy_manager |
| `test_invalid_key_rotation` | ✓ |  |
| `test_concurrent_operations` | ✓ | policy_manager |
| `test_health_check_healthy` | ✓ | policy_manager |
| `test_health_check_unhealthy` | ✓ | policy_manager |
| `test_database_operations` | ✓ |  |
| `test_database_error_handling` | ✓ |  |
| `test_check_permission_available` | ✓ |  |
| `test_check_permission_unavailable` | ✓ |  |
| `test_corrupted_json_after_decrypt` | ✓ | policy_manager |
| `test_legacy_key_fallback` | ✓ |  |
| `test_get_and_set_policies` |  |  |

---

## tests/test_arbiter_policy_policy_metrics.py
**Lines:** 337

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_config` |  | monkeypatch |
| `test_counter_idempotency` |  |  |
| `test_gauge_idempotency_and_set` |  |  |
| `test_histogram_buckets_and_idempotency` |  |  |
| `test_summary_idempotency` |  |  |
| `test_metric_conflicting_types` |  | caplog |
| `test_dynamic_compliance_metrics_exist` |  | mock_config |
| `test_metric_label_cardinality` |  |  |
| `test_invalid_label_raises` |  |  |
| `test_metrics_thread_safety` |  |  |
| `test_prometheus_scrape_for_all_metrics` |  | mock_config |
| `test_duplicate_registration_does_not_crash` |  |  |
| `test_bad_buckets_graceful` |  |  |
| `test_public_symbols_present` |  | mock_config |
| `test_metric_updates_observable` |  |  |
| `test_metric_name_overlap_does_not_break` |  |  |
| `test_metrics_module_all_public_symbols_present` |  | mock_config |

---

## tests/test_arbiter_queue_consumer_worker.py
**Lines:** 537

### `MockArbiterConfig`
**Attributes:** LOG_LEVEL, MQ_BACKEND_TYPE, REDIS_URL, KAFKA_BOOTSTRAP_SERVERS, ENCRYPTION_KEY_BYTES, MQ_TOPIC_PREFIX, MQ_DLQ_TOPIC_SUFFIX, MQ_MAX_RETRIES, MQ_RETRY_DELAY_BASE, MQ_CONSUMER_GROUP_ID, MQ_KAFKA_PRODUCER_ACKS, MQ_KAFKA_PRODUCER_RETRIES, MQ_KAFKA_CONSUMER_AUTO_OFFSET_RESET, MQ_KAFKA_CONSUMER_ENABLE_AUTO_COMMIT, MQ_KAFKA_CONSUMER_AUTO_COMMIT_INTERVAL_MS

### `TestRedactSensitive`

| Method | Async | Args |
|--------|-------|------|
| `test_redact_sensitive_keys` |  | self |
| `test_redact_nested_data` |  | self |
| `test_non_dict_passthrough` |  | self |

### `TestProcessEvent`

| Method | Async | Args |
|--------|-------|------|
| `test_successful_delivery` | ✓ | self, mock_mq_service, mock_audit_logger |
| `test_failed_delivery` | ✓ | self, mock_mq_service, mock_audit_logger |
| `test_poison_message_detection` | ✓ | self, mock_mq_service, mock_audit_logger |

### `TestExternalNotifiers`

| Method | Async | Args |
|--------|-------|------|
| `test_send_notification_success` | ✓ | self |
| `test_send_notification_failure` | ✓ | self |

### `TestHealthCheck`

| Method | Async | Args |
|--------|-------|------|
| `test_health_check_healthy` | ✓ | self, mock_mq_service |
| `test_health_check_degraded` | ✓ | self, mock_mq_service |

### `TestHandleMessage`

| Method | Async | Args |
|--------|-------|------|
| `test_handle_message_calls_process` | ✓ | self, mock_mq_service, mock_audit_logger |

### `TestQueueConsumerWorker`

| Method | Async | Args |
|--------|-------|------|
| `test_worker_initialization` | ✓ | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `setup_test_environment` |  |  |
| `_restore_original_modules` |  |  |
| `cleanup_mocked_modules` |  |  |
| `reset_state` |  |  |
| `mock_mq_service` |  |  |
| `mock_audit_logger` |  |  |

**Constants:** `_ORIGINAL_MODULES`, `_MOCKED_MODULE_NAMES`, `mock_config_module`

---

## tests/test_arbiter_run_exploration.py
**Lines:** 512

### `MockRetry`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `__call__` |  | self, func |

### `AsyncFileMock`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, content |
| `__aenter__` | ✓ | self |
| `__aexit__` | ✓ | self |
| `read` | ✓ | self |

### `AiofilesMock`

| Method | Async | Args |
|--------|-------|------|
| `open` |  | path, mode |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_create_package_mock` |  | name |
| `_safe_mock_module` |  | name, mock_obj |
| `_restore_original_modules` |  |  |
| `cleanup_mocked_modules` |  |  |
| `mock_config` |  |  |
| `test_setup_logging` |  | tmp_path |
| `test_load_config_json` | ✓ | monkeypatch, tmp_path |
| `test_load_config_yaml` | ✓ | tmp_path |
| `test_load_config_no_file` | ✓ | monkeypatch |
| `test_load_config_file_error` | ✓ |  |
| `test_load_config_invalid_json` | ✓ | tmp_path |
| `test_notify_critical_error_slack` |  | mock_getenv, mock_post |
| `test_notify_critical_error_slack_failure` |  | mock_getenv, mock_post, caplog |
| `test_load_plugins` |  | mock_import, mock_iter, mock_exists |
| `test_run_agent_task_success` | ✓ |  |
| `test_run_agent_task_error` | ✓ |  |
| `test_run_agentic_workflow_success` | ✓ | mock_file_open, mock_makedirs, mock_health_server, mock_arena_class, mock_config |
| `test_run_agentic_workflow_with_errors` | ✓ | mock_makedirs, mock_health_server, mock_arena_class, mock_config |
| `test_main_no_args` | ✓ | mock_event, mock_workflow, mock_health, mock_setup |
| `test_main_with_config_file` | ✓ | mock_health, mock_setup |
| `test_main_unhandled_exception` | ✓ | caplog |

**Constants:** `_ORIGINAL_MODULES`, `_MOCKED_MODULE_NAMES`, `_NEVER_MOCK_MODULES`, `tenacity_mock`, `aiofiles_mock`

---

## tests/test_arbiter_utils.py
**Lines:** 257

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `test_random_chance_deterministic` |  | prob, mock_value, expected |
| `test_random_chance_statistical` |  |  |
| `test_random_chance_invalid` |  | invalid_prob |
| `test_get_system_metrics_normal` |  |  |
| `test_get_system_metrics_error` |  | mock_disk, mock_mem, mock_cpu |
| `test_get_system_metrics_async_normal` | ✓ |  |
| `test_get_system_metrics_async_error` | ✓ | mock_to_thread |
| `test_check_service_health_success` | ✓ | mock_get_session |
| `test_check_service_health_non_json` | ✓ | mock_get_session |
| `test_check_service_health_client_error` | ✓ | mock_get_session |
| `test_check_service_health_timeout` | ✓ | mock_get_session |
| `test_check_service_health_unexpected_error` | ✓ | mock_get_session |
| `test_check_service_health_invalid_url` | ✓ | mock_get_session |
| `test_is_valid_directory_path` |  | path, expected |
| `test_safe_makedirs_with_valid_path` |  | tmp_path |
| `test_safe_makedirs_with_invalid_path_uses_fallback` |  | tmp_path |
| `test_safe_makedirs_with_empty_path_uses_fallback` |  | tmp_path |

---

## tests/test_audit_fixes.py
**Lines:** 276

### `TestFileCleanup`

| Method | Async | Args |
|--------|-------|------|
| `test_garbage_files_removed` |  | self |
| `test_log_files_removed` |  | self |
| `test_backup_files_removed` |  | self |

### `TestGitignore`

| Method | Async | Args |
|--------|-------|------|
| `test_root_gitignore_has_backup_patterns` |  | self |
| `test_sfe_gitignore_has_patterns` |  | self |

### `TestModuleImports`

| Method | Async | Args |
|--------|-------|------|
| `test_module_imports_successfully` |  | self |
| `test_module_has_proper_logger` |  | self |

### `TestConfigWrapper`

| Method | Async | Args |
|--------|-------|------|
| `test_config_wrapper_optional_fields` |  | self |
| `test_config_wrapper_raises_for_unknown_fields` |  | self |
| `test_config_wrapper_has_repr` |  | self |

### `TestRunWorkingTests`

| Method | Async | Args |
|--------|-------|------|
| `test_run_working_tests_exists` |  | self |
| `test_run_working_tests_has_docstring` |  | self |
| `test_run_working_tests_main_returns_one` |  | self |

### `TestAuditLogJsonl`

| Method | Async | Args |
|--------|-------|------|
| `test_audit_log_exists_and_not_empty` |  | self |
| `test_audit_log_is_valid_jsonl` |  | self |
| `test_audit_log_has_required_fields` |  | self |

### `TestCLIFunctions`

| Method | Async | Args |
|--------|-------|------|
| `test_repair_issues_is_documented` | ✓ | self |
| `test_simple_scan_has_docstring` | ✓ | self |

### `TestCodeQuality`

| Method | Async | Args |
|--------|-------|------|
| `test_init_has_comprehensive_docstring` |  | self |
| `test_config_has_type_hints` |  | self |
| `test_no_bare_except_clauses` |  | self |

---

## tests/test_envs_code_health_env.py
**Lines:** 918

### `TestEnvironmentConfig`

| Method | Async | Args |
|--------|-------|------|
| `test_default_configuration` |  | self |
| `test_configuration_validation` |  | self |
| `test_action_costs_and_cooldowns` |  | self |

### `TestSystemMetrics`

| Method | Async | Args |
|--------|-------|------|
| `test_metrics_validation` |  | self |
| `test_metrics_to_array` |  | self |
| `test_metrics_to_dict` |  | self |

### `TestAsyncActionExecutor`

| Method | Async | Args |
|--------|-------|------|
| `test_async_execution` | ✓ | self |
| `test_executor_cleanup` |  | self |
| `test_executor_error_handling` |  | self |

### `TestCodeHealthEnvBasics`

| Method | Async | Args |
|--------|-------|------|
| `test_environment_initialization` |  | self, basic_config, mock_metrics, mock_action |
| `test_backward_compatibility` |  | self, mock_metrics, mock_action |
| `test_invalid_parameters` |  | self |
| `test_reset` |  | self, basic_config, mock_metrics, mock_action |

### `TestCodeHealthEnvStep`

| Method | Async | Args |
|--------|-------|------|
| `test_basic_step` |  | self, basic_config, mock_metrics, mock_action |
| `test_invalid_action` |  | self, basic_config, mock_metrics, mock_action |
| `test_action_cooldown` |  | self, basic_config, mock_metrics, mock_action |
| `test_max_steps_termination` |  | self, mock_metrics, mock_action |

### `TestAutomaticRollback`

| Method | Async | Args |
|--------|-------|------|
| `test_automatic_rollback_triggered` |  | self, mock_action |
| `test_rollback_disabled` |  | self, mock_metrics, mock_action |

### `TestAsyncSupport`

| Method | Async | Args |
|--------|-------|------|
| `test_async_action_function` | ✓ | self, mock_metrics |
| `test_sync_action_function` |  | self, mock_metrics, mock_action |

### `TestThreadSafety`

| Method | Async | Args |
|--------|-------|------|
| `test_concurrent_steps` |  | self, mock_metrics, mock_action |
| `test_concurrent_reset_and_step` |  | self, mock_metrics, mock_action |

### `TestMemoryManagement`

| Method | Async | Args |
|--------|-------|------|
| `test_action_history_limit` |  | self, mock_metrics, mock_action |
| `test_metrics_history_limit` |  | self, mock_metrics, mock_action |

### `TestRewardCalculation`

| Method | Async | Args |
|--------|-------|------|
| `test_state_based_rewards` |  | self, mock_action |
| `test_action_costs` |  | self, mock_metrics |
| `test_success_bonus` |  | self, mock_metrics |

### `TestRendering`

| Method | Async | Args |
|--------|-------|------|
| `test_human_render` |  | self, basic_config, mock_metrics, mock_action, capsys |
| `test_rgb_array_render` |  | self, basic_config, mock_metrics, mock_action |
| `test_ansi_render` |  | self, basic_config, mock_metrics, mock_action, capsys |
| `test_invalid_render_mode` |  | self, basic_config, mock_metrics, mock_action |

### `TestDataExport`

| Method | Async | Args |
|--------|-------|------|
| `test_get_training_data` |  | self, basic_config, mock_metrics, mock_action |
| `test_get_metrics_summary` |  | self, mock_action |

### `TestAuditLogging`

| Method | Async | Args |
|--------|-------|------|
| `test_audit_logging_lifecycle` |  | self, basic_config, mock_metrics, mock_action, mock_audit_logger |

### `TestIntegration`

| Method | Async | Args |
|--------|-------|------|
| `test_full_episode` |  | self, mock_action |
| `test_stress_test` |  | self, mock_metrics, mock_action |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `basic_config` |  |  |
| `mock_metrics` |  |  |
| `mock_action` |  |  |
| `mock_audit_logger` |  |  |

**Constants:** `pytestmark`

---

## tests/test_envs_e2e_env.py
**Lines:** 816

### `SimulatedSystem`

| Method | Async | Args |
|--------|-------|------|
| `__post_init__` |  | self |
| `degrade` |  | self |
| `apply_action` |  | self, action |
| `trigger_incident` |  | self |
| `resolve_incident` |  | self |

### `TestE2EBasicWorkflows`

| Method | Async | Args |
|--------|-------|------|
| `test_complete_episode_workflow` |  | self |
| `test_multi_episode_training` |  | self |

### `TestE2EIncidentResponse`

| Method | Async | Args |
|--------|-------|------|
| `test_incident_detection_and_recovery` |  | self |
| `test_cascading_failures` |  | self |

### `TestE2EAsyncOperations`

| Method | Async | Args |
|--------|-------|------|
| `test_async_action_pipeline` | ✓ | self |
| `test_mixed_async_sync_operations` |  | self |

### `TestE2EPerformanceAndScale`

| Method | Async | Args |
|--------|-------|------|
| `test_high_frequency_monitoring` |  | self |
| `test_concurrent_environments` |  | self |

### `TestE2EVisualization`

| Method | Async | Args |
|--------|-------|------|
| `test_metrics_visualization_pipeline` |  | self |
| `test_metrics_export_for_dashboards` |  | self |

### `TestE2EProductionScenarios`

| Method | Async | Args |
|--------|-------|------|
| `test_maintenance_window_handling` |  | self |
| `test_blue_green_deployment` |  | self |
| `test_gradual_rollout` |  | self |

### `TestE2EAuditingAndCompliance`

| Method | Async | Args |
|--------|-------|------|
| `test_complete_audit_trail` |  | self |
| `test_session_tracking` |  | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `convert_numpy_types` |  | obj |

**Constants:** `pytestmark`

---

## tests/test_envs_evolution.py
**Lines:** 708

### `TestConfigurationSpace`

| Method | Async | Args |
|--------|-------|------|
| `test_default_configuration_space` |  | self |
| `test_custom_configuration_space` |  | self, basic_config_space |
| `test_gene_count_with_multiple_features` |  | self |
| `test_configuration_validation` |  | self |

### `TestEvolutionConfig`

| Method | Async | Args |
|--------|-------|------|
| `test_default_evolution_config` |  | self |
| `test_evolution_config_validation` |  | self |
| `test_reward_weights` |  | self |

### `TestFitnessEvaluator`

| Method | Async | Args |
|--------|-------|------|
| `test_evaluator_initialization` |  | self, evolution_config |
| `test_cache_key_generation` |  | self, evolution_config |
| `test_caching_mechanism` |  | self, evolution_config, simple_test_function |
| `test_evaluation_with_custom_function` |  | self, evolution_config |
| `test_heuristic_evaluation` |  | self, evolution_config |
| `test_gene_to_config_mapping` |  | self, evolution_config |
| `test_fitness_calculation_from_metrics` |  | self, evolution_config |

### `TestGeneticOptimizer`

| Method | Async | Args |
|--------|-------|------|
| `test_optimizer_initialization` |  | self, basic_config_space, evolution_config |
| `test_deap_setup` |  | self, basic_config_space, evolution_config |
| `test_evolution_basic` |  | self, basic_config_space, simple_test_function |
| `test_early_stopping` |  | self |
| `test_elitism` |  | self, basic_config_space |
| `test_evolution_summary` |  | self, basic_config_space, simple_test_function |
| `test_checkpoint_save_load` |  | self, basic_config_space, simple_test_function |
| `test_parallel_evaluation` |  | self |

### `TestWithoutDEAP`

| Method | Async | Args |
|--------|-------|------|
| `test_optimizer_without_deap` |  | self |

### `TestRunTestEvaluation`

| Method | Async | Args |
|--------|-------|------|
| `test_good_configuration` |  | self |
| `test_bad_configuration` |  | self |
| `test_failing_configuration` |  | self |

### `TestSandboxing`

| Method | Async | Args |
|--------|-------|------|
| `test_sandboxed_subprocess_check` |  | self |
| `test_sandboxed_execution` |  | self |

### `TestThreadSafety`

| Method | Async | Args |
|--------|-------|------|
| `test_concurrent_cache_access` |  | self, evolution_config |

### `TestAuditLogging`

| Method | Async | Args |
|--------|-------|------|
| `test_evolution_audit_logging` |  | self, mock_audit_logger, simple_test_function |

### `TestIntegration`

| Method | Async | Args |
|--------|-------|------|
| `test_full_optimization_pipeline` |  | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `basic_config_space` |  |  |
| `evolution_config` |  |  |
| `simple_test_function` |  |  |
| `mock_audit_logger` |  |  |

---

## tests/test_guardrails_audit_log.py
**Lines:** 372

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_dependencies` |  | monkeypatch |
| `temp_log_path` |  | tmp_path |
| `mock_env` |  | monkeypatch |
| `caplog` |  | caplog |
| `test_validate_dependencies_production` | ✓ | mock_env, monkeypatch, caplog |
| `test_validate_sensitive_env_vars_production` | ✓ | mock_env, monkeypatch, caplog |
| `test_load_public_keys` | ✓ | mock_env, monkeypatch |
| `test_load_private_key` | ✓ | mock_env, monkeypatch |
| `test_load_private_key_missing_vars` | ✓ | mock_env, monkeypatch, caplog |
| `test_key_rotation` | ✓ | mock_env, temp_log_path, monkeypatch |
| `test_key_revocation` | ✓ |  |
| `test_audit_logger_init` | ✓ | temp_log_path, mock_env |
| `test_audit_logger_add_entry` | ✓ | temp_log_path, mock_env |
| `test_verify_audit_chain` | ✓ | temp_log_path, mock_env |
| `test_verify_audit_chain_corrupted` | ✓ | temp_log_path, mock_env |
| `test_audit_log_event_async` | ✓ | mock_env, temp_log_path |
| `test_concurrent_add_entry` | ✓ | temp_log_path, mock_env |
| `test_low_disk_space_write` | ✓ | temp_log_path, monkeypatch, caplog |
| `test_main_cli` | ✓ | temp_log_path, monkeypatch, capsys |
| `test_health_check` | ✓ | temp_log_path, mock_env |
| `test_sanitize_log` |  |  |
| `test_add_entry_with_signature_revoked` | ✓ | temp_log_path, mock_env, monkeypatch |
| `test_key_rotation_failure` | ✓ | temp_log_path, monkeypatch |
| `test_verify_audit_chain_missing_pub_key` | ✓ | temp_log_path, mock_env, monkeypatch |
| `test_get_last_audit_hash` | ✓ | temp_log_path, mock_env |
| `test_close_resources` | ✓ | temp_log_path, mock_env |

---

## tests/test_guardrails_compliance_mapper.py
**Lines:** 428

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `temp_config` |  | tmp_path |
| `invalid_config` |  | tmp_path |
| `malformed_yaml` |  | tmp_path |
| `mock_env` |  | monkeypatch |
| `caplog` |  | caplog |
| `test_load_compliance_map` | ✓ | temp_config, caplog |
| `test_load_compliance_map_missing_file` | ✓ | mock_env, caplog |
| `test_load_compliance_map_permission_error` | ✓ | mock_env, temp_config, monkeypatch |
| `test_load_compliance_map_malformed_yaml` | ✓ | malformed_yaml, caplog |
| `test_load_compliance_map_invalid_schema` | ✓ | invalid_config, caplog |
| `test_load_compliance_map_no_controls` | ✓ | temp_config, monkeypatch, caplog |
| `test_load_compliance_map_empty_yaml` | ✓ | temp_config, monkeypatch, caplog |
| `test_load_compliance_map_yaml_with_only_comments` | ✓ | tmp_path, caplog |
| `test_load_compliance_map_non_dict_root` | ✓ | temp_config, monkeypatch, caplog |
| `test_load_compliance_map_string_root` | ✓ | temp_config, monkeypatch, caplog |
| `test_load_compliance_map_prometheus_inc` | ✓ | temp_config, monkeypatch |
| `test_check_coverage` | ✓ |  |
| `test_check_coverage_prometheus_set` | ✓ | monkeypatch |
| `test_generate_report` | ✓ | temp_config, capsys, caplog |
| `test_generate_report_all_enforced` | ✓ | temp_config, monkeypatch |
| `test_health_check` | ✓ |  |
| `test_compliance_enforcement_error` | ✓ | caplog |
| `test_audit_log_gap` | ✓ | caplog |
| `test_write_dummy_config` | ✓ | tmp_path |
| `test_write_dummy_config_low_disk_space` | ✓ | tmp_path, monkeypatch |
| `test_main_cli` | ✓ | mock_env, temp_config, monkeypatch, capsys |
| `test_main_cli_health_check` | ✓ | mock_env, monkeypatch, capsys |
| `test_main_cli_prometheus_required` | ✓ | mock_env, monkeypatch |
| `test_main_cli_permission_error` | ✓ | mock_env, monkeypatch |
| `test_main_cli_compliance_error` | ✓ | mock_env, monkeypatch |
| `test_main_cli_unexpected_error` | ✓ | mock_env, monkeypatch |
| `test_sanitize_log` |  |  |

---

## tests/test_guardrails_integration.py
**Lines:** 189

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `temp_config_and_log` |  | tmp_path |
| `test_integration_load_and_audit` | ✓ | temp_config_and_log, monkeypatch |
| `test_integration_generate_report_with_gaps` | ✓ | temp_config_and_log, monkeypatch |
| `test_integration_main_cli_in_production` | ✓ | temp_config_and_log, monkeypatch, capsys |
| `test_integration_health_check_with_audit` | ✓ | temp_config_and_log, monkeypatch |
| `test_concurrent_report_generation` | ✓ | temp_config_and_log, monkeypatch |
| `test_audit_chain_creation` | ✓ | temp_config_and_log, monkeypatch |

---

## tests/test_intent_capture_agent_core.py
**Lines:** 684

### `MockJWT`

| Method | Async | Args |
|--------|-------|------|
| `encode` |  | payload, key, algorithm |
| `decode` |  | token, key, algorithms |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_env_vars` |  |  |
| `mock_redis_client` |  |  |
| `mock_llm` |  |  |
| `mock_llm_factory` |  | mock_llm |
| `test_sanitize_input` |  |  |
| `test_anonymize_pii` |  |  |
| `test_safety_guard` |  |  |
| `test_mock_llm` | ✓ |  |
| `test_fallback_llm` | ✓ |  |
| `test_get_usable_keys` | ✓ | mock_env_vars, mock_redis_client |
| `test_get_llm_test_mode` | ✓ | mock_env_vars |
| `test_get_llm_caching` | ✓ | mock_env_vars, mock_redis_client |
| `test_redis_state_backend_create` | ✓ | mock_redis_client |
| `test_redis_state_backend_save_load` | ✓ | mock_redis_client |
| `test_validate_session_token_valid` | ✓ | mock_env_vars, mock_redis_client |
| `test_validate_session_token_revoked` | ✓ | mock_env_vars, mock_redis_client |
| `test_validate_session_token_invalid` | ✓ | mock_env_vars |
| `test_agent_creation` | ✓ | mock_env_vars, mock_llm_factory, mock_redis_client |
| `test_agent_predict` | ✓ | mock_env_vars, mock_llm, mock_redis_client |
| `test_agent_state_persistence` | ✓ | mock_env_vars, mock_llm, mock_redis_client |
| `test_get_or_create_agent_with_token` | ✓ | mock_env_vars, mock_llm_factory, mock_redis_client |
| `test_validate_environment` |  | mock_env_vars |
| `test_full_integration` | ✓ | mock_env_vars, mock_redis_client |
| `test_agent_predict_error_handling` | ✓ | mock_env_vars, mock_llm, mock_redis_client |

**Constants:** `jwt`

---

## tests/test_intent_capture_api.py
**Lines:** 330

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `test_secret_key` |  |  |
| `app` |  | test_secret_key |
| `async_client` | ✓ | app |
| `mock_get_or_create_agent` |  |  |
| `create_test_token` |  | secret_key, overrides |
| `valid_token` |  | test_secret_key |
| `test_create_token_endpoint` | ✓ | async_client |
| `test_predict_success` | ✓ | async_client, valid_token, mock_get_or_create_agent, test_secret_key |
| `test_predict_no_auth_header` | ✓ | async_client, valid_token |
| `test_predict_invalid_token` | ✓ | async_client |
| `test_predict_invalid_payload` | ✓ | async_client, test_secret_key |
| `test_predict_agent_error` | ✓ | async_client, valid_token, mock_get_or_create_agent, test_secret_key |
| `test_predict_timeout_error` | ✓ | async_client, valid_token, mock_get_or_create_agent, test_secret_key |
| `test_prune_sessions_success` | ✓ | async_client, test_secret_key |
| `test_prune_sessions_forbidden` | ✓ | async_client, test_secret_key |
| `test_app_config_secret_handling` |  | monkeypatch, test_secret_key |
| `test_health_check` | ✓ | async_client |

---

## tests/test_intent_capture_autocomplete.py
**Lines:** 462

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_autocomplete_state` |  |  |
| `mock_llm` |  |  |
| `mock_readline` |  |  |
| `mock_logger` |  |  |
| `test_json_formatter` |  |  |
| `test_anonymize_pii` |  |  |
| `test_autocomplete_state_singleton` | ✓ |  |
| `test_autocomplete_state_initialize_redis` | ✓ |  |
| `test_command_registry_initialization` |  |  |
| `test_command_registry_update_all_commands` |  |  |
| `test_fernet_encryptor` |  |  |
| `test_is_toxic` |  |  |
| `test_add_to_history` |  | mock_readline |
| `test_handle_command_not_found` |  | capsys |
| `test_get_ai_suggestions_success` | ✓ | mock_llm, mock_autocomplete_state |
| `test_get_ai_suggestions_no_llm` | ✓ | mock_autocomplete_state |
| `test_get_ai_suggestions_filters_toxic` | ✓ | mock_llm, mock_autocomplete_state |
| `test_fuzzy_matches_with_cache` | ✓ | mock_autocomplete_state |
| `test_fuzzy_matches_without_cache` | ✓ | mock_autocomplete_state |
| `test_command_completer_basic` |  | mock_readline |
| `test_command_completer_ai_suggestions` |  | mock_readline, mock_llm |
| `test_execute_macro_success` |  |  |
| `test_execute_macro_unknown` |  |  |
| `test_setup_autocomplete` |  | mock_readline |
| `test_log_audit_event` |  |  |

---

## tests/test_intent_capture_cli.py
**Lines:** 296

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_console` |  | capsys |
| `mock_agent` |  |  |
| `mock_session_state` |  |  |
| `mock_get_or_create_agent` |  | mock_agent |
| `mock_websockets` |  |  |
| `mock_logger` |  |  |
| `mock_console_output` |  | capsys |
| `mock_input` |  | monkeypatch |
| `temp_files` |  | tmp_path |
| `test_json_formatter` |  |  |
| `test_shutdown_handler` |  |  |
| `test_resource_guard_normal` |  |  |
| `test_resource_guard_high_memory` |  | monkeypatch |
| `test_session_state_get_set` | ✓ |  |
| `test_session_state_get_agent` | ✓ |  |
| `test_collab_server_init` | ✓ |  |
| `test_command_dispatcher_help` | ✓ | mock_session_state, mock_console_output |
| `test_command_dispatcher_unknown` | ✓ | mock_session_state |
| `test_command_dispatcher_clear` | ✓ | mock_session_state, mock_agent |
| `test_command_dispatcher_exit` | ✓ | mock_session_state |
| `test_main_cli_loop_basic_flow` | ✓ | monkeypatch, mock_console_output |

---

## tests/test_intent_capture_e2e.py
**Lines:** 303

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_redis` |  |  |
| `mock_agent` |  |  |
| `test_jwt_token` | ✓ |  |
| `test_input_sanitization` | ✓ |  |
| `test_agent_prediction` | ✓ | mock_agent |
| `test_redis_state` | ✓ | mock_redis |
| `test_session_validation` | ✓ |  |
| `test_requirements_structure` | ✓ |  |
| `test_spec_validation` | ✓ |  |
| `test_file_security` | ✓ |  |
| `test_pii_anonymization` | ✓ |  |
| `test_concurrent_requests` | ✓ | mock_agent |
| `test_error_recovery` | ✓ |  |
| `test_encryption` | ✓ |  |

---

## tests/test_intent_capture_intent_config.py
**Lines:** 372

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_env` |  | monkeypatch |
| `mock_hvac` |  |  |
| `mock_boto3` |  |  |
| `mock_requests` |  |  |
| `mock_redis` |  |  |
| `temp_plugin_dir` |  | tmp_path |
| `mock_logger` |  |  |
| `temp_config_file` |  | tmp_path |
| `test_pii_masking_formatter` |  |  |
| `test_setup_logging` |  | tmp_path, monkeypatch |
| `test_fetch_from_vault_success` |  | mock_hvac, monkeypatch |
| `test_fetch_from_vault_disabled` |  | monkeypatch |
| `test_fetch_from_vault_not_authenticated` |  | mock_hvac, monkeypatch |
| `test_fetch_config_from_service_success` |  | mock_requests, monkeypatch |
| `test_fetch_config_from_service_no_url` |  | monkeypatch |
| `test_fetch_config_from_service_error` |  | mock_requests |
| `test_config_encryptor_encrypt_decrypt` |  | tmp_path, monkeypatch |
| `test_config_encryptor_no_key` |  |  |
| `test_config_validation_success` |  | mock_env |
| `test_config_validation_invalid_redis_url` |  | mock_env |
| `test_config_validation_invalid_log_level` |  | mock_env |
| `test_plugin_manager_discover_and_apply_plugins` |  | temp_plugin_dir, monkeypatch |
| `test_plugin_manager_verify_signature_disabled` |  | monkeypatch |
| `test_global_config_manager_get_config` |  | mock_env, mock_requests |
| `test_global_config_manager_singleton` |  | mock_env, mock_requests |
| `test_global_config_manager_reload` |  | mock_env, mock_requests, mock_logger |
| `test_log_audit_event_enabled` |  | mock_boto3, monkeypatch |
| `test_log_audit_event_disabled` |  | mock_boto3, monkeypatch |
| `test_prune_audit_logs` |  | mock_boto3, monkeypatch |
| `test_startup_validation_success` |  | mock_env |
| `test_startup_validation_missing_fields` |  | monkeypatch |
| `test_config_change_handler` |  | mock_env, temp_config_file |

---

## tests/test_intent_capture_io_utils.py
**Lines:** 464

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_env` |  | monkeypatch |
| `temp_workspace` |  | tmp_path |
| `file_manager` |  | temp_workspace |
| `mock_redis` |  |  |
| `mock_aiohttp` |  |  |
| `mock_circuit_breaker` |  |  |
| `mock_boto3` |  |  |
| `mock_prometheus` |  |  |
| `test_file_manager_init` |  | temp_workspace |
| `test_file_manager_validate_path_valid` |  | file_manager, temp_workspace |
| `test_file_manager_validate_path_traversal` |  | file_manager |
| `test_file_manager_safe_open` |  | file_manager, temp_workspace |
| `test_provenance_logger_log_event` |  | mock_env |
| `test_provenance_logger_hash_chain` |  |  |
| `test_get_redis_client_available` | ✓ | mock_redis |
| `test_get_redis_client_not_available` | ✓ | monkeypatch |
| `test_hash_file_distributed_cache_success` | ✓ | file_manager, temp_workspace, mock_redis, mock_prometheus |
| `test_hash_file_distributed_cache_cached` | ✓ | file_manager, temp_workspace, mock_redis, mock_prometheus |
| `test_hash_file_size_limit` | ✓ | file_manager, temp_workspace |
| `test_download_file_to_temp_success` | ✓ | file_manager, mock_circuit_breaker, mock_prometheus, monkeypatch |
| `test_download_file_rate_limited` | ✓ | file_manager, monkeypatch |
| `test_download_file_content_too_large` | ✓ | file_manager, mock_circuit_breaker, monkeypatch |
| `test_log_audit_event_enabled` |  | mock_boto3, monkeypatch |
| `test_log_audit_event_disabled` |  | mock_boto3, monkeypatch |
| `test_prune_audit_logs` |  | mock_boto3, monkeypatch |
| `test_startup_validation_success` |  | mock_env |
| `test_startup_validation_missing_provenance_salt` |  | monkeypatch |
| `test_startup_validation_missing_redis_url` |  | monkeypatch |

---

## tests/test_intent_capture_requirements.py
**Lines:** 649

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_asyncpg` |  |  |
| `mock_redis` |  |  |
| `mock_sentence_transformer` |  |  |
| `mock_llm` |  |  |
| `mock_tracer` |  |  |
| `mock_prometheus` |  |  |
| `temp_files` |  | tmp_path, monkeypatch |
| `mock_cachetools` |  |  |
| `reset_manager` |  |  |
| `test_get_tracing_context` |  | mock_tracer |
| `test_get_tracing_context_no_opentelemetry` |  | monkeypatch |
| `test_get_embedding_model_success` | ✓ | mock_sentence_transformer, mock_tracer, mock_prometheus |
| `test_get_embedding_model_no_ml` | ✓ |  |
| `test_get_db_conn_pool_success` | ✓ | mock_asyncpg, monkeypatch |
| `test_get_db_conn_pool_no_db` | ✓ |  |
| `test_get_db_conn_pool_missing_vars` | ✓ | monkeypatch |
| `test_db_get_custom_checklists_success` | ✓ | mock_asyncpg, mock_tracer, mock_prometheus |
| `test_db_get_custom_checklists_retry` | ✓ | monkeypatch |
| `test_db_save_custom_checklists_success` | ✓ | mock_asyncpg, mock_tracer, mock_prometheus |
| `test_get_global_custom_checklists_db` | ✓ | mock_asyncpg, temp_files |
| `test_get_global_custom_checklists_file` | ✓ | temp_files |
| `test_set_global_custom_checklists_db` | ✓ | mock_asyncpg |
| `test_set_global_custom_checklists_file` | ✓ | temp_files |
| `test_get_checklist` | ✓ | mock_asyncpg |
| `test_add_item_success` | ✓ | mock_asyncpg |
| `test_add_item_invalid_name` | ✓ |  |
| `test_update_item_status_custom` | ✓ | mock_asyncpg |
| `test_update_item_status_global` | ✓ |  |
| `test_update_item_status_invalid` | ✓ |  |
| `test_generate_novel_requirements_success` | ✓ | mock_llm, mock_tracer, mock_prometheus |
| `test_generate_novel_requirements_timeout` | ✓ | mock_llm |
| `test_generate_novel_requirements_invalid_response` | ✓ | mock_llm |
| `test_suggest_requirements_ml` | ✓ | mock_sentence_transformer, mock_llm |
| `test_suggest_requirements_no_ml` | ✓ | mock_llm |
| `test_propose_checklist_updates_success` | ✓ | mock_llm |
| `test_propose_checklist_updates_timeout` | ✓ | mock_llm |
| `test_log_coverage_snapshot_redis` | ✓ | mock_redis |
| `test_log_coverage_snapshot_file` | ✓ | temp_files |
| `test_get_coverage_history_redis` | ✓ | mock_redis |
| `test_get_coverage_history_file` | ✓ | temp_files |
| `test_generate_coverage_report` | ✓ |  |
| `test_compute_coverage_pandas` | ✓ |  |
| `test_compute_coverage_llm_fallback` | ✓ | mock_llm |
| `test_compute_coverage_timeout` | ✓ | mock_llm |
| `test_register_plugin_requirements` |  |  |

---

## tests/test_intent_capture_session.py
**Lines:** 649

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_asyncpg` |  |  |
| `mock_redis` |  |  |
| `mock_sentence_transformer` |  |  |
| `mock_llm` |  |  |
| `mock_tracer` |  |  |
| `mock_prometheus` |  |  |
| `temp_files` |  | tmp_path, monkeypatch |
| `mock_cachetools` |  |  |
| `reset_manager` |  |  |
| `test_get_tracing_context` |  | mock_tracer |
| `test_get_tracing_context_no_opentelemetry` |  | monkeypatch |
| `test_get_embedding_model_success` | ✓ | mock_sentence_transformer, mock_tracer, mock_prometheus |
| `test_get_embedding_model_no_ml` | ✓ |  |
| `test_get_db_conn_pool_success` | ✓ | mock_asyncpg, monkeypatch |
| `test_get_db_conn_pool_no_db` | ✓ |  |
| `test_get_db_conn_pool_missing_vars` | ✓ | monkeypatch |
| `test_db_get_custom_checklists_success` | ✓ | mock_asyncpg, mock_tracer, mock_prometheus |
| `test_db_get_custom_checklists_retry` | ✓ | monkeypatch |
| `test_db_save_custom_checklists_success` | ✓ | mock_asyncpg, mock_tracer, mock_prometheus |
| `test_get_global_custom_checklists_db` | ✓ | mock_asyncpg, temp_files |
| `test_get_global_custom_checklists_file` | ✓ | temp_files |
| `test_set_global_custom_checklists_db` | ✓ | mock_asyncpg |
| `test_set_global_custom_checklists_file` | ✓ | temp_files |
| `test_get_checklist` | ✓ | mock_asyncpg |
| `test_add_item_success` | ✓ | mock_asyncpg |
| `test_add_item_invalid_name` | ✓ |  |
| `test_update_item_status_custom` | ✓ | mock_asyncpg |
| `test_update_item_status_global` | ✓ |  |
| `test_update_item_status_invalid` | ✓ |  |
| `test_generate_novel_requirements_success` | ✓ | mock_llm, mock_tracer, mock_prometheus |
| `test_generate_novel_requirements_timeout` | ✓ | mock_llm |
| `test_generate_novel_requirements_invalid_response` | ✓ | mock_llm |
| `test_suggest_requirements_ml` | ✓ | mock_sentence_transformer, mock_llm |
| `test_suggest_requirements_no_ml` | ✓ | mock_llm |
| `test_propose_checklist_updates_success` | ✓ | mock_llm |
| `test_propose_checklist_updates_timeout` | ✓ | mock_llm |
| `test_log_coverage_snapshot_redis` | ✓ | mock_redis |
| `test_log_coverage_snapshot_file` | ✓ | temp_files |
| `test_get_coverage_history_redis` | ✓ | mock_redis |
| `test_get_coverage_history_file` | ✓ | temp_files |
| `test_generate_coverage_report` | ✓ |  |
| `test_compute_coverage_pandas` | ✓ |  |
| `test_compute_coverage_llm_fallback` | ✓ | mock_llm |
| `test_compute_coverage_timeout` | ✓ | mock_llm |
| `test_register_plugin_requirements` |  |  |

---

## tests/test_intent_capture_spec_utils.py
**Lines:** 642

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_requests` |  |  |
| `mock_nltk` |  | monkeypatch |
| `mock_llm` |  |  |
| `mock_tracer` |  |  |
| `mock_prometheus` |  |  |
| `temp_locales` |  | tmp_path, monkeypatch |
| `mock_memory` |  |  |
| `mock_checklist` |  |  |
| `reset_spec_handlers` |  |  |
| `test_get_tracing_context` |  | mock_tracer |
| `test_get_tracing_context_no_opentelemetry` |  | monkeypatch |
| `test_nltk_data_setup` |  | monkeypatch |
| `test_load_locales` |  | temp_locales |
| `test_get_localized_prompt_default` |  | temp_locales |
| `test_load_ambiguous_words_file` |  | tmp_path |
| `test_load_ambiguous_words_url` |  | mock_requests |
| `test_load_ambiguous_words_retry` |  |  |
| `test_load_ambiguous_words_failure` |  |  |
| `test_register_spec_handler` |  |  |
| `test_validate_spec_json_valid` |  | mock_tracer, mock_prometheus |
| `test_validate_spec_json_invalid` |  | mock_tracer, mock_prometheus |
| `test_validate_spec_json_schema_violation` |  | mock_tracer, mock_prometheus |
| `test_validate_spec_yaml_valid` |  | mock_tracer, mock_prometheus |
| `test_validate_spec_yaml_duplicate_keys` |  | mock_tracer, mock_prometheus |
| `test_validate_spec_gherkin_valid` |  | mock_tracer, mock_prometheus |
| `test_validate_spec_gherkin_missing_feature` |  | mock_tracer, mock_prometheus |
| `test_validate_spec_user_story_valid` |  | mock_tracer, mock_prometheus |
| `test_validate_spec_unknown_format` |  | mock_tracer, mock_prometheus |
| `test_migrate_spec` |  |  |
| `test_detect_ambiguity` |  | mock_nltk |
| `test_auto_fix_spec_no_issues` | ✓ | mock_llm |
| `test_auto_fix_spec_success` | ✓ | mock_tracer, mock_prometheus, temp_locales |
| `test_auto_fix_spec_failure` | ✓ | mock_llm, temp_locales |
| `test_traceable_artifact_persistence` |  | mock_requests |
| `test_traceable_artifact_update` |  | mock_requests |
| `test_generate_code_stub` | ✓ | mock_llm, temp_locales, mock_requests |
| `test_generate_test_stub` | ✓ | mock_llm, temp_locales, mock_requests |
| `test_generate_security_review` | ✓ | mock_llm, temp_locales, mock_requests |
| `test_generate_spec_from_memory_success` | ✓ | mock_memory, mock_tracer, mock_prometheus, mock_checklist, temp_locales |
| `test_generate_spec_from_memory_auto_fix` | ✓ | mock_memory, mock_tracer, mock_prometheus, mock_checklist, temp_locales |
| `test_generate_gaps_success` | ✓ | mock_llm, mock_checklist, temp_locales |
| `test_refine_spec_success` | ✓ | mock_llm, temp_locales |
| `test_review_spec_success` | ✓ | mock_llm, temp_locales |
| `test_diff_specs_success` |  |  |
| `test_diff_specs_type_error` |  |  |

---

## tests/test_intent_capture_web_app.py
**Lines:** 441

### `MockSessionState` (dict)

| Method | Async | Args |
|--------|-------|------|
| `__getattr__` |  | self, key |
| `__setattr__` |  | self, key, value |
| `__delattr__` |  | self, key |
| `get` |  | self, key, default |

### `TestAuthenticationSecurity`

| Method | Async | Args |
|--------|-------|------|
| `test_password_hashing_on_load` |  | self, tmp_path |
| `test_captcha_expiry` |  | self |
| `test_failed_login_rate_limiting` |  | self |
| `test_input_sanitization_xss` |  | self |

### `TestRedisIntegration`

| Method | Async | Args |
|--------|-------|------|
| `test_redis_connection_retry` |  | self |
| `test_redis_connection_total_failure` |  | self |
| `test_collaboration_channel_format` |  | self |

### `TestSessionManagement`

| Method | Async | Args |
|--------|-------|------|
| `test_session_state_initialization` |  | self |
| `test_unauthenticated_no_agent` |  | self |

### `TestLocalization`

| Method | Async | Args |
|--------|-------|------|
| `test_locale_loading_from_file` |  | self, tmp_path |
| `test_translation_fallback` |  | self |

### `TestErrorHandling`

| Method | Async | Args |
|--------|-------|------|
| `test_agent_prediction_error_handling` |  | self |
| `test_redis_error_logging` |  | self |

### `TestMetrics`

| Method | Async | Args |
|--------|-------|------|
| `test_metrics_increment_on_page_view` |  | self |
| `test_error_metrics_on_failure` |  | self |

### `TestContentSafety`

| Method | Async | Args |
|--------|-------|------|
| `test_max_input_length_enforcement` |  | self |
| `test_empty_input_rejection` |  | self |

### `TestAsyncOperations`

| Method | Async | Args |
|--------|-------|------|
| `test_run_async_wrapper` |  | self |
| `test_run_async_with_exception` |  | self |

---

## tests/test_mesh_adapter.py
**Lines:** 711

### `TestBackendDetection`

| Method | Async | Args |
|--------|-------|------|
| `test_detect_backend_urls` |  | self |

### `TestConnection`

| Method | Async | Args |
|--------|-------|------|
| `test_redis_connect` | ✓ | self, mock_metrics |
| `test_connection_retry` | ✓ | self |
| `test_healthcheck` | ✓ | self, redis_adapter |

### `TestPublishing`

| Method | Async | Args |
|--------|-------|------|
| `test_publish_redis` | ✓ | self, redis_adapter, test_message |
| `test_publish_kafka` | ✓ | self, mock_kafka_adapter, test_message |
| `test_publish_with_encryption` | ✓ | self, redis_adapter, test_message |
| `test_publish_schema_validation` | ✓ | self, redis_adapter |

### `TestSubscription`

| Method | Async | Args |
|--------|-------|------|
| `test_subscribe_redis` | ✓ | self, redis_adapter, test_message |
| `test_subscribe_kafka_consumer_group` | ✓ | self, mock_kafka_adapter |

### `TestDeadLetterQueue`

| Method | Async | Args |
|--------|-------|------|
| `test_dlq_write` | ✓ | self, redis_adapter |
| `test_dlq_replay` | ✓ | self, redis_adapter |
| `test_native_dlq_kafka` | ✓ | self, mock_kafka_adapter |

### `TestReliability`

| Method | Async | Args |
|--------|-------|------|
| `test_circuit_breaker` | ✓ | self, redis_adapter |
| `test_rate_limiting` | ✓ | self, redis_adapter |

### `TestSecurity`

| Method | Async | Args |
|--------|-------|------|
| `test_payload_scrubbing` | ✓ | self, redis_adapter |
| `test_encryption_rotation` | ✓ | self, redis_adapter |

### `TestProductionMode`

| Method | Async | Args |
|--------|-------|------|
| `test_prod_mode_requirements` |  | self |

### `TestPerformance`

| Method | Async | Args |
|--------|-------|------|
| `test_publish_latency` | ✓ | self, redis_adapter |
| `test_concurrent_operations` | ✓ | self, redis_adapter |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `redis_adapter` | ✓ |  |
| `mock_kafka_adapter` | ✓ |  |
| `test_message` |  |  |
| `mock_metrics` |  |  |
| `cleanup` |  |  |

**Constants:** `TEST_DIR`, `TEST_KEYS`, `TEST_HMAC_KEY`, `TEST_ENV`

---

## tests/test_mesh_checkpoint.py
**Lines:** 534

### `MockCheckpointSchema` (BaseModel)

### `TestCoreOperations`

| Method | Async | Args |
|--------|-------|------|
| `test_save_and_load` | ✓ | self, checkpoint_manager, test_state |
| `test_versioning` | ✓ | self, checkpoint_manager, test_state |
| `test_rollback` | ✓ | self, checkpoint_manager, test_state |
| `test_diff` | ✓ | self, checkpoint_manager |

### `TestSecurity`

| Method | Async | Args |
|--------|-------|------|
| `test_encryption` | ✓ | self, checkpoint_manager, test_state |
| `test_key_rotation` | ✓ | self, checkpoint_manager, test_state |
| `test_hash_chain_integrity` | ✓ | self, checkpoint_manager, test_state |
| `test_data_scrubbing` | ✓ | self, checkpoint_manager, sensitive_state |

### `TestReliability`

| Method | Async | Args |
|--------|-------|------|
| `test_retry_mechanism_concept` | ✓ | self, checkpoint_manager |
| `test_circuit_breaker` | ✓ | self, checkpoint_manager |
| `test_dlq_handling` | ✓ | self, checkpoint_manager, test_state |
| `test_auto_healing_basic` | ✓ | self, checkpoint_manager |

### `TestPerformance`

| Method | Async | Args |
|--------|-------|------|
| `test_caching` | ✓ | self, checkpoint_manager, test_state |
| `test_compression` | ✓ | self, checkpoint_manager |
| `test_concurrent_operations` | ✓ | self, checkpoint_manager |

### `TestBackendIntegration`

| Method | Async | Args |
|--------|-------|------|
| `test_backend_configuration` | ✓ | self |

### `TestEdgeCases`

| Method | Async | Args |
|--------|-------|------|
| `test_empty_state` | ✓ | self, checkpoint_manager |
| `test_large_state` | ✓ | self, checkpoint_manager |
| `test_special_characters` | ✓ | self, checkpoint_manager |
| `test_nonexistent_checkpoint` | ✓ | self, checkpoint_manager |
| `test_schema_validation` | ✓ | self, checkpoint_manager |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `checkpoint_manager` | ✓ |  |
| `s3_checkpoint_manager` | ✓ |  |
| `test_state` |  |  |
| `sensitive_state` |  |  |
| `cleanup` |  |  |

**Constants:** `TEST_DIR`, `TEST_KEYS`, `TEST_HMAC_KEY`, `TEST_ENV`

---

## tests/test_mesh_checkpoint_backends.py
**Lines:** 798

### `CheckpointTestData`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, name, state, metadata, user |

### `TestS3Backend`

| Method | Async | Args |
|--------|-------|------|
| `s3_client_mock` | ✓ | self |
| `test_s3_save` | ✓ | self, backend_registry, s3_client_mock, test_data, mock_metrics, ...+1 |
| `test_s3_load` | ✓ | self, backend_registry, s3_client_mock, test_data, mock_encryption |
| `test_s3_version_cleanup` | ✓ | self, backend_registry, s3_client_mock |
| `test_s3_key_rotation` | ✓ | self, backend_registry, s3_client_mock, mock_encryption |

### `TestRedisBackend`

| Method | Async | Args |
|--------|-------|------|
| `redis_client_mock` | ✓ | self |
| `test_redis_save` | ✓ | self, backend_registry, redis_client_mock, test_data, mock_encryption |
| `test_redis_load` | ✓ | self, backend_registry, redis_client_mock, test_data, mock_encryption |

### `TestPostgresBackend`

| Method | Async | Args |
|--------|-------|------|
| `postgres_pool_mock` | ✓ | self |
| `test_postgres_save` | ✓ | self, backend_registry, postgres_pool_mock, test_data, mock_encryption |
| `test_postgres_load` | ✓ | self, backend_registry, postgres_pool_mock, test_data, mock_encryption |

### `TestSecurity`

| Method | Async | Args |
|--------|-------|------|
| `test_encryption_decryption` | ✓ | self |
| `test_hmac_integrity` | ✓ | self |
| `test_prod_mode_enforcement` | ✓ | self |

### `TestReliability`

| Method | Async | Args |
|--------|-------|------|
| `test_retry_mechanism` | ✓ | self, backend_registry |
| `test_circuit_breaker` | ✓ | self, backend_registry |
| `test_dlq_write` | ✓ | self |

### `TestPerformance`

| Method | Async | Args |
|--------|-------|------|
| `test_concurrent_operations` | ✓ | self, backend_registry |
| `test_connection_pooling` | ✓ | self, backend_registry |

### `TestEdgeCases`

| Method | Async | Args |
|--------|-------|------|
| `test_invalid_backend` | ✓ | self |
| `test_missing_operation` | ✓ | self |
| `test_backend_initialization_failure` | ✓ | self, backend_registry |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `test_data` |  |  |
| `mock_encryption` |  |  |
| `mock_metrics` |  |  |
| `mock_tracer` |  |  |
| `mock_circuit_breakers` |  |  |
| `backend_registry` | ✓ |  |
| `cleanup` |  |  |

**Constants:** `TEST_DIR`, `TEST_KEYS`, `TEST_HMAC_KEY`, `TEST_ENV`

---

## tests/test_mesh_checkpoint_exceptions.py
**Lines:** 583

### `TestConstants`
**Attributes:** MESSAGE, SENSITIVE_CONTEXT, SCRUBBED_CONTEXT

### `TestCheckpointError`

| Method | Async | Args |
|--------|-------|------|
| `test_initialization` |  | self, mock_tracing, mock_metrics |
| `test_string_representation` |  | self |
| `test_context_size_limit` |  | self |
| `test_hmac_signing` |  | self |
| `test_exception_chaining` |  | self |
| `test_raise_with_alert` | ✓ | self, mock_alert_callback, mock_alert_cache |
| `test_alert_throttling` | ✓ | self, mock_alert_callback, mock_alert_cache |

### `TestExceptionSubclasses`

| Method | Async | Args |
|--------|-------|------|
| `test_audit_error` |  | self |
| `test_backend_error` |  | self |
| `test_retryable_error` |  | self |
| `test_validation_error` |  | self |

### `TestRetryDecorator`

| Method | Async | Args |
|--------|-------|------|
| `test_successful_retry` | ✓ | self |
| `test_circuit_breaker_integration` | ✓ | self, mock_circuit_breaker |
| `test_no_tenacity_fallback` | ✓ | self |

### `TestSecurity`

| Method | Async | Args |
|--------|-------|------|
| `test_sensitive_data_scrubbing` |  | self |
| `test_token_masking` |  | self |

### `TestEdgeCases`

| Method | Async | Args |
|--------|-------|------|
| `test_empty_context` |  | self |
| `test_none_context` |  | self |
| `test_custom_error_code` |  | self |
| `test_missing_dependencies` |  | self |

### `TestPerformance`

| Method | Async | Args |
|--------|-------|------|
| `test_exception_creation_performance` |  | self, benchmark |
| `test_context_scrubbing_performance` |  | self, benchmark |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_tracing` |  |  |
| `mock_metrics` |  |  |
| `mock_alert_callback` |  |  |
| `mock_circuit_breaker` |  |  |
| `mock_alert_cache` |  |  |

---

## tests/test_mesh_checkpoint_manager.py
**Lines:** 665

### `MockStateSchema` (BaseModel)

### `TestCoreOperations`

| Method | Async | Args |
|--------|-------|------|
| `test_save_and_load` | ✓ | self, manager, test_state |
| `test_versioning` | ✓ | self, manager |
| `test_rollback` | ✓ | self, manager |
| `test_rollback_dry_run` | ✓ | self, manager |
| `test_diff` | ✓ | self, manager |

### `TestSecurity`

| Method | Async | Args |
|--------|-------|------|
| `test_encryption` | ✓ | self, manager, test_state |
| `test_hash_chain` | ✓ | self, manager |
| `test_tamper_detection` | ✓ | self, manager |
| `test_access_control` | ✓ | self, manager, mock_access_policy |

### `TestPerformance`

| Method | Async | Args |
|--------|-------|------|
| `test_caching` | ✓ | self, manager, test_state |
| `test_compression` | ✓ | self, manager |
| `test_concurrent_operations` | ✓ | self, manager |

### `TestSchemaValidation`

| Method | Async | Args |
|--------|-------|------|
| `test_valid_schema` | ✓ | self, manager_with_schema |
| `test_invalid_schema` | ✓ | self, manager_with_schema |
| `test_auto_heal_schema_failure` | ✓ | self, manager_with_schema |

### `TestAuditCompliance`

| Method | Async | Args |
|--------|-------|------|
| `test_audit_logging` | ✓ | self, manager, mock_audit_hook |
| `test_audit_trail` | ✓ | self, manager |

### `TestErrorHandling`

| Method | Async | Args |
|--------|-------|------|
| `test_load_nonexistent` | ✓ | self, manager |
| `test_rollback_nonexistent` | ✓ | self, manager |
| `test_dlq_on_failure` | ✓ | self, manager |

### `TestProductionMode`

| Method | Async | Args |
|--------|-------|------|
| `test_prod_mode_requirements` | ✓ | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `clean_test_env` | ✓ |  |
| `manager` | ✓ |  |
| `manager_with_schema` | ✓ |  |
| `test_state` |  |  |
| `mock_audit_hook` |  |  |
| `mock_access_policy` |  |  |
| `cleanup` |  |  |

**Constants:** `TEST_DIR`, `TEST_KEY`, `TEST_HMAC_KEY`, `TEST_ENV`

---

## tests/test_mesh_checkpoint_utils.py
**Lines:** 552

### `TestData`
**Attributes:** SIMPLE_DICT, NESTED_DICT, SENSITIVE_DATA, LARGE_DATA

### `TestCryptography`

| Method | Async | Args |
|--------|-------|------|
| `test_key_generation` |  | self, crypto_provider |
| `test_key_derivation` |  | self, crypto_provider |
| `test_aes_gcm_encryption` |  | self, crypto_provider |
| `test_secure_compare` |  | self, crypto_provider |

### `TestHashing`

| Method | Async | Args |
|--------|-------|------|
| `test_hash_data_consistency` |  | self |
| `test_hash_dict_with_chaining` |  | self |
| `test_hmac_signing` |  | self |
| `test_multiple_hash_algorithms` |  | self, algorithm |

### `TestCompression`

| Method | Async | Args |
|--------|-------|------|
| `test_compress_decompress_roundtrip` |  | self |
| `test_compress_json` |  | self |
| `test_compression_algorithms` |  | self, algorithm |
| `test_auto_detect_compression` |  | self |

### `TestDataScrubbing`

| Method | Async | Args |
|--------|-------|------|
| `test_scrub_sensitive_fields` |  | self |
| `test_scrub_patterns` |  | self |
| `test_anonymize_data` |  | self |

### `TestDataComparison`

| Method | Async | Args |
|--------|-------|------|
| `test_deep_diff_additions` |  | self |
| `test_deep_diff_modifications` |  | self |
| `test_deep_diff_type_changes` |  | self |

### `TestKeyRotation`

| Method | Async | Args |
|--------|-------|------|
| `test_create_fernet_key` |  | self |
| `test_rotate_keys` |  | self |

### `TestValidation`

| Method | Async | Args |
|--------|-------|------|
| `test_validate_checkpoint_data` |  | self |

### `TestUtilities`

| Method | Async | Args |
|--------|-------|------|
| `test_generate_checkpoint_id` |  | self |
| `test_format_size` |  | self |
| `test_parse_duration` |  | self |
| `test_valid_identifier` |  | self |

### `TestPerformance`

| Method | Async | Args |
|--------|-------|------|
| `test_hash_performance` |  | self, benchmark |
| `test_compression_performance` |  | self, benchmark |
| `test_scrubbing_performance` |  | self, benchmark |

### `TestSecurityCompliance`

| Method | Async | Args |
|--------|-------|------|
| `test_fips_mode` |  | self |
| `test_secure_deletion` |  | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `crypto_provider` |  |  |
| `test_payload` |  |  |

**Constants:** `TEST_KEYS`, `TEST_HMAC_KEY`, `TEST_ENV`

---

## tests/test_mesh_e2e_mesh.py
**Lines:** 365

### `MockSpan`

| Method | Async | Args |
|--------|-------|------|
| `set_attribute` |  | self, key, value |
| `add_event` |  | self, name, attributes |
| `set_status` |  | self, status |

### `MockTracer`

| Method | Async | Args |
|--------|-------|------|
| `start_as_current_span` |  | self, name |

### `StateSchema` (BaseModel)

### `TestFullWorkflow`

| Method | Async | Args |
|--------|-------|------|
| `test_policy_checkpoint_and_event_flow` | ✓ | self, services |

### `TestFailureAndRecovery`

| Method | Async | Args |
|--------|-------|------|
| `test_dlq_recovery_workflow` | ✓ | self, services |

### `TestSecurityIntegration`

| Method | Async | Args |
|--------|-------|------|
| `test_encryption_key_rotation` | ✓ | self, services |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `generate_test_key` |  |  |
| `services` | ✓ |  |
| `cleanup` |  |  |

**Constants:** `pytestmark`, `TEST_DIR`, `TEST_POLICY_ID`, `TEST_POLICY_DATA`, `TEST_KEY_1`, `TEST_KEY_2`, `mock_redis_module`

---

## tests/test_mesh_event_bus.py
**Lines:** 548

### `ConnectionError` (Exception)

### `TimeoutError` (Exception)

### `RedisError` (Exception)

### `ResponseError` (Exception)

### `MockSpan`

| Method | Async | Args |
|--------|-------|------|
| `set_attribute` |  | self, key, value |
| `set_status` |  | self, status |
| `add_event` |  | self, name, attributes |
| `__enter__` |  | self |
| `__exit__` |  | self |

### `MockTracer`

| Method | Async | Args |
|--------|-------|------|
| `start_as_current_span` |  | self, name |

### `TestAsyncSafeLogger`

| Method | Async | Args |
|--------|-------|------|
| `test_logger_creation` |  | self |
| `test_logger_operations` |  | self |

### `TestCircuitBreaker`

| Method | Async | Args |
|--------|-------|------|
| `test_initial_state` |  | self |
| `test_opens_after_threshold` |  | self |

### `TestPublishing`

| Method | Async | Args |
|--------|-------|------|
| `test_basic_publish` | ✓ | self, mock_redis_client, reset_circuit_breaker |
| `test_publish_with_retry` | ✓ | self, mock_redis_client, reset_circuit_breaker |
| `test_publish_batch` | ✓ | self, mock_redis_client, reset_circuit_breaker |
| `test_publish_fails_after_max_retries` | ✓ | self, mock_redis_client, reset_circuit_breaker |

### `TestSubscription`

| Method | Async | Args |
|--------|-------|------|
| `test_subscribe_setup` | ✓ | self, mock_redis_client, reset_circuit_breaker |
| `test_subscribe_receives_message` | ✓ | self, mock_redis_client, reset_circuit_breaker |

### `TestDLQ`

| Method | Async | Args |
|--------|-------|------|
| `test_dlq_replay` | ✓ | self, mock_redis_client, reset_circuit_breaker |

### `TestIntegration`

| Method | Async | Args |
|--------|-------|------|
| `test_concurrent_publishers` | ✓ | self, mock_redis_client, reset_circuit_breaker |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `setup_mocks` |  |  |
| `setup_prometheus_mocks` |  |  |
| `tracked_init` |  | self |
| `cleanup_all_loggers` |  |  |
| `session_cleanup` |  |  |
| `patch_tracer` |  |  |
| `mock_redis_client` |  |  |
| `reset_circuit_breaker` |  |  |

**Constants:** `_ORIGINAL_MODULES`, `current_dir`, `parent_dir`, `event_bus_path`, `spec`, `event_bus`, `publish_event`, `publish_events`, `subscribe_event`, `replay_dlq`, `CircuitBreaker`, `AsyncSafeLogger`, `get_redis_client`, `created_loggers`, `original_logger_init`

---

## tests/test_mesh_integration.py
**Lines:** 189

### `TestPolicyAndEvents`

| Method | Async | Args |
|--------|-------|------|
| `test_successful_publish_after_policy_check` | ✓ | self, policy_enforcer |

### `TestCheckpointAndEvents`

| Method | Async | Args |
|--------|-------|------|
| `test_checkpoint_save_triggers_event` | ✓ | self, checkpoint_manager_service |

### `TestFullWorkflow`

| Method | Async | Args |
|--------|-------|------|
| `test_policy_checkpoint_event_workflow` | ✓ | self, policy_enforcer, checkpoint_manager_service |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `policy_enforcer` | ✓ |  |
| `checkpoint_manager_service` | ✓ |  |
| `cleanup` |  |  |

**Constants:** `TEST_DIR`, `TEST_KEYS`, `TEST_ENV`

---

## tests/test_mesh_policy.py
**Lines:** 661

### `MockPolicySchema` (BaseModel)

### `TestBackends`

| Method | Async | Args |
|--------|-------|------|
| `test_local_backend_save_load` | ✓ | self, local_backend, test_policy |
| `test_s3_backend_save_load` | ✓ | self, mock_s3_backend, test_policy |
| `test_backend_healthcheck` | ✓ | self, local_backend |

### `TestPolicyOperations`

| Method | Async | Args |
|--------|-------|------|
| `test_save_with_encryption` | ✓ | self, local_backend, test_policy |
| `test_save_with_validation` | ✓ | self, local_backend |
| `test_versioning` | ✓ | self, local_backend, test_policy |
| `test_batch_operations` | ✓ | self, local_backend, test_policy |
| `test_rollback` | ✓ | self, local_backend, test_policy |

### `TestSecurity`

| Method | Async | Args |
|--------|-------|------|
| `test_data_scrubbing` | ✓ | self, local_backend |
| `test_hmac_integrity` | ✓ | self, local_backend, test_policy |

### `TestPolicyEnforcement`

| Method | Async | Args |
|--------|-------|------|
| `test_enforce_with_jwt` | ✓ | self, policy_enforcer, test_policy, test_jwt_token |
| `test_enforce_mfa_requirement` | ✓ | self, policy_enforcer, test_policy |
| `test_enforce_invalid_jwt` | ✓ | self, policy_enforcer, test_policy |
| `test_enforce_no_policy` | ✓ | self, policy_enforcer |
| `test_max_redeliveries` | ✓ | self, policy_enforcer, test_policy |

### `TestReliability`

| Method | Async | Args |
|--------|-------|------|
| `test_retry_on_failure` | ✓ | self, local_backend, test_policy |
| `test_circuit_breaker` | ✓ | self, local_backend |
| `test_dlq_replay` | ✓ | self, local_backend |

### `TestPerformance`

| Method | Async | Args |
|--------|-------|------|
| `test_caching` | ✓ | self, local_backend, test_policy |
| `test_concurrent_operations` | ✓ | self, local_backend, test_policy |

### `TestProductionMode`

| Method | Async | Args |
|--------|-------|------|
| `test_prod_mode_requirements` |  | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `local_backend` | ✓ |  |
| `mock_s3_backend` | ✓ |  |
| `policy_enforcer` | ✓ | local_backend |
| `test_policy` |  |  |
| `reset_circuit_breakers` |  |  |
| `test_jwt_token` |  |  |
| `cleanup` |  |  |

**Constants:** `TEST_DIR`, `TEST_KEYS`, `TEST_HMAC_KEY`, `TEST_JWT_SECRET`, `TEST_ENV`

---

## tests/test_plugins_azure_eventgrid_plugin.py
**Lines:** 587

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_alert_operator` |  | message, level |
| `mock_scrub_secrets` |  | data |
| `setup_logging` |  |  |
| `reset_mocks` |  |  |
| `mock_aiohttp_session` |  |  |
| `set_env` |  | monkeypatch |
| `test_plugin_manifest_structure` |  |  |
| `test_production_mode_block_missing_key` |  | set_env, monkeypatch |
| `test_init_success` | ✓ | set_env |
| `test_init_invalid_endpoint_prod` |  | set_env, monkeypatch |
| `test_init_not_in_allowlist_prod` |  | set_env, monkeypatch |
| `test_audit_hook_success` | ✓ |  |
| `test_audit_hook_shutdown_drops_event` | ✓ |  |
| `test_send_batch_success` | ✓ | mock_aiohttp_session |
| `test_send_batch_retriable_failure` | ✓ | mock_aiohttp_session |
| `test_send_batch_permanent_failure` | ✓ | mock_aiohttp_session |
| `test_send_batch_all_retries_fail` | ✓ | mock_aiohttp_session |
| `test_send_batch_on_failure_callback` | ✓ | mock_aiohttp_session |
| `test_batch_sender_success` | ✓ | mock_aiohttp_session |
| `test_batch_sender_shutdown_drains_queue` | ✓ | mock_aiohttp_session |
| `test_sign_event` | ✓ |  |
| `test_close_own_session` | ✓ |  |
| `test_close_external_session` | ✓ | mock_aiohttp_session |

**Constants:** `mock_secrets_manager`, `mock_core_secrets`, `mock_audit_logger`, `mock_core_audit`, `mock_alert_operator_instance`, `mock_core_utils`

---

## tests/test_plugins_core_audit.py
**Lines:** 292

### `DummySecretsManager`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, secrets |
| `get_secret` |  | self, key, default, type_cast |
| `reload` |  | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `temp_log_dir` |  |  |
| `secrets` |  | temp_log_dir |
| `reset_singleton` |  |  |
| `logger` |  | secrets |
| `test_singleton_and_context` |  | logger |
| `test_log_event_to_file` |  | logger, secrets |
| `test_log_event_invalid_event_type` |  | logger |
| `test_log_event_truncation` |  | logger, secrets |
| `test_log_event_hmac_signature` |  | logger, secrets |
| `test_log_event_hmac_kid` |  | logger, secrets |
| `test_log_exception_and_stacktrace` |  | logger, secrets |
| `test_rate_limiting` |  | logger, secrets |
| `test_rate_limit_max_keys` |  | logger, secrets |
| `test_queue_drop_on_full` |  | logger, secrets |
| `test_reload_and_update_context` |  | logger |
| `test_close_and_log_after_close` |  | logger, secrets |
| `test_strict_writes_kills_process` |  | logger, secrets |
| `test_handler_permission_error` |  | monkeypatch, secrets |
| `test_serialization_error` |  | logger |
| `test_sighup_reload` |  | monkeypatch, secrets |
| `test_extra_context_json` |  | logger, secrets |
| `test_multithreaded_logging` |  | logger, secrets |
| `test_correlation_id` |  | logger, secrets |

---

## tests/test_plugins_core_secrets.py
**Lines:** 286

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `reset_singleton` |  |  |
| `sandbox_env` |  | monkeypatch |
| `temp_env_file` |  | tmp_path |
| `secrets_manager` |  | tmp_path, sandbox_env |
| `test_singleton_behavior` |  | secrets_manager |
| `test_env_file_loading` |  | monkeypatch, tmp_path |
| `test_env_file_load_disabled_in_prod` |  | monkeypatch, tmp_path, caplog |
| `test_env_file_override_allowed` |  | monkeypatch, tmp_path, caplog |
| `test_name_validation_strict_and_non_strict` |  | monkeypatch |
| `test_blank_values_treated_as_missing` |  | secrets_manager, monkeypatch |
| `test_required_secret_missing_raises` |  | secrets_manager |
| `test_cache_behavior` |  | secrets_manager, monkeypatch |
| `test_reload_clears_cache_and_reloads` |  | monkeypatch, tmp_path |
| `test_set_secret_sets_env_and_cache` |  | secrets_manager |
| `test_get_with_fallback` |  | secrets_manager, monkeypatch |
| `test_type_casting` |  | secrets_manager, monkeypatch |
| `test_cast_bool_strict_and_loose` |  |  |
| `test_get_choice` |  | secrets_manager, monkeypatch |
| `test_get_json` |  | secrets_manager, monkeypatch |
| `test_get_list` |  | secrets_manager, monkeypatch |
| `test_get_path` |  | secrets_manager, monkeypatch, tmp_path |
| `test_get_bytes_and_get_duration` |  | secrets_manager, monkeypatch |
| `test_get_int_in_range` |  | secrets_manager, monkeypatch |
| `test_snapshot` |  | secrets_manager, monkeypatch |
| `test_thread_safety` |  | monkeypatch |
| `test_logger_is_used` |  | monkeypatch, tmp_path, caplog |
| `test_null_handler_attached` |  | monkeypatch |

---

## tests/test_plugins_core_utils.py
**Lines:** 360

### `DummySecretsManager`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, secrets |
| `get_secret` |  | self, key, default |
| `get_int` |  | self, key, default |
| `get_bool` |  | self, key, default |
| `reload` |  | self |

### `DummyAuditLogger`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, secrets_manager |
| `log_event` |  | self |
| `reload` |  | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `secrets` |  |  |
| `alert_operator` |  | monkeypatch, secrets |
| `test_scrub_patterns` |  | input_str, pattern |
| `test_scrub_dict_and_large` |  | monkeypatch |
| `test_safe_err_redacts` |  |  |
| `test_reject_header_injection_raises` |  | bad |
| `test_reject_header_injection_ok` |  |  |
| `test_truncate_robust` |  | s, max_len, expect |
| `test_dispatcher_enqueue_and_stop` |  | alert_operator |
| `test_dispatcher_drops_when_full` |  | alert_operator |
| `test_rate_limiting` |  | alert_operator |
| `test_dispatch_slack_success` |  | alert_operator |
| `test_dispatch_slack_retries_on_failure` |  | alert_operator |
| `test_dispatch_email_success` |  | alert_operator |
| `test_dispatch_email_missing_config` |  | alert_operator |
| `test_alert_operator_alert_and_audit` |  | alert_operator |
| `test_alert_operator_reloads` |  | alert_operator |
| `test_alert_operator_context_update` |  | alert_operator |
| `test_alert_operator_alert_invalid_input` |  | alert_operator |
| `test_get_alert_operator_singleton` |  | monkeypatch, secrets |
| `test_post_with_retry_attempts_zero` |  | alert_operator |
| `test_post_with_retry_retry_after_header` |  | alert_operator |
| `test_post_with_retry_raises_after_max_attempts` |  | alert_operator |
| `test_logger_configures_stdout` |  | alert_operator, capsys |

---

## tests/test_plugins_demo_python_plugin.py
**Lines:** 307

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `setup_logging` |  |  |
| `mock_audit_logger` |  |  |
| `mock_alert_operator` |  |  |
| `mock_scrub_secrets` |  |  |
| `mock_importlib` |  |  |
| `set_env` |  | monkeypatch |
| `test_plugin_manifest_structure` |  |  |
| `test_manifest_version_format` |  |  |
| `test_production_mode_block` |  | monkeypatch, mock_audit_logger, mock_alert_operator |
| `test_plugin_health_healthy` |  | mock_importlib, mock_audit_logger, mock_scrub_secrets |
| `test_plugin_health_degraded` |  | mock_importlib, mock_audit_logger, mock_scrub_secrets |
| `test_plugin_health_unhealthy_runtime` |  | mock_importlib, mock_audit_logger, mock_alert_operator, mock_scrub_secrets, set_env |
| `test_plugin_health_unhandled_exception` |  | mock_importlib, mock_audit_logger, mock_alert_operator, mock_scrub_secrets |
| `test_plugin_api_hello` |  | mock_audit_logger |
| `test_plugin_api_hello_qa_mode` |  | mock_audit_logger, set_env |
| `test_no_hardcoded_secrets` |  |  |
| `test_plugin_load_with_missing_core_utils` |  | monkeypatch |
| `cleanup_env` |  | monkeypatch |

---

## tests/test_plugins_dlt_backend.py
**Lines:** 434

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `reset_clients_and_globals` |  | monkeypatch |
| `dummy_state` |  |  |
| `dummy_checkpoint_manager` |  | dummy_state |
| `redis_mock` |  | monkeypatch |
| `s3_and_fabric_dummy` |  | monkeypatch |
| `scrub_patch` |  | monkeypatch |
| `dummy_audit_logger` |  | monkeypatch |
| `dummy_alert_operator` |  | monkeypatch |
| `dummy_tracer` |  | monkeypatch |
| `test_initialize_dlt_backend_success` | ✓ | monkeypatch, s3_and_fabric_dummy, dummy_audit_logger, dummy_alert_operator |
| `test_initialize_dlt_backend_offchain_fail` | ✓ | monkeypatch, dummy_alert_operator |
| `test_initialize_dlt_backend_dlt_fail` | ✓ | monkeypatch, s3_and_fabric_dummy, dummy_alert_operator |
| `test_save_and_load_cycle` | ✓ | monkeypatch, redis_mock, scrub_patch, dummy_audit_logger, dummy_alert_operator |
| `test_save_idempotency` | ✓ | monkeypatch, redis_mock, scrub_patch |
| `test_rollback_and_diff` | ✓ | monkeypatch, redis_mock, scrub_patch |
| `test_hash_chain_integrity` | ✓ | monkeypatch, redis_mock, scrub_patch, dummy_audit_logger, dummy_alert_operator |
| `test_off_chain_payload_corrupt` | ✓ | monkeypatch, redis_mock, scrub_patch, dummy_audit_logger, dummy_alert_operator |
| `test_async_retry_decorator_works` | ✓ | monkeypatch |
| `test_maybe_sign_checkpoint_and_verify` | ✓ | monkeypatch |
| `test_encrypt_decrypt` | ✓ | monkeypatch |
| `test_save_blob_max_size` | ✓ | monkeypatch |
| `test_name_validation` | ✓ | dummy_checkpoint_manager |
| `test_deep_diff` |  |  |
| `test_unsupported_op` | ✓ | dummy_checkpoint_manager, dummy_alert_operator |
| `test_distributed_lock` | ✓ | redis_mock |
| `test_cache_fast_path` | ✓ | redis_mock, scrub_patch |

---

## tests/test_plugins_e2e.py
**Lines:** 409

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `discover_plugin_modules` |  |  |
| `mock_environment` |  | monkeypatch, tmp_path |
| `test_core_modules_available` |  |  |
| `test_discovered_plugins` |  |  |
| `test_plugin_import` |  | plugin_name |
| `test_core_utils_integration` | ✓ |  |
| `test_core_audit_integration` | ✓ |  |
| `test_core_secrets_integration` |  |  |
| `test_demo_plugin_health` | ✓ |  |
| `test_grpc_runner_functions` | ✓ |  |
| `test_plugin_health_checks` | ✓ |  |
| `test_e2e_summary` |  |  |

**Constants:** `plugins_dir`, `logger`

---

## tests/test_plugins_grpc_runner.py
**Lines:** 677

### `AnalyzerCriticalError` (RuntimeError)

### `PluginManifest` (object)

### `DummyAuditLogger`

| Method | Async | Args |
|--------|-------|------|
| `log_event` |  | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `alert_operator` |  | message, level |
| `scrub_sensitive_data` |  | data |
| `_get_tls_credentials` |  |  |
| `_is_endpoint_allowed` |  | endpoint |
| `connect` | ✓ | endpoint, retries, backoff_sec |
| `run_method` | ✓ | stub, method, request, timeout |
| `emit_metric` |  | name, value, labels, metric_type |
| `plugin_health` | ✓ | channel, plugin_name |
| `validate_manifest` |  | manifest |
| `list_plugins` |  | plugin_dir |
| `generate_plugin_docs` |  | manifest, output_path |
| `start_prometheus_exporter` | ✓ | host, port |
| `setup_logging` |  |  |
| `mock_audit_logger` |  |  |
| `mock_alert_operator` |  |  |
| `mock_scrub_sensitive_data` |  |  |
| `mock_secrets_manager` |  |  |
| `mock_grpc_channel` |  | monkeypatch |
| `mock_health_stub` |  | mock_grpc_channel |
| `mock_prometheus` |  |  |
| `set_env` |  | monkeypatch |
| `temp_dir` |  | tmp_path |
| `test_production_mode_block` |  | monkeypatch, mock_audit_logger, mock_alert_operator, set_env |
| `test_get_tls_credentials_production_missing` |  | mock_secrets_manager, mock_audit_logger, mock_alert_operator, set_env |
| `test_get_tls_credentials_non_production_insecure` |  | mock_secrets_manager, set_env |
| `test_get_tls_credentials_load_failure` |  | mock_secrets_manager, mock_alert_operator, set_env |
| `test_is_endpoint_allowed_production_missing` |  | mock_secrets_manager, mock_alert_operator, set_env |
| `test_is_endpoint_allowed_non_production_all_allowed` |  | mock_secrets_manager, set_env |
| `test_is_endpoint_allowed_forbidden` |  | mock_secrets_manager, mock_audit_logger, mock_alert_operator |
| `test_plugin_health_success` | ✓ | mock_health_stub, mock_prometheus |
| `test_plugin_health_timeout` | ✓ | mock_health_stub, mock_prometheus |
| `test_plugin_health_grpc_error_unavailable` | ✓ | mock_health_stub, mock_prometheus |
| `test_plugin_health_unhandled_error` | ✓ | mock_health_stub, mock_prometheus |
| `test_connect_success_secure` | ✓ | mock_secrets_manager, mock_grpc_channel, set_env |
| `test_connect_insecure_non_prod` | ✓ | mock_secrets_manager, mock_grpc_channel, set_env |
| `test_connect_forbidden_endpoint` | ✓ | mock_secrets_manager, mock_alert_operator |
| `test_connect_retry_failure` | ✓ | mock_secrets_manager, mock_grpc_channel, mock_alert_operator |
| `test_run_method_success` | ✓ | mock_grpc_channel |
| `test_run_method_timeout` | ✓ | mock_grpc_channel |
| `test_run_method_grpc_error` | ✓ | mock_grpc_channel, mock_alert_operator |
| `test_run_method_unhandled_error` | ✓ | mock_grpc_channel, mock_alert_operator |
| `test_emit_metric_health_gauge` |  | mock_prometheus |
| `test_emit_metric_operations_counter` |  | mock_prometheus |
| `test_emit_metric_unknown` |  | mock_prometheus |
| `test_validate_manifest_success` |  | mock_secrets_manager, mock_scrub_sensitive_data |
| `test_validate_manifest_signature_mismatch` |  | mock_secrets_manager |
| `test_validate_manifest_invalid_format` |  | mock_secrets_manager |
| `test_list_plugins_valid_manifest` |  | temp_dir, mock_scrub_sensitive_data |
| `test_list_plugins_invalid_json` |  | temp_dir |
| `test_generate_plugin_docs_success` |  | temp_dir |
| `test_generate_plugin_docs_failure` |  | temp_dir, monkeypatch |
| `test_start_prometheus_exporter_success` | ✓ |  |
| `test_start_prometheus_exporter_failure` | ✓ | mock_alert_operator |
| `cleanup_env` |  | monkeypatch |

**Constants:** `audit_logger`, `logger`, `PRODUCTION_MODE`, `PLUGIN_HEALTH_GAUGE`, `PLUGIN_OPERATION_COUNTER`

---

## tests/test_plugins_kafka_plugin.py
**Lines:** 518

### `AnalyzerCriticalError` (RuntimeError)

### `NonCriticalError` (Exception)

### `DummyAuditLogger`

| Method | Async | Args |
|--------|-------|------|
| `log_event` |  | self |

### `KafkaSettings`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `model_validate` |  | cls, data |

### `AuditMetrics`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, registry |

### `AuditEvent`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `model_dump` |  | self, exclude |
| `_sign_event` |  | self |

### `KafkaAuditProducer`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, settings, metrics |
| `start` | ✓ | self |
| `stop` | ✓ | self |
| `send` | ✓ | self, event_name, details |
| `send_batch` | ✓ | self, events |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `alert_operator` |  | message, level |
| `scrub_sensitive_data` |  | data |
| `kafka_audit_hook` | ✓ | event_name, details |
| `shutdown_kafka_producer` | ✓ |  |
| `setup_logging` |  |  |
| `mock_audit_logger` |  |  |
| `mock_alert_operator` |  |  |
| `mock_scrub_sensitive_data` |  |  |
| `mock_secrets_manager` |  |  |
| `mock_aiokafka_producer` |  | monkeypatch |
| `mock_prometheus_registry` |  |  |
| `set_env` |  | monkeypatch |
| `sample_settings_dict` |  |  |
| `test_kafka_settings_success` |  | sample_settings_dict |
| `test_kafka_settings_plaintext_prod` |  | set_env, sample_settings_dict |
| `test_kafka_settings_missing_ssl_cafile_prod` |  | set_env, sample_settings_dict |
| `test_kafka_settings_invalid_brokers_allowlist` |  | set_env, sample_settings_dict |
| `test_kafka_settings_wildcard_topic_prod` |  | set_env, sample_settings_dict |
| `test_audit_metrics_init` |  | mock_prometheus_registry |
| `test_metrics_init_failure` |  | mock_alert_operator |
| `test_audit_event_success` |  | mock_secrets_manager |
| `test_audit_event_pii_scrubbing` |  | mock_scrub_sensitive_data |
| `test_audit_event_pii_detection_aborts` |  | mock_scrub_sensitive_data, mock_alert_operator |
| `test_audit_event_sign` |  | mock_secrets_manager |
| `test_audit_event_sign_missing_key_prod` |  | set_env, mock_secrets_manager |
| `test_producer_start_success` | ✓ | mock_aiokafka_producer, mock_prometheus_registry |
| `test_producer_start_failure` | ✓ | mock_aiokafka_producer, mock_alert_operator |
| `test_producer_stop_success` | ✓ | mock_aiokafka_producer, mock_prometheus_registry |
| `test_producer_stop_flush_timeout` | ✓ | mock_aiokafka_producer, mock_alert_operator |
| `test_producer_send_success` | ✓ | mock_aiokafka_producer, mock_prometheus_registry |
| `test_producer_send_batch_success` | ✓ | mock_aiokafka_producer, mock_prometheus_registry |
| `test_producer_send_retry_failure` | ✓ | mock_aiokafka_producer, mock_alert_operator |
| `test_kafka_audit_hook_success` | ✓ | mock_aiokafka_producer |
| `test_shutdown_kafka_producer_success` | ✓ | mock_aiokafka_producer |

**Constants:** `PRODUCTION_MODE`, `logger`, `audit_logger`, `SECRETS_MANAGER`, `PLUGIN_MANIFEST`, `kafka_audit_producer`

---

## tests/test_plugins_pagerduty_plugin.py
**Lines:** 634

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_scrub_sensitive_data` |  | data |
| `setup_logging` |  |  |
| `reset_mocks` |  |  |
| `mock_aiohttp_session` |  |  |
| `set_env` |  | monkeypatch |
| `sample_settings_dict` |  |  |
| `sample_metrics` |  |  |
| `gateway` | ✓ | sample_settings_dict, sample_metrics |
| `test_pagerduty_settings_success` |  | sample_settings_dict, monkeypatch |
| `test_pagerduty_settings_non_https_prod` |  | set_env, sample_settings_dict, monkeypatch |
| `test_pagerduty_settings_not_in_allowlist_prod` |  | set_env, sample_settings_dict, monkeypatch |
| `test_pagerduty_settings_dry_run_prod` |  | set_env, sample_settings_dict, monkeypatch |
| `test_pagerduty_metrics_init` |  | sample_metrics |
| `test_metrics_init_failure` |  |  |
| `test_pagerduty_event_payload_success` |  |  |
| `test_pagerduty_event_payload_invalid_timestamp` |  |  |
| `test_pagerduty_api_request_trigger_success` |  |  |
| `test_pagerduty_api_request_missing_payload_trigger` |  |  |
| `test_pagerduty_api_request_sign` |  |  |
| `test_pagerduty_api_request_sign_missing_key_prod` |  | set_env, monkeypatch |
| `test_gateway_startup_success` | ✓ | gateway, sample_settings_dict |
| `test_gateway_shutdown_success` | ✓ | gateway |
| `test_send_request_success` | ✓ | mock_aiohttp_session, gateway |
| `test_send_request_permanent_failure` | ✓ | mock_aiohttp_session, gateway |
| `test_send_request_circuit_breaker` | ✓ | mock_aiohttp_session, gateway, monkeypatch |
| `test_enqueue_request_success` | ✓ | gateway |
| `test_enqueue_request_queue_full` | ✓ | gateway, sample_settings_dict |
| `test_trigger_success` | ✓ | gateway |
| `test_acknowledge_success` | ✓ | gateway |
| `test_resolve_success` | ✓ | gateway |

**Constants:** `mock_secrets_manager`, `mock_audit_logger`, `mock_alert_operator`, `test_dir`, `plugins_dir`, `pagerduty_file_path`, `spec`, `pagerduty_plugin`, `pytestmark`

---

## tests/test_plugins_pubsub_plugin.py
**Lines:** 671

### `AnalyzerCriticalError` (RuntimeError)

### `NonCriticalError` (Exception)

### `DummyAuditLogger`

| Method | Async | Args |
|--------|-------|------|
| `log_event` |  | self |

### `PubSubSettings`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `model_validate` |  | cls, data |

### `PubSubMetrics`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, registry |

### `AuditEvent`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `model_validate` |  | cls, data |
| `model_dump` |  | self |

### `CircuitBreaker`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, threshold, reset_seconds, metrics |
| `check` |  | self |
| `record_failure` |  | self |

### `PubSubGateway`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, settings, metrics |
| `startup` | ✓ | self |
| `shutdown` | ✓ | self |
| `publish` |  | self, event_name, service_name, details |
| `_worker` | ✓ | self |
| `_publish_batch` | ✓ | self, batch |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `alert_operator` |  | message, level |
| `scrub_sensitive_data` |  | data |
| `setup_logging` |  |  |
| `mock_audit_logger` |  |  |
| `mock_alert_operator` |  |  |
| `mock_scrub_sensitive_data` |  |  |
| `mock_secrets_manager` |  |  |
| `mock_pubsub_publisher` |  | monkeypatch |
| `mock_redis` |  | monkeypatch |
| `mock_tracer` |  |  |
| `set_env` |  | monkeypatch |
| `sample_settings_dict` |  |  |
| `sample_metrics` |  |  |
| `test_pubsub_settings_success` |  | sample_settings_dict |
| `test_pubsub_settings_invalid_project_id_prod` |  | set_env, sample_settings_dict |
| `test_pubsub_settings_invalid_topic_id_prod` |  | set_env, sample_settings_dict |
| `test_pubsub_settings_dry_run_prod` |  | set_env, sample_settings_dict |
| `test_pubsub_settings_missing_credentials_prod` |  | set_env, sample_settings_dict |
| `test_pubsub_settings_dummy_project_id_prod` |  | set_env, sample_settings_dict |
| `test_pubsub_metrics_init` |  | sample_metrics |
| `test_metrics_init_failure` |  | mock_alert_operator |
| `test_audit_event_success` |  |  |
| `test_audit_event_pii_scrubbing` |  | mock_scrub_sensitive_data |
| `test_audit_event_pii_detection_aborts` |  | mock_scrub_sensitive_data, mock_alert_operator |
| `test_circuit_breaker_initial_state` |  | sample_metrics |
| `test_circuit_breaker_trip` |  | sample_metrics, mock_alert_operator |
| `test_circuit_breaker_reset` |  | sample_metrics, mock_alert_operator |
| `test_gateway_init_success` | ✓ | sample_settings_dict, sample_metrics |
| `test_gateway_startup_success` | ✓ | mock_pubsub_publisher, sample_settings_dict, sample_metrics, mock_secrets_manager |
| `test_gateway_startup_topic_not_found` | ✓ | mock_pubsub_publisher, sample_settings_dict, mock_alert_operator, mock_secrets_manager |
| `test_gateway_startup_credentials_failure` | ✓ | mock_pubsub_publisher, sample_settings_dict, mock_secrets_manager, mock_alert_operator |
| `test_gateway_shutdown_success` | ✓ | mock_pubsub_publisher, sample_settings_dict, sample_metrics |
| `test_gateway_shutdown_timeout` | ✓ | mock_pubsub_publisher, sample_settings_dict, sample_metrics, mock_alert_operator |
| `test_publish_success` | ✓ | mock_pubsub_publisher, sample_settings_dict, sample_metrics, mock_secrets_manager |
| `test_publish_queue_full` | ✓ | mock_pubsub_publisher, sample_settings_dict, sample_metrics, mock_alert_operator, mock_secrets_manager |
| `test_publish_batch_success` | ✓ | mock_pubsub_publisher, sample_settings_dict, sample_metrics, mock_tracer, mock_secrets_manager |
| `test_publish_batch_service_unavailable` | ✓ | mock_pubsub_publisher, sample_settings_dict, sample_metrics, mock_alert_operator, mock_secrets_manager |
| `test_worker_success` | ✓ | mock_pubsub_publisher, sample_settings_dict, sample_metrics, mock_secrets_manager |
| `test_worker_dry_run` | ✓ | mock_pubsub_publisher, sample_settings_dict, sample_metrics, mock_secrets_manager |
| `test_main_block_prod` |  | set_env, mock_alert_operator |

**Constants:** `PRODUCTION_MODE`, `logger`, `audit_logger`, `SECRETS_MANAGER`

---

## tests/test_plugins_rabbitmq_plugin.py
**Lines:** 714

### `AnalyzerCriticalError` (RuntimeError)

### `NonCriticalError` (Exception)

### `DummyAuditLogger`

| Method | Async | Args |
|--------|-------|------|
| `log_event` |  | self |

### `RabbitMQSettings`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |

### `RabbitMQMetrics`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, registry |

### `AuditEvent`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `model_dump` |  | self, exclude |
| `_sign_event` |  | self |

### `CircuitBreaker`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, threshold, reset_seconds, metrics |
| `check` |  | self |
| `record_failure` |  | self |
| `record_success` |  | self |

### `RabbitMQGateway`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, settings, metrics |
| `startup` | ✓ | self |
| `shutdown` | ✓ | self |
| `publish` |  | self, event_name, service_name, details, routing_key |
| `_publish_batch` | ✓ | self, batch |
| `_worker` | ✓ | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `alert_operator` |  | message, level |
| `scrub_sensitive_data` |  | data |
| `setup_logging` |  |  |
| `mock_audit_logger` |  |  |
| `mock_alert_operator` |  |  |
| `mock_scrub_sensitive_data` |  |  |
| `mock_secrets_manager` |  |  |
| `mock_aiormq` |  | monkeypatch |
| `mock_prometheus_registry` |  |  |
| `set_env` |  | monkeypatch |
| `sample_settings_dict` |  |  |
| `sample_metrics` |  | mock_prometheus_registry |
| `test_rabbitmq_settings_success` |  | sample_settings_dict, mock_secrets_manager |
| `test_rabbitmq_settings_insecure_url_prod` |  | set_env, sample_settings_dict, mock_secrets_manager |
| `test_rabbitmq_settings_default_credentials_prod` |  | set_env, sample_settings_dict, mock_secrets_manager |
| `test_rabbitmq_settings_not_in_allowlist_prod` |  | set_env, sample_settings_dict, mock_secrets_manager |
| `test_rabbitmq_settings_wildcard_exchange_prod` |  | set_env, sample_settings_dict |
| `test_rabbitmq_settings_invalid_routing_keys_regex` |  | sample_settings_dict |
| `test_rabbitmq_settings_dry_run_prod` |  | set_env, sample_settings_dict |
| `test_rabbitmq_metrics_init` |  | sample_metrics |
| `test_metrics_init_failure` |  | mock_prometheus_registry, mock_alert_operator |
| `test_audit_event_success` |  | mock_secrets_manager |
| `test_audit_event_pii_scrubbing` |  | mock_scrub_sensitive_data |
| `test_audit_event_pii_detection_aborts` |  | mock_scrub_sensitive_data, mock_alert_operator |
| `test_audit_event_sign` |  | mock_secrets_manager |
| `test_audit_event_sign_missing_key_prod` |  | set_env, mock_secrets_manager |
| `test_circuit_breaker_initial_state` |  | sample_metrics |
| `test_circuit_breaker_trip` |  | sample_metrics, mock_alert_operator |
| `test_circuit_breaker_reset` |  | sample_metrics, mock_alert_operator |
| `test_gateway_init_success` | ✓ | sample_settings_dict, sample_metrics, mock_secrets_manager |
| `test_gateway_init_insecure_url_prod` | ✓ | set_env, sample_settings_dict, mock_secrets_manager, mock_alert_operator |
| `test_gateway_startup_success` | ✓ | mock_aiormq, sample_settings_dict, sample_metrics |
| `test_gateway_startup_connection_failure` | ✓ | mock_aiormq, sample_settings_dict, sample_metrics, mock_alert_operator |
| `test_gateway_shutdown_success` | ✓ | mock_aiormq, sample_settings_dict, sample_metrics |
| `test_gateway_shutdown_timeout` | ✓ | mock_aiormq, sample_settings_dict, sample_metrics, mock_alert_operator |
| `test_publish_success` | ✓ | mock_aiormq, sample_settings_dict, sample_metrics, mock_secrets_manager |
| `test_publish_queue_full` | ✓ | mock_aiormq, sample_settings_dict, sample_metrics, mock_alert_operator, mock_secrets_manager |
| `test_publish_invalid_routing_key_prod` | ✓ | set_env, sample_settings_dict, mock_alert_operator |
| `test_publish_batch_success` | ✓ | mock_aiormq, sample_settings_dict, sample_metrics, mock_tracer, mock_secrets_manager |
| `test_publish_batch_connection_error` | ✓ | mock_aiormq, sample_settings_dict, sample_metrics, mock_alert_operator, mock_secrets_manager |
| `test_worker_success` | ✓ | mock_aiormq, sample_settings_dict, sample_metrics, mock_secrets_manager |
| `test_worker_dry_run` | ✓ | mock_aiormq, sample_settings_dict, sample_metrics, mock_secrets_manager |
| `test_main_block_prod` |  | set_env, mock_alert_operator |

**Constants:** `PRODUCTION_MODE`, `logger`, `audit_logger`, `SECRETS_MANAGER`

---

## tests/test_plugins_siem_plugin.py
**Lines:** 836

### `MockAsyncFile`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `write` | ✓ | self, data |
| `flush` | ✓ | self |
| `close` | ✓ | self |
| `__aenter__` | ✓ | self |
| `__aexit__` | ✓ | self |
| `__aiter__` |  | self |
| `__anext__` | ✓ | self |

### `MockJsonFormatter`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `add_fields` |  | self, log_record, message_dict |

### `AnalyzerCriticalError` (Exception)

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_alert_operator` |  | message, level |
| `_restore_original_aiofiles` |  |  |
| `setup_logging` |  |  |
| `reset_mocks` |  |  |
| `restore_aiofiles_after_module` |  |  |
| `mock_aiohttp_session` |  |  |
| `mock_psutil` |  | monkeypatch |
| `set_env` |  | monkeypatch |
| `temp_dir` |  | tmp_path |
| `sample_settings_dict` |  |  |
| `sample_metrics` |  |  |
| `test_siem_target_validate_url_protocol_prod` |  | monkeypatch |
| `test_siem_gateway_settings_success` |  | sample_settings_dict |
| `test_siem_gateway_settings_default_secret` |  | monkeypatch, sample_settings_dict |
| `test_siem_gateway_settings_admin_api_host_prod` |  | monkeypatch, sample_settings_dict |
| `test_siem_gateway_settings_immutable_prod` |  | monkeypatch, sample_settings_dict |
| `test_siem_metrics_init` |  |  |
| `test_siem_metrics_update_system_metrics` |  | mock_psutil |
| `test_siem_event_success` |  |  |
| `test_siem_event_pii_scrubbing` |  |  |
| `test_siem_event_pattern_scrubbing` |  |  |
| `test_json_hec_serializer` |  |  |
| `test_gzip_json_hec_serializer` |  |  |
| `test_circuit_breaker_initial_state` |  | sample_metrics |
| `test_circuit_breaker_trip` |  | sample_metrics |
| `test_circuit_breaker_reset` |  | sample_metrics |
| `test_circuit_breaker_check_open_raises` |  | sample_metrics |
| `test_circuit_breaker_record_success` |  | sample_metrics |
| `test_token_bucket_acquire` | ✓ | sample_metrics |
| `test_token_bucket_rate_limit_429` | ✓ | sample_metrics |
| `test_token_bucket_refill` | ✓ | sample_metrics |
| `test_siem_gateway_init` |  | sample_settings_dict, sample_metrics |
| `test_siem_gateway_pause_resume` |  | sample_settings_dict, sample_metrics |
| `test_siem_gateway_manager_init` |  | sample_settings_dict, sample_metrics |
| `test_siem_gateway_manager_register_serializer` |  | sample_settings_dict, sample_metrics |
| `test_siem_gateway_manager_startup_prod_checks` | ✓ | monkeypatch, sample_settings_dict, sample_metrics |
| `test_siem_gateway_manager_publish_unknown_target` |  | sample_settings_dict, sample_metrics |
| `test_siem_gateway_manager_health_check` | ✓ | sample_settings_dict, sample_metrics, temp_dir |
| `test_dead_letter_to_file` | ✓ | temp_dir |

**Constants:** `mock_secrets_manager`, `mock_audit_logger`, `_original_aiofiles`, `_original_aiofiles_threadpool`, `_original_aiofiles_threadpool_binary`, `_original_aiofiles_os`, `mock_aiofiles`, `mock_aiofiles_open`, `mock_jsonlogger`, `test_dir`, `plugins_dir`, `siem_file_path`, `siem_code`, `siem_code`, `siem_code`, ...+6 more

---

## tests/test_plugins_slack_plugin.py
**Lines:** 777

### `AnalyzerCriticalError` (RuntimeError)

### `NonCriticalError` (Exception)

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `setup_environment` |  | monkeypatch |
| `setup_logging` |  |  |
| `mock_audit_logger` |  |  |
| `mock_alert_operator` |  |  |
| `mock_secrets_manager` |  |  |
| `mock_aiohttp_session` |  |  |
| `mock_psutil_fixture` |  | monkeypatch |
| `mock_aiofiles_open` |  |  |
| `mock_tracer` |  |  |
| `set_env` |  | monkeypatch |
| `temp_dir` |  | tmp_path |
| `sample_settings_dict` |  |  |
| `sample_metrics` |  |  |
| `test_slack_target_validate_url_protocol_prod` |  | monkeypatch |
| `test_slack_gateway_settings_success` |  | sample_settings_dict |
| `test_slack_gateway_settings_default_secret` |  | sample_settings_dict |
| `test_slack_gateway_settings_admin_api_host_prod` |  | monkeypatch, sample_settings_dict |
| `test_slack_gateway_settings_immutable_prod` |  | monkeypatch, sample_settings_dict |
| `test_slack_gateway_settings_dry_run_prod` | ✓ | monkeypatch, sample_settings_dict, mock_secrets_manager |
| `test_slack_gateway_settings_url_not_in_allowlist_prod` |  | monkeypatch, sample_settings_dict |
| `test_slack_metrics_init` |  |  |
| `test_slack_metrics_update_system_metrics` |  | sample_metrics |
| `test_slack_event_success` |  |  |
| `test_slack_event_pii_scrubbing` |  |  |
| `test_slack_block_kit_serializer` |  |  |
| `test_persistent_wal_queue_startup` | ✓ | temp_dir, mock_secrets_manager |
| `test_persistent_wal_queue_put_get` | ✓ | temp_dir, mock_secrets_manager |
| `test_circuit_breaker_initial_state` |  | sample_metrics |
| `test_circuit_breaker_trip` |  | sample_metrics |
| `test_circuit_breaker_reset` |  | sample_metrics |
| `test_circuit_breaker_check_open_raises` |  | sample_metrics |
| `test_circuit_breaker_record_success` |  | sample_metrics |
| `test_token_bucket_acquire` | ✓ | sample_metrics |
| `test_token_bucket_rate_limit_429` | ✓ | sample_metrics |
| `test_token_bucket_refill` | ✓ | sample_metrics |
| `test_slack_gateway_init` | ✓ | sample_settings_dict, sample_metrics, mock_secrets_manager |
| `test_slack_gateway_pause_resume` |  | sample_settings_dict, sample_metrics |
| `test_slack_gateway_manager_init` |  | sample_settings_dict, sample_metrics |
| `test_slack_gateway_manager_register_serializer` |  | sample_settings_dict, sample_metrics |
| `test_slack_gateway_manager_startup_prod_no_opentelemetry` | ✓ | monkeypatch, sample_settings_dict, sample_metrics |
| `test_slack_gateway_manager_health_check` | ✓ | sample_settings_dict, sample_metrics, temp_dir |
| `test_dead_letter_to_file` | ✓ | temp_dir, mock_secrets_manager |

**Constants:** `mock_psutil`, `mock_aiofiles`

---

## tests/test_plugins_sns_plugin.py
**Lines:** 417

### `MockSecretsManager`

| Method | Async | Args |
|--------|-------|------|
| `get_secret` |  | self, key, required |

### `AnalyzerCriticalError` (RuntimeError)

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `setup_logging` |  |  |
| `sample_settings_dict` |  |  |
| `sample_metrics` |  |  |
| `test_settings_dict_creation` |  | sample_settings_dict |
| `test_sns_target_creation` |  |  |
| `test_sns_event_creation` |  |  |
| `test_json_serializer` |  |  |
| `test_circuit_breaker_initialization` |  |  |
| `test_token_bucket_initialization` |  |  |
| `test_dead_letter_function` | ✓ |  |
| `test_sns_metrics_creation` |  |  |
| `test_gateway_settings_creation` |  | sample_settings_dict |
| `test_module_imports` |  |  |
| `test_persistent_wal_queue` | ✓ |  |
| `test_sns_gateway` | ✓ |  |

**Constants:** `mock_secrets`, `mock_alert`, `mock_audit_logger`

---

## tests/test_plugins_wasm_runner.py
**Lines:** 543

### `AnalyzerCriticalError` (Exception)

### `NonCriticalError` (Exception)

### `DummyAuditLogger`

| Method | Async | Args |
|--------|-------|------|
| `log_event` |  | self |

### `WasmManifestModel` (object)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |

### `WasmRuntimeError` (RuntimeError)

### `WasmRunner`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, name, manifest, wasm_path, whitelist |
| `run_function` | ✓ | self, func_name |
| `plugin_health` | ✓ | self |
| `reload_if_changed` | ✓ | self, operator_approved |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `alert_operator` |  | message, level |
| `scrub_sensitive_data` |  | data |
| `host_log` |  | caller, ptr, size |
| `list_plugins` |  | plugin_dir, whitelist_dirs |
| `generate_plugin_docs` |  | plugin_dir, whitelist_dirs, output_path |
| `setup_logging` |  |  |
| `mock_audit_logger` |  |  |
| `mock_alert_operator` |  |  |
| `mock_scrub_sensitive_data` |  |  |
| `mock_secrets_manager` |  |  |
| `mock_wasmtime` |  | monkeypatch |
| `temp_wasm_file` |  | tmp_path |
| `temp_plugins_dir` |  | tmp_path |
| `set_env` |  | monkeypatch |
| `test_manifest_model_valid` |  |  |
| `test_manifest_model_invalid_version` |  |  |
| `test_manifest_model_sandbox_disabled_prod` |  | set_env |
| `test_manifest_model_invalid_memory_limit` |  |  |
| `test_wasm_runner_init_success` | ✓ | mock_secrets_manager, mock_wasmtime, temp_wasm_file, mock_alert_operator |
| `test_wasm_runner_init_demo_prod` | ✓ | set_env, mock_wasmtime, temp_wasm_file, mock_alert_operator |
| `test_wasm_runner_init_file_not_found` | ✓ | mock_wasmtime, mock_alert_operator |
| `test_wasm_runner_init_outside_whitelist` | ✓ | mock_wasmtime, mock_alert_operator, temp_wasm_file |
| `test_wasm_runner_run_function_success` | ✓ | mock_wasmtime |
| `test_wasm_runner_plugin_health_success` | ✓ | mock_wasmtime, temp_wasm_file |
| `test_wasm_runner_reload_if_changed` | ✓ | mock_wasmtime, temp_wasm_file |
| `test_list_plugins_valid` |  | temp_plugins_dir, mock_scrub_sensitive_data |
| `test_list_plugins_non_whitelisted` |  | temp_plugins_dir, set_env |
| `test_generate_plugin_docs_success` |  | temp_plugins_dir |
| `test_generate_plugin_docs_non_whitelisted` |  | temp_plugins_dir, set_env |

**Constants:** `PRODUCTION_MODE`, `logger`, `audit_logger`, `SECRETS_MANAGER`, `WHITELISTED_HOST_FUNCTIONS`

---

## tests/test_self_healing_import_fixer_analyzer.py
**Lines:** 381

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `valid_config_yaml_path` |  | tmp_path |
| `valid_config_json_path` |  | tmp_path |
| `malformed_config_path` |  | tmp_path |
| `invalid_schema_config_path` |  | tmp_path |
| `mock_alert_operator` |  |  |
| `mock_audit_logger` |  |  |
| `tmp_config_file` |  | tmp_path |
| `mock_sys_exit` |  |  |
| `mock_os_env` |  |  |
| `test_load_config_valid_yaml` |  | valid_config_yaml_path, mock_audit_logger |
| `test_load_config_valid_json` |  | valid_config_json_path, mock_audit_logger |
| `test_load_config_invalid_file_path` |  | mock_alert_operator |
| `test_load_config_malformed_file` |  | malformed_config_path, mock_alert_operator |
| `test_load_config_invalid_schema` |  | invalid_schema_config_path, mock_alert_operator |
| `test_production_mode_enforcement` |  |  |
| `test_prod_mode_blocks_demo_mode` |  | tmp_path |
| `test_prod_mode_blocks_mock_llm` |  | tmp_path |
| `test_prod_mode_blocks_disabled_audit_logging` |  | tmp_path |
| `test_production_mode_flag_precedence` |  |  |
| `test_main_analyze_action_success` |  | tmp_path |

---

## tests/test_self_healing_import_fixer_analyzer_integration.py
**Lines:** 354

### `FakeRedis`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `setex` |  | self, key, ttl, value |
| `get` |  | self, key |
| `incr` |  | self, key |

### `FakeLLMClient`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `aclose` | ✓ | self |

### `FakeAIManager`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `generate_async` | ✓ | self |
| `generate_sync` |  | self |
| `aclose` | ✓ | self |

### `AuditSink`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `emit` |  | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `import_module_with_fallback` |  |  |
| `find_click_root_command` |  | mod |
| `write_min_policy` |  | path, proj_root |
| `make_tiny_project` |  | base |
| `test_analyzer_stack_end_to_end` |  | tmp_path, monkeypatch |

---

## tests/test_self_healing_import_fixer_cache_layer.py
**Lines:** 273

### `FakeRedisClient`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `ping` | ✓ | self |
| `get` | ✓ | self, key |
| `setex` | ✓ | self, key, ttl, value |
| `incr` | ✓ | self, key |

### `FakeRedisModule`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `Redis` |  | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_metrics` |  |  |
| `mock_loggers` |  |  |
| `mock_audit_logger` |  |  |
| `test_get_cache_uses_in_memory_when_no_redis_and_no_project_root` | ✓ | monkeypatch |
| `test_get_cache_uses_file_cache_when_project_root_provided` | ✓ | tmp_path, monkeypatch |
| `test_get_cache_prefers_redis_when_available` | ✓ | monkeypatch |
| `test_inmemory_cache_expiration` | ✓ |  |
| `test_file_cache_roundtrip_and_expiration` | ✓ | tmp_path |
| `test__connect_redis_success` | ✓ | monkeypatch |
| `test__connect_redis_failure` | ✓ | monkeypatch |
| `test_fallback_warning_helper_is_called` | ✓ | monkeypatch |

**Constants:** `PKG_PATH`

---

## tests/test_self_healing_import_fixer_cli.py
**Lines:** 516

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_core_dependencies` |  |  |
| `setup_teardown_env_vars` |  |  |
| `mock_plugin_manager` |  |  |
| `test_project_setup` |  | tmp_path |
| `test_validate_path_argument_security_checks` |  | path_arg, is_dir, allow_symlink, should_raise, tmp_path |
| `test_main_handles_analyze_command` |  | test_project_setup, mock_plugin_manager, mock_core_dependencies |
| `test_heal_command_in_prod_requires_interactive` |  | test_project_setup, mock_plugin_manager, mock_core_dependencies |
| `test_heal_command_in_prod_forbids_yes_flag` |  | test_project_setup, mock_plugin_manager, mock_core_dependencies |
| `test_serve_command_in_prod_enforces_security` |  | test_project_setup, mock_plugin_manager, mock_core_dependencies |
| `test_main_loads_plugins_from_config` | ✓ | mock_plugin_manager, test_project_setup |
| `test_selftest_command_runs_diagnostics` |  | mock_plugin_manager, mock_core_dependencies |
| `test_cli_execution_failure_logs_and_aborts` |  | mock_plugin_manager, mock_core_dependencies |

---

## tests/test_self_healing_import_fixer_compat_core.py
**Lines:** 185

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `setup_logging` |  |  |
| `mock_env_vars` |  |  |
| `mock_core_modules` |  |  |
| `mock_redis` |  |  |
| `mock_boto3` |  |  |
| `test_fallback_behavior` | ✓ | mock_env_vars, mock_redis, mock_boto3 |
| `test_health_check` |  | mock_env_vars, mock_redis, mock_boto3 |
| `test_verify_audit_log` |  |  |
| `test_environment_variables` |  |  |
| `test_metrics_noop` |  |  |

**Constants:** `PKG_PATH`

---

## tests/test_self_healing_import_fixer_compat_core_fixes.py
**Lines:** 340

### `TestPrometheusMetricsDeduplication`

| Method | Async | Args |
|--------|-------|------|
| `reset_metrics_registry` |  | self |
| `test_metrics_created_once` |  | self |
| `test_metrics_reuse_existing_from_registry` |  | self |
| `test_concurrent_metric_creation_thread_safe` |  | self |
| `test_metrics_disabled_returns_noop` |  | self |
| `test_all_seven_metrics_created` |  | self |

### `TestRedisConnectionFallback`

| Method | Async | Args |
|--------|-------|------|
| `reset_redis_client` |  | self |
| `test_redis_url_preferred` |  | self |
| `test_redis_host_port_fallback` |  | self |
| `test_railway_variables_fallback` |  | self |
| `test_default_localhost_fallback` |  | self |
| `test_connection_failure_returns_none` |  | self |
| `test_ping_failure_logs_warning` |  | self |
| `test_redis_client_cached_after_success` |  | self |

### `TestIntegration`

| Method | Async | Args |
|--------|-------|------|
| `reset_state` |  | self |
| `test_metrics_and_redis_both_work` |  | self |

**Constants:** `PKG_PATH`

---

## tests/test_self_healing_import_fixer_core_ai.py
**Lines:** 400

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `valid_ai_config` |  |  |
| `mock_secrets_manager` |  |  |
| `mock_audit_logger_ai` |  |  |
| `mock_alert_operator` |  |  |
| `mock_httpx_client` |  |  |
| `mock_sys_exit` |  |  |
| `mock_openai_client` |  |  |
| `mock_llm_client` |  | mock_openai_client, mock_httpx_client |
| `test_ai_manager_init_success` | ✓ | valid_ai_config, mock_secrets_manager, mock_audit_logger_ai, mock_httpx_client, mock_openai_client |
| `test_ai_manager_init_missing_api_key_exits` | ✓ | valid_ai_config, mock_secrets_manager, mock_alert_operator, mock_sys_exit |
| `test_ai_manager_init_missing_endpoint_exits` | ✓ | valid_ai_config, mock_alert_operator, mock_sys_exit, mock_secrets_manager |
| `test_ai_manager_init_no_https_in_prod_exits` | ✓ | valid_ai_config, mock_alert_operator, mock_sys_exit, mock_secrets_manager |
| `test_ai_manager_init_no_proxy_in_prod_exits` | ✓ | valid_ai_config, mock_alert_operator, mock_sys_exit, mock_secrets_manager |
| `test_ai_manager_init_auto_apply_in_prod_exits` | ✓ | valid_ai_config, mock_alert_operator, mock_sys_exit, mock_secrets_manager |
| `test_call_llm_api_success` | ✓ | mock_llm_client, mock_audit_logger_ai, mock_secrets_manager, valid_ai_config |
| `test_call_llm_api_failure_and_retry` | ✓ | mock_llm_client, mock_audit_logger_ai, mock_secrets_manager, valid_ai_config, mock_alert_operator |
| `test_token_quota_enforcement_waits` | ✓ | mock_llm_client, mock_secrets_manager, valid_ai_config, mock_audit_logger_ai, mock_alert_operator |
| `test_token_quota_overrun_aborts` | ✓ | mock_llm_client, mock_secrets_manager, valid_ai_config, mock_audit_logger_ai, mock_alert_operator |
| `test_get_ai_suggestions_success` | ✓ | mock_llm_client, mock_secrets_manager, valid_ai_config |
| `test_get_ai_patch_success` | ✓ | mock_llm_client, mock_secrets_manager, valid_ai_config |
| `test_ai_public_functions_handle_empty_response` | ✓ | mock_llm_client, mock_secrets_manager, valid_ai_config |

**Constants:** `test_dir`, `parent_dir`, `pytestmark`

---

## tests/test_self_healing_import_fixer_core_audit.py
**Lines:** 338

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `test_missing_methods` |  |  |
| `test_splunk_client_initialization` |  |  |
| `test_log_critical_event_references` | ✓ |  |
| `test_asyncio_create_task_issue` |  |  |
| `test_os_chown_windows_compatibility` |  |  |
| `test_integrity_monitor_thread` |  |  |
| `test_undefined_imports` |  |  |
| `test_file_operations_error_handling` |  |  |
| `test_summary_of_issues` |  |  |

**Constants:** `current_dir`, `analyzer_dir`, `mock_secrets_manager`, `mock_secrets`, `mock_utils`, `original_exit`

---

## tests/test_self_healing_import_fixer_core_graph.py
**Lines:** 363

### `MockCoreUtils`
**Attributes:** alert_operator, scrub_secrets

### `MockCoreAudit`
**Attributes:** audit_logger

### `MockCoreSecrets`
**Attributes:** SECRETS_MANAGER

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_alert_operator_graph` |  |  |
| `mock_audit_logger_graph` |  |  |
| `mock_os_env_graph` |  |  |
| `mock_graphviz` |  |  |
| `mock_shutil_which` |  |  |
| `test_project_setup` |  | tmp_path |
| `test_init_success` |  | test_project_setup, mock_audit_logger_graph |
| `test_init_invalid_project_root` |  | mock_alert_operator_graph |
| `test_init_not_in_whitelisted_path` |  | tmp_path, mock_alert_operator_graph |
| `test_build_graph_no_python_files` |  | tmp_path, mock_audit_logger_graph |
| `test_build_graph_with_files` |  | test_project_setup, mock_audit_logger_graph |
| `test_detect_cycles` |  | test_project_setup |
| `test_detect_dead_nodes` |  | test_project_setup |
| `test_visualize_graph_success` |  | tmp_path, mock_graphviz, mock_shutil_which |
| `test_visualize_graph_disabled_in_production` |  | tmp_path, mock_graphviz, mock_alert_operator_graph |
| `test_max_files_limit` |  | tmp_path, mock_alert_operator_graph |
| `test_parsing_error_threshold` |  | test_project_setup, mock_alert_operator_graph |

**Constants:** `current_dir`, `analyzer_dir`, `mock_alert_operator`, `mock_scrub_secrets`, `mock_audit_logger`, `mock_secrets_manager`, `core_graph_path`, `source`, `source`, `source`, `spec`, `core_graph_module`, `mock_redis_class`, `mock_redis_instance`, `ImportGraphAnalyzer`, ...+3 more

---

## tests/test_self_healing_import_fixer_core_policy.py
**Lines:** 343

### `MockCoreUtils`
**Attributes:** alert_operator, scrub_secrets

### `MockCoreAudit`
**Attributes:** audit_logger

### `MockCoreSecrets`
**Attributes:** SECRETS_MANAGER

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_get_policy_hmac_key` |  |  |
| `mock_alert_operator_policy` |  |  |
| `mock_audit_logger_policy` |  |  |
| `mock_secrets_manager_policy` |  |  |
| `policy_file_with_signature` |  | tmp_path |
| `mock_codebase_data` |  |  |
| `test_init_with_valid_policy_succeeds` |  | policy_file_with_signature, mock_audit_logger_policy, mock_secrets_manager_policy |
| `test_init_with_missing_file_raises_critical_error` |  |  |
| `test_init_with_invalid_json_raises_critical_error` |  | tmp_path |
| `test_init_with_invalid_schema_raises_critical_error` |  | tmp_path |
| `test_init_with_tampered_policy_fails` |  | tmp_path, mock_alert_operator_policy |
| `test_enforce_import_restriction_violations` |  | policy_file_with_signature, mock_codebase_data |
| `test_enforce_dependency_limit_violations` |  | policy_file_with_signature, mock_codebase_data |
| `test_enforce_cycle_prevention_violations` |  | policy_file_with_signature, mock_codebase_data |
| `test_enforce_naming_convention_violations` |  | policy_file_with_signature, mock_codebase_data |
| `test_enforcement_error_handling` |  | policy_file_with_signature, mock_codebase_data, mock_alert_operator_policy |

**Constants:** `current_dir`, `analyzer_dir`, `mock_alert_operator`, `mock_scrub_secrets`, `mock_audit_logger`, `mock_secrets_manager`, `mock_boto3`, `mock_redis`, `mock_redis_async`, `mock_redis_instance`, `core_policy_path`, `source`, `source`, `source`, `spec`, ...+6 more

---

## tests/test_self_healing_import_fixer_core_report.py
**Lines:** 337

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `flask_app` |  |  |
| `test_init_with_valid_dir_succeeds` |  | tmp_path |
| `test_init_with_unapproved_dir_in_prod_exits` |  | tmp_path |
| `test_init_with_unwritable_dir_exits` |  | tmp_path |
| `test_generate_report_formats_and_saves_correctly` |  | tmp_path, report_format, file_ext |
| `test_generate_pdf_report_calls_weasyprint` |  | tmp_path, monkeypatch |
| `test_generate_report_catches_formatting_errors` |  | tmp_path |
| `test_generate_report_catches_saving_io_errors` |  | tmp_path |
| `test_public_generate_report_calls_encryption_in_prod` |  | tmp_path, monkeypatch |
| `test_dashboard_login_success` |  | monkeypatch, flask_app |
| `test_dashboard_login_failure` |  | monkeypatch, flask_app |
| `test_get_report_endpoint_success_no_encryption` |  | tmp_path, monkeypatch, flask_app |
| `test_get_report_endpoint_with_encryption_in_prod` |  | tmp_path, monkeypatch, flask_app |
| `test_get_report_endpoint_path_traversal_prevention` |  | tmp_path, monkeypatch |

---

## tests/test_self_healing_import_fixer_core_secrets.py
**Lines:** 283

### `MockClientError` (Exception)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, error_response, operation_name |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `cleanup_env` |  |  |
| `env_secrets_manager` |  |  |
| `local_enc_secrets_manager` |  | monkeypatch, tmp_path |
| `aws_secrets_manager` |  | monkeypatch |
| `test_env_get_set_delete_list` |  | env_secrets_manager |
| `test_local_encrypted_get_set_delete_list` |  | local_enc_secrets_manager |
| `test_local_encrypted_rotation` |  | local_enc_secrets_manager |
| `test_local_encrypted_cache_expiry` |  | local_enc_secrets_manager, monkeypatch |
| `test_local_encrypted_clear_cache` |  | local_enc_secrets_manager |
| `test_local_encrypted_stats` |  | local_enc_secrets_manager |
| `test_secret_policy_validation` |  | local_enc_secrets_manager |
| `test_list_secrets_empty` |  | local_enc_secrets_manager |
| `test_aws_secrets_manager_get_set_delete` |  | aws_secrets_manager |
| `test_aws_secrets_manager_list` |  | aws_secrets_manager |
| `test_get_secret_returns_none_on_missing_env` |  | env_secrets_manager |
| `test_set_secret_handles_unknown_provider` |  |  |
| `test_delete_secret_handles_unknown_provider` |  |  |
| `test_list_secrets_handles_unknown_provider` |  |  |
| `test_get_secret_handles_unknown_provider` |  |  |

---

## tests/test_self_healing_import_fixer_core_security.py
**Lines:** 307

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `patch_tool_path_and_subprocess` |  | monkeypatch |
| `mock_alert_operator_security` |  | mocker |
| `mock_audit_logger_security` |  | monkeypatch |
| `mock_sys_exit_security` |  | mocker |
| `test_security_project` |  | tmp_path |
| `test_init_success_with_available_tools` |  | test_security_project, mock_audit_logger_security |
| `test_init_missing_bandit_exits` |  | monkeypatch, test_security_project, mock_alert_operator_security, mock_sys_exit_security, mock_audit_logger_security |
| `test_init_invalid_project_root_exits` |  | mock_alert_operator_security |
| `test_run_subprocess_safely_success` |  | monkeypatch, test_security_project |
| `test_run_subprocess_safely_failure_raises_exception` |  | monkeypatch, test_security_project, mock_alert_operator_security |
| `test_run_subprocess_safely_file_not_found` |  | monkeypatch, test_security_project, mock_alert_operator_security |
| `test_run_bandit_success_no_issues` |  | monkeypatch, test_security_project, mock_audit_logger_security |
| `test_run_bandit_with_issues` |  | monkeypatch, test_security_project, mock_audit_logger_security, mock_alert_operator_security |
| `test_run_pip_audit_with_vulnerabilities` |  | monkeypatch, test_security_project, mock_audit_logger_security, mock_alert_operator_security |
| `test_security_health_check_success` |  | test_security_project, mock_audit_logger_security |
| `test_security_health_check_failure_and_exit` |  | monkeypatch, test_security_project, mock_alert_operator_security, mock_sys_exit_security |

---

## tests/test_self_healing_import_fixer_core_utils.py
**Lines:** 643

### `TestAlertSystem`

| Method | Async | Args |
|--------|-------|------|
| `setup` |  | self |
| `test_alert_operator_basic` |  | self, caplog |
| `test_alert_operator_with_details` |  | self, caplog |
| `test_alert_deduplication` |  | self, caplog |
| `test_alert_rate_limiting` |  | self |
| `test_critical_alerts_bypass_rate_limit` |  | self |
| `test_slack_alert` |  | self, mock_urlopen |
| `test_sns_alert` |  | self, mock_boto_client |
| `test_email_alert` |  | self, mock_smtp |
| `test_multiple_channels` |  | self, caplog |

### `TestCircuitBreaker`

| Method | Async | Args |
|--------|-------|------|
| `test_circuit_breaker_normal_operation` |  | self |
| `test_circuit_breaker_opens_after_failures` |  | self |
| `test_circuit_breaker_half_open_state` |  | self |
| `test_get_circuit_breaker_singleton` |  | self |

### `TestRateLimiter`

| Method | Async | Args |
|--------|-------|------|
| `test_rate_limiter_allows_within_limit` |  | self |
| `test_rate_limiter_window_reset` |  | self |
| `test_rate_limiter_wait_if_needed` |  | self |
| `test_rate_limiter_thread_safety` |  | self |

### `TestRetryMechanism`

| Method | Async | Args |
|--------|-------|------|
| `test_retry_successful_on_second_attempt` |  | self |
| `test_retry_exhausts_attempts` |  | self |
| `test_async_retry` | ✓ | self |
| `test_retry_with_specific_exceptions` |  | self |

### `TestCaching`

| Method | Async | Args |
|--------|-------|------|
| `setup` |  | self |
| `test_cache_hit` |  | self |
| `test_cache_expiration` |  | self |
| `test_cache_different_args` |  | self |

### `TestSecurityUtilities`

| Method | Async | Args |
|--------|-------|------|
| `test_scrub_secrets_dict` |  | self |
| `test_scrub_secrets_nested` |  | self |
| `test_secure_hash` |  | self |
| `test_sanitize_path` |  | self |

### `TestValidation`

| Method | Async | Args |
|--------|-------|------|
| `test_validate_required_fields` |  | self |
| `test_validate_types` |  | self |
| `test_validate_string_length` |  | self |

### `TestOperationalUtilities`

| Method | Async | Args |
|--------|-------|------|
| `test_generate_correlation_id` |  | self |
| `test_get_system_health` |  | self, mock_disk, mock_memory, mock_cpu |
| `test_timing_context` |  | self |
| `test_encode_for_logging` |  | self |

---

## tests/test_self_healing_import_fixer_e2e_analyzer.py
**Lines:** 265

### `MockRegulatoryAuditLogger`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `log_event` |  | self |
| `log_critical_event` | ✓ | self |
| `verify_integrity` | ✓ | self |

### `TestAnalyzerE2E`

| Method | Async | Args |
|--------|-------|------|
| `setup_and_teardown` |  | self |
| `create_simple_project` |  | self |
| `create_config_file` |  | self |
| `create_policy_file` |  | self |
| `test_graph_analysis_simple` |  | self |
| `test_report_generation` |  | self |
| `test_error_handling` |  | self |
| `test_secrets_management` |  | self |

**Constants:** `mock_audit_module`, `logger`

---

## tests/test_self_healing_import_fixer_e2e_cli.py
**Lines:** 333

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `test_e2e_cli` |  | tmp_path, monkeypatch |

---

## tests/test_self_healing_import_fixer_fixer_ai.py
**Lines:** 480

### `MockRateLimitError` (Exception)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, response, body |

### `MockAPIError` (Exception)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, request, body |

### `MockTimeoutException` (Exception)

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_retry` |  |  |
| `reset_globals` |  |  |
| `mock_core_dependencies` |  |  |
| `mock_redis_client` | ✓ |  |
| `mock_httpx_client` |  |  |
| `mock_openai_client` |  |  |
| `setup_teardown_env_vars` |  |  |
| `test_aimanager_init_success` |  | mock_core_dependencies, mock_httpx_client, mock_openai_client |
| `test_aimanager_init_defaults` |  | mock_core_dependencies, mock_httpx_client, mock_openai_client |
| `test_production_mode_requires_https` |  | mock_core_dependencies |
| `test_production_mode_requires_proxy` |  | mock_core_dependencies |
| `test_production_mode_forbids_auto_apply` |  | mock_core_dependencies |
| `test_sanitize_prompt_valid` |  |  |
| `test_sanitize_prompt_rejects_injection` |  |  |
| `test_sanitize_prompt_rejects_control_chars` |  |  |
| `test_sanitize_prompt_truncates_long` |  |  |
| `test_sanitize_response_removes_patterns` |  | mock_core_dependencies |
| `test_token_quota_enforcement` | ✓ | mock_core_dependencies, mock_httpx_client, mock_openai_client |
| `test_call_llm_api_success` | ✓ | mock_core_dependencies, mock_openai_client, mock_redis_client |
| `test_call_llm_api_uses_cache` | ✓ | mock_core_dependencies, mock_openai_client, mock_redis_client |
| `test_get_ai_suggestions` |  | mock_core_dependencies, mock_openai_client |
| `test_get_ai_patch` |  | mock_core_dependencies, mock_openai_client |
| `test_redis_failure_alerting` |  | mock_core_dependencies |

**Constants:** `current_dir`, `parent_dir`, `import_fixer_dir`, `mock_redis_module`, `mock_redis_async`, `mock_openai`, `mock_tiktoken`, `mock_encoder`

---

## tests/test_self_healing_import_fixer_fixer_ast.py
**Lines:** 465

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `reset_globals` |  |  |
| `mock_core_dependencies` |  |  |
| `mock_redis_client` | ✓ |  |
| `test_project_setup` |  | tmp_path |
| `test_import_resolver_init` |  | test_project_setup, mock_core_dependencies |
| `test_import_resolver_converts_relative_imports` |  | test_project_setup, mock_core_dependencies |
| `test_import_resolver_validates_paths` |  | test_project_setup, mock_core_dependencies |
| `test_cycle_healer_init_validates_file` |  | test_project_setup, mock_core_dependencies |
| `test_cycle_healer_init_validates_whitelist` |  | test_project_setup, mock_core_dependencies |
| `test_cycle_healer_handles_syntax_error` |  | test_project_setup, mock_core_dependencies |
| `test_cycle_healer_finds_problematic_import` | ✓ | test_project_setup, mock_core_dependencies |
| `test_cycle_healer_moves_import_to_function` | ✓ | test_project_setup, mock_core_dependencies |
| `test_dynamic_import_healer_finds_dynamic_imports` |  | test_project_setup, mock_core_dependencies |
| `test_dynamic_import_healer_validates_paths` |  | test_project_setup, mock_core_dependencies |
| `test_get_ai_refactoring_suggestion` |  | mock_core_dependencies |
| `test_get_ai_refactoring_suggestion_handles_error` |  | mock_core_dependencies |
| `test_run_async_in_sync_no_loop` |  |  |
| `test_run_async_in_sync_with_loop` | ✓ |  |
| `test_cycle_healer_uses_cache` | ✓ | test_project_setup, mock_core_dependencies, mock_redis_client |

**Constants:** `current_dir`, `parent_dir`, `import_fixer_dir`, `mock_redis_module`, `mock_redis_async`

---

## tests/test_self_healing_import_fixer_fixer_dep.py
**Lines:** 325

### `_AuditLogger`

| Method | Async | Args |
|--------|-------|------|
| `log_event` |  | self |

### `_SecretsMgr`

| Method | Async | Args |
|--------|-------|------|
| `get_secret` |  | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_noop_alert` |  | msg, level |
| `_scrub` |  | x |
| `norm_name` |  | name |
| `mock_stdlib_unavailable` |  |  |
| `setup_teardown_env_vars` |  | tmp_path |
| `test_heal_dependencies_dry_run_no_changes` | ✓ | setup_teardown_env_vars |
| `test_heal_dependencies_actual_run_with_changes` | ✓ | setup_teardown_env_vars |
| `test_get_py_files_unwhitelisted_path_raises_error` |  | tmp_path |
| `test_heal_dependencies_no_read_access_is_graceful` |  | setup_teardown_env_vars |
| `test_heal_dependencies_no_write_access_raises_error` | ✓ | setup_teardown_env_vars |
| `test_heal_dependencies_stdlib_unavailable_in_prod_is_graceful` | ✓ | setup_teardown_env_vars, monkeypatch |
| `test_get_all_imports_async_parallel_parsing_performance` | ✓ | setup_teardown_env_vars |

**Constants:** `core_utils`, `core_audit`, `core_secrets`

---

## tests/test_self_healing_import_fixer_fixer_plugins.py
**Lines:** 277

### `_AuditLogger`

| Method | Async | Args |
|--------|-------|------|
| `log_event` |  | self |

### `_SecretsMgr`

| Method | Async | Args |
|--------|-------|------|
| `get_secret` |  | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_alert_operator` |  | msg, level |
| `_scrub_secrets` |  | x |
| `make_plugin_file` |  | dirpath, module_name, body |
| `sha256_hex` |  | key, data |
| `clean_env` |  | monkeypatch |
| `plugin_dir` |  | tmp_path, monkeypatch |
| `hmac_key` |  | monkeypatch |
| `test_plugin_manager_init_success` |  | plugin_dir |
| `test_plugin_manager_init_no_whitelisted_dirs_in_prod_raises_error` |  | monkeypatch |
| `test_load_plugin_lazy_loading_success` | ✓ | plugin_dir, hmac_key, monkeypatch |
| `test_load_plugin_with_signature_mismatch_raises_error` | ✓ | plugin_dir, hmac_key |
| `test_load_plugin_from_unwhitelisted_dir_raises_noncritical` | ✓ | tmp_path, plugin_dir, hmac_key |
| `test_dynamic_registration_in_prod_forbidden` |  | plugin_dir |
| `test_get_plugin_signature_key_missing_in_prod_raises_error` |  | monkeypatch |
| `test_run_hook_with_exception_raises_and_alerts` |  | monkeypatch |

**Constants:** `core_utils`, `core_audit`, `core_secrets`

---

## tests/test_self_healing_import_fixer_fixer_validate.py
**Lines:** 384

### `_AuditLogger`

| Method | Async | Args |
|--------|-------|------|
| `log_event` |  | self |

### `_SecretsMgr`

| Method | Async | Args |
|--------|-------|------|
| `get_secret` |  | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_alert_operator` |  | msg, level |
| `_scrub_secrets` |  | x |
| `quiet_logs` |  | monkeypatch |
| `project` |  | tmp_path |
| `test_validator_init_auto_whitelists_project_root_in_prod_when_list_empty` |  | monkeypatch, tmp_path |
| `test_compile_file_unwhitelisted_path_raises_error` |  | project |
| `test_run_linting_with_ruff_success` | ✓ | project, monkeypatch |
| `test_run_linting_with_ruff_failure` | ✓ | project, monkeypatch |
| `test_run_linting_missing_tools_is_graceful_even_in_prod` | ✓ | project, monkeypatch |
| `test_validate_and_commit_file_no_write_access_raises_error` | ✓ | project, monkeypatch |
| `test_validate_and_commit_file_pipeline_success` | ✓ | project, monkeypatch, tmp_path |
| `test_validate_and_commit_file_pipeline_failure_rolls_back` | ✓ | project, monkeypatch |
| `test_validate_and_commit_batch_unwhitelisted_path_raises_error` | ✓ | project |
| `test_interactive_prompt_in_prod_commits_when_allowed` | ✓ | monkeypatch, project |

**Constants:** `core_utils`, `core_audit`, `core_secrets`

---

## tests/test_self_healing_import_fixer_import_fixer_engine.py
**Lines:** 388

### `FakeRedis`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `setex` |  | self, key, ttl, value |
| `get` |  | self, key |
| `incr` |  | self, key |

### `FakeLLMClient`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `aclose` | ✓ | self |

### `FakeAIManager`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `generate_async` | ✓ | self |
| `generate_sync` |  | self |
| `aclose` | ✓ | self |

### `DummyProc`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, returncode, stdout, stderr |
| `communicate` | ✓ | self |

### `PluginProbe`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `__call__` |  | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `install_core_stubs` |  |  |
| `_import_by_candidates` |  | name_candidates, file_candidates |
| `load_import_fixer_modules` |  | test_dir |
| `make_tiny_project` |  | base |
| `test_import_fixer_stack_end_to_end` |  | tmp_path, monkeypatch |

---

## tests/test_self_healing_import_fixer_import_fixer_integration.py
**Lines:** 371

### `FakeRedis`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `setex` |  | self, key, ttl, value |
| `setex` | ✓ | self, key, ttl, value |
| `get` |  | self, key |
| `get` | ✓ | self, key |
| `incr` |  | self, key |
| `incr` | ✓ | self, key |
| `ping` | ✓ | self |

### `FakeLLMClient`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `aclose` | ✓ | self |

### `FakeAIManager`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `generate_async` | ✓ | self |
| `generate_sync` |  | self |
| `aclose` | ✓ | self |

### `DummyProc`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, returncode, stdout, stderr |
| `communicate` | ✓ | self |

### `PluginProbe`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `__call__` |  | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_install_core_stubs` |  |  |
| `_import_by_candidates` |  | name_candidates, file_candidates |
| `load_import_fixer_modules` |  | test_dir |
| `make_tiny_project` |  | base |
| `_patch_infra` |  | monkeypatch, modules |
| `test_import_fixer_stack_end_to_end` | ✓ | tmp_path, monkeypatch |

---

## tests/test_sfe_basic.py
**Lines:** 35

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `test_import_sfe` |  |  |
| `test_sfe_directory_exists` |  |  |
| `test_async_functionality` | ✓ |  |
| `async_helper` | ✓ |  |

---

## tests/test_simulation_agentic.py
**Lines:** 232

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `temp_file` |  | tmp_path |
| `mock_config` |  |  |
| `mock_audit_log_path` |  | tmp_path |
| `cleanup_tasks` | ✓ |  |
| `mock_httpx` |  | monkeypatch |
| `test_check_and_import_success` |  | monkeypatch |
| `test_check_and_import_critical_failure` |  | monkeypatch |
| `test_secrets_manager_get_secret_from_env` |  | monkeypatch |
| `test_secrets_manager_get_required_secret_missing` |  | monkeypatch |
| `test_audit_logger_init_file_backend` | ✓ | mock_audit_log_path, monkeypatch |
| `test_audit_logger_log_event_file` | ✓ | mock_audit_log_path, monkeypatch |
| `test_object_storage_save_load_success` | ✓ | monkeypatch |
| `test_mesh_notifier_notify_slack` | ✓ | monkeypatch |
| `test_event_bus_publish_memory` | ✓ |  |
| `test_policy_manager_has_permission_no_opa` |  | mock_httpx |
| `test_rbac_enforce_allowed` | ✓ | mock_httpx |
| `test_swarm_config_validation` |  |  |
| `test_ga_optimizer_evolve` | ✓ | monkeypatch |
| `test_run_simulation_swarm_success` | ✓ | mock_config |
| `test_operator_api_health_status` | ✓ | monkeypatch |
| `test_main_async_health` | ✓ | monkeypatch, caplog |

**Constants:** `pytestmark`

---

## tests/test_simulation_aws_batch_runner_plugin.py
**Lines:** 325

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_aws_clients` |  |  |
| `mock_env_vars` |  |  |
| `mock_job_config_valid` |  |  |
| `test_job_config_validates_successfully` |  | mock_job_config_valid |
| `test_job_config_invalid_arn_format` |  |  |
| `test_job_config_path_traversal_prevention` |  |  |
| `test_job_config_missing_required_fields` |  |  |
| `test_plugin_health_success` | ✓ | mock_aws_clients |
| `test_plugin_health_no_credentials_error` | ✓ | mock_aws_clients |
| `test_run_batch_job_full_workflow_success` | ✓ | mock_aws_clients, mock_job_config_valid |
| `test_run_batch_job_failure_workflow` | ✓ | mock_aws_clients, mock_job_config_valid |
| `test_run_batch_job_s3_download_failure` | ✓ | mock_aws_clients, mock_job_config_valid |
| `test_run_batch_job_invalid_path_traversal` | ✓ | mock_aws_clients, mock_job_config_valid |
| `test_run_batch_job_with_vault_credentials_failure` | ✓ | mock_aws_clients, mock_job_config_valid |

**Constants:** `parent_dir`

---

## tests/test_simulation_cloud_logging_integrations.py
**Lines:** 595

### `MockAWSClientError` (Exception)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, response |

### `MockCloudWatchLogger`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config |
| `__aenter__` | ✓ | self |
| `__aexit__` | ✓ | self, exc_type, exc_val, exc_tb |
| `health_check` | ✓ | self |
| `log_event` |  | self, event |
| `flush` | ✓ | self |
| `query_logs` | ✓ | self, query, time_range, limit |
| `close` | ✓ | self |

### `MockGCPLogger`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config |
| `__aenter__` | ✓ | self |
| `__aexit__` | ✓ | self, exc_type, exc_val, exc_tb |
| `health_check` | ✓ | self |
| `log_event` |  | self, event |
| `flush` | ✓ | self |
| `close` | ✓ | self |

### `MockAzureLogger`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config |
| `__aenter__` | ✓ | self |
| `__aexit__` | ✓ | self, exc_type, exc_val, exc_tb |
| `health_check` | ✓ | self |
| `log_event` |  | self, event |
| `flush` | ✓ | self |
| `close` | ✓ | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `aws_mocks` |  |  |
| `gcp_mocks` |  |  |
| `azure_mocks` |  |  |
| `test_cw_logger_health_check_success` | ✓ | aws_mocks |
| `test_cw_logger_auth_error` | ✓ | aws_mocks |
| `test_cw_logger_other_error` | ✓ | aws_mocks |
| `test_cw_logger_flushes_batch` | ✓ | aws_mocks |
| `test_cw_logger_flush_rollback` | ✓ | aws_mocks |
| `test_cw_logger_query_logs` | ✓ | aws_mocks |
| `test_gcp_logger_health_check_success` | ✓ | gcp_mocks |
| `test_gcp_logger_health_check_auth_error` | ✓ | gcp_mocks |
| `test_gcp_logger_flushes_batch` | ✓ | gcp_mocks |
| `azure_config` |  |  |
| `test_azure_logger_health_check_success` | ✓ | azure_config, azure_mocks |
| `test_azure_logger_health_check_auth_error` | ✓ | azure_config, azure_mocks |
| `test_azure_logger_flushes_batch` | ✓ | azure_config, azure_mocks |
| `test_azure_logger_auto_flushes_on_exit` | ✓ | azure_config, azure_mocks |
| `test_get_cloud_logger_factory_with_valid_config` |  | azure_config |
| `test_get_cloud_logger_factory_with_invalid_type` |  |  |

**Constants:** `PROJECT_ROOT`

---

## tests/test_simulation_core.py
**Lines:** 330

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `temp_dir` |  | tmp_path |
| `mock_config_yaml` |  | temp_dir |
| `mock_rbac_yaml` |  | temp_dir |
| `mock_args` |  |  |
| `test_load_config_success` |  | mock_config_yaml, monkeypatch |
| `test_load_config_file_not_found` |  | monkeypatch |
| `test_load_config_no_pydantic` |  | mock_config_yaml, monkeypatch |
| `test_load_rbac_policy_success` |  | mock_rbac_yaml, monkeypatch |
| `test_load_rbac_policy_file_not_found` |  | monkeypatch |
| `test_get_user_roles` |  | monkeypatch |
| `test_get_role_permissions` |  | monkeypatch |
| `test_check_permission_granted` |  | monkeypatch |
| `test_check_permission_denied` |  | monkeypatch |
| `test_circuit_breaker_attempt_success` |  |  |
| `test_circuit_breaker_permanent_failure` |  | monkeypatch, caplog |
| `test_notification_manager_send_slack` |  | monkeypatch |
| `test_generate_correlation_id` |  |  |
| `test_correlated_decorator` | ✓ | caplog |
| `test_execute_remotely_success` |  |  |
| `test_execute_remotely_failure` |  | monkeypatch |
| `test_run_job_success` |  | monkeypatch |
| `test_run_job_disabled` |  |  |
| `test_watch_mode_success` |  | monkeypatch |
| `test_watch_mode_no_watchdog` |  | monkeypatch |
| `test_main_success` | ✓ | mock_args, monkeypatch |
| `test_validate_file_success` |  | temp_dir |
| `test_validate_file_not_found` |  | temp_dir |

**Constants:** `pytestmark`

---

## tests/test_simulation_cross_repo_refactor_plugin.py
**Lines:** 394

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_external_dependencies` |  |  |
| `temp_repo_dir` |  |  |
| `valid_refactor_plan` |  |  |
| `test_validate_refactor_plan_success` |  | valid_refactor_plan |
| `test_validate_refactor_plan_failures` |  | invalid_plan, expected_error |
| `test_mask_token_in_url` |  |  |
| `test_is_safe_path` |  | temp_repo_dir |
| `test_git_repo_manager_clone` | ✓ | mock_external_dependencies, temp_repo_dir |
| `test_git_repo_manager_push` | ✓ | mock_external_dependencies, temp_repo_dir |
| `test_perform_refactor_full_success_workflow` | ✓ | mock_external_dependencies, temp_repo_dir, valid_refactor_plan |
| `test_perform_refactor_dry_run` | ✓ | mock_external_dependencies, temp_repo_dir, valid_refactor_plan |
| `test_perform_refactor_clone_failure` | ✓ | mock_external_dependencies, temp_repo_dir, valid_refactor_plan |
| `test_perform_refactor_no_cleanup_on_failure` | ✓ | mock_external_dependencies, temp_repo_dir, valid_refactor_plan |
| `test_plugin_health_success` | ✓ |  |
| `test_plugin_health_gitpython_missing` | ✓ |  |

---

## tests/test_simulation_custom_llm_provider_plugin.py
**Lines:** 518

### `MockMetric`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `labels` |  | self |
| `observe` |  | self, value |
| `inc` |  | self, value |
| `dec` |  | self, value |
| `set` |  | self, value |

### `TestLLMConfiguration`

| Method | Async | Args |
|--------|-------|------|
| `test_valid_llm_config` |  | self, valid_config_dict |
| `test_invalid_temperature` |  | self, valid_config_dict, invalid_temp |
| `test_https_enforced_in_production` |  | self, valid_config_dict, monkeypatch |
| `test_known_hosts_enforcement_in_production` |  | self, valid_config_dict, monkeypatch |
| `test_invalid_int_params` |  | self, valid_config_dict, field, value, error_msg |

### `TestCustomLLMProvider`

| Method | Async | Args |
|--------|-------|------|
| `test_acall_success` | ✓ | self, llm_provider |
| `test_acall_rate_limit_and_retry` | ✓ | self, llm_provider |
| `test_acall_fallback_on_client_error` | ✓ | self, llm_provider |
| `test_astream_yields_chunks` | ✓ | self, llm_provider |
| `test_astream_handles_malformed_data` | ✓ | self, llm_provider |
| `test_caching_works_full_cycle` | ✓ | self, llm_provider |
| `test_plugin_health_reports_ok` | ✓ | self |
| `test_plugin_health_handles_errors` | ✓ | self |

### `TestPluginFunctions`

| Method | Async | Args |
|--------|-------|------|
| `test_generate_custom_llm_response_runs` | ✓ | self, monkeypatch |
| `test_vault_key_caching_reduces_requests` | ✓ | self, monkeypatch |
| `test_negative_cache_prevents_stampede` | ✓ | self, monkeypatch |
| `test_circuit_breaker_opens_on_failures` | ✓ | self, llm_provider |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_prometheus_metrics` |  | monkeypatch |
| `valid_config_dict` |  |  |
| `llm_provider` |  | valid_config_dict, monkeypatch |

---

## tests/test_simulation_dashboard.py
**Lines:** 272

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_streamlit` |  |  |
| `mock_plugin_and_result_dirs` |  |  |
| `mock_onboarding_backends` |  |  |
| `test_load_plugin_dashboard_panels_cached` |  | mock_plugin_and_result_dirs |
| `test_load_plugin_dashboard_panels_cached_with_dangerous_name` |  | mock_plugin_and_result_dirs |
| `test_is_version_compatible` |  |  |
| `test_display_onboarding_wizard_config_generation` |  | mock_streamlit, mock_plugin_and_result_dirs |
| `test_run_health_checks_gui_success` | ✓ | mock_onboarding_backends |
| `test_sanitize_plugin_name` |  |  |
| `test_load_all_simulation_results` |  | mock_plugin_and_result_dirs |
| `test_load_all_simulation_results_with_invalid_json` |  | mock_plugin_and_result_dirs |
| `test_translation_function` |  | mock_streamlit |

---

## tests/test_simulation_dlt_base.py
**Lines:** 290

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_external_deps` |  | mocker |
| `test_circuit_breaker_trip_and_recovery` | ✓ | mocker |
| `test_audit_manager_integrity_check_success` | ✓ | mocker |
| `test_audit_manager_integrity_check_failure` | ✓ | mocker |
| `test_secrets_manager_production_mode_enforcement` | ✓ | mocker |
| `test_scrub_secrets_utility` |  |  |
| `test_async_retry_decorator` | ✓ | mocker |

---

## tests/test_simulation_dlt_corda_clients.py
**Lines:** 525

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_off_chain` |  |  |
| `create_mock_response` |  | status, text, json_data, headers |
| `mock_aiohttp` |  | mocker |
| `mock_secrets_manager` |  | mocker |
| `mock_scrub_secrets` |  | mocker |
| `test_corda_init_success` | ✓ | mock_off_chain, mock_secrets_manager |
| `test_corda_init_failure_invalid_config` | ✓ | mock_off_chain, mock_secrets_manager |
| `test_corda_init_production_mode_validation` | ✓ | rpc_url, user, password, is_prod, should_fail, ...+2 |
| `test_health_check_success` | ✓ | mock_off_chain, mock_aiohttp |
| `test_health_check_failures` | ✓ | mock_off_chain, mock_aiohttp, status, error_type, message_part |
| `test_write_checkpoint_success` | ✓ | mock_off_chain, mock_aiohttp |
| `test_write_checkpoint_retry_on_transient_error` | ✓ | mock_off_chain, mock_aiohttp, mocker |
| `test_read_checkpoint_not_found_on_dlt` | ✓ | mock_off_chain, mock_aiohttp |
| `test_read_checkpoint_off_chain_blob_not_found` | ✓ | mock_off_chain, mock_aiohttp |
| `test_rollback_checkpoint_success` | ✓ | mock_off_chain, mock_aiohttp |
| `test_session_management_and_closing` | ✓ | mock_off_chain, mock_aiohttp, mocker |

---

## tests/test_simulation_dlt_e2e.py
**Lines:** 284

### `Checkpoint`

### `MockDLTClient`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, network_label, audit_path |
| `_audit` |  | self, event |
| `write_checkpoint` |  | self, name, hash, prevHash, metadata, ...+1 |
| `read_checkpoint` |  | self, name |
| `rollback_checkpoint` |  | self, name, targetHash |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `dlt_client` |  | tmp_path, request |
| `test_write_read_rollback_roundtrip` |  | dlt_client |
| `test_isolation_between_streams` |  | dlt_client |
| `test_prevhash_mismatch_rejected` |  | dlt_client |
| `test_genesis_prevhash_rules` |  | dlt_client |
| `test_rollback_unknown_hash_errors` |  | dlt_client |
| `test_audit_log_file_and_hmac` |  | monkeypatch, tmp_path |

---

## tests/test_simulation_dlt_evm_clients.py
**Lines:** 501

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_off_chain` |  |  |
| `mock_secrets_manager` |  | mocker |
| `mock_web3_provider` |  | mocker |
| `test_evm_init_success` | ✓ | mock_off_chain, mock_web3_provider, mocker |
| `test_evm_init_with_secrets_provider` | ✓ | mock_off_chain, mock_web3_provider, mocker |
| `test_evm_init_failure_missing_abi` | ✓ | mock_off_chain, mocker |
| `test_evm_init_failure_private_key_source_in_prod` | ✓ | mock_off_chain, mocker |
| `test_evm_init_secrets_backend_unavailable` | ✓ | mock_off_chain, mock_web3_provider, mocker |
| `test_health_check_success` | ✓ | mock_off_chain, mock_web3_provider, mocker |
| `test_health_check_failures` | ✓ | chain_id, connected, contract_code, balance, expected_exception, ...+4 |
| `test_write_checkpoint_success` | ✓ | mock_off_chain, mock_web3_provider, mocker |
| `test_read_checkpoint_success` | ✓ | mock_off_chain, mock_web3_provider, mocker |
| `test_client_close_method` | ✓ | mock_off_chain, mock_web3_provider, mocker |
| `test_safe_copy_dict_with_cycle` |  |  |
| `test_safe_copy_dict_redacts_sensitive` |  |  |

---

## tests/test_simulation_dlt_fabric_clients.py
**Lines:** 478

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_off_chain` |  |  |
| `mock_secrets_manager` |  | mocker |
| `cleanup` | ✓ |  |
| `mock_aiohttp_session` |  | mocker |
| `mock_fabric_sdk` |  | mocker |
| `test_fabric_rest_mode_init_success` | ✓ | mock_off_chain, mock_aiohttp_session, mocker |
| `test_fabric_sdk_mode_init_success` | ✓ | mock_off_chain, mock_fabric_sdk, mocker |
| `test_fabric_init_failure_invalid_mode` | ✓ | mock_off_chain, mocker |
| `test_fabric_init_failure_missing_rest_url` | ✓ | mock_off_chain, mocker |
| `test_fabric_init_failure_missing_sdk_fields` | ✓ | mock_off_chain, mock_fabric_sdk, mocker |
| `test_health_check_rest_mode_success` | ✓ | mock_off_chain, mock_aiohttp_session, mocker |
| `test_health_check_sdk_mode_success` | ✓ | mock_off_chain, mock_fabric_sdk, mocker |
| `test_write_checkpoint_rest_mode_success` | ✓ | mock_off_chain, mock_aiohttp_session, mocker |
| `test_read_checkpoint_rest_mode_success` | ✓ | mock_off_chain, mock_aiohttp_session, mocker |
| `test_client_close_rest_mode` | ✓ | mock_off_chain, mock_aiohttp_session, mocker |
| `test_rate_limiting` | ✓ | mock_off_chain, mock_aiohttp_session, mocker |

---

## tests/test_simulation_dlt_main_unit.py
**Lines:** 479

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `disable_info_logging` |  |  |
| `mock_dlt_client` |  |  |
| `mock_factory` |  | mocker, mock_dlt_client |
| `mock_config_file` |  | tmp_path |
| `mock_payload_file` |  | tmp_path |
| `test_cli_health_check_success` |  | mock_factory, mock_config_file |
| `test_cli_health_check_failure` |  | mock_factory, mock_config_file |
| `test_cli_write_checkpoint_success` |  | mock_factory, mock_config_file, mock_payload_file |
| `test_cli_read_checkpoint_success` |  | mock_factory, mock_config_file, tmp_path |
| `test_cli_read_checkpoint_with_output_file` |  | mock_factory, mock_config_file, tmp_path |
| `test_cli_rollback_checkpoint_success` |  | mock_factory, mock_config_file |
| `test_cli_invalid_config_file` |  | mocker, tmp_path |
| `test_cli_invalid_json_in_config` |  | mocker, tmp_path |
| `test_cli_dlt_client_configuration_error` |  | mocker, mock_config_file |
| `test_cli_dlt_client_error` |  | mocker, mock_config_file |
| `test_cli_write_checkpoint_invalid_metadata` |  | mocker, mock_config_file, mock_payload_file |
| `test_cli_verbose_flag` |  | mock_factory, mock_config_file |
| `test_cli_correlation_id_provided` |  | mock_factory, mock_config_file |

---

## tests/test_simulation_dlt_network_config_manager.py
**Lines:** 403

### `TestSecretScrubbing`

| Method | Async | Args |
|--------|-------|------|
| `test_scrub_azure_connection_string` |  | self |
| `test_scrub_generic_secrets` |  | self |
| `test_scrub_nested_secrets` |  | self |

### `TestDLTNetworkConfig`

| Method | Async | Args |
|--------|-------|------|
| `test_valid_simple_config` |  | self |
| `test_invalid_dlt_type` |  | self |
| `test_invalid_off_chain_type` |  | self |
| `test_evm_config_validation` |  | self |
| `test_evm_invalid_contract_address` |  | self |
| `test_missing_off_chain_config` |  | self |

### `TestDLTNetworkConfigManager`

| Method | Async | Args |
|--------|-------|------|
| `test_singleton_pattern` |  | self, clean_env |
| `test_load_from_individual_env_vars` |  | self, clean_env |
| `test_load_from_combined_env_var` |  | self, clean_env |
| `test_individual_overrides_combined` |  | self, clean_env |
| `test_name_normalization` |  | self, clean_env |
| `test_get_default_config` |  | self, clean_env |
| `test_refresh_configs_if_changed` | ✓ | self, clean_env |
| `test_no_refresh_when_unchanged` | ✓ | self, clean_env |
| `test_production_mode_validation` |  | self, clean_env |
| `test_aws_secrets_loading` |  | self, clean_env, mock_boto3 |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `clean_env` |  |  |
| `mock_boto3` |  |  |

---

## tests/test_simulation_dlt_offchain_clients.py
**Lines:** 465

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_secrets_manager` |  | mocker |
| `mock_aioboto3_client` |  | mocker |
| `mock_boto3_client` |  | mocker |
| `mock_gcs_client` |  | mocker |
| `mock_azure_blob_client` |  | mocker |
| `mock_ipfs_client` |  | mocker |
| `mock_temp_file` |  | mocker, tmp_path |
| `mock_s3_config` |  |  |
| `mock_gcs_config` |  |  |
| `mock_azure_config` |  |  |
| `suppress_logs` |  |  |
| `test_s3_health_check_success` | ✓ | mock_s3_config, mock_aioboto3_client, mock_boto3_client |
| `test_s3_health_check_failure` | ✓ | mock_s3_config, mock_aioboto3_client, mock_boto3_client |
| `test_s3_save_blob_success` | ✓ | mock_s3_config, mock_aioboto3_client, mock_boto3_client |
| `test_s3_get_blob_success` | ✓ | mock_s3_config, mock_aioboto3_client, mock_boto3_client |
| `test_s3_save_blob_empty_payload` | ✓ | mock_s3_config, mock_aioboto3_client, mock_boto3_client |
| `test_gcs_health_check_success` | ✓ | mock_gcs_config, mock_gcs_client, mock_boto3_client, mock_temp_file |
| `test_gcs_save_blob_success` | ✓ | mock_gcs_config, mock_gcs_client, mock_boto3_client, mock_temp_file |
| `test_gcs_get_blob_success` | ✓ | mock_gcs_config, mock_gcs_client, mock_boto3_client, mock_temp_file |
| `test_azure_blob_health_check_success` | ✓ | mock_azure_config, mock_azure_blob_client, mock_boto3_client |
| `test_azure_blob_save_blob_success` | ✓ | mock_azure_config, mock_azure_blob_client, mock_boto3_client |
| `test_azure_blob_get_blob_success` | ✓ | mock_azure_config, mock_azure_blob_client, mock_boto3_client |
| `test_ipfs_health_check_success` | ✓ | mock_ipfs_client |
| `test_ipfs_save_blob_success` | ✓ | mock_ipfs_client |
| `test_ipfs_get_blob_success` | ✓ | mock_ipfs_client |
| `test_in_memory_client_forbidden_in_prod` |  | mocker |
| `test_in_memory_save_and_get_blob_success` | ✓ | mocker |

---

## tests/test_simulation_dlt_quorum_clients.py
**Lines:** 417

### `TestQuorumConfigValidation`

| Method | Async | Args |
|--------|-------|------|
| `test_valid_config` |  | self |
| `test_invalid_url_scheme` |  | self |
| `test_missing_required_fields` |  | self |
| `test_invalid_contract_address` |  | self |
| `test_incomplete_privacy_config` |  | self |
| `test_valid_privacy_config` |  | self |
| `test_missing_abi_source` |  | self |
| `test_missing_private_key_source` |  | self |

### `TestQuorumClient`

| Method | Async | Args |
|--------|-------|------|
| `mock_off_chain` |  | self |
| `mock_secrets_manager` |  | self, mocker |
| `mock_web3_provider` |  | self, mocker |
| `mock_filesystem` |  | self, mocker |
| `test_quorum_init_success` | ✓ | self, mock_off_chain, mock_web3_provider, mocker |

---

## tests/test_simulation_dlt_simple_clients.py
**Lines:** 648

### `TestConfiguration`

| Method | Async | Args |
|--------|-------|------|
| `test_valid_config` |  | self |
| `test_invalid_ttl` |  | self |
| `test_invalid_config_raises_error` |  | self, mock_off_chain_client |
| `test_production_requires_chain_path` |  | self |

### `TestCoreOperations`

| Method | Async | Args |
|--------|-------|------|
| `test_write_checkpoint` | ✓ | self, client, sample_data |
| `test_read_checkpoint` | ✓ | self, client, sample_data |
| `test_read_specific_version` | ✓ | self, client |
| `test_rollback_checkpoint` | ✓ | self, client |
| `test_nonexistent_checkpoint` | ✓ | self, client |
| `test_get_version_tx` | ✓ | self, client, sample_data |

### `TestValidation`

| Method | Async | Args |
|--------|-------|------|
| `test_empty_checkpoint_name` | ✓ | self, client |
| `test_empty_hash` | ✓ | self, client |
| `test_empty_payload` | ✓ | self, client |

### `TestPersistence`

| Method | Async | Args |
|--------|-------|------|
| `test_dump_and_load_chain` | ✓ | self, tmp_path, mock_off_chain_client |
| `test_checksum_calculation` | ✓ | self, client |

### `TestHealthCheck`

| Method | Async | Args |
|--------|-------|------|
| `test_health_check_success` | ✓ | self, client |
| `test_health_check_off_chain_failure` | ✓ | self, client |

### `TestConcurrency`

| Method | Async | Args |
|--------|-------|------|
| `test_concurrent_writes` | ✓ | self, client |
| `test_concurrent_reads` | ✓ | self, client, sample_data |

### `TestErrorHandling`

| Method | Async | Args |
|--------|-------|------|
| `test_off_chain_failure_propagation` | ✓ | self, client |
| `test_credential_rotation` | ✓ | self, client |
| `test_credential_rotation_not_supported` | ✓ | self, client |

### `TestPluginSystem`

| Method | Async | Args |
|--------|-------|------|
| `test_plugin_manifest` |  | self |
| `test_factory_function` |  | self, mock_off_chain_client, config |
| `test_factory_without_off_chain_client` |  | self |

### `TestPerformance`

| Method | Async | Args |
|--------|-------|------|
| `test_write_performance` | ✓ | self, client |
| `test_read_performance` | ✓ | self, client |

### `TestEdgeCases`

| Method | Async | Args |
|--------|-------|------|
| `test_unicode_metadata` | ✓ | self, client |
| `test_special_characters_in_name` | ✓ | self, client |
| `test_none_metadata` | ✓ | self, client |

### `TestCleanup`

| Method | Async | Args |
|--------|-------|------|
| `test_client_close` | ✓ | self, client |
| `test_close_with_persistence` | ✓ | self, tmp_path, mock_off_chain_client |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_off_chain_client` |  |  |
| `config` |  |  |
| `client` | ✓ | config, mock_off_chain_client |
| `sample_data` |  |  |

---

## tests/test_simulation_e2e_plugins_submodule.py
**Lines:** 461

### `MockPlugin`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, name, plugin_type |
| `execute` | ✓ | self |

### `MockPluginManager`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, plugins_dir |
| `_create_mock_plugins` |  | self |
| `load_all` | ✓ | self, check_health |
| `list_plugins` |  | self |
| `close_all_plugins` | ✓ | self |

### `MockDLTClient`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config |
| `write_checkpoint` | ✓ | self, checkpoint_name, hash, prev_hash, metadata, ...+1 |
| `read_checkpoint` | ✓ | self, name |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `event_loop` |  |  |
| `setup_test_environment` |  |  |
| `mock_external_services` |  |  |
| `mock_jest_runner` |  | args |
| `mock_patch_generator` | ✓ | args |
| `mock_runtime_tracer` | ✓ | args |
| `mock_evolution_cycle` | ✓ | args |
| `test_plugins_submodule_end_to_end` | ✓ | setup_test_environment, mock_external_services |
| `test_plugin_manager_lifecycle` | ✓ | mock_llm_provider_dependencies |
| `test_dlt_client_operations` | ✓ | mock_llm_provider_dependencies |

**Constants:** `mock_custom_llm`, `mock_minio`, `TEST_DIR`, `SIMULATION_DIR`, `pytestmark`

---

## tests/test_simulation_e2e_simulation_module.py
**Lines:** 332

### `MockRLTunerConfig`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `event_loop` |  |  |
| `setup_test_environment` |  | tmpdir_factory |
| `create_mock_core_functions` |  |  |
| `test_simulation_module_end_to_end` | ✓ | setup_test_environment |

**Constants:** `TEST_DIR`, `SIMULATION_DIR`, `mock_dashboard`, `mock_transformers`, `mock_dwave`, `mock_dwave_system`

---

## tests/test_simulation_example_plugin.py
**Lines:** 376

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `reset_metrics` |  |  |
| `mock_prometheus_client` |  |  |
| `mock_plugin_config` |  |  |
| `mock_metrics` |  |  |
| `test_plugin_manifest_loading` |  | mock_plugin_config |
| `test_check_compatibility_success` |  |  |
| `test_check_compatibility_failure_min_version` |  |  |
| `test_run_custom_chaos_experiment_success` |  | mock_metrics |
| `test_run_custom_chaos_experiment_failure_injected` |  | mock_metrics |
| `test_run_custom_chaos_experiment_validation_error` |  | mock_metrics |
| `test_run_custom_chaos_experiment_simulated_error` |  | mock_metrics |
| `test_perform_custom_security_audit_success_no_findings` |  | mock_plugin_config, mock_metrics |
| `test_perform_custom_security_audit_with_findings` |  | mock_plugin_config, mock_metrics |
| `test_perform_custom_security_audit_path_traversal_attack` |  | mock_plugin_config, mock_metrics |
| `test_perform_custom_security_audit_file_not_found` |  | mock_plugin_config, mock_metrics |
| `test_scrub_secrets_utility_function` |  |  |
| `test_plugin_health_check_success` |  |  |
| `test_plugin_health_check_degraded` |  |  |

---

## tests/test_simulation_explain.py
**Lines:** 310

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `temp_db_path` |  | tmp_path |
| `mock_config` |  |  |
| `mock_settings` |  |  |
| `mock_history_manager` |  | temp_db_path |
| `reasoner_with_shutdown` | ✓ | mock_config, mock_settings |
| `test_explanation_result_dataclass` |  |  |
| `test_sanitize_input` |  | input_text, expected |
| `test_sanitize_context` |  | input_context, expected |
| `test_rule_based_fallback` |  |  |
| `test_history_manager_init_db` | ✓ | mock_history_manager |
| `test_history_manager_add_entry` | ✓ | mock_history_manager |
| `test_history_manager_get_entries` | ✓ | mock_history_manager |
| `test_history_manager_clear` | ✓ | mock_history_manager |
| `test_explainable_reasoner_explain` | ✓ | reasoner_with_shutdown |
| `test_explainable_reasoner_reason` | ✓ | reasoner_with_shutdown |
| `test_explainable_reasoner_plugin_explain_result` | ✓ | reasoner_with_shutdown, mock_settings, mock_config |
| `test_explainable_reasoner_plugin_execute` | ✓ | reasoner_with_shutdown, mock_settings, mock_config |

**Constants:** `pytestmark`

---

## tests/test_simulation_gcp_cloud_run_runner_plugin.py
**Lines:** 1023

### `MockOperation`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, result_val, exception |
| `result` |  | self, timeout |

### `TestJobConfigValidation`

| Method | Async | Args |
|--------|-------|------|
| `test_valid_config` |  | self, valid_job_config |
| `test_invalid_image_url` |  | self |
| `test_invalid_project_id` |  | self |
| `test_invalid_location` |  | self |
| `test_invalid_bucket_name` |  | self |
| `test_optional_fields_defaults` |  | self |

### `TestHelperFunctions`

| Method | Async | Args |
|--------|-------|------|
| `test_bucket_valid` |  | self |
| `test_tar_directory_to_temp` |  | self, temp_project_dir |

### `TestPluginHealth`

| Method | Async | Args |
|--------|-------|------|
| `test_health_check_success` | ✓ | self, mock_environment, mock_credentials, mock_gcs_client, mock_jobs_client |
| `test_health_check_no_credentials` | ✓ | self, mock_environment |
| `test_health_check_gcs_failure` | ✓ | self, mock_environment, mock_credentials, mock_gcs_client |
| `test_health_check_no_gcp_libraries` | ✓ | self |

### `TestRunCloudRunJob`

| Method | Async | Args |
|--------|-------|------|
| `test_successful_job_execution` | ✓ | self, valid_job_config, temp_project_dir, mock_environment, mock_credentials, ...+3 |
| `test_failed_job_execution` | ✓ | self, valid_job_config, temp_project_dir, mock_environment, mock_credentials, ...+3 |
| `test_quota_exceeded_retry` | ✓ | self, valid_job_config, temp_project_dir, mock_environment, mock_credentials, ...+2 |
| `test_gcs_download_failure` | ✓ | self, valid_job_config, temp_project_dir, mock_environment, mock_credentials, ...+2 |
| `test_run_job_no_gcp_libraries` | ✓ | self, valid_job_config, temp_project_dir |

### `TestSecurity`

| Method | Async | Args |
|--------|-------|------|
| `test_vault_credentials_loading` | ✓ | self, mock_environment |
| `test_vault_https_enforcement` | ✓ | self |

### `TestPerformance`

| Method | Async | Args |
|--------|-------|------|
| `test_archive_excludes_heavy_dirs` |  | self, temp_project_dir |

### `TestEndToEnd`

| Method | Async | Args |
|--------|-------|------|
| `test_real_cloud_run_execution` | ✓ | self, valid_job_config, temp_project_dir |

### `TestEdgeCases`

| Method | Async | Args |
|--------|-------|------|
| `test_monitoring_timeout` | ✓ | self, valid_job_config, temp_project_dir, mock_environment, mock_credentials, ...+2 |
| `test_invalid_job_config` | ✓ | self, temp_project_dir |
| `test_job_conflict_retry` | ✓ | self, valid_job_config, temp_project_dir, mock_environment, mock_credentials, ...+2 |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_credentials` |  |  |
| `mock_gcs_client` |  |  |
| `mock_jobs_client` |  |  |
| `mock_logging_client` |  |  |
| `mock_environment` |  |  |
| `valid_job_config` |  |  |
| `temp_project_dir` |  |  |

**Constants:** `SIMULATION_DIR`

---

## tests/test_simulation_gremlin_chaos_plugin.py
**Lines:** 298

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `clear_gremlin_metrics` |  |  |
| `mock_gremlin_and_env` |  |  |
| `test_plugin_health_success` | ✓ | mock_gremlin_and_env |
| `test_plugin_health_missing_credentials` | ✓ |  |
| `test_plugin_health_api_error` | ✓ | mock_gremlin_and_env |
| `test_run_chaos_experiment_cpu_hog_success` | ✓ | mock_gremlin_and_env |
| `test_run_chaos_experiment_network_latency_kubernetes_success` | ✓ | mock_gremlin_and_env |
| `test_run_chaos_experiment_unsupported_type` | ✓ | mock_gremlin_and_env |
| `test_run_chaos_experiment_api_error_on_initiation` | ✓ | mock_gremlin_and_env |
| `test_run_chaos_experiment_monitoring_timeout` | ✓ | mock_gremlin_and_env |

**Constants:** `PLUGIN_DIR`

---

## tests/test_simulation_java_test_runner_plugin.py
**Lines:** 627

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_node_in_path` |  |  |
| `mock_temp_jest_project` |  |  |
| `test_plugin_health_success` | ✓ | mock_node_in_path |
| `test_plugin_health_npx_not_found` | ✓ |  |
| `test_detect_package_manager` | ✓ |  |
| `test_run_jest_tests_success_full_workflow` | ✓ | mock_temp_jest_project |
| `test_run_jest_tests_test_failure` | ✓ | mock_temp_jest_project |
| `test_run_jest_tests_file_not_found` | ✓ |  |
| `test_run_jest_tests_timeout` | ✓ |  |
| `test_get_package_version` | ✓ |  |
| `test_which_command` | ✓ |  |
| `test_run_jest_tests_no_npx` | ✓ |  |
| `test_run_jest_tests_with_extra_args` | ✓ | mock_temp_jest_project |

---

## tests/test_simulation_jest_runner_plugin.py
**Lines:** 638

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_node_in_path` |  |  |
| `mock_temp_jest_project` |  |  |
| `test_plugin_health_success` | ✓ | mock_node_in_path |
| `test_plugin_health_npx_not_found` | ✓ |  |
| `test_detect_package_manager` | ✓ |  |
| `test_run_jest_tests_success_full_workflow` | ✓ | mock_temp_jest_project |
| `test_run_jest_tests_test_failure` | ✓ | mock_temp_jest_project |
| `test_run_jest_tests_file_not_found` | ✓ |  |
| `test_run_jest_tests_timeout` | ✓ |  |
| `test_get_package_version` | ✓ |  |
| `test_which_command` | ✓ |  |
| `test_run_jest_tests_no_npx` | ✓ |  |
| `test_run_jest_tests_with_extra_args` | ✓ | mock_temp_jest_project |

---

## tests/test_simulation_main_sim_runner.py
**Lines:** 513

### `TestPluginDiscovery`

| Method | Async | Args |
|--------|-------|------|
| `test_register_entrypoint` |  | self |
| `test_discover_plugins_empty_dir` |  | self |

### `TestValidation`

| Method | Async | Args |
|--------|-------|------|
| `test_validate_local_mode` |  | self |
| `test_validate_remote_mode_missing_env` |  | self |

### `TestUtilityFunctions`

| Method | Async | Args |
|--------|-------|------|
| `test_parse_plugin_args_valid` |  | self |
| `test_parse_plugin_args_empty` |  | self |
| `test_parse_plugin_args_invalid` |  | self |

### `TestResultAggregation`

| Method | Async | Args |
|--------|-------|------|
| `test_aggregate_simple` |  | self |
| `test_aggregate_with_multiple_plugins` |  | self |

### `TestSecurity`

| Method | Async | Args |
|--------|-------|------|
| `test_rbac_permission_check` |  | self |
| `test_rbac_caching` |  | self |
| `test_verify_signature` |  | self |

### `TestNotifications`

| Method | Async | Args |
|--------|-------|------|
| `test_send_notification_dry_run` |  | self |
| `test_send_notification_normal` |  | self, mock_imports |

### `TestPluginExecution`

| Method | Async | Args |
|--------|-------|------|
| `test_run_plugin_sandbox` |  | self |

### `TestRemoteExecution`

| Method | Async | Args |
|--------|-------|------|
| `test_execute_remotely_basic` |  | self, mock_imports |

### `TestEnforcement`

| Method | Async | Args |
|--------|-------|------|
| `test_enforce_kernel_sandboxing_basic` |  | self |
| `test_enforce_kernel_sandboxing_with_apparmor` |  | self |

### `TestIntegration`

| Method | Async | Args |
|--------|-------|------|
| `test_main_help` |  | self |
| `test_main_validate` |  | self |

### `TestPerformance`

| Method | Async | Args |
|--------|-------|------|
| `test_large_plugin_registry` |  | self |
| `test_large_result_aggregation` |  | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_create_package_mock` |  | name |
| `clean_registries` |  |  |
| `mock_imports` |  |  |

**Constants:** `test_env_vars`, `_ORIGINAL_MODULES`, `_MODULES_TO_MOCK`, `mock_otel`, `mock_tracer`, `mock_span`

---

## tests/test_simulation_model_deployment_plugin.py
**Lines:** 288

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_external_dependencies` |  |  |
| `mock_global_config_path` |  | mock_external_dependencies |
| `test_model_deployment_strategy_abstract_methods` |  |  |
| `test_model_deployment_strategy_concrete` |  |  |
| `test_validate_config_and_logic` |  |  |
| `test_deploy_model_local_api_success` | ✓ | mock_external_dependencies |
| `test_undeploy_model_cloud_service_success` | ✓ | mock_external_dependencies |
| `test_deploy_model_missing_config_local_api` | ✓ | mock_external_dependencies |
| `test_deploy_model_unknown_strategy_type` | ✓ | mock_external_dependencies |

---

## tests/test_simulation_module.py
**Lines:** 667

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `wait_for_async_conditions` | ✓ | condition_fn, timeout, check_interval |
| `mock_settings` |  |  |
| `mock_metrics` |  |  |
| `mock_db` |  |  |
| `mock_message_bus` |  |  |
| `mock_reasoner` |  |  |
| `mock_quantum_api` |  |  |
| `mock_sandbox` |  |  |
| `mock_agent_runners` |  |  |
| `simulation_module_instance` | ✓ | mock_db, mock_message_bus, mock_reasoner, mock_quantum_api |
| `test_initialization` | ✓ | mock_db, mock_message_bus, mock_reasoner, mock_quantum_api |
| `test_double_initialization` | ✓ | simulation_module_instance, mock_reasoner |
| `test_health_check_success` | ✓ | simulation_module_instance, mock_db, mock_message_bus, mock_reasoner, mock_quantum_api |
| `test_health_check_failure_reasoner` | ✓ | mock_db, mock_message_bus, mock_reasoner, mock_quantum_api |
| `test_health_check_fail_on_error` | ✓ | mock_db, mock_message_bus, mock_reasoner, mock_quantum_api |
| `test_shutdown` | ✓ | mock_db, mock_message_bus, mock_reasoner, mock_quantum_api |
| `test_execute_simulation_agent_type` | ✓ | simulation_module_instance, mock_agent_runners, mock_db, mock_metrics |
| `test_execute_simulation_swarm_type` | ✓ | simulation_module_instance, mock_agent_runners, mock_metrics |
| `test_execute_simulation_parallel_type` | ✓ | simulation_module_instance, mock_agent_runners, mock_metrics |
| `test_execute_simulation_unknown_type` | ✓ | simulation_module_instance, mock_metrics |
| `test_execute_simulation_failure` | ✓ | simulation_module_instance, mock_agent_runners, mock_db, mock_metrics |
| `test_perform_quantum_op_mutation` | ✓ | simulation_module_instance, mock_quantum_api, mock_db, mock_metrics |
| `test_perform_quantum_op_forecast` | ✓ | simulation_module_instance, mock_quantum_api, mock_metrics |
| `test_perform_quantum_op_unknown_type` | ✓ | simulation_module_instance, mock_metrics |
| `test_perform_quantum_op_api_error` | ✓ | simulation_module_instance, mock_quantum_api, mock_metrics |
| `test_explain_result_success` | ✓ | simulation_module_instance, mock_reasoner, mock_db |
| `test_explain_result_invalid_input` | ✓ | simulation_module_instance, mock_reasoner |
| `test_explain_result_reasoner_error` | ✓ | simulation_module_instance, mock_reasoner |
| `test_run_in_secure_sandbox` | ✓ | simulation_module_instance, mock_sandbox |
| `test_run_in_secure_sandbox_with_custom_policy` | ✓ | simulation_module_instance, mock_sandbox |
| `test_register_message_handlers` | ✓ | simulation_module_instance, mock_message_bus |
| `test_register_message_handlers_not_initialized` | ✓ | mock_db, mock_message_bus |
| `test_handle_simulation_request_success` | ✓ | simulation_module_instance, mock_message_bus, mock_agent_runners |
| `test_handle_simulation_request_with_explanation` | ✓ | simulation_module_instance, mock_message_bus, mock_agent_runners, mock_reasoner |
| `test_handle_simulation_request_error` | ✓ | simulation_module_instance, mock_message_bus, mock_agent_runners |

**Constants:** `logger`, `TEST_CONFIG`, `SAMPLE_SIMULATION_CONFIG`, `SAMPLE_SWARM_CONFIG`, `SAMPLE_PARALLEL_CONFIG`, `SAMPLE_QUANTUM_PARAMS`

---

## tests/test_simulation_onboard.py
**Lines:** 426

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_external_dependencies` |  |  |
| `mock_filesystem` |  |  |
| `mock_user_input` |  |  |
| `test_onboarding_wizard_full_flow` | ✓ | mock_external_dependencies, mock_filesystem, mock_user_input |
| `test_safe_mode_profile_generation` | ✓ | mock_filesystem, mock_external_dependencies |
| `test_run_health_checks_with_failures` | ✓ | mock_external_dependencies, mock_filesystem |
| `test_reset_to_safe_mode` | ✓ | mock_filesystem, mock_external_dependencies |
| `test_generate_secure_config_local_encrypted` |  | mock_filesystem, mock_external_dependencies |
| `test_load_secure_config_local_decrypted` |  | mock_filesystem, mock_external_dependencies |
| `test_run_basic_onboarding_tests` |  | mock_filesystem, mock_external_dependencies |
| `test_get_user_input_non_interactive` |  |  |

---

## tests/test_simulation_parallel.py
**Lines:** 348

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `temp_yaml_file` |  | tmp_path |
| `mock_config_data` |  |  |
| `mock_rl_tuner_config` |  |  |
| `mock_resources` |  |  |
| `test_get_or_create_metric_success` |  |  |
| `test_parallel_config_load_success` |  | temp_yaml_file, mock_config_data, monkeypatch |
| `test_parallel_config_validation_failure` |  | temp_yaml_file, mock_config_data |
| `test_ray_rllib_concurrency_tuner_init_success` | ✓ | mock_rl_tuner_config, monkeypatch, caplog |
| `test_ray_rllib_concurrency_tuner_check_liveness_success` |  | monkeypatch |
| `test_ray_rllib_concurrency_tuner_get_optimal_concurrency` |  | monkeypatch |
| `test_get_available_resources` |  |  |
| `test_auto_tune_concurrency_heuristic` |  |  |
| `test_progress_reporter_task_completed` | ✓ |  |
| `test_progress_reporter_finish` |  |  |
| `test_execute_local_asyncio_success` | ✓ |  |
| `test_execute_kubernetes_success` | ✓ | monkeypatch |
| `test_execute_aws_batch_success` | ✓ | monkeypatch |
| `test_run_parallel_simulations_success` | ✓ | monkeypatch |
| `test_run_parallel_simulations_no_backends` |  | monkeypatch |

**Constants:** `pytestmark`

---

## tests/test_simulation_pip_audit_plugin.py
**Lines:** 352

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_external_dependencies` |  |  |
| `mock_filesystem` |  |  |
| `test_pip_audit_config_validation_success` |  |  |
| `test_pip_audit_config_invalid_scan_method` |  |  |
| `test_load_config_from_env_override` |  |  |
| `test_plugin_health_success` | ✓ | mock_external_dependencies |
| `test_plugin_health_cli_not_found` | ✓ |  |
| `test_validate_safe_args_success` |  |  |
| `test_validate_safe_args_injection_failure` |  |  |
| `test_scan_dependencies_success_with_findings` | ✓ | mock_external_dependencies, mock_filesystem |
| `test_scan_dependencies_timeout_with_retry_success` | ✓ | mock_external_dependencies |
| `test_scan_dependencies_timeout_persistent_failure` | ✓ | mock_external_dependencies |
| `test_scan_dependencies_requirements_file_not_found` | ✓ |  |

---

## tests/test_simulation_plugin_manager.py
**Lines:** 459

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `add_health_method_to_plugin_manager` |  |  |
| `mock_external_dependencies` |  |  |
| `mock_plugin_and_manifest_files` |  |  |
| `test_load_plugin_success_python` | ✓ | mock_plugin_and_manifest_files |
| `test_load_plugin_failure_invalid_syntax` | ✓ | mock_plugin_and_manifest_files |
| `test_load_plugin_with_dangerous_permission` | ✓ | mock_plugin_and_manifest_files |
| `test_enable_disable_reload_plugin_cycle` | ✓ | mock_plugin_and_manifest_files |
| `test_discover_plugins_and_load_all` | ✓ | mock_plugin_and_manifest_files |
| `test_health_check_workflow` | ✓ | mock_plugin_and_manifest_files |
| `test_close_all_plugins_gracefully` | ✓ | mock_plugin_and_manifest_files |

---

## tests/test_simulation_quantum.py
**Lines:** 311

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `cleanup_backend_pool` |  |  |
| `test_get_or_create_metric_success` | ✓ | monkeypatch |
| `test_check_any_backend_available_success` |  | monkeypatch |
| `test_check_any_backend_available_failure` |  | monkeypatch |
| `test_alert_operator` | ✓ | caplog |
| `test_load_quantum_credentials_success` | ✓ | monkeypatch |
| `test_load_quantum_credentials_failure` | ✓ | monkeypatch |
| `test_check_backend_health_qiskit_success` | ✓ | monkeypatch |
| `test_check_backend_health_dwave_success` | ✓ | monkeypatch |
| `test_run_mutation_circuit_params_validation_success` | ✓ |  |
| `test_run_mutation_circuit_params_validation_failure` | ✓ |  |
| `test_forecast_failure_trend_params_validation_success` | ✓ |  |
| `test_forecast_failure_trend_params_validation_failure` | ✓ |  |
| `test_run_quantum_mutation_success` | ✓ | monkeypatch |
| `test_run_quantum_mutation_no_backend` | ✓ | monkeypatch |
| `test_quantum_forecast_failure_success` | ✓ | monkeypatch |
| `test_quantum_rl_agent_init_success` |  | monkeypatch |
| `test_quantum_rl_agent_init_failure` |  | monkeypatch |
| `test_quantum_plugin_api_get_available_backends` |  | monkeypatch |
| `test_quantum_plugin_api_perform_quantum_operation` | ✓ | monkeypatch |

**Constants:** `pytestmark`

---

## tests/test_simulation_registry.py
**Lines:** 774

### `TestAuditLogger`

| Method | Async | Args |
|--------|-------|------|
| `test_fallback_audit_logger_emit_event` | ✓ | self, caplog |
| `test_dlt_audit_logger_success` | ✓ | self |
| `test_dlt_audit_logger_fallback_on_error` | ✓ | self, caplog |
| `test_get_audit_logger_with_dlt` |  | self |
| `test_get_audit_logger_fallback` |  | self |

### `TestMetricsProvider`

| Method | Async | Args |
|--------|-------|------|
| `test_dummy_metrics_provider` |  | self |
| `test_prometheus_metrics_provider_init` |  | self |
| `test_get_metrics_provider_with_prometheus` |  | self |
| `test_get_metrics_provider_fallback` |  | self |

### `TestOutputRefiner`

| Method | Async | Args |
|--------|-------|------|
| `test_noop_output_refiner` | ✓ | self |
| `test_langchain_output_refiner_success` | ✓ | self |
| `test_langchain_output_refiner_fallback` | ✓ | self |
| `test_langchain_output_refiner_error_handling` | ✓ | self |

### `TestSecurityFunctions`

| Method | Async | Args |
|--------|-------|------|
| `test_generate_file_hash_success` |  | self |
| `test_generate_file_hash_file_not_found` |  | self |
| `test_sanitize_path_valid` |  | self |
| `test_sanitize_path_outside_root` |  | self |
| `test_redact_sensitive_api_keys` |  | self |
| `test_redact_sensitive_passwords` |  | self |
| `test_redact_sensitive_credit_cards` |  | self |

### `TestPluginValidation`

| Method | Async | Args |
|--------|-------|------|
| `test_validate_manifest_success` |  | self, valid_manifest |
| `test_validate_manifest_missing_keys` |  | self |
| `test_validate_manifest_invalid_type` |  | self |
| `test_check_plugin_dependencies_no_deps` | ✓ | self, valid_manifest |
| `test_check_plugin_dependencies_satisfied` | ✓ | self |
| `test_check_plugin_dependencies_missing` | ✓ | self |

### `TestRegistry`

| Method | Async | Args |
|--------|-------|------|
| `test_get_registry` |  | self, reset_registry |
| `test_is_allowed_not_in_allowlist` | ✓ | self |
| `test_is_allowed_hash_mismatch` | ✓ | self |
| `test_is_allowed_success` | ✓ | self |
| `test_register_plugin_no_manifest` | ✓ | self, reset_registry |
| `test_register_plugin_runner_success` | ✓ | self, reset_registry, mock_plugin_module |
| `test_register_plugin_invalid_runner` | ✓ | self, reset_registry, valid_manifest |
| `test_register_plugin_other_type` | ✓ | self, reset_registry |

### `TestPluginDiscovery`

| Method | Async | Args |
|--------|-------|------|
| `test_discover_and_register_all_from_directory` | ✓ | self, reset_registry |
| `test_discover_and_register_all_no_plugins` | ✓ | self, reset_registry |
| `test_discover_and_register_all_import_error` | ✓ | self, reset_registry |

### `TestPluginExecution`

| Method | Async | Args |
|--------|-------|------|
| `test_refine_plugin_output` | ✓ | self |
| `test_run_plugin_success` | ✓ | self, reset_registry, mock_plugin_module |
| `test_run_plugin_not_registered` | ✓ | self, reset_registry |
| `test_run_plugin_timeout` | ✓ | self, reset_registry |
| `test_run_plugin_exception` | ✓ | self, reset_registry |
| `test_run_plugin_with_sensitive_output` | ✓ | self, reset_registry |

### `TestIntegration`

| Method | Async | Args |
|--------|-------|------|
| `test_full_plugin_lifecycle` | ✓ | self, reset_registry |
| `test_concurrent_plugin_execution` | ✓ | self, reset_registry |

### `TestEdgeCases`

| Method | Async | Args |
|--------|-------|------|
| `test_empty_allowlist_security` | ✓ | self, reset_registry |
| `test_malformed_manifest_handling` | ✓ | self, reset_registry |
| `test_invalid_path_types` |  | self |
| `test_plugin_with_no_run_method` | ✓ | self, reset_registry |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_audit_logger` |  |  |
| `mock_metrics_provider` |  |  |
| `valid_manifest` |  |  |
| `mock_plugin_module` |  | valid_manifest |
| `reset_registry` |  |  |

---

## tests/test_simulation_runtime_tracer_plugin.py
**Lines:** 514

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_external_dependencies` |  |  |
| `mock_filesystem` |  |  |
| `get_metric_value` |  | metric |
| `test_plugin_health_success` | ✓ | mock_external_dependencies |
| `test_plugin_health_docker_not_found` | ✓ | mock_external_dependencies |
| `test_plugin_health_docker_not_found_but_unsafe_allowed` | ✓ | mock_external_dependencies |
| `test_analyze_runtime_behavior_success` | ✓ | mock_filesystem |
| `test_analyze_runtime_behavior_subprocess_failure` | ✓ | mock_filesystem |
| `test_analyze_runtime_behavior_timeout` | ✓ |  |
| `test_analyze_runtime_behavior_target_not_found` | ✓ |  |
| `test_analyze_runtime_behavior_empty_trace_log` | ✓ | mock_filesystem |
| `test_analyze_runtime_behavior_malformed_json_in_trace` | ✓ |  |

**Constants:** `plugin_paths`

---

## tests/test_simulation_sandbox.py
**Lines:** 424

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `temp_dir` |  | tmp_path |
| `mock_policy` |  |  |
| `mock_audit_log` |  | temp_dir |
| `reset_audit_hmac_key` |  |  |
| `test_sandbox_policy_validation_success` |  |  |
| `test_sandbox_policy_run_as_user_not_root` |  |  |
| `test_container_validation_config_success` |  |  |
| `test_container_validation_config_image_not_whitelist` |  |  |
| `test_log_audit` |  | mock_audit_log, monkeypatch |
| `test_verify_audit_log_integrity_recent` |  | mock_glob, mock_audit_log, monkeypatch |
| `test_verify_audit_log_integrity_mismatch` |  | mock_glob, mock_audit_log, monkeypatch |
| `test_cleanup_sandbox_docker` | ✓ | monkeypatch |
| `test_run_in_docker_sandbox_success` | ✓ | monkeypatch |
| `test_run_in_podman_sandbox_success` | ✓ | monkeypatch |
| `test_deploy_to_kubernetes_success` | ✓ | monkeypatch |
| `test_run_in_local_process_sandbox_success` | ✓ | monkeypatch |
| `test_burst_to_cloud_aws_success` | ✓ | monkeypatch |
| `test_run_chaos_experiment_success` | ✓ | monkeypatch |
| `test_run_in_sandbox_success` | ✓ | monkeypatch |
| `test_run_in_sandbox_no_backends` | ✓ | monkeypatch |
| `test_get_audit_hmac_key_env` |  | monkeypatch |
| `test_check_external_services_async_success` | ✓ | monkeypatch |
| `test_periodic_external_service_check` | ✓ | monkeypatch |
| `test_start_background_tasks` | ✓ | monkeypatch |

**Constants:** `pytestmark`, `_audit_hmac_key`

---

## tests/test_simulation_scala_test_runner_plugin.py
**Lines:** 447

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_sbt_and_java_in_path` |  |  |
| `test_plugin_health_success` | ✓ | mock_sbt_and_java_in_path |
| `test_plugin_health_sbt_not_found` | ✓ |  |
| `test_parse_junit_xml_success` |  |  |
| `test_parse_junit_xml_malformed` |  |  |
| `test_parse_scoverage_xml_success` |  |  |
| `test_parse_scoverage_xml_no_coverage_info` |  |  |
| `test_run_scala_tests_success_full_workflow` | ✓ |  |
| `test_run_scala_tests_test_failure` | ✓ |  |
| `test_run_scala_tests_sbt_not_found` | ✓ |  |
| `test_run_scala_tests_file_not_found` | ✓ |  |
| `test_run_scala_tests_with_existing_build_sbt` | ✓ |  |

**Constants:** `plugin_paths`

---

## tests/test_simulation_security_patch_generator_plugin.py
**Lines:** 441

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_external_dependencies` |  |  |
| `mock_config_path` |  |  |
| `test_llm_config_validation_success` |  |  |
| `test_llm_config_invalid_temperature` |  |  |
| `test_validate_vuln_details_success` |  |  |
| `test_validate_vuln_details_failure` |  |  |
| `test_parse_llm_output_diff_format` |  |  |
| `test_parse_llm_output_code_block` |  |  |
| `test_parse_llm_output_refusal` |  |  |
| `test_validate_patch_syntax_python_valid` |  |  |
| `test_validate_patch_syntax_python_invalid` |  |  |
| `test_validate_patch_syntax_non_python` |  |  |
| `test_generate_security_patch_success` | ✓ | mock_external_dependencies |
| `test_generate_security_patch_llm_refusal` | ✓ | mock_external_dependencies |
| `test_generate_security_patch_empty_response` | ✓ | mock_external_dependencies |
| `test_generate_security_patch_with_cache` | ✓ | mock_external_dependencies |
| `test_generate_security_patch_invalid_input` | ✓ |  |
| `test_plugin_health_success` | ✓ | mock_external_dependencies |
| `test_plugin_health_with_live_call` | ✓ | mock_external_dependencies |

**Constants:** `plugin_paths`

---

## tests/test_simulation_self_evolution_plugin.py
**Lines:** 329

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_check_content_safety` | ✓ | content |
| `config_env` |  | monkeypatch |
| `mock_meta_learning` |  |  |
| `mock_policy_engine` |  |  |
| `mock_llm` |  |  |
| `mock_audit_logger` |  |  |
| `dependency_patches` |  | mock_meta_learning, mock_policy_engine, mock_llm, mock_audit_logger |
| `test_evolution_config_validation_success` |  | config_env |
| `test_evolution_config_invalid_temperature` |  |  |
| `test_validate_agents_success` |  |  |
| `test_validate_agents_failure` |  |  |
| `test_plugin_health_success` | ✓ |  |
| `test_initiate_evolution_cycle_success` | ✓ | mock_meta_learning, mock_policy_engine, mock_llm |
| `test_initiate_evolution_cycle_llm_no_change` | ✓ | mock_llm, mock_policy_engine |
| `test_initiate_evolution_cycle_llm_api_error` | ✓ | mock_llm |
| `test_initiate_evolution_cycle_unsupported_strategy` | ✓ | mock_llm |
| `test_fallback_logic` | ✓ | monkeypatch |
| `test_audit_event_secret_scrubbing` | ✓ |  |
| `test_content_safety_check_mock` | ✓ |  |
| `test_initiate_evolution_cycle_unsafe_content` | ✓ | mock_llm |

**Constants:** `plugin_paths`

---

## tests/test_simulation_siem_aws_clients.py
**Lines:** 599

### `SIEMClientError` (Exception)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, client_type, original_exception, correlation_id |

### `SIEMClientConfigurationError` (SIEMClientError)

### `SIEMClientAuthError` (SIEMClientError)

### `SIEMClientConnectivityError` (SIEMClientError)

### `SIEMClientPublishError` (SIEMClientError)

### `SIEMClientQueryError` (SIEMClientError)

### `SIEMClientResponseError` (SIEMClientError)

### `AwsCloudWatchConfig`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |

### `AwsCloudWatchClient`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config |
| `_get_aws_client` | ✓ | self |
| `health_check` | ✓ | self |
| `send_log` | ✓ | self, log_entry |
| `send_logs` | ✓ | self, log_entries |
| `query_logs` | ✓ | self, query_string, time_range, limit |
| `_ensure_log_group_and_stream` | ✓ | self |
| `_parse_relative_time_range_to_ms` |  | self, time_range |
| `close` | ✓ | self |

### `TestConfiguration`

| Method | Async | Args |
|--------|-------|------|
| `test_valid_basic_config` |  | self |
| `test_empty_required_fields` |  | self |
| `test_auto_create_forbidden_in_production` |  | self |

### `TestCoreFunctionality`

| Method | Async | Args |
|--------|-------|------|
| `test_health_check_success` | ✓ | self, cloudwatch_client |
| `test_send_single_log` | ✓ | self, cloudwatch_client |
| `test_send_batch_logs` | ✓ | self, cloudwatch_client |
| `test_send_large_batch_chunking` | ✓ | self, cloudwatch_client |
| `test_query_logs_success` | ✓ | self, cloudwatch_client |
| `test_query_timeout` | ✓ | self, cloudwatch_client |
| `test_log_group_auto_creation` | ✓ | self, cloudwatch_client |
| `test_parse_time_range` |  | self, cloudwatch_client |

### `TestErrorHandling`

| Method | Async | Args |
|--------|-------|------|
| `test_auth_error` | ✓ | self, cloudwatch_client |
| `test_connectivity_error` | ✓ | self, cloudwatch_client |
| `test_rejected_logs` | ✓ | self, cloudwatch_client |
| `test_query_failure` | ✓ | self, cloudwatch_client |

### `TestConcurrency`

| Method | Async | Args |
|--------|-------|------|
| `test_concurrent_send_logs` | ✓ | self, cloudwatch_client |
| `test_concurrent_queries` | ✓ | self, cloudwatch_client |

### `TestIntegration`

| Method | Async | Args |
|--------|-------|------|
| `test_full_pipeline` | ✓ | self, cloudwatch_client |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_boto3_client` |  |  |
| `base_config` |  |  |
| `cloudwatch_client` | ✓ | base_config, mock_boto3_client |

---

## tests/test_simulation_siem_aws_e2e.py
**Lines:** 611

### `SIEMClientError` (Exception)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, client_type, original_exception, details, ...+1 |

### `SIEMClientConfigurationError` (SIEMClientError)

### `SIEMClientAuthError` (SIEMClientError)

### `SIEMClientConnectivityError` (SIEMClientError)

### `SIEMClientPublishError` (SIEMClientError)

### `SIEMClientQueryError` (SIEMClientError)

### `ClientError` (Exception)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, error_response, operation_name |

### `MockSecretsManager`

| Method | Async | Args |
|--------|-------|------|
| `get_secret` |  | self, key, default, required |

### `MockAwsCloudWatchClient`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config, metrics_hook |
| `__aenter__` | ✓ | self |
| `__aexit__` | ✓ | self, exc_type, exc_val, exc_tb |
| `close` | ✓ | self |
| `health_check` | ✓ | self, correlation_id |
| `send_log` | ✓ | self, log_entry, correlation_id |
| `send_logs` | ✓ | self, log_entries, correlation_id |
| `query_logs` | ✓ | self, query_string, time_range, limit, correlation_id |
| `_perform_send_log_logic` | ✓ | self, log_entry |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `alert_operator` |  | message, level |
| `get_siem_client` | ✓ | client_type, config, metrics_hook |
| `list_available_siem_clients` |  |  |
| `aws_credentials` |  |  |
| `ensure_log_group` |  | aws_credentials |
| `metrics_collector` |  |  |
| `siem_config` |  | aws_credentials |
| `aws_client` | ✓ | siem_config, metrics_collector |
| `generate_log_entry` |  | severity, include_sensitive |
| `generate_log_batch` |  | count, include_sensitive |
| `wait_for_logs_to_be_indexed` | ✓ | client, test_id, max_wait_time |
| `test_client_initialization` | ✓ | siem_config, metrics_collector |
| `test_health_check` | ✓ | aws_client |
| `test_send_single_log` | ✓ | aws_client |
| `test_send_log_batch` | ✓ | aws_client |
| `test_query_logs` | ✓ | aws_client |
| `test_metrics_collection` | ✓ | aws_client, metrics_collector |
| `test_rate_limiting` | ✓ | siem_config, metrics_collector |
| `test_retry_logic` | ✓ | aws_client, monkeypatch |
| `test_concurrent_operations` | ✓ | aws_client |

**Constants:** `pytestmark`, `logger`, `PRODUCTION_MODE`, `_base_logger`, `SECRETS_MANAGER`, `DEFAULT_REGION`, `LOG_GROUP_NAME`, `LOG_STREAM_NAME`, `TEST_SECRET_ID`, `TEST_QUERY_TIMEOUT_SECONDS`

---

## tests/test_simulation_siem_azure_clients.py
**Lines:** 927

### `SIEMClientError` (Exception)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, client_type, original_exception, correlation_id |

### `SIEMClientConfigurationError` (SIEMClientError)

### `SIEMClientAuthError` (SIEMClientError)

### `SIEMClientConnectivityError` (SIEMClientError)

### `SIEMClientPublishError` (SIEMClientError)

### `SIEMClientQueryError` (SIEMClientError)

### `SIEMClientResponseError` (SIEMClientError)

### `AzureSentinelConfig`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |

### `AzureEventGridConfig`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |

### `AzureServiceBusConfig`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |

### `AzureSentinelClient`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config |
| `_get_session` | ✓ | self |
| `_ensure_shared_key_loaded` | ✓ | self |
| `health_check` | ✓ | self |
| `send_log` | ✓ | self, log_entry |
| `send_logs` | ✓ | self, log_entries |
| `query_logs` | ✓ | self, query_string, time_range, limit |
| `close` | ✓ | self |

### `AzureEventGridClient`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config |
| `_ensure_key_loaded` | ✓ | self |
| `health_check` | ✓ | self |
| `send_log` | ✓ | self, log_entry |
| `send_logs` | ✓ | self, log_entries |
| `query_logs` | ✓ | self, query_string, time_range, limit |
| `close` | ✓ | self |

### `AzureServiceBusClient`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config |
| `_get_servicebus_client` | ✓ | self |
| `health_check` | ✓ | self |
| `send_log` | ✓ | self, log_entry |
| `send_logs` | ✓ | self, log_entries |
| `query_logs` | ✓ | self, query_string, time_range, limit |
| `close` | ✓ | self |

### `TestConfiguration`

| Method | Async | Args |
|--------|-------|------|
| `test_sentinel_valid_config` |  | self |
| `test_sentinel_invalid_log_type` |  | self |
| `test_sentinel_production_mode_validation` |  | self |
| `test_eventgrid_valid_config` |  | self |
| `test_servicebus_queue_or_topic_required` |  | self |
| `test_servicebus_only_one_destination` |  | self |

### `TestAzureSentinel`

| Method | Async | Args |
|--------|-------|------|
| `test_health_check_success` | ✓ | self, sentinel_client |
| `test_health_check_failure` | ✓ | self, sentinel_client, mock_aiohttp_session |
| `test_send_single_log` | ✓ | self, sentinel_client |
| `test_send_batch_logs` | ✓ | self, sentinel_client |
| `test_large_batch_chunking` | ✓ | self, sentinel_client |
| `test_query_logs_success` | ✓ | self, sentinel_client |
| `test_query_logs_auth_failure` | ✓ | self, sentinel_client |
| `test_query_without_aad` | ✓ | self, sentinel_client |
| `test_missing_shared_key` | ✓ | self, sentinel_config |

### `TestAzureEventGrid`

| Method | Async | Args |
|--------|-------|------|
| `test_health_check_success` | ✓ | self, eventgrid_client |
| `test_send_single_event` | ✓ | self, eventgrid_client |
| `test_send_batch_events` | ✓ | self, eventgrid_client |
| `test_query_not_supported` | ✓ | self, eventgrid_client |
| `test_missing_key` | ✓ | self, eventgrid_config |

### `TestAzureServiceBus`

| Method | Async | Args |
|--------|-------|------|
| `test_health_check_with_queue` | ✓ | self, servicebus_client |
| `test_health_check_with_topic` | ✓ | self, servicebus_config |
| `test_send_to_queue` | ✓ | self, servicebus_client |
| `test_send_to_topic` | ✓ | self, servicebus_config |
| `test_send_batch_messages` | ✓ | self, servicebus_client |
| `test_query_not_supported` | ✓ | self, servicebus_client |
| `test_no_destination_configured` | ✓ | self, servicebus_config |

### `TestConcurrency`

| Method | Async | Args |
|--------|-------|------|
| `test_concurrent_sentinel_sends` | ✓ | self, sentinel_client |
| `test_concurrent_eventgrid_sends` | ✓ | self, eventgrid_client |
| `test_concurrent_servicebus_sends` | ✓ | self, servicebus_client |

### `TestErrorHandling`

| Method | Async | Args |
|--------|-------|------|
| `test_sentinel_api_error` | ✓ | self, sentinel_client, mock_aiohttp_session |
| `test_invalid_base64_key` | ✓ | self, sentinel_config |
| `test_eventgrid_missing_endpoint` | ✓ | self |
| `test_servicebus_connection_error` | ✓ | self, servicebus_client |

### `TestIntegration`

| Method | Async | Args |
|--------|-------|------|
| `test_sentinel_full_pipeline` | ✓ | self, sentinel_client |
| `test_multi_client_workflow` | ✓ | self, sentinel_client, eventgrid_client, servicebus_client |

### `TestPerformance`

| Method | Async | Args |
|--------|-------|------|
| `test_sentinel_large_batch_performance` | ✓ | self, sentinel_client |
| `test_eventgrid_throughput` | ✓ | self, eventgrid_client |

### `TestSecurity`

| Method | Async | Args |
|--------|-------|------|
| `test_production_mode_enforcement` |  | self |
| `test_shared_key_encryption` | ✓ | self, sentinel_client |
| `test_connection_string_masking` | ✓ | self, servicebus_client |

### `TestCleanup`

| Method | Async | Args |
|--------|-------|------|
| `test_sentinel_cleanup` | ✓ | self, sentinel_client |
| `test_servicebus_cleanup` | ✓ | self, servicebus_client |
| `test_eventgrid_cleanup` | ✓ | self, eventgrid_client |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_aiohttp_session` |  |  |
| `sentinel_config` |  |  |
| `eventgrid_config` |  |  |
| `servicebus_config` |  |  |
| `sentinel_client` | ✓ | sentinel_config, mock_aiohttp_session |
| `eventgrid_client` | ✓ | eventgrid_config |
| `servicebus_client` | ✓ | servicebus_config |

---

## tests/test_simulation_siem_base.py
**Lines:** 1107

### `SIEMClientError` (Exception)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, client_type, original_exception, details, ...+1 |

### `SIEMClientConfigurationError` (SIEMClientError)

### `SIEMClientAuthError` (SIEMClientError)

### `SIEMClientConnectivityError` (SIEMClientError)

### `SIEMClientResponseError` (SIEMClientError)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, client_type, status_code, response_text, ...+3 |

### `SIEMClientQueryError` (SIEMClientError)

### `SIEMClientPublishError` (SIEMClientError)

### `SIEMClientValidationError` (SIEMClientError)

### `GenericLogEvent`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `parse_obj` |  | cls, obj |
| `dict` |  | self |

### `AuditLogger`

| Method | Async | Args |
|--------|-------|------|
| `log_event` | ✓ | self, event_type |

### `SecretsManager`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `get_secret` | ✓ | self, key, default, required, backend |

### `BaseSIEMClient`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config, metrics_hook, paranoid_mode |
| `_scrub_env_vars_on_init` |  | self |
| `__aenter__` | ✓ | self |
| `__aexit__` | ✓ | self, exc_type, exc_val, exc_tb |
| `_set_correlation_id` |  | self, correlation_id |
| `_run_blocking_in_executor` | ✓ | self, func |
| `_apply_rate_limit` | ✓ | self |
| `_release_rate_limit` |  | self |
| `health_check` | ✓ | self, correlation_id |
| `_perform_health_check_logic` | ✓ | self |
| `send_log` | ✓ | self, log_entry, correlation_id, validate_schema |
| `_perform_send_log_logic` | ✓ | self, log_entry |
| `send_logs` | ✓ | self, log_entries, correlation_id, validate_schema |
| `_perform_send_logs_batch_logic` | ✓ | self, log_entries |
| `query_logs` | ✓ | self, query_string, time_range, limit, correlation_id |
| `_perform_query_logs_logic` | ✓ | self, query_string, time_range, limit |
| `close` | ✓ | self |
| `_parse_relative_time_range_to_ms` |  | self, time_range_str |
| `_parse_relative_time_range_to_timedelta` |  | self, time_range_str |

### `AiohttpClientMixin`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `_get_session` | ✓ | self |
| `close` | ✓ | self |

### `ConcreteTestClient` (AiohttpClientMixin, BaseSIEMClient)
**Attributes:** client_type

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `_perform_health_check_logic` | ✓ | self |
| `_perform_send_log_logic` | ✓ | self, log_entry |
| `_perform_send_logs_batch_logic` | ✓ | self, log_entries |
| `_perform_query_logs_logic` | ✓ | self, query_string, time_range, limit |

### `TestClientInitialization`

| Method | Async | Args |
|--------|-------|------|
| `test_default_configuration` |  | self |
| `test_custom_configuration` |  | self |
| `test_paranoid_mode_scrubs_env_vars` |  | self |

### `TestSecretScrubbing`

| Method | Async | Args |
|--------|-------|------|
| `test_scrub_secrets` |  | self, data, expected |

### `TestAsyncOperations`

| Method | Async | Args |
|--------|-------|------|
| `test_run_blocking_in_executor` | ✓ | self, test_client |
| `test_rate_limiting` | ✓ | self |
| `test_rate_limiting_timing` | ✓ | self |
| `test_context_manager` | ✓ | self |

### `TestHealthCheck`

| Method | Async | Args |
|--------|-------|------|
| `test_health_check_success` | ✓ | self, test_client |
| `test_health_check_failure` | ✓ | self, test_client |
| `test_health_check_exception` | ✓ | self, test_client |

### `TestLogSending`

| Method | Async | Args |
|--------|-------|------|
| `test_send_log_valid_schema` | ✓ | self, test_client |
| `test_send_log_invalid_schema` | ✓ | self, test_client |
| `test_send_log_no_validation` | ✓ | self, test_client |
| `test_send_logs_batch` | ✓ | self, test_client |
| `test_send_logs_batch_with_failures` | ✓ | self, test_client |

### `TestQueryLogs`

| Method | Async | Args |
|--------|-------|------|
| `test_query_logs_success` | ✓ | self, test_client |
| `test_query_logs_unimplemented` | ✓ | self |

### `TestAiohttpMixin`

| Method | Async | Args |
|--------|-------|------|
| `test_session_management` | ✓ | self, test_client |
| `test_session_close_retry` | ✓ | self, test_client |

### `TestTimeRangeParsing`

| Method | Async | Args |
|--------|-------|------|
| `test_parse_time_to_ms` |  | self, test_client |
| `test_parse_time_to_timedelta` |  | self, test_client |

### `TestMetricsHook`

| Method | Async | Args |
|--------|-------|------|
| `test_metrics_hook_called` | ✓ | self |

### `TestCorrelationId`

| Method | Async | Args |
|--------|-------|------|
| `test_correlation_id_propagation` | ✓ | self, test_client |
| `test_correlation_id_in_errors` | ✓ | self, test_client |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `alert_operator` |  | message, level |
| `scrub_secrets` |  | data, patterns |
| `mock_alert_operator` |  | monkeypatch |
| `mock_production_mode` |  |  |
| `test_client` | ✓ |  |

**Constants:** `PRODUCTION_MODE`, `_base_logger`, `AUDIT`, `SECRETS_MANAGER`, `_compiled_global_secret_patterns`, `_compiled_env_secret_patterns`

---

## tests/test_simulation_siem_factory.py
**Lines:** 563

### `SIEMClientError` (Exception)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, client_type, original_exception |

### `SIEMClientConfigurationError` (SIEMClientError)

### `BaseSIEMClient`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config, metrics_hook, paranoid_mode |
| `__aenter__` | ✓ | self |
| `__aexit__` | ✓ | self, exc_type, exc_val, exc_tb |

### `SplunkClient` (BaseSIEMClient)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |

### `ElasticClient` (BaseSIEMClient)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |

### `DatadogClient` (BaseSIEMClient)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |

### `AwsCloudWatchClient` (BaseSIEMClient)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |

### `GcpLoggingClient` (BaseSIEMClient)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |

### `AzureSentinelClient` (BaseSIEMClient)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |

### `AzureEventGridClient` (BaseSIEMClient)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |

### `AzureServiceBusClient` (BaseSIEMClient)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |

### `SecretsManager`

| Method | Async | Args |
|--------|-------|------|
| `get_secret` | ✓ | self, key, default |

### `TestGetSiemClient`

| Method | Async | Args |
|--------|-------|------|
| `test_successful_instantiation` |  | self, valid_config, mock_metrics_hook |
| `test_unknown_client_type` |  | self, valid_config, mock_metrics_hook, mock_alert_operator |
| `test_missing_metrics_hook` |  | self, valid_config, mock_alert_operator |
| `test_production_mode_requires_paranoid` |  | self, valid_config, mock_metrics_hook, mock_alert_operator |
| `test_production_mode_with_paranoid` |  | self, valid_config, mock_metrics_hook |
| `test_client_init_error_handling` |  | self, valid_config, mock_metrics_hook, mock_alert_operator |
| `test_all_client_types` |  | self, valid_config, mock_metrics_hook |

### `TestListAvailableClients`

| Method | Async | Args |
|--------|-------|------|
| `test_lists_all_clients` |  | self |
| `test_client_info_structure` |  | self |
| `test_client_descriptions` |  | self |

### `TestConcurrentOperations`

| Method | Async | Args |
|--------|-------|------|
| `test_concurrent_same_type` | ✓ | self, valid_config, mock_metrics_hook |
| `test_concurrent_mixed_types` | ✓ | self, mock_metrics_hook |

### `TestErrorScenarios`

| Method | Async | Args |
|--------|-------|------|
| `test_registry_manipulation` |  | self, valid_config, mock_metrics_hook |
| `test_client_with_config_validation_error` |  | self, mock_metrics_hook |

### `TestIntegration`

| Method | Async | Args |
|--------|-------|------|
| `test_full_lifecycle` |  | self, mock_metrics_hook |
| `test_async_context_manager` | ✓ | self, valid_config, mock_metrics_hook |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `alert_operator` |  | message, level |
| `get_siem_client` |  | siem_type, config, metrics_hook |
| `list_available_siem_clients` |  |  |
| `reset_globals` |  |  |
| `mock_alert_operator` |  |  |
| `mock_metrics_hook` |  |  |
| `valid_config` |  |  |

**Constants:** `PRODUCTION_MODE`, `_base_logger`, `SECRETS_MANAGER`

---

## tests/test_simulation_siem_gcp_clients.py
**Lines:** 714

### `SIEMClientError` (Exception)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, client_type, original_exception, details, ...+1 |

### `SIEMClientConfigurationError` (SIEMClientError)

### `SIEMClientAuthError` (SIEMClientError)

### `SIEMClientConnectivityError` (SIEMClientError)

### `SIEMClientPublishError` (SIEMClientError)

### `SIEMClientQueryError` (SIEMClientError)

### `SIEMClientResponseError` (SIEMClientError)

### `SIEMClientValidationError` (SIEMClientError)

### `GoogleAPIError` (Exception)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message |

### `Forbidden` (GoogleAPIError)

### `NotFound` (GoogleAPIError)

### `GoogleAPICallError` (GoogleAPIError)

### `SecretsManager`

| Method | Async | Args |
|--------|-------|------|
| `get_secret` | ✓ | self, key, default |

### `GcpLoggingConfig`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `dict` |  | self, exclude_unset |

### `BaseSIEMClient`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config, metrics_hook, paranoid_mode |
| `_run_blocking_in_executor` | ✓ | self, func |
| `_parse_relative_time_range_to_timedelta` |  | self, time_range |
| `close` | ✓ | self |

### `GCPSecretManagerBackend`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, project_id |
| `get_secret` | ✓ | self, secret_id |
| `close` | ✓ | self |

### `GcpLoggingClient` (BaseSIEMClient)
**Attributes:** client_type, MAX_BATCH_SIZE

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config, metrics_hook, paranoid_mode |
| `_ensure_credentials_loaded` | ✓ | self |
| `_encoded_log_id` |  | self |
| `_get_gcp_client` | ✓ | self |
| `health_check` | ✓ | self, correlation_id |
| `send_log` | ✓ | self, log_entry, validate_schema, correlation_id |
| `send_logs` | ✓ | self, log_entries, validate_schema, correlation_id |
| `query_logs` | ✓ | self, query_string, time_range, limit, correlation_id |
| `close` | ✓ | self |

### `TestConfiguration`

| Method | Async | Args |
|--------|-------|------|
| `test_valid_config` |  | self, default_config |
| `test_missing_project_id` |  | self |
| `test_production_mode_validation` |  | self |
| `test_production_requires_credentials` |  | self |

### `TestClientInitialization`

| Method | Async | Args |
|--------|-------|------|
| `test_successful_init` |  | self, default_config |
| `test_invalid_config` |  | self |
| `test_credentials_loading` | ✓ | self, default_config |

### `TestHealthCheck`

| Method | Async | Args |
|--------|-------|------|
| `test_health_check_success` | ✓ | self, default_config, mock_gcp_client |
| `test_health_check_failure` | ✓ | self, default_config |

### `TestLogSending`

| Method | Async | Args |
|--------|-------|------|
| `test_send_single_log` | ✓ | self, default_config, mock_gcp_client |
| `test_send_batch_logs` | ✓ | self, default_config, mock_gcp_client |
| `test_large_batch_chunking` | ✓ | self, default_config, mock_gcp_client |
| `test_send_log_failure` | ✓ | self, default_config |

### `TestQueryLogs`

| Method | Async | Args |
|--------|-------|------|
| `test_query_logs_success` | ✓ | self, default_config, mock_gcp_client |
| `test_query_with_time_range` | ✓ | self, default_config, mock_gcp_client |

### `TestConcurrentOperations`

| Method | Async | Args |
|--------|-------|------|
| `test_concurrent_sends` | ✓ | self, default_config, mock_gcp_client |
| `test_concurrent_queries` | ✓ | self, default_config, mock_gcp_client |

### `TestResourceCleanup`

| Method | Async | Args |
|--------|-------|------|
| `test_temp_credentials_cleanup` | ✓ | self, default_config |
| `test_cleanup_on_error` | ✓ | self, default_config |

### `TestErrorHandling`

| Method | Async | Args |
|--------|-------|------|
| `test_auth_error` | ✓ | self, default_config, mock_gcp_client |
| `test_not_found_error` | ✓ | self, default_config, mock_gcp_client |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `alert_operator` |  | message, level |
| `reset_globals` |  |  |
| `mock_gcp_client` |  |  |
| `default_config` |  |  |

**Constants:** `PRODUCTION_MODE`, `GCP_AVAILABLE`, `_base_logger`, `SECRETS_MANAGER`

---

## tests/test_simulation_siem_generic_clients.py
**Lines:** 990

### `SIEMClientError` (Exception)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, client_type, original_exception, details, ...+1 |

### `SIEMClientConfigurationError` (SIEMClientError)

### `SIEMClientAuthError` (SIEMClientError)

### `SIEMClientConnectivityError` (SIEMClientError)

### `SIEMClientPublishError` (SIEMClientError)

### `SIEMClientQueryError` (SIEMClientError)

### `SIEMClientResponseError` (SIEMClientError)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, client_type, status_code, response_text, ...+3 |

### `SIEMClientValidationError` (SIEMClientError)

### `ClientError` (Exception)

### `ClientResponseError` (ClientError)

### `ClientConnectionError` (ClientError)

### `SecretsManager`

| Method | Async | Args |
|--------|-------|------|
| `get_secret` |  | self, key, default, required |

### `SplunkConfig`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `_validate` |  | self |
| `dict` |  | self, exclude_unset |

### `ElasticConfig`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `_validate` |  | self |
| `dict` |  | self, exclude_unset |

### `DatadogConfig`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `_validate` |  | self |
| `dict` |  | self, exclude_unset |

### `BaseSIEMClient`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config, metrics_hook, paranoid_mode |
| `_run_blocking_in_executor` | ✓ | self, func |
| `_parse_relative_time_range_to_ms` |  | self, time_range |
| `close` | ✓ | self |

### `MockAsyncResponse`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, status, text, json_data |
| `__aenter__` | ✓ | self |
| `__aexit__` | ✓ | self, exc_type, exc_val, exc_tb |
| `text` | ✓ | self |
| `json` | ✓ | self |

### `AiohttpClientMixin`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self |
| `_get_session` | ✓ | self |
| `close` | ✓ | self |

### `SplunkClient` (AiohttpClientMixin, BaseSIEMClient)
**Attributes:** client_type

| Method | Async | Args |
|--------|-------|------|
| `_ensure_config_loaded` | ✓ | self |
| `_hec_health_url` |  | self |
| `health_check` | ✓ | self, correlation_id |
| `send_log` | ✓ | self, log_entry, validate_schema, correlation_id |
| `send_logs` | ✓ | self, log_entries, validate_schema, correlation_id |
| `query_logs` | ✓ | self, query_string, time_range, limit, correlation_id |

### `ElasticClient` (AiohttpClientMixin, BaseSIEMClient)
**Attributes:** client_type

| Method | Async | Args |
|--------|-------|------|
| `_ensure_config_loaded` | ✓ | self |
| `health_check` | ✓ | self, correlation_id |
| `send_log` | ✓ | self, log_entry, validate_schema, correlation_id |
| `send_logs` | ✓ | self, log_entries, validate_schema, correlation_id |
| `query_logs` | ✓ | self, query_string, time_range, limit, correlation_id |

### `DatadogClient` (AiohttpClientMixin, BaseSIEMClient)
**Attributes:** client_type

| Method | Async | Args |
|--------|-------|------|
| `_ensure_config_loaded` | ✓ | self |
| `health_check` | ✓ | self, correlation_id |
| `send_log` | ✓ | self, log_entry, validate_schema, correlation_id |
| `send_logs` | ✓ | self, log_entries, validate_schema, correlation_id |
| `query_logs` | ✓ | self, query_string, time_range, limit, correlation_id |

### `TestConfiguration`

| Method | Async | Args |
|--------|-------|------|
| `test_splunk_valid_config` |  | self, splunk_config |
| `test_splunk_production_validation` |  | self |
| `test_elastic_valid_config` |  | self, elastic_config |
| `test_elastic_missing_auth` |  | self |
| `test_datadog_valid_config` |  | self, datadog_config |

### `TestSplunkClient`

| Method | Async | Args |
|--------|-------|------|
| `test_health_check` | ✓ | self, splunk_config |
| `test_send_single_log` | ✓ | self, splunk_config |
| `test_send_batch_logs` | ✓ | self, splunk_config |
| `test_large_batch_chunking` | ✓ | self, splunk_config |
| `test_query_logs` | ✓ | self, splunk_config |

### `TestElasticClient`

| Method | Async | Args |
|--------|-------|------|
| `test_health_check` | ✓ | self, elastic_config |
| `test_send_batch_logs` | ✓ | self, elastic_config |
| `test_query_logs` | ✓ | self, elastic_config |

### `TestDatadogClient`

| Method | Async | Args |
|--------|-------|------|
| `test_health_check` | ✓ | self, datadog_config |
| `test_send_batch_logs` | ✓ | self, datadog_config |
| `test_query_logs` | ✓ | self, datadog_config |

### `TestConcurrentOperations`

| Method | Async | Args |
|--------|-------|------|
| `test_concurrent_splunk_sends` | ✓ | self, splunk_config |
| `test_concurrent_elastic_sends` | ✓ | self, elastic_config |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `alert_operator` |  | message, level |
| `_is_transient_status` |  | status_code |
| `_maybe_await` | ✓ | value |
| `_get_secret` | ✓ | key, default, required |
| `reset_globals` |  |  |
| `splunk_config` |  |  |
| `elastic_config` |  |  |
| `datadog_config` |  |  |

**Constants:** `PRODUCTION_MODE`, `_base_logger`, `SECRETS_MANAGER`

---

## tests/test_simulation_siem_integration_plugin.py
**Lines:** 339

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_external_dependencies` |  |  |
| `test_config_model_validation_success` |  |  |
| `test_config_model_validation_invalid_type` |  |  |
| `test_policy_enforcer_mask_rule_enforcement` |  |  |
| `test_policy_enforcer_block_rule_enforcement` |  |  |
| `test_send_siem_event_success` | ✓ | mock_external_dependencies |
| `test_send_siem_event_policy_blocked` | ✓ | mock_external_dependencies |
| `test_send_siem_event_backend_disabled` | ✓ | mock_external_dependencies |
| `test_query_siem_logs_success` | ✓ | mock_external_dependencies |

**Constants:** `SIEM_CONFIG_MODEL`

---

## tests/test_simulation_siem_main.py
**Lines:** 582

### `SIEMClientError` (Exception)

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, message, client_type, original_exception, details, ...+1 |

### `SIEMClientConfigurationError` (SIEMClientError)

### `SIEMClientAuthError` (SIEMClientError)

### `SIEMClientConnectivityError` (SIEMClientError)

### `SIEMClientPublishError` (SIEMClientError)

### `SIEMClientQueryError` (SIEMClientError)

### `SIEMClientValidationError` (SIEMClientError)

### `MockSecretsManager`

| Method | Async | Args |
|--------|-------|------|
| `get_secret` |  | self, key, default, required |

### `BaseSIEMClient`

| Method | Async | Args |
|--------|-------|------|
| `__init__` |  | self, config, metrics_hook, paranoid_mode |
| `__aenter__` | ✓ | self |
| `__aexit__` | ✓ | self, exc_type, exc_val, exc_tb |
| `close` | ✓ | self |
| `health_check` | ✓ | self, correlation_id |
| `send_log` | ✓ | self, log_entry, correlation_id |
| `send_logs` | ✓ | self, log_entries, correlation_id |
| `query_logs` | ✓ | self, query_string, time_range, limit, correlation_id |

### `SplunkClient` (BaseSIEMClient)
**Attributes:** client_type

### `ElasticClient` (BaseSIEMClient)
**Attributes:** client_type

### `DatadogClient` (BaseSIEMClient)
**Attributes:** client_type

### `AwsCloudWatchClient` (BaseSIEMClient)
**Attributes:** client_type

### `AzureSentinelClient` (BaseSIEMClient)
**Attributes:** client_type

### `GcpLoggingClient` (BaseSIEMClient)
**Attributes:** client_type

### `TestRunTests`

| Method | Async | Args |
|--------|-------|------|
| `test_run_tests_success` | ✓ | self, mock_env_vars |
| `test_run_tests_without_flag` | ✓ | self, monkeypatch |
| `test_run_tests_handles_client_error` | ✓ | self, mock_env_vars |

### `TestMain`

| Method | Async | Args |
|--------|-------|------|
| `test_main_in_production_mode` | ✓ | self |
| `test_main_in_test_mode` | ✓ | self, mock_env_vars |

### `TestCLI`

| Method | Async | Args |
|--------|-------|------|
| `test_cli_blocks_non_production` |  | self |
| `test_health_check_command_success` |  | self, tmp_path |
| `test_health_check_command_hmac_failure` |  | self, tmp_path |

### `TestFactoryFunctions`

| Method | Async | Args |
|--------|-------|------|
| `test_get_siem_client_valid` |  | self |
| `test_get_siem_client_invalid` |  | self |
| `test_list_available_siem_clients` |  | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `alert_operator` |  | message, level |
| `get_siem_client` |  | siem_type, config, metrics_hook |
| `list_available_siem_clients` |  |  |
| `_maybe_await` | ✓ | value |
| `_get_secret` | ✓ | key, default, required |
| `_scrub_and_dump` |  | obj |
| `run_tests` | ✓ |  |
| `main` | ✓ |  |
| `reset_globals` |  |  |
| `mock_env_vars` |  | monkeypatch |

**Constants:** `PRODUCTION_MODE`, `_base_logger`, `SECRETS_MANAGER`, `SIEM_CLIENT_REGISTRY`

---

## tests/test_simulation_utils.py
**Lines:** 307

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `utils_module` |  | tmp_path_factory, monkeypatch |
| `test_metrics_safe_on_reload` |  | monkeypatch, tmp_path |
| `test_hash_file_single_and_multi_algorithms` |  | utils_module, tmp_path |
| `test_hash_cache_invalidation_on_change` |  | utils_module, tmp_path |
| `test_find_files_by_pattern_dedup_and_validation` |  | utils_module, tmp_path |
| `test_print_file_diff_unified_and_context` |  | utils_module, tmp_path |
| `test_sanitize_and_validate_safe_path` |  | utils_module, tmp_path |
| `test_load_artifact_success_and_limits` |  | utils_module, tmp_path |
| `test_save_sim_result_and_provenance_chain` | ✓ | utils_module, tmp_path |
| `test_provenance_scrubs_secrets` |  | utils_module |
| `test_plugin_api_temp_dir_context_and_cleanup` |  | utils_module |
| `test_plugin_api_report_and_error_log_to_provenance` |  | utils_module |
| `test_plugin_api_core_compatibility_checks` |  | utils_module |
| `test_plugin_api_warn_sandbox_limitations` |  | utils_module, monkeypatch |

---

## tests/test_simulation_viz.py
**Lines:** 436

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_matplotlib` |  |  |
| `mock_filesystem` |  |  |
| `test_validate_panel_id_success` |  |  |
| `test_validate_panel_id_failure` |  |  |
| `test_plot_flakiness_trend_with_mock` |  | mock_matplotlib |
| `test_plot_coverage_history_no_data` |  |  |
| `test_plot_coverage_history_with_data` |  | mock_matplotlib |
| `test_plot_metric_trend_no_matplotlib` |  |  |
| `test_plot_metric_trend_with_data` |  | mock_matplotlib |
| `test_register_and_unregister_panel` |  |  |
| `test_register_panel_invalid_id` |  |  |
| `test_get_panels_for_role` |  |  |
| `test_batch_export_panels` | ✓ | mock_matplotlib, mock_filesystem |
| `test_pre_plot_hooks` |  |  |
| `test_post_plot_hooks` |  |  |
| `test_load_config_defaults` |  |  |
| `test_load_config_from_env` |  |  |
| `test_plot_with_exception` |  |  |
| `test_scrub_metadata_without_detect_secrets` |  |  |
| `test_dashboard_interface_check` |  |  |

**Constants:** `current_file`, `tests_dir`, `simulation_dir`, `plugins_dir`

---

## tests/test_simulation_web_ui_dashboard_plugin_template.py
**Lines:** 480

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_external_dependencies` |  |  |
| `api_client` |  |  |
| `test_dashboard_config_validation_success` |  |  |
| `test_dashboard_config_invalid_state_storage` |  |  |
| `test_validate_component_name_success` |  |  |
| `test_validate_component_name_failure` |  |  |
| `test_manifest_endpoint` |  | api_client |
| `test_components_endpoint` |  | api_client |
| `test_state_update_and_get_endpoints` | ✓ | api_client, mock_external_dependencies |
| `test_get_component_endpoint_success` |  | api_client |
| `test_get_component_endpoint_not_found` |  | api_client |
| `test_websocket_workflow` | ✓ | api_client, mock_external_dependencies |
| `test_websocket_multiple_connections` | ✓ | api_client, mock_external_dependencies |
| `test_state_update_with_invalid_json` |  | api_client |
| `test_component_data_scrubbing` |  | api_client, mock_external_dependencies |
| `test_websocket_error_handling` | ✓ | api_client, mock_external_dependencies |
| `test_rapid_state_updates` | ✓ | api_client, mock_external_dependencies |
| `test_component_registry_operations` |  |  |
| `test_config_environment_override` |  | mock_external_dependencies |
| `test_redis_fallback_to_memory` |  |  |
| `test_component_name_injection_prevention` |  | api_client |
| `test_api_key_authentication` |  | api_client |

---

## tests/test_simulation_workflow_viz.py
**Lines:** 437

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_external_dependencies` |  |  |
| `mock_filesystem` |  |  |
| `mock_result_data` |  |  |
| `test_workflow_viz_config_validation_success` |  |  |
| `test_validate_custom_phases_success` |  |  |
| `test_validate_custom_phases_failure_invalid_color` |  |  |
| `test_validate_export_path_success` |  | mock_filesystem |
| `test_validate_export_path_failure` |  | mock_filesystem |
| `test_scrub_secrets` |  |  |
| `test_render_workflow_viz_plotly_success` | ✓ | mock_external_dependencies, mock_result_data |
| `test_render_workflow_viz_matplotlib_fallback` | ✓ | mock_external_dependencies, mock_result_data |
| `test_batch_export_panels_success` | ✓ | mock_external_dependencies, mock_result_data, mock_filesystem |
| `test_dashboard_api_methods` |  |  |
| `test_render_workflow_viz_no_data` |  | mock_external_dependencies |
| `test_render_workflow_viz_no_libraries` |  |  |

**Constants:** `test_dir`, `self_fixing_engineer_dir`, `project_root`, `simulation_dir`, `plugins_dir`

---

## tests/test_test_generation_agents.py
**Lines:** 205

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_subprocess_exec_with_json_output` |  |  |
| `test_planner_agent_valid_plan` | ✓ |  |
| `test_planner_agent_invalid_json_sets_error` | ✓ |  |
| `test_generator_agent_strips_code_fences` | ✓ |  |
| `test_generator_agent_inserts_placeholder_on_empty` | ✓ |  |
| `test_security_agent_skips_if_bandit_unavailable` | ✓ |  |
| `test_security_agent_runs_bandit` | ✓ | mock_subprocess_exec_with_json_output |
| `test_performance_agent_skips_if_locust_unavailable` | ✓ |  |
| `test_performance_agent_runs_locust` | ✓ |  |
| `test_agents_increment_metrics` | ✓ |  |
| `test_sanitization_fallback` | ✓ | monkeypatch |

---

## tests/test_test_generation_api.py
**Lines:** 414

### `TestFunctional`

| Method | Async | Args |
|--------|-------|------|
| `test_generate_tests_valid_payload` |  | self, client |
| `test_generate_tests_missing_field` |  | self, client |
| `test_generate_tests_invalid_json` |  | self, client |
| `test_swagger_schema` |  | self, client |

### `TestSecurity`

| Method | Async | Args |
|--------|-------|------|
| `test_no_jwt_required_when_disabled` |  | self, client, mock_dependencies |
| `test_jwt_required_when_enabled` |  | self, client, mock_dependencies |

### `TestOperational`

| Method | Async | Args |
|--------|-------|------|
| `test_rate_limiting_enforced` |  | self, client, mock_dependencies |
| `test_cors_headers_applied_correctly` |  | self, client, mock_dependencies |

### `TestFailurePath`

| Method | Async | Args |
|--------|-------|------|
| `test_llm_init_failure` |  | self, client, mock_dependencies |
| `test_run_async_raises_timeout_error` |  | self, client, mock_dependencies |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_mock_invoke_graph` | ✓ | graph, state, config, progress_callback |
| `_mock_invoke_graph_timeout` | ✓ | graph, state, config, progress_callback |
| `mock_dependencies` |  | monkeypatch |
| `app` |  | mock_dependencies |
| `client` |  | app |
| `test_production_jwt_requirement` |  | monkeypatch, env, jwt_available |

---

## tests/test_test_generation_audit.py
**Lines:** 138

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `test_audit_with_arbiter` | ✓ | monkeypatch |
| `test_audit_fallback` | ✓ | monkeypatch, caplog |
| `test_audit_arbiter_failure` | ✓ | monkeypatch, caplog |
| `project` |  | tmp_path |
| `test_audit_non_serializable` | ✓ | project, bad_obj, monkeypatch |
| `test_audit_serialization_failure_handling` | ✓ | monkeypatch, caplog |
| `test_audit_export` |  |  |

---

## tests/test_test_generation_backends.py
**Lines:** 435

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_config` |  |  |
| `temp_project_root` |  |  |
| `test_backend_registry_register_and_get` |  |  |
| `test_backend_registry_overwrite_warning` |  | caplog |
| `test_backend_registry_list_backends` |  |  |
| `test_backend_registry_get_nonexistent` |  |  |
| `test_validate_inputs` |  | target_id, output_path, params, expected_exception |
| `test_pynguin_backend_init_success` |  | mock_config, temp_project_root |
| `test_pynguin_backend_init_missing_config_key` |  | mock_config, temp_project_root |
| `test_pynguin_backend_generate_success` | ✓ | mock_config, temp_project_root |
| `test_pynguin_backend_generate_timeout` | ✓ | mock_config, temp_project_root |
| `test_pynguin_backend_generate_no_file_generated` | ✓ | mock_config, temp_project_root |
| `test_jest_llm_backend_init_success` |  | mock_config, temp_project_root, monkeypatch |
| `test_jest_llm_backend_init_missing_config_key` |  | mock_config, temp_project_root |
| `test_jest_llm_backend_init_no_langchain` |  | monkeypatch, mock_config, temp_project_root |
| `test_jest_llm_backend_generate_success` | ✓ | mock_config, temp_project_root, monkeypatch |
| `test_jest_llm_backend_generate_timeout` | ✓ | mock_config, temp_project_root, monkeypatch |
| `test_jest_llm_backend_generate_retry` | ✓ | mock_config, temp_project_root, monkeypatch |
| `test_jest_llm_backend_generate_retry_exceeded` | ✓ | mock_config, temp_project_root, monkeypatch |
| `test_diffblue_backend_init_success` |  | mock_config, temp_project_root |
| `test_diffblue_backend_init_missing_config_key` |  | mock_config, temp_project_root |
| `test_diffblue_backend_generate_success` | ✓ | mock_config, temp_project_root, monkeypatch |
| `test_diffblue_backend_generate_simulated_failure` | ✓ | mock_config, temp_project_root, monkeypatch |
| `test_diffblue_backend_generate_timeout` | ✓ | mock_config, temp_project_root |
| `test_registry_with_all_backends` |  |  |

**Constants:** `pytestmark`

---

## tests/test_test_generation_compliance_mapper.py
**Lines:** 165

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `test_e2e_pipeline_full_success` | ✓ |  |
| `test_import_guard` |  |  |

---

## tests/test_test_generation_config.py
**Lines:** 256

### `TestLoadConfig`

| Method | Async | Args |
|--------|-------|------|
| `test_missing_file_uses_defaults_and_sets_globals` |  | self, project |
| `test_invalid_json_falls_back_with_warning` |  | self, project, caplog |
| `test_top_level_not_object_is_rejected_but_defaults_used` |  | self, project, caplog |
| `test_path_traversal_is_blocked` |  | self, project |
| `test_symlink_escaping_is_blocked` |  | self, project, tmp_path |
| `test_deep_merge_and_defaults_unchanged` |  | self, project |
| `test_immutability_and_independence_from_globals` |  | self, project |
| `test_coerce_critical_dicts_when_wrong_types` |  | self, project, caplog |

### `TestEnsureArtifactDirs`

| Method | Async | Args |
|--------|-------|------|
| `test_creates_all_directories_and_audit_parent` |  | self, project |
| `test_uses_defaults_when_not_overridden` |  | self, project |

### `TestRegressionGuards`

| Method | Async | Args |
|--------|-------|------|
| `test_logging_config_is_json_serializable` |  | self |
| `test_default_config_json_roundtrip` |  | self |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_isolate_cwd` |  | tmp_path, monkeypatch |
| `project` |  | tmp_path |
| `read_json` |  | path |
| `test_deep_merge_resilience_on_malformed_overrides` |  | project, bad_value, caplog |
| `test_ensure_dir_normalizes_and_creates` |  | project |
| `test_config_load` |  |  |

**Constants:** `MODULE_ROOT`, `config`

---

## tests/test_test_generation_console.py
**Lines:** 330

### `TestLoggingInitialization`

| Method | Async | Args |
|--------|-------|------|
| `test_dictconfig_failure_falls_back_with_glyph_parity` |  | self, monkeypatch, caplog |
| `test_plain_vs_rich_toggles` |  | self, monkeypatch, reload_console, caplog |

### `TestAuditHandler`

| Method | Async | Args |
|--------|-------|------|
| `test_audit_file_path_patched_and_writable` |  | self, tmp_path, reload_console, monkeypatch |

### `TestThreadSafety`

| Method | Async | Args |
|--------|-------|------|
| `test_backend_switch_is_thread_safe` |  | self, monkeypatch, reload_console |

### `TestGlyphMapping`

| Method | Async | Args |
|--------|-------|------|
| `test_ascii_vs_utf8_glyphs_via_reload` |  | self, monkeypatch, caplog |

### `TestProgressReporting`

| Method | Async | Args |
|--------|-------|------|
| `test_progress_bar_rich_available` |  | self, reload_console, monkeypatch |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_isolate_env` |  | monkeypatch |
| `reload_console` |  | monkeypatch |
| `test_fallback_logging` |  |  |

**Constants:** `MODULE_ROOT`

---

## tests/test_test_generation_e2e_pipeline.py
**Lines:** 166

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `test_e2e_pipeline_full_success` | ✓ |  |
| `test_pipeline_import` |  |  |

---

## tests/test_test_generation_gen_agent_cli.py
**Lines:** 212

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `runner` |  |  |
| `temp_config_file` |  |  |
| `test_cli_version_option` |  | runner |
| `test_cli_loads_config_file` |  | runner, temp_config_file |
| `test_run_async_command_graceful_shutdown` | ✓ |  |
| `test_cli_generate_command_runs` |  | runner, tmp_path |
| `test_cli_handles_missing_yaml_dependency` |  | runner, temp_config_file |
| `test_make_run_id` |  | monkeypatch |
| `test_graceful_shutdown_logs_message` | ✓ | caplog |
| `test_feedback_async_command` | ✓ | runner, tmp_path |
| `test_cli_import` |  |  |

---

## tests/test_test_generation_graph.py
**Lines:** 130

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `test_decide_to_refine_fail_status` | ✓ |  |
| `test_decide_to_refine_low_score` | ✓ |  |
| `test_decide_to_refine_max_repairs_reached` | ✓ |  |
| `test_step_enforces_timeout` | ✓ |  |
| `test_step_returns_value` | ✓ |  |
| `test_build_graph_with_langgraph_available` | ✓ |  |
| `test_build_graph_without_langgraph_falls_back` | ✓ |  |

---

## tests/test_test_generation_integration_e2e.py
**Lines:** 211

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `project` |  | tmp_path |
| `config` |  | project |
| `test_e2e_happy_and_quarantine_paths` | ✓ | project, config, monkeypatch |
| `test_e2e_cli_main` | ✓ | project, config, monkeypatch |
| `test_asyncmock_import_guard` |  |  |
| `test_orchestrator_class_rename_exists` |  |  |
| `test_policy_engine_import_guard` |  |  |
| `test_orchestrator_import` |  |  |

---

## tests/test_test_generation_integration_full.py
**Lines:** 182

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `runner` |  |  |
| `test_full_cli_run_with_mocked_agents` | ✓ | tmp_path, runner |
| `test_graph_with_real_agent_mocks` | ✓ | tmp_path |
| `test_feedback_log_written` | ✓ | tmp_path |
| `test_cli_import` |  |  |

---

## tests/test_test_generation_io.py
**Lines:** 154

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `temp_dir` |  |  |
| `test_validate_path_prevents_traversal` |  | temp_dir |
| `test_append_creates_file_and_writes_json` | ✓ | temp_dir |
| `test_append_existing_file_appends_newline` | ✓ | temp_dir |
| `test_append_to_gzip_file` | ✓ | temp_dir |
| `test_auto_compress_when_threshold_exceeded` | ✓ | temp_dir |
| `test_append_respects_redaction` | ✓ | temp_dir |
| `test_invalid_path_type_raises` | ✓ | temp_dir |
| `test_no_prometheus_duplicates` | ✓ | temp_dir |
| `test_io_import` |  |  |

---

## tests/test_test_generation_metrics.py
**Lines:** 55

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `test_metrics_available` | ✓ | monkeypatch |
| `test_dummy_metrics_noop` | ✓ | monkeypatch, caplog |
| `test_no_duplicate_metrics` |  |  |

---

## tests/test_test_generation_orchestrator.py
**Lines:** 346

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `project` |  | tmp_path |
| `mock_aiofiles_open` | ✓ |  |
| `orchestrator` |  | project, monkeypatch |
| `test_generate_tests_with_concurrency` | ✓ | orchestrator, project, monkeypatch |
| `test_integrate_with_stubbed_components` | ✓ | orchestrator, project, monkeypatch |
| `test_stub_initialization` | ✓ | project, monkeypatch |
| `test_calculate_test_quality_score` |  | orchestrator |
| `test_handle_single_test_deduplication` | ✓ | orchestrator, project, monkeypatch |
| `test_jira_integration` | ✓ | orchestrator, project, monkeypatch |
| `test_compliance_mapper_import_guard` |  |  |
| `test_compliance_reporting` | ✓ | orchestrator, project, monkeypatch |
| `test_low_mutation_score_quarantine` | ✓ | orchestrator, project, monkeypatch |
| `test_orchestrator_import` |  |  |

---

## tests/test_test_generation_orchestrator_cli.py
**Lines:** 192

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `project` |  | tmp_path |
| `test_main_invalid_paths` | ✓ | project, monkeypatch |
| `test_main_config_loading` | ✓ | project, monkeypatch |
| `test_make_run_id` |  | monkeypatch |
| `test_graceful_shutdown` |  | monkeypatch |
| `test_check_writable` |  | project, monkeypatch |
| `test_orchestrator_import_guard` |  |  |
| `test_oserror_typo_guard` |  |  |
| `test_check_disk_space` |  | project, monkeypatch |
| `test_orchestrator_rename` |  |  |
| `test_cli_args` | ✓ | monkeypatch |

---

## tests/test_test_generation_pipeline.py
**Lines:** 244

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `project` |  | tmp_path |
| `mock_aiofiles_open` | ✓ |  |
| `orchestrator` |  | project, monkeypatch |
| `test_empty_targets` | ✓ | orchestrator, project |
| `test_quarantine_on_failing_test` | ✓ | orchestrator, project, monkeypatch |
| `test_quarantine_on_low_coverage` | ✓ | orchestrator, project, monkeypatch |
| `test_quarantine_on_security_issues` | ✓ | orchestrator, project, monkeypatch |
| `test_quarantine_on_low_mutation_score` | ✓ | orchestrator, project, monkeypatch |
| `test_pr_required_stages_file` | ✓ | orchestrator, project, monkeypatch |

---

## tests/test_test_generation_plugins.py
**Lines:** 153

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `mock_logger` |  | monkeypatch |
| `test_slicing_limit` |  |  |
| `test_generate_tests_plugin_slicing_limit` |  |  |
| `test_generate_tests_with_unsupported_language` |  |  |
| `test_generate_tests_with_dangerous_code` |  |  |
| `test_ai_fallback_is_not_called_if_ai_succeeds` |  |  |
| `test_ai_fallback_is_called_if_ai_fails` |  |  |

**Constants:** `code_with_2_funcs`

---

## tests/test_test_generation_policy_and_audit.py
**Lines:** 445

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `temp_project_root` |  |  |
| `mock_config` |  |  |
| `mock_policy_file` |  | temp_project_root |
| `test_redact_sensitive` |  | input_data, expected_output |
| `test_policy_engine_init_success` |  | mock_policy_file, temp_project_root |
| `test_policy_engine_init_invalid_path` |  | temp_project_root |
| `test_policy_engine_init_missing_file` |  | temp_project_root |
| `test_policy_engine_should_generate_tests_local_allowed` | ✓ | mock_policy_file, temp_project_root |
| `test_policy_engine_should_generate_tests_local_denied_regulated` | ✓ | mock_policy_file, temp_project_root |
| `test_policy_engine_should_generate_tests_opa_enabled` | ✓ | mock_policy_file, temp_project_root, mock_config |
| `test_policy_engine_should_generate_tests_opa_failure` | ✓ | mock_policy_file, temp_project_root |
| `test_policy_engine_should_integrate_test_local_allowed` | ✓ | mock_policy_file, temp_project_root |
| `test_policy_engine_should_integrate_test_local_denied_quality` | ✓ | mock_policy_file, temp_project_root |
| `test_policy_engine_requires_pr_for_integration_local_required` | ✓ | mock_policy_file, temp_project_root |
| `test_policy_engine_requires_pr_for_integration_local_not_required` | ✓ | mock_policy_file, temp_project_root |
| `test_policy_engine_metrics` | ✓ | mock_policy_file, temp_project_root |
| `test_audit_logger_init_success` |  | temp_project_root |
| `test_audit_logger_init_no_dlt` |  |  |
| `test_audit_logger_log_event` | ✓ | temp_project_root |
| `test_audit_logger_log_event_redaction` | ✓ | temp_project_root |
| `test_event_bus_init_with_mq` |  | mock_config |
| `test_event_bus_init_no_mq` |  | mock_config |
| `test_event_bus_publish_critical_with_mq` | ✓ | mock_config |
| `test_event_bus_publish_non_critical_no_aiohttp` | ✓ | mock_config, monkeypatch |
| `test_event_bus_publish_webhook` | ✓ | mock_config |
| `test_event_bus_publish_slack` | ✓ | mock_config |
| `test_event_bus_publish_metrics_failure` | ✓ | mock_config |
| `test_event_bus_publish_redaction` | ✓ | mock_config |
| `test_audit_import` |  |  |

**Constants:** `pytestmark`

---

## tests/test_test_generation_reporting.py
**Lines:** 127

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `project` |  | tmp_path |
| `test_write_sarif_success` | ✓ | project, monkeypatch |
| `test_write_sarif_failure` | ✓ | project, monkeypatch |
| `test_generate_html_report_success` | ✓ | project, monkeypatch |
| `test_prometheus_toggle` |  | monkeypatch |

---

## tests/test_test_generation_runtime.py
**Lines:** 123

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `reset_runtime_flags` |  |  |
| `test_setup_logging_no_duplicate_handlers` |  |  |
| `test_load_config_with_env_override` |  | tmp_path |
| `test_load_config_with_dynaconf` |  | tmp_path |
| `test_load_config_with_pydantic` |  | tmp_path |
| `test_init_llm_pydantic` |  |  |
| `test_load_config_fallback` |  |  |
| `test_dependency_flags_set_correctly` |  |  |
| `test_audit_logger_fallback_when_missing` |  |  |
| `test_redact_sensitive` |  | input_data, expected |
| `test_runtime_import` |  |  |

---

## tests/test_test_generation_signal.py
**Lines:** 154

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `reset_signal_state` |  |  |
| `test_install_and_trigger_shutdown` |  | monkeypatch |
| `test_signal_debounce` |  | monkeypatch |
| `test_escalation_after_multiple_signals` |  | monkeypatch |
| `test_thread_dump_on_signal` |  | tmp_path, monkeypatch |
| `test_faulthandler_enabled` |  | monkeypatch |
| `test_forward_child_signals` |  | monkeypatch |
| `test_debounce_per_signal` |  | monkeypatch |
| `test_signal_import` |  |  |

---

## tests/test_test_generation_stubs.py
**Lines:** 100

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `test_dummy_policy_engine` | ✓ | caplog |
| `test_dummy_event_bus` | ✓ | caplog |
| `test_dummy_security_scanner` | ✓ | caplog |
| `test_dummy_pr_creator` | ✓ | caplog |
| `test_dummy_mutation_tester` | ✓ | caplog |
| `test_dummy_test_enricher` | ✓ | caplog |
| `test_dummy_policy` | ✓ |  |
| `test_dummy_policy_import` |  |  |

---

## tests/test_test_generation_utils.py
**Lines:** 727

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `temp_project_root` |  |  |
| `mock_config` |  |  |
| `mock_policy_engine` |  |  |
| `test_atco_config_init_success` |  | temp_project_root, mock_config |
| `test_atco_config_init_invalid_project_root` |  | mock_config |
| `test_atomic_write` |  | tmp_path |
| `test_generate_file_hash_success` |  | temp_project_root |
| `test_generate_file_hash_not_found` |  | temp_project_root |
| `test_backup_existing_test_success` | ✓ | temp_project_root |
| `test_backup_existing_test_not_found` | ✓ | temp_project_root |
| `test_compare_files_identical` |  | temp_project_root |
| `test_compare_files_different` |  | temp_project_root |
| `test_cleanup_temp_dir_file` | ✓ | temp_project_root |
| `test_cleanup_temp_dir_directory` | ✓ | temp_project_root |
| `test_security_scanner_bandit_success` | ✓ | temp_project_root, mock_config |
| `test_security_scanner_no_bandit` | ✓ | temp_project_root, mock_config |
| `test_knowledge_graph_client_update_metrics` | ✓ | temp_project_root, mock_config |
| `test_pr_creator_create_pr_success` | ✓ | temp_project_root, mock_config |
| `test_pr_creator_create_jira_success` | ✓ | temp_project_root, mock_config |
| `test_mutation_tester_success` | ✓ | temp_project_root, mock_config |
| `test_mutation_tester_disabled` | ✓ | temp_project_root, mock_config |
| `test_test_enricher_apply_plugins` | ✓ | temp_project_root |
| `test_test_enricher_plugin_failure` | ✓ | temp_project_root |
| `test_add_atco_header_python` |  | temp_project_root |
| `test_add_mocking_framework_import_python` |  | temp_project_root |
| `test_llm_refine_test_plugin_python` | ✓ | temp_project_root |
| `test_create_and_install_venv_success` | ✓ | temp_project_root |
| `test_create_and_install_venv_timeout` | ✓ | temp_project_root |
| `test_run_pytest_and_coverage_success` | ✓ | temp_project_root, mock_config |
| `test_run_pytest_and_coverage_timeout` | ✓ | temp_project_root, mock_config |
| `test_run_jest_and_coverage_success` | ✓ | temp_project_root, mock_config |
| `test_run_jest_and_coverage_no_npm` | ✓ | temp_project_root, mock_config |
| `test_run_junit_and_coverage_success` | ✓ | temp_project_root, mock_config |
| `test_run_junit_and_coverage_no_build_tool` | ✓ | temp_project_root, mock_config |
| `test_parse_coverage_delta_python` | ✓ | temp_project_root |
| `test_parse_coverage_delta_javascript` | ✓ | temp_project_root |
| `test_parse_coverage_delta_java_with_class_name` | ✓ | temp_project_root |
| `test_parse_coverage_delta_java_no_class_name` | ✓ | temp_project_root |
| `test_parse_coverage_delta_invalid_file` | ✓ | temp_project_root |
| `test_scan_for_uncovered_code_from_xml` |  | temp_project_root |
| `test_scan_for_uncovered_code_from_xml_no_file` |  | temp_project_root |
| `test_monitor_and_prioritize_uncovered_code` | ✓ | temp_project_root, mock_policy_engine, mock_config |
| `test_monitor_and_prioritize_uncovered_code_policy_denied` | ✓ | temp_project_root, mock_policy_engine, mock_config |
| `test_check_and_install_dependencies_all_present` | ✓ | temp_project_root |
| `test_check_and_install_dependencies_missing` | ✓ | temp_project_root |
| `test_scan_for_uncovered_code_rust_success` |  | temp_project_root |
| `test_scan_for_uncovered_code_rust_fully_covered` |  | temp_project_root |
| `test_monitor_import` |  |  |

**Constants:** `pytestmark`

---

## tests/test_test_generation_venvs.py
**Lines:** 465

### `TestSanitizePath`

| Method | Async | Args |
|--------|-------|------|
| `test_happy_path` |  | self, venvs, project |
| `test_empty_path_rejected` |  | self, venvs, project |
| `test_traversal_rejected` |  | self, venvs, project |
| `test_symlink_escape_rejected` |  | self, venvs, project, tmp_path, monkeypatch |

### Module Functions

| Function | Async | Args |
|----------|-------|------|
| `_import_venvs` |  |  |
| `_isolate_env` |  | monkeypatch |
| `venvs` |  | monkeypatch |
| `project` |  | tmp_path |
| `test_retry_and_jitter_bounds` | ✓ | monkeypatch, venvs, project |
| `test_cancel_during_backoff_sleep` | ✓ | monkeypatch, venvs, project |
| `test_persist_vs_cleanup` | ✓ | monkeypatch, venvs, project |
| `test_keep_on_failure_prevents_cleanup` | ✓ | monkeypatch, venvs, project |
| `test_timeout_and_dependency_sanitation` | ✓ | monkeypatch, venvs, project |
| `test_cfg_parsers` |  | monkeypatch, venvs |
| `test_cfg_parsers_with_bad_inputs` |  | monkeypatch, venvs |
| `test_venv_async` | ✓ | venvs, project |
| `_coerce_exec_path` |  | python_exec |
| `run_pytest_and_coverage` | ✓ | target_path, python_exec |

---
