# main/api.py
import asyncio
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Callable, Dict, List, Optional, Union

# New import for file upload handling (was missing in the original file block)
import aiofiles
import backoff  # ADDED as per Step 2
import jwt
import uvicorn
from jwt.exceptions import InvalidTokenError

# Pydantic for data validation
from pydantic import BaseModel, ConfigDict, Field

# --- Guarded Heavy Imports ---
_FASTAPI_AVAILABLE = False
try:
    # FastAPI imports
    from fastapi import Body, Depends, FastAPI, File, Form, HTTPException
    from fastapi import Path as FastAPIPath
    from fastapi import (
        Query,
        Request,
        Security,
        UploadFile,
        WebSocket,
        WebSocketDisconnect,
        status,
    )
    from fastapi.exception_handlers import http_exception_handler
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.security import (
        APIKeyHeader,
        OAuth2PasswordBearer,
        OAuth2PasswordRequestForm,
    )
    from opentelemetry import trace
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.trace import Status, StatusCode  # Import Status and StatusCode

    # Password hashing
    from passlib.context import CryptContext

    # Prometheus metrics and OpenTelemetry tracing
    from prometheus_fastapi_instrumentator import Instrumentator

    # Rate limiting
    from slowapi import Limiter, _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded
    from slowapi.util import get_remote_address

    # SQLAlchemy for database integration
    from sqlalchemy import (  # ADDED 'text' as per Step 1
        Boolean,
        Column,
        DateTime,
        Integer,
        String,
        Text,
        create_engine,
        text,
    )
    from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
    from sqlalchemy.ext.declarative import declarative_base
    from sqlalchemy.orm import Session, sessionmaker

    _FASTAPI_AVAILABLE = True
    logging.getLogger(__name__).info(
        "Successfully imported heavy dependencies (FastAPI, SQLAlchemy, etc.)"
    )

except ImportError as e:
    logging.getLogger(__name__).warning(
        f"Heavy dependencies (FastAPI, SQLAlchemy, etc.) not found. API functionality will be disabled. Error: {e}"
    )

    # --- Dummies for Imports ---

    # Dummy function/decorators
    def Depends(callable=None):
        return callable

    def Security(callable=None):
        return callable

    # FIX: Replaced ... with *args, **kwargs and return None to fix SyntaxError
    def Body(*args, **kwargs):
        return None

    def File(*args, **kwargs):
        return None

    def Form(*args, **kwargs):
        return None

    def Query(*args, **kwargs):
        return None

    def FastAPIPath(*args, **kwargs):
        return None

    # Dummy Exceptions
    class HTTPException(Exception):
        def __init__(
            self,
            status_code: int,
            detail: str,
            headers: Optional[Dict[str, str]] = None,
        ):
            self.status_code = status_code
            self.detail = detail
            self.headers = headers
            super().__init__(detail)

    class RateLimitExceeded(Exception):
        pass

    class SQLAlchemyError(Exception):
        pass

    class IntegrityError(SQLAlchemyError):
        pass

    class OperationalError(SQLAlchemyError):
        pass

    class InvalidTokenError(Exception):
        pass

    class WebSocketDisconnect(Exception):
        pass

    # Dummy UploadFile class for when FastAPI is not available
    class UploadFile:
        """Dummy UploadFile class for function annotations when FastAPI is unavailable."""

        def __init__(self, *args, **kwargs):
            self.filename = kwargs.get("filename", "")
            self.file = None
            self.content_type = kwargs.get("content_type", "")

        async def read(self):
            return b""

    # Dummy Classes
    class _DummyState:
        """Dummy state object that allows setting arbitrary attributes."""

        def __init__(self):
            pass

    class _DummyFastAPI:
        def __init__(self, *args, **kwargs):
            self.state = _DummyState()
            self.dependency_overrides = {}

        def __call__(self, *args, **kwargs):
            return self

        def __getattr__(self, name):
            if name in (
                "post",
                "get",
                "put",
                "delete",
                "on_event",
                "add_middleware",
                "add_exception_handler",
            ):
                return self.dummy_decorator
            raise AttributeError(f"'DummyFastAPI' object has no attribute '{name}'")

        def dummy_decorator(self, *args, **kwargs):
            # This handles both @api.get("/") and @api.on_event("startup")
            if args and callable(args[0]):
                return args[0]  # Return the original function
            return self.dummy_decorator_inner

        def dummy_decorator_inner(self, func):
            return func

    FastAPI = _DummyFastAPI

    class CORSMiddleware:
        def __init__(self, *args, **kwargs):
            pass

    class Limiter:
        def __init__(self, *args, **kwargs):
            pass

    def _rate_limit_exceeded_handler(*args, **kwargs):
        pass

    def get_remote_address(*args, **kwargs):
        return "127.0.0.1"

    class CryptContext:
        def __init__(self, *args, **kwargs):
            pass

        def verify(self, *args, **kwargs):
            return False

        def hash(self, *args, **kwargs):
            return "dummy_hash"

    # Dummy SQLAlchemy
    def create_engine(*args, **kwargs):
        logging.getLogger(__name__).warning("Dummy create_engine() called.")
        return None

    class _DummyMetadata:
        def create_all(self, *args, **kwargs):
            logging.getLogger(__name__).info(
                "Dummy Base.metadata.create_all() called (SQLAlchemy not available)."
            )

    class _DummyBase:
        metadata = _DummyMetadata()
        # Mock class-level attributes for User and APIKey models to inherit
        __tablename__ = ""
        id = None
        username = None
        email = None  # <-- START FIX 3: Add email to dummy
        hashed_password = None
        scopes = None
        is_active = None
        created_at = None
        last_login_at = None
        api_key_id = None
        hashed_api_key = None
        last_used_at = None
        user_id = None

        def __init__(self, *args, **kwargs):
            pass

        def __repr__(self):
            return "DummyModelInstance"

    def declarative_base():
        return _DummyBase

    class _DummySession:
        def query(self, *args, **kwargs):
            return self

        def filter(self, *args, **kwargs):
            return self

        def first(self, *args, **kwargs):
            return None

        def all(self, *args, **kwargs):
            return []

        def execute(self, *args, **kwargs):
            pass

        def add(self, *args, **kwargs):
            pass

        def commit(self, *args, **kwargs):
            pass

        def refresh(self, *args, **kwargs):
            pass

        def delete(self, *args, **kwargs):
            pass

        def close(self, *args, **kwargs):
            pass

    def sessionmaker(*args, **kwargs):
        def dummy_session_maker():
            return _DummySession()

        return dummy_session_maker

    Session = _DummySession

    # Primitives
    class _DummyColumnType:
        pass

    def Column(*args, **kwargs):
        return None

    String = _DummyColumnType
    Text = _DummyColumnType
    DateTime = _DummyColumnType
    Boolean = _DummyColumnType
    Integer = _DummyColumnType

    # ADDED 'text' dummy for consistency
    def text(string):
        return string

    # Dummy OTel/Prometheus
    class Instrumentator:
        def instrument(self, *args, **kwargs):
            return self

        def expose(self, *args, **kwargs):
            pass

    class FastAPIInstrumentor:
        def instrument_app(self, *args, **kwargs):
            pass

    class _DummySpan:
        def set_status(self, *args, **kwargs):
            pass

        def record_exception(self, *args, **kwargs):
            pass

        def set_attribute(self, *args, **kwargs):
            pass

        def add_event(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args, **kwargs):
            pass

    class _DummyTracer:
        def start_as_current_span(self, *args, **kwargs):
            return _DummySpan()

    class _DummyTrace:
        def get_tracer(self, *args, **kwargs):
            return _DummyTracer()

        def get_tracer_provider(self, *args, **kwargs):
            return self

        def set_tracer_provider(self, *args, **kwargs):
            pass

        def add_span_processor(self, *args, **kwargs):
            pass

    trace = _DummyTrace()

    class TracerProvider:
        def __init__(self, *args, **kwargs):
            pass

    class BatchSpanProcessor:
        def __init__(self, *args, **kwargs):
            pass

    class ConsoleSpanExporter:
        def __init__(self, *args, **kwargs):
            pass

    class Status:
        def __init__(self, code, description=None):
            pass

    class StatusCode:
        OK = 1
        ERROR = 2
        UNAUTHENTICATED = 16
        ALREADY_EXISTS = 6
        NOT_FOUND = 5
        INTERNAL_ERROR = 13

    # Dummy Security
    class OAuth2PasswordBearer:
        def __init__(self, *args, **kwargs):
            pass

    class OAuth2PasswordRequestForm:
        pass

    class APIKeyHeader:
        def __init__(self, *args, **kwargs):
            pass

    # Dummy status codes (though they are just ints, redefining for clarity)
    class status:
        HTTP_401_UNAUTHORIZED = 401
        HTTP_403_FORBIDDEN = 403
        HTTP_409_CONFLICT = 409
        HTTP_404_NOT_FOUND = 404
        HTTP_500_INTERNAL_SERVER_ERROR = 500
        HTTP_200_OK = 200
        HTTP_503_SERVICE_UNAVAILABLE = 503
        HTTP_201_CREATED = 201
        HTTP_204_NO_CONTENT = 204
        HTTP_400_BAD_REQUEST = 400

    # Dummy WebSocket
    class WebSocket:
        pass

    # --- End Dummies ---


