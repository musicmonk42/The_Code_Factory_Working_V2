# self_fixing_engineer/arbiter/learner/tests/conftest.py
import os
import sys
from unittest.mock import MagicMock, patch
import pytest

# Set test environment variables BEFORE any arbiter imports
os.environ["TESTING"] = "1"
os.environ["AWS_REGION"] = ""  # Disable AWS SSM lookup
os.environ.setdefault("FALLBACK_ENCRYPTION_KEY", "dGVzdC1lbmNyeXB0aW9uLWtleS0zMi1ieXRlcw==")  # base64 32-byte key

# Mock botocore exceptions BEFORE importing encryption module
# This prevents "catching classes that do not inherit from BaseException" errors
class MockNoCredentialsError(Exception):
    """Mock AWS NoCredentialsError exception"""
    pass

class MockClientError(Exception):
    """Mock AWS ClientError exception"""
    pass

# Patch botocore.exceptions module
botocore_exceptions = MagicMock()
botocore_exceptions.NoCredentialsError = MockNoCredentialsError
botocore_exceptions.ClientError = MockClientError
sys.modules['botocore.exceptions'] = botocore_exceptions

# Patch boto3 to prevent actual AWS calls
boto3_mock = MagicMock()
boto3_mock.client = MagicMock(return_value=MagicMock())
sys.modules['boto3'] = boto3_mock

@pytest.fixture(scope="session", autouse=True)
def setup_encryption_for_tests():
    """
    Ensure encryption module is properly initialized for tests.
    This runs once per test session before any tests execute.
    """
    # Import after mocks are in place
    from arbiter.learner.encryption import ArbiterConfig
    
    # Ensure keys are loaded with test-safe configuration
    if not ArbiterConfig.ENCRYPTION_KEYS:
        ArbiterConfig.load_keys()
    
    yield
    
    # Cleanup after tests
    ArbiterConfig.ENCRYPTION_KEYS.clear()


@pytest.fixture(autouse=True)
def mock_aws_ssm():
    """
    Mock AWS SSM client for all learner tests to prevent real AWS API calls.
    """
    with patch('boto3.client') as mock_client:
        # Configure the mock SSM client
        mock_ssm = MagicMock()
        mock_ssm.get_parameter.return_value = {
            'Parameter': {
                'Value': 'dGVzdC1lbmNyeXB0aW9uLWtleS0zMi1ieXRlcw=='  # base64 32-byte key
            }
        }
        mock_client.return_value = mock_ssm
        yield mock_client
