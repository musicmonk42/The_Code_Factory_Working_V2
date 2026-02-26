# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# generator/tests/test_main_post_materialize.py
"""Unit tests for generator/main/post_materialize.py — Phase 8: auto-wire routers."""

from pathlib import Path

import pytest

from generator.main.post_materialize import _auto_wire_routers, PostMaterializeResult, post_materialize


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


def test_auto_wire_skipped_when_already_wired(tmp_path: Path) -> None:
    """Phase 8 must not double-wire when include_router is already present."""
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

    assert (app_dir / "main.py").read_text() == original
    assert result.files_created == []


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
