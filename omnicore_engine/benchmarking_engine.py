# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
omnicore_engine.benchmarking_engine
====================================
Production-ready performance benchmarking subsystem for the OmniCore Omega Pro platform.

Architecture
------------
This module provides a full benchmarking lifecycle built to the same engineering
standard as every other subsystem in the OmniCore platform:

* **BenchmarkProfile** — immutable, validated benchmark scenario descriptor.
* **ConsoleReporter** — rich-formatted or plain-text result printer.
* **JSONReporter** — atomic JSON persistence with ISO-8601 timestamps.
* **MonteCarloSimulator** — bootstrap confidence-interval estimator.
* **MultiverseSimulator** — parameter-sweep orchestrator across universes.
* **BenchmarkingEngine** — async orchestrator with Prometheus instrumentation,
  OpenTelemetry tracing, per-profile circuit-breaker, and structured logging.

Design Decisions
----------------
* All Prometheus metrics are created via :mod:`omnicore_engine.metrics_utils`
  (thread-safe, idempotent, no-op-fallback) so the module is safe to import
  during ``pytest --collect-only`` without a running Prometheus server.
* OpenTelemetry spans are obtained through the shared
  ``omnicore_engine._tracer`` lazy-init helper so the module degrades
  gracefully when the OTel SDK is not installed.
* The engine exposes *both* a high-level async ``run_benchmark()`` API and
  the arbiter-style ``execute(action, **kwargs)`` entry-point so it can be
  used as a standalone library or as a registered plugin.
* ``BenchmarkProfile`` is a plain ``__slots__``-bearing class (not Pydantic)
  to keep import time negligible and avoid a hard Pydantic dependency at the
  engine level.

Usage
-----
::

    from omnicore_engine.benchmarking_engine import (
        BenchmarkingEngine, BenchmarkProfile, ConsoleReporter, JSONReporter,
        MonteCarloSimulator, MultiverseSimulator,
    )

    engine = BenchmarkingEngine(
        reporters=[ConsoleReporter()],
        monte_carlo=MonteCarloSimulator(n_simulations=500, seed=42),
    )

    profile = BenchmarkProfile(name="sha256_10k", iterations=10_000, warmup_runs=100)
    results = await engine.run_benchmark(
        functions=[lambda d: __import__('hashlib').sha256(b"x").digest()],
        profiles=[profile],
    )