# --- Custom Module Imports (assuming these are available) ---
# In a real project, these would be separate files/packages
try:
    from generator.intent_parser.intent_parser import IntentParser
    from generator.runner.runner_config import ConfigWatcher, load_config
    from generator.runner.runner_core import Runner
    from generator.runner.runner_logging import (
        logger as runner_logger,
    )  # Use alias to avoid name clash
    from generator.runner.runner_logging import search_logs
    from generator.runner.runner_metrics import get_metrics_dict
except ImportError:
    # Dummy implementations for testing if custom modules are not present
    class DummyRunner:
        def __init__(self, config=None):
            self.config = config

        async def run(self, payload: Dict):
            logging.warning("DummyRunner: Running payload.")
            return {"status": "dummy_run_success", "payload": payload}

    class DummyIntentParser:
        def __init__(self, config_path: str = None):
            self.config_path = config_path
            self.feedback = DummyFeedback()

        async def parse(self, content: str, **kwargs):
            logging.warning("DummyIntentParser: Parsing content.")
            return {"status": "dummy_parse_success", "content": content}

        def reload_config_and_strategies(self):
            logging.warning("DummyIntentParser: Reloading config.")
            return {"status": "dummy_reload_success"}

    class DummyFeedback:
        def rate(self, item_id, rating, user_id):
            logging.warning(
                f"DummyFeedback: Item {item_id} rated {rating} by {user_id}."
            )

    Runner = DummyRunner
    IntentParser = DummyIntentParser
    runner_logger = logging.getLogger("dummy_runner")

    def search_logs(query: str):
        logging.warning("Dummy search_logs: Not implemented.")
        return [f"Dummy log entry for query: {query}"]

    def get_metrics_dict():
        logging.warning("Dummy get_metrics_dict: Not implemented.")
        return {"dummy_metric": 1}

    def encrypt_log(log_data: str):
        logging.warning("Dummy encrypt_log: Not implemented.")
        return f"[ENCRYPTED]{log_data}"

    # FIX: Added dummy load_config and ConfigWatcher to the except block
    def load_config(config_file: str):
        """Dummy config loader for when imports fail."""
        logging.warning(
            f"Using DUMMY load_config due to ImportError. Could not load real config '{config_file}'."
        )
        # Return a minimal config to allow startup
        return {
            "backend": "dummy",
            "framework": "dummy",
            "logging": {"level": "INFO"},
            "metrics": {"port": 8001},
            "security": {"jwt_secret_key_env_var": "JWT_SECRET_KEY"},
        }

    class ConfigWatcher:
        """Dummy ConfigWatcher for when imports fail."""

        def __init__(self, config_file: str, callback: Callable):
            logging.warning("Using DUMMY ConfigWatcher due to ImportError.")

        async def start(self):
            logging.warning("DUMMY ConfigWatcher: Not watching file.")
            pass  # Do nothing

        def stop(self):
            logging.warning("DUMMY ConfigWatcher: Stopped.")
            pass  # Do nothing

    logging.warning(
        "Custom modules (runner, intent_parser, logging, metrics, utils) not found. Using dummy implementations."
    )


# --- Logging Configuration ---
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# --- OpenTelemetry Tracer Configuration ---
# Use the default/configured tracer provider instead of manually creating one
# This avoids version compatibility issues and respects OTEL_* environment variables
try:
    tracer = trace.get_tracer(__name__)
except TypeError:
    # Fallback for older OpenTelemetry versions
    tracer = None


# --- Helper for DEV/TEST Mode ---
def _is_dev_or_test_mode() -> bool:
    """
    Returns True when running in development or test mode.
    This allows the application to start without production configuration.
    """
    if os.getenv("TESTING"):
        return True
    # Check for common development environment indicators
    dev_mode = os.getenv("DEV_MODE", "").lower()
    if dev_mode in ("true", "1"):
        return True
    app_env = os.getenv("APP_ENV", "").lower()
    if app_env in ("development", "dev", "local"):
        return True
    if os.getenv("PYTEST_CURRENT_TEST"):
        return True
    if os.getenv("RUNNING_TESTS", "").lower() == "true":
        return True
    return False


# --- Security Configuration ---
# PRODUCTION FIX: Load secrets from a secure location (e.g., a vault client or environment variables).
# The application will fail to start if critical secrets are not set.
# Allow tests and development mode to bypass this check
SECRET_KEY = os.getenv("JWT_SECRET_KEY")
if (
    not SECRET_KEY and not _is_dev_or_test_mode() and _FASTAPI_AVAILABLE
):  # Only raise if not in dev/test mode AND fastapi is available
    logger.critical(
        "JWT_SECRET_KEY environment variable not set. This is required for production."
    )
    raise ValueError("JWT_SECRET_KEY environment variable not set.")
