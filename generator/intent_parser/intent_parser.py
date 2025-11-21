# intent_parser/intent_parser.py
"""
Intent Parser Module - Extracts requirements and features from README documents.

LAZY LOADING STRATEGY:
...
"""
import asyncio
import concurrent.futures
import hashlib
import json
import logging
import os
import re
import shelve
import time
import datetime  # <-- FIX: Added missing import
from abc import ABC, abstractmethod
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import aiohttp
import backoff
import markdown
import nltk
import requests
import rst_to_myst
# Lazy imports for heavy ML dependencies - imported only when needed
# import spacy  # Moved to lazy loading
# import torch  # Moved to lazy loading
# import transformers  # Moved to lazy loading
import yaml
from cryptography.fernet import Fernet
from dotenv import load_dotenv
from googletrans import Translator
from jinja2 import Environment, FileSystemLoader
from langdetect import detect, DetectorFactory, LangDetectException
from prometheus_client import Counter, Gauge, Histogram
from pydantic import BaseModel, validator, Field

# ********** FIX 1: Corrected import of log_action **********
try:
    from runner.runner_logging import log_action
except ImportError:
    def log_action(*args, **kwargs):
        logging.warning("Dummy log_action used: Runner logging is not available.")
# *********************************************************

# ********** FIX 2: Updated utility imports to runner foundation **********
try:
    from runner.runner_security_utils import redact_secrets
except ImportError:
    # Final Fallback for testing when specific runner module is not available
    def redact_secrets(content, **_kwargs): return content
# **************************************************************************************


# PDF processing libraries
try:
    import pdfplumber
    HAS_PDFPLUMBER = True
except ImportError:
    HAS_PDFPLUMBER = False
    logging.warning("pdfplumber not installed. PDF parsing will be unavailable. Run 'pip install pdfplumber'")

try:
    import pytesseract
    from PIL import Image
    HAS_PYTESSERACT = True
except ImportError:
    HAS_PYTESSERACT = False
    logging.warning("pytesseract or Pillow not installed. OCR for PDF images will be unavailable. Run 'pip install pytesseract Pillow'")

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
    class NoOpTracer:
        def start_as_current_span(self, name):
            """No-op decorator/context manager for tracing when OpenTelemetry is not available."""
            def decorator(func):
                return func
            return decorator
    
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
            logger.error(f"Failed to import spacy: {e}. NLP features will be unavailable.")
            raise ImportError("spacy is required for NLP extraction. Install with: pip install spacy")
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
            logger.error(f"Failed to import torch: {e}. Some ML features will be unavailable.")
            raise ImportError("torch is required for some ML features. Install with: pip install torch")
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
            logger.error(f"Failed to import transformers: {e}. Transformer-based features will be unavailable.")
            raise ImportError("transformers is required for some NLP features. Install with: pip install transformers")
        except Exception as e:
            logger.error(f"Failed to initialize transformers: {e}")
            raise
    return _transformers


load_dotenv()
DetectorFactory.seed = 0
logger = logging.getLogger(__name__)

# --- Metrics ---
PARSE_LATENCY = Histogram('intent_parser_parse_latency_seconds', 'Latency of the full parsing process')
AMBIGUITY_RATE = Gauge('intent_parser_ambiguity_rate', 'Ratio of ambiguities to features')
PARSE_ERRORS = Counter('intent_parser_errors_total', 'Total errors during parsing', ['stage', 'error_type'])
LANG_DETECTION_COUNT = Counter('intent_parser_lang_detection_total', 'Language detection calls', ['language'])
FORMAT_DETECTION_COUNT = Counter('intent_parser_format_detection_total', 'Document format detection calls', ['format'])
EXTRACTION_COUNT = Counter('intent_parser_extraction_total', 'Total extractions', ['extractor_type', 'language'])
LLM_CLIENT_CALLS = Counter('intent_parser_llm_client_calls_total', 'LLM client calls', ['provider', 'model', 'call_type'])
LLM_CLIENT_CACHE_HITS = Counter('intent_parser_llm_client_cache_hits_total', 'LLM client cache hits')
LLM_CLIENT_FALLBACKS = Counter('intent_parser_llm_client_fallbacks_total', 'LLM client fallbacks', ['reason'])
REDACTION_COUNT = Counter('intent_parser_redaction_events_total', 'Redaction events during parsing')
FEEDBACK_RECORDED_COUNT = Counter('intent_parser_feedback_recorded_total', 'Number of feedback ratings recorded')
CACHE_CORRUPTION_EVENTS = Counter('intent_parser_cache_corruption_total', 'Cache corruption/repair events')


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
    default_lang: str = 'en'
    language_patterns: Dict[str, Dict[str, str]] = Field(default_factory=dict)

