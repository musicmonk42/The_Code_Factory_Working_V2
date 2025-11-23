import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Fix the import to be an absolute path
from test_generation.gen_agent import graph


@pytest.mark.asyncio
async def test_decide_to_refine_fail_status():
    """Should choose 'refine' when execution failed."""
    state = {"execution_results": {"status": "FAIL"}}
    result = graph._decide_to_refine(state)
    assert result == "refine"


@pytest.mark.asyncio
async def test_decide_to_refine_low_score():
    """Should choose 'refine' when coverage score below threshold."""
    state = {
        "review": {"scores": {"coverage": 0.3}},
        "thresholds": {"refine_threshold": 0.8},
    }
    result = graph._decide_to_refine(state)
    assert result == "refine"


@pytest.mark.asyncio
async def test_decide_to_refine_max_repairs_reached():
    """Should execute if repair attempts exceed limit."""
    # Fix: The logic was updated to return "execute" when max_repairs is reached, not "refine".
    state = {
        "execution_results": {"status": "PASS"},
        "repair_attempts": 5,
        "thresholds": {"max_repairs": 3},
    }
    result = graph._decide_to_refine(state)
    assert result == "execute"


@pytest.mark.asyncio
async def test_step_enforces_timeout():
    """_step should raise asyncio.TimeoutError if func hangs."""

    async def slow_func():
        await asyncio.sleep(2)

    with pytest.raises(asyncio.TimeoutError):
        # Fix: The `_step` function now correctly accepts a `timeout` argument.
        await graph._step(slow_func(), timeout=0.1)


@pytest.mark.asyncio
async def test_step_returns_value():
    """_step should return awaited value."""

    async def fast_func(x):
        return x + 1

    result = await graph._step(fast_func(1), timeout=1)
    assert result == 2


@pytest.mark.asyncio
async def test_build_graph_with_langgraph_available():
    """Should build a LangGraph state graph if available."""
    fake_state_graph = MagicMock()
    fake_state_graph.add_node = MagicMock()
    fake_state_graph.add_edge = MagicMock()
    fake_state_graph.add_conditional_edges = MagicMock()
    fake_state_graph.compile = MagicMock(return_value="compiled_graph")

    # Fix: Correct the mock target from `LangGraphStateGraph` to `StateGraph`.
    with patch(
        "test_generation.gen_agent.graph.StateGraph", return_value=fake_state_graph
    ):
        g = graph.build_graph(llm=MagicMock())
        assert g == "compiled_graph"
        # Fix: The `partial` wrapper is not needed here; the function is passed directly.
        fake_state_graph.add_node.assert_any_call("planner", graph.planner_agent)


@pytest.mark.asyncio
async def test_build_graph_without_langgraph_falls_back():
    """Should build fallback sequential runner if LangGraph not available."""
    # Fix: The `LANGGRAPH_AVAILABLE` flag is now defined in graph.py and can be patched.
    with patch("test_generation.gen_agent.graph.LANGGRAPH_AVAILABLE", False):
        g = graph.build_graph(llm=MagicMock())
        assert asyncio.iscoroutinefunction(g.ainvoke)
        # Run fallback with mocked agents
        state = {}
        with (
            patch(
                "test_generation.gen_agent.graph.planner_agent",
                AsyncMock(return_value=state),
            ),
            patch(
                "test_generation.gen_agent.graph.generator_agent",
                AsyncMock(return_value=state),
            ),
            patch(
                "test_generation.gen_agent.graph.judge_agent",
                AsyncMock(return_value=state),
            ),
            patch(
                "test_generation.gen_agent.graph.refiner_agent",
                AsyncMock(return_value=state),
            ),
            patch(
                "test_generation.gen_agent.graph.adaptive_test_executor_agent",
                AsyncMock(return_value=state),
            ),
            patch(
                "test_generation.gen_agent.graph.security_agent",
                AsyncMock(return_value=state),
            ),
            patch(
                "test_generation.gen_agent.graph.performance_agent",
                AsyncMock(return_value=state),
            ),
            patch(
                "test_generation.gen_agent.graph._decide_to_refine",
                return_value="execute",
            ),
        ):
            result = await g.ainvoke(state, config={})
            assert result == state
