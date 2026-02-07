# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

import asyncio
import json
import logging
from datetime import datetime, timezone
from importlib import import_module
from typing import Any, Callable, Coroutine, Dict, List, Optional, Tuple

import aiohttp
import tenacity
from prometheus_client import REGISTRY, Counter, Histogram

from .utils import (  # or from self_fixing_engineer.arbiter.bug_manager.utils import (
    MLRemediationError,
    RemediationError,
    redact_pii,
    validate_input_details,
)

logger = logging.getLogger(__name__)


def get_or_create_metric(metric_class, name, documentation, labelnames=None):
    for collector in list(REGISTRY._collector_to_names.keys()):
        if hasattr(collector, "_name") and collector._name == name:
            return collector
    try:
        if labelnames:
            return metric_class(name, documentation, labelnames)
        else:
            return metric_class(name, documentation)
    except ValueError:
        for collector in list(REGISTRY._collector_to_names.keys()):
            if hasattr(collector, "_name") and collector._name == name:
                return collector
        raise


# --- Prometheus Metrics Definitions ---
REMEDIATION_PLAYBOOK_EXECUTION = get_or_create_metric(
    Counter,
    "remediation_playbook_execution",
    "Total number of remediation playbook executions",
    ["playbook_name", "source"],
)
REMEDIATION_STEP_EXECUTION = get_or_create_metric(
    Counter,
    "remediation_step_execution",
    "Total number of remediation step executions",
    ["playbook_name", "step_name", "outcome"],
)
REMEDIATION_STEP_DURATION_SECONDS = get_or_create_metric(
    Histogram,
    "remediation_step_duration_seconds",
    "Step execution duration",
    ["playbook_name", "step_name"],
)
REMEDIATION_SUCCESS = get_or_create_metric(
    Counter,
    "remediation_success",
    "Total number of successful remediations",
    ["playbook_name"],
)
REMEDIATION_FAILURE = get_or_create_metric(
    Counter,
    "remediation_failure",
    "Total number of failed remediations",
    ["playbook_name"],
)
ML_REMEDIATION_PREDICTION = get_or_create_metric(
    Counter,
    "ml_remediation_prediction",
    "Total number of ML remediation prediction requests",
)
ML_REMEDIATION_PREDICTION_SUCCESS = get_or_create_metric(
    Counter,
    "ml_remediation_prediction_success",
    "Total number of successful ML remediation predictions",
)
ML_REMEDIATION_PREDICTION_FAILED = get_or_create_metric(
    Counter,
    "ml_remediation_prediction_failed",
    "Total number of failed ML remediation predictions",
    ["reason"],
)
ML_REMEDIATION_FEEDBACK = get_or_create_metric(
    Counter,
    "ml_remediation_feedback",
    "Total number of ML remediation feedback submissions",
)
ML_REMEDIATION_FEEDBACK_FAILED = get_or_create_metric(
    Counter,
    "ml_remediation_feedback_failed",
    "Total number of failed ML remediation feedback submissions",
    ["reason"],
)


