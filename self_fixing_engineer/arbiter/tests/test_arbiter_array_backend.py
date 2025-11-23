"""
Async, production-grade tests for arbiter_array_backend.

Aligns with the REAL backend API:
- async: initialize, append, get, update, delete, query, rotate_encryption_key, health_check, on_reload
- JSON persistence default; storage_path is a file path for JSON
- size limit via env ARRAY_MAX_SIZE
- optional encryption via ARRAY_ENCRYPTION_ENABLED + SFE_ENCRYPTION_KEY

Run:
  pytest -q arbiter/tests/test_arbiter_array_backend.py
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest


# -------------------------------------------------------------------
# Robust import: supports either repo_root/arbiter_array_backend.py
# or repo_root/arbiter/arbiter_array_backend.py without PYTHONPATH.
# -------------------------------------------------------------------
def _import_backend():
    import importlib
    import sys
    from importlib.util import module_from_spec, spec_from_file_location
    from pathlib import Path

    candidates = ("arbiter.arbiter_array_backend", "arbiter_array_backend")
    for name in candidates:
        try:
            return importlib.import_module(name)
        except Exception:
            pass

    here = Path(__file__).resolve()
    repo_root = here.parents[2]
    for p in (repo_root, repo_root / "arbiter"):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)

    for name in candidates:
        try:
            return importlib.import_module(name)
        except Exception:
            pass

    # Last resort: direct file load
    for f in (
        repo_root / "arbiter_array_backend.py",
        repo_root / "arbiter" / "arbiter_array_backend.py",
    ):
        if f.exists():
            spec = spec_from_file_location("arbiter_array_backend_fallback", str(f))
            assert spec and spec.loader
            mod = module_from_spec(spec)
            sys.modules[spec.name] = mod
            spec.loader.exec_module(mod)  # type: ignore[arg-type]
            return mod
    raise ImportError("Cannot import arbiter_array_backend")


_backend = _import_backend()

ConcreteArrayBackend = getattr(_backend, "ConcreteArrayBackend")
ArrayBackendError = getattr(_backend, "ArrayBackendError")
ArraySizeLimitError = getattr(_backend, "ArraySizeLimitError")
StorageError = getattr(_backend, "StorageError")
ArrayMeta = getattr(_backend, "ArrayMeta")


# -----------------
# Fixtures / utils
# -----------------
@pytest.fixture
def json_file(tmp_path: Path) -> Path:
    return tmp_path / "array.json"


@pytest.fixture
def fresh_env(monkeypatch):
    # Make sure defaults are predictable per test
    monkeypatch.delenv("ARRAY_MAX_SIZE", raising=False)
    monkeypatch.setenv("ARRAY_PAGE_SIZE", "1000")
    monkeypatch.setenv("ARRAY_ENCRYPTION_ENABLED", "false")
    return monkeypatch


@pytest.fixture
async def backend(json_file: Path, fresh_env) -> ConcreteArrayBackend:
    be = ConcreteArrayBackend(
        name="test_array", storage_path=str(json_file), storage_type="json"
    )
    await be.initialize()
    yield be
    # graceful close if implemented
    try:
        await be.close()
    except Exception:
        pass


# -------------
# Basic flows
# -------------
@pytest.mark.asyncio
async def test_initialize_empty_and_get_page(backend: ConcreteArrayBackend):
    # Fresh JSON store yields empty page
    page = await backend.get()  # defaults to current page
    assert isinstance(page, list)
    assert page == []


@pytest.mark.asyncio
async def test_append_get_update_delete_roundtrip(backend: ConcreteArrayBackend):
    await backend.append({"id": 1, "v": "a"})
    await backend.append({"id": 2, "v": "b"})
    await backend.append({"id": 3, "v": "c"})

    # get by index
    assert await backend.get(0) == {"id": 1, "v": "a"}
    assert await backend.get(2) == {"id": 3, "v": "c"}

    # update
    await backend.update(1, {"id": 2, "v": "bb"})
    assert await backend.get(1) == {"id": 2, "v": "bb"}

    # delete
    await backend.delete(0)
    with pytest.raises(IndexError):
        await backend.get(2)  # old index 2 shifted after delete, should now be OOB


@pytest.mark.asyncio
async def test_persistence_reopen(json_file: Path, fresh_env):
    be1 = ConcreteArrayBackend(
        name="persist", storage_path=str(json_file), storage_type="json"
    )
    await be1.initialize()
    await be1.append({"n": 1})
    await be1.append({"n": 2})
    await be1.close()

    # reopen a new instance on same file
    be2 = ConcreteArrayBackend(
        name="persist", storage_path=str(json_file), storage_type="json"
    )
    await be2.initialize()
    page = await be2.get()
    assert page == [{"n": 1}, {"n": 2}]
    await be2.close()


# -------------
# Limits / query
# -------------
@pytest.mark.asyncio
async def test_size_limit_enforced(json_file: Path, fresh_env):
    fresh_env.setenv("ARRAY_MAX_SIZE", "2")
    be = ConcreteArrayBackend(
        name="limit", storage_path=str(json_file), storage_type="json"
    )
    await be.initialize()
    await be.append(1)
    await be.append(2)
    with pytest.raises(ArraySizeLimitError):
        await be.append(3)
    await be.close()


@pytest.mark.asyncio
async def test_query_predicate(backend: ConcreteArrayBackend):
    for i in range(10):
        await backend.append({"i": i})
    # keep even items
    out = await backend.query(lambda item: item.get("i", -1) % 2 == 0)
    assert isinstance(out, list)
    assert all(x["i"] % 2 == 0 for x in out)
    assert len(out) == 5


# --------------------
# Encryption / rotate
# --------------------
@pytest.mark.asyncio
async def test_rotate_encryption_key(json_file: Path, fresh_env):
    from cryptography.fernet import Fernet

    fresh_env.setenv("ARRAY_ENCRYPTION_ENABLED", "true")
    fresh_env.setenv("SFE_ENCRYPTION_KEY", Fernet.generate_key().decode())

    be = ConcreteArrayBackend(
        name="enc", storage_path=str(json_file), storage_type="json"
    )
    await be.initialize()
    await be.append({"secret": 1})
    await be.append({"secret": 2})

    new_key = Fernet.generate_key()
    await be.rotate_encryption_key(new_key)
    # still readable after rotation
    page = await be.get()
    assert page == [{"secret": 1}, {"secret": 2}]
    await be.close()


# --------------------
# Health & reload
# --------------------
@pytest.mark.asyncio
async def test_health_check(backend: ConcreteArrayBackend):
    health = await backend.health_check()
    assert isinstance(health, dict)
    assert health.get("status") in {
        "healthy",
        "unhealthy",
    }  # json backend reports healthy if file exists


@pytest.mark.asyncio
async def test_on_reload_triggers_background_load(backend: ConcreteArrayBackend):
    # append some data then "reload"
    await backend.append({"r": 1})
    backend.on_reload()
    # allow the scheduled task to run
    await asyncio.sleep(0.05)
    page = await backend.get()
    assert page and page[0] == {"r": 1}


# --------------------
# Corruption recovery
# --------------------
@pytest.mark.asyncio
async def test_json_corruption_recovers_to_empty(json_file: Path, fresh_env):
    be = ConcreteArrayBackend(
        name="corrupt", storage_path=str(json_file), storage_type="json"
    )
    await be.initialize()
    await be.append({"x": 1})
    await be.close()

    # corrupt the JSON file
    json_file.write_text("{ this is not valid json ")

    # loader should not raise — it logs a warning and resets to []
    be2 = ConcreteArrayBackend(
        name="corrupt", storage_path=str(json_file), storage_type="json"
    )
    await be2.initialize()
    page = await be2.get()
    assert page == []
    await be2.close()
