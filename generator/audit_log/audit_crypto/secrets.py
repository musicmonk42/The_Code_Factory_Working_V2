# secrets.py
"""
WARNING: In production, this module will ABORT if no production-grade secret manager is enabled.
Allowed managers for production: AWSSecretsManager, GCPSecretManager, VaultSecretManager.
DO NOT fetch secrets from environment variables, files, or code anywhere else in the application.
All sensitive configurations MUST BE retrieved via the configured SECRET_MANAGER.
"""

import os
import base64
import logging
import time
import asyncio
from typing import Any, Dict, List, Optional, Protocol, Union
from abc import ABC, abstractmethod # For SecretManager ABC
from collections import defaultdict # For rate limiting in MockSecretManager

# Conditional import for AWS Secrets Manager
try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
    HAS_BOTO3 = True
except ImportError:
    HAS_BOTO3 = False
    logging.warning("boto3 not found. AWSSecretsManager will not be available.")

# Conditional imports for GCP Secret Manager
try:
    from google.cloud import secretmanager
    from google.api_core.exceptions import NotFound, GoogleAPIError
    HAS_GCP_SECRET_MANAGER = True
except ImportError:
    HAS_GCP_SECRET_MANAGER = False
    logging.warning("google-cloud-secret-manager not found. GCPSecretManager will not be available.")

# Conditional imports for HashiCorp Vault
try:
    import hvac
    from hvac.exceptions import InvalidRequest, Forbidden
    HAS_HVAC = True
except ImportError:
    HAS_HVAC = False
    logging.warning("hvac not found. VaultSecretManager will not be available.")

# Placeholder for audit_log.log_action for key use auditing
try:
    from audit_log import log_action
except ImportError:
    logging.warning("audit_log.py not found or circular dependency. log_action will be a dummy function.",
                   extra={"operation": "audit_log_import_fail"})
    async def log_action(*args, **kwargs): # Make dummy async to match expected signature
        logging.info(f"Dummy log_action: {args}, {kwargs}", extra={"operation": "dummy_log_action"})

logger = logging.getLogger(__name__)

# --- Custom Exceptions ---
class SecretError(Exception):
    """Base exception for secret management failures."""
    pass

class SecretNotFoundError(SecretError):
    """Exception raised when a requested secret is not found."""
    pass

class SecretDecodingError(SecretError):
    """Exception raised when a secret cannot be decoded (e.g., invalid base64)."""
    pass

class SecretManagerConfigurationError(SecretError):
    """Exception raised when the secret manager itself is misconfigured."""
    pass

class InsecureSecretManagerError(SecretManagerConfigurationError):
    """Exception raised when an insecure secret manager is used in a production context."""
    pass

class SecretAccessRateLimitExceeded(SecretError):
    """Exception raised when secret access attempts exceed the rate limit."""
    pass

# --- Secret Manager Interface/Protocol ---
class SecretManager(ABC):
    """
    Abstract base class defining the interface for a secure secret manager.
    This allows for plug-and-play secret sources (e.g., AWS Secrets Manager, HashiCorp Vault).
    """
    @abstractmethod
    async def get_secret(self, secret_name: str) -> Optional[bytes]:
        """
        Fetches a secret by its name.
        Implementations should handle their own error logging and potentially raise specific exceptions.
        Returns the secret as bytes.
        """
        pass

    @property
    @abstractmethod
    def is_production_ready(self) -> bool:
        """True if this manager meets all production requirements (e.g., uses a secure vault)."""
        pass

# --- Secret Manager Implementations ---

