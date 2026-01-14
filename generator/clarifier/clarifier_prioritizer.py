# clarifier_prioritizer.py
"""
Prioritization strategies for ambiguities in requirements clarification.

This module provides strategies for prioritizing ambiguous requirements
and generating clarifying questions in an optimal order.

Classes:
- Prioritizer: Abstract base class for prioritization strategies
- DefaultPrioritizer: Default prioritization implementation
"""

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


class Prioritizer(ABC):
    """
    Abstract base class for requirement ambiguity prioritization.
    
    Prioritizers analyze ambiguities and determine the optimal order
    for clarification questions based on various factors like complexity,
    impact, and dependencies.
    """
    
    def __init__(self, llm):
        """
        Initialize prioritizer with an LLM provider.
        
        Args:
            llm: LLM provider instance for analysis
        """
        self.llm = llm
        logger.info(f"Initialized {self.__class__.__name__}")
    
    @abstractmethod
    async def prioritize(
        self,
        ambiguities: List[str],
        context: Dict[str, Any],
        target_language: str = "en"
    ) -> Dict[str, Any]:
        """
        Prioritize ambiguities and generate clarifying questions.
        
        Args:
            ambiguities: List of ambiguous requirement statements
            context: Contextual information (history, retrieved context, etc.)
            target_language: Target language for questions
            
        Returns:
            Dictionary containing:
                - prioritized: List of prioritized ambiguities with scores and questions
                - batch: List of indices for the current batch to ask
                
        Raises:
            NotImplementedError: If not implemented by subclass
        """
        raise NotImplementedError(
            f"{self.__class__.__name__}.prioritize() must be implemented"
        )


