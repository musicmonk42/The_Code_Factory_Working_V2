import asyncio
import hashlib
import logging
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx
import tiktoken
from openai import APIError, AsyncOpenAI, RateLimitError
from tenacity import RetryError, retry, stop_after_attempt, wait_exponential

# --- Global Production Mode Flag (from main orchestrator) ---
PRODUCTION_MODE = os.getenv("PRODUCTION_MODE", "false").lower() == "true"

logger = logging.getLogger(__name__)


# --- Custom Exception for critical errors (from analyzer.py) ---
class AnalyzerCriticalError(RuntimeError):
    """
    Custom exception for critical errors that should halt execution and alert ops.
    """

    def __init__(self, message: str, alert_level: str = "CRITICAL"):
        super().__init__(message)
        try:
            alert_operator(message, alert_level)
        except Exception:
            pass


class NonCriticalError(Exception):
    """
    Custom exception for recoverable issues that should be logged but not halt execution.
    """

    pass


# --- Centralized Utilities (replacing placeholders) ---
# Use try/except with graceful fallbacks to avoid circular import issues
try:
    from self_healing_import_fixer.import_fixer.cache_layer import get_cache
    _HAS_CACHE_LAYER = True
except ImportError as e:
    _HAS_CACHE_LAYER = False
    get_cache = None
    logger.warning(f"cache_layer not available: {e}. Caching will be disabled.")

try:
    from self_healing_import_fixer.import_fixer.compat_core import (
        SECRETS_MANAGER,
        alert_operator,
        audit_logger,
        scrub_secrets,
    )
    _HAS_COMPAT_CORE = True
except ImportError as e:
    _HAS_COMPAT_CORE = False
    logger.warning(f"compat_core not available: {e}. Using fallbacks.")
    
    # Fallback implementations
    class _FallbackSecretsManager:
        def get_secret(self, key: str, required: bool = False) -> Optional[str]:
            return os.getenv(key)
    
    SECRETS_MANAGER = _FallbackSecretsManager()
    
    def alert_operator(msg: str, level: str = "WARNING") -> None:
        logger.log(getattr(logging, level, logging.WARNING), f"ALERT: {msg}")
    
    class _FallbackAuditLogger:
        def info(self, msg: str, **kwargs): logger.info(msg)
        def warning(self, msg: str, **kwargs): logger.warning(msg)
        def error(self, msg: str, **kwargs): logger.error(msg)
        def debug(self, msg: str, **kwargs): logger.debug(msg)
        def log_event(self, event: str, **kwargs): logger.info(f"AUDIT: {event}")
    
    audit_logger = _FallbackAuditLogger()
    
    def scrub_secrets(data):
        return data

# --- Caching: Redis Client Initialization ---
_redis_failure_count = 0
_redis_failure_alerted = False
REDIS_ALERT_THRESHOLD = 5
_cache_client = None


async def _get_cache_client():
    global _cache_client
    if not _HAS_CACHE_LAYER or get_cache is None:
        return None
    if _cache_client is None:
        _cache_client = await get_cache()
    return _cache_client


def _redis_alert_on_failure(e):
    global _redis_failure_count, _redis_failure_alerted
    _redis_failure_count += 1
    logger.warning(
        f"Redis cache operation failed: {e} (failure count: {_redis_failure_count})"
    )
    if _redis_failure_count >= REDIS_ALERT_THRESHOLD and not _redis_failure_alerted:
        alert_operator(
            "WARNING: Redis cache is persistently failing. Caching is degraded and performance/costs may suffer.",
            level="WARNING",
        )
        audit_logger.log_event(
            "redis_persistent_failure_alert", failure_count=_redis_failure_count
        )
        _redis_failure_alerted = True


def _reset_redis_failure_counter():
    global _redis_failure_count, _redis_failure_alerted
    _redis_failure_count = 0
    _redis_failure_alerted = False


