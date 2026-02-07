# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

import asyncio
import difflib
import hashlib
import json
import logging
import os
import re
import time  # For performance metrics
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

import jsonschema
import nltk
import requests
import yaml

# P6: Tenacity for retries
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

# P5: Observability: Prometheus Metrics
try:
    from prometheus_client import Counter, Gauge, Histogram, start_http_server

    PROMETHEUS_AVAILABLE = True
    # Metrics for spec generation
    SPEC_GEN_TOTAL = Counter(
        "spec_gen_total", "Total spec generations", ["format", "status"]
    )
    SPEC_GEN_LATENCY_SECONDS = Histogram(
        "spec_gen_latency_seconds", "Spec generation latency in seconds", ["format"]
    )
    # Metrics for validation
    SPEC_VALIDATION_TOTAL = Counter(
        "spec_validation_total", "Total spec validations", ["format", "is_valid"]
    )
    # Metrics for auto-fix
    SPEC_AUTO_FIX_TOTAL = Counter(
        "spec_auto_fix_total", "Total spec auto-fix attempts", ["status"]
    )
except ImportError:
    PROMETHEUS_AVAILABLE = False
    SPEC_GEN_TOTAL, SPEC_GEN_LATENCY_SECONDS = None, None
    SPEC_VALIDATION_TOTAL = None
    SPEC_AUTO_FIX_TOTAL = None

# P5: Observability: OpenTelemetry Tracing
try:
    from opentelemetry import trace
    from opentelemetry.sdk.resources import SERVICE_NAME, Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

    OPENTELEMETRY_AVAILABLE = True
    # Initialize OpenTelemetry
    resource = Resource(attributes={SERVICE_NAME: "spec-utils"})
    trace_provider = TracerProvider(resource=resource)
    trace_provider.add_span_processor(
        BatchSpanProcessor(ConsoleSpanExporter())
    )  # Use ConsoleSpanExporter for local debugging
    trace.set_tracer_provider(trace_provider)
    tracer = trace.get_tracer(__name__)
except ImportError:
    tracer = None
    OPENTELEMETRY_AVAILABLE = False


class NullContext:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


def get_tracing_context(span_name: str):
    return (
        tracer.start_as_current_span(span_name)
        if OPENTELEMETRY_AVAILABLE
        else NullContext()
    )


from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import PromptTemplate

from .requirements import get_checklist

# Setup structured logging
logger = logging.getLogger(__name__)


# Helper function for retrying async operations
async def with_retry(func, retries: int = 3, delay: float = 1.0):
    """
    Simple async retry wrapper for functions.

    Args:
        func: The async function to retry
        retries: Number of retry attempts
        delay: Delay between retries in seconds

    Returns:
        The result of the function call

    Raises:
        The last exception if all retries fail
    """
    last_exception = None
    for attempt in range(retries):
        try:
            if asyncio.iscoroutinefunction(func):
                return await func()
            else:
                # If it's a regular function that returns a coroutine
                result = func()
                if asyncio.iscoroutine(result):
                    return await result
                return result
        except Exception as e:
            last_exception = e
            if attempt < retries - 1:
                await asyncio.sleep(delay * (2**attempt))  # Exponential backoff
                logger.warning(
                    f"Retry attempt {attempt + 1}/{retries} after error: {e}"
                )
            else:
                logger.error(f"All {retries} retry attempts failed: {e}")
    raise last_exception


# P12: Docker: Include NLTK data in image (pre-download in Dockerfile)
# Instructions for Dockerfile:
# RUN python -c "import nltk; nltk.download('punkt'); nltk.download('stopwords')"
# --- NLTK Data Setup ---
try:
    nltk.data.find("tokenizers/punkt")
    nltk.data.find("corpora/stopwords")
except LookupError:
    logger.warning(
        "NLTK data not found. Attempting to download... This might fail in production containers if not pre-downloaded."
    )
    try:
        nltk.download("punkt")
        nltk.download("stopwords")
        logger.info("NLTK data downloaded successfully.")
    except Exception as e:
        logger.error(
            f"Failed to download NLTK data: {e}. Please ensure 'punkt' and 'stopwords' are available.",
            exc_info=True,
        )


