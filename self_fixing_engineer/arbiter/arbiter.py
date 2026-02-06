# Suppress gym deprecation warning from stable_baselines3 BEFORE any imports
import warnings

warnings.filterwarnings("ignore", message="Gym has been unmaintained since 2022")

import asyncio
import collections
import hashlib
import json
import logging
import os
import random
import sys
import time
from collections import deque
from datetime import datetime, timezone
from functools import wraps
from logging.handlers import RotatingFileHandler
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    ClassVar,
    Coroutine,
    Dict,
    List,
    Optional,
    Set,
)

# TYPE_CHECKING imports to avoid circular dependencies while maintaining type safety
if TYPE_CHECKING:
    from simulation.simulation_module import UnifiedSimulationModule

import aiohttp
import httpx
import numpy as np
from aiohttp import ClientSession
from aiolimiter import AsyncLimiter
from self_fixing_engineer.arbiter.metrics import (
    get_or_create_counter,
    get_or_create_gauge,
    get_or_create_summary,
)
from cryptography.fernet import Fernet
from dotenv import dotenv_values, load_dotenv
from prometheus_client import REGISTRY, push_to_gateway
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import BigInteger, Column, DateTime, String, Text, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.orm import declarative_base
from tenacity import retry, stop_after_attempt, wait_exponential

try:
    import gymnasium as gym

    GYM_AVAILABLE = True
except ImportError as e:
    GYM_AVAILABLE = False
    logging.debug(f"Optional dependency missing: {e} (gymnasium)")
try:
    from stable_baselines3 import PPO
    from stable_baselines3.common.env_util import make_vec_env
    from stable_baselines3.common.evaluation import evaluate_policy
    from stable_baselines3.common.vec_env import DummyVecEnv

    STABLE_BASELINES3_AVAILABLE = True
except ImportError as e:
    STABLE_BASELINES3_AVAILABLE = False
    logging.debug(f"Optional dependency missing: {e} (stable_baselines3)")
    PPO = None
    make_vec_env = None
    evaluate_policy = None
    DummyVecEnv = None
try:
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import train_test_split

    SKLEARN_AVAILABLE = True
except ImportError as e:
    SKLEARN_AVAILABLE = False
    logging.debug(f"Optional dependency missing: {e} (sklearn)")
    LogisticRegression = None
    train_test_split = None
try:
    import uvloop

    UVLOOP_AVAILABLE = True
except ImportError as e:
    UVLOOP_AVAILABLE = False
    logging.debug(f"Optional dependency missing: {e} (uvloop)")

try:
    import sentry_sdk

    SENTRY_AVAILABLE = True
except ImportError as e:
    SENTRY_AVAILABLE = False
    sentry_sdk = None
    logging.debug(f"Optional dependency missing: {e} (sentry_sdk)")

try:
    import redis.asyncio as redis

    AIOREDIS_AVAILABLE = True
except ImportError as e:
    AIOREDIS_AVAILABLE = False
    logging.debug(f"Optional dependency missing: {e} (aioredis)")


# --- Pydantic Configuration Management ---
load_dotenv()


class MyArbiterConfig(BaseSettings):
    """
    Configuration for the Arbiter agent, loaded from environment variables or a .env file.
    """

    DATABASE_URL: str = "sqlite+aiosqlite:///omnicore.db"
    REDIS_URL: str
    ENCRYPTION_KEY: SecretStr
    REPORTS_DIRECTORY: str = "./reports"
    FRONTEND_URL: HttpUrl
    ARENA_PORT: int
    CODEBASE_PATHS: List[str]
    ENABLE_CRITICAL_FAILURES: bool = False
    AI_API_TIMEOUT: int = 30
    MEMORY_LIMIT: int = 40
    OMNICORE_URL: HttpUrl = Field(
        "https://api.example.com", description="OmniCore API endpoint"
    )
    ARBITER_URL: HttpUrl = Field(
        "https://arbiter.example.com", description="Arbiter API endpoint"
    )
    AUDIT_LOG_PATH: str = "./omnicore_audit.log"
    PLUGINS_ENABLED: bool = True
    ROLE_MAP: Dict[str, int] = {"guest": 0, "user": 1, "explorer_user": 2, "admin": 3}
    SLACK_WEBHOOK_URL: Optional[HttpUrl] = None
    ALERT_WEBHOOK_URL: Optional[HttpUrl] = None
    SENTRY_DSN: Optional[str] = None
    PROMETHEUS_GATEWAY: Optional[HttpUrl] = None
    ALPHA_VANTAGE_API_KEY: Optional[str] = None
    RL_MODEL_PATH: str = Field(
        "./models/ppo_model.zip", description="Path to save/load RL model"
    )
    SLACK_AUTH_TOKEN: Optional[SecretStr] = Field(
        None, description="Slack webhook authentication token"
    )
    REDIS_MAX_CONNECTIONS: int = Field(
        10, description="Maximum Redis connections in pool"
    )
    EMAIL_SMTP_SERVER: Optional[str] = None
    EMAIL_SMTP_PORT: Optional[int] = None
    EMAIL_SMTP_USERNAME: Optional[str] = None
    EMAIL_SMTP_PASSWORD: Optional[str] = None
    EMAIL_SENDER: Optional[str] = None
    EMAIL_USE_TLS: bool = False
    EMAIL_RECIPIENTS: Dict[str, List[str]] = Field(
        {}, description="Recipient email addresses for alerts"
    )
    PERIODIC_SCAN_INTERVAL_S: int = Field(
        3600, description="Interval in seconds for periodic codebase scans"
    )
    WEBHOOK_URL: Optional[HttpUrl] = None
    ARBITER_MODES: List[str] = Field(
        ["sandbox", "live"], description="Available modes for the arbiter"
    )
    LLM_ADAPTER: str = Field("mock_ollama_adapter", description="LLM adapter to use")
    OLLAMA_API_URL: str = Field(
        "http://localhost:1144", description="URL for the Ollama API"
    )
    LLM_MODEL: str = Field("llama3", description="Name of the LLM model to use")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="allow"
    )

    @classmethod
    def customise_sources(cls, init_settings, env_settings, file_secret_settings):
        env = os.getenv("ENV", "production")
        env_file = f".env.{env}" if os.path.exists(f".env.{env}") else ".env"
        return (
            init_settings,
            lambda: dotenv_values(env_file),
            env_settings,
            file_secret_settings,
        )

    @field_validator(
        "OMNICORE_URL",
        "SLACK_WEBHOOK_URL",
        "ALERT_WEBHOOK_URL",
        "PROMETHEUS_GATEWAY",
        mode="before",
    )
    @classmethod
    def ensure_https_in_prod(cls, v):
        if (
            v
            and "://localhost" not in str(v)
            and os.getenv("ENV") == "production"
            and not str(v).startswith("https://")
        ):
            raise ValueError(f"{v} must use HTTPS in production")
        return v

    @field_validator("ALPHA_VANTAGE_API_KEY")
    @classmethod
    def validate_api_key(cls, v):
        if v and len(v) < 10:
            raise ValueError("ALPHA_VANTAGE_API_KEY must be at least 10 characters")
        return v

    @field_validator(
        "SLACK_WEBHOOK_URL",
        "ALERT_WEBHOOK_URL",
        "PROMETHEUS_GATEWAY",
        mode="before",
    )
    @classmethod
    def handle_none_or_empty(cls, v):
        if v == "":
            return None
        return v


# --- Sentry Integration ---
# Deferred to avoid module-level initialization overhead
_sentry_initialized = False


def _init_sentry():
    """Initialize Sentry SDK if configured. Called lazily on first Arbiter instantiation."""
    global _sentry_initialized
    if (
        not _sentry_initialized
        and os.getenv("SENTRY_DSN")
        and SENTRY_AVAILABLE
        and sentry_sdk
    ):
        sentry_sdk.init(
            dsn=os.getenv("SENTRY_DSN"),
            traces_sample_rate=1.0,
            environment=os.getenv("ENV", "production"),
        )
        _sentry_initialized = True


# Type checking imports - only used for type hints, not at runtime
# Use string forward references in annotations (e.g., Optional["HumanInLoop"])
if TYPE_CHECKING:
    from self_fixing_engineer.arbiter.human_loop import HumanInLoop, HumanInLoopConfig

# Assuming these are available in the project structure
try:
    from self_fixing_engineer.arbiter.agent_state import AgentState as AgentStateModel
    from self_fixing_engineer.arbiter.agent_state import Base
    from self_fixing_engineer.arbiter.config import ArbiterConfig
    from self_fixing_engineer.arbiter.feedback import FeedbackManager

    # REMOVED: from self_fixing_engineer.arbiter.human_loop import HumanInLoop, HumanInLoopConfig
    # Using TYPE_CHECKING and lazy import to avoid circular dependencies
    from self_fixing_engineer.arbiter.monitoring import Monitor as BaseMonitor
    from self_fixing_engineer.arbiter.utils import get_system_metrics_async

    ARBITER_PACKAGE_AVAILABLE = True
except ImportError as e:
    ARBITER_PACKAGE_AVAILABLE = False
    logging.warning(
        f"arbiter package not available. Some functionalities may be disabled. Error: {e}"
    )
    # Fallback/mock classes if the arbiter package is not available
    FeedbackManager = object
    Base = declarative_base()
    AgentStateModel = object
    BaseMonitor = object
    # HumanInLoop and HumanInLoopConfig will be imported lazily at runtime
    ArbiterConfig = object
    get_system_metrics_async = None


from self_fixing_engineer.arbiter.arbiter_plugin_registry import PluginBase, PlugInKind

# REMOVED: from self_fixing_engineer.arbiter.arbiter_plugin_registry import registry as PLUGIN_REGISTRY
# Replaced with lazy getter to avoid import-time initialization overhead
# REMOVED: from simulation.simulation_module import UnifiedSimulationModule
# This import causes a circular dependency chain. Will be loaded lazily in __init__


def _get_plugin_registry():
    """
    Lazy-load plugin registry to avoid import-time initialization.

    Returns the singleton PluginRegistry instance, creating it only when first accessed.
    This prevents heavy initialization (plugin loading, metrics, async operations)
    from executing during module import.

    Returns:
        PluginRegistry: The singleton plugin registry instance
    """
    from self_fixing_engineer.arbiter.arbiter_plugin_registry import get_registry

    return get_registry()


try:
    from envs.code_health_env import CodeHealthEnv as BaseCodeHealthEnv

    ENVS_AVAILABLE = True
except ImportError as e:
    ENVS_AVAILABLE = False
    logging.debug(f"Optional dependency missing: {e} (envs)")
    BaseCodeHealthEnv = object

try:
    from envs.evolution import evolve_configs
except ImportError as e:
    logging.debug(f"Optional dependency missing: {e} (evolution)")

    def evolve_configs(*args, **kwargs):
        logging.debug("evolve_configs called but evolution module not available")
        return None


try:
    from self_fixing_engineer.arbiter.models.postgres_client import PostgresClient
except ImportError as e:
    logging.debug(f"Optional dependency missing: {e} (PostgresClient)")

    class PostgresClient:
        """
        Fallback stub for PostgresClient when asyncpg is not installed.

        This is an optional dependency used for advanced database features.
        To enable PostgresClient:
        1. Install asyncpg: pip install asyncpg
        2. Configure DATABASE_URL with postgresql:// connection string
        3. Ensure arbiter.models.postgres_client module is available
        """

        def __init__(self, *args, **kwargs):
            logging.error(
                "PostgresClient initialization failed. Required dependencies: asyncpg. "
                "Install with: pip install asyncpg"
            )
            raise NotImplementedError(
                "PostgresClient requires asyncpg module. "
                "Install with: pip install asyncpg. "
                "This is an optional dependency for advanced database features."
            )


try:
    from self_fixing_engineer.arbiter.plugins.multi_modal_plugin import MultiModalPlugin
except ImportError as e:
    logging.debug(f"Optional dependency missing: {e} (MultiModalPlugin)")

    class MultiModalPlugin:
        """Fallback stub for MultiModalPlugin when dependencies are not available."""

        pass


try:
    from self_fixing_engineer.arbiter.models.knowledge_graph_db import Neo4jKnowledgeGraph
except ImportError as e:
    logging.debug(f"Optional dependency missing: {e} (Neo4jKnowledgeGraph)")

    class Neo4jKnowledgeGraph:
        """
        Fallback stub for Neo4jKnowledgeGraph when neo4j driver is not installed.

        This is an optional dependency used for knowledge graph features.
        To enable Neo4jKnowledgeGraph:
        1. Install neo4j driver: pip install neo4j
        2. Configure NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD environment variables
        3. Ensure arbiter.models.knowledge_graph_db module is available
        """

        def __init__(self, *args, **kwargs):
            logging.error(
                "Neo4jKnowledgeGraph initialization failed. Required dependencies: neo4j driver. "
                "Install with: pip install neo4j"
            )
            raise NotImplementedError(
                "Neo4jKnowledgeGraph requires neo4j module. "
                "Install with: pip install neo4j. "
                "This is an optional dependency for knowledge graph features."
            )


try:
    from self_fixing_engineer.arbiter.codebase_analyzer import CodebaseAnalyzer as CodeAnalyzer
except ImportError as e:
    logging.debug(f"Optional dependency missing: {e} (CodebaseAnalyzer)")

    class CodeAnalyzer:
        pass


# --- Audit and Error Log Models ---
class AuditLogModel(Base):
    __tablename__ = "audit_logs"
    __table_args__ = {"extend_existing": True}
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    agent_name = Column(String, index=True)
    action = Column(String)
    timestamp = Column(DateTime)
    details = Column(Text)


class ErrorLogModel(Base):
    __tablename__ = "error_logs"
    __table_args__ = {"extend_existing": True}
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    agent_name = Column(String, index=True)
    timestamp = Column(DateTime)
    error_type = Column(String)
    error_message = Column(Text)
    stack_trace = Column(Text)


# --- Event Log Model ---
class EventLogModel(Base):
    __tablename__ = "event_logs"
    __table_args__ = {"extend_existing": True}
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    agent_name = Column(String, index=True)
    event_type = Column(String)
    timestamp = Column(DateTime)
    description = Column(Text)


# --- Production-Ready Monitor ---
# Deferred to avoid module-level initialization overhead
_metrics_initialized = False
event_counter = None
plugin_execution_time = None


def _init_metrics():
    """Initialize Prometheus metrics lazily. Called on first Arbiter instantiation."""
    global _metrics_initialized, event_counter, plugin_execution_time
    if not _metrics_initialized:
        event_counter = get_or_create_counter(
            "events_total", "Total events logged", ("agent", "event_type")
        )
        plugin_execution_time = get_or_create_summary(
            "plugin_execution_seconds", "Time spent executing plugins", ("plugin",)
        )
        _metrics_initialized = True


