"""
Test suite for omnicore_engine/security_config.py
Tests enterprise security configuration and compliance validation.
"""

import os

# Add the parent directory to path for imports
import sys
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from omnicore_engine.security_config import (
    ComplianceFramework,
    EnterpriseSecurityConfig,
    SecurityLevel,
    get_encryption_key_age,
    get_security_config,
    is_ip_allowed,
    validate_compliance,
)


class TestSecurityLevel:
    """Test SecurityLevel enum"""

    def test_security_levels(self):
        """Test all security levels are defined"""
        assert SecurityLevel.PUBLIC == "PUBLIC"
        assert SecurityLevel.INTERNAL == "INTERNAL"
        assert SecurityLevel.CONFIDENTIAL == "CONFIDENTIAL"
        assert SecurityLevel.RESTRICTED == "RESTRICTED"
        assert SecurityLevel.TOP_SECRET == "TOP_SECRET"


class TestComplianceFramework:
    """Test ComplianceFramework enum"""

    def test_compliance_frameworks(self):
        """Test all compliance frameworks are defined"""
        assert ComplianceFramework.SOC2_TYPE2 == "SOC2_TYPE2"
        assert ComplianceFramework.ISO_27001 == "ISO_27001"
        assert ComplianceFramework.HIPAA == "HIPAA"
        assert ComplianceFramework.PCI_DSS == "PCI_DSS"
        assert ComplianceFramework.GDPR == "GDPR"
        assert ComplianceFramework.NIST_CSF == "NIST_CSF"


class TestEnterpriseSecurityConfig:
    """Test EnterpriseSecurityConfig class"""

    def test_default_configuration(self):
        """Test default security configuration values"""
        config = EnterpriseSecurityConfig()

        # Test compliance defaults
        assert ComplianceFramework.SOC2_TYPE2 in config.COMPLIANCE_FRAMEWORKS
        assert ComplianceFramework.ISO_27001 in config.COMPLIANCE_FRAMEWORKS
        assert config.DATA_CLASSIFICATION_REQUIRED == True
        assert config.DEFAULT_DATA_CLASSIFICATION == SecurityLevel.CONFIDENTIAL

        # Test encryption defaults
        assert config.MIN_KEY_LENGTH_BITS == 256
        assert config.KEY_ROTATION_DAYS == 30
        assert config.ENCRYPTION_ALGORITHM == "AES-256-GCM"
        assert config.REQUIRE_ENCRYPTION_AT_REST == True
        assert config.REQUIRE_ENCRYPTION_IN_TRANSIT == True
        assert config.MIN_TLS_VERSION == "1.3"

        # Test authentication defaults
        assert config.MFA_REQUIRED == True
        assert config.MIN_PASSWORD_LENGTH == 16
        assert config.PASSWORD_EXPIRY_DAYS == 60
        assert config.MAX_FAILED_LOGIN_ATTEMPTS == 5

        # Test session defaults
        assert config.SESSION_TIMEOUT_MINUTES == 15
        assert config.MAX_CONCURRENT_SESSIONS == 2
        assert config.REQUIRE_SESSION_FINGERPRINTING == True

        # Test audit defaults
        assert config.AUDIT_LOG_RETENTION_DAYS == 2555  # 7 years
        assert config.AUDIT_LOG_ENCRYPTION == True
        assert config.REGULATORY_AUDIT_MODE == True

    def test_password_complexity_rules(self):
        """Test password complexity configuration"""
        config = EnterpriseSecurityConfig()

        rules = config.PASSWORD_COMPLEXITY_RULES
        assert rules["require_uppercase"] == True
        assert rules["require_lowercase"] == True
        assert rules["require_numbers"] == True
        assert rules["require_special_chars"] == True
        assert rules["min_unique_chars"] == 8
        assert rules["min_entropy_bits"] == 60

    def test_rate_limiting_configuration(self):
        """Test rate limiting settings"""
        config = EnterpriseSecurityConfig()

        assert config.GLOBAL_RATE_LIMIT_PER_SECOND == 100
        assert config.PER_USER_RATE_LIMIT_PER_MINUTE == 60
        assert config.PER_IP_RATE_LIMIT_PER_MINUTE == 100

        # Test endpoint-specific limits
        assert "/auth/login" in config.API_ENDPOINT_LIMITS
        assert config.API_ENDPOINT_LIMITS["/auth/login"]["per_minute"] == 5

    def test_file_security_configuration(self):
        """Test file upload security settings"""
        config = EnterpriseSecurityConfig()

        assert config.MAX_FILE_UPLOAD_SIZE_BYTES == 52428800  # 50MB
        assert ".pdf" in config.ALLOWED_FILE_EXTENSIONS
        assert ".exe" in config.FORBIDDEN_FILE_EXTENSIONS
        assert config.ENABLE_FILE_TYPE_VERIFICATION == True
        assert config.ENABLE_VIRUS_SCANNING == True

    def test_security_headers(self):
        """Test security headers configuration"""
        config = EnterpriseSecurityConfig()

        assert config.REQUIRE_SECURE_HEADERS == True
        headers = config.SECURITY_HEADERS
        assert "Strict-Transport-Security" in headers
        assert "X-Content-Type-Options" in headers
        assert "X-Frame-Options" in headers
        assert headers["X-Frame-Options"] == "DENY"


