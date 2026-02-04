# plugins/security_patch_generator_plugin.py

import asyncio
import hashlib
import json
import logging
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

# --- Manifest aligned with PluginManager schema (AST-extracted by the manager) ---
PLUGIN_MANIFEST = {
    "name": "SecurityPatchGeneratorPlugin",
    "version": "1.1.1",
    "description": "Generates AI-powered code patches for identified security vulnerabilities.",
    "author": "Self-Fixing Engineer Team",
    "capabilities": ["ai_security_patch_generation", "code_remediation"],
    "permissions": ["llm_access_external"],
    "dependencies": [],
    "type": "python",
    "entrypoint": "plugin_health",
    "health_check": "plugin_health",
    "api_version": "v1",
    "min_core_version": "0.0.0",
    "max_core_version": "9.9.9",
    "license": "MIT",
    "homepage": "https://www.self-fixing.engineer",
    "tags": ["security", "ai", "patch", "code_remediation", "vulnerability"],
    "sandbox": {"enabled": False},
    "manifest_version": "2.0",
}

# --- Logger Setup ---
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

# --- Conditional Imports for LangChain, Tenacity, Pydantic, etc. ---
try:
    from langchain_core.language_models import BaseChatModel as _LCBaseChatModel
    from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
    from langchain_core.outputs import ChatResult
    from langchain_core.prompts import PromptTemplate

    LANGCHAIN_AVAILABLE = True
except ImportError as e:
    logger.warning(
        f"LangChain libraries not found ({e}). Security patch generation will use the 'generic_llm_client' interface if configured."
    )
    # Use a unique dummy class so isinstance checks do not accidentally match arbitrary objects.
    _LCBaseChatModel = type("LCBaseChatModel", (), {})
    PromptTemplate = None  # Will guard before use
    ChatResult = None
    BaseMessage = None
    HumanMessage = None
    SystemMessage = None
    LANGCHAIN_AVAILABLE = False

try:
    from tenacity import (
        retry,
        retry_if_exception_type,
        stop_after_attempt,
        wait_exponential,
    )

    TENACITY_AVAILABLE = True
except ImportError:
    TENACITY_AVAILABLE = False

    def retry(*args, **kwargs):
        return lambda f: f

    def stop_after_attempt(n):
        return None

    def wait_exponential(*args, **kwargs):
        return None

    def retry_if_exception_type(e):
        return lambda x: False


try:
    import threading

    from prometheus_client import REGISTRY, Counter, Histogram

    PROMETHEUS_AVAILABLE = True

    # Lock for thread-safe metric creation
    _metrics_lock = threading.Lock()

    def _get_or_create_metric(
        metric_type: type,
        name: str,
        documentation: str,
        labelnames: Optional[Tuple[str, ...]] = None,
        buckets: Optional[Tuple[float, ...]] = None,
    ) -> Any:
        labelnames = labelnames or ()
        with _metrics_lock:
            try:
                names_map = getattr(REGISTRY, "_names_to_collectors", None)
                if isinstance(names_map, dict) and name in names_map:
                    return names_map[name]
            except Exception:
                pass
            try:
                if metric_type == Histogram:
                    return metric_type(
                        name,
                        documentation,
                        labelnames=labelnames,
                        buckets=buckets or Histogram.DEFAULT_BUCKETS,
                    )
                if metric_type == Counter:
                    return metric_type(name, documentation, labelnames=labelnames)
                return metric_type(name, documentation, labelnames=labelnames)
            except ValueError:
                try:
                    names_map = getattr(REGISTRY, "_names_to_collectors", None)
                    if isinstance(names_map, dict) and name in names_map:
                        return names_map[name]
                except Exception:
                    pass

                class _Dummy:
                    def inc(self, *a, **k):
                        pass

                    def set(self, *a, **k):
                        pass

                    def observe(self, *a, **k):
                        pass

                    def labels(self, *a, **k):
                        return self

                return _Dummy()

except ImportError:
    PROMETHEUS_AVAILABLE = False
    logger.warning(
        "Prometheus client not found. Metrics for security patch generator will be disabled."
    )

    class _Dummy:
        def inc(self, *a, **k):
            pass

        def set(self, *a, **k):
            pass

        def observe(self, *a, **k):
            pass

        def labels(self, *a, **k):
            return self

    def _get_or_create_metric(*args, **kwargs) -> Any:
        return _Dummy()


try:
    from pydantic import BaseModel, Field, ValidationError

    PYDANTIC_AVAILABLE = True
except ImportError:
    PYDANTIC_AVAILABLE = False

try:
    from detect_secrets.core import SecretsCollection
    from detect_secrets.settings import transient_settings

    DETECT_SECRETS_AVAILABLE = True
except ImportError:
    DETECT_SECRETS_AVAILABLE = False

try:
    from redis.asyncio import Redis

    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False

# --- Pydantic Config Model ---
if PYDANTIC_AVAILABLE:

    class LLMPatchGenConfig(BaseModel):
        llm_provider_name: str = Field(default="openai")
        llm_model_name: str = Field(default="gpt-4o-mini")
        llm_temperature: float = Field(default=0.2, ge=0.0, le=1.0)
        llm_max_tokens: int = Field(default=1024, ge=1)
        llm_timeout_seconds: int = Field(default=90, ge=1)
        retry_attempts: int = Field(default=2, ge=0)
        retry_backoff_factor: float = Field(default=2.0, ge=0)
        llm_interface_type: str = Field(
            default="langchain", pattern="^(langchain|generic_llm_client)$"
        )
        llm_system_prompt: str = Field(...)
        redis_cache_url: Optional[str] = None
        redis_cache_ttl: int = Field(default=3600, ge=1)
        health_live_call: bool = Field(default=False)

