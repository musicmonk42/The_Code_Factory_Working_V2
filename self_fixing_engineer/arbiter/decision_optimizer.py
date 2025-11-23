# decision_optimizer.py
from __future__ import (
    annotations,
)  # Enable postponed evaluation of annotations for forward references

import asyncio
import logging
import time
import json
import uuid
import hashlib
import traceback
from typing import List, Dict, Any, Optional, Callable, Set, Tuple
from dataclasses import dataclass, field

# from threading import Lock # REMOVED: Replaced with asyncio.Lock
from collections import Counter
import networkx as nx
from cryptography.fernet import Fernet, InvalidToken
import redis.asyncio as redis
from circuitbreaker import circuit
from fastapi import WebSocket, WebSocketDisconnect
from functools import wraps
from datetime import datetime, timezone
import numpy as np  # Using numpy for array-based prioritization
from collections.abc import Mapping, Sequence

# SFE Core AI System Imports
from arbiter.arbiter import Arbiter
from arbiter.arena import ArbiterArena
from arbiter.feedback import FeedbackManager

# from arbiter.knowledge_graph import KnowledgeGraph
from arbiter.explainable_reasoner import ExplainableReasoner
from arbiter.policy import PolicyEngine
from arbiter.bug_manager import BugManager
from arbiter.monitoring import Monitor
from arbiter.human_loop import HumanInLoop
from arbiter.config import ArbiterConfig
from arbiter.utils import get_system_metrics_async

# SFE Engine Shared Components
from arbiter.arbiter_plugin_registry import PLUGIN_REGISTRY
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from arbiter.arbiter_array_backend import ConcreteArrayBackend as ArrayBackend
from arbiter.metrics import (
    get_or_create_counter,
    get_or_create_histogram,
    get_or_create_gauge,
)
from simulation.simulation_module import Database


class SFECoreEngine:
    """Represents the central core engine of the Self-Fixing Engineer (SFE) system."""

    database: Database
    feedback_manager: FeedbackManager
    knowledge_graph: "KnowledgeGraph"
    explainable_reasoner: ExplainableReasoner
    policy_engine: PolicyEngine
    bug_manager: BugManager
    monitor: Monitor
    human_in_loop: HumanInLoop
    plugin_registry: PLUGIN_REGISTRY
    notification_service: Any
    audit: Any


class MetaLearningService:
    """
    A conceptual service interface for retrieving updated models, policies,
    and configurations from the meta-learning pipeline.
    """

    def __init__(self, logger):
        self.logger = logger

    async def get_latest_prioritization_weights(self) -> Optional[Dict[str, float]]:
        self.logger.debug("Fetching latest prioritization weights from meta-learning service.")
        await asyncio.sleep(0.1)
        return None

    async def get_latest_policy_rules(self) -> Optional[Dict[str, Any]]:
        self.logger.debug("Fetching latest policy rules from meta-learning service.")
        await asyncio.sleep(0.1)
        return None

    async def get_latest_plugin_version(self, kind: str, name: str) -> Optional[str]:
        self.logger.debug(
            f"Fetching latest version for plugin {kind}:{name} from meta-learning service."
        )
        await asyncio.sleep(0.05)
        return None

    async def get_plugin_code(self, kind: str, name: str, version: str) -> Optional[Callable]:
        self.logger.debug(
            f"Fetching code for plugin {kind}:{name} version {version} from meta-learning service."
        )
        await asyncio.sleep(0.2)
        return None


logger = logging.getLogger("DecisionOptimizer")
logger.setLevel(logging.INFO)
if not logger.handlers:
    import sys

    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)

# Prometheus Metrics
TASK_PRIORITIZATION_COUNT = get_or_create_counter(
    "sfe_decision_optimizer_prioritization_total", "Total task prioritizations for SFE"
)
ALLOCATION_LATENCY = get_or_create_histogram(
    "sfe_decision_optimizer_allocation_latency_seconds", "SFE task allocation latency"
)
COORDINATION_SUCCESS = get_or_create_counter(
    "sfe_decision_optimizer_coordination_success_total",
    "Successful SFE coordination actions",
)
AGENT_ACTIVE_GAUGE = get_or_create_gauge(
    "sfe_decision_optimizer_active_agents", "Number of active SFE agents"
)
EXPLANATION_EVENTS = get_or_create_counter(
    "sfe_decision_optimizer_explanation_events_total", "Total SFE explanation requests"
)
ERRORS_CRITICAL = get_or_create_counter(
    "sfe_decision_optimizer_critical_errors_total",
    "Critical errors caught in SFE DecisionOptimizer",
)
PLUGIN_EXECUTION_LATENCY = get_or_create_histogram(
    "sfe_decision_optimizer_plugin_execution_latency_seconds",
    "Latency of SFE plugin executions",
    ("plugin_name",),
)
DB_OPERATION_LATENCY = get_or_create_histogram(
    "sfe_decision_optimizer_db_operation_latency_seconds",
    "Latency of SFE database operations",
    ("operation_type",),
)
STRATEGY_REFRESH_COUNT = get_or_create_counter(
    "sfe_decision_optimizer_strategy_refresh_total", "Total strategy refresh attempts"
)
STRATEGY_REFRESH_SUCCESS = get_or_create_counter(
    "sfe_decision_optimizer_strategy_refresh_success_total",
    "Successful strategy refreshes",
)


@dataclass
class Task:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    priority: float = 1.0
    deadline: Optional[float] = None
    dependencies: Set[str] = field(default_factory=set)
    required_skills: Set[str] = field(default_factory=set)
    estimated_compute: float = 1.0
    risk_level: str = "medium"
    action_type: str = "unknown"
    metadata: Dict[str, Any] = field(default_factory=dict)
    sim_request: Optional[Any] = None

    def __post_init__(self):
        self.dependencies = self.dependencies or set()
        self.required_skills = self.required_skills or set()
        self.metadata = self.metadata or {}
        # Ensure ID and action_type are sanitized and of the correct type
        self.id = str(self.id).replace("<", "").replace(">", "")
        self.action_type = str(self.action_type).lower()


@dataclass
class Agent:
    id: str
    skills: Set[str]
    max_compute: float
    current_load: float = 0.0
    energy: float = 100.0
    role: str = "user"
    metadata: Dict[str, Any] = field(default_factory=dict)
    arbiter_instance: Optional[Arbiter] = None

    def __post_init__(self):
        self.metadata = self.metadata or {}
        self.id = str(self.id).replace("<", "").replace(">", "")
        self.role = str(self.role).lower()


def safe_serialize(obj: Any) -> Any:
    """
    Safely serializes an object into a JSON-friendly format.
    Handles dataclasses, sets, and other non-standard types.
    """
    if isinstance(obj, (datetime, datetime)):
        return obj.isoformat()
    if isinstance(obj, (Set, frozenset)):
        return list(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, (Mapping, Sequence)) and not isinstance(obj, str):
        if hasattr(obj, "items"):  # Dictionary-like
            return {k: safe_serialize(v) for k, v in obj.items()}
        else:  # List-like
            return [safe_serialize(item) for item in obj]
    if hasattr(obj, "__dict__"):
        return safe_serialize(obj.__dict__)
    return obj


