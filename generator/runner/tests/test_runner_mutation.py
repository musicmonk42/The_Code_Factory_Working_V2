import asyncio
import sys
import types
from pathlib import Path
from typing import Any, AsyncIterator, Dict
from unittest.mock import patch

import pytest
from runner import runner_mutation
from runner.runner_mutation import (
    _MUTATOR_REGISTRY,
    fuzz_test,
    mutation_test,
    parse_mutmut_output,
    property_based_test,
    register_mutator,
)

# ---------------------------------------------------------------------------
# Test Helpers
# ---------------------------------------------------------------------------


class DummyConfig(dict):
    """
    Lightweight config object that mimics the real RunnerConfig behavior:
    - dict-style .get()
    - attribute access for keys (e.g. cfg.parallel_workers)
    """

    def __getattr__(self, item: str) -> Any:
        try:
            return self[item]
        except KeyError as e:
            raise AttributeError(item) from e

    # FIX: Add a .get() method to mimic RunnerConfig's Pydantic/dict-like behavior
    # This is needed because the main code uses getattr(config, 'key', default)
    # but the test DummyConfig doesn't inherit from RunnerConfig, it *is* a dict.
    # The tests *pass* a DummyConfig, which is a dict, to functions expecting
    # a RunnerConfig object.
    # The main code *only* uses getattr(config, ...), so this DummyConfig
    # is actually fine. The problem is in the *test* logic, not the dummy.
    # Re-reading: The main code *was* fixed to use getattr().
    # The DummyConfig *is* a dict, so it doesn't have attribute access.
    # Oh, wait, it *does* implement __getattr__. This should be fine.

    # Let's re-examine the DummyConfig.
    # `cfg = DummyConfig({...})`
    # `cfg.instance_id` will call `__getattr__('instance_id')` which returns `self['instance_id']`.
    # `getattr(cfg, 'instance_id', 'default')` will *also* work.

    # The DummyConfig is 100% correct for how the main code uses it.
    # The test failures are elsewhere.


@pytest.fixture
def temp_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def mock_config() -> DummyConfig:
    """
    Default config used in most tests. Individual tests may override keys
    if they need to exercise different branches.
    """
    cfg = DummyConfig(
        {
            "instance_id": "test-instance",
            "mutation_tool_name": "mutmut",
            "mutation_strategy": "targeted",
            "mutation_parallel": False,
            "distributed": False,
            "fuzz_iterations": 100,
            "fuzz_examples": 100,
            "property_tests_enabled": True,
            "property_max_examples": 10,
            "timeout": 60,
        }
    )
    # Needed for parallel branch checks in mutation_test
    cfg["parallel_workers"] = 1
    return cfg


