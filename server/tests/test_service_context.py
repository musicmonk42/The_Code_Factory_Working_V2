"""Tests for ServiceContext and extracted helper modules.

Validates Phase 1 of the omnicore_service.py decomposition:
- ServiceContext dataclass holds correct defaults
- All 16 helper functions importable from new locations
- Validation and detection helpers produce correct results
"""

from __future__ import annotations

import dataclasses
import os
import tempfile
import unittest
from pathlib import Path


class TestServiceContextCreation(unittest.TestCase):
    """Verify ServiceContext dataclass structure and defaults."""

    def _make_ctx(self, **overrides):
        from server.services.service_context import ServiceContext

        return ServiceContext(**overrides)

    def test_context_creation_with_defaults(self):
        """ServiceContext with no args should have safe defaults."""
        ctx = self._make_ctx()

        self.assertIsNone(ctx.llm_config)
        self.assertIsInstance(ctx.agents, dict)
        self.assertEqual(ctx.agents, {})
        self.assertIsNone(ctx.message_bus)
        self.assertIsNone(ctx.omnicore_engine)
        self.assertIsNone(ctx.kafka_producer)
        self.assertIsInstance(ctx.job_output_base, Path)

    def test_context_components_graceful_degradation(self):
        """All OmniCore components should default to False."""
        ctx = self._make_ctx()

        components = ctx.omnicore_components_available
        self.assertIsInstance(components, dict)
        expected_keys = {"message_bus", "plugin_registry", "metrics", "audit"}
        self.assertEqual(set(components.keys()), expected_keys)
        for key, val in components.items():
            self.assertFalse(val, f"Component '{key}' should default to False")

    def test_context_agents_start_empty(self):
        """Agents dict should start empty for lazy loading."""
        ctx = self._make_ctx()
        self.assertEqual(len(ctx.agents), 0)

    def test_context_is_dataclass(self):
        """ServiceContext must be a proper dataclass."""
        from server.services.service_context import ServiceContext

        self.assertTrue(dataclasses.is_dataclass(ServiceContext))
        field_names = [f.name for f in dataclasses.fields(ServiceContext)]
        self.assertIn("llm_config", field_names)
        self.assertIn("agents", field_names)
        self.assertIn("message_bus", field_names)
        self.assertIn("omnicore_components_available", field_names)

    def test_context_accepts_custom_config(self):
        """ServiceContext should accept custom values."""
        config = {"provider": "openai", "model": "gpt-4"}
        ctx = self._make_ctx(llm_config=config)
        self.assertEqual(ctx.llm_config, config)


class TestHelperImports(unittest.TestCase):
    """Verify all extracted helper modules are importable."""

    def test_helpers_validation_importable(self):
        from server.services.helpers.validation import _validate_report_structure
        from server.services.helpers.validation import _validate_helm_chart_structure
        from server.services.helpers.validation import _create_placeholder_critique_report

        self.assertTrue(callable(_validate_report_structure))
        self.assertTrue(callable(_validate_helm_chart_structure))
        self.assertTrue(callable(_create_placeholder_critique_report))

    def test_helpers_project_detection_importable(self):
        from server.services.helpers.project_detection import _detect_project_language
        from server.services.helpers.project_detection import _is_test_file
        from server.services.helpers.project_detection import _is_third_party_import_error

        self.assertTrue(callable(_detect_project_language))
        self.assertTrue(callable(_is_test_file))
        self.assertTrue(callable(_is_third_party_import_error))

    def test_helpers_fallback_generators_importable(self):
        from server.services.helpers.fallback_generators import _generate_fallback_readme
        from server.services.helpers.fallback_generators import _load_readme_from_disk

        self.assertTrue(callable(_generate_fallback_readme))
        self.assertTrue(callable(_load_readme_from_disk))

    def test_helpers_file_utils_importable(self):
        from server.services.helpers.file_utils import _ensure_python_package_structure
        from server.services.helpers.file_utils import _pre_materialization_import_check

        self.assertTrue(callable(_ensure_python_package_structure))
        self.assertTrue(callable(_pre_materialization_import_check))

    def test_helpers_sfe_cache_importable(self):
        from server.services.helpers.sfe_cache import _load_sfe_analysis_report
        from server.services.helpers.sfe_cache import _invalidate_sfe_analysis_cache

        self.assertTrue(callable(_load_sfe_analysis_report))
        self.assertTrue(callable(_invalidate_sfe_analysis_cache))

    def test_helpers_init_reexports(self):
        """The helpers __init__ should re-export all functions."""
        import server.services.helpers as helpers

        self.assertTrue(hasattr(helpers, "_validate_report_structure"))
        self.assertTrue(hasattr(helpers, "_detect_project_language"))
        self.assertTrue(hasattr(helpers, "_load_readme_from_disk"))
        self.assertTrue(hasattr(helpers, "_ensure_python_package_structure"))


