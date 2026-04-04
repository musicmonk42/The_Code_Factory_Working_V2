"""Tests for Phase 3 pipeline services extracted from OmniCoreService.

Validates that each new pipeline sub-service module is importable, accepts a
ServiceContext for construction, exposes the expected public methods, and that
PipelineOrchestrator correctly composes its sub-services.

Covered services:
    - CodegenService        (server.services.pipeline.codegen_service)
    - DeployService         (server.services.pipeline.deploy_service)
    - QualityService        (server.services.pipeline.quality_service)
    - PipelineOrchestrator  (server.services.pipeline.pipeline_orchestrator)
"""

import unittest
from server.services.service_context import ServiceContext


def _make_context() -> ServiceContext:
    """Return a minimal ServiceContext suitable for unit tests."""
    return ServiceContext()


# -- Import tests ------------------------------------------------------------

class TestPipelineImports(unittest.TestCase):
    """Each pipeline service module must be importable."""

    def test_codegen_service_importable(self):
        from server.services.pipeline.codegen_service import CodegenService
        self.assertTrue(CodegenService is not None)

    def test_deploy_service_importable(self):
        from server.services.pipeline.deploy_service import DeployService
        self.assertTrue(DeployService is not None)

    def test_quality_service_importable(self):
        from server.services.pipeline.quality_service import QualityService
        self.assertTrue(QualityService is not None)

    def test_pipeline_orchestrator_importable(self):
        from server.services.pipeline.pipeline_orchestrator import PipelineOrchestrator
        self.assertTrue(PipelineOrchestrator is not None)

    def test_pipeline_init_reexports(self):
        """The pipeline __init__ should re-export PipelineOrchestrator."""
        from server.services.pipeline import PipelineOrchestrator
        self.assertTrue(PipelineOrchestrator is not None)


# -- Construction tests ------------------------------------------------------

class TestPipelineConstruction(unittest.TestCase):
    """Each service must accept a ServiceContext and construct without error."""

    def setUp(self):
        self.ctx = _make_context()

    def test_codegen_service_construction(self):
        from server.services.pipeline.codegen_service import CodegenService
        service = CodegenService(self.ctx)
        self.assertIsNotNone(service)

    def test_deploy_service_construction(self):
        from server.services.pipeline.deploy_service import DeployService
        service = DeployService(self.ctx)
        self.assertIsNotNone(service)

    def test_quality_service_construction(self):
        from server.services.pipeline.quality_service import QualityService
        service = QualityService(self.ctx)
        self.assertIsNotNone(service)

    def test_orchestrator_construction(self):
        from server.services.pipeline.pipeline_orchestrator import PipelineOrchestrator
        service = PipelineOrchestrator(self.ctx)
        self.assertIsNotNone(service)


# -- Orchestrator composition test -------------------------------------------

class TestOrchestratorComposition(unittest.TestCase):
    """PipelineOrchestrator must compose its sub-services as attributes."""

    def setUp(self):
        from server.services.pipeline.pipeline_orchestrator import PipelineOrchestrator
        self.orchestrator = PipelineOrchestrator(_make_context())

    def test_orchestrator_has_sub_services(self):
        for attr in ("_codegen", "_deploy", "_quality"):
            with self.subTest(attr=attr):
                self.assertTrue(
                    hasattr(self.orchestrator, attr),
                    f"PipelineOrchestrator missing attribute: {attr}",
                )


# -- Method presence tests ---------------------------------------------------

class TestCodegenServiceMethods(unittest.TestCase):
    """CodegenService must expose the run_codegen method."""

    def setUp(self):
        from server.services.pipeline.codegen_service import CodegenService
        self.service = CodegenService(_make_context())

    def test_codegen_has_run_codegen(self):
        self.assertTrue(
            hasattr(self.service, "run_codegen"),
            "CodegenService missing method: run_codegen",
        )


class TestDeployServiceMethods(unittest.TestCase):
    """DeployService must expose the run_deploy method."""

    def setUp(self):
        from server.services.pipeline.deploy_service import DeployService
        self.service = DeployService(_make_context())

    def test_deploy_has_run_deploy(self):
        self.assertTrue(
            hasattr(self.service, "run_deploy"),
            "DeployService missing method: run_deploy",
        )


class TestQualityServiceMethods(unittest.TestCase):
    """QualityService must expose testgen, docgen, and critique methods."""

    def setUp(self):
        from server.services.pipeline.quality_service import QualityService
        self.service = QualityService(_make_context())

    def test_quality_has_run_testgen(self):
        self.assertTrue(
            hasattr(self.service, "run_testgen"),
            "QualityService missing method: run_testgen",
        )

    def test_quality_has_run_docgen(self):
        self.assertTrue(
            hasattr(self.service, "run_docgen"),
            "QualityService missing method: run_docgen",
        )

    def test_quality_has_run_critique(self):
        self.assertTrue(
            hasattr(self.service, "run_critique"),
            "QualityService missing method: run_critique",
        )


class TestOrchestratorMethods(unittest.TestCase):
    """PipelineOrchestrator must expose dispatch_generator_action."""

    def setUp(self):
        from server.services.pipeline.pipeline_orchestrator import PipelineOrchestrator
        self.service = PipelineOrchestrator(_make_context())

    def test_orchestrator_has_dispatch_generator_action(self):
        self.assertTrue(
            hasattr(self.service, "dispatch_generator_action"),
            "PipelineOrchestrator missing method: dispatch_generator_action",
        )


if __name__ == "__main__":
    unittest.main()
