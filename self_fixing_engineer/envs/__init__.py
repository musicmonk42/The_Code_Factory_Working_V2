# envs/__init__.py
"""
Enhanced Code Health and Evolution Modules
"""

# Try to import gymnasium-dependent modules, use fallbacks if not available
try:
    from .code_health_env import (
        CodeHealthEnv,
        EnvironmentConfig,
        SystemMetrics,
        ActionType,
        AsyncActionExecutor,
    )

    CODE_HEALTH_ENV_AVAILABLE = True
except ImportError as e:
    import logging

    logging.warning(f"Code health environment not available: {e}. Using mock classes.")
    CODE_HEALTH_ENV_AVAILABLE = False

    # Provide mock classes
    class CodeHealthEnv:
        pass

    class EnvironmentConfig:
        pass

    class SystemMetrics:
        pass

    class ActionType:
        pass

    class AsyncActionExecutor:
        pass


# The evolution module no longer exports evolve_configs directly
# It now uses the GeneticOptimizer class
try:
    from .evolution import (
        GeneticOptimizer,
        ConfigurationSpace,
        EvolutionConfig,
        FitnessEvaluator,
        run_test_evaluation,
        DEAP_AVAILABLE,
    )

    EVOLUTION_AVAILABLE = True
except ImportError as e:
    import logging

    logging.warning(f"Evolution module not available: {e}. Using mock classes.")
    EVOLUTION_AVAILABLE = False
    DEAP_AVAILABLE = False

    # Provide mock classes
    class GeneticOptimizer:
        pass

    class ConfigurationSpace:
        pass

    class EvolutionConfig:
        pass

    class FitnessEvaluator:
        pass

    def run_test_evaluation(*args, **kwargs):
        pass


__all__ = [
    # From code_health_env
    "CodeHealthEnv",
    "EnvironmentConfig",
    "SystemMetrics",
    "ActionType",
    "AsyncActionExecutor",
    "CODE_HEALTH_ENV_AVAILABLE",
    # From evolution
    "GeneticOptimizer",
    "ConfigurationSpace",
    "EvolutionConfig",
    "FitnessEvaluator",
    "run_test_evaluation",
    "DEAP_AVAILABLE",
    "EVOLUTION_AVAILABLE",
]
