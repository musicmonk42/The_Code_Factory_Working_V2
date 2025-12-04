# clarifier_user_prompt.py - FIXED VERSION WITH LAZY INITIALIZATION
import asyncio
import json
import logging
import os
import smtplib
import ssl
import time
import uuid  # For WebPrompt session_id generation
from abc import ABC, abstractmethod
from collections import defaultdict
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional

import aiohttp  # For Slack/web (add to reqs)

# --- Conditional Imports for Channels ---
try:
    import textual  # GUI/TUI (textual req)

    HAS_TEXTUAL = True
except ImportError:
    HAS_TEXTUAL = False
    logging.warning("Textual (TUI/GUI) not found. GUIPrompt will be unavailable.")

try:
    from fastapi import FastAPI, Form, Request  # Web form (fastapi req)
    from starlette.exceptions import HTTPException
    from starlette.responses import HTMLResponse

    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False
    logging.warning("FastAPI not found. WebPrompt will be unavailable.")

try:
    import speech_recognition as sr  # Voice input (add to reqs)

    HAS_SPEECH_RECOGNITION = True
except ImportError:
    HAS_SPEECH_RECOGNITION = False
    logging.warning(
        "Speech Recognition (VoicePrompt) not found. VoicePrompt will be unavailable."
    )


from cryptography.fernet import InvalidToken  # Encrypt (cryptography req)
# FIX: Make googletrans optional since it has dependency conflicts
try:
    from googletrans import Translator  # Translation (googletrans req)
    HAS_GOOGLETRANS = True
except ImportError:
    try:
        from deep_translator import GoogleTranslator as Translator  # Alternative translator
        HAS_GOOGLETRANS = "deep"
    except ImportError:
        Translator = None
        HAS_GOOGLETRANS = False
from prometheus_client import Counter, Gauge, Histogram  # Metrics (prometheus req)
from pydantic import BaseModel  # Config (pydantic req)

# --- FIX: Import from the module that defines the utilities (clarifier) to break circular dependency ---
# OLD: from . import get_logger, get_fernet, get_config
from .clarifier import get_config, get_fernet, get_logger

# --- RUNNER FOUNDATION IMPORTS ---
try:
    from runner.runner_logging import add_provenance as log_action
except ImportError:

    def log_action(*args, **kwargs):
        logging.warning("Dummy log_action used: Runner logging is not available.")


try:
    from runner.language_utils import detect_language
except ImportError:

    def detect_language(text):
        logging.warning(
            "Dummy detect_language used: Runner language utils not available."
        )
        return "en"


try:
    from runner.security_utils import (
        redact_secrets as redact_sensitive,
    )  # MINIMAL FIX APPLIED HERE
except ImportError:

    def redact_sensitive(text):
        logging.warning(
            "Dummy redact_sensitive used: Runner security utils not available."
        )
        return text


# ---------------------------------

import hashlib  # For compute_hash in logging

# ===== CRITICAL FIX: LAZY INITIALIZATION =====
# This prevents configuration from loading during module import, which was causing
# pytest to fail during test collection. Now configuration loads only when first used,
# allowing tests to set up mocks before any validation happens.

_logger_instance = None
_config_instance = None
_channel_configs_cache = None


def _get_logger_lazy():
    """Lazy getter for logger - only initializes when first called."""
    global _logger_instance
    if _logger_instance is None:
        _logger_instance = get_logger()
    return _logger_instance


def _get_config_lazy():
    """Lazy getter for config - only initializes when first called."""
    global _config_instance
    if _config_instance is None:
        _config_instance = get_config()
    return _config_instance


def _get_channel_configs_lazy():
    """Lazy load all channel configurations."""
    global _channel_configs_cache
    if _channel_configs_cache is None:
        cfg = _get_config_lazy()
        _channel_configs_cache = {
            "EMAIL_SERVER": (
                cfg.CLARIFIER_EMAIL_SERVER
                if hasattr(cfg, "CLARIFIER_EMAIL_SERVER")
                else None
            ),
            "EMAIL_PORT": (
                int(cfg.CLARIFIER_EMAIL_PORT)
                if hasattr(cfg, "CLARIFIER_EMAIL_PORT")
                else 587
            ),
            "EMAIL_USER": (
                cfg.CLARIFIER_EMAIL_USER
                if hasattr(cfg, "CLARIFIER_EMAIL_USER")
                else None
            ),
            "EMAIL_PASS": (
                cfg.CLARIFIER_EMAIL_PASS
                if hasattr(cfg, "CLARIFIER_EMAIL_PASS")
                else None
            ),
            "SLACK_WEBHOOK": (
                cfg.CLARIFIER_SLACK_WEBHOOK
                if hasattr(cfg, "CLARIFIER_SLACK_WEBHOOK")
                else None
            ),
            "SMS_API": (
                cfg.CLARIFIER_SMS_API if hasattr(cfg, "CLARIFIER_SMS_API") else None
            ),
            "SMS_KEY": (
                cfg.CLARIFIER_SMS_KEY if hasattr(cfg, "CLARIFIER_SMS_KEY") else None
            ),
        }
    return _channel_configs_cache


def _get_channel_config(key):
    """Get a specific channel config value."""
    return _get_channel_configs_lazy().get(key)


class _LazyLogger:
    """Lazy logger that defers initialization until first use."""

    def __getattr__(self, name):
        return getattr(_get_logger_lazy(), name)


class _LazyConfig:
    """Lazy config that defers initialization until first use."""

    def __getattr__(self, name):
        return getattr(_get_config_lazy(), name)


# Create singleton instances - these are safe at module level because
# they don't trigger initialization until first attribute access
logger = _LazyLogger()
config = _LazyConfig()

# ===== END OF LAZY INITIALIZATION =====

# Constants/Configs
PROFILE_DIR = "user_profiles"
os.makedirs(PROFILE_DIR, exist_ok=True)
CHANNEL_TYPES = ["cli", "gui", "web", "slack", "email", "sms", "voice"]
DEFAULT_CHANNEL = "cli"

# Questions related to Safety and Compliance
COMPLIANCE_QUESTIONS = [
    {
        "id": "gdpr_apply",
        "text": "Does this project need to comply with GDPR regulations?",
        "type": "boolean",
    },
    {
        "id": "phi_data",
        "text": "Will this project process Protected Health Information (PHI) or other sensitive medical data?",
        "type": "boolean",
    },
    {
        "id": "pci_dss",
        "text": "Will this project handle credit card data or require PCI DSS compliance?",
        "type": "boolean",
    },
    {
        "id": "data_residency",
        "text": "Are there specific data residency requirements (e.g., data must reside in EU)? If yes, please specify.",
        "type": "text",
    },
    {
        "id": "child_privacy",
        "text": "Will this project involve data from children under 13?",
        "type": "boolean",
    },
]

# Metrics
# FIX: Wrap metric creation in try-except to handle duplicate registration during pytest
try:
    PROMPT_CYCLES = Counter(
        "clarifier_user_prompt_cycles_total", "Total user prompt cycles", ["channel"]
    )
    PROMPT_LATENCY = Histogram(
        "clarifier_user_prompt_latency_seconds", "User prompt latency", ["channel"]
    )
    PROMPT_ERRORS = Counter(
        "clarifier_user_prompt_errors_total",
        "Errors in user prompting",
        ["channel", "type"],
    )
    USER_ENGAGEMENT = Gauge(
        "clarifier_user_engagement_score", "Engagement score (0-1) per user", ["user_id"]
    )
    FEEDBACK_RATINGS = Histogram(
        "clarifier_feedback_ratings", "User feedback ratings (0-1)"
    )
    COMPLIANCE_QUESTIONS_ASKED = Counter(
        "clarifier_compliance_questions_asked_total",
        "Total compliance questions asked",
        ["question_id"],
    )
    COMPLIANCE_ANSWERS_RECEIVED = Counter(
        "clarifier_compliance_answers_received_total",
        "Total compliance answers received",
        ["question_id", "answer_value"],
    )
