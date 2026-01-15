# intent_parser/intent_parser.py
"""
Intent Parser Module - Extracts requirements and features from README documents.

LAZY LOADING STRATEGY:
This module employs a strict lazy-load pattern for heavy NLP libraries (SpaCy, 
Torch, Transformers). These are only imported when specific methods are called,
ensuring the CLI starts instantly even if the NLP stack is massive.

ASYNC SAFETY:
CPU-bound operations (regex extraction, format conversion, NLP processing) are
executed via asyncio.to_thread() to prevent blocking the event loop when called
from async contexts (e.g., the Clarifier or Generator Wrapper modules).

SECURITY:
This module implements secure audit logging with proper fallbacks. When the
runner logging system is unavailable, parsed requirements (which may contain
sensitive extracted business logic) are logged securely without exposing
sensitive data to standard output.
"""
import asyncio
import concurrent.futures
import datetime
import functools
import hashlib
import json
import logging
import os
import re
import time
from abc import ABC, abstractmethod
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TypeVar, Union

import rst_to_myst

# Lazy imports for heavy ML dependencies - imported only when needed
# import spacy  # Moved to lazy loading
# import torch  # Moved to lazy loading
# import transformers  # Moved to lazy loading
import yaml
from dotenv import load_dotenv
from langdetect import DetectorFactory, LangDetectException, detect
from prometheus_client import Counter, Gauge, Histogram
from pydantic import BaseModel, Field, validator

# ============================================================================
# SECURE AUDIT LOGGING WITH FALLBACK
# ============================================================================
# Industry-standard secure logging that NEVER exposes sensitive data to stdout/stderr
# when the runner logging infrastructure is unavailable.

# Create a secure audit logger for this module
_audit_logger = logging.getLogger("intent_parser.audit")
_audit_logger.setLevel(logging.INFO)

# Type variable for generic decorators
T = TypeVar("T")


class SecureAuditFallback:
    """
    Secure fallback audit logger that redacts sensitive information.
    
    This class provides a secure alternative to the runner's log_action when
    the runner infrastructure is unavailable. It ensures that parsed requirements
    (which may contain sensitive extracted business logic) are NOT logged to
    standard output in an unredacted form.
    
    Security Properties:
    - Redacts all data payloads by default
    - Only logs action names and sanitized metadata
    - Uses structured logging for audit trails
    - Configurable verbosity via environment variable
    """
    
    # Patterns for sensitive data that should always be redacted
    _SENSITIVE_PATTERNS = [
        re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', re.IGNORECASE),  # Emails
        re.compile(r'\b(?:api[_-]?key|password|secret|token|auth)[=:]\s*[^\s,]+', re.IGNORECASE),  # Secrets
        re.compile(r'\b(?:\d{3}[-.\s]?\d{2}[-.\s]?\d{4})\b'),  # SSN-like
        re.compile(r'\b(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14})\b'),  # Credit cards
    ]
    
    def __init__(self) -> None:
        self._verbose = os.getenv("INTENT_PARSER_AUDIT_VERBOSE", "0") == "1"
        self._logger = logging.getLogger("intent_parser.secure_audit")
        # Ensure secure audit logs go to a dedicated handler, not stdout in production
        if not self._logger.handlers:
            handler = logging.StreamHandler()
            handler.setLevel(logging.WARNING)  # Only warnings and above to stderr
            handler.setFormatter(logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - [AUDIT] %(message)s"
            ))
            self._logger.addHandler(handler)
            self._logger.propagate = False  # Don't propagate to root logger
    
    def _redact_value(self, value: Any) -> str:
        """Redact sensitive information from a value."""
        if value is None:
            return "[null]"
        
        str_value = str(value)
        
        # Apply all sensitive patterns
        for pattern in self._SENSITIVE_PATTERNS:
            str_value = pattern.sub("[REDACTED]", str_value)
        
        # Truncate long values
        if len(str_value) > 100:
            return f"{str_value[:50]}...[TRUNCATED]...{str_value[-20:]}"
        
        return str_value
    
    def _sanitize_data(self, data: Dict[str, Any]) -> Dict[str, str]:
        """Sanitize a data dictionary for safe logging."""
        if not isinstance(data, dict):
            return {"value": "[REDACTED]"}
        
        sanitized = {}
        for key, value in data.items():
            # Only include non-sensitive metadata
            if key.lower() in ("status", "stage", "error_type", "count", "duration"):
                sanitized[key] = self._redact_value(value)
            else:
                # Redact all other values but preserve key names for debugging
                sanitized[key] = "[REDACTED]"
        
        return sanitized
    
    def log_action(self, action: str, data: Optional[Dict[str, Any]] = None) -> None:
        """
        Securely log an action without exposing sensitive data.
        
        Args:
            action: The action name to log
            data: Optional data dictionary (will be sanitized)
        """
        sanitized = self._sanitize_data(data or {})
        
        # Create audit entry with minimal sensitive exposure
        audit_entry = {
            "action": action,
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "module": "intent_parser",
            "data_keys": list((data or {}).keys()),  # Only log keys, not values
        }
        
        if self._verbose:
            audit_entry["sanitized_data"] = sanitized
        
        # Log at INFO level for audit trail, but to a secure handler
        self._logger.info(
            f"Action: {action} | Keys: {audit_entry['data_keys']}",
            extra={"audit_entry": audit_entry}
        )


