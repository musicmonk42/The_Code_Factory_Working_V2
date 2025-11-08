"""
Test suite for omnicore_engine/fastapi_app.py
Tests FastAPI endpoints, middleware, and startup/shutdown events.
"""

import pytest
import asyncio
import json
import jwt
from datetime import datetime, timedelta
from unittest.mock import Mock, MagicMock, patch, AsyncMock, mock_open
from fastapi.testclient import TestClient
from fastapi import HTTPException
import tempfile
from pathlib import Path
import sys
import os

# Add the parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mock dependencies before importing the app
with patch('fastapi_app.omnicore_engine') as mock_engine:
    with patch('omnicore_engine.fastapi_app.settings') as mock_settings:
        mock_settings.LOG_LEVEL = "INFO"
        mock_settings.JWT_SECRET_KEY.get_secret_value.return_value = "test_secret"
        mock_settings.ENCRYPTION_KEY.get_secret_value.return_value = "test_encryption_key_32_bytes_long!"
        mock_settings.database_path = "sqlite:///:memory:"
        mock_settings.redis_url = "redis://localhost"
        mock_settings.ARENA_PORT = 8001
        mock_settings.MERKLE_TREE_BRANCHING_FACTOR = 2
        mock_settings.MERKLE_TREE_PRIVATE_KEY = None
        mock_settings.EXPERIMENTAL_FEATURES_ENABLED = True
        
        from omnicore_engine.fastapi_app import app, router, admin_router


class TestStartupShutdown:
    """Test startup and shutdown events"""
    
    @pytest.mark.asyncio
    @patch('omnicore_engine.fastapi_app.omnicore_engine')
    @patch('omnicore_engine.fastapi_app.UnifiedSimulationModule')
    @patch('omnicore_engine.fastapi_app.MetaSupervisor')
    async def test_startup_event_success(self, mock_meta, mock_sim, mock_engine):
        """Test successful startup"""
        mock_engine.initialize = AsyncMock()
        mock_engine.database = Mock()
        mock_engine.message_bus = Mock()
        mock_sim_instance = Mock()
        mock_sim_instance.initialize = AsyncMock()
        mock_sim.return_value = mock_sim_instance
        
        mock_meta_instance = Mock()
        mock_meta_instance.initialize = AsyncMock()
        mock_meta_instance.run = AsyncMock()
        mock_meta.return_value = mock_meta_instance
        
        from omnicore_engine.fastapi_app import startup_event_fastapi
        
        await startup_event_fastapi()
        
        mock_engine.initialize.assert_called_once()
        mock_sim_instance.initialize.assert_called_once()
        mock_meta_instance.initialize.assert_called_once()
    
    @pytest.mark.asyncio
    @patch('omnicore_engine.fastapi_app.omnicore_engine')
    @patch('omnicore_engine.fastapi_app.simulation_module')
    @patch('omnicore_engine.fastapi_app.chatbot_arbiter')
    @patch('omnicore_engine.fastapi_app.arena')
    @patch('omnicore_engine.fastapi_app.meta_supervisor_instance')
    async def test_shutdown_event(self, mock_meta, mock_arena, mock_arbiter, mock_sim, mock_engine):
        """Test shutdown event"""
        mock_engine.shutdown = AsyncMock()
        mock_sim.shutdown = AsyncMock()
        mock_arbiter.stop_async_services = AsyncMock()
        mock_arena.stop_arena_services = AsyncMock()
        mock_meta.stop = AsyncMock()
        
        from omnicore_engine.fastapi_app import shutdown_event_fastapi
        
        await shutdown_event_fastapi()
        
        mock_engine.shutdown.assert_called_once()
        mock_sim.shutdown.assert_called_once()
        mock_arbiter.stop_async_services.assert_called_once()
        mock_arena.stop_arena_services.assert_called_once()
        mock_meta.stop.assert_called_once()


