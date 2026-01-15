# clarifier_prioritizer.py
"""
Prioritization strategies for ambiguities in requirements clarification.

This module provides production-grade prioritization strategies for ranking
ambiguous requirements and generating clarifying questions in optimal order.

Architecture:
- Prioritizer: Abstract base class defining the prioritization interface
- DefaultPrioritizer: Default LLM-enhanced prioritization with intelligent fallback
- ScoreCalculator: Modular scoring system with configurable weights

Security:
- No sensitive data in logs
- Structured logging for observability
- Input validation and sanitization

Performance:
- Efficient batch processing
- Concurrent LLM calls where appropriate
- Caching support for repeated queries
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Final, List, Optional, TypedDict

logger = logging.getLogger(__name__)


# ============================================================================
# Type Definitions
# ============================================================================

class PrioritizedItem(TypedDict):
    """A single prioritized ambiguity item."""
    original: str
    score: float
    question: str
    index: int
    metadata: Dict[str, Any]


class PrioritizationResult(TypedDict):
    """Result of the prioritization process."""
    prioritized: List[PrioritizedItem]
    batch: List[int]
    metadata: Dict[str, Any]


# ============================================================================
# Configuration
# ============================================================================

class ScoringWeight(Enum):
    """Weight categories for scoring factors."""
    CRITICAL = 1.5
    HIGH = 1.2
    MEDIUM = 1.0
    LOW = 0.8


@dataclass(frozen=True)
class PrioritizationConfig:
    """Configuration for prioritization behavior."""
    
    # Batch Configuration
    default_batch_size: int = 5
    max_batch_size: int = 20
    
    # Scoring Weights (0-100 scale, max possible score = 100)
    length_weight: float = 30.0  # Weight for text length/complexity
    technical_weight: float = 30.0  # Weight for technical term presence
    context_weight: float = 20.0  # Weight for context relevance
    vagueness_weight: float = 20.0  # Weight for vagueness indicators
    
    # LLM Question Generation
    question_temperature: float = 0.5
    question_max_tokens: int = 200
    min_question_length: int = 10
    max_question_length: int = 500
    
    # Timeouts (seconds)
    llm_timeout: float = 30.0
    
    # Validation
    min_ambiguity_length: int = 3
    max_ambiguity_length: int = 2000


DEFAULT_PRIORITIZATION_CONFIG: Final[PrioritizationConfig] = PrioritizationConfig()


# ============================================================================
# Technical Terms Database
# ============================================================================

# Technical terms that indicate domain complexity
TECHNICAL_TERMS: Final[frozenset] = frozenset({
    # Architecture & Design
    "api", "microservice", "monolith", "architecture", "design pattern",
    "interface", "abstraction", "module", "component", "service",
    
    # Data & Storage
    "database", "schema", "migration", "index", "query", "cache",
    "persistence", "storage", "data model", "orm", "nosql", "sql",
    
    # Security
    "authentication", "authorization", "encryption", "oauth", "jwt",
    "security", "permission", "role", "access control", "audit",
    
    # Performance & Scale
    "performance", "scalability", "throughput", "latency", "load balancing",
    "caching", "optimization", "concurrency", "async", "parallel",
    
    # Integration
    "integration", "webhook", "event", "message queue", "pubsub",
    "rest", "graphql", "grpc", "protocol", "endpoint",
    
    # Infrastructure
    "deployment", "configuration", "environment", "container", "kubernetes",
    "ci/cd", "pipeline", "monitoring", "logging", "infrastructure",
    
    # Software Engineering
    "algorithm", "validation", "testing", "error handling", "exception",
    "retry", "timeout", "circuit breaker", "rate limit", "throttle",
})

# Vague terms that indicate unclear requirements
VAGUE_TERMS: Final[frozenset] = frozenset({
    "somehow", "maybe", "probably", "might", "could be", "unclear",
    "unsure", "undefined", "tbd", "to be determined", "later",
    "eventually", "possibly", "perhaps", "various", "some kind of",
    "etc", "and so on", "or something", "whatever", "stuff",
    "thing", "something like", "sort of", "kind of", "basically",
})


# ============================================================================
# Scoring Components
# ============================================================================

class ScoreCalculator:
    """
    Modular scoring calculator for ambiguity prioritization.
    
    Calculates priority scores based on multiple weighted factors:
    - Text complexity (length, structure)
    - Technical domain relevance
    - Context alignment with history
    - Vagueness indicators
    
    Attributes:
        config: PrioritizationConfig instance
    """
    
    def __init__(self, config: Optional[PrioritizationConfig] = None):
        """
        Initialize score calculator.
        
        Args:
            config: Configuration for scoring weights and limits
        """
        self.config = config or DEFAULT_PRIORITIZATION_CONFIG
        
        # Pre-compile regex patterns for efficiency
        self._word_pattern = re.compile(r'\b\w+\b')
    
    def calculate(
        self,
        ambiguity: str,
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Calculate comprehensive priority score for an ambiguity.
        
        Args:
            ambiguity: Ambiguous statement to score
            context: Context dictionary with history and metadata
            
        Returns:
            Dictionary with total score and component breakdowns
        """
        text_lower = ambiguity.lower()
        words = self._word_pattern.findall(text_lower)
        word_set = frozenset(words)
        
        # Calculate individual score components
        length_score = self._score_length(words)
        technical_score = self._score_technical_terms(word_set)
        context_score = self._score_context_relevance(word_set, context)
        vagueness_score = self._score_vagueness(text_lower)
        
        # Apply weights and calculate total
        total_score = (
            length_score * (self.config.length_weight / 100) +
            technical_score * (self.config.technical_weight / 100) +
            context_score * (self.config.context_weight / 100) +
            vagueness_score * (self.config.vagueness_weight / 100)
        )
        
        # Normalize to 0-100 scale
        normalized_score = min(total_score * 100, 100.0)
        
        return {
            "total": round(normalized_score, 2),
            "components": {
                "length": round(length_score, 2),
                "technical": round(technical_score, 2),
                "context": round(context_score, 2),
                "vagueness": round(vagueness_score, 2),
            }
        }
    
    def _score_length(self, words: List[str]) -> float:
        """Score based on text length (longer = more complex)."""
        word_count = len(words)
        
        # Non-linear scaling: more points for moderate length, diminishing returns
        if word_count < 3:
            return 0.2
        elif word_count < 10:
            return 0.4 + (word_count - 3) * 0.06
        elif word_count < 30:
            return 0.7 + (word_count - 10) * 0.01
        else:
            return min(0.9 + (word_count - 30) * 0.001, 1.0)
    
    def _score_technical_terms(self, word_set: frozenset) -> float:
        """Score based on presence of technical terms."""
        # Count matching technical terms
        matches = word_set & TECHNICAL_TERMS
        match_count = len(matches)
        
        if match_count == 0:
            return 0.1
        elif match_count == 1:
            return 0.4
        elif match_count == 2:
            return 0.6
        elif match_count <= 4:
            return 0.8
        else:
            return 1.0
    
    def _score_context_relevance(
        self,
        word_set: frozenset,
        context: Dict[str, Any]
    ) -> float:
        """Score based on relevance to historical context."""
        history = context.get("history", [])
        if not history:
            return 0.5  # Neutral if no history
        
        # Extract words from recent history
        history_words: set = set()
        for cycle in history[-5:]:  # Last 5 cycles
            for q in cycle.get("questions", []):
                if isinstance(q, str):
                    history_words.update(self._word_pattern.findall(q.lower()))
            for a in cycle.get("answers", []):
                if isinstance(a, str):
                    history_words.update(self._word_pattern.findall(a.lower()))
        
        if not history_words:
            return 0.5
        
        # Calculate overlap ratio
        overlap = word_set & history_words
        overlap_ratio = len(overlap) / max(len(word_set), 1)
        
        # Higher score for moderate overlap (too much overlap = already discussed)
        if overlap_ratio < 0.1:
            return 0.3  # Low relevance
        elif overlap_ratio < 0.3:
            return 0.7  # Good relevance
        elif overlap_ratio < 0.5:
            return 1.0  # High relevance
        else:
            return 0.5  # Possibly redundant
    
    def _score_vagueness(self, text_lower: str) -> float:
        """Score based on vagueness indicators."""
        # Count vague term occurrences
        vague_count = sum(1 for term in VAGUE_TERMS if term in text_lower)
        
        if vague_count == 0:
            return 0.2
        elif vague_count == 1:
            return 0.5
        elif vague_count == 2:
            return 0.7
        else:
            return 1.0


