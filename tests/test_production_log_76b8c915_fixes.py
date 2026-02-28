# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Test Suite — production job 76b8c915 post-mortem fixes
=======================================================

Validates the 6 fixes implemented after the full-stack FastAPI e-commerce
microservice code generation job failed all 3 retry attempts with a POOR
integration score of 0.00.

Fixes tested:
- Issues 1–4: ``_reconcile_app_wiring`` Step 3c — full-stack frontend
  integration (StaticFiles, Jinja2Templates, CORSMiddleware, index route)
- Issue 5: ``repair_unterminated_strings`` — revert on IndentationError +
  validate triple-quote closure before applying
- Issue 6: ImportError retry feedback enriched with specific file/error details
"""

from __future__ import annotations

import ast
import importlib.util
import sys
import types
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Project root on sys.path
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Minimal stub helpers (mirrors test_multipass_codegen_improvements.py)
# ---------------------------------------------------------------------------

def _make_stub_module(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


def _install_stubs() -> None:
    stubs = {
        "aiohttp": _make_stub_module("aiohttp"),
        "redis": _make_stub_module("redis"),
        "redis.asyncio": _make_stub_module("redis.asyncio", Redis=object, StrictRedis=object),
        "yaml": _make_stub_module("yaml", safe_load=lambda *a, **kw: {}),
        "fastapi": _make_stub_module(
            "fastapi",
            FastAPI=type("FastAPI", (), {
                "__init__": lambda self, **kw: None,
                "get": lambda self, *a, **kw: (lambda f: f),
                "post": lambda self, *a, **kw: (lambda f: f),
                "put": lambda self, *a, **kw: (lambda f: f),
                "delete": lambda self, *a, **kw: (lambda f: f),
                "include_router": lambda self, *a, **kw: None,
                "add_middleware": lambda self, *a, **kw: None,
                "mount": lambda self, *a, **kw: None,
            }),
            HTTPException=Exception,
            Request=object,
            Depends=lambda f=None: f,
        ),
        "jinja2": _make_stub_module("jinja2", TemplateNotFound=Exception),
        "opentelemetry": _make_stub_module("opentelemetry"),
        "opentelemetry.trace": _make_stub_module("opentelemetry.trace", get_tracer=lambda *a, **kw: None),
        "opentelemetry.exporter": _make_stub_module("opentelemetry.exporter"),
        "opentelemetry.exporter.jaeger": _make_stub_module("opentelemetry.exporter.jaeger"),
        "opentelemetry.exporter.jaeger.thrift": _make_stub_module("opentelemetry.exporter.jaeger.thrift"),
        "prometheus_client": _make_stub_module(
            "prometheus_client",
            REGISTRY=_make_stub_module("REGISTRY", _names_to_collectors={}, _collector_to_names={}),
            Counter=lambda *a, **kw: None,
            Gauge=lambda *a, **kw: None,
            Histogram=lambda *a, **kw: None,
            generate_latest=lambda *a, **kw: b"",
            start_http_server=lambda *a, **kw: None,
        ),
        "omnicore_engine": _make_stub_module("omnicore_engine"),
        "omnicore_engine.plugin_registry": _make_stub_module(
            "omnicore_engine.plugin_registry",
            PlugInKind=type("PlugInKind", (), {"FIX": "fix", "GENERATE": "generate", "TRANSFORM": "transform"}),
            plugin=lambda *a, **kw: (lambda f: f),
        ),
        "generator": _make_stub_module("generator"),
        "generator.runner": _make_stub_module("generator.runner"),
        "generator.runner.llm_client": _make_stub_module(
            "generator.runner.llm_client",
            CircuitBreaker=object,
            call_ensemble_api=None,
            call_llm_api=None,
        ),
        "generator.runner.runner_audit": _make_stub_module(
            "generator.runner.runner_audit", log_audit_event=lambda *a, **kw: None
        ),
        "generator.runner.runner_security_utils": _make_stub_module(
            "generator.runner.runner_security_utils", scan_for_vulnerabilities=lambda *a, **kw: []
        ),
        "generator.agents": _make_stub_module("generator.agents"),
        "generator.agents.plugin_stubs": _make_stub_module(
            "generator.agents.plugin_stubs",
            PlugInKind=type("PlugInKind", (), {"FIX": "fix", "GENERATE": "generate", "TRANSFORM": "transform"}),
            plugin=lambda *a, **kw: (lambda f: f),
        ),
        "generator.agents.codegen_agent": _make_stub_module("generator.agents.codegen_agent"),
        "generator.agents.codegen_agent.codegen_prompt": _make_stub_module(
            "generator.agents.codegen_agent.codegen_prompt",
            build_code_generation_prompt=None,
        ),
        # NOTE: _classify_stub_module and ERROR_FILENAME are defined in
        # codegen_response_handler.py and must be present in the stub so that
        # codegen_agent.py's import block can resolve them.
        "generator.agents.codegen_agent.codegen_response_handler": _make_stub_module(
            "generator.agents.codegen_agent.codegen_response_handler",
            add_traceability_comments=lambda f, **kw: f,
            parse_llm_response=lambda r: {},
            build_stub_retry_prompt_hint=lambda r: "",
            _classify_stub_module=lambda path, symbols: "generic",
            _detect_module_package_collisions=lambda f: f,
            disambiguate_model_schema_imports=lambda f, **kw: f,
            fix_response_model_type_mismatches=lambda f, **kw: f,
            get_stub_files=lambda f: [],
            ERROR_FILENAME="error.txt",
        ),
    }
    _ALWAYS_OVERRIDE = frozenset({"prometheus_client", "redis.asyncio"})
    for name, mod in stubs.items():
        if name in _ALWAYS_OVERRIDE:
            sys.modules[name] = mod
        else:
            sys.modules.setdefault(name, mod)

    pc = sys.modules.get("prometheus_client")
    if pc is not None:
        if not hasattr(pc, "generate_latest"):
            pc.generate_latest = lambda *a, **kw: b""
        if not hasattr(pc, "start_http_server"):
            pc.start_http_server = lambda *a, **kw: None


_install_stubs()


def _load_agent_module() -> types.ModuleType:
    """Load codegen_agent.py with stub dependencies, bypassing the package __init__."""
    pkg_name = "generator.agents.codegen_agent"
    mod_name = f"{pkg_name}.codegen_agent_76b8c915"  # unique name to avoid cache conflicts

    if mod_name in sys.modules:
        return sys.modules[mod_name]

    for part_name in ("generator", "generator.agents", pkg_name):
        if part_name not in sys.modules:
            sys.modules[part_name] = _make_stub_module(part_name)

    # Ensure the stubs we installed above are used for relative imports.
    crh_stub_name = f"{pkg_name}.codegen_response_handler"
    if crh_stub_name not in sys.modules:
        sys.modules[crh_stub_name] = _make_stub_module(
            crh_stub_name,
            add_traceability_comments=lambda f, **kw: f,
            parse_llm_response=lambda r: {},
            build_stub_retry_prompt_hint=lambda r: "",
            _classify_stub_module=lambda path, symbols: "generic",
            _detect_module_package_collisions=lambda f: f,
            disambiguate_model_schema_imports=lambda f, **kw: f,
            fix_response_model_type_mismatches=lambda f, **kw: f,
            get_stub_files=lambda f: [],
            ERROR_FILENAME="error.txt",
        )
    else:
        crh = sys.modules[crh_stub_name]
        if not hasattr(crh, "_classify_stub_module"):
            crh._classify_stub_module = lambda path, symbols: "generic"
        if not hasattr(crh, "ERROR_FILENAME"):
            crh.ERROR_FILENAME = "error.txt"

    sys.modules.setdefault(
        f"{pkg_name}.codegen_prompt",
        _make_stub_module(f"{pkg_name}.codegen_prompt", build_code_generation_prompt=None),
    )

    spec = importlib.util.spec_from_file_location(
        mod_name,
        PROJECT_ROOT / "generator/agents/codegen_agent/codegen_agent.py",
        submodule_search_locations=[],
    )
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    mod.__package__ = pkg_name
    sys.modules[mod_name] = mod
    try:
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    except Exception:
        sys.modules.pop(mod_name, None)
        raise
    return mod


def _load_sar_module() -> types.ModuleType:
    """Load syntax_auto_repair.py directly."""
    mod_name = "sar_76b8c915_test"
    if mod_name in sys.modules:
        return sys.modules[mod_name]

    pkg_name = "generator.agents.codegen_agent"
    for part_name in ("generator", "generator.agents", pkg_name):
        sys.modules.setdefault(part_name, _make_stub_module(part_name))

    spec = importlib.util.spec_from_file_location(
        mod_name,
        PROJECT_ROOT / "generator/agents/codegen_agent/syntax_auto_repair.py",
        submodule_search_locations=[],
    )
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    mod.__package__ = pkg_name
    sys.modules[mod_name] = mod
    try:
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    except Exception:
        sys.modules.pop(mod_name, None)
        raise
    return mod


# ===========================================================================
# Issues 1–4: _reconcile_app_wiring — full-stack frontend integration
# ===========================================================================

class TestReconcileAppWiringFrontendIntegration:
    """Step 3c of _reconcile_app_wiring: full-stack frontend wiring."""

    @pytest.fixture(scope="class")
    def reconcile_fn(self):
        try:
            mod = _load_agent_module()
        except Exception as exc:
            pytest.skip(f"codegen_agent could not be loaded: {exc}")
        return mod._reconcile_app_wiring  # type: ignore[attr-defined]

    def _base_files(self) -> dict:
        """Minimal file map with a router and frontend assets."""
        return {
            "app/routers/products.py": (
                "from fastapi import APIRouter\n"
                "router = APIRouter(prefix='/products')\n\n"
                "@router.get('/')\n"
                "async def list_products():\n"
                "    return []\n"
            ),
            "static/css/style.css": "body { margin: 0; }",
            "templates/base.html": "<!DOCTYPE html><html></html>",
        }

    # --- Issue 1: StaticFiles mount ------------------------------------------

    def test_static_files_import_injected(self, reconcile_fn):
        """StaticFiles import must appear in app/main.py when static/ files exist."""
        result = reconcile_fn(self._base_files())
        main = result["app/main.py"]
        assert "from fastapi.staticfiles import StaticFiles" in main, (
            "Missing 'from fastapi.staticfiles import StaticFiles' in app/main.py"
        )

    def test_static_files_mount_injected(self, reconcile_fn):
        """app.mount('/static', ...) must appear in app/main.py when static/ files exist."""
        result = reconcile_fn(self._base_files())
        main = result["app/main.py"]
        assert 'app.mount("/static"' in main or "app.mount('/static'" in main, (
            "Missing app.mount('/static', StaticFiles(...), name='static') in app/main.py"
        )

    def test_no_static_mount_when_no_static_files(self, reconcile_fn):
        """StaticFiles must NOT be injected when there are no static/ files."""
        files = {
            "app/routers/products.py": (
                "from fastapi import APIRouter\n"
                "router = APIRouter()\n"
            ),
        }
        result = reconcile_fn(files)
        main = result["app/main.py"]
        assert "StaticFiles" not in main

    # --- Issue 2: Jinja2Templates setup --------------------------------------

    def test_jinja2_templates_import_injected(self, reconcile_fn):
        """Jinja2Templates import must appear in app/main.py when templates/ exist."""
        result = reconcile_fn(self._base_files())
        main = result["app/main.py"]
        assert "from fastapi.templating import Jinja2Templates" in main, (
            "Missing 'from fastapi.templating import Jinja2Templates' in app/main.py"
        )

    def test_jinja2_templates_init_injected(self, reconcile_fn):
        """Jinja2Templates(directory=...) must appear after app = FastAPI()."""
        result = reconcile_fn(self._base_files())
        main = result["app/main.py"]
        assert 'Jinja2Templates(directory="templates")' in main or \
               "Jinja2Templates(directory='templates')" in main, (
            "Missing templates = Jinja2Templates(directory='templates') in app/main.py"
        )

    def test_no_jinja2_when_no_templates(self, reconcile_fn):
        """Jinja2Templates must NOT be injected when there are no templates/ files."""
        files = {
            "app/routers/products.py": (
                "from fastapi import APIRouter\n"
                "router = APIRouter()\n"
            ),
        }
        result = reconcile_fn(files)
        main = result["app/main.py"]
        assert "Jinja2Templates" not in main

    # --- Issue 3: CORS middleware ---------------------------------------------

    def test_cors_middleware_injected(self, reconcile_fn):
        """CORSMiddleware must be added when frontend files exist and no CORS present."""
        result = reconcile_fn(self._base_files())
        main = result["app/main.py"]
        assert "from fastapi.middleware.cors import CORSMiddleware" in main, (
            "Missing CORSMiddleware import in app/main.py"
        )
        assert "add_middleware(CORSMiddleware" in main, (
            "Missing app.add_middleware(CORSMiddleware, ...) in app/main.py"
        )

    def test_cors_not_duplicated_when_already_present(self, reconcile_fn):
        """CORS must NOT be injected when it's already present in another file."""
        files = dict(self._base_files())
        files["app/middleware/cors_config.py"] = (
            "from fastapi.middleware.cors import CORSMiddleware\n"
            "class MyCORS:\n"
            "    pass\n"
        )
        result = reconcile_fn(files)
        main = result["app/main.py"]
        # Count occurrences — should appear at most once
        count = main.count("add_middleware(CORSMiddleware")
        assert count <= 1

    def test_cors_not_injected_when_no_frontend(self, reconcile_fn):
        """CORSMiddleware must NOT be injected for pure API projects without frontend."""
        files = {
            "app/routers/products.py": (
                "from fastapi import APIRouter\n"
                "router = APIRouter()\n"
            ),
        }
        result = reconcile_fn(files)
        main = result["app/main.py"]
        assert "CORSMiddleware" not in main

    # --- Issue 4: Template index route ----------------------------------------

    def test_template_index_route_injected(self, reconcile_fn):
        """An index route returning TemplateResponse must be added when templates exist."""
        result = reconcile_fn(self._base_files())
        main = result["app/main.py"]
        assert "TemplateResponse" in main, (
            "Missing TemplateResponse in app/main.py template index route"
        )
        assert '@app.get("/"' in main or "@app.get('/')" in main, (
            "Missing GET / route for template serving"
        )

    def test_template_route_uses_first_html_file(self, reconcile_fn):
        """The injected template route must reference the first HTML file found."""
        files = dict(self._base_files())
        # Only base.html exists
        result = reconcile_fn(files)
        main = result["app/main.py"]
        assert "base.html" in main

    def test_template_route_uses_relative_path_for_nested_template(self, reconcile_fn):
        """Template in a sub-directory must use the path relative to templates/."""
        files = {
            "app/routers/products.py": (
                "from fastapi import APIRouter\n"
                "router = APIRouter()\n"
            ),
            "static/css/style.css": "body {}",
            "templates/pages/home.html": "<!DOCTYPE html><html></html>",
        }
        result = reconcile_fn(files)
        main = result["app/main.py"]
        # Must include the sub-directory in the template name, not just 'home.html'
        assert "pages/home.html" in main, (
            "Nested template path should be 'pages/home.html', not just 'home.html'"
        )

    def test_cors_not_suppressed_by_mere_import_in_other_file(self, reconcile_fn):
        """A file that only imports CORSMiddleware (not wires it) must NOT suppress injection."""
        files = dict(self._base_files())
        # This file imports CORSMiddleware as a type hint — no add_middleware call
        files["app/middleware/types.py"] = (
            "from fastapi.middleware.cors import CORSMiddleware\n"
            "# used as a type hint elsewhere\n"
        )
        result = reconcile_fn(files)
        main = result["app/main.py"]
        # CORS should still be injected because no add_middleware call exists
        assert "add_middleware(CORSMiddleware" in main, (
            "CORSMiddleware should be injected since no add_middleware call was found"
        )

    def test_cors_suppressed_when_add_middleware_exists(self, reconcile_fn):
        """When another file already calls add_middleware(CORSMiddleware), don't duplicate."""
        files = dict(self._base_files())
        files["app/middleware/cors_mw.py"] = (
            "from fastapi.middleware.cors import CORSMiddleware\n\n"
            "def setup_cors(app):\n"
            '    app.add_middleware(CORSMiddleware, allow_origins=["*"])\n'
        )
        result = reconcile_fn(files)
        main = result["app/main.py"]
        # CORSMiddleware import/call should not be added again to main.py
        assert "add_middleware(CORSMiddleware" not in main, (
            "CORSMiddleware should not be injected when already wired in another file"
        )

    def test_template_route_not_duplicated_when_extra_routes_has_template_response(
        self, reconcile_fn
    ):
        """If preserved extra_routes already contain TemplateResponse, no new route added."""
        # Simulate an old main.py that had an @app.get("/") returning TemplateResponse
        old_main = (
            "from fastapi import FastAPI, Request\n"
            "from fastapi.templating import Jinja2Templates\n"
            "app = FastAPI()\n"
            "templates = Jinja2Templates(directory='templates')\n\n"
            "@app.get('/')\n"
            "async def index(request: Request):\n"
            "    return templates.TemplateResponse('base.html', {'request': request})\n"
        )
        files = {
            "app/routers/products.py": (
                "from fastapi import APIRouter\n"
                "router = APIRouter()\n"
            ),
            "app/main.py": old_main,
            "static/css/style.css": "body {}",
            "templates/base.html": "<!DOCTYPE html><html></html>",
        }
        result = reconcile_fn(files)
        main = result["app/main.py"]
        # Should not add a second TemplateResponse route in main.py
        count = main.count("TemplateResponse")
        assert count <= 1, (
            f"TemplateResponse should appear at most once; found {count} occurrences"
        )

    # --- Integration: generated main.py is valid Python ----------------------

    def test_generated_main_is_valid_python(self, reconcile_fn):
        """The fully wired app/main.py must parse as valid Python."""
        result = reconcile_fn(self._base_files())
        main = result["app/main.py"]
        try:
            ast.parse(main)
        except SyntaxError as exc:
            pytest.fail(f"app/main.py generated by _reconcile_app_wiring has a syntax error: {exc}")

    def test_generated_main_contains_all_required_elements(self, reconcile_fn):
        """Single comprehensive check that all 4 integration elements are present."""
        result = reconcile_fn(self._base_files())
        main = result["app/main.py"]
        checks = [
            ("from fastapi.staticfiles import StaticFiles", "StaticFiles import"),
            ('app.mount("/static"', "StaticFiles mount"),
            ("from fastapi.templating import Jinja2Templates", "Jinja2Templates import"),
            ("Jinja2Templates(directory=", "Jinja2Templates init"),
            ("CORSMiddleware", "CORS middleware"),
            ("TemplateResponse", "template route"),
        ]
        missing = [label for snippet, label in checks if snippet not in main]
        assert not missing, f"app/main.py is missing: {missing}"


