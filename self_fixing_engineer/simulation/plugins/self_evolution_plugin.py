#!/usr/bin/env python3
"""
Self-Evolution Plugin for AI Agents

This plugin manages the self-evolution of AI agents in the SFE system,
coordinating meta-learning and adapting behavior over time based on performance data.

Features:
- Prompt optimization based on performance data
- Metrics and monitoring for evolution cycles
- Configurable evolution strategies
- Resilient handling of dependencies and error conditions
"""

import os
import sys
import asyncio
import json
import logging
import time
import uuid
import re
from typing import Dict, Any, Optional, Tuple, Callable, List, Union, TypeVar, cast, AsyncContextManager
from pathlib import Path
import functools
import inspect
from contextlib import asynccontextmanager

# --- Version ---
__version__ = "1.0.3"

# --- Logger Setup FIRST (so it is always available) ---
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

# --- Type Variables ---
T = TypeVar('T')
AsyncFunc = Callable[..., Any]

# --- Conditional Imports for Enhancements ---
try:
    from pydantic import BaseModel, Field, ValidationError
    try:
        from pydantic import VERSION as PYDANTIC_VERSION
        PYDANTIC_V2 = int(PYDANTIC_VERSION.split('.')[0]) >= 2
    except Exception:
        PYDANTIC_V2 = False
    
    # Version-specific imports
    if PYDANTIC_V2:
        try:
            from pydantic import model_validator
        except ImportError:
            model_validator = None
        validator = None  # v1 validator not available in v2
    else:
        try:
            from pydantic import validator
        except ImportError:
            validator = None
        model_validator = None  # v2 model_validator not available in v1
    
    pydantic_available = True
except ImportError:
    pydantic_available = False
    PYDANTIC_V2 = False
    validator = None
    model_validator = None
    logger.warning("Pydantic not found. Using simplified configuration model.")

try:
    from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
    from tenacity import RetryError, wait_random_exponential, before_log, after_log
    tenacity_available = True
except ImportError:
    logger.warning("Tenacity not found. Retries will be non-functional.")
    def retry(*args, **kwargs): return lambda f: f
    def stop_after_attempt(n): return None
    def wait_exponential(*args, **kwargs): return None
    def wait_random_exponential(*args, **kwargs): return None
    def retry_if_exception_type(e): return lambda x: False
    def before_log(*args, **kwargs): return None
    def after_log(*args, **kwargs): return None
    class RetryError(Exception): pass
    tenacity_available = False

try:
    from langchain_core.prompts import PromptTemplate
    from langchain_core.messages import SystemMessage, HumanMessage, BaseMessage, AIMessage
    from langchain_core.language_models import BaseChatModel
    from langchain_openai import ChatOpenAI
    from langchain_core.outputs import ChatResult
    langchain_available = True
except ImportError:
    logger.warning("LangChain core components not found. PromptTemplate and Message classes will be mocked.")
    class PromptTemplate:
        def __init__(self, template: str): self.template = template
        def format(self, **kwargs): return self.template.format(**kwargs)
        @staticmethod
        def from_template(template: str): return PromptTemplate(template)
    class BaseMessage:
        def __init__(self, content: str): self.content = content
    class SystemMessage(BaseMessage): pass
    class HumanMessage(BaseMessage): pass
    class AIMessage(BaseMessage): pass
    class BaseChatModel:
        def ainvoke(self, messages: List[Any], **kwargs: Any) -> Any: raise NotImplementedError
        @property
        def model_name(self) -> str: return "mock_llm"
    ChatOpenAI = object
    ChatResult = object
    langchain_available = False

try:
    from prometheus_client import Counter, Histogram, Gauge, Summary, REGISTRY
    from prometheus_client.metrics import MetricWrapperBase
    prometheus_available = True
    def _get_or_create_metric(metric_type: type, name: str, documentation: str, 
                             labelnames: Optional[Tuple[str, ...]] = None, 
                             buckets: Optional[Tuple[float, ...]] = None) -> MetricWrapperBase:
        if labelnames is None: labelnames = ()
        if name in REGISTRY._names_to_collectors:
            return REGISTRY._names_to_collectors[name]
        if metric_type == Histogram: 
            return metric_type(name, documentation, labelnames=labelnames, 
                               buckets=buckets or Histogram.DEFAULT_BUCKETS)
        if metric_type == Counter: 
            return metric_type(name, documentation, labelnames=labelnames)
        return metric_type(name, documentation, labelnames=labelnames)
except ImportError:
    prometheus_available = False
    logger.warning("Prometheus client not found. Metrics for self-evolution plugin will be disabled.")
    class DummyMetric:
        def inc(self, amount: float = 1.0): pass
        def set(self, value: float): pass
        def observe(self, value: float): pass
        def labels(self, *args, **kwargs): return self
    _get_or_create_metric = lambda *args, **kwargs: DummyMetric()

try:
    from detect_secrets.core import SecretsCollection
    from detect_secrets.settings import transient_settings
    detect_secrets_available = True
except ImportError:
    logger.warning("detect-secrets not found. Secret detection will be disabled.")
    detect_secrets_available = False
    # Minimal implementation for secret detection fallback
    class FallbackSecretsDetector:
        def scan_string(self, data: str) -> List[Any]:
            # Basic pattern matching for common secrets
            patterns = [
                r'(password|passwd|pwd|secret|token|api[_\-]?key)\s*[=:]\s*[\'\"][^\'\"]{8,}[\'\"]',
                r'(access[_\-]?key|secret[_\-]?key)[=:]\s*[\'\"][^\'\"]{8,}[\'\"]',
                r'bearer\s+[a-zA-Z0-9\-_\.]{8,}',
                r'sk-[a-zA-Z0-9]{32,}',  # OpenAI API key pattern
                r'gh[pousr]_[a-zA-Z0-9]{36,}',  # GitHub token pattern
                r'[a-zA-Z0-9+/]{40,}={0,2}'  # Base64 pattern (common for tokens)
            ]
            
            # Pre-compile patterns for efficiency
            compiled_patterns = [re.compile(p, re.IGNORECASE) for p in patterns]
            
            results = []
            for pattern in compiled_patterns:
                matches = pattern.finditer(data)
                for match in matches:
                    results.append(type('Secret', (), {'secret_value': match.group(0)}))
            return results
    
    class FallbackSecretsCollection:
        def __init__(self):
            self.detector = FallbackSecretsDetector()
            self.secrets = []
            
        def scan_string(self, data: str) -> None:
            self.secrets = self.detector.scan_string(data)
            
        def __iter__(self):
            return iter(self.secrets)
    
    class FallbackTransientSettings:
        def __enter__(self): return self
        def __exit__(self, *args): pass
    
    SecretsCollection = FallbackSecretsCollection
    transient_settings = lambda: FallbackTransientSettings()

try:
    from redis.asyncio import Redis
    from redis.exceptions import RedisError, ConnectionError as RedisConnectionError
    redis_available = True
    @asynccontextmanager
    async def get_redis_client(url: str) -> AsyncContextManager[Redis]:
        """Get a Redis client with proper async context management."""
        client = None
        try:
            client = Redis.from_url(url, socket_timeout=5.0, socket_connect_timeout=5.0)
            await client.ping()  # Test connection
            yield client
        except (RedisError, RedisConnectionError) as e:
            logger.error(f"Redis connection error: {e}")
            if client:
                await client.close()
            raise
        finally:
            if client:
                await client.close()
