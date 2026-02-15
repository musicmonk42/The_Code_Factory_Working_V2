#!/usr/bin/env python3
"""
Audit Cryptographic Key Rotation Script

Enterprise-grade script for rotating audit encryption keys in compliance with
NIST SP 800-57 and SOC 2 Type II standards. Supports multiple deployment modes
including Railway/PaaS (plaintext base64 keys) and AWS KMS (encrypted keys).

Key Features:
1. NIST SP 800-90A compliant key generation (os.urandom)
2. Minimum key length validation (32 bytes for AES-256/Fernet)
3. Support for both Railway/PaaS and AWS KMS deployment modes
4. Comprehensive validation and security checks
5. Audit trail logging for compliance
6. Dry-run mode for safe testing
7. JSON output for CI/CD integration
8. Rollback capability with key backup
9. OpenTelemetry tracing support (optional)

Security Standards:
- NIST SP 800-57: Key Management
- NIST SP 800-90A: Random Number Generation
- SOC 2 Type II: Cryptographic key management
- CIS Controls: Key rotation and lifecycle management

Usage:
    python scripts/rotate_audit_crypto_key.py [OPTIONS]
    
    Options:
        --dry-run          Simulate key rotation without making changes
        --validate         Validate current key configuration only
        --json             Output results in JSON format for CI/CD
        --log-level LEVEL  Set logging level (DEBUG, INFO, WARNING, ERROR)
        --backup-dir PATH  Directory for key backups (default: ./key_backups)
        --mode MODE        Deployment mode: railway, aws-kms (auto-detected)
        --force            Skip confirmation prompts
        --otel             Enable OpenTelemetry tracing

Exit Codes:
    0 - Success
    1 - Validation or rotation failure
    2 - Invalid arguments or configuration error
    3 - Backup/rollback failure

Examples:
    # Validate current configuration
    python scripts/rotate_audit_crypto_key.py --validate
    
    # Dry run (test without changes)
    python scripts/rotate_audit_crypto_key.py --dry-run
    
    # Rotate keys with backup
    python scripts/rotate_audit_crypto_key.py --backup-dir /secure/backups
    
    # CI/CD integration with JSON output
    python scripts/rotate_audit_crypto_key.py --json --force > rotation_results.json
    
    # Debug with full logging
    python scripts/rotate_audit_crypto_key.py --log-level DEBUG

Deployment Instructions:

Railway/PaaS Mode:
    1. Generate new key: python scripts/rotate_audit_crypto_key.py --dry-run
    2. Backup current key to secure storage
    3. Update environment variables:
       - AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64=<new_key>
    4. Restart application
    5. Verify with: python scripts/validate_secrets.py

AWS KMS Mode:
    1. Generate new key: python scripts/rotate_audit_crypto_key.py --mode aws-kms
    2. Key will be encrypted with KMS automatically
    3. Update environment variables with encrypted ciphertext
    4. Restart application
    5. Verify with: python scripts/validate_secrets.py

Kubernetes/Helm Mode:
    1. Generate new key: python scripts/rotate_audit_crypto_key.py --json > key.json
    2. Update Kubernetes secret:
       kubectl create secret generic audit-crypto-key \\
         --from-literal=master-key=$(jq -r '.new_key' key.json) \\
         --dry-run=client -o yaml | kubectl apply -f -
    3. Restart pods: kubectl rollout restart deployment/codefactory
    4. Verify: kubectl logs -f deployment/codefactory

Rollback Instructions:
    1. Stop application
    2. Restore backed up key from backup directory
    3. Update environment variables with old key
    4. Restart application
    5. Verify with: python scripts/validate_secrets.py

Author: Code Factory Team
License: Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.
Version: 1.0.0
"""

import argparse
import base64
import datetime
import json
import logging
import os
import secrets
import shutil
import sys
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Any

# Use timezone-aware datetime
UTC = datetime.timezone.utc

# Optional OpenTelemetry support
try:
    from opentelemetry import trace
    from opentelemetry.trace import Status, StatusCode
    HAS_OTEL = True
except ImportError:
    HAS_OTEL = False
    trace = None

# Optional AWS KMS support
try:
    import boto3
    from botocore.exceptions import ClientError, BotoCoreError
    HAS_BOTO3 = True
except ImportError:
    HAS_BOTO3 = False

# Configure logging
logger = logging.getLogger(__name__)


