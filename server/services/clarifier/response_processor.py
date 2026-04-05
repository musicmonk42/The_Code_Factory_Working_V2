"""Clarification response processing.

This module contains ``ResponseProcessor``, which handles user answers to
clarification questions and synthesises the final clarified-requirements
document.  Extracted from ``OmniCoreService._submit_clarification_response``,
``OmniCoreService._get_clarification_feedback``,
``OmniCoreService._generate_clarified_requirements``, and
``OmniCoreService._categorize_answer``.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict

from server.services.service_context import ServiceContext
from server.services.clarifier._response_parser import (
    categorize_answer,
    generate_clarified_requirements,
)

logger = logging.getLogger(__name__)


class ResponseProcessor:
    """Processes user answers to clarification questions.

    All methods expect a *sessions* dict (the module-level
    ``_clarification_sessions`` store) to be passed explicitly so that state
    ownership remains with ``SessionManager``.

    Args:
        ctx: Shared service context.
    """

    def __init__(self, ctx: ServiceContext) -> None:
        self._ctx = ctx

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_clarification_feedback(
        self,
        job_id: str,
        payload: Dict[str, Any],
        sessions: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Get feedback from a clarification session.

        If all questions have been answered the clarified requirements are
        generated and returned.  Otherwise a progress summary is returned.

        Args:
            job_id: The job identifier.
            payload: Request payload (currently unused, reserved for future
                filtering options).
            sessions: The mutable clarification sessions store.

        Returns:
            A status dict describing the session state.
        """
        session = sessions.get(job_id)

        if not session:
            return {
                "status": "not_found",
                "message": f"No clarification session found for job {job_id}",
            }

        if len(session["answers"]) == len(session["questions"]):
            return generate_clarified_requirements(session)

        return {
            "status": "in_progress",
            "job_id": job_id,
            "total_questions": len(session["questions"]),
            "answered_questions": len(session["answers"]),
            "answers": session["answers"],
        }

    def submit_clarification_response(
        self,
        job_id: str,
        payload: Dict[str, Any],
        sessions: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Submit an answer to a clarification question.

        Args:
            job_id: The job identifier.
            payload: Must contain ``question_id`` and optionally ``response``.
            sessions: The mutable clarification sessions store.

        Returns:
            A status dict.  When all questions have been answered the
            ``clarified_requirements`` key is included.
        """
        session = sessions.get(job_id)

        if not session:
            return {
                "status": "error",
                "message": f"No clarification session found for job {job_id}",
            }

        question_id = payload.get("question_id", "")
        response = payload.get("response", "")

        # Bug 5 Fix: Allow question_id without response (for skip/empty answers)
        if not question_id:
            return {
                "status": "error",
                "message": "question_id is required",
            }

        # Store the answer -- use "[SKIPPED]" marker for empty/skip responses
        if not response or response.strip() == "":
            session["answers"][question_id] = "[SKIPPED]"
            logger.info(f"Question {question_id} skipped for job {job_id}")
        else:
            session["answers"][question_id] = response
            logger.info(f"Stored answer for {job_id}, question {question_id}")

        session["updated_at"] = datetime.now().isoformat()

        # Check if all questions answered (including skipped ones)
        if len(session["answers"]) >= len(session["questions"]):
            session["status"] = "completed"
            return {
                "status": "completed",
                "job_id": job_id,
                "message": "All questions answered",
                "clarified_requirements": generate_clarified_requirements(session),
            }

        return {
            "status": "answer_recorded",
            "job_id": job_id,
            "remaining_questions": len(session["questions"]) - len(session["answers"]),
        }

    # ------------------------------------------------------------------
    # Delegated helpers (kept for API parity with OmniCoreService)
    # ------------------------------------------------------------------

    @staticmethod
    def categorize_answer(
        requirements: Dict[str, Any],
        q_lower: str,
        answer: str,
    ) -> None:
        """Categorize an answer by question text.  Delegates to the parser module."""
        categorize_answer(requirements, q_lower, answer)

    @staticmethod
    def generate_clarified_requirements(
        session: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Generate clarified requirements from session answers."""
        return generate_clarified_requirements(session)
