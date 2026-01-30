# tests/test_agents.py
import json
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

# Fix the import to be an absolute path
from test_generation.gen_agent import agents


@pytest.fixture
def mock_subprocess_exec_with_json_output():
    """A fixture to mock asyncio.create_subprocess_exec for tests."""
    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b'{"results": "SECURITY REPORT"}', b"")
    mock_proc.returncode = 0
    with patch(
        "test_generation.gen_agent.agents.asyncio.create_subprocess_exec",
        return_value=mock_proc,
    ) as mock_exec:
        yield mock_exec


@pytest.mark.asyncio
async def test_planner_agent_valid_plan():
    """Planner agent should parse valid JSON from LLM and set 'plan' in state."""
    mock_llm = AsyncMock()
    # The mock should return a MagicMock with a 'content' attribute, matching the agents.py implementation.
    mock_llm.ainvoke = AsyncMock(
        return_value=MagicMock(content=json.dumps({"steps": ["a", "b"]}))
    )
    state = {"spec": "Do X", "spec_format": "gherkin"}

    # The tests now pass the mock LLM directly to the agent.
    result = await agents.planner_agent(state, llm=mock_llm)

    assert "plan" in result
    assert result["plan"]["steps"] == ["a", "b"]
    assert "plan_generated_at" in result


@pytest.mark.asyncio
async def test_planner_agent_invalid_json_sets_error():
    """Invalid JSON from LLM should populate error field."""
    mock_llm = AsyncMock()
    # The mock should return a MagicMock with a 'content' attribute.
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="not-json"))
    state = {"spec": "Do X"}

    # The tests now pass the mock LLM directly to the agent.
    result = await agents.planner_agent(state, llm=mock_llm)

    assert "error" in result["plan"]
    assert "Failed to generate plan." in result["plan"]["message"]


@pytest.mark.asyncio
async def test_generator_agent_strips_code_fences():
    """Generator agent should remove markdown code fences."""
    llm_output = "```python\nprint('hi')\n```"
    mock_llm = AsyncMock()
    # The mock should return a MagicMock with a 'content' attribute.
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content=llm_output))
    state = {
        "language": "python",
        "framework": "pytest",
        "code_under_test": "print(123)",
    }

    # The tests now pass the mock LLM directly to the agent.
    result = await agents.generator_agent(state, llm=mock_llm)

    assert result["test_code"] == "print('hi')\n"


@pytest.mark.asyncio
async def test_generator_agent_inserts_placeholder_on_empty():
    """Empty LLM output should trigger placeholder test code."""
    mock_llm = AsyncMock()
    # The mock should return a MagicMock with a 'content' attribute.
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content=""))
    state = {"language": "python", "framework": "pytest"}

    # The tests now pass the mock LLM directly to the agent.
    result = await agents.generator_agent(state, llm=mock_llm)

    assert "import pytest" in result["test_code"]
    assert "test_placeholder" in result["test_code"]


@pytest.mark.asyncio
async def test_security_agent_skips_if_bandit_unavailable():
    """Security agent should skip if BANDIT_AVAILABLE is False."""
    with patch("test_generation.gen_agent.agents.BANDIT_AVAILABLE", False):
        state = {"code_under_test": "pass", "language": "python"}
        result = await agents.security_agent(state)

        assert result["security_report"]["status"] == "skipped"


@pytest.mark.asyncio
async def test_security_agent_runs_bandit(mock_subprocess_exec_with_json_output):
    """Security agent should include report from mocked bandit subprocess."""
    with (
        patch("test_generation.gen_agent.agents.BANDIT_AVAILABLE", True),
        patch(
            "test_generation.gen_agent.agents.shutil.which",
            return_value="/usr/bin/bandit",
        ),
    ):

        state = {"code_under_test": "pass", "language": "python"}
        result = await agents.security_agent(state)

    assert result["security_report"]["status"] == "complete"
    assert result["security_report"]["report"]["results"] == "SECURITY REPORT"
    mock_subprocess_exec_with_json_output.assert_called_once()
    assert "bandit" in str(mock_subprocess_exec_with_json_output.call_args)


@pytest.mark.asyncio
async def test_performance_agent_skips_if_locust_unavailable():
    """Performance agent should skip if LOCUST_AVAILABLE is False."""
    with patch("test_generation.gen_agent.agents.LOCUST_AVAILABLE", False):
        state = {"code_under_test": "pass", "language": "python"}
        result = await agents.performance_agent(state)

        assert result["performance_report"]["status"] == "skipped"


@pytest.mark.asyncio
async def test_performance_agent_runs_locust():
    """Performance agent should generate performance script if available."""
    with patch("test_generation.gen_agent.agents.LOCUST_AVAILABLE", True):
        mock_run = AsyncMock(return_value="PERF SCRIPT")
        with patch("test_generation.gen_agent.agents._run_locust", mock_run):
            state = {"code_under_test": "pass", "language": "python"}
            result = await agents.performance_agent(state)

    assert "PERF SCRIPT" in result["performance_report"]["report"]
    assert result["performance_report"]["status"] == "completed"


@pytest.mark.asyncio
async def test_agents_increment_metrics():
    """All agents should increment Prometheus counters."""
    mock_llm = AsyncMock()
    mock_llm.ainvoke = AsyncMock(
        return_value=MagicMock(content=json.dumps({"steps": []}))
    )

    with patch.object(agents.agent_runs_total, "labels") as mock_counter_labels:
        # The tests now pass the mock LLM directly to the agent.
        mock_counter_labels.return_value.inc = Mock()

        state = {"spec": "X", "language": "python", "framework": "pytest"}
        await agents.planner_agent(state, llm=mock_llm)

    mock_counter_labels.assert_called_with(status="success", agent_name="planner")
    mock_counter_labels.return_value.inc.assert_called_once()


@pytest.mark.asyncio
async def test_sanitization_fallback(monkeypatch):
    """
    Tests that the generator agent correctly sanitizes input even when bleach is unavailable.
    This test verifies that the `_sanitize_input` fallback to `html.escape` is functioning.
    """
    monkeypatch.setattr("test_generation.gen_agent.agents._BLEACH_OK", False)

    # Mock LLM to return a placeholder. The point of the test is to check the input to the LLM.
    mock_llm = AsyncMock()
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="def test(): pass"))

    # The input code contains a raw script tag
    state = {
        "code_under_test": "<script>alert(1)</script>",
        "language": "python",
        "framework": "pytest",
    }

    # Patch the audit_logger to prevent errors
    with (
        patch(
            "test_generation.gen_agent.agents.audit_logger.log_event",
            new_callable=AsyncMock,
        ),
        patch("test_generation.gen_agent.agents.logger"),
    ):

        # We need to test what happens when the agent calls `_sanitize_input`.
        with patch("test_generation.gen_agent.agents._sanitize_input") as mock_sanitize:
            mock_sanitize.return_value = "&lt;script&gt;alert(1)&lt;/script&gt;"

            await agents.generator_agent(state, llm=mock_llm)

            # Assert that our mocked sanitization function was called with the raw input
            mock_sanitize.assert_called_once_with("<script>alert(1)</script>")

            # The LLM should have received the sanitized content in its prompt
            llm_prompt_call = mock_llm.ainvoke.call_args[0][0]
            assert "&lt;script&gt;" in llm_prompt_call
            assert "<script>" not in llm_prompt_call
