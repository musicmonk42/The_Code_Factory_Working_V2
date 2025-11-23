# arbiter/learner/explanations.py

import asyncio
import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import structlog
from opentelemetry import trace
from prometheus_client import Counter, Histogram
from tenacity import RetryError, retry, stop_after_attempt, wait_exponential

# Structured logging setup
structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.add_log_level_number,
        structlog.stdlib.add_logger_name,
        lambda logger, method_name, event_dict: {
            **event_dict,
            "trace_id": (
                f"{trace.get_current_span().get_span_context().trace_id:x}"
                if trace.get_current_span().is_recording()
                else "none"
            ),
        },
        structlog.processors.JSONRenderer(),
    ],
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)
logger = structlog.get_logger(__name__)

# OpenTelemetry tracer
tracer = trace.get_tracer(__name__)

# Metrics for LLM operations
explanation_llm_latency_seconds = Histogram(
    "explanation_llm_latency_seconds",
    "Latency of LLM calls for explanations",
    ["domain"],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0],
)
explanation_llm_failure_total = Counter(
    "explanation_llm_failure_total",
    "Total LLM call failures for explanations",
    ["domain", "error_type"],
)

# Cache configuration
EXPLANATION_CACHE_REDIS_TTL = int(
    os.getenv("EXPLANATION_CACHE_REDIS_TTL", 86400)
)  # 24 hours
EXPLANATION_PROMPT_TEMPLATE_PATH = os.getenv(
    "EXPLANATION_PROMPT_TEMPLATE_PATH",
    os.path.join(os.path.dirname(__file__), "templates/explanation_prompt.json"),
)
EXPLANATION_LLM_TIMEOUT_SECONDS = float(
    os.getenv("EXPLANATION_LLM_TIMEOUT_SECONDS", 30.0)
)
EXPLANATION_PROMPT_TEMPLATES = {}


def _load_prompt_templates() -> None:
    """Load prompt templates from a JSON file or use fallback."""
    global EXPLANATION_PROMPT_TEMPLATES

    # Define fallback templates
    fallback_templates = {
        "new_fact": (
            "A new fact has been learned in the domain '{domain}' with key '{key}'. "
            "The value is: {new_value}. "
            "Please provide a concise human-readable explanation of this new fact and its significance. "
            "{kg_insights}"
        ),
        "updated_fact": (
            "A fact in the domain '{domain}' with key '{key}' has been updated. "
            "The previous value was: {old_value}. The new value is: {new_value}. "
            "The changes are: {diff}. "
            "Please explain the update, highlight the changes, and comment on their significance. "
            "{kg_insights}"
        ),
        "unchanged_fact": (
            "The fact in the domain '{domain}' with key '{key}' remained the same, "
            "but its metadata might have been refreshed. "
            "Please provide a brief explanation of what this fact represents. "
            "{kg_insights}"
        ),
    }

    try:
        with open(EXPLANATION_PROMPT_TEMPLATE_PATH, "r", encoding="utf-8") as f:
            loaded_data = json.load(f)

            # Handle the rich template structure (with metadata) or simple strings
            processed_templates = {}
            for key, value in loaded_data.items():
                if isinstance(value, str):
                    # Simple string template
                    processed_templates[key] = value
                elif isinstance(value, dict) and "template" in value:
                    # Rich structure with metadata - extract just the template
                    processed_templates[key] = value["template"]
                else:
                    logger.warning(
                        f"Template '{key}' has unexpected structure, skipping",
                        value_type=type(value).__name__,
                    )

            # Use processed templates if we got any valid ones
            if processed_templates:
                EXPLANATION_PROMPT_TEMPLATES = processed_templates
            else:
                logger.warning("No valid templates found in file, using fallback")
                EXPLANATION_PROMPT_TEMPLATES = fallback_templates

        logger.info(
            "Loaded explanation prompt templates",
            path=EXPLANATION_PROMPT_TEMPLATE_PATH,
            templates=list(EXPLANATION_PROMPT_TEMPLATES.keys()),
        )
    except FileNotFoundError:
        logger.warning(
            "Prompt template file not found, using fallback templates",
            path=EXPLANATION_PROMPT_TEMPLATE_PATH,
        )
        EXPLANATION_PROMPT_TEMPLATES = fallback_templates
    except json.JSONDecodeError as e:
        logger.error(
            "Failed to decode JSON prompt template file",
            path=EXPLANATION_PROMPT_TEMPLATE_PATH,
            error=str(e),
        )
        # Use fallback instead of raising
        EXPLANATION_PROMPT_TEMPLATES = fallback_templates
    except Exception as e:
        logger.error(
            "Unexpected error loading prompt templates",
            path=EXPLANATION_PROMPT_TEMPLATE_PATH,
            error=str(e),
        )
        # Use fallback instead of raising
        EXPLANATION_PROMPT_TEMPLATES = fallback_templates


_load_prompt_templates()