else:

    class LLMPatchGenConfig:
        def __init__(self):
            self.llm_provider_name = "openai"
            self.llm_model_name = "gpt-4o-mini"
            self.llm_temperature = 0.2
            self.llm_max_tokens = 1024
            self.llm_timeout_seconds = 90
            self.retry_attempts = 2
            self.retry_backoff_factor = 2.0
            self.llm_interface_type = "langchain"
            self.llm_system_prompt = ""
            self.redis_cache_url = None
            self.redis_cache_ttl = 3600
            self.health_live_call = False


# --- Load Config from File or Env ---
def _load_config() -> LLMPatchGenConfig:
    config_file_path = (
        Path(__file__).parent / "configs" / "security_patch_gen_config.json"
    )
    config_dict: Dict[str, Any] = {}
    if config_file_path.exists():
        try:
            with open(config_file_path, "r", encoding="utf-8") as f:
                config_dict = json.load(f)
        except (IOError, json.JSONDecodeError) as e:
            logger.warning(
                f"Could not load config file {config_file_path}: {e}. Using environment variables and defaults."
            )

    # Env overrides (best-effort types)
    for key in getattr(LLMPatchGenConfig, "__annotations__", {}).keys():
        env_var = os.getenv(f"PATCH_GEN_{key.upper()}")
        if env_var is None:
            continue
        try:
            if key in {"llm_temperature", "retry_backoff_factor"}:
                config_dict[key] = float(env_var)
            elif key in {
                "llm_max_tokens",
                "llm_timeout_seconds",
                "retry_attempts",
                "redis_cache_ttl",
            }:
                config_dict[key] = int(env_var)
            elif key in {"health_live_call"}:
                config_dict[key] = str(env_var).strip().lower() in (
                    "1",
                    "true",
                    "t",
                    "yes",
                    "y",
                    "on",
                )
            else:
                config_dict[key] = env_var
        except ValueError:
            logger.warning(
                f"Invalid type for environment variable PATCH_GEN_{key.upper()}. Using default."
            )

    # Default system prompt if not set
    config_dict["llm_system_prompt"] = config_dict.get(
        "llm_system_prompt",
        """
You are a highly skilled and ethical AI security engineer. Your task is to generate a code patch
to fix a specific security vulnerability.

Guidelines:
1. Safety First: ONLY generate code that fixes the vulnerability and is safe. NEVER introduce new vulnerabilities, backdoors, or malicious code.
2. Minimal Changes: Propose the smallest, most targeted change necessary to fix the issue.
3. Correctness: Ensure the patch is syntactically correct and logically sound.
4. Explain: Provide a brief, clear explanation of the vulnerability and the proposed fix.
5. Format: Prefer unified diff format if appropriate; otherwise provide a clear, minimal code block.
6. Ethical Hacking Context: This is for DEFENSIVE purposes. Never generate exploits.
7. No Harm: If you cannot generate a safe and effective fix, state clearly that a manual fix is required.
8. Do not reproduce secrets found in the input; never include hard-coded secrets in patches.
""".strip(),
    )

    # Health call toggle (avoid paid calls in health by default)
    if "health_live_call" not in config_dict:
        config_dict["health_live_call"] = str(
            os.getenv("PATCH_GEN_HEALTH_LIVE_CALL", "false")
        ).lower() in ("1", "true", "yes", "on")

    if PYDANTIC_AVAILABLE:
        try:
            return LLMPatchGenConfig.model_validate(config_dict)
        except Exception as e:
            logger.error(f"Configuration validation failed: {e}. Using defaults.")
            return LLMPatchGenConfig(llm_system_prompt=config_dict["llm_system_prompt"])
    else:
        cfg = LLMPatchGenConfig()
        cfg.__dict__.update(config_dict)
        return cfg


LLM_PATCH_GEN_CONFIG = _load_config()

# --- Prometheus Metrics ---
PATCH_GENERATION_ATTEMPTS = _get_or_create_metric(
    Counter,
    "security_patch_gen_attempts_total",
    "Total security patch generation attempts",
    ("vulnerability_type",),
)
PATCH_GENERATION_SUCCESS = _get_or_create_metric(
    Counter,
    "security_patch_gen_success_total",
    "Total successful security patch generations",
    ("vulnerability_type",),
)
PATCH_GENERATION_ERRORS = _get_or_create_metric(
    Counter,
    "security_patch_gen_errors_total",
    "Total errors during security patch generation",
    ("vulnerability_type", "error_type"),
)
LLM_PATCH_GEN_LATENCY_SECONDS = _get_or_create_metric(
    Histogram,
    "security_patch_gen_llm_latency_seconds",
    "Latency of LLM calls for patch generation",
    ("vulnerability_type",),
)
PATCH_COMPLEXITY = _get_or_create_metric(
    Histogram,
    "security_patch_complexity_lines",
    "Number of lines in generated patches",
    ("vulnerability_type",),
)
LLM_TOKEN_USAGE = _get_or_create_metric(
    Counter, "security_patch_llm_token_usage", "Token usage for LLM calls", ("type",)
)


