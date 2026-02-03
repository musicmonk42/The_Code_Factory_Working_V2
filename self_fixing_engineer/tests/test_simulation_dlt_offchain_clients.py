# tests/test_dlt_offchain_clients.py

import json
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from botocore.exceptions import ClientError as BotoClientError
from self_fixing_engineer.simulation.plugins.dlt_clients.dlt_base import (
    SECRETS_MANAGER,
    DLTClientConfigurationError,
    DLTClientError,
    DLTClientValidationError,
    _base_logger,
)
from self_fixing_engineer.simulation.plugins.dlt_clients.dlt_offchain_clients import (
    AzureBlobOffChainClient,
    GcsOffChainClient,
    InMemoryOffChainClient,
    IPFSClient,
    S3OffChainClient,
)


# Mock secrets manager to control credential loading
@pytest.fixture(autouse=True)
def mock_secrets_manager(mocker):
    mocker.patch.object(
        SECRETS_MANAGER,
        "get_secret",
        side_effect=lambda key, **kwargs: (
            json.dumps(
                {"aws_access_key_id": "mock_id", "aws_secret_access_key": "mock_key"}
            )
            if "aws_credentials" in key.lower()
            else "mock_secret"
        ),
    )


# A mock for aioboto3.Session.client
@pytest.fixture
def mock_aioboto3_client(mocker):
    mock_session = MagicMock()
    mock_client = AsyncMock()
    mock_session.client.return_value.__aenter__.return_value = mock_client
    mocker.patch("aioboto3.Session", return_value=mock_session)
    return mock_client


# Mock boto3.client for AWSSecretsBackend
@pytest.fixture
def mock_boto3_client(mocker):
    mock_client = MagicMock()
    # Mock the return value for get_secret_value, which is a dictionary with a 'SecretString' key
    mock_client.get_secret_value.return_value = {
        "SecretString": json.dumps(
            {"aws_access_key_id": "mock_id", "aws_secret_access_key": "mock_key"}
        )
    }
    mocker.patch("boto3.client", return_value=mock_client)
    return mock_client


# Mock for google.cloud.storage
@pytest.fixture
def mock_gcs_client(mocker):
    mock_client = MagicMock()
    mock_blob = MagicMock()
    mock_blob.exists = MagicMock(return_value=True)
    mock_blob.upload_from_string = MagicMock()
    mock_blob.download_as_bytes = MagicMock(return_value=b"mock_data")
    mock_client.bucket.return_value.blob.return_value = mock_blob

    # Mock service account - properly mock the from_service_account_file method
    mock_credentials = MagicMock()
    mocker.patch(
        "google.oauth2.service_account.Credentials.from_service_account_file",
        return_value=mock_credentials,
    )

    mocker.patch("google.cloud.storage.Client", return_value=mock_client)

    # Mock GCP Secret Manager
    mock_secret_manager = MagicMock()
    mock_secret_manager.SecretManagerServiceClient.return_value.access_secret_version.return_value.payload.data = (
        b'{"type": "service_account", "project_id": "mock_project"}'
    )
    mocker.patch("google.cloud.secretmanager", mock_secret_manager)

    return mock_client


# Mock for azure.storage.blob.aio.BlobServiceClient
@pytest.fixture
def mock_azure_blob_client(mocker):
    mock_blob_service = AsyncMock()
    mock_container = AsyncMock()
    mock_blob = AsyncMock()

    # Setup blob client for download
    mock_downloader = AsyncMock()
    mock_downloader.readall = AsyncMock(return_value=b"mock_data")
    mock_blob.download_blob.return_value = mock_downloader

    # Setup blob client for upload
    mock_blob.upload_blob = AsyncMock(return_value=None)

    # Make get_blob_client return the mock directly, not a coroutine
    mock_container.get_blob_client = MagicMock(return_value=mock_blob)

    # Fix: Make sure get_container_client returns the mock directly, not a coroutine
    mock_blob_service.get_container_client = MagicMock(return_value=mock_container)

    mocker.patch(
        "azure.storage.blob.aio.BlobServiceClient.from_connection_string",
        return_value=mock_blob_service,
    )

    # Mock Azure identity and keyvault
    mock_credential = AsyncMock()
    mock_secret_client = AsyncMock()
    mock_secret = AsyncMock()
    mock_secret.value = "DefaultEndpointsProtocol=https;AccountName=mockaccount;AccountKey=mockkey;EndpointSuffix=core.windows.net"
    mock_secret_client.get_secret.return_value = mock_secret

    mocker.patch(
        "azure.identity.aio.DefaultAzureCredential", return_value=mock_credential
    )
    mocker.patch(
        "azure.keyvault.secrets.aio.SecretClient", return_value=mock_secret_client
    )

    return mock_blob_service