def _sanitize_prompt(prompt: str) -> str:
    """
    Enhanced prompt sanitization to defend against prompt injection/jailbreak techniques.
    """
    if not isinstance(prompt, str):
        raise ValueError("Prompt must be a string.")

    # Control characters and dangerous unicode
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", prompt):
        raise ValueError("Prompt contains ASCII control characters.")
    if re.search(r"[\u200b-\u200f\u2028\u2029\u202a-\u202e\u2060-\u206f]", prompt):
        raise ValueError("Prompt contains suspicious unicode control characters.")
    # Excessive prompt length
    if len(prompt) > 4096:
        logger.warning(
            "Prompt length is unusually long (>{} chars). Truncating for safety.".format(
                4096
            )
        )
        prompt = prompt[:4096]
    # Too many newlines (overflow attack)
    if prompt.count("\n") > 300:
        logger.warning("Prompt has more than 300 newlines. Truncating for safety.")
        prompt = "\n".join(prompt.splitlines()[:300])
    # Unmatched code blocks
    if prompt.count("```") % 2 != 0:
        logger.warning(
            "Unmatched code block delimiter '```' in prompt. Potential injection attempt."
        )
    # Common prompt injection/jailbreak phrases
    forbidden_patterns = [
        r"(?i)(ignore|disregard|override|bypass).*previous",
        r"(?i)as an ai language model",
        r"\b(system|user|assistant):",
        r"\{.*?\}",
        r"<\s*script",
        r"",
        r"\b(?:base64|eval|exec|import os|subprocess|openai\.api_key)\b",
        r"(?i)you are now in developer mode",
        r"(?i)repeat after me",
        r"(?i)write a jailbreak",
        r"(?i)begin jailbreak",
        r"(?i)simulate being unfiltered",
        r"(?i)do anything now",
        r"(?i)unrestricted code",
        r"(?i)forget all previous",
        r"(?i)disregard all prior",
        r"(?i)simulate prompt injection",
    ]
    for pat in forbidden_patterns:
        if re.search(pat, prompt):
            logger.warning(f"Prompt contains forbidden pattern: {pat}")
            raise ValueError("Prompt contains forbidden or suspicious phrases.")
    return prompt


def _sanitize_response(response: str) -> str:
    """
    Enhanced response sanitization for LLM output.
    """
    # Remove accidental echo of secrets or keys
    scrubbed = scrub_secrets(response)
    # Remove injected system prompts or jailbreak markers
    forbidden_out_patterns = [
        r"(?i)as an ai language model",
        r"(?i)developer mode output",
        r"\b(system|user|assistant):",
        r"(?i)jailbreak",
        r"(?i)ignore all previous",
        r"(?i)disregard all prior",
        r"(?i)simulate prompt injection",
    ]
    for pat in forbidden_out_patterns:
        scrubbed = re.sub(pat, "[REDACTED]", scrubbed)
    # Remove any HTML/script tags
    scrubbed = re.sub(
        r"<\s*script.*?>.*?<\s*/\s*script\s*>",
        "[REDACTED]",
        scrubbed,
        flags=re.DOTALL | re.IGNORECASE,
    )
    return scrubbed


