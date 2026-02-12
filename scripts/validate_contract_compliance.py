#!/usr/bin/env python3
"""
Contract Compliance Validation Script

This script validates that the code generation pipeline produces output
that complies with the contract specified in New_Test_README.md.

Usage:
    python test_contract_compliance.py <output_directory>

Example:
    python test_contract_compliance.py ./uploads/job-123/generated/hello_generator
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Tuple


class ContractValidator:
    """Validates generated code against contract requirements."""
    
    def __init__(self, output_dir: Path, language: str = "python"):
        self.output_dir = output_dir
        self.language = language.lower()
        self.errors: List[str] = []
        self.warnings: List[str] = []
    
    def validate_all(self) -> bool:
        """Run all validation checks. Returns True if all checks pass."""
        print(f"🔍 Validating contract compliance for: {self.output_dir}\n")
        
        checks = [
            ("Output Directory Structure", self.check_output_structure),
            ("Schema Validation", self.check_schema_validation),
            ("README Completeness", self.check_readme_completeness),
            ("Sphinx Documentation", self.check_sphinx_docs),
            ("Reports Location and Content", self.check_reports),
            ("No Bogus Fallback Tests", self.check_no_fallback_tests),
        ]
        
        for name, check_func in checks:
            print(f"📋 {name}...")
            try:
                check_func()
                print(f"   ✅ PASS\n")
            except AssertionError as e:
                self.errors.append(f"{name}: {e}")
                print(f"   ❌ FAIL: {e}\n")
            except Exception as e:
                self.warnings.append(f"{name}: Unexpected error - {e}")
                print(f"   ⚠️  WARNING: {e}\n")
        
        self.print_summary()
        return len(self.errors) == 0
    
    def check_output_structure(self):
        """Issue #1: Verify output directory structure."""
        if not self.output_dir.exists():
            raise AssertionError(f"Output directory does not exist: {self.output_dir}")
        
        # Check for required directories
        required_dirs = ["app", "tests", "reports"]
        for dir_name in required_dirs:
            dir_path = self.output_dir / dir_name
            if not dir_path.exists():
                raise AssertionError(f"Required directory missing: {dir_name}/")
        
        # Check for required files
        required_files = [
            "app/main.py",
            "app/routes.py",
            "app/schemas.py",
            "requirements.txt",
            "README.md",
        ]
        for file_path in required_files:
            full_path = self.output_dir / file_path
            if not full_path.exists():
                raise AssertionError(f"Required file missing: {file_path}")
        
        # Check for double-nesting (should NOT exist)
        parent = self.output_dir.parent
        if parent.name == "generated" and (parent.parent / "generated").exists():
            raise AssertionError(
                f"Double-nesting detected: {parent.parent}/generated/generated/"
            )
    
    def check_schema_validation(self):
        """Issue #2: Verify Pydantic validators are used, not manual validation."""
        schemas_path = self.output_dir / "app" / "schemas.py"
        if not schemas_path.exists():
            raise AssertionError("app/schemas.py not found")
        
        schema_content = schemas_path.read_text()
        
        # Check for @validator decorator usage
        if "@validator" not in schema_content:
            raise AssertionError(
                "app/schemas.py should use @validator decorators for validation"
            )
        
        # Check routes.py for absence of manual validation
        routes_path = self.output_dir / "app" / "routes.py"
        if routes_path.exists():
            routes_content = routes_path.read_text()
            
            # Check for common manual validation patterns (should NOT exist)
            bad_patterns = [
                ".strip()",  # Manual trimming in routes
                "if not message",  # Manual validation
                "len(message)",  # Manual length check in routes
            ]
            
            found_manual_validation = False
            for pattern in bad_patterns:
                if pattern in routes_content and "def " in routes_content:
                    # Only flag if it's in a route handler function
                    lines = routes_content.split('\n')
                    for i, line in enumerate(lines):
                        if pattern in line and any(
                            'async def' in lines[j] or 'def' in lines[j]
                            for j in range(max(0, i-5), i)
                        ):
                            found_manual_validation = True
                            break
            
            if found_manual_validation:
                self.warnings.append(
                    "app/routes.py may contain manual validation - "
                    "should use Pydantic validators in schemas.py"
                )
    
    def check_readme_completeness(self):
        """Issue #3: Verify README has all required sections."""
        readme_path = self.output_dir / "README.md"
        if not readme_path.exists():
            raise AssertionError("README.md not found")
        
        readme_content = readme_path.read_text()
        
        # Language-specific required sections
        if self.language in ("typescript", "javascript"):
            required_sections = [
                ("## Setup", "Setup section with installation instructions"),
                ("## Run", "Run section with npm run command"),
                ("## Test", "Test section with npm test or jest command"),
                ("## API Endpoints", "API Endpoints section"),
                ("## Project Structure", "Project Structure section"),
                ("curl", "curl examples for testing endpoints"),
            ]
        elif self.language == "go":
            required_sections = [
                ("## Setup", "Setup section with installation instructions"),
                ("## Run", "Run section with go run command"),
                ("## Test", "Test section with go test command"),
                ("## API Endpoints", "API Endpoints section"),
                ("## Project Structure", "Project Structure section"),
                ("curl", "curl examples for testing endpoints"),
            ]
        elif self.language == "java":
            required_sections = [
                ("## Setup", "Setup section with installation instructions"),
                ("## Run", "Run section with java -jar or mvn/gradle run command"),
                ("## Test", "Test section with mvn test or gradle test command"),
                ("## API Endpoints", "API Endpoints section"),
                ("## Project Structure", "Project Structure section"),
                ("curl", "curl examples for testing endpoints"),
            ]
        else:
            # Python (default)
            required_sections = [
                ("## Setup", "Setup section with installation instructions"),
                ("## Run", "Run section with uvicorn command"),
                ("## Test", "Test section with pytest command"),
                ("## API Endpoints", "API Endpoints section"),
                ("## Project Structure", "Project Structure section"),
                ("curl", "curl examples for testing endpoints"),
            ]
        
        for section, description in required_sections:
            if section not in readme_content:
                raise AssertionError(f"README.md missing required section: {description}")
        
        # Check for placeholder content (should NOT exist)
        bad_phrases = [
            "Configuration options are not required",
            "Environment variables and configuration options",
        ]
        for phrase in bad_phrases:
            if phrase in readme_content:
                raise AssertionError(
                    f"README.md contains placeholder text: '{phrase}'"
                )
    
    def check_sphinx_docs(self):
        """Issue #4: Verify Sphinx documentation exists."""
        docs_dir = self.output_dir / "docs"
        
        # Check if docs directory exists
        if not docs_dir.exists():
            raise AssertionError("docs/ directory not found")
        
        # Check for Sphinx build output
        build_dir = docs_dir / "_build" / "html"
        if not build_dir.exists():
            raise AssertionError("docs/_build/html/ directory not found")
        
        # Check for index.html
        index_html = build_dir / "index.html"
        if not index_html.exists():
            raise AssertionError("docs/_build/html/index.html not found")
        
        # Verify it has content
        if index_html.stat().st_size < 100:
            raise AssertionError("docs/_build/html/index.html is too small (likely empty)")
    
    def check_reports(self):
        """Issue #5: Verify reports are in correct location with valid content."""
        reports_dir = self.output_dir / "reports"
        
        if not reports_dir.exists():
            raise AssertionError("reports/ directory not found")
        
        # Check provenance.json
        provenance_path = reports_dir / "provenance.json"
        if not provenance_path.exists():
            raise AssertionError("reports/provenance.json not found")
        
        try:
            with open(provenance_path) as f:
                provenance = json.load(f)
            
            # Validate provenance structure
            required_fields = ["job_id", "timestamp", "stages"]
            for field in required_fields:
                if field not in provenance:
                    raise AssertionError(
                        f"provenance.json missing required field: {field}"
                    )
        except json.JSONDecodeError as e:
            raise AssertionError(f"provenance.json is not valid JSON: {e}")
        
        # Check critique_report.json
        critique_path = reports_dir / "critique_report.json"
        if not critique_path.exists():
            raise AssertionError("reports/critique_report.json not found")
        
        try:
            with open(critique_path) as f:
                critique = json.load(f)
            
            # Validate critique report structure
            required_fields = [
                "job_id",
                "timestamp",
                "coverage",
                "test_results",
                "issues",
                "fixes_applied",
            ]
            for field in required_fields:
                if field not in critique:
                    raise AssertionError(
                        f"critique_report.json missing required field: {field}"
                    )
            
            # Validate coverage structure
            if "coverage" in critique:
                coverage = critique["coverage"]
                if not isinstance(coverage, dict):
                    raise AssertionError("coverage field should be a dict")
                
                coverage_fields = ["total_lines", "covered_lines", "percentage"]
                for field in coverage_fields:
                    if field not in coverage:
                        self.warnings.append(
                            f"coverage missing field: {field}"
                        )
            
            # Validate test_results structure
            if "test_results" in critique:
                test_results = critique["test_results"]
                if not isinstance(test_results, dict):
                    raise AssertionError("test_results field should be a dict")
                
                test_fields = ["total", "passed", "failed"]
                for field in test_fields:
                    if field not in test_results:
                        self.warnings.append(
                            f"test_results missing field: {field}"
                        )
        
        except json.JSONDecodeError as e:
            raise AssertionError(f"critique_report.json is not valid JSON: {e}")
    
    def check_no_fallback_tests(self):
        """Issue #6: Verify no bogus fallback tests exist."""
        tests_dir = self.output_dir / "tests"
        
        if not tests_dir.exists():
            # Tests directory should exist
            raise AssertionError("tests/ directory not found")
        
        # Search for fallback test markers
        test_files = list(tests_dir.glob("*.py"))
        
        if not test_files:
            raise AssertionError("No test files found in tests/")
        
        for test_file in test_files:
            content = test_file.read_text()
            
            # Check for fallback test markers
            bad_markers = [
                "AUTO-GENERATED FALLBACK TESTS",
                "Syntax error detected in",
                "fallback test for file with syntax errors",
            ]
            
            for marker in bad_markers:
                if marker in content:
                    raise AssertionError(
                        f"{test_file.name} contains bogus fallback test marker: '{marker}'"
                    )
        
        # Check for tests in wrong location (top-level tests/ instead of service tests/)
        top_level_tests = Path(self.output_dir.parent.parent.parent) / "tests"
        if top_level_tests.exists():
            # Check if it contains auto-generated fallback tests
            for test_file in top_level_tests.glob("*.py"):
                content = test_file.read_text()
                if "AUTO-GENERATED FALLBACK" in content:
                    raise AssertionError(
                        f"Bogus fallback test found in wrong location: {test_file}"
                    )
    
    def print_summary(self):
        """Print validation summary."""
        print("\n" + "=" * 70)
        print("VALIDATION SUMMARY")
        print("=" * 70)
        
        if self.errors:
            print(f"\n❌ FAILED ({len(self.errors)} errors)")
            for error in self.errors:
                print(f"   • {error}")
        else:
            print("\n✅ ALL CHECKS PASSED")
        
        if self.warnings:
            print(f"\n⚠️  WARNINGS ({len(self.warnings)} warnings)")
            for warning in self.warnings:
                print(f"   • {warning}")
        
        print("\n" + "=" * 70)


def main():
    parser = argparse.ArgumentParser(
        description="Validate code generation contract compliance"
    )
    parser.add_argument(
        "output_dir",
        type=Path,
        help="Path to the generated output directory (e.g., ./uploads/job-123/generated/hello_generator)",
    )
    parser.add_argument(
        "--language",
        type=str,
        default="python",
        help="Programming language of the generated code (default: python)",
    )
    
    args = parser.parse_args()
    
    if not args.output_dir.exists():
        print(f"❌ Error: Output directory does not exist: {args.output_dir}")
        sys.exit(1)
    
    validator = ContractValidator(args.output_dir, language=args.language)
    success = validator.validate_all()
    
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