# --- Abstracted LLM Interface for pluggability ---
class LLMClientWrapper:
    def __init__(self, llm_backend: Union[_LCBaseChatModel, Callable]):
        self._llm_backend = llm_backend

    async def generate_text(
        self, messages: List[Dict[str, str]], **kwargs: Any
    ) -> Tuple[str, Optional[Dict[str, Any]]]:
        # LangChain backend
        if LANGCHAIN_AVAILABLE and isinstance(self._llm_backend, _LCBaseChatModel):
            # Build LC message list
            langchain_messages = [
                (
                    SystemMessage(content=m["content"])
                    if m.get("role") == "system"
                    else HumanMessage(content=m.get("content", ""))
                )
                for m in messages
            ]
            generation_info = None
            # Prefer agenerate with batch to extract generation_info
            try:
                resp: ChatResult = await self._llm_backend.agenerate(
                    [langchain_messages], **kwargs
                )
                gen0 = resp.generations[0][0]
                text = (
                    getattr(gen0, "text", "")
                    or getattr(gen0, "message", None)
                    and getattr(gen0.message, "content", "")
                )
                generation_info = getattr(gen0, "generation_info", None) or {}
                # Token usage (best-effort)
                try:
                    usage = {}
                    if isinstance(generation_info, dict):
                        usage = (
                            generation_info.get("token_usage")
                            or generation_info.get("usage")
                            or {}
                        )
                    for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
                        if k in usage:
                            LLM_TOKEN_USAGE.labels(type=k).inc(float(usage.get(k, 0)))
                except Exception:
                    pass
                return str(text or ""), generation_info
            except AttributeError:
                # Fallback to ainvoke
                msg = await self._llm_backend.ainvoke(langchain_messages, **kwargs)
                text = getattr(msg, "content", "") or ""
                return str(text), {}
        # Generic callable backend:
        backend = self._llm_backend
        # Accept either an object with async generate_text(messages_text, **kwargs)
        # or any awaitable callable(messages_text, **kwargs) -> (text, info)
        prompt_text = "\n".join(
            [f"{m.get('role','user').upper()}: {m.get('content','')}" for m in messages]
        )
        # Method 1: has generate_text
        gen_fn = getattr(backend, "generate_text", None)
        if callable(gen_fn):
            maybe = gen_fn(prompt_text, **kwargs)
            if asyncio.iscoroutine(maybe):
                text_response, info = await maybe
            else:
                text_response, info = maybe  # sync (rare); not awaited
            return str(text_response or ""), info if isinstance(info, dict) else {}
        # Method 2: backend itself is callable
        if callable(backend):
            maybe = backend(prompt_text, **kwargs)
            if asyncio.iscoroutine(maybe):
                text_response, info = await maybe
            else:
                text_response, info = maybe
            return str(text_response or ""), info if isinstance(info, dict) else {}
        raise TypeError(
            "Unsupported LLM backend interface; expected LangChain ChatModel or a callable/generate_text API."
        )


_llm_client_instance: Optional[LLMClientWrapper] = None


async def _get_llm_client() -> LLMClientWrapper:
    global _llm_client_instance
    if _llm_client_instance is not None:
        return _llm_client_instance

    interface = LLM_PATCH_GEN_CONFIG.llm_interface_type
    if interface == "langchain":
        if not LANGCHAIN_AVAILABLE:
            # Try to degrade to generic client if available
            try:
                from self_fixing_engineer.arbiter.plugins.llm_client import LLMClient

                generic_llm_client = LLMClient(
                    provider=LLM_PATCH_GEN_CONFIG.llm_provider_name,
                    model=LLM_PATCH_GEN_CONFIG.llm_model_name,
                    api_key=os.getenv(
                        f"{LLM_PATCH_GEN_CONFIG.llm_provider_name.upper()}_API_KEY"
                    ),
                    temperature=LLM_PATCH_GEN_CONFIG.llm_temperature,
                    max_tokens=LLM_PATCH_GEN_CONFIG.llm_max_tokens,
                    timeout=LLM_PATCH_GEN_CONFIG.llm_timeout_seconds,
                )
                _llm_client_instance = LLMClientWrapper(generic_llm_client)
                logger.info(
                    "LangChain not available; using generic LLM client instead."
                )
                return _llm_client_instance
            except Exception:
                raise ImportError(
                    "LangChain is required for 'langchain' interface and no generic LLM client is available."
                )
        # Use central factory if present
        try:
            from simulation.explain import get_llm_by_provider_name

            langchain_llm = get_llm_by_provider_name(
                LLM_PATCH_GEN_CONFIG.llm_provider_name,
                model_name_override=LLM_PATCH_GEN_CONFIG.llm_model_name,
                temperature_override=LLM_PATCH_GEN_CONFIG.llm_temperature,
                max_tokens_override=LLM_PATCH_GEN_CONFIG.llm_max_tokens,
                timeout_override=LLM_PATCH_GEN_CONFIG.llm_timeout_seconds,
            )
            _llm_client_instance = LLMClientWrapper(langchain_llm)
            return _llm_client_instance
        except Exception as e:
            logger.warning(
                f"Central LLM factory not available ({e}). Attempting basic LangChain initialization."
            )
            try:
                from langchain_openai import ChatOpenAI

                api_key = os.getenv("OPENAI_API_KEY")
                if not api_key:
                    raise ValueError(
                        "OPENAI_API_KEY environment variable not set for basic LangChain fallback."
                    )
                langchain_llm = ChatOpenAI(
                    model=LLM_PATCH_GEN_CONFIG.llm_model_name,
                    temperature=LLM_PATCH_GEN_CONFIG.llm_temperature,
                    max_tokens=LLM_PATCH_GEN_CONFIG.llm_max_tokens,
                    request_timeout=LLM_PATCH_GEN_CONFIG.llm_timeout_seconds,
                    api_key=api_key,
                )
                _llm_client_instance = LLMClientWrapper(langchain_llm)
                return _llm_client_instance
            except Exception as e2:
                logger.error(f"Failed to initialize LangChain client: {e2}")
                raise
    elif interface == "generic_llm_client":
        try:
            from self_fixing_engineer.arbiter.plugins.llm_client import LLMClient

            generic_llm_client = LLMClient(
                provider=LLM_PATCH_GEN_CONFIG.llm_provider_name,
                model=LLM_PATCH_GEN_CONFIG.llm_model_name,
                api_key=os.getenv(
                    f"{LLM_PATCH_GEN_CONFIG.llm_provider_name.upper()}_API_KEY"
                ),
                temperature=LLM_PATCH_GEN_CONFIG.llm_temperature,
                max_tokens=LLM_PATCH_GEN_CONFIG.llm_max_tokens,
                timeout=LLM_PATCH_GEN_CONFIG.llm_timeout_seconds,
            )
            _llm_client_instance = LLMClientWrapper(generic_llm_client)
            return _llm_client_instance
        except Exception as e:
            logger.error(f"Failed to initialize generic LLMClient: {e}", exc_info=True)
            raise
    else:
        raise ValueError(f"Unsupported LLM interface type: {interface}")