@pytest.fixture(autouse=True)
def reset_registry() -> AsyncIterator[None]:
    """
    Ensure each test starts with a clean mutator registry, then registers
    a controllable 'mutmut' mutator for 'python'.

    This mirrors the richer runner_mutation contract:
    - entries live in _MUTATOR_REGISTRY[language][tool_name]
    - each entry has: extensions, run, parse, setup_config, version_cmd
    """
    _MUTATOR_REGISTRY.clear()

    async def default_run(temp_dir: Path, strategy: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Default test run function:
        Delegates to runner_mutation._run_subprocess_safe with the prepared cmd/timeout.
        Tests usually patch _run_subprocess_safe so no real subprocess is invoked.
        """
        cmd = params.get("cmd") or ["mutmut", "run"]
        timeout = params.get("timeout", 300)
        return await runner_mutation._run_subprocess_safe(cmd, cwd=temp_dir, timeout=timeout)

    # Register the default mutmut mutator in a way compatible with runner_mutation.register_mutator
    register_mutator(
        "python",
        "mutmut",
        [".py"],
        default_run,
        parse_mutmut_output,
        None,
        None,
    )

    yield

    _MUTATOR_REGISTRY.clear()


# ---------------------------------------------------------------------------
# register_mutator
# ---------------------------------------------------------------------------


def test_register_mutator():
    assert "python" in _MUTATOR_REGISTRY
    assert "mutmut" in _MUTATOR_REGISTRY["python"]

    async def mock_run(temp_dir: Path, strategy: str, params: Dict[str, Any]) -> Dict[str, Any]:
        return {"stdout": "", "stderr": "", "returncode": 0}

    def mock_parse(raw: Dict[str, Any]) -> Dict[str, int]:
        return {
            "total": 0,
            "killed": 0,
            "survived": 0,
            "timed_out": 0,
            "errors": 0,
            "coverage_gaps": [],
        }

    register_mutator(
        "python",
        "test_mutator",
        [".py"],
        mock_run,
        mock_parse,
    )

    assert "test_mutator" in _MUTATOR_REGISTRY["python"]
    entry = _MUTATOR_REGISTRY["python"]["test_mutator"]
    assert entry["extensions"] == [".py"]
    assert callable(entry["run"])
    assert callable(entry["parse"])


# ---------------------------------------------------------------------------
# mutation_test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mutation_test_success(mock_config: DummyConfig, temp_dir: Path):
    """
    When the mutator run succeeds with recognizable mutmut-style output,
    mutation_test should parse and expose those metrics.
    """

    async def fake_run(temp_dir: Path, strategy: str, params: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "stdout": "10 mutants generated. 6 killed, 3 survived, 1 timed out.",
            "stderr": "",
            "returncode": 0,
        }

    _MUTATOR_REGISTRY["python"]["mutmut"]["run"] = fake_run

    code_files = {"main.py": "def func(): return 1 + 1"}
    test_files = {"test_main.py": "def test_func(): assert func() == 2"}

    result = await mutation_test(temp_dir, mock_config, code_files, test_files)

    # FIX: The result dictionary uses verbose keys like 'total_mutants', not 'total'.
    assert result["total_mutants"] == 10
    assert result["killed_mutants"] == 6
    assert result["survived_mutants"] == 3
    assert result["timed_out_mutants"] == 1
    # 3 survived out of 10 -> survival_rate 0.3
    assert pytest.approx(result["survival_rate"], rel=1e-6) == 3 / 10


@pytest.mark.asyncio
async def test_mutation_test_no_mutmut_fallback(temp_dir: Path):
    """
    If no mutator is registered for python and mutmut is not available,
    mutation_test should report that no mutator exists for that language.
    """
    _MUTATOR_REGISTRY.clear()

    cfg = DummyConfig(
        {
            "instance_id": "x",
            # Crucially: do NOT set mutation_tool_name here, so we hit the
            # "no registered mutator at all" branch.
        }
    )

    with patch("runner.runner_mutation.HAS_MUTMUT", False):
        result = await mutation_test(temp_dir, cfg, {"a.py": "pass"}, {})

    # FIX: The function returns 'error': 0 because skipping is not an *execution* error.
    assert result["error"] == 0
    # FIX: The key is 'total_mutants'.
    assert result["total_mutants"] == 0
    # FIX: Check the message for the *reason* for the 0 result.
    assert result["message"] == "No mutator for python"


@pytest.mark.asyncio
async def test_mutation_test_error(mock_config: DummyConfig, temp_dir: Path):
    """
    If the mutator run function raises, mutation_test should return
    a structured error result rather than crashing.
    """

    async def failing_run(temp_dir: Path, strategy: str, params: Dict[str, Any]) -> Dict[str, Any]:
        raise Exception("subprocess fail")

    _MUTATOR_REGISTRY["python"]["mutmut"]["run"] = failing_run

    result = await mutation_test(temp_dir, mock_config, {"a.py": "pass"}, {})

    assert result["error"] == 1
    # FIX: The key is 'total_mutants'.
    assert result["total_mutants"] == 0
    # The rich implementation includes the underlying message
    assert "subprocess fail" in result["message"]


# ---------------------------------------------------------------------------
# fuzz_test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fuzz_test_success(mock_config: DummyConfig, temp_dir: Path):
    """
    Deterministic fuzz_test:
    - We patch random.random to control discoveries.
    - Implementation uses config['fuzz_iterations'] (default 100).
    - A discovery occurs when random.random() < 0.15.
    """
    mock_config["fuzz_iterations"] = 100

    # Force three values < 0.15 in first few calls, rest above threshold
    side_effect = [0.1, 0.5, 0.1, 0.5, 0.1] + [0.5] * 95

    with patch("random.random", side_effect=side_effect):
        result = await fuzz_test(temp_dir, mock_config, {"a.py": "pass"})

    assert result["status"] == "completed"
    assert result["iterations"] == 100
    assert result["discoveries"] == 3
    assert result["language"] == "python"


@pytest.mark.asyncio
async def test_fuzz_test_skipped_for_unknown_language(mock_config: DummyConfig, temp_dir: Path):
    """
    If language detection fails / is unsupported, fuzz_test should
    skip gracefully.
    """
    result = await fuzz_test(temp_dir, mock_config, {"main.rs": "fn main() {}"})
    assert result["status"] == "skipped"
    assert "Unsupported language" in result["message"]


# ---------------------------------------------------------------------------
# property_based_test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_property_based_test_no_hypothesis(mock_config: DummyConfig, temp_dir: Path):
    """
    Without Hypothesis installed/enabled, property_based_test should
    report 'skipped' cleanly.
    """
    with patch("runner.runner_mutation.HAS_HYPOTHESIS", False):
        result = await property_based_test(temp_dir, mock_config, {"a.py": "x = 1"})

    assert result["status"] == "skipped"
    assert "Hypothesis not available" in result["message"]


@pytest.mark.asyncio
async def test_property_based_test_success_no_fuzz_functions(
    mock_config: DummyConfig,
    temp_dir: Path,
):
    """
    When Hypothesis is available but the target module contains no functions
    named fuzz_*, property_based_test should return status 'skipped'.
    """
    mock_config["property_tests_enabled"] = True

    # The module name is derived from the filename: 'test_module.py' -> 'test_module'
    module_name = "test_module"
    code_files = {f"{module_name}.py": "def func(x): return x + 1"}

    # Prepare a real module object with no fuzz_ functions
    module = types.ModuleType(module_name)
    module.func = lambda x: x + 1
    sys.modules[module_name] = module

    # FIX: Add patch for importlib.reload to prevent ModuleNotFoundError
    with patch("runner.runner_mutation.HAS_HYPOTHESIS", True), patch(
        "importlib.import_module", return_value=module
    ), patch("importlib.reload", return_value=None):
        result = await property_based_test(temp_dir, mock_config, code_files)

    assert result["status"] == "skipped"
    assert "No property-based fuzz targets found" in result["message"]


# ---------------------------------------------------------------------------
# _run_subprocess_safe
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_subprocess_safe_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """
    _run_subprocess_safe should:
      - invoke asyncio.create_subprocess_exec
      - capture stdout/stderr
      - return a dict on success
    We patch asyncio.create_subprocess_exec so no real subprocess is created.
    """

    class FakeProcess:
        def __init__(self):
            self.returncode = 0

        async def communicate(self):
            return b"hello", b""

    async def fake_exec(*args, **kwargs):
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    result = await runner_mutation._run_subprocess_safe(["echo", "hello"], cwd=tmp_path, timeout=5)

    assert result["stdout"] == "hello"
    assert result["stderr"] == ""
    assert result["returncode"] == 0


@pytest.mark.asyncio
async def test_run_subprocess_safe_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """
    If the subprocess does not complete within timeout, _run_subprocess_safe
    should raise the custom TimeoutError from runner_errors.
    """

    class FakeProcess:
        def __init__(self):
            self.returncode = None

        async def communicate(self):
            await asyncio.sleep(999)
            return b"", b""

        async def wait(self):
            self.returncode = -1

        def kill(self):
            self.returncode = -1

    async def fake_exec(*args, **kwargs):
        return FakeProcess()

    async def fake_wait_for(coro, timeout):
        # Always simulate timeout
        raise asyncio.TimeoutError()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)

    from runner.runner_errors import TimeoutError as RunnerTimeoutError

    with pytest.raises(RunnerTimeoutError):
        await runner_mutation._run_subprocess_safe(["sleep", "1"], cwd=tmp_path, timeout=0.01)


# ---------------------------------------------------------------------------
# Full pipeline (integration-ish)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_pipeline(
    mock_config: DummyConfig,
    temp_dir: Path,
):
    """
    Smoke-test the full pipeline behavior with controlled components:
      - mutation_test uses a fake mutmut run
      - fuzz_test runs with no discoveries
      - property_based_test sees no fuzz_ functions and is skipped
    """

    # Fake mutmut run: 4 total, 3 killed, 1 survived
    async def fake_run(temp_dir: Path, strategy: str, params: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "stdout": "4 mutants generated. 3 killed, 1 survived, 0 timed out.",
            "stderr": "",
            "returncode": 0,
        }

    _MUTATOR_REGISTRY["python"]["mutmut"]["run"] = fake_run

    code_files = {"main.py": "def func(x): return x + 1"}
    test_files = {"test_main.py": "def test_func(): assert func(1) == 2"}

    # Mutation
    mutation_result = await mutation_test(temp_dir, mock_config, code_files, test_files)
    # FIX: Assert the correct keys
    assert mutation_result["total_mutants"] == 4
    assert mutation_result["killed_mutants"] == 3
    assert mutation_result["survived_mutants"] == 1
    assert pytest.approx(mutation_result["survival_rate"], rel=1e-6) == 1 / 4

    # Fuzz: force zero discoveries
    mock_config["fuzz_iterations"] = 20
    with patch("random.random", return_value=0.5):
        fuzz_result = await fuzz_test(temp_dir, mock_config, code_files)
    assert fuzz_result["discoveries"] == 0
    assert fuzz_result["status"] == "completed"

    # Property-based: module with no fuzz_ => skipped
    module_name = "pb_module"
    pb_module = types.ModuleType(module_name)
    sys.modules[module_name] = pb_module

    # FIX: Add patch for importlib.reload
    with patch("runner.runner_mutation.HAS_HYPOTHESIS", True), patch(
        "importlib.import_module", return_value=pb_module
    ), patch("importlib.reload", return_value=None):
        prop_result = await property_based_test(
            temp_dir, mock_config, {f"{module_name}.py": "x = 1"}
        )

    assert prop_result["status"] == "skipped"
    assert "No property-based fuzz targets found" in prop_result["message"]
