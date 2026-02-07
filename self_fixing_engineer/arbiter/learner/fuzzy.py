# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# arbiter/learner/fuzzy.py

import asyncio
import hashlib
import json
import os
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Protocol

import structlog
from opentelemetry import trace
from self_fixing_engineer.arbiter.otel_config import get_tracer_safe
from prometheus_client import Counter, Histogram
from tenacity import retry, stop_after_attempt, wait_exponential

from .metrics import learn_error_counter

# Use logger without reconfiguring structlog (configured in __init__.py)
logger = structlog.get_logger(__name__)

# OpenTelemetry tracer
tracer = get_tracer_safe(__name__)

# Metrics
fuzzy_parser_success_total = Counter(
    "fuzzy_parser_success_total",
    "Total successful fuzzy parser executions",
    ["parser_name"],
)
fuzzy_parser_failure_total = Counter(
    "fuzzy_parser_failure_total",
    "Total failed fuzzy parser executions",
    ["parser_name", "error_type"],
)
fuzzy_parser_latency_seconds = Histogram(
    "fuzzy_parser_latency_seconds",
    "Latency of fuzzy parser executions",
    ["parser_name"],
    buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0],
)

# Configuration
PARSER_TIMEOUT_SECONDS = float(os.getenv("FUZZY_PARSER_TIMEOUT_SECONDS", 10.0))
PARSER_MAX_CONCURRENT = int(os.getenv("FUZZY_PARSER_MAX_CONCURRENT", 10))
PARSER_PRIORITIES = {}  # Dictionary to store parser priorities, loaded dynamically


def load_parser_priorities() -> None:
    """Load parser priorities from environment or file."""
    global PARSER_PRIORITIES
    priority_file = os.getenv(
        "FUZZY_PARSER_PRIORITY_FILE",
        os.path.join(os.path.dirname(__file__), "parser_priorities.json"),
    )
    try:
        with open(priority_file, "r", encoding="utf-8") as f:
            PARSER_PRIORITIES = json.load(f)
        logger.info(
            "Loaded parser priorities",
            file=priority_file,
            parsers=list(PARSER_PRIORITIES.keys()),
        )
    except FileNotFoundError:
        logger.warning(
            "Parser priority file not found, using default priorities",
            file=priority_file,
        )
        PARSER_PRIORITIES = {}  # Default: equal priority
    except json.JSONDecodeError as e:
        logger.error(
            "Failed to decode parser priority JSON", file=priority_file, error=str(e)
        )
        raise
    except Exception as e:
        logger.error(
            "Unexpected error loading parser priorities",
            file=priority_file,
            error=str(e),
        )
        raise


# Only load priorities if not in test mode (allows tests to control priorities)
if not os.getenv("PYTEST_CURRENT_TEST"):
    load_parser_priorities()

if TYPE_CHECKING:
    from .core import Learner


