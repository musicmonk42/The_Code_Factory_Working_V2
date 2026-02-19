# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

from importlib import import_module
from typing import Any

_LAZY_IMPORTS = {
    "is_llm_policy_circuit_breaker_open": (
        "self_fixing_engineer.arbiter.policy.circuit_breaker",
        "is_llm_policy_circuit_breaker_open",
    ),
    "record_llm_policy_api_failure": (
        "self_fixing_engineer.arbiter.policy.circuit_breaker",
        "record_llm_policy_api_failure",
    ),
    "record_llm_policy_api_success": (
        "self_fixing_engineer.arbiter.policy.circuit_breaker",
        "record_llm_policy_api_success",
    ),
    "ArbiterConfig": (
        "self_fixing_engineer.arbiter.policy.config",
        "ArbiterConfig",
    ),
    "BasicDecisionOptimizer": (
        "self_fixing_engineer.arbiter.policy.core",
        "BasicDecisionOptimizer",
    ),
    "PolicyEngine": ("self_fixing_engineer.arbiter.policy.core", "PolicyEngine"),
    "SQLiteClient": ("self_fixing_engineer.arbiter.policy.core", "SQLiteClient"),
    "get_policy_engine_instance": (
        "self_fixing_engineer.arbiter.policy.core",
        "get_policy_engine_instance",
    ),
    "initialize_policy_engine": (
        "self_fixing_engineer.arbiter.policy.core",
        "initialize_policy_engine",
    ),
    "reset_policy_engine": (
        "self_fixing_engineer.arbiter.policy.core",
        "reset_policy_engine",
    ),
    "should_auto_learn": (
        "self_fixing_engineer.arbiter.policy.core",
        "should_auto_learn",
    ),
    "LLM_CALL_LATENCY": (
        "self_fixing_engineer.arbiter.policy.metrics",
        "LLM_CALL_LATENCY",
    ),
    "feedback_processing_time": (
        "self_fixing_engineer.arbiter.policy.metrics",
        "feedback_processing_time",
    ),
    "get_or_create_metric": (
        "self_fixing_engineer.arbiter.policy.metrics",
        "get_or_create_metric",
    ),
    "policy_decision_total": (
        "self_fixing_engineer.arbiter.policy.metrics",
        "policy_decision_total",
    ),
    "policy_file_reload_count": (
        "self_fixing_engineer.arbiter.policy.metrics",
        "policy_file_reload_count",
    ),
    "policy_last_reload_timestamp": (
        "self_fixing_engineer.arbiter.policy.metrics",
        "policy_last_reload_timestamp",
    ),
}

__all__ = list(_LAZY_IMPORTS.keys())


def __getattr__(name: str) -> Any:
    if name in _LAZY_IMPORTS:
        module_name, attr_name = _LAZY_IMPORTS[name]
        module = import_module(module_name)
        value = getattr(module, attr_name)
        globals()[name] = value
        return value
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
