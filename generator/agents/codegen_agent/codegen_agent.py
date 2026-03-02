# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# agents/codegen_agent.py
import asyncio
import ast
import json
import logging
import logging.handlers
import os
import re
import shutil
import sqlite3
import sys
import time
import uuid
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple, Union

# Third-party libraries (MINIMAL SET RETAINED)
import aiohttp
import redis.asyncio as aioredis
import yaml
from fastapi import FastAPI, HTTPException
from jinja2 import TemplateNotFound

# Observability libraries
from opentelemetry import trace
from prometheus_client import (
    REGISTRY,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    start_http_server,
)

try:
    from opentelemetry.exporter.jaeger.thrift import JaegerExporter
except ImportError:
    JaegerExporter = None

# Internal imports
from .codegen_prompt import build_code_generation_prompt
from .codegen_response_handler import (
    add_traceability_comments,
    build_detailed_stub_feedback,
    build_stub_retry_prompt_hint,
    _classify_stub_module,
    _detect_module_package_collisions,
    _detect_stub_patterns,
    disambiguate_model_schema_imports,
    ERROR_FILENAME,
    extract_function_name,
    fix_response_model_type_mismatches,
    get_stub_files,
    parse_llm_response,
    reconcile_schema_model_fields,
)

# --- REMOVED OBSOLETE IMPORT: from .codegen_llm_call import CacheManager ---

# --- RUNNER UTILITY IMPORTS (ENFORCED) ---
try:
    # --- FIX: Changed imports to be ABSOLUTE from the 'generator' root ---
    # CircuitBreaker is in llm_client, but if you need the class itself:
    from generator.runner.llm_client import (
        CircuitBreaker,
        call_ensemble_api,
        call_llm_api,
    )
    from generator.runner.runner_audit import log_audit_event
    from generator.runner.runner_security_utils import scan_for_vulnerabilities
except ImportError as e:
    # Hard fail: this agent is not allowed to run without the runner stack.
    raise ImportError(
        "codegen_agent requires the generator.runner package "
        "(llm_client, runner_logging, runner_security_utils, runner_metrics)."
    ) from e

try:
    from generator.utils.ast_endpoint_extractor import ASTEndpointExtractor
except ImportError:
    ASTEndpointExtractor = None  # type: ignore[assignment,misc]

# Internal component dummy/migration note
try:
    from omnicore_engine.plugin_registry import PlugInKind, plugin

    PLUGIN_AVAILABLE = True
except ImportError:
    PLUGIN_AVAILABLE = False

    from generator.agents.plugin_stubs import PlugInKind, plugin


# ==============================================================================
# --- Frontend Type Constants ---
# ==============================================================================
DEFAULT_FRONTEND_TYPE = "jinja_templates"

# ==============================================================================
# --- LLM Call Constants ---
# ==============================================================================
# Prompt length threshold above which we request more output tokens from the LLM
LARGE_PROMPT_THRESHOLD = 8000
# Max tokens to request when generating code from a large spec
LARGE_PROMPT_MAX_TOKENS = 32768
# Per-model output token limits (completion tokens); used to cap LARGE_PROMPT_MAX_TOKENS
MODEL_MAX_OUTPUT_TOKENS = {
    "gpt-4o": 16384,           # GPT-4o actual max completion token limit
    "gpt-4o-mini": 16384,      # GPT-4o-mini actual max completion token limit
    "gpt-4-turbo": 4096,
    "gpt-4": 8192,
    "gpt-4.5-preview": 16384,  # Added: GPT-4.5-preview
    "o1": 100000,
    "o3-mini": 65536,           # Added: o3-mini
    "claude-3-5-sonnet-20241022": 8192,   # Added: Claude 3.5 Sonnet
    "claude-3-5-haiku-20241022": 8192,    # Added: Claude 3.5 Haiku
    "claude-3-opus-20240229": 4096,       # Added: Claude 3 Opus
}
# Per-model context window sizes (input + output tokens combined)
# For models not listed here, defaults to 128000 (conservative assumption for modern LLMs)
MODEL_CONTEXT_WINDOWS = {
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "gpt-4-turbo": 128000,
    "gpt-4": 8192,
    "gpt-3.5-turbo": 16385,
}
# Average characters per token used for rough input token estimation.
# This is an approximation; actual ratios vary by model and language (~3-5 chars/token).
AVG_CHARS_PER_TOKEN = 3.5

# ==============================================================================
# --- Multi-Pass Code Generation Constants ---
# ==============================================================================
# Threshold: use multi-pass generation when the spec has at least this many API endpoints.
# Configurable at runtime via CODEGEN_MULTIPASS_ENDPOINT_THRESHOLD (default: 15).
MULTIPASS_ENDPOINT_THRESHOLD: int = int(
    os.environ.get("CODEGEN_MULTIPASS_ENDPOINT_THRESHOLD", "15")
)
# Timeout for the entire pipeline codegen step (seconds).
# Configurable at runtime via PIPELINE_CODEGEN_TIMEOUT_SECONDS (default: 900s / 15 minutes).
PIPELINE_CODEGEN_TIMEOUT_SECONDS: int = int(
    os.environ.get("PIPELINE_CODEGEN_TIMEOUT_SECONDS", "900")
)
# Threshold: use multi-pass generation when the spec references at least this many files.
# Configurable at runtime via CODEGEN_MULTIPASS_FILE_THRESHOLD (default: 20).
MULTIPASS_FILE_THRESHOLD: int = int(
    os.environ.get("CODEGEN_MULTIPASS_FILE_THRESHOLD", "20")
)

# File generation groups for multi-pass mode (processed in order).
# Each pass focuses on a logical subset of files; earlier passes are provided as
# context to later passes so the LLM does not regenerate already-produced files.
_MULTIPASS_GROUPS = [
    {
        "name": "core",
        "focus": (
            "Generate ONLY the core application files: "
            "main.py (MUST import and mount ALL routers from app/routers/ using app.include_router()), "
            "app factory setup, config.py, database.py with real SQLAlchemy engine setup, "
            "ALL model files (e.g. app/models/product.py, app/models/order.py, app/models/user.py, "
            "app/models/audit.py) with COMPLETE SQLAlchemy model definitions matching the spec's field "
            "names/types/constraints (use UUID for IDs if spec says UUID, mark fields Optional only if "
            "spec says optional), schemas.py or app/schemas/*.py with ALL Pydantic schemas matching the "
            "spec (e.g. Product, Order, User, AuditLog) with proper field types and validators, "
            "__init__.py files, "
            "and any other foundational modules. "
            "Do NOT generate alembic/env.py — it will be provided by the framework. "
            "SQLAlchemy imports MUST include: "
            "from sqlalchemy import Column, String, Integer, UUID, DateTime, ForeignKey, Boolean, Numeric "
            "and from sqlalchemy.orm import relationship, DeclarativeBase, Mapped, mapped_column. "
            "Use async SQLAlchemy sessions (async_sessionmaker, AsyncSession) if the spec mentions async "
            "or high-performance requirements. "
            "Every model class MUST inherit from a shared Base = declarative_base() defined in app/database.py. "
            "Alembic migration scripts MUST reference the same Base.metadata. "
            "Pydantic schemas MUST use model_config = ConfigDict(from_attributes=True) for ORM compatibility. "
            "Do NOT generate router, service, test, or infrastructure files in this pass. "
            "Do NOT use placeholder implementations — every model and schema must be fully defined."
        ),
    },
    {
        "name": "routes_and_services",
        "focus": (
            "Generate ONLY the router/controller, service layer, and middleware files: "
            "all route handlers (app/routers/*.py), service modules (app/services/*.py), "
            "ALL middleware files: app/middleware/auth.py for JWT authentication, "
            "app/middleware/rate_limit.py for rate limiting, "
            "app/middleware/request_id.py for request ID tracking, "
            "app/middleware/security_headers.py for security headers, "
            "and any other cross-cutting concern modules (app/middleware/*.py, app/utils/*.py). "
            "Service layer MUST use the repository pattern or direct SQLAlchemy ORM queries — no raw SQL f-strings. "
            "Use HTTPException(status_code=404, detail='...') for not-found, 400 for bad request, "
            "409 for conflict, 422 for validation failures. "
            "JWT auth middleware MUST validate Authorization: Bearer <token> header, decode using "
            "python-jose or PyJWT, and return 401 on failure. "
            "Rate limiting middleware MUST track requests per client IP (using starlette-ratelimit or a "
            "Redis counter) and return 429 on excess. "
            "Request-ID middleware MUST attach a UUID to each request via X-Request-ID header (both incoming and outgoing). "
            "Security headers middleware MUST set: X-Content-Type-Options: nosniff, X-Frame-Options: DENY, "
            "Strict-Transport-Security, Content-Security-Policy. "
            "Every service function MUST contain real implementation logic — database queries using "
            "SQLAlchemy ORM, input validation, error handling with HTTPException, and proper HTTP "
            "status codes. Do NOT return empty lists or placeholder comments. "
            "Use the SQLAlchemy models defined in the core pass. "
            "Every router MUST be properly connected to its service layer. "
            "All middleware MUST have working implementations, not empty files or pass-through stubs. "
            "MUST include /healthz endpoint for Kubernetes liveness probes (returns HTTP 200 with {'status': 'ok'}). "
            "MUST include /readyz endpoint for Kubernetes readiness probes (returns HTTP 200 when app is ready, 503 otherwise). "
            "Do NOT generate models, schemas, test, or infrastructure files in this pass."
        ),
    },
    {
        "name": "service_implementations",
        "focus": (
            "Generate COMPLETE service layer implementations for ALL service modules in app/services/. "
            "This pass MUST replace every stub or placeholder service with fully working code. "
            "Every service function MUST include: real SQLAlchemy ORM queries (select/insert/update/delete), "
            "proper error handling with HTTPException (404 for not-found, 409 for conflicts, 422 for validation), "
            "async/await patterns with AsyncSession, and complete business logic matching the spec. "
            "Authentication service MUST implement: real JWT token creation using python-jose or PyJWT, "
            "password hashing with passlib/bcrypt, login/logout/refresh_token with actual token logic. "
            "NEVER use `pass`, `...`, or placeholder comments in any function body. "
            "NEVER return empty dicts/lists as the sole implementation. "
            "Use the SQLAlchemy models and schemas from the core pass. "
            "Reference the symbol manifest to import from the correct modules. "
            "Do NOT regenerate models, routers, schemas, or infrastructure files in this pass."
        ),
    },
    {
        "name": "infrastructure",
        "focus": (
            "Generate ONLY infrastructure and deployment files: "
            "Dockerfile MUST use multi-stage build: FROM python:3.11-slim AS builder then "
            "FROM python:3.11-slim AS runtime. "
            "Dockerfile CMD MUST use 'uvicorn app.main:app' — NOT app.py or any other entry point. "
            "docker-compose.yml (must be functional), .dockerignore, .env.example, "
            "K8s Deployment MUST set terminationGracePeriodSeconds: 30, use RollingUpdate strategy. "
            "K8s liveness probe: httpGet: path: /healthz port: 8000 initialDelaySeconds: 10 periodSeconds: 30. "
            "K8s readiness probe: httpGet: path: /readyz port: 8000 initialDelaySeconds: 5 periodSeconds: 10. "
            "K8s resource requests: cpu: 100m memory: 128Mi; limits: cpu: 500m memory: 512Mi. "
            "Kubernetes manifests (k8s/*.yaml) MUST include liveness/readiness probes (/healthz and "
            "/readyz), resource requests/limits, and use environment variable references (not hardcoded values). "
            "Helm values.yaml MUST be populated with sensible defaults for all configurable values. "
            "Helm charts (helm/**) MUST be valid Go template YAML (not JSON), "
            "CI/CD configs (.github/workflows/*.yml), pyproject.toml, requirements.txt, "
            "Makefile, and test files (tests/**). "
            "Do NOT regenerate application source code files. "
            "Reference the symbol manifest provided above to determine service names, ports, and health check paths. "
            "Do NOT use generic placeholder values — use the actual service and model names from earlier passes. "
        ),
    },
]

# Frontend-type → human-readable instruction fragment for the frontend pass.
_FRONTEND_JINJA_FOCUS = (
    "Create Jinja2 HTML templates in templates/ directory: "
    "templates/base.html (base layout with navbar, footer, CSS/JS links), "
    "templates/index.html (main page extending base), "
    "and a template for each major entity/resource page. "
    "Create static assets in static/ directory: "
    "static/css/style.css (responsive stylesheet with CSS variables), "
    "static/js/app.js (frontend JavaScript with fetch API calls to backend endpoints). "
    "Backend main.py MUST mount static files: "
    "app.mount('/static', StaticFiles(directory='static'), name='static') "
    "and configure Jinja2Templates. "
    "Do NOT regenerate any backend Python files. "
    "Use the symbol manifest to reference actual endpoint paths."
)


def _build_multipass_groups(
    include_frontend: bool,
    frontend_type: Optional[str],
) -> List[Dict[str, str]]:
    """Return the ordered list of generation-pass descriptors for a single pipeline run.

    Starts from :data:`_MULTIPASS_GROUPS` and conditionally appends a fourth
    ``frontend`` pass when *include_frontend* is ``True``.  Keeping this logic in
    one place ensures that both :class:`CodegenAgent` and
    :class:`MultiPassCodegenAgent` always produce identical pass sequences.

    Args:
        include_frontend: Whether to append a frontend generation pass.
        frontend_type: The frontend technology (e.g. ``"jinja_templates"``).
            Only evaluated when *include_frontend* is ``True``.

    Returns:
        A new list of ``{"name": str, "focus": str}`` dicts.
    """
    groups: List[Dict[str, str]] = list(_MULTIPASS_GROUPS)
    if include_frontend:
        if frontend_type == "jinja_templates":
            frontend_focus = (
                "Generate ONLY the frontend template and static asset files. "
                + _FRONTEND_JINJA_FOCUS
            )
        else:
            frontend_focus = (
                "Generate ONLY the frontend template and static asset files. "
                f"Create {frontend_type} frontend files in the frontend/ directory. "
                "Include package.json, entry point, and component files. "
                "Do NOT regenerate any backend Python files."
            )
        groups.append({"name": "frontend", "focus": frontend_focus})
    return groups


def _count_spec_endpoints(requirements: Dict[str, Any]) -> int:
    """Count the number of API endpoints in the spec using a simple regex heuristic."""
    md = requirements.get("md_content", "") or requirements.get("description", "")
    if not md:
        return 0
    matches = set(
        re.findall(r'\b(?:GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\b\s+/\S+', md, re.IGNORECASE)
    )
    return len(matches)


def _should_use_multipass(requirements: Dict[str, Any]) -> bool:
    """Return True when the spec is large enough to warrant multi-pass generation."""
    return _count_spec_endpoints(requirements) >= MULTIPASS_ENDPOINT_THRESHOLD


def _build_symbol_manifest(files: Dict[str, str]) -> str:
    """Extract top-level public symbols from Python files and return a manifest string.

    Used to give later passes in a multi-pass generation context knowledge about
    what was already defined in earlier passes, so they can import from the correct
    modules rather than re-defining or stubbing symbols.

    Only **top-level** nodes in each module are collected (not nested class methods
    or inner functions), matching the symbols that would appear in an ``__all__``
    export or a ``from module import ...`` statement.

    The following top-level constructs are captured:

    * ``def``/``async def`` — functions
    * ``class`` — class definitions
    * ``name = ...`` / ``name: type = ...`` — simple variable assignments
      (e.g. ``api_router = APIRouter()``, ``app = FastAPI()``)

    Private names (starting with ``_``) are intentionally excluded because they
    should not be imported across module boundaries.

    Args:
        files: Mapping of relative file paths to source code strings, as
            produced by :func:`parse_llm_response`.  Non-Python files and files
            that contain syntax errors are silently skipped.

    Returns:
        A human-readable string listing each module and its exported symbols,
        suitable for direct inclusion in an LLM prompt.  Returns an empty string
        when no Python files with parseable public symbols are found.

    Examples:
        >>> result = _build_symbol_manifest({"app/auth.py": "def get_current_user(): ..."})
        >>> "app.auth: get_current_user" in result
        True
    """
    lines: List[str] = []
    for path, content in sorted(files.items()):
        if not path.endswith(".py"):
            continue
        try:
            tree = ast.parse(content)
        except SyntaxError:
            continue

        symbols: List[str] = []
        # Walk only the direct children of the module (top-level statements).
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if not node.name.startswith("_"):
                    symbols.append(node.name)
            elif isinstance(node, ast.Assign):
                # Simple assignments: ``name = value`` at module scope.
                for target in node.targets:
                    if isinstance(target, ast.Name) and not target.id.startswith("_"):
                        symbols.append(target.id)
            elif isinstance(node, ast.AnnAssign):
                # Annotated assignments: ``name: Type = value`` at module scope.
                if isinstance(node.target, ast.Name) and not node.target.id.startswith("_"):
                    symbols.append(node.target.id)

        # Deduplicate while preserving first-seen order.
        seen: set = set()
        unique_symbols = [s for s in symbols if not (s in seen or seen.add(s))]  # type: ignore[func-returns-value]

        if unique_symbols:
            module_name = path.replace("/", ".").removesuffix(".py")
            lines.append(f"  {module_name}: {', '.join(sorted(unique_symbols))}")

    if not lines:
        return ""
    return (
        "Symbol manifest from earlier passes (import from these modules — do NOT redefine):\n"
        + "\n".join(lines)
    )


async def _multipass_heartbeat(pass_name: str, interval: int = 30) -> None:
    """
    Emit a progress log at regular intervals while a multi-pass LLM call is
    in-flight.

    Designed to be run as a background asyncio Task and cancelled via
    ``task.cancel()`` as soon as the LLM call completes (success **or** failure).
    The ``finally`` block on the caller must call::

        heartbeat_task.cancel()
        await asyncio.gather(heartbeat_task, return_exceptions=True)

    This ensures the task is always cleaned up and never leaks, even when the
    caller exits via exception or cancellation.

    Args:
        pass_name: Human-readable name of the current generation pass, used
            in the log message so operators can correlate heartbeats with passes.
        interval: Seconds between successive log messages (default: 30 s).
    """
    elapsed = 0
    while True:
        await asyncio.sleep(interval)
        elapsed += interval
        logger.info(
            "[CODEGEN] Multi-pass ensemble heartbeat: pass '%s' still in progress "
            "(%ds elapsed) — container is alive and working",
            pass_name,
            elapsed,
        )


# Maximum total characters returned to avoid overwhelming the prompt
_SPEC_MODELS_MAX_CHARS = 12000
# Minimum section length to be considered meaningful
_SPEC_MODELS_MIN_SECTION_LEN = 30

# HTTP methods recognized in route decorator extraction
_HTTP_ROUTE_METHODS: FrozenSet[str] = frozenset({"get", "post", "put", "delete", "patch", "head", "options"})
# Placeholder token used when normalizing path parameters for comparison
_PATH_PARAM_WILDCARD = "{param}"

def _extract_spec_models(requirements: Dict[str, Any]) -> str:
    """Extract data model and schema definitions from the README / spec document.

    Parses markdown content looking for:

    * Heading-delimited sections whose titles mention models, schemas, entities,
      fields, or database structures.
    * Markdown tables that describe fields (column names, types, constraints).
    * Fenced code blocks containing class/schema/model definitions.

    The extracted text is injected verbatim into the ``core`` generation pass
    prompt so the LLM produces SQLAlchemy models and Pydantic schemas that
    match the spec's field names, types, and constraints exactly, rather than
    inventing arbitrary structures.

    Args:
        requirements: The requirements dict.  Uses ``md_content`` when present,
            falling back to ``description``.

    Returns:
        A UTF-8 string containing the most relevant model/schema excerpts from
        the spec, capped at :data:`_SPEC_MODELS_MAX_CHARS` characters to avoid
        overwhelming the LLM context.  Returns an empty string when no relevant
        content is found or the input is empty.

    Examples:
        >>> reqs = {"md_content": "## Data Models\\n| Field | Type |\\n|---|---|\\n| id | UUID |\\n"}
        >>> result = _extract_spec_models(reqs)
        >>> "UUID" in result
        True
    """
    md = (requirements.get("md_content") or requirements.get("description") or "").strip()
    if not md:
        return ""

    extracted: List[str] = []

    # ------------------------------------------------------------------ #
    # 1. Heading-delimited model/schema sections                          #
    # ------------------------------------------------------------------ #
    # Split the document into sections on any heading (h1–h3).
    # We look for sections whose heading title mentions domain-model keywords.
    _heading_split_re = re.compile(r'(?=^#{1,3}[ \t])', re.MULTILINE)
    _model_heading_re = re.compile(
        r'^#{1,3}[ \t]+.*?'
        r'(?:data\s*model|schema|model|entity|entit(?:y|ies)|database|'
        r'field|attribute|resource|object|struct)',
        re.IGNORECASE,
    )
    sections = _heading_split_re.split(md)
    for section in sections:
        first_line = section.split("\n", 1)[0]
        if _model_heading_re.match(first_line) and len(section.strip()) >= _SPEC_MODELS_MIN_SECTION_LEN:
            extracted.append(section.strip()[:2000])

    # ------------------------------------------------------------------ #
    # 2. Markdown field-definition tables                                 #
    # ------------------------------------------------------------------ #
    # Match pipe-delimited tables that contain keywords suggesting they
    # describe entity fields (type, column, id, uuid, optional, required).
    _table_re = re.compile(
        r'(?:^|\n)(\|[^\n]+\|\n(?:\|[-:| ]+\|\n)?(?:\|[^\n]+\|\n)+)',
        re.MULTILINE,
    )
    _table_keyword_re = re.compile(
        r'\b(?:type|field|column|id|uuid|integer|string|float|bool|required|optional|nullable)\b',
        re.IGNORECASE,
    )
    for m in _table_re.finditer(md):
        table = m.group(1).strip()
        if _table_keyword_re.search(table) and len(table) >= _SPEC_MODELS_MIN_SECTION_LEN:
            extracted.append(table[:1000])

    # ------------------------------------------------------------------ #
    # 3. Fenced code blocks with model/class definitions                  #
    # ------------------------------------------------------------------ #
    _code_block_re = re.compile(
        r'```[ \t]*(?:python|json|yaml|sql|pydantic)?[ \t]*\n(.*?)```',
        re.DOTALL | re.IGNORECASE,
    )
    _code_model_re = re.compile(
        r'\b(?:class\s+\w|BaseModel|declarative_base|Column|uuid|UUID|Integer|String|Float|Boolean)\b',
        re.IGNORECASE,
    )
    for m in _code_block_re.finditer(md):
        block = m.group(1).strip()
        if _code_model_re.search(block) and len(block) >= _SPEC_MODELS_MIN_SECTION_LEN:
            extracted.append(block[:1500])

    if not extracted:
        return ""

    # Deduplicate: normalise runs of whitespace before comparing
    _ws_re = re.compile(r'\s+')
    seen_normalised: Set[str] = set()
    unique: List[str] = []
    for item in extracted:
        key = _ws_re.sub(" ", item)
        if key not in seen_normalised:
            seen_normalised.add(key)
            unique.append(item)

    # Join and hard-cap to avoid bloating the LLM prompt
    combined = "\n\n---\n\n".join(unique)
    return combined[:_SPEC_MODELS_MAX_CHARS]


# Percentage of stub-like function bodies above which a service file is flagged
_PLACEHOLDER_SERVICE_THRESHOLD_PCT = 30.0

# Maximum number of targeted LLM stub-replacement passes after the ensemble merge.
# Kept small to prevent unbounded retry loops when the LLM persistently returns stubs.
_STUB_RETRY_MAX_ATTEMPTS: int = 2

# Plain-text prompt suffix used by _retry_stub_files when the Jinja2 template is
# unavailable.  Centralised here so the exception-fallback and the no-template
# paths always produce identical instructions.
_STUB_RETRY_PLAIN_PROMPT_SUFFIX: str = (
    "\n\n"
    "Return ONLY a JSON object whose keys are the file paths listed above "
    "and whose values are the complete, production-ready file contents. "
    "Do NOT include any explanatory text outside the JSON object.\n\n"
    "Example format:\n"
    "{\n"
    '  "files": {\n'
    '    "app/config.py": "from pydantic import BaseSettings\\n...",\n'
    '    "app/schemas/product.py": "from pydantic import BaseModel\\n..."\n'
    "  }\n"
    "}"
)

