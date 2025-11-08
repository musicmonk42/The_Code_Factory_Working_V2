# envs/__init__.py
"""
Enhanced Code Health and Evolution Modules
"""

from .code_health_env import (
    CodeHealthEnv,
    EnvironmentConfig,
    SystemMetrics,
    ActionType,
    AsyncActionExecutor
)

# The evolution module no longer exports evolve_configs directly
# It now uses the GeneticOptimizer class
from .evolution import (
    GeneticOptimizer,
    ConfigurationSpace,
    EvolutionConfig,
    FitnessEvaluator,
    run_test_evaluation,
    DEAP_AVAILABLE
)

__all__ = [
    # From code_health_env
    'CodeHealthEnv',
    'EnvironmentConfig',
    'SystemMetrics',
    'ActionType',
    'AsyncActionExecutor',
    
    # From evolution
    'GeneticOptimizer',
    'ConfigurationSpace',
    'EvolutionConfig',
    'FitnessEvaluator',
    'run_test_evaluation',
    'DEAP_AVAILABLE'
]