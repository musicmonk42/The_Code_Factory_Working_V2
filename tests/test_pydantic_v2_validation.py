# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Tests for Pydantic v2 compatibility validation and auto-fix utilities.

Covers:
- validate_pydantic_v2_compatibility: detects deprecated Pydantic v1 patterns
- auto_fix_pydantic_v1_imports: automatically repairs v1 → v2 migration issues
"""

import pytest


# ---------------------------------------------------------------------------
# validate_pydantic_v2_compatibility
# ---------------------------------------------------------------------------


class TestValidatePydanticV2Compatibility:
    """Tests for the Pydantic v2 compatibility validator."""

    def _validate(self, files):
        from generator.agents.codegen_agent.codegen_response_handler import (
            validate_pydantic_v2_compatibility,
        )
        return validate_pydantic_v2_compatibility(files)

    def test_detect_pydantic_v1_basesettings_import(self):
        """Deprecated 'from pydantic import BaseSettings' should be flagged."""
        files = {
            "config.py": (
                "from pydantic import BaseSettings\n\n"
                "class Settings(BaseSettings):\n"
                "    app_env: str\n"
            ),
            "requirements.txt": "pydantic>=2.0.0\npydantic-settings>=2.0.0\n",
        }
        errors = self._validate(files)
        # Must flag the deprecated import
        assert any("BaseSettings" in err for err in errors), (
            f"Expected error about BaseSettings import, got: {errors}"
        )
        deprecated_errors = [e for e in errors if "from pydantic import BaseSettings" in e]
        assert len(deprecated_errors) >= 1

    def test_detect_missing_pydantic_settings_dependency(self):
        """Using BaseSettings without pydantic-settings in requirements.txt should be flagged."""
        files = {
            "config.py": (
                "from pydantic_settings import BaseSettings\n\n"
                "class Settings(BaseSettings):\n"
                "    app_env: str\n"
            ),
            "requirements.txt": "pydantic>=2.0.0\n",  # pydantic-settings missing
        }
        errors = self._validate(files)
        assert any("pydantic-settings" in err for err in errors), (
            f"Expected error about missing pydantic-settings dependency, got: {errors}"
        )

    def test_no_false_positives_for_correct_v2_code(self):
        """Correct Pydantic v2 code should produce zero validation errors."""
        files = {
            "config.py": (
                "from pydantic_settings import BaseSettings\n"
                "from pydantic import Field\n\n"
                "class Settings(BaseSettings):\n"
                "    model_config = {'env_file': '.env'}\n\n"
                "    app_env: str = Field(default='development')\n"
                "    log_level: str = Field(default='info')\n"
            ),
            "requirements.txt": "pydantic>=2.0.0\npydantic-settings>=2.0.0\n",
        }
        errors = self._validate(files)
        assert errors == [], f"Expected no errors for correct v2 code, got: {errors}"

    def test_non_python_files_are_skipped(self):
        """Non-.py files should not be inspected for Pydantic issues."""
        files = {
            "README.md": "from pydantic import BaseSettings\n",
            "requirements.txt": "pydantic>=2.0.0\n",
        }
        errors = self._validate(files)
        assert errors == [], f"Non-Python files should not trigger errors, got: {errors}"

    def test_empty_files_dict(self):
        """Empty file dict should return no errors."""
        assert self._validate({}) == []

    def test_python_file_without_pydantic_is_clean(self):
        """Python file that doesn't use Pydantic at all should have no errors."""
        files = {
            "utils.py": "def add(a, b):\n    return a + b\n",
            "requirements.txt": "requests>=2.0.0\n",
        }
        errors = self._validate(files)
        assert errors == []

    def test_detect_deprecated_validator_decorators(self):
        """Deprecated @validator / @root_validator should be flagged."""
        files = {
            "schemas.py": (
                "from pydantic import BaseModel, validator\n\n"
                "class User(BaseModel):\n"
                "    name: str\n\n"
                "    @validator('name')\n"
                "    def validate_name(cls, v):\n"
                "        return v\n"
            )
        }
        errors = self._validate(files)
        assert any("validator decorators" in err for err in errors)

    def test_detect_deprecated_class_config(self):
        """Deprecated class Config in BaseModel should be flagged."""
        files = {
            "schemas.py": (
                "from pydantic import BaseModel\n\n"
                "class User(BaseModel):\n"
                "    name: str\n\n"
                "    class Config:\n"
                "        extra = 'forbid'\n"
            )
        }
        errors = self._validate(files)
        assert any("class Config" in err for err in errors)


# ---------------------------------------------------------------------------
# auto_fix_pydantic_v1_imports
# ---------------------------------------------------------------------------


