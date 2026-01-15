# clarifier_prompt.py
"""
User interaction manager for the Clarifier system, handling prompting for ambiguities,
documentation formats, and compliance questions. Delegates core clarification logic
to clarifier.py.
Created: July 30, 2025.

Security & Limitations:
- Relies on clarifier.py for core clarification logic.
- Translation for prompts may have rate limits or accuracy issues (googletrans).
- Some channels (GUI, web) require optional dependencies (textual, fastapi).
"""

import asyncio
import os
import sys
import time
import unittest
from typing import Any, Callable, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

from omnicore_engine.plugin_registry import PlugInKind, plugin

# Import shared metrics from clarifier.py
# Import shared utilities and the core Clarifier class from clarifier.py
from .clarifier import (
    CLARIFIER_CYCLES,
    CLARIFIER_ERRORS,
    CLARIFIER_LATENCY,
    Clarifier,
    get_circuit_breaker,
    get_config,
    get_fernet,
    get_logger,
    get_tracer,
)

# Import user interaction channel from its dedicated module
from .clarifier_user_prompt import get_channel


# Create a wrapper for log_audit_event that maintains backwards compatibility
async def _wrap_log_audit_event(action: str, **kwargs) -> None:
    """
    Wrapper that converts legacy log_action calls to log_audit_event format.
    """
    try:
        from runner.runner_logging import log_audit_event
        await log_audit_event(action=action, data=kwargs)
    except ImportError:
        get_logger().debug(f"log_action: {action}, {kwargs}")
    except Exception as e:
        get_logger().warning(f"log_action failed: {e}", extra={"action": action})


# Import log_action and send_alert, with fallbacks
# NOTE: In production environments, these should come from the runner module.
# The fallback is only for development/testing scenarios.
_USING_DUMMY_LOG_ACTION = False
try:
    from runner.runner_logging import log_audit_event as _log_audit_event, send_alert
    # Use the wrapper to maintain backwards compatibility
    log_action = _wrap_log_audit_event
except ImportError:
    try:
        from audit_log import log_action, send_alert
    except ImportError:
        # In production, we should fail hard if runner logging is not available
        _is_production = os.getenv("PYTHON_ENV", "development").lower() == "production"
        _is_testing = (
            os.getenv("TESTING") == "1" 
            or "pytest" in sys.modules
            or os.getenv("PYTEST_CURRENT_TEST") is not None
        )
        
        if _is_production and not _is_testing:
            # Fail hard in production if runner logging is not available
            raise ImportError(
                "CRITICAL: Runner logging module (runner.runner_logging) is required in production. "
                "Clarification events must be logged to the secure audit trail. "
                "Please ensure the runner module is properly installed and configured."
            )
        
        _USING_DUMMY_LOG_ACTION = True
        
        async def log_action(action: str, **kwargs) -> None:
            """
            Fallback log_action for development/testing only.
            WARNING: This does NOT provide secure audit logging.
            """
            get_logger().warning(
                f"DUMMY log_action (NOT FOR PRODUCTION): {action}",
                extra={
                    "operation": "dummy_log_action", 
                    "warning": "not_audit_logged",
                    "action": action,
                },
            )

        async def send_alert(*args, **kwargs) -> None:
            """
            Fallback send_alert for development/testing only.
            WARNING: Alerts are NOT sent in this mode.
            """
            get_logger().warning(
                f"DUMMY send_alert (NOT FOR PRODUCTION): {args}",
                extra={"operation": "dummy_send_alert", "warning": "alert_not_sent"},
            )


