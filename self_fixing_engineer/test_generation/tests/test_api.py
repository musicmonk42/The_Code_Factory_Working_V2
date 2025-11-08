import pytest
import os
import json
import asyncio
import inspect
from functools import wraps
from unittest.mock import MagicMock, patch, AsyncMock, Mock
from asyncio import TimeoutError as AsyncTimeoutError
from werkzeug.exceptions import BadRequest, TooManyRequests
from flask.testing import FlaskClient

# Fix: Import all necessary components for mocking from api.py
with patch.dict(os.environ, {"ENV": "dev", "BEHIND_PROXY": "false", "ENABLE_METRICS": "false", "CORS_ORIGINS": ""}):
    from test_generation.gen_agent.api import create_app, create_access_token, _run_async, Flask, JWTManager, with_jwt_required, Limiter, BadRequest as FlaskBadRequest, jwt_required as jwt_required_import, _generate_tests_logic

async def _mock_invoke_graph(graph, state, config=None, progress_callback=None):
    """A mock implementation for the async invoke_graph function."""
    await asyncio.sleep(0.01)  # Simulate some work
    return {
        **state,
        "test_code": "def test_something():\n    assert True",
        "final_scores": {"coverage": 100},
        "status": "PASS"
    }

async def _mock_invoke_graph_timeout(graph, state, config=None, progress_callback=None):
    """A mock implementation that simulates a timeout."""
    await asyncio.sleep(2)  # Simulate work that exceeds the timeout
    return {
        **state,
        "test_code": "This should not be returned.",
    }

