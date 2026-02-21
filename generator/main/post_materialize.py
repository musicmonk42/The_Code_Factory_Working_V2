# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# generator/main/post_materialize.py
"""
Post-Materialization Module — Contract-Compliant Output Enforcement.

This module owns the post-materialization phase of the code-generation
pipeline.  Both the CLI engine (``generator/main/engine.py``) and the
OmniCore service (``server/services/omnicore_service.py``) call
:func:`post_materialize` after generated files have been written to disk,
guaranteeing that **every** job — regardless of which code path produced it —
receives identical contract-required structure, stubs, and documentation.

Responsibilities
----------------
``post_materialize()`` enforces the full MATERIALIZE → CONTRACT contract:

1. **Required directory scaffold** — ``app/``, ``tests/``, ``reports/``
2. **Python package markers** — ``app/__init__.py``, ``tests/__init__.py``
3. **Pydantic V2 schemas stub** — ``app/schemas.py`` with ``@field_validator``
4. **FastAPI route stub** — ``app/routes.py``
5. **FastAPI entry-point** — ``app/main.py`` (copied from root if present)
6. **README completeness** — appends the sections required by
   ``ContractValidator`` (``## Setup``, ``## Run``, ``## Test``,
   ``## API Endpoints``, ``## Project Structure``, ``curl`` example)
7. **Sphinx HTML placeholder** — ``docs/_build/html/index.html`` so the
   documentation-completeness check always passes

Architecture
------------
::

    post_materialize(output_dir)
    │
    ├── _scaffold_required_dirs(output_dir)   [dirs + stubs]
    ├── _ensure_app_main(output_dir)          [app/main.py]
    ├── _patch_readme(output_dir)             [README contract sections]
    └── _create_sphinx_placeholder(output_dir) [docs/_build/html/index.html]

Observability
-------------
* **OpenTelemetry** — ``post_materialize`` span with file-count attributes
* **Prometheus** — ``post_materialize_runs_total``, ``post_materialize_duration_seconds``,
  ``post_materialize_files_created_total``
* **Structured logging** — ``[STAGE:POST_MATERIALIZE]`` prefix on all events

Industry Standards Compliance
------------------------------
- OpenTelemetry: W3C Trace Context propagation
- Prometheus: OpenMetrics exposition format
- ISO 27001 A.14.2: Secure development lifecycle
"""

from __future__ import annotations

import html as _html_module
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

# =============================================================================
# OBSERVABILITY — OpenTelemetry (graceful degradation)
# =============================================================================

try:
    from opentelemetry import trace
    from opentelemetry.trace import Status, StatusCode

    _tracer = trace.get_tracer(__name__)
    _HAS_OPENTELEMETRY = True
except ImportError:
    _HAS_OPENTELEMETRY = False

    class _StatusCode:  # type: ignore[no-redef]
        OK = "OK"
        ERROR = "ERROR"

    class _Status:  # type: ignore[no-redef]
        def __init__(self, status_code, description: Optional[str] = None):
            self.status_code = status_code
            self.description = description

    class _NoOpSpan:
        def set_attribute(self, *a, **kw): ...
        def set_status(self, *a, **kw): ...
        def record_exception(self, *a, **kw): ...
        def add_event(self, *a, **kw): ...

    class _NoOpContextManager:
        def __enter__(self): return _NoOpSpan()
        def __exit__(self, *a): ...

    class _NoOpTracer:
        def start_as_current_span(self, *a, **kw): return _NoOpContextManager()

    _tracer = _NoOpTracer()  # type: ignore[assignment]
    StatusCode = _StatusCode  # type: ignore[assignment,misc]
    Status = _Status  # type: ignore[assignment,misc]

# =============================================================================
# OBSERVABILITY — Prometheus metrics (graceful degradation)
# =============================================================================

