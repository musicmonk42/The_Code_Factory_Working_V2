"""
agent_core.py - Agent core components adapter for simulation module

This module provides proper implementations for agent core functionality.
Uses abstract base classes and factory patterns for extensibility.
"""

import logging
import os
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Callable
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Production mode flag
PRODUCTION_MODE = os.getenv("PRODUCTION_MODE", "false").lower() == "true"


# ============================================================================
# MetaLearning - Proper Implementation with ABC
# ============================================================================


@dataclass
class LearningInsight:
    """Represents a learning insight from the meta-learning system."""

    timestamp: float
    insight_type: str
    data: Dict[str, Any]
    confidence: float = 0.0


class MetaLearningBase(ABC):
    """Abstract base class for meta-learning implementations."""

    @abstractmethod
    def learn(
        self, experiences: List[Dict[str, Any]], **kwargs
    ) -> Optional[Dict[str, Any]]:
        """
        Learn from a set of experiences.

        Args:
            experiences: List of experience dictionaries containing state, action, reward, etc.
            **kwargs: Additional learning parameters

        Returns:
            Dictionary with learning results or None if learning failed
        """
        pass

    @abstractmethod
    def get_insights(self) -> Dict[str, Any]:
        """
        Get current learning insights.

        Returns:
            Dictionary containing current insights and statistics
        """
        pass