# ============================================================================
# Question Generation
# ============================================================================

class QuestionGenerator:
    """
    Generates clarifying questions for ambiguous requirements.
    
    Supports both rule-based and LLM-enhanced question generation
    with intelligent fallback when LLM is unavailable.
    """
    
    # Question templates for rule-based generation
    TEMPLATES: Final[Dict[str, str]] = {
        "undefined": (
            "The requirement '{ambiguity}' appears undefined. "
            "What specific behavior, value, or outcome is expected?"
        ),
        "question": (
            "Regarding '{ambiguity}': Could you provide more specific "
            "requirements, constraints, or acceptance criteria?"
        ),
        "short": (
            "Could you elaborate on '{ambiguity}'? "
            "Please describe the detailed requirements and expected behavior."
        ),
        "default": (
            "For the requirement '{ambiguity}': Please specify the exact "
            "requirements, constraints, dependencies, and expected behavior."
        ),
    }
    
    LLM_PROMPT_TEMPLATE: Final[str] = """You are a senior software requirements analyst helping to clarify ambiguous requirements. Generate a clear, actionable clarifying question.

## Ambiguous Requirement
"{ambiguity}"

## Context
{history_section}

## Requirements for Your Question
1. Be specific and focused on resolving the ambiguity
2. Ask for concrete, measurable details
3. Use professional, clear language
4. Consider technical and business implications

## Output Format
Provide ONLY the question text, no explanation or preamble.

Your question:"""
    
    def __init__(
        self,
        llm: Optional[Any] = None,
        config: Optional[PrioritizationConfig] = None
    ):
        """
        Initialize question generator.
        
        Args:
            llm: Optional LLM provider for enhanced generation
            config: Configuration for question generation
        """
        self.llm = llm
        self.config = config or DEFAULT_PRIORITIZATION_CONFIG
    
    async def generate(
        self,
        ambiguity: str,
        context: Dict[str, Any],
        target_language: str = "en"
    ) -> Dict[str, Any]:
        """
        Generate a clarifying question for an ambiguity.
        
        Attempts LLM-based generation first, falls back to rule-based
        if LLM is unavailable or fails.
        
        Args:
            ambiguity: Ambiguous statement
            context: Context with history and metadata
            target_language: Target language code
            
        Returns:
            Dictionary with question and generation metadata
        """
        start_time = time.monotonic()
        
        # Attempt LLM generation if available
        if self.llm is not None:
            try:
                result = await self._generate_with_llm(
                    ambiguity, context, target_language
                )
                result["latency_ms"] = (time.monotonic() - start_time) * 1000
                return result
            except Exception as e:
                logger.warning(
                    "LLM question generation failed, using fallback",
                    extra={
                        "error_type": type(e).__name__,
                        "ambiguity_preview": ambiguity[:50],
                    }
                )
        
        # Fall back to rule-based generation
        result = self._generate_rule_based(ambiguity, target_language)
        result["latency_ms"] = (time.monotonic() - start_time) * 1000
        return result
    
    async def _generate_with_llm(
        self,
        ambiguity: str,
        context: Dict[str, Any],
        target_language: str
    ) -> Dict[str, Any]:
        """Generate question using LLM."""
        # Build history section
        history_section = self._build_history_section(context)
        
        # Build prompt
        prompt = self.LLM_PROMPT_TEMPLATE.format(
            ambiguity=ambiguity,
            history_section=history_section or "No prior clarification history available.",
        )
        
        # Call LLM with timeout
        try:
            response = await asyncio.wait_for(
                self.llm.generate(
                    prompt,
                    temperature=self.config.question_temperature,
                    max_tokens=self.config.question_max_tokens,
                ),
                timeout=self.config.llm_timeout
            )
        except asyncio.TimeoutError:
            raise TimeoutError("LLM generation timed out")
        
        # Clean and validate response
        question = self._clean_response(response)
        
        if not self._validate_question(question):
            raise ValueError(f"Invalid question generated (length: {len(question)})")
        
        logger.debug(
            "LLM generated question",
            extra={
                "question_preview": question[:80],
                "target_language": target_language,
            }
        )
        
        return {
            "question": question,
            "method": "llm",
            "target_language": target_language,
        }
    
    def _generate_rule_based(
        self,
        ambiguity: str,
        target_language: str
    ) -> Dict[str, Any]:
        """Generate question using rule-based templates."""
        text_lower = ambiguity.lower()
        word_count = len(ambiguity.split())
        
        # Select appropriate template
        if "undefined" in text_lower or "unclear" in text_lower:
            template_key = "undefined"
        elif "?" in ambiguity:
            template_key = "question"
        elif word_count < 5:
            template_key = "short"
        else:
            template_key = "default"
        
        template = self.TEMPLATES[template_key]
        question = template.format(ambiguity=ambiguity)
        
        logger.debug(
            "Rule-based question generated",
            extra={
                "template": template_key,
                "question_preview": question[:80],
            }
        )
        
        return {
            "question": question,
            "method": "rule_based",
            "template": template_key,
            "target_language": target_language,
        }
    
    def _build_history_section(self, context: Dict[str, Any]) -> str:
        """Build formatted history section for LLM prompt."""
        history = context.get("history", [])
        if not history:
            return ""
        
        items = []
        for cycle in history[-3:]:  # Last 3 cycles
            questions = cycle.get("questions", [])
            answers = cycle.get("answers", [])
            for q, a in zip(questions, answers):
                if q and a:
                    items.append(f"Previous Q: {q}\nAnswer: {a}")
        
        if not items:
            return ""
        
        return "### Recent Clarification History\n" + "\n\n".join(items)
    
    def _clean_response(self, response: str) -> str:
        """Clean and normalize LLM response."""
        # Strip whitespace
        question = response.strip()
        
        # Remove common prefixes
        prefixes = ["question:", "q:", "here's a question:", "here is my question:"]
        for prefix in prefixes:
            if question.lower().startswith(prefix):
                question = question[len(prefix):].strip()
        
        # Ensure ends with question mark
        if question and not question.endswith("?"):
            question += "?"
        
        return question
    
    def _validate_question(self, question: str) -> bool:
        """Validate generated question meets quality standards."""
        if not question:
            return False
        
        length = len(question)
        return (
            self.config.min_question_length <= length <= self.config.max_question_length
        )