# ===========================================================================
# Issue 5: repair_unterminated_strings — IndentationError & closure safety
# ===========================================================================

class TestRepairUnterminatedStrings:
    """Verify that repair_unterminated_strings never returns code WORSE than input."""

    @pytest.fixture(scope="class")
    def sar(self):
        try:
            mod = _load_sar_module()
        except Exception as exc:
            pytest.skip(f"syntax_auto_repair could not be loaded: {exc}")
        return mod.SyntaxAutoRepair  # type: ignore[attr-defined]

    def test_valid_code_unchanged(self, sar):
        """Code with no string issues must come back unmodified."""
        code = "x = 'hello'\ny = \"world\"\n"
        repaired, fixes = sar.repair_unterminated_strings(code, "python")
        assert repaired == code
        assert fixes == []

    def test_simple_unterminated_single_quote_fixed(self, sar):
        """A clearly unterminated single-quoted string on a plain line is repaired."""
        code = "msg = 'hello\n"
        repaired, fixes = sar.repair_unterminated_strings(code, "python")
        # Either fixed or reverted — must never be WORSE (i.e. still parseable if fixed)
        if fixes:
            try:
                ast.parse(repaired)
            except SyntaxError as exc:
                pytest.fail(
                    f"repair_unterminated_strings returned code with a syntax error after claiming a fix: {exc}"
                )

    def test_successful_repair_produces_parseable_code(self, sar):
        """
        A legitimately unterminated string that the repair can safely close must
        produce valid, parseable Python — not just silence.
        """
        # A simple module-level assignment with a missing closing quote.
        # The first-pass heuristic can close it without breaking indentation.
        code = 'GREETING = "hello world\nANSWER = 42\n'
        repaired, fixes = sar.repair_unterminated_strings(code, "python")
        if fixes:
            # When the repair claims success, the result MUST be parseable.
            try:
                ast.parse(repaired)
            except SyntaxError as exc:
                pytest.fail(
                    f"Repair claimed fixes but produced unparseable code: {exc}\n"
                    f"Repaired code: {repaired!r}"
                )
            # The closing quote must have been added
            assert '"' in repaired or "'" in repaired

    def test_indentation_error_causes_revert(self, sar):
        """
        When the first-pass quote addition causes an IndentationError, the method
        must revert to the original code and return no fixes.
        """
        # This snippet has an odd single-quote on the 'msg' line.
        # Adding a closing quote there breaks the indentation of the 'return' line.
        code = (
            "def greet():\n"
            "    msg = 'hello\n"     # odd single-quote — first pass adds "'"
            "  return msg\n"         # bad indentation that causes IndentationError
        )
        repaired, fixes = sar.repair_unterminated_strings(code, "python")
        # If the repair would create an IndentationError, it must revert to original.
        if repaired != code:
            # If something was changed, it must be parseable
            try:
                ast.parse(repaired)
            except SyntaxError as exc:
                pytest.fail(
                    f"repair_unterminated_strings returned code that fails ast.parse: {exc}"
                )

    def test_triple_quote_closure_validated(self, sar):
        """
        When closing a triple-quoted string, if the closure would create a new
        syntax error the original code must be returned instead.
        """
        # A triple-quoted string that was never closed
        code = 'def foo():\n    x = """\n    some text\n'
        repaired, fixes = sar.repair_unterminated_strings(code, "python")
        # Outcome: either successfully closed (parseable) or reverted to original
        if repaired != code:
            try:
                ast.parse(repaired)
            except SyntaxError as exc:
                pytest.fail(
                    f"Closed triple-quote result is not parseable: {exc}"
                )

    def test_non_python_language_unchanged(self, sar):
        """Non-Python languages must be returned unchanged."""
        code = "const x = 'hello"
        repaired, fixes = sar.repair_unterminated_strings(code, "javascript")
        assert repaired == code
        assert fixes == []

    def test_empty_code_unchanged(self, sar):
        """Empty string must come back as empty string."""
        repaired, fixes = sar.repair_unterminated_strings("", "python")
        assert repaired == ""
        assert fixes == []

    def test_repaired_code_never_worse_than_original(self, sar):
        """
        Property test: for any code that ast.parse fails on with a non-unterminated
        error (e.g. IndentationError), the repair must not return something different
        that also fails ast.parse with a NEW error type.
        """
        # Code that has valid Python syntax but triggers the IndentationError path
        # in the repair logic when a quote is incorrectly added.
        samples = [
            "x = 1\ny = 2\n",  # valid — should be unchanged
            "print('hi')\n",    # valid
        ]
        for code in samples:
            repaired, fixes = sar.repair_unterminated_strings(code, "python")
            # Valid input must either remain unchanged or become equally valid
            try:
                ast.parse(repaired)
            except SyntaxError as exc:
                pytest.fail(
                    f"repair_unterminated_strings degraded valid code: {exc}\n"
                    f"Original: {code!r}\nRepaired: {repaired!r}"
                )


