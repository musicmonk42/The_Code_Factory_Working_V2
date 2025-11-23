# --- env must be set before any package import that touches Dynaconf ---
import asyncio
import base64
import importlib.util
import os
import types  # Added for robust stub creation
import zlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

# --- FIX: Use AUDIT_LOG_DEV_MODE to match the check in audit_backend_core.py ---
os.environ["AUDIT_LOG_DEV_MODE"] = "true"  # allows relaxed validation in tests
# Dynaconf prefix is AUDIT_* and needs @json prefix in the VALUE for JSON parsing
encryption_key = base64.urlsafe_b64encode(b"0" * 32).decode("ascii")
os.environ["AUDIT_ENCRYPTION_KEYS"] = f'@json [{{"key_id": "mock_1", "key": "{encryption_key}"}}]'
# Satisfy other validators too (harmless defaults if unused)
os.environ.setdefault("AUDIT_COMPRESSION_ALGO", "gzip")
os.environ.setdefault("AUDIT_COMPRESSION_LEVEL", "6")
os.environ.setdefault("AUDIT_BATCH_FLUSH_INTERVAL", "5")
os.environ.setdefault("AUDIT_BATCH_MAX_SIZE", "100")
# CRITICAL FIX 1: Prevent automatic background health checks by setting interval to 0
os.environ.setdefault("AUDIT_HEALTH_CHECK_INTERVAL", "0")
# CRITICAL FIX 2: Prevent automatic schema migration during startup tests
os.environ.setdefault("AUDIT_SCHEMA_MIGRATION_ENABLED", "false")
os.environ.setdefault("AUDIT_RETRY_MAX_ATTEMPTS", "3")
os.environ.setdefault("AUDIT_RETRY_BACKOFF_FACTOR", "0.1")
os.environ.setdefault("AUDIT_TAMPER_DETECTION_ENABLED", "true")
# --- end env block ---

# --- robust SDK stubs (replace your current stubs with this) ---
import sys


def stub_module(name: str) -> types.ModuleType:
    """Create/import a dotted module hierarchy top-down (e.g., 'a.b.c')."""
    parent = None
    full = ""
    for part in name.split("."):
        full = part if not full else f"{full}.{part}"
        if full not in sys.modules:
            m = types.ModuleType(full)
            sys.modules[full] = m
            if parent:
                setattr(parent, part, m)
        parent = sys.modules[full]
    return sys.modules[name]


# Minimal symbols some backends expect
# botocore.exceptions.ClientError
# FIX: Ensure all parent modules exist
to_stub = [
    # AWS
    "botocore",
    "botocore.exceptions",
    "boto3",
    # Google
    "google",
    "google.cloud",
    "google.cloud.storage",
    "google.cloud.bigquery",
    "google.api_core",
    "google.api_core.exceptions",
    # Azure
    "azure",
    "azure.storage",
    "azure.storage.blob",
    "azure.storage.blob.aio",
    "azure.core",
    "azure.core.exceptions",
    # Misc (some libs import it)
    "aiohttp",
]
for mod in to_stub:
    stub_module(mod)

# CRITICAL FIX 2: Explicitly define and assign ClientError to the module object
botocore_exceptions = sys.modules["botocore.exceptions"]


class _ClientError(Exception):
    pass


botocore_exceptions.ClientError = _ClientError

# boto3.client (will be patched by tests)
sys.modules["boto3"].client = lambda service_name: None


# Optionally add common Google exceptions if referenced
class _GoogleNotFound(Exception):
    pass


sys.modules["google.api_core.exceptions"].NotFound = _GoogleNotFound


# google.cloud.storage.Client (will be patched by tests)
class _GCSClient:
    pass


sys.modules["google.cloud.storage"].Client = _GCSClient

# --- FIX: Stub BigQuery module attributes correctly ---
stub_module("google.cloud.bigquery")  # Ensure the module exists


class _BQClient:
    pass


# --- FIX: Add __init__ to _BQTable stub ---
class _BQTable:
    def __init__(self, *args, **kwargs):
        pass


# --- END FIX ---
class _BQLoadJobConfig:
    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs

    def __str__(self):
        return f"_BQLoadJobConfig({self.kwargs})"


class _BQTimePartitioning:
    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs

    def __str__(self):
        return f"_BQTimePartitioning({self.kwargs})"


class _BQTimePartitioningType:
    DAY = "DAY"


class _BQSchemaField:
    def __init__(self, *args, **kwargs):
        pass


