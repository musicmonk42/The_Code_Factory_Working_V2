# ARBITER MODULE - COMPLETE API REFERENCE

**Total Files:** 106
**Total Classes:** 410
**Total Functions:** 284
**Total Methods:** 1665

---

## __init__.py

**Lines:** 179
**Description:** Arbiter package - Core components for the Self-Fixing Engineer platform.

This package provides:
- Arbiter: Main orchestrator for self-fixing engineering workflows
- ArbiterArena: Multi-agent collabor...

### Constants

| Name |
|------|
| `PYTEST_COLLECTING` |
| `arbiter` |
| `Arbiter` |
| `ArbiterArena` |
| `FeedbackManager` |
| `ArbiterConfig` |
| `_components_loaded` |
| `_LAZY_COMPONENT_NAMES` |
| `__version__` |
| `__all__` |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `_load_components` |  | `` | `-` | - |
| `_get_human_loop` |  | `` | `-` | - |
| `_get_human_loop_config` |  | `` | `-` | - |
| `get_component_status` |  | `` | `-` | - |
| `__getattr__` |  | `name` | `-` | - |

---

## agent_state.py

**Lines:** 563
**Description:** SQLAlchemy models for persisting agent state and metadata in the Arbiter system.

This implementation balances production requirements with testability:
- Works in both test and production environment...

### Constants

| Name |
|------|
| `logger` |
| `tracer` |
| `Base` |
| `SCHEMA_VALIDATION_ERRORS` |

### Class: `AgentState`
**Inherits:** Base
**Description:** Agent state model with comprehensive validation for regulated environments.

Maintains strict data integrity through multi-layer validation:
- Field-level validation via @validates decorators
- Schema validation for complex JSON structures
- Database constraints for data consistency
- Comprehensive ...

**Class Variables:** `__tablename__`, `__table_args__`, `id`, `name`, `x`, `y`, `energy`, `inventory`, `language`, `memory`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__repr__` |  | `self` | `str` | - |
| `_parse_json_field` |  | `self, field_value` | `-` | - |
| `_validate_inventory` |  | `self, inventory` | `-` | - |
| `_validate_language` |  | `self, language` | `-` | - |
| `_validate_memory` |  | `self, memory` | `-` | - |
| `_validate_personality` |  | `self, personality` | `-` | - |
| `validate_inventory` |  | `self, key, value` | `-` | @validates('inventory') |
| `validate_language` |  | `self, key, value` | `-` | @validates('language') |
| `validate_memory` |  | `self, key, value` | `-` | @validates('memory') |
| `validate_personality` |  | `self, key, value` | `-` | @validates('personality') |
| `validate_energy` |  | `self, key, value` | `-` | @validates('energy') |
| `validate_world_size` |  | `self, key, value` | `-` | @validates('world_size') |
| `_validate_json_fields_sync` |  | `mapper, connection, target` | `-` | @staticmethod |
| `_validate_json_fields` | ✓ | `mapper, connection, target` | `-` | @staticmethod |
| `_validate_fields_sync` |  | `target` | `-` | @staticmethod |
| `_validate_fields` | ✓ | `target` | `-` | @staticmethod |

### Class: `AgentMetadata`
**Inherits:** Base
**Description:** Key-value metadata storage for agents with comprehensive validation.

Provides flexible metadata storage while maintaining data integrity
through schema validation and comprehensive error tracking.

**Class Variables:** `__tablename__`, `__table_args__`, `id`, `key`, `value`, `created_at`, `updated_at`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__repr__` |  | `self` | `str` | - |
| `_parse_json_field` |  | `self, field_value` | `-` | - |
| `_validate_value` |  | `self, value` | `-` | - |
| `validate_value` |  | `self, key, value` | `-` | @validates('value') |
| `_validate_json_fields_sync` |  | `mapper, connection, target` | `-` | @staticmethod |
| `_validate_json_fields` | ✓ | `mapper, connection, target` | `-` | @staticmethod |
| `_validate_fields_sync` |  | `target` | `-` | @staticmethod |
| `_validate_fields` | ✓ | `target` | `-` | @staticmethod |

---

## arbiter.py

**Lines:** 3924

### Constants

| Name |
|------|
| `logger` |
| `_sentry_initialized` |
| `_metrics_initialized` |
| `event_counter` |
| `plugin_execution_time` |
| `_additional_metrics_initialized` |
| `action_counter` |
| `energy_gauge` |
| `memory_gauge` |
| `db_health_gauge` |
| `rl_reward_gauge` |
| `_plugins_registered` |

### Class: `MyArbiterConfig`
**Inherits:** BaseSettings
**Description:** Configuration for the Arbiter agent, loaded from environment variables or a .env file.