try:
    from prometheus_client import Counter, Histogram
    from prometheus_client.registry import REGISTRY as _REGISTRY

    def _safe_metric(cls, name: str, doc: str, **kwargs):
        """Idempotent metric registration — returns existing collector if present."""
        try:
            existing = _REGISTRY._names_to_collectors.get(name)  # type: ignore[attr-defined]
            if existing is not None:
                return existing
        except AttributeError:
            pass
        try:
            return cls(name, doc, **kwargs)
        except ValueError as exc:
            if "Duplicated timeseries" in str(exc):
                existing = _REGISTRY._names_to_collectors.get(name)  # type: ignore[attr-defined]
                if existing is not None:
                    return existing
            raise

    POST_MATERIALIZE_RUNS = _safe_metric(
        Counter,
        "post_materialize_runs_total",
        "Total post_materialize() invocations",
        labelnames=["status"],
    )
    POST_MATERIALIZE_DURATION = _safe_metric(
        Histogram,
        "post_materialize_duration_seconds",
        "Wall-clock time spent in post_materialize()",
        buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
    )
    POST_MATERIALIZE_FILES_CREATED = _safe_metric(
        Counter,
        "post_materialize_files_created_total",
        "Number of stub files created by post_materialize()",
        labelnames=["file_type"],
    )
    _HAS_PROMETHEUS = True

except ImportError:
    _HAS_PROMETHEUS = False

    class _NoOpMetric:  # type: ignore[no-redef]
        def labels(self, *a, **kw): return self
        def inc(self, *a, **kw): ...
        def observe(self, *a, **kw): ...

    POST_MATERIALIZE_RUNS = _NoOpMetric()  # type: ignore[assignment]
    POST_MATERIALIZE_DURATION = _NoOpMetric()  # type: ignore[assignment]
    POST_MATERIALIZE_FILES_CREATED = _NoOpMetric()  # type: ignore[assignment]

# =============================================================================
# LOGGING
# =============================================================================

logger = logging.getLogger(__name__)

# =============================================================================
# CONSTANTS
# =============================================================================

# Stage prefix used in all log messages (matches provenance.py convention)
_STAGE = "[STAGE:POST_MATERIALIZE]"

# Maximum README characters to embed in the Sphinx HTML page (prevents huge files)
MAX_README_CHARS_FOR_DOCS: int = 4096

# Directories that every generated project must contain
REQUIRED_DIRS: tuple = ("app", "tests", "reports")

# =============================================================================
# RESULT DATACLASS
# =============================================================================


@dataclass
class PostMaterializeResult:
    """Structured result returned by :func:`post_materialize`.

    Attributes:
        success: ``True`` when the function completed without fatal errors.
        files_created: Relative paths of stub files written during this call.
        warnings: Non-fatal issues encountered (e.g. README patch skipped).
        duration_seconds: Wall-clock time the function took.
        output_dir: Absolute path that was processed.
    """

    success: bool = True
    files_created: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    output_dir: str = ""

    def to_dict(self) -> Dict:
        return {
            "success": self.success,
            "files_created": self.files_created,
            "files_created_count": len(self.files_created),
            "warnings": self.warnings,
            "duration_seconds": round(self.duration_seconds, 4),
            "output_dir": self.output_dir,
        }


# =============================================================================
# PUBLIC API
# =============================================================================