class TestValidationHelpers(unittest.TestCase):
    """Test extracted validation functions produce correct results."""

    def test_validate_report_structure_with_all_defects(self):
        from server.services.helpers.validation import _validate_report_structure

        report = {"all_defects": [{"id": 1, "msg": "test"}]}
        result = _validate_report_structure(report)
        self.assertTrue(result)

    def test_validate_report_structure_with_issues(self):
        from server.services.helpers.validation import _validate_report_structure

        report = {"issues": [{"id": 1}]}
        result = _validate_report_structure(report)
        self.assertTrue(result)

    def test_validate_report_structure_invalid(self):
        from server.services.helpers.validation import _validate_report_structure

        result = _validate_report_structure({})
        self.assertFalse(result)

    def test_validate_report_structure_non_dict(self):
        from server.services.helpers.validation import _validate_report_structure

        result = _validate_report_structure("not a dict")
        self.assertFalse(result)

    def test_validate_helm_chart_valid(self):
        from server.services.helpers.validation import _validate_helm_chart_structure

        chart = {"apiVersion": "v2", "name": "my-chart", "version": "1.0.0"}
        result = _validate_helm_chart_structure(chart)
        self.assertTrue(result)

    def test_validate_helm_chart_missing_fields(self):
        from server.services.helpers.validation import _validate_helm_chart_structure

        result = _validate_helm_chart_structure({"name": "only-name"})
        self.assertFalse(result)


class TestProjectDetectionHelpers(unittest.TestCase):
    """Test extracted project detection functions."""

    def test_detect_language_python(self):
        from server.services.helpers.project_detection import _detect_project_language

        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "main.py").write_text("print('hello')")
            (Path(tmpdir) / "utils.py").write_text("pass")
            result = _detect_project_language(tmpdir)
            self.assertEqual(result, "python")

    def test_detect_language_nonexistent_defaults_python(self):
        from server.services.helpers.project_detection import _detect_project_language

        result = _detect_project_language("/nonexistent/path")
        self.assertEqual(result, "python")

    def test_is_test_file_positive(self):
        from server.services.helpers.project_detection import _is_test_file

        self.assertTrue(_is_test_file("test_foo.py"))
        self.assertTrue(_is_test_file("bar_test.py"))

    def test_is_test_file_negative(self):
        from server.services.helpers.project_detection import _is_test_file

        self.assertFalse(_is_test_file("foo.py"))
        self.assertFalse(_is_test_file("main.py"))


class TestAsyncFactory(unittest.TestCase):
    """Test the async ServiceContext factory."""

    def test_create_service_context_returns_context(self):
        import asyncio
        from server.services.service_context import ServiceContext, create_service_context

        async def _run():
            return await create_service_context()

        ctx = asyncio.get_event_loop().run_until_complete(_run())
        self.assertIsInstance(ctx, ServiceContext)
        self.assertIsNone(ctx.llm_config)
        self.assertEqual(ctx.agents, {})


if __name__ == "__main__":
    unittest.main()
