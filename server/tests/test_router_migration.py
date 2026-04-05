# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
DEC-5 / DEC-6 Router Migration Verification Tests.

These tests verify that the OmniCore facade decomposition is correctly
reflected in the HTTP router layer:

- Routers must NOT import the old monolithic ``get_omnicore_service`` helper
  once the migration is complete.
- Routers must NOT access private attributes (``._message_bus``) on the
  service object; they should go through a dedicated bus service instead.
- Event emission in the jobs router must go through a bus/message-bus
  service, not through ``omnicore_service.emit_event`` directly.
- The new ``job_router`` service module must be importable and produce the
  expected result structure.
- The fixes router must not import ``OmniCoreService`` once decoupled.

All "grep-style" tests read source files with ``pathlib.Path.read_text()``
so they work without importing (and therefore without satisfying) the
module's runtime dependencies.
"""

import unittest
from pathlib import Path

# Absolute paths to the source files under test
_REPO = Path(__file__).resolve().parents[2]  # .../A.S.E
_ROUTERS = _REPO / "server" / "routers"
_SERVICES = _REPO / "server" / "services"


class TestOmnicoreRouterNoOldImport(unittest.TestCase):
    """DEC-5: omnicore.py must not use the legacy service-locator import."""

    def test_omnicore_router_no_old_import(self):
        source = (_ROUTERS / "omnicore.py").read_text(encoding="utf-8")
        self.assertNotIn(
            "from server.services.omnicore_service import get_omnicore_service",
            source,
            "omnicore.py still contains the old "
            "'from server.services.omnicore_service import get_omnicore_service' "
            "import.  After DEC-5 migration it should use a thin dependency "
            "provider or the decomposed services directly.",
        )


class TestEventsRouterNoPrivateAttr(unittest.TestCase):
    """DEC-5: events.py must not reach into private OmniCoreService internals."""

    def test_events_router_no_private_attr(self):
        source = (_ROUTERS / "events.py").read_text(encoding="utf-8")
        self.assertNotIn(
            "._message_bus",
            source,
            "events.py still accesses '._message_bus' on OmniCoreService.  "
            "After DEC-5 migration it should use the public MessageBusService "
            "interface instead of reaching into private attributes.",
        )


class TestJobsRouterNoOmnicoreEmit(unittest.TestCase):
    """DEC-6: jobs.py event emission must go through the bus service."""

    def test_jobs_router_no_omnicore_emit(self):
        source = (_ROUTERS / "jobs.py").read_text(encoding="utf-8")
        # After migration, emit_event should be called on bus_service or
        # message_bus_service -- NOT on omnicore_service.
        lines = source.splitlines()
        for lineno, line in enumerate(lines, start=1):
            if "emit_event" in line and "omnicore_service" in line:
                # Allow comments that merely describe the old pattern
                stripped = line.lstrip()
                if stripped.startswith("#"):
                    continue
                self.fail(
                    f"jobs.py:{lineno} still calls emit_event on "
                    f"omnicore_service:\n  {line.strip()}\n"
                    "After DEC-6 migration, emit_event should be called on "
                    "bus_service or message_bus_service.",
                )


class TestJobRouterModuleImportable(unittest.TestCase):
    """DEC-6: the new job_router service module must be importable."""

    def test_job_router_module_importable(self):
        # This exercises the actual import machinery.
        from server.services.job_router import route_job  # noqa: F401

        self.assertTrue(
            callable(route_job),
            "route_job should be a callable (async function).",
        )


class TestJobRouterMakeResultStructure(unittest.TestCase):
    """DEC-6: _make_route_result must produce a dict with required keys."""

    def test_job_router_make_result_structure(self):
        from server.services.job_router import _make_route_result

        result = _make_route_result(
            job_id="test-001",
            source="generator",
            target="sfe",
            transport="message_bus",
        )

        self.assertIsInstance(result, dict)

        required_keys = {"job_id", "routed", "source", "target", "transport"}
        missing = required_keys - result.keys()
        self.assertFalse(
            missing,
            f"_make_route_result is missing expected keys: {missing}",
        )

        # Verify values match what was passed in
        self.assertEqual(result["job_id"], "test-001")
        self.assertEqual(result["source"], "generator")
        self.assertEqual(result["target"], "sfe")
        self.assertEqual(result["transport"], "message_bus")
        self.assertTrue(result["routed"])

    def test_job_router_make_result_with_error(self):
        from server.services.job_router import _make_route_result

        result = _make_route_result(
            job_id="test-002",
            source="api",
            target="generator",
            transport="direct",
            routed=False,
            error="dispatch failed",
        )

        self.assertFalse(result["routed"])
        self.assertEqual(result["error"], "dispatch failed")
        self.assertIn("data", result)
        self.assertEqual(result["data"]["status"], "error")


class TestFixesRouterNoOmnicoreImport(unittest.TestCase):
    """DEC-5: fixes.py should not import OmniCoreService after migration."""

    def test_fixes_router_no_omnicore_import(self):
        source = (_ROUTERS / "fixes.py").read_text(encoding="utf-8")
        # Check for direct class import from the monolith
        self.assertNotIn(
            "from server.services import OmniCoreService",
            source,
            "fixes.py still imports OmniCoreService from server.services.  "
            "After DEC-5 migration it should depend only on the specific "
            "decomposed service(s) it actually needs.",
        )


if __name__ == "__main__":
    unittest.main()