class FuzzyParser(Protocol):
    async def parse(self, text: str, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Parse unstructured text into structured facts."""
        ...


@retry(
    stop=stop_after_attempt(int(os.getenv("FUZZY_LEARN_BATCH_RETRIES", 3))),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    reraise=True,
)
async def _learn_batch_with_retry(
    learner: "Learner", facts: List[Dict[str, Any]], user_id: Optional[str], source: str
) -> List[Dict[str, Any]]:
    """Wrapper for learner.learn_batch with retries."""
    return await learner.learn_batch(facts, user_id=user_id, source=source)


async def process_unstructured_data(
    learner: "Learner",
    text: str,
    domain_hint: Optional[str] = None,
    user_id: Optional[str] = None,
    source: str = "unstructured_ingestion",
    context: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Process unstructured data using registered fuzzy parsers and learn extracted facts.
    Args:
        learner: Learner instance with fuzzy_parser_hooks and audit_logger.
        text: Unstructured input text to parse.
        domain_hint: Optional hint for the domain.
        user_id: Optional user identifier.
        source: Source of the data (default: 'unstructured_ingestion').
        context: Additional context for parsers.
    Returns:
        List of results from learn_batch or error statuses.
    Raises:
        ValueError: If text or context is invalid.
    """
    with tracer.start_as_current_span("process_unstructured_data") as span:
        span.set_attribute("source", source)
        span.set_attribute("domain_hint", domain_hint or "none")
        span.set_attribute("user_id", user_id or "none")
        span.set_attribute("text_hash", hashlib.sha256(text.encode()).hexdigest())

        # Input validation
        if not isinstance(text, str) or not text.strip():
            logger.error(
                "Invalid text input",
                type=type(text),
                length=len(text) if isinstance(text, str) else None,
            )
            fuzzy_parser_failure_total.labels(
                parser_name="none", error_type="invalid_text"
            ).inc()
            raise ValueError("Text must be a non-empty string")
        if context is not None and not isinstance(context, dict):
            logger.error("Invalid context", type=type(context))
            fuzzy_parser_failure_total.labels(
                parser_name="none", error_type="invalid_context"
            ).inc()
            raise ValueError("Context must be a dictionary or None")

        # Check for registered parsers
        if not getattr(learner, "fuzzy_parser_hooks", None):
            logger.warning("No fuzzy parsers registered")
            fuzzy_parser_failure_total.labels(
                parser_name="none", error_type="no_parsers"
            ).inc()
            result = [
                {
                    "status": "failed",
                    "reason": "no_fuzzy_parsers",
                    "text_hash": hashlib.sha256(text.encode()).hexdigest(),
                }
            ]
            try:
                await learner.audit_logger.log_event(
                    component="fuzzy_parser",
                    event="no_parsers",
                    details={
                        "text_hash": result[0]["text_hash"],
                        "reason": "No parsers registered",
                    },
                    user_id=user_id or "system",
                )
            except Exception as e:
                logger.error("Failed to audit no parsers error", error=str(e))
            return result

        # Prepare context
        context = {
            "domain_hint": domain_hint,
            "user_id": user_id,
            "source": source,
            **(context or {}),
        }

        # Sort parsers by priority (higher priority first)
        parsers = sorted(
            learner.fuzzy_parser_hooks,
            key=lambda p: PARSER_PRIORITIES.get(p.__class__.__name__, 0),
            reverse=True,
        )

        # Parallelize parser execution with semaphore
        semaphore = asyncio.Semaphore(PARSER_MAX_CONCURRENT)
        extracted_facts: List[Dict[str, Any]] = []

        async def run_parser(parser: FuzzyParser) -> List[Dict[str, Any]]:
            async with semaphore:
                parser_name = parser.__class__.__name__
                span.set_attribute(f"parser.{parser_name}.executed", True)
                start_time = time.perf_counter()
                try:
                    # FIXED: Python 3.10 compatible timeout handling using asyncio.wait_for
                    facts = await asyncio.wait_for(
                        parser.parse(text, context), timeout=PARSER_TIMEOUT_SECONDS
                    )
                    if not isinstance(facts, list):
                        logger.error(
                            "Parser returned invalid facts",
                            parser=parser_name,
                            type=type(facts),
                        )
                        fuzzy_parser_failure_total.labels(
                            parser_name=parser_name, error_type="invalid_facts"
                        ).inc()
                        return []
                    fuzzy_parser_success_total.labels(parser_name=parser_name).inc()
                    fuzzy_parser_latency_seconds.labels(
                        parser_name=parser_name
                    ).observe(time.perf_counter() - start_time)
                    logger.info(
                        "Extracted facts", parser=parser_name, fact_count=len(facts)
                    )
                    return facts
                except asyncio.TimeoutError:
                    logger.error(
                        "Parser timed out",
                        parser=parser_name,
                        timeout=PARSER_TIMEOUT_SECONDS,
                    )
                    fuzzy_parser_failure_total.labels(
                        parser_name=parser_name, error_type="timeout"
                    ).inc()
                    span.set_attribute(f"parser.{parser_name}.timeout", True)
                    return []
                except Exception as e:
                    logger.error(
                        "Parser execution failed", parser=parser_name, error=str(e)
                    )
                    fuzzy_parser_failure_total.labels(
                        parser_name=parser_name, error_type="execution_error"
                    ).inc()
                    span.record_exception(e)
                    return []

        # Run parsers in parallel
        tasks = [run_parser(parser) for parser in parsers]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for i, result in enumerate(results):
            parser_name = parsers[i].__class__.__name__
            if isinstance(result, Exception):
                logger.error(
                    "Parser task failed", parser=parser_name, error=str(result)
                )
                fuzzy_parser_failure_total.labels(
                    parser_name=parser_name, error_type="task_error"
                ).inc()
                span.record_exception(result)
            else:
                extracted_facts.extend(result)

        # Handle no facts extracted
        if not extracted_facts:
            logger.info(
                "No facts extracted by any parser",
                text_hash=hashlib.sha256(text.encode()).hexdigest(),
            )
            result = [
                {
                    "status": "skipped",
                    "reason": "no_facts_extracted",
                    "text_hash": hashlib.sha256(text.encode()).hexdigest(),
                }
            ]
            try:
                await learner.audit_logger.log_event(
                    component="fuzzy_parser",
                    event="no_facts_extracted",
                    details={
                        "text_hash": result[0]["text_hash"],
                        "reason": "No facts extracted",
                    },
                    user_id=user_id or "system",
                )
            except Exception as e:
                logger.error("Failed to audit no facts extracted", error=str(e))
            return result

        # Learn extracted facts
        try:
            results = await _learn_batch_with_retry(
                learner, extracted_facts, user_id, source
            )
            try:
                await learner.audit_logger.log_event(
                    component="fuzzy_parser",
                    event="facts_learned",
                    details={
                        "text_hash": hashlib.sha256(text.encode()).hexdigest(),
                        "fact_count": len(extracted_facts),
                    },
                    user_id=user_id or "system",
                )
            except Exception as e:
                logger.error("Failed to audit facts learned", error=str(e))
            return results
        except Exception as e:
            logger.error("Failed to learn batch of fuzzy facts", error=str(e))
            learn_error_counter.labels(
                domain="unstructured", error_type="learn_batch_failure"
            ).inc()
            try:
                await learner.audit_logger.log_event(
                    component="fuzzy_parser",
                    event="learn_batch_failed",
                    details={
                        "text_hash": hashlib.sha256(text.encode()).hexdigest(),
                        "error": str(e),
                    },
                    user_id=user_id or "system",
                )
            except Exception as e:
                logger.error("Failed to audit learn batch failure", error=str(e))
            return [
                {
                    "status": "failed",
                    "reason": f"learn_batch_failure: {e}",
                    "text_hash": hashlib.sha256(text.encode()).hexdigest(),
                }
            ]


def register_fuzzy_parser_hook(
    learner: "Learner", parser: FuzzyParser, priority: int = 0
) -> None:
    """
    Register a fuzzy parser with an optional priority.
    Args:
        learner: Learner instance with fuzzy_parser_hooks.
        parser: FuzzyParser implementation.
        priority: Priority for execution order (higher runs first).
    Raises:
        TypeError: If parser does not implement FuzzyParser protocol.
    """
    with tracer.start_as_current_span("register_fuzzy_parser_hook"):
        if not hasattr(parser, "parse") or not asyncio.iscoroutinefunction(
            parser.parse
        ):
            logger.error("Invalid parser", parser_type=type(parser).__name__)
            raise TypeError(
                "Parser must implement FuzzyParser protocol with async parse method"
            )
        learner.fuzzy_parser_hooks.append(parser)
        PARSER_PRIORITIES[parser.__class__.__name__] = priority
        logger.info(
            "Registered fuzzy parser",
            parser=parser.__class__.__name__,
            priority=priority,
        )

        # Since this is a synchronous function, we cannot use await here
        # Log the registration without await
        logger.info(
            "Fuzzy parser registered (audit logging skipped in sync context)",
            parser_name=parser.__class__.__name__,
            priority=priority,
        )


# Optional async version for full functionality
async def register_fuzzy_parser_hook_async(
    learner: "Learner", parser: FuzzyParser, priority: int = 0
) -> None:
    """
    Register a fuzzy parser with an optional priority (async version).
    Args:
        learner: Learner instance with fuzzy_parser_hooks.
        parser: FuzzyParser implementation.
        priority: Priority for execution order (higher runs first).
    Raises:
        TypeError: If parser does not implement FuzzyParser protocol.
    """
    with tracer.start_as_current_span("register_fuzzy_parser_hook_async"):
        if not hasattr(parser, "parse") or not asyncio.iscoroutinefunction(
            parser.parse
        ):
            logger.error("Invalid parser", parser_type=type(parser).__name__)
            raise TypeError(
                "Parser must implement FuzzyParser protocol with async parse method"
            )
        learner.fuzzy_parser_hooks.append(parser)
        PARSER_PRIORITIES[parser.__class__.__name__] = priority
        logger.info(
            "Registered fuzzy parser",
            parser=parser.__class__.__name__,
            priority=priority,
        )

        # Now we can properly audit the registration
        try:
            await learner.audit_logger.log_event(
                component="fuzzy_parser",
                event="parser_registered",
                details={
                    "parser_name": parser.__class__.__name__,
                    "priority": priority,
                },
                user_id="system",
            )
        except Exception as e:
            logger.error("Failed to audit parser registration", error=str(e))