class TestSecurityMiddleware:
    """Test security middleware and authentication"""
    
    def test_size_limit_middleware(self):
        """Test request size limiting"""
        client = TestClient(app)
        
        # Create large payload
        large_data = "x" * 11_000_000  # 11MB
        
        response = client.post(
            "/api/notify",
            json={"data": large_data},
            headers={"content-length": str(len(large_data))}
        )
        
        assert response.status_code == 413
        assert "Request too large" in response.json()["error"]
    
    def test_csrf_protection(self):
        """Test CSRF protection"""
        client = TestClient(app)
        
        # Request without CSRF token should fail for protected endpoints
        # This would need actual CSRF testing setup
        pass
    
    def test_jwt_authentication(self):
        """Test JWT token validation"""
        from omnicore_engine.fastapi_app import get_user_id
        
        # Valid token
        valid_token = jwt.encode(
            {"sub": "user123", "exp": datetime.utcnow() + timedelta(hours=1)},
            "test_secret",
            algorithm="HS256"
        )
        
        # Should work with valid token
        user_id = asyncio.run(get_user_id(valid_token))
        assert user_id == "user123"
        
        # Expired token
        expired_token = jwt.encode(
            {"sub": "user123", "exp": datetime.utcnow() - timedelta(hours=1)},
            "test_secret",
            algorithm="HS256"
        )
        
        with pytest.raises(HTTPException) as exc:
            asyncio.run(get_user_id(expired_token))
        assert exc.value.status_code == 401
        assert "expired" in exc.value.detail.lower()
        
        # Invalid token
        with pytest.raises(HTTPException) as exc:
            asyncio.run(get_user_id("invalid_token"))
        assert exc.value.status_code == 401


class TestHealthEndpoint:
    """Test health check endpoint"""
    
    @patch('omnicore_engine.fastapi_app.omnicore_engine')
    def test_health_check(self, mock_engine):
        """Test /health endpoint"""
        mock_engine.health_check = AsyncMock(return_value={
            "status": "healthy",
            "components": {"database": "ok", "message_bus": "ok"}
        })
        
        client = TestClient(app)
        response = client.get("/api/health")
        
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"


class TestChatEndpoint:
    """Test chat endpoint"""
    
    @patch('omnicore_engine.fastapi_app.chatbot_arbiter')
    @patch('omnicore_engine.fastapi_app.ARBITER_AVAILABLE', True)
    def test_chat_success(self, mock_arbiter):
        """Test successful chat interaction"""
        mock_arbiter.respond = AsyncMock(return_value="Hello! How can I help?")
        
        client = TestClient(app)
        response = client.post(
            "/api/chat",
            json={
                "user_id": "user123",
                "message": "Hello",
                "context": {}
            }
        )
        
        assert response.status_code == 200
        assert response.json()["response"] == "Hello! How can I help?"
        assert response.json()["status"] == "success"
    
    @patch('omnicore_engine.fastapi_app.ARBITER_AVAILABLE', False)
    def test_chat_unavailable(self):
        """Test chat when arbiter not available"""
        client = TestClient(app)
        response = client.post(
            "/api/chat",
            json={
                "user_id": "user123",
                "message": "Hello",
                "context": {}
            }
        )
        
        assert response.status_code == 200
        assert response.json()["status"] == "error"
        assert "unavailable" in response.json()["response"].lower()


class TestSimulationEndpoints:
    """Test simulation endpoints"""
    
    @patch('omnicore_engine.fastapi_app.simulation_module')
    def test_execute_simulation_success(self, mock_sim):
        """Test simulation execution"""
        mock_sim.execute_simulation = AsyncMock(return_value={
            "result": "simulation_completed",
            "metrics": {"accuracy": 0.95}
        })
        
        client = TestClient(app)
        response = client.post(
            "/api/simulation/execute",
            json={"config": {"param": "value"}}
        )
        
        assert response.status_code == 200
        assert response.json()["status"] == "success"
        assert response.json()["result"]["result"] == "simulation_completed"
    
    @patch('omnicore_engine.fastapi_app.simulation_module', None)
    def test_execute_simulation_not_initialized(self):
        """Test simulation when module not initialized"""
        client = TestClient(app)
        response = client.post(
            "/api/simulation/execute",
            json={"config": {}}
        )
        
        assert response.status_code == 500
        assert "not initialized" in response.json()["detail"]
    
    @patch('omnicore_engine.fastapi_app.simulation_module')
    def test_explain_simulation_success(self, mock_sim):
        """Test simulation explanation"""
        mock_sim.explain_result = AsyncMock(return_value="This simulation shows...")
        
        client = TestClient(app)
        response = client.post(
            "/api/simulation/explain",
            json={"result": {"data": "value"}}
        )
        
        assert response.status_code == 200
        assert response.json()["status"] == "success"
        assert response.json()["explanation"] == "This simulation shows..."


