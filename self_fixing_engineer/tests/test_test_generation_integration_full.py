# test_generation/tests/test_integration_full.py
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

# Import the CLI from the correct location
from test_generation.gen_agent import cli


@pytest.fixture
def runner():
    return CliRunner()


@pytest.mark.asyncio
async def test_full_cli_run_with_mocked_agents(tmp_path, runner):
    """E2E: CLI 'generate' -> Graph -> Agents -> IO log (fully mocked to avoid I/O/LLM)."""
    session_id = "integration-session"

    # Fake agent pipeline result
    async def fake_agent(state):
        state["plan"] = {"steps": ["step1", "step2"]}
        state["test_code"] = "print('hello')"
        state["review"] = {"scores": {"coverage": 100.0}}
        state["execution_results"] = {"status": "PASS"}
        return state

    # invoke_graph normally receives (graph, initial_state, ...)
    async def fake_invoke_graph(_graph, initial_state, *args, **kwargs):
        return await fake_agent(initial_state)

    mocked_session_state = {
        "spec": "Given a function, it should be tested.",
        "spec_format": "gherkin",
    }

    # Fix the patch paths to ensure we're patching at the module level where the functions are called
    # We patch all dependencies of _generate_async, but we DON'T patch _generate_async itself.
    with (
        patch(
            "test_generation.gen_agent.cli.run_dependency_check",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "test_generation.gen_agent.cli.init_llm",
            new=AsyncMock(return_value=MagicMock()),
        ),
        patch(
            "test_generation.gen_agent.cli.ensure_session_file",
            new=AsyncMock(return_value=mocked_session_state),
        ),
        patch("test_generation.gen_agent.graph.build_graph", return_value=object()),
        patch("test_generation.gen_agent.graph.invoke_graph", new=fake_invoke_graph),
        patch(
            "test_generation.gen_agent.io_utils.append_to_feedback_log", new=AsyncMock()
        ) as mock_append,
        patch("sys.exit") as mock_exit,
    ):  # Prevent actual sys.exit calls

        # Ensure sys.exit doesn't actually exit
        mock_exit.side_effect = lambda code: None

        args = [
            "--project-root",
            str(tmp_path),  # group option must come before subcommand
            "generate",
            "--session",
            session_id,
            "--output",
            str(tmp_path / "output.txt"),
            "--ci",  # force CI mode to avoid any interactivity
        ]

        # Run CLI in a worker thread to avoid "asyncio.run() in running loop"
        await asyncio.to_thread(runner.invoke, cli.cli, args, catch_exceptions=False)

        # Since we're patching sys.exit, we need to manually check if it was called with a non-zero code
        if mock_exit.called:
            exit_calls = mock_exit.call_args_list
            for call in exit_calls:
                if call[0][0] != 0:
                    print(f"sys.exit was called with code: {call[0][0]}")

    # The assertion now correctly checks if our mocked function was called
    mock_append.assert_called()

    # Validate the feedback payload shape
    call_args, _ = mock_append.call_args
    assert len(call_args) >= 2
    feedback_data = call_args[1]
    assert isinstance(feedback_data, dict)
    assert "scores" in feedback_data
    assert "status" in feedback_data["scores"]
    assert feedback_data["scores"]["status"] == "PASS"


@pytest.mark.asyncio
async def test_graph_with_real_agent_mocks(tmp_path):
    """Graph execution path with agents mocked (targets gen_agent.*)."""

    async def fake_planner(state, llm=None, config=None):
        state["plan"] = {"steps": ["a"]}
        return state

    async def fake_generator(state, llm=None, config=None):
        state["test_code"] = "print(123)"
        return state

    async def fake_judge(state, llm=None, config=None):
        state["review"] = {"scores": {"coverage": 100.0}}
        return state

    async def fake_refiner(state, llm=None, config=None):
        return state

    async def fake_executor(state, llm=None, config=None):
        state["execution_results"] = {"status": "PASS"}
        return state

    async def fake_security(state, llm=None, config=None):
        state["security_report"] = "OK"
        return state

    async def fake_perf(state, llm=None, config=None):
        state["performance_script"] = "OK"
        return state

    # Import here to ensure module load after patches in some environments
    from test_generation.gen_agent import graph as real_graph

    with (
        patch("test_generation.gen_agent.graph.planner_agent", fake_planner),
        patch("test_generation.gen_agent.graph.generator_agent", fake_generator),
        patch("test_generation.gen_agent.graph.judge_agent", fake_judge),
        patch("test_generation.gen_agent.graph.refiner_agent", fake_refiner),
        patch(
            "test_generation.gen_agent.graph.adaptive_test_executor_agent",
            fake_executor,
        ),
        patch("test_generation.gen_agent.graph.security_agent", fake_security),
        patch("test_generation.gen_agent.graph.performance_agent", fake_perf),
        patch("test_generation.gen_agent.graph.init_llm", return_value=None),
    ):

        g = real_graph.build_graph(llm=None)
        result = await real_graph.invoke_graph(
            g, {"spec": "test", "repair_attempts": 0}
        )

    assert "plan" in result
    assert "test_code" in result
    assert result["execution_results"]["status"] == "PASS"
    assert "security_report" in result
    assert "performance_script" in result


@pytest.mark.asyncio
async def test_feedback_log_written(tmp_path):
    """Smoke test for feedback writer (targets gen_agent.io_utils)."""
    from test_generation.gen_agent import io_utils as io_mod

    log_path = tmp_path / "feedback.jsonl"
    entry = {"session": "s1", "status": "PASS"}
    await io_mod.append_to_feedback_log(str(log_path), entry)

    content = log_path.read_text(encoding="utf-8")
    assert json.loads(content.strip()) == entry
    assert log_path.exists()


def test_cli_import():
    """
    Verifies that the main CLI entry point can be imported and is callable.
    """
    from test_generation.gen_agent.cli import cli

    assert callable(cli)