# --- Health Check ---
async def plugin_health() -> Dict[str, Any]:
    status = "ok"
    details: List[str] = []
    try:
        llm_client = await _get_llm_client()
        details.append(
            f"LLM client interface acquired: {LLM_PATCH_GEN_CONFIG.llm_interface_type}."
        )
        if LLM_PATCH_GEN_CONFIG.health_live_call:
            test_messages = [
                {"role": "system", "content": "You are a test bot."},
                {"role": "user", "content": "ping"},
            ]
            try:
                test_response_text, _ = await asyncio.wait_for(
                    llm_client.generate_text(messages=test_messages, timeout=5),
                    timeout=7,
                )
                if test_response_text and len(test_response_text) > 0:
                    details.append("LLM inference test successful.")
                else:
                    status = "degraded"
                    details.append("LLM inference test returned empty response.")
            except Exception as e:
                status = "degraded"
                details.append(f"LLM live inference check failed: {e}")
        else:
            details.append(
                "Live LLM call in health check is disabled (PATCH_GEN_HEALTH_LIVE_CALL=false)."
            )
    except Exception as e:
        status = "error"
        details.append(
            f"Failed to acquire LLM client: {e}. Check dependencies, API keys, and network."
        )
        logger.error(details[-1], exc_info=True)
    logger.info(f"Plugin health check: {status}")
    return {"status": status, "details": details}


# --- UTILITY FOR LLM OUTPUT PARSING AND VALIDATION ---
def _looks_like_unified_diff(text: str) -> bool:
    lines = text.strip().splitlines()
    if len(lines) < 2:
        return False
    if not lines[0].startswith("--- ") or not lines[1].startswith("+++ "):
        return False
    for ln in lines[2:]:
        if (
            ln.startswith("@@ ")
            or ln.startswith("+")
            or ln.startswith("-")
            or ln.startswith(" ")
        ):
            return True
    return False


# Tighter diff pattern: capture from first header lines until next code fence, explanation marker, or end
DIFF_PATTERN = re.compile(
    r"(?ms)^--- [^\n]+\n\+\+\+ [^\n]+\n.*?(?=^```|^\s*Explanation:|^\s*Reasoning:|\Z)",
    re.MULTILINE,
)
CODE_BLOCK_PATTERN = re.compile(
    r"^(.*?)(```(?:\s*(\w+))?\n(.*?)\n```)(.*)$", re.DOTALL | re.MULTILINE
)
EXPLANATION_DELIMITERS = [
    re.compile(r"\nExplanation:\s*", re.IGNORECASE),
    re.compile(r"\nReasoning:\s*", re.IGNORECASE),
    re.compile(r"\n(?:Vulnerability|Fix) Explanation:\s*", re.IGNORECASE),
]


