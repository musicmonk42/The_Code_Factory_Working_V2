# D:\SFE\self_fixing_engineer\arbiter\learner\encryption.py
import base64
import json
import os
from typing import Any, Dict, Optional

import boto3
import structlog
from botocore.exceptions import ClientError, NoCredentialsError
from cryptography.fernet import Fernet, InvalidToken
from prometheus_client import Counter

logger = structlog.get_logger(__name__)

# Prometheus metrics
key_rotation_counter = Counter(
    "key_rotation_total", "Total number of key rotation events", ["version"]
)
learn_error_counter = Counter(
    "arbiter_learn_errors_total",
    "Total number of learning errors",
    ["domain", "error_type"],
)


class ArbiterConfig:
    """Configuration for encryption and domain settings."""

    ENCRYPTION_KEYS = {}
    VALID_DOMAIN_PATTERN = r"^[A-Za-z0-9_.-]+$"
    DEFAULT_SCHEMA_DIR = os.path.join(os.path.dirname(__file__), "../schemas")
    ENCRYPTED_DOMAINS = ["FinancialData", "PersonalData", "SecretProject"]
    KNOWLEDGE_REDIS_TTL_SECONDS = 3600
    MAX_LEARN_RETRIES = 3
    SELF_AUDIT_INTERVAL_SECONDS = 3600
    JIRA_URL: Optional[str] = os.getenv("JIRA_URL", "https://jira.example.com")
    JIRA_USER: Optional[str] = os.getenv("JIRA_USER", "admin")
    JIRA_PASSWORD: Optional[str] = os.getenv("JIRA_PASSWORD", "dummy_password")
    NEO4J_URL: Optional[str] = os.getenv("NEO4J_URL", "bolt://localhost:7687")
    NEO4J_USER: Optional[str] = os.getenv("NEO4J_USER", "neo4j")
    NEO4J_PASSWORD: Optional[str] = os.getenv("NEO4J_PASSWORD", "dummy_password")
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "openai")
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "dummy_llm_key")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "gpt-4o-mini")
    PLUGINS_ENABLED: bool = True

    @classmethod
    def load_keys(cls):
        """
        Dynamically loads encryption keys from AWS SSM Parameter Store.
        Falls back to a single, hardcoded key if SSM is not configured.
        """
        keys_to_load = os.getenv("ENCRYPTION_KEY_VERSIONS", "v1").split(",")
        ssm_client = None

        # Only try to use SSM if AWS_REGION is set
        aws_region = os.getenv("AWS_REGION")
        if aws_region:
            try:
                ssm_client = boto3.client("ssm", region_name=aws_region)
                for version in keys_to_load:
                    key_param_name = os.getenv(f"ENCRYPTION_KEY_{version.upper()}_PATH")
                    if key_param_name:
                        response = ssm_client.get_parameter(
                            Name=key_param_name, WithDecryption=True
                        )
                        key_value = response["Parameter"]["Value"]
                        cls.ENCRYPTION_KEYS[version] = Fernet(key_value.encode("utf-8"))
                        logger.info(
                            "Loaded encryption key from SSM",
                            version=version,
                            key_param_name=key_param_name,
                        )
                    else:
                        logger.warning(
                            "SSM parameter path not set for key version",
                            version=version,
                        )

                if not cls.ENCRYPTION_KEYS:
                    raise ValueError("No encryption keys loaded from SSM.")

            except (NoCredentialsError, ClientError, ValueError) as e:
                logger.error(
                    "Failed to load keys from AWS SSM. Falling back to in-memory key.",
                    error=str(e),
                    exc_info=True,
                )
                # Fallback to a single, in-memory key if SSM fails
                fallback_key = os.getenv("FALLBACK_ENCRYPTION_KEY")
                if fallback_key:
                    cls.ENCRYPTION_KEYS = {"v1": Fernet(fallback_key.encode("utf-8"))}
                else:
                    # Generate a key for development if none exists
                    cls.ENCRYPTION_KEYS = {"v1": Fernet(Fernet.generate_key())}
        else:
            # No AWS region set, skip SSM and use fallback
            logger.info("AWS_REGION not set, using fallback encryption key")
            fallback_key = os.getenv("FALLBACK_ENCRYPTION_KEY")
            if fallback_key:
                cls.ENCRYPTION_KEYS = {"v1": Fernet(fallback_key.encode("utf-8"))}
            else:
                # Generate a key for development if none exists
                cls.ENCRYPTION_KEYS = {"v1": Fernet(Fernet.generate_key())}

        return cls.ENCRYPTION_KEYS

    @classmethod
    async def rotate_keys(cls, new_version: str):
        """
        Rotates encryption keys by adding a new one.
        In a real-world scenario, this would also involve persisting the new key
        to a secrets manager and updating old keys.
        """
        new_key = Fernet.generate_key()
        # FIX: Store as Fernet instance, not raw bytes
        cls.ENCRYPTION_KEYS[new_version] = Fernet(new_key)
        key_rotation_counter.labels(version=new_version).inc()
        logger.info("Encryption key rotated", new_version=new_version)

        # IMPLEMENTED: Persist the new key to SSM Parameter Store
        try:
            cls._persist_key_to_ssm(new_version, new_key)
            logger.info(
                "Successfully persisted rotated key to SSM", version=new_version
            )
        except Exception as e:
            logger.error(
                "Failed to persist rotated key to SSM. Key is still usable in memory.",
                version=new_version,
                error=str(e),
                exc_info=True,
            )

        # Optionally delete oldest key if we have too many versions
        # Keep at least 2 versions for graceful rotation
        if len(cls.ENCRYPTION_KEYS) > 5:
            oldest_version = min(cls.ENCRYPTION_KEYS.keys())
            try:
                cls._delete_key_from_ssm(oldest_version)
                del cls.ENCRYPTION_KEYS[oldest_version]
                logger.info(
                    "Deleted oldest encryption key",
                    version=oldest_version,
                    remaining_versions=list(cls.ENCRYPTION_KEYS.keys()),
                )
            except Exception as e:
                logger.warning(
                    "Failed to delete oldest key from SSM",
                    version=oldest_version,
                    error=str(e),
                )

    @classmethod
    def _persist_key_to_ssm(cls, version: str, key: bytes):
        """
        Persist an encryption key to AWS SSM Parameter Store.

        Args:
            version: Key version identifier (e.g., "v2", "v3")
            key: Fernet encryption key as bytes

        Raises:
            ClientError: If SSM operation fails
            NoCredentialsError: If AWS credentials are not configured
        """
        try:
            ssm_client = boto3.client(
                "ssm", region_name=os.getenv("AWS_REGION", "us-east-1")
            )

            parameter_name = f"/arbiter/encryption_keys/{version}"

            # Fernet.generate_key() returns URL-safe base64-encoded bytes (44 chars)
            # Decode to ASCII string for SSM storage
            key_str = key.decode("ascii")

            # Store as SecureString for added security
            ssm_client.put_parameter(
                Name=parameter_name,
                Value=key_str,
                Type="SecureString",
                Overwrite=True,
                Description=f"Arbiter encryption key version {version}",
                Tags=[
                    {"Key": "Component", "Value": "Arbiter"},
                    {"Key": "Purpose", "Value": "Encryption"},
                    {"Key": "Version", "Value": version},
                ],
            )

            logger.info(
                "Persisted encryption key to SSM",
                parameter=parameter_name,
                version=version,
            )
        except NoCredentialsError:
            logger.warning(
                "AWS credentials not configured. Key persisted in memory only.",
                version=version,
            )
            raise
        except ClientError as e:
            logger.error(
                "Failed to persist key to SSM",
                version=version,
                error=str(e),
                exc_info=True,
            )
            raise

    @classmethod
    def _delete_key_from_ssm(cls, version: str):
        """
        Delete an encryption key from AWS SSM Parameter Store.

        Args:
            version: Key version identifier to delete

        Raises:
            ClientError: If SSM operation fails
        """
        try:
            ssm_client = boto3.client(
                "ssm", region_name=os.getenv("AWS_REGION", "us-east-1")
            )

            parameter_name = f"/arbiter/encryption_keys/{version}"
            ssm_client.delete_parameter(Name=parameter_name)

            logger.info(
                "Deleted encryption key from SSM",
                parameter=parameter_name,
                version=version,
            )
        except ClientError as e:
            # Parameter might not exist, which is acceptable
            if e.response["Error"]["Code"] != "ParameterNotFound":
                logger.error(
                    "Failed to delete key from SSM", version=version, error=str(e)
                )
                raise


