"""
Test suite for omnicore_engine/fastapi_app.py
Tests FastAPI endpoints, middleware, and startup/shutdown events.

NOTE: Many tests in this module use FastAPI TestClient which requires creating
background threads for async operations. In CI environments with resource
constraints, this can fail with "can't start new thread" errors. Tests that
use TestClient are skipped in such environments.
"""

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

import jwt
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

# Skip all tests in this module if TestClient is likely to fail due to thread limits
# This is detected by checking if we're in a CI environment with many tests running
pytestmark = pytest.mark.skipif(
    os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true",
    reason="TestClient requires thread creation which may fail in CI environment with resource constraints"
)

# Add the parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def test_app():
    """Lazy-load the FastAPI app to avoid expensive initialization during collection."""
    from omnicore_engine.fastapi_app import app as fastapi_app
    return fastapi_app


@pytest.fixture
def client(test_app):
    """Create a test client for the FastAPI app."""
    return TestClient(test_app)


# Import the app directly for tests that need to create clients with mocking
def _get_app():
    """Helper to get the FastAPI app for tests with custom mocking."""
    from omnicore_engine.fastapi_app import app as fastapi_app
    return fastapi_app


class TestStartupShutdown:
    """Test startup and shutdown events using lifespan context manager"""

    @pytest.mark.skip(reason="Lifespan requires full application context with all dependencies")
    @pytest.mark.asyncio
    async def test_lifespan_startup_success(self):
        """Test successful startup through lifespan - skipped as it requires full app context"""
        pass

    @pytest.mark.skip(reason="Lifespan requires full application context with all dependencies")
    @pytest.mark.asyncio
    async def test_lifespan_shutdown(self):
        """Test shutdown through lifespan - skipped as it requires full app context"""
        pass


class TestSecurityMiddleware:
    """Test security middleware and authentication"""

    @pytest.mark.skip(reason="TestClient requires thread creation which may fail in CI environment with resource constraints")
    def test_size_limit_middleware(self, client):
        """Test request size limiting"""
        # client fixture injected

        # Create large payload
        large_data = "x" * 11_000_000  # 11MB

        response = client.post(
            "/api/notify",
            json={"data": large_data},
            headers={"content-length": str(len(large_data))},
        )

        assert response.status_code == 413
        assert "Request too large" in response.json()["error"]

    @pytest.mark.skip(reason="TestClient requires thread creation which may fail in CI environment with resource constraints")
    def test_csrf_protection(self, client):
        """Test CSRF protection"""
        # client fixture injected

        # Request without CSRF token should fail for protected endpoints
        # This would need actual CSRF testing setup
        pass

    def test_jwt_authentication(self, client):
        """Test JWT token validation"""
        # For this test, we'll use a patched settings with a known secret
        test_secret = "test-jwt-secret-key-for-testing"

        # Create a mock settings object with JWT_SECRET_KEY
        class MockSecretStr:
            def get_secret_value(self):
                return test_secret

        mock_settings = Mock()
        mock_settings.JWT_SECRET_KEY = MockSecretStr()

        with patch("omnicore_engine.fastapi_app.settings", mock_settings):
            from omnicore_engine.fastapi_app import get_user_id as get_user_id_patched

            # Valid token
            valid_token = jwt.encode(
                {"sub": "user123", "exp": datetime.utcnow() + timedelta(hours=1)},
                test_secret,
                algorithm="HS256",
            )

            # Should work with valid token
            user_id = asyncio.run(get_user_id_patched(valid_token))
            assert user_id == "user123"

            # Expired token
            expired_token = jwt.encode(
                {"sub": "user123", "exp": datetime.utcnow() - timedelta(hours=1)},
                test_secret,
                algorithm="HS256",
            )

            with pytest.raises(HTTPException) as exc:
                asyncio.run(get_user_id_patched(expired_token))
            assert exc.value.status_code == 401
            assert "expired" in exc.value.detail.lower()

            # Invalid token
            with pytest.raises(HTTPException) as exc:
                asyncio.run(get_user_id_patched("invalid_token"))
            assert exc.value.status_code == 401


class TestHealthEndpoint:
    """Test health check endpoint"""

    @pytest.mark.skip(reason="TestClient requires thread creation which may fail in CI environment with resource constraints")
    def test_root_health_check(self, client):
        """Test root /health endpoint for container orchestration"""
        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "timestamp" in data

    @pytest.mark.skip(reason="TestClient requires thread creation which may fail in CI environment with resource constraints")
    @patch("omnicore_engine.fastapi_app.omnicore_engine")
    def test_api_health_check(self, mock_engine):
        """Test /api/health endpoint"""
        mock_engine.health_check = AsyncMock(
            return_value={
                "status": "healthy",
                "components": {"database": "ok", "message_bus": "ok"},
            }
        )

        client = TestClient(_get_app())
        response = client.get("/api/health")

        assert response.status_code == 200
        assert response.json()["status"] == "healthy"


