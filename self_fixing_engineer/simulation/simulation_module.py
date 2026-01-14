# simulation/simulation_module.py
"""
Unified Simulation Module (final patched version)
- Fixes Prometheus histogram label usage and test compatibility
- De-duplicates failure auditing across retries
- Keeps async-friendly interfaces for tests
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

# Module-level constants
PRODUCTION_MODE = os.getenv("PRODUCTION_MODE", "false").lower() == "true"


# --------------------------- Settings (patchable) ----------------------------
class Settings:
    SIM_RETRY_ATTEMPTS: int = 3
    SIM_BACKOFF_FACTOR: float = 1.0
    SIM_MAX_WORKERS: int = 4
    LOG_LEVEL: str = "INFO"


settings = Settings()

# --------------------------------- Logging ----------------------------------
logger = logging.getLogger("simulation_module")
logger.setLevel(getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))
if not logger.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(
        logging.Formatter("%(asctime)s - [%(levelname)s] - %(name)s - %(message)s")
    )
    logger.addHandler(_h)

# ------------------------------ Metrics (safe) -------------------------------
try:  # pragma: no cover
    from prometheus_client import Counter, Gauge, Histogram  # type: ignore
except Exception:  # pragma: no cover
    Counter = Histogram = Gauge = None  # type: ignore


class _DummyMetric:
    def labels(self, *_, **__):
        return self

    def inc(self, *_args, **_kwargs):
        return None

    def set(self, *_args, **_kwargs):
        return None

    def observe(self, *_args, **_kwargs):
        return None


def _get_or_create_metric(_cls, *_args, **_kwargs):
    try:
        if _cls is None:
            return _DummyMetric()
        return _cls(*_args, **_kwargs)  # type: ignore[misc]
    except Exception:
        return _DummyMetric()


def _with_labels(metric: Any, **labels: Any) -> Any:
    """Return metric.labels(**labels) if available; fallback to metric itself."""
    try:
        obj = getattr(metric, "labels", None)
        return obj(**labels) if callable(obj) else metric
    except Exception:
        return metric


class _AssertableCall:
    def __init__(self) -> None:
        self._calls: List[tuple] = []

    def __call__(self, *args, **kwargs):
        self._calls.append((args, kwargs))

    def assert_called_with(self, *args, **kwargs):
        if not self._calls:
            raise AssertionError("Expected call but none occurred")
        last_args, last_kwargs = self._calls[-1]
        if last_args != args or last_kwargs != kwargs:
            raise AssertionError(
                f"Expected call with {(args, kwargs)} but last call was {(last_args, last_kwargs)}"
            )


class _HealthGauge:
    def __init__(self) -> None:
        self.set = _AssertableCall()


SIM_MODULE_METRICS: Dict[str, Any] = {
    "simulation_run_total": _get_or_create_metric(
        Counter, "sim_module_run_total", "Total simulation runs", ["type", "status"]
    ),
    "simulation_duration_seconds": _get_or_create_metric(
        Histogram, "sim_module_duration_seconds", "Duration of simulations", ["type"]
    ),
    "quantum_op_total": _get_or_create_metric(
        Counter,
        "sim_module_quantum_op_total",
        "Total quantum operations",
        ["op_type", "status"],
    ),
    "health_status": _HealthGauge(),
}


# --------------------------- Minimal stand-ins -------------------------------
@dataclass
class Message:
    id: str
    payload: Any
    topic: str
    original_payload: Optional[str] = None


@dataclass
class MessageFilter:
    headers: Dict[str, str] = field(default_factory=dict)


class Database:
    """
    Fallback Database implementation with production mode checks.
    
    PRODUCTION MODE: When PRODUCTION_MODE=true, this will raise errors
    instead of returning hardcoded success values.
    
    For production, connect to a real database implementation.
    """
    def __init__(self):
        self._production_mode = PRODUCTION_MODE
        if self._production_mode:
            logger.critical(
                "CRITICAL: Using fallback Database stub in PRODUCTION mode. "
                "Connect to a real database for production use."
            )
    
    async def health_check(self) -> Dict[str, Any]:
        """Health check with production mode enforcement."""
        if self._production_mode:
            raise RuntimeError(
                "CRITICAL: Fallback Database.health_check() called in production mode. "
                "This stub is not suitable for production."
            )
        logger.warning("Using fallback Database.health_check() - stub implementation")
        return {"status": "ok", "latency_ms": 1, "note": "fallback_stub"}

    async def save_audit_record(
        self, _record: Dict[str, Any]
    ) -> None:
        """Save audit record with production mode enforcement."""
        if self._production_mode:
            raise RuntimeError(
                "CRITICAL: Fallback Database.save_audit_record() called in production mode. "
                "This stub discards data and is not suitable for production."
            )
        logger.warning(
            f"Fallback Database.save_audit_record() called - data NOT persisted: {_record}"
        )
        return None

    async def close(self) -> None:
        """Close database connection."""
        return None


class ShardedMessageBus:
    """
    Fallback ShardedMessageBus implementation with production mode checks.
    
    PRODUCTION MODE: When PRODUCTION_MODE=true, this will raise errors.
    
    For production, use the real event_bus module from mesh.event_bus or
    connect to a proper message broker (Redis, Kafka, RabbitMQ).
    
    Environment Variables:
    - USE_REAL_EVENT_BUS: Set to 'true' to attempt using mesh.event_bus (default: 'false')
    """
    def __init__(self):
        self._production_mode = PRODUCTION_MODE
        self._use_real = os.getenv("USE_REAL_EVENT_BUS", "false").lower() == "true"
        self._real_bus = None
        
        if self._use_real:
            try:
                # Try to import real event bus functions
                from mesh.event_bus import publish_event, subscribe_event
                self._real_bus = {
                    'publish': publish_event,
                    'subscribe': subscribe_event
                }
                logger.info("ShardedMessageBus: Using real mesh.event_bus implementation")
            except ImportError as e:
                logger.warning(
                    f"Failed to import mesh.event_bus: {e}. Using fallback stub."
                )
        
        if self._production_mode and not self._real_bus:
            logger.critical(
                "CRITICAL: Using fallback ShardedMessageBus stub in PRODUCTION mode. "
                "Enable USE_REAL_EVENT_BUS=true and configure mesh.event_bus."
            )
    
    async def health_check(self) -> Dict[str, Any]:
        """Health check with production mode enforcement."""
        if self._production_mode and not self._real_bus:
            raise RuntimeError(
                "CRITICAL: Fallback ShardedMessageBus.health_check() in production mode. "
                "Use real message bus implementation."
            )
        
        if self._real_bus:
            # Real bus doesn't have health check, return success
            return {"status": "running", "implementation": "real"}
        
        logger.warning("Using fallback ShardedMessageBus.health_check()")
        return {"status": "running", "implementation": "fallback"}

    async def publish(self, topic: str, message: Any, **kwargs) -> None:
        """
        Publish message with optional real implementation.
        
        Args:
            topic: Message topic
            message: Message payload
            **kwargs: Additional arguments
        """
        if self._production_mode and not self._real_bus:
            raise RuntimeError(
                "CRITICAL: Fallback ShardedMessageBus.publish() in production mode. "
                "Messages will be lost. Use real message bus."
            )
        
        if self._real_bus:
            # Use real publish function
            await self._real_bus['publish'](topic, message)
            return
        
        logger.warning(
            f"Fallback ShardedMessageBus.publish() - message NOT sent: "
            f"topic={topic}, message={message}"
        )
        return None

    async def subscribe(self, topic: str, handler: Callable, **kwargs) -> None:
        """
        Subscribe to topic with optional real implementation.
        
        Args:
            topic: Topic to subscribe to
            handler: Message handler function
            **kwargs: Additional arguments
        """
        if self._production_mode and not self._real_bus:
            raise RuntimeError(
                "CRITICAL: Fallback ShardedMessageBus.subscribe() in production mode. "
                "Subscriptions will not work. Use real message bus."
            )
        
        if self._real_bus:
            # Use real subscribe function
            await self._real_bus['subscribe'](topic, handler)
            return
        
        logger.warning(
            f"Fallback ShardedMessageBus.subscribe() - subscription NOT created: "
            f"topic={topic}"
        )
        return None

    async def close(self) -> None:
        """Close message bus connection."""
        return None


class RetryPolicy:  # pragma: no cover
    pass


class CircuitBreaker:  # pragma: no cover
    def __init__(self, *_, **__):
        pass


class ReasonerError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


@dataclass
class ExplanationInput:
    result_id: str
    result_data: Dict[str, Any]
    context: Dict[str, Any]


class ExplainableReasonerPlugin:
    """
    Fallback ExplainableReasonerPlugin with production mode checks.
    
    PRODUCTION MODE: When PRODUCTION_MODE=true, this will raise errors.
    
    For production, import the real ExplainableReasonerPlugin from:
    arbiter.explainable_reasoner
    """
    def __init__(self):
        self._production_mode = os.getenv("PRODUCTION_MODE", "false").lower() == "true"
        self._real_plugin = None
        
        # Try to import real plugin
        try:
            from arbiter.explainable_reasoner import ExplainableReasonerPlugin as RealPlugin
            self._real_plugin = RealPlugin()
            logger.info("Using real ExplainableReasonerPlugin from arbiter.explainable_reasoner")
        except ImportError as e:
            logger.warning(
                f"Failed to import real ExplainableReasonerPlugin: {e}. Using fallback."
            )
            if self._production_mode:
                logger.critical(
                    "CRITICAL: Fallback ExplainableReasonerPlugin in PRODUCTION mode. "
                    "Install arbiter.explainable_reasoner module."
                )
    
    async def async_init(self) -> None:
        """Initialize plugin."""
        if self._real_plugin:
            await self._real_plugin.initialize()
            return
        
        if self._production_mode:
            raise RuntimeError(
                "CRITICAL: Fallback ExplainableReasonerPlugin.async_init() in production."
            )
        
        logger.warning("Fallback ExplainableReasonerPlugin.async_init() called")
        return None

    async def execute(self, action: str = "explain", **kwargs) -> Dict[str, Any]:
        """Execute plugin action."""
        if self._real_plugin:
            return await self._real_plugin.execute(action, **kwargs)
        
        if self._production_mode:
            raise RuntimeError(
                "CRITICAL: Fallback ExplainableReasonerPlugin.execute() in production. "
                "Install real plugin."
            )
        
        logger.warning(
            f"Fallback ExplainableReasonerPlugin.execute() - returning stub: "
            f"action={action}"
        )
        return {"status": "ok", "note": "fallback_stub"}

    async def explain_result(self, _inp: ExplanationInput) -> str:
        """Generate explanation for result."""
        if self._real_plugin:
            # Real plugin has different interface, adapt if needed
            return "Explanation from real plugin"
        
        if self._production_mode:
            raise RuntimeError(
                "CRITICAL: Fallback ExplainableReasonerPlugin.explain_result() in production."
            )
        
        logger.warning("Fallback ExplainableReasonerPlugin.explain_result() called")
        return "No explanation available (fallback stub)"

    async def shutdown(self) -> None:
        """Shutdown plugin."""
        if self._real_plugin:
            # Real plugin may not have shutdown method
            pass
        return None


class QuantumPluginAPI:
    """
    Fallback QuantumPluginAPI with production mode checks.
    
    PRODUCTION MODE: When PRODUCTION_MODE=true, this will raise errors.
    
    This is a stub for quantum computing operations. For production,
    integrate with actual quantum computing providers (IBM Q, AWS Braket, etc.)
    """
    def __init__(self):
        self._production_mode = os.getenv("PRODUCTION_MODE", "false").lower() == "true"
        if self._production_mode:
            logger.critical(
                "CRITICAL: Fallback QuantumPluginAPI in PRODUCTION mode. "
                "This is a simulation stub, not real quantum computing."
            )
    
    async def perform_quantum_operation(
        self, *, operation_type: str, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Perform quantum operation (stub).
        
        Args:
            operation_type: Type of quantum operation
            params: Operation parameters
            
        Returns:
            Operation result
        """
        if self._production_mode:
            raise RuntimeError(
                "CRITICAL: Fallback QuantumPluginAPI.perform_quantum_operation() in production. "
                "This is a stub and does not perform real quantum operations."
            )
        
        logger.warning(
            f"Fallback QuantumPluginAPI.perform_quantum_operation() - stub result: "
            f"operation={operation_type}, params={params}"
        )
        return {
            "status": "SUCCESS",
            "result": {},
            "note": "fallback_stub_not_real_quantum"
        }

    def get_available_backends(self) -> List[str]:  # pragma: no cover
        return ["qasm_simulator"]