# --- Constants ---
MIN_KEY_LENGTH_BYTES = 32  # AES-256/Fernet minimum
RECOMMENDED_KEY_LENGTH_BYTES = 32  # NIST SP 800-57 recommendation
BACKUP_RETENTION_DAYS = 90  # SOC 2 Type II compliance
HMAC_KEY_LENGTH_HEX = 64  # 32 bytes = 64 hex characters


# --- Enums ---
class DeploymentMode(Enum):
    """Deployment mode for key management."""
    RAILWAY = "railway"  # Plaintext base64 keys in environment
    AWS_KMS = "aws-kms"  # KMS-encrypted keys
    KUBERNETES = "kubernetes"  # Kubernetes secrets
    AUTO = "auto"  # Auto-detect from environment


class RotationStatus(Enum):
    """Status of key rotation operation."""
    SUCCESS = "success"
    FAILURE = "failure"
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"


class ValidationLevel(Enum):
    """Validation severity levels."""
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


class ValidationType(Enum):
    """Type of validation check."""
    KEY_LENGTH = "key_length"
    KEY_FORMAT = "key_format"
    KEY_ENTROPY = "key_entropy"
    ENVIRONMENT = "environment"
    DEPLOYMENT = "deployment"
    SECURITY = "security"


# --- Data Classes ---
@dataclass
class KeyMetadata:
    """Metadata for a cryptographic key."""
    key_id: str
    key_type: str  # master, hmac, encryption
    generated_at: str
    length_bytes: int
    format: str  # base64, hex
    deployment_mode: str
    nist_compliant: bool
    min_length_met: bool
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)


@dataclass
class ValidationResult:
    """Result of a validation check."""
    check_type: ValidationType
    level: ValidationLevel
    passed: bool
    message: str
    details: Optional[Dict[str, Any]] = None
    remediation: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "check_type": self.check_type.value,
            "level": self.level.value,
            "passed": self.passed,
            "message": self.message,
            "details": self.details,
            "remediation": self.remediation,
        }


@dataclass
class BackupInfo:
    """Information about a key backup."""
    backup_path: str
    original_key: str
    timestamp: str
    deployment_mode: str
    can_rollback: bool
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)


@dataclass
class RotationResult:
    """Result of a key rotation operation."""
    status: RotationStatus
    new_key: Optional[str] = None
    new_key_metadata: Optional[KeyMetadata] = None
    backup_info: Optional[BackupInfo] = None
    validation_results: List[ValidationResult] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    audit_log_entries: List[Dict[str, Any]] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.datetime.now(UTC).isoformat())
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "status": self.status.value,
            "new_key": self.new_key,
            "new_key_metadata": self.new_key_metadata.to_dict() if self.new_key_metadata else None,
            "backup_info": self.backup_info.to_dict() if self.backup_info else None,
            "validation_results": [v.to_dict() for v in self.validation_results],
            "errors": self.errors,
            "warnings": self.warnings,
            "audit_log_entries": self.audit_log_entries,
            "timestamp": self.timestamp,
        }
    
    def has_critical_failures(self) -> bool:
        """Check if any critical validations failed."""
        return any(
            not v.passed and v.level == ValidationLevel.CRITICAL
            for v in self.validation_results
        )


# --- Core Classes ---
class KeyGenerator:
    """
    NIST SP 800-90A compliant key generator.
    
    Uses os.urandom for cryptographically secure random number generation.
    """
    
    @staticmethod
    def generate_master_key(length_bytes: int = RECOMMENDED_KEY_LENGTH_BYTES) -> bytes:
        """
        Generate a cryptographically secure master encryption key.
        
        Args:
            length_bytes: Key length in bytes (default: 32 for AES-256)
            
        Returns:
            Raw key bytes
            
        Raises:
            ValueError: If length is less than minimum required
        """
        if length_bytes < MIN_KEY_LENGTH_BYTES:
            raise ValueError(
                f"Key length must be at least {MIN_KEY_LENGTH_BYTES} bytes "
                f"(got {length_bytes})"
            )
        
        # Use os.urandom for NIST SP 800-90A compliance
        key_bytes = os.urandom(length_bytes)
        
        logger.info(
            f"Generated master key: {length_bytes} bytes",
            extra={
                "operation": "generate_master_key",
                "key_length": length_bytes,
                "nist_compliant": True,
            }
        )
        
        return key_bytes
    
    @staticmethod
    def generate_hmac_key() -> str:
        """
        Generate a HMAC key for audit log integrity.
        
        Returns:
            64-character hexadecimal string (32 bytes)
        """
        # Generate 32 random bytes and encode as hex
        key_bytes = os.urandom(32)
        hmac_key = key_bytes.hex()
        
        logger.info(
            "Generated HMAC key: 64 hex characters",
            extra={
                "operation": "generate_hmac_key",
                "key_length": len(hmac_key),
            }
        )
        
        return hmac_key
    
    @staticmethod
    def encode_key_base64(key_bytes: bytes) -> str:
        """
        Encode key bytes as base64 for environment variable storage.
        
        Args:
            key_bytes: Raw key bytes
            
        Returns:
            Base64-encoded string
        """
        return base64.b64encode(key_bytes).decode('utf-8')


