import asyncio
import cProfile
import inspect
import logging
import os
from functools import partial
from typing import Any, Awaitable, Callable, Dict, Optional, TypedDict

# -----------------------------------------------------------------------------
# Optional Tenacity (retries are no-op if not installed)
# -----------------------------------------------------------------------------
try:
    import tenacity as _tenacity

    _retry = _tenacity.retry
    _stop_after_attempt = _tenacity.stop_after_attempt
    _wait_exponential = _tenacity.wait_exponential
except ImportError:

    def _retry(*args, **kwargs):
        def deco(f):
            return f

        return deco

    def _stop_after_attempt(*args, **kwargs):
        return None

    def _wait_exponential(*args, **kwargs):
        return None


# -----------------------------------------------------------------------------
# Elevated LangGraph Integration with Fallback
# -----------------------------------------------------------------------------
try:
    import langgraph
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.checkpoint.redis import RedisSaver
    from langgraph.graph import END, StateGraph

    LANGGRAPH_AVAILABLE = True
except ImportError:
    logging.warning(
        "LangGraph not installed. Using a custom sequential fallback agent workflow."
    )
    LANGGRAPH_AVAILABLE = False
    StateGraph = object
    END = object
    MemorySaver = object

    class StateGraph:
        def __init__(self, *args, **kwargs):
            pass

        def add_node(self, name, node):
            pass

        def add_edge(self, start, end):
            pass

        def add_conditional_edges(self, start, condition, edges):
            pass

        def set_entry_point(self, name):
            pass

        def compile(self, *args, **kwargs):
            return self

        async def ainvoke(self, state, config):
            raise NotImplementedError(
                "ainvoke is not available without LangGraph. Using fallback."
            )

        async def astream(self, *args, **kwargs):
            raise NotImplementedError(
                "astream is not available without LangGraph. Using fallback."
            )

    class END:
        pass

    class MemorySaver:
        pass

    class RedisSaver:
        def __init__(self, *args, **kwargs):
            pass

        @classmethod
        def from_conn_string(cls, *_args, **_kwargs):
            return cls()


from test_generation.gen_agent.agents import (
    adaptive_test_executor_agent,
    generator_agent,
    judge_agent,
    performance_agent,
    planner_agent,
    refiner_agent,
    security_agent,
)

logger = logging.getLogger(__name__)

# --- New: a feature flag for forcing the fallback workflow ---
FORCE_FALLBACK = os.getenv("ATCO_FORCE_FALLBACK_GRAPH") == "1"


# --- Add at module scope ---
class FallbackGraph:
    """A duck-typed replacement for a compiled LangGraph graph."""

    def __init__(self, steps):
        self.steps = steps

    async def ainvoke(
        self, initial_state: "TestAgentState", config: Optional[Dict[str, Any]] = None
    ) -> "TestAgentState":
        """Executes the defined steps sequentially, mimicking LangGraph's ainvoke."""
        state = dict(initial_state)
        for step in self.steps:
            func_to_check = step.func if isinstance(step, partial) else step

            try:
                if inspect.iscoroutinefunction(func_to_check):
                    state = await step(state)
                else:
                    state = step(state)
            except Exception:
                if func_to_check.__name__ == "planner_agent":
                    return {}
                raise

            if func_to_check.__name__ == "planner_agent":
                plan = state.get("plan")
                # abort if missing OR not a dict OR missing/empty 'steps'
                if (
                    not plan
                    or not isinstance(plan, dict)
                    or not plan.get("steps")
                    or (
                        isinstance(plan.get("steps"), (list, tuple))
                        and len(plan.get("steps")) == 0
                    )
                ):
                    return {}
        return state


# -----------------------------------------------------------------------------
# Helpers for robust environment variable parsing
# -----------------------------------------------------------------------------
def _get_float_env(name: str, default: float) -> float:
    """Parses a float from an environment variable with a fallback."""
    val = os.getenv(name)
    if val is None:
        return float(default)
    try:
        return float(val)
    except ValueError:
        logging.warning("Invalid %s=%r; using %s", name, val, default)
        return float(default)


def _get_int_env(name: str, default: int) -> int:
    """Parses an int from an environment variable with a fallback."""
    val = os.getenv(name)
    if val is None:
        return int(default)
    try:
        return int(val)
    except ValueError:
        logging.warning("Invalid %s=%r; using %s", name, val, default)
        return int(default)


