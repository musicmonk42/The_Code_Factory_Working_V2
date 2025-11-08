"""
docgen_response_validator.py
MERGED FILE: Combines docgen_response_handler.py and docgen_validator.py.

This is the unified pipeline for parsing, validating, enriching, and auto-correcting
LLM-generated documentation. It features a unified plugin registry, advanced NLP-based
quality scoring, and full integration with the central runner foundation.

Features:
- Unified plugin registry (DocGenPlugin) for format handling (MD, RST, HTML, etc.) with hot-reload.
- Advanced safety/security scanning with Presidio and content pattern matching.
- GOAT Upgrade: NLP-powered quality assessment (readability, sentiment, coherence, anomaly detection).
- Intelligent auto-correction and section repair via central runner's LLM client.
- Contextual enrichment with badges, diagrams, links, and Git changelogs.
- Explainable reports with provenance, rationale, and step tracing.
- API (FastAPI) and CLI (unittest) integration.

STRICT FAILURE ENFORCEMENT:
- Presidio is REQUIRED for PII/secret scrubbing. No regex fallback.
- All DocGenPlugins are REQUIRED. No fallback to default if missing.
- Central Runner LLM client is REQUIRED for all repair/correction.
- All key external tools (e.g., Pandoc, docutils, NLTK) are checked for.
"""

import asyncio
import uuid
import logging
import json
import os
import re
import glob
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple, Type, Union
from abc import ABC, abstractmethod
from pathlib import Path
import importlib.util
import sys
import subprocess
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# --- Format/Tooling Dependencies ---
import pypandoc  # For format conversions
import docutils.core  # For RST validation
from bs4 import BeautifulSoup # For HTML validation
import nltk  # For GOAT NLP metrics
from nltk.sentiment import SentimentIntensityAnalyzer  # For sentiment analysis
from nltk.tokenize import sent_tokenize
from nltk.corpus import stopwords
import tiktoken  # For token counting
from jinja2 import Template # For templating enrichments

# --- External Dependencies (Strictly Required) ---
from presidio_analyzer import AnalyzerEngine
from presidio_anonymizer import AnonymizerEngine
try:
    from plantuml import PlantUML
except ImportError:
    PlantUML = None
    logging.warning("PlantUML library not found. Diagram generation in enrichment will be skipped.")

# --- Async/API/CLI Dependencies ---
import aiofiles
import tempfile
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn # For running FastAPI in __main__
import unittest # For tests in __main__

# --- CENTRAL RUNNER FOUNDATION ---
from runner import tracer
from runner.llm_client import call_llm_api, call_ensemble_api
from runner.runner_logging import logger, add_provenance
from runner.runner_metrics import LLM_CALLS_TOTAL, LLM_ERRORS_TOTAL, LLM_LATENCY_SECONDS, LLM_TOKEN_INPUT_TOTAL, LLM_TOKEN_OUTPUT_TOTAL
from runner.runner_errors import LLMError
from runner.runner_file_utils import get_commits # For changelog enrichment
# -----------------------------------

# --- Local Prometheus Metrics (Internal Stats Only) ---
# LLM-related metrics are IMPORTED from the runner.
# These metrics track the internal operations of this specific service.
from prometheus_client import Counter, Histogram, Gauge
process_calls_total = Counter('docgen_validator_calls_total', 'Total validation calls by format and operation', ['format', 'operation'])
process_errors_total = Counter('docgen_validator_errors_total', 'Total validation errors by format, operation, and type', ['format', 'operation', 'error_type'])
process_latency_seconds = Histogram('docgen_validator_latency_seconds', 'Validation latency in seconds by format and operation', ['format', 'operation'])
docgen_compliance_issues_total = Counter('docgen_validator_compliance_issues_total', 'Compliance issues detected during validation', ['format', 'issue_type'])
docgen_security_findings_total = Counter('docgen_validator_security_findings_total', 'Security findings detected during validation', ['format', 'finding_category'])
docgen_content_quality_score = Gauge('docgen_validator_content_quality_score', 'Quality score of processed documentation', ['format', 'metric_type'])
section_status_gauge = Gauge('docgen_response_section_status', 'Status of required sections in docs (1=present, 0=missing)', ['output_format', 'section_name'])

# --- NLTK Data Download (Strictly required data for NLP features) ---
# Use a helper function to avoid polluting global scope
def setup_nltk_data():
    nltk_data_paths = {
        'punkt': 'tokenizers/punkt',
        'stopwords': 'corpora/stopwords',
        'vader_lexicon': 'sentiment/vader_lexicon'
    }
    for name, path in nltk_data_paths.items():
        try:
            nltk.data.find(path)
        except LookupError:
            logger.info(f"NLTK data '{name}' not found. Downloading...")
            nltk.download(name, quiet=True)
setup_nltk_data()


# --- Constants ---
# Merged schema from validator and handler
DEFAULT_SCHEMA = {
    "sections": ["introduction", "installation", "usage", "api_reference", "testing", "safety", "license", "copyright", "conclusion"],
    "order": ["introduction", "installation", "usage", "api_reference", "testing", "safety", "license", "copyright", "conclusion"],
    "min_section_length": 50,
    "min_total_length": 500,
    "languages": ["en"]
}

# Merged patterns from validator and handler
DANGEROUS_CONTENT_PATTERNS = {
    "HardcodedCredentials": r'(?i)(api_key|secret|password|token)\s*=\s*[\'"]?\S+[\'"]?',
    "InsecureProtocolUsage": r'(?i)(http://|ftp://)',
    "DirectRootAccess": r'(?i)^user\s+root\b',
    "SensitiveFilePaths": r'(?i)/etc/passwd|/root/\.ssh|/var/log/secure',
    "ExposedSensitivePorts": r'(?i)expose\s+(21|23|80|443|3389|8080|8443)',
}


# --- FastAPI App Definition (from validator) ---
app = FastAPI(
    title="DocGen Response Validator API",
    description="Unified API for processing, validating, healing, and enriching LLM-generated documentation.",
    version="2.0.0"
)

# --- Request/Response Models for FastAPI (from validator) ---
class ValidationRequest(BaseModel):
    raw_response: Dict[str, Any] # Expecting LLM response format: {'content': '...', 'usage': {...}}
    target_files: Optional[List[str]] = None
    format: str = "md"
    lang: str = "en"
    auto_correct: bool = False
    repo_path: str = "." # Path to the repository for context