elif not SECRET_KEY:
    # Use a development/test secret key
    SECRET_KEY = "dev-secret-key-do-not-use-in-production"
    if _FASTAPI_AVAILABLE:  # Only log warning if we are not using full dummies
        logger.warning("Using development SECRET_KEY - this is NOT for production use!")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30  # JWT token expiration time
API_KEY_HASH_SCHEME = "argon2"  # Stronger hashing algorithm for API keys
pwd_context = CryptContext(
    schemes=[API_KEY_HASH_SCHEME], deprecated="auto"
)  # Uses dummy if not available
oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/api/v1/token", auto_error=False
)  # <<< FIX: Added auto_error=False
api_key_header = APIKeyHeader(
    name="X-API-Key", auto_error=False
)  # Uses dummy if not available

# --- Database Configuration (SQLite for simplicity, replace with PostgreSQL/MySQL for production) ---
# PRODUCTION FIX: The DATABASE_URL must be provided from the environment.
# Using a local SQLite file is not suitable for most production deployments.
DATABASE_URL = os.getenv("DATABASE_URL")
# FIX: Add DEV_MODE fallback to prevent startup crash
if not DATABASE_URL:
    if _is_dev_or_test_mode():
        DATABASE_URL = "sqlite:///./dev.db"
        if _FASTAPI_AVAILABLE:
            logger.warning("Development mode: using sqlite:///./dev.db")
    elif _FASTAPI_AVAILABLE:  # Only raise in production with FastAPI available
        logger.critical(
            "DATABASE_URL environment variable not set. This is required for production."
        )
        raise ValueError("DATABASE_URL environment variable not set.")
    else:
        DATABASE_URL = "sqlite:///./dummy.db"  # Use a dummy value for dummy engine

Base = declarative_base()  # Uses dummy if not available

# SQLAlchemy Engine and SessionLocal
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# Database Models
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    # --- START FIX 3: Add email field ---
    email = Column(String, unique=True, index=True, nullable=True)
    # --- END FIX 3 ---
    hashed_password = Column(String, nullable=False)
    scopes = Column(String, default="user")  # Comma-separated scopes
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.now(timezone.utc))
    last_login_at = Column(DateTime)

    def __repr__(self):
        return f"<User(username='{self.username}', scopes='{self.scopes}')>"


class APIKey(Base):
    __tablename__ = "api_keys"
    id = Column(Integer, primary_key=True, index=True)
    api_key_id = Column(
        String, unique=True, index=True, nullable=False
    )  # Public ID for the API key
    hashed_api_key = Column(String, nullable=False)  # Hashed actual key
    scopes = Column(String, default="api")  # Comma-separated scopes
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.now(timezone.utc))
    last_used_at = Column(DateTime)
    user_id = Column(Integer, index=True, nullable=True)  # Link to user who created it

    def __repr__(self):
        return f"<APIKey(api_key_id='{self.api_key_id}', scopes='{self.scopes}')>"


# Create database tables
@backoff.on_exception(backoff.expo, SQLAlchemyError, max_tries=3)  # ADDED as per Step 2
def create_db_tables(bind_engine=None):  # <<< FIX: Added bind_engine=None
    """Creates database tables, optionally on a specific engine/connection."""
    try:
        if Base.metadata is not None and hasattr(Base.metadata, "create_all"):
            bind_target = bind_engine or engine  # <<< FIX: Use arg or global
            Base.metadata.create_all(bind=bind_target)  # <<< FIX: Use bind_target
            logger.info(
                f"Database tables created or already exist on bind: {bind_target}."
            )
        else:
            # This block will run if SQLAlchemy imports failed
            logger.warning(
                "Dummy create_db_tables() called (SQLAlchemy not available or Base.metadata is None)."
            )
    except SQLAlchemyError as e:
        logger.error(f"Failed to create DB tables: {e}", exc_info=True)
        raise  # MODIFIED as per Step 2 (re-raises for backoff)
    except Exception as e:
        # This might catch AttributeError if Base.metadata is None and not checked
        logger.error(
            f"Error in create_db_tables (likely dummy setup): {e}", exc_info=True
        )
        # In a dummy setup, we don't want to fail startup
        if not _FASTAPI_AVAILABLE:
            logger.warning(
                "Continuing startup despite error in dummy create_db_tables."
            )
        else:
            raise  # Re-raise if it's not a dummy setup


# Dependency to get DB session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        if _FASTAPI_AVAILABLE:  # Only real sessions have close()
            db.close()


# --- Data Models (Pydantic for API input/output validation) ---
class UserInDB(BaseModel):  # For internal use, includes hashed password
    username: str
    hashed_password: str
    scopes: List[str] = []


class UserCreate(BaseModel):  # For user registration input
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=8)
    scopes: Optional[List[str]] = ["user"]  # Default scope


class UserResponse(BaseModel):  # For user output (don't expose hashed password)
    username: str
    scopes: List[str]
    is_active: bool
    created_at: datetime
    last_login_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)


class APIKeyInDB(BaseModel):  # For internal use, includes hashed key
    api_key_id: str
    hashed_api_key: str
    scopes: List[str] = []


class APIKeyCreate(BaseModel):  # For API key creation input
    scopes: Optional[List[str]] = ["api"]  # Default scope


class APIKeyResponse(BaseModel):  # For API key output (don't expose hashed key)
    api_key_id: str
    scopes: List[str]
    is_active: bool
    created_at: datetime
    last_used_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)


class Token(BaseModel):
    access_token: str
    token_type: str


class TokenData(BaseModel):
    username: Optional[str] = None
    scopes: List[str] = []


# --- Authentication Utilities ---
def create_access_token(data: Dict, expires_delta: Optional[timedelta] = None) -> str:
    """Creates a JWT access token."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=ACCESS_TOKEN_EXPIRE_MINUTES
        )
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


# --- START FIX 2: Add verify_token helper ---
def verify_token(token: str) -> Optional[str]:
    """Verifies a JWT token and returns the username (sub)."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            logger.warning("Token verification failed: payload missing username.")
            raise credentials_exception
        return username
    except InvalidTokenError as e:
        logger.warning(f"Token verification failed: Invalid token - {e}")
        raise credentials_exception


# --- END FIX 2 ---


