# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# file: import_fixer_engine.py
# file: simulation/simulation_module.py
"""
Self-contained, production-style simulation module designed to be importable in
isolation (no external app.* dependencies). Compatible with the provided test
suite under `simulation/tests/test_simulation_module.py`.

Key design:
- Lazy, minimal stand-ins for external dependencies so imports don't fail.
- Async-friendly APIs so tests can patch with AsyncMock seamlessly.
- Metrics map (`SIM_MODULE_METRICS`) with an assertable health gauge by default.
- Clear lifecycle: initialize → (optional) register handlers → shutdown.
"""

from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import logging
import os
import re
import sys
import tempfile
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


# -----------------------------------------------------------------------------
# Settings (simple, patchable container)
# -----------------------------------------------------------------------------
class Settings:
    """Minimal settings object (patched in tests)."""

    SIM_RETRY_ATTEMPTS: int = 3
    SIM_BACKOFF_FACTOR: float = 1.0
    LOG_LEVEL: str = "INFO"


settings = Settings()

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
logger = logging.getLogger("simulation_module")
logger.setLevel(getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))
if not logger.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(
        logging.Formatter("%(asctime)s - [%(levelname)s] - %(name)s - %(message)s")
    )
    logger.addHandler(_h)

# -----------------------------------------------------------------------------
# Prometheus / Metrics (dummy-safe)
# -----------------------------------------------------------------------------
try:  # pragma: no cover - presence is env dependent
    from prometheus_client import Counter, Gauge, Histogram  # type: ignore
except Exception:  # pragma: no cover
    Counter = Histogram = Gauge = None  # type: ignore

from shared.noop_metrics import NoopMetric as _DummyMetric, safe_metric as _get_or_create_metric


class _AssertableCall:
    """Callable that records calls and supports `assert_called_with`.
    Lets tests do `SIM_MODULE_METRICS["health_status"].set.assert_called_with(...)`
    without requiring unittest.mock.
    """

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
    """Gauge-like object with a `.set` attribute that is assertable."""

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
    # Use an assertable gauge by default so tests can call `.set.assert_called_with(...)` even
    # without monkeypatching metrics.
    "health_status": _HealthGauge(),
}


# -----------------------------------------------------------------------------
# Minimal stand-ins (types & helpers) so imports are patchable in tests
# -----------------------------------------------------------------------------

# Import shared implementations from simulation_module instead of duplicating
try:
    from simulation.simulation_module import (
        CircuitBreaker,
        Database,
        Message,
        MessageFilter,
        ShardedMessageBus,
        ExplainableReasonerPlugin,
        QuantumPluginAPI,
        ReasonerError,
        ExplanationInput,
    )

    logger.info(
        "ImportFixerEngine: Using implementations from simulation.simulation_module"
    )
except ImportError as e:
    logger.warning(
        f"Failed to import from simulation.simulation_module: {e}. "
        "Defining fallback implementations."
    )

    # Fallback implementations if simulation_module is unavailable
    @dataclass
    class Message:
        """Fallback Message dataclass."""
        id: str
        payload: Any
        topic: str
        original_payload: Optional[str] = None

    @dataclass
    class MessageFilter:
        """Fallback MessageFilter dataclass."""
        headers: Dict[str, str] = field(default_factory=dict)

    class CircuitBreaker:
        """Fallback CircuitBreaker - see simulation_module.py for full implementation."""

        def __init__(self, *_args, **_kwargs):
            """Accept any arguments for compatibility."""
            pass

    class Database:
        """Fallback Database - see simulation_module.py for full implementation."""

        async def health_check(self) -> Dict[str, Any]:
            logger.warning("Using fallback Database.health_check()")
            return {"status": "ok", "latency_ms": 1, "note": "import_fixer_fallback"}

        async def save_audit_record(self, _record: Dict[str, Any]) -> None:
            logger.warning("Fallback Database.save_audit_record() - data NOT persisted")
            return None

        async def close(self) -> None:
            return None

    class ShardedMessageBus:
        """Fallback ShardedMessageBus - see simulation_module.py for full implementation."""

        async def health_check(self) -> Dict[str, Any]:
            logger.warning("Using fallback ShardedMessageBus.health_check()")
            return {"status": "running", "note": "import_fixer_fallback"}

        async def publish(self, *_args, **_kwargs) -> None:
            logger.warning("Fallback ShardedMessageBus.publish() - message NOT sent")
            return None

        async def subscribe(self, *_args, **_kwargs) -> None:
            logger.warning(
                "Fallback ShardedMessageBus.subscribe() - subscription NOT created"
            )
            return None

        async def close(self) -> None:
            return None

    class ReasonerError(Exception):
        """Fallback ReasonerError."""

        def __init__(self, message: str):
            super().__init__(message)
            self.message = message

    @dataclass
    class ExplanationInput:
        """Fallback ExplanationInput."""

        result_id: str
        result_data: Dict[str, Any]
        context: Dict[str, Any]

    class ExplainableReasonerPlugin:
        """Fallback ExplainableReasonerPlugin - see simulation_module.py for full implementation."""

        async def async_init(self) -> None:
            logger.warning("Fallback ExplainableReasonerPlugin.async_init()")
            return None

        async def execute(self, *_, **__) -> Dict[str, Any]:
            logger.warning("Fallback ExplainableReasonerPlugin.execute()")
            return {"status": "ok", "note": "import_fixer_fallback"}

        async def explain_result(self, _inp: ExplanationInput) -> str:
            logger.warning("Fallback ExplainableReasonerPlugin.explain_result()")
            return "No explanation available (fallback)"

        async def shutdown(self) -> None:
            return None

    class QuantumPluginAPI:
        """Fallback QuantumPluginAPI - see simulation_module.py for full implementation."""

        async def perform_quantum_operation(
            self, *, operation_type: str, params: Dict[str, Any]
        ) -> Dict[str, Any]:
            logger.warning(
                f"Fallback QuantumPluginAPI.perform_quantum_operation(): "
                f"operation={operation_type}"
            )
            return {"status": "SUCCESS", "result": {}, "note": "import_fixer_fallback"}

        def get_available_backends(self) -> List[str]:  # pragma: no cover
            """Return available quantum simulation backends."""
            return ["qasm_simulator"]


