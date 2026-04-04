"""Response parsing and answer categorization helpers for the clarifier.

This module contains the logic for mapping user answers back into structured
clarified-requirements dictionaries.  Extracted from
``OmniCoreService._generate_clarified_requirements`` and
``OmniCoreService._categorize_answer``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def categorize_answer(
    requirements: Dict[str, Any],
    q_lower: str,
    answer: str,
) -> None:
    """Categorize an answer based on question text and store it in *requirements*.

    This mutates ``requirements["clarified_requirements"]`` in place, matching
    keyword patterns in the lowered question text to known domain categories.

    Args:
        requirements: The mutable requirements dict being built.
        q_lower: Lowered question text used for keyword matching.
        answer: The user-provided answer string.
    """
    mapping = [
        (["database"], "database"),
        (["auth", "login"], "authentication"),
        (["api"], "api_type"),
        (["frontend", "framework"], "frontend_framework"),
        (["deploy", "platform"], "deployment_platform"),
        (["test"], "testing_strategy"),
        (["performance"], "performance_requirements"),
        (["security"], "security_requirements"),
        (["language"], "programming_language"),
        (["user"], "target_users"),
        (["integration"], "third_party_integrations"),
    ]

    clarified = requirements["clarified_requirements"]
    for keywords, category in mapping:
        if any(kw in q_lower for kw in keywords):
            clarified[category] = answer
            return


def generate_clarified_requirements(session: Dict[str, Any]) -> Dict[str, Any]:
    """Generate clarified requirements from a completed clarification session.

    Iterates over the stored answers, maps them to domain categories using
    either the explicit ``category`` field (new rule-based format) or
    keyword-based heuristics (legacy/LLM string format), and returns a
    structured requirements dict.

    Args:
        session: The clarification session dict containing ``requirements``,
            ``questions``, and ``answers``.

    Returns:
        A dict with ``original_requirements``, ``clarified_requirements``,
        ``confidence``, and ``status`` keys.
    """
    requirements: Dict[str, Any] = {
        "original_requirements": session["requirements"],
        "clarified_requirements": {},
    }

    for question_id, answer in session["answers"].items():
        q_idx = int(question_id.replace("q", "")) - 1
        if q_idx < len(session["questions"]):
            question = session["questions"][q_idx]

            if isinstance(question, dict):
                q_text = question.get("question", "")
                q_category = question.get("category", "")

                if q_category:
                    requirements["clarified_requirements"][q_category] = answer
                else:
                    q_lower = q_text.lower()
                    categorize_answer(requirements, q_lower, answer)
            else:
                # Legacy string format
                q_lower = str(question).lower()
                categorize_answer(requirements, q_lower, answer)
                requirements["clarified_requirements"][f"answer_{q_idx + 1}"] = answer

    requirements["confidence"] = 0.95  # High confidence after clarification
    requirements["status"] = "clarified"

    return requirements
