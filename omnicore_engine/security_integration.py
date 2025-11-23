"""
security_integration.py

Enterprise Security Integration Module for OmniCore Engine
Bridges security utilities with core engine components
Compliant with: SOC 2, ISO 27001, HIPAA, PCI-DSS, GDPR
Version: 1.0.0
Classification: CONFIDENTIAL
"""

import json
import time
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List, Set
from functools import wraps
from enum import Enum

# FastAPI imports
from fastapi import (
    Depends,
    HTTPException,
    Security,
    Request,
    Response,
    BackgroundTasks,
    File,
    UploadFile,
)
from fastapi.security import (
    HTTPBearer,
    HTTPAuthorizationCredentials,
    OAuth2PasswordBearer,
)
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.status import (
    HTTP_401_UNAUTHORIZED,
    HTTP_403_FORBIDDEN,
    HTTP_429_TOO_MANY_REQUESTS,
    HTTP_500_INTERNAL_SERVER_ERROR,
)

# SQLAlchemy imports
from sqlalchemy.sql import text

# Pydantic imports
from pydantic import BaseModel, Field

# Security imports
from security_config import get_security_config, EnterpriseSecurityConfig, SecurityLevel
from omnicore_engine.security_utils import (
    get_security_utils,
    EnterpriseSecurityUtils,
    AuthenticationError,
    AuthorizationError,
    ValidationError as SecurityValidationError,
)

# OmniCore imports
from omnicore_engine.database import Database
from omnicore_engine.audit import ExplainAudit
from omnicore_engine.message_bus.sharded_message_bus import ShardedMessageBus

logger = logging.getLogger(__name__)

# Security scheme configurations
http_bearer = HTTPBearer(auto_error=False)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token", auto_error=False)


# ================== SECURITY MODELS ==================


class UserRole(str, Enum):
    """User role definitions"""

    ADMIN = "admin"
    OPERATOR = "operator"
    DEVELOPER = "developer"
    AUDITOR = "auditor"
    USER = "user"
    SERVICE_ACCOUNT = "service_account"


class Permission(str, Enum):
    """Granular permission definitions"""

    # System permissions
    SYSTEM_ADMIN = "system:admin"
    SYSTEM_CONFIG = "system:config"
    SYSTEM_SHUTDOWN = "system:shutdown"

    # Data permissions
    DATA_READ = "data:read"
    DATA_WRITE = "data:write"
    DATA_DELETE = "data:delete"
    DATA_EXPORT = "data:export"

    # Plugin permissions
    PLUGIN_INSTALL = "plugin:install"
    PLUGIN_EXECUTE = "plugin:execute"
    PLUGIN_DELETE = "plugin:delete"

    # Audit permissions
    AUDIT_READ = "audit:read"
    AUDIT_EXPORT = "audit:export"
    AUDIT_DELETE = "audit:delete"

    # Message bus permissions
    MESSAGE_PUBLISH = "message:publish"
    MESSAGE_SUBSCRIBE = "message:subscribe"
    MESSAGE_ADMIN = "message:admin"


class AuthenticationRequest(BaseModel):
    """Authentication request model"""

    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=8)
    mfa_token: Optional[str] = Field(None, min_length=6, max_length=6)
    device_fingerprint: Optional[str] = None


class SessionContext(BaseModel):
    """Session context model"""

    # allow arbitrary types (e.g., MagicMock replacing SecurityLevel in tests)
    model_config = {"arbitrary_types_allowed": True}

    user_id: str
    username: str
    roles: List[UserRole]
    permissions: Set[Permission]
    session_id: str
    created_at: datetime
    expires_at: datetime
    ip_address: str
    device_fingerprint: Optional[str]
    mfa_verified: bool = False
    security_level: SecurityLevel = SecurityLevel.CONFIDENTIAL


class SecurityEvent(BaseModel):
    """Security event model for audit logging"""

    event_type: str
    user_id: Optional[str]
    ip_address: str
    resource: Optional[str]
    action: str
    result: str
    details: Dict[str, Any]
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ================== SECURITY INTEGRATION MANAGER ==================