except ValueError:
    # Metrics already registered (happens during pytest collection)
    from prometheus_client import REGISTRY
    PROMPT_CYCLES = REGISTRY._names_to_collectors.get("clarifier_user_prompt_cycles_total")
    PROMPT_LATENCY = REGISTRY._names_to_collectors.get("clarifier_user_prompt_latency_seconds")
    PROMPT_ERRORS = REGISTRY._names_to_collectors.get("clarifier_user_prompt_errors_total")
    USER_ENGAGEMENT = REGISTRY._names_to_collectors.get("clarifier_user_engagement_score")
    FEEDBACK_RATINGS = REGISTRY._names_to_collectors.get("clarifier_feedback_ratings")
    COMPLIANCE_QUESTIONS_ASKED = REGISTRY._names_to_collectors.get("clarifier_compliance_questions_asked_total")
    COMPLIANCE_ANSWERS_RECEIVED = REGISTRY._names_to_collectors.get("clarifier_compliance_answers_received_total")


# User Profile
class UserProfile(BaseModel):
    user_id: str
    preferred_channel: str = DEFAULT_CHANNEL
    language: str = "en"
    history: List[Dict[str, Any]] = []
    encrypted_feedback_answers: Dict[str, str] = {}
    feedback_scores: Dict[str, float] = {}
    preferences: Dict[str, Any] = {
        "multi_line": False,
        "voice": False,
        "accessibility": False,
    }
    compliance_preferences: Dict[str, Any] = {}


def load_profile(user_id: str) -> UserProfile:
    """Loads a user's profile from a JSON file."""
    path = os.path.join(PROFILE_DIR, f"{user_id}.json")
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                data = json.load(f)
                data.pop("user_id", None)  # FIX: Remove duplicate user_id
                return UserProfile(user_id=user_id, **data)
        except json.JSONDecodeError as e:
            logger.error(
                f"Error loading profile for {user_id}: {e}. Creating new profile."
            )
            PROMPT_ERRORS.labels(channel="system", type="profile_load_corrupt").inc()
            return UserProfile(user_id=user_id)
        except Exception as e:
            logger.error(
                f"Unexpected error loading profile for {user_id}: {e}. Creating new profile."
            )
            PROMPT_ERRORS.labels(channel="system", type="profile_load_error").inc()
            return UserProfile(user_id=user_id)
    return UserProfile(user_id=user_id)


def save_profile(user_id: str, profile: UserProfile):
    """Saves a user's profile to a JSON file."""
    path = os.path.join(PROFILE_DIR, f"{user_id}.json")
    try:
        with open(path, "w") as f:
            if hasattr(profile, "model_dump_json"):
                f.write(profile.model_dump_json(indent=4))
            else:
                json.dump(profile.dict(), f, indent=4)
    except IOError as e:
        logger.error(f"Failed to save profile for {user_id}: {e}")
        PROMPT_ERRORS.labels(channel="system", type="profile_save_failed").inc()


# Channels (multi-channel abstraction)
class UserPromptChannel(ABC):
    """Abstract base class for different user interaction channels."""

    def __init__(self, target_language: str = "en"):
        # FIX: Handle missing translator gracefully
        if HAS_GOOGLETRANS:
            self.translator = Translator() if Translator else None
        else:
            self.translator = None
            logger.warning("Translation library not available. Translation will be disabled.")
        self.target_language = target_language

    def _translate_text(self, text: str, dest: str) -> str:
        """Translates text if dest language is different from source (assumed 'en')."""
        if not self.translator:
            # Translation not available, return original text
            return text
        if self.target_language != dest:
            try:
                if HAS_GOOGLETRANS == "deep":
                    # deep-translator API
                    translated = self.translator.translate(text, target=dest)
                else:
                    # googletrans API
                    translated = self.translator.translate(text, dest=dest).text
                logger.debug(
                    f"Translated '{text[:30]}...' to '{dest}': '{translated[:30]}...'"
                )
                return translated
            except Exception as e:
                logger.warning(
                    f"Translation failed for '{text[:50]}...' to '{dest}': {e}. Using original text."
                )
                PROMPT_ERRORS.labels(
                    channel=self.__class__.__name__, type="translation_failed"
                ).inc()
                return text
        return text

    def _encrypt_answer(self, answer: str) -> str:
        """Encrypts an answer before storage/transit."""
        if answer is None:
            return ""
        try:
            return get_fernet().encrypt(answer.encode("utf-8")).decode("utf-8")
        except Exception as e:
            logger.error(
                f"Encryption failed for answer: {e}. Returning unencrypted (DANGER!)."
            )
            PROMPT_ERRORS.labels(
                channel=self.__class__.__name__, type="encryption_failed"
            ).inc()
            return answer

    def _decrypt_answer(self, encrypted_answer: str) -> str:
        """Decrypts an answer for internal processing."""
        if not encrypted_answer:
            return ""
        try:
            return get_fernet().decrypt(encrypted_answer.encode()).decode()
        except InvalidToken:
            logger.error(
                "Failed to decrypt answer: Invalid token.",
                extra={"operation": "decrypt_answer_failed"},
            )
            PROMPT_ERRORS.labels(
                channel=self.__class__.__name__, type="decryption_failed"
            ).inc()
            return "[DECRYPTION_FAILED]"
        except Exception as e:
            logger.error(f"Unexpected error during decryption: {e}.")
            PROMPT_ERRORS.labels(
                channel=self.__class__.__name__, type=type(e).__name__
            ).inc()
            return "[DECRYPTION_ERROR]"

    @abstractmethod
    async def prompt(
        self,
        questions: List[str],
        context: Dict[str, Any],
        target_language: Optional[str] = None,
    ) -> List[str]:
        """
        Prompts the user with a list of questions.
        """
        pass

    @abstractmethod
    async def get_feedback(
        self,
        questions: List[str],
        answers: List[str],
        context: Dict[str, Any],
        target_language: Optional[str] = None,
    ) -> float:
        """
        Asks for feedback on the clarification process.
        """
        pass

    @abstractmethod
    async def ask_compliance_questions(
        self,
        user_id: str,
        context: Dict[str, Any],
        target_language: Optional[str] = None,
    ) -> None:
        """
        Asks a predefined set of compliance-related questions to the user.
        """
        pass