def _parse_llm_output(
    generated_content: str, code_language: str = "python"
) -> Tuple[Optional[str], Optional[str], Optional[str], bool]:
    generated_content = (
        (generated_content or "").strip().replace("\r\n", "\n").replace("\r", "\n")
    )
    # Try to extract a unified diff
    diff_match = DIFF_PATTERN.search(generated_content)
    if diff_match:
        patch_content = diff_match.group(0).strip()
        if _looks_like_unified_diff(patch_content):
            remaining_content = generated_content.replace(patch_content, "", 1).strip()
            explanation = (
                remaining_content
                if remaining_content
                else "AI provided a unified diff patch."
            )
            logger.debug("Extracted unified diff patch.")
            return patch_content, explanation, None, True

    # Try to extract a fenced code block
    code_block_match = CODE_BLOCK_PATTERN.search(generated_content)
    if code_block_match:
        explanation_prefix = (code_block_match.group(1) or "").strip()
        code_content = (code_block_match.group(4) or "").strip()
        explanation_suffix = (code_block_match.group(5) or "").strip()
        patch_content = code_content
        explanation = f"{explanation_prefix}\n{explanation_suffix}".strip()
        if not explanation:
            explanation = "AI provided a code block patch."
        logger.debug("Extracted code block patch.")
        return patch_content, explanation, None, False

    # Try generic delimiter split
    for pattern in EXPLANATION_DELIMITERS:
        match = pattern.search(generated_content)
        if match:
            explanation_start_index = match.start()
            patch_candidate = generated_content[:explanation_start_index].strip()
            explanation_candidate = generated_content[match.end() :].strip()
            if len(explanation_candidate) > 20:
                patch_content = (
                    patch_candidate if patch_candidate else generated_content
                )
                explanation = explanation_candidate
                logger.debug("Extracted patch and explanation using delimiter.")
                return (
                    patch_content,
                    explanation,
                    None,
                    _looks_like_unified_diff(patch_candidate),
                )

    # Otherwise treat entire content as patch or refusal
    if (
        "manual fix required" in generated_content.lower()
        or "cannot generate a safe and effective fix" in generated_content.lower()
    ):
        logger.debug("LLM explicitly indicated no safe fix could be generated.")
        return (
            None,
            "AI indicated a manual fix is required or it could not generate a safe fix.",
            "Refusal",
            False,
        )

    return (
        generated_content,
        "Unstructured patch; manual review recommended.",
        "Unstructured",
        _looks_like_unified_diff(generated_content),
    )


def _validate_patch_syntax(proposed_patch: str, language: str) -> Tuple[bool, str]:
    if not isinstance(proposed_patch, str):
        return False, "not_string"
    lang = (language or "").lower()
    if lang == "python":
        try:
            import ast

            ast.parse(proposed_patch)
            logger.debug("Proposed patch (Python) passed basic AST parsing.")
            return True, "validated"
        except SyntaxError as e:
            logger.warning(f"Proposed Python patch failed basic syntax check: {e}")
            return False, "syntax_error"
        except Exception as e:
            logger.warning(f"Error during Python patch syntax check: {e}")
            return False, "validation_error"
    # For non-Python languages, skip strict validation
    return True, "skipped"


# High-confidence secret patterns (used to block)
_HIGH_CONF_SECRET_REGEXES = [
    re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS Access Key
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9\-\._~\+\/]+=*"),  # Bearer tokens
    re.compile(
        r"(?:eyJ[0-9A-Za-z_\-]{10,}\.[0-9A-Za-z_\-]{10,}\.[0-9A-Za-z_\-]{10,})"
    ),  # JWT-like
    re.compile(r"[A-Za-z0-9_\-]{32,}"),  # long random tokens
]


def _validate_vuln_details(details: Dict[str, Any]):
    # Only block on high-confidence secret-looking values
    for key, value in (details or {}).items():
        if isinstance(value, str):
            for rx in _HIGH_CONF_SECRET_REGEXES:
                if rx.search(value):
                    raise ValueError(
                        f"High-confidence secret-like value detected in vulnerability details key: {key}"
                    )
    return True


# --- Basic scrubbing fallback for secrets (used even if detect-secrets is absent) ---
_SECRET_REGEXES = [
    re.compile(
        r'(?i)(api[_-]?key|secret|token)\s*[:=]\s*([^\s\'";]+)'
    ),  # key=value style
    re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS Access Key
    re.compile(r'(?i)\bpasswd\s*[:=]\s*[^\s\'";]+'),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9\-\._~\+\/]+=*"),
    re.compile(r"[A-Za-z0-9_\-]{24,}"),  # long tokens
]


def _basic_scrub(text: str) -> str:
    if not isinstance(text, str):
        return text
    redacted = text
    for idx, rx in enumerate(_SECRET_REGEXES):
        if idx == 0:
            # Preserve the key name only; never echo the secret value
            redacted = rx.sub(lambda m: f"{m.group(1)}=[REDACTED]", redacted)
        else:
            redacted = rx.sub("[REDACTED]", redacted)
    return redacted