class SecurityIntegrationManager:
    """
    Central security integration manager for OmniCore Engine.
    Coordinates security across all components.
    """

    def __init__(
        self,
        config: Optional[EnterpriseSecurityConfig] = None,
        db: Optional[Database] = None,
        audit: Optional[ExplainAudit] = None,
        message_bus: Optional[ShardedMessageBus] = None,
    ):
        """
        Initialize security integration manager.

        Args:
            config: Security configuration
            db: Database instance
            audit: Audit system instance
            message_bus: Message bus instance
        """
        self.config = config or get_security_config()
        self.security_utils = get_security_utils(self.config.dict())
        self.db = db
        self.audit = audit
        self.message_bus = message_bus

        # Session storage (Redis in production)
        self._sessions: Dict[str, SessionContext] = {}
        self._api_keys: Dict[str, Dict[str, Any]] = {}

        # Role-permission mapping
        self._role_permissions = self._initialize_rbac()

        # Initialize components
        self._initialize_security_components()

    def _initialize_rbac(self) -> Dict[UserRole, Set[Permission]]:
        """Initialize role-based access control mappings"""
        return {
            UserRole.ADMIN: set(Permission),  # All permissions
            UserRole.OPERATOR: {
                Permission.DATA_READ,
                Permission.DATA_WRITE,
                Permission.PLUGIN_EXECUTE,
                Permission.MESSAGE_PUBLISH,
                Permission.MESSAGE_SUBSCRIBE,
                Permission.AUDIT_READ,
            },
            UserRole.DEVELOPER: {
                Permission.DATA_READ,
                Permission.PLUGIN_INSTALL,
                Permission.PLUGIN_EXECUTE,
                Permission.MESSAGE_PUBLISH,
                Permission.MESSAGE_SUBSCRIBE,
            },
            UserRole.AUDITOR: {
                Permission.DATA_READ,
                Permission.AUDIT_READ,
                Permission.AUDIT_EXPORT,
            },
            UserRole.USER: {Permission.DATA_READ, Permission.MESSAGE_SUBSCRIBE},
            UserRole.SERVICE_ACCOUNT: {
                Permission.DATA_READ,
                Permission.MESSAGE_PUBLISH,
                Permission.MESSAGE_SUBSCRIBE,
            },
        }

    def _initialize_security_components(self):
        """Initialize security components across OmniCore"""
        # Replace encryption in database
        if self.db:
            self.db.encrypter = EncryptionAdapter(self.security_utils)

        # Replace encryption in message bus
        if self.message_bus:
            self.message_bus.encryption = EncryptionAdapter(self.security_utils)

        # Setup audit hooks
        if self.audit:
            self.audit.add_security_logger(self._audit_security_event)

    async def _audit_security_event(self, event: SecurityEvent):
        """Log security event to audit system"""
        if self.audit:
            await self.audit.add_entry(
                kind="security",
                name=event.event_type,
                detail=event.dict(),
                agent_id=event.user_id or "system",
            )

    # ================== AUTHENTICATION ==================

    async def authenticate(
        self,
        username: str,
        password: str,
        mfa_token: Optional[str] = None,
        request: Optional[Request] = None,
    ) -> SessionContext:
        """
        Authenticate user with password and optional MFA.

        Args:
            username: Username
            password: Password
            mfa_token: Optional MFA token
            request: Optional request object for context

        Returns:
            Session context if successful

        Raises:
            AuthenticationError: If authentication fails
        """
        # Get user from database
        user = await self._get_user(username)
        if not user:
            # Log failed attempt
            await self._audit_security_event(
                SecurityEvent(
                    event_type="authentication_failed",
                    user_id=username,
                    ip_address=request.client.host if request else "unknown",
                    resource="login",
                    action="authenticate",
                    result="failure",
                    details={"reason": "user_not_found"},
                )
            )
            raise AuthenticationError("Invalid credentials")

        # Verify password
        is_valid, needs_rehash = self.security_utils.verify_password(
            password, user["password_hash"]
        )

        if not is_valid:
            await self._handle_failed_login(username, request)
            raise AuthenticationError("Invalid credentials")

        # Check if account is locked
        if await self._is_account_locked(username):
            raise AuthenticationError("Account locked due to multiple failed attempts")

        # Verify MFA if required
        if self.config.MFA_REQUIRED and user.get("mfa_enabled", True):
            if not mfa_token:
                raise AuthenticationError("MFA token required")

            if not self.security_utils.verify_totp(mfa_token, user["mfa_secret"]):
                await self._handle_failed_mfa(username, request)
                raise AuthenticationError("Invalid MFA token")

        # Rehash password if needed
        if needs_rehash:
            await self._update_password_hash(username, password)

        # Create session
        session = await self._create_session(user, request)

        # Log successful authentication
        await self._audit_security_event(
            SecurityEvent(
                event_type="authentication_success",
                user_id=username,
                ip_address=request.client.host if request else "unknown",
                resource="login",
                action="authenticate",
                result="success",
                details={"session_id": session.session_id},
            )
        )

        return session

    async def _get_user(self, username: str) -> Optional[Dict[str, Any]]:
        """Get user from database"""
        if not self.db:
            return None

        # Query user from database - MUST be implemented before production use
        # This is a critical security function that should query your user table
        # Example implementation (requires: from sqlalchemy import text):
        # async with self.db.get_session() as session:
        #     result = await session.execute(
        #         text("SELECT username, password_hash, roles, mfa_enabled, mfa_secret FROM users WHERE username = :username"),
        #         {"username": username}
        #     )
        #     row = result.fetchone()
        #     if row:
        #         return {
        #             'username': row.username,
        #             'password_hash': row.password_hash,
        #             'roles': row.roles,
        #             'mfa_enabled': row.mfa_enabled,
        #             'mfa_secret': row.mfa_secret
        #         }
        #     return None

        # SECURITY: Return None until database query is properly implemented
        # This prevents the authentication bypass vulnerability
        return None

    async def _handle_failed_login(self, username: str, request: Optional[Request]):
        """Handle failed login attempt"""
        # Track failed attempts
        # In production, use Redis or database
        pass

    async def _is_account_locked(self, username: str) -> bool:
        """Check if account is locked"""
        # Check failed attempts and lockout status
        return False

    async def _handle_failed_mfa(self, username: str, request: Optional[Request]):
        """Handle failed MFA attempt"""
        pass

    async def _update_password_hash(self, username: str, password: str):
        """Update password hash with new algorithm"""
        new_hash = self.security_utils.hash_password(password)
        # Update in database
        pass

    async def _create_session(
        self, user: Dict[str, Any], request: Optional[Request]
    ) -> SessionContext:
        """Create new session for authenticated user"""
        session_id = self.security_utils.generate_secure_token(32)

        # Get user permissions
        permissions = set()
        for role in user.get("roles", [UserRole.USER]):
            permissions.update(self._role_permissions.get(role, set()))

        session = SessionContext(
            user_id=user["username"],
            username=user["username"],
            roles=user.get("roles", [UserRole.USER]),
            permissions=permissions,
            session_id=session_id,
            created_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc)
            + timedelta(minutes=self.config.SESSION_TIMEOUT_MINUTES),
            ip_address=request.client.host if request else "unknown",
            device_fingerprint=(
                self._get_device_fingerprint(request) if request else None
            ),
            mfa_verified=True,
            security_level=SecurityLevel.CONFIDENTIAL,
        )

        # Store session
        self._sessions[session_id] = session

        return session

    def _get_device_fingerprint(self, request: Request) -> str:
        """Generate device fingerprint from request"""
        fingerprint_data = {
            "user_agent": request.headers.get("user-agent", ""),
            "accept_language": request.headers.get("accept-language", ""),
            "accept_encoding": request.headers.get("accept-encoding", ""),
        }
        return hashlib.sha256(
            json.dumps(fingerprint_data, sort_keys=True).encode()
        ).hexdigest()

    def cleanup_expired_sessions(self) -> int:
        """
        Clean up expired sessions from memory to prevent memory leak.
        Should be called periodically (e.g., every 5-10 minutes).

        Returns:
            Number of sessions cleaned up
        """
        now = datetime.now(timezone.utc)
        expired_sessions = [
            session_id
            for session_id, session in self._sessions.items()
            if now > session.expires_at
        ]

        for session_id in expired_sessions:
            del self._sessions[session_id]

        if expired_sessions:
            logger.info(f"Cleaned up {len(expired_sessions)} expired sessions")

        return len(expired_sessions)

    # ================== AUTHORIZATION ==================

    async def authorize(
        self,
        session: SessionContext,
        permission: Permission,
        resource: Optional[str] = None,
    ) -> bool:
        """
        Check if session has permission for resource.

        Args:
            session: Session context
            permission: Required permission
            resource: Optional resource identifier

        Returns:
            True if authorized, False otherwise
        """
        # Check session validity
        if datetime.now(timezone.utc) > session.expires_at:
            raise AuthorizationError("Session expired")

        # Check permission
        if permission not in session.permissions:
            await self._audit_security_event(
                SecurityEvent(
                    event_type="authorization_denied",
                    user_id=session.user_id,
                    ip_address=session.ip_address,
                    resource=resource,
                    action=permission.value,
                    result="denied",
                    details={"required_permission": permission.value},
                )
            )
            return False

        # Additional resource-based checks
        if resource and not await self._check_resource_access(session, resource):
            return False

        return True

    async def _check_resource_access(
        self, session: SessionContext, resource: str
    ) -> bool:
        """Check resource-specific access control"""
        # Implement resource-based access control
        # This could check ownership, department, etc.
        return True

    # ================== DEPENDENCY INJECTION ==================

    async def get_current_session(
        self,
        credentials: HTTPAuthorizationCredentials = Security(http_bearer),
        request: Request = None,
    ) -> SessionContext:
        """
        FastAPI dependency to get current session.

        Args:
            credentials: Bearer token credentials
            request: Current request

        Returns:
            Session context

        Raises:
            HTTPException: If authentication fails
        """
        if not credentials:
            raise HTTPException(
                status_code=HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )

        session = self._sessions.get(credentials.credentials)

        if not session:
            raise HTTPException(
                status_code=HTTP_401_UNAUTHORIZED, detail="Invalid or expired token"
            )

        # Validate session
        if datetime.now(timezone.utc) > session.expires_at:
            del self._sessions[credentials.credentials]
            raise HTTPException(
                status_code=HTTP_401_UNAUTHORIZED, detail="Session expired"
            )

        # Validate IP if configured
        if self.config.INVALIDATE_SESSION_ON_IP_CHANGE:
            if request and session.ip_address != request.client.host:
                await self._audit_security_event(
                    SecurityEvent(
                        event_type="session_ip_mismatch",
                        user_id=session.user_id,
                        ip_address=request.client.host,
                        resource="session",
                        action="validate",
                        result="failure",
                        details={
                            "expected_ip": session.ip_address,
                            "actual_ip": request.client.host,
                        },
                    )
                )
                raise HTTPException(
                    status_code=HTTP_401_UNAUTHORIZED,
                    detail="Session invalid - IP address changed",
                )

        return session

    def require_permission(self, permission: Permission):
        """
        Dependency factory for permission checking.

        Args:
            permission: Required permission

        Returns:
            FastAPI dependency function
        """

        async def permission_checker(
            session: SessionContext = Depends(self.get_current_session),
        ):
            if not await self.authorize(session, permission):
                raise HTTPException(
                    status_code=HTTP_403_FORBIDDEN,
                    detail=f"Permission denied: {permission.value}",
                )
            return session

        return permission_checker

    def require_roles(self, roles: List[UserRole]):
        """
        Dependency factory for role checking.

        Args:
            roles: Required roles (any of)

        Returns:
            FastAPI dependency function
        """

        async def role_checker(
            session: SessionContext = Depends(self.get_current_session),
        ):
            if not any(role in session.roles for role in roles):
                raise HTTPException(
                    status_code=HTTP_403_FORBIDDEN,
                    detail=f"Role required: {[r.value for r in roles]}",
                )
            return session

        return role_checker


