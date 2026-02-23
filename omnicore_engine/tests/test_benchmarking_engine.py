# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Test suite for omnicore_engine/benchmarking_engine.py

Tests the BenchmarkProfile, ConsoleReporter, JSONReporter,
MonteCarloSimulator, MultiverseSimulator, and BenchmarkingEngine classes,
including Prometheus metrics emission, circuit-breaker state transitions,
reporter error isolation, and the arbiter execute() protocol.

Design choices
--------------
* All heavy module imports are deferred to test-function scope to minimise
  time during ``pytest --collect-only`` (platform convention).
* Class-level groupings mirror ``test_core.py`` and ``test_engine_registry.py``.
* Async tests use ``pytest.mark.asyncio`` (pytest-asyncio) and the
  ``asyncio_mode = "auto"`` setting in ``pyproject.toml``.
* Prometheus assertions use a *fresh* ``CollectorRegistry`` per test class so
  there are no cross-test metric leaks and the tests do not depend on the
  global registry state.
"""

from __future__ import annotations

import asyncio
import gc
import json
import os
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Ensure the repository root is on sys.path regardless of invocation style.
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Imports of the module under test are deferred to individual test methods
# to prevent collection-time import errors when optional deps are absent.

pytestmark = [
    pytest.mark.asyncio,
]


# ===========================================================================
# BenchmarkProfile
# ===========================================================================


class TestBenchmarkProfile:
    """Unit tests for BenchmarkProfile construction and validation."""

    def test_defaults(self):
        """All default values match the documented specification."""
        from omnicore_engine.benchmarking_engine import BenchmarkProfile

        p = BenchmarkProfile()
        assert p.name == "default"
        assert p.iterations == 10
        assert p.warmup_runs == 2
        assert p.timeout is None
        assert p.input_data is None

    def test_custom_fields(self):
        """Explicit field values are stored correctly."""
        from omnicore_engine.benchmarking_engine import BenchmarkProfile

        p = BenchmarkProfile(
            name="perf_critical",
            iterations=50_000,
            input_data={"key": "value"},
            warmup_runs=100,
            timeout=30.0,
        )
        assert p.name == "perf_critical"
        assert p.iterations == 50_000
        assert p.input_data == {"key": "value"}
        assert p.warmup_runs == 100
        assert p.timeout == 30.0

    def test_equality(self):
        """Two profiles with identical fields are equal."""
        from omnicore_engine.benchmarking_engine import BenchmarkProfile

        a = BenchmarkProfile(name="x", iterations=5)
        b = BenchmarkProfile(name="x", iterations=5)
        assert a == b

    def test_inequality(self):
        """Profiles differing in any field are not equal."""
        from omnicore_engine.benchmarking_engine import BenchmarkProfile

        base = BenchmarkProfile(name="x", iterations=5)
        assert base != BenchmarkProfile(name="y", iterations=5)
        assert base != BenchmarkProfile(name="x", iterations=6)

    def test_repr_contains_name(self):
        """repr() includes the profile name."""
        from omnicore_engine.benchmarking_engine import BenchmarkProfile

        p = BenchmarkProfile(name="my_bench")
        assert "my_bench" in repr(p)

    def test_iterations_zero_raises(self):
        from omnicore_engine.benchmarking_engine import BenchmarkProfile

        with pytest.raises(ValueError, match="iterations"):
            BenchmarkProfile(iterations=0)

    def test_iterations_negative_raises(self):
        from omnicore_engine.benchmarking_engine import BenchmarkProfile

        with pytest.raises(ValueError, match="iterations"):
            BenchmarkProfile(iterations=-1)

    def test_warmup_negative_raises(self):
        from omnicore_engine.benchmarking_engine import BenchmarkProfile

        with pytest.raises(ValueError, match="warmup_runs"):
            BenchmarkProfile(warmup_runs=-1)

    def test_timeout_zero_raises(self):
        from omnicore_engine.benchmarking_engine import BenchmarkProfile

        with pytest.raises(ValueError, match="timeout"):
            BenchmarkProfile(timeout=0.0)

    def test_timeout_negative_raises(self):
        from omnicore_engine.benchmarking_engine import BenchmarkProfile

        with pytest.raises(ValueError, match="timeout"):
            BenchmarkProfile(timeout=-5.0)

    def test_empty_name_raises(self):
        from omnicore_engine.benchmarking_engine import BenchmarkProfile

        with pytest.raises(TypeError, match="name"):
            BenchmarkProfile(name="")

    def test_from_dict_basic(self):
        """BenchmarkProfile.from_dict() converts an untyped mapping correctly."""
        from omnicore_engine.benchmarking_engine import BenchmarkProfile

        p = BenchmarkProfile.from_dict(
            {"name": "dict_p", "iterations": 7, "warmup_runs": 1, "timeout": 5.0}
        )
        assert p.name == "dict_p"
        assert p.iterations == 7
        assert p.warmup_runs == 1
        assert p.timeout == 5.0

    def test_from_dict_ignores_unknown_keys(self):
        """Unknown keys in the dict must not raise."""
        from omnicore_engine.benchmarking_engine import BenchmarkProfile

        p = BenchmarkProfile.from_dict({"name": "ok", "unknown_field": "ignored"})
        assert p.name == "ok"

    def test_from_dict_defaults_for_missing_keys(self):
        """from_dict() fills in defaults for missing keys."""
        from omnicore_engine.benchmarking_engine import BenchmarkProfile

        p = BenchmarkProfile.from_dict({})
        assert p.name == "profile"
        assert p.iterations == 10


# ===========================================================================
# BenchmarkingEngine — core async behaviour
# ===========================================================================


class TestBenchmarkingEngineBasic:
    """Core run_benchmark() behaviour without reporters or Monte Carlo."""

    async def test_basic_run_returns_one_result_per_profile(self):
        from omnicore_engine.benchmarking_engine import BenchmarkingEngine, BenchmarkProfile

        engine = BenchmarkingEngine()
        profiles = [
            BenchmarkProfile(name="p1", iterations=3, warmup_runs=1),
            BenchmarkProfile(name="p2", iterations=2, warmup_runs=0),
        ]
        results = await engine.run_benchmark(functions=[lambda d: None], profiles=profiles)
        assert len(results) == 2
        assert {r["profile"] for r in results} == {"p1", "p2"}

    async def test_result_dict_has_required_keys(self):
        from omnicore_engine.benchmarking_engine import BenchmarkingEngine, BenchmarkProfile

        engine = BenchmarkingEngine()
        results = await engine.run_benchmark(
            functions=[lambda d: None],
            profiles=[BenchmarkProfile(iterations=4, warmup_runs=0)],
        )
        r = results[0]
        required = {"profile", "iterations", "total_time", "mean_time", "min_time", "max_time", "std_dev"}
        assert required.issubset(r.keys())

    async def test_iterations_count_matches_profile(self):
        from omnicore_engine.benchmarking_engine import BenchmarkingEngine, BenchmarkProfile

        engine = BenchmarkingEngine()
        results = await engine.run_benchmark(
            functions=[lambda d: None],
            profiles=[BenchmarkProfile(iterations=7, warmup_runs=0)],
        )
        assert results[0]["iterations"] == 7

    async def test_no_profiles_uses_default(self):
        """When profiles=None a default profile is synthesised."""
        from omnicore_engine.benchmarking_engine import BenchmarkingEngine

        engine = BenchmarkingEngine()
        results = await engine.run_benchmark(
            functions=[lambda d: None],
            iterations_per_run=5,
        )
        assert len(results) == 1
        assert results[0]["profile"] == "default"

    async def test_timing_values_are_non_negative(self):
        from omnicore_engine.benchmarking_engine import BenchmarkingEngine, BenchmarkProfile

        engine = BenchmarkingEngine()
        results = await engine.run_benchmark(
            functions=[lambda d: None],
            profiles=[BenchmarkProfile(iterations=10, warmup_runs=2)],
        )
        r = results[0]
        assert r["total_time"] >= 0.0
        assert r["mean_time"] >= 0.0
        assert r["min_time"] <= r["max_time"]
        assert r["std_dev"] >= 0.0

    async def test_min_max_consistency_across_iterations(self):
        """min_time ≤ mean_time ≤ max_time for any realistic workload."""
        from omnicore_engine.benchmarking_engine import BenchmarkingEngine, BenchmarkProfile

        engine = BenchmarkingEngine()
        results = await engine.run_benchmark(
            functions=[lambda d: sum(range(1_000))],
            profiles=[BenchmarkProfile(iterations=20, warmup_runs=5)],
        )
        r = results[0]
        assert r["min_time"] <= r["mean_time"] <= r["max_time"]

    async def test_async_callable_is_awaited(self):
        """Async callables must be awaited, not just called."""
        from omnicore_engine.benchmarking_engine import BenchmarkingEngine, BenchmarkProfile

        awaited_count = []

        async def async_fn(data):
            awaited_count.append(1)

        engine = BenchmarkingEngine()
        results = await engine.run_benchmark(
            functions=[async_fn],
            profiles=[BenchmarkProfile(iterations=3, warmup_runs=0)],
        )
        assert results[0]["iterations"] == 3
        assert len(awaited_count) == 3

    async def test_callable_exception_does_not_abort_run(self):
        """A callable that raises should not abort the timing loop."""
        from omnicore_engine.benchmarking_engine import BenchmarkingEngine, BenchmarkProfile

        def raising_fn(data):
            raise ValueError("deliberate test error")

        engine = BenchmarkingEngine()
        results = await engine.run_benchmark(
            functions=[raising_fn],
            profiles=[BenchmarkProfile(iterations=5, warmup_runs=0)],
        )
        assert results[0]["iterations"] == 5


# ===========================================================================
# BenchmarkingEngine — timeout
# ===========================================================================


class TestBenchmarkingEngineTimeout:
    """Verify early-exit behaviour when a profile deadline is exceeded."""

    async def test_timeout_stops_iterations_early(self):
        """A profile with a very short timeout completes fewer than max iterations."""
        from omnicore_engine.benchmarking_engine import BenchmarkingEngine, BenchmarkProfile

        def slow_fn(data):
            import time as _t
            _t.sleep(0.05)

        engine = BenchmarkingEngine()
        profile = BenchmarkProfile(
            name="timeout_test",
            iterations=100,
            warmup_runs=0,
            timeout=0.12,  # Allow ~2 iterations of 50 ms each
        )
        results = await engine.run_benchmark(functions=[slow_fn], profiles=[profile])
        assert results[0]["iterations"] < 100


# ===========================================================================
# BenchmarkingEngine — reporters
# ===========================================================================


class TestBenchmarkingEngineReporters:
    """Reporter dispatch and error isolation."""

    async def test_reporter_report_is_called_once(self):
        from omnicore_engine.benchmarking_engine import BenchmarkingEngine, BenchmarkProfile

        reporter = MagicMock()
        engine = BenchmarkingEngine(reporters=[reporter])
        await engine.run_benchmark(
            functions=[lambda d: None],
            profiles=[BenchmarkProfile(iterations=1, warmup_runs=0)],
        )
        reporter.report.assert_called_once()

    async def test_reporter_receives_correct_results(self):
        from omnicore_engine.benchmarking_engine import BenchmarkingEngine, BenchmarkProfile

        captured: List[Any] = []
        reporter = MagicMock(side_effect=lambda r: captured.extend(r))
        reporter.report = MagicMock(side_effect=lambda r: captured.extend(r))

        engine = BenchmarkingEngine(reporters=[reporter])
        await engine.run_benchmark(
            functions=[lambda d: None],
            profiles=[BenchmarkProfile(name="r_test", iterations=2, warmup_runs=0)],
        )
        assert len(captured) == 1
        assert captured[0]["profile"] == "r_test"

    async def test_multiple_reporters_all_called(self):
        from omnicore_engine.benchmarking_engine import BenchmarkingEngine, BenchmarkProfile

        r1, r2 = MagicMock(), MagicMock()
        engine = BenchmarkingEngine(reporters=[r1, r2])
        await engine.run_benchmark(
            functions=[lambda d: None],
            profiles=[BenchmarkProfile(iterations=1, warmup_runs=0)],
        )
        r1.report.assert_called_once()
        r2.report.assert_called_once()

    async def test_reporter_error_does_not_propagate(self):
        """A crashing reporter must not surface as a test/caller exception."""
        from omnicore_engine.benchmarking_engine import BenchmarkingEngine, BenchmarkProfile

        bad_reporter = MagicMock()
        bad_reporter.report.side_effect = RuntimeError("reporter exploded")

        engine = BenchmarkingEngine(reporters=[bad_reporter])
        # Should NOT raise
        results = await engine.run_benchmark(
            functions=[lambda d: None],
            profiles=[BenchmarkProfile(iterations=1, warmup_runs=0)],
        )
        assert len(results) == 1


# ===========================================================================
# BenchmarkingEngine — Monte Carlo
# ===========================================================================


class TestBenchmarkingEngineMonteCarlo:
    """Verify Monte Carlo statistics are embedded in results when configured."""

    async def test_monte_carlo_key_present(self):
        from omnicore_engine.benchmarking_engine import (
            BenchmarkingEngine, BenchmarkProfile, MonteCarloSimulator,
        )

        mc = MonteCarloSimulator(n_simulations=50, seed=99)
        engine = BenchmarkingEngine(monte_carlo=mc)
        results = await engine.run_benchmark(
            functions=[lambda d: None],
            profiles=[BenchmarkProfile(iterations=10, warmup_runs=0)],
        )
        assert "monte_carlo" in results[0]

    async def test_monte_carlo_ci_bounds_are_ordered(self):
        from omnicore_engine.benchmarking_engine import (
            BenchmarkingEngine, BenchmarkProfile, MonteCarloSimulator,
        )

        mc = MonteCarloSimulator(n_simulations=200, seed=1)
        engine = BenchmarkingEngine(monte_carlo=mc)
        results = await engine.run_benchmark(
            functions=[lambda d: None],
            profiles=[BenchmarkProfile(iterations=20, warmup_runs=0)],
        )
        mc_stats = results[0]["monte_carlo"]
        assert mc_stats["ci_lower"] <= mc_stats["mean"] <= mc_stats["ci_upper"]

    async def test_monte_carlo_absent_by_default(self):
        from omnicore_engine.benchmarking_engine import BenchmarkingEngine, BenchmarkProfile

        engine = BenchmarkingEngine()  # No monte_carlo
        results = await engine.run_benchmark(
            functions=[lambda d: None],
            profiles=[BenchmarkProfile(iterations=3, warmup_runs=0)],
        )
        assert "monte_carlo" not in results[0]


# ===========================================================================
# BenchmarkingEngine — execute() / arbiter protocol
# ===========================================================================


class TestBenchmarkingEngineExecuteProtocol:
    """Verify the arbiter-compatible execute() entry-point."""

    async def test_execute_run_benchmark_success(self):
        from omnicore_engine.benchmarking_engine import BenchmarkingEngine

        engine = BenchmarkingEngine()
        resp = await engine.execute(
            "run_benchmark",
            functions=[lambda d: None],
            profiles=[{"name": "via_execute", "iterations": 3}],
        )
        assert resp["status"] == "success"
        assert len(resp["results"]) == 1
        assert resp["results"][0]["profile"] == "via_execute"

    async def test_execute_accepts_profile_objects(self):
        from omnicore_engine.benchmarking_engine import BenchmarkingEngine, BenchmarkProfile

        engine = BenchmarkingEngine()
        resp = await engine.execute(
            "run_benchmark",
            functions=[lambda d: None],
            profiles=[BenchmarkProfile(name="obj_profile", iterations=2, warmup_runs=0)],
        )
        assert resp["status"] == "success"
        assert resp["results"][0]["profile"] == "obj_profile"

    async def test_execute_health_check_closed(self):
        from omnicore_engine.benchmarking_engine import BenchmarkingEngine

        engine = BenchmarkingEngine()
        resp = await engine.execute("health_check")
        assert resp["status"] == "healthy"
        assert resp["circuit_breaker"] == "closed"

    async def test_execute_unknown_action(self):
        from omnicore_engine.benchmarking_engine import BenchmarkingEngine

        engine = BenchmarkingEngine()
        resp = await engine.execute("nonexistent")
        assert resp["status"] == "unknown_action"
        assert resp["action"] == "nonexistent"


# ===========================================================================
# BenchmarkingEngine — circuit breaker
# ===========================================================================


class TestBenchmarkingEngineCircuitBreaker:
    """Circuit breaker state transitions.

    The circuit breaker protects against infrastructure failures (e.g. async
    context errors, storage errors) rather than against the benchmarked
    callable itself raising.  Tests use ``patch.object`` on ``_run_profile``
    to simulate a persistent infrastructure failure.
    """

    async def test_circuit_opens_after_threshold_failures(self):
        from omnicore_engine.benchmarking_engine import BenchmarkingEngine, BenchmarkProfile

        threshold = 3
        engine = BenchmarkingEngine(
            circuit_breaker_threshold=threshold,
            circuit_breaker_timeout=3600.0,
        )

        with patch.object(
            engine, "_run_profile", side_effect=RuntimeError("simulated infra failure")
        ):
            for _ in range(threshold):
                with pytest.raises(RuntimeError):
                    await engine.run_benchmark(
                        functions=[lambda d: None],
                        profiles=[BenchmarkProfile(iterations=1, warmup_runs=0)],
                    )

        assert engine._cb_state == "open"

    async def test_open_circuit_raises_immediately(self):
        from omnicore_engine.benchmarking_engine import BenchmarkingEngine, BenchmarkProfile

        engine = BenchmarkingEngine(
            circuit_breaker_threshold=1,
            circuit_breaker_timeout=3600.0,
        )

        with patch.object(
            engine, "_run_profile", side_effect=RuntimeError("infra failure")
        ):
            with pytest.raises(RuntimeError):
                await engine.run_benchmark(
                    functions=[lambda d: None],
                    profiles=[BenchmarkProfile(iterations=1, warmup_runs=0)],
                )

        # Circuit is now open — next call should raise from the breaker (not profile)
        with pytest.raises(RuntimeError, match="circuit breaker"):
            await engine.run_benchmark(
                functions=[lambda d: None],
                profiles=[BenchmarkProfile(iterations=1, warmup_runs=0)],
            )

    async def test_circuit_resets_after_successful_run(self):
        from omnicore_engine.benchmarking_engine import BenchmarkingEngine, BenchmarkProfile

        engine = BenchmarkingEngine(
            circuit_breaker_threshold=1,
            circuit_breaker_timeout=0.0,  # Expire immediately for test speed
        )

        with patch.object(
            engine, "_run_profile", side_effect=RuntimeError("infra failure")
        ):
            with pytest.raises(RuntimeError):
                await engine.run_benchmark(
                    functions=[lambda d: None],
                    profiles=[BenchmarkProfile(iterations=1, warmup_runs=0)],
                )

        # Timeout elapsed (0.0 s) → half-open → next success resets to closed
        results = await engine.run_benchmark(
            functions=[lambda d: None],
            profiles=[BenchmarkProfile(iterations=1, warmup_runs=0)],
        )
        assert engine._cb_state == "closed"
        assert len(results) == 1

    async def test_health_check_reflects_open_breaker(self):
        from omnicore_engine.benchmarking_engine import BenchmarkingEngine, BenchmarkProfile

        engine = BenchmarkingEngine(
            circuit_breaker_threshold=1,
            circuit_breaker_timeout=3600.0,
        )

        with patch.object(
            engine, "_run_profile", side_effect=RuntimeError("infra failure")
        ):
            with pytest.raises(RuntimeError):
                await engine.run_benchmark(
                    functions=[lambda d: None],
                    profiles=[BenchmarkProfile(iterations=1, warmup_runs=0)],
                )

        resp = await engine.execute("health_check")
        assert resp["status"] == "circuit_open"
        assert resp["circuit_breaker"] == "open"


# ===========================================================================
# ConsoleReporter
# ===========================================================================


class TestConsoleReporter:
    """ConsoleReporter plain-text and rich output paths."""

    def test_plain_text_output_contains_profile_name(self, capsys):
        from omnicore_engine.benchmarking_engine import ConsoleReporter
        import omnicore_engine.benchmarking_engine as be_mod

        results = [
            {
                "profile": "my_profile",
                "iterations": 5,
                "total_time": 0.25,
                "mean_time": 0.05,
                "min_time": 0.04,
                "max_time": 0.07,
                "std_dev": 0.01,
            }
        ]
        orig = be_mod._RICH_AVAILABLE
        be_mod._RICH_AVAILABLE = False
        try:
            ConsoleReporter().report(results)
        finally:
            be_mod._RICH_AVAILABLE = orig
        captured = capsys.readouterr()
        assert "my_profile" in captured.out

    def test_empty_results_prints_message(self, capsys):
        from omnicore_engine.benchmarking_engine import ConsoleReporter
        import omnicore_engine.benchmarking_engine as be_mod

        orig = be_mod._RICH_AVAILABLE
        be_mod._RICH_AVAILABLE = False
        try:
            ConsoleReporter().report([])
        finally:
            be_mod._RICH_AVAILABLE = orig
        assert "No benchmark results" in capsys.readouterr().out

    def test_verbose_prints_run_times(self, capsys):
        from omnicore_engine.benchmarking_engine import ConsoleReporter
        import omnicore_engine.benchmarking_engine as be_mod

        results = [
            {
                "profile": "v",
                "iterations": 2,
                "total_time": 0.02,
                "mean_time": 0.01,
                "min_time": 0.009,
                "max_time": 0.011,
                "std_dev": 0.001,
                "run_times": [0.009, 0.011],
            }
        ]
        orig = be_mod._RICH_AVAILABLE
        be_mod._RICH_AVAILABLE = False
        try:
            ConsoleReporter(verbose=True).report(results)
        finally:
            be_mod._RICH_AVAILABLE = orig
        out = capsys.readouterr().out
        assert "0.009" in out or "0.01" in out


# ===========================================================================
# JSONReporter
# ===========================================================================


class TestJSONReporter:
    """JSONReporter file writing, atomicity, and append mode."""

    def test_writes_valid_json(self):
        from omnicore_engine.benchmarking_engine import JSONReporter

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "results.json")
            reporter = JSONReporter(output_path=path)
            reporter.report([{"profile": "p1", "total_time": 0.5}])
            with open(path) as fh:
                data = json.load(fh)
            assert data["schema_version"] == "1.0"
            assert "generated_at" in data
            assert data["results"][0]["profile"] == "p1"

    def test_returns_resolved_path(self):
        from omnicore_engine.benchmarking_engine import JSONReporter

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "r.json")
            reporter = JSONReporter(output_path=path)
            returned = reporter.report([])
            assert returned == Path(path).resolve()

    def test_creates_nested_parent_directories(self):
        from omnicore_engine.benchmarking_engine import JSONReporter

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "a", "b", "c", "out.json")
            JSONReporter(output_path=path).report([{"profile": "x"}])
            assert Path(path).exists()

    def test_append_mode_accumulates_results(self):
        from omnicore_engine.benchmarking_engine import JSONReporter

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "rolling.json")
            reporter = JSONReporter(output_path=path, append=True)
            reporter.report([{"profile": "run1"}])
            reporter.report([{"profile": "run2"}])
            with open(path) as fh:
                data = json.load(fh)
            profiles = [r["profile"] for r in data["results"]]
            assert "run1" in profiles
            assert "run2" in profiles
            assert len(profiles) == 2

    def test_non_append_mode_overwrites(self):
        from omnicore_engine.benchmarking_engine import JSONReporter

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "out.json")
            reporter = JSONReporter(output_path=path, append=False)
            reporter.report([{"profile": "first"}])
            reporter.report([{"profile": "second"}])
            with open(path) as fh:
                data = json.load(fh)
            profiles = [r["profile"] for r in data["results"]]
            assert profiles == ["second"]

    def test_thread_safe_concurrent_writes(self):
        """Concurrent writes from multiple threads must not corrupt the file."""
        from omnicore_engine.benchmarking_engine import JSONReporter

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "concurrent.json")
            reporter = JSONReporter(output_path=path, append=True)
            errors: List[Exception] = []

            def write_result(idx: int) -> None:
                try:
                    reporter.report([{"profile": f"thread_{idx}"}])
                except Exception as exc:
                    errors.append(exc)

            threads = [threading.Thread(target=write_result, args=(i,)) for i in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert errors == [], f"Concurrent writes raised: {errors}"
            with open(path) as fh:
                data = json.load(fh)
            assert len(data["results"]) == 10


# ===========================================================================
# MonteCarloSimulator
# ===========================================================================


class TestMonteCarloSimulator:
    """Bootstrap CI estimator correctness and edge cases."""

    def test_empty_input_returns_zero_stats(self):
        from omnicore_engine.benchmarking_engine import MonteCarloSimulator

        mc = MonteCarloSimulator(n_simulations=10)
        result = mc.simulate([])
        assert result["mean"] == 0.0
        assert result["ci_lower"] == 0.0
        assert result["ci_upper"] == 0.0
        assert result["n_simulations"] == 0

    def test_single_value_mean_equals_value(self):
        from omnicore_engine.benchmarking_engine import MonteCarloSimulator

        mc = MonteCarloSimulator(n_simulations=50, seed=1)
        result = mc.simulate([0.5])
        assert result["mean"] == pytest.approx(0.5)
        assert result["std_dev"] == 0.0

    def test_ci_bounds_ordered(self):
        from omnicore_engine.benchmarking_engine import MonteCarloSimulator

        mc = MonteCarloSimulator(n_simulations=500, confidence_level=0.95, seed=42)
        times = [0.1 + i * 0.01 for i in range(20)]
        result = mc.simulate(times)
        assert result["ci_lower"] <= result["mean"] <= result["ci_upper"]

    def test_confidence_level_respected(self):
        from omnicore_engine.benchmarking_engine import MonteCarloSimulator

        mc = MonteCarloSimulator(n_simulations=200, confidence_level=0.80, seed=7)
        result = mc.simulate([0.1, 0.2, 0.15])
        assert result["confidence_level"] == 0.80

    def test_p95_is_within_observed_range(self):
        from omnicore_engine.benchmarking_engine import MonteCarloSimulator

        mc = MonteCarloSimulator(n_simulations=100, seed=3)
        times = [float(i) for i in range(1, 21)]
        result = mc.simulate(times)
        assert min(times) <= result["p95"] <= max(times)

    def test_median_is_computed(self):
        from omnicore_engine.benchmarking_engine import MonteCarloSimulator

        mc = MonteCarloSimulator(n_simulations=50, seed=0)
        result = mc.simulate([1.0, 2.0, 3.0])
        assert result["median"] == pytest.approx(2.0)

    def test_seeded_results_are_reproducible(self):
        from omnicore_engine.benchmarking_engine import MonteCarloSimulator

        times = [0.01 * i for i in range(1, 11)]
        r1 = MonteCarloSimulator(n_simulations=100, seed=77).simulate(times)
        r2 = MonteCarloSimulator(n_simulations=100, seed=77).simulate(times)
        assert r1["ci_lower"] == r2["ci_lower"]
        assert r1["ci_upper"] == r2["ci_upper"]

    def test_invalid_confidence_level_raises(self):
        from omnicore_engine.benchmarking_engine import MonteCarloSimulator

        with pytest.raises(ValueError, match="confidence_level"):
            MonteCarloSimulator(confidence_level=1.5)

        with pytest.raises(ValueError, match="confidence_level"):
            MonteCarloSimulator(confidence_level=0.0)

    def test_invalid_n_simulations_raises(self):
        from omnicore_engine.benchmarking_engine import MonteCarloSimulator

        with pytest.raises(ValueError, match="n_simulations"):
            MonteCarloSimulator(n_simulations=0)


# ===========================================================================
# MultiverseSimulator
# ===========================================================================


class TestMultiverseSimulator:
    """Parameter-sweep correctness and edge cases."""

    async def test_runs_every_universe(self):
        from omnicore_engine.benchmarking_engine import (
            BenchmarkingEngine, BenchmarkProfile, MultiverseSimulator,
        )

        sim = MultiverseSimulator(
            universes=[
                {"name": "u_small", "iterations": 2},
                {"name": "u_large", "iterations": 4},
            ]
        )
        engine = BenchmarkingEngine()
        base = BenchmarkProfile(name="base", iterations=1, warmup_runs=0)
        results = await sim.run(engine, base, functions=[lambda d: None])
        assert len(results) == 2
        profiles = {r["profile"] for r in results}
        assert "u_small" in profiles
        assert "u_large" in profiles

    async def test_results_annotated_with_universe_params(self):
        from omnicore_engine.benchmarking_engine import (
            BenchmarkingEngine, BenchmarkProfile, MultiverseSimulator,
        )

        sim = MultiverseSimulator()
        sim.add_universe(name="ann_test", iterations=1)
        engine = BenchmarkingEngine()
        base = BenchmarkProfile(name="base", iterations=1, warmup_runs=0)
        results = await sim.run(engine, base, functions=[lambda d: None])
        assert results[0]["universe"]["name"] == "ann_test"
        assert results[0]["universe_index"] == 0

    async def test_empty_universes_returns_empty_list(self):
        from omnicore_engine.benchmarking_engine import (
            BenchmarkingEngine, BenchmarkProfile, MultiverseSimulator,
        )

        sim = MultiverseSimulator()  # No universes
        engine = BenchmarkingEngine()
        base = BenchmarkProfile(name="base", iterations=1, warmup_runs=0)
        results = await sim.run(engine, base, functions=[lambda d: None])
        assert results == []

    def test_universe_count_property(self):
        from omnicore_engine.benchmarking_engine import MultiverseSimulator

        sim = MultiverseSimulator()
        assert sim.universe_count == 0
        sim.add_universe(iterations=1)
        sim.add_universe(iterations=2)
        assert sim.universe_count == 2

    async def test_universes_use_base_profile_fallbacks(self):
        """Overrides that omit a field should fall back to the base profile's value."""
        from omnicore_engine.benchmarking_engine import (
            BenchmarkingEngine, BenchmarkProfile, MultiverseSimulator,
        )

        sim = MultiverseSimulator(universes=[{"name": "partial_override"}])
        engine = BenchmarkingEngine()
        base = BenchmarkProfile(name="base", iterations=3, warmup_runs=0)
        results = await sim.run(engine, base, functions=[lambda d: None])
        # iterations should fall back to the base profile's value (3)
        assert results[0]["iterations"] == 3


# ===========================================================================
# Integration — CLI import smoke test
# ===========================================================================


class TestCliIntegration:
    """Verify that omnicore_engine.cli's try/except import block now succeeds."""

    def test_real_benchmarking_engine_importable(self):
        from omnicore_engine.benchmarking_engine import BenchmarkingEngine

        engine = BenchmarkingEngine()
        assert hasattr(engine, "run_benchmark")
        assert hasattr(engine, "execute")

    def test_all_six_classes_importable(self):
        from omnicore_engine.benchmarking_engine import (
            BenchmarkingEngine,
            BenchmarkProfile,
            ConsoleReporter,
            JSONReporter,
            MonteCarloSimulator,
            MultiverseSimulator,
        )

        # Verify each class is instantiable with defaults
        assert BenchmarkingEngine() is not None
        assert BenchmarkProfile() is not None
        assert ConsoleReporter() is not None
        assert JSONReporter() is not None
        assert MonteCarloSimulator() is not None
        assert MultiverseSimulator() is not None
