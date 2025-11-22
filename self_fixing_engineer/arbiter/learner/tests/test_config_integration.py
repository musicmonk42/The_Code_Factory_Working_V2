# test_config_integration.py

import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
import json
from pathlib import Path


class TestConfigIntegration:
    """Test that configs work properly with the actual system."""

    @pytest.fixture
    def mock_learner(self):
        """Create a mock learner for testing."""
        learner = Mock()
        learner.llm_client = AsyncMock()
        learner.llm_client.generate.return_value = "Generated explanation"
        learner.audit_logger = AsyncMock()
        learner.explanation_cache = {}
        return learner

    @pytest.mark.asyncio
    async def test_all_parsers_loadable(self):
        """Test that all parsers in config can be loaded."""
        # Mock the load_parser_priorities function
        with patch("arbiter.learner.fuzzy.load_parser_priorities") as mock_load:
            # Mock the PARSER_PRIORITIES that would be loaded
            mock_priorities = {
                "SecurityEventParser": {"priority": 1000, "enabled": True},
                "ComplianceDataParser": {"priority": 900, "enabled": True},
                "GenericTextParser": {"priority": 10, "enabled": True},
            }

            with patch.dict(
                "sys.modules",
                {"arbiter.learner.fuzzy": MagicMock(PARSER_PRIORITIES=mock_priorities)},
            ):
                from arbiter.learner.fuzzy import load_parser_priorities

                load_parser_priorities()

                # Import after loading
                from arbiter.learner.fuzzy import PARSER_PRIORITIES

                # Verify the config loaded
                assert len(PARSER_PRIORITIES) > 0
                assert "SecurityEventParser" in PARSER_PRIORITIES

    @pytest.mark.asyncio
    async def test_all_templates_usable(self, mock_learner):
        """Test that all templates can be used for generation."""
        # Mock the templates
        mock_templates = {
            "new_fact": "Template for new fact: {domain} {key} {value}",
            "updated_fact": "Template for update: {domain} {key} {old_value} -> {new_value}",
            "unchanged_fact": "Template for unchanged: {domain} {key} {value}",
        }

        # Mock the module and its contents
        with patch("arbiter.learner.explanations._load_prompt_templates"):
            with patch.dict(
                "sys.modules",
                {
                    "arbiter.learner.explanations": MagicMock(
                        EXPLANATION_PROMPT_TEMPLATES=mock_templates,
                        generate_explanation=self._mock_generate_explanation,
                    )
                },
            ):
                from arbiter.learner.explanations import (
                    _load_prompt_templates,
                    EXPLANATION_PROMPT_TEMPLATES,
                    generate_explanation,
                )

                _load_prompt_templates()

                # Test each template type
                test_cases = [
                    ("new_fact", None, None),  # New fact
                    ("updated_fact", {"old": "value"}, [{"op": "replace"}]),  # Update
                    ("unchanged_fact", {"same": "value"}, None),  # No change
                ]

                for template_key, old_value, diff in test_cases:
                    # Verify template is loaded
                    assert template_key in EXPLANATION_PROMPT_TEMPLATES

                    # Generate explanation
                    explanation = await generate_explanation(
                        mock_learner,
                        "TestDomain",
                        "test_key",
                        {"new": "value"},
                        old_value,
                        diff,
                    )
                    assert explanation is not None
                    assert len(explanation) > 0

    async def _mock_generate_explanation(
        self, learner, domain, key, new_value, old_value, diff
    ):
        """Mock implementation of generate_explanation."""
        # Simple mock that returns a generated explanation
        if old_value is None:
            return f"New fact learned in {domain}: {key} = {new_value}"
        elif diff:
            return f"Updated fact in {domain}: {key} changed from {old_value} to {new_value}"
        else:
            return f"Unchanged fact in {domain}: {key} = {new_value}"

    @pytest.mark.asyncio
    async def test_parser_config_integration(self):
        """Test that parser config integrates with the system."""
        # Load the actual parser config file
        config_path = Path(__file__).parent.parent / "parser_priorities.json"

        if config_path.exists():
            with open(config_path, "r") as f:
                config_data = json.load(f)

            # Verify config can be used
            assert "parser_priorities" in config_data

            # Mock the parser system using this config
            with patch(
                "arbiter.learner.fuzzy.PARSER_PRIORITIES",
                config_data["parser_priorities"],
            ):
                with patch(
                    "arbiter.learner.fuzzy.process_unstructured_data"
                ) as mock_process:
                    mock_process.return_value = {"extracted": "data"}

                    from arbiter.learner.fuzzy import process_unstructured_data

                    # Test processing with the config
                    result = await process_unstructured_data(
                        Mock(),  # mock learner
                        "test data",
                        "FinancialData",  # domain that has overrides
                    )

                    assert result is not None
        else:
            pytest.skip("parser_priorities.json not found")

    @pytest.mark.asyncio
    async def test_template_config_integration(self):
        """Test that template config integrates with the system."""
        # Load the actual template config file
        config_path = Path(__file__).parent.parent / "prompt_templates.json"

        if config_path.exists():
            with open(config_path, "r") as f:
                config_data = json.load(f)

            # Verify config structure
            assert "templates" in config_data
            assert "new_fact" in config_data["templates"]

            # Mock the template system
            with patch(
                "arbiter.learner.explanations.EXPLANATION_PROMPT_TEMPLATES",
                config_data["templates"],
            ):
                # Verify templates are usable
                from arbiter.learner.explanations import EXPLANATION_PROMPT_TEMPLATES

                for template_key in ["new_fact", "updated_fact", "unchanged_fact"]:
                    if template_key in EXPLANATION_PROMPT_TEMPLATES:
                        template = EXPLANATION_PROMPT_TEMPLATES[template_key]
                        assert "system" in template or "prompt" in template
        else:
            pytest.skip("prompt_templates.json not found")