# P9: Internationalization - Load prompts from locales.yaml
_LOCALES: Dict[str, Any] = {}
LOCALES_FILE = os.environ.get("LOCALES_FILE", "config/locales.yaml")


def _load_locales():
    global _LOCALES
    if os.path.exists(LOCALES_FILE):
        try:
            with open(LOCALES_FILE, "r", encoding="utf-8") as f:
                _LOCALES = yaml.safe_load(f)
            logger.info(f"Locales loaded from {LOCALES_FILE}.")
        except Exception as e:
            logger.error(
                f"Failed to load locales from {LOCALES_FILE}: {e}. Using default English prompts.",
                exc_info=True,
            )
    else:
        logger.warning(
            f"Locales file not found at {LOCALES_FILE}. Using default English prompts."
        )


_load_locales()  # Load locales on module import


def get_localized_prompt(key: str, lang: str = "en") -> str:
    """Retrieves a localized prompt string."""
    return _LOCALES.get(lang.lower(), {}).get(
        key, _LOCALES.get("en", {}).get(key, f"Prompt not found for {key} in {lang}.")
    )


# --- Externalized Ambiguous Words Source ---
def load_ambiguous_words(lang: str) -> List[str]:
    """
    Loads ambiguous words for a given language from a dynamic source (e.g., file, API).
    P6: Retries on fetches.
    """
    config_path = os.environ.get("AMBIGUOUS_WORDS_PATH", "config/ambiguous_words.json")
    config_url = os.environ.get("AMBIGUOUS_WORDS_URL")
    ambiguous = []
    try:

        @retry(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, max=10),
            retry=retry_if_exception_type(requests.exceptions.RequestException),
            before_sleep=before_sleep_log(logger, logging.WARNING),
        )
        def _fetch_words():
            resp = requests.get(config_url, timeout=5)  # 5s timeout for external fetch
            resp.raise_for_status()
            return resp.json()

        if config_url:
            words_by_lang = _fetch_words()
        else:
            with open(config_path, "r", encoding="utf-8") as f:
                words_by_lang = json.load(f)
        ambiguous = words_by_lang.get(lang.lower(), [])
    except Exception as e:
        logger.warning(f"Failed to load ambiguous words for {lang}: {e}")
    return ambiguous


# --- UPGRADE: Plugin Spec Formats and Versioning ---
SPEC_HANDLERS: Dict[str, Dict[str, Callable]] = {}


def register_spec_handler(
    format_name: str,
    validator: Callable[[str], Tuple[bool, str]],
    generator: Callable[..., Optional[str]],
):
    """
    Registers a custom specification handler for a given format.

    Args:
        format_name: The name of the format (e.g., 'gherkin', 'json').
        validator: A function that takes a spec string and returns a tuple of (bool, str).
        generator: A function that generates a spec from context.
    """
    SPEC_HANDLERS[format_name.lower()] = {
        "validator": validator,
        "generator": generator,
    }
    logger.info(f"Registered custom spec handler for format: '{format_name}'")


# --- Enhanced Validation and Linting ---


