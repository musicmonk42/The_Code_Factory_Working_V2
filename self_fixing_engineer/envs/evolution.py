"""
Enhanced Genetic Algorithm Configuration Optimizer
This module provides a production-ready genetic algorithm for optimizing
system configurations with proper sandboxing, caching, and error handling.
"""

import sys
import numpy as np
import logging
import subprocess
import json
import time
import os
import tempfile
import hashlib
from typing import Dict, Any, Optional, Tuple, List, Callable
from dataclasses import dataclass, field, asdict
import threading
from queue import Queue, Empty
import pickle

# Configure module logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Try to import DEAP
try:
    from deap import base, creator, tools, algorithms
    DEAP_AVAILABLE = True
except ImportError:
    DEAP_AVAILABLE = False
    logger.warning("DEAP library not available. Install with: pip install deap")


@dataclass
class ConfigurationSpace:
    """Define the configuration search space"""
    parameters: Dict[str, Dict[str, Any]] = field(default_factory=lambda: {
        "max_connections": {"min": 50, "max": 1000, "type": "int"},
        "timeout_sec": {"min": 1.0, "max": 10.0, "type": "float"},
        "retry_count": {"min": 1, "max": 5, "type": "int"},
        "cache_size": {"min": 100, "max": 10000, "type": "int"},
        "alert_threshold": {"min": 0.1, "max": 1.0, "type": "float"},
        "batch_size": {"min": 1, "max": 100, "type": "int"},
        "worker_threads": {"min": 1, "max": 20, "type": "int"},
        "feature_flags": {"min": 0, "max": 1, "type": "bool", "count": 3}
    })
    
    @property
    def gene_count(self) -> int:
        """Calculate total number of genes needed"""
        count = 0
        for param_info in self.parameters.values():
            count += param_info.get("count", 1)
        return count
    
    def validate(self) -> None:
        """Validate configuration space definition"""
        for param_name, param_info in self.parameters.items():
            if "min" not in param_info or "max" not in param_info:
                raise ValueError(f"Parameter {param_name} missing min/max bounds")
            if "type" not in param_info:
                raise ValueError(f"Parameter {param_name} missing type specification")
            if param_info["min"] >= param_info["max"]:
                raise ValueError(f"Parameter {param_name} has invalid bounds")


@dataclass
class EvolutionConfig:
    """Configuration for the evolution process"""
    generations: int = 10
    population_size: int = 20
    crossover_probability: float = 0.7
    mutation_probability: float = 0.2
    mutation_sigma: float = 0.2
    tournament_size: int = 3
    elite_size: int = 2
    timeout_seconds: int = 30
    max_parallel_evaluations: int = 4
    cache_evaluations: bool = True
    early_stopping_generations: int = 5
    early_stopping_threshold: float = 0.01
    sandbox_evaluation: bool = True
    
    # Reward weights for fitness calculation
    reward_weights: Dict[str, float] = field(default_factory=lambda: {
        "pass_rate": 100.0,
        "latency": -10.0,
        "alerts": -50.0,
        "errors": -100.0,
        "throughput": 5.0
    })
    
    def validate(self) -> None:
        """Validate evolution configuration"""
        if self.generations <= 0:
            raise ValueError("generations must be positive")
        if self.population_size <= 0:
            raise ValueError("population_size must be positive")
        if not 0 <= self.crossover_probability <= 1:
            raise ValueError("crossover_probability must be between 0 and 1")
        if not 0 <= self.mutation_probability <= 1:
            raise ValueError("mutation_probability must be between 0 and 1")
        if self.tournament_size > self.population_size:
            raise ValueError("tournament_size cannot exceed population_size")
        if self.elite_size >= self.population_size:
            raise ValueError("elite_size must be less than population_size")