# Load keys on module import
ArbiterConfig.load_keys()


async def encrypt_value(value: Any, cipher: Fernet, key_id: str = "v1") -> bytes:
    """
    Encrypt a value with a specific key version.
    Args:
        value: Data to encrypt.
        cipher: Fernet cipher instance.
        key_id: Encryption key version (default: "v1").
    Returns:
        Encrypted bytes with key ID prefix.
    Raises:
        ValueError: If serialization or encryption fails.
    """
    try:
        serialized = json.dumps(value, default=str).encode("utf-8")
        encrypted = cipher.encrypt(serialized)
        return f"{key_id}:".encode("utf-8") + encrypted
    except Exception as e:
        learn_error_counter.labels(
            domain="encryption", error_type="serialization_failed"
        ).inc()
        logger.error("Failed to encrypt value", error=str(e), exc_info=True)
        raise ValueError(f"Failed to encrypt value: {e}") from e


async def decrypt_value(encrypted: bytes, ciphers: Dict[str, Fernet]) -> Any:
    """
    Decrypt a value, handling key versions.
    Args:
        encrypted: Encrypted bytes with key ID prefix.
        ciphers: Dictionary of Fernet ciphers by key ID.
    Returns:
        Decrypted value.
    Raises:
        InvalidToken: If decryption fails.
        TypeError: If input is not bytes.
    """
    if not isinstance(encrypted, bytes):
        raise TypeError(f"Expected bytes, got {type(encrypted)}")

    try:
        parts = encrypted.split(b":", 1)
        key_id, encrypted_data = (
            (parts[0].decode("utf-8"), parts[1])
            if len(parts) == 2
            else ("v1", encrypted)
        )
        cipher = ciphers.get(key_id)

        # FIX: Check for unknown key ID before attempting decryption
        if not cipher:
            logger.error("Unknown encryption key ID", key_id=key_id)
            learn_error_counter.labels(
                domain="decryption", error_type="unknown_key_id"
            ).inc()
            raise InvalidToken(f"Unknown encryption key ID: {key_id}")

        # Try to decrypt
        try:
            decrypted_data = cipher.decrypt(encrypted_data)
            return json.loads(decrypted_data.decode("utf-8"))
        except InvalidToken:
            # This is a genuine invalid token error (bad data, wrong key, etc.)
            learn_error_counter.labels(
                domain="decryption", error_type="invalid_token"
            ).inc()
            raise
        except json.JSONDecodeError as e:
            # Deserialization failed
            logger.error("JSON deserialization failed", error=str(e), exc_info=True)
            learn_error_counter.labels(
                domain="decryption", error_type="deserialization_failed"
            ).inc()
            raise InvalidToken(f"Decryption or deserialization failed: {e}") from e

    except InvalidToken:
        # Re-raise InvalidToken exceptions (already logged)
        raise
    except Exception as e:
        # Unexpected error
        logger.error("Unexpected error during decryption", error=str(e), exc_info=True)
        learn_error_counter.labels(
            domain="decryption", error_type="deserialization_failed"
        ).inc()
        raise InvalidToken(f"Decryption or deserialization failed: {e}") from e