except ImportError:
    redis_available = False
    logger.warning("Redis async client not found. Caching will be disabled.")
    @asynccontextmanager
    async def get_redis_client(url: str) -> AsyncContextManager[None]:
        """Mock Redis client context manager when Redis is unavailable."""
        yield None


# --- Pydantic Config Model ---
if pydantic_available:
    class EvolutionConfig(BaseModel):
        default_evolution_strategy: str = Field(default="prompt_optimization", 
                                                description="Default strategy to use for evolution")
        prompt_optimization_model: str = Field(default="gpt-4o-mini", 
                                                description="Model to use for prompt optimization")
        prompt_optimization_temperature: float = Field(default=0.7, ge=0.0, le=1.0,
                                                       description="Temperature setting for LLM")
        llm_timeout_seconds: int = Field(default=60, ge=1,
                                       description="Timeout for LLM API calls")
        evolution_cycle_interval_seconds: int = Field(default=3600, ge=1,
                                                      description="Interval between evolution cycles")
        evolution_data_window_days: int = Field(default=7, ge=1,
                                                description="Days of data to analyze for evolution")
        feedback_threshold_for_adaptation: float = Field(default=0.1, ge=0.0,
                                                         description="Threshold for triggering adaptation")
        max_evolution_retries: int = Field(default=3, ge=0,
                                         description="Maximum retries for evolution cycle")
        retry_backoff_factor: float = Field(default=1.0, ge=0.1,
                                            description="Backoff factor for retries")
        redis_cache_url: Optional[str] = Field(default=None,
                                                description="Redis URL for caching data")
        redis_cache_ttl: int = Field(default=3600, ge=1,
                                     description="TTL for cached data in Redis")
        content_safety_check: bool = Field(default=True,
                                            description="Enable content safety checks for LLM outputs")
        
        @validator('redis_cache_url')
        def validate_redis_url(cls, v):
            if v is not None and not v.startswith(('redis://', 'rediss://')):
                raise ValueError('Redis URL must start with redis:// or rediss://')
            return v
        
        @model_validator(mode='after')
        def check_retry_settings(cls, values):
            if values.max_evolution_retries > 0 and not tenacity_available:
                logger.warning("Tenacity not available but retries configured. Retries will be disabled.")
                values.max_evolution_retries = 0
            return values
else:
    class EvolutionConfig:
        """Configuration class for evolution settings when Pydantic is unavailable."""
        def __init__(self):
            self.default_evolution_strategy = "prompt_optimization"
            self.prompt_optimization_model = "gpt-4o-mini"
            self.prompt_optimization_temperature = 0.7
            self.llm_timeout_seconds = 60
            self.evolution_cycle_interval_seconds = 3600
            self.evolution_data_window_days = 7
            self.feedback_threshold_for_adaptation = 0.1
            self.max_evolution_retries = 3
            self.retry_backoff_factor = 1.0
            self.redis_cache_url = None
            self.redis_cache_ttl = 3600
            self.content_safety_check = True
        
        def validate(self) -> bool:
            """Basic validation for configuration when Pydantic is unavailable."""
            if self.prompt_optimization_temperature < 0 or self.prompt_optimization_temperature > 1:
                logger.error("prompt_optimization_temperature must be between 0 and 1")
                return False
            if self.llm_timeout_seconds < 1:
                logger.error("llm_timeout_seconds must be positive")
                return False
            if self.redis_cache_url and not (
                self.redis_cache_url.startswith('redis://') or 
                self.redis_cache_url.startswith('rediss://')
            ):
                logger.error("redis_cache_url must start with redis:// or rediss://")
                return False
            return True


# --- Load Config from File or Env ---
def _load_config() -> EvolutionConfig:
    """
    Load configuration from file and environment variables.
    
    Returns:
        EvolutionConfig: The loaded and validated configuration object
    """
    config_file_path = Path(__file__).parent / "configs" / "self_evolution_config.json"
    config_dict = {}
    
    # Try to load from config file
    if config_file_path.exists():
        try:
            with open(config_file_path, "r") as f:
                config_dict = json.load(f)
                logger.info(f"Loaded config from {config_file_path}")
        except (IOError, json.JSONDecodeError) as e:
            logger.warning(f"Could not load config file {config_file_path}: {e}. Using defaults.")
    
    # Override with environment variables
    env_prefix = "SFE_EVO_"
    config_fields = (EvolutionConfig.model_fields if pydantic_available else vars(EvolutionConfig()))
    
    for key in config_fields:
        env_var_name = f"{env_prefix}{key.upper()}"
        env_var = os.getenv(env_var_name)
        
        if env_var is not None:
            try:
                # Type conversion based on expected type
                if key in ('prompt_optimization_temperature', 'feedback_threshold_for_adaptation', 
                           'retry_backoff_factor'):
                    config_dict[key] = float(env_var)
                elif key in ('llm_timeout_seconds', 'evolution_cycle_interval_seconds',
                            'evolution_data_window_days', 'max_evolution_retries',
                            'redis_cache_ttl'):
                    config_dict[key] = int(env_var)
                elif key == 'content_safety_check':
                    config_dict[key] = env_var.lower() in ('true', 'yes', '1')
                else:
                    config_dict[key] = env_var
                logger.info(f"Loaded config from environment: {env_var_name}={env_var}")
            except ValueError:
                logger.warning(f"Invalid type for environment variable {env_var_name}. Using default.")
    
    # Create and validate config
    if pydantic_available:
        try:
            config = EvolutionConfig(**config_dict)
            logger.info("Configuration loaded and validated with Pydantic")
            return config
        except ValidationError as e:
            logger.error(f"Configuration validation failed: {e}. Using defaults.")
            return EvolutionConfig()
    else:
        config = EvolutionConfig()
        for key, value in config_dict.items():
            if hasattr(config, key):
                setattr(config, key, value)
        
        if not config.validate():
            logger.error("Configuration validation failed. Using defaults.")
            return EvolutionConfig()
            
        logger.info("Configuration loaded with basic validation")
        return config

EVOLUTION_CONFIG = _load_config()


# --- Prometheus Metrics ---
if prometheus_available:
    EVOLUTION_CYCLES_TOTAL = _get_or_create_metric(
        Counter, 
        'self_evolution_cycles_total', 
        'Total self-evolution cycles initiated'
    )
    EVOLUTION_ADAPTATIONS_SUCCESS = _get_or_create_metric(
        Counter, 
        'self_evolution_adaptations_success_total', 
        'Total successful adaptations applied', 
        labelnames=('evolution_id', 'adaptation_type')
    )
    EVOLUTION_ERRORS = _get_or_create_metric(
        Counter, 
        'self_evolution_errors_total', 
        'Total errors during self-evolution', 
        labelnames=('error_type',)
    )
    EVOLUTION_CYCLE_LATENCY_SECONDS = _get_or_create_metric(
        Histogram, 
        'self_evolution_cycle_latency_seconds', 
        'Latency of self-evolution cycles',
        buckets=(0.1, 0.5, 1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0)
    )
    ADAPTATION_TYPES = _get_or_create_metric(
        Counter, 
        'self_evolution_adaptation_types_total', 
        'Types of adaptations applied', 
        labelnames=('type',)
    )
    CACHE_HITS = _get_or_create_metric(
        Counter, 
        'self_evolution_cache_hits_total', 
        'Total cache hits'
    )
    CACHE_MISSES = _get_or_create_metric(
        Counter, 
        'self_evolution_cache_misses_total', 
        'Total cache misses'
    )
    LLM_REQUEST_DURATION = _get_or_create_metric(
        Histogram, 
        'self_evolution_llm_request_duration_seconds', 
        'Duration of LLM requests',
        labelnames=('model',),
        buckets=(0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0)
    )
    EXTERNAL_DEPENDENCY_HEALTH = _get_or_create_metric(
        Gauge, 
        'self_evolution_external_dependency_health', 
        'Health status of external dependencies (1=healthy, 0=unhealthy)',
        labelnames=('dependency',)
    )