class AWSSecretsManager(SecretManager):
    """
    A SecretManager implementation that fetches secrets from AWS Secrets Manager.
    """
    def __init__(self, region_name: Optional[str] = None):
        if not HAS_BOTO3:
            raise SecretManagerConfigurationError("boto3 library not found, cannot use AWSSecretsManager.")
        self._client = boto3.client('secretsmanager', region_name=region_name)
        self._prod_ready = True
        self.logger = logging.getLogger(f"{__name__}.AWSSecretsManager")

    async def get_secret(self, secret_name: str) -> Optional[bytes]:
        try:
            response = await asyncio.to_thread(self._client.get_secret_value, SecretId=secret_name)

            secret_value: Optional[bytes] = None
            if 'SecretString' in response:
                secret_value = response['SecretString'].encode('utf-8')
            elif 'SecretBinary' in response:
                secret_value = response['SecretBinary']

            if secret_value:
                self.logger.debug(f"Secret '{secret_name}' retrieved from AWS Secrets Manager.")
                await log_action("secret_access", secret_name=secret_name, source="aws_secrets_manager", status="success")
                return secret_value
            else:
                self.logger.warning(f"Secret '{secret_name}' from AWS Secrets Manager is empty or malformed.")
                await log_action("secret_access", secret_name=secret_name, source="aws_secrets_manager", status="empty_or_malformed")
                return None

        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code')
            if error_code == 'ResourceNotFoundException':
                self.logger.warning(f"Secret '{secret_name}' not found in AWS Secrets Manager.",
                                    extra={"operation": "secret_access_not_found", "secret_name": secret_name, "error_code": error_code})
                await log_action("secret_access", secret_name=secret_name, source="aws_secrets_manager", status="not_found", error=str(e))
                raise SecretNotFoundError(f"Secret '{secret_name}' not found.") from e
            else:
                self.logger.error(f"Error retrieving secret '{secret_name}' from AWS Secrets Manager: {e}", exc_info=True,
                                  extra={"operation": "secret_access_client_error", "secret_name": secret_name, "error_code": error_code})
                await log_action("secret_access", secret_name=secret_name, source="aws_secrets_manager", status="error", error=str(e))
                raise SecretError(f"AWS Secrets Manager client error: {e}") from e
        except BotoCoreError as e:
            self.logger.error(f"AWS SDK error retrieving secret '{secret_name}': {e}", exc_info=True,
                              extra={"operation": "secret_access_sdk_error", "secret_name": secret_name})
            await log_action("secret_access", secret_name=secret_name, source="aws_secrets_manager", status="sdk_error", error=str(e))
            raise SecretError(f"AWS SDK error: {e}") from e
        except Exception as e:
            self.logger.critical(f"Unexpected error in AWSSecretsManager for '{secret_name}': {e}", exc_info=True,
                                 extra={"operation": "secret_access_unexpected_error", "secret_name": secret_name})
            await log_action("secret_access", secret_name=secret_name, source="aws_secrets_manager", status="unexpected_error", error=str(e))
            raise SecretError(f"Unexpected error in AWSSecretsManager: {e}") from e

    @property
    def is_production_ready(self) -> bool:
        return self._prod_ready

class GCPSecretManager(SecretManager):
    """
    A SecretManager implementation that fetches secrets from Google Cloud Secret Manager.
    """
    def __init__(self, project_id: Optional[str] = None):
        if not HAS_GCP_SECRET_MANAGER:
            raise SecretManagerConfigurationError("google-cloud-secret-manager library not found, cannot use GCPSecretManager.")
        if not project_id:
            raise SecretManagerConfigurationError("project_id must be provided for GCPSecretManager.")
        self._project_id = project_id
        self._client = secretmanager.SecretManagerServiceClient()
        self._prod_ready = True
        self.logger = logging.getLogger(f"{__name__}.GCPSecretManager")

    async def get_secret(self, secret_name: str) -> Optional[bytes]:
        try:
            name = f"projects/{self._project_id}/secrets/{secret_name}/versions/latest"
            response = await asyncio.to_thread(self._client.access_secret_version, request={"name": name})
            secret_value = response.payload.data

            if secret_value:
                self.logger.debug(f"Secret '{secret_name}' retrieved from GCP Secret Manager.")
                await log_action("secret_access", secret_name=secret_name, source="gcp_secret_manager", status="success")
                return secret_value
            else:
                self.logger.warning(f"Secret '{secret_name}' from GCP Secret Manager is empty.")
                await log_action("secret_access", secret_name=secret_name, source="gcp_secret_manager", status="empty")
                return None
        except NotFound as e:
            self.logger.warning(f"Secret '{secret_name}' not found in GCP Secret Manager.",
                                extra={"operation": "secret_access_not_found", "secret_name": secret_name})
            await log_action("secret_access", secret_name=secret_name, source="gcp_secret_manager", status="not_found", error=str(e))
            raise SecretNotFoundError(f"Secret '{secret_name}' not found.") from e
        except GoogleAPIError as e:
            self.logger.error(f"GCP API error retrieving secret '{secret_name}': {e}", exc_info=True,
                              extra={"operation": "secret_access_api_error", "secret_name": secret_name})
            await log_action("secret_access", secret_name=secret_name, source="gcp_secret_manager", status="api_error", error=str(e))
            raise SecretError(f"GCP Secret Manager API error: {e}") from e
        except Exception as e:
            self.logger.critical(f"Unexpected error in GCPSecretManager for '{secret_name}': {e}", exc_info=True,
                                 extra={"operation": "secret_access_unexpected_error", "secret_name": secret_name})
            await log_action("secret_access", secret_name=secret_name, source="gcp_secret_manager", status="unexpected_error", error=str(e))
            raise SecretError(f"Unexpected error in GCPSecretManager: {e}") from e

    @property
    def is_production_ready(self) -> bool:
        return self._prod_ready

