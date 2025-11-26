"""
Test suite for omnicore_engine/security_integration.py
Tests enterprise security integration with FastAPI and OmniCore components.
"""

import os

# Add the parent directory to path for imports
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from omnicore_engine.security_integration import (
    AuthenticationRequest,
    EncryptionAdapter,
    Permission,
    SecurityEvent,
    SecurityIntegrationManager,
    SecurityMiddleware,
    SessionContext,
    UserRole,
    configure_app_security,
    get_security_integration_manager,
    secure_endpoint,
    validate_input,
)


class TestEnums:
    """Test enum definitions"""

    def test_user_roles(self):
        """Test UserRole enum values"""
        assert UserRole.ADMIN == "admin"
        assert UserRole.OPERATOR == "operator"
        assert UserRole.DEVELOPER == "developer"
        assert UserRole.AUDITOR == "auditor"
        assert UserRole.USER == "user"
        assert UserRole.SERVICE_ACCOUNT == "service_account"

    def test_permissions(self):
        """Test Permission enum values"""
        assert Permission.SYSTEM_ADMIN == "system:admin"
        assert Permission.DATA_READ == "data:read"
        assert Permission.PLUGIN_INSTALL == "plugin:install"
        assert Permission.AUDIT_READ == "audit:read"
        assert Permission.MESSAGE_PUBLISH == "message:publish"


class TestModels:
    """Test Pydantic models"""

    def test_authentication_request(self):
        """Test AuthenticationRequest model"""
        auth_req = AuthenticationRequest(
            username="testuser",
            password="Test@Password123",
            mfa_token="123456",
            device_fingerprint="abc123",
        )

        assert auth_req.username == "testuser"
        assert auth_req.password == "Test@Password123"
        assert auth_req.mfa_token == "123456"
        assert auth_req.device_fingerprint == "abc123"

    def test_session_context(self):
        """Test SessionContext model"""
        session = SessionContext(
            user_id="user123",
            username="testuser",
            roles=[UserRole.USER, UserRole.DEVELOPER],
            permissions={Permission.DATA_READ, Permission.DATA_WRITE},
            session_id="session_abc123",
            created_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
            ip_address="192.168.1.1",
            device_fingerprint="device123",
            mfa_verified=True,
        )

        assert session.user_id == "user123"
        assert UserRole.DEVELOPER in session.roles
        assert Permission.DATA_READ in session.permissions
        assert session.mfa_verified == True

    def test_security_event(self):
        """Test SecurityEvent model"""
        event = SecurityEvent(
            event_type="authentication_success",
            user_id="user123",
            ip_address="192.168.1.1",
            resource="/api/data",
            action="read",
            result="success",
            details={"additional": "info"},
        )

        assert event.event_type == "authentication_success"
        assert event.user_id == "user123"
        assert isinstance(event.timestamp, datetime)


