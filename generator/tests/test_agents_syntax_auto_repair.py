# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# -*- coding: utf-8 -*-
"""
Production-Grade Test Suite for Syntax Auto-Repair

This test suite validates the syntax auto-repair functionality with
comprehensive coverage of edge cases, error conditions, and integration points.

Test Categories:
- Unit tests for individual repair strategies
- Integration tests with validation flow
- Edge case and error handling tests
- Performance and regression tests
"""

import pytest

from generator.agents.codegen_agent.syntax_auto_repair import (
    SyntaxAutoRepair,
    SyntaxAutoRepairError,
    auto_repair_code,
)


class TestRepairUnterminatedStrings:
    """Test suite for unterminated string repair functionality."""
    
    def test_repair_single_quote(self):
        """Should add missing single quote at end of line."""
        code = "print('hello"
        repaired, fixes = SyntaxAutoRepair.repair_unterminated_strings(code, "python")
        
        assert repaired.rstrip().endswith("'"), "Should end with single quote"
        assert len(fixes) == 1, "Should have exactly one fix"
        assert "single quote" in fixes[0].lower()
    
    def test_repair_double_quote(self):
        """Should add missing double quote at end of line."""
        code = 'print("hello'
        repaired, fixes = SyntaxAutoRepair.repair_unterminated_strings(code, "python")
        
        assert repaired.rstrip().endswith('"'), "Should end with double quote"
        assert len(fixes) == 1, "Should have exactly one fix"
        assert "double quote" in fixes[0].lower()
    
    def test_repair_multiline_strings(self):
        """Should handle multiple lines with unterminated strings."""
        code = """x = 'value1
y = "value2
z = 'value3'"""
        repaired, fixes = SyntaxAutoRepair.repair_unterminated_strings(code, "python")
        
        assert len(fixes) == 2, "Should fix two unterminated strings"
        lines = repaired.split('\n')
        assert lines[0].rstrip().endswith("'"), "First line should end with quote"
        assert lines[1].rstrip().endswith('"'), "Second line should end with quote"
    
    def test_no_repair_needed(self):
        """Should not modify code with properly terminated strings."""
        code = 'print("hello world")'
        repaired, fixes = SyntaxAutoRepair.repair_unterminated_strings(code, "python")
        
        assert repaired == code, "Should not modify valid code"
        assert len(fixes) == 0, "Should have no fixes"
    
    def test_skip_triple_quoted_strings(self):
        """Should not modify triple-quoted strings (docstrings)."""
        code = '"""This is a docstring'
        repaired, fixes = SyntaxAutoRepair.repair_unterminated_strings(code, "python")
        
        assert repaired == code, "Should not modify triple-quoted strings"
        assert len(fixes) == 0, "Should have no fixes"
    
    def test_skip_comments(self):
        """Should not modify comment lines."""
        code = "# This is a comment with 'quotes"
        repaired, fixes = SyntaxAutoRepair.repair_unterminated_strings(code, "python")
        
        assert repaired == code, "Should not modify comments"
        assert len(fixes) == 0, "Should have no fixes"
    
    def test_skip_empty_lines(self):
        """Should not modify empty lines."""
        code = "\n\n   \n"
        repaired, fixes = SyntaxAutoRepair.repair_unterminated_strings(code, "python")
        
        assert repaired == code, "Should not modify empty lines"
        assert len(fixes) == 0, "Should have no fixes"
    
    def test_dont_add_quote_to_lines_ending_with_quote(self):
        """Should not add quote if line already ends with one."""
        code = "x = 'value'"
        repaired, fixes = SyntaxAutoRepair.repair_unterminated_strings(code, "python")
        
        assert repaired == code, "Should not modify"
        assert len(fixes) == 0, "Should have no fixes"
    
    def test_unsupported_language(self):
        """Should return unchanged code for unsupported languages."""
        code = 'console.log("test'
        repaired, fixes = SyntaxAutoRepair.repair_unterminated_strings(code, "java")
        
        assert repaired == code, "Should not modify unsupported language"
        assert len(fixes) == 0, "Should have no fixes"
    
    def test_invalid_input_type(self):
        """Should handle invalid input gracefully."""
        repaired, fixes = SyntaxAutoRepair.repair_unterminated_strings(None, "python")
        
        assert fixes == [], "Should return empty fixes"
    
    def test_escaped_quotes(self):
        """Should not count escaped quotes."""
        code = r"print('He said \'hello\'')"
        repaired, fixes = SyntaxAutoRepair.repair_unterminated_strings(code, "python")
        
        assert repaired == code, "Should not modify escaped quotes"
        assert len(fixes) == 0, "Should have no fixes"