class VaultSecretManager(SecretManager):
    """
    A SecretManager implementation that fetches secrets from HashiCorp Vault.
    """
    def __init__(self, url: str, token: str, mount_point: str = "secret"):
        if not HAS_HVAC:
            raise SecretManagerConfigurationError("hvac library not found, cannot use VaultSecretManager.")
        if not url or not token:
            raise SecretManagerConfigurationError("Vault URL and token must be provided.")
        self._client = hvac.Client(url=url, token=token)
        self._mount_point = mount_point
        self._prod_ready = True
        self.logger = logging.getLogger(f"{__name__}.VaultSecretManager")

    async def get_secret(self, secret_name: str) -> Optional[bytes]:
        try:
            # Vault stores secrets as key-value pairs in a dictionary
            # We assume the secret_name corresponds to a key within a path, e.g., 'path/to/my_secret'
            # and the key within the secret is 'value'
            # This is a simple implementation, more complex paths might be needed.
            response = await asyncio.to_thread(self._client.secrets.kv.v2.read_secret_version, path=secret_name, mount_point=self._mount_point)
            secret_value = response['data']['data'].get('value')
            if secret_value:
                # Vault returns strings, convert to bytes
                secret_value = secret_value.encode('utf-8')
                self.logger.debug(f"Secret '{secret_name}' retrieved from HashiCorp Vault.")
                await log_action("secret_access", secret_name=secret_name, source="vault", status="success")
                return secret_value
            else:
                self.logger.warning(f"Secret '{secret_name}' from Vault is empty.")
                await log_action("secret_access", secret_name=secret_name, source="vault", status="empty")
                return None
        except InvalidRequest as e:
            self.logger.warning(f"Secret '{secret_name}' not found or malformed path in Vault: {e}",
                                extra={"operation": "secret_access_not_found", "secret_name": secret_name})
            await log_action("secret_access", secret_name=secret_name, source="vault", status="not_found", error=str(e))
            raise SecretNotFoundError(f"Secret '{secret_name}' not found.") from e
        except Forbidden as e:
            self.logger.error(f"Permission denied for secret '{secret_name}' in Vault: {e}",
                              extra={"operation": "secret_access_forbidden", "secret_name": secret_name})
            await log_action("secret_access", secret_name=secret_name, source="vault", status="permission_denied", error=str(e))
            raise SecretError(f"Vault permission denied: {e}") from e
        except Exception as e:
            self.logger.critical(f"Unexpected error in VaultSecretManager for '{secret_name}': {e}", exc_info=True,
                                 extra={"operation": "secret_access_unexpected_error", "secret_name": secret_name})
            await log_action("secret_access", secret_name=secret_name, source="vault", status="unexpected_error", error=str(e))
            raise SecretError(f"Unexpected error in VaultSecretManager: {e}") from e

    @property
    def is_production_ready(self) -> bool:
        return self._prod_ready

class DummySecretManager(SecretManager):
    """
    A dummy secret manager for development/testing environments.
    It does not actually fetch secrets and is explicitly NOT production-ready.
    """
    def __init__(self):
        self.logger = logging.getLogger(f"{__name__}.DummySecretManager")

    async def get_secret(self, secret_name: str) -> Optional[bytes]:
        self.logger.warning(f"DummySecretManager: Attempted to fetch secret '{secret_name}'. No secrets available.",
                            extra={"operation": "dummy_secret_access", "secret_name": secret_name})
        await log_action("secret_access", secret_name=secret_name, source="dummy_secret_manager", status="not_available")
        raise SecretNotFoundError(f"Dummy secret manager: secret '{secret_name}' not available.")

    @property
    def is_production_ready(self) -> bool:
        return False

# --- Secret Manager Initialization ---
# This section determines which SecretManager implementation to use based on environment variables.
_secret_manager: SecretManager

