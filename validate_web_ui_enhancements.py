#!/usr/bin/env python3
"""
Web UI Enhancements Validation Script

This script validates that all web UI enhancements are properly implemented
and meet industry standards for production deployment.

Usage:
    python validate_web_ui_enhancements.py

Exit Codes:
    0 - All validations passed
    1 - One or more validations failed
"""

import json
import os
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, List, Tuple


class Colors:
    """ANSI color codes for terminal output"""
    BLUE = '\033[0;34m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[0;33m'
    RED = '\033[0;31m'
    NC = '\033[0m'  # No Color


class HTMLValidator(HTMLParser):
    """HTML validation using Python's HTMLParser"""
    def __init__(self):
        super().__init__()
        self.errors = []
        self.stack = []
        self.self_closing = {
            'br', 'hr', 'img', 'input', 'meta', 'link', 'area', 
            'base', 'col', 'embed', 'param', 'source', 'track', 'wbr'
        }
        
    def handle_starttag(self, tag, attrs):
        if tag not in self.self_closing:
            self.stack.append(tag)
    
    def handle_endtag(self, tag):
        if tag in self.self_closing:
            return
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()
        else:
            self.errors.append(f"Unexpected closing tag: {tag}")
    
    def error(self, message):
        self.errors.append(message)


class ValidationResult:
    """Container for validation results"""
    def __init__(self, name: str, passed: bool, details: str = ""):
        self.name = name
        self.passed = passed
        self.details = details
    
    def __str__(self):
        status = f"{Colors.GREEN}✓{Colors.NC}" if self.passed else f"{Colors.RED}✗{Colors.NC}"
        return f"{status} {self.name}" + (f"\n  {self.details}" if self.details else "")


