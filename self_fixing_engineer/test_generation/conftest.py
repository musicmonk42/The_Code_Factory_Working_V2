# test_generation/tests/conftest.py
from __future__ import annotations

import importlib
import logging
import os
import sys
import types
from pathlib import Path

logger = logging.getLogger(__name__)
if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")

# -----------------------------------------------------------------------------
# Ensure the repo root is on sys.path so "import test_generation" can resolve
# even when pytest sets rootdir to .../test_generation
# -----------------------------------------------------------------------------
THIS_FILE = Path(__file__).resolve()
TESTS_DIR = THIS_FILE.parent  # .../test_generation/tests
PKG_DIR = TESTS_DIR.parent  # .../test_generation
REPO_DIR = PKG_DIR.parent  # .../

# Put the repo root at the *front* of sys.path
repo_str = str(REPO_DIR)
if repo_str not in sys.path:
    sys.path.insert(0, repo_str)


# -----------------------------------------------------------------------------
# If direct import of "test_generation" fails (or resolves to a broken stub),
# alias it to "self_fixing_engineer.test_generation" if that package exists.
# This covers the case where rootdir == .../test_generation (so Python would
# otherwise look for .../test_generation/test_generation which doesn't exist).
# -----------------------------------------------------------------------------
def _alias_test_generation_if_needed() -> None:
    primary = "test_generation"
    alt = "self_fixing_engineer.test_generation"

    try:
        tg = importlib.import_module(primary)
        # If we somehow got a nameless/namespace-y module with no __file__ nor __path__,
        # consider it broken and fall back to aliasing.
        if not getattr(tg, "__file__", None) and not getattr(tg, "__path__", None):
            raise ImportError(f"{primary} resolved to a stub without __file__/__path__")
        return
    except Exception:
        pass

    try:
        alt_mod = importlib.import_module(alt)
    except Exception as e:
        # Surface a clear error that explains *why* import failed
        raise ImportError(
            f"Could not import '{primary}', and aliasing to '{alt}' also failed: {e}"
        ) from e

    # Install alias for the package itself
    sys.modules[primary] = alt_mod

    # Install aliases for already-imported submodules so dotted imports work too
    alt_prefix = alt + "."
    for name, mod in list(sys.modules.items()):
        if name.startswith(alt_prefix):
            sys.modules[primary + name[len(alt) :]] = mod

    logger.warning("Aliased %s -> %s for pytest collection", primary, alt)


# Skip expensive module aliasing during pytest collection to improve collection performance
# Set PYTEST_COLLECTING=1 environment variable in root conftest.py during collection phase
if os.environ.get("PYTEST_COLLECTING") != "1":
    _alias_test_generation_if_needed()

# -----------------------------------------------------------------------------
# Provide a tiny stub for arbiter.audit_log if not installed (avoid side effects)
# -----------------------------------------------------------------------------
try:
    import arbiter.audit_log as _al  # noqa: F401
except Exception:
    logging.getLogger().warning("Using stubbed arbiter.audit_log in conftest.py")
    stub_mod = types.ModuleType("arbiter.audit_log")

    async def _stub_log_event(event_type, data=None, critical=False, **kwargs):
        # no-op for tests (keeps stdout-visible breadcrumb for assertions if needed)
        print(f"Audit event logged: {event_type}")

    stub_mod.audit_logger = types.SimpleNamespace(log_event=_stub_log_event)
    sys.modules["arbiter.audit_log"] = stub_mod

# -----------------------------------------------------------------------------
# Provide a tiny stub for test_generation.onboard (tests don't need real onboarding)
# -----------------------------------------------------------------------------
try:
    import test_generation.onboard  # noqa: F401
except Exception:
    logging.getLogger().warning("Using stubbed test_generation.onboard in conftest.py")
    onboard_stub = types.ModuleType("test_generation.onboard")
    onboard_stub.onboard = lambda *a, **k: None
    onboard_stub.OnboardConfig = object
    onboard_stub.ONBOARD_DEFAULTS = {}
    onboard_stub.CORE_VERSION = "test"
    sys.modules["test_generation.onboard"] = onboard_stub

# -----------------------------------------------------------------------------
# Optional dep stub: werkzeug (some tests import it indirectly; keep it minimal)
# -----------------------------------------------------------------------------
try:
    import werkzeug  # noqa: F401
except Exception:
    wz = types.ModuleType("werkzeug")

    # Minimal surface so "from werkzeug.exceptions import HTTPException" won't explode
    class _HTTPException(Exception):
        code = 500
        description = "Stubbed HTTP exception"

    wz.exceptions = types.SimpleNamespace(HTTPException=_HTTPException)
    sys.modules["werkzeug"] = wz


# -----------------------------------------------------------------------------
# Normalize/repair the gen_agent package so "from test_generation.gen_agent import agents" works
# -----------------------------------------------------------------------------
def _repair_gen_agent_package():
    pkg_name = "test_generation.gen_agent"

    # If some previous run inserted a broken stub (no __file__/__path__), drop it
    existing = sys.modules.get(pkg_name)
    if (
        existing
        and not getattr(existing, "__file__", None)
        and not getattr(existing, "__path__", None)
    ):
        sys.modules.pop(pkg_name, None)

    # Import the real package (must exist as a directory with __init__.py)
    pkg = importlib.import_module(pkg_name)

    # Ensure submodule attaches so "from test_generation.gen_agent import agents" is valid
    try:
        importlib.import_module(f"{pkg_name}.agents")
    except Exception as e:
        # If there's a genuine bug in the submodule, let the test surface it later
        logger.debug("Unable to import %s.agents at collection time: %s", pkg_name, e)
    else:
        if not hasattr(pkg, "agents") and f"{pkg_name}.agents" in sys.modules:
            setattr(pkg, "agents", sys.modules[f"{pkg_name}.agents"])


# Skip expensive package repair during pytest collection to improve collection performance
# Set PYTEST_COLLECTING=1 environment variable in root conftest.py during collection phase
if os.environ.get("PYTEST_COLLECTING") != "1":
    try:
        _repair_gen_agent_package()
    except Exception as e:
        logger.debug("gen_agent package repair skipped: %s", e)

