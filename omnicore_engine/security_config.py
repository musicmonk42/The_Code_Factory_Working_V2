# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
security_config.py

Enterprise Security Configuration Module
Compliant with: SOC 2, ISO 27001, HIPAA, PCI-DSS, GDPR, NIST Cybersecurity Framework
Version: 1.0.0
Classification: CONFIDENTIAL
"""

import ipaddress
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Set

try:
    from pydantic import Field, field_validator, model_validator, conint
except ImportError:
    # Fallback for older pydantic versions or missing pydantic
    from pydantic import Field, field_validator, model_validator
    try:
        from pydantic.types import conint
    except ImportError:
        # Create a minimal conint equivalent if pydantic.types is not available
        def conint(*, gt=None, ge=None, lt=None, le=None, strict=False, multiple_of=None):
            return int

# Pydantic V2 Imports: BaseSettings is now in pydantic_settings
try:
    from pydantic_settings import BaseSettings, SettingsConfigDict
except ImportError:
    # Fallback for environments without pydantic_settings
    from pydantic import BaseSettings
    SettingsConfigDict = dict


class SecurityLevel(str, Enum):
    """Security classification levels per regulatory requirements"""

    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    RESTRICTED = "RESTRICTED"
    TOP_SECRET = "TOP_SECRET"


class ComplianceFramework(str, Enum):
    """Supported compliance frameworks"""

    SOC2_TYPE2 = "SOC2_TYPE2"
    ISO_27001 = "ISO_27001"
    HIPAA = "HIPAA"
    PCI_DSS = "PCI_DSS"
    GDPR = "GDPR"
    CCPA = "CCPA"
    NIST_CSF = "NIST_CSF"
    FEDRAMP = "FEDRAMP"
    FIPS_140_2 = "FIPS_140_2"


class EnterpriseSecurityConfig(BaseSettings):
    """
    Enterprise-grade security configuration for highly regulated environments.
    All settings follow industry best practices and regulatory requirements.
    """

    # ================== COMPLIANCE & GOVERNANCE ==================
    COMPLIANCE_FRAMEWORKS: List[ComplianceFramework] = Field(
        default=[
            ComplianceFramework.SOC2_TYPE2,
            ComplianceFramework.ISO_27001,
            ComplianceFramework.NIST_CSF,
        ],
        description="Active compliance frameworks",
    )

    DATA_CLASSIFICATION_REQUIRED: bool = Field(
        default=True, description="Enforce data classification for all operations"
    )

    DEFAULT_DATA_CLASSIFICATION: SecurityLevel = Field(
        default=SecurityLevel.CONFIDENTIAL,
        description="Default classification if not specified",
    )

    REGULATORY_AUDIT_MODE: bool = Field(
        default=True, description="Enhanced audit logging for regulatory compliance"
    )

    # ================== CRYPTOGRAPHY & KEY MANAGEMENT ==================

    # Key Management
    KEY_ROTATION_DAYS: conint(ge=1, le=90) = Field(
        default=30, description="Maximum age for encryption keys (days)"
    )

    KEY_ROTATION_OVERLAP_DAYS: conint(ge=1, le=30) = Field(
        default=7, description="Overlap period for key rotation"
    )

    MIN_KEY_LENGTH_BITS: conint(ge=256) = Field(
        default=256, description="Minimum encryption key length in bits"
    )

    KEY_DERIVATION_ITERATIONS: conint(ge=100000) = Field(
        default=600000, description="PBKDF2 iterations for key derivation"
    )

    REQUIRE_HARDWARE_SECURITY_MODULE: bool = Field(
        default=False, description="Require HSM for key storage"
    )

    # Encryption Standards
    ENCRYPTION_ALGORITHM: str = Field(
        default="AES-256-GCM", description="Primary encryption algorithm"
    )

    BACKUP_ENCRYPTION_ALGORITHM: str = Field(
        default="ChaCha20-Poly1305", description="Fallback encryption algorithm"
    )

    HASH_ALGORITHM: str = Field(
        default="SHA3-512", description="Cryptographic hash algorithm"
    )

    REQUIRE_ENCRYPTION_AT_REST: bool = Field(
        default=True, description="Mandate encryption for stored data"
    )

    REQUIRE_ENCRYPTION_IN_TRANSIT: bool = Field(
        default=True, description="Mandate TLS for all communications"
    )

    MIN_TLS_VERSION: str = Field(default="1.3", description="Minimum TLS version")

    APPROVED_TLS_CIPHERS: List[str] = Field(
        default=[
            "TLS_AES_256_GCM_SHA384",
            "TLS_CHACHA20_POLY1305_SHA256",
            "TLS_AES_128_GCM_SHA256",
        ],
        description="Approved TLS cipher suites",
    )

    # ================== AUTHENTICATION & AUTHORIZATION ==================

    # Multi-Factor Authentication
    MFA_REQUIRED: bool = Field(
        default=True, description="Require multi-factor authentication"
    )

    MFA_METHODS: List[str] = Field(
        default=["TOTP", "FIDO2", "SMS_BACKUP"], description="Allowed MFA methods"
    )

    MFA_REMEMBER_DEVICE_DAYS: conint(ge=0, le=30) = Field(
        default=0, description="Days to remember trusted device (0=never)"
    )

    # Password Policy
    MIN_PASSWORD_LENGTH: conint(ge=14) = Field(
        default=16, description="Minimum password length"
    )

    PASSWORD_COMPLEXITY_RULES: Dict[str, Any] = Field(
        default={
            "require_uppercase": True,
            "require_lowercase": True,
            "require_numbers": True,
            "require_special_chars": True,
            "min_unique_chars": 8,
            "prevent_common_passwords": True,
            "prevent_personal_info": True,
            "prevent_keyboard_patterns": True,
            "max_consecutive_chars": 2,
            "min_entropy_bits": 60,
        },
        description="Password complexity requirements",
    )

    PASSWORD_HISTORY_COUNT: conint(ge=12) = Field(
        default=24, description="Number of previous passwords to prevent reuse"
    )

    PASSWORD_EXPIRY_DAYS: conint(ge=30, le=90) = Field(
        default=60, description="Maximum password age"
    )

    PASSWORD_EXPIRY_WARNING_DAYS: conint(ge=7) = Field(
        default=14, description="Days before expiry to warn user"
    )

    # Account Security
    MAX_FAILED_LOGIN_ATTEMPTS: conint(ge=3, le=10) = Field(
        default=5, description="Failed attempts before account lockout"
    )

    ACCOUNT_LOCKOUT_DURATION_MINUTES: conint(ge=15) = Field(
        default=30, description="Account lockout duration"
    )

    PROGRESSIVE_LOCKOUT: bool = Field(
        default=True, description="Increase lockout duration with repeated violations"
    )

    REQUIRE_ACCOUNT_APPROVAL: bool = Field(
        default=True, description="New accounts require admin approval"
    )

    PRIVILEGED_ACCESS_DURATION_MINUTES: conint(ge=15, le=480) = Field(
        default=60, description="Maximum duration for elevated privileges"
    )

    # ================== SESSION MANAGEMENT ==================

    SESSION_TIMEOUT_MINUTES: conint(ge=5, le=60) = Field(
        default=15, description="Idle session timeout"
    )

    ABSOLUTE_SESSION_TIMEOUT_MINUTES: conint(ge=60, le=480) = Field(
        default=240, description="Maximum session duration regardless of activity"
    )

    MAX_CONCURRENT_SESSIONS: conint(ge=1, le=5) = Field(
        default=2, description="Maximum concurrent sessions per user"
    )

    SESSION_TOKEN_LENGTH: conint(ge=32) = Field(
        default=64, description="Session token length in bytes"
    )

    REQUIRE_SESSION_FINGERPRINTING: bool = Field(
        default=True, description="Bind sessions to device fingerprint"
    )

    INVALIDATE_SESSION_ON_IP_CHANGE: bool = Field(
        default=True, description="Terminate session if IP address changes"
    )

    # ================== ACCESS CONTROL ==================

    RBAC_ENABLED: bool = Field(
        default=True, description="Enable Role-Based Access Control"
    )

    ABAC_ENABLED: bool = Field(
        default=True, description="Enable Attribute-Based Access Control"
    )

    DEFAULT_DENY: bool = Field(
        default=True, description="Deny access unless explicitly granted"
    )

    PRIVILEGE_ESCALATION_REQUIRES_MFA: bool = Field(
        default=True, description="Require MFA for privilege escalation"
    )

    SEPARATION_OF_DUTIES: Dict[str, List[str]] = Field(
        default={
            "approval_required": ["DELETE", "MODIFY_SECURITY", "EXPORT_DATA"],
            "dual_control": ["KEY_ROTATION", "AUDIT_MODIFICATION"],
            "time_delayed": ["BULK_DELETE", "SYSTEM_SHUTDOWN"],
        },
        description="Operations requiring special controls",
    )

    # ================== RATE LIMITING & DDoS PROTECTION ==================

    GLOBAL_RATE_LIMIT_PER_SECOND: conint(ge=10) = Field(
        default=100, description="Global rate limit per second"
    )

    PER_USER_RATE_LIMIT_PER_MINUTE: conint(ge=10) = Field(
        default=60, description="Per-user rate limit per minute"
    )

    PER_IP_RATE_LIMIT_PER_MINUTE: conint(ge=10) = Field(
        default=100, description="Per-IP rate limit per minute"
    )

    API_ENDPOINT_LIMITS: Dict[str, Dict[str, int]] = Field(
        default={
            "/auth/login": {"per_minute": 5, "burst": 10},
            "/auth/register": {"per_minute": 2, "burst": 3},
            "/api/sensitive": {"per_minute": 10, "burst": 20},
            "/api/public": {"per_minute": 100, "burst": 200},
        },
        description="Endpoint-specific rate limits",
    )

    ENABLE_DISTRIBUTED_RATE_LIMITING: bool = Field(
        default=True, description="Enable rate limiting across cluster"
    )

    # ================== INPUT VALIDATION & SANITIZATION ==================

    MAX_REQUEST_SIZE_BYTES: conint(ge=1024) = Field(
        default=10485760, description="Maximum HTTP request size"  # 10MB
    )

    MAX_FILE_UPLOAD_SIZE_BYTES: conint(ge=1024) = Field(
        default=52428800, description="Maximum file upload size"  # 50MB
    )

    ALLOWED_FILE_EXTENSIONS: Set[str] = Field(
        default={
            ".pdf",
            ".doc",
            ".docx",
            ".xls",
            ".xlsx",
            ".txt",
            ".csv",
            ".json",
            ".xml",
            ".zip",
        },
        description="Permitted file upload extensions",
    )

    FORBIDDEN_FILE_EXTENSIONS: Set[str] = Field(
        default={
            ".exe",
            ".dll",
            ".so",
            ".dylib",
            ".bat",
            ".sh",
            ".ps1",
            ".vbs",
            ".js",
            ".jar",
            ".class",
            ".war",
        },
        description="Explicitly forbidden file extensions",
    )

    ENABLE_FILE_TYPE_VERIFICATION: bool = Field(
        default=True, description="Verify file content matches extension"
    )

    ENABLE_VIRUS_SCANNING: bool = Field(
        default=True, description="Scan uploads for malware"
    )

    SQL_INJECTION_PROTECTION: bool = Field(
        default=True, description="Enable SQL injection prevention"
    )

    XSS_PROTECTION: bool = Field(default=True, description="Enable XSS protection")

    # ================== AUDIT & MONITORING ==================

    AUDIT_LOG_RETENTION_DAYS: conint(ge=365) = Field(
        default=2555, description="Audit log retention period"  # 7 years
    )

    AUDIT_LOG_ENCRYPTION: bool = Field(default=True, description="Encrypt audit logs")

    AUDIT_LOG_INTEGRITY_CHECK: bool = Field(
        default=True, description="Use cryptographic signatures for audit logs"
    )

    AUDIT_LOG_EVENTS: Set[str] = Field(
        default={
            "AUTHENTICATION",
            "AUTHORIZATION",
            "DATA_ACCESS",
            "DATA_MODIFICATION",
            "DATA_DELETION",
            "CONFIGURATION_CHANGE",
            "PRIVILEGE_ESCALATION",
            "SECURITY_VIOLATION",
            "SYSTEM_ACCESS",
            "KEY_MANAGEMENT",
            "AUDIT_LOG_ACCESS",
            "EXPORT_OPERATION",
        },
        description="Events requiring audit logging",
    )

    SECURITY_MONITORING_ENABLED: bool = Field(
        default=True, description="Enable real-time security monitoring"
    )

    ANOMALY_DETECTION_ENABLED: bool = Field(
        default=True, description="Enable ML-based anomaly detection"
    )

    ALERT_CHANNELS: List[str] = Field(
        default=["SIEM", "EMAIL", "SMS", "SLACK"],
        description="Security alert notification channels",
    )

    # ================== NETWORK SECURITY ==================

    ALLOWED_IP_RANGES: List[str] = Field(
        default=[], description="Allowed IP ranges (empty = all)"
    )

    BLOCKED_IP_RANGES: List[str] = Field(
        default=[
            "10.0.0.0/8",  # Private network
            "172.16.0.0/12",  # Private network
            "192.168.0.0/16",  # Private network
            "169.254.0.0/16",  # Link local
            "::1/128",  # IPv6 loopback
            "fe80::/10",  # IPv6 link local
        ],
        description="Blocked IP ranges",
    )

    REQUIRE_SECURE_HEADERS: bool = Field(
        default=True, description="Enforce security headers"
    )

    SECURITY_HEADERS: Dict[str, str] = Field(
        default={
            "Strict-Transport-Security": "max-age=31536000; includeSubDomains; preload",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "X-XSS-Protection": "1; mode=block",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'",
            "Referrer-Policy": "strict-origin-when-cross-origin",
            "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
        },
        description="Required security headers",
    )

    # ================== DATA PROTECTION ==================

    DATA_RETENTION_POLICIES: Dict[str, int] = Field(
        default={
            "user_data": 2555,  # 7 years
            "transaction_data": 2555,
            "audit_logs": 2555,
            "session_data": 30,
            "temporary_files": 1,
            "cache_data": 7,
        },
        description="Data retention periods in days",
    )

    PII_ENCRYPTION_REQUIRED: bool = Field(
        default=True, description="Encrypt Personally Identifiable Information"
    )

    PII_FIELDS: Set[str] = Field(
        default={
            "ssn",
            "social_security",
            "tax_id",
            "passport",
            "driver_license",
            "credit_card",
            "bank_account",
            "email",
            "phone",
            "address",
            "date_of_birth",
            "medical_record",
            "biometric_data",
        },
        description="Fields considered as PII",
    )

    DATA_MASKING_ENABLED: bool = Field(
        default=True, description="Enable data masking for sensitive fields"
    )

    RIGHT_TO_ERASURE_ENABLED: bool = Field(
        default=True, description="Support GDPR right to erasure"
    )

    # ================== INCIDENT RESPONSE ==================

    INCIDENT_RESPONSE_ENABLED: bool = Field(
        default=True, description="Enable automated incident response"
    )

    AUTO_CONTAINMENT_ENABLED: bool = Field(
        default=True, description="Automatically contain detected threats"
    )

    SECURITY_INCIDENT_CONTACTS: List[Dict[str, str]] = Field(
        default=[
            {"role": "CISO", "contact": "security@company.com"},
            {"role": "SOC", "contact": "soc@company.com"},
            {"role": "DPO", "contact": "privacy@company.com"},
        ],
        description="Security incident notification contacts",
    )

    BREACH_NOTIFICATION_TIMEOUT_HOURS: conint(ge=1, le=72) = Field(
        default=72, description="Maximum time to notify of data breach"
    )

    # ================== BACKUP & RECOVERY ==================

    BACKUP_ENCRYPTION_REQUIRED: bool = Field(
        default=True, description="Encrypt all backups"
    )

    BACKUP_RETENTION_COPIES: conint(ge=3) = Field(
        default=30, description="Number of backup copies to retain"
    )

    BACKUP_GEOGRAPHIC_DISTRIBUTION: bool = Field(
        default=True, description="Distribute backups geographically"
    )

    BACKUP_INTEGRITY_CHECK: bool = Field(
        default=True, description="Verify backup integrity"
    )

    # ================== COMPLIANCE VALIDATION ==================

    @field_validator("MIN_TLS_VERSION")
    @classmethod
    def validate_tls_version(cls, v: str) -> str:
        allowed = ["1.2", "1.3"]
        if v not in allowed:
            raise ValueError(f"TLS version must be one of {allowed}")
        return v

    @field_validator("ALLOWED_IP_RANGES", "BLOCKED_IP_RANGES")
    @classmethod
    def validate_ip_ranges(cls, values: List[str]) -> List[str]:
        for v in values:
            try:
                ipaddress.ip_network(v)
            except ValueError:
                raise ValueError(f"Invalid IP range: {v}")
        return values

    @model_validator(mode="after")
    def validate_compliance_requirements(self) -> "EnterpriseSecurityConfig":
        """Ensure configuration meets compliance framework requirements"""
        # HIPAA Requirements
        if ComplianceFramework.HIPAA in self.COMPLIANCE_FRAMEWORKS:
            if self.AUDIT_LOG_RETENTION_DAYS < 2190:  # 6 years
                raise ValueError("HIPAA requires 6+ years audit retention")
            if not self.REQUIRE_ENCRYPTION_AT_REST:
                raise ValueError("HIPAA requires encryption at rest")

        # PCI-DSS Requirements
        if ComplianceFramework.PCI_DSS in self.COMPLIANCE_FRAMEWORKS:
            if self.MIN_PASSWORD_LENGTH < 7:
                raise ValueError("PCI-DSS requires minimum 7 character passwords")
            if self.PASSWORD_EXPIRY_DAYS > 90:
                raise ValueError("PCI-DSS requires password change every 90 days")

        # GDPR Requirements
        if ComplianceFramework.GDPR in self.COMPLIANCE_FRAMEWORKS:
            if not self.RIGHT_TO_ERASURE_ENABLED:
                raise ValueError("GDPR requires right to erasure support")
            if self.BREACH_NOTIFICATION_TIMEOUT_HOURS > 72:
                raise ValueError("GDPR requires breach notification within 72 hours")

        return self

    def export_compliance_report(self) -> Dict[str, Any]:
        """Generate compliance configuration report"""
        return {
            "timestamp": datetime.utcnow().isoformat(),
            "frameworks": [f.value for f in self.COMPLIANCE_FRAMEWORKS],
            "encryption": {
                "algorithm": self.ENCRYPTION_ALGORITHM,
                "key_length": self.MIN_KEY_LENGTH_BITS,
                "at_rest": self.REQUIRE_ENCRYPTION_AT_REST,
                "in_transit": self.REQUIRE_ENCRYPTION_IN_TRANSIT,
            },
            "authentication": {
                "mfa_required": self.MFA_REQUIRED,
                "password_length": self.MIN_PASSWORD_LENGTH,
                "session_timeout": self.SESSION_TIMEOUT_MINUTES,
            },
            "audit": {
                "retention_days": self.AUDIT_LOG_RETENTION_DAYS,
                "encryption": self.AUDIT_LOG_ENCRYPTION,
                "integrity_check": self.AUDIT_LOG_INTEGRITY_CHECK,
            },
        }

    # Pydantic V2 model_config replaces the old Config class
    model_config = SettingsConfigDict(
        env_file=".env.security", env_prefix="SECURITY_", case_sensitive=True
    )


# Singleton instance
_security_config: Optional[EnterpriseSecurityConfig] = None


def get_security_config() -> EnterpriseSecurityConfig:
    """Get or create the security configuration singleton"""
    global _security_config
    if _security_config is None:
        _security_config = EnterpriseSecurityConfig()
    return _security_config


# Export commonly used functions
def validate_compliance() -> bool:
    """Validate current configuration against compliance requirements"""
    config = get_security_config()
    try:
        # Pydantic v2 validates on instantiation, so just creating it is a check
        EnterpriseSecurityConfig.model_validate(config.model_dump())
        return True
    except ValueError as e:
        print(f"Compliance validation failed: {e}")
        return False


def get_encryption_key_age() -> timedelta:
    """Get the age of the current encryption key"""
    # This would integrate with your key management system
    get_security_config()
    # Placeholder - implement actual key age checking
    return timedelta(days=0)


def is_ip_allowed(ip: str) -> bool:
    """Check if an IP address is allowed"""
    config = get_security_config()

    # Check blocked ranges first
    for blocked_range in config.BLOCKED_IP_RANGES:
        if ipaddress.ip_address(ip) in ipaddress.ip_network(blocked_range):
            return False

    # If no allowed ranges specified, allow all (except blocked)
    if not config.ALLOWED_IP_RANGES:
        return True

    # Check if IP is in allowed ranges
    for allowed_range in config.ALLOWED_IP_RANGES:
        if ipaddress.ip_address(ip) in ipaddress.ip_network(allowed_range):
            return True

    return False
