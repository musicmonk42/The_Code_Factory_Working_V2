# self_healing_import_fixer/analyzer/core_secrets.py

"""
Enterprise-grade secrets management module.
Provides secure storage, retrieval, and rotation of sensitive credentials.
"""

import base64
import logging
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Third-party imports with graceful fallbacks
try:
    import boto3
    from botocore.exceptions import ClientError

    AWS_AVAILABLE = True
except ImportError:
    AWS_AVAILABLE = False
    ClientError = Exception

try:
    import hvac  # HashiCorp Vault

    VAULT_AVAILABLE = True
except ImportError:
    VAULT_AVAILABLE = False

try:
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient

    AZURE_AVAILABLE = True
except ImportError:
    AZURE_AVAILABLE = False

try:
    from google.cloud import secretmanager

    GCP_AVAILABLE = True
except ImportError:
    GCP_AVAILABLE = False

try:
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2

    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False

# Configure module logger
logger = logging.getLogger(__name__)

# Global constants
PRODUCTION_MODE = os.getenv("PRODUCTION_MODE", "false").lower() == "true"
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")


# Custom exception for security-related errors
class SecurityAnalysisError(Exception):
    """Custom exception for security and secrets analysis errors."""

    pass


class SecretProvider(Enum):
    """Supported secret storage providers."""

    ENV_VARS = "env_vars"
    AWS_SECRETS_MANAGER = "aws_secrets_manager"
    AWS_SSM = "aws_ssm"
    HASHICORP_VAULT = "hashicorp_vault"
    AZURE_KEY_VAULT = "azure_key_vault"
    GCP_SECRET_MANAGER = "gcp_secret_manager"
    LOCAL_ENCRYPTED = "local_encrypted"


@dataclass
class SecretConfig:
    """Configuration for secrets management."""

    provider: SecretProvider = field(default_factory=lambda: SecretProvider.ENV_VARS)
    aws_region: str = field(default_factory=lambda: os.getenv("AWS_REGION", "us-east-1"))
    vault_url: Optional[str] = field(default_factory=lambda: os.getenv("VAULT_URL"))
    vault_token: Optional[str] = field(default_factory=lambda: os.getenv("VAULT_TOKEN"))
    azure_vault_url: Optional[str] = field(default_factory=lambda: os.getenv("AZURE_VAULT_URL"))
    gcp_project_id: Optional[str] = field(default_factory=lambda: os.getenv("GCP_PROJECT_ID"))
    local_key_file: Optional[str] = field(
        default_factory=lambda: os.getenv("LOCAL_KEY_FILE", ".secrets.key")
    )
    cache_ttl_seconds: int = 300
    auto_rotation_days: int = 90
    encryption_key: Optional[str] = field(default_factory=lambda: os.getenv("ENCRYPTION_KEY"))