class TestTestGenerationEndpoints:
    """Test test generation endpoints"""
    
    @patch('omnicore_engine.fastapi_app.omnicore_engine')
    def test_run_test_generation_success(self, mock_engine):
        """Test test generation endpoint"""
        mock_orchestrator = Mock()
        mock_orchestrator.generate_tests_for_targets = AsyncMock(
            return_value={"tests_generated": 10}
        )
        mock_engine.test_generation_orchestrator = mock_orchestrator
        
        client = TestClient(app)
        response = client.post(
            "/api/test-generation/run",
            json={
                "targets": [{"file": "test.py"}],
                "config": {}
            }
        )
        
        assert response.status_code == 200
        assert response.json()["status"] == "success"
        assert response.json()["result"]["tests_generated"] == 10
    
    @patch('omnicore_engine.fastapi_app.PLUGIN_REGISTRY')
    def test_run_test_generation_plugin(self, mock_registry):
        """Test plugin-based test generation"""
        mock_plugin = Mock()
        mock_plugin.execute = AsyncMock(return_value={"tests": ["test1", "test2"]})
        mock_registry.get.return_value = mock_plugin
        
        client = TestClient(app)
        response = client.post(
            "/api/scenarios/test_generation/run",
            json={
                "code": "def test(): pass",
                "language": "python",
                "config": {}
            }
        )
        
        assert response.status_code == 200
        assert "tests" in response.json()


class TestAdminEndpoints:
    """Test admin endpoints"""
    
    def create_auth_token(self):
        """Helper to create valid auth token"""
        return jwt.encode(
            {"sub": "admin_user", "exp": datetime.utcnow() + timedelta(hours=1)},
            "test_secret",
            algorithm="HS256"
        )
    
    @patch('omnicore_engine.fastapi_app.settings')
    def test_admin_api_disabled(self, mock_settings):
        """Test admin API when disabled"""
        mock_settings.EXPERIMENTAL_FEATURES_ENABLED = False
        
        client = TestClient(app)
        response = client.get("/admin/feature-flag")
        
        assert response.status_code == 404
    
    @patch('omnicore_engine.fastapi_app.omnicore_engine')
    @patch('omnicore_engine.fastapi_app.PluginMarketplace')
    def test_install_plugin(self, mock_marketplace_class, mock_engine):
        """Test plugin installation"""
        mock_marketplace = Mock()
        mock_marketplace.install_plugin = AsyncMock()
        mock_marketplace_class.return_value = mock_marketplace
        mock_engine.database = Mock()
        
        client = TestClient(app)
        token = self.create_auth_token()
        
        response = client.post(
            "/admin/plugins/install",
            json={
                "kind": "execution",
                "name": "test_plugin",
                "version": "1.0.0"
            },
            headers={"Authorization": f"Bearer {token}"}
        )
        
        assert response.status_code == 200
        assert "installed" in response.json()["message"]
    
    @patch('omnicore_engine.fastapi_app.omnicore_engine')
    @patch('omnicore_engine.fastapi_app.PluginMarketplace')
    def test_rate_plugin(self, mock_marketplace_class, mock_engine):
        """Test plugin rating"""
        mock_marketplace = Mock()
        mock_marketplace.rate_plugin = AsyncMock()
        mock_marketplace_class.return_value = mock_marketplace
        mock_engine.database = Mock()
        
        client = TestClient(app)
        token = self.create_auth_token()
        
        response = client.post(
            "/admin/plugins/rate",
            json={
                "kind": "execution",
                "name": "test_plugin",
                "version": "1.0.0",
                "rating": 5,
                "comment": "Great plugin!"
            },
            headers={"Authorization": f"Bearer {token}"}
        )
        
        assert response.status_code == 200
        assert "rated" in response.json()["message"]
    
    @patch('omnicore_engine.fastapi_app.omnicore_engine')
    def test_export_audit_proof_bundle(self, mock_engine):
        """Test audit proof bundle export"""
        mock_audit = Mock()
        mock_proof_exporter = Mock()
        mock_proof_exporter.export_proof_bundle = AsyncMock(return_value={
            "merkle_root": "abc123",
            "records": []
        })
        mock_audit.proof_exporter = mock_proof_exporter
        mock_engine.audit = mock_audit
        
        client = TestClient(app)
        token = self.create_auth_token()
        
        response = client.get(
            "/admin/audit/export-proof-bundle",
            headers={"Authorization": f"Bearer {token}"}
        )
        
        assert response.status_code == 200
        assert response.json()["status"] == "success"
        assert "merkle_root" in response.json()["data"]
    
    @patch('omnicore_engine.fastapi_app.meta_supervisor_instance')
    def test_generate_test_cases(self, mock_meta):
        """Test test case generation via meta supervisor"""
        mock_meta.generate_test_cases = AsyncMock(return_value={
            "test_cases": ["test1", "test2"]
        })
        
        client = TestClient(app)
        token = self.create_auth_token()
        
        response = client.get(
            "/admin/generate-test-cases",
            headers={"Authorization": f"Bearer {token}"}
        )
        
        assert response.status_code == 200
        assert response.json()["status"] == "success"