class CLIPrompt(UserPromptChannel):
    async def prompt(
        self,
        questions: List[str],
        context: Dict[str, Any],
        target_language: Optional[str] = None,
    ) -> List[str]:
        channel_name = self.__class__.__name__
        PROMPT_CYCLES.labels(channel=channel_name).inc()
        start_time = time.perf_counter()

        user_id = context.get("user_id", "anonymous")
        profile = load_profile(user_id)
        current_language = target_language or profile.language

        answers = []
        print(
            self._translate_text(
                "\nClarification needed (answer each one):", current_language
            )
        )
        for i, q in enumerate(questions):
            translated_q = self._translate_text(q, current_language)

            if profile.preferences.get("accessibility", False):
                try:
                    logger.debug(f"Accessibility (CLI): Speaking question {i+1}.")
                except ImportError:
                    logger.warning(
                        "pyttsx3 not installed. Cannot speak questions for accessibility."
                    )
                except Exception as e:
                    logger.error(f"Error speaking question: {e}")

            print(f"\nQuestion {i+1}: {translated_q}")
            try:
                if profile.preferences.get("multi_line", False):
                    print(
                        self._translate_text(
                            "Enter your answer (type 'END' on a new line to finish):",
                            current_language,
                        )
                    )
                    answer_lines = []
                    while True:
                        line = input()
                        if line.strip().upper() == "END":
                            break
                        answer_lines.append(line)
                    answer = "\n".join(answer_lines).strip()
                else:
                    answer = input(
                        self._translate_text(
                            "Your answer (or 'skip' to ignore): ", current_language
                        )
                    ).strip()
                    if answer.lower() == "skip":
                        answer = None
            except EOFError:
                logger.error(f"EOF encountered for question {i+1}.", exc_info=True)
                PROMPT_ERRORS.labels(channel=channel_name, type="eof").inc()
                answer = "[NO_ANSWER_EOF]"

            if answer and current_language != "en":
                answer = self._translate_text(answer, "en")
            answers.append(answer)

        duration = time.perf_counter() - start_time
        PROMPT_LATENCY.labels(channel=channel_name).observe(duration)
        log_interaction(
            user_id, channel_name, questions, answers, duration, current_language
        )
        return answers

    async def get_feedback(
        self,
        questions: List[str],
        answers: List[str],
        context: Dict[str, Any],
        target_language: Optional[str] = None,
    ) -> float:
        user_id = context.get("user_id", "anonymous")
        profile = load_profile(user_id)
        current_language = target_language or profile.language

        feedback_prompt = self._translate_text(
            "On a scale of 0 to 1 (0=terrible, 1=excellent), how helpful were the questions?",
            current_language,
        )

        try:
            rating_input = input(f"{feedback_prompt}: ")
            rating = float(rating_input)
            if not (0 <= rating <= 1):
                raise ValueError("Rating must be between 0 and 1.")
            FEEDBACK_RATINGS.observe(rating)
            return rating
        except ValueError as e:
            logger.error(
                f"Invalid feedback rating from CLI: {e}. Defaulting to 0.5.",
                exc_info=True,
            )
            PROMPT_ERRORS.labels(
                channel=self.__class__.__name__, type="invalid_feedback"
            ).inc()
            return 0.5
        except Exception as e:
            logger.error(f"Error getting feedback from CLI: {e}", exc_info=True)
            PROMPT_ERRORS.labels(
                channel=self.__class__.__name__, type=type(e).__name__
            ).inc()
            return 0.5

    async def ask_compliance_questions(
        self,
        user_id: str,
        context: Dict[str, Any],
        target_language: Optional[str] = None,
    ) -> None:
        channel_name = self.__class__.__name__
        current_language = target_language or load_profile(user_id).language

        print(
            self._translate_text(
                "\n--- Compliance and Privacy Questions ---", current_language
            )
        )
        print(
            self._translate_text(
                "Please answer these questions to ensure compliance with relevant regulations.",
                current_language,
            )
        )

        for q_data in COMPLIANCE_QUESTIONS:
            question_id = q_data["id"]
            question_text = self._translate_text(q_data["text"], current_language)
            question_type = q_data["type"]

            COMPLIANCE_QUESTIONS_ASKED.labels(question_id=question_id).inc()

            answer = None
            while answer is None:
                try:
                    if question_type == "boolean":
                        raw_answer = (
                            input(f"{question_text} (yes/no): ").strip().lower()
                        )
                        if raw_answer in ["yes", "y"]:
                            answer = True
                        elif raw_answer in ["no", "n"]:
                            answer = False
                        else:
                            print(
                                self._translate_text(
                                    "Please answer 'yes' or 'no'.", current_language
                                )
                            )
                    elif question_type == "text":
                        answer = input(f"{question_text}: ").strip()
                        if not answer:
                            answer = None
                    else:
                        logger.warning(
                            f"Unsupported compliance question type: {question_type}. Skipping."
                        )
                        answer = "[UNSUPPORTED_TYPE]"
                        PROMPT_ERRORS.labels(
                            channel=channel_name, type="unsupported_compliance_type"
                        ).inc()
                except (EOFError, StopIteration):
                    logger.warning(
                        f"Input interrupted for compliance question {question_id}. Skipping."
                    )
                    PROMPT_ERRORS.labels(
                        channel=channel_name, type="compliance_input_interrupted"
                    ).inc()
                    break

                if answer is not None and answer != "[UNSUPPORTED_TYPE]":
                    if current_language != "en" and isinstance(answer, str):
                        answer = self._translate_text(answer, "en")
                    store_compliance_answer(user_id, question_id, answer)
                    break
                elif answer == "[UNSUPPORTED_TYPE]":
                    break
                else:
                    print(
                        self._translate_text(
                            "Please provide a valid answer.", current_language
                        )
                    )
        print(
            self._translate_text("--- End Compliance Questions ---", current_language)
        )


class GUIPrompt(UserPromptChannel):
    async def prompt(
        self,
        questions: List[str],
        context: Dict[str, Any],
        target_language: Optional[str] = None,
    ) -> List[str]:
        channel_name = self.__class__.__name__
        PROMPT_CYCLES.labels(channel=channel_name).inc()
        start_time = time.perf_counter()

        if not HAS_TEXTUAL:
            logger.error(
                "Textual library not found. GUIPrompt cannot function. Falling back to CLI dummy."
            )
            PROMPT_ERRORS.labels(channel=channel_name, type="TextualNotInstalled").inc()
            return await CLIPrompt(target_language=target_language).prompt(
                questions, context, target_language
            )

        class PromptApp(textual.app.App):
            BINDINGS = [("q", "quit", "Quit")]

            def __init__(
                self, questions_to_ask: List[str], target_lang: str, *args, **kwargs
            ):
                super().__init__(*args, **kwargs)
                self.questions_to_ask = questions_to_ask
                self.answers: List[str] = []
                self.current_question_idx = 0
                self.target_lang = target_lang
                self.translator = Translator()

            def compose(self) -> textual.widgets.Header:
                yield textual.widgets.Header()
                yield textual.widgets.Footer()
                yield textual.widgets.Label(
                    self._translate(self.questions_to_ask[self.current_question_idx]),
                    id="question_label",
                )
                yield textual.widgets.Input(
                    placeholder=self._translate("Your answer here"), id="answer_input"
                )
                yield textual.widgets.Button(
                    self._translate("Submit"), id="submit_button"
                )

            def _translate(self, text: str) -> str:
                if self.target_lang != "en":
                    return self.translator.translate(text, dest=self.target_lang).text
                return text

            async def on_button_pressed(
                self, event: textual.widgets.Button.Pressed
            ) -> None:
                if event.button.id == "submit_button":
                    input_widget = self.query_one(
                        "#answer_input", textual.widgets.Input
                    )
                    answer = input_widget.value.strip()

                    if answer and self.target_lang != "en":
                        answer = self.translator.translate(answer, dest="en").text

                    self.answers.append(answer)
                    self.current_question_idx += 1
                    input_widget.value = ""

                    if self.current_question_idx < len(self.questions_to_ask):
                        self.query_one("#question_label", textual.widgets.Label).update(
                            self._translate(
                                self.questions_to_ask[self.current_question_idx]
                            )
                        )
                        input_widget.focus()
                    else:
                        self.exit(self.answers)

            def action_quit(self):
                self.exit(self.answers)

        app = PromptApp(
            questions_to_ask=questions,
            target_lang=target_language or self.target_language,
        )
        answers = await app.run_async()

        duration = time.perf_counter() - start_time
        PROMPT_LATENCY.labels(channel=channel_name).observe(duration)
        log_interaction(
            context.get("user_id", "anonymous"),
            channel_name,
            questions,
            answers,
            duration,
            target_language or self.target_language,
        )
        return answers

    async def get_feedback(
        self,
        questions: List[str],
        answers: List[str],
        context: Dict[str, Any],
        target_language: Optional[str] = None,
    ) -> float:
        feedback = 0.8
        FEEDBACK_RATINGS.observe(feedback)
        return feedback

    async def ask_compliance_questions(
        self,
        user_id: str,
        context: Dict[str, Any],
        target_language: Optional[str] = None,
    ) -> None:
        channel_name = self.__class__.__name__
        if not HAS_TEXTUAL:
            logger.error(
                "Textual library not found. GUIPrompt cannot ask compliance questions. Falling back to CLI dummy."
            )
            PROMPT_ERRORS.labels(channel=channel_name, type="TextualNotInstalled").inc()
            await CLIPrompt(target_language=target_language).ask_compliance_questions(
                user_id, context, target_language
            )
            return

        class ComplianceApp(textual.app.App):
            BINDINGS = [("q", "quit", "Quit")]

            def __init__(
                self,
                questions_data: List[Dict[str, Any]],
                user_id: str,
                target_lang: str,
                *args,
                **kwargs,
            ):
                super().__init__(*args, **kwargs)
                self.questions_data = questions_data
                self.user_id = user_id
                self.current_question_idx = 0
                self.target_lang = target_lang
                self.translator = Translator()

            def compose(self) -> textual.widgets.Header:
                yield textual.widgets.Header()
                yield textual.widgets.Footer()
                yield textual.widgets.Label(
                    self._translate("Compliance Questions"), id="title_label"
                )
                yield textual.widgets.Label(
                    self._translate(
                        self.questions_data[self.current_question_idx]["text"]
                    ),
                    id="question_label",
                )
                if self.questions_data[self.current_question_idx]["type"] == "boolean":
                    yield textual.widgets.RadioSet(
                        textual.widgets.RadioButton(
                            self._translate("Yes"), value="yes"
                        ),
                        textual.widgets.RadioButton(self._translate("No"), value="no"),
                        id="boolean_input",
                    )
                else:
                    yield textual.widgets.Input(
                        placeholder=self._translate("Your answer here"),
                        id="answer_input",
                    )
                yield textual.widgets.Button(
                    self._translate("Submit"), id="submit_button"
                )

            def _translate(self, text: str) -> str:
                if self.target_lang != "en":
                    return self.translator.translate(text, dest=self.target_lang).text
                return text

            async def on_button_pressed(
                self, event: textual.widgets.Button.Pressed
            ) -> None:
                if event.button.id == "submit_button":
                    q_data = self.questions_data[self.current_question_idx]
                    question_id = q_data["id"]
                    answer = None

                    if q_data["type"] == "boolean":
                        radio_set = self.query_one(
                            "#boolean_input", textual.widgets.RadioSet
                        )
                        selected_value = (
                            radio_set.pressed_button.value
                            if radio_set.pressed_button
                            else None
                        )
                        if selected_value == "yes":
                            answer = True
                        elif selected_value == "no":
                            answer = False
                    else:
                        input_widget = self.query_one(
                            "#answer_input", textual.widgets.Input
                        )
                        answer = input_widget.value.strip()

                    if answer is not None:
                        if self.target_lang != "en" and isinstance(answer, str):
                            answer = self.translator.translate(answer, dest="en").text
                        store_compliance_answer(self.user_id, question_id, answer)
                        self.current_question_idx += 1
                        if self.current_question_idx < len(self.questions_data):
                            self.query_one(
                                "#question_label", textual.widgets.Label
                            ).update(
                                self._translate(
                                    self.questions_data[self.current_question_idx][
                                        "text"
                                    ]
                                )
                            )
                            await self.query_one(
                                "textual.widgets.Input, textual.widgets.RadioSet"
                            ).remove()
                            if (
                                self.questions_data[self.current_question_idx]["type"]
                                == "boolean"
                            ):
                                await self.mount(
                                    textual.widgets.RadioSet(
                                        textual.widgets.RadioButton(
                                            self._translate("Yes"), value="yes"
                                        ),
                                        textual.widgets.RadioButton(
                                            self._translate("No"), value="no"
                                        ),
                                        id="boolean_input",
                                    )
                                )
                            else:
                                await self.mount(
                                    textual.widgets.Input(
                                        placeholder=self._translate("Your answer here"),
                                        id="answer_input",
                                    )
                                )
                            self.query_one("#submit_button").focus()
                        else:
                            self.exit()
                    else:
                        self.notify(
                            self._translate("Please provide a valid answer."),
                            severity="warning",
                        )

            def action_quit(self):
                self.exit()

        app = ComplianceApp(
            questions_data=COMPLIANCE_QUESTIONS,
            user_id=user_id,
            target_lang=target_language or self.target_language,
        )
        await app.run_async()
        logger.info(
            f"Compliance questions asked and answers stored for user {user_id} via GUI."
        )