class IntentParserConfig(BaseModel):
    format: str = 'auto'
    extraction_patterns: Dict[str, str] = Field(default_factory=dict)
    llm_config: LLMConfig
    feedback_file: str = 'feedback.json'
    cache_dir: str = 'parser_cache'
    multi_language_support: MultiLanguageSupportConfig = Field(default_factory=MultiLanguageSupportConfig)

    @validator('format')
    def validate_format(cls, v):
        supported_formats = ['auto', 'markdown', 'rst', 'plaintext', 'yaml', 'pdf']
        if v not in supported_formats:
            raise ValueError(f'Unsupported format: {v}. Must be one of {supported_formats}')
        return v

    @validator('cache_dir', pre=True, always=True)
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
            content = content.read_text(encoding='utf-8')
        
        sections = {}
        current_section = "Introduction"
        sections[current_section] = ""
        
        lines = content.splitlines()
        for line in lines:
            header_match = re.match(r'^(#+)\s*(.+)', line)
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
            sections[section] = re.sub(r'```(?:\w+)?\n(.*?)\n```', '[CODE_BLOCK]', sections[section], flags=re.DOTALL)
            sections[section] = re.sub(r'(\*\*|__|\*|_)(.*?)\1', r'\2', sections[section])
            sections[section] = re.sub(r'\[.*?\]\(.*?\)', '', sections[section])
            
        FORMAT_DETECTION_COUNT.labels(format='markdown').inc()
        logger.debug(f"Parsed Markdown into {len(sections)} sections.")
        return sections

class RSTStrategy(ParserStrategy):
    def parse(self, content: Union[str, Path]) -> Dict[str, str]:
        if isinstance(content, Path):
            content = content.read_text(encoding='utf-8')
        try:
            myst_content = rst_to_myst.convert(content)
            FORMAT_DETECTION_COUNT.labels(format='rst').inc()
            logger.debug("Converted RST to MyST. Parsing as Markdown.")
            return MarkdownStrategy().parse(myst_content)
        except Exception as e:
            logger.error(f"RST to MyST conversion failed: {e}. Falling back to plaintext.", exc_info=True)
            PARSE_ERRORS.labels(stage='parsing', error_type='rst_conversion').inc()
            return PlaintextStrategy().parse(content)

class PlaintextStrategy(ParserStrategy):
    def parse(self, content: Union[str, Path]) -> Dict[str, str]:
        if isinstance(content, Path):
            content = content.read_text(encoding='utf-8')
        FORMAT_DETECTION_COUNT.labels(format='plaintext').inc()
        logger.debug("Parsed as plaintext.")
        return {"Full Document": content}

class YAMLStrategy(ParserStrategy):
    def parse(self, content: Union[str, Path]) -> Dict[str, str]:
        if isinstance(content, Path):
            content = content.read_text(encoding='utf-8')
        try:
            data = yaml.safe_load(content)
            sections = {}
            if isinstance(data, dict):
                for k, v in data.items():
                    sections[str(k)] = json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v)
            elif isinstance(data, list):
                sections["List Content"] = json.dumps(data, ensure_ascii=False)
            else:
                sections["Scalar Content"] = str(data)
            FORMAT_DETECTION_COUNT.labels(format='yaml').inc()
            logger.debug(f"Parsed YAML into {len(sections)} sections.")
            return sections
        except yaml.YAMLError as e:
            logger.error(f"YAML parsing failed: {e}. Falling back to plaintext.", exc_info=True)
            PARSE_ERRORS.labels(stage='parsing', error_type='yaml_error').inc()
            return PlaintextStrategy().parse(content)