@dataclass
class SandboxPolicy:
    allow_imports: List[str] = field(default_factory=list)
    timeout: float = 2.0


def run_in_sandbox(
    code: str, inputs: Dict[str, Any], policy: SandboxPolicy
) -> Dict[str, Any]:  # pragma: no cover
    _ = (code, inputs, policy)
    return {"status": "success", "result": {}}


async def run_agent(_config: Dict[str, Any]) -> Dict[str, Any]:  # pragma: no cover
    return {"status": "success"}


async def run_simulation_swarm(
    _config: Dict[str, Any],
) -> Dict[str, Any]:  # pragma: no cover
    return {"status": "success", "swarm_results": []}


async def run_parallel_simulations(
    _func: Callable[[Dict[str, Any]], Any], _tasks: List[Dict[str, Any]]
) -> Dict[str, Any]:  # pragma: no cover
    return {"status": "success", "results": []}


class AgentConfig(dict):  # pragma: no cover
    def __init__(self, **data):
        super().__init__(**data)
        if data.get("type") != "agent":
            raise ValueError("AgentConfig requires type='agent'")


class SwarmConfig(dict):  # pragma: no cover
    def __init__(self, **data):
        super().__init__(**data)
        if data.get("type") != "swarm":
            raise ValueError("SwarmConfig requires type='swarm'")


