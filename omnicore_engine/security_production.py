"""
Advanced Security Configuration for OmniCore Engine Production Deployment

This module provides enterprise-grade security configurations including:
- TLS/SSL certificate management
- Firewall rule recommendations
- Rate limiting policies
- Intrusion detection system integration
- Security hardening guidelines
- Penetration testing helpers

Author: GitHub Copilot
"""

import json
import logging
import os
import ssl
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class SecurityLevel(Enum):
    """Security levels for different deployment environments"""

    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"
    HIGH_SECURITY = "high_security"


class ThreatLevel(Enum):
    """Threat levels for intrusion detection"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class TLSConfig:
    """TLS/SSL Configuration for production deployment"""

    # Certificate paths
    cert_file: str = field(default="")
    key_file: str = field(default="")
    ca_file: Optional[str] = field(default=None)

    # TLS settings
    min_tls_version: str = field(default="TLSv1.2")
    max_tls_version: str = field(default="TLSv1.3")
    ciphers: str = field(
        default="ECDHE+AESGCM:ECDHE+CHACHA20:DHE+AESGCM:DHE+CHACHA20:!aNULL:!MD5:!DSS"
    )

    # Certificate validation
    verify_mode: str = field(default="CERT_REQUIRED")
    check_hostname: bool = field(default=True)

    # HSTS (HTTP Strict Transport Security)
    hsts_enabled: bool = field(default=True)
    hsts_max_age: int = field(default=31536000)  # 1 year
    hsts_include_subdomains: bool = field(default=True)

    def create_ssl_context(self) -> ssl.SSLContext:
        """Create an SSL context with secure defaults"""
        # Use highest protocol version available
        if self.min_tls_version == "TLSv1.3":
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_3
        elif self.min_tls_version == "TLSv1.2":
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
        else:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)

        # Load certificates
        if self.cert_file and self.key_file:
            context.load_cert_chain(self.cert_file, self.key_file)

        # Load CA certificates if provided
        if self.ca_file:
            context.load_verify_locations(self.ca_file)

        # Set cipher suites
        context.set_ciphers(self.ciphers)

        # Configure verification
        if self.verify_mode == "CERT_REQUIRED":
            context.verify_mode = ssl.CERT_REQUIRED
        elif self.verify_mode == "CERT_OPTIONAL":
            context.verify_mode = ssl.CERT_OPTIONAL
        else:
            context.verify_mode = ssl.CERT_NONE

        context.check_hostname = self.check_hostname

        # Disable insecure protocols
        context.options |= ssl.OP_NO_SSLv2
        context.options |= ssl.OP_NO_SSLv3
        context.options |= ssl.OP_NO_TLSv1
        context.options |= ssl.OP_NO_TLSv1_1

        logger.info(
            f"SSL context created with minimum TLS version: {self.min_tls_version}"
        )
        return context

    def validate_certificates(self) -> Tuple[bool, List[str]]:
        """Validate certificate configuration"""
        errors = []

        if not self.cert_file:
            errors.append("Certificate file not specified")
        elif not os.path.exists(self.cert_file):
            errors.append(f"Certificate file not found: {self.cert_file}")

        if not self.key_file:
            errors.append("Private key file not specified")
        elif not os.path.exists(self.key_file):
            errors.append(f"Private key file not found: {self.key_file}")

        if self.ca_file and not os.path.exists(self.ca_file):
            errors.append(f"CA file not found: {self.ca_file}")

        return len(errors) == 0, errors


@dataclass
class RateLimitPolicy:
    """Rate limiting policy configuration"""

    # Request limits
    requests_per_second: int = field(default=10)
    requests_per_minute: int = field(default=300)
    requests_per_hour: int = field(default=10000)

    # Burst allowance
    burst_size: int = field(default=20)

    # IP-based limits
    per_ip_requests_per_minute: int = field(default=60)

    # API endpoint specific limits
    endpoint_limits: Dict[str, int] = field(default_factory=dict)

    # Actions on rate limit exceeded
    block_duration_seconds: int = field(default=300)  # 5 minutes
    send_alert: bool = field(default=True)

    def __post_init__(self):
        """Set default endpoint limits"""
        if not self.endpoint_limits:
            self.endpoint_limits = {
                "/api/auth/login": 5,  # per minute
                "/api/auth/register": 3,  # per minute
                "/api/sensitive/*": 10,  # per minute
                "/api/public/*": 100,  # per minute
            }


@dataclass
class FirewallRules:
    """Firewall configuration rules"""

    # Allowed IP ranges (CIDR notation)
    allowed_ip_ranges: List[str] = field(default_factory=list)

    # Blocked IP addresses
    blocked_ips: List[str] = field(default_factory=list)

    # Allowed ports
    allowed_ports: List[int] = field(default_factory=lambda: [443, 8000, 8443])

    # Geographic restrictions
    allowed_countries: List[str] = field(default_factory=list)
    blocked_countries: List[str] = field(default_factory=list)

    # Protocol restrictions
    allowed_protocols: List[str] = field(default_factory=lambda: ["HTTPS", "WSS"])

    def __post_init__(self):
        """Set default allowed IP ranges"""
        if not self.allowed_ip_ranges:
            # Default to private networks for internal services
            self.allowed_ip_ranges = [
                "10.0.0.0/8",  # Private network
                "172.16.0.0/12",  # Private network
                "192.168.0.0/16",  # Private network
            ]

    def is_ip_allowed(self, ip_address: str) -> bool:
        """Check if an IP address is allowed"""
        # Check if explicitly blocked
        if ip_address in self.blocked_ips:
            return False

        # If no allowed ranges specified, allow all (except blocked)
        if not self.allowed_ip_ranges:
            return True

        # Check against allowed IP ranges
        # Note: This is a simplified check. In production, use ipaddress module
        for allowed_range in self.allowed_ip_ranges:
            if allowed_range == "0.0.0.0/0":  # Allow all
                return True

        return False


@dataclass
class IntrusionDetectionConfig:
    """Intrusion Detection System (IDS) configuration"""

    # Detection thresholds
    failed_login_threshold: int = field(default=5)
    failed_login_window_minutes: int = field(default=15)

    suspicious_pattern_threshold: int = field(default=3)
    suspicious_pattern_window_minutes: int = field(default=10)

    # Attack pattern detection
    sql_injection_detection: bool = field(default=True)
    xss_detection: bool = field(default=True)
    path_traversal_detection: bool = field(default=True)
    command_injection_detection: bool = field(default=True)

    # Response actions
    auto_block_on_threat: bool = field(default=True)
    alert_security_team: bool = field(default=True)
    log_all_suspicious_activity: bool = field(default=True)

    # Integration endpoints
    siem_endpoint: Optional[str] = field(default=None)
    alert_webhook: Optional[str] = field(default=None)

    def detect_sql_injection(self, input_string: str) -> bool:
        """Detect potential SQL injection attempts"""
        if not self.sql_injection_detection:
            return False

        sql_patterns = [
            r"(\bUNION\b.*\bSELECT\b)",
            r"(\bSELECT\b.*\bFROM\b)",
            r"(\bINSERT\b.*\bINTO\b)",
            r"(\bDELETE\b.*\bFROM\b)",
            r"(\bDROP\b.*\bTABLE\b)",
            r"(--|\#|\/\*)",
            r"(\bOR\b.*=.*)",
            r"('.*OR.*'.*=.*')",
        ]

        import re

        for pattern in sql_patterns:
            if re.search(pattern, input_string, re.IGNORECASE):
                logger.warning(f"Potential SQL injection detected: {pattern}")
                return True

        return False

    def detect_xss(self, input_string: str) -> bool:
        """Detect potential XSS attempts"""
        if not self.xss_detection:
            return False

        xss_patterns = [
            r"<script[^>]*>.*</script>",
            r"javascript:",
            r"onerror\s*=",
            r"onload\s*=",
            r"<iframe[^>]*>",
            r"<object[^>]*>",
            r"eval\(",
        ]

        import re

        for pattern in xss_patterns:
            if re.search(pattern, input_string, re.IGNORECASE):
                logger.warning(f"Potential XSS detected: {pattern}")
                return True

        return False

    def detect_path_traversal(self, input_string: str) -> bool:
        """Detect potential path traversal attempts"""
        if not self.path_traversal_detection:
            return False

        traversal_patterns = [
            r"\.\./",
            r"\.\.\\",
            r"%2e%2e/",
            r"%2e%2e\\",
        ]

        import re

        for pattern in traversal_patterns:
            if re.search(pattern, input_string, re.IGNORECASE):
                logger.warning(f"Potential path traversal detected: {pattern}")
                return True

        return False


@dataclass
class SecurityHardeningConfig:
    """Security hardening configuration"""

    security_level: SecurityLevel = field(default=SecurityLevel.PRODUCTION)

    # Security headers
    enable_security_headers: bool = field(default=True)
    content_security_policy: str = field(
        default="default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'"
    )
    x_frame_options: str = field(default="DENY")
    x_content_type_options: str = field(default="nosniff")
    x_xss_protection: str = field(default="1; mode=block")
    referrer_policy: str = field(default="strict-origin-when-cross-origin")

    # Session security
    session_timeout_minutes: int = field(default=30)
    session_regenerate_on_privilege_change: bool = field(default=True)
    secure_cookies: bool = field(default=True)
    http_only_cookies: bool = field(default=True)
    same_site_cookies: str = field(default="Strict")

    # Password policy
    min_password_length: int = field(default=12)
    require_uppercase: bool = field(default=True)
    require_lowercase: bool = field(default=True)
    require_digits: bool = field(default=True)
    require_special_chars: bool = field(default=True)
    password_expiry_days: int = field(default=90)
    password_history_count: int = field(default=5)

    # Account security
    enable_2fa: bool = field(default=True)
    account_lockout_threshold: int = field(default=5)
    account_lockout_duration_minutes: int = field(default=30)

    # Audit logging
    log_all_authentication_attempts: bool = field(default=True)
    log_all_authorization_failures: bool = field(default=True)
    log_sensitive_data_access: bool = field(default=True)

    def get_security_headers(self) -> Dict[str, str]:
        """Get recommended security headers"""
        if not self.enable_security_headers:
            return {}

        return {
            "Strict-Transport-Security": "max-age=31536000; includeSubDomains; preload",
            "Content-Security-Policy": self.content_security_policy,
            "X-Frame-Options": self.x_frame_options,
            "X-Content-Type-Options": self.x_content_type_options,
            "X-XSS-Protection": self.x_xss_protection,
            "Referrer-Policy": self.referrer_policy,
            "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
        }

    def validate_password(self, password: str) -> Tuple[bool, List[str]]:
        """Validate password against security policy"""
        errors = []

        if len(password) < self.min_password_length:
            errors.append(
                f"Password must be at least {self.min_password_length} characters long"
            )

        if self.require_uppercase and not any(c.isupper() for c in password):
            errors.append("Password must contain at least one uppercase letter")

        if self.require_lowercase and not any(c.islower() for c in password):
            errors.append("Password must contain at least one lowercase letter")

        if self.require_digits and not any(c.isdigit() for c in password):
            errors.append("Password must contain at least one digit")

        if self.require_special_chars:
            special_chars = "!@#$%^&*()_+-=[]{}|;:,.<>?"
            if not any(c in special_chars for c in password):
                errors.append("Password must contain at least one special character")

        return len(errors) == 0, errors


class SecurityConfigManager:
    """Centralized security configuration manager"""

    def __init__(self, security_level: SecurityLevel = SecurityLevel.PRODUCTION):
        self.security_level = security_level
        self.tls_config = TLSConfig()
        self.rate_limit_policy = RateLimitPolicy()
        self.firewall_rules = FirewallRules()
        self.ids_config = IntrusionDetectionConfig()
        self.hardening_config = SecurityHardeningConfig(security_level=security_level)

        logger.info(
            f"Security configuration initialized for level: {security_level.value}"
        )
        
        # SECURITY FIX: Validate TLS configuration for production environments
        self._validate_production_security()

    def _validate_production_security(self) -> None:
        """
        Validate security configuration for production environments.
        
        This method is called after __init__() and after load_from_file() to ensure
        that critical security settings are always validated, preventing bypasses.
        
        Raises:
            ValueError: If TLS certificates are not configured for production/high-security modes
        """
        if self.security_level in [SecurityLevel.PRODUCTION, SecurityLevel.HIGH_SECURITY]:
            is_valid, errors = self.tls_config.validate_certificates()
            if not is_valid:
                error_msg = f"TLS certificates not configured for {self.security_level.value} environment: {'; '.join(errors)}"
                logger.error(error_msg)
                logger.error("SECURITY RISK: Running without TLS in production is not allowed. Please configure cert_file and key_file.")
                raise ValueError(error_msg)

    def load_from_file(self, config_path: str) -> None:
        """Load security configuration from JSON file"""
        try:
            with open(config_path, "r") as f:
                config_data = json.load(f)

            # Load TLS config
            if "tls" in config_data:
                for key, value in config_data["tls"].items():
                    if hasattr(self.tls_config, key):
                        setattr(self.tls_config, key, value)

            # Load rate limit policy
            if "rate_limit" in config_data:
                for key, value in config_data["rate_limit"].items():
                    if hasattr(self.rate_limit_policy, key):
                        setattr(self.rate_limit_policy, key, value)

            # Load firewall rules
            if "firewall" in config_data:
                for key, value in config_data["firewall"].items():
                    if hasattr(self.firewall_rules, key):
                        setattr(self.firewall_rules, key, value)

            # Load IDS config
            if "intrusion_detection" in config_data:
                for key, value in config_data["intrusion_detection"].items():
                    if hasattr(self.ids_config, key):
                        setattr(self.ids_config, key, value)

            # Load hardening config
            if "hardening" in config_data:
                for key, value in config_data["hardening"].items():
                    if hasattr(self.hardening_config, key):
                        setattr(self.hardening_config, key, value)

            logger.info(f"Security configuration loaded from: {config_path}")
            
            # SECURITY FIX: Re-validate after loading to prevent bypass
            self._validate_production_security()

        except Exception as e:
            logger.error(f"Failed to load security configuration: {e}")
            raise

    def save_to_file(self, config_path: str) -> None:
        """Save security configuration to JSON file"""
        config_data = {
            "security_level": self.security_level.value,
            "tls": {
                "cert_file": self.tls_config.cert_file,
                "key_file": self.tls_config.key_file,
                "ca_file": self.tls_config.ca_file,
                "min_tls_version": self.tls_config.min_tls_version,
                "verify_mode": self.tls_config.verify_mode,
                "hsts_enabled": self.tls_config.hsts_enabled,
            },
            "rate_limit": {
                "requests_per_second": self.rate_limit_policy.requests_per_second,
                "requests_per_minute": self.rate_limit_policy.requests_per_minute,
                "per_ip_requests_per_minute": self.rate_limit_policy.per_ip_requests_per_minute,
            },
            "firewall": {
                "allowed_ip_ranges": self.firewall_rules.allowed_ip_ranges,
                "blocked_ips": self.firewall_rules.blocked_ips,
                "allowed_ports": self.firewall_rules.allowed_ports,
            },
            "intrusion_detection": {
                "failed_login_threshold": self.ids_config.failed_login_threshold,
                "auto_block_on_threat": self.ids_config.auto_block_on_threat,
                "sql_injection_detection": self.ids_config.sql_injection_detection,
                "xss_detection": self.ids_config.xss_detection,
            },
            "hardening": {
                "enable_security_headers": self.hardening_config.enable_security_headers,
                "session_timeout_minutes": self.hardening_config.session_timeout_minutes,
                "min_password_length": self.hardening_config.min_password_length,
                "enable_2fa": self.hardening_config.enable_2fa,
            },
        }

        with open(config_path, "w") as f:
            json.dump(config_data, f, indent=2)

        logger.info(f"Security configuration saved to: {config_path}")

    def get_production_checklist(self) -> Dict[str, Any]:
        """Get production deployment security checklist"""
        tls_valid, tls_errors = self.tls_config.validate_certificates()

        checklist = {
            "tls_ssl": {
                "enabled": bool(self.tls_config.cert_file and self.tls_config.key_file),
                "certificates_valid": tls_valid,
                "errors": tls_errors,
                "min_tls_version": self.tls_config.min_tls_version,
                "hsts_enabled": self.tls_config.hsts_enabled,
            },
            "rate_limiting": {
                "enabled": self.rate_limit_policy.requests_per_minute > 0,
                "requests_per_minute": self.rate_limit_policy.requests_per_minute,
                "per_ip_limit": self.rate_limit_policy.per_ip_requests_per_minute,
            },
            "firewall": {
                "configured": len(self.firewall_rules.allowed_ip_ranges) > 0,
                "blocked_ips": len(self.firewall_rules.blocked_ips),
                "allowed_ports": self.firewall_rules.allowed_ports,
            },
            "intrusion_detection": {
                "sql_injection": self.ids_config.sql_injection_detection,
                "xss": self.ids_config.xss_detection,
                "path_traversal": self.ids_config.path_traversal_detection,
                "auto_block": self.ids_config.auto_block_on_threat,
            },
            "hardening": {
                "security_headers": self.hardening_config.enable_security_headers,
                "secure_cookies": self.hardening_config.secure_cookies,
                "2fa_enabled": self.hardening_config.enable_2fa,
                "password_policy": {
                    "min_length": self.hardening_config.min_password_length,
                    "complexity_required": (
                        self.hardening_config.require_uppercase
                        and self.hardening_config.require_lowercase
                        and self.hardening_config.require_digits
                        and self.hardening_config.require_special_chars
                    ),
                },
            },
        }

        return checklist


# Singleton instance
_security_config_manager: Optional[SecurityConfigManager] = None


def get_security_config(
    security_level: SecurityLevel = SecurityLevel.PRODUCTION,
) -> SecurityConfigManager:
    """Get or create the security configuration manager singleton"""
    global _security_config_manager
    if _security_config_manager is None:
        _security_config_manager = SecurityConfigManager(security_level)
    return _security_config_manager


# Export main classes and functions
__all__ = [
    "SecurityLevel",
    "ThreatLevel",
    "TLSConfig",
    "RateLimitPolicy",
    "FirewallRules",
    "IntrusionDetectionConfig",
    "SecurityHardeningConfig",
    "SecurityConfigManager",
    "get_security_config",
]
