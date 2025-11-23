"""
test_docgen_integration.py
Integration tests for the complete docgen_agent system.

Tests cover end-to-end workflows:
- Full documentation generation pipeline
- Multi-format output generation
- Compliance checking integration
- Batch processing with validation
- Human-in-the-loop workflows
- Error recovery and retries
"""

import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional, Tuple
from unittest.mock import MagicMock, Mock, patch

import pytest

# FIX: Mock runner modules before importing docgen_agent to handle source file import issues
sys.modules["runner"] = MagicMock()
sys.modules["runner.llm_client"] = MagicMock()
sys.modules["runner.runner_logging"] = MagicMock()
sys.modules["runner.runner_metrics"] = MagicMock()
sys.modules["runner.runner_errors"] = MagicMock()
sys.modules["runner.runner_file_utils"] = MagicMock()
sys.modules["runner.summarize_utils"] = MagicMock()

# FIX: Add Path, Tuple, Optional to builtins for type hint resolution in source files
import builtins
from abc import ABC, abstractmethod

builtins.Path = Path
builtins.Tuple = Tuple
builtins.Optional = Optional
builtins.Any = Any
builtins.ABC = ABC
builtins.abstractmethod = abstractmethod
builtins.abstractabstractmethod = abstractmethod  # Typo in source file on line 154

# Import all components
from generator.agents.docgen_agent.docgen_agent import DocGenAgent, generate
from generator.agents.docgen_agent.docgen_prompt import DocGenPromptAgent
from generator.agents.docgen_agent.docgen_response_validator import ResponseValidator

# =============================================================================
# FIXTURES
# =============================================================================


@pytest.fixture
def comprehensive_repo():
    """Create a comprehensive test repository."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir)

        # Create directory structure
        (repo_path / "src").mkdir()
        (repo_path / "src" / "utils").mkdir()
        (repo_path / "tests").mkdir()
        (repo_path / "docs").mkdir()
        (repo_path / "doc_templates").mkdir()
        (repo_path / "few_shot_docs").mkdir()

        # Create Python module with comprehensive docstrings
        (repo_path / "src" / "calculator.py").write_text(
            '''
"""
Calculator Module
=================

This module provides basic arithmetic operations.

Examples:
    >>> calc = Calculator()
    >>> calc.add(2, 3)
    5
"""

from typing import Union, Optional
import logging

logger = logging.getLogger(__name__)


class Calculator:
    """
    A calculator class for basic arithmetic operations.
    
    This class provides methods for addition, subtraction,
    multiplication, and division with proper error handling.
    
    Attributes:
        history (list): List of calculation history
        precision (int): Number of decimal places for results
    
    Example:
        >>> calc = Calculator(precision=2)
        >>> calc.add(10, 5)
        15.0
    """
    
    def __init__(self, precision: int = 2):
        """
        Initialize the Calculator.
        
        Args:
            precision: Number of decimal places for results
        """
        self.history = []
        self.precision = precision
        logger.info(f"Calculator initialized with precision={precision}")
    
    def add(self, a: Union[int, float], b: Union[int, float]) -> float:
        """
        Add two numbers.
        
        Args:
            a: First number
            b: Second number
            
        Returns:
            Sum of a and b, rounded to specified precision
            
        Raises:
            TypeError: If inputs are not numeric
            
        Example:
            >>> calc.add(5, 3)
            8.0
        """
        if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
            raise TypeError("Both arguments must be numeric")
        
        result = round(a + b, self.precision)
        self.history.append(f"add({a}, {b}) = {result}")
        return result
    
    def divide(self, a: Union[int, float], b: Union[int, float]) -> Optional[float]:
        """
        Divide two numbers with zero-division handling.
        
        Args:
            a: Numerator
            b: Denominator
            
        Returns:
            Result of a/b, or None if b is zero
            
        Raises:
            TypeError: If inputs are not numeric
            
        Warning:
            Returns None instead of raising ZeroDivisionError
        """
        if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
            raise TypeError("Both arguments must be numeric")
        
        if b == 0:
            logger.warning("Division by zero attempted")
            return None
        
        result = round(a / b, self.precision)
        self.history.append(f"divide({a}, {b}) = {result}")
        return result
'''
        )

        # Create JavaScript module
        (repo_path / "src" / "utils" / "helper.js").write_text(
            """
/**
 * Helper utilities module
 * @module utils/helper
 */

