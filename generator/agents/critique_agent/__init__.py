# agents/critique_agent/__init__.py
"""
Critique Agent — Unified Interface

Exposes core functions, config, and metrics for the Generator Orchestrator.
All core services (LLM, Security, Execution) now delegate to the runner package.
"""

# --- 1. Imports from Core Orchestration (critique_agent.py) ---
from .critique_agent import (
    # Main Agent Function
    orchestrate_critique_pipeline,
    # Configuration and Base Classes
    CritiqueConfig,
    LanguageCritiquePlugin,
    get_plugin,
    # Metrics (must be defined in critique_agent.py before exporting)
    CRITIQUE_STEPS,
    CRITIQUE_LATENCY,
    CRITIQUE_ERRORS,
    CRITIQUE_COVERAGE,
    CRITIQUE_VULNERABILITIES_FOUND,
    tracer,
)

# --- 2. Imports from Prompt Builder (critique_prompt.py) ---
from .critique_prompt import build_semantic_critique_prompt, PromptConfig

# --- 3. Imports from Linter/Tooling (critique_linter.py) ---
from .critique_linter import run_all_lints_and_checks, LINTER_CONFIG

# --- 4. Imports from Fixer (critique_fixer.py) ---
from .critique_fixer import (
    apply_auto_fixes,
    commit_fixes_to_git,
    FixStrategy,
    DiffPatchStrategy,
    RegexStrategy,
    LLMGenerateStrategy,
)

# --- 5. Unified LLM Client (from runner) ---
from runner.llm_client import call_llm_api, call_ensemble_api, shutdown_llm_client

__all__ = [
    # Main Orchestration
    "orchestrate_critique_pipeline",
    "get_plugin",
    # Configuration
    "CritiqueConfig",
    "PromptConfig",
    "LINTER_CONFIG",
    # Core Functions
    "build_semantic_critique_prompt",
    "run_all_lints_and_checks",
    "apply_auto_fixes",
    "commit_fixes_to_git",
    "LanguageCritiquePlugin",
    # Fix Strategies
    "FixStrategy",
    "DiffPatchStrategy",
    "RegexStrategy",
    "LLMGenerateStrategy",
    # Metrics and Observability
    "CRITIQUE_STEPS",
    "CRITIQUE_LATENCY",
    "CRITIQUE_ERRORS",
    "CRITIQUE_COVERAGE",
    "CRITIQUE_VULNERABILITIES_FOUND",
    "tracer",
    # Unified LLM
    "call_llm_api",
    "call_ensemble_api",
    "shutdown_llm_client",
]
