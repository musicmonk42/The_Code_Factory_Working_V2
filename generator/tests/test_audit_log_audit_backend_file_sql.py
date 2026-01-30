"""
test_audit_backend_file_sql.py

Modern test suite for audit_backend_file_sql.py, compatible with the
new core architecture (encryption, batching, async-native).
"""

import base64
import datetime
import json

# --- env must be set before any package import that touches Dynaconf ---
import os
import zlib
from typing import Dict

os.environ["AUDIT_LOG_DEV_MODE"] = "true"
encryption_key = base64.urlsafe_b64encode(b"0" * 32).decode("ascii")
os.environ["AUDIT_ENCRYPTION_KEYS"] = (
    f'@json [{{"key_id": "mock_1", "key": "{encryption_key}"}}]'
)
os.environ.setdefault("AUDIT_COMPRESSION_ALGO", "gzip")
os.environ.setdefault("AUDIT_COMPRESSION_LEVEL", "6")
os.environ.setdefault("AUDIT_BATCH_FLUSH_INTERVAL", "5")
os.environ.setdefault("AUDIT_BATCH_MAX_SIZE", "100")
os.environ.setdefault("AUDIT_HEALTH_CHECK_INTERVAL", "60")
os.environ.setdefault("AUDIT_RETRY_MAX_ATTEMPTS", "3")
os.environ.setdefault("AUDIT_RETRY_BACKOFF_FACTOR", "0.1")
os.environ.setdefault("AUDIT_TAMPER_DETECTION_ENABLED", "true")
# --- end env block ---

import asyncio
import sqlite3
import sys
import types
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiofiles
import pytest
import pytest_asyncio
from prometheus_client import REGISTRY

# --- Package Shim ---
REPO_ROOT = Path(__file__).resolve().parents[2]  # .../The_Code_Factory_Working_V2
PKG_ROOT = REPO_ROOT / "generator" / "audit_log" / "audit_backend"
p = str(REPO_ROOT / "generator")
if p not in sys.path:
    sys.path.insert(0, p)
if "audit_log" not in sys.modules:
    pkg = types.ModuleType("audit_log")
    pkg.__path__ = [str(REPO_ROOT / "generator" / "audit_log")]
    pkg.__spec__ = None
    pkg.__file__ = "<mocked>"
    sys.modules["audit_log"] = pkg
if "audit_log.audit_backend" not in sys.modules:
    subpkg = types.ModuleType("audit_log.audit_backend")
    subpkg.__path__ = [str(PKG_ROOT)]
    subpkg.__spec__ = None
    subpkg.__file__ = "<mocked>"
    sys.modules["audit_log.audit_backend"] = subpkg
    sys.modules["audit_log"].audit_backend = subpkg

# Load core first
import importlib.util

CORE_PATH = PKG_ROOT / "audit_backend_core.py"
core_spec = importlib.util.spec_from_file_location(
    "audit_log.audit_backend.audit_backend_core", str(CORE_PATH)
)
core = importlib.util.module_from_spec(core_spec)
sys.modules["audit_log.audit_backend.audit_backend_core"] = core
sys.modules["audit_log.audit_backend"].audit_backend_core = core
core_spec.loader.exec_module(core)

# Load file/sql module
FILE_SQL_PATH = PKG_ROOT / "audit_backend_file_sql.py"
spec = importlib.util.spec_from_file_location(
    "audit_log.audit_backend.audit_backend_file_sql", str(FILE_SQL_PATH)
)
file_sql = importlib.util.module_from_spec(spec)
sys.modules["audit_log.audit_backend.audit_backend_file_sql"] = file_sql
sys.modules["audit_log.audit_backend"].audit_backend_file_sql = file_sql
spec.loader.exec_module(file_sql)

# Expose classes for tests
FileBackend = file_sql.FileBackend
SQLiteBackend = file_sql.SQLiteBackend
SCHEMA_VERSION = core.SCHEMA_VERSION
ENCRYPTER = core.ENCRYPTER
COMPRESSION_ALGO = core.COMPRESSION_ALGO
COMPRESSION_LEVEL = core.COMPRESSION_LEVEL
compute_hash = core.compute_hash
# --- End Shim ---


# --- Test Helper Functions ---