class PromptClarifier:
    """
    Handles user-facing interactions, including special prompts for documentation
    formats and compliance, before delegating to the core Clarifier logic.
    """

    def __init__(self):
        self.config = get_config()
        self.fernet = get_fernet()
        self.logger = get_logger()
        self.tracer, self.Status, self.StatusCode = get_tracer()
        self.circuit_breaker = get_circuit_breaker()

        # Initialize the interaction channel for prompting the user
        self.interaction = get_channel(
            self.config.INTERACTION_MODE, target_language=self.config.TARGET_LANGUAGE
        )

        # Create an instance of the core Clarifier for delegation
        self.core_clarifier = Clarifier()

        # State to track if doc formats have been asked in this session
        self.doc_formats_asked = False

    def _translate_text(self, text: str, dest: str) -> str:
        """Translates text to the destination language using googletrans."""
        # Requires googletrans==4.0.0-rc1; install with `pip install googletrans==4.0.0-rc1`
        try:
            from googletrans import Translator
        except ImportError:
            self.logger.error(
                "googletrans library not found. Please install it (`pip install googletrans==4.0.0-rc1`) for translation features."
            )
            return text  # Return original text if library is missing

        translator = Translator()
        if self.config.TARGET_LANGUAGE != dest:
            try:
                translated = translator.translate(text, dest=dest).text
                self.logger.debug(
                    f"Translated '{text[:30]}...' to '{dest}': '{translated[:30]}...'"
                )
                return translated
            except Exception as e:
                self.logger.warning(
                    f"Translation failed for '{text[:50]}...' to '{dest}': {e}. Using original text."
                )
                CLARIFIER_ERRORS.labels(error_type="translation_failed").inc()
                return text
        return text

    async def _retry(
        self, func: Callable, *args, retries: int = 3, delay: float = 1.0, **kwargs
    ) -> Any:
        """Retries an async function, integrated with the shared circuit breaker."""
        for attempt in range(1, retries + 1):
            if self.circuit_breaker.is_open():
                error_msg = "Operation aborted by circuit breaker."
                self.logger.error(error_msg, extra={"operation": "retry_aborted_by_cb"})
                raise Exception(error_msg)

            try:
                result = await func(*args, **kwargs)
                self.circuit_breaker.record_success()
                return result
            except Exception as e:
                self.logger.warning(
                    f"Attempt {attempt}/{retries} failed for {func.__name__}: {e}",
                    exc_info=True if attempt == retries else False,
                )
                self.circuit_breaker.record_failure(e)
                if attempt == retries:
                    self.logger.error(
                        f"All {retries} attempts failed for {func.__name__}. Giving up."
                    )
                    raise
                await asyncio.sleep(delay * (2 ** (attempt - 1)))

    async def get_clarifications(
        self,
        ambiguities: List[str],
        requirements: Dict[str, Any],
        user_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Manages the user-facing clarification flow, including special prompts,
        and then delegates the core logic to clarifier.py.
        """
        if self.circuit_breaker.is_open():
            self.logger.error(
                "Circuit breaker is open. Aborting clarification.",
                extra={"operation": "circuit_breaker_open"},
            )
            CLARIFIER_ERRORS.labels(error_type="circuit_breaker_open").inc()
            raise Exception("Circuit breaker is open.")

        CLARIFIER_CYCLES.labels(status="started").inc()
        start_time = time.perf_counter()
        span = (
            self.tracer.start_span("prompt_clarification_cycle")
            if self.tracer
            else None
        )

        try:
            # Step 1: Ask for documentation formats if not already asked this session
            if not self.doc_formats_asked:
                doc_question = self._translate_text(
                    "What documentation formats do you prefer (e.g., Markdown, PDF, HTML)?",
                    self.config.TARGET_LANGUAGE,
                )
                try:
                    answer = (
                        await self._retry(
                            self.interaction.prompt,
                            [doc_question],
                            user_context,
                            self.config.TARGET_LANGUAGE,
                        )
                    )[0]
                    if answer and answer.strip():
                        requirements["desired_doc_formats"] = [
                            fmt.strip() for fmt in answer.split(",") if fmt.strip()
                        ]
                        asyncio.create_task(
                            log_action(
                                "clarification_doc_formats_asked",
                                {"question": doc_question, "answer": answer},
                            )
                        )
                        if span:
                            span.set_attribute("clarifier.doc_formats_specified", True)
                            span.add_event(
                                "Documentation formats recorded",
                                attributes={"formats": answer},
                            )
                    else:
                        self.logger.info(
                            "User did not specify desired documentation formats.",
                            extra={"operation": "doc_formats_not_specified"},
                        )
                        asyncio.create_task(
                            log_action(
                                "clarification_doc_formats_asked",
                                {
                                    "question": doc_question,
                                    "answer": "No answer provided",
                                },
                            )
                        )
                        if span:
                            span.set_attribute("clarifier.doc_formats_specified", False)
                    self.doc_formats_asked = True
                except Exception as e:
                    self.logger.error(
                        f"Error asking about doc formats: {e}.",
                        exc_info=True,
                        extra={"operation": "doc_formats_query_failed"},
                    )
                    CLARIFIER_ERRORS.labels(error_type="doc_formats_query_failed").inc()
                    self.circuit_breaker.record_failure(e)
                    if span:
                        span.set_status(
                            self.Status(
                                self.StatusCode.ERROR, f"Doc format query failed: {e}"
                            )
                        )
                        span.record_exception(e)

            # Step 2: Ask compliance questions using the interaction channel
            try:
                if hasattr(self.interaction, "ask_compliance_questions"):
                    await self.interaction.ask_compliance_questions(
                        user_context.get("user_id", "default"), user_context
                    )
                    if span:
                        span.add_event("Compliance questions asked")
                else:
                    self.logger.warning(
                        f"Interaction channel {type(self.interaction).__name__} does not support ask_compliance_questions."
                    )
            except Exception as e:
                self.logger.error(
                    f"Error asking compliance questions: {e}.",
                    exc_info=True,
                    extra={"operation": "compliance_query_failed"},
                )
                CLARIFIER_ERRORS.labels(error_type="compliance_query_failed").inc()
                self.circuit_breaker.record_failure(e)
                if span:
                    span.set_status(
                        self.Status(
                            self.StatusCode.ERROR, f"Compliance query failed: {e}"
                        )
                    )
                    span.record_exception(e)

            # Step 3: Delegate the core clarification logic to the main Clarifier instance
            # Note: We do not pass user_context here, as the core clarifier does not handle it directly.
            self.logger.info(
                "Delegating core clarification process to Clarifier instance."
            )
            updated_requirements = await self.core_clarifier.get_clarifications(
                ambiguities, requirements
            )

            CLARIFIER_LATENCY.labels(status="success").observe(
                time.perf_counter() - start_time
            )
            asyncio.create_task(
                log_action(
                    "prompt_clarification_cycle",
                    {
                        "status": "success",
                        "duration_sec": time.perf_counter() - start_time,
                    },
                )
            )
            if span:
                span.set_attribute("clarifier.status", "success")
                span.set_status(
                    self.Status(self.StatusCode.OK, "Prompt clarification completed")
                )
            return updated_requirements

        except Exception as e:
            CLARIFIER_ERRORS.labels(
                error_type="prompt_clarification_cycle_failed"
            ).inc()
            self.logger.error(
                f"Prompt clarification cycle failed: {e}",
                exc_info=True,
                extra={"operation": "prompt_clarification_cycle_failed"},
            )
            asyncio.create_task(
                log_action(
                    "prompt_clarification_cycle_error",
                    {"error": str(e), "status": "failed"},
                )
            )
            self.circuit_breaker.record_failure(e)
            if span:
                span.set_status(
                    self.Status(
                        self.StatusCode.ERROR, f"Prompt clarification failed: {e}"
                    )
                )
                span.record_exception(e)
            raise
        finally:
            if span:
                span.end()


# --- Plugin Entrypoint ---
@plugin(
    kind=PlugInKind.FIX,
    name="clarifier_prompt",
    version="1.0.0",
    params_schema={
        "requirements": {
            "type": "dict",
            "description": "The requirements document containing ambiguities.",
        },
        "ambiguities": {
            "type": "array",
            "items": {"type": "string"},
            "description": "A list of ambiguous statements identified in the requirements.",
        },
        "user_context": {
            "type": "dict",
            "description": "User context (e.g., user_id, user_email).",
        },
    },
    description="Handles user prompting for clarifying ambiguous requirements, including documentation formats and compliance questions.",
    safe=True,
)
async def run(
    requirements: Dict[str, Any],
    ambiguities: List[str],
    user_context: Dict[str, Any] = None,
) -> Dict[str, Any]:
    """OmniCore plugin entry point for the prompt-focused clarification pipeline."""
    # Mock the core clarifier and its dependencies for plugin execution if needed
    with patch("generator.clarifier.clarifier.Clarifier") as MockClarifier:
        # Configure the mock to have async methods
        mock_clarifier_instance = MagicMock()
        mock_clarifier_instance.get_clarifications = AsyncMock(
            return_value=requirements
        )
        mock_clarifier_instance.graceful_shutdown = AsyncMock()
        MockClarifier.return_value = mock_clarifier_instance

        clarifier = PromptClarifier()
        # Note: clarifier.core_clarifier is now already set to mock_clarifier_instance
        # via PromptClarifier.__init__ calling Clarifier()
        try:
            if user_context is None:
                user_context = {"user_id": "default"}
            clarified_requirements = await clarifier.get_clarifications(
                ambiguities, requirements, user_context
            )
            return {"requirements": clarified_requirements}
        finally:
            # Graceful shutdown should be called on the core clarifier instance
            if hasattr(clarifier, "core_clarifier"):
                await clarifier.core_clarifier.graceful_shutdown("plugin_run_complete")


async def main():
    """Main entrypoint for running the Clarifier Prompt service independently."""
    import argparse

    parser = argparse.ArgumentParser(description="Run the Clarifier Prompt service.")
    parser.add_argument("--test", action="store_true", help="Run unit tests.")
    args = parser.parse_args()

    if args.test:

        class TestPromptClarifier(unittest.TestCase):
            def setUp(self):
                # We patch the dependencies of PromptClarifier's __init__
                with (
                    patch("generator.clarifier.clarifier.get_config"),
                    patch("generator.clarifier.clarifier.get_fernet"),
                    patch("generator.clarifier.clarifier.get_logger"),
                    patch("generator.clarifier.clarifier.get_tracer"),
                    patch("generator.clarifier.clarifier.get_circuit_breaker"),
                    patch("generator.clarifier.clarifier_user_prompt.get_channel"),
                    patch("generator.clarifier.clarifier.Clarifier"),
                ):
                    self.clarifier = PromptClarifier()
                self.requirements = {"features": ["test"]}
                self.ambiguities = ["ambiguous term"]
                self.user_context = {"user_id": "test_user"}

            async def test_doc_formats_prompting(self):
                # Mock the interaction channel and the core clarifier's method
                mock_channel = AsyncMock()
                mock_channel.prompt = AsyncMock(return_value=["Markdown, PDF"])
                self.clarifier.interaction = mock_channel
                self.clarifier.core_clarifier.get_clarifications = AsyncMock(
                    return_value=self.requirements
                )

                result = await self.clarifier.get_clarifications(
                    self.ambiguities, self.requirements.copy(), self.user_context
                )

                self.assertIn("desired_doc_formats", result)
                self.assertEqual(result["desired_doc_formats"], ["Markdown", "PDF"])
                self.assertTrue(self.clarifier.doc_formats_asked)

            async def test_compliance_questions(self):
                mock_channel = AsyncMock()
                mock_channel.ask_compliance_questions = AsyncMock()
                self.clarifier.interaction = mock_channel
                self.clarifier.core_clarifier.get_clarifications = AsyncMock(
                    return_value=self.requirements
                )

                await self.clarifier.get_clarifications(
                    self.ambiguities, self.requirements, self.user_context
                )
                mock_channel.ask_compliance_questions.assert_awaited_with(
                    "test_user", self.user_context
                )

            async def test_delegation(self):
                mock_channel = AsyncMock()
                mock_channel.prompt = AsyncMock(
                    return_value=["answer"]
                )  # for doc prompt
                self.clarifier.interaction = mock_channel
                self.clarifier.core_clarifier.get_clarifications = AsyncMock(
                    return_value=self.requirements
                )

                result = await self.clarifier.get_clarifications(
                    self.ambiguities, self.requirements, self.user_context
                )
                self.clarifier.core_clarifier.get_clarifications.assert_awaited_with(
                    self.ambiguities, self.requirements
                )
                self.assertEqual(result, self.requirements)

        print("Running PromptClarifier Unit Tests...")
        test_instance = TestPromptClarifier()
        test_instance.setUp()
        await test_instance.test_doc_formats_prompting()
        await test_instance.test_compliance_questions()
        await test_instance.test_delegation()
        print("Tests completed.")
        return

    clarifier_instance = None
    try:
        clarifier_instance = PromptClarifier()
        # The main run loop is managed by the core clarifier
        await clarifier_instance.core_clarifier.run()
    except Exception as e:
        get_logger().critical(
            f"Fatal error during Clarifier Prompt startup or main loop: {e}",
            exc_info=True,
        )
        if clarifier_instance:
            # Ensure shutdown is called on the core instance
            await clarifier_instance.core_clarifier.graceful_shutdown("FATAL_ERROR")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