# Mock for ipfshttpclient.Client
@pytest.fixture
def mock_ipfs_client(mocker):
    mock_client = MagicMock()
    mock_client.add_bytes = MagicMock(return_value="mock_ipfs_hash")
    mock_client.cat = MagicMock(return_value=b"mock_data")
    mock_client.id = MagicMock(return_value={})
    mocker.patch("ipfshttpclient.connect", return_value=mock_client)
    return mock_client


# Mock create_temp_file to avoid actual file creation and handle the file reading
@pytest.fixture
def mock_temp_file(mocker, tmp_path):
    # Create an actual temporary file with mock credentials
    temp_file = tmp_path / "mock_credentials.json"
    mock_creds = {
        "type": "service_account",
        "project_id": "mock_project",
        "private_key_id": "mock_key_id",
        "private_key": (
            "-----BEGIN PRIVATE KEY-----\n"
            "MIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQDzA3wB/g2S/n8l\n"
            "6gVzG4i6nswwX0PSn59/+B6e+x9YxT5y9X1Z3a5b7c9d1e2f3g4h5i6j7k8l9m0\n"
            "n1o2p3q4r5s6t7u8v9w0x1y2z3a4b5c6d7e8f9g0h1i2j3k4l5m6n7o8p9q0r\n"
            "1s2t3u4v5w6x7y8z9a0b1c2d3e4f5g6h7i8j9k0l1m2n3o4p5q6r7s8t9u0v\n"
            "1w2x3y4z5a6b7c8d9e0f1g2h3i4j5k6l7m8n9o0p1q2r3s4t5u6v7w8x9y0z\n"
            "1a2b3c4d5e6f7g8h9i0j1k2l3m4n5o6p7q8r9s0t1u2v3w4x5y6z7a8b9c0d\n"
            "1e2f3g4h5i6j7k8l9m0n1o2p3q4r5s6t7u8v9w0x1y2z3a4b5c6d7e8f9g0h\n"
            "1i2j3k4l5m6n7o8p9q0r1s2t3u4v5w6x7y8z9a0b1c2d3e4f5g6h7i8j9k0l\n"
            "1m2n3o4p5q6r7s8t9u0v1w2x3y4z5a6b7c8d9e0f1g2h3i4j5k6l7m8n9o0p\n"
            "1q2r3s4t5u6v7w8x9y0z1a2b3c4d5e6f7g8h9i0j1k2l3m4n5o6p7q8r9s0t\n"
            "1u2v3w4x5y6z7a8b9c0d+d4f5g==\n"
            "-----END PRIVATE KEY-----\n"
        ),
        "client_email": "mock@mock-project.iam.gserviceaccount.com",
        "client_id": "123456789",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        "client_x509_cert_url": "https://www.googleapis.com/robot/v1/metadata/x509/mock%40mock-project.iam.gserviceaccount.com",
    }
    temp_file.write_text(json.dumps(mock_creds))

    mocker.patch(
        "simulation.plugins.dlt_clients.dlt_offchain_clients.create_temp_file",
        return_value=str(temp_file),
    )
    return str(temp_file)


# Configuration fixtures for S3 and GCS
@pytest.fixture
def mock_s3_config():
    """Provides a valid S3 configuration for tests."""
    return {
        "s3": {
            "bucket_name": "mock_bucket",
            "log_format": "json",
            "aws_credentials_secret_id": "mock_aws_credentials_secret_id",
            "secrets_providers": ["aws"],
        }
    }


@pytest.fixture
def mock_gcs_config():
    """Provides a valid GCS configuration for tests."""
    return {
        "gcs": {
            "bucket_name": "mock_bucket",
            "log_format": "json",
            "credentials_secret_id": "mock_gcs_credentials_secret_id",
            "secrets_providers": [
                "aws"
            ],  # Use AWS secrets manager to avoid validation error
        }
    }


@pytest.fixture
def mock_azure_config():
    """Provides a valid Azure Blob configuration for tests."""
    return {
        "azure_blob": {
            "container_name": "mock_container",
            "log_format": "json",
            "connection_string_secret_id": "mock_connection_string_secret_id",
            "secrets_providers": [
                "aws"
            ],  # Use AWS secrets manager to avoid validation error
        }
    }