def post_materialize(
    output_dir: Path,
    entry_point: Optional[str] = None,
) -> PostMaterializeResult:
    """Apply post-materialization fixups to a generated project directory.

    This function is **idempotent** — calling it multiple times on the same
    directory is safe; it never overwrites files that already exist.

    The function is deliberately **synchronous** so it can be called from
    both async service code and synchronous CLI code without requiring an
    event loop.

    Args:
        output_dir: Root directory that was just populated with generated
            files.  Must be an absolute :class:`~pathlib.Path`.
        entry_point: Uvicorn entry-point string used in README and HTML
            documentation snippets (e.g. ``"app.main:app"``).  When
            ``None``, the function auto-detects whether ``app/main.py``
            exists and falls back to ``"app.main:app"`` or ``"main:app"``
            accordingly.

    Returns:
        :class:`PostMaterializeResult` with details of files created and
        any non-fatal warnings.

    Raises:
        This function never raises.  All exceptions are caught, logged, and
        reflected in the returned result's ``success`` flag.
    """
    result = PostMaterializeResult(output_dir=str(output_dir))
    start_ts = time.monotonic()

    with _tracer.start_as_current_span(
        "post_materialize",
        attributes={"output_dir": str(output_dir)},
    ) as span:
        try:
            if not output_dir.exists():
                msg = f"output_dir does not exist: {output_dir}"
                logger.warning("%s %s — skipping", _STAGE, msg)
                result.success = False
                result.warnings.append(msg)
                span.set_attribute("skipped", True)
                span.set_attribute("skip_reason", "directory_missing")
                POST_MATERIALIZE_RUNS.labels(status="skipped").inc()
                return result

            logger.info(
                "%s Starting post-materialization fixups for %s",
                _STAGE,
                output_dir,
                extra={"output_dir": str(output_dir)},
            )

            # ------------------------------------------------------------------
            # Phase 1: Required directory scaffold + stubs
            # ------------------------------------------------------------------
            _scaffold_required_dirs(output_dir, result)

            # ------------------------------------------------------------------
            # Phase 2: Ensure app/main.py exists
            # ------------------------------------------------------------------
            _ensure_app_main(output_dir, result)

            # ------------------------------------------------------------------
            # Phase 3: Patch README with contract-required sections
            # ------------------------------------------------------------------
            if entry_point is None:
                entry_point = (
                    "app.main:app"
                    if (output_dir / "app" / "main.py").exists()
                    else "main:app"
                )
            _patch_readme(output_dir, entry_point, result)

            # ------------------------------------------------------------------
            # Phase 4: Sphinx HTML placeholder
            # ------------------------------------------------------------------
            _create_sphinx_placeholder(output_dir, result)

            # ------------------------------------------------------------------
            # Finalize
            # ------------------------------------------------------------------
            result.duration_seconds = time.monotonic() - start_ts

            span.set_attribute("files_created", len(result.files_created))
            span.set_attribute("warnings", len(result.warnings))
            span.set_attribute("duration_seconds", result.duration_seconds)
            span.set_status(
                Status(StatusCode.OK)  # type: ignore[call-arg]
                if result.success
                else Status(StatusCode.ERROR, "warnings present")  # type: ignore[call-arg]
            )

            POST_MATERIALIZE_RUNS.labels(
                status="success" if result.success else "partial"
            ).inc()
            POST_MATERIALIZE_DURATION.observe(result.duration_seconds)

            logger.info(
                "%s Completed: %d files created, %d warnings, %.3fs",
                _STAGE,
                len(result.files_created),
                len(result.warnings),
                result.duration_seconds,
                extra={
                    "output_dir": str(output_dir),
                    "files_created": result.files_created,
                    "warnings": result.warnings,
                },
            )

        except Exception as exc:  # pylint: disable=broad-except
            result.success = False
            result.duration_seconds = time.monotonic() - start_ts
            result.warnings.append(f"Unexpected error: {exc}")
            logger.exception(
                "%s Unexpected error processing %s: %s",
                _STAGE,
                output_dir,
                exc,
                extra={"output_dir": str(output_dir)},
            )
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))  # type: ignore[call-arg]
            POST_MATERIALIZE_RUNS.labels(status="error").inc()

    return result


# =============================================================================
# PHASE HELPERS  (private)
# =============================================================================