# Parse rate limit settings from environment variables with defaults
SECRET_RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("SECRET_RATE_LIMIT_WINDOW_SECONDS", "60"))
SECRET_MAX_ATTEMPTS_PER_WINDOW = int(os.getenv("SECRET_MAX_ATTEMPTS_PER_WINDOW", "10"))
SECRET_BURST_LIMIT = int(os.getenv("SECRET_BURST_LIMIT", "5"))

if os.getenv("USE_AWS_SECRETS", "false").lower() == "true":
    aws_region = os.getenv("AWS_REGION")
    try:
        _secret_manager = AWSSecretsManager(region_name=aws_region)
        logger.info(f"Configured to use AWSSecretsManager in region: {aws_region or 'default'}.")
    except SecretManagerConfigurationError as e:
        logger.critical(f"Failed to configure AWSSecretsManager: {e}. Falling back to DummySecretManager.", exc_info=True)
        _secret_manager = DummySecretManager()
elif os.getenv("USE_GCP_SECRETS", "false").lower() == "true":
    gcp_project_id = os.getenv("GCP_PROJECT_ID")
    try:
        _secret_manager = GCPSecretManager(project_id=gcp_project_id)
        logger.info(f"Configured to use GCPSecretManager in project: {gcp_project_id}.")
    except SecretManagerConfigurationError as e:
        logger.critical(f"Failed to configure GCPSecretManager: {e}. Falling back to DummySecretManager.", exc_info=True)
        _secret_manager = DummySecretManager()
elif os.getenv("USE_HASHICORP_VAULT", "false").lower() == "true":
    vault_url = os.getenv("VAULT_ADDR")
    vault_token = os.getenv("VAULT_TOKEN")
    try:
        _secret_manager = VaultSecretManager(url=vault_url, token=vault_token)
        logger.info(f"Configured to use VaultSecretManager at {vault_url}.")
    except SecretManagerConfigurationError as e:
        logger.critical(f"Failed to configure VaultSecretManager: {e}. Falling back to DummySecretManager.", exc_info=True)
        _secret_manager = DummySecretManager()
else:
    # This path is for local development/testing where a real secret manager might not be configured.
    _secret_manager = DummySecretManager()
    logger.warning("No production-ready secret manager explicitly configured. Using DummySecretManager. THIS IS NOT FOR PRODUCTION!")

# --- Production Guardrail with Dev Mode Bypass ---
# Check for explicit dev mode bypass
DEV_MODE_BYPASS = os.getenv("AUDIT_DEV_MODE_ALLOW_INSECURE_SECRETS", "false").lower() == "true"
PYTHON_ENV = os.getenv("PYTHON_ENV", "development").lower()

if PYTHON_ENV == "production" and not _secret_manager.is_production_ready:
    if DEV_MODE_BYPASS:
        logger.warning("INSECURE SECRET MANAGER WARNING: Running in 'production' environment but using insecure secret manager due to AUDIT_DEV_MODE_ALLOW_INSECURE_SECRETS=true.")
    else:
        error_msg = "CRITICAL: No production-ready secret manager configured for production environment. Aborting startup."
        logger.critical(error_msg, extra={"operation": "startup_abort", "reason": "insecure_secret_manager"})
        raise InsecureSecretManagerError(error_msg)

# Rate limiting settings for secret access attempts
_SECRET_ACCESS_ATTEMPTS: Dict[str, List[float]] = defaultdict(list) # {secret_name: [timestamp1, timestamp2, ...]}
RETRY_DELAY_SECONDS = 0.5 # Initial delay for retries