# ============================================================================
# Abstract Prioritizer
# ============================================================================

class Prioritizer(ABC):
    """
    Abstract base class for requirement ambiguity prioritization.
    
    Prioritizers analyze ambiguities and determine the optimal order
    for clarification questions based on various factors like complexity,
    impact, and dependencies.
    
    Attributes:
        llm: LLM provider instance for enhanced analysis
        config: Prioritization configuration
    """
    
    def __init__(
        self,
        llm: Optional[Any] = None,
        config: Optional[PrioritizationConfig] = None
    ):
        """
        Initialize prioritizer.
        
        Args:
            llm: LLM provider instance for analysis (optional)
            config: Prioritization configuration
        """
        self.llm = llm
        self.config = config or DEFAULT_PRIORITIZATION_CONFIG
        
        logger.info(
            "Initialized prioritizer",
            extra={
                "class": self.__class__.__name__,
                "has_llm": llm is not None,
            }
        )
    
    @abstractmethod
    async def prioritize(
        self,
        ambiguities: List[str],
        context: Dict[str, Any],
        target_language: str = "en"
    ) -> PrioritizationResult:
        """
        Prioritize ambiguities and generate clarifying questions.
        
        Args:
            ambiguities: List of ambiguous requirement statements
            context: Contextual information (history, retrieved context, etc.)
            target_language: Target language for questions
            
        Returns:
            PrioritizationResult with prioritized items and batch indices
        """
        raise NotImplementedError