class TestWorkflowEndpoints:
    """Test workflow endpoints"""
    
    @patch('omnicore_engine.fastapi_app.omnicore_engine')
    def test_code_factory_workflow(self, mock_engine):
        """Test code factory workflow endpoint"""
        mock_bus = Mock()
        mock_bus.publish = AsyncMock()
        mock_engine.message_bus = mock_bus
        
        client = TestClient(app)
        token = jwt.encode(
            {"sub": "user123", "exp": datetime.utcnow() + timedelta(hours=1)},
            "test_secret",
            algorithm="HS256"
        )
        
        response = client.post(
            "/code-factory-workflow",
            json={"workflow": "test"},
            headers={"Authorization": f"Bearer {token}"}
        )
        
        assert response.status_code == 200
        assert response.json()["status"] == "workflow_started"
        assert "trace_id" in response.json()


class TestImportFixerEndpoint:
    """Test import fixer endpoint"""
    
    @patch('omnicore_engine.fastapi_app.AIManager')
    def test_fix_imports_success(self, mock_ai_manager_class):
        """Test successful import fixing"""
        mock_ai_manager = Mock()
        mock_ai_manager.get_refactoring_suggestion = Mock(
            return_value="Fixed imports:\nimport os\nimport sys"
        )
        mock_ai_manager_class.return_value = mock_ai_manager
        
        client = TestClient(app)
        
        # Create a test file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write("import sys, os")
            temp_file = f.name
        
        try:
            with open(temp_file, 'rb') as f:
                response = client.post(
                    "/fix-imports/",
                    files={"file": ("test.py", f, "text/x-python")}
                )
            
            assert response.status_code == 200
            assert "suggestion" in response.json()
            assert "Fixed imports" in response.json()["suggestion"]
        finally:
            os.unlink(temp_file)
    
    def test_fix_imports_invalid_file_type(self):
        """Test import fixer with invalid file type"""
        client = TestClient(app)
        
        response = client.post(
            "/fix-imports/",
            files={"file": ("test.txt", b"not python", "text/plain")}
        )
        
        assert response.status_code == 400
        assert "not allowed" in response.json()["detail"]


class TestUtilityEndpoints:
    """Test utility endpoints"""
    
    def test_notify_endpoint(self):
        """Test notification endpoint"""
        client = TestClient(app)
        
        response = client.post(
            "/api/notify",
            json={"message": "Test notification", "type": "info"}
        )
        
        assert response.status_code == 200
        assert response.json()["status"] == "received"
    
    def test_custom_docs_endpoints(self):
        """Test custom documentation endpoints"""
        client = TestClient(app)
        
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
    
    @patch('omnicore_engine.fastapi_app.simulation_module')
    def test_simulation_error_handling(self, mock_sim):
        """Test error handling in simulation endpoint"""
        mock_sim.execute_simulation = AsyncMock(
            side_effect=Exception("Simulation failed")
        )
        
        client = TestClient(app)
        response = client.post(
            "/api/simulation/execute",
            json={"config": {}}
        )
        
        assert response.status_code == 500
        assert "Simulation failed" in response.json()["message"]
    
    @patch('omnicore_engine.fastapi_app.chatbot_arbiter')
    @patch('omnicore_engine.fastapi_app.ARBITER_AVAILABLE', True)
    def test_chat_error_handling(self, mock_arbiter):
        """Test error handling in chat endpoint"""
        mock_arbiter.respond = AsyncMock(
            side_effect=Exception("Chat error")
        )
        
        client = TestClient(app)
        response = client.post(
            "/api/chat",
            json={
                "user_id": "user123",
                "message": "Hello",
                "context": {}
            }
        )
        
        assert response.status_code == 200
        assert response.json()["status"] == "error"
        assert "Chat error" in response.json()["message"]


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--asyncio-mode=auto"])