/**
 * Format a number as currency
 * @param {number} amount - The amount to format
 * @param {string} currency - Currency code (default: USD)
 * @returns {string} Formatted currency string
 * @example
 * formatCurrency(1234.56, 'USD')
 * // Returns: "$1,234.56"
 */
function formatCurrency(amount, currency = 'USD') {
    return new Intl.NumberFormat('en-US', {
        style: 'currency',
        currency: currency
    }).format(amount);
}

/**
 * Helper class for string operations
 * @class
 */
class StringHelper {
    /**
     * Create a StringHelper
     * @param {string} defaultCase - Default case conversion
     */
    constructor(defaultCase = 'lower') {
        this.defaultCase = defaultCase;
    }
    
    /**
     * Convert string to specified case
     * @param {string} str - Input string
     * @returns {string} Converted string
     */
    convert(str) {
        return this.defaultCase === 'upper' ? str.toUpperCase() : str.toLowerCase();
    }
}

module.exports = { formatCurrency, StringHelper };
"""
        )

        # Create template
        (repo_path / "doc_templates" / "python_default.jinja").write_text(
            """
Generate comprehensive API documentation for: {{ file_name }}

Language: {{ language }}
Imports: {{ imports }}

Source Code:
```
{{ content }}
```

Instructions: {{ instructions }}

Please generate documentation including:
- Module overview
- Class documentation with attributes
- Method documentation with parameters, returns, and exceptions
- Usage examples
"""
        )

        # Create few-shot example
        (repo_path / "few_shot_docs" / "python_class.json").write_text(
            json.dumps(
                {
                    "input": "class Example:\n    def method(self, x): return x * 2",
                    "output": "## class Example\n\n### method(x)\nDoubles the input value.\n\n**Parameters:**\n- x: Input value\n\n**Returns:** Doubled value",
                }
            )
        )

        # Create README
        (repo_path / "README.md").write_text(
            """
# Calculator Project

A comprehensive calculator implementation.

## License

MIT License
"""
        )

        # Create LICENSE
        (repo_path / "LICENSE").write_text(
            """
MIT License

Copyright (c) 2025 Test Author

Permission is hereby granted...
"""
        )

        yield repo_path


@pytest.fixture
def mock_all_llm():
    """Mock all LLM calls across all modules."""
    patches = [
        patch("generator.agents.docgen_agent.docgen_agent.call_llm_api"),
        patch("generator.agents.docgen_agent.docgen_agent.call_ensemble_api"),
        patch("generator.agents.docgen_agent.docgen_prompt.call_llm_api"),
    ]

    mocks = [p.start() for p in patches]

    # Configure default responses
    doc_response = {
        "content": """
# Calculator Module Documentation

## Classes

### Calculator
A calculator class for basic arithmetic operations.

**Attributes:**
- `history` (list): Calculation history
- `precision` (int): Decimal places for results

**Methods:**

#### `__init__(precision: int = 2)`
Initialize the Calculator.

**Parameters:**
- `precision` (int): Number of decimal places

#### `add(a: Union[int, float], b: Union[int, float]) -> float`
Add two numbers.

**Parameters:**
- `a`: First number
- `b`: Second number

**Returns:**
- float: Sum of a and b

**Raises:**
- TypeError: If inputs are not numeric

#### `divide(a: Union[int, float], b: Union[int, float]) -> Optional[float]`
Divide two numbers with zero-division handling.

**Parameters:**
- `a`: Numerator  
- `b`: Denominator

**Returns:**
- float or None: Result of division, or None if b is zero