class ValidationReportResponse(BaseModel):
    is_valid: bool
    overall_status: str # "passed", "failed", "partially_corrected"
    docs: str # The final, enriched documentation content
    issues: Dict[str, Any]
    suggestions: List[str]
    quality_metrics: Dict[str, float]
    corrected_doc: Optional[str] = None
    provenance: Dict[str, Any]


# --- Security: Sensitive Data Scrubbing (Using Validator's robust version) ---
def scrub_text(text: str) -> str:
    """
    Strictly redacts sensitive information from the text using Presidio.
    Raises RuntimeError if Presidio fails during scrubbing.
    """
    if not text:
        return ""
    
    try:
        analyzer = AnalyzerEngine()
        anonymizer = AnonymizerEngine()
        
        # Comprehensive entity list
        entities = ["PERSON", "EMAIL_ADDRESS", "PHONE_NUMBER", "CREDIT_CARD", "US_SSN", "IP_ADDRESS", "URL", "NRP", "LOCATION"]
        
        results = analyzer.analyze(text=text, entities=entities, language="en")
        anonymized_text = anonymizer.anonymize(text=text, analyzer_results=results, anonymizers={"DEFAULT": {"type": "replace", "new_value": "[REDACTED]"}}).text
        
        return anonymized_text
            
    except Exception as e:
        logger.error(f"Presidio PII/secret scrubbing failed critically: {e}", exc_info=True)
        raise RuntimeError(f"Critical error during sensitive data scrubbing with Presidio: {e}") from e


# --- Unified Plugin System (Based on Validator's `ValidationPlugin`) ---
class DocGenPlugin(ABC):
    """
    Abstract base for documentation format plugins.
    This unified plugin handles normalization, validation, conversion, and suggestions.
    """
    __version__ = "1.0"
    __source__ = "built-in"

    @abstractmethod
    def normalize(self, content: str) -> str:
        """Normalizes raw LLM-generated content into this plugin's base format."""
        pass
    
    @abstractmethod
    def validate(self, content: str, schema: Dict[str, Any]) -> Dict[str, Any]:
        """Validates the content for syntax, structure, and format-specific rules."""
        pass

    @abstractmethod
    def convert(self, content: str, to_format: str) -> str:
        """Converts content from this plugin's base format to another."""
        pass

    @abstractmethod
    def suggest(self, issues: Dict[str, Any]) -> List[str]:
        """Generates concrete suggestions based on validation issues."""
        pass

# --- Concrete Plugins (Using Validator's implementations) ---
class MarkdownPlugin(DocGenPlugin):
    __version__ = "1.2"
    def normalize(self, content: str) -> str:
        if not content: return ""
        try: return pypandoc.convert_text(content, 'md', format='md') 
        except Exception as e: raise ValueError(f"Failed to normalize content to Markdown: {e}")
    def validate(self, content: str, schema: Dict[str, Any]) -> Dict[str, Any]:
        issues = []
        is_valid = True
        if not content.strip(): issues.append("Markdown content is empty."); is_valid = False
        if not re.search(r'^\s*#\s*\S', content, re.M): issues.append("Markdown missing a top-level H1 header."); is_valid = False
        if len(content) < schema.get("min_total_length", 0): issues.append(f"Content too short: {len(content)} characters (min: {schema['min_total_length']})."); is_valid = False
        return {"valid": is_valid, "issues": issues}
    def convert(self, content: str, to_format: str) -> str:
        if to_format == "md": return content
        try: return pypandoc.convert_text(content, to_format, format='md')
        except Exception as e: raise ValueError(f"Failed to convert Markdown to '{to_format}': {e}")
    def suggest(self, issues: Dict[str, Any]) -> List[str]:
        suggestions = []
        for issue in issues.get("issues", []):
            if "heading" in issue.lower(): suggestions.append("Add a top-level Markdown heading (e.g., # Document Title).")
            if "too short" in issue.lower(): suggestions.append("Expand content to meet minimum length requirement.")
        return suggestions

class RSTPlugin(DocGenPlugin):
    __version__ = "1.0"
    def normalize(self, content: str) -> str:
        if not content: return ""
        try: return pypandoc.convert_text(content, 'rst', format='md') 
        except Exception as e: raise ValueError(f"Failed to normalize content to reStructuredText: {e}")
    def validate(self, content: str, schema: Dict[str, Any]) -> Dict[str, Any]:
        issues = []
        is_valid = True
        settings = docutils.core.get_default_settings(docutils.core.publish_string)
        settings.halt_level = 2; settings.warning_stream = []
        try:
            docutils.core.publish_string(source=content, writer_name='html', settings=settings)
            if settings.warning_stream: issues.extend([str(m) for m in settings.warning_stream]); is_valid = False
        except docutils.utils.SystemMessage as e: issues.append(f"RST parsing error: {e.args[0]}"); is_valid = False
        except Exception as e: issues.append(f"Unexpected RST validation error: {e}"); is_valid = False
        if len(content) < schema.get("min_total_length", 0): issues.append(f"Content too short: {len(content)} chars (min: {schema['min_total_length']})."); is_valid = False
        return {"valid": is_valid, "issues": issues}
    def convert(self, content: str, to_format: str) -> str:
        if to_format == "rst": return content
        try: return pypandoc.convert_text(content, to_format, format='rst')
        except Exception as e: raise ValueError(f"Failed to convert reStructuredText to '{to_format}': {e}")
    def suggest(self, issues: Dict[str, Any]) -> List[str]:
        suggestions = []
        for issue in issues.get("issues", []):
            if "RST parsing error" in issue: suggestions.append("Fix reStructuredText syntax errors (e.g., check directives, indentation).")
            if "too short" in issue.lower(): suggestions.append("Expand content to meet minimum length requirement.")
        return suggestions