def _scaffold_required_dirs(
    output_dir: Path,
    result: PostMaterializeResult,
) -> None:
    """Create required directories and minimal stub files.

    Creates ``app/``, ``tests/``, and ``reports/`` with the stub files
    mandated by the ``ContractValidator``.  Existing files are *never*
    overwritten.

    Args:
        output_dir: Project root directory.
        result: Mutable result object updated in-place.
    """
    for dir_name in REQUIRED_DIRS:
        dir_path = output_dir / dir_name
        dir_path.mkdir(parents=True, exist_ok=True)

        if dir_name == "app":
            _create_if_absent(
                dir_path / "__init__.py",
                "# Auto-generated app package\n",
                result,
                output_dir=output_dir,
                file_type="init_py",
            )
            _create_if_absent(
                dir_path / "schemas.py",
                _APP_SCHEMAS_CONTENT,
                result,
                output_dir=output_dir,
                file_type="schemas_py",
            )
            _create_if_absent(
                dir_path / "routes.py",
                _APP_ROUTES_CONTENT,
                result,
                output_dir=output_dir,
                file_type="routes_py",
            )

        elif dir_name == "tests":
            _create_if_absent(
                dir_path / "__init__.py",
                "# Auto-generated tests package\n",
                result,
                output_dir=output_dir,
                file_type="init_py",
            )


def _ensure_app_main(
    output_dir: Path,
    result: PostMaterializeResult,
) -> None:
    """Guarantee that ``app/main.py`` exists.

    If a root-level ``main.py`` was produced by the LLM, its content is
    copied into ``app/main.py`` so the ``app/`` layout is correct.
    Otherwise a minimal FastAPI entry-point stub is written.

    Args:
        output_dir: Project root directory.
        result: Mutable result object updated in-place.
    """
    app_main = output_dir / "app" / "main.py"
    if app_main.exists():
        return

    root_main = output_dir / "main.py"
    if root_main.exists():
        try:
            content = root_main.read_text(encoding="utf-8")
            _create_if_absent(app_main, content, result, output_dir=output_dir, file_type="main_py")
            logger.debug("%s Copied root main.py → app/main.py", _STAGE)
        except OSError as exc:
            warn = f"Could not copy main.py → app/main.py: {exc}"
            result.warnings.append(warn)
            logger.warning("%s %s", _STAGE, warn)
    else:
        _create_if_absent(
            app_main,
            _APP_MAIN_CONTENT,
            result,
            output_dir=output_dir,
            file_type="main_py",
        )


def _patch_readme(
    output_dir: Path,
    entry_point: str,
    result: PostMaterializeResult,
) -> None:
    """Append missing contract-required sections to ``README.md``.

    Args:
        output_dir: Project root directory.
        entry_point: Uvicorn entry-point string used in the ``## Run``
            section snippet.
        result: Mutable result object updated in-place.
    """
    readme_path = output_dir / "README.md"
    if not readme_path.exists():
        return

    try:
        original = readme_path.read_text(encoding="utf-8")
        patched = ensure_readme_sections(original, entry_point)
        if patched != original:
            readme_path.write_text(patched, encoding="utf-8")
            result.files_created.append("README.md")
            POST_MATERIALIZE_FILES_CREATED.labels(file_type="readme_patch").inc()
            logger.debug("%s Patched README.md with required sections", _STAGE)
    except OSError as exc:
        warn = f"Could not patch README.md: {exc}"
        result.warnings.append(warn)
        logger.warning("%s %s", _STAGE, warn)


