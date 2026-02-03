"""
Test suite for critical API signature fixes.

This module tests:
1. call_ensemble_api accepts stream parameter
2. process_and_validate_response no longer requires lang parameter
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))


class TestCallEnsembleAPIStreamParameter:
    """Test that call_ensemble_api accepts stream parameter."""

    @pytest.mark.asyncio
    async def test_call_ensemble_api_accepts_stream_parameter(self):
        """Verify call_ensemble_api accepts stream=True and stream=False."""
        from generator.runner.llm_client import call_ensemble_api
        
        # Mock the LLMClient to avoid actual API calls
        with patch("generator.runner.llm_client.LLMClient") as MockLLMClient:
            mock_instance = AsyncMock()
            mock_instance.call_ensemble_api = AsyncMock(
                return_value={"content": "test response", "ensemble_results": []}
            )
            MockLLMClient.return_value = mock_instance
            
            # Reset global client
            import generator.runner.llm_client as llm_client_module
            llm_client_module._async_client = None
            
            # Test with stream=True
            try:
                result = await call_ensemble_api(
                    prompt="test prompt",
                    models=[{"model": "gpt-4o", "provider": "openai"}],
                    voting_strategy="majority",
                    stream=True,
                )
                assert result is not None
                print("✓ call_ensemble_api accepts stream=True")
            except TypeError as e:
                pytest.fail(f"call_ensemble_api should accept stream=True: {e}")
            
            # Test with stream=False
            llm_client_module._async_client = None
            try:
                result = await call_ensemble_api(
                    prompt="test prompt",
                    models=[{"model": "gpt-4o", "provider": "openai"}],
                    voting_strategy="majority",
                    stream=False,
                )
                assert result is not None
                print("✓ call_ensemble_api accepts stream=False")
            except TypeError as e:
                pytest.fail(f"call_ensemble_api should accept stream=False: {e}")

    @pytest.mark.asyncio
    async def test_call_ensemble_api_forwards_stream_to_instance(self):
        """Verify stream parameter is forwarded to instance method."""
        from generator.runner.llm_client import call_ensemble_api
        
        with patch("generator.runner.llm_client.LLMClient") as MockLLMClient:
            mock_instance = AsyncMock()
            mock_instance.call_ensemble_api = AsyncMock(
                return_value={"content": "test response"}
            )
            MockLLMClient.return_value = mock_instance
            
            # Reset global client
            import generator.runner.llm_client as llm_client_module
            llm_client_module._async_client = None
            
            await call_ensemble_api(
                prompt="test",
                models=[{"model": "gpt-4o", "provider": "openai"}],
                voting_strategy="majority",
                stream=True,
            )
            
            # Verify stream parameter was passed to instance method
            mock_instance.call_ensemble_api.assert_called_once()
            call_args = mock_instance.call_ensemble_api.call_args
            assert "stream" in call_args.kwargs
            assert call_args.kwargs["stream"] is True
            print("✓ stream parameter is forwarded to instance method")


class TestDocgenResponseValidatorSignature:
    """Test that ResponseValidator.process_and_validate_response doesn't require lang parameter."""

    @pytest.mark.asyncio
    async def test_process_and_validate_response_no_lang_parameter(self):
        """Verify process_and_validate_response works without lang parameter."""
        from generator.agents.docgen_agent.docgen_response_validator import (
            ResponseValidator,
        )
        
        validator = ResponseValidator(schema={})
        
        # Mock the internal methods to avoid complex setup
        with patch.object(validator, "_parse_response", return_value={"docs": "test"}):
            with patch.object(validator, "_validate_schema", return_value=(True, [])):
                with patch.object(
                    validator, "_enrich_content", return_value="enriched test"
                ):
                    try:
                        result = await validator.process_and_validate_response(
                            raw_response={"content": "test content"},
                            output_format="md",
                            auto_correct=False,
                            repo_path=".",
                        )
                        assert result is not None
                        print("✓ process_and_validate_response works without lang parameter")
                    except TypeError as e:
                        if "lang" in str(e):
                            pytest.fail(
                                f"process_and_validate_response should not require lang parameter: {e}"
                            )
                        raise


def test_api_signature_fixes_summary():
    """Summary test to verify all fixes are in place."""
    print("\n" + "=" * 60)
    print("API Signature Fixes Summary")
    print("=" * 60)
    
    # Check call_ensemble_api signature
    from generator.runner.llm_client import call_ensemble_api
    import inspect
    
    sig = inspect.signature(call_ensemble_api)
    params = list(sig.parameters.keys())
    
    assert "stream" in params, "call_ensemble_api should have 'stream' parameter"
    print(f"✓ call_ensemble_api signature: {params}")
    
    # Check process_and_validate_response signature
    from generator.agents.docgen_agent.docgen_response_validator import ResponseValidator
    
    sig = inspect.signature(ResponseValidator.process_and_validate_response)
    params = list(sig.parameters.keys())
    
    # lang should not be a required parameter
    assert "lang" not in params or sig.parameters["lang"].default != inspect.Parameter.empty, \
        "process_and_validate_response should not require 'lang' parameter"
    print(f"✓ process_and_validate_response signature: {params}")
    
    print("=" * 60)
    print("All API signature fixes verified!")
    print("=" * 60)


if __name__ == "__main__":
    # Run synchronous test
    test_api_signature_fixes_summary()
    
    # Run async tests
    async def run_async_tests():
        test = TestCallEnsembleAPIStreamParameter()
        await test.test_call_ensemble_api_accepts_stream_parameter()
        await test.test_call_ensemble_api_forwards_stream_to_instance()
        
        test2 = TestDocgenResponseValidatorSignature()
        await test2.test_process_and_validate_response_no_lang_parameter()
    
    asyncio.run(run_async_tests())
    print("\n✓ All tests passed!")
