"""
TestGen Agent Package
=====================

Orchestrates test generation, validation, and refinement using the runner
infrastructure for LLM calls, metrics, and provenance tracking.

Modules:
--------
- testgen_agent.py            → Orchestrates end-to-end test generation.
- testgen_prompt.py           → Builds LLM prompt context.
- testgen_response_handler.py → Parses and repairs LLM responses.
- testgen_validator.py        → Executes validation, coverage, and stress testing.

Each submodule integrates with:
- runner.runner_logging       → Unified structured logging and provenance.
- runner.llm_client           → Async LLM orchestration (OpenAI, Anthropic, local).
- runner.runner_metrics       → Prometheus / OTEL-compatible instrumentation.
- runner.tracer               → Distributed tracing support.

FIXED: This version uses relative imports and proper error handling to work
both as a standalone package and as part of a larger project.
"""

# Import main classes and functions with error handling
__all__ = []

try:
    from .testgen_agent import TestGenAgent, Policy, validate_policy

    __all__.extend(["TestGenAgent", "Policy", "validate_policy"])
except ImportError as e:
    import warnings

    warnings.warn(f"Could not import testgen_agent components: {e}")

try:
    from .testgen_prompt import build_agentic_prompt, initialize_codebase_for_rag

    __all__.extend(["build_agentic_prompt", "initialize_codebase_for_rag"])
except ImportError as e:
    import warnings

    warnings.warn(f"Could not import testgen_prompt components: {e}")

try:
    from .testgen_response_handler import parse_llm_response, handle_testgen_response

    __all__.extend(["parse_llm_response", "handle_testgen_response"])
except ImportError as e:
    import warnings

    warnings.warn(f"Could not import testgen_response_handler components: {e}")

try:
    from .testgen_validator import (
        validate_test_quality,
        CoverageValidator,
        MutationValidator,
        PropertyBasedValidator,
        StressPerformanceValidator,
    )

    __all__.extend(
        [
            "validate_test_quality",
            "CoverageValidator",
            "MutationValidator",
            "PropertyBasedValidator",
            "StressPerformanceValidator",
        ]
    )
except ImportError as e:
    import warnings

    warnings.warn(f"Could not import testgen_validator components: {e}")

# Package metadata
__version__ = "1.0.0"
__author__ = "TestGen Agent Team"
__description__ = (
    "Intelligent test generation agent with validation and refinement capabilities"
)

# If no imports succeeded, provide helpful error message
if not __all__:
    import warnings

    warnings.warn(
        "No testgen_agent components could be imported. "
        "Please ensure all dependencies are installed and the runner foundation is available."
    )