@retry(
    stop=stop_after_attempt(int(os.getenv("EXPLANATION_LLM_RETRIES", 3))),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    reraise=True,
)
async def _generate_text_with_retry(client: Any, prompt: str) -> str:
    """Generate text with retries and timeout."""
    try:
        # Python 3.10 compatible timeout handling
        result = await asyncio.wait_for(
            client.generate_text(prompt=prompt), timeout=EXPLANATION_LLM_TIMEOUT_SECONDS
        )
        return result
    except asyncio.TimeoutError:
        logger.error("LLM call timed out", timeout=EXPLANATION_LLM_TIMEOUT_SECONDS)
        explanation_llm_failure_total.labels(
            domain="explanation", error_type="timeout"
        ).inc()
        raise
    except Exception as e:
        logger.error("LLM call failed", error=str(e))
        explanation_llm_failure_total.labels(
            domain="explanation", error_type="client_error"
        ).inc()
        raise


async def generate_explanation(
    learner: Any,
    domain: str,
    key: str,
    new_value: Any,
    old_value: Any,
    diff: Optional[List[Dict[str, Any]]],
) -> str:
    """
    Generate a human-readable explanation for a learning event using an LLM.
    Args:
        learner: Learner instance with redis and llm_explanation_client.
        domain: Knowledge domain (e.g., 'FinancialData').
        key: Unique identifier for the fact.
        new_value: New data being learned.
        old_value: Previous data (None if new fact).
        diff: JSON patch diff for updates.
    Returns:
        String explanation, either from cache or newly generated.
    Raises:
        ValueError: If domain or key are invalid.
    """
    with tracer.start_as_current_span("generate_explanation") as span:
        span.set_attribute("domain", domain)
        span.set_attribute("key", key)

        # Input validation
        if not domain or not isinstance(domain, str):
            logger.error("Invalid domain", domain=domain)
            explanation_llm_failure_total.labels(
                domain="explanation", error_type="invalid_domain"
            ).inc()
            raise ValueError(f"Invalid domain: {domain}")
        if not key or not isinstance(key, str):
            logger.error("Invalid key", key=key)
            explanation_llm_failure_total.labels(
                domain="explanation", error_type="invalid_key"
            ).inc()
            raise ValueError(f"Invalid key: {key}")

        # Check Redis cache
        cache_key = f"explanation_cache:{domain}:{key}:{hash(json.dumps(new_value, sort_keys=True, default=str))}"
        try:
            cached_explanation = await learner.redis.get(cache_key)
            if cached_explanation:
                logger.debug("Retrieved cached explanation", cache_key=cache_key)
                span.set_attribute("cache_hit", True)
                return cached_explanation.decode("utf-8")
        except Exception as e:
            logger.warning("Failed to access Redis cache", error=str(e))
            span.record_exception(e)

        # Build prompt
        prompt_data = {
            "domain": domain,
            "key": key,
            "new_value": json.dumps(new_value, default=str),
            "old_value": (
                json.dumps(old_value, default=str) if old_value is not None else ""
            ),
            "diff": json.dumps(diff, default=str) if diff else "",
            "kg_insights": "",
        }

        prompt_template = ""
        if old_value is None:
            template = EXPLANATION_PROMPT_TEMPLATES.get(
                "new_fact", "New fact: {new_value}."
            )
            # Ensure we have a string template, not a dict or other type
            prompt_template = (
                template if isinstance(template, str) else "New fact: {new_value}."
            )
        elif diff:
            template = EXPLANATION_PROMPT_TEMPLATES.get(
                "updated_fact", "Updated fact. Old: {old_value}. New: {new_value}."
            )
            prompt_template = (
                template
                if isinstance(template, str)
                else "Updated fact. Old: {old_value}. New: {new_value}."
            )
        else:
            template = EXPLANATION_PROMPT_TEMPLATES.get(
                "unchanged_fact", "Fact metadata refreshed. Value: {new_value}."
            )
            prompt_template = (
                template
                if isinstance(template, str)
                else "Fact metadata refreshed. Value: {new_value}."
            )

        # Integrate knowledge graph insights
        if (
            hasattr(learner.arbiter, "knowledge_graph")
            and learner.arbiter.knowledge_graph
        ):
            try:
                related_facts = (
                    await learner.arbiter.knowledge_graph.find_related_facts(
                        domain, key, new_value
                    )
                )
                kg_consistency_issue = "No immediate consistency issues were found."
                if hasattr(learner.arbiter.knowledge_graph, "check_consistency"):
                    consistency_issue = (
                        await learner.arbiter.knowledge_graph.check_consistency(
                            domain, key, new_value
                        )
                    )
                    if consistency_issue:
                        kg_consistency_issue = f"A potential consistency issue was detected: {consistency_issue}."
                kg_insights = f"This fact is related to: {', '.join(related_facts or [])}. {kg_consistency_issue}"
                prompt_data["kg_insights"] = kg_insights
                span.set_attribute("knowledge_graph_insights", True)
            except Exception as e:
                logger.warning(
                    "Error interacting with KnowledgeGraph",
                    error=str(e),
                    domain=domain,
                    key=key,
                )
                span.record_exception(e)
                prompt_data["kg_insights"] = (
                    f"(Note: Knowledge graph insights unavailable due to error: {e})."
                )

        # Sanitize prompt data to prevent injection
        sanitized_prompt_data = {}
        for k, v in prompt_data.items():
            if isinstance(v, str):
                # Don't double-escape braces in the sanitized data
                sanitized_prompt_data[k] = v
            else:
                sanitized_prompt_data[k] = v

        try:
            full_prompt = prompt_template.format(**sanitized_prompt_data)
        except KeyError as e:
            logger.error(
                "Invalid prompt template", error=str(e), template=prompt_template
            )
            explanation_llm_failure_total.labels(
                domain="explanation", error_type="template_error"
            ).inc()
            raise ValueError(f"Invalid prompt template: {e}")

        # Generate explanation
        start_time = time.monotonic()
        try:
            explanation = await _generate_text_with_retry(
                learner.llm_explanation_client, full_prompt
            )
            span.set_attribute("llm_success", True)
            try:
                await learner.redis.setex(
                    cache_key, EXPLANATION_CACHE_REDIS_TTL, explanation.encode("utf-8")
                )
                logger.debug(
                    "Cached explanation",
                    cache_key=cache_key,
                    ttl=EXPLANATION_CACHE_REDIS_TTL,
                )
            except Exception as e:
                logger.warning("Failed to cache explanation in Redis", error=str(e))
                span.record_exception(e)
            return explanation
        except RetryError as e:
            logger.error(
                "Failed to generate explanation after retries",
                domain=domain,
                key=key,
                error=str(e),
            )
            explanation_llm_failure_total.labels(
                domain=domain, error_type="retry_exhausted"
            ).inc()
            span.set_attribute("llm_success", False)
            return "Failed to generate detailed explanation after multiple retries. Please review the raw fact data."
        except Exception as e:
            logger.error(
                "Unexpected error generating explanation",
                domain=domain,
                key=key,
                error=str(e),
            )
            explanation_llm_failure_total.labels(
                domain=domain, error_type="unexpected"
            ).inc()
            span.set_attribute("llm_success", False)
            span.record_exception(e)
            return "Failed to generate detailed explanation due to an unexpected error."
        finally:
            explanation_llm_latency_seconds.labels(domain=domain).observe(
                time.monotonic() - start_time
            )