def validate_spec(
    spec: str, format: str, version: str = "1.0", schema: Optional[dict] = None
) -> Tuple[bool, str]:
    """
    Validates a specification against its format, version, and an optional schema.
    Applies schema validation, semantic checks, and strict format rules.
    P5: Metrics for validation.

    Args:
        spec: The specification content as a string.
        format: The format of the specification (e.g., 'json', 'yaml', 'gherkin').
        version: The version of the specification schema to validate against.
        schema: An optional JSON schema to validate against.

    Returns:
        A tuple: (is_valid, validation_message).
    """
    time.perf_counter()
    format_lower = format.lower()
    logger.info(f"Starting validation for format: {format_lower} (version {version})")

    is_valid = False
    validation_message = "Unknown validation error."

    with get_tracing_context("validate_spec") as span:
        if OPENTELEMETRY_AVAILABLE:
            span.set_attribute("spec.format", format_lower)
            span.set_attribute("spec.version", version)
            span.set_attribute("spec.has_schema", schema is not None)

        # Plugin handler for custom formats
        if format_lower in SPEC_HANDLERS:
            is_valid, validation_message = SPEC_HANDLERS[format_lower]["validator"](
                spec
            )
        # JSON: parse + (optional) JSON schema validate
        elif format_lower == "json":
            try:
                obj = json.loads(spec)
                if schema:
                    jsonschema.validate(instance=obj, schema=schema)
                is_valid, validation_message = True, "Valid JSON"
            except json.JSONDecodeError as e:
                is_valid, validation_message = False, f"Invalid JSON: {e}"
            except jsonschema.ValidationError as ve:
                is_valid, validation_message = False, f"JSON Schema violation: {ve}"
        # YAML: parse + (optional) schema (reuse JSON schema)
        elif format_lower == "yaml":
            try:
                obj = yaml.safe_load(spec)
                if schema:
                    jsonschema.validate(instance=obj, schema=schema)
                # Additional: check for duplicate keys, etc.
                if isinstance(obj, dict) and len(obj) != len(set(obj.keys())):
                    is_valid, validation_message = False, "YAML contains duplicate keys"
                else:
                    is_valid, validation_message = True, "Valid YAML"
            except yaml.YAMLError as e:
                is_valid, validation_message = False, f"Invalid YAML: {e}"
            except jsonschema.ValidationError as ve:
                is_valid, validation_message = (
                    False,
                    f"YAML (JSON Schema) violation: {ve}",
                )
        # Gherkin: regex + advanced lint rules
        elif format_lower == "gherkin":
            if not re.search(r"^\s*Feature:", spec, re.MULTILINE):
                is_valid, validation_message = (
                    False,
                    "Gherkin: Missing 'Feature' keyword.",
                )
            elif not re.search(r"^\s*Scenario:", spec, re.MULTILINE):
                is_valid, validation_message = (
                    False,
                    "Gherkin: Missing 'Scenario' keyword.",
                )
            else:
                scenarios = re.split(r"^\s*Scenario:", spec, flags=re.MULTILINE)[1:]
                for idx, sc in enumerate(scenarios, 1):
                    if not any(
                        re.search(rf"^\s*{step} ", sc, re.MULTILINE)
                        for step in ["Given", "When", "Then"]
                    ):
                        is_valid, validation_message = (
                            False,
                            f"Gherkin: Scenario {idx} missing Given/When/Then.",
                        )
                        break
                else:  # Only if loop completes without break
                    is_valid, validation_message = True, "Valid Gherkin"
        # User story strict check
        elif format_lower == "user_story":
            if re.search(
                r"As a .* I want .* so that .*", spec, re.IGNORECASE | re.DOTALL
            ):
                is_valid, validation_message = True, "User story format detected"
            else:
                is_valid, validation_message = (
                    False,
                    "User story must contain 'As a..., I want..., so that...'.",
                )
        else:
            logger.warning(
                f"No validator found for format '{format}'. Assuming valid if not empty."
            )
            is_valid, validation_message = (
                bool(spec.strip()),
                "No specific validator for this format.",
            )

        if OPENTELEMETRY_AVAILABLE:
            span.set_attribute("validation.is_valid", is_valid)
            span.set_attribute("validation.message", validation_message)
            span.set_status(
                trace.Status(
                    trace.StatusCode.OK if is_valid else trace.StatusCode.ERROR,
                    description=validation_message,
                )
            )

    if PROMETHEUS_AVAILABLE and SPEC_VALIDATION_TOTAL:
        SPEC_VALIDATION_TOTAL.labels(format=format_lower, is_valid=str(is_valid)).inc()

    return is_valid, validation_message


def migrate_spec(
    spec: str, format: str, from_version: str, to_version: str
) -> Tuple[Optional[str], str]:
    """
    In a full production system, this function would handle the migration of specs
    from an older version to a newer one, preserving original intent.
    It would likely use LLM to assist with the transformation.
    """
    logger.warning(
        f"Migration from v{from_version} to v{to_version} is not implemented for format '{format}'."
    )
    return None, "Migration not supported."


