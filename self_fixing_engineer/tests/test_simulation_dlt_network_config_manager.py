# simulation/tests/test_dlt_network_config_manager.py
"""
Test suite for the DLT Network Config Manager module.
"""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# Add parent directories to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

# Import the module dynamically after setting environment variables
import self_fixing_engineer.simulation.plugins.dlt_network_config_manager as dlt_module


@pytest.fixture
def clean_env():
    """Clean environment variables before and after tests."""
    # Store original env
    original_env = os.environ.copy()

    # Clean DLT-related env vars
    for key in list(os.environ.keys()):
        if key.startswith("DLT_NETWORK_CONFIG_") or key == "DLT_NETWORK_CONFIGS_JSON":
            del os.environ[key]

    # Set test defaults
    os.environ["PRODUCTION_MODE"] = "false"
    os.environ["DLT_VALIDATE_PATHS"] = "false"

    yield

    # Restore original env
    os.environ.clear()
    os.environ.update(original_env)

    # Reset singleton
    dlt_module.DLTNetworkConfigManager._instance = None


@pytest.fixture
def mock_boto3():
    """Mock boto3 for AWS Secrets Manager."""
    with patch.object(dlt_module, "BOTO3_AVAILABLE", True):
        with patch.object(dlt_module, "boto3") as mock_boto:
            mock_client = MagicMock()
            mock_boto.client.return_value = mock_client

            def get_secret_value(SecretId):
                if SecretId == "evm/prod-private-key":
                    return {"SecretString": "0x" + "A" * 64}
                raise Exception("Secret not found")

            mock_client.get_secret_value = get_secret_value
            yield mock_boto


class TestSecretScrubbing:
    """Test secret scrubbing functionality."""

    def test_scrub_azure_connection_string(self):
        """Test Azure connection string scrubbing."""
        data = {
            "connection": "DefaultEndpointsProtocol=https;AccountName=myaccount;AccountKey=mykey123"
        }
        scrubbed = dlt_module.scrub_secrets(data)
        assert (
            scrubbed["connection"]
            == "DefaultEndpointsProtocol=https;AccountName=[REDACTED];AccountKey=[REDACTED]"
        )

    def test_scrub_generic_secrets(self):
        """Test generic secret key-value scrubbing."""
        data = {
            "private_key": "0xdeadbeef",
            "password": "supersecret",
            "api_key": "key123",
            "normal_field": "visible",
        }
        scrubbed = dlt_module.scrub_secrets(data)
        assert scrubbed["private_key"] == "[REDACTED]"
        assert scrubbed["password"] == "[REDACTED]"
        assert scrubbed["api_key"] == "[REDACTED]"
        assert scrubbed["normal_field"] == "visible"

    def test_scrub_nested_secrets(self):
        """Test scrubbing in nested structures."""
        data = {"config": {"database": {"password": "dbpass", "host": "localhost"}}}
        scrubbed = dlt_module.scrub_secrets(data)
        assert scrubbed["config"]["database"]["password"] == "[REDACTED]"
        assert scrubbed["config"]["database"]["host"] == "localhost"


