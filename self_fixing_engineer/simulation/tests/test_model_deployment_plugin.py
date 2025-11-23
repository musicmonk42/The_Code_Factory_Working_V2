# tests/test_model_deployment_plugin.py

import pytest
import json
from unittest.mock import patch, AsyncMock, MagicMock, mock_open

# Import the plugin using the correct module path
from simulation.plugins.model_deployment_plugin import (
    ModelDeploymentPlugin,
    ModelDeploymentStrategy,
)

# ==============================================================================
# Pytest Fixtures for mocking external dependencies and environment
# ==============================================================================


@pytest.fixture(autouse=True)
def mock_external_dependencies():
    """
    Mocks external libraries and environment variables for complete isolation.
    """
    with patch(
        "simulation.plugins.model_deployment_plugin.os.path.exists", return_value=True
    ) as mock_exists, patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep, patch(
        "os.getenv"
    ) as mock_getenv, patch(
        "os.remove"
    ), patch(
        "json.dump"
    ) as mock_json_dump, patch(
        "simulation.plugins.model_deployment_plugin.logger"
    ) as mock_logger:

        mock_getenv.return_value = "mock_env_key"

        # Configure mock logger
        mock_logger.info = MagicMock()
        mock_logger.warning = MagicMock()
        mock_logger.error = MagicMock()
        mock_logger.addHandler = MagicMock()
        mock_logger.setLevel = MagicMock()

        yield {
            "mock_sleep": mock_sleep,
            "mock_getenv": mock_getenv,
            "mock_exists": mock_exists,
            "mock_json_dump": mock_json_dump,
            "mock_logger": mock_logger,
        }


@pytest.fixture
def mock_global_config_path(mock_external_dependencies):
    """Mocks the global deployment config file content."""
    config_data = {
        "local_api": {
            "endpoint_url": "http://mock-global-api.com",
            "api_key_env_var": "LOCAL_API_KEY",
        },
        "cloud_service": {
            "service_name": "aws_sagemaker",
            "region": "us-east-1",
        },
    }

    # Mock the open() function and json.load() together
    with patch("builtins.open", mock_open(read_data=json.dumps(config_data))):
        with patch("json.load", return_value=config_data):
            yield "mock_config.json"


# ==============================================================================
# Unit Tests for `ModelDeploymentStrategy` (Abstract Base Class)
# ==============================================================================


def test_model_deployment_strategy_abstract_methods():
    """Test that abstract class cannot be instantiated without implementing abstract methods."""

    class MockStrategy(ModelDeploymentStrategy):
        pass

    # This should raise TypeError because abstract methods are not implemented
    with pytest.raises(TypeError, match="Can't instantiate abstract class"):
        MockStrategy({}, correlation_id="test")


def test_model_deployment_strategy_concrete():
    """Test that concrete implementation can be instantiated."""

    class ConcreteStrategy(ModelDeploymentStrategy):
        async def deploy(self, *args, **kwargs):
            return {"status": "success"}

        async def undeploy(self, *args, **kwargs):
            return {"status": "success"}

    # This should work fine
    strategy = ConcreteStrategy({}, correlation_id="test")
    assert strategy is not None


def test_validate_config_and_logic():
    """Test the configuration validation logic with AND/OR rules."""

    class MockStrategy(ModelDeploymentStrategy):
        async def deploy(self, *args, **kwargs):
            pass

        async def undeploy(self, *args, **kwargs):
            pass

    # Test AND logic - both keys present (should pass)
    strategy = MockStrategy({"key1": "val", "key2": "val"}, correlation_id="test")
    strategy._validate_config(["key1", "key2"])

    # Test AND logic - missing key2 (should fail)
    with pytest.raises(ValueError, match="Config for MockStrategy must include: key2"):
        strategy = MockStrategy({"key1": "val"}, correlation_id="test")
        strategy._validate_config(["key1", "key2"])

    # Test AND with inner OR - has at least one from the OR group AND the required key
    strategy = MockStrategy({"key1": "val", "key3": "val"}, correlation_id="test")
    strategy._validate_config([["key1", "key2"], "key3"])

    # Also valid: has key2 from OR group and key3
    strategy = MockStrategy({"key2": "val", "key3": "val"}, correlation_id="test")
    strategy._validate_config([["key1", "key2"], "key3"])

    # Test AND with inner OR - missing the OR group (should fail)
    with pytest.raises(ValueError, match=r"\(key1 OR key2\)"):
        strategy = MockStrategy({"key3": "val"}, correlation_id="test")
        strategy._validate_config([["key1", "key2"], "key3"])

    # Test AND with inner OR - missing the required key (should fail)
    with pytest.raises(ValueError, match="key3"):
        strategy = MockStrategy({"key1": "val"}, correlation_id="test")
        strategy._validate_config([["key1", "key2"], "key3"])


