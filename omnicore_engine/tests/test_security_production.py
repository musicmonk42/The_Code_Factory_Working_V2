"""
Tests for security_production module

Tests TLS/SSL, rate limiting, firewall rules, intrusion detection,
and security hardening configurations.
"""

import pytest
import tempfile
import os

from omnicore_engine.security_production import (
    SecurityLevel,
    TLSConfig,
    RateLimitPolicy,
    FirewallRules,
    IntrusionDetectionConfig,
    SecurityHardeningConfig,
    SecurityConfigManager,
    get_security_config,
)


class TestTLSConfig:
    """Test TLS/SSL configuration"""

    def test_tls_config_defaults(self):
        """Test TLS config has secure defaults"""
        config = TLSConfig()
        assert config.min_tls_version == "TLSv1.2"
        assert config.hsts_enabled is True
        assert config.check_hostname is True
        assert config.verify_mode == "CERT_REQUIRED"

    def test_certificate_validation_missing_files(self):
        """Test certificate validation with missing files"""
        config = TLSConfig(cert_file="nonexistent.crt", key_file="nonexistent.key")
        valid, errors = config.validate_certificates()
        assert not valid
        assert len(errors) >= 2


class TestRateLimitPolicy:
    """Test rate limiting policy"""

    def test_rate_limit_defaults(self):
        """Test rate limit policy has reasonable defaults"""
        policy = RateLimitPolicy()
        assert policy.requests_per_second == 10
        assert policy.requests_per_minute == 300
        assert policy.per_ip_requests_per_minute == 60
        assert policy.block_duration_seconds == 300

    def test_endpoint_specific_limits(self):
        """Test endpoint-specific rate limits"""
        policy = RateLimitPolicy()
        assert "/api/auth/login" in policy.endpoint_limits
        assert policy.endpoint_limits["/api/auth/login"] == 5
        assert policy.endpoint_limits["/api/public/*"] == 100


class TestFirewallRules:
    """Test firewall configuration"""

    def test_firewall_defaults(self):
        """Test firewall rules have secure defaults"""
        rules = FirewallRules()
        assert 443 in rules.allowed_ports
        assert 8443 in rules.allowed_ports
        assert "HTTPS" in rules.allowed_protocols
        assert len(rules.allowed_ip_ranges) > 0

    def test_ip_blocking(self):
        """Test IP address blocking"""
        rules = FirewallRules(blocked_ips=["192.168.1.100"])
        assert not rules.is_ip_allowed("192.168.1.100")

    def test_ip_allowing(self):
        """Test IP address allowing"""
        rules = FirewallRules(allowed_ip_ranges=["0.0.0.0/0"])
        assert rules.is_ip_allowed("8.8.8.8")


class TestIntrusionDetection:
    """Test intrusion detection system"""

    def test_ids_defaults(self):
        """Test IDS configuration defaults"""
        config = IntrusionDetectionConfig()
        assert config.sql_injection_detection is True
        assert config.xss_detection is True
        assert config.path_traversal_detection is True
        assert config.auto_block_on_threat is True

    def test_sql_injection_detection(self):
        """Test SQL injection pattern detection"""
        config = IntrusionDetectionConfig()

        # Should detect SQL injection attempts
        assert config.detect_sql_injection("' OR '1'='1")
        assert config.detect_sql_injection("UNION SELECT * FROM users")
        assert config.detect_sql_injection("DROP TABLE users--")

        # Should not flag normal input
        assert not config.detect_sql_injection("normal user input")

    def test_xss_detection(self):
        """Test XSS pattern detection"""
        config = IntrusionDetectionConfig()

        # Should detect XSS attempts
        assert config.detect_xss("<script>alert('xss')</script>")
        assert config.detect_xss("javascript:alert(1)")
        assert config.detect_xss("<img onerror='alert(1)'>")

        # Should not flag normal HTML
        assert not config.detect_xss("<p>normal paragraph</p>")

    def test_path_traversal_detection(self):
        """Test path traversal detection"""
        config = IntrusionDetectionConfig()

        # Should detect path traversal attempts
        assert config.detect_path_traversal("../../etc/passwd")
        assert config.detect_path_traversal("..\\..\\windows\\system32")

        # Should not flag normal paths
        assert not config.detect_path_traversal("/normal/path/to/file")

    def test_ids_disabled(self):
        """Test IDS with detection disabled"""
        config = IntrusionDetectionConfig(
            sql_injection_detection=False,
            xss_detection=False,
            path_traversal_detection=False,
        )

        # Should not detect anything when disabled
        assert not config.detect_sql_injection("' OR '1'='1")
        assert not config.detect_xss("<script>alert(1)</script>")
        assert not config.detect_path_traversal("../../etc/passwd")