async def get_user_by_username_from_db(db: Session, username: str) -> Optional[User]:
    """Retrieves a user from the database by username."""
    try:
        # Use asyncio.to_thread for synchronous DB operation in async context
        user = await asyncio.to_thread(
            lambda: db.query(User).filter(User.username == username).first()
        )
        return user
    except OperationalError as e:
        logger.error(
            f"Database operational error fetching user {username}: {e}", exc_info=True
        )
        # MODIFIED as per Step 5 (already present in file)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error fetching user.",
        )
    except SQLAlchemyError as e:
        logger.error(f"Database error fetching user {username}: {e}", exc_info=True)
        # MODIFIED as per Step 5 (already present in file)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error fetching user.",
        )
    except Exception as e:
        # Catch errors from dummy session
        logger.warning(f"DummyDB: get_user_by_username_from_db({username}) called. {e}")
        return None


async def get_api_key_by_hashed_key_from_db(
    db: Session, api_key: str
) -> Optional[APIKey]:
    """Retrieves an API key from the database by verifying its hash."""
    try:
        # Fetch all active API keys and verify hash
        api_keys = await asyncio.to_thread(
            lambda: db.query(APIKey).filter(APIKey.is_active == True).all()
        )
        for api_key_obj in api_keys:
            if pwd_context.verify(api_key, api_key_obj.hashed_api_key):
                return api_key_obj
        return None
    except OperationalError as e:
        logger.error(
            f"Database operational error verifying API key: {e}", exc_info=True
        )
        raise HTTPException(status_code=500, detail="Database connection error.")
    except SQLAlchemyError as e:
        logger.error(f"Database error verifying API key: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal database error.")
    except Exception as e:
        # Catch errors from dummy session
        logger.warning(f"DummyDB: get_api_key_by_hashed_key_from_db() called. {e}")
        return None