# Suppress non-critical logs to reduce test output noise
@pytest.fixture(autouse=True)
def suppress_logs():
    """Suppresses non-critical logs to reduce test output noise."""
    original_level = _base_logger.level
    _base_logger.setLevel(logging.CRITICAL + 1)  # Suppress all logs below CRITICAL
    yield
    _base_logger.setLevel(original_level)


@pytest.mark.asyncio
async def test_s3_health_check_success(
    mock_s3_config, mock_aioboto3_client, mock_boto3_client
):
    """Test successful health check for S3 client."""
    client = S3OffChainClient(mock_s3_config)
    await client.initialize()
    mock_aioboto3_client.list_objects_v2.return_value = {}
    result = await client.health_check()
    assert result["status"] is True
    assert mock_aioboto3_client.list_objects_v2.called


@pytest.mark.asyncio
async def test_s3_health_check_failure(
    mock_s3_config, mock_aioboto3_client, mock_boto3_client
):
    """Test failed health check for S3 client."""
    client = S3OffChainClient(mock_s3_config)
    await client.initialize()

    # Create a proper BotoClientError with the required structure
    error_response = {
        "Error": {"Code": "AccessDenied", "Message": "Access Denied"},
        "ResponseMetadata": {"HTTPStatusCode": 403},
    }

    # Create a counter to track retries
    call_count = 0

    async def side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise BotoClientError(error_response, "list_objects_v2")

    # Make list_objects_v2 raise the error every time it's called
    mock_aioboto3_client.list_objects_v2.side_effect = side_effect

    # The health_check method with @async_retry will retry and eventually raise DLTClientError
    with pytest.raises(DLTClientError) as exc_info:
        await client.health_check()

    # Verify the exception message
    assert "S3 health check failed" in str(exc_info.value)
    # Verify retries occurred (should be called multiple times due to @async_retry)
    assert call_count > 1


@pytest.mark.asyncio
async def test_s3_save_blob_success(
    mock_s3_config, mock_aioboto3_client, mock_boto3_client
):
    """Test successful blob save for S3 client."""
    client = S3OffChainClient(mock_s3_config)
    await client.initialize()
    mock_aioboto3_client.put_object.return_value = {}
    off_chain_id = await client.save_blob("test_prefix", b"test_payload")
    assert off_chain_id.startswith("dlt_payloads/test_prefix")
    assert mock_aioboto3_client.put_object.called


@pytest.mark.asyncio
async def test_s3_get_blob_success(
    mock_s3_config, mock_aioboto3_client, mock_boto3_client
):
    """Test successful blob retrieval for S3 client."""
    client = S3OffChainClient(mock_s3_config)
    await client.initialize()
    mock_response = AsyncMock()
    mock_response.read = AsyncMock(return_value=b"test_payload")
    mock_aioboto3_client.get_object.return_value = {"Body": mock_response}
    retrieved_blob = await client.get_blob("mock_key")
    assert retrieved_blob == b"test_payload"
    assert mock_aioboto3_client.get_object.called


@pytest.mark.asyncio
async def test_s3_save_blob_empty_payload(
    mock_s3_config, mock_aioboto3_client, mock_boto3_client
):
    """Test that saving an empty payload raises a validation error."""
    client = S3OffChainClient(mock_s3_config)
    await client.initialize()
    with pytest.raises(DLTClientValidationError, match="Payload blob cannot be empty"):
        await client.save_blob("test_prefix", b"")


@pytest.mark.asyncio
async def test_gcs_health_check_success(
    mock_gcs_config, mock_gcs_client, mock_boto3_client, mock_temp_file
):
    """Test successful health check for GCS client."""
    client = GcsOffChainClient(mock_gcs_config)
    await client.initialize()
    result = await client.health_check()
    assert result["status"] is True


@pytest.mark.asyncio
async def test_gcs_save_blob_success(
    mock_gcs_config, mock_gcs_client, mock_boto3_client, mock_temp_file
):
    """Test successful blob save for GCS client."""
    client = GcsOffChainClient(mock_gcs_config)
    await client.initialize()
    off_chain_id = await client.save_blob("test_prefix", b"test_payload")
    assert off_chain_id.startswith("dlt_payloads/test_prefix")


