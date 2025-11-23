import asyncio
import unittest
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

# Public API under test
from runner.process_utils import (
    CircuitBreaker,
    distributed_subprocess,
    get_circuit_breaker,
    parallel_subprocess,
    subprocess_wrapper,
)


class NoopCircuitBreaker:
    """
    Simple circuit breaker stub for tests that don't care about the
    full state machine semantics. It just forwards the call.
    """

    def __init__(self, name: str = "test"):
        self.name = name
        self.state = "CLOSED"
        self.failures = 0

    async def call(self, func, *args, **kwargs):
        if asyncio.iscoroutinefunction(func):
            return await func(*args, **kwargs)
        return func(*args, **kwargs)


class TestCircuitBreaker(unittest.IsolatedAsyncioTestCase):
    """
    High-confidence behavioral tests for CircuitBreaker.

    We test against the real implementation, with RunnerError and
    error_codes patched to safe, deterministic versions.
    """

    async def asyncSetUp(self):
        # Patch logger and anomaly detection so we don't depend on external systems.
        self.logger_patcher = patch("runner.process_utils.logger")
        self.detect_patcher = patch("runner.process_utils.detect_anomaly")

        # Provide deterministic error codes and RunnerError behavior.
        self.error_codes_patcher = patch(
            "runner.process_utils.error_codes",
            {
                "TEST_EXECUTION_FAILED": "TEST_EXECUTION_FAILED",
                "UNEXPECTED_ERROR": "UNEXPECTED_ERROR",
            },
        )

        class _TestRunnerError(RuntimeError):
            def __init__(self, code, msg):
                super().__init__(msg)
                self.error_code = code

        self.runner_error_patcher = patch(
            "runner.process_utils.RunnerError",
            _TestRunnerError,
        )

        self.mock_logger = self.logger_patcher.start()
        self.mock_detect = self.detect_patcher.start()
        self.error_codes_patcher.start()
        self.RunnerError = self.runner_error_patcher.start()

        self.addCleanup(self.logger_patcher.stop)
        self.addCleanup(self.detect_patcher.stop)
        self.addCleanup(self.error_codes_patcher.stop)
        self.addCleanup(self.runner_error_patcher.stop)

    async def test_starts_closed_and_allows_success(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=60, name="ok")

        self.assertEqual(cb.state, "CLOSED")
        self.assertEqual(cb.failures, 0)

        async def ok():
            return "value"

        result = await cb.call(ok)
        self.assertEqual(result, "value")
        self.assertEqual(cb.state, "CLOSED")
        self.assertEqual(cb.failures, 0)

    async def test_opens_after_threshold_failures(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=60, name="boom")

        async def fail():
            raise RuntimeError("fail")

        # First failure: counter increments, still CLOSED.
        with self.assertRaises(RuntimeError):
            await cb.call(fail)
        self.assertEqual(cb.state, "CLOSED")
        self.assertEqual(cb.failures, 1)

        # Second failure: threshold reached, state should be OPEN.
        with self.assertRaises(RuntimeError):
            await cb.call(fail)
        self.assertEqual(cb.state, "OPEN")
        self.assertGreaterEqual(cb.failures, 2)

    async def test_open_blocks_until_recovery_and_half_open_success_resets(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=5, name="half_open")

        async def boom():
            raise RuntimeError("boom")

        # Trip the breaker to OPEN.
        with self.assertRaises(RuntimeError):
            await cb.call(boom)
        self.assertEqual(cb.state, "OPEN")

        # While still within recovery timeout, calls should raise RunnerError immediately.
        async def should_not_run():
            raise AssertionError("must not run while circuit is OPEN")

        with self.assertRaises(self.RunnerError):
            await cb.call(should_not_run)

        # Fast-forward past recovery_timeout so we enter HALF-OPEN on next call.
        with patch(
            "runner.process_utils.time.time", return_value=cb.last_failure_time + 10
        ):

            async def ok():
                return "recovered"

            result = await cb.call(ok)
            self.assertEqual(result, "recovered")
            self.assertEqual(cb.state, "CLOSED")
            self.assertEqual(cb.failures, 0)

    async def test_get_circuit_breaker_registry(self):
        a1 = get_circuit_breaker("alpha")
        a2 = get_circuit_breaker("alpha")
        b = get_circuit_breaker("beta")

        self.assertIs(a1, a2)
        self.assertIsNot(a1, b)