class TestRepairLineContinuations:
    """Test suite for line continuation character repair functionality."""

    def test_repair_single_line_continuation(self):
        """Should remove stray backslash at line ending."""
        code = "print('hello')\\\nprint('world')"
        repaired, fixes = SyntaxAutoRepair.repair_line_continuations(code, "python")

        assert repaired == "print('hello')\nprint('world')", "Should remove backslash"
        assert len(fixes) == 1, "Should have exactly one fix"
        assert "line continuation" in fixes[0].lower()

    def test_repair_windows_line_endings(self):
        """Should handle Windows-style line endings."""
        code = "x = 1\\\r\ny = 2"
        repaired, fixes = SyntaxAutoRepair.repair_line_continuations(code, "python")

        assert "\\" not in repaired, "Should remove backslash"
        assert len(fixes) == 1, "Should have exactly one fix"

    def test_repair_multiple_continuations(self):
        """Should fix multiple line continuation characters."""
        code = "x = 1\\\ny = 2\\\nz = 3\n"
        repaired, fixes = SyntaxAutoRepair.repair_line_continuations(code, "python")

        assert len(fixes) == 2, "Should fix two line continuations"
        assert "\\" not in repaired, "Should remove all backslashes"

    def test_no_repair_needed(self):
        """Should not modify code without line continuations."""
        code = "print('hello')\nprint('world')"
        repaired, fixes = SyntaxAutoRepair.repair_line_continuations(code, "python")

        assert repaired == code, "Should not modify valid code"
        assert len(fixes) == 0, "Should have no fixes"

    def test_preserve_intentional_continuations(self):
        """Should not remove intentional line continuations in strings."""
        code = "text = 'line1\\\nline2'"
        repaired, fixes = SyntaxAutoRepair.repair_line_continuations(code, "python")

        # Inside a string, quotes are unbalanced, so it should be preserved
        # This is a conservative heuristic
        assert len(fixes) <= 1, "Should be conservative with string contexts"

    def test_skip_empty_lines(self):
        """Should not modify empty lines."""
        code = "\n\n   \n"
        repaired, fixes = SyntaxAutoRepair.repair_line_continuations(code, "python")

        assert repaired == code, "Should not modify empty lines"
        assert len(fixes) == 0, "Should have no fixes"

    def test_unsupported_language(self):
        """Should return unchanged code for unsupported languages."""
        code = "console.log('test')\\\nconsole.log('test2')"
        repaired, fixes = SyntaxAutoRepair.repair_line_continuations(code, "java")

        assert repaired == code, "Should not modify unsupported language"
        assert len(fixes) == 0, "Should have no fixes"

    def test_invalid_input_type(self):
        """Should handle invalid input gracefully."""
        repaired, fixes = SyntaxAutoRepair.repair_line_continuations(None, "python")

        assert fixes == [], "Should return empty fixes"