class DecisionOptimizer:
    """
    The DecisionOptimizer orchestrates tasks and agents within the Self-Fixing Engineer (SFE) platform.
    It handles task prioritization, resource allocation, and agent coordination using advanced strategies.
    This component is part of the Arbiter AI system, which is integral to the SFE.
    This class is thread-safe for its internal state.
    """

    def __init__(
        self,
        plugin_registry: PLUGIN_REGISTRY,
        settings: ArbiterConfig,
        logger: logging.Logger,
        arbiter: Optional[Arbiter] = None,
        arena: Optional[ArbiterArena] = None,
        sfe_core_engine: Optional[SFECoreEngine] = None,
        prioritizer: Optional[Callable] = None,
        allocator: Optional[Callable] = None,
        coordinator: Optional[Callable] = None,
        meta_learning_service: Optional[MetaLearningService] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        self.plugin_registry = plugin_registry
        self.settings = settings
        self.logger = logger
        self.arbiter = arbiter
        self.arena = arena if arena else (arbiter.arena if arbiter else None)
        self.sfe_core_engine = sfe_core_engine
        self.meta_learning_service = meta_learning_service or MetaLearningService(self.logger)

        self.prioritizer = prioritizer or self._default_prioritize
        self.allocator = allocator or self._default_allocate
        self.coordinator = coordinator or self._default_coordinate

        self.config = {
            "default_weights": (
                config.get(
                    "default_weights",
                    {"priority": 0.5, "deadline": 0.3, "risk": 0.15, "context": 0.05},
                )
                if config
                else {"priority": 0.5, "deadline": 0.3, "risk": 0.15, "context": 0.05}
            ),
            "max_tasks_per_agent": (config.get("max_tasks_per_agent", 10) if config else 10),
            "encryption_key": (
                config.get(
                    "encryption_key",
                    (
                        settings.ENCRYPTION_KEY.get_secret_value()
                        if hasattr(settings, "ENCRYPTION_KEY") and settings.ENCRYPTION_KEY
                        else None
                    ),
                )
                if config
                else (
                    settings.ENCRYPTION_KEY.get_secret_value()
                    if hasattr(settings, "ENCRYPTION_KEY") and settings.ENCRYPTION_KEY
                    else None
                )
            ),
            "redis_url": (
                config.get("redis_url", settings.REDIS_URL or "redis://localhost:6379")
                if config
                else (settings.REDIS_URL or "redis://localhost:6379")
            ),
            "redis_pool_size": (
                config.get("redis_pool_size", getattr(settings, "REDIS_POOL_SIZE", 100))
                if config
                else getattr(settings, "REDIS_POOL_SIZE", 100)
            ),
            "db_pool_size": (
                config.get("db_pool_size", getattr(settings, "DB_POOL_SIZE", 50))
                if config
                else getattr(settings, "DB_POOL_SIZE", 50)
            ),
            "policy_check": (config.get("policy_check", True) if config else True),
            "human_approval_threshold": (
                config.get("human_approval_threshold", "high") if config else "high"
            ),
            "batch_size": (
                config.get("batch_size", getattr(settings, "DB_BATCH_SIZE", 100))
                if config
                else getattr(settings, "DB_BATCH_SIZE", 100)
            ),
            "strategy_refresh_interval_seconds": (
                config.get("strategy_refresh_interval_seconds", 300) if config else 300
            ),
        }
        self.lock = asyncio.Lock()
        self.task_graph = nx.DiGraph()

        if self.config["encryption_key"]:
            try:
                self.encrypter = Fernet(self.config["encryption_key"])
            except Exception as e:
                self.logger.error(
                    f"Failed to initialize Fernet encrypter for SFE: {e}. Encryption features will be disabled.",
                    exc_info=True,
                )
                self.encrypter = None
        else:
            self.logger.warning(
                "Encryption key not provided in SFE config or settings. Fernet will not be initialized."
            )
            self.encrypter = None

        self.event_log: List[Dict[str, Any]] = []

        self.db: Optional[Database] = (
            sfe_core_engine.database
            if sfe_core_engine and hasattr(sfe_core_engine, "database")
            else None
        )
        self.feedback_manager: Optional[FeedbackManager] = (
            sfe_core_engine.feedback_manager
            if sfe_core_engine and hasattr(sfe_core_engine, "feedback_manager")
            else None
        )
        self.knowledge_graph: Optional["KnowledgeGraph"] = (
            sfe_core_engine.knowledge_graph
            if sfe_core_engine and hasattr(sfe_core_engine, "knowledge_graph")
            else None
        )
        self.explainable_reasoner: Optional[ExplainableReasoner] = (
            sfe_core_engine.explainable_reasoner
            if sfe_core_engine and hasattr(sfe_core_engine, "explainable_reasoner")
            else None
        )
        self.policy_engine: Optional[PolicyEngine] = (
            sfe_core_engine.policy_engine
            if sfe_core_engine and hasattr(sfe_core_engine, "policy_engine")
            else None
        )
        self.bug_manager: Optional[BugManager] = (
            sfe_core_engine.bug_manager
            if sfe_core_engine and hasattr(sfe_core_engine, "bug_manager")
            else None
        )
        self.monitor: Optional[Monitor] = (
            sfe_core_engine.monitor
            if sfe_core_engine and hasattr(sfe_core_engine, "monitor")
            else None
        )
        self.human_in_loop: Optional[HumanInLoop] = (
            sfe_core_engine.human_in_loop
            if sfe_core_engine and hasattr(sfe_core_engine, "human_in_loop")
            else None
        )

        self.array_backend = ArrayBackend(
            name="decision_optimizer_array",
            storage_path="decision_optimizer_arrays.json",
        )
        self.redis_client: Optional[redis.Redis] = None
        self.db_connection = None

        config_for_log = dict(self.config)
        if "encryption_key" in config_for_log and config_for_log["encryption_key"]:
            config_for_log["encryption_key"] = "<REDACTED>"
        self.logger.info(
            "SFE DecisionOptimizer initialized as part of Arbiter AI system. Config: %s",
            json.dumps(config_for_log, indent=2),
        )

        if not self.feedback_manager:
            self.logger.warning(
                "SFE FeedbackManager not provided. Some SFE metrics and error reporting will be degraded."
            )
        if not self.knowledge_graph:
            self.logger.warning(
                "SFE KnowledgeGraph not provided. SFE graph-based reasoning and auditing will be degraded."
            )
        if not self.explainable_reasoner:
            self.logger.warning(
                "SFE ExplainableReasoner not provided. SFE explanations will not be generated."
            )
        if not self.policy_engine:
            self.logger.warning(
                "SFE PolicyEngine not provided. SFE policy checks will be bypassed."
            )
        if not self.bug_manager:
            self.logger.warning(
                "SFE BugManager not provided. SFE critical error reporting will be degraded."
            )

        self._refresh_task: Optional[asyncio.Task] = None

    async def __aenter__(self):
        try:
            self.redis_client = redis.from_url(
                self.config["redis_url"],
                max_connections=self.config["redis_pool_size"],
                decode_responses=True,
            )
            await self.redis_client.ping()
            self.logger.info("Connected to Redis successfully for SFE operations.")
        except Exception as e:
            self.logger.critical(f"Failed to connect to Redis for SFE: {e}", exc_info=True)
            raise ConnectionError(f"Failed to connect to Redis for SFE: {e}") from e

        if not self.db and hasattr(self.settings, "DATABASE_URL"):
            try:
                self.db = Database(self.settings.DATABASE_URL)
                if hasattr(self.db, "connect"):
                    await self.db.connect()
                self.logger.info("Standalone SFE Database connection established.")
            except Exception as e:
                self.logger.critical(
                    f"Failed to establish standalone SFE database connection: {e}",
                    exc_info=True,
                )
                raise ConnectionError(f"Failed to connect to SFE Database: {e}") from e
        elif not self.db and not hasattr(self.settings, "DATABASE_URL"):
            self.logger.warning(
                "No database URL provided and no SFECoreEngine.database instance. DB operations will be skipped."
            )

        await self.refresh_strategies()
        self._refresh_task = asyncio.create_task(self._periodic_strategy_refresh())

        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._refresh_task:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                self.logger.info("Periodic strategy refresh task cancelled.")

        if self.redis_client:
            try:
                await self.redis_client.close()
            except Exception as e:
                self.logger.error(f"Error closing Redis client for SFE: {e}", exc_info=True)
            self.logger.info("Redis client closed for SFE.")

        if self.db and hasattr(self.db, "disconnect"):
            await self.db.disconnect()
            self.logger.info("SFE Database connection closed.")
        self.logger.info("SFE DecisionOptimizer connections closed.")

    async def _periodic_strategy_refresh(self):
        while True:
            try:
                interval = self.config["strategy_refresh_interval_seconds"]
                await asyncio.sleep(interval)
                self.logger.info(
                    f"Initiating periodic strategy refresh for SFE DecisionOptimizer. Next refresh in {interval} seconds."
                )
                await self.refresh_strategies()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(
                    f"Error during periodic strategy refresh for SFE: {e}",
                    exc_info=True,
                )
                await asyncio.sleep(60)

    async def refresh_strategies(self):
        STRATEGY_REFRESH_COUNT.inc()
        self.logger.info("Attempting to refresh SFE strategies from meta-learning pipeline.")
        try:
            new_weights = await self.meta_learning_service.get_latest_prioritization_weights()
            if new_weights:
                async with self.lock:
                    old_weights = self.config["default_weights"]
                    self.config["default_weights"] = new_weights
                self.logger.info(
                    f"SFE Prioritization weights updated: {old_weights} -> {new_weights}"
                )
                await self._log_event(
                    "sfe_prioritization_weights_updated",
                    {"old_weights": old_weights, "new_weights": new_weights},
                )

            if self.policy_engine:
                new_policy_rules = await self.meta_learning_service.get_latest_policy_rules()
                if new_policy_rules:
                    if hasattr(self.policy_engine, "update_rules"):
                        async with self.lock:
                            await self.policy_engine.update_rules(new_policy_rules)
                        self.logger.info("SFE Policy rules updated from meta-learning.")
                        await self._log_event(
                            "sfe_policy_rules_updated", {"new_rules": new_policy_rules}
                        )
                    else:
                        self.logger.warning(
                            "SFE PolicyEngine does not support dynamic rule updates. Skipping."
                        )

            new_prioritizer_version = await self.meta_learning_service.get_latest_plugin_version(
                "PRIORITIZER", "advanced_prioritizer"
            )
            if new_prioritizer_version:
                prioritizer_plugin = self.plugin_registry.get("PRIORITIZER", "advanced_prioritizer")
                if (
                    prioritizer_plugin
                    and getattr(prioritizer_plugin, "_version", "0.0") != new_prioritizer_version
                ):
                    self.logger.info(
                        f"New version of 'advanced_prioritizer' plugin available: {new_prioritizer_version}. Attempting to load."
                    )
                    self.logger.warning(
                        "Could not load new version of '%s' SFE plugin. Sticking to current.",
                        "advanced_prioritizer",
                    )
                elif not prioritizer_plugin:
                    self.logger.warning(
                        "No 'advanced_prioritizer' SFE plugin registered to update."
                    )

            STRATEGY_REFRESH_SUCCESS.inc()
            self.logger.info("SFE Strategy refresh completed successfully.")
        except Exception as e:
            self.logger.error(f"Failed to refresh SFE strategies: {e}", exc_info=True)
            await self._log_event("sfe_strategy_refresh_failed", {"error": str(e)})

    async def prioritize_and_allocate(
        self,
        agents: List["Agent"],
        tasks: List["Task"],
    ) -> Tuple[Dict[str, List[str]], List[str]]:
        """
        Assigns tasks to agents using skill, load, and availability constraints.
        Returns a dict: {agent_id: [task_id, ...]}, and a list of unassigned task IDs.
        """
        assignments: Dict[str, List[str]] = {a.id: [] for a in agents}
        unassigned: List[str] = []
        available_agents = {a.id: a for a in agents}

        sorted_tasks = sorted(
            tasks,
            key=lambda t: (
                -getattr(t, "priority", 0),
                getattr(t, "estimated_compute", 1.0),
            ),
        )

        for task in sorted_tasks:
            eligible = [
                agent
                for agent in available_agents.values()
                if task.required_skills.issubset(getattr(agent, "skills", set()))
                and (getattr(agent, "current_load", 0.0) + getattr(task, "estimated_compute", 1.0))
                <= getattr(agent, "max_compute", float("inf"))
            ]
            if eligible:
                agent = min(eligible, key=lambda a: getattr(a, "current_load", 0.0))
                assignments[agent.id].append(task.id)
                agent.current_load += getattr(task, "estimated_compute", 1.0)
            else:
                unassigned.append(task.id)
        assignments = {aid: tids for aid, tids in assignments.items() if tids}
        return assignments, unassigned

    def shutdown(self):
        """
        Best-practice: cleanly shut down async resources.
        """
        if hasattr(self, "array_backend") and self.array_backend:
            stop = getattr(self.array_backend, "stop", None)
            if callable(stop):
                import asyncio

                try:
                    loop = asyncio.get_running_loop()
                    if loop.is_running():
                        if asyncio.iscoroutinefunction(stop):
                            loop.create_task(stop())
                        else:
                            stop()
                    else:
                        if asyncio.iscoroutinefunction(stop):
                            loop.run_until_complete(stop())
                        else:
                            stop()
                except (RuntimeError, Exception):
                    # Ignore errors on shutdown, e.g., if loop is closed
                    pass

    async def process_remediation_proposal(self, proposal: Dict[str, Any]):
        await self._log_event("remediation_proposal_received", proposal)

        if not self.policy_engine:
            self.logger.warning(
                "SFE PolicyEngine not available, bypassing policy check for remediation."
            )
        else:
            is_allowed, reason = await self.policy_engine.should_auto_learn(
                domain=proposal.get("type", "unknown"),
                key=proposal.get("details", {}).get("cycle", "unknown"),
                user_id="sfe_system",
                value=proposal,
            )
            if not is_allowed:
                await self._log_event(
                    "remediation_denied_by_policy",
                    {"proposal": proposal, "reason": reason},
                )
                return

        if proposal.get("risk_level") == "low":
            await self._log_event("remediation_auto_applying", {"proposal": proposal})
            await self._execute_fix(proposal)
        else:
            await self._log_event("remediation_escalating_to_human", {"proposal": proposal})

            if not self.human_in_loop:
                self.logger.error(
                    "HumanInLoop component not available. Cannot escalate high-risk remediation. Denying action."
                )
                await self._log_event(
                    "remediation_escalation_failed",
                    {"proposal": proposal, "reason": "HumanInLoop not configured."},
                )
                return

            human_request = {
                "decision_id": f"fix-{proposal['type']}-{uuid.uuid4().hex[:8]}",
                "action": "apply_remediation",
                "details": proposal,
                "risk_level": proposal.get("risk_level", "high"),
            }

            approval_response = await self.human_in_loop.request_approval(human_request)

            if approval_response.get("approved"):
                await self._log_event(
                    "remediation_human_approved",
                    {"proposal": proposal, "response": approval_response},
                )
                await self._execute_fix(proposal)
            else:
                await self._log_event(
                    "remediation_human_denied",
                    {"proposal": proposal, "response": approval_response},
                )

    async def _execute_fix(self, proposal: Dict[str, Any]):
        fixer_name = proposal.get("suggested_fixer")

        result = None
        if fixer_name == "self_healing_import_fixer":
            self.logger.info(
                f"Simulating execution of fixer '{fixer_name}' for proposal type '{proposal.get('type')}'."
            )
            result = {
                "status": "success",
                "details": "Simulated fix applied successfully.",
            }
        else:
            self.logger.error(
                f"Execution failed: Unknown fixer '{fixer_name}' for proposal type '{proposal.get('type')}'."
            )
            result = {"status": "failure", "details": f"Unknown fixer '{fixer_name}'"}

        if self.sfe_core_engine and hasattr(self.sfe_core_engine, "audit"):
            await self.sfe_core_engine.audit.add_entry(
                kind="remediation_executed",
                name=proposal["type"],
                detail={"proposal": proposal, "result": result},
                agent_id="sfe_system",
            )
        else:
            await self._log_event(
                "remediation_executed",
                {
                    "proposal_type": proposal.get("type"),
                    "details": {"proposal": proposal, "result": result},
                    "agent_id": "sfe_system",
                },
            )

        self.logger.info(
            f"Completed execution of fix for proposal '{proposal.get('type')}'. Status: {result.get('status')}"
        )

    async def _log_event(self, event_type: str, details: Dict[str, Any]):
        audit_instance = (
            self.sfe_core_engine.audit
            if self.sfe_core_engine and hasattr(self.sfe_core_engine, "audit")
            else None
        )

        event_id = str(uuid.uuid4())
        timestamp_iso = datetime.now(timezone.utc).isoformat()
        event = {
            "id": event_id,
            "type": event_type,
            "timestamp": timestamp_iso,
            "details": details,
        }

        async with self.lock:
            self.event_log.append(event)

        if self.knowledge_graph:
            try:
                await self.knowledge_graph.add_fact(
                    "SFEDecisionOptimizerEvents",
                    event["id"],
                    event,
                    source="sfe_decision_optimizer",
                    timestamp=timestamp_iso,
                )
            except Exception as e:
                self.logger.error(f"Failed to log event to SFE KnowledgeGraph: {e}", exc_info=True)

        if self.monitor:
            try:
                self.monitor.log_action(
                    {
                        "type": event_type,
                        "decision_id": event["id"],
                        "description": details.get("description", ""),
                        "details": details,
                    }
                )
            except Exception as e:
                self.logger.error(f"Failed to log event to SFE Monitor: {e}", exc_info=True)

        if audit_instance and hasattr(audit_instance, "add_entry"):
            try:
                await audit_instance.add_entry(
                    kind="sfe_decision_optimizer",
                    name=event_type,
                    detail=details,
                    sim_id=details.get("sim_id"),
                    agent_id=details.get("user_id", "system"),
                )
            except Exception as e:
                self.logger.error(
                    f"Failed to add audit entry for SFE event {event_type}: {e}",
                    exc_info=True,
                )

        if self.feedback_manager:
            try:
                await self.feedback_manager.record_metric(
                    f"sfe_{event_type}_count", 1, {"event_id": event["id"]}
                )
            except Exception as e:
                self.logger.error(
                    f"Failed to record metric via SFE FeedbackManager for event {event_type}: {e}",
                    exc_info=True,
                )

        if self.redis_client:
            try:
                await self._redis_publish(
                    "sfe_decision_optimizer_events", json.dumps(safe_serialize(event))
                )
            except Exception as e:
                self.logger.error(f"Failed to publish SFE event to Redis: {e}", exc_info=True)

        self.logger.debug("SFE Event logged: %s", json.dumps(event, indent=2))

    def critical_alert_decorator(method: Callable) -> Callable:
        @wraps(method)
        async def wrapper(self: DecisionOptimizer, *args, **kwargs):
            try:
                return await method(self, *args, **kwargs)
            except Exception as e:
                error_id = str(uuid.uuid4())
                ERRORS_CRITICAL.inc()
                self.logger.exception(
                    f"Critical error in SFE DecisionOptimizer method {method.__name__} (ID: {error_id}): {e}"
                )

                error_log_data = {
                    "type": "sfe_critical_error",
                    "method": method.__name__,
                    "error": str(e),
                    "error_id": error_id,
                    "traceback": traceback.format_exc(),
                }

                if self.feedback_manager:
                    try:
                        await self.feedback_manager.log_error(error_log_data)
                    except Exception as fe:
                        self.logger.error(f"Failed to log SFE error to FeedbackManager: {fe}")

                if self.bug_manager:
                    try:
                        if hasattr(self.bug_manager, "bug_detected"):
                            await self.bug_manager.bug_detected(
                                "sfe_critical_error",
                                f"Critical error in SFE {method.__name__}: {e}",
                                error_id=error_id,
                            )
                        else:
                            self.logger.warning(
                                "BugManager instance does not have a 'bug_detected' method."
                            )
                    except Exception as be:
                        self.logger.error(f"Failed to log SFE bug to BugManager: {be}")

                if (
                    self.sfe_core_engine
                    and hasattr(self.sfe_core_engine, "notification_service")
                    and self.sfe_core_engine.notification_service
                ):
                    try:
                        if hasattr(self.sfe_core_engine.notification_service, "send_alert"):
                            self.sfe_core_engine.notification_service.send_alert(
                                f"Critical error in SFE {method.__name__}: {e}",
                                "critical",
                                time.time(),
                                {"error_id": error_id},
                            )
                        else:
                            self.logger.warning(
                                "SFECoreEngine.notification_service does not have a 'send_alert' method."
                            )
                    except Exception as ne:
                        self.logger.error(f"Failed to send SFE notification: {ne}")

                raise

        return wrapper

    async def load_strategy_plugin(self, kind: str, name: str, strategy_type: str):
        async with self.lock:
            plugin_instance = self.plugin_registry.get(kind.upper(), name)
            if not plugin_instance:
                raise ValueError(f"SFE Plugin {kind}:{name} not found in registry.")

            if not hasattr(plugin_instance, "execute") or not callable(plugin_instance.execute):
                raise TypeError(
                    f"SFE Plugin {kind}:{name} does not have a callable 'execute' method."
                )

            if strategy_type == "prioritizer":
                self.prioritizer = plugin_instance.execute
            elif strategy_type == "allocator":
                self.allocator = plugin_instance.execute
            elif strategy_type == "coordinator":
                self.coordinator = plugin_instance.execute
            else:
                raise ValueError(
                    f"Unknown strategy_type: {strategy_type}. Must be 'prioritizer', 'allocator', or 'coordinator'."
                )

            setattr(getattr(self, strategy_type), "_plugin_name", f"{kind}:{name}")

            if self.knowledge_graph:
                await self.knowledge_graph.add_fact(
                    "SFEStrategyVersions",
                    f"{strategy_type}_{name}_{time.time()}",
                    {"plugin": f"{kind}:{name}", "strategy_type": strategy_type},
                    source="sfe_decision_optimizer",
                )
            await self._log_event(
                "sfe_strategy_plugin_loaded",
                {"kind": kind, "name": name, "strategy_type": strategy_type},
            )

    async def safe_execute(self, callable: Callable, *args, **kwargs):
        if self.policy_engine:
            allowed, reason = await self.policy_engine.should_auto_learn(
                "SFEStrategyExecution",
                callable.__name__,
                "system",
                {"args": args, "kwargs": kwargs},
            )
            if not allowed:
                await self._log_event(
                    "sfe_strategy_execution_denied",
                    {
                        "strategy_name": callable.__name__,
                        "reason": reason,
                        "args": safe_serialize(args),
                        "kwargs": safe_serialize(kwargs),
                    },
                )
                raise ValueError(f"SFE Strategy execution denied by policy: {reason}")

        start_time = time.monotonic()
        try:
            if asyncio.iscoroutinefunction(callable):
                result = await callable(*args, **kwargs)
            else:
                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(
                    self.array_backend.executor, callable, *args, **kwargs
                )

            latency = time.monotonic() - start_time
            callable_name = getattr(callable, "_plugin_name", callable.__name__)
            PLUGIN_EXECUTION_LATENCY.labels(plugin_name=callable_name).observe(latency)
            return result
        except Exception as e:
            if self.bug_manager:
                callable_name = getattr(callable, "_plugin_name", callable.__name__)
                await self.bug_manager.bug_detected(
                    "sfe_strategy_execution_error",
                    f"SFE Strategy '{callable_name}' execution failed: {e}",
                    {
                        "strategy": callable_name,
                        "error": str(e),
                        "args": safe_serialize(args),
                        "kwargs": safe_serialize(kwargs),
                    },
                )
            raise

    async def anonymize_task(self, task: Task):
        if "user_id" in task.metadata:
            task.metadata["user_id"] = hashlib.sha256(task.metadata["user_id"].encode()).hexdigest()
        if self.knowledge_graph:
            await self.knowledge_graph.add_fact(
                "SFEAnonymizedTasks",
                task.id,
                task.metadata,
                source="sfe_decision_optimizer",
            )

    async def _handle_failed_task(self, task: Task, error: str):
        if self.redis_client:
            await self.redis_client.lpush(
                "sfe_dead_letter_queue",
                json.dumps(
                    {
                        "task_id": task.id,
                        "error": error,
                        "metadata": safe_serialize(task.metadata),
                    }
                ),
            )
        await self._log_event(
            "sfe_task_failed",
            {"task_id": task.id, "error": error, "sim_id": task.metadata.get("sim_id")},
        )
        if self.feedback_manager:
            await self.feedback_manager.log_error(
                {"type": "sfe_allocation_failure", "task_id": task.id, "reason": error}
            )

    @circuit(failure_threshold=5, recovery_timeout=60)
    async def _redis_publish(self, channel: str, message: str):
        if self.redis_client:
            await self.redis_client.publish(channel, message)
        else:
            self.logger.warning(
                "Redis client not initialized for SFE. Cannot publish to channel %s.",
                channel,
            )

    @critical_alert_decorator
    async def prioritize_tasks(
        self,
        agent_pool: List[Agent],
        task_queue: List[Task],
        criteria: Optional[Dict[str, Any]] = None,
    ) -> List[Task]:
        start_time = time.monotonic()

        TASK_PRIORITIZATION_COUNT.inc()

        if not task_queue:
            return []

        try:
            if not all(isinstance(t, Task) for t in task_queue):
                raise ValueError("All tasks must be SFE Task instances")
            if not all(isinstance(a, Agent) for a in agent_pool):
                raise ValueError("All agents must be SFE Agent instances")

            await asyncio.gather(*[self.anonymize_task(task) for task in task_queue])

            self.task_graph.clear()
            for task in task_queue:
                self.task_graph.add_node(task.id, task=task)
                for dep_id in task.dependencies:
                    if not self.task_graph.has_node(dep_id):
                        self.logger.warning(
                            f"SFE task dependency {dep_id} for task {task.id} not found in current task queue. Skipping edge."
                        )
                        continue
                    self.task_graph.add_edge(dep_id, task.id)

            if not nx.is_directed_acyclic_graph(self.task_graph):
                raise ValueError("SFE Task dependencies contain cycles, cannot prioritize.")

            allowed_tasks = []
            if self.config["policy_check"] and self.policy_engine:
                policy_checks = await asyncio.gather(
                    *[
                        self.policy_engine.should_auto_learn(
                            "SFETasks", task.id, "system", task.metadata
                        )
                        for task in task_queue
                    ]
                )
                for i, (allowed, reason) in enumerate(policy_checks):
                    task = task_queue[i]
                    if allowed:
                        allowed_tasks.append(task)
                    else:
                        await self._log_event(
                            "sfe_task_policy_denied",
                            {
                                "task_id": task.id,
                                "reason": reason,
                                "sim_id": task.metadata.get("sim_id"),
                            },
                        )
                        if self.feedback_manager:
                            await self.feedback_manager.log_error(
                                {
                                    "type": "sfe_policy_denial",
                                    "task_id": task.id,
                                    "reason": reason,
                                }
                            )
            else:
                allowed_tasks = task_queue[:]

            explorer_context = {}

            prioritized = []
            batch_size = self.config["batch_size"]
            for i in range(0, len(allowed_tasks), batch_size):
                batch = allowed_tasks[i : i + batch_size]
                batch_prioritized = await self.safe_execute(
                    self.prioritizer,
                    batch,
                    criteria or {"weights": self.config["default_weights"]},
                    agent_pool,
                    explorer_context,
                )
                prioritized.extend(batch_prioritized)

            if self.explainable_reasoner and criteria and criteria.get("explain", False):
                try:
                    explanation = await self.explainable_reasoner.explain(
                        "SFE Task prioritization",
                        {
                            "tasks": [t.id for t in prioritized],
                            "criteria": criteria,
                            "explorer_context": explorer_context,
                        },
                    )
                    await self._log_event(
                        "sfe_prioritization_explained",
                        {
                            "explanation": explanation,
                            "task_ids": [t.id for t in prioritized],
                        },
                    )
                    EXPLANATION_EVENTS.inc()
                except Exception as e:
                    self.logger.error(
                        f"Failed to generate explanation for SFE prioritization: {e}",
                        exc_info=True,
                    )

            if self.db:
                db_start_time = time.monotonic()
                try:
                    batch_db_data = []
                    for task in prioritized:
                        task_sim_id = (
                            task.sim_request.id
                            if task.sim_request and hasattr(task.sim_request, "id")
                            else task.id
                        )
                        batch_db_data.append(
                            {
                                "sim_id": task_sim_id,
                                "task_id": task.id,
                                "task_metadata": safe_serialize(task.metadata),
                                "result_metadata": {"priority": task.priority},
                                "status": "prioritized",
                                "user_id": "system",
                            }
                        )

                    if batch_db_data:
                        await self.db.save_simulation_batch(batch_db_data)

                    DB_OPERATION_LATENCY.labels(operation_type="sfe_save_simulation_batch").observe(
                        time.monotonic() - db_start_time
                    )
                except Exception as e:
                    self.logger.error(
                        f"Failed to save SFE simulation status for prioritized tasks: {e}",
                        exc_info=True,
                    )
                    DB_OPERATION_LATENCY.labels(
                        operation_type="sfe_save_simulation_batch_error"
                    ).observe(time.monotonic() - db_start_time)

            await self._log_event(
                "sfe_prioritize_tasks",
                {
                    "task_count": len(prioritized),
                    "criteria": criteria,
                    "latency": time.monotonic() - start_time,
                    "sim_id": (task_queue[0].metadata.get("sim_id") if task_queue else None),
                },
            )
            return prioritized

        except Exception as e:
            await self._handle_failed_task(
                task_queue[0] if task_queue else Task(id="unknown", priority=0), str(e)
            )
            raise

    async def _default_prioritize(
        self,
        task_queue: List[Task],
        criteria: Dict[str, Any],
        agent_pool: List[Agent],
        explorer_context: Dict[str, Any],
    ) -> List[Task]:
        """Default SFE task prioritization strategy. Uses weights from self.config['default_weights'] directly."""
        weights = criteria.get("weights", self.config["default_weights"])

        now = time.time()

        # SFE DREAM_MODE plugin logic (if configured)
        if criteria.get("use_dream_mode", False):
            dream_plugin = self.plugin_registry.get("CUSTOM", "dream_mode")
            if dream_plugin:
                try:
                    dream_strategy_scores = await self.safe_execute(
                        dream_plugin.execute,
                        {"tasks": [t.metadata for t in task_queue]},
                    )
                    return sorted(
                        task_queue,
                        key=lambda t: dream_strategy_scores.get(t.id, 0),
                        reverse=True,
                    )
                except Exception as e:
                    self.logger.warning(
                        f"SFE Dream mode plugin execution failed: {e}. Proceeding with default prioritization."
                    )
            else:
                self.logger.warning(
                    "SFE Dream mode requested but 'dream_mode' plugin not found. Proceeding with default prioritization."
                )

        async def score(task: Task) -> float:
            deadline_score = (
                1.0 / (task.deadline - now + 1)
                if task.deadline and (task.deadline - now > 0)
                else 0
            )
            risk_score = {"low": 0.1, "medium": 0.5, "high": 1.0}.get(task.risk_level, 0.5)
            context_score = 0.0
            if self.knowledge_graph:
                context_score += await self._get_knowledge_graph_context_score(task)

            if explorer_context:
                if (
                    explorer_context.get("code_vulnerabilities_found")
                    and task.action_type == "security_fix"
                ):
                    context_score += 0.8
                elif (
                    explorer_context.get("test_coverage_issues")
                    and task.action_type == "test_generation"
                ):
                    context_score += 0.5

            return (
                weights.get("priority", 0) * task.priority
                + weights.get("deadline", 0) * deadline_score
                + weights.get("risk", 0) * risk_score
                + weights.get("context", 0) * context_score
            )

        task_scores = await asyncio.gather(*[score(task) for task in task_queue])

        scores_array = self.array_backend.array(task_scores)
        numpy_scores = self.array_backend.asnumpy(scores_array)

        # Defensive check to prevent crashes from bad mocks or backends
        if not hasattr(numpy_scores, "argsort"):
            self.logger.warning(
                "Array backend did not return a NumPy-like array. Converting manually."
            )
            numpy_scores = np.array(numpy_scores)

        indices = numpy_scores.argsort()[::-1]

        return [task_queue[i] for i in indices]

    async def _get_knowledge_graph_context_score(self, task: Task) -> float:
        """Helper to get context score from SFE KnowledgeGraph for a task."""
        if not self.knowledge_graph:
            return 0.0

        try:
            related_facts = await self.knowledge_graph.find_related_facts(
                task.action_type, task.id, task.metadata
            )
            score = len(related_facts) / 10.0
            for fact_id in related_facts:
                fact_data = await self.knowledge_graph.get_fact(task.action_type, fact_id)
                if fact_data and "relevance_score" in fact_data:
                    score += fact_data["relevance_score"] * 0.1
            return score
        except Exception as e:
            self.logger.warning(
                f"Error getting SFE KnowledgeGraph context for task {task.id}: {e}",
                exc_info=True,
            )
            return 0.0

    @critical_alert_decorator
    async def allocate_resources(
        self,
        agent_pool: List[Agent],
        task_queue: List[Task],
        resource_limits: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, List[Task]]:
        start_time = time.monotonic()

        ALLOCATION_LATENCY.observe(0)
        AGENT_ACTIVE_GAUGE.set(len(agent_pool))
        assignments = {}

        try:
            metrics = await get_system_metrics_async()
            if metrics.get("cpu_percent", 0) > 80:
                await self._log_event(
                    "sfe_high_system_load",
                    {
                        "cpu_percent": metrics.get("cpu_percent"),
                        "description": "SFE task allocation limited due to high system load",
                    },
                )

            assignments = await self.safe_execute(
                self.allocator, agent_pool, task_queue, resource_limits or self.config
            )

            final_assignments = {}
            for agent_id, tasks in assignments.items():
                approved_tasks = []
                for task in tasks:
                    if (
                        self.human_in_loop
                        and self.config.get("human_approval_threshold")
                        and self.config["human_approval_threshold"] != "none"
                        and task.risk_level.lower()
                        == self.config["human_approval_threshold"].lower()
                    ):
                        try:
                            response = await self.human_in_loop.request_approval(
                                {
                                    "decision_id": task.id,
                                    "risk_level": task.risk_level,
                                    "action": task.action_type,
                                    "task_details": safe_serialize(task.metadata),
                                    "sim_id": task.metadata.get("sim_id"),
                                }
                            )
                            if response.get("approved", False):
                                approved_tasks.append(task)
                                await self._log_event(
                                    "sfe_task_human_approved",
                                    {
                                        "task_id": task.id,
                                        "agent_id": agent_id,
                                        "sim_id": task.metadata.get("sim_id"),
                                    },
                                )
                            else:
                                await self._log_event(
                                    "sfe_task_allocation_denied_human",
                                    {
                                        "task_id": task.id,
                                        "reason": response.get("comment", "Human denial"),
                                        "sim_id": task.metadata.get("sim_id"),
                                    },
                                )
                                if self.feedback_manager:
                                    await self.feedback_manager.log_error(
                                        {
                                            "type": "sfe_human_denial",
                                            "task_id": task.id,
                                            "reason": response.get("comment"),
                                        }
                                    )
                        except Exception as e:
                            self.logger.error(
                                f"Error requesting SFE human approval for task {task.id}: {e}",
                                exc_info=True,
                            )
                            await self._log_event(
                                "sfe_human_approval_error",
                                {
                                    "task_id": task.id,
                                    "error": str(e),
                                    "sim_id": task.metadata.get("sim_id"),
                                },
                            )
                            if self.config.get("strict_human_approval_failure", False):
                                pass
                            else:
                                approved_tasks.append(task)
                    else:
                        approved_tasks.append(task)
                final_assignments[agent_id] = approved_tasks

                for task in approved_tasks:
                    plugin_execution_start_time = time.monotonic()
                    try:
                        if self.sfe_core_engine and hasattr(self.sfe_core_engine, "execute_task"):
                            result = await self.sfe_core_engine.execute_task(
                                task.action_type, task.sim_request, task_id=task.id
                            )
                            await self._log_event(
                                "sfe_task_executed_via_core_engine",
                                {
                                    "task_id": task.id,
                                    "result": safe_serialize(result),
                                    "sim_id": task.id,
                                    "agent_id": agent_id,
                                },
                            )
                        else:
                            plugin_instance = None
                            if hasattr(self.plugin_registry, "get_plugin_by_action_name"):
                                plugin_instance = self.plugin_registry.get_plugin_by_action_name(
                                    task.action_type
                                )
                            else:
                                plugin_instance = self.plugin_registry.get(
                                    "ACTION", task.action_type
                                )

                            if plugin_instance and hasattr(plugin_instance, "execute"):
                                result = await self.safe_execute(
                                    plugin_instance.execute,
                                    task.sim_request,
                                    task_id=task.id,
                                )
                                await self._log_event(
                                    "sfe_task_executed_direct_plugin",
                                    {
                                        "task_id": task.id,
                                        "result": safe_serialize(result),
                                        "sim_id": task.id,
                                        "agent_id": agent_id,
                                    },
                                )
                            else:
                                self.logger.warning(
                                    f"No executable SFE plugin found for action_type '{task.action_type}'. Task {task.id} not executed."
                                )
                                await self._log_event(
                                    "sfe_plugin_not_found",
                                    {
                                        "task_id": task.id,
                                        "action_type": task.action_type,
                                        "description": "No executable SFE plugin found for action_type",
                                    },
                                )
                                continue

                        PLUGIN_EXECUTION_LATENCY.labels(plugin_name=task.action_type).observe(
                            time.monotonic() - plugin_execution_start_time
                        )

                    except Exception as e:
                        PLUGIN_EXECUTION_LATENCY.labels(plugin_name=task.action_type).observe(
                            time.monotonic() - plugin_execution_start_time
                        )
                        if self.bug_manager:
                            await self.bug_manager.bug_detected(
                                "sfe_plugin_execution_error",
                                f"SFE Plugin {task.action_type} for task {task.id} failed: {e}",
                                {"task_id": task.id, "error": str(e)},
                            )
                        await self._handle_failed_task(task, str(e))

            await self._log_event(
                "sfe_allocate_resources",
                {
                    "agent_count": len(agent_pool),
                    "task_count": sum(len(tasks) for tasks in final_assignments.values()),
                    "latency": time.monotonic() - start_time,
                },
            )
            ALLOCATION_LATENCY.observe(time.monotonic() - start_time)
            return final_assignments

        except Exception as e:
            await self.rollback_allocation(assignments, agent_pool)
            await self._handle_failed_task(
                task_queue[0] if task_queue else Task(id="unknown", priority=0), str(e)
            )
            raise

    async def _default_allocate(
        self,
        agent_pool: List[Agent],
        task_queue: List[Task],
        resource_limits: Dict[str, Any],
    ) -> Dict[str, List[Task]]:
        assignments = {agent.id: [] for agent in agent_pool}
        max_tasks = resource_limits.get("max_tasks_per_agent", self.config["max_tasks_per_agent"])
        role_priority = {"admin": 3, "sfe_operator": 2, "user": 1}

        for task in task_queue:
            candidates = []
            for agent in agent_pool:
                if not task.required_skills.issubset(agent.skills):
                    continue
                if agent.current_load + task.estimated_compute > agent.max_compute:
                    continue
                if len(assignments[agent.id]) >= max_tasks:
                    continue
                if agent.energy < task.estimated_compute * 10:
                    continue
                task_required_role = str(task.metadata.get("required_role", "user")).lower()
                if role_priority.get(agent.role, 0) < role_priority.get(task_required_role, 0):
                    continue
                candidates.append(agent)

            if not candidates:
                await self._log_event(
                    "sfe_no_suitable_agent",
                    {
                        "task_id": task.id,
                        "description": "No suitable SFE agent found for skills/load/role",
                        "sim_id": task.metadata.get("sim_id"),
                    },
                )
                if self.feedback_manager:
                    await self.feedback_manager.log_error(
                        {
                            "type": "sfe_allocation_failure",
                            "task_id": task.id,
                            "reason": "No suitable SFE agent",
                        }
                    )
                continue

            best_agent = min(
                candidates,
                key=lambda a: (a.current_load, -role_priority.get(a.role, 1)),
            )
            assignments[best_agent.id].append(task)
            best_agent.current_load += task.estimated_compute
            best_agent.energy -= task.estimated_compute * 10
            if best_agent.arbiter_instance:
                if hasattr(best_agent.arbiter_instance, "adjust_energy"):
                    best_agent.arbiter_instance.adjust_energy(-task.estimated_compute * 10)

        return assignments

    @critical_alert_decorator
    async def coordinate_arbiters(
        self, agent_pool: List[Agent], shared_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        start_time = time.monotonic()
        try:
            if self.encrypter is None:
                self.logger.error(
                    "Fernet encrypter not initialized for SFE. Cannot perform secure coordination."
                )
                raise RuntimeError(
                    "Fernet encrypter is not initialized. Cannot coordinate SFE arbiters securely."
                )

            shared_context = shared_context or {}
            encrypted_context = self.encrypter.encrypt(json.dumps(shared_context).encode("utf-8"))

            coordination = await self.safe_execute(self.coordinator, agent_pool, encrypted_context)

            if self.redis_client:
                await self._redis_publish(
                    "sfe_arbiter_coordination",
                    json.dumps(
                        {
                            "event_type": "coordination",
                            "context": safe_serialize(shared_context),
                            "timestamp": time.time(),
                        }
                    ),
                )
            COORDINATION_SUCCESS.inc()
            await self._log_event(
                "sfe_coordinate_arbiters",
                {
                    "agent_count": len(agent_pool),
                    "latency": time.monotonic() - start_time,
                },
            )
            return coordination

        except Exception as e:
            await self._handle_failed_task(Task(id="sfe_coord_unknown", priority=0), str(e))
            raise

    async def _default_coordinate(
        self, agent_pool: List[Agent], encrypted_context: bytes
    ) -> Dict[str, Any]:
        coordination_results = {}

        if self.encrypter is None:
            self.logger.error(
                "Fernet encrypter not initialized for SFE. Cannot decrypt context for coordination."
            )
            raise RuntimeError("Fernet encrypter is not initialized. Cannot decrypt SFE context.")

        try:
            decrypted_context = json.loads(
                self.encrypter.decrypt(encrypted_context).decode("utf-8")
            )
        except InvalidToken as e:
            self.logger.error(
                f"Failed to decrypt context during SFE coordination: {e}. Invalid encryption token.",
                exc_info=True,
            )
            raise RuntimeError(
                "Failed to decrypt SFE coordination context due to invalid token."
            ) from e
        except Exception as e:
            self.logger.error(
                f"Failed to decrypt or deserialize context during SFE coordination: {e}",
                exc_info=True,
            )
            raise RuntimeError(f"Failed to decrypt SFE coordination context: {e}.") from e

        proposals = {}

        for agent in agent_pool:
            if agent.arbiter_instance and hasattr(agent.arbiter_instance, "propose_action"):
                try:
                    action = await agent.arbiter_instance.propose_action(decrypted_context)
                    proposals[agent.id] = action
                    coordination_results[agent.id] = "proposed"
                    await self.share_learning(agent, action)
                except Exception as e:
                    self.logger.warning(
                        f"SFE Agent {agent.id} failed to propose action: {e}",
                        exc_info=True,
                    )
                    coordination_results[agent.id] = "proposal_failed"
                    if self.feedback_manager:
                        await self.feedback_manager.log_error(
                            {
                                "type": "sfe_proposal_failure",
                                "agent_id": agent.id,
                                "reason": str(e),
                            }
                        )

        if proposals and decrypted_context and "action_schema" in decrypted_context:
            actions = [
                p["action"] for p in proposals.values() if isinstance(p, dict) and "action" in p
            ]
            if actions:
                majority_action = Counter(actions).most_common(1)[0][0]
                decrypted_context["agreed_action"] = majority_action
                if self.knowledge_graph:
                    await self.knowledge_graph.add_fact(
                        "SFECoordinationResults",
                        f"coord_{uuid.uuid4()}",
                        {"action": majority_action, "context": decrypted_context},
                        source="sfe_decision_optimizer",
                    )
                self.logger.info(f"SFE Consensus reached on action: {majority_action}")
            else:
                self.logger.info(
                    "No valid actions proposed for SFE consensus or no 'action' key in proposals."
                )
        elif not proposals:
            self.logger.info("No proposals received from agents for SFE coordination.")
        else:
            self.logger.warning(
                "Decrypted context or action_schema missing for SFE consensus evaluation."
            )

        for agent in agent_pool:
            if agent.arbiter_instance and hasattr(agent.arbiter_instance, "receive_context"):
                try:
                    await agent.arbiter_instance.receive_context(decrypted_context)
                    coordination_results[agent.id] = "context_updated"
                except Exception as e:
                    self.logger.warning(
                        f"SFE Agent {agent.id} failed to receive context: {e}",
                        exc_info=True,
                    )
                    coordination_results[agent.id] = "context_update_failed"
                    if self.feedback_manager:
                        await self.feedback_manager.log_error(
                            {
                                "type": "sfe_context_update_failure",
                                "agent_id": agent.id,
                                "reason": str(e),
                            }
                        )

        return coordination_results

    async def share_learning(self, agent: Agent, strategy: Dict[str, Any]):
        """Shares an SFE agent's learning/strategy with the SFE KnowledgeGraph."""
        if self.knowledge_graph:
            await self.knowledge_graph.add_fact(
                "SFEAgentLearning",
                f"{agent.id}_{uuid.uuid4()}",
                {"strategy": strategy, "agent_id": agent.id},
                source="sfe_decision_optimizer",
            )
        else:
            self.logger.warning(
                f"SFE KnowledgeGraph not initialized, skipping learning share for agent {agent.id}."
            )

    async def rollback_allocation(
        self, assignments: Dict[str, List[Task]], agent_pool: List[Agent]
    ):
        """Rolls back SFE resource allocations in case of an error."""
        agent_map = {agent.id: agent for agent in agent_pool}

        for agent_id, tasks in assignments.items():
            agent = agent_map.get(agent_id)
            if agent:
                for task in tasks:
                    agent.current_load -= task.estimated_compute
                    agent.energy += task.estimated_compute * 10
                agent.current_load = max(0.0, agent.current_load)
                agent.energy = min(100.0, agent.energy)
            else:
                self.logger.warning(
                    f"SFE Agent {agent_id} not found in agent_pool during rollback. Possible stale agent reference."
                )

        await self._log_event("sfe_allocation_rollback", {"agent_count": len(assignments)})

    async def get_metrics(self) -> Dict[str, Any]:
        """Retrieves current SFE DecisionOptimizer metrics."""
        return {
            "event_log_size": len(self.event_log),
            "active_agents_gauge": AGENT_ACTIVE_GAUGE._value.get(),
            "allocation_latency_metrics": ALLOCATION_LATENCY.collect(),
            "coordination_success_total": COORDINATION_SUCCESS._value.get(),
            "critical_errors_total": ERRORS_CRITICAL._value.get(),
            "plugin_execution_latency_metrics": PLUGIN_EXECUTION_LATENCY.collect(),
            "db_operation_latency_metrics": DB_OPERATION_LATENCY.collect(),
            "strategy_refresh_count_total": STRATEGY_REFRESH_COUNT._value.get(),
            "strategy_refresh_success_total": STRATEGY_REFRESH_SUCCESS._value.get(),
        }

    async def explain_decision(self, decision_id: str) -> Dict[str, Any]:
        """Generates an explanation for a specific SFE decision."""
        EXPLANATION_EVENTS.inc()
        decision_event = next((e for e in self.event_log if e["id"] == decision_id), None)

        if not decision_event:
            if self.knowledge_graph:
                kg_fact = await self.knowledge_graph.get_fact(
                    "SFEDecisionOptimizerEvents", decision_id
                )
                if kg_fact and "value" in kg_fact:
                    decision_event = kg_fact["value"]
                else:
                    return {
                        "error": f"SFE Decision {decision_id} not found in memory or KnowledgeGraph."
                    }
            else:
                return {
                    "error": f"SFE Decision {decision_id} not found in memory. KnowledgeGraph not initialized to search further."
                }

        if self.explainable_reasoner:
            try:
                context_for_reasoner = decision_event.get("details", {})
                explanation = await self.explainable_reasoner.explain(
                    f"Why was the SFE '{decision_event['type']}' decision made?",
                    context_for_reasoner,
                )
                return {
                    "decision_id": decision_id,
                    "type": decision_event["type"],
                    "explanation": explanation,
                    "context": context_for_reasoner,
                    "timestamp": decision_event.get("timestamp"),
                }
            except Exception as e:
                self.logger.error(
                    f"Failed to generate explanation for SFE decision {decision_id}: {e}",
                    exc_info=True,
                )
                return {"error": f"Failed to generate SFE explanation: {e}"}
        else:
            return {"error": "SFE ExplainableReasoner not initialized."}

    async def stream_events(self, websocket: WebSocket):
        """Streams real-time SFE events over a WebSocket connection."""
        await websocket.accept()
        last_index_sent = -1
        try:
            while True:
                async with self.lock:
                    current_event_count = len(self.event_log)
                    if current_event_count > last_index_sent + 1:
                        events_to_send = self.event_log[last_index_sent + 1 :]
                        for event in events_to_send:
                            await websocket.send_json(safe_serialize(event))
                        last_index_sent = current_event_count - 1
                await asyncio.sleep(1)
        except WebSocketDisconnect:
            self.logger.info("SFE WebSocket disconnected for event streaming.")
        except Exception as e:
            self.logger.error(f"Error during SFE WebSocket streaming: {e}", exc_info=True)
            if self.feedback_manager:
                await self.feedback_manager.log_error(
                    {"type": "sfe_websocket_error", "error": str(e)}
                )

    async def compute_trust_score(
        self, context: Dict[str, Any], user_id: Optional[str] = None
    ) -> float:
        """
        Computes a trust score for an authentication context using SFE policies,
        knowledge graph enrichment, and human-in-the-loop approval.
        """
        start_time = time.monotonic()
        task_id = f"sfe_trust_score_{uuid.uuid4()}"
        user_id = user_id or "system"

        task = Task(
            id=task_id,
            priority=0.5,
            required_skills={"authentication"},
            estimated_compute=1.0,
            risk_level=context.get("risk_level", "medium"),
            action_type="sfe_trust_scoring",
            metadata={"context": context, "user_id": user_id, "sim_id": task_id},
        )

        try:
            if self.policy_engine and self.config["policy_check"]:
                policy_start_time = time.monotonic()
                allowed, reason = await self.policy_engine.should_auto_learn(
                    "SFEAuthentication", task.id, user_id, context
                )
                DB_OPERATION_LATENCY.labels(operation_type="sfe_policy_check").observe(
                    time.monotonic() - policy_start_time
                )

                if not allowed:
                    await self._log_event(
                        "sfe_trust_score_policy_denied",
                        {
                            "task_id": task.id,
                            "reason": reason,
                            "user_id": user_id,
                            "score_result": 0.0,
                        },
                    )
                    if self.feedback_manager:
                        await self.feedback_manager.log_error(
                            {
                                "type": "sfe_policy_denial",
                                "task_id": task.id,
                                "reason": reason,
                                "user_id": user_id,
                            }
                        )
                    return 0.0

            context_score = 0.0
            if self.knowledge_graph:
                kg_start_time = time.monotonic()
                try:
                    related_facts = await self.knowledge_graph.find_related_facts(
                        "SFEAuthenticationContext", task.id, context
                    )
                    context_score = len(related_facts) / 10.0
                except Exception as kg_e:
                    self.logger.warning(
                        f"SFE KnowledgeGraph context enrichment failed for trust score task {task.id}: {kg_e}",
                        exc_info=True,
                    )
                    context_score = 0.0
                DB_OPERATION_LATENCY.labels(operation_type="sfe_knowledge_graph_lookup").observe(
                    time.monotonic() - kg_start_time
                )

            score = 0.7
            anomalies = 0

            weights = {
                "mfa_enabled": 0.15,
                "device_registered": 0.10,
                "secure_boot_enabled": 0.05,
                "unusual_location": -0.30,
                "outdated_os": -0.20,
                "jailbroken_rooted": -0.40,
                "threat_intel_flagged": -0.50,
            }

            for key, weight in weights.items():
                if key in context and bool(context[key]):
                    score += weight
                    if weight < 0:
                        anomalies += 1

            score += context_score * 0.10

            last_seen = context.get("last_seen", 0)
            if last_seen and last_seen < time.time() - 86400:
                score *= 0.9

            if anomalies >= 3:
                score *= 0.8

            score = min(max(score, 0.0), 1.0)

            if self.human_in_loop and task.risk_level.lower() == "high":
                human_loop_start_time = time.monotonic()
                try:
                    response = await self.human_in_loop.request_approval(
                        {
                            "decision_id": task.id,
                            "risk_level": "high",
                            "action": "sfe_trust_scoring_approval",
                            "context": safe_serialize(context),
                            "user_id": user_id,
                        }
                    )
                    if not response.get("approved", False):
                        score *= 0.5
                        await self._log_event(
                            "sfe_trust_score_human_denial",
                            {
                                "task_id": task.id,
                                "reason": response.get("comment", "Human denial"),
                                "user_id": user_id,
                                "score_result": score,
                            },
                        )
                        if self.feedback_manager:
                            await self.feedback_manager.log_error(
                                {
                                    "type": "sfe_human_denial_trust_score",
                                    "task_id": task.id,
                                    "reason": response.get("comment"),
                                    "user_id": user_id,
                                }
                            )
                    else:
                        await self._log_event(
                            "sfe_trust_score_human_approved",
                            {
                                "task_id": task.id,
                                "user_id": user_id,
                                "score_result": score,
                            },
                        )
                except Exception as hitl_e:
                    self.logger.error(
                        f"SFE Human-in-loop approval failed for trust score task {task.id}: {hitl_e}",
                        exc_info=True,
                    )
                DB_OPERATION_LATENCY.labels(operation_type="sfe_human_in_loop_approval").observe(
                    time.monotonic() - human_loop_start_time
                )

            await self._log_event(
                "sfe_trust_score_computed",
                {
                    "task_id": task.id,
                    "score": score,
                    "anomalies": anomalies,
                    "user_id": user_id,
                    "context_factors": {k: context.get(k) for k in weights.keys()},
                },
            )

            if self.knowledge_graph:
                await self.knowledge_graph.add_fact(
                    "SFETrustScores",
                    task.id,
                    {
                        "score": score,
                        "context": safe_serialize(context),
                        "user_id": user_id,
                    },
                    source="sfe_decision_optimizer_trust_score",
                )

            if self.feedback_manager:
                await self.feedback_manager.record_metric(
                    "sfe_trust_score", score, {"task_id": task.id, "user_id": user_id}
                )

            ALLOCATION_LATENCY.observe(time.monotonic() - start_time)
            return score

        except Exception as e:
            await self._handle_failed_task(task, f"SFE Trust score computation failed: {e}")
            self.logger.error(
                f"SFE Trust score computation for user {user_id} failed unexpectedly: {e}",
                exc_info=True,
            )
            return 0.0