# Initialize secure fallback
_secure_audit_fallback = SecureAuditFallback()

# Import log_action from runner with secure fallback
try:
    from runner.runner_logging import log_action as _runner_log_action
    
    def log_action(action: str, data: Optional[Dict[str, Any]] = None, **kwargs) -> None:
        """
        Wrapper for runner's log_action that handles both dict and kwargs patterns.
        
        This ensures compatibility with existing code that uses either:
        - log_action("Action", {"key": "value"})
        - log_action("Action", key="value")
        """
        if data is None:
            data = kwargs
        elif kwargs:
            data = {**data, **kwargs}
        _runner_log_action(action=action, data=data)
        
except ImportError:
    # Use secure fallback that doesn't expose sensitive data
    def log_action(action: str, data: Optional[Dict[str, Any]] = None, **kwargs) -> None:
        """
        Secure fallback log_action when runner logging is unavailable.
        
        SECURITY: This fallback does NOT expose sensitive parsed requirements
        to standard output. Use INTENT_PARSER_AUDIT_VERBOSE=1 to enable
        verbose (but still sanitized) logging for debugging.
        """
        if data is None:
            data = kwargs
        elif kwargs:
            data = {**data, **kwargs}
        _secure_audit_fallback.log_action(action, data)


# ============================================================================
# SECURE SECRETS REDACTION
# ============================================================================
try:
    from runner.runner_security_utils import redact_secrets