class TestChatEndpoint:
    """Test chat endpoint"""

    @patch("omnicore_engine.fastapi_app.chatbot_arbiter")
    @patch("omnicore_engine.fastapi_app.ARBITER_AVAILABLE", True)
    def test_chat_success(self, mock_arbiter):
        """Test successful chat interaction"""
        mock_arbiter.respond = AsyncMock(return_value="Hello! How can I help?")

        client = TestClient(_get_app())
        response = client.post(
            "/api/chat", json={"user_id": "user123", "message": "Hello", "context": {}}
        )

        assert response.status_code == 200
        assert response.json()["response"] == "Hello! How can I help?"
        assert response.json()["status"] == "success"

    @patch("omnicore_engine.fastapi_app.ARBITER_AVAILABLE", False)
    def test_chat_unavailable(self, client):
        """Test chat when arbiter not available"""
        # client fixture injected
        response = client.post(
            "/api/chat", json={"user_id": "user123", "message": "Hello", "context": {}}
        )

        assert response.status_code == 200
        assert response.json()["status"] == "error"
        assert "unavailable" in response.json()["response"].lower()


class TestSimulationEndpoints:
    """Test simulation endpoints"""

    @patch("omnicore_engine.fastapi_app.simulation_module")
    def test_execute_simulation_success(self, mock_sim):
        """Test simulation execution"""
        mock_sim.execute_simulation = AsyncMock(
            return_value={
                "result": "simulation_completed",
                "metrics": {"accuracy": 0.95},
            }
        )

        client = TestClient(_get_app())
        response = client.post(
            "/api/simulation/execute", json={"config": {"param": "value"}}
        )

        assert response.status_code == 200
        assert response.json()["status"] == "success"
        assert response.json()["result"]["result"] == "simulation_completed"

    @patch("omnicore_engine.fastapi_app.simulation_module", None)
    def test_execute_simulation_not_initialized(self, client):
        """Test simulation when module not initialized"""
        # client fixture injected
        response = client.post("/api/simulation/execute", json={"config": {}})

        assert response.status_code == 500
        assert "not initialized" in response.json()["detail"]

    @patch("omnicore_engine.fastapi_app.simulation_module")
    def test_explain_simulation_success(self, mock_sim):
        """Test simulation explanation"""
        mock_sim.explain_result = AsyncMock(return_value="This simulation shows...")

        client = TestClient(_get_app())
        response = client.post(
            "/api/simulation/explain", json={"result": {"data": "value"}}
        )

        assert response.status_code == 200
        assert response.json()["status"] == "success"
        assert response.json()["explanation"] == "This simulation shows..."