# --- PLUGIN MANIFEST ---
PLUGIN_MANIFEST = {
    "name": "SelfEvolutionPlugin",
    "version": __version__,
    "description": "Manages the self-evolution of SFE agents, coordinating meta-learning and adapting behavior over time.",
    "author": "Self-Fixing Engineer Team",
    "capabilities": ["self_evolution", "meta_learning_orchestration", "agent_adaptation", "policy_adaptation"],
    "permissions_required": ["llm_access_internal", "meta_learning_access", "policy_write_access"],
    "compatibility": {
        "min_sim_runner_version": "1.0.0",
        "max_sim_runner_version": "2.0.0"
    },
    "entry_points": {
        "initiate_evolution_cycle": {
            "description": "Triggers a self-evolution cycle for specified agents or the entire system.",
            "parameters": ["target_agents", "evolution_strategy", "strategy_params"]
        }
    },
    "health_check": "plugin_health",
    "api_version": "v1",
    "license": "MIT",
    "homepage": "https://www.self-fixing.engineer",
    "tags": ["evolution", "meta_learning", "adaptation", "ai_governance"]
}


# Flag to indicate if core SFE components are available.
SFE_CORE_AVAILABLE = False
try:
    from simulation.agent_core import MetaLearning, PolicyEngine
    SFE_CORE_AVAILABLE = True
except ImportError:
    logger.warning("simulation.agent_core not found. Mocking MetaLearning and PolicyEngine.")
    class MetaLearning:
        """Mock implementation of MetaLearning when the actual component is unavailable."""
        
        def __init__(self, filepath: str = "mock_learnings.jsonl"):
            """Initialize the mock MetaLearning component.
            
            Args:
                filepath: Path to store mock learnings data
            """
            self.filepath = filepath
            self.corrections_log = []
            logger.debug(f"MockMetaLearning initialized with filepath: {filepath}")
            
        async def get_recent_performance_data(
            self, 
            window_days: int, 
            target_agents: Optional[List[str]]
        ) -> List[Dict[str, Any]]:
            """Get mock performance data for the specified window and agents.
            
            Args:
                window_days: Number of days to look back
                target_agents: Optional list of agent IDs to filter by
                
            Returns:
                List of performance data dictionaries
            """
            logger.debug(f"MockMetaLearning: Fetching performance data for {target_agents} over {window_days} days.")
            return []
            
        async def log_correction(
            self, 
            agent_id: str, 
            correction_type: str, 
            details: Dict[str, Any]
        ) -> None:
            """Log a mock correction event.
            
            Args:
                agent_id: ID of the agent being corrected
                correction_type: Type of correction being applied
                details: Additional details about the correction
            """
            log_entry = {
                "timestamp": time.time(), 
                "agent_id": agent_id, 
                "correction_type": correction_type, 
                "details": details
            }
            self.corrections_log.append(log_entry)
            logger.debug(f"MockMetaLearning: Logged correction: {log_entry}")

    class PolicyEngine:
        """Mock implementation of PolicyEngine when the actual component is unavailable."""
        
        def __init__(self, arbiter_instance: Any = None):
            """Initialize the mock PolicyEngine.
            
            Args:
                arbiter_instance: Optional arbiter instance
            """
            self.arbiter_instance = arbiter_instance
            logger.debug("MockPolicyEngine initialized.")
            
        async def health_check(self) -> Dict[str, Any]:
            """Perform a mock health check.
            
            Returns:
                Health status dictionary
            """
            return {"status": "ok", "message": "Mock PolicyEngine is healthy."}
            
        async def update_agent_prompt(self, agent_id: str, new_prompt: str) -> Dict[str, Any]:
            """Mock updating an agent's prompt.
            
            Args:
                agent_id: ID of the agent to update
                new_prompt: New prompt content
                
            Returns:
                Status dictionary
            """
            logger.info(f"MockPolicyEngine: (Action) Updated prompt for {agent_id} to: {new_prompt[:50]}...")
            return {"status": "success", "agent_id": agent_id}
            
        async def update_policy(self, policy_name: str, new_policy_content: Dict[str, Any]) -> Dict[str, Any]:
            """Mock updating a policy.
            
            Args:
                policy_name: Name of the policy to update
                new_policy_content: New policy content
                
            Returns:
                Status dictionary
            """
            logger.info(f"MockPolicyEngine: (Action) Updated policy {policy_name}.")
            return {"status": "success", "policy": policy_name}
            
        async def start_policy_refresher(self) -> None:
            """Mock starting the policy refresher."""
            logger.debug("MockPolicyEngine: Starting policy refresher.")


# --- SFE Core Service Instances (initialized upon use) ---
_meta_learning_instance: Optional[MetaLearning] = None
_policy_engine_instance: Optional[PolicyEngine] = None
_core_llm_instance: Optional[BaseChatModel] = None

async def _get_meta_learning() -> MetaLearning:
    """
    Get or initialize the MetaLearning instance.
    
    Returns:
        MetaLearning: The MetaLearning instance
    """
    global _meta_learning_instance
    if _meta_learning_instance is None:
        if not SFE_CORE_AVAILABLE:
            _meta_learning_instance = MetaLearning(filepath="self_evolution_learnings.jsonl")
            logger.debug("Initialized mock MetaLearning instance")
        else:
            from simulation.agent_core import get_meta_learning_instance
            _meta_learning_instance = get_meta_learning_instance()
            logger.debug("Acquired MetaLearning instance from SFE core")
        
        if prometheus_available:
            EXTERNAL_DEPENDENCY_HEALTH.labels(dependency='meta_learning').set(1)
    return _meta_learning_instance

async def _get_policy_engine() -> PolicyEngine:
    """
    Get or initialize the PolicyEngine instance.
    
    Returns:
        PolicyEngine: The PolicyEngine instance
    """
    global _policy_engine_instance
    if _policy_engine_instance is None:
        if not SFE_CORE_AVAILABLE:
            _policy_engine_instance = PolicyEngine()
            logger.debug("Initialized mock PolicyEngine instance")
        else:
            from simulation.agent_core import get_policy_engine_instance
            _policy_engine_instance = get_policy_engine_instance()
            logger.debug("Acquired PolicyEngine instance from SFE core")
            
            # Start policy refresher if available
            if hasattr(_policy_engine_instance, 'start_policy_refresher') and callable(_policy_engine_instance.start_policy_refresher):
                try:
                    await _policy_engine_instance.start_policy_refresher()
                    logger.debug("Started PolicyEngine refresher")
                except Exception as e:
                    logger.warning(f"Failed to start policy refresher: {e}")
        
        if prometheus_available:
            EXTERNAL_DEPENDENCY_HEALTH.labels(dependency='policy_engine').set(1)
    return _policy_engine_instance

