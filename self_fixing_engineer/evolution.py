# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Genetic Algorithm Platform Evolution

Evolves the platform's own configuration and agent hyperparameters
using a genetic algorithm.

Genome represents:
- Agent reward weights (from EnvironmentConfig.reward_weights)
- LLM sampling parameters (temperature, top_p, max_tokens)
- Cooldown values for RL actions
- Critique thresholds

Note: prompt template evolution is a future enhancement and is not
currently implemented.

Fitness function evaluates genome quality based on metrics provided by
the caller (e.g. the Arbiter), which is responsible for collecting real
metrics from the running platform.
"""

import json
import logging
import random
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

try:
    from self_fixing_engineer.arbiter.metrics import get_or_create_counter, get_or_create_gauge
    _PROM_AVAILABLE = True
except ImportError:
    _PROM_AVAILABLE = False
    def get_or_create_counter(*a, **kw): return None  # type: ignore[misc]
    def get_or_create_gauge(*a, **kw): return None    # type: ignore[misc]

# Parameter bounds for validity
_REWARD_WEIGHT_RANGE = (-5.0, 5.0)
_TEMPERATURE_RANGE = (0.0, 1.5)
_TOP_P_RANGE = (0.0, 1.0)
_MAX_TOKENS_RANGE = (256, 4096)
_COOLDOWN_RANGE = (1, 100)
_CRITIQUE_THRESHOLD_RANGE = (0.0, 1.0)


@dataclass
class Genome:
    """Represents a configuration genome for the genetic algorithm."""

    reward_weights: Dict[str, float] = field(
        default_factory=lambda: {
            "pass_rate": 2.0,
            "code_coverage": 1.5,
            "complexity": -0.5,
            "generation_success_rate": 2.5,
            "critique_score": 1.0,
        }
    )
    llm_temperature: float = 0.7
    llm_top_p: float = 1.0
    llm_max_tokens: int = 1024
    action_cooldowns: Dict[str, int] = field(
        default_factory=lambda: {
            "run_linter": 5,
            "run_tests": 10,
            "run_formatter": 3,
            "apply_patch": 15,
            "noop": 1,
        }
    )
    critique_threshold: float = 0.6
    generation: int = 0
    fitness: float = 0.0
    genome_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Genome":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def is_valid(self) -> bool:
        """Check if all genome parameters are within valid ranges."""
        if not (_TEMPERATURE_RANGE[0] <= self.llm_temperature <= _TEMPERATURE_RANGE[1]):
            return False
        if not (_TOP_P_RANGE[0] <= self.llm_top_p <= _TOP_P_RANGE[1]):
            return False
        if not (_MAX_TOKENS_RANGE[0] <= self.llm_max_tokens <= _MAX_TOKENS_RANGE[1]):
            return False
        if not (_CRITIQUE_THRESHOLD_RANGE[0] <= self.critique_threshold <= _CRITIQUE_THRESHOLD_RANGE[1]):
            return False
        for w in self.reward_weights.values():
            if not (_REWARD_WEIGHT_RANGE[0] <= w <= _REWARD_WEIGHT_RANGE[1]):
                return False
        for c in self.action_cooldowns.values():
            if not (_COOLDOWN_RANGE[0] <= c <= _COOLDOWN_RANGE[1]):
                return False
        return True


def _rand_reward_weights() -> Dict[str, float]:
    keys = ["pass_rate", "code_coverage", "complexity", "generation_success_rate", "critique_score"]
    return {k: round(random.uniform(*_REWARD_WEIGHT_RANGE), 3) for k in keys}


def _rand_cooldowns() -> Dict[str, int]:
    keys = ["run_linter", "run_tests", "run_formatter", "apply_patch", "noop"]
    return {k: random.randint(*_COOLDOWN_RANGE) for k in keys}


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _clip_int(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(round(value))))


class GeneticEvolutionEngine:
    """
    Genetic algorithm engine that evolves platform configuration parameters.
    """

    #: Weights used by :meth:`evaluate_fitness`.  Positive weights reward higher
    #: values; negative weights penalise them (e.g. ``complexity``).
    #: Exposed as a class constant so tests and callers can reference the same
    #: formula without duplicating the numbers.
    FITNESS_WEIGHTS: Dict[str, float] = {
        "pass_rate": 2.0,
        "code_coverage": 1.5,
        "complexity": -0.5,
        "generation_success_rate": 2.5,
        "critique_score": 1.0,
    }

    def __init__(
        self,
        population_size: int = 10,
        mutation_rate: float = 0.15,
        crossover_rate: float = 0.7,
        elite_count: int = 2,
    ):
        self.population_size = population_size
        self.mutation_rate = mutation_rate
        self.crossover_rate = crossover_rate
        self.elite_count = elite_count
        self.population: List[Genome] = []
        self.generation = 0
        self._evolution_history: List[Dict[str, Any]] = []

        # Prometheus metrics — lazily initialised to avoid registration races
        self._prom_generations = get_or_create_counter(
            "evolution_generations_total",
            "Total genetic-algorithm generations completed",
            ("engine",),
        )
        self._prom_best_fitness = get_or_create_gauge(
            "evolution_best_fitness",
            "Best genome fitness in the current population",
            ("engine",),
        )
        self._prom_population_size = get_or_create_gauge(
            "evolution_population_size",
            "Current population size of the genetic algorithm",
            ("engine",),
        )
        self._engine_label = "default"

    def initialize_population(self) -> List[Genome]:
        """Create a population of random genomes within valid parameter ranges."""
        self.population = []
        for _ in range(self.population_size):
            genome = Genome(
                reward_weights=_rand_reward_weights(),
                llm_temperature=round(random.uniform(*_TEMPERATURE_RANGE), 3),
                llm_top_p=round(random.uniform(*_TOP_P_RANGE), 3),
                llm_max_tokens=random.randint(*_MAX_TOKENS_RANGE),
                action_cooldowns=_rand_cooldowns(),
                critique_threshold=round(random.uniform(*_CRITIQUE_THRESHOLD_RANGE), 3),
                generation=0,
            )
            self.population.append(genome)
        logger.info(f"GeneticEvolutionEngine: Initialized population of {self.population_size} genomes.")
        return self.population

    def evaluate_fitness(self, genome: Genome, metrics: Any) -> float:
        """Compute fitness as a weighted sum of ``SystemMetrics`` values.

        The formula is driven by :attr:`FITNESS_WEIGHTS`.  Positive weights
        reward higher metric values; negative weights penalise them.

        ``fitness = Σ weight_i · metric_i``
        """
        fitness = sum(
            self.FITNESS_WEIGHTS.get(key, 0.0) * (getattr(metrics, key, 0.0) or 0.0)
            for key in self.FITNESS_WEIGHTS
        )
        genome.fitness = round(fitness, 6)
        return genome.fitness

    def tournament_selection(
        self, population: List[Genome], tournament_size: int = 3
    ) -> Genome:
        """Select a parent via tournament selection (highest fitness wins)."""
        competitors = random.sample(population, min(tournament_size, len(population)))
        return max(competitors, key=lambda g: g.fitness)

    def crossover(self, parent1: Genome, parent2: Genome) -> Tuple[Genome, Genome]:
        """Uniform crossover of all numeric and dict fields from both parents."""
        if random.random() > self.crossover_rate:
            return parent1, parent2

        # Crossover reward_weights
        keys = list(parent1.reward_weights.keys())
        child1_rw = {}
        child2_rw = {}
        for k in keys:
            if random.random() < 0.5:
                child1_rw[k] = parent1.reward_weights.get(k, 1.0)
                child2_rw[k] = parent2.reward_weights.get(k, 1.0)
            else:
                child1_rw[k] = parent2.reward_weights.get(k, 1.0)
                child2_rw[k] = parent1.reward_weights.get(k, 1.0)

        # Crossover action_cooldowns
        cd_keys = list(parent1.action_cooldowns.keys())
        child1_cd = {}
        child2_cd = {}
        for k in cd_keys:
            if random.random() < 0.5:
                child1_cd[k] = parent1.action_cooldowns.get(k, 5)
                child2_cd[k] = parent2.action_cooldowns.get(k, 5)
            else:
                child1_cd[k] = parent2.action_cooldowns.get(k, 5)
                child2_cd[k] = parent1.action_cooldowns.get(k, 5)

        # Crossover scalar fields
        child1_temp = parent1.llm_temperature if random.random() < 0.5 else parent2.llm_temperature
        child2_temp = parent2.llm_temperature if random.random() < 0.5 else parent1.llm_temperature
        child1_top_p = parent1.llm_top_p if random.random() < 0.5 else parent2.llm_top_p
        child2_top_p = parent2.llm_top_p if random.random() < 0.5 else parent1.llm_top_p
        child1_tokens = parent1.llm_max_tokens if random.random() < 0.5 else parent2.llm_max_tokens
        child2_tokens = parent2.llm_max_tokens if random.random() < 0.5 else parent1.llm_max_tokens
        child1_crit = parent1.critique_threshold if random.random() < 0.5 else parent2.critique_threshold
        child2_crit = parent2.critique_threshold if random.random() < 0.5 else parent1.critique_threshold

        child1 = Genome(
            reward_weights=child1_rw,
            llm_temperature=child1_temp,
            llm_top_p=child1_top_p,
            llm_max_tokens=child1_tokens,
            action_cooldowns=child1_cd,
            critique_threshold=child1_crit,
            generation=self.generation + 1,
        )
        child2 = Genome(
            reward_weights=child2_rw,
            llm_temperature=child2_temp,
            llm_top_p=child2_top_p,
            llm_max_tokens=child2_tokens,
            action_cooldowns=child2_cd,
            critique_threshold=child2_crit,
            generation=self.generation + 1,
        )
        return child1, child2

    def mutate(self, genome: Genome) -> Genome:
        """Apply Gaussian noise to floats (σ=0.1), ±1 to ints, clip to valid ranges."""
        if random.random() > self.mutation_rate:
            return genome

        # Mutate reward_weights
        for k in genome.reward_weights:
            if random.random() < self.mutation_rate:
                genome.reward_weights[k] = _clip(
                    genome.reward_weights[k] + random.gauss(0, 0.1),
                    *_REWARD_WEIGHT_RANGE,
                )
                genome.reward_weights[k] = round(genome.reward_weights[k], 4)

        # Mutate llm_temperature
        if random.random() < self.mutation_rate:
            genome.llm_temperature = _clip(
                genome.llm_temperature + random.gauss(0, 0.1), *_TEMPERATURE_RANGE
            )
            genome.llm_temperature = round(genome.llm_temperature, 4)

        # Mutate llm_top_p
        if random.random() < self.mutation_rate:
            genome.llm_top_p = _clip(
                genome.llm_top_p + random.gauss(0, 0.1), *_TOP_P_RANGE
            )
            genome.llm_top_p = round(genome.llm_top_p, 4)

        # Mutate llm_max_tokens
        if random.random() < self.mutation_rate:
            delta = random.choice([-1, 1]) * random.randint(1, 128)
            genome.llm_max_tokens = _clip_int(
                genome.llm_max_tokens + delta, *_MAX_TOKENS_RANGE
            )

        # Mutate action_cooldowns
        for k in genome.action_cooldowns:
            if random.random() < self.mutation_rate:
                genome.action_cooldowns[k] = _clip_int(
                    genome.action_cooldowns[k] + random.choice([-1, 1]),
                    *_COOLDOWN_RANGE,
                )

        # Mutate critique_threshold
        if random.random() < self.mutation_rate:
            genome.critique_threshold = _clip(
                genome.critique_threshold + random.gauss(0, 0.1),
                *_CRITIQUE_THRESHOLD_RANGE,
            )
            genome.critique_threshold = round(genome.critique_threshold, 4)

        # Validate after mutation (defensive check)
        if not genome.is_valid():
            logger.warning(f"Genome {genome.genome_id} failed validation after mutation")

        return genome

    def evolve_generation(self, current_metrics: Any) -> Genome:
        """
        Run one full generation of evolution.

        1. Evaluate fitness of all genomes using current_metrics
        2. Select elites to carry forward
        3. Fill remaining slots via tournament selection + crossover + mutation
        4. Update population and log history
        5. Return best genome
        """
        if not self.population:
            self.initialize_population()

        # Evaluate all
        for genome in self.population:
            self.evaluate_fitness(genome, current_metrics)

        # Sort by fitness
        self.population.sort(key=lambda g: g.fitness, reverse=True)
        best = self.population[0]

        # Log generation stats
        fitnesses = [g.fitness for g in self.population]
        self._evolution_history.append({
            "generation": self.generation,
            "best_fitness": best.fitness,
            "avg_fitness": sum(fitnesses) / len(fitnesses),
            "best_genome_id": best.genome_id,
            "population_size": len(self.population),
        })
        logger.info(
            f"GeneticEvolutionEngine: Generation {self.generation} complete. "
            f"Best fitness={best.fitness:.4f}, Avg={self._evolution_history[-1]['avg_fitness']:.4f}"
        )

        # Elitism: keep top elite_count genomes
        new_population = list(self.population[: self.elite_count])

        # Fill remaining slots
        while len(new_population) < self.population_size:
            parent1 = self.tournament_selection(self.population)
            parent2 = self.tournament_selection(self.population)
            child1, child2 = self.crossover(parent1, parent2)
            child1 = self.mutate(child1)
            child2 = self.mutate(child2)
            new_population.append(child1)
            if len(new_population) < self.population_size:
                new_population.append(child2)

        self.population = new_population
        self.generation += 1

        # Emit Prometheus metrics
        try:
            if self._prom_generations:
                self._prom_generations.labels(engine=self._engine_label).inc()
            if self._prom_best_fitness:
                self._prom_best_fitness.labels(engine=self._engine_label).set(best.fitness)
            if self._prom_population_size:
                self._prom_population_size.labels(engine=self._engine_label).set(len(self.population))
        except Exception:
            pass

        return best

    def apply_genome_to_config(self, genome: Genome, config: Any) -> None:
        """Update EnvironmentConfig in-place with evolved parameters."""
        if hasattr(config, "reward_weights"):
            config.reward_weights = dict(genome.reward_weights)
        if hasattr(config, "critique_threshold"):
            config.critique_threshold = genome.critique_threshold
        if hasattr(config, "llm_temperature"):
            config.llm_temperature = genome.llm_temperature
        if hasattr(config, "llm_top_p"):
            config.llm_top_p = genome.llm_top_p
        if hasattr(config, "llm_max_tokens"):
            config.llm_max_tokens = genome.llm_max_tokens
        if hasattr(config, "action_cooldowns"):
            config.action_cooldowns = dict(genome.action_cooldowns)
        logger.info(
            f"GeneticEvolutionEngine: Applied genome {genome.genome_id} to config. "
            f"fitness={genome.fitness:.4f}"
        )

    def save_population(self, path: str) -> None:
        """Serialize population to a JSON file."""
        data = {
            "generation": self.generation,
            "population": [g.to_dict() for g in self.population],
            "history": self._evolution_history,
        }
        from pathlib import Path as _Path
        _Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        logger.info(f"GeneticEvolutionEngine: Saved population to {path}")

    def load_population(self, path: str) -> None:
        """Deserialize population from a JSON file."""
        with open(path, "r") as f:
            data = json.load(f)
        self.generation = data.get("generation", 0)
        self.population = [Genome.from_dict(g) for g in data.get("population", [])]
        self._evolution_history = data.get("history", [])
        logger.info(f"GeneticEvolutionEngine: Loaded population from {path} (gen={self.generation})")

    def get_evolution_history(self) -> List[Dict[str, Any]]:
        """Return per-generation fitness statistics."""
        return list(self._evolution_history)