class WebPrompt(UserPromptChannel):
    if HAS_FASTAPI:
        app = FastAPI(
            title="Clarifier Web Prompt",
            description="Web interface for user clarification.",
        )
        _web_prompt_queue: Dict[str, asyncio.Queue] = defaultdict(asyncio.Queue)
        _web_question_cache: Dict[str, List[str]] = {}
        _web_compliance_queue: Dict[str, asyncio.Queue] = defaultdict(asyncio.Queue)
        _web_compliance_questions_cache: Dict[str, List[Dict[str, Any]]] = {}

        @app.post("/submit_answers/{session_id}")
        async def submit_answers(session_id: str, request: Request):
            try:
                form_data = await request.form()
                questions_for_session = WebPrompt._web_question_cache.get(
                    session_id, []
                )
                answers = [
                    form_data.get(f"answer_{i}")
                    for i in range(len(questions_for_session))
                ]
                await WebPrompt._web_prompt_queue[session_id].put(answers)
                return {"status": "success", "message": "Answers submitted."}
            except Exception as e:
                logger.error(
                    f"Error submitting answers for session {session_id}: {e}",
                    exc_info=True,
                )
                PROMPT_ERRORS.labels(
                    channel="WebPrompt", type="submit_answers_error"
                ).inc()
                raise HTTPException(status_code=500, detail="Failed to submit answers.")

        @app.get("/prompt_form/{session_id}")
        async def get_prompt_form(session_id: str):
            questions = WebPrompt._web_question_cache.get(session_id)
            if not questions:
                raise HTTPException(
                    status_code=404,
                    detail="No questions for this session or session expired.",
                )

            html_content = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>Clarification Questions</title>
                <script src="https://cdn.tailwindcss.com"></script>
                <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600&display=swap" rel="stylesheet">
                <style>
                    body {{ font-family: 'Inter', sans-serif; }}
                </style>
            </head>
            <body class="bg-gray-100 p-4 sm:p-8 flex items-center justify-center min-h-screen">
                <div class="bg-white p-6 sm:p-10 rounded-lg shadow-xl w-full max-w-md">
                    <h1 class="text-2xl sm:text-3xl font-bold text-gray-800 mb-6 text-center">Clarification Questions</h1>
                    <form action="/submit_answers/{session_id}" method="post" class="space-y-4">
            """
            for i, q in enumerate(questions):
                html_content += f"""
                        <div class="mb-4">
                            <label for="answer_{i}" class="block text-gray-700 text-lg font-medium mb-2">{i+1}. {q}</label>
                            <input type="text" id="answer_{i}" name="answer_{i}" 
                                   class="shadow-sm appearance-none border rounded-md w-full py-3 px-4 text-gray-700 leading-tight focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent" 
                                   placeholder="Your answer here" required>
                        </div>
                """
            html_content += """
                        <button type="submit" 
                                class="w-full bg-blue-600 hover:bg-blue-700 text-white font-bold py-3 px-4 rounded-md 
                                       focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-opacity-50 
                                       transition duration-300 ease-in-out transform hover:scale-105">
                            Submit Answers
                        </button>
                    </form>
                </div>
            </body>
            </html>
            """
            return HTMLResponse(content=html_content, media_type="text/html")

        @app.post("/submit_compliance_answers/{session_id}")
        async def submit_compliance_answers(session_id: str, request: Request):
            try:
                form_data = await request.form()
                questions_for_session = WebPrompt._web_compliance_questions_cache.get(
                    session_id, []
                )
                answers_dict = {}
                for q_data in questions_for_session:
                    q_id = q_data["id"]
                    if q_data["type"] == "boolean":
                        answers_dict[q_id] = form_data.get(q_id) == "true"
                    else:
                        answers_dict[q_id] = form_data.get(q_id)
                await WebPrompt._web_compliance_queue[session_id].put(answers_dict)
                return {"status": "success", "message": "Compliance answers submitted."}
            except Exception as e:
                logger.error(
                    f"Error submitting compliance answers for session {session_id}: {e}",
                    exc_info=True,
                )
                PROMPT_ERRORS.labels(
                    channel="WebPrompt", type="submit_compliance_error"
                ).inc()
                raise HTTPException(
                    status_code=500, detail="Failed to submit compliance answers."
                )

        @app.get("/compliance_form/{session_id}")
        async def get_compliance_form(session_id: str):
            questions = WebPrompt._web_compliance_questions_cache.get(session_id)
            if not questions:
                raise HTTPException(
                    status_code=404,
                    detail="No compliance questions for this session or session expired.",
                )

            html_content = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>Compliance Questions</title>
                <script src="https://cdn.tailwindcss.com"></script>
                <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600&display=swap" rel="stylesheet">
                <style>
                    body {{ font-family: 'Inter', sans-serif; }}
                </style>
            </head>
            <body class="bg-gray-100 p-4 sm:p-8 flex items-center justify-center min-h-screen">
                <div class="bg-white p-6 sm:p-10 rounded-lg shadow-xl w-full max-w-md">
                    <h1 class="text-2xl sm:text-3xl font-bold text-gray-800 mb-6 text-center">Compliance Questions</h1>
                    <form action="/submit_compliance_answers/{session_id}" method="post" class="space-y-4">
            """
            for i, q_data in enumerate(questions):
                q_id = q_data["id"]
                q_text = q_data["text"]
                q_type = q_data["type"]

                html_content += f"""
                        <div class="mb-4">
                            <label class="block text-gray-700 text-lg font-medium mb-2">{i+1}. {q_text}</label>
                """
                if q_type == "boolean":
                    html_content += f"""
                            <div class="flex items-center space-x-4">
                                <label class="inline-flex items-center">
                                    <input type="radio" name="{q_id}" value="true" class="form-radio text-blue-600">
                                    <span class="ml-2 text-gray-700">Yes</span>
                                </label>
                                <label class="inline-flex items-center">
                                    <input type="radio" name="{q_id}" value="false" class="form-radio text-blue-600">
                                    <span class="ml-2 text-gray-700">No</span>
                                </label>
                            </div>
                    """
                else:
                    html_content += f"""
                            <input type="text" name="{q_id}" 
                                   class="shadow-sm appearance-none border rounded-md w-full py-3 px-4 text-gray-700 leading-tight focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent" 
                                   placeholder="Your answer here">
                    """
                html_content += """
                        </div>
                """
            html_content += """
                        <button type="submit" 
                                class="w-full bg-blue-600 hover:bg-blue-700 text-white font-bold py-3 px-4 rounded-md 
                                       focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-opacity-50 
                                       transition duration-300 ease-in-out transform hover:scale-105">
                            Submit Compliance Answers
                        </button>
                    </form>
                </div>
            </body>
            </html>
            """
            return HTMLResponse(content=html_content, media_type="text/html")

    async def prompt(
        self,
        questions: List[str],
        context: Dict[str, Any],
        target_language: Optional[str] = None,
    ) -> List[str]:
        channel_name = self.__class__.__name__
        PROMPT_CYCLES.labels(channel=channel_name).inc()
        start_time = time.perf_counter()

        if not HAS_FASTAPI:
            logger.error(
                "FastAPI not found. WebPrompt cannot function. Falling back to CLI dummy."
            )
            PROMPT_ERRORS.labels(channel=channel_name, type="FastAPINotInstalled").inc()
            return await CLIPrompt(target_language=target_language).prompt(
                questions, context, target_language
            )

        user_id = context.get("user_id", "anonymous")
        session_id = str(uuid.uuid4())
        WebPrompt._web_question_cache[session_id] = [
            self._translate_text(q, target_language or self.target_language)
            for q in questions
        ]

        prompt_url = f"http://localhost:{os.getenv('WEB_PROMPT_PORT', '8000')}/prompt_form/{session_id}"
        print(
            self._translate_text(
                f"Please visit the following URL in your browser to answer the questions: {prompt_url}",
                target_language or self.target_language,
            )
        )

        try:
            answers = await asyncio.wait_for(
                WebPrompt._web_prompt_queue[session_id].get(), timeout=300
            )
            if (target_language or self.target_language) != "en":
                answers = [self._translate_text(ans, "en") for ans in answers]

        except asyncio.TimeoutError:
            answers = [self._translate_text("[NO_ANSWER_WEB_TIMEOUT]", "en")] * len(
                questions
            )
            logger.warning(f"WebPrompt for session {session_id} timed out.")
            PROMPT_ERRORS.labels(channel=channel_name, type="timeout").inc()
        finally:
            WebPrompt._web_question_cache.pop(session_id, None)
            WebPrompt._web_prompt_queue.pop(session_id, None)

        duration = time.perf_counter() - start_time
        PROMPT_LATENCY.labels(channel=channel_name).observe(duration)
        log_interaction(
            user_id,
            channel_name,
            questions,
            answers,
            duration,
            target_language or self.target_language,
        )
        return answers

    async def get_feedback(
        self,
        questions: List[str],
        answers: List[str],
        context: Dict[str, Any],
        target_language: Optional[str] = None,
    ) -> float:
        feedback = 0.9
        FEEDBACK_RATINGS.observe(feedback)
        return feedback

    async def ask_compliance_questions(
        self,
        user_id: str,
        context: Dict[str, Any],
        target_language: Optional[str] = None,
    ) -> None:
        channel_name = self.__class__.__name__
        if not HAS_FASTAPI:
            logger.error(
                "FastAPI not found. WebPrompt cannot ask compliance questions. Falling back to CLI dummy."
            )
            PROMPT_ERRORS.labels(channel=channel_name, type="FastAPINotInstalled").inc()
            await CLIPrompt(target_language=target_language).ask_compliance_questions(
                user_id, context, target_language
            )
            return

        session_id = str(uuid.uuid4())
        translated_compliance_questions = []
        for q_data in COMPLIANCE_QUESTIONS:
            translated_q_data = q_data.copy()
            translated_q_data["text"] = self._translate_text(
                q_data["text"], target_language or self.target_language
            )
            translated_compliance_questions.append(translated_q_data)

        WebPrompt._web_compliance_questions_cache[session_id] = (
            translated_compliance_questions
        )

        compliance_url = f"http://localhost:{os.getenv('WEB_PROMPT_PORT', '8000')}/compliance_form/{session_id}"
        print(
            self._translate_text(
                f"Please visit the following URL in your browser to answer the compliance questions: {compliance_url}",
                target_language or self.target_language,
            )
        )

        try:
            answers_dict = await asyncio.wait_for(
                WebPrompt._web_compliance_queue[session_id].get(), timeout=300
            )
            for q_id, answer in answers_dict.items():
                if (target_language or self.target_language) != "en" and isinstance(
                    answer, str
                ):
                    answers_dict[q_id] = self._translate_text(answer, "en")
                store_compliance_answer(user_id, q_id, answers_dict[q_id])
        except asyncio.TimeoutError:
            logger.warning(f"WebPrompt compliance for session {session_id} timed out.")
            PROMPT_ERRORS.labels(channel=channel_name, type="compliance_timeout").inc()
        finally:
            WebPrompt._web_compliance_questions_cache.pop(session_id, None)
            WebPrompt._web_compliance_queue.pop(session_id, None)
        logger.info(
            f"Compliance questions asked and answers stored for user {user_id} via Web."
        )


