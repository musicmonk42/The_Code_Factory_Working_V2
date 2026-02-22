# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Test suite for self_fixing_engineer/evolution.py

Tests cover:
- Genome initialization with valid parameter ranges
- Crossover produces children with parameters from both parents
- Mutation changes values within bounds
- Fitness evaluation with known metrics
- One full evolve_generation() call with mock metrics
- save_population / load_population round-trip
"""

import json
import os
import sys
import tempfile
from types import SimpleNamespace
from typing import Dict

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from self_fixing_engineer.evolution import (
    Genome,
    GeneticEvolutionEngine,
    _COOLDOWN_RANGE,
    _CRITIQUE_THRESHOLD_RANGE,
    _MAX_TOKENS_RANGE,
    _REWARD_WEIGHT_RANGE,
    _TEMPERATURE_RANGE,
)


def make_metrics(**kwargs):
    """Create a SimpleNamespace metrics object with default values."""
    defaults = {
        "pass_rate": 0.8,
        "code_coverage": 0.6,
        "complexity": 0.3,
        "generation_success_rate": 0.7,
        "critique_score": 0.75,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class TestGenome:
    """Test the Genome dataclass."""

    def test_default_genome_is_valid(self):
        """Default genome has valid parameter ranges."""
        genome = Genome()
        assert genome.is_valid(), "Default genome should be valid"

    def test_genome_temperature_in_range(self):
        """Genome temperature is within [0.0, 1.5]."""
        genome = Genome()
        assert _TEMPERATURE_RANGE[0] <= genome.llm_temperature <= _TEMPERATURE_RANGE[1]

    def test_genome_max_tokens_in_range(self):
        """Genome max_tokens is within [256, 4096]."""
        genome = Genome()
        assert _MAX_TOKENS_RANGE[0] <= genome.llm_max_tokens <= _MAX_TOKENS_RANGE[1]

    def test_genome_critique_threshold_in_range(self):
        """Genome critique_threshold is within [0.0, 1.0]."""
        genome = Genome()
        assert _CRITIQUE_THRESHOLD_RANGE[0] <= genome.critique_threshold <= _CRITIQUE_THRESHOLD_RANGE[1]

    def test_genome_reward_weights_in_range(self):
        """All reward weights are within [-5.0, 5.0]."""
        genome = Genome()
        for k, v in genome.reward_weights.items():
            assert _REWARD_WEIGHT_RANGE[0] <= v <= _REWARD_WEIGHT_RANGE[1], f"reward_weight[{k}]={v} out of range"

    def test_genome_cooldowns_in_range(self):
        """All action cooldowns are within [1, 100]."""
        genome = Genome()
        for k, v in genome.action_cooldowns.items():
            assert _COOLDOWN_RANGE[0] <= v <= _COOLDOWN_RANGE[1], f"cooldown[{k}]={v} out of range"

    def test_genome_to_dict_round_trip(self):
        """Genome can be serialized to dict and deserialized back."""
        genome = Genome(llm_temperature=0.42, llm_max_tokens=512, critique_threshold=0.55)
        d = genome.to_dict()
        restored = Genome.from_dict(d)
        assert restored.llm_temperature == genome.llm_temperature
        assert restored.llm_max_tokens == genome.llm_max_tokens
        assert restored.critique_threshold == genome.critique_threshold
        assert restored.genome_id == genome.genome_id

    def test_genome_has_unique_id(self):
        """Each genome gets a unique genome_id."""
        g1 = Genome()
        g2 = Genome()
        assert g1.genome_id != g2.genome_id


class TestGeneticEvolutionEngine:
    """Test the GeneticEvolutionEngine class."""

    def setup_method(self):
        """Create a small engine for each test."""
        self.engine = GeneticEvolutionEngine(
            population_size=5,
            mutation_rate=0.3,
            crossover_rate=0.8,
            elite_count=1,
        )

    def test_initialize_population_size(self):
        """initialize_population() creates the correct number of genomes."""
        pop = self.engine.initialize_population()
        assert len(pop) == 5

    def test_initialize_population_all_valid(self):
        """All genomes in initial population are valid."""
        pop = self.engine.initialize_population()
        for genome in pop:
            assert genome.is_valid(), f"Genome {genome.genome_id} is not valid"

    def test_evaluate_fitness_known_metrics(self):
        """evaluate_fitness() produces expected score with known metrics."""
        genome = Genome()
        # Single source of truth: one dict used for both make_metrics() and expected calc
        input_vals = {
            "pass_rate": 1.0,
            "code_coverage": 1.0,
            "complexity": 0.0,
            "generation_success_rate": 1.0,
            "critique_score": 1.0,
        }
        metrics = make_metrics(**input_vals)
        fitness = self.engine.evaluate_fitness(genome, metrics)
        # Expected value computed from GeneticEvolutionEngine.FITNESS_WEIGHTS so
        # this test stays correct if the weights change.
        expected = sum(
            GeneticEvolutionEngine.FITNESS_WEIGHTS[k] * input_vals[k]
            for k in GeneticEvolutionEngine.FITNESS_WEIGHTS
        )
        assert abs(fitness - expected) < 0.01, f"Expected {expected}, got {fitness}"

    def test_evaluate_fitness_zero_metrics(self):
        """evaluate_fitness() returns 0.0 for all-zero metrics."""
        genome = Genome()
        metrics = make_metrics(
            pass_rate=0.0,
            code_coverage=0.0,
            complexity=0.0,
            generation_success_rate=0.0,
            critique_score=0.0,
        )
        fitness = self.engine.evaluate_fitness(genome, metrics)
        assert fitness == 0.0

    def test_tournament_selection_returns_genome(self):
        """tournament_selection() returns a Genome from the population."""
        pop = self.engine.initialize_population()
        for g in pop:
            self.engine.evaluate_fitness(g, make_metrics())
        selected = self.engine.tournament_selection(pop)
        assert isinstance(selected, Genome)
        assert selected in pop

    def test_crossover_produces_two_children(self):
        """crossover() returns two children."""
        g1 = Genome(llm_temperature=0.1, llm_max_tokens=256)
        g2 = Genome(llm_temperature=1.4, llm_max_tokens=4096)
        child1, child2 = self.engine.crossover(g1, g2)
        assert isinstance(child1, Genome)
        assert isinstance(child2, Genome)

    def test_crossover_children_parameters_from_parents(self):
        """crossover() children have parameters from both parents."""
        g1 = Genome(llm_temperature=0.1, llm_max_tokens=256, critique_threshold=0.1)
        g2 = Genome(llm_temperature=1.4, llm_max_tokens=4096, critique_threshold=0.9)

        # Run crossover multiple times to get statistical coverage
        temperatures_seen = set()
        for _ in range(20):
            child1, child2 = self.engine.crossover(g1, g2)
            temperatures_seen.add(round(child1.llm_temperature, 1))
            temperatures_seen.add(round(child2.llm_temperature, 1))

        # At least one of the parent temperatures should appear in children
        assert 0.1 in temperatures_seen or 1.4 in temperatures_seen

    def test_mutation_preserves_validity(self):
        """mutate() keeps all parameters within valid bounds."""
        genome = Genome()
        for _ in range(50):
            genome = self.engine.mutate(genome)
        assert genome.is_valid(), "Genome should remain valid after multiple mutations"

    def test_mutation_changes_values(self):
        """mutate() actually changes some values (with high mutation rate)."""
        engine = GeneticEvolutionEngine(mutation_rate=1.0)  # always mutate
        genome = Genome(llm_temperature=0.5, llm_max_tokens=1024, critique_threshold=0.5)
        original_temp = genome.llm_temperature
        original_tokens = genome.llm_max_tokens

        # Run multiple times to ensure at least one changes
        changed = False
        for _ in range(20):
            g = Genome(llm_temperature=0.5, llm_max_tokens=1024)
            mutated = engine.mutate(g)
            if mutated.llm_temperature != 0.5 or mutated.llm_max_tokens != 1024:
                changed = True
                break
        assert changed, "Mutation should change at least one parameter"

    def test_evolve_generation_returns_best_genome(self):
        """evolve_generation() returns the best genome."""
        self.engine.initialize_population()
        metrics = make_metrics()
        best = self.engine.evolve_generation(metrics)
        assert isinstance(best, Genome)
        assert best.fitness >= 0.0

    def test_evolve_generation_updates_population(self):
        """evolve_generation() maintains population size."""
        self.engine.initialize_population()
        metrics = make_metrics()
        self.engine.evolve_generation(metrics)
        assert len(self.engine.population) == 5

    def test_evolve_generation_increments_generation_counter(self):
        """evolve_generation() increments the generation counter."""
        self.engine.initialize_population()
        assert self.engine.generation == 0
        self.engine.evolve_generation(make_metrics())
        assert self.engine.generation == 1

    def test_get_evolution_history(self):
        """get_evolution_history() returns per-generation stats."""
        self.engine.initialize_population()
        self.engine.evolve_generation(make_metrics())
        self.engine.evolve_generation(make_metrics())
        history = self.engine.get_evolution_history()
        assert len(history) == 2
        assert "best_fitness" in history[0]
        assert "avg_fitness" in history[0]
        assert "generation" in history[0]

    def test_save_load_population_round_trip(self):
        """save_population / load_population preserves population data."""
        self.engine.initialize_population()
        metrics = make_metrics()
        self.engine.evolve_generation(metrics)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            tmp_path = f.name

        try:
            self.engine.save_population(tmp_path)
            assert os.path.exists(tmp_path)

            new_engine = GeneticEvolutionEngine(population_size=5)
            new_engine.load_population(tmp_path)

            assert new_engine.generation == self.engine.generation
            assert len(new_engine.population) == len(self.engine.population)
            assert new_engine.population[0].genome_id == self.engine.population[0].genome_id
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def test_apply_genome_to_config(self):
        """apply_genome_to_config() updates config reward_weights."""
        genome = Genome(
            reward_weights={"pass_rate": 3.0, "code_coverage": 2.0},
            critique_threshold=0.8,
        )

        class MockConfig:
            reward_weights = {}
            critique_threshold = 0.5

        config = MockConfig()
        self.engine.apply_genome_to_config(genome, config)
        assert config.reward_weights["pass_rate"] == 3.0
        assert config.critique_threshold == 0.8