class AIManager:
    """
    Manages AI/LLM integrations for generating refactoring and cycle-breaking suggestions.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}

        self.llm_api_key = SECRETS_MANAGER.get_secret(
            self.config.get("llm_api_key_secret_id", "LLM_API_KEY"),
            required=True if PRODUCTION_MODE else False,
        )
        self.llm_endpoint = self.config.get("llm_endpoint")
        self.model_name = self.config.get("model_name", "gpt-3.5-turbo")
        self.temperature = self.config.get("temperature", 0.7)
        self.max_tokens = self.config.get("max_tokens", 500)
        self.proxy_url = self.config.get("proxy_url")
        self.allow_auto_apply_patches = self.config.get(
            "allow_auto_apply_patches", False
        )

        # Mandatory production checks
        if PRODUCTION_MODE:
            if not self.llm_api_key:
                raise AnalyzerCriticalError(
                    "LLM API key is not configured. AI features cannot function."
                )
            if not self.llm_endpoint or not self.llm_endpoint.startswith("https://"):
                raise AnalyzerCriticalError(
                    "LLM endpoint must use HTTPS in production."
                )
            if not self.proxy_url:
                raise AnalyzerCriticalError(
                    "In PRODUCTION_MODE, 'proxy_url' for LLM traffic is required but not configured."
                )
            if self.allow_auto_apply_patches:
                raise AnalyzerCriticalError(
                    "'allow_auto_apply_patches' is enabled in PRODUCTION_MODE. This is forbidden."
                )

        assert (
            self.llm_endpoint.startswith("https://") or not PRODUCTION_MODE
        ), "LLM endpoint must use HTTPS in production."

        try:
            self.token_encoder = tiktoken.encoding_for_model(self.model_name)
        except Exception:
            logger.warning(
                "Failed to get specific tiktoken encoder. Falling back to cl100k_base."
            )
            self.token_encoder = tiktoken.get_encoding("cl100k_base")

        self.api_concurrency_limit = self.config.get("api_concurrency_limit", 5)
        self.token_quota_per_minute = self.config.get("token_quota_per_minute", 60000)
        self._api_semaphore = asyncio.Semaphore(self.api_concurrency_limit)
        self._token_usage_history: List[Tuple[float, int]] = []
        self._token_usage_lock = asyncio.Lock()

        self.http_client = httpx.AsyncClient(
            proxies=self.proxy_url, verify=True, timeout=90
        )
        self.llm_client = AsyncOpenAI(
            api_key=self.llm_api_key,
            base_url=self.llm_endpoint,
            http_client=self.http_client,
        )
        logger.info(
            f"AIManager initialized for model: {self.model_name}, endpoint: {self.llm_endpoint}"
        )
        audit_logger.log_event(
            "ai_manager_init",
            model=self.model_name,
            endpoint=self.llm_endpoint,
            concurrency_limit=self.api_concurrency_limit,
            token_quota=self.token_quota_per_minute,
            proxy_configured=bool(self.proxy_url),
            auto_apply_patches_allowed=self.allow_auto_apply_patches,
        )

    async def aclose(self):
        """Deterministically close all async clients."""
        try:
            if getattr(self, "llm_client", None) and hasattr(self.llm_client, "close"):
                await self.llm_client.close()
        finally:
            if getattr(self, "http_client", None):
                await self.http_client.aclose()

    def _estimate_tokens(self, text: str) -> int:
        try:
            return len(self.token_encoder.encode(text))
        except Exception:
            return len(text) // 4

    async def _enforce_token_quota(self, tokens_to_use: int):
        async with self._token_usage_lock:
            current_time = time.time()
            self._token_usage_history = [
                (ts, tokens)
                for ts, tokens in self._token_usage_history
                if current_time - ts < 60
            ]
            current_minute_usage = sum(
                tokens for _, tokens in self._token_usage_history
            )
            if current_minute_usage + tokens_to_use > self.token_quota_per_minute:
                oldest_timestamp = (
                    self._token_usage_history[0][0]
                    if self._token_usage_history
                    else current_time - 60
                )
                wait_until = oldest_timestamp + 60
                sleep_duration = max(0, wait_until - current_time) + 1
                logger.warning(
                    f"LLM token quota exceeded. Current usage: {current_minute_usage}, requested: {tokens_to_use}. Waiting for {sleep_duration:.2f}s.",
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
                current_minute_usage = sum(
                    tokens for _, tokens in self._token_usage_history
                )
                if current_minute_usage + tokens_to_use > self.token_quota_per_minute:
                    logger.critical(
                        f"CRITICAL: LLM token quota overrun after waiting. Current usage: {current_minute_usage}, requested: {tokens_to_use}. Aborting API call.",
                        extra={
                            "current_usage": current_minute_usage,
                            "requested_tokens": tokens_to_use,
                        },
                    )
                    audit_logger.log_event(
                        "llm_token_quota_overrun_abort",
                        current_usage=current_minute_usage,
                        requested_tokens=tokens_to_use,
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
    async def _call_llm_api(self, prompt: str) -> Optional[str]:
        cache = await _get_cache_client()
        prompt = _sanitize_prompt(prompt)
        cache_key = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        if cache:
            try:
                cached_response = await cache.get(cache_key)
                _reset_redis_failure_counter()
                if cached_response:
                    logger.debug("Returning cached response for prompt.")
                    audit_logger.log_event("llm_api_cache_hit", cache_key=cache_key)
                    return _sanitize_response(cached_response)
            except Exception as e:
                _redis_alert_on_failure(e)
        logger.debug(f"Sending prompt to LLM (first 100 chars): {prompt[:100]}...")
        audit_logger.log_event(
            "llm_api_call_start",
            model=self.model_name,
            endpoint=self.llm_endpoint,
            prompt_length=len(prompt),
            max_tokens_requested=self.max_tokens,
        )
        audit_logger.log_event("llm_api_call_input", prompt=scrub_secrets(prompt[:500]))
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
                    response.choices[0].message.content.strip()
                    if response.choices
                    else None
                )
                if response_content:
                    logger.debug("Successfully received response from LLM.")
                    audit_logger.log_event(
                        "llm_api_call_success",
                        model=self.model_name,
                        response_length=len(response_content),
                        tokens_used=getattr(response.usage, "total_tokens", None),
                    )
                    audit_logger.log_event(
                        "llm_api_call_output",
                        response=scrub_secrets(response_content[:500]),
                    )
                    if cache:
                        try:
                            await cache.setex(cache_key, 3600, response_content)
                            _reset_redis_failure_counter()
                            audit_logger.log_event(
                                "llm_api_cache_set", cache_key=cache_key
                            )
                        except Exception as e:
                            _redis_alert_on_failure(e)
                else:
                    raise RuntimeError("Unexpected empty response from LLM API.")
        except RateLimitError as e:
            logger.error(f"Rate limit exceeded: {e}")
            audit_logger.log_event(
                "llm_api_call_failure",
                model=self.model_name,
                error_type="RateLimitError",
                error_message=str(e),
            )
            raise NonCriticalError(f"LLM rate limit exceeded: {e}")
        except APIError as e:
            logger.error(f"LLM API error: {e}")
            audit_logger.log_event(
                "llm_api_call_failure",
                model=self.model_name,
                error_type="APIError",
                error_message=str(e),
            )
            raise NonCriticalError(f"LLM API error: {e}")
        except httpx.TimeoutException as e:
            logger.error(f"LLM API timeout: {e}")
            audit_logger.log_event(
                "llm_api_call_failure",
                model=self.model_name,
                error_type="TimeoutException",
                error_message=str(e),
            )
            raise NonCriticalError(f"LLM API timeout: {e}")
        except Exception as e:
            logger.error(f"An error occurred during LLM API call: {e}", exc_info=True)
            audit_logger.log_event(
                "llm_api_call_failure",
                model=self.model_name,
                error_type=type(e).__name__,
                error_message=str(e),
            )
            alert_operator(f"ERROR: LLM API call failed: {e}.", level="ERROR")
            raise
        return _sanitize_response(response_content) if response_content else None

    async def get_refactoring_suggestion(self, context: str) -> str:
        logger.info("Generating AI refactoring suggestion...")
        prompt = (
            f"Given the following context about a Python code issue or area, provide a concise, actionable, high-level refactoring strategy. "
            f"Focus on architectural improvements, design patterns, or common code smells. "
            f"Context: {context}\n\nRefactoring Suggestion:"
        )
        try:
            response = await self._call_llm_api(prompt)
            if response:
                logger.info("AI refactoring suggestion generated.")
                return response
            logger.warning("No AI refactoring suggestion generated.")
            return "No refactoring suggestion could be generated by AI."
        except NonCriticalError as e:
            logger.error(
                f"Refactoring suggestion generation failed due to LLM API issue: {e}"
            )
            return f"Refactoring suggestion generation failed due to LLM API issue: {e}"
        except RetryError:
            logger.error(
                "Refactoring suggestion generation failed after multiple retries."
            )
            return "Refactoring suggestion generation failed after multiple retries."

    async def get_cycle_breaking_suggestion(
        self, cycle_path: List[str], relevant_code_snippets: Dict[str, str]
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
        try:
            response = await self._call_llm_api(prompt)
            if response:
                logger.info("AI cycle-breaking suggestion generated.")
                return response
            logger.warning("No AI cycle-breaking suggestion generated.")
            return "No cycle-breaking suggestion could be generated by AI."
        except NonCriticalError as e:
            logger.error(
                f"Cycle-breaking suggestion generation failed due to LLM API issue: {e}"
            )
            return (
                f"Cycle-breaking suggestion generation failed due to LLM API issue: {e}"
            )
        except RetryError:
            logger.error(
                "Cycle-breaking suggestion generation failed after multiple retries."
            )
            return "Cycle-breaking suggestion generation failed after multiple retries."


# --- Public facing functions (used by analyzer.py) ---
_ai_manager_instance: Optional[AIManager] = None
_instance_lock = asyncio.Lock()


async def _get_ai_manager_instance(
    config: Optional[Dict[str, Any]] = None,
) -> AIManager:
    global _ai_manager_instance
    async with _instance_lock:
        if _ai_manager_instance is None:
            _ai_manager_instance = AIManager(config)
    return _ai_manager_instance


def _run_async_in_sync(coro):
    """
    Helper to run an async coroutine from a synchronous context.
    Safely bridges sync/async environments by checking for a running loop.
    """
    try:
        loop = asyncio.get_running_loop()
        # If we got here, a loop is running. Use run_coroutine_threadsafe.
        return asyncio.run_coroutine_threadsafe(coro, loop).result()
    except RuntimeError:
        # No running loop, so we can safely use asyncio.run()
        return asyncio.run(coro)


def get_ai_suggestions(
    codebase_context: str, config: Optional[Dict[str, Any]] = None
) -> List[str]:
    async def _async_suggestions():
        manager = await _get_ai_manager_instance(config)
        suggestions_str = await manager.get_refactoring_suggestion(codebase_context)
        if (
            suggestions_str
            and "AI features are unavailable" not in suggestions_str
            and "failed due to LLM API issue" not in suggestions_str
            and "failed after multiple retries" not in suggestions_str
        ):
            return [s.strip() for s in suggestions_str.split("\n") if s.strip()]
        return []

    return _run_async_in_sync(_async_suggestions())


def get_ai_patch(
    problem_description: str,
    relevant_code: str,
    suggestions: List[str],
    config: Optional[Dict[str, Any]] = None,
) -> List[str]:
    async def _async_patch():
        manager = await _get_ai_manager_instance(config)
        patch_str = await manager.get_cycle_breaking_suggestion(
            cycle_path=[],
            relevant_code_snippets={
                "problem_context": f"Problem: {problem_description}\nSuggestions: {'; '.join(suggestions)}\nCode:\n{relevant_code}"
            },
        )
        if (
            patch_str
            and "AI features are unavailable" not in patch_str
            and "failed due to LLM API issue" not in patch_str
            and "failed after multiple retries" not in patch_str
        ):
            return [p.strip() for p in patch_str.split("\n") if p.strip()]
        return []

    return _run_async_in_sync(_async_patch())


# Example usage (for testing this module independently)
async def main_test():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
    )
    logger.setLevel(logging.DEBUG)

    class DummySecretsManager:
        def get_secret(
            self, key: str, default: Optional[str] = None, required: bool = True
        ) -> Optional[str]:
            if key == "LLM_API_KEY":
                return os.getenv("OPENAI_API_KEY", "sk-dummy-test-key")
            if required:
                raise ValueError(f"Missing required secret for test: {key}")
            return default

    def alert_operator(message: str, level: str = "CRITICAL"):
        print(f"[OPS ALERT - {level}] {message}")

    def scrub_secrets(data: Any) -> Any:
        return data

    class DummyAuditLogger:
        def log_event(self, event_type: str, **kwargs: Any):
            print(f"[AUDIT_LOG] {event_type}: {kwargs}")

    class DummyCache:
        def __init__(self):
            self.cache = {}

        async def get(self, key):
            return self.cache.get(key)

        async def setex(self, key, expiry, value):
            self.cache[key] = value

        async def ping(self):
            return True

    async def get_cache(*a, **k):
        return DummyCache()

    sys.modules["core_utils"] = sys.modules["__main__"]
    sys.modules["core_audit"] = sys.modules["__main__"]
    sys.modules["core_secrets"] = sys.modules["__main__"]
    DummySecretsManager()
    DummyAuditLogger()

    if "OPENAI_API_KEY" not in os.environ:
        os.environ["OPENAI_API_KEY"] = "sk-dummy-test-key"

    test_config = {
        "llm_api_key_secret_id": "LLM_API_KEY",
        "llm_endpoint": "https://api.openai.com/v1/chat/completions",
        "model_name": "gpt-3.5-turbo",
        "temperature": 0.5,
        "max_tokens": 300,
        "proxy_url": None,
        "api_concurrency_limit": 1,
        "token_quota_per_minute": 100,
        "allow_auto_apply_patches": False,
    }

    print("\n--- Testing AI Refactoring Suggestion ---")
    sample_refactoring_context = """
    The `data_loader` module directly imports `database_connector`, creating tight coupling.
    We want to introduce an abstraction layer.
    """
    suggestion = get_ai_suggestions(sample_refactoring_context, test_config)
    print(f"Refactoring Suggestion:\n{suggestion}\n")

    print("\n--- Testing AI Cycle-Breaking Suggestion ---")
    cycle_suggestion = get_ai_patch(
        "Circular import between modules",
        "some code",
        ["Extract interfaces"],
        test_config,
    )
    print(f"Cycle-Breaking Suggestion:\n{cycle_suggestion}\n")

    print("\n--- Testing AI with Quota Overrun (expecting abort) ---")
    test_config["token_quota_per_minute"] = 10
    try:
        await _get_ai_manager_instance(test_config)
        await asyncio.run(
            _ai_manager_instance._call_llm_api(
                "Generate a very long suggestion about complex architecture patterns and microservices."
            )
        )
    except RuntimeError as e:
        print(f"Caught expected RuntimeError due to quota overrun: {e}")
    finally:
        test_config["token_quota_per_minute"] = 60000

    print("\n--- Testing PRODUCTION_MODE with auto_apply_patches (expecting abort) ---")
    os.environ["PRODUCTION_MODE"] = "true"
    test_config["allow_auto_apply_patches"] = True
    try:
        await _get_ai_manager_instance(test_config)
    except AnalyzerCriticalError as e:
        print(
            f"Caught expected AnalyzerCriticalError due to PRODUCTION_MODE + auto_apply_patches=True: {e}"
        )
    finally:
        os.environ["PRODUCTION_MODE"] = "false"
        test_config["allow_auto_apply_patches"] = False


if __name__ == "__main__":
    # In a real-world scenario, you might have a main entry point for your application.
    # This is a self-contained example for testing this specific file.
    asyncio.run(main_test())