class TestDLTNetworkConfig:
    """Test DLTNetworkConfig model validation."""

    def test_valid_simple_config(self):
        """Test valid simple DLT configuration."""
        config_data = {
            "name": "test-simple",
            "dlt_type": "simple",
            "off_chain_storage_type": "in_memory",
        }
        config = dlt_module.DLTNetworkConfig.load_and_validate(config_data)
        assert config.name == "test-simple"
        assert config.dlt_type == "simple"
        assert config.off_chain_storage_type == "in_memory"

    def test_invalid_dlt_type(self):
        """Test invalid DLT type validation."""
        config_data = {
            "name": "test",
            "dlt_type": "invalid_type",
            "off_chain_storage_type": "in_memory",
        }
        with pytest.raises(dlt_module.DLTClientConfigurationError) as exc:
            dlt_module.DLTNetworkConfig.load_and_validate(config_data)
        assert "DLT type must be one of" in str(exc.value)

    def test_invalid_off_chain_type(self):
        """Test invalid off-chain storage type validation."""
        config_data = {
            "name": "test",
            "dlt_type": "simple",
            "off_chain_storage_type": "invalid_storage",
        }
        with pytest.raises(dlt_module.DLTClientConfigurationError) as exc:
            dlt_module.DLTNetworkConfig.load_and_validate(config_data)
        assert "Off-chain storage type must be one of" in str(exc.value)

    def test_evm_config_validation(self):
        """Test EVM configuration validation."""
        config_data = {
            "name": "test-evm",
            "dlt_type": "evm",
            "off_chain_storage_type": "in_memory",
            "evm": {
                "rpc_url": "https://mainnet.infura.io/v3/abc",
                "chain_id": 1,
                "contract_address": "0x1234567890abcdef1234567890abcdef12345678",
                "contract_abi_path": __file__,  # Use this file as it exists
                "private_key": "0x" + "B" * 64,
            },
        }
        config = dlt_module.DLTNetworkConfig.load_and_validate(config_data)
        assert config.evm.chain_id == 1
        assert (
            config.evm.contract_address == "0x1234567890abcdef1234567890abcdef12345678"
        )

    def test_evm_invalid_contract_address(self):
        """Test EVM invalid contract address validation."""
        config_data = {
            "name": "test-evm",
            "dlt_type": "evm",
            "off_chain_storage_type": "in_memory",
            "evm": {
                "rpc_url": "https://mainnet.infura.io/v3/abc",
                "chain_id": 1,
                "contract_address": "invalid_address",
                "contract_abi_path": __file__,
                "private_key": "0x" + "B" * 64,
            },
        }
        with pytest.raises(dlt_module.DLTClientConfigurationError) as exc:
            dlt_module.DLTNetworkConfig.load_and_validate(config_data)
        assert "contract_address must be a 0x-prefixed" in str(exc.value)

    def test_missing_off_chain_config(self):
        """Test validation when off-chain config is missing."""
        config_data = {
            "name": "test",
            "dlt_type": "simple",
            "off_chain_storage_type": "s3",
            # Missing s3 config
        }
        with pytest.raises(dlt_module.DLTClientConfigurationError) as exc:
            dlt_module.DLTNetworkConfig.load_and_validate(config_data)
        assert "requires 's3' configuration" in str(exc.value)


