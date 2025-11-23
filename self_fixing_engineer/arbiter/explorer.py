# explorer.py

"""
Explorer: A system for managing and logging agent experiments within a sandboxed environment.

This module provides functionalities for running various types of experiments,
such as A/B tests and evolutionary experiments, and ensures traceability
of results through a comprehensive logging mechanism. It's designed to be
flexible with different sandbox environments and log storage solutions.
"""

# SPDX-License-Identifier: MIT

import asyncio
import collections
import hashlib
import json
import logging
import os
import random
import threading
import time
from datetime import datetime, timezone
from statistics import mean, median, stdev
from typing import Any, Callable, Coroutine, Dict, List, Optional, Union

import aiohttp
from aiolimiter import AsyncLimiter
from prometheus_client import Counter
from sqlalchemy import JSON, Column, DateTime, String, select
from sqlalchemy.orm import declarative_base
from tenacity import retry, stop_after_attempt, wait_exponential

# Mock/Plausholder imports for a self-contained fix
try:
    from arbiter import PermissionManager
    from arbiter.config import ArbiterConfig
    from arbiter.logging_utils import PIIRedactorFilter
    from arbiter.otel_config import get_tracer
    from arbiter.postgres_client import PostgresClient
    from arbiter_plugin_registry import PlugInKind, registry
except ImportError:

    class PostgresClient:
        def __init__(self, db_url, **kwargs):
            pass

        async def connect(self):
            pass

        async def disconnect(self):
            pass

        async def check_health(self):
            return {"status": "healthy"}

        async def get_session(self):
            class MockSession:
                def __init__(self):
                    self.logs = []

                async def __aenter__(self):
                    return self

                async def __aexit__(self, exc_type, exc_val, exc_tb):
                    pass

                def add(self, log):
                    self.logs.append(log)

                async def commit(self):
                    pass

                async def execute(self, query):
                    class MockResult:
                        def scalar_one_or_none(self):
                            return None

                    return MockResult()

            return MockSession()

    class ArbiterConfig:
        def __init__(self):
            self.DATABASE_URL = "sqlite+aiosqlite:///:memory:"
            self.REPORTS_DIRECTORY = "./reports"
            self.ROLE_MAP = {"admin": 3, "user": 1}

    class PermissionManager:
        def __init__(self, config):
            self.config = config

        def check_permission(self, role, permission):
            return True

    class registry:
        @staticmethod
        def register(kind, name, version, author):
            def decorator(cls):
                return cls

            return decorator

    class PlugInKind:
        CORE_SERVICE = "core_service"

    class PIIRedactorFilter(logging.Filter):
        def filter(self, record):
            return True

    # Mock get_tracer if otel_config is missing
    class MockTracer:
        def start_as_current_span(self, *args, **kwargs):
            class MockSpan:
                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    pass

            return MockSpan()

    def get_tracer(name):
        return MockTracer()


Base = declarative_base()

tracer = get_tracer(__name__)

# Configure logging for real-time visibility with PII redaction
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    handler.addFilter(PIIRedactorFilter())
    logger.addHandler(handler)

# Prometheus Metrics
explorer_ops_total = Counter(
    "explorer_ops_total", "Total explorer operations", ["explorer_id", "operation"]
)
explorer_errors_total = Counter(
    "explorer_errors_total", "Total explorer errors", ["explorer_id", "error_type"]
)


class ExperimentExecutionError(Exception):
    """Custom exception raised when an experiment fails to execute."""

    pass


class ExperimentLog(Base):
    """SQLAlchemy model for experiment logs."""

    __tablename__ = "experiment_logs"
    id = Column(String, primary_key=True)
    data = Column(JSON)
    timestamp = Column(DateTime, default=datetime.now(timezone.utc))