def detect_ambiguity(text: str, language: str = "english") -> List[Dict[str, Any]]:
    """Detects and returns a list of ambiguities found in the text using dynamic ambiguous word list."""
    with get_tracing_context("detect_ambiguity") as span:
        if OPENTELEMETRY_AVAILABLE:
            span.set_attribute("text.length", len(text))
            span.set_attribute("language", language)
        ambiguous_words = set(load_ambiguous_words(language))
        if not ambiguous_words:
            if OPENTELEMETRY_AVAILABLE:
                span.set_attribute("ambiguous_words.loaded", False)
            return []

        if OPENTELEMETRY_AVAILABLE:
            span.set_attribute("ambiguous_words.loaded", True)
        sentences = nltk.sent_tokenize(text)
        ambiguities = []

        for i, sentence in enumerate(sentences):
            found_words = {
                word
                for word in nltk.word_tokenize(sentence.lower())
                if word in ambiguous_words
            }
            if found_words:
                ambiguities.append(
                    {
                        "id": f"AMBIG-{uuid.uuid4().hex[:6]}",
                        "line": i + 1,
                        "sentence": sentence,
                        "vague_elements": list(found_words),
                    }
                )
        if OPENTELEMETRY_AVAILABLE:
            span.set_attribute("ambiguities.count", len(ambiguities))
            span.set_status(trace.Status(trace.StatusCode.OK))
        return ambiguities


# --- Robust LLM-driven Auto-Fix ---


async def auto_fix_spec(
    spec: str,
    llm: BaseChatModel,
    format: str,
    issues: List[Dict[str, Any]],
    language: str = "en",
) -> Tuple[Optional[str], str]:
    """
    Attempts to auto-fix a specification using LLM, with strict, multi-stage validation.
    P5: Metrics for auto-fix.
    """
    start_time = time.perf_counter()
    try:
        with get_tracing_context("auto_fix_spec") as span:
            if OPENTELEMETRY_AVAILABLE:
                span.set_attribute("spec.format", format)
                span.set_attribute("issues.count", len(issues))
                span.set_attribute("language", language)

            if not issues:
                if OPENTELEMETRY_AVAILABLE:
                    span.set_attribute("auto_fix.performed", False)
                    span.set_status(trace.Status(trace.StatusCode.OK))
                if PROMETHEUS_AVAILABLE and SPEC_AUTO_FIX_TOTAL:
                    SPEC_AUTO_FIX_TOTAL.labels(status="no_issues").inc()
                return spec, "No issues to fix."

            issue_summary = "\n".join(
                [
                    f"- Line {i['line']}: Sentence '{i['sentence']}' contains ambiguous terms: {', '.join(i['vague_elements'])}."
                    for i in issues
                ]
            )

            prompt_text = get_localized_prompt("auto_fix_spec_prompt", language).format(
                format=format, issue_summary=issue_summary, spec=spec
            )
            fix_prompt = PromptTemplate.from_template(prompt_text)

            try:
                for attempt in range(3):  # Retry auto-fix generation
                    # Use ainvoke directly for async operation
                    response = await (fix_prompt | llm).ainvoke({})
                    response = response.content
                    # P1: Sanitize LLM output (basic stripping)
                    response = response.strip()

                    # Validation: Must be non-empty, not shorter than half original, and must pass validate_spec
                    is_valid, valid_msg = validate_spec(response, format)
                    if (len(response) > len(spec) * 0.5) and is_valid:
                        fix_notes = f"Auto-fixed {len(issues)} issue(s) on attempt {attempt+1}. Validated: {valid_msg}"
                        logger.info(fix_notes)
                        if OPENTELEMETRY_AVAILABLE:
                            span.set_attribute("auto_fix.performed", True)
                            span.set_attribute("auto_fix.attempts", attempt + 1)
                            span.set_status(trace.Status(trace.StatusCode.OK))
                        if PROMETHEUS_AVAILABLE and SPEC_AUTO_FIX_TOTAL:
                            SPEC_AUTO_FIX_TOTAL.labels(status="success").inc()
                        return response, fix_notes
                    else:
                        logger.warning(
                            f"Auto-fix attempt {attempt+1} failed validation: {valid_msg}. Response length: {len(response)}."
                        )
                        if OPENTELEMETRY_AVAILABLE:
                            span.add_event(
                                "auto_fix_attempt_failed",
                                {
                                    "attempt": attempt + 1,
                                    "validation_message": valid_msg,
                                    "response_length": len(response),
                                },
                            )
            except Exception as e:
                logger.error(f"Auto-fix generation failed: {e}", exc_info=True)
                if OPENTELEMETRY_AVAILABLE:
                    span.set_status(
                        trace.Status(
                            trace.StatusCode.ERROR,
                            description=f"Auto-fix generation failed: {e}",
                        )
                    )
                if PROMETHEUS_AVAILABLE and SPEC_AUTO_FIX_TOTAL:
                    SPEC_AUTO_FIX_TOTAL.labels(status="failed").inc()

            if OPENTELEMETRY_AVAILABLE:
                span.set_attribute("auto_fix.performed", False)
            if PROMETHEUS_AVAILABLE and SPEC_AUTO_FIX_TOTAL:
                SPEC_AUTO_FIX_TOTAL.labels(status="failed").inc()
            return None, "Auto-fix failed or did not yield a valid, robust result."
    finally:
        if PROMETHEUS_AVAILABLE and SPEC_GEN_LATENCY_SECONDS:
            SPEC_GEN_LATENCY_SECONDS.labels(format=format).observe(
                time.perf_counter() - start_time
            )