class MetaLearning(MetaLearningBase):
    """
    Concrete implementation of MetaLearning.

    This implementation provides basic experience-based learning with
    configurable learning rate and insight generation.
    """

    def __init__(self, *args, **kwargs):
        """
        Initialize MetaLearning.

        Args:
            config: Configuration dictionary with learning parameters
            learning_rate: Float between 0 and 1 for learning speed (default: 0.1)
            insight_threshold: Minimum confidence for generating insights (default: 0.5)
        """
        self.config = kwargs.get("config", {})
        self.learning_rate = self.config.get("learning_rate", 0.1)
        self.insight_threshold = self.config.get("insight_threshold", 0.5)
        self.experiences = []
        self.insights = []
        self.stats = {
            "total_experiences": 0,
            "successful_learnings": 0,
            "failed_learnings": 0,
            "insights_generated": 0,
        }
        logger.info(
            f"MetaLearning initialized with learning_rate={self.learning_rate}, "
            f"insight_threshold={self.insight_threshold}"
        )

    def learn(
        self, experiences: List[Dict[str, Any]], **kwargs
    ) -> Optional[Dict[str, Any]]:
        """
        Learn from experiences by analyzing patterns and updating internal state.

        Args:
            experiences: List of experience dictionaries
            **kwargs: Additional parameters like 'force_learning' (bool)

        Returns:
            Dictionary with learning results including success status and metrics
        """
        if not experiences:
            logger.warning("MetaLearning.learn called with empty experiences list")
            return None

        try:
            self.stats["total_experiences"] += len(experiences)
            self.experiences.extend(experiences)

            # Keep only recent experiences to avoid memory bloat
            max_experiences = self.config.get("max_experiences", 1000)
            if len(self.experiences) > max_experiences:
                self.experiences = self.experiences[-max_experiences:]

            # Analyze patterns in experiences
            patterns = self._analyze_patterns(experiences)

            # Generate insights from patterns
            new_insights = self._generate_insights(patterns)
            self.insights.extend(new_insights)
            self.stats["insights_generated"] += len(new_insights)

            self.stats["successful_learnings"] += 1

            logger.debug(
                f"MetaLearning processed {len(experiences)} experiences, "
                f"found {len(patterns)} patterns, generated {len(new_insights)} insights"
            )

            return {
                "success": True,
                "experiences_processed": len(experiences),
                "patterns_found": len(patterns),
                "insights_generated": len(new_insights),
                "total_insights": len(self.insights),
            }

        except Exception as e:
            self.stats["failed_learnings"] += 1
            logger.error(f"MetaLearning.learn failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    def get_insights(self) -> Dict[str, Any]:
        """
        Get current learning insights and statistics.

        Returns:
            Dictionary with insights, statistics, and metadata
        """
        return {
            "status": "active",
            "insights": [
                {
                    "type": insight.insight_type,
                    "confidence": insight.confidence,
                    "data": insight.data,
                    "timestamp": insight.timestamp,
                }
                for insight in self.insights[-10:]  # Return latest 10 insights
            ],
            "statistics": self.stats.copy(),
            "config": {
                "learning_rate": self.learning_rate,
                "insight_threshold": self.insight_threshold,
            },
        }

    def _analyze_patterns(
        self, experiences: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Analyze patterns in experiences.

        This is a simplified pattern analysis. In production, this could use
        more sophisticated ML techniques.
        """
        patterns = []

        # Group experiences by type
        experience_types = {}
        for exp in experiences:
            exp_type = exp.get("type", "unknown")
            if exp_type not in experience_types:
                experience_types[exp_type] = []
            experience_types[exp_type].append(exp)

        # Identify patterns in each type
        for exp_type, type_experiences in experience_types.items():
            if len(type_experiences) >= 3:  # Need at least 3 for a pattern
                patterns.append(
                    {
                        "type": exp_type,
                        "count": len(type_experiences),
                        "avg_reward": sum(e.get("reward", 0) for e in type_experiences)
                        / len(type_experiences),
                    }
                )

        return patterns

    def _generate_insights(
        self, patterns: List[Dict[str, Any]]
    ) -> List[LearningInsight]:
        """Generate insights from detected patterns."""
        import time

        insights = []
        for pattern in patterns:
            if pattern["count"] >= 5:  # Significant pattern
                confidence = min(pattern["count"] / 10.0, 1.0)
                if confidence >= self.insight_threshold:
                    insights.append(
                        LearningInsight(
                            timestamp=time.time(),
                            insight_type=f"pattern_{pattern['type']}",
                            data=pattern,
                            confidence=confidence,
                        )
                    )

        return insights


# ============================================================================
# PolicyEngine - Proper Implementation with Configuration
# ============================================================================


@dataclass
class Policy:
    """Represents a policy rule."""

    name: str
    condition: Callable[[Dict[str, Any]], bool]
    action: str
    priority: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


class PolicyEngineBase(ABC):
    """Abstract base class for policy engines."""

    @abstractmethod
    def evaluate(self, context: Dict[str, Any], **kwargs) -> bool:
        """
        Evaluate policies against a context.

        Args:
            context: Context dictionary to evaluate
            **kwargs: Additional evaluation parameters

        Returns:
            True if policies pass, False otherwise
        """
        pass

    @abstractmethod
    def add_policy(self, name: str, policy: Any) -> None:
        """Add a policy to the engine."""
        pass


class PolicyEngine(PolicyEngineBase):
    """
    Concrete implementation of PolicyEngine with configuration-driven evaluation.

    This implementation allows for dynamic policy addition and evaluation based
    on configurable rules.
    """

    def __init__(self, *args, **kwargs):
        """
        Initialize PolicyEngine.

        Args:
            config: Configuration dictionary
            default_action: Default action if no policies match (default: "allow")
        """
        self.config = kwargs.get("config", {})
        self.policies: Dict[str, Policy] = {}
        self.default_action = self.config.get("default_action", "allow")
        self.stats = {
            "total_evaluations": 0,
            "allowed": 0,
            "denied": 0,
            "policy_hits": {},
        }
        logger.info(
            f"PolicyEngine initialized with default_action={self.default_action}"
        )

    def evaluate(self, context: Dict[str, Any], **kwargs) -> bool:
        """
        Evaluate all policies against the provided context.

        Args:
            context: Dictionary containing the evaluation context
            **kwargs: Additional parameters like 'strict_mode' (bool)

        Returns:
            True if evaluation passes (allow), False otherwise (deny)
        """
        self.stats["total_evaluations"] += 1
        strict_mode = kwargs.get("strict_mode", False)

        try:
            # Evaluate policies in priority order
            sorted_policies = sorted(
                self.policies.values(), key=lambda p: p.priority, reverse=True
            )

            for policy in sorted_policies:
                try:
                    if policy.condition(context):
                        # Policy matched
                        policy_name = policy.name
                        self.stats["policy_hits"][policy_name] = (
                            self.stats["policy_hits"].get(policy_name, 0) + 1
                        )

                        result = policy.action == "allow"
                        if result:
                            self.stats["allowed"] += 1
                        else:
                            self.stats["denied"] += 1

                        logger.debug(
                            f"Policy '{policy_name}' matched with action '{policy.action}'"
                        )
                        return result

                except Exception as policy_error:
                    logger.error(
                        f"Error evaluating policy '{policy.name}': {policy_error}",
                        exc_info=True,
                    )
                    if strict_mode:
                        # In strict mode, policy errors result in denial
                        self.stats["denied"] += 1
                        return False

            # No policies matched, use default action
            result = self.default_action == "allow"
            if result:
                self.stats["allowed"] += 1
            else:
                self.stats["denied"] += 1

            logger.debug(
                f"No policies matched, using default action: {self.default_action}"
            )
            return result

        except Exception as e:
            logger.error(f"PolicyEngine.evaluate failed: {e}", exc_info=True)
            # On critical error, deny by default for safety
            self.stats["denied"] += 1
            return False

    def add_policy(self, name: str, policy: Any) -> None:
        """
        Add a policy to the engine.

        Args:
            name: Policy name
            policy: Policy object (Policy instance or dict with condition/action)
        """
        if isinstance(policy, Policy):
            self.policies[name] = policy
        elif isinstance(policy, dict):
            # Create Policy from dict
            condition = policy.get("condition", lambda ctx: False)
            action = policy.get("action", "deny")
            priority = policy.get("priority", 0)
            metadata = policy.get("metadata", {})

            self.policies[name] = Policy(
                name=name,
                condition=condition,
                action=action,
                priority=priority,
                metadata=metadata,
            )
        else:
            self.policies[name] = policy

        logger.info(f"Added policy '{name}' to PolicyEngine")

    def get_stats(self) -> Dict[str, Any]:
        """Get policy evaluation statistics."""
        return self.stats.copy()


# ============================================================================
# LLM Factory - Proper Implementation
# ============================================================================


class LLMBase(ABC):
    """Abstract base class for LLM implementations."""

    @abstractmethod
    def generate(self, prompt: str, **kwargs) -> str:
        """Generate text from a prompt."""
        pass

    def __call__(self, *args, **kwargs) -> str:
        """Allow calling the LLM instance directly."""
        return self.generate(*args, **kwargs)


class MockLLM(LLMBase):
    """Mock LLM for testing and development."""

    def __init__(self, provider: str, **config):
        self.provider = provider
        self.config = config
        logger.info(f"MockLLM initialized with provider={provider}")

    def generate(self, prompt: str, **kwargs) -> str:
        """Generate a mock response."""
        if not isinstance(prompt, str):
            raise TypeError(f"prompt must be a string, got {type(prompt)}")

        max_length = kwargs.get("max_length", 100)
        response = f"[Mock {self.provider} response for: {prompt[:50]}...]"
        return response[:max_length]


class OpenAILLM(LLMBase):
    """OpenAI LLM implementation with production mode checks."""

    def __init__(self, **config):
        self.config = config
        self.api_key = config.get("api_key") or os.environ.get("OPENAI_API_KEY")
        self.model = config.get("model", "gpt-3.5-turbo")

        if not self.api_key:
            if PRODUCTION_MODE:
                raise RuntimeError(
                    "CRITICAL: OpenAI API key required in production mode. "
                    "Set OPENAI_API_KEY environment variable."
                )
            logger.warning("OpenAI API key not found, LLM calls will fail or use mock")

    def generate(self, prompt: str, **kwargs) -> str:
        """Generate text using OpenAI API with production mode enforcement."""
        if not isinstance(prompt, str):
            raise TypeError(f"prompt must be a string, got {type(prompt)}")

        if not self.api_key:
            if PRODUCTION_MODE:
                raise RuntimeError(
                    "CRITICAL: Cannot generate text in production mode without API key"
                )
            logger.warning("No API key, returning empty response")
            return ""

        try:
            import openai

            client = openai.OpenAI(api_key=self.api_key)

            response = client.chat.completions.create(
                model=kwargs.get("model", self.model),
                messages=[{"role": "user", "content": prompt}],
                max_tokens=kwargs.get("max_tokens", 150),
            )
            content = response.choices[0].message.content
            return content if content is not None else ""
        except ImportError:
            if PRODUCTION_MODE:
                raise RuntimeError(
                    "CRITICAL: openai package required in production mode. "
                    "Install with: pip install openai"
                )
            logger.warning("openai package not installed, returning empty response")
            return ""
        except Exception as e:
            logger.error(f"OpenAI API call failed: {e}")
            if PRODUCTION_MODE:
                raise
            return ""


class AnthropicLLM(LLMBase):
    """Anthropic Claude LLM implementation with production mode checks."""

    def __init__(self, **config):
        self.config = config
        self.api_key = config.get("api_key") or os.environ.get("ANTHROPIC_API_KEY")
        self.model = config.get("model", "claude-3-haiku-20240307")

        if not self.api_key:
            if PRODUCTION_MODE:
                raise RuntimeError(
                    "CRITICAL: Anthropic API key required in production mode. "
                    "Set ANTHROPIC_API_KEY environment variable."
                )
            logger.warning(
                "Anthropic API key not found, LLM calls will fail or use mock"
            )

    def generate(self, prompt: str, **kwargs) -> str:
        """Generate text using Anthropic API with production mode enforcement."""
        if not isinstance(prompt, str):
            raise TypeError(f"prompt must be a string, got {type(prompt)}")

        if not self.api_key:
            if PRODUCTION_MODE:
                raise RuntimeError(
                    "CRITICAL: Cannot generate text in production mode without API key"
                )
            logger.warning("No API key, returning empty response")
            return ""

        try:
            import anthropic

            client = anthropic.Anthropic(api_key=self.api_key)

            message = client.messages.create(
                model=kwargs.get("model", self.model),
                max_tokens=kwargs.get("max_tokens", 1024),
                messages=[{"role": "user", "content": prompt}],
            )
            # Handle potential empty content or missing text attribute
            if message.content and len(message.content) > 0:
                return getattr(message.content[0], "text", "")
            return ""
        except ImportError:
            if PRODUCTION_MODE:
                raise RuntimeError(
                    "CRITICAL: anthropic package required in production mode. "
                    "Install with: pip install anthropic"
                )
            logger.warning("anthropic package not installed, returning empty response")
            return ""
        except Exception as e:
            logger.error(f"Anthropic API call failed: {e}")
            if PRODUCTION_MODE:
                raise
            return ""


class GeminiLLM(LLMBase):
    """Google Gemini LLM implementation with production mode checks."""

    def __init__(self, **config):
        self.config = config
        self.api_key = config.get("api_key") or os.environ.get("GEMINI_API_KEY")
        self.model = config.get("model", "gemini-pro")

        if not self.api_key:
            if PRODUCTION_MODE:
                raise RuntimeError(
                    "CRITICAL: Gemini API key required in production mode. "
                    "Set GEMINI_API_KEY environment variable."
                )
            logger.warning("Gemini API key not found, LLM calls will fail or use mock")

    def generate(self, prompt: str, **kwargs) -> str:
        """Generate text using Gemini API with production mode enforcement."""
        if not isinstance(prompt, str):
            raise TypeError(f"prompt must be a string, got {type(prompt)}")

        if not self.api_key:
            if PRODUCTION_MODE:
                raise RuntimeError(
                    "CRITICAL: Cannot generate text in production mode without API key"
                )
            logger.warning("No API key, returning empty response")
            return ""

        try:
            import google.generativeai as genai

            genai.configure(api_key=self.api_key)

            model = genai.GenerativeModel(kwargs.get("model", self.model))
            response = model.generate_content(prompt)
            return response.text if response.text is not None else ""
        except ImportError:
            if PRODUCTION_MODE:
                raise RuntimeError(
                    "CRITICAL: google-generativeai package required in production mode. "
                    "Install with: pip install google-generativeai"
                )
            logger.warning(
                "google-generativeai package not installed, returning empty response"
            )
            return ""
        except Exception as e:
            logger.error(f"Gemini API call failed: {e}")
            if PRODUCTION_MODE:
                raise
            return ""


def init_llm(provider: str = "openai", **kwargs) -> LLMBase:
    """
    Factory function to initialize an LLM instance.

    Supports multiple providers with proper fallback to mock implementation.

    Args:
        provider: LLM provider name ("openai", "anthropic", "gemini", "mock")
        **kwargs: Provider-specific configuration
            - api_key: API key for the provider
            - model: Model name to use
            - max_tokens: Maximum tokens for generation
            - temperature: Sampling temperature

    Returns:
        LLMBase: An LLM instance

    Raises:
        ValueError: If provider is not supported

    Examples:
        >>> llm = init_llm("openai", api_key="sk-...", model="gpt-4")
        >>> response = llm.generate("Hello, world!")

        >>> llm = init_llm("anthropic", api_key="sk-ant-...", model="claude-3-opus-20240229")
        >>> response = llm.generate("Explain quantum computing")

        >>> llm = init_llm("gemini", api_key="...", model="gemini-pro")
        >>> response = llm.generate("Write a poem")

        >>> llm = init_llm("mock")  # For testing
        >>> response = llm("Test prompt")
    """
    provider = provider.lower()

    logger.info(f"Initializing LLM with provider={provider}")

    if provider == "mock":
        return MockLLM(provider="mock", **kwargs)

    elif provider == "openai":
        # Check if we should use mock based on environment
        use_mock = os.environ.get("LLM_USE_MOCK", "false").lower() == "true"
        if use_mock:
            logger.info("LLM_USE_MOCK is set, using MockLLM instead of real OpenAI")
            return MockLLM(provider="openai", **kwargs)

        # Check if API key is available
        api_key = kwargs.get("api_key") or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            logger.warning("OpenAI API key not found, using MockLLM")
            return MockLLM(provider="openai", **kwargs)

        return OpenAILLM(**kwargs)

    elif provider == "anthropic":
        # Check if we should use mock based on environment
        use_mock = os.environ.get("LLM_USE_MOCK", "false").lower() == "true"
        if use_mock:
            logger.info("LLM_USE_MOCK is set, using MockLLM instead of real Anthropic")
            return MockLLM(provider="anthropic", **kwargs)

        # Check if API key is available
        api_key = kwargs.get("api_key") or os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            logger.warning("Anthropic API key not found, using MockLLM")
            return MockLLM(provider="anthropic", **kwargs)

        return AnthropicLLM(**kwargs)

    elif provider == "gemini":
        # Check if we should use mock based on environment
        use_mock = os.environ.get("LLM_USE_MOCK", "false").lower() == "true"
        if use_mock:
            logger.info("LLM_USE_MOCK is set, using MockLLM instead of real Gemini")
            return MockLLM(provider="gemini", **kwargs)

        # Check if API key is available
        api_key = kwargs.get("api_key") or os.environ.get("GEMINI_API_KEY")
        if not api_key:
            logger.warning("Gemini API key not found, using MockLLM")
            return MockLLM(provider="gemini", **kwargs)

        return GeminiLLM(**kwargs)

    else:
        raise ValueError(
            f"Unsupported LLM provider: {provider}. "
            f"Supported providers: openai, anthropic, gemini, mock"
        )


# ============================================================================
# Singleton Instances
# ============================================================================

_meta_learning_instance = None
_policy_engine_instance = None


def get_meta_learning_instance(*args, **kwargs) -> MetaLearning:
    """
    Get or create the MetaLearning singleton instance.

    Returns:
        MetaLearning: The singleton instance
    """
    global _meta_learning_instance
    if _meta_learning_instance is None:
        _meta_learning_instance = MetaLearning(*args, **kwargs)
    return _meta_learning_instance


def get_policy_engine_instance(*args, **kwargs) -> PolicyEngine:
    """
    Get or create the PolicyEngine singleton instance.

    Returns:
        PolicyEngine: The singleton instance
    """
    global _policy_engine_instance
    if _policy_engine_instance is None:
        _policy_engine_instance = PolicyEngine(*args, **kwargs)
    return _policy_engine_instance


__all__ = [
    "MetaLearning",
    "MetaLearningBase",
    "PolicyEngine",
    "PolicyEngineBase",
    "Policy",
    "LLMBase",
    "MockLLM",
    "OpenAILLM",
    "AnthropicLLM",
    "GeminiLLM",
    "get_meta_learning_instance",
    "get_policy_engine_instance",
    "init_llm",
    "LearningInsight",
]
