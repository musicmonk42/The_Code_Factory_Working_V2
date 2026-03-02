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
    _TOP_P_RANGE,
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

    def test_genome_top_p_in_range(self):
        """Genome llm_top_p is within [0.0, 1.0]."""
        genome = Genome()
        assert _TOP_P_RANGE[0] <= genome.llm_top_p <= _TOP_P_RANGE[1]

    def test_genome_top_p_invalid(self):
        """Genome with out-of-range llm_top_p fails is_valid()."""
        genome = Genome()
        genome.llm_top_p = 1.5
        assert not genome.is_valid()

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
        # this test stays correct if the weights change. Missing keys default to 0.
        expected = sum(
            GeneticEvolutionEngine.FITNESS_WEIGHTS[k] * input_vals.get(k, 0.0)
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
        """apply_genome_to_config() updates all config fields from genome."""
        genome = Genome(
            reward_weights={"pass_rate": 3.0, "code_coverage": 2.0},
            critique_threshold=0.8,
            llm_temperature=0.9,
            llm_top_p=0.85,
            llm_max_tokens=2048,
            action_cooldowns={"run_linter": 7, "run_tests": 12, "run_formatter": 4, "apply_patch": 20, "noop": 2},
        )

        class MockConfig:
            reward_weights = {}
            critique_threshold = 0.5
            llm_temperature = 0.7
            llm_top_p = 1.0
            llm_max_tokens = 1024
            action_cooldowns = {}

        config = MockConfig()
        self.engine.apply_genome_to_config(genome, config)
        assert config.reward_weights["pass_rate"] == 3.0
        assert config.critique_threshold == 0.8
        assert config.llm_temperature == 0.9
        assert config.llm_top_p == 0.85
        assert config.llm_max_tokens == 2048
        assert config.action_cooldowns["run_linter"] == 7


class TestPromptTemplateEvolution:
    """Test prompt template evolution features."""

    def test_default_genome_has_prompt_templates(self):
        """Default genome should have prompt templates."""
        genome = Genome()
        assert hasattr(genome, "prompt_templates")
        assert isinstance(genome.prompt_templates, dict)
        assert len(genome.prompt_templates) > 0
        assert "system_prompt" in genome.prompt_templates

    def test_prompt_creativity_in_range(self):
        """Prompt creativity should be within valid range."""
        genome = Genome()
        assert 0.0 <= genome.prompt_creativity <= 1.0

    def test_prompt_verbosity_in_range(self):
        """Prompt verbosity should be within valid range."""
        genome = Genome()
        assert 0.0 <= genome.prompt_verbosity <= 1.0

    def test_default_genome_is_valid_with_prompts(self):
        """Default genome including prompt templates should pass is_valid()."""
        genome = Genome()
        assert genome.is_valid()

    def test_invalid_prompt_creativity_fails_validation(self):
        """Genome with out-of-range prompt_creativity fails is_valid()."""
        genome = Genome()
        genome.prompt_creativity = 1.5
        assert not genome.is_valid()

    def test_invalid_prompt_verbosity_fails_validation(self):
        """Genome with out-of-range prompt_verbosity fails is_valid()."""
        genome = Genome()
        genome.prompt_verbosity = -0.1
        assert not genome.is_valid()

    def test_invalid_prompt_template_too_short_fails_validation(self):
        """Genome with too-short prompt template fails is_valid()."""
        genome = Genome()
        genome.prompt_templates["system_prompt"] = "Hi"
        assert not genome.is_valid()

    def test_prompt_templates_preserved_after_serialization(self):
        """Prompt templates should survive to_dict/from_dict round-trip."""
        genome = Genome()
        genome.prompt_templates["custom"] = "Custom prompt: {code}"
        d = genome.to_dict()
        restored = Genome.from_dict(d)
        assert "custom" in restored.prompt_templates
        assert restored.prompt_templates["custom"] == "Custom prompt: {code}"

    def test_mutation_preserves_placeholders(self):
        """Mutation should preserve required placeholders like {code}."""
        engine = GeneticEvolutionEngine(mutation_rate=1.0)
        genome = Genome()
        genome.prompt_templates["test"] = "Review this code carefully: {code}"
        for _ in range(10):
            engine.mutate(genome)
            assert "{code}" in genome.prompt_templates["test"], (
                "Placeholder {code} was lost during mutation"
            )

    def test_mutation_preserves_validity(self):
        """mutate() keeps prompt fields within valid bounds."""
        engine = GeneticEvolutionEngine(mutation_rate=1.0)
        genome = Genome()
        for _ in range(20):
            genome = engine.mutate(genome)
        assert genome.is_valid(), "Genome should remain valid after multiple mutations"

    def test_crossover_produces_valid_prompts(self):
        """Crossover should produce non-empty prompt templates."""
        engine = GeneticEvolutionEngine(crossover_rate=1.0)
        parent1 = Genome()
        parent1.prompt_templates["system_prompt"] = "You are helpful. Be precise."
        parent2 = Genome()
        parent2.prompt_templates["system_prompt"] = "You are an expert. Be thorough."
        child1, child2 = engine.crossover(parent1, parent2)
        assert len(child1.prompt_templates["system_prompt"]) > 0
        assert len(child2.prompt_templates["system_prompt"]) > 0

    def test_apply_genome_updates_prompt_templates(self):
        """apply_genome_to_config should update prompt templates."""
        engine = GeneticEvolutionEngine()
        genome = Genome()
        genome.prompt_templates["system_prompt"] = "Evolved system prompt."

        class MockConfig:
            prompt_templates: dict = {}
            prompt_creativity: float = 0.0
            prompt_verbosity: float = 0.0

        config = MockConfig()
        engine.apply_genome_to_config(genome, config)
        assert config.prompt_templates["system_prompt"] == "Evolved system prompt."

    def test_apply_genome_updates_prompt_registry(self):
        """apply_genome_to_config should update a prompt_registry when present."""
        from self_fixing_engineer.prompt_registry import PromptRegistry

        engine = GeneticEvolutionEngine()
        genome = Genome()
        genome.prompt_templates["system_prompt"] = "Registry updated prompt."

        class MockConfig:
            prompt_templates: dict = {}
            prompt_creativity: float = 0.0
            prompt_verbosity: float = 0.0
            prompt_registry = PromptRegistry()

        config = MockConfig()
        engine.apply_genome_to_config(genome, config)
        assert config.prompt_registry.get_template("system_prompt") == "Registry updated prompt."

    def test_fitness_weights_include_prompt_metrics(self):
        """FITNESS_WEIGHTS should include prompt-related metric keys."""
        assert "prompt_effectiveness" in GeneticEvolutionEngine.FITNESS_WEIGHTS
        assert "prompt_token_efficiency" in GeneticEvolutionEngine.FITNESS_WEIGHTS
        assert "prompt_consistency" in GeneticEvolutionEngine.FITNESS_WEIGHTS


class TestPromptRegistry:
    """Test the PromptRegistry singleton."""

    def setup_method(self):
        """Reset singleton state between tests."""
        from self_fixing_engineer.prompt_registry import PromptRegistry
        instance = PromptRegistry()
        with instance._template_lock:
            instance._templates = {}
            instance._generation = 0
            instance._fitness = 0.0

    def test_singleton_instance(self):
        """PromptRegistry should be a singleton."""
        from self_fixing_engineer.prompt_registry import get_prompt_registry
        registry1 = get_prompt_registry()
        registry2 = get_prompt_registry()
        assert registry1 is registry2

    def test_update_and_get_template(self):
        """Should be able to update and retrieve a template."""
        from self_fixing_engineer.prompt_registry import get_prompt_registry
        registry = get_prompt_registry()
        registry.update_template("test_template", "Hello {name}")
        assert registry.get_template("test_template") == "Hello {name}"

    def test_get_missing_template_returns_default(self):
        """get_template should return the default when the key is absent."""
        from self_fixing_engineer.prompt_registry import get_prompt_registry
        registry = get_prompt_registry()
        assert registry.get_template("nonexistent", default="fallback") == "fallback"

    def test_update_all(self):
        """update_all should replace all templates and update stats."""
        from self_fixing_engineer.prompt_registry import get_prompt_registry
        registry = get_prompt_registry()
        templates = {"a": "Template A.", "b": "Template B."}
        registry.update_all(templates, generation=3, fitness=7.5)
        assert registry.get_template("a") == "Template A."
        stats = registry.get_stats()
        assert stats["generation"] == 3
        assert stats["fitness"] == 7.5
        assert stats["template_count"] == 2

    def test_get_all_returns_copy(self):
        """get_all should return an independent copy."""
        from self_fixing_engineer.prompt_registry import get_prompt_registry
        registry = get_prompt_registry()
        registry.update_template("x", "X template.")
        copy = registry.get_all()
        copy["x"] = "modified"
        assert registry.get_template("x") == "X template."

    def test_thread_safety(self):
        """Registry should be thread-safe."""
        import concurrent.futures
        from self_fixing_engineer.prompt_registry import get_prompt_registry

        registry = get_prompt_registry()

        def update_and_read(i: int) -> bool:
            registry.update_template(f"template_{i}", f"Content {i}")
            return registry.get_template(f"template_{i}") is not None

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            results = list(executor.map(update_and_read, range(50)))

        assert all(results)