def _scrub_secrets(data: Union[Dict, List, str, None]) -> Union[Dict, List, str, None]:
    if data is None:
        return None
    if isinstance(data, str):
        text = data
        if DETECT_SECRETS_AVAILABLE:
            try:
                sc = SecretsCollection()
                sc.scan_string(text)
                for secret in sc:
                    try:
                        text = text.replace(secret.secret_value, "[REDACTED]")
                    except Exception:
                        pass
            except Exception:
                pass
        text = _basic_scrub(text)
        return text
    if isinstance(data, dict):
        return {k: _scrub_secrets(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_scrub_secrets(item) for item in data]
    return data


# --- Caching helpers ---
async def _get_cached_patch(cache_key: str) -> Optional[Dict[str, Any]]:
    if not REDIS_AVAILABLE or not LLM_PATCH_GEN_CONFIG.redis_cache_url:
        return None
    try:
        redis = Redis.from_url(LLM_PATCH_GEN_CONFIG.redis_cache_url)
        cached_result = await redis.get(cache_key)
        await redis.aclose()
        if cached_result:
            logger.info(f"Returning cached patch for key: {cache_key}")
            return json.loads(cached_result)
    except Exception as e:
        logger.error(f"Failed to retrieve from Redis cache: {e}")
    return None


async def _cache_patch_result(cache_key: str, result: Dict[str, Any]):
    if not REDIS_AVAILABLE or not LLM_PATCH_GEN_CONFIG.redis_cache_url:
        return
    try:
        redis = Redis.from_url(LLM_PATCH_GEN_CONFIG.redis_cache_url)
        await redis.set(
            cache_key, json.dumps(result), ex=LLM_PATCH_GEN_CONFIG.redis_cache_ttl
        )
        await redis.aclose()
        logger.info(f"Cached patch for key: {cache_key}")
    except Exception as e:
        logger.error(f"Failed to set Redis cache: {e}")


# --- Retry policy (transient errors only), if Tenacity available ---
_transient_errors: Tuple[type, ...] = (asyncio.TimeoutError, TimeoutError, OSError)
try:
    import httpx  # type: ignore

    _transient_errors = _transient_errors + (
        getattr(httpx, "HTTPError", Exception),
        getattr(httpx, "TransportError", Exception),
        getattr(httpx, "ReadTimeout", Exception),
        getattr(httpx, "ConnectTimeout", Exception),
    )
except Exception:
    pass
try:
    import aiohttp  # type: ignore

    _transient_errors = _transient_errors + (
        getattr(aiohttp, "ClientError", Exception),
    )
except Exception:
    pass

_retry_decorator = retry(
    stop=stop_after_attempt(LLM_PATCH_GEN_CONFIG.retry_attempts),
    wait=wait_exponential(
        multiplier=LLM_PATCH_GEN_CONFIG.retry_backoff_factor, min=1, max=10
    ),
    retry=retry_if_exception_type(_transient_errors) if TENACITY_AVAILABLE else None,
)


@_retry_decorator
async def generate_security_patch(
    vulnerability_details: Dict[str, Any],
    vulnerable_code_snippet: str,
    context: Optional[Dict[str, Any]] = None,
    llm_params: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Generates an AI-powered code patch to fix a security vulnerability.
    """
    if not isinstance(vulnerability_details, dict):
        raise TypeError("vulnerability_details must be a dictionary.")
    if not isinstance(vulnerable_code_snippet, str):
        raise TypeError("vulnerable_code_snippet must be a string.")
    if context is not None and not isinstance(context, dict):
        raise TypeError("context must be a dictionary or None.")
    if llm_params is not None and not isinstance(llm_params, dict):
        raise TypeError("llm_params must be a dictionary or None.")

    context = context or {}
    _validate_vuln_details(
        vulnerability_details
    )  # Security check for high-confidence secrets

    # Deterministic cache key with sha256 of normalized inputs
    vuln_json = json.dumps(vulnerability_details, sort_keys=True, separators=(",", ":"))
    ctx_json = json.dumps(context, sort_keys=True, separators=(",", ":"))
    key_basis = f"{vuln_json}||{vulnerable_code_snippet}||{ctx_json}".encode(
        "utf-8", "ignore"
    )
    cache_key = "patch:" + hashlib.sha256(key_basis).hexdigest()

    cached_result = await _get_cached_patch(cache_key)
    if cached_result:
        cached_result = dict(cached_result)
        cached_result.setdefault("cache_hit", True)
        return cached_result

    patch_id = f"sfe-sec-patch-{uuid.uuid4().hex[:8]}"
    vulnerability_type = vulnerability_details.get("type", "unknown_vulnerability")
    code_language = context.get("language", "Python")

    if PROMETHEUS_AVAILABLE:
        PATCH_GENERATION_ATTEMPTS.labels(vulnerability_type=vulnerability_type).inc()

    llm_client = await _get_llm_client()
    start_time = time.monotonic()

    result: Dict[str, Any] = {
        "success": False,
        "patch_id": patch_id,
        "vulnerability_type": vulnerability_type,
        "proposed_patch": None,
        "explanation": "Failed to generate patch.",
        "llm_reasoning_trace": {},
        "status_reason": "Patch generation failed due to an internal error or LLM refusal.",
        "error": None,
        "is_diff": False,
        "patch_lines": 0,
        "syntax_validation": "skipped",
        "cache_hit": False,
    }
    try:
        framework = context.get("framework", "general")
        system_prompt = LLM_PATCH_GEN_CONFIG.llm_system_prompt

        # If LangChain isn't available for langchain interface, PromptTemplate may be None; fallback to simple format
        if LANGCHAIN_AVAILABLE and PromptTemplate is not None:
            human_prompt_template = PromptTemplate.from_template(
                "Vulnerability Details:\n{vuln_details_json}\n"
                "Vulnerable Code Snippet ({code_language}):\n```\n{code_snippet}\n```\n"
                "Codebase Context (Framework: {framework}):\n{context_json}\n"
                "Based on the above, provide a code patch to fix this vulnerability and a brief explanation."
            )
            human_prompt = human_prompt_template.format(
                vuln_details_json=json.dumps(vulnerability_details, indent=2),
                code_language=code_language,
                code_snippet=vulnerable_code_snippet,
                framework=framework,
                context_json=json.dumps(context, indent=2),
            )
        else:
            human_prompt = (
                f"Vulnerability Details:\n{json.dumps(vulnerability_details, indent=2)}\n"
                f"Vulnerable Code Snippet ({code_language}):\n```\n{vulnerable_code_snippet}\n```\n"
                f"Codebase Context (Framework: {framework}):\n{json.dumps(context, indent=2)}\n"
                f"Provide a minimal, SAFE patch and a brief explanation."
            )

        llm_call_params = {
            "temperature": (llm_params or {}).get(
                "temperature", LLM_PATCH_GEN_CONFIG.llm_temperature
            ),
            "max_tokens": (llm_params or {}).get(
                "max_tokens", LLM_PATCH_GEN_CONFIG.llm_max_tokens
            ),
            "timeout": (llm_params or {}).get(
                "timeout_seconds", LLM_PATCH_GEN_CONFIG.llm_timeout_seconds
            ),
        }

        messages: List[Dict[str, str]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": human_prompt},
        ]

        generated_content, generation_info = await llm_client.generate_text(
            messages=messages, **llm_call_params
        )

        # LLM empty content
        if not generated_content:
            result["success"] = False
            result["explanation"] = (
                "LLM failed to generate a patch. Manual review required."
            )
            result["status_reason"] = "LLM returned empty content."
            if PROMETHEUS_AVAILABLE:
                PATCH_GENERATION_ERRORS.labels(
                    vulnerability_type=vulnerability_type,
                    error_type="LLM_EmptyResponse",
                ).inc()
            return result

        result["llm_reasoning_trace"] = generation_info or {}

        # Parse output
        proposed_patch, explanation, parse_error_msg, is_diff = _parse_llm_output(
            generated_content, code_language
        )
        result["proposed_patch"] = proposed_patch
        result["explanation"] = explanation
        result["is_diff"] = bool(is_diff)
        result["patch_lines"] = len((proposed_patch or "").splitlines())

        # Truncate very large outputs before further processing/logging
        MAX_OUTPUT_CHARS = int(os.getenv("PATCH_GEN_MAX_OUTPUT_CHARS", "100000"))
        if (
            result["proposed_patch"]
            and len(result["proposed_patch"]) > MAX_OUTPUT_CHARS
        ):
            result["proposed_patch"] = (
                result["proposed_patch"][:MAX_OUTPUT_CHARS] + "\n...[truncated]"
            )

        if result["explanation"] and len(result["explanation"]) > MAX_OUTPUT_CHARS:
            result["explanation"] = (
                result["explanation"][:MAX_OUTPUT_CHARS] + "\n...[truncated]"
            )

        # Validation and success determination
        if proposed_patch is None:
            result["success"] = False
            result["status_reason"] = explanation
            result["error"] = parse_error_msg or "LLM refused/empty/unspecific."
            logger.warning(
                f"LLM refused to generate patch for {vulnerability_type}: {result['status_reason']}"
            )
        else:
            if is_diff:
                if not _looks_like_unified_diff(proposed_patch):
                    result["success"] = False
                    result["status_reason"] = (
                        "Generated patch looks like a malformed diff."
                    )
                    result["error"] = "Malformed diff format."
                    logger.warning(
                        f"Malformed diff generated for {vulnerability_type}."
                    )
                else:
                    result["success"] = True
                    result["status_reason"] = "AI proposed a unified diff patch."
            else:
                valid, reason = _validate_patch_syntax(proposed_patch, code_language)
                result["syntax_validation"] = reason
                if not valid and code_language.lower() == "python":
                    result["success"] = False
                    result["status_reason"] = "Generated patch has a syntax error."
                    result["error"] = "Syntax error in proposed code."
                    logger.warning(
                        f"Syntax error in generated code for {vulnerability_type}."
                    )
                else:
                    result["success"] = True
                    result["status_reason"] = "AI proposed a code patch."

            # Metrics on success/failure
            if result["success"]:
                if PROMETHEUS_AVAILABLE:
                    PATCH_GENERATION_SUCCESS.labels(
                        vulnerability_type=vulnerability_type
                    ).inc()
                    PATCH_COMPLEXITY.labels(
                        vulnerability_type=vulnerability_type
                    ).observe(float(result["patch_lines"]))
            else:
                if PROMETHEUS_AVAILABLE:
                    PATCH_GENERATION_ERRORS.labels(
                        vulnerability_type=vulnerability_type,
                        error_type="PlausibilityValidationError",
                    ).inc()

        if PROMETHEUS_AVAILABLE:
            LLM_PATCH_GEN_LATENCY_SECONDS.labels(
                vulnerability_type=vulnerability_type
            ).observe(time.monotonic() - start_time)

        # Cache successful result
        if result["success"]:
            await _cache_patch_result(cache_key, result)

        return result
    except Exception as e:
        result["error"] = str(e)
        result["status_reason"] = f"Patch generation failed due to exception: {e}"
        logger.error(
            f"Error generating patch for {vulnerability_type}: {e}", exc_info=True
        )
        if PROMETHEUS_AVAILABLE:
            PATCH_GENERATION_ERRORS.labels(
                vulnerability_type=vulnerability_type, error_type=type(e).__name__
            ).inc()
        return result
    finally:
        # Audit logging (best-effort, scrubbed and truncated)
        try:
            from self_fixing_engineer.arbiter.guardrails.audit_log import audit_log as global_audit_log

            scrubbed_vuln = _scrub_secrets(vulnerability_details)
            scrubbed_ctx = _scrub_secrets(context)
            if not isinstance(scrubbed_vuln, dict):
                scrubbed_vuln = {"summary": str(scrubbed_vuln)[:200]}
            if not isinstance(scrubbed_ctx, dict):
                scrubbed_ctx = {"summary": str(scrubbed_ctx)[:200]}
            patch_summary = (
                _basic_scrub((result.get("proposed_patch") or "")[:200])
                if isinstance(result.get("proposed_patch"), str)
                else "N/A"
            )
            explanation_summary = (
                _basic_scrub((result.get("explanation") or "")[:200])
                if isinstance(result.get("explanation"), str)
                else "N/A"
            )

            global_audit_log(
                event_type="security_patch_generation_attempt",
                message=result.get("status_reason", ""),
                data={
                    "patch_id": patch_id,
                    "vulnerability_type": vulnerability_type,
                    "success": result.get("success", False),
                    "status_reason": result.get("status_reason", ""),
                    "error": result.get("error", None),
                    "vulnerability_details_summary": {
                        k: v
                        for k, v in scrubbed_vuln.items()
                        if k not in ["full_code", "sensitive_data"]
                    },
                    "context_summary": {
                        k: v
                        for k, v in scrubbed_ctx.items()
                        if k not in ["full_repo_content"]
                    },
                    "proposed_patch_summary": patch_summary,
                    "explanation_summary": explanation_summary,
                    "is_diff": result.get("is_diff", False),
                    "patch_lines": result.get("patch_lines", 0),
                },
                agent_id="SecurityPatchGeneratorPlugin",
            )
        except ImportError:
            logger.warning(
                "Could not import global audit_log. Logging audit event locally."
            )
            try:
                local_event = {
                    "event_type": "security_patch_generation_attempt",
                    "message": result.get("status_reason", ""),
                    "data": {
                        "patch_id": patch_id,
                        "vulnerability_type": vulnerability_type,
                        "success": result.get("success", False),
                        "status_reason": result.get("status_reason", ""),
                        "error": result.get("error", None),
                    },
                }
                logger.info(f"AUDIT_EVENT: {json.dumps(local_event)}")
            except Exception:
                pass


def register_plugin_entrypoints(register_func: Callable):
    logger.info("Registering SecurityPatchGeneratorPlugin entrypoints...")
    register_func(
        name="ai_security_patch_generator",
        executor_func=generate_security_patch,
        capabilities=["ai_security_patch_generation", "code_remediation"],
    )


if __name__ == "__main__":
    _mock_registered_remediation_strategies: Dict[str, Any] = {}

    def _mock_register_remediation_strategy(
        name: str, executor_func: Callable, capabilities: List[str]
    ):
        _mock_registered_remediation_strategies[name] = {
            "executor_func": executor_func,
            "capabilities": capabilities,
        }
        print(
            f"Mocked registration: Registered remediation strategy '{name}' with capabilities: {capabilities}."
        )

    register_plugin_entrypoints(_mock_register_remediation_strategy)

    async def main_test_run():
        print("\n--- Security Patch Generator Plugin Standalone Test ---")
        print("\n--- Running Plugin Health Check ---")
        health_status = await plugin_health()
        print(f"Health Status: {health_status['status']}")
        for detail in health_status["details"]:
            print(f"  - {detail}")
        if health_status["status"] == "error":
            print("\n--- Skipping Patch Generation Test: Plugin not healthy. ---")
            print("Please ensure an LLM client is configured and API keys are set.")
            return

        print("\n--- Generating Patch for SQL Injection (Python) ---")
        vuln_details_sql_injection = {
            "type": "SQL Injection",
            "severity": "High",
            "component": "auth_service",
            "file": "database.py",
            "line": 45,
            "description": "Unsanitized user input directly concatenated into SQL query string in login function.",
        }
        vulnerable_code_sql_injection = "def login_user(username, password):\n    query = f\"SELECT * FROM users WHERE username = '{username}' AND password = '{password}'\"\n    cursor.execute(query)\n    return cursor.fetchone()"
        context_sql_injection = {"language": "Python", "framework": "Flask"}
        patch_result_sql = await generate_security_patch(
            vulnerability_details=vuln_details_sql_injection,
            vulnerable_code_snippet=vulnerable_code_sql_injection,
            context=context_sql_injection,
            llm_params={"temperature": 0.1},
        )
        print("\nSQL Injection Patch Result:")
        print(json.dumps(patch_result_sql, indent=2))
        print("-" * 50)

        print("\n--- Generating Patch for XSS (JavaScript) ---")
        vuln_details_xss = {
            "type": "Cross-Site Scripting (XSS)",
            "severity": "Medium",
            "description": "User-provided comment directly inserted into DOM without escaping.",
        }
        vulnerable_code_xss = "function displayComment(comment) {\n    document.getElementById('comments').innerHTML += '<div>' + comment + '</div>';\n}"
        context_xss = {"language": "JavaScript", "framework": "React (simplified)"}
        patch_result_xss = await generate_security_patch(
            vulnerability_details=vuln_details_xss,
            vulnerable_code_snippet=vulnerable_code_xss,
            context=context_xss,
            llm_params={"temperature": 0.2},
        )
        print("\nXSS Patch Result:")
        print(json.dumps(patch_result_xss, indent=2))
        print("-" * 50)

        print("\n--- Test LLM refusal/safe response ---")
        vuln_details_refusal = {
            "type": "Malicious Code Generation Request",
            "severity": "Critical",
            "description": "User explicitly asked for ransomware code.",
        }
        vulnerable_code_refusal = "import os; os.system('rm -rf /')"
        context_refusal = {"language": "Python"}
        patch_result_refusal = await generate_security_patch(
            vulnerability_details=vuln_details_refusal,
            vulnerable_code_snippet=vulnerable_code_refusal,
            context=context_refusal,
        )
        print("\nLLM Refusal Test Result:")
        print(json.dumps(patch_result_refusal, indent=2))
        print("-" * 50)

        print("\n--- Test Run Complete ---")

    asyncio.run(main_test_run())
