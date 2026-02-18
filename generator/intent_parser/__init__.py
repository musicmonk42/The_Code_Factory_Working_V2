# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Intent Parser package entry point.

This module provides the Intent Parser functionality for extracting structured
requirements from unstructured natural language documents (READMEs, specs, etc.).

REMOVED: sys.modules hack that caused type identity issues with isinstance() checks.
All imports should now use the canonical path: generator.intent_parser
"""

# Lazy imports to avoid loading heavy dependencies during package import
# Import only when explicitly requested to support test mocking
__all__ = [
    # Main parser class
    "IntentParser",
    # Configuration models
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
    # Utility classes
    "LLMClient",
    "FeedbackLoop",
    # Utility functions
    "generate_provenance",
    "get_spacy",
    "get_torch",
    "get_transformers",
    # Spec Block support
    "SpecBlock",
    "InterfacesSpec",
    "extract_spec_block",
    "extract_spec_blocks_all",
    # Question Loop
    "Question",
    "QuestionResponse",
    "SpecLock",
    "generate_questions",
    "run_question_loop",
]


def __getattr__(name):
    """Lazy import mechanism to avoid loading heavy dependencies at package import time."""
    if name in __all__:
        # Spec block and question loop imports
        if name in ["SpecBlock", "InterfacesSpec", "extract_spec_block", "extract_spec_blocks_all"]:
            from generator.intent_parser import spec_block
            return getattr(spec_block, name)
        elif name in ["Question", "QuestionResponse", "SpecLock", "generate_questions", "run_question_loop"]:
            from generator.intent_parser import question_loop
            return getattr(question_loop, name)
        else:
            from generator.intent_parser import intent_parser
            return getattr(intent_parser, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