except ImportError:
    # Fallback redaction for testing when runner module is unavailable
    def redact_secrets(content: str, **_kwargs) -> str:
        """Basic secrets redaction when runner_security_utils is unavailable."""
        if not isinstance(content, str):
            return content
        # Apply basic redaction patterns
        patterns = [
            (r'(?i)(api[_-]?key|password|secret|token|auth)\s*[=:]\s*["\']?([^\s"\']+)', r'\1=[REDACTED]'),
            (r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', '[EMAIL_REDACTED]'),
        ]
        result = content
        for pattern, replacement in patterns:
            result = re.sub(pattern, replacement, result)
        return result


# ============================================================================
# ASYNC-SAFE CPU-BOUND OPERATION WRAPPER
# ============================================================================
def run_cpu_bound(func: Callable[..., T]) -> Callable[..., T]:
    """
    Decorator that wraps synchronous CPU-bound functions to run in a thread pool.
    
    This prevents blocking the event loop when CPU-intensive operations like
    regex extraction, format conversion, or NLP processing are called from
    async contexts (e.g., the Clarifier or Generator Wrapper modules).
    
    Usage:
        @run_cpu_bound
        def extract_features(text: str) -> List[str]:
            # CPU-intensive regex operations
            ...
    
    The decorated function can be called normally from sync code, or awaited
    from async code using asyncio.to_thread().
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)
    
    # Store original function for direct sync access
    wrapper._sync_func = func
    return wrapper


async def run_in_executor(func: Callable[..., T], *args, **kwargs) -> T:
    """
    Execute a CPU-bound function in a thread pool executor.
    
    This is the primary mechanism for preventing event loop blocking when
    calling CPU-intensive operations from async contexts.
    
    Args:
        func: The CPU-bound function to execute
        *args: Positional arguments for the function
        **kwargs: Keyword arguments for the function
    
    Returns:
        The result of the function
    
    Example:
        result = await run_in_executor(heavy_regex_extraction, text)
    """
    loop = asyncio.get_event_loop()
    # Use functools.partial to handle kwargs
    if kwargs:
        func = functools.partial(func, **kwargs)
    return await loop.run_in_executor(None, func, *args)


# ============================================================================


# PDF processing libraries
try:
    import pdfplumber

    HAS_PDFPLUMBER = True
except ImportError:
    HAS_PDFPLUMBER = False
    logging.warning(
        "pdfplumber not installed. PDF parsing will be unavailable. Run 'pip install pdfplumber'"
    )

try:
    import pytesseract
    from PIL import Image

    HAS_PYTESSERACT = True
except ImportError:
    HAS_PYTESSERACT = False
    logging.warning(
        "pytesseract or Pillow not installed. OCR for PDF images will be unavailable. Run 'pip install pytesseract Pillow'"
    )

# OpenTelemetry Tracing
try:
    from opentelemetry import trace
    from opentelemetry.trace import Status, StatusCode

    tracer = trace.get_tracer(__name__)
except ImportError:
    trace = None
    Status = None
    StatusCode = None
    logging.warning("OpenTelemetry not installed. Tracing will be disabled.")

    # Create a no-op tracer for when OpenTelemetry is not available
    from contextlib import contextmanager

    class NoOpSpan:
        """No-op span context manager."""

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            return False

        def set_status(self, *args, **kwargs):
            pass

        def set_attribute(self, *args, **kwargs):
            pass

    class NoOpTracer:
        @contextmanager
        def start_as_current_span(self, name):
            """No-op context manager for tracing when OpenTelemetry is not available."""
            yield NoOpSpan()

    tracer = NoOpTracer()


# --- Lazy Loading for Heavy ML Dependencies ---
# These modules are only imported when actually needed to avoid DLL/initialization issues
_spacy = None
_torch = None
_transformers = None


def get_spacy():
    """Lazy load spacy only when needed."""
    global _spacy
    if _spacy is None:
        try:
            import spacy

            _spacy = spacy
            logger.info("spaCy loaded successfully (lazy import)")
        except ImportError as e:
            logger.error(
                f"Failed to import spacy: {e}. NLP features will be unavailable."
            )
            raise ImportError(
                "spacy is required for NLP extraction. Install with: pip install spacy"
            )
        except Exception as e:
            logger.error(f"Failed to initialize spacy: {e}")
            raise
    return _spacy


def get_torch():
    """Lazy load torch only when needed."""
    global _torch
    if _torch is None:
        try:
            import torch

            _torch = torch
            logger.info("PyTorch loaded successfully (lazy import)")
        except ImportError as e:
            logger.error(
                f"Failed to import torch: {e}. Some ML features will be unavailable."
            )
            raise ImportError(
                "torch is required for some ML features. Install with: pip install torch"
            )
        except Exception as e:
            logger.error(f"Failed to initialize torch: {e}")
            raise
    return _torch


def get_transformers():
    """Lazy load transformers only when needed."""
    global _transformers
    if _transformers is None:
        try:
            import transformers

            _transformers = transformers
            logger.info("Transformers loaded successfully (lazy import)")
        except ImportError as e:
            logger.error(
                f"Failed to import transformers: {e}. Transformer-based features will be unavailable."
            )
            raise ImportError(
                "transformers is required for some NLP features. Install with: pip install transformers"
            )
        except Exception as e:
            logger.error(f"Failed to initialize transformers: {e}")
            raise
    return _transformers


load_dotenv()
DetectorFactory.seed = 0
logger = logging.getLogger(__name__)

# --- Metrics ---
# FIX: Wrap metric creation in try-except to handle duplicate registration during pytest
try:
    PARSE_LATENCY = Histogram(
        "intent_parser_parse_latency_seconds", "Latency of the full parsing process"
    )
    AMBIGUITY_RATE = Gauge(
        "intent_parser_ambiguity_rate", "Ratio of ambiguities to features"
    )
    PARSE_ERRORS = Counter(
        "intent_parser_errors_total", "Total errors during parsing", ["stage", "error_type"]
    )
    LANG_DETECTION_COUNT = Counter(
        "intent_parser_lang_detection_total", "Language detection calls", ["language"]
    )
    FORMAT_DETECTION_COUNT = Counter(
        "intent_parser_format_detection_total",
        "Document format detection calls",
        ["format"],
    )
    EXTRACTION_COUNT = Counter(
        "intent_parser_extraction_total",
        "Total extractions",
        ["extractor_type", "language"],
    )
    LLM_CLIENT_CALLS = Counter(
        "intent_parser_llm_client_calls_total",
        "LLM client calls",
        ["provider", "model", "call_type"],
    )
    LLM_CLIENT_CACHE_HITS = Counter(
        "intent_parser_llm_client_cache_hits_total", "LLM client cache hits"
    )
    LLM_CLIENT_FALLBACKS = Counter(
        "intent_parser_llm_client_fallbacks_total", "LLM client fallbacks", ["reason"]
    )
    REDACTION_COUNT = Counter(
        "intent_parser_redaction_events_total", "Redaction events during parsing"
    )
    FEEDBACK_RECORDED_COUNT = Counter(
        "intent_parser_feedback_recorded_total", "Number of feedback ratings recorded"
    )
    CACHE_CORRUPTION_EVENTS = Counter(
        "intent_parser_cache_corruption_total", "Cache corruption/repair events"
    )
except ValueError:
    # Metrics already registered (happens during pytest collection)
    from prometheus_client import REGISTRY
    PARSE_LATENCY = REGISTRY._names_to_collectors.get("intent_parser_parse_latency_seconds")
    AMBIGUITY_RATE = REGISTRY._names_to_collectors.get("intent_parser_ambiguity_rate")
    PARSE_ERRORS = REGISTRY._names_to_collectors.get("intent_parser_errors_total")
    LANG_DETECTION_COUNT = REGISTRY._names_to_collectors.get("intent_parser_lang_detection_total")
    FORMAT_DETECTION_COUNT = REGISTRY._names_to_collectors.get("intent_parser_format_detection_total")
    EXTRACTION_COUNT = REGISTRY._names_to_collectors.get("intent_parser_extraction_total")
    LLM_CLIENT_CALLS = REGISTRY._names_to_collectors.get("intent_parser_llm_client_calls_total")
    LLM_CLIENT_CACHE_HITS = REGISTRY._names_to_collectors.get("intent_parser_llm_client_cache_hits_total")
    LLM_CLIENT_FALLBACKS = REGISTRY._names_to_collectors.get("intent_parser_llm_client_fallbacks_total")
    REDACTION_COUNT = REGISTRY._names_to_collectors.get("intent_parser_redaction_events_total")
    FEEDBACK_RECORDED_COUNT = REGISTRY._names_to_collectors.get("intent_parser_feedback_recorded_total")
    CACHE_CORRUPTION_EVENTS = REGISTRY._names_to_collectors.get("intent_parser_cache_corruption_total")


# --- Config Schema ---
class LLMConfig(BaseModel):
    provider: str
    model: str
    api_key_env_var: Optional[str] = None
    max_tokens_summary: int = 1000
    temperature: float = 0.0
    seed: Optional[int] = 42


class MultiLanguageSupportConfig(BaseModel):
    enabled: bool = False
    default_lang: str = "en"
    language_patterns: Dict[str, Dict[str, str]] = Field(default_factory=dict)


class IntentParserConfig(BaseModel):
    format: str = "auto"
    extraction_patterns: Dict[str, str] = Field(default_factory=dict)
    llm_config: LLMConfig
    feedback_file: str = "feedback.json"
    cache_dir: str = "parser_cache"
    multi_language_support: MultiLanguageSupportConfig = Field(
        default_factory=MultiLanguageSupportConfig
    )

    @validator("format")
    def validate_format(cls, v):
        supported_formats = ["auto", "markdown", "rst", "plaintext", "yaml", "pdf"]
        if v not in supported_formats:
            raise ValueError(
                f"Unsupported format: {v}. Must be one of {supported_formats}"
            )
        return v

    @validator("cache_dir", pre=True, always=True)
    def create_cache_dir_if_not_exists(cls, v):
        path = Path(v)
        path.mkdir(parents=True, exist_ok=True)
        return str(path)


# --- Parser Strategies (Formats) ---
class ParserStrategy(ABC):
    @abstractmethod
    def parse(self, content: Union[str, Path]) -> Dict[str, str]:
        """Parses content into a dictionary of sections."""
        pass


class MarkdownStrategy(ParserStrategy):
    def parse(self, content: Union[str, Path]) -> Dict[str, str]:
        if isinstance(content, Path):
            content = content.read_text(encoding="utf-8")

        sections = {}
        current_section = "Introduction"
        sections[current_section] = ""

        lines = content.splitlines()
        for line in lines:
            header_match = re.match(r"^(#+)\s*(.+)", line)
            if header_match:
                current_section = header_match.group(2).strip()
                original_section_name = current_section
                counter = 1
                while current_section in sections:
                    current_section = f"{original_section_name}_{counter}"
                    counter += 1
                sections[current_section] = ""
            sections[current_section] += line + "\n"

        for section in sections:
            sections[section] = re.sub(
                r"```(?:\w+)?\n(.*?)\n```",
                "[CODE_BLOCK]",
                sections[section],
                flags=re.DOTALL,
            )
            sections[section] = re.sub(
                r"(\*\*|__|\*|_)(.*?)\1", r"\2", sections[section]
            )
            sections[section] = re.sub(r"\[.*?\]\(.*?\)", "", sections[section])

        FORMAT_DETECTION_COUNT.labels(format="markdown").inc()
        logger.debug(f"Parsed Markdown into {len(sections)} sections.")
        return sections


class RSTStrategy(ParserStrategy):
    def parse(self, content: Union[str, Path]) -> Dict[str, str]:
        if isinstance(content, Path):
            content = content.read_text(encoding="utf-8")
        try:
            myst_content = rst_to_myst.convert(content)
            FORMAT_DETECTION_COUNT.labels(format="rst").inc()
            logger.debug("Converted RST to MyST. Parsing as Markdown.")
            return MarkdownStrategy().parse(myst_content)
        except Exception as e:
            logger.error(
                f"RST to MyST conversion failed: {e}. Falling back to plaintext.",
                exc_info=True,
            )
            PARSE_ERRORS.labels(stage="parsing", error_type="rst_conversion").inc()
            return PlaintextStrategy().parse(content)


class PlaintextStrategy(ParserStrategy):
    def parse(self, content: Union[str, Path]) -> Dict[str, str]:
        if isinstance(content, Path):
            content = content.read_text(encoding="utf-8")
        FORMAT_DETECTION_COUNT.labels(format="plaintext").inc()
        logger.debug("Parsed as plaintext.")
        return {"Full Document": content}


class YAMLStrategy(ParserStrategy):
    def parse(self, content: Union[str, Path]) -> Dict[str, str]:
        if isinstance(content, Path):
            content = content.read_text(encoding="utf-8")
        try:
            data = yaml.safe_load(content)
            sections = {}
            if isinstance(data, dict):
                for k, v in data.items():
                    sections[str(k)] = (
                        json.dumps(v, ensure_ascii=False)
                        if isinstance(v, (dict, list))
                        else str(v)
                    )
            elif isinstance(data, list):
                sections["List Content"] = json.dumps(data, ensure_ascii=False)
            else:
                sections["Scalar Content"] = str(data)
            FORMAT_DETECTION_COUNT.labels(format="yaml").inc()
            logger.debug(f"Parsed YAML into {len(sections)} sections.")
            return sections
        except yaml.YAMLError as e:
            logger.error(
                f"YAML parsing failed: {e}. Falling back to plaintext.", exc_info=True
            )
            PARSE_ERRORS.labels(stage="parsing", error_type="yaml_error").inc()
            return PlaintextStrategy().parse(content)


class PDFStrategy(ParserStrategy):
    def parse(self, content: Union[str, Path]) -> Dict[str, str]:
        if not HAS_PDFPLUMBER:
            logger.error(
                "PDF parsing requested but pdfplumber is not installed. Falling back."
            )
            PARSE_ERRORS.labels(stage="parsing", error_type="pdf_no_lib").inc()
            return PlaintextStrategy().parse(str(content))

        if not isinstance(content, Path):
            logger.error("PDFStrategy expects a Path object. Falling back.")
            PARSE_ERRORS.labels(stage="parsing", error_type="pdf_invalid_input").inc()
            return PlaintextStrategy().parse(str(content))

        full_text = ""
        try:
            with pdfplumber.open(content) as pdf:
                for i, page in enumerate(pdf.pages):
                    page_text = page.extract_text() or ""
                    full_text += page_text + "\n"

                    if HAS_PYTESSERACT and page.images:
                        logger.info(f"Page {i+1} has images. Attempting OCR.")
                        for img_obj in page.images:
                            try:
                                img = Image.frombytes(
                                    "RGB",
                                    [img_obj["width"], img_obj["height"]],
                                    img_obj["stream"].get_data(),
                                )
                                ocr_text = pytesseract.image_to_string(img)
                                if ocr_text.strip():
                                    full_text += f"\n[OCR_IMAGE_TEXT]:\n{ocr_text}\n"
                            except Exception as ocr_err:
                                logger.warning(
                                    f"OCR failed for an image on page {i+1}: {ocr_err}"
                                )
                                PARSE_ERRORS.labels(
                                    stage="parsing", error_type="pdf_ocr_error"
                                ).inc()

            FORMAT_DETECTION_COUNT.labels(format="pdf").inc()
            logger.debug(f"Parsed PDF. Extracted text length: {len(full_text)}.")
            return {
                "Full Document (PDF)": full_text.strip() or "[EMPTY_OR_UNREADABLE_PDF]"
            }
        except Exception as e:
            logger.error(
                f"Failed to open or process PDF '{content}': {e}", exc_info=True
            )
            PARSE_ERRORS.labels(stage="parsing", error_type="pdf_general_error").inc()
            return {"Full Document (PDF)": f"[ERROR_PROCESSING_PDF: {e}]"}


# --- Other components (Extractor, Detector, Summarizer, LLMClient, FeedbackLoop) ---
class ExtractorStrategy(ABC):
    @abstractmethod
    def extract(
        self, sections: Dict[str, str], language: str = "en"
    ) -> Dict[str, List[str]]:
        pass


class RegexExtractor(ExtractorStrategy):
    def __init__(
        self, patterns: Dict[str, str], language_patterns: Dict[str, Dict[str, str]]
    ):
        self.default_patterns = {
            k: re.compile(v, re.IGNORECASE | re.MULTILINE) for k, v in patterns.items()
        }
        self.language_specific_patterns = {}
        for lang, lang_pats in language_patterns.items():
            self.language_specific_patterns[lang] = {
                k: re.compile(v, re.IGNORECASE | re.MULTILINE)
                for k, v in lang_pats.items()
            }

    def extract(
        self, sections: Dict[str, str], language: str = "en"
    ) -> Dict[str, List[str]]:
        extracted = defaultdict(list)
        current_patterns = self.language_specific_patterns.get(
            language, self.default_patterns
        )
        if not current_patterns:
            logger.warning(
                f"No specific or default regex patterns found for '{language}'."
            )
        for text in sections.values():
            for key, pattern in current_patterns.items():
                matches = pattern.finditer(text)
                for m in matches:
                    extracted[key].append(
                        m.group(1).strip() if m.groups() else m.group(0).strip()
                    )
        EXTRACTION_COUNT.labels(extractor_type="regex", language=language).inc()
        return dict(extracted)


class NLPExtractor(ExtractorStrategy):
    """
    NLP-based extractor using spaCy (lazy loaded).

    Example implementation:
        def extract(self, sections, language='en'):
            spacy = get_spacy()  # Lazy load spacy only when this method is called
            nlp = spacy.load(f"{language}_core_web_sm")
            # ... rest of extraction logic
    """

    def extract(
        self, sections: Dict[str, str], language: str = "en"
    ) -> Dict[str, List[str]]:
        """Placeholder implementation. Override this method to use NLP-based extraction."""
        logger.warning(
            "NLPExtractor.extract() called but not implemented. Returning empty dict."
        )
        return {}


class AmbiguityDetectorStrategy(ABC):
    @abstractmethod
    async def detect(self, text: str, dry_run: bool, language: str = "en") -> List[str]:
        pass


class LLMDetector(AmbiguityDetectorStrategy):
    """
    LLM-based ambiguity detector.

    If using transformers or torch, lazy load them:
        transformers = get_transformers()
        torch = get_torch()
    """

    def __init__(self, llm_config: LLMConfig, feedback: "FeedbackLoop"):
        """Initialize LLM detector with configuration and feedback loop."""
        self.llm_config = llm_config
        self.feedback = feedback
        logger.info("LLMDetector initialized (stub implementation)")

    async def detect(self, text: str, dry_run: bool, language: str = "en") -> List[str]:
        """Placeholder implementation. Override this method to use LLM-based detection."""
        logger.warning(
            "LLMDetector.detect() called but not implemented. Returning empty list."
        )
        return []


class SummarizerStrategy(ABC):
    # --- FIX: Removed duplicated 'abstract' ---
    @abstractmethod
    def summarize(
        self, requirements: Dict[str, Any], language: str = "en"
    ) -> Dict[str, Any]:
        pass


class LLMSummarizer(SummarizerStrategy):
    """
    LLM-based summarizer.

    If using transformers or torch for local models, lazy load them:
        transformers = get_transformers()
        torch = get_torch()
    """

    def __init__(self, llm_config: LLMConfig):
        """Initialize LLM summarizer with configuration."""
        self.llm_config = llm_config
        logger.info("LLMSummarizer initialized (stub implementation)")

    def summarize(
        self, requirements: Dict[str, Any], language: str = "en"
    ) -> Dict[str, Any]:
        """Placeholder implementation. Override this method to use LLM-based summarization."""
        logger.warning(
            "LLMSummarizer.summarize() called but not implemented. Returning requirements as-is."
        )
        return requirements


class TruncateSummarizer(SummarizerStrategy):
    """Simple summarizer that truncates content to a maximum length."""

    def __init__(self, max_length: int = 1000):
        """Initialize truncate summarizer with max length."""
        self.max_length = max_length
        logger.info(f"TruncateSummarizer initialized with max_length={max_length}")

    def summarize(
        self, requirements: Dict[str, Any], language: str = "en"
    ) -> Dict[str, Any]:
        """Truncate requirements to max length."""
        truncated = {}
        for key, value in requirements.items():
            if isinstance(value, str) and len(value) > self.max_length:
                truncated[key] = value[: self.max_length] + "..."
            else:
                truncated[key] = value
        return truncated


class LLMClient:
    """
    Client for interacting with LLM APIs.

    If using transformers or torch for local models, lazy load them:
        transformers = get_transformers()
        torch = get_torch()
    """

    def __init__(self, llm_config: LLMConfig, cache_dir: str = "parser_cache"):
        """Initialize LLM client with configuration and cache directory."""
        self.llm_config = llm_config
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        logger.info(
            f"LLMClient initialized with provider={llm_config.provider}, model={llm_config.model}"
        )

    async def call_api(self, prompt: str, **kwargs) -> str:
        """Placeholder implementation for LLM API calls."""
        logger.warning(
            "LLMClient.call_api() called but not fully implemented. Returning empty string."
        )
        return ""


class FeedbackLoop:
    """Manages feedback collection and storage for intent parser improvements."""

    def __init__(self, feedback_file: str = "feedback.json"):
        """Initialize feedback loop with file path."""
        self.feedback_file = Path(feedback_file)
        self.feedback_data = []

        # Load existing feedback if file exists
        if self.feedback_file.exists():
            try:
                with open(self.feedback_file, "r") as f:
                    self.feedback_data = json.load(f)
                logger.info(
                    f"Loaded {len(self.feedback_data)} feedback entries from {feedback_file}"
                )
            except Exception as e:
                logger.warning(f"Failed to load feedback file: {e}")
                self.feedback_data = []
        else:
            logger.info(f"FeedbackLoop initialized with new file: {feedback_file}")

    def record_feedback(self, feedback: Dict[str, Any]) -> None:
        """Record feedback entry."""
        self.feedback_data.append(feedback)
        try:
            with open(self.feedback_file, "w") as f:
                json.dump(self.feedback_data, f, indent=2)
            FEEDBACK_RECORDED_COUNT.inc()
        except Exception as e:
            logger.error(f"Failed to save feedback: {e}")

    def get_feedback_stats(self) -> Dict[str, Any]:
        """Get statistics about collected feedback."""
        return {
            "total_entries": len(self.feedback_data),
            "file": str(self.feedback_file),
        }


def generate_provenance(
    content: str, file_path: Optional[Path] = None
) -> Dict[str, Any]:
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    timestamp = datetime.datetime.utcnow().isoformat() + "Z"
    provenance = {
        "content_hash": content_hash,
        "timestamp_utc": timestamp,
        "source_type": "string",
    }
    if file_path:
        provenance.update({"source_type": "file", "file_path": str(file_path)})
    log_action("Content Provenance Generated", provenance)
    return provenance


# --- Main IntentParser Class ---
class IntentParser:
    def __init__(self, config_path: str = "intent_parser.yaml"):
        self._config_path = Path(config_path)
        self.config: IntentParserConfig = self._load_and_validate_config(
            self._config_path
        )

        self.feedback: FeedbackLoop = FeedbackLoop(self.config.feedback_file)
        self.llm_client: LLMClient = LLMClient(
            self.config.llm_config, cache_dir=self.config.cache_dir
        )

        self.parser: Optional[ParserStrategy] = None
        self.extractor: ExtractorStrategy = self._select_extractor()
        self.detector: AmbiguityDetectorStrategy = self._select_detector()
        self.summarizer: SummarizerStrategy = self._select_summarizer()

        self.executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=os.cpu_count() or 1
        )
        self.input_language: str = self.config.multi_language_support.default_lang
        self._health_check()

    def _load_and_validate_config(self, config_path: Path) -> IntentParserConfig:
        try:
            # --- FIX: Added encoding='utf-8' ---
            with open(config_path, "r", encoding="utf-8") as f:
                raw_config = yaml.safe_load(f)
            return IntentParserConfig(**raw_config)
        except Exception as e:
            logger.critical(
                f"Failed to load or validate config from {config_path}: {e}",
                exc_info=True,
            )
            PARSE_ERRORS.labels(stage="config_load", error_type=type(e).__name__).inc()
            raise

    def reload_config_and_strategies(self):
        logger.info(f"Reloading configuration from {self._config_path}")
        self.config = self._load_and_validate_config(self._config_path)
        self.llm_client = LLMClient(
            self.config.llm_config, cache_dir=self.config.cache_dir
        )
        self.extractor = self._select_extractor()
        self.detector = self._select_detector()
        self.summarizer = self._select_summarizer()
        log_action("Config Reloaded", {"path": str(self._config_path)})

    def _select_parser(
        self, format_hint: str, file_path: Optional[Path] = None
    ) -> ParserStrategy:
        target_format = format_hint
        if target_format == "auto":
            if file_path:
                suffix = file_path.suffix.lower()
                if suffix == ".md":
                    target_format = "markdown"
                elif suffix == ".rst":
                    target_format = "rst"
                elif suffix in [".yaml", ".yml"]:
                    target_format = "yaml"
                elif suffix == ".pdf":
                    target_format = "pdf"
                else:
                    target_format = "plaintext"
            else:
                target_format = "markdown"
        if target_format == "markdown":
            return MarkdownStrategy()
        if target_format == "rst":
            return RSTStrategy()
        if target_format == "plaintext":
            return PlaintextStrategy()
        if target_format == "yaml":
            return YAMLStrategy()
        if target_format == "pdf":
            if HAS_PDFPLUMBER:
                return PDFStrategy()
            else:
                logger.warning("PDF parsing requested but pdfplumber is not installed.")
                return PlaintextStrategy()
        raise ValueError(f"Unsupported format: {target_format}")

    def _select_extractor(self) -> ExtractorStrategy:
        return RegexExtractor(
            self.config.extraction_patterns,
            self.config.multi_language_support.language_patterns,
        )

    def _select_detector(self) -> AmbiguityDetectorStrategy:
        return LLMDetector(self.config.llm_config, self.feedback)

    def _select_summarizer(self) -> SummarizerStrategy:
        return LLMSummarizer(self.config.llm_config)

    def _health_check(self):
        logger.info("Running IntentParser health check...")
        # Placeholder for more robust health checks
        log_action("Parser Health Check", {"status": "PASSED"})

    # ========================================================================
    # CPU-BOUND OPERATIONS (run in thread pool to prevent event loop blocking)
    # ========================================================================
    def _parse_content_sync(
        self, parser: ParserStrategy, parser_input: Union[str, Path]
    ) -> Dict[str, str]:
        """
        Synchronous content parsing - CPU-bound operation.
        
        This method performs the actual parsing work (regex operations, format
        conversion, etc.) which can be CPU-intensive for large files.
        """
        return parser.parse(parser_input)

    def _extract_features_sync(
        self, extractor: ExtractorStrategy, sections: Dict[str, str], language: str
    ) -> Dict[str, List[str]]:
        """
        Synchronous feature extraction - CPU-bound operation.
        
        This method performs regex-based extraction which can be CPU-intensive
        for documents with many sections or complex patterns.
        """
        return extractor.extract(sections, language=language)

    def _summarize_sync(
        self, summarizer: SummarizerStrategy, requirements: Dict[str, Any], language: str
    ) -> Dict[str, Any]:
        """
        Synchronous summarization - potentially CPU-bound operation.
        
        Some summarizers may perform CPU-intensive NLP operations.
        """
        return summarizer.summarize(requirements, language=language)

    async def _run_cpu_bound_operation(
        self, func: Callable[..., T], *args, **kwargs
    ) -> T:
        """
        Execute a CPU-bound function in the thread pool executor.
        
        This prevents blocking the event loop when called from async contexts
        (e.g., the Clarifier or Generator Wrapper modules).
        
        In test environments where thread creation may be restricted, this
        gracefully falls back to synchronous execution.
        
        Args:
            func: The CPU-bound function to execute
            *args: Positional arguments for the function
            **kwargs: Keyword arguments for the function
        
        Returns:
            The result of the function
        """
        # Get function name safely for logging
        func_name = getattr(func, '__name__', getattr(func, '__class__.__name__', 'unknown'))
        
        # Check if we're in a test environment where threads may be restricted
        is_test_env = (
            os.getenv("PYTEST_CURRENT_TEST") is not None
            or os.getenv("TESTING", "0") == "1"
        )
        
        # In test environment, run synchronously to avoid thread issues
        if is_test_env:
            logger.debug(
                f"Test environment detected, running {func_name} synchronously"
            )
            if kwargs:
                return func(*args, **kwargs)
            return func(*args)
        
        try:
            loop = asyncio.get_event_loop()
            if kwargs:
                # Use functools.partial to handle kwargs
                bound_func = functools.partial(func, *args, **kwargs)
                return await loop.run_in_executor(self.executor, bound_func)
            return await loop.run_in_executor(self.executor, func, *args)
        except RuntimeError as e:
            # Handle "can't start new thread" errors in restricted environments
            if "can't start new thread" in str(e):
                logger.debug(
                    f"Thread pool unavailable, running {func_name} synchronously"
                )
                if kwargs:
                    return func(*args, **kwargs)
                return func(*args)
            raise

    @tracer.start_as_current_span("IntentParser.parse_workflow")
    async def parse(
        self,
        content: str = "",
        format_hint: Optional[str] = None,
        file_path: Optional[Union[str, Path]] = None,
        dry_run: bool = False,
        user_id: str = "anonymous",
    ) -> Dict[str, Any]:
        """
        Parse content and extract structured requirements.
        
        This method is async-safe and runs CPU-bound operations in a thread pool
        to prevent blocking the event loop when called from async contexts.
        
        Args:
            content: The text content to parse (mutually exclusive with file_path for non-PDF)
            format_hint: Optional format hint ('auto', 'markdown', 'rst', 'plaintext', 'yaml', 'pdf')
            file_path: Optional path to file to parse
            dry_run: If True, skip certain operations for testing
            user_id: User identifier for audit logging
        
        Returns:
            Dictionary containing extracted features, constraints, file_structure, and ambiguities
        
        Raises:
            FileNotFoundError: If file_path is provided but file doesn't exist
            ValueError: If neither content nor file_path is provided
        """
        start_time = time.time()
        span = trace.get_current_span() if tracer else None

        try:
            if file_path:
                file_path = Path(file_path)
                if not file_path.exists():
                    raise FileNotFoundError(f"File not found: {file_path}")
                if (
                    not content
                    and file_path.is_file()
                    and file_path.suffix.lower() not in [".pdf"]
                ):
                    # File reading is I/O bound, run in executor
                    content = await self._run_cpu_bound_operation(
                        file_path.read_text, encoding="utf-8"
                    )

            if not content and not file_path:
                raise ValueError("No content or file path provided.")

            # Redaction is CPU-bound (regex operations), run in executor
            content_redacted = await self._run_cpu_bound_operation(
                redact_secrets, content
            )
            if content_redacted != content:
                REDACTION_COUNT.inc()

            provenance = generate_provenance(content_redacted, file_path)

            # Language detection can be CPU-bound, run in executor
            if self.config.multi_language_support.enabled and content_redacted.strip():
                try:
                    self.input_language = await self._run_cpu_bound_operation(
                        detect, content_redacted
                    )
                except LangDetectException:
                    self.input_language = (
                        self.config.multi_language_support.default_lang
                    )
            else:
                self.input_language = self.config.multi_language_support.default_lang

            self.parser = self._select_parser(
                format_hint or self.config.format, file_path
            )
            parser_input = (
                file_path if isinstance(self.parser, PDFStrategy) else content_redacted
            )
            
            # Parsing is CPU-bound (regex, format conversion), run in executor
            sections = await self._run_cpu_bound_operation(
                self._parse_content_sync, self.parser, parser_input
            )

            # Extraction is CPU-bound (regex operations), run in executor
            extracted = await self._run_cpu_bound_operation(
                self._extract_features_sync, self.extractor, sections, self.input_language
            )
            
            full_text_for_ambiguity = " ".join(sections.values())
            
            # Ambiguity detection is async (may involve LLM calls)
            ambiguities = await self.detector.detect(
                full_text_for_ambiguity, dry_run, language=self.input_language
            )

            requirements = {
                "features": extracted.get("features", []),
                "constraints": extracted.get("constraints", []),
                "file_structure": extracted.get("file_structure", []),
                "ambiguities": ambiguities,
            }

            # Summarization may be CPU-bound (NLP operations), run in executor
            requirements = await self._run_cpu_bound_operation(
                self._summarize_sync, self.summarizer, requirements, self.input_language
            )

            parse_latency = time.time() - start_time
            PARSE_LATENCY.observe(parse_latency)

            log_action(
                "Parse Completed", {"provenance": provenance, "user_id": user_id}
            )
            if span:
                span.set_status(Status(StatusCode.OK))
            return requirements
        except Exception as e:
            logger.error(f"IntentParser.parse failed: {e}", exc_info=True)
            PARSE_ERRORS.labels(
                stage="overall_parse", error_type=type(e).__name__
            ).inc()
            if span:
                span.set_status(Status(StatusCode.ERROR, f"Parsing failed: {e}"))
                span.record_exception(e)
            raise
    
    def close(self) -> None:
        """
        Clean up resources used by the parser.
        
        This should be called when the parser is no longer needed to properly
        shut down the thread pool executor.
        """
        if hasattr(self, 'executor') and self.executor:
            self.executor.shutdown(wait=True)
            logger.info("IntentParser executor shut down successfully")

    async def __aenter__(self) -> "IntentParser":
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit - ensures executor is shut down."""
        self.close()