class TestRepairMissingColons:
    """Test suite for missing colon repair functionality."""
    
    def test_repair_def_statement(self):
        """Should add missing colon to function definition."""
        code = "def hello()\n    pass"
        repaired, fixes = SyntaxAutoRepair.repair_missing_colons(code, "python")
        
        assert "def hello():" in repaired, "Should add colon after def"
        assert len(fixes) == 1, "Should have exactly one fix"
        assert "colon" in fixes[0].lower()
    
    def test_repair_if_statement(self):
        """Should add missing colon to if statement."""
        code = "if x > 0\n    pass"
        repaired, fixes = SyntaxAutoRepair.repair_missing_colons(code, "python")
        
        assert "if x > 0:" in repaired, "Should add colon after if"
        assert len(fixes) == 1, "Should have exactly one fix"
    
    def test_repair_for_loop(self):
        """Should add missing colon to for loop."""
        code = "for i in range(10)\n    print(i)"
        repaired, fixes = SyntaxAutoRepair.repair_missing_colons(code, "python")
        
        assert "for i in range(10):" in repaired, "Should add colon after for"
        assert len(fixes) == 1, "Should have exactly one fix"
    
    def test_repair_class_definition(self):
        """Should add missing colon to class definition."""
        code = "class MyClass\n    pass"
        repaired, fixes = SyntaxAutoRepair.repair_missing_colons(code, "python")
        
        assert "class MyClass:" in repaired, "Should add colon after class"
        assert len(fixes) == 1, "Should have exactly one fix"
    
    def test_repair_async_def(self):
        """Should add missing colon to async function."""
        code = "async def fetch_data()\n    pass"
        repaired, fixes = SyntaxAutoRepair.repair_missing_colons(code, "python")
        
        assert "async def fetch_data():" in repaired, "Should add colon after async def"
        assert len(fixes) == 1, "Should have exactly one fix"
    
    def test_repair_try_except(self):
        """Should add missing colons to try-except blocks."""
        code = "try\n    pass\nexcept\n    pass"
        repaired, fixes = SyntaxAutoRepair.repair_missing_colons(code, "python")
        
        assert "try:" in repaired, "Should add colon after try"
        assert "except:" in repaired, "Should add colon after except"
        assert len(fixes) == 2, "Should have two fixes"
    
    def test_no_repair_needed(self):
        """Should not modify code with proper colons."""
        code = "def hello():\n    pass"
        repaired, fixes = SyntaxAutoRepair.repair_missing_colons(code, "python")
        
        assert repaired == code, "Should not modify valid code"
        assert len(fixes) == 0, "Should have no fixes"
    
    def test_skip_comments(self):
        """Should not add colons to comments."""
        code = "# def this_is_a_comment"
        repaired, fixes = SyntaxAutoRepair.repair_missing_colons(code, "python")
        
        assert repaired == code, "Should not modify comments"
        assert len(fixes) == 0, "Should have no fixes"
    
    def test_multiple_statements(self):
        """Should fix multiple missing colons."""
        code = """def func1()
    pass

def func2()
    pass

if True
    pass"""
        repaired, fixes = SyntaxAutoRepair.repair_missing_colons(code, "python")
        
        assert len(fixes) == 3, "Should fix three missing colons"
        assert "def func1():" in repaired
        assert "def func2():" in repaired
        assert "if True:" in repaired
    
    def test_unsupported_language(self):
        """Should return unchanged code for unsupported languages."""
        code = "function test() { }"
        repaired, fixes = SyntaxAutoRepair.repair_missing_colons(code, "javascript")
        
        assert repaired == code, "Should not modify unsupported language"
        assert len(fixes) == 0, "Should have no fixes"


