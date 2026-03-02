# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Prompt Registry for Evolved Templates

Stores and serves prompt templates that have been evolved by the genetic algorithm.
Provides thread-safe access for LLM clients to retrieve current best prompts.
"""

import logging
import threading
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class PromptRegistry:
    """
    Thread-safe registry for evolved prompt templates.

    LLM clients can query this registry to get the current best-performing
    prompt templates from the genetic algorithm.
    """

    _instance: Optional["PromptRegistry"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "PromptRegistry":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:  # type: ignore[has-type]
            return

        self._templates: Dict[str, str] = {}
        self._template_lock = threading.RLock()
        self._generation: int = 0
        self._fitness: float = 0.0
        self._initialized = True
        logger.info("PromptRegistry singleton initialized")

    def update_template(self, name: str, template: str) -> None:
        """Update a single template."""
        with self._template_lock:
            self._templates[name] = template
            logger.debug(f"PromptRegistry: Updated template '{name}'")

    def update_all(
        self,
        templates: Dict[str, str],
        generation: int = 0,
        fitness: float = 0.0,
    ) -> None:
        """Update all templates from an evolved genome."""
        with self._template_lock:
            self._templates = dict(templates)
            self._generation = generation
            self._fitness = fitness
            logger.info(
                f"PromptRegistry: Updated {len(templates)} templates "
                f"(gen={generation}, fitness={fitness:.4f})"
            )

    def get_template(self, name: str, default: Optional[str] = None) -> Optional[str]:
        """Get a template by name."""
        with self._template_lock:
            return self._templates.get(name, default)

    def get_all(self) -> Dict[str, str]:
        """Get all templates."""
        with self._template_lock:
            return dict(self._templates)

    def get_stats(self) -> Dict[str, Any]:
        """Get registry statistics."""
        with self._template_lock:
            return {
                "template_count": len(self._templates),
                "generation": self._generation,
                "fitness": self._fitness,
                "template_names": list(self._templates.keys()),
            }


def get_prompt_registry() -> PromptRegistry:
    """Get the singleton PromptRegistry instance."""
    return PromptRegistry()
