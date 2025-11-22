# Restored on August 20, 2025
import logging
import json
import uuid
import time
import re
import asyncio
import hashlib
import sys
from abc import ABC, abstractmethod
from typing import Dict, Any, Tuple, List, Optional
from contextlib import asynccontextmanager

# Python version compatibility for asyncio.timeout
if sys.version_info >= (3, 11):
    from asyncio import timeout as async_timeout
else:
    # Provide a fallback that raises error only when used
    from contextlib import asynccontextmanager
    
    @asynccontextmanager
    async def async_timeout(seconds):
        """Fallback that raises error when timeout is used with Python < 3.11"""
        raise RuntimeError(
            "asyncio.timeout is not available in Python < 3.11. "
            "Please upgrade to Python 3.11 or higher."
        )
        yield  # pragma: no cover (unreachable)

# Langchain imports
from langchain.memory import ConversationBufferWindowMemory
from langchain.chains import ConversationChain
from langchain_core.prompts import PromptTemplate
from langchain_core.messages import messages_from_dict, messages_to_dict, HumanMessage

# LLM Provider Imports
try:
    from langchain_community.chat_models import ChatXAI
except ImportError:
    logging.getLogger(__name__).warning("Warning: ChatXAI not found in langchain_community. This provider will not be available.")
    ChatXAI = None

from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic
try:
    from langchain_google_genai import ChatGoogleGenerativeAI
except ImportError:
    logging.getLogger(__name__).warning("Warning: langchain_google_genai not found. Google LLM provider via LangChain will not be available.")
    ChatGoogleGenerativeAI = None

# For MetaLearning model persistence
import pickle
from ratelimit import limits, sleep_and_retry
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.exceptions import NotFittedError

# For JWT Validation
try:
    import jwt
except ImportError:
    logging.getLogger(__name__).warning("Warning: PyJWT not found. JWT validation will be disabled.")
    jwt = None

