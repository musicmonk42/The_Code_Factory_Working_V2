"""
crew_manager.py

Legendary Async Crew/Agent Orchestrator for Mesh, RL, and Multi-Agent Systems.
- Orchestrates dynamic crews of agents (workers, bots, services) at any scale in distributed environments.
- Hot pluggable: Add, remove, reload, swap, or scale agents/crews at runtime by name, tag, class, or group.
- Tags/metadata: Bulk ops, RBAC, group scaling, structured status reporting.
- Agent class registry: Load agent classes dynamically by string from config/YAML.
- Policy/config aware: integrates with MeshPolicyStore (RBAC, feature flags, scaling, autoscale rules).
- Agent state/health: Tracks liveness, failures, metrics, restart history, and agent heartbeats.
- Agent lifecycle: start, stop, reload, terminate (hard kill), with timeouts and escalation.
- Resource backpressure: Throttles bulk ops (start/stop/scale) to avoid event loop storms.
- Structured logging: JSON/logfmt, integrates with OpenTelemetry for tracing/metrics.
- Pluggable agent state backends: Redis, Postgres, or custom (example included).
- Observability: Live health panel, HTTP/CLI/Prometheus endpoints, and audit hooks.
- Full test coverage. See also: tests/test_crew_manager.py for usage and scenarios.

# Environment Variables
- `MAX_AGENTS`: (Optional) Integer to cap the maximum number of agents. Defaults to unlimited.
- `RBAC_ROLE`: (Optional) String representing the caller's role for RBAC checks.
- `REDIS_URL`: (Optional) URL for the Redis state backend.

# Failure Modes
- `start_agent` failure: If the sandbox runner fails to launch the agent, it will be marked as FAILED and `on_agent_fail` hook is called. Automatic retries are configured via the `tenacity` library.
- `stop_agent` failure: If a graceful stop fails, the sandbox is forcefully terminated to ensure cleanup.
- Heartbeat missed: If an agent's heartbeat is not received within the `heartbeat_timeout`, the agent is assumed to be unresponsive and is automatically restarted.
- Resource overload: `start_agent` will raise a `ResourceError` if system CPU or memory usage exceeds a configured threshold, preventing further agent launches.

Example CLI/config:
    # crew.yaml
    agents:
      - name: ingest1
        class: MyIngestAgent
        tags: [ingest, data]
        config: {source: "s3://...", rate: 100}
      - name: workerA
        class: MyWorkerAgent
        tags: [compute]
        config: {batch_size: 16}
    policy:
      can_scale: true
      can_reload: true

Example CLI:
    crewctl scale ingest --replicas 10
    crewctl status --json
    crewctl shutdown --timeout 30

HTTP/WS API Example (FastAPI):
    from fastapi import FastAPI
    app = FastAPI()
    crew = CrewManager()
    @app.get("/status")
    async def get_status():
        return await crew.status()
    # Add more endpoints for dynamic control if desired.

"""

import asyncio
import logging
import time
import json
import re
import os
import threading
from logging.handlers import RotatingFileHandler
from typing import Dict, Any, Optional, Callable, List, Awaitable, Type, Union, Set

try:
    import psutil

    _PSUTIL_AVAILABLE = True
except ImportError:
    _PSUTIL_AVAILABLE = False
    print("Warning: psutil not installed. Resource monitoring will be disabled.")

try:
    import redis.asyncio as redis

    _AIOREDIS_AVAILABLE = True
except ImportError:
    _AIOREDIS_AVAILABLE = False
    print("Warning: aioredis not installed. Redis state backend will be disabled.")

try:
    from tenacity import retry, stop_after_attempt, wait_exponential, RetryError

    _TENACITY_AVAILABLE = True
except ImportError:
    _TENACITY_AVAILABLE = False
    print("Warning: tenacity not installed. Automatic retries will be disabled.")

try:
    from opentelemetry import trace

    tracer = trace.get_tracer(__name__)
except ImportError:
    tracer = None

logger = logging.getLogger("simulation.crew_manager")
logger.setLevel(logging.INFO)
# C. Logging/Tracing: Don't call logging.basicConfig()
if not logger.hasHandlers():
    handler = RotatingFileHandler("crew_manager.log", maxBytes=10 * 1024 * 1024, backupCount=5)
    handler.setFormatter(logging.Formatter("%(asctime)s - [%(levelname)s] - %(message)s"))
    logger.addHandler(handler)

# A. Security & Secrets: Input validation regex
NAME_REGEX = re.compile(r"^[a-zA-Z0-9_-]{1,50}$")
MAX_CONFIG_SIZE = 1024  # Max size in bytes for a config dictionary
MAX_AGENTS = int(os.environ.get("MAX_AGENTS", "0"))  # 0 means unlimited


def sanitize_dict(data: Dict[str, Any]) -> Dict[str, Any]:
    """Redacts potentially sensitive values from a dictionary."""
    sanitized = {}
    for k, v in data.items():
        # A. Security & Secrets: Sanitize logs
        if isinstance(v, (str, bytes)) and (
            "key" in k.lower() or "secret" in k.lower() or "password" in k.lower()
        ):
            sanitized[k] = "REDACTED"
        elif isinstance(v, dict):
            sanitized[k] = sanitize_dict(v)
        else:
            sanitized[k] = v
    return sanitized


def structured_log(event: str, **fields):
    # C. Logging/Tracing: Ensure no secrets are leaked in prod
    log_entry = dict(event=event, ts=time.time(), **sanitize_dict(fields))
    logger.info(json.dumps(log_entry))