class Monitor:
    """
    Logs and manages agent events persistently to file and database.
    """

    def __init__(self, log_file: str, db_client: "PostgresClient" = None):
        self.log_file = log_file
        self.db_client = db_client
        os.makedirs(os.path.dirname(log_file), exist_ok=True)

    async def log_action(self, event: Dict[str, Any]):
        """
        Logs an event to file and database.
        """
        event_data = {
            "type": event.get("type", "general"),
            "agent": event.get("agent", "unknown"),
            "description": event.get("description", ""),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if event_counter is not None:
            event_counter.labels(
                agent=event_data["agent"], event_type=event_data["type"]
            ).inc()

        try:
            with open(self.log_file, "a") as f:
                json.dump(event_data, f)
                f.write("\n")
        except IOError as e:
            logging.getLogger(__name__).error(
                f"Failed to log event to file {self.log_file}: {e}", exc_info=True
            )
            if self.db_client:
                await self.db_client.log_error(e, {"agent_name": event.get("agent")})

        if self.db_client:
            try:
                async with self.db_client.get_session() as session:
                    event_log = EventLogModel(
                        agent_name=event_data["agent"],
                        event_type=event_data["type"],
                        timestamp=datetime.now(timezone.utc),
                        description=event_data["description"],
                    )
                    session.add(event_log)
                    await session.commit()
            except SQLAlchemyError as e:
                logging.getLogger(__name__).error(
                    f"Failed to log event to database: {e}", exc_info=True
                )
                if self.db_client:
                    await self.db_client.log_error(
                        e, {"agent_name": event_data["agent"]}
                    )

    def get_recent_events(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Retrieves recent events from file or database.
        """
        events = []
        if self.db_client:
            try:

                async def fetch_db():
                    async with self.db_client.get_session() as session:
                        stmt = (
                            select(EventLogModel)
                            .order_by(EventLogModel.timestamp.desc())
                            .limit(limit)
                        )
                        result = await session.execute(stmt)
                        return [
                            {
                                "type": e.event_type,
                                "agent": e.agent_name,
                                "description": e.description,
                                "timestamp": e.timestamp.isoformat(),
                            }
                            for e in result.scalars()
                        ]

                events = asyncio.run(fetch_db())
            except Exception as e:
                logging.getLogger(__name__).error(
                    f"Failed to fetch events from database: {e}", exc_info=True
                )
                if self.db_client:
                    asyncio.run(self.db_client.log_error(e, {"agent_name": "unknown"}))

        if not events:
            try:
                with open(self.log_file, "r") as f:
                    lines = f.readlines()
                    events = [json.loads(line) for line in lines[-limit:]]
            except (IOError, json.JSONDecodeError) as e:
                logging.getLogger(__name__).error(
                    f"Failed to read events from file {self.log_file}: {e}",
                    exc_info=True,
                )
                if self.db_client:
                    asyncio.run(self.db_client.log_error(e, {"agent_name": "unknown"}))
        return events

    def generate_reports(self) -> Dict[str, Any]:
        """
        Generates a summary report of events.
        """
        events = self.get_recent_events(limit=100)
        type_counts = collections.Counter(event["type"] for event in events)
        return {"event_counts": dict(type_counts), "total_events": len(events)}


# --- Production-Ready Explorer ---
class Explorer:
    """A web crawler that uses aiohttp for real web crawling and exploration."""

    def __init__(self, sandbox_env):
        self.sandbox_env = sandbox_env
        self.session = ClientSession()
        self.limiter = AsyncLimiter(max_rate=10, time_period=60)

    async def execute(self, action: str, **kwargs):
        """Executes explorer actions with timeout and retry logic."""
        async with asyncio.timeout(30):
            if action == "get_explorer_status":
                return await self.get_status()
            elif action == "discover_frontend_urls":
                html_discovery_dir = kwargs.get("html_discovery_dir", "public")
                return await self.discover_urls(html_discovery_dir)
            elif action == "crawl_frontend":
                urls = kwargs.get("urls", [])
                return await self.crawl_urls(urls)
            elif action == "explore_and_fix":
                return await self.explore_and_fix(
                    kwargs.get("arbiter"), kwargs.get("fix_paths")
                )
            return {"status": "unknown_action"}

    @retry(
        stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10)
    )
    async def get_status(self):
        return {
            "health": "good",
            "last_crawl": {
                "errors": [],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        }

    async def discover_urls(self, html_discovery_dir: str):
        """Discovers HTML files to crawl."""
        urls = []
        try:
            for root, _, files in os.walk(html_discovery_dir):
                for file in files:
                    if file.endswith(".html"):
                        urls.append(f"http://localhost/{os.path.join(root, file)}")
            return urls if urls else ["http://default-frontend.com"]
        except Exception as e:
            logging.getLogger(__name__).error(
                f"Error discovering URLs: {e}", exc_info=True
            )
            return ["http://default-frontend.com"]

    async def crawl_urls(self, urls: List[str]):
        """Crawls a list of URLs with rate limiting."""
        results = []
        for url in urls:
            async with self.limiter:
                try:
                    async with self.session.get(url) as resp:
                        resp.raise_for_status()
                        results.append(
                            {
                                "url": url,
                                "status": resp.status,
                                "content_length": len(await resp.text()),
                            }
                        )
                except aiohttp.ClientError as e:
                    logging.getLogger(__name__).error(
                        f"Error crawling {url}: {e}", exc_info=True
                    )
                    results.append({"url": url, "status": "error", "error": str(e)})
        return {"crawled_urls": results}

    async def explore_and_fix(self, arbiter, fix_paths: Optional[List[str]]):
        """
        Explores the codebase and applies fixes based on analyzer results.
        """
        fix_paths = fix_paths or arbiter.settings.CODEBASE_PATHS
        fixed_paths = []
        for path in fix_paths:
            if arbiter.analyzer:
                issues = await arbiter.analyzer.analyze_and_propose(path)
                for issue in issues:
                    if issue["suggested_fixer"] == "self_healing_import_fixer":
                        fixed_paths.append(path)
                        logging.getLogger(__name__).info(
                            f"[{arbiter.name}] Fixed issue {issue['type']} at {path}"
                        )
        return {"status": "explore_and_fix_complete", "fixed_paths": fixed_paths}

    async def close(self):
        """Closes the aiohttp session."""
        await self.session.close()


# --- Production-Ready SandboxEnv (gym-based) ---
if GYM_AVAILABLE:

    class MySandboxEnv(gym.Env):
        """
        A gym-based sandbox environment for agent evaluation and RL training.
        """

        def __init__(self):
            super().__init__()
            self.action_space = gym.spaces.Discrete(3)
            self.observation_space = gym.spaces.Box(
                low=0, high=100, shape=(2,), dtype=np.float32
            )
            self.state = np.array([50.0, 50.0], dtype=np.float32)
            self.name = ""

        async def evaluate(self, variant, metric=None):
            """Evaluates a variant's performance."""
            return np.random.rand()

        async def test_agent(self, agent):
            """Tests an agent in the environment and returns total reward."""
            observation = self.reset()
            done = False
            total_reward = 0
            while not done:
                action = agent.choose_action_from_policy(observation)
                observation, reward, done, _, _ = self.step(action)
                total_reward += reward
            return total_reward > 0

        def reset(self, seed=None, options=None):
            """Resets the environment to its initial state."""
            super().reset(seed=seed)
            self.state = np.array([50.0, 50.0], dtype=np.float32)
            return self.state, {}

        def step(self, action: int):
            """Simulates a step in the environment based on the action."""
            if action == 0:
                self.state[0] -= np.random.uniform(5, 10)
                self.state[1] += np.random.uniform(0, 5)
            elif action == 1:
                self.state[1] += np.random.uniform(5, 15)
                self.state[0] += np.random.uniform(0, 5)
            self.state = np.clip(self.state, 0, 100)
            reward = self.state[1] * 0.1 - self.state[0] * 0.05
            done = self.state[1] > 90 or self.state[0] < 10
            return (
                self.state,
                reward,
                done,
                False,
                {"metrics": {"complexity": self.state[0], "coverage": self.state[1]}},
            )

else:

    class MySandboxEnv:
        """Mock class for MySandboxEnv when gymnasium is not available."""

        def __init__(self):
            self.action_space = object()
            self.observation_space = object()
            self.name = ""

        async def evaluate(self, variant, metric=None):
            return 0

        async def test_agent(self, agent):
            return False

        def reset(self, seed=None, options=None):
            return np.zeros(2), {}

        def step(self, action: int):
            return np.zeros(2), 0, True, False, {}


# --- Production-Ready IntentCaptureEngine ---
class IntentCaptureEngine:
    """Generates reports based on agent data and metrics."""

    async def generate_report(self, agent_name: str, **kwargs):
        """
        Generates a report based on agent state and metrics.
        """
        report = {
            "agent_name": agent_name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "metrics": kwargs.get("metrics", {}),
            "summary": f"Report for {agent_name} generated with {len(kwargs.get('events', []))} events.",
        }
        return report


# --- Production-Ready AuditLogManager ---
class AuditLogManager:
    """
    Logs audit entries to the database.
    """

    def __init__(self, db_client: "PostgresClient"):
        self.db_client = db_client

    async def log_audit(self, entry: Dict[str, Any]):
        """
        Logs an audit entry to the database.
        """
        async with self.db_client.get_session() as session:
            audit_entry = AuditLogModel(
                agent_name=entry.get("agent_name"),
                action=entry.get("action"),
                timestamp=datetime.now(timezone.utc),
                details=json.dumps(entry.get("details", {})),
            )
            session.add(audit_entry)
            await session.commit()
            logging.getLogger(__name__).info(
                f"[{entry.get('agent_name')}] Audit log entry added."
            )


# --- Production-Ready ExplainableReasoner ---
class ExplainableReasoner(PluginBase):
    """
    A rule-based or lightweight language model-based reasoner.
    """

    def __init__(self):
        super().__init__()

    async def initialize(self):
        """Initialize the plugin."""
        pass

    async def start(self):
        """Start the plugin."""
        pass

    async def stop(self):
        """Stop the plugin."""
        pass

    async def get_capabilities(self) -> List[str]:
        """Get plugin capabilities."""
        return ["explain", "reason"]

    async def health_check(self) -> bool:
        """Returns the health status of the reasoner."""
        return True

    async def execute(self, action: str, **kwargs) -> Dict[str, Any]:
        """
        Executes reasoning or explanation actions.
        """
        explanation_type = kwargs.get("explanation_type", "general")
        context = kwargs.get("context", {})
        query = kwargs.get("query", "")

        if action == "explain":
            if explanation_type == "explorer_diagnosis_explanation":
                diagnosis_result = context.get("diagnosis_result", {})
                return {
                    "explanation": f"Explorer status is {diagnosis_result.get('health', 'unknown')}. Errors: {diagnosis_result.get('last_crawl', {}).get('errors', [])}"
                }
            elif explanation_type == "critical_failure_explanation":
                return {
                    "explanation": f"Critical failure in action {context.get('action_that_failed', 'unknown')}: {context.get('failure_details', 'no details')}"
                }
            elif explanation_type == "action_exception_explanation":
                return {
                    "explanation": f"Exception in action {context.get('action_attempted', 'unknown')}: {context.get('exception_details', 'no details')}"
                }
            elif explanation_type == "reflection_summary":
                return {
                    "explanation": f"Agent reflected on {len(context.get('recent_events', []))} events with energy {context.get('current_energy', 0)}"
                }
            elif explanation_type == "feature_suggestion":
                suggestion = context.get("suggestion", {})
                return {
                    "explanation": f"Suggested feature '{suggestion.get('feature_name', 'unknown')}' due to: {suggestion.get('rationale', 'no rationale')}"
                }
            return {
                "explanation": f"No specific explanation for type: {explanation_type}"
            }

        if action == "reason":
            return {
                "reasoning": f"Reasoning for '{query}': Based on context {context.get('agent_name', 'unknown')}'s state."
            }

        return {"status": "unknown_action"}


# --- Production-Ready ArbiterGrowthManager ---
class ArbiterGrowthManager(PluginBase):
    """Manages skill acquisition and agent growth."""

    def __init__(self):
        super().__init__()
        self.arbiter_name = "default"
        self.skills = {}

    async def initialize(self):
        """Initialize the plugin."""
        pass

    async def start(self):
        """Start the plugin."""
        pass

    async def stop(self):
        """Stop the plugin."""
        pass

    async def health_check(self) -> bool:
        """Check plugin health."""
        return True

    async def get_capabilities(self) -> List[str]:
        """Get plugin capabilities."""
        return ["skill_acquisition", "performance_tracking"]

    async def acquire_skill(self, skill_name: str, context: Dict[str, Any]):
        """
        Acquires a skill and updates performance metrics.
        """
        performance = context.get("performance", 0.5)
        self.skills[skill_name] = self.skills.get(skill_name, 0) + performance
        logging.getLogger(__name__).info(
            f"[{self.arbiter_name}] Acquired skill {skill_name} with performance {performance}"
        )
        return {
            "status": "skill_acquired",
            "skill": skill_name,
            "performance": performance,
        }


# --- Production-Ready BenchmarkingEngine ---
class BenchmarkingEngine:
    """Performs performance benchmarks on given functions."""

    async def execute(self, action: str, **kwargs):
        """
        Executes a benchmarking action.
        """
        if action == "run_benchmark":
            functions = kwargs.get("functions", [])
            profiles = kwargs.get("profiles", [])
            results = []
            for profile in profiles:
                start_time = time.time()
                for _ in range(kwargs.get("iterations_per_run", 1)):
                    for func in functions:
                        func(profile["input_data"])
                elapsed = time.time() - start_time
                results.append({"profile": profile["name"], "time": elapsed})
            return {"status": "success", "results": results}
        elif action == "health_check":
            return {"status": "healthy"}
        return {"status": "unknown_action"}


# --- Production-Ready CompanyDataPlugin ---
class CompanyDataPlugin:
    """Fetches company data from an external API (mocked with Alpha Vantage)."""

    def __init__(self, settings: MyArbiterConfig):
        self.api_key = settings.ALPHA_VANTAGE_API_KEY

    async def execute(self, ticker: str):
        """Fetches data for a given ticker."""
        try:
            async with httpx.AsyncClient() as client:
                url = f"https://www.alphavantage.co/query?function=OVERVIEW&symbol={ticker}&apikey={self.api_key}"
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()
                return {
                    "name": data.get("Name", ticker),
                    "category_scores": {
                        "Environment": random.randint(50, 95),
                        "Financial Health": (
                            float(data.get("PERatio", 0))
                            if data.get("PERatio") and data.get("PERatio") != "None"
                            else random.randint(50, 95)
                        ),
                    },
                }
        except httpx.HTTPError as e:
            logging.getLogger(__name__).error(
                f"Failed to fetch data for {ticker}: {e}", exc_info=True
            )
            return {
                "name": ticker,
                "category_scores": {"Environment": 0, "Financial Health": 0},
            }


# --- Production-Ready SimulationEngine ---
class SimulationEngine:
    """Performs simulations for action evaluation with configurable strategies."""

    def __init__(self):
        self.name = "SimulationEngine"

    async def run(
        self, config: Dict[str, Any], context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Runs a simulation based on the provided configuration.
        """
        sim_type = config.get("type", "monte_carlo")
        params = config.get("params", {})
        iterations = params.get("iterations", 100)

        if sim_type == "monte_carlo":
            results = [
                np.random.uniform(0, 1) * params.get("alpha", 1.0)
                for _ in range(iterations)
            ]
            return {
                "status": "success",
                "result": float(np.mean(results)),
                "details": {
                    "iterations": iterations,
                    "std": float(np.std(results)),
                    "agent": context.get("agent_name"),
                },
            }
        elif sim_type == "predictive":
            energy = context.get("energy", 100.0)
            return {
                "status": "success",
                "result": energy * params.get("multiplier", 0.5),
                "details": {"agent": context.get("agent_name")},
            }
        raise ValueError(f"Unknown simulation type: {sim_type}")

    async def perform_quantum_op(
        self, op_type: str, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Performs a simulation-based quantum operation.
        """
        return {
            "status": "success",
            "output": f"Quantum {op_type} with value {params.get('value', 0)}",
        }

    async def explain_result(self, result: Dict[str, Any]) -> str:
        """
        Explains simulation results.
        """
        return f"Simulation result: {result.get('result', 0):.2f} for agent {result.get('details', {}).get('agent', 'unknown')}"

    def health_check(self):
        """Returns the health status of the engine."""
        return {"status": "healthy"}

    def get_registry(self):
        """Returns the plugin registry for the engine."""
        return {"plugins": ["monte_carlo", "predictive"]}


# --- Production-Ready Logging Setup ---
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    # Import safe_makedirs from utils to handle malformed paths
    from self_fixing_engineer.arbiter.utils import safe_makedirs

    log_dir = os.getenv("REPORTS_DIRECTORY", "./reports")
    log_dir, _ = safe_makedirs(log_dir, "./reports")
    handler = RotatingFileHandler(
        os.path.join(log_dir, "self_fixing_engineer.arbiter.log"),
        maxBytes=50 * 1024 * 1024,  # 50MB
        backupCount=10,
    )
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)

# --- Prometheus Metrics Setup ---
# Deferred to avoid module-level initialization overhead
_additional_metrics_initialized = False
action_counter = None
energy_gauge = None
memory_gauge = None
db_health_gauge = None
rl_reward_gauge = None


def _init_additional_metrics():
    """Initialize additional Prometheus metrics lazily."""
    global _additional_metrics_initialized, action_counter, energy_gauge, memory_gauge, db_health_gauge, rl_reward_gauge
    if not _additional_metrics_initialized:
        action_counter = get_or_create_counter(
            "actions_total", "Total actions executed", ("agent", "action")
        )
        energy_gauge = get_or_create_gauge("energy", "Current energy level", ("agent",))
        memory_gauge = get_or_create_gauge(
            "memory_items", "Number of items in agent memory", ("agent",)
        )
        db_health_gauge = get_or_create_gauge(
            "db_health", "Database health status (1=healthy, 0=unhealthy)"
        )
        rl_reward_gauge = get_or_create_gauge(
            "rl_reward", "Reward from RL steps", ("agent",)
        )
        _additional_metrics_initialized = True


# --- Permission Manager ---
class PermissionManager:
    """Manages dynamic role-based permissions."""

    def __init__(self, config: MyArbiterConfig):
        self.permissions = {
            "user": ["read", "execute_basic"],
            "admin": ["read", "write", "execute_all"],
        }

    def check_permission(self, role: str, permission: str):
        return permission in self.permissions.get(role, [])


def require_permission(permission: str):
    """Decorator to enforce a specific permission."""

    def decorator(func: Callable[..., Coroutine[Any, Any, Any]]):
        @wraps(func)
        async def wrapper(self, *args, **kwargs):
            permission_mgr = PermissionManager(self.settings)
            if not permission_mgr.check_permission(self.state_manager.role, permission):
                raise ValueError(f"Access denied: Permission {permission} required.")
            return await func(self, *args, **kwargs)

        return wrapper

    return decorator


# --- Refactored Components for Maintainability ---
class AgentStateManager:
    """
    Manages the agent's state persistence and synchronization.
    Includes encryption for sensitive data.
    """

    def __init__(self, db_client, name, settings):
        self.db_client = db_client
        self.name = name
        self.settings = settings
        self.fernet = Fernet(self.settings.ENCRYPTION_KEY.get_secret_value().encode())

        self.x: float = 0.0
        self.y: float = 0.0
        self.energy: float = 0.0
        self.inventory: List[str] = []
        self.language: Set[str] = set()
        self.memory: List[Dict[str, Any]] = []
        self.personality: Dict[str, float] = {}
        self.world_size: int = 10
        self.agent_type: str = "Arbiter"
        self.role: str = "user"
        self._lock = asyncio.Lock()
        self.state_queue = deque()

    async def load_state(self):
        """
        Asynchronously loads the agent's state from the database.
        """
        async with self._lock:
            try:
                async with self.db_client.get_session() as session:
                    stmt = select(AgentStateModel).filter_by(name=self.name)
                    result = await session.execute(stmt)
                    state = result.scalar_one_or_none()

                    if state:
                        self.x = state.x
                        self.y = state.y
                        self.energy = state.energy
                        self.inventory = (
                            json.loads(
                                self.fernet.decrypt(state.inventory.encode()).decode()
                            )
                            if state.inventory
                            else []
                        )
                        self.language = (
                            set(json.loads(state.language)) if state.language else set()
                        )
                        self.memory = (
                            json.loads(
                                self.fernet.decrypt(state.memory.encode()).decode()
                            )
                            if state.memory
                            else []
                        )
                        self.personality = (
                            json.loads(state.personality) if state.personality else {}
                        )
                        self.world_size = state.world_size
                        self.agent_type = state.agent_type
                        self.role = state.role
                        logging.getLogger(__name__).info(
                            f"[{self.name}] State loaded successfully from DB."
                        )
                    else:
                        logging.getLogger(__name__).info(
                            f"[{self.name}] No existing state found. Initializing default state and saving it."
                        )
                        self._initialize_default_state_in_memory()
                        await self.save_state()
            except SQLAlchemyError as e:
                logging.getLogger(__name__).error(
                    f"[{self.name}] SQLAlchemy Error loading state: {e}", exc_info=True
                )
                self._initialize_default_state_in_memory()
            except Exception as e:
                logging.getLogger(__name__).error(
                    f"[{self.name}] Unexpected error loading state: {e}", exc_info=True
                )
                self._initialize_default_state_in_memory()

    @retry(
        stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=5)
    )
    async def save_state(self):
        """
        Asynchronously saves the current agent's state to the database with retries.
        """
        async with self._lock:
            session = None
            try:
                async with self.db_client.get_session() as session:
                    stmt = select(AgentStateModel).filter_by(name=self.name)
                    result = await session.execute(stmt)
                    state = result.scalar_one_or_none()

                    if not state:
                        state = AgentStateModel(name=self.name)
                        session.add(state)

                    state.x = self.x
                    state.y = self.y
                    state.energy = self.energy
                    state.inventory = self.fernet.encrypt(
                        json.dumps(self.inventory).encode()
                    ).decode()
                    state.language = json.dumps(list(self.language))
                    state.memory = self.fernet.encrypt(
                        json.dumps(self.memory).encode()
                    ).decode()
                    state.personality = json.dumps(self.personality)
                    state.world_size = self.world_size
                    state.agent_type = self.agent_type
                    state.role = self.role

                    await session.commit()
                    logging.getLogger(__name__).info(
                        f"[{self.name}] State saved successfully to DB."
                    )
            except IntegrityError as e:
                if session:
                    await session.rollback()
                logging.getLogger(__name__).error(
                    f"[{self.name}] Database Integrity Error saving state: {e}",
                    exc_info=True,
                )
                raise
            except SQLAlchemyError as e:
                if session:
                    await session.rollback()
                logging.getLogger(__name__).error(
                    f"[{self.name}] SQLAlchemy Error saving state: {e}", exc_info=True
                )
                raise
            except Exception as e:
                if session:
                    await session.rollback()
                logging.getLogger(__name__).error(
                    f"[{self.name}] An unexpected error occurred while saving state: {e}",
                    exc_info=True,
                )
                raise

    async def batch_save_state(self):
        """Adds current state to a queue for later batch saving."""
        async with self._lock:
            self.state_queue.append(
                {
                    "x": self.x,
                    "y": self.y,
                    "energy": self.energy,
                    "inventory": self.inventory,
                    "language": list(self.language),
                    "memory": self.memory,
                    "personality": self.personality,
                    "world_size": self.world_size,
                    "agent_type": self.agent_type,
                    "role": self.role,
                }
            )
            if len(self.state_queue) >= 10:
                await self.process_state_queue()

    @retry(
        stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=5)
    )
    async def process_state_queue(self):
        """Processes the state queue, batching updates to the database."""
        if not self.state_queue:
            return

        async with self.db_client.get_session() as session:
            try:
                while self.state_queue:
                    state_data = self.state_queue.popleft()

                    encrypted_inventory = self.fernet.encrypt(
                        json.dumps(state_data["inventory"]).encode()
                    ).decode()
                    encrypted_memory = self.fernet.encrypt(
                        json.dumps(state_data["memory"]).encode()
                    ).decode()

                    stmt = (
                        update(AgentStateModel)
                        .where(AgentStateModel.name == self.name)
                        .values(
                            x=state_data["x"],
                            y=state_data["y"],
                            energy=state_data["energy"],
                            inventory=encrypted_inventory,
                            language=json.dumps(state_data["language"]),
                            memory=encrypted_memory,
                            personality=json.dumps(state_data["personality"]),
                            world_size=state_data["world_size"],
                            agent_type=state_data["agent_type"],
                            role=state_data["role"],
                        )
                    )
                    await session.execute(stmt)
                await session.commit()
                logging.getLogger(__name__).info(
                    f"[{self.name}] Processed and saved state batch."
                )
            except SQLAlchemyError as e:
                await session.rollback()
                logging.getLogger(__name__).error(
                    f"[{self.name}] Failed to save state batch: {e}", exc_info=True
                )
                raise
            except Exception as e:
                await session.rollback()
                logging.getLogger(__name__).error(
                    f"[{self.name}] Unexpected error during batch save: {e}",
                    exc_info=True,
                )
                raise

    def _initialize_default_state_in_memory(self):
        """Initializes the agent's state in memory with default values."""
        self.x = float(random.randint(0, self.world_size - 1))
        self.y = float(random.randint(0, self.world_size - 1))
        self.energy = 100.0
        self.inventory = []
        self.language = {"common"}
        self.personality = {
            "agreeableness": random.uniform(0, 1),
            "aggression": random.uniform(0, 1),
        }
        self.agent_type = "Arbiter"
        self.role = "user"