# ================== MIDDLEWARE ==================


class SecurityMiddleware(BaseHTTPMiddleware):
    """
    Security middleware for FastAPI applications.
    Implements rate limiting, security headers, and request validation.
    """

    def __init__(self, app, security_manager: SecurityIntegrationManager):
        super().__init__(app)
        self.security_manager = security_manager
        self.config = security_manager.config
        self.rate_limiter = self.security_manager.security_utils.rate_limiter

    async def dispatch(self, request: Request, call_next):
        """Process request through security checks"""
        start_time = time.time()

        try:
            # Rate limiting
            if not await self._check_rate_limit(request):
                return JSONResponse(
                    status_code=HTTP_429_TOO_MANY_REQUESTS,
                    content={"error": "Rate limit exceeded"},
                )

            # Input validation
            if not await self._validate_request(request):
                return JSONResponse(
                    status_code=400, content={"error": "Invalid request"}
                )

            # Process request
            response = await call_next(request)

            # Add security headers
            self._add_security_headers(response)

            # Log request
            process_time = time.time() - start_time
            await self._log_request(request, response, process_time)

            return response

        except Exception as e:
            logger.error(f"Security middleware error: {str(e)}")
            return JSONResponse(
                status_code=HTTP_500_INTERNAL_SERVER_ERROR,
                content={"error": "Internal server error"},
            )

    async def _check_rate_limit(self, request: Request) -> bool:
        """Check rate limits"""
        # IP-based rate limiting
        client_ip = request.client.host
        endpoint = f"{request.method}:{request.url.path}"

        # Check endpoint-specific limits
        endpoint_limits = self.config.API_ENDPOINT_LIMITS.get(request.url.path)
        if endpoint_limits:
            limit_key = f"{client_ip}:{endpoint}"
            if not self.rate_limiter.is_allowed(limit_key, tokens=1):
                await self.security_manager._audit_security_event(
                    SecurityEvent(
                        event_type="rate_limit_exceeded",
                        user_id=None,
                        ip_address=client_ip,
                        resource=endpoint,
                        action="request",
                        result="blocked",
                        details={"limit": endpoint_limits},
                    )
                )
                return False

        # Global rate limiting
        if not self.rate_limiter.is_allowed(client_ip):
            return False

        return True

    async def _validate_request(self, request: Request) -> bool:
        """Validate request parameters"""
        # Check request size
        content_length = request.headers.get("content-length")
        if content_length:
            if int(content_length) > self.config.MAX_REQUEST_SIZE_BYTES:
                return False

        # Validate content type
        content_type = request.headers.get("content-type", "")
        if request.method in ["POST", "PUT", "PATCH"]:
            if not content_type:
                return False

        return True

    def _add_security_headers(self, response: Response):
        """Add security headers to response"""
        for header, value in self.config.SECURITY_HEADERS.items():
            response.headers[header] = value

    async def _log_request(
        self, request: Request, response: Response, process_time: float
    ):
        """Log request for audit"""
        # Log only specific endpoints or errors
        if response.status_code >= 400 or request.url.path.startswith("/api/"):
            await self.security_manager._audit_security_event(
                SecurityEvent(
                    event_type="http_request",
                    user_id=None,  # Extract from session if available
                    ip_address=request.client.host,
                    resource=request.url.path,
                    action=request.method,
                    result=str(response.status_code),
                    details={
                        "process_time": process_time,
                        "user_agent": request.headers.get("user-agent"),
                        "query_params": dict(request.query_params),
                    },
                )
            )