class DefaultPrioritizer(Prioritizer):
    """
    Default prioritization strategy for requirement ambiguities.
    
    This implementation uses a simple scoring algorithm that considers:
    1. Length and complexity of the ambiguity
    2. Presence of technical terms
    3. Context relevance from history
    4. Question clarity and actionability
    
    For production use, this can be enhanced with:
    - LLM-based impact analysis
    - Dependency graph analysis
    - User feedback incorporation
    - Domain-specific scoring
    """
    
    async def prioritize(
        self,
        ambiguities: List[str],
        context: Dict[str, Any],
        target_language: str = "en"
    ) -> Dict[str, Any]:
        """
        Prioritize ambiguities using default scoring strategy.
        
        This implementation provides a working baseline that:
        1. Scores each ambiguity based on complexity indicators
        2. Generates clarifying questions
        3. Batches high-priority items for user interaction
        
        Args:
            ambiguities: List of ambiguous statements
            context: Context dictionary with history and retrieved info
            target_language: Target language code
            
        Returns:
            Dictionary with 'prioritized' list and 'batch' indices
        """
        if not ambiguities:
            logger.info("No ambiguities to prioritize")
            return {"prioritized": [], "batch": []}
        
        logger.info(
            f"Prioritizing {len(ambiguities)} ambiguities for language: {target_language}"
        )
        
        # Score and analyze each ambiguity
        prioritized = []
        for idx, ambiguity in enumerate(ambiguities):
            score = self._calculate_score(ambiguity, context)
            question = self._generate_question(ambiguity, target_language)
            
            prioritized.append({
                "original": ambiguity,
                "score": score,
                "question": question,
                "index": idx
            })
        
        # Sort by score (descending - higher score = higher priority)
        prioritized.sort(key=lambda x: x["score"], reverse=True)
        
        # Select batch: top N items (default: up to 5 questions per batch)
        batch_size = context.get("batch_size", 5)
        batch_indices = [i for i in range(min(batch_size, len(prioritized)))]
        
        logger.info(
            f"Prioritization complete: {len(prioritized)} items, "
            f"batch size: {len(batch_indices)}"
        )
        
        return {
            "prioritized": prioritized,
            "batch": batch_indices
        }
    
    def _calculate_score(self, ambiguity: str, context: Dict[str, Any]) -> float:
        """
        Calculate priority score for an ambiguity.
        
        Higher scores indicate higher priority for clarification.
        
        Scoring factors:
        - Length (longer statements are often more complex): 0-30 points
        - Technical terms (indicates domain complexity): 0-30 points  
        - Context relevance (related to history): 0-20 points
        - Specificity (vague terms get higher priority): 0-20 points
        
        Args:
            ambiguity: Ambiguous statement to score
            context: Context dictionary
            
        Returns:
            Priority score (0-100)
        """
        score = 0.0
        text_lower = ambiguity.lower()
        
        # Length-based scoring (longer = more complex)
        word_count = len(ambiguity.split())
        score += min(word_count / 2, 30)  # Cap at 30 points
        
        # Technical term detection
        technical_terms = [
            "api", "database", "authentication", "authorization", "security",
            "performance", "scalability", "integration", "interface", "protocol",
            "algorithm", "architecture", "deployment", "configuration"
        ]
        tech_term_count = sum(1 for term in technical_terms if term in text_lower)
        score += min(tech_term_count * 10, 30)  # Cap at 30 points
        
        # Context relevance scoring
        history = context.get("history", [])
        if history:
            # Check if ambiguity relates to recent history
            recent_text = " ".join(
                [str(cycle.get("questions", [])) for cycle in history[-3:]]
            ).lower()
            
            # Count overlapping words (simple relevance check)
            ambiguity_words = set(text_lower.split())
            history_words = set(recent_text.split())
            overlap = len(ambiguity_words & history_words)
            score += min(overlap, 20)  # Cap at 20 points
        
        # Vagueness detection (indicators of unclear requirements)
        vague_terms = [
            "somehow", "maybe", "probably", "might", "could be", "unclear",
            "unsure", "undefined", "tbd", "to be determined", "later", "eventually"
        ]
        vague_count = sum(1 for term in vague_terms if term in text_lower)
        score += min(vague_count * 10, 20)  # Cap at 20 points
        
        logger.debug(f"Ambiguity score: {score:.2f} for: {ambiguity[:50]}...")
        return score
    
    def _generate_question(self, ambiguity: str, target_language: str) -> str:
        """
        Generate a clarifying question for an ambiguity.
        
        This creates actionable, specific questions that help resolve
        the ambiguity. The questions are designed to elicit concrete
        information from the user.
        
        Args:
            ambiguity: Ambiguous statement
            target_language: Target language for the question
            
        Returns:
            Clarifying question string
        """
        # Extract key terms from the ambiguity
        words = ambiguity.split()
        
        # Generate question based on ambiguity characteristics
        if "undefined" in ambiguity.lower() or "unclear" in ambiguity.lower():
            question = f"Please clarify: {ambiguity}. What specific behavior or value is expected?"
        elif "?" in ambiguity:
            # Already a question, refine it
            question = f"Regarding '{ambiguity}': Can you provide more specific requirements or constraints?"
        elif len(words) < 5:
            # Very short ambiguity - ask for elaboration
            question = f"Can you elaborate on '{ambiguity}'? What are the detailed requirements?"
        else:
            # General case - ask for specification
            question = f"For '{ambiguity}': Please specify the exact requirements, constraints, or expected behavior."
        
        # Language adaptation (stub - in production, use i18n/translation)
        if target_language != "en":
            logger.debug(f"Question language adaptation to {target_language} is not implemented")
            # In production, integrate with translation service here
        
        return question


# Convenience function for backwards compatibility
def create_prioritizer(strategy: str = "default", llm = None) -> Prioritizer:
    """
    Factory function to create prioritizer instances.
    
    Args:
        strategy: Prioritization strategy ('default', etc.)
        llm: LLM provider instance
        
    Returns:
        Configured prioritizer instance
        
    Raises:
        ValueError: If strategy is not supported or llm is None
    """
    if llm is None:
        raise ValueError("LLM provider is required for prioritizer")
    
    strategies = {
        "default": DefaultPrioritizer,
    }
    
    prioritizer_class = strategies.get(strategy.lower())
    if not prioritizer_class:
        raise ValueError(
            f"Unknown prioritization strategy: {strategy}. "
            f"Supported strategies: {list(strategies.keys())}"
        )
    
    return prioritizer_class(llm)