async def record_explanation_quality(
    learner: Any, domain: str, key: str, version: Optional[int], score: int
) -> None:
    """
    Record human feedback on explanation quality and audit the action.
    Args:
        learner: Learner instance with audit_logger.
        domain: Knowledge domain.
        key: Unique identifier.
        version: Fact version (optional).
        score: Quality score (e.g., 1-5).
    Raises:
        ValueError: If score is invalid (not 1-5).
    """
    with tracer.start_as_current_span("record_explanation_quality") as span:
        span.set_attribute("domain", domain)
        span.set_attribute("key", key)
        span.set_attribute("version", version or "none")
        span.set_attribute("score", score)

        # Validate score
        if not isinstance(score, int) or score < 1 or score > 5:
            logger.error("Invalid explanation quality score", score=score)
            explanation_llm_failure_total.labels(
                domain=domain, error_type="invalid_score"
            ).inc()
            raise ValueError(f"Score must be an integer between 1 and 5, got {score}")

        feedback_entry = {
            "domain": domain,
            "key": key,
            "version": version,
            "score": score,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        learner.explanation_feedback_log.append(feedback_entry)
        logger.info(
            "Recorded explanation quality",
            domain=domain,
            key=key,
            version=version,
            score=score,
        )

        # Audit the feedback
        try:
            await learner.audit_logger.log_event(
                component="explanation",
                event="quality_feedback",
                details=feedback_entry,
                user_id="system",
            )
            logger.debug("Audited explanation quality feedback", domain=domain, key=key)
        except Exception as e:
            logger.error("Failed to audit explanation quality feedback", error=str(e))
            explanation_llm_failure_total.labels(
                domain=domain, error_type="audit_failure"
            ).inc()
            span.record_exception(e)


def get_explanation_quality_report(
    learner: Any, domain: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Generate a report on explanation quality scores.
    Args:
        learner: Learner instance with explanation_feedback_log.
        domain: Optional domain to filter results.
    Returns:
        List of feedback entries.
    """
    with tracer.start_as_current_span("get_explanation_quality_report") as span:
        span.set_attribute("domain", domain or "all")
        filtered_logs = [
            e
            for e in learner.explanation_feedback_log
            if domain is None or e["domain"] == domain
        ]
        logger.info(
            "Generated explanation quality report",
            domain=domain,
            entry_count=len(filtered_logs),
        )
        return filtered_logs