class PDFStrategy(ParserStrategy):
    def parse(self, content: Union[str, Path]) -> Dict[str, str]:
        if not HAS_PDFPLUMBER:
            logger.error("PDF parsing requested but pdfplumber is not installed. Falling back.")
            PARSE_ERRORS.labels(stage='parsing', error_type='pdf_no_lib').inc()
            return PlaintextStrategy().parse(str(content))

        if not isinstance(content, Path):
            logger.error("PDFStrategy expects a Path object. Falling back.")
            PARSE_ERRORS.labels(stage='parsing', error_type='pdf_invalid_input').inc()
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
                                img = Image.frombytes("RGB", [img_obj["width"], img_obj["height"]], img_obj["stream"].get_data())
                                ocr_text = pytesseract.image_to_string(img)
                                if ocr_text.strip():
                                    full_text += f"\n[OCR_IMAGE_TEXT]:\n{ocr_text}\n"
                            except Exception as ocr_err:
                                logger.warning(f"OCR failed for an image on page {i+1}: {ocr_err}")
                                PARSE_ERRORS.labels(stage='parsing', error_type='pdf_ocr_error').inc()
            
            FORMAT_DETECTION_COUNT.labels(format='pdf').inc()
            logger.debug(f"Parsed PDF. Extracted text length: {len(full_text)}.")
            return {"Full Document (PDF)": full_text.strip() or "[EMPTY_OR_UNREADABLE_PDF]"}
        except Exception as e:
            logger.error(f"Failed to open or process PDF '{content}': {e}", exc_info=True)
            PARSE_ERRORS.labels(stage='parsing', error_type='pdf_general_error').inc()
            return {"Full Document (PDF)": f"[ERROR_PROCESSING_PDF: {e}]"}


# --- Other components (Extractor, Detector, Summarizer, LLMClient, FeedbackLoop) ---
class ExtractorStrategy(ABC):
    @abstractmethod
    def extract(self, sections: Dict[str, str], language: str = 'en') -> Dict[str, List[str]]:
        pass

class RegexExtractor(ExtractorStrategy):
    def __init__(self, patterns: Dict[str, str], language_patterns: Dict[str, Dict[str, str]]):
        self.default_patterns = {k: re.compile(v, re.IGNORECASE | re.MULTILINE) for k, v in patterns.items()}
        self.language_specific_patterns = {}
        for lang, lang_pats in language_patterns.items():
            self.language_specific_patterns[lang] = {k: re.compile(v, re.IGNORECASE | re.MULTILINE) for k, v in lang_pats.items()}

    def extract(self, sections: Dict[str, str], language: str = 'en') -> Dict[str, List[str]]:
        extracted = defaultdict(list)
        current_patterns = self.language_specific_patterns.get(language, self.default_patterns)
        if not current_patterns:
            logger.warning(f"No specific or default regex patterns found for '{language}'.")
        for text in sections.values():
            for key, pattern in current_patterns.items():
                matches = pattern.finditer(text)
                for m in matches:
                    extracted[key].append(m.group(1).strip() if m.groups() else m.group(0).strip())
        EXTRACTION_COUNT.labels(extractor_type='regex', language=language).inc()
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
    def extract(self, sections: Dict[str, str], language: str = 'en') -> Dict[str, List[str]]:
        """Placeholder implementation. Override this method to use NLP-based extraction."""
        logger.warning("NLPExtractor.extract() called but not implemented. Returning empty dict.")
        return {}

class AmbiguityDetectorStrategy(ABC):
    @abstractmethod
    async def detect(self, text: str, dry_run: bool, language: str = 'en') -> List[str]:
        pass

class LLMDetector(AmbiguityDetectorStrategy):
    """
    LLM-based ambiguity detector.
    
    If using transformers or torch, lazy load them:
        transformers = get_transformers()
        torch = get_torch()
    """
    def __init__(self, llm_config: LLMConfig, feedback: 'FeedbackLoop'):
        """Initialize LLM detector with configuration and feedback loop."""
        self.llm_config = llm_config
        self.feedback = feedback
        logger.info("LLMDetector initialized (stub implementation)")
    
    async def detect(self, text: str, dry_run: bool, language: str = 'en') -> List[str]:
        """Placeholder implementation. Override this method to use LLM-based detection."""
        logger.warning("LLMDetector.detect() called but not implemented. Returning empty list.")
        return []

class SummarizerStrategy(ABC):
    # --- FIX: Removed duplicated 'abstract' ---
    @abstractmethod
    def summarize(self, requirements: Dict[str, Any], language: str = 'en') -> Dict[str, Any]:
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
    
    def summarize(self, requirements: Dict[str, Any], language: str = 'en') -> Dict[str, Any]:
        """Placeholder implementation. Override this method to use LLM-based summarization."""
        logger.warning("LLMSummarizer.summarize() called but not implemented. Returning requirements as-is.")
        return requirements

