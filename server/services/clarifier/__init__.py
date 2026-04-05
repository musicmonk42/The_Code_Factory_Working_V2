"""Clarification sub-service for the OmniCore service layer.

This package decomposes the clarification-related methods formerly embedded
in ``OmniCoreService`` into three focused sub-modules:

* ``question_generator`` -- rule-based clarification question generation.
* ``response_processor`` -- answer submission and requirements synthesis.
* ``session_manager`` -- session orchestration and lifecycle cleanup.

The top-level ``ClarifierService`` composes all three and exposes delegate
methods that mirror the original ``OmniCoreService`` signatures so that
callers can migrate incrementally.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional

from server.services.service_context import ServiceContext
from server.services.clarifier.question_generator import QuestionGenerator
from server.services.clarifier.response_processor import ResponseProcessor
from server.services.clarifier.session_manager import SessionManager


class ClarifierService:
    """Facade that composes the three clarifier sub-modules.

    Args:
        ctx: Shared service context forwarded to each sub-module.
    """

    def __init__(self, ctx: ServiceContext) -> None:
        self._ctx = ctx
        self._questions = QuestionGenerator(ctx)
        self._responses = ResponseProcessor(ctx)
        self._sessions = SessionManager(ctx, question_generator=self._questions)

    # ------------------------------------------------------------------
    # Delegate: session orchestration
    # ------------------------------------------------------------------

    async def run_clarifier(
        self, job_id: str, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute requirements clarification (LLM or rule-based)."""
        return await self._sessions.run_clarifier(job_id, payload)

    # ------------------------------------------------------------------
    # Delegate: question generation
    # ------------------------------------------------------------------

    def generate_clarification_questions(
        self, requirements: str
    ) -> List[Dict[str, str]]:
        """Generate rule-based clarification questions."""
        return self._questions.generate_clarification_questions(requirements)

    # ------------------------------------------------------------------
    # Delegate: response processing
    # ------------------------------------------------------------------

    def get_clarification_feedback(
        self, job_id: str, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Get feedback / progress for a clarification session."""
        return self._responses.get_clarification_feedback(
            job_id, payload, self._sessions.sessions
        )

    def submit_clarification_response(
        self, job_id: str, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Submit an answer to a clarification question."""
        return self._responses.submit_clarification_response(
            job_id, payload, self._sessions.sessions
        )

    def generate_clarified_requirements(
        self, session: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Generate clarified requirements from session answers."""
        return self._responses.generate_clarified_requirements(session)

    def categorize_answer(
        self,
        requirements: Dict[str, Any],
        q_lower: str,
        answer: str,
    ) -> None:
        """Categorize an answer by question text."""
        self._responses.categorize_answer(requirements, q_lower, answer)

    # ------------------------------------------------------------------
    # Delegate: session cleanup
    # ------------------------------------------------------------------

    async def cleanup_expired_clarification_sessions(
        self, max_age_seconds: int = 3600
    ) -> int:
        """Remove expired clarification sessions."""
        return await self._sessions.cleanup_expired_clarification_sessions(
            max_age_seconds
        )

    async def start_periodic_session_cleanup(
        self,
        interval_seconds: int = 600,
        max_age_seconds: int = 3600,
    ) -> None:
        """Start background periodic session cleanup."""
        await self._sessions.start_periodic_session_cleanup(
            interval_seconds, max_age_seconds
        )

    # ------------------------------------------------------------------
    # Session store access (for callers that need direct access)
    # ------------------------------------------------------------------

    @property
    def sessions(self) -> Dict[str, Dict[str, Any]]:
        """Direct access to the sessions store."""
        return self._sessions.sessions


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_clarifier_instance: Optional[ClarifierService] = None
_clarifier_lock = threading.Lock()


def get_clarifier_service(ctx: ServiceContext) -> ClarifierService:
    """Get or create the singleton ``ClarifierService`` instance.

    Args:
        ctx: Shared service context.  Only used on first call; subsequent
            calls return the cached instance regardless of the *ctx* value.

    Returns:
        The singleton ``ClarifierService``.
    """
    global _clarifier_instance
    if _clarifier_instance is None:
        with _clarifier_lock:
            if _clarifier_instance is None:
                _clarifier_instance = ClarifierService(ctx)
    return _clarifier_instance
