#!/usr/bin/env python3
"""
Audit Log Configuration Validator

This script validates audit logging configuration to catch common errors
and insecure settings before deployment.

Usage:
    python validate_config.py [--config path/to/config.yaml] [--strict]

Options:
    --config: Path to configuration file (default: audit_config.yaml)
    --strict: Enable strict validation (fail on warnings)
    --env: Validate environment variables instead of config file
"""

import argparse
import json
import os
import sys
import yaml
from typing import Any, Dict, List, Tuple
from pathlib import Path


class ConfigValidator:
    """Validates audit log configuration"""

    def __init__(self, strict: bool = False):
        self.strict = strict
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.info: List[str] = []

    def add_error(self, message: str):
        """Add an error message"""
        self.errors.append(f"❌ ERROR: {message}")

    def add_warning(self, message: str):
        """Add a warning message"""
        self.warnings.append(f"⚠️  WARNING: {message}")

    def add_info(self, message: str):
        """Add an info message"""
        self.info.append(f"ℹ️  INFO: {message}")

    def validate_crypto_provider(self, config: Dict[str, Any]):
        """Validate cryptographic provider configuration"""
        provider_type = config.get("PROVIDER_TYPE", "software")

        if provider_type not in ["software", "hsm"]:
            self.add_error(
                f"Invalid PROVIDER_TYPE: '{provider_type}'. Must be 'software' or 'hsm'"
            )

        if provider_type == "software":
            self.add_warning(
                "Using 'software' crypto provider. Consider using HSM for production."
            )

        algo = config.get("DEFAULT_ALGO", "ed25519")
        valid_algos = ["rsa", "ecdsa", "ed25519", "hmac"]
        if algo not in valid_algos:
            self.add_error(
                f"Invalid DEFAULT_ALGO: '{algo}'. Must be one of {valid_algos}"
            )

        rotation_interval = config.get("KEY_ROTATION_INTERVAL_SECONDS", 86400)
        if rotation_interval < 86400:
            self.add_error(
                f"KEY_ROTATION_INTERVAL_SECONDS must be >= 86400 (24 hours). Got: {rotation_interval}"
            )
        elif rotation_interval < 604800:  # 7 days
            self.add_warning(
                f"KEY_ROTATION_INTERVAL_SECONDS is {rotation_interval}s. Consider increasing to 604800s (7 days) for production."
            )

    def validate_backend(self, config: Dict[str, Any]):
        """Validate backend configuration"""
        backend_type = config.get("BACKEND_TYPE", "file")

        valid_backends = [
            "file",
            "sqlite",
            "s3",
            "gcs",
            "azure",
            "http",
            "kafka",
            "splunk",
            "memory",
        ]

        if backend_type not in valid_backends:
            self.add_error(
                f"Invalid BACKEND_TYPE: '{backend_type}'. Must be one of {valid_backends}"
            )

        if backend_type == "memory":
            self.add_warning("Using 'memory' backend. Data will not persist!")

        if backend_type in ["file", "sqlite"]:
            self.add_warning(
                f"Using '{backend_type}' backend. Consider cloud storage for production."
            )

    def validate_compression(self, config: Dict[str, Any]):
        """Validate compression settings"""
        algo = config.get("COMPRESSION_ALGO", "none")
        valid_algos = ["none", "gzip", "zstd"]

        if algo not in valid_algos:
            self.add_error(
                f"Invalid COMPRESSION_ALGO: '{algo}'. Must be one of {valid_algos}"
            )

        level = config.get("COMPRESSION_LEVEL", 0)
        if algo == "zstd" and not (1 <= level <= 22):
            self.add_warning(
                f"COMPRESSION_LEVEL {level} is out of recommended range for zstd (1-22)"
            )
        elif algo == "gzip" and not (1 <= level <= 9):
            self.add_warning(
                f"COMPRESSION_LEVEL {level} is out of recommended range for gzip (1-9)"
            )

    def validate_batch_processing(self, config: Dict[str, Any]):
        """Validate batch processing settings"""
        flush_interval = config.get("BATCH_FLUSH_INTERVAL", 10)
        if not (1 <= flush_interval <= 60):
            self.add_warning(
                f"BATCH_FLUSH_INTERVAL {flush_interval} is outside recommended range (1-60 seconds)"
            )

        batch_size = config.get("BATCH_MAX_SIZE", 100)
        if not (1 <= batch_size <= 1000):
            self.add_warning(
                f"BATCH_MAX_SIZE {batch_size} is outside recommended range (1-1000)"
            )

    def validate_retry_settings(self, config: Dict[str, Any]):
        """Validate retry and fault tolerance settings"""
        max_attempts = config.get("RETRY_MAX_ATTEMPTS", 3)
        if not (0 <= max_attempts <= 10):
            self.add_warning(
                f"RETRY_MAX_ATTEMPTS {max_attempts} is outside recommended range (0-10)"
            )

        backoff = config.get("RETRY_BACKOFF_FACTOR", 0.5)
        if not (0.1 <= backoff <= 5.0):
            self.add_warning(
                f"RETRY_BACKOFF_FACTOR {backoff} is outside recommended range (0.1-5.0)"
            )

    def validate_security(self, config: Dict[str, Any]):
        """Validate security settings"""
        dev_mode = config.get("DEV_MODE", False)
        if dev_mode:
            self.add_warning(
                "DEV_MODE is enabled. NEVER use this in production!"
            )

        # Check if we're in production environment
        is_production = any(
            os.getenv(var, "").lower() in ["production", "prod"]
            for var in ["PYTHON_ENV", "APP_ENV", "NODE_ENV", "ENVIRONMENT"]
        ) or os.getenv("PRODUCTION_MODE") == "1"

        if is_production:
            # Strict production checks
            if not config.get("ENCRYPTION_ENABLED", True):
                self.add_error("ENCRYPTION_ENABLED must be true in production")

            if not config.get("RBAC_ENABLED", True):
                self.add_error("RBAC_ENABLED must be true in production")

            if not config.get("IMMUTABLE", True):
                self.add_error("IMMUTABLE must be true in production")

            if not config.get("TAMPER_DETECTION_ENABLED", True):
                self.add_error("TAMPER_DETECTION_ENABLED must be true in production")

            if config.get("CRYPTO_ALLOW_DUMMY_PROVIDER", False):
                self.add_error("CRYPTO_ALLOW_DUMMY_PROVIDER must be false in production")

            if config.get("DEV_MODE_ALLOW_INSECURE_SECRETS", False):
                self.add_error("DEV_MODE_ALLOW_INSECURE_SECRETS must be false in production")

            secret_manager = config.get("SECRET_MANAGER", "env")
            if secret_manager == "mock":
                self.add_error("SECRET_MANAGER cannot be 'mock' in production")
            elif secret_manager == "env":
                self.add_warning(
                    "Using 'env' SECRET_MANAGER. Consider using AWS/GCP/Vault for production."
                )

            crypto_mode = config.get("CRYPTO_MODE", "full")
            if crypto_mode != "full":
                self.add_error(f"CRYPTO_MODE must be 'full' in production. Got: '{crypto_mode}'")
            
            # NEW: Validate AUDIT_CRYPTO_MODE for production
            # This validates the environment variable-based config used by audit_crypto_factory
            audit_crypto_mode = os.getenv("AUDIT_CRYPTO_MODE", "software").lower()
            if audit_crypto_mode == "disabled":
                self.add_error(
                    "AUDIT_CRYPTO_MODE cannot be 'disabled' in production. "
                    "Use 'software' or 'hsm' for cryptographic audit log signatures. "
                    "Refer to docs/AUDIT_CONFIGURATION.md for migration guide."
                )
            elif audit_crypto_mode not in ["software", "hsm"]:
                self.add_warning(
                    f"AUDIT_CRYPTO_MODE='{audit_crypto_mode}' is not a standard production value. "
                    "Recommended: 'software' or 'hsm'"
                )

    def validate_compliance(self, config: Dict[str, Any]):
        """Validate compliance settings"""
        compliance_mode = config.get("COMPLIANCE_MODE", "standard")
        valid_modes = ["soc2", "hipaa", "pci-dss", "gdpr", "standard"]

        if compliance_mode not in valid_modes:
            self.add_error(
                f"Invalid COMPLIANCE_MODE: '{compliance_mode}'. Must be one of {valid_modes}"
            )

        retention_days = config.get("DATA_RETENTION_DAYS", 365)

        # Check compliance-specific requirements
        if compliance_mode == "soc2" and retention_days < 365:
            self.add_error(
                f"SOC2 compliance requires DATA_RETENTION_DAYS >= 365. Got: {retention_days}"
            )
        elif compliance_mode == "hipaa" and retention_days < 2555:  # 7 years
            self.add_error(
                f"HIPAA compliance requires DATA_RETENTION_DAYS >= 2555 (7 years). Got: {retention_days}"
            )
        elif compliance_mode == "pci-dss" and retention_days < 365:
            self.add_error(
                f"PCI-DSS compliance requires DATA_RETENTION_DAYS >= 365. Got: {retention_days}"
            )

        # PII redaction required for GDPR and HIPAA
        if compliance_mode in ["gdpr", "hipaa"]:
            if not config.get("PII_REDACTION_ENABLED", True):
                self.add_error(
                    f"{compliance_mode.upper()} compliance requires PII_REDACTION_ENABLED=true"
                )

    def validate_ports(self, config: Dict[str, Any]):
        """Validate port configuration"""
        ports = {
            "METRICS_PORT": config.get("METRICS_PORT", 8002),
            "API_PORT": config.get("API_PORT", 8003),
            "GRPC_PORT": config.get("GRPC_PORT", 50051),
        }

        # Check for port conflicts
        port_values = list(ports.values())
        if len(port_values) != len(set(port_values)):
            self.add_error("Port conflict detected: Multiple services using the same port")

        # Check valid port ranges
        for name, port in ports.items():
            if not (1024 <= port <= 65535):
                self.add_warning(
                    f"{name} {port} is outside recommended range (1024-65535)"
                )

    def validate_observability(self, config: Dict[str, Any]):
        """Validate observability settings"""
        if config.get("TRACING_ENABLED", False):
            sample_rate = config.get("TRACING_SAMPLE_RATE", 0.1)
            if not (0.0 <= sample_rate <= 1.0):
                self.add_error(
                    f"TRACING_SAMPLE_RATE must be between 0.0 and 1.0. Got: {sample_rate}"
                )

            if sample_rate == 1.0:
                self.add_warning(
                    "TRACING_SAMPLE_RATE is 1.0 (100%). This may cause high overhead in production."
                )

    def validate_environment_variables(self):
        """Validate required environment variables"""
        # Check for encryption key in production
        is_production = any(
            os.getenv(var, "").lower() in ["production", "prod"]
            for var in ["PYTHON_ENV", "APP_ENV", "NODE_ENV", "ENVIRONMENT"]
        ) or os.getenv("PRODUCTION_MODE") == "1"

        if is_production:
            if not os.getenv("AUDIT_LOG_ENCRYPTION_KEY"):
                self.add_error(
                    "AUDIT_LOG_ENCRYPTION_KEY environment variable must be set in production"
                )

            # Check for secure random key (at least 32 bytes base64)
            key = os.getenv("AUDIT_LOG_ENCRYPTION_KEY", "")
            if key and len(key) < 40:  # Base64 of 32 bytes is ~44 chars
                self.add_warning(
                    "AUDIT_LOG_ENCRYPTION_KEY appears too short. Use Fernet.generate_key()"
                )

    def validate_config(self, config: Dict[str, Any]) -> bool:
        """
        Validate entire configuration

        Returns:
            True if validation passes, False otherwise
        """
        self.add_info("Starting configuration validation...")

        # Run all validation checks
        self.validate_crypto_provider(config)
        self.validate_backend(config)
        self.validate_compression(config)
        self.validate_batch_processing(config)
        self.validate_retry_settings(config)
        self.validate_security(config)
        self.validate_compliance(config)
        self.validate_ports(config)
        self.validate_observability(config)
        self.validate_environment_variables()

        # Print results
        print("\n" + "=" * 70)
        print("AUDIT LOG CONFIGURATION VALIDATION RESULTS")
        print("=" * 70 + "\n")

        if self.info:
            for msg in self.info:
                print(msg)
            print()

        if self.warnings:
            print("WARNINGS:")
            for msg in self.warnings:
                print(msg)
            print()

        if self.errors:
            print("ERRORS:")
            for msg in self.errors:
                print(msg)
            print()

        # Determine overall status
        has_errors = len(self.errors) > 0
        has_warnings = len(self.warnings) > 0

        if has_errors:
            print("❌ Validation FAILED - Configuration has critical errors")
            return False
        elif has_warnings and self.strict:
            print("⚠️  Validation FAILED - Strict mode enabled and warnings present")
            return False
        elif has_warnings:
            print("⚠️  Validation PASSED with warnings - Review warnings before deployment")
            return True
        else:
            print("✅ Validation PASSED - Configuration is valid")
            return True