class HTMLPlugin(DocGenPlugin):
    __version__ = "1.0"
    def normalize(self, content: str) -> str:
        if not content: return ""
        try: return BeautifulSoup(content, 'html.parser').prettify()
        except Exception as e: raise ValueError(f"Failed to normalize HTML content: {e}")
    def validate(self, content: str, schema: Dict[str, Any]) -> Dict[str, Any]:
        issues = []; is_valid = True
        if not content.strip(): issues.append("HTML content is empty."); is_valid = False
        try:
            soup = BeautifulSoup(content, 'html.parser')
            if not soup.find(['html', 'body']): issues.append("HTML content missing basic <html> or <body> tags."); is_valid = False
            if not soup.find(['h1', 'h2', 'h3']): issues.append("HTML content missing any headings (h1, h2, h3)."); is_valid = False
        except Exception as e: issues.append(f"HTML parsing error: {e}"); is_valid = False
        if len(content) < schema.get("min_total_length", 0): issues.append(f"Content too short: {len(content)} chars (min: {schema['min_total_length']})."); is_valid = False
        return {"valid": is_valid, "issues": issues}
    def convert(self, content: str, to_format: str) -> str:
        if to_format == "html": return content
        try: return pypandoc.convert_text(content, to_format, format='html')
        except Exception as e: raise ValueError(f"Failed to convert HTML to '{to_format}': {e}")
    def suggest(self, issues: Dict[str, Any]) -> List[str]:
        suggestions = []
        for issue in issues.get("issues", []):
            if "html" in issue.lower() and "tags" in issue.lower(): suggestions.append("Ensure content includes proper <html> and <body> tags.")
            if "headings" in issue.lower(): suggestions.append("Add HTML headings (e.g., <h1>, <h2>) for structure.")
        return suggestions

# --- Conceptual Plugins (from Validator) ---
class LaTeXPlugin(DocGenPlugin):
    __version__ = "1.0"; __source__ = "conceptual"
    def normalize(self, content: str) -> str: logger.warning("LaTeXPlugin normalize is conceptual."); return content
    def validate(self, content: str, schema: Dict[str, Any]) -> Dict[str, Any]: return {"valid": True, "issues": ["LaTeX validation is conceptual only."]}
    def convert(self, content: str, to_format: str) -> str: raise NotImplementedError
    def suggest(self, issues: Dict[str, Any]) -> List[str]: return []
class DocusaurusPlugin(DocGenPlugin):
    __version__ = "1.0"; __source__ = "conceptual"
    def normalize(self, content: str) -> str: raise NotImplementedError
    def validate(self, content: str, schema: Dict[str, Any]) -> Dict[str, Any]: raise NotImplementedError
    def convert(self, content: str, to_format: str) -> str: raise NotImplementedError
    def suggest(self, issues: Dict[str, Any]) -> List[str]: return []
class SphinxPlugin(DocGenPlugin):
    __version__ = "1.0"; __source__ = "conceptual"
    def normalize(self, content: str) -> str: raise NotImplementedError
    def validate(self, content: str, schema: Dict[str, Any]) -> Dict[str, Any]: raise NotImplementedError
    def convert(self, content: str, to_format: str) -> str: raise NotImplementedError
    def suggest(self, issues: Dict[str, Any]) -> List[str]: return []
class JupyterNotebookPlugin(DocGenPlugin):
    __version__ = "1.0"; __source__ = "conceptual"
    def normalize(self, content: str) -> str: raise NotImplementedError
    def validate(self, content: str, schema: Dict[str, Any]) -> Dict[str, Any]: raise NotImplementedError
    def convert(self, content: str, to_format: str) -> str: raise NotImplementedError
    def suggest(self, issues: Dict[str, Any]) -> List[str]: return []
class MkDocsPlugin(DocGenPlugin):
    __version__ = "1.0"; __source__ = "conceptual"
    def normalize(self, content: str) -> str: raise NotImplementedError
    def validate(self, content: str, schema: Dict[str, Any]) -> Dict[str, Any]: raise NotImplementedError
    def convert(self, content: str, to_format: str) -> str: raise NotImplementedError
    def suggest(self, issues: Dict[str, Any]) -> List[str]: return []


class PluginRegistry(FileSystemEventHandler):
    """
    Unified registry for DocGenPlugins with auto-discovery and hot-reloading.
    (Based on Validator's registry implementation).
    """
    def __init__(self, plugin_dir: str = "./docgen_plugins"):
        super().__init__()
        self.plugins: Dict[str, Type[DocGenPlugin]] = {}
        self.plugin_dir = plugin_dir
        self.observer = Observer()
        self._load_plugins_sync_init()
        self._setup_hot_reload()

    def _load_plugins_sync_init(self):
        """Loads built-in and custom plugins."""
        self.plugins.clear()
        built_in_plugins = {
            'md': MarkdownPlugin, 'rst': RSTPlugin, 'html': HTMLPlugin,
            'latex': LaTeXPlugin, 'docusaurus': DocusaurusPlugin, 'sphinx': SphinxPlugin,
            'ipynb': JupyterNotebookPlugin, 'mkdocs': MkDocsPlugin,
        }
        for name, cls in built_in_plugins.items():
            self.plugins[name] = cls
        
        if not os.path.exists(self.plugin_dir): os.makedirs(self.plugin_dir, exist_ok=True)
        if self.plugin_dir not in sys.path: sys.path.insert(0, self.plugin_dir)

        for file_path in glob.glob(f"{self.plugin_dir}/*_plugin.py"):
            if file_path.endswith('__init__.py'): continue
            module_name_base = Path(file_path).stem
            unique_module_name = f"dynamic_docgen_plugin_{module_name_base}_{uuid.uuid4().hex}"
            spec = importlib.util.spec_from_file_location(unique_module_name, file_path)
            if spec is None or spec.loader is None:
                logger.warning(f"Could not find module spec for docgen plugin: {file_path}")
                continue
            try:
                module = importlib.util.module_from_spec(spec)
                sys.modules[unique_module_name] = module
                spec.loader.exec_module(module)
                for name, obj in vars(module).items():
                    if isinstance(obj, type) and issubclass(obj, DocGenPlugin) and obj != DocGenPlugin:
                        plugin_key = name.lower().replace('plugin', '')
                        self.plugins[plugin_key] = obj
                        logger.info(f"Loaded custom docgen plugin: {plugin_key} from {file_path}.")
            except Exception as e:
                logger.error(f"Failed to load custom docgen plugin from {file_path}: {e}", exc_info=True)
                if unique_module_name in sys.modules: del sys.modules[unique_module_name]
        logger.info(f"DocGen Plugin Registry loaded {len(self.plugins)} plugin classes.")

    async def reload_plugins_async(self):
        """Asynchronously reloads all validation plugins."""
        self._load_plugins_sync_init()
        logger.info("DocGen plugins reloaded due to file system change.")

    def _setup_hot_reload(self):
        """Sets up Watchdog observer."""
        class ReloadHandler(FileSystemEventHandler):
            def __init__(self, registry_instance: 'PluginRegistry'):
                self.registry_instance = registry_instance
            def dispatch(self, event):
                if not event.is_directory and event.src_path.endswith('.py') and event.event_type in ('created', 'modified', 'deleted'):
                    logger.info(f"DocGen plugin file changed: {event.src_path}. Triggering async reload.")
                    asyncio.create_task(self.registry_instance.reload_plugins_async())
        observer = Observer()
        observer.schedule(ReloadHandler(self), self.plugin_dir, recursive=False)
        observer.start()
        logger.info(f"Started hot-reload observer for docgen plugins in: {self.plugin_dir}")

    def get_plugin(self, format_name: str) -> DocGenPlugin:
        """Retrieves an instantiated plugin. Strictly raises ValueError if not found."""
        plugin_class = self.plugins.get(format_name.lower())
        if plugin_class:
            return plugin_class()
        raise ValueError(f"No validation plugin found for format '{format_name}'.")