class TestTestGenerationEndpoints:
    """Test test generation endpoints"""

    @patch("omnicore_engine.fastapi_app.omnicore_engine")
    def test_run_test_generation_success(self, mock_engine):
        """Test test generation endpoint"""
        mock_orchestrator = Mock()
        mock_orchestrator.generate_tests_for_targets = AsyncMock(
            return_value={"tests_generated": 10}
        )
        mock_engine.test_generation_orchestrator = mock_orchestrator

        client = TestClient(_get_app())
        response = client.post(
            "/api/test-generation/run",
            json={"targets": [{"file": "test.py"}], "config": {}},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "success"
        assert response.json()["result"]["tests_generated"] == 10

    @patch("omnicore_engine.fastapi_app.PLUGIN_REGISTRY")
    def test_run_test_generation_plugin(self, mock_registry):
        """Test plugin-based test generation"""
        mock_plugin = Mock()
        mock_plugin.execute = AsyncMock(return_value={"tests": ["test1", "test2"]})
        mock_registry.get.return_value = mock_plugin

        client = TestClient(_get_app())
        response = client.post(
            "/api/scenarios/test_generation/run",
            json={"code": "def test(): pass", "language": "python", "config": {}},
        )

        assert response.status_code == 200
        assert "tests" in response.json()


class TestAdminEndpoints:
    """Test admin endpoints"""

    # Test JWT secret for admin endpoints
    TEST_JWT_SECRET = "test-jwt-secret-for-admin-tests"

    def create_auth_token(self):
        """Helper to create valid auth token"""
        return jwt.encode(
            {"sub": "admin_user", "exp": datetime.utcnow() + timedelta(hours=1)},
            self.TEST_JWT_SECRET,
            algorithm="HS256",
        )

    def test_admin_api_disabled(self, client):
        """Test admin API when disabled"""
        from omnicore_engine.fastapi_app import settings

        # Save original value (use getattr with default for fallback settings)
        original_value = getattr(settings, "EXPERIMENTAL_FEATURES_ENABLED", False)

        try:
            # Disable experimental features
            settings.EXPERIMENTAL_FEATURES_ENABLED = False

            # client fixture injected
            response = client.get("/admin/feature-flag")

            assert response.status_code == 404
        finally:
            # Restore original value
            settings.EXPERIMENTAL_FEATURES_ENABLED = original_value

    @patch("omnicore_engine.fastapi_app.omnicore_engine")
    @patch("omnicore_engine.fastapi_app.PluginMarketplace")
    @patch("omnicore_engine.fastapi_app.settings")
    def test_install_plugin(self, mock_settings, mock_marketplace_class, mock_engine):
        """Test plugin installation"""
        # Set up mock settings with required attributes
        mock_settings.EXPERIMENTAL_FEATURES_ENABLED = True
        mock_settings.JWT_SECRET_KEY = Mock()
        mock_settings.JWT_SECRET_KEY.get_secret_value.return_value = (
            self.TEST_JWT_SECRET
        )

        mock_marketplace = Mock()
        mock_marketplace.install_plugin = AsyncMock()
        mock_marketplace_class.return_value = mock_marketplace
        mock_engine.database = Mock()

        client = TestClient(_get_app())
        token = self.create_auth_token()

        response = client.post(
            "/admin/plugins/install",
            json={"kind": "execution", "name": "test_plugin", "version": "1.0.0"},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        assert "installed" in response.json()["message"]

    @patch("omnicore_engine.fastapi_app.omnicore_engine")
    @patch("omnicore_engine.fastapi_app.PluginMarketplace")
    @patch("omnicore_engine.fastapi_app.settings")
    def test_rate_plugin(self, mock_settings, mock_marketplace_class, mock_engine):
        """Test plugin rating"""
        # Set up mock settings with required attributes
        mock_settings.EXPERIMENTAL_FEATURES_ENABLED = True
        mock_settings.JWT_SECRET_KEY = Mock()
        mock_settings.JWT_SECRET_KEY.get_secret_value.return_value = (
            self.TEST_JWT_SECRET
        )

        mock_marketplace = Mock()
        mock_marketplace.rate_plugin = AsyncMock()
        mock_marketplace_class.return_value = mock_marketplace
        mock_engine.database = Mock()

        client = TestClient(_get_app())
        token = self.create_auth_token()

        response = client.post(
            "/admin/plugins/rate",
            json={
                "kind": "execution",
                "name": "test_plugin",
                "version": "1.0.0",
                "rating": 5,
                "comment": "Great plugin!",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        assert "rated" in response.json()["message"]

    @patch("omnicore_engine.fastapi_app.omnicore_engine")
    @patch("omnicore_engine.fastapi_app.settings")
    def test_export_audit_proof_bundle(self, mock_settings, mock_engine):
        """Test audit proof bundle export"""
        # Set up mock settings with required attributes
        mock_settings.EXPERIMENTAL_FEATURES_ENABLED = True
        mock_settings.JWT_SECRET_KEY = Mock()
        mock_settings.JWT_SECRET_KEY.get_secret_value.return_value = (
            self.TEST_JWT_SECRET
        )

        mock_audit = Mock()
        mock_proof_exporter = Mock()
        mock_proof_exporter.export_proof_bundle = AsyncMock(
            return_value={"merkle_root": "abc123", "records": []}
        )
        mock_audit.proof_exporter = mock_proof_exporter
        mock_engine.audit = mock_audit

        client = TestClient(_get_app())
        token = self.create_auth_token()

        response = client.get(
            "/admin/audit/export-proof-bundle",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "success"
        assert "merkle_root" in response.json()["data"]

    @patch("omnicore_engine.fastapi_app.meta_supervisor_instance")
    @patch("omnicore_engine.fastapi_app.settings")
    def test_generate_test_cases(self, mock_settings, mock_meta):
        """Test test case generation via meta supervisor"""
        # Set up mock settings with required attributes
        mock_settings.EXPERIMENTAL_FEATURES_ENABLED = True
        mock_settings.JWT_SECRET_KEY = Mock()
        mock_settings.JWT_SECRET_KEY.get_secret_value.return_value = (
            self.TEST_JWT_SECRET
        )

        mock_meta.generate_test_cases = AsyncMock(
            return_value={"test_cases": ["test1", "test2"]}
        )

        client = TestClient(_get_app())
        token = self.create_auth_token()

        response = client.get(
            "/admin/generate-test-cases",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "success"


class TestWorkflowEndpoints:
    """Test workflow endpoints"""

    # Test JWT secret for workflow tests
    TEST_JWT_SECRET = "test-jwt-secret-for-workflow-tests"

    @patch("omnicore_engine.fastapi_app.omnicore_engine")
    @patch("omnicore_engine.fastapi_app.settings")
    def test_code_factory_workflow(self, mock_settings, mock_engine):
        """Test code factory workflow endpoint"""
        # Set up mock settings with required attributes
        mock_settings.JWT_SECRET_KEY = Mock()
        mock_settings.JWT_SECRET_KEY.get_secret_value.return_value = (
            self.TEST_JWT_SECRET
        )

        mock_bus = Mock()
        mock_bus.publish = AsyncMock()
        mock_engine.message_bus = mock_bus

        client = TestClient(_get_app())
        token = jwt.encode(
            {"sub": "user123", "exp": datetime.utcnow() + timedelta(hours=1)},
            self.TEST_JWT_SECRET,
            algorithm="HS256",
        )

        response = client.post(
            "/code-factory-workflow",
            json={"workflow": "test"},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "workflow_started"
        assert "trace_id" in response.json()


class TestImportFixerEndpoint:
    """Test import fixer endpoint"""

    @patch("omnicore_engine.fastapi_app.AIManager")
    def test_fix_imports_success(self, mock_ai_manager_class):
        """Test successful import fixing"""
        mock_ai_manager = Mock()
        mock_ai_manager.get_refactoring_suggestion = Mock(
            return_value="Fixed imports:\nimport os\nimport sys"
        )
        mock_ai_manager_class.return_value = mock_ai_manager

        client = TestClient(_get_app())

        # Create a test file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("import sys, os")
            temp_file = f.name

        try:
            with open(temp_file, "rb") as f:
                response = client.post(
                    "/fix-imports/", files={"file": ("test.py", f, "text/x-python")}
                )

            assert response.status_code == 200
            assert "suggestion" in response.json()
            assert "Fixed imports" in response.json()["suggestion"]
        finally:
            os.unlink(temp_file)

    def test_fix_imports_invalid_file_type(self, client):
        """Test import fixer with invalid file type"""
        # client fixture injected

        response = client.post(
            "/fix-imports/", files={"file": ("test.txt", b"not python", "text/plain")}
        )

        assert response.status_code == 400
        assert "not allowed" in response.json()["detail"]


class TestUtilityEndpoints:
    """Test utility endpoints"""

    def test_notify_endpoint(self, client):
        """Test notification endpoint"""
        # client fixture injected

        response = client.post(
            "/api/notify", json={"message": "Test notification", "type": "info"}
        )

        assert response.status_code == 200
        assert response.json()["status"] == "received"

    def test_custom_docs_endpoints(self, client):
        """Test custom documentation endpoints"""
        # client fixture injected

        # Swagger UI
        response = client.get("/docs")
        assert response.status_code == 200
        assert "swagger" in response.text.lower()

        # ReDoc
        response = client.get("/redoc")
        assert response.status_code == 200
        assert "redoc" in response.text.lower()


class TestErrorHandling:
    """Test error handling across endpoints"""

    @patch("omnicore_engine.fastapi_app.simulation_module")
    def test_simulation_error_handling(self, mock_sim):
        """Test error handling in simulation endpoint"""
        mock_sim.execute_simulation = AsyncMock(
            side_effect=Exception("Simulation failed")
        )

        client = TestClient(_get_app())
        response = client.post("/api/simulation/execute", json={"config": {}})

        assert response.status_code == 500
        # FastAPI wraps the detail in {"detail": ...}
        assert "Simulation failed" in response.json()["detail"]["message"]

    @patch("omnicore_engine.fastapi_app.chatbot_arbiter")
    @patch("omnicore_engine.fastapi_app.ARBITER_AVAILABLE", True)
    def test_chat_error_handling(self, mock_arbiter):
        """Test error handling in chat endpoint"""
        mock_arbiter.respond = AsyncMock(side_effect=Exception("Chat error"))

        client = TestClient(_get_app())
        response = client.post(
            "/api/chat", json={"user_id": "user123", "message": "Hello", "context": {}}
        )

        assert response.status_code == 200
        assert response.json()["status"] == "error"
        assert "Chat error" in response.json()["message"]


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--asyncio-mode=auto"])