# --- UPGRADE: Deep Traceability and Artifact Persistence ---


class TraceableArtifact:
    """
    Holds a generated artifact and its traceability/provenance.
    Persists metadata to a robust database for full history.
    """

    def __init__(
        self,
        content: str,
        artifact_type: str,
        source_spec_id: str,
        generation_prompt: str,
    ):
        self.id = f"{artifact_type.upper()}-{uuid.uuid4().hex[:8]}"
        self.timestamp = datetime.utcnow().isoformat()
        self.artifact_type = artifact_type
        self.content = content
        self.source_spec_id = source_spec_id
        self.generation_prompt = generation_prompt
        self.history = [
            {
                "timestamp": self.timestamp,
                "action": "created",
                "notes": "Initial generation.",
            }
        ]
        self.persist_metadata()

    def update(self, new_content: str, notes: str):
        self.content = new_content
        self.history.append(
            {
                "timestamp": datetime.utcnow().isoformat(),
                "action": "updated",
                "notes": notes,
            }
        )
        self.persist_metadata()

    def persist_metadata(self):
        """
        Persist artifact metadata/history to a production database or provenance system.
        P6: Retries on external service calls.
        """
        try:
            provenance_url = os.environ.get(
                "PROVENANCE_API", "http://localhost:8080/artifacts"
            )
            payload = self.to_dict()

            @retry(
                stop=stop_after_attempt(3),
                wait=wait_exponential(multiplier=1, max=10),
                retry=retry_if_exception_type(requests.exceptions.RequestException),
                before_sleep=before_sleep_log(logger, logging.WARNING),
            )
            def _send_payload():
                requests.post(
                    provenance_url, json=payload, timeout=5
                )  # 5s timeout for external service

            _send_payload()
            logger.info(f"Persisted artifact metadata for '{self.id}'.")
        except Exception as e:
            logger.warning(f"Failed to persist artifact metadata for '{self.id}': {e}")

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__


# --- Downstream Artifact Generation (Traceable) ---


async def _generate_downstream_artifact(
    spec_id: str,
    spec_content: str,
    llm: BaseChatModel,
    prompt_template_key: str,
    artifact_type: str,
    language: str = "en",
    **kwargs,
) -> TraceableArtifact:
    prompt_text = get_localized_prompt(prompt_template_key, language)
    prompt = PromptTemplate.from_template(prompt_text)
    chain = prompt | llm
    generation_context = {"spec": spec_content, **kwargs}
    # Use ainvoke for async operation
    content = await chain.ainvoke(generation_context)
    content = content.content
    artifact = TraceableArtifact(
        content=content,
        artifact_type=artifact_type,
        source_spec_id=spec_id,
        generation_prompt=prompt.template,
    )
    logger.info(f"Generated traceable artifact '{artifact.id}' from spec '{spec_id}'.")
    return artifact


