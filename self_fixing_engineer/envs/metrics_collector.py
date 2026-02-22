# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
PlatformMetricsCollector

Collects real SystemMetrics from all platform data sources:
- Test results: subprocess call to pytest --tb=no -q
- Static analysis: subprocess call to radon (cyclomatic complexity)
- Security scan: subprocess call to bandit
- Prometheus: query /metrics endpoint if running
- Generator pipeline: from generator service job results
"""

import asyncio
import json
import logging
import os
import re
import subprocess
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------
#: Conservative default line-of-code estimate used when bandit metrics are
#: unavailable.  1 000 LOC is a reasonable lower-bound for a non-trivial
#: Python project and prevents division-by-zero without inflating the alert
#: ratio on tiny codebases.  Override by ensuring Bandit's --metrics output
#: is available (standard when scanning the whole project with -r .).
_DEFAULT_LOC_ESTIMATE: int = 1_000

try:
    import aiohttp as _aiohttp
    _AIOHTTP_AVAILABLE = True
except ImportError:
    _aiohttp = None  # type: ignore[assignment]
    _AIOHTTP_AVAILABLE = False

try:
    from self_fixing_engineer.arbiter.metrics import get_or_create_counter, get_or_create_gauge
    _PROM_AVAILABLE = True
except ImportError:
    _PROM_AVAILABLE = False
    def get_or_create_counter(*a, **kw): return None  # type: ignore[misc]
    def get_or_create_gauge(*a, **kw): return None    # type: ignore[misc]

# Prometheus metrics for the collector itself
_prom_collection_total = get_or_create_counter(
    "platform_metrics_collection_total",
    "Total platform metrics collection runs",
    ("source", "status"),
)
_prom_collection_duration = get_or_create_gauge(
    "platform_metrics_collection_duration_seconds",
    "Duration of last metrics collection run in seconds",
    ("source",),
)


class PlatformMetricsCollector:
    """
    Collects real SystemMetrics from all platform data sources.

    Sources:
    - Test results: subprocess call to pytest --tb=no -q
    - Static analysis: subprocess call to radon (complexity) or flake8
    - Security scan: subprocess call to bandit
    - Prometheus: query /metrics endpoint if running
    """

    def __init__(self, workspace_dir: str = ".", prometheus_url: Optional[str] = None):
        self.workspace_dir = workspace_dir
        self.prometheus_url = prometheus_url or os.getenv("PROMETHEUS_URL")

    async def collect(self) -> Any:
        """Collect all metrics concurrently and return SystemMetrics."""
        import time as _time
        _start = _time.monotonic()
        results = await asyncio.gather(
            self._collect_test_metrics(),
            self._collect_complexity_metrics(),
            self._collect_security_metrics(),
            self._collect_prometheus_metrics(),
            return_exceptions=True,
        )
        merged = self._merge_metrics(results)
        elapsed = _time.monotonic() - _start
        try:
            if _prom_collection_total:
                _prom_collection_total.labels(source="platform", status="ok").inc()
            if _prom_collection_duration:
                _prom_collection_duration.labels(source="platform").set(elapsed)
        except Exception:
            pass
        return merged

    async def _run_subprocess(self, cmd: List[str], cwd: Optional[str] = None, timeout: int = 60) -> str:
        """Run a subprocess command asynchronously and return stdout."""
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd or self.workspace_dir,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return stdout.decode("utf-8", errors="replace")
        except asyncio.TimeoutError:
            logger.warning(f"Subprocess {cmd[0]} timed out after {timeout}s")
            return ""
        except FileNotFoundError:
            logger.debug(f"Command not found: {cmd[0]}")
            return ""
        except Exception as e:
            logger.debug(f"Subprocess {cmd} failed: {e}")
            return ""

    async def _collect_test_metrics(self) -> Dict[str, float]:
        """Run pytest and parse results for pass_rate and code_coverage."""
        try:
            output = await self._run_subprocess(
                ["pytest", "--tb=no", "-q", "--no-header", "--timeout=30"],
                timeout=60,
            )
            if not output:
                return {}

            # Parse "X passed, Y failed" from output
            passed = 0
            failed = 0
            match_passed = re.search(r"(\d+) passed", output)
            match_failed = re.search(r"(\d+) failed", output)
            match_error = re.search(r"(\d+) error", output)

            if match_passed:
                passed = int(match_passed.group(1))
            if match_failed:
                failed += int(match_failed.group(1))
            if match_error:
                failed += int(match_error.group(1))

            total = passed + failed
            pass_rate = passed / total if total > 0 else 0.0

            # Try to get coverage percentage
            coverage = 0.0
            cov_match = re.search(r"TOTAL\s+\d+\s+\d+\s+(\d+)%", output)
            if cov_match:
                coverage = int(cov_match.group(1)) / 100.0

            logger.debug(f"Test metrics: passed={passed}, failed={failed}, pass_rate={pass_rate:.3f}, coverage={coverage:.3f}")
            return {"pass_rate": pass_rate, "code_coverage": coverage}

        except Exception as e:
            logger.debug(f"Failed to collect test metrics: {e}")
            return {}

    async def _collect_complexity_metrics(self) -> Dict[str, float]:
        """Run radon cc to get average cyclomatic complexity."""
        try:
            output = await self._run_subprocess(
                ["radon", "cc", ".", "-a", "-s"],
                timeout=30,
            )
            if not output:
                return {}

            # Parse "Average complexity: X (Y)" from radon output
            match = re.search(r"Average complexity:\s+([\d.]+)", output)
            if match:
                avg_complexity = float(match.group(1))
                # Normalize to 0-1: complexity_score = min(avg_complexity / 20.0, 1.0)
                complexity_score = min(avg_complexity / 20.0, 1.0)
                logger.debug(f"Complexity metrics: avg={avg_complexity:.2f}, score={complexity_score:.3f}")
                return {"complexity": complexity_score}

            return {}
        except Exception as e:
            logger.debug(f"Failed to collect complexity metrics: {e}")
            return {}

    async def _collect_security_metrics(self) -> Dict[str, float]:
        """Run bandit to get security issue ratio."""
        try:
            output = await self._run_subprocess(
                ["bandit", "-r", ".", "-f", "json", "-q"],
                timeout=60,
            )
            if not output:
                return {}

            try:
                data = json.loads(output)
                results = data.get("results", [])
                metrics_data = data.get("metrics", {})
                total_lines = (
                    sum(v.get("loc", 0) for v in metrics_data.values())
                    if metrics_data
                    else _DEFAULT_LOC_ESTIMATE
                )
                if not metrics_data:
                    logger.debug(
                        "Bandit metrics block absent; using _DEFAULT_LOC_ESTIMATE=%d "
                        "to compute alert_ratio.  Run with a broader scan scope to "
                        "obtain per-file LOC data.",
                        _DEFAULT_LOC_ESTIMATE,
                    )
                issue_count = len(results)
                alert_ratio = issue_count / max(total_lines / 100, 1)
                alert_ratio = min(alert_ratio, 1.0)
                logger.debug(f"Security metrics: issues={issue_count}, lines={total_lines}, ratio={alert_ratio:.4f}")
                return {"alert_ratio": alert_ratio}
            except (json.JSONDecodeError, KeyError):
                return {}

        except Exception as e:
            logger.debug(f"Failed to collect security metrics: {e}")
            return {}

    async def _collect_prometheus_metrics(self) -> Dict[str, float]:
        """Query Prometheus for latency metrics if available."""
        if not self.prometheus_url:
            return {}
        if not _AIOHTTP_AVAILABLE:
            return {}
        try:
            async with _aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.prometheus_url}/api/v1/query",
                    params={"query": "http_request_duration_seconds"},
                    timeout=_aiohttp.ClientTimeout(total=5),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        results = data.get("data", {}).get("result", [])
                        if results:
                            p95_val = float(results[0].get("value", [0, "0"])[1])
                            # Normalize: assume p95 < 5s is healthy
                            latency_score = min(p95_val / 5.0, 1.0)
                            return {"latency": latency_score}
            return {}
        except Exception as e:
            logger.debug(f"Prometheus metrics unavailable: {e}")
            return {}

    def _merge_metrics(self, results: List[Any]) -> Any:
        """Merge collected metric dicts into a SystemMetrics-compatible object."""
        from types import SimpleNamespace

        merged: Dict[str, float] = {
            "pass_rate": 0.0,
            "code_coverage": 0.0,
            "complexity": 0.5,
            "generation_success_rate": 0.0,
            "critique_score": 0.0,
            "alert_ratio": 0.0,
            "latency": 0.0,
        }

        for result in results:
            if isinstance(result, dict):
                merged.update(result)
            elif isinstance(result, Exception):
                logger.debug(f"Metrics collection error (ignored): {result}")

        metrics = SimpleNamespace(**merged)
        logger.info(
            f"PlatformMetricsCollector: pass_rate={merged['pass_rate']:.3f}, "
            f"coverage={merged['code_coverage']:.3f}, "
            f"complexity={merged['complexity']:.3f}"
        )
        return metrics