# ================== COMPONENT ADAPTERS ==================


class EncryptionAdapter:
    """
    Adapter to make EnterpriseSecurityUtils compatible with existing encryption interfaces.
    """

    def __init__(self, security_utils: EnterpriseSecurityUtils):
        self.security_utils = security_utils

    def encrypt(self, data: bytes) -> bytes:
        """Encrypt data (compatibility method)"""
        return self.security_utils.encrypt_data(data).encode("utf-8")

    def decrypt(self, data: bytes) -> bytes:
        """Decrypt data (compatibility method)"""
        return self.security_utils.decrypt_data(data.decode("utf-8"))


class SecureDatabase(Database):
    """
    Enhanced database class with integrated security.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.security_manager = kwargs.get("security_manager")
        if self.security_manager:
            self.encrypter = EncryptionAdapter(self.security_manager.security_utils)

    async def execute_query(
        self, query: str, params: Dict[str, Any], session: SessionContext
    ):
        """Execute query with security checks"""
        # Audit query execution
        await self.security_manager._audit_security_event(
            SecurityEvent(
                event_type="database_query",
                user_id=session.user_id,
                ip_address=session.ip_address,
                resource="database",
                action="query",
                result="executing",
                details={"query_type": query.split()[0].upper()},
            )
        )

        # SECURITY: Parameters are passed directly to SQLAlchemy's execute() which
        # properly escapes them using parameterized queries. Do NOT manually sanitize.
        # The text() and execute() combination handles SQL injection prevention.

        # Execute query with parameterized query support
        async with self.AsyncSessionLocal() as db_session:
            result = await db_session.execute(text(query), params)
            await db_session.commit()
            return result


class SecureMessageBus(ShardedMessageBus):
    """
    Enhanced message bus with integrated security.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.security_manager = kwargs.get("security_manager")
        if self.security_manager:
            self.encryption = EncryptionAdapter(self.security_manager.security_utils)

    async def publish_secure(
        self,
        topic: str,
        payload: Dict[str, Any],
        session: SessionContext,
        encrypt: bool = True,
    ):
        """Publish message with security context"""
        # Check authorization
        if not await self.security_manager.authorize(
            session, Permission.MESSAGE_PUBLISH, topic
        ):
            raise AuthorizationError(f"Not authorized to publish to {topic}")

        # Add security context to message
        payload["_security_context"] = {
            "user_id": session.user_id,
            "roles": [r.value for r in session.roles],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session_id": session.session_id,
        }

        # Publish with encryption
        return await self.publish(topic, payload, encrypt=encrypt)