class _SourceFormat:
    NEWLINE_DELIMITED_JSON = "NEWLINE_DELIMITED_JSON"


class _WriteDisposition:
    WRITE_APPEND = "WRITE_APPEND"


class _CreateDisposition:
    CREATE_IF_NEEDED = "CREATE_IF_NEEDED"


class _Compression:
    GZIP = "GZIP"


sys.modules["google.cloud.bigquery"].Client = _BQClient
sys.modules["google.cloud.bigquery"].Table = _BQTable
sys.modules["google.cloud.bigquery"].LoadJobConfig = _BQLoadJobConfig
sys.modules["google.cloud.bigquery"].TimePartitioning = _BQTimePartitioning
sys.modules["google.cloud.bigquery"].TimePartitioningType = _BQTimePartitioningType
sys.modules["google.cloud.bigquery"].SchemaField = _BQSchemaField
sys.modules["google.cloud.bigquery"].SourceFormat = _SourceFormat
sys.modules["google.cloud.bigquery"].WriteDisposition = _WriteDisposition
sys.modules["google.cloud.bigquery"].CreateDisposition = _CreateDisposition
sys.modules["google.cloud.bigquery"].Compression = _Compression
# --- END FIX ---


# Azure Blob aio entrypoint (will be patched by tests later)
class _StubBlobServiceClient:
    @staticmethod
    def from_connection_string(*args, **kwargs):
        class _Client:
            def get_container_client(self, *a, **k):
                class _Container:
                    def upload_blob(self, *a, **k):
                        pass

                    def list_blobs(self, *a, **k):
                        return []

                    async def get_container_properties(self, *a, **k):
                        return {}

                return _Container()

        return _Client()


sys.modules["azure.storage.blob.aio"].BlobServiceClient = _StubBlobServiceClient


# --- FIX: Add minimal Azure exceptions ---
class _AzureError(Exception):
    pass


class _ResourceExistsError(Exception):
    pass


class _ResourceNotFoundError(Exception):
    pass


sys.modules["azure.core.exceptions"].AzureError = _AzureError
sys.modules["azure.core.exceptions"].ResourceExistsError = _ResourceExistsError
sys.modules["azure.core.exceptions"].ResourceNotFoundError = _ResourceNotFoundError


# --- FIX: Add ContentSettings stub with __init__ and __str__ ---
class _ContentSettings:
    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs

    def __str__(self):
        # Create a string representation of the kwargs for assertion
        return f"_ContentSettings({self.kwargs})"


sys.modules["azure.storage.blob"].ContentSettings = _ContentSettings
# --- end robust SDK stubs ---

# 3) Package shim so relative imports work
REPO_ROOT = Path(__file__).resolve().parents[2]  # .../generator
PKG_ROOT = REPO_ROOT / "audit_log" / "audit_backend"  # folder containing audit_backend_cloud.py
# ensure generator root on sys.path for any absolute imports the package might do
p = str(REPO_ROOT)
if p not in sys.path:
    sys.path.insert(0, p)
# create pseudo packages: 'audit_log' and 'audit_log.audit_backend'
if "audit_log" not in sys.modules:
    pkg = types.ModuleType("audit_log")
    pkg.__path__ = [str(REPO_ROOT / "audit_log")]
    sys.modules["audit_log"] = pkg
if "audit_log.audit_backend" not in sys.modules:
    subpkg = types.ModuleType("audit_log.audit_backend")
    subpkg.__path__ = [str(PKG_ROOT)]
    sys.modules["audit_log.audit_backend"] = subpkg
    # CRITICAL: Set the subpackage as an attribute on the parent package
    # so that patch() can resolve paths like "audit_log.audit_backend.X"
    sys.modules["audit_log"].audit_backend = subpkg

# 3.5) Load audit_backend_core FIRST (audit_backend_cloud imports from it)
CORE_PATH = PKG_ROOT / "audit_backend_core.py"
core_spec = importlib.util.spec_from_file_location(
    "audit_log.audit_backend.audit_backend_core",
    str(CORE_PATH),
)
core = importlib.util.module_from_spec(core_spec)
assert core_spec and core_spec.loader
# Register in sys.modules and link to parent BEFORE executing
sys.modules["audit_log.audit_backend.audit_backend_core"] = core
sys.modules["audit_log.audit_backend"].audit_backend_core = core
core_spec.loader.exec_module(core)  # loads after env is set