def _prepare_v1_entry(entry_data: Dict) -> str:
    """Creates a V1-style (schema_version=1) prepared entry string."""
    entry_data["schema_version"] = 1
    if "entry_id" not in entry_data:
        entry_data["entry_id"] = str(uuid.uuid4())
    if "timestamp" not in entry_data:
        # --- FIX: Ensure timestamp matches core logic (milliseconds + Z) ---
        entry_data["timestamp"] = (
            datetime.datetime.now(datetime.timezone.utc).isoformat(
                timespec="milliseconds"
            )
            + "Z"
        )

    # --- FIX: Compute hash on a copy *before* adding the hash itself ---
    # The hash is computed on the data *without* the hash in it.
    temp_hash_data = entry_data.copy()
    hash_str = json.dumps(temp_hash_data, sort_keys=True).encode("utf-8")
    computed_hash = compute_hash(hash_str)

    # Now add the hash to the data to be encrypted
    entry_data["_audit_hash"] = computed_hash
    # --- END FIX ---

    # Encrypt/Compress the payload (which now includes the hash)
    data_str = json.dumps(entry_data, sort_keys=True)
    if COMPRESSION_ALGO == "gzip":
        compressed = zlib.compress(data_str.encode("utf-8"), level=COMPRESSION_LEVEL)
    else:
        compressed = data_str.encode("utf-8")

    encrypted = ENCRYPTER.encrypt(compressed)
    base64_data = base64.b64encode(encrypted).decode("utf-8")

    # This is the format the backend stores
    stored_entry = {
        "encrypted_data": base64_data,
        "entry_id": entry_data["entry_id"],
        "schema_version": 1,
        "timestamp": entry_data["timestamp"],
        "_audit_hash": computed_hash,  # Store the correctly computed hash
    }
    return json.dumps(stored_entry)


# --- Mocks and Fixtures ---


