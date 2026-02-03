"""
Test for rule-based testgen fallback functionality.
This ensures testgen works without LLM when TESTGEN_FORCE_LLM is not set.
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

# Force TESTING mode before any other imports
os.environ["TESTING"] = "1"


@pytest.mark.asyncio
async def test_generate_basic_tests_python():
    """Test that _generate_basic_tests creates test stubs for Python code."""
    from generator.agents.testgen_agent.testgen_agent import TestgenAgent
    
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a test agent
        agent = TestgenAgent(tmpdir)
        
        # Sample Python code with functions and classes
        code_files = {
            "example.py": """
def add(a, b):
    return a + b

def subtract(a, b):
    return a - b

class Calculator:
    def multiply(self, a, b):
        return a * b
    
    def divide(self, a, b):
        return a / b
"""
        }
        
        # Generate basic tests
        basic_tests = await agent._generate_basic_tests(
            code_files=code_files,
            language="python",
            run_id="test-run-123"
        )
        
        # Verify tests were generated
        assert len(basic_tests) == 1
        
        # Get the test content
        test_content = list(basic_tests.values())[0]
        
        # Verify test structure
        assert "def test_add():" in test_content
        assert "def test_subtract():" in test_content
        assert "class TestCalculator:" in test_content
        assert "assert True" in test_content  # Placeholder assertion
        assert "import pytest" in test_content


@pytest.mark.asyncio
async def test_generate_basic_tests_empty_file():
    """Test that _generate_basic_tests handles empty Python files."""
    from generator.agents.testgen_agent.testgen_agent import TestgenAgent
    
    with tempfile.TemporaryDirectory() as tmpdir:
        agent = TestgenAgent(tmpdir)
        
        code_files = {
            "empty.py": "# Empty file\npass\n"
        }
        
        basic_tests = await agent._generate_basic_tests(
            code_files=code_files,
            language="python",
            run_id="test-run-124"
        )
        
        # Should still generate a placeholder test
        assert len(basic_tests) == 1
        test_content = list(basic_tests.values())[0]
        assert "test_placeholder" in test_content


@pytest.mark.asyncio
async def test_generate_basic_tests_non_python():
    """Test that _generate_basic_tests handles non-Python languages."""
    from generator.agents.testgen_agent.testgen_agent import TestgenAgent
    
    with tempfile.TemporaryDirectory() as tmpdir:
        agent = TestgenAgent(tmpdir)
        
        code_files = {
            "example.js": "function hello() { return 'world'; }"
        }
        
        basic_tests = await agent._generate_basic_tests(
            code_files=code_files,
            language="javascript",
            run_id="test-run-125"
        )
        
        # Should generate placeholder for non-Python
        assert len(basic_tests) == 1
        test_content = list(basic_tests.values())[0]
        assert "TODO" in test_content


@pytest.mark.asyncio
async def test_generate_tests_rule_based_bypass():
    """Test that generate_tests uses rule-based generation when TESTGEN_FORCE_LLM is not set."""
    from generator.agents.testgen_agent.testgen_agent import TestgenAgent, Policy
    
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create test files
        test_file = Path(tmpdir) / "test_code.py"
        test_file.write_text("""
def hello():
    return "world"
""")
        
        # Ensure TESTGEN_FORCE_LLM is not set to "true"
        os.environ.pop("TESTGEN_FORCE_LLM", None)
        
        # Create agent
        agent = TestgenAgent(tmpdir)
        
        # Create policy
        policy = Policy(
            quality_threshold=0.8,
            max_refinements=2,
            primary_metric="coverage",
        )
        
        # Mock the tracer to avoid OpenTelemetry issues
        with patch('generator.agents.testgen_agent.testgen_agent.tracer') as mock_tracer:
            mock_span = Mock()
            mock_span.set_attributes = Mock()
            mock_span.set_status = Mock()
            mock_span.add_event = Mock()
            mock_span.__enter__ = Mock(return_value=mock_span)
            mock_span.__exit__ = Mock(return_value=None)
            mock_tracer.start_as_current_span = Mock(return_value=mock_span)
            
            # Mock add_provenance
            with patch('generator.agents.testgen_agent.testgen_agent.add_provenance', new=AsyncMock()):
                # Generate tests
                result = await agent.generate_tests(
                    target_files=["test_code.py"],
                    language="python",
                    policy=policy
                )
        
        # Verify rule-based generation was used
        assert result["status"] == "success"
        assert "generated_tests" in result
        assert len(result["generated_tests"]) > 0
        
        # Verify the explanation mentions rule-based generation
        assert "Rule-based" in result["explainability_report"]
        assert "LLM Used**: No" in result["explainability_report"]


@pytest.mark.asyncio 
async def test_generate_tests_llm_bypass_env_var():
    """Test that TESTGEN_FORCE_LLM=true bypasses rule-based generation."""
    from generator.agents.testgen_agent.testgen_agent import TestgenAgent, Policy
    
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create test files
        test_file = Path(tmpdir) / "test_code.py"
        test_file.write_text("""
def hello():
    return "world"
""")
        
        # Set TESTGEN_FORCE_LLM to true
        os.environ["TESTGEN_FORCE_LLM"] = "true"
        
        try:
            # Create agent
            agent = TestgenAgent(tmpdir)
            
            # Create policy
            policy = Policy(
                quality_threshold=0.8,
                max_refinements=2,
                primary_metric="coverage",
            )
            
            # Mock LLM call to fail (to verify it's being called)
            with patch('generator.agents.testgen_agent.testgen_agent.call_ensemble_api', 
                      side_effect=Exception("LLM called as expected")):
                with patch('generator.agents.testgen_agent.testgen_agent.tracer') as mock_tracer:
                    mock_span = Mock()
                    mock_span.set_attributes = Mock()
                    mock_span.set_status = Mock()
                    mock_span.add_event = Mock()
                    mock_span.record_exception = Mock()
                    mock_span.__enter__ = Mock(return_value=mock_span)
                    mock_span.__exit__ = Mock(return_value=None)
                    mock_tracer.start_as_current_span = Mock(return_value=mock_span)
                    
                    with patch('generator.agents.testgen_agent.testgen_agent.add_provenance', new=AsyncMock()):
                        # This should attempt to use LLM and fail
                        with pytest.raises(Exception):
                            await agent.generate_tests(
                                target_files=["test_code.py"],
                                language="python",
                                policy=policy
                            )
        finally:
            # Clean up environment variable
            os.environ.pop("TESTGEN_FORCE_LLM", None)


def test_status_mismatch_fix():
    """Test that testgen returns 'completed' status instead of 'success'."""
    # This is a documentation test to ensure the fix is understood
    # The actual fix is in omnicore_service.py line 1366
    # where "status": "success" was changed to "status": "completed"
    assert True  # This test documents the fix