class TestSecurityIntegrationManager:
    """Test SecurityIntegrationManager class"""

    @pytest.fixture
    def mock_config(self):
        """Create mock security config"""
        config = Mock()
        config.dict.return_value = {}
        config.MFA_REQUIRED = True
        config.SESSION_TIMEOUT_MINUTES = 30
        config.INVALIDATE_SESSION_ON_IP_CHANGE = True
        config.MAX_FAILED_LOGIN_ATTEMPTS = 5
        config.ACCOUNT_LOCKOUT_DURATION_MINUTES = 30
        config.API_ENDPOINT_LIMITS = {"/auth/login": {"per_minute": 5}}
        config.MAX_REQUEST_SIZE_BYTES = 10485760
        config.SECURITY_HEADERS = {
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
        }
        return config

    @pytest.fixture
    def mock_security_utils(self):
        """Create mock security utils"""
        utils = Mock()
        utils.verify_password.return_value = (True, False)
        utils.verify_totp.return_value = True
        utils.hash_password.return_value = "hashed_password"
        utils.generate_secure_token.return_value = "secure_token_123"
        utils._rate_limiter = Mock()
        utils._rate_limiter.is_allowed.return_value = True
        return utils

    @pytest.fixture
    def manager(self, mock_config, mock_security_utils):
        """Create SecurityIntegrationManager instance"""
        with patch(
            "omnicore_engine.security_integration.get_security_utils"
        ) as mock_get_utils:
            mock_get_utils.return_value = mock_security_utils

            manager = SecurityIntegrationManager(
                config=mock_config, db=Mock(), audit=Mock(), message_bus=Mock()
            )
            return manager

    def test_initialization(self, manager):
        """Test manager initialization"""
        assert manager.config is not None
        assert manager.security_utils is not None
        assert manager._sessions == {}
        assert manager._role_permissions is not None

    def test_rbac_initialization(self, manager):
        """Test RBAC permission mapping"""
        perms = manager._role_permissions

        # Admin should have all permissions
        assert len(perms[UserRole.ADMIN]) == len(Permission)

        # User should have limited permissions
        assert Permission.DATA_READ in perms[UserRole.USER]
        assert Permission.SYSTEM_ADMIN not in perms[UserRole.USER]

        # Auditor should have audit permissions
        assert Permission.AUDIT_READ in perms[UserRole.AUDITOR]
        assert Permission.AUDIT_EXPORT in perms[UserRole.AUDITOR]

    @pytest.mark.asyncio
    async def test_authenticate_success(self, manager):
        """Test successful authentication"""
        manager._get_user = AsyncMock(
            return_value={
                "username": "testuser",
                "password_hash": "hash",
                "roles": [UserRole.USER],
                "mfa_enabled": True,
                "mfa_secret": "secret",
            }
        )
        manager._is_account_locked = AsyncMock(return_value=False)
        manager._audit_security_event = AsyncMock()

        # Create a proper mock request object with headers that return strings
        request = Mock()
        request.client = Mock()
        request.client.host = "192.168.1.1"
        # Set up headers as a mock that returns string values for .get() calls
        request.headers = Mock()
        request.headers.get = Mock(side_effect=lambda key, default="": {
            "user-agent": "TestAgent/1.0",
            "accept-language": "en-US",
            "accept-encoding": "gzip",
        }.get(key, default))

        session = await manager.authenticate(
            username="testuser",
            password="password",
            mfa_token="123456",
            request=request,
        )

        assert session.user_id == "testuser"
        assert session.session_id == "secure_token_123"
        assert UserRole.USER in session.roles
        manager._audit_security_event.assert_called()

    @pytest.mark.asyncio
    async def test_authenticate_invalid_password(self, manager, mock_security_utils):
        """Test authentication with invalid password"""
        mock_security_utils.verify_password.return_value = (False, False)

        manager._get_user = AsyncMock(
            return_value={
                "username": "testuser",
                "password_hash": "hash",
                "roles": [UserRole.USER],
            }
        )
        manager._handle_failed_login = AsyncMock()
        manager._audit_security_event = AsyncMock()

        with pytest.raises(Exception) as exc_info:
            await manager.authenticate(username="testuser", password="wrong_password")

        manager._handle_failed_login.assert_called_once()

    @pytest.mark.asyncio
    async def test_authenticate_account_locked(self, manager):
        """Test authentication with locked account"""
        manager._get_user = AsyncMock(
            return_value={"username": "testuser", "password_hash": "hash"}
        )
        manager._is_account_locked = AsyncMock(return_value=True)

        with pytest.raises(Exception) as exc_info:
            await manager.authenticate(username="testuser", password="password")

    @pytest.mark.asyncio
    async def test_authorize_valid_permission(self, manager):
        """Test authorization with valid permission"""
        session = SessionContext(
            user_id="user123",
            username="testuser",
            roles=[UserRole.USER],
            permissions={Permission.DATA_READ},
            session_id="session123",
            created_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            ip_address="192.168.1.1",
        )

        manager._audit_security_event = AsyncMock()

        result = await manager.authorize(session, Permission.DATA_READ)
        assert result == True

    @pytest.mark.asyncio
    async def test_authorize_invalid_permission(self, manager):
        """Test authorization with invalid permission"""
        session = SessionContext(
            user_id="user123",
            username="testuser",
            roles=[UserRole.USER],
            permissions={Permission.DATA_READ},
            session_id="session123",
            created_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            ip_address="192.168.1.1",
        )

        manager._audit_security_event = AsyncMock()

        result = await manager.authorize(session, Permission.SYSTEM_ADMIN)
        assert result == False
        manager._audit_security_event.assert_called()

    @pytest.mark.asyncio
    async def test_authorize_expired_session(self, manager):
        """Test authorization with expired session"""
        session = SessionContext(
            user_id="user123",
            username="testuser",
            roles=[UserRole.USER],
            permissions={Permission.DATA_READ},
            session_id="session123",
            created_at=datetime.now(timezone.utc) - timedelta(hours=2),
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
            ip_address="192.168.1.1",
        )

        with pytest.raises(Exception) as exc_info:
            await manager.authorize(session, Permission.DATA_READ)

    @pytest.mark.asyncio
    async def test_get_current_session_valid(self, manager):
        """Test getting current session with valid token"""
        session = SessionContext(
            user_id="user123",
            username="testuser",
            roles=[UserRole.USER],
            permissions={Permission.DATA_READ},
            session_id="token123",
            created_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            ip_address="192.168.1.1",
        )

        manager._sessions["token123"] = session

        credentials = Mock()
        credentials.credentials = "token123"

        request = Mock()
        request.client.host = "192.168.1.1"

        result = await manager.get_current_session(credentials, request)
        assert result == session

    @pytest.mark.asyncio
    async def test_get_current_session_invalid_token(self, manager):
        """Test getting current session with invalid token"""
        credentials = Mock()
        credentials.credentials = "invalid_token"

        with pytest.raises(HTTPException) as exc_info:
            await manager.get_current_session(credentials)

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_get_current_session_ip_mismatch(self, manager):
        """Test session validation with IP mismatch"""
        session = SessionContext(
            user_id="user123",
            username="testuser",
            roles=[UserRole.USER],
            permissions={Permission.DATA_READ},
            session_id="token123",
            created_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            ip_address="192.168.1.1",
        )

        manager._sessions["token123"] = session
        manager._audit_security_event = AsyncMock()

        credentials = Mock()
        credentials.credentials = "token123"

        request = Mock()
        request.client.host = "192.168.1.2"  # Different IP

        with pytest.raises(HTTPException) as exc_info:
            await manager.get_current_session(credentials, request)

        assert exc_info.value.status_code == 401
        manager._audit_security_event.assert_called()