@pytest.fixture(scope="function")
def event_loop():
    """Separate loop per test."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(autouse=True)
async def mock_alerts_and_otel():
    """Mock alerts and tracing for all tests."""
    with (
        patch(
            "audit_log.audit_backend.audit_backend_core.send_alert",
            new_callable=AsyncMock,
        ) as mock_alert,
        patch("audit_log.audit_backend.audit_backend_core.tracer") as mock_tracer,
    ):

        mock_span = MagicMock()
        mock_tracer.start_as_current_span.return_value.__enter__.return_value = (
            mock_span
        )
        yield mock_alert, mock_tracer, mock_span


@pytest_asyncio.fixture
async def file_backend(tmp_path):
    """Create a FileBackend instance in a temp directory."""
    log_file = tmp_path / "audit.log"
    backend = FileBackend({"log_file": str(log_file)})

    # --- FIX: Call start() to run migration and init tasks ---
    await backend.start()

    yield backend

    # --- FIX: Iterate over a list copy and remove .close() call ---
    # Manually cancel tasks to avoid resource warnings
    for task in list(backend._async_tasks):  # Iterate over a copy
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest_asyncio.fixture
async def sqlite_backend(tmp_path):
    """Create a SQLiteBackend instance in a temp directory."""
    db_file = tmp_path / "audit.db"
    backend = SQLiteBackend({"db_file": str(db_file)})

    # --- FIX: Call start() to run init and migration tasks ---
    await backend.start()

    yield backend

    await backend.close()


# --- Test Suite ---


@pytest.mark.asyncio
async def test_file_backend_append_and_flush(file_backend, mock_alerts_and_otel):
    """Test FileBackend append, flush (atomic write), and WAL cleanup."""
    mock_alert, mock_tracer, mock_span = mock_alerts_and_otel

    # --- FIX: Don't pass entry_id, let append() create it ---
    entry = {"action": "login", "user": "test"}

    # 1. Append (adds to batch)
    await file_backend.append(entry)

    # 2. Flush (triggers WAL write + atomic write)
    await file_backend.flush_batch()

    # Check WAL file is gone (written and then deleted)
    assert not os.path.exists(file_backend.wal_file)

    # Check main log file
    assert os.path.exists(file_backend.log_file)

    # --- FIX: Query the backend, don't read the raw file ---
    results = await file_backend.query({}, limit=1)
    assert len(results) == 1
    assert results[0]["action"] == "login"
    # --- END FIX ---

    # Check metrics and traces
    mock_span.set_attribute.assert_any_call("batch.size", 1)
    assert (
        REGISTRY.get_sample_value(
            "audit_backend_writes_total", {"backend": "FileBackend"}
        )
        == 1
    )


@pytest.mark.asyncio
async def test_sqlite_backend_append_and_flush(sqlite_backend, mock_alerts_and_otel):
    """Test SQLiteBackend append and flush (transaction commit)."""
    # --- FIX: Don't pass entry_id ---
    entry = {"action": "create_user", "user": "test"}

    await sqlite_backend.append(entry)
    await sqlite_backend.flush_batch()

    # --- FIX: Query for the content, not the ephemeral entry_id ---
    conn = sqlite3.connect(sqlite_backend.db_file)
    cursor = conn.cursor()
    cursor.execute(f"SELECT data FROM logs_v{SCHEMA_VERSION} LIMIT 1")
    result = cursor.fetchone()
    conn.close()

    assert result is not None

    # Decrypt to verify
    decrypted = ENCRYPTER.decrypt(base64.b64decode(result[0]))
    decompressed = zlib.decompress(decrypted).decode("utf-8")
    final_entry = json.loads(decompressed)

    assert final_entry["action"] == "create_user"
    assert (
        REGISTRY.get_sample_value(
            "audit_backend_writes_total", {"backend": "SQLiteBackend"}
        )
        == 1
    )


@pytest.mark.asyncio
async def test_file_backend_query_and_tamper(file_backend, mock_alerts_and_otel):
    """Tests FileBackend query and tamper detection."""
    mock_alert, _, _ = mock_alerts_and_otel

    # --- FIX: Let append create the ID ---
    entry = {"action": "query_test", "user": "test"}

    await file_backend.append(entry)
    await file_backend.flush_batch()

    # 1. Test successful query
    # --- FIX: Query by content ---
    results = await file_backend.query({}, limit=1)
    assert len(results) == 1
    assert results[0]["action"] == "query_test"
    entry_id = results[0]["entry_id"]  # Get the real ID

    # 2. Manually tamper with the log file
    async with aiofiles.open(file_backend.log_file, "r") as f:
        log_content = await f.read()

    stored_entry = json.loads(log_content)
    stored_entry["encrypted_data"] = "tampered_data"  # Corrupt the data

    async with aiofiles.open(file_backend.log_file, "w") as f:
        await f.write(json.dumps(stored_entry))

    # 3. Test query with tampered data (using the real ID)
    results = await file_backend.query({"entry_id": entry_id}, limit=1)
    assert len(results) == 0  # Query should fail decryption/tamper check

    # --- FIX: Check for the correct alert ---
    # The query method logs the decryption error and sends a "Failed to process" alert.
    mock_alert.assert_called_with(
        f"Failed to process log entry from FileBackend. Entry ID: {entry_id}",
        severity="medium",
    )
    # --- END FIX ---
    assert (
        REGISTRY.get_sample_value(
            "audit_backend_errors_total",
            {"backend": "FileBackend", "type": "DecodeError"},
        )
        > 0
    )


@pytest.mark.asyncio
async def test_sqlite_backend_query_and_tamper(sqlite_backend, mock_alerts_and_otel):
    """Tests SQLiteBackend query and tamper detection."""
    mock_alert, _, _ = mock_alerts_and_otel

    # --- FIX: Let append create the ID ---
    entry = {"action": "query_test_db", "user": "test"}

    await sqlite_backend.append(entry)
    await sqlite_backend.flush_batch()

    # 1. Test successful query
    # --- FIX: Query by content ---
    results = await sqlite_backend.query({}, limit=1)
    assert len(results) == 1
    assert results[0]["action"] == "query_test_db"
    entry_id = results[0]["entry_id"]  # Get the real ID

    # 2. Manually tamper with the DB
    conn = sqlite3.connect(sqlite_backend.db_file)
    conn.execute(
        f"UPDATE logs_v{SCHEMA_VERSION} SET _audit_hash = 'invalid_hash' WHERE entry_id = ?",
        (entry_id,),
    )
    conn.commit()
    conn.close()

    # 3. Test query with tampered data (using the real ID)
    # --- FIX: This will now fail thanks to the fix in audit_backend_core.py ---
    results = await sqlite_backend.query({"entry_id": entry_id}, limit=1)
    assert len(results) == 0  # Query should fail tamper check
    # --- END FIX ---

    # --- START: FIX for test_sqlite_backend_query_and_tamper ---
    # The test was expecting a "Failed to process" alert (medium),
    # but the code correctly identifies tampering and sends a "Tamper detected" alert (critical).
    mock_alert.assert_called_with(
        f"Tamper detected for entry_id {entry_id} in SQLiteBackend!",
        severity="critical",
    )
    # --- END: FIX for test_sqlite_backend_query_and_tamper ---

    assert (
        REGISTRY.get_sample_value(
            "audit_backend_tamper_detection_failures_total",
            {"backend": "SQLiteBackend"},
        )
        == 1
    )


@pytest.mark.asyncio
async def test_file_backend_wal_recovery(file_backend, mock_alerts_and_otel):
    """Test FileBackend WAL recovery logic."""
    entry_id_1 = str(uuid.uuid4())
    entry_id_2 = str(uuid.uuid4())
    entry_1_prepared = json.loads(
        _prepare_v1_entry({"action": "wal_test_1", "entry_id": entry_id_1})
    )
    entry_2_prepared = json.loads(
        _prepare_v1_entry({"action": "wal_test_2", "entry_id": entry_id_2})
    )

    # 1. Add one entry to the main log (simulating a successful flush)
    async with aiofiles.open(file_backend.log_file, "w") as f:
        await f.write(json.dumps(entry_1_prepared) + "\n")

    # 2. Add both entries to the WAL (simulating a crash during next flush)
    async with aiofiles.open(file_backend.wal_file, "w") as f:
        await f.write(json.dumps(entry_1_prepared) + "\n")  # This one is a duplicate
        await f.write(json.dumps(entry_2_prepared) + "\n")  # This one is new

    # 3. Run recovery
    await file_backend.recover_wal()

    # 4. Check main log file
    async with aiofiles.open(file_backend.log_file, "r") as f:
        lines = await f.readlines()

    assert len(lines) == 2  # Should have entry 1 and entry 2
    content = "".join(lines)
    assert entry_id_1 in content
    assert entry_id_2 in content

    # 5. Check WAL is gone
    assert not os.path.exists(file_backend.wal_file)


@pytest.mark.asyncio
async def test_file_backend_migration(tmp_path, mock_alerts_and_otel):
    """Test FileBackend schema migration."""
    log_file = tmp_path / "audit.log"
    entry_id_v1 = str(uuid.uuid4())

    # 1. Create a V1 log file manually
    v1_entry_str = _prepare_v1_entry({"action": "v1_test", "entry_id": entry_id_v1})
    async with aiofiles.open(log_file, "w") as f:
        await f.write(v1_entry_str + "\n")

    # 2. Initialize the backend. This will trigger migration.
    backend = FileBackend({"log_file": str(log_file)})
    # --- FIX: Call start() to run migration and init tasks ---
    await backend.start()

    # 3. Query for the migrated entry
    results = await backend.query({"entry_id": entry_id_v1}, limit=1)

    # 4. Validate migration
    assert len(results) == 1
    assert results[0]["action"] == "v1_test"
    assert results[0]["schema_version"] == SCHEMA_VERSION  # Should be 2
    assert (
        results[0]["_audit_hash"] != json.loads(v1_entry_str)["_audit_hash"]
    )  # Hash should be recomputed

    # --- FIX: Clean up FileBackend tasks manually ---
    for task in list(backend._async_tasks):  # Iterate over a copy
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_sqlite_backend_migration(tmp_path, mock_alerts_and_otel):
    """Test SQLiteBackend schema migration."""
    db_file = tmp_path / "audit.db"
    entry_id_v1 = str(uuid.uuid4())
    # --- FIX: Corrected typo _prepare_vv1_entry to _prepare_v1_entry ---
    v1_entry = json.loads(
        _prepare_v1_entry({"action": "v1_db_test", "entry_id": entry_id_v1})
    )

    # 1. Create a V1 database manually
    conn = sqlite3.connect(db_file)
    conn.execute("""
        CREATE TABLE logs_v1 (
            id INTEGER PRIMARY KEY,
            timestamp TEXT,
            entry_id TEXT UNIQUE,
            schema_version INTEGER,
            _audit_hash TEXT,
            data TEXT
        )
    """)
    conn.execute(
        "INSERT INTO logs_v1 (entry_id, data, timestamp, schema_version, _audit_hash) VALUES (?, ?, ?, ?, ?)",
        (
            entry_id_v1,
            v1_entry["encrypted_data"],
            v1_entry["timestamp"],
            1,
            v1_entry["_audit_hash"],
        ),
    )
    conn.commit()
    conn.close()

    # 2. Initialize the backend, triggering migration
    backend = SQLiteBackend({"db_file": str(db_file)})
    # --- FIX: Call start() to run init and migration tasks ---
    await backend.start()

    # 3. Query for the migrated entry
    results = await backend.query({"entry_id": entry_id_v1}, limit=1)

    # 4. Validate migration
    assert len(results) == 1
    assert results[0]["action"] == "v1_db_test"
    assert results[0]["schema_version"] == SCHEMA_VERSION  # Should be 2

    # 5. Check table structure
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='logs_v1'"
    )
    assert cursor.fetchone() is None  # v1 table should be gone
    cursor.execute(
        f"SELECT name FROM sqlite_master WHERE type='table' AND name='logs_v{SCHEMA_VERSION}'"
    )
    assert cursor.fetchone() is not None  # v2 table should exist
    conn.close()

    await backend.close()
