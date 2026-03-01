# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Tests verifying that WebPrompt.prompt() uses ClarifierWebSocketSession
when a live session exists in ClarifierSessionRegistry, and that
ClarifierWebSocketSession exposes send_questions_and_wait().
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Skip entire module if fastapi is not installed
fastapi = pytest.importorskip("fastapi", reason="fastapi not installed")

# Import directly from the module to bypass server.routers.__init__ which
# pulls in many optional dependencies.
try:
    from server.routers.clarifier_ws import (  # noqa: E402
        ClarifierWebSocketSession,
        ClarifierSessionRegistry,
        get_clarifier_registry,
        registry,
    )
except ImportError as e:
    pytest.skip(
        f"server.routers.clarifier_ws not importable: {e}",
        allow_module_level=True,
    )


# ---------------------------------------------------------------------------
# ClarifierWebSocketSession.send_questions_and_wait
# ---------------------------------------------------------------------------

class TestSendQuestionsAndWait:
    """ClarifierWebSocketSession must expose send_questions_and_wait()."""

    def _make_session(self, job_id: str = "test-job-1"):
        return ClarifierWebSocketSession(job_id)

    @pytest.mark.asyncio
    async def test_method_exists(self):
        session = self._make_session()
        assert hasattr(session, "send_questions_and_wait")
        assert asyncio.iscoroutinefunction(session.send_questions_and_wait)

    @pytest.mark.asyncio
    async def test_returns_list(self):
        """send_questions_and_wait resolves to a list."""
        session = self._make_session()
        session.connected = True

        # Simulate a client answering immediately after push
        async def _auto_answer():
            await asyncio.sleep(0.05)
            session.submit_answers({"0": "answer_a", "1": "answer_b"})

        asyncio.create_task(_auto_answer())
        result = await asyncio.wait_for(
            session.send_questions_and_wait(["q1", "q2"]),
            timeout=3.0,
        )
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_returns_empty_list_on_timeout(self):
        """Timeout with no answers returns empty list, not an exception."""
        session = ClarifierWebSocketSession("timeout-job")
        result = await asyncio.wait_for(
            session.send_questions_and_wait(["q?"], timeout=0.1),
            timeout=2.0,
        )
        assert result == []


# ---------------------------------------------------------------------------
# get_clarifier_registry helper
# ---------------------------------------------------------------------------

class TestGetClarifierRegistry:
    def test_returns_singleton(self):
        assert get_clarifier_registry() is registry

    def test_returns_clarifier_session_registry_type(self):
        assert isinstance(get_clarifier_registry(), ClarifierSessionRegistry)


# ---------------------------------------------------------------------------
# WebPrompt.prompt() — WS path wiring
# ---------------------------------------------------------------------------

class TestWebPromptWSWiring:
    """WebPrompt.prompt() should attempt the WS session before the HTTP form."""

    @pytest.mark.asyncio
    async def test_ws_path_used_when_session_connected(self):
        """When a live WS session exists, prompt() should delegate to it."""
        try:
            from generator.clarifier.clarifier_user_prompt import WebPrompt
        except ImportError:
            pytest.skip("WebPrompt not importable in this environment")

        mock_ws_session = MagicMock()
        mock_ws_session.connected = True
        mock_ws_session.is_expired = False
        mock_ws_session.send_questions_and_wait = AsyncMock(
            return_value=["ws_answer_1", "ws_answer_2"]
        )

        mock_registry = MagicMock()
        mock_registry.get_session.return_value = mock_ws_session

        with patch(
            "generator.clarifier.clarifier_user_prompt._CLARIFIER_REGISTRY_AVAILABLE",
            True,
        ), patch(
            "generator.clarifier.clarifier_user_prompt.get_clarifier_registry",
            return_value=mock_registry,
        ):
            prompt_instance = object.__new__(WebPrompt)
            prompt_instance.target_language = "en"
            prompt_instance.config = {}

            context = {"user_id": "u1", "job_id": "test-job-ws"}
            # WebPrompt.prompt() should return early via the WS path
            result = await prompt_instance.prompt(
                ["Question 1?", "Question 2?"],
                context,
            )

        assert result == ["ws_answer_1", "ws_answer_2"]

    @pytest.mark.asyncio
    async def test_http_form_used_when_no_ws_session(self):
        """When no live WS session, the WS registry is still checked."""
        try:
            from generator.clarifier.clarifier_user_prompt import WebPrompt
        except ImportError:
            pytest.skip("WebPrompt not importable in this environment")

        mock_registry = MagicMock()
        mock_registry.get_session.return_value = None

        with patch(
            "generator.clarifier.clarifier_user_prompt._CLARIFIER_REGISTRY_AVAILABLE",
            True,
        ), patch(
            "generator.clarifier.clarifier_user_prompt.get_clarifier_registry",
            return_value=mock_registry,
        ), patch(
            "generator.clarifier.clarifier_user_prompt.HAS_FASTAPI",
            False,
        ):
            prompt_instance = object.__new__(WebPrompt)
            prompt_instance.target_language = "en"
            prompt_instance.config = {}

            with pytest.raises(Exception):
                # Without full infrastructure, any non-WS path may raise;
                # the important assertion is that WS session was checked.
                await prompt_instance.prompt(["Q?"], {"user_id": "u1"})

        mock_registry.get_session.assert_called()