async def _get_core_llm() -> BaseChatModel:
    """
    Get or initialize the Core LLM instance.
    
    Returns:
        BaseChatModel: The Core LLM instance
    """
    global _core_llm_instance
    if _core_llm_instance is None:
        if not SFE_CORE_AVAILABLE:
            try:
                if not langchain_available:
                    raise ImportError("LangChain not available")
                
                openai_api_key = os.getenv("OPENAI_API_KEY")
                if not openai_api_key:
                    raise ValueError("OPENAI_API_KEY environment variable not set for basic LLM fallback.")
                
                _core_llm_instance = ChatOpenAI(
                    model=EVOLUTION_CONFIG.prompt_optimization_model,
                    temperature=EVOLUTION_CONFIG.prompt_optimization_temperature,
                    request_timeout=EVOLUTION_CONFIG.llm_timeout_seconds,
                    api_key=openai_api_key
                )
                logger.info(f"Initialized LangChain LLM with model {EVOLUTION_CONFIG.prompt_optimization_model}")
                
            except (ImportError, ValueError) as e:
                logger.warning(f"Failed to initialize LangChain LLM: {e}. Using mock LLM.")
                
                class FallbackMockLLM(BaseChatModel):
                    """Mock LLM implementation for when no real LLM is available."""
                    
                    def __init__(self):
                        self.model_name = "fallback_mock_llm"
                    
                    async def ainvoke(self, messages: List[Any], **kwargs: Any) -> Any:
                        """Mock LLM invocation."""
                        return type('MockResponse', (object,), {'content': "Fallback mock LLM response."})()
                
                _core_llm_instance = FallbackMockLLM()
                logger.debug("Using fallback mock LLM")
                
            except Exception as e:
                logger.error(f"Failed to initialize LLM: {e}", exc_info=True)
                raise
        else:
            try:
                from simulation.agent_core import init_llm as get_core_sfe_llm
                _core_llm_instance = get_core_sfe_llm(
                    provider="openai",
                    model=EVOLUTION_CONFIG.prompt_optimization_model,
                    temperature=EVOLUTION_CONFIG.prompt_optimization_temperature,
                    timeout=EVOLUTION_CONFIG.llm_timeout_seconds
                )
            except Exception as e:
                logger.error(f"Failed to initialize core SFE LLM: {e}", exc_info=True)
                raise
        
        if prometheus_available:
            EXTERNAL_DEPENDENCY_HEALTH.labels(dependency='llm').set(1)
            
    return _core_llm_instance


# --- Audit Logging ---
try:
    from simulation.audit_log import AuditLogger as SFE_AuditLogger
    _sfe_audit_logger = SFE_AuditLogger.from_environment()
    logger.info("Using SFE AuditLogger for audit events")
except ImportError:
    logger.warning("SFE AuditLogger not found. Audit events will be logged to plugin's logger only.")
    class MockAuditLogger:
        """Mock AuditLogger when the actual component is unavailable."""
        
        async def log(self, event_type: str, details: Dict[str, Any], **kwargs: Any) -> None:
            """Log a mock audit event.
            
            Args:
                event_type: Type of event being audited
                details: Event details
                **kwargs: Additional keyword arguments
            """
            logger.info(f"[AUDIT_MOCK] {event_type}: {details}")
    
    _sfe_audit_logger = MockAuditLogger()

# Add these patterns for secret detection
SECRET_PATTERNS = [
    (r'sk-[a-zA-Z0-9]{20,}', '[REDACTED_API_KEY]'),  # OpenAI API keys
    (r'sk_test_[a-zA-Z0-9]{20,}', '[REDACTED_TEST_API_KEY]'),  # Stripe test keys
    (r'sk_live_[a-zA-Z0-9]{20,}', '[REDACTED_LIVE_API_KEY]'),  # Stripe live keys
    (r'xoxb-[0-9]{10,}-[0-9]{10,}-[a-zA-Z0-9]{24}', '[REDACTED_SLACK_BOT_TOKEN]'),  # Slack bot tokens
    (r'ghp_[a-zA-Z0-9]{36}', '[REDACTED_GITHUB_TOKEN]'),  # GitHub personal access tokens
    (r'gho_[a-zA-Z0-9]{36}', '[REDACTED_GITHUB_OAUTH_TOKEN]'),  # GitHub OAuth tokens
    (r'[A-Za-z0-9+/]{40}={0,2}', '[REDACTED_BASE64_SECRET]'),  # Generic base64 secrets
    (r'password["\s]*[:=]["\s]*[^"\s,}]+', 'password": "[REDACTED_PASSWORD]'),  # Passwords in JSON-like strings
    (r'token["\s]*[:=]["\s]*[^"\s,}]+', 'token": "[REDACTED_TOKEN]'),  # Tokens in JSON-like strings
]

def _scrub_secrets(data: Any) -> Any:
    """
    Recursively scrub secrets from data structures.
    
    Args:
        data: The data to scrub (dict, list, str, or other)
        
    Returns:
        Data with secrets redacted
    """
    if isinstance(data, dict):
        scrubbed = {}
        for key, value in data.items():
            # Check if key suggests a secret
            key_lower = key.lower()
            if any(secret_word in key_lower for secret_word in ['password', 'secret', 'key', 'token', 'auth']):
                if isinstance(value, str) and len(value) > 8:
                    scrubbed[key] = '[REDACTED_SECRET]'
                else:
                    scrubbed[key] = value
            else:
                scrubbed[key] = _scrub_secrets(value)
        return scrubbed
    elif isinstance(data, list):
        return [_scrub_secrets(item) for item in data]
    elif isinstance(data, str):
        scrubbed_str = data
        for pattern, replacement in SECRET_PATTERNS:
            scrubbed_str = re.sub(pattern, replacement, scrubbed_str, flags=re.IGNORECASE)
        return scrubbed_str
    else:
        return data

async def _audit_event(event_type: str, details: Dict[str, Any]):
    """
    Log an audit event with secret scrubbing.
    
    Args:
        event_type: Type of the audit event
        details: Event details (will be scrubbed of secrets)
    """
    try:
        # Scrub secrets from details before logging
        scrubbed_details = _scrub_secrets(details.copy() if details else {})
        
        # Log to SFE audit logger if available
        if _sfe_audit_logger:
            await _sfe_audit_logger.log(event_type, scrubbed_details)
        else:
            # Fallback to plugin logger
            logger.info(f"AUDIT_EVENT: {event_type}", extra={"details": scrubbed_details})
    
    except Exception as e:
        logger.error(f"Failed to log audit event {event_type}: {e}")

