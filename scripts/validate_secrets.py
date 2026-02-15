#!/usr/bin/env python3
"""
Secret Validation Script

This script validates that all critical secrets and environment variables
are properly configured before application startup. It performs comprehensive
validation of cryptographic keys, API tokens, and configuration settings.

The validator checks:
1. Audit crypto configuration (master keys, HMAC keys)
2. Encryption keys (Fernet keys for data at rest)
3. JWT and session secrets
4. LLM provider API keys
5. Environment-specific settings (Railway vs AWS)

Run this script in CI/CD pipelines or before deployment to catch
configuration errors early and prevent runtime failures.

Usage:
    python scripts/validate_secrets.py [--strict] [--json]
    
    Options:
        --strict    Exit with error code 1 for warnings (not just critical errors)
        --json      Output results in JSON format for CI/CD integration
        --help      Show this help message

Exit Codes:
    0 - All required secrets validated successfully (warnings allowed unless --strict)
    1 - One or more critical secrets are invalid or missing
    2 - Invalid arguments or usage error

Examples:
    # Basic validation
    python scripts/validate_secrets.py
    
    # Strict mode (fail on warnings)
    python scripts/validate_secrets.py --strict
    
    # JSON output for CI/CD
    python scripts/validate_secrets.py --json > validation_results.json

Author: Code Factory Team
License: Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.
"""

import argparse
import base64
import json
import os
import sys
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class ValidationLevel(Enum):
    """Validation severity levels."""
    CRITICAL = "critical"  # Required for application to start
    WARNING = "warning"    # Optional but recommended
    INFO = "info"          # Informational only


class ValidationStatus(Enum):
    """Validation check status."""
    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"


@dataclass
class ValidationResult:
    """Result of a validation check."""
    name: str
    status: ValidationStatus
    level: ValidationLevel
    message: str
    details: Optional[Dict] = None
    remediation: Optional[str] = None


@dataclass
class ValidationSummary:
    """Summary of all validation results."""
    results: List[ValidationResult] = field(default_factory=list)
    total_checks: int = 0
    passed: int = 0
    failed: int = 0
    warnings: int = 0
    
    def add_result(self, result: ValidationResult) -> None:
        """Add a validation result and update counters."""
        self.results.append(result)
        self.total_checks += 1
        
        if result.status == ValidationStatus.PASS:
            self.passed += 1
        elif result.status == ValidationStatus.FAIL:
            self.failed += 1
        elif result.status == ValidationStatus.WARN:
            self.warnings += 1
    
    def has_critical_failures(self) -> bool:
        """Check if any critical checks failed."""
        return any(
            r.status == ValidationStatus.FAIL and r.level == ValidationLevel.CRITICAL
            for r in self.results
        )
    
    def to_dict(self) -> Dict:
        """Convert summary to dictionary for JSON output."""
        return {
            "total_checks": self.total_checks,
            "passed": self.passed,
            "failed": self.failed,
            "warnings": self.warnings,
            "has_critical_failures": self.has_critical_failures(),
            "results": [
                {
                    "name": r.name,
                    "status": r.status.value,
                    "level": r.level.value,
                    "message": r.message,
                    "details": r.details,
                    "remediation": r.remediation,
                }
                for r in self.results
            ],
        }