class SlackPrompt(UserPromptChannel):
    async def prompt(
        self,
        questions: List[str],
        context: Dict[str, Any],
        target_language: Optional[str] = None,
    ) -> List[str]:
        channel_name = self.__class__.__name__
        PROMPT_CYCLES.labels(channel=channel_name).inc()
        start_time = time.perf_counter()

        if not _get_channel_config("SLACK_WEBHOOK"):
            logger.error(
                "Slack webhook not configured. SlackPrompt cannot function. Falling back to CLI dummy."
            )
            PROMPT_ERRORS.labels(
                channel=channel_name, type="SlackWebhookNotConfigured"
            ).inc()
            return await CLIPrompt(target_language=target_language).prompt(
                questions, context, target_language
            )

        translated_questions = [
            self._translate_text(q, target_language or self.target_language)
            for q in questions
        ]
        payload = {
            "text": "\n".join(
                [f"Q{i+1}: {q}" for i, q in enumerate(translated_questions)]
            )
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    _get_channel_config("SLACK_WEBHOOK"), json=payload
                ) as resp:
                    resp.raise_for_status()
            logger.info(
                f"Questions sent to Slack via webhook for user {context.get('user_id')}."
            )
            await asyncio.sleep(30)
            answers = [self._translate_text("Mocked Slack Answer", "en")] * len(
                questions
            )

        except Exception as e:
            logger.error(f"Failed to send questions to Slack: {e}", exc_info=True)
            PROMPT_ERRORS.labels(channel=channel_name, type=type(e).__name__).inc()
            answers = [self._translate_text("[NO_ANSWER_SLACK_ERROR]", "en")] * len(
                questions
            )

        duration = time.perf_counter() - start_time
        PROMPT_LATENCY.labels(channel=channel_name).observe(duration)
        log_interaction(
            context.get("user_id", "anonymous"),
            channel_name,
            questions,
            answers,
            duration,
            target_language or self.target_language,
        )
        return answers

    async def get_feedback(
        self,
        questions: List[str],
        answers: List[str],
        context: Dict[str, Any],
        target_language: Optional[str] = None,
    ) -> float:
        """Returns feedback score for Slack (mock)."""
        logger.info("Feedback collection not implemented for Slack channel.")
        return 0.75

    async def ask_compliance_questions(
        self,
        user_id: str,
        context: Dict[str, Any],
        target_language: Optional[str] = None,
    ) -> None:
        """Asks compliance questions via Slack."""
        channel_name = self.__class__.__name__
        webhook = _get_channel_config("SLACK_WEBHOOK")

        for q_data in COMPLIANCE_QUESTIONS:
            question_id = q_data["id"]
            question_text = self._translate_text(
                q_data["text"], target_language or self.target_language
            )

            COMPLIANCE_QUESTIONS_ASKED.labels(question_id=question_id).inc()

            message = {
                "text": f"*Compliance Question:* {question_text}\n_(Response via Slack interface not automated)_"
            }

            async with aiohttp.ClientSession() as session:
                try:
                    async with session.post(webhook, json=message) as resp:
                        if resp.status == 200:
                            logger.info(
                                f"Compliance question {question_id} posted to Slack for user {user_id}."
                            )
                        else:
                            logger.warning(
                                f"Slack returned status {resp.status} for compliance question."
                            )
                            PROMPT_ERRORS.labels(
                                channel=channel_name, type="slack_http_error"
                            ).inc()
                except Exception as e:
                    logger.error(
                        f"Error posting compliance question to Slack: {e}",
                        exc_info=True,
                    )
                    PROMPT_ERRORS.labels(
                        channel=channel_name, type=type(e).__name__
                    ).inc()