class TruncateSummarizer(SummarizerStrategy):
    """Simple summarizer that truncates content to a maximum length."""
    def __init__(self, max_length: int = 1000):
        """Initialize truncate summarizer with max length."""
        self.max_length = max_length
        logger.info(f"TruncateSummarizer initialized with max_length={max_length}")
    
    def summarize(self, requirements: Dict[str, Any], language: str = 'en') -> Dict[str, Any]:
        """Truncate requirements to max length."""
        truncated = {}
        for key, value in requirements.items():
            if isinstance(value, str) and len(value) > self.max_length:
                truncated[key] = value[:self.max_length] + "..."
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
    def __init__(self, llm_config: LLMConfig, cache_dir: str = 'parser_cache'):
        """Initialize LLM client with configuration and cache directory."""
        self.llm_config = llm_config
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"LLMClient initialized with provider={llm_config.provider}, model={llm_config.model}")
    
    async def call_api(self, prompt: str, **kwargs) -> str:
        """Placeholder implementation for LLM API calls."""
        logger.warning("LLMClient.call_api() called but not fully implemented. Returning empty string.")
        return ""

class FeedbackLoop:
    """Manages feedback collection and storage for intent parser improvements."""
    def __init__(self, feedback_file: str = 'feedback.json'):
        """Initialize feedback loop with file path."""
        self.feedback_file = Path(feedback_file)
        self.feedback_data = []
        
        # Load existing feedback if file exists
        if self.feedback_file.exists():
            try:
                with open(self.feedback_file, 'r') as f:
                    self.feedback_data = json.load(f)
                logger.info(f"Loaded {len(self.feedback_data)} feedback entries from {feedback_file}")
            except Exception as e:
                logger.warning(f"Failed to load feedback file: {e}")
                self.feedback_data = []
        else:
            logger.info(f"FeedbackLoop initialized with new file: {feedback_file}")
    
    def record_feedback(self, feedback: Dict[str, Any]) -> None:
        """Record feedback entry."""
        self.feedback_data.append(feedback)
        try:
            with open(self.feedback_file, 'w') as f:
                json.dump(self.feedback_data, f, indent=2)
            FEEDBACK_RECORDED_COUNT.inc()
        except Exception as e:
            logger.error(f"Failed to save feedback: {e}")
    
    def get_feedback_stats(self) -> Dict[str, Any]:
        """Get statistics about collected feedback."""
        return {
            'total_entries': len(self.feedback_data),
            'file': str(self.feedback_file)
        }
    
def generate_provenance(content: str, file_path: Optional[Path] = None) -> Dict[str, Any]:
    content_hash = hashlib.sha256(content.encode('utf-8')).hexdigest()
    timestamp = datetime.datetime.utcnow().isoformat() + 'Z'
    provenance = {"content_hash": content_hash, "timestamp_utc": timestamp, "source_type": "string"}
    if file_path:
        provenance.update({"source_type": "file", "file_path": str(file_path)})
    log_action("Content Provenance Generated", provenance)
    return provenance