class SecretValidator:
    """Validates application secrets and environment variables."""
    
    def __init__(self, strict_mode: bool = False):
        """
        Initialize the validator.
        
        Args:
            strict_mode: If True, warnings are treated as failures
        """
        self.strict_mode = strict_mode
        self.summary = ValidationSummary()
    
    def _add_pass(
        self,
        name: str,
        message: str,
        level: ValidationLevel = ValidationLevel.CRITICAL,
        details: Optional[Dict] = None,
    ) -> None:
        """Add a passing validation result."""
        result = ValidationResult(
            name=name,
            status=ValidationStatus.PASS,
            level=level,
            message=message,
            details=details,
        )
        self.summary.add_result(result)
    
    def _add_fail(
        self,
        name: str,
        message: str,
        level: ValidationLevel,
        remediation: str,
        details: Optional[Dict] = None,
    ) -> None:
        """Add a failing validation result."""
        status = ValidationStatus.FAIL if level == ValidationLevel.CRITICAL else ValidationStatus.WARN
        
        # In strict mode, warnings become failures
        if self.strict_mode and level == ValidationLevel.WARNING:
            status = ValidationStatus.FAIL
        
        result = ValidationResult(
            name=name,
            status=status,
            level=level,
            message=message,
            details=details,
            remediation=remediation,
        )
        self.summary.add_result(result)
    
    def validate_audit_crypto(self) -> None:
        """Validate audit crypto configuration."""
        mode = os.getenv('AUDIT_CRYPTO_MODE', 'software')
        use_env_secrets = os.getenv('USE_ENV_SECRETS', '').lower() in ('true', '1', 'yes')
        
        if mode == 'software':
            key_b64 = os.getenv('AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64')
            if not key_b64:
                self._add_fail(
                    name="Audit Crypto Master Key",
                    message="AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64 not set",
                    level=ValidationLevel.CRITICAL,
                    remediation=(
                        "Generate with:\n"
                        '  python -c "import base64, os; print(base64.b64encode(os.urandom(32)).decode())"\n\n'
                        "For Railway deployment:\n"
                        "  1. Set USE_ENV_SECRETS=true\n"
                        "  2. Set AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64 to the generated key\n\n"
                        "For AWS KMS deployment:\n"
                        "  1. Generate the key as above\n"
                        "  2. Encrypt with KMS:\n"
                        "     aws kms encrypt --key-id YOUR_KMS_KEY_ID \\\n"
                        "       --plaintext fileb://<(echo -n 'YOUR_KEY' | base64 -d) \\\n"
                        "       --query CiphertextBlob --output text\n"
                        "  3. Set AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64 to the ciphertext"
                    ),
                )
                return
            
            # Validate it is valid base64
            try:
                decoded = base64.b64decode(key_b64)
                self._add_pass(
                    name="Audit Crypto Master Key",
                    message=f"Valid base64-encoded key ({len(decoded)} bytes)",
                    details={"key_length_bytes": len(decoded), "mode": mode},
                )
            except Exception as e:
                self._add_fail(
                    name="Audit Crypto Master Key",
                    message=f"Invalid base64 encoding: {e}",
                    level=ValidationLevel.CRITICAL,
                    remediation="Regenerate the key using the command in the error message above",
                    details={"error": str(e)},
                )
                return
            
            # Check if using environment secrets (recommended for Railway)
            if use_env_secrets:
                self._add_pass(
                    name="Environment Secret Manager",
                    message="USE_ENV_SECRETS=true (environment variable secret manager enabled)",
                    level=ValidationLevel.INFO,
                )
            else:
                self._add_fail(
                    name="Environment Secret Manager",
                    message="USE_ENV_SECRETS not set to 'true'",
                    level=ValidationLevel.WARNING,
                    remediation="For Railway deployments, set USE_ENV_SECRETS=true",
                )
    
    def validate_audit_hmac(self) -> None:
        """Validate audit HMAC key configuration."""
        hmac_key = os.getenv('AGENTIC_AUDIT_HMAC_KEY')
        
        if not hmac_key:
            self._add_fail(
                name="Audit HMAC Key",
                message="AGENTIC_AUDIT_HMAC_KEY not set",
                level=ValidationLevel.CRITICAL,
                remediation=(
                    "Generate with:\n"
                    "  openssl rand -hex 32\n\n"
                    "This key is required for audit log integrity (must be exactly 64 hex characters)"
                ),
            )
            return
        
        # Validate it is 64 hex characters
        if len(hmac_key) != 64:
            self._add_fail(
                name="Audit HMAC Key",
                message=f"Must be exactly 64 characters (got {len(hmac_key)})",
                level=ValidationLevel.CRITICAL,
                remediation="Regenerate using: openssl rand -hex 32",
                details={"actual_length": len(hmac_key), "expected_length": 64},
            )
            return
        
        try:
            int(hmac_key, 16)
            self._add_pass(
                name="Audit HMAC Key",
                message="Valid 64-character hexadecimal key",
            )
        except ValueError:
            self._add_fail(
                name="Audit HMAC Key",
                message="Contains non-hexadecimal characters",
                level=ValidationLevel.CRITICAL,
                remediation="Regenerate using: openssl rand -hex 32",
            )
    
    def validate_encryption_key(self) -> None:
        """Validate encryption key configuration."""
        enc_key = os.getenv('ENCRYPTION_KEY')
        
        if not enc_key:
            self._add_fail(
                name="Encryption Key",
                message="ENCRYPTION_KEY not set",
                level=ValidationLevel.WARNING,
                remediation=(
                    "Generate with:\n"
                    '  python -c "from cryptography.fernet import Fernet; '
                    'print(Fernet.generate_key().decode())"\n\n'
                    "This is required for data encryption at rest"
                ),
            )
            return
        
        # Basic validation - Fernet keys are 44 characters (32 bytes base64url encoded)
        if len(enc_key) != 44:
            self._add_fail(
                name="Encryption Key",
                message=f"Invalid length: {len(enc_key)} (expected 44 for Fernet key)",
                level=ValidationLevel.WARNING,
                remediation='Regenerate using: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"',
                details={"actual_length": len(enc_key), "expected_length": 44},
            )
            return
        
        # Try to validate it's a valid Fernet key
        try:
            # Fernet keys should be base64url encoded
            base64.urlsafe_b64decode(enc_key)
            self._add_pass(
                name="Encryption Key",
                message="Valid Fernet encryption key",
            )
        except Exception as e:
            self._add_fail(
                name="Encryption Key",
                message=f"Invalid Fernet key format: {e}",
                level=ValidationLevel.WARNING,
                remediation='Regenerate using: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"',
                details={"error": str(e)},
            )
    
    def validate_jwt_secrets(self) -> None:
        """Validate JWT and secret keys."""
        secret_key = os.getenv('SECRET_KEY')
        jwt_secret = os.getenv('JWT_SECRET_KEY')
        
        if not secret_key:
            self._add_fail(
                name="SECRET_KEY",
                message="SECRET_KEY not set",
                level=ValidationLevel.WARNING,
                remediation='Generate with:\n  python -c "import secrets; print(secrets.token_urlsafe(32))"',
            )
        else:
            self._add_pass(
                name="SECRET_KEY",
                message="SECRET_KEY is set",
                level=ValidationLevel.WARNING,
                details={"length": len(secret_key)},
            )
        
        if not jwt_secret:
            self._add_fail(
                name="JWT_SECRET_KEY",
                message="JWT_SECRET_KEY not set",
                level=ValidationLevel.WARNING,
                remediation='Generate with:\n  python -c "import secrets; print(secrets.token_urlsafe(32))"',
            )
        else:
            self._add_pass(
                name="JWT_SECRET_KEY",
                message="JWT_SECRET_KEY is set",
                level=ValidationLevel.WARNING,
                details={"length": len(jwt_secret)},
            )
    
    def validate_llm_keys(self) -> None:
        """Validate LLM API keys."""
        openai_key = os.getenv('OPENAI_API_KEY')
        
        if not openai_key:
            self._add_fail(
                name="OPENAI_API_KEY",
                message="OPENAI_API_KEY not set",
                level=ValidationLevel.WARNING,
                remediation="Obtain an API key from https://platform.openai.com/api-keys",
            )
        else:
            # Basic validation - OpenAI keys start with 'sk-'
            if openai_key.startswith('sk-'):
                self._add_pass(
                    name="OPENAI_API_KEY",
                    message="OPENAI_API_KEY is set (format appears valid)",
                    level=ValidationLevel.WARNING,
                )
            else:
                self._add_fail(
                    name="OPENAI_API_KEY",
                    message="OPENAI_API_KEY format may be invalid (should start with 'sk-')",
                    level=ValidationLevel.WARNING,
                    remediation="Verify the API key is correct",
                    details={"key_prefix": openai_key[:10] if len(openai_key) >= 10 else openai_key},
                )
    
    def validate_all(self) -> ValidationSummary:
        """Run all validation checks."""
        self.validate_audit_crypto()
        self.validate_audit_hmac()
        self.validate_encryption_key()
        self.validate_jwt_secrets()
        self.validate_llm_keys()
        
        return self.summary