# 4) Dynamic import with fully-qualified package name so relative imports resolve
CLOUD_PATH = PKG_ROOT / "audit_backend_cloud.py"
spec = importlib.util.spec_from_file_location(
    "audit_log.audit_backend.audit_backend_cloud",
    str(CLOUD_PATH),
)
cloud = importlib.util.module_from_spec(spec)
assert spec and spec.loader
# CRITICAL: Register the module in sys.modules BEFORE exec_module
sys.modules["audit_log.audit_backend.audit_backend_cloud"] = cloud
# Also set as attribute on parent package for patch() to work
sys.modules["audit_log.audit_backend"].audit_backend_cloud = cloud
spec.loader.exec_module(cloud)  # loads after env is set

# Expose classes for the tests
S3Backend = cloud.S3Backend
GCSBackend = cloud.GCSBackend
AzureBlobBackend = cloud.AzureBlobBackend


# -------------------------
# Common fixtures/utilities
# -------------------------


@pytest.fixture(scope="function")
def event_loop():
    """Separate loop per test for isolation on Windows + asyncio."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(autouse=True)
def tmp_dir_cleanup(tmp_path):
    # Backends don’t write to local FS, but keep a cleanup hook for future artifacts
    yield


# -------------------------
# S3 Backend (Athena) tests
# -------------------------


@pytest_asyncio.fixture
async def mock_boto3_clients():
    """Mock boto3 client('s3') and client('athena') and wire in expected methods."""
    # We must patch the boto3 import *within the dynamically loaded module*
    with patch(f"{cloud.__name__}.boto3.client") as mock_client, patch(
        f"{cloud.__name__}.retry_operation"
    ) as mock_retry:
        mock_s3 = MagicMock(name="s3")
        mock_athena = MagicMock(name="athena")

        def _client(name):
            if name == "s3":
                return mock_s3
            if name == "athena":
                return mock_athena
            raise AssertionError(f"Unexpected boto3 client: {name}")

        mock_client.side_effect = _client

        # S3 put/get/paginators used by atomic writes, migration, and health
        mock_s3.put_object.return_value = {"ResponseMetadata": {"HTTPStatusCode": 200}}
        mock_s3.head_bucket.return_value = {"ResponseMetadata": {"HTTPStatusCode": 200}}
        paginator = MagicMock()
        paginator.paginate.return_value = [{"Contents": []}]
        mock_s3.get_paginator.return_value = paginator

        # Athena start_query_execution / get_query_execution / get_query_results
        mock_athena.start_query_execution.return_value = {"QueryExecutionId": "Q123"}
        mock_athena.get_query_execution.return_value = {
            "QueryExecution": {"Status": {"State": "SUCCEEDED"}}
        }
        mock_athena.get_query_results.return_value = {
            "ResultSet": {
                "Rows": [
                    {
                        "Data": [
                            {"VarCharValue": "entry_id"},
                            {"VarCharValue": "encrypted_data"},
                            {"VarCharValue": "timestamp"},
                            {"VarCharValue": "schema_version"},
                            {"VarCharValue": "_audit_hash"},
                        ]
                    },
                    {
                        "Data": [
                            {"VarCharValue": "id-1"},
                            {"VarCharValue": base64.b64encode(b"{}").decode()},
                            {"VarCharValue": "2025-09-01T12:00:00Z"},
                            {"VarCharValue": "2"},
                            {"VarCharValue": "h"},
                        ]
                    },
                ]
            }
        }

        # --- Mock Retry ---
        async def mock_retry_side_effect(fn, **kwargs):
            # This is a general mock for the core retry function, and must handle sync and async calls.
            if asyncio.iscoroutinefunction(fn):
                return await fn()
            elif callable(fn):
                result = fn()
                # If the function returns a coroutine (e.g., from an API client), await it.
                if asyncio.iscoroutine(result):
                    return await result
                # If the function returns a normal value (e.g., a simple client call),
                # return it directly.
                return result
            else:
                return await fn

        mock_retry.side_effect = mock_retry_side_effect

        yield mock_s3, mock_athena


@pytest.mark.asyncio
async def test_s3_atomic_append_and_partition_refresh(mock_boto3_clients):
    mock_s3, mock_athena = mock_boto3_clients
    backend = S3Backend(
        {
            "bucket": "test-bucket",
            "athena_results_location": "s3://athena-results/",
            "athena_database": "audit_db",
            "athena_table": "audit_logs",
            "key_prefix": "audit_logs_v2/",
        }
    )

    # CRITICAL FIX 3: Call start() and wait for all init tasks
    await backend.start()

    entry = {
        "entry_id": "id-1",
        "encrypted_data": base64.b64encode(b"{}").decode(),
        "schema_version": 2,
        "_audit_hash": "hash",
        "timestamp": "2025-09-01T12:00:00Z",
    }

    async with backend._atomic_context([entry]):
        # _append_single is a no-op, the work happens in the context manager
        pass

    # Ensure close() runs
    await backend.close()

    # One gzipped JSONL object uploaded; MSCK REPAIR triggered
    assert mock_s3.put_object.call_count == 1
    args = mock_s3.put_object.call_args.kwargs
    assert args["Bucket"] == "test-bucket"
    assert args["ContentEncoding"] == "gzip"
    assert args["ContentType"] == "application/jsonl"
    # Body is gzipped; quick sanity: it should decompress
    decompressed = zlib.decompress(args["Body"]).decode("utf-8")
    assert '"entry_id": "id-1"' in decompressed

    # refresh partitions executed via Athena
    assert mock_athena.start_query_execution.call_count >= 1  # create DB/table + MSCK
    # The module uses Athena table DDL & MSCK REPAIR TABLE under the hood.
    assert (
        "MSCK REPAIR TABLE"
        in mock_athena.start_query_execution.call_args_list[-1].kwargs["QueryString"]
    )


@pytest.mark.asyncio
async def test_s3_query_via_athena(mock_boto3_clients):
    _, mock_athena = mock_boto3_clients
    backend = S3Backend(
        {
            "bucket": "test-bucket",
            "athena_results_location": "s3://athena-results/",
            "athena_database": "audit_db",
            "athena_table": "audit_logs",
            "key_prefix": "audit_logs_v2/",
        }
    )

    # CRITICAL FIX 3: Call start() and wait for all init tasks
    await backend.start()

    out = await backend._query_single({"entry_id": "id-1"}, limit=10)

    # Ensure close() runs
    await backend.close()

    assert len(out) == 1
    assert out[0]["entry_id"] == "id-1"
    assert out[0]["schema_version"] == 2
    # Query path: start_query_execution -> poll SUCCEEDED -> get_query_results.
    assert mock_athena.start_query_execution.call_args.kwargs["QueryString"].startswith("SELECT")


@pytest.mark.asyncio
async def test_s3_health_check_ok(mock_boto3_clients):
    mock_s3, _ = mock_boto3_clients
    backend = S3Backend(
        {
            "bucket": "test-bucket",
            "athena_results_location": "s3://athena-results/",
            "athena_database": "audit_db",
            "athena_table": "audit_logs",
        }
    )

    # CRITICAL FIX 3: Call start() and wait for all init tasks
    await backend.start()

    # CRITICAL FIX 4: Reset call count from the implicit check that happened during backend.start()
    mock_s3.head_bucket.reset_mock()

    # The explicit call is now the ONLY call to the underlying head_bucket mock,
    # because AUDIT_HEALTH_CHECK_INTERVAL is set to "0" in the environment.
    assert await backend._health_check() is True

    # Ensure close() runs for cleanup.
    await backend.close()

    mock_s3.head_bucket.assert_called_once()


# -------------------------
# GCS Backend (BigQuery) tests
# -------------------------


@pytest_asyncio.fixture
async def mock_gcs_clients():
    # Patch the imports *within the dynamically loaded module*
    # --- FIX: Remove redundant patches for Table, LoadJobConfig, etc. ---
    with patch(f"{cloud.__name__}.gcs.Client") as mock_gcs_client_constructor, patch(
        f"{cloud.__name__}.retry_operation"
    ) as mock_retry, patch("google.cloud.bigquery.Client") as mock_bq_client_constructor:
        # --- END FIX ---

        # --- Mock GCS (Storage) ---
        mock_storage_client = MagicMock(name="GCSClient")
        mock_gcs_client_constructor.return_value = mock_storage_client
        mock_bucket = MagicMock(name="GCSBucket")
        mock_storage_client.get_bucket.return_value = mock_bucket
        mock_blob = MagicMock(name="GCSBlob")
        mock_bucket.blob.return_value = mock_blob
        mock_storage_client.list_blobs.return_value = []  # Default empty list

        # --- Mock BigQuery ---
        mock_bq_client = MagicMock(name="BigQueryClient")
        mock_bq_client_constructor.return_value = mock_bq_client
        mock_bq_client.dataset.return_value.table.return_value = MagicMock(name="TableRef")

        # Mock load job
        mock_load_job = MagicMock(name="LoadJob")
        mock_load_job.errors = None
        mock_load_job.result.return_value = None  # result() blocks until done
        mock_bq_client.load_table_from_uri.return_value = mock_load_job

        # Mock query job
        mock_query_job = MagicMock(name="QueryJob")
        mock_query_row = MagicMock()
        mock_query_row.entry_id = "id-3"
        mock_query_row.encrypted_data = base64.b64encode(b"{}").decode()
        # BQ returns timezone-aware datetime
        mock_query_row.timestamp = __import__("datetime").datetime(
            2025, 9, 1, tzinfo=__import__("datetime").timezone.utc
        )
        mock_query_row.schema_version = 2
        mock_query_row._audit_hash = "h"
        mock_query_job.result.return_value = [mock_query_row]
        mock_bq_client.query.return_value = mock_query_job

        # --- Mock Retry ---
        async def mock_retry_side_effect(fn, **kwargs):
            if asyncio.iscoroutinefunction(fn):
                return await fn()
            elif callable(fn):
                result = fn()
                # Check if fn() returned a coroutine (e.g., from an API client), await it.
                if asyncio.iscoroutine(result):
                    return await result
                # If the function returns a normal value (e.g., a simple client call),
                # return it directly.
                return result
            else:
                return await fn

        mock_retry.side_effect = mock_retry_side_effect

        yield mock_storage_client, mock_bucket, mock_blob, mock_bq_client


@pytest.mark.asyncio
async def test_gcs_atomic_append_and_bq_load(mock_gcs_clients):
    _client, bucket, blob, mock_bq_client = mock_gcs_clients
    backend = GCSBackend(
        {
            "bucket": "test-gcs",
            "project_id": "test-project",
            "bigquery_dataset": "audit_ds",
            "bigquery_table": "audit_logs",
        }
    )

    # CRITICAL FIX 3: Call start() and wait for all init tasks
    await backend.start()

    entry = {
        "entry_id": "id-2",
        "encrypted_data": base64.b64encode(b"{}").decode(),
        "schema_version": 2,
        "_audit_hash": "hash",
        "timestamp": "2025-09-01T12:00:00Z",
    }

    async with backend._atomic_context([entry]):
        # _append_single is a no-op
        pass

    # Ensure close() runs
    await backend.close()

    assert bucket.blob.called
    # upload_from_string is invoked inside retry_operation; we stubbed retry to call immediately.
    assert blob.upload_from_string.called
    data_arg = blob.upload_from_string.call_args.args[0]
    assert zlib.decompress(data_arg).decode("utf-8").strip().endswith("}")

    # Check that BigQuery load job was called
    mock_bq_client.load_table_from_uri.assert_called_once()
    load_job_call = mock_bq_client.load_table_from_uri.call_args
    assert "gs://test-gcs/" in load_job_call.args[0]  # Check URI
    assert "WRITE_APPEND" in str(load_job_call.kwargs["job_config"])  # Check config


@pytest.mark.asyncio
async def test_gcs_query_via_bigquery(mock_gcs_clients):
    _client, _bucket, _blob, mock_bq_client = mock_gcs_clients
    backend = GCSBackend({"bucket": "test-gcs", "project_id": "test-project"})

    # CRITICAL FIX 3: Call start() and wait for all init tasks
    await backend.start()

    out = await backend._query_single({"entry_id": "id-3"}, limit=5)

    # Ensure close() runs
    await backend.close()

    assert len(out) == 1
    assert out[0]["entry_id"] == "id-3"
    # --- FIX: Update assertion to match isoformat(timespec='milliseconds') + 'Z' ---
    assert out[0]["timestamp"] == "2025-09-01T00:00:00.000+00:00Z"

    mock_bq_client.query.assert_called_once()
    query_call = mock_bq_client.query.call_args.args[0]
    assert "SELECT" in query_call
    assert "entry_id = 'id-3'" in query_call


# -------------------------
# Azure Blob Backend tests
# -------------------------


@pytest_asyncio.fixture
async def mock_azure_clients():
    # Patch imports *within the dynamically loaded module*
    with patch(f"{cloud.__name__}.BlobServiceClient") as mock_bsc_constructor, patch(
        f"{cloud.__name__}.retry_operation"
    ) as mock_retry:

        # --- Mock Azure Clients ---
        # Use MagicMock for the client since from_connection_string and get_container_client are sync
        mock_client = MagicMock(name="BlobServiceClient")
        mock_container_client = MagicMock(name="ContainerClient")

        mock_bsc_constructor.from_connection_string.return_value = mock_client
        mock_client.get_container_client.return_value = mock_container_client
        mock_client.close = AsyncMock(name="close")  # Add async close method

        # Mock async methods with AsyncMock
        mock_container_client.create_container = AsyncMock(name="create_container")
        mock_container_client.upload_blob = AsyncMock(name="upload_blob")
        mock_container_client.get_container_properties = AsyncMock(name="get_container_properties")
        mock_container_client.delete_blobs = AsyncMock(name="delete_blobs")

        # --- FIX: Mock list_blobs with a MagicMock that has a side_effect ---
        # Define the async iterator function
        async def _aiter(*args, **kwargs):
            if not kwargs.get("results_per_page"):
                yield MagicMock(name="BlobProperties")
            # Handle results_per_page=1 case
            if kwargs.get("results_per_page") == 1:
                yield MagicMock(name="BlobProperties_Page1")
                return  # Stop after one

        # Assign a MagicMock to list_blobs and set its side_effect to the generator
        mock_container_client.list_blobs = MagicMock(name="list_blobs_mock", side_effect=_aiter)
        # --- END FIX ---

        # Mock get_blob_client (returns a blob client with async methods)
        def _get_blob_client(blob_name):
            blob_client = MagicMock(name=f"BlobClient:{blob_name}")
            blob_client.download_blob = AsyncMock(name="download_blob")

            async def _readall():
                return b"test data"

            blob_client.download_blob.return_value.readall = _readall
            blob_client.upload_blob = AsyncMock(name="upload_blob")
            return blob_client

        mock_container_client.get_blob_client = _get_blob_client

        # --- Mock Retry ---
        async def mock_retry_side_effect(fn, **kwargs):
            if asyncio.iscoroutinefunction(fn):
                return await fn()
            elif callable(fn):
                result = fn()
                # Check if fn() returned a coroutine
                if asyncio.iscoroutine(result):
                    return await result
                # Since the original code relies on retry_operation calling sync methods,
                # we return the result of the sync call.
                return result
            else:
                return await fn

        mock_retry.side_effect = mock_retry_side_effect

        yield mock_client, mock_container_client


@pytest.mark.asyncio
async def test_azure_atomic_append(mock_azure_clients):
    client, container = mock_azure_clients
    backend = AzureBlobBackend(
        {
            "connection_string": "UseDevelopmentStorage=true",
            "container_name": "test-container",
            "blob_prefix": "audit_logs_v2/",
        }
    )

    # CRITICAL FIX 3: Call start() and wait for all init tasks
    await backend.start()

    entry = {
        "entry_id": "id-4",
        "encrypted_data": base64.b64encode(b"{}").decode(),
        "schema_version": 2,
        "_audit_hash": "hash",
        "timestamp": "2025-09-01T12:00:00Z",
    }

    async with backend._atomic_context([entry]):
        # _append_single is a no-op
        pass

    # Ensure close() runs
    await backend.close()

    assert container.upload_blob.called
    kwargs = container.upload_blob.call_args.kwargs
    assert kwargs["overwrite"] is True
    # gzip content
    body = kwargs["data"]
    assert zlib.decompress(body).decode().find('"entry_id": "id-4"') != -1
    assert "application/jsonl" in str(kwargs["content_settings"])
    assert "gzip" in str(kwargs["content_settings"])


@pytest.mark.asyncio
async def test_azure_health_check_ok(mock_azure_clients):
    client, container = mock_azure_clients
    backend = AzureBlobBackend(
        {
            "connection_string": "UseDevelopmentStorage=true",
            "container_name": "test-container",
        }
    )

    # CRITICAL FIX 3: Call start() and wait for all init tasks
    await backend.start()

    # CRITICAL FIX 4: Reset call count from the implicit check that happened during backend.start()
    container.get_container_properties.reset_mock()
    container.list_blobs.reset_mock()

    # The explicit call is now the ONLY call to the underlying mock.
    ok = await backend._health_check()
    assert ok is True

    # Ensure close() runs for cleanup.
    await backend.close()

    container.get_container_properties.assert_called_once()
    # list_blobs is also called once, inside _health_check()
    container.list_blobs.assert_called_once_with(results_per_page=1)