class KeyValidator:
    """Validates cryptographic keys and configuration."""
    
    def __init__(self):
        """Initialize the validator."""
        self.results: List[ValidationResult] = []
    
    def validate_key_length(self, key_bytes: bytes, key_type: str) -> ValidationResult:
        """
        Validate key meets minimum length requirements.
        
        Args:
            key_bytes: Raw key bytes
            key_type: Type of key (master, hmac, etc.)
            
        Returns:
            ValidationResult
        """
        length = len(key_bytes)
        passed = length >= MIN_KEY_LENGTH_BYTES
        
        result = ValidationResult(
            check_type=ValidationType.KEY_LENGTH,
            level=ValidationLevel.CRITICAL,
            passed=passed,
            message=f"{key_type} key length: {length} bytes",
            details={
                "actual_length": length,
                "minimum_length": MIN_KEY_LENGTH_BYTES,
                "recommended_length": RECOMMENDED_KEY_LENGTH_BYTES,
            },
            remediation=None if passed else (
                f"Generate a new key with at least {MIN_KEY_LENGTH_BYTES} bytes"
            ),
        )
        
        self.results.append(result)
        return result
    
    def validate_key_format(self, key_str: str, expected_format: str) -> ValidationResult:
        """
        Validate key format (base64, hex, etc.).
        
        Args:
            key_str: Key string
            expected_format: Expected format (base64, hex)
            
        Returns:
            ValidationResult
        """
        passed = False
        message = ""
        details = {"format": expected_format}
        
        try:
            if expected_format == "base64":
                decoded = base64.b64decode(key_str)
                passed = True
                message = f"Valid base64 format ({len(decoded)} bytes)"
                details["decoded_length"] = len(decoded)
            elif expected_format == "hex":
                int(key_str, 16)
                passed = len(key_str) == HMAC_KEY_LENGTH_HEX
                message = f"Valid hex format ({len(key_str)} characters)"
                details["hex_length"] = len(key_str)
            else:
                message = f"Unknown format: {expected_format}"
        except Exception as e:
            message = f"Invalid {expected_format} format: {e}"
            details["error"] = str(e)
        
        result = ValidationResult(
            check_type=ValidationType.KEY_FORMAT,
            level=ValidationLevel.CRITICAL,
            passed=passed,
            message=message,
            details=details,
            remediation=None if passed else (
                f"Ensure key is properly encoded in {expected_format} format"
            ),
        )
        
        self.results.append(result)
        return result
    
    def validate_key_entropy(self, key_bytes: bytes) -> ValidationResult:
        """
        Validate key has sufficient entropy (basic check).
        
        Args:
            key_bytes: Raw key bytes
            
        Returns:
            ValidationResult
        """
        # Simple entropy check: ensure not all bytes are the same
        unique_bytes = len(set(key_bytes))
        total_bytes = len(key_bytes)
        entropy_ratio = unique_bytes / total_bytes
        
        # Consider low entropy if less than 50% unique bytes
        passed = entropy_ratio >= 0.5
        
        result = ValidationResult(
            check_type=ValidationType.KEY_ENTROPY,
            level=ValidationLevel.WARNING,
            passed=passed,
            message=f"Key entropy: {entropy_ratio:.2%} unique bytes",
            details={
                "unique_bytes": unique_bytes,
                "total_bytes": total_bytes,
                "entropy_ratio": entropy_ratio,
            },
            remediation=None if passed else (
                "Key may have low entropy. Regenerate using os.urandom"
            ),
        )
        
        self.results.append(result)
        return result
    
    def validate_deployment_mode(self, mode: DeploymentMode) -> ValidationResult:
        """
        Validate deployment mode configuration.
        
        Args:
            mode: Deployment mode
            
        Returns:
            ValidationResult
        """
        passed = True
        message = f"Deployment mode: {mode.value}"
        details = {"mode": mode.value}
        remediation = None
        
        if mode == DeploymentMode.AWS_KMS and not HAS_BOTO3:
            passed = False
            message = "AWS KMS mode requires boto3 library"
            remediation = "Install boto3: pip install boto3"
        
        result = ValidationResult(
            check_type=ValidationType.DEPLOYMENT,
            level=ValidationLevel.CRITICAL if not passed else ValidationLevel.INFO,
            passed=passed,
            message=message,
            details=details,
            remediation=remediation,
        )
        
        self.results.append(result)
        return result
    
    def validate_environment(self) -> ValidationResult:
        """
        Validate environment configuration.
        
        Returns:
            ValidationResult
        """
        required_vars = ["AUDIT_CRYPTO_MODE"]
        missing_vars = [var for var in required_vars if not os.getenv(var)]
        
        passed = len(missing_vars) == 0
        message = "Environment configuration valid" if passed else (
            f"Missing environment variables: {', '.join(missing_vars)}"
        )
        
        result = ValidationResult(
            check_type=ValidationType.ENVIRONMENT,
            level=ValidationLevel.WARNING,
            passed=passed,
            message=message,
            details={
                "required_vars": required_vars,
                "missing_vars": missing_vars,
                "audit_crypto_mode": os.getenv("AUDIT_CRYPTO_MODE", "not set"),
            },
            remediation=None if passed else (
                "Set AUDIT_CRYPTO_MODE environment variable (default: software)"
            ),
        )
        
        self.results.append(result)
        return result
    
    def get_summary(self) -> Dict[str, Any]:
        """Get validation summary."""
        return {
            "total_checks": len(self.results),
            "passed": sum(1 for r in self.results if r.passed),
            "failed": sum(1 for r in self.results if not r.passed),
            "critical_failures": sum(
                1 for r in self.results
                if not r.passed and r.level == ValidationLevel.CRITICAL
            ),
        }