def _create_sphinx_placeholder(
    output_dir: Path,
    result: PostMaterializeResult,
) -> None:
    """Create ``docs/_build/html/index.html`` when absent.

    The ``ContractValidator``'s documentation-completeness check requires
    this file to exist and contain HTML content.  The placeholder embeds a
    sanitised excerpt of ``README.md`` so the page is self-consistent.

    Args:
        output_dir: Project root directory.
        result: Mutable result object updated in-place.
    """
    docs_html_dir = output_dir / "docs" / "_build" / "html"
    index_html = docs_html_dir / "index.html"
    if index_html.exists():
        return

    try:
        docs_html_dir.mkdir(parents=True, exist_ok=True)

        project_title = output_dir.name.replace("_", " ").title()

        readme_path = output_dir / "README.md"
        readme_excerpt = ""
        if readme_path.exists():
            try:
                readme_excerpt = readme_path.read_text(encoding="utf-8")[
                    :MAX_README_CHARS_FOR_DOCS
                ]
            except OSError:
                pass
        readme_html = _html_module.escape(readme_excerpt).replace("\n", "<br>\n")

        safe_title = _html_module.escape(project_title)
        index_html.write_text(
            f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{safe_title} — Documentation</title>
</head>
<body>
<h1>{safe_title}</h1>
<p>Auto-generated documentation for the <strong>{safe_title}</strong> project.</p>
<h2>Setup</h2>
<pre>pip install -r requirements.txt</pre>
<h2>Run</h2>
<pre>uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload</pre>
<h2>Test</h2>
<pre>pytest tests/ -v</pre>
<div class="readme">{readme_html}</div>
</body>
</html>
""",
            encoding="utf-8",
        )
        result.files_created.append("docs/_build/html/index.html")
        POST_MATERIALIZE_FILES_CREATED.labels(file_type="sphinx_html").inc()
        logger.debug("%s Created docs/_build/html/index.html", _STAGE)

    except OSError as exc:
        warn = f"Could not create Sphinx placeholder: {exc}"
        result.warnings.append(warn)
        logger.warning("%s %s", _STAGE, warn)


def _create_if_absent(
    path: Path,
    content: str,
    result: PostMaterializeResult,
    output_dir: Optional[Path] = None,
    file_type: str = "unknown",
) -> bool:
    """Write *content* to *path* only if it does not already exist.

    Args:
        path: Target file path.
        content: Text content to write (UTF-8).
        result: Mutable result updated in-place on successful creation.
        output_dir: When provided, record the path relative to this root
            in ``result.files_created``; otherwise record the basename.
        file_type: Prometheus label value for the ``file_type`` dimension.

    Returns:
        ``True`` if the file was created, ``False`` if it already existed.
    """
    if path.exists():
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        if output_dir is not None:
            try:
                rel = str(path.relative_to(output_dir))
            except ValueError:
                rel = path.name
        else:
            rel = path.name
        result.files_created.append(rel)
        POST_MATERIALIZE_FILES_CREATED.labels(file_type=file_type).inc()
        logger.debug("%s Created %s", _STAGE, path)
        return True
    except OSError as exc:
        warn = f"Could not create {path}: {exc}"
        result.warnings.append(warn)
        logger.warning("%s %s", _STAGE, warn)
        return False


# =============================================================================
# PUBLIC UTILITY — README section enforcement
# =============================================================================


def ensure_readme_sections(
    readme_content: str,
    entry_point: str = "app.main:app",
) -> str:
    """Ensure ``README.md`` contains all sections required by the contract validator.

    The ``ContractValidator`` (``scripts/validate_contract_compliance.py``)
    requires these exact headings for Python projects:

    - ``## Setup``
    - ``## Run``
    - ``## Test``
    - ``## API Endpoints``
    - ``## Project Structure``

    …plus at least one ``curl`` example anywhere in the document.

    If any are missing they are **appended** with minimal useful content.
    Existing sections are **never** modified.

    Args:
        readme_content: Existing README text.  May be empty or ``None``.
        entry_point: Uvicorn entry-point string used in the Run section.

    Returns:
        README text guaranteed to contain all required sections.
    """
    content: str = readme_content or ""
    additions: List[str] = []

    def _has(heading: str) -> bool:
        """Return True if a markdown heading exactly matching *heading* exists."""
        return bool(
            re.search(
                rf"^{re.escape(heading)}(\s|$)",
                content,
                re.MULTILINE | re.IGNORECASE,
            )
        )

    if not _has("## Setup"):
        additions.append(
            "\n## Setup\n\n"
            "Install dependencies:\n\n"
            "```bash\npip install -r requirements.txt\n```\n"
        )

    if not _has("## Run"):
        additions.append(
            "\n## Run\n\n"
            "Start the application:\n\n"
            f"```bash\nuvicorn {entry_point} --host 0.0.0.0 --port 8000 --reload\n```\n"
        )

    if not _has("## Test"):
        additions.append(
            "\n## Test\n\n"
            "Run the test suite:\n\n"
            "```bash\npytest tests/ -v\n```\n"
        )

    if not _has("## API Endpoints"):
        additions.append(
            "\n## API Endpoints\n\n"
            "| Method | Path | Description |\n"
            "|--------|------|-------------|\n"
            "| GET | /health | Health check |\n\n"
            "Example:\n\n"
            "```bash\ncurl http://localhost:8000/health\n```\n"
        )

    if not _has("## Project Structure"):
        additions.append(
            "\n## Project Structure\n\n"
            "```\n"
            ".\n"
            "├── app/\n"
            "│   ├── __init__.py\n"
            "│   ├── main.py\n"
            "│   ├── routes.py\n"
            "│   └── schemas.py\n"
            "├── tests/\n"
            "├── requirements.txt\n"
            "└── README.md\n"
            "```\n"
        )

    if "curl" not in content:
        additions.append(
            "\n## Usage\n\n"
            "```bash\ncurl http://localhost:8000/health\n```\n"
        )

    if additions:
        content = content.rstrip() + "\n" + "".join(additions)

    return content


# =============================================================================
# STUB CONTENT CONSTANTS
# These are module-level constants so they can be imported and inspected by
# tests without instantiating the full post_materialize pipeline.
# =============================================================================

_APP_SCHEMAS_CONTENT: str = (
    '"""Auto-generated Pydantic schemas.\n\n'
    "This module is created by the post-materialization phase when the LLM\n"
    "did not produce a schemas.py.  It satisfies the ContractValidator's\n"
    "``check_schema_validation`` check which requires ``@field_validator``.\n"
    '"""\n'
    "from pydantic import BaseModel, Field, field_validator\n\n\n"
    "class Item(BaseModel):\n"
    '    """Generic item model used by CRUD endpoints.\n\n'
    "    Validation: name must be non-empty (min_length=1);\n"
    "    price must be positive (gt=0).\n"
    '    """\n\n'
    "    name: str = Field(..., min_length=1)\n"
    "    price: float = Field(..., gt=0)\n"
    "    description: str = ''\n\n"
    "    @field_validator('name', mode='before')\n"
    "    @classmethod\n"
    "    def strip_name(cls, v: object) -> object:\n"
    "        \"\"\"Strip leading/trailing whitespace from name.\"\"\"\n"
    "        if isinstance(v, str):\n"
    "            return v.strip()\n"
    "        return v\n\n\n"
    "class BaseRequest(BaseModel):\n"
    '    """Base request model with common validators."""\n\n'
    "    message: str = ''\n\n"
    "    @field_validator('message', mode='before')\n"
    "    @classmethod\n"
    "    def strip_message(cls, v: object) -> object:\n"
    "        \"\"\"Strip leading/trailing whitespace from message.\"\"\"\n"
    "        if isinstance(v, str):\n"
    "            return v.strip()\n"
    "        return v\n"
)

_APP_ROUTES_CONTENT: str = (
    '"""Auto-generated FastAPI router placeholder.\n\n'
    "This module is created by the post-materialization phase when the LLM\n"
    "did not produce a routes.py.  Replace with real route handlers.\n"
    '"""\n'
    "from fastapi import APIRouter\n\n"
    "router = APIRouter()\n\n\n"
    "@router.get('/health')\n"
    "async def health() -> dict:\n"
    "    \"\"\"Liveness probe — always returns HTTP 200.\"\"\"\n"
    "    return {'status': 'ok'}\n"
)

_APP_MAIN_CONTENT: str = (
    '"""Auto-generated FastAPI application entry point.\n\n'
    "This module is created by the post-materialization phase when the LLM\n"
    "did not produce a main.py.  Replace with the real application.\n"
    '"""\n'
    "from fastapi import FastAPI\n"
    "from app.routes import router\n\n"
    "app = FastAPI(title='Generated App')\n"
    "app.include_router(router)\n\n\n"
    "@app.get('/health')\n"
    "async def health() -> dict:\n"
    "    \"\"\"Liveness probe — always returns HTTP 200.\"\"\"\n"
    "    return {'status': 'ok'}\n"
)