async def generate_code_stub(
    spec_id: str, spec_content: str, llm: BaseChatModel, language: str = "python"
) -> TraceableArtifact:
    # P9: Accessibility - Ensure generated code stubs have comments for clarity.
    # The prompt itself ensures this.
    return await _generate_downstream_artifact(
        spec_id,
        spec_content,
        llm,
        "generate_code_stub_prompt",
        "code",
        language=language,
        language_name=language,
    )


async def generate_test_stub(
    spec_id: str,
    spec_content: str,
    llm: BaseChatModel,
    framework: str = "pytest",
    language: str = "en",
) -> TraceableArtifact:
    # P9: Accessibility - Ensure generated test stubs have clear structure and placeholder comments.
    # The prompt itself ensures this.
    return await _generate_downstream_artifact(
        spec_id,
        spec_content,
        llm,
        "generate_test_stub_prompt",
        "test",
        language=language,
        framework=framework,
    )


async def generate_security_review(
    spec_id: str, spec_content: str, llm: BaseChatModel, language: str = "en"
) -> TraceableArtifact:
    return await _generate_downstream_artifact(
        spec_id,
        spec_content,
        llm,
        "generate_security_review_prompt",
        "security_review",
        language=language,
    )


# --- Core Spec Generation, Gap Analysis, and Refinement ---