def load_config_file(config_path: str) -> Dict[str, Any]:
    """Load configuration from YAML file"""
    try:
        with open(config_path, "r") as f:
            config = yaml.safe_load(f)
            if not config:
                print(f"Error: Configuration file {config_path} is empty")
                sys.exit(1)
            return config
    except FileNotFoundError:
        print(f"Error: Configuration file {config_path} not found")
        sys.exit(1)
    except yaml.YAMLError as e:
        print(f"Error: Invalid YAML in {config_path}: {e}")
        sys.exit(1)


def load_env_config() -> Dict[str, Any]:
    """Load configuration from environment variables"""
    config = {}

    # Map environment variables to config keys
    env_mapping = {
        "AUDIT_LOG_BACKEND_TYPE": "BACKEND_TYPE",
        "AUDIT_LOG_ENCRYPTION_KEY": "ENCRYPTION_KEY",
        "AUDIT_LOG_METRICS_PORT": "METRICS_PORT",
        "AUDIT_LOG_API_PORT": "API_PORT",
        "AUDIT_LOG_GRPC_PORT": "GRPC_PORT",
        "AUDIT_LOG_IMMUTABLE": "IMMUTABLE",
        "AUDIT_LOG_DEV_MODE": "DEV_MODE",
        "AUDIT_CRYPTO_PROVIDER_TYPE": "PROVIDER_TYPE",
        "AUDIT_CRYPTO_DEFAULT_ALGO": "DEFAULT_ALGO",
        "AUDIT_CRYPTO_MODE": "CRYPTO_MODE",
        "AUDIT_CRYPTO_ALLOW_INIT_FAILURE": "CRYPTO_ALLOW_INIT_FAILURE",
        "AUDIT_CRYPTO_ALLOW_DUMMY_PROVIDER": "CRYPTO_ALLOW_DUMMY_PROVIDER",
        "AUDIT_COMPRESSION_ALGO": "COMPRESSION_ALGO",
        "AUDIT_COMPRESSION_LEVEL": "COMPRESSION_LEVEL",
        "AUDIT_BATCH_FLUSH_INTERVAL": "BATCH_FLUSH_INTERVAL",
        "AUDIT_BATCH_MAX_SIZE": "BATCH_MAX_SIZE",
        "AUDIT_RETRY_MAX_ATTEMPTS": "RETRY_MAX_ATTEMPTS",
        "AUDIT_RETRY_BACKOFF_FACTOR": "RETRY_BACKOFF_FACTOR",
        "AUDIT_TAMPER_DETECTION_ENABLED": "TAMPER_DETECTION_ENABLED",
        "SECRET_MANAGER": "SECRET_MANAGER",
    }

    for env_var, config_key in env_mapping.items():
        value = os.getenv(env_var)
        if value is not None:
            # Convert string values to appropriate types
            if value.lower() in ["true", "false"]:
                config[config_key] = value.lower() == "true"
            elif value.isdigit():
                config[config_key] = int(value)
            else:
                try:
                    config[config_key] = float(value)
                except ValueError:
                    config[config_key] = value

    return config


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="Validate audit log configuration",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--config",
        default="audit_config.yaml",
        help="Path to configuration file (default: audit_config.yaml)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Enable strict validation (fail on warnings)",
    )
    parser.add_argument(
        "--env",
        action="store_true",
        help="Validate environment variables instead of config file",
    )

    args = parser.parse_args()

    # Load configuration
    if args.env:
        print("Validating environment variables...")
        config = load_env_config()
        if not config:
            print("Warning: No audit configuration found in environment variables")
            print("This may be expected if using config files instead")
    else:
        config_path = args.config
        print(f"Loading configuration from: {config_path}")
        config = load_config_file(config_path)

    # Validate configuration
    validator = ConfigValidator(strict=args.strict)
    success = validator.validate_config(config)

    # Exit with appropriate code
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
