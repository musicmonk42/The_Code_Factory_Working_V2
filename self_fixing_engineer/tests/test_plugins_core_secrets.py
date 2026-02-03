import logging
import os
import threading
from pathlib import Path

from plugins import core_secrets
import pytest

# --- Test Fixtures and Helpers ---


@pytest.fixture(autouse=True)
def reset_singleton():
    # Reset singleton between tests
    core_secrets.SecretsManager._instance = None
    yield
    core_secrets.SecretsManager._instance = None


@pytest.fixture
def sandbox_env(monkeypatch):
    old_env = dict(os.environ)
    monkeypatch.setattr(os, "environ", dict())
    yield
    monkeypatch.setattr(os, "environ", old_env)


@pytest.fixture
def temp_env_file(tmp_path):
    file = tmp_path / ".env"
    yield file


@pytest.fixture
def secrets_manager(tmp_path, sandbox_env):
    # Use a temp env file and logger
    logger = logging.getLogger("test_secrets")
    logger.handlers = []
    sm = core_secrets.SecretsManager(
        env_file=str(tmp_path / ".env"), logger=logger, allow_dotenv=True
    )
    return sm


# --- Tests ---


def test_singleton_behavior(secrets_manager):
    sm1 = secrets_manager
    sm2 = core_secrets.SecretsManager()
    assert sm1 is sm2


def test_env_file_loading(monkeypatch, tmp_path):
    # Create a fake .env file
    envfile = tmp_path / ".env"
    envfile.write_text("TEST_KEY=envval\n")
    monkeypatch.setenv("ENVIRONMENT", "dev")
    # Patch load_dotenv to actually load the file
    import importlib

    importlib.invalidate_caches()
    sm = core_secrets.SecretsManager(env_file=str(envfile), allow_dotenv=True)
    assert sm.get_secret("TEST_KEY") == "envval"


def test_env_file_load_disabled_in_prod(monkeypatch, tmp_path, caplog):
    envfile = tmp_path / ".env"
    envfile.write_text("PROD_KEY=shouldnotload\n")
    monkeypatch.setenv("ENVIRONMENT", "prod")
    sm = core_secrets.SecretsManager(env_file=str(envfile), allow_dotenv=False)
    assert sm.get_secret("PROD_KEY") is None
    assert "Skipping .env file load" in caplog.text


def test_env_file_override_allowed(monkeypatch, tmp_path, caplog):
    envfile = tmp_path / ".env"
    envfile.write_text("PROD_OVERRIDE=works\n")
    monkeypatch.setenv("ENVIRONMENT", "prod")
    monkeypatch.setenv("SECRETS_ALLOW_DOTENV_IN_PROD", "1")
    sm = core_secrets.SecretsManager(env_file=str(envfile))
    assert sm.get_secret("PROD_OVERRIDE") == "works"
    assert "override detected" in caplog.text


def test_name_validation_strict_and_non_strict(monkeypatch):
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    sm = core_secrets.SecretsManager(allow_dotenv=False)
    # Non-strict allows lowercase
    assert sm.get_secret("my_key", default=123) == 123
    # Strict mode (simulate prod)
    monkeypatch.setenv("ENVIRONMENT", "prod")
    core_secrets.SecretsManager._instance = None  # force re-init
    sm2 = core_secrets.SecretsManager(allow_dotenv=False)
    with pytest.raises(ValueError):
        sm2.get_secret("my_key")
    with pytest.raises(ValueError):
        sm2.get_secret("BAD KEY")
    with pytest.raises(ValueError):
        sm2.get_secret("BAD=KEY")
    assert sm2.get_secret("VALID_KEY", default=1) == 1


def test_blank_values_treated_as_missing(secrets_manager, monkeypatch):
    monkeypatch.setenv("FOO", "")
    assert secrets_manager.get_secret("FOO") is None
    assert secrets_manager.get_secret("FOO", blank_ok=True) == ""


def test_required_secret_missing_raises(secrets_manager):
    with pytest.raises(RuntimeError):
        secrets_manager.get_secret("MISSING", required=True)


def test_cache_behavior(secrets_manager, monkeypatch):
    monkeypatch.setenv("CACHEME", "abc")
    secrets_manager.clear_cache()
    val1 = secrets_manager.get_secret("CACHEME")
    monkeypatch.setenv("CACHEME", "changed")
    val2 = secrets_manager.get_secret("CACHEME")  # from cache
    assert val1 == val2 == "abc"
    secrets_manager.clear_cache_key("CACHEME")
    val3 = secrets_manager.get_secret("CACHEME")
    assert val3 == "changed"


def test_reload_clears_cache_and_reloads(monkeypatch, tmp_path):
    envfile = tmp_path / ".env"
    envfile.write_text("HELLO=WORLD\n")
    monkeypatch.setenv("HELLO", "X")
    sm = core_secrets.SecretsManager(env_file=str(envfile), allow_dotenv=True)
    assert sm.get_secret("HELLO") == "X"
    del os.environ["HELLO"]
    sm.clear_cache()
    sm.reload()
    assert sm.get_secret("HELLO") == "WORLD"


def test_set_secret_sets_env_and_cache(secrets_manager):
    val = secrets_manager.set_secret("SETME", 42)
    assert val == "42"
    assert os.environ["SETME"] == "42"
    assert secrets_manager.get_secret("SETME") == "42"
    with pytest.raises(ValueError):
        secrets_manager.set_secret("SETNONE", None)


def test_get_with_fallback(secrets_manager, monkeypatch):
    monkeypatch.setenv("A", "")
    monkeypatch.setenv("B", "bval")
    monkeypatch.setenv("C", "cval")
    assert secrets_manager.get_with_fallback(["A", "B", "C"]) == "bval"
    assert secrets_manager.get_with_fallback(["A", "Z"], default="def") == "def"