# For Postgres client
try:
    import asyncpg
    class PostgresClient:
        def __init__(self, db_url):
            self.db_url = db_url
            self.pool = None
        async def connect(self):
            if not self.pool:
                self.pool = await asyncpg.create_pool(self.db_url)
            async with self.pool.acquire() as conn:
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS agent_states (
                        session_id TEXT PRIMARY KEY,
                        state TEXT
                    )
                """)
            logger.info("Postgres client connection pool created and schema ensured.")

        async def save(self, table: str, data: Dict[str, Any]):
            async with self.pool.acquire() as conn:
                await conn.execute(
                    f"INSERT INTO {table} (session_id, state) VALUES ($1, $2) ON CONFLICT (session_id) DO UPDATE SET state = $2",
                    data["session_id"], data["state"]
                )

        async def load(self, table: str, session_id: str) -> Optional[Dict[str, Any]]:
            async with self.pool.acquire() as conn:
                result = await conn.fetchrow(f"SELECT state FROM {table} WHERE session_id = $1", session_id)
                return {"state": result["state"]} if result else None

except ImportError:
    logging.getLogger(__name__).warning("Warning: asyncpg not found. Postgres client functionality will be unavailable.")
    PostgresClient = None
    
# Local imports
from .config import Config, load_persona_dict, SensitiveValue, MultiModalData
from .utils import (
    async_with_retry, _sanitize_user_input, _sanitize_context,
    AgentErrorCode, AgentCoreException, AGENT_METRICS, trace_id_var,
    logger
)
from .prompt_strategies import PromptStrategy, DefaultPromptStrategy, ConcisePromptStrategy, \
                               BASE_AGENT_PROMPT_TEMPLATE, REFLECTION_PROMPT_TEMPLATE, CRITIQUE_PROMPT_TEMPLATE, SELF_CORRECT_PROMPT_TEMPLATE
from .multimodal import MultiModalProcessor, DefaultMultiModalProcessor

# Imports for StateBackends and AuditLedgerClient
try:
    from arbiter.models.redis_client import RedisClient
    from arbiter.models.audit_ledger_client import AuditLedgerClient
except ImportError:
    logger.warning("Warning: Could not import one or more client classes for state and audit logging. Falling back to mocks.")
    RedisClient = None
    class DummyAuditLedgerClient:
        def __init__(self, *args, **kwargs):
            self._logger = logging.getLogger("DummyAuditLedgerClient")
            self._logger.warning("Using dummy AuditLedgerClient.")
        async def log_event(self, *args, **kwargs):
            self._logger.info("Dummy log_event called. Event not recorded.")
            return True
    AuditLedgerClient = DummyAuditLedgerClient

from opentelemetry import trace
tracer = trace.get_tracer(__name__)

class StateBackend(ABC):
    """
    Abstract Base Class for state backends.
    Expected state is a JSON-serializable dictionary with keys like 'history', 'persona', 'language'.
    """
    @abstractmethod
    async def load_state(self, session_id: str) -> Optional[Dict[str, Any]]: pass
    @abstractmethod
    async def save_state(self, session_id: str, state: Dict[str, Any]) -> None: pass

class RedisStateBackend(StateBackend):
    def __init__(self, redis_url):
        if RedisClient is None:
            raise RuntimeError("RedisClient not available. Cannot use RedisStateBackend.")
        self.client = RedisClient(redis_url)
        logger.info(f"RedisStateBackend initialized with URL: {redis_url}")

    async def init_client(self):
        try:
            await async_with_retry(lambda: self.client.ping(), retries=3, delay=2, backoff=2)
            logger.info("Redis client ping successful.")
        except Exception as e:
            logger.error(f"Failed to connect/ping Redis: {e}", exc_info=True)
            raise AgentCoreException(f"Redis connection failed: {e}", code=AgentErrorCode.STATE_LOAD_FAILED, original_exception=e) from e

    async def save_state(self, session_id: str, state: Dict[str, Any]):
        start_time = time.monotonic()
        with tracer.start_as_current_span("redis_save_state") as span:
            span.set_attribute("session_id", session_id)
            try:
                await asyncio.wait_for(self.client.set(f"agent_state:{session_id}", json.dumps(state)), timeout=5)
                AGENT_METRICS["state_backend_operations_total"].labels(operation="save", backend_type="redis").inc()
                AGENT_METRICS["state_backend_latency_seconds"].labels(operation="save", backend_type="redis").observe(time.monotonic() - start_time)
                logger.debug(f"Saved state for session {session_id} in Redis. Trace ID: {trace_id_var.get()}")
            except (asyncio.TimeoutError, Exception) as e:
                AGENT_METRICS["state_backend_errors_total"].labels(operation="save", backend_type="redis", error_code="redis_save_failed").inc()
                span.record_exception(e)
                span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
                logger.error(f"Failed to save state for session {session_id} in Redis: {e}. Trace ID: {trace_id_var.get()}", exc_info=True)
                raise AgentCoreException(f"Redis save state failed: {e}", code=AgentErrorCode.STATE_SAVE_FAILED, original_exception=e) from e

    async def load_state(self, session_id: str) -> Optional[Dict[str, Any]]:
        start_time = time.monotonic()
        with tracer.start_as_current_span("redis_load_state") as span:
            span.set_attribute("session_id", session_id)
            try:
                data = await asyncio.wait_for(self.client.get(f"agent_state:{session_id}"), timeout=5)
                if data:
                    AGENT_METRICS["state_backend_operations_total"].labels(operation="load", backend_type="redis").inc()
                    AGENT_METRICS["state_backend_latency_seconds"].labels(operation="load", backend_type="redis").observe(time.monotonic() - start_time)
                    logger.debug(f"Loaded state for session {session_id} from Redis. Trace ID: {trace_id_var.get()}")
                    return json.loads(data)
                else:
                    logger.info(f"No state found for session {session_id} in Redis. Trace ID: {trace_id_var.get()}")
                    return None
            except json.JSONDecodeError as e:
                AGENT_METRICS["state_backend_errors_total"].labels(operation="load", backend_type="redis", error_code="json_decode_failed").inc()
                span.record_exception(e)
                span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
                logger.error(f"Failed to decode state for session {session_id} from Redis: {e}. Trace ID: {trace_id_var.get()}", exc_info=True)
                raise AgentCoreException(f"Redis load state failed: {e}", code=AgentErrorCode.STATE_LOAD_FAILED, original_exception=e) from e
            except (asyncio.TimeoutError, Exception) as e:
                AGENT_METRICS["state_backend_errors_total"].labels(operation="load", backend_type="redis", error_code="redis_load_failed").inc()
                span.record_exception(e)
                span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
                logger.error(f"Failed to load state for session {session_id} from Redis: {e}. Trace ID: {trace_id_var.get()}", exc_info=True)
                raise AgentCoreException(f"Redis load state failed: {e}", code=AgentErrorCode.STATE_LOAD_FAILED, original_exception=e) from e

class PostgresStateBackend(StateBackend):
    def __init__(self, db_url):
        if PostgresClient is None:
            raise RuntimeError("PostgresClient not available. Cannot use PostgresStateBackend.")
        self.client = PostgresClient(db_url=db_url)
        logger.info(f"PostgresStateBackend initialized with URL: {db_url}")

    async def init_client(self):
        try:
            await async_with_retry(lambda: self.client.connect(), retries=3, delay=2, backoff=2)
            logger.info("Postgres client connected and schema ensured.")
        except Exception as e:
            logger.error(f"Failed to connect/init PostgreSQL: {e}", exc_info=True)
            raise AgentCoreException(f"PostgreSQL initialization failed: {e}", code=AgentErrorCode.STATE_LOAD_FAILED, original_exception=e) from e

    async def save_state(self, session_id: str, state: Dict[str, Any]):
        start_time = time.monotonic()
        with tracer.start_as_current_span("postgres_save_state") as span:
            span.set_attribute("session_id", session_id)
            try:
                await asyncio.wait_for(self.client.save("agent_states", {"session_id": session_id, "state": json.dumps(state)}), timeout=5)
                AGENT_METRICS["state_backend_operations_total"].labels(operation="save", backend_type="postgres").inc()
                AGENT_METRICS["state_backend_latency_seconds"].labels(operation="save", backend_type="postgres").observe(time.monotonic() - start_time)
                logger.debug(f"Saved state for session {session_id} in Postgres. Trace ID: {trace_id_var.get()}")
            except (asyncio.TimeoutError, Exception) as e:
                AGENT_METRICS["state_backend_errors_total"].labels(operation="save", backend_type="postgres", error_code="postgres_save_failed").inc()
                span.record_exception(e)
                span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
                logger.error(f"Failed to save state for session {session_id} in Postgres: {e}. Trace ID: {trace_id_var.get()}", exc_info=True)
                raise AgentCoreException(f"Postgres save state failed: {e}", code=AgentErrorCode.STATE_SAVE_FAILED, original_exception=e) from e

    async def load_state(self, session_id: str) -> Optional[Dict[str, Any]]:
        start_time = time.monotonic()
        with tracer.start_as_current_span("postgres_load_state") as span:
            span.set_attribute("session_id", session_id)
            try:
                data = await asyncio.wait_for(self.client.load("agent_states", session_id), timeout=5)
                if data:
                    AGENT_METRICS["state_backend_operations_total"].labels(operation="load", backend_type="postgres").inc()
                    AGENT_METRICS["state_backend_latency_seconds"].labels(operation="load", backend_type="postgres").observe(time.monotonic() - start_time)
                    logger.debug(f"Loaded state for session {session_id} from Postgres. Trace ID: {trace_id_var.get()}")
                    return json.loads(data["state"])
                else:
                    logger.info(f"No state found for session {session_id} in Postgres. Trace ID: {trace_id_var.get()}")
                    return None
            except json.JSONDecodeError as e:
                AGENT_METRICS["state_backend_errors_total"].labels(operation="load", backend_type="postgres", error_code="json_decode_failed").inc()
                span.record_exception(e)
                span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
                logger.error(f"Failed to decode state for session {session_id} from Postgres: {e}. Trace ID: {trace_id_var.get()}", exc_info=True)
                raise AgentCoreException(f"Postgres load state failed: {e}", code=AgentErrorCode.STATE_LOAD_FAILED, original_exception=e) from e
            except (asyncio.TimeoutError, Exception) as e:
                AGENT_METRICS["state_backend_errors_total"].labels(operation="load", backend_type="postgres", error_code="postgres_load_failed").inc()
                span.record_exception(e)
                span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
                logger.error(f"Failed to load state for session {session_id} from Postgres: {e}. Trace ID: {trace_id_var.get()}", exc_info=True)
                raise AgentCoreException(f"Postgres load state failed: {e}", code=AgentErrorCode.STATE_LOAD_FAILED, original_exception=e) from e

class InMemoryStateBackend(StateBackend):
    def __init__(self):
        self._store = {}
        self._lock = asyncio.Lock()
        logger.warning("Using InMemoryStateBackend. State will be lost on application restart.")

    async def load_state(self, session_id: str) -> Optional[Dict[str, Any]]:
        start_time = time.monotonic()
        with tracer.start_as_current_span("inmemory_load_state") as span:
            span.set_attribute("session_id", session_id)
            try:
                async with self._lock:
                    data = self._store.get(session_id)
                    if data:
                        AGENT_METRICS["state_backend_operations_total"].labels(operation="load", backend_type="inmemory").inc()
                        AGENT_METRICS["state_backend_latency_seconds"].labels(operation="load", backend_type="inmemory").observe(time.monotonic() - start_time)
                        logger.debug(f"Loaded state for session {session_id} from InMemory. Trace ID: {trace_id_var.get()}")
                        # Return a deep copy to prevent race conditions after lock release
                        import copy
                        return copy.deepcopy(data)
                    else:
                        logger.info(f"No state found for session {session_id} in InMemory. Trace ID: {trace_id_var.get()}")
                        return None
            except Exception as e:
                AGENT_METRICS["state_backend_errors_total"].labels(operation="load", backend_type="inmemory", error_code="inmemory_load_failed").inc()
                span.record_exception(e)
                span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
                logger.error(f"Failed to load state for session {session_id} from InMemory: {e}. Trace ID: {trace_id_var.get()}", exc_info=True)
                raise AgentCoreException(f"InMemory load state failed: {e}", code=AgentErrorCode.STATE_LOAD_FAILED, original_exception=e) from e

    async def save_state(self, session_id: str, state: Dict[str, Any]) -> None:
        start_time = time.monotonic()
        with tracer.start_as_current_span("inmemory_save_state") as span:
            span.set_attribute("session_id", session_id)
            try:
                async with self._lock:
                    self._store[session_id] = state
                AGENT_METRICS["state_backend_operations_total"].labels(operation="save", backend_type="inmemory").inc()
                AGENT_METRICS["state_backend_latency_seconds"].labels(operation="save", backend_type="inmemory").observe(time.monotonic() - start_time)
                logger.debug(f"Saved state for session {session_id} in InMemory. Trace ID: {trace_id_var.get()}")
            except Exception as e:
                AGENT_METRICS["state_backend_errors_total"].labels(operation="save", backend_type="inmemory", error_code="inmemory_save_failed").inc()
                span.record_exception(e)
                span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
                logger.error(f"Failed to save state for session {session_id} in InMemory: {e}. Trace ID: {trace_id_var.get()}", exc_info=True)
                raise AgentCoreException(f"InMemory save state failed: {e}", code=AgentErrorCode.STATE_SAVE_FAILED, original_exception=e) from e

class MetaLearning:
    """
    A class for logging and applying self-correction feedback to the agent's responses.
    This implementation uses a simple scikit-learn pipeline for demonstration.
    """
    def __init__(self):
        self.corrections = []
        self.model_pipeline = Pipeline([
            ('tfidf', TfidfVectorizer()),
            ('classifier', LogisticRegression(max_iter=1000))
        ])
        try:
            self.load()
        except Exception as e:
            logger.debug(f"Could not load meta-learning data: {e}")

    def log_correction(self, input_text: str, initial_response: str, corrected_response: str):
        """
        Logs a correction tuple for future training.
        """
        if len(self.corrections) >= Config.MAX_META_LEARNING_CORRECTIONS:
            self.corrections.pop(0) # Simple FIFO to cap memory usage
        if len(input_text) + len(initial_response) + len(corrected_response) > Config.MAX_CORRECTION_ENTRY_SIZE:
            logger.warning("Correction entry too large, skipping logging.")
            return
        self.corrections.append((input_text, initial_response, corrected_response))
        # Only increment metric if it exists
        if "meta_learning_corrections_logged_total" in AGENT_METRICS:
            AGENT_METRICS["meta_learning_corrections_logged_total"].inc()
        self.persist()

    def train_model(self):
        """
        Trains the meta-learning model using the collected corrections.
        """
        if len(self.corrections) < Config.MIN_RECORDS_FOR_TRAINING:
            logger.info("Not enough corrections to train meta-learning model.")
            return
        start_time = time.monotonic()
        try:
            X = [f"{corr[0]} {corr[1]}" for corr in self.corrections]
            y = [corr[2] for corr in self.corrections]
            self.model_pipeline.fit(X, y)
            if "meta_learning_train_duration_seconds" in AGENT_METRICS:
                AGENT_METRICS["meta_learning_train_duration_seconds"].observe(time.monotonic() - start_time)
            logger.info("Meta-learning model trained successfully.")
        except Exception as e:
            logger.error(f"Failed to train meta-learning model: {e}", exc_info=True)
            if "meta_learning_train_errors_total" in AGENT_METRICS:
                AGENT_METRICS["meta_learning_train_errors_total"].inc()

    def apply_correction(self, response: str, input_text: str) -> str:
        """
        Applies a correction based on the trained model's prediction.
        """
        if not self.model_pipeline or len(self.corrections) < Config.MIN_RECORDS_FOR_TRAINING:
            return response
        try:
            # Check if the model has been fitted
            _ = self.model_pipeline.predict(["dummy input"])
            predicted_correction = self.model_pipeline.predict([f"{input_text} {response}"])
            if predicted_correction and predicted_correction[0] != response:
                logger.info(f"Applying meta-learning correction. Model predicted: {predicted_correction[0][:50]}...")
                return predicted_correction[0]
        except NotFittedError:
            logger.warning("Meta-learning model is not fitted. Cannot apply corrections.")
        except Exception as e:
            logger.error(f"Failed to apply meta-learning correction: {e}", exc_info=True)
        return response

    def persist(self):
        """
        Persists the meta-learning data and model to disk.
        """
        try:
            with open("meta_learning.pkl", "wb") as f:
                pickle.dump({"corrections": self.corrections, "model": self.model_pipeline}, f)
            logger.debug("Meta-learning data and model persisted.")
        except Exception as e:
            logger.error(f"Failed to persist meta-learning data: {e}", exc_info=True)

    def load(self):
        """
        Loads the meta-learning data and model from disk.
        """
        try:
            with open("meta_learning.pkl", "rb") as f:
                data = pickle.load(f)
                self.corrections = data.get("corrections", [])
                model_pipeline = data.get("model")
                if isinstance(model_pipeline, Pipeline):
                    self.model_pipeline = model_pipeline
                else:
                    logger.warning("Loaded object is not a scikit-learn pipeline. Re-initializing model.")
                    self.model_pipeline = Pipeline([
                        ('tfidf', TfidfVectorizer()),
                        ('classifier', LogisticRegression(max_iter=1000))
                    ])
        except FileNotFoundError:
            logger.info("Meta-learning data file not found. Starting with empty corrections.")
        except Exception as e:
            logger.error(f"Failed to load meta-learning data: {e}", exc_info=True)

class CollaborativeAgent:
    """
    Core class for a self-correcting, stateful AI agent.
    
    This agent uses a multi-step process for generating responses:
    1. Initial LLM call to get a baseline response.
    2. Self-reflection on the response.
    3. Peer critique to identify weaknesses.
    4. Self-correction to produce a final, refined response.
    5. Meta-learning to apply past corrections to new responses.
    """
    def __init__(self, agent_id: str, session_id: str, llm_config: Dict[str, Any], persona: str = "default", state_backend: Optional[StateBackend] = None, meta_learning: Optional[MetaLearning] = None, prompt_strategy: Optional[PromptStrategy] = None, mm_processor: Optional[MultiModalProcessor] = None):
        self.agent_id = agent_id
        self.session_id = session_id
        self.llm_config = llm_config
        self.persona = load_persona_dict().get(persona, "You are a helpful AI assistant.")
        self.language = Config.DEFAULT_LANGUAGE
        self.state_backend = state_backend or InMemoryStateBackend()
        self.meta_learning = meta_learning or MetaLearning()
        self.prompt_strategy = prompt_strategy or DefaultPromptStrategy(logger)
        self.mm_processor = mm_processor or DefaultMultiModalProcessor(logger)
        self.audit_ledger = AuditLedgerClient()
        self.memory = ConversationBufferWindowMemory(memory_key="history", k=Config.MEMORY_WINDOW)
        self.llm = self._get_llm()
        self._last_success_timestamp = None
        self._last_error_timestamp = None
        self.fallback_llm = self._get_fallback_llm()

    def _get_llm(self) -> Any:
        provider = self.llm_config.get("provider", Config.DEFAULT_PROVIDER)
        model = self.llm_config.get("model", Config.DEFAULT_LLM_MODEL)
        temperature = self.llm_config.get("temperature", Config.DEFAULT_TEMP)
        api_key = self.llm_config.get("api_key")
        if isinstance(api_key, SensitiveValue):
            api_key = api_key.get_actual_value()

        # Skip validation for test keys or None
        if api_key and not (api_key.startswith("test") or api_key == "test_api_key"):
            if provider == "openai" and not re.match(r"sk-[a-zA-Z0-9]{48}", api_key):
                raise AgentCoreException("Invalid OpenAI API key format.", code=AgentErrorCode.LLM_INIT_FAILED)
            elif provider == "anthropic" and not re.match(r"sk-ant-api03-[a-zA-Z0-9]{86}", api_key):
                raise AgentCoreException("Invalid Anthropic API key format.", code=AgentErrorCode.LLM_INIT_FAILED)
        
        try:
            if provider == "openai":
                return ChatOpenAI(model_name=model, temperature=temperature, api_key=api_key)
            elif provider == "anthropic":
                return ChatAnthropic(model=model, temperature=temperature, api_key=api_key)
            elif provider == "google" and ChatGoogleGenerativeAI:
                return ChatGoogleGenerativeAI(model=model, temperature=temperature, google_api_key=api_key)
            elif provider == "xai" and ChatXAI:
                return ChatXAI(model_name=model, temperature=temperature, xai_api_key=api_key)
            raise AgentCoreException(f"Unsupported LLM provider: {provider}", code=AgentErrorCode.LLM_UNSUPPORTED_PROVIDER)
        except Exception as e:
            logger.error(f"Failed to initialize LLM for provider {provider}: {e}", exc_info=True)
            raise AgentCoreException(f"LLM initialization failed: {e}", code=AgentErrorCode.LLM_INIT_FAILED, original_exception=e) from e
    
    def _get_fallback_llm(self) -> Optional[Any]:
        fallback_provider = Config.FALLBACK_PROVIDER
        if not fallback_provider or fallback_provider == self.llm_config.get("provider"):
            return None
        
        fallback_config = Config.FALLBACK_LLM_CONFIG.copy()
        fallback_config["provider"] = fallback_provider
        
        try:
            if fallback_provider == "openai":
                return ChatOpenAI(**fallback_config)
            elif fallback_provider == "anthropic":
                return ChatAnthropic(**fallback_config)
            elif fallback_provider == "google" and ChatGoogleGenerativeAI:
                return ChatGoogleGenerativeAI(**fallback_config)
            elif fallback_provider == "xai" and ChatXAI:
                return ChatXAI(**fallback_config)
            logger.warning(f"Unsupported fallback LLM provider: {fallback_provider}")
            return None
        except Exception as e:
            logger.error(f"Failed to initialize fallback LLM: {e}", exc_info=True)
            return None

    async def load_state(self, operator_id: str = "system"):
        """Loads agent state from the configured backend."""
        current_trace_id = str(uuid.uuid4())
        token = trace_id_var.set(current_trace_id)
        try:
            state = await self.state_backend.load_state(self.session_id)
            if state:
                if "history" in state and isinstance(state["history"], list):
                    self.memory.chat_memory.messages = messages_from_dict(state["history"])
                self.persona = state.get("persona", self.persona)
                self.language = state.get("language", self.language)
                logger.debug(f"State loaded for agent {self.agent_id}, session {self.session_id}. Trace ID: {current_trace_id}")
            await self.audit_ledger.log_event(
                event_type="agent:state_loaded",
                details={"agent_id": self.agent_id, "session_id": self.session_id},
                operator=operator_id
            )
        except Exception as e:
            logger.error(f"State load failed for agent {self.agent_id}: {e}. Trace ID: {current_trace_id}", exc_info=True)
            raise AgentCoreException(f"State load failed: {e}", code=AgentErrorCode.STATE_LOAD_FAILED, original_exception=e) from e
        finally:
            trace_id_var.reset(token)

    async def save_state(self, operator_id: str = "system"):
        """Saves the current agent state to the configured backend."""
        current_trace_id = str(uuid.uuid4())
        token = trace_id_var.set(current_trace_id)
        try:
            state = {
                "history": messages_to_dict(self.memory.chat_memory.messages),
                "persona": self.persona,
                "language": self.language
            }
            await self.state_backend.save_state(self.session_id, state)
            await self.audit_ledger.log_event(
                event_type="agent:state_saved",
                details={"agent_id": self.agent_id, "session_id": self.session_id},
                operator=operator_id
            )
        except Exception as e:
            logger.error(f"State save failed for agent {self.agent_id}: {e}. Trace ID: {current_trace_id}", exc_info=True)
            raise AgentCoreException(f"State save failed: {e}", code=AgentErrorCode.STATE_SAVE_FAILED, original_exception=e) from e
        finally:
            trace_id_var.reset(token)

    async def set_persona(self, persona: str, operator_id: str = "system"):
        """
        Sets a new persona for the agent.
        
        Args:
            persona: The name of the persona (e.g., "scrum_master", "default").
            operator_id: Identifier for the operator updating the persona.
        """
        self.persona = load_persona_dict().get(persona, self.persona)
        await self.save_state(operator_id)
        await self.audit_ledger.log_event(
            event_type="agent:persona_updated",
            details={"agent_id": self.agent_id, "session_id": self.session_id, "persona": persona},
            operator=operator_id
        )

    @sleep_and_retry
    @limits(calls=Config.LLM_RATE_LIMIT_CALLS, period=Config.LLM_RATE_LIMIT_PERIOD)
    async def _call_llm_with_retries(self, llm_instance: Any, messages: List[Any], provider: str, model: str) -> Any:
        """
        Wrapper to call an LLM instance with retries and rate limiting.
        """
        try:
            response = await async_with_retry(lambda: llm_instance.ainvoke(messages), retries=3, delay=2, backoff=2)
            AGENT_METRICS["llm_calls_total"].labels(provider=provider, model=model).inc()
            return response
        except Exception as e:
            AGENT_METRICS["llm_errors_total"].labels(provider=provider, model=model).inc()
            raise AgentCoreException(f"LLM call failed: {e}", code=AgentErrorCode.LLM_CALL_FAILED, original_exception=e) from e

    async def predict(self, user_input: str, context: Optional[Dict[str, Any]] = None, timeout: int = 30, operator_id: str = "system") -> Dict[str, Any]:
        """
        Generates a response to user input with self-reflection, critique, and correction.
        
        Args:
            user_input: The user's input string.
            context: Optional context dictionary, including multimodal data.
            timeout: Maximum time (seconds) for the operation.
            operator_id: Identifier for the operator (e.g., JWT token).
        
        Returns:
            Dict with 'response' (final output) and 'trace' (reflection/critique details).
        
        Raises:
            AgentCoreException: For timeouts, LLM failures, or invalid inputs.
        """
        current_trace_id = str(uuid.uuid4())
        token = trace_id_var.set(current_trace_id)
        AGENT_METRICS["agent_predict_total"].labels(agent_id=self.agent_id).inc()
        AGENT_METRICS["agent_active_sessions_current"].inc()
        start_time = time.monotonic()
        
        # JWT Validation
        if jwt and Config.AUDIT_SIGNING_PUBLIC_KEY and operator_id.startswith("JWT:"):
            try:
                decoded = jwt.decode(operator_id[4:], Config.AUDIT_SIGNING_PUBLIC_KEY.get_actual_value(), algorithms=["HS256"])
                logger.debug(f"JWT validated for operator: {decoded.get('sub')}. Trace ID: {current_trace_id}")
            except jwt.InvalidTokenError as e:
                logger.error(f"Invalid operator JWT: {e}. Trace ID: {current_trace_id}", exc_info=True)
                raise AgentCoreException(f"Invalid operator JWT: {e}", code=AgentErrorCode.INVALID_INPUT, original_exception=e)
        
        # Caching check
        cache_key = f"predict:{hashlib.sha256(user_input.encode('utf-8')).hexdigest()}"
        cached_result = None
        redis_client = None  # Initialize to avoid undefined variable errors
        if Config.REDIS_URL and RedisClient:
            try:
                redis_client = RedisClient(Config.REDIS_URL)
                # Connect to Redis asynchronously if needed
                if hasattr(redis_client, 'connect') and asyncio.iscoroutinefunction(redis_client.connect):
                    await redis_client.connect()
                cached_result = await redis_client.get(cache_key)
                if cached_result:
                    logger.info(f"Cache hit for input. Session: {self.session_id}. Trace ID: {current_trace_id}")
                    return json.loads(cached_result)
            except Exception as e:
                logger.warning(f"Failed to check Redis cache: {e}. Trace ID: {current_trace_id}")
                redis_client = None  # Reset on error
        
        try:
            async with async_timeout(timeout):
                # Sanitize inputs
                user_input = _sanitize_user_input(user_input)
                context = await _sanitize_context(context or {})

                # Process multimodal data
                multi_modal_context = context.get("multi_modal", [])
                processed_mm = []
                for item in multi_modal_context:
                    if not isinstance(item, MultiModalData):
                        logger.warning(f"Invalid multimodal data type: {type(item)}. Skipping. Trace ID: {current_trace_id}")
                        continue
                    AGENT_METRICS["multimodal_data_processed_total"].labels(data_type=item.data_type).inc()
                    summary = await self.mm_processor.summarize(item)
                    processed_mm.append(MultiModalData(data_type=item.data_type, data=item.data, metadata=summary))

                # Get conversation history
                history = self.memory.load_memory_variables({})["history"]

                # Create prompt
                full_prompt = await self.prompt_strategy.create_agent_prompt(
                    base_template=BASE_AGENT_PROMPT_TEMPLATE,
                    history=history,
                    user_input=user_input,
                    persona=self.persona,
                    language=self.language,
                    multi_modal_context=processed_mm
                )

                # LLM call for initial response
                response_text = ""
                initial_llm_start = time.monotonic()
                with tracer.start_as_current_span("llm_call_initial") as span:
                    span.set_attribute("provider", self.llm_config["provider"])
                    span.set_attribute("model", self.llm_config["model"])
                    try:
                        response = await self._call_llm_with_retries(self.llm, [HumanMessage(content=full_prompt)], self.llm_config["provider"], self.llm_config["model"])
                        response_text = response.content if hasattr(response, 'content') else str(response)
                    except AgentCoreException as e:
                        if self.fallback_llm:
                            logger.warning(f"Primary LLM call failed, attempting fallback: {e}")
                            span.set_attribute("fallback_used", True)
                            response = await self._call_llm_with_retries(self.fallback_llm, [HumanMessage(content=full_prompt)], Config.FALLBACK_PROVIDER, Config.FALLBACK_LLM_CONFIG.get("model"))
                            response_text = response.content if hasattr(response, 'content') else str(response)
                        else:
                            raise
                AGENT_METRICS["agent_step_duration_seconds"].labels(step="initial_llm").observe(time.monotonic() - initial_llm_start)
                
                # Update memory with initial response
                self.memory.save_context({"input": user_input}, {"output": response_text})

                # Self-reflection
                reflection_text = ""
                reflection_start = time.monotonic()
                with tracer.start_as_current_span("llm_call_reflection") as span:
                    reflection_prompt = REFLECTION_PROMPT_TEMPLATE.format(input=user_input, ai_response=response_text)
                    reflection = await self._call_llm_with_retries(self.llm, [HumanMessage(content=reflection_prompt)], self.llm_config["provider"], self.llm_config["model"])
                    reflection_text = reflection.content if hasattr(reflection, 'content') else str(reflection)
                AGENT_METRICS["agent_step_duration_seconds"].labels(step="reflection").observe(time.monotonic() - reflection_start)

                # Peer critique
                critique_text = ""
                critique_start = time.monotonic()
                with tracer.start_as_current_span("llm_call_critique") as span:
                    critique_prompt = CRITIQUE_PROMPT_TEMPLATE.format(persona=self.persona, ai_response=response_text)
                    critique = await self._call_llm_with_retries(self.llm, [HumanMessage(content=critique_prompt)], self.llm_config["provider"], self.llm_config["model"])
                    critique_text = critique.content if hasattr(critique, 'content') else str(critique)
                AGENT_METRICS["agent_step_duration_seconds"].labels(step="critique").observe(time.monotonic() - critique_start)

                # Self-correction
                corrected_text = ""
                correction_start = time.monotonic()
                with tracer.start_as_current_span("llm_call_correction") as span:
                    correct_prompt = SELF_CORRECT_PROMPT_TEMPLATE.format(ai_response=response_text, reflection=reflection_text, critique=critique_text)
                    corrected = await self._call_llm_with_retries(self.llm, [HumanMessage(content=correct_prompt)], self.llm_config["provider"], self.llm_config["model"])
                    corrected_text = corrected.content if hasattr(corrected, 'content') else str(corrected)
                AGENT_METRICS["agent_step_duration_seconds"].labels(step="correction").observe(time.monotonic() - correction_start)

                # Apply meta-learning
                self.meta_learning.log_correction(user_input, response_text, corrected_text)
                adjusted_response = self.meta_learning.apply_correction(corrected_text, user_input)
                
                # Prepare final result
                result = {
                    "response": adjusted_response,
                    "trace": {
                        "initial": response_text,
                        "reflection": reflection_text,
                        "critique": critique_text,
                        "corrected": corrected_text
                    }
                }

                # Save state
                await self.save_state(operator_id)

                # Log success
                AGENT_METRICS["agent_predict_success"].labels(agent_id=self.agent_id).inc()
                AGENT_METRICS["agent_predict_duration_seconds"].labels(agent_id=self.agent_id).observe(time.monotonic() - start_time)
                self._last_success_timestamp = time.time()
                AGENT_METRICS["agent_last_success_timestamp"].labels(agent_id=self.agent_id).set(self._last_success_timestamp)
                AGENT_METRICS["agent_heartbeat_timestamp"].labels(agent_id=self.agent_id).set(time.time())

                # Log audit
                await self.audit_ledger.log_event(
                    event_type="agent:prediction_success",
                    details={
                        "agent_id": self.agent_id,
                        "session_id": self.session_id,
                        "input_preview": user_input[:100],
                        "response_preview": adjusted_response[:100],
                        "trace_id": current_trace_id
                    },
                    operator=operator_id
                )
                
                # Cache result
                if Config.REDIS_URL and RedisClient and redis_client is not None:
                    try:
                        await redis_client.setex(cache_key, Config.CACHE_EXPIRATION_SECONDS, json.dumps(result))
                        logger.debug(f"Result cached for key: {cache_key}. Trace ID: {current_trace_id}")
                    except Exception as e:
                        logger.warning(f"Failed to cache result in Redis: {e}. Trace ID: {current_trace_id}")

                return result
        except asyncio.TimeoutError as e:
            AGENT_METRICS["agent_predict_errors"].labels(agent_id=self.agent_id, error_code=AgentErrorCode.TIMEOUT.value).inc()
            self._last_error_timestamp = time.time()
            AGENT_METRICS["agent_last_error_timestamp"].labels(agent_id=self.agent_id).set(self._last_error_timestamp)
            AGENT_METRICS["agent_heartbeat_timestamp"].labels(agent_id=self.agent_id).set(time.time())
            logger.error(f"Prediction timed out for agent {self.agent_id}. Trace ID: {current_trace_id}", exc_info=True)
            await self.audit_ledger.log_event(
                event_type="agent:prediction_timeout",
                details={"agent_id": self.agent_id, "session_id": self.session_id, "timeout": timeout},
                operator=operator_id
            )
            raise AgentCoreException(f"Prediction timed out after {timeout}s", code=AgentErrorCode.TIMEOUT, original_exception=e) from e
        except AgentCoreException as e:
            AGENT_METRICS["agent_predict_errors"].labels(agent_id=self.agent_id, error_code=e.code.value).inc()
            self._last_error_timestamp = time.time()
            AGENT_METRICS["agent_last_error_timestamp"].labels(agent_id=self.agent_id).set(self._last_error_timestamp)
            AGENT_METRICS["agent_heartbeat_timestamp"].labels(agent_id=self.agent_id).set(time.time())
            logger.error(f"Agent prediction failed for {self.agent_id}: {e}. Trace ID: {current_trace_id}", exc_info=True)
            await self.audit_ledger.log_event(
                event_type="agent:prediction_failed",
                details={"agent_id": self.agent_id, "session_id": self.session_id, "error": str(e), "error_code": e.code.value},
                operator=operator_id
            )
            raise
        except Exception as e:
            AGENT_METRICS["agent_predict_errors"].labels(agent_id=self.agent_id, error_code=AgentErrorCode.UNEXPECTED_ERROR.value).inc()
            self._last_error_timestamp = time.time()
            AGENT_METRICS["agent_last_error_timestamp"].labels(agent_id=self.agent_id).set(self._last_error_timestamp)
            AGENT_METRICS["agent_heartbeat_timestamp"].labels(agent_id=self.agent_id).set(time.time())
            logger.error(f"Unexpected error in agent prediction for {self.agent_id}: {e}. Trace ID: {current_trace_id}", exc_info=True)
            await self.audit_ledger.log_event(
                event_type="agent:prediction_failed_unexpected",
                details={"agent_id": self.agent_id, "session_id": self.session_id, "error": str(e)},
                operator=operator_id
            )
            raise AgentCoreException(f"Unexpected error during agent prediction: {e}", code=AgentErrorCode.UNEXPECTED_ERROR, original_exception=e) from e
        finally:
            AGENT_METRICS["agent_active_sessions_current"].dec()
            trace_id_var.reset(token)

def get_transcript(memory: ConversationBufferWindowMemory) -> str:
    """Retrieves and sanitizes the conversation history transcript."""
    raw_history = memory.load_memory_variables({})["history"]
    sanitized_history = [_sanitize_user_input(str(m)) for m in raw_history]
    return sanitized_history

class AgentTeam:
    """
    A class for managing a team of collaborative agents to handle complex tasks.
    """
    def __init__(self, session_id: str, llm_config: Dict[str, Any], state_backend: StateBackend, meta_learning: MetaLearning):
        self.session_id = session_id
        if not all([llm_config, state_backend, meta_learning]):
            raise ValueError("llm_config, state_backend, and meta_learning are required for AgentTeam.")
        self.agents = {
            "requirements": CollaborativeAgent(
                agent_id=f"agent_{session_id}_reqs",
                session_id=session_id,
                llm_config=llm_config,
                persona="default",
                state_backend=state_backend,
                meta_learning=meta_learning,
                prompt_strategy=DefaultPromptStrategy(logger),
                mm_processor=DefaultMultiModalProcessor(logger)
            ),
            "refiner": CollaborativeAgent(
                agent_id=f"agent_{session_id}_refine",
                session_id=session_id,
                llm_config=llm_config,
                persona="scrum_master",
                state_backend=state_backend,
                meta_learning=meta_learning,
                prompt_strategy=ConcisePromptStrategy(logger),
                mm_processor=DefaultMultiModalProcessor(logger)
            )
        }
        logger.info(json.dumps({"event": "agent_team_created", "session_id": session_id, "agents": list(self.agents.keys())}))

    async def delegate_task(self, initial_input: str, context: Optional[Dict[str, Any]] = None, timeout: int = 45, operator_id: str = "system") -> Dict[str, Any]:
        """
        Delegates a complex task to a team of specialized agents.
        
        Args:
            initial_input: The starting user input for the task.
            context: Optional context for the task.
            timeout: Maximum time (seconds) for the entire team operation.
            operator_id: Identifier for the operator.
        
        Returns:
            A dictionary containing the final response and traces from all agents.
        
        Raises:
            AgentCoreException: If any agent task fails or times out.
        """
        current_trace_id = str(uuid.uuid4())
        token = trace_id_var.set(current_trace_id)
        logger.info(json.dumps({"event": "team_task_start", "session_id": self.session_id, "input": initial_input, "operator_id": operator_id, "trace_id": current_trace_id}))
        await self.agents["requirements"].audit_ledger.log_event(
            event_type="team:task_delegated",
            details={"session_id": self.session_id, "initial_input_preview": initial_input[:100], "team_agents": list(self.agents.keys())},
            operator=operator_id
        )
        task_start_time = time.monotonic()
        try:
            reqs_output = await self.agents["requirements"].predict(initial_input, context=context, timeout=timeout, operator_id=operator_id)
            refinement_context = {
                "initial_human_input": initial_input,
                "initial_ai_response": reqs_output['response'],
                **(context if context else {})
            }
            refinement_prompt = """
            Please review the following initial requirements gathering conversation and synthesize a formal specification from it.
            Focus on creating a clear, structured document.
            """
            final_output = await self.agents["refiner"].predict(refinement_prompt, context=refinement_context, timeout=timeout, operator_id=operator_id)
            
            if "agent_team_task_duration_seconds" in AGENT_METRICS:
                AGENT_METRICS["agent_team_task_duration_seconds"].observe(time.monotonic() - task_start_time)
            logger.info(json.dumps({"event": "team_task_end", "session_id": self.session_id, "trace_id": current_trace_id}))
            await self.agents["requirements"].audit_ledger.log_event(
                event_type="team:task_completed",
                details={"session_id": self.session_id, "final_response_preview": final_output['response'][:100]},
                operator=operator_id
            )
            return {
                "requirements_trace": reqs_output['trace'],
                "final_spec_trace": final_output['trace'],
                "final_response": final_output['response']
            }
        except Exception as e:
            if "agent_team_task_errors_total" in AGENT_METRICS:
                AGENT_METRICS["agent_team_task_errors_total"].labels(error_code=AgentErrorCode.UNEXPECTED_ERROR.value).inc()
            logger.error(f"AgentTeam task failed: {e}. Trace ID: {current_trace_id}", exc_info=True)
            await self.agents["requirements"].audit_ledger.log_event(
                event_type="team:task_failed",
                details={"session_id": self.session_id, "error": str(e)},
                operator=operator_id
            )
            raise AgentCoreException(f"AgentTeam task failed: {e}", code=AgentErrorCode.UNEXPECTED_ERROR, original_exception=e) from e
        finally:
            trace_id_var.reset(token)

async def get_or_create_agent(
    session_id: str = "default_session",
    llm_config: Optional[Dict[str, Any]] = None,
    state_backend: Optional[StateBackend] = None,
    meta_learning: Optional[MetaLearning] = None,
    prompt_strategy: Optional[PromptStrategy] = None,
    mm_processor: Optional[MultiModalProcessor] = None,
    operator_id: str = "system"
) -> CollaborativeAgent:
    """
    Initializes or loads a CollaborativeAgent instance for a given session.
    
    This factory function handles:
    - Defaulting LLM configuration.
    - Handling SensitiveValue for API keys.
    - Selecting and initializing a state backend (Redis, Postgres, or In-Memory).
    - Setting up meta-learning and multimodal processors.
    - Loading previous state if it exists.
    """
    llm_conf = llm_config or {
        "provider": Config.DEFAULT_PROVIDER,
        "model": Config.DEFAULT_LLM_MODEL,
        "temperature": Config.DEFAULT_TEMP
    }
    if "api_key" in llm_conf:
        api_key_value = llm_conf["api_key"]
        if isinstance(api_key_value, SensitiveValue):
            api_key_value = api_key_value.get_actual_value()
        if isinstance(api_key_value, str):
            llm_conf["api_key"] = SensitiveValue(root=api_key_value)
        else:
            logger.warning(f"Unexpected type for api_key in llm_conf: {type(api_key_value)}. Skipping SensitiveValue wrapping.")

    agent_id = f"agent_{session_id}_{uuid.uuid4().hex[:8]}"
    if meta_learning is None:
        meta_learning = MetaLearning()

    final_state_backend = state_backend
    if final_state_backend is None:
        if Config.REDIS_URL and RedisClient is not None:
            try:
                sb = RedisStateBackend(redis_url=Config.REDIS_URL)
                await async_with_retry(lambda: sb.init_client(), retries=3, delay=2, backoff=2)
                final_state_backend = sb
                logger.info("Using RedisStateBackend for state management.")
            except Exception as e:
                logger.error(f"Failed to initialize RedisStateBackend: {e}. Falling back to InMemoryStateBackend.", exc_info=True)
                final_state_backend = InMemoryStateBackend()
        elif Config.POSTGRES_DB_URL and PostgresClient is not None:
            try:
                sb = PostgresStateBackend(db_url=Config.POSTGRES_DB_URL)
                await async_with_retry(lambda: sb.init_client(), retries=3, delay=2, backoff=2)
                final_state_backend = sb
                logger.info("Using PostgresStateBackend for state management.")
            except Exception as e:
                logger.error(f"Failed to initialize PostgresStateBackend: {e}. Falling back to InMemoryStateBackend.", exc_info=True)
                final_state_backend = InMemoryStateBackend()
        else:
            final_state_backend = InMemoryStateBackend()
            logger.info("Using InMemoryStateBackend for state management (no Redis/Postgres configured or available).")

    if mm_processor is None:
        mm_processor = DefaultMultiModalProcessor(logger)

    start_time = time.monotonic()
    agent = CollaborativeAgent(
        agent_id=agent_id,
        session_id=session_id,
        llm_config=llm_conf,
        state_backend=final_state_backend,
        meta_learning=meta_learning,
        prompt_strategy=prompt_strategy,
        mm_processor=mm_processor
    )
    await agent.load_state(operator_id=operator_id)
    if "agent_creation_duration_seconds" in AGENT_METRICS:
        AGENT_METRICS["agent_creation_duration_seconds"].observe(time.monotonic() - start_time)
    logger.info(json.dumps({"event": "agent_session_ready", "agent_id": agent.agent_id, "session_id": agent.session_id, "operator_id": operator_id}))
    await agent.audit_ledger.log_event(
        event_type="agent:session_ready",
        details={"agent_id": agent.agent_id, "session_id": agent.session_id},
        operator=operator_id
    )
    return agent

async def setup_conversation(llm: Any, persona: str = "", language: str = Config.DEFAULT_LANGUAGE) -> Tuple[ConversationChain, ConversationBufferWindowMemory]:
    """
    Legacy function for setting up a conversation chain.
    
    WARNING: This function is deprecated. Use `get_or_create_agent` instead, as it
    provides state persistence, meta-learning, and multimodal capabilities.
    """
    logger.warning("Legacy function 'setup_conversation' called. Use get_or_create_agent instead, which is now async and handles state persistence, meta-learning, and multimodal data. This function will be deprecated in a future release.")
    session_id = f"legacy_session_{uuid.uuid4().hex}"
    llm_config_from_langchain = {}
    
    # Check if llm is an actual ChatOpenAI instance
    if hasattr(llm, '__class__') and llm.__class__.__name__ == 'ChatOpenAI':
        llm_config_from_langchain = {"provider": "openai", "model": llm.model_name, "temperature": llm.temperature, "api_key": getattr(llm, 'openai_api_key', None)}
    elif hasattr(llm, '__class__') and llm.__class__.__name__ == 'ChatAnthropic':
        llm_config_from_langchain = {"provider": "anthropic", "model": llm.model, "temperature": llm.temperature, "api_key": getattr(llm, 'anthropic_api_key', None)}
    elif hasattr(llm, '__class__') and llm.__class__.__name__ == 'ChatGoogleGenerativeAI':
        llm_config_from_langchain = {"provider": "google", "model": llm.model, "temperature": llm.temperature, "api_key": getattr(llm, 'google_api_key', None)}
    elif hasattr(llm, '__class__') and llm.__class__.__name__ == 'ChatXAI':
        llm_config_from_langchain = {"provider": "xai", "model": llm.model_name, "temperature": llm.temperature, "api_key": getattr(llm, 'xai_api_key', None)}
    else:
        llm_config_from_langchain = {"provider": "unknown", "model": getattr(llm, 'model_name', 'unknown_model'), "temperature": getattr(llm, 'temperature', 0.7)}
    
    agent = await get_or_create_agent(session_id=session_id, llm_config=llm_config_from_langchain)
    await agent.set_persona(persona)
    agent.language = language
    # For legacy compatibility, create a ConversationChain wrapper
    prompt_template = PromptTemplate.from_template("{history}\nHuman: {input}\nAI:")
    conversation_chain = ConversationChain(llm=agent.llm, memory=agent.memory, prompt=prompt_template)
    return conversation_chain, agent.memory