async def _check_content_safety(content: str) -> Tuple[bool, str]:
    """
    Check if content is safe for execution/deployment.
    
    Args:
        content: The content to check
        
    Returns:
        Tuple of (is_safe: bool, reason: str)
    """
    try:
        # Add your actual content safety logic here
        # For now, implement basic checks or integrate with your safety system
        
        # Example basic checks:
        dangerous_patterns = [
            'rm -rf',
            'del /f /s /q',
            'format c:',
            'DROP TABLE',
            'eval(',
            'exec(',
            '__import__',
        ]
        
        content_lower = content.lower()
        for pattern in dangerous_patterns:
            if pattern in content_lower:
                return False, f"Content contains potentially dangerous pattern: {pattern}"
        
        return True, "Content appears safe"
    except Exception as e:
        # If safety check fails, err on the side of caution
        return False, f"Safety check failed: {str(e)}"

# --- Cache Management ---
async def cache_performance_data(data: List[Dict[str, Any]]) -> bool:
    """
    Cache performance data to Redis if available.
    
    Args:
        data: Performance data to cache
        
    Returns:
        bool: Whether the caching was successful
    """
    if not redis_available or not EVOLUTION_CONFIG.redis_cache_url:
        return False
    
    try:
        async with get_redis_client(EVOLUTION_CONFIG.redis_cache_url) as redis_client:
            if not redis_client:
                return False
                
            cache_key = f"evolution_feedback:{time.time()}"
            await redis_client.set(
                cache_key, 
                json.dumps(data), 
                ex=EVOLUTION_CONFIG.redis_cache_ttl
            )
            logger.info(f"Cached performance data to Redis with key {cache_key}")
            return True
    except Exception as e:
        logger.error(f"Failed to cache performance data to Redis: {e}", exc_info=True)
        return False


async def get_cached_performance_data() -> Optional[List[Dict[str, Any]]]:
    """
    Retrieve cached performance data from Redis if available.
    
    Returns:
        Optional[List[Dict[str, Any]]]: Cached performance data or None if unavailable
    """
    if not redis_available or not EVOLUTION_CONFIG.redis_cache_url:
        return None
    
    try:
        async with get_redis_client(EVOLUTION_CONFIG.redis_cache_url) as redis_client:
            if not redis_client:
                return None
                
            # Get the most recent cache key
            keys = await redis_client.keys("evolution_feedback:*")
            if not keys:
                if prometheus_available:
                    CACHE_MISSES.inc()
                return None
                
            # Sort by timestamp (extract from key)
            keys.sort(key=lambda k: float(k.split(":")[-1]))
            latest_key = keys[-1]
            
            cached_data = await redis_client.get(latest_key)
            if not cached_data:
                if prometheus_available:
                    CACHE_MISSES.inc()
                return None
                
            if prometheus_available:
                CACHE_HITS.inc()
                
            logger.info(f"Retrieved cached performance data with key {latest_key}")
            return json.loads(cached_data)
    except Exception as e:
        logger.error(f"Failed to retrieve cached performance data: {e}", exc_info=True)
        return None


# --- Input Validation ---
def validate_agents(agents: List[str]) -> List[str]:
    """
    Validates agent names to prevent injection and ensure they meet naming requirements.
    
    Args:
        agents: List of agent names to validate
        
    Returns:
        List[str]: List of valid agent names
    """
    if not agents:
        return []
        
    valid_agents = []
    for agent in agents:
        # Sanitize agent names to prevent injection
        if isinstance(agent, str) and re.match(r'^[a-zA-Z0-9_\-\.]+$', agent):
            valid_agents.append(agent)
        else:
            logger.warning(f"Invalid agent name rejected: {agent}")
            
    return valid_agents


def validate_evolution_strategy(strategy: str) -> str:
    """
    Validate that the requested evolution strategy is supported.
    
    Args:
        strategy: The strategy name to validate
        
    Returns:
        str: The validated strategy name or default if invalid
    """
    supported_strategies = ["prompt_optimization"]
    
    if strategy in supported_strategies:
        return strategy
    else:
        logger.warning(f"Unsupported evolution strategy: {strategy}, using default")
        return EVOLUTION_CONFIG.default_evolution_strategy


