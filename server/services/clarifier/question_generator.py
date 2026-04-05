"""Rule-based clarification question generator.

This module contains ``QuestionGenerator``, which encapsulates the logic
previously in ``OmniCoreService._generate_clarification_questions``.  The
heavy lifting (keyword matching and question construction) is delegated to
``_prompt_builder.build_rule_based_questions`` so that this file stays small
and focused on the public interface.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from server.services.service_context import ServiceContext
from server.services.clarifier._prompt_builder import build_rule_based_questions

logger = logging.getLogger(__name__)


class QuestionGenerator:
    """Generates clarification questions from raw requirements text.

    In the current implementation this is purely rule-based.  An LLM-based
    path exists in the session orchestration layer (``SessionManager``) which
    delegates to the ``generator.clarifier`` package when an LLM provider is
    available.

    Args:
        ctx: Shared service context (currently unused but reserved for
            future LLM-based question generation).
    """

    def __init__(self, ctx: ServiceContext) -> None:
        self._ctx = ctx

    def generate_clarification_questions(
        self,
        requirements: str,
    ) -> List[Dict[str, str]]:
        """Generate clarification questions based on requirements content.

        This is the rule-based approach.  Returns a list of dicts each
        containing ``id``, ``question``, and ``category`` keys.

        Args:
            requirements: Raw requirements / README content string.

        Returns:
            A list of at most 5 question dicts.  May be empty if the
            requirements text already specifies all detected domains.
        """
        return build_rule_based_questions(requirements)
