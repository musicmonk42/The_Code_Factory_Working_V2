import json
import logging
import os
from abc import ABC, abstractmethod
from typing import List, Optional

from .config import MultiModalData

# Use standard logging for a regular logger instance
logger = logging.getLogger(__name__)

# --- Prompt Templates (loaded from external source) ---
PROMPT_TEMPLATES = {}
PROMPT_TEMPLATE_FILE = os.getenv("PROMPT_TEMPLATE_FILE", "prompt_templates.json")
PROMPT_TEMPLATES_FALLBACK = {
    "BASE_AGENT_PROMPT_TEMPLATE": """
You are a highly collaborative AI agent. Your primary goal is to help the user by breaking down complex problems and providing clear, actionable solutions.
Your persona: {persona}
Your communication language: {language}

Additional Context (Multi-modal Data):
{multi_modal_context}

Current conversation:
{history}
Human: {input}
AI: """,
    "REFLECTION_PROMPT_TEMPLATE": """
You are an expert self-reflector.
Your task is to analyze the user's input and your AI response to it.
Critically evaluate the response for accuracy, helpfulness, and alignment with the persona.
Focus on identifying potential ambiguities, logical fallacies, or areas where the response could be improved.
User Input: {input}
AI Response: {ai_response}
Your reflection: """,
    "CRITIQUE_PROMPT_TEMPLATE": """
You are a peer-critique agent.
Your task is to provide a concise, constructive critique of the AI's response based on the persona '{persona}'.
Identify a single, most important area for improvement.
AI Response: {ai_response}
Your critique: """,
    "SELF_CORRECT_PROMPT_TEMPLATE": """
You are a self-correcting AI agent.
Based on your initial response, your self-reflection, and a peer critique, generate a final, improved response.
Initial AI Response: {ai_response}
Self-Reflection: {reflection}
Peer Critique: {critique}
Your final, corrected response: """,
}


def _load_templates() -> None:
    """
    Loads prompt templates from a JSON file, or falls back to hardcoded defaults.
    """
    global PROMPT_TEMPLATES
    try:
        with open(PROMPT_TEMPLATE_FILE, "r", encoding="utf-8") as f:
            PROMPT_TEMPLATES = json.load(f)
        logger.info(
            "Prompt templates loaded from file.",
            extra={
                "file": PROMPT_TEMPLATE_FILE,
                "templates": list(PROMPT_TEMPLATES.keys()),
            },
        )
    except FileNotFoundError:
        logger.warning(
            "Prompt template file not found. Using hardcoded fallback templates.",
            extra={"file": PROMPT_TEMPLATE_FILE},
        )
        PROMPT_TEMPLATES = PROMPT_TEMPLATES_FALLBACK
    except json.JSONDecodeError as e:
        logger.error(
            "Failed to parse prompt template file. Using hardcoded fallback.",
            extra={"file": PROMPT_TEMPLATE_FILE, "error": str(e)},
        )
        PROMPT_TEMPLATES = PROMPT_TEMPLATES_FALLBACK
    except Exception as e:
        logger.error(
            "An unexpected error occurred while loading prompt templates. Using hardcoded fallback.",
            extra={"error": str(e)},
            exc_info=True,
        )
        PROMPT_TEMPLATES = PROMPT_TEMPLATES_FALLBACK


_load_templates()

BASE_AGENT_PROMPT_TEMPLATE = PROMPT_TEMPLATES["BASE_AGENT_PROMPT_TEMPLATE"]
REFLECTION_PROMPT_TEMPLATE = PROMPT_TEMPLATES["REFLECTION_PROMPT_TEMPLATE"]
CRITIQUE_PROMPT_TEMPLATE = PROMPT_TEMPLATES["CRITIQUE_PROMPT_TEMPLATE"]
SELF_CORRECT_PROMPT_TEMPLATE = PROMPT_TEMPLATES["SELF_CORRECT_PROMPT_TEMPLATE"]


# --- Prompt Strategies ---
class PromptStrategy(ABC):
    """
    Abstract base class for defining prompt strategies.
    This separates the logic for crafting the prompt from the core agent behavior.
    """

    def __init__(self, logger: logging.Logger):
        self._logger = logger
        self.history_transcript: Optional[str] = None

    def get_history_transcript(self) -> str:
        """Returns the current conversation history transcript."""
        return self.history_transcript if self.history_transcript else ""

    @abstractmethod
    async def create_agent_prompt(
        self,
        base_template: str,
        history: str,
        user_input: str,
        persona: str,
        language: str,
        multi_modal_context: List[MultiModalData],
    ) -> str:
        """
        Creates the full prompt for the agent's LLM call.

        Args:
            base_template: The base prompt template string.
            history: The conversation history transcript.
            user_input: The current user input.
            persona: The agent's persona string.
            language: The communication language.
            multi_modal_context: A list of processed MultiModalData objects.

        Returns:
            The formatted prompt string.
        """
        pass


class DefaultPromptStrategy(PromptStrategy):
    """
    A basic prompt strategy that uses the default template with no frills.
    """

    async def create_agent_prompt(
        self,
        base_template: str,
        history: str,
        user_input: str,
        persona: str,
        language: str,
        multi_modal_context: List[MultiModalData],
    ) -> str:

        mm_context_summary = ""
        if multi_modal_context:
            mm_context_summary = "\n".join(
                [
                    f"- {item.data_type}: {item.metadata.get('summary', 'No summary available.')}"
                    for item in multi_modal_context
                ]
            )

        prompt = base_template.format(
            persona=persona,
            language=language,
            multi_modal_context=mm_context_summary,
            history=history,
            input=user_input,
        )
        self._logger.debug(
            "Generated prompt using DefaultPromptStrategy.",
            extra={"prompt_length": len(prompt)},
        )
        return prompt


class ConcisePromptStrategy(PromptStrategy):
    """
    A prompt strategy focused on brevity for a specific persona or task.
    This could truncate history or simplify the base template.
    """

    async def create_agent_prompt(
        self,
        base_template: str,
        history: str,
        user_input: str,
        persona: str,
        language: str,
        multi_modal_context: List[MultiModalData],
    ) -> str:

        # In a real implementation, this would use a different template or logic.
        # For this example, it's a simple, conceptual demonstration.
        mm_context_summary = ""
        if multi_modal_context:
            mm_context_summary = "\n".join(
                [
                    f"- {item.data_type}: {item.metadata.get('summary', 'No summary available.')}"
                    for item in multi_modal_context
                ]
            )

        concise_history = self._truncate_history(history, max_chars=500)

        prompt = base_template.format(
            persona=persona,
            language=language,
            multi_modal_context=mm_context_summary,
            history=concise_history,
            input=user_input,
        )
        self._logger.debug(
            "Generated prompt using ConcisePromptStrategy.",
            extra={"prompt_length": len(prompt)},
        )
        return prompt

    def _truncate_history(self, history: str, max_chars: int) -> str:
        """Truncates the history to a maximum number of characters."""
        if len(history) > max_chars:
            return f"... (truncated for brevity) ...\n{history[-max_chars:]}"
        return history