**Class Variables:** `DATABASE_URL`, `REDIS_URL`, `ENCRYPTION_KEY`, `REPORTS_DIRECTORY`, `FRONTEND_URL`, `ARENA_PORT`, `CODEBASE_PATHS`, `ENABLE_CRITICAL_FAILURES`, `AI_API_TIMEOUT`, `MEMORY_LIMIT`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `customise_sources` |  | `cls, init_settings, env_settings, file_secret_sett` | `-` | @classmethod |
| `ensure_https_in_prod` |  | `cls, v` | `-` | @field_validator('OMNICORE_URL |
| `validate_api_key` |  | `cls, v` | `-` | @field_validator('ALPHA_VANTAG |
| `handle_none_or_empty` |  | `cls, v` | `-` | @field_validator('SLACK_WEBHOO |

### Class: `AuditLogModel`
**Inherits:** Base

**Class Variables:** `__tablename__`, `__table_args__`, `id`, `agent_name`, `action`, `timestamp`, `details`

### Class: `ErrorLogModel`
**Inherits:** Base

**Class Variables:** `__tablename__`, `__table_args__`, `id`, `agent_name`, `timestamp`, `error_type`, `error_message`, `stack_trace`

### Class: `EventLogModel`
**Inherits:** Base

**Class Variables:** `__tablename__`, `__table_args__`, `id`, `agent_name`, `event_type`, `timestamp`, `description`

### Class: `Monitor`
**Description:** Logs and manages agent events persistently to file and database.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, log_file: str, db_client: 'PostgresClient'` | `-` | - |
| `log_action` | ✓ | `self, event: Dict[str, Any]` | `-` | - |
| `get_recent_events` |  | `self, limit: int` | `List[Dict[str, Any]]` | - |
| `generate_reports` |  | `self` | `Dict[str, Any]` | - |

### Class: `Explorer`
**Description:** A web crawler that uses aiohttp for real web crawling and exploration.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, sandbox_env` | `-` | - |
| `execute` | ✓ | `self, action: str` | `-` | - |
| `get_status` | ✓ | `self` | `-` | @retry(stop=stop_after_attempt |
| `discover_urls` | ✓ | `self, html_discovery_dir: str` | `-` | - |
| `crawl_urls` | ✓ | `self, urls: List[str]` | `-` | - |
| `explore_and_fix` | ✓ | `self, arbiter, fix_paths: Optional[List[str]]` | `-` | - |
| `close` | ✓ | `self` | `-` | - |

### Class: `IntentCaptureEngine`
**Description:** Generates reports based on agent data and metrics.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `generate_report` | ✓ | `self, agent_name: str` | `-` | - |

### Class: `AuditLogManager`
**Description:** Logs audit entries to the database.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, db_client: 'PostgresClient'` | `-` | - |
| `log_audit` | ✓ | `self, entry: Dict[str, Any]` | `-` | - |

### Class: `ExplainableReasoner`
**Inherits:** PluginBase
**Description:** A rule-based or lightweight language model-based reasoner.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self` | `-` | - |
| `initialize` | ✓ | `self` | `-` | - |
| `start` | ✓ | `self` | `-` | - |
| `stop` | ✓ | `self` | `-` | - |
| `get_capabilities` | ✓ | `self` | `List[str]` | - |
| `health_check` | ✓ | `self` | `bool` | - |
| `execute` | ✓ | `self, action: str` | `Dict[str, Any]` | - |

### Class: `ArbiterGrowthManager`
**Inherits:** PluginBase
**Description:** Manages skill acquisition and agent growth.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self` | `-` | - |
| `initialize` | ✓ | `self` | `-` | - |
| `start` | ✓ | `self` | `-` | - |
| `stop` | ✓ | `self` | `-` | - |
| `health_check` | ✓ | `self` | `bool` | - |
| `get_capabilities` | ✓ | `self` | `List[str]` | - |
| `acquire_skill` | ✓ | `self, skill_name: str, context: Dict[str, Any]` | `-` | - |

### Class: `BenchmarkingEngine`
**Description:** Performs performance benchmarks on given functions.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `execute` | ✓ | `self, action: str` | `-` | - |

### Class: `CompanyDataPlugin`
**Description:** Fetches company data from an external API (mocked with Alpha Vantage).

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, settings: MyArbiterConfig` | `-` | - |
| `execute` | ✓ | `self, ticker: str` | `-` | - |

### Class: `PermissionManager`
**Description:** Manages dynamic role-based permissions.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, config: MyArbiterConfig` | `-` | - |
| `check_permission` |  | `self, role: str, permission: str` | `-` | - |

### Class: `AgentStateManager`
**Description:** Manages the agent's state persistence and synchronization.
Includes encryption for sensitive data.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, db_client, name, settings` | `-` | - |
| `load_state` | ✓ | `self` | `-` | - |
| `save_state` | ✓ | `self` | `-` | @retry(stop=stop_after_attempt |
| `batch_save_state` | ✓ | `self` | `-` | - |
| `process_state_queue` | ✓ | `self` | `-` | @retry(stop=stop_after_attempt |
| `_initialize_default_state_in_memory` |  | `self` | `-` | - |

### Class: `Arbiter`
**Description:** The core Arbiter agent, responsible for observing, planning, and executing actions.
It integrates with various plugins and services to perform its tasks.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, name: str, db_engine: AsyncEngine, settings:` | `-` | - |
| `orchestrate` | ✓ | `self, task: dict` | `dict` | - |
| `health_check` | ✓ | `self` | `dict` | - |
| `register_plugin` | ✓ | `self, kind: str, name: str, plugin: Any` | `None` | - |
| `publish_to_omnicore` | ✓ | `self, event_type: str, data: dict` | `-` | - |
| `run_test_generation` | ✓ | `self, code: str, language: str, config: dict` | `-` | @retry(stop=stop_after_attempt |
| `run_test_generation_in_process` | ✓ | `self, code: str, language: str, config: dict` | `-` | - |
| `is_alive` |  | `self` | `bool` | @property |
| `log_event` |  | `self, event_description: str, event_type: str` | `-` | - |
| `evolve` | ✓ | `self, arena: Any` | `Dict[str, Any]` | - |
| `choose_action_from_policy` |  | `self, observation` | `-` | - |
| `observe_environment` | ✓ | `self, arena: Any` | `Dict[str, Any]` | - |
| `plan_decision` | ✓ | `self, observation: Dict[str, Any]` | `Dict[str, Any]` | - |
| `_build_observation` |  | `self, obs_dict: Dict[str, Any]` | `np.ndarray` | - |
| `execute_action` | ✓ | `self, decision: Dict[str, Any]` | `Dict[str, Any]` | @require_permission('execute_b |
| `reflect` | ✓ | `self` | `str` | - |
| `answer_why` | ✓ | `self, query: str` | `str` | - |
| `log_social_event` | ✓ | `self, event: str, with_whom: str, round_n: int` | `-` | - |
| `sync_with_explorer` | ✓ | `self, explorer_knowledge: Dict[str, Any]` | `-` | - |
| `start_async_services` | ✓ | `self` | `-` | - |
| `work_cycle` | ✓ | `self` | `Dict[str, Any]` | - |
| `explore_and_fix` | ✓ | `self, fix_paths: Optional[List[str]]` | `Dict[str, Any]` | - |
| `learn_from_data` | ✓ | `self` | `Dict[str, Any]` | - |
| `auto_optimize` | ✓ | `self` | `Dict[str, Any]` | - |
| `report_findings` | ✓ | `self` | `Dict[str, Any]` | - |
| `self_debug` | ✓ | `self` | `Dict[str, Any]` | - |
| `suggest_feature` | ✓ | `self` | `Dict[str, Any]` | - |
| `filter_companies` | ✓ | `self, preferences: Dict[str, Any]` | `Dict[str, Any]` | @require_permission('read') |
| `stop_async_services` | ✓ | `self` | `-` | - |
| `get_status` | ✓ | `self` | `Dict[str, Any]` | - |
| `run_benchmark` | ✓ | `self` | `-` | - |
| `explain` | ✓ | `self` | `-` | - |
| `push_metrics` | ✓ | `self` | `-` | - |
| `alert_critical_issue` | ✓ | `self, issue: str` | `-` | @retry(stop=stop_after_attempt |
| `coordinate_with_peers` | ✓ | `self, message: Dict[str, Any]` | `-` | - |
| `listen_for_peers` | ✓ | `self` | `-` | - |
| `setup_event_receiver` | ✓ | `self` | `-` | - |
| `_handle_incoming_event_http` | ✓ | `self, request` | `-` | - |
| `_handle_incoming_event` | ✓ | `self, event_type: str, data: Dict[str, Any]` | `-` | - |
| `_sanitize_event_data` |  | `self, data: Dict[str, Any]` | `Dict[str, Any]` | - |
| `_on_bug_detected` | ✓ | `self, data: Dict[str, Any]` | `-` | - |
| `_on_policy_violation` | ✓ | `self, data: Dict[str, Any]` | `-` | - |
| `_on_analysis_complete` | ✓ | `self, data: Dict[str, Any]` | `-` | - |
| `_on_generator_output` | ✓ | `self, data: Dict[str, Any]` | `-` | - |
| `_on_test_results` | ✓ | `self, data: Dict[str, Any]` | `-` | - |
| `_calculate_failure_priority` |  | `self, failure: Dict[str, Any]` | `float` | - |
| `_on_workflow_completed` | ✓ | `self, data: Dict[str, Any]` | `-` | - |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `_init_sentry` |  | `` | `-` | - |
| `_get_plugin_registry` |  | `` | `-` | - |
| `_init_metrics` |  | `` | `-` | - |
| `_init_additional_metrics` |  | `` | `-` | - |
| `require_permission` |  | `permission: str` | `-` | - |
| `save_rl_model` |  | `model: PPO, path: str` | `-` | - |
| `load_rl_model` |  | `path: str, env` | `PPO` | - |
| `_register_default_plugins` |  | `` | `-` | - |
| `main` |  | `` | `-` | - |

---

## arbiter_array_backend.py

**Lines:** 1107
**Description:** Array backend for the Arbiter/SFE platform.

- Type-safe array operations (append, get, update, delete, query)
- Thread-safe with concurrent access handling
- Persistent storage with JSON, SQLite, or ...

### Constants

| Name |
|------|
| `logger` |
| `tracer` |
| `_metrics_lock` |
| `VALID_METRIC_TYPES` |
| `array_ops_total` |
| `array_op_time` |
| `array_size` |
| `array_errors_total` |

### Class: `ArrayBackendError`
**Inherits:** Exception
**Description:** Base exception for ArrayBackend errors.

### Class: `StorageError`
**Inherits:** ArrayBackendError
**Description:** Raised for storage-related errors.

### Class: `ArraySizeLimitError`
**Inherits:** ArrayBackendError
**Description:** Raised when the array exceeds the maximum size.

### Class: `ArrayMeta`
**Decorators:** @dataclass
**Description:** Metadata for the array.

**Class Variables:** `name`, `created_at`, `modified_at`, `size_limit`, `encryption_enabled`

### Class: `ArrayBackend`
**Inherits:** PluginBase
**Description:** Abstract base class for array backend implementations.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `initialize` | ✓ | `self` | `None` | @abstractmethod |
| `append` | ✓ | `self, item: Any` | `None` | @abstractmethod |
| `get` | ✓ | `self, index: Optional[int]` | `Union[Any, List[Any]]` | @abstractmethod |
| `update` | ✓ | `self, index: int, item: Any` | `None` | @abstractmethod |
| `delete` | ✓ | `self, index: Optional[int]` | `None` | @abstractmethod |
| `query` | ✓ | `self, condition: Callable[[Any], bool]` | `List[Any]` | @abstractmethod |
| `meta` |  | `self` | `ArrayMeta` | @abstractmethod |
| `health_check` | ✓ | `self` | `Dict[str, Any]` | @abstractmethod |
| `rotate_encryption_key` | ✓ | `self, new_key: bytes` | `None` | @abstractmethod |

### Class: `PermissionManager`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, config` | `-` | - |
| `check_permission` |  | `self, role, permission` | `-` | - |

### Class: `ConcreteArrayBackend`
**Inherits:** ArrayBackend
**Description:** Concrete implementation of the array backend with JSON, SQLite, Redis, or PostgreSQL storage.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, name: str, storage_path: str, storage_type: ` | `-` | - |
| `__del__` |  | `self` | `-` | - |
| `__aenter__` | ✓ | `self` | `-` | - |
| `__aexit__` | ✓ | `self, exc_type, exc_val, exc_tb` | `-` | - |
| `initialize` | ✓ | `self` | `None` | @retry(stop=stop_after_attempt |
| `close` | ✓ | `self` | `None` | - |
| `array` |  | `self, data: Any` | `Any` | - |
| `asnumpy` |  | `self, data: Any` | `np.ndarray` | - |
| `_save_to_storage` | ✓ | `self` | `None` | @retry(stop=stop_after_attempt |
| `_load_from_storage` | ✓ | `self` | `None` | @retry(stop=stop_after_attempt |
| `append` | ✓ | `self, item: Any` | `None` | - |
| `get` | ✓ | `self, index: Optional[int]` | `Union[Any, List[Any]]` | - |
| `update` | ✓ | `self, index: int, item: Any` | `None` | - |
| `delete` | ✓ | `self, index: Optional[int]` | `None` | - |
| `query` | ✓ | `self, condition: Callable[[Any], bool]` | `List[Any]` | - |
| `meta` |  | `self` | `ArrayMeta` | - |
| `rotate_encryption_key` | ✓ | `self, new_key: bytes` | `None` | - |
| `_load_raw_from_storage` | ✓ | `self` | `List[str]` | - |
| `health_check` | ✓ | `self` | `Dict[str, Any]` | - |
| `on_reload` |  | `self` | `None` | - |
| `start` | ✓ | `self` | `None` | - |
| `stop` |  | `self` | `None` | - |
| `get_capabilities` |  | `self` | `Dict[str, Any]` | - |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `get_or_create_metric` |  | `metric_cls, name, desc, labelnames, buckets` | `-` | - |

---

## arbiter_constitution.py

**Lines:** 288

### Constants

| Name |
|------|
| `ARB_CONSTITUTION` |
| `logger` |

### Class: `ConstitutionViolation`
**Inherits:** Exception
**Description:** Exception raised when an action violates the Arbiter Constitution.

This exception should be raised when the enforce() method determines
that an action is not permitted by constitutional rules.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, message: str, violated_principle: str` | `-` | - |
| `__str__` |  | `self` | `-` | - |

### Class: `ArbiterConstitution`
**Description:** Represents the foundational rules and ethical guidelines for an Arbiter agent.
This constitution defines the core purpose, capabilities, and behavioral principles
of an Arbiter within the Legal Tender platform.

This class is designed to be immutable and thread-safe. The constitution's
text and pars...

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self` | `-` | - |
| `_parse_constitution` |  | `self, text: str` | `Dict[str, Any]` | - |
| `get_purpose` |  | `self` | `List[str]` | - |
| `get_powers` |  | `self` | `List[str]` | - |
| `get_principles` |  | `self` | `List[str]` | - |
| `get_evolution` |  | `self` | `List[str]` | - |
| `get_aim` |  | `self` | `List[str]` | - |
| `check_action` | ✓ | `self, action: str, context: Dict[str, Any]` | `Tuple[bool, str]` | - |
| `enforce` | ✓ | `self, action: str, context: Dict[str, Any]` | `None` | - |
| `__str__` |  | `self` | `str` | - |
| `__repr__` |  | `self` | `str` | - |

---

## arbiter_growth/arbiter_growth_manager.py

**Lines:** 789

### Constants

| Name |
|------|
| `logger` |
| `tracer` |

### Class: `HealthStatus`
**Inherits:** Enum
**Description:** Health status states for the arbiter manager.

**Class Variables:** `INITIALIZING`, `HEALTHY`, `DEGRADED`, `STOPPED`, `ERROR`

### Class: `PluginHook`
**Inherits:** Protocol
**Description:** A protocol for plugins to hook into the growth event lifecycle.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `on_growth_event` | ✓ | `self, event: GrowthEvent, state: ArbiterState` | `None` | - |

### Class: `CircuitBreakerListener`
**Description:** Listener for circuit breaker state changes.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, arbiter_name: str` | `-` | - |
| `before_call` |  | `self, cb, func` | `-` | - |
| `success` |  | `self, cb` | `-` | - |
| `failure` |  | `self, cb, exc` | `-` | - |
| `state_change` |  | `self, cb, old_state, new_state` | `-` | - |

### Class: `Neo4jKnowledgeGraph`
**Description:** A concrete implementation for interacting with a Neo4j Knowledge Graph.
NOTE: This is a simplified example. A real implementation would use the
official neo4j-driver and handle connection pooling, transactions, and errors.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, config_store: ConfigStore` | `-` | - |
| `add_fact` | ✓ | `self, arbiter_id: str, event_type: str, event_deta` | `None` | - |

### Class: `LoggingFeedbackManager`
**Description:** A concrete implementation that logs feedback events.
In a real system, this might write to a database, a message queue,
or a dedicated feedback analysis service.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, config_store: ConfigStore` | `-` | - |
| `record_feedback` | ✓ | `self, arbiter_id: str, event_type: str, event_deta` | `None` | - |

### Class: `ContextAwareCallable`
**Description:** Wraps an async callable to capture and restore OpenTelemetry context.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, coro: Callable[[], Awaitable[None]], context` | `-` | - |
| `__call__` | ✓ | `self` | `-` | - |

### Class: `ArbiterGrowthManager`
**Description:** Manages the state, evolution, and event processing for a single arbiter.
This class is the core logic engine, orchestrating storage, business logic,
and integrations with external systems like knowledge graphs.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, arbiter_name: str, storage_backend: StorageB` | `-` | - |
| `_add_breaker_listeners` |  | `self` | `-` | - |
| `start` | ✓ | `self` | `None` | - |
| `stop` | ✓ | `self` | `None` | - |
| `_process_pending_operations` | ✓ | `self` | `-` | - |
| `_periodic_flush` | ✓ | `self` | `None` | - |
| `_periodic_evolution_cycle` | ✓ | `self` | `None` | - |
| `_run_evolution_cycle` | ✓ | `self` | `None` | - |
| `_validate_audit_chain` | ✓ | `self` | `None` | - |
| `_recalculate_log_hash` |  | `self, log: Dict[str, Any]` | `str` | - |
| `_load_state_and_replay_events` | ✓ | `self` | `None` | - |
| `_apply_event` | ✓ | `self, event: GrowthEvent, is_replay: bool` | `None` | - |
| `_is_event_valid` |  | `self, event: GrowthEvent` | `bool` | - |
| `_save_if_dirty` | ✓ | `self, force: bool` | `None` | - |
| `_save_snapshot_to_db` | ✓ | `self` | `None` | @tracer.start_as_current_span( |
| `_audit_log` | ✓ | `self, operation: str, details: Dict[str, Any]` | `None` | - |
| `_generate_idempotency_key` |  | `self, event: GrowthEvent, service_name: str` | `str` | - |
| `register_hook` |  | `self, hook: PluginHook, stage: str` | `None` | - |
| `_push_events` | ✓ | `self, events: List[GrowthEvent]` | `None` | - |
| `_queue_operation` | ✓ | `self, operation_coro: Callable[[], Awaitable[None]` | `None` | - |
| `record_growth_event` | ✓ | `self, event_type: str, details: Dict[str, Any]` | `None` | - |
| `improve_skill` | ✓ | `self, skill_name: str, improvement_amount: float` | `None` | - |
| `level_up` | ✓ | `self` | `None` | - |
| `get_health_status` | ✓ | `self` | `Dict[str, Any]` | - |
| `liveness_probe` |  | `self` | `bool` | - |
| `readiness_probe` | ✓ | `self` | `bool` | - |

---

## arbiter_growth/config_store.py

**Lines:** 437

### Constants

| Name |
|------|
| `logger` |

### Class: `ConfigStore`
**Description:** Manages configuration settings with a primary source (etcd), a local file
fallback, and in-memory caching with TTL for performance.

The configuration lookup follows this order:
1. In-memory cache (if the value is not expired).
2. etcd distributed key-value store (with retries).
3. Local JSON fallba...

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, etcd_host: str, etcd_port: int, fallback_pat` | `-` | - |
| `get` |  | `self, key, default` | `-` | - |
| `start_watcher` | ✓ | `self` | `-` | - |
| `stop_watcher` | ✓ | `self` | `-` | - |
| `_watch_for_changes` | ✓ | `self` | `-` | - |
| `_watch_etcd_updates` | ✓ | `self` | `-` | - |
| `_is_cache_valid` |  | `self, key: str` | `bool` | - |
| `_load_from_fallback` | ✓ | `self` | `None` | - |
| `_parse_value` |  | `self, value_str: str` | `Any` | - |
| `_get_from_etcd_with_retry` | ✓ | `self, key: str` | `Optional[Any]` | - |
| `_get_from_etcd` | ✓ | `self, key: str` | `Optional[Any]` | - |
| `get_config` | ✓ | `self, key: str, default: Optional[Any]` | `Any` | - |
| `get_all` |  | `self` | `Dict[str, Any]` | - |

### Class: `TokenBucketRateLimiter`
**Description:** Implements a token bucket rate limiter with blocking capability.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, config_store: ConfigStore` | `-` | - |
| `acquire` | ✓ | `self, timeout: Optional[float]` | `bool` | - |

---

## arbiter_growth/exceptions.py

**Lines:** 119

### Constants

| Name |
|------|
| `logger` |

### Class: `ArbiterGrowthError`
**Inherits:** Exception
**Description:** Base exception for all errors originating from the ArbiterGrowthManager.

This exception is not typically raised directly but serves as a parent class
for more specific exceptions, allowing for consolidated error handling.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, message: str, details: Optional[Dict[str, An` | `-` | - |
| `__str__` |  | `self` | `str` | - |

### Class: `OperationQueueFullError`
**Inherits:** ArbiterGrowthError
**Description:** Raised when an operation cannot be added because the pending operations queue is full.

This indicates that the system is currently processing at its maximum configured
capacity and cannot accept new work until some existing operations complete.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, message: str, details: Optional[Dict[str, An` | `-` | - |

### Class: `RateLimitError`
**Inherits:** ArbiterGrowthError
**Description:** Raised when an operation is rejected due to rate limiting.

This error occurs when the number of operations exceeds a predefined threshold
within a specific time window, protecting the system from being overwhelmed.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, message: str, details: Optional[Dict[str, An` | `-` | - |

### Class: `CircuitBreakerOpenError`
**Inherits:** ArbiterGrowthError
**Description:** Raised when an operation fails because the circuit breaker is in the 'open' state.

This signifies that a downstream service or a critical component has been
experiencing repeated failures, and the circuit breaker has tripped to prevent
further requests, allowing the failing component time to recove...

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, message: str, details: Optional[Dict[str, An` | `-` | - |

### Class: `AuditChainTamperedError`
**Inherits:** ArbiterGrowthError
**Description:** Raised when a validation check of the audit log's hash chain fails.

This is a critical security exception, indicating that the integrity of the
audit log may have been compromised and the log has been tampered with.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, message: str, details: Optional[Dict[str, An` | `-` | - |

---

## arbiter_growth/idempotency.py

**Lines:** 220

### Constants

| Name |
|------|
| `logger` |
| `tracer` |
| `IDEMPOTENCY_HITS_TOTAL` |

### Class: `IdempotencyStoreError`
**Inherits:** Exception
**Description:** Custom exception for IdempotencyStore errors.

### Class: `IdempotencyStore`
**Description:** Manages idempotency keys for exactly-once processing using Redis.

This store provides a mechanism to check for and set idempotency keys to prevent
duplicate processing of requests or messages. It is designed to be resilient,
configurable, and observable.

It supports standard Redis connections, SSL...

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self` | `-` | - |
| `check_and_set` | ✓ | `self, key: str, ttl: Optional[int]` | `bool` | - |
| `start` | ✓ | `self` | `-` | @retry(stop=stop_after_attempt |
| `stop` | ✓ | `self` | `-` | - |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `_get_or_create_metric` |  | `metric_class: type, name: str, doc: str, labelname` | `-` | - |

---

## arbiter_growth/metrics.py

**Lines:** 205

### Constants

| Name |
|------|
| `logger` |
| `GROWTH_EVENTS` |
| `GROWTH_SAVE_ERRORS` |
| `GROWTH_PENDING_QUEUE` |
| `GROWTH_SKILL_IMPROVEMENT` |
| `GROWTH_SNAPSHOTS` |
| `GROWTH_CIRCUIT_BREAKER_TRIPS` |
| `GROWTH_ANOMALY_SCORE` |
| `GROWTH_EVENT_PUSH_LATENCY` |
| `GROWTH_OPERATION_QUEUE_LATENCY` |
| `GROWTH_OPERATION_EXECUTION_LATENCY` |
| `STORAGE_LATENCY_SECONDS` |
| `AUDIT_VALIDATION_ERRORS_TOTAL` |
| `GROWTH_AUDIT_ANCHORS_TOTAL` |
| `IDEMPOTENCY_HITS_TOTAL` |
| `RATE_LIMIT_REJECTIONS_TOTAL` |
| `CONFIG_FALLBACK_USED` |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `get_or_create_metric` |  | `metric_class: Type[Union[Counter, Gauge, Histogram` | `Union[Counter, Gauge, Histogra` | - |

---

## arbiter_growth/models.py

**Lines:** 263

### Class: `GrowthEvent`
**Inherits:** BaseModel
**Description:** Represents a single, atomic event in an arbiter's growth lifecycle.
This model is used to validate incoming event data before it is processed.

**Class Variables:** `type`, `timestamp`, `details`, `event_version`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `type_must_not_be_whitespace` |  | `cls, v: str` | `str` | @field_validator('type'), @cla |
| `validate_timestamp` |  | `cls, v: str` | `str` | @field_validator('timestamp'), |

### Class: `ArbiterState`
**Inherits:** BaseModel
**Description:** Represents the complete, in-memory state of an arbiter at a point in time.
This model serves as the single source of truth for an arbiter's current
attributes and is what gets snapshotted for persistence.

**Class Variables:** `arbiter_id`, `level`, `skills`, `user_preferences`, `event_offset`, `schema_version`, `experience_points`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `convert_event_offset` |  | `cls, v: Union[int, str]` | `int` | @field_validator('event_offset |
| `validate_skill_scores` |  | `cls, v: Dict[str, float]` | `Dict[str, float]` | @field_validator('skills'), @c |
| `set_skill_score` |  | `self, skill_name: str, score: float` | `-` | - |
| `model_dump` |  | `self` | `Dict[str, Any]` | - |

### Class: `GrowthSnapshot`
**Inherits:** Base
**Description:** Database model for storing a serialized, persistent snapshot of an
arbiter's state. This table allows for quick state restoration without
replaying the entire event history.

**Class Variables:** `__tablename__`, `arbiter_id`, `level`, `skills_encrypted`, `user_preferences_encrypted`, `experience_points`, `schema_version`, `event_offset`, `timestamp`, `__table_args__`

### Class: `GrowthEventRecord`
**Inherits:** Base
**Description:** Database model for storing the immutable log of all growth events.
This serves as the ultimate source of truth for an arbiter's history.

**Class Variables:** `__tablename__`, `id`, `arbiter_id`, `event_type`, `timestamp`, `details_encrypted`, `event_version`, `__table_args__`

### Class: `AuditLog`
**Inherits:** Base
**Description:** Database model for a chained audit log to ensure the integrity and
non-repudiation of all operations performed on an arbiter.

**Class Variables:** `__tablename__`, `id`, `arbiter_id`, `operation`, `timestamp`, `details`, `previous_log_hash`, `log_hash`, `__table_args__`

---

## arbiter_growth/plugins.py

**Lines:** 113

### Class: `PluginHook`
**Inherits:** ABC
**Description:** Defines the interface for plugins that can hook into the lifecycle
of the ArbiterGrowthManager.

Plugins allow for extending the core functionality of the manager
without modifying its code. This is useful for custom logging, metrics,
notifications, or triggering external workflows.

To create a plu...

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `on_start` | ✓ | `self, arbiter_name: str` | `None` | - |
| `on_stop` | ✓ | `self, arbiter_name: str` | `None` | - |
| `on_error` | ✓ | `self, arbiter_name: str, error: 'ArbiterGrowthErro` | `None` | - |
| `on_growth_event` | ✓ | `self, event: 'GrowthEvent', state: 'ArbiterState'` | `None` | @abstractmethod |

---

## arbiter_growth/storage_backends.py

**Lines:** 988

### Constants

| Name |
|------|
| `logger` |
| `tracer` |
| `CACHE_TIMEOUT_SECONDS` |
| `STORAGE_LATENCY_SECONDS` |
| `REDIS_BREAKER` |
| `KAFKA_BREAKER` |
| `SQL_BREAKER` |

### Class: `StorageBackend`
**Inherits:** Protocol
**Description:** Defines the interface for all storage backend implementations.

This protocol ensures that any class acting as a storage backend will have a
consistent set of methods for saving, loading, and managing data.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `start` | ✓ | `self` | `None` | - |
| `stop` | ✓ | `self` | `None` | - |
| `__aenter__` | ✓ | `self` | `-` | - |
| `__aexit__` | ✓ | `self, exc_type, exc_val, exc_tb` | `-` | - |
| `load_snapshot` | ✓ | `self, arbiter_id: str` | `Optional[Dict[str, Any]]` | - |
| `save_snapshot` | ✓ | `self, arbiter_id: str, data: Dict[str, Any]` | `None` | - |
| `save_event` | ✓ | `self, arbiter_id: str, event: Dict[str, Any]` | `None` | - |
| `load_events` | ✓ | `self, arbiter_id: str, from_offset: Union[int, str` | `List[Dict[str, Any]]` | - |
| `save_audit_log` | ✓ | `self, arbiter_id: str, operation: str, details: Di` | `str` | - |
| `get_last_audit_hash` | ✓ | `self, arbiter_id: str` | `str` | - |
| `load_all_audit_logs` | ✓ | `self, arbiter_id: str` | `List[Dict[str, Any]]` | - |

### Class: `SQLiteStorageBackend`
**Description:** A storage backend using SQLite for persistent storage.
Suitable for single-node deployments and development.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, config: ConfigStore` | `-` | - |
| `_get_session` | ✓ | `self` | `AsyncSession` | @asynccontextmanager |
| `start` | ✓ | `self` | `-` | @_wrap_exception('SQLite') |
| `stop` | ✓ | `self` | `-` | @_wrap_exception('SQLite') |
| `__aenter__` | ✓ | `self` | `-` | - |
| `__aexit__` | ✓ | `self, exc_type, exc_val, exc_tb` | `-` | - |
| `load_snapshot` | ✓ | `self, arbiter_id: str` | `Optional[Dict[str, Any]]` | @STORAGE_LATENCY_SECONDS.label |
| `save_snapshot` | ✓ | `self, arbiter_id: str, data: Dict[str, Any]` | `None` | @STORAGE_LATENCY_SECONDS.label |
| `save_event` | ✓ | `self, arbiter_id: str, event: Dict[str, Any]` | `None` | @STORAGE_LATENCY_SECONDS.label |
| `load_events` | ✓ | `self, arbiter_id: str, from_offset: Union[int, str` | `List[Dict[str, Any]]` | @STORAGE_LATENCY_SECONDS.label |
| `save_audit_log` | ✓ | `self, arbiter_id: str, operation: str, details: Di` | `str` | @STORAGE_LATENCY_SECONDS.label |
| `get_last_audit_hash` | ✓ | `self, arbiter_id: str` | `str` | @STORAGE_LATENCY_SECONDS.label |
| `load_all_audit_logs` | ✓ | `self, arbiter_id: str` | `List[Dict[str, Any]]` | @STORAGE_LATENCY_SECONDS.label |

### Class: `RedisStreamsStorageBackend`
**Description:** A storage backend using Redis Streams for event sourcing.
Snapshots and audit logs are stored in Redis Hashes and Lists respectively.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, config: ConfigStore` | `-` | - |
| `_key` |  | `self, arbiter_id: str, key_type: str` | `str` | - |
| `start` | ✓ | `self` | `-` | @_wrap_exception('Redis') |
| `stop` | ✓ | `self` | `-` | @_wrap_exception('Redis') |
| `__aenter__` | ✓ | `self` | `-` | - |
| `__aexit__` | ✓ | `self, exc_type, exc_val, exc_tb` | `-` | - |
| `load_snapshot` | ✓ | `self, arbiter_id: str` | `Optional[Dict[str, Any]]` | @STORAGE_LATENCY_SECONDS.label |
| `save_snapshot` | ✓ | `self, arbiter_id: str, data: Dict[str, Any]` | `None` | @STORAGE_LATENCY_SECONDS.label |
| `save_event` | ✓ | `self, arbiter_id: str, event: Dict[str, Any]` | `None` | @STORAGE_LATENCY_SECONDS.label |
| `load_events` | ✓ | `self, arbiter_id: str, from_offset: Union[int, str` | `List[Dict[str, Any]]` | @STORAGE_LATENCY_SECONDS.label |
| `save_audit_log` | ✓ | `self, arbiter_id: str, operation: str, details: Di` | `str` | @STORAGE_LATENCY_SECONDS.label |
| `get_last_audit_hash` | ✓ | `self, arbiter_id: str` | `str` | @STORAGE_LATENCY_SECONDS.label |
| `load_all_audit_logs` | ✓ | `self, arbiter_id: str` | `List[Dict[str, Any]]` | @STORAGE_LATENCY_SECONDS.label |

### Class: `KafkaStorageBackend`
**Description:** A storage backend using Kafka for event sourcing and audit logging.
This backend is highly scalable but has performance trade-offs for
snapshot loading and retrieving the last audit hash.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, config: ConfigStore` | `-` | - |
| `_topic` |  | `self, arbiter_id: str, topic_type: str` | `str` | - |
| `start` | ✓ | `self` | `-` | @_wrap_exception('Kafka') |
| `stop` | ✓ | `self` | `-` | @_wrap_exception('Kafka') |
| `__aenter__` | ✓ | `self` | `-` | - |
| `__aexit__` | ✓ | `self, exc_type, exc_val, exc_tb` | `-` | - |
| `load_snapshot` | ✓ | `self, arbiter_id: str` | `Optional[Dict[str, Any]]` | @STORAGE_LATENCY_SECONDS.label |
| `save_snapshot` | ✓ | `self, arbiter_id: str, data: Dict[str, Any]` | `None` | @STORAGE_LATENCY_SECONDS.label |
| `save_event` | ✓ | `self, arbiter_id: str, event: Dict[str, Any]` | `None` | @STORAGE_LATENCY_SECONDS.label |
| `load_events` | ✓ | `self, arbiter_id: str, from_offset: Union[int, str` | `List[Dict[str, Any]]` | @STORAGE_LATENCY_SECONDS.label |
| `save_audit_log` | ✓ | `self, arbiter_id: str, operation: str, details: Di` | `str` | @STORAGE_LATENCY_SECONDS.label |
| `get_last_audit_hash` | ✓ | `self, arbiter_id: str` | `str` | @STORAGE_LATENCY_SECONDS.label |
| `load_all_audit_logs` | ✓ | `self, arbiter_id: str` | `List[Dict[str, Any]]` | @STORAGE_LATENCY_SECONDS.label |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `_get_encryption_key_from_env` |  | `` | `bytes` | - |
| `_create_hmac_hash` |  | `key: bytes` | `str` | - |
| `_wrap_exception` |  | `backend_name: str` | `-` | - |
| `_normalize_event_offset` |  | `offset: Union[int, str]` | `Union[int, str]` | - |
| `storage_backend_factory` |  | `config: ConfigStore` | `StorageBackend` | - |

---

## arbiter_growth.py

**Lines:** 2782

### Constants

| Name |
|------|
| `tracer` |
| `propagator` |
| `logger` |
| `VALID_METRIC_TYPES` |
| `GROWTH_EVENTS` |
| `GROWTH_SAVE_ERRORS` |
| `GROWTH_PENDING_QUEUE` |
| `GROWTH_SKILL_IMPROVEMENT` |
| `GROWTH_SNAPSHOTS` |
| `GROWTH_EVENT_PUSH_LATENCY` |
| `GROWTH_OPERATION_QUEUE_LATENCY` |
| `GROWTH_OPERATION_EXECUTION_LATENCY` |
| `GROWTH_CIRCUIT_BREAKER_TRIPS` |
| `GROWTH_ANOMALY_SCORE` |
| `CONFIG_FALLBACK_USED` |
| `GROWTH_AUDIT_ANCHORS_TOTAL` |
| `GROWTH_ERRORS_TOTAL` |

### Class: `ArbiterGrowthError`
**Inherits:** Exception
**Description:** Base exception for the ArbiterGrowthManager.

### Class: `OperationQueueFullError`
**Inherits:** ArbiterGrowthError
**Description:** Raised when the pending operations queue is full.

### Class: `RateLimitError`
**Inherits:** ArbiterGrowthError
**Description:** Raised when an operation is rejected due to rate limiting.

### Class: `CircuitBreakerOpenError`
**Inherits:** ArbiterGrowthError
**Description:** Raised when an operation fails because the circuit breaker is open.

### Class: `AuditChainTamperedError`
**Inherits:** ArbiterGrowthError
**Description:** Raised when the audit log hash chain validation fails.

### Class: `ConfigStore`
**Description:** Manages configuration settings with etcd and a local fallback file.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, etcd_host: str, etcd_port: int, fallback_pat` | `-` | - |
| `_load_from_fallback` | ✓ | `self` | `None` | - |
| `get_config` | ✓ | `self, key: str` | `Any` | - |
| `ping` | ✓ | `self` | `Dict[str, Any]` | - |

### Class: `TokenBucketRateLimiter`
**Description:** Implements a token bucket rate limiter with blocking capability.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, config_store: ConfigStore` | `-` | - |
| `acquire` | ✓ | `self, timeout: Optional[float]` | `bool` | - |

### Class: `ContextAwareCallable`
**Description:** Wraps an async callable to capture and restore OpenTelemetry context.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, coro: Callable[[], Awaitable[None]], context` | `-` | - |
| `__call__` | ✓ | `self` | `-` | - |

### Class: `IdempotencyStore`
**Description:** Manages idempotency keys for exactly-once processing.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, redis_url: str` | `-` | - |
| `check_and_set` | ✓ | `self, key: str, ttl: int` | `bool` | - |
| `start` | ✓ | `self` | `-` | - |
| `ping` | ✓ | `self` | `Dict[str, Any]` | - |
| `stop` | ✓ | `self` | `-` | - |
| `remember` | ✓ | `self, key: str, ttl: int` | `None` | - |

### Class: `StorageBackend`
**Inherits:** Protocol

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `load` | ✓ | `self, arbiter_id: str` | `Optional[Dict[str, Any]]` | - |
| `save` | ✓ | `self, arbiter_id: str, data: Dict[str, Any]` | `None` | - |
| `save_event` | ✓ | `self, arbiter_id: str, event: Dict[str, Any]` | `None` | - |
| `load_events` | ✓ | `self, arbiter_id: str, from_offset: Union[int, str` | `List[Dict[str, Any]]` | - |
| `start` | ✓ | `self` | `None` | - |
| `stop` | ✓ | `self` | `None` | - |
| `ping` | ✓ | `self` | `Dict[str, Any]` | - |
| `save_audit_log` | ✓ | `self, arbiter_id: str, operation: str, details: Di` | `str` | - |
| `get_last_audit_hash` | ✓ | `self, arbiter_id: str` | `str` | - |
| `load_all_audit_logs` | ✓ | `self, arbiter_id: str` | `List[Dict[str, Any]]` | - |

### Class: `SQLiteStorageBackend`
**Description:** SQLite storage backend with encryption and audit logging.

Consistency: Provides strong, serializable consistency for all operations. Changes
are immediately visible upon transaction commit. Not suitable for high-concurrency
production workloads; use PostgreSQL or MySQL instead.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, session_factory: Callable[[], AsyncSession],` | `-` | - |
| `start` | ✓ | `self` | `-` | - |
| `stop` | ✓ | `self` | `-` | - |
| `ping` | ✓ | `self` | `Dict[str, Any]` | - |
| `load` | ✓ | `self, arbiter_id: str` | `Optional[Dict[str, Any]]` | @retry(stop=stop_after_attempt |
| `save` | ✓ | `self, arbiter_id: str, data: Dict[str, Any]` | `None` | @retry(stop=stop_after_attempt |
| `save_event` | ✓ | `self, arbiter_id: str, event: Dict[str, Any]` | `None` | @tracer.start_as_current_span( |
| `load_events` | ✓ | `self, arbiter_id: str, from_offset: Union[int, str` | `List[Dict[str, Any]]` | @tracer.start_as_current_span( |
| `save_audit_log` | ✓ | `self, arbiter_id: str, operation: str, details: Di` | `str` | @tracer.start_as_current_span( |
| `get_last_audit_hash` | ✓ | `self, arbiter_id: str` | `str` | @tracer.start_as_current_span( |
| `load_all_audit_logs` | ✓ | `self, arbiter_id: str` | `List[Dict[str, Any]]` | @tracer.start_as_current_span( |

### Class: `RedisStreamsStorageBackend`
**Description:** Redis Streams storage backend.

Consistency: Provides at-least-once semantics for event processing due to retries.
Snapshots are eventually consistent. Does not guarantee transactional atomicity between
saving an event and saving a snapshot.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, redis_url: str, encryption_key: Optional[byt` | `-` | - |
| `start` | ✓ | `self` | `-` | - |
| `stop` | ✓ | `self` | `-` | - |
| `ping` | ✓ | `self` | `Dict[str, Any]` | - |
| `_get_stream_key` | ✓ | `self, arbiter_id: str` | `str` | - |
| `_get_snapshot_key` | ✓ | `self, arbiter_id: str` | `str` | - |
| `_get_audit_key` | ✓ | `self, arbiter_id: str` | `str` | - |
| `load` | ✓ | `self, arbiter_id: str` | `Optional[Dict[str, Any]]` | @retry(stop=stop_after_attempt |
| `save` | ✓ | `self, arbiter_id: str, data: Dict[str, Any]` | `None` | @retry(stop=stop_after_attempt |
| `save_event` | ✓ | `self, arbiter_id: str, event: Dict[str, Any]` | `None` | @retry(stop=stop_after_attempt |
| `load_events` | ✓ | `self, arbiter_id: str, from_offset: Union[int, str` | `List[Dict[str, Any]]` | @retry(stop=stop_after_attempt |
| `save_audit_log` | ✓ | `self, arbiter_id: str, operation: str, details: Di` | `str` | @tracer.start_as_current_span( |
| `get_last_audit_hash` | ✓ | `self, arbiter_id: str` | `str` | @tracer.start_as_current_span( |
| `load_all_audit_logs` | ✓ | `self, arbiter_id: str` | `List[Dict[str, Any]]` | @tracer.start_as_current_span( |

### Class: `KafkaStorageBackend`
**Description:** Kafka storage backend for growth events.

Consistency: Provides at-least-once delivery for events when using retries.
Transactional sends provide atomicity for batches of messages per producer session,
ensuring that a batch is either fully written or not at all. Ordering is guaranteed
per partition ...

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, bootstrap_servers: str, schema_registry_url:` | `-` | - |
| `start` | ✓ | `self` | `-` | - |
| `stop` | ✓ | `self` | `-` | - |
| `ping` | ✓ | `self` | `Dict[str, Any]` | - |
| `load` | ✓ | `self, arbiter_id: str` | `Optional[Dict[str, Any]]` | - |
| `save` | ✓ | `self, arbiter_id: str, data: Dict[str, Any]` | `None` | - |
| `save_event` | ✓ | `self, arbiter_id: str, event: Dict[str, Any]` | `None` | @retry(stop=stop_after_attempt |
| `load_events` | ✓ | `self, arbiter_id: str, from_offset: Union[int, str` | `List[Dict[str, Any]]` | @retry(stop=stop_after_attempt |
| `save_audit_log` | ✓ | `self, arbiter_id: str, operation: str, details: Di` | `str` | @tracer.start_as_current_span( |
| `get_last_audit_hash` | ✓ | `self, arbiter_id: str` | `str` | @tracer.start_as_current_span( |
| `load_all_audit_logs` | ✓ | `self, arbiter_id: str` | `List[Dict[str, Any]]` | @tracer.start_as_current_span( |

### Class: `GrowthEvent`
**Inherits:** BaseModel

**Class Variables:** `type`, `timestamp`, `details`, `event_version`

### Class: `ArbiterState`
**Inherits:** BaseModel

**Class Variables:** `arbiter_id`, `level`, `skills`, `user_preferences`, `event_offset`, `schema_version`, `experience_points`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `set_skill_score` |  | `self, skill_name: str, score: float` | `-` | - |

### Class: `GrowthSnapshot`
**Inherits:** Base

**Class Variables:** `__tablename__`, `arbiter_id`, `level`, `skills_encrypted`, `user_preferences_encrypted`, `schema_version`, `event_offset`

### Class: `GrowthEventRecord`
**Inherits:** Base

**Class Variables:** `__tablename__`, `id`, `arbiter_id`, `event_type`, `timestamp`, `details_encrypted`, `event_version`

### Class: `AuditLog`
**Inherits:** Base

**Class Variables:** `__tablename__`, `id`, `arbiter_id`, `operation`, `timestamp`, `details`, `previous_log_hash`, `log_hash`

### Class: `KnowledgeGraph`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `add_fact` | ✓ | `self` | `-` | - |

### Class: `FeedbackManager`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `record_feedback` | ✓ | `self` | `-` | - |

### Class: `PluginHook`
**Inherits:** ABC

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `on_growth_event` | ✓ | `self, event: GrowthEvent, state: ArbiterState` | `None` | @abstractmethod |

### Class: `ArbiterGrowthManager`

**Class Variables:** `MAX_PENDING_OPERATIONS`, `SCHEMA_VERSION`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, arbiter_name: str, storage_backend: StorageB` | `-` | - |
| `_call_maybe_async` | ✓ | `fn: Callable[..., AnyType]` | `AnyType` | @staticmethod |
| `start` | ✓ | `self` | `None` | - |
| `_periodic_evolution_cycle` | ✓ | `self` | `None` | - |
| `_run_evolution_cycle` | ✓ | `self` | `None` | - |
| `_validate_audit_chain` | ✓ | `self` | `bool` | - |
| `anchor_audit_chain_periodically` | ✓ | `self, external_ledger_api: Callable[[str, str], Aw` | `-` | - |
| `_on_load_done` |  | `self, fut: asyncio.Future` | `None` | - |
| `_periodic_flush` | ✓ | `self` | `None` | - |
| `shutdown` | ✓ | `self` | `None` | - |
| `_load_state_and_replay_events` | ✓ | `self` | `None` | - |
| `_apply_event` | ✓ | `self, event: GrowthEvent` | `None` | - |
| `_save_snapshot_to_db` | ✓ | `self` | `None` | @tracer.start_as_current_span( |
| `__do_save_snapshot` | ✓ | `self` | `-` | - |
| `_save_if_dirty` | ✓ | `self, force: bool` | `None` | - |
| `_audit_log` | ✓ | `self, operation: str, details: Dict[str, Any]` | `None` | - |
| `_generate_idempotency_key` |  | `self, event: GrowthEvent, service_name: str` | `str` | - |
| `register_hook` |  | `self, hook: PluginHook, stage: str` | `None` | - |
| `_push_event` | ✓ | `self, event: GrowthEvent` | `None` | @tracer.start_as_current_span( |
| `__do_push_event` | ✓ | `self, event: GrowthEvent` | `-` | - |
| `_queue_operation` | ✓ | `self, operation_coro: Callable[[], Awaitable[None]` | `None` | - |
| `record_growth_event` | ✓ | `self, event_type: str, details: Dict[str, Any]` | `None` | - |
| `acquire_skill` | ✓ | `self, skill_name: str, initial_score: float, conte` | `None` | - |
| `improve_skill` | ✓ | `self, skill_name: str, improvement_amount: float, ` | `None` | - |
| `level_up` | ✓ | `self` | `None` | - |
| `gain_experience` | ✓ | `self, amount: float, context: Optional[Dict[str, A` | `None` | - |
| `update_user_preference` | ✓ | `self, key: str, value: Any, context: Optional[Dict` | `None` | - |
| `_record_event_now` | ✓ | `self, event: 'GrowthEvent'` | `None` | - |
| `get_growth_summary` | ✓ | `self` | `Dict[str, Any]` | - |
| `health` | ✓ | `self` | `Dict[str, Any]` | - |
| `liveness_probe` | ✓ | `self` | `bool` | - |
| `readiness_probe` | ✓ | `self` | `bool` | - |
| `rotate_encryption_key` | ✓ | `self, new_key: bytes` | `None` | - |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `get_or_create_metric` |  | `metric_class, name, documentation, labelnames, buc` | `-` | - |

---

## arbiter_plugin_registry.py

**Lines:** 1375
**Description:**  plugin registry for Arbiter/SFE platform.

- **Type-safe**: Enforces a strict `PluginBase` interface and runtime type checking.
- **Thread-safe**: Uses a global lock for mutation operations.
- **Metr...

### Constants

| Name |
|------|
| `logger` |
| `_IMPORT_IN_PROGRESS` |
| `logger` |
| `_registry_lock` |
| `_IMPORT_IN_PROGRESS` |

### Class: `PlugInKind`
**Inherits:** Enum
**Description:** Supported plugin kinds.

**Class Variables:** `WORKFLOW`, `VALIDATOR`, `REPORTER`, `GROWTH_MANAGER`, `CORE_SERVICE`, `ANALYTICS`, `STRATEGY`, `TRANSFORMER`, `AI_ASSISTANT`

### Class: `PluginError`
**Inherits:** Exception
**Description:** Raised when a plugin operation fails.

### Class: `PluginDependencyError`
**Inherits:** PluginError
**Description:** Raised when a plugin's dependencies cannot be satisfied.

### Class: `PluginMeta`
**Decorators:** @dataclass(frozen=True)
**Description:** Metadata for a registered plugin.

**Class Variables:** `name`, `kind`, `version`, `author`, `description`, `tags`, `loaded_at`, `plugin_type`, `dependencies`, `rbac_roles`

### Class: `PluginBase`
**Inherits:** ABC
**Description:** Base class for all plugins.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `initialize` | ✓ | `self` | `None` | @abstractmethod |
| `start` | ✓ | `self` | `None` | @abstractmethod |
| `stop` | ✓ | `self` | `None` | @abstractmethod |
| `health_check` | ✓ | `self` | `bool` | @abstractmethod |
| `get_capabilities` | ✓ | `self` | `List[str]` | @abstractmethod |
| `on_reload` |  | `self` | `None` | - |

### Class: `PluginRegistry`
**Description:** Singleton registry for managing plugins.
This class is thread-safe for mutation operations via a global lock.

**Class Variables:** `_instance`, `_lock`, `_event_hook`, `_kind_locks`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__new__` |  | `cls, persist_path: str` | `-` | - |
| `set_event_hook` |  | `self, hook: Callable[[Dict[str, Any]], None]` | `-` | - |
| `_trigger_event` |  | `self, event_dict: Dict[str, Any]` | `-` | - |
| `_meta_to_dict` |  | `self, meta: PluginMeta` | `dict` | - |
| `_load_persisted_plugins` |  | `self` | `-` | - |
| `_persist_plugins` |  | `self` | `-` | - |
| `_verify_signature` |  | `self, plugin: Any, meta: PluginMeta` | `-` | - |
| `_validate_name` |  | `self, name: str` | `None` | - |
| `_validate_version` |  | `self, version_str: str` | `None` | - |
| `_validate_dependencies` |  | `self, kind: PlugInKind, name: str, dependencies: L` | `None` | - |
| `_satisfies_version` |  | `self, current: str, required: str` | `bool` | - |
| `register_with_omnicore` | ✓ | `self, kind: PlugInKind, name: str, plugin: PluginB` | `-` | - |
| `_validate_plugin_class` |  | `self, plugin: Type[PluginBase], meta: PluginMeta` | `None` | - |
| `register` |  | `self, kind: PlugInKind, name: str, version: str, d` | `-` | - |
| `register_instance` |  | `self, kind: PlugInKind, name: str, instance: Any, ` | `-` | - |
| `get` |  | `self, kind: PlugInKind, name: str` | `Any` | - |
| `get_metadata` |  | `self, kind: PlugInKind, name: str` | `Optional[PluginMeta]` | - |
| `list_plugins` |  | `self, kind: Optional[PlugInKind]` | `Dict[str, Any]` | - |
| `export_registry` |  | `self` | `Dict[str, Any]` | - |
| `unregister` | ✓ | `self, kind: PlugInKind, name: str` | `-` | - |
| `reload` | ✓ | `self, kind: PlugInKind, name: str` | `bool` | - |
| `health_check` | ✓ | `self, kind: PlugInKind, name: str` | `bool` | - |
| `health_check_all` | ✓ | `self` | `Dict[str, Any]` | - |
| `discover` |  | `self, package: str, kind: PlugInKind` | `List[str]` | - |
| `load_from_package` | ✓ | `self, package_url: str, signature: Optional[str]` | `-` | - |
| `sandboxed_plugin` |  | `self, kind: PlugInKind, name: str` | `-` | @contextmanager |
| `initialize_all` | ✓ | `self` | `None` | - |
| `_initialize_plugin` | ✓ | `self, kind: PlugInKind, name: str, plugin: PluginB` | `-` | @retry(stop=stop_after_attempt |
| `start_all` | ✓ | `self` | `None` | - |
| `_start_plugin` | ✓ | `self, kind: PlugInKind, name: str, plugin: PluginB` | `-` | - |
| `stop_all` | ✓ | `self` | `None` | - |
| `_stop_plugin` | ✓ | `self, kind: PlugInKind, name: str, plugin: PluginB` | `-` | - |
| `__aenter__` | ✓ | `self` | `-` | - |
| `__aexit__` | ✓ | `self, exc_type, exc_val, exc_tb` | `-` | - |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `register` |  | `kind: PlugInKind, name: str, version: str, author:` | `-` | - |
| `get_registry` |  | `` | `PluginRegistry` | - |
| `__getattr__` |  | `name: str` | `Any` | - |

---

## arena.py

**Lines:** 1390

### Constants

| Name |
|------|
| `__all__` |
| `logger` |
| `_Arbiter_class` |
| `tracer` |
| `JWT_SECRET_FALLBACK` |
| `_metrics_lock` |
| `scan_repair_cycles_total` |
| `defects_found_total` |
| `repairs_attempted_total` |
| `repairs_successful_total` |
| `agent_evolutions_total` |
| `active_arbiters` |
| `arena_ops_total` |
| `arena_errors_total` |

### Class: `ArbiterArena`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, settings: ArbiterConfig, port: Optional[int]` | `-` | - |
| `__aenter__` | ✓ | `self` | `-` | - |
| `__aexit__` | ✓ | `self, exc_type, exc_val, exc_tb` | `-` | - |
| `_send_webhook` | ✓ | `self, event_type: str, data: Dict` | `-` | - |
| `_setup_error_handlers` |  | `self` | `-` | - |
| `_update_and_persist_map` | ✓ | `self, new_map_data: Dict, source: str` | `-` | - |
| `_create_initial_scan_coro` | ✓ | `self` | `-` | - |
| `_create_periodic_scan_coro` | ✓ | `self` | `-` | - |
| `_initialize_arbiters` |  | `self` | `-` | - |
| `register` | ✓ | `self, arbiter: Any` | `-` | - |
| `remove` | ✓ | `self, arbiter: Any` | `-` | - |
| `get_random_arbiter` | ✓ | `self` | `'Arbiter'` | - |
| `distribute_task` | ✓ | `self, task_coro: Callable` | `Any` | - |
| `_setup_routes` |  | `self` | `-` | - |
| `start_arena_services` | ✓ | `self, http_port: int` | `-` | - |
| `handle_status` | ✓ | `self, request: Optional[Request]` | `Dict[str, Any]` | - |
| `run_all` | ✓ | `self, max_cycles: int` | `-` | - |
| `run_arena_rounds` | ✓ | `self` | `-` | - |
| `stop_all` | ✓ | `self` | `-` | - |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `_get_registry` |  | `` | `-` | - |
| `_get_plugin_registry_dict` |  | `` | `-` | - |
| `_get_arbiter_class` |  | `` | `-` | - |
| `get_or_create_prom_counter` |  | `name: str, documentation: str, labelnames: Tuple[s` | `-` | - |
| `get_or_create_prom_gauge` |  | `name: str, documentation: str, labelnames: Tuple[s` | `-` | - |
| `require_auth` |  | `func: Callable` | `Callable` | - |
| `_handle_shutdown` |  | `loop: asyncio.AbstractEventLoop, arena: ArbiterAre` | `-` | - |
| `_extract_sqlite_db_file` |  | `db_url: str` | `str` | - |
| `run_arena_async` | ✓ | `settings` | `-` | - |
| `run_arena` |  | `` | `-` | - |

---

## audit_log.py

**Lines:** 1123

### Constants

| Name |
|------|
| `audit_logger` |

### Class: `RotationType`
**Inherits:** str, Enum
**Description:** Valid rotation types for TimedRotatingFileHandler.

**Class Variables:** `SIZE`, `SECOND`, `MINUTE`, `HOUR`, `DAY`, `MIDNIGHT`, `WEEKDAY`

### Class: `CompressionType`
**Inherits:** str, Enum
**Description:** Supported compression types for rotated files.

**Class Variables:** `NONE`, `GZIP`

### Class: `AuditLoggerConfig`
**Decorators:** @dataclass
**Description:** Configuration for the Tamper-Evident Audit Logger.
Supports advanced options for encryption, batching, and custom validation.

**Class Variables:** `log_path`, `rotation_type`, `rotation_interval`, `max_file_size`, `retention_count`, `compression_type`, `encrypt_logs`, `encryption_key`, `batch_size`, `batch_timeout`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__post_init__` |  | `self` | `-` | - |

### Class: `SizedTimedRotatingFileHandler`
**Inherits:** TimedRotatingFileHandler
**Description:** Custom handler supporting size-based rotation and compression.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, filename: str, when: str, interval: int, bac` | `-` | - |
| `shouldRollover` |  | `self, record: logging.LogRecord` | `bool` | - |
| `doRollover` |  | `self` | `-` | - |
| `_compress_rotated_file` |  | `self` | `-` | - |

### Class: `TamperEvidentLogger`
**Description:** A tamper-evident audit logger with hash chaining, async support, encryption, batching, and integrations.
Uses a singleton pattern for consistent state across the application.

**Class Variables:** `_instance`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__new__` |  | `cls, config: Optional[AuditLoggerConfig]` | `-` | - |
| `__init__` |  | `self, config: Optional[AuditLoggerConfig]` | `-` | - |
| `get_instance` |  | `cls, config: Optional[AuditLoggerConfig]` | `'TamperEvidentLogger'` | @classmethod |
| `_setup_file_logger` |  | `self` | `logging.Logger` | - |
| `_setup_dlt_client` |  | `self` | `Optional[Any]` | - |
| `_setup_metrics` |  | `self` | `Dict[str, Any]` | - |
| `_setup_encryption` |  | `self` | `Optional[Fernet]` | - |
| `_get_trace_ids` |  | `` | `Tuple[Optional[str], Optional[` | @staticmethod |
| `_get_agent_info` |  | `` | `Dict[str, Any]` | @staticmethod |
| `_hash_entry` |  | `prev_hash: Optional[str], entry_dict: Dict[str, An` | `str` | @staticmethod |
| `_sanitize_dict` |  | `d: Dict[str, Any], max_size: int` | `Dict[str, Any]` | @staticmethod |
| `_encrypt_entry` |  | `self, entry: Dict[str, Any]` | `Dict[str, Any]` | - |
| `_decrypt_entry` |  | `self, entry: Dict[str, Any]` | `Dict[str, Any]` | - |
| `_log_to_file_async` | ✓ | `self, entries: List[Dict[str, Any]]` | `-` | - |
| `_log_to_file_sync` |  | `self, entries: List[Dict[str, Any]]` | `-` | - |
| `_process_batch_loop` | ✓ | `self` | `-` | - |
| `_anchor_to_dlt` | ✓ | `self, entries: List[Dict[str, Any]]` | `List[Optional[str]]` | - |
| `log_event` | ✓ | `self, event_type: str, details: Dict[str, Any], us` | `str` | - |
| `_sanitize_details` |  | `self, details: Dict[str, Any]` | `Dict[str, Any]` | - |
| `_compute_hmac` |  | `self, event_id, event_type, details, user_id` | `-` | - |
| `emit_audit_event` | ✓ | `self, event_type: str, details: Dict[str, Any], us` | `-` | - |
| `_get_log_files` |  | `self, log_path: Path` | `List[Path]` | - |
| `verify_log_integrity` | ✓ | `self, log_path: Optional[Path]` | `Tuple[bool, Optional[int], Opt` | - |
| `load_audit_trail` |  | `self, log_path: Optional[Path], event_type: Option` | `Iterator[Dict[str, Any]]` | - |
| `_filter_log_entries` |  | `self, file_handle, event_type: Optional[str], star` | `Iterator[Dict[str, Any]]` | - |
| `_filter_entry_logic` |  | `entry: Dict[str, Any], event_type: Optional[str], ` | `bool` | @staticmethod |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `log_event` | ✓ | `event_type: str, details: Dict[str, Any], critical` | `str` | - |
| `verify_log_integrity` | ✓ | `log_path: Optional[Path]` | `Tuple[bool, Optional[int], Opt` | - |
| `load_audit_trail` | ✓ | `log_path: Optional[Path], event_type: Optional[str` | `Iterator[Dict[str, Any]]` | - |
| `emit_audit_event` | ✓ | `event_type: str, details: Dict[str, Any], user_id:` | `-` | - |

---

## audit_schema.py

**Lines:** 419
**Description:** Unified Audit Event Schema
===========================

Canonical Pydantic models for audit events across all platform modules.
Provides a single source of truth for audit event structure, ensuring 
c...

### Constants

| Name |
|------|
| `logger` |

### Class: `AuditEventType`
**Inherits:** str, Enum
**Description:** Standard audit event types across all modules.

**Class Variables:** `CODE_GENERATION_STARTED`, `CODE_GENERATION_COMPLETED`, `CODE_GENERATION_FAILED`, `CRITIQUE_COMPLETED`, `TEST_GENERATION_COMPLETED`, `DEPLOYMENT_STARTED`, `DEPLOYMENT_COMPLETED`, `POLICY_CHECK`, `POLICY_VIOLATION`, `CONSTITUTION_CHECK`

### Class: `AuditSeverity`
**Inherits:** str, Enum
**Description:** Severity levels for audit events.

**Class Variables:** `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`

### Class: `AuditModule`
**Inherits:** str, Enum
**Description:** Platform modules that generate audit events.

**Class Variables:** `GENERATOR`, `ARBITER`, `TEST_GENERATION`, `SIMULATION`, `OMNICORE`, `GUARDRAILS`, `SERVER`, `MESH`

### Class: `AuditRouter`
**Description:** Routes audit events to appropriate backends.

Accepts events in the unified AuditEvent schema and routes them to 
the appropriate storage backend (database, file, message queue, etc).

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self` | `-` | - |
| `register_backend` |  | `self, backend: Any, name: str` | `-` | - |
| `route_event` | ✓ | `self, event: AuditEvent` | `Dict[str, Any]` | - |
| `route_event_sync` |  | `self, event: AuditEvent` | `Dict[str, Any]` | - |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `create_audit_event` |  | `event_type: str, module: str, message: str` | `AuditEvent` | - |

---

## bug_manager/audit_log.py

**Lines:** 620

### Constants

| Name |
|------|
| `logger` |
| `AUDIT_LOG_FLUSH` |
| `AUDIT_LOG_WRITE_SUCCESS` |
| `AUDIT_LOG_WRITE_FAILED` |
| `AUDIT_LOG_DEAD_LETTER` |
| `AUDIT_LOG_ROTATION` |
| `AUDIT_LOG_BUFFER_SIZE_GAUGE` |
| `AUDIT_LOG_REMOTE_SEND_SUCCESS` |
| `AUDIT_LOG_REMOTE_SEND_FAILED` |
| `AUDIT_LOG_DISK_CHECK_FAILED` |
| `AUDIT_LOG_FLUSH_DURATION_SECONDS` |
| `AUDIT_LOG_DROPPED` |

### Class: `AuditLogManager`
**Description:** A robust, asynchronous audit log manager for high-throughput applications.

This manager buffers log entries in-memory, flushes them periodically and
on a full buffer, and writes them to a local file. It supports log rotation,
dead-letter queuing for failed writes, and optional remote forwarding.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, log_path: Optional[str], dead_letter_log_pat` | `-` | - |
| `initialize` | ✓ | `self` | `None` | - |
| `shutdown` | ✓ | `self` | `None` | - |
| `_write_to_dead_letter_queue` | ✓ | `self, entry: Dict[str, Any], reason: str` | `None` | - |
| `_sync_write_to_dead_letter` |  | `self, data: str` | `None` | - |
| `_periodic_flush` | ✓ | `self` | `None` | - |
| `_flush_buffer` | ✓ | `self, final_flush: bool` | `None` | - |
| `_rotate_logs` | ✓ | `self` | `None` | - |
| `_sync_rotate_logs` |  | `self` | `None` | - |
| `_sync_atomic_write_with_retry` |  | `self, entries: List[Dict[str, Any]]` | `None` | @tenacity.retry(stop=tenacity. |
| `_send_to_remote_audit_service` | ✓ | `self, entries: List[Dict[str, Any]]` | `None` | - |
| `_handle_remote_send_failure` | ✓ | `self, entries: List[Dict[str, Any]], reason: str` | `-` | - |
| `audit` | ✓ | `self, event_type: str, details: Dict[str, Any]` | `None` | - |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `get_or_create_metric` |  | `metric_class, name, documentation, labelnames` | `-` | - |

---

## bug_manager/bug_manager.py

**Lines:** 746

### Constants

| Name |
|------|
| `logger` |
| `_bug_id_var` |
| `BUG_REPORT` |
| `BUG_REPORT_SUCCESS` |
| `BUG_REPORT_FAILED` |
| `BUG_AUTO_FIX_ATTEMPT` |
| `BUG_AUTO_FIX_SUCCESS` |
| `BUG_NOTIFICATION_DISPATCH` |
| `BUG_PROCESSING_DURATION_SECONDS` |
| `BUG_RATE_LIMITED` |
| `BUG_CURRENT_ACTIVE_REPORTS` |
| `BUG_NOTIFICATION_FAILED` |
| `BUG_ML_INIT_FAILED` |

### Class: `Settings`
**Inherits:** BaseModel

**Class Variables:** `DEBUG_MODE`, `SLACK_WEBHOOK_URL`, `EMAIL_RECIPIENTS`, `EMAIL_ENABLED`, `EMAIL_SENDER`, `EMAIL_SMTP_SERVER`, `EMAIL_SMTP_PORT`, `EMAIL_USE_STARTTLS`, `EMAIL_SMTP_USERNAME`, `EMAIL_SMTP_PASSWORD`

### Class: `RateLimiter`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, settings: Settings` | `-` | - |
| `initialize` | ✓ | `self` | `-` | - |
| `rate_limit` |  | `self, func` | `-` | - |

### Class: `BugManager`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, settings: Settings` | `-` | - |
| `_initialize` | ✓ | `self` | `-` | - |
| `shutdown` | ✓ | `self` | `None` | - |
| `report` | ✓ | `self, error_data: Union[Exception, str, Dict[str, ` | `None` | - |
| `_report_impl` | ✓ | `self, error_data: Union[Exception, str, Dict[str, ` | `None` | - |
| `_parse_error_data` |  | `self, error_data: Union[Exception, str, Dict[str, ` | `Dict[str, Any]` | - |
| `_get_stack_trace_from_caller` |  | `self` | `Optional[str]` | - |
| `_generate_bug_signature` |  | `self, error_data: Union[Exception, str, Dict[str, ` | `str` | - |
| `_dispatch_notifications` | ✓ | `self, error_details: Dict[str, Any]` | `None` | - |

### Class: `BugManagerArena`
**Inherits:** BugManager

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, settings: Optional[Settings]` | `-` | - |
| `report` |  | `self, error: Exception` | `None` | - |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `register` |  | `kind` | `-` | - |
| `manage_bug` | ✓ | `error_data: Union[Exception, str, Dict[str, Any]],` | `Dict[str, Any]` | @register(kind=PlugInKind.CORE |

---

## bug_manager/notifications.py

**Lines:** 1023

### Constants

| Name |
|------|
| `logger` |
| `NOTIFICATION_SEND` |
| `NOTIFICATION_SEND_SUCCESS` |
| `NOTIFICATION_SEND_FAILED` |
| `NOTIFICATION_CIRCUIT_BREAKER_OPEN` |
| `NOTIFICATION_RATE_LIMITED` |
| `NOTIFICATION_CURRENT_FAILURES_GAUGE` |
| `NOTIFICATION_SEND_DURATION_SECONDS` |

### Class: `CircuitBreaker`
**Description:** Implements a circuit breaker pattern to prevent repeated failures against a service.

This class can operate using an in-memory state or, for distributed systems,
a shared state via Redis.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, failure_threshold: int, recovery_timeout: in` | `-` | - |
| `initialize` | ✓ | `self` | `-` | - |
| `_get_state` | ✓ | `self, channel: str` | `tuple` | - |
| `_set_state` | ✓ | `self, channel: str, state: str, failures: int, las` | `-` | - |
| `__call__` |  | `self, channel: str` | `-` | - |

### Class: `RateLimiter`
**Description:** Implements a rate limiter using a sliding window.

This class can operate using an in-memory state or, for distributed systems,
a shared state via Redis.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, redis_url: Optional[str]` | `-` | - |
| `initialize` | ✓ | `self` | `-` | - |
| `rate_limit` |  | `self, channel: str, max_calls: int, period: int` | `-` | - |

### Class: `NotificationService`
**Description:** A service for sending notifications via different channels (Slack, Email, PagerDuty).

It incorporates advanced resilience patterns like Circuit Breakers and Rate Limiters.

**Class Variables:** `_critical_notification_handler`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `register_critical_notification_handler` |  | `cls, handler: Callable[[str, int, str], Coroutine[` | `-` | @classmethod |
| `__init__` |  | `self, settings: Any` | `-` | - |
| `_validate_settings` |  | `self` | `-` | - |
| `_initialize` | ✓ | `self` | `-` | - |
| `shutdown` | ✓ | `self` | `-` | - |
| `_check_and_escalate` | ✓ | `self, channel: str, message: str` | `None` | - |
| `_record_notification_failure` | ✓ | `self, channel: str, message: str, error_code: str` | `None` | - |
| `_record_notification_success` | ✓ | `self, channel: str` | `None` | - |
| `_simulate_failure` |  | `self, channel: str` | `None` | - |
| `_notify_slack_with_decorators` |  | `self` | `-` | @property |
| `_notify_email_with_decorators` |  | `self` | `-` | @property |
| `_notify_pagerduty_with_decorators` |  | `self` | `-` | @property |
| `notify_batch` | ✓ | `self, notifications: List[Dict[str, Any]]` | `List[Any]` | - |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `get_or_create_metric` |  | `metric_class, name, documentation, labelnames` | `-` | - |
| `default_escalation_handler` |  | `channel: str, failures: int` | `None` | - |

---

## bug_manager/remediations.py

**Lines:** 880

### Constants

| Name |
|------|
| `logger` |
| `REMEDIATION_PLAYBOOK_EXECUTION` |
| `REMEDIATION_STEP_EXECUTION` |
| `REMEDIATION_STEP_DURATION_SECONDS` |
| `REMEDIATION_SUCCESS` |
| `REMEDIATION_FAILURE` |
| `ML_REMEDIATION_PREDICTION` |
| `ML_REMEDIATION_PREDICTION_SUCCESS` |
| `ML_REMEDIATION_PREDICTION_FAILED` |
| `ML_REMEDIATION_FEEDBACK` |
| `ML_REMEDIATION_FEEDBACK_FAILED` |
| `restart_service_playbook` |

### Class: `MLRemediationModel`
**Description:** Manages interactions with an external Machine Learning model for bug remediation.
Handles predictions, feedback, and ensures secure, resilient communication.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, model_endpoint: str, settings: Any` | `-` | - |
| `_get_session` | ✓ | `self` | `aiohttp.ClientSession` | - |
| `close` | ✓ | `self` | `None` | - |
| `predict_remediation_strategy` | ✓ | `self, bug_details: Dict[str, Any]` | `Tuple[Optional[str], float]` | @tenacity.retry(stop=tenacity. |
| `record_remediation_outcome` | ✓ | `self, bug_details: Dict[str, Any], playbook_name: ` | `None` | @tenacity.retry(stop=tenacity. |

### Class: `RemediationStep`
**Description:** Represents a single, potentially retryable step in a remediation playbook.

**Class Variables:** `_action_registry`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `register_action` |  | `cls, name: str, action: Callable[..., Coroutine]` | `None` | @classmethod |
| `__init__` |  | `self, name: str, action_name: str, pre_condition: ` | `-` | - |
| `execute` | ✓ | `self, bug_details: Dict[str, Any], playbook_name: ` | `bool` | - |

### Class: `RemediationPlaybook`
**Description:** Defines and executes a sequence of remediation steps for a specific bug.
It acts as a state machine, moving from step to step based on outcomes.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, name: str, steps: List[RemediationStep], des` | `-` | - |
| `execute` | ✓ | `self, location: str, bug_details: Dict[str, Any]` | `bool` | - |

### Class: `BugFixerRegistry`
**Description:** A central registry for managing and selecting remediation playbooks.

**Class Variables:** `_playbooks`, `_ml_remediation_model`, `_settings`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `set_settings` |  | `cls, settings: Any` | `-` | @classmethod |
| `set_ml_model` |  | `cls, model: MLRemediationModel` | `-` | @classmethod |
| `register_playbook` |  | `cls, playbook: RemediationPlaybook, location: str,` | `-` | @classmethod |
| `run_remediation` | ✓ | `cls, location: str, bug_details: Dict[str, Any], b` | `bool` | @classmethod |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `get_or_create_metric` |  | `metric_class, name, documentation, labelnames` | `-` | - |
| `restart_service` | ✓ | `bug_details: Dict[str, Any]` | `bool` | - |
| `clear_cache` | ✓ | `bug_details: Dict[str, Any]` | `bool` | - |

---

## bug_manager/utils.py

**Lines:** 538

### Constants

| Name |
|------|
| `logger` |
| `PII_REDACTION_COUNT` |
| `SETTINGS_VALIDATION_ERRORS` |

### Class: `Severity`
**Inherits:** str, Enum
**Description:** Enum representing severity levels for bug reports.

**Class Variables:** `CRITICAL`, `HIGH`, `MEDIUM`, `LOW`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `from_string` |  | `cls, value: str` | `'Severity'` | @classmethod |

### Class: `SecretStr`
**Inherits:** SecretStrBase

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, value` | `-` | - |

### Class: `BugManagerError`
**Inherits:** Exception
**Description:** Base class for all custom errors in the BugManager.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, message: str, error_id: Optional[str], times` | `-` | - |
| `__str__` |  | `self` | `str` | - |

### Class: `NotificationError`
**Inherits:** BugManagerError
**Description:** Raised when a notification channel fails to send a message.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, message: str, channel: str, error_code: str` | `-` | - |

### Class: `CircuitBreakerOpenError`
**Inherits:** BugManagerError
**Description:** Raised when a circuit breaker is in an OPEN state.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, message: str, channel: Optional[str]` | `-` | - |

### Class: `RateLimitExceededError`
**Inherits:** BugManagerError
**Description:** Raised when a rate limit is exceeded.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, message: str, key: Optional[str]` | `-` | - |

### Class: `AuditLogError`
**Inherits:** BugManagerError
**Description:** Raised when an audit log operation fails.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, message: str, log_path: Optional[str]` | `-` | - |

### Class: `RemediationError`
**Inherits:** BugManagerError
**Description:** Raised when a remediation step or playbook fails.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, message: str, step_name: Optional[str], play` | `-` | - |

### Class: `MLRemediationError`
**Inherits:** BugManagerError
**Description:** Raised when an ML-based remediation operation fails.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, message: str, model_endpoint: Optional[str]` | `-` | - |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `get_or_create_metric` |  | `metric_class, name, documentation, labelnames` | `-` | - |
| `parse_env` |  | `var: str, default: Any, type_hint: type` | `Any` | - |
| `parse_bool_env` |  | `var: str, default: bool` | `bool` | - |
| `redact_pii` |  | `details: Dict[str, Any], settings: Any` | `Dict[str, Any]` | - |
| `validate_settings` |  | `settings_obj: Any, required_fields: Dict[str, type` | `List[str]` | - |
| `apply_settings_validation` |  | `settings_obj: Any` | `None` | - |
| `validate_input_details` |  | `details: Optional[Dict[str, Any]]` | `Dict[str, Any]` | - |

---

## codebase_analyzer.py

**Lines:** 1454

### Constants

| Name |
|------|
| `tracer` |
| `logger` |
| `analyzer_ops_total` |
| `analyzer_errors_total` |
| `app` |

### Class: `AnalyzerError`
**Inherits:** Exception
**Description:** Base exception for analyzer errors.

### Class: `ConfigurationError`
**Inherits:** AnalyzerError
**Description:** Configuration-related errors.

### Class: `AnalysisError`
**Inherits:** AnalyzerError
**Description:** Analysis-related errors.

### Class: `Defect`
**Inherits:** TypedDict

**Class Variables:** `file`, `line`, `column`, `message`, `source`

### Class: `Dependency`
**Inherits:** TypedDict

**Class Variables:** `file`, `import_name`, `asname`, `level`, `from_import`, `module`, `line`, `is_external`

### Class: `ToolInfo`
**Inherits:** TypedDict

**Class Variables:** `name`, `type`, `available`, `installed_via`

### Class: `ComplexityInfo`
**Inherits:** TypedDict

**Class Variables:** `file`, `name`, `type`, `complexity`, `maintainability_index`

### Class: `FileSummary`
**Inherits:** TypedDict

**Class Variables:** `files`, `modules`, `defects`, `complexity`, `coverage`, `dependency_summary`

### Class: `Plugin`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, name: str, type: str` | `-` | - |
| `run` | ✓ | `self, file_path: Path, source: str` | `List[Defect]` | - |
| `metadata` |  | `self` | `Dict[str, Any]` | - |

### Class: `CodebaseAnalyzer`
**Description:** A comprehensive, asynchronous, and pluggable codebase analysis tool.
Analyzes Python code for defects, complexity, and dependencies.
This class is thread-safe.

**Class Variables:** `DEFAULT_IGNORE_PATTERNS`, `CONFIG_FILES`, `BASELINE_FILE`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, root_dir: Optional[str], ignore_patterns: Op` | `-` | - |
| `__aenter__` | ✓ | `self` | `-` | - |
| `__aexit__` | ✓ | `self, exc_type, exc_val, exc_tb` | `-` | - |
| `_load_config` |  | `self, config_file: Optional[str]` | `Dict[str, Any]` | - |
| `_parse_config_file` |  | `self, config_path: Path` | `Dict[str, Any]` | - |
| `_load_baseline` |  | `self` | `Dict[str, List[Defect]]` | - |
| `_save_baseline` |  | `self, defects: List[Defect]` | `-` | - |
| `_load_plugins` |  | `self` | `-` | - |
| `_should_ignore` |  | `self, path: Path` | `bool` | - |
| `_collect_py_files` | ✓ | `self, path: Path` | `List[Path]` | - |
| `_read_file` |  | `self, file_path: Path` | `Tuple[Optional[str], Optional[` | - |
| `_analyze_file_defects_and_complexity_blocking` |  | `self, file_path: Path` | `Tuple[List[Defect], List[Compl` | - |
| `_run_linters_sync` |  | `self, file_path: Path, source: str, tree: ast.AST` | `List[Defect]` | - |
| `_run_plugins_sync` |  | `self, file_path: Path, source: str` | `List[Defect]` | - |
| `_analyze_complexity_sync` |  | `self, file_path: Path, source: str` | `List[ComplexityInfo]` | - |
| `_analyze_coverage_sync` |  | `self, path: Path` | `Dict[str, Any]` | - |
| `scan_codebase` | ✓ | `self, path: Optional[Union[str, List[str]]], use_b` | `FileSummary` | - |
| `analyze_and_propose` | ✓ | `self, path: str` | `List[Dict[str, Any]]` | @retry(stop=stop_after_attempt |
| `_analyze_and_propose_sync` |  | `self, path: str` | `List[Dict[str, Any]]` | - |
| `audit_repair_tools` | ✓ | `self` | `List[Dict[str, Any]]` | - |
| `_audit_repair_tools_sync` |  | `self` | `List[ToolInfo]` | - |
| `map_dependencies` | ✓ | `self, path: Optional[str]` | `List[Dependency]` | - |
| `_extract_dependencies_from_file` | ✓ | `self, file_path: Path` | `List[Dependency]` | - |
| `generate_report` | ✓ | `self, output_format: str, output_path: Optional[st` | `Dict[str, Any]` | - |
| `_generate_markdown_report` |  | `self, summary: FileSummary` | `str` | - |
| `_generate_junit_xml_report` |  | `self, summary: FileSummary` | `str` | - |
| `analyze_file` | ✓ | `self, file_path: str` | `Dict[str, Any]` | - |
| `discover_files` |  | `self` | `List[str]` | - |
| `_filter_baseline` |  | `self, defects: List[Defect]` | `List[Defect]` | - |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `_create_dummy_metric` |  | `` | `-` | - |
| `_get_or_create_metric` |  | `metric_class, name, description, labelnames` | `-` | - |
| `analyze_codebase` | ✓ | `root_dir: str, config_file: Optional[str], output_` | `Dict[str, Any]` | - |
| `_run_async` |  | `coro` | `-` | - |
| `scan` |  | `root_dir: str, config_file: Optional[str], output_` | `-` | @app.command() |
| `tools` |  | `root_dir: str` | `-` | @app.command() |

---

## config.py

**Lines:** 1162

### Constants

| Name |
|------|
| `_tracer_cache` |
| `logger` |
| `_metrics_lock` |
| `CONFIG_ACCESS` |
| `CONFIG_ERRORS` |
| `CONFIG_OPS_TOTAL` |

### Class: `ConfigError`
**Inherits:** Exception
**Description:** Custom exception for configuration errors.

### Class: `LLMSettings`
**Inherits:** BaseSettings
**Description:** LLM configuration settings with explicit environment variable mapping.

Environment variables are mapped using the LLM_ prefix by default.
For example: LLM_DEFAULT_PROVIDER, LLM_TEMPERATURE, etc.

Special case: api_key supports both OPENAI_API_KEY (legacy) and LLM_API_KEY.

**Class Variables:** `default_provider`, `retry_providers`, `timeout_seconds`, `api_url`, `api_key`, `model_name`, `temperature`, `max_tokens`, `top_p`, `frequency_penalty`

### Class: `ArbiterConfig`
**Inherits:** BaseSettings
**Description:** Centralized configuration management for the Arbiter AI Assistant and OmniCore ecosystem.
Leverages Pydantic BaseSettings for automatic loading from environment variables and .env files.

**Class Variables:** `model_config`, `REDIS_URL`, `REDIS_POOL_SIZE`, `KAFKA_BOOTSTRAP_SERVERS`, `DB_PATH`, `DB_POOL_SIZE`, `DB_POOL_MAX_OVERFLOW`, `DB_RETRY_ATTEMPTS`, `DB_RETRY_DELAY`, `DB_CIRCUIT_THRESHOLD`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self` | `-` | - |
| `ensure_https_in_prod` |  | `cls, v` | `-` | @field_validator('OMNICORE_URL |
| `validate_api_key` |  | `cls, v` | `-` | @field_validator('ALPHAVANTAGE |
| `validate_email_recipients` |  | `cls, v` | `-` | @field_validator('EMAIL_RECIPI |
| `initialize` |  | `cls` | `'ArbiterConfig'` | @classmethod |
| `load_from_file` |  | `cls, file_path: str` | `'ArbiterConfig'` | @classmethod |
| `load_from_env` |  | `cls` | `'ArbiterConfig'` | @classmethod |
| `decrypt_sensitive_fields` |  | `self` | `None` | - |
| `encrypt_sensitive_fields` |  | `self` | `None` | - |
| `_validate_custom_settings` |  | `self` | `-` | - |
| `validate_file` |  | `cls, file_path: str` | `bool` | @classmethod |
| `reload` |  | `cls` | `-` | @classmethod |
| `refresh` | ✓ | `self` | `None` | - |
| `stream_config_change` | ✓ | `cls, key: str, value: Any` | `-` | @classmethod |
| `model_dump` |  | `self` | `-` | - |
| `to_dict` |  | `self` | `Dict[str, Any]` | - |
| `rotate_encryption_key` | ✓ | `self` | `None` | - |
| `health_check` |  | `self` | `Dict[str, Any]` | - |
| `DATABASE_URL` |  | `self` | `str` | @property |
| `database_path` |  | `self` | `str` | @property |
| `plugin_dir` |  | `self` | `str` | @property |
| `log_level` |  | `self` | `str` | @property |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `_get_tracer` |  | `` | `-` | - |
| `get_or_create_counter` |  | `name: str, documentation: str, labelnames: Tuple[s` | `-` | - |
| `get_or_create_gauge` |  | `name: str, documentation: str, labelnames: Tuple[s` | `-` | - |
| `get_or_create_histogram` |  | `name: str, documentation: str, labelnames: Tuple[s` | `-` | - |
| `load_persona_dict` |  | `` | `Dict[str, str]` | @retry(stop=stop_after_attempt |

---

## decision_optimizer.py

**Lines:** 2044

### Constants

| Name |
|------|
| `logger` |
| `TASK_PRIORITIZATION_COUNT` |
| `ALLOCATION_LATENCY` |
| `COORDINATION_SUCCESS` |
| `AGENT_ACTIVE_GAUGE` |
| `EXPLANATION_EVENTS` |
| `ERRORS_CRITICAL` |
| `PLUGIN_EXECUTION_LATENCY` |
| `DB_OPERATION_LATENCY` |
| `STRATEGY_REFRESH_COUNT` |
| `STRATEGY_REFRESH_SUCCESS` |

### Class: `SFECoreEngine`
**Description:** Represents the central core engine of the Self-Fixing Engineer (SFE) system.

**Class Variables:** `database`, `feedback_manager`, `knowledge_graph`, `explainable_reasoner`, `policy_engine`, `bug_manager`, `monitor`, `human_in_loop`, `plugin_registry`, `notification_service`

### Class: `MetaLearningService`
**Description:** A conceptual service interface for retrieving updated models, policies,
and configurations from the meta-learning pipeline.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, logger` | `-` | - |
| `get_latest_prioritization_weights` | ✓ | `self` | `Optional[Dict[str, float]]` | - |
| `get_latest_policy_rules` | ✓ | `self` | `Optional[Dict[str, Any]]` | - |
| `get_latest_plugin_version` | ✓ | `self, kind: str, name: str` | `Optional[str]` | - |
| `get_plugin_code` | ✓ | `self, kind: str, name: str, version: str` | `Optional[Callable]` | - |

### Class: `Task`
**Decorators:** @dataclass

**Class Variables:** `id`, `priority`, `deadline`, `dependencies`, `required_skills`, `estimated_compute`, `risk_level`, `action_type`, `metadata`, `sim_request`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__post_init__` |  | `self` | `-` | - |

### Class: `Agent`
**Decorators:** @dataclass

**Class Variables:** `id`, `skills`, `max_compute`, `current_load`, `energy`, `role`, `metadata`, `arbiter_instance`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__post_init__` |  | `self` | `-` | - |

### Class: `DecisionOptimizer`
**Description:** The DecisionOptimizer orchestrates tasks and agents within the Self-Fixing Engineer (SFE) platform.
It handles task prioritization, resource allocation, and agent coordination using advanced strategies.
This component is part of the Arbiter AI system, which is integral to the SFE.
This class is thre...

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, plugin_registry: PLUGIN_REGISTRY, settings: ` | `-` | - |
| `__aenter__` | ✓ | `self` | `-` | - |
| `__aexit__` | ✓ | `self, exc_type, exc_val, exc_tb` | `-` | - |
| `_periodic_strategy_refresh` | ✓ | `self` | `-` | - |
| `refresh_strategies` | ✓ | `self` | `-` | - |
| `prioritize_and_allocate` | ✓ | `self, agents: List['Agent'], tasks: List['Task']` | `Tuple[Dict[str, List[str]], Li` | - |
| `shutdown` |  | `self` | `-` | - |
| `process_remediation_proposal` | ✓ | `self, proposal: Dict[str, Any]` | `-` | - |
| `_execute_fix` | ✓ | `self, proposal: Dict[str, Any]` | `-` | - |
| `_log_event` | ✓ | `self, event_type: str, details: Dict[str, Any]` | `-` | - |
| `critical_alert_decorator` |  | `method: Callable` | `Callable` | - |
| `load_strategy_plugin` | ✓ | `self, kind: str, name: str, strategy_type: str` | `-` | - |
| `safe_execute` | ✓ | `self, callable: Callable` | `-` | - |
| `anonymize_task` | ✓ | `self, task: Task` | `-` | - |
| `_handle_failed_task` | ✓ | `self, task: Task, error: str` | `-` | - |
| `_redis_publish` | ✓ | `self, channel: str, message: str` | `-` | @circuit(failure_threshold=5,  |
| `prioritize_tasks` | ✓ | `self, agent_pool: List[Agent], task_queue: List[Ta` | `List[Task]` | @critical_alert_decorator |
| `_default_prioritize` | ✓ | `self, task_queue: List[Task], criteria: Dict[str, ` | `List[Task]` | - |
| `_get_knowledge_graph_context_score` | ✓ | `self, task: Task` | `float` | - |
| `allocate_resources` | ✓ | `self, agent_pool: List[Agent], task_queue: List[Ta` | `Dict[str, List[Task]]` | @critical_alert_decorator |
| `_default_allocate` | ✓ | `self, agent_pool: List[Agent], task_queue: List[Ta` | `Dict[str, List[Task]]` | - |
| `coordinate_arbiters` | ✓ | `self, agent_pool: List[Agent], shared_context: Opt` | `Dict[str, Any]` | @critical_alert_decorator |
| `_default_coordinate` | ✓ | `self, agent_pool: List[Agent], encrypted_context: ` | `Dict[str, Any]` | - |
| `share_learning` | ✓ | `self, agent: Agent, strategy: Dict[str, Any]` | `-` | - |
| `rollback_allocation` | ✓ | `self, assignments: Dict[str, List[Task]], agent_po` | `-` | - |
| `get_metrics` | ✓ | `self` | `Dict[str, Any]` | - |
| `explain_decision` | ✓ | `self, decision_id: str` | `Dict[str, Any]` | - |
| `stream_events` | ✓ | `self, websocket: WebSocket` | `-` | - |
| `compute_trust_score` | ✓ | `self, context: Dict[str, Any], user_id: Optional[s` | `float` | - |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `safe_serialize` |  | `obj: Any` | `Any` | - |

---

## event_bus_bridge.py

**Lines:** 369
**Description:** EventBusBridge - Bidirectional bridge between Mesh EventBus and Arbiter MessageQueue.

[GAP #7 FIX] This module creates a bidirectional bridge that allows events to flow
between the Mesh event system ...

### Constants

| Name |
|------|
| `logger` |

### Class: `EventBusBridge`
**Description:** Bidirectional bridge between Mesh EventBus and Arbiter MessageQueueService.

This bridge enables event flow in both directions:
- Mesh → Arbiter: Events from the mesh system are published to Arbiter
- Arbiter → Mesh: Events from Arbiter are published to the mesh system

Configurable event types allo...

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, mesh_to_arbiter_events: Optional[Set[str]], ` | `-` | - |
| `_init_mesh_bus` |  | `self` | `-` | - |
| `_init_arbiter_mqs` |  | `self` | `-` | - |
| `start` | ✓ | `self` | `-` | - |
| `stop` | ✓ | `self` | `-` | - |
| `_bridge_mesh_to_arbiter` | ✓ | `self` | `-` | - |
| `_bridge_arbiter_to_mesh` | ✓ | `self` | `-` | - |
| `_subscribe_and_forward` | ✓ | `self, event_type: str, direction: str` | `-` | - |
| `_forward_mesh_to_arbiter` | ✓ | `self, event_type: str, data: Dict[str, Any]` | `-` | - |
| `_forward_arbiter_to_mesh` | ✓ | `self, event_type: str, data: Dict[str, Any]` | `-` | - |
| `get_stats` |  | `self` | `Dict[str, Any]` | - |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `get_bridge` | ✓ | `` | `EventBusBridge` | - |
| `stop_bridge` | ✓ | `` | `-` | - |

---

## explainable_reasoner/adapters.py

**Lines:** 1476

### Constants

| Name |
|------|
| `_logger` |
| `STREAM_CHUNKS` |
| `HEALTH_CHECK_ERRORS` |
| `__all__` |

### Class: `LLMAdapter`
**Inherits:** ABC
**Description:** Abstract base for LLM adapters. Handles text/multimodal inference/streaming with resilience/security.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, model_name: str, api_key: SensitiveValue, ba` | `-` | - |
| `_get_client` | ✓ | `self` | `httpx.AsyncClient` | - |
| `rotate_key` | ✓ | `self, new_key: str` | `-` | - |
| `generate` | ✓ | `self, prompt: str, multi_modal_data: Optional[Dict` | `str` | @abstractmethod |
| `stream_generate` | ✓ | `self, prompt: str, multi_modal_data: Optional[Dict` | `AsyncGenerator[str, None]` | @abstractmethod |
| `health_check` | ✓ | `self` | `bool` | @abstractmethod |
| `aclose` | ✓ | `self` | `-` | - |

### Class: `OpenAIGPTAdapter`
**Inherits:** LLMAdapter
**Description:** Adapter for OpenAI API compatible models (e.g., GPT-4, GPT-3.5-Turbo).
This adapter supports multi-modal inputs (text and images) for vision-capable models.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self` | `-` | - |
| `_build_openai_messages` |  | `self, prompt: str, multi_modal_data: Optional[Dict` | `List[Dict[str, Any]]` | - |
| `generate` | ✓ | `self, prompt: str, multi_modal_data: Optional[Dict` | `str` | @retry() |
| `stream_generate` | ✓ | `self, prompt: str, multi_modal_data: Optional[Dict` | `AsyncGenerator[str, None]` | @retry() |
| `health_check` | ✓ | `self` | `bool` | - |

### Class: `GeminiAPIAdapter`
**Inherits:** LLMAdapter
**Description:** Adapter for the Google Gemini API (e.g., gemini-1.5-pro-latest).
This adapter supports multi-modal inputs by encoding them into the request payload.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self` | `-` | - |
| `_build_gemini_parts` |  | `self, prompt: str, multi_modal_data: Optional[Dict` | `List[Dict[str, Any]]` | - |
| `generate` | ✓ | `self, prompt: str, multi_modal_data: Optional[Dict` | `str` | @retry() |
| `stream_generate` | ✓ | `self, prompt: str, multi_modal_data: Optional[Dict` | `AsyncGenerator[str, None]` | @retry() |
| `health_check` | ✓ | `self` | `bool` | - |

### Class: `AnthropicAdapter`
**Inherits:** LLMAdapter
**Description:** Adapter for the Anthropic Messages API (e.g., Claude 3 family).
This adapter supports multi-modal inputs (text and images) for Claude 3 models.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self` | `-` | - |
| `_build_anthropic_messages` |  | `self, prompt: str, multi_modal_data: Optional[Dict` | `List[Dict[str, Any]]` | - |
| `generate` | ✓ | `self, prompt: str, multi_modal_data: Optional[Dict` | `str` | @retry() |
| `stream_generate` | ✓ | `self, prompt: str, multi_modal_data: Optional[Dict` | `AsyncGenerator[str, None]` | @retry() |
| `health_check` | ✓ | `self` | `bool` | - |

### Class: `LLMAdapterFactory`
**Description:** A factory for creating LLMAdapter instances based on configuration, with caching.

**Class Variables:** `_adapters`, `_default_base_urls`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `register_adapter` |  | `cls, name: str, adapter_class: Type[LLMAdapter]` | `-` | @classmethod |
| `get_adapter` |  | `cls, model_config_json: str` | `LLMAdapter` | @classmethod, @lru_cache(maxsi |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `retry` |  | `max_retries: int, initial_backoff_delay: float, ex` | `-` | - |

---

## explainable_reasoner/audit_ledger.py

**Lines:** 651

### Constants

| Name |
|------|
| `_logger` |
| `AUDIT_SEND_LATENCY` |
| `AUDIT_ERRORS` |
| `AUDIT_BATCH_SIZE` |
| `AUDIT_RATE_LIMIT_HITS` |

### Class: `AuditLedgerClient`
**Description:** Client for an external, immutable audit ledger.
This client uses `httpx` for asynchronous HTTP POST requests to the ledger endpoint.
It includes retry logic with exponential backoff and robust error handling.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, ledger_url: str, api_key: Optional[str], max` | `-` | - |
| `_get_client` | ✓ | `self` | `httpx.AsyncClient` | - |
| `_send_event_with_retries` | ✓ | `self, audit_record: Dict[str, Any]` | `bool` | @tenacity.retry(wait=tenacity. |
| `log_event` | ✓ | `self, event_type: str, details: Dict[str, Any], op` | `bool` | - |
| `log_batch_events` | ✓ | `self, events: List[Dict[str, Any]]` | `bool` | - |
| `health_check` | ✓ | `self` | `bool` | - |
| `rotate_key` | ✓ | `self, new_key: str` | `-` | - |
| `close` | ✓ | `self` | `-` | - |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `stop_after_attempt_from_self` |  | `retry_state: tenacity.RetryCallState` | `bool` | - |

---

## explainable_reasoner/explainable_reasoner.py

**Lines:** 2374

### Constants

| Name |
|------|
| `__version__` |
| `TRANSFORMERS_AVAILABLE` |
| `log_file_path` |
| `log_handler` |
| `_logger` |
| `logger` |

### Class: `SensitiveValue`
**Description:** A wrapper for sensitive values to prevent accidental logging.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, value: str` | `-` | - |
| `get_actual_value` |  | `self` | `str` | - |
| `__str__` |  | `self` | `str` | - |
| `__repr__` |  | `self` | `str` | - |

### Class: `ReasonerConfig`
**Inherits:** BaseModel
**Description:** Configuration for the Explainable Reasoner, validated by Pydantic.
All settings can be overridden by environment variables with the prefix REASONER_
(e.g., REASONER_MODEL_NAME=... or REASONER_STRICT_MODE=true).

**Class Variables:** `model_reload_retries`, `context_buffer_tokens`, `max_context_bytes`, `calls_per_second`, `max_workers`, `max_concurrent_requests`, `model_configs`, `model_name`, `device`, `mock_mode`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `from_env` |  | `cls` | `Self` | @classmethod |
| `get_public_config` |  | `self` | `Dict[str, Any]` | - |

### Class: `ExplainableReasoner`
**Description:** Core reasoner for generating explanations using transformer models.
Supports multi-model loading, async inference, history, and auditing.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, config: ReasonerConfig, settings: Optional[A` | `-` | - |
| `_initialize_history_manager` |  | `self` | `None` | - |
| `async_init` | ✓ | `self` | `None` | - |
| `_init_redis` | ✓ | `self` | `-` | - |
| `_init_history_db` | ✓ | `self` | `-` | - |
| `_run_model_readiness_test` | ✓ | `self` | `-` | - |
| `_run_history_pruner` | ✓ | `self` | `None` | - |
| `_initialize_models_async` | ✓ | `self` | `None` | - |
| `_load_single_model` | ✓ | `self, model_cfg: Dict[str, Any], is_reload: bool` | `Optional[Dict[str, Any]]` | - |
| `_load_hf_pipeline_sync` |  | `self, model_cfg: Dict[str, Any]` | `Dict[str, Any]` | - |
| `_execute_in_thread` | ✓ | `self, fn: Callable` | `Any` | - |
| `_get_next_pipeline` | ✓ | `self` | `Optional[Dict[str, Any]]` | - |
| `_unload_model` | ✓ | `self, model_key: str` | `None` | - |
| `_attempt_reload_model` | ✓ | `self, model_info: Dict[str, Any], initial_delay: i` | `None` | - |
| `_reload_model_with_retries` | ✓ | `self, model_info: Dict[str, Any], initial_delay: i` | `None` | - |
| `_truncate_prompt_if_needed` | ✓ | `self, prompt: str, tokenizer: Any, max_new_tokens:` | `str` | - |
| `_rate_key_extractor` |  | `self` | `-` | - |
| `_async_generate_text` | ✓ | `self, prompt: str, max_length: int, temperature: f` | `str` | @rate_limited(calls_per_second |
| `_generate_text_sync` |  | `self, pipeline_info: Dict[str, Any], prompt: str, ` | `str` | - |
| `_prepare_prompt_with_history` | ✓ | `self, prompt_type: str, context: Dict[str, Any], q` | `str` | - |
| `_validate_session` | ✓ | `self, session_id: str` | `bool` | - |
| `_validate_request_inputs` | ✓ | `self, query: str, context: Optional[Dict[str, Any]` | `Tuple[str, Dict[str, Any], Dic` | - |
| `_perform_inference_with_fallback` | ✓ | `self, task_type: str, prompt: str, sanitized_query` | `Tuple[str, str]` | - |
| `_finalize_request` | ✓ | `self, task_type: str, sanitized_query: str, saniti` | `Dict[str, Any]` | - |
| `_handle_request` | ✓ | `self, task_type: str, query: str, context: Optiona` | `Dict[str, Any]` | - |
| `_build_history_string` |  | `self, history_entries: List[Dict[str, Any]], sessi` | `str` | - |
| `explain` | ✓ | `self, query: str, context: Optional[Dict[str, Any]` | `Dict[str, Any]` | - |
| `reason` | ✓ | `self, query: str, context: Optional[Dict[str, Any]` | `Dict[str, Any]` | - |
| `batch_explain` | ✓ | `self, queries: List[str], contexts: List[Optional[` | `List[Union[Dict[str, Any], Dic` | - |
| `health_check` | ✓ | `self` | `Dict[str, Any]` | - |
| `get_history` | ✓ | `self, limit: int, session_id: Optional[str]` | `List[Dict[str, Any]]` | - |
| `clear_history` | ✓ | `self, session_id: Optional[str]` | `None` | - |
| `purge_history` | ✓ | `self, operator_id: str` | `None` | - |
| `export_history` | ✓ | `self, output_format: str, operator_id: str` | `AsyncGenerator[Union[str, byte` | - |
| `shutdown` | ✓ | `self` | `None` | - |

### Class: `PlugInKind`

**Class Variables:** `AI_ASSISTANT`, `FIX`

### Class: `ExecuteInput`
**Inherits:** BaseModel

**Class Variables:** `action`, `query`, `context`, `session_id`, `user_id`, `auth_token`, `queries`, `contexts`, `session_ids`, `user_ids`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `validate_for_action` |  | `cls, data: Dict[str, Any]` | `Self` | @classmethod |

### Class: `ExplainableReasonerPlugin`
**Inherits:** ExplainableReasoner
**Decorators:** @plugin(PlugInKind.AI_ASSISTANT, name='explainable_reasoner', description='Generates explanations and reasoning with transformer-based models', version='1.2.0')
**Description:** Plugin wrapper for ExplainableReasoner, exposing functionalities as actions.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, settings: Any` | `-` | - |
| `initialize` | ✓ | `self` | `-` | - |
| `execute` | ✓ | `self, action: str` | `Any` | - |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `plugin` |  | `kind, name, description, version` | `-` | - |

---

## explainable_reasoner/history_manager.py

**Lines:** 1432

### Constants

| Name |
|------|
| `logger` |

### Class: `BaseHistoryManager`
**Inherits:** ABC
**Description:** Abstract base class for managing history entries.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, max_history_size: int, retention_days: int, ` | `-` | - |
| `_pre_add_entry_checks` | ✓ | `self, entry: Dict[str, Any]` | `None` | - |
| `_encrypt` |  | `self, text: str` | `str` | - |
| `_decrypt` |  | `self, encrypted_text: str` | `str` | - |
| `_record_op_success` |  | `self, operation: str, start_time: float` | `-` | - |
| `_record_op_error` |  | `self, operation: str, start_time: float, e: Except` | `-` | - |
| `_log_audit_event` | ✓ | `self, event_type: str, details: Dict, operator: st` | `-` | - |
| `init_db` | ✓ | `self` | `None` | @abstractmethod |
| `add_entry` | ✓ | `self, entry: Dict[str, Any]` | `None` | @abstractmethod |
| `add_entries_batch` | ✓ | `self, entries: List[Dict[str, Any]]` | `None` | @abstractmethod |
| `get_entries` | ✓ | `self, limit: int, session_id: Optional[str]` | `List[Dict[str, Any]]` | @abstractmethod |
| `get_size` | ✓ | `self` | `int` | @abstractmethod |
| `prune_old_entries` | ✓ | `self` | `None` | @abstractmethod |
| `clear` | ✓ | `self, session_id: Optional[str]` | `None` | @abstractmethod |
| `purge_all` | ✓ | `self, operator_id: str` | `None` | @abstractmethod |
| `export_history` | ✓ | `self, output_format: str, operator_id: str` | `AsyncGenerator[Union[str, byte` | @abstractmethod |
| `aclose` | ✓ | `self` | `None` | @abstractmethod |

### Class: `SQLiteHistoryManager`
**Inherits:** BaseHistoryManager
**Description:** SQLite implementation of history manager.

**Class Variables:** `_backend_name`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, db_path: Path, max_history_size: int, retent` | `-` | - |
| `init_db` | ✓ | `self` | `None` | - |
| `add_entry` | ✓ | `self, entry: Dict[str, Any]` | `None` | - |
| `add_entries_batch` | ✓ | `self, entries: List[Dict[str, Any]]` | `None` | - |
| `get_entries` | ✓ | `self, limit: int, session_id: Optional[str]` | `List[Dict[str, Any]]` | - |
| `get_size` | ✓ | `self` | `int` | - |
| `prune_old_entries` | ✓ | `self` | `None` | - |
| `clear` | ✓ | `self, session_id: Optional[str]` | `None` | - |
| `purge_all` | ✓ | `self, operator_id: str` | `None` | - |
| `export_history` | ✓ | `self, output_format: str, operator_id: str` | `AsyncGenerator[Union[str, byte` | - |
| `aclose` | ✓ | `self` | `None` | - |

### Class: `PostgresHistoryManager`
**Inherits:** BaseHistoryManager
**Description:** Postgres implementation of history manager with response encryption.

**Class Variables:** `_backend_name`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, db_url: str, max_history_size: int, retentio` | `-` | - |
| `init_db` | ✓ | `self` | `None` | - |
| `add_entry` | ✓ | `self, entry: Dict[str, Any]` | `None` | - |
| `add_entries_batch` | ✓ | `self, entries: List[Dict[str, Any]]` | `None` | - |
| `get_entries` | ✓ | `self, limit: int, session_id: Optional[str]` | `List[Dict[str, Any]]` | - |
| `get_size` | ✓ | `self` | `int` | - |
| `prune_old_entries` | ✓ | `self` | `None` | - |
| `clear` | ✓ | `self, session_id: Optional[str]` | `None` | - |
| `purge_all` | ✓ | `self, operator_id: str` | `None` | - |
| `export_history` | ✓ | `self, output_format: str, operator_id: str` | `AsyncGenerator[Union[str, byte` | - |
| `aclose` | ✓ | `self` | `None` | - |

### Class: `RedisHistoryManager`
**Inherits:** BaseHistoryManager
**Description:** Redis implementation of history manager using a sorted set.

**Class Variables:** `_backend_name`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, redis_url: str, max_history_size: int, reten` | `-` | - |
| `init_db` | ✓ | `self` | `None` | - |
| `add_entry` | ✓ | `self, entry: Dict[str, Any]` | `None` | - |
| `add_entries_batch` | ✓ | `self, entries: List[Dict[str, Any]]` | `None` | - |
| `get_entries` | ✓ | `self, limit: int, session_id: Optional[str]` | `List[Dict[str, Any]]` | - |
| `get_size` | ✓ | `self` | `int` | - |
| `prune_old_entries` | ✓ | `self` | `None` | - |
| `clear` | ✓ | `self, session_id: Optional[str]` | `None` | - |
| `purge_all` | ✓ | `self, operator_id: str` | `None` | - |
| `export_history` | ✓ | `self, output_format: str, operator_id: str` | `AsyncGenerator[Union[str, byte` | - |
| `aclose` | ✓ | `self` | `None` | - |

---

## explainable_reasoner/metrics.py

**Lines:** 232

### Constants

| Name |
|------|
| `_metrics_logger` |
| `_metrics_logger` |
| `METRICS_NAMESPACE` |
| `PROMETHEUS_MULTIPROC_DIR` |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `get_or_create_metric` |  | `metric_type: Type, name: str, description: str, la` | `-` | - |
| `initialize_metrics` |  | `` | `-` | - |
| `get_metrics_content` |  | `` | `bytes` | - |

---

## explainable_reasoner/prompt_strategies.py

**Lines:** 528

### Constants

| Name |
|------|
| `_prompt_strategy_logger` |

### Class: `PromptStrategy`
**Inherits:** ABC
**Description:** Abstract base class for prompt generation strategies.

Each strategy is responsible for formatting input data (context, goal, history)
into a coherent prompt string for a language model. The methods are asynchronous
to support potential future I/O operations (e.g., retrieving data from a remote
cach...

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, logger_instance: Union[logging.Logger, struc` | `-` | - |
| `create_explanation_prompt` | ✓ | `self, context: Dict[str, Any], goal: str, history_` | `str` | @abstractmethod |
| `create_reasoning_prompt` | ✓ | `self, context: Dict[str, Any], goal: str, history_` | `str` | @abstractmethod |

### Class: `DefaultPromptStrategy`
**Inherits:** PromptStrategy
**Description:** Default prompt strategy with moderately detailed, balanced templates.
This strategy is designed to work well with general-purpose language models.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `create_explanation_prompt` | ✓ | `self, context: Dict[str, Any], goal: str, history_` | `str` | - |
| `create_reasoning_prompt` | ✓ | `self, context: Dict[str, Any], goal: str, history_` | `str` | - |

### Class: `ConcisePromptStrategy`
**Inherits:** PromptStrategy
**Description:** Concise prompt strategy for shorter, more direct interactions.
This is ideal for use cases where speed or token limits are critical.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `create_explanation_prompt` | ✓ | `self, context: Dict[str, Any], goal: str, history_` | `str` | - |
| `create_reasoning_prompt` | ✓ | `self, context: Dict[str, Any], goal: str, history_` | `str` | - |

### Class: `VerbosePromptStrategy`
**Inherits:** PromptStrategy
**Description:** Verbose prompt strategy designed to elicit detailed, comprehensive responses.
This is best suited for models with large context windows and tasks
requiring in-depth analysis.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `create_explanation_prompt` | ✓ | `self, context: Dict[str, Any], goal: str, history_` | `str` | - |
| `create_reasoning_prompt` | ✓ | `self, context: Dict[str, Any], goal: str, history_` | `str` | - |

### Class: `StructuredPromptStrategy`
**Inherits:** PromptStrategy
**Description:** Structured prompt strategy for models that can reliably generate JSON.
This is crucial for downstream processing and automation.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `create_explanation_prompt` | ✓ | `self, context: Dict[str, Any], goal: str, history_` | `str` | - |
| `create_reasoning_prompt` | ✓ | `self, context: Dict[str, Any], goal: str, history_` | `str` | - |

### Class: `PromptStrategyFactory`
**Description:** A factory class for creating and managing PromptStrategy instances.

**Class Variables:** `_strategies`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `register_strategy` |  | `cls, name: str, strategy_class: Type[PromptStrateg` | `-` | @classmethod |
| `get_strategy` |  | `cls, name: str, logger_instance: Union[logging.Log` | `PromptStrategy` | @classmethod |
| `list_strategies` |  | `cls` | `List[str]` | @classmethod |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `_truncate_context` |  | `context: Dict[str, Any], max_len: int` | `str` | - |

---

## explainable_reasoner/reasoner_config.py

**Lines:** 172

### Constants

| Name |
|------|
| `logger` |

### Class: `SensitiveValue`
**Description:** A wrapper for sensitive values to prevent accidental logging.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, value: str` | `-` | - |
| `get_actual_value` |  | `self` | `str` | - |
| `__str__` |  | `self` | `str` | - |
| `__repr__` |  | `self` | `str` | - |

### Class: `ReasonerConfig`
**Inherits:** BaseModel
**Description:** Configuration for the Reasoner, loaded from defaults, files, or environment variables.

**Class Variables:** `model_config`, `model_name`, `device`, `max_workers`, `timeout`, `max_generation_tokens`, `temperature_explain`, `temperature_reason`, `temperature_neutral`, `temperature_negative`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `from_env` |  | `cls` | `-` | @classmethod |
| `validate_dependencies` |  | `self` | `-` | @model_validator(mode='after') |
| `get_public_config` |  | `self` | `Dict[str, Any]` | - |
| `from_file` |  | `cls, file_path: Union[str, Path]` | `'ReasonerConfig'` | @classmethod |

---

## explainable_reasoner/reasoner_errors.py

**Lines:** 255

### Constants

| Name |
|------|
| `logger` |

### Class: `ReasonerErrorCode`
**Description:** A collection of standard, structured error codes for the Reasoner application.
Using a class with attributes provides a clear, discoverable, and auto-completable list of codes.

**Class Variables:** `GENERIC_ERROR`, `UNEXPECTED_ERROR`, `INVALID_INPUT`, `TIMEOUT`, `SERVICE_UNAVAILABLE`, `CONFIGURATION_ERROR`, `RATE_LIMIT_EXCEEDED`, `CUDA_OOM`, `MODEL_NOT_INITIALIZED`, `CONTEXT_SIZE_EXCEEDED`

### Class: `ReasonerError`
**Inherits:** Exception
**Description:** A custom exception for the Reasoner application that includes a structured
error code and a user-friendly message. It automatically logs itself upon
creation and can hold a reference to the original underlying exception.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, message: str, code: str, original_exception:` | `-` | - |
| `__repr__` |  | `self` | `str` | - |
| `to_api_response` |  | `self, include_traceback: bool` | `Dict[str, Any]` | - |
| `to_json` |  | `self, indent: Optional[int]` | `str` | - |

---

## explainable_reasoner/utils.py

**Lines:** 496

### Constants

| Name |
|------|
| `METRICS` |
| `_utils_logger` |
| `P` |

### Class: `DummyCounter`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `labels` |  | `self` | `-` | - |
| `inc` |  | `self, value` | `-` | - |

### Class: `DummyHistogram`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `labels` |  | `self` | `-` | - |
| `observe` |  | `self, value` | `-` | - |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `_format_multimodal_for_prompt` |  | `data: Union[ImageAnalysisResult, AudioAnalysisResu` | `str` | - |
| `_sanitize_context` | ✓ | `context: Dict[str, Any], config: ReasonerConfig` | `Dict[str, Any]` | - |
| `_simple_text_sanitize` |  | `text: str, max_length: int` | `str` | - |
| `_rule_based_fallback` |  | `query: str, context: Dict[str, Any], mode: str` | `str` | - |
| `rate_limited` |  | `calls_per_second: float, key_extractor: Optional[C` | `-` | - |
| `redact_pii` |  | `data` | `-` | - |

---

## explorer.py

**Lines:** 1459
**Description:** Explorer: A system for managing and logging agent experiments within a sandboxed environment.

This module provides functionalities for running various types of experiments,
such as A/B tests and evol...

### Constants

| Name |
|------|
| `Base` |
| `tracer` |
| `logger` |
| `explorer_ops_total` |
| `explorer_errors_total` |

### Class: `ExperimentExecutionError`
**Inherits:** Exception
**Description:** Custom exception raised when an experiment fails to execute.

### Class: `ExperimentLog`
**Inherits:** Base
**Description:** SQLAlchemy model for experiment logs.

**Class Variables:** `__tablename__`, `id`, `data`, `timestamp`

### Class: `LogDB`
**Description:** A production-ready database for storing experiment logs.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, config: ArbiterConfig` | `-` | - |
| `save_experiment_log` | ✓ | `self, log_entry: Dict[str, Any]` | `-` | - |
| `get_experiment_log` | ✓ | `self, experiment_id: str` | `Optional[Dict[str, Any]]` | - |
| `find_experiments` | ✓ | `self, query: Dict[str, Any]` | `List[Dict[str, Any]]` | - |
| `health_check` | ✓ | `self` | `Dict[str, Any]` | - |

### Class: `MutatedAgent`
**Description:** Represents an agent that has been mutated from a base agent.
This class is top-level for better extensibility and serialization.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, original_name: str, generation: int` | `-` | - |
| `test_in_sandbox` | ✓ | `self` | `Dict[str, Any]` | - |

### Class: `MySandboxEnv`
**Description:** A mock sandbox environment for evaluating and testing agents.
Replace with your actual simulation or testing environment.

NOTE: This is a fallback mock. Use RealSandboxAdapter for production.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `evaluate` | ✓ | `self, variant: Any, metric: Optional[str]` | `float` | - |
| `test_agent` | ✓ | `self, agent: Any` | `Dict[str, Any]` | - |

### Class: `RealSandboxAdapter`
**Description:** Adapter that wraps the real sandbox.py for use with Explorer.
Integrates simulation/sandbox.py with Explorer's interface.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, backend: str, workdir: Optional[str]` | `-` | - |
| `evaluate` | ✓ | `self, variant: Any, metric: Optional[str]` | `float` | - |
| `test_agent` | ✓ | `self, agent: Any` | `Dict[str, Any]` | - |
| `get_stats` |  | `self` | `Dict[str, Any]` | - |

### Class: `Explorer`
**Description:** Manages agent experimentation within a sandboxed environment.
Provides a unified API for running various experiment types and logs results for traceability.
This class is thread-safe.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, sandbox_env: Any, log_db: Optional[LogDB], c` | `-` | - |
| `__aenter__` | ✓ | `self` | `-` | - |
| `__aexit__` | ✓ | `self, exc_type, exc_val, exc_tb` | `-` | - |
| `check_permission` |  | `self, role: str, permission: str` | `bool` | - |
| `execute` | ✓ | `self, action: str` | `Dict[str, Any]` | - |
| `run_experiment` | ✓ | `self, experiment_config: Dict[str, Any]` | `Dict[str, Any]` | - |
| `get_status` | ✓ | `self` | `Dict[str, Any]` | - |
| `discover_urls` | ✓ | `self, html_discovery_dir: str` | `List[str]` | - |
| `crawl_urls` | ✓ | `self, urls: List[str]` | `Dict[str, Any]` | - |
| `explore_and_fix` | ✓ | `self, arbiter, fix_paths: Optional[List[str]]` | `Dict[str, Any]` | - |
| `replay_experiment` | ✓ | `self, experiment_id: str, new_sandbox_env: Optiona` | `Dict[str, Any]` | - |
| `_run_ab_test` | ✓ | `self, experiment_id: str, variant_a_agent: Any, va` | `Dict[str, Any]` | - |
| `_run_evolution_experiment` | ✓ | `self, experiment_id: str, initial_population: List` | `Dict[str, Any]` | - |
| `_create_mutated_agent` |  | `self, base_agent: Any, generation: int` | `MutatedAgent` | - |
| `_generate_experiment_id` |  | `self, kind: str` | `str` | - |
| `_calculate_metrics` |  | `self, runs_data: List[Dict[str, Any]], metrics: Op` | `Dict[str, Any]` | - |
| `_compare_variants` |  | `self, metrics_a: Dict[str, Any], metrics_b: Dict[s` | `Dict[str, Any]` | - |

### Class: `MockLogDB`
**Description:** Mock database for testing - implements the same interface as LogDB

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self` | `-` | - |
| `save_experiment_log` | ✓ | `self, log_entry: Dict[str, Any]` | `-` | - |
| `get_experiment_log` | ✓ | `self, experiment_id: str` | `Optional[Dict[str, Any]]` | - |
| `find_experiments` | ✓ | `self, query: Dict[str, Any]` | `List[Dict[str, Any]]` | - |
| `health_check` | ✓ | `self` | `Dict[str, Any]` | - |

### Class: `ArbiterExplorer`
**Description:** Explorer for arbiter system with experiment management capabilities

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, sandbox_env: Any, log_db: Optional[Union[Log` | `-` | - |
| `run_ab_test` | ✓ | `self, experiment_name: str, variant_a: Any, varian` | `Dict[str, Any]` | - |
| `run_evolutionary_experiment` | ✓ | `self, experiment_name: str, initial_agent: Any, nu` | `Dict[str, Any]` | - |
| `_run_experiment` | ✓ | `self, name: str, experiment_func: Callable` | `Dict[str, Any]` | - |
| `_log_experiment` | ✓ | `self, entry: Dict[str, Any]` | `-` | - |
| `_calculate_metrics` |  | `self, results: List[Dict[str, Any]]` | `Dict[str, Any]` | - |
| `_compare_variants` |  | `self, metrics_a: Dict[str, Any], metrics_b: Dict[s` | `Dict[str, Any]` | - |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `_serialize_random_state` |  | `state: tuple` | `str` | - |
| `_deserialize_random_state` |  | `state_str: str` | `tuple` | - |

---

## feedback.py

**Lines:** 1043

### Constants

| Name |
|------|
| `POSTGRES_AVAILABLE` |
| `IS_PRODUCTION` |
| `tracer` |
| `logger` |
| `_metrics_lock` |
| `feedback_received_total` |
| `feedback_errors_total` |
| `feedback_metrics_recorded_total` |
| `feedback_processing_time` |
| `human_in_loop_approvals` |
| `human_in_loop_denials` |
| `last_feedback_timestamp` |
| `feedback_ops_total` |
| `_feedback_plugin_registered` |

### Class: `FeedbackType`
**Description:** Enumeration of feedback types

**Class Variables:** `BUG_REPORT`, `FEATURE_REQUEST`, `GENERAL`, `APPROVAL`, `DENIAL`, `IMPROVEMENT`, `ISSUE`

### Class: `FeedbackLog`
**Inherits:** Base
**Description:** SQLAlchemy model for feedback logs.

**Class Variables:** `__tablename__`, `decision_id`, `data`, `timestamp`

### Class: `SQLiteClient`
**Description:** Asynchronous client for SQLite database interactions.
This client uses aiosqlite and is designed to be used with async context managers.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, db_file: str` | `-` | - |
| `connect` | ✓ | `self` | `-` | - |
| `disconnect` | ✓ | `self` | `-` | - |
| `save_feedback_entry` | ✓ | `self, entry: Dict[str, Any]` | `-` | - |
| `get_feedback_entries` | ✓ | `self, query: Optional[Dict[str, Any]]` | `List[Dict[str, Any]]` | - |
| `update_feedback_entry` | ✓ | `self, query: Dict[str, Any], updates: Dict[str, An` | `bool` | - |

### Class: `FeedbackManager`
**Description:** Collects and summarizes metrics, error logs, and user feedback for any agent/arena.
Designed to be used as a utility in Arbiter, Arena, or globally.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, db_client: Optional[Union[SQLiteClient, Post` | `-` | - |
| `connect_db` | ✓ | `self` | `-` | - |
| `disconnect_db` | ✓ | `self` | `-` | - |
| `_ensure_log_file_exists` |  | `self` | `-` | - |
| `_sync_and_filter_to_logfile` | ✓ | `self` | `-` | - |
| `add_feedback` | ✓ | `self, decision_id: str, feedback: Dict[str, Any]` | `None` | @retry(stop=stop_after_attempt |
| `record_metric` | ✓ | `self, name: str, value: float, tags: Optional[Dict` | `-` | - |
| `log_error` | ✓ | `self, error_info: Dict[str, Any]` | `-` | - |
| `add_user_feedback` | ✓ | `self, feedback: Dict[str, Any]` | `-` | - |
| `record_feedback` | ✓ | `self, user_id: str, feedback_type: Optional[str], ` | `None` | - |
| `_purge_metrics_and_sync_loop` | ✓ | `self` | `-` | - |
| `get_summary` | ✓ | `self` | `Dict[str, Any]` | - |
| `log_approval_request` | ✓ | `self, decision_id: str, decision_context: Dict[str` | `-` | - |
| `log_approval_response` | ✓ | `self, decision_id: str, response: Dict[str, Any]` | `-` | - |
| `get_pending_approvals` | ✓ | `self` | `List[Dict[str, Any]]` | - |
| `get_feedback_by_decision_id` | ✓ | `self, decision_id: str` | `List[Dict[str, Any]]` | - |
| `get_approval_stats` | ✓ | `self, start_date: Optional[datetime], end_date: Op` | `Dict[str, Any]` | - |
| `start_async_services` | ✓ | `self` | `-` | - |
| `stop_async_services` | ✓ | `self` | `-` | - |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `_get_arbiter_registry` |  | `` | `-` | - |
| `_get_or_create_metric` |  | `metric_class: Union[Type[Counter], Type[Gauge], Ty` | `-` | - |
| `check_permission` |  | `role: str, permission: str` | `-` | - |
| `receive_human_feedback` | ✓ | `feedback: Dict[str, Any]` | `None` | - |
| `_ensure_feedback_plugin_registered` |  | `` | `-` | - |

---

## file_watcher.py

**Lines:** 1584

### Constants

| Name |
|------|
| `logger` |
| `logger` |
| `processed_files` |
| `errors` |
| `deployments` |
| `notifications` |
| `emails_sent` |
| `SUMMARY_LATENCY` |
| `_METRICS_LOCK` |
| `lock_file` |
| `app` |
| `start_time` |

### Class: `SMTPConfig`
**Inherits:** BaseModel

**Class Variables:** `host`, `port`, `username`, `password`, `use_tls`, `timeout`, `rate_limit`

### Class: `AlerterConfig`
**Inherits:** BaseModel

**Class Variables:** `smtp`, `audit_file`

### Class: `AWSConfig`
**Inherits:** BaseModel

**Class Variables:** `bucket`, `region`, `access_key_id`, `secret_access_key`

### Class: `LLMConfig`
**Inherits:** BaseModel

**Class Variables:** `provider`, `openai_api_key`, `ollama_url`, `ollama_model`, `anthropic_api_key`, `gemini_api_key`, `model`, `prompt_template`, `max_code_size`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `validate_provider` |  | `cls, v` | `-` | @field_validator('provider'),  |

### Class: `DeployConfig`
**Inherits:** BaseModel

**Class Variables:** `command`, `rollback_command`, `ci_cd_url`, `ci_cd_token`, `webhook_urls`, `aws_s3`

### Class: `ReportingConfig`
**Inherits:** BaseModel

**Class Variables:** `changelog_file`, `formats`

### Class: `CacheConfig`
**Inherits:** BaseModel

**Class Variables:** `redis_url`, `pool_size`, `ttl`

### Class: `MetricsConfig`
**Inherits:** BaseModel

**Class Variables:** `prometheus_port`, `auth_token`

### Class: `HealthConfig`
**Inherits:** BaseModel

**Class Variables:** `port`

### Class: `WatchConfig`
**Inherits:** BaseModel

**Class Variables:** `folder`, `extensions`, `skip_patterns`, `cooldown_seconds`, `batch_mode`, `batch_schedule`

### Class: `ApiConfig`
**Inherits:** BaseModel

**Class Variables:** `upload_url`, `rate_limit`

### Class: `Config`
**Inherits:** BaseModel

**Class Variables:** `watch`, `llm`, `api`, `deploy`, `reporting`, `cache`, `metrics`, `health`, `alerter`

### Class: `CodeChangeHandler`
**Inherits:** FileSystemEventHandler
**Description:** Handles file system events for watched directories.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, semaphore: asyncio.Semaphore` | `-` | - |
| `process_file` | ✓ | `self, filepath: str` | `None` | - |
| `on_modified` |  | `self, event` | `None` | - |
| `on_created` |  | `self, event` | `None` | - |
| `on_deleted` |  | `self, event` | `None` | - |
| `on_moved` |  | `self, event` | `None` | - |

### Class: `MetricsAndHealthServer`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, config: Config` | `-` | - |
| `start` | ✓ | `self` | `-` | - |
| `stop` | ✓ | `self` | `-` | - |
| `prometheus_metrics_handler` | ✓ | `self, request: web.Request` | `-` | - |
| `health_check_handler` | ✓ | `self, request: web.Request` | `-` | - |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `_get_or_create_metric` |  | `metric_class: type, name: str, doc: str, labelname` | `-` | - |
| `_safe_int` |  | `value: str, default: int` | `int` | - |
| `_safe_float` |  | `value: str, default: float` | `float` | - |
| `load_config_with_env` |  | `config_path: Optional[str]` | `Config` | - |
| `is_valid_file` |  | `filename: str` | `bool` | - |
| `read_file` | ✓ | `filepath: str` | `Optional[str]` | - |
| `get_cached_summary` | ✓ | `filename: str, content: str` | `Optional[str]` | - |
| `cache_summary` | ✓ | `filename: str, content: str, summary: str` | `None` | - |
| `log_audit` | ✓ | `event: str, details: Dict[str, Any]` | `None` | - |
| `summarize_code` | ✓ | `filename: str, code: str` | `str` | @retry(stop=stop_after_attempt |
| `send_to_api` | ✓ | `filename: str, content: str, summary: str` | `bool` | @retry(stop=stop_after_attempt |
| `send_email_alert` | ✓ | `subject: str, body: str` | `None` | @retry(stop=stop_after_attempt |
| `upload_to_s3` | ✓ | `filename: str, content: str` | `bool` | - |
| `send_notification` | ✓ | `filename: str, status: str, summary: str` | `None` | - |
| `trigger_deployment` | ✓ | `filename: str, content: str` | `bool` | - |
| `write_changelog` | ✓ | `filename: str, summary: str, old_content: Optional` | `None` | - |
| `summarize_code_changes` | ✓ | `diff: str, prompt_template: str` | `str` | - |
| `compare_diffs` |  | `old: str, new: str` | `str` | - |
| `batch_process` | ✓ | `semaphore: asyncio.Semaphore` | `None` | - |
| `start_watch` | ✓ | `config_path: Optional[str]` | `None` | - |
| `watch` | ✓ | `config_path: Optional[str]` | `None` | - |
| `register_plugin` |  | `` | `-` | - |
| `run` |  | `config_path: Optional[str]` | `-` | @app.command() |
| `batch` |  | `config_path: Optional[str]` | `-` | @app.command() |
| `send_slack_alert` | ✓ | `message: str, webhook_url: str` | `-` | @retry(stop=stop_after_attempt |
| `send_pagerduty_alert` | ✓ | `message: str, routing_key: str` | `-` | @retry(stop=stop_after_attempt |
| `deploy_code` | ✓ | `cmd: str` | `dict` | - |
| `notify_changes` | ✓ | `filename: str, diff: str, summary: str, deploy_res` | `None` | - |
| `process_file` | ✓ | `path: str` | `Optional[dict]` | - |

---

## human_loop.py

**Lines:** 1357

### Constants

| Name |
|------|
| `SECRET_SALT` |
| `tracer` |
| `logger` |
| `human_in_loop_approvals` |
| `human_in_loop_denials` |
| `human_loop_feedback_total` |

### Class: `FeedbackManager`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, db_client: Union[DummyDBClient, PostgresClie` | `None` | - |
| `log_approval_request` | ✓ | `self, decision_id: str, decision_context: Dict[str` | `None` | - |
| `log_approval_response` | ✓ | `self, decision_id: str, response: Dict[str, Any]` | `None` | - |
| `record_metric` | ✓ | `self, metric_name: str, value: Union[int, float], ` | `None` | - |
| `log_error` | ✓ | `self, error_details: Dict[str, Any]` | `None` | - |

### Class: `HumanFeedbackSchema`
**Inherits:** BaseModel

**Class Variables:** `decision_id`, `approved`, `user_id`, `signature`, `comment`, `timestamp`

### Class: `DecisionRequestSchema`
**Inherits:** BaseModel

**Class Variables:** `decision_id`, `cycle`, `risk_level`, `required_role`, `timeout_seconds`, `action`, `details`, `model_config`

### Class: `HumanInLoopConfig`
**Inherits:** BaseModel

**Class Variables:** `DATABASE_URL`, `EMAIL_ENABLED`, `EMAIL_SMTP_SERVER`, `EMAIL_SMTP_PORT`, `EMAIL_SMTP_USER`, `EMAIL_SMTP_PASSWORD`, `EMAIL_SENDER`, `EMAIL_USE_TLS`, `EMAIL_RECIPIENTS`, `SLACK_WEBHOOK_URL`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `validate_production_email_config` |  | `cls, values` | `-` | @model_validator(mode='before' |
| `validate_database_url_in_production` |  | `cls, v, info` | `-` | @field_validator('DATABASE_URL |
| `validate_salt_in_production` |  | `self` | `-` | @model_validator(mode='after') |

### Class: `WebSocketManager`
**Description:** WebSocket connection manager for real-time communication with UI clients.

Supports multiple concurrent connections, automatic reconnection,
connection state tracking, and integration with FastAPI WebSocket endpoints.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, max_connections: int` | `-` | - |
| `start` | ✓ | `self` | `None` | - |
| `stop` | ✓ | `self` | `None` | - |
| `register_connection` | ✓ | `self, connection_id: str, websocket: Any, metadata` | `bool` | - |
| `unregister_connection` | ✓ | `self, connection_id: str` | `None` | - |
| `send_json` | ✓ | `self, data: Dict[str, Any], connection_id: Optiona` | `None` | - |
| `_broadcast_worker` | ✓ | `self` | `None` | - |
| `get_connection_count` |  | `self` | `int` | - |
| `get_connection_stats` |  | `self` | `Dict[str, Any]` | - |
| `ping_all` | ✓ | `self` | `Dict[str, bool]` | - |

### Class: `HumanInLoop`
**Description:** Human-in-the-loop approval and feedback pipeline with secure validation,
multi-channel notification/escalation, hooks, and gold-standard testability.

**Class Variables:** `mock_approval_delay_seconds`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, config: HumanInLoopConfig, feedback_manager:` | `None` | - |
| `__aenter__` | ✓ | `self` | `-` | - |
| `__aexit__` | ✓ | `self, exc_type, exc_val, exc_tb` | `-` | - |
| `check_permission` |  | `self, role: str, permission: str` | `bool` | - |
| `_handle_hook` | ✓ | `self, hook: Optional[Callable[[Dict[str, Any]], Aw` | `None` | - |
| `request_approval` | ✓ | `self, decision: Dict[str, Any]` | `Dict[str, Any]` | - |
| `_get_notification_tasks` |  | `self, decision_id: str, context: Dict[str, Any], r` | `List[Awaitable[None]]` | - |
| `receive_human_feedback` | ✓ | `self, feedback: Dict[str, Any]` | `None` | @retry(stop=stop_after_attempt |
| `_validate_user_signature` | ✓ | `self, user_id: str, signature: str, decision_id: s` | `bool` | - |
| `_send_email_approval` | ✓ | `self, decision_id: str, context: Dict[str, Any], r` | `None` | - |
| `_send_sync_email` |  | `self, config: HumanInLoopConfig, recipient: str, m` | `-` | - |
| `_post_slack_approval` | ✓ | `self, decision_id: str, context: Dict[str, Any]` | `None` | - |
| `_notify_ui_approval` | ✓ | `self, decision_id: str, context: Dict[str, Any]` | `None` | - |
| `_mock_user_approval` | ✓ | `self, decision_id: str, decision_context: Dict[str` | `None` | - |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `get_human_approval` | ✓ | `decision_id: str, decision_context: Dict[str, Any]` | `Dict[str, Any]` | - |

---

## knowledge_graph/config.py

**Lines:** 554

### Constants

| Name |
|------|
| `logger` |
| `env` |
| `SensitiveString` |

### Class: `SensitiveValue`
**Inherits:** RootModel[str]
**Description:** A Pydantic wrapper for sensitive string values that automatically redacts
itself when serialized to JSON or other formats.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__str__` |  | `self` | `str` | - |
| `__repr__` |  | `self` | `str` | - |
| `get_actual_value` |  | `self` | `str` | - |
| `__hash__` |  | `self` | `int` | - |
| `__eq__` |  | `self, other` | `bool` | - |
| `__get_pydantic_json_schema__` |  | `cls, core_schema, handler` | `-` | @classmethod |
| `model_dump` |  | `self` | `-` | - |
| `model_dump_json` |  | `self` | `-` | - |

### Class: `MetaLearningConfig`
**Inherits:** BaseSettings
**Description:** Configuration settings for the Meta-Learning Orchestrator.
Settings are loaded from environment variables (prefixed with ML_) or .env file.

**Class Variables:** `model_config`, `DATA_LAKE_PATH`, `DATA_LAKE_S3_BUCKET`, `DATA_LAKE_S3_PREFIX`, `USE_S3_DATA_LAKE`, `AUDIT_LEDGER_URL`, `LOCAL_AUDIT_LOG_PATH`, `AUDIT_ENCRYPTION_KEY`, `AUDIT_SIGNING_PRIVATE_KEY`, `AUDIT_SIGNING_PUBLIC_KEY`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `validate_file_paths` |  | `cls, v, info: ValidationInfo` | `-` | @field_validator('DATA_LAKE_PA |
| `handle_sensitive_values` |  | `cls, v` | `-` | @field_validator('AUDIT_ENCRYP |
| `validate_kafka_settings` |  | `self` | `-` | @model_validator(mode='after') |
| `validate_redis_url` |  | `cls, v` | `-` | @field_validator('REDIS_URL') |
| `validate_http_endpoints` |  | `cls, v` | `-` | @field_validator('ML_PLATFORM_ |
| `reload_config` |  | `self` | `-` | - |
| `_reload_from_etcd` |  | `self` | `-` | - |

### Class: `MultiModalData`
**Inherits:** BaseModel

**Class Variables:** `model_config`, `data_type`, `data`, `metadata`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `model_dump_for_log` |  | `self` | `Dict[str, Any]` | - |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `load_persona_dict` |  | `` | `Dict[str, str]` | - |

---

## knowledge_graph/core.py

**Lines:** 1636

### Constants

| Name |
|------|
| `AuditLedgerClient` |
| `RedisClient` |
| `tracer` |

### Class: `StateBackend`
**Inherits:** ABC
**Description:** Abstract Base Class for state backends.
Expected state is a JSON-serializable dictionary with keys like 'history', 'persona', 'language'.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `load_state` | ✓ | `self, session_id: str` | `Optional[Dict[str, Any]]` | @abstractmethod |
| `save_state` | ✓ | `self, session_id: str, state: Dict[str, Any]` | `None` | @abstractmethod |

### Class: `RedisStateBackend`
**Inherits:** StateBackend

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, redis_url` | `-` | - |
| `init_client` | ✓ | `self` | `-` | - |
| `save_state` | ✓ | `self, session_id: str, state: Dict[str, Any]` | `-` | - |
| `load_state` | ✓ | `self, session_id: str` | `Optional[Dict[str, Any]]` | - |

### Class: `PostgresStateBackend`
**Inherits:** StateBackend

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, db_url` | `-` | - |
| `init_client` | ✓ | `self` | `-` | - |
| `save_state` | ✓ | `self, session_id: str, state: Dict[str, Any]` | `-` | - |
| `load_state` | ✓ | `self, session_id: str` | `Optional[Dict[str, Any]]` | - |

### Class: `InMemoryStateBackend`
**Inherits:** StateBackend

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self` | `-` | - |
| `load_state` | ✓ | `self, session_id: str` | `Optional[Dict[str, Any]]` | - |
| `save_state` | ✓ | `self, session_id: str, state: Dict[str, Any]` | `None` | - |

### Class: `MetaLearning`
**Description:** A class for logging and applying self-correction feedback to the agent's responses.
This implementation uses a simple scikit-learn pipeline for demonstration.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self` | `-` | - |
| `log_correction` |  | `self, input_text: str, initial_response: str, corr` | `-` | - |
| `train_model` |  | `self` | `-` | - |
| `apply_correction` |  | `self, response: str, input_text: str` | `str` | - |
| `persist` |  | `self` | `-` | - |
| `load` |  | `self` | `-` | - |

### Class: `CollaborativeAgent`
**Description:** Core class for a self-correcting, stateful AI agent.

This agent uses a multi-step process for generating responses:
1. Initial LLM call to get a baseline response.
2. Self-reflection on the response.
3. Peer critique to identify weaknesses.
4. Self-correction to produce a final, refined response.
5...

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, agent_id: str, session_id: str, llm_config: ` | `-` | - |
| `_get_llm` |  | `self` | `Any` | - |
| `_get_fallback_llm` |  | `self` | `Optional[Any]` | - |
| `load_state` | ✓ | `self, operator_id: str` | `-` | - |
| `save_state` | ✓ | `self, operator_id: str` | `-` | - |
| `set_persona` | ✓ | `self, persona: str, operator_id: str` | `-` | - |
| `_call_llm_with_retries` | ✓ | `self, llm_instance: Any, messages: List[Any], prov` | `Any` | @sleep_and_retry, @limits(call |
| `predict` | ✓ | `self, user_input: str, context: Optional[Dict[str,` | `Dict[str, Any]` | - |

### Class: `AgentTeam`
**Description:** A class for managing a team of collaborative agents to handle complex tasks.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, session_id: str, llm_config: Dict[str, Any],` | `-` | - |
| `delegate_task` | ✓ | `self, initial_input: str, context: Optional[Dict[s` | `Dict[str, Any]` | - |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `get_transcript` |  | `memory: ConversationBufferWindowMemory` | `str` | - |
| `get_or_create_agent` | ✓ | `session_id: str, llm_config: Optional[Dict[str, An` | `CollaborativeAgent` | - |
| `setup_conversation` | ✓ | `llm: Any, persona: str, language: str` | `Tuple[ConversationChain, Conve` | - |

---

## knowledge_graph/multimodal.py

**Lines:** 503

### Class: `MultiModalProcessor`
**Inherits:** ABC
**Description:** Abstract base class for processing multi-modal data.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `summarize` | ✓ | `self, item: MultiModalData` | `Dict[str, Any]` | @abstractmethod |

### Class: `DefaultMultiModalProcessor`
**Inherits:** MultiModalProcessor
**Description:** A concrete implementation of the MultiModalProcessor.
Relies on external libraries for specific data types and includes caching, timeouts, metrics, and auditing.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, logger: logging.Logger` | `-` | - |
| `_ensure_models_initialized` | ✓ | `self` | `-` | - |
| `summarize` | ✓ | `self, item: MultiModalData` | `Dict[str, Any]` | - |
| `_process_image` | ✓ | `self, item: MultiModalData` | `Dict[str, Any]` | - |
| `_process_audio` | ✓ | `self, item: MultiModalData` | `Dict[str, Any]` | - |
| `_process_video` | ✓ | `self, item: MultiModalData` | `Dict[str, Any]` | - |
| `_process_text_file` | ✓ | `self, item: MultiModalData` | `Dict[str, Any]` | - |
| `_process_pdf_file` | ✓ | `self, item: MultiModalData` | `Dict[str, Any]` | - |

---

## knowledge_graph/prompt_strategies.py

**Lines:** 242

### Constants

| Name |
|------|
| `logger` |
| `PROMPT_TEMPLATES` |
| `PROMPT_TEMPLATE_FILE` |
| `PROMPT_TEMPLATES_FALLBACK` |
| `BASE_AGENT_PROMPT_TEMPLATE` |
| `REFLECTION_PROMPT_TEMPLATE` |
| `CRITIQUE_PROMPT_TEMPLATE` |
| `SELF_CORRECT_PROMPT_TEMPLATE` |

### Class: `PromptStrategy`
**Inherits:** ABC
**Description:** Abstract base class for defining prompt strategies.
This separates the logic for crafting the prompt from the core agent behavior.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, logger: logging.Logger` | `-` | - |
| `get_history_transcript` |  | `self` | `str` | - |
| `create_agent_prompt` | ✓ | `self, base_template: str, history: str, user_input` | `str` | @abstractmethod |

### Class: `DefaultPromptStrategy`
**Inherits:** PromptStrategy
**Description:** A basic prompt strategy that uses the default template with no frills.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `create_agent_prompt` | ✓ | `self, base_template: str, history: str, user_input` | `str` | - |

### Class: `ConcisePromptStrategy`
**Inherits:** PromptStrategy
**Description:** A prompt strategy focused on brevity for a specific persona or task.
This could truncate history or simplify the base template.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `create_agent_prompt` | ✓ | `self, base_template: str, history: str, user_input` | `str` | - |
| `_truncate_history` |  | `self, history: str, max_chars: int` | `str` | - |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `_load_templates` |  | `` | `None` | - |

---

## knowledge_graph/utils.py

**Lines:** 593

### Constants

| Name |
|------|
| `tracer` |
| `trace_id_var` |
| `logger` |
| `AGENT_METRICS` |
| `_PII_SENSITIVE_KEYS` |
| `_PII_SENSITIVE_PATTERNS` |
| `audit_ledger_client` |

### Class: `ContextVarFormatter`
**Inherits:** logging.Formatter

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `format` |  | `self, record` | `-` | - |

### Class: `AgentErrorCode`
**Inherits:** str, Enum

**Class Variables:** `UNEXPECTED_ERROR`, `TIMEOUT`, `INVALID_INPUT`, `UNSUPPORTED_PERSONA`, `STATE_LOAD_FAILED`, `STATE_SAVE_FAILED`, `LLM_INIT_FAILED`, `LLM_CALL_FAILED`, `LLM_RATE_LIMITED`, `LLM_KEY_INVALID`

### Class: `AgentCoreException`
**Inherits:** Exception

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, message: str, code: AgentErrorCode, original` | `-` | - |

### Class: `AuditLedgerClient`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, ledger_url: str` | `-` | - |
| `log_event` | ✓ | `self, event_type: str, details: Dict[str, Any], op` | `bool` | - |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `get_or_create_metric` |  | `metric_type, name, documentation, labelnames, buck` | `-` | - |
| `datetime_now` |  | `` | `str` | - |
| `async_with_retry` | ✓ | `func: Callable[..., Awaitable[Any]], retries: int,` | `Any` | - |
| `_get_pii_sensitive_keys` |  | `` | `-` | - |
| `_get_pii_sensitive_patterns` |  | `` | `-` | - |
| `_redact_sensitive_pii` |  | `key: str, value: Any` | `Any` | - |
| `_sanitize_context` | ✓ | `context: Dict[str, Any], max_size_bytes: int, reda` | `Dict[str, Any]` | - |
| `_sanitize_user_input` |  | `user_input: str` | `str` | - |

---

## knowledge_loader.py

**Lines:** 391

### Constants

| Name |
|------|
| `logger` |

### Class: `KnowledgeLoader`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, knowledge_data_path: Union[str, os.PathLike]` | `-` | - |
| `get_knowledge` |  | `self` | `Dict[str, Any]` | - |
| `save_current_knowledge` |  | `self` | `None` | - |
| `load_all` |  | `self` | `None` | - |
| `inject_to_arbiter` |  | `self, arbiter_instance: Any` | `None` | - |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `merge_dict` |  | `orig: Dict[str, Any], new: Dict[str, Any]` | `None` | - |
| `save_knowledge_atomic` |  | `filename: Union[str, os.PathLike], knowledge_data:` | `None` | - |
| `_load_knowledge_sync` |  | `filename: Union[str, os.PathLike]` | `Optional[Dict[str, Any]]` | - |
| `load_knowledge` | ✓ | `filename: str` | `Optional[Dict[str, Any]]` | - |

---

## learner/__init__.py

**Lines:** 58

### Constants

| Name |
|------|
| `__version__` |
| `logger` |
| `required_envs` |
| `missing` |
| `__all__` |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `setup_module` |  | `` | `-` | - |

---

## learner/audit.py

**Lines:** 326

### Constants

| Name |
|------|
| `logger` |
| `circuit_breaker_state` |

### Class: `CircuitBreaker`
**Description:** Manages circuit breaker state to prevent DB overload on failures.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, failure_threshold: int, cooldown_seconds: in` | `-` | - |
| `record_failure` | ✓ | `self` | `-` | - |
| `record_success` | ✓ | `self` | `-` | - |
| `can_proceed` | ✓ | `self` | `bool` | - |

### Class: `MerkleTree`
**Description:** Enhanced Merkle Tree for cryptographic integrity checking.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, leaves: List[bytes]` | `-` | - |
| `_hash` |  | `self, data: bytes` | `bytes` | - |
| `_build_tree_levels` |  | `self, leaves: List[bytes]` | `List[List[bytes]]` | - |
| `get_root` |  | `self` | `bytes` | - |
| `get_proof` |  | `self, index: int` | `List[Tuple[str, str]]` | - |
| `serialize` |  | `self` | `Dict[str, Any]` | - |
| `deserialize` |  | `data: Dict[str, Any]` | `'MerkleTree'` | @staticmethod |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `_get_circuit_breaker_metric` |  | `` | `-` | - |
| `_persist_knowledge_inner` | ✓ | `db: Any, circuit_breaker: CircuitBreaker, domain: ` | `-` | - |
| `persist_knowledge` | ✓ | `db: Any, circuit_breaker: CircuitBreaker, domain: ` | `-` | @retry(stop=stop_after_attempt |
| `_persist_knowledge_batch_inner` | ✓ | `db: Any, circuit_breaker: CircuitBreaker, entries:` | `-` | - |
| `persist_knowledge_batch` | ✓ | `db: Any, circuit_breaker: CircuitBreaker, entries:` | `-` | @retry(stop=stop_after_attempt |

---

## learner/core.py

**Lines:** 1640

### Constants

| Name |
|------|
| `logger` |
| `tracer` |
| `meter` |

### Class: `LearningRecord`
**Inherits:** BaseModel
**Description:** Model for meta-learning records.

**Class Variables:** `timestamp`, `agent_id`, `session_id`, `decision_trace`, `user_feedback`, `event_type`, `learned_domain`, `learned_key`, `new_value_summary`, `old_value_summary`

### Class: `LearnerArbiterHelper`
**Description:** Helper class for Learner's internal use.
NOTE: This is NOT the main Arbiter class from self_fixing_engineer.arbiter.py.

This lightweight helper manages state and dependencies specifically for the Learner module:
- Maintains a memory dictionary for knowledge storage
- Provides access to BugManager f...

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self` | `-` | - |

### Class: `Learner`
**Description:** Central learning module for Arbiter.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, arbiter: LearnerArbiterHelper, redis: Redis,` | `-` | - |
| `start` | ✓ | `self` | `-` | - |
| `_run_self_audit` | ✓ | `self` | `-` | - |
| `start_self_audit` | ✓ | `self` | `-` | - |
| `stop_self_audit` | ✓ | `self` | `-` | - |
| `learn_new_thing` | ✓ | `self, domain: str, key: str, value: Any, user_id: ` | `Dict[str, Any]` | - |
| `_process_learn` | ✓ | `self, domain: str, key: str, value: Any, user_id: ` | `Dict[str, Any]` | - |
| `_get_previous_value` | ✓ | `self, previous_entry, domain` | `-` | - |
| `learn_batch` | ✓ | `self, facts: List[Dict[str, Any]], user_id: Option` | `List[Dict[str, Any]]` | - |
| `_prepare_and_process_single_fact_for_batch` | ✓ | `self, domain: str, key: str, value: Any, user_id: ` | `Dict[str, Any]` | - |
| `forget_fact` | ✓ | `self, domain: str, key: str, user_id: Optional[str` | `Dict[str, Any]` | - |
| `retrieve_knowledge` | ✓ | `self, domain: str, key: str, decrypt: bool` | `Optional[Dict[str, Any]]` | - |
| `_process_retrieved_data` | ✓ | `self, data: Dict[str, Any], domain: str, decrypt: ` | `Dict[str, Any]` | - |
| `_compute_diff` |  | `self, old_value: Any, new_value: Any` | `Optional[List[Dict[str, Any]]]` | - |

---

## learner/encryption.py

**Lines:** 334

### Constants

| Name |
|------|
| `logger` |
| `key_rotation_counter` |
| `learn_error_counter` |

### Class: `ArbiterConfig`
**Description:** Configuration for encryption and domain settings.

**Class Variables:** `ENCRYPTION_KEYS`, `VALID_DOMAIN_PATTERN`, `DEFAULT_SCHEMA_DIR`, `ENCRYPTED_DOMAINS`, `KNOWLEDGE_REDIS_TTL_SECONDS`, `MAX_LEARN_RETRIES`, `SELF_AUDIT_INTERVAL_SECONDS`, `JIRA_URL`, `JIRA_USER`, `JIRA_PASSWORD`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `load_keys` |  | `cls` | `-` | @classmethod |
| `rotate_keys` | ✓ | `cls, new_version: str` | `-` | @classmethod |
| `_persist_key_to_ssm` |  | `cls, version: str, key: bytes` | `-` | @classmethod |
| `_delete_key_from_ssm` |  | `cls, version: str` | `-` | @classmethod |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `encrypt_value` | ✓ | `value: Any, cipher: Fernet, key_id: str` | `bytes` | - |
| `decrypt_value` | ✓ | `encrypted: bytes, ciphers: Dict[str, Fernet]` | `Any` | - |

---

## learner/explanations.py

**Lines:** 445

### Constants

| Name |
|------|
| `logger` |
| `tracer` |
| `explanation_llm_latency_seconds` |
| `explanation_llm_failure_total` |
| `EXPLANATION_CACHE_REDIS_TTL` |
| `EXPLANATION_PROMPT_TEMPLATE_PATH` |
| `EXPLANATION_LLM_TIMEOUT_SECONDS` |
| `EXPLANATION_PROMPT_TEMPLATES` |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `_load_prompt_templates` |  | `` | `None` | - |
| `_generate_text_with_retry` | ✓ | `client: Any, prompt: str` | `str` | @retry(stop=stop_after_attempt |
| `generate_explanation` | ✓ | `learner: Any, domain: str, key: str, new_value: An` | `str` | - |
| `record_explanation_quality` | ✓ | `learner: Any, domain: str, key: str, version: Opti` | `None` | - |
| `get_explanation_quality_report` |  | `learner: Any, domain: Optional[str]` | `List[Dict[str, Any]]` | - |

---

## learner/fuzzy.py

**Lines:** 417

### Constants

| Name |
|------|
| `logger` |
| `tracer` |
| `fuzzy_parser_success_total` |
| `fuzzy_parser_failure_total` |
| `fuzzy_parser_latency_seconds` |
| `PARSER_TIMEOUT_SECONDS` |
| `PARSER_MAX_CONCURRENT` |
| `PARSER_PRIORITIES` |

### Class: `FuzzyParser`
**Inherits:** Protocol

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `parse` | ✓ | `self, text: str, context: Dict[str, Any]` | `List[Dict[str, Any]]` | - |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `load_parser_priorities` |  | `` | `None` | - |
| `_learn_batch_with_retry` | ✓ | `learner: 'Learner', facts: List[Dict[str, Any]], u` | `List[Dict[str, Any]]` | @retry(stop=stop_after_attempt |
| `process_unstructured_data` | ✓ | `learner: 'Learner', text: str, domain_hint: Option` | `List[Dict[str, Any]]` | - |
| `register_fuzzy_parser_hook` |  | `learner: 'Learner', parser: FuzzyParser, priority:` | `None` | - |
| `register_fuzzy_parser_hook_async` | ✓ | `learner: 'Learner', parser: FuzzyParser, priority:` | `None` | - |

---

## learner/metrics.py

**Lines:** 246

### Constants

| Name |
|------|
| `logger` |
| `learner_info` |
| `learn_counter` |
| `learn_error_counter` |
| `learn_duration_seconds` |
| `learn_duration_summary` |
| `forget_counter` |
| `forget_duration_seconds` |
| `forget_duration_summary` |
| `retrieve_hit_miss` |
| `audit_events_total` |
| `circuit_breaker_state` |
| `audit_failure_total` |
| `explanation_llm_latency_seconds` |
| `explanation_llm_failure_total` |
| `fuzzy_parser_success_total` |
| `fuzzy_parser_failure_total` |
| `fuzzy_parser_latency_seconds` |
| `self_audit_duration_seconds` |
| `self_audit_failure_total` |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `_get_or_create_metric` |  | `metric_class: Type, name: str, documentation: str,` | `Any` | - |
| `get_labels` |  | `` | `Dict[str, str]` | - |

---

## learner/validation.py

**Lines:** 365

### Constants

| Name |
|------|
| `logger` |
| `tracer` |
| `validation_success_total` |
| `validation_failure_total` |
| `validation_latency_seconds` |
| `schema_reload_total` |
| `schema_reload_latency_seconds` |
| `SCHEMA_RELOAD_RETRIES` |
| `SCHEMA_CACHE_TTL_SECONDS` |
| `SCHEMA_DIR_PERMISSION_CHECK` |

### Class: `DomainNotFoundError`
**Inherits:** Exception
**Description:** Raised when a validation schema or hook is not found for a domain.

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `validate_data` | ✓ | `learner: Any, domain: str, value: Any` | `Dict[str, Any]` | - |
| `register_validation_hook` |  | `learner: Any, domain: str, hook_func: Callable[[An` | `None` | - |
| `reload_schemas` | ✓ | `learner: Any, directory: Optional[str]` | `None` | @retry(stop=stop_after_attempt |

---

## logging_utils.py

**Lines:** 615
**Description:** Production-grade logging utilities for the Arbiter platform.
Provides PII redaction, structured logging, audit trails, and security features.

### Constants

| Name |
|------|
| `_context` |
| `__all__` |

### Class: `LogLevel`
**Inherits:** Enum
**Description:** Enhanced log levels for security and audit events.

**Class Variables:** `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`, `AUDIT`, `SECURITY`

### Class: `PIIRedactorFilter`
**Inherits:** logging.Filter
**Description:** Advanced PII redaction filter with configurable patterns and performance optimization.
Supports multiple redaction strategies and maintains audit trail of redactions.

**Class Variables:** `DEFAULT_PATTERNS`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, patterns: Optional[List[Tuple[Pattern, str, ` | `-` | - |
| `filter` |  | `self, record: logging.LogRecord` | `bool` | - |
| `_redact_text` |  | `self, text: str` | `str` | - |
| `_redact_args` |  | `self, args: Union[tuple, dict]` | `Union[tuple, dict]` | - |
| `_redact_value` |  | `self, value: Any` | `Any` | - |
| `_update_metrics` |  | `self, pii_type: str` | `-` | - |
| `_audit_redaction` |  | `self, record: logging.LogRecord, original: str, re` | `-` | - |
| `get_metrics` |  | `self` | `Dict[str, Any]` | - |
| `clear_cache` |  | `self` | `-` | - |

### Class: `StructuredFormatter`
**Inherits:** logging.Formatter
**Description:** Structured JSON formatter for machine-readable logs.
Includes additional context and metadata.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, include_traceback: bool` | `-` | - |
| `format` |  | `self, record: logging.LogRecord` | `str` | - |

### Class: `AuditLogger`
**Description:** Specialized logger for audit trails with guaranteed delivery.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, name: str, log_file: Optional[str]` | `-` | - |
| `log_event` |  | `self, event_type: str` | `-` | - |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `get_logger` |  | `name: str, level: int, enable_pii_filter: bool, st` | `logging.Logger` | - |
| `logging_context` |  | `` | `-` | @contextmanager |
| `configure_logging` |  | `level: int, log_file: Optional[str], structured: b` | `None` | - |
| `get_redaction_patterns` |  | `` | `List[Tuple[Pattern, str, str]]` | @lru_cache(maxsize=128) |
| `redact_text` |  | `text: str` | `str` | - |

---

## message_queue_service.py

**Lines:** 1166

### Constants

| Name |
|------|
| `_metrics_lock` |
| `MQ_PUBLISH_TOTAL` |
| `MQ_CONSUME_TOTAL` |
| `MQ_PUBLISH_LATENCY` |
| `MQ_CONSUME_LATENCY` |
| `MQ_DLQ_TOTAL` |
| `MQ_ENCRYPTION_ERRORS` |
| `MQ_CONNECTION_STATUS` |
| `logger` |

### Class: `MessageQueueServiceError`
**Inherits:** Exception

### Class: `BackendNotAvailableError`
**Inherits:** MessageQueueServiceError

### Class: `SerializationError`
**Inherits:** MessageQueueServiceError

### Class: `DecryptionError`
**Inherits:** MessageQueueServiceError

### Class: `PermissionError`
**Inherits:** MessageQueueServiceError

### Class: `MessageQueueService`
**Description:** Ultra Gold Standard Async Message Queue Service

This class provides a high-level, asynchronous API for publishing and consuming
messages, with support for Redis Streams and Kafka. It includes features for
encryption, dead-letter queues, and robust error handling.

Parameters:
    - backend_type: 'r...

**Class Variables:** `SUPPORTED_BACKENDS`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, backend_type: str, redis_url: Optional[str],` | `-` | - |
| `__aenter__` | ✓ | `self` | `-` | - |
| `__aexit__` | ✓ | `self, exc_type, exc_val, exc_tb` | `-` | - |
| `connect` | ✓ | `self` | `None` | - |
| `disconnect` | ✓ | `self` | `None` | - |
| `healthcheck` | ✓ | `self` | `Dict[str, Any]` | - |
| `_get_topic_name` |  | `self, event_type: str, is_dlq: bool` | `str` | - |
| `_encrypt_payload` |  | `self, payload: bytes` | `bytes` | - |
| `_decrypt_payload` |  | `self, encrypted_payload: bytes` | `bytes` | - |
| `_serialize_message` |  | `self, data: Dict[str, Any]` | `bytes` | - |
| `_deserialize_message` |  | `self, data_bytes: bytes` | `Dict[str, Any]` | - |
| `check_permission` |  | `self, role: str, permission: str` | `bool` | - |
| `rotate_encryption_key` | ✓ | `self, new_key: bytes` | `None` | - |
| `publish` | ✓ | `self, event_type: str, data: Dict[str, Any], is_cr` | `None` | @retry(stop=stop_after_attempt |
| `subscribe` | ✓ | `self, event_type: str, handler: Callable[[Dict[str` | `None` | - |
| `_memory_consumer` | ✓ | `self, event_type: str, handler: Callable[[Dict[str` | `-` | - |
| `_redis_stream_consumer` | ✓ | `self, stream_name: str, handler: Callable[[Dict[st` | `None` | - |
| `_kafka_consumer` | ✓ | `self, topic_name: str, handler: Callable[[Dict[str` | `None` | - |
| `_send_to_dlq` | ✓ | `self, event_type: str, original_data: Dict[str, An` | `None` | - |
| `replay_dlq` | ✓ | `self, event_type: str` | `None` | - |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `_get_or_create_metric` |  | `metric_type: Type, name: str, documentation: str, ` | `-` | - |

---

## meta_learning_orchestrator/audit_utils.py

**Lines:** 629

### Constants

| Name |
|------|
| `AUDIT_LOG_PATH` |
| `AUDIT_ENCRYPTION_KEY` |
| `AUDIT_LOG_ROTATION_SIZE_MB` |
| `AUDIT_LOG_MAX_FILES` |
| `AUDIT_RETENTION_DAYS` |
| `USE_KAFKA_AUDIT` |
| `KAFKA_BROKERS` |
| `KAFKA_TOPIC` |
| `logger` |
| `ML_AUDIT_HASH_MISMATCH` |
| `ML_AUDIT_EVENTS_TOTAL` |
| `ML_AUDIT_SIGNATURE_MISMATCH` |
| `ML_AUDIT_ROTATIONS_TOTAL` |
| `ML_AUDIT_CRYPTO_ERRORS` |
| `AUDIT_VALIDATION_LATENCY` |

### Class: `AuditEvent`
**Inherits:** BaseModel

**Class Variables:** `event_id`, `timestamp`, `event_type`, `details`, `event_hash`, `prev_hash`, `signature`

### Class: `AuditUtils`
**Description:** A utility class for managing and validating a tamper-proof audit log for a distributed ML orchestrator.

This class provides methods to:
- Write new events to an append-only log file or Kafka topic with cryptographic protections.
- Validate the integrity of the entire audit chain from file or Kafka....

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, log_path: str, rotation_size_mb: int, max_fi` | `-` | - |
| `__aenter__` | ✓ | `self` | `-` | - |
| `__aexit__` | ✓ | `self, exc_type, exc_val, exc_tb` | `-` | - |
| `_setup_log_file` |  | `self` | `-` | - |
| `_rotate_log` | ✓ | `self` | `-` | - |
| `_write_audit_event` | ✓ | `self, event: Dict[str, Any]` | `-` | @retry(stop=stop_after_attempt |
| `_send_to_kafka` | ✓ | `self, event: Dict[str, Any]` | `-` | - |
| `_get_last_hash` | ✓ | `self` | `str` | - |
| `hash_event` |  | `self, event_data: Dict[str, Any], prev_hash: str` | `Tuple[str, bytes]` | - |
| `_sign_hash` |  | `self, digest: bytes` | `str` | - |
| `_verify_signature` |  | `self, digest: bytes, signature: str` | `bool` | - |
| `get_current_timestamp` |  | `self` | `str` | - |
| `add_audit_event` | ✓ | `self, event_type: str, details: Dict[str, Any]` | `-` | - |
| `validate_audit_chain` | ✓ | `self` | `Dict[str, Any]` | @AUDIT_VALIDATION_LATENCY.time |
| `_validate_file_chain` | ✓ | `self` | `Dict[str, Any]` | - |
| `_validate_kafka_chain` | ✓ | `self` | `Dict[str, Any]` | - |

---

## meta_learning_orchestrator/clients.py

**Lines:** 543

### Constants

| Name |
|------|
| `_pii_filter` |
| `logger` |
| `HTTP_CALLS_TOTAL` |
| `HTTP_CALL_LATENCY_SECONDS` |

### Class: `_BaseHTTPClient`
**Description:** Abstract base class to reduce duplication between clients.
Now with timeouts, concurrency limits, and enhanced PII for production.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, endpoint: str, session: Optional[aiohttp.Cli` | `-` | - |
| `__aenter__` | ✓ | `self` | `-` | - |
| `__aexit__` | ✓ | `self, exc_type, exc_val, exc_tb` | `-` | - |
| `close` | ✓ | `self` | `-` | - |
| `_request_with_redaction` | ✓ | `self, method: str, url: str, data: Optional[Dict[s` | `Dict[str, Any]` | - |

### Class: `MLPlatformClient`
**Inherits:** _BaseHTTPClient
**Description:** Client for interacting with the ML Platform service via HTTP.
Handles training, evaluation, deployment, and status checks.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `trigger_training_job` | ✓ | `self, training_data_path: str, params: Dict[str, A` | `str` | - |
| `get_training_job_status` | ✓ | `self, job_id: str` | `Dict[str, Any]` | - |
| `train_model` | ✓ | `self, training_data: Dict[str, Any]` | `str` | @retry(stop=stop_after_attempt |
| `get_training_status` | ✓ | `self, job_id: str` | `Dict[str, Any]` | @retry(stop=stop_after_attempt |
| `evaluate_model` | ✓ | `self, model_id: str, eval_data: Dict[str, Any]` | `Dict[str, Any]` | @retry(stop=stop_after_attempt |
| `deploy_model` | ✓ | `self, model_id: str, version: str` | `bool` | @retry(stop=stop_after_attempt |
| `delete_model` | ✓ | `self, model_id: str` | `bool` | @retry(stop=stop_after_attempt |
| `get_evaluation_metrics` | ✓ | `self, model_id: str` | `Dict[str, Any]` | @retry(stop=stop_after_attempt |

### Class: `AgentConfigurationService`
**Inherits:** _BaseHTTPClient
**Description:** Client for a service that updates configurations for agents
(e.g., DecisionOptimizer, PolicyEngine) via HTTP.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `update_prioritization_weights` | ✓ | `self, weights: Dict[str, float], version: str` | `bool` | @retry(stop=stop_after_attempt |
| `update_policy_rules` | ✓ | `self, rules: Dict[str, Any], version: str` | `bool` | @retry(stop=stop_after_attempt |
| `update_rl_policy` | ✓ | `self, policy_model_id: str, version: str` | `bool` | @retry(stop=stop_after_attempt |
| `delete_config` | ✓ | `self, config_type: str, config_id: str` | `bool` | @retry(stop=stop_after_attempt |
| `rollback_config` | ✓ | `self, config_type: str, config_id: str, version: s` | `bool` | @retry(stop=stop_after_attempt |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `_with_client_logging_and_metrics` |  | `span_name: str, span_attributes: Dict[str, Any]` | `-` | - |

---

## meta_learning_orchestrator/config.py

**Lines:** 471

### Constants

| Name |
|------|
| `logger` |

### Class: `MetaLearningConfig`
**Inherits:** BaseSettings
**Description:** Configuration settings for the Meta-Learning Orchestrator.
Now with full dynamic reloading (file watcher, Etcd support), secure key enforcement, and health checks.
Settings are loaded from environment variables (prefixed with ML_) or .env file.

**Class Variables:** `model_config`, `SECURE_MODE`, `DATA_LAKE_PATH`, `DATA_LAKE_S3_BUCKET`, `DATA_LAKE_S3_PREFIX`, `USE_S3_DATA_LAKE`, `LOCAL_AUDIT_LOG_PATH`, `AUDIT_ENCRYPTION_KEY`, `AUDIT_SIGNING_PRIVATE_KEY`, `AUDIT_SIGNING_PUBLIC_KEY`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `validate_file_paths` |  | `cls, v: Optional[str]` | `Optional[str]` | @field_validator('DATA_LAKE_PA |
| `validate_security_keys` |  | `cls, v: Optional[str], info: ValidationInfo` | `Optional[str]` | @field_validator('AUDIT_ENCRYP |
| `validate_kafka_brokers` |  | `cls, v: str, info: ValidationInfo` | `str` | @field_validator('KAFKA_BOOTST |
| `validate_redis_url` |  | `cls, v: str` | `str` | @field_validator('REDIS_URL'), |
| `validate_endpoints` |  | `cls, v: str, info: ValidationInfo` | `str` | @field_validator('ML_PLATFORM_ |
| `validate_retention` |  | `cls, v: int, info: ValidationInfo` | `int` | @field_validator('DATA_RETENTI |
| `reload_config` |  | `self` | `-` | - |
| `_reload_from_file` |  | `self` | `-` | - |
| `start_watcher` | ✓ | `self` | `-` | - |
| `_load_from_etcd` |  | `self, client` | `-` | - |
| `is_healthy` | ✓ | `self` | `bool` | - |

---

## meta_learning_orchestrator/logging_utils.py

**Lines:** 301

### Constants

| Name |
|------|
| `REDACTION_ENABLED` |

### Class: `LogCorrelationFilter`
**Inherits:** logging.Filter
**Description:** Adds OpenTelemetry Span ID and Trace ID to log records if available.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `filter` |  | `self, record` | `-` | - |
| `_set_no_trace_fields` |  | `self, record` | `-` | - |

### Class: `JSONFormatter`
**Inherits:** logging.Formatter
**Description:** Formats log records as a single line of JSON.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `format` |  | `self, record` | `-` | - |

### Class: `PIIRedactorFilter`
**Inherits:** logging.Filter
**Description:** Redacts sensitive PII from log records. Now with recursion safety and thread-safe dynamic configuration.
- SENSITIVE_KEYS are reloaded periodically from PII_SENSITIVE_KEYS env var.
- EXTRA_REGEX_PATTERNS are loaded from PII_EXTRA_REGEX_PATTERNS env var.
- Redaction can be disabled globally by settin...

**Class Variables:** `REDACTION_STRING`, `MAX_RECURSION_DEPTH`, `BASE_PII_REGEX_PATTERNS`, `DEFAULT_SENSITIVE_KEYS`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self` | `-` | - |
| `_load_config` |  | `self` | `-` | - |
| `filter` |  | `self, record` | `-` | - |
| `_redact_value` |  | `self, value: Any, seen: Set[int], depth: int` | `Any` | - |
| `_redact_dict` |  | `self, data: Dict[str, Any], seen: Set[int], depth:` | `Dict[str, Any]` | - |
| `_redact_string_with_regex` |  | `self, text: str` | `str` | - |

---

## meta_learning_orchestrator/metrics.py

**Lines:** 323

### Constants

| Name |
|------|
| `logger` |
| `multiproc_dir` |
| `GLOBAL_LABELS` |
| `registry` |
| `METRIC_CONFLICTS` |
| `ML_INGESTION_COUNT` |
| `ML_TRAINING_TRIGGER_COUNT` |
| `ML_TRAINING_SUCCESS_COUNT` |
| `ML_TRAINING_FAILURE_COUNT` |
| `ML_EVALUATION_COUNT` |
| `ML_DEPLOYMENT_TRIGGER_COUNT` |
| `ML_DEPLOYMENT_SUCCESS_COUNT` |
| `ML_DEPLOYMENT_FAILURE_COUNT` |
| `ML_ORCHESTRATOR_ERRORS` |
| `ML_TRAINING_LATENCY` |
| `ML_EVALUATION_LATENCY` |
| `ML_DEPLOYMENT_LATENCY` |
| `ML_CURRENT_MODEL_VERSION` |
| `ML_DATA_QUEUE_SIZE` |
| `ML_DEPLOYMENT_RETRIES_EXHAUSTED` |
| ... and 4 more |

### Class: `LabeledMetricWrapper`
**Description:** Wrapper that automatically applies global labels to metrics.
This allows metrics to be used without explicitly specifying global labels each time.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, metric, global_labels: Dict[str, str], extra` | `-` | - |
| `labels` |  | `self` | `-` | - |
| `inc` |  | `self, amount` | `-` | - |
| `dec` |  | `self, amount` | `-` | - |
| `set` |  | `self, value` | `-` | - |
| `observe` |  | `self, amount` | `-` | - |
| `time` |  | `self` | `-` | - |
| `__getattr__` |  | `self, name` | `-` | - |

### Class: `MetricRegistry`
**Description:** A registry for managing Prometheus metrics, ensuring metrics are created
once and can be retrieved, with automatic application of global labels.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self` | `-` | - |
| `get_or_create` |  | `self, metric_class, name: str, documentation: str,` | `-` | - |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `_get_or_create_metric_internal` |  | `metric_class, name, documentation, labelnames, buc` | `-` | - |
| `get_or_create_metric` |  | `metric_type, name, documentation, labelnames` | `-` | - |

---

## meta_learning_orchestrator/models.py

**Lines:** 259

### Class: `EventType`
**Inherits:** str, Enum

**Class Variables:** `DECISION_MADE`, `FEEDBACK_RECEIVED`, `ACTION_TAKEN`

### Class: `DeploymentStatus`
**Inherits:** str, Enum

**Class Variables:** `PENDING`, `DEPLOYED`, `FAILED`, `ROLLED_BACK`

### Class: `LearningRecord`
**Inherits:** BaseModel
**Description:** Represents a single record of agent learning data.
Uses enums for type safety and Pydantic's frozen config for immutability.

**Class Variables:** `timestamp`, `agent_id`, `session_id`, `decision_trace`, `user_feedback`, `event_type`, `lineage_id`, `model_config`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `serialize_datetime` |  | `self, value: Any` | `Any` | @field_serializer('*', when_us |

### Class: `ModelVersion`
**Inherits:** BaseModel
**Description:** Represents a trained ML model version with metadata.
Includes validation to ensure deployed models meet quality thresholds.

**Class Variables:** `model_id`, `version`, `training_timestamp`, `evaluation_metrics`, `deployment_status`, `deployment_timestamp`, `is_active`, `retry_count`, `lineage_id`, `metadata`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `serialize_datetime` |  | `self, value: Any` | `Any` | @field_serializer('*', when_us |
| `validate_metrics_and_status` |  | `self` | `'ModelVersion'` | @model_validator(mode='after') |

### Class: `DataIngestionError`
**Inherits:** Exception
**Description:** Raised when data ingestion fails due to invalid input or file corruption.

### Class: `ModelDeploymentError`
**Inherits:** Exception
**Description:** Raised when a model deployment fails after all retries are exhausted.

### Class: `LeaderElectionError`
**Inherits:** Exception
**Description:** Raised when leader election fails in distributed orchestrator setup.

---

## meta_learning_orchestrator/orchestrator.py

**Lines:** 1305

### Constants

| Name |
|------|
| `logger` |

### Class: `Ingestor`
**Description:** Handles data ingestion from various sources (Kafka, S3, local file).

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, config: MetaLearningConfig, kafka_producer: ` | `-` | - |
| `initialize` | ✓ | `self` | `-` | - |
| `shutdown` | ✓ | `self` | `-` | - |
| `ingest_learning_record` | ✓ | `self, record_data: Dict[str, Any]` | `-` | - |

### Class: `Trainer`
**Description:** Manages the training, evaluation, and deployment of new models.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, config: MetaLearningConfig, ml_platform_clie` | `-` | - |
| `_evaluate_model` | ✓ | `self, model_version: ModelVersion` | `bool` | - |
| `_deploy_model` | ✓ | `self, model_version: ModelVersion` | `-` | @retry(stop=stop_after_attempt |
| `trigger_model_training_and_deployment` | ✓ | `self, data_location: str` | `Optional[ModelVersion]` | - |

### Class: `MetaLearningOrchestrator`
**Description:** A central, production-ready module to manage the meta-learning lifecycle for the SFE.
Now features structured logging, robust task supervision, atomic leadership with fencing,
and comprehensive health/readiness checks for high availability.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, config: MetaLearningConfig, ml_platform_clie` | `-` | - |
| `_validate_local_dir` |  | `self, path: str` | `-` | - |
| `start` | ✓ | `self` | `-` | - |
| `stop` | ✓ | `self` | `-` | - |
| `_run_periodic_leader_task` | ✓ | `self, coro: Callable[[], Awaitable], task_name: st` | `-` | - |
| `_run_leader_election` | ✓ | `self` | `-` | - |
| `_become_leader` | ✓ | `self, lock_info: Dict[str, Any]` | `-` | - |
| `_step_down_leadership` | ✓ | `self, reason: str` | `-` | - |
| `_acquire_leader_lock` | ✓ | `self` | `Tuple[bool, Dict[str, Any]]` | - |
| `_verify_leadership_and_fencing` | ✓ | `self` | `bool` | - |
| `_get_local_file_records_count` | ✓ | `self` | `int` | - |
| `_get_kafka_new_records_count` | ✓ | `self` | `int` | - |
| `ingest_learning_record` | ✓ | `self, record_data: Dict[str, Any]` | `-` | - |
| `_training_check_core` | ✓ | `self` | `-` | - |
| `_data_cleanup_core` | ✓ | `self` | `-` | - |
| `_cleanup_s3_data_lake` | ✓ | `self` | `-` | - |
| `_cleanup_local_data_lake` | ✓ | `self` | `-` | - |
| `get_health_status` | ✓ | `self` | `Dict[str, Any]` | - |
| `is_ready` | ✓ | `self` | `bool` | - |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `_log_structured` |  | `level: int, message: str` | `-` | - |
| `create_task_with_supervision` | ✓ | `coro: Awaitable, task_name: str, restart_on_error:` | `-` | - |
| `setup_signal_handlers` |  | `orchestrator: MetaLearningOrchestrator` | `-` | - |

---

## metrics.py

**Lines:** 622

### Constants

| Name |
|------|
| `tracer` |
| `_metrics_logger` |
| `_METRICS_LOCK` |
| `METRIC_REGISTRATIONS_TOTAL` |
| `METRIC_REGISTRATION_ERRORS` |
| `METRIC_REGISTRATION_TIME` |
| `HTTP_REQUESTS_TOTAL` |
| `HTTP_REQUESTS_LATENCY_SECONDS` |
| `ERRORS_TOTAL` |
| `security` |
| `CONFIG_FALLBACK_USED` |
| `__all__` |

### Class: `MetricsService`
**Inherits:** PluginBase
**Description:** Metrics service plugin for Prometheus metrics management.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `initialize` | ✓ | `self` | `-` | - |
| `start` | ✓ | `self` | `-` | - |
| `stop` | ✓ | `self` | `-` | - |
| `health_check` | ✓ | `self` | `-` | - |
| `get_capabilities` | ✓ | `self` | `-` | - |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `_idempotent_metric` |  | `metric_class: type, name: str, documentation: str,` | `-` | - |
| `get_or_create_metric` |  | `metric_type: Type, name: str, documentation: str, ` | `Any` | - |
| `get_or_create_counter` |  | `name: str, documentation: str, labelnames: Optiona` | `Counter` | - |
| `get_or_create_gauge` |  | `name: str, documentation: str, labelnames: Optiona` | `Gauge` | - |
| `get_or_create_histogram` |  | `name: str, documentation: str, labelnames: Optiona` | `Histogram` | - |
| `get_or_create_summary` |  | `name: str, documentation: str, labelnames: Optiona` | `Summary` | - |
| `metrics_handler` |  | `auth: HTTPAuthorizationCredentials` | `Response` | - |
| `register_dynamic_metric` |  | `metric_type: Type, name: str, documentation: str, ` | `Any` | - |
| `health_check` |  | `` | `Dict[str, Any]` | - |
| `clear_stale_metrics` |  | `` | `None` | - |
| `rotate_metrics_auth_token` |  | `` | `str` | - |

---

## metrics_helper.py

**Lines:** 64
**Description:** Metrics helper to handle duplicate registrations gracefully

### Constants

| Name |
|------|
| `logger` |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `get_or_create_metric` |  | `metric_type, name, description, labelnames` | `-` | - |
| `clear_all_metrics` |  | `` | `-` | - |

---

## models/audit_ledger_client.py

**Lines:** 1938

### Constants

| Name |
|------|
| `logger` |
| `tracer` |

### Class: `DLTError`
**Inherits:** Exception
**Description:** Base class for all DLT-related errors.

### Class: `DLTConnectionError`
**Inherits:** DLTError
**Description:** Custom exception for DLT connection failures.

### Class: `DLTContractError`
**Inherits:** DLTError
**Description:** Custom exception for smart contract interaction failures.

### Class: `DLTTransactionError`
**Inherits:** DLTError
**Description:** Custom exception for DLT transaction failures.

### Class: `DLTUnsupportedError`
**Inherits:** DLTError
**Description:** Custom exception for unsupported DLT types or operations.

### Class: `SecretScrubber`
**Inherits:** logging.Filter
**Description:** A logging filter to redact sensitive information.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `filter` |  | `self, record: logging.LogRecord` | `bool` | - |

### Class: `AuditEvent`
**Inherits:** BaseModel
**Description:** Represents a single audit event with validation rules.

**Class Variables:** `event_type`, `details`, `operator`, `correlation_id`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `validate_details_size` |  | `cls, v: Dict[str, Any]` | `Dict[str, Any]` | @field_validator('details'), @ |
| `hash_pii` |  | `cls, data: Any` | `Any` | @model_validator(mode='before' |

### Class: `AuditLedgerClient`
**Description:** A client for DLT-based audit logging. Supports different DLT types
(Ethereum/EVM). Hyperledger Fabric is not supported.

Integrates with Web3.py for Ethereum and provides observability through
Prometheus metrics and OpenTelemetry tracing. This version uses native
asyncio support, EIP-1559 transactio...

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, dlt_type: str, extra_metric_labels: Optional` | `None` | - |
| `_get_private_key` |  | `self` | `Optional[str]` | - |
| `rotate_private_key` |  | `self` | `None` | - |
| `__aenter__` | ✓ | `self` | `'AuditLedgerClient'` | - |
| `__aexit__` | ✓ | `self, exc_type: Optional[Type[BaseException]], exc` | `Optional[bool]` | - |
| `connect` | ✓ | `self` | `None` | @retry(stop=stop_after_attempt |
| `disconnect` | ✓ | `self` | `None` | - |
| `log_event` | ✓ | `self, event_type: str, details: Dict[str, Any], op` | `str` | @retry(stop=stop_after_attempt |
| `batch_log_events` | ✓ | `self, events: List[AuditEvent]` | `str` | @retry(stop=stop_after_attempt |
| `get_event` | ✓ | `self, tx_hash: str` | `Dict[str, Any]` | - |
| `get_events_by_type` | ✓ | `self, event_type: str, start_block: int, end_block` | `List[Dict[str, Any]]` | - |
| `verify_event` | ✓ | `self, tx_hash: str, expected_details: Dict[str, An` | `bool` | - |
| `flag_for_redaction` | ✓ | `self, tx_hash: str, reason: str` | `None` | @retry(stop=stop_after_attempt |
| `wait_for_confirmations` | ✓ | `self, tx_hash: str` | `None` | - |
| `is_connected` | ✓ | `self` | `bool` | - |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `_get_or_create_metric` |  | `metric_class: type[Counter] | type[Gauge] | type[H` | `Counter | Gauge | Histogram` | - |
| `main` | ✓ | `` | `None` | - |

---

## models/common.py

**Lines:** 63
**Description:** Common models and enums shared across the arbiter module.

This module provides canonical definitions for enums and data structures
used throughout the arbiter system to prevent duplication and incons...

### Constants

| Name |
|------|
| `logger` |

### Class: `Severity`
**Inherits:** str, Enum
**Description:** Canonical severity enum for the arbiter system.

This enum consolidates severity levels used across different components:
- DEBUG: Diagnostic information for troubleshooting
- INFO: General informational messages
- LOW: Low severity issues (from bug tracking)
- MEDIUM: Medium severity issues (from b...

**Class Variables:** `DEBUG`, `INFO`, `LOW`, `MEDIUM`, `HIGH`, `WARN`, `ERROR`, `CRITICAL`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `from_string` |  | `cls, s: str` | `'Severity'` | @classmethod |

---

## models/db_clients.py

**Lines:** 1395
**Description:** Database client implementations for the Arbiter platform.

This module provides unified database client abstractions with full observability,
retry logic, and production-grade error handling.

Feature...

### Constants

| Name |
|------|
| `logger` |
| `DB_CLIENT_OPS_TOTAL` |
| `DB_CLIENT_OPS_LATENCY` |
| `DB_CLIENT_ENTRIES` |
| `DB_CLIENT_ERRORS` |
| `__all__` |

### Class: `DBClientError`
**Inherits:** Exception
**Description:** Base exception for all database client errors.

### Class: `DBClientConnectionError`
**Inherits:** DBClientError
**Description:** Raised when connection to the database fails.

### Class: `DBClientQueryError`
**Inherits:** DBClientError
**Description:** Raised for query execution failures.

### Class: `DBClientTimeoutError`
**Inherits:** DBClientError
**Description:** Raised when a database operation times out.

### Class: `DBClientIntegrityError`
**Inherits:** DBClientError
**Description:** Raised for data integrity violations.

### Class: `DummyDBClient`
**Description:** In-memory database client for testing and development.

Thread-safe implementation with full observability integration.
All data is stored in memory and lost when the instance is garbage collected.

Features:
- Thread-safe operations using RLock
- Full OpenTelemetry tracing integration
- Prometheus ...

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self` | `None` | - |
| `__aenter__` | ✓ | `self` | `'DummyDBClient'` | - |
| `__aexit__` | ✓ | `self, exc_type, exc_val, exc_tb` | `None` | - |
| `feedback_entries` |  | `self` | `List[Dict[str, Any]]` | @property |
| `connect` | ✓ | `self` | `None` | - |
| `disconnect` | ✓ | `self` | `None` | - |
| `save_feedback_entry` | ✓ | `self, entry: Dict[str, Any]` | `str` | - |
| `get_feedback_entries` | ✓ | `self, query: Optional[Dict[str, Any]]` | `List[Dict[str, Any]]` | - |
| `update_feedback_entry` | ✓ | `self, query: Dict[str, Any], updates: Dict[str, An` | `int` | - |
| `delete_feedback_entry` | ✓ | `self, query: Dict[str, Any]` | `int` | - |
| `health_check` | ✓ | `self` | `Dict[str, Any]` | - |
| `clear` |  | `self` | `None` | - |

### Class: `SQLiteClient`
**Description:** SQLite database client with async interface and full observability.

Suitable for development, single-instance deployments, and edge cases
where a full PostgreSQL database is not available.

Features:
- WAL mode for improved concurrency
- Automatic schema migrations
- Connection pooling (thread-loca...

**Class Variables:** `_SCHEMA_VERSION`, `_CREATE_TABLE_SQL`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, db_file: Optional[str], timeout: Optional[fl` | `None` | - |
| `_get_connection` |  | `self` | `sqlite3.Connection` | - |
| `__aenter__` | ✓ | `self` | `'SQLiteClient'` | - |
| `__aexit__` | ✓ | `self, exc_type, exc_val, exc_tb` | `None` | - |
| `connect` | ✓ | `self` | `None` | - |
| `disconnect` | ✓ | `self` | `None` | - |
| `save_feedback_entry` | ✓ | `self, entry: Dict[str, Any]` | `str` | - |
| `get_feedback_entries` | ✓ | `self, query: Optional[Dict[str, Any]]` | `List[Dict[str, Any]]` | - |
| `update_feedback_entry` | ✓ | `self, query: Dict[str, Any], updates: Dict[str, An` | `int` | - |
| `delete_feedback_entry` | ✓ | `self, query: Dict[str, Any]` | `int` | - |
| `health_check` | ✓ | `self` | `Dict[str, Any]` | - |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `_get_or_create_metric` |  | `metric_class: Union[Type[Counter], Type[Gauge], Ty` | `Union[Counter, Gauge, Histogra` | - |

---

## models/feature_store_client.py

**Lines:** 1627

### Constants

| Name |
|------|
| `logger` |
| `tracer` |
| `FS_CALLS_TOTAL` |
| `FS_CALLS_ERRORS` |
| `FS_CALL_LATENCY_SECONDS` |
| `FS_FEATURE_FRESHNESS_SECONDS` |
| `FS_REDACTIONS_TOTAL` |
| `FS_AUDIT_LOGS_TOTAL` |

### Class: `FeatureEntityModel`
**Inherits:** BaseModel
**Description:** Pydantic model for validating Feast Entity definitions.

**Class Variables:** `name`, `value_type`, `description`, `model_config`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `validate_value_type` |  | `cls, v` | `-` | @field_validator('value_type') |

### Class: `FeatureViewModel`
**Inherits:** BaseModel
**Description:** Pydantic model for validating Feast FeatureView definitions.

**Class Variables:** `name`, `entities`, `ttl`, `feature_schema`, `source`, `model_config`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `validate_feature_schema` |  | `cls, v` | `-` | @field_validator('feature_sche |
| `validate_ttl` |  | `cls, v` | `-` | @field_validator('ttl'), @clas |

### Class: `FeatureSourceModel`
**Inherits:** BaseModel
**Description:** Pydantic model for validating Feast DataSource configurations.

**Class Variables:** `name`, `type`, `config`, `timestamp_field`, `model_config`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `validate_source_type` |  | `cls, v` | `-` | @field_validator('type'), @cla |

### Class: `FeatureStoreClient`
**Description:** Asynchronous client for managing Feast Feature Store operations in the Self-Fixing Engineer (SFE) system.
Supports connection, feature definition, ingestion (with Ray for distributed processing), retrieval (online/historical),
validation (with Great Expectations and statistical drift detection), GDP...

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, repo_path: Optional[str]` | `-` | - |
| `_get_credentials` |  | `self, key: str` | `str` | - |
| `__aenter__` | ✓ | `self` | `-` | - |
| `__aexit__` | ✓ | `self, exc_type, exc_val, exc_tb` | `-` | - |
| `connect` | ✓ | `self` | `None` | @retry(stop=stop_after_attempt |
| `disconnect` | ✓ | `self` | `None` | - |
| `health_check` | ✓ | `self` | `bool` | - |
| `log_operation` | ✓ | `self, operation: str, details: Dict[str, Any]` | `str` | - |
| `apply_feature_definitions` | ✓ | `self, definitions: List[Union[Entity, FeatureView]` | `None` | @retry(stop=stop_after_attempt |
| `wait_for_ingestion` | ✓ | `self, feature_view_name: str, timeout: int` | `None` | - |
| `ingest_features` | ✓ | `self, feature_view_name: str, data_df: Any` | `None` | @retry(stop=stop_after_attempt |
| `get_online_features` | ✓ | `self, feature_refs: List[str], entity_rows: List[D` | `List[Dict[str, Any]]` | @retry(stop=stop_after_attempt |
| `get_historical_features` | ✓ | `self, entity_df: Any, feature_refs: List[str]` | `Any` | @retry(stop=stop_after_attempt |
| `validate_features` | ✓ | `self, feature_view_name: str` | `Dict[str, Any]` | - |
| `flag_for_redaction` | ✓ | `self, feature_view_name: str, reason: str` | `None` | - |

### Class: `ConnectionError`
**Inherits:** Exception
**Description:** Exception raised when connection to Feature Store fails.

### Class: `SchemaValidationError`
**Inherits:** Exception
**Description:** Exception raised when schema validation fails.

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `_get_or_create_metric` |  | `metric_class: Union[Type[Counter], Type[Gauge], Ty` | `-` | - |
| `main` | ✓ | `` | `-` | - |

---

## models/knowledge_graph_db.py

**Lines:** 1508
**Description:** Neo4j Knowledge Graph implementation for managing graph-based knowledge storage.

### Constants

| Name |
|------|
| `logger` |
| `_is_test_environment` |
| `KG_REGISTRY` |
| `KG_OPS_TOTAL` |
| `KG_OPS_LATENCY` |
| `KG_CONNECTIONS` |
| `KG_ERRORS` |
| `_NAME_RX` |

### Class: `KnowledgeGraphError`
**Inherits:** Exception
**Description:** Base exception for all Knowledge Graph errors.

### Class: `ConnectionError`
**Inherits:** KnowledgeGraphError
**Description:** Raised when there is a failure to connect to the database.

### Class: `QueryError`
**Inherits:** KnowledgeGraphError
**Description:** Raised for Cypher query execution failures.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, message: str, original_error: Exception` | `-` | - |

### Class: `SchemaValidationError`
**Inherits:** KnowledgeGraphError
**Description:** Raised when input data fails Pydantic schema validation.

### Class: `NodeNotFoundError`
**Inherits:** QueryError
**Description:** Raised when a specific node cannot be found.

### Class: `ImmutableAuditLogger`
**Description:** A conceptual client for an immutable, tamper-evident audit log.
In a real system, this would write to a WORM store, hash-chain, or similar.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, file_path: str, max_bytes: int, backup_count` | `-` | - |
| `_worker` | ✓ | `self` | `-` | - |
| `_rotate_log` | ✓ | `self` | `-` | - |
| `log_event` | ✓ | `self, event: str, details: Dict[str, Any]` | `-` | - |
| `close` | ✓ | `self` | `-` | - |

### Class: `KGNode`
**Inherits:** BaseModel

**Class Variables:** `label`, `properties`, `model_config`

### Class: `KGRelationship`
**Inherits:** BaseModel

**Class Variables:** `from_node_id`, `to_node_id`, `rel_type`, `properties`, `model_config`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `validate_properties` |  | `cls, v` | `-` | @field_validator('properties', |

### Class: `Neo4jKnowledgeGraph`
**Description:** Gold Standard Async Neo4j Knowledge Graph Client

- Fully async, observable, auditable, and security-conscious.
- Complete type safety and pydantic validation.
- All actions traced, logged, metered, and (optionally) audited.
- Pluggable for real Neo4j, memory, or test backends.
- Robust error handli...

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, url: Optional[str], user: Optional[str], pas` | `-` | - |
| `_get_password` |  | `self` | `Optional[str]` | - |
| `__aenter__` | ✓ | `self` | `-` | - |
| `__aexit__` | ✓ | `self, exc_type, exc_val, exc_tb` | `-` | - |
| `_with_retry` | ✓ | `self, func` | `-` | @retry(stop=stop_after_attempt |
| `_do_connect` | ✓ | `self` | `-` | - |
| `health_check` | ✓ | `self` | `bool` | - |
| `connect` | ✓ | `self` | `-` | - |
| `disconnect` | ✓ | `self` | `-` | - |
| `_execute_tx` | ✓ | `self, tx: AsyncManagedTransaction, query: str, par` | `Any` | - |
| `add_node` | ✓ | `self, label: str, properties: Dict[str, Any]` | `str` | - |
| `add_relationship` | ✓ | `self, from_node_id: str, to_node_id: str, rel_type` | `str` | - |
| `add_fact` | ✓ | `self, domain: str, key: str, data: Dict[str, Any],` | `Optional[Dict[str, Any]]` | - |
| `find_related_facts` | ✓ | `self, domain: str, key: str, value: Any` | `List[Dict[str, Any]]` | - |
| `check_consistency` | ✓ | `self, domain: str, key: str, value: Any` | `Optional[str]` | - |
| `_export_nodes` | ✓ | `self, session: AsyncSession, filename: str, chunk_` | `-` | - |
| `_export_relationships` | ✓ | `self, session: AsyncSession, filename: str, chunk_` | `-` | - |
| `export_graph` | ✓ | `self, filename: str, chunk_size: int` | `-` | - |
| `_import_nodes` | ✓ | `self, session: AsyncSession, filename: str, chunk_` | `-` | - |
| `_import_relationships` | ✓ | `self, session: AsyncSession, filename: str, chunk_` | `-` | - |
| `import_graph` | ✓ | `self, filename: str, chunk_size: int, validate: bo` | `-` | - |
| `_import_nodes_batch` | ✓ | `self, session: AsyncSession, nodes: List[KGNode]` | `-` | - |
| `_import_relationships_batch` | ✓ | `self, session: AsyncSession, relationships: List[K` | `-` | - |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `_get_or_create_metric` |  | `metric_class: Union[Type[Counter], Type[Gauge], Ty` | `Union[Counter, Gauge, Histogra` | - |
| `_safe_name` |  | `name: str` | `str` | - |

---

## models/merkle_tree.py

**Lines:** 904

### Constants

| Name |
|------|
| `logger` |
| `tracer` |
| `METRICS_REGISTRY` |
| `HASH_OFFLOAD_THRESHOLD` |
| `MERKLE_OPS_TOTAL` |
| `MERKLE_OPS_LATENCY_SECONDS` |
| `MERKLE_TREE_SIZE` |
| `MERKLE_TREE_DEPTH` |

### Class: `MerkleTreeError`
**Inherits:** Exception
**Description:** Base exception for MerkleTree operations.

### Class: `MerkleTreeEmptyError`
**Inherits:** MerkleTreeError
**Description:** Raised when an operation is attempted on an empty tree.

### Class: `MerkleProofError`
**Inherits:** MerkleTreeError
**Description:** Raised when a Merkle proof is invalid or malformed.

### Class: `MerkleTree`
**Description:** A Merkle Tree implementation for data integrity auditing,
leveraging the `merklelib` library.

Provides methods for adding leaves, computing the root, generating proofs,
and verifying proofs, with integrated observability and persistence.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, leaves: Optional[List[bytes]], store_raw: bo` | `-` | - |
| `_hash_leaf` |  | `self, leaf_bytes: bytes` | `bytes` | - |
| `_hashed_leaves` |  | `self` | `List[bytes]` | - |
| `_update_tree` | ✓ | `self` | `None` | - |
| `_update_metrics` |  | `self` | `None` | - |
| `size` |  | `self` | `int` | @property |
| `approx_depth` |  | `self` | `int` | @property |
| `_root_bytes` |  | `self` | `bytes` | - |
| `_proof_for_index` |  | `self, idx: int` | `List[Tuple[bytes, str]]` | - |
| `add_leaf` | ✓ | `self, data: Union[str, bytes]` | `None` | - |
| `add_leaves` | ✓ | `self, data_list: List[Union[str, bytes]]` | `None` | - |
| `get_root` |  | `self` | `str` | - |
| `get_proof` |  | `self, index: int` | `List[Dict[str, str]]` | - |
| `verify_proof` |  | `root: str, leaf_data: Union[str, bytes], proof: Li` | `bool` | @staticmethod |
| `save` | ✓ | `self, filepath: Optional[str]` | `None` | - |
| `load` | ✓ | `cls, filepath: Optional[str]` | `'MerkleTree'` | @classmethod |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `_get_or_create_metric` |  | `metric_class: Union[Type[Counter], Type[Gauge], Ty` | `-` | - |
| `_write_compressed_json` |  | `path: str, data: Any` | `None` | @retry(stop=stop_after_attempt |
| `_read_compressed_json` |  | `path: str` | `Any` | @retry(stop=stop_after_attempt |
| `main` | ✓ | `` | `-` | - |

---

## models/meta_learning_data_store.py

**Lines:** 1240
**Description:** meta_learning_data_store.py

A production-ready, extensible data store for tracking meta-learning experiments and metadata.

Features:
- Pydantic schemas for meta-learning records.
- Async CRUD interf...

### Constants

| Name |
|------|
| `logger` |
| `tracer` |
| `MLDS_OPS_TOTAL` |
| `MLDS_OPS_LATENCY` |
| `MLDS_DATA_SIZE` |
| `TRANSIENT_ERRORS` |

### Class: `MetaLearningDataStoreConfig`
**Inherits:** BaseModel
**Description:** Configuration for meta learning data store.

**Class Variables:** `db_url`

### Class: `MetaLearningRecord`
**Inherits:** BaseModel

**Class Variables:** `experiment_id`, `task_type`, `dataset_name`, `meta_features`, `hyperparameters`, `metrics`, `model_artifact_uri`, `timestamp`, `tags`, `model_config`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `validate_tags` |  | `cls, v` | `-` | @field_validator('tags', mode= |
| `serialize_timestamp` |  | `self, v: datetime` | `str` | @field_serializer('timestamp') |

### Class: `MetaLearningDataStoreError`
**Inherits:** Exception
**Description:** Base exception for MetaLearningDataStore.

### Class: `MetaLearningRecordNotFound`
**Inherits:** MetaLearningDataStoreError

### Class: `MetaLearningRecordValidationError`
**Inherits:** MetaLearningDataStoreError

### Class: `MetaLearningBackendError`
**Inherits:** MetaLearningDataStoreError
**Description:** Exception for issues with the backend storage.

### Class: `MetaLearningEncryptionError`
**Inherits:** MetaLearningDataStoreError
**Description:** Exception for encryption/decryption failures.

### Class: `BaseMetaLearningDataStore`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self` | `-` | - |
| `__aenter__` | ✓ | `self` | `-` | - |
| `__aexit__` | ✓ | `self, exc_type, exc, tb` | `-` | - |
| `add_record` | ✓ | `self, record: Union[MetaLearningRecord, Dict[str, ` | `str` | - |
| `get_record` | ✓ | `self, experiment_id: str` | `MetaLearningRecord` | - |
| `list_records` | ✓ | `self, filter_by: Optional[Dict[str, Any]]` | `List[MetaLearningRecord]` | - |
| `update_record` | ✓ | `self, experiment_id: str, updates: Dict[str, Any]` | `MetaLearningRecord` | - |
| `delete_record` | ✓ | `self, experiment_id: str` | `None` | - |
| `_encrypt_field` | ✓ | `self, data: Optional[str]` | `Optional[str]` | - |
| `_decrypt_field` | ✓ | `self, data: Optional[str]` | `Optional[str]` | - |

### Class: `InMemoryMetaLearningDataStore`
**Inherits:** BaseMetaLearningDataStore
**Description:** In-memory implementation of the MetaLearningDataStore for dev/test/small scale.
Uses asyncio.Lock for concurrency control.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self` | `-` | - |
| `add_record` | ✓ | `self, record: Union[MetaLearningRecord, Dict[str, ` | `str` | @retry(stop=stop_after_attempt |
| `get_record` | ✓ | `self, experiment_id: str` | `MetaLearningRecord` | @retry(stop=stop_after_attempt |
| `list_records` | ✓ | `self, filter_by: Optional[Dict[str, Any]]` | `List[MetaLearningRecord]` | @retry(stop=stop_after_attempt |
| `update_record` | ✓ | `self, experiment_id: str, updates: Dict[str, Any]` | `MetaLearningRecord` | @retry(stop=stop_after_attempt |
| `delete_record` | ✓ | `self, experiment_id: str` | `None` | @retry(stop=stop_after_attempt |

### Class: `RedisMetaLearningDataStore`
**Inherits:** BaseMetaLearningDataStore
**Description:** Redis implementation of the MetaLearningDataStore.
Uses Redis hashes to store records.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self` | `-` | - |
| `__aenter__` | ✓ | `self` | `-` | - |
| `__aexit__` | ✓ | `self, exc_type, exc_val, exc_tb` | `-` | - |
| `_check_connection` | ✓ | `self` | `-` | - |
| `add_record` | ✓ | `self, record: Union[MetaLearningRecord, Dict[str, ` | `str` | @retry(stop=stop_after_attempt |
| `get_record` | ✓ | `self, experiment_id: str` | `MetaLearningRecord` | @retry(stop=stop_after_attempt |
| `list_records` | ✓ | `self, filter_by: Optional[Dict[str, Any]]` | `List[MetaLearningRecord]` | @retry(stop=stop_after_attempt |
| `update_record` | ✓ | `self, experiment_id: str, updates: Dict[str, Any]` | `MetaLearningRecord` | @retry(stop=stop_after_attempt |
| `delete_record` | ✓ | `self, experiment_id: str` | `None` | @retry(stop=stop_after_attempt |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `_get_or_create_metric` |  | `metric_class: Union[Type[Counter], Type[Gauge], Ty` | `Union[Counter, Gauge, Histogra` | - |
| `get_meta_learning_data_store` |  | `backend: Optional[str]` | `BaseMetaLearningDataStore` | - |
| `main` | ✓ | `` | `-` | - |

---

## models/multi_modal_schemas.py

**Lines:** 519
**Description:** multi_modal_schemas.py

Pydantic schemas for structured representation of multi-modal data analysis results.
These schemas define the data models for outputs from the MultiModalProcessor,
ensuring dat...

### Constants

| Name |
|------|
| `logger` |
| `MultiModalAnalysisResult` |

### Class: `Sentiment`
**Inherits:** str, Enum

**Class Variables:** `POSITIVE`, `NEUTRAL`, `NEGATIVE`, `MIXED`, `UNKNOWN`

### Class: `BaseConfig`
**Inherits:** BaseModel
**Description:** Base configuration for Pydantic models to ensure consistent JSON serialization
and camelCase alias generation.

**Class Variables:** `model_config`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `serialize_datetime` |  | `self, value: Any` | `Any` | @field_serializer('*', when_us |
| `sanitize_text_fields` |  | `cls, v, info: ValidationInfo` | `-` | @field_validator('*', mode='be |

### Class: `ImageOCRResult`
**Inherits:** BaseConfig

**Class Variables:** `text`, `confidence`

### Class: `ImageCaptioningResult`
**Inherits:** BaseConfig

**Class Variables:** `caption`, `confidence`

### Class: `ImageAnalysisResult`
**Inherits:** BaseConfig

**Class Variables:** `kind`, `image_id`, `source_url`, `timestamp_utc`, `ocr_result`, `captioning_result`, `detected_objects`, `face_detection_count`, `raw_response`, `severity`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `ensure_utc_timestamp` |  | `cls, v` | `-` | @field_validator('timestamp_ut |

### Class: `AudioTranscriptionResult`
**Inherits:** BaseConfig

**Class Variables:** `text`, `language`, `duration_seconds`, `speakers`

### Class: `AudioAnalysisResult`
**Inherits:** BaseConfig

**Class Variables:** `kind`, `audio_id`, `source_url`, `timestamp_utc`, `transcription`, `sentiment`, `keywords`, `speaker_count`, `raw_response`, `severity`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `check_speaker_count` |  | `self` | `-` | @model_validator(mode='after') |
| `ensure_utc_timestamp` |  | `cls, v` | `-` | @field_validator('timestamp_ut |

### Class: `VideoSummaryResult`
**Inherits:** BaseConfig

**Class Variables:** `summary_text`, `key_moments_timestamps`, `chapters`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `non_negative` |  | `cls, v` | `-` | @field_validator('key_moments_ |

### Class: `VideoAnalysisResult`
**Inherits:** BaseConfig

**Class Variables:** `kind`, `video_id`, `source_url`, `timestamp_utc`, `duration_seconds`, `summary_result`, `audio_transcription_result`, `main_entities`, `raw_response`, `severity`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `ensure_utc_timestamp` |  | `cls, v` | `-` | @field_validator('timestamp_ut |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `to_camel` |  | `string: str` | `str` | - |

---

## models/postgres_client.py

**Lines:** 1838

### Constants

| Name |
|------|
| `logger` |
| `tracer` |
| `DB_CALLS_TOTAL` |
| `DB_CALLS_ERRORS` |
| `DB_CALL_LATENCY_SECONDS` |
| `DB_CONNECTIONS_CURRENT` |
| `DB_CONNECTIONS_IN_USE` |
| `DB_TABLE_ROWS` |
| `FATAL_EXC` |
| `TRANSIENT_EXC` |

### Class: `ConnectionError`
**Inherits:** Exception
**Description:** Database connection error.

### Class: `QueryError`
**Inherits:** Exception
**Description:** Database query error.

### Class: `PostgresClientError`
**Inherits:** Exception
**Description:** Base exception for PostgresClient errors.

### Class: `PostgresClientConnectionError`
**Inherits:** PostgresClientError
**Description:** Raised when connection to the database fails.

### Class: `PostgresClientSchemaError`
**Inherits:** PostgresClientError
**Description:** Raised for schema-related issues.

### Class: `PostgresClientQueryError`
**Inherits:** PostgresClientError
**Description:** Raised for query execution failures.

### Class: `PostgresClientTimeoutError`
**Inherits:** PostgresClientError
**Description:** Raised when a query times out.

### Class: `PostgresClient`
**Description:** An asynchronous PostgreSQL client with connection pooling, schema management,
and integrated observability (Prometheus metrics and OpenTelemetry tracing).

Supported Environment Variables:

Database
- **DATABASE_URL**: (required if no db_url passed) The PostgreSQL connection string.
- **PG_POOL_MIN_...

**Class Variables:** `_TABLE_SCHEMAS`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, db_url: Optional[str]` | `-` | - |
| `__aenter__` | ✓ | `self` | `-` | - |
| `__aexit__` | ✓ | `self, exc_type, exc_val, exc_tb` | `-` | - |
| `ping` | ✓ | `self` | `bool` | - |
| `reconnect` | ✓ | `self` | `None` | - |
| `_start_health_check` | ✓ | `self, interval: float` | `None` | - |
| `update_table_row_counts` | ✓ | `self` | `None` | - |
| `_init_conn` | ✓ | `self, conn` | `-` | - |
| `connect` | ✓ | `self` | `None` | @retry(stop=stop_after_attempt |
| `disconnect` | ✓ | `self` | `None` | - |
| `_validate_table_and_columns` |  | `self, table: str, cols: Optional[List[str]]` | `None` | - |
| `_normalize_row` |  | `self, table: str, row: asyncpg.Record, normalize_d` | `Dict[str, Any]` | - |
| `_ensure_table_exists` | ✓ | `self, table_name: str` | `None` | - |
| `_execute_query` | ✓ | `self, operation: str, table: str, query: str` | `Any` | - |
| `_scrub_secrets` |  | `self, value: Any` | `Any` | - |
| `_get_insert_update_sql_and_values` | ✓ | `self, table: str, data: Dict[str, Any]` | `Tuple[str, List[Any]]` | - |
| `_save_many_copy` | ✓ | `self, table: str, data_list: List[Dict[str, Any]]` | `List[str]` | - |
| `save` | ✓ | `self, table: str, data: Dict[str, Any]` | `str` | - |
| `save_many` | ✓ | `self, table: str, data_list: List[Dict[str, Any]]` | `List[str]` | - |
| `load` | ✓ | `self, table: str, query_value: Any, query_field: s` | `Optional[Dict[str, Any]]` | - |
| `load_all` | ✓ | `self, table: str, filters: Optional[Dict[str, Any]` | `List[Dict[str, Any]]` | - |
| `update` | ✓ | `self, table: str, query: Dict[str, Any], updates: ` | `bool` | - |
| `delete` | ✓ | `self, table: str, query_value: Any, query_field: s` | `bool` | - |

### Class: `SchemaValidationError`
**Inherits:** Exception

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `_get_or_create_metric` |  | `metric_class: Union[Type[Counter], Type[Gauge], Ty` | `-` | - |
| `_sanitize_dsn` |  | `dsn: str` | `str` | - |
| `main` | ✓ | `` | `-` | - |

---

## models/redis_client.py

**Lines:** 886

### Constants

| Name |
|------|
| `logger` |
| `tracer` |
| `REDIS_CALLS_TOTAL` |
| `REDIS_CALLS_ERRORS` |
| `REDIS_CALL_LATENCY_SECONDS` |
| `REDIS_CONNECTIONS_CURRENT` |
| `REDIS_LOCK_ACQUIRED_TOTAL` |
| `REDIS_LOCK_RELEASED_TOTAL` |
| `REDIS_LOCK_FAILED_TOTAL` |
| `REDIS_MEMORY_USAGE` |
| `REDIS_KEYSPACE_SIZE` |

### Class: `RedisClient`
**Description:** An asynchronous Redis client with connection management, CRUD operations,
and integrated observability (Prometheus metrics and OpenTelemetry tracing).

Supports basic key-value operations and distributed locking.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, redis_url: Optional[str]` | `-` | - |
| `__aenter__` | ✓ | `self` | `-` | - |
| `__aexit__` | ✓ | `self, exc_type, exc_val, exc_tb` | `-` | - |
| `connect` | ✓ | `self` | `None` | @retry(stop=stop_after_attempt |
| `reconnect` | ✓ | `self` | `None` | - |
| `ping` | ✓ | `self` | `bool` | - |
| `_start_health_check` | ✓ | `self, interval: float` | `None` | - |
| `update_redis_stats` | ✓ | `self` | `None` | - |
| `disconnect` | ✓ | `self` | `None` | - |
| `_execute_operation` | ✓ | `self, operation: str, key: str, func: callable` | `Any` | - |
| `set` | ✓ | `self, key: str, value: Any, ex: Optional[int], px:` | `bool` | - |
| `mset` | ✓ | `self, mapping: Dict[str, Any]` | `bool` | - |
| `get` | ✓ | `self, key: str` | `Optional[Union[str, Dict]]` | - |
| `mget` | ✓ | `self, keys: List[str]` | `List[Optional[Union[str, Dict]` | - |
| `delete` | ✓ | `self` | `int` | - |
| `setex` | ✓ | `self, key: str, time: int, value: Any` | `bool` | - |
| `lock` |  | `self, name: str, timeout: int, blocking_timeout: i` | `RedisLock` | - |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `_get_or_create_metric` |  | `metric_class: Union[Type[Counter], Type[Gauge], Ty` | `-` | - |
| `_redact_key` |  | `key: str` | `str` | - |
| `main` | ✓ | `` | `-` | - |

---

## monitoring.py

**Lines:** 639

### Constants

| Name |
|------|
| `MAX_IN_MEMORY_LOG_SIZE_MB` |
| `JSON_LOG_WRITE_LIMIT` |
| `logger` |
| `monitor_ops_total` |
| `monitor_errors_total` |

### Class: `LogFormat`
**Inherits:** Enum

**Class Variables:** `JSONL`, `JSON`, `PLAINTEXT`

### Class: `ActionLog`
**Inherits:** Base

**Class Variables:** `__tablename__`, `id`, `data`, `timestamp`

### Class: `Monitor`
**Description:** Gold-standard monitor for tracking and auditing agent/system actions.

This class is designed for robustness in a production environment.
It is thread-safe and provides features for in-memory and on-disk logging,
log rotation, tamper-evidence, and anomaly detection.

- Supports in-memory and on-disk...

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, log_file: Optional[Union[str, Path]], logger` | `-` | - |
| `check_permission` |  | `self, role: str, permission: str` | `bool` | - |
| `__aenter__` | ✓ | `self` | `-` | - |
| `__aexit__` | ✓ | `self, exc_type, exc_val, exc_tb` | `-` | - |
| `_default_logger` |  | `` | `logging.Logger` | @staticmethod |
| `_compute_hash` |  | `self, action: Dict[str, Any]` | `str` | - |
| `log_action` |  | `self, action: Dict[str, Any]` | `None` | - |
| `_write_log` |  | `self, action: Dict[str, Any]` | `None` | - |
| `log_to_database` | ✓ | `self, action: Dict[str, Any]` | `None` | - |
| `detect_anomalies` | ✓ | `self, window_minutes: int` | `List[Dict[str, Any]]` | - |
| `generate_reports` |  | `self` | `Dict[str, Any]` | - |
| `get_recent_events` |  | `self, count: int` | `List[Dict[str, Any]]` | - |
| `explain_decision` |  | `self, decision_id: str` | `Dict[str, Any]` | - |
| `search` |  | `self, filter_fn: Optional[Callable[[Dict[str, Any]` | `List[Dict[str, Any]]` | - |
| `export_log` | ✓ | `self, file_path: Union[str, Path], format: Optiona` | `None` | - |
| `health_check` | ✓ | `self` | `Dict[str, Any]` | - |

---

## otel_config.py

**Lines:** 961
**Description:** otel_config.py - Enterprise OpenTelemetry Configuration for Arbiter Platform

This module provides centralized, production-grade OpenTelemetry configuration
with proper service discovery, circuit brea...

### Constants

| Name |
|------|
| `logger` |
| `__all__` |

### Class: `NoOpTracer`
**Description:** A no-operation tracer that can be used when OpenTelemetry is unavailable.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `start_as_current_span` |  | `self, name` | `-` | - |
| `start_span` |  | `self, name` | `-` | - |

### Class: `Environment`
**Inherits:** Enum
**Description:** Deployment environment enumeration.

**Class Variables:** `DEVELOPMENT`, `STAGING`, `PRODUCTION`, `TESTING`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `current` |  | `cls` | `'Environment'` | @classmethod |

### Class: `CollectorEndpoint`
**Decorators:** @dataclass
**Description:** OpenTelemetry collector endpoint configuration.

**Class Variables:** `url`, `protocol`, `timeout`, `headers`, `tls_cert_path`, `tls_key_path`, `tls_ca_path`, `insecure`, `compression`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `is_reachable` |  | `self` | `bool` | - |

### Class: `SamplingStrategy`
**Decorators:** @dataclass
**Description:** Advanced sampling configuration.

**Class Variables:** `base_rate`, `error_rate`, `high_latency_threshold_ms`, `high_latency_rate`, `service_rates`, `operation_rates`, `adaptive_enabled`, `target_spans_per_second`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `should_sample` |  | `self, span_name: str, service_name: str, attribute` | `bool` | - |
| `_should_sample_rate` |  | `self, rate: float` | `bool` | - |

### Class: `OpenTelemetryConfig`
**Description:** Enterprise-grade OpenTelemetry configuration manager.

This class handles all aspects of OpenTelemetry initialization, including:
- Multi-environment configuration
- Service discovery integration
- Circuit breaking for collector failures
- Advanced sampling strategies
- Security and compliance

**Class Variables:** `_instance`, `_lock`, `_initialized`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self` | `-` | - |
| `get_instance` |  | `cls` | `'OpenTelemetryConfig'` | @classmethod |
| `_initialize` |  | `self` | `-` | - |
| `_discover_endpoints` |  | `self` | `-` | - |
| `_discover_from_consul` |  | `self` | `List[CollectorEndpoint]` | - |
| `_discover_from_etcd` |  | `self` | `List[CollectorEndpoint]` | - |
| `_endpoints_from_env` |  | `self` | `List[CollectorEndpoint]` | - |
| `_validate_endpoint` |  | `self, endpoint: CollectorEndpoint` | `bool` | - |
| `_create_resource` |  | `self` | `Resource` | - |
| `_create_tracer_provider` |  | `self, resource: Resource` | `TracerProvider` | - |
| `_create_sampler` |  | `self` | `-` | - |
| `_create_span_processor` |  | `self, endpoint: CollectorEndpoint` | `-` | - |
| `_create_exporter` |  | `self, endpoint: CollectorEndpoint` | `-` | - |
| `_create_credentials` |  | `self, endpoint: CollectorEndpoint` | `-` | - |
| `_configure_propagators` |  | `self` | `-` | - |
| `_initialize_metrics` |  | `self, resource: Resource` | `-` | - |
| `_initialize_logging` |  | `self, resource: Resource` | `-` | - |
| `_parse_headers` |  | `self, headers_str: str` | `Dict[str, str]` | - |
| `trace_context` |  | `self, operation_name: str` | `-` | @contextmanager |
| `get_tracer` |  | `self, name: Optional[str]` | `Any` | - |
| `shutdown` |  | `self` | `-` | - |

### Class: `NoOpSpan`
**Description:** No-operation span implementation for when OpenTelemetry is disabled.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__enter__` |  | `self` | `-` | - |
| `__exit__` |  | `self` | `-` | - |
| `set_attribute` |  | `self, key: str, value: Any` | `-` | - |
| `add_event` |  | `self, name: str, attributes: Optional[Dict]` | `-` | - |
| `set_status` |  | `self, status: Any` | `-` | - |
| `record_exception` |  | `self, exception: Exception` | `-` | - |
| `get_span_context` |  | `self` | `-` | - |

### Class: `NoOpTracer`
**Description:** No-operation tracer implementation.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `start_as_current_span` |  | `self, name: str` | `-` | @contextmanager |
| `start_span` |  | `self, name: str` | `-` | - |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `get_tracer_safe` |  | `name: str, version: Optional[str]` | `Any` | - |
| `get_tracer` |  | `name: Optional[str]` | `Any` | - |
| `trace_operation` |  | `operation_name: str` | `-` | - |

---

## plugin_config.py

**Lines:** 306

### Constants

| Name |
|------|
| `tracer` |
| `logger` |
| `plugin_config_ops_total` |
| `plugin_config_errors_total` |
| `SANDBOXED_PLUGINS` |

### Class: `ImmutableDict`
**Inherits:** dict
**Description:** An immutable dictionary that prevents modification after creation.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__setitem__` |  | `self, key, value` | `-` | - |
| `__delitem__` |  | `self, key` | `-` | - |
| `clear` |  | `self` | `-` | - |
| `pop` |  | `self` | `-` | - |
| `popitem` |  | `self` | `-` | - |
| `setdefault` |  | `self, key, default` | `-` | - |
| `update` |  | `self` | `-` | - |

### Class: `PluginRegistryMeta`
**Inherits:** type
**Description:** Metaclass to control attribute access on PluginRegistry class.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__setattr__` |  | `cls, name, value` | `-` | - |

### Class: `PluginRegistry`
**Description:** A centralized registry for managing plugin configurations and their lifecycle.

**Class Variables:** `__ORIGINAL_PLUGINS`, `_PLUGINS`, `_REGISTRY_LOCK`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `get_plugins` |  | `cls` | `Dict[str, str]` | @classmethod |
| `check_permission` |  | `cls, role: str, permission: str` | `bool` | @classmethod |
| `validate` |  | `cls` | `None` | @classmethod |
| `register_plugin` | ✓ | `cls, name: str, import_path: str` | `None` | @classmethod |
| `health_check` | ✓ | `cls` | `Dict[str, Any]` | @classmethod |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `_get_or_create_metric` |  | `metric_class: type, name: str, doc: str, labelname` | `-` | - |

---

## plugins/anthropic_adapter.py

**Lines:** 370

### Constants

| Name |
|------|
| `logger` |
| `LLM_PROVIDER_NAME` |
| `anthropic_call_latency_seconds` |
| `anthropic_call_success_total` |
| `anthropic_call_errors_total` |

### Class: `AnthropicAdapter`
**Description:** Adapter for Anthropic LLM integration.

This class provides a robust and observable interface for interacting with Anthropic's API,
handling various error conditions and leveraging the shared LLMClient's retry mechanisms.
It includes features for production readiness such as metrics, circuit breakin...

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, settings: Dict[str, Any]` | `-` | - |
| `__aenter__` | ✓ | `self` | `-` | - |
| `__aexit__` | ✓ | `self, exc_type, exc, tb` | `-` | - |
| `_update_circuit_breaker` |  | `self, success: bool` | `-` | - |
| `_sanitize_prompt` |  | `self, prompt: str` | `str` | - |
| `generate` | ✓ | `self, prompt: str, max_tokens: int, temperature: f` | `str` | - |

---

## plugins/gemini_adapter.py

**Lines:** 407

### Constants

| Name |
|------|
| `logger` |
| `gemini_call_latency_seconds` |
| `gemini_call_success_total` |
| `gemini_call_errors_total` |

### Class: `GeminiAdapter`
**Description:** Adapter for Google Gemini LLM integration.
This class provides a robust and observable interface for interacting with Gemini's API,
handling various error conditions and leveraging the shared LLMClient's retry mechanisms.
It includes:
- Prometheus metrics for observability.
- Explicit handling of Re...

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, settings: Dict[str, Any]` | `-` | - |
| `__aenter__` | ✓ | `self` | `-` | - |
| `__aexit__` | ✓ | `self, exc_type, exc_val, exc_tb` | `-` | - |
| `generate` | ✓ | `self, prompt: str, max_tokens: int, temperature: f` | `str` | - |
| `_sanitize_prompt` |  | `self, prompt: str` | `str` | - |
| `_update_circuit_breaker` |  | `self, success: bool` | `-` | - |

---

## plugins/llm_client.py

**Lines:** 1466

### Constants

| Name |
|------|
| `logger` |
| `tracer` |
| `_metrics_lock` |
| `LLM_CALL_LATENCY` |
| `LLM_CALL_ERRORS` |
| `LLM_CALL_SUCCESS` |
| `LLM_PROVIDER_FAILOVERS_TOTAL` |

### Class: `LLMClientError`
**Inherits:** Exception
**Description:** Base custom exception for LLM Client errors.

### Class: `AuthError`
**Inherits:** LLMClientError
**Description:** Raised for authentication failures (e.g., invalid API key).

### Class: `RateLimitError`
**Inherits:** LLMClientError
**Description:** Raised when API rate limits are exceeded.

### Class: `TimeoutError`
**Inherits:** LLMClientError
**Description:** Raised when an LLM API call times out.

### Class: `APIError`
**Inherits:** LLMClientError
**Description:** Raised for general LLM API errors (e.g., bad request, server errors).

### Class: `InputValidationError`
**Inherits:** LLMClientError
**Description:** Raised for invalid or out-of-range input parameters.

### Class: `CircuitBreakerOpenError`
**Inherits:** LLMClientError
**Description:** Raised when the circuit breaker is open, preventing a call.

### Class: `LLMClient`
**Description:** Unified async client for LLM providers (OpenAI, Anthropic, Gemini, Ollama).

**Class Variables:** `_client_sessions`, `_session_lock`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, provider: str, api_key: Optional[str], model` | `-` | - |
| `_update_circuit_breaker` |  | `self, success: bool` | `-` | - |
| `_check_circuit_breaker` |  | `self` | `-` | - |
| `_get_ollama_session` | ✓ | `cls` | `aiohttp.ClientSession` | @classmethod |
| `_close_ollama_session_atexit` | ✓ | `cls` | `-` | @classmethod |
| `aclose_session` | ✓ | `self` | `-` | - |
| `_handle_llm_call` | ✓ | `self, coro_producer: Callable[[], Awaitable], prom` | `Union[str, AsyncGenerator[str,` | - |
| `_llm_type` |  | `self` | `str` | @property |
| `_sanitize_prompt` |  | `self, prompt: str` | `str` | - |
| `_generate_prompt` |  | `self, messages: List[Union[Dict[str, str], Any]]` | `str` | - |
| `generate_text` | ✓ | `self, prompt: str, max_tokens: int, temperature: f` | `str` | - |
| `_generate_core` | ✓ | `self, messages: List[Dict[str, str]], max_tokens: ` | `str` | - |
| `async_stream_text` | ✓ | `self, prompt: str, max_tokens: int, temperature: f` | `AsyncGenerator[str, None]` | - |
| `_stream_core` | ✓ | `self, messages: List[Dict[str, str]], max_tokens: ` | `AsyncGenerator[str, None]` | - |

### Class: `LoadBalancedLLMClient`
**Description:** Intelligent LLM client that can load balance and failover between multiple providers
based on performance, cost, and availability.

**Class Variables:** `FAILURE_QUARANTINE_THRESHOLD`, `QUARANTINE_DURATION_SECONDS`, `RETRYABLE_FAILURE_PENALTY_TIME`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, providers_config: List[Dict[str, Any]]` | `-` | - |
| `_select_provider` |  | `self` | `LLMClient` | - |
| `_update_provider_status` |  | `self, provider_name: str, success: bool, is_retrya` | `-` | - |
| `generate_text` | ✓ | `self, prompt: str, max_tokens: int, temperature: f` | `str` | - |
| `async_stream_text` | ✓ | `self, prompt: str, max_tokens: int, temperature: f` | `AsyncGenerator[str, None]` | - |
| `close_all_sessions` | ✓ | `self` | `-` | - |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `get_or_create_metric` |  | `metric_class: Union[Type[Counter], Type[Gauge], Ty` | `-` | - |
| `main` | ✓ | `` | `-` | - |

---

## plugins/multi_modal_config.py

**Lines:** 387

### Class: `CircuitBreakerConfig`
**Inherits:** BaseModel
**Description:** Configuration for the circuit breaker mechanism.

**Class Variables:** `enabled`, `threshold`, `timeout_seconds`, `modalities`

### Class: `ProcessorConfig`
**Inherits:** BaseModel
**Description:** Configuration for a specific modality's processing.

**Class Variables:** `enabled`, `default_provider`, `provider_config`

### Class: `SecurityConfig`
**Inherits:** BaseModel
**Description:** Configuration for security and input/output validation.

**Class Variables:** `sandbox_enabled`, `input_validation_rules`, `output_validation_rules`, `mask_pii_in_logs`, `compliance_frameworks`, `pii_patterns`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `validate_validation_rules` |  | `cls, v: Dict[str, Any]` | `Dict[str, Any]` | @field_validator('input_valida |
| `validate_pii_patterns` |  | `cls, v: Dict[str, str]` | `Dict[str, str]` | @field_validator('pii_patterns |

### Class: `AuditLogConfig`
**Inherits:** BaseModel
**Description:** Configuration for the audit logging system.

**Class Variables:** `enabled`, `log_level`, `destination`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `validate_log_level` |  | `cls, v: str` | `str` | @field_validator('log_level'), |
| `validate_destination` |  | `cls, v: str` | `str` | @field_validator('destination' |

### Class: `MetricsConfig`
**Inherits:** BaseModel
**Description:** Configuration for the Prometheus metrics system.

**Class Variables:** `enabled`, `exporter_port`

### Class: `CacheConfig`
**Inherits:** BaseModel
**Description:** Configuration for the caching mechanism.

**Class Variables:** `enabled`, `type`, `host`, `port`, `ttl_seconds`

### Class: `ComplianceConfig`
**Inherits:** BaseModel
**Description:** Configuration for compliance mapping and validation.

**Class Variables:** `mapping`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `validate_compliance_mapping` |  | `cls, v: Dict[str, List[str]]` | `Dict[str, List[str]]` | @field_validator('mapping'), @ |

### Class: `MultiModalConfig`
**Inherits:** BaseModel
**Description:** Main configuration model for the MultiModal plugin.
This model composes all the other configuration classes into a single,
hierarchical, and self-documenting structure.

Supported Environment Variables:
- MULTI_MODAL_IMAGE_PROCESSING_ENABLED: bool
- MULTI_MODAL_AUDIO_PROCESSING_ENABLED: bool
- MULTI...

**Class Variables:** `image_processing`, `audio_processing`, `video_processing`, `text_processing`, `security_config`, `audit_log_config`, `metrics_config`, `cache_config`, `compliance_config`, `circuit_breaker_config`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `load_config` |  | `cls, config_file: str` | `'MultiModalConfig'` | @classmethod |

---

## plugins/multi_modal_plugin.py

**Lines:** 1215

### Constants

| Name |
|------|
| `logger` |

### Class: `AuditLogger`
**Description:** A dedicated logger for auditing multi-modal events.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, config` | `-` | - |
| `log_event` |  | `self, user_id: str, event_type: str, timestamp: st` | `-` | - |

### Class: `MetricsCollector`
**Description:** Collects and exposes Prometheus metrics for the plugin.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, config` | `-` | - |
| `increment_successful_requests` |  | `self, modality: str` | `-` | - |
| `increment_failed_requests` |  | `self, modality: str` | `-` | - |
| `observe_latency` |  | `self, modality: str, latency_ms: float` | `-` | - |
| `increment_cache_hits` |  | `self, modality: str` | `-` | - |
| `increment_cache_misses` |  | `self, modality: str` | `-` | - |

### Class: `CacheManager`
**Description:** Manages an async Redis cache with graceful failure.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, config` | `-` | - |
| `connect` | ✓ | `self` | `-` | - |
| `disconnect` | ✓ | `self` | `-` | - |
| `get` | ✓ | `self, key: str` | `Optional[Dict[str, Any]]` | - |
| `set` | ✓ | `self, key: str, value: Dict[str, Any], ttl_seconds` | `-` | - |

### Class: `InputValidator`
**Description:** Validates input data against predefined rules and masks PII for each modality.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `validate` |  | `modality: str, data: Any, security_config` | `Any` | @staticmethod |

### Class: `OutputValidator`
**Description:** Validates output data against predefined rules for each modality.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `validate` |  | `modality: str, result: Dict[str, Any], security_co` | `-` | @staticmethod |

### Class: `SandboxExecutor`
**Description:** Executes functions in a sandboxed environment using Docker.
This implementation provides a basic level of isolation for untrusted code execution.
It serializes input/output via stdin/stdout and runs the function in a restricted
Docker container with no network access and a read-only filesystem.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `execute` | ✓ | `func: Callable` | `Any` | @staticmethod |

### Class: `MultiModalProcessor`
**Description:** Internal processor that dispatches data to the correct modality-specific provider.
This acts as an orchestration layer for the actual processing logic.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, providers: Dict[str, Any]` | `-` | - |
| `process` | ✓ | `self, modality: str, data: Any` | `Any` | - |

### Class: `MultiModalPlugin`
**Description:** Industry-leading plugin for multimodal (image/audio/video/text) processing.
Extensible, secure, observable, and SOTA-compliant.

This class orchestrates various modal processors, handles configuration,
ensures security, logs events, and exposes metrics.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, config: Optional[Dict[str, Any]]` | `-` | - |
| `initialize` | ✓ | `self` | `None` | - |
| `start` | ✓ | `self` | `-` | - |
| `stop` | ✓ | `self` | `-` | - |
| `health_check` | ✓ | `self` | `bool` | - |
| `get_capabilities` | ✓ | `self` | `List[str]` | - |
| `_load_processors` |  | `self` | `-` | - |
| `_setup_hooks` |  | `self` | `-` | - |
| `add_hook` |  | `self, modality: str, hook_fn: Callable, hook_type:` | `-` | - |
| `_execute_hooks` | ✓ | `self, modality: str, data: Any, hook_type: str` | `Any` | - |
| `_check_circuit_breaker` |  | `self, modality: str` | `-` | - |
| `_update_circuit_breaker` |  | `self, modality: str, success: bool` | `-` | - |
| `_process_data` | ✓ | `self, modality: str, data: Any, processor: Any` | `ProcessingResult` | - |
| `process_image` | ✓ | `self, image_data: Any` | `ProcessingResult` | - |
| `process_audio` | ✓ | `self, audio_data: Any` | `ProcessingResult` | - |
| `process_video` | ✓ | `self, video_data: Any` | `ProcessingResult` | - |
| `process_text` | ✓ | `self, text: str` | `ProcessingResult` | - |
| `get_supported_providers` |  | `self, modality: str` | `List[str]` | - |
| `set_default_provider` |  | `self, modality: str, provider_name: str` | `-` | - |
| `update_model_version` |  | `self, modality: str, version: str` | `-` | - |
| `__aenter__` | ✓ | `self` | `-` | - |
| `__aexit__` | ✓ | `self, exc_type, exc_val, exc_tb` | `-` | - |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `get_or_create_counter` |  | `name: str, description: str, labels: List[str]` | `Counter` | - |
| `get_or_create_histogram` |  | `name: str, description: str, labels: List[str], bu` | `Histogram` | - |
| `main` | ✓ | `` | `-` | - |

---

## plugins/multimodal/interface.py

**Lines:** 1085
**Description:** interface.py — Universal Multimodal Plugin Interface

Bar-setting, production-grade interface for modular, AI-driven multimodal analysis plugins.
Defines the contract for any plugin supporting images,...

### Constants

| Name |
|------|
| `T` |

### Class: `MultiModalException`
**Inherits:** Exception
**Description:** Base exception for all multimodal plugin errors.

### Class: `InvalidInputError`
**Inherits:** MultiModalException
**Description:** Raised when input data is invalid.

### Class: `ConfigurationError`
**Inherits:** MultiModalException
**Description:** Raised when plugin configuration is invalid.

### Class: `ProviderNotAvailableError`
**Inherits:** MultiModalException
**Description:** Raised when a requested provider is not available or configured.

### Class: `ProcessingError`
**Inherits:** MultiModalException
**Description:** Raised when a generic processing error occurs within a processor.

### Class: `ProcessingResult`
**Inherits:** BaseModel, Generic[T]
**Description:** Standardized result object for all multimodal processing operations.

**Class Variables:** `success`, `error`, `data`, `summary`, `operation_id`, `model_confidence`, `model_config`

### Class: `ImageProcessor`
**Inherits:** ABC

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `process` | ✓ | `self, image_data: Any` | `ProcessingResult` | @abstractmethod |

### Class: `AudioProcessor`
**Inherits:** ABC

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `process` | ✓ | `self, audio_data: Any` | `ProcessingResult` | @abstractmethod |

### Class: `VideoProcessor`
**Inherits:** ABC

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `process` | ✓ | `self, video_data: Any` | `ProcessingResult` | @abstractmethod |

### Class: `TextProcessor`
**Inherits:** ABC

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `process` | ✓ | `self, text_data: str` | `ProcessingResult` | @abstractmethod |

### Class: `AnalysisResultType`
**Inherits:** str, Enum
**Description:** Enum for standardizing analysis result types.

**Class Variables:** `IMAGE`, `AUDIO`, `VIDEO`, `TEXT`, `GENERIC`

### Class: `MultiModalAnalysisResult`
**Inherits:** BaseModel, Generic[T], ABC
**Description:** Base class for results of multimodal analysis.
Uses Pydantic for data validation, serialization, and robust structure.
Includes raw data, standardized metadata, and export/summary methods.
This class is abstract and should not be instantiated directly.

**Class Variables:** `raw_data`, `meta`, `result_type`, `success`, `error_message`, `confidence`, `model_id`, `timestamp_utc`, `data_provenance`, `audit_id`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `summary` |  | `self` | `str` | @abstractmethod |
| `is_valid` |  | `self` | `bool` | - |
| `get_provenance_info` |  | `self` | `Dict[str, Any]` | - |
| `__str__` |  | `self` | `str` | - |
| `__repr__` |  | `self` | `str` | - |

### Class: `ImageAnalysisResult`
**Inherits:** MultiModalAnalysisResult[Union[Dict[str, Any], List[Dict[str, Any]]]]
**Description:** Standardized result for image analysis, including common features like
classification, object detection, segmentation masks, OCR text, and embeddings.

**Class Variables:** `result_type`, `classifications`, `objects`, `ocr_text`, `embedding`, `segmentation_masks`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `summary` |  | `self` | `str` | - |

### Class: `AudioAnalysisResult`
**Inherits:** MultiModalAnalysisResult[Union[str, Dict[str, Any]]]
**Description:** Standardized result for audio analysis, including speech-to-text,
speaker identification, sentiment, and audio event classification.

**Class Variables:** `result_type`, `transcript`, `speakers`, `sentiment`, `audio_events`, `language`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `summary` |  | `self` | `str` | - |

### Class: `VideoAnalysisResult`
**Inherits:** MultiModalAnalysisResult[Union[Dict[str, Any], List[Dict[str, Any]]]]
**Description:** Standardized result for video analysis, combining insights from frames and temporal modeling.
Includes scene detection, object tracking, action recognition, and summarization.

**Class Variables:** `result_type`, `scene_changes`, `tracked_objects`, `actions`, `summary_transcript`, `key_frames_analysis`, `overall_sentiment`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `summary` |  | `self` | `str` | - |

### Class: `TextAnalysisResult`
**Inherits:** MultiModalAnalysisResult[str]
**Description:** Standardized result for text analysis, including classification, sentiment,
entity extraction, summarization, and translation.

**Class Variables:** `result_type`, `classification`, `sentiment`, `entities`, `summary_text`, `translation`, `language`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `summary` |  | `self` | `str` | - |

### Class: `MultiModalPluginInterface`
**Inherits:** ABC
**Description:** Abstract base class for all multimodal plugins.
Plugins must implement at least one supported media type for synchronous processing.
Asynchronous methods are also provided for scalable, non-blocking operations.

This interface emphasizes:
- Clear, type-hinted methods.
- Standardized input (Union for...

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, config: Optional[Dict[str, Any]]` | `-` | - |
| `analyze_image` |  | `self, image_data: Union[bytes, str, Any]` | `ImageAnalysisResult` | @abstractmethod |
| `analyze_audio` |  | `self, audio_data: Union[bytes, str, Any]` | `AudioAnalysisResult` | @abstractmethod |
| `analyze_video` |  | `self, video_data: Union[bytes, str, Any]` | `VideoAnalysisResult` | @abstractmethod |
| `analyze_text` |  | `self, text_data: str` | `TextAnalysisResult` | - |
| `supported_modalities` |  | `self` | `List[str]` | @abstractmethod |
| `analyze_image_async` | ✓ | `self, image_data: Union[bytes, str, Any]` | `ImageAnalysisResult` | - |
| `analyze_audio_async` | ✓ | `self, audio_data: Union[bytes, str, Any]` | `AudioAnalysisResult` | - |
| `analyze_video_async` | ✓ | `self, video_data: Union[bytes, str, Any]` | `VideoAnalysisResult` | - |
| `analyze_text_async` | ✓ | `self, text_data: str` | `TextAnalysisResult` | - |
| `model_info` |  | `self` | `Dict[str, Any]` | - |
| `__enter__` |  | `self` | `'MultiModalPluginInterface'` | - |
| `__exit__` |  | `self, exc_type, exc_val, exc_tb` | `None` | - |
| `__aenter__` | ✓ | `self` | `'MultiModalPluginInterface'` | - |
| `__aexit__` | ✓ | `self, exc_type, exc_val, exc_tb` | `None` | - |
| `shutdown` |  | `self` | `None` | - |
| `shutdown_async` | ✓ | `self` | `None` | - |

### Class: `DummyMultiModalPlugin`
**Inherits:** MultiModalPluginInterface
**Description:** Dummy plugin for tests/development. Returns stub results for all modalities.
This implementation is synchronous by default and raises NotImplementedError
for async methods, simulating a basic plugin.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, config: Optional[Dict[str, Any]]` | `-` | - |
| `analyze_image` |  | `self, image_data: Union[bytes, str, Any]` | `ImageAnalysisResult` | - |
| `analyze_audio` |  | `self, audio_data: Union[bytes, str, Any]` | `AudioAnalysisResult` | - |
| `analyze_video` |  | `self, video_data: Union[bytes, str, Any]` | `VideoAnalysisResult` | - |
| `analyze_text` |  | `self, text_data: str` | `TextAnalysisResult` | - |
| `supported_modalities` |  | `self` | `List[str]` | - |
| `model_info` |  | `self` | `Dict[str, Any]` | - |
| `shutdown` |  | `self` | `None` | - |
| `shutdown_async` | ✓ | `self` | `None` | - |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `get_or_create` |  | `metric` | `-` | - |

---

## plugins/multimodal/providers/default_multimodal_providers.py

**Lines:** 934
**Description:** default_multimodal_providers.py — Default Multimodal Processor Implementations

This module contains concrete, production-ready implementations of the multimodal
processor interfaces defined in `inter...

### Constants

| Name |
|------|
| `logger` |
| `__all__` |

### Class: `PluginRegistry`
**Description:** Manages the registration and retrieval of multimodal processors.
This registry is designed to be populated once at application startup.
While methods for dynamic registration/unregistration are provided,
care should be taken in multi-threaded environments.

**Class Variables:** `_processors`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `register_processor` |  | `cls, modality: str, name: str, processor_class: Ty` | `-` | @classmethod |
| `unregister_processor` |  | `cls, modality: str, name: str` | `None` | @classmethod |
| `get_processor` |  | `cls, modality: str, name: str, config: Dict[str, A` | `Any` | @classmethod |
| `get_supported_providers` |  | `cls, modality: str` | `List[str]` | @classmethod |

### Class: `DefaultImageProcessorConfig`
**Inherits:** BaseModel
**Description:** Configuration schema for DefaultImageProcessor.

**Class Variables:** `mock_min_latency_ms`, `mock_max_latency_ms`, `max_size_mb`

### Class: `DefaultAudioProcessorConfig`
**Inherits:** BaseModel
**Description:** Configuration schema for DefaultAudioProcessor.

**Class Variables:** `mock_min_latency_ms`, `mock_max_latency_ms`, `max_size_mb`

### Class: `DefaultVideoProcessorConfig`
**Inherits:** BaseModel
**Description:** Configuration schema for DefaultVideoProcessor.

**Class Variables:** `mock_min_latency_ms`, `mock_max_latency_ms`, `max_size_mb`

### Class: `DefaultTextProcessorConfig`
**Inherits:** BaseModel
**Description:** Configuration schema for DefaultTextProcessor.

**Class Variables:** `mock_min_latency_ms`, `mock_max_latency_ms`, `max_length`

### Class: `DefaultImageProcessor`
**Inherits:** ImageProcessor
**Description:** Default mock processor for image data.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, config: Dict[str, Any]` | `-` | - |
| `process` | ✓ | `self, image_data: Any, operation_id: Optional[str]` | `ProcessingResult` | - |
| `_decode_data` |  | `self, data: Union[bytes, str]` | `Optional[bytes]` | - |
| `health_check` | ✓ | `self` | `bool` | - |

### Class: `DefaultAudioProcessor`
**Inherits:** AudioProcessor
**Description:** Default mock processor for audio data.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, config: Dict[str, Any]` | `-` | - |
| `process` | ✓ | `self, audio_data: Any, operation_id: Optional[str]` | `ProcessingResult` | - |
| `_decode_data` |  | `self, data: Union[bytes, str]` | `Optional[bytes]` | - |
| `health_check` | ✓ | `self` | `bool` | - |

### Class: `DefaultVideoProcessor`
**Inherits:** VideoProcessor
**Description:** Default mock processor for video data.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, config: Dict[str, Any]` | `-` | - |
| `process` | ✓ | `self, video_data: Any, operation_id: Optional[str]` | `ProcessingResult` | - |
| `_decode_data` |  | `self, data: Union[bytes, str]` | `Optional[bytes]` | - |
| `health_check` | ✓ | `self` | `bool` | - |

### Class: `DefaultTextProcessor`
**Inherits:** TextProcessor
**Description:** Default mock processor for text data.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, config: Dict[str, Any]` | `-` | - |
| `process` | ✓ | `self, text_data: str, operation_id: Optional[str]` | `ProcessingResult` | - |
| `health_check` | ✓ | `self` | `bool` | - |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `get_or_create` |  | `metric` | `-` | - |
| `demonstrate_provider_usage` | ✓ | `` | `-` | - |

---

## plugins/ollama_adapter.py

**Lines:** 308

### Constants

| Name |
|------|
| `logger` |

### Class: `AuthError`
**Inherits:** Exception
**Description:** Custom exception for authentication errors specific to OllamaAdapter.

### Class: `RateLimitError`
**Inherits:** Exception
**Description:** Custom exception for rate limit errors specific to OllamaAdapter.

### Class: `OllamaAdapter`
**Description:** Adapter for Ollama LLM integration (local server).
This class provides a robust and observable interface for interacting with a local
Ollama instance, handling various error conditions and leveraging the shared
LLMClient's retry mechanisms.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, settings: Dict[str, Any]` | `-` | - |
| `__aenter__` | ✓ | `self` | `-` | - |
| `__aexit__` | ✓ | `self, exc_type, exc_val, exc_tb` | `-` | - |
| `_check_circuit_breaker` |  | `self` | `-` | - |
| `_update_circuit_breaker` |  | `self, success: bool` | `-` | - |
| `health_check` | ✓ | `self` | `bool` | - |
| `generate` | ✓ | `self, prompt: str, max_tokens: int, temperature: f` | `str` | - |

---

## plugins/openai_adapter.py

**Lines:** 292

### Constants

| Name |
|------|
| `logger` |

### Class: `OpenAIAdapter`
**Description:** Adapter for OpenAI LLM integration.
This class provides a robust and observable interface for interacting with OpenAI's API,
handling various error conditions and leveraging the shared LLMClient's retry mechanisms.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, settings: Dict[str, Any]` | `-` | - |
| `_check_circuit_breaker` |  | `self` | `-` | - |
| `_update_circuit_breaker` |  | `self, success: bool` | `-` | - |
| `__aenter__` | ✓ | `self` | `-` | - |
| `__aexit__` | ✓ | `self, exc_type, exc_val, exc_tb` | `-` | - |
| `health_check` | ✓ | `self` | `bool` | - |
| `generate` | ✓ | `self, prompt: str, max_tokens: int, temperature: f` | `str` | - |

---

## policy/circuit_breaker.py

**Lines:** 1201
**Description:** Circuit breaker for LLM policy API calls, with per-provider state management and optional Redis persistence.

Required ArbiterConfig attributes (to be defined in config.py):
- LLM_API_FAILURE_THRESHOL...

### Constants

| Name |
|------|
| `ArbiterConfig` |
| `logger` |
| `_metrics_lock` |
| `LLM_API_FAILURE_COUNT` |
| `LLM_CIRCUIT_BREAKER_STATE` |
| `LLM_CIRCUIT_BREAKER_TRIPS` |
| `LLM_CIRCUIT_BREAKER_ERRORS` |
| `LLM_CIRCUIT_BREAKER_TRANSITIONS` |
| `CIRCUIT_BREAKER_CLEANUP_OPERATIONS` |
| `REDIS_OPERATION_LATENCY` |
| `CONFIG_REFRESH_OPERATIONS` |
| `TASK_STATE_TRANSITIONS` |
| `tracer` |
| `_connection_pool_lock` |
| `BreakerStateManager` |
| `_breaker_states_lock` |
| `_cleanup_task_lock` |
| `_config_refresh_lock` |
| `_MAX_PROVIDERS` |
| `_pause_tasks` |

### Class: `InMemoryBreakerStateManager`
**Description:** Manages the state of a single circuit breaker in memory.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, provider: str` | `-` | - |
| `get_state` | ✓ | `self` | `dict` | - |
| `set_state` | ✓ | `self, state: dict` | `None` | - |
| `state_lock` |  | `self` | `-` | - |
| `close` | ✓ | `self` | `None` | - |

### Class: `CircuitBreakerState`
**Description:** Manages the state of a single circuit breaker, with optional Redis persistence.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, provider: str, config: 'ArbiterConfig'` | `-` | - |
| `initialize` | ✓ | `self` | `None` | @retry(stop=stop_after_attempt |
| `close` | ✓ | `self` | `None` | - |
| `state_lock` |  | `self` | `-` | - |
| `_rate_limit` | ✓ | `self` | `None` | - |
| `_check_redis_health` | ✓ | `self` | `bool` | - |
| `get_state` | ✓ | `self` | `Dict[str, Any]` | @retry(stop=stop_after_attempt |
| `set_state` | ✓ | `self, state: Dict[str, Any]` | `None` | @retry(stop=stop_after_attempt |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `get_or_create_metric` |  | `metric_class: type, name: str, documentation: str,` | `-` | - |
| `sanitize_log_message` |  | `message: Optional[str]` | `str` | - |
| `_sanitize_provider` |  | `provider: str` | `str` | - |
| `_log_validation_error` |  | `message: str, error_type: str` | `None` | - |
| `get_global_connection_pool` |  | `config: 'ArbiterConfig'` | `Optional[redis.ConnectionPool]` | - |
| `validate_config` |  | `config: 'ArbiterConfig'` | `None` | - |
| `get_breaker_state` | ✓ | `provider: str, config: 'ArbiterConfig'` | `BreakerStateManager` | - |
| `close_all_breaker_states` | ✓ | `` | `None` | - |
| `register_shutdown_handler` |  | `` | `None` | - |
| `cleanup_breaker_states` | ✓ | `` | `None` | - |
| `start_cleanup_task` |  | `` | `None` | - |
| `periodic_config_refresh` | ✓ | `` | `None` | - |
| `start_config_refresh_task` |  | `` | `None` | - |
| `refresh_breaker_states` | ✓ | `` | `None` | - |
| `is_llm_policy_circuit_breaker_open` | ✓ | `provider: str, config: Optional['ArbiterConfig']` | `bool` | - |
| `record_llm_policy_api_success` | ✓ | `provider: str, config: Optional['ArbiterConfig']` | `None` | - |
| `record_llm_policy_api_failure` | ✓ | `provider: str, error_message: Optional[str], confi` | `None` | - |

---

## policy/config.py

**Lines:** 669
**Description:** Configuration management for the Arbiter system using pydantic-settings.
Supports environment variables, .env files, and runtime reloading.

Metrics:
- arbiter_config_errors_total: Total configuration...

### Constants

| Name |
|------|
| `logger` |
| `CONFIG_ERRORS` |
| `CONFIG_INITIALIZATIONS` |
| `CONFIG_RELOAD_FREQUENCY` |
| `CONFIG_VALIDATION_DURATION` |
| `CONFIG_TO_DICT_CACHE_HITS` |
| `CONFIG_REDIS_VALIDATION_DURATION` |
| `tracer` |
| `_instance` |
| `_lock` |

### Class: `ArbiterConfig`
**Inherits:** BaseSettings
**Description:** ArbiterConfig provides a production-ready configuration system using pydantic-settings.
Settings can be loaded from environment variables or a .env file.

**Class Variables:** `model_config`, `_config_cache`, `_config_cache_timestamp`, `_config_cache_ttl`, `_cache_lock`, `_redis_pool`, `_redis_pool_lock`, `_redis_pools`, `POLICY_CONFIG_FILE_PATH`, `AUDIT_LOG_FILE_PATH`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `parse_optimizer_settings` |  | `cls, v` | `-` | @field_validator('DECISION_OPT |
| `get_redis_pool` |  | `self, redis_url: str` | `-` | - |
| `validate_secrets` |  | `cls, values: dict` | `dict` | @model_validator(mode='before' |
| `validate_redis_url` |  | `self` | `-` | @model_validator(mode='after') |
| `reload_config` | ✓ | `self` | `None` | - |
| `to_dict` |  | `self` | `Dict[str, Any]` | - |
| `get_api_key_for_provider` |  | `provider: str` | `Optional[str]` | @staticmethod |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `_get_or_create_metric` |  | `metric_class: type, name: str, doc: str, labelname` | `-` | - |
| `get_config` |  | `` | `ArbiterConfig` | - |

---

## policy/core.py

**Lines:** 1998

### Constants

| Name |
|------|
| `logger` |
| `tracer` |
| `PolicyRuleCallable` |
| `SQLITE_QUERY_LATENCY` |
| `POLICY_REFRESH_STATE_TRANSITIONS` |
| `POLICY_UPDATE_OUTCOMES` |
| `SQLITE_CLOSE_ERRORS` |
| `AUDIT_LOG_ERRORS` |
| `POLICY_REFRESH_ERRORS` |
| `POLICY_ENGINE_INIT_ERRORS` |
| `POLICY_ENGINE_RESET_ERRORS` |
| `_policy_engine_lock` |

### Class: `SQLiteClient`
**Description:** Manages SQLite database interactions for feedback storage.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, db_file: str` | `-` | - |
| `connect` | ✓ | `self` | `-` | @retry(stop=stop_after_attempt |
| `_init_db` | ✓ | `self` | `-` | - |
| `save_feedback_entry` | ✓ | `self, entry: Dict[str, Any]` | `-` | @retry(stop=stop_after_attempt |
| `get_feedback_entries` | ✓ | `self, query: Optional[Dict[str, Any]]` | `List[Dict[str, Any]]` | @retry(stop=stop_after_attempt |
| `update_feedback_entry` | ✓ | `self, query: Dict[str, Any], updates: Dict[str, An` | `bool` | @retry(stop=stop_after_attempt |
| `close` | ✓ | `self` | `-` | - |

### Class: `BasicDecisionOptimizer`
**Description:** Fallback decision optimizer for trust score computation.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, settings: Optional[Dict]` | `-` | - |
| `compute_trust_score` | ✓ | `self, auth_context: Dict, user_id: Optional[str]` | `float` | - |

### Class: `PolicyEngine`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, arbiter_instance: Any, config: ArbiterConfig` | `-` | - |
| `_create_llm_client` |  | `self` | `-` | @retry(stop=stop_after_attempt |
| `_call_llm_for_policy_evaluation` | ✓ | `self, prompt: str` | `Tuple[str, str, float]` | - |
| `_load_policies_from_file` |  | `self` | `-` | @retry(stop=stop_after_attempt |
| `_load_compliance_controls` |  | `self` | `-` | @retry(stop=stop_after_attempt |
| `_get_default_policies` |  | `self` | `Dict[str, Any]` | - |
| `register_custom_rule` |  | `self, rule_func: PolicyRuleCallable` | `-` | @retry(stop=stop_after_attempt |
| `reload_policies` |  | `self` | `-` | - |
| `apply_policy_update_from_evolution` | ✓ | `self, proposed_policies: Dict[str, Any]` | `Tuple[bool, str]` | - |
| `validate_policies` |  | `policies: Dict[str, Any]` | `bool` | @staticmethod |
| `_audit_policy_changes` | ✓ | `self, old_policies: Dict[str, Any], new_policies: ` | `-` | - |
| `_enforce_compliance` | ✓ | `self, action_name: str, control_tag: Optional[str]` | `Tuple[bool, str]` | - |
| `should_auto_learn` | ✓ | `self, domain: str, key: str, user_id: Optional[str` | `Tuple[bool, str]` | - |
| `_audit_policy_decision` | ✓ | `self, decision_type: str, domain: str, key: str, u` | `-` | @retry(stop=stop_after_attempt |
| `_get_user_roles` | ✓ | `self, user_id: str` | `List[str]` | - |
| `_sanitize_prompt` |  | `self, prompt: str` | `str` | - |
| `_validate_llm_policy_output` |  | `self, llm_response_text: str, valid_responses: Lis` | `Tuple[str, str, float]` | - |
| `trust_score_rule` | ✓ | `self, domain: str, key: str, user_id: Optional[str` | `Tuple[bool, str]` | - |
| `start_policy_refresher` | ✓ | `self` | `-` | - |
| `_periodic_policy_refresh` | ✓ | `self` | `-` | - |
| `stop` | ✓ | `self` | `-` | - |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `initialize_policy_engine` |  | `arbiter_instance: Any` | `-` | - |
| `should_auto_learn` | ✓ | `domain: str, key: str, user_id: Optional[str], val` | `Tuple[bool, str]` | - |
| `get_policy_engine_instance` |  | `` | `Optional[PolicyEngine]` | - |
| `reset_policy_engine` | ✓ | `` | `-` | - |

---

## policy/metrics.py

**Lines:** 784
**Description:** Prometheus metrics for the Arbiter system.

Metrics:
- policy_decisions_total: Total policy decisions made (allowed, domain, user_type, reason_code)
- policy_file_reloads_total: Total times policy fil...

### Constants

| Name |
|------|
| `tracer` |
| `current_dir` |
| `parent_dir` |
| `COMPLIANCE_CONFIG_PATH` |
| `ArbiterConfig` |
| `logger` |
| `_metrics_lock` |
| `_refresh_task_lock` |
| `policy_decision_total` |
| `policy_file_reload_count` |
| `policy_last_reload_timestamp` |
| `_default_decision_optimizer_settings` |
| `decision_optimizer_settings` |
| `feedback_buckets` |
| `llm_buckets` |
| `feedback_processing_time` |
| `LLM_CALL_LATENCY` |
| `COMPLIANCE_CONTROL_ACTIONS_TOTAL` |
| `COMPLIANCE_CONTROL_STATUS` |
| `COMPLIANCE_VIOLATIONS_TOTAL` |
| ... and 8 more |

### Class: `_FallbackArbiterConfig`
**Description:** Fallback configuration class with default values.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self` | `-` | - |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `_sanitize_label` |  | `value: Any` | `str` | - |
| `_log_error_rate_limited` |  | `message: str, error_type: str` | `None` | - |
| `get_or_create_metric` |  | `metric_class: Union[Type[Counter], Type[Gauge], Ty` | `-` | - |
| `_get_config` |  | `` | `-` | - |
| `record_policy_decision` |  | `allowed: str, domain: str, user_type: str, reason_` | `None` | - |
| `record_llm_call_latency` |  | `provider: str, latency: float` | `None` | - |
| `record_compliance_violation` |  | `control_id: str, violation_type: str` | `None` | - |
| `record_compliance_action` |  | `control_id: str, result: str, action_type: str` | `None` | - |
| `register_shutdown_handler` |  | `` | `None` | - |
| `cleanup_compliance_metrics` | ✓ | `` | `None` | - |
| `initialize_compliance_metrics` |  | `` | `-` | @retry(stop=stop_after_attempt |
| `refresh_compliance_metrics` | ✓ | `` | `None` | - |
| `start_metric_refresh_task` |  | `` | `None` | - |

---

## policy/policy_manager.py

**Lines:** 649
**Description:** Policy Manager (production-ready)

- Encrypted (Fernet) on-disk policy store (atomic writes).
- Optional async Postgres sync via SQLAlchemy ORM (single-row, id="current").
- Pydantic v2 models with st...

### Constants

| Name |
|------|
| `tracer` |
| `logger` |
| `policy_ops_total` |
| `policy_errors_total` |
| `policy_file_read_latency` |
| `policy_file_write_latency` |
| `policy_db_upsert_latency` |

### Class: `DomainRule`
**Inherits:** BaseModel

**Class Variables:** `active`, `allow`, `required_roles`, `reason`, `control_tag`, `max_size_kb`, `sensitive_keys`, `trust_score_threshold`, `temporal_window_seconds`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `_trust_score_range` |  | `cls, v: Optional[float]` | `Optional[float]` | @field_validator('trust_score_ |

### Class: `UserRule`
**Inherits:** BaseModel

**Class Variables:** `active`, `allow`, `restricted_domains`, `reason`, `control_tag`

### Class: `LLMRules`
**Inherits:** BaseModel

**Class Variables:** `enabled`, `threshold`, `prompt_template`, `control_tag`, `valid_responses`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `_threshold_range` |  | `cls, v: float` | `float` | @field_validator('threshold'), |

### Class: `TrustRules`
**Inherits:** BaseModel

**Class Variables:** `enabled`, `threshold`, `reason`, `temporal_window_seconds`, `control_tag`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `_trust_threshold` |  | `cls, v: float` | `float` | @field_validator('threshold'), |

### Class: `PolicyConfig`
**Inherits:** BaseModel

**Class Variables:** `file_metadata`, `global_settings`, `domain_rules`, `user_rules`, `llm_rules`, `trust_rules`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `_check_versions_and_globals` |  | `self` | `'PolicyConfig'` | @model_validator(mode='after') |
| `default` |  | `` | `'PolicyConfig'` | @staticmethod |

### Class: `PolicyManager`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, config: ArbiterConfig` | `-` | - |
| `_build_fernet_from_config` |  | `self` | `Fernet` | - |
| `_get_old_fernet` |  | `self` | `Optional[Fernet]` | - |
| `_read_encrypted_json` | ✓ | `self` | `Dict[str, Any]` | - |
| `_write_encrypted_json` | ✓ | `self, payload: Dict[str, Any]` | `None` | - |
| `load_policies` | ✓ | `self` | `None` | - |
| `save_policies` | ✓ | `self` | `None` | - |
| `load_from_database` | ✓ | `self` | `None` | - |
| `save_to_database` | ✓ | `self` | `None` | - |
| `get_policies` |  | `self` | `Optional[PolicyConfig]` | - |
| `set_policies` |  | `self, cfg: PolicyConfig` | `None` | - |
| `rotate_encryption_key` | ✓ | `self, new_key_b64: str` | `None` | - |
| `health_check` | ✓ | `self` | `Dict[str, Any]` | - |
| `check_permission` | ✓ | `self, role: str, permission: str` | `bool` | - |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `_get_or_create_metric` |  | `metric_class: type, name: str, doc: str, labelname` | `-` | - |
| `_sanitize_label` |  | `value: Any` | `str` | - |

---

## queue_consumer_worker.py

**Lines:** 746

### Constants

| Name |
|------|
| `logger` |
| `SFE_CORE_AVAILABLE` |
| `_settings_instance` |
| `CONSUMER_MESSAGES_PROCESSED_TOTAL` |
| `CONSUMER_DELIVERY_ATTEMPTS_TOTAL` |
| `CONSUMER_DELIVERY_SUCCESS_TOTAL` |
| `CONSUMER_DELIVERY_FAILURE_TOTAL` |
| `CONSUMER_DELIVERY_LATENCY_SECONDS` |
| `CONSUMER_POISON_MESSAGES_TOTAL` |
| `CONSUMER_OPS_TOTAL` |
| `CONSUMER_ERRORS_TOTAL` |
| `shutdown_event` |
| `_settings_for_attrs` |
| `POISON_MESSAGE_THRESHOLD` |
| `POISON_MESSAGE_KEY_PREFIX` |
| `CONCURRENT_LIMIT` |
| `delivery_semaphore` |
| `start_time` |

### Class: `QueueConsumerWorker`

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self, settings` | `-` | - |
| `__aenter__` | ✓ | `self` | `-` | - |
| `__aexit__` | ✓ | `self, exc_type, exc_val, exc_tb` | `-` | - |
| `run` | ✓ | `self` | `-` | - |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `_get_metric` |  | `mt, name, doc, labels, buckets` | `-` | - |
| `redact_sensitive` |  | `data: Dict[str, Any]` | `Dict[str, Any]` | - |
| `initialize_handlers` | ✓ | `` | `-` | - |
| `send_to_external_notifier` | ✓ | `event_type: str, data: Dict[str, Any]` | `bool` | - |
| `process_event` | ✓ | `event_type: str, data: Dict[str, Any], mq_service:` | `-` | - |
| `handle_message` | ✓ | `event_type: str, data: Dict[str, Any], mq_service:` | `-` | - |
| `health_check_handler` | ✓ | `request: web.Request` | `web.Response` | - |
| `consumer_main_loop` | ✓ | `` | `-` | - |

---

## run_exploration.py

**Lines:** 681

### Constants

| Name |
|------|
| `logger` |
| `workflow_ops_total` |
| `workflow_errors_total` |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `setup_logging` |  | `log_file: str` | `-` | - |
| `load_config` | ✓ | `path: Optional[str]` | `Dict[str, Any]` | @retry(stop=stop_after_attempt |
| `load_plugins` |  | `plugin_folder: str` | `Dict[str, Any]` | - |
| `notify_critical_error` |  | `message: str, error: Optional[Exception]` | `-` | - |
| `run_agent_task` | ✓ | `arbiter: Arbiter, agent_task: Dict[str, Any], outp` | `-` | - |
| `run_agentic_workflow` | ✓ | `config: Dict[str, Any]` | `-` | - |
| `main` | ✓ | `` | `-` | - |
| `start_health_server` | ✓ | `config` | `-` | - |
| `health_handler` | ✓ | `request: web.Request` | `web.Response` | - |

---

## stubs.py

**Lines:** 538
**Description:** Canonical stub implementations for Arbiter components.

This module provides production-quality stub implementations for all Arbiter components
to enable graceful degradation when real implementations...

### Constants

| Name |
|------|
| `logger` |
| `_production_mode` |
| `_test_mode` |
| `_stub_warnings_shown` |
| `_stub_lock` |
| `__all__` |

### Class: `ArbiterStub`
**Description:** Stub implementation of the main Arbiter class.

Provides no-op methods for all core Arbiter operations.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self` | `-` | - |
| `start_async_services` | ✓ | `self` | `-` | - |
| `stop_async_services` | ✓ | `self` | `-` | - |
| `respond` | ✓ | `self` | `str` | - |
| `plan_decision` | ✓ | `self` | `Dict[str, Any]` | - |
| `evolve` | ✓ | `self` | `-` | - |

### Class: `PolicyEngineStub`
**Description:** Stub implementation of PolicyEngine.

Always allows operations by default. Logs critical warnings in production.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self` | `-` | - |
| `should_auto_learn` | ✓ | `self, component: str, action: str` | `Tuple[bool, str]` | - |
| `evaluate_policy` | ✓ | `self, action: str, context: Optional[Dict[str, Any` | `Tuple[bool, str]` | - |
| `check_circuit_breaker` | ✓ | `self` | `Tuple[bool, str]` | - |

### Class: `BugManagerStub`
**Description:** Stub implementation of BugManager.

Logs bug reports but takes no action.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self` | `-` | - |
| `report_bug` | ✓ | `self, bug_data: Dict[str, Any]` | `Optional[str]` | - |
| `get_bug` | ✓ | `self, bug_id: str` | `Optional[Dict[str, Any]]` | - |
| `update_bug` | ✓ | `self, bug_id: str, updates: Dict[str, Any]` | `bool` | - |

### Class: `KnowledgeGraphStub`
**Description:** Stub implementation of KnowledgeGraph.

Maintains an in-memory graph for basic functionality.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self` | `-` | - |
| `add_fact` | ✓ | `self, domain: str, key: str, data: Dict[str, Any]` | `Dict[str, Any]` | - |
| `find_related_facts` | ✓ | `self, domain: str, key: str, value: Any` | `List[Dict[str, Any]]` | - |
| `add_node` | ✓ | `self, node_id: str, properties: Dict[str, Any]` | `None` | - |
| `add_relationship` | ✓ | `self, from_node: str, to_node: str, relationship_t` | `None` | - |
| `query` | ✓ | `self, query: str` | `List[Dict[str, Any]]` | - |
| `connect` | ✓ | `self` | `-` | - |
| `close` | ✓ | `self` | `-` | - |

### Class: `HumanInLoopStub`
**Description:** Stub implementation of HumanInLoop.

Auto-approves all requests in stub mode.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self` | `-` | - |
| `request_approval` | ✓ | `self, action: str, context: Dict[str, Any], timeou` | `bool` | - |
| `notify` | ✓ | `self, message: str, severity: str` | `bool` | - |

### Class: `MessageQueueServiceStub`
**Description:** Stub implementation of MessageQueueService.

Logs events but doesn't deliver them.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self` | `-` | - |
| `publish` | ✓ | `self, topic: str, message: Dict[str, Any]` | `bool` | - |
| `subscribe` | ✓ | `self, topic: str, handler: Callable[[Dict[str, Any` | `None` | - |
| `start` | ✓ | `self` | `-` | - |
| `stop` | ✓ | `self` | `-` | - |

### Class: `FeedbackManagerStub`
**Description:** Stub implementation of FeedbackManager.

Logs feedback but doesn't persist it.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self` | `-` | - |
| `record_feedback` | ✓ | `self, component: str, feedback_type: str, data: Di` | `bool` | - |

### Class: `ArbiterArenaStub`
**Description:** Stub implementation of ArbiterArena.

Provides no-op multi-arbiter coordination.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self` | `-` | - |
| `coordinate` | ✓ | `self, arbiters: List[Any]` | `Dict[str, Any]` | - |

### Class: `KnowledgeLoaderStub`
**Description:** Stub implementation of KnowledgeLoader.

Returns empty knowledge sets.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `__init__` |  | `self` | `-` | - |
| `load_knowledge` | ✓ | `self, domain: str` | `Dict[str, Any]` | - |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `_log_stub_usage` |  | `component: str, method: str` | `-` | - |
| `is_using_stubs` |  | `` | `Dict[str, bool]` | - |

---

## utils.py

**Lines:** 366

### Constants

| Name |
|------|
| `tracer` |
| `HEALTH_CHECK_TIMEOUT` |
| `HEALTH_CHECK_RATE_LIMIT_MAX_RATE` |
| `HEALTH_CHECK_RATE_LIMIT_TIME_PERIOD` |
| `logger` |
| `utils_ops_total` |
| `utils_errors_total` |
| `_HEALTH_SESSION` |
| `_HEALTH_SESSION_LOCK` |
| `_HEALTH_CHECK_LIMITER` |

### Class: `UtilsPlugin`
**Inherits:** PluginBase
**Description:** Plugin class to expose utility functions as service methods.
Inherits from PluginBase and implements required lifecycle methods.

| Method | Async | Parameters | Returns | Decorators |
|--------|-------|------------|---------|------------|
| `initialize` | ✓ | `self` | `None` | - |
| `start` | ✓ | `self` | `None` | - |
| `stop` | ✓ | `self` | `None` | - |
| `health_check` | ✓ | `self` | `bool` | - |
| `get_capabilities` | ✓ | `self` | `List[str]` | - |
| `random_chance` |  | `` | `-` | @staticmethod |
| `get_system_metrics` |  | `` | `-` | @staticmethod |
| `get_system_metrics_async` | ✓ | `` | `-` | @staticmethod |
| `get_health_session` | ✓ | `` | `-` | @staticmethod |
| `close_health_session` | ✓ | `` | `-` | @staticmethod |
| `check_service_health` | ✓ | `` | `-` | @staticmethod |

### Module Functions

| Function | Async | Parameters | Returns | Decorators |
|----------|-------|------------|---------|------------|
| `_get_or_create_metric` |  | `metric_class: type, name: str, doc: str, labelname` | `-` | - |
| `is_valid_directory_path` |  | `path: str` | `bool` | - |
| `safe_makedirs` |  | `path: str, fallback: str` | `Tuple[str, bool]` | - |
| `random_chance` |  | `probability: float` | `bool` | - |
| `get_system_metrics` |  | `` | `Dict[str, Any]` | - |
| `get_system_metrics_async` | ✓ | `` | `Dict[str, Any]` | - |
| `get_health_session` | ✓ | `` | `aiohttp.ClientSession` | - |
| `close_health_session` | ✓ | `` | `None` | - |
| `check_service_health` | ✓ | `url: str` | `Dict[str, Any]` | @retry(stop=stop_after_attempt |

---