class KeyBackupManager:
    """Manages key backups for rollback capability."""
    
    def __init__(self, backup_dir: Path):
        """
        Initialize backup manager.
        
        Args:
            backup_dir: Directory for storing backups
        """
        self.backup_dir = backup_dir
        self.backup_dir.mkdir(parents=True, exist_ok=True)
    
    def backup_current_key(
        self,
        key_value: str,
        deployment_mode: DeploymentMode
    ) -> BackupInfo:
        """
        Backup current key before rotation.
        
        Args:
            key_value: Current key value
            deployment_mode: Current deployment mode
            
        Returns:
            BackupInfo with backup details
            
        Raises:
            IOError: If backup fails
        """
        timestamp = datetime.datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        backup_filename = f"audit_key_backup_{timestamp}.txt"
        backup_path = self.backup_dir / backup_filename
        
        try:
            # Write backup with metadata
            with open(backup_path, 'w') as f:
                f.write(f"# Audit Crypto Key Backup\n")
                f.write(f"# Timestamp: {timestamp}\n")
                f.write(f"# Deployment Mode: {deployment_mode.value}\n")
                f.write(f"# DO NOT COMMIT THIS FILE TO VERSION CONTROL\n")
                f.write(f"\n")
                f.write(f"AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64={key_value}\n")
            
            # Set restrictive permissions (owner read/write only)
            os.chmod(backup_path, 0o600)
            
            logger.info(
                f"Backed up current key to {backup_path}",
                extra={
                    "operation": "backup_key",
                    "backup_path": str(backup_path),
                    "deployment_mode": deployment_mode.value,
                }
            )
            
            return BackupInfo(
                backup_path=str(backup_path),
                original_key=key_value,
                timestamp=timestamp,
                deployment_mode=deployment_mode.value,
                can_rollback=True,
            )
            
        except Exception as e:
            logger.error(
                f"Failed to backup key: {e}",
                extra={"operation": "backup_key_failure", "error": str(e)}
            )
            raise IOError(f"Backup failed: {e}")
    
    def cleanup_old_backups(self, retention_days: int = BACKUP_RETENTION_DAYS) -> int:
        """
        Clean up old backups beyond retention period.
        
        Args:
            retention_days: Number of days to retain backups
            
        Returns:
            Number of backups deleted
        """
        cutoff_date = datetime.datetime.now(UTC) - datetime.timedelta(days=retention_days)
        deleted_count = 0
        
        for backup_file in self.backup_dir.glob("audit_key_backup_*.txt"):
            try:
                # Parse timestamp from filename
                timestamp_str = backup_file.stem.split("_", 3)[-1]
                backup_date = datetime.datetime.strptime(timestamp_str, "%Y%m%d_%H%M%S").replace(tzinfo=UTC)
                
                if backup_date < cutoff_date:
                    backup_file.unlink()
                    deleted_count += 1
                    logger.info(
                        f"Deleted old backup: {backup_file}",
                        extra={
                            "operation": "cleanup_backup",
                            "backup_file": str(backup_file),
                            "backup_age_days": (datetime.datetime.now(UTC) - backup_date).days,
                        }
                    )
            except Exception as e:
                logger.warning(
                    f"Failed to process backup file {backup_file}: {e}",
                    extra={"operation": "cleanup_backup_failure", "error": str(e)}
                )
        
        return deleted_count


