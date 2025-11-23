import os
import sys
import logging
from typing import Dict, List, Any, Optional, Tuple
import time
import asyncio
import tiktoken  # For accurate token counting
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
)  # For robust retry logic
import httpx  # Recommended for async HTTP requests
from openai import (
    AsyncOpenAI,
    APIError,
    RateLimitError,
)  # Production-grade LLM client with specific error handling
import hashlib
import uuid
import re
import warnings

# Make Redis optional
try:
    import redis.asyncio as redis

    REDIS_AVAILABLE = True
except ImportError:
    redis = None
    REDIS_AVAILABLE = False

# --- Global Production Mode Flag (from analyzer.py) ---
PRODUCTION_MODE = os.getenv("PRODUCTION_MODE", "false").lower() == "true"

logger = logging.getLogger(__name__)


class NonCriticalError(Exception):
    pass


try:
    from .core_secrets import SECRETS_MANAGER
    from .core_utils import alert_operator, scrub_secrets
except ImportError as e:
    logger.critical(f"CRITICAL: Missing core dependency for core_ai: {e}. Aborting startup.")
    try:
        from .core_utils import alert_operator

        alert_operator(
            f"CRITICAL: AI features missing core dependency: {e}. Aborting.",
            level="CRITICAL",
        )
    except Exception:
        pass
    raise RuntimeError("[CRITICAL][AI] Missing core dependency") from e