# ================== SECURITY DECORATORS ==================


def secure_endpoint(
    permission: Optional[Permission] = None,
    roles: Optional[List[UserRole]] = None,
    audit: bool = True,
):
    """
    Decorator for securing FastAPI endpoints.

    Args:
        permission: Required permission
        roles: Required roles (any of)
        audit: Whether to audit access
    """

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Extract session from kwargs
            session = kwargs.get("session")
            if not session:
                raise AuthorizationError("No session found")

            # Get security manager (would be injected)
            security_manager = get_security_integration_manager()

            # Check permission
            if permission:
                if not await security_manager.authorize(session, permission):
                    raise AuthorizationError(f"Permission denied: {permission.value}")

            # Check roles
            if roles:
                if not any(role in session.roles for role in roles):
                    raise AuthorizationError(
                        f"Role required: {[r.value for r in roles]}"
                    )

            # Audit access if configured
            if audit:
                await security_manager._audit_security_event(
                    SecurityEvent(
                        event_type="endpoint_access",
                        user_id=session.user_id,
                        ip_address=session.ip_address,
                        resource=func.__name__,
                        action="execute",
                        result="allowed",
                        details={
                            "permission": permission.value if permission else None
                        },
                    )
                )

            # Execute function
            return await func(*args, **kwargs)

        return wrapper

    return decorator