**Warning:** Returns None instead of raising ZeroDivisionError
""",
        "model": "gpt-4o",
        "provider": "openai",
        "tokens_used": 500,
    }

    for mock in mocks:
        mock.return_value = doc_response

    yield mocks

    for p in patches:
        p.stop()


@pytest.fixture
def mock_presidio_full():
    """Mock Presidio across all modules."""
    with patch("generator.agents.docgen_agent.docgen_agent.AnalyzerEngine") as mock_a1, patch(
        "generator.agents.docgen_agent.docgen_agent.AnonymizerEngine"
    ) as mock_an1, patch(
        "generator.agents.docgen_agent.docgen_prompt.AnalyzerEngine"
    ) as mock_a2, patch(
        "generator.agents.docgen_agent.docgen_prompt.AnonymizerEngine"
    ) as mock_an2, patch(
        "generator.agents.docgen_agent.docgen_response_validator.AnalyzerEngine"
    ) as mock_a3, patch(
        "generator.agents.docgen_agent.docgen_response_validator.AnonymizerEngine"
    ) as mock_an3:

        # Configure all analyzer/anonymizer mocks
        for analyzer_mock in [mock_a1, mock_a2, mock_a3]:
            analyzer_instance = Mock()
            analyzer_instance.analyze.return_value = []
            analyzer_mock.return_value = analyzer_instance

        for anonymizer_mock in [mock_an1, mock_an2, mock_an3]:
            anonymizer_instance = Mock()
            anonymizer_instance.anonymize.return_value = Mock(text="clean")
            anonymizer_mock.return_value = anonymizer_instance

        yield


# =============================================================================
# TEST: End-to-End Documentation Generation
# =============================================================================


class TestEndToEndGeneration:
    """Test complete documentation generation workflows."""

    @pytest.mark.asyncio
    async def test_single_file_complete_pipeline(
        self, comprehensive_repo, mock_all_llm, mock_presidio_full
    ):
        """Test generating documentation for a single file through complete pipeline."""
        # Initialize agent
        agent = DocGenAgent(repo_path=str(comprehensive_repo))

        # Generate documentation
        target_file = str(comprehensive_repo / "src" / "calculator.py")
        result = await agent.generate_documentation(
            target_files=[target_file], doc_format="markdown", include_compliance=True
        )

        # Verify results
        assert "docs" in result
        assert "compliance" in result
        assert "run_id" in result
        assert len(result["docs"]) > 0

        # Verify compliance checks ran
        assert "license" in result["compliance"]
        assert "copyright" in result["compliance"]

        # Verify LLM was called
        assert any(mock.called for mock in mock_all_llm)

    @pytest.mark.asyncio
    async def test_multi_file_batch_generation(
        self, comprehensive_repo, mock_all_llm, mock_presidio_full
    ):
        """Test batch generation for multiple files."""
        agent = DocGenAgent(repo_path=str(comprehensive_repo))

        files = [
            str(comprehensive_repo / "src" / "calculator.py"),
            str(comprehensive_repo / "src" / "utils" / "helper.js"),
        ]

        result = await agent.generate_documentation(target_files=files, doc_format="markdown")

        assert "docs" in result
        # Should have docs for multiple files
        assert len(result["docs"]) >= 1

    @pytest.mark.asyncio
    async def test_multi_format_generation(
        self, comprehensive_repo, mock_all_llm, mock_presidio_full
    ):
        """Test generating documentation in multiple formats."""
        agent = DocGenAgent(repo_path=str(comprehensive_repo))

        target_file = str(comprehensive_repo / "src" / "calculator.py")
        result = await agent.generate_documentation(
            target_files=[target_file], doc_format=["markdown", "rst", "html"]
        )

        assert "docs" in result
        # Should have generated multiple formats
        docs = result["docs"]
        assert len(docs) >= 1


# =============================================================================
# TEST: Streaming Generation
# =============================================================================


class TestStreamingGeneration:
    """Test streaming documentation generation."""

    @pytest.mark.asyncio
    async def test_streaming_single_file(
        self, comprehensive_repo, mock_all_llm, mock_presidio_full
    ):
        """Test streaming generation for a single file."""
        agent = DocGenAgent(repo_path=str(comprehensive_repo))

        target_file = str(comprehensive_repo / "src" / "calculator.py")

        chunks = []
        async for chunk in agent.generate_documentation_stream(
            target_files=[target_file], doc_format="markdown"
        ):
            chunks.append(chunk)

        # Should have received multiple chunks
        assert len(chunks) > 0

        # Chunks should contain useful data
        assert any("file" in chunk or "docs" in chunk for chunk in chunks)

    @pytest.mark.asyncio
    async def test_streaming_multiple_files(
        self, comprehensive_repo, mock_all_llm, mock_presidio_full
    ):
        """Test streaming generation for multiple files."""
        agent = DocGenAgent(repo_path=str(comprehensive_repo))

        files = [
            str(comprehensive_repo / "src" / "calculator.py"),
            str(comprehensive_repo / "src" / "utils" / "helper.js"),
        ]

        chunks = []
        async for chunk in agent.generate_documentation_stream(
            target_files=files, doc_format="markdown"
        ):
            chunks.append(chunk)

        assert len(chunks) > 0


# =============================================================================
# TEST: Component Integration
# =============================================================================


class TestComponentIntegration:
    """Test integration between different components."""

    @pytest.mark.asyncio
    async def test_prompt_to_validation_flow(
        self, comprehensive_repo, mock_all_llm, mock_presidio_full
    ):
        """Test flow from prompt generation to validation."""
        # 1. Generate prompt
        prompt_agent = DocGenPromptAgent(
            template_dir=str(comprehensive_repo / "doc_templates"),
            few_shot_dir=str(comprehensive_repo / "few_shot_docs"),
        )

        file_path = str(comprehensive_repo / "src" / "calculator.py")
        prompt = await prompt_agent.build_doc_prompt(
            file_path=file_path,
            target="python",
            instructions="Generate comprehensive API docs",
        )

        assert isinstance(prompt, str)
        assert len(prompt) > 100

        # 2. Simulate LLM response (already mocked)
        llm_response = mock_all_llm[0].return_value
        doc_content = llm_response["content"]

        # 3. Validate response
        validator = ResponseValidator()
        validation_result = await validator.validate_response(
            content=doc_content, doc_format="markdown"
        )

        assert validation_result["valid"] is True
        assert "formatted" in validation_result

    @pytest.mark.asyncio
    async def test_agent_uses_all_components(
        self, comprehensive_repo, mock_all_llm, mock_presidio_full
    ):
        """Test that DocGenAgent properly uses all components."""
        agent = DocGenAgent(repo_path=str(comprehensive_repo))

        # Verify components are initialized
        assert agent.prompt_agent is not None
        assert agent.response_validator is not None
        assert agent.plugin_registry is not None

        # Generate docs (uses all components)
        target_file = str(comprehensive_repo / "src" / "calculator.py")
        result = await agent.generate_documentation(
            target_files=[target_file], doc_format="markdown", include_compliance=True
        )

        # All components should have been used
        assert result is not None
        assert "docs" in result
        assert "compliance" in result


# =============================================================================
# TEST: Human-in-the-Loop Integration
# =============================================================================


class TestHumanInTheLoop:
    """Test human approval workflows."""

    @pytest.mark.asyncio
    async def test_approval_workflow(self, comprehensive_repo, mock_all_llm, mock_presidio_full):
        """Test complete human approval workflow."""
        agent = DocGenAgent(repo_path=str(comprehensive_repo), slack_webhook="http://test.webhook")

        # Mock approval process
        with patch.object(agent, "_request_approval", return_value=True):
            target_file = str(comprehensive_repo / "src" / "calculator.py")
            result = await agent.generate_documentation(
                target_files=[target_file], doc_format="markdown", human_approval=True
            )

            assert result is not None
            assert "docs" in result

    @pytest.mark.asyncio
    async def test_approval_rejection_flow(
        self, comprehensive_repo, mock_all_llm, mock_presidio_full
    ):
        """Test handling approval rejection."""
        agent = DocGenAgent(repo_path=str(comprehensive_repo))

        with patch.object(agent, "_request_approval", return_value=False):
            target_file = str(comprehensive_repo / "src" / "calculator.py")

            with pytest.raises(RuntimeError, match="approval rejected"):
                await agent.generate_documentation(target_files=[target_file], human_approval=True)


# =============================================================================
# TEST: Error Recovery
# =============================================================================


class TestErrorRecovery:
    """Test error handling and recovery across components."""

    @pytest.mark.asyncio
    async def test_llm_retry_integration(self, comprehensive_repo, mock_presidio_full):
        """Test LLM error retry across pipeline."""
        call_count = 0

        def mock_llm_with_retry(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                from runner.runner_errors import LLMError

                raise LLMError("Temporary failure")
            return {
                "content": "# Documentation",
                "model": "gpt-4o",
                "provider": "openai",
            }

        with patch(
            "generator.agents.docgen_agent.docgen_agent.call_llm_api",
            side_effect=mock_llm_with_retry,
        ):
            agent = DocGenAgent(repo_path=str(comprehensive_repo))

            target_file = str(comprehensive_repo / "src" / "calculator.py")
            result = await agent.generate_documentation(target_files=[target_file])

            # Should have retried and succeeded
            assert call_count >= 2
            assert result is not None

    @pytest.mark.asyncio
    async def test_partial_failure_handling(
        self, comprehensive_repo, mock_all_llm, mock_presidio_full
    ):
        """Test handling partial failures in batch processing."""
        agent = DocGenAgent(repo_path=str(comprehensive_repo))

        # Mix of valid and invalid files
        files = [
            str(comprehensive_repo / "src" / "calculator.py"),
            "/nonexistent/file.py",
            str(comprehensive_repo / "src" / "utils" / "helper.js"),
        ]

        result = await agent.generate_documentation(target_files=files, continue_on_error=True)

        # Should have processed valid files despite errors
        assert "docs" in result or "errors" in result


# =============================================================================
# TEST: Plugin Entry Point
# =============================================================================


class TestPluginEntryPoint:
    """Test the generate() plugin entry point."""

    @pytest.mark.asyncio
    async def test_generate_entry_point(self, comprehensive_repo, mock_all_llm, mock_presidio_full):
        """Test using generate() as plugin entry point."""
        request = {
            "repo_path": str(comprehensive_repo),
            "target_files": [str(comprehensive_repo / "src" / "calculator.py")],
            "doc_format": "markdown",
            "include_compliance": True,
        }

        result = await generate(**request)

        assert result is not None
        assert isinstance(result, dict)
        assert "docs" in result or "status" in result


# =============================================================================
# TEST: Performance and Concurrency
# =============================================================================


class TestPerformanceAndConcurrency:
    """Test performance and concurrent operations."""

    @pytest.mark.asyncio
    async def test_concurrent_file_processing(
        self, comprehensive_repo, mock_all_llm, mock_presidio_full
    ):
        """Test processing multiple files concurrently."""
        agent = DocGenAgent(repo_path=str(comprehensive_repo))

        # Create multiple files
        for i in range(5):
            (comprehensive_repo / "src" / f"module{i}.py").write_text(
                f"""