def test_type_casting(secrets_manager, monkeypatch):
    monkeypatch.setenv("INTVAL", "9")
    monkeypatch.setenv("FLOATVAL", "1.23")
    monkeypatch.setenv("BOOLTRUE", "yes")
    monkeypatch.setenv("BOOLFALSE", "No")
    monkeypatch.setenv("STRVAL", "abc")
    assert secrets_manager.get_int("INTVAL") == 9
    assert secrets_manager.get_float("FLOATVAL") == 1.23
    assert secrets_manager.get_bool("BOOLTRUE") is True
    assert secrets_manager.get_bool("BOOLFALSE") is False
    assert secrets_manager.get_secret("STRVAL") == "abc"
    with pytest.raises(TypeError):
        secrets_manager.get_int("STRVAL")


def test_cast_bool_strict_and_loose():
    assert core_secrets.cast_bool_strict("yes") is True
    assert core_secrets.cast_bool_strict("no") is False
    with pytest.raises(TypeError):
        core_secrets.cast_bool_strict("maybe")
    # _cast_to_bool is more lenient
    assert core_secrets._cast_to_bool("maybe") is False
    assert core_secrets._cast_to_bool("TRUE") is True


def test_get_choice(secrets_manager, monkeypatch):
    monkeypatch.setenv("COLOR", "Red")
    assert secrets_manager.get_choice("COLOR", {"red", "blue"}) == "Red"
    with pytest.raises(TypeError):
        secrets_manager.get_choice("COLOR", {"blue", "green"})


def test_get_json(secrets_manager, monkeypatch):
    monkeypatch.setenv("JDATA", '{"k":1}')
    assert secrets_manager.get_json("JDATA") == {"k": 1}
    monkeypatch.setenv("BADJSON", "{")
    with pytest.raises(TypeError):
        secrets_manager.get_json("BADJSON")


def test_get_list(secrets_manager, monkeypatch):
    monkeypatch.setenv("LISTVALS", "a, b ,c")
    assert secrets_manager.get_list("LISTVALS") == ["a", "b", "c"]
    monkeypatch.setenv("EMPTY", "")
    assert secrets_manager.get_list("EMPTY", default=["z"]) == ["z"]


def test_get_path(secrets_manager, monkeypatch, tmp_path):
    monkeypatch.setenv("PVAL", str(tmp_path))
    p = secrets_manager.get_path("PVAL")
    assert isinstance(p, Path) and p.exists()


def test_get_bytes_and_get_duration(secrets_manager, monkeypatch):
    monkeypatch.setenv("B1", "100")
    monkeypatch.setenv("B2", "2k")
    monkeypatch.setenv("B3", "3M")
    monkeypatch.setenv("B4", "4G")
    monkeypatch.setenv("B5", "5T")
    assert secrets_manager.get_bytes("B1") == 100
    assert secrets_manager.get_bytes("B2") == 2 * 1024
    assert secrets_manager.get_bytes("B3") == 3 * 1024**2
    assert secrets_manager.get_bytes("B4") == 4 * 1024**3
    assert secrets_manager.get_bytes("B5") == 5 * 1024**4
    monkeypatch.setenv("D1", "3.5s")
    monkeypatch.setenv("D2", "2m")
    monkeypatch.setenv("D3", "1h")
    monkeypatch.setenv("D4", "1d")
    monkeypatch.setenv("D5", "123")
    assert secrets_manager.get_duration("D1") == 3.5
    assert secrets_manager.get_duration("D2") == 120
    assert secrets_manager.get_duration("D3") == 3600
    assert secrets_manager.get_duration("D4") == 86400
    assert secrets_manager.get_duration("D5") == 123.0
    monkeypatch.setenv("BADBYTES", "10Q")
    with pytest.raises(TypeError):
        secrets_manager.get_bytes("BADBYTES")
    monkeypatch.setenv("BADDUR", "5weeks")
    with pytest.raises(TypeError):
        secrets_manager.get_duration("BADDUR")


def test_get_int_in_range(secrets_manager, monkeypatch):
    monkeypatch.setenv("RINT", "7")
    assert secrets_manager.get_int_in_range("RINT", min_val=5, max_val=10) == 7
    with pytest.raises(TypeError):
        secrets_manager.get_int_in_range("RINT", min_val=8, max_val=10)


def test_snapshot(secrets_manager, monkeypatch):
    monkeypatch.setenv("A", "x")
    monkeypatch.setenv("B", "")
    monkeypatch.setenv("C", "y")
    secrets_manager.clear_cache()
    snap = secrets_manager.snapshot({"A", "B", "C", "D"})
    assert snap["A"] == "set"
    assert snap["B"] == "missing"
    assert snap["C"] == "set"
    assert snap["D"] == "missing"


def test_thread_safety(monkeypatch):
    sm = core_secrets.SecretsManager()
    monkeypatch.setenv("TS", "start")
    results = []

    def worker(i):
        for _ in range(10):
            sm.set_secret("TS", f"{i}")
            results.append(sm.get_secret("TS"))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # All values set and get should be string digits
    assert all(r in {"0", "1", "2"} for r in results)


def test_logger_is_used(monkeypatch, tmp_path, caplog):
    sm = core_secrets.SecretsManager(env_file=str(tmp_path / ".env"))
    sm._logger.info("test log")
    assert "test log" in caplog.text


def test_null_handler_attached(monkeypatch):
    sm = core_secrets.SecretsManager()
    assert any(isinstance(h, logging.NullHandler) for h in sm._logger.handlers)