def validate_strategy_params(params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Validate strategy parameters to prevent injection or invalid values.
    
    Args:
        params: Dictionary of strategy parameters
        
    Returns:
        Dict[str, Any]: Validated parameters
    """
    if not params:
        return {}
        
    # Clone to avoid modifying the original
    validated_params = {}
    
    # Validate optimization_scope
    if 'optimization_scope' in params:
        valid_scopes = ["system_prompt", "user_instructions", "tool_descriptions"]
        scope = params['optimization_scope']
        if isinstance(scope, str) and scope in valid_scopes:
            validated_params['optimization_scope'] = scope
        else:
            logger.warning(f"Invalid optimization_scope: {scope}")
    
    # Validate feedback_window_days
    if 'feedback_window_days' in params:
        days = params['feedback_window_days']
        if isinstance(days, (int, float)) and 1 <= days <= 90:
            validated_params['feedback_window_days'] = int(days)
        else:
            logger.warning(f"Invalid feedback_window_days: {days}")
    
    # Validate other numeric parameters
    for param_name in ['min_confidence', 'max_iterations']:
        if param_name in params:
            value = params[param_name]
            if isinstance(value, (int, float)) and 0 <= value <= 100:
                validated_params[param_name] = value
            else:
                logger.warning(f"Invalid {param_name}: {value}")
    
    # Remove any unexpected parameters
    for key in params:
        if key not in validated_params and key not in ['optimization_scope', 'feedback_window_days', 
                                                       'min_confidence', 'max_iterations']:
            logger.warning(f"Unexpected parameter ignored: {key}")
    
    return validated_params


# --- PLUGIN HEALTH CHECK ---
async def plugin_health() -> Dict[str, Any]:
    """
    Check the health of the plugin and its dependencies.
    
    Returns:
        Dict[str, Any]: Health status information
    """
    status = "ok"
    details = []
    checks_failed = 0
    start_time = time.monotonic()
    
    # Check MetaLearning
    try:
        await _get_meta_learning()
        details.append("MetaLearning component accessible.")
        if prometheus_available:
            EXTERNAL_DEPENDENCY_HEALTH.labels(dependency='meta_learning').set(1)
    except Exception as e:
        status = "error"
        checks_failed += 1
        error_msg = f"MetaLearning component inaccessible: {e}."
        details.append(error_msg)
        logger.error(error_msg, exc_info=True)
        if prometheus_available:
            EXTERNAL_DEPENDENCY_HEALTH.labels(dependency='meta_learning').set(0)
    
    # Check PolicyEngine
    try:
        policy_engine = await _get_policy_engine()
        if policy_engine and hasattr(policy_engine, 'health_check') and callable(policy_engine.health_check):
            policy_health = await policy_engine.health_check()
            details.append(f"PolicyEngine accessible and healthy: {policy_health.get('status', 'OK')}.")
            if policy_health.get('status', 'ok').lower() != 'ok':
                status = "degraded"
                checks_failed += 0.5
        else:
            details.append("PolicyEngine accessible (basic check).")
        
        if prometheus_available:
            EXTERNAL_DEPENDENCY_HEALTH.labels(dependency='policy_engine').set(1)
    except Exception as e:
        status = "error"
        checks_failed += 1
        error_msg = f"PolicyEngine component inaccessible: {e}."
        details.append(error_msg)
        logger.error(error_msg, exc_info=True)
        if prometheus_available:
            EXTERNAL_DEPENDENCY_HEALTH.labels(dependency='policy_engine').set(0)
    
    # Check LLM
    try:
        llm_instance = await _get_core_llm()
        if llm_instance:
            details.append(f"Core LLM instance accessible: {llm_instance.model_name}.")
            
            # Perform a lightweight test of the LLM
            llm_test_start = time.monotonic()
            test_messages = [
                SystemMessage(content="You are a test bot."), 
                HumanMessage(content="ping")
            ]
            
            try:
                test_response = await asyncio.wait_for(
                    llm_instance.ainvoke(test_messages, config={"timeout": 5}),
                    timeout=7  # Slightly longer than the request timeout
                )
                
                llm_test_duration = time.monotonic() - llm_test_start
                if prometheus_available:
                    LLM_REQUEST_DURATION.labels(model=getattr(llm_instance, 'model_name', 'unknown')).observe(llm_test_duration)
                
                if hasattr(test_response, 'content') and test_response.content and len(test_response.content) > 0:
                    details.append(f"LLM inference test successful (took {llm_test_duration:.2f}s).")
                    if prometheus_available:
                        EXTERNAL_DEPENDENCY_HEALTH.labels(dependency='llm').set(1)
                else:
                    status = "degraded"
                    checks_failed += 0.5
                    details.append("LLM inference test returned empty response.")
                    if prometheus_available:
                        EXTERNAL_DEPENDENCY_HEALTH.labels(dependency='llm').set(0.5)
            except asyncio.TimeoutError:
                status = "degraded"
                checks_failed += 0.5
                details.append("LLM inference test timed out.")
                if prometheus_available:
                    EXTERNAL_DEPENDENCY_HEALTH.labels(dependency='llm').set(0.5)
            except Exception:
                status = "degraded"
                checks_failed += 0.5
                details.append("LLM inference test failed with exception.")
                if prometheus_available:
                    EXTERNAL_DEPENDENCY_HEALTH.labels(dependency='llm').set(0.5)
        else:
            status = "error"
            checks_failed += 1
            details.append("Core LLM instance could not be acquired.")
            if prometheus_available:
                EXTERNAL_DEPENDENCY_HEALTH.labels(dependency='llm').set(0)
    except Exception as e:
        status = "error"
        checks_failed += 1
        error_msg = f"Core LLM inaccessible or test failed: {e}. Check API keys, model name, network."
        details.append(error_msg)
        logger.error(error_msg, exc_info=True)
        if prometheus_available:
            EXTERNAL_DEPENDENCY_HEALTH.labels(dependency='llm').set(0)
    
    # Check Redis if configured
    if redis_available and EVOLUTION_CONFIG.redis_cache_url:
        try:
            async with get_redis_client(EVOLUTION_CONFIG.redis_cache_url) as redis_client:
                if redis_client:
                    await redis_client.ping()
                    details.append("Redis cache accessible.")
                    if prometheus_available:
                        EXTERNAL_DEPENDENCY_HEALTH.labels(dependency='redis').set(1)
        except Exception as e:
            status = "degraded"  # Redis is optional, so just degraded
            checks_failed += 0.5
            error_msg = f"Redis cache inaccessible: {e}."
            details.append(error_msg)
            logger.warning(error_msg, exc_info=True)
            if prometheus_available:
                EXTERNAL_DEPENDENCY_HEALTH.labels(dependency='redis').set(0)
    
    # Overall status determination
    if checks_failed >= 1:
        status = "error"
    elif checks_failed > 0:
        status = "degraded"
    
    # Add version info and check duration
    details.append(f"Plugin version: {__version__}")
    check_duration = time.monotonic() - start_time
    details.append(f"Health check completed in {check_duration:.2f}s.")
    
    logger.info(f"Plugin health check: {status} ({check_duration:.2f}s)")
    return {
        "status": status, 
        "details": details,
        "timestamp": time.time(),
        "version": __version__
    }


# --- EVOLUTION STRATEGIES ---
async def _strategy_prompt_optimization(
    meta_learning: MetaLearning,
    core_llm: BaseChatModel,
    target_agents: Optional[List[str]] = None,
    **strategy_params: Any
) -> Dict[str, Any]:
    """
    Analyzes agent performance data from MetaLearning and uses LLM to
    generate optimized prompts or behavior adjustments.
    
    Args:
        meta_learning: MetaLearning instance to get performance data
        core_llm: LLM instance for optimization
        target_agents: Optional list of agent IDs to optimize
        **strategy_params: Additional parameters for the strategy
        
    Returns:
        Dict[str, Any]: Result of the optimization strategy
    """
    optimization_scope = strategy_params.get("optimization_scope", "system_prompt")
    feedback_window_days = strategy_params.get("feedback_window_days", EVOLUTION_CONFIG.evolution_data_window_days)
    
    # Try to get cached data first
    recent_feedback = await get_cached_performance_data()
    
    # If no cached data, fetch from MetaLearning
    if not recent_feedback:
        logger.info(f"No cached performance data found. Fetching from MetaLearning for {feedback_window_days} days.")
        
        if hasattr(meta_learning, 'get_recent_performance_data') and callable(meta_learning.get_recent_performance_data):
            try:
                recent_feedback = await meta_learning.get_recent_performance_data(
                    window_days=feedback_window_days, 
                    target_agents=target_agents
                )
                logger.info(f"Retrieved {len(recent_feedback)} performance data points from MetaLearning")
                
                # Cache the fetched data
                await cache_performance_data(recent_feedback)
            except Exception as e:
                logger.error(f"Failed to get performance data from MetaLearning: {e}", exc_info=True)
                recent_feedback = []
        else:
            logger.warning("MetaLearning.get_recent_performance_data not available. Simulating performance data.")
            recent_feedback = [
                {"agent_id": "mock_agent_1", "metric": "pass_rate", "value": 0.7, "timestamp": time.time() - 86400 * 2, 
                 "feedback": "good", "context": "Fixing simple bugs"},
                {"agent_id": "mock_agent_1", "metric": "pass_rate", "value": 0.65, "timestamp": time.time(), 
                 "feedback": "bad", "context": "Fixing complex security issues, high LLM hallucination"},
                {"agent_id": "mock_agent_2", "metric": "accuracy", "value": 0.90, "timestamp": time.time() - 86400 * 1, 
                 "feedback": "neutral", "context": "Generating test cases"},
            ]
            
            # Cache the simulated data
            await cache_performance_data(recent_feedback)
    else:
        logger.info(f"Using {len(recent_feedback)} cached performance data points")
    
    if not recent_feedback:
        return {
            "status": "skipped", 
            "reason": "No recent performance data available to inform prompt optimization."
        }
    
    # Filter data for target agents if specified
    if target_agents:
        target_agents_set = set(target_agents)
        filtered_feedback = [f for f in recent_feedback if f.get("agent_id") in target_agents_set]
        
        if not filtered_feedback:
            return {
                "status": "skipped", 
                "reason": f"No performance data found for specified agents: {', '.join(target_agents)}"
            }
        
        recent_feedback = filtered_feedback

    # Prepare performance data for the LLM
    # Scrub any potentially sensitive data
    performance_summary_for_llm = _scrub_secrets(json.dumps(recent_feedback, indent=2))
    
    # Limit the size to avoid exceeding context limits
    max_feedback_chars = 4000
    if len(performance_summary_for_llm) > max_feedback_chars:
        logger.warning(f"Performance data exceeds {max_feedback_chars} chars, truncating")
        performance_summary_for_llm = performance_summary_for_llm[:max_feedback_chars] + "\n... (truncated)"
    
    # Create detailed prompt template for optimization
    prompt_template_str = """
        You are an AI system designed to self-optimize and improve. Your task is to analyze
        the provided performance feedback for SFE agents and propose an optimized version
        of their system prompt or persona. The goal is to enhance their effectiveness,
        address shortcomings (e.g., hallucination, inefficiency, failure rates), and
        align behavior with desired outcomes.

        Focus on the following optimization goals:
        1. Clarity - Make the prompt clear and unambiguous
        2. Precision - Provide specific guidance for the agent's behavior
        3. Error reduction - Address identified failure patterns
        4. Alignment - Ensure the prompt aligns with desired outcomes
        5. Efficiency - Help the agent complete tasks effectively

        Performance Feedback (from MetaLearning, last {feedback_window_days} days):
        ```json
        {performance_data}
        ```

        Current Agent Prompt/Persona to optimize:
        "You are a helpful and efficient AI assistant. Your goal is to accurately and reliably
        assist in software engineering tasks, providing clear and concise information.
        Always prioritize safety and correctness. If unsure, ask for clarification or state limitations."

        Based on the above feedback, provide ONLY the proposed NEW system prompt or persona.
        If no change is needed, respond with "NO_CHANGE".
        Do NOT include any explanations or conversational text.
        """

    prompt_template = PromptTemplate.from_template(prompt_template_str)
    prompt_text = prompt_template.format(
        feedback_window_days=feedback_window_days,
        performance_data=performance_summary_for_llm,
        current_agent_prompt=""
    )
    
    proposed_changes: List[Dict[str, Any]] = []
    target_agent_id = target_agents[0] if target_agents else "system_default_prompt"

    try:
        # Measure LLM request time for metrics
        llm_start_time = time.monotonic()
        
        llm_response = await asyncio.wait_for(
            core_llm.ainvoke(
                messages=[HumanMessage(content=prompt_text)],
                config={"timeout": EVOLUTION_CONFIG.llm_timeout_seconds}
            ),
            timeout=EVOLUTION_CONFIG.llm_timeout_seconds + 5  # Add buffer for networking
        )
        
        llm_duration = time.monotonic() - llm_start_time
        if prometheus_available:
            LLM_REQUEST_DURATION.labels(model=getattr(core_llm, 'model_name', 'unknown')).observe(llm_duration)
            
        optimized_prompt_content = llm_response.content.strip()
        logger.info(f"LLM optimization completed in {llm_duration:.2f}s")

        if not optimized_prompt_content or optimized_prompt_content == "NO_CHANGE":
            logger.info(f"LLM proposed no change for prompt optimization for {target_agent_id}.")
            return {
                "status": "no_change_proposed", 
                "reason": "LLM determined no prompt optimization was needed."
            }
        
        # Perform content safety check
        is_safe, safety_reason = await _check_content_safety(optimized_prompt_content)
        if not is_safe:
            logger.warning(f"Optimized prompt failed safety check: {safety_reason}")
            return {
                "status": "unsafe_content",
                "reason": f"Generated content failed safety check: {safety_reason}"
            }
        
        proposed_changes.append({
            "agent_id": target_agent_id,
            "change_type": "prompt_update",
            "old_value": "Current general agent prompt/persona (as in prompt)",
            "new_value": optimized_prompt_content,
            "reasoning": f"Optimized based on {len(recent_feedback)} recent feedback points and the LLM's analysis."
        })
        
        if prometheus_available: 
            ADAPTATION_TYPES.labels(type='prompt_update').inc()
            
        logger.info(f"Proposed prompt optimization for {target_agent_id}.")
        return {
            "status": "success", 
            "proposed_changes": proposed_changes,
            "performance_data_points": len(recent_feedback),
            "optimization_duration_seconds": llm_duration
        }
    except asyncio.TimeoutError:
        logger.error(f"LLM request timed out after {EVOLUTION_CONFIG.llm_timeout_seconds}s")
        if prometheus_available:
            EVOLUTION_ERRORS.labels(error_type="llm_timeout").inc()
        return {
            "status": "timeout", 
            "reason": f"LLM request timed out after {EVOLUTION_CONFIG.llm_timeout_seconds}s"
        }
    except Exception as e:
        logger.error(f"Prompt optimization strategy failed during LLM invocation: {e}", exc_info=True)
        if prometheus_available:
            EVOLUTION_ERRORS.labels(error_type=type(e).__name__).inc()
        raise


# --- Function to wrap async functions with better error handling ---
def with_enhanced_error_handling(func: AsyncFunc) -> AsyncFunc:
    """
    Decorator to enhance error handling for async functions.
    Captures and logs exceptions, and ensures proper cleanup.
    
    Args:
        func: The async function to wrap
        
    Returns:
        AsyncFunc: Wrapped function with enhanced error handling
    """
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        function_name = func.__name__
        try:
            return await func(*args, **kwargs)
        except RetryError as e:
            # This is thrown by tenacity after max retries
            logger.error(f"Function {function_name} exhausted all retry attempts: {e}")
            inner_exception = e.__cause__ if hasattr(e, '__cause__') and e.__cause__ else e
            error_type = type(inner_exception).__name__
            
            if prometheus_available:
                EVOLUTION_ERRORS.labels(error_type=f"retry_exhausted_{error_type}").inc()
                
            # Re-raise with more context
            raise type(e)(f"{function_name} failed after max retries: {inner_exception}") from e
        except asyncio.TimeoutError:
            logger.error(f"Function {function_name} timed out")
            if prometheus_available:
                EVOLUTION_ERRORS.labels(error_type="timeout").inc()
            raise
        except Exception as e:
            logger.error(f"Unexpected error in {function_name}: {e}", exc_info=True)
            if prometheus_available:
                EVOLUTION_ERRORS.labels(error_type=type(e).__name__).inc()
            raise
    return wrapper


# --- Function to setup retry logic if tenacity is available ---
def _setup_retry(func: AsyncFunc) -> AsyncFunc:
    """
    Set up retry logic if tenacity is available.
    
    Args:
        func: Function to apply retries to
        
    Returns:
        AsyncFunc: Function with retry logic if available, otherwise original function
    """
    if not tenacity_available or EVOLUTION_CONFIG.max_evolution_retries <= 0:
        return func
        
    # Add detailed retry logging
    @retry(
        stop=stop_after_attempt(EVOLUTION_CONFIG.max_evolution_retries),
        wait=wait_random_exponential(
            multiplier=EVOLUTION_CONFIG.retry_backoff_factor, 
            min=1, 
            max=10
        ),
        retry=retry_if_exception_type((Exception)),
        before=before_log(logger, logging.INFO),
        after=after_log(logger, logging.INFO),
    )
    @functools.wraps(func)
    async def with_retry(*args, **kwargs):
        return await func(*args, **kwargs)
        
    return with_retry

@with_enhanced_error_handling
@_setup_retry
async def initiate_evolution_cycle(
    target_agents: Optional[List[str]] = None,
    evolution_strategy: str = EVOLUTION_CONFIG.default_evolution_strategy,
    strategy_params: Optional[Dict[str, Any]] = None,
    **kwargs: Any
) -> Dict[str, Any]:
    """
    Initiate a self-evolution cycle to improve agent behavior.
    
    Args:
        target_agents: Optional list of agent IDs to evolve
        evolution_strategy: Strategy to use for evolution
        strategy_params: Parameters for the evolution strategy
        **kwargs: Additional parameters
        
    Returns:
        Dict[str, Any]: Results of the evolution cycle
    """
    evolution_id = f"sfe-evo-{uuid.uuid4().hex[:8]}"
    start_time = time.monotonic()
    
    if prometheus_available: 
        EVOLUTION_CYCLES_TOTAL.inc()
    
    result: Dict[str, Any] = {
        "success": False,
        "evolution_id": evolution_id,
        "strategy_used": evolution_strategy,
        "proposed_adaptations": [],
        "applied_adaptations": [],
        "status_reason": "Evolution cycle failed.",
        "error": None,
        "duration_seconds": 0.0,
        "timestamp": time.time()
    }
    
    try:
        # Validate inputs
        if target_agents:
            target_agents = validate_agents(target_agents)
            
        if not target_agents:
            logger.info("No valid target agents provided. Running evolution for default system prompt.")
        
        # Validate strategy
        validated_strategy = validate_evolution_strategy(evolution_strategy)
        if validated_strategy != evolution_strategy:
            result["strategy_used"] = validated_strategy
            logger.info(f"Changed strategy from {evolution_strategy} to {validated_strategy}")
        
        # Validate strategy params
        validated_params = validate_strategy_params(strategy_params)
        
        # Get required components
        meta_learning = await _get_meta_learning()
        core_llm = await _get_core_llm()
        policy_engine = await _get_policy_engine()

        if not meta_learning or not core_llm or not policy_engine:
            raise RuntimeError("Core SFE components not initialized.")

        # Select and apply strategy
        strategy_function: Callable = None
        if validated_strategy == "prompt_optimization":
            strategy_function = _strategy_prompt_optimization
        else:
            raise ValueError(f"Unsupported evolution strategy: {validated_strategy}")

        if not strategy_function:
            raise RuntimeError(f"Evolution strategy function not resolved for: {validated_strategy}")

        logger.info(f"Applying evolution strategy '{validated_strategy}' for evolution cycle {evolution_id}...")
        
        # Execute the evolution strategy
        adaptations_result = await strategy_function(
            meta_learning=meta_learning,
            core_llm=core_llm,
            target_agents=target_agents,
            **validated_params
        )
        
        result["proposed_adaptations"] = adaptations_result.get("proposed_changes", [])
        
        # Process adaptations based on strategy result status
        if adaptations_result["status"] == "success" and result["proposed_adaptations"]:
            applied_adaptations = []
            
            # Apply each adaptation
            for adaptation in result["proposed_adaptations"]:
                try:
                    if adaptation["change_type"] == "prompt_update":
                        # Apply prompt update to agent
                        await policy_engine.update_agent_prompt(
                            adaptation["agent_id"], 
                            adaptation["new_value"]
                        )
                        logger.info(f"Applied prompt update for {adaptation['agent_id']}.")
                        applied_adaptations.append(adaptation["agent_id"])
                        
                        # Log adaptation details to meta-learning
                        await meta_learning.log_correction(
                            agent_id=adaptation["agent_id"],
                            correction_type="prompt_optimization",
                            details={
                                "evolution_id": evolution_id,
                                "change_type": adaptation["change_type"],
                                "reasoning": adaptation["reasoning"],
                                "timestamp": time.time()
                            }
                        )
                        
                        if prometheus_available:
                            EVOLUTION_ADAPTATIONS_SUCCESS.labels(
                                evolution_id=evolution_id,
                                adaptation_type=adaptation["change_type"]
                            ).inc()
                except Exception as e:
                    logger.error(f"Failed to apply adaptation for {adaptation['agent_id']}: {e}", exc_info=True)
                    if prometheus_available:
                        EVOLUTION_ERRORS.labels(error_type="adaptation_application_failure").inc()
            
            result["applied_adaptations"] = applied_adaptations
            result["success"] = len(applied_adaptations) > 0
            result["status_reason"] = (
                f"Evolution cycle completed successfully. "
                f"{len(applied_adaptations)}/{len(result['proposed_adaptations'])} adaptations applied."
            )
            logger.info(
                f"Evolution cycle {evolution_id} SUCCEEDED. "
                f"{len(applied_adaptations)}/{len(result['proposed_adaptations'])} adaptations applied."
            )
        elif adaptations_result["status"] in ["no_change_proposed", "skipped", "unsafe_content"]:
            result["success"] = True
            result["status_reason"] = adaptations_result.get("reason", "Evolution cycle completed without adaptations.")
            logger.info(f"Evolution cycle {evolution_id} COMPLETED: {result['status_reason']}")
        else:
            result["error"] = f"Strategy returned unexpected status: {adaptations_result['status']}"
            logger.warning(f"Evolution cycle {evolution_id}: Strategy returned unexpected status: {adaptations_result['status']}")
    except Exception as e:
        result["error"] = str(e)
        logger.error(f"Error during evolution cycle {evolution_id}: {e}", exc_info=True)
        if prometheus_available:
            EVOLUTION_ERRORS.labels(error_type=type(e).__name__).inc()
    finally:
        result["duration_seconds"] = time.monotonic() - start_time
        if prometheus_available:
            EVOLUTION_CYCLE_LATENCY_SECONDS.observe(result["duration_seconds"])
        
        # Log audit event
        await _audit_event(
            "self_evolution_cycle_completed",
            {
                "evolution_id": evolution_id,
                "success": result["success"],
                "strategy_used": result["strategy_used"],
                "status_reason": result["status_reason"],
                "error": result["error"],
                "applied_adaptations_count": len(result["applied_adaptations"]),
                "proposed_adaptations_count": len(result["proposed_adaptations"]),
                "duration_seconds": result["duration_seconds"]
            }
        )
        return result

def register_plugin_entrypoints(register_func: Callable) -> None:
    """
    Register plugin entry points with the SFE system.
    
    Args:
        register_func: Function to register the plugin entry points
    """
    logger.info("Registering SelfEvolutionPlugin entrypoints...")
    register_func(
        name="initiate_evolution_cycle",
        executor_func=initiate_evolution_cycle,
        capabilities=["self_evolution", "meta_learning_orchestration", "agent_adaptation"]
    )

if __name__ == "__main__":
    # For standalone testing
    async def test_plugin():
        health = await plugin_health()
        print(f"Plugin health: {health['status']}")
        print("Details:")
        for detail in health['details']:
            print(f"  - {detail}")
            
        print("\nTesting evolution cycle...")
        result = await initiate_evolution_cycle(
            target_agents=["test_agent"],
            evolution_strategy="prompt_optimization"
        )
        print(f"Evolution result: {json.dumps(result, indent=2)}")
        
    asyncio.run(test_plugin())