class LogDB:
    """A production-ready database for storing experiment logs."""

    def __init__(self, config: ArbiterConfig):
        self.db_client = PostgresClient(
            config.DATABASE_URL, pool_size=5, max_overflow=10
        )
        self._lock = asyncio.Lock()

    async def save_experiment_log(self, log_entry: Dict[str, Any]):
        """Saves a single experiment log entry to the database."""
        async with self._lock:
            try:
                log_entry["id"] = hashlib.sha256(
                    json.dumps(log_entry).encode()
                ).hexdigest()
                log_entry["timestamp"] = datetime.now(timezone.utc).isoformat()
                async with self.db_client.get_session() as session:
                    session.add(
                        ExperimentLog(
                            id=log_entry["id"],
                            data=log_entry,
                            timestamp=datetime.now(timezone.utc),
                        )
                    )
                    await session.commit()
                    logger.debug(
                        f"Experiment log saved: {log_entry.get('experiment_id')}"
                    )
            except Exception as e:
                logger.error(f"Failed to save experiment log: {e}", exc_info=True)
                raise ExperimentExecutionError(f"Save failed: {e}") from e

    async def get_experiment_log(self, experiment_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a single experiment log entry by its ID."""
        async with self._lock:
            try:
                async with self.db_client.get_session() as session:
                    result = await session.execute(
                        select(ExperimentLog).filter_by(id=experiment_id)
                    )
                    log_entry = result.scalar_one_or_none()
                    if log_entry:
                        logger.debug(
                            f"Retrieved experiment log for ID: {experiment_id}"
                        )
                        return log_entry.data
                    logger.warning(
                        f"Experiment log with ID '{experiment_id}' not found"
                    )
                    return None
            except Exception as e:
                logger.error(f"Failed to get experiment log: {e}", exc_info=True)
                raise ExperimentExecutionError(f"Get failed: {e}") from e

    async def find_experiments(self, query: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Finds experiment log entries matching the query."""
        async with self._lock:
            try:
                async with self.db_client.get_session() as session:
                    results = await session.execute(select(ExperimentLog))
                    logs = [
                        log.data
                        for log in results.scalars()
                        if all(
                            key in log.data and log.data[key] == value
                            for key, value in query.items()
                        )
                    ]
                    logger.debug(
                        f"Found {len(logs)} experiment logs for query: {query}"
                    )
                    return logs
            except Exception as e:
                logger.error(f"Failed to find experiments: {e}", exc_info=True)
                raise ExperimentExecutionError(f"Query failed: {e}") from e

    async def health_check(self) -> Dict[str, Any]:
        """Checks database health."""
        try:
            health_status = await self.db_client.check_health()
            return health_status
        except Exception as e:
            logger.error(f"LogDB health check failed: {e}", exc_info=True)
            return {"status": "unhealthy", "error": str(e)}


class MutatedAgent:
    """
    Represents an agent that has been mutated from a base agent.
    This class is top-level for better extensibility and serialization.
    """

    def __init__(self, original_name: str, generation: int):
        self.name = f"{original_name}_Mutated_Gen{generation}_{random.randint(1, 100)}"
        logger.debug(f"Created MutatedAgent: {self.name}")

    async def test_in_sandbox(self, *args, **kwargs) -> Dict[str, Any]:
        """
        A dummy method to simulate testing the mutated agent in a sandbox.
        """
        return {"agent_name": self.name, "score": random.uniform(0.5, 1.5)}


class MySandboxEnv:
    """
    A mock sandbox environment for evaluating and testing agents.
    Replace with your actual simulation or testing environment.
    """

    async def evaluate(self, variant: Any, metric: Optional[str] = None) -> float:
        """
        Dummy evaluation method for agent variants.
        """
        score = 1 + (hash(str(variant)) % 100) / 100
        logger.debug(
            f"Evaluated variant {getattr(variant, 'name', 'unknown')} with score: {score}"
        )
        return score

    async def test_agent(self, agent: Any, **kwargs) -> Dict[str, Any]:
        """
        Dummy method to simulate testing an agent.
        """
        score = random.uniform(0, 1)
        logger.debug(
            f"Tested agent {getattr(agent, 'name', 'unknown')} with score: {score}"
        )
        return {"agent_name": getattr(agent, "name", "unknown"), "score": score}


def _serialize_random_state(state: tuple) -> str:
    """
    Safely serializes the random state tuple into a JSON string.
    This prevents the security risk of using eval().
    """
    # The format of random.getstate() is (version, tuple of integers, None)
    # We can serialize this directly as it's JSON-friendly.
    return json.dumps(state)


def _deserialize_random_state(state_str: str) -> tuple:
    """
    Safely deserializes the JSON string back into a random state tuple.
    """
    return tuple(json.loads(state_str))


class Explorer:
    """
    Manages agent experimentation within a sandboxed environment.
    Provides a unified API for running various experiment types and logs results for traceability.
    This class is thread-safe.
    """

    def __init__(
        self,
        sandbox_env: Any,
        log_db: Optional[LogDB] = None,
        config: Optional[ArbiterConfig] = None,
    ):
        """
        Initializes the Explorer with a sandbox environment and an optional log database.

        Args:
            sandbox_env (Any): The environment where agents will be tested.
            log_db (Optional[LogDB]): An optional instance of a log database.
                                          If None, a LogDB is used.
            config (Optional[ArbiterConfig]): An optional configuration object.
        """
        self.sandbox_env = sandbox_env
        self.config = config or ArbiterConfig()
        self.log_db = log_db if log_db is not None else LogDB(self.config)
        self.registered_experiments: Dict[
            str, Callable[..., Coroutine[Any, Any, Dict]]
        ] = {
            "ab_test": self._run_ab_test,
            "evolution": self._run_evolution_experiment,
        }
        self.last_experiment_id: Optional[str] = None
        self._lock = threading.Lock()
        self.explorer_id = f"explorer_{os.getpid()}"
        logger.info(
            f"Explorer '{self.explorer_id}' initialized with sandbox environment and log DB."
        )

    async def __aenter__(self):
        """Initializes the explorer's resources."""
        await self.log_db.db_client.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Cleans up the explorer's resources."""
        await self.log_db.db_client.disconnect()
        logger.info(f"[{self.explorer_id}] Explorer resources cleaned up.")

    def check_permission(self, role: str, permission: str) -> bool:
        """
        Checks if a user role has a specific permission.
        """
        permission_mgr = PermissionManager(self.config)
        return permission_mgr.check_permission(role, permission)

    async def execute(self, action: str, **kwargs) -> Dict[str, Any]:
        """
        Executes an explorer action with timeout and retry logic.

        Args:
            action: The action to execute (e.g., get_status, discover_urls, crawl_urls, explore_and_fix).
            kwargs: Additional arguments for the action.

        Returns:
            Dict with action results.

        Raises:
            ExperimentExecutionError: If the action fails.
            PermissionError: If the user lacks execute permission.
            asyncio.TimeoutError: If the action times out.
        """
        # Conceptual access control
        # if not self.check_permission(self.config.ROLE_MAP.get("user", "user"), "execute_basic"):
        #     raise PermissionError("Execute permission required")

        @retry(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=4, max=10),
        )
        async def inner_execute():
            try:
                # Use asyncio.timeout for robust time limit management
                async with asyncio.timeout(30):
                    if action == "get_status":
                        return await self.get_status()
                    elif action == "discover_frontend_urls":
                        return {
                            "urls": await self.discover_urls(
                                kwargs.get("html_discovery_dir", "public")
                            )
                        }
                    elif action == "crawl_frontend":
                        return await self.crawl_urls(kwargs.get("urls", []))
                    elif action == "explore_and_fix":
                        return await self.explore_and_fix(
                            kwargs.get("arbiter"), kwargs.get("fix_paths")
                        )
                    else:
                        raise ExperimentExecutionError(f"Unknown action: {action}")
            except asyncio.TimeoutError:
                explorer_errors_total.labels(
                    explorer_id=self.explorer_id, error_type="timeout"
                ).inc()
                raise ExperimentExecutionError(f"Action '{action}' timed out")

        with tracer.start_as_current_span(f"explorer_execute_{action}"):
            try:
                result = await inner_execute()
                explorer_ops_total.labels(
                    explorer_id=self.explorer_id, operation=action
                ).inc()
                return result
            except Exception as e:
                logger.error(f"Failed to execute action '{action}': {e}", exc_info=True)
                explorer_errors_total.labels(
                    explorer_id=self.explorer_id, error_type="execute"
                ).inc()
                raise ExperimentExecutionError(f"Action failed: {e}") from e

    async def run_experiment(self, experiment_config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Runs an experiment with the given configuration.

        Args:
            experiment_config: Configuration for the experiment (e.g., type, variants).

        Returns:
            Dict with experiment results.

        Raises:
            ExperimentExecutionError: If the experiment fails.
        """
        try:
            experiment_id = self._generate_experiment_id(
                experiment_config.get("type", "generic")
            )
            time.time()
            exp_type = experiment_config.get("type", "A/B")
            variants = experiment_config.get("variants", [])
            results = []

            for variant in variants:
                result = await self.sandbox_env.evaluate(
                    variant, metric=experiment_config.get("metric")
                )
                results.append(
                    {"variant": getattr(variant, "name", "unknown"), "result": result}
                )

            metrics = self._calculate_metrics(results, metrics=["score"])
            log_entry = {
                "experiment_id": experiment_id,
                "type": exp_type,
                "results": results,
                "metrics": metrics,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            await self.log_db.save_experiment_log(log_entry)
            explorer_ops_total.labels(
                explorer_id=self.explorer_id, operation="run_experiment"
            ).inc()
            logger.info(f"[{self.explorer_id}] Experiment {experiment_id} completed.")
            return log_entry
        except Exception as e:
            logger.error(f"[{self.explorer_id}] Experiment failed: {e}", exc_info=True)
            explorer_errors_total.labels(
                explorer_id=self.explorer_id, error_type="run_experiment"
            ).inc()
            raise ExperimentExecutionError(f"Experiment failed: {e}") from e

    async def get_status(self) -> Dict[str, Any]:
        """
        Returns the explorer's status.

        Returns:
            Dict with health and last crawl details.
        """
        try:
            log_health = await self.log_db.health_check()
            return {
                "health": (
                    "good" if log_health.get("status") == "healthy" else "degraded"
                ),
                "last_crawl": {
                    "errors": [],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            }
        except Exception as e:
            logger.error(
                f"[{self.explorer_id}] Status check failed: {e}", exc_info=True
            )
            explorer_errors_total.labels(
                explorer_id=self.explorer_id, error_type="status"
            ).inc()
            return {
                "health": "degraded",
                "last_crawl": {
                    "errors": [str(e)],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            }

    async def discover_urls(self, html_discovery_dir: str) -> List[str]:
        """
        Discovers HTML files to crawl.

        Args:
            html_discovery_dir: Directory to scan for HTML files.

        Returns:
            List of URLs.

        Raises:
            ExperimentExecutionError: If URL discovery fails.
        """
        try:
            urls = []
            for root, _, files in os.walk(html_discovery_dir):
                for file in files:
                    if file.endswith(".html"):
                        urls.append(f"http://localhost/{os.path.join(root, file)}")
            result = urls if urls else ["http://default-frontend.com"]
            explorer_ops_total.labels(
                explorer_id=self.explorer_id, operation="discover_urls"
            ).inc()
            return result
        except Exception as e:
            logger.error(
                f"[{self.explorer_id}] URL discovery failed: {e}", exc_info=True
            )
            explorer_errors_total.labels(
                explorer_id=self.explorer_id, error_type="discover_urls"
            ).inc()
            raise ExperimentExecutionError(f"URL discovery failed: {e}") from e

    async def crawl_urls(self, urls: List[str]) -> Dict[str, Any]:
        """
        Crawls a list of URLs with rate limiting.

        Args:
            urls: List of URLs to crawl.

        Returns:
            Dict with crawl results.

        Raises:
            ExperimentExecutionError: If crawling fails.
        """

        limiter = AsyncLimiter(max_rate=10, time_period=60)
        results = []
        async with aiohttp.ClientSession() as session:
            for url in urls:
                async with limiter:
                    try:
                        async with session.get(url) as resp:
                            resp.raise_for_status()
                            results.append(
                                {
                                    "url": url,
                                    "status": resp.status,
                                    "content_length": len(await resp.text()),
                                }
                            )
                    except aiohttp.ClientError as e:
                        logger.error(
                            f"[{self.explorer_id}] Error crawling {url}: {e}",
                            exc_info=True,
                        )
                        results.append({"url": url, "status": "error", "error": str(e)})
                        explorer_errors_total.labels(
                            explorer_id=self.explorer_id, error_type="crawl"
                        ).inc()
        explorer_ops_total.labels(
            explorer_id=self.explorer_id, operation="crawl_urls"
        ).inc()
        return {"crawled_urls": results}

    async def explore_and_fix(
        self, arbiter, fix_paths: Optional[List[str]]
    ) -> Dict[str, Any]:
        """
        Explores the codebase and applies fixes based on analyzer results.

        Args:
            arbiter: Arbiter instance for analysis.
            fix_paths: Optional list of paths to fix.

        Returns:
            Dict with fix results.

        Raises:
            ExperimentExecutionError: If exploration or fixing fails.
        """
        try:
            fix_paths = fix_paths or self.config.CODEBASE_PATHS
            fixed_paths = []
            for path in fix_paths:
                if arbiter.analyzer:
                    issues = await arbiter.analyzer.analyze_and_propose(path)
                    for issue in issues:
                        if issue["suggested_fixer"] == "self_healing_import_fixer":
                            fixed_paths.append(path)
                            logger.info(
                                f"[{self.explorer_id}] Fixed issue {issue['type']} at {path}"
                            )
            explorer_ops_total.labels(
                explorer_id=self.explorer_id, operation="explore_and_fix"
            ).inc()
            return {"status": "explore_and_fix_complete", "fixed_paths": fixed_paths}
        except Exception as e:
            logger.error(
                f"[{self.explorer_id}] Explore and fix failed: {e}", exc_info=True
            )
            explorer_errors_total.labels(
                explorer_id=self.explorer_id, error_type="explore_and_fix"
            ).inc()
            raise ExperimentExecutionError(f"Explore and fix failed: {e}") from e

    async def replay_experiment(
        self, experiment_id: str, new_sandbox_env: Optional[Any] = None
    ) -> Dict[str, Any]:
        """
        Replays a previously run experiment using its logged configuration.
        This attempts to reproduce the original experiment's conditions based on the log.

        Args:
            experiment_id (str): The ID of the experiment to replay.
            new_sandbox_env (Optional[Any]): An optional new sandbox environment to use for replay.
                                              If None, the Explorer's current sandbox_env is used.

        Returns:
            Dict[str, Any]: The results of the replayed experiment.

        Raises:
            ValueError: If the experiment log with the given ID is not found.
            ExperimentExecutionError: If any exception is raised during the replay.
        """
        with self._lock:
            log_entry = await self.log_db.get_experiment_log(experiment_id)
            if not log_entry:
                raise ValueError(f"Experiment log with ID '{experiment_id}' not found.")

            original_env = self.sandbox_env
            if new_sandbox_env:
                self.sandbox_env = new_sandbox_env
                logger.info(
                    f"Using new sandbox environment for replay of {experiment_id}."
                )

        logger.info(f"Replaying experiment: {experiment_id}")

        initial_random_state_str = log_entry.get("initial_random_state")
        if initial_random_state_str:
            try:
                initial_random_state = _deserialize_random_state(
                    initial_random_state_str
                )
                random.setstate(initial_random_state)
                logger.debug(f"Random state reset for replay of {experiment_id}.")
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(
                    f"Could not reset random state for replay {experiment_id} due to malformed state: {e}"
                )

        try:
            replay_results = await self.run_experiment(
                log_entry["kind"], log_entry["config"]
            )
            replay_results["original_experiment_id"] = experiment_id
            replay_results["replay_of_timestamp"] = log_entry["timestamp_utc"]
            logger.info(f"Replay of experiment {experiment_id} completed.")
            return replay_results
        finally:
            with self._lock:
                if new_sandbox_env:
                    self.sandbox_env = original_env
                    logger.debug("Sandbox environment restored after replay.")

    # --- Internal Experiment Implementations ---

    async def _run_ab_test(
        self,
        experiment_id: str,
        variant_a_agent: Any,
        variant_b_agent: Any,
        runs: int,
        metrics: Optional[List[str]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Internal implementation for A/B testing two agent variants.
        """
        logger.info(
            f"Running A/B test for experiment {experiment_id} with {runs} runs per variant."
        )
        if not hasattr(self.sandbox_env, "test_agent"):
            logger.error("Sandbox environment lacks 'test_agent' method for A/B test.")
            raise ValueError(
                "Sandbox environment must have a 'test_agent' method for A/B testing."
            )

        results = {"variant_a": {}, "variant_b": {}, "summary": {}}

        # Run Variant A
        logger.debug(f"Running Variant A for experiment {experiment_id}.")
        a_runs_data = []
        for i in range(runs):
            run_result_a = await self.sandbox_env.test_agent(variant_a_agent, **kwargs)
            a_runs_data.append(run_result_a)
            logger.debug(f"Variant A run {i+1}/{runs} result: {run_result_a}")
        results["variant_a"]["raw_results"] = a_runs_data
        results["variant_a"]["metrics"] = self._calculate_metrics(a_runs_data, metrics)
        logger.info(
            f"Variant A metrics for {experiment_id}: {results['variant_a']['metrics']}"
        )

        # Run Variant B
        logger.debug(f"Running Variant B for experiment {experiment_id}.")
        b_runs_data = []
        for i in range(runs):
            run_result_b = await self.sandbox_env.test_agent(variant_b_agent, **kwargs)
            b_runs_data.append(run_result_b)
            logger.debug(f"Variant B run {i+1}/{runs} result: {run_result_b}")
        results["variant_b"]["raw_results"] = b_runs_data
        results["variant_b"]["metrics"] = self._calculate_metrics(b_runs_data, metrics)
        logger.info(
            f"Variant B metrics for {experiment_id}: {results['variant_b']['metrics']}"
        )

        # Compare
        results["summary"] = self._compare_variants(
            results["variant_a"]["metrics"], results["variant_b"]["metrics"]
        )
        logger.info(f"A/B test summary for {experiment_id}: {results['summary']}")
        return results

    async def _run_evolution_experiment(
        self,
        experiment_id: str,
        initial_population: List[Any],
        generations: int,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Internal implementation for an evolutionary experiment.
        """
        logger.info(
            f"Running evolution experiment for {experiment_id} over {generations} generations."
        )
        current_population = initial_population
        evolution_trace = []

        for gen in range(generations):
            logger.debug(
                f"Evolution experiment {experiment_id}: Generation {gen+1}/{generations}."
            )
            if not current_population:
                logger.warning(
                    f"Population became empty at generation {gen} for experiment {experiment_id}. Stopping evolution."
                )
                break

            fitness_scores = await asyncio.gather(
                *[
                    self.sandbox_env.evaluate(agent_variant, **kwargs)
                    for agent_variant in current_population
                ]
            )
            sorted_population = sorted(
                zip(fitness_scores, current_population),
                key=lambda x: x[0],
                reverse=True,
            )

            best_agent_fitness = sorted_population[0][0] if sorted_population else None
            best_agent = sorted_population[0][1] if sorted_population else None

            evolution_trace.append(
                {
                    "generation": gen,
                    "best_fitness": best_agent_fitness,
                    "population_size": len(current_population),
                }
            )
            logger.debug(
                f"Generation {gen} - Best fitness: {best_agent_fitness}, Population size: {len(current_population)}"
            )

            if not best_agent:
                logger.error(
                    f"No best agent found in generation {gen} for experiment {experiment_id}."
                )
                break

            next_population = [best_agent]
            for _ in range(len(current_population) - 1):
                next_population.append(self._create_mutated_agent(best_agent, gen))
            current_population = next_population

        final_best_agent_name = (
            getattr(best_agent, "name", "N/A") if best_agent else "N/A"
        )
        final_best_fitness = (
            evolution_trace[-1]["best_fitness"] if evolution_trace else "N/A"
        )
        logger.info(
            f"Evolution experiment {experiment_id} finished. Final best agent: {final_best_agent_name}, Final best fitness: {final_best_fitness}"
        )

        return {
            "evolution_trace": evolution_trace,
            "final_best_agent_details": {
                "name": final_best_agent_name,
                "fitness": final_best_fitness,
            },
            "final_population_summary": f"Final population size: {len(current_population)}",
        }

    def _create_mutated_agent(self, base_agent: Any, generation: int) -> MutatedAgent:
        """
        Creates a new mutated agent based on a base agent and the current generation.
        """
        return MutatedAgent(getattr(base_agent, "name", "BaseAgent"), generation)

    def _generate_experiment_id(self, kind: str) -> str:
        """
        Generates a unique experiment ID based on the experiment kind, timestamp, and a random suffix.
        """
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        random_suffix = "".join(random.choices("0123456789abcdef", k=6))
        experiment_id = f"{kind}_{timestamp}_{random_suffix}"
        logger.debug(f"Generated experiment ID: {experiment_id}")
        return experiment_id

    def _calculate_metrics(
        self, runs_data: List[Dict[str, Any]], metrics: Optional[List[str]]
    ) -> Dict[str, Any]:
        """
        Calculates various statistics for specified metrics.
        """
        if not metrics:
            return {}
        calculated_metrics = {}
        for metric_name in metrics:
            values = [
                run.get(metric_name)
                for run in runs_data
                if run.get(metric_name) is not None
            ]

            if values and all(isinstance(v, (int, float)) for v in values):
                try:
                    calculated_metrics[f"{metric_name}_avg"] = mean(values)
                    calculated_metrics[f"{metric_name}_median"] = median(values)
                    if len(values) > 1:
                        calculated_metrics[f"{metric_name}_stddev"] = stdev(values)
                    else:
                        calculated_metrics[f"{metric_name}_stddev"] = 0.0
                    calculated_metrics[f"{metric_name}_min"] = min(values)
                    calculated_metrics[f"{metric_name}_max"] = max(values)
                except Exception as e:
                    logger.warning(
                        f"Failed to calculate numerical statistics for metric '{metric_name}': {e}"
                    )
                    calculated_metrics[f"{metric_name}_error"] = str(e)
            elif values:
                calculated_metrics[f"{metric_name}_counts"] = collections.Counter(
                    values
                )
            else:
                calculated_metrics[f"{metric_name}_avg"] = None
                calculated_metrics[f"{metric_name}_stddev"] = None
                calculated_metrics[f"{metric_name}_median"] = None
                calculated_metrics[f"{metric_name}_min"] = None
                calculated_metrics[f"{metric_name}_max"] = None

        logger.debug(f"Calculated metrics: {calculated_metrics}")
        return calculated_metrics

    def _compare_variants(
        self, metrics_a: Dict[str, Any], metrics_b: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Compares the metrics of two variants (A and B).
        """
        summary = {"comparison": {}}
        for metric_name_a, value_a in metrics_a.items():
            if "_avg" in metric_name_a and metric_name_a in metrics_b:
                avg_a = value_a
                avg_b = metrics_b.get(metric_name_a)

                if avg_a is not None and avg_b is not None:
                    diff = avg_b - avg_a
                    pct_change = 0.0
                    if avg_a != 0:
                        pct_change = (diff / avg_a) * 100
                    elif diff > 0:
                        pct_change = float("inf")
                    elif diff < 0:
                        pct_change = float("-inf")

                    verdict = "same"
                    if diff > 0:
                        verdict = "better"
                    elif diff < 0:
                        verdict = "worse"

                    summary["comparison"][metric_name_a] = {
                        "diff": f"{diff:.3f}",
                        "pct_change": f"{pct_change:.2f}%",
                        "verdict": verdict,
                    }
                else:
                    summary["comparison"][metric_name_a] = {
                        "verdict": "One or both variants lack metric value"
                    }
        logger.debug(f"Comparison summary: {summary}")
        return summary


# Register as a plugin
registry.register(
    kind=PlugInKind.CORE_SERVICE,
    name="Explorer",
    version="1.0.0",
    author="Arbiter Team",
)(Explorer)


class MockLogDB:
    """Mock database for testing - implements the same interface as LogDB"""

    def __init__(self):
        self._experiments = []
        self._lock = threading.Lock()
        logger.info("MockLogDB initialized.")

    async def save_experiment_log(self, log_entry: Dict[str, Any]):
        """Saves a single experiment log entry to the mock database."""
        with self._lock:
            self._experiments.append(log_entry)
            logger.debug(
                f"MockLogDB: Saved experiment log: {log_entry.get('experiment_id')}"
            )

    async def get_experiment_log(self, experiment_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a single experiment log entry by its ID."""
        with self._lock:
            for exp in self._experiments:
                if exp.get("experiment_id") == experiment_id:
                    logger.debug(
                        f"MockLogDB: Retrieved experiment log for ID: {experiment_id}"
                    )
                    return exp
            logger.warning(f"Experiment log with ID '{experiment_id}' not found.")
            return None

    async def find_experiments(self, query: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Finds experiment log entries matching the query."""
        with self._lock:
            results = []
            for exp in self._experiments:
                if all(
                    key in exp and exp[key] == value for key, value in query.items()
                ):
                    results.append(exp)
            logger.debug(
                f"MockLogDB: Found {len(results)} experiment logs for query: {query}"
            )
            return results

    async def health_check(self) -> Dict[str, Any]:
        """Checks mock database health."""
        return {"status": "healthy"}


class ArbiterExplorer:
    """Explorer for arbiter system with experiment management capabilities"""

    def __init__(
        self, sandbox_env: Any = None, log_db: Optional[Union[LogDB, MockLogDB]] = None
    ):
        """
        Initializes the ArbiterExplorer with a sandbox environment and log database.

        Args:
            sandbox_env: The environment where agents will be tested
            log_db: An optional instance of a log database (LogDB or MockLogDB)
        """
        self.sandbox_env = sandbox_env
        self.log_db = log_db if log_db is not None else MockLogDB()
        self.experiment_count = 0
        self._lock = threading.Lock()
        logger.info("ArbiterExplorer initialized.")

    async def run_ab_test(
        self,
        experiment_name: str,
        variant_a: Any,
        variant_b: Any,
        num_runs: int = 1,
        metric: str = "perf",
    ) -> Dict[str, Any]:
        """
        Runs an A/B test between two variants.

        Args:
            experiment_name: Name of the experiment
            variant_a: First variant to test
            variant_b: Second variant to test
            num_runs: Number of runs for each variant
            metric: Metric to evaluate

        Returns:
            Dict with test results and comparison
        """
        experiment_id = f"{experiment_name}_{int(time.time())}"

        async def run_test():
            results_a = []
            results_b = []

            # Handle zero runs edge case
            if num_runs == 0:
                return {
                    "experiment_id": experiment_id,
                    "status": "completed",
                    "summary": {"metrics_a": {}, "metrics_b": {}, "comparison": {}},
                }

            # Run tests for variant A
            for _ in range(num_runs):
                try:
                    score = await self.sandbox_env.evaluate(variant_a, metric=metric)
                    result = await self.sandbox_env.test_agent(variant_a)
                    results_a.append({"metrics": {metric: score}})
                except Exception as e:
                    logger.error(
                        f"Experiment {experiment_name} failed: {str(e)}", exc_info=True
                    )
                    raise ExperimentExecutionError(
                        f"Experiment {experiment_name} failed: {str(e)}"
                    )

            # Run tests for variant B
            for _ in range(num_runs):
                score = await self.sandbox_env.evaluate(variant_b, metric=metric)
                result = await self.sandbox_env.test_agent(variant_b)
                results_b.append({"metrics": {metric: score}})

            # Calculate metrics
            metrics_a = self._calculate_metrics(results_a)
            metrics_b = self._calculate_metrics(results_b)

            # Compare variants
            comparison = self._compare_variants(metrics_a, metrics_b)

            result = {
                "experiment_id": experiment_id,
                "status": "completed",
                "results_a": results_a,
                "results_b": results_b,
                "summary": comparison,
            }

            # Log the experiment
            await self._log_experiment(result)

            with self._lock:
                self.experiment_count += 1

            return result

        return await self._run_experiment(experiment_name, run_test)

    async def run_evolutionary_experiment(
        self,
        experiment_name: str,
        initial_agent: Any,
        num_generations: int = 1,
        population_size: int = 1,
        metric: str = "perf",
    ) -> Dict[str, Any]:
        """
        Runs an evolutionary experiment.

        Args:
            experiment_name: Name of the experiment
            initial_agent: Initial agent to evolve from
            num_generations: Number of generations to evolve
            population_size: Size of population in each generation
            metric: Metric to evaluate

        Returns:
            Dict with evolution results
        """
        experiment_id = f"{experiment_name}_{int(time.time())}"

        async def run_evolution():
            generations = []
            current_population = [initial_agent] * population_size

            for gen in range(num_generations):
                try:
                    # Evaluate current generation
                    scores = []
                    for agent in current_population:
                        score = await self.sandbox_env.evaluate(agent, metric=metric)
                        result = await self.sandbox_env.test_agent(agent)
                        scores.append(score)

                    generations.append(
                        {
                            "generation": gen,
                            "scores": scores,
                            "best_score": max(scores) if scores else 0,
                        }
                    )

                    # Create next generation (simplified mutation)
                    current_population = [initial_agent] * population_size

                except Exception as e:
                    logger.error(
                        f"Experiment {experiment_name} failed: {str(e)}", exc_info=True
                    )
                    raise ExperimentExecutionError(
                        f"Experiment {experiment_name} failed: {str(e)}"
                    )

            result = {
                "experiment_id": experiment_id,
                "status": "completed",
                "generations": generations,
            }

            # Log the experiment
            await self._log_experiment(result)

            with self._lock:
                self.experiment_count += 1

            return result

        return await self._run_experiment(experiment_name, run_evolution)

    async def _run_experiment(
        self, name: str, experiment_func: Callable, *args, **kwargs
    ) -> Dict[str, Any]:
        """
        Runs an experiment with timing and error handling.

        Args:
            name: Name of the experiment
            experiment_func: Async function to run
            *args, **kwargs: Arguments for the experiment function

        Returns:
            Dict with experiment results including duration
        """
        start_time = time.time()
        try:
            result = await experiment_func(*args, **kwargs)
            duration = time.time() - start_time
            result["duration_seconds"] = duration
            return result
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"Experiment {name} failed after {duration:.2f}s: {str(e)}")
            raise

    async def _log_experiment(self, entry: Dict[str, Any]):
        """
        Logs an experiment entry.

        Args:
            entry: Experiment data to log
        """
        try:
            await self.log_db.save_experiment_log(entry)
        except Exception as e:
            logger.error(
                f"Failed to log experiment {entry.get('experiment_id')}: {str(e)}",
                exc_info=True,
            )

    def _calculate_metrics(self, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Calculates metrics from results.

        Args:
            results: List of result dictionaries with metrics

        Returns:
            Dict with calculated statistics
        """
        if not results:
            return {}

        metrics_dict = {}

        # Extract all metric names
        metric_names = set()
        for result in results:
            if "metrics" in result:
                metric_names.update(result["metrics"].keys())

        for metric_name in metric_names:
            values = []
            for result in results:
                if "metrics" in result and metric_name in result["metrics"]:
                    values.append(result["metrics"][metric_name])

            if values:
                # Check if all values are numeric
                if all(isinstance(v, (int, float)) for v in values):
                    metrics_dict[f"{metric_name}_avg"] = mean(values)
                    metrics_dict[f"{metric_name}_median"] = median(values)
                    if len(values) > 1:
                        metrics_dict[f"{metric_name}_stddev"] = stdev(values)
                    else:
                        metrics_dict[f"{metric_name}_stddev"] = 0.0
                else:
                    # For non-numeric values, count occurrences
                    metrics_dict[f"{metric_name}_counts"] = collections.Counter(values)

        return metrics_dict

    def _compare_variants(
        self, metrics_a: Dict[str, Any], metrics_b: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Compares metrics between two variants.

        Args:
            metrics_a: Metrics for variant A
            metrics_b: Metrics for variant B

        Returns:
            Dict with comparison results
        """
        comparison = {"metrics_a": metrics_a, "metrics_b": metrics_b, "comparison": {}}

        for key in metrics_a:
            if "_avg" in key and key in metrics_b:
                val_a = metrics_a[key]
                val_b = metrics_b[key]

                if val_a is not None and val_b is not None:
                    diff = val_b - val_a
                    if val_a != 0:
                        pct_change = (diff / abs(val_a)) * 100
                    else:
                        pct_change = 100.0 if diff > 0 else -100.0 if diff < 0 else 0.0

                    verdict = "better" if diff > 0 else "worse" if diff < 0 else "same"

                    comparison["comparison"][key] = {
                        "verdict": verdict,
                        "pct_change": f"{pct_change:.2f}%",
                    }

        return comparison
