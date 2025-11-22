# test_explanation_prompt_config.py

import pytest
import json
from pathlib import Path


class TestExplanationPromptConfig:
    """Test the explanation_prompt.json configuration file."""

    @pytest.fixture
    def template_data(self):
        """Load the actual explanation_prompt.json file."""
        template_path = (
            Path(__file__).parent.parent / "templates" / "explanation_prompt.json"
        )
        with open(template_path, "r") as f:
            return json.load(f)

    def test_all_templates_present(self, template_data):
        """Test all expected templates are present."""
        expected_templates = [
            "new_fact",
            "updated_fact",
            "unchanged_fact",
            "conflict_resolution",
            "batch_summary",
            "security_alert",
            "data_quality_assessment",
            "compliance_check",
            "performance_analysis",
            "audit_trail",
        ]

        for template in expected_templates:
            assert template in template_data, f"Missing template: {template}"

    def test_template_structure(self, template_data):
        """Test each template has required fields."""
        for template_name, config in template_data.items():
            assert "template" in config, f"{template_name} missing template"
            assert "description" in config, f"{template_name} missing description"
            assert "variables" in config, f"{template_name} missing variables"
            assert "max_tokens" in config, f"{template_name} missing max_tokens"
            assert "temperature" in config, f"{template_name} missing temperature"

            # Validate types
            assert isinstance(config["template"], str)
            assert isinstance(config["variables"], list)
            assert isinstance(config["max_tokens"], int)
            assert isinstance(config["temperature"], (int, float))

    def test_template_variables_in_text(self, template_data):
        """Test that all declared variables appear in template text."""
        for template_name, config in template_data.items():
            template_text = config["template"]
            for variable in config["variables"]:
                assert (
                    f"{{{variable}}}" in template_text
                ), f"Variable {variable} not in {template_name} template"

    def test_temperature_ranges(self, template_data):
        """Test temperature values are in valid range."""
        for template_name, config in template_data.items():
            temp = config["temperature"]
            assert 0 <= temp <= 1, f"{template_name} invalid temperature: {temp}"

            # Security/audit templates should have lower temperature
            if template_name in ["security_alert", "audit_trail", "compliance_check"]:
                assert (
                    temp <= 0.5
                ), f"{template_name} temperature too high for critical template"

    def test_max_tokens_reasonable(self, template_data):
        """Test max_tokens values are reasonable."""
        for template_name, config in template_data.items():
            tokens = config["max_tokens"]
            assert (
                100 <= tokens <= 2000
            ), f"{template_name} unreasonable tokens: {tokens}"

            # Complex templates should have more tokens
            if template_name in ["conflict_resolution", "compliance_check"]:
                assert tokens >= 700, f"{template_name} needs more tokens"

    def test_security_templates(self, template_data):
        """Test security-related templates have proper configuration."""
        security_templates = ["security_alert", "audit_trail", "compliance_check"]

        for template in security_templates:
            config = template_data[template]

            # Should have lower temperature for consistency
            assert config["temperature"] <= 0.5

            # Should include critical variables
            if template == "security_alert":
                assert "risk_level" in config["variables"]
                assert "user_id" in config["variables"]

            if template == "audit_trail":
                assert "merkle_proof" in config["variables"]
                assert "timestamp" in config["variables"]