@pytest.mark.asyncio
async def test_gcs_get_blob_success(
    mock_gcs_config, mock_gcs_client, mock_boto3_client, mock_temp_file
):
    """Test successful blob retrieval for GCS client."""
    client = GcsOffChainClient(mock_gcs_config)
    await client.initialize()
    retrieved_blob = await client.get_blob("mock_key")
    assert retrieved_blob == b"mock_data"


@pytest.mark.asyncio
async def test_azure_blob_health_check_success(
    mock_azure_config, mock_azure_blob_client, mock_boto3_client
):
    """Test successful health check for Azure Blob client."""
    client = AzureBlobOffChainClient(mock_azure_config)
    await client.initialize()

    # Mock the list_blobs async iterator properly
    async def mock_list_blobs():
        yield MagicMock()  # Yield at least one mock blob

    mock_container = mock_azure_blob_client.get_container_client.return_value
    mock_container.list_blobs = MagicMock(return_value=mock_list_blobs())

    result = await client.health_check()
    assert result["status"] is True


@pytest.mark.asyncio
async def test_azure_blob_save_blob_success(
    mock_azure_config, mock_azure_blob_client, mock_boto3_client
):
    """Test successful blob save for Azure Blob client."""
    client = AzureBlobOffChainClient(mock_azure_config)
    await client.initialize()
    off_chain_id = await client.save_blob("test_prefix", b"test_payload")
    assert off_chain_id.startswith("dlt_payloads/test_prefix")


@pytest.mark.asyncio
async def test_azure_blob_get_blob_success(
    mock_azure_config, mock_azure_blob_client, mock_boto3_client
):
    """Test successful blob retrieval for Azure Blob client."""
    client = AzureBlobOffChainClient(mock_azure_config)
    await client.initialize()
    retrieved_blob = await client.get_blob("mock_key")
    assert retrieved_blob == b"mock_data"


@pytest.mark.asyncio
async def test_ipfs_health_check_success(mock_ipfs_client):
    """Test successful health check for IPFS client."""
    mock_config = {
        "ipfs": {"api_url": "mock_url", "log_format": "json", "temp_file_ttl": 3600.0}
    }
    client = IPFSClient(mock_config)
    await client.initialize()
    result = await client.health_check()
    assert result["status"] is True


@pytest.mark.asyncio
async def test_ipfs_save_blob_success(mock_ipfs_client):
    """Test successful blob save for IPFS client."""
    mock_config = {
        "ipfs": {"api_url": "mock_url", "log_format": "json", "temp_file_ttl": 3600.0}
    }
    client = IPFSClient(mock_config)
    await client.initialize()
    off_chain_id = await client.save_blob("test_prefix", b"test_payload")
    assert off_chain_id == "mock_ipfs_hash"


@pytest.mark.asyncio
async def test_ipfs_get_blob_success(mock_ipfs_client):
    """Test successful blob retrieval for IPFS client."""
    mock_config = {
        "ipfs": {"api_url": "mock_url", "log_format": "json", "temp_file_ttl": 3600.0}
    }
    client = IPFSClient(mock_config)
    await client.initialize()
    retrieved_blob = await client.get_blob("mock_hash")
    assert retrieved_blob == b"mock_data"


def test_in_memory_client_forbidden_in_prod(mocker):
    """Test that InMemoryOffChainClient is forbidden in production mode."""
    mocker.patch(
        "simulation.plugins.dlt_clients.dlt_offchain_clients.PRODUCTION_MODE", True
    )
    with pytest.raises(DLTClientConfigurationError):
        InMemoryOffChainClient({"in_memory": {}})


@pytest.mark.asyncio
async def test_in_memory_save_and_get_blob_success(mocker):
    """Test successful save and get blob operation on the InMemory client."""
    mocker.patch(
        "simulation.plugins.dlt_clients.dlt_offchain_clients.PRODUCTION_MODE", False
    )
    mock_config = {"in_memory": {"log_format": "json", "temp_file_ttl": 3600.0}}
    client = InMemoryOffChainClient(mock_config)
    payload = b"this is a test blob"
    off_chain_id = await client.save_blob("test_prefix", payload)
    assert off_chain_id in client.store
    assert client.store[off_chain_id] == payload
    retrieved_blob = await client.get_blob(off_chain_id)
    assert retrieved_blob == payload
    with pytest.raises(FileNotFoundError):
        await client.get_blob("nonexistent-key")