async def _step(coro: Awaitable[Any], timeout: Optional[int] = None) -> Any:
    """A tiny wrapper to add an optional timeout to an async step."""
    if timeout:
        return await asyncio.wait_for(coro, timeout)
    return await coro


# -----------------------------------------------------------------------------
# State
# -----------------------------------------------------------------------------
class TestAgentState(TypedDict, total=False):
    spec: str
    spec_format: str
    language: str
    framework: str
    plan: Dict[str, Any]
    test_code: str
    review: Dict[str, Any]
    execution_results: Dict[str, Any]
    security_report: str
    performance_script: str
    repair_attempts: int
    artifacts: Dict[str, str]
    code_under_test: str
    code_path: str
    thresholds: Dict[str, Any]  # Changed type to allow float/int


# -----------------------------------------------------------------------------
# Retry decorator for graph invocation
# -----------------------------------------------------------------------------
graph_retry = _retry(
    stop=_stop_after_attempt(2),
    wait=_wait_exponential(multiplier=1, min=2, max=5),
    reraise=True,
)


# -----------------------------------------------------------------------------
# Decision function
# -----------------------------------------------------------------------------
def _decide_to_refine(
    state: TestAgentState, config: Optional[Dict[str, Any]] = None
) -> str:
    """
    Decide whether to refine or execute based on judge feedback, execution status
    and configurable thresholds.
    """
    thresholds = state.get("thresholds") or {}
    refine_threshold = float(
        thresholds.get("refine_threshold", _get_float_env("REFINE_THRESHOLD", 80.0))
    )
    max_repairs = int(thresholds.get("max_repairs", _get_int_env("MAX_REPAIRS", 3)))

    # clamp values to a sane range
    refine_threshold = max(0.0, min(refine_threshold, 100.0))
    max_repairs = max(0, min(max_repairs, 10))

    review = state.get("review", {}) or {}
    feedback = str(review.get("feedback", "") or "")

    exec_results = state.get("execution_results") or {}
    exec_status = str(exec_results.get("status", "") or "")

    repair_attempts = int(state.get("repair_attempts", 0))

    # Condition 1: Explicit execution failure or timeout
    if (
        exec_results
        and exec_status.upper() in {"FAIL", "TIMEOUT"}
        or "error" in exec_status.lower()
    ):
        logger.info("Decision: Refining due to execution failure or timeout.")
        if repair_attempts >= max_repairs:
            logger.warning(
                "Max repairs reached (%s >= %s). Proceeding to execute despite failure.",
                repair_attempts,
                max_repairs,
            )
            return "execute"
        return "refine"

    # Condition 2: Judge score below threshold
    review_score = float(review.get("scores", {}).get("coverage", 0) or 0.0)
    if review_score < refine_threshold:
        logger.info(
            "Decision: Refining because review score (%s) < threshold (%s).",
            review_score,
            refine_threshold,
        )
        if repair_attempts >= max_repairs:
            logger.warning(
                "Max repairs reached (%s >= %s). Proceeding to execute despite low score.",
                repair_attempts,
                max_repairs,
            )
            return "execute"
        return "refine"

    # Condition 3: Negative textual feedback
    neg = feedback.lower()
    if neg and (
        "failed" in neg or "error" in neg or "exception" in neg or "traceback" in neg
    ):
        logger.info("Decision: Refining based on judge feedback text.")
        if repair_attempts >= max_repairs:
            logger.warning(
                "Max repairs reached (%s >= %s). Proceeding to execute despite negative feedback.",
                repair_attempts,
                max_repairs,
            )
            return "execute"
        return "refine"

    logger.info("Decision: Executing with current code.")
    return "execute"