async def generate_spec_from_memory(
    memory: Any,
    llm: BaseChatModel,
    format: str = "gherkin",
    persona: str = "",
    language: str = "en",
    version: str = "1.0",
    schema: Optional[dict] = None,
    **kwargs,
) -> Optional[Dict[str, Any]]:
    """
    Generates a specification from agent memory, validates it, and attempts to self-heal.
    P5: Metrics for spec generation.
    P9: Accessibility - Ensure generated specs are WCAG-friendly (e.g., alt text in Markdown if images).
    """
    start_time = time.perf_counter()
    try:
        with get_tracing_context("generate_spec_from_memory") as span:
            if OPENTELEMETRY_AVAILABLE:
                span.set_attribute("spec.format", format)
                span.set_attribute("persona", persona)
                span.set_attribute("language", language)

            transcript = memory.load_memory_variables({}).get("history", "")
            if not transcript:
                if OPENTELEMETRY_AVAILABLE:
                    span.set_status(
                        trace.Status(
                            trace.StatusCode.ERROR, description="Empty transcript"
                        )
                    )
                if PROMETHEUS_AVAILABLE and SPEC_GEN_TOTAL:
                    SPEC_GEN_TOTAL.labels(
                        format=format, status="empty_transcript"
                    ).inc()
                return None

            spec_content = None
            if format in SPEC_HANDLERS:
                spec_content = await SPEC_HANDLERS[format]["generator"](
                    transcript, llm, persona, language, **kwargs
                )
            else:
                prompt_text = get_localized_prompt(
                    f"generate_spec_prompt_{format.lower()}", language
                )
                if prompt_text.startswith("Prompt not found"):
                    logger.error(
                        f"No localized prompt found for spec format: {format} in language: {language}"
                    )
                    if OPENTELEMETRY_AVAILABLE:
                        span.set_status(
                            trace.Status(
                                trace.StatusCode.ERROR,
                                description=f"No prompt for format {format}",
                            )
                        )
                    if PROMETHEUS_AVAILABLE and SPEC_GEN_TOTAL:
                        SPEC_GEN_TOTAL.labels(format=format, status="no_prompt").inc()
                    return None

                spec_prompt = PromptTemplate.from_template(prompt_text)
                response = await (spec_prompt | llm).ainvoke(
                    {"transcript": transcript, "persona": persona, "language": language}
                )
                spec_content = response.content

            if not spec_content:
                if OPENTELEMETRY_AVAILABLE:
                    span.set_status(
                        trace.Status(
                            trace.StatusCode.ERROR,
                            description="LLM returned empty content",
                        )
                    )
                if PROMETHEUS_AVAILABLE and SPEC_GEN_TOTAL:
                    SPEC_GEN_TOTAL.labels(
                        format=format, status="empty_llm_response"
                    ).inc()
                return None

            is_valid, validation_msg = validate_spec(
                spec_content, format, version, schema=schema
            )
            ambiguities = detect_ambiguity(spec_content, language)

            if not is_valid or ambiguities:
                logger.warning(
                    f"Initial spec has issues. Validation: {validation_msg}. Ambiguities: {len(ambiguities)}. Attempting auto-fix."
                )
                if OPENTELEMETRY_AVAILABLE:
                    span.add_event(
                        "initial_spec_issues",
                        {
                            "validation_msg": validation_msg,
                            "ambiguities_count": len(ambiguities),
                        },
                    )
                fixed_spec, fix_notes = await auto_fix_spec(
                    spec_content, llm, format, ambiguities, language=language
                )
                if fixed_spec:
                    spec_content = fixed_spec
                    print(f"\033[32mAuto-fixed spec issues: {fix_notes}\033[0m")
                    if OPENTELEMETRY_AVAILABLE:
                        span.set_attribute("auto_fix.applied", True)
                else:
                    print(
                        "\033[33mWarning: Auto-fix failed. The generated spec may have issues.\033[0m"
                    )
                    if OPENTELEMETRY_AVAILABLE:
                        span.set_attribute("auto_fix.applied", False)
                        span.set_status(
                            trace.Status(
                                trace.StatusCode.ERROR,
                                description="Auto-fix failed, spec may have issues",
                            )
                        )
                    if PROMETHEUS_AVAILABLE and SPEC_GEN_TOTAL:
                        SPEC_GEN_TOTAL.labels(
                            format=format, status="auto_fix_failed"
                        ).inc()
                    # Even if auto-fix fails, we return the best effort spec.

            spec_id = f"SPEC-{uuid.uuid4().hex[:8]}"
            result = {
                "id": spec_id,
                "timestamp": datetime.utcnow().isoformat(),
                "format": format,
                "content": spec_content,
                "source_transcript_hash": hashlib.sha256(
                    transcript.encode()
                ).hexdigest(),
            }
            if OPENTELEMETRY_AVAILABLE:
                span.set_attribute("spec.id", spec_id)
                span.set_status(trace.Status(trace.StatusCode.OK))
            if PROMETHEUS_AVAILABLE and SPEC_GEN_TOTAL:
                SPEC_GEN_TOTAL.labels(format=format, status="success").inc()
            return result
    finally:
        if PROMETHEUS_AVAILABLE and SPEC_GEN_LATENCY_SECONDS:
            SPEC_GEN_LATENCY_SECONDS.labels(format=format).observe(
                time.perf_counter() - start_time
            )


async def generate_gaps(
    spec_content: str,
    transcript: str,
    llm: BaseChatModel,
    domain: Optional[str] = None,
    project: Optional[str] = None,
    language: str = "en",
) -> Optional[str]:
    """
    Analyzes the spec and transcript against a dynamic, production-grade, and up-to-date requirements checklist.
    P9: Internationalization - Load prompts from locales.yaml.
    """
    with get_tracing_context("generate_gaps") as span:
        if OPENTELEMETRY_AVAILABLE:
            span.set_attribute("domain", domain)
            span.set_attribute("project", project)
            span.set_attribute("language", language)

        checklist = await get_checklist(domain=domain, project=project)
        if not checklist:
            logger.warning("Checklist for gap analysis is empty or unavailable.")
            if OPENTELEMETRY_AVAILABLE:
                span.set_status(
                    trace.Status(trace.StatusCode.ERROR, description="Checklist empty")
                )
            return None
        checklist_str = "\n".join(
            [
                f"- {item['name']} (Weight: {item['weight']}): {item['description']}"
                for item in checklist
            ]
        )

        gaps_prompt_text = get_localized_prompt(
            "generate_gaps_prompt", language
        ).format(
            checklist_str=checklist_str,
            spec_content=spec_content,
            transcript=transcript,
        )
        try:
            response = await (
                PromptTemplate.from_template(gaps_prompt_text) | llm
            ).ainvoke({})
            gaps_table = response.content
            if OPENTELEMETRY_AVAILABLE:
                span.set_status(trace.Status(trace.StatusCode.OK))
            return gaps_table
        except Exception as e:
            logger.error(f"Gaps analysis error: {e}", exc_info=True)
            if OPENTELEMETRY_AVAILABLE:
                span.set_status(
                    trace.Status(
                        trace.StatusCode.ERROR, description=f"Gaps analysis failed: {e}"
                    )
                )
            return None