class TestAutoRepair:
    """Test suite for the combined auto-repair orchestration."""

    def test_repair_line_continuation_and_colon(self):
        """Should fix line continuation before adding colon."""
        code = "def test()\\\n    pass"
        result = SyntaxAutoRepair.auto_repair(code, "python")

        assert result['was_modified'], "Should be modified"
        # Should remove backslash and add colon
        assert len(result['fixes_applied']) == 2, "Should have two fixes"
        assert ':' in result['repaired_code'], "Should add colon"
        assert '\\' not in result['repaired_code'], "Should remove backslash"

    def test_repair_all_three_issues(self):
        """Should fix line continuation, missing colon, and unterminated string."""
        code = 'def test()\\\n    print("hello'
        result = SyntaxAutoRepair.auto_repair(code, "python")

        assert result['was_modified'], "Should be modified"
        assert len(result['fixes_applied']) >= 2, "Should have multiple fixes"
        assert ':' in result['repaired_code'], "Should add colon"
        # Backslash and quotes should be handled
        assert '\\' not in result['repaired_code'] or result['repaired_code'].count('"') % 2 == 0

    def test_repair_both_issues(self):
        """Should fix both missing colon and unterminated string."""
        code = 'def test()\n    print("hello'
        result = SyntaxAutoRepair.auto_repair(code, "python")
        
        assert result['was_modified'], "Should be modified"
        assert len(result['fixes_applied']) == 2, "Should have two fixes"
        assert ':' in result['repaired_code'], "Should add colon"
        assert result['repaired_code'].count('"') % 2 == 0, "Should balance quotes"
    
    def test_no_repairs_needed(self):
        """Should return unchanged for valid code."""
        code = 'def test():\n    print("hello")'
        result = SyntaxAutoRepair.auto_repair(code, "python")
        
        assert not result['was_modified'], "Should not be modified"
        assert len(result['fixes_applied']) == 0, "Should have no fixes"
        assert result['repaired_code'] == code, "Code should be unchanged"
    
    def test_complex_code(self):
        """Should handle complex real-world code patterns."""
        code = '''def process_data(items)
    results = []
    for item in items
        if item.is_valid()
            results.append(f"Valid: {item.name}')
    return results'''
        
        result = SyntaxAutoRepair.auto_repair(code, "python")
        
        assert result['was_modified'], "Should be modified"
        assert len(result['fixes_applied']) >= 3, "Should have multiple fixes"
        assert 'def process_data(items):' in result['repaired_code']
    
    def test_empty_code(self):
        """Should handle empty code gracefully."""
        result = SyntaxAutoRepair.auto_repair("", "python")
        
        assert not result['was_modified'], "Should not be modified"
        assert result['repaired_code'] == "", "Should return empty string"
    
    def test_whitespace_only(self):
        """Should handle whitespace-only code."""
        code = "   \n  \n   "
        result = SyntaxAutoRepair.auto_repair(code, "python")
        
        assert not result['was_modified'], "Should not be modified"
        assert result['repaired_code'] == code, "Should preserve whitespace"
    
    def test_invalid_input_type(self):
        """Should handle invalid input types gracefully."""
        result = SyntaxAutoRepair.auto_repair(None, "python")
        
        assert not result['was_modified'], "Should not be modified"
        assert result['fixes_applied'] == [], "Should have no fixes"
    
    def test_convenience_function(self):
        """Should work through convenience function."""
        code = 'def test()\n    pass'
        result = auto_repair_code(code, "python")
        
        assert result['was_modified'], "Should be modified"
        assert len(result['fixes_applied']) == 1, "Should have one fix"


class TestEdgeCases:
    """Test suite for edge cases and error conditions."""
    
    def test_very_long_line(self):
        """Should handle very long lines without performance issues."""
        # Create a line with 10,000 characters
        long_line = 'x = "' + 'a' * 10000
        result = SyntaxAutoRepair.auto_repair(long_line, "python")
        
        assert result['was_modified'], "Should be modified"
        assert '"' in result['repaired_code'][-5:], "Should add closing quote"
    
    def test_many_lines(self):
        """Should handle files with many lines."""
        # Create 1000 lines of code
        lines = [f'x{i} = "value{i}"' for i in range(1000)]
        code = '\n'.join(lines)
        result = SyntaxAutoRepair.auto_repair(code, "python")
        
        assert not result['was_modified'], "Should not modify valid code"
    
    def test_mixed_indentation(self):
        """Should preserve indentation while repairing."""
        code = """def outer()
    def inner()
        pass"""
        result = SyntaxAutoRepair.auto_repair(code, "python")
        
        assert '    def inner():' in result['repaired_code'], "Should preserve indentation"
    
    def test_unicode_characters(self):
        """Should handle unicode characters in strings."""
        code = 'message = "Hello 世界'
        result = SyntaxAutoRepair.auto_repair(code, "python")
        
        assert result['was_modified'], "Should be modified"
        assert '世界' in result['repaired_code'], "Should preserve unicode"
    
    def test_multiple_quotes_per_line(self):
        """Should handle lines with multiple quote pairs."""
        code = 'x = "a" + "b" + "c"'
        result = SyntaxAutoRepair.auto_repair(code, "python")
        
        assert not result['was_modified'], "Should not modify balanced quotes"