async def _get_secret_with_retries_and_rate_limit(secret_name: str, max_retries: int = 3, initial_delay: float = RETRY_DELAY_SECONDS) -> Optional[bytes]:
    """
    Internal helper to fetch a secret with rate limiting and exponential backoff retries.
    Calls the currently configured global _secret_manager.
    """
    current_time = time.time()
    
    # Clean up old timestamps (older than the configured window)
    _SECRET_ACCESS_ATTEMPTS[secret_name] = [t for t in _SECRET_ACCESS_ATTEMPTS[secret_name] if t > current_time - SECRET_RATE_LIMIT_WINDOW_SECONDS]
    _SECRET_ACCESS_ATTEMPTS[secret_name].append(current_time)
    
    # --- FIX 1: Changed >= to > for correct rate limit logic ---
    # This allows 5 attempts (if limit=5) and fails on the 6th.
    if len(_SECRET_ACCESS_ATTEMPTS[secret_name]) > SECRET_MAX_ATTEMPTS_PER_WINDOW + SECRET_BURST_LIMIT:
        logger.warning(f"Rate limit exceeded for secret '{secret_name}'. Too many access attempts.",
                       extra={"operation": "secret_access_rate_limit", "secret_name": secret_name})
        await log_action("secret_access", secret_name=secret_name, status="rate_limited", reason="too_many_attempts")
        raise SecretAccessRateLimitExceeded(f"Rate limit exceeded for secret '{secret_name}'.")

    attempt = 0
    while attempt < max_retries:
        try:
            secret_value = await _secret_manager.get_secret(secret_name)
            return secret_value
        # --- FIX 2: Swapped SecretError and Exception blocks ---
        # This ensures SecretError (and its children) are retried.
        except SecretError as e: # Catch custom SecretErrors for retries
            attempt += 1
            if attempt >= max_retries:
                logger.error(f"Failed to retrieve secret '{secret_name}' after {max_retries} retries: {e}", exc_info=True,
                             extra={"operation": "secret_access_retry_fail", "secret_name": secret_name})
                await log_action("secret_access", secret_name=secret_name, status="failed_after_retries", error=str(e))
                raise SecretError(f"Failed to retrieve secret '{secret_name}': {e}") from e
            
            delay = initial_delay * (2 ** (attempt - 1))
            logger.warning(f"Transient error retrieving secret '{secret_name}' (attempt {attempt}/{max_retries}). Retrying in {delay:.2f} seconds. Error: {e}")
            await log_action("secret_access", secret_name=secret_name, status="retry_attempt", attempt=attempt, error=str(e))
            await asyncio.sleep(delay)
        except Exception as e: # Catch any other unexpected exceptions
            logger.critical(f"Unexpected error during secret retrieval for '{secret_name}': {e}", exc_info=True)
            await log_action("secret_access", secret_name=secret_name, status="unexpected_error", error=str(e))
            raise SecretError(f"Unexpected error during secret retrieval: {e}") from e
    return None # Should not be reached if max_retries > 0 and no re-raise on final attempt

# --- Public Async Functions ---
async def aget_hsm_pin() -> str:
    """
    Async version to fetch the HSM PIN securely from the configured secret manager.
    Raises SecretError if the PIN cannot be retrieved.
    """
    try:
        hsm_pin_bytes = await _get_secret_with_retries_and_rate_limit("AUDIT_CRYPTO_HSM_PIN")
        if not hsm_pin_bytes:
            logger.critical("HSM PIN (AUDIT_CRYPTO_HSM_PIN) is missing or could not be retrieved. HSM operations will fail.")
            raise SecretNotFoundError("HSM PIN not found or accessible.")
        logger.debug("HSM PIN successfully retrieved.")
        return hsm_pin_bytes.decode('utf-8')
    # --- FIX: Swapped SecretError and Exception blocks ---
    except SecretError as e:
        logger.critical(f"Failed to get HSM PIN: {e}. HSM operations will fail.", exc_info=True)
        raise ValueError(f"HSM PIN not found or accessible: {e}") from e
    except Exception as e:
        logger.critical(f"Unexpected error when fetching HSM PIN: {e}. HSM operations will fail.", exc_info=True)
        raise SecretError(f"Unexpected error fetching HSM PIN: {e}") from e

async def aget_fallback_hmac_secret() -> Optional[bytes]:
    """
    Async version to fetch the fallback HMAC secret securely from the configured secret manager.
    Returns None if the secret is not found or cannot be decoded.
    """
    # --- FIX 3: Removed outer try/except block to allow exceptions to propagate ---
    fallback_secret_bytes = await _get_secret_with_retries_and_rate_limit("AUDIT_CRYPTO_FALLBACK_HMAC_SECRET_B64")
    if not fallback_secret_bytes:
        logger.warning("Fallback HMAC secret (AUDIT_CRYPTO_FALLBACK_HMAC_SECRET_B64) is missing. Fallback signing will be disabled.")
        return None

    try:
        secret_bytes = base64.b64decode(fallback_secret_bytes)
        if len(secret_bytes) < 16: # Ensure a reasonable minimum length for HMAC
            logger.error("Decoded fallback HMAC secret is too short. Must be at least 16 bytes.")
            await log_action("secret_access", secret_name="AUDIT_CRYPTO_FALLBACK_HMAC_SECRET_B64", status="decoding_error", reason="too_short")
            raise SecretDecodingError("Decoded fallback HMAC secret is too short.")
        logger.debug("Fallback HMAC secret successfully retrieved and decoded.")
        return secret_bytes
    except Exception as e:
        logger.critical(f"Failed to decode FALLBACK_HMAC_SECRET_B64: {e}. Fallback HMAC will be disabled.", exc_info=True)
        await log_action("secret_access", secret_name="AUDIT_CRYPTO_FALLBACK_HMAC_SECRET_B64", status="decoding_error", error=str(e))
        raise SecretDecodingError(f"Failed to decode FALLBACK_HMAC_SECRET_B64: {e}") from e

