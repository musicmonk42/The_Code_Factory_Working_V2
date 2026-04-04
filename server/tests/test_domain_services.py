"""Tests for Phase 2 domain services extracted from OmniCoreService.

Validates that each new domain service module is importable, accepts a
ServiceContext for construction, exposes the expected public methods, and
(where applicable) provides a singleton accessor.  These tests act as the
contract that the Phase 2 implementation must satisfy.

Covered services:
    - AdminService          (server.services.admin_service)
    - AuditQueryService     (server.services.audit_query_service)
    - DiagnosticsService    (server.services.diagnostics_service)
    - MessageBusService     (server.services.message_bus_service)
    - SFEDispatchService    (server.services.sfe_dispatch_service)
"""

import unittest
from server.services.service_context import ServiceContext


def _make_context() -> ServiceContext:
    """Return a minimal ServiceContext suitable for unit tests."""
    return ServiceContext()


# ── Import tests ────────────────────────────────────────────────────────────

class TestServiceImports(unittest.TestCase):
    """Each domain service module must be importable."""

    def test_admin_service_importable(self):
        from server.services.admin_service import AdminService
        self.assertTrue(AdminService is not None)

    def test_audit_query_service_importable(self):
        from server.services.audit_query_service import AuditQueryService
        self.assertTrue(AuditQueryService is not None)

    def test_diagnostics_service_importable(self):
        from server.services.diagnostics_service import DiagnosticsService
        self.assertTrue(DiagnosticsService is not None)

    def test_message_bus_service_importable(self):
        from server.services.message_bus_service import MessageBusService
        self.assertTrue(MessageBusService is not None)

    def test_sfe_dispatch_service_importable(self):
        from server.services.sfe_dispatch_service import SFEDispatchService
        self.assertTrue(SFEDispatchService is not None)


# ── Construction tests ──────────────────────────────────────────────────────

class TestServiceConstruction(unittest.TestCase):
    """Each service must accept a ServiceContext and construct without error."""

    def setUp(self):
        self.ctx = _make_context()

    def test_admin_service_construction(self):
        from server.services.admin_service import AdminService
        service = AdminService(self.ctx)
        self.assertIsNotNone(service)

    def test_audit_query_service_construction(self):
        from server.services.audit_query_service import AuditQueryService
        service = AuditQueryService(self.ctx)
        self.assertIsNotNone(service)

    def test_diagnostics_service_construction(self):
        from server.services.diagnostics_service import DiagnosticsService
        service = DiagnosticsService(self.ctx)
        self.assertIsNotNone(service)

    def test_message_bus_service_construction(self):
        from server.services.message_bus_service import MessageBusService
        service = MessageBusService(self.ctx)
        self.assertIsNotNone(service)

    def test_sfe_dispatch_service_construction(self):
        from server.services.sfe_dispatch_service import SFEDispatchService
        service = SFEDispatchService(self.ctx)
        self.assertIsNotNone(service)


# ── Singleton accessor tests ────────────────────────────────────────────────

class TestSingletonAccessors(unittest.TestCase):
    """Services that expose a module-level get_service() must be callable."""

    def test_admin_get_service_callable(self):
        from server.services import admin_service
        self.assertTrue(
            callable(getattr(admin_service, "get_service", None)),
            "admin_service must expose a callable get_service()",
        )

    def test_message_bus_get_service_callable(self):
        from server.services import message_bus_service
        self.assertTrue(
            callable(getattr(message_bus_service, "get_service", None)),
            "message_bus_service must expose a callable get_service()",
        )


# ── Method presence tests ───────────────────────────────────────────────────

class TestAdminServiceMethods(unittest.TestCase):
    """AdminService must expose all admin/plugin/DB/circuit-breaker methods."""

    EXPECTED_METHODS = [
        "configure_llm",
        "get_plugin_status",
        "reload_plugin",
        "browse_marketplace",
        "install_plugin",
        "query_database",
        "export_database",
        "get_circuit_breakers",
        "reset_circuit_breaker",
        "configure_rate_limit",
    ]

    def setUp(self):
        from server.services.admin_service import AdminService
        self.service = AdminService(_make_context())

    def test_all_methods_present(self):
        for name in self.EXPECTED_METHODS:
            with self.subTest(method=name):
                self.assertTrue(
                    hasattr(self.service, name),
                    f"AdminService missing method: {name}",
                )


class TestAuditQueryServiceMethods(unittest.TestCase):
    """AuditQueryService must expose audit trail retrieval."""

    EXPECTED_METHODS = [
        "get_audit_trail",
    ]

    def setUp(self):
        from server.services.audit_query_service import AuditQueryService
        self.service = AuditQueryService(_make_context())

    def test_all_methods_present(self):
        for name in self.EXPECTED_METHODS:
            with self.subTest(method=name):
                self.assertTrue(
                    hasattr(self.service, name),
                    f"AuditQueryService missing method: {name}",
                )


class TestDiagnosticsServiceMethods(unittest.TestCase):
    """DiagnosticsService must expose health, metrics, and status methods."""

    EXPECTED_METHODS = [
        "get_system_status",
        "get_system_health",
        "get_job_metrics",
    ]

    def setUp(self):
        from server.services.diagnostics_service import DiagnosticsService
        self.service = DiagnosticsService(_make_context())

    def test_all_methods_present(self):
        for name in self.EXPECTED_METHODS:
            with self.subTest(method=name):
                self.assertTrue(
                    hasattr(self.service, name),
                    f"DiagnosticsService missing method: {name}",
                )


class TestMessageBusServiceMethods(unittest.TestCase):
    """MessageBusService must expose pub/sub and topic management methods."""

    EXPECTED_METHODS = [
        "publish_message",
        "emit_event",
        "subscribe_to_topic",
        "list_topics",
    ]

    def setUp(self):
        from server.services.message_bus_service import MessageBusService
        self.service = MessageBusService(_make_context())

    def test_all_methods_present(self):
        for name in self.EXPECTED_METHODS:
            with self.subTest(method=name):
                self.assertTrue(
                    hasattr(self.service, name),
                    f"MessageBusService missing method: {name}",
                )


class TestSFEDispatchServiceMethods(unittest.TestCase):
    """SFEDispatchService must expose the SFE analysis dispatch method."""

    EXPECTED_METHODS = [
        "run_sfe_analysis",
    ]

    def setUp(self):
        from server.services.sfe_dispatch_service import SFEDispatchService
        self.service = SFEDispatchService(_make_context())

    def test_all_methods_present(self):
        for name in self.EXPECTED_METHODS:
            with self.subTest(method=name):
                self.assertTrue(
                    hasattr(self.service, name),
                    f"SFEDispatchService missing method: {name}",
                )


if __name__ == "__main__":
    unittest.main()
