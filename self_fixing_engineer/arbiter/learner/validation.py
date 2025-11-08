# arbiter/learner/validation.py

import os
import json
import logging
import structlog
import asyncio
import time
from typing import Any, Dict, Optional, Callable, Union, Coroutine
import jsonschema
from jsonschema.exceptions import SchemaError, ValidationError as JsonValidationError
from tenacity import retry, stop_after_attempt, wait_exponential
from prometheus_client import Counter, Histogram
from opentelemetry import trace

from .encryption import ArbiterConfig

# Structured logging setup
structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.add_log_level_number,
        structlog.stdlib.add_logger_name,
        lambda record, _: {
            **record,
            "trace_id": f"{trace.get_current_span().get_span_context().trace_id:x}" if trace.get_current_span().is_recording() else "none"
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

# Metrics
validation_success_total = Counter(
    "arbiter_learner_validation_success_total",
    "Total successful validations",
    ["domain"]
)
validation_failure_total = Counter(
    "arbiter_learner_validation_failure_total",
    "Total failed validations",
    ["domain", "reason_code"]
)
validation_latency_seconds = Histogram(
    "arbiter_learner_validation_latency_seconds",
    "Latency of validation operations",
    ["domain"],
    buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0]
)
schema_reload_total = Counter(
    "arbiter_learner_schema_reload_total",
    "Total schema reload attempts",
    ["status"]
)
schema_reload_latency_seconds = Histogram(
    "arbiter_learner_schema_reload_latency_seconds",
    "Latency of schema reload operations",
    buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 2.0]
)

# Configuration
SCHEMA_RELOAD_RETRIES = int(os.getenv("SCHEMA_RELOAD_RETRIES", 3))
SCHEMA_CACHE_TTL_SECONDS = int(os.getenv("SCHEMA_CACHE_TTL_SECONDS", 3600))  # 1 hour
SCHEMA_DIR_PERMISSION_CHECK = bool(int(os.getenv("SCHEMA_DIR_PERMISSION_CHECK", 1)))

class DomainNotFoundError(Exception):
    """Raised when a validation schema or hook is not found for a domain."""
    pass

async def validate_data(learner: Any, domain: str, value: Any) -> Dict[str, Any]:
    """
    Validate data against schemas and hooks.
    Args:
        learner: Learner instance with validation_schemas and validation_hooks.
        domain: Domain to validate.
        value: Data to validate.
    Returns:
        Dict with is_valid, reason_code, and reason.
    Raises:
        ValueError: If domain or value is invalid.
    """
    with tracer.start_as_current_span("validate_data") as span:
        span.set_attribute("domain", domain)
        start_time = time.perf_counter()

        # Input validation
        if not isinstance(domain, str) or not domain:
            logger.error("Invalid domain", domain=domain)
            validation_failure_total.labels(domain="unknown", reason_code="invalid_domain").inc()
            raise ValueError(f"Invalid domain: {domain}")
        if value is None:
            logger.error("Value is None", domain=domain)
            validation_failure_total.labels(domain=domain, reason_code="null_value").inc()
            raise ValueError("Value cannot be None")

        try:
            schema_info = learner.validation_schemas.get(domain)
            hook = learner.validation_hooks.get(domain)

            if not schema_info and not hook:
                logger.warning("No schema or hook found", domain=domain)
                validation_failure_total.labels(domain=domain, reason_code="domain_not_found").inc()
                raise DomainNotFoundError(f"No validation schema or hook found for domain: {domain}")

            if schema_info:
                try:
                    jsonschema.validate(instance=value, schema=schema_info["schema"])
                    logger.debug("Schema validation passed", domain=domain, version=schema_info['version'])
                except JsonValidationError as e:
                    logger.error("Schema validation failed", domain=domain, error=e.message)
                    validation_failure_total.labels(domain=domain, reason_code="schema_validation_failed").inc()
                    return {"is_valid": False, "reason_code": "schema_validation_failed", "reason": f"Schema validation failed: {e.message}"}
                except SchemaError as e:
                    logger.error("Invalid schema", domain=domain, error=str(e))
                    validation_failure_total.labels(domain=domain, reason_code="invalid_schema").inc()
                    return {"is_valid": False, "reason_code": "invalid_schema", "reason": f"Invalid schema: {str(e)}"}
                except Exception as e:
                    logger.exception("Unexpected schema validation error", domain=domain)
                    validation_failure_total.labels(domain=domain, reason_code="schema_validation_error").inc()
                    return {"is_valid": False, "reason_code": "schema_validation_error", "reason": f"Schema validation error: {str(e)}"}

            if hook:
                try:
                    is_valid = await hook(value) if asyncio.iscoroutinefunction(hook) else hook(value)
                    if not is_valid:
                        logger.warning("Custom validation failed", domain=domain)
                        validation_failure_total.labels(domain=domain, reason_code="custom_validation_failed").inc()
                        return {"is_valid": False, "reason_code": "custom_validation_failed", "reason": f"Custom validation failed for '{domain}'."}
                except Exception as e:
                    logger.exception("Custom validation hook error", domain=domain)
                    validation_failure_total.labels(domain=domain, reason_code="custom_validation_error").inc()
                    return {"is_valid": False, "reason_code": "custom_validation_error", "reason": f"Error in custom validation hook: {str(e)}"}

            validation_success_total.labels(domain=domain).inc()
            return {"is_valid": True, "reason_code": "success", "reason": "All validations passed."}
        finally:
            validation_latency_seconds.labels(domain=domain).observe(time.perf_counter() - start_time)