class TestSecurityMiddleware:
    """Test SecurityMiddleware class"""

    @pytest.fixture
    def middleware(self):
        """Create middleware instance"""
        app = Mock()
        manager = Mock()
        manager.config = Mock()
        manager.config.API_ENDPOINT_LIMITS = {"/api/limited": {"per_minute": 5}}
        manager.config.MAX_REQUEST_SIZE_BYTES = 1024
        manager.config.SECURITY_HEADERS = {"X-Frame-Options": "DENY"}
        manager.security_utils = Mock()
        # Use rate_limiter (without underscore) to match the middleware implementation
        manager.security_utils.rate_limiter = Mock()
        manager.security_utils.rate_limiter.is_allowed = Mock(return_value=True)
        manager._audit_security_event = AsyncMock()

        return SecurityMiddleware(app, manager)

    @pytest.mark.asyncio
    async def test_dispatch_success(self, middleware):
        """Test successful request dispatch"""
        request = Mock()
        request.client.host = "192.168.1.1"
        request.method = "GET"
        request.url.path = "/api/test"
        request.headers = {}
        request.query_params = {}

        response = Mock()
        response.status_code = 200
        response.headers = {}

        call_next = AsyncMock(return_value=response)

        result = await middleware.dispatch(request, call_next)

        assert result == response
        assert "X-Frame-Options" in response.headers

    @pytest.mark.asyncio
    async def test_rate_limit_exceeded(self, middleware):
        """Test rate limiting"""
        # Set rate limiter to return False (not allowed)
        middleware.rate_limiter.is_allowed = Mock(return_value=False)

        request = Mock()
        request.client.host = "192.168.1.1"
        request.method = "GET"
        request.url.path = "/api/limited"

        call_next = AsyncMock()

        response = await middleware.dispatch(request, call_next)

        # Check response is JSONResponse with 429 status
        assert response.status_code == 429
        call_next.assert_not_called()

    @pytest.mark.asyncio
    async def test_request_size_validation(self, middleware):
        """Test request size validation"""
        request = Mock()
        request.client.host = "192.168.1.1"
        request.method = "POST"
        request.url.path = "/api/test"
        request.headers = {"content-length": "2048"}  # Exceeds limit

        call_next = AsyncMock()

        response = await middleware.dispatch(request, call_next)

        assert response.status_code == 400
        call_next.assert_not_called()


