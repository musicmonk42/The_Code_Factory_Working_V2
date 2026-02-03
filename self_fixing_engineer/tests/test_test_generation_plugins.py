from unittest.mock import Mock, patch

import pytest
from test_generation.gen_plugins import (
    LANGUAGE_GENERATORS,
    PythonTestGenerator,
    generate_tests,
)

# Sample code with multiple functions for testing
code_with_2_funcs = """
def func1(x):
    return x * 2

def func2(y, z):
    return y + z
"""


@pytest.fixture(autouse=True)
def mock_logger(monkeypatch):
    """Mocks the logger to prevent console output during tests."""
    mock_log = Mock()
    monkeypatch.setattr("self_fixing_engineer.test_generation.gen_plugins.logger", mock_log)
    return mock_log


def test_slicing_limit():
    """
    Tests that the PythonTestGenerator correctly limits the number of generated tests
    per function based on the configuration.
    """
    # Create an instance of the generator
    backend = PythonTestGenerator()

    # The `generate` method calls the cached private method, we can test it directly
    # or patch the public method. Let's call the public method to test the full flow.
    # We configure a limit of 1 test per function.
    config = {"max_tests_per_function": 1}

    # We assume the test generation logic creates 2 tests per function by default.
    # Our manual test will mock this behavior to simplify the test.

    # To test the slicing logic, we need to mock the test generation.
    with patch.object(backend, "_generate_cached") as mock_generate_cached:
        # Our mock will return a list that would be generated if the limit was not applied.
        mock_generate_cached.return_value = [
            "import pytest",
            "def test_func1_basic(): ...",
            "def test_func1_edge(): ...",
            "def test_func2_basic(): ...",
            "def test_func2_edge(): ...",
        ]

        result = backend.generate(code_with_2_funcs, config)

    # The expected result should be 1 import + 1 basic test for func1 + 1 basic test for func2.
    assert len(result) == 3
    assert result[0] == "import pytest"
    assert "func1_basic" in result[1]
    assert "func2_basic" in result[2]


def test_generate_tests_plugin_slicing_limit():
    """
    Tests the `generate_tests` plugin's output with the slicing limit.
    """
    with patch.object(
        LANGUAGE_GENERATORS.get("python"),
        "generate",
        return_value=[
            "import pytest",
            "def test_func1_basic(): ...",
            "def test_func1_edge(): ...",
            "def test_func2_basic(): ...",
            "def test_func2_edge(): ...",
        ],
    ):
        result = generate_tests(
            code=code_with_2_funcs,
            language="python",
            config={"max_tests_per_function": 1},
        )

    # The expected output should be 3 tests: import + 1 test per function.
    # The `generate_tests` plugin wraps the generator, so the result is a dictionary.
    assert result["status"] == "success"
    assert len(result["tests"]) == 3
    assert "func1_basic" in result["tests"][1]
    assert "func2_basic" in result["tests"][2]


def test_generate_tests_with_unsupported_language():
    """
    Tests that the plugin correctly handles an unsupported language.
    """
    unsupported_lang = "ruby"
    result = generate_tests("def test(): pass", unsupported_lang)

    assert result["status"] == "error"
    assert f"Language '{unsupported_lang}' is not supported" in result["error"]


def test_generate_tests_with_dangerous_code():
    """
    Tests that the plugin correctly rejects dangerous code.
    """
    dangerous_code = """
def test_eval():
    eval('__import__("os").system("rm -rf /")')
"""
    result = generate_tests(dangerous_code, "python")

    assert result["status"] == "error"
    assert "dangerous constructs" in result["error"]


def test_ai_fallback_is_not_called_if_ai_succeeds():
    """
    Tests that the internal generator is not called if the AI API returns tests.
    """
    mock_ai_tests = ["ai test 1", "ai test 2"]
    with patch(
        "self_fixing_engineer.test_generation.gen_plugins._call_ai_for_tests", return_value=mock_ai_tests
    ):
        with patch.object(
            LANGUAGE_GENERATORS.get("python"), "generate", new=Mock()
        ) as mock_internal_generator:
            result = generate_tests(code_with_2_funcs, language="python")

    assert result["status"] == "success"
    assert len(result["tests"]) == 2
    mock_internal_generator.assert_not_called()


def test_ai_fallback_is_called_if_ai_fails():
    """
    Tests that the internal generator is called if the AI API fails.
    """
    mock_internal_tests = ["internal test 1", "internal test 2"]
    with patch("self_fixing_engineer.test_generation.gen_plugins._call_ai_for_tests", return_value=[]):
        with patch.object(
            LANGUAGE_GENERATORS.get("python"),
            "generate",
            return_value=mock_internal_tests,
        ) as mock_internal_generator:
            result = generate_tests(code_with_2_funcs, language="python")

    assert result["status"] == "success"
    assert len(result["tests"]) == 2
    mock_internal_generator.assert_called_once()