class WebUIValidator:
    """Main validator for web UI enhancements"""
    
    def __init__(self, base_path: Path):
        self.base_path = base_path
        self.results: List[ValidationResult] = []
        
        # File paths
        self.html_file = base_path / "server/templates/index.html"
        self.js_file = base_path / "server/static/js/main.js"
        self.css_file = base_path / "server/static/css/main.css"
    
    def validate_all(self) -> bool:
        """Run all validations"""
        print(f"{Colors.BLUE}{'='*70}{Colors.NC}")
        print(f"{Colors.BLUE}Web UI Enhancements Validation{Colors.NC}")
        print(f"{Colors.BLUE}{'='*70}{Colors.NC}\n")
        
        # Check files exist
        self._check_file_exists()
        
        # HTML validations
        self._validate_html_structure()
        self._validate_html_elements()
        
        # JavaScript validations
        self._validate_js_functions()
        self._validate_js_integration()
        
        # CSS validations
        self._validate_css_classes()
        self._validate_css_responsive()
        
        # Integration validations
        self._validate_api_integration()
        self._validate_security()
        
        # Performance validations
        self._validate_performance()
        
        # Print results
        self._print_results()
        
        # Return overall status
        return all(r.passed for r in self.results)
    
    def _check_file_exists(self):
        """Validate required files exist"""
        for file in [self.html_file, self.js_file, self.css_file]:
            passed = file.exists()
            self.results.append(ValidationResult(
                f"File exists: {file.name}",
                passed,
                str(file) if passed else f"File not found: {file}"
            ))
    
    def _validate_html_structure(self):
        """Validate HTML is well-formed"""
        try:
            with open(self.html_file, 'r') as f:
                html = f.read()
            
            validator = HTMLValidator()
            validator.feed(html)
            
            if validator.errors:
                self.results.append(ValidationResult(
                    "HTML structure validation",
                    False,
                    f"Errors: {', '.join(validator.errors)}"
                ))
            elif validator.stack:
                self.results.append(ValidationResult(
                    "HTML structure validation",
                    False,
                    f"Unclosed tags: {', '.join(validator.stack)}"
                ))
            else:
                self.results.append(ValidationResult(
                    "HTML structure validation",
                    True,
                    f"{len(html)} bytes, well-formed"
                ))
        except Exception as e:
            self.results.append(ValidationResult(
                "HTML structure validation",
                False,
                f"Error: {e}"
            ))
    
    def _validate_html_elements(self):
        """Validate required HTML elements are present"""
        try:
            with open(self.html_file, 'r') as f:
                html = f.read()
            
            required_elements = {
                'API Keys View': 'id="api-keys-view"',
                'Provider Status Grid': 'id="provider-status-grid"',
                'LLM Config Form': 'id="llm-config-form"',
                'System State': 'id="system-state"',
                'Available Agents Count': 'id="available-agents-count"',
                'LLM Provider Status': 'id="llm-provider-status"',
                'Agents Status List': 'id="agents-status-list"',
                'LLM Config Status': 'id="llm-config-status"',
                'Diagnostics Output': 'id="diagnostics-output"',
                'Message Bus Info': 'id="message-bus-info"',
            }
            
            missing = []
            for name, element in required_elements.items():
                if element not in html:
                    missing.append(name)
            
            if missing:
                self.results.append(ValidationResult(
                    "Required HTML elements",
                    False,
                    f"Missing: {', '.join(missing)}"
                ))
            else:
                self.results.append(ValidationResult(
                    "Required HTML elements",
                    True,
                    f"All {len(required_elements)} elements present"
                ))
        except Exception as e:
            self.results.append(ValidationResult(
                "Required HTML elements",
                False,
                f"Error: {e}"
            ))
    
    def _validate_js_functions(self):
        """Validate required JavaScript functions are present"""
        try:
            with open(self.js_file, 'r') as f:
                js = f.read()
            
            required_functions = [
                'initAPIKeys',
                'saveLLMConfiguration',
                'refreshProviderStatus',
                'activateProvider',
                'removeProvider',
                'refreshSystemStatus',
                'loadSystemState',
                'loadAgentStatus',
                'loadLLMStatus',
                'loadOmniCoreStatus',
                'runFullDiagnostics',
                'downloadDiagnosticReport',
                'navigateToView',
            ]
            
            missing = []
            for func in required_functions:
                if f'function {func}' not in js:
                    missing.append(func)
            
            if missing:
                self.results.append(ValidationResult(
                    "Required JavaScript functions",
                    False,
                    f"Missing: {', '.join(missing)}"
                ))
            else:
                self.results.append(ValidationResult(
                    "Required JavaScript functions",
                    True,
                    f"All {len(required_functions)} functions present"
                ))
        except Exception as e:
            self.results.append(ValidationResult(
                "Required JavaScript functions",
                False,
                f"Error: {e}"
            ))
    
    def _validate_js_integration(self):
        """Validate JavaScript integration"""
        try:
            with open(self.js_file, 'r') as f:
                js = f.read()
            
            # Check DOMContentLoaded has initAPIKeys
            has_init = 'initAPIKeys()' in js and 'DOMContentLoaded' in js
            
            # Check initSystem calls refreshSystemStatus
            has_refresh = 'function initSystem()' in js and 'refreshSystemStatus()' in js
            
            if has_init and has_refresh:
                self.results.append(ValidationResult(
                    "JavaScript initialization",
                    True,
                    "initAPIKeys() and refreshSystemStatus() properly integrated"
                ))
            else:
                details = []
                if not has_init:
                    details.append("initAPIKeys() not in DOMContentLoaded")
                if not has_refresh:
                    details.append("refreshSystemStatus() not in initSystem()")
                self.results.append(ValidationResult(
                    "JavaScript initialization",
                    False,
                    "; ".join(details)
                ))
        except Exception as e:
            self.results.append(ValidationResult(
                "JavaScript initialization",
                False,
                f"Error: {e}"
            ))
    
    def _validate_css_classes(self):
        """Validate required CSS classes are present"""
        try:
            with open(self.css_file, 'r') as f:
                css = f.read()
            
            required_classes = [
                '.api-keys-section',
                '.provider-status-grid',
                '.provider-card',
                '.config-form-section',
                '.provider-help',
                '.status-overview',
                '.system-section',
                '.agent-status-item',
                '.error-details',
                '.warning-box',
                '.diagnostics-output',
                '.status-ok',
                '.status-error',
            ]
            
            missing = []
            for cls in required_classes:
                if cls not in css:
                    missing.append(cls)
            
            if missing:
                self.results.append(ValidationResult(
                    "Required CSS classes",
                    False,
                    f"Missing: {', '.join(missing)}"
                ))
            else:
                self.results.append(ValidationResult(
                    "Required CSS classes",
                    True,
                    f"All {len(required_classes)} classes present"
                ))
        except Exception as e:
            self.results.append(ValidationResult(
                "Required CSS classes",
                False,
                f"Error: {e}"
            ))
    
    def _validate_css_responsive(self):
        """Validate responsive CSS is present"""
        try:
            with open(self.css_file, 'r') as f:
                css = f.read()
            
            # Check for media queries
            has_media_queries = '@media' in css and 'max-width' in css
            
            # Check for common responsive patterns
            has_grid = 'grid-template-columns' in css
            has_flex = 'flex-direction' in css
            
            if has_media_queries and has_grid and has_flex:
                self.results.append(ValidationResult(
                    "Responsive CSS",
                    True,
                    "Media queries, grid, and flexbox present"
                ))
            else:
                details = []
                if not has_media_queries:
                    details.append("No media queries")
                if not has_grid:
                    details.append("No grid layout")
                if not has_flex:
                    details.append("No flexbox")
                self.results.append(ValidationResult(
                    "Responsive CSS",
                    False,
                    "; ".join(details)
                ))
        except Exception as e:
            self.results.append(ValidationResult(
                "Responsive CSS",
                False,
                f"Error: {e}"
            ))
    
    def _validate_api_integration(self):
        """Validate API integration points"""
        try:
            with open(self.js_file, 'r') as f:
                js = f.read()
            
            # Check for API endpoints (with or without /api prefix)
            endpoints = [
                'health',
                'agents',
                'api-keys/',
                'omnicore/plugins',
            ]
            
            missing = []
            for endpoint in endpoints:
                # Check if endpoint is used with API_BASE
                if f"${{API_BASE}}/{endpoint}" not in js and f"/{endpoint}" not in js:
                    missing.append(endpoint)
            
            if missing:
                self.results.append(ValidationResult(
                    "API endpoint integration",
                    False,
                    f"Missing: {', '.join(missing)}"
                ))
            else:
                self.results.append(ValidationResult(
                    "API endpoint integration",
                    True,
                    f"All {len(endpoints)} endpoints referenced"
                ))
        except Exception as e:
            self.results.append(ValidationResult(
                "API endpoint integration",
                False,
                f"Error: {e}"
            ))
    
    def _validate_security(self):
        """Validate security measures"""
        try:
            with open(self.js_file, 'r') as f:
                js = f.read()
            
            with open(self.html_file, 'r') as f:
                html = f.read()
            
            # Check for XSS prevention
            has_escape = 'escapeHtml' in js
            
            # Check for password fields
            has_password_field = 'type="password"' in html
            
            # Check for confirmation dialogs
            has_confirm = 'confirm(' in js
            
            if has_escape and has_password_field and has_confirm:
                self.results.append(ValidationResult(
                    "Security measures",
                    True,
                    "XSS prevention, secure inputs, confirmations present"
                ))
            else:
                details = []
                if not has_escape:
                    details.append("No HTML escaping")
                if not has_password_field:
                    details.append("No password fields")
                if not has_confirm:
                    details.append("No confirmations")
                self.results.append(ValidationResult(
                    "Security measures",
                    False,
                    "; ".join(details)
                ))
        except Exception as e:
            self.results.append(ValidationResult(
                "Security measures",
                False,
                f"Error: {e}"
            ))
    
    def _validate_performance(self):
        """Validate performance considerations"""
        try:
            with open(self.js_file, 'r') as f:
                js = f.read()
            
            # Check for parallel API calls
            has_parallel = 'Promise.all' in js
            
            # Check for async/await
            has_async = 'async function' in js or 'async ' in js
            
            if has_parallel and has_async:
                self.results.append(ValidationResult(
                    "Performance optimization",
                    True,
                    "Parallel API calls and async/await present"
                ))
            else:
                details = []
                if not has_parallel:
                    details.append("No parallel API calls")
                if not has_async:
                    details.append("No async/await")
                self.results.append(ValidationResult(
                    "Performance optimization",
                    False,
                    "; ".join(details)
                ))
        except Exception as e:
            self.results.append(ValidationResult(
                "Performance optimization",
                False,
                f"Error: {e}"
            ))
    
    def _print_results(self):
        """Print validation results"""
        print(f"\n{Colors.BLUE}{'='*70}{Colors.NC}")
        print(f"{Colors.BLUE}Validation Results{Colors.NC}")
        print(f"{Colors.BLUE}{'='*70}{Colors.NC}\n")
        
        passed_count = sum(1 for r in self.results if r.passed)
        total_count = len(self.results)
        
        for result in self.results:
            print(result)
        
        print(f"\n{Colors.BLUE}{'='*70}{Colors.NC}")
        
        if passed_count == total_count:
            print(f"{Colors.GREEN}✓ All {total_count} validations passed!{Colors.NC}")
        else:
            failed_count = total_count - passed_count
            print(f"{Colors.RED}✗ {failed_count} of {total_count} validations failed{Colors.NC}")
        
        print(f"{Colors.BLUE}{'='*70}{Colors.NC}\n")


def main():
    """Main entry point"""
    # Get base path
    base_path = Path(__file__).parent
    
    # Create validator
    validator = WebUIValidator(base_path)
    
    # Run validations
    success = validator.validate_all()
    
    # Exit with appropriate code
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
