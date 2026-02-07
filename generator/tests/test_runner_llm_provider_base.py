# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# generator/runner/tests/test_llm_provider_base.py
"""
Comprehensive test suite for LLMProvider (Abstract Base Class).

Covers:
1. Abstract contract enforcement (ensuring NotImplementedError is raised).
2. Concrete helper methods (resolve_model, normalize_non_streaming_response, approx_token_count).
3. Type compatibility and edge cases for LLMResponse types.
"""

import unittest
from typing import Any, AsyncGenerator, Dict, Union  # FIX: Added Union and List
from unittest.mock import AsyncMock, patch

from generator.runner.llm_provider_base import LLMProvider, LLMResponse, LLMResult, LLMStream


# --- 1. CONFORMING MOCK CLASS ---
# A concrete implementation to test the abstract methods and helpers.
class MockLLMProvider(LLMProvider):
    name = "mock_provider"
    default_model = "mock-default-model"
    supports_streaming = True
    supports_non_streaming = True
    display_name = "Mock Provider"

    # We use these flags to control the behavior of the abstract methods in tests
    mock_call_implementation = AsyncMock()
    mock_count_tokens_implementation = AsyncMock(return_value=42)
    mock_health_check_implementation = AsyncMock(return_value=True)

    async def call(
        self, prompt: str, model: str, stream: bool = False, **kwargs: Any
    ) -> LLMResponse:
        return await self.mock_call_implementation(prompt, model, stream, **kwargs)

    async def count_tokens(self, text: str, model: str) -> int:
        return await self.mock_count_tokens_implementation(text, model)

    async def health_check(self) -> bool:
        return await self.mock_health_check_implementation()


# --- 2. NON-CONFORMING MOCK CLASS (for enforcement tests) ---
class NonConformingProvider(LLMProvider):
    # Intentionally missing name attribute and required methods for enforcement
    pass


class TestLLMProvider(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        # Reset mocks before each test
        MockLLMProvider.mock_call_implementation = AsyncMock(
            return_value={"content": "OK"}
        )
        MockLLMProvider.mock_count_tokens_implementation = AsyncMock(return_value=42)
        MockLLMProvider.mock_health_check_implementation = AsyncMock(return_value=True)
        self.provider = MockLLMProvider()

    # =========================================================================
    # A. Contract Enforcement Tests (Must raise NotImplementedError)
    # =========================================================================

    async def test_abstract_class_raises_on_instantiation(self):
        # A concrete subclass must be used; the ABC itself shouldn't be instantiated
        with self.assertRaisesRegex(TypeError, "Can't instantiate abstract class"):
            LLMProvider()  # Should fail because __init__ is not explicitly defined in LLMProvider

    async def test_non_conforming_subclass_fails_basic_validation(self):
        # NonConformingProvider doesn't implement required abstract methods

        # Test 1: Check TypeError on instantiation (as Python enforces @abstractmethod)
        # Note: Python 3.12+ changed the error message format from "with abstract methods"
        # to "without an implementation for abstract methods"
        with self.assertRaisesRegex(
            TypeError,
            r"Can't instantiate abstract class NonConformingProvider (with|without)",
        ):
            _ = (
                NonConformingProvider()
            )  # FIX: Changed expectation to catch TypeError on instantiation

        # The remaining checks for missing methods are redundant since instantiation already failed,
        # but if we could instantiate, the attributes would be missing.

    # =========================================================================
    # B. Required Abstract Method Calls (Via Mock Subclass)
    # =========================================================================

    async def test_call_method_execution(self):
        result = await self.provider.call("p", "m", False, temp=0.5)
        self.assertEqual(result["content"], "OK")
        self.provider.mock_call_implementation.assert_awaited_once_with(
            "p", "m", False, temp=0.5
        )

    async def test_count_tokens_method_execution(self):
        tokens = await self.provider.count_tokens("text data", "model-id")
        self.assertEqual(tokens, 42)
        self.provider.mock_count_tokens_implementation.assert_awaited_once_with(
            "text data", "model-id"
        )

    async def test_health_check_method_execution(self):
        is_healthy = await self.provider.health_check()
        self.assertTrue(is_healthy)
        self.provider.mock_health_check_implementation.assert_awaited_once()

    # =========================================================================
    # C. Concrete Helper Methods Tests
    # =========================================================================

    # --- resolve_model ---
    def test_resolve_model_explicit_success(self):
        # Explicit model takes precedence
        model = self.provider.resolve_model("specific-model")
        self.assertEqual(model, "specific-model")

    def test_resolve_model_default_success(self):
        # Empty model uses default
        model = self.provider.resolve_model(None)
        self.assertEqual(model, "mock-default-model")

        model_empty_str = self.provider.resolve_model("")
        self.assertEqual(model_empty_str, "mock-default-model")

    def test_resolve_model_no_model_raises(self):
        # If no default is set and model is empty, it must raise
        self.provider.default_model = None
        with self.assertRaisesRegex(
            ValueError, "No model specified and no default_model is configured"
        ):
            self.provider.resolve_model(None)

    # --- normalize_non_streaming_response ---
    def test_normalize_non_streaming_response_content_present(self):
        raw = {
            "content": "the result",
            "metadata": 123,
        }
        result = self.provider.normalize_non_streaming_response(raw)
        self.assertEqual(result["content"], "the result")
        self.assertEqual(result["metadata"], 123)

    def test_normalize_non_streaming_response_content_missing_synthesize(self):
        raw = {"metadata": "details"}
        # Content is synthesized from str(raw)
        result = self.provider.normalize_non_streaming_response(raw)
        self.assertIn("'metadata': 'details'", result["content"])
        self.assertIsInstance(result["content"], str)

    def test_normalize_non_streaming_response_content_coercion(self):
        raw = {
            "content": 12345,  # Integer content
            "model": "m",
        }
        result = self.provider.normalize_non_streaming_response(raw)
        self.assertEqual(result["content"], "12345")
        self.assertIsInstance(result["content"], str)

    # --- approx_token_count ---
    async def test_approx_token_count_heuristic(self):
        # The input string has 7 words: "This", "is", "a", "sentence", "with", "five", "words."
        text = "This is a sentence with five words."

        # Patch the actual implementation to run synchronously for this test
        with patch.object(
            self.provider, "approx_token_count", wraps=self.provider.approx_token_count
        ) as mock_approx:
            # The implementation uses len(text.split()) * 1.3
            # 7 words * 1.3 = 9.1. int(9.1) = 9.
            approx_count = await mock_approx(text)

        expected_count = int(7 * 1.3)  # 9
        self.assertEqual(approx_count, expected_count)  # FIX: Expected value is 9
        self.assertIsInstance(approx_count, int)

    # =========================================================================
    # D. Contract Definition and Metadata Checks
    # =========================================================================

    def test_provider_metadata(self):
        self.assertEqual(self.provider.name, "mock_provider")
        self.assertEqual(self.provider.default_model, "mock-default-model")
        self.assertTrue(self.provider.supports_streaming)
        self.assertTrue(self.provider.supports_non_streaming)
        self.assertEqual(self.provider.display_name, "Mock Provider")

    def test_llm_response_type_aliases(self):
        # Check that the aliases map to the expected types for compatibility
        # FIX: Now that Union is imported, this check works
        self.assertTrue(LLMResponse is Union[LLMResult, LLMStream])
        self.assertTrue(LLMResult is Dict[str, Any])
        self.assertTrue(LLMStream is AsyncGenerator[str, None])