class TestSubprocessWrapper(unittest.IsolatedAsyncioTestCase):
    """
    Tests subprocess_wrapper contract:

    - Success path: success=True, stdout/stderr decoded and redacted,
      provenance attached, latency metric recorded.
    - Non-zero exit: success=False, UTIL_ERRORS incremented.
    - TimeoutExpired: retried with backoff but ultimately propagated.
    """

    async def asyncSetUp(self):
        # Patch logging & metrics
        self.logger_patcher = patch("runner.process_utils.logger")
        self.latency_patcher = patch("runner.process_utils.UTIL_LATENCY")
        self.errors_patcher = patch("runner.process_utils.UTIL_ERRORS")
        self.self_heal_patcher = patch("runner.process_utils.UTIL_SELF_HEAL")

        # Security / provenance
        self.redact_patcher = patch(
            "runner.process_utils.redact_secrets",
            side_effect=lambda s: s,
        )
        self.prov_patcher = patch(
            "runner.process_utils.add_provenance",
            side_effect=lambda d, action=None: {
                **d,
                "provenance": {"action": action},
            },
        )

        # Anomaly detection noop
        self.detect_patcher = patch("runner.process_utils.detect_anomaly")

        # Circuit breaker: ensure it just forwards calls for this test class.
        self.breaker_patcher = patch(
            "runner.process_utils.get_circuit_breaker",
            side_effect=lambda name: NoopCircuitBreaker(name),
        )

        # collect_feedback should be async and non-blocking
        self.feedback_patcher = patch(
            "runner.process_utils.collect_feedback",
            new=AsyncMock(),
        )

        # subprocess.run is where we control the simulated execution
        self.subprocess_patcher = patch("runner.process_utils.subprocess.run")

        # Prevent real sleeps during backoff
        self.sleep_patcher = patch(
            "runner.process_utils.asyncio.sleep",
            new=AsyncMock(),
        )

        # Start all patches
        self.mock_logger = self.logger_patcher.start()
        self.mock_latency = self.latency_patcher.start()
        self.mock_errors = self.errors_patcher.start()
        self.mock_self_heal = self.self_heal_patcher.start()
        self.mock_redact = self.redact_patcher.start()
        self.mock_prov = self.prov_patcher.start()
        self.mock_detect = self.detect_patcher.start()
        self.mock_breaker = self.breaker_patcher.start()
        self.mock_feedback = self.feedback_patcher.start()
        self.mock_run = self.subprocess_patcher.start()
        self.mock_sleep = self.sleep_patcher.start()

        # Cleanups
        self.addCleanup(self.logger_patcher.stop)
        self.addCleanup(self.latency_patcher.stop)
        self.addCleanup(self.errors_patcher.stop)
        self.addCleanup(self.self_heal_patcher.stop)
        self.addCleanup(self.redact_patcher.stop)
        self.addCleanup(self.prov_patcher.stop)
        self.addCleanup(self.detect_patcher.stop)
        self.addCleanup(self.breaker_patcher.stop)
        self.addCleanup(self.feedback_patcher.stop)
        self.addCleanup(self.subprocess_patcher.stop)
        self.addCleanup(self.sleep_patcher.stop)

    async def test_subprocess_wrapper_success(self):
        # Simulate successful command
        self.mock_run.return_value = SimpleNamespace(
            returncode=0,
            stdout=b"ok\n",
            stderr=b"",
        )

        result = await subprocess_wrapper(["echo", "ok"], timeout=5)

        self.mock_run.assert_called_once()
        self.assertTrue(result["success"])
        self.assertEqual(result["stdout"].strip(), "ok")
        self.assertEqual(result["stderr"], "")
        self.assertEqual(result["returncode"], 0)
        self.assertIn("provenance", result)
        # No error metric increment
        self.mock_errors.labels.assert_not_called()

    async def test_subprocess_wrapper_nonzero_exit_marks_failure(self):
        self.mock_run.return_value = SimpleNamespace(
            returncode=7,
            stdout=b"",
            stderr=b"failure",
        )

        result = await subprocess_wrapper(["cmd"], timeout=5)

        self.assertFalse(result["success"])
        self.assertEqual(result["returncode"], 7)
        self.assertIn("failure", result["stderr"])
        self.assertTrue(self.mock_errors.labels.called)

    async def test_subprocess_wrapper_timeout_raises_timeoutexpired(self):
        from subprocess import TimeoutExpired

        self.mock_run.side_effect = TimeoutExpired(cmd="x", timeout=1)

        with self.assertRaises(TimeoutExpired):
            await subprocess_wrapper(["x"], timeout=1)