class TestRepairMissingCommas:
    """Test suite for missing comma repair functionality."""
    
    def test_repair_adjacent_strings(self):
        """Should add comma between adjacent string literals."""
        code = 'items = ["hello" "world"]'
        repaired, fixes = SyntaxAutoRepair.repair_missing_commas(code, "python")
        
        assert '"hello", "world"' in repaired, "Should add comma between strings"
        assert len(fixes) > 0, "Should report fix"
        assert "Adjacent string literals" in fixes[0]
    
    def test_repair_identifier_followed_by_string(self):
        """Should add comma between identifier and string."""
        code = 'func(name "value")'
        repaired, fixes = SyntaxAutoRepair.repair_missing_commas(code, "python")
        
        assert 'name, "value"' in repaired, "Should add comma between identifier and string"
        assert len(fixes) > 0, "Should report fix"
    
    def test_repair_number_followed_by_identifier(self):
        """Should add comma between number and identifier in collections."""
        code = '[1 two, 3 four]'
        repaired, fixes = SyntaxAutoRepair.repair_missing_commas(code, "python")
        
        assert '1, two' in repaired, "Should add comma between number and identifier"
        assert '3, four' in repaired, "Should add comma between number and identifier"
        assert len(fixes) > 0, "Should report fix"
    
    def test_repair_adjacent_brackets(self):
        """Should add comma between closing and opening brackets."""
        code = '[(1, 2) (3, 4)]'
        repaired, fixes = SyntaxAutoRepair.repair_missing_commas(code, "python")
        
        assert ') (' not in repaired or ', ' in repaired, "Should add comma between bracket pairs"
        assert len(fixes) > 0, "Should report fix"
    
    def test_no_repair_needed(self):
        """Should not modify code with proper commas."""
        code = 'func(a, b, c)'
        repaired, fixes = SyntaxAutoRepair.repair_missing_commas(code, "python")
        
        assert repaired == code, "Should not modify valid code"
        assert len(fixes) == 0, "Should have no fixes"
    
    def test_no_repair_for_operators(self):
        """Should not add commas where operators are expected."""
        code = 'x = 1 + 2'
        repaired, fixes = SyntaxAutoRepair.repair_missing_commas(code, "python")
        
        # Should not break valid expressions with operators
        assert '1 + 2' in repaired or '1+2' in repaired, "Should preserve operators"
    
    def test_unsupported_language(self):
        """Should return unchanged code for unsupported languages."""
        code = 'const x = [1 2 3];'
        repaired, fixes = SyntaxAutoRepair.repair_missing_commas(code, "javascript")
        
        assert repaired == code, "Should not modify non-Python code"
        assert len(fixes) == 0, "Should have no fixes"
    
    def test_multiple_missing_commas(self):
        """Should repair multiple missing commas in one pass."""
        code = 'items = ["a" "b" "c"]'
        repaired, fixes = SyntaxAutoRepair.repair_missing_commas(code, "python")
        
        # Should have added commas
        assert fixes, "Should report fixes"
        # Check that result is better than original
        assert repaired.count(',') > code.count(','), "Should add commas"


class TestIntegrationWithValidation:
    """Test integration with the validation flow."""
    
    def test_repaired_code_is_valid_python(self):
        """Repaired code should pass Python syntax validation."""
        code = 'def test()\n    pass'
        result = SyntaxAutoRepair.auto_repair(code, "python")
        
        # Try to compile the repaired code
        try:
            compile(result['repaired_code'], '<test>', 'exec')
            assert True, "Repaired code should compile"
        except SyntaxError:
            pytest.fail("Repaired code should be valid Python")
    
    def test_complex_repair_produces_valid_code(self):
        """Complex repairs should produce valid Python."""
        code = '''def process()
    for i in range(10)
        if i % 2 == 0
            print(f"Even: {i}")'''
        
        result = SyntaxAutoRepair.auto_repair(code, "python")
        
        # Should compile without errors
        try:
            compile(result['repaired_code'], '<test>', 'exec')
            assert True, "Complex repaired code should compile"
        except SyntaxError:
            pytest.fail(f"Repaired code should be valid. Got: {result['repaired_code']}")


if __name__ == "__main__":
    # Allow running tests directly
    pytest.main([__file__, "-v"])