class TestEncryptionAdapter:
    """Test EncryptionAdapter class"""

    def test_encrypt(self):
        """Test encryption adapter"""
        mock_utils = Mock()
        mock_utils.encrypt_data.return_value = "encrypted_data"

        adapter = EncryptionAdapter(mock_utils)
        result = adapter.encrypt(b"test_data")

        mock_utils.encrypt_data.assert_called_once_with(b"test_data")
        assert result == b"encrypted_data"

    def test_decrypt(self):
        """Test decryption adapter"""
        mock_utils = Mock()
        mock_utils.decrypt_data.return_value = b"decrypted_data"

        adapter = EncryptionAdapter(mock_utils)
        result = adapter.decrypt(b"encrypted_data")

        mock_utils.decrypt_data.assert_called_once_with("encrypted_data")
        assert result == b"decrypted_data"


class TestDecorators:
    """Test security decorators"""

    @pytest.mark.asyncio
    async def test_secure_endpoint_with_permission(self):
        """Test secure_endpoint decorator with permission check"""

        @secure_endpoint(permission=Permission.DATA_READ)
        async def test_function(session=None):
            return "success"

        session = Mock()
        session.user_id = "user123"
        session.ip_address = "192.168.1.1"
        session.permissions = {Permission.DATA_READ}
        session.roles = []

        with patch(
            "omnicore_engine.security_integration.get_security_integration_manager"
        ) as mock_get:
            manager = Mock()
            manager.authorize = AsyncMock(return_value=True)
            manager._audit_security_event = AsyncMock()
            mock_get.return_value = manager

            result = await test_function(session=session)
            assert result == "success"
            manager.authorize.assert_called_once()

    @pytest.mark.asyncio
    async def test_validate_input_decorator(self):
        """Test validate_input decorator"""

        class TestSchema(BaseModel):
            name: str
            value: int

        @validate_input(TestSchema)
        async def test_function(data):
            return data

        result = await test_function(data={"name": "test", "value": 42})
        assert isinstance(result, TestSchema)
        assert result.name == "test"
        assert result.value == 42


class TestFastAPIIntegration:
    """Test FastAPI integration"""

    def test_configure_app_security(self):
        """Test app security configuration"""
        app = FastAPI()

        with patch(
            "omnicore_engine.security_integration.get_security_integration_manager"
        ) as mock_get:
            manager = Mock()
            mock_get.return_value = manager

            result = configure_app_security(app)

            # Check endpoints are added
            routes = [route.path for route in app.routes]
            assert "/auth/login" in routes
            assert "/auth/logout" in routes
            assert "/auth/me" in routes
            assert "/api/admin/users" in routes
            assert "/api/plugins/install" in routes


class TestSingleton:
    """Test singleton pattern"""

    def test_get_security_integration_manager_singleton(self):
        """Test manager singleton"""
        with patch(
            "omnicore_engine.security_integration._security_integration_manager", None
        ):
            manager1 = get_security_integration_manager()
            manager2 = get_security_integration_manager()

            assert manager1 is manager2


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--asyncio-mode=auto"])
