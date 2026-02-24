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

    def test_detect_field_regex_kwarg(self):
        """Field(regex=...) should be flagged as a Pydantic v1 compatibility issue."""
        files = {
            "schemas.py": (
                "from pydantic import BaseModel, Field\n\n"
                "class User(BaseModel):\n"
                "    name: str = Field(..., regex='^[a-z]+$')\n"
            )
        }
        errors = self._validate(files)
        assert any("regex=" in err for err in errors), (
            f"Expected error about regex= kwarg, got: {errors}"
        )

    def test_detect_constr_regex_kwarg(self):
        """constr(regex=...) should be flagged as a Pydantic v1 compatibility issue."""
        files = {
            "schemas.py": (
                "from pydantic import BaseModel, constr\n\n"
                "class Item(BaseModel):\n"
                "    code: str = constr(regex='^[A-Z]{3}$')\n"
            )
        }
        errors = self._validate(files)
        assert any("regex=" in err for err in errors), (
            f"Expected error about regex= kwarg in constr(), got: {errors}"
        )


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

    def test_fix_validator_decorator_to_field_validator(self):
        """@validator should be rewritten to @field_validator with mode='before'."""
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
        fixed = self._fix(files)
        content = fixed["schemas.py"]
        assert "@field_validator('name'" in content, f"Expected @field_validator in:\n{content}"
        assert "@validator(" not in content, f"Unexpected @validator in:\n{content}"
        assert "field_validator" in content  # import also updated

    def test_fix_validator_import_updated(self):
        """'validator' in pydantic import should be replaced with 'field_validator'."""
        files = {
            "schemas.py": (
                "from pydantic import BaseModel, validator, Field\n\n"
                "class Item(BaseModel):\n"
                "    price: float\n\n"
                "    @validator('price')\n"
                "    def check_price(cls, v):\n"
                "        return v\n"
            )
        }
        fixed = self._fix(files)
        content = fixed["schemas.py"]
        # Original standalone 'validator' import token should be replaced
        assert "from pydantic import BaseModel, field_validator, Field" in content

    def test_fix_validator_and_validate_roundtrip(self):
        """After fixing @validator, the validator should report no errors."""
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
        fixed = self._fix(files)
        errors = self._validate(fixed)
        assert errors == [], f"Expected no errors after fixing @validator, got: {errors}"

    def test_fix_root_validator_to_model_validator(self):
        """@root_validator should be rewritten to @model_validator(mode='before')."""
        files = {
            "schemas.py": (
                "from pydantic import BaseModel, root_validator\n\n"
                "class Config(BaseModel):\n"
                "    a: int\n"
                "    b: int\n\n"
                "    @root_validator\n"
                "    def check_values(cls, values):\n"
                "        return values\n"
            )
        }
        fixed = self._fix(files)
        content = fixed["schemas.py"]
        assert "@model_validator(mode='before')" in content, f"Expected @model_validator in:\n{content}"
        assert "@root_validator" not in content, f"Unexpected @root_validator in:\n{content}"

    def test_fix_root_validator_with_args(self):
        """@root_validator(pre=True) should also be replaced."""
        files = {
            "schemas.py": (
                "from pydantic import BaseModel, root_validator\n\n"
                "class Config(BaseModel):\n"
                "    x: int\n\n"
                "    @root_validator(pre=True)\n"
                "    def check_x(cls, values):\n"
                "        return values\n"
            )
        }
        fixed = self._fix(files)
        content = fixed["schemas.py"]
        assert "@model_validator(mode='before')" in content
        assert "@root_validator" not in content

    def test_fix_field_regex_to_pattern(self):
        """Field(..., regex='...') should be rewritten to Field(..., pattern='...')."""
        files = {
            "schemas.py": (
                "from pydantic import BaseModel, Field\n\n"
                "class User(BaseModel):\n"
                "    name: str = Field(..., regex='^[a-z]+$')\n"
            )
        }
        fixed = self._fix(files)
        content = fixed["schemas.py"]
        assert "pattern='^[a-z]+$'" in content, f"Expected pattern= in:\n{content}"
        assert "regex='^[a-z]+$'" not in content, f"Unexpected regex= in:\n{content}"

    def test_fix_constr_regex_to_pattern(self):
        """constr(regex='...') should be rewritten to constr(pattern='...')."""
        files = {
            "schemas.py": (
                "from pydantic import BaseModel, constr\n\n"
                "class Item(BaseModel):\n"
                "    code: str = constr(regex='^[A-Z]{3}$')\n"
            )
        }
        fixed = self._fix(files)
        content = fixed["schemas.py"]
        assert "pattern='^[A-Z]{3}$'" in content, f"Expected pattern= in:\n{content}"
        assert "regex='^[A-Z]{3}$'" not in content, f"Unexpected regex= in:\n{content}"

    def test_fix_regex_and_validate_roundtrip(self):
        """After fixing regex= to pattern=, the validator should report no errors."""
        files = {
            "schemas.py": (
                "from pydantic import BaseModel, Field\n\n"
                "class User(BaseModel):\n"
                "    name: str = Field(..., regex='^[a-z]+$')\n"
            )
        }
        fixed = self._fix(files)
        errors = self._validate(fixed)
        assert errors == [], f"Expected no errors after fixing regex=, got: {errors}"

    def test_fix_class_config_to_model_config(self):
        """'class Config:' inside a BaseModel subclass should be replaced with model_config = ConfigDict(...)."""
        files = {
            "schemas.py": (
                "from pydantic import BaseModel\n\n"
                "class User(BaseModel):\n"
                "    name: str\n\n"
                "    class Config:\n"
                "        extra = 'forbid'\n"
            )
        }
        fixed = self._fix(files)
        content = fixed["schemas.py"]
        assert "class Config:" not in content, f"Expected class Config: to be removed:\n{content}"
        assert "model_config = ConfigDict(" in content, f"Expected model_config = ConfigDict(...):\n{content}"
        assert "ConfigDict" in content
        assert "extra='forbid'" in content

    def test_fix_class_config_orm_mode_renamed(self):
        """orm_mode = True should be renamed to from_attributes = True in ConfigDict."""
        files = {
            "schemas.py": (
                "from pydantic import BaseModel\n\n"
                "class Item(BaseModel):\n"
                "    id: int\n\n"
                "    class Config:\n"
                "        orm_mode = True\n"
            )
        }
        fixed = self._fix(files)
        content = fixed["schemas.py"]
        assert "class Config:" not in content
        assert "from_attributes=True" in content, f"Expected from_attributes=True:\n{content}"
        assert "orm_mode" not in content, f"Unexpected orm_mode in:\n{content}"

    def test_fix_class_config_schema_extra_renamed(self):
        """schema_extra should be renamed to json_schema_extra in ConfigDict."""
        files = {
            "schemas.py": (
                "from pydantic import BaseModel\n\n"
                "class Item(BaseModel):\n"
                "    name: str\n\n"
                "    class Config:\n"
                "        schema_extra = {'example': {'name': 'foo'}}\n"
            )
        }
        fixed = self._fix(files)
        content = fixed["schemas.py"]
        assert "class Config:" not in content
        assert "json_schema_extra=" in content, f"Expected json_schema_extra=:\n{content}"
        # Ensure the old standalone 'schema_extra =' (without json_ prefix) is gone
        assert "schema_extra =" not in content, f"Unexpected bare schema_extra= in:\n{content}"

    def test_fix_class_config_adds_configdict_import(self):
        """After fixing class Config:, 'ConfigDict' should be added to pydantic imports."""
        files = {
            "schemas.py": (
                "from pydantic import BaseModel\n\n"
                "class User(BaseModel):\n"
                "    name: str\n\n"
                "    class Config:\n"
                "        extra = 'forbid'\n"
            )
        }
        fixed = self._fix(files)
        content = fixed["schemas.py"]
        assert "ConfigDict" in content
        # Should have ConfigDict in the pydantic import
        assert "from pydantic import" in content

    def test_fix_class_config_and_validate_roundtrip(self):
        """After fixing class Config:, the validator should report no errors."""
        files = {
            "schemas.py": (
                "from pydantic import BaseModel\n\n"
                "class User(BaseModel):\n"
                "    name: str\n\n"
                "    class Config:\n"
                "        extra = 'forbid'\n"
                "        orm_mode = True\n"
            )
        }
        fixed = self._fix(files)
        errors = self._validate(fixed)
        assert errors == [], f"Expected no errors after fixing class Config:, got: {errors}"

    def test_fix_class_config_multiple_models(self):
        """Multiple models with class Config: should all be fixed."""
        files = {
            "schemas.py": (
                "from pydantic import BaseModel\n\n"
                "class User(BaseModel):\n"
                "    name: str\n\n"
                "    class Config:\n"
                "        extra = 'forbid'\n\n"
                "class Item(BaseModel):\n"
                "    id: int\n\n"
                "    class Config:\n"
                "        orm_mode = True\n"
            )
        }
        fixed = self._fix(files)
        content = fixed["schemas.py"]
        assert "class Config:" not in content, f"Expected all class Config: to be removed:\n{content}"
        assert content.count("model_config = ConfigDict(") == 2, (
            f"Expected 2 model_config = ConfigDict(...), got:\n{content}"
        )



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
        assert "class Settings(BaseSettings):" in parsed["config.py"]
        assert "requirements.txt" in parsed
        assert "pydantic-settings>=2.0.0" in parsed["requirements.txt"]

    def test_parse_auto_fixes_deprecated_validator_decorator(self):
        """@validator decorators should be auto-fixed to @field_validator (not blocked)."""
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
        # The file should be auto-fixed, not blocked with an error
        assert "error.txt" not in parsed, (
            f"Expected auto-fix, not error.txt. Got: {parsed.get('error.txt', '')}"
        )
        assert "schemas.py" in parsed
        assert "@field_validator" in parsed["schemas.py"]
        assert "@validator(" not in parsed["schemas.py"]
