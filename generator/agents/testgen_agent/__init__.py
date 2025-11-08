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
"""

from .testgen_agent import TestGenAgent
from .testgen_prompt import build_testgen_prompt
from .testgen_response_handler import parse_testgen_response, handle_testgen_response
from .testgen_validator import (
    validate_test_quality,
    CoverageValidator,
    MutationValidator,
    PropertyBasedValidator,
    StressPerformanceValidator,
)

__all__ = [
    "TestGenAgent",
    "build_testgen_prompt",
    "parse_testgen_response",
    "handle_testgen_response",
    "validate_test_quality",
    "CoverageValidator",
    "MutationValidator",
    "PropertyBasedValidator",
    "StressPerformanceValidator",
]
