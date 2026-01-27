"""
Comprehensive test suite for enhanced _clean_code_block function.

This test suite validates the robustness of the code extraction logic
against various LLM response formats, edge cases, and real-world scenarios.
Tests follow AAA (Arrange-Act-Assert) pattern and include descriptive docstrings.
"""
import re


def _clean_code_block(code_content: str) -> str:
    """
    Extracts clean code from LLM responses by removing markdown fences and conversational text.
    
    This function implements a multi-strategy approach to extract code from various LLM response formats:
    1. Language-specific markdown fences (```python, ```py)
    2. Generic markdown fences (```)
    3. Conversational preamble detection and removal
    
    When multiple code blocks are present, the largest block is returned as it's most likely
    to contain the complete implementation rather than a small example.
    
    Args:
        code_content: Raw LLM response text that may contain code mixed with explanatory text
        
    Returns:
        Cleaned code string with markdown fences and preamble removed
    """
    if not code_content:
        return ""
    
    text = code_content.strip()
    
    # Strategy 1: Extract from language-specific markdown fences (```python, ```py)
    # Non-optional language specifier to avoid overlap with Strategy 2
    code_block_pattern = r'```(?:python|py)\s*\n(.*?)```'
    matches = re.findall(code_block_pattern, text, flags=re.DOTALL | re.IGNORECASE)
    
    if matches:
        return max(matches, key=len).strip()
    
    # Strategy 2: Extract from generic markdown fences (```)
    generic_pattern = r'```\s*\n(.*?)```'
    matches = re.findall(generic_pattern, text, flags=re.DOTALL)
    if matches:
        return max(matches, key=len).strip()
    
    # Strategy 3: Strip conversational preamble
    lines = text.split('\n')
    code_start_idx = 0
    
    PREAMBLE_PATTERNS = (
        'here is', 'here\'s', 'this is', 'this will', 
        'the following', 'below is', 'below you',
        'i will', 'i\'ve', 'i have'
    )
    CODE_MARKERS = ('import ', 'from ', 'def ', 'class ', '#', '"""', "'''", '@', 'if ', 'for ', 'while ')
    
    for i, line in enumerate(lines):
        stripped = line.strip().lower()
        
        if not stripped:
            continue
            
        if any(stripped.startswith(pattern) for pattern in PREAMBLE_PATTERNS):
            code_start_idx = i + 1
            continue
            
        if stripped.startswith(CODE_MARKERS):
            break
            
        if '=' in stripped and not any(stripped.startswith(pattern) for pattern in PREAMBLE_PATTERNS):
            break
    
    return '\n'.join(lines[code_start_idx:]).strip()


# ==============================================================================
# Test Suite
# ==============================================================================

def test_clean_code_block_with_python_fence():
    """Test extraction from Python-specific markdown fence."""
    # Arrange
    input_text = """```python
def hello():
    print("Hello, World!")
```"""
    expected = 'def hello():\n    print("Hello, World!")'
    
    # Act
    result = _clean_code_block(input_text)
    
    # Assert
    assert result == expected, f"Expected:\n{expected}\n\nGot:\n{result}"
    print("✓ Test passed: Python-specific fence extraction")


def test_clean_code_block_with_py_fence():
    """Test extraction from 'py' language fence variant."""
    # Arrange
    input_text = """```py
x = 42
```"""
    expected = 'x = 42'
    
    # Act
    result = _clean_code_block(input_text)
    
    # Assert
    assert result == expected, f"Expected:\n{expected}\n\nGot:\n{result}"
    print("✓ Test passed: 'py' fence extraction")


def test_clean_code_block_with_preamble_and_postamble():
    """Test removal of conversational text before and after code."""
    # Arrange
    input_text = """Here's the code you requested:

```python
def add(a, b):
    return a + b
```

Let me know if you need any changes!"""
    expected = 'def add(a, b):\n    return a + b'
    
    # Act
    result = _clean_code_block(input_text)
    
    # Assert
    assert result == expected, f"Expected:\n{expected}\n\nGot:\n{result}"
    print("✓ Test passed: Preamble and postamble removal")


def test_clean_code_block_multiple_fences_returns_largest():
    """Test that largest code block is returned when multiple blocks exist."""
    # Arrange
    input_text = """Here's a small example:

```python
x = 1
```

And here's the full implementation:

```python
def calculate(x, y):
    result = x + y
    return result * 2
```"""
    expected = 'def calculate(x, y):\n    result = x + y\n    return result * 2'
    
    # Act
    result = _clean_code_block(input_text)
    
    # Assert
    assert result == expected, f"Expected:\n{expected}\n\nGot:\n{result}"
    print("✓ Test passed: Largest block extraction from multiple fences")


def test_clean_code_block_no_fences_with_preamble():
    """Test preamble stripping when no markdown fences are present."""
    # Arrange
    input_text = """Here is the solution:

import os

def main():
    print("Hello")"""
    expected = 'import os\n\ndef main():\n    print("Hello")'
    
    # Act
    result = _clean_code_block(input_text)
    
    # Assert
    assert result == expected, f"Expected:\n{expected}\n\nGot:\n{result}"
    print("✓ Test passed: No fences with preamble")


def test_clean_code_block_plain_code():
    """Test that plain code without formatting is returned unchanged."""
    # Arrange
    input_text = """import sys

def greet(name):
    return f"Hello, {name}!" """
    expected = 'import sys\n\ndef greet(name):\n    return f"Hello, {name}!"'
    
    # Act
    result = _clean_code_block(input_text)
    
    # Assert
    assert result == expected, f"Expected:\n{expected}\n\nGot:\n{result}"
    print("✓ Test passed: Plain code preservation")


