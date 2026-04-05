# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
DEC-6 Facade Removal validation tests.

Verify that the OmniCore god-object facade has been properly decomposed:
- GeneratorService no longer couples to OmniCoreService directly
- main.py accesses the message bus through bus_service, not omnicore._message_bus
- Domain services are exported from the services package __init__
"""

import unittest
from pathlib import Path

# Resolve paths relative to this file so tests work from any cwd
_SERVICES_DIR = Path(__file__).resolve().parent.parent / "services"
_MAIN_PY = Path(__file__).resolve().parent.parent / "main.py"

_GENERATOR_SRC = (_SERVICES_DIR / "generator_service.py").read_text(encoding="utf-8")
_MAIN_SRC = _MAIN_PY.read_text(encoding="utf-8")
_INIT_SRC = (_SERVICES_DIR / "__init__.py").read_text(encoding="utf-8")


class TestGeneratorServiceDecoupled(unittest.TestCase):
    """GeneratorService must not reference OmniCoreService directly."""

    def test_generator_service_no_omnicore_param(self):
        """Constructor should accept route_job_fn and ctx, not omnicore_service."""
        # After DEC-6, the __init__ signature should use route_job_fn/ctx,
        # not omnicore_service as a parameter name.
        lines = _GENERATOR_SRC.splitlines()
        init_lines = [
            line for line in lines
            if "omnicore_service" in line and "def __init__" in line
        ]
        # Also check for omnicore_service as a standalone parameter anywhere
        # in the constructor (may span multiple lines)
        in_init = False
        init_block: list[str] = []
        for line in lines:
            if "def __init__" in line:
                in_init = True
            if in_init:
                init_block.append(line)
                if "):" in line or ") ->" in line:
                    break
        init_sig = " ".join(init_block)
        has_omnicore_param = "omnicore_service" in init_sig
        self.assertFalse(
            has_omnicore_param,
            f"GeneratorService.__init__ still accepts omnicore_service parameter. "
            f"DEC-6 requires route_job_fn and ctx instead.\n"
            f"Signature: {init_sig.strip()}"
        )

    def test_generator_service_no_omnicore_import(self):
        """generator_service.py must not import from omnicore_service."""
        self.assertNotIn(
            "from server.services.omnicore_service import",
            _GENERATOR_SRC,
            "generator_service.py still imports from omnicore_service. "
            "DEC-6 requires this coupling to be removed."
        )

    def test_generator_service_accepts_route_job_fn(self):
        """GeneratorService should be constructable with route_job_fn and ctx."""
        # This is an import-time smoke test. If the signature has changed
        # to route_job_fn/ctx, construction with those kwargs must not raise.
        try:
            from server.services.generator_service import GeneratorService
            svc = GeneratorService(route_job_fn=lambda *a, **kw: None, ctx=None)
            self.assertIsNotNone(svc)
        except TypeError as exc:
            self.fail(
                f"GeneratorService does not accept route_job_fn/ctx kwargs: {exc}"
            )


class TestMainNoOmnicorePrivateAccess(unittest.TestCase):
    """main.py must not reach into OmniCoreService private attributes."""

    def test_main_no_omnicore_message_bus(self):
        """main.py must not access omnicore_service._message_bus."""
        for pattern in ("omnicore_service._message_bus", "omnicore._message_bus"):
            self.assertNotIn(
                pattern,
                _MAIN_SRC,
                f"main.py still contains '{pattern}'. "
                f"DEC-6 requires bus_service.get_bus() instead."
            )

    def test_main_no_omnicore_private_attrs(self):
        """main.py must not use ._message_bus at all (use bus_service.get_bus())."""
        lines = _MAIN_SRC.splitlines()
        violations = [
            (i + 1, line.strip())
            for i, line in enumerate(lines)
            if "._message_bus" in line and not line.strip().startswith("#")
        ]
        self.assertEqual(
            violations,
            [],
            f"main.py still accesses ._message_bus in {len(violations)} place(s): "
            + "; ".join(f"line {n}: {l}" for n, l in violations[:5])
        )


class TestInitExportsDomainServices(unittest.TestCase):
    """services/__init__.py must export the new domain services."""

    def test_init_exports_domain_services(self):
        """__init__.py should export AdminService, MessageBusService, etc."""
        required_exports = [
            "AdminService",
            "MessageBusService",
            "DiagnosticsService",
            "AuditQueryService",
        ]
        missing = [name for name in required_exports if name not in _INIT_SRC]
        self.assertEqual(
            missing,
            [],
            f"services/__init__.py is missing exports for: {', '.join(missing)}. "
            f"DEC-6 requires these domain services to be publicly exported."
        )


if __name__ == "__main__":
    unittest.main()