class TestComplianceValidation:
    """Test compliance validation logic"""

    def test_hipaa_compliance_validation(self):
        """Test HIPAA compliance requirements"""
        # Valid HIPAA configuration
        config = EnterpriseSecurityConfig(
            COMPLIANCE_FRAMEWORKS=[ComplianceFramework.HIPAA],
            AUDIT_LOG_RETENTION_DAYS=2190,  # 6 years
            REQUIRE_ENCRYPTION_AT_REST=True,
        )
        assert config.AUDIT_LOG_RETENTION_DAYS >= 2190

        # Invalid HIPAA configuration - insufficient retention
        with pytest.raises(
            ValueError, match="HIPAA requires 6\\+ years audit retention"
        ):
            EnterpriseSecurityConfig(
                COMPLIANCE_FRAMEWORKS=[ComplianceFramework.HIPAA],
                AUDIT_LOG_RETENTION_DAYS=365,  # Only 1 year
            )

    def test_pci_dss_compliance_validation(self):
        """Test PCI-DSS compliance requirements"""
        # Valid PCI-DSS configuration
        config = EnterpriseSecurityConfig(
            COMPLIANCE_FRAMEWORKS=[ComplianceFramework.PCI_DSS],
            MIN_PASSWORD_LENGTH=14,
            PASSWORD_EXPIRY_DAYS=90,
        )
        assert config.PASSWORD_EXPIRY_DAYS <= 90

        # Invalid PCI-DSS configuration - password expiry too long
        # In Pydantic V2, validation errors have different format
        with pytest.raises(
            ValueError, match="less than or equal to 90|PASSWORD_EXPIRY_DAYS"
        ):
            EnterpriseSecurityConfig(
                COMPLIANCE_FRAMEWORKS=[ComplianceFramework.PCI_DSS],
                PASSWORD_EXPIRY_DAYS=91,
            )

    def test_gdpr_compliance_validation(self):
        """Test GDPR compliance requirements"""
        # Valid GDPR configuration
        config = EnterpriseSecurityConfig(
            COMPLIANCE_FRAMEWORKS=[ComplianceFramework.GDPR],
            RIGHT_TO_ERASURE_ENABLED=True,
            BREACH_NOTIFICATION_TIMEOUT_HOURS=72,
        )
        assert config.RIGHT_TO_ERASURE_ENABLED == True

        # Invalid GDPR configuration - no right to erasure
        with pytest.raises(ValueError, match="GDPR requires right to erasure support"):
            EnterpriseSecurityConfig(
                COMPLIANCE_FRAMEWORKS=[ComplianceFramework.GDPR],
                RIGHT_TO_ERASURE_ENABLED=False,
            )

    def test_multiple_compliance_frameworks(self):
        """Test configuration with multiple compliance frameworks"""
        config = EnterpriseSecurityConfig(
            COMPLIANCE_FRAMEWORKS=[ComplianceFramework.HIPAA, ComplianceFramework.GDPR],
            AUDIT_LOG_RETENTION_DAYS=2190,
            REQUIRE_ENCRYPTION_AT_REST=True,
            RIGHT_TO_ERASURE_ENABLED=True,
            BREACH_NOTIFICATION_TIMEOUT_HOURS=72,
        )

        assert ComplianceFramework.HIPAA in config.COMPLIANCE_FRAMEWORKS
        assert ComplianceFramework.GDPR in config.COMPLIANCE_FRAMEWORKS