# --- Event-loop bridging ---
def _run_async(coro):
    """
    Helper to run an async coroutine from a synchronous context.
    Safely bridges sync/async environments by checking for a running loop.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    else:
        if loop.is_running():
            fut = asyncio.run_coroutine_threadsafe(coro, loop)
            return fut.result()
        else:
            return asyncio.run(coro)


# --- Caching: Redis Client Initialization ---
REDIS_CLIENT = None
if REDIS_AVAILABLE:
    try:
        REDIS_CLIENT = redis.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", 6379)),
            db=0,
            decode_responses=True,
        )
        _run_async(REDIS_CLIENT.ping())
    except Exception as e:
        logger.warning(f"Failed to connect to Redis for caching: {e}. Caching will be disabled.")
        REDIS_CLIENT = None
else:
    logger.info("Redis not available - caching disabled")


def _generate_trace_id() -> str:
    return str(uuid.uuid4())


def _default_trace_id_from_env():
    """Propagate trace_id from analyzer.py/CLI or generate new."""
    return os.getenv("ANALYZER_TRACE_ID", _generate_trace_id())


class AIManager:
    """Manages AI/LLM integrations for generating suggestions and patches."""

    def __init__(self, config: Dict[str, Any], trace_id: Optional[str] = None):
        from .core_audit import audit_logger

        self.config = config.copy() if config else {}
        self.trace_id = trace_id or self.config.get("trace_id") or _default_trace_id_from_env()

        # --- Mandatory production checks ---
        if PRODUCTION_MODE:
            if not self.config.get("llm_endpoint") or not self.config.get(
                "llm_endpoint"
            ).startswith("https://"):
                raise RuntimeError("[CRITICAL][AI] LLM endpoint must use HTTPS in production.")
            if not self.config.get("proxy_url"):
                raise RuntimeError(
                    "[CRITICAL][AI] In PRODUCTION_MODE, 'proxy_url' for LLM traffic is required but not configured."
                )
            if self.config.get("allow_auto_apply_patches", False):
                raise RuntimeError(
                    "[CRITICAL][AI] 'allow_auto_apply_patches' is enabled in PRODUCTION_MODE. This is forbidden."
                )

        self.llm_api_key = SECRETS_MANAGER.get_secret(
            self.config.get("llm_api_key_secret_id", "LLM_API_KEY"),
            required=True if PRODUCTION_MODE else False,
        )
        if not self.llm_api_key:
            raise RuntimeError(
                "[CRITICAL][AI] LLM API key is not configured. AI features cannot function."
            )

        self.llm_endpoint = self.config.get("llm_endpoint")
        if not self.llm_endpoint:
            raise RuntimeError(
                "[CRITICAL][AI] LLM API endpoint is not configured. AI features cannot function."
            )

        # --- General configuration ---
        self.model_name = self.config.get("model_name", "gpt-3.5-turbo")
        self.temperature = self.config.get("temperature", 0.7)
        self.max_tokens = self.config.get("max_tokens", 500)
        self.proxy_url = self.config.get("proxy_url")
        self.allow_auto_apply_patches = self.config.get("allow_auto_apply_patches", False)

        # --- Tokenizer with fallback ---
        try:
            self.token_encoder = tiktoken.encoding_for_model(self.model_name)
        except Exception:
            warnings.warn("Failed to get specific tiktoken encoder. Falling back to default.")
            self.token_encoder = tiktoken.get_encoding("cl100k_base")

        self.api_concurrency_limit = self.config.get("api_concurrency_limit", 5)
        self.token_quota_per_minute = self.config.get("token_quota_per_minute", 60000)
        self._api_semaphore = asyncio.Semaphore(self.api_concurrency_limit)
        self._token_usage_history: List[Tuple[float, int]] = []
        self._token_usage_lock = asyncio.Lock()

        # --- HTTP and LLM Clients ---
        self.http_client = httpx.AsyncClient(proxies=self.proxy_url, verify=True, timeout=90)
        self.llm_client = AsyncOpenAI(
            api_key=self.llm_api_key,
            base_url=self.llm_endpoint,
            http_client=self.http_client,
        )
        logger.info(
            f"AIManager initialized for model: {self.model_name}, endpoint: {self.llm_endpoint}, trace_id: {self.trace_id}"
        )
        audit_logger.log_event(
            "ai_manager_init",
            model=self.model_name,
            endpoint=self.llm_endpoint,
            concurrency_limit=self.api_concurrency_limit,
            token_quota=self.token_quota_per_minute,
            proxy_configured=bool(self.proxy_url),
            auto_apply_patches_allowed=self.allow_auto_apply_patches,
            trace_id=self.trace_id,
        )

    async def aclose(self):
        """Deterministically close all async clients."""
        try:
            if getattr(self, "llm_client", None) and hasattr(self.llm_client, "close"):
                await self.llm_client.close()
        finally:
            if getattr(self, "http_client", None):
                await self.http_client.aclose()

    def _sanitize_prompt(self, prompt: str) -> str:
        """Defense-in-depth prompt sanitation. Update as new vectors discovered."""
        if not isinstance(prompt, str):
            raise ValueError("Prompt must be a string.")

        # Control characters (beyond printable ASCII)
        if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", prompt):
            raise ValueError("Prompt contains ASCII control characters.")

        # Long prompt
        if len(prompt) > 4096:
            logger.warning(
                "Prompt length is unusually long (>{} chars). Truncating for safety.".format(4096)
            )
            prompt = prompt[:4096]

        # Too many newlines (could be prompt overflow attack)
        if prompt.count("\n") > 300:
            logger.warning("Prompt has more than 300 newlines. Truncating for safety.")
            prompt = "\n".join(prompt.splitlines()[:300])

        # Unicode control/block characters (can be used for obfuscation)
        if re.search(r"[\u200b-\u200f\u2028\u2029\u202a-\u202e\u2060-\u206f]", prompt):
            raise ValueError("Prompt contains suspicious unicode control characters.")

        # Repeated dangerous patterns (common prompt injection tricks)
        forbidden_patterns = [
            r"(?i)(ignore|disregard|override|bypass).*previous",  # "ignore previous instructions"
            r"(?i)as an ai language model",  # attempts to elicit system prompt leaks
            r"\b(system|user|assistant):",  # role injection
            r"\{.*?\}",  # curly braces (may signal template injection)
            r"<\s*script",  # HTML/script tag
            r"",  # HTML comments
            r"\b(?:base64|eval|exec|import os|subprocess|openai\.api_key)\b",  # suspicious code
        ]
        for pat in forbidden_patterns:
            if re.search(pat, prompt):
                logger.warning(f"Prompt contains forbidden pattern: {pat}")
                raise ValueError("Prompt contains forbidden or suspicious phrases.")

        if prompt.count("```") % 2 != 0:
            logger.warning(
                "Unmatched code block delimiter '```' in prompt. Potential injection attempt."
            )

        return prompt

    def _estimate_tokens(self, text: str) -> int:
        try:
            return len(self.token_encoder.encode(text))
        except Exception:
            return len(text) // 4  # fallback estimate

    async def _enforce_token_quota(self, tokens_to_use: int, timeout: float = 30.0):
        from .core_audit import audit_logger

        async with self._token_usage_lock:
            current_time = time.time()
            self._token_usage_history = [
                (ts, tokens) for ts, tokens in self._token_usage_history if current_time - ts < 60
            ]
            current_minute_usage = sum(tokens for _, tokens in self._token_usage_history)
            if current_minute_usage + tokens_to_use > self.token_quota_per_minute:
                oldest_timestamp = (
                    self._token_usage_history[0][0]
                    if self._token_usage_history
                    else current_time - 60
                )
                wait_until = oldest_timestamp + 60
                sleep_duration = max(0, wait_until - current_time) + 1
                if sleep_duration > timeout:
                    logger.critical(
                        f"CRITICAL: LLM token quota would require waiting {sleep_duration:.2f}s (>{timeout}s max). Aborting API call."
                    )
                    audit_logger.log_event(
                        "llm_token_quota_timeout_abort",
                        current_usage=current_minute_usage,
                        requested_tokens=tokens_to_use,
                        trace_id=self.trace_id,
                    )
                    alert_operator(
                        "CRITICAL: LLM token quota overrun; would require excessive wait. Aborting API call.",
                        level="CRITICAL",
                    )
                    raise RuntimeError(
                        "LLM token quota overrun. Aborting API call (wait too long)."
                    )
                logger.warning(
                    f"LLM token quota exceeded. Waiting for {sleep_duration:.2f}s.",
                    extra={
                        "current_usage": current_minute_usage,
                        "requested_tokens": tokens_to_use,
                    },
                )
                audit_logger.log_event(
                    "llm_token_quota_exceeded",
                    current_usage=current_minute_usage,
                    requested_tokens=tokens_to_use,
                    wait_time=sleep_duration,
                    trace_id=self.trace_id,
                )
                alert_operator(
                    f"WARNING: LLM token quota exceeded. Waiting {sleep_duration:.2f}s. Current usage: {current_minute_usage}",
                    level="WARNING",
                )
                await asyncio.sleep(sleep_duration)
                self._token_usage_history = [
                    (ts, tokens)
                    for ts, tokens in self._token_usage_history
                    if time.time() - ts < 60
                ]
                current_minute_usage = sum(tokens for _, tokens in self._token_usage_history)
                if current_minute_usage + tokens_to_use > self.token_quota_per_minute:
                    logger.critical(
                        "CRITICAL: LLM token quota overrun after waiting. Aborting API call.",
                        extra={
                            "current_usage": current_minute_usage,
                            "requested_tokens": tokens_to_use,
                        },
                    )
                    audit_logger.log_event(
                        "llm_token_quota_overrun_abort",
                        current_usage=current_minute_usage,
                        requested_tokens=tokens_to_use,
                        trace_id=self.trace_id,
                    )
                    alert_operator(
                        "CRITICAL: LLM token quota overrun after waiting. Aborting API call.",
                        level="CRITICAL",
                    )
                    raise RuntimeError("LLM token quota overrun. Aborting API call.")
            self._token_usage_history.append((current_time, tokens_to_use))

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        reraise=True,
    )
    async def _call_llm_api(self, prompt: str, trace_id: Optional[str] = None) -> Optional[str]:
        from .core_audit import audit_logger

        prompt = self._sanitize_prompt(prompt)
        trace_id = trace_id or self.trace_id or _generate_trace_id()
        cache_key = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        if REDIS_CLIENT:
            try:
                cached_response = await REDIS_CLIENT.get(cache_key)
                if cached_response:
                    logger.debug("Returning cached response for prompt.")
                    audit_logger.log_event(
                        "llm_api_cache_hit", cache_key=cache_key, trace_id=trace_id
                    )
                    return cached_response
            except Exception as e:
                logger.warning(f"Redis cache unavailable for get: {e}")
        logger.debug(f"Sending prompt to LLM (first 100 chars): {prompt[:100]}...")
        audit_logger.log_event(
            "llm_api_call_start",
            model=self.model_name,
            endpoint=self.llm_endpoint,
            prompt_length=len(prompt),
            max_tokens_requested=self.max_tokens,
            trace_id=trace_id,
        )
        audit_logger.log_event(
            "llm_api_call_input", prompt=scrub_secrets(prompt[:500]), trace_id=trace_id
        )
        response_content = None
        try:
            async with self._api_semaphore:
                estimated_tokens = self._estimate_tokens(prompt) + self.max_tokens
                await self._enforce_token_quota(estimated_tokens)
                response = await self.llm_client.chat.completions.create(
                    model=self.model_name,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=self.max_tokens,
                    temperature=self.temperature,
                    timeout=60,
                )
                response_content = (
                    response.choices[0].message.content.strip() if response.choices else None
                )
                if response_content:
                    logger.debug("Successfully received response from LLM.")
                    audit_logger.log_event(
                        "llm_api_call_success",
                        model=self.model_name,
                        response_length=len(response_content),
                        tokens_used=getattr(response.usage, "total_tokens", None),
                        trace_id=trace_id,
                    )
                    audit_logger.log_event(
                        "llm_api_call_output",
                        response=scrub_secrets(response_content[:500]),
                        trace_id=trace_id,
                    )
                    if REDIS_CLIENT:
                        try:
                            await REDIS_CLIENT.setex(cache_key, 3600, response_content)
                            audit_logger.log_event(
                                "llm_api_cache_set",
                                cache_key=cache_key,
                                trace_id=trace_id,
                            )
                        except Exception as e:
                            logger.warning(f"Redis cache unavailable for set: {e}")
                else:
                    raise RuntimeError("Unexpected empty response from LLM API.")
        except RateLimitError as e:
            logger.error(f"Rate limit exceeded: {e}")
            audit_logger.log_event(
                "llm_api_call_failure",
                model=self.model_name,
                error_type="RateLimitError",
                error_message=str(e),
                trace_id=trace_id,
            )
            raise NonCriticalError(f"LLM rate limit exceeded: {e}")
        except APIError as e:
            logger.error(f"LLM API error: {e}")
            audit_logger.log_event(
                "llm_api_call_failure",
                model=self.model_name,
                error_type="APIError",
                error_message=str(e),
                trace_id=trace_id,
            )
            raise NonCriticalError(f"LLM API error: {e}")
        except Exception as e:
            logger.error(f"An error occurred during LLM API call: {e}", exc_info=True)
            audit_logger.log_event(
                "llm_api_call_failure",
                model=self.model_name,
                error_type=type(e).__name__,
                error_message=str(e),
                trace_id=trace_id,
            )
            alert_operator(f"ERROR: LLM API call failed: {e}.", level="ERROR")
            raise

        return response_content

    async def get_refactoring_suggestion(self, context: str, trace_id: Optional[str] = None) -> str:
        logger.info("Generating AI refactoring suggestion...")
        prompt = (
            f"Given the following context about a Python code issue or area, provide a concise, actionable, high-level refactoring strategy. "
            f"Focus on architectural improvements, design patterns, or common code smells. "
            f"Context: {context}\n\nRefactoring Suggestion:"
        )
        response = await self._call_llm_api(prompt, trace_id=trace_id)
        if response:
            logger.info("AI refactoring suggestion generated.")
            return response
        logger.warning("No AI refactoring suggestion generated.")
        return "No refactoring suggestion could be generated by AI."

    async def get_cycle_breaking_suggestion(
        self,
        cycle_path: List[str],
        relevant_code_snippets: Dict[str, str],
        trace_id: Optional[str] = None,
    ) -> str:
        logger.info(
            f"Generating AI cycle-breaking suggestion for cycle: {' -> '.join(cycle_path)}..."
        )
        code_context_str = "\n\n".join(
            [
                f"Module: {mod_name}\n```python\n{code}\n```"
                for mod_name, code in relevant_code_snippets.items()
            ]
        )
        prompt = (
            f"A circular import dependency has been detected in a Python codebase. "
            f"The cycle path is: {' -> '.join(cycle_path)}. "
            f"Here are relevant code snippets from the modules involved:\n\n{code_context_str}\n\n"
            f"Suggest concrete strategies to break this cycle. Consider options like: "
            f"1. Extracting a common interface/abstraction to a new module. "
            f"2. Using dependency injection. "
            f"3. Splitting a module into smaller, more focused modules. "
            f"4. Moving the problematic import into a function (lazy import). "
            f"Provide actionable steps and, if possible, small Python code examples."
        )
        response = await self._call_llm_api(prompt, trace_id=trace_id)
        if response:
            logger.info("AI cycle-breaking suggestion generated.")
            return response
        logger.warning("No AI cycle-breaking suggestion generated.")
        return "No cycle-breaking suggestion could be generated by AI."

    async def health_check(self) -> Dict[str, Any]:

        results = {}
        try:
            await self._call_llm_api("Say 'pong'.", trace_id=self.trace_id)
            results["llm_api"] = True
        except Exception as e:
            results["llm_api"] = False
            results["llm_api_error"] = str(e)
        if REDIS_CLIENT:
            try:
                await REDIS_CLIENT.ping()
                results["redis"] = True
            except Exception as e:
                results["redis"] = False
                results["redis_error"] = str(e)
        else:
            results["redis"] = False
            results["redis_error"] = "REDIS_CLIENT not initialized."
        return results


# Multi-tenant support: avoid global singleton, use per-tenant manager registry if needed
_ai_manager_instances: Dict[str, AIManager] = {}
_instance_lock = asyncio.Lock()


async def get_ai_manager_instance(
    config: Optional[Dict[str, Any]] = None,
    trace_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> AIManager:
    """Multi-tenant: use tenant_id as key, or use trace_id for session isolation."""
    key = tenant_id or (trace_id or _default_trace_id_from_env())
    async with _instance_lock:
        if key not in _ai_manager_instances:
            _ai_manager_instances[key] = AIManager(config or {}, trace_id=trace_id)
        return _ai_manager_instances[key]


async def get_ai_suggestions(
    codebase_context: str,
    config: Optional[Dict[str, Any]] = None,
    trace_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> List[str]:
    manager = await get_ai_manager_instance(config, trace_id, tenant_id)
    suggestion = await manager.get_refactoring_suggestion(codebase_context, trace_id=trace_id)
    if suggestion and "AI features are unavailable" not in suggestion:
        return [s.strip() for s in suggestion.split("\n") if s.strip()]
    return []


async def get_ai_patch(
    problem_description: str,
    relevant_code: str,
    suggestions: List[str],
    config: Optional[Dict[str, Any]] = None,
    trace_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> List[str]:
    manager = await get_ai_manager_instance(config, trace_id, tenant_id)
    patch_str = await manager.get_cycle_breaking_suggestion(
        cycle_path=[],
        relevant_code_snippets={
            "problem_context": f"Problem: {problem_description}\nSuggestions: {'; '.join(suggestions)}\nCode:\n{relevant_code}"
        },
        trace_id=trace_id,
    )
    if patch_str and "AI features are unavailable" not in patch_str:
        return [p.strip() for p in patch_str.split("\n") if p.strip()]
    return []


async def ai_health_check(
    config: Optional[Dict[str, Any]] = None,
    trace_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> Dict[str, Any]:
    manager = await get_ai_manager_instance(config, trace_id, tenant_id)
    return await manager.health_check()


# Sync wrappers using the new _run_async helper
def get_ai_suggestions_sync(
    codebase_context: str,
    config: Optional[Dict[str, Any]] = None,
    trace_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> List[str]:
    return _run_async(get_ai_suggestions(codebase_context, config, trace_id, tenant_id))


def get_ai_patch_sync(
    problem_description: str,
    relevant_code: str,
    suggestions: List[str],
    config: Optional[Dict[str, Any]] = None,
    trace_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> List[str]:
    return _run_async(
        get_ai_patch(problem_description, relevant_code, suggestions, config, trace_id, tenant_id)
    )


def ai_health_check_sync(
    config: Optional[Dict[str, Any]] = None,
    trace_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> Dict[str, Any]:
    return _run_async(ai_health_check(config, trace_id, tenant_id))


# Example usage (for testing this module independently)
async def main_test():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    logger.setLevel(logging.DEBUG)

    # Mock SECRETS_MANAGER
    class MockSecretsManager:
        def get_secret(self, key, required):
            if key == "OPENAI_API_KEY":
                if "OPENAI_API_KEY" not in os.environ:
                    os.environ["OPENAI_API_KEY"] = "sk-dummy-test-key"
                return os.environ.get("OPENAI_API_KEY")
            return None

    global SECRETS_MANAGER
    SECRETS_MANAGER = MockSecretsManager()

    # Mock audit_logger
    class DummyAuditLogger:
        def log_event(self, event_type, **kwargs):
            logger.info(f"[AUDIT_LOG] {event_type}: {kwargs}")

    # Mock alert_operator and scrub_secrets
    def alert_operator(message, level="CRITICAL"):
        logger.critical(f"[OPS ALERT - {level}] {message}")

    def scrub_secrets(data):
        return data

    sys.modules["core_audit"] = sys.modules["__main__"]
    sys.modules["core_utils"] = sys.modules["__main__"]

    test_config = {
        "llm_api_key_secret_id": "OPENAI_API_KEY",
        "llm_endpoint": "https://api.openai.com/v1/chat/completions",
        "model_name": "gpt-3.5-turbo",
        "temperature": 0.5,
        "max_tokens": 300,
        "proxy_url": None,
        "api_concurrency_limit": 1,
        "token_quota_per_minute": 100,
        "allow_auto_apply_patches": False,
    }
    trace_id = _generate_trace_id()
    tenant_id = "tenant-test-1"

    # Create manager and wrap in a finally block for cleanup
    manager = None
    try:
        manager = await get_ai_manager_instance(test_config, trace_id=trace_id, tenant_id=tenant_id)

        print("\n--- Testing AI Refactoring Suggestion ---")
        sample_refactoring_context = """
        The `data_loader` module directly imports `database_connector`, creating tight coupling.
        We want to introduce an abstraction layer.
        """
        suggestion = await manager.get_refactoring_suggestion(
            sample_refactoring_context, trace_id=trace_id
        )
        print(f"Refactoring Suggestion:\n{suggestion}\n")

        print("\n--- Testing AI Cycle-Breaking Suggestion ---")
        patch = await manager.get_cycle_breaking_suggestion(
            cycle_path=[],
            relevant_code_snippets={
                "problem_context": "Circular import between module_a and module_b.\nSuggestions: Extract interfaces; Use lazy import.\nCode:\nimport module_b\nimport module_a"
            },
            trace_id=trace_id,
        )
        print(f"Cycle-Breaking Suggestion:\n{patch}\n")

        print("\n--- Testing Health Check ---")
        health = await manager.health_check()
        print(f"AI Health Check: {health}\n")
    finally:
        if manager:
            await manager.aclose()


if __name__ == "__main__":
    asyncio.run(main_test())