"""

from __future__ import annotations

import asyncio
import json
import logging
import statistics
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Generator, Iterator, List, Optional, Sequence

# ---------------------------------------------------------------------------
# Module-level logger (structured, platform-standard)
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prometheus metrics — lazy, thread-safe, no-op-fallback via metrics_utils
# ---------------------------------------------------------------------------

from omnicore_engine.metrics_utils import get_or_create_metric  # noqa: E402

try:
    from prometheus_client import Counter, Histogram

    _PROMETHEUS_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PROMETHEUS_AVAILABLE = False
    Counter = None  # type: ignore[assignment,misc]
    Histogram = None  # type: ignore[assignment,misc]

_BENCH_RUNS_TOTAL: Any = get_or_create_metric(
    Counter,
    "benchmarking_engine_runs_total",
    "Total benchmark profile runs dispatched",
    ("profile", "status"),
)
_BENCH_DURATION_SECONDS: Any = get_or_create_metric(
    Histogram,
    "benchmarking_engine_run_duration_seconds",
    "Wall-clock duration of a completed benchmark profile run",
    ("profile",),
    buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0, 30.0, 60.0),
)
_BENCH_TIMEOUT_TOTAL: Any = get_or_create_metric(
    Counter,
    "benchmarking_engine_timeouts_total",
    "Total benchmark profiles aborted due to timeout",
    ("profile",),
)
_BENCH_REPORTER_ERRORS_TOTAL: Any = get_or_create_metric(
    Counter,
    "benchmarking_engine_reporter_errors_total",
    "Total errors raised by result reporters",
    ("reporter",),
)

# ---------------------------------------------------------------------------
# OpenTelemetry tracer — graceful degradation when OTel SDK is absent
# ---------------------------------------------------------------------------

try:
    from self_fixing_engineer.arbiter.otel_config import get_tracer_safe as _get_tracer_safe

    _tracer: Any = _get_tracer_safe(__name__)
except ImportError:  # pragma: no cover
    # Minimal no-op tracer so engine code never has ``if tracer is None`` guards.
    class _NoOpSpan:
        def __enter__(self) -> "_NoOpSpan":
            return self

        def __exit__(self, *_: Any) -> None:
            pass

        def set_attribute(self, *_: Any, **__: Any) -> None:
            pass

    class _NoOpTracer:
        def start_as_current_span(self, name: str, **kwargs: Any) -> "_NoOpSpan":  # noqa: ARG002
            return _NoOpSpan()

    _tracer = _NoOpTracer()  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Optional rich console — used by ConsoleReporter
# ---------------------------------------------------------------------------

try:
    from rich.console import Console as _RichConsole
    from rich.table import Table as _RichTable

    _RICH_AVAILABLE = True
except ImportError:  # pragma: no cover
    _RICH_AVAILABLE = False


# ===========================================================================
# BenchmarkProfile
# ===========================================================================


class BenchmarkProfile:
    """Immutable, validated descriptor for a single benchmark scenario.

    Parameters
    ----------
    name:
        Human-readable identifier used in logs, metrics labels, and reports.
    iterations:
        Number of timed invocations performed after the warm-up phase.
        Must be ≥ 1.
    input_data:
        Arbitrary value forwarded to every benchmarked callable as its sole
        positional argument.  Pass ``None`` for zero-argument functions.
    warmup_runs:
        Number of un-timed invocations performed before measurement begins.
        Warm-up populates CPU caches and JIT tiers.  Must be ≥ 0.
    timeout:
        Maximum wall-clock seconds for the entire timed phase.  A ``None``
        value means no limit.  When the deadline is exceeded the profile
        finishes early with the timings collected so far.

    Raises
    ------
    ValueError
        If *iterations* < 1 or *warmup_runs* < 0 or *timeout* ≤ 0.
    TypeError
        If *name* is not a ``str``.
    """

    __slots__ = ("name", "iterations", "input_data", "warmup_runs", "timeout")

    def __init__(
        self,
        name: str = "default",
        iterations: int = 10,
        input_data: Any = None,
        warmup_runs: int = 2,
        timeout: Optional[float] = None,
    ) -> None:
        if not isinstance(name, str) or not name:
            raise TypeError("BenchmarkProfile.name must be a non-empty str.")
        if not isinstance(iterations, int) or iterations < 1:
            raise ValueError("BenchmarkProfile.iterations must be an integer ≥ 1.")
        if not isinstance(warmup_runs, int) or warmup_runs < 0:
            raise ValueError("BenchmarkProfile.warmup_runs must be an integer ≥ 0.")
        if timeout is not None and (not isinstance(timeout, (int, float)) or timeout <= 0):
            raise ValueError("BenchmarkProfile.timeout must be a positive number or None.")

        self.name: str = name
        self.iterations: int = iterations
        self.input_data: Any = input_data
        self.warmup_runs: int = warmup_runs
        self.timeout: Optional[float] = timeout

    def __repr__(self) -> str:
        return (
            f"BenchmarkProfile("
            f"name={self.name!r}, "
            f"iterations={self.iterations}, "
            f"warmup_runs={self.warmup_runs}, "
            f"timeout={self.timeout!r})"
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, BenchmarkProfile):
            return NotImplemented
        return (
            self.name == other.name
            and self.iterations == other.iterations
            and self.input_data == other.input_data
            and self.warmup_runs == other.warmup_runs
            and self.timeout == other.timeout
        )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BenchmarkProfile":
        """Construct a ``BenchmarkProfile`` from an untyped mapping.

        Unknown keys are silently ignored so that callers can pass richer
        dicts (e.g. from YAML configuration files) without stripping fields
        beforehand.
        """
        return cls(
            name=data.get("name", "profile"),
            iterations=int(data.get("iterations", 10)),
            input_data=data.get("input_data"),
            warmup_runs=int(data.get("warmup_runs", 2)),
            timeout=data.get("timeout"),
        )


# ===========================================================================
# ConsoleReporter
# ===========================================================================


class ConsoleReporter:
    """Prints benchmark results to stdout.

    Uses *rich* table formatting when the ``rich`` package is installed;
    falls back to plain-text columnar output otherwise so that the reporter
    is usable in environments without optional display dependencies.

    Parameters
    ----------
    verbose:
        When ``True``, per-iteration timing arrays are printed beneath the
        summary table.
    width:
        Terminal width hint forwarded to ``rich.Console``.  Ignored in
        plain-text mode.
    """

    def __init__(self, verbose: bool = False, width: int = 100) -> None:
        self.verbose = verbose
        self.width = width

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def report(self, results: List[Dict[str, Any]]) -> None:
        """Print *results* to ``stdout``.

        Parameters
        ----------
        results:
            List of result dicts as returned by
            :meth:`BenchmarkingEngine.run_benchmark`.
        """
        if not results:
            print("No benchmark results to display.")
            return

        if _RICH_AVAILABLE:
            self._report_rich(results)
        else:
            self._report_plain(results)

        if self.verbose:
            for r in results:
                run_times = r.get("run_times", [])
                if run_times:
                    print(
                        f"  [{r.get('profile', '?')}] "
                        f"per-iteration (s): {', '.join(f'{t:.6f}' for t in run_times)}"
                    )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _report_plain(self, results: List[Dict[str, Any]]) -> None:
        header = (
            f"{'Profile':<30} {'Iters':>6} {'Total (s)':>12} "
            f"{'Mean (s)':>12} {'Min (s)':>12} {'Max (s)':>12} {'StdDev':>10}"
        )
        print(f"\n{'='*96}")
        print("  Benchmark Results")
        print(f"{'='*96}")
        print(f"  {header}")
        print(f"  {'-'*94}")
        for r in results:
            print(
                f"  {r.get('profile', '?'):<30} "
                f"{r.get('iterations', 0):>6} "
                f"{r.get('total_time', 0):>12.6f} "
                f"{r.get('mean_time', 0):>12.6f} "
                f"{r.get('min_time', 0):>12.6f} "
                f"{r.get('max_time', 0):>12.6f} "
                f"{r.get('std_dev', 0):>10.6f}"
            )
        print(f"{'='*96}\n")

    def _report_rich(self, results: List[Dict[str, Any]]) -> None:
        console = _RichConsole(width=self.width)
        table = _RichTable(
            title="[bold cyan]Benchmark Results[/bold cyan]",
            show_lines=True,
            border_style="bright_black",
        )
        table.add_column("Profile", style="cyan", no_wrap=True)
        table.add_column("Iters", justify="right", style="white")
        table.add_column("Total (s)", justify="right", style="green")
        table.add_column("Mean (s)", justify="right", style="yellow")
        table.add_column("Min (s)", justify="right", style="blue")
        table.add_column("Max (s)", justify="right", style="red")
        table.add_column("Std Dev", justify="right", style="magenta")
        for r in results:
            mc = r.get("monte_carlo")
            ci_str = (
                f"  [dim]CI [{mc['ci_lower']:.6f}, {mc['ci_upper']:.6f}][/dim]"
                if mc
                else ""
            )
            table.add_row(
                f"{r.get('profile', '?')}{ci_str}",
                str(r.get("iterations", 0)),
                f"{r.get('total_time', 0):.6f}",
                f"{r.get('mean_time', 0):.6f}",
                f"{r.get('min_time', 0):.6f}",
                f"{r.get('max_time', 0):.6f}",
                f"{r.get('std_dev', 0):.6f}",
            )
        console.print(table)


# ===========================================================================
# JSONReporter
# ===========================================================================


class JSONReporter:
    """Serialises benchmark results to a JSON file atomically.

    Writes to a sibling ``*.tmp`` file and renames it to the target path so
    that a crash mid-write never leaves a partially-written result file.

    Parameters
    ----------
    output_path:
        Destination file.  Parent directories are created as needed.
        Defaults to ``benchmark_results.json`` in the current working
        directory.
    indent:
        JSON indentation level for human-readable output.
    append:
        When ``True``, existing results are loaded and the new *results* are
        appended before writing, creating a rolling history file.
    """

    def __init__(
        self,
        output_path: str = "benchmark_results.json",
        indent: int = 2,
        append: bool = False,
    ) -> None:
        self.output_path = Path(output_path)
        self.indent = indent
        self.append = append
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def report(self, results: List[Dict[str, Any]]) -> Path:
        """Write *results* to the configured JSON file.

        Returns
        -------
        Path
            Absolute path of the file that was written.
        """
        with self._lock:
            self.output_path.parent.mkdir(parents=True, exist_ok=True)

            existing: List[Dict[str, Any]] = []
            if self.append and self.output_path.exists():
                try:
                    with self.output_path.open("r", encoding="utf-8") as fh:
                        data = json.load(fh)
                    existing = data.get("results", [])
                except (json.JSONDecodeError, OSError) as exc:
                    logger.warning(
                        "JSONReporter: could not read existing results; starting fresh.",
                        extra={"path": str(self.output_path), "error": str(exc)},
                    )

            payload: Dict[str, Any] = {
                "schema_version": "1.0",
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "results": existing + results,
            }

            # Atomic write: write to .tmp then rename.
            tmp_path = self.output_path.with_suffix(".tmp")
            try:
                with tmp_path.open("w", encoding="utf-8") as fh:
                    json.dump(payload, fh, indent=self.indent, default=str)
                tmp_path.replace(self.output_path)
            except OSError:
                # Clean up partial file if rename failed.
                if tmp_path.exists():
                    tmp_path.unlink(missing_ok=True)
                raise

        logger.info(
            "JSONReporter: results written.",
            extra={
                "path": str(self.output_path),
                "result_count": len(results),
            },
        )
        return self.output_path.resolve()


# ===========================================================================
# MonteCarloSimulator
# ===========================================================================


class MonteCarloSimulator:
    """Bootstrap confidence-interval estimator for benchmark timing samples.

    Generates *n_simulations* re-samples (with replacement) from the observed
    run-time distribution and uses the empirical quantiles to construct a
    confidence interval around the population mean.

    Parameters
    ----------
    n_simulations:
        Number of bootstrap re-samples.  Higher values reduce sampling error
        at the cost of CPU time.  1 000 is a reasonable production default.
    confidence_level:
        Width of the confidence interval expressed as a probability in
        (0, 1).  ``0.95`` → 95 % CI.
    seed:
        Optional integer seed for the internal ``random.Random`` instance,
        enabling deterministic results in tests.

    Raises
    ------
    ValueError
        If *confidence_level* ≤ 0 or ≥ 1, or *n_simulations* < 1.
    """

    def __init__(
        self,
        n_simulations: int = 1_000,
        confidence_level: float = 0.95,
        seed: Optional[int] = None,
    ) -> None:
        if not isinstance(n_simulations, int) or n_simulations < 1:
            raise ValueError("MonteCarloSimulator.n_simulations must be an integer ≥ 1.")
        if not (0.0 < confidence_level < 1.0):
            raise ValueError(
                "MonteCarloSimulator.confidence_level must be strictly between 0 and 1."
            )
        self.n_simulations = n_simulations
        self.confidence_level = confidence_level
        self._rng_seed = seed

        import random as _random

        self._rng = _random.Random(seed)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def simulate(self, run_times: Sequence[float]) -> Dict[str, Any]:
        """Compute bootstrap CI statistics for *run_times*.

        Parameters
        ----------
        run_times:
            Observed iteration timings in seconds.

        Returns
        -------
        dict
            Keys: ``mean``, ``std_dev``, ``median``, ``p95``,
            ``ci_lower``, ``ci_upper``, ``n_simulations``,
            ``confidence_level``.
        """
        times = list(run_times)
        if not times:
            return {
                "mean": 0.0,
                "std_dev": 0.0,
                "median": 0.0,
                "p95": 0.0,
                "ci_lower": 0.0,
                "ci_upper": 0.0,
                "n_simulations": 0,
                "confidence_level": self.confidence_level,
            }

        n = len(times)
        bootstrap_means: List[float] = []
        for _ in range(self.n_simulations):
            sample = [self._rng.choice(times) for _ in range(n)]
            bootstrap_means.append(sum(sample) / n)

        bootstrap_means.sort()
        alpha = 1.0 - self.confidence_level
        lo_idx = max(int(alpha / 2.0 * self.n_simulations), 0)
        hi_idx = min(int((1.0 - alpha / 2.0) * self.n_simulations) - 1, len(bootstrap_means) - 1)

        sorted_times = sorted(times)
        p95_idx = min(int(0.95 * n), n - 1)

        return {
            "mean": statistics.mean(times),
            "std_dev": statistics.stdev(times) if n > 1 else 0.0,
            "median": statistics.median(times),
            "p95": sorted_times[p95_idx],
            "ci_lower": bootstrap_means[lo_idx],
            "ci_upper": bootstrap_means[hi_idx],
            "n_simulations": self.n_simulations,
            "confidence_level": self.confidence_level,
        }


# ===========================================================================
# MultiverseSimulator
# ===========================================================================


class MultiverseSimulator:
    """Parameter-sweep benchmark runner across multiple universe configurations.

    Each *universe* is a ``dict`` of keyword overrides applied to a base
    :class:`BenchmarkProfile` before the run.  This lets you measure how
    performance degrades or improves as ``iterations``, ``input_data``,
    or any other knob changes.

    Parameters
    ----------
    universes:
        Pre-registered list of parameter-override dicts.  More can be added
        at any time via :meth:`add_universe`.

    Example
    -------
    ::

        sim = MultiverseSimulator()
        sim.add_universe(name="small_payload",  input_data=b"x" * 100)
        sim.add_universe(name="large_payload",  input_data=b"x" * 10_000)
        results = await sim.run(engine, base_profile, functions=[compress])
    """

    def __init__(self, universes: Optional[List[Dict[str, Any]]] = None) -> None:
        self._universes: List[Dict[str, Any]] = list(universes or [])
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_universe(self, **params: Any) -> None:
        """Register an additional parameter combination for the next sweep."""
        with self._lock:
            self._universes.append(params)

    @property
    def universe_count(self) -> int:
        """Number of registered universe configurations."""
        with self._lock:
            return len(self._universes)

    async def run(
        self,
        engine: "BenchmarkingEngine",
        base_profile: BenchmarkProfile,
        functions: List[Callable[..., Any]],
    ) -> List[Dict[str, Any]]:
        """Execute one benchmark run per universe and collect all results.

        Each result dict is annotated with a ``universe`` key containing the
        override parameters so the caller can correlate results with the
        configuration that produced them.

        Parameters
        ----------
        engine:
            :class:`BenchmarkingEngine` instance to delegate each run to.
        base_profile:
            Prototype profile whose attributes may be overridden per universe.
        functions:
            Callables to benchmark in each universe.

        Returns
        -------
        list[dict]
            Flat list of result dicts — one per universe.
        """
        with self._lock:
            universes_snapshot = list(self._universes)

        if not universes_snapshot:
            logger.warning(
                "MultiverseSimulator.run() called with no universes registered; "
                "returning empty result set.",
                extra={"base_profile": base_profile.name},
            )
            return []

        all_results: List[Dict[str, Any]] = []
        for idx, universe_params in enumerate(universes_snapshot):
            patched = BenchmarkProfile(
                name=universe_params.get("name", f"{base_profile.name}_u{idx}"),
                iterations=int(universe_params.get("iterations", base_profile.iterations)),
                input_data=universe_params.get("input_data", base_profile.input_data),
                warmup_runs=int(universe_params.get("warmup_runs", base_profile.warmup_runs)),
                timeout=universe_params.get("timeout", base_profile.timeout),
            )
            logger.debug(
                "MultiverseSimulator: running universe.",
                extra={"universe_index": idx, "profile": patched.name},
            )
            universe_results = await engine.run_benchmark(
                functions=functions,
                profiles=[patched],
            )
            for r in universe_results:
                r["universe_index"] = idx
                r["universe"] = universe_params
            all_results.extend(universe_results)

        logger.info(
            "MultiverseSimulator: sweep complete.",
            extra={
                "universes": len(universes_snapshot),
                "result_count": len(all_results),
            },
        )
        return all_results


# ===========================================================================
# BenchmarkingEngine
# ===========================================================================


class BenchmarkingEngine:
    """Async orchestrator for the OmniCore benchmarking subsystem.

    Runs :class:`BenchmarkProfile` scenarios against one or more callables,
    collects per-iteration timing data, emits Prometheus metrics and
    OpenTelemetry spans, and forwards results to registered reporters.

    The engine also exposes the arbiter-compatible ``execute(action, **kwargs)``
    entry-point so it can be registered as a plugin in the OmniCore plugin
    registry without any adapter code.

    Parameters
    ----------
    reporters:
        Zero or more reporter instances (e.g. :class:`ConsoleReporter`,
        :class:`JSONReporter`).  Reporters are called sequentially after
        every ``run_benchmark()`` invocation.
    monte_carlo:
        Optional :class:`MonteCarloSimulator`.  When provided, CI statistics
        are embedded in each profile result dict under the ``monte_carlo``
        key.
    circuit_breaker_threshold:
        Maximum consecutive errors before the engine trips its internal
        circuit breaker and short-circuits subsequent calls with an
        exception.  The breaker resets automatically on a successful run.
    circuit_breaker_timeout:
        Seconds the circuit breaker stays open before entering *half-open*
        state and allowing a probe run.

    Thread Safety
    -------------
    The engine is safe to share across multiple async tasks and OS threads.
    All mutable state (circuit breaker, reporter list) is protected by a
    ``threading.Lock``.

    Prometheus Metrics
    ------------------
    * ``benchmarking_engine_runs_total`` (Counter, labels: profile, status)
    * ``benchmarking_engine_run_duration_seconds`` (Histogram, label: profile)
    * ``benchmarking_engine_timeouts_total`` (Counter, label: profile)
    * ``benchmarking_engine_reporter_errors_total`` (Counter, label: reporter)
    """

    def __init__(
        self,
        reporters: Optional[List[Any]] = None,
        monte_carlo: Optional[MonteCarloSimulator] = None,
        circuit_breaker_threshold: int = 5,
        circuit_breaker_timeout: float = 60.0,
    ) -> None:
        self.reporters: List[Any] = list(reporters or [])
        self.monte_carlo = monte_carlo

        # Circuit breaker state
        self._cb_threshold = circuit_breaker_threshold
        self._cb_timeout = circuit_breaker_timeout
        self._cb_failures = 0
        self._cb_state: str = "closed"  # closed | open | half-open
        self._cb_last_failure: Optional[float] = None
        self._cb_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run_benchmark(
        self,
        functions: List[Callable[..., Any]],
        profiles: Optional[List[BenchmarkProfile]] = None,
        iterations_per_run: int = 1,
    ) -> List[Dict[str, Any]]:
        """Execute all *profiles* against *functions* and return timing results.

        Parameters
        ----------
        functions:
            Callables to invoke during each benchmark iteration.  May be
            sync or ``async``; coroutines are ``await``-ed automatically.
        profiles:
            Scenarios to execute.  A single default profile is synthesised
            from *iterations_per_run* when this is ``None`` or empty.
        iterations_per_run:
            Fallback iteration count used only when *profiles* is empty.

        Returns
        -------
        list[dict]
            One result dict per profile.  Each dict contains:
            ``profile``, ``iterations``, ``total_time``, ``mean_time``,
            ``min_time``, ``max_time``, ``std_dev``, and optionally
            ``monte_carlo`` when a :class:`MonteCarloSimulator` is attached.

        Raises
        ------
        RuntimeError
            If the engine's circuit breaker is open and the timeout has not
            yet elapsed.
        """
        self._check_circuit_breaker()

        if not profiles:
            profiles = [BenchmarkProfile(name="default", iterations=iterations_per_run)]

        results: List[Dict[str, Any]] = []
        for profile in profiles:
            with _tracer.start_as_current_span(
                "benchmarking_engine.run_profile"
            ) as span:
                span.set_attribute("benchmark.profile", profile.name)
                span.set_attribute("benchmark.iterations", profile.iterations)
                span.set_attribute("benchmark.warmup_runs", profile.warmup_runs)

                t_start = time.monotonic()
                try:
                    result = await self._run_profile(functions, profile)
                    self._record_success()
                    _BENCH_RUNS_TOTAL.labels(
                        profile=profile.name, status="success"
                    ).inc()
                    _BENCH_DURATION_SECONDS.labels(profile=profile.name).observe(
                        time.monotonic() - t_start
                    )
                    span.set_attribute("benchmark.status", "success")
                    span.set_attribute(
                        "benchmark.mean_time_s", result.get("mean_time", 0.0)
                    )
                    results.append(result)
                except Exception as exc:
                    self._record_failure()
                    _BENCH_RUNS_TOTAL.labels(
                        profile=profile.name, status="error"
                    ).inc()
                    span.set_attribute("benchmark.status", "error")
                    span.set_attribute("benchmark.error", str(exc))
                    logger.error(
                        "BenchmarkingEngine: profile run failed.",
                        exc_info=True,
                        extra={"profile": profile.name, "error": str(exc)},
                    )
                    raise

        self._dispatch_reporters(results)
        return results

    async def execute(self, action: str, **kwargs: Any) -> Dict[str, Any]:
        """Arbiter-compatible plugin entry-point.

        Supported actions
        -----------------
        ``run_benchmark``
            Delegates to :meth:`run_benchmark`.  Accepts ``functions``,
            ``profiles`` (list of dicts *or* :class:`BenchmarkProfile`
            objects), and ``iterations_per_run``.
        ``health_check``
            Returns ``{"status": "healthy"}`` or
            ``{"status": "circuit_open"}`` when the circuit breaker is open.

        Returns
        -------
        dict
            Structured response with at least a ``status`` key.
        """
        with _tracer.start_as_current_span("benchmarking_engine.execute") as span:
            span.set_attribute("benchmark.action", action)

            if action == "run_benchmark":
                functions: List[Callable[..., Any]] = kwargs.get("functions", [])
                raw_profiles: List[Any] = kwargs.get("profiles", [])
                profiles: List[BenchmarkProfile] = [
                    p
                    if isinstance(p, BenchmarkProfile)
                    else BenchmarkProfile.from_dict(
                        {**p, "iterations": p.get("iterations", kwargs.get("iterations_per_run", 1))}
                    )
                    for p in raw_profiles
                ]
                results = await self.run_benchmark(
                    functions=functions,
                    profiles=profiles,
                    iterations_per_run=int(kwargs.get("iterations_per_run", 1)),
                )
                return {"status": "success", "results": results}

            elif action == "health_check":
                cb_status = self._cb_state
                span.set_attribute("benchmark.cb_state", cb_status)
                return {
                    "status": "healthy" if cb_status != "open" else "circuit_open",
                    "circuit_breaker": cb_status,
                }

            else:
                logger.warning(
                    "BenchmarkingEngine.execute: unknown action.",
                    extra={"action": action},
                )
                return {"status": "unknown_action", "action": action}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _run_profile(
        self,
        functions: List[Callable[..., Any]],
        profile: BenchmarkProfile,
    ) -> Dict[str, Any]:
        """Execute *profile* against *functions* and return timing statistics."""
        # --- Warm-up phase (un-timed) ---
        for _ in range(profile.warmup_runs):
            for fn in functions:
                try:
                    coro_or_val = fn(profile.input_data)
                    if asyncio.iscoroutine(coro_or_val):
                        await coro_or_val
                except Exception:
                    pass  # Warm-up errors do not abort the benchmark.

        # --- Timed phase ---
        run_times: List[float] = []
        deadline: Optional[float] = (
            time.monotonic() + profile.timeout if profile.timeout is not None else None
        )

        for _ in range(profile.iterations):
            if deadline is not None and time.monotonic() > deadline:
                logger.warning(
                    "BenchmarkingEngine: profile aborted — deadline exceeded.",
                    extra={
                        "profile": profile.name,
                        "timeout_s": profile.timeout,
                        "completed_iterations": len(run_times),
                    },
                )
                _BENCH_TIMEOUT_TOTAL.labels(profile=profile.name).inc()
                break

            t0 = time.monotonic()
            for fn in functions:
                try:
                    coro_or_val = fn(profile.input_data)
                    if asyncio.iscoroutine(coro_or_val):
                        await coro_or_val
                except Exception as exc:
                    logger.debug(
                        "BenchmarkingEngine: benchmarked callable raised.",
                        extra={"profile": profile.name, "error": str(exc)},
                    )
            run_times.append(time.monotonic() - t0)

        n = len(run_times)
        total = sum(run_times)
        mean = total / n if n else 0.0
        std_dev = statistics.stdev(run_times) if n > 1 else 0.0

        result: Dict[str, Any] = {
            "profile": profile.name,
            "iterations": n,
            "total_time": total,
            "mean_time": mean,
            "min_time": min(run_times) if run_times else 0.0,
            "max_time": max(run_times) if run_times else 0.0,
            "std_dev": std_dev,
            "run_times": run_times,
        }

        if self.monte_carlo is not None and run_times:
            result["monte_carlo"] = self.monte_carlo.simulate(run_times)

        logger.debug(
            "BenchmarkingEngine: profile complete.",
            extra={
                "profile": profile.name,
                "iterations": n,
                "mean_time_s": round(mean, 9),
                "std_dev": round(std_dev, 9),
            },
        )
        return result

    def _dispatch_reporters(self, results: List[Dict[str, Any]]) -> None:
        """Call every registered reporter, logging errors without propagating them."""
        for reporter in self.reporters:
            reporter_name = type(reporter).__name__
            try:
                reporter.report(results)
            except Exception as exc:
                _BENCH_REPORTER_ERRORS_TOTAL.labels(reporter=reporter_name).inc()
                logger.warning(
                    "BenchmarkingEngine: reporter raised an exception.",
                    extra={"reporter": reporter_name, "error": str(exc)},
                )

    # ------------------------------------------------------------------
    # Circuit breaker
    # ------------------------------------------------------------------

    def _check_circuit_breaker(self) -> None:
        """Raise ``RuntimeError`` if the circuit breaker is open."""
        with self._cb_lock:
            if self._cb_state == "open":
                elapsed = time.monotonic() - (self._cb_last_failure or 0.0)
                if elapsed >= self._cb_timeout:
                    self._cb_state = "half-open"
                    logger.info(
                        "BenchmarkingEngine: circuit breaker → half-open (probe allowed).",
                        extra={"elapsed_s": round(elapsed, 2)},
                    )
                else:
                    raise RuntimeError(
                        f"BenchmarkingEngine circuit breaker is OPEN "
                        f"({self._cb_failures} consecutive failures). "
                        f"Retry after {self._cb_timeout - elapsed:.1f}s."
                    )

    def _record_failure(self) -> None:
        with self._cb_lock:
            self._cb_failures += 1
            self._cb_last_failure = time.monotonic()
            if self._cb_failures >= self._cb_threshold:
                self._cb_state = "open"
                logger.warning(
                    "BenchmarkingEngine: circuit breaker tripped → OPEN.",
                    extra={"consecutive_failures": self._cb_failures},
                )

    def _record_success(self) -> None:
        with self._cb_lock:
            if self._cb_state in ("half-open", "open"):
                logger.info(
                    "BenchmarkingEngine: circuit breaker → closed after successful probe.",
                )
            self._cb_failures = 0
            self._cb_state = "closed"


# ===========================================================================
# Module exports
# ===========================================================================

__all__ = [
    "BenchmarkProfile",
    "BenchmarkingEngine",
    "ConsoleReporter",
    "JSONReporter",
    "MonteCarloSimulator",
    "MultiverseSimulator",
]