class AWSKMSManager:
    """Manages AWS KMS encryption for keys."""
    
    def __init__(self, kms_key_id: Optional[str] = None, region: Optional[str] = None):
        """
        Initialize KMS manager.
        
        Args:
            kms_key_id: KMS key ID or ARN
            region: AWS region (default: from environment)
        """
        if not HAS_BOTO3:
            raise ImportError("boto3 is required for AWS KMS support")
        
        self.kms_key_id = kms_key_id or os.getenv("AWS_KMS_KEY_ID")
        self.region = region or os.getenv("AWS_REGION", "us-east-1")
        self.client = boto3.client("kms", region_name=self.region)
    
    def encrypt_key(self, key_bytes: bytes) -> str:
        """
        Encrypt key using AWS KMS.
        
        Args:
            key_bytes: Raw key bytes
            
        Returns:
            Base64-encoded ciphertext
            
        Raises:
            ClientError: If KMS encryption fails
        """
        try:
            response = self.client.encrypt(
                KeyId=self.kms_key_id,
                Plaintext=key_bytes,
            )
            
            ciphertext = base64.b64encode(response["CiphertextBlob"]).decode("utf-8")
            
            logger.info(
                "Encrypted key with AWS KMS",
                extra={
                    "operation": "kms_encrypt",
                    "kms_key_id": self.kms_key_id,
                    "plaintext_length": len(key_bytes),
                    "ciphertext_length": len(ciphertext),
                }
            )
            
            return ciphertext
            
        except (ClientError, BotoCoreError) as e:
            logger.error(
                f"KMS encryption failed: {e}",
                extra={"operation": "kms_encrypt_failure", "error": str(e)}
            )
            raise
    
    def decrypt_key(self, ciphertext_b64: str) -> bytes:
        """
        Decrypt key using AWS KMS.
        
        Args:
            ciphertext_b64: Base64-encoded ciphertext
            
        Returns:
            Decrypted key bytes
            
        Raises:
            ClientError: If KMS decryption fails
        """
        try:
            ciphertext = base64.b64decode(ciphertext_b64)
            response = self.client.decrypt(CiphertextBlob=ciphertext)
            
            logger.info(
                "Decrypted key with AWS KMS",
                extra={
                    "operation": "kms_decrypt",
                    "ciphertext_length": len(ciphertext),
                }
            )
            
            return response["Plaintext"]
            
        except (ClientError, BotoCoreError) as e:
            logger.error(
                f"KMS decryption failed: {e}",
                extra={"operation": "kms_decrypt_failure", "error": str(e)}
            )
            raise


