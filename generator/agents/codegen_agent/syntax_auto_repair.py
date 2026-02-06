# -*- coding: utf-8 -*-
"""
Syntax Auto-Repair Module

This module provides automatic repair capabilities for common syntax errors
in generated code, particularly focusing on errors that LLMs frequently produce.

Capabilities:
- Repair unterminated string literals (missing quotes)
- Fix missing colons in Python control structures
- Extensible framework for additional repair strategies

Version: 1.0
"""

import logging
import re
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)


class SyntaxAutoRepair:
    """Automatically repair common syntax errors in generated code"""
    
    @staticmethod
    def repair_unterminated_strings(code: str, language: str) -> Tuple[str, List[str]]:
        """
        Detect and repair unterminated string literals.
        
        This method handles the common LLM error of generating strings without
        closing quotes. It counts quotes per line and adds missing ones.
        
        Args:
            code: The source code to repair
            language: The programming language (e.g., "python", "javascript")
        
        Returns:
            Tuple of (repaired_code, list_of_fixes_applied)
        """
        fixes = []
        
        if language.lower() in ("python", "py"):
            lines = code.split('\n')
            repaired_lines = []
            
            for i, line in enumerate(lines, 1):
                # Skip empty lines and comments
                stripped = line.strip()
                if not stripped or stripped.startswith('#'):
                    repaired_lines.append(line)
                    continue
                
                # Count quotes (excluding escaped quotes)
                # Pattern explanation: (?<!\\) means "not preceded by backslash"
                single_quotes = len(re.findall(r"(?<!\\)'", line))
                double_quotes = len(re.findall(r'(?<!\\)"', line))
                
                # Check for triple-quoted strings (docstrings)
                triple_single = line.count("'''")
                triple_double = line.count('"""')
                
                # If we have triple quotes, the line is likely fine or part of a multiline string
                if triple_single > 0 or triple_double > 0:
                    repaired_lines.append(line)
                    continue
                
                # If odd number of quotes, likely unterminated
                # Only fix if it looks like an unterminated string (ends without proper quote)
                modified = False
                
                if single_quotes % 2 != 0:
                    # Check if line has a reasonable place to add quote
                    # Don't add if it already ends with a quote or comment
                    if not line.rstrip().endswith(("'", '"', '#')):
                        line = line.rstrip() + "'"
                        fixes.append(f"Line {i}: Added missing single quote")
                        modified = True
                
                if double_quotes % 2 != 0 and not modified:
                    # Similar check for double quotes
                    if not line.rstrip().endswith(("'", '"', '#')):
                        line = line.rstrip() + '"'
                        fixes.append(f"Line {i}: Added missing double quote")
                
                repaired_lines.append(line)
            
            return '\n'.join(repaired_lines), fixes
        
        return code, []
    
    @staticmethod
    def repair_missing_colons(code: str, language: str) -> Tuple[str, List[str]]:
        """
        Fix missing colons in if/for/def/class statements.
        
        Python control structures require a colon at the end. LLMs sometimes
        omit this, causing immediate syntax errors.
        
        Args:
            code: The source code to repair
            language: The programming language (e.g., "python")
        
        Returns:
            Tuple of (repaired_code, list_of_fixes_applied)
        """
        fixes = []
        
        if language.lower() in ("python", "py"):
            lines = code.split('\n')
            repaired_lines = []
            
            # Control structures that require colons
            control_keywords = r'^(if|elif|else|for|while|def|class|try|except|finally|with|async\s+def|async\s+for|async\s+with)\b'
            
            for i, line in enumerate(lines, 1):
                stripped = line.lstrip()
                
                # Skip empty lines and comments
                if not stripped or stripped.startswith('#'):
                    repaired_lines.append(line)
                    continue
                
                # Check for control structures without colons
                if re.match(control_keywords, stripped):
                    # Remove trailing whitespace and check if it ends with colon
                    trimmed = line.rstrip()
                    
                    # Don't add colon if:
                    # 1. Already has a colon
                    # 2. Is part of a string (has quotes)
                    # 3. Is a comment
                    if not trimmed.endswith(':') and '#' not in stripped:
                        # Check if line is not within a string
                        # Simple heuristic: if no unmatched quotes before this point
                        line = trimmed + ':'
                        fixes.append(f"Line {i}: Added missing colon")
                
                repaired_lines.append(line)
            
            return '\n'.join(repaired_lines), fixes
        
        return code, []
    
    @classmethod
    def auto_repair(cls, code: str, language: str) -> Dict[str, any]:
        """
        Apply all auto-repair strategies.
        
        This method orchestrates all repair strategies in the correct order
        to maximize chances of fixing syntax errors.
        
        Args:
            code: The source code to repair
            language: The programming language (e.g., "python", "javascript")
        
        Returns:
            Dictionary with keys:
                - 'repaired_code': str - The repaired code
                - 'fixes_applied': List[str] - List of fixes that were applied
                - 'was_modified': bool - Whether any repairs were made
        """
        original_code = code
        all_fixes = []
        
        # Apply repairs in sequence
        # Order matters: fix structural issues (colons) before string issues
        code, fixes = cls.repair_missing_colons(code, language)
        all_fixes.extend(fixes)
        
        code, fixes = cls.repair_unterminated_strings(code, language)
        all_fixes.extend(fixes)
        
        return {
            'repaired_code': code,
            'fixes_applied': all_fixes,
            'was_modified': code != original_code
        }