class FitnessEvaluator:
    """Handles fitness evaluation with caching and sandboxing"""
    
    def __init__(self, config: EvolutionConfig, test_function: Optional[Callable] = None):
        self.config = config
        self.test_function = test_function
        self.evaluation_cache = {}
        self.evaluation_count = 0
        self._cache_lock = threading.Lock()
        
        # Set up subprocess pool for parallel evaluation
        self.evaluation_queue = Queue()
        self.result_queue = Queue()
        self.workers = []
        
        if config.max_parallel_evaluations > 1:
            self._start_worker_pool()
    
    def _start_worker_pool(self):
        """Start worker threads for parallel fitness evaluation"""
        for i in range(self.config.max_parallel_evaluations):
            worker = threading.Thread(
                target=self._evaluation_worker,
                daemon=True,
                name=f"EvalWorker-{i}"
            )
            worker.start()
            self.workers.append(worker)
    
    def _evaluation_worker(self):
        """Worker thread for fitness evaluation"""
        while True:
            try:
                individual, callback = self.evaluation_queue.get(timeout=1)
                if individual is None:  # Shutdown signal
                    break
                
                fitness = self.evaluate_single(individual)
                self.result_queue.put((individual, fitness, callback))
            except Empty:
                continue
            except Exception as e:
                logger.error(f"Worker error: {e}")
                self.result_queue.put((individual, (-1000.0,), callback))
    
    def evaluate_single(self, individual: List[float]) -> Tuple[float]:
        """Evaluate a single individual's fitness"""
        # Check cache
        if self.config.cache_evaluations:
            cache_key = self._get_cache_key(individual)
            with self._cache_lock:
                if cache_key in self.evaluation_cache:
                    logger.debug(f"Cache hit for individual {cache_key[:8]}...")
                    return self.evaluation_cache[cache_key]
        
        self.evaluation_count += 1
        
        # Use provided test function or sandboxed evaluation
        if self.test_function:
            fitness = self._evaluate_with_function(individual)
        elif self.config.sandbox_evaluation:
            fitness = self._evaluate_sandboxed(individual)
        else:
            fitness = self._evaluate_heuristic(individual)
        
        # Cache result
        if self.config.cache_evaluations:
            with self._cache_lock:
                self.evaluation_cache[cache_key] = fitness
        
        return fitness
    
    def _get_cache_key(self, individual: List[float]) -> str:
        """Generate cache key for an individual"""
        # Round to avoid floating point precision issues
        rounded = [round(gene, 6) for gene in individual]
        return hashlib.md5(str(rounded).encode()).hexdigest()
    
    def _evaluate_with_function(self, individual: List[float]) -> Tuple[float]:
        """Evaluate using provided test function"""
        try:
            result = self.test_function(individual)
            if isinstance(result, (int, float)):
                return (float(result),)
            return tuple(result)
        except Exception as e:
            logger.error(f"Test function failed: {e}")
            return (-1000.0,)
    
    def _evaluate_sandboxed(self, individual: List[float]) -> Tuple[float]:
        """Evaluate in sandboxed subprocess"""
        config = self._map_genes_to_config(individual)
        
        # Use stdin for passing configuration (more secure than command line)
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(config, f)
            config_file = f.name
        
        try:
            # Set up sandboxed environment
            env = os.environ.copy()
            env["SANDBOXED_TEST_RUNNER"] = "1"
            env["CONFIG_FILE"] = config_file
            
            # Run evaluation subprocess
            process = subprocess.Popen(
                [sys.executable, __file__, "run_test"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env
            )
            
            try:
                stdout, stderr = process.communicate(
                    input=json.dumps(config),
                    timeout=self.config.timeout_seconds
                )
                
                if process.returncode != 0:
                    logger.error(f"Subprocess failed: {stderr}")
                    return (-1000.0,)
                
                # Parse metrics from stdout
                metrics = json.loads(stdout)
                fitness = self._calculate_fitness(metrics)
                return (fitness,)
                
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
                logger.error("Evaluation timed out")
                return (-1000.0,)
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse metrics: {e}")
                return (-1000.0,)
                
        finally:
            # Clean up temp file
            try:
                os.unlink(config_file)
            except:
                pass
    
    def _evaluate_heuristic(self, individual: List[float]) -> Tuple[float]:
        """Evaluate using heuristic fitness function"""
        config = self._map_genes_to_config(individual)
        
        fitness = 0.0
        
        # Heuristic evaluation based on configuration
        if 400 < config.get('max_connections', 0) < 800:
            fitness += 10
        if 2 < config.get('timeout_sec', 0) < 6:
            fitness += 10
        if config.get('retry_count', 0) == 3:
            fitness += 5
        if 1000 < config.get('cache_size', 0) < 5000:
            fitness += 8
        if 0.3 < config.get('alert_threshold', 1.0) < 0.7:
            fitness += 10
        if 5 < config.get('worker_threads', 1) < 15:
            fitness += 7
        if 10 < config.get('batch_size', 1) < 50:
            fitness += 6
        
        # Penalize extreme values
        for key, value in config.items():
            if isinstance(value, (int, float)):
                if value <= 0:
                    fitness -= 20
        
        return (fitness,)
    
    def _map_genes_to_config(self, individual: List[float]) -> Dict[str, Any]:
        """Map genetic representation to configuration"""
        config = {}
        gene_idx = 0
    
        # Use actual config space if available
        if hasattr(self, 'config_space') and self.config_space:
            # Validate individual length before mapping
            expected_gene_count = len(self.config_space.parameters)
            if len(individual) < expected_gene_count:
                logger.warning(
                    f"Individual has {len(individual)} genes but expected {expected_gene_count}. "
                    f"Using defaults for missing parameters."
                )
            
            for param_name, param_info in self.config_space.parameters.items():
                if gene_idx >= len(individual):
                    # Use default value or midpoint for missing genes
                    min_val = param_info["min"]
                    max_val = param_info["max"]
                    param_type = param_info["type"]
                    
                    if param_type == "int":
                        config[param_name] = int((min_val + max_val) / 2)
                    elif param_type == "bool":
                        config[param_name] = True
                    else:  # float
                        config[param_name] = (min_val + max_val) / 2.0
                    
                    logger.warning(f"Using default value for missing gene: {param_name}")
                    gene_idx += 1
                    continue
                
                gene = np.clip(individual[gene_idx], 0.0, 1.0)
                min_val = param_info["min"]
                max_val = param_info["max"]
                param_type = param_info["type"]
    
                if param_type == "int":
                    config[param_name] = int(min_val + gene * (max_val - min_val))
                elif param_type == "bool":
                    config[param_name] = gene > 0.5
                else:  # float
                    config[param_name] = min_val + gene * (max_val - min_val)
    
                gene_idx += 1
        else:
            # Fallback to default mapping
            default_mapping = {
                0: ("max_connections", 50, 1000, int),
                1: ("timeout_sec", 1.0, 10.0, float),
                2: ("retry_count", 1, 5, int),
                3: ("cache_size", 100, 10000, int),
                4: ("alert_threshold", 0.1, 1.0, float),
                5: ("batch_size", 1, 100, int),
                6: ("worker_threads", 1, 20, int)
            }
    
            for i, gene in enumerate(individual):
                if i in default_mapping:
                    name, min_val, max_val, type_func = default_mapping[i]
                    gene_clamped = np.clip(gene, 0.0, 1.0)
    
                    if type_func == int:
                        config[name] = int(min_val + gene_clamped * (max_val - min_val))
                    elif type_func == bool:
                        config[name] = gene_clamped > 0.5
                    else:
                        config[name] = min_val + gene_clamped * (max_val - min_val)
        return config
    
    def _calculate_fitness(self, metrics: Dict[str, float]) -> float:
        """Calculate fitness from metrics using configured weights"""
        fitness = 0.0
        
        for metric, weight in self.config.reward_weights.items():
            value = metrics.get(metric, 0.0)
            fitness += value * weight
        
        return fitness
    
    def cleanup(self):
        """Clean up worker pool"""
        # Send shutdown signal to workers
        for _ in self.workers:
            self.evaluation_queue.put((None, None))
        
        # Wait for workers to finish
        for worker in self.workers:
            worker.join(timeout=5)


class GeneticOptimizer:
    """
    Encapsulated genetic algorithm optimizer.
    Avoids global namespace pollution and provides clean interface.
    """
    
    def __init__(self, 
                 config_space: Optional[ConfigurationSpace] = None,
                 evolution_config: Optional[EvolutionConfig] = None):
        """Initialize genetic optimizer"""
        
        if not DEAP_AVAILABLE:
            raise ImportError("DEAP library required. Install with: pip install deap")
        
        self.config_space = config_space or ConfigurationSpace()
        self.evolution_config = evolution_config or EvolutionConfig()
        
        # Validate configurations
        self.config_space.validate()
        self.evolution_config.validate()
        
        # Set up DEAP components in isolated namespace
        self._setup_deap()
        
        # Initialize evaluator
        self.evaluator = None
        
        # Track evolution history
        self.evolution_history = []
        self.best_individual = None
        self.best_fitness = float('-inf')
    
    def _setup_deap(self):
        """Set up DEAP components without global pollution"""
        # Create unique class names to avoid conflicts
        self._creator_id = id(self)
        fitness_name = f"FitnessMax_{self._creator_id}"
        individual_name = f"Individual_{self._creator_id}"
        
        # Create fitness and individual classes in local namespace
        if not hasattr(creator, fitness_name):
            creator.create(fitness_name, base.Fitness, weights=(1.0,))
        if not hasattr(creator, individual_name):
            creator.create(individual_name, list, fitness=getattr(creator, fitness_name))
        
        self.fitness_class = getattr(creator, fitness_name)
        self.individual_class = getattr(creator, individual_name)
        
        # Set up toolbox
        self.toolbox = base.Toolbox()
        
        # Register genetic operators
        self.toolbox.register("attr_float", np.random.uniform, 0, 1)
        self.toolbox.register("individual", tools.initRepeat, self.individual_class,
                            self.toolbox.attr_float, n=self.config_space.gene_count)
        self.toolbox.register("population", tools.initRepeat, list, self.toolbox.individual)
        
        # Genetic operators with configured parameters
        self.toolbox.register("mate", tools.cxBlend, alpha=0.5)
        self.toolbox.register("mutate", tools.mutGaussian, mu=0, 
                            sigma=self.evolution_config.mutation_sigma, indpb=0.2)
        self.toolbox.register("select", tools.selTournament, 
                            tournsize=self.evolution_config.tournament_size)
    
    def evolve(self, 
               test_function: Optional[Callable] = None,
               audit_logger: Optional[Any] = None,
               verbose: bool = True) -> Dict[str, Any]:
        """
        Run genetic algorithm to find optimal configuration.
        
        Args:
            test_function: Optional function to evaluate configurations
            audit_logger: Optional logger for audit trail
            verbose: Whether to print progress
        
        Returns:
            Best configuration found
        """
        
        # Set up evaluator
        self.evaluator = FitnessEvaluator(self.evolution_config, test_function)
        self.evaluator.config_space = self.config_space
        self.toolbox.register("evaluate", self.evaluator.evaluate_single)
        
        # Initialize population
        population = self.toolbox.population(n=self.evolution_config.population_size)
        
        # Set up statistics tracking
        stats = tools.Statistics(lambda ind: ind.fitness.values)
        stats.register("avg", np.mean)
        stats.register("min", np.min)
        stats.register("max", np.max)
        stats.register("std", np.std)
        
        # Hall of fame to track best individuals
        hall_of_fame = tools.HallOfFame(self.evolution_config.elite_size)
        
        # Logbook for evolution history
        logbook = tools.Logbook()
        logbook.header = ['gen', 'nevals'] + stats.fields if hasattr(stats, 'fields') else ['gen', 'nevals', 'avg', 'min', 'max', 'std']
        
        # Evaluate initial population
        invalid_ind = [ind for ind in population if not ind.fitness.valid]
        fitnesses = list(map(self.toolbox.evaluate, invalid_ind))
        for ind, fit in zip(invalid_ind, fitnesses):
            ind.fitness.values = fit
        
        hall_of_fame.update(population)
        record = stats.compile(population)
        logbook.record(gen=0, nevals=len(invalid_ind), **record)
        
        if verbose:
            print(f"Generation 0: {logbook.stream}")
        
        # Evolution loop
        plateau_counter = 0
        last_best = float('-inf')
        
        for gen in range(1, self.evolution_config.generations + 1):
            # Select next generation
            offspring = self.toolbox.select(population, len(population))
            offspring = list(map(self.toolbox.clone, offspring))
            
            # Apply crossover
            for child1, child2 in zip(offspring[::2], offspring[1::2]):
                if np.random.random() < self.evolution_config.crossover_probability:
                    self.toolbox.mate(child1, child2)
                    del child1.fitness.values
                    del child2.fitness.values
            
            # Apply mutation
            for mutant in offspring:
                if np.random.random() < self.evolution_config.mutation_probability:
                    self.toolbox.mutate(mutant)
                    del mutant.fitness.values
            
            # Evaluate offspring
            invalid_ind = [ind for ind in offspring if not ind.fitness.valid]
            fitnesses = list(map(self.toolbox.evaluate, invalid_ind))
            for ind, fit in zip(invalid_ind, fitnesses):
                ind.fitness.values = fit
            
            # Update population with elitism
            elite = tools.selBest(population, self.evolution_config.elite_size)
            offspring = elite + tools.selBest(offspring, 
                                             len(population) - self.evolution_config.elite_size)
            population[:] = offspring
            
            # Update statistics
            hall_of_fame.update(population)
            record = stats.compile(population)
            logbook.record(gen=gen, nevals=len(invalid_ind), **record)
            
            if verbose:
                print(f"Generation {gen}: {logbook.stream}")
            
            # Log to audit logger if provided
            if audit_logger:
                audit_logger.log_event(
                    "ga_generation",
                    {
                        "generation": gen,
                        "stats": record,
                        "evaluations": self.evaluator.evaluation_count
                    }
                )
            
            # Check for early stopping
            current_best = record['max']
            if abs(current_best - last_best) < self.evolution_config.early_stopping_threshold:
                plateau_counter += 1
                if plateau_counter >= self.evolution_config.early_stopping_generations:
                    if verbose:
                        print(f"Early stopping at generation {gen} - fitness plateaued")
                    break
            else:
                plateau_counter = 0
            last_best = current_best
        
        # Extract best solution
        self.best_individual = hall_of_fame[0]
        self.best_fitness = self.best_individual.fitness.values[0]
        best_config = self.evaluator._map_genes_to_config(self.best_individual)
        
        # Save evolution history
        self.evolution_history = logbook
        
        # Log final result
        if audit_logger:
            audit_logger.log_event(
                "ga_complete",
                {
                    "best_config": best_config,
                    "best_fitness": self.best_fitness,
                    "total_evaluations": self.evaluator.evaluation_count,
                    "generations_run": gen,
                    "cache_hits": len(self.evaluator.evaluation_cache)
                }
            )
        
        if verbose:
            print("\nEvolution complete!")
            print(f"Best fitness: {self.best_fitness:.2f}")
            print(f"Total evaluations: {self.evaluator.evaluation_count}")
            print(f"Cache hits: {len(self.evaluator.evaluation_cache)}")
            print(f"Best configuration: {best_config}")
        
        # Clean up
        self.evaluator.cleanup()
        
        return best_config
    
    def get_evolution_summary(self) -> Dict[str, Any]:
        """Get summary of evolution run"""
        if not self.evolution_history:
            return {}
        
        return {
            "best_fitness": self.best_fitness,
            "best_config": self.evaluator._map_genes_to_config(self.best_individual) 
                          if self.best_individual else None,
            "generations": len(self.evolution_history),
            "total_evaluations": self.evaluator.evaluation_count if self.evaluator else 0,
            "cache_size": len(self.evaluator.evaluation_cache) if self.evaluator else 0,
            "history": [dict(record) for record in self.evolution_history]
        }
    
    def save_checkpoint(self, filepath: str):
        """Save optimizer state to file"""
        checkpoint = {
            "best_individual": self.best_individual,
            "best_fitness": self.best_fitness,
            "evolution_history": self.evolution_history,
            "config_space": asdict(self.config_space),
            "evolution_config": asdict(self.evolution_config)
        }
        
        with open(filepath, 'wb') as f:
            pickle.dump(checkpoint, f)
        
        logger.info(f"Checkpoint saved to {filepath}")
    
    def load_checkpoint(self, filepath: str):
        """Load optimizer state from file"""
        with open(filepath, 'rb') as f:
            checkpoint = pickle.load(f)
        
        self.best_individual = checkpoint["best_individual"]
        self.best_fitness = checkpoint["best_fitness"]
        self.evolution_history = checkpoint["evolution_history"]
        
        logger.info(f"Checkpoint loaded from {filepath}")
    
    def __del__(self):
        """Clean up DEAP creator classes to prevent memory leaks"""
        try:
            # Remove creator classes to prevent memory leaks
            if hasattr(self, '_creator_id'):
                fitness_name = f"FitnessMax_{self._creator_id}"
                individual_name = f"Individual_{self._creator_id}"
                
                # Remove from creator if they exist
                if hasattr(creator, fitness_name):
                    delattr(creator, fitness_name)
                if hasattr(creator, individual_name):
                    delattr(creator, individual_name)
        except Exception as e:
            # Don't raise exceptions in __del__
            logger.debug(f"Error cleaning up DEAP creator classes: {e}")


def evolve_configs(configs):
    """
    Wrapper function to evolve configurations.
    This bridges the gap between the old API and the new GeneticOptimizer class.
    """
    optimizer = GeneticOptimizer()
    
    # If configs is a dict, use it to set up the optimizer
    if isinstance(configs, dict):
        if 'config_space' in configs:
            optimizer.config_space = configs['config_space']
        if 'evolution_config' in configs:
            optimizer.evolution_config = configs['evolution_config']
    
    # Run evolution and return the best configuration
    best_config = optimizer.evolve(verbose=False)
    return best_config


def run_test_evaluation(config: Dict[str, Any]) -> Dict[str, float]:
    """
    Test evaluation function for sandboxed execution.
    In production, replace with actual system testing.
    """
    
    # Simulate realistic metrics based on configuration
    is_good_config = (
        400 < config.get("max_connections", 0) < 800 and
        2 < config.get("timeout_sec", 10) < 6 and
        config.get("retry_count", 1) == 3 and
        config.get("alert_threshold", 1.0) < 0.7
    )
    
    if is_good_config:
        metrics = {
            "pass_rate": np.random.uniform(0.95, 1.0),
            "latency": np.random.uniform(0.01, 0.05),
            "alerts": np.random.randint(0, 2),
            "errors": np.random.randint(0, 1),
            "throughput": np.random.uniform(900, 1000)
        }
    else:
        metrics = {
            "pass_rate": np.random.uniform(0.7, 0.9),
            "latency": np.random.uniform(0.1, 0.5),
            "alerts": np.random.randint(1, 5),
            "errors": np.random.randint(0, 3),
            "throughput": np.random.uniform(500, 800)
        }
    
    # Simulate occasional failures
    if config.get("retry_count", 1) >= 5 or config.get("timeout_sec", 5) <= 1:
        raise RuntimeError("Configuration caused system failure")
    
    return metrics


def run_evolution_demonstration():
    """Run a demonstration of the genetic optimizer"""
    
    print("\n" + "="*70)
    print("GENETIC ALGORITHM CONFIGURATION OPTIMIZER")
    print("="*70 + "\n")
    
    # Set up configuration
    evolution_config = EvolutionConfig(
        generations=15,
        population_size=30,
        crossover_probability=0.8,
        mutation_probability=0.3,
        elite_size=3,
        early_stopping_generations=5,
        cache_evaluations=True
    )
    
    # Create optimizer
    optimizer = GeneticOptimizer(evolution_config=evolution_config)
    
    # Define test function
    def test_function(individual: List[float]) -> float:
        config = optimizer.evaluator._map_genes_to_config(individual)
        try:
            metrics = run_test_evaluation(config)
            fitness = sum(
                metrics.get(metric, 0) * weight
                for metric, weight in evolution_config.reward_weights.items()
            )
            return fitness
        except Exception as e:
            logger.debug(f"Evaluation failed: {e}")
            return -1000.0
    
    # Run evolution
    print("Starting evolution process...\n")
    optimizer.evolve(
        test_function=test_function,
        verbose=True
    )
    
    # Show summary
    print("\n" + "="*70)
    print("EVOLUTION SUMMARY")
    print("="*70)
    
    summary = optimizer.get_evolution_summary()
    print(f"\nBest Fitness: {summary['best_fitness']:.2f}")
    print(f"Total Evaluations: {summary['total_evaluations']}")
    print(f"Cache Hits: {summary['cache_size']}")
    print("\nBest Configuration:")
    for key, value in summary['best_config'].items():
        print(f"  {key}: {value}")
    
    # Save checkpoint
    checkpoint_path = "evolution_checkpoint.pkl"
    optimizer.save_checkpoint(checkpoint_path)
    print(f"\nCheckpoint saved to {checkpoint_path}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "run_test":
        # Sandboxed test execution
        if os.environ.get("SANDBOXED_TEST_RUNNER") != "1":
            print("Error: Must be run in sandboxed environment", file=sys.stderr)
            sys.exit(1)
        
        try:
            # Read configuration from stdin (more secure than command line)
            config_json = sys.stdin.read()
            config = json.loads(config_json)
            
            # Run test evaluation
            metrics = run_test_evaluation(config)
            
            # Output metrics as JSON
            print(json.dumps(metrics))
            sys.exit(0)
            
        except Exception as e:
            print(f"Test evaluation failed: {e}", file=sys.stderr)
            sys.exit(1)
    
    elif os.environ.get("SANDBOXED_EVOLUTION") == "1":
        # Run in sandboxed mode
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
        run_evolution_demonstration()
    
    else:
        # Launch sandboxed process
        env = os.environ.copy()
        env["SANDBOXED_EVOLUTION"] = "1"
        
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Launching sandboxed evolution process...")
        
        try:
            proc = subprocess.Popen(
                [sys.executable, __file__],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            
            # Stream output
            for line in proc.stdout:
                print(line, end='')
            
            proc.wait()
            
            if proc.returncode != 0:
                print(f"\n[ERROR] Process exited with code: {proc.returncode}")
                for line in proc.stderr:
                    print(line, end='')
                    
        except KeyboardInterrupt:
            print("\n[INFO] Evolution interrupted by user")
            proc.terminate()
            proc.wait(timeout=5)
        except Exception as e:
            print(f"[ERROR] Failed to launch process: {e}")