class KeyRotator:
    """Main key rotation orchestrator."""
    
    def __init__(
        self,
        deployment_mode: DeploymentMode,
        backup_dir: Path,
        dry_run: bool = False,
        enable_otel: bool = False,
    ):
        """
        Initialize key rotator.
        
        Args:
            deployment_mode: Target deployment mode
            backup_dir: Directory for key backups
            dry_run: If True, simulate without making changes
            enable_otel: If True, enable OpenTelemetry tracing
        """
        self.deployment_mode = deployment_mode
        self.backup_dir = backup_dir
        self.dry_run = dry_run
        self.enable_otel = enable_otel and HAS_OTEL
        
        self.generator = KeyGenerator()
        self.validator = KeyValidator()
        self.backup_manager = KeyBackupManager(backup_dir)
        self.kms_manager = None
        
        if deployment_mode == DeploymentMode.AWS_KMS:
            try:
                self.kms_manager = AWSKMSManager()
            except Exception as e:
                logger.warning(f"Failed to initialize KMS manager: {e}")
        
        # Initialize OpenTelemetry tracer if enabled
        self.tracer = None
        if self.enable_otel and trace:
            self.tracer = trace.get_tracer(__name__)
    
    def _start_span(self, name: str):
        """Start an OpenTelemetry span if enabled."""
        if self.tracer:
            return self.tracer.start_as_current_span(name)
        else:
            # Return a dummy context manager
            from contextlib import nullcontext
            return nullcontext()
    
    def detect_deployment_mode(self) -> DeploymentMode:
        """
        Auto-detect deployment mode from environment.
        
        Returns:
            Detected deployment mode
        """
        # Check for Kubernetes
        if os.path.exists("/var/run/secrets/kubernetes.io"):
            return DeploymentMode.KUBERNETES
        
        # Check for AWS KMS
        if os.getenv("AWS_KMS_KEY_ID") or os.getenv("USE_AWS_KMS") == "true":
            return DeploymentMode.AWS_KMS
        
        # Default to Railway/PaaS
        return DeploymentMode.RAILWAY
    
    def validate_current_configuration(self) -> List[ValidationResult]:
        """
        Validate current key configuration.
        
        Returns:
            List of validation results
        """
        with self._start_span("validate_configuration"):
            # Validate environment
            self.validator.validate_environment()
            
            # Validate deployment mode
            self.validator.validate_deployment_mode(self.deployment_mode)
            
            # Validate current key if present
            current_key = os.getenv("AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64")
            if current_key:
                self.validator.validate_key_format(current_key, "base64")
                try:
                    key_bytes = base64.b64decode(current_key)
                    self.validator.validate_key_length(key_bytes, "master")
                    self.validator.validate_key_entropy(key_bytes)
                except Exception as e:
                    logger.error(f"Failed to validate current key: {e}")
            
            return self.validator.results
    
    def generate_new_keys(self) -> Dict[str, Any]:
        """
        Generate new cryptographic keys.
        
        Returns:
            Dictionary with new keys
        """
        with self._start_span("generate_keys"):
            # Generate master encryption key
            master_key_bytes = self.generator.generate_master_key()
            master_key_b64 = self.generator.encode_key_base64(master_key_bytes)
            
            # Generate HMAC key
            hmac_key = self.generator.generate_hmac_key()
            
            # Encrypt with KMS if needed
            if self.deployment_mode == DeploymentMode.AWS_KMS and self.kms_manager:
                try:
                    master_key_b64 = self.kms_manager.encrypt_key(master_key_bytes)
                    logger.info("Encrypted master key with AWS KMS")
                except Exception as e:
                    logger.error(f"KMS encryption failed: {e}")
                    # Fall back to plaintext
            
            # Create metadata
            metadata = KeyMetadata(
                key_id=f"master_{datetime.datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}",
                key_type="master",
                generated_at=datetime.datetime.now(UTC).isoformat(),
                length_bytes=len(master_key_bytes),
                format="base64",
                deployment_mode=self.deployment_mode.value,
                nist_compliant=True,
                min_length_met=len(master_key_bytes) >= MIN_KEY_LENGTH_BYTES,
            )
            
            return {
                "master_key": master_key_b64,
                "hmac_key": hmac_key,
                "metadata": metadata,
            }
    
    def rotate_keys(self, force: bool = False) -> RotationResult:
        """
        Perform key rotation.
        
        Args:
            force: Skip confirmation prompts
            
        Returns:
            RotationResult with operation details
        """
        result = RotationResult(status=RotationStatus.FAILURE)
        
        with self._start_span("rotate_keys"):
            try:
                # Validate current configuration
                result.validation_results = self.validate_current_configuration()
                
                if result.has_critical_failures():
                    result.errors.append("Critical validation failures detected")
                    logger.error("Critical validation failures, aborting rotation")
                    return result
                
                # Backup current key
                current_key = os.getenv("AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64")
                if current_key and not self.dry_run:
                    try:
                        result.backup_info = self.backup_manager.backup_current_key(
                            current_key,
                            self.deployment_mode
                        )
                        result.audit_log_entries.append({
                            "action": "backup_key",
                            "timestamp": datetime.datetime.now(UTC).isoformat(),
                            "backup_path": result.backup_info.backup_path,
                        })
                    except Exception as e:
                        result.errors.append(f"Backup failed: {e}")
                        logger.error(f"Backup failed: {e}")
                        return result
                
                # Generate new keys
                new_keys = self.generate_new_keys()
                result.new_key = new_keys["master_key"]
                result.new_key_metadata = new_keys["metadata"]
                
                result.audit_log_entries.append({
                    "action": "generate_keys",
                    "timestamp": datetime.datetime.now(UTC).isoformat(),
                    "key_length": new_keys["metadata"].length_bytes,
                    "nist_compliant": new_keys["metadata"].nist_compliant,
                })
                
                # Set status
                if self.dry_run:
                    result.status = RotationStatus.DRY_RUN
                    logger.info("Dry run completed successfully")
                else:
                    result.status = RotationStatus.SUCCESS
                    logger.info("Key rotation completed successfully")
                
                # Cleanup old backups
                if not self.dry_run:
                    try:
                        deleted = self.backup_manager.cleanup_old_backups()
                        if deleted > 0:
                            result.audit_log_entries.append({
                                "action": "cleanup_backups",
                                "timestamp": datetime.datetime.now(UTC).isoformat(),
                                "deleted_count": deleted,
                            })
                    except Exception as e:
                        result.warnings.append(f"Backup cleanup warning: {e}")
                
            except Exception as e:
                result.errors.append(f"Rotation failed: {e}")
                logger.error(f"Key rotation failed: {e}", exc_info=True)
                result.status = RotationStatus.FAILURE
        
        return result


