# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# generator/tests/test_main_post_materialize.py
"""Unit tests for generator/main/post_materialize.py — Phase 8: auto-wire routers,
plus _ensure_initial_migration (Alembic initial migration generation)."""

import ast
from pathlib import Path

import pytest

from generator.main.post_materialize import (
    _auto_wire_routers,
    _ensure_initial_migration,
    PostMaterializeResult,
    post_materialize,
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_result() -> PostMaterializeResult:
    return PostMaterializeResult()


# ---------------------------------------------------------------------------
# Phase 8: _auto_wire_routers
# ---------------------------------------------------------------------------


def test_auto_wire_routers_into_main(tmp_path: Path) -> None:
    """Auto-wire router files into main.py when include_router is missing."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    routers_dir = app_dir / "routers"
    routers_dir.mkdir()
    (routers_dir / "__init__.py").write_text("")
    (routers_dir / "products.py").write_text(
        "from fastapi import APIRouter\nrouter = APIRouter()\n"
        "@router.get('/products')\ndef list_products(): pass\n"
    )
    (app_dir / "main.py").write_text(
        "from fastapi import FastAPI\napp = FastAPI()\n"
    )

    result = _make_result()
    _auto_wire_routers(tmp_path, result)

    main_content = (app_dir / "main.py").read_text()
    assert "include_router" in main_content
    assert "from app.routers.products import router as products_router" in main_content
    assert 'app.include_router(products_router, prefix="/api/v1/products")' in main_content


def test_auto_wire_import_inserted_after_existing_imports(tmp_path: Path) -> None:
    """Router imports must appear after — not before — existing import statements."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    routers_dir = app_dir / "routers"
    routers_dir.mkdir()
    (routers_dir / "__init__.py").write_text("")
    (routers_dir / "orders.py").write_text(
        "from fastapi import APIRouter\nrouter = APIRouter()\n"
    )
    (app_dir / "main.py").write_text(
        "from fastapi import FastAPI\n"
        "from app.config import settings\n"
        "app = FastAPI(title=settings.APP_TITLE)\n"
    )

    result = _make_result()
    _auto_wire_routers(tmp_path, result)

    main_content = (app_dir / "main.py").read_text()
    assert "include_router" in main_content
    # The router import should come after the existing imports, not before them.
    fastapi_pos = main_content.index("from fastapi import FastAPI")
    router_import_pos = main_content.index("from app.routers.orders")
    assert router_import_pos > fastapi_pos


def test_auto_wire_wire_calls_after_fastapi_instantiation(tmp_path: Path) -> None:
    """include_router calls must be placed after app = FastAPI(...)."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    routers_dir = app_dir / "routers"
    routers_dir.mkdir()
    (routers_dir / "__init__.py").write_text("")
    (routers_dir / "users.py").write_text(
        "from fastapi import APIRouter\nrouter = APIRouter()\n"
    )
    (app_dir / "main.py").write_text(
        "from fastapi import FastAPI\napp = FastAPI()\n"
    )

    result = _make_result()
    _auto_wire_routers(tmp_path, result)

    main_content = (app_dir / "main.py").read_text()
    fastapi_pos = main_content.index("app = FastAPI()")
    wire_pos = main_content.index("app.include_router(users_router")
    assert wire_pos > fastapi_pos


def test_auto_wire_adds_missing_prefix(tmp_path: Path) -> None:
    """Phase 8 adds a missing /api/v1/ prefix to routers already wired without one."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    routers_dir = app_dir / "routers"
    routers_dir.mkdir()
    (routers_dir / "__init__.py").write_text("")
    (routers_dir / "orders.py").write_text(
        "from fastapi import APIRouter\nrouter = APIRouter()\n"
    )
    original = (
        "from fastapi import FastAPI\n"
        "from app.routers.orders import router as orders_router\n"
        "app = FastAPI()\n"
        "app.include_router(orders_router)\n"
    )
    (app_dir / "main.py").write_text(original)

    result = _make_result()
    _auto_wire_routers(tmp_path, result)

    # The prefix should have been injected since orders_router was wired without one.
    expected = original.replace(
        "app.include_router(orders_router)",
        'app.include_router(orders_router, prefix="/api/v1/orders")',
    )
    assert (app_dir / "main.py").read_text() == expected
    assert result.files_created != []


def test_auto_wire_skipped_when_no_router_dir(tmp_path: Path) -> None:
    """Phase 8 must be a no-op when app/routers/ does not exist."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    original = "from fastapi import FastAPI\napp = FastAPI()\n"
    (app_dir / "main.py").write_text(original)

    result = _make_result()
    _auto_wire_routers(tmp_path, result)

    assert (app_dir / "main.py").read_text() == original
    assert result.files_created == []


def test_auto_wire_skipped_when_no_router_files(tmp_path: Path) -> None:
    """Phase 8 must be a no-op when app/routers/ exists but contains only __init__.py."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    routers_dir = app_dir / "routers"
    routers_dir.mkdir()
    (routers_dir / "__init__.py").write_text("")
    original = "from fastapi import FastAPI\napp = FastAPI()\n"
    (app_dir / "main.py").write_text(original)

    result = _make_result()
    _auto_wire_routers(tmp_path, result)

    assert (app_dir / "main.py").read_text() == original
    assert result.files_created == []


def test_auto_wire_multiple_routers(tmp_path: Path) -> None:
    """All router modules must be wired when multiple router files exist."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    routers_dir = app_dir / "routers"
    routers_dir.mkdir()
    (routers_dir / "__init__.py").write_text("")
    for mod in ("products", "users", "orders"):
        (routers_dir / f"{mod}.py").write_text(
            "from fastapi import APIRouter\nrouter = APIRouter()\n"
        )
    (app_dir / "main.py").write_text(
        "from fastapi import FastAPI\napp = FastAPI()\n"
    )

    result = _make_result()
    _auto_wire_routers(tmp_path, result)

    main_content = (app_dir / "main.py").read_text()
    for mod in ("products", "users", "orders"):
        assert f"from app.routers.{mod} import router as {mod}_router" in main_content
        assert f"app.include_router({mod}_router" in main_content


def test_auto_wire_records_modified_file(tmp_path: Path) -> None:
    """Wired files must be recorded in result.files_created."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    routers_dir = app_dir / "routers"
    routers_dir.mkdir()
    (routers_dir / "__init__.py").write_text("")
    (routers_dir / "items.py").write_text(
        "from fastapi import APIRouter\nrouter = APIRouter()\n"
    )
    (app_dir / "main.py").write_text(
        "from fastapi import FastAPI\napp = FastAPI()\n"
    )

    result = _make_result()
    _auto_wire_routers(tmp_path, result)

    assert "app/main.py" in result.files_created


def test_auto_wire_preserves_module_docstring(tmp_path: Path) -> None:
    """Module docstring must remain the first statement — imports go after it."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    routers_dir = app_dir / "routers"
    routers_dir.mkdir()
    (routers_dir / "__init__.py").write_text("")
    (routers_dir / "products.py").write_text(
        "from fastapi import APIRouter\nrouter = APIRouter()\n"
    )
    (app_dir / "main.py").write_text(
        '"""Application entry point."""\nfrom fastapi import FastAPI\napp = FastAPI()\n'
    )

    result = _make_result()
    _auto_wire_routers(tmp_path, result)

    main_content = (app_dir / "main.py").read_text()
    assert main_content.split("\n")[0].startswith('"""'), (
        "module docstring must remain the first line"
    )
    assert "include_router" in main_content