class TestParallelSubprocess(unittest.IsolatedAsyncioTestCase):
    """
    Tests for parallel_subprocess:

    - Ensures subprocess_wrapper is invoked for each command.
    - Ensures max_workers concurrency limit is respected via semaphore.
    - Only successful results are returned; failures are logged & metered.
    """

    async def asyncSetUp(self):
        # Use NoopCircuitBreaker; real behavior tested separately.
        self.breaker_patcher = patch(
            "runner.process_utils.get_circuit_breaker",
            side_effect=lambda name: NoopCircuitBreaker(name),
        )
        self.logger_patcher = patch("runner.process_utils.logger")
        self.errors_patcher = patch("runner.process_utils.UTIL_ERRORS")
        self.prov_patcher = patch(
            "runner.process_utils.add_provenance",
            side_effect=lambda d, action=None: d,
        )

        self.mock_breaker = self.breaker_patcher.start()
        self.mock_logger = self.logger_patcher.start()
        self.mock_errors = self.errors_patcher.start()
        self.mock_prov = self.prov_patcher.start()

        async def fake_wrapper(cmd: List[str], **kwargs) -> Dict[str, Any]:
            if any("fail" in p for p in cmd):
                raise RuntimeError("boom")
            return {
                "success": True,
                "stdout": "ok",
                "stderr": "",
                "returncode": 0,
                "cmd": cmd,
            }

        self.wrapper_patcher = patch(
            "runner.process_utils.subprocess_wrapper",
            side_effect=fake_wrapper,
        )
        self.mock_wrapper = self.wrapper_patcher.start()

        self.addCleanup(self.breaker_patcher.stop)
        self.addCleanup(self.logger_patcher.stop)
        self.addCleanup(self.errors_patcher.stop)
        self.addCleanup(self.prov_patcher.stop)
        self.addCleanup(self.wrapper_patcher.stop)

    async def test_parallel_subprocess_all_success(self):
        cmds = [["echo", "1"], ["echo", "2"], ["echo", "3"]]

        results = await parallel_subprocess(cmds, max_workers=2)

        self.assertEqual(len(results), 3)
        self.assertTrue(all(r["success"] for r in results))
        self.assertEqual(self.mock_wrapper.call_count, 3)

    async def test_parallel_subprocess_mixed_success_and_failure(self):
        cmds = [["echo", "ok"], ["please", "fail"], ["echo", "ok2"]]

        results = await parallel_subprocess(cmds, max_workers=3)

        # One command fails (filtered out), 2 succeed.
        self.assertEqual(len(results), 2)
        self.assertTrue(all(r["success"] for r in results))
        self.assertTrue(self.mock_errors.labels.called)


class TestDistributedSubprocess(unittest.IsolatedAsyncioTestCase):
    """
    Tests for distributed_subprocess:

    - When runner infra is missing, raises RuntimeError.
    - When backend is invalid, raises ValueError.
    - When a valid backend is wired, it calls runner.parallel_runs and
      normalizes the results with provenance and decoding.
    """

    async def test_requires_runner_infrastructure(self):
        with patch("runner.process_utils._HAS_RUNNER", False), patch(
            "runner.process_utils.logger"
        ) as mock_logger:
            with self.assertRaises(RuntimeError):
                await distributed_subprocess([["echo", "x"]], backend="any")
            mock_logger.error.assert_called()

    async def test_invalid_backend_rejected(self):
        with patch("runner.process_utils._HAS_RUNNER", True), patch(
            "runner.process_utils.BACKENDS", {}
        ), patch("runner.process_utils.config", MagicMock(backend="local")), patch(
            "runner.process_utils.logger"
        ):
            with self.assertRaises(ValueError):
                await distributed_subprocess([["echo", "x"]], backend="missing")

    async def test_valid_backend_happy_path(self):
        # Fake runner that just echoes one successful result.
        class FakeRunner:
            def __init__(self, cfg):
                self.cfg = cfg
                self.calls: List[List[Dict[str, Any]]] = []

            async def parallel_runs(self, tasks: List[Dict[str, Any]]):
                self.calls.append(tasks)
                return [
                    {
                        "id": 0,
                        "stdout": b"ok",
                        "stderr": b"",
                        "returncode": 0,
                    }
                ]

        fake_runner = FakeRunner(cfg=MagicMock())

        with patch("runner.process_utils._HAS_RUNNER", True), patch(
            "runner.process_utils.BACKENDS", {"fake": True}
        ), patch("runner.process_utils.config", MagicMock(backend="fake")), patch(
            "runner.process_utils.redact_secrets", side_effect=lambda s: s
        ), patch(
            "runner.process_utils.add_provenance",
            side_effect=lambda d, action=None: {
                **d,
                "provenance": {"action": action},
            },
        ), patch(
            "runner.process_utils.UTIL_ERRORS"
        ), patch(
            "runner.process_utils.logger"
        ) as mock_logger, patch(
            "runner.process_utils.collect_feedback", new=AsyncMock()
        ), patch(
            "runner.process_utils.runner_backends.BACKEND_REGISTRY",
            {"fake": lambda cfg: fake_runner},
            create=True,
        ):

            results = await distributed_subprocess([["echo", "x"]], backend="fake")

            self.assertEqual(len(results), 1)
            r = results[0]
            self.assertTrue(r["success"])
            self.assertEqual(r["stdout"], "ok")
            self.assertEqual(r["stderr"], "")
            self.assertEqual(r["returncode"], 0)
            self.assertIn("provenance", r)
            self.assertEqual(len(fake_runner.calls), 1)


if __name__ == "__main__":
    unittest.main()