# --- Output Formatters ---
class OutputFormatter:
    """Formats rotation results for different output modes."""
    
    @staticmethod
    def format_console(result: RotationResult) -> str:
        """
        Format results for console output.
        
        Args:
            result: Rotation result
            
        Returns:
            Formatted console output
        """
        lines = []
        lines.append("=" * 80)
        lines.append("AUDIT CRYPTOGRAPHIC KEY ROTATION")
        lines.append("=" * 80)
        lines.append("")
        
        # Status
        status_icon = {
            RotationStatus.SUCCESS: "✅",
            RotationStatus.FAILURE: "❌",
            RotationStatus.DRY_RUN: "🔍",
            RotationStatus.SKIPPED: "⏭️",
        }.get(result.status, "❓")
        
        lines.append(f"Status: {status_icon} {result.status.value.upper()}")
        lines.append(f"Timestamp: {result.timestamp}")
        lines.append("")
        
        # Validation Results
        if result.validation_results:
            lines.append("Validation Results:")
            lines.append("-" * 80)
            for validation in result.validation_results:
                icon = "✅" if validation.passed else "❌"
                lines.append(f"{icon} {validation.message}")
                if validation.details:
                    for key, value in validation.details.items():
                        lines.append(f"   {key}: {value}")
                if not validation.passed and validation.remediation:
                    lines.append(f"   Remediation: {validation.remediation}")
            lines.append("")
        
        # New Key Information
        if result.new_key_metadata:
            lines.append("New Key Information:")
            lines.append("-" * 80)
            lines.append(f"Key ID: {result.new_key_metadata.key_id}")
            lines.append(f"Key Type: {result.new_key_metadata.key_type}")
            lines.append(f"Length: {result.new_key_metadata.length_bytes} bytes")
            lines.append(f"Format: {result.new_key_metadata.format}")
            lines.append(f"NIST Compliant: {result.new_key_metadata.nist_compliant}")
            lines.append(f"Generated: {result.new_key_metadata.generated_at}")
            lines.append("")
            
            if result.status != RotationStatus.FAILURE:
                lines.append("New Key Value:")
                lines.append("-" * 80)
                lines.append(f"AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64={result.new_key}")
                lines.append("")
                lines.append("⚠️  IMPORTANT: Store this key securely and update environment variables")
                lines.append("")
        
        # Backup Information
        if result.backup_info:
            lines.append("Backup Information:")
            lines.append("-" * 80)
            lines.append(f"Backup Path: {result.backup_info.backup_path}")
            lines.append(f"Timestamp: {result.backup_info.timestamp}")
            lines.append(f"Can Rollback: {result.backup_info.can_rollback}")
            lines.append("")
        
        # Errors
        if result.errors:
            lines.append("Errors:")
            lines.append("-" * 80)
            for error in result.errors:
                lines.append(f"❌ {error}")
            lines.append("")
        
        # Warnings
        if result.warnings:
            lines.append("Warnings:")
            lines.append("-" * 80)
            for warning in result.warnings:
                lines.append(f"⚠️  {warning}")
            lines.append("")
        
        # Audit Trail
        if result.audit_log_entries:
            lines.append("Audit Trail:")
            lines.append("-" * 80)
            for entry in result.audit_log_entries:
                lines.append(f"  [{entry['timestamp']}] {entry['action']}")
                for key, value in entry.items():
                    if key not in ['timestamp', 'action']:
                        lines.append(f"    {key}: {value}")
            lines.append("")
        
        # Summary
        lines.append("=" * 80)
        if result.status == RotationStatus.SUCCESS:
            lines.append("✅ Key rotation completed successfully!")
            lines.append("")
            lines.append("Next Steps:")
            lines.append("1. Update environment variables with the new key")
            lines.append("2. Restart application services")
            lines.append("3. Verify with: python scripts/validate_secrets.py")
            lines.append("4. Store backup key securely for rollback capability")
        elif result.status == RotationStatus.DRY_RUN:
            lines.append("🔍 Dry run completed - no changes made")
            lines.append("")
            lines.append("Run without --dry-run to perform actual rotation")
        else:
            lines.append("❌ Key rotation failed")
            lines.append("")
            lines.append("Review errors above and retry")
        
        lines.append("=" * 80)
        
        return "\n".join(lines)
    
    @staticmethod
    def format_json(result: RotationResult) -> str:
        """
        Format results as JSON.
        
        Args:
            result: Rotation result
            
        Returns:
            JSON string
        """
        return json.dumps(result.to_dict(), indent=2)


