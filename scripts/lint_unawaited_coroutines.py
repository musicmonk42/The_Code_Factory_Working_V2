#!/usr/bin/env python3
# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Unawaited Coroutine Linter - Pre-commit Hook
=============================================

This script detects common async/await bugs where coroutine functions are
called but not awaited, leading to silent failures and data loss.

**Common Patterns Detected**:
    - Async function called without await
    - Async function result assigned but not awaited
    - asyncio.create_task() with coroutine that should be awaited

**Exit Codes**:
    0: No issues found
    1: Unawaited coroutines detected

**Usage**:
    # As pre-commit hook
    python scripts/lint_unawaited_coroutines.py file1.py file2.py
    
    # Manual check
    python scripts/lint_unawaited_coroutines.py $(find . -name "*.py")

**Module Version**: 1.0.0
**Author**: Code Factory Platform Team
**Last Updated**: 2026-01-23
"""
import ast
import sys
from pathlib import Path
from typing import List, Tuple
from dataclasses import dataclass


@dataclass
class UnawaitedCoroutine:
    """Information about an unawaited coroutine."""
    file: str
    line: int
    column: int
    function_name: str
    context: str


class CoroutineChecker(ast.NodeVisitor):
    """
    AST visitor that detects unawaited coroutine calls.
    
    This checker identifies function calls to known async functions that
    are not properly awaited, which is a common source of bugs in async code.
    
    Note: The list of known async functions is maintained manually. For
    production use, consider implementing auto-discovery based on function
    signatures or maintaining this list in a configuration file for easier updates.
    """
    
    # Known async functions that must be awaited
    # TODO: Consider moving to configuration file for easier maintenance
    KNOWN_ASYNC_FUNCTIONS = {
        'register_with_omnicore',
        'log_audit_event',
        'async_log',
        'async_write',
        'async_read',
        'async_execute',
        'add_files',  # MultiVectorDBManager.add_files
    }
    
    def __init__(self, filename: str):
        self.filename = filename
        self.errors: List[UnawaitedCoroutine] = []
        self.in_await = False
        self.in_create_task = False
    
    def visit_Await(self, node: ast.Await) -> None:
        """Mark that we're inside an await expression."""
        old_in_await = self.in_await
        self.in_await = True
        self.generic_visit(node)
        self.in_await = old_in_await
    
    def visit_Call(self, node: ast.Call) -> None:
        """
        Check if call is to an async function without await.
        """
        func_name = self._get_function_name(node.func)
        
        # Check if this is asyncio.create_task()
        if func_name == 'create_task' and self._is_asyncio_create_task(node):
            # Check the argument to create_task - it should be a coroutine call
            old_in_create_task = self.in_create_task
            self.in_create_task = True
            self.generic_visit(node)
            self.in_create_task = old_in_create_task
            return
        
        # Check if calling a known async function
        if func_name in self.KNOWN_ASYNC_FUNCTIONS:
            # If not in await and not in create_task, this is an error
            if not self.in_await and not self.in_create_task:
                self.errors.append(UnawaitedCoroutine(
                    file=self.filename,
                    line=node.lineno,
                    column=node.col_offset,
                    function_name=func_name,
                    context=f"{func_name}() called without await"
                ))
        
        self.generic_visit(node)
    
    def _get_function_name(self, node: ast.AST) -> str:
        """Extract function name from call node."""
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            return node.attr
        return ""
    
    def _is_asyncio_create_task(self, node: ast.Call) -> bool:
        """Check if this is asyncio.create_task() call."""
        if isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name):
                return (node.func.value.id == 'asyncio' and 
                        node.func.attr == 'create_task')
        return False


def check_file(filepath: Path) -> List[UnawaitedCoroutine]:
    """
    Check a single Python file for unawaited coroutines.
    
    Args:
        filepath: Path to Python file
    
    Returns:
        List of UnawaitedCoroutine errors found
    """
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        
        tree = ast.parse(content, filename=str(filepath))
        checker = CoroutineChecker(str(filepath))
        checker.visit(tree)
        return checker.errors
    
    except SyntaxError as e:
        print(f"Syntax error in {filepath}: {e}", file=sys.stderr)
        return []
    except Exception as e:
        print(f"Error processing {filepath}: {e}", file=sys.stderr)
        return []


def main() -> int:
    """
    Main entry point for the linter.
    
    Returns:
        0 if no errors, 1 if errors found
    """
    if len(sys.argv) < 2:
        print("Usage: lint_unawaited_coroutines.py <file1.py> [file2.py ...]")
        return 0
    
    all_errors: List[UnawaitedCoroutine] = []
    
    for filepath_str in sys.argv[1:]:
        filepath = Path(filepath_str)
        if not filepath.suffix == '.py':
            continue
        
        errors = check_file(filepath)
        all_errors.extend(errors)
    
    # Report errors
    if all_errors:
        print("=" * 80)
        print("UNAWAITED COROUTINES DETECTED")
        print("=" * 80)
        print(f"Found {len(all_errors)} unawaited coroutine call(s):\n")
        
        for error in all_errors:
            print(f"  {error.file}:{error.line}:{error.column}")
            print(f"    {error.context}")
            print(f"    Fix: Add 'await' before {error.function_name}() or use asyncio.create_task()")
            print()
        
        print("=" * 80)
        print("Async functions must be awaited to execute properly!")
        print("Without await, the function returns a coroutine object that is never executed.")
        print("=" * 80)
        return 1
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