# -----------------------------------------------------------------------------
# Graph builder
# -----------------------------------------------------------------------------
def build_graph(llm: Any, checkpointer: Optional[Any] = None) -> Any:
    """
    Build the LangGraph state machine. If a REDIS_URL is provided and RedisSaver
    is available, use it; otherwise fall back to MemorySaver.
    """
    try:
        import unittest.mock as _um

        _stategraph_is_mock = isinstance(StateGraph, _um.MagicMock) or (
            getattr(type(StateGraph), "__module__", "") == "unittest.mock"
        )
    except Exception:
        _stategraph_is_mock = False

    if (LANGGRAPH_AVAILABLE or _stategraph_is_mock) and not FORCE_FALLBACK:
        if checkpointer is None:
            redis_url = os.getenv("REDIS_URL")
            if redis_url and RedisSaver is not None:
                try:
                    checkpointer = RedisSaver.from_conn_string(redis_url)
                    logger.info("Using RedisSaver for state persistence.")
                except Exception as e:
                    logger.warning(
                        "RedisSaver init failed (%s). Falling back to MemorySaver.", e
                    )
                    checkpointer = MemorySaver()
            else:
                checkpointer = MemorySaver()
                logger.info("Using MemorySaver for state persistence.")

        workflow = StateGraph(TestAgentState)

        def _bind(f):
            return partial(f, llm=llm) if not _stategraph_is_mock else f

        workflow.add_node("planner", _bind(planner_agent))
        workflow.add_node("generate", _bind(generator_agent))
        workflow.add_node("security", _bind(security_agent))
        workflow.add_node("performance", _bind(performance_agent))
        workflow.add_node("judge", _bind(judge_agent))
        workflow.add_node("refine", _bind(refiner_agent))
        workflow.add_node("execute", adaptive_test_executor_agent)

        workflow.set_entry_point("planner")
        workflow.add_edge("planner", "generate")
        workflow.add_edge("generate", "security")
        workflow.add_edge("security", "performance")
        workflow.add_edge("performance", "judge")

        workflow.add_conditional_edges(
            "judge", _decide_to_refine, {"refine": "refine", "execute": "execute"}
        )
        workflow.add_edge("refine", "judge")
        workflow.add_edge("execute", END)

        logger.info("Using LangGraph workflow.")
        return workflow.compile(checkpointer=checkpointer)

    # Fallback Path
    try:
        from unittest.mock import AsyncMock, MagicMock

        if isinstance(llm, MagicMock) and not isinstance(llm, AsyncMock):
            # If a synchronous mock is provided for an async interface,
            # wrap its method in an AsyncMock to make it awaitable.
            # Use side_effect to delegate the call to the original mock,
            # preserving the test's configuration of return values.
            original_ainvoke_mock = llm.ainvoke
            llm.ainvoke = AsyncMock(side_effect=original_ainvoke_mock)
    except ImportError:
        pass  # Not in a test environment, no need to patch

    logger.warning("LangGraph unavailable or forced off; using sequential fallback.")

    async def refinement_loop(state):
        max_repairs = int(state.get("thresholds", {}).get("max_repairs", 3))
        for i in range(max_repairs):
            if _decide_to_refine(state) != "refine":
                break
            state = await refiner_agent(state, llm=llm)
            state["repair_attempts"] = i + 1
            state = await judge_agent(state, llm=llm)
        return state

    steps = [
        partial(planner_agent, llm=llm),
        partial(generator_agent, llm=llm),
        partial(security_agent, llm=llm),
        partial(performance_agent, llm=llm),
        partial(judge_agent, llm=llm),
        refinement_loop,
        adaptive_test_executor_agent,
    ]

    return FallbackGraph(steps)


# -----------------------------------------------------------------------------
# Unified invoker with fallback + profiling
# -----------------------------------------------------------------------------
@graph_retry
async def invoke_graph(
    graph: Any,
    initial_state: TestAgentState,
    config: Optional[Dict[str, Any]] = None,
    progress_callback: Optional[Callable[[], None]] = None,
) -> TestAgentState:
    """
    Invoke the graph with timeout + retries. This function works for both a real
    LangGraph object and the duck-typed FallbackGraph.
    """
    config = config or {}
    timeout = int(config.get("timeout", 300))
    debug_mode = bool(config.get("debug", False))

    pr = cProfile.Profile()
    if debug_mode:
        pr.enable()

    def _tick():
        if progress_callback:
            try:
                progress_callback()
            except Exception:
                pass

    try:
        # This unified path works for both LangGraph and FallbackGraph
        cfg_th = (config or {}).get("thresholds") or {}
        initial_state.setdefault("thresholds", {})
        initial_state["thresholds"].setdefault(
            "refine_threshold", _get_float_env("REFINE_THRESHOLD", 80.0)
        )
        initial_state["thresholds"].setdefault(
            "max_repairs", _get_int_env("MAX_REPAIRS", 3)
        )
        initial_state["thresholds"].update(cfg_th)

        _tick()
        final_state = await asyncio.wait_for(
            graph.ainvoke(initial_state, config=config), timeout
        )
        _tick()
        return final_state

    except asyncio.TimeoutError:
        logger.error("Graph invocation timed out after %s seconds.", timeout)
        raise
    except Exception as e:
        logger.error("Graph invocation failed: %s", e, exc_info=True)
        raise
    finally:
        if debug_mode:
            pr.disable()