class OutputFormatter:
    """Formats validation results for different output modes."""
    
    @staticmethod
    def format_console(summary: ValidationSummary) -> str:
        """Format results for console output."""
        lines = []
        lines.append("=" * 70)
        lines.append("SECRET VALIDATION CHECK")
        lines.append("=" * 70)
        lines.append("")
        
        # Group results by status
        for status in [ValidationStatus.FAIL, ValidationStatus.WARN, ValidationStatus.PASS]:
            results_of_status = [r for r in summary.results if r.status == status]
            if not results_of_status:
                continue
            
            for result in results_of_status:
                # Status icon
                if result.status == ValidationStatus.PASS:
                    icon = "✓"
                    color = ""
                elif result.status == ValidationStatus.FAIL:
                    icon = "✗"
                    color = ""
                else:
                    icon = "⚠"
                    color = ""
                
                lines.append(f"{icon} {result.name}: {result.message}")
                
                if result.details:
                    for key, value in result.details.items():
                        lines.append(f"  └─ {key}: {value}")
                
                if result.remediation and result.status != ValidationStatus.PASS:
                    lines.append(f"  Remediation:")
                    for line in result.remediation.split('\n'):
                        lines.append(f"    {line}")
                
                lines.append("")
        
        # Summary
        lines.append("=" * 70)
        lines.append("VALIDATION SUMMARY")
        lines.append("=" * 70)
        lines.append(f"Total checks: {summary.total_checks}")
        lines.append(f"Passed: {summary.passed}")
        lines.append(f"Failed: {summary.failed}")
        lines.append(f"Warnings: {summary.warnings}")
        lines.append("=" * 70)
        
        if summary.has_critical_failures():
            lines.append("")
            lines.append("❌ CRITICAL: Application cannot start with missing critical secrets")
            lines.append("   Fix the errors above before starting the application")
        elif summary.failed > 0 or summary.warnings > 0:
            lines.append("")
            lines.append("⚠️  WARNING: Some secrets are missing or invalid")
            lines.append("   Application may start but some features will be disabled")
            lines.append("   Review warnings above")
        else:
            lines.append("")
            lines.append("✓ All secrets validated successfully")
            lines.append("   Application is ready to start")
        
        return "\n".join(lines)
    
    @staticmethod
    def format_json(summary: ValidationSummary) -> str:
        """Format results as JSON."""
        return json.dumps(summary.to_dict(), indent=2)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Validate application secrets and environment variables",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                  # Basic validation
  %(prog)s --strict         # Strict mode (fail on warnings)
  %(prog)s --json          # JSON output for CI/CD
        """,
    )
    
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat warnings as failures (exit code 1)",
    )
    
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results in JSON format",
    )
    
    return parser.parse_args()


def main() -> int:
    """Main entry point."""
    try:
        args = parse_args()
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 2
    
    # Run validation
    validator = SecretValidator(strict_mode=args.strict)
    summary = validator.validate_all()
    
    # Format and print output
    if args.json:
        output = OutputFormatter.format_json(summary)
    else:
        output = OutputFormatter.format_console(summary)
    
    print(output)
    
    # Determine exit code
    if summary.has_critical_failures():
        return 1
    elif args.strict and (summary.failed > 0 or summary.warnings > 0):
        return 1
    else:
        return 0


if __name__ == '__main__':
    sys.exit(main())