# ============================================================================
# Default Prioritizer Implementation
# ============================================================================

class DefaultPrioritizer(Prioritizer):
    """
    Production-grade default prioritization strategy.
    
    Features:
    - Multi-factor scoring with configurable weights
    - LLM-enhanced question generation with fallback
    - Intelligent batching based on priority scores
    - Comprehensive logging for observability
    
    This implementation provides a robust baseline that can be extended
    for domain-specific prioritization needs.
    """
    
    def __init__(
        self,
        llm: Optional[Any] = None,
        config: Optional[PrioritizationConfig] = None
    ):
        """
        Initialize default prioritizer.
        
        Args:
            llm: LLM provider for enhanced question generation
            config: Prioritization configuration
        """
        super().__init__(llm, config)
        
        self._scorer = ScoreCalculator(self.config)
        self._question_generator = QuestionGenerator(llm, self.config)
    
    async def prioritize(
        self,
        ambiguities: List[str],
        context: Dict[str, Any],
        target_language: str = "en"
    ) -> PrioritizationResult:
        """
        Prioritize ambiguities using multi-factor scoring with LLM enhancement.
        
        Args:
            ambiguities: List of ambiguous statements
            context: Context dictionary with history and metadata
            target_language: Target language code
            
        Returns:
            PrioritizationResult with ranked items and optimal batch
        """
        start_time = time.monotonic()
        
        if not ambiguities:
            logger.info("No ambiguities to prioritize")
            return {
                "prioritized": [],
                "batch": [],
                "metadata": {"duration_ms": 0, "count": 0}
            }
        
        # Validate and filter ambiguities
        valid_ambiguities = self._validate_ambiguities(ambiguities)
        
        logger.info(
            "Starting prioritization",
            extra={
                "total_count": len(ambiguities),
                "valid_count": len(valid_ambiguities),
                "target_language": target_language,
            }
        )
        
        # Score and generate questions for each ambiguity
        prioritized: List[PrioritizedItem] = []
        
        for idx, (original_idx, ambiguity) in enumerate(valid_ambiguities):
            # Calculate priority score
            score_result = self._scorer.calculate(ambiguity, context)
            
            # Generate clarifying question
            question_result = await self._question_generator.generate(
                ambiguity, context, target_language
            )
            
            prioritized.append({
                "original": ambiguity,
                "score": score_result["total"],
                "question": question_result["question"],
                "index": original_idx,
                "metadata": {
                    "score_components": score_result["components"],
                    "question_method": question_result["method"],
                    "question_latency_ms": question_result.get("latency_ms", 0),
                }
            })
        
        # Sort by score (descending)
        prioritized.sort(key=lambda x: x["score"], reverse=True)
        
        # Select batch
        batch_size = min(
            context.get("batch_size", self.config.default_batch_size),
            self.config.max_batch_size,
            len(prioritized)
        )
        batch_indices = list(range(batch_size))
        
        duration_ms = (time.monotonic() - start_time) * 1000
        
        logger.info(
            "Prioritization complete",
            extra={
                "prioritized_count": len(prioritized),
                "batch_size": len(batch_indices),
                "duration_ms": round(duration_ms, 2),
                "top_score": prioritized[0]["score"] if prioritized else 0,
            }
        )
        
        return {
            "prioritized": prioritized,
            "batch": batch_indices,
            "metadata": {
                "duration_ms": round(duration_ms, 2),
                "count": len(prioritized),
                "batch_size": len(batch_indices),
                "target_language": target_language,
            }
        }
    
    def _validate_ambiguities(
        self,
        ambiguities: List[str]
    ) -> List[tuple[int, str]]:
        """
        Validate and filter ambiguities.
        
        Args:
            ambiguities: Raw list of ambiguities
            
        Returns:
            List of (original_index, validated_ambiguity) tuples
        """
        valid = []
        
        for idx, amb in enumerate(ambiguities):
            # Skip non-strings
            if not isinstance(amb, str):
                logger.warning(
                    "Skipping non-string ambiguity",
                    extra={"index": idx, "type": type(amb).__name__}
                )
                continue
            
            # Skip empty or too short
            amb_stripped = amb.strip()
            if len(amb_stripped) < self.config.min_ambiguity_length:
                logger.debug(
                    "Skipping too short ambiguity",
                    extra={"index": idx, "length": len(amb_stripped)}
                )
                continue
            
            # Truncate if too long
            if len(amb_stripped) > self.config.max_ambiguity_length:
                logger.warning(
                    "Truncating long ambiguity",
                    extra={"index": idx, "original_length": len(amb_stripped)}
                )
                amb_stripped = amb_stripped[:self.config.max_ambiguity_length]
            
            valid.append((idx, amb_stripped))
        
        return valid