@pytest.fixture
def mock_dependencies(monkeypatch):
    """Mocks external dependencies like LLM, graph, and Flask extensions."""
    # Mock LLM and Graph functions
    monkeypatch.setattr("test_generation.gen_agent.api.runtime_init_llm", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr("test_generation.gen_agent.api.build_graph", MagicMock(return_value=MagicMock()))
    # Use AsyncMock for invoke_graph to handle its async nature
    monkeypatch.setattr("test_generation.gen_agent.api.invoke_graph", AsyncMock(side_effect=_mock_invoke_graph))
    monkeypatch.setattr("test_generation.gen_agent.api.get_swaggerui_blueprint", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr("test_generation.gen_agent.api.audit_logger", MagicMock())
    monkeypatch.setattr("test_generation.gen_agent.api.AUDIT_LOGGER_AVAILABLE", True)

    # Mock Flask extensions to control their behavior
    monkeypatch.setattr("test_generation.gen_agent.api.CORS", MagicMock())
    mock_limiter = MagicMock()
    mock_limiter_instance = MagicMock()
    mock_limiter.return_value = mock_limiter_instance
    # Ensure the mock decorator returns a function with a __name__ attribute
    def mock_decorator_with_name(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            return f(*args, **kwargs)
        return wrapper
    mock_limiter_instance.limit.return_value.side_effect = mock_decorator_with_name
    
    monkeypatch.setattr("test_generation.gen_agent.api.JWTManager", MagicMock())
    
    # Correctly mock jwt_required to behave like a decorator
    def mock_jwt_required_decorator():
        def decorator(f):
            @wraps(f)
            def wrapper(*args, **kwargs):
                return f(*args, **kwargs)
            return wrapper
        return decorator
    monkeypatch.setattr("test_generation.gen_agent.api.jwt_required", mock_jwt_required_decorator)

    monkeypatch.setattr("test_generation.gen_agent.api.get_remote_address", lambda: "127.0.0.1")

    return {
        "limiter": mock_limiter,
        "jwt_manager": MagicMock(),
        "create_access_token": MagicMock()
    }

@pytest.fixture
def app(mock_dependencies):
    """Creates a Flask app instance for testing."""
    app_config = {
        "SECRET_KEY": "test-key",
        "JWT_SECRET_KEY": "test-jwt-key",
    }
    app_instance = create_app(app_config)
    app_instance.testing = True
    return app_instance

@pytest.fixture
def client(app):
    """Provides a test client for making requests."""
    return app.test_client()

# --- Functional Tests ---

class TestFunctional:
    def test_generate_tests_valid_payload(self, client):
        """Verify POST /generate-tests with a valid payload returns test_code."""
        payload = {
            "spec": "Given a function that adds two numbers, it should return their sum.",
            "language": "Python",
            "framework": "pytest",
            "spec_format": "gherkin"
        }
        with patch("test_generation.gen_agent.api._generate_tests_logic", new_callable=AsyncMock) as mock_logic:
            mock_logic.return_value = {"test_code": "test code"}
            response = client.post("/generate-tests", json=payload)
        
        assert response.status_code == 200
        assert response.get_json() == {"test_code": "test code"}

    def test_generate_tests_missing_field(self, client):
        """Verify missing a required field returns a 400 with a Pydantic error."""
        payload = {
            "language": "Python",
            "framework": "pytest"
        }
        response = client.post("/generate-tests", json=payload)
        
        assert response.status_code == 400
        data = response.get_json()
        assert "error" in data
        assert data["error"] == "Bad Request"
        assert "details" in data
        assert any("spec" in err["loc"] for err in data["details"])

    def test_generate_tests_invalid_json(self, client):
        """Verify an invalid JSON body returns a 400 with a specific message."""
        response = client.post("/generate-tests", data="not a json", content_type="application/json")
        
        assert response.status_code == 400
        data = response.get_json()
        assert "error" in data
        assert data["error"] == "Bad Request"
        assert "Invalid JSON body" in data["message"]

    def test_swagger_schema(self, client):
        """
        Tests that the API's Pydantic schema validation returns a 400
        with an empty JSON payload.
        This test was added as a user request.
        """
        response = client.post("/generate-tests", json={})
        assert response.status_code == 400
        
# --- Security Tests ---

class TestSecurity:
    def test_no_jwt_required_when_disabled(self, client, mock_dependencies):
        """The endpoint should be open when JWTManager is None."""
        with patch("test_generation.gen_agent.api.JWT_AVAILABLE", False):
            # Re-create app with JWT disabled to test the logic branch in create_app
            app_no_jwt = create_app({"SECRET_KEY": "a", "JWT_SECRET_KEY": "b"})
            app_no_jwt.testing = True
            with patch("test_generation.gen_agent.api._generate_tests_logic", new_callable=AsyncMock) as mock_logic:
                mock_logic.return_value = {"test_code": "test code"}
                response = app_no_jwt.test_client().post("/generate-tests", json={"spec": "test", "language": "Python", "framework": "pytest", "spec_format": "gherkin"})
                assert response.status_code == 200

    def test_jwt_required_when_enabled(self, client, mock_dependencies):
        """Unauthorized requests should return 401 when JWT is enabled."""
        with patch("test_generation.gen_agent.api.JWT_AVAILABLE", True), \
             patch("test_generation.gen_agent.api.jwt_required") as mock_jwt_required:

            def mock_decorator_return():
                def decorator(f):
                    @wraps(f)
                    def wrapper(*args, **kwargs):
                        # Simulate the behavior of a real @jwt_required failing
                        return "Unauthorized", 401
                    return wrapper
                return decorator
            # The decorator itself is called, which returns another decorator
            mock_jwt_required.return_value = mock_decorator_return()

            app_with_jwt = create_app({"SECRET_KEY": "test-key", "JWT_SECRET_KEY": "test-jwt-key"})
            app_with_jwt.testing = True

            response = app_with_jwt.test_client().post("/generate-tests", json={"spec": "test", "language": "Python"})
            assert response.status_code == 401
            # In a real scenario, this would be a JSON response. For this mock, text is fine.
            assert "Unauthorized" in response.get_data(as_text=True)

@pytest.mark.parametrize("env, jwt_available", [
    ("prod", True),
    ("prod", False),
    ("dev", True),
    ("dev", False),
])
def test_production_jwt_requirement(monkeypatch, env, jwt_available):
    """
    Tests that create_app raises a RuntimeError if JWT is not available in production.
    """
    monkeypatch.setitem(os.environ, "ENV", env)
    monkeypatch.setattr("test_generation.gen_agent.api.JWT_AVAILABLE", jwt_available)
    monkeypatch.setattr("test_generation.gen_agent.api.runtime_init_llm", MagicMock())
    monkeypatch.setattr("test_generation.gen_agent.api.build_graph", MagicMock())

    if env == "prod" and not jwt_available:
        with pytest.raises(RuntimeError, match="JWT authentication is required in production"):
            create_app({"SECRET_KEY": "a", "JWT_SECRET_KEY": "b"})
    else:
        # Should not raise an error
        create_app({"SECRET_KEY": "a", "JWT_SECRET_KEY": "b"})


    def test_owasp_headers_in_prod(self, client):
        """Verify OWASP headers are set in production mode."""
        with patch.dict(os.environ, {"ENV": "prod"}), \
             patch("test_generation.gen_agent.api.serve_api"):
            app = create_app({"SECRET_KEY": "a", "JWT_SECRET_KEY": "b"})
            app.testing = True
            
            response = app.test_client().get("/health")
            
            assert response.status_code == 200
            assert "Content-Security-Policy" in response.headers
            assert "X-Content-Type-Options" in response.headers
            assert "X-Frame-Options" in response.headers
            assert "Referrer-Policy" in response.headers
            assert "Strict-Transport-Security" in response.headers

# --- Operational Tests ---

class TestOperational:
    def test_rate_limiting_enforced(self, client, mock_dependencies):
        """Verify rate limiting returns a 429 status code."""
        with patch("test_generation.gen_agent.api.LIMITER_AVAILABLE", True), \
             patch("test_generation.gen_agent.api.Limiter") as mock_limiter_class:

            mock_limiter_instance = Mock()
            mock_limiter_class.return_value = mock_limiter_instance

            # Create a mock decorator that raises TooManyRequests on call
            def mock_decorator(limit_string):
                def decorator(f):
                    @wraps(f)
                    def wrapper(*args, **kwargs):
                        raise TooManyRequests()
                    return wrapper
                return decorator

            mock_limiter_instance.limit.side_effect = mock_decorator
            
            app_with_limiter = create_app({"SECRET_KEY": "a", "JWT_SECRET_KEY": "b"})
            app_with_limiter.testing = True

            payload = {"spec": "test", "language": "Python", "framework": "pytest", "spec_format": "gherkin"}
            response = app_with_limiter.test_client().post("/generate-tests", json=payload)
            
            assert response.status_code == 429
            data = response.get_json()
            assert "Too many requests" in data["error"]

    def test_cors_headers_applied_correctly(self, client, mock_dependencies):
        """Verify CORS headers are set based on environment variable."""
        with patch.dict(os.environ, {"CORS_ORIGINS": "https://test-origin.com"}), \
             patch("test_generation.gen_agent.api.CORS") as mock_cors:
            app = create_app({"SECRET_KEY": "a", "JWT_SECRET_KEY": "b"})
            app.testing = True

            mock_cors.assert_called_with(app, resources={'/*': {'origins': ['https://test-origin.com'], 'supports_credentials': False}})

# --- Failure-path Tests ---

class TestFailurePath:
    def test_llm_init_failure(self, client, mock_dependencies):
        """Verify a failure in init_llm() leads to a 503 response."""
        with patch("test_generation.gen_agent.api.runtime_init_llm", side_effect=Exception("LLM connection failed.")):
            with patch("test_generation.gen_agent.api.logging.Logger.error") as mock_log:
                # The create_app call must be inside the patch block
                app = create_app({"SECRET_KEY": "a", "JWT_SECRET_KEY": "b"})
                app.testing = True

                mock_log.assert_called_with("LLM init failed at startup: LLM connection failed.")
                assert app.config["_GRAPH"] is None
            
            payload = {"spec": "test", "language": "Python", "framework": "pytest", "spec_format": "gherkin"}
            response = app.test_client().post("/generate-tests", json=payload)
            
            assert response.status_code == 503
            data = response.get_json()
            assert "Service unavailable" in data["error"]
            assert "LLM agent failed to initialize at startup." in data["message"]

    # Fix: Remove `@pytest.mark.asyncio` and `async`. The test calls a sync endpoint
    # which internally handles the async logic. Running the test itself in an
    # event loop conflicts with the `asyncio.run()` call in the endpoint.
    def test_run_async_raises_timeout_error(self, client, mock_dependencies):
        """
        Verify that the endpoint correctly handles a timeout from the async logic.
        This test simulates a coroutine that takes longer than the timeout.
        """
        # Patch the logic layer to simulate a timeout condition
        with patch("test_generation.gen_agent.api._generate_tests_logic", new_callable=AsyncMock, side_effect=asyncio.TimeoutError) as mock_logic:
            payload = {"spec": "test", "language": "Python", "framework": "pytest", "spec_format": "gherkin"}
            response = client.post("/generate-tests", json=payload)
            
        assert response.status_code == 504
        data = response.get_json()
        assert "Request timed out" in data["error"]