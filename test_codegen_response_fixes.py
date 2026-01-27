"""
Test the enhanced _clean_code_block function directly without full imports.
"""
import re


def _clean_code_block(code_content: str) -> str:
    """
    Removes markdown-style code fences and any conversational preamble/postamble.
    
    Handles patterns like:
        Here's the code:
        ```python
        code
        ```
        Let me know if you need changes.
    """
    text = code_content.strip()
    
    # Find ALL code blocks and extract the largest one (likely the main code)
    # Pattern: ```language\ncode\n```
    code_block_pattern = r'```(?:python|py)?\s*\n(.*?)```'
    matches = re.findall(code_block_pattern, text, flags=re.DOTALL | re.IGNORECASE)
    
    if matches:
        # Return the largest code block (most likely to be the actual code)
        return max(matches, key=len).strip()
    
    # Try without language specifier
    generic_pattern = r'```\s*\n(.*?)```'
    matches = re.findall(generic_pattern, text, flags=re.DOTALL)
    if matches:
        return max(matches, key=len).strip()
    
    # No code fences found - try to strip common preamble patterns
    # Remove lines that look like conversational text before actual code
    lines = text.split('\n')
    code_start_idx = 0
    
    for i, line in enumerate(lines):
        stripped = line.strip().lower()
        # Skip lines that look like conversational preamble
        if stripped.startswith(('here', 'this', 'the following', 'below', 'i ', "i'")):
            code_start_idx = i + 1
            continue
        # Stop at first line that looks like code
        if stripped.startswith(('import ', 'from ', 'def ', 'class ', '#', '"""', "'''")):
            break
        if '=' in stripped and not stripped.startswith(('here', 'this')):
            break
    
    return '\n'.join(lines[code_start_idx:]).strip()


def test_clean_code_block_with_markdown_fences():
    """Test cleaning code with markdown fences."""
    input_text = """```python
def hello():
    print("Hello, World!")
```"""
    
    result = _clean_code_block(input_text)
    expected = 'def hello():\n    print("Hello, World!")'
    assert result == expected, f"Expected:\n{expected}\n\nGot:\n{result}"
    print("✓ Test passed: clean_code_block with markdown fences")


def test_clean_code_block_with_preamble():
    """Test cleaning code with conversational preamble."""
    input_text = """Here's the code you requested:

```python
def add(a, b):
    return a + b
```

Let me know if you need any changes!"""
    
    result = _clean_code_block(input_text)
    expected = 'def add(a, b):\n    return a + b'
    assert result == expected, f"Expected:\n{expected}\n\nGot:\n{result}"
    print("✓ Test passed: clean_code_block with preamble and postamble")


def test_clean_code_block_multiple_fences():
    """Test cleaning code with multiple code blocks (should return largest)."""
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
    
    result = _clean_code_block(input_text)
    expected = 'def calculate(x, y):\n    result = x + y\n    return result * 2'
    assert result == expected, f"Expected:\n{expected}\n\nGot:\n{result}"
    print("✓ Test passed: clean_code_block with multiple fences (returns largest)")


def test_clean_code_block_no_fences_with_preamble():
    """Test cleaning code without fences but with conversational preamble."""
    input_text = """Here is the solution:

import os

def main():
    print("Hello")"""
    
    result = _clean_code_block(input_text)
    expected = 'import os\n\ndef main():\n    print("Hello")'
    assert result == expected, f"Expected:\n{expected}\n\nGot:\n{result}"
    print("✓ Test passed: clean_code_block without fences but with preamble")


def test_clean_code_block_plain_code():
    """Test cleaning plain code without any fences or preamble."""
    input_text = """import sys

def greet(name):
    return f"Hello, {name}!" """
    
    result = _clean_code_block(input_text)
    expected = 'import sys\n\ndef greet(name):\n    return f"Hello, {name}!"'
    assert result == expected, f"Expected:\n{expected}\n\nGot:\n{result}"
    print("✓ Test passed: clean_code_block with plain code")


def test_clean_code_block_generic_fence():
    """Test cleaning code with generic fence (no language specifier)."""
    input_text = """```
def test():
    pass
```"""
    
    result = _clean_code_block(input_text)
    expected = 'def test():\n    pass'
    assert result == expected, f"Expected:\n{expected}\n\nGot:\n{result}"
    print("✓ Test passed: clean_code_block with generic fence")


if __name__ == "__main__":
    print("Running _clean_code_block tests...\n")
    
    test_clean_code_block_with_markdown_fences()
    test_clean_code_block_with_preamble()
    test_clean_code_block_multiple_fences()
    test_clean_code_block_no_fences_with_preamble()
    test_clean_code_block_plain_code()
    test_clean_code_block_generic_fence()
    
    print("\n✅ All tests passed!")