class MLRemediationModel:
    """
    Manages interactions with an external Machine Learning model for bug remediation.
    Handles predictions, feedback, and ensures secure, resilient communication.
    """

    def __init__(self, model_endpoint: str, settings: Any):
        if not model_endpoint.startswith(("http://", "https://")):
            raise ValueError(f"Invalid ML endpoint: {model_endpoint}")
        self.model_endpoint = model_endpoint
        self.auth_token = getattr(settings, "ML_AUTH_TOKEN", None)
        self.settings = settings
        self._http_session: Optional[aiohttp.ClientSession] = None

        self.retry_attempts = getattr(settings, "ML_REMEDIATION_RETRY_ATTEMPTS", 3)
        self.retry_delay_seconds = getattr(
            settings, "ML_REMEDIATION_RETRY_DELAY_SECONDS", 1
        )
        self.request_timeout = getattr(settings, "ML_REMEDIATION_REQUEST_TIMEOUT", 5.0)

    async def _get_session(self) -> aiohttp.ClientSession:
        """Ensures a single aiohttp ClientSession is used for efficiency with pooling."""
        if self._http_session is None or self._http_session.closed:
            connector = aiohttp.TCPConnector(
                limit=getattr(self.settings, "ML_HTTP_CONN_LIMIT", 50)
            )
            self._http_session = aiohttp.ClientSession(connector=connector)
            logger.info(
                json.dumps({"event": "ml_http_session_created", "with_pooling": True})
            )
        return self._http_session

    async def close(self) -> None:
        """Closes the aiohttp client session."""
        if self._http_session and not self._http_session.closed:
            await self._http_session.close()
            self._http_session = None
            logger.info("MLRemediationModel HTTP session closed.")

    @tenacity.retry(
        stop=tenacity.stop_after_attempt(3),  # Fixed: use static value instead of lambda
        wait=tenacity.wait_exponential(  # Fixed: correct API path
            multiplier=1,
            min=1,
            max=10,
        ),
        retry=tenacity.retry_if_exception_type(
            (aiohttp.ClientError, asyncio.TimeoutError)
        ),
        before_sleep=tenacity.before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def predict_remediation_strategy(
        self, bug_details: Dict[str, Any]
    ) -> Tuple[Optional[str], float]:
        """
        Queries the ML model for a remediation strategy. Includes retry logic.

        Args:
            bug_details (Dict[str, Any]): Details of the bug, including message,
                                          exception type, location, and severity.

        Returns:
            Tuple[Optional[str], float]: Predicted playbook name and confidence score.
        """
        ML_REMEDIATION_PREDICTION.inc()

        model_input = redact_pii(
            {
                "message": bug_details.get("message", ""),
                "exception_type": bug_details.get("exception_type", ""),
                "location": bug_details.get("location", ""),
                "severity": bug_details.get("severity", ""),
                "custom_details": validate_input_details(
                    bug_details.get("custom_details", {})
                ),
            }
        )
        logger.info(
            json.dumps(
                {
                    "event": "ml_prediction_request",
                    "message_preview": model_input.get("message", "")[:50],
                }
            )
        )

        headers = (
            {"Authorization": f"Bearer {self.auth_token.get_secret_value()}"}
            if self.auth_token
            else {}
        )

        try:
            session = await self._get_session()
            async with session.post(
                self.model_endpoint,
                json=model_input,
                timeout=aiohttp.ClientTimeout(total=self.request_timeout),
                headers=headers,
                ssl=True,
            ) as response:
                response.raise_for_status()
                prediction_result = await response.json()

            predicted_playbook = prediction_result.get("playbook_name")
            confidence = prediction_result.get("confidence", 0.0)

            logger.info(
                json.dumps(
                    {
                        "event": "ml_prediction_success",
                        "predicted_playbook": predicted_playbook,
                        "confidence": confidence,
                    }
                )
            )
            ML_REMEDIATION_PREDICTION_SUCCESS.inc()
            return predicted_playbook, confidence
        except aiohttp.ClientError as e:
            logger.error(
                json.dumps(
                    {
                        "event": "ml_prediction_failed",
                        "reason": "api_error",
                        "error_message": str(e),
                    }
                )
            )
            ML_REMEDIATION_PREDICTION_FAILED.labels(reason="API_ERROR").inc()
            raise MLRemediationError(f"ML prediction API failed: {e}") from e
        except asyncio.TimeoutError:
            logger.error(
                json.dumps(
                    {
                        "event": "ml_prediction_failed",
                        "reason": "timeout",
                        "timeout_seconds": self.request_timeout,
                    }
                )
            )
            ML_REMEDIATION_PREDICTION_FAILED.labels(reason="TIMEOUT").inc()
            raise MLRemediationError("ML prediction timed out.")
        except Exception as e:
            logger.error(
                json.dumps(
                    {
                        "event": "ml_prediction_failed",
                        "reason": "unexpected_error",
                        "error_message": str(e),
                    }
                ),
                exc_info=True,
            )
            ML_REMEDIATION_PREDICTION_FAILED.labels(reason="UNEXPECTED_ERROR").inc()
            raise MLRemediationError(f"ML prediction failed: {e}") from e

    @tenacity.retry(
        stop=tenacity.stop_after_attempt(
            lambda retry_state: retry_state.args[0].retry_attempts
        ),
        wait=tenacity.wait_exponential(
            multiplier=lambda retry_state: retry_state.args[0].retry_delay_seconds,
            min=1,
            max=10,
        ),
        retry=tenacity.retry_if_exception_type(
            (aiohttp.ClientError, asyncio.TimeoutError)
        ),
        before_sleep=tenacity.before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def record_remediation_outcome(
        self, bug_details: Dict[str, Any], playbook_name: str, outcome: str
    ) -> None:
        """
        Sends feedback on remediation outcome to the ML model for retraining/improvement.

        Args:
            bug_details (Dict[str, Any]): Original bug details.
            playbook_name (str): Name of the playbook executed.
            outcome (str): "success" or "failure".
        """
        ML_REMEDIATION_FEEDBACK.inc()

        feedback_data = redact_pii(
            {
                "bug_signature": bug_details.get("signature"),
                "original_bug_details": bug_details,
                "playbook_executed": playbook_name,
                "remediation_outcome": outcome,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        logger.info(
            json.dumps(
                {
                    "event": "ml_feedback_submission",
                    "playbook": playbook_name,
                    "outcome": outcome,
                }
            )
        )

        try:
            session = await self._get_session()
            feedback_endpoint = f"{self.model_endpoint.rstrip('/')}/feedback"
            headers = (
                {"Authorization": f"Bearer {self.auth_token.get_secret_value()}"}
                if self.auth_token
                else {}
            )
            async with session.post(
                feedback_endpoint,
                json=feedback_data,
                timeout=aiohttp.ClientTimeout(total=2.0),
                headers=headers,
                ssl=True,
            ) as response:
                response.raise_for_status()
                logger.debug("ML remediation feedback sent successfully.")
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            reason = "TIMEOUT" if isinstance(e, asyncio.TimeoutError) else "API_ERROR"
            logger.warning(
                json.dumps(
                    {
                        "event": "ml_feedback_failed",
                        "reason": reason,
                        "error_message": str(e),
                    }
                )
            )
            ML_REMEDIATION_FEEDBACK_FAILED.labels(reason=reason).inc()
            raise
        except Exception as e:
            logger.warning(
                json.dumps(
                    {
                        "event": "ml_feedback_failed",
                        "reason": "unexpected_error",
                        "error_message": str(e),
                    }
                ),
                exc_info=True,
            )
            ML_REMEDIATION_FEEDBACK_FAILED.labels(reason="UNEXPECTED_ERROR").inc()
            raise


class RemediationStep:
    """Represents a single, potentially retryable step in a remediation playbook."""

    _action_registry: Dict[str, Callable[..., Coroutine]] = {}

    @classmethod
    def register_action(cls, name: str, action: Callable[..., Coroutine]) -> None:
        """Registers a remediation action with a given name."""
        cls._action_registry[name] = action
        logger.info(f"Registered remediation action: {name}")

    def __init__(
        self,
        name: str,
        action_name: str,
        pre_condition: Optional[
            Callable[[Dict[str, Any]], Coroutine[Any, Any, bool]]
        ] = None,
        on_success: Optional[str] = "FINISH",
        on_failure: Optional[str] = "ABORT",
        description: Optional[str] = None,
        idempotent: bool = True,
        retries: int = 0,
        retry_delay_seconds: float = 1.0,
        timeout_seconds: float = 30.0,
    ):
        self.name = name
        self.description = description
        self.pre_condition = pre_condition
        self.on_success = on_success
        self.on_failure = on_failure
        self.idempotent = idempotent
        self.retries = retries
        self.retry_delay_seconds = retry_delay_seconds
        self.timeout_seconds = timeout_seconds

        self.action = self._action_registry.get(action_name)
        if not self.action:
            try:
                # Try to load dynamically, e.g., from a module in settings
                module_path, func_name = action_name.rsplit(".", 1)
                module = import_module(module_path)
                self.action = getattr(module, func_name)
                logger.info(f"Loaded action {action_name} dynamically")
            except (ImportError, AttributeError) as e:
                logger.error(f"Failed to load action {action_name}: {e}")
                raise ValueError(f"Invalid action: {action_name}")

    async def execute(self, bug_details: Dict[str, Any], playbook_name: str) -> bool:
        """
        Executes the remediation step, including pre-condition checks and retries.
        """
        logger.info(
            json.dumps(
                {
                    "event": "step_execution_start",
                    "playbook": playbook_name,
                    "step": self.name,
                    "description": self.description,
                }
            )
        )

        if not self.idempotent and self.retries > 0:
            logger.warning(
                json.dumps(
                    {
                        "event": "non_idempotent_step_with_retries",
                        "playbook": playbook_name,
                        "step": self.name,
                    }
                )
            )

        if self.pre_condition:
            try:
                if not await self.pre_condition(bug_details):
                    logger.info(
                        json.dumps(
                            {
                                "event": "step_skipped",
                                "reason": "pre_condition_not_met",
                                "playbook": playbook_name,
                                "step": self.name,
                            }
                        )
                    )
                    REMEDIATION_STEP_EXECUTION.labels(
                        playbook_name=playbook_name,
                        step_name=self.name,
                        outcome="skipped_precondition",
                    ).inc()
                    return False
            except Exception as e:
                logger.error(
                    json.dumps(
                        {
                            "event": "pre_condition_failed",
                            "playbook": playbook_name,
                            "step": self.name,
                            "error": str(e),
                        }
                    ),
                    exc_info=True,
                )
                REMEDIATION_STEP_EXECUTION.labels(
                    playbook_name=playbook_name,
                    step_name=self.name,
                    outcome="failed_precondition",
                ).inc()
                raise RemediationError(
                    f"Error in pre-condition for step '{self.name}'",
                    self.name,
                    original_exception=e,
                )

        with REMEDIATION_STEP_DURATION_SECONDS.labels(
            playbook_name=playbook_name, step_name=self.name
        ).time():
            try:
                # Dynamically create retry decorator for the action
                action_with_retries = tenacity.retry(
                    stop=tenacity.stop_after_attempt(self.retries + 1),
                    wait=tenacity.wait_fixed(self.retry_delay_seconds),
                    before_sleep=tenacity.before_sleep_log(logger, logging.INFO),
                    reraise=True,
                )(self.action)

                # Use asyncio.wait_for for compatibility with Python < 3.11
                result = await asyncio.wait_for(
                    action_with_retries(bug_details), timeout=self.timeout_seconds
                )

                if result:
                    logger.info(
                        json.dumps(
                            {
                                "event": "step_succeeded",
                                "playbook": playbook_name,
                                "step": self.name,
                            }
                        )
                    )
                    REMEDIATION_STEP_EXECUTION.labels(
                        playbook_name=playbook_name,
                        step_name=self.name,
                        outcome="success",
                    ).inc()
                else:
                    logger.warning(
                        json.dumps(
                            {
                                "event": "step_failed",
                                "playbook": playbook_name,
                                "step": self.name,
                                "reason": "action_returned_false",
                            }
                        )
                    )
                    REMEDIATION_STEP_EXECUTION.labels(
                        playbook_name=playbook_name,
                        step_name=self.name,
                        outcome="failure",
                    ).inc()
                return result

            except asyncio.TimeoutError:
                logger.error(
                    json.dumps(
                        {
                            "event": "step_timeout",
                            "playbook": playbook_name,
                            "step": self.name,
                            "timeout_seconds": self.timeout_seconds,
                        }
                    )
                )
                REMEDIATION_STEP_EXECUTION.labels(
                    playbook_name=playbook_name, step_name=self.name, outcome="timeout"
                ).inc()
                raise RemediationError(
                    f"Step '{self.name}' timed out",
                    self.name,
                    playbook_name=playbook_name,
                )
            except Exception as e:
                logger.error(
                    json.dumps(
                        {
                            "event": "step_error",
                            "playbook": playbook_name,
                            "step": self.name,
                            "error": str(e),
                        }
                    ),
                    exc_info=True,
                )
                REMEDIATION_STEP_EXECUTION.labels(
                    playbook_name=playbook_name, step_name=self.name, outcome="error"
                ).inc()
                raise RemediationError(
                    f"Error in step '{self.name}'",
                    self.name,
                    playbook_name=playbook_name,
                    original_exception=e,
                )


class RemediationPlaybook:
    """
    Defines and executes a sequence of remediation steps for a specific bug.
    It acts as a state machine, moving from step to step based on outcomes.
    """

    def __init__(
        self, name: str, steps: List[RemediationStep], description: Optional[str] = None
    ):
        if not steps:
            raise ValueError("Remediation playbook must contain at least one step.")
        self.name = name
        self.steps = {step.name: step for step in steps}
        self.description = description
        self.initial_step_name = steps[0].name
        self._running_tasks: List[asyncio.Task] = []

    async def execute(self, location: str, bug_details: Dict[str, Any]) -> bool:
        """
        Executes the playbook state machine, iterating through steps based on success/failure.

        Args:
            location (str): The logical location of the bug (e.g., service name, module).
            bug_details (Dict[str, Any]): A dictionary containing all relevant bug information.

        Returns:
            bool: True if the bug was fixed successfully, False otherwise.
        """
        logger.info(
            json.dumps(
                {
                    "event": "playbook_execution_start",
                    "playbook": self.name,
                    "location": location,
                }
            )
        )

        current_step_name: Optional[str] = self.initial_step_name
        is_fixed = False

        try:
            while current_step_name:
                step = self.steps.get(current_step_name)
                if not step:
                    logger.error(
                        json.dumps(
                            {
                                "event": "playbook_invalid_step",
                                "playbook": self.name,
                                "step": current_step_name,
                            }
                        )
                    )
                    is_fixed = False
                    break

                try:
                    step_success = await step.execute(bug_details, self.name)

                    if step_success:
                        current_step_name = step.on_success
                        if current_step_name == "FINISH":
                            is_fixed = True
                    else:
                        current_step_name = step.on_failure
                except RemediationError as e:
                    logger.error(
                        json.dumps(
                            {
                                "event": "playbook_step_exception",
                                "playbook": self.name,
                                "step": e.step_name,
                                "error": str(e),
                            }
                        )
                    )
                    current_step_name = step.on_failure

                if current_step_name == "FINISH":
                    logger.info(
                        json.dumps(
                            {
                                "event": "playbook_finished_explicitly",
                                "playbook": self.name,
                            }
                        )
                    )
                    break
                if current_step_name == "ABORT":
                    logger.warning(
                        json.dumps({"event": "playbook_aborted", "playbook": self.name})
                    )
                    is_fixed = False
                    break
        finally:
            for task in self._running_tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*self._running_tasks, return_exceptions=True)

        if is_fixed:
            REMEDIATION_SUCCESS.labels(playbook_name=self.name).inc()
        else:
            REMEDIATION_FAILURE.labels(playbook_name=self.name).inc()

        logger.info(
            json.dumps(
                {
                    "event": "playbook_execution_end",
                    "playbook": self.name,
                    "outcome": "success" if is_fixed else "failure",
                }
            )
        )
        return is_fixed


class BugFixerRegistry:
    """A central registry for managing and selecting remediation playbooks."""

    _playbooks: Dict[str, Dict[str, RemediationPlaybook]] = {}
    _ml_remediation_model: Optional[MLRemediationModel] = None
    _settings: Any = None

    @classmethod
    def set_settings(cls, settings: Any):
        cls._settings = settings

    @classmethod
    def set_ml_model(cls, model: MLRemediationModel):
        cls._ml_remediation_model = model
        logger.info("ML Remediation Model registered with BugFixerRegistry.")

    @classmethod
    def register_playbook(
        cls,
        playbook: RemediationPlaybook,
        location: str,
        bug_signature_prefix: str = "*",
    ):
        """
        Registers a remediation playbook, mapping it to a specific location and bug signature.

        Args:
            playbook (RemediationPlaybook): The playbook instance to register.
            location (str): The logical location (e.g., service, component).
            bug_signature_prefix (str): A string prefix used to match bugs.
                                        "*" acts as a wildcard for a location.
        """
        if location not in cls._playbooks:
            cls._playbooks[location] = {}
        if bug_signature_prefix in cls._playbooks[location]:
            logger.warning(
                f"Overwriting existing playbook for location '{location}' with signature prefix '{bug_signature_prefix}'."
            )
        cls._playbooks[location][bug_signature_prefix] = playbook
        logger.info(
            f"Remediation playbook '{playbook.name}' registered for '{location}' with signature '{bug_signature_prefix}'."
        )

    @classmethod
    async def run_remediation(
        cls, location: str, bug_details: Dict[str, Any], bug_signature: str
    ) -> bool:
        """
        Selects and runs the most appropriate remediation playbook for a given bug.

        Args:
            location (str): The logical location of the bug.
            bug_details (Dict[str, Any]): Detailed information about the bug.
            bug_signature (str): A unique signature for the bug.

        Returns:
            bool: True if a remediation playbook was run successfully and fixed the bug,
                  False otherwise.
        """
        validated_details = validate_input_details(bug_details)
        chosen_playbook: Optional[RemediationPlaybook] = None
        playbook_source = "none"

        # 1. Attempt ML-based prediction if model is available
        ml_confidence_threshold = (
            getattr(cls._settings, "ML_CONFIDENCE_THRESHOLD", 0.75)
            if cls._settings
            else 0.75
        )
        if cls._ml_remediation_model:
            try:
                predicted_name, confidence = (
                    await cls._ml_remediation_model.predict_remediation_strategy(
                        validated_details
                    )
                )
                if predicted_name and confidence >= ml_confidence_threshold:
                    # Find the predicted playbook anywhere in the registry
                    for loc, sig_map in cls._playbooks.items():
                        for sig, pb in sig_map.items():
                            if pb.name == predicted_name:
                                chosen_playbook = pb
                                playbook_source = (
                                    f"ml_prediction (confidence: {confidence:.2f})"
                                )
                                break
                        if chosen_playbook:
                            break
                    if chosen_playbook:
                        logger.info(
                            f"ML model selected playbook '{chosen_playbook.name}'."
                        )
                    else:
                        logger.warning(
                            f"ML model recommended playbook '{predicted_name}' but it was not found in registry."
                        )
            except MLRemediationError as e:
                logger.error(
                    f"ML prediction failed: {e}. Falling back to rule-based remediation."
                )

        # 2. If no ML prediction, use rule-based fallback
        if not chosen_playbook:
            location_playbooks = cls._playbooks.get(location, {})
            # Find most specific signature match
            best_match_prefix = ""
            for prefix in location_playbooks:
                if bug_signature.startswith(prefix) and len(prefix) > len(
                    best_match_prefix
                ):
                    best_match_prefix = prefix

            if best_match_prefix:
                chosen_playbook = location_playbooks[best_match_prefix]
                playbook_source = "rule_specific_location"
            elif "*" in location_playbooks:
                chosen_playbook = location_playbooks["*"]
                playbook_source = "rule_general_location"

        fixed_successfully = False
        if chosen_playbook:
            logger.info(
                json.dumps(
                    {
                        "event": "playbook_selected",
                        "playbook": chosen_playbook.name,
                        "source": playbook_source,
                    }
                )
            )
            REMEDIATION_PLAYBOOK_EXECUTION.labels(
                playbook_name=chosen_playbook.name, source=playbook_source
            ).inc()
            fixed_successfully = await chosen_playbook.execute(
                location, validated_details
            )
        else:
            logger.info(
                f"No applicable remediation playbook found for bug at '{location}'."
            )

        # 3. Record feedback only if a playbook was actually attempted
        if cls._ml_remediation_model and chosen_playbook:
            try:
                await cls._ml_remediation_model.record_remediation_outcome(
                    bug_details=validated_details,
                    playbook_name=chosen_playbook.name,
                    outcome="success" if fixed_successfully else "failure",
                )
            except Exception as e:
                logger.warning(f"ML feedback failed: {e}")

        return fixed_successfully


# --- Example Remediation Actions (Placeholder functions) ---
async def restart_service(bug_details: Dict[str, Any]) -> bool:
    """
    Simulates restarting a service.
    This is a placeholder and should be replaced by a real action.
    """
    service_name = bug_details.get("service", "unknown_service")
    logger.info(f"Attempting to restart service: {service_name}")
    await asyncio.sleep(0.1)  # Simulate async I/O
    success = True
    logger.info(
        f"Service '{service_name}' restart {'succeeded' if success else 'failed'}."
    )
    return success


async def clear_cache(bug_details: Dict[str, Any]) -> bool:
    """
    Simulates clearing a cache.
    This is a placeholder and should be replaced by a real action.
    """
    cache_type = bug_details.get("cache_type", "general_cache")
    logger.info(f"Attempting to clear cache: {cache_type}")
    await asyncio.sleep(0.1)
    success = True
    logger.info(f"Cache '{cache_type}' clear {'succeeded' if success else 'failed'}.")
    return success


# Register placeholder actions for demonstration purposes
RemediationStep.register_action("restart_service", restart_service)
RemediationStep.register_action("clear_cache", clear_cache)

# --- Example Remediation Playbooks ---
restart_service_playbook = RemediationPlaybook(
    name="RestartServicePlaybook",
    description="A simple playbook to restart a service.",
    steps=[
        RemediationStep(
            name="RestartTheService",
            description="Executes the restart command for the affected service.",
            action_name="restart_service",
            retries=2,
            retry_delay_seconds=1.0,
        )
    ],
)

BugFixerRegistry.register_playbook(
    restart_service_playbook, location="*", bug_signature_prefix="500_error"
)