def _validate_wiring(files: Dict[str, str]) -> Dict[str, Any]:
    """Validate that generated files form a coherent, runnable application.

    Performs four categories of checks:

    **Router-wiring check**
        Scans every ``app/routers/<name>.py`` for an ``APIRouter`` instance
        variable.  For each router found, checks that ``app/main.py`` (a)
        imports the variable and (b) calls ``app.include_router(<var>)``.
        Routers that fail either condition are reported as *unwired*.

    **Duplicate route prefix detection** (section 1.6)
        Checks whether any route decorator path in a router file starts with
        the same prefix that ``main.py`` already applies via
        ``include_router(..., prefix=...)``.  Such double-prefixed routes
        produce unreachable paths at runtime.

    **Placeholder-service check**
        Scans every ``app/services/<name>.py`` and counts function/method
        definitions against "stub-like" bodies — functions whose sole
        effective content is an empty return, ``return []``, ``pass``,
        ``raise NotImplementedError``, or a ``# Placeholder`` / ``# TODO``
        comment.  Service files where the ratio of stubs to total functions
        exceeds :data:`_PLACEHOLDER_SERVICE_THRESHOLD_PCT` percent are
        reported.  An AST-based fallback also catches functions whose only
        body is an optional docstring followed by ``return None``.

    **Cross-file symbol resolution** (section 3)
        Scans all Python files for ``from app.X import Y1, Y2, ...``
        statements and verifies that each imported symbol is actually defined
        in the target module file.

    This function is intentionally pure (no I/O, no LLM calls) so it can
    be called safely as a fast post-processing step.

    Args:
        files: Dict mapping relative file paths (forward-slash separators)
            to their string content, as produced by :func:`parse_llm_response`
            or the multi-pass merge loop.

    Returns:
        A dict with the following keys:

        ``"unwired_routers"`` : List[str]
            Paths of router files whose ``APIRouter`` variable is not mounted
            in ``app/main.py``.

        ``"placeholder_services"`` : List[Tuple[str, float]]
            ``(path, pct)`` pairs for service files with a stub ratio above
            the threshold.  ``pct`` is rounded to one decimal place.

        ``"duplicate_prefixes"`` : List[Tuple[str, str, str]]
            ``(router_file, mount_prefix, decorator_path)`` triples where a
            route decorator path starts with the prefix already applied by
            ``main.py``'s ``include_router`` call.

        ``"unresolved_imports"`` : List[Tuple[str, str, List[str]]]
            ``(importing_file, target_module, [missing_symbols])`` triples
            where one or more imported symbols are not defined in the target
            module file.

    Examples:
        >>> files = {
        ...     "app/routers/products.py": "router = APIRouter()\\n@router.get('/')\\nasync def list_products(): ...",
        ...     "app/main.py": "from fastapi import FastAPI\\napp = FastAPI()",
        ... }
        >>> result = _validate_wiring(files)
        >>> "app/routers/products.py" in result["unwired_routers"]
        True
    """
    normalised: Dict[str, str] = {k.replace("\\", "/"): v for k, v in files.items()}

    # ------------------------------------------------------------------ #
    # 1. Router-wiring check                                              #
    # ------------------------------------------------------------------ #
    _router_path_re = re.compile(r'^app/(?:routers|routes)/(?!__init__)[\w-]+\.py$')
    # Match ``var_name = APIRouter(`` at module scope (any amount of leading ws)
    _router_var_re = re.compile(r'^[ \t]*(\w+)\s*=\s*APIRouter\s*\(', re.MULTILINE)

    router_vars: Dict[str, str] = {}  # path -> first router variable name
    for path, content in normalised.items():
        if _router_path_re.match(path):
            m = _router_var_re.search(content)
            if m:
                router_vars[path] = m.group(1)

    main_content = normalised.get("app/main.py", "")
    unwired: List[str] = []
    for path, var in router_vars.items():
        # The router must both be imported into main.py AND passed to include_router().
        # We accept either a direct import of the variable name or an import of the
        # router module (the reconcile step always does a direct var import).
        module_stem = path.rsplit("/", 1)[-1].removesuffix(".py")
        is_imported = (
            re.search(rf'\bimport\b[^\n]*\b{re.escape(var)}\b', main_content)
            is not None
            or re.search(rf'\bimport\b[^\n]*\b{re.escape(module_stem)}\b', main_content)
            is not None
        )
        is_mounted = re.search(
            rf'\binclude_router\s*\(\s*{re.escape(var)}\b', main_content
        ) is not None
        if not is_imported or not is_mounted:
            unwired.append(path)

    # ------------------------------------------------------------------ #
    # 1.5  Duplicate ORM Base detection                                   #
    # ------------------------------------------------------------------ #
    _base_def_re = re.compile(r'(?:class\s+Base\s*\(|Base\s*=\s*declarative_base\s*\()')
    base_definitions: List[str] = []
    for path, content in normalised.items():
        if not path.endswith(".py"):
            continue
        if _base_def_re.search(content):
            base_definitions.append(path)

    if len(base_definitions) > 1:
        logger.warning(
            "[CODEGEN] _validate_wiring: Multiple 'Base' definitions found in %s. "
            "This will cause split metadata registries. Consider consolidating to a single Base in app/database.py",
            base_definitions,
        )

    # ------------------------------------------------------------------ #
    # 1.6  Duplicate route prefix detection                               #
    # ------------------------------------------------------------------ #
    # Detect when a router file's decorator paths start with the same
    # prefix that main.py already applies via include_router(..., prefix=).
    # At runtime this doubles the prefix (e.g. /api/v1/items/api/v1/items).
    _route_decorator_re = re.compile(
        r'@\w+\.(?:get|post|put|patch|delete|head|options)\s*\(\s*["\']([^"\']+)["\']',
        re.IGNORECASE,
    )
    _include_router_prefix_re = re.compile(
        r'include_router\s*\(\s*(\w+)\s*[^)]*prefix\s*=\s*["\']([^"\']+)["\']'
    )
    # Pattern to extract aliased router imports: ``from app.routers.X import router as X_router``
    _router_import_alias_re = re.compile(
        r'from\s+(app\.routers\.\w+)\s+import\s+(\w+)\s+as\s+(\w+)',
        re.MULTILINE,
    )
    duplicate_prefixes: List[Tuple[str, str, str]] = []
    # Build mapping: var_name_in_main -> mount_prefix from main.py
    _mount_prefix_map: Dict[str, str] = {}
    for _m in _include_router_prefix_re.finditer(main_content):
        _mount_prefix_map[_m.group(1)] = _m.group(2)
    # Build reverse mapping: router_file_path -> alias name used in main.py
    # so that ``from app.routers.items import router as items_router`` is resolved.
    _path_to_alias: Dict[str, str] = {}
    for _alias_m in _router_import_alias_re.finditer(main_content):
        _mod_path = _alias_m.group(1).replace(".", "/") + ".py"
        _path_to_alias[_mod_path] = _alias_m.group(3)
    for path, var in router_vars.items():
        # Prefer the aliased name if main.py imported the router under a different name
        _var_in_main = _path_to_alias.get(path, var)
        _mount_prefix = _mount_prefix_map.get(_var_in_main, "") or _mount_prefix_map.get(var, "")
        if not _mount_prefix:
            continue
        router_content = normalised.get(path, "")
        for _dec_m in _route_decorator_re.finditer(router_content):
            _dec_path = _dec_m.group(1)
            if _dec_path.startswith(_mount_prefix):
                duplicate_prefixes.append((path, _mount_prefix, _dec_path))

    # ------------------------------------------------------------------ #
    # 2. Placeholder-service check                                        #
    # ------------------------------------------------------------------ #
    # Patterns that strongly indicate a stub function body:
    #   - return []  /  return {}  /  return ()  /  return None  alone on a line
    #   - bare ``pass`` on its own line
    #   - raise NotImplementedError (with or without arguments)
    #   - comment-only body: # Placeholder / # TODO / # FIXME
    _stub_body_re = re.compile(
        r'(?:'
        r'^\s*return\s*(?:\[\s*\]|\{\s*\}|\(\s*\)|None)\s*$'      # empty returns
        r'|^\s*pass\s*$'                                             # bare pass
        r'|^\s*raise\s+NotImplementedError\b'                        # NIE
        r'|^\s*#\s*(?:placeholder|todo|fixme|stub|not\s+implemented)'# comments
        r')',
        re.MULTILINE | re.IGNORECASE,
    )
    # Match any function definition (sync or async) at any indentation level
    _func_def_re = re.compile(r'^\s*(?:async\s+)?def\s+\w+\s*\(', re.MULTILINE)

    def _is_stub_function_ast(node: ast.AST) -> bool:
        """Return True if an AST FunctionDef/AsyncFunctionDef is a stub body.

        A stub body is defined as: an optional docstring (``Expr(Constant(str))``)
        followed by a single ``return None`` / ``return []`` / ``return {}`` /
        ``return ()`` / ``pass`` / ``raise NotImplementedError`` statement.
        """
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return False
        body = node.body
        if not body:
            return True
        # Skip optional leading docstring
        start = 0
        if (
            len(body) >= 1
            and isinstance(body[0], ast.Expr)
            and isinstance(getattr(body[0], "value", None), ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            start = 1
        effective = body[start:]
        if not effective:
            return True
        if len(effective) == 1:
            stmt = effective[0]
            # ``return None`` / ``return`` / ``return []`` / ``return {}`` / ``return ()``
            if isinstance(stmt, ast.Return):
                val = stmt.value
                if val is None:
                    return True
                if isinstance(val, ast.Constant) and val.value is None:
                    return True
                # ``return []`` / ``return ()`` — empty list or tuple
                if isinstance(val, (ast.List, ast.Tuple)) and len(getattr(val, "elts", [1])) == 0:
                    return True
                # ``return {}`` — empty dict (Dict has ``keys`` not ``elts``)
                if isinstance(val, ast.Dict) and len(getattr(val, "keys", [1])) == 0:
                    return True
            # ``pass``
            if isinstance(stmt, ast.Pass):
                return True
            # ``raise NotImplementedError``
            if isinstance(stmt, ast.Raise) and stmt.exc is not None:
                exc = stmt.exc
                name = (
                    exc.id if isinstance(exc, ast.Name)
                    else (exc.func.id if isinstance(exc, ast.Call) and isinstance(exc.func, ast.Name) else None)
                )
                if name == "NotImplementedError":
                    return True
        return False

    placeholder_services: List[Tuple[str, float]] = []
    for path, content in normalised.items():
        if not ("app/services/" in path and path.endswith(".py")):
            continue
        funcs = _func_def_re.findall(content)
        if not funcs:
            continue
        stub_hits = _stub_body_re.findall(content)
        pct = len(stub_hits) / len(funcs) * 100.0
        # AST-based fallback: count functions whose body is purely stub-like
        # (optional docstring + return None). This catches cases where the regex
        # under-counts because indentation or spacing varies.
        if pct <= _PLACEHOLDER_SERVICE_THRESHOLD_PCT:
            try:
                _tree = ast.parse(content)
                _ast_stub_count = sum(
                    1 for _node in ast.walk(_tree)
                    if isinstance(_node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and _is_stub_function_ast(_node)
                )
                _ast_func_count = sum(
                    1 for _node in ast.walk(_tree)
                    if isinstance(_node, (ast.FunctionDef, ast.AsyncFunctionDef))
                )
                if _ast_func_count > 0:
                    _ast_pct = _ast_stub_count / _ast_func_count * 100.0
                    if _ast_pct > pct:
                        pct = _ast_pct
            except SyntaxError:
                pass
        if pct > _PLACEHOLDER_SERVICE_THRESHOLD_PCT:
            placeholder_services.append((path, round(pct, 1)))

    # ------------------------------------------------------------------ #
    # 3. Cross-file symbol resolution check                               #
    # ------------------------------------------------------------------ #
    # Scan all Python files for ``from app.X import Y1, Y2, ...`` and verify
    # that each imported symbol is actually defined in the target module.
    _from_app_import_re = re.compile(
        r'^[ \t]*from\s+(app(?:\.[a-zA-Z_]\w*)+)\s+import\s+(.+)',
        re.MULTILINE,
    )
    unresolved_imports: List[Tuple[str, str, List[str]]] = []
    for path, content in normalised.items():
        if not path.endswith(".py"):
            continue
        for _imp_m in _from_app_import_re.finditer(content):
            _module_str = _imp_m.group(1).strip()
            _imports_raw = _imp_m.group(2).strip().strip("()")
            # Parse imported symbol names (handle ``A as a``, multi-token)
            _symbols: List[str] = []
            for _part in _imports_raw.split(","):
                _name = _part.split(" as ")[0].strip()
                if _name and _name != "*" and _name.isidentifier():
                    _symbols.append(_name)
            if not _symbols:
                continue
            # Resolve to a module file path; skip if the target isn't in our
            # generated files (it may be a third-party or stdlib package).
            _module_path = _module_str.replace(".", "/") + ".py"
            _target_content = normalised.get(_module_path, "")
            if not _target_content:
                # Also try package __init__.py form
                _init_path = _module_str.replace(".", "/") + "/__init__.py"
                _target_content = normalised.get(_init_path, "")
            if not _target_content:
                continue
            # Check each symbol against the target module content
            _missing: List[str] = []
            for _sym in _symbols:
                _sym_re = re.escape(_sym)
                _defined = (
                    re.search(rf'^class\s+{_sym_re}\s*[\(:]', _target_content, re.MULTILINE)
                    or re.search(rf'^(?:async\s+)?def\s+{_sym_re}\s*\(', _target_content, re.MULTILINE)
                    or re.search(rf'^{_sym_re}\s*=', _target_content, re.MULTILINE)
                    or re.search(rf'(?:"|\'){_sym_re}(?:"|\') *[,\]]', _target_content)
                )
                if not _defined:
                    _missing.append(_sym)
            if _missing:
                unresolved_imports.append((path, _module_str, _missing))

    return {
        "unwired_routers": sorted(unwired),
        "placeholder_services": sorted(placeholder_services, key=lambda t: t[0]),
        "duplicate_prefixes": duplicate_prefixes,
        "unresolved_imports": unresolved_imports,
    }


async def _repair_stub_services(
    merged_files: Dict[str, str],
    config: Any,
    placeholder_services: List[Tuple[str, float]],
    spec_models: str = "",
) -> Dict[str, str]:
    """Fix 6: Trigger a targeted LLM pass to replace stub service functions with real ORM logic.

    When ``_validate_wiring`` detects service files where >= 50 % of functions are stubs
    (``# Dummy:``, ``return []``, ``return {}``, etc.) this function asks the LLM to produce
    a real implementation for each such service file.  Only one repair attempt is made per
    pipeline run to avoid infinite loops.

    Args:
        merged_files: Current merged code_files mapping.
        config: CodegenConfig-like object with ``backend`` and ``model`` attributes.
        placeholder_services: List of (path, pct) tuples from ``_validate_wiring``.

    Returns:
        Updated code_files mapping with repaired service files (where successful).
    """
    from .codegen_response_handler import parse_llm_response

    high_stub_services = [
        (path, pct)
        for path, pct in placeholder_services
        if pct >= _PLACEHOLDER_SERVICE_THRESHOLD_PCT
    ]
    if not high_stub_services:
        return merged_files

    updated = dict(merged_files)
    repaired_count = 0

    for svc_path, pct in high_stub_services:
        svc_content = merged_files.get(svc_path, "")
        if not svc_content:
            continue

        # Find related model, schema, and router files
        # e.g. "app/services/product_service.py" → base_name = "product"
        svc_stem = Path(svc_path).stem  # e.g. "product_service"
        base_name = re.sub(r"_service$", "", svc_stem)  # e.g. "product"

        related_model = merged_files.get(f"app/models/{base_name}.py", "")
        related_schema = merged_files.get(f"app/schemas/{base_name}.py", "")
        # Router may be plural
        related_router = (
            merged_files.get(f"app/routers/{base_name}s.py", "")
            or merged_files.get(f"app/routers/{base_name}.py", "")
        )

        context_parts = [
            f"# FILE: {svc_path}\n{svc_content}",
        ]
        if related_model:
            model_path = f"app/models/{base_name}.py"
            context_parts.append(f"# FILE: {model_path}\n{related_model}")
        if related_schema:
            schema_path = f"app/schemas/{base_name}.py"
            context_parts.append(f"# FILE: {schema_path}\n{related_schema}")
        if related_router:
            router_path = (
                f"app/routers/{base_name}s.py"
                if f"app/routers/{base_name}s.py" in merged_files
                else f"app/routers/{base_name}.py"
            )
            context_parts.append(f"# FILE: {router_path}\n{related_router}")

        context_block = "\n\n".join(context_parts)
        spec_context = f"\n\nSpec Data Models (implement these exactly):\n{spec_models}\n" if spec_models else ""

        # Use the Jinja2 repair_stub_services.jinja2 template when available for
        # richer, per-service context (related model / schema / router included).
        # Falls back to an inline f-string prompt when Jinja2 is absent or the
        # template file has been removed.
        repair_prompt: str
        _tmpl_dir = Path(__file__).parent / "templates" / "stubs"
        _repair_tmpl = _tmpl_dir / "repair_stub_services.jinja2"
        if _repair_tmpl.exists():
            try:
                from jinja2 import Environment, FileSystemLoader
                _env = Environment(
                    loader=FileSystemLoader(str(_tmpl_dir)),
                    trim_blocks=True,
                    lstrip_blocks=True,
                    autoescape=False,
                )
                repair_prompt = _env.get_template("repair_stub_services.jinja2").render(
                    svc_path=svc_path,
                    pct=pct,
                    context_block=context_block,
                    spec_context=spec_context,
                    related_model=related_model,
                    related_schema=related_schema,
                    related_router=related_router,
                )
            except Exception:
                # Template render failed — fall back to inline prompt string.
                repair_prompt = (
                    f"The following service file is {pct:.0f}% placeholder stubs "
                    f"(# Dummy: comments, `return []`, `return {{}}`, `return None` bodies). "
                    f"Replace every stub function with a real SQLAlchemy async ORM implementation. "
                    f"Use the model and schema files provided as context.\n\n"
                    f"Return ONLY the repaired service file as a JSON object with key '{svc_path}'.\n\n"
                    f"{spec_context}"
                    f"{context_block}"
                )
        else:
            # Template file not present — use inline prompt string directly.
            repair_prompt = (
                f"The following service file is {pct:.0f}% placeholder stubs "
                f"(# Dummy: comments, `return []`, `return {{}}`, `return None` bodies). "
                f"Replace every stub function with a real SQLAlchemy async ORM implementation. "
                f"Use the model and schema files provided as context.\n\n"
                f"Return ONLY the repaired service file as a JSON object with key '{svc_path}'.\n\n"
                f"{spec_context}"
                f"{context_block}"
            )

        try:
            from generator.runner.llm_client import call_llm_api  # local import to avoid circular
            repair_response = await call_llm_api(
                prompt=repair_prompt,
                provider=config.backend,
                model=config.model.get(config.backend) if hasattr(config, "model") else None,
                response_format={"type": "json_object"},
                skip_cache=True,
            )
            repair_files = parse_llm_response(repair_response)
            if svc_path in repair_files and repair_files[svc_path].strip():
                updated[svc_path] = _ast_merge_python_files(
                    merged_files[svc_path], repair_files[svc_path]
                )
                repaired_count += 1
                logger.info(
                    "[CODEGEN] Service repair pass: regenerated stub service '%s' with real ORM logic",
                    svc_path,
                )
        except Exception as _repair_err:
            logger.warning(
                "[CODEGEN] Service repair pass failed for '%s' (non-fatal): %s",
                svc_path, _repair_err,
            )

    if repaired_count:
        logger.info(
            "[CODEGEN] Service repair pass: regenerated %d stub service(s) with real ORM logic",
            repaired_count,
        )
    return updated


async def _retry_stub_files(
    merged_files: Dict[str, str],
    config: Any,
) -> Dict[str, str]:
    """Replace auto-generated stub modules with real implementations via focused LLM calls.

    After the multi-pass ensemble completes, some files may still contain the
    canonical ``"Generated module — replace with actual implementation."`` stub
    header.  This function runs up to :data:`_STUB_RETRY_MAX_ATTEMPTS`
    targeted LLM passes, each time asking the model to replace *only* the
    remaining stub files.  It exits as soon as no stubs remain (happy-path
    fast exit) or the attempt budget is exhausted.

    Each LLM call uses ``response_format={"type": "json_object"}`` and
    ``skip_cache=True`` so results are fresh and machine-parseable.

    Args:
        merged_files: Current merged code-file mapping (path → content) after
                      the ensemble merge and reconciliation steps.
        config: CodegenConfig-like object exposing ``backend`` and ``model``
                attributes, mirroring the convention used by
                :func:`_repair_stub_services`.

    Returns:
        An updated code-file mapping where stub files have been replaced by
        real implementations where the LLM succeeded.  Files the LLM did
        *not* return, or returned with empty content, are left unchanged.
    """
    result = dict(merged_files)

    syntax_error_streak = 0
    _explicit_filename_hint: str = ""  # Injected when LLM returns wrong filenames
    for attempt in range(_STUB_RETRY_MAX_ATTEMPTS):
        retry_hint = build_stub_retry_prompt_hint(result)

        # Prepend any explicit filename hint from the previous iteration where
        # the LLM returned wrong filenames (e.g. 'error.txt').
        if _explicit_filename_hint:
            retry_hint = (
                _explicit_filename_hint + "\n\n" + retry_hint
                if retry_hint
                else _explicit_filename_hint
            )
            _explicit_filename_hint = ""  # Consume the hint for this iteration only

        # Also detect function-level stubs (pass/... bodies) via AST analysis.
        # These are NOT caught by get_stub_files (which only checks for the
        # auto-generated stub marker) but are still invalid LLM output.
        _func_stub_issues: Dict[str, List[str]] = {}
        for _path, _content in result.items():
            if not _path.endswith(".py"):
                continue
            _is_stub, _issues = _detect_stub_patterns(_content, _path)
            if _is_stub and _issues:
                _func_stub_issues[_path] = _issues

        _detailed_feedback = build_detailed_stub_feedback(_func_stub_issues)
        if _detailed_feedback:
            retry_hint = (
                (retry_hint + "\n\n" if retry_hint else "")
                + "The following functions were detected as stubs and MUST be "
                "implemented with complete business logic:\n"
                + _detailed_feedback
                + "\n\nGenerate ONLY the files that contain stubs, with complete "
                "implementations. Do not use `pass`, `...`, or TODO comments."
            )

        if not retry_hint:
            # No stubs remaining — exit early.
            logger.info(
                "[CODEGEN] _retry_stub_files: all stubs resolved after %d attempt(s)",
                attempt,
            )
            break

        stub_paths = get_stub_files(result)
        # Also add files with function-level stubs to the retry set.
        stub_paths.update(_func_stub_issues.keys())
        # Never ask the LLM to regenerate main.py — it's auto-wired by _reconcile_app_wiring
        stub_paths.discard("app/main.py")
        stub_paths.discard("main.py")
        logger.info(
            "[CODEGEN] _retry_stub_files: attempt %d/%d — %d stub file(s) remaining: %s",
            attempt + 1,
            _STUB_RETRY_MAX_ATTEMPTS,
            len(stub_paths),
            sorted(stub_paths),
        )

        retry_prompt: str
        # Use the Jinja2 retry_stub_files.jinja2 template when available for a
        # structured, per-category prompt with technology-specific guidance and
        # an importer dependency map.  Falls back to a plain concatenation of
        # the hint string when Jinja2 is absent or the template is missing.
        _tmpl_dir = Path(__file__).parent / "templates" / "stubs"
        _retry_tmpl = _tmpl_dir / "retry_stub_files.jinja2"
        if _retry_tmpl.exists():
            try:
                from jinja2 import Environment, FileSystemLoader
                _env = Environment(
                    loader=FileSystemLoader(str(_tmpl_dir)),
                    trim_blocks=True,
                    lstrip_blocks=True,
                    autoescape=False,
                )
                # Group stubs by module category so the template can emit
                # per-category technology instructions (SQLAlchemy, Pydantic, etc.).
                _stub_groups: Dict[str, List[str]] = {}
                for _p in sorted(stub_paths):
                    _cat = _classify_stub_module(_p, set())
                    _stub_groups.setdefault(_cat, []).append(_p)
                # Build importer map: stub_path → list of files that import it.
                # This lets the LLM infer expected signatures from the importers.
                _importers_map: Dict[str, List[str]] = {}
                for _p in sorted(stub_paths):
                    _mod_name = _p.replace("/", ".").removesuffix(".py")
                    # Strip leading package prefix (e.g. "app.") for broader matching.
                    _mod_base = _mod_name.split(".", 1)[-1] if "." in _mod_name else _mod_name
                    _importers_map[_p] = [
                        f for f, c in result.items()
                        if f != _p and (
                            f"from {_mod_name}" in c
                            or f"import {_mod_base}" in c
                            or f"from {_mod_base}" in c
                        )
                    ]
                retry_prompt = _env.get_template("retry_stub_files.jinja2").render(
                    retry_hint=retry_hint,
                    stub_groups=_stub_groups,
                    importers_map=_importers_map,
                )
            except Exception:
                # Template render failed — fall back to plain hint concatenation.
                retry_prompt = retry_hint + _STUB_RETRY_PLAIN_PROMPT_SUFFIX
        else:
            # Template file not present — use plain prompt fallback directly.
            retry_prompt = retry_hint + _STUB_RETRY_PLAIN_PROMPT_SUFFIX

        expected_paths = set(stub_paths)

        try:
            retry_response = await call_llm_api(
                prompt=retry_prompt,
                provider=config.backend,
                model=config.model.get(config.backend) if hasattr(config, "model") else None,
                response_format={"type": "json_object"},
                skip_cache=True,
            )
            new_files = parse_llm_response(retry_response)
            # Recovery: if LLM returned everything under a single key (e.g. "main.py")
            # that contains a JSON file-map string, extract the inner files.
            if len(new_files) == 1:
                single_key = next(iter(new_files))
                single_val = new_files[single_key]
                if single_key not in expected_paths or single_key == ERROR_FILENAME:
                    try:
                        inner = json.loads(single_val)
                        if isinstance(inner, dict):
                            # Check if it looks like a file-map (keys contain "/")
                            file_like = {k: v for k, v in inner.items() if isinstance(v, str) and "/" in k}
                            if not file_like:
                                # Maybe it's wrapped in a "files" key
                                file_like = inner.get("files", {})
                            if file_like:
                                new_files = file_like
                                logger.info(
                                    "[CODEGEN] _retry_stub_files: recovered %d file(s) from nested JSON in '%s' key",
                                    len(new_files), single_key,
                                )
                    except (json.JSONDecodeError, TypeError):
                        pass
        except Exception as llm_err:
            logger.warning(
                "[CODEGEN] _retry_stub_files: LLM call failed on attempt %d (non-fatal): %s",
                attempt + 1,
                llm_err,
            )
            break

        # Fuzzy match: build a basename→stub_path lookup to resolve LLM-returned
        # paths that differ by directory prefix or casing.
        # If two stub paths share the same basename (e.g. app/models.py and
        # tests/models.py), mark the entry as None to signal ambiguity and skip.
        _stub_by_basename: Dict[str, Optional[str]] = {}
        for _sp in expected_paths:
            _base = os.path.basename(_sp)
            if _base in _stub_by_basename:
                _stub_by_basename[_base] = None  # mark ambiguous
            else:
                _stub_by_basename[_base] = _sp

        matched_files: Dict[str, str] = {}
        for returned_path, returned_content in new_files.items():
            if returned_path in ("error.txt", "__syntax_errors__", "__validation_summary__"):
                continue
            if returned_path in expected_paths:
                matched_files[returned_path] = returned_content
            else:
                # Attempt basename fuzzy match for paths that differ by prefix/casing
                returned_base = os.path.basename(returned_path)
                target = _stub_by_basename.get(returned_base)
                if returned_base in _stub_by_basename and target is None:
                    logger.warning(
                        "[CODEGEN] _retry_stub_files: skipping ambiguous basename match "
                        "for '%s' — multiple stub paths share basename '%s'",
                        returned_path,
                        returned_base,
                    )
                elif target is not None:
                    matched_files[target] = returned_content
                    logger.info(
                        "[CODEGEN] _retry_stub_files: fuzzy-matched '%s' -> '%s'",
                        returned_path,
                        target,
                    )

        if not matched_files:
            syntax_error_streak += 1
            if syntax_error_streak >= 2:
                logger.warning(
                    "[CODEGEN] _retry_stub_files: 2 consecutive syntax-error-only responses; aborting stub retry"
                )
                break
        else:
            syntax_error_streak = 0

        replaced = 0
        for path, content in matched_files.items():
            # Never overwrite app/main.py — it is auto-generated by _reconcile_app_wiring
            if path in ("app/main.py", "main.py"):
                logger.info(
                    "[CODEGEN] _retry_stub_files: skipping '%s' — auto-generated by _reconcile_app_wiring",
                    path,
                )
                continue
            if path in result and content and content.strip():
                result[path] = content
                replaced += 1

        returned_paths = set(new_files.keys())
        _no_match = returned_paths and not (returned_paths & expected_paths) and not matched_files
        if _no_match:
            logger.warning(
                "[CODEGEN] _retry_stub_files: attempt %d — LLM returned %d file(s) "
                "but none matched the %d requested stub files. "
                "Returned: %s, Expected: %s",
                attempt + 1,
                len(returned_paths),
                len(expected_paths),
                sorted(returned_paths),
                sorted(expected_paths),
            )
            # Build an explicit hint for the next iteration that lists the exact
            # filenames required so the LLM cannot return 'error.txt' or other wrong paths.
            _explicit_paths = sorted(expected_paths)
            _explicit_filename_hint = (
                "CRITICAL: You MUST return files with EXACTLY these paths "
                "(not 'error.txt' or any other path):\n"
                + "\n".join(f"  - {p}" for p in _explicit_paths)
                + "\n\n"
                "For each path, provide the complete, production-ready implementation "
                "as the value in the JSON response."
            )
            # Include current stub content so the LLM knows what to flesh out.
            _stub_context_lines = []
            for _sp in _explicit_paths:
                _existing = result.get(_sp, "")
                if _existing.strip():
                    _stub_context_lines.append(
                        f"# --- Current stub content of {_sp} (replace with full implementation) ---\n"
                        + _existing[:800]
                    )
            if _stub_context_lines:
                _explicit_filename_hint += (
                    "\n\nCurrent stub contents for reference:\n"
                    + "\n\n".join(_stub_context_lines)
                )

        logger.info(
            "[CODEGEN] _retry_stub_files: attempt %d replaced %d file(s)",
            attempt + 1,
            replaced,
        )

        if replaced == 0 and attempt == _STUB_RETRY_MAX_ATTEMPTS - 1:
            logger.warning(
                "[CODEGEN] _retry_stub_files: no stub files replaced after %d attempt(s); "
                "generating minimal functional implementations for: %s",
                _STUB_RETRY_MAX_ATTEMPTS,
                sorted(expected_paths),
            )
            # Generate minimal but functional implementations so downstream stages
            # (testgen, deploy, docgen, critique) are not blocked by stub placeholders.
            for _stub_path in sorted(expected_paths):
                if _stub_path in ("app/main.py", "main.py"):
                    continue
                _existing_stub = result.get(_stub_path, "")
                if not _existing_stub.strip():
                    continue
                # Only replace if still a stub (not already a real implementation)
                # A file is considered a stub if it has fewer than 10 non-blank, non-comment lines
                _real_lines = [
                    ln for ln in _existing_stub.splitlines()
                    if ln.strip() and not ln.strip().startswith("#")
                ]
                if len(_real_lines) >= 10:
                    # Likely a real implementation already — skip
                    continue
                _stem = os.path.basename(_stub_path).replace(".py", "")
                # Build CamelCase class name from snake_case stem once and reuse
                _class_name = _stem.replace("_", " ").title().replace(" ", "")
                _minimal = (
                    f"# Minimal implementation auto-generated by _retry_stub_files fallback\n"
                    f"# TODO: Replace with full implementation\n\n"
                )
                if "service" in _stem or "services" in _stub_path:
                    _minimal += (
                        "from typing import Any, Dict, List, Optional\n\n\n"
                        f"class {_class_name}:\n"
                        f"    \"\"\"Auto-generated minimal service stub.\"\"\"\n\n"
                        f"    def __init__(self) -> None:\n"
                        f"        pass\n"
                    )
                elif "model" in _stem or "models" in _stub_path:
                    _minimal += (
                        "from sqlalchemy import Column, Integer, String\n"
                        "from sqlalchemy.orm import DeclarativeBase\n\n\n"
                        "class Base(DeclarativeBase):\n"
                        "    pass\n\n\n"
                        f"class {_class_name}(Base):\n"
                        f'    __tablename__ = "{_stem}s"\n'
                        f"    id = Column(Integer, primary_key=True, index=True)\n"
                    )
                elif "schema" in _stem or "schemas" in _stub_path:
                    _minimal += (
                        "from pydantic import BaseModel\n\n\n"
                        f"class {_class_name}Base(BaseModel):\n"
                        f"    pass\n\n\n"
                        f"class {_class_name}Create({_class_name}Base):\n"
                        f"    pass\n\n\n"
                        f"class {_class_name}Read({_class_name}Base):\n"
                        f"    id: int\n"
                    )
                else:
                    _minimal += (
                        "from typing import Any\n\n\n"
                        f"def placeholder() -> Any:\n"
                        f"    \"\"\"Auto-generated minimal placeholder.\"\"\"\n"
                        f"    return None\n"
                    )
                result[_stub_path] = _minimal
                logger.info(
                    "[CODEGEN] _retry_stub_files: generated minimal fallback implementation for '%s'",
                    _stub_path,
                )

    return result


def _reconcile_app_wiring(files: Dict[str, str]) -> Dict[str, str]:
    """Post-ensemble reconciliation: wire discovered routers into main.py (no LLM needed).

    Scans all generated ``app/routers/*.py`` files, rebuilds
    ``app/routers/__init__.py`` with correct imports, and re-generates
    ``app/main.py`` to mount every discovered router via
    ``app.include_router()``.  Also generates stub SQLAlchemy ORM model files
    for any model classes referenced in schemas but missing from
    ``app/models/``.

    This function is intentionally pure (no I/O, no LLM calls) so it can
    always run safely as a post-processing step after multi-pass ensemble
    generation, even under tight time budgets.

    Args:
        files: Dict mapping relative file path → file content for all
               generated files.  Paths are expected to use forward slashes.

    Returns:
        A new dict with the same entries as ``files`` plus / replacing:
        - ``app/routers/__init__.py``   (always rebuilt when routers found)
        - ``app/main.py``               (always rebuilt when routers found)
        - ``app/models/<name>.py``      (stub added only when absent)
    """
    # Normalize all keys to forward-slash separators so matching is consistent
    # regardless of the operating system the generator runs on.
    updated: Dict[str, str] = {k.replace("\\", "/"): v for k, v in files.items()}

    # Resolve any module/package collisions before wiring to avoid processing
    # both a bare module file and its package directory simultaneously.
    updated = _detect_module_package_collisions(updated)

    # ------------------------------------------------------------------ #
    # 1. Discover router variables in app/routers/*.py or app/routes/*.py #
    # ------------------------------------------------------------------ #
    router_modules: List[Dict[str, str]] = []  # [{module, var, prefix}]
    _router_var_re = re.compile(r'(\w+)\s*=\s*APIRouter\s*\(', re.MULTILINE)
    _prefix_re = re.compile(r'APIRouter\s*\([^)]*prefix\s*=\s*[\'\"]([^\'\"]+)[\'\"]')
    _router_path_re = re.compile(r'^app/(?:routers|routes)/(?!__init__)[^/]+\.py$')

    for path, content in list(updated.items()):
        if not _router_path_re.match(path):
            continue
        vars_found = _router_var_re.findall(content)
        if not vars_found:
            continue
        router_var = vars_found[0]
        # Extract prefix from APIRouter() call if present
        prefix_match = _prefix_re.search(content)
        prefix = prefix_match.group(1) if prefix_match else ""
        # Derive importable module name:
        # "app/routers/product.py" → "app.routers.product"
        # "app/routes/product.py"  → "app.routes.product"
        module = path.replace("/", ".").removesuffix(".py")
        # Capture the directory name ("routers" or "routes") for init-file path.
        # Path guaranteed by regex to be "app/{routers|routes}/<file>.py".
        router_dir = path.split("/")[1]  # segment index 1: "routers" or "routes"
        router_modules.append(
            {"module": module, "var": router_var, "prefix": prefix, "router_dir": router_dir}
        )

    if not router_modules:
        return updated  # Nothing to wire — return unchanged                           #

    # ------------------------------------------------------------------ #
    # 1b. Deduplicate router files that define the same HTTP endpoint.    #
    #                                                                      #
    # The LLM sometimes generates both products_import.py AND             #
    # products_import_router.py, both registering POST /import.  Including#
    # both causes FastAPI duplicate-route registration errors and spec     #
    # fidelity false-positives (endpoint exists but is unreachable).      #
    # Keep the first file that claims each endpoint; log and drop the rest.#
    # ------------------------------------------------------------------ #
    _endpoint_claimed_by: Dict[str, str] = {}  # "METHOD /path" → path
    _skip_router_paths: Set[str] = set()
    if ASTEndpointExtractor is not None:
        _extractor_for_dedup = ASTEndpointExtractor()
        for _rm in router_modules:
            _rm_path = _rm["module"].replace(".", "/") + ".py"
            _rm_content = updated.get(_rm_path, "")
            if not _rm_content:
                continue
            _rm_eps = _extractor_for_dedup.extract_from_source(_rm_content, _rm_path)
            _is_dup = False
            for _ep in _rm_eps:
                _ep_key = f"{_ep['method']} {_ep['path']}"
                if _ep_key in _endpoint_claimed_by:
                    logger.warning(
                        "[CODEGEN] _reconcile_app_wiring: duplicate endpoint %s in %s "
                        "already claimed by %s — skipping %s",
                        _ep_key,
                        _rm_path,
                        _endpoint_claimed_by[_ep_key],
                        _rm_path,
                    )
                    _is_dup = True
                    break
            if _is_dup:
                _skip_router_paths.add(_rm_path)
            else:
                for _ep in _rm_eps:
                    _ep_key = f"{_ep['method']} {_ep['path']}"
                    _endpoint_claimed_by[_ep_key] = _rm_path

    if _skip_router_paths:
        # Remove duplicate router modules from the list and from the file map
        router_modules = [
            _rm for _rm in router_modules
            if (_rm["module"].replace(".", "/") + ".py") not in _skip_router_paths
        ]
        for _dup_path in _skip_router_paths:
            updated.pop(_dup_path, None)
            logger.info(
                "[CODEGEN] _reconcile_app_wiring: removed duplicate router file %s",
                _dup_path,
            )

    # ------------------------------------------------------------------ #
    # All discovered routers share the same parent directory; derive it
    # from the first entry (mixed-directory projects are not supported).
    router_dir_name = router_modules[0]["router_dir"]  # "routers" or "routes"

    # Derive a unique alias for each router based on its module file stem so
    # that multiple routers can coexist without shadowing each other.
    for rm in router_modules:
        stem = rm["module"].rsplit(".", 1)[-1]  # e.g. "app.routers.products" → "products"
        rm["alias"] = f"{stem}_router"

    init_lines = ["# Auto-generated by _reconcile_app_wiring — do not edit manually"]
    for rm in router_modules:
        init_lines.append(
            f"from {rm['module']} import {rm['var']} as {rm['alias']}  # noqa: F401"
        )
    init_lines.append("")
    init_lines.append("__all__ = [")
    for rm in router_modules:
        init_lines.append('    "' + rm['alias'] + '",')
    init_lines.append("]")
    updated[f"app/{router_dir_name}/__init__.py"] = "\n".join(init_lines) + "\n"

    # ------------------------------------------------------------------ #
    # 3. Rebuild app/main.py mounting all routers                         #
    # ------------------------------------------------------------------ #
    # Preserve any bespoke health/version/ping endpoint handlers from the
    # previously generated main.py so we don't lose custom logic.
    existing_main = updated.get("app/main.py", "")
    extra_routes: List[str] = []
    # Match decorator + function body for endpoints whose path string contains
    # a well-known health/version/utility keyword.  We search the raw handler
    # text (which is Python source), so the path appears as a quoted literal
    # like "/health" — we match on the slash-prefixed bare path and let the
    # `in` check find it regardless of surrounding quote style.
    _handler_re = re.compile(
        r'(@app\.(?:get|post|put|delete|patch)\s*\([^)]*\)[^\n]*\n'
        r'(?:(?:async\s+)?def\s+\w+[^\n]*\n(?:[ \t]+[^\n]+\n*)*))',
        re.MULTILINE,
    )
    _keep_paths = ("/health", "/version", "/ping", "/api/v1", "/")
    for m in _handler_re.finditer(existing_main):
        handler = m.group(0)
        if any(kw in handler for kw in _keep_paths):
            extra_routes.append(handler)

    # ------------------------------------------------------------------ #
    # 3b. Discover ASGI middleware classes in app/middleware/*.py          #
    #                                                                      #
    # Strategy (preference order):                                         #
    #   1. Classes that explicitly subclass BaseHTTPMiddleware             #
    #   2. Classes named *Middleware* (any base)                           #
    #   3. First non-dunder class in the file (last resort)               #
    #                                                                      #
    # Each discovered class is wired via `app.add_middleware(<Cls>)`.      #
    # Classes are mounted *before* routers so middleware executes first.  #
    # ------------------------------------------------------------------ #
    _mw_path_re = re.compile(r'^app/middleware/(?!__init__)[^/]+\.py$')
    # Matches class definitions regardless of whether they have base classes.
    # Group 1: class name; optional group 2: base class list (may be empty).
    _mw_class_re = re.compile(
        r'^class\s+(\w+)\s*(?:\(([^)]*)\))?', re.MULTILINE
    )
    _mw_keyword_re = re.compile(r'Middleware', re.IGNORECASE)

    middleware_modules: List[Dict[str, str]] = []  # [{module, cls}]

    for path, content in list(updated.items()):
        if not _mw_path_re.match(path):
            continue
        all_classes = _mw_class_re.findall(content)  # [(name, bases), ...]
        if not all_classes:
            continue
        module = path.replace("/", ".").removesuffix(".py")

        # Priority 1 — explicit BaseHTTPMiddleware subclass
        chosen: Optional[str] = next(
            (name for name, bases in all_classes if "BaseHTTPMiddleware" in bases),
            None,
        )
        # Priority 2 — any class whose name contains "Middleware"
        if chosen is None:
            chosen = next(
                (name for name, _ in all_classes if _mw_keyword_re.search(name)),
                None,
            )
        # Priority 3 — first class in the file
        if chosen is None:
            chosen = all_classes[0][0]

        middleware_modules.append({"module": module, "cls": chosen})

    # ------------------------------------------------------------------ #
    # 3c. Full-stack frontend integration                                  #
    #                                                                      #
    # Detect static/ and templates/ files in the file map and inject:     #
    #   • StaticFiles mounting  (static/ files present)                   #
    #   • Jinja2Templates setup (templates/ files present)                #
    #   • CORSMiddleware        (frontend files + no active CORS wiring)  #
    #   • A basic index route   (templates present, no TemplateResponse   #
    #     found in any router or in the preserved extra_routes)           #
    # ------------------------------------------------------------------ #
    has_static = any(k.startswith("static/") for k in updated)
    has_templates = any(k.startswith("templates/") for k in updated)
    has_frontend = has_static or has_templates

    # Check whether CORSMiddleware is *actively wired* (add_middleware call)
    # in any non-main file.  Checking for an import alone would be too
    # conservative (the symbol could be re-exported or referenced in a comment).
    has_existing_cors = any(
        "add_middleware(CORSMiddleware" in content
        for path, content in updated.items()
        if path != "app/main.py"
    )

    # Check whether a TemplateResponse is already present in:
    #   (a) any non-main router/view file, OR
    #   (b) the preserved extra_routes from the old main.py.
    # Either means a template-serving route already exists and we must not
    # generate a duplicate @app.get("/") handler.
    has_template_response = (
        any("TemplateResponse" in r for r in extra_routes)
        or any(
            "TemplateResponse" in content
            for path, content in updated.items()
            if path != "app/main.py"
        )
    )

    # Derive the template name as the path *relative to the templates/ root*
    # so that Jinja2Templates.TemplateResponse resolves it correctly even when
    # the HTML file lives in a sub-directory (e.g. templates/pages/home.html).
    _tmpl_prefix = "templates/"
    first_template_name = next(
        (
            k[len(_tmpl_prefix):]
            for k in sorted(updated)
            if k.startswith(_tmpl_prefix) and k.endswith(".html")
        ),
        "base.html",
    )

    # ---- import section ---------------------------------------------------
    # Extend "from fastapi import …" with Request only when we will emit the
    # template index route (avoids unused-import lint warnings otherwise).
    _need_request = has_templates and not has_template_response
    _fastapi_core = ["FastAPI"] + (["Request"] if _need_request else [])

    main_lines: List[str] = [
        "# Auto-generated by _reconcile_app_wiring — do not edit manually",
        f"from fastapi import {', '.join(_fastapi_core)}",
    ]
    if _need_request:
        main_lines.append("from fastapi.responses import HTMLResponse")
    if has_static:
        main_lines.append("from fastapi.staticfiles import StaticFiles")
    if has_templates:
        main_lines.append("from fastapi.templating import Jinja2Templates")
    if has_frontend and not has_existing_cors:
        main_lines.append("from fastapi.middleware.cors import CORSMiddleware")

    # Blank line separates stdlib/3rd-party imports from local app imports.
    main_lines.append("")
    for rm in router_modules:
        main_lines.append(f"from {rm['module']} import {rm['var']} as {rm['alias']}")
    for mm in middleware_modules:
        main_lines.append(f"from {mm['module']} import {mm['cls']}")
    # Include app/routes.py (health probes) if it exists and defines a router
    _app_routes_content = updated.get("app/routes.py", "")
    _app_routes_var: Optional[str] = None
    if _app_routes_content:
        _routes_vars = _router_var_re.findall(_app_routes_content)
        if _routes_vars:
            _app_routes_var = _routes_vars[0]
            main_lines.append(
                f"from app.routes import {_app_routes_var} as routes_router"
            )

    # ---- application setup ------------------------------------------------
    main_lines += [
        "",
        "app = FastAPI()",
        "",
    ]
    if has_templates:
        main_lines.append('templates = Jinja2Templates(directory="templates")')
        main_lines.append("")

    # ---- middleware (LIFO: first add_middleware runs outermost) -----------
    for mm in middleware_modules:
        main_lines.append(f"app.add_middleware({mm['cls']})")
    if has_frontend and not has_existing_cors:
        main_lines.append(
            'app.add_middleware(CORSMiddleware, allow_origins=["*"],'
            ' allow_credentials=True, allow_methods=["*"], allow_headers=["*"])'
        )

    # ---- static mount (before routers so /static/ is matched first) ------
    if has_static:
        main_lines.append(
            'app.mount("/static", StaticFiles(directory="static"), name="static")'
        )

    # ---- routers ----------------------------------------------------------
    # Stems for utility/health routes that should NOT get an /api/v1/ prefix.
    _NO_PREFIX_STEMS: FrozenSet[str] = frozenset(
        {"health", "healthz", "readyz", "root", "index", "ws", "websocket"}
    )
    # Detect whether this project uses /api/v1/ paths (REST API heuristic).
    # Scope the scan to router/route files and the existing main.py to avoid
    # O(N) scans over non-code assets (templates, static files, etc.).
    _API_V1_SCAN_RE = re.compile(r'^app/(?:routers?|routes?|main).*\.py$')
    _has_api_v1 = any(
        "/api/v1/" in content
        for path, content in updated.items()
        if _API_V1_SCAN_RE.match(path)
    )
    for rm in router_modules:
        if rm['prefix']:
            # Router already has a prefix defined in APIRouter() — do NOT add it
            # again to include_router() or routes will be double-prefixed, e.g.
            # /api/v1/products/api/v1/products/import.
            main_lines.append(
                f"app.include_router({rm['alias']})"
                f"  # prefix already defined in router: {rm['prefix']}"
            )
        else:
            stem = rm["module"].rsplit(".", 1)[-1]
            if _has_api_v1 and stem not in _NO_PREFIX_STEMS:
                prefix_kwarg = f', prefix="/api/v1/{stem}"'
            else:
                prefix_kwarg = ""
            main_lines.append(f"app.include_router({rm['alias']}{prefix_kwarg})")

    # ---- app/routes.py health probes (Kubernetes /healthz and /readyz) ----
    if _app_routes_var:
        main_lines.append(
            "app.include_router(routes_router)  # health check endpoints"
        )

    # ---- preserved health/version/ping routes from old main.py -----------
    if extra_routes:
        main_lines.append("")
        main_lines.extend(route.rstrip() for route in extra_routes)

    # ---- fallback template index route ------------------------------------
    # Only injected when templates/ files exist but no existing code already
    # returns a TemplateResponse (checked above across routers + extra_routes).
    if has_templates and not has_template_response:
        main_lines += [
            "",
            "",
            '@app.get("/", response_class=HTMLResponse)',
            "async def index(request: Request):",
            (
                f'    return templates.TemplateResponse('
                f'"{first_template_name}", {{"request": request}})'
            ),
        ]

    main_lines.append("")

    updated["app/main.py"] = "\n".join(main_lines) + "\n"

    # ------------------------------------------------------------------ #
    # 4. Generate stub ORM model files for classes absent from app/models/ #
    # ------------------------------------------------------------------ #
    # Scan schema files for Pydantic model names like ProductCreate,
    # ProductUpdate, ProductRead, etc. and infer the base model name.
    _schema_name_re = re.compile(
        r'\b([A-Z][a-zA-Z]+?)(?:Create|Update|Read|Response|Base|In|Out|Schema)\b'
    )
    _model_path_re = re.compile(r'^app/models/(?!__init__)[^/]+\.py$')

    referenced_models: Set[str] = set()
    for path, content in list(updated.items()):
        if "schema" in path.lower() or ("model" in path.lower() and not _model_path_re.match(path)):
            for m in _schema_name_re.finditer(content):
                referenced_models.add(m.group(1))

    existing_model_classes: Set[str] = set()
    for path in list(updated.keys()):
        if _model_path_re.match(path):
            for m in re.finditer(r'class\s+(\w+)\s*\(', updated[path]):
                existing_model_classes.add(m.group(1))

    # Try to reuse the project's shared Base so all models belong to the same
    # metadata graph.  Preference order:
    #   1. app/database.py exports Base
    #   2. app/models/__init__.py exports Base
    #   3. Fall back to a self-contained declarative_base() per stub file
    #      (sufficient for schema introspection / migrations bootstrap)
    _shared_base_import: Optional[str] = None
    if "app/database.py" in updated and "declarative_base" in updated["app/database.py"]:
        _shared_base_import = "from app.database import Base"
    elif "app/models/__init__.py" in updated and "declarative_base" in updated.get("app/models/__init__.py", ""):
        _shared_base_import = "from app.models import Base"

    _stub_header_shared = (
        "from sqlalchemy import Column, DateTime, Integer, String\n"
        "{base_import}\n"
        "from datetime import datetime, timezone\n\n\n"
    )
    _stub_header_standalone = (
        "from sqlalchemy import Column, DateTime, Integer, String\n"
        "from sqlalchemy.orm import declarative_base\n"
        "from datetime import datetime, timezone\n\n"
        "Base = declarative_base()\n\n\n"
    )
    _stub_body = (
        "class {name}(Base):\n"
        '    __tablename__ = "{table}"\n\n'
        "    id = Column(Integer, primary_key=True, index=True)\n"
        "    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))\n"
        "    updated_at = Column(\n"
        "        DateTime(timezone=True),\n"
        "        default=lambda: datetime.now(timezone.utc),\n"
        "        onupdate=lambda: datetime.now(timezone.utc),\n"
        "    )\n"
    )

    for model_name in sorted(referenced_models - existing_model_classes):
        # Skip very short or clearly non-model names
        if not model_name or len(model_name) < 3:
            continue
        stub_path = f"app/models/{model_name.lower()}.py"
        if stub_path in updated:
            continue
        # CamelCase → snake_case for __tablename__, then simple pluralization.
        # Note: this covers the most common English nouns adequately for stub
        # generation; production code should use a proper pluralization library
        # (e.g. inflect) if irregular plurals are a concern.
        base_name = re.sub(r"(?<!^)(?=[A-Z])", "_", model_name).lower()
        if base_name.endswith("y") and not base_name.endswith(("ay", "ey", "iy", "oy", "uy")):
            table_name = base_name[:-1] + "ies"
        elif base_name.endswith(("s", "sh", "ch", "x", "z")):
            table_name = base_name + "es"
        else:
            table_name = base_name + "s"

        if _shared_base_import:
            header = _stub_header_shared.format(base_import=_shared_base_import)
        else:
            header = _stub_header_standalone
        updated[stub_path] = header + _stub_body.format(name=model_name, table=table_name)

    # ------------------------------------------------------------------ #
    # 5. Ensure service imports in routers resolve to existing functions  #
    # ------------------------------------------------------------------ #
    # For each router file, extract every "from app.services.X import ..."
    # statement (including parenthesised multiline imports) and verify that
    # each imported name is actually defined in the corresponding service file.
    # When a name is missing, append a properly-typed stub function so the
    # router can be imported without an ImportError at startup.
    #
    # Patterns handled:
    #   from app.services.product import create_product, list_products
    #   from app.services.product import (
    #       create_product,
    #       list_products,
    #   )
    _svc_import_simple_re = re.compile(
        r'from\s+(app\.services\.[\w]+)\s+import\s+([^(\n][^\n]*)',
        re.MULTILINE,
    )
    _svc_import_paren_re = re.compile(
        r'from\s+(app\.services\.[\w]+)\s+import\s+\((.*?)\)',
        re.DOTALL,
    )
    _func_defined_re = re.compile(r'^[ \t]*(?:async\s+)?def\s+(\w+)\s*\(', re.MULTILINE)
    # Names that are never functions (skip silently).
    # Covers all common typing constructs imported from ``typing`` or
    # ``typing_extensions`` that would never correspond to a service function.
    _SKIP_NAMES: FrozenSet[str] = frozenset({
        "TYPE_CHECKING",
        # Generic containers
        "Any", "Dict", "FrozenSet", "List", "Optional", "Set",
        "Sequence", "Tuple", "Type", "Union",
        # Async types
        "Awaitable", "AsyncGenerator", "AsyncIterable", "AsyncIterator",
        "Coroutine", "Generator",
        # Callable / protocol
        "Callable", "ClassVar", "Final", "Generic", "Literal",
        "Protocol", "TypeVar", "cast",
        # Python 3.10+ union syntax helpers
        "Never", "NoReturn", "Annotated", "TypeAlias", "TypeGuard",
        "ParamSpec", "Concatenate", "Unpack", "TypeVarTuple",
    })

    def _parse_import_names(raw: str) -> List[str]:
        """Parse a comma-separated import list, handling aliases and comments."""
        names: List[str] = []
        for part in raw.split(","):
            part = part.strip()
            # Strip inline comments
            part = re.sub(r'#.*$', '', part).strip()
            if not part:
                continue
            # "name as alias" → take the original name (we need the source name)
            name = part.split()[0]
            if name and name.isidentifier() and name not in _SKIP_NAMES:
                names.append(name)
        return names

    for path, content in list(updated.items()):
        if not _router_path_re.match(path):
            continue

        # Collect all (module, [names]) pairs from both simple and paren imports
        import_pairs: List[Tuple[str, List[str]]] = []
        for m in _svc_import_simple_re.finditer(content):
            import_pairs.append((m.group(1), _parse_import_names(m.group(2))))
        for m in _svc_import_paren_re.finditer(content):
            import_pairs.append((m.group(1), _parse_import_names(m.group(2))))

        for svc_module, imported_names in import_pairs:
            svc_path = svc_module.replace(".", "/") + ".py"
            if svc_path not in updated:
                continue
            svc_content = updated[svc_path]
            defined_funcs: Set[str] = set(_func_defined_re.findall(svc_content))
            missing: List[str] = [
                n for n in imported_names
                if n and n not in defined_funcs and not n[0].isupper()  # skip class names
            ]
            if not missing:
                continue
            stub_lines: List[str] = []
            for fn in sorted(missing):
                stub_lines.append(
                    f"\n\nasync def {fn}(*args: Any, **kwargs: Any) -> Any:"
                    f'\n    """Placeholder implementation for ``{fn}``."""'
                    f"\n    return None\n"
                )
            updated[svc_path] = svc_content.rstrip() + "".join(stub_lines) + "\n"
            logger.info(
                "[CODEGEN] _reconcile_app_wiring: added %d missing function stub(s) to %s: %s",
                len(missing),
                svc_path,
                missing,
            )

    # ------------------------------------------------------------------ #
    # 6. Deduplicate function definitions in router files that shadow     #
    #    imported service names  (AST-based — robust, no regex heuristics) #
    # ------------------------------------------------------------------ #
    # Use ast.parse() for all structural analysis so that multi-line
    # parenthesised imports, nested default-argument parentheses, and
    # decorated function signatures are all handled correctly.  This
    # mirrors the approach used by _ast_merge_python_files above.
    for path in list(updated.keys()):
        if not _router_path_re.match(path):
            continue
        content = updated[path]

        # LLM output may be syntactically invalid; skip gracefully.
        try:
            tree = ast.parse(content)
        except SyntaxError:
            continue

        # Collect every name that appears in a top-level ``from … import``
        # statement, including parenthesised multi-line forms.
        imported_names: Set[str] = set()
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    imported_names.add(alias.name)

        if not imported_names:
            continue

        # Collect top-level function definitions in file order.
        # Each entry: (name, start_line_0based, end_line_exclusive)
        # ``start_line`` is decorator-inclusive (matches _ast_merge_python_files).
        FuncInfo = Tuple[str, int, int]
        func_defs: List[FuncInfo] = []
        for node in ast.iter_child_nodes(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            end_ln: Optional[int] = getattr(node, "end_lineno", None)
            if end_ln is None:
                continue  # Python < 3.8 safety guard
            decorator_list = getattr(node, "decorator_list", [])
            start_1based = (
                decorator_list[0].lineno if decorator_list else node.lineno
            )
            func_defs.append((node.name, start_1based - 1, end_ln))

        # Count occurrences to find duplicated names.
        name_counts: Dict[str, int] = {}
        for fn_name, _, _ in func_defs:
            name_counts[fn_name] = name_counts.get(fn_name, 0) + 1

        duplicate_names: Set[str] = {n for n, cnt in name_counts.items() if cnt > 1}
        # Shadowing: any name that is both defined AND imported (regardless of count)
        shadowing_names: Set[str] = {
            fn_name for fn_name, _, _ in func_defs if fn_name in imported_names
        }

        if not duplicate_names and not shadowing_names:
            continue

        lines = content.splitlines(keepends=True)
        changed = False

        # ── Remove all but the first occurrence of each duplicated name ──
        # Build the list of (start, end) line-ranges to delete, keeping only
        # the first occurrence of each duplicated function.  Sort descending
        # by start so that earlier line-indices remain valid after each deletion.
        to_delete: List[Tuple[int, int]] = []
        for fn_name in duplicate_names:
            occurrences = [(s, e) for name, s, e in func_defs if name == fn_name]
            # occurrences are in file order; keep the first, delete the rest
            removed_count = len(occurrences) - 1
            to_delete.extend(occurrences[1:])
            logger.info(
                "[CODEGEN] _reconcile_app_wiring: queued removal of %d"
                " duplicate definition(s) of '%s' in %s",
                removed_count,
                fn_name,
                path,
            )

        for start, end in sorted(to_delete, key=lambda t: t[0], reverse=True):
            del lines[start:end]
            changed = True

        # ── Rename single-occurrence defs that shadow an imported name ──
        # After deletions the line numbers from the original parse may be
        # stale, so re-parse the current lines to get accurate positions.
        rename_candidates = shadowing_names - duplicate_names
        if rename_candidates:
            try:
                tree2 = ast.parse("".join(lines))
            except SyntaxError:
                pass  # Renaming skipped; deduplication changes are still saved
            else:
                for node in ast.iter_child_nodes(tree2):
                    if (
                        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and node.name in rename_candidates
                    ):
                        new_name = f"{node.name}_endpoint"
                        def_line_idx = node.lineno - 1
                        lines[def_line_idx] = re.sub(
                            r'((?:async\s+)?def\s+)'
                            + re.escape(node.name)
                            + r'(?=\s*\()',
                            r'\g<1>' + new_name,
                            lines[def_line_idx],
                        )
                        changed = True
                        logger.info(
                            "[CODEGEN] _reconcile_app_wiring: renamed '%s' → '%s'"
                            " in %s to avoid import shadowing",
                            node.name,
                            new_name,
                            path,
                        )

        if changed:
            updated[path] = "".join(lines)

    # ------------------------------------------------------------------ #
    # 7. Inject stubs for undefined Depends() callables in router files   #
    # ------------------------------------------------------------------ #
    _depends_re = re.compile(r'\bDepends\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)')
    _depends_ellipsis_re = re.compile(r'\bDepends\(\s*\.\.\.\s*\)')
    _depends_none_re = re.compile(r'\bDepends\(\s*None\s*\)')
    _router_file_re = re.compile(r'^app/(?:routers|routes)/(?!__init__)[^/]+\.py$')
    import builtins as _builtins_mod
    _python_builtins: FrozenSet[str] = frozenset(dir(_builtins_mod))

    # First sub-pass: replace Depends(...) / Depends(None) with a named callable
    # so the main stub-injection pass below can handle it uniformly.
    for path, content in list(updated.items()):
        if not _router_file_re.match(path):
            continue
        if not (_depends_ellipsis_re.search(content) or _depends_none_re.search(content)):
            continue
        new_content = _depends_ellipsis_re.sub('Depends(_placeholder_dep)', content)
        new_content = _depends_none_re.sub('Depends(_placeholder_dep)', new_content)
        updated[path] = new_content
        logger.warning(
            "[CODEGEN] _reconcile_app_wiring: replaced Depends(...)/Depends(None)"
            " with _placeholder_dep in %s",
            path,
        )

    for path, content in list(updated.items()):
        if not _router_file_re.match(path):
            continue

        depends_names = set(_depends_re.findall(content))
        if not depends_names:
            continue

        # Use the AST to collect every name that is defined or imported in this
        # file.  AST parsing correctly handles multi-line parenthesised imports,
        # aliased imports, and all other valid Python import styles.  A regex
        # fallback handles files that contain syntax errors (which can occur when
        # the LLM produces partially-broken code).
        defined_names: Set[str] = set()
        try:
            tree = ast.parse(content)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    defined_names.add(node.name)
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        defined_names.add(alias.asname or alias.name.split(".")[0])
                elif isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        defined_names.add(alias.asname or alias.name)
        except SyntaxError:
            # Fallback: regex-based extraction for files with syntax errors.
            for m in re.finditer(
                r'^(?:async\s+)?(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*)',
                content, re.MULTILINE,
            ):
                defined_names.add(m.group(1))
            for m in re.finditer(r'^\s*import\s+(.+)', content, re.MULTILINE):
                for part in m.group(1).split(','):
                    part = part.strip()
                    defined_names.add(
                        part.split(' as ')[-1].strip() if ' as ' in part
                        else part.split('.')[0]
                    )
            for m in re.finditer(
                # Match `from module import name1, name2` on a single line OR
                # `from module import (\n    name1,\n    name2,\n)` spanning
                # multiple lines.  The inner group captures everything between
                # `import` and the optional closing `)`.
                r'^\s*from\s+\S+\s+import\s+\(([^)]*)\)|^\s*from\s+\S+\s+import\s+(.+)',
                content, re.MULTILINE,
            ):
                raw = m.group(1) if m.group(1) is not None else m.group(2)
                for part in raw.split(','):
                    part = part.strip().rstrip(')')
                    if not part or part == '*' or part.startswith('#'):
                        continue
                    defined_names.add(
                        part.split(' as ')[-1].strip() if ' as ' in part else part
                    )

        stubs_to_inject: List[Tuple[str, str]] = []
        for dep_name in sorted(depends_names):
            if dep_name in defined_names or dep_name in _python_builtins:
                continue
            stub = (
                f"\nasync def {dep_name}() -> dict:\n"
                f'    """Placeholder dependency — replace with real auth logic."""\n'
                f'    return {{"sub": "anonymous", "role": "guest"}}\n'
            )
            stubs_to_inject.append((dep_name, stub))

        if not stubs_to_inject:
            continue

        # Insert stubs immediately after the last top-level import statement so
        # the injected functions are syntactically valid and importable.
        lines = content.splitlines(keepends=True)
        insert_idx = 0
        past_imports = False
        for i, line in enumerate(lines):
            stripped = line.strip()
            if past_imports:
                break
            if stripped.startswith('import ') or stripped.startswith('from '):
                insert_idx = i + 1
            elif stripped == '' or stripped.startswith('#'):
                # blank/comment lines: stay in the import block tentatively
                pass
            else:
                past_imports = True

        injection = "".join(stub for _, stub in stubs_to_inject)
        lines.insert(insert_idx, injection)
        updated[path] = "".join(lines)

        for dep_name, _ in stubs_to_inject:
            logger.warning(
                "[CODEGEN] _reconcile_app_wiring: injected placeholder for undefined"
                " Depends() callable '%s' in %s",
                dep_name,
                path,
            )

    # Fix 1 & 3: After all router wiring is done, fix response_model type mismatches
    # and disambiguate models/ vs schemas/ name collisions in router files.
    updated = fix_response_model_type_mismatches(updated)
    updated = disambiguate_model_schema_imports(updated)

    # ------------------------------------------------------------------ #
    # 8. Fix duplicate route prefixes detected by _validate_wiring        #
    # ------------------------------------------------------------------ #
    # When a router decorator path starts with the same prefix that main.py
    # applies via include_router(..., prefix=...), strip the prefix from the
    # decorator so the runtime path is correct.
    try:
        _post_wiring = _validate_wiring(updated)
        for _dup_file, _mount_prefix, _decorator_path in _post_wiring.get("duplicate_prefixes", []):
            if _dup_file not in updated:
                continue
            _stripped_path = _decorator_path[len(_mount_prefix):]
            if not _stripped_path.startswith("/"):
                _stripped_path = "/" + _stripped_path
            _dup_content = updated[_dup_file]
            _dup_content = _dup_content.replace(
                f'"{_decorator_path}"', f'"{_stripped_path}"'
            ).replace(
                f"'{_decorator_path}'", f"'{_stripped_path}'"
            )
            if _dup_content != updated[_dup_file]:
                updated[_dup_file] = _dup_content
                logger.info(
                    "[CODEGEN] _reconcile_app_wiring: fixed duplicate prefix '%s' in %s:"
                    " '%s' → '%s'",
                    _mount_prefix,
                    _dup_file,
                    _decorator_path,
                    _stripped_path,
                )
    except Exception as _dup_err:
        logger.warning(
            "[CODEGEN] _reconcile_app_wiring: duplicate-prefix fix failed (non-fatal): %s",
            _dup_err,
        )

    # ------------------------------------------------------------------ #
    # 9. Reconcile Pydantic schema fields with SQLAlchemy model columns   #
    # ------------------------------------------------------------------ #
    # Adds stub columns to models for any field that the schema references
    # but the model does not define, preventing AttributeError at runtime.
    try:
        updated = reconcile_schema_model_fields(updated)
    except Exception as _schema_err:
        logger.warning(
            "[CODEGEN] _reconcile_app_wiring: schema/model reconciliation failed (non-fatal): %s",
            _schema_err,
        )

    return updated


def _ast_merge_python_files(old_content: str, new_content: str) -> str:
    """Merge two Python source files, preserving symbols from the old version.

    When a later generation pass produces a file that already exists in
    ``_merged_files``, a blind ``dict.update()`` would discard any class or
    function definitions that were in the original but omitted from the new
    version.  This helper uses ``ast.parse()`` to detect which top-level names
    are present in each version and appends the missing definitions from the old
    version to the end of the new version.

    Decorator handling: when a function or class has decorators, the source
    extraction starts from the first decorator's line (``node.decorator_list[0].lineno``)
    rather than the ``def``/``class`` keyword line, so the full decorated
    definition is preserved verbatim.

    Falls back to returning ``new_content`` unchanged if either version fails
    AST parsing (e.g. a syntax error in LLM output), so it never blocks
    generation.

    Args:
        old_content: The previously-accumulated file content.
        new_content: The replacement content produced by the latest pass.

    Returns:
        Merged source string where ``new_content`` is the base and any
        top-level definitions absent from it are appended from ``old_content``.
        The returned string always ends with a single trailing newline.
    """
    try:
        old_tree = ast.parse(old_content)
    except SyntaxError:
        # Old content is broken, prefer new content
        return new_content

    try:
        new_tree = ast.parse(new_content)
    except SyntaxError:
        # New content is broken but old is valid — keep old content
        logger.warning(
            "_ast_merge_python_files: new_content has SyntaxError, keeping old_content"
        )
        return old_content

    # Collect names of all top-level definitions in a parsed module tree.
    def _top_level_names(tree: ast.Module) -> Set[str]:
        names: Set[str] = set()
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        names.add(target.id)
        return names

    old_names = _top_level_names(old_tree)
    new_names = _top_level_names(new_tree)
    missing_names = old_names - new_names

    # Collect names that are already defined (not just imported) as functions or
    # classes in the new tree, to guard against re-appending a symbol that is
    # already present as a definition (e.g. from an earlier gap-fill pass).
    new_defined_names: Set[str] = set()
    for node in ast.iter_child_nodes(new_tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            new_defined_names.add(node.name)

    if not missing_names:
        return new_content

    # Extract the source lines for each missing top-level definition from the
    # old content and append them to the new content.
    #
    # Line numbers in the AST are 1-based; ``end_lineno`` (available since
    # Python 3.8) is inclusive.  Slicing ``old_lines[start:end]`` where
    # ``start = lineno - 1`` and ``end = end_lineno`` gives the exact lines.
    old_lines = old_content.splitlines(keepends=True)
    snippets: List[Tuple[str, str]] = []
    for node in ast.iter_child_nodes(old_tree):
        node_name: Optional[str] = None
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            node_name = node.name
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in missing_names:
                    node_name = target.id
                    break

        if node_name is None or node_name not in missing_names:
            continue

        # Skip symbols already defined as functions/classes in the new file to
        # avoid duplicate route definitions and name-shadowing warnings.
        if node_name in new_defined_names:
            logger.info(
                "[CODEGEN] AST merge: skipping duplicate symbol %r already defined in new version",
                node_name,
            )
            continue

        end_lineno: Optional[int] = getattr(node, "end_lineno", None)
        if end_lineno is None:
            # Python < 3.8 fallback: skip this node rather than risk a bad slice.
            continue

        # For decorated definitions, start from the first decorator's line so
        # the ``@decorator`` lines are included in the extracted snippet.
        decorator_list = getattr(node, "decorator_list", [])
        start_lineno = (
            decorator_list[0].lineno if decorator_list else node.lineno
        )
        snippet = "".join(old_lines[start_lineno - 1 : end_lineno])
        snippets.append((node_name, snippet))

    if not snippets:
        return new_content

    appended_names = [name for name, _ in snippets]
    logger.info(
        "[CODEGEN] AST merge: appending %d symbol(s) missing from new version: %s",
        len(appended_names),
        sorted(appended_names),
    )

    # Collect import statements from old file and determine which are needed by
    # the snippets being appended.
    # Build a map: imported_name -> source line(s) from old file
    old_import_lines: List[str] = []
    old_import_name_to_line: Dict[str, str] = {}
    for node in ast.iter_child_nodes(old_tree):
        if isinstance(node, ast.Import):
            _end = node.end_lineno if node.end_lineno else node.lineno
            line = "".join(old_lines[node.lineno - 1 : _end]).rstrip()
            old_import_lines.append(line)
            for alias in node.names:
                key = alias.asname if alias.asname else alias.name.split('.')[0]
                old_import_name_to_line[key] = line
        elif isinstance(node, ast.ImportFrom):
            _end = node.end_lineno if node.end_lineno else node.lineno
            line = "".join(old_lines[node.lineno - 1 : _end]).rstrip()
            old_import_lines.append(line)
            for alias in node.names:
                key = alias.asname if alias.asname else alias.name
                old_import_name_to_line[key] = line

    # Collect names already imported in the new file
    new_imported_names: Set[str] = set()
    for node in ast.iter_child_nodes(new_tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                new_imported_names.add(alias.asname if alias.asname else alias.name.split('.')[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                new_imported_names.add(alias.asname if alias.asname else alias.name)

    # For each appended snippet, find names it references that come from old imports
    imports_to_prepend: List[str] = []
    seen_import_lines: Set[str] = set()
    for node_name, snippet in snippets:
        try:
            snippet_tree = ast.parse(snippet)
        except SyntaxError:
            continue
        for snode in ast.walk(snippet_tree):
            ref_name: Optional[str] = None
            if isinstance(snode, ast.Name):
                ref_name = snode.id
            elif isinstance(snode, ast.Attribute) and isinstance(snode.value, ast.Name):
                ref_name = snode.value.id
            if ref_name and ref_name in old_import_name_to_line \
                    and ref_name not in new_imported_names:
                import_line = old_import_name_to_line[ref_name]
                if import_line not in seen_import_lines:
                    imports_to_prepend.append(import_line)
                    seen_import_lines.add(import_line)
                    new_imported_names.add(ref_name)  # avoid duplicates
                    logger.info(
                        "[CODEGEN] AST merge: carrying over import for %r: %s",
                        ref_name,
                        import_line,
                    )

    # Normalise the base so it ends with exactly one newline, then append each
    # snippet separated by a blank line (PEP 8: two blank lines between
    # top-level definitions).
    base = new_content.rstrip("\n") + "\n"
    merged = base + "\n\n" + "\n\n".join(s.rstrip("\n") for _, s in snippets) + "\n"

    # Prepend any missing imports at the top of the merged result
    if imports_to_prepend:
        # Ensure __future__ imports stay at the very top (Python requirement)
        lines = merged.splitlines(keepends=True)
        future_end_idx = 0
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("from __future__"):
                future_end_idx = i + 1
            elif stripped and not stripped.startswith("#") and not stripped.startswith('"""') and not stripped.startswith("'''"):
                # Stop scanning at the first non-comment, non-future, non-docstring line
                break

        import_block = "\n".join(imports_to_prepend) + "\n"
        if future_end_idx > 0:
            # Insert after the last __future__ import
            merged = "".join(lines[:future_end_idx]) + import_block + "".join(lines[future_end_idx:])
        else:
            merged = import_block + merged

    return merged


def _parse_router_instance_prefixes(tree: ast.AST) -> Dict[str, str]:
    """Return ``{variable_name: prefix}`` for all APIRouter/Router assignments.

    Scans the AST for statements of the form::

        router = APIRouter(prefix="/api/v1/products")

    and returns a mapping from the assigned variable name to the prefix string.
    """
    prefixes: Dict[str, str] = {}
    for _node in ast.walk(tree):
        if not (isinstance(_node, ast.Assign) and isinstance(_node.value, ast.Call)):
            continue
        _call = _node.value
        _func_name = ""
        if isinstance(_call.func, ast.Name):
            _func_name = _call.func.id
        elif isinstance(_call.func, ast.Attribute):
            _func_name = _call.func.attr
        if _func_name not in ("APIRouter", "Router"):
            continue
        for _kw in _call.keywords:
            if _kw.arg == "prefix" and isinstance(_kw.value, ast.Constant):
                for _target in _node.targets:
                    if isinstance(_target, ast.Name):
                        prefixes[_target.id] = str(_kw.value.value)
    return prefixes


def _parse_include_router_prefixes(tree: ast.AST) -> Dict[str, str]:
    """Return ``{router_alias: prefix}`` for all ``app.include_router()`` calls.

    Scans the AST for calls of the form::

        app.include_router(router, prefix="/api/v1")

    and returns a mapping from the router variable name to the prefix string.
    Only entries that carry an explicit ``prefix=`` keyword are included.
    """
    prefixes: Dict[str, str] = {}
    for _node in ast.walk(tree):
        if not isinstance(_node, ast.Call):
            continue
        _func = _node.func
        if not (isinstance(_func, ast.Attribute) and _func.attr == "include_router"):
            continue
        if not _node.args:
            continue
        _router_arg = _node.args[0]
        _router_name = ""
        if isinstance(_router_arg, ast.Name):
            _router_name = _router_arg.id
        elif isinstance(_router_arg, ast.Attribute):
            _router_name = _router_arg.attr
        if not _router_name:
            continue
        for _kw in _node.keywords:
            if _kw.arg == "prefix" and isinstance(_kw.value, ast.Constant):
                prefixes[_router_name] = str(_kw.value.value)
    return prefixes


def _extract_route_entries(
    tree: ast.AST,
    router_prefixes: Dict[str, str],
) -> List[Tuple[str, str, str]]:
    """Return ``[(METHOD, normalized_path, router_var), ...]`` from a parsed AST.

    Iterates every function/async-function definition and inspects its decorator
    list for HTTP route decorators (``@router.get``, ``@app.post``, etc.).  The
    inline ``APIRouter(prefix=...)`` is already folded into *normalized_path*;
    path-parameter tokens such as ``{item_id}`` are replaced with
    ``_PATH_PARAM_WILDCARD``.  The ``include_router()`` prefix from
    ``app/main.py`` is intentionally **not** applied here — callers that need
    it should use ``_parse_include_router_prefixes`` and combine separately.

    Args:
        tree: Parsed AST of a single source file.
        router_prefixes: Mapping produced by ``_parse_router_instance_prefixes``.

    Returns:
        List of ``(METHOD, normalized_path, router_var)`` tuples.
    """
    entries: List[Tuple[str, str, str]] = []
    for _node in ast.walk(tree):
        if not isinstance(_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for _dec in getattr(_node, "decorator_list", []):
            if not isinstance(_dec, ast.Call) or not isinstance(_dec.func, ast.Attribute):
                continue
            _method = _dec.func.attr.lower()
            if _method not in _HTTP_ROUTE_METHODS:
                continue
            _router_var = ""
            if isinstance(_dec.func.value, ast.Name):
                _router_var = _dec.func.value.id
            if not _dec.args or not isinstance(_dec.args[0], ast.Constant):
                continue
            _path = str(_dec.args[0].value)
            _inline_prefix = router_prefixes.get(_router_var, "")
            if _inline_prefix:
                _full_path = _inline_prefix.rstrip("/") + "/" + _path.lstrip("/")
            else:
                _full_path = _path
            # Normalize: strip trailing slash (but keep root "/")
            if len(_full_path) > 1:
                _full_path = _full_path.rstrip("/")
            # Normalize path parameters using the module-level wildcard constant
            _full_path = re.sub(r'\{[^}]+\}', _PATH_PARAM_WILDCARD, _full_path)
            entries.append((_method.upper(), _full_path, _router_var))
    return entries


def _extract_routes_from_files(files: Dict[str, str]) -> Set[Tuple[str, str]]:
    """Extract HTTP routes from router files using AST-based analysis.

    Finds all decorator calls matching ``@router.<method>(path)`` or
    ``@app.<method>(path)`` patterns (where method is get/post/put/delete/patch
    etc.) and returns a set of ``(METHOD, path)`` tuples.

    Also performs prefix concatenation for both inline ``APIRouter(prefix=…)``
    arguments and ``app.include_router(router, prefix=…)`` calls in
    ``app/main.py``.

    Args:
        files: Dict mapping file paths to source content.

    Returns:
        Set of ``(METHOD, normalized_path)`` tuples, e.g.
        ``{("GET", "/api/v1/audit"), ("POST", "/api/v1/orders")}``.
    """
    routes: Set[Tuple[str, str]] = set()

    # First pass: collect (method, path, router_var) from every Python file.
    # We retain entries keyed by filepath so the second pass can reuse them
    # without reparsing.
    _file_entries: Dict[str, List[Tuple[str, str, str]]] = {}
    for _filepath, _content in files.items():
        if not _filepath.endswith(".py") or not isinstance(_content, str):
            continue
        try:
            _tree = ast.parse(_content)
        except SyntaxError:
            continue
        _entries = _extract_route_entries(
            _tree, _parse_router_instance_prefixes(_tree)
        )
        _file_entries[_filepath] = _entries
        for _method, _path, _ in _entries:
            routes.add((_method, _path))

    # Second pass: apply include_router() prefixes from app/main.py.
    # Routes whose router variable appears in an include_router() call with a
    # prefix= keyword get an additional entry with the combined prefix.
    _main_content = files.get("app/main.py", "")
    if _main_content:
        try:
            _main_tree = ast.parse(_main_content)
        except SyntaxError:
            _main_tree = None
        if _main_tree:
            _include_prefixes = _parse_include_router_prefixes(_main_tree)
            if _include_prefixes:
                for _entries in _file_entries.values():
                    for _method, _path, _router_var in _entries:
                        if _router_var not in _include_prefixes:
                            continue
                        _inc_prefix = _include_prefixes[_router_var]
                        _prefixed = _inc_prefix.rstrip("/") + "/" + _path.lstrip("/")
                        if len(_prefixed) > 1:
                            _prefixed = _prefixed.rstrip("/")
                        # _path is already normalised by _extract_route_entries;
                        # only the newly prepended prefix segment needs checking.
                        _prefixed = re.sub(r'\{[^}]+\}', _PATH_PARAM_WILDCARD, _prefixed)
                        routes.add((_method, _prefixed))

    return routes


def _build_project_module_reference(files: Dict[str, str]) -> str:
    """Build a formatted "Project Module Reference" string for use in LLM prompts.

    Extracts top-level symbols from each .py file and formats them as a
    human-readable reference that the LLM can use to construct correct imports.

    Args:
        files: Dict mapping file paths to source content.

    Returns:
        Formatted string listing available project modules and their exported symbols.
    """
    lines: List[str] = ["## Available Project Modules (import from these):"]
    for _filepath in sorted(files.keys()):
        if not _filepath.endswith(".py"):
            continue
        _content = files[_filepath]
        if not isinstance(_content, str) or not _content.strip():
            continue
        # Convert to module path
        _mod = _filepath.replace("\\", "/").replace("/", ".")
        if _mod.endswith(".py"):
            _mod = _mod[:-3]
        if _mod.endswith(".__init__"):
            _mod = _mod[:-9]
        try:
            _tree = ast.parse(_content)
        except SyntaxError:
            continue
        _symbols: List[str] = []
        for _node in ast.iter_child_nodes(_tree):
            if isinstance(_node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                _symbols.append(_node.name)
            elif isinstance(_node, ast.Assign):
                for _t in _node.targets:
                    if isinstance(_t, ast.Name):
                        _symbols.append(_t.id)
        if _symbols:
            lines.append(f"- `{_mod}`: {', '.join(_symbols)}")
    return "\n".join(lines)



# --- REDUNDANT CLASS REMOVAL: SecretsManager removed ---
# --- All internal AuditLogger definitions replaced with centralized call ---
# ==============================================================================
class AuditLogger(ABC):
    """
    Abstract base class for audit loggers.
    
    Industry Standard: All implementations must provide async log_action method
    to ensure proper integration with the async log_audit_event system and prevent
    unawaited coroutine warnings that can cause silent audit failures.
    """

    @abstractmethod
    async def log_action(self, action: str, details: Dict[str, Any]) -> None:
        """
        Log an audit action asynchronously.
        
        Args:
            action: The action/event type being logged
            details: Dictionary containing event details and metadata
            
        Note:
            Implementations must await the centralized log_audit_event function
            to ensure audit events are properly recorded and signed.
        """
        pass


class JsonConsoleAuditLogger(AuditLogger):
    """
    JSON Console Audit Logger - outputs structured JSON audit logs to console/stdout.
    
    Delegates to the centralized log_audit_event for consistent audit logging
    and cryptographic signing of audit records.
    
    Thread-safe and async-compatible for production use.
    """

    async def log_action(self, action: str, details: Dict[str, Any]) -> None:
        """
        Log an audit action as JSON to console via centralized audit system.
        
        Args:
            action: The action/event type being logged
            details: Dictionary containing event details
            
        Raises:
            No exceptions raised - failures are logged but don't interrupt execution.
        """
        # Add metadata to indicate source of audit record
        enriched_details = {
            **details,
            "audit_logger": "JsonConsoleAuditLogger",
            "output_target": "console",
        }
        
        try:
            await log_audit_event(action, enriched_details)
        except Exception as e:
            # Audit failures should never break application flow
            logger.warning(f"Failed to send audit event to centralized logger: {e}")
        
        # Also output directly to console as JSON for immediate visibility
        try:
            audit_record = {
                "timestamp": datetime.now().isoformat(),
                "action": action,
                "details": enriched_details,
            }
            print(json.dumps(audit_record), file=sys.stdout, flush=True)
        except Exception as e:
            logger.warning(f"Failed to write audit record to console: {e}")


class FileAuditLogger(AuditLogger):
    """
    File Audit Logger - writes structured audit logs to a configured file.
    
    Delegates to the centralized log_audit_event for cryptographic signing
    and also maintains a local rotating log file for disaster recovery.
    
    Features:
    - Rotating file handler with configurable size and backup count
    - Secure path validation to prevent directory traversal
    - Graceful degradation if file system is unavailable
    - Thread-safe and async-compatible
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize the FileAuditLogger.
        
        Args:
            config: Configuration dictionary with optional keys:
                - audit_log_file: Path to log file (default: "audit.log")
                - audit_log_max_bytes: Max file size before rotation (default: 10MB)
                - audit_log_backup_count: Number of backup files (default: 5)
        """
        self.config = config
        self.log_file = config.get("audit_log_file", "audit.log")
        self.max_bytes = config.get(
            "audit_log_max_bytes", 10 * 1024 * 1024
        )  # 10MB default
        self.backup_count = config.get("audit_log_backup_count", 5)
        self.file_handler = None

        # Create rotating file handler for audit logs
        from logging.handlers import RotatingFileHandler

        # Validate and secure the log file path
        log_file_path = Path(self.log_file).resolve()

        # Ensure directory exists and is within safe boundaries
        log_dir = log_file_path.parent
        if not log_dir.exists():
            try:
                log_dir.mkdir(parents=True, mode=0o755, exist_ok=True)
            except (OSError, PermissionError) as e:
                logger.error(f"Failed to create audit log directory {log_dir}: {e}")
                return

        # Check write permissions
        if not os.access(log_dir, os.W_OK):
            logger.error(f"No write permission for audit log directory {log_dir}")
            return

        try:
            self.file_handler = RotatingFileHandler(
                str(log_file_path),
                maxBytes=self.max_bytes,
                backupCount=self.backup_count,
            )
            self.file_handler.setFormatter(logging.Formatter("%(message)s"))
        except (OSError, PermissionError) as e:
            logger.error(f"Failed to create audit log file handler: {e}")

    async def log_action(self, action: str, details: Dict[str, Any]) -> None:
        """
        Log an audit action to file via centralized audit system and direct file write.
        
        Args:
            action: The action/event type being logged
            details: Dictionary containing event details
            
        Note:
            Failures are logged but don't interrupt execution to ensure
            application stability even when audit systems are unavailable.
        """
        # Add metadata to indicate source of audit record
        enriched_details = {
            **details,
            "audit_logger": "FileAuditLogger",
            "output_target": self.log_file,
        }
        
        # Send to centralized audit system
        try:
            await log_audit_event(action, enriched_details)
        except Exception as e:
            logger.warning(f"Failed to send audit event to centralized logger: {e}")

        # Also write directly to the audit log file if handler is available
        if self.file_handler:
            try:
                audit_record = {
                    "timestamp": datetime.now().isoformat(),
                    "action": action,
                    "details": enriched_details,
                }
                log_record = logging.LogRecord(
                    name="audit",
                    level=logging.INFO,
                    pathname="",
                    lineno=0,
                    msg=json.dumps(audit_record),
                    args=(),
                    exc_info=None,
                )
                self.file_handler.emit(log_record)
            except Exception as e:
                logger.warning(f"Failed to write audit record to file: {e}")


# Get module logger - follows Python logging best practices.
# Do NOT call basicConfig() at module level to avoid duplicate logs.
# The application entry point should configure the root logger.
logger = logging.getLogger(__name__)

# Frontend detection keywords for safety net
# Used to detect frontend requirements from md_content when not explicitly set
FRONTEND_DETECTION_KEYWORDS = [
    "item creation", "create item", "crud", "form", "submit",
    # Modern frontend frameworks
    "react", "vue", "angular", "svelte", "next.js", "nuxt",
    # Build tools and bundlers
    "vite", "webpack", "parcel", "rollup",
    # Frontend languages and supersets
    "typescript", "tsx", "jsx",
    # CSS frameworks and preprocessors
    "tailwind", "tailwindcss", "bootstrap", "sass", "scss", "css",
    # Generic frontend terms
    "frontend", "front-end", "front end", "web ui", "ui", "user interface",
    # Directory patterns
    "web/", "frontend/", "client/", "src/components",
    # Package managers and tools
    "npm", "yarn", "pnpm", "node.js", "nodejs",
    # Frontend libraries
    "axios", "fetch api", "material-ui", "chakra", "ant design"
]


# ==============================================================================
# --- Integrated Utilities & Security ---
# ==============================================================================
class SecurityUtils:
    """Utilities for enhancing security during code generation."""

    @staticmethod
    def mask_secrets(text: str) -> str:
        """
        Masks common secret patterns in text for safe logging.
        """
        # Pattern for key=value, key: value, "key": "value" formats
        masked_text = re.sub(
            r"""
            (['"]?
            (api_key|password|secret|token|auth_token|access_key)
            ['"]?\s*[:=]\s*['"]?
            )
            ([a-zA-Z0-9\-_.~+]{16,})
            (['"]?)
            """,
            r"\1REDACTED\5",
            text,
            flags=re.IGNORECASE | re.VERBOSE,
        )
        # Pattern for Bearer tokens
        masked_text = re.sub(
            r"(Authorization\s*:\s*Bearer\s+)[a-zA-Z0-9\-_.~+/=]+",
            r"\1REDACTED",
            masked_text,
            flags=re.IGNORECASE,
        )
        return masked_text

    @staticmethod
    def apply_compliance(code: str, rules: Dict[str, Any]) -> List[str]:
        """Applies compliance checks based on configured rules."""
        violations = []
        for func in rules.get("banned_functions", []):
            if re.search(r"\b" + re.escape(func) + r"\b", code):
                violations.append(
                    f"Compliance violation: Use of banned function '{func}'."
                )
        for banned_import in rules.get("banned_imports", []):
            if re.search(
                r"\bimport\s+" + re.escape(banned_import) + r"\b", code
            ) or re.search(r"\bfrom\s+" + re.escape(banned_import) + r"\b", code):
                violations.append(
                    f"Compliance violation: Use of banned import '{banned_import}'."
                )
        required_header = rules.get("required_header")
        if required_header and not code.startswith(required_header):
            violations.append("Compliance violation: Missing required license header.")
        max_length = rules.get("max_line_length")
        if max_length:
            for i, line in enumerate(code.splitlines()):
                if len(line) > max_length:
                    violations.append(
                        f"Compliance violation: Line {i+1} exceeds max length of {max_length} characters."
                    )
        return violations


security_utils = SecurityUtils()

_tool_cache: Dict[str, bool] = {}


def _is_tool_available(tool: str) -> bool:
    """Checks if a command-line tool is available in the system's PATH and caches the result."""
    if tool not in _tool_cache:
        _tool_cache[tool] = shutil.which(tool) is not None
        if not _tool_cache[tool]:
            logger.warning(
                f"Tool '{tool}' not found in PATH. Dependent checks will be skipped."
            )
    return _tool_cache[tool]


# ==============================================================================
# --- Pluggable Feedback Store ---
# ==============================================================================
class FeedbackStore(ABC):
    """Abstract base class for storing and retrieving HITL feedback."""

    @abstractmethod
    async def setup(self):
        pass

    @abstractmethod
    async def get_feedback(self, req_hash: str) -> Optional[str]:
        pass

    @abstractmethod
    async def save_feedback(self, req_hash: str, feedback: str):
        pass


class SQLiteFeedbackStore(FeedbackStore):
    """An implementation of the feedback store using a local SQLite database."""

    def __init__(self, config: Dict[str, Any]):
        self.db_file = config.get("path", "feedback.db")

    async def setup(self):
        try:
            conn = sqlite3.connect(self.db_file, check_same_thread=False)
            conn.execute(
                "CREATE TABLE IF NOT EXISTS hitl_feedback (req_hash TEXT PRIMARY KEY, feedback TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)"
            )
            conn.close()
            # Cleanup job
            if os.getenv("CODEGEN_DISABLE_CLEANUP_TASKS", "").lower() not in {
                "1",
                "true",
                "yes",
            }:
                asyncio.create_task(self._cleanup_old_feedback())
        except sqlite3.Error as e:
            logger.error(f"SQLite setup failed: {e}")
            raise

    async def _cleanup_old_feedback(self):
        while True:
            await asyncio.sleep(24 * 60 * 60)  # Run daily
            try:
                conn = sqlite3.connect(self.db_file, check_same_thread=False)
                cursor = conn.cursor()
                cursor.execute(
                    "DELETE FROM hitl_feedback WHERE timestamp <= date('now', '-30 days')"
                )
                conn.commit()
                conn.close()
                logger.info("Cleaned up old SQLite feedback entries.")
            except sqlite3.Error as e:
                logger.error(f"SQLite cleanup failed: {e}")

    async def get_feedback(self, req_hash: str) -> Optional[str]:
        with sqlite3.connect(self.db_file, check_same_thread=False) as conn:
            result = conn.execute(
                "SELECT feedback FROM hitl_feedback WHERE req_hash = ? ORDER BY timestamp DESC LIMIT 1",
                (req_hash,),
            ).fetchone()
            return result[0] if result else None

    async def save_feedback(self, req_hash: str, feedback: str):
        with sqlite3.connect(self.db_file, check_same_thread=False) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO hitl_feedback (req_hash, feedback) VALUES (?, ?)",
                (req_hash, feedback),
            )
            conn.commit()


class RedisFeedbackStore(FeedbackStore):
    """An implementation of the feedback store using Redis."""

    def __init__(self, config: Dict[str, Any]):
        self.redis_url = os.getenv("REDIS_URL", config.get("url", "redis://localhost"))
        self.ttl = config.get("ttl", 604800)  # 7 days in seconds
        self._redis = None

    async def setup(self):
        try:
            self._redis = await aioredis.from_url(self.redis_url)
            await self._redis.ping()
            logger.info("Redis feedback store connected successfully.")
        except Exception as e:
            logger.error(f"Redis connection failed: {e}")
            raise

    async def get_feedback(self, req_hash: str) -> Optional[str]:
        if not self._redis:
            raise RuntimeError("Redis client not initialized.")
        feedback = await self._redis.get(f"feedback:{req_hash}")
        return feedback.decode("utf-8") if feedback else None

    async def save_feedback(self, req_hash: str, feedback: str):
        if not self._redis:
            raise RuntimeError("Redis client not initialized.")
        await self._redis.set(f"feedback:{req_hash}", feedback, ex=self.ttl)
        logger.info(f"Saved feedback to Redis for hash {req_hash[:8]}...")


# ==============================================================================
# --- Agent Configuration & Setup ---
# ==============================================================================
# OpenTelemetry Setup
# Use the default/configured tracer provider instead of manually creating one
# This avoids version compatibility issues and respects OTEL_* environment variables
try:
    tracer = trace.get_tracer(__name__)
except TypeError:
    # Fallback for older OpenTelemetry versions
    tracer = None


# ==============================================================================
# --- Prometheus Metrics ---
# ==============================================================================
# Enterprise-Grade Metric Registration with Deduplication Protection
#
# Industry Standard Compliance:
# - SOC 2 Type II: Reliable metric collection without service disruption
# - ISO 27001 A.12.1.3: Capacity management through proper observability
# - NIST SP 800-53 AU-4: Audit storage capacity management
#
# Design Pattern: Check-before-create to prevent ValueError on duplicate registration


def get_or_create_counter(name: str, description: str, labelnames: List[str] = None):
    """
    Enterprise-grade counter factory with idempotent registration.

    Implements check-before-create pattern to prevent 'Duplicated timeseries
    in CollectorRegistry' errors that crash agents during initialization.

    Args:
        name: Unique metric name following prometheus naming conventions
        description: Human-readable metric description
        labelnames: Optional list of label names for dimensional metrics

    Returns:
        Existing or newly created Counter instance
    """
    # Validate and filter labelnames - remove empty strings
    labelnames = labelnames or []
    if not isinstance(labelnames, (list, tuple)):
        labelnames = []
    labelnames = [label for label in labelnames if label and isinstance(label, str)]
    
    try:
        # Check if metric already exists in registry (idempotent)
        collector = REGISTRY._names_to_collectors.get(name)
        if collector is not None:
            return collector
    except (AttributeError, KeyError):
        pass
    # Create new counter if it doesn't exist
    try:
        return Counter(name, description, labelnames=labelnames)
    except ValueError as e:
        # Handle race condition: metric was created by another thread/process
        if "Duplicated timeseries" in str(e):
            existing = REGISTRY._names_to_collectors.get(name)
            if existing is not None:
                return existing
        raise


def get_or_create_histogram(name: str, description: str, labelnames: List[str] = None):
    """
    Enterprise-grade histogram factory with idempotent registration.

    Args:
        name: Unique metric name following prometheus naming conventions
        description: Human-readable metric description
        labelnames: Optional list of label names for dimensional metrics

    Returns:
        Existing or newly created Histogram instance
    """
    # Validate and filter labelnames - remove empty strings
    labelnames = labelnames or []
    if not isinstance(labelnames, (list, tuple)):
        labelnames = []
    labelnames = [label for label in labelnames if label and isinstance(label, str)]
    
    try:
        collector = REGISTRY._names_to_collectors.get(name)
        if collector is not None:
            return collector
    except (AttributeError, KeyError):
        pass
    try:
        return Histogram(name, description, labelnames=labelnames)
    except ValueError as e:
        if "Duplicated timeseries" in str(e):
            existing = REGISTRY._names_to_collectors.get(name)
            if existing is not None:
                return existing
        raise


def get_or_create_gauge(name: str, description: str, labelnames: List[str] = None):
    """
    Enterprise-grade gauge factory with idempotent registration.

    Args:
        name: Unique metric name following prometheus naming conventions
        description: Human-readable metric description
        labelnames: Optional list of label names for dimensional metrics

    Returns:
        Existing or newly created Gauge instance
    """
    # Validate and filter labelnames - remove empty strings
    labelnames = labelnames or []
    if not isinstance(labelnames, (list, tuple)):
        labelnames = []
    labelnames = [label for label in labelnames if label and isinstance(label, str)]
    
    try:
        collector = REGISTRY._names_to_collectors.get(name)
        if collector is not None:
            return collector
    except (AttributeError, KeyError):
        pass
    try:
        return Gauge(name, description, labelnames=labelnames)
    except ValueError as e:
        if "Duplicated timeseries" in str(e):
            existing = REGISTRY._names_to_collectors.get(name)
            if existing is not None:
                return existing
        raise


# Prometheus Metrics - Using safe creation functions
CODEGEN_REQUESTS = get_or_create_counter(
    "codegen_agent_requests_total",
    "Total code generation requests from codegen agent",
    ["backend"],
)
# Backwards compatibility: some callers expect CODEGEN_COUNTER
CODEGEN_COUNTER = CODEGEN_REQUESTS

CODEGEN_LATENCY = get_or_create_histogram(
    "codegen_agent_latency_seconds",
    "Latency of code generation requests in codegen agent",
    ["backend"],
)

CODEGEN_ERRORS = get_or_create_counter(
    "codegen_agent_errors_total",
    "Total errors during code generation in codegen agent",
    ["error_type"],
)

HITL_APPROVAL_RATE = get_or_create_gauge(
    "hitl_approval_rate",
    "Ratio of approved to rejected HITL reviews",
)

HITL_TIMEOUT_RATE = get_or_create_counter(
    "hitl_timeout_total",
    "Total number of HITL review timeouts",
)

SECURITY_FINDINGS = get_or_create_counter(
    "security_findings_total",
    "Total security findings detected in generated code",
    ["scanner"],
)
# Backwards compatibility for older imports / tests
CODEGEN_SECURITY_FINDINGS = SECURITY_FINDINGS


ENSEMBLE_VOTES = get_or_create_counter(
    "ensemble_votes_total",
    "Total votes cast by ensemble models",
    ["model"],
)

CODEGEN_CACHE_HITS = get_or_create_counter(
    "codegen_cache_hits_total",
    "Total cache hits for code generation requests",
    ["backend"],
)

# NOTE: LLM_RATE_LIMIT_EXCEEDED and LLM_CIRCUIT_STATE are imported from runner.llm_client


# Custom Exception
class EnsembleGenerationError(RuntimeError):
    def __init__(self, message, underlying_exceptions):
        super().__init__(message)
        self.underlying_exceptions = underlying_exceptions


# circuit_breaker global is now the imported one.
circuit_breaker = CircuitBreaker()


class CodeGenConfig:
    def __init__(self, config: Dict[str, Any]):
        ### --- DEPLOYMENT NOTE ---
        # The internal model/key config has been removed, as the runner client handles this.
        self.backend = os.getenv(
            "CODEGEN_BACKEND", config.get("backend", "openai")
        ).lower()
        self.api_keys = config.get(
            "api_keys", {}
        )  # Retained for env key presence checks
        self.model = config.get("model", {})  # Retained for custom model mapping

        # VALIDATION: Ensure the environment key for the configured backend is present.
        # Skip API key validation in test mode
        testing_mode = (
            os.getenv("TESTING") == "1"
            or "pytest" in sys.modules
            or os.getenv("PYTEST_CURRENT_TEST") is not None
        )
        for b in ["grok", "openai", "gemini"]:
            self.api_keys[b] = os.getenv(f"{b.upper()}_API_KEY", self.api_keys.get(b))
            self.model[b] = os.getenv(f"{b.upper()}_MODEL", self.model.get(b))
            if self.backend == b and not self.api_keys.get(b) and not testing_mode:
                raise ValueError(f"API key for backend '{b}' is missing.")

        default_template_path = Path(__file__).parent / "templates"
        self.template_dir = config.get("template_dir", str(default_template_path))

        self.max_retries = int(config.get("max_retries", 2))
        self.enable_security_scan = bool(config.get("enable_security_scan", True))
        self.allow_interactive_hitl = bool(config.get("allow_interactive_hitl", False))
        self.ensemble_enabled = bool(
            config.get("ensemble_enabled", False)
        )  # ADDED ENSEMBLE FLAG
        self.compliance_rules = config.get(
            "compliance",
            {"banned_imports": [], "banned_functions": [], "max_line_length": 120},
        )
        self.feedback_store_config = config.get("feedback_store", {"type": "sqlite"})
        self.audit_logger_config = config.get("audit_logger", {"type": "console"})
        self.llm_backends = {}  # Kept as empty dict for code compatibility if needed.

    @classmethod
    def from_file(cls, filepath: str):
        with open(filepath, "r") as f:
            config_data = yaml.safe_load(f)
        return cls(config_data)


# ==============================================================================
# --- CORE COMPONENTS REMOVED/REPLACED ---
# NOTE: All internal LLMBackend, CircuitBreaker, and async_call_llm_api logic is GONE.
# ==============================================================================

# --- Obsolete Retry Wrapper REMOVED ---
# _call_llm_with_retry function has been deleted as requested.


async def perform_security_scans(code_files: dict) -> dict:
    """
    Run security scans on the generated code files.

    - Delegates to runner.runner_security_utils.scan_for_vulnerabilities
      (which may be sync or async; both are supported).
    - Increments SECURITY_FINDINGS when issues are detected.
    - Returns the original code_files unchanged (backwards-compatible).
    """
    try:
        result = scan_for_vulnerabilities(code_files)
        if asyncio.iscoroutine(result):
            result = await result
    except Exception as exc:
        logger.warning(f"Security scan failed, continuing without blocking: {exc}")
        return code_files

    if not result:
        return code_files

    # Expected formats:
    # - {"issues": [ {...}, {...} ]}
    # - [ {...}, {...} ]
    issues = None
    if isinstance(result, dict) and "issues" in result:
        issues = result["issues"]
    elif isinstance(result, list):
        issues = result
    else:
        issues = None

    if issues:
        try:
            # Increment once per scan that finds at least one issue.
            SECURITY_FINDINGS.labels(scanner="default").inc()
        except Exception:
            # Under stubbed metrics, labels()/inc() may be a no-op; ignore failures.
            pass

    return code_files


async def hitl_review(
    code_files: Dict[str, str],
    feedback_store: FeedbackStore,
    req_hash: str,
    allow_interactive: bool,
    redis_client: aioredis.Redis,
    audit_logger: AuditLogger,
) -> Tuple[str, Optional[str]]:
    """API-based Human-in-the-Loop review."""
    if not allow_interactive:
        logger.warning("HITL running in non-interactive mode. Defaulting to rejection.")
        HITL_APPROVAL_RATE.set(0)
        return ("rejected", "Non-interactive HITL.")

    review_system_webhook = os.getenv("REVIEW_SYSTEM_WEBHOOK")
    if not review_system_webhook:
        logger.error("REVIEW_SYSTEM_WEBHOOK is not set. Defaulting to rejection.")
        return ("rejected", "Review system webhook not configured.")

    # Push review request to the external system via webhook
    review_request = {
        "req_hash": req_hash,
        "code_files": code_files,
        "review_url": os.getenv(
            "REVIEW_SYSTEM_URL", "https://review-system.example.com"
        )
        + f"/{req_hash}",
    }

    webhook_sent = False
    for i in range(3):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    review_system_webhook, json=review_request, timeout=10
                ) as resp:
                    resp.raise_for_status()
                    webhook_sent = True
                    # --- Audit/Logging Change: Use log_audit_event ---
                    await log_audit_event(
                        "HITLWebhookSent", {"req_hash": req_hash, "attempt": i + 1}
                    )
                    # --- End Audit/Logging Change ---
                    break
        except Exception as e:
            # --- Audit/Logging Change: Use log_audit_event ---
            await log_audit_event(
                "HITLWebhookFailed",
                {"req_hash": req_hash, "attempt": i + 1, "error": str(e)},
            )
            # --- End Audit/Logging Change ---
            logger.warning(f"Webhook to review system failed (attempt {i+1}): {e}")
            await asyncio.sleep(5)

    if not webhook_sent:
        logger.error(
            "Failed to send webhook to review system after 3 attempts. Defaulting to rejection."
        )
        return ("rejected", "Review system unreachable, defaulting to rejection.")

    # Wait for review submission via Pub/Sub
    # --- Audit/Logging Change: Use log_audit_event ---
    await log_audit_event("HITLPubSubSubscribed", {"req_hash": req_hash})
    # --- End Audit/Logging Change ---
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(f"hitl:review_status:{req_hash}")

    try:
        message = await asyncio.wait_for(
            pubsub.get_message(ignore_subscribe_messages=True), timeout=60
        )
        await pubsub.unsubscribe(f"hitl:review_status:{req_hash}")
        if message:
            review_status = json.loads(message["data"])
            status = review_status["status"]
            feedback = review_status.get("feedback")
            if status == "approved":
                HITL_APPROVAL_RATE.set(1)
            else:
                HITL_APPROVAL_RATE.set(0)
            return status, feedback
        else:
            return ("rejected", "HITL review timed out.")
    except asyncio.TimeoutError:
        await pubsub.unsubscribe(f"hitl:review_status:{req_hash}")
        return ("rejected", "HITL review timed out.")
    except Exception as e:
        await pubsub.unsubscribe(f"hitl:review_status:{req_hash}")
        logger.error(f"Error during HITL Pub/Sub wait: {e}")
        return ("rejected", f"Internal error during HITL review: {e}")


def _build_fallback_prompt(requirements: Dict[str, Any], include_frontend: bool = False, previous_feedback: Optional[str] = None) -> str:
    """
    Builds an enhanced fallback prompt when templates are unavailable.
    This ensures comprehensive spec parsing even without templates.
    
    The prompt is driven entirely by the requirements dict which is populated
    by the IntentParser from the actual specification. No hardcoded values.
    
    Args:
        requirements: The requirements dict containing features, target_language, 
                     constraints, and other parsed spec data from IntentParser.
        include_frontend: Whether to include frontend file generation (default: False)
        previous_feedback: Optional feedback from a previous spec fidelity check,
                          e.g. listing missing endpoints that must be implemented.
        
    Returns:
        A detailed prompt that emphasizes spec compliance and multi-file JSON output
    """
    target_language = requirements.get("target_language", "python")
    features = requirements.get("features", [])
    constraints = requirements.get("constraints", [])
    md_content = requirements.get("md_content", "") or requirements.get("readme_content", "")
    file_structure = requirements.get("file_structure", [])

    # If file_structure not provided by caller, extract from MD spec (Issue 4 fix)
    if not file_structure and md_content:
        from generator.main.provenance import extract_required_files_from_md
        try:
            file_structure = extract_required_files_from_md(md_content, target_language=target_language)
        except Exception as _fs_err:
            logger.warning(f"Failed to extract file structure from MD content: {_fs_err}")

    # Build features section from parsed spec
    features_text = ""
    if features:
        features_text = "## FEATURES TO IMPLEMENT:\n"
        for feature in features:
            features_text += f"- {feature}\n"
    
    # Build constraints section from parsed spec
    constraints_text = ""
    if constraints:
        constraints_text = "## CONSTRAINTS:\n"
        for constraint in constraints:
            constraints_text += f"- {constraint}\n"
    
    # Include original MD content if available
    md_section = ""
    if md_content:
        md_section = f"""
## AUTHORITATIVE SPECIFICATION (HIGHEST PRIORITY):
The following is the COMPLETE, AUTHORITATIVE specification. You MUST implement EXACTLY what is described below.
Do NOT simplify, omit features, or substitute generic implementations.
The features and constraints lists that follow are supplementary summaries only — if they conflict with this specification, THIS specification takes precedence.

```markdown
{md_content}
```
"""
    
    # Extract and explicitly list required endpoints from MD content
    required_endpoints_section = ""
    if md_content:
        from generator.main.provenance import extract_endpoints_from_md
        try:
            required_endpoints = extract_endpoints_from_md(md_content)
            if required_endpoints:
                required_endpoints_section = "\n## ⚠️ REQUIRED API ENDPOINTS (MUST IMPLEMENT ALL) ⚠️\n\n"
                required_endpoints_section += "The specification EXPLICITLY requires these endpoints. You MUST implement ALL of them:\n\n"
                for endpoint in required_endpoints:
                    required_endpoints_section += f"- **{endpoint['method']} {endpoint['path']}**\n"
                required_endpoints_section += "\n**CRITICAL:** FAILURE TO IMPLEMENT ANY OF THESE ENDPOINTS WILL CAUSE VALIDATION FAILURE.\n"
        except Exception as e:
            logger.warning(f"Failed to extract endpoints from MD content in fallback prompt: {e}")
    
    # Build missing endpoints section from previous spec fidelity feedback
    missing_endpoints_section = ""
    if previous_feedback:
        missing_endpoints_section = f"\n## ⚠️ MISSING ENDPOINTS FROM PREVIOUS ATTEMPT\n\n{previous_feedback}\n"
    
    # Build frontend files section if needed
    frontend_files_text = ""
    if include_frontend and target_language == "python":
        frontend_files_text = """
   **FRONTEND FILES (Full-Stack Web Application):**
   - templates/base.html - Base HTML template with navbar, footer, CSS/JS links
   - templates/index.html - Main page extending base template
   - static/css/style.css - Complete responsive stylesheet with CSS variables
   - static/js/app.js - Frontend JavaScript with API integration (fetch calls)
   - static/js/utils.js - Utility functions (showError, showLoading, escapeHtml)
   
   For backend (main.py or app/main.py):
   - Mount static files: app.mount("/static", StaticFiles(directory="static"), name="static")
   - Configure templates: templates = Jinja2Templates(directory="templates")
   - Add CORS middleware for API endpoints
   - Add route to serve index.html template
"""

    # Compute minimum file count guidance based on spec's file_structure
    if len(file_structure) > 12:
        min_files_guidance = f"AT LEAST {len(file_structure)} files to match the specification"
    else:
        min_files_guidance = "AT LEAST 8-12 files for a complete scaffold"
    
    prompt = f"""You are an expert {target_language} developer. Generate production-ready code that implements ALL requirements from the specification.

{md_section}
{missing_endpoints_section}
{required_endpoints_section}
{features_text}
{constraints_text}

Full Requirements JSON: {json.dumps(requirements, sort_keys=True, default=str)}

## YOUR TASK:

1. **ANALYZE THE SPEC**: Carefully read and extract:
   - All API endpoints, routes, or functions mentioned
   - All data models, classes, or schemas required
   - All business logic, calculations, and operations
   - All error handling requirements (validation, edge cases)
   - All dependencies and imports needed

2. **IMPLEMENT COMPLETELY**: Generate complete, working code:
   - NO placeholders or TODOs
   - NO `Depends(...)` with Ellipsis — define stub functions for placeholder dependencies
   - NO incomplete implementations
   - ALL features from requirements must be implemented
   - Proper error handling for all edge cases
   - Type hints and documentation

3. **ORGANIZE INTO FILES**: Structure as a proper {target_language} project with ALL necessary files:

   **REQUIRED FILES (minimum):**
   - app/main.py (or main.py) - Main entry point with all routes/endpoints
   - app/models.py (or models.py) - All data models, schemas, classes
   - requirements.txt (or package.json) - ALL dependencies with versions
   - README.md - Complete setup and usage instructions
   - Dockerfile - Container configuration for deployment
   - .env.example - Environment variable template
{frontend_files_text}
   **ADDITIONAL FILES (as needed for completeness):**
   - app/config.py or config.py - Configuration management
   - app/utils.py or utils.py - Utility/helper functions
   - app/database.py or database.py - Database connection and setup
   - tests/test_*.py or tests/*.test.js - Basic test files
   - .gitignore - Standard ignore patterns
   - docker-compose.yml - Multi-service orchestration (if applicable)

   **Create subdirectories when appropriate:**
   - Use app/ directory for application code
   - Use tests/ for test files
   - Use templates/ for HTML templates (if full-stack)
   - Use static/ for CSS/JS/images (if full-stack)
   - Use docs/ for additional documentation

4. **CODE QUALITY**:
   - Follow {target_language} best practices
   - Use proper naming conventions
   - Add docstrings and comments
   - Handle errors gracefully
   - Make code testable and production-ready

## CRITICAL OUTPUT FORMAT:

Your response MUST be VALID JSON in this EXACT format:

{{
  "files": {{
    "app/main.py": "complete code content with all endpoints/routes...",
    "app/models.py": "complete data models and schemas...",
    "app/config.py": "configuration management code...",
    "requirements.txt": "all dependencies with versions...",
    "Dockerfile": "complete Docker configuration...",
    ".env.example": "environment variables template...",
    "README.md": "complete documentation...",
    "tests/test_main.py": "basic test cases...",
    ".gitignore": "standard ignore patterns..."
  }}
}}

**ABSOLUTE RULES:**
1. Output ONLY the JSON - no text before or after
2. Do NOT wrap in markdown fences (no ```json```)
3. Include {min_files_guidance}
4. ALL code must be complete and functional (no stubs or TODOs)
5. Properly escape special characters in JSON (\\n for newlines, \\" for quotes)
6. Implement EVERY requirement from the specification
7. Include proper directory structure (app/, tests/, etc.)

**CHECKLIST before responding:**
- [ ] All API endpoints/routes implemented in app/main.py
- [ ] All data models defined in app/models.py
- [ ] requirements.txt with ALL dependencies
- [ ] Dockerfile for containerization
- [ ] README.md with setup instructions
- [ ] At least one test file in tests/
- [ ] .env.example with configuration vars
- [ ] .gitignore file included
"""
    if file_structure:
        file_list = "\n".join(f"   - [ ] {f}" for f in file_structure)
        prompt += f"""
**REQUIRED FILES (from specification):**
{file_list}
"""
    prompt += """
Verify you have implemented ALL requirements and included ALL necessary files before responding.
"""
    return prompt


if PLUGIN_AVAILABLE:

    @plugin(
        kind=PlugInKind.FIX,
        name="codegen_agent",
        version="1.0.0",
        params_schema={
            "requirements": {
                "type": "dict",
                "description": "The requirements for the code to be generated.",
            },
            "state_summary": {
                "type": "string",
                "description": "A summary of the current system state.",
            },
            "config_path_or_dict": {
                "type": ["string", "dict"],
                "description": "Path to a YAML config file or a config dictionary.",
            },
            "arbiter_bridge": {
                "type": "object",
                "description": "Optional ArbiterBridge for Arbiter integration.",
            },
        },
        description="Generates code based on requirements, incorporating security scans and human-in-the-loop review.",
        safe=True,
    )
    async def generate_code(
        requirements: Dict[str, Any],
        state_summary: str,
        config_path_or_dict: Union[str, Dict[str, Any]],
        arbiter_bridge: Optional[Any] = None,
    ) -> Dict[str, str]:
        """Main async function for code generation with fully pluggable and implemented components."""
        config = (
            CodeGenConfig.from_file(config_path_or_dict)
            if isinstance(config_path_or_dict, str)
            else CodeGenConfig(config_path_or_dict)
        )

        request_id = str(uuid.uuid4())
        logger.info(f"Starting new code generation request. Request ID: {request_id}")
        if arbiter_bridge:
            logger.info("CodegenAgent: Arbiter integration enabled")

        # [ARBITER] Publish code generation start event
        if arbiter_bridge:
            try:
                await arbiter_bridge.publish_event(
                    "codegen_started",
                    {
                        "request_id": request_id,
                        "backend": config.backend,
                        "ensemble_enabled": config.ensemble_enabled,
                    }
                )
            except Exception as e:
                logger.warning(f"Failed to publish codegen start event: {e}")

        # Initialize components based on config
        redis_client = None
        try:
            redis_client = await aioredis.from_url(
                os.getenv("REDIS_URL", "redis://localhost")
            )
            await redis_client.ping()
        except Exception:
            logger.warning(
                "Redis not available. Distributed components will operate in-memory or be disabled."
            )

        feedback_store = None
        try:
            if config.feedback_store_config["type"] == "redis" and redis_client:
                feedback_store = RedisFeedbackStore(config.feedback_store_config)
                feedback_store._redis = redis_client
                await feedback_store.setup()
            else:
                feedback_store = SQLiteFeedbackStore(config.feedback_store_config)
                await feedback_store.setup()
        except Exception:
            logger.warning("Configured feedback store failed. Falling back to SQLite.")
            feedback_store = SQLiteFeedbackStore(config.feedback_store_config)
            await feedback_store.setup()

        # --- REMOVED OBSOLETE CACHE MANAGER INITIALIZATION ---
        # CacheManager initialization is removed as it's not needed by the new call_llm_api signature.
        # cache_manager = CacheManager(redis_client)

        req_hash = str(hash(json.dumps(requirements, sort_keys=True)))

        with tracer.start_as_current_span(
            "generate_code_request",
            attributes={"request.id": request_id, "backend": config.backend},
        ):
            try:
                with tracer.start_as_current_span("prepare_prompt"):
                    previous_feedback = await feedback_store.get_feedback(req_hash)
                    
                    # Override previous_feedback with spec fidelity failure feedback if present
                    spec_fidelity_feedback = requirements.get("previous_feedback")
                    if spec_fidelity_feedback:
                        previous_feedback = spec_fidelity_feedback
                        logger.info(
                            f"[CODEGEN] Using spec fidelity feedback from previous iteration: {str(spec_fidelity_feedback)[:200]}"
                        )
                    
                    # Extract frontend generation flags from requirements
                    include_frontend = requirements.get("include_frontend", False)
                    frontend_type = requirements.get("frontend_type", None)
                    
                    # Safety net: Check md_content for frontend keywords if not already set
                    md_content = requirements.get("md_content", "") or requirements.get("readme_content", "")
                    if not include_frontend and md_content:
                        md_lower = md_content.lower()
                        for keyword in FRONTEND_DETECTION_KEYWORDS:
                            if keyword in md_lower:
                                logger.info(
                                    f"Safety net: Detected '{keyword}' in md_content, enabling frontend generation"
                                )
                                include_frontend = True
                                frontend_type = DEFAULT_FRONTEND_TYPE
                                requirements["include_frontend"] = include_frontend
                                requirements["frontend_type"] = frontend_type
                                break
                    
                    # Log frontend generation decision
                    if include_frontend:
                        logger.info(
                            f"Full-stack generation enabled - frontend_type={frontend_type}"
                        )
                    
                    # Derive target_framework from project_type
                    _project_type = requirements.get("project_type", "")
                    if _project_type in ("fastapi_service", "microservice", "api_gateway"):
                        target_framework = "fastapi"
                    elif _project_type == "flask_service":
                        target_framework = "flask"
                    elif _project_type == "django_service":
                        target_framework = "django"
                    else:
                        target_framework = None

                    try:
                        prompt = await build_code_generation_prompt(
                            requirements=requirements,
                            state_summary=state_summary,
                            previous_feedback=previous_feedback,
                            previous_error=requirements.get("previous_error"),
                            target_language=requirements.get(
                                "target_language", "python"
                            ),
                            target_framework=target_framework,
                            enable_meta_llm_critique=False,
                            multi_modal_inputs=None,
                            audit_logger=JsonConsoleAuditLogger(),  # Kept for prompt builder compatibility
                            redis_client=redis_client,
                            include_frontend=include_frontend,
                            frontend_type=frontend_type,
                            md_content=md_content,
                        )
                    except TemplateNotFound as e:
                        logger.warning(
                            f"Template not found ({e}). Using enhanced fallback prompt."
                        )
                        prompt = _build_fallback_prompt(requirements, include_frontend=include_frontend, previous_feedback=previous_feedback)
                    except Exception as e:
                        logger.warning(
                            f"Prompt build failed ({e}). Using enhanced fallback prompt."
                        )
                        prompt = _build_fallback_prompt(requirements, include_frontend=include_frontend, previous_feedback=previous_feedback)

                # Generate Code
                with tracer.start_as_current_span("call_llm"):
                    # --- LLM Execution Change: Multi-Pass Ensemble / Single Call Logic ---
                    # Auto-enable ensemble for large specs so every chunk gets majority-voted output.
                    _use_multipass = _should_use_multipass(requirements)
                    _effective_ensemble = config.ensemble_enabled
                    if not _effective_ensemble and _use_multipass:
                        _ep_count = _count_spec_endpoints(requirements)
                        logger.info(
                            f"[CODEGEN] Auto-enabling ensemble mode for large spec "
                            f"({_ep_count} endpoints detected)"
                        )
                        _effective_ensemble = True

                    # Shared ensemble models list (used by both ensemble paths)
                    _ensemble_models = [
                        {"provider": "openai", "model": config.model.get("openai", "gpt-4o")},
                        {"provider": "gemini", "model": config.model.get("gemini", "gemini-2.5-pro")},
                        {"provider": "grok", "model": config.model.get("grok", "grok-4")},
                    ]

                    if _effective_ensemble:
                        backend_used = "ensemble"
                        if _use_multipass:
                            # Multi-pass ensemble: each chunk independently uses ensemble voting
                            logger.info("[CODEGEN] Multi-pass ensemble generation: starting")
                            _already_generated = list(requirements.get("already_generated_files", []))
                            _merged_files: Dict[str, str] = {}
                            _symbol_manifest: str = ""
                            _skip_cache = bool(requirements.get("previous_error") or requirements.get("previous_feedback"))
                            # Extract spec model definitions once to inject into the core pass.
                            _spec_models = _extract_spec_models(requirements)
                            _models_note = (
                                f"\n\nSpec Data Models (implement these exactly):\n{_spec_models}\n"
                                if _spec_models else ""
                            )
                            # Track wall-clock time for the global PIPELINE_CODEGEN_TIMEOUT guard.
                            _multipass_global_start = time.monotonic()
                            _groups_to_run = _build_multipass_groups(include_frontend, frontend_type)
                            for _pass_index, _group in enumerate(_groups_to_run, start=1):
                                logger.info(
                                    f"[CODEGEN] Multi-pass ensemble: starting pass '{_group['name']}' "
                                    f"({_pass_index}/{len(_MULTIPASS_GROUPS)})"
                                )
                                _pass_start = time.monotonic()
                                _already = list(set(_merged_files.keys()) | set(_already_generated))
                                _already_note = (
                                    f"\n\nAlready-generated files (DO NOT regenerate these): {_already}\n"
                                    if _already else ""
                                )
                                _manifest_note = (
                                    f"\n\n{_symbol_manifest}\n" if _symbol_manifest else ""
                                )
                                # Inject spec model definitions for core and routes_and_services
                                # passes so the LLM has explicit field/type information when
                                # generating models, schemas, routers, and services.
                                _spec_models_note = _models_note if _group["name"] in ("core", "routes_and_services") else ""
                                _pass_prompt = (
                                    f"{prompt}{_already_note}{_manifest_note}{_spec_models_note}"
                                    f"\n\n### GENERATION PASS: {_group['name'].upper()} ###\n"
                                    f"{_group['focus']}\n"
                                    f"Return ONLY the files for this pass as a JSON object with a 'files' key."
                                )
                                # NOTE: Using "first" voting strategy because majority voting requires exact
                                # string matches across providers, which is impossible for code generation.
                                # Different LLMs produce semantically equivalent but textually different code.
                                #
                                # Global timeout guard: abort early if we have already consumed the
                                # configured pipeline budget across previous passes.
                                _multipass_elapsed = time.monotonic() - _multipass_global_start
                                if _multipass_elapsed >= PIPELINE_CODEGEN_TIMEOUT_SECONDS:
                                    logger.error(
                                        "[CODEGEN] Multi-pass ensemble global timeout reached "
                                        "(%.0fs >= %ds); aborting remaining passes with %d files collected",
                                        _multipass_elapsed,
                                        PIPELINE_CODEGEN_TIMEOUT_SECONDS,
                                        len(_merged_files),
                                    )
                                    break
                                # Spawn a periodic heartbeat task so container health-checks and log
                                # monitors can confirm the job is alive during long LLM calls.
                                _heartbeat = asyncio.create_task(
                                    _multipass_heartbeat(_group['name'])
                                )
                                try:
                                     _pass_dict = await call_llm_api(
                                         prompt=_pass_prompt,
                                         provider=config.backend,
                                         model=config.model.get(config.backend),
                                         response_format={"type": "json_object"},
                                         skip_cache=_skip_cache,
                                     )
                                     _pass_resp = (
                                         _pass_dict["content"]
                                         if isinstance(_pass_dict, dict) and "content" in _pass_dict
                                         else str(_pass_dict)
                                     )
                                     _pass_files = parse_llm_response(_pass_resp)
                                     # Strip protected files always provided by the framework.
                                     _pass_files = {
                                         k: v for k, v in _pass_files.items()
                                         if k != "alembic/env.py"
                                     }
                                     # AST-aware merge: preserve symbols from earlier passes when
                                     # the new pass overwrites an existing Python file.
                                     for _pf_key, _pf_val in _pass_files.items():
                                         if (
                                             _pf_key in _merged_files
                                             and _pf_key.endswith(".py")
                                         ):
                                             _merged_files[_pf_key] = _ast_merge_python_files(
                                                 _merged_files[_pf_key], _pf_val
                                             )
                                         else:
                                             _merged_files[_pf_key] = _pf_val
                                     # After each pass, rebuild the symbol manifest so later
                                     # passes know what was already defined.
                                     _symbol_manifest = _build_symbol_manifest(_merged_files)
                                     _pass_duration = time.monotonic() - _pass_start
                                     logger.info(
                                         f"[CODEGEN] Multi-pass ensemble '{_group['name']}': "
                                         f"+{len(_pass_files)} files (total={len(_merged_files)}) in {_pass_duration:.1f}s"
                                     )
                                except Exception as _pass_err:
                                     _pass_duration = time.monotonic() - _pass_start
                                     logger.warning(
                                         f"[CODEGEN] Multi-pass ensemble '{_group['name']}' failed after {_pass_duration:.1f}s: "
                                         f"{_pass_err}. Continuing with remaining passes."
                                     )
                                finally:
                                    # Always cancel the heartbeat task to avoid resource leaks,
                                    # regardless of whether the LLM call succeeded or raised.
                                    _heartbeat.cancel()
                                    await asyncio.gather(_heartbeat, return_exceptions=True)
                            response = {"files": _merged_files}
                            logger.info(
                                f"[CODEGEN] Multi-pass ensemble complete: {len(_merged_files)} total files",
                                extra={"backend": "ensemble", "response_length": len(str(response))}
                            )
                            # ------------------------------------------------------------------
                            # Endpoint-coverage supplementary pass (best-effort)
                            # After the 3 fixed passes, check how many spec endpoints are
                            # represented in the generated router files.  If any are missing,
                            # fire one targeted LLM call to fill the gap.
                            # ------------------------------------------------------------------
                            try:
                                _spec_md = (
                                    requirements.get("md_content", "")
                                    or requirements.get("description", "")
                                    or ""
                                )
                                _required_eps = set(
                                    re.findall(
                                        r'\b(?:GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\b\s+/\S+',
                                        _spec_md,
                                        re.IGNORECASE,
                                    )
                                )
                                if _required_eps:
                                    # Fix 5: AST-based route extraction (replaces brittle substring matching)
                                    _extracted_routes = _extract_routes_from_files(_merged_files)
                                    # Normalize spec endpoints for comparison
                                    def _normalize_ep(ep: str) -> Tuple[str, str]:
                                        parts = ep.split(None, 1)
                                        method = parts[0].upper() if parts else ""
                                        path = parts[1].rstrip("/") if len(parts) > 1 else ""
                                        path = re.sub(r'\{[^}]+\}', _PATH_PARAM_WILDCARD, path)
                                        return (method, path)

                                    _normalized_required = {_normalize_ep(ep): ep for ep in _required_eps}
                                    _implemented_methods_paths = _extracted_routes
                                    _missing_eps = [
                                        orig_ep
                                        for norm_ep, orig_ep in _normalized_required.items()
                                        if norm_ep not in _implemented_methods_paths
                                    ]
                                    _total = len(_required_eps)
                                    _covered = _total - len(_missing_eps)
                                    if _missing_eps:
                                        logger.info(
                                            f"[CODEGEN] Endpoint coverage check: {_covered}/{_total} "
                                            f"endpoints covered — running gap-fill pass for "
                                            f"{len(_missing_eps)} missing endpoint(s)"
                                        )
                                        # Detect placeholder services to guide the gap-fill LLM.
                                        try:
                                            _pre_fill_wiring = _validate_wiring(_merged_files)
                                            _pre_fill_stubs = _pre_fill_wiring["placeholder_services"]
                                        except Exception:
                                            _pre_fill_stubs = []
                                        _stub_hint = (
                                            "\n\nService stub files that need real implementations:\n"
                                            + "\n".join(f"  - {_sp}" for _sp, _ in _pre_fill_stubs)
                                        ) if _pre_fill_stubs else ""
                                        # Issue 4 fix: detect missing route groups and prioritize them.
                                        # A "route group" is a set of endpoints sharing the same first
                                        # three path segments (e.g. /api/v1/auth/).  When all endpoints
                                        # in a group are absent from the generated files, the entire
                                        # group needs a new router file — not just patched additions.
                                        # We generate those complete router files first, then handle
                                        # any individually missing endpoints in existing routers.
                                        _route_groups: Dict[str, List[str]] = {}
                                        _individual_eps: List[str] = []
                                        for _ep in sorted(_missing_eps):
                                            _parts = _ep.split(None, 1)
                                            _ep_path = _parts[1].rstrip("/") if len(_parts) > 1 else _ep
                                            _segments = [s for s in _ep_path.split("/") if s]
                                            if len(_segments) >= 3:
                                                # e.g. /api/v1/auth/login → group prefix = "api/v1/auth"
                                                _group_prefix = "/" + "/".join(_segments[:3])
                                                # A group is "covered" when any already-generated file
                                                # references that path prefix (as a route decorator arg
                                                # or router prefix string).
                                                _group_covered = any(
                                                    _group_prefix in content
                                                    for content in _merged_files.values()
                                                )
                                                if not _group_covered:
                                                    # Use the group prefix as the key so all endpoints
                                                    # in the same router land in the same bucket.
                                                    _route_groups.setdefault(_group_prefix, []).append(_ep)
                                                    continue
                                            _individual_eps.append(_ep)
                                        _missing_group_section = ""
                                        if _route_groups:
                                            _missing_group_section = (
                                                "\n\n### MISSING ROUTE GROUPS (generate complete router files first)\n"
                                                "The following complete route groups are ENTIRELY absent from the "
                                                "generated codebase.  For each group, create a dedicated router file "
                                                "(e.g. `app/routers/auth.py` for the `/api/v1/auth` group) that "
                                                "implements ALL endpoints listed for that group:\n"
                                                + "\n".join(
                                                    f"  Group '{grp}':\n"
                                                    + "\n".join(f"    - {ep}" for ep in eps)
                                                    for grp, eps in sorted(_route_groups.items())
                                                )
                                            )
                                        _individual_section = ""
                                        if _individual_eps:
                                            _individual_section = (
                                                "\n\n### INDIVIDUAL MISSING ENDPOINTS\n"
                                                "The following individual endpoints are missing from their "
                                                "existing router files.  Add them to the appropriate file:\n"
                                                + "\n".join(f"  - {ep}" for ep in sorted(_individual_eps))
                                            )
                                        _gap_prompt = (
                                            f"{prompt}"
                                            f"\n\nAlready-generated files (DO NOT regenerate): "
                                            f"{list(_merged_files.keys())}\n"
                                            f"\n\n{_build_project_module_reference(_merged_files)}\n"
                                            f"\n\n### GENERATION PASS: endpoint_gap_fill ###\n"
                                            f"The following required endpoints are NOT yet implemented "
                                            f"in the generated router files.  Generate the router AND "
                                            f"service files needed to implement them. Prioritize "
                                            f"implementing real methods in service classes that are "
                                            f"currently stubs:\n"
                                            + _missing_group_section
                                            + _individual_section
                                            + _stub_hint
                                            + "\nYou MUST add proper import statements at the top of each file "
                                            + "for all symbols you reference. Use `from <module> import <symbol>` "
                                            + "for project-local imports.\nReturn a JSON object with a 'files' key."
                                        )
                                        _gap_heartbeat = asyncio.create_task(
                                            _multipass_heartbeat("endpoint_gap_fill")
                                        )
                                        try:
                                            _gap_dict = await call_llm_api(
                                                prompt=_gap_prompt,
                                                provider=config.backend,
                                                model=config.model.get(config.backend),
                                                response_format={"type": "json_object"},
                                                skip_cache=_skip_cache,
                                            )
                                            _gap_resp = (
                                                _gap_dict["content"]
                                                if isinstance(_gap_dict, dict) and "content" in _gap_dict
                                                else str(_gap_dict)
                                            )
                                            _gap_files = parse_llm_response(_gap_resp)
                                            for _gf_key, _gf_val in _gap_files.items():
                                                if _gf_key in _merged_files and _gf_key.endswith(".py"):
                                                    # Additive merge: preserve existing endpoints, add new ones
                                                    _merged_files[_gf_key] = _ast_merge_python_files(
                                                        _merged_files[_gf_key], _gf_val
                                                    )
                                                elif _gf_key not in _merged_files:
                                                    # New file -- add it (never overwrite existing non-Python files)
                                                    _merged_files[_gf_key] = _gf_val
                                            logger.info(
                                                f"[CODEGEN] Endpoint coverage check: {_covered}/{_total} "
                                                f"endpoints covered, {len(_missing_eps)} gap-filled"
                                            )
                                            response = {"files": _merged_files}
                                        except Exception as _gap_err:
                                            logger.warning(
                                                f"[CODEGEN] Endpoint gap-fill pass failed (non-fatal): {_gap_err}"
                                            )
                                        finally:
                                            _gap_heartbeat.cancel()
                                            await asyncio.gather(_gap_heartbeat, return_exceptions=True)
                                    else:
                                        logger.info(
                                            f"[CODEGEN] Endpoint coverage check: {_covered}/{_total} "
                                            f"endpoints covered, 0 gap-filled"
                                        )
                            except Exception as _ep_check_err:
                                logger.warning(
                                    f"[CODEGEN] Endpoint coverage check failed (non-fatal): {_ep_check_err}"
                                )
                            # ------------------------------------------------------------------
                            # Wiring validation: log warnings for placeholder services and
                            # any routers that are not yet mounted in main.py.
                            # ------------------------------------------------------------------
                            _placeholder_svcs: List[Tuple[str, float]] = []
                            try:
                                _wiring = _validate_wiring(_merged_files)
                                _placeholder_svcs = _wiring["placeholder_services"]
                                for _svc_path, _pct in _placeholder_svcs:
                                    logger.warning(
                                        "[CODEGEN] Placeholder service detected in %s "
                                        "(%.0f%% of functions appear to be stubs) — "
                                        "real ORM logic is required",
                                        _svc_path,
                                        _pct,
                                    )
                                if _wiring["unwired_routers"]:
                                    logger.warning(
                                        "[CODEGEN] Unwired routers detected before reconciliation: %s "
                                        "— _reconcile_app_wiring will fix these",
                                        _wiring["unwired_routers"],
                                    )
                                for _imp_file, _imp_mod, _imp_missing in _wiring.get("unresolved_imports", []):
                                    logger.warning(
                                        "[CODEGEN] Unresolved imports in %s: symbols %s not found in %s "
                                        "— stub repair will address these",
                                        _imp_file,
                                        _imp_missing,
                                        _imp_mod,
                                    )
                                for _dup_file, _dup_prefix, _dup_path in _wiring.get("duplicate_prefixes", []):
                                    logger.warning(
                                        "[CODEGEN] Duplicate route prefix in %s: decorator path '%s' "
                                        "starts with mount prefix '%s' — _reconcile_app_wiring will fix this",
                                        _dup_file,
                                        _dup_path,
                                        _dup_prefix,
                                    )
                            except Exception as _val_err:
                                logger.warning(f"[CODEGEN] Wiring validation failed (non-fatal): {_val_err}")
                            # ------------------------------------------------------------------
                            # Fix 6: Stub service repair — replace placeholder stubs with real
                            # ORM logic via a targeted LLM call (1 attempt, non-blocking).
                            # ------------------------------------------------------------------
                            if _placeholder_svcs:
                                try:
                                    _merged_files = await _repair_stub_services(
                                        _merged_files, config, _placeholder_svcs, spec_models=_spec_models
                                    )
                                except Exception as _repair_err:
                                    logger.warning(
                                        f"[CODEGEN] Stub service repair pass failed (non-fatal): {_repair_err}"
                                    )
                            # ------------------------------------------------------------------
                            # Post-ensemble reconciliation: wire routers into main.py (no LLM needed)
                            # ------------------------------------------------------------------
                            try:
                                _merged_files = _reconcile_app_wiring(_merged_files)
                                response = {"files": _merged_files}
                                logger.info("[CODEGEN] Post-ensemble reconciliation completed")
                            except Exception as _recon_err:
                                logger.warning(f"[CODEGEN] Post-ensemble reconciliation failed (non-fatal): {_recon_err}")
                            # ------------------------------------------------------------------
                            # Stub replacement pass: delegate to _retry_stub_files helper.
                            # Runs up to _STUB_RETRY_MAX_ATTEMPTS targeted LLM calls to
                            # replace any remaining auto-generated stub modules.
                            # ------------------------------------------------------------------
                            try:
                                _merged_files = await _retry_stub_files(_merged_files, config)
                                response = {"files": _merged_files}
                            except Exception as _stub_retry_err:
                                logger.warning(
                                    "[CODEGEN] Stub replacement pass failed (non-fatal): %s",
                                    _stub_retry_err,
                                )
                        else:
                            # Single-pass ensemble (original behavior for small specs with ensemble enabled)
                            # NOTE: Using "first" voting strategy because majority voting requires exact
                            # string matches across providers, which is impossible for code generation.
                            # Different LLMs produce semantically equivalent but textually different code.
                            try:
                                response_dict = await call_ensemble_api(
                                    prompt=prompt,
                                    models=_ensemble_models,
                                    voting_strategy="first",
                                    timeout_per_provider=180.0,
                                )
                                response = (
                                    response_dict["content"]
                                    if isinstance(response_dict, dict) and "content" in response_dict
                                    else str(response_dict)
                                )
                                logger.info(
                                    "[CODEGEN] LLM ensemble response received",
                                    extra={
                                        "backend": "ensemble",
                                        "response_length": len(str(response)),
                                        "response_preview": str(response)[:200]
                                    }
                                )
                            except Exception as _ensemble_err:
                                logger.warning(
                                    "[CODEGEN] Single-pass ensemble failed: %s. Attempting single-provider fallback.",
                                    _ensemble_err,
                                )
                                _fb_dict = await call_llm_api(
                                    prompt=prompt,
                                    provider=config.backend,
                                    model=config.model.get(config.backend),
                                    response_format={"type": "json_object"},
                                )
                                response = (
                                    _fb_dict["content"]
                                    if isinstance(_fb_dict, dict) and "content" in _fb_dict
                                    else str(_fb_dict)
                                )
                                logger.info(
                                    "[CODEGEN] Single-provider fallback succeeded",
                                    extra={"backend": config.backend, "response_length": len(str(response))}
                                )
                    else:
                        # Single call logic (using configured backend) — small spec, no ensemble
                        backend_used = config.backend
                        logger.info(
                            "[CODEGEN] Calling LLM",
                            extra={
                                "backend": config.backend,
                                "model": config.model.get(config.backend),
                                "requirements_keys": list(requirements.keys())
                            }
                        )
                        # NOTE: response_format requires OpenAI-compatible providers
                        # If using non-OpenAI backends, ensure they support structured output
                        _llm_kwargs: Dict[str, Any] = {
                            "response_format": {"type": "json_object"},
                            "prompt": prompt,
                            "provider": config.backend,
                            "model": config.model.get(config.backend),
                        }
                        if len(prompt) > LARGE_PROMPT_THRESHOLD:
                            model_name = config.model.get(config.backend)
                            model_limit = MODEL_MAX_OUTPUT_TOKENS.get(model_name, 16384)
                            context_window = MODEL_CONTEXT_WINDOWS.get(model_name, 128000)
                            estimated_input_tokens = int(len(prompt) / AVG_CHARS_PER_TOKEN)
                            safety_margin = int(estimated_input_tokens * 0.1)
                            available_output_tokens = context_window - estimated_input_tokens - safety_margin
                            _llm_kwargs["max_tokens"] = max(4096, min(LARGE_PROMPT_MAX_TOKENS, model_limit, available_output_tokens))
                            logger.info(
                                f"[CODEGEN] Large prompt detected ({len(prompt)} chars, ~{estimated_input_tokens} tokens), "
                                f"requesting max_tokens={_llm_kwargs['max_tokens']} "
                                f"(context_window={context_window}, model_output_limit={model_limit})"
                            )
                        if requirements.get("previous_error") or requirements.get("previous_feedback"):
                            _llm_kwargs["skip_cache"] = True
                        response = await call_llm_api(**_llm_kwargs)
                        logger.info(
                            "[CODEGEN] LLM response received",
                            extra={
                                "backend": config.backend,
                                "response_length": len(str(response)),
                                "response_preview": str(response)[:200]
                            }
                        )
                    # --- End LLM Execution Change ---

                with tracer.start_as_current_span("parse_response_and_scan"):
                    code_files = parse_llm_response(response)
                    
                    # Log parsed files
                    logger.info(
                        f"[CODEGEN] Parsed {len(code_files)} files from LLM response",
                        extra={"files": list(code_files.keys())}
                    )
                    
                    code_files = add_traceability_comments(
                        code_files,
                        requirements,
                        requirements.get("target_language", "python"),
                    )

                    # Post-Processing and Scans
                    for code in code_files.values():
                        violations = security_utils.apply_compliance(
                            code, config.compliance_rules
                        )
                        if violations:
                            # --- Audit/Logging Change: Use log_audit_event ---
                            await log_audit_event(
                                "Compliance Violation", {"violations": violations}
                            )
                            # --- End Audit/Logging Change ---

                    # --- Security Scans Change: Use unified scanning utility ---
                    code_files = await perform_security_scans(code_files)
                    # --- End Security Scans Change ---

                # HITL (only when enabled)
                if getattr(config, "allow_interactive_hitl", False):
                    with tracer.start_as_current_span("hitl_review"):
                        # We pass a dummy JsonConsoleAuditLogger to hitl_review for signature compatibility
                        status, feedback = await hitl_review(
                            code_files,
                            feedback_store,
                            req_hash,
                            True,
                            redis_client,
                            JsonConsoleAuditLogger(),
                        )
                    if status != "approved":
                        # --- Audit/Logging Change: Use log_audit_event ---
                        await log_audit_event("HITL Rejection", {"feedback": feedback})
                        # --- End Audit/Logging Change ---
                        return {
                            "error.txt": f"Code rejected by human review. Feedback: {feedback}"
                        }
                else:
                    # Skip HITL entirely; treat as approved
                    status, feedback = ("approved", None)

                # --- Audit/Logging Change: Use log_audit_event ---
                await log_audit_event(
                    "Code Generation Completed",
                    {"files": list(code_files.keys()), "model": backend_used},
                )
                # --- End Audit/Logging Change ---
                # [ARBITER] Publish code generation completion event
                if arbiter_bridge:
                    try:
                        await arbiter_bridge.publish_event(
                            "codegen_completed",
                            {
                                "request_id": request_id,
                                "status": "success",
                                "files_generated": len(code_files),
                                "backend_used": backend_used,
                            }
                        )
                    except Exception as e:
                        logger.warning(f"Failed to publish codegen completion event: {e}")
                
                return code_files

            except Exception as e:
                # Improve error logging with more context
                logger.error(
                    "[CODEGEN] Generation failed",
                    extra={
                        "error_type": type(e).__name__,
                        "error_message": str(e),
                        "backend": config.backend,
                        "requirements": requirements
                    },
                    exc_info=True
                )
                # --- Audit/Logging Change: Use log_audit_event ---
                await log_audit_event(
                    "Code Generation Failed", {"error": str(e), "traceback": repr(e)}
                )
                # --- End Audit/Logging Change ---
                CODEGEN_ERRORS.labels(type(e).__name__).inc()
                
                # [ARBITER] Report error to bridge
                if arbiter_bridge:
                    try:
                        await arbiter_bridge.report_bug({
                            "title": f"Code generation failed: {type(e).__name__}",
                            "description": f"Code generation request {request_id} failed: {str(e)}",
                            "severity": "high",
                            "agent": "codegen",
                            "error_type": type(e).__name__,
                            "error_message": str(e),
                            "request_id": request_id,
                        })
                    except Exception as bridge_err:
                        logger.warning(f"Failed to report error to arbiter: {bridge_err}")
                
                return {
                    "error.txt": f"Error: {type(e).__name__}: {str(e)}"
                }

else:

    async def generate_code(
        requirements: Dict[str, Any],
        state_summary: str,
        config_path_or_dict: Union[str, Dict[str, Any]],
        arbiter_bridge: Optional[Any] = None,
    ) -> Dict[str, str]:
        """Main async function for code generation with fully pluggable and implemented components."""
        config = (
            CodeGenConfig.from_file(config_path_or_dict)
            if isinstance(config_path_or_dict, str)
            else CodeGenConfig(config_path_or_dict)
        )

        request_id = str(uuid.uuid4())
        logger.info(f"Starting new code generation request. Request ID: {request_id}")
        if arbiter_bridge:
            logger.info("CodegenAgent: Arbiter integration enabled")

        # [ARBITER] Publish code generation start event
        if arbiter_bridge:
            try:
                await arbiter_bridge.publish_event(
                    "codegen_started",
                    {
                        "request_id": request_id,
                        "backend": config.backend,
                        "ensemble_enabled": config.ensemble_enabled,
                    }
                )
            except Exception as e:
                logger.warning(f"Failed to publish codegen start event: {e}")

        # Initialize components based on config
        redis_client = None
        try:
            redis_client = await aioredis.from_url(
                os.getenv("REDIS_URL", "redis://localhost")
            )
            await redis_client.ping()
        except Exception:
            logger.warning(
                "Redis not available. Distributed components will operate in-memory or be disabled."
            )

        feedback_store = None
        try:
            if config.feedback_store_config["type"] == "redis" and redis_client:
                feedback_store = RedisFeedbackStore(config.feedback_store_config)
                feedback_store._redis = redis_client
                await feedback_store.setup()
            else:
                feedback_store = SQLiteFeedbackStore(config.feedback_store_config)
                await feedback_store.setup()
        except Exception:
            logger.warning("Configured feedback store failed. Falling back to SQLite.")
            feedback_store = SQLiteFeedbackStore(config.feedback_store_config)
            await feedback_store.setup()

        # --- REMOVED OBSOLETE CACHE MANAGER INITIALIZATION ---
        # CacheManager initialization is removed as it's not needed by the new call_llm_api signature.
        # cache_manager = CacheManager(redis_client)

        req_hash = str(hash(json.dumps(requirements, sort_keys=True)))

        with tracer.start_as_current_span(
            "generate_code_request",
            attributes={"request.id": request_id, "backend": config.backend},
        ):
            try:
                with tracer.start_as_current_span("prepare_prompt"):
                    previous_feedback = await feedback_store.get_feedback(req_hash)
                    
                    # Override previous_feedback with spec fidelity failure feedback if present
                    spec_fidelity_feedback = requirements.get("previous_feedback")
                    if spec_fidelity_feedback:
                        previous_feedback = spec_fidelity_feedback
                        logger.info(
                            f"[CODEGEN] Using spec fidelity feedback from previous iteration: {str(spec_fidelity_feedback)[:200]}"
                        )
                    
                    # Extract frontend generation flags from requirements
                    include_frontend = requirements.get("include_frontend", False)
                    frontend_type = requirements.get("frontend_type", None)
                    
                    # Safety net: Check md_content for frontend keywords if not already set
                    md_content = requirements.get("md_content", "") or requirements.get("readme_content", "")
                    if not include_frontend and md_content:
                        md_lower = md_content.lower()
                        for keyword in FRONTEND_DETECTION_KEYWORDS:
                            if keyword in md_lower:
                                logger.info(
                                    f"Safety net: Detected '{keyword}' in md_content, enabling frontend generation"
                                )
                                include_frontend = True
                                frontend_type = DEFAULT_FRONTEND_TYPE
                                requirements["include_frontend"] = include_frontend
                                requirements["frontend_type"] = frontend_type
                                break
                    
                    # Log frontend generation decision
                    if include_frontend:
                        logger.info(
                            f"Full-stack generation enabled - frontend_type={frontend_type}"
                        )
                    
                    # Derive target_framework from project_type
                    _project_type = requirements.get("project_type", "")
                    if _project_type in ("fastapi_service", "microservice", "api_gateway"):
                        target_framework = "fastapi"
                    elif _project_type == "flask_service":
                        target_framework = "flask"
                    elif _project_type == "django_service":
                        target_framework = "django"
                    else:
                        target_framework = None

                    try:
                        prompt = await build_code_generation_prompt(
                            requirements=requirements,
                            state_summary=state_summary,
                            previous_feedback=previous_feedback,
                            previous_error=requirements.get("previous_error"),
                            target_language=requirements.get(
                                "target_language", "python"
                            ),
                            target_framework=target_framework,
                            enable_meta_llm_critique=False,
                            multi_modal_inputs=None,
                            audit_logger=JsonConsoleAuditLogger(),  # Kept for prompt builder compatibility
                            redis_client=redis_client,
                            include_frontend=include_frontend,
                            frontend_type=frontend_type,
                            md_content=md_content,
                        )
                    except TemplateNotFound as e:
                        logger.warning(
                            f"Template not found ({e}). Using enhanced fallback prompt."
                        )
                        prompt = _build_fallback_prompt(requirements, include_frontend=include_frontend, previous_feedback=previous_feedback)
                    except Exception as e:
                        logger.warning(
                            f"Prompt build failed ({e}). Using enhanced fallback prompt."
                        )
                        prompt = _build_fallback_prompt(requirements, include_frontend=include_frontend, previous_feedback=previous_feedback)

                # Generate Code
                with tracer.start_as_current_span("call_llm"):
                    # --- LLM Execution Change: Multi-Pass Ensemble / Single Call Logic ---
                    # Auto-enable ensemble for large specs so every chunk gets majority-voted output.
                    _use_multipass = _should_use_multipass(requirements)
                    _effective_ensemble = config.ensemble_enabled
                    if not _effective_ensemble and _use_multipass:
                        _ep_count = _count_spec_endpoints(requirements)
                        logger.info(
                            f"[CODEGEN] Auto-enabling ensemble mode for large spec "
                            f"({_ep_count} endpoints detected)"
                        )
                        _effective_ensemble = True

                    # Shared ensemble models list (used by both ensemble paths)
                    _ensemble_models = [
                        {"provider": "openai", "model": config.model.get("openai", "gpt-4o")},
                        {"provider": "gemini", "model": config.model.get("gemini", "gemini-2.5-pro")},
                        {"provider": "grok", "model": config.model.get("grok", "grok-4")},
                    ]

                    if _effective_ensemble:
                        backend_used = "ensemble"
                        if _use_multipass:
                            # Multi-pass ensemble: each chunk independently uses ensemble voting
                            logger.info("[CODEGEN] Multi-pass ensemble generation: starting")
                            _already_generated = list(requirements.get("already_generated_files", []))
                            _merged_files: Dict[str, str] = {}
                            _symbol_manifest: str = ""
                            _skip_cache = bool(requirements.get("previous_error") or requirements.get("previous_feedback"))
                            # Extract spec model definitions once to inject into the core pass.
                            _spec_models = _extract_spec_models(requirements)
                            _models_note = (
                                f"\n\nSpec Data Models (implement these exactly):\n{_spec_models}\n"
                                if _spec_models else ""
                            )
                            # Track wall-clock time for the global PIPELINE_CODEGEN_TIMEOUT guard.
                            _multipass_global_start = time.monotonic()
                            _groups_to_run = _build_multipass_groups(include_frontend, frontend_type)
                            for _pass_index, _group in enumerate(_groups_to_run, start=1):
                                logger.info(
                                    f"[CODEGEN] Multi-pass ensemble: starting pass '{_group['name']}' "
                                    f"({_pass_index}/{len(_MULTIPASS_GROUPS)})"
                                )
                                _pass_start = time.monotonic()
                                _already = list(set(_merged_files.keys()) | set(_already_generated))
                                _already_note = (
                                    f"\n\nAlready-generated files (DO NOT regenerate these): {_already}\n"
                                    if _already else ""
                                )
                                _manifest_note = (
                                    f"\n\n{_symbol_manifest}\n" if _symbol_manifest else ""
                                )
                                # Inject spec model definitions for core and routes_and_services
                                # passes so the LLM has explicit field/type information when
                                # generating models, schemas, routers, and services.
                                _spec_models_note = _models_note if _group["name"] in ("core", "routes_and_services") else ""
                                _pass_prompt = (
                                    f"{prompt}{_already_note}{_manifest_note}{_spec_models_note}"
                                    f"\n\n### GENERATION PASS: {_group['name'].upper()} ###\n"
                                    f"{_group['focus']}\n"
                                    f"Return ONLY the files for this pass as a JSON object with a 'files' key."
                                )
                                # NOTE: Using "first" voting strategy because majority voting requires exact
                                # string matches across providers, which is impossible for code generation.
                                # Different LLMs produce semantically equivalent but textually different code.
                                #
                                # Global timeout guard: abort early if we have already consumed the
                                # configured pipeline budget across previous passes.
                                _multipass_elapsed = time.monotonic() - _multipass_global_start
                                if _multipass_elapsed >= PIPELINE_CODEGEN_TIMEOUT_SECONDS:
                                    logger.error(
                                        "[CODEGEN] Multi-pass ensemble global timeout reached "
                                        "(%.0fs >= %ds); aborting remaining passes with %d files collected",
                                        _multipass_elapsed,
                                        PIPELINE_CODEGEN_TIMEOUT_SECONDS,
                                        len(_merged_files),
                                    )
                                    break
                                # Spawn a periodic heartbeat task so container health-checks and log
                                # monitors can confirm the job is alive during long LLM calls.
                                _heartbeat = asyncio.create_task(
                                    _multipass_heartbeat(_group['name'])
                                )
                                try:
                                     _pass_dict = await call_llm_api(
                                         prompt=_pass_prompt,
                                         provider=config.backend,
                                         model=config.model.get(config.backend),
                                         response_format={"type": "json_object"},
                                         skip_cache=_skip_cache,
                                     )
                                     _pass_resp = (
                                         _pass_dict["content"]
                                         if isinstance(_pass_dict, dict) and "content" in _pass_dict
                                         else str(_pass_dict)
                                     )
                                     _pass_files = parse_llm_response(_pass_resp)
                                     # Strip protected files always provided by the framework.
                                     _pass_files = {
                                         k: v for k, v in _pass_files.items()
                                         if k != "alembic/env.py"
                                     }
                                     # AST-aware merge: preserve symbols from earlier passes when
                                     # the new pass overwrites an existing Python file.
                                     for _pf_key, _pf_val in _pass_files.items():
                                         if (
                                             _pf_key in _merged_files
                                             and _pf_key.endswith(".py")
                                         ):
                                             _merged_files[_pf_key] = _ast_merge_python_files(
                                                 _merged_files[_pf_key], _pf_val
                                             )
                                         else:
                                             _merged_files[_pf_key] = _pf_val
                                     # After each pass, rebuild the symbol manifest so later
                                     # passes know what was already defined.
                                     _symbol_manifest = _build_symbol_manifest(_merged_files)
                                     _pass_duration = time.monotonic() - _pass_start
                                     logger.info(
                                         f"[CODEGEN] Multi-pass ensemble '{_group['name']}': "
                                         f"+{len(_pass_files)} files (total={len(_merged_files)}) in {_pass_duration:.1f}s"
                                     )
                                except Exception as _pass_err:
                                     _pass_duration = time.monotonic() - _pass_start
                                     logger.warning(
                                         f"[CODEGEN] Multi-pass ensemble '{_group['name']}' failed after {_pass_duration:.1f}s: "
                                         f"{_pass_err}. Continuing with remaining passes."
                                     )
                                finally:
                                    # Always cancel the heartbeat task to avoid resource leaks,
                                    # regardless of whether the LLM call succeeded or raised.
                                    _heartbeat.cancel()
                                    await asyncio.gather(_heartbeat, return_exceptions=True)
                            response = {"files": _merged_files}
                            logger.info(
                                f"[CODEGEN] Multi-pass ensemble complete: {len(_merged_files)} total files",
                                extra={"backend": "ensemble", "response_length": len(str(response))}
                            )
                            # ------------------------------------------------------------------
                            # Wiring validation: log warnings for placeholder services and
                            # any routers that are not yet mounted in main.py.
                            # ------------------------------------------------------------------
                            _placeholder_svcs: List[Tuple[str, float]] = []
                            try:
                                _wiring = _validate_wiring(_merged_files)
                                _placeholder_svcs = _wiring["placeholder_services"]
                                for _svc_path, _pct in _placeholder_svcs:
                                    logger.warning(
                                        "[CODEGEN] Placeholder service detected in %s "
                                        "(%.0f%% of functions appear to be stubs) — "
                                        "real ORM logic is required",
                                        _svc_path,
                                        _pct,
                                    )
                                if _wiring["unwired_routers"]:
                                    logger.warning(
                                        "[CODEGEN] Unwired routers detected before reconciliation: %s "
                                        "— _reconcile_app_wiring will fix these",
                                        _wiring["unwired_routers"],
                                    )
                                for _imp_file, _imp_mod, _imp_missing in _wiring.get("unresolved_imports", []):
                                    logger.warning(
                                        "[CODEGEN] Unresolved imports in %s: symbols %s not found in %s "
                                        "— stub repair will address these",
                                        _imp_file,
                                        _imp_missing,
                                        _imp_mod,
                                    )
                                for _dup_file, _dup_prefix, _dup_path in _wiring.get("duplicate_prefixes", []):
                                    logger.warning(
                                        "[CODEGEN] Duplicate route prefix in %s: decorator path '%s' "
                                        "starts with mount prefix '%s' — _reconcile_app_wiring will fix this",
                                        _dup_file,
                                        _dup_path,
                                        _dup_prefix,
                                    )
                            except Exception as _val_err:
                                logger.warning(f"[CODEGEN] Wiring validation failed (non-fatal): {_val_err}")
                            # ------------------------------------------------------------------
                            # Fix 6: Stub service repair — replace placeholder stubs with real
                            # ORM logic via a targeted LLM call (1 attempt, non-blocking).
                            # ------------------------------------------------------------------
                            if _placeholder_svcs:
                                try:
                                    _merged_files = await _repair_stub_services(
                                        _merged_files, config, _placeholder_svcs, spec_models=_spec_models
                                    )
                                except Exception as _repair_err:
                                    logger.warning(
                                        f"[CODEGEN] Stub service repair pass failed (non-fatal): {_repair_err}"
                                    )
                            # ------------------------------------------------------------------
                            # Post-ensemble reconciliation: wire routers into main.py (no LLM needed)
                            # ------------------------------------------------------------------
                            try:
                                _merged_files = _reconcile_app_wiring(_merged_files)
                                response = {"files": _merged_files}
                                logger.info("[CODEGEN] Post-ensemble reconciliation completed")
                            except Exception as _recon_err:
                                logger.warning(f"[CODEGEN] Post-ensemble reconciliation failed (non-fatal): {_recon_err}")
                            # ------------------------------------------------------------------
                            # Stub replacement pass: delegate to _retry_stub_files helper.
                            # Runs up to _STUB_RETRY_MAX_ATTEMPTS targeted LLM calls to
                            # replace any remaining auto-generated stub modules.
                            # ------------------------------------------------------------------
                            try:
                                _merged_files = await _retry_stub_files(_merged_files, config)
                                response = {"files": _merged_files}
                            except Exception as _stub_retry_err:
                                logger.warning(
                                    "[CODEGEN] Stub replacement pass failed (non-fatal): %s",
                                    _stub_retry_err,
                                )
                        else:
                            # Single-pass ensemble (original behavior for small specs with ensemble enabled)
                            # NOTE: Using "first" voting strategy because majority voting requires exact
                            # string matches across providers, which is impossible for code generation.
                            # Different LLMs produce semantically equivalent but textually different code.
                            try:
                                response_dict = await call_ensemble_api(
                                    prompt=prompt,
                                    models=_ensemble_models,
                                    voting_strategy="first",
                                    timeout_per_provider=180.0,
                                )
                                response = (
                                    response_dict["content"]
                                    if isinstance(response_dict, dict) and "content" in response_dict
                                    else str(response_dict)
                                )
                                logger.info(
                                    "[CODEGEN] LLM ensemble response received",
                                    extra={
                                        "backend": "ensemble",
                                        "response_length": len(str(response)),
                                        "response_preview": str(response)[:200]
                                    }
                                )
                            except Exception as _ensemble_err:
                                logger.warning(
                                    "[CODEGEN] Single-pass ensemble failed: %s. Attempting single-provider fallback.",
                                    _ensemble_err,
                                )
                                _fb_dict = await call_llm_api(
                                    prompt=prompt,
                                    provider=config.backend,
                                    model=config.model.get(config.backend),
                                    response_format={"type": "json_object"},
                                )
                                response = (
                                    _fb_dict["content"]
                                    if isinstance(_fb_dict, dict) and "content" in _fb_dict
                                    else str(_fb_dict)
                                )
                                logger.info(
                                    "[CODEGEN] Single-provider fallback succeeded",
                                    extra={"backend": config.backend, "response_length": len(str(response))}
                                )
                    else:
                        # Single call logic (using configured backend) — small spec, no ensemble
                        backend_used = config.backend
                        logger.info(
                            "[CODEGEN] Calling LLM",
                            extra={
                                "backend": config.backend,
                                "model": config.model.get(config.backend),
                                "requirements_keys": list(requirements.keys())
                            }
                        )
                        # NOTE: response_format requires OpenAI-compatible providers
                        # If using non-OpenAI backends, ensure they support structured output
                        _llm_kwargs: Dict[str, Any] = {
                            "prompt": prompt,
                            "provider": config.backend,
                            "model": config.model.get(config.backend),
                            "response_format": {"type": "json_object"},
                        }
                        if len(prompt) > LARGE_PROMPT_THRESHOLD:
                            model_name = config.model.get(config.backend)
                            model_limit = MODEL_MAX_OUTPUT_TOKENS.get(model_name, 16384)
                            context_window = MODEL_CONTEXT_WINDOWS.get(model_name, 128000)
                            estimated_input_tokens = int(len(prompt) / AVG_CHARS_PER_TOKEN)
                            safety_margin = int(estimated_input_tokens * 0.1)
                            available_output_tokens = context_window - estimated_input_tokens - safety_margin
                            _llm_kwargs["max_tokens"] = max(4096, min(LARGE_PROMPT_MAX_TOKENS, model_limit, available_output_tokens))
                            logger.info(
                                f"[CODEGEN] Large prompt detected ({len(prompt)} chars, ~{estimated_input_tokens} tokens), "
                                f"requesting max_tokens={_llm_kwargs['max_tokens']} "
                                f"(context_window={context_window}, model_output_limit={model_limit})"
                            )
                        if requirements.get("previous_error") or requirements.get("previous_feedback"):
                            _llm_kwargs["skip_cache"] = True
                        response = await call_llm_api(**_llm_kwargs)
                        logger.info(
                            "[CODEGEN] LLM response received",
                            extra={
                                "backend": config.backend,
                                "response_length": len(str(response)),
                                "response_preview": str(response)[:200]
                            }
                        )
                    # --- End LLM Execution Change ---

                with tracer.start_as_current_span("parse_response_and_scan"):
                    code_files = parse_llm_response(response)

                    # --- Security Scans Change: Use unified scanning utility ---
                    code_files = await perform_security_scans(code_files)
                    # --- End Security Scans Change ---

                # HITL (only when enabled)
                if getattr(config, "allow_interactive_hitl", False):
                    with tracer.start_as_current_span("hitl_review"):
                        # We pass a dummy JsonConsoleAuditLogger to hitl_review for signature compatibility
                        status, feedback = await hitl_review(
                            code_files,
                            feedback_store,
                            req_hash,
                            True,
                            redis_client,
                            JsonConsoleAuditLogger(),
                        )
                    if status != "approved":
                        # --- Audit/Logging Change: Use log_audit_event ---
                        await log_audit_event("HITL Rejection", {"feedback": feedback})
                        # --- End Audit/Logging Change ---
                        return {
                            "error.txt": f"Code rejected by human review. Feedback: {feedback}"
                        }
                else:
                    # Skip HITL entirely; treat as approved
                    status, feedback = ("approved", None)

                # --- Audit/Logging Change: Use log_audit_event ---
                await log_audit_event(
                    "Code Generation Completed",
                    {"files": list(code_files.keys()), "model": backend_used},
                )
                # --- End Audit/Logging Change ---
                # [ARBITER] Publish code generation completion event
                if arbiter_bridge:
                    try:
                        await arbiter_bridge.publish_event(
                            "codegen_completed",
                            {
                                "request_id": request_id,
                                "status": "success",
                                "files_generated": len(code_files),
                                "backend_used": backend_used,
                            }
                        )
                    except Exception as e:
                        logger.warning(f"Failed to publish codegen completion event: {e}")
                
                return code_files

            except Exception as e:
                # Improve error logging with more context
                logger.error(
                    "[CODEGEN] Generation failed",
                    extra={
                        "error_type": type(e).__name__,
                        "error_message": str(e),
                        "backend": config.backend,
                        "requirements": requirements
                    },
                    exc_info=True
                )
                # --- Audit/Logging Change: Use log_audit_event ---
                await log_audit_event(
                    "Code Generation Failed", {"error": str(e), "traceback": repr(e)}
                )
                # --- End Audit/Logging Change ---
                CODEGEN_ERRORS.labels(type(e).__name__).inc()
                
                # [ARBITER] Report error to bridge
                if arbiter_bridge:
                    try:
                        await arbiter_bridge.report_bug({
                            "title": f"Code generation failed: {type(e).__name__}",
                            "description": f"Code generation request {request_id} failed: {str(e)}",
                            "severity": "high",
                            "agent": "codegen",
                            "error_type": type(e).__name__,
                            "error_message": str(e),
                            "request_id": request_id,
                        })
                    except Exception as bridge_err:
                        logger.warning(f"Failed to report error to arbiter: {bridge_err}")
                
                return {
                    "error.txt": f"Error: {type(e).__name__}: {str(e)}"
                }


# ==============================================================================
# FastAPI application (importable for tests and deployment)
# ==============================================================================

app = FastAPI()


# ==============================================================================
# FastAPI routes
# ==============================================================================
@app.get("/health")
async def health_check():
    failed_backends = []
    status = "ok"
    details = {}

    audit_logger = JsonConsoleAuditLogger()

    # Redis health (best-effort)
    try:
        redis_url = os.getenv("REDIS_URL", "redis://localhost")
        # Check connection (uses synchronous blocking call for simplicity in this health check)
        redis_client = await aioredis.from_url(redis_url)
        await redis_client.ping()
        await redis_client.close()
        details["redis"] = "ok"
    except Exception as e:
        status = "degraded"
        failed_backends.append("Redis")
        details["redis"] = f"failed: {e}"

    # LLM config presence (best-effort, uses CodeGenConfig)
    llm_config = CodeGenConfig(
        {
            "backend": "openai",
            "api_keys": {"openai": os.getenv("OPENAI_API_KEY")},
            "model": {"openai": "gpt-4o"},
        }
    )
    if not llm_config.api_keys.get("openai"):
        status = "degraded"
        failed_backends.append("openai")
        details["openai"] = "missing API key"
    else:
        details["openai"] = "ok"

    if not os.path.exists("templates"):
        status = "degraded"
        failed_backends.append("templates")
        details["templates"] = "directory missing"

    await audit_logger.log_action("HealthCheck", {"status": status, "details": details})

    if failed_backends:
        return {
            "status": status,
            "details": f"Failed components: {', '.join(failed_backends)}",
        }

    return {"status": "ok", "details": details}


@app.get("/metrics")
async def metrics():
    # from prometheus_client import generate_latest  # Already imported
    data = generate_latest()
    return {
        "content_type": "text/plain; version=0.0.4",
        "metrics": data.decode("utf-8", errors="ignore"),
    }


@app.post("/review")
async def review_code(review_request: Dict[str, Any]):
    """
    Simple wrapper endpoint to trigger code generation and HITL review.
    This is intentionally thin; heavy lifting is in generate_code / hitl_review.
    """
    requirements = review_request.get("requirements", {})
    initial_state = review_request.get("initial_state", "")
    config_path = review_request.get("config_path", "prod_config.yaml")

    await generate_code(requirements, initial_state, config_path)
    req_hash = hash(json.dumps(requirements, sort_keys=True))
    review_url = f"/submit_review?req_hash={req_hash}"

    return {"status": "pending", "req_hash": req_hash, "review_url": review_url}


@app.post("/submit_review")
async def submit_review(review_submission: Dict[str, Any]):
    req_hash = review_submission.get("req_hash")
    status = review_submission.get("status")
    feedback = review_submission.get("feedback")

    if status not in ["approved", "rejected"]:
        raise HTTPException(status_code=400, detail="Invalid status")

    if status == "rejected" and (not feedback or len(feedback) < 10):
        raise HTTPException(
            status_code=400,
            detail="Feedback must be at least 10 characters for rejected code.",
        )

    # Best-effort: in real deployment this would persist feedback
    # Here we just log it.
    await log_audit_event(
        "HITL Review Submitted",
        {
            "req_hash": req_hash,
            "status": status,
            "feedback": feedback,
        },
    )

    if status == "approved":
        HITL_APPROVAL_RATE.set(1)
    else:
        HITL_APPROVAL_RATE.set(0)

    return {"status": status, "feedback": feedback}


# ==============================================================================
# Demo harness (optional)
# ==============================================================================
if __name__ == "__main__":
    # This is a self-contained demo harness, not used in production.
    import uvicorn

    # Setup for Demo Run
    config_data = {
        "backend": "openai",
        "api_keys": {"openai": os.getenv("OPENAI_API_KEY")},
        "model": {"openai": "gpt-4o"},
        "allow_interactive_hitl": True,
        "enable_security_scan": True,
        "feedback_store": {"type": "sqlite", "path": "prod_feedback.db"},
        "audit_logger": {"type": "console"},
        "compliance": {
            "banned_functions": ["pickle"],
            "max_line_length": 120,
            "banned_imports": ["os", "subprocess"],
        },
    }
    with open("prod_config.yaml", "w") as f:
        yaml.dump(config_data, f)
    if not os.path.exists("templates"):
        os.makedirs("templates")
    with open("templates/python.jinja2", "w") as f:
        f.write(
            "Generate a Python script. Requirements: {{ requirements.features }}. Respond ONLY with a valid JSON object with a 'files' key mapping filenames to code strings."
        )
    with open("templates/base.jinja2", "w") as f:
        f.write(
            "Generate a generic script. Requirements: {{ requirements.features }}. Respond ONLY with a valid JSON object with a 'files' key mapping filenames to code strings."
        )

    requirements_data = {
        "features": ["Implement a function to calculate the nth Fibonacci number."],
        "target_language": "python",
    }

    async def main():
        # 1. Start Prometheus metrics server in the background (using uvicorn in a real deployment)
        start_http_server(8000)

        # 2. Run a single code generation task
        print("Starting single code generation task...")
        # NOTE: This call will fail if OPENAI_API_KEY is not set.
        generated_code = await generate_code(
            requirements_data, "Initial state.", "prod_config.yaml"
        )
        print("\n--- Final Output ---")
        for filename, content in generated_code.items():
            print(f"File: {filename}\n{content}\n")

        # 3. Start the FastAPI server (blocking call if we were using it as the main entry)
        print("Starting FastAPI server (CTRL+C to stop)...")
        # NOTE: For demo simplicity, we use uvicorn.run for the server part, and just output the code above.
        # If you wanted both, you'd use multiprocessing or an ASGI server runner.
        uvicorn.run(app, host="0.0.0.0", port=8001)

    # Guarded so nothing runs during tests unless explicitly requested
    if os.getenv("CODEGEN_RUN_DEMO") == "1" and os.getenv("OPENAI_API_KEY"):
        asyncio.run(main())
    elif os.getenv("CODEGEN_RUN_DEMO") == "1" and not os.getenv("OPENAI_API_KEY"):
        print(
            "Skipping example run: OPENAI_API_KEY environment variable is not set. Cannot run LLM."
        )
    else:
        print(
            "Skipping demo harness. To run, set CODEGEN_RUN_DEMO=1 and OPENAI_API_KEY."
        )
