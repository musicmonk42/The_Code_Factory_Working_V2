from __future__ import annotations

from abc import ABC, abstractmethod
from typing import (
    Any,
    AsyncGenerator,
    Dict,
    Optional,
    Union,
)

# ============================================================================
# Type aliases for clarity and alignment with runner.llm_client expectations.
# ============================================================================

# Non-streaming responses:
#   - MUST be a dict.
#   - SHOULD contain at minimum a "content" field with the model output as str.
#
# Streaming responses:
#   - MUST be an async generator yielding string chunks (str).
#
# The LLMClient is responsible for:
#   - Logging
#   - Metrics (Prometheus / OTEL)
#   - Tracing
#   - Caching
#   - Rate Limiting & Circuit Breaking
#   - Audit Logging (log_audit_event)
#   - Prompt Redaction (redact_secrets)
#
# Provider plugins are responsible for:
#   - Translating the unified call into provider-specific SDK/API calls.
#   - Normalizing responses into the expected shapes described below.

LLMResult = Dict[str, Any]
LLMStream = AsyncGenerator[str, None]
LLMResponse = Union[LLMResult, LLMStream]


class LLMProvider(ABC):
    """
    Abstract Base Class for all LLM Provider plugins.

    This class defines the strict-but-minimal contract between:

      - runner.llm_client.LLMClient
          Orchestrates:
            * logging
            * metrics
            * tracing
            * caching
            * rate limiting
            * circuit breaking
            * auditing
            * prompt redaction
            * provider routing
            * ensemble logic

      - runner.llm_plugin_manager.LLMPluginManager
          Handles:
            * plugin discovery
            * integrity checks
            * dynamic (re)loading
            * registration and lifecycle

      - Concrete provider plugins (OpenAI, Claude, Gemini, Grok, Local, etc.)
          Handle:
            * provider-specific configuration and API details
            * mapping logical models to provider model IDs
            * executing LLM calls
            * normalizing responses

    RESPONSIBILITY BOUNDARY
    -----------------------
    LLMClient / Runner side:
      * Handles all cross-cutting concerns:
          - Logging
          - Observability (Prometheus, OTEL)
          - Resilience (circuit breaker, retries where appropriate)
          - Caching + Idempotence
          - Redaction and security controls
          - Rate limiting and backpressure
          - Auditable provenance and policies

    Provider plugin side:
      * Knows how to talk to ONE provider:
          - Base URLs, credentials, auth headers
          - Provider SDK wiring
          - Model name resolution (if needed)
          - Response shape normalization

    EXPECTED CONTRACT
    -----------------
    Each provider module MUST:

      1. Define a subclass of `LLMProvider` implementing:
            async def call(
                self,
                prompt: str,
                model: str,
                stream: bool = False,
                **kwargs: Any,
            ) -> LLMResponse

            async def count_tokens(
                self,
                text: str,
                model: str,
            ) -> int

            async def health_check(self) -> bool

      2. Set a unique, lowercase `name` attribute on the subclass:
            name = "openai"
            name = "claude"
            name = "gemini"
            name = "grok"
            name = "local"
         etc.

      3. Expose a module-level factory:
            def get_provider() -> LLMProvider

         This is used by LLMPluginManager to instantiate and register the plugin.

    RESPONSE FORMAT
    ---------------
    Non-streaming:
      - MUST return a dict.
      - MUST contain a "content" key as a string (the primary model output).
      - MAY contain metadata fields, for example:
            {
                "content": "answer text",
                "model": "gpt-5.0-enterprise",
                "usage": {...},
                "provider_raw": {...}
            }

    Streaming:
      - MUST return an async generator of str chunks.
      - The LLMClient will consume it, accumulate for metrics/audit if needed,
        and forward chunks to the caller.

    COMPATIBILITY
    -------------
    - This design remains compatible with the earlier minimal version:
        * Existing providers that already implement the three abstract methods
          will continue to work.
    - Additional helpers below are optional conveniences, not requirements.
    """

    # ------------------------------------------------------------------ #
    # Provider identity and capabilities
    # ------------------------------------------------------------------ #

    #: Canonical provider name for registration and lookup.
    #: Example values: "openai", "claude", "gemini", "grok", "local".
    name: str = "base"

    #: Whether this provider supports streaming responses.
    supports_streaming: bool = True

    #: Whether this provider supports non-streaming responses.
    supports_non_streaming: bool = True

    #: Optional default model name for this provider.
    #: If set, may be used when caller does not specify a model explicitly.
    default_model: Optional[str] = None

    #: Optional human-readable label for logs / UIs.
    display_name: Optional[str] = None

    # ------------------------------------------------------------------ #
    # Required abstract methods
    # ------------------------------------------------------------------ #

    @abstractmethod
    async def call(
        self,
        prompt: str,
        model: str,
        stream: bool = False,
        **kwargs: Any,
    ) -> LLMResponse:
        """
        Execute an LLM call against the underlying provider.

        Args:
            prompt: The (already redacted/sanitized) text input produced by
                    LLMClient. The provider MUST NOT assume it is safe to log
                    raw prompts; logging is the responsibility of the caller.
            model: The provider-specific model identifier. The plugin may choose
                   to accept logical names and map them internally.
            stream: If True, the provider MUST return an async generator that
                    yields string chunks. If False, MUST return a dict with at
                    least a "content" key.
            **kwargs: Additional parameters (temperature, top_p, tools, system
                     prompts, extra headers, etc.) as needed by the provider.

        Returns:
            One of:
              - Non-streaming:
                    dict with at least:
                        {
                            "content": "<model_output>",
                            ...
                        }
              - Streaming:
                    async generator yielding str chunks.

        Error handling:
            - Hard failures (network, auth, malformed requests) SHOULD raise
              exceptions appropriate to the provider or a generic Exception.
            - The LLMClient layer decides how to translate/log/track them.

        NOTE:
            This method MUST NOT perform global logging/metrics/audit logic that
            conflicts with LLMClient; it MAY log internally for debug, but the
            source of truth is the orchestrator.
        """
        raise NotImplementedError

    @abstractmethod
    async def count_tokens(
        self,
        text: str,
        model: str,
    ) -> int:
        """
        Count the number of tokens for the given text and model.

        This is used for:
            - cost/budget estimation
            - rate limiting
            - observability
            - safe guards in the LLMClient

        Implementations SHOULD:
            - Use official / provider-recommended tokenizers when available.
            - Fall back to an approximate heuristic ONLY if no tokenizer exists.

        Returns:
            Integer token count (best-effort if heuristic).

        NOTE:
            If your provider has no tokenizer, you MAY implement:

                async def count_tokens(self, text, model):
                    return await self.approx_token_count(text)

            which uses the shared heuristic defined at the bottom of this class.
        """
        raise NotImplementedError

    @abstractmethod
    async def health_check(self) -> bool:
        """
        Perform a lightweight health check for this provider.

        Requirements:
            - SHOULD be fast and cheap (non-billable or minimal).
            - SHOULD return:
                  True  -> provider appears healthy and usable.
                  False -> provider not healthy / not ready, without raising.
            - MAY internally handle expected conditions (e.g. 401, 429) and
              return False instead of raising, so that LLMClient can decide
              whether to open a circuit or route elsewhere.

        This is used by:
            - LLMClient.health_check()
            - Monitoring, readiness/liveness probes
            - Circuit breaker priming
        """
        raise NotImplementedError

    # ------------------------------------------------------------------ #
    # Optional helpers (concrete). Providers MAY use or ignore these.
    # ------------------------------------------------------------------ #

    def resolve_model(self, model: Optional[str]) -> str:
        """
        Resolve the effective model name, applying provider defaults.

        Behavior:
            - If `model` is provided and non-empty, use it as-is.
            - Else, if `self.default_model` is set, use that.
            - Else, raise ValueError to force callers to be explicit.

        This keeps model resolution consistent across providers that choose
        to rely on it.

        Raises:
            ValueError: If no usable model name is found.
        """
        chosen = model or self.default_model
        if not chosen:
            raise ValueError(
                f"{self.__class__.__name__}: No model specified and no "
                f"default_model is configured."
            )
        return chosen

    def normalize_non_streaming_response(self, data: Dict[str, Any]) -> LLMResult:
        """
        Normalize and validate a non-streaming response dict.

        Enforces:
            - A "content" key MUST exist and MUST be a string.
            - If "content" is missing, we synthesize it from the dict.
            - If "content" is not a string, we coerce it via str().

        This is a convenience helper for providers; it is safe but not required.

        Example:

            raw = some_sdk_call()
            result = {
                "content": raw["choices"][0]["message"]["content"],
                "model": raw.get("model"),
                "usage": raw.get("usage"),
                "provider_raw": raw,
            }
            return self.normalize_non_streaming_response(result)
        """
        if "content" not in data:
            # As a defensive fallback, provide SOME textual content.
            data["content"] = str(data)
        else:
            if not isinstance(data["content"], str):
                data["content"] = str(data["content"])
        return data

    async def approx_token_count(self, text: str) -> int:
        """
        Fallback heuristic for token counting.

        This helper is intentionally simple and dependency-free. Providers that
        do not have a native tokenizer MAY use it as:

            async def count_tokens(self, text: str, model: str) -> int:
                return await self.approx_token_count(text)

        Heuristic:
            - Use whitespace tokenization and multiply by 1.3 to approximate
              subword/BPE behavior.

        This mirrors the fallback behavior used in runner.llm_client when a
        real tokenizer library (e.g. tiktoken) is unavailable.
        """
        # NOTE:
        # We keep this trivial on purpose; real accuracy belongs to the
        # provider-specific implementations where official tooling exists.
        return int(len(text.split()) * 1.3)
