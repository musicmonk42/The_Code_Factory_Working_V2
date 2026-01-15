"""
Intent Parser package entry point.

This module provides structured parsing of unstructured requirements documents
(README files, requirement docs, issue tickets) into machine-readable specifications.

IMPORTANT: Type Identity Note
-----------------------------
This module uses explicit re-exports instead of sys.modules aliasing to avoid
type identity issues. When importing, always use the canonical import path:

    from generator.intent_parser.intent_parser import IntentParser

Legacy imports (e.g., 'import intent_parser') are NOT supported and will result
in ImportError. This ensures isinstance() checks work correctly across all
import paths.

Example Usage:
    from generator.intent_parser import IntentParser, IntentParserConfig
    
    parser = IntentParser(config_path="intent_parser.yaml")
    result = await parser.parse(content="# My README")
"""

# Explicit re-exports for public API - ensures type identity consistency
# This replaces the problematic sys.modules hack that caused isinstance() failures
from generator.intent_parser.intent_parser import (
    # Main classes
    IntentParser,
    IntentParserConfig,
    LLMConfig,
    MultiLanguageSupportConfig,
    # Parser strategies
    ParserStrategy,
    MarkdownStrategy,
    RSTStrategy,
    PlaintextStrategy,
    YAMLStrategy,
    PDFStrategy,
    # Extractor strategies
    ExtractorStrategy,
    RegexExtractor,
    NLPExtractor,
    # Detector strategies
    AmbiguityDetectorStrategy,
    LLMDetector,
    # Summarizer strategies
    SummarizerStrategy,
    LLMSummarizer,
    TruncateSummarizer,
    # Supporting classes
    LLMClient,
    FeedbackLoop,
    # Utility functions
    generate_provenance,
    # Lazy loaders (for advanced usage)
    get_spacy,
    get_torch,
    get_transformers,
)

# Define public API explicitly
__all__ = [
    # Main classes
    "IntentParser",
    "IntentParserConfig",
    "LLMConfig",
    "MultiLanguageSupportConfig",
    # Parser strategies
    "ParserStrategy",
    "MarkdownStrategy",
    "RSTStrategy",
    "PlaintextStrategy",
    "YAMLStrategy",
    "PDFStrategy",
    # Extractor strategies
    "ExtractorStrategy",
    "RegexExtractor",
    "NLPExtractor",
    # Detector strategies
    "AmbiguityDetectorStrategy",
    "LLMDetector",
    # Summarizer strategies
    "SummarizerStrategy",
    "LLMSummarizer",
    "TruncateSummarizer",
    # Supporting classes
    "LLMClient",
    "FeedbackLoop",
    # Utility functions
    "generate_provenance",
    # Lazy loaders
    "get_spacy",
    "get_torch",
    "get_transformers",
]