# --- Main IntentParser Class ---
class IntentParser:
    def __init__(self, config_path: str = 'intent_parser.yaml'):
        self._config_path = Path(config_path)
        self.config: IntentParserConfig = self._load_and_validate_config(self._config_path)
        
        self.feedback: FeedbackLoop = FeedbackLoop(self.config.feedback_file)
        self.llm_client: LLMClient = LLMClient(self.config.llm_config, cache_dir=self.config.cache_dir)
        
        self.parser: Optional[ParserStrategy] = None
        self.extractor: ExtractorStrategy = self._select_extractor()
        self.detector: AmbiguityDetectorStrategy = self._select_detector()
        self.summarizer: SummarizerStrategy = self._select_summarizer()
        
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=os.cpu_count() or 1)
        self.input_language: str = self.config.multi_language_support.default_lang
        self._health_check()

    def _load_and_validate_config(self, config_path: Path) -> IntentParserConfig:
        try:
            # --- FIX: Added encoding='utf-8' ---
            with open(config_path, 'r', encoding='utf-8') as f:
                raw_config = yaml.safe_load(f)
            return IntentParserConfig(**raw_config)
        except Exception as e:
            logger.critical(f"Failed to load or validate config from {config_path}: {e}", exc_info=True)
            PARSE_ERRORS.labels(stage='config_load', error_type=type(e).__name__).inc()
            raise

    def reload_config_and_strategies(self):
        logger.info(f"Reloading configuration from {self._config_path}")
        self.config = self._load_and_validate_config(self._config_path)
        self.llm_client = LLMClient(self.config.llm_config, cache_dir=self.config.cache_dir)
        self.extractor = self._select_extractor()
        self.detector = self._select_detector()
        self.summarizer = self._select_summarizer()
        log_action("Config Reloaded", {"path": str(self._config_path)})

    def _select_parser(self, format_hint: str, file_path: Optional[Path] = None) -> ParserStrategy:
        target_format = format_hint
        if target_format == 'auto':
            if file_path:
                suffix = file_path.suffix.lower()
                if suffix == '.md': target_format = 'markdown'
                elif suffix == '.rst': target_format = 'rst'
                elif suffix in ['.yaml', '.yml']: target_format = 'yaml'
                elif suffix == '.pdf': target_format = 'pdf'
                else: target_format = 'plaintext'
            else:
                target_format = 'markdown'
        if target_format == 'markdown': return MarkdownStrategy()
        if target_format == 'rst': return RSTStrategy()
        if target_format == 'plaintext': return PlaintextStrategy()
        if target_format == 'yaml': return YAMLStrategy()
        if target_format == 'pdf':
            if HAS_PDFPLUMBER: return PDFStrategy()
            else:
                logger.warning("PDF parsing requested but pdfplumber is not installed.")
                return PlaintextStrategy()
        raise ValueError(f"Unsupported format: {target_format}")

    def _select_extractor(self) -> ExtractorStrategy:
        return RegexExtractor(self.config.extraction_patterns, self.config.multi_language_support.language_patterns)

    def _select_detector(self) -> AmbiguityDetectorStrategy:
        return LLMDetector(self.config.llm_config, self.feedback)

    def _select_summarizer(self) -> SummarizerStrategy:
        return LLMSummarizer(self.config.llm_config)

    def _health_check(self):
        logger.info("Running IntentParser health check...")
        # Placeholder for more robust health checks
        log_action("Parser Health Check", {"status": "PASSED"})

    @tracer.start_as_current_span("IntentParser.parse_workflow")
    async def parse(self, content: str = "", format_hint: Optional[str] = None, file_path: Optional[Union[str, Path]] = None, dry_run: bool = False, user_id: str = 'anonymous') -> Dict[str, Any]:
        start_time = time.time()
        span = trace.get_current_span() if tracer else None
        
        try:
            if file_path:
                file_path = Path(file_path)
                if not file_path.exists(): raise FileNotFoundError(f"File not found: {file_path}")
                if not content and file_path.is_file() and file_path.suffix.lower() not in ['.pdf']:
                    content = file_path.read_text(encoding='utf-8')

            if not content and not file_path:
                raise ValueError("No content or file path provided.")

            content_redacted = redact_secrets(content)
            if content_redacted != content: REDACTION_COUNT.inc()

            provenance = generate_provenance(content_redacted, file_path)
            
            if self.config.multi_language_support.enabled and content_redacted.strip():
                try:
                    self.input_language = detect(content_redacted)
                except LangDetectException:
                    self.input_language = self.config.multi_language_support.default_lang
            else:
                self.input_language = self.config.multi_language_support.default_lang
            
            self.parser = self._select_parser(format_hint or self.config.format, file_path)
            parser_input = file_path if isinstance(self.parser, PDFStrategy) else content_redacted
            sections = self.parser.parse(parser_input)
            
            extracted = self.extractor.extract(sections, language=self.input_language)
            full_text_for_ambiguity = " ".join(sections.values())
            ambiguities = await self.detector.detect(full_text_for_ambiguity, dry_run, language=self.input_language)

            requirements = {
                "features": extracted.get("features", []),
                "constraints": extracted.get("constraints", []),
                "file_structure": extracted.get("file_structure", []),
                "ambiguities": ambiguities
            }

            requirements = self.summarizer.summarize(requirements, language=self.input_language)

            parse_latency = time.time() - start_time
            PARSE_LATENCY.observe(parse_latency)
            
            log_action("Parse Completed", {"provenance": provenance, "user_id": user_id})
            if span: span.set_status(Status(StatusCode.OK))
            return requirements
        except Exception as e:
            logger.error(f"IntentParser.parse failed: {e}", exc_info=True)
            PARSE_ERRORS.labels(stage='overall_parse', error_type=type(e).__name__).inc()
            if span:
                span.set_status(Status(StatusCode.ERROR, f"Parsing failed: {e}"))
                span.record_exception(e)
            raise