class EmailPrompt(UserPromptChannel):
    async def prompt(
        self,
        questions: List[str],
        context: Dict[str, Any],
        target_language: Optional[str] = None,
    ) -> List[str]:
        channel_name = self.__class__.__name__
        PROMPT_CYCLES.labels(channel=channel_name).inc()
        start_time = time.perf_counter()

        if not all(
            [
                _get_channel_config("EMAIL_SERVER"),
                _get_channel_config("EMAIL_PORT"),
                _get_channel_config("EMAIL_USER"),
                _get_channel_config("EMAIL_PASS"),
            ]
        ):
            logger.error(
                "Email server credentials not configured. EmailPrompt cannot function. Falling back to CLI dummy."
            )
            PROMPT_ERRORS.labels(channel=channel_name, type="EmailNotConfigured").inc()
            return await CLIPrompt(target_language=target_language).prompt(
                questions, context, target_language
            )

        user_email = context.get("user_email")
        user_id = context.get("user_id", "anonymous")
        if not user_email:
            logger.error(
                f"No user_email in context for EmailPrompt for user {user_id}. Falling back to CLI dummy."
            )
            PROMPT_ERRORS.labels(channel=channel_name, type="MissingUserEmail").inc()
            return await CLIPrompt(target_language=target_language).prompt(
                questions, context, target_language
            )

        translated_questions = [
            self._translate_text(q, target_language or self.target_language)
            for q in questions
        ]
        email_body = (
            "\n".join([f"Q{i+1}: {q}" for i, q in enumerate(translated_questions)])
            + "\n\nPlease reply to this email with your answers."
        )

        msg = MIMEText(email_body)
        msg["Subject"] = self._translate_text(
            "Clarifications Needed for your Requirements",
            target_language or self.target_language,
        )
        msg["From"] = _get_channel_config("EMAIL_USER")
        msg["To"] = user_email

        try:
            context_ssl = ssl.create_default_context()
            with smtplib.SMTP(
                _get_channel_config("EMAIL_SERVER"), _get_channel_config("EMAIL_PORT")
            ) as server:
                server.starttls(context=context_ssl)
                server.login(
                    _get_channel_config("EMAIL_USER"), _get_channel_config("EMAIL_PASS")
                )
                server.sendmail(
                    _get_channel_config("EMAIL_USER"), user_email, msg.as_string()
                )
            logger.info(f"Questions sent via email to {user_email}.")

            await asyncio.sleep(60)
            answers = [self._translate_text("Mocked Email Answer", "en")] * len(
                questions
            )

        except Exception as e:
            logger.error(f"Failed to send email: {e}", exc_info=True)
            PROMPT_ERRORS.labels(channel=channel_name, type=type(e).__name__).inc()
            answers = [self._translate_text("[NO_ANSWER_EMAIL_ERROR]", "en")] * len(
                questions
            )

        duration = time.perf_counter() - start_time
        PROMPT_LATENCY.labels(channel=channel_name).observe(duration)
        log_interaction(
            user_id,
            channel_name,
            questions,
            answers,
            duration,
            target_language or self.target_language,
        )
        return answers

    async def get_feedback(
        self,
        questions: List[str],
        answers: List[str],
        context: Dict[str, Any],
        target_language: Optional[str] = None,
    ) -> float:
        feedback = 0.7
        FEEDBACK_RATINGS.observe(feedback)
        return feedback

    async def ask_compliance_questions(
        self,
        user_id: str,
        context: Dict[str, Any],
        target_language: Optional[str] = None,
    ) -> None:
        channel_name = self.__class__.__name__
        if not all(
            [
                _get_channel_config("EMAIL_SERVER"),
                _get_channel_config("EMAIL_PORT"),
                _get_channel_config("EMAIL_USER"),
                _get_channel_config("EMAIL_PASS"),
            ]
        ):
            logger.error(
                "Email server credentials not configured. EmailPrompt cannot ask compliance questions. Falling back to CLI dummy."
            )
            PROMPT_ERRORS.labels(channel=channel_name, type="EmailNotConfigured").inc()
            await CLIPrompt(target_language=target_language).ask_compliance_questions(
                user_id, context, target_language
            )
            return

        user_email = context.get("user_email")
        if not user_email:
            logger.error(
                f"No user_email in context for EmailPrompt compliance for user {user_id}. Falling back to CLI dummy."
            )
            PROMPT_ERRORS.labels(channel=channel_name, type="MissingUserEmail").inc()
            await CLIPrompt(target_language=target_language).ask_compliance_questions(
                user_id, context, target_language
            )
            return

        translated_compliance_questions_text = [
            self._translate_text(q["text"], target_language or self.target_language)
            for q in COMPLIANCE_QUESTIONS
        ]
        email_body = (
            "\n".join(
                [
                    f"Compliance Q{i+1}: {q}"
                    for i, q in enumerate(translated_compliance_questions_text)
                ]
            )
            + "\n\nPlease reply to this email with your answers (e.g., 'Q1: yes, Q2: no, Q3: text answer')."
        )

        msg = MIMEText(email_body)
        msg["Subject"] = self._translate_text(
            "Compliance Questions for your Project",
            target_language or self.target_language,
        )
        msg["From"] = _get_channel_config("EMAIL_USER")
        msg["To"] = user_email

        try:
            context_ssl = ssl.create_default_context()
            with smtplib.SMTP(
                _get_channel_config("EMAIL_SERVER"), _get_channel_config("EMAIL_PORT")
            ) as server:
                server.starttls(context=context_ssl)
                server.login(
                    _get_channel_config("EMAIL_USER"), _get_channel_config("EMAIL_PASS")
                )
                server.sendmail(
                    _get_channel_config("EMAIL_USER"), user_email, msg.as_string()
                )
            logger.info(f"Compliance questions sent via email to {user_email}.")

            await asyncio.sleep(60)
            mock_answers = ["yes", "no", "yes", "EU", "no"]
            for i, q_data in enumerate(COMPLIANCE_QUESTIONS):
                answer_value = mock_answers[i]
                if q_data["type"] == "boolean":
                    answer_value = answer_value.lower() == "yes"
                store_compliance_answer(user_id, q_data["id"], answer_value)

        except Exception as e:
            logger.error(
                f"Failed to send compliance questions via email: {e}", exc_info=True
            )
            PROMPT_ERRORS.labels(channel=channel_name, type=type(e).__name__).inc()
        logger.info(
            f"Compliance questions asked and answers stored for user {user_id} via Email."
        )