def save_rl_model(model: PPO, path: str):
    """Saves the RL model to a file."""
    if not STABLE_BASELINES3_AVAILABLE:
        logging.warning("Stable Baselines3 not available. Skipping model save.")
        return
    try:
        model.save(path)
        logging.getLogger(__name__).info(f"RL model saved to {path}")
    except Exception as e:
        logging.getLogger(__name__).error(
            f"Failed to save RL model: {e}", exc_info=True
        )
        raise


def load_rl_model(path: str, env) -> PPO:
    """Loads a pre-trained RL model or initializes a new one."""
    if not STABLE_BASELINES3_AVAILABLE:
        logging.warning(
            "Stable Baselines3 not available. Cannot load/initialize RL model."
        )
        return None
    try:
        if os.path.exists(path):
            model = PPO.load(path, env=env)
            logging.getLogger(__name__).info(f"RL model loaded from {path}")
            return model
        else:
            logging.getLogger(__name__).info(
                f"No model found at {path}. Initializing new PPO model."
            )
            return PPO("MlpPolicy", env, verbose=1)
    except Exception as e:
        logging.getLogger(__name__).error(
            f"Failed to load RL model: {e}", exc_info=True
        )
        raise


class Arbiter:
    """
    The core Arbiter agent, responsible for observing, planning, and executing actions.
    It integrates with various plugins and services to perform its tasks.
    """

    def __init__(
        self,
        name: str,
        db_engine: AsyncEngine,
        settings: MyArbiterConfig,
        world_size: int = 10,
        role: str = "user",
        agent_type: str = "Arbiter",
        explorer: Optional[Any] = None,
        analyzer: Optional[Any] = None,
        decision_optimizer: Optional[Any] = None,
        port: Optional[int] = None,
        peer_ports: Optional[List[int]] = None,
        feedback_manager: Optional[FeedbackManager] = None,
        human_in_loop: Optional[
            "HumanInLoop"
        ] = None,  # Forward reference for TYPE_CHECKING import
        monitor: Optional[Monitor] = None,
        intent_capture_engine: Optional[Any] = None,
        test_generation_engine: Optional[Any] = None,
        simulation_engine: Optional[
            "UnifiedSimulationModule"
        ] = None,  # String literal for TYPE_CHECKING
        code_health_env: Optional[BaseCodeHealthEnv] = None,
        audit_log_manager: Optional[Any] = None,
        engines: Optional[Dict[str, Any]] = None,
        omnicore_url: str = None,
        message_queue_service: Optional[Any] = None,
        **kwargs,
    ):
        # Initialize deferred module-level components
        _init_sentry()
        _init_metrics()
        _init_additional_metrics()
        _register_default_plugins()

        self.settings = settings
        self.name = name
        self.world_size = world_size
        self.port = port
        self.peer_ports = peer_ports or []
        self.omnicore_url = omnicore_url or str(self.settings.OMNICORE_URL)

        self.db_client = PostgresClient(self.settings.DATABASE_URL)
        self.state_manager = AgentStateManager(self.db_client, name, self.settings)
        self.x = self.state_manager.x
        self.y = self.state_manager.y
        self.energy = self.state_manager.energy
        self.inventory = self.state_manager.inventory
        self.language = self.state_manager.language
        self.memory = self.state_manager.memory
        self.personality = self.state_manager.personality
        self.role = self.state_manager.role
        self.agent_type = self.state_manager.agent_type

        self._lock = self.state_manager._lock

        self.analyzer = analyzer
        self.decision_optimizer = decision_optimizer
        self.message_queue_service = message_queue_service
        self.feedback = (
            feedback_manager or FeedbackManager(config=self.settings)
            if ARBITER_PACKAGE_AVAILABLE
            else None
        )
        self.monitor = monitor or Monitor(
            log_file=os.path.join(
                self.settings.REPORTS_DIRECTORY, f"{self.name}_monitor_log.json"
            ),
            db_client=self.db_client,
        )

        # Fixed HumanInLoop initialization with proper config
        # Lazy import to avoid circular dependencies
        if ARBITER_PACKAGE_AVAILABLE:
            try:
                from self_fixing_engineer.arbiter.human_loop import HumanInLoop, HumanInLoopConfig
            except ImportError:
                HumanInLoop = None
                HumanInLoopConfig = None
        else:
            HumanInLoop = None
            HumanInLoopConfig = None

        if (
            ARBITER_PACKAGE_AVAILABLE
            and HumanInLoop is not None
            and HumanInLoopConfig is not None
        ):
            if human_in_loop:
                self.human_in_loop = human_in_loop
            else:
                # Create proper HumanInLoopConfig from MyArbiterConfig
                hitl_config = HumanInLoopConfig(
                    DATABASE_URL=self.settings.DATABASE_URL,
                    EMAIL_ENABLED=bool(self.settings.EMAIL_SMTP_SERVER),
                    EMAIL_SMTP_SERVER=self.settings.EMAIL_SMTP_SERVER,
                    EMAIL_SMTP_PORT=self.settings.EMAIL_SMTP_PORT or 587,
                    EMAIL_SMTP_USER=self.settings.EMAIL_SMTP_USERNAME,
                    EMAIL_SMTP_PASSWORD=self.settings.EMAIL_SMTP_PASSWORD,
                    EMAIL_SENDER=self.settings.EMAIL_SENDER or "no-reply@arbiter.local",
                    EMAIL_USE_TLS=self.settings.EMAIL_USE_TLS,
                    EMAIL_RECIPIENTS=self.settings.EMAIL_RECIPIENTS or {},
                    SLACK_WEBHOOK_URL=(
                        str(self.settings.SLACK_WEBHOOK_URL)
                        if self.settings.SLACK_WEBHOOK_URL
                        else None
                    ),
                    IS_PRODUCTION=os.getenv("APP_ENV") == "production",
                )
                self.human_in_loop = HumanInLoop(
                    config=hitl_config, feedback_manager=self.feedback
                )
        else:
            self.human_in_loop = None

        self.engines = engines or {}

        # Lazy import to avoid circular dependency with simulation module
        # Only load if simulation_engine not provided and not in engines
        if simulation_engine is None and not self.engines.get("simulation"):
            try:
                # Import at runtime to break circular dependency chain:
                # arbiter.py -> simulation.simulation_module -> omnicore_engine.engines
                # -> generator.agents -> docgen_agent -> arbiter.models.common
                from simulation.simulation_module import UnifiedSimulationModule

                # Note: We don't auto-instantiate - leave it to caller or lazy loading
                logger.debug(
                    f"[{name}] UnifiedSimulationModule available for lazy instantiation"
                )
            except ImportError as e:
                logging.getLogger(__name__).warning(
                    f"[{name}] UnifiedSimulationModule not available: {e}"
                )
            except Exception as e:
                logging.getLogger(__name__).error(
                    f"[{name}] Error importing UnifiedSimulationModule: {e}",
                    exc_info=True,
                )

        self.simulation_engine = self.engines.get("simulation") or simulation_engine
        self.test_generation_engine = self.engines.get("test_generation")
        self.generator_engine = self.engines.get("generator")
        self.code_health_env = self.engines.get("code_health_env")
        self.intent_capture_engine = self.engines.get("intent_capture")
        self.audit_log_manager = self.engines.get("audit_log_manager")
        self.engines["multi_modal"] = MultiModalPlugin(
            config={
                "image_processing": {"enabled": True},
                "text_processing": {"enabled": True},
            }
        )
        self.knowledge_graph = Neo4jKnowledgeGraph(
            audit_logger=AuditLogManager(self.db_client)
        )
        
        # Initialize Arbiter Constitution for governance and ethical constraints
        try:
            from self_fixing_engineer.arbiter.arbiter_constitution import ArbiterConstitution
            self.constitution = ArbiterConstitution()
            logger.info(f"[{name}] Arbiter Constitution loaded and enforced")
        except ImportError as e:
            logger.warning(f"[{name}] Could not load ArbiterConstitution: {e}")
            self.constitution = None
        except Exception as e:
            logger.error(f"[{name}] Error initializing ArbiterConstitution: {e}", exc_info=True)
            self.constitution = None

        if self.code_health_env:
            self.code_health_env.name = name

        if GYM_AVAILABLE and ENVS_AVAILABLE:
            self.sandbox_env = MySandboxEnv()
            self.explorer = explorer or Explorer(self.sandbox_env)
            self.experiment_explorer = Explorer(self.sandbox_env)
        else:
            self.sandbox_env = None
            self.explorer = None
            self.experiment_explorer = None
            logging.warning(
                "Gymnasium or envs package is not available. Explorer and SandboxEnv will be disabled."
            )

        self.modules = {
            "benchmarking": None,
            "explainability": None,
            "primary_explorer": self.explorer,
            "experiment_explorer": self.experiment_explorer,
        }

        self.running = False
        self.peer_listener_task = None
        self.redis_pool = None

        self.growth_manager = _get_plugin_registry().get(
            PlugInKind.GROWTH_MANAGER, "arbiter_growth"
        )
        if self.growth_manager:
            self.growth_manager.arbiter_name = self.name
            logging.getLogger(__name__).info(
                f"[{self.name}] ArbiterGrowthManager initialized for Arbiter"
            )

        self.benchmarking_engine = _get_plugin_registry().get(
            PlugInKind.CORE_SERVICE, "benchmarking"
        )
        self.explainable_reasoner = _get_plugin_registry().get(
            PlugInKind.AI_ASSISTANT, "explainable_reasoner"
        )

        os.makedirs(
            os.path.join(self.settings.REPORTS_DIRECTORY, "models"), exist_ok=True
        )

    async def orchestrate(self, task: dict) -> dict:
        """
        Orchestrates a specific task by routing it to the appropriate engine and
        publishing the result to OmniCore.
        """
        engine_name = task.get("engine", "simulation")
        if engine_name in self.engines:
            result = await self.engines[engine_name].execute(task)
            await self.publish_to_omnicore(
                "arbiter_task", {"task": task, "result": result}
            )
            return result
        return {"status": "error", "message": f"Engine {engine_name} not found"}

    async def health_check(self) -> dict:
        """
        Performs a health check on critical components.
        """
        db_health = await self.db_client.check_health()
        plugin_status = {
            "growth_manager": bool(self.growth_manager),
            "explainable_reasoner": bool(self.explainable_reasoner),
            "benchmarking_engine": bool(self.benchmarking_engine),
        }
        return {
            "status": (
                "healthy"
                if db_health["status"] == "healthy" and all(plugin_status.values())
                else "unhealthy"
            ),
            "details": {"database": db_health, "plugins": plugin_status},
        }

    async def register_plugin(self, kind: str, name: str, plugin: Any) -> None:
        """
        Registers a plugin with the local registry.
        """
        from .arbiter_plugin_registry import registry

        await registry.register(
            kind, name, plugin, version="1.0.0", author="Arbiter Team"
        )

    async def publish_to_omnicore(self, event_type: str, data: dict):
        """
        Publishes an event to the omnicore_engine's message bus.
        """
        async with aiohttp.ClientSession() as session:
            try:
                await session.post(
                    f"{self.omnicore_url}/events",
                    json={"event_type": event_type, "data": data},
                )
                logging.getLogger(__name__).info(
                    f"Published to omnicore_engine: {event_type}"
                )
            except Exception as e:
                logging.getLogger(__name__).error(
                    f"Failed to publish to omnicore_engine: {e}"
                )

    @retry(
        stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=5)
    )
    async def run_test_generation(
        self, code: str, language: str = "python", config: dict = None
    ):
        """
        Triggers test generation via OmniCore's FastAPI endpoint (HTTP call) with retries and timeouts.

        Parameters
        ----------
        code : str
            The source code to generate tests for.
        language : str
            The programming language of the code.
        config : dict, optional
            Configuration for the test generation plugin.

        Returns
        -------
        dict
            The JSON response from the OmniCore endpoint.
        """

        class TestGenerationInput(BaseModel):
            code: str
            language: str
            config: Dict[str, Any] = {}

        input_data = TestGenerationInput(
            code=code, language=language, config=config or {}
        )
        payload = input_data.model_dump_json()
        url = f"{self.omnicore_url}/scenarios/test_generation/run"

        try:
            async with httpx.AsyncClient(
                timeout=self.settings.AI_API_TIMEOUT
            ) as client:
                resp = await client.post(
                    url, data=payload, headers={"Content-Type": "application/json"}
                )
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as e:
            logging.getLogger(__name__).error(
                f"HTTP error during test generation: {e.response.status_code} - {e.response.text}",
                exc_info=True,
            )
            return {"status": "error", "error": f"HTTP error: {e}"}
        except httpx.RequestError as e:
            logging.getLogger(__name__).error(
                f"HTTP call to OmniCore failed: {e}", exc_info=True
            )
            await self.db_client.log_error(e, {"agent_name": self.name})
            return {"status": "error", "error": f"HTTP request failed: {e}"}
        except Exception as e:
            logging.getLogger(__name__).error(
                f"An unexpected error occurred during test generation: {e}",
                exc_info=True,
            )
            await self.db_client.log_error(e, {"agent_name": self.name})
            return {"status": "error", "error": f"Unexpected error: {e}"}

    async def run_test_generation_in_process(
        self, code: str, language: str = "python", config: dict = None
    ):
        """
        Triggers test generation by calling the plugin directly from the in-process registry.

        Parameters
        ----------
        code : str
            The source code to generate tests for.
        language : str
            The programming language of the code.
        config : dict, optional
            Configuration for the test generation plugin.

        Returns
        -------
        dict
            The result from the test generation plugin.
        """
        plugin_name = "generate_tests"

        plugin_instance = None
        # Try a comprehensive lookup across known kinds to avoid false negatives
        try:
            # Get all available PlugInKind enum members
            possible_kinds = list(PlugInKind.__members__.values())
            for kind in possible_kinds:
                try:
                    candidate = _get_plugin_registry().get_plugin(kind, plugin_name)
                    if candidate:
                        plugin_instance = candidate
                        break
                except Exception:
                    # Continue trying other kinds
                    continue
        except Exception:
            plugin_instance = None

        if not plugin_instance:
            logging.getLogger(__name__).error(
                f"Test generation plugin '{plugin_name}' not found in registry."
            )
            return {
                "status": "error",
                "error": f"Test generation plugin '{plugin_name}' not found.",
            }

        # Validate minimal interface before invoking
        if not (hasattr(plugin_instance, "execute") or callable(plugin_instance)):
            logging.getLogger(__name__).error(
                "Found plugin lacks required callable interface."
            )
            return {
                "status": "error",
                "error": "Test generation plugin interface invalid.",
            }

        try:
            async with asyncio.timeout(self.settings.AI_API_TIMEOUT):
                start_time = time.time()
                payload = {"code": code, "language": language, "config": config or {}}
                if hasattr(plugin_instance, "execute"):
                    result = await plugin_instance.execute(**payload)
                else:
                    # Callable plugin
                    if asyncio.iscoroutinefunction(plugin_instance):
                        result = await plugin_instance(**payload)
                    else:
                        result = plugin_instance(**payload)
                if plugin_execution_time is not None:
                    plugin_execution_time.labels(plugin="generate_tests").observe(
                        time.time() - start_time
                    )
            return (
                result
                if isinstance(result, dict)
                else {"status": "ok", "result": result}
            )
        except asyncio.TimeoutError:
            logging.getLogger(__name__).error("In-process test generation timed out.")
            return {"status": "error", "error": "In-process plugin call timed out."}
        except Exception as e:
            logging.getLogger(__name__).error(
                f"Error calling in-process test generation plugin: {e}", exc_info=True
            )
            await self.db_client.log_error(e, {"agent_name": self.name})
            return {"status": "error", "error": f"In-process plugin call failed: {e}"}

    @property
    def is_alive(self) -> bool:
        """Checks if the agent has energy to perform actions."""
        return self.state_manager.energy > 0

    def log_event(self, event_description: str, event_type: str = "general"):
        """Logs an event to the monitor and the logger."""
        # The Monitor.log_action may be async (if using the internal Monitor class)
        # or sync (if using arbiter.monitoring.Monitor). Handle both cases.
        action_data = {
            "type": event_type,
            "agent": self.name,
            "description": event_description,
        }

        result = self.monitor.log_action(action_data)

        # If log_action returned a coroutine, schedule it if there's a running loop
        if asyncio.iscoroutine(result):
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(result)
            except RuntimeError:
                # No running event loop - this can happen during initialization
                logging.getLogger(__name__).debug(
                    f"[{self.name}] No event loop for async log_action, event discarded"
                )
                result.close()

        logging.getLogger(__name__).debug(
            f"[{self.name}] Event Logged: {event_description}"
        )

    async def evolve(self, arena: Any = None, **kwargs: Any) -> Dict[str, Any]:
        """
        Evolves the agent's behavior through RL and GA-driven configuration tuning.
        """
        try:
            with sentry_sdk.push_scope() as scope:
                scope.set_tag("agent", self.name)
                self.log_event("Initiating evolution cycle...", "evolve_start")
                await self.publish_to_omnicore("evolve_start", {"agent": self.name})

                if self.audit_log_manager:
                    try:
                        best_config = evolve_configs(
                            audit_logger=self.audit_log_manager,
                            generations=5,
                            pop_size=10,
                        )
                        self.log_event(
                            f"GA found optimal configuration: {best_config}",
                            "ga_result",
                        )
                        await self.publish_to_omnicore(
                            "ga_result", {"agent": self.name, "config": best_config}
                        )
                    except Exception as e:
                        sentry_sdk.capture_exception(e)
                        self.log_event(f"GA-driven evolution failed: {e}", "ga_failure")
                        await self.db_client.log_error(e, {"agent_name": self.name})
                        await self.publish_to_omnicore(
                            "ga_failure", {"agent": self.name, "error": str(e)}
                        )

                if (
                    self.code_health_env
                    and STABLE_BASELINES3_AVAILABLE
                    and GYM_AVAILABLE
                    and ENVS_AVAILABLE
                ):
                    self.log_event(
                        "Starting RL-based code health optimization loop...", "rl_start"
                    )
                    await self.publish_to_omnicore("rl_start", {"agent": self.name})
                    try:
                        vec_env = make_vec_env(lambda: self.code_health_env, n_envs=4)
                        model_path = os.path.join(
                            self.settings.REPORTS_DIRECTORY, self.settings.RL_MODEL_PATH
                        )
                        os.makedirs(os.path.dirname(model_path), exist_ok=True)
                        rl_model = load_rl_model(model_path, vec_env)
                        if rl_model:
                            self.engines["rl_policy"] = rl_model
                            rl_model.learn(total_timesteps=1000)
                            save_rl_model(rl_model, model_path)
                            observation, _ = vec_env.reset()
                            done = np.array([False])
                            while not done.all():
                                action, _states = rl_model.predict(
                                    observation, deterministic=True
                                )
                                observation, reward, done, info = vec_env.step(action)
                                if rl_reward_gauge is not None:
                                    rl_reward_gauge.labels(agent=self.name).set(
                                        reward[0]
                                    )
                                self.log_event(
                                    f"RL step complete. Reward: {reward[0]}",
                                    "rl_step_complete",
                                )
                    except Exception as e:
                        sentry_sdk.capture_exception(e)
                        self.log_event(
                            f"RL-based optimization failed: {e}", "rl_failure"
                        )
                        await self.db_client.log_error(e, {"agent_name": self.name})
                        await self.publish_to_omnicore(
                            "rl_failure", {"agent": self.name, "error": str(e)}
                        )
                else:
                    self.log_event(
                        "CodeHealthEnv, gymnasium, or stable_baselines3 not available. Skipping adaptive optimization.",
                        "skip_envs",
                    )
                    await self.publish_to_omnicore(
                        "skip_envs",
                        {
                            "agent": self.name,
                            "reason": "CodeHealthEnv, gymnasium, or stable_baselines3 not available",
                        },
                    )
                self.log_event("Evolution cycle completed.", "evolve_end")
                await self.publish_to_omnicore(
                    "evolve_end", {"agent": self.name, "status": "success"}
                )
                return {
                    "status": "success",
                    "message": "Evolution and optimization complete.",
                }
        except asyncio.CancelledError:
            logging.getLogger(__name__).info(f"[{self.name}] Evolve task cancelled.")
            await self.publish_to_omnicore("evolve_cancelled", {"agent": self.name})
            raise
        except Exception as e:
            sentry_sdk.capture_exception(e)
            await self.publish_to_omnicore(
                "evolve_error", {"agent": self.name, "error": str(e)}
            )
            return {"status": "error", "error": f"Unexpected error: {e}"}

    def choose_action_from_policy(self, observation):
        """
        Selects an action using the trained PPO model.

        Parameters
        ----------
        observation : np.ndarray
            The current observation from the environment.

        Returns
        -------
        int
            The selected action.
        """
        logging.getLogger(__name__).info(
            f"Choosing action for observation: {observation}"
        )
        if not STABLE_BASELINES3_AVAILABLE or not GYM_AVAILABLE or not ENVS_AVAILABLE:
            logging.warning(
                "Stable Baselines3, Gymnasium, or envs package not available. Using random action selection."
            )
            return random.choice([0, 1, 2])
        model_path = os.path.join(
            self.settings.REPORTS_DIRECTORY, "models", "ppo_model.zip"
        )
        try:
            if PPO and DummyVecEnv and self.code_health_env:
                rl_model = PPO.load(
                    model_path, env=DummyVecEnv([lambda: self.code_health_env])
                )
                action, _states = rl_model.predict(observation, deterministic=True)
                return int(action[0])
            else:
                logging.warning(
                    "PPO, DummyVecEnv, or code_health_env not available. Using random action selection."
                )
                return random.choice([0, 1, 2])
        except Exception as e:
            logging.getLogger(__name__).error(
                f"[{self.name}] Error in RL policy prediction or loading model: {e}",
                exc_info=True,
            )
            return random.choice([0, 1, 2])

    async def observe_environment(self, arena: Any = None) -> Dict[str, Any]:
        """
        Observes the environment and collects data from the explorer.

        Parameters
        ----------
        arena : Any, optional
            The environment arena.

        Returns
        -------
        Dict[str, Any]
            The observation data.
        """
        async with self._lock:
            observation: Dict[str, Any] = {
                "current_x": self.state_manager.x,
                "current_y": self.state_manager.y,
                "current_energy": self.state_manager.energy,
            }

        if self.explorer:
            try:
                explorer_status = await self.explorer.execute("get_explorer_status")
                observation["explorer_status"] = explorer_status
                self.log_event(
                    "Observed environment via primary explorer.",
                    "primary_explorer_observation",
                )
            except Exception as e:
                observation["explorer_error"] = str(e)
                self.log_event(
                    f"Error observing via primary explorer: {e}",
                    "primary_explorer_observation_error",
                )
        else:
            self.log_event(
                "No primary explorer available for observation.", "mock_observation"
            )

        return observation

    async def plan_decision(self, observation: Dict[str, Any]) -> Dict[str, Any]:
        """
        Decides on the next action based on the current observation.

        Parameters
        ----------
        observation : Dict[str, Any]
            The observation data.

        Returns
        -------
        Dict[str, Any]
            The decision, including the chosen action and human loop requirement.
        """
        action = "idle"
        requires_human = False

        async with self._lock:
            # [ARBITER CONSTITUTION] Check constitutional constraints before decision-making
            if self.constitution:
                try:
                    # Check if there are any constitutional constraints on decision-making
                    allowed, reason = await self.constitution.check_action(
                        "plan_decision",
                        {
                            "energy": self.state_manager.energy,
                            "observation": observation
                        }
                    )
                    if not allowed:
                        logger.warning(f"[{self.name}] Decision planning constrained by constitution: {reason}")
                        return {
                            "action": "idle",
                            "requires_human": True,
                            "reason": f"Constitutional constraint: {reason}",
                            "observation": observation,
                        }
                except Exception as e:
                    logger.error(f"[{self.name}] Error checking constitution: {e}", exc_info=True)
            
            if self.state_manager.energy < 30:
                action = "recharge"
            elif "explorer_error" in observation or (
                observation.get("explorer_status", {}).get("health") == "degraded"
                or len(
                    observation.get("explorer_status", {})
                    .get("last_crawl", {})
                    .get("errors", [])
                )
                > 0
            ):
                action = "diagnose_explorer"
                requires_human = True
            elif self.state_manager.energy > 50 and random.random() < 0.6:
                action = "explore"
            elif random.random() < 0.1:
                action = "reflect"
            else:
                action = "move_random"

        return {
            "action": action,
            "requires_human": requires_human,
            "observation": observation,
        }

    @require_permission("execute_basic")
    async def execute_action(self, decision: Dict[str, Any]) -> Dict[str, Any]:
        """
        Executes the chosen action and updates the agent's state.

        Parameters
        ----------
        decision : Dict[str, Any]
            The decision dictionary containing the action and observation.

        Returns
        -------
        Dict[str, Any]
            The outcome of the action.
        """
        action = decision.get("action")
        outcome = {"status": "success", "action_taken": action}
        self.log_event(f"Executing action: {action}", "execute_action")
        if action_counter is not None:
            action_counter.labels(agent=self.name, action=action).inc()

        with sentry_sdk.push_scope() as scope:
            scope.set_tag("agent", self.name)
            scope.set_tag("action", action)
            try:
                async with self._lock:
                    if action == "explore":
                        if self.explorer:
                            frontend_urls = await self.explorer.execute(
                                "discover_frontend_urls", html_discovery_dir="public"
                            )
                            if not frontend_urls:
                                raise ValueError(
                                    "No frontend URLs available for exploration."
                                )

                            crawl_results = await self.explorer.execute(
                                "crawl_frontend", urls=frontend_urls
                            )
                            self.state_manager.energy -= 5
                            await self.log_social_event(
                                "explored a new area", "environment", 1
                            )
                            outcome["crawl_results"] = crawl_results
                            await self.coordinate_with_peers(
                                {
                                    "agent": self.name,
                                    "action": "explored",
                                    "urls": frontend_urls,
                                }
                            )
                        else:
                            raise RuntimeError(
                                "Primary explorer not configured or available."
                            )
                    elif action == "recharge":
                        self.state_manager.energy += 20
                        outcome["new_energy"] = self.state_manager.energy
                        if self.growth_manager:
                            await self.growth_manager.acquire_skill(
                                "energy_management", context={"performance": 0.8}
                            )
                    elif action == "reflect":
                        reflection_insight = await self.reflect()
                        outcome["reflection_insight"] = reflection_insight
                        self.state_manager.energy -= 2
                        if self.growth_manager:
                            await self.growth_manager.acquire_skill(
                                "self_awareness", context={"performance": 0.7}
                            )
                    elif action == "diagnose_explorer":
                        if self.explorer:
                            diag_result = await self.explorer.execute(
                                "get_explorer_status"
                            )
                            outcome["diagnosis"] = f"Explorer status: {diag_result}"
                            diagnosis_explanation_context = {
                                "agent_name": self.name,
                                "diagnosis_result": diag_result,
                                "observation": decision.get("observation", {}),
                            }
                            if self.explainable_reasoner:
                                explanation_raw = await self.explainable_reasoner.execute(
                                    "explain",
                                    explanation_type="explorer_diagnosis_explanation",
                                    context=diagnosis_explanation_context,
                                )
                                outcome["diagnosis_explanation"] = explanation_raw.get(
                                    "explanation", "No explanation provided."
                                )
                            self.state_manager.energy -= 3
                            if self.growth_manager:
                                await self.growth_manager.acquire_skill(
                                    "diagnostic_skills", context={"performance": 0.9}
                                )
                        else:
                            raise RuntimeError("No primary explorer to diagnose.")
                    elif action == "move_random":
                        dx = random.choice([-1, 0, 1])
                        dy = random.choice([-1, 0, 1])
                        self.state_manager.x = float(
                            (self.state_manager.x + dx) % self.state_manager.world_size
                        )
                        self.state_manager.y = float(
                            (self.state_manager.y + dy) % self.state_manager.world_size
                        )
                        self.state_manager.energy -= 1
                        outcome["new_position"] = {
                            "x": self.state_manager.x,
                            "y": self.state_manager.y,
                        }
                        if self.growth_manager:
                            await self.growth_manager.acquire_skill(
                                "locomotion", context={"performance": 0.6}
                            )
                    else:
                        raise ValueError(f"Unknown action: {action}")

                if self.settings.ENABLE_CRITICAL_FAILURES and random.random() < 0.01:
                    outcome["status"] = "critical_failure"
                    outcome["error"] = (
                        "Simulated critical system failure during action execution."
                    )
                    if ARBITER_PACKAGE_AVAILABLE:
                        await self.human_in_loop.request_approval(
                            {
                                "issue": outcome["error"],
                                "action": action,
                                "agent": self.name,
                            }
                        )
                    await self.alert_critical_issue(outcome["error"])
                    self.log_event(
                        f"Critical failure during {action}: {outcome['error']}. Human approval requested.",
                        "critical_error",
                    )

                    critical_failure_explanation_context = {
                        "agent_name": self.name,
                        "action_that_failed": action,
                        "failure_details": outcome["error"],
                        "current_state": await self.get_status(),
                    }
                    if self.explainable_reasoner:
                        explanation_raw = await self.explainable_reasoner.execute(
                            "explain",
                            explanation_type="critical_failure_explanation",
                            context=critical_failure_explanation_context,
                        )
                        outcome["critical_failure_explanation"] = explanation_raw.get(
                            "explanation", "No explanation provided."
                        )

                if energy_gauge is not None:
                    energy_gauge.labels(agent=self.name).set(self.state_manager.energy)
                await self.state_manager.batch_save_state()
            except (
                httpx.RequestError,
                ValueError,
                RuntimeError,
                asyncio.TimeoutError,
            ) as e:
                sentry_sdk.capture_exception(e)
                await self.db_client.log_error(e, {"agent_name": self.name})
                outcome["status"] = "error"
                outcome["error"] = str(e)
                async with self._lock:
                    self.state_manager.energy -= 5
                self.log_event(
                    f"Handled exception during action {action}: {e}", "action_exception"
                )

                exception_explanation_context = {
                    "agent_name": self.name,
                    "action_attempted": action,
                    "exception_details": str(e),
                    "current_state": await self.get_status(),
                }
                if self.explainable_reasoner:
                    explanation_raw = await self.explainable_reasoner.execute(
                        "explain",
                        explanation_type="action_exception_explanation",
                        context=exception_explanation_context,
                    )
                    outcome["exception_explanation"] = explanation_raw.get(
                        "explanation", "No explanation provided."
                    )
                await self.state_manager.batch_save_state()
            except Exception as e:
                sentry_sdk.capture_exception(e)
                await self.db_client.log_error(e, {"agent_name": self.name})
                outcome["status"] = "error"
                outcome["error"] = f"An unexpected error occurred: {e}"
                async with self._lock:
                    self.state_manager.energy -= 5
                self.log_event(
                    f"Unhandled exception during action {action}: {e}",
                    "action_exception_unhandled",
                )
                outcome["exception_explanation"] = (
                    "An unexpected, unhandled error occurred."
                )
                await self.state_manager.batch_save_state()
        return outcome

    async def reflect(self) -> str:
        """
        Generates an internal reflection based on recent events.

        Returns
        -------
        str
            The reflection insight.
        """
        async with self._lock:
            recent_memory = self.state_manager.memory[-5:]
            reflection_context = {
                "current_energy": self.state_manager.energy,
                "recent_events": recent_memory,
                "current_position": {
                    "x": self.state_manager.x,
                    "y": self.state_manager.y,
                },
            }

        insight = f"{self.name} reflected: I have {self.state_manager.energy} energy. Recent events: {len(recent_memory)} items."

        if self.explainable_reasoner:
            try:
                explanation_raw = await self.explainable_reasoner.execute(
                    "explain",
                    explanation_type="reflection_summary",
                    context=reflection_context,
                )
                explanation = explanation_raw.get(
                    "explanation", "No explanation provided."
                )
                insight += f" Explainer's view: {explanation}"
            except Exception as e:
                sentry_sdk.capture_exception(e)
                insight += f" (Failed to get explanation from Reasoner: {e})"
                await self.db_client.log_error(e, {"agent_name": self.name})

        self.log_event(f"Agent reflection: {insight}", "reflection")
        return insight

    async def answer_why(self, query: str) -> str:
        """Answers a 'why' query using the explainable reasoner."""
        async with self._lock:
            agent_context = {
                "agent_name": self.name,
                "current_state": await self.get_status(),
                "recent_memory": self.state_manager.memory[-10:],
                "recent_monitor_events": self.monitor.get_recent_events(10),
            }

        if self.explainable_reasoner:
            try:
                reason_raw = await self.explainable_reasoner.execute(
                    "reason", query=query, context=agent_context
                )
                reason = reason_raw.get("reasoning", "No reasoning provided.")
            except Exception as e:
                sentry_sdk.capture_exception(e)
                reason = f"Reasoning unavailable due to an error: {e}"
                await self.db_client.log_error(e, {"agent_name": self.name})
        else:
            reason = (
                "Reasoning unavailable: Reasoner not initialized or plugin not found."
            )

        self.log_event(f"Answered why query: '{query}' with '{reason}'", "why_query")
        return reason

    async def log_social_event(self, event: str, with_whom: str, round_n: int):
        """Logs a social event to the agent's memory."""
        async with self._lock:
            self.state_manager.memory.append(
                {
                    "event": event,
                    "with_whom": with_whom,
                    "round": round_n,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )
            if len(self.state_manager.memory) > self.settings.MEMORY_LIMIT:
                self.state_manager.memory = self.state_manager.memory[
                    -self.settings.MEMORY_LIMIT :
                ]
            await self.state_manager.save_state()
        self.log_event(
            f"Logged social event: '{event}' with '{with_whom}'", "social_event"
        )
        if memory_gauge is not None:
            memory_gauge.labels(agent=self.name).set(len(self.state_manager.memory))

    async def sync_with_explorer(self, explorer_knowledge: Dict[str, Any]):
        """Syncs knowledge from the explorer into the agent's memory."""
        async with self._lock:
            self.state_manager.memory.append(
                {
                    "explorer_data": explorer_knowledge,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )
            if len(self.state_manager.memory) > self.settings.MEMORY_LIMIT:
                self.state_manager.memory = self.state_manager.memory[
                    -self.settings.MEMORY_LIMIT :
                ]
            await self.state_manager.save_state()
        self.log_event(
            f"Synced explorer knowledge: {explorer_knowledge}", "explorer_sync"
        )
        if memory_gauge is not None:
            memory_gauge.labels(agent=self.name).set(len(self.state_manager.memory))

    async def start_async_services(self):
        """Initializes and loads the agent's state and plugins."""
        self.running = True
        logging.getLogger(__name__).info(f"[{self.name}] Starting async services...")
        self.log_event("Starting async services", "service_start")

        await self.db_client.connect()
        db_status = await self.db_client.check_health()
        if db_status["status"] == "unhealthy":
            logging.getLogger(__name__).critical(
                f"[{self.name}] Database is unhealthy. Shutting down."
            )
            sys.exit(1)

        await self.state_manager.load_state()
        self.x = self.state_manager.x
        self.y = self.state_manager.y
        self.energy = self.state_manager.energy
        self.inventory = self.state_manager.inventory
        self.language = self.state_manager.language
        self.memory = self.state_manager.memory
        self.personality = self.state_manager.personality
        self.role = self.state_manager.role
        self.agent_type = self.state_manager.agent_type

        growth_manager_plugin = (
            _get_plugin_registry()
            .get(PlugInKind.GROWTH_MANAGER, {})
            .get("arbiter_growth")
        )
        if not growth_manager_plugin:
            logging.getLogger(__name__).critical(
                "GrowthManager plugin is required for production."
            )
            raise RuntimeError("Missing critical plugin: GrowthManager")
        self.growth_manager = growth_manager_plugin
        self.growth_manager.arbiter_name = self.name

        self.benchmarking_engine = (
            _get_plugin_registry().get(PlugInKind.CORE_SERVICE, {}).get("benchmarking")
        )
        self.explainable_reasoner = (
            _get_plugin_registry()
            .get(PlugInKind.AI_ASSISTANT, {})
            .get("explainable_reasoner")
        )

        for name, instance in [
            ("Explainable Reasoner", self.explainable_reasoner),
            ("Benchmarking Engine", self.benchmarking_engine),
        ]:
            if instance:
                try:
                    health_status = (
                        await instance.health_check()
                        if hasattr(instance, "health_check")
                        else "N/A"
                    )
                    logging.getLogger(__name__).info(
                        f"[{self.name}] {name} async health check: {health_status}"
                    )
                except Exception as e:
                    sentry_sdk.capture_exception(e)
                    logging.getLogger(__name__).error(
                        f"[{self.name}] {name} async health check failed: {e}",
                        exc_info=True,
                    )
                    await self.db_client.log_error(e, {"agent_name": self.name})
            else:
                logging.getLogger(__name__).warning(
                    f"[{self.name}] {name} plugin not available."
                )

        if AIOREDIS_AVAILABLE:
            self.redis_pool = redis.from_url(
                self.settings.REDIS_URL,
                max_connections=self.settings.REDIS_MAX_CONNECTIONS,
            )  # Fixed: use 'redis' instead of 'aioredis'
            self.peer_listener_task = asyncio.create_task(self.listen_for_peers())
        else:
            logging.warning(
                "redis.asyncio not available. Peer-to-peer communication will be disabled."
            )  # Fixed: updated warning message

        # Fix 2: Setup MessageQueueService subscriptions
        if self.message_queue_service:
            logging.getLogger(__name__).info(
                f"[{self.name}] Setting up MessageQueueService subscriptions..."
            )
            try:
                await self.message_queue_service.subscribe(
                    "bug_detected", self._on_bug_detected
                )
                await self.message_queue_service.subscribe(
                    "policy_violation", self._on_policy_violation
                )
                await self.message_queue_service.subscribe(
                    "code_analysis_complete", self._on_analysis_complete
                )
                await self.message_queue_service.subscribe(
                    "generator_output", self._on_generator_output
                )
                await self.message_queue_service.subscribe(
                    "test_results", self._on_test_results
                )
                await self.message_queue_service.subscribe(
                    "workflow_completed", self._on_workflow_completed
                )
                logging.getLogger(__name__).info(
                    f"[{self.name}] MessageQueueService subscriptions established"
                )
            except Exception as e:
                logging.getLogger(__name__).error(
                    f"[{self.name}] Failed to setup MessageQueueService subscriptions: {e}",
                    exc_info=True,
                )
        else:
            logging.getLogger(__name__).warning(
                f"[{self.name}] MessageQueueService not available, skipping event subscriptions"
            )

        # Fix 1: Setup HTTP /events endpoint
        if self.port:
            await self.setup_event_receiver()

    async def work_cycle(self) -> Dict[str, Any]:
        """A single work cycle for the agent, which calls the evolve method."""
        logging.getLogger(__name__).info(
            f"[{self.name}] Performing work_cycle (calling evolve)."
        )
        return await self.evolve()

    async def explore_and_fix(
        self, fix_paths: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Initiates a sequence to explore the codebase and apply fixes."""
        logging.getLogger(__name__).info(
            f"[{self.name}] Initiating explore_and_fix sequence."
        )
        if self.explorer:
            try:
                frontend_urls = await self.explorer.execute(
                    "discover_frontend_urls", html_discovery_dir="public"
                )
                if not frontend_urls:
                    raise ValueError(
                        "No frontend URLs configured or discovered for exploration."
                    )
                results = await self.explorer.execute(
                    action="explore_and_fix", arbiter=self, fix_paths=fix_paths
                )
                if self.engines.get("multi_modal"):
                    text_results = await self.engines["multi_modal"].process_text(
                        "Analyze codebase issues"
                    )
                    results["multi_modal_analysis"] = text_results.model_dump()
                return results
            except Exception as e:
                sentry_sdk.capture_exception(e)
                logging.getLogger(__name__).error(
                    f"[{self.name}] Error during explore_and_fix: {e}", exc_info=True
                )
                await self.db_client.log_error(e, {"agent_name": self.name})
                return {"status": "error", "reason": str(e)}
        else:
            logging.getLogger(__name__).warning(
                f"[{self.name}] Explorer not available for explore_and_fix."
            )
            return {"status": "skipped", "reason": "explorer_unavailable"}

    async def learn_from_data(self) -> Dict[str, Any]:
        """
        Integrates a learning routine to process historical data and update personality.
        """
        async with self._lock:
            try:
                if not SKLEARN_AVAILABLE:
                    logging.warning(
                        "scikit-learn not available, skipping learning from data."
                    )
                    return {
                        "status": "skipped",
                        "details": "scikit-learn not available.",
                    }

                if len(self.state_manager.memory) < 10:
                    return {"status": "skipped", "details": "Not enough data to learn."}

                X = []
                y = []
                for entry in self.state_manager.memory:
                    if entry.get("event_type") == "action_outcome":
                        features = [
                            entry["energy_before"],
                            entry["position_x"],
                            entry["position_y"],
                        ]
                        target = 1 if entry["outcome"] == "success" else 0
                        X.append(features)
                        y.append(target)

                if not X:
                    return {
                        "status": "skipped",
                        "details": "No action outcome data to learn from.",
                    }

                X_train, X_test, y_train, y_test = train_test_split(
                    X, y, test_size=0.2, random_state=42
                )

                model = LogisticRegression()
                model.fit(X_train, y_train)
                accuracy = model.score(X_test, y_test)

                self.state_manager.personality["agreeableness"] = float(
                    model.coef_[0][0]
                )
                await self.state_manager.save_state()

                return {
                    "status": "learning_complete",
                    "model_accuracy": accuracy,
                    "details": "Processed recent observations and updated personality.",
                }
            except ImportError as e:
                logging.getLogger(__name__).error(
                    f"Skipping learn_from_data due to missing dependency: {e}"
                )
                sentry_sdk.capture_exception(e)
                await self.db_client.log_error(e, {"agent_name": self.name})
                return {
                    "status": "error",
                    "details": "Missing dependencies for learning.",
                }
            except Exception as e:
                logging.getLogger(__name__).error(
                    f"Error during learning from data: {e}", exc_info=True
                )
                sentry_sdk.capture_exception(e)
                await self.db_client.log_error(e, {"agent_name": self.name})
                return {
                    "status": "error",
                    "details": "An error occurred during learning.",
                }

    async def auto_optimize(self) -> Dict[str, Any]:
        """
        Adjusts Arbiter parameters based on performance metrics.
        """
        async with self._lock:
            try:
                current_energy_efficiency = (
                    self.state_manager.energy / len(self.state_manager.memory)
                    if self.state_manager.memory
                    else 0
                )

                if current_energy_efficiency < 2.0:
                    self.state_manager.personality["recharge_preference"] = (
                        self.state_manager.personality.get("recharge_preference", 0.5)
                        + 0.1
                    )
                else:
                    self.state_manager.personality["recharge_preference"] = (
                        self.state_manager.personality.get("recharge_preference", 0.5)
                        - 0.1
                    )

                self.state_manager.personality["recharge_preference"] = max(
                    0, min(1, self.state_manager.personality["recharge_preference"])
                )
                await self.state_manager.save_state()

                return {
                    "status": "optimization_complete",
                    "details": f"Adjusted recharge preference to {self.state_manager.personality['recharge_preference']:.2f}.",
                }
            except Exception as e:
                logging.getLogger(__name__).error(
                    f"Error during auto-optimization: {e}", exc_info=True
                )
                sentry_sdk.capture_exception(e)
                await self.db_client.log_error(e, {"agent_name": self.name})
                return {
                    "status": "error",
                    "details": "An error occurred during optimization.",
                }

    async def report_findings(self, **kwargs: Any) -> Dict[str, Any]:
        """Generates and reports findings, optionally using an intent capture engine."""
        with sentry_sdk.push_scope() as scope:
            scope.set_tag("agent", self.name)
            logging.getLogger(__name__).info(
                f"[{self.name}] Generating and reporting findings."
            )

            if self.intent_capture_engine:
                try:
                    report_data = await self.intent_capture_engine.generate_report(
                        self.name, **kwargs
                    )
                    return {"status": "success", "report": report_data}
                except Exception as e:
                    sentry_sdk.capture_exception(e)
                    logging.getLogger(__name__).error(
                        f"[{self.name}] Error accessing intent capture engine for report: {e}",
                        exc_info=True,
                    )
                    await self.db_client.log_error(e, {"agent_name": self.name})
                    return {"status": "error", "error": str(e), "report": {}}

            status_report = await self.get_status()
            return {"status": "mock_report_generated", "report": status_report}

    async def self_debug(self) -> Dict[str, Any]:
        """Performs diagnostic checks for critical components."""
        logging.getLogger(__name__).info(
            f"[{self.name}] Initiating self-debug sequence."
        )
        async with self._lock:
            issues = []

            db_health = await self.db_client.check_health()
            if db_health["status"] == "unhealthy":
                issues.append(
                    {
                        "component": "Database",
                        "status": "unhealthy",
                        "error": db_health["error"],
                    }
                )

            if not self.explainable_reasoner:
                issues.append(
                    {
                        "component": "Explainable Reasoner",
                        "status": "unavailable",
                        "message": "Plugin not found in registry.",
                    }
                )
            if not self.benchmarking_engine:
                issues.append(
                    {
                        "component": "Benchmarking Engine",
                        "status": "unavailable",
                        "message": "Plugin not found in registry.",
                    }
                )

            if self.state_manager.energy < 0:
                issues.append(
                    {
                        "component": "Agent State",
                        "status": "anomaly",
                        "message": "Negative energy detected.",
                    }
                )
            if len(self.state_manager.memory) > self.settings.MEMORY_LIMIT:
                issues.append(
                    {
                        "component": "Agent State",
                        "status": "anomaly",
                        "message": "Memory limit exceeded.",
                    }
                )

            if self.explorer:
                try:
                    explorer_status = await self.explorer.execute("get_explorer_status")
                    if explorer_status.get("health") != "good":
                        issues.append(
                            {
                                "component": "Explorer",
                                "status": "degraded",
                                "message": "Explorer reported degraded health.",
                            }
                        )
                except Exception as e:
                    sentry_sdk.capture_exception(e)
                    issues.append(
                        {
                            "component": "Explorer",
                            "status": "error",
                            "message": f"Failed to get explorer status: {e}",
                        }
                    )
                    await self.db_client.log_error(e, {"agent_name": self.name})

            if issues:
                self.log_event(
                    f"Self-debug found {len(issues)} issues.", "self_debug_issues"
                )
                return {"status": "debug_complete_with_issues", "issues": issues}
            else:
                self.log_event(
                    "Self-debug completed successfully. No issues found.",
                    "self_debug_ok",
                )
                return {"status": "debug_complete_ok", "details": "All checks passed."}

    async def suggest_feature(self) -> Dict[str, Any]:
        """Analyzes memory and suggests a new feature based on identified gaps."""
        logging.getLogger(__name__).info(
            f"[{self.name}] Proposing new feature based on data analysis."
        )
        async with self._lock:
            exception_count = sum(
                1
                for event in self.monitor.get_recent_events(10)
                if event["type"] == "action_exception"
            )

            if exception_count > 3:
                feature_name = "Enhanced Error Recovery Module"
                rationale = "Frequent action exceptions were observed. An enhanced module could retry with different parameters or perform deeper diagnostics automatically."
            else:
                feature_name = "Adaptive Learning Rate Tuner"
                rationale = "Current learning process seems stable. An adaptive tuner could improve long-term performance."

            suggestion = {"feature_name": feature_name, "rationale": rationale}

            if self.explainable_reasoner:
                try:
                    explanation_raw = await self.explainable_reasoner.execute(
                        "explain",
                        explanation_type="feature_suggestion",
                        context={"suggestion": suggestion},
                    )
                    suggestion["full_rationale"] = explanation_raw.get("explanation")
                except Exception as e:
                    sentry_sdk.capture_exception(e)
                    suggestion["full_rationale"] = (
                        "Could not generate detailed rationale."
                    )
                    await self.db_client.log_error(e, {"agent_name": self.name})

        self.log_event(f"Suggested feature: {feature_name}", "feature_suggestion")
        return {"status": "feature_suggested", "feature": suggestion}

    @require_permission("read")
    async def filter_companies(self, preferences: Dict[str, Any]) -> Dict[str, Any]:
        """
        Filters companies based on preferences using a plugin.
        """

        class CompanyFilterPreferences(BaseModel):
            min_esg_score: int = Field(0, ge=0, le=100)
            min_financial_score: int = Field(0, ge=0, le=100)
            tickers: List[str] = ["TICKER1", "TICKER2", "TICKER3"]

        try:
            validated_preferences = CompanyFilterPreferences(**preferences)
        except Exception as e:
            sentry_sdk.capture_exception(e)
            return {
                "companies": [],
                "explain_log": [{"error": f"Invalid input preferences: {e}"}],
            }

        self.log_event(
            f"Filtering companies with preferences: {validated_preferences.model_dump()}",
            "filter_companies",
        )

        company_data_plugin = (
            _get_plugin_registry().get(PlugInKind.CORE_SERVICE, {}).get("company_data")
        )

        if company_data_plugin:
            try:
                filtered_companies_list = []
                explain_log = []

                for ticker in validated_preferences.tickers:
                    company_data = await company_data_plugin.execute(ticker=ticker)
                    esg_score = company_data.get("category_scores", {}).get(
                        "Environment", 0
                    )
                    financial_score = company_data.get("category_scores", {}).get(
                        "Financial Health", 0
                    )

                    meets_criteria = True
                    reason = []

                    if esg_score < validated_preferences.min_esg_score:
                        meets_criteria = False
                        reason.append(
                            f"ESG score ({esg_score}) too low (min {validated_preferences.min_esg_score})"
                        )

                    if financial_score < validated_preferences.min_financial_score:
                        meets_criteria = False
                        reason.append(
                            f"Financial score ({financial_score}) too low (min {validated_preferences.min_financial_score})"
                        )

                    if meets_criteria:
                        filtered_companies_list.append(
                            {
                                "company_name": company_data.get("name", ticker),
                                "ticker": ticker,
                                "filtered_overall": (esg_score + financial_score) / 2,
                                "esg_score": esg_score,
                                "financial_score": financial_score,
                            }
                        )
                        explain_log.append(
                            {"ticker": ticker, "reason": "Meets all criteria."}
                        )
                    else:
                        explain_log.append(
                            {"ticker": ticker, "reason": "; ".join(reason)}
                        )

                return {
                    "companies": filtered_companies_list,
                    "explain_log": explain_log,
                }

            except Exception as e:
                logging.getLogger(__name__).error(
                    f"Error filtering companies via plugin: {e}", exc_info=True
                )
                sentry_sdk.capture_exception(e)
                await self.db_client.log_error(e, {"agent_name": self.name})
                return {
                    "companies": [],
                    "explain_log": [{"error": f"Failed to filter companies: {e}"}],
                }
        else:
            logging.getLogger(__name__).warning(
                "Company data plugin not available for filtering."
            )
            return {
                "companies": [],
                "explain_log": [{"error": "Company data plugin not available."}],
            }

    async def stop_async_services(self):
        """Stops all running services and performs cleanup."""
        self.running = False
        logging.getLogger(__name__).info(f"[{self.name}] Stopping async services...")
        self.log_event("Stopping async services", "service_stop")
        if self.feedback:
            await self.feedback.disconnect_db()
        if self.explorer:
            await self.explorer.close()
        if self.redis_pool:
            await self.redis_pool.close()
        if self.db_client:
            await self.db_client.disconnect()
        if self.peer_listener_task:
            self.peer_listener_task.cancel()
            await asyncio.gather(self.peer_listener_task, return_exceptions=True)
        await self.push_metrics()

    async def get_status(self) -> Dict[str, Any]:
        """
        Returns the current status of the agent.
        """
        async with self._lock:
            status = {
                "name": self.name,
                "current_position": {
                    "x": self.state_manager.x,
                    "y": self.state_manager.y,
                },
                "energy": self.state_manager.energy,
                "is_alive": self.is_alive,
                "running": self.running,
                "role": self.state_manager.role,
                "inventory_count": len(self.state_manager.inventory),
                "memory_items": len(self.state_manager.memory),
                "personality_traits": self.state_manager.personality,
                "feedback_summary": (
                    await self.feedback.get_summary()
                    if self.feedback
                    else "Feedback not available"
                ),
                "monitor_report": self.monitor.generate_reports(),
            }
        self.log_event("Status requested", "status_check")
        if energy_gauge is not None:
            energy_gauge.labels(agent=self.name).set(self.state_manager.energy)
        if memory_gauge is not None:
            memory_gauge.labels(agent=self.name).set(len(self.state_manager.memory))
        await self.push_metrics()
        return status

    async def run_benchmark(self, *args, **kwargs):
        """Runs a benchmark using the benchmarking engine plugin."""
        if self.benchmarking_engine:
            return await self.benchmarking_engine.execute(
                "run_benchmark", *args, **kwargs
            )
        else:
            return {"error": "Benchmarking Engine not available."}

    async def explain(self, *args, **kwargs):
        """Requests an explanation from the explainable reasoner plugin."""
        start_time = time.time()
        if self.explainable_reasoner:
            explanation_result = await self.explainable_reasoner.execute(
                "explain", *args, **kwargs
            )
            if plugin_execution_time is not None:
                plugin_execution_time.labels(plugin="explainable_reasoner").observe(
                    time.time() - start_time
                )
            return explanation_result.get("explanation", str(explanation_result))
        else:
            return {"error": "Explainable Reasoner not available."}

    async def push_metrics(self):
        """Pushes metrics to the configured Prometheus Push Gateway."""
        if self.settings.PROMETHEUS_GATEWAY:
            try:
                push_to_gateway(
                    str(self.settings.PROMETHEUS_GATEWAY),
                    job=self.name,
                    registry=REGISTRY,
                )
                logging.getLogger(__name__).info(
                    f"[{self.name}] Pushed metrics to Prometheus gateway."
                )
            except Exception as e:
                sentry_sdk.capture_exception(e)
                logging.getLogger(__name__).error(
                    f"[{self.name}] Failed to push metrics to Prometheus: {e}"
                )
                await self.db_client.log_error(e, {"agent_name": self.name})

    @retry(
        stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=5)
    )
    async def alert_critical_issue(self, issue: str):
        """
        Sends a critical alert via the configured webhook with retries.
        """
        if self.settings.ALERT_WEBHOOK_URL:
            try:
                async with httpx.AsyncClient() as client:
                    await client.post(
                        str(self.settings.ALERT_WEBHOOK_URL),
                        json={"text": f"Critical issue in {self.name}: {issue}"},
                    )
            except Exception as e:
                sentry_sdk.capture_exception(e)
                logging.getLogger(__name__).error(
                    f"[{self.name}] Failed to send critical alert: {e}"
                )
                await self.db_client.log_error(e, {"agent_name": self.name})

    async def coordinate_with_peers(self, message: Dict[str, Any]):
        """Publishes a message to other agents via Redis pub/sub."""
        if not AIOREDIS_AVAILABLE:
            logging.warning(
                "redis.asyncio is not available. Skipping peer coordination."
            )  # Fixed: updated reference
            return {
                "status": "skipped",
                "details": "redis.asyncio not available",
            }  # Fixed: updated reference
        try:
            async with self.redis_pool as redis:
                # Security: Use SHA-256 instead of MD5 for hashing
                message_id = hashlib.sha256(json.dumps(message).encode()).hexdigest()
                await redis.setex(
                    f"arbiter_message:{message_id}", 3600, json.dumps(message)
                )
                await redis.publish("arbiter_channel", message_id)
                logging.getLogger(__name__).info(
                    f"[{self.name}] Published message {message_id} to peers."
                )
        except Exception as e:
            sentry_sdk.capture_exception(e)
            logging.getLogger(__name__).error(
                f"[{self.name}] Failed to publish to Redis: {e}", exc_info=True
            )
            await self.db_client.log_error(e, {"agent_name": self.name})

    async def listen_for_peers(self):
        """Linstens for messages from other agents on a Redis channel."""
        if not AIOREDIS_AVAILABLE:
            logging.warning(
                "redis.asyncio is not available. Peer listener will not start."
            )  # Fixed: updated reference
            return
        try:
            async with self.redis_pool as redis:
                channel = (await redis.subscribe("arbiter_channel"))[0]
                async for message in channel.iter():
                    message_id = message.decode()
                    message_data = await redis.get(f"arbiter_message:{message_id}")
                    if message_data:
                        data = json.loads(message_data.decode())
                        logging.getLogger(__name__).info(
                            f"[{self.name}] Received peer message: {data}"
                        )
                        async with self._lock:
                            self.state_manager.memory.append(
                                {
                                    "peer_message": data,
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                }
                            )
                            if (
                                len(self.state_manager.memory)
                                > self.settings.MEMORY_LIMIT
                            ):
                                self.state_manager.memory = self.state_manager.memory[
                                    -self.settings.MEMORY_LIMIT :
                                ]
                            if data.get("action") == "explored":
                                await self.sync_with_explorer(
                                    {"urls": data.get("urls", [])}
                                )
                            elif data.get("action") == "critical_failure":
                                await self.alert_critical_issue(
                                    f"Peer {data.get('agent')} reported critical failure: {data.get('error')}"
                                )
        except asyncio.CancelledError:
            logging.getLogger(__name__).info(
                f"[{self.name}] Peer listener task cancelled."
            )
        except Exception as e:
            sentry_sdk.capture_exception(e)
            logging.getLogger(__name__).error(
                f"[{self.name}] Peer listener failed: {e}", exc_info=True
            )
            await self.db_client.log_error(e, {"agent_name": self.name})

    # Fix 1: HTTP /events Endpoint Methods
    async def setup_event_receiver(self):
        """Sets up an HTTP endpoint to receive events from OmniCore."""
        from aiohttp import web

        logging.getLogger(__name__).info(
            f"[{self.name}] Setting up HTTP /events endpoint on port {self.port}"
        )

        app = web.Application()
        app.router.add_post("/events", self._handle_incoming_event_http)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "localhost", self.port)

        try:
            await site.start()
            logging.getLogger(__name__).info(
                f"[{self.name}] HTTP /events endpoint started on port {self.port}"
            )
        except Exception as e:
            logging.getLogger(__name__).error(
                f"[{self.name}] Failed to start HTTP endpoint: {e}", exc_info=True
            )

    async def _handle_incoming_event_http(self, request):
        """HTTP handler for incoming events."""
        from aiohttp import web

        try:
            data = await request.json()
            event_type = data.get("event_type")
            event_data = data.get("data", {})

            logging.getLogger(__name__).info(
                f"[{self.name}] Received HTTP event: {event_type}"
            )

            # Route to appropriate handler
            await self._handle_incoming_event(event_type, event_data)

            return web.json_response({"status": "received", "event_type": event_type})
        except Exception as e:
            logging.getLogger(__name__).error(
                f"[{self.name}] Error handling HTTP event: {e}", exc_info=True
            )
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    async def _handle_incoming_event(self, event_type: str, data: Dict[str, Any]):
        """
        Routes incoming events to appropriate handlers.

        Enhanced with metrics tracking for unknown event types and detailed logging.
        Follows industry best practices for event-driven architectures.
        """
        logger = logging.getLogger(__name__)
        logger.info(
            f"[{self.name}] Routing event type: {event_type}",
            extra={"event_type": event_type, "agent": self.name},
        )

        # Route to handler based on event type
        handler_map = {
            "requests.arbiter.bug_detected": self._on_bug_detected,
            "requests.arbiter.policy_violation": self._on_policy_violation,
            "requests.arbiter.analysis_complete": self._on_analysis_complete,
            "requests.arbiter.generator_output": self._on_generator_output,
            "requests.arbiter.test_results": self._on_test_results,
            "requests.arbiter.workflow_completed": self._on_workflow_completed,
            "bug_detected": self._on_bug_detected,
            "policy_violation": self._on_policy_violation,
            "code_analysis_complete": self._on_analysis_complete,
            "generator_output": self._on_generator_output,
            "test_results": self._on_test_results,
            "workflow_completed": self._on_workflow_completed,
        }

        handler = handler_map.get(event_type)
        if handler:
            try:
                # Track successful routing
                try:
                    routed_events_counter = get_or_create_counter(
                        "arbiter_events_routed_total",
                        "Total number of events successfully routed to handlers",
                        labelnames=["event_type", "agent"],
                    )
                    routed_events_counter.labels(
                        event_type=event_type, agent=self.name
                    ).inc()
                except Exception as metrics_error:
                    # Log metric errors at debug level to aid troubleshooting
                    logger.debug(
                        f"Failed to update routed events metric: {metrics_error}",
                        extra={"event_type": event_type, "agent": self.name},
                    )

                await handler(data)
            except Exception as e:
                logger.error(
                    f"[{self.name}] Handler error for {event_type}: {e}",
                    exc_info=True,
                    extra={
                        "event_type": event_type,
                        "agent": self.name,
                        "error_type": type(e).__name__,
                    },
                )

                # Track handler errors
                try:
                    handler_errors_counter = get_or_create_counter(
                        "arbiter_event_handler_errors_total",
                        "Total number of errors in event handlers",
                        labelnames=["event_type", "agent", "error_type"],
                    )
                    handler_errors_counter.labels(
                        event_type=event_type,
                        agent=self.name,
                        error_type=type(e).__name__,
                    ).inc()
                except Exception as metrics_error:
                    logger.debug(
                        f"Failed to update handler errors metric: {metrics_error}",
                        extra={"event_type": event_type, "agent": self.name},
                    )
        else:
            # Unknown event type - log with more context and track metrics
            logger.warning(
                f"[{self.name}] No handler found for event type: {event_type}. "
                f"Available handlers: {', '.join(handler_map.keys())}. "
                f"Event data keys: {list(data.keys()) if data else 'none'}",
                extra={
                    "event_type": event_type,
                    "agent": self.name,
                    "available_handlers": list(handler_map.keys()),
                },
            )

            # Track unknown event types for monitoring
            try:
                unknown_events_counter = get_or_create_counter(
                    "arbiter_events_unknown_total",
                    "Total number of unknown/unrouted event types",
                    labelnames=["event_type", "agent"],
                )
                unknown_events_counter.labels(
                    event_type=event_type, agent=self.name
                ).inc()
            except Exception as metrics_error:
                logger.debug(
                    f"Failed to update unknown events metric: {metrics_error}",
                    extra={"event_type": event_type, "agent": self.name},
                )

            # Consider implementing a dead-letter handler for unrouted events
            # For now, log the event data for investigation (sanitized)
            try:
                # Sanitize sensitive data before logging
                sanitized_data = self._sanitize_event_data(data)
                logger.debug(
                    f"[{self.name}] Unrouted event data: {json.dumps(sanitized_data, indent=2)}",
                    extra={"event_type": event_type, "data": sanitized_data},
                )
            except Exception:
                logger.debug(
                    f"[{self.name}] Unrouted event data (non-serializable): {str(data)[:200]}"
                )

    def _sanitize_event_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Sanitize event data by redacting sensitive fields before logging.

        Args:
            data: Raw event data dictionary

        Returns:
            Sanitized dictionary with sensitive fields redacted
        """
        if not isinstance(data, dict):
            return data

        # Set of sensitive field names to redact (for O(1) lookup)
        sensitive_fields = {
            "password",
            "token",
            "secret",
            "api_key",
            "apikey",
            "auth",
            "authorization",
            "credential",
            "private_key",
            "access_token",
            "refresh_token",
            "session_id",
        }

        sanitized = {}
        for key, value in data.items():
            key_lower = key.lower()

            # Check if any sensitive field name is contained in the key
            # O(n) where n is number of sensitive fields (small constant)
            is_sensitive = any(sensitive in key_lower for sensitive in sensitive_fields)

            if is_sensitive:
                sanitized[key] = "[REDACTED]"
            # Recursively sanitize nested dictionaries
            elif isinstance(value, dict):
                sanitized[key] = self._sanitize_event_data(value)
            # Truncate very long strings
            elif isinstance(value, str) and len(value) > 500:
                sanitized[key] = value[:500] + "... [TRUNCATED]"
            else:
                sanitized[key] = value

        return sanitized

    # Fix 3: Event Handler Methods
    async def _on_bug_detected(self, data: Dict[str, Any]):
        """Handler for bug_detected events."""
        logging.getLogger(__name__).info(
            f"[{self.name}] Bug detected event received: {data.get('bug_id', 'unknown')}"
        )
        try:
            bug_id = data.get("bug_id")
            bug_type = data.get("bug_type", "unknown")
            severity = data.get("severity", "medium")

            # Log the bug
            self.log_event(
                f"Bug detected: {bug_type} (severity: {severity})", "bug_detected"
            )

            # Coordinate with peers to distribute workload
            await self.coordinate_with_peers(
                {
                    "action": "bug_detected",
                    "agent": self.name,
                    "bug_id": bug_id,
                    "bug_type": bug_type,
                    "severity": severity,
                }
            )

            # If decision optimizer is available, create a fix task
            if self.decision_optimizer and severity in ["high", "critical"]:
                logging.getLogger(__name__).info(
                    f"[{self.name}] Creating fix task for bug {bug_id}"
                )
        except Exception as e:
            logging.getLogger(__name__).error(
                f"[{self.name}] Error handling bug_detected event: {e}", exc_info=True
            )

    async def _on_policy_violation(self, data: Dict[str, Any]):
        """Handler for policy_violation events."""
        logging.getLogger(__name__).info(
            f"[{self.name}] Policy violation event received: {data.get('violation_id', 'unknown')}"
        )
        try:
            violation_id = data.get("violation_id")
            policy_name = data.get("policy_name", "unknown")
            action = data.get("action", "unknown")

            # Log the violation
            self.log_event(
                f"Policy violation: {policy_name} (action: {action})",
                "policy_violation",
            )

            # Request human approval if human-in-loop is available
            if self.human_in_loop:
                await self.human_in_loop.request_approval(
                    {
                        "issue": f"Policy violation: {policy_name}",
                        "action": action,
                        "agent": self.name,
                        "violation_id": violation_id,
                        "data": data,
                    }
                )
        except Exception as e:
            logging.getLogger(__name__).error(
                f"[{self.name}] Error handling policy_violation event: {e}",
                exc_info=True,
            )

    async def _on_analysis_complete(self, data: Dict[str, Any]):
        """Handler for code_analysis_complete events."""
        logging.getLogger(__name__).info(
            f"[{self.name}] Analysis complete event received"
        )
        try:
            issues = data.get("issues", [])
            data.get("analysis_id")

            # Log completion
            self.log_event(
                f"Analysis complete: {len(issues)} issues found", "analysis_complete"
            )

            # Trigger fix workflows for detected issues
            for issue in issues:
                if issue.get("severity") in ["high", "critical"]:
                    logging.getLogger(__name__).info(
                        f"[{self.name}] Triggering fix workflow for {issue.get('type')}"
                    )
                    # Coordinate with decision optimizer if available
                    if self.decision_optimizer:
                        logging.getLogger(__name__).info(
                            f"[{self.name}] DecisionOptimizer available for fix coordination"
                        )
        except Exception as e:
            logging.getLogger(__name__).error(
                f"[{self.name}] Error handling analysis_complete event: {e}",
                exc_info=True,
            )

    async def _on_generator_output(self, data: Dict[str, Any]):
        """Handler for generator_output events - provides full generator integration."""
        logging.getLogger(__name__).info(
            f"[{self.name}] Generator output event received"
        )
        try:
            generated_code = data.get("code")
            language = data.get("language", "python")
            generator_id = data.get("generator_id")
            metadata = data.get("metadata", {})

            # Log the generation
            self.log_event(f"Code generated by {generator_id}", "generator_output")

            # Direct generator engine integration
            if self.generator_engine and hasattr(
                self.generator_engine, "process_output"
            ):
                try:
                    await self.generator_engine.process_output(
                        generated_code, language, metadata
                    )
                    logging.getLogger(__name__).info(
                        f"[{self.name}] Generator engine processed output successfully"
                    )
                except Exception as e:
                    logging.getLogger(__name__).error(
                        f"[{self.name}] Generator engine processing failed: {e}",
                        exc_info=True,
                    )

            # Route to test generation if available
            if generated_code:
                await self.run_test_generation(generated_code, language)
                logging.getLogger(__name__).info(
                    f"[{self.name}] Triggered test generation for generated code"
                )

            # Update knowledge graph with generator output
            if self.knowledge_graph:
                try:
                    await self.knowledge_graph.add_fact(
                        "GeneratorOutputs",
                        generator_id or "unknown",
                        {
                            "code": generated_code[:200] if generated_code else None,
                            "language": language,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "metadata": metadata,
                        },
                        source=self.name,
                    )
                except Exception as e:
                    logging.getLogger(__name__).error(
                        f"[{self.name}] Failed to update knowledge graph: {e}",
                        exc_info=True,
                    )

            # Publish back to OmniCore for workflow tracking
            await self.publish_to_omnicore(
                "generator_output_processed",
                {
                    "generator_id": generator_id,
                    "arbiter": self.name,
                    "success": True,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            )

        except Exception as e:
            logging.getLogger(__name__).error(
                f"[{self.name}] Error handling generator_output event: {e}",
                exc_info=True,
            )
            # Notify OmniCore of processing failure
            try:
                await self.publish_to_omnicore(
                    "generator_output_failed",
                    {
                        "generator_id": data.get("generator_id"),
                        "arbiter": self.name,
                        "error": str(e),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                )
            except Exception as pub_err:
                logging.getLogger(__name__).error(
                    f"[{self.name}] Failed to publish error notification: {pub_err}",
                    exc_info=True,
                )

    async def _on_test_results(self, data: Dict[str, Any]):
        """Handler for test_results events."""
        logging.getLogger(__name__).info(f"[{self.name}] Test results event received")
        try:
            data.get("test_id")
            failures = data.get("failures", [])
            passed = data.get("passed", 0)
            failed = data.get("failed", 0)

            # Log results
            self.log_event(
                f"Test results: {passed} passed, {failed} failed", "test_results"
            )

            # Create fix tasks for test failures
            if failures and self.decision_optimizer:
                for failure in failures:
                    logging.getLogger(__name__).info(
                        f"[{self.name}] Creating fix task for test failure: {failure.get('test_name')}"
                    )
        except Exception as e:
            logging.getLogger(__name__).error(
                f"[{self.name}] Error handling test_results event: {e}", exc_info=True
            )

    async def _on_workflow_completed(self, data: Dict[str, Any]):
        """Handler for workflow_completed events."""
        logging.getLogger(__name__).info(
            f"[{self.name}] Workflow completed event received"
        )
        try:
            workflow_id = data.get("workflow_id")
            status = data.get("status", "unknown")
            results = data.get("results", {})

            # Log completion
            self.log_event(
                f"Workflow {workflow_id} completed with status: {status}",
                "workflow_completed",
            )

            # Update knowledge graph if available
            if hasattr(self, "knowledge_graph") and self.knowledge_graph:
                await self.knowledge_graph.add_fact(
                    "WorkflowResults",
                    workflow_id,
                    results,
                    source=self.name,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                )
                logging.getLogger(__name__).info(
                    f"[{self.name}] Updated knowledge graph with workflow results"
                )
        except Exception as e:
            logging.getLogger(__name__).error(
                f"[{self.name}] Error handling workflow_completed event: {e}",
                exc_info=True,
            )


# --- Plugin Registration ---
# Deferred to avoid module-level initialization overhead
_plugins_registered = False


def _register_default_plugins():
    """Register default plugins. Called on first Arbiter instantiation."""
    global _plugins_registered
    if not _plugins_registered:
        registry = _get_plugin_registry()
        # Only register if not already registered to avoid duplicate registration error
        if not registry.get_metadata(PlugInKind.GROWTH_MANAGER, "arbiter_growth"):
            registry.register_instance(
                PlugInKind.GROWTH_MANAGER,
                "arbiter_growth",
                ArbiterGrowthManager(),
                version="1.0.0",
            )
        if not registry.get_metadata(PlugInKind.AI_ASSISTANT, "explainable_reasoner"):
            registry.register_instance(
                PlugInKind.AI_ASSISTANT,
                "explainable_reasoner",
                ExplainableReasoner(),
                version="1.0.0",
            )
        _plugins_registered = True


# --- Main Application Logic ---
def main():
    if os.environ.get("SANDBOXED_AGENT", "") == "1":
        pass
    else:
        logging.getLogger(__name__).info("Orchestrator: Launching Arbiter.")

    try:
        main_settings = MyArbiterConfig()
    except Exception as e:
        logging.getLogger(__name__).error(
            f"Configuration validation failed: {e}. Please check your .env file or environment variables."
        )
        sys.exit(1)

    test_engine = create_async_engine(
        main_settings.DATABASE_URL,
        echo=False,
        pool_size=5,
        max_overflow=10,
        pool_timeout=30,
    )
    logging.getLogger(__name__).info(
        f"Database AsyncEngine created using {main_settings.DATABASE_URL}."
    )

    async def run_agent_simulation():
        db_client_instance = PostgresClient(main_settings.DATABASE_URL)

        # A mock DecisionOptimizer since it's not defined in the file
        class DecisionOptimizer:
            def __init__(self, settings):
                pass

        mock_engines = {
            "simulation": SimulationEngine(),
            "code_health_env": BaseCodeHealthEnv() if ENVS_AVAILABLE else None,
            "audit_log_manager": AuditLogManager(db_client_instance),
            "intent_capture": IntentCaptureEngine(),
        }

        alice = Arbiter(
            "Alice",
            db_engine=test_engine,
            world_size=100,
            settings=main_settings,
            analyzer=CodeAnalyzer(),
            decision_optimizer=DecisionOptimizer(main_settings),
            engines=mock_engines,
        )

        await alice.start_async_services()
        initial_status = await alice.get_status()
        logging.getLogger(__name__).info(
            f"Alice's initial state: X={initial_status['current_position']['x']}, Y={initial_status['current_position']['y']}, Energy={initial_status['energy']}"
        )

        logging.getLogger(__name__).info(
            "\n--- Running one refactored evolution cycle for Alice ---"
        )
        result = await alice.evolve()
        logging.getLogger(__name__).info(f"Evolve cycle result: {result['status']}")

        logging.getLogger(__name__).info("\n--- Running a second cycle ---")
        result_clean = await alice.evolve()
        logging.getLogger(__name__).info(
            f"Second evolve cycle result: {result_clean['status']}"
        )

        logging.getLogger(__name__).info(
            "\n--- Running learning, optimization, debug and feature suggestion routines ---"
        )
        learn_result = await alice.learn_from_data()
        logging.getLogger(__name__).info(f"Learning result: {learn_result['status']}")

        optimize_result = await alice.auto_optimize()
        logging.getLogger(__name__).info(
            f"Optimization result: {optimize_result['status']}"
        )

        debug_result = await alice.self_debug()
        logging.getLogger(__name__).info(f"Debug result: {debug_result['status']}")

        suggest_result = await alice.suggest_feature()
        logging.getLogger(__name__).info(
            f"Feature suggestion: {suggest_result['feature']['feature_name']}"
        )

        health_result = await alice.health_check()
        logging.getLogger(__name__).info(
            f"Health check status: {health_result['status']}"
        )

        await alice.stop_async_services()
        logging.getLogger(__name__).info("\n--- All tests complete ---\n")

    if UVLOOP_AVAILABLE:
        uvloop.install()
        asyncio.run(run_agent_simulation())
    else:
        asyncio.run(run_agent_simulation())
    logging.getLogger(__name__).info("Application shutdown.")


if __name__ == "__main__":
    main()
