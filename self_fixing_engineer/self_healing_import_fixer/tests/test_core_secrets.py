# test_core_secrets.py - Complete fixed version

import os
import shutil
import pytest
from unittest.mock import patch, MagicMock

import self_healing_import_fixer.analyzer.core_secrets as core_secrets
from self_healing_import_fixer.analyzer.core_secrets import (
    SecretsManager,
    SecretConfig,
    SecretProvider,
)


# Create a proper mock for ClientError
class MockClientError(Exception):
    def __init__(self, error_response, operation_name):
        self.response = error_response
        self.operation_name = operation_name
        super().__init__(str(error_response))


@pytest.fixture(autouse=True)
def cleanup_env():
    yield
    if os.path.exists(".secrets"):
        shutil.rmtree(".secrets", ignore_errors=True)
    for k in list(os.environ.keys()):
        if k.startswith("TEST_SECRET"):
            os.environ.pop(k, None)
    if os.path.exists(".secrets.key"):
        os.remove(".secrets.key")


@pytest.fixture
def env_secrets_manager():
    config = SecretConfig(provider=SecretProvider.ENV_VARS)
    return SecretsManager(config)


@pytest.fixture
def local_enc_secrets_manager(monkeypatch, tmp_path):
    import self_healing_import_fixer.analyzer.core_secrets as core_secrets_mod

    MockFernet = MagicMock()
    instance = MagicMock()
    instance.encrypt.side_effect = lambda b: b"encrypted-" + b
    instance.decrypt.side_effect = lambda b: b.replace(b"encrypted-", b"")
    MockFernet.generate_key.return_value = (
        b"ZmFrZWtleTEyMzQ1Njc4OTAxMjM0NTY3ODkwMTIzNDU2Nzg5MDEyMzQ1Njc4OTA="
    )
    MockFernet.return_value = instance

    monkeypatch.setattr(core_secrets_mod, "Fernet", MockFernet)
    monkeypatch.setattr(core_secrets_mod, "CRYPTO_AVAILABLE", True)
    key = "ZmFrZWtleTEyMzQ1Njc4OTAxMjM0NTY3ODkwMTIzNDU2Nzg5MDEyMzQ1Njc4OTA="
    config = SecretConfig(
        provider=SecretProvider.LOCAL_ENCRYPTED,
        encryption_key=key,
        local_key_file=str(tmp_path / ".secrets.key"),
    )
    sm = SecretsManager(config)

    fake_files = {}

    def fake_set_local_encrypted_secret(secret_name, secret_value):
        fake_files[secret_name] = secret_value
        return True

    def fake_get_local_encrypted_secret(secret_name):
        if secret_name not in fake_files:
            return None
        return fake_files[secret_name]

    def fake_delete_local_encrypted_secret(secret_name):
        if secret_name in fake_files:
            del fake_files[secret_name]
            return True
        return False

    sm._set_local_encrypted_secret = fake_set_local_encrypted_secret
    sm._get_local_encrypted_secret = fake_get_local_encrypted_secret
    sm._delete_local_encrypted_secret = fake_delete_local_encrypted_secret

    def fake_list_secrets(prefix=None):
        if prefix:
            return [k for k in fake_files if k.startswith(prefix)]
        return list(fake_files.keys())

    sm.list_secrets = fake_list_secrets

    return sm


@pytest.fixture
def aws_secrets_manager(monkeypatch):
    # Patch ClientError to use our mock
    monkeypatch.setattr(
        "self_healing_import_fixer.analyzer.core_secrets.ClientError", MockClientError
    )

    mock_client = MagicMock()
    store = {}

    def get_secret_value(SecretId, VersionStage=None):
        if SecretId not in store:
            error_response = {"Error": {"Code": "ResourceNotFoundException"}}
            raise MockClientError(error_response, "GetSecretValue")
        return {"SecretString": store[SecretId]}

    def create_secret(Name, Description, SecretString):
        if Name in store:
            error_response = {"Error": {"Code": "ResourceExistsException"}}
            raise MockClientError(error_response, "CreateSecret")
        store[Name] = SecretString
        return {"ARN": "arn:aws:secretsmanager:test:" + Name}

    def update_secret(SecretId, SecretString):
        store[SecretId] = SecretString
        return {"ARN": "arn:aws:secretsmanager:test:" + SecretId}

    def delete_secret(SecretId, **kwargs):
        store.pop(SecretId, None)
        return {}

    def list_secrets(**kwargs):
        prefix = None
        if "Filters" in kwargs:
            for f in kwargs["Filters"]:
                if f["Key"] == "name":
                    prefix = f["Values"][0]
        result = []
        for k in store:
            if not prefix or k.startswith(prefix):
                result.append({"Name": k})
        return {"SecretList": result}

    mock_client.get_secret_value.side_effect = get_secret_value
    mock_client.create_secret.side_effect = create_secret
    mock_client.update_secret.side_effect = update_secret
    mock_client.delete_secret.side_effect = delete_secret
    mock_client.list_secrets.side_effect = list_secrets

    with patch(
        "self_healing_import_fixer.analyzer.core_secrets.boto3.client",
        return_value=mock_client,
    ):
        config = SecretConfig(provider=SecretProvider.AWS_SECRETS_MANAGER, aws_region="us-east-1")
        yield SecretsManager(config)