def register_validation_hook(learner: Any, domain: str, hook_func: Callable[[Any], Union[bool, Coroutine[Any, Any, bool]]]) -> None:
    """
    Register a validation hook for a domain.
    Args:
        learner: Learner instance.
        domain: Domain to validate.
        hook_func: Callable returning True if valid.
    Raises:
        TypeError: If hook_func is not callable or has invalid signature.
    """
    with tracer.start_as_current_span("register_validation_hook") as span:
        span.set_attribute("domain", domain)

        if not callable(hook_func):
            logger.error("Invalid hook_func", type=type(hook_func))
            validation_failure_total.labels(domain="registration", reason_code="invalid_hook").inc()
            raise TypeError("Validation hook must be a callable.")

        # Validate hook signature
        if asyncio.iscoroutinefunction(hook_func):
            import inspect
            sig = inspect.signature(hook_func)
            if len(sig.parameters) != 1:
                logger.error("Invalid async hook signature", domain=domain)
                validation_failure_total.labels(domain=domain, reason_code="invalid_hook_signature").inc()
                raise TypeError("Async validation hook must accept exactly one argument (value).")
        else:
            if not hasattr(hook_func, "__code__") or hook_func.__code__.co_argcount != 1:
                logger.error("Invalid sync hook signature", domain=domain)
                validation_failure_total.labels(domain=domain, reason_code="invalid_hook_signature").inc()
                raise TypeError("Sync validation hook must accept exactly one argument (value).")

        learner.validation_hooks[domain] = hook_func
        logger.info("Registered validation hook", domain=domain)
        
        # Since this is a synchronous function, we cannot use await here
        # Log the audit attempt without await
        try:
            # If we need to audit this, we should either:
            # 1. Make this function async, or
            # 2. Schedule the audit as a background task, or
            # 3. Just log it synchronously
            logger.info("Validation hook registered (audit logging skipped in sync context)", 
                       domain=domain, 
                       hook_type="async" if asyncio.iscoroutinefunction(hook_func) else "sync")
        except Exception as e:
            logger.error("Failed to log hook registration", error=str(e))

@retry(stop=stop_after_attempt(SCHEMA_RELOAD_RETRIES), wait=wait_exponential(multiplier=1, min=1, max=10))
async def reload_schemas(learner: Any, directory: Optional[str] = None) -> None:
    """
    Reload JSON schemas from disk or remote source.
    Args:
        learner: Learner instance with validation_schemas.
        directory: Optional schema directory (defaults to ArbiterConfig.DEFAULT_SCHEMA_DIR).
    Raises:
        OSError: If directory access fails.
        json.JSONDecodeError: If schema file is invalid JSON.
    """
    with tracer.start_as_current_span("reload_schemas") as span:
        start_time = time.perf_counter()

        directory = directory or ArbiterConfig.DEFAULT_SCHEMA_DIR
        logger.info("Initiating schema reload", directory=directory)
        new_schemas: Dict[str, Dict[str, Any]] = {}

        # Check directory permissions if enabled
        if SCHEMA_DIR_PERMISSION_CHECK and not os.access(directory, os.R_OK):
            logger.error("Schema directory not readable", directory=directory)
            schema_reload_total.labels(status="failure").inc()
            raise OSError(f"Schema directory '{directory}' is not readable")

        if not os.path.exists(directory):
            logger.warning("Schema directory not found", directory=directory)
            schema_reload_total.labels(status="not_found").inc()
            return

        for filename in os.listdir(directory):
            if filename.endswith(".json"):
                domain_name = os.path.splitext(filename)[0]
                filepath = os.path.join(directory, filename)
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        schema = json.load(f)
                    jsonschema.Draft7Validator.check_schema(schema)
                    schema_version = schema.get("version", "1.0")
                    new_schemas[domain_name] = {"schema": schema, "version": schema_version, "filepath": filepath}
                    logger.info("Loaded schema", domain=domain_name, version=schema_version, file=filename)
                except json.JSONDecodeError as e:
                    logger.error("Invalid JSON schema file", file=filename, error=str(e))
                    schema_reload_total.labels(status="invalid_json").inc()
                except SchemaError as e:
                    logger.error("Invalid schema structure", file=filename, error=str(e))
                    schema_reload_total.labels(status="invalid_schema").inc()
                except Exception as e:
                    logger.error("Unexpected error loading schema", file=filename, error=str(e))
                    schema_reload_total.labels(status="load_error").inc()

        learner.validation_schemas = new_schemas
        logger.info("Schemas reloaded", total_schemas=len(new_schemas))
        schema_reload_total.labels(status="success").inc()

        # Run on_schema_reload hooks
        for hook in learner.event_hooks["on_schema_reload"]:
            try:
                if asyncio.iscoroutinefunction(hook):
                    await hook(learner.validation_schemas)
                else:
                    hook(learner.validation_schemas)
            except Exception as e:
                logger.error("Schema reload hook failed", error=str(e))

        # Cache schemas in Redis if configured
        redis_cache_key = "learner_validation_schemas_cache"
        try:
            await learner.redis.setex(redis_cache_key, SCHEMA_CACHE_TTL_SECONDS, json.dumps(new_schemas))
            logger.debug("Cached reloaded schemas in Redis", ttl=SCHEMA_CACHE_TTL_SECONDS)
        except Exception as e:
            logger.warning("Failed to cache schemas in Redis", error=str(e))

        try:
            await learner.audit_logger.add_entry(
                component="validation",
                event="schemas_reloaded",
                details={"schema_count": len(new_schemas), "directory": directory},
                user_id="system"
            )
        except Exception as e:
            logger.error("Failed to audit schema reload", error=str(e))
        finally:
            schema_reload_latency_seconds.observe(time.perf_counter() - start_time)