class TestDLTNetworkConfigManager:
    """Test DLTNetworkConfigManager functionality."""

    def test_singleton_pattern(self, clean_env):
        """Test that manager follows singleton pattern."""
        mgr1 = dlt_module.get_dlt_network_config_manager()
        mgr2 = dlt_module.get_dlt_network_config_manager()
        assert mgr1 is mgr2

    def test_load_from_individual_env_vars(self, clean_env):
        """Test loading configurations from individual environment variables."""
        os.environ["DLT_NETWORK_CONFIG_TEST_JSON"] = json.dumps(
            {
                "name": "test",
                "dlt_type": "simple",
                "off_chain_storage_type": "in_memory",
            }
        )

        mgr = dlt_module.get_dlt_network_config_manager()
        configs = mgr.get_all_configs()

        assert "test" in configs
        assert configs["test"].dlt_type == "simple"

    def test_load_from_combined_env_var(self, clean_env):
        """Test loading configurations from combined environment variable."""
        configs_list = [
            {
                "name": "config1",
                "dlt_type": "simple",
                "off_chain_storage_type": "in_memory",
            },
            {
                "name": "config2",
                "dlt_type": "simple",
                "off_chain_storage_type": "in_memory",
                "default_timeout_seconds": 60,
            },
        ]
        os.environ["DLT_NETWORK_CONFIGS_JSON"] = json.dumps(configs_list)

        mgr = dlt_module.get_dlt_network_config_manager()
        configs = mgr.get_all_configs()

        assert "config1" in configs
        assert "config2" in configs
        assert configs["config2"].default_timeout_seconds == 60

    def test_individual_overrides_combined(self, clean_env):
        """Test that individual env vars override combined ones."""
        os.environ["DLT_NETWORK_CONFIGS_JSON"] = json.dumps(
            [
                {
                    "name": "test",
                    "dlt_type": "simple",
                    "off_chain_storage_type": "in_memory",
                    "default_timeout_seconds": 30,
                }
            ]
        )
        os.environ["DLT_NETWORK_CONFIG_TEST_JSON"] = json.dumps(
            {
                "name": "test",
                "dlt_type": "simple",
                "off_chain_storage_type": "in_memory",
                "default_timeout_seconds": 60,
            }
        )

        mgr = dlt_module.get_dlt_network_config_manager()
        configs = mgr.get_all_configs()

        assert configs["test"].default_timeout_seconds == 60

    def test_name_normalization(self, clean_env):
        """Test that config names are normalized to lowercase."""
        os.environ["DLT_NETWORK_CONFIG_TEST_JSON"] = json.dumps(
            {
                "name": "TEST-Config",
                "dlt_type": "simple",
                "off_chain_storage_type": "in_memory",
            }
        )

        mgr = dlt_module.get_dlt_network_config_manager()
        configs = mgr.get_all_configs()

        assert "test-config" in configs
        assert "TEST-Config" not in configs

    def test_get_default_config(self, clean_env):
        """Test getting default configuration."""
        os.environ["DLT_NETWORK_CONFIG_ALPHA_JSON"] = json.dumps(
            {
                "name": "alpha",
                "dlt_type": "simple",
                "off_chain_storage_type": "in_memory",
            }
        )
        os.environ["DLT_NETWORK_CONFIG_BETA_JSON"] = json.dumps(
            {
                "name": "beta",
                "dlt_type": "simple",
                "off_chain_storage_type": "in_memory",
            }
        )

        mgr = dlt_module.get_dlt_network_config_manager()
        default = mgr.get_default_config()

        # Should return alphabetically first
        assert default.name == "alpha"

    @pytest.mark.asyncio
    async def test_refresh_configs_if_changed(self, clean_env):
        """Test runtime configuration refresh."""
        os.environ["DLT_NETWORK_CONFIG_INITIAL_JSON"] = json.dumps(
            {
                "name": "initial",
                "dlt_type": "simple",
                "off_chain_storage_type": "in_memory",
            }
        )

        mgr = dlt_module.get_dlt_network_config_manager()
        initial_configs = mgr.get_all_configs()
        assert "initial" in initial_configs

        # Add new config
        os.environ["DLT_NETWORK_CONFIG_NEW_JSON"] = json.dumps(
            {"name": "new", "dlt_type": "simple", "off_chain_storage_type": "in_memory"}
        )

        refreshed = await mgr.refresh_configs_if_changed()
        assert refreshed is True

        updated_configs = mgr.get_all_configs()
        assert "new" in updated_configs
        assert "initial" in updated_configs

    @pytest.mark.asyncio
    async def test_no_refresh_when_unchanged(self, clean_env):
        """Test that refresh returns False when configs haven't changed."""
        os.environ["DLT_NETWORK_CONFIG_TEST_JSON"] = json.dumps(
            {
                "name": "test",
                "dlt_type": "simple",
                "off_chain_storage_type": "in_memory",
            }
        )

        mgr = dlt_module.get_dlt_network_config_manager()

        # First refresh should detect no change
        refreshed = await mgr.refresh_configs_if_changed()
        assert refreshed is False

    def test_production_mode_validation(self, clean_env):
        """Test production mode specific validations."""
        # Set production mode before importing/reloading the module
        os.environ["PRODUCTION_MODE"] = "true"

        # We need to reload the module to pick up the new PRODUCTION_MODE value
        # First, reset the singleton
        dlt_module.DLTNetworkConfigManager._instance = None

        # Patch PRODUCTION_MODE directly in the module
        with patch.object(dlt_module, "PRODUCTION_MODE", True):
            os.environ["DLT_NETWORK_CONFIG_INSECURE_JSON"] = json.dumps(
                {
                    "name": "insecure",
                    "dlt_type": "evm",
                    "off_chain_storage_type": "in_memory",
                    "evm": {
                        "rpc_url": "http://insecure.com",  # Non-HTTPS
                        "chain_id": 1,
                        "contract_address": "0x1234567890abcdef1234567890abcdef12345678",
                        "contract_abi_path": __file__,
                        "private_key": "0x" + "A" * 64,
                    },
                }
            )

            mgr = dlt_module.get_dlt_network_config_manager()
            configs = mgr.get_all_configs()

            # Should not load insecure config in production
            assert "insecure" not in configs

    def test_aws_secrets_loading(self, clean_env, mock_boto3):
        """Test loading secrets from AWS Secrets Manager."""
        os.environ["DLT_NETWORK_CONFIG_EVM_JSON"] = json.dumps(
            {
                "name": "evm-with-secret",
                "dlt_type": "evm",
                "off_chain_storage_type": "in_memory",
                "evm": {
                    "rpc_url": "https://mainnet.infura.io/v3/abc",
                    "chain_id": 1,
                    "contract_address": "0x1234567890abcdef1234567890abcdef12345678",
                    "contract_abi_path": __file__,
                    "private_key_secret_id": "evm/prod-private-key",
                },
            }
        )

        mgr = dlt_module.get_dlt_network_config_manager()
        configs = mgr.get_all_configs()

        assert "evm-with-secret" in configs
        assert configs["evm-with-secret"].evm.private_key == "0x" + "A" * 64


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