def test_clean_code_block_generic_fence():
    """Test extraction from generic fence without language specifier."""
    # Arrange
    input_text = """```
def test():
    pass
```"""
    expected = 'def test():\n    pass'
    
    # Act
    result = _clean_code_block(input_text)
    
    # Assert
    assert result == expected, f"Expected:\n{expected}\n\nGot:\n{result}"
    print("✓ Test passed: Generic fence extraction")


def test_clean_code_block_empty_input():
    """Test that empty input returns empty string."""
    # Arrange
    input_text = ""
    expected = ""
    
    # Act
    result = _clean_code_block(input_text)
    
    # Assert
    assert result == expected, f"Expected empty string, got: {repr(result)}"
    print("✓ Test passed: Empty input handling")


def test_clean_code_block_whitespace_only():
    """Test that whitespace-only input returns empty string."""
    # Arrange
    input_text = "   \n   \n   "
    expected = ""
    
    # Act
    result = _clean_code_block(input_text)
    
    # Assert
    assert result == expected, f"Expected empty string, got: {repr(result)}"
    print("✓ Test passed: Whitespace-only input handling")


def test_clean_code_block_with_decorator():
    """Test detection of code starting with Python decorator."""
    # Arrange
    input_text = """This is the implementation:

@app.route('/api')
def handler():
    return {'status': 'ok'}"""
    expected = "@app.route('/api')\ndef handler():\n    return {'status': 'ok'}"
    
    # Act
    result = _clean_code_block(input_text)
    
    # Assert
    assert result == expected, f"Expected:\n{expected}\n\nGot:\n{result}"
    print("✓ Test passed: Decorator detection")


def test_clean_code_block_case_insensitive_fence():
    """Test case-insensitive matching of language specifiers."""
    # Arrange
    input_text = """```PYTHON
def test():
    return True
```"""
    expected = 'def test():\n    return True'
    
    # Act
    result = _clean_code_block(input_text)
    
    # Assert
    assert result == expected, f"Expected:\n{expected}\n\nGot:\n{result}"
    print("✓ Test passed: Case-insensitive fence matching")


def test_clean_code_block_nested_quotes():
    """Test handling of nested string quotes in code."""
    # Arrange
    input_text = """```python
def greet():
    return "He said, 'Hello!'"
```"""
    expected = 'def greet():\n    return "He said, \'Hello!\'"'
    
    # Act
    result = _clean_code_block(input_text)
    
    # Assert
    assert result == expected, f"Expected:\n{expected}\n\nGot:\n{result}"
    print("✓ Test passed: Nested quotes handling")


def test_clean_code_block_multiline_docstring():
    """Test handling of multiline docstrings."""
    # Arrange
    input_text = '''```python
def func():
    """
    This is a docstring.
    It spans multiple lines.
    """
    pass
```'''
    expected = '''def func():
    """
    This is a docstring.
    It spans multiple lines.
    """
    pass'''
    
    # Act
    result = _clean_code_block(input_text)
    
    # Assert
    assert result == expected, f"Expected:\n{expected}\n\nGot:\n{result}"
    print("✓ Test passed: Multiline docstring handling")


def test_clean_code_block_no_false_positives():
    """Test that legitimate variable names starting with preamble words are preserved."""
    # Arrange - Variable names that might trigger false positives
    input_text = """here_config = {'key': 'value'}
this_module_name = 'example'
the_following_items = [1, 2, 3]

def process():
    return here_config"""
    expected = """here_config = {'key': 'value'}
this_module_name = 'example'
the_following_items = [1, 2, 3]

def process():
    return here_config"""
    
    # Act
    result = _clean_code_block(input_text)
    
    # Assert
    assert result == expected, f"Expected:\n{expected}\n\nGot:\n{result}"
    print("✓ Test passed: No false positives on variable names")


if __name__ == "__main__":
    print("=" * 70)
    print("Running comprehensive _clean_code_block test suite")
    print("=" * 70)
    print()
    
    # Basic functionality tests
    print("Basic Functionality Tests:")
    print("-" * 70)
    test_clean_code_block_with_python_fence()
    test_clean_code_block_with_py_fence()
    test_clean_code_block_generic_fence()
    test_clean_code_block_plain_code()
    
    print()
    print("Conversational Text Handling:")
    print("-" * 70)
    test_clean_code_block_with_preamble_and_postamble()
    test_clean_code_block_no_fences_with_preamble()
    
    print()
    print("Edge Cases:")
    print("-" * 70)
    test_clean_code_block_multiple_fences_returns_largest()
    test_clean_code_block_empty_input()
    test_clean_code_block_whitespace_only()
    test_clean_code_block_with_decorator()
    test_clean_code_block_case_insensitive_fence()
    
    print()
    print("Complex Code Patterns:")
    print("-" * 70)
    test_clean_code_block_nested_quotes()
    test_clean_code_block_multiline_docstring()
    test_clean_code_block_no_false_positives()
    
    print()
    print("=" * 70)
    print("✅ All 14 tests passed successfully!")
    print("=" * 70)
    print()
    print("Test Coverage Summary:")
    print("  - Markdown fence extraction: ✓")
    print("  - Conversational text removal: ✓")
    print("  - Multiple code blocks handling: ✓")
    print("  - Edge cases (empty, whitespace): ✓")
    print("  - Complex Python patterns: ✓")
    print("  - False positive prevention: ✓")