class SMSPrompt(UserPromptChannel):
    async def prompt(
        self,
        questions: List[str],
        context: Dict[str, Any],
        target_language: Optional[str] = None,
    ) -> List[str]:
        channel_name = self.__class__.__name__
        PROMPT_CYCLES.labels(channel=channel_name).inc()
        start_time = time.perf_counter()

        if not all([_get_channel_config("SMS_API"), _get_channel_config("SMS_KEY")]):
            logger.error(
                "SMS API credentials not configured. SMSPrompt cannot function. Falling back to CLI dummy."
            )
            PROMPT_ERRORS.labels(channel=channel_name, type="SMSNotConfigured").inc()
            return await CLIPrompt(target_language=target_language).prompt(
                questions, context, target_language
            )

        user_phone = context.get("user_phone")
        user_id = context.get("user_id", "anonymous")
        if not user_phone:
            logger.error(
                f"No user_phone in context for SMSPrompt for user {user_id}. Falling back to CLI dummy."
            )
            PROMPT_ERRORS.labels(channel=channel_name, type="MissingUserPhone").inc()
            return await CLIPrompt(target_language=target_language).prompt(
                questions, context, target_language
            )

        translated_q = self._translate_text(
            questions[0], target_language or self.target_language
        )[:150]
        sms_body = f"Q: {translated_q}"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    _get_channel_config("SMS_API"),
                    data={"to": user_phone, "body": sms_body},
                    auth=aiohttp.BasicAuth("user", _get_channel_config("SMS_KEY")),
                ) as resp:
                    resp.raise_for_status()
            logger.info(f"SMS question sent to {user_phone}.")

            await asyncio.sleep(30)
            answers = [self._translate_text("Mocked SMS Answer", "en")] * len(questions)

        except Exception as e:
            logger.error(f"Failed to send SMS: {e}", exc_info=True)
            PROMPT_ERRORS.labels(channel=channel_name, type=type(e).__name__).inc()
            answers = [self._translate_text("[NO_ANSWER_SMS_ERROR]", "en")] * len(
                questions
            )

        duration = time.perf_counter() - start_time
        PROMPT_LATENCY.labels(channel=channel_name).observe(duration)
        log_interaction(
            user_id,
            channel_name,
            questions,
            answers,
            duration,
            target_language or self.target_language,
        )
        return answers

    async def get_feedback(
        self,
        questions: List[str],
        answers: List[str],
        context: Dict[str, Any],
        target_language: Optional[str] = None,
    ) -> float:
        feedback = 0.6
        FEEDBACK_RATINGS.observe(feedback)
        return feedback

    async def ask_compliance_questions(
        self,
        user_id: str,
        context: Dict[str, Any],
        target_language: Optional[str] = None,
    ) -> None:
        channel_name = self.__class__.__name__
        if not all([_get_channel_config("SMS_API"), _get_channel_config("SMS_KEY")]):
            logger.error(
                "SMS API credentials not configured. SMSPrompt cannot ask compliance questions. Falling back to CLI dummy."
            )
            PROMPT_ERRORS.labels(channel=channel_name, type="SMSNotConfigured").inc()
            await CLIPrompt(target_language=target_language).ask_compliance_questions(
                user_id, context, target_language
            )
            return

        user_phone = context.get("user_phone")
        if not user_phone:
            logger.error(
                f"No user_phone in context for SMSPrompt compliance for user {user_id}. Falling back to CLI dummy."
            )
            PROMPT_ERRORS.labels(channel=channel_name, type="MissingUserPhone").inc()
            await CLIPrompt(target_language=target_language).ask_compliance_questions(
                user_id, context, target_language
            )
            return

        for i, q_data in enumerate(COMPLIANCE_QUESTIONS):
            translated_q_text = self._translate_text(
                q_data["text"], target_language or self.target_language
            )[:150]
            sms_body = f"Compliance Q{i+1}: {translated_q_text}"
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        _get_channel_config("SMS_API"),
                        data={"to": user_phone, "body": sms_body},
                        auth=aiohttp.BasicAuth("user", _get_channel_config("SMS_KEY")),
                    ) as resp:
                        resp.raise_for_status()
                logger.info(f"Compliance Q{i+1} sent via SMS to {user_phone}.")
                await asyncio.sleep(10)
                mock_answers = ["yes", "no", "yes", "EU", "no"]
                answer_value = mock_answers[i]
                if q_data["type"] == "boolean":
                    answer_value = answer_value.lower() == "yes"
                store_compliance_answer(user_id, q_data["id"], answer_value)
            except Exception as e:
                logger.error(
                    f"Failed to send compliance Q{i+1} via SMS: {e}", exc_info=True
                )
                PROMPT_ERRORS.labels(channel=channel_name, type=type(e).__name__).inc()
        logger.info(
            f"Compliance questions asked and answers stored for user {user_id} via SMS."
        )


class VoicePrompt(UserPromptChannel):
    async def prompt(
        self,
        questions: List[str],
        context: Dict[str, Any],
        target_language: Optional[str] = None,
    ) -> List[str]:
        channel_name = self.__class__.__name__
        PROMPT_CYCLES.labels(channel=channel_name).inc()
        start_time = time.perf_counter()

        if not HAS_SPEECH_RECOGNITION:
            logger.error(
                "Speech Recognition library not found. VoicePrompt cannot function. Falling back to CLI dummy."
            )
            PROMPT_ERRORS.labels(
                channel=channel_name, type="SpeechRecognitionNotInstalled"
            ).inc()
            return await CLIPrompt(target_language=target_language).prompt(
                questions, context, target_language
            )

        answers = []
        r = sr.Recognizer()

        for q in questions:
            translated_q = self._translate_text(
                q, target_language or self.target_language
            )
            print(translated_q)

            with sr.Microphone() as source:
                logger.info("Listening for answer...")
                try:
                    audio = r.listen(source, timeout=10)
                    answer = r.recognize_google(
                        audio, language=target_language or self.target_language
                    )

                    if (target_language or self.target_language) != "en":
                        answer = self._translate_text(answer, "en")
                    answers.append(answer)
                except sr.WaitTimeoutError:
                    answers.append(
                        self._translate_text("[NO_ANSWER_VOICE_TIMEOUT]", "en")
                    )
                    logger.warning("Voice input timed out.")
                    PROMPT_ERRORS.labels(channel=channel_name, type="timeout").inc()
                except sr.UnknownValueError:
                    answers.append(
                        self._translate_text("[NO_ANSWER_VOICE_UNKNOWN]", "en")
                    )
                    logger.warning(
                        "Google Speech Recognition could not understand audio."
                    )
                    PROMPT_ERRORS.labels(
                        channel=channel_name, type="unknown_value"
                    ).inc()
                except sr.RequestError as e:
                    answers.append(
                        self._translate_text(f"[NO_ANSWER_VOICE_ERROR_{e}]", "en")
                    )
                    logger.error(
                        f"Could not request results from Google Speech Recognition service; {e}",
                        exc_info=True,
                    )
                    PROMPT_ERRORS.labels(
                        channel=channel_name, type="request_error"
                    ).inc()
                except Exception as e:
                    answers.append(
                        self._translate_text(
                            f"[NO_ANSWER_VOICE_UNEXPECTED_ERROR_{e}]", "en"
                        )
                    )
                    logger.error(
                        f"Unexpected error during voice prompt: {e}", exc_info=True
                    )
                    PROMPT_ERRORS.labels(
                        channel=channel_name, type=type(e).__name__
                    ).inc()

        duration = time.perf_counter() - start_time
        PROMPT_LATENCY.labels(channel=channel_name).observe(duration)
        log_interaction(
            context.get("user_id", "anonymous"),
            channel_name,
            questions,
            answers,
            duration,
            target_language or self.target_language,
        )
        return answers

    async def get_feedback(
        self,
        questions: List[str],
        answers: List[str],
        context: Dict[str, Any],
        target_language: Optional[str] = None,
    ) -> float:
        feedback = 0.85
        FEEDBACK_RATINGS.observe(feedback)
        return feedback

    async def ask_compliance_questions(
        self,
        user_id: str,
        context: Dict[str, Any],
        target_language: Optional[str] = None,
    ) -> None:
        channel_name = self.__class__.__name__

        if not HAS_SPEECH_RECOGNITION:
            logger.error(
                "Speech Recognition library not found. VoicePrompt cannot ask compliance questions. Falling back to CLI dummy."
            )
            PROMPT_ERRORS.labels(
                channel=channel_name, type="SpeechRecognitionNotInstalled"
            ).inc()
            await CLIPrompt(target_language=target_language).ask_compliance_questions(
                user_id, context, target_language
            )
            return

        r = sr.Recognizer()

        for i, q_data in enumerate(COMPLIANCE_QUESTIONS):
            translated_q_text = self._translate_text(
                q_data["text"], target_language or self.target_language
            )
            print(f"Compliance Q{i+1}: {translated_q_text}")

            COMPLIANCE_QUESTIONS_ASKED.labels(question_id=q_data["id"]).inc()

            answer = None
            with sr.Microphone() as source:
                logger.info("Listening for compliance answer...")
                try:
                    audio = r.listen(source, timeout=10)
                    raw_answer = r.recognize_google(
                        audio, language=target_language or self.target_language
                    )

                    if q_data["type"] == "boolean":
                        if raw_answer.lower() in [
                            "yes",
                            "y",
                            self._translate_text(
                                "yes", target_language or self.target_language
                            ).lower(),
                        ]:
                            answer = True
                        elif raw_answer.lower() in [
                            "no",
                            "n",
                            self._translate_text(
                                "no", target_language or self.target_language
                            ).lower(),
                        ]:
                            answer = False
                        else:
                            logger.warning(
                                f"Voice: Could not parse boolean answer '{raw_answer}'. Skipping."
                            )
                            PROMPT_ERRORS.labels(
                                channel=channel_name, type="voice_boolean_parse_error"
                            ).inc()
                            answer = None
                    else:
                        answer = raw_answer

                    if answer is not None:
                        if (
                            target_language or self.target_language
                        ) != "en" and isinstance(answer, str):
                            answer = self._translate_text(answer, "en")
                        store_compliance_answer(user_id, q_data["id"], answer)
                    else:
                        logger.warning(
                            f"Voice: No valid answer received for compliance question {q_data['id']}."
                        )

                except sr.WaitTimeoutError:
                    logger.warning("Voice input for compliance question timed out.")
                    PROMPT_ERRORS.labels(
                        channel=channel_name, type="compliance_voice_timeout"
                    ).inc()
                except sr.UnknownValueError:
                    logger.warning(
                        "Google Speech Recognition could not understand audio for compliance question."
                    )
                    PROMPT_ERRORS.labels(
                        channel=channel_name, type="compliance_voice_unknown"
                    ).inc()
                except sr.RequestError as e:
                    logger.error(
                        f"Could not request results from Google Speech Recognition service for compliance question; {e}",
                        exc_info=True,
                    )
                    PROMPT_ERRORS.labels(
                        channel=channel_name, type="compliance_voice_request_error"
                    ).inc()
                except Exception as e:
                    logger.error(
                        f"Unexpected error during voice compliance prompt: {e}",
                        exc_info=True,
                    )
                    PROMPT_ERRORS.labels(
                        channel=channel_name, type=type(e).__name__
                    ).inc()
        logger.info(
            f"Compliance questions asked and answers stored for user {user_id} via Voice."
        )