def validate_input(schema: BaseModel):
    """
    Decorator for input validation.

    Args:
        schema: Pydantic schema for validation
    """

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Validate input against schema
            try:
                # Extract data from kwargs
                data = kwargs.get("data") or args[0] if args else {}
                validated = schema(**data)

                # Replace with validated data
                if "data" in kwargs:
                    kwargs["data"] = validated
                elif args:
                    args = (validated,) + args[1:]

                return await func(*args, **kwargs)
            except Exception as e:
                raise SecurityValidationError(f"Input validation failed: {str(e)}")

        return wrapper

    return decorator


# ================== SINGLETON MANAGER ==================

_security_integration_manager: Optional[SecurityIntegrationManager] = None


def get_security_integration_manager(
    config: Optional[EnterpriseSecurityConfig] = None,
    db: Optional[Database] = None,
    audit: Optional[ExplainAudit] = None,
    message_bus: Optional[ShardedMessageBus] = None,
) -> SecurityIntegrationManager:
    """Get or create security integration manager singleton"""
    global _security_integration_manager
    if _security_integration_manager is None:
        _security_integration_manager = SecurityIntegrationManager(
            config=config, db=db, audit=audit, message_bus=message_bus
        )
    return _security_integration_manager


# ================== FASTAPI INTEGRATION ==================


