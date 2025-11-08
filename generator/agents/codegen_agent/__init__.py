# agents/codegen_agent/__init__.py
"""
CodeGen Agent — Unified Interface

All LLM calls go through runner.llm_client.
No local backends, no duplicated utilities.
"""

from .codegen_agent import (
    # Configuration
    CodeGenConfig,

    # Feedback Stores
    FeedbackStore,
    SQLiteFeedbackStore,
    RedisFeedbackStore,

    # Main Functions
    generate_code,
    # REMOVED: perform_security_scans (logic is now internal to generate_code)
    hitl_review,

    # Exceptions
    EnsembleGenerationError,

    # Metrics
    CODEGEN_COUNTER,
    CODEGEN_SECURITY_FINDINGS,
    CODEGEN_LATENCY,
    CODEGEN_ERRORS,
    HITL_APPROVAL_RATE,

    # Observability
    tracer,
)

# Prompt & Response
from .codegen_prompt import build_code_generation_prompt
from .codegen_response_handler import (
    parse_llm_response,
    add_traceability_comments,
    # NOTE: monitor_and_scan_code is effectively replaced by runner.runner_security_utils.scan_for_vulnerabilities
    # but kept here for signature compatibility with the agent ecosystem.
    monitor_and_scan_code
)

# Unified LLM Client (from runner)
from runner.llm_client import (
    call_llm_api,
    # NOTE: Assuming this now exists in runner.llm_client post-refactor
    call_ensemble_api,
    shutdown_llm_client
)

__all__ = [
    # Agent Core
    'CodeGenConfig',
    'FeedbackStore', 'SQLiteFeedbackStore', 'RedisFeedbackStore',
    'generate_code', 
    'hitl_review',
    'EnsembleGenerationError',
    'CODEGEN_COUNTER', 'CODEGEN_SECURITY_FINDINGS', 'CODEGEN_LATENCY',
    'CODEGEN_ERRORS', 'HITL_APPROVAL_RATE', 'tracer',

    # Workflow
    'build_code_generation_prompt',
    'parse_llm_response',
    'add_traceability_comments',
    'monitor_and_scan_code', # Kept for external compatibility

    # Unified LLM
    'call_llm_api',
    'call_ensemble_api',
    'shutdown_llm_client'
]