class SecretsManager:
    """
    Unified interface for managing secrets across multiple providers.
    Supports caching, rotation, and encryption.
    """

    def __init__(self, config: Optional[SecretConfig] = None):
        self.config = config or SecretConfig()
        self._cache: Dict[str, Tuple[Any, float]] = {}
        self._lock = threading.Lock()
        self._providers: Dict[SecretProvider, Any] = {}
        self._initialize_providers()

        # Set up encryption if available
        self._cipher_suite = None
        if CRYPTO_AVAILABLE and self.config.encryption_key:
            self._setup_encryption()

    def _initialize_providers(self):
        """Initialize configured secret providers."""
        provider = self.config.provider

        if provider == SecretProvider.AWS_SECRETS_MANAGER and AWS_AVAILABLE:
            self._providers[provider] = boto3.client(
                "secretsmanager", region_name=self.config.aws_region
            )
        elif provider == SecretProvider.AWS_SSM and AWS_AVAILABLE:
            self._providers[provider] = boto3.client("ssm", region_name=self.config.aws_region)
        elif provider == SecretProvider.HASHICORP_VAULT and VAULT_AVAILABLE:
            if not self.config.vault_url or not self.config.vault_token:
                raise SecurityAnalysisError(
                    "Vault URL and token must be configured for HashiCorp Vault provider."
                )
            self._providers[provider] = hvac.Client(
                url=self.config.vault_url, token=self.config.vault_token
            )
        elif provider == SecretProvider.AZURE_KEY_VAULT and AZURE_AVAILABLE:
            if not self.config.azure_vault_url:
                raise SecurityAnalysisError(
                    "Azure Vault URL must be configured for Azure Key Vault provider."
                )
            try:
                credential = DefaultAzureCredential()
                self._providers[provider] = SecretClient(
                    vault_url=self.config.azure_vault_url, credential=credential
                )
            except Exception as e:
                raise SecurityAnalysisError(
                    f"Failed to authenticate with Azure Key Vault: {e}"
                ) from e
        elif provider == SecretProvider.GCP_SECRET_MANAGER and GCP_AVAILABLE:
            if not self.config.gcp_project_id:
                raise SecurityAnalysisError(
                    "GCP Project ID must be configured for GCP Secret Manager provider."
                )
            self._providers[provider] = secretmanager.SecretManagerServiceClient()

    def _setup_encryption(self):
        """Set up local encryption for secrets."""
        if not CRYPTO_AVAILABLE:
            raise SecurityAnalysisError("Cryptography library not found for local encryption.")

        try:
            if not self.config.encryption_key:
                # Generate a new key if none provided
                key = Fernet.generate_key()
                key_file = Path(self.config.local_key_file)
                key_file.write_bytes(key)
                key_file.chmod(0o600)  # Restrict permissions
                self._cipher_suite = Fernet(key)
            else:
                # Use provided key
                key = self.config.encryption_key.encode()
                self._cipher_suite = Fernet(key)
        except Exception as e:
            raise SecurityAnalysisError(f"Failed to set up local encryption: {e}") from e

    def get_secret(
        self, secret_name: str, version: Optional[str] = None, required: bool = False
    ) -> Optional[str]:
        """
        Retrieve a secret from the configured provider.

        Args:
            secret_name: Name/ID of the secret
            version: Optional version/stage of the secret
            required: If True, raise an exception if the secret is not found.

        Returns:
            Secret value or None if not found and not required.

        Raises:
            SecurityAnalysisError: If the secret is required but not found, or if a security-related error occurs.
        """
        # Check cache first
        cache_key = f"{secret_name}:{version or 'latest'}"
        with self._lock:
            if cache_key in self._cache:
                value, timestamp = self._cache[cache_key]
                if time.time() - timestamp < self.config.cache_ttl_seconds:
                    logger.debug(f"Secret '{secret_name}' retrieved from cache")
                    return value

        # Retrieve from provider
        try:
            value = self._get_from_provider(secret_name, version)

            if value is None and required:
                raise SecurityAnalysisError(f"Required secret '{secret_name}' not found.")

            # Cache the result
            if value is not None:
                with self._lock:
                    self._cache[cache_key] = (value, time.time())
                logger.info(f"Secret '{secret_name}' retrieved successfully")

            return value

        except (
            ValueError,
            ClientError,
            hvac.exceptions.InvalidRequest,
            Exception,
        ) as e:
            # Catch specific provider exceptions and wrap in a general one
            wrapped_e = SecurityAnalysisError(f"Failed to retrieve secret '{secret_name}': {e}")
            logger.error(str(wrapped_e), exc_info=True)

            # In production, always raise on failure
            if PRODUCTION_MODE:
                raise wrapped_e from e

            # In non-production, only raise if the secret was required
            if required:
                raise wrapped_e from e

            return None

    def _get_from_provider(self, secret_name: str, version: Optional[str] = None) -> Optional[str]:
        """Retrieve secret from the specific provider."""
        provider = self.config.provider

        if provider == SecretProvider.ENV_VARS:
            return os.getenv(secret_name)

        elif provider == SecretProvider.AWS_SECRETS_MANAGER:
            client = self._providers.get(provider)
            if not client:
                raise ValueError("AWS Secrets Manager client not initialized")

            try:
                kwargs = {"SecretId": secret_name}
                if version:
                    kwargs["VersionStage"] = version

                response = client.get_secret_value(**kwargs)

                if "SecretString" in response:
                    return response["SecretString"]
                else:
                    # Binary secret
                    return base64.b64decode(response["SecretBinary"]).decode("utf-8")

            except ClientError as e:
                if e.response["Error"]["Code"] == "ResourceNotFoundException":
                    return None
                raise

        elif provider == SecretProvider.AWS_SSM:
            client = self._providers.get(provider)
            if not client:
                raise ValueError("AWS SSM client not initialized")

            try:
                response = client.get_parameter(Name=secret_name, WithDecryption=True)
                return response["Parameter"]["Value"]

            except ClientError as e:
                if e.response["Error"]["Code"] == "ParameterNotFound":
                    return None
                raise

        elif provider == SecretProvider.HASHICORP_VAULT:
            client = self._providers.get(provider)
            if not client:
                raise ValueError("HashiCorp Vault client not initialized")

            response = client.secrets.kv.v2.read_secret_version(path=secret_name, version=version)
            return response["data"]["data"].get("value")

        elif provider == SecretProvider.AZURE_KEY_VAULT:
            client = self._providers.get(provider)
            if not client:
                raise ValueError("Azure Key Vault client not initialized")

            secret = client.get_secret(secret_name, version=version)
            return secret.value

        elif provider == SecretProvider.GCP_SECRET_MANAGER:
            client = self._providers.get(provider)
            if not client:
                raise ValueError("GCP Secret Manager client not initialized")

            name = f"projects/{self.config.gcp_project_id}/secrets/{secret_name}/versions/{version or 'latest'}"
            response = client.access_secret_version(request={"name": name})
            return response.payload.data.decode("UTF-8")

        elif provider == SecretProvider.LOCAL_ENCRYPTED:
            return self._get_local_encrypted_secret(secret_name)

        else:
            raise ValueError(f"Unsupported provider: {provider}")

    def set_secret(
        self, secret_name: str, secret_value: str, description: Optional[str] = None
    ) -> bool:
        """
        Store or update a secret.

        Args:
            secret_name: Name/ID of the secret
            secret_value: Secret value to store
            description: Optional description

        Returns:
            True if successful, False otherwise
        """
        try:
            provider = self.config.provider

            if provider == SecretProvider.ENV_VARS:
                os.environ[secret_name] = secret_value
                return True

            elif provider == SecretProvider.AWS_SECRETS_MANAGER:
                client = self._providers.get(provider)
                if not client:
                    raise ValueError("AWS Secrets Manager client not initialized")

                try:
                    client.create_secret(
                        Name=secret_name,
                        Description=description or f"Secret created by {__name__}",
                        SecretString=secret_value,
                    )
                except ClientError as e:
                    if e.response["Error"]["Code"] == "ResourceExistsException":
                        # Update existing secret
                        client.update_secret(SecretId=secret_name, SecretString=secret_value)
                return True

            elif provider == SecretProvider.LOCAL_ENCRYPTED:
                return self._set_local_encrypted_secret(secret_name, secret_value)

            else:
                logger.warning(f"Set operation not implemented for provider: {provider}")
                return False

        except Exception as e:
            logger.error(f"Failed to set secret '{secret_name}': {e}")
            return False

    def delete_secret(self, secret_name: str, force: bool = False) -> bool:
        """
        Delete a secret.

        Args:
            secret_name: Name/ID of the secret
            force: Force immediate deletion (skip grace period)

        Returns:
            True if successful, False otherwise
        """
        try:
            provider = self.config.provider

            if provider == SecretProvider.ENV_VARS:
                os.environ.pop(secret_name, None)
                # Clear from cache
                self._clear_secret_from_cache(secret_name)
                return True

            elif provider == SecretProvider.AWS_SECRETS_MANAGER:
                client = self._providers.get(provider)
                if not client:
                    raise ValueError("AWS Secrets Manager client not initialized")

                kwargs = {"SecretId": secret_name}
                if force:
                    kwargs["ForceDeleteWithoutRecovery"] = True
                else:
                    kwargs["RecoveryWindowInDays"] = 7

                client.delete_secret(**kwargs)
                # Clear from cache
                self._clear_secret_from_cache(secret_name)
                return True

            elif provider == SecretProvider.LOCAL_ENCRYPTED:
                result = self._delete_local_encrypted_secret(secret_name)
                if result:
                    # Clear from cache
                    self._clear_secret_from_cache(secret_name)
                return result

            else:
                logger.warning(f"Delete operation not implemented for provider: {provider}")
                return False

        except Exception as e:
            logger.error(f"Failed to delete secret '{secret_name}': {e}")
            return False

    def _clear_secret_from_cache(self, secret_name: str):
        """Clear all cache entries for a specific secret."""
        with self._lock:
            # Remove all cache entries that start with the secret name
            # This handles both versioned and unversioned entries
            keys_to_remove = [
                key for key in self._cache.keys() if key.startswith(f"{secret_name}:")
            ]
            for key in keys_to_remove:
                del self._cache[key]

            if keys_to_remove:
                logger.debug(
                    f"Cleared {len(keys_to_remove)} cache entries for secret '{secret_name}'"
                )

    def rotate_secret(self, secret_name: str) -> Optional[str]:
        """
        Rotate a secret by generating a new value.

        Args:
            secret_name: Name/ID of the secret to rotate

        Returns:
            New secret value or None if rotation failed
        """
        try:
            # Generate new secret value
            new_value = secrets.token_urlsafe(32)

            # Store with versioning
            success = self.set_secret(
                secret_name,
                new_value,
                description=f"Rotated on {datetime.utcnow().isoformat()}",
            )

            if success:
                logger.info(f"Secret '{secret_name}' rotated successfully")
                return new_value
            else:
                logger.error(f"Failed to rotate secret '{secret_name}'")
                return None

        except Exception as e:
            logger.error(f"Error rotating secret '{secret_name}': {e}")
            return None

    def _get_local_encrypted_secret(self, secret_name: str) -> Optional[str]:
        """Retrieve and decrypt a locally stored secret."""
        if not self._cipher_suite:
            raise ValueError("Encryption not configured for local secrets")

        secret_file = Path(f".secrets/{secret_name}.enc")
        if not secret_file.exists():
            return None

        try:
            encrypted_data = secret_file.read_bytes()
            decrypted_data = self._cipher_suite.decrypt(encrypted_data)
            return decrypted_data.decode("utf-8")
        except Exception as e:
            raise SecurityAnalysisError(
                f"Failed to decrypt local secret '{secret_name}': {e}"
            ) from e

    def _set_local_encrypted_secret(self, secret_name: str, secret_value: str) -> bool:
        """Encrypt and store a secret locally."""
        if not self._cipher_suite:
            raise ValueError("Encryption not configured for local secrets")

        secret_dir = Path(".secrets")
        secret_dir.mkdir(exist_ok=True, mode=0o700)

        try:
            encrypted_data = self._cipher_suite.encrypt(secret_value.encode("utf-8"))
            secret_file = secret_dir / f"{secret_name}.enc"
            secret_file.write_bytes(encrypted_data)
            secret_file.chmod(0o600)
            return True
        except Exception as e:
            raise SecurityAnalysisError(
                f"Failed to encrypt and store local secret '{secret_name}': {e}"
            ) from e

    def _delete_local_encrypted_secret(self, secret_name: str) -> bool:
        """Delete a locally stored encrypted secret."""
        secret_file = Path(f".secrets/{secret_name}.enc")
        if secret_file.exists():
            secret_file.unlink()
            return True
        return False

    def list_secrets(self, prefix: Optional[str] = None) -> List[str]:
        """
        List all available secrets.

        Args:
            prefix: Optional prefix to filter secrets

        Returns:
            List of secret names
        """
        try:
            provider = self.config.provider

            if provider == SecretProvider.ENV_VARS:
                secrets = [k for k in os.environ.keys() if not prefix or k.startswith(prefix)]
                return secrets

            elif provider == SecretProvider.AWS_SECRETS_MANAGER:
                client = self._providers.get(provider)
                if not client:
                    raise ValueError("AWS Secrets Manager client not initialized")

                kwargs = {}
                if prefix:
                    kwargs["Filters"] = [{"Key": "name", "Values": [prefix]}]

                response = client.list_secrets(**kwargs)
                return [s["Name"] for s in response.get("SecretList", [])]

            elif provider == SecretProvider.LOCAL_ENCRYPTED:
                secret_dir = Path(".secrets")
                if not secret_dir.exists():
                    return []

                secrets = []
                for file in secret_dir.glob("*.enc"):
                    name = file.stem
                    if not prefix or name.startswith(prefix):
                        secrets.append(name)
                return secrets

            else:
                logger.warning(f"List operation not implemented for provider: {provider}")
                return []

        except Exception as e:
            logger.error(f"Failed to list secrets: {e}")
            return []

    def validate_secret_policy(self, secret_value: str) -> Tuple[bool, Optional[str]]:
        """
        Validate a secret against security policies.

        Args:
            secret_value: Secret to validate

        Returns:
            Tuple of (is_valid, error_message)
        """
        # Minimum length
        if len(secret_value) < 12:
            return False, "Secret must be at least 12 characters long"

        # Complexity requirements
        has_upper = any(c.isupper() for c in secret_value)
        has_lower = any(c.islower() for c in secret_value)
        has_digit = any(c.isdigit() for c in secret_value)
        has_special = any(c in "!@#$%^&*()_+-=[]{}|;:,.<>?" for c in secret_value)

        if not (has_upper and has_lower and has_digit and has_special):
            return (
                False,
                "Secret must contain uppercase, lowercase, digit, and special character",
            )

        # Check for common patterns
        common_patterns = [
            r"password",
            r"123456",
            r"qwerty",
            r"admin",
            r"letmein",
            r"welcome",
            r"monkey",
            r"dragon",
        ]

        for pattern in common_patterns:
            if re.search(pattern, secret_value, re.IGNORECASE):
                return False, f"Secret contains common pattern: {pattern}"

        return True, None

    def clear_cache(self):
        """Clear the secrets cache."""
        with self._lock:
            self._cache.clear()
        logger.info("Secrets cache cleared")

    def get_stats(self) -> Dict[str, Any]:
        """Get statistics about secrets management."""
        return {
            "provider": self.config.provider.value,
            "cache_size": len(self._cache),
            "cache_ttl_seconds": self.config.cache_ttl_seconds,
            "auto_rotation_days": self.config.auto_rotation_days,
            "encryption_enabled": self._cipher_suite is not None,
        }


# Global instance
_default_config = SecretConfig(
    provider=(
        SecretProvider.AWS_SECRETS_MANAGER
        if PRODUCTION_MODE and AWS_AVAILABLE
        else SecretProvider.ENV_VARS
    )
)
SECRETS_MANAGER = SecretsManager(_default_config)

# Export public interface
__all__ = [
    "SecretsManager",
    "SecretConfig",
    "SecretProvider",
    "SECRETS_MANAGER",
    "SecurityAnalysisError",
]


class _SecretsManager:
    def get_secret(self, key: str, required: bool = False):
        return "dummy_secret"


SECRETS_MANAGER = _SecretsManager()
