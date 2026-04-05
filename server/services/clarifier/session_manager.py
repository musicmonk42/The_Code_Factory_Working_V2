"""Clarification session lifecycle management.

Extracted from ``OmniCoreService._run_clarifier``,
``OmniCoreService.cleanup_expired_clarification_sessions``, and
``OmniCoreService.start_periodic_session_cleanup``.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

from server.services.service_context import ServiceContext
from server.services.clarifier.question_generator import QuestionGenerator

logger = logging.getLogger(__name__)

CLARIFICATION_SESSION_TTL_SECONDS: int = int(
    os.getenv("CLARIFICATION_SESSION_TTL_SECONDS", "3600")
)


def _make_session(
    job_id: str, readme: str, questions: List[Any], method: str, channel: str,
) -> Dict[str, Any]:
    """Build a new clarification session dict."""
    return {
        "job_id": job_id,
        "requirements": readme,
        "questions": questions,
        "answers": {},
        "status": "in_progress",
        "created_at": datetime.now().isoformat(),
        "method": method,
        "channel": channel,
    }


def _make_result(
    job_id: str, questions: List[Any], method: str, channel: str,
) -> Dict[str, Any]:
    """Build the standard clarification-initiated result dict."""
    return {
        "status": "clarification_initiated",
        "job_id": job_id,
        "clarifications": questions,
        "confidence": 0.65,
        "questions_count": len(questions),
        "method": method,
        "channel": channel,
    }


class SessionManager:
    """Manages clarification session lifecycle.

    Args:
        ctx: Shared service context.
        question_generator: Used for rule-based fallback.
    """

    def __init__(
        self, ctx: ServiceContext, question_generator: QuestionGenerator | None = None,
    ) -> None:
        self._ctx = ctx
        self._questions = question_generator or QuestionGenerator(ctx)
        self.sessions: Dict[str, Dict[str, Any]] = {}

    async def run_clarifier(self, job_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Execute requirements clarification (LLM-based or rule-based).

        Args:
            job_id: Job identifier.
            payload: Must contain ``readme_content``; may contain ``channel``.

        Returns:
            A status dict with clarification questions or an error.
        """
        try:
            readme_content: str = payload.get("readme_content", "")
            channel: str = payload.get("channel", "cli")
            logger.info(f"Running clarifier for job {job_id} with channel: {channel}")

            if not readme_content:
                return {"status": "error", "message": "No README content provided for clarification"}

            # --- LLM path ---
            if self._ctx.agents_available.get("clarifier"):
                result = await self._try_llm_clarification(job_id, readme_content, channel)
                if result is not None:
                    return result

            # --- Rule-based fallback ---
            logger.info(f"Running rule-based clarifier for job {job_id}")
            questions = self._questions.generate_clarification_questions(readme_content)
            self.sessions[job_id] = _make_session(job_id, readme_content, questions, "rule_based", channel)
            logger.info(f"Clarifier completed for job {job_id} with {len(questions)} questions")
            return _make_result(job_id, questions, "rule_based", channel)

        except Exception as e:
            logger.error(f"Error running clarifier: {e}", exc_info=True)
            return {"status": "error", "message": str(e), "error_type": type(e).__name__}

    async def _try_llm_clarification(
        self, job_id: str, readme_content: str, channel: str,
    ) -> Dict[str, Any] | None:
        """Attempt LLM-based clarification.  Returns ``None`` on failure."""
        logger.info(f"Running LLM-based clarifier for job {job_id}")
        try:
            from generator.clarifier.clarifier import Clarifier
            from generator.clarifier.clarifier_user_prompt import get_channel

            clarifier = await Clarifier.create()
            try:
                target_lang = getattr(getattr(clarifier, "config", None), "TARGET_LANGUAGE", "en")
                clarifier.interaction = get_channel(channel_type=channel, target_language=target_lang)
                logger.info(f"Set clarifier channel to: {channel}")
            except Exception as ce:
                logger.warning(f"Could not set channel to {channel}: {ce}. Using default.", exc_info=True)

            has_llm = hasattr(clarifier, "llm") and clarifier.llm is not None
            if not has_llm:
                logger.info("No LLM configured, using rule-based clarification")
                return None

            try:
                ambiguities = await clarifier.detect_ambiguities(readme_content)
                questions = await clarifier.generate_questions(ambiguities, readme_content)
                logger.info(
                    f"LLM clarifier generated {len(questions)} questions for job {job_id}",
                    extra={"method": "llm", "questions_count": len(questions), "channel": channel},
                )
                self.sessions[job_id] = _make_session(job_id, readme_content, questions, "llm", channel)
                return _make_result(job_id, questions, "llm", channel)
            except Exception as llm_err:
                logger.warning(f"LLM clarification failed: {llm_err}. Falling back.", exc_info=True)
                return None

        except ImportError as e:
            logger.warning(f"Could not import Clarifier module: {e}. Using rule-based.")
            return None
        except Exception as e:
            logger.warning(f"Error initializing clarifier: {e}. Falling back.", exc_info=True)
            return None

    # ------------------------------------------------------------------
    # Session cleanup
    # ------------------------------------------------------------------

    async def cleanup_expired_clarification_sessions(
        self, max_age_seconds: int = CLARIFICATION_SESSION_TTL_SECONDS,
    ) -> int:
        """Remove sessions older than *max_age_seconds*.

        Returns:
            Number of sessions removed.
        """
        now = datetime.now(timezone.utc)
        expired: List[str] = []

        for job_id, session in self.sessions.items():
            try:
                raw = session.get("created_at", "")
                if not raw:
                    expired.append(job_id)
                    continue
                try:
                    created = datetime.fromisoformat(raw)
                    if created.tzinfo is None:
                        created = created.replace(tzinfo=timezone.utc)
                    if (now - created).total_seconds() > max_age_seconds:
                        expired.append(job_id)
                except (ValueError, TypeError):
                    logger.warning(f"Invalid timestamp in session {job_id}: {raw}")
                    expired.append(job_id)
            except Exception as e:
                logger.error(f"Error processing session {job_id}: {e}")
                expired.append(job_id)

        for jid in expired:
            del self.sessions[jid]
            logger.info(f"Cleaned up expired clarification session for job {jid}")
        if expired:
            logger.info(f"Cleaned up {len(expired)} expired clarification sessions")
        return len(expired)

    async def start_periodic_session_cleanup(
        self, interval_seconds: int = 600, max_age_seconds: int = CLARIFICATION_SESSION_TTL_SECONDS,
    ) -> None:
        """Run cleanup in a loop until cancelled."""
        logger.info(
            f"Starting periodic session cleanup (interval: {interval_seconds}s, max_age: {max_age_seconds}s)"
        )
        while True:
            try:
                await asyncio.sleep(interval_seconds)
                cleaned = await self.cleanup_expired_clarification_sessions(max_age_seconds)
                if cleaned > 0:
                    logger.info(f"Periodic cleanup: removed {cleaned} expired sessions")
            except asyncio.CancelledError:
                logger.info("Periodic cleanup task cancelled")
                break
            except Exception as e:
                logger.error(f"Error in periodic cleanup: {e}", exc_info=True)