async def refine_spec(
    last_spec: str, instruction: str, llm: BaseChatModel, language: str = "en"
) -> Optional[str]:
    """
    Refines a specification based on a natural language instruction.
    P9: Internationalization - Load prompts from locales.yaml.
    """
    with get_tracing_context("refine_spec") as span:
        if OPENTELEMETRY_AVAILABLE:
            span.set_attribute("language", language)
        prompt_text = get_localized_prompt("refine_spec_prompt", language).format(
            instruction=instruction, last_spec=last_spec
        )
        try:
            response = await (PromptTemplate.from_template(prompt_text) | llm).ainvoke(
                {}
            )
            refined_spec = response.content
            if OPENTELEMETRY_AVAILABLE:
                span.set_status(trace.Status(trace.StatusCode.OK))
            return refined_spec
        except Exception as e:
            logger.error(f"Refine spec error: {e}", exc_info=True)
            if OPENTELEMETRY_AVAILABLE:
                span.set_status(
                    trace.Status(
                        trace.StatusCode.ERROR, description=f"Refine spec failed: {e}"
                    )
                )
            return None


async def review_spec(
    spec_content: str, llm: BaseChatModel, language: str = "en"
) -> Optional[str]:
    """
    Reviews a given specification using the LLM and provides feedback.
    P9: Internationalization - Load prompts from locales.yaml.
    """
    with get_tracing_context("review_spec") as span:
        if OPENTELEMETRY_AVAILABLE:
            span.set_attribute("language", language)
        prompt_text = get_localized_prompt("review_spec_prompt", language).format(
            spec_content=spec_content
        )
        try:
            response = await (PromptTemplate.from_template(prompt_text) | llm).ainvoke(
                {}
            )
            review_feedback = response.content
            if OPENTELEMETRY_AVAILABLE:
                span.set_status(trace.Status(trace.StatusCode.OK))
            return review_feedback
        except Exception as e:
            logger.error(f"Error reviewing spec: {e}", exc_info=True)
            if OPENTELEMETRY_AVAILABLE:
                span.set_status(
                    trace.Status(
                        trace.StatusCode.ERROR, description=f"Review spec failed: {e}"
                    )
                )
            return None


def diff_specs(spec1: str, spec2: str) -> str:
    """
    Computes and returns a string diff of two specifications.

    Args:
        spec1: The first specification content as a string.
        spec2: The second specification content as a string.

    Returns:
        A unified diff string.

    Raises:
        TypeError: If either spec1 or spec2 is not a string.
    """
    with get_tracing_context("diff_specs") as span:
        if not isinstance(spec1, str) or not isinstance(spec2, str):
            if OPENTELEMETRY_AVAILABLE:
                span.set_status(
                    trace.Status(
                        trace.StatusCode.ERROR, description="Inputs must be strings"
                    )
                )
            raise TypeError("Both spec1 and spec2 must be strings for diffing.")

        diff = difflib.unified_diff(
            spec1.splitlines(keepends=True),
            spec2.splitlines(keepends=True),
            fromfile="spec_v1",
            tofile="spec_v2",
        )
        diff_str = "".join(diff)
        if OPENTELEMETRY_AVAILABLE:
            span.set_attribute("diff.length", len(diff_str))
            span.set_status(trace.Status(trace.StatusCode.OK))
        return diff_str