# ==============================================================================
# Integration Tests for `ModelDeploymentPlugin` workflow
# ==============================================================================


@pytest.mark.asyncio
async def test_deploy_model_local_api_success(mock_external_dependencies):
    """
    Tests a successful deployment to the Local API strategy, including config merging.
    """
    mock_getenv = mock_external_dependencies["mock_getenv"]
    mock_getenv.return_value = "mock_local_env_key"  # Ensure env key is loaded

    config_data = {
        "local_api": {
            "endpoint_url": "http://mock-global-api.com",
            "api_key_env_var": "LOCAL_API_KEY",
        },
        "cloud_service": {
            "service_name": "aws_sagemaker",
            "region": "us-east-1",
        },
    }

    with patch("builtins.open", mock_open(read_data=json.dumps(config_data))):
        with patch("json.load", return_value=config_data):
            deployer = ModelDeploymentPlugin(global_config_path="mock_config.json")

            specific_config = {
                # This will be merged with global config, overwriting some fields
                "api_key_env_var": "MOCK_ENV_VAR",
            }

            deployment_info = await deployer.deploy_model(
                strategy_type="local_api",
                model_path="/mock/path/to/model.pkl",
                model_version="1.0.0",
                specific_config=specific_config,
            )

            assert deployment_info["status"] == "success"
            assert "mock-global-api.com" in deployment_info["endpoint_url"]
            assert "local-1.0.0" in deployment_info["deployment_id"]
            mock_getenv.assert_called_with("MOCK_ENV_VAR")


@pytest.mark.asyncio
async def test_undeploy_model_cloud_service_success(mock_external_dependencies):
    """
    Tests a successful undeployment from the Cloud Service strategy.
    """
    config_data = {
        "local_api": {
            "endpoint_url": "http://mock-global-api.com",
            "api_key_env_var": "LOCAL_API_KEY",
        },
        "cloud_service": {
            "service_name": "aws_sagemaker",
            "region": "us-east-1",
        },
    }

    with patch("builtins.open", mock_open(read_data=json.dumps(config_data))):
        with patch("json.load", return_value=config_data):
            deployer = ModelDeploymentPlugin(global_config_path="mock_config.json")

            specific_config = {
                "service_name": "mock_service",
                "region": "us-west-2",
            }

            deployment_info = await deployer.undeploy_model(
                strategy_type="cloud_service",
                deployment_id="mock-deployment-id-123",
                specific_config=specific_config,
            )

            assert deployment_info["status"] == "success"


@pytest.mark.asyncio
async def test_deploy_model_missing_config_local_api(mock_external_dependencies):
    """
    Test that deployment fails with a ValueError when a required key is missing.
    """
    # Mock getenv to return None to simulate missing environment variable
    mock_external_dependencies["mock_getenv"].return_value = None

    config_data = {
        "local_api": {
            "endpoint_url": "http://mock.url",
            "api_key_env_var": "LOCAL_API_KEY",
        },
        "cloud_service": {
            "service_name": "aws_sagemaker",
            "region": "us-east-1",
        },
    }

    with patch("builtins.open", mock_open(read_data=json.dumps(config_data))):
        with patch("json.load", return_value=config_data):
            deployer = ModelDeploymentPlugin(global_config_path="mock_config.json")

            specific_config = {
                "endpoint_url": "http://mock.url",
                # Missing API key config intentionally
            }

            with pytest.raises(ValueError, match="No API key found"):
                await deployer.deploy_model(
                    strategy_type="local_api",
                    model_path="/mock/path/to/model.pkl",
                    model_version="1.0.0",
                    specific_config=specific_config,
                )


@pytest.mark.asyncio
async def test_deploy_model_unknown_strategy_type(mock_external_dependencies):
    """
    Test that the plugin manager raises a ValueError for an unknown strategy type.
    """
    config_data = {
        "local_api": {
            "endpoint_url": "http://mock-global-api.com",
            "api_key_env_var": "LOCAL_API_KEY",
        },
        "cloud_service": {
            "service_name": "aws_sagemaker",
            "region": "us-east-1",
        },
    }

    with patch("builtins.open", mock_open(read_data=json.dumps(config_data))):
        with patch("json.load", return_value=config_data):
            deployer = ModelDeploymentPlugin(global_config_path="mock_config.json")

            with pytest.raises(ValueError, match="Unknown deployment strategy type"):
                await deployer.deploy_model(
                    strategy_type="unknown_strategy",
                    model_path="/mock/path/to/model.pkl",
                    model_version="1.0.0",
                    specific_config={},
                )