def test_auto_wire_multiline_fastapi_constructor(tmp_path: Path) -> None:
    """Wire calls must be placed after the closing parenthesis of a multi-line FastAPI()."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    routers_dir = app_dir / "routers"
    routers_dir.mkdir()
    (routers_dir / "__init__.py").write_text("")
    (routers_dir / "items.py").write_text(
        "from fastapi import APIRouter\nrouter = APIRouter()\n"
    )
    (app_dir / "main.py").write_text(
        "from fastapi import FastAPI\n"
        "app = FastAPI(\n"
        "    title='My App',\n"
        "    version='1.0.0',\n"
        ")\n"
    )

    result = _make_result()
    _auto_wire_routers(tmp_path, result)

    main_content = (app_dir / "main.py").read_text()
    assert "include_router" in main_content
    # Wire call must come after the closing ) of the multi-line FastAPI()
    closing_paren_pos = main_content.rindex(")\n", 0, main_content.index("include_router"))
    wire_pos = main_content.index("app.include_router(items_router")
    assert wire_pos > closing_paren_pos, (
        "include_router must appear after the closing ) of FastAPI()"
    )


# ---------------------------------------------------------------------------
# Integration: post_materialize wires routers end-to-end
# ---------------------------------------------------------------------------


def test_post_materialize_auto_wire_routers_integration(tmp_path: Path) -> None:
    """post_materialize() end-to-end: routers are wired into main.py."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    routers_dir = app_dir / "routers"
    routers_dir.mkdir()
    (routers_dir / "__init__.py").write_text("")
    (routers_dir / "products.py").write_text(
        "from fastapi import APIRouter\nrouter = APIRouter()\n"
        "@router.get('/products')\ndef list_products(): pass\n"
    )
    (app_dir / "main.py").write_text(
        "from fastapi import FastAPI\napp = FastAPI()\n"
    )
    # Provide minimal required files so earlier phases don't interfere.
    (tmp_path / "README.md").write_text("# Test\n")

    result = post_materialize(tmp_path)

    main_content = (app_dir / "main.py").read_text()
    assert "include_router" in main_content
    assert result.success