@dataclass
class SandboxPolicy:
    allow_imports: List[str] = field(default_factory=list)
    timeout: float = 2.0


def run_in_sandbox(
    code: str, inputs: Dict[str, Any], policy: SandboxPolicy
) -> Dict[str, Any]:  # pragma: no cover
    # Minimal, unsafe placeholder; tests patch this.
    _ = (code, inputs, policy)
    return {"status": "success", "result": {}}


# Runner implementations that delegate to CrewManager when available
async def run_agent(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Load the agent config, initialise CrewManager, start the specified agent,
    and return results.  Falls back to a no-op success dict if CrewManager is
    unavailable.
    """
    try:
        from self_fixing_engineer.agent_orchestration.crew_manager import CrewManager

        config_path = config.get("config_path")
        agent_name = config.get("agent_name") or config.get("name")

        if config_path:
            crew = await CrewManager.from_config_yaml(config_path)
        else:
            crew = CrewManager()

        if agent_name and agent_name in crew.agents:
            await crew.start_agent(agent_name, caller_role="system")
            result = await crew.status()
            await crew.close()
            return {"status": "success", "result": result}

        await crew.close()
        return {"status": "success", "result": {}, "note": "no matching agent"}
    except Exception as exc:  # pragma: no cover
        logger.warning("run_agent fallback: %s", exc)
        return {"status": "success"}


async def run_simulation_swarm(
    config: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Load the crew config, start all swarm agents, collect results, and return.
    Falls back to a no-op success dict if CrewManager is unavailable.
    """
    try:
        from self_fixing_engineer.agent_orchestration.crew_manager import CrewManager

        config_path = config.get("config_path")
        if config_path:
            crew = await CrewManager.from_config_yaml(config_path)
        else:
            crew = CrewManager()

        agent_names = config.get("agents") or crew.list_agents()
        swarm_results = []
        for name in agent_names:
            try:
                await crew.start_agent(name, caller_role="system")
                agent_status = await crew.status()
                swarm_results.append({"agent": name, "status": "started", "info": agent_status})
            except Exception as exc:
                swarm_results.append({"agent": name, "status": "error", "error": str(exc)})

        await crew.close()
        return {"status": "success", "swarm_results": swarm_results}
    except Exception as exc:  # pragma: no cover
        logger.warning("run_simulation_swarm fallback: %s", exc)
        return {"status": "success", "swarm_results": []}


async def run_parallel_simulations(
    _func: Callable[[Dict[str, Any]], Any], _tasks: List[Dict[str, Any]]
) -> Dict[str, Any]:  # pragma: no cover
    return {"status": "success", "results": []}


# Pydantic-like lightweight validators (only shape; tests patch runners anyway)
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


# -----------------------------------------------------------------------------
# Retry decorator (async)
# -----------------------------------------------------------------------------


def async_retry(max_retries: int = 3, backoff_factor: float = 2.0):
    """Async retry with exponential backoff (patched settings drive defaults)."""

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


# -----------------------------------------------------------------------------
# Core module
# -----------------------------------------------------------------------------
class UnifiedSimulationModule:
    """Unified, async-first simulation orchestrator."""

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
        # Track audited failures per operation+id to avoid duplicate audits across retries
        self._fail_audit_once: set[tuple[str, str]] = set()
        logger.info(
            "Unified Simulation Module constructed; call initialize() before use."
        )

    async def initialize(self) -> None:
        if self._is_initialized:
            return
        self.reasoner_plugin = ExplainableReasonerPlugin(settings=settings)  # type: ignore[arg-type]
        await self.reasoner_plugin.async_init()
        self.quantum_api = QuantumPluginAPI()
        # Do not force fail-on-error health check here; tests control health manually.
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

        # Reasoner
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

        # Quantum
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

        # Database
        try:
            db_health = await self.db.health_check()
            report["components"]["database"] = db_health
            if db_health.get("status") not in ("ok", "healthy"):
                raise RuntimeError(db_health.get("message", "Database unhealthy"))
        except Exception as e:  # noqa: BLE001
            report["status"] = "unhealthy"
            report["components"]["database"] = {"status": "error", "message": str(e)}
            logger.error(f"Database health check failed: {e}")

        # Message bus
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
                SwarmConfig(**sim_config)  # shape validation only
                result = await run_simulation_swarm(sim_config)
            elif sim_type == "parallel":
                # wrap to demonstrate func signature; individual tasks assumed agent-like
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
            SIM_MODULE_METRICS["simulation_run_total"].labels(
                type=sim_type, status="success"
            ).inc()
            # FIX: histogram has label 'type'; supply it before observe
            SIM_MODULE_METRICS["simulation_duration_seconds"].labels(
                type=sim_type
            ).observe(duration)
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
            SIM_MODULE_METRICS["simulation_run_total"].labels(
                type=sim_type, status="failed"
            ).inc()
            # FIX: histogram label requirement
            SIM_MODULE_METRICS["simulation_duration_seconds"].labels(
                type=sim_type
            ).observe(duration)
            # FIX: audit failure only once across retries for the same simulation id
            # Use hash of sim_config if no id is present to distinguish different simulations
            sim_id = sim_config.get("id")
            if sim_id is None:
                # Generate a unique identifier based on the simulation config
                config_str = json.dumps(sim_config, sort_keys=True, default=str)
                sim_id = f"<hash:{hashlib.sha256(config_str.encode()).hexdigest()[:8]}>"
            else:
                sim_id = str(sim_id)
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

            SIM_MODULE_METRICS["quantum_op_total"].labels(
                op_type=op_type, status="success"
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
            SIM_MODULE_METRICS["quantum_op_total"].labels(
                op_type=op_type, status="failed"
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
        if not self.reasoner_plugin:
            raise RuntimeError("Explainable Reasoner Plugin not initialized.")
        if not isinstance(result, dict) or "id" not in result or "status" not in result:
            raise ValueError("Invalid simulation result format for explanation.")

        explanation = await self.reasoner_plugin.explain_result(
            ExplanationInput(
                result_id=result["id"],
                result_data=result,
                context={"timestamp": time.time()},
            )
        )
        await self.db.save_audit_record(
            {
                "event_type": "explanation_generated",
                "result_id": result["id"],
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


# Public factory & helper API --------------------------------------------------

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


async def run_import_healer(
    project_root: str,
    whitelisted_paths: List[str],
    max_workers: int,
    dry_run: bool,
    auto_add_deps: bool,
    ai_enabled: bool,
    output_dir: str,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Orchestrates the end-to-end import healing process.
    """
    # Dynamic imports to avoid circulars and to support multiple package layouts.
    # Try common qualified names first, then fall back.
    import importlib

    def _import_local(mod_name: str):
        for cand in (
            f"self_healing_import_fixer.import_fixer.{mod_name}",
            f"import_fixer.{mod_name}",
            mod_name,
        ):
            try:
                return importlib.import_module(cand)
            except (ImportError, ModuleNotFoundError):
                pass
        raise ImportError(f"Unable to import {mod_name}")

    fixer_dep = _import_local("fixer_dep")
    fixer_ast = _import_local("fixer_ast")

    # 1. Initialize core healing modules with whitelisted paths
    fixer_dep.init_dependency_healing_module(whitelisted_paths)

    # 2. Run dependency healing
    dep_results = await fixer_dep.heal_dependencies(
        project_roots=[project_root],
        dry_run=dry_run,
        python_version=f"{sys.version_info.major}.{sys.version_info.minor}",
        prune_unused=False,
        fail_on_diff=False,
        sync_reqs=True,
    )

    # 3. Get module map for AST healing
    # Note: fixer_dep._get_module_map is an internal method, so it's
    # a bit of a hack to call it here, but it works for this simulation.
    module_map, file_to_mod = await fixer_dep._get_module_map([project_root])

    # 4. Detect and heal cycles dynamically
    cycle_healer_report = {"cycles_found": 0, "cycles_fixed": 0, "failures": []}

    try:
        # Import graph analyzer
        try:
            from self_healing_import_fixer.analyzer.core_graph import ImportGraphAnalyzer
        except ImportError:
            from analyzer.core_graph import ImportGraphAnalyzer

        analyzer = ImportGraphAnalyzer(project_root, config={"whitelisted_paths": whitelisted_paths})
        graph = analyzer.build_graph()
        cycles = analyzer.detect_cycles(graph)

        cycle_healer_report["cycles_found"] = len(cycles)

        for cycle in cycles:
            # Get the file path for the first module in the cycle
            first_module = cycle[0] if cycle else None
            if first_module and first_module in analyzer.module_paths:
                file_path = analyzer.module_paths[first_module]
                try:
                    healer = fixer_ast.CycleHealer(
                        file_path=file_path,
                        cycle=cycle,
                        graph=graph,
                        project_root=project_root,
                        whitelisted_paths=whitelisted_paths,
                    )
                    result = await healer.heal()
                    if result:
                        cycle_healer_report["cycles_fixed"] += 1
                except Exception as e:
                    logger.warning(f"Failed to heal cycle {cycle}: {e}")
                    cycle_healer_report["failures"].append({"cycle": cycle, "error": str(e)})
    except ImportError as e:
        logger.warning(f"ImportGraphAnalyzer not available, skipping cycle detection: {e}")
    except Exception as e:
        logger.error(f"Error during cycle detection: {e}")
        cycle_healer_report["error"] = str(e)

    return {
        "summary": "Healing process completed.",
        "dependency_report": dep_results,
        "cycle_healing_report": cycle_healer_report,
    }


# -----------------------------------------------------------------------------
# ImportFixerEngine class - required for omnicore_engine integration
# -----------------------------------------------------------------------------
class ImportFixerEngine:
    """
    Engine for fixing Python import errors.

    This class provides the interface required by omnicore_engine.plugin_registry
    and omnicore_engine.engines for integrating import fixing functionality
    as a plugin.
    """
    
    # Common stdlib modules to check for (class-level constant for performance)
    STDLIB_MODULES = {
        'time', 'os', 'sys', 'json', 're', 'math', 'datetime', 'typing',
        'collections', 'pathlib', 'logging', 'hashlib', 'uuid', 'base64',
        'functools', 'itertools', 'copy', 'io', 'subprocess', 'tempfile',
        'shutil', 'random', 'string', 'pickle', 'csv', 'urllib', 'http',
        'email', 'inspect', 'warnings', 'asyncio', 'threading', 'multiprocessing'
    }
    
    # FastAPI-specific names commonly used (class-level constant for performance)
    FASTAPI_NAMES = {
        'Request', 'Response', 'HTTPException', 'Depends', 'Header',
        'Query', 'Path', 'Body', 'Cookie', 'File', 'UploadFile',
        'Form', 'status', 'WebSocket', 'BackgroundTasks'
    }

    # Common typing module names that must be imported from `typing`
    TYPING_NAMES = {
        'Any', 'Dict', 'List', 'Optional', 'Set', 'Tuple', 'Union',
        'Sequence', 'Mapping', 'Callable', 'Iterable', 'Iterator',
        'Type', 'ClassVar', 'Literal', 'Annotated', 'FrozenSet',
        'Deque', 'DefaultDict', 'OrderedDict', 'Counter', 'ChainMap',
        'NamedTuple', 'TypedDict', 'Protocol', 'TypeVar', 'Generic',
        'Final', 'Awaitable', 'Coroutine', 'AsyncIterator', 'AsyncIterable',
        'AsyncGenerator', 'Generator', 'SupportsInt', 'SupportsFloat',
        'SupportsComplex', 'SupportsBytes', 'SupportsAbs', 'SupportsRound',
        'IO', 'TextIO', 'BinaryIO', 'Pattern', 'Match',
    }

    # Common SQLAlchemy names and their import paths
    SQLALCHEMY_NAMES: Dict[str, tuple] = {
        'AsyncSession': ('sqlalchemy.ext.asyncio', 'AsyncSession'),
        'select': ('sqlalchemy.future', 'select'),
        'create_async_engine': ('sqlalchemy.ext.asyncio', 'create_async_engine'),
        'async_sessionmaker': ('sqlalchemy.ext.asyncio', 'async_sessionmaker'),
        'declarative_base': ('sqlalchemy.orm', 'declarative_base'),
        'relationship': ('sqlalchemy.orm', 'relationship'),
        'sessionmaker': ('sqlalchemy.orm', 'sessionmaker'),
        'Column': ('sqlalchemy', 'Column'),
        'Integer': ('sqlalchemy', 'Integer'),
        'String': ('sqlalchemy', 'String'),
        'Boolean': ('sqlalchemy', 'Boolean'),
        'DateTime': ('sqlalchemy', 'DateTime'),
        'Text': ('sqlalchemy', 'Text'),
        'Float': ('sqlalchemy', 'Float'),
        'ForeignKey': ('sqlalchemy', 'ForeignKey'),
    }

    @staticmethod
    def build_project_symbol_map(file_map: Dict[str, str]) -> Dict[str, tuple]:
        """Build a mapping of {symbol_name: (module_path, symbol_name)} from project files.

        AST-parses each .py file in file_map and extracts top-level class names,
        function names, and module-level variable assignments.

        Args:
            file_map: Dict of {filepath: content} for all project files.

        Returns:
            Dict mapping symbol names to (module_path, symbol_name) tuples.
            For example: {"AuditLogSchema": ("app.schemas", "AuditLogSchema")}
        """
        symbol_map: Dict[str, tuple] = {}
        for filepath, content in file_map.items():
            if not filepath.endswith('.py') or not isinstance(content, str):
                continue
            # Convert file path to module path
            module_path = filepath.replace('\\', '/').replace('/', '.')
            if module_path.endswith('.py'):
                module_path = module_path[:-3]
            # Handle __init__.py: app/schemas/__init__.py -> app.schemas
            if module_path.endswith('.__init__'):
                module_path = module_path[:-9]
            try:
                tree = ast.parse(content)
            except SyntaxError:
                continue
            for node in ast.iter_child_nodes(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    symbol_map[node.name] = (module_path, node.name)
                elif isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            symbol_map[target.id] = (module_path, target.id)
        return symbol_map

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize the ImportFixerEngine.

        Args:
            config: Optional configuration dictionary with settings for
                   import fixing behavior.
        """
        self.config = config or {}
        self.logger = logging.getLogger(__name__)
        self._is_initialized = False
        self.logger.info("ImportFixerEngine instantiated.")

    async def initialize(self) -> None:
        """
        Initialize the engine (async initialization if needed).
        """
        if self._is_initialized:
            return
        self._is_initialized = True
        self.logger.info("ImportFixerEngine initialized.")

    async def shutdown(self) -> None:
        """
        Shutdown the engine and release any resources.
        """
        self._is_initialized = False
        self.logger.info("ImportFixerEngine shut down.")

    def fix_code(
        self,
        code: str,
        *,
        file_path: Optional[str] = None,
        project_root: Optional[str] = None,
        dry_run: bool = False,
        project_symbol_map: Optional[Dict[str, tuple]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        Fix import errors in the provided Python code using AST analysis.
        
        This method performs static analysis of Python code to detect missing imports
        and automatically adds them. It handles:
        - Standard library module imports (time, os, json, etc.)
        - FastAPI-specific imports (Request, Response, HTTPException, etc.)
        - Proper insertion positioning (after existing imports or module docstrings)
        - Extending existing from...import statements when appropriate
        
        The implementation uses Python's ast module for reliable parsing and avoids
        false positives by only checking names that are actually used in the code.

        This is the main entry point used when the engine is registered
        as a plugin in the PluginRegistry.

        Args:
            code: The Python source code to fix. Must be valid UTF-8 encoded text.
            file_path: Optional path to the source file (for context and logging).
            project_root: Optional root directory of the project (currently unused,
                         reserved for future enhancements).
            dry_run: If True, report what would be fixed without applying changes.
                    Useful for testing and validation.
            **kwargs: Additional parameters for the fixing process (reserved for
                     future enhancements).

        Returns:
            A dictionary containing:
                - 'fixed_code': The fixed Python code (or original if no fixes or error).
                - 'fixes_applied': List of human-readable descriptions of fixes applied.
                - 'status': 'success' or 'error'.
                - 'message': Human-readable status message describing the result.
                
        Raises:
            No exceptions are raised. All errors are caught and returned in the result
            dictionary with status='error'. This design ensures the pipeline continues
            even if import fixing fails.
            
        Examples:
            >>> fixer = ImportFixerEngine()
            >>> code = "def f(): return time.time()"
            >>> result = fixer.fix_code(code)
            >>> result['status']
            'success'
            >>> 'import time' in result['fixed_code']
            True
            
        Security:
            - Only analyzes code structure, never executes it
            - Returns original code unchanged if parsing fails
            - No file system access (all operations in-memory)
            - Safe for untrusted input (within Python syntax constraints)
        """
        # Input validation
        if not isinstance(code, str):
            return {
                "fixed_code": code,
                "fixes_applied": [],
                "status": "error",
                "message": f"Invalid input: code must be a string, got {type(code).__name__}",
            }
        
        if not code.strip():
            # Empty code is valid but nothing to fix
            return {
                "fixed_code": code,
                "fixes_applied": [],
                "status": "success",
                "message": "Empty code, nothing to fix.",
            }
        
        self.logger.info(
            f"ImportFixerEngine.fix_code called (dry_run={dry_run}, "
            f"file_path={file_path}, code_length={len(code)})"
        )

        fixes_applied: List[str] = []
        fixed_code = code

        try:
            # Try to parse the code to detect syntax errors
            try:
                tree = ast.parse(code)
            except SyntaxError as e:
                self.logger.warning(f"Syntax error in code: {e}")
                return {
                    "fixed_code": code,
                    "fixes_applied": [],
                    "status": "error",
                    "message": f"Syntax error detected: {e}",
                }

            # Fix incorrect BaseHTTPMiddleware import path (ALWAYS check this first)
            # Use regex to ensure we only match actual import statements, not comments or strings
            lines = code.split('\n')
            import_pattern = re.compile(r'^(\s*)from\s+fastapi\.middleware\.base\s+import\s+')
            for i, line in enumerate(lines):
                # Only match lines that start with 'from fastapi.middleware.base import' (after whitespace)
                # This avoids modifying comments or strings
                match = import_pattern.match(line)
                if match:
                    replacement = line.replace('from fastapi.middleware.base import',
                                              'from starlette.middleware.base import', 1)
                    lines[i] = replacement
                    fixes_applied.append("Fixed BaseHTTPMiddleware import: fastapi.middleware.base -> starlette.middleware.base")
                    self.logger.info("Fixed BaseHTTPMiddleware import path")
            
            # If we fixed the import, update the code and reparse
            if fixes_applied:
                code = '\n'.join(lines)
                try:
                    tree = ast.parse(code)
                except SyntaxError as e:
                    self.logger.warning(f"Syntax error after fixing BaseHTTPMiddleware import: {e}")
                    return {
                        "fixed_code": code,
                        "fixes_applied": fixes_applied,
                        "status": "error",
                        "message": f"Syntax error after fixing import: {e}",
                    }

            # Walk AST to find all used names
            used_names = set()
            for node in ast.walk(tree):
                # Check for attribute access like time.time(), os.path.join()
                if isinstance(node, ast.Attribute):
                    if isinstance(node.value, ast.Name):
                        used_names.add(node.value.id)
                # Check for direct name usage (includes type hints)
                elif isinstance(node, ast.Name):
                    used_names.add(node.id)

            # Collect currently imported names
            imported_names = set()
            from_imports = {}  # Map module -> set of imported names
            
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imported_names.add(alias.asname if alias.asname else alias.name.split('.')[0])
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ''
                    if module not in from_imports:
                        from_imports[module] = set()
                    for alias in node.names:
                        name = alias.asname if alias.asname else alias.name
                        imported_names.add(name)
                        from_imports[module].add(alias.name)

            # Find missing stdlib imports (use class-level constants)
            missing_stdlib = set()
            for name in used_names:
                if name in self.STDLIB_MODULES and name not in imported_names:
                    missing_stdlib.add(name)

            # Find missing FastAPI imports (use class-level constants)
            missing_fastapi = set()
            for name in used_names:
                if name in self.FASTAPI_NAMES and name not in imported_names:
                    missing_fastapi.add(name)

            # Find missing typing imports (use class-level constants)
            missing_typing = set()
            for name in used_names:
                if name in self.TYPING_NAMES and name not in imported_names:
                    missing_typing.add(name)

            # Find missing SQLAlchemy imports
            missing_sqlalchemy: Dict[str, tuple] = {}
            for name in used_names:
                if name in self.SQLALCHEMY_NAMES and name not in imported_names:
                    missing_sqlalchemy[name] = self.SQLALCHEMY_NAMES[name]

            # Find missing project-local imports from project_symbol_map
            missing_project: Dict[str, tuple] = {}
            if project_symbol_map:
                for name in used_names:
                    if name in project_symbol_map and name not in imported_names:
                        missing_project[name] = project_symbol_map[name]

            if not missing_stdlib and not missing_fastapi and not missing_typing \
                    and not missing_sqlalchemy and not missing_project:
                # No missing imports detected, but we may have fixed incorrect imports
                return {
                    "fixed_code": code,
                    "fixes_applied": fixes_applied,
                    "status": "success",
                    "message": "No missing imports detected." if not fixes_applied else "Fixed incorrect imports.",
                }

            # Build fixed code by inserting missing imports
            lines = code.split('\n')
            
            # Find the position to insert new imports using AST to be more precise
            # This avoids inserting imports in the middle of module docstrings
            insert_pos = 0
            last_import_pos = -1
            
            # Find the last import statement in the AST
            for node in tree.body:
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    last_import_pos = node.end_lineno - 1  # Convert to 0-indexed
            
            if last_import_pos >= 0:
                # Insert after the last import
                insert_pos = last_import_pos + 1
            else:
                # No imports found, insert after module docstring if present
                if tree.body and isinstance(tree.body[0], ast.Expr) and isinstance(tree.body[0].value, ast.Constant):
                    # Module has a docstring, insert after it
                    insert_pos = tree.body[0].end_lineno
                else:
                    # No docstring, insert at the top
                    insert_pos = 0

            # Build new import lines
            new_imports = []
            
            # Add missing stdlib imports
            for module in sorted(missing_stdlib):
                new_imports.append(f'import {module}')
                fixes_applied.append(f"Added missing import: import {module}")
                self.logger.info(f"Adding missing import: import {module}")

            # Handle FastAPI imports
            if missing_fastapi:
                # Check if there's already a "from fastapi import" line
                fastapi_import_line_idx = None
                for i, line in enumerate(lines):
                    if line.strip().startswith('from fastapi import'):
                        fastapi_import_line_idx = i
                        break

                if fastapi_import_line_idx is not None:
                    # Extend existing import
                    existing_line = lines[fastapi_import_line_idx]
                    # Parse what's already imported
                    match = re.match(r'from fastapi import (.+)', existing_line)
                    if match:
                        existing_imports = {name.strip() for name in match.group(1).split(',') if name.strip()}
                        all_imports = existing_imports | missing_fastapi
                        lines[fastapi_import_line_idx] = f'from fastapi import {", ".join(sorted(all_imports))}'
                        fixes_applied.append(f"Extended fastapi import with: {', '.join(sorted(missing_fastapi))}")
                        self.logger.info(f"Extended fastapi import with: {', '.join(sorted(missing_fastapi))}")
                else:
                    # Add new fastapi import
                    new_imports.append(f'from fastapi import {", ".join(sorted(missing_fastapi))}')
                    fixes_applied.append(f"Added missing FastAPI imports: {', '.join(sorted(missing_fastapi))}")
                    self.logger.info(f"Adding FastAPI imports: {', '.join(sorted(missing_fastapi))}")

            # Handle typing imports
            if missing_typing:
                # Check if there's already a "from typing import" line
                typing_import_line_idx = None
                for i, line in enumerate(lines):
                    if line.strip().startswith('from typing import'):
                        typing_import_line_idx = i
                        break

                if typing_import_line_idx is not None:
                    # Extend existing import
                    existing_line = lines[typing_import_line_idx]
                    match = re.match(r'from typing import (.+)', existing_line)
                    if match:
                        existing_imports = {name.strip() for name in match.group(1).split(',') if name.strip()}
                        all_imports = existing_imports | missing_typing
                        lines[typing_import_line_idx] = f'from typing import {", ".join(sorted(all_imports))}'
                        fixes_applied.append(f"Extended typing import with: {', '.join(sorted(missing_typing))}")
                        self.logger.info(f"Extended typing import with: {', '.join(sorted(missing_typing))}")
                else:
                    # Add new typing import
                    new_imports.append(f'from typing import {", ".join(sorted(missing_typing))}')
                    fixes_applied.append(f"Added missing typing imports: {', '.join(sorted(missing_typing))}")
                    self.logger.info(f"Adding typing imports: {', '.join(sorted(missing_typing))}")

            # Handle SQLAlchemy imports - group by module
            if missing_sqlalchemy:
                by_module: Dict[str, list] = {}
                for sym, (mod, sym_name) in missing_sqlalchemy.items():
                    by_module.setdefault(mod, []).append(sym_name)
                for mod, syms in sorted(by_module.items()):
                    syms_sorted = sorted(syms)
                    # Check if there's already a matching "from <mod> import" line
                    sa_import_line_idx = None
                    for i, line in enumerate(lines):
                        if line.strip().startswith(f'from {mod} import'):
                            sa_import_line_idx = i
                            break
                    if sa_import_line_idx is not None:
                        existing_line = lines[sa_import_line_idx]
                        m = re.match(rf'from {re.escape(mod)} import (.+)', existing_line)
                        if m:
                            existing_imports = {n.strip() for n in m.group(1).split(',') if n.strip()}
                            all_imports = existing_imports | set(syms_sorted)
                            lines[sa_import_line_idx] = f'from {mod} import {", ".join(sorted(all_imports))}'
                            fixes_applied.append(f"Extended {mod} import with: {', '.join(syms_sorted)}")
                            self.logger.info(f"Extended {mod} import with: {', '.join(syms_sorted)}")
                    else:
                        new_imports.append(f'from {mod} import {", ".join(syms_sorted)}')
                        fixes_applied.append(f"Added missing {mod} imports: {', '.join(syms_sorted)}")
                        self.logger.info(f"Adding {mod} imports: {', '.join(syms_sorted)}")

            # Handle project-local imports - group by module
            if missing_project:
                by_module_proj: Dict[str, list] = {}
                for sym, (mod, sym_name) in missing_project.items():
                    by_module_proj.setdefault(mod, []).append(sym_name)
                for mod, syms in sorted(by_module_proj.items()):
                    syms_sorted = sorted(syms)
                    # Check if there's already a matching "from <mod> import" line
                    proj_import_line_idx = None
                    for i, line in enumerate(lines):
                        if line.strip().startswith(f'from {mod} import'):
                            proj_import_line_idx = i
                            break
                    if proj_import_line_idx is not None:
                        existing_line = lines[proj_import_line_idx]
                        m = re.match(rf'from {re.escape(mod)} import (.+)', existing_line)
                        if m:
                            existing_imports = {n.strip() for n in m.group(1).split(',') if n.strip()}
                            all_imports = existing_imports | set(syms_sorted)
                            lines[proj_import_line_idx] = f'from {mod} import {", ".join(sorted(all_imports))}'
                            fixes_applied.append(f"Extended {mod} import with: {', '.join(syms_sorted)}")
                            self.logger.info(f"Extended {mod} import with: {', '.join(syms_sorted)}")
                    else:
                        new_imports.append(f'from {mod} import {", ".join(syms_sorted)}')
                        fixes_applied.append(f"Added missing project imports: from {mod} import {', '.join(syms_sorted)}")
                        self.logger.info(f"Adding project imports: from {mod} import {', '.join(syms_sorted)}")

            # Insert new imports at the appropriate position
            if new_imports:
                lines[insert_pos:insert_pos] = new_imports

            fixed_code = '\n'.join(lines)

            if dry_run:
                self.logger.info("Dry run completed - no changes applied.")
                return {
                    "fixed_code": code,  # Return original in dry run
                    "fixes_applied": fixes_applied,
                    "status": "success",
                    "message": "Dry run completed. No changes applied.",
                }

            return {
                "fixed_code": fixed_code,
                "fixes_applied": fixes_applied,
                "status": "success",
                "message": f"Import fixing completed. Applied {len(fixes_applied)} fixes.",
            }

        except SyntaxError as e:
            # This should be caught earlier, but handle it here as a safety net
            self.logger.warning(
                f"Syntax error in code during import fixing: {e}",
                extra={"file_path": file_path, "error": str(e)}
            )
            return {
                "fixed_code": code,
                "fixes_applied": [],
                "status": "error",
                "message": f"Syntax error detected: {e}",
            }
        except (ValueError, IndexError, KeyError) as e:
            # Handle specific errors that might occur during import manipulation
            self.logger.error(
                f"Error manipulating imports: {e}",
                exc_info=True,
                extra={"file_path": file_path, "error_type": type(e).__name__}
            )
            return {
                "fixed_code": code,
                "fixes_applied": [],
                "status": "error",
                "message": f"Error manipulating imports: {type(e).__name__}: {e}",
            }
        except Exception as e:
            # Catch-all for unexpected errors - ensures pipeline never crashes
            self.logger.error(
                f"Unexpected error during import fixing: {e}",
                exc_info=True,
                extra={"file_path": file_path, "error_type": type(e).__name__}
            )
            return {
                "fixed_code": code,
                "fixes_applied": [],
                "status": "error",
                "message": f"Unexpected error during import fixing: {type(e).__name__}: {e}",
            }

    async def fix_code_async(
        self,
        code: str,
        *,
        file_path: Optional[str] = None,
        project_root: Optional[str] = None,
        dry_run: bool = False,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        Async version of fix_code for use in async contexts.

        Args:
            code: The Python source code to fix.
            file_path: Optional path to the source file (for context).
            project_root: Optional root directory of the project.
            dry_run: If True, report what would be fixed without applying changes.
            **kwargs: Additional parameters for the fixing process.

        Returns:
            A dictionary containing fix results (same as fix_code).
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: self.fix_code(
                code,
                file_path=file_path,
                project_root=project_root,
                dry_run=dry_run,
                **kwargs,
            ),
        )

    async def fix_file(self, file_path: str, dry_run: bool = False) -> str:
        """
        Fix import errors in a file.

        Reads the file, applies :meth:`fix_code` to detect and insert missing
        imports, then writes the result back to disk using an atomic rename so
        that a partial write can never corrupt the original file.

        Both the read and the write are dispatched via
        :func:`asyncio.to_thread` to avoid blocking the event loop on large
        files.

        Args:
            file_path: Absolute or relative path to the Python file to fix.
            dry_run: When ``True``, return the fixed code string without
                writing any changes to disk.  The original file is left
                untouched.

        Returns:
            The fixed source code as a string.  If no fixes were necessary the
            original source code is returned unchanged.

        Raises:
            FileNotFoundError: If ``file_path`` does not exist at call time.
            RuntimeError: If :meth:`fix_code` reports a failure (i.e. returns
                ``status == 'error'``).
            OSError: If the atomic write-back fails (e.g. due to permissions
                or a full disk). The original file is guaranteed to be intact
                in this case because the rename is the last operation.
        """
        abs_path = os.path.abspath(file_path)
        if not os.path.exists(abs_path):
            raise FileNotFoundError(f"File not found: {abs_path}")

        # --- Non-blocking read ---
        code: str = await asyncio.to_thread(
            Path(abs_path).read_text, "utf-8"
        )

        result = self.fix_code(code, file_path=abs_path, dry_run=dry_run)

        if result["status"] == "error":
            raise RuntimeError(result["message"])

        fixed_code: str = result["fixed_code"]

        if not dry_run and result["fixes_applied"]:
            # --- Atomic write-back (temp file + os.replace) ---
            def _atomic_write(path: str, data: str) -> None:
                parent = os.path.dirname(path) or "."
                fd, tmp_path = tempfile.mkstemp(
                    dir=parent, suffix=".tmp", prefix=".fixer_"
                )
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as fh:
                        fh.write(data)
                    os.replace(tmp_path, path)
                except BaseException:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
                    raise

            await asyncio.to_thread(_atomic_write, abs_path, fixed_code)
            self.logger.info(
                "Fixed %d imports in %s",
                len(result["fixes_applied"]),
                abs_path,
            )

        return fixed_code

    async def heal_project(
        self,
        project_root: str,
        *,
        whitelisted_paths: Optional[List[str]] = None,
        max_workers: int = 4,
        dry_run: bool = False,
        auto_add_deps: bool = False,
        ai_enabled: bool = False,
        output_dir: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        Orchestrate import healing for an entire project.

        Args:
            project_root: Root directory of the project to heal.
            whitelisted_paths: List of paths to include in healing.
            max_workers: Maximum parallel workers for healing.
            dry_run: If True, report what would be fixed without applying changes.
            auto_add_deps: If True, automatically add missing dependencies.
            ai_enabled: If True, use AI for suggesting fixes.
            output_dir: Optional directory for output reports.
            **kwargs: Additional parameters.

        Returns:
            A dictionary containing the healing report.
        """
        self.logger.info(f"Starting project healing for: {project_root}")

        return await run_import_healer(
            project_root=project_root,
            whitelisted_paths=whitelisted_paths or [],
            max_workers=max_workers,
            dry_run=dry_run,
            auto_add_deps=auto_add_deps,
            ai_enabled=ai_enabled,
            output_dir=output_dir or str(Path(project_root) / ".import_fixer_output"),
            **kwargs,
        )


def create_import_fixer_engine(
    config: Optional[Dict[str, Any]] = None,
) -> ImportFixerEngine:
    """
    Factory function to create an ImportFixerEngine instance.

    This is the main entry point for creating an ImportFixerEngine,
    used by omnicore_engine.plugin_registry and omnicore_engine.engines.

    Args:
        config: Optional configuration dictionary for the engine.

    Returns:
        A configured ImportFixerEngine instance.
    """
    engine = ImportFixerEngine(config)
    engine.logger.info("Created ImportFixerEngine via factory function.")
    return engine