# Initialize the plugin registry globally
validation_registry = PluginRegistry()


class ResponseValidator:
    """
    MERGED CLASS: Orchestrates the entire response pipeline from handling to validation.
    (Based on ResponseHandler, integrating DocValidator's logic).
    """
    def __init__(self, schema: Optional[Dict[str, Any]] = None):
        self.schema = schema or DEFAULT_SCHEMA
        self.run_id = str(uuid.uuid4())
        self.rationale_steps: List[str] = []
        self.sia = SentimentIntensityAnalyzer()
        self.corrections_log: List[Dict[str, Any]] = []
        self.format: str = "md"

    def add_rationale(self, step: str):
        """Adds a step to the rationale log."""
        self.rationale_steps.append(step)
        add_provenance({"run_id": self.run_id, "rationale_step": step})

    # --- Merged Core Logic Methods (Primarily from Validator) ---

    async def scan_unsafe_content(self, doc_text: str, format_type: str) -> List[Dict[str, str]]:
        """Scans for PII (implicitly) and dangerous content patterns."""
        findings: List[Dict[str, str]] = []
        for name, pattern in DANGEROUS_CONTENT_PATTERNS.items():
            if re.search(pattern, doc_text):
                finding = {"type": "Security_Content_Pattern", "category": name, "description": f"Detected: {name}", "severity": "High"}
                findings.append(finding)
                docgen_security_findings_total.labels(format=format_type, finding_category=f"Content_{name}").inc()
        return findings

    def check_compliance(self, doc_text: str) -> List[Dict[str, str]]:
        """Checks for schema-defined compliance (license, copyright)."""
        issues = []
        license_patterns = [r'MIT License', r'Apache License', r'GNU General Public License', r'BSD License']
        if not any(re.search(pattern, doc_text, re.IGNORECASE) for pattern in license_patterns):
            issues.append({"type": "compliance", "issue": "Missing recognized open-source license statement", "severity": "High"})
            docgen_compliance_issues_total.labels(format=self.format, issue_type='missing_license').inc()
        if not re.search(r'Copyright\s+\(c\)\s+\d{4}\s+[\w\s,.]+', doc_text, re.IGNORECASE):
            issues.append({"type": "compliance", "issue": "Missing copyright notice (e.g., 'Copyright (c) YYYY Owner')", "severity": "High"})
            docgen_compliance_issues_total.labels(format=self.format, issue_type='missing_copyright').inc()
        return issues

    def assess_quality(self, doc_text: str) -> Dict[str, float]:
        """GOAT Upgrade: Assesses content quality using NLP metrics."""
        sentences = sent_tokenize(doc_text); num_sentences = len(sentences)
        words = doc_text.split(); num_words = len(words)
        if num_words == 0: return {"readability": 0.0, "sentiment": 0.0, "coherence": 0.0, "keyword_density": 0.0, "anomaly_score": 0.0, "overall_score": 0.0}

        # Readability (Simplified)
        readability_score = 0.0
        if num_sentences > 0: readability_score = max(0.0, 100.0 - (num_words / num_sentences) * 5)
        
        # Sentiment
        sentiment_scores = self.sia.polarity_scores(doc_text); sentiment_compound_score = sentiment_scores['compound']

        # Coherence
        coherence_score = self.compute_coherence(sentences)

        # Keyword Density
        stop_words = set(stopwords.words('english'))
        meaningful_words = [w.lower() for w in words if w.isalpha() and w.lower() not in stop_words]
        unique_meaningful_words = len(set(meaningful_words))
        keyword_density_score = unique_meaningful_words / num_words if num_words else 0.0

        # Anomaly Detection
        anomaly_score = self.detect_anomalies(doc_text, sentiment_compound_score)
        
        # Overall Score
        overall_score = (readability_score/100 * 0.3 + (sentiment_compound_score + 1)/2 * 0.2 + coherence_score * 0.3 + keyword_density_score * 0.15 + (1 - anomaly_score) * 0.05)
        overall_score = min(1.0, max(0.0, overall_score))

        quality_metrics = {
            "readability": readability_score, "sentiment": sentiment_compound_score,
            "coherence": coherence_score, "keyword_density": keyword_density_score,
            "anomaly_score": anomaly_score, "overall_score": overall_score
        }
        # Update Prometheus Gauges
        for metric_type, value in quality_metrics.items():
            docgen_content_quality_score.labels(format=self.format, metric_type=metric_type).set(value)
            
        return quality_metrics

    def compute_coherence(self, sentences: List[str]) -> float:
        """Helper for assess_quality: computes coherence score."""
        try: from textblob import TextBlob
        except ImportError:
            logger.warning("TextBlob not installed. Coherence score will be 0. Install with `pip install textblob`.")
            return 0.0
        coherence_scores = []
        for i in range(len(sentences) - 1):
            words1 = set(word.lower() for word in TextBlob(sentences[i]).words if word.isalpha())
            words2 = set(word.lower() for word in TextBlob(sentences[i+1]).words if word.isalpha())
            if not words1 and not words2: continue
            overlap = len(words1 & words2); union = len(words1 | words2)
            coherence_scores.append(overlap / union if union else 0)
        return sum(coherence_scores) / len(coherence_scores) if coherence_scores else 0.0

    def detect_anomalies(self, doc_text: str, sentiment: float) -> float:
        """Helper for assess_quality: detects anomalies."""
        negative_words = set(['error', 'fail', 'issue', 'problem', 'bug', 'critical', 'warning'])
        words = doc_text.lower().split()
        negative_keyword_count = sum(1 for word in words if word in negative_words)
        total_words = len(words)
        anomaly_score = 0.0
        if total_words > 0: anomaly_score = negative_keyword_count / total_words
        if sentiment < -0.8: anomaly_score += abs(sentiment) * 0.5
        return min(1.0, anomaly_score)

    def generate_suggestions(self, quality_report: Dict[str, float], all_issues: Dict[str, Any]) -> List[str]:
        """Generates concrete suggestions for improving documentation quality."""
        suggestions = []
        if quality_report.get("completeness", 1.0) < 1.0 or all_issues.get("section_issues", {}).get("missing_sections"):
            suggestions.append("Ensure all required sections are present and clearly titled (e.g., Introduction, Usage, License).")
        if quality_report.get("readability", 100.0) < 50.0:
            suggestions.append("Improve readability by simplifying sentence structure and using clearer language.")
        if all_issues.get("format_issues"):
            suggestions.append(f"Fix format-specific syntax errors: {', '.join(all_issues['format_issues'])}")
        return suggestions

    def detect_sections(self, content: str) -> Dict[str, bool]:
        """Detects presence of REQUIRED_SECTIONS in the content."""
        status = {}
        for section in self.schema.get("sections", REQUIRED_SECTIONS):
            # Check for header-like patterns (Markdown/RST)
            is_present = bool(re.search(rf'(^#{{1,6}}\s*{re.escape(section)}\s*$|^{re.escape(section)}\n[=~\-`^"]{{3,}})', content, re.I | re.M))
            status[section] = is_present
            section_status_gauge.labels(output_format=self.format, section_name=section).set(1 if is_present else 0)
        return status
        
    async def enrich_content_with_context(self, content: str, output_format: str, run_id: str, repo_path: str) -> str:
        """Enriches content with badges, diagrams, links, and changelog."""
        enriched_parts = []
        
        # 1. Add Badges (Placeholder)
        quality_badge_url = "https://img.shields.io/badge/Quality-Pending-yellow.svg"
        security_badge_url = "https://img.shields.io/badge/Security-Pending-yellow.svg"
        enriched_parts.append(f"![Quality Score]({quality_badge_url})\n")
        enriched_parts.append(f"![Security Status]({security_badge_url})\n\n")

        # 2. Add Diagrams (PlantUML)
        if PlantUML:
            plantuml_server_url = os.getenv('PLANTUML_SERVER_URL', 'http://www.plantuml.com/plantuml')
            plantuml_client = PlantUML(plantuml_server_url)
            uml_code = f"@startuml\ntitle {output_format.capitalize()} Flow\nactor User\nparticipant DocGen\nUser -> DocGen : Request\nDocGen -> User : Document\n@enduml"
            try:
                diagram_url = plantuml_client.get_url(uml_code)
                enriched_parts.append(f"## Documentation Flow Diagram\n![Documentation Flow Diagram]({diagram_url})\n\n")
            except Exception as e:
                logger.warning(f"Failed to generate PlantUML diagram: {e}. Skipping.", extra={"run_id": self.run_id})
        else:
            enriched_parts.append("## Documentation Flow Diagram\n_PlantUML library not available. Diagram generation skipped._\n\n")

        # 3. Add Links
        enriched_parts.append(f"## Related Resources\n- [Full Report for Run {self.run_id}](https://your.reports.platform/run/{self.run_id})\n\n")

        # 4. Add Changelog from Git (Using central runner function)
        try:
            # Use imported runner.runner_file_utils.get_commits
            git_log = await get_commits(repo_path, limit=5)
            if git_log and "ERROR" not in git_log and "Failed" not in git_log:
                enriched_parts.append(f"## Recent Changes\n```\n{git_log}\n```\n")
            else:
                enriched_parts.append("## Recent Changes\n_No recent changes found or Git repository not accessible._\n")
        except Exception as e:
            logger.warning(f"Failed to retrieve changelog during enrichment: {e}", extra={"run_id": self.run_id})
            enriched_parts.append("## Recent Changes\n_Failed to retrieve changelog._\n")

        return f"{''.join(enriched_parts)}\n{content}"

    async def auto_correct_via_llm(self, doc_text: str, issues: Dict[str, Any], format_type: str) -> str:
        """
        Consolidated auto-correction function (from validator).
        Uses central runner's call_ensemble_api.
        """
        llm_model_for_correction = "gpt-4o"
        correction_prompt = f"""
        The following documentation content in {format_type} format has issues.
        Detected issues (summary): {json.dumps(issues, indent=2)[:1000]}
        
        Original content:
        ```
        {doc_text}
        ```
        
        Your task is to provide ONLY the corrected, well-formed documentation content in {format_type} format.
        Address all detected issues. Do NOT include any conversational text, explanations, or markdown wrappers.
        """
        
        logger.info(f"Attempting LLM auto-correction for issues in {format_type} doc.", extra={"run_id": self.run_id})
        try:
            start_time = time.time()
            # Use central runner's LLM client
            llm_response = await call_ensemble_api(
                prompt=correction_prompt,
                models=[{"model": llm_model_for_correction}],
                voting_strategy="majority"
            )
            
            # Log metrics using central runner imports
            latency = time.time() - start_time
            LLM_CALLS_TOTAL.labels(provider="docgen_validator", model=llm_model_for_correction).inc()
            LLM_LATENCY_SECONDS.labels(provider="docgen_validator", model=llm_model_for_correction).observe(latency)
            
            corrected_content = llm_response.get('content', '').strip()
            
            if not corrected_content:
                LLM_ERRORS_TOTAL.labels(provider="docgen_validator", model=llm_model_for_correction, error_type="EmptyLLMResponse").inc()
                raise ValueError("LLM returned empty content for auto-correction.")
            
            # Log token usage
            usage = llm_response.get('usage', {})
            if usage.get('input_tokens'):
                LLM_TOKEN_INPUT_TOTAL.labels(provider="docgen_validator", model=llm_model_for_correction).inc(usage['input_tokens'])
            if usage.get('output_tokens'):
                LLM_TOKEN_OUTPUT_TOTAL.labels(provider="docgen_validator", model=llm_model_for_correction).inc(usage['output_tokens'])
            
            # Re-validate the LLM-corrected content
            plugin = validation_registry.get_plugin(format_type)
            re_validation_result = plugin.validate(corrected_content, self.schema)
            
            if not re_validation_result["valid"]:
                LLM_ERRORS_TOTAL.labels(provider="docgen_validator", model=llm_model_for_correction, error_type="InvalidRepairFormat").inc()
                raise ValueError(f"LLM auto-correction produced invalid {format_type} content. Remaining issues: {re_validation_result['issues']}")
                
            self.corrections_log.append({"type": "auto_correct_llm", "issues_fixed": issues, "llm_output_length": len(corrected_content)})
            self.add_rationale(f"Auto-corrected documentation using LLM. Fixed {len(issues.get('issues', []))} issues.")
            add_provenance({"run_id": self.run_id, "action": "auto_correct_llm", "model": llm_model_for_correction, "issues_fixed": len(issues.get('issues', []))})
            return corrected_content
            
        except Exception as e:
            logger.error(f"LLM auto-correction failed: {e}", exc_info=True, extra={"run_id": self.run_id})
            if not isinstance(e, LLMError):
                LLM_ERRORS_TOTAL.labels(provider="docgen_validator", model=llm_model_for_correction, error_type=type(e).__name__).inc()
            raise RuntimeError(f"Critical error during LLM-based auto-correction: {e}") from e

    # --- Main Orchestration Pipeline (Merged Logic) ---

    async def process_and_validate_response(
        self, 
        raw_response: Dict[str, Any], 
        output_format: str = "md", 
        lang: str = "en",
        auto_correct: bool = False,
        repo_path: str = "."
    ) -> Dict[str, Any]:
        """
        Main orchestration method for the merged response/validation pipeline.
        """
        with tracer.start_as_current_span("process_and_validate_response_pipeline") as span:
            self.format = output_format # Set format for class methods
            log_extra = {'run_id': self.run_id, 'format': output_format, 'auto_correct': auto_correct}
            process_calls_total.labels(format=output_format, operation='total_pipeline').inc()
            start_time = time.time()
            logger.info("Documentation response processing and validation started", extra=log_extra)
            span.set_attribute("format", output_format)
            span.set_attribute("auto_correct", auto_correct)
            span.set_attribute("run_id", self.run_id)

            # Reset logs
            self.rationale_steps = []
            self.corrections_log = []

            try:
                llm_generated_content = raw_response.get('content', '')
                if not llm_generated_content:
                    raise ValueError("LLM response 'content' field is empty or missing.")

                # 1. PII/Secret Scrubbing (Strict)
                scrubbed_content = scrub_text(llm_generated_content)
                self.add_rationale("LLM-generated content scrubbed for sensitive data.")

                # 2. Normalize Document Format
                plugin = validation_registry.get_plugin(output_format)
                process_calls_total.labels(format=output_format, operation='normalize').inc()
                start_normalize = time.time()
                normalized_doc = plugin.normalize(scrubbed_content)
                process_latency_seconds.labels(format=output_format, operation='normalize').observe(time.time() - start_normalize)
                self.add_rationale(f"Normalized document to {output_format} using {plugin.__class__.__name__}.")
                span.set_attribute("normalization_successful", True)
                
                current_doc_state = normalized_doc
                
                # 3. Run All Initial Validations
                all_issues: Dict[str, Any] = {
                    "format_issues": [], "section_issues": {}, "compliance_issues": [],
                    "quality_suggestions": [], "unsafe_content_findings": []
                }
                
                # Format/Syntax Validation
                process_calls_total.labels(format=output_format, operation='plugin_validate').inc()
                start_plugin_validate = time.time()
                format_validation_result = plugin.validate(current_doc_state, self.schema)
                all_issues["format_issues"].extend(format_validation_result.get("issues", []))
                process_latency_seconds.labels(format=output_format, operation='plugin_validate').observe(time.time() - start_plugin_validate)
                self.add_rationale(f"{plugin.__class__.__name__} validation run. Valid: {format_validation_result['valid']}.")

                # Section/Schema Validation
                section_presence = self.detect_sections(current_doc_state)
                missing_sections = [s for s in self.schema.get("sections", REQUIRED_SECTIONS) if not section_presence.get(s, False)]
                all_issues["section_issues"] = {"missing_sections": missing_sections} # Simplified from validator
                self.add_rationale(f"Section validation run. Missing: {len(missing_sections)}.")

                # Compliance Validation
                all_issues["compliance_issues"] = self.check_compliance(current_doc_state)
                self.add_rationale(f"Compliance validation run. Issues: {len(all_issues['compliance_issues'])}.")

                # Security Content Scan
                all_issues["unsafe_content_findings"] = await self.scan_unsafe_content(current_doc_state, output_format)
                self.add_rationale(f"Content scanned for unsafe patterns: {len(all_issues['unsafe_content_findings'])} found.")

                # 4. Auto-Correct Document (LLM-based)
                corrected_doc_text = current_doc_state
                issues_found_before_correction = bool(
                    all_issues["format_issues"] or 
                    all_issues["section_issues"]["missing_sections"] or 
                    all_issues["compliance_issues"] or 
                    all_issues["unsafe_content_findings"]
                )
                
                if auto_correct and issues_found_before_correction:
                    process_calls_total.labels(format=output_format, operation='auto_correct').inc()
                    start_auto_correct = time.time()
                    try:
                        # Use the consolidated, runner-based auto_correct method
                        corrected_doc_text = await self.auto_correct_via_llm(current_doc_state, all_issues, output_format)
                        self.add_rationale("Document auto-corrected via LLM.")
                        span.set_attribute("auto_correct_successful", True)
                    except RuntimeError as e:
                        logger.error(f"Auto-correction failed critically: {e}", exc_info=True, extra=log_extra)
                        all_issues["format_issues"].append(f"Auto-correction failed: {e}")
                        span.set_attribute("auto_correct_failed", True)
                        corrected_doc_text = current_doc_state # Revert
                    process_latency_seconds.labels(format=output_format, operation='auto_correct').observe(time.time() - start_auto_correct)
                
                # 5. Final Validation Pass (on corrected text)
                final_validation_result = plugin.validate(corrected_doc_text, self.schema)
                final_section_presence = self.detect_sections(corrected_doc_text)
                final_missing_sections = [s for s in self.schema.get("sections", REQUIRED_SECTIONS) if not final_section_presence.get(s, False)]
                final_compliance_issues = self.check_compliance(corrected_doc_text)
                final_security_findings = await self.scan_unsafe_content(corrected_doc_text, output_format)

                overall_is_valid = (
                    final_validation_result["valid"] and
                    not final_missing_sections and
                    not final_compliance_issues and
                    not final_security_findings
                )
                
                overall_status_text = "passed" if overall_is_valid else "failed"
                if auto_correct and not overall_is_valid and issues_found_before_correction:
                    overall_status_text = "partially_corrected"

                # 6. Assess Quality (NLP metrics) on final, corrected text
                quality_metrics = self.assess_quality(corrected_doc_text)
                self.add_rationale(f"Final quality score computed: {quality_metrics['overall_score']:.2f}.")

                # 7. Generate Suggestions
                final_issues_summary = {
                    "format_issues": final_validation_result["issues"],
                    "section_issues": {"missing_sections": final_missing_sections},
                    "compliance_issues": final_compliance_issues,
                    "unsafe_content_findings": final_security_findings
                }
                suggestions = self.generate_suggestions(quality_metrics, final_issues_summary)

                # 8. Enrich Content
                process_calls_total.labels(format=output_format, operation='enrich').inc()
                start_enrich = time.time()
                enriched_final_output = await self.enrich_content_with_context(
                    corrected_doc_text, output_format, self.run_id, repo_path
                )
                process_latency_seconds.labels(format=output_format, operation='enrich').observe(time.time() - start_enrich)
                self.add_rationale("Content enriched with contextual elements.")
                
                # 9. Build Final Report & Provenance
                provenance = {
                    "validator_run_id": self.run_id,
                    "timestamp_utc": datetime.utcnow().isoformat() + "Z",
                    "schema_used": self.schema,
                    "original_format": output_format,
                    "corrections_log": self.corrections_log,
                    "rationale_steps": self.rationale_steps,
                    "auto_correct_enabled": auto_correct
                }
                add_provenance(provenance) # Log final provenance to central logger

                total_latency = time.time() - start_time
                process_latency_seconds.labels(format=output_format, operation='total').observe(total_latency)
                span.set_status(Status(StatusCode.OK if overall_is_valid else StatusCode.ERROR, "Validation completed."))
                logger.info("Documentation response processing completed", extra={**log_extra, 'overall_valid': overall_is_valid, 'total_latency': total_latency})

                # Return the FastAPI response model structure
                return ValidationReportResponse(
                    is_valid=overall_is_valid,
                    overall_status=overall_status_text,
                    docs=enriched_final_output, # Final user-facing doc
                    issues=final_issues_summary,
                    suggestions=suggestions,
                    quality_metrics=quality_metrics,
                    corrected_doc=corrected_doc_text if auto_correct else None,
                    provenance=provenance
                ).model_dump() # Convert to dict for JSON serialization
            
            except Exception as e:
                error_type = str(type(e).__name__)
                process_errors_total.labels(format=output_format, error_stage='pipeline', error_type=error_type).inc()
                logger.error(f"Documentation validation pipeline failed: {e}", exc_info=True, extra=log_extra)
                span.set_status(Status(StatusCode.ERROR, f"Validation pipeline failed: {e}"))
                span.record_exception(e)
                raise HTTPException(status_code=500, detail=f"Validation failed: {str(e)}")