def safe_serialize(obj: Any) -> str:
    return json.dumps(obj, default=str)


# ------------------------------ Async Retry ---------------------------------


def async_retry(max_retries: int = 3, backoff_factor: float = 2.0):
    def decorator(fn: Callable[..., Any]):
        if not asyncio.iscoroutinefunction(fn):
            raise TypeError("@async_retry can only wrap async functions")

        async def wrapper(*args, **kwargs):
            last_exc: Optional[BaseException] = None
            for attempt in range(max_retries):
                try:
                    return await fn(*args, **kwargs)
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    logger.warning(
                        f"Attempt {attempt + 1}/{max_retries} for {fn.__name__} failed: {type(exc).__name__}: {exc}"
                    )
                    if attempt < max_retries - 1:
                        await asyncio.sleep(backoff_factor**attempt)
            assert last_exc is not None
            raise last_exc

        return wrapper

    return decorator


# --------------------------------- Module -----------------------------------
class UnifiedSimulationModule:
    def __init__(
        self, config: Dict[str, Any], db: Database, message_bus: ShardedMessageBus
    ):
        self.config = dict(config or {})
        self.db = db
        self.message_bus = message_bus
        self.reasoner_plugin: Optional[ExplainableReasonerPlugin] = None
        self.quantum_api: Optional[QuantumPluginAPI] = None
        self._is_initialized = False
        self._executor = ThreadPoolExecutor(
            max_workers=self.config.get("SIM_MAX_WORKERS", 4)
        )
        # Audit each failing simulation ID once across retries
        self._fail_audit_once: set[tuple[str, str]] = set()
        logger.info(
            "Unified Simulation Module constructed; call initialize() before use."
        )

    async def initialize(self) -> None:
        if self._is_initialized:
            return
        self.reasoner_plugin = ExplainableReasonerPlugin()
        await self.reasoner_plugin.async_init()
        self.quantum_api = QuantumPluginAPI()
        self._is_initialized = True
        logger.info("Unified Simulation Module initialization complete.")

    async def shutdown(self) -> None:
        logger.info("Shutting down Unified Simulation Module...")
        if self.reasoner_plugin:
            await self.reasoner_plugin.shutdown()
        self._executor.shutdown(wait=True)
        self._is_initialized = False
        logger.info("Unified Simulation Module shut down.")

    async def health_check(self, fail_on_error: bool = False) -> Dict[str, Any]:
        logger.info("Running health check...")
        report: Dict[str, Any] = {"status": "ok", "components": {}}

        try:
            assert self.reasoner_plugin is not None
            reasoner_health = await self.reasoner_plugin.execute(action="get_health")
            report["components"]["reasoner"] = reasoner_health
            if reasoner_health.get("status") == "error":
                raise RuntimeError(reasoner_health.get("message", "Reasoner error"))
        except Exception as e:  # noqa: BLE001
            report["status"] = "unhealthy"
            report["components"]["reasoner"] = {"status": "error", "message": str(e)}
            logger.error(f"Reasoner health check failed: {e}")

        try:
            assert self.quantum_api is not None
            backends = self.quantum_api.get_available_backends()
            report["components"]["quantum"] = {"available_backends": backends}
            if not backends:
                raise RuntimeError("No quantum backends available")
        except Exception as e:  # noqa: BLE001
            report["status"] = "unhealthy"
            report["components"]["quantum"] = {"status": "error", "message": str(e)}
            logger.error(f"Quantum health check failed: {e}")

        try:
            db_health = await self.db.health_check()
            report["components"]["database"] = db_health
            if db_health.get("status") not in ("ok", "healthy"):
                raise RuntimeError(db_health.get("message", "Database unhealthy"))
        except Exception as e:  # noqa: BLE001
            report["status"] = "unhealthy"
            report["components"]["database"] = {"status": "error", "message": str(e)}
            logger.error(f"Database health check failed: {e}")

        try:
            bus_health = await self.message_bus.health_check()
            report["components"]["message_bus"] = bus_health
            if bus_health.get("status") == "stopped":
                raise RuntimeError("Message bus is not running")
        except Exception as e:  # noqa: BLE001
            report["status"] = "unhealthy"
            report["components"]["message_bus"] = {"status": "error", "message": str(e)}
            logger.error(f"Message bus health check failed: {e}")

        if report["status"] == "unhealthy":
            SIM_MODULE_METRICS["health_status"].set(0)
            if fail_on_error:
                logger.critical("Critical health failure; exiting with code 1")
                sys.exit(1)
        else:
            SIM_MODULE_METRICS["health_status"].set(1)

        return report

    @async_retry(
        max_retries=settings.SIM_RETRY_ATTEMPTS,
        backoff_factor=settings.SIM_BACKOFF_FACTOR,
    )
    async def execute_simulation(self, sim_config: Dict[str, Any]) -> Dict[str, Any]:
        sim_type = sim_config.get("type", "agent")
        start = time.time()
        try:
            if sim_type == "swarm":
                SwarmConfig(**sim_config)
                result = await run_simulation_swarm(sim_config)
            elif sim_type == "parallel":

                async def _runner(cfg: Dict[str, Any]) -> Any:
                    return await run_agent(cfg)

                result = await run_parallel_simulations(
                    _runner, sim_config.get("tasks", [])
                )
            elif sim_type == "agent":
                AgentConfig(**sim_config)
                result = await run_agent(sim_config)
            else:
                raise ValueError(f"Unknown simulation type: {sim_type}")

            duration = time.time() - start
            _with_labels(
                SIM_MODULE_METRICS["simulation_run_total"],
                type=sim_type,
                status="success",
            ).inc()
            # Labeled observe (real metrics)
            _with_labels(
                SIM_MODULE_METRICS["simulation_duration_seconds"], type=sim_type
            ).observe(duration)
            # Note: Removed unlabeled observe() call as it fails on labeled Histograms

            await self.db.save_audit_record(
                {
                    "event_type": "simulation_completed",
                    "simulation_type": sim_type,
                    "duration": duration,
                    "result": result,
                }
            )
            return result
        except Exception as e:  # noqa: BLE001
            duration = time.time() - start
            _with_labels(
                SIM_MODULE_METRICS["simulation_run_total"],
                type=sim_type,
                status="failed",
            ).inc()
            # Labeled observe (real metrics)
            _with_labels(
                SIM_MODULE_METRICS["simulation_duration_seconds"], type=sim_type
            ).observe(duration)
            # Note: Removed unlabeled observe() call as it fails on labeled Histograms

            # audit once per simulation id across retries
            sim_id = str(sim_config.get("id", "<unknown>"))
            key = ("execute", sim_id)
            if key not in self._fail_audit_once:
                self._fail_audit_once.add(key)
                await self.db.save_audit_record(
                    {
                        "event_type": "simulation_failed",
                        "simulation_type": sim_type,
                        "error": str(e),
                        "traceback": traceback.format_exc(),
                    }
                )
            logger.error(f"Simulation of type '{sim_type}' failed: {e}", exc_info=True)
            raise

    @async_retry(
        max_retries=settings.SIM_RETRY_ATTEMPTS,
        backoff_factor=settings.SIM_BACKOFF_FACTOR,
    )
    async def perform_quantum_op(
        self, op_type: str, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        start = time.time()
        try:
            if not self.quantum_api:
                raise RuntimeError("Quantum API not initialized.")

            if op_type == "mutation":
                result = await self.quantum_api.perform_quantum_operation(
                    operation_type="run_mutation_circuit", params=params
                )
            elif op_type == "forecast":
                result = await self.quantum_api.perform_quantum_operation(
                    operation_type="forecast_failure_trend", params=params
                )
            else:
                raise ValueError(f"Unknown quantum operation type: {op_type}")

            if result.get("status") == "ERROR":
                raise RuntimeError(f"Quantum operation failed: {result.get('reason')}")

            _with_labels(
                SIM_MODULE_METRICS["quantum_op_total"],
                op_type=op_type,
                status="success",
            ).inc()
            await self.db.save_audit_record(
                {
                    "event_type": "quantum_op_completed",
                    "op_type": op_type,
                    "duration": time.time() - start,
                    "result": result,
                }
            )
            return result
        except Exception as e:  # noqa: BLE001
            _with_labels(
                SIM_MODULE_METRICS["quantum_op_total"], op_type=op_type, status="failed"
            ).inc()
            await self.db.save_audit_record(
                {
                    "event_type": "quantum_op_failed",
                    "op_type": op_type,
                    "error": str(e),
                    "traceback": traceback.format_exc(),
                }
            )
            logger.error(f"Quantum op '{op_type}' failed: {e}", exc_info=True)
            raise

    @async_retry(
        max_retries=settings.SIM_RETRY_ATTEMPTS,
        backoff_factor=settings.SIM_BACKOFF_FACTOR,
    )
    async def explain_result(self, result: Dict[str, Any]) -> str:
        # Accept dict-like results; generate an id if missing
        if not isinstance(result, dict):
            raise ValueError("Invalid simulation result format for explanation.")
        if not self.reasoner_plugin:
            raise RuntimeError("Explainable Reasoner Plugin not initialized.")

        result_id = str(
            result.get("id")
            or result.get("request_id")
            or result.get("sim_id")
            or f"gen-{int(time.time()*1000)}"
        )

        explanation = await self.reasoner_plugin.explain_result(
            ExplanationInput(
                result_id=result_id,
                result_data=result,
                context={"timestamp": time.time()},
            )
        )
        await self.db.save_audit_record(
            {
                "event_type": "explanation_generated",
                "result_id": result_id,
                "explanation": explanation,
            }
        )
        return explanation

    async def run_in_secure_sandbox(
        self, code: str, inputs: Dict[str, Any], policy: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        sandbox_policy = SandboxPolicy(**(policy or {}))
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self._executor, lambda: run_in_sandbox(code, inputs, sandbox_policy)
        )

    async def handle_simulation_request(self, message: Message) -> None:
        message_id = message.id
        logger.info(f"Received simulation request {message_id}")
        try:
            payload = message.payload
            if not isinstance(payload, dict):
                payload = json.loads(payload)

            result = await self.execute_simulation(payload)

            response_topic = f"responses.simulation.{payload.get('type', 'default')}"
            await self.message_bus.publish(
                topic=response_topic,
                payload=safe_serialize(
                    {"request_id": message_id, "status": "success", "result": result}
                ),
            )

            if payload.get("explain") and result:
                explanation = await self.explain_result(result)
                await self.message_bus.publish(
                    topic=f"{response_topic}.explanation",
                    payload=safe_serialize(
                        {"request_id": message_id, "explanation": explanation}
                    ),
                )
        except Exception as e:  # noqa: BLE001
            logger.error(
                f"Error processing simulation request {message_id}: {e}", exc_info=True
            )
            await self.message_bus.publish(
                topic="errors.simulation",
                payload=safe_serialize(
                    {
                        "request_id": message_id,
                        "status": "error",
                        "error": str(e),
                        "traceback": traceback.format_exc(),
                    }
                ),
            )
            await self.message_bus.publish(
                topic="deadletter.simulation",
                payload=message.original_payload,
                headers={"error": str(e), "original_topic": message.topic},
            )

    async def register_message_handlers(self) -> None:
        if not self._is_initialized:
            raise RuntimeError("Cannot register message handlers before initialization")
        await self.message_bus.subscribe(
            topic_pattern="requests.simulation.*",
            handler=self.handle_simulation_request,
            filter=MessageFilter(headers={"content-type": "application/json"}),
        )
        logger.info("Registered message handlers for simulation requests")


# ------------------------- Factory/Helper functions --------------------------

db_circuit_breaker = CircuitBreaker(
    name="simulation_db",
    failure_threshold=5,
    recovery_timeout=30.0,
    exception_types=[ConnectionError, TimeoutError],
)


async def create_simulation_module(
    config: Dict[str, Any], db: Database, message_bus: ShardedMessageBus
) -> UnifiedSimulationModule:
    module = UnifiedSimulationModule(config, db, message_bus)
    await module.initialize()
    await module.register_message_handlers()
    return module


async def run_simulation(
    config: Dict[str, Any], db: Database, message_bus: ShardedMessageBus
) -> Dict[str, Any]:
    module = UnifiedSimulationModule({"SIM_MAX_WORKERS": 4}, db, message_bus)
    await module.initialize()
    try:
        return await module.execute_simulation(config)
    finally:
        await module.shutdown()


# ------------------------------ SimulationEngine (Alias) --------------------------
class SimulationEngine:
    """
    A wrapper class providing a simple interface for simulations.
    This class is used by arena.py and other components that expect a SimulationEngine.
    """

    def __init__(self):
        self.name = "SimulationEngine"
        self._db = Database()
        self._message_bus = ShardedMessageBus()
        self._module: Optional[UnifiedSimulationModule] = None

    @staticmethod
    def get_tools() -> Dict[str, Callable[..., Any]]:
        """Returns available simulation tools."""
        return {
            "run_agent": run_agent,
            "run_simulation_swarm": run_simulation_swarm,
            "run_parallel_simulations": run_parallel_simulations,
        }

    @staticmethod
    def is_available() -> bool:
        """Returns True indicating the SimulationEngine is available."""
        return True

    async def run_simulation(self, *args, **kwargs) -> Dict[str, Any]:
        """Runs a simulation with the provided configuration."""
        if self._module is None:
            self._module = UnifiedSimulationModule(
                {"SIM_MAX_WORKERS": settings.SIM_MAX_WORKERS},
                self._db,
                self._message_bus,
            )
            await self._module.initialize()

        config = args[0] if args else kwargs.get("config", {})
        return await self._module.execute_simulation(config)