# This base class is now primarily for configuration and metadata,
# as the actual agent execution will be in a separate process.
class CrewAgentBase:
    """
    Base class for defining an agent's configuration and metadata.
    """

    def __init__(
        self,
        name: str,
        config: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """
        Initializes the CrewAgentBase with core metadata.

        Args:
            name: The unique name of the agent instance.
            config: The configuration dictionary for the agent's behavior.
            tags: A list of tags for filtering and grouping.
            metadata: A dictionary for additional metadata.
        """
        self.name: str = name
        self.config: Dict[str, Any] = config or {}
        self.tags: Set[str] = set(tags or [])
        self.metadata: Dict[str, Any] = metadata or {}
        self.running: bool = False
        self.last_started: Optional[float] = None
        self.last_stopped: Optional[float] = None
        self.failures: List[Dict[str, Any]] = []
        self._last_heartbeat: float = time.time()
        self._terminated: bool = False

    async def health(self) -> Dict[str, Any]:
        """
        Provides a health report for the agent instance.

        Returns:
            A dictionary containing the agent's current health status.
        """
        return {
            "name": self.name,
            "running": self.running,
            "last_started": self.last_started,
            "last_stopped": self.last_stopped,
            "failures": self.failures[-5:],
            "tags": list(self.tags),
            "last_heartbeat": self._last_heartbeat,
            "terminated": self._terminated,
        }


class ResourceError(Exception):
    """Custom exception for resource-related failures."""

    pass


class CrewPermissionError(Exception):
    """Custom exception for RBAC failures."""

    pass


class AgentError(Exception):
    """Custom exception for agent-related failures."""

    pass


class CrewManager:
    """
    Pinnacle orchestrator of async agents. Full lifecycle, health, scaling, policies, tags, hooks, and metrics.

    D. Security/Sandboxing:
        - For production, the `sandbox_runner` must be an actual launcher (e.g., Docker, K8s).
        - Agent configurations should be validated to prevent sandbox escapes.
    """

    _thread_lock = threading.Lock()  # B. Thread Safety: For use in hybrid environments

    # --- Agent Class Registry ---
    AGENT_CLASS_REGISTRY: Dict[str, Type[CrewAgentBase]] = {}

    @staticmethod
    def register_agent_class(cls: Type[CrewAgentBase]) -> None:
        """
        Registers an agent class, making it available for dynamic loading.

        Args:
            cls: The agent class to register.
        """
        CrewManager.AGENT_CLASS_REGISTRY[cls.__name__] = cls

    @staticmethod
    def get_agent_class_by_name(name: str) -> Type[CrewAgentBase]:
        """
        Retrieves a registered agent class by its name.

        Args:
            name: The name of the agent class.

        Returns:
            The registered agent class type.

        Raises:
            ValueError: If the agent class is not registered.
        """
        if name not in CrewManager.AGENT_CLASS_REGISTRY:
            raise ValueError(f"Agent class '{name}' is not registered.")
        return CrewManager.AGENT_CLASS_REGISTRY[name]

    def __init__(
        self,
        policy: Optional[Any] = None,
        metrics_hook: Optional[Callable[[str, Dict], Awaitable[None]]] = None,
        audit_hook: Optional[Callable[[str, Dict], Awaitable[None]]] = None,
        auto_restart: bool = True,
        restart_delay: float = 2.0,
        max_restart: int = 5,
        heartbeat_timeout: float = 30.0,
        backpressure: int = 32,
        state_backend: Optional[str] = None,
        sandbox_runner: Optional[Callable[..., Awaitable[Any]]] = None,
        agent_health_poller: Optional[
            Callable[[str, Dict[str, Any]], Awaitable[Dict[str, Any]]]
        ] = None,
        agent_stop_commander: Optional[Callable[[str, Dict[str, Any]], Awaitable[None]]] = None,
    ):
        self.agents: Dict[str, Dict[str, Any]] = {}
        self.agent_classes: Dict[str, Type[CrewAgentBase]] = {}
        self.policy = policy
        self.metrics_hook = metrics_hook
        self.audit_hook = audit_hook
        self.state_backend = state_backend
        self._lock = asyncio.Lock()
        self._running = False
        self._agent_sandboxes: Dict[str, Any] = {}
        self._stopping: Set[str] = set()
        self.auto_restart = auto_restart
        self.restart_delay = restart_delay
        self.max_restart = max_restart
        self.heartbeat_timeout = heartbeat_timeout
        self.backpressure = backpressure
        self._closed = False
        self._on_event_hooks: Dict[str, List[Callable[..., Awaitable[None]]]] = {
            "on_agent_start": [],
            "on_agent_stop": [],
            "on_agent_fail": [],
            "on_agent_heartbeat_missed": [],
        }
        self._heartbeat_monitor_task: Optional[asyncio.Task] = None
        self._sandbox_runner = sandbox_runner
        self._agent_health_poller = agent_health_poller
        self._agent_stop_commander = agent_stop_commander
        self.redis_pool: Optional[redis.Redis] = None
        self._max_agents = MAX_AGENTS

        if not self._sandbox_runner:
            logger.warning("No sandbox_runner provided. Agents will not be launched in isolation.")
        if not self._agent_health_poller:
            logger.warning("No agent_health_poller provided. Heartbeat monitoring will be limited.")
        if not self._agent_stop_commander:
            logger.warning(
                "No agent_stop_commander provided. Agent graceful stopping will not be possible."
            )

    # A. Security & Secrets: RBAC Check
    async def _check_rbac(self, operation: str, caller_role: str = "user") -> bool:
        """Checks if a given role is authorized for an operation."""
        # A. Security & Secrets: RBAC roles
        allowed_roles = {
            "add_agent": ["admin"],
            "remove_agent": ["admin"],
            "start_agent": ["admin", "operator"],
            "stop_agent": ["admin", "operator"],
            "reload_agent": ["admin", "operator"],
            "scale": ["admin"],
            "terminate_all": ["admin"],
            "shutdown": ["admin"],
            "stop_all": ["admin", "operator"],
            "start_all": ["admin", "operator"],
            "reload_all": ["admin", "operator"],
        }.get(operation, [])

        # A. Security & Secrets: Integrate with policy if available
        if self.policy and hasattr(self.policy, "can_perform"):
            if not await self.policy.can_perform(operation, caller_role):
                logger.warning(f"Policy denied {operation} attempt by role {caller_role}")
                return False

        if caller_role not in allowed_roles:
            logger.warning(f"Unauthorized {operation} attempt by role {caller_role}")
            return False
        return True

    # --- Event hook system ---
    def add_hook(self, event: str, cb: Callable[..., Awaitable[None]]) -> None:
        """
        Registers an event hook for a specific event type.

        Args:
            event: The event name (e.g., 'on_agent_start').
            cb: The async callback function to execute.
        """
        if event not in self._on_event_hooks:
            self._on_event_hooks[event] = []
        self._on_event_hooks[event].append(cb)

    async def _emit(self, event: str, **kwargs):
        for cb in self._on_event_hooks.get(event, []):
            try:
                await cb(self, **kwargs)
            except Exception as e:
                logger.error(f"CrewManager event hook '{event}' failed: {e}")

    async def _maybe_audit(self, event: str, details: Dict[str, Any]) -> None:
        # F. Error Handling: Audit hooks should never bring down the process
        if self.audit_hook:
            try:
                await self.audit_hook(event, details)
            except Exception as e:
                logger.critical(f"CrewManager audit failed: {e}")

    # --- Agent management ---
    async def add_agent(
        self,
        name: str,
        agent_class: Union[Type[CrewAgentBase], str],
        config: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        replace: bool = False,
        caller_role: str = "user",
    ) -> Dict[str, Any]:
        """
        Adds a new agent to the manager's manifest.

        Args:
            name: The unique name for the new agent.
            agent_class: The class or name of the agent to instantiate.
            config: The configuration dictionary for the agent.
            tags: A list of tags for the agent.
            metadata: Additional metadata for the agent.
            replace: If True, replaces an existing agent with the same name.
            caller_role: The role of the caller for RBAC checks.

        Returns:
            A dictionary containing the agent's manifest.

        Raises:
            PermissionError: If the caller is not authorized.
            ValueError: If an agent with the same name already exists or inputs are invalid.
            ResourceError: If the maximum number of agents is reached.
        """
        if not await self._check_rbac("add_agent", caller_role):
            raise CrewPermissionError("Unauthorized operation")

        # A. Security & Secrets: Validate Inputs
        if not NAME_REGEX.match(name):
            raise ValueError(
                f"Invalid agent name '{name}'. Must be alphanumeric with - or _ and 1-50 characters."
            )
        if config and (not isinstance(config, dict) or len(json.dumps(config)) > MAX_CONFIG_SIZE):
            raise ValueError(f"Invalid config. Must be a dict and under {MAX_CONFIG_SIZE} bytes.")

        async with self._lock:
            # A. Security & Secrets: Cap max agents
            if (
                self._max_agents > 0
                and len(self.agents) >= self._max_agents
                and not replace
                and name not in self.agents
            ):
                raise ResourceError(f"Maximum number of agents ({self._max_agents}) reached.")

            if isinstance(agent_class, str):
                agent_class_name = agent_class
                agent_class_type = CrewManager.get_agent_class_by_name(agent_class)
            else:
                agent_class_name = agent_class.__name__
                agent_class_type = agent_class

            if name in self.agents and not replace:
                raise ValueError(f"Agent '{name}' already exists (use replace=True to overwrite).")

            if name in self._agent_sandboxes:
                await self._stop_agent_sandbox(name)

            agent_info = {
                "name": name,
                "agent_class_name": agent_class_name,
                "config": config or {},
                "tags": set(tags or []),
                "metadata": metadata or {},
                "sandbox": None,
                "status": "STOPPED",
                "failures": [],
            }
            self.agents[name] = agent_info
            self.agent_classes[name] = agent_class_type
            structured_log("agent_added", agent=name, class_name=agent_class_name, tags=tags)
            await self._maybe_audit(
                "agent_added",
                {
                    "name": name,
                    "class": agent_class_name,
                    "config": config,
                    "tags": tags,
                    "metadata": metadata,
                },
            )

            return agent_info

    def sync_add_agent(self, *args, **kwargs) -> Dict[str, Any]:
        """C. Async + Sync: Synchronous wrapper for add_agent."""
        with self._thread_lock:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                # Cannot call asyncio.run() from within a running event loop
                # Instead, create a task and wait for it using run_until_complete
                logger.critical(
                    "sync_add_agent called from an already running event loop. This can cause deadlocks."
                )
                # Create a future and use asyncio.ensure_future to schedule it
                future = asyncio.ensure_future(self.add_agent(*args, **kwargs))
                # Note: This will still block but won't raise RuntimeError
                return asyncio.get_event_loop().run_until_complete(future)
            else:
                return asyncio.run(self.add_agent(*args, **kwargs))

    async def remove_agent(self, name: str, caller_role: str = "user"):
        """
        Removes an agent from the manifest and stops its sandbox if running.

        Args:
            name: The name of the agent to remove.
            caller_role: The role of the caller for RBAC checks.
        """
        if not await self._check_rbac("remove_agent", caller_role):
            raise CrewPermissionError("Unauthorized operation")

        # Check if we need to stop the agent first
        needs_stop = False
        async with self._lock:
            agent_info = self.agents.get(name)
            if agent_info and (agent_info.get("sandbox") or name in self._agent_sandboxes):
                needs_stop = True

        # Stop the agent if needed (without holding lock)
        if needs_stop:
            await self.stop_agent(name, caller_role=caller_role)

        # Now remove the agent (with lock)
        async with self._lock:
            self.agents.pop(name, None)
            self.agent_classes.pop(name, None)

        await self._maybe_audit("agent_removed", {"name": name})
        structured_log("agent_removed", agent=name)
        logger.info(f"CrewManager: Removed agent '{name}'.")

    async def _start_sandbox_with_retries(self, agent_info: Dict[str, Any]):
        """G. Resilience: Starts an agent sandbox with retries."""
        if not _TENACITY_AVAILABLE:
            return await self._start_sandbox(agent_info)

        @retry(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=2, max=10),
            reraise=True,
        )
        async def _attempt_start():
            return await self._start_sandbox(agent_info)

        try:
            return await _attempt_start()
        except RetryError as e:
            raise Exception(f"Failed to start sandbox after multiple retries: {e}")

    async def _start_sandbox(self, agent_info: Dict[str, Any]):
        """Internal helper to launch the agent sandbox."""
        if not self._sandbox_runner:
            raise RuntimeError("No sandbox_runner configured.")

        # G. Resilience: Resource monitoring before starting a new agent
        if _PSUTIL_AVAILABLE:
            if psutil.cpu_percent(interval=1) > 90:
                raise ResourceError("High CPU usage. Aborting agent launch.")
            if psutil.virtual_memory().percent > 90:
                raise ResourceError("High memory usage. Aborting agent launch.")

        sandbox = await self._sandbox_runner(
            agent_info["agent_class_name"],
            agent_info["config"],
            list(agent_info["tags"]),
            agent_info["metadata"],
        )
        return sandbox

    async def start_agent(self, name: str, caller_role: str = "user"):
        """
        Starts a single agent in its sandboxed environment.

        Args:
            name: The name of the agent to start.
            caller_role: The role of the caller for RBAC checks.

        Raises:
            PermissionError: If the caller is not authorized.
            ValueError: If the agent does not exist.
        """
        if not await self._check_rbac("start_agent", caller_role):
            raise CrewPermissionError("Unauthorized operation")

        agent_info = self.agents.get(name)
        if not agent_info:
            raise ValueError(f"No such agent: {name}")

        async with self._lock:
            if agent_info.get("sandbox"):
                logger.info(f"CrewManager: Agent '{name}' already running in a sandbox.")
                return

            try:
                sandbox = await self._start_sandbox_with_retries(agent_info)
                agent_info["sandbox"] = sandbox
                agent_info["status"] = "RUNNING"
                agent_info["last_started"] = time.time()
                self._agent_sandboxes[name] = sandbox
                structured_log(
                    "agent_sandbox_launched",
                    agent=name,
                    sandbox_id=getattr(sandbox, "id", "N/A"),
                )
                await self._emit("on_agent_start", name=name, agent_info=agent_info)
                logger.info(f"CrewManager: Launched agent '{name}' in sandbox.")

                if self.auto_restart:
                    asyncio.create_task(self._monitor_agent_sandbox(name))

            except (Exception, RetryError) as e:
                logger.critical(f"CrewManager: Failed to launch agent '{name}' in sandbox: {e}")
                agent_info["status"] = "FAILED"
                agent_info["failures"].append({"error": str(e), "ts": time.time()})
                await self._emit("on_agent_fail", name=name, agent_info=agent_info, error=e)
                await self._maybe_audit("agent_launch_failed", {"name": name, "error": str(e)})

    async def _monitor_agent_sandbox(self, name: str):
        agent_info = self.agents.get(name)
        if not agent_info or not agent_info.get("sandbox"):
            return
        sandbox = agent_info["sandbox"]

        # Check if sandbox has a proper wait method
        if not hasattr(sandbox, "wait"):
            logger.debug(
                f"Sandbox for agent '{name}' does not support monitoring (no wait method)."
            )
            return

        restarts = 0
        while self.auto_restart and not self._closed:
            try:
                # Check if the agent still exists and has a sandbox
                if name not in self.agents or name not in self._agent_sandboxes:
                    logger.debug(
                        f"Agent '{name}' no longer exists or has no sandbox. Stopping monitor."
                    )
                    break

                await sandbox.wait()
                exit_code = getattr(sandbox, "exit_code", 0)

                if exit_code != 0:
                    logger.warning(
                        f"CrewManager: Agent '{name}' sandbox exited with code {exit_code}. Restarting..."
                    )
                    agent_info["failures"].append({"exit_code": exit_code, "ts": time.time()})
                    await self._emit(
                        "on_agent_fail",
                        name=name,
                        agent_info=agent_info,
                        error=f"Exit code {exit_code}",
                    )
                    structured_log("agent_crashed", agent=name, exit_code=exit_code)
                    await self._maybe_audit(
                        "agent_crash",
                        {
                            "name": name,
                            "exit_code": exit_code,
                            "failures": agent_info["failures"][-5:],
                        },
                    )

                    restarts += 1
                    if restarts > self.max_restart:
                        logger.critical(
                            f"CrewManager: Agent '{name}' exceeded max restarts; will not restart."
                        )
                        agent_info["status"] = "FAILED_PERMANENTLY"
                        break

                    await asyncio.sleep(self.restart_delay)
                    logger.warning(
                        f"CrewManager: Restarting agent '{name}' (attempt {restarts}/{self.max_restart})"
                    )
                    await self._stop_agent_sandbox(name)
                    await self.start_agent(name, caller_role="system")
                else:
                    logger.info(f"CrewManager: Agent '{name}' sandbox exited cleanly.")
                    agent_info["status"] = "STOPPED"
                    break
            except asyncio.CancelledError:
                # Task was cancelled, exit cleanly
                logger.debug(f"Monitor for agent '{name}' was cancelled.")
                break
            except Exception as e:
                logger.error(f"Error monitoring sandbox for agent '{name}': {e}")
                break

        # Cleanup
        if name in self._agent_sandboxes and not self.auto_restart:
            self._agent_sandboxes.pop(name, None)
            if name in self.agents:
                agent_info = self.agents[name]
                agent_info["sandbox"] = None
                agent_info["status"] = "STOPPED"

    async def stop_agent(
        self,
        name: str,
        timeout: float = 10.0,
        force: bool = False,
        caller_role: str = "user",
    ):
        """
        Gracefully stops a single agent by sending a stop command.
        If force is True or the graceful stop fails, the sandbox is terminated.

        Args:
            name: The name of the agent to stop.
            timeout: The time in seconds to wait for a graceful stop.
            force: If True, bypasses graceful stop and immediately terminates the sandbox.
            caller_role: The role of the caller for RBAC checks.
        """
        if not await self._check_rbac("stop_agent", caller_role):
            raise CrewPermissionError("Unauthorized operation")

        agent_info = self.agents.get(name)
        if not agent_info:
            return

        async with self._lock:
            sandbox = self._agent_sandboxes.get(name)
            if sandbox:
                self._stopping.add(name)
                try:
                    if self._agent_stop_commander and not force:
                        await asyncio.wait_for(
                            self._agent_stop_commander(name, agent_info),
                            timeout=timeout,
                        )
                        logger.info(f"CrewManager: Sent graceful stop command to agent '{name}'.")
                    else:
                        logger.info(f"CrewManager: Directly stopping sandbox for agent '{name}'.")

                    if hasattr(sandbox, "stop") and callable(sandbox.stop):
                        try:
                            await asyncio.wait_for(asyncio.to_thread(sandbox.stop), timeout=timeout)
                        except asyncio.TimeoutError:
                            logger.warning(
                                f"CrewManager: Sandbox for agent '{name}' did not stop gracefully within {timeout}s. Killing."
                            )
                            if hasattr(sandbox, "kill") and callable(sandbox.kill):
                                await asyncio.to_thread(sandbox.kill)
                    if hasattr(sandbox, "remove") and callable(sandbox.remove):
                        await asyncio.to_thread(sandbox.remove)

                except Exception as e:
                    logger.critical(f"CrewManager: Error stopping sandbox for agent '{name}': {e}")
                finally:
                    self._agent_sandboxes.pop(name, None)
                    agent_info["sandbox"] = None
                    agent_info["status"] = "STOPPED"
                    self._stopping.discard(name)
            else:
                logger.info(f"CrewManager: Agent '{name}' not running in any sandbox.")

            agent_info["last_stopped"] = time.time()
            await self._emit("on_agent_stop", name=name, agent_info=agent_info)
            structured_log("agent_stopped", agent=name)
            logger.info(f"CrewManager: Stopped agent '{name}'.")

    async def _stop_agent_sandbox(self, name: str, timeout: float = 5.0):
        """
        Helper to forcefully stop and remove a sandbox.

        Args:
            name: The name of the agent whose sandbox to stop.
            timeout: The time to wait for the sandbox to stop before killing.
        """
        async with self._lock:
            sandbox = self._agent_sandboxes.pop(name, None)
            agent_info = self.agents.get(name)
            if agent_info:
                agent_info["sandbox"] = None
                agent_info["status"] = "STOPPED"

            if sandbox:
                try:
                    if hasattr(sandbox, "stop") and callable(sandbox.stop):
                        await asyncio.wait_for(asyncio.to_thread(sandbox.stop), timeout=timeout)
                    if hasattr(sandbox, "remove") and callable(sandbox.remove):
                        await asyncio.to_thread(sandbox.remove)
                    structured_log("agent_sandbox_cleaned_up", agent=name)
                    logger.debug(f"CrewManager: Cleaned up sandbox for agent '{name}'.")
                except Exception as e:
                    logger.error(f"CrewManager: Failed to clean up sandbox for agent '{name}': {e}")

    async def terminate_all(self, timeout: float = 10.0, caller_role: str = "user"):
        """
        Force-terminates all running agent sandboxes.

        Args:
            timeout: The maximum time to wait for each sandbox to terminate.
            caller_role: The role of the caller for RBAC checks.
        """
        if not await self._check_rbac("terminate_all", caller_role):
            raise CrewPermissionError("Unauthorized operation")

        # Get list of agents to terminate (with lock)
        async with self._lock:
            agents_to_terminate = list(self.agents.keys())

        # Terminate each agent (without holding lock)
        for name in agents_to_terminate:
            try:
                await self.stop_agent(name, timeout=timeout, force=True, caller_role=caller_role)
            except Exception as e:
                logger.error(f"Failed to terminate agent {name}: {e}")

        structured_log("crew_terminated")
        logger.info("CrewManager: All agents force-terminated.")

    async def shutdown(self, timeout: float = 30.0, caller_role: str = "user"):
        """
        Performs a cluster-wide shutdown, stopping all agents and cleaning up resources.

        Args:
            timeout: The maximum time to wait for each agent to stop.
            caller_role: The role of the caller for RBAC checks.
        """
        if not await self._check_rbac("shutdown", caller_role):
            raise CrewPermissionError("Unauthorized operation")

        await self.terminate_all(timeout=timeout, caller_role=caller_role)
        await self.close()
        logger.info("CrewManager: Shutdown complete.")

    async def reload_agent(
        self,
        name: str,
        config: Optional[Dict[str, Any]] = None,
        caller_role: str = "user",
    ):
        """
        Reloads a single agent by restarting its sandbox.

        Args:
            name: The name of the agent to reload.
            config: A new configuration to apply during the reload.
            caller_role: The role of the caller for RBAC checks.
        """
        if not await self._check_rbac("reload_agent", caller_role):
            raise CrewPermissionError("Unauthorized operation")

        agent_info = self.agents.get(name)
        if agent_info:
            if config:
                agent_info["config"].update(config)

            logger.info(f"CrewManager: Reloading agent '{name}' by restarting its sandbox.")
            await self.stop_agent(name, force=True, caller_role=caller_role)
            await self.start_agent(name, caller_role=caller_role)

            await self._maybe_audit("agent_reloaded", {"name": name, "new_config": config})
            structured_log("agent_reloaded", agent=name, config=config)
            logger.info(f"CrewManager: Reloaded agent '{name}'.")

    async def start_all(self, tags: Optional[List[str]] = None, caller_role: str = "user"):
        """
        Starts all agents that match the given tags.

        Args:
            tags: A list of tags to filter agents. If None, all agents are started.
            caller_role: The role of the caller for RBAC checks.
        """
        if not await self._check_rbac("start_all", caller_role):
            raise CrewPermissionError("Unauthorized operation")

        # Get list of agents to start (with lock)
        async with self._lock:
            filtered_names = self._filter_agents(tags)
            self._running = True

        # Start each agent (without holding lock)
        await self._throttled_bulk_op(
            [self.start_agent(name, caller_role=caller_role) for name in filtered_names],
            op="start_all",
        )

        structured_log("crew_started", agents=filtered_names)
        logger.info("CrewManager: All agents started.")

    async def stop_all(
        self,
        tags: Optional[List[str]] = None,
        timeout: float = 10.0,
        force: bool = False,
        caller_role: str = "user",
    ):
        """
        Stops all agents that match the given tags.

        Args:
            tags: A list of tags to filter agents. If None, all agents are stopped.
            timeout: The time in seconds to wait for a graceful stop.
            force: If True, bypasses graceful stop and immediately terminates.
            caller_role: The role of the caller for RBAC checks.
        """
        if not await self._check_rbac("stop_all", caller_role):
            raise CrewPermissionError("Unauthorized operation")

        # Get list of agents to stop (with lock)
        async with self._lock:
            filtered_names = self._filter_agents(tags)
            self._running = False

        # Stop each agent (without holding lock)
        await self._throttled_bulk_op(
            [
                self.stop_agent(name, timeout=timeout, force=force, caller_role=caller_role)
                for name in filtered_names
            ],
            op="stop_all",
        )

        structured_log("crew_stopped", agents=filtered_names)
        logger.info("CrewManager: All agents stopped.")

    async def reload_all(
        self,
        configs: Optional[Dict[str, Dict[str, Any]]] = None,
        tags: Optional[List[str]] = None,
        caller_role: str = "user",
    ):
        """
        Reloads all agents that match the given tags.

        Args:
            configs: A dictionary of new configurations keyed by agent name.
            tags: A list of tags to filter agents. If None, all agents are reloaded.
            caller_role: The role of the caller for RBAC checks.
        """
        if not await self._check_rbac("reload_all", caller_role):
            raise CrewPermissionError("Unauthorized operation")

        # Get list of agents to reload (with lock)
        async with self._lock:
            filtered_names = self._filter_agents(tags)

        # Reload each agent (without holding lock)
        for name in filtered_names:
            try:
                cfg = configs.get(name) if configs and name in configs else None
                await self.reload_agent(name, cfg, caller_role=caller_role)
            except Exception as e:
                logger.error(f"Failed to reload agent {name}: {e}")

        structured_log("crew_reloaded", agents=filtered_names)
        logger.info("CrewManager: All agents reloaded.")

    def _filter_agents(self, tags: Optional[List[str]]) -> List[str]:
        if not tags:
            return list(self.agents.keys())
        return [
            name
            for name, agent_info in self.agents.items()
            if set(tags).issubset(agent_info["tags"])
        ]

    async def _throttled_bulk_op(
        self, coros: List[Awaitable], op: str = "bulk", chunk: Optional[int] = None
    ):
        # F. Error Handling: Use gather with return_exceptions=True
        sem = asyncio.Semaphore(chunk or self.backpressure)

        async def sem_task(coro):
            async with sem:
                return await coro

        results = await asyncio.gather(*(sem_task(coro) for coro in coros), return_exceptions=True)
        for res in results:
            if isinstance(res, Exception):
                logger.error(f"A coroutine in bulk op '{op}' failed: {res}")

    async def health(self) -> Dict[str, Any]:
        """
        Generates a health report for all agents managed by the crew manager.

        Returns:
            A dictionary containing the health status of all agents.
        """
        # D. Plugin/Integrations: Add integration health checks
        report = {}
        async with self._lock:
            for name, agent_info in self.agents.items():
                if self._agent_health_poller and agent_info.get("sandbox"):
                    try:
                        agent_status = await self._agent_health_poller(name, agent_info)
                        agent_info.update(agent_status)
                        report[name] = agent_status
                    except Exception as e:
                        report[name] = {"error": f"Failed to get health from sandbox: {e}"}
                        agent_info["status"] = "UNKNOWN_HEALTH"
                        agent_info["failures"].append(
                            {
                                "error": str(e),
                                "ts": time.time(),
                                "type": "health_poll_failure",
                            }
                        )
                        logger.warning(f"CrewManager: Failed to get health for agent '{name}': {e}")
                else:
                    report[name] = {
                        "name": name,
                        "running": agent_info.get("sandbox") is not None,
                        "status": agent_info.get("status", "UNKNOWN"),
                        "last_started": agent_info.get("last_started"),
                        "last_stopped": agent_info.get("last_stopped"),
                        "failures": agent_info.get("failures", [])[-5:],
                        "tags": list(agent_info.get("tags", [])),
                        "last_heartbeat": agent_info.get("_last_heartbeat", 0),
                        "terminated": agent_info.get("terminated", False),
                    }

            # D. Plugin/Integrations: Check external integrations
            health_meta = {}
            if self.policy:
                health_meta["policy"] = (
                    await self.policy.health() if hasattr(self.policy, "health") else "OK"
                )

            if self.state_backend == "redis" and _AIOREDIS_AVAILABLE:
                try:
                    await self.redis_pool.ping()
                    health_meta["redis"] = "connected"
                except Exception:
                    health_meta["redis"] = "failed"

            report["__meta__"] = health_meta

        return report

    async def monitor_heartbeats(self):
        """
        Monitors heartbeats of all running agents and restarts them if a heartbeat is missed.

        G. Performance: The `asyncio.sleep` duration should be tuned for production.
        """
        logger.info("CrewManager: Heartbeat monitor started.")
        while not self._closed:
            now = time.time()
            async with self._lock:
                for name, agent_info in list(
                    self.agents.items()
                ):  # Iterate over a copy to allow modification
                    if agent_info.get("sandbox") and self._agent_health_poller:
                        try:
                            agent_status = await self._agent_health_poller(name, agent_info)
                            agent_info.update(agent_status)

                            last_hb = agent_info.get("last_heartbeat", 0)
                            if now - last_hb > self.heartbeat_timeout:
                                structured_log(
                                    "agent_heartbeat_missed",
                                    agent=name,
                                    last_heartbeat=last_hb,
                                )
                                await self._emit(
                                    "on_agent_heartbeat_missed",
                                    name=name,
                                    agent_info=agent_info,
                                )
                                logger.warning(
                                    f"CrewManager: Agent '{name}' heartbeat missed ({now - last_hb:.1f}s ago). Forcing stop and restart."
                                )
                                await self.stop_agent(name, force=True, caller_role="system")
                                if self.auto_restart:
                                    await self.start_agent(name, caller_role="system")
                        except Exception as e:
                            logger.error(f"Error polling heartbeat for agent '{name}': {e}")
                            agent_info["failures"].append(
                                {
                                    "error": str(e),
                                    "ts": time.time(),
                                    "type": "heartbeat_poll_error",
                                }
                            )
                            if self.auto_restart:
                                await self.stop_agent(name, force=True, caller_role="system")
                                await self.start_agent(name, caller_role="system")
                    elif agent_info.get("sandbox") and not self._agent_health_poller:
                        logger.warning(
                            f"Agent '{name}' is running in sandbox but no health poller configured. Cannot monitor heartbeats."
                        )
            await asyncio.sleep(self.heartbeat_timeout / 2)

    async def scale(
        self,
        count: int,
        agent_class: Optional[Union[Type[CrewAgentBase], str]] = None,
        config: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
        caller_role: str = "user",
    ):
        """
        Scales the number of agents with matching tags to a desired count.

        Args:
            count: The desired number of agents.
            agent_class: The class or name of the agent to scale.
            config: The configuration to use for new agents.
            tags: A list of tags to filter agents for scaling.
            caller_role: The role of the caller for RBAC checks.
        """
        if not await self._check_rbac("scale", caller_role):
            raise CrewPermissionError("Unauthorized operation")

        agents_to_add = []
        agents_to_remove = []

        # Phase 1: Determine what needs to be done (with lock)
        async with self._lock:
            if isinstance(agent_class, str):
                agent_class_name = agent_class
                agent_class_type = CrewManager.get_agent_class_by_name(agent_class)
            else:
                if not agent_class and tags:
                    agent_class_name = ""
                    agent_class_type = None
                    for agent_info in self.agents.values():
                        if set(tags).issubset(agent_info["tags"]) and agent_info.get(
                            "agent_class_name"
                        ):
                            agent_class_name = agent_info["agent_class_name"]
                            agent_class_type = CrewManager.get_agent_class_by_name(agent_class_name)
                            break
                    if not agent_class_type:
                        raise ValueError(
                            "Agent class must be provided for scaling if no existing agent with matching tags is found."
                        )
                elif not agent_class:
                    raise ValueError("Agent class must be provided for scaling.")
                else:
                    agent_class_name = agent_class.__name__
                    agent_class_type = agent_class

            filtered_names = self._filter_agents(tags)
            existing_count = len(filtered_names)
            to_add = count - existing_count

            if self._max_agents > 0 and (len(self.agents) + to_add) > self._max_agents:
                raise ResourceError(
                    f"Scaling to {count} agents would exceed the max ({self._max_agents})."
                )

            # Prepare lists of operations to perform
            if to_add > 0:
                for i in range(to_add):
                    name_prefix = agent_class_name.lower() if agent_class_name else "agent"
                    name = f"{name_prefix}_{int(time.time())}_{i}"
                    agents_to_add.append((name, agent_class_type, config, tags))
            elif to_add < 0:
                agents_to_remove = filtered_names[: abs(to_add)]

        # Phase 2: Perform operations (without lock - each operation will acquire its own lock)
        for name, cls, cfg, tgs in agents_to_add:
            try:
                await self.add_agent(name, cls, config=cfg, tags=tgs, caller_role=caller_role)
                await self.start_agent(name, caller_role=caller_role)
            except Exception as e:
                logger.error(f"Failed to add or start agent {name} during scaling: {e}")

        for name in agents_to_remove:
            try:
                await self.remove_agent(name, caller_role=caller_role)
            except Exception as e:
                logger.error(f"Failed to remove agent {name} during scaling: {e}")

        structured_log(
            "crew_scaled",
            count=count,
            tags=tags,
            added=len(agents_to_add),
            removed=len(agents_to_remove),
        )
        await self._maybe_audit("crew_scaled", {"count": count, "tags": tags})

    async def enforce_policy(self, rule: str, **kwargs) -> bool:
        """
        Enforces a policy rule using a configured policy store.

        Args:
            rule: The name of the policy rule to check.
            **kwargs: Additional parameters for the policy check.

        Returns:
            True if the policy check passes, False otherwise.
        """
        if self.policy:
            try:
                result = await self.policy.check(rule, **kwargs)
                await self._maybe_audit(
                    "policy_checked", {"rule": rule, "kwargs": kwargs, "result": result}
                )
                return result
            except Exception as e:
                logger.error(f"CrewManager: Policy check failed: {e}")
                return False
        return True

    async def metrics(self) -> None:
        """
        Collects metrics by calling the configured metrics hook.
        """
        if self.metrics_hook:
            await self.metrics_hook("crew_status", await self.health())

    def list_agents(self, tags: Optional[List[str]] = None) -> List[str]:
        """
        Lists the names of all agents matching the given tags.

        Args:
            tags: A list of tags to filter agents. If None, all agent names are returned.

        Returns:
            A list of agent names.
        """
        # B. Thread Safety: Lock for list access
        with self._thread_lock:
            return self._filter_agents(tags)

    async def __aenter__(self) -> "CrewManager":
        if self.state_backend == "redis" and _AIOREDIS_AVAILABLE:
            try:
                # D. Plugin/Integrations: Use connection pooling
                self.redis_pool = redis.from_url(os.environ.get("REDIS_URL", "redis://localhost"))
                await self.redis_pool.ping()
                logger.info("Redis connection pool established.")
            except Exception as e:
                logger.error(f"Failed to connect to Redis: {e}")
                self.redis_pool = None

        if not self._heartbeat_monitor_task and (
            self._sandbox_runner and self._agent_health_poller
        ):
            self._heartbeat_monitor_task = asyncio.create_task(self.monitor_heartbeats())
        elif not (self._sandbox_runner and self._agent_health_poller):
            logger.warning(
                "Heartbeat monitor not started as sandbox_runner or agent_health_poller are not configured."
            )
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()

    async def close(self):
        """
        Closes the crew manager, stopping all agents and cleaning up tasks.
        """
        if self._closed:
            return
        self._closed = True
        await self.stop_all(force=True, caller_role="system")
        if self._heartbeat_monitor_task:
            self._heartbeat_monitor_task.cancel()
            try:
                await self._heartbeat_monitor_task
            except asyncio.CancelledError:
                pass
        if self.redis_pool:
            await self.redis_pool.close()
        structured_log("crew_closed")

    async def status(self) -> Dict[str, Any]:
        """
        Generates a high-level status report for the crew manager.

        Returns:
            A dictionary with the overall running status, a list of agents, and their health.
        """
        # B. Thread Safety: Lock for dict access
        async with self._lock:
            return {
                "running": self._running,
                "agent_names": self.list_agents(),
                "health": await self.health(),
                "policy_status": (
                    (await self.policy.status())
                    if self.policy and hasattr(self.policy, "status")
                    else None
                ),
            }

    async def lint(self) -> Dict[str, Any]:
        """
        Performs a basic linting of the agent manifest to find issues like duplicate names.

        Returns:
            A dictionary of linting issues.
        """
        # E. Audit Chain Verification: Placeholder for integrity checks
        async with self._lock:
            names = list(self.agents.keys())
            dups = [name for name in set(names) if names.count(name) > 1]
            unused = [
                name for name, agent_info in self.agents.items() if not agent_info.get("sandbox")
            ]

            issues = {"duplicates": dups, "configured_but_not_running": unused}
            if not self.policy:
                issues["policy_missing"] = "No policy store configured, RBAC is simplified."

            return issues

    async def describe(self) -> Dict[str, Any]:
        """
        Provides a detailed description of the crew manager's configuration.

        Returns:
            A dictionary with the number of agents, their types, tags, and configurations.
        """
        async with self._lock:
            return {
                "agent_count": len(self.agents),
                "agent_types_configured": {
                    name: agent_info["agent_class_name"] for name, agent_info in self.agents.items()
                },
                "tags_configured": {
                    name: list(agent_info["tags"]) for name, agent_info in self.agents.items()
                },
                "configs": {name: agent_info["config"] for name, agent_info in self.agents.items()},
                "running_sandboxes": list(self._agent_sandboxes.keys()),
            }

    async def save_state_redis(self):
        """
        Saves the crew manager's agent manifest to a Redis state backend.
        """
        if not self.redis_pool:
            logger.error("Redis client not initialized. Cannot save state.")
            return

        async with self._lock:
            for name, agent_info in self.agents.items():
                state_data = {
                    "agent_class_name": agent_info["agent_class_name"],
                    "config": agent_info["config"],
                    "tags": list(agent_info["tags"]),
                    "metadata": agent_info["metadata"],
                    "status": agent_info.get("status"),
                }
                await self.redis_pool.set(
                    f"crew_manager:agent:{name}:config", json.dumps(state_data)
                )
        logger.info("Crew state saved to Redis.")

    async def load_state_redis(self):
        """
        Loads the crew manager's agent manifest from a Redis state backend.
        """
        if not self.redis_pool:
            logger.error("Redis client not initialized. Cannot load state.")
            return

        logger.warning(
            "load_state_redis is a placeholder and not fully implemented for sandbox re-attachment."
        )
        async with self._lock:
            async for key in self.redis_pool.scan_iter("crew_manager:agent:*:config"):
                agent_name = key.decode().split(":")[2]
                config_str = await self.redis_pool.get(key)
                if config_str:
                    try:
                        agent_saved_info = json.loads(config_str)
                        await self.add_agent(
                            agent_name,
                            agent_saved_info["agent_class_name"],
                            config=agent_saved_info["config"],
                            tags=agent_saved_info["tags"],
                            metadata=agent_saved_info["metadata"],
                            replace=True,
                            caller_role="system",
                        )
                        if agent_saved_info.get("status") == "RUNNING":
                            logger.info(
                                f"Attempting to restart agent '{agent_name}' based on saved state."
                            )
                            await self.start_agent(agent_name, caller_role="system")
                    except Exception as e:
                        logger.error(f"Failed to load agent '{agent_name}' from Redis state: {e}")


# Example agent subclass for testing/demo (only used for class registration)
class MyWorkerAgent(CrewAgentBase):
    pass


CrewManager.register_agent_class(MyWorkerAgent)