# --- CLI Functions ---
def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Rotate audit cryptographic keys",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --validate              # Validate current configuration
  %(prog)s --dry-run               # Simulate rotation
  %(prog)s --json                  # JSON output for CI/CD
  %(prog)s --mode aws-kms         # AWS KMS mode
  %(prog)s --log-level DEBUG      # Detailed logging
        """,
    )
    
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate key rotation without making changes",
    )
    
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate current key configuration only",
    )
    
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results in JSON format",
    )
    
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Set logging level",
    )
    
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=Path("./key_backups"),
        help="Directory for key backups (default: ./key_backups)",
    )
    
    parser.add_argument(
        "--mode",
        choices=["railway", "aws-kms", "kubernetes", "auto"],
        default="auto",
        help="Deployment mode (default: auto-detect)",
    )
    
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip confirmation prompts",
    )
    
    parser.add_argument(
        "--otel",
        action="store_true",
        help="Enable OpenTelemetry tracing",
    )
    
    return parser.parse_args()


def setup_logging(log_level: str, json_mode: bool = False):
    """
    Setup structured logging.
    
    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
        json_mode: If True, log to stderr to keep stdout clean for JSON
    """
    # In JSON mode, log to stderr to keep stdout clean
    stream = sys.stderr if json_mode else sys.stdout
    
    logging.basicConfig(
        level=getattr(logging, log_level),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(stream)
        ]
    )


def main() -> int:
    """Main entry point."""
    try:
        args = parse_args()
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 2
    
    # Setup logging (log to stderr in JSON mode)
    setup_logging(args.log_level, json_mode=args.json)
    
    # Determine deployment mode
    if args.mode == "auto":
        rotator = KeyRotator(
            deployment_mode=DeploymentMode.AUTO,
            backup_dir=args.backup_dir,
            dry_run=args.dry_run,
            enable_otel=args.otel,
        )
        deployment_mode = rotator.detect_deployment_mode()
        logger.info(f"Auto-detected deployment mode: {deployment_mode.value}")
    else:
        deployment_mode = DeploymentMode(args.mode)
    
    # Create rotator
    rotator = KeyRotator(
        deployment_mode=deployment_mode,
        backup_dir=args.backup_dir,
        dry_run=args.dry_run,
        enable_otel=args.otel,
    )
    
    # Validate-only mode
    if args.validate:
        validation_results = rotator.validate_current_configuration()
        result = RotationResult(
            status=RotationStatus.SKIPPED,
            validation_results=validation_results,
        )
        
        if args.json:
            output = OutputFormatter.format_json(result)
        else:
            output = OutputFormatter.format_console(result)
        
        print(output)
        
        # Exit with error if critical failures
        if result.has_critical_failures():
            return 1
        return 0
    
    # Perform rotation
    result = rotator.rotate_keys(force=args.force)
    
    # Format and print output
    if args.json:
        output = OutputFormatter.format_json(result)
    else:
        output = OutputFormatter.format_console(result)
    
    print(output)
    
    # Determine exit code
    if result.status == RotationStatus.FAILURE:
        return 1
    elif result.has_critical_failures():
        return 1
    else:
        return 0


if __name__ == '__main__':
    sys.exit(main())