class TestSecurityHardening:
    """Test security hardening configuration"""

    def test_hardening_defaults(self):
        """Test security hardening defaults"""
        config = SecurityHardeningConfig()
        assert config.security_level == SecurityLevel.PRODUCTION
        assert config.enable_security_headers is True
        assert config.secure_cookies is True
        assert config.enable_2fa is True
        assert config.min_password_length == 12

    def test_security_headers(self):
        """Test security headers generation"""
        config = SecurityHardeningConfig()
        headers = config.get_security_headers()

        assert "Strict-Transport-Security" in headers
        assert "Content-Security-Policy" in headers
        assert "X-Frame-Options" in headers
        assert "X-Content-Type-Options" in headers

    def test_password_validation_strong(self):
        """Test strong password validation"""
        config = SecurityHardeningConfig()
        valid, errors = config.validate_password("StrongP@ssw0rd123")
        assert valid
        assert len(errors) == 0

    def test_password_validation_weak(self):
        """Test weak password validation"""
        config = SecurityHardeningConfig()

        # Too short
        valid, errors = config.validate_password("short")
        assert not valid
        assert any("12 characters" in err for err in errors)

        # No uppercase
        valid, errors = config.validate_password("alllowercase123!")
        assert not valid
        assert any("uppercase" in err for err in errors)

        # No digits
        valid, errors = config.validate_password("NoDigitsHere!")
        assert not valid
        assert any("digit" in err for err in errors)

        # No special chars
        valid, errors = config.validate_password("NoSpecialChars123")
        assert not valid
        assert any("special character" in err for err in errors)


class TestSecurityConfigManager:
    """Test security configuration manager"""

    def test_manager_initialization(self):
        """Test manager initializes with correct defaults"""
        manager = SecurityConfigManager(SecurityLevel.PRODUCTION)
        assert manager.security_level == SecurityLevel.PRODUCTION
        assert manager.tls_config is not None
        assert manager.rate_limit_policy is not None
        assert manager.firewall_rules is not None
        assert manager.ids_config is not None
        assert manager.hardening_config is not None

    def test_production_checklist(self):
        """Test production deployment checklist generation"""
        manager = SecurityConfigManager(SecurityLevel.PRODUCTION)
        checklist = manager.get_production_checklist()

        assert "tls_ssl" in checklist
        assert "rate_limiting" in checklist
        assert "firewall" in checklist
        assert "intrusion_detection" in checklist
        assert "hardening" in checklist

    def test_config_save_load(self):
        """Test configuration save and load"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "security_config.json")

            # Create and configure manager
            manager = SecurityConfigManager(SecurityLevel.HIGH_SECURITY)
            manager.tls_config.cert_file = "/path/to/cert.pem"
            manager.tls_config.key_file = "/path/to/key.pem"
            manager.rate_limit_policy.requests_per_minute = 500

            # Save configuration
            manager.save_to_file(config_path)
            assert os.path.exists(config_path)

            # Load into new manager
            new_manager = SecurityConfigManager(SecurityLevel.DEVELOPMENT)
            new_manager.load_from_file(config_path)

            # Verify loaded correctly
            assert new_manager.tls_config.cert_file == "/path/to/cert.pem"
            assert new_manager.tls_config.key_file == "/path/to/key.pem"
            assert new_manager.rate_limit_policy.requests_per_minute == 500

    def test_singleton_getter(self):
        """Test singleton getter function"""
        manager1 = get_security_config()
        manager2 = get_security_config()
        assert manager1 is manager2


class TestSecurityLevels:
    """Test different security levels"""

    def test_development_level(self):
        """Test development security level"""
        manager = SecurityConfigManager(SecurityLevel.DEVELOPMENT)
        assert manager.security_level == SecurityLevel.DEVELOPMENT

    def test_staging_level(self):
        """Test staging security level"""
        manager = SecurityConfigManager(SecurityLevel.STAGING)
        assert manager.security_level == SecurityLevel.STAGING

    def test_production_level(self):
        """Test production security level"""
        manager = SecurityConfigManager(SecurityLevel.PRODUCTION)
        assert manager.security_level == SecurityLevel.PRODUCTION

    def test_high_security_level(self):
        """Test high security level"""
        manager = SecurityConfigManager(SecurityLevel.HIGH_SECURITY)
        assert manager.security_level == SecurityLevel.HIGH_SECURITY


class TestIntegration:
    """Integration tests for security configurations"""

    def test_complete_production_setup(self):
        """Test complete production security setup"""
        manager = SecurityConfigManager(SecurityLevel.PRODUCTION)

        # Configure TLS
        manager.tls_config.min_tls_version = "TLSv1.3"
        manager.tls_config.hsts_enabled = True

        # Configure rate limiting
        manager.rate_limit_policy.requests_per_minute = 1000
        manager.rate_limit_policy.per_ip_requests_per_minute = 100

        # Configure firewall
        manager.firewall_rules.allowed_ports = [443, 8443]
        manager.firewall_rules.allowed_protocols = ["HTTPS"]

        # Configure IDS
        manager.ids_config.auto_block_on_threat = True
        manager.ids_config.alert_security_team = True

        # Configure hardening
        manager.hardening_config.enable_2fa = True
        manager.hardening_config.min_password_length = 16

        # Verify complete setup
        checklist = manager.get_production_checklist()
        assert checklist["rate_limiting"]["enabled"]
        assert checklist["intrusion_detection"]["sql_injection"]
        assert checklist["hardening"]["2fa_enabled"]

    def test_threat_detection_workflow(self):
        """Test complete threat detection workflow"""
        manager = SecurityConfigManager(SecurityLevel.PRODUCTION)
        ids = manager.ids_config

        # Simulate multiple attack types
        malicious_inputs = [
            "' OR '1'='1",  # SQL injection
            "<script>alert(1)</script>",  # XSS
            "../../etc/passwd",  # Path traversal
        ]

        threats_detected = 0
        for input_str in malicious_inputs:
            if (
                ids.detect_sql_injection(input_str)
                or ids.detect_xss(input_str)
                or ids.detect_path_traversal(input_str)
            ):
                threats_detected += 1

        assert threats_detected == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