def test_auto_wire_routers_from_routes_dir(tmp_path: Path) -> None:
    """_auto_wire_routers must wire routers from app/routes/ when app/routers/ is absent."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    routes_dir = app_dir / "routes"
    routes_dir.mkdir()
    (routes_dir / "__init__.py").write_text("")
    (routes_dir / "products.py").write_text(
        "from fastapi import APIRouter\nrouter = APIRouter()\n"
        "@router.get('/products')\ndef list_products(): pass\n"
    )
    (app_dir / "main.py").write_text(
        "from fastapi import FastAPI\napp = FastAPI()\n"
    )

    result = _make_result()
    _auto_wire_routers(tmp_path, result)

    main_content = (app_dir / "main.py").read_text()
    assert "include_router" in main_content
    assert "from app.routes.products import router as products_router" in main_content
    assert 'app.include_router(products_router, prefix="/api/v1/products")' in main_content


def test_auto_wire_not_skipped_when_stubs_are_none(tmp_path: Path) -> None:
    """_auto_wire_routers must wire modules not yet imported via the per-module path.

    Even when 'include_router' is already present in main.py (from an old stub
    that imported a None value), routers that are not yet imported from their
    specific module path should still be wired.
    """
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    routers_dir = app_dir / "routers"
    routers_dir.mkdir()
    (routers_dir / "__init__.py").write_text("")
    (routers_dir / "products.py").write_text(
        "from fastapi import APIRouter\nrouter = APIRouter()\n"
        "@router.get('/products')\ndef list_products(): pass\n"
    )
    # main.py has include_router but imports from a stub package (not module-specific path)
    (app_dir / "main.py").write_text(
        "from fastapi import FastAPI\n"
        "from app.routers import products_router\n"
        "app = FastAPI()\n"
        "app.include_router(products_router, prefix='/api/v1')\n"
    )

    result = _make_result()
    _auto_wire_routers(tmp_path, result)

    main_content = (app_dir / "main.py").read_text()
    # The per-module import must now be injected.
    assert "from app.routers.products import router as products_router" in main_content


def test_auto_wire_adds_prefix_to_already_wired_router(tmp_path: Path) -> None:
    """Phase 8 must inject /api/v1/{mod} prefix when router is wired without one."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    routers_dir = app_dir / "routers"
    routers_dir.mkdir()
    (routers_dir / "__init__.py").write_text("")
    (routers_dir / "products.py").write_text(
        "from fastapi import APIRouter\nrouter = APIRouter()\n"
    )
    original = (
        "from fastapi import FastAPI\n"
        "from app.routers.products import router as products_router\n"
        "app = FastAPI()\n"
        "app.include_router(products_router)\n"
    )
    (app_dir / "main.py").write_text(original)

    result = _make_result()
    _auto_wire_routers(tmp_path, result)

    content = (app_dir / "main.py").read_text()
    assert 'prefix="/api/v1/products"' in content, (
        "Missing /api/v1/products prefix should have been injected"
    )
    assert result.files_created != [], "modified file should be recorded"


def test_auto_wire_skips_prefix_injection_for_health_routers(tmp_path: Path) -> None:
    """Phase 8 must NOT inject /api/v1/ prefix for health/utility routers."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    routers_dir = app_dir / "routers"
    routers_dir.mkdir()
    (routers_dir / "__init__.py").write_text("")
    (routers_dir / "health.py").write_text(
        "from fastapi import APIRouter\nrouter = APIRouter()\n"
    )
    original = (
        "from fastapi import FastAPI\n"
        "from app.routers.health import router as health_router\n"
        "app = FastAPI()\n"
        "app.include_router(health_router)\n"
    )
    (app_dir / "main.py").write_text(original)

    result = _make_result()
    _auto_wire_routers(tmp_path, result)

    content = (app_dir / "main.py").read_text()
    assert 'prefix="/api/v1/health"' not in content, (
        "health router must not receive an /api/v1/ prefix"
    )
    # file may or may not be rewritten — but the prefix must not appear
    assert "health_router)" in content or 'health_router, prefix' not in content


def test_auto_wire_does_not_duplicate_existing_prefix(tmp_path: Path) -> None:
    """Phase 8 must not modify a router that is already wired with a prefix."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    routers_dir = app_dir / "routers"
    routers_dir.mkdir()
    (routers_dir / "__init__.py").write_text("")
    (routers_dir / "orders.py").write_text(
        "from fastapi import APIRouter\nrouter = APIRouter()\n"
    )
    original = (
        "from fastapi import FastAPI\n"
        "from app.routers.orders import router as orders_router\n"
        "app = FastAPI()\n"
        'app.include_router(orders_router, prefix="/api/v1/orders")\n'
    )
    (app_dir / "main.py").write_text(original)

    result = _make_result()
    _auto_wire_routers(tmp_path, result)

    content = (app_dir / "main.py").read_text()
    # The existing prefix must not be duplicated
    assert content.count('prefix="/api/v1/orders"') == 1, (
        "Existing prefix must not be duplicated"
    )
    assert content == original, "File must not be changed when prefix already exists"