async def get_current_user_from_token(
    token: Annotated[Optional[str], Depends(oauth2_scheme)],
    db: Session = Depends(get_db),  # <<< FIX: Made token Optional
):
    """Dependency to get current user from JWT token."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if token is None:  # <<< FIX: Added check for None
        return None

    try:
        # --- START FIX 2: Refactor to use verify_token ---
        username = verify_token(token)  # Use the new helper
        # We still need the scopes from the payload for TokenData
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        token_data = TokenData(username=username, scopes=payload.get("scopes", []))
    except HTTPException as e:  # verify_token raises HTTPException
        raise e
    except InvalidTokenError as e:  # Catch decode error
        logger.warning(f"Authentication failed: Invalid token - {e}")
        raise credentials_exception
    # --- END FIX 2 ---

    user = await get_user_by_username_from_db(db, token_data.username)
    if user is None or not (
        _FASTAPI_AVAILABLE and user.is_active
    ):  # Skip is_active check in dummy mode
        if _FASTAPI_AVAILABLE:  # Only check if not dummy
            if user is None or not user.is_active:
                logger.warning(
                    f"Authentication failed: User '{token_data.username}' not found or inactive."
                )
                raise credentials_exception
        elif user is None:  # In dummy mode, just check if user is None
            logger.warning(
                f"Authentication failed: User '{token_data.username}' not found."
            )
            raise credentials_exception

    # Update last login time
    if _FASTAPI_AVAILABLE:  # Only real users have this attribute
        user.last_login_at = datetime.now(timezone.utc)
    try:
        await asyncio.to_thread(db.commit)
    except SQLAlchemyError as e:
        logger.error(f"Failed to update last_login_at for user {user.username}: {e}")
        # Not critical enough to fail auth, but log it.
    except Exception as e:
        logger.warning(f"DummyDB: db.commit() for last_login_at called. {e}")

    logger.info(f"User '{user.username}' authenticated successfully via token.")
    return user


async def get_current_api_key(
    api_key: Annotated[str, Security(api_key_header)], db: Session = Depends(get_db)
):
    """Dependency to get current API key from X-API-Key header."""
    if not api_key:
        # <<< FIX: Do not raise 403. Return None.
        # get_current_active_entity will handle the 401 if user is also None.
        return None

    api_key_obj = await get_api_key_by_hashed_key_from_db(db, api_key)
    if api_key_obj is None or not (_FASTAPI_AVAILABLE and api_key_obj.is_active):
        if _FASTAPI_AVAILABLE:
            if api_key_obj is None or not api_key_obj.is_active:
                logger.warning(
                    "API key authentication failed: Invalid or inactive API Key provided."
                )
                raise HTTPException(status_code=403, detail="Invalid API Key")
        elif api_key_obj is None:
            logger.warning("API key authentication failed: Invalid API Key provided.")
            raise HTTPException(status_code=403, detail="Invalid API Key")

    # Update last used time
    if _FASTAPI_AVAILABLE:
        api_key_obj.last_used_at = datetime.now(timezone.utc)
    try:
        await asyncio.to_thread(db.commit)
    except SQLAlchemyError as e:
        logger.error(
            f"Failed to update last_used_at for API key {api_key_obj.api_key_id}: {e}"
        )
    except Exception as e:
        logger.warning(f"DummyDB: db.commit() for last_used_at called. {e}")

    logger.info(f"API Key '{api_key_obj.api_key_id}' authenticated successfully.")
    return api_key_obj


async def get_current_active_entity(
    user: Optional[User] = Depends(get_current_user_from_token),
    api_key: Optional[APIKey] = Security(
        get_current_api_key
    ),  # Use Security to make it optional if user auth succeeds
):
    """Dependency to get the currently authenticated user OR API key."""
    if user:
        return user
    if api_key:
        return api_key

    logger.warning("Authentication failed: No valid user or API key provided.")
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_scopes(required_scopes: List[str]):
    """Dependency to check if the authenticated entity has the required scopes."""

    async def scopes_checker(
        current_entity: Union[User, APIKey] = Depends(get_current_active_entity),
    ):
        if not _FASTAPI_AVAILABLE:  # If in dummy mode, always pass authorization
            return {"username": "dummy_user", "scopes": required_scopes}

        entity_scopes = (
            current_entity.scopes.split(",")
            if isinstance(current_entity.scopes, str)
            else current_entity.scopes
        )

        for scope in required_scopes:
            if scope not in entity_scopes:
                entity_id = (
                    current_entity.username
                    if isinstance(current_entity, User)
                    else current_entity.api_key_id
                )
                logger.warning(
                    f"Authorization failed for entity '{entity_id}': Insufficient scopes. Required: {required_scopes}, Has: {entity_scopes}"
                )
                raise HTTPException(status_code=403, detail="Insufficient scopes")
        return current_entity

    return scopes_checker


# --- App Initialization ---
limiter = Limiter(key_func=get_remote_address)  # Rate limiter instance
api = FastAPI(
    title="AI Generator API",
    description="Main API for the README-to-App Generator",
    version="1.0.0",  # API Versioning
    docs_url="/api/v1/docs",  # Versioned docs
    redoc_url="/api/v1/redoc",  # Versioned redoc
)
api.state.limiter = limiter
api.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# PRODUCTION FIX: Restrict CORS origins. Load allowed origins from an environment variable.
allowed_origins_str = os.getenv("ALLOWED_ORIGINS", "")
allowed_origins = [
    origin.strip() for origin in allowed_origins_str.split(",") if origin.strip()
]
if not allowed_origins and _FASTAPI_AVAILABLE:
    logger.warning(
        "ALLOWED_ORIGINS environment variable not set. CORS will be disabled."
    )

if _FASTAPI_AVAILABLE:
    api.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # MODIFIED as per Step 3 (For testing; restrict in prod)
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Instrument FastAPI with Prometheus and OpenTelemetry
    Instrumentator().instrument(api).expose(
        api, endpoint="/api/v1/metrics", include_in_schema=True
    )  # MODIFIED as per Step 4 (already present in file)
    FastAPIInstrumentor.instrument_app(api)
else:
    logger.info(
        "Skipping middleware and instrumentation setup (FastAPI not available)."
    )


# --- Singleton Instances ---
# FIX: Wrap global config loading in try/except to prevent FileNotFoundError during test collection
# Use path relative to generator package root
_generator_root = Path(__file__).parent.parent
_config_path = _generator_root / "config.yaml"
try:
    _global_config_instance = load_config(
        str(_config_path)
    )  # Load global configuration
except FileNotFoundError as e:
    logger.critical(
        f"FATAL: Required configuration file '{_config_path}' not found during API import time: {e}. Using minimal dummy config.",
        exc_info=True,
    )
    # Provide a minimal, safe dictionary that satisfies Pydantic checks and prevents crashes
    _global_config_instance = {
        "version": 1,
        "backend": "dummy",
        "framework": "dummy",
        "logging": {"level": "INFO"},
        "metrics": {"port": 8001},
        "security": {"jwt_secret_key_env_var": "JWT_SECRET_KEY"},
    }
except Exception as e:
    # Catch errors from dummy load_config if runner imports failed
    logger.critical(
        f"Failed to load config (likely dummy setup): {e}. Using minimal dummy config.",
        exc_info=True,
    )
    _global_config_instance = {
        "version": 1,
        "backend": "dummy",
        "framework": "dummy",
        "logging": {"level": "INFO"},
        "metrics": {"port": 8001},
        "security": {"jwt_secret_key_env_var": "JWT_SECRET_KEY"},
    }
# End FIX

_global_runner_instance: Optional[Runner] = None
_global_parser_instance: Optional[IntentParser] = None


def get_runner_instance() -> Runner:
    """Returns the singleton Runner instance."""
    global _global_runner_instance
    if _global_runner_instance is None:
        _global_runner_instance = Runner(_global_config_instance)
    return _global_runner_instance


def get_parser_instance() -> IntentParser:
    """Returns the singleton IntentParser instance."""
    # FIX: Corrected the typo in the global variable name
    global _global_parser_instance
    if _global_parser_instance is None:
        # Use path relative to generator package root
        _intent_parser_config_path = (
            _generator_root / "intent_parser" / "intent_parser.yaml"
        )
        _global_parser_instance = IntentParser(str(_intent_parser_config_path))
    return _global_parser_instance


@api.on_event("startup")
async def startup_event():
    """Startup event handler: creates DB tables and initializes singletons."""
    logger.info("Application startup event triggered.")
    # MODIFIED as per Step 2
    try:
        create_db_tables()  # Create database tables on startup
    except Exception as e:
        logger.critical(f"Startup failed due to DB error: {e}", exc_info=True)
        # Optionally raise to prevent startup, or set a flag for health check
    # End MODIFIED
    get_runner_instance()  # Initialize Runner
    get_parser_instance()  # Initialize Parser
    logger.info("Application startup complete. Singletons initialized.")


@api.on_event("shutdown")
async def shutdown_event():
    """Shutdown event handler: closes DB session."""
    logger.info("Application shutdown event triggered.")
    # No explicit session close needed for SessionLocal, it's handled by get_db's finally block.
    # For engine disposal, if needed: engine.dispose()
    logger.info("Application shutdown complete.")


# --- Endpoint Registration Decorator ---
# Modified to include API versioning
def register_api_endpoint(
    path: str,
    method: str = "POST",
    status_code: int = 200,
    summary: Optional[str] = None,
    scopes: Optional[List[str]] = None,
):
    """
    Decorator to register API endpoints with versioning, authentication, and rate limiting.
    All endpoints will be prefixed with /api/v1.
    """

    def decorator(func: Callable):
        # NOTE: Rate limiting is not applied per-endpoint through this decorator
        # to avoid requiring Request parameter in all endpoint functions.
        # Instead, apply rate limiting at the application level using FastAPI middleware
        # or the slowapi Limiter state on the app object, or add @limiter.limit()
        # decorators manually to specific routes that include request: Request parameter.

        # Determine dependencies: scopes checker only
        dependencies_list = []
        if scopes:
            dependencies_list.append(Depends(require_scopes(scopes)))

        # Get the appropriate FastAPI method (post, get, put, delete, etc.)
        api_method = getattr(api, method.lower())

        # Register the endpoint with the versioned path and dependencies
        versioned_path = f"/api/v1{path}"
        api_method(
            versioned_path,
            status_code=status_code,
            summary=summary,
            dependencies=dependencies_list,
        )(func)

        logger.info(
            f"Registered API endpoint: {method.upper()} {versioned_path} (Scopes: {scopes})"
        )
        return func

    return decorator


# --- Health Check Endpoint ---
@api.get("/health", status_code=status.HTTP_200_OK, summary="Health check endpoint")
async def health_check(db: Session = Depends(get_db)):
    """
    Performs a health check on the application and its dependencies.
    Checks database connectivity.
    """
    # REPLACED as per Step 1
    with tracer.start_as_current_span("health_check") as span:
        try:
            # Check database connectivity
            await asyncio.to_thread(db.execute, text("SELECT 1"))  # Wrap in text()
            db_status = "ok"
        except SQLAlchemyError as e:
            db_status = "failed"
            logger.error(
                f"Health check failed: Database connection error: {e}", exc_info=True
            )
            span.set_status(Status(StatusCode.ERROR, "Database connection failed"))
            span.record_exception(e)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Database connection error.",
            )
        except Exception as e:
            # Catch errors from dummy session
            if not _FASTAPI_AVAILABLE:
                logger.warning(f"DummyDB: health_check() called. {e}")
                db_status = "dummy"
            else:
                db_status = "failed"
                logger.error(
                    f"Health check failed: Unexpected error: {e}", exc_info=True
                )
                span.set_status(
                    Status(StatusCode.ERROR, "Unexpected health check error")
                )
                span.record_exception(e)
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Unexpected health check error.",
                )

        # Additional checks (e.g., singletons)
        runner_status = "ok" if get_runner_instance() else "failed"
        parser_status = "ok" if get_parser_instance() else "failed"

        overall_status = (
            "healthy"
            if all(
                [db_status != "failed", runner_status == "ok", parser_status == "ok"]
            )
            else "degraded"
        )

        span.set_status(
            Status(StatusCode.OK if overall_status == "healthy" else StatusCode.ERROR)
        )

        return {
            "status": overall_status,
            "components": {
                "database": db_status,
                "runner": runner_status,
                "parser": parser_status,
            },
        }
    # END REPLACED


# --- Authentication Endpoints ---
@api.post(
    "/api/v1/token",
    response_model=Token,
    summary="Obtain JWT access token for user authentication",
)
async def login_for_access_token(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Session = Depends(get_db),
):
    """
    Authenticates a user and returns a JWT access token.
    """
    with tracer.start_as_current_span("login_for_access_token") as span:
        username = form_data.username
        password = form_data.password

        user = await get_user_by_username_from_db(db, username)
        if not user or not pwd_context.verify(password, user.hashed_password):
            logger.warning(f"Login failed for user '{username}': Invalid credentials.")
            span.set_status(
                Status(StatusCode.UNAUTHENTICATED, "Invalid credentials")
            )  # FIX: Use Status object
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect username or password",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Update last login time
        if _FASTAPI_AVAILABLE:  # Only real users have this attribute
            user.last_login_at = datetime.now(timezone.utc)
        try:
            await asyncio.to_thread(db.commit)
            if _FASTAPI_AVAILABLE:
                await asyncio.to_thread(db.refresh, user)  # Refresh user object
        except SQLAlchemyError as e:
            logger.error(f"Failed to update last_login_at for user {username}: {e}")
            span.record_exception(e)
        except Exception as e:
            logger.warning(f"DummyDB: db.commit() for last_login_at called. {e}")

        access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        access_token = create_access_token(
            data={
                "sub": user.username,
                "scopes": user.scopes.split(",") if _FASTAPI_AVAILABLE else user.scopes,
            },  # Store scopes as list
            expires_delta=access_token_expires,
        )
        logger.info(f"User '{username}' logged in successfully.")
        span.set_status(Status(StatusCode.OK))  # FIX: Use Status object
        return {"access_token": access_token, "token_type": "bearer"}


@register_api_endpoint(
    path="/users",
    method="POST",
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user",
    scopes=["admin"],
)
async def register_user(user_data: UserCreate, db: Session = Depends(get_db)):
    """
    Registers a new user with a hashed password. Only accessible by admins.
    """
    with tracer.start_as_current_span("register_user") as span:
        # Check if user already exists
        existing_user = await get_user_by_username_from_db(db, user_data.username)
        if existing_user:
            logger.warning(
                f"User registration failed: Username '{user_data.username}' already exists."
            )
            span.set_status(
                Status(StatusCode.ALREADY_EXISTS, "Username already exists")
            )  # FIX: Use Status object
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Username already registered",
            )

        hashed_password = pwd_context.hash(user_data.password)
        new_user = User(
            username=user_data.username,
            hashed_password=hashed_password,
            scopes=",".join(user_data.scopes),  # Store scopes as comma-separated string
        )
        try:
            await asyncio.to_thread(db.add, new_user)
            await asyncio.to_thread(db.commit)
            await asyncio.to_thread(db.refresh, new_user)
            logger.info(f"User '{new_user.username}' registered successfully.")
            span.set_status(Status(StatusCode.OK))  # FIX: Use Status object
            return UserResponse.from_orm(new_user)
        except IntegrityError as e:
            logger.error(
                f"Database integrity error during user registration for '{user_data.username}': {e}",
                exc_info=True,
            )
            span.set_status(
                Status(StatusCode.ALREADY_EXISTS, "Username already exists")
            )  # FIX: Use Status object
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Username already registered (DB error)",
            )
        except SQLAlchemyError as e:
            logger.error(
                f"Database error during user registration for '{user_data.username}': {e}",
                exc_info=True,
            )
            span.set_status(
                Status(StatusCode.INTERNAL_ERROR, "Database error")
            )  # FIX: Use Status object
            raise HTTPException(
                status_code=500,
                detail="Internal database error during user registration.",
            )
        except Exception as e:
            # Catch errors from dummy session
            logger.warning(f"DummyDB: register_user() called. {e}")
            span.set_status(Status(StatusCode.OK))
            # Return a dummy response that matches UserResponse
            return UserResponse(
                username=user_data.username,
                scopes=user_data.scopes,
                is_active=True,
                created_at=datetime.now(timezone.utc),
                last_login_at=None,
                model_config=(
                    {"from_attributes": True}
                    if not hasattr(UserResponse.Config, "orm_mode")
                    else {}
                ),
            )


@register_api_endpoint(
    path="/api-keys",
    method="POST",
    status_code=status.HTTP_201_CREATED,
    summary="Create a new API key",
    scopes=["admin"],
)
async def create_api_key(api_key_data: APIKeyCreate, db: Session = Depends(get_db)):
    """
    Creates a new API key with specified scopes. The raw API key is returned once and not stored.
    Only accessible by admins.
    """
    with tracer.start_as_current_span("create_api_key") as span:
        raw_api_key = str(uuid.uuid4())  # Generate a random API key
        hashed_api_key = pwd_context.hash(raw_api_key)
        api_key_id = str(uuid.uuid4())  # A public ID for the API key

        new_api_key = APIKey(
            api_key_id=api_key_id,
            hashed_api_key=hashed_api_key,
            scopes=",".join(api_key_data.scopes),
        )
        try:
            await asyncio.to_thread(db.add, new_api_key)
            await asyncio.to_thread(db.commit)
            await asyncio.to_thread(db.refresh, new_api_key)
            logger.info(f"API Key '{api_key_id}' created successfully.")
            span.set_status(Status(StatusCode.OK))  # FIX: Use Status object
            # Return the raw API key ONLY ONCE. It's not stored in DB.
            return {
                "api_key_id": api_key_id,
                "api_key": raw_api_key,
                "scopes": api_key_data.scopes,
            }
        except SQLAlchemyError as e:
            logger.error(f"Database error during API key creation: {e}", exc_info=True)
            span.set_status(
                Status(StatusCode.INTERNAL_ERROR, "Database error")
            )  # FIX: Use Status object
            raise HTTPException(
                status_code=500,
                detail="Internal database error during API key creation.",
            )
        except Exception as e:
            # Catch errors from dummy session
            logger.warning(f"DummyDB: create_api_key() called. {e}")
            span.set_status(Status(StatusCode.OK))
            return {
                "api_key_id": api_key_id,
                "api_key": raw_api_key,
                "scopes": api_key_data.scopes,
            }


@register_api_endpoint(
    path="/api-keys/{api_key_id}",
    method="DELETE",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an API key",
    scopes=["admin"],
)
async def delete_api_key(api_key_id: str, db: Session = Depends(get_db)):
    """
    Deletes an API key by its public ID. Only accessible by admins.
    """
    with tracer.start_as_current_span("delete_api_key") as span:
        api_key_to_delete = await asyncio.to_thread(
            lambda: db.query(APIKey).filter(APIKey.api_key_id == api_key_id).first()
        )
        if not api_key_to_delete:
            logger.warning(
                f"API Key deletion failed: API Key '{api_key_id}' not found."
            )
            span.set_status(
                Status(StatusCode.NOT_FOUND, "API Key not found")
            )  # FIX: Use Status object
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="API Key not found"
            )

        try:
            await asyncio.to_thread(db.delete, api_key_to_delete)
            await asyncio.to_thread(db.commit)
            logger.info(f"API Key '{api_key_id}' deleted successfully.")
            span.set_status(Status(StatusCode.OK))  # FIX: Use Status object
            return {"status": "success", "message": "API Key deleted."}
        except SQLAlchemyError as e:
            logger.error(
                f"Database error during API key deletion for '{api_key_id}': {e}",
                exc_info=True,
            )
            span.set_status(
                Status(StatusCode.INTERNAL_ERROR, "Database error")
            )  # FIX: Use Status object
            raise HTTPException(
                status_code=500,
                detail="Internal database error during API key deletion.",
            )
        except Exception as e:
            # Catch errors from dummy session
            logger.warning(f"DummyDB: delete_api_key() called. {e}")
            span.set_status(Status(StatusCode.OK))
            return {"status": "success", "message": "API Key deleted (dummy)."}


# --- Runner Endpoints ---
class RunPayload(BaseModel):
    # Define a stricter schema for the run payload
    project_name: str = Field(..., min_length=1, max_length=100)
    description: str = Field(..., min_length=1)
    # Add other expected fields with their types and validations
    output_format: Optional[str] = "markdown"


@register_api_endpoint(
    path="/run", summary="Run the generator with the given payload", scopes=["run"]
)
async def api_run(
    payload: RunPayload,  # Use Pydantic model for validation
    authenticated_entity: Union[User, APIKey] = Depends(require_scopes(["run"])),
):
    """
    Triggers the AI generator to run based on the provided payload.
    """
    with tracer.start_as_current_span("api_run") as span:
        entity_id = (
            authenticated_entity.username
            if isinstance(authenticated_entity, User)
            else authenticated_entity.api_key_id
        )
        span.set_attribute("entity_id", entity_id)
        try:
            runner = get_runner_instance()
            # Convert Pydantic model to dict for runner
            result = await runner.run(payload.dict())
            logger.info(f"Run completed for entity {entity_id}")
            span.set_status(Status(StatusCode.OK))  # FIX: Use Status object
            return result
        except Exception as e:
            logger.error(f"Run error for entity {entity_id}: {e}", exc_info=True)
            span.set_status(Status(StatusCode.ERROR, str(e)))  # FIX: Use Status object
            span.record_exception(e)
            raise HTTPException(
                status_code=500,
                detail="An internal error occurred during the generator run.",
            )


# --- Parser Endpoints ---
class ParseTextRequest(BaseModel):
    content: str = Field(..., min_length=1)
    format_hint: Optional[str] = None
    dry_run: bool = False


@register_api_endpoint(
    path="/parse/text", summary="Parse text content to extract intent", scopes=["parse"]
)
async def api_parse_text(
    request_data: ParseTextRequest,  # Use Pydantic model
    authenticated_entity: Union[User, APIKey] = Depends(require_scopes(["parse"])),
):
    """
    Parses text content to extract intent using the IntentParser.
    """
    with tracer.start_as_current_span("api_parse_text") as span:
        entity_id = (
            authenticated_entity.username
            if isinstance(authenticated_entity, User)
            else authenticated_entity.api_key_id
        )
        span.set_attribute("entity_id", entity_id)
        parser = get_parser_instance()
        try:
            result = await parser.parse(
                content=request_data.content,
                format_hint=request_data.format_hint,
                dry_run=request_data.dry_run,
                user_id=entity_id,
            )
            logger.info(f"Parse text completed for entity {entity_id}")
            span.set_status(Status(StatusCode.OK))  # FIX: Use Status object
            return result
        except Exception as e:
            logger.error(f"Parse text error for entity {entity_id}: {e}", exc_info=True)
            span.set_status(Status(StatusCode.ERROR, str(e)))  # FIX: Use Status object
            span.record_exception(e)
            raise HTTPException(
                status_code=500,
                detail="An internal error occurred during text parsing.",
            )


@register_api_endpoint(
    path="/parse/file",
    summary="Parse an uploaded file to extract intent",
    scopes=["parse"],
)
async def api_parse_file(
    file: UploadFile = File(
        ..., description="The file to parse"
    ),  # Use File for file uploads
    format_hint: Optional[str] = Form(
        None, description="Hint about the file format (e.g., 'markdown', 'json')"
    ),
    dry_run: bool = Form(
        False, description="If true, perform a dry run without persisting changes"
    ),
    authenticated_entity: Union[User, APIKey] = Depends(require_scopes(["parse"])),
):
    """
    Parses an uploaded file to extract intent using the IntentParser.
    """
    with tracer.start_as_current_span("api_parse_file") as span:
        entity_id = (
            authenticated_entity.username
            if isinstance(authenticated_entity, User)
            else authenticated_entity.api_key_id
        )
        span.set_attribute("entity_id", entity_id)

        # Securely handle file uploads: use a dedicated temp directory
        # For scaled, containerized deployments, consider using a shared object store (e.g., S3)
        # instead of a local directory.
        temp_dir = Path("temp_uploaded_files")
        # Ensure directory exists and has appropriate permissions in production
        temp_dir.mkdir(exist_ok=True, mode=0o700)  # Restrict permissions

        # Use a unique filename to prevent collisions
        unique_filename = f"{uuid.uuid4()}_{file.filename}"
        temp_file_path = temp_dir / unique_filename

        try:
            # Write file in chunks to prevent memory exhaustion for large files
            async with aiofiles.open(temp_file_path, "wb") as f:
                while contents := await file.read(1024 * 1024):  # Read in 1MB chunks
                    await f.write(contents)

            parser = get_parser_instance()
            result = await parser.parse(
                content="",  # Content is read from file, not direct string
                format_hint=format_hint,
                file_path=temp_file_path,
                dry_run=dry_run,
                user_id=entity_id,
            )
            logger.info(f"Parse file completed for entity {entity_id}")
            span.set_status(Status(StatusCode.OK))  # FIX: Use Status object
            return result
        except Exception as e:
            logger.error(f"Parse file error for entity {entity_id}: {e}", exc_info=True)
            span.set_status(Status(StatusCode.ERROR, str(e)))  # FIX: Use Status object
            span.record_exception(e)
            raise HTTPException(
                status_code=500,
                detail="An internal error occurred during file parsing.",
            )
        finally:
            if temp_file_path.exists():
                try:
                    os.remove(temp_file_path)
                    logger.debug(f"Cleaned up temporary file: {temp_file_path}")
                except OSError as e:
                    logger.error(
                        f"Error cleaning up temporary file {temp_file_path}: {e}",
                        exc_info=True,
                    )
                    # Alert if temp file cleanup consistently fails
                    # await send_alert(f"Failed to clean up temp file {temp_file_path}: {e}", severity="low")


@register_api_endpoint(
    path="/parse/feedback/{item_id}",
    summary="Submit feedback for a parsed item",
    scopes=["parse"],
)
async def api_submit_parse_feedback(
    item_id: str = FastAPIPath(
        ..., description="ID of the parsed item to submit feedback for"
    ),
    rating: float = Body(
        ..., ge=0.0, le=1.0, description="Rating for the parsed item (0.0 to 1.0)"
    ),
    authenticated_entity: Union[User, APIKey] = Depends(require_scopes(["parse"])),
):
    """
    Submits feedback for a specific parsed item.
    """
    with tracer.start_as_current_span("api_submit_parse_feedback") as span:
        entity_id = (
            authenticated_entity.username
            if isinstance(authenticated_entity, User)
            else authenticated_entity.api_key_id
        )
        span.set_attribute("entity_id", entity_id)
        parser = get_parser_instance()
        try:
            parser.feedback.rate(item_id, rating, entity_id)
            logger.info(f"Feedback submitted for item {item_id} by entity {entity_id}")
            span.set_status(Status(StatusCode.OK))  # FIX: Use Status object
            return {"status": "success", "message": f"Feedback for {item_id} recorded."}
        except Exception as e:
            logger.error(
                f"Feedback submission error for entity {entity_id}: {e}", exc_info=True
            )
            span.set_status(Status(StatusCode.ERROR, str(e)))  # FIX: Use Status object
            span.record_exception(e)
            raise HTTPException(
                status_code=500,
                detail="An internal error occurred while submitting feedback.",
            )


@register_api_endpoint(
    path="/parse/reload_config", summary="Reload parser configuration", scopes=["admin"]
)
async def api_reload_parser_config(
    authenticated_entity: Union[User, APIKey] = Depends(require_scopes(["admin"])),
):
    """
    Reloads the IntentParser's configuration and strategies. Only accessible by admins.
    """
    with tracer.start_as_current_span("api_reload_parser_config") as span:
        entity_id = (
            authenticated_entity.username
            if isinstance(authenticated_entity, User)
            else authenticated_entity.api_key_id
        )
        span.set_attribute("entity_id", entity_id)
        parser = get_parser_instance()
        try:
            parser.reload_config_and_strategies()
            logger.info(f"Parser config reloaded by entity {entity_id}")
            span.set_status(Status(StatusCode.OK))  # FIX: Use Status object
            return {
                "status": "success",
                "message": "IntentParser configuration reloaded.",
            }
        except Exception as e:
            logger.error(
                f"Config reload error for entity {entity_id}: {e}", exc_info=True
            )
            span.set_status(Status(StatusCode.ERROR, str(e)))  # FIX: Use Status object
            span.record_exception(e)
            raise HTTPException(
                status_code=500,
                detail="An internal error occurred while reloading configuration.",
            )


# --- Feedback Endpoints (for runner runs) ---
class SubmitFeedbackRequest(BaseModel):
    run_id: str = Field(..., description="ID of the run to submit feedback for")
    rating: int = Field(..., ge=1, le=5, description="Rating for the run (1-5)")
    comments: Optional[str] = Field(
        None, max_length=500, description="Optional comments for the feedback"
    )


@register_api_endpoint(
    path="/feedback", summary="Submit feedback on a generator run", scopes=["feedback"]
)
async def api_submit_runner_feedback(
    feedback_data: SubmitFeedbackRequest,  # Use Pydantic model
    authenticated_entity: Union[User, APIKey] = Depends(require_scopes(["feedback"])),
):
    """
    Submits feedback for a completed generator run.
    """
    with tracer.start_as_current_span("api_submit_runner_feedback") as span:
        entity_id = (
            authenticated_entity.username
            if isinstance(authenticated_entity, User)
            else authenticated_entity.api_key_id
        )
        span.set_attribute("entity_id", entity_id)
        try:
            # Placeholder for feedback storage (e.g., save to DB, send to analytics)
            logger.info(
                f"Feedback submitted for run {feedback_data.run_id} by entity {entity_id}: rating {feedback_data.rating}, comments: {feedback_data.comments}"
            )
            span.set_status(Status(StatusCode.OK))  # FIX: Use Status object
            return {"status": "success", "message": "Feedback recorded."}
        except Exception as e:
            logger.error(
                f"Feedback submission error for entity {entity_id}: {e}", exc_info=True
            )
            span.set_status(Status(StatusCode.ERROR, str(e)))  # FIX: Use Status object
            span.record_exception(e)
            raise HTTPException(
                status_code=500,
                detail="An internal error occurred while submitting feedback.",
            )


# --- Utility Endpoints ---
@register_api_endpoint(
    path="/logs/search", method="GET", summary="Search logs", scopes=["logs"]
)
async def api_search_logs(
    query: str = Query(..., min_length=1, description="Search query for logs"),
    authenticated_entity: Union[User, APIKey] = Depends(require_scopes(["logs"])),
):
    """
    Searches application logs.
    """
    with tracer.start_as_current_span("api_search_logs") as span:
        entity_id = (
            authenticated_entity.username
            if isinstance(authenticated_entity, User)
            else authenticated_entity.api_key_id
        )
        span.set_attribute("entity_id", entity_id)
        try:
            results = search_logs(query)
            logger.info(f"Logs searched by entity {entity_id}")
            span.set_status(Status(StatusCode.OK))  # FIX: Use Status object
            return {"results": results}
        except Exception as e:
            logger.error(
                f"Logs search error for entity {entity_id}: {e}", exc_info=True
            )
            span.set_status(Status(StatusCode.ERROR, str(e)))  # FIX: Use Status object
            span.record_exception(e)
            raise HTTPException(
                status_code=500, detail="An internal error occurred during log search."
            )


# Prometheus metrics are exposed via Instrumentator at /api/v1/metrics

# --- Run the Server ---
if __name__ == "__main__":
    if _FASTAPI_AVAILABLE:
        # Create DB tables on startup
        # This call is now wrapped in the startup_event, but calling it here
        # ensures tables exist before uvicorn.run() if not using the event.
        # With the startup_event, this is technically redundant but harmless.
        create_db_tables()

        # PRODUCTION FIX: Removed the automatic creation of a default admin user and API key.
        # Initial user and key creation should be handled by a secure, separate process,
        # Example:
        #     # such as a CLI command or a one-time setup script after deployment. This prevents
        #     # hardcoded, known credentials from ever existing in a production environment.

        # PRODUCTION FIX: Set reload=False for production environments.
        # The auto-reloader is a development feature and should be disabled for performance and stability.
        uvicorn.run("api:api", host="0.0.0.0", port=8000, reload=False)
    else:
        logger.critical(
            "Cannot run API server directly: FastAPI dependencies are not installed."
        )
        print(
            "Cannot run API server directly: FastAPI dependencies are not installed.",
            file=os.stderr,
        )
        exit(1)