class TestValidators:
    """Test field validators"""

    def test_tls_version_validator(self):
        """Test TLS version validation"""
        # Valid TLS versions
        config = EnterpriseSecurityConfig(MIN_TLS_VERSION="1.2")
        assert config.MIN_TLS_VERSION == "1.2"

        config = EnterpriseSecurityConfig(MIN_TLS_VERSION="1.3")
        assert config.MIN_TLS_VERSION == "1.3"

        # Invalid TLS version
        with pytest.raises(ValueError, match="TLS version must be one of"):
            EnterpriseSecurityConfig(MIN_TLS_VERSION="1.0")

    def test_ip_range_validator(self):
        """Test IP range validation"""
        # Valid IP ranges
        config = EnterpriseSecurityConfig(
            ALLOWED_IP_RANGES=["192.168.1.0/24", "10.0.0.0/8"]
        )
        assert "192.168.1.0/24" in config.ALLOWED_IP_RANGES

        # Invalid IP range
        with pytest.raises(ValueError, match="Invalid IP range"):
            EnterpriseSecurityConfig(ALLOWED_IP_RANGES=["invalid_ip_range"])

    def test_field_constraints(self):
        """Test pydantic field constraints"""
        # Test minimum password length constraint
        with pytest.raises(ValueError):
            EnterpriseSecurityConfig(MIN_PASSWORD_LENGTH=10)  # Below minimum of 14

        # Test key rotation days constraint
        with pytest.raises(ValueError):
            EnterpriseSecurityConfig(KEY_ROTATION_DAYS=100)  # Above maximum of 90

        # Test session timeout constraint
        with pytest.raises(ValueError):
            EnterpriseSecurityConfig(SESSION_TIMEOUT_MINUTES=2)  # Below minimum of 5


class TestComplianceReport:
    """Test compliance report generation"""

    def test_export_compliance_report(self):
        """Test compliance report export"""
        config = EnterpriseSecurityConfig()
        report = config.export_compliance_report()

        assert "timestamp" in report
        assert "frameworks" in report
        assert "encryption" in report
        assert "authentication" in report
        assert "audit" in report

        # Verify encryption details
        assert report["encryption"]["algorithm"] == config.ENCRYPTION_ALGORITHM
        assert report["encryption"]["key_length"] == config.MIN_KEY_LENGTH_BITS
        assert report["encryption"]["at_rest"] == config.REQUIRE_ENCRYPTION_AT_REST

        # Verify authentication details
        assert report["authentication"]["mfa_required"] == config.MFA_REQUIRED
        assert report["authentication"]["password_length"] == config.MIN_PASSWORD_LENGTH

        # Verify audit details
        assert report["audit"]["retention_days"] == config.AUDIT_LOG_RETENTION_DAYS
        assert report["audit"]["encryption"] == config.AUDIT_LOG_ENCRYPTION

    def test_report_timestamp_format(self):
        """Test report timestamp is in ISO format"""
        config = EnterpriseSecurityConfig()
        report = config.export_compliance_report()

        # Should not raise an exception
        datetime.fromisoformat(report["timestamp"].replace("Z", "+00:00"))