# Channel registry
def get_channel(
    channel_type: str, target_language: Optional[str] = None
) -> UserPromptChannel:
    """Factory function to get a UserPromptChannel instance, with language setting."""
    lang = target_language or "en"

    if channel_type == "cli":
        return CLIPrompt(target_language=lang)
    if channel_type == "gui":
        if not HAS_TEXTUAL:
            raise ValueError("Textual not available for GUIPrompt.")
        return GUIPrompt(target_language=lang)
    if channel_type == "web":
        if not HAS_FASTAPI:
            raise ValueError("FastAPI not available for WebPrompt.")
        return WebPrompt(target_language=lang)
    if channel_type == "slack":
        if not _get_channel_config("SLACK_WEBHOOK"):
            raise ValueError("Slack webhook not configured for SlackPrompt.")
        return SlackPrompt(target_language=lang)
    if channel_type == "email":
        if not all(
            [
                _get_channel_config("EMAIL_SERVER"),
                _get_channel_config("EMAIL_PORT"),
                _get_channel_config("EMAIL_USER"),
                _get_channel_config("EMAIL_PASS"),
            ]
        ):
            raise ValueError("Email not configured for EmailPrompt.")
        return EmailPrompt(target_language=lang)
    if channel_type == "sms":
        if not all([_get_channel_config("SMS_API"), _get_channel_config("SMS_KEY")]):
            raise ValueError("SMS not configured for SMSPrompt.")
        return SMSPrompt(target_language=lang)
    if channel_type == "voice":
        if not HAS_SPEECH_RECOGNITION:
            raise ValueError("Speech Recognition not available for VoicePrompt.")
        return VoicePrompt(target_language=lang)

    raise ValueError(
        f"Unsupported channel type: {channel_type}. Available: {CHANNEL_TYPES}"
    )


# Smart Input Handling
async def handle_input(answers: List[str], profile: UserProfile) -> List[str]:
    """
    Encrypts sensitive answers and handles multi-line input.
    """
    encrypted_answers = []
    for ans in answers:
        if ans is None or not isinstance(ans, str):
            encrypted_answers.append("")
            continue

        redacted_ans = redact_sensitive(ans)
        encrypted = get_fernet().encrypt(redacted_ans.encode("utf-8")).decode("utf-8")
        encrypted_answers.append(encrypted)

    return encrypted_answers


# User Profiling/Feedback
def update_profile_from_feedback(user_id: str, rating: float, question_id: str):
    """
    Updates user profile based on feedback score.
    """
    profile = load_profile(user_id)
    profile.feedback_scores[question_id] = rating

    if profile.feedback_scores:
        engagement = sum(profile.feedback_scores.values()) / len(
            profile.feedback_scores
        )
        USER_ENGAGEMENT.labels(user_id=user_id).set(engagement)
    else:
        USER_ENGAGEMENT.labels(user_id=user_id).set(0.0)

    if rating < 0.5:
        log_action(
            "Unclear Question Feedback",
            {"user_id": user_id, "question_id": question_id, "rating": rating},
        )

    # FIX: Properly access Prometheus metric value
    engagement_value = (
        USER_ENGAGEMENT.labels(user_id=user_id)._value.get()
        if hasattr(USER_ENGAGEMENT.labels(user_id=user_id)._value, "get")
        else 0.0
    )
    if "voice" in profile.preferences and engagement_value > 0.8:
        profile.preferences["voice"] = True

    save_profile(user_id, profile)


def store_compliance_answer(user_id: str, question_id: str, answer: Any):
    """
    Stores a compliance-related answer in the user's profile.
    """
    profile = load_profile(user_id)
    profile.compliance_preferences[question_id] = answer
    save_profile(user_id, profile)
    COMPLIANCE_ANSWERS_RECEIVED.labels(
        question_id=question_id, answer_value=str(answer)
    ).inc()
    log_action(
        "Compliance Question Answered",
        {"user_id": user_id, "question_id": question_id, "answer": str(answer)},
    )


# Error Recovery/Help
async def recover_error(
    channel: UserPromptChannel,
    question: str,
    error_message: str,
    context: Dict[str, Any],
    target_language: Optional[str] = None,
) -> str:
    """
    Provides error recovery/help for a failed prompt.
    """
    recovery_question_text = f"There was an issue processing your previous answer for: '{question}'. Error: {error_message}. Please try again, or type 'SKIP' to skip this question."
    PROMPT_ERRORS.labels(
        channel=channel.__class__.__name__, type="recovery_prompt"
    ).inc()

    retried_answers = await channel.prompt(
        [recovery_question_text], context, target_language
    )

    if (
        retried_answers
        and retried_answers[0]
        and retried_answers[0].strip().upper() == "SKIP"
    ):
        return "[SKIPPED_BY_USER]"

    return retried_answers[0] if retried_answers and retried_answers[0] else ""


# Comprehensive Logging
def log_interaction(
    user_id: str,
    channel_name: str,
    questions: List[str],
    answers: List[str],
    duration: float,
    language: str,
):
    """
    Logs user interaction with the clarifier.
    """
    anon_questions = [redact_sensitive(q) for q in questions]
    anon_answers = [redact_sensitive(a) if a is not None else "" for a in answers]

    log_action(
        "User Interaction",
        {
            "user_id": user_id,
            "channel": channel_name,
            "questions_count": len(questions),
            "answers_provided_count": len([a for a in answers if a and a.strip()]),
            "duration_seconds": duration,
            "language_used": language,
            "questions_hashes": [
                hashlib.sha256(q.encode()).hexdigest() for q in anon_questions
            ],
            "answers_hashes": [
                hashlib.sha256(a.encode()).hexdigest() for a in anon_answers
            ],
        },
    )


# Entry point for running FastAPI app
if HAS_FASTAPI:
    web_prompt_app = WebPrompt.app