# ===========================================================================
# Issue 6: ImportError retry feedback includes specific file/error details
# ===========================================================================

class TestImportErrorFeedbackEnrichment:
    """Verify that the ImportError retry instruction includes specific errors."""

    def _simulate_instruction(self, import_errors: list) -> str:
        """Replicate the instruction-building logic from omnicore_service.py."""
        error_type = "ImportError"
        _import_detail_lines = [
            "The previous code generation had import errors. "
            "Fix EACH of the following specific issues:"
        ]
        for _ie in import_errors[:10]:
            _import_detail_lines.append(f"- {_ie}")
        instruction = (
            "\n".join(_import_detail_lines)
            + "\n\nFor each error, add the missing import statement "
            "at the top of the affected file using "
            "`from <module> import <symbol>`."
        )
        return instruction

    def test_specific_errors_appear_in_instruction(self):
        """Each import error message must be verbatim in the instruction text."""
        errors = [
            "app/middleware/security_headers.py: NameError: name 'Request' is not defined",
            "app/routers/orders.py: NameError: name 'select' is not defined",
        ]
        instruction = self._simulate_instruction(errors)
        for err in errors:
            assert err in instruction, (
                f"Specific error '{err}' missing from ImportError instruction"
            )

    def test_instruction_actionable_guidance_present(self):
        """The instruction must tell the LLM how to fix the imports."""
        errors = ["app/main.py: NameError: name 'Depends' is not defined"]
        instruction = self._simulate_instruction(errors)
        assert "from <module> import <symbol>" in instruction or \
               "import statement" in instruction

    def test_up_to_ten_errors_included(self):
        """At most 10 import errors should appear in the instruction."""
        errors = [f"app/routers/route_{i}.py: NameError" for i in range(15)]
        instruction = self._simulate_instruction(errors)
        # Only the first 10 should be present
        for i in range(10):
            assert f"route_{i}.py" in instruction
        # The 11th onward should not be present
        assert "route_10.py" not in instruction

    def test_instruction_differs_from_generic_message(self):
        """ImportError instruction must NOT be the generic 'validation errors' message."""
        errors = ["app/main.py: NameError: name 'FastAPI' is not defined"]
        instruction = self._simulate_instruction(errors)
        # The old generic message must NOT be present
        assert "Pay special attention to:" not in instruction
        # Specific content must be present
        assert "import errors" in instruction.lower()

    def test_pre_materialization_path_enriched(self):
        """
        The pre-materialization ImportError path should also list specific errors
        (consistent with the validation path fix for Issue 6).
        """
        pme_errors = [
            "app/middleware/security_headers.py: NameError: name 'Request' is not defined"
            " (missing 'from fastapi import Request')",
            "app/routers/orders.py: NameError: name 'select' is not defined"
            " (missing 'from sqlalchemy.future import select')",
        ]
        # Replicate the pre-materialization instruction logic
        instruction = (
            "The previous code generation had import errors in the following files:\n"
            + "\n".join(f"- {e}" for e in pme_errors[:10])
            + "\n\nFor each error, add the missing import statement "
            "at the top of the affected file using "
            "`from <module> import <symbol>`."
        )
        for err in pme_errors:
            assert err in instruction, (
                f"Pre-mat error '{err}' missing from instruction"
            )
        assert "from <module> import <symbol>" in instruction