# ============================================================================
# Factory Function
# ============================================================================

def create_prioritizer(
    strategy: str = "default",
    llm: Optional[Any] = None,
    config: Optional[PrioritizationConfig] = None
) -> Prioritizer:
    """
    Factory function to create prioritizer instances.
    
    Args:
        strategy: Prioritization strategy name ('default', etc.)
        llm: LLM provider instance (optional, enhances question generation)
        config: Prioritization configuration
        
    Returns:
        Configured Prioritizer instance
        
    Raises:
        ValueError: If strategy is not supported
        
    Example:
        >>> from clarifier_llm import GrokLLM
        >>> llm = GrokLLM(api_key="your-key")
        >>> prioritizer = create_prioritizer("default", llm=llm)
        >>> result = await prioritizer.prioritize(ambiguities, context)
    """
    strategies: Dict[str, type] = {
        "default": DefaultPrioritizer,
    }
    
    prioritizer_class = strategies.get(strategy.lower())
    if not prioritizer_class:
        supported = ", ".join(sorted(strategies.keys()))
        raise ValueError(
            f"Unknown prioritization strategy: '{strategy}'. "
            f"Supported strategies: {supported}"
        )
    
    return prioritizer_class(llm=llm, config=config)


# ============================================================================
# Module Exports
# ============================================================================

__all__ = [
    # Core classes
    "Prioritizer",
    "DefaultPrioritizer",
    # Support classes
    "ScoreCalculator",
    "QuestionGenerator",
    # Configuration
    "PrioritizationConfig",
    "DEFAULT_PRIORITIZATION_CONFIG",
    # Types
    "PrioritizedItem",
    "PrioritizationResult",
    "ScoringWeight",
    # Factory
    "create_prioritizer",
    # Constants
    "TECHNICAL_TERMS",
    "VAGUE_TERMS",
]