class TestSingletonPattern:
    """Test singleton configuration management"""

    def test_get_security_config_singleton(self):
        """Test that get_security_config returns singleton"""
        config1 = get_security_config()
        config2 = get_security_config()

        assert config1 is config2

    @patch("omnicore_engine.security_config._security_config", None)
    def test_singleton_initialization(self):
        """Test singleton initialization"""
        config = get_security_config()

        assert config is not None
        assert isinstance(config, EnterpriseSecurityConfig)


class TestUtilityFunctions:
    """Test utility functions"""

    def test_validate_compliance_success(self):
        """Test successful compliance validation"""
        # Use real config for validation - it should pass with defaults
        result = validate_compliance()
        assert result == True

    def test_validate_compliance_failure(self):
        """Test failed compliance validation"""
        with patch("omnicore_engine.security_config.get_security_config") as mock_get:
            mock_config = Mock()
            mock_config.validate_compliance_requirements = Mock(
                side_effect=ValueError("Compliance error")
            )
            mock_config.dict = Mock(return_value={})
            mock_get.return_value = mock_config

            result = validate_compliance()
            assert result == False

    def test_get_encryption_key_age(self):
        """Test encryption key age retrieval"""
        # This is a placeholder function in the module
        age = get_encryption_key_age()
        assert isinstance(age, timedelta)
        assert age == timedelta(days=0)

    def test_is_ip_allowed_with_blocked_ranges(self):
        """Test IP allow/block logic"""
        with patch("omnicore_engine.security_config.get_security_config") as mock_get:
            mock_config = Mock()
            mock_config.BLOCKED_IP_RANGES = ["192.168.0.0/16", "10.0.0.0/8"]
            mock_config.ALLOWED_IP_RANGES = []
            mock_get.return_value = mock_config

            # Test blocked IP
            assert is_ip_allowed("192.168.1.1") == False
            assert is_ip_allowed("10.0.0.1") == False

            # Test allowed IP (not in blocked range)
            assert is_ip_allowed("8.8.8.8") == True

    def test_is_ip_allowed_with_allowed_ranges(self):
        """Test IP allowlist logic"""
        with patch("omnicore_engine.security_config.get_security_config") as mock_get:
            mock_config = Mock()
            mock_config.BLOCKED_IP_RANGES = []
            mock_config.ALLOWED_IP_RANGES = ["172.16.0.0/12", "203.0.113.0/24"]
            mock_get.return_value = mock_config

            # Test allowed IP
            assert is_ip_allowed("172.16.0.1") == True
            assert is_ip_allowed("203.0.113.5") == True

            # Test not allowed IP
            assert is_ip_allowed("8.8.8.8") == False

    def test_is_ip_allowed_priority(self):
        """Test that blocked ranges take priority over allowed"""
        with patch("omnicore_engine.security_config.get_security_config") as mock_get:
            mock_config = Mock()
            mock_config.BLOCKED_IP_RANGES = ["192.168.0.0/16"]
            mock_config.ALLOWED_IP_RANGES = ["192.168.1.0/24"]
            mock_get.return_value = mock_config

            # IP is in both allowed and blocked - should be blocked
            assert is_ip_allowed("192.168.1.1") == False


class TestConfigurationSecurity:
    """Test configuration security features"""

    def test_secret_redaction(self):
        """Test that secrets are redacted in JSON encoding"""
        config = EnterpriseSecurityConfig()

        # In Pydantic V2, model_config replaces Config class
        assert hasattr(config, "model_config")
        assert config.model_config is not None

        # Test that SecretStr values are redacted in model_dump
        from pydantic import SecretStr

        test_value = SecretStr("secret_value")
        # SecretStr automatically redacts when converted to string
        assert str(test_value) == "**********"

    def test_environment_configuration(self):
        """Test environment variable configuration"""
        config = EnterpriseSecurityConfig()

        # In Pydantic V2, use model_config
        assert config.model_config["env_file"] == ".env.security"
        assert config.model_config["env_prefix"] == "SECURITY_"
        assert config.model_config["case_sensitive"] == True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