# --- API Endpoint (from Validator, adapted for merged logic) ---
@app.post("/process_and_validate", response_model=ValidationReportResponse)
async def process_validate_api(request: ValidationRequest):
    """
    API endpoint to process, validate, and enrich documentation.
    """
    validator_instance = ResponseValidator(schema=DEFAULT_SCHEMA)
    try:
        report_dict = await validator_instance.process_and_validate_response(
            raw_response=request.raw_response,
            output_format=request.format,
            lang=request.lang,
            auto_correct=request.auto_correct,
            repo_path=request.repo_path
        )
        return report_dict # Return the dict which conforms to ValidationReportResponse
    except HTTPException as e:
        raise e # Re-raise FastAPI exceptions
    except Exception as e:
        logger.error(f"API validation request failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Validation failed: {str(e)}")


# --- CLI Execution and Test Harness (Merged) ---
if __name__ == "__main__":
    import argparse
    import sys
    import unittest.mock

    # --- Setup for local testing/CLI demonstration ---
    test_repo_path = "temp_docgen_validator_merged"
    if not os.path.exists("docgen_plugins"): os.makedirs("docgen_plugins")
    if not os.path.exists(test_repo_path):
        os.makedirs(test_repo_path)
        try:
            subprocess.run(["git", "init"], cwd=test_repo_path, check=True, capture_output=True, text=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=test_repo_path, check=True, capture_output=True, text=True)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=test_repo_path, check=True, capture_output=True, text=True)
            (Path(test_repo_path) / "sample.py").write_text("print('hello')")
            subprocess.run(["git", "add", "."], cwd=test_repo_path, check=True, capture_output=True, text=True)
            subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=test_repo_path, check=True, capture_output=True, text=True)
            print(f"Initialized git repo in {test_repo_path}.")
        except Exception as e:
            print(f"Git init failed (is git installed?): {e}")
    
    parser = argparse.ArgumentParser(description="Documentation Response Validator CLI and API Server")
    parser.add_argument("--raw_response_file", help="Path to a file containing raw LLM response (as JSON: {'content': '...'})")
    parser.add_argument("--format", default="md", help="Format of the documentation.")
    parser.add_argument("--auto_correct", action="store_true", help="Enable LLM-based auto-correction.")
    parser.add_argument("--repo_path", default=test_repo_path, help="Path to the repository for context.")
    parser.add_argument("--server", action="store_true", help="Start the FastAPI server.")
    parser.add_argument("--host", default="0.0.0.0", help="Host for the API server.")
    parser.add_argument("--port", type=int, default=8084, help="Port for the API server (merged port).")
    parser.add_argument("--test", action="store_true", help="Run unit tests.")
    args = parser.parse_args()

    # --- Unit Tests (Merged) ---
    class TestDocgenResponseValidator(unittest.TestCase):
        
        def setUp(self):
            # Mock the central runner functions used by the class
            self.mock_call_ensemble_api = unittest.mock.AsyncMock()
            self.mock_get_commits = unittest.mock.AsyncMock()
            
            # Patch the imported runner functions
            self.patch_call_ensemble = unittest.mock.patch('docgen_response_validator.call_ensemble_api', self.mock_call_ensemble_api)
            self.patch_get_commits = unittest.mock.patch('docgen_response_validator.get_commits', self.mock_get_commits)
            
            self.patch_call_ensemble.start()
            self.patch_get_commits.start()

            # Mock implementations
            self.mock_call_ensemble_api.return_value = {"content": "# Fixed Title\n\nThis is [VALID] and corrected.", "usage": {"input_tokens": 10, "output_tokens": 10}}
            self.mock_get_commits.return_value = "abc1234 - Initial commit"
            
            self.validator = ResponseValidator(schema=DEFAULT_SCHEMA)

        def tearDown(self):
            self.patch_call_ensemble.stop()
            self.patch_get_commits.stop()

        def _run_async_test(self, coro):
            return asyncio.run(coro)

        def test_scrub_text_presidio_strict(self):
            test_text = "Secret: sk-123. Email: user@example.com."
            scrubbed = scrub_text(test_text)
            self.assertIn('[REDACTED]', scrubbed)
            self.assertNotIn('sk-123', scrubbed)

        def test_plugin_registry_strict_get_plugin_fail(self):
            registry = PluginRegistry()
            with self.assertRaisesRegex(ValueError, "No validation plugin found for 'nonexistent_format'"):
                registry.get_plugin('nonexistent_format')

        def test_markdown_plugin_validation_success(self):
            plugin = MarkdownPlugin()
            validation = plugin.validate("# Title\nContent is long enough for this.", {"min_total_length": 10})
            self.assertTrue(validation["valid"])

        def test_assess_quality_goat_upgrade(self):
            """Test the GOAT NLP quality assessment."""
            doc = "# Title\nThis is good. This is clear. This is coherent."
            quality = self.validator.assess_quality(doc)
            self.assertTrue(quality['overall_score'] > 0)
            self.assertTrue(quality['readability'] > 0)
            self.assertTrue(quality['sentiment'] >= 0) # Neutral or positive
            self.assertTrue(quality['coherence'] > 0) # Should have some coherence

        def test_process_and_validate_success(self):
            """Test the full pipeline success case."""
            raw_response = {"content": "# Title\n\n## Introduction\nIntro text.\n\n## Installation\nInstall text.\n\n## Usage\nUsage text.\n\n## API_Reference\nAPI text.\n\n## Testing\nTest text.\n\n## Safety\nSafety text.\n\n## License\nMIT License.\n\n## Copyright\nCopyright (c) 2025 Me.\n\n## Conclusion\nConclusion text."}
            
            async def run_test():
                result = await self.validator.process_and_validate_response(
                    raw_response, "md", auto_correct=False, repo_path=test_repo_path
                )
                self.assertTrue(result['is_valid'])
                self.assertEqual(result['overall_status'], 'passed')
                self.assertIn('## Recent Changes', result['docs']) # Enrichment check
                self.assertIn('abc1234', result['docs']) # get_commits mock
            self._run_async_test(run_test())

        def test_process_and_validate_autocorrect_llm_call(self):
            """Test that auto_correct triggers the (mocked) LLM call."""
            raw_response = {"content": "# Title\n\nThis is missing sections."} # Will fail validation
            
            async def run_test():
                result = await self.validator.process_and_validate_response(
                    raw_response, "md", auto_correct=True, repo_path=test_repo_path
                )
                # Check that the mocked LLM was called
                self.mock_call_ensemble_api.assert_called_once()
                # Check that the pipeline used the LLM's fixed content
                self.assertIn("# Fixed Title", result['docs'])
                self.assertTrue(result['is_valid']) # Mock returns valid content
                self.assertIn("Auto-corrected document via LLM", result['provenance']['rationale_steps'])
            self._run_async_test(run_test())

    # --- CLI Execution ---
    if args.server:
        logger.info(f"Starting FastAPI server on {args.host}:{args.port}...")
        uvicorn.run(app, host=args.host, port=args.port)
    
    elif args.test:
        logger.info("Running unit tests...")
        suite = unittest.TestSuite()
        suite.addTest(unittest.makeSuite(TestDocgenResponseValidator))
        runner = unittest.TextTestRunner(verbosity=2)
        runner.run(suite)
    
    else:
        # Run in CLI mode for a single validation
        if not args.raw_response_file:
            parser.error("--raw_response_file is required for CLI mode.")
        
        try:
            with open(args.raw_response_file, 'r', encoding='utf-8') as f:
                raw_response_data = json.load(f)
        except Exception as e:
            logger.error(f"Failed to read raw response file {args.raw_response_file}: {e}")
            sys.exit(1)

        async def run_cli_mode():
            validator_instance = ResponseValidator(schema=DEFAULT_SCHEMA)
            try:
                report_result = await validator_instance.process_and_validate_response(
                    raw_response=raw_response_data,
                    output_format=args.format,
                    auto_correct=args.auto_correct,
                    repo_path=args.repo_path
                )
                print("\n--- Documentation Validation Report ---")
                print(json.dumps(report_result, indent=2))
                
                if not report_result.get('is_valid', False):
                    sys.exit(1) # Exit with error if validation failed
            except Exception as e:
                logger.critical(f"CLI validation failed: {e}", exc_info=True)
                sys.exit(1)

        asyncio.run(run_cli_mode())