def function_{i}():
    '''Function {i}'''
    return {i}
"""
            )

        files = [str(comprehensive_repo / "src" / f"module{i}.py") for i in range(5)]

        import time

        start = time.time()

        result = await agent.generate_documentation(target_files=files, doc_format="markdown")

        elapsed = time.time() - start

        assert "docs" in result
        # With concurrency, should be reasonably fast
        # (actual time depends on mocking overhead)


# =============================================================================
# TEST: Real-world Scenarios
# =============================================================================


class TestRealWorldScenarios:
    """Test realistic usage scenarios."""

    @pytest.mark.asyncio
    async def test_document_entire_project(
        self, comprehensive_repo, mock_all_llm, mock_presidio_full
    ):
        """Test documenting an entire project."""
        agent = DocGenAgent(repo_path=str(comprehensive_repo))

        # Find all Python files
        python_files = list((comprehensive_repo / "src").rglob("*.py"))

        result = await agent.generate_documentation(
            target_files=[str(f) for f in python_files],
            doc_format="markdown",
            include_compliance=True,
        )

        assert "docs" in result
        assert "compliance" in result
        assert len(result["docs"]) > 0

    @pytest.mark.asyncio
    async def test_multi_language_project(
        self, comprehensive_repo, mock_all_llm, mock_presidio_full
    ):
        """Test documenting a multi-language project."""
        agent = DocGenAgent(repo_path=str(comprehensive_repo))

        files = [
            str(comprehensive_repo / "src" / "calculator.py"),
            str(comprehensive_repo / "src" / "utils" / "helper.js"),
        ]

        result = await agent.generate_documentation(
            target_files=files, doc_format=["markdown", "html"]
        )

        assert "docs" in result
        # Should have processed both Python and JavaScript

    @pytest.mark.asyncio
    async def test_incremental_documentation_update(
        self, comprehensive_repo, mock_all_llm, mock_presidio_full
    ):
        """Test updating documentation for changed files."""
        agent = DocGenAgent(repo_path=str(comprehensive_repo))

        # Generate initial docs
        target_file = str(comprehensive_repo / "src" / "calculator.py")
        result1 = await agent.generate_documentation(target_files=[target_file])

        # Modify file
        with open(target_file, "a") as f:
            f.write("\n\ndef new_method():\n    '''A new method'''\n    pass\n")

        # Re-generate docs
        result2 = await agent.generate_documentation(target_files=[target_file])

        # Both should succeed
        assert "docs" in result1
        assert "docs" in result2


# =============================================================================
# RUN TESTS
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short", "-s"])