def test_env_get_set_delete_list(env_secrets_manager):
    sm = env_secrets_manager
    os.environ["TEST_SECRET_ENV"] = "envvalue"
    assert sm.get_secret("TEST_SECRET_ENV") == "envvalue"
    assert sm.set_secret("TEST_SECRET_NEW", "valnew")
    assert os.environ["TEST_SECRET_NEW"] == "valnew"
    secrets = sm.list_secrets(prefix="TEST_SECRET")
    assert "TEST_SECRET_ENV" in secrets and "TEST_SECRET_NEW" in secrets
    assert sm.delete_secret("TEST_SECRET_ENV")
    assert os.environ.get("TEST_SECRET_ENV") is None


def test_local_encrypted_get_set_delete_list(local_enc_secrets_manager):
    sm = local_enc_secrets_manager
    assert sm.set_secret("TEST_SECRET_LOC", "localval")
    assert sm.get_secret("TEST_SECRET_LOC") == "localval"
    secrets = sm.list_secrets()
    assert "TEST_SECRET_LOC" in secrets
    assert sm.delete_secret("TEST_SECRET_LOC")
    sm.clear_cache()
    assert sm.get_secret("TEST_SECRET_LOC") is None


def test_local_encrypted_rotation(local_enc_secrets_manager):
    sm = local_enc_secrets_manager
    sm.set_secret("ROTATE_ME", "oldval1")
    new_value = sm.rotate_secret("ROTATE_ME")
    assert new_value is not None and new_value != "oldval1"
    assert sm.get_secret("ROTATE_ME") == new_value


def test_local_encrypted_cache_expiry(local_enc_secrets_manager, monkeypatch):
    sm = local_enc_secrets_manager
    sm.config.cache_ttl_seconds = 1
    sm.set_secret("CACHE_TTL", "v1")
    assert sm.get_secret("CACHE_TTL") == "v1"
    monkeypatch.setattr(core_secrets.time, "time", lambda: 10000000)
    sm._cache["CACHE_TTL:latest"] = ("v1", 10000000 - 2)
    assert sm.get_secret("CACHE_TTL") == "v1"


def test_local_encrypted_clear_cache(local_enc_secrets_manager):
    sm = local_enc_secrets_manager
    sm.set_secret("CACHE1", "abc")
    sm.get_secret("CACHE1")
    sm.clear_cache()
    assert sm._cache == {}


def test_local_encrypted_stats(local_enc_secrets_manager):
    sm = local_enc_secrets_manager
    sm.set_secret("STATS1", "a" * 20)
    stats = sm.get_stats()
    assert stats["provider"] == "local_encrypted"
    assert stats["cache_size"] >= 0
    assert stats["encryption_enabled"]


def test_secret_policy_validation(local_enc_secrets_manager):
    sm = local_enc_secrets_manager
    valid, error = sm.validate_secret_policy("short1!")
    assert not valid and "at least" in error
    valid, error = sm.validate_secret_policy("Nouppercase!!")
    assert not valid
    valid, error = sm.validate_secret_policy("ABCdefg123!x")
    assert valid
    valid, error = sm.validate_secret_policy("Password123!x")
    assert not valid and "common pattern" in error


def test_list_secrets_empty(local_enc_secrets_manager):
    sm = local_enc_secrets_manager
    secrets = sm.list_secrets()
    assert isinstance(secrets, list) and len(secrets) == 0


def test_aws_secrets_manager_get_set_delete(aws_secrets_manager):
    sm = aws_secrets_manager
    assert sm.set_secret("aws-test", "awsval1")
    assert sm.get_secret("aws-test") == "awsval1"

    # Clear cache before updating to ensure we get the new value
    sm.clear_cache()

    assert sm.set_secret("aws-test", "awsval2")
    assert sm.get_secret("aws-test") == "awsval2"
    assert sm.delete_secret("aws-test")
    assert sm.get_secret("aws-test") is None


def test_aws_secrets_manager_list(aws_secrets_manager):
    sm = aws_secrets_manager
    sm.set_secret("aws1", "v1")
    sm.set_secret("aws2", "v2")
    secrets = sm.list_secrets()
    assert "aws1" in secrets and "aws2" in secrets


def test_get_secret_returns_none_on_missing_env(env_secrets_manager):
    sm = env_secrets_manager
    if "NOT_EXISTING_ENV" in os.environ:
        del os.environ["NOT_EXISTING_ENV"]
    assert sm.get_secret("NOT_EXISTING_ENV") is None


def test_set_secret_handles_unknown_provider():
    config = SecretConfig(provider="unknown_provider")
    sm = SecretsManager(config)
    assert not sm.set_secret("FOO", "BAR")


def test_delete_secret_handles_unknown_provider():
    config = SecretConfig(provider="unknown_provider")
    sm = SecretsManager(config)
    assert not sm.delete_secret("FOO")


def test_list_secrets_handles_unknown_provider():
    config = SecretConfig(provider="unknown_provider")
    sm = SecretsManager(config)
    assert sm.list_secrets() == []


def test_get_secret_handles_unknown_provider():
    config = SecretConfig(provider="unknown_provider")
    sm = SecretsManager(config)
    assert sm.get_secret("FOO") is None