# ---------------------------------------------------------------------------
# _ensure_initial_migration
# ---------------------------------------------------------------------------


def _scaffold_alembic(tmp_path: Path) -> Path:
    """Create the minimal alembic/versions/ directory structure."""
    versions_dir = tmp_path / "alembic" / "versions"
    versions_dir.mkdir(parents=True)
    (versions_dir / ".gitkeep").write_text("# placeholder\n")
    return versions_dir


def test_initial_migration_created_when_versions_empty(tmp_path: Path) -> None:
    """001_initial.py must be generated when alembic/versions/ has no .py files."""
    _scaffold_alembic(tmp_path)

    result = _make_result()
    _ensure_initial_migration(tmp_path, result)

    migration_file = tmp_path / "alembic" / "versions" / "001_initial.py"
    assert migration_file.exists(), "001_initial.py must be created"
    content = migration_file.read_text()
    assert 'revision: str = "001_initial"' in content
    assert "def upgrade" in content
    assert "def downgrade" in content
    assert str(migration_file.relative_to(tmp_path)) in result.files_created


def test_initial_migration_uses_module_imports_not_wildcards(tmp_path: Path) -> None:
    """Generated migration must use explicit module imports, not wildcard 'import *'."""
    _scaffold_alembic(tmp_path)
    models_dir = tmp_path / "app" / "models"
    models_dir.mkdir(parents=True)
    (models_dir / "patient.py").write_text("x = 1\n")
    (models_dir / "user.py").write_text("x = 1\n")

    result = _make_result()
    _ensure_initial_migration(tmp_path, result)

    content = (tmp_path / "alembic" / "versions" / "001_initial.py").read_text()
    # Must use explicit module-level imports, not wildcard syntax.
    assert "import *" not in content, "Wildcard imports must not be used"
    assert "import app.models.patient" in content
    assert "import app.models.user" in content
    # Models must be imported inside the try: block (indented).
    assert "    import app.models.patient" in content


def test_initial_migration_skips_private_model_files(tmp_path: Path) -> None:
    """Model files starting with '_' (e.g. __init__.py) must be excluded."""
    _scaffold_alembic(tmp_path)
    models_dir = tmp_path / "app" / "models"
    models_dir.mkdir(parents=True)
    (models_dir / "__init__.py").write_text("")
    (models_dir / "_base.py").write_text("x = 1\n")
    (models_dir / "order.py").write_text("x = 1\n")

    result = _make_result()
    _ensure_initial_migration(tmp_path, result)

    content = (tmp_path / "alembic" / "versions" / "001_initial.py").read_text()
    assert "import app.models.order" in content
    assert "import app.models.__init__" not in content
    assert "import app.models._base" not in content


def test_initial_migration_idempotent_when_migrations_exist(tmp_path: Path) -> None:
    """_ensure_initial_migration must not overwrite existing migration files."""
    versions_dir = _scaffold_alembic(tmp_path)
    existing = versions_dir / "002_custom.py"
    existing_content = "# custom migration\n"
    existing.write_text(existing_content)

    result = _make_result()
    _ensure_initial_migration(tmp_path, result)

    # The existing file must not be touched.
    assert existing.read_text() == existing_content
    # 001_initial.py must NOT be created since migrations already exist.
    assert not (versions_dir / "001_initial.py").exists()
    assert result.files_created == []


def test_initial_migration_skips_when_no_versions_dir(tmp_path: Path) -> None:
    """_ensure_initial_migration must be a no-op when alembic/versions/ does not exist."""
    result = _make_result()
    _ensure_initial_migration(tmp_path, result)  # Must not raise
    assert result.files_created == []


def test_initial_migration_no_model_dir_produces_placeholder(tmp_path: Path) -> None:
    """When app/models/ does not exist the migration must include a placeholder comment."""
    _scaffold_alembic(tmp_path)

    result = _make_result()
    _ensure_initial_migration(tmp_path, result)

    content = (tmp_path / "alembic" / "versions" / "001_initial.py").read_text()
    # Should have a fallback pass or placeholder, not broken syntax.
    assert "pass" in content or "No model files" in content
    # File must be syntactically valid Python.
    try:
        ast.parse(content)
    except SyntaxError as exc:
        pytest.fail(f"Generated migration is not valid Python: {exc}")
