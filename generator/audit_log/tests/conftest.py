import os
from pathlib import Path


def _load_dotenv(path: Path):
    if not path.exists():
        return
    # minimal .env parser: KEY=VALUE (keeps quotes literal handling simple)
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)


def pytest_load_initial_conftests(*_args, **_kwargs):
    # If you keep a master .env elsewhere, point to it here:
    external_env = Path(r"D:\SFE\self_fixing_engineer\.env")  # <— change if needed
    _load_dotenv(external_env)

    # Hard test-safe defaults (only if not already provided)
    os.environ.setdefault("AUDIT_LOG_DEV_MODE", "true")

    # ENCRYPTION_KEYS must be a JSON list; provide a valid Fernet key
    os.environ.setdefault(
        "ENCRYPTION_KEYS",
        '[{"key_id":"mock_key_1","key":"hYnO2bq3m0yqgqz5WJt9j3ZCsb3dC-5H9qv1Hj4XGxw="}]',
    )

    os.environ.setdefault("COMPRESSION_ALGO", "gzip")
    os.environ.setdefault("COMPRESSION_LEVEL", "9")
    os.environ.setdefault("BATCH_FLUSH_INTERVAL", "10")
    os.environ.setdefault("BATCH_MAX_SIZE", "100")
    os.environ.setdefault("HEALTH_CHECK_INTERVAL", "30")
    os.environ.setdefault("RETRY_MAX_ATTEMPTS", "3")
    os.environ.setdefault("RETRY_BACKOFF_FACTOR", "0.1")
    os.environ.setdefault("TAMPER_DETECTION_ENABLED", "true")