async def aget_kms_master_key_ciphertext_blob() -> bytes:
    """
    Async version to fetch the KMS-encrypted ciphertext blob for the software key master securely
    from the configured secret manager.
    Raises SecretError if the blob cannot be retrieved or decoded.
    """
    # --- FIX 3: Removed outer try/except block to allow exceptions to propagate ---
    encrypted_data_key_bytes = await _get_secret_with_retries_and_rate_limit("AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64")
    if not encrypted_data_key_bytes:
        logger.critical("Software key master encryption key (AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64) is missing or could not be retrieved.")
        raise SecretNotFoundError("Software key master encryption key not found or accessible.")

    try:
        ciphertext_blob = base64.b64decode(encrypted_data_key_bytes)
        logger.debug("KMS master key ciphertext blob successfully retrieved.")
        return ciphertext_blob
    except Exception as e:
        logger.critical(f"Failed to base64 decode KMS master key ciphertext: {e}.", exc_info=True)
        await log_action("secret_access", secret_name="AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64", status="decoding_error", error=str(e))
        raise SecretDecodingError(f"Invalid base64 encoding for KMS master key ciphertext: {e}") from e

# --- Public Synchronous Functions (for compatibility) ---
def get_hsm_pin() -> str:
    """
    Fetches the HSM PIN securely from the configured secret manager.
    Raises SecretError if the PIN cannot be retrieved.
    NOTE: This is a synchronous wrapper. For async contexts, use aget_hsm_pin.
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            raise SecretError("Cannot call sync get_hsm_pin from an async context. Use aget_hsm_pin instead.")
        return loop.run_until_complete(aget_hsm_pin())
    # --- FIX 4: Only catch RuntimeError, let SecretError propagate ---
    except RuntimeError as e:
        logger.critical(f"Error in sync get_hsm_pin (no event loop?): {e}", exc_info=True)
        raise SecretError(f"Failed to run async get_hsm_pin: {e}") from e

def get_fallback_hmac_secret() -> Optional[bytes]:
    """
    Fetches the fallback HMAC secret securely from the configured secret manager.
    Returns None if the secret is not found or cannot be decoded.
    NOTE: This is a synchronous wrapper. For async contexts, use aget_fallback_hmac_secret.
    """
    # --- FIX 4: Refactored to separate guardrail from execution ---
    # This prevents the `except SecretError` from catching its own guardrail raise.
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError as e:
        logger.critical(f"Error in sync get_fallback_hmac_secret (no event loop?): {e}", exc_info=True)
        return None

    if loop.is_running():
        raise SecretError("Cannot call sync get_fallback_hmac_secret from an async context. Use aget_fallback_hmac_secret instead.")

    try:
        return loop.run_until_complete(aget_fallback_hmac_secret())
    except SecretError as e:
        # Catch SecretError from the async function and log it, but return None
        logger.critical(f"Error in sync get_fallback_hmac_secret: {e}", exc_info=True)
        return None


def get_kms_master_key_ciphertext_blob() -> bytes:
    """
    Fetches the KMS-encrypted ciphertext blob for the software key master securely
    from the configured secret manager.
    Raises SecretError if the blob cannot be retrieved or decoded.
    NOTE: This is a synchronous wrapper. For async contexts, use aget_kms_master_key_ciphertext_blob.
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            raise SecretError("Cannot call sync get_kms_master_key_ciphertext_blob from an async context. Use aget_kms_master_key_ciphertext_blob instead.")
        return loop.run_until_complete(aget_kms_master_key_ciphertext_blob())
    # --- FIX 4: Only catch RuntimeError, let SecretError propagate ---
    except RuntimeError as e:
        logger.critical(f"Error in sync get_kms_master_key_ciphertext_blob (no event loop?): {e}", exc_info=True)
        raise SecretError(f"Failed to run async get_kms_master_key_ciphertext_blob: {e}") from e