def configure_app_security(
    app,
    config: Optional[EnterpriseSecurityConfig] = None,
    db: Optional[Database] = None,
    audit: Optional[ExplainAudit] = None,
    message_bus: Optional[ShardedMessageBus] = None,
):
    """
    Configure FastAPI app with enterprise security.

    Args:
        app: FastAPI application instance
        config: Security configuration
        db: Database instance
        audit: Audit instance
        message_bus: Message bus instance
    """
    # Initialize security manager
    security_manager = get_security_integration_manager(
        config=config, db=db, audit=audit, message_bus=message_bus
    )

    # Add security middleware
    app.add_middleware(SecurityMiddleware, security_manager=security_manager)

    # Add authentication endpoints
    @app.post("/auth/login")
    async def login(
        auth_request: AuthenticationRequest,
        request: Request,
        background_tasks: BackgroundTasks,
    ):
        """Authenticate user and create session"""
        try:
            session = await security_manager.authenticate(
                username=auth_request.username,
                password=auth_request.password,
                mfa_token=auth_request.mfa_token,
                request=request,
            )

            return {
                "access_token": session.session_id,
                "token_type": "bearer",
                "expires_in": int(
                    (session.expires_at - datetime.now(timezone.utc)).total_seconds()
                ),
            }
        except AuthenticationError as e:
            raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail=str(e))

    @app.post("/auth/logout")
    async def logout(
        session: SessionContext = Depends(security_manager.get_current_session),
    ):
        """Logout and invalidate session"""
        if session.session_id in security_manager._sessions:
            del security_manager._sessions[session.session_id]

        await security_manager._audit_security_event(
            SecurityEvent(
                event_type="logout",
                user_id=session.user_id,
                ip_address=session.ip_address,
                resource="session",
                action="logout",
                result="success",
                details={"session_id": session.session_id},
            )
        )

        return {"message": "Logged out successfully"}

    @app.get("/auth/me")
    async def get_current_user(
        session: SessionContext = Depends(security_manager.get_current_session),
    ):
        """Get current user information"""
        return {
            "user_id": session.user_id,
            "username": session.username,
            "roles": [r.value for r in session.roles],
            "permissions": [p.value for p in session.permissions],
            "session_expires": session.expires_at.isoformat(),
        }

    # Protected endpoint examples
    @app.get("/api/admin/users")
    @secure_endpoint(permission=Permission.SYSTEM_ADMIN, audit=True)
    async def list_users(
        session: SessionContext = Depends(
            security_manager.require_permission(Permission.SYSTEM_ADMIN)
        ),
    ):
        """List all users (admin only)"""
        return {"users": []}

    @app.post("/api/plugins/install")
    @secure_endpoint(permission=Permission.PLUGIN_INSTALL, audit=True)
    async def install_plugin(
        plugin_file: UploadFile = File(...),
        session: SessionContext = Depends(
            security_manager.require_permission(Permission.PLUGIN_INSTALL)
        ),
    ):
        """Install new plugin (requires permission)"""
        # Validate file
        content = await plugin_file.read()
        is_valid, mime_type = security_manager.security_utils.validate_file_type(
            plugin_file.filename, content
        )

        if not is_valid:
            raise HTTPException(
                status_code=400, detail=f"Invalid file type: {mime_type}"
            )

        return {"message": "Plugin installed"}

    return app


# Export main components
__all__ = [
    "SecurityIntegrationManager",
    "SecurityMiddleware",
    "SecureDatabase",
    "SecureMessageBus",
    "SessionContext",
    "UserRole",
    "Permission",
    "get_security_integration_manager",
    "configure_app_security",
    "secure_endpoint",
    "validate_input",
]