class TestAutoFixPydanticV1Imports:
    """Tests for the auto-fix function that migrates Pydantic v1 code to v2."""

    def _fix(self, files):
        from generator.agents.codegen_agent.codegen_response_handler import (
            auto_fix_pydantic_v1_imports,
        )
        return auto_fix_pydantic_v1_imports(files)

    def _validate(self, files):
        from generator.agents.codegen_agent.codegen_response_handler import (
            validate_pydantic_v2_compatibility,
        )
        return validate_pydantic_v2_compatibility(files)

    def test_fix_basesettings_import(self):
        """'from pydantic import BaseSettings' should be rewritten to pydantic_settings."""
        files = {
            "config.py": "from pydantic import BaseSettings\n",
            "requirements.txt": "pydantic>=2.0.0\n",
        }
        fixed = self._fix(files)
        assert "from pydantic_settings import BaseSettings" in fixed["config.py"]
        assert "from pydantic import BaseSettings" not in fixed["config.py"]

    def test_adds_pydantic_settings_to_requirements(self):
        """pydantic-settings should be added to requirements.txt when BaseSettings is used."""
        files = {
            "config.py": "from pydantic_settings import BaseSettings\n",
            "requirements.txt": "pydantic>=2.0.0\n",
        }
        fixed = self._fix(files)
        assert "pydantic-settings" in fixed["requirements.txt"]

    def test_does_not_duplicate_pydantic_settings(self):
        """pydantic-settings should not be added twice if already present."""
        files = {
            "config.py": "from pydantic_settings import BaseSettings\n",
            "requirements.txt": "pydantic>=2.0.0\npydantic-settings>=2.0.0\n",
        }
        fixed = self._fix(files)
        count = fixed["requirements.txt"].count("pydantic-settings")
        assert count == 1, f"Expected exactly one occurrence, found {count}"

    def test_does_not_add_pydantic_settings_when_not_needed(self):
        """requirements.txt should not be modified if BaseSettings is not used."""
        files = {
            "models.py": "from pydantic import BaseModel\n\nclass User(BaseModel):\n    name: str\n",
            "requirements.txt": "pydantic>=2.0.0\n",
        }
        fixed = self._fix(files)
        assert "pydantic-settings" not in fixed["requirements.txt"]

    def test_creates_requirements_when_missing_for_basesettings(self):
        """requirements.txt should be created when BaseSettings is used and requirements is absent."""
        files = {
            "config.py": "from pydantic_settings import BaseSettings\n\nclass Settings(BaseSettings):\n    env: str = 'dev'\n",
        }
        fixed = self._fix(files)
        assert "requirements.txt" in fixed
        assert "pydantic-settings>=2.0.0" in fixed["requirements.txt"]

    def test_fix_and_validate_roundtrip(self):
        """After auto-fix, re-running the validator should return no errors."""
        files = {
            "config.py": "from pydantic import BaseSettings\n\nclass Settings(BaseSettings):\n    app_env: str\n",
            "requirements.txt": "pydantic>=2.0.0\n",
        }
        fixed = self._fix(files)
        errors = self._validate(fixed)
        assert errors == [], f"Expected no errors after auto-fix, got: {errors}"

    def test_original_files_not_mutated(self):
        """The input dict must not be modified in place."""
        original_content = "from pydantic import BaseSettings\n"
        files = {"config.py": original_content, "requirements.txt": "pydantic>=2.0.0\n"}
        _ = self._fix(files)
        assert files["config.py"] == original_content, "Input dict should not be mutated"


class TestParseLLMResponsePydanticV2Validation:
    """Integration tests: pydantic validation runs before files are materialized."""

    def _parse(self, response, lang="python"):
        from generator.agents.codegen_agent.codegen_response_handler import parse_llm_response
        return parse_llm_response(response, lang)

    def test_parse_auto_fixes_and_adds_requirements(self):
        files_payload = {
            "files": {
                "config.py": (
                    "from pydantic import BaseSettings\n\n"
                    "class Settings(BaseSettings):\n"
                    "    app_env: str = 'dev'\n"
                )
            }
        }
        parsed = self._parse(files_payload)
        assert "config.py" in parsed
        assert "from pydantic_settings import BaseSettings" in parsed["config.py"]
        assert "requirements.txt" in parsed
        assert "pydantic-settings>=2.0.0" in parsed["requirements.txt"]

    def test_parse_blocks_deprecated_v1_patterns_with_error_file(self):
        files_payload = {
            "files": {
                "schemas.py": (
                    "from pydantic import BaseModel, validator\n\n"
                    "class User(BaseModel):\n"
                    "    name: str\n\n"
                    "    @validator('name')\n"
                    "    def validate_name(cls, v):\n"
                    "        return v\n"
                )
            }
        }
        parsed = self._parse(files_payload)
        assert list(parsed.keys()) == ["error.txt"]
        assert "Pydantic v2 compatibility validation failed" in parsed["error.txt"]
