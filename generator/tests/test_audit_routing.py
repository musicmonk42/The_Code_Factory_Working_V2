# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Test audit log routing functionality.

Tests that audit events are properly routed to the OmniCore hub
when ROUTE_TO_MAIN_AUDIT is enabled.
"""

import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestAuditRouting:
    """Test suite for audit log routing to OmniCore hub."""
    
    def test_audit_config_has_routing_params(self):
        """Test that audit_config.yaml contains routing parameters."""
        import yaml
        from pathlib import Path
        
        config_path = Path(__file__).parent.parent / "audit_config.yaml"
        assert config_path.exists(), "audit_config.yaml not found"
        
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        
        # Check routing parameters exist
        assert "ROUTE_TO_MAIN_AUDIT" in config
        assert "MAIN_AUDIT_ENDPOINT" in config
        assert "ROUTING_RETRY_ENABLED" in config
        assert "ROUTING_MAX_ATTEMPTS" in config
        
        # Verify expected values
        assert config["ROUTE_TO_MAIN_AUDIT"] is True
        assert "localhost:8001" in config["MAIN_AUDIT_ENDPOINT"]
        assert config["ROUTING_MAX_ATTEMPTS"] >= 1
    
    def test_runner_audit_loads_config(self):
        """Test that runner_audit.py loads audit config on import."""
        # Re-import to test config loading
        import importlib
        from generator.runner import runner_audit
        
        importlib.reload(runner_audit)
        
        # Check that config values are available
        assert hasattr(runner_audit, 'ROUTE_TO_MAIN_AUDIT')
        assert hasattr(runner_audit, 'MAIN_AUDIT_ENDPOINT')
        assert hasattr(runner_audit, 'ROUTING_MAX_ATTEMPTS')
    
    @pytest.mark.asyncio
    async def test_route_audit_to_hub_success(self):
        """Test successful routing of audit event to hub."""
        from generator.runner import runner_audit
        import aiohttp
        
        # Mock aiohttp client
        mock_response = AsyncMock()
        mock_response.status = 200
        
        # Create an AsyncContextManager class for session.post()
        class MockPostContextManager:
            def __init__(self, response):
                self.response = response
            
            async def __aenter__(self):
                return self.response
            
            async def __aexit__(self, exc_type, exc_val, exc_tb):
                return None
        
        # Create a mock session instance
        mock_session_instance = MagicMock()
        
        # post should return an AsyncContextManager, not an async function
        mock_session_instance.post = MagicMock(
            return_value=MockPostContextManager(mock_response)
        )
        mock_session_instance.__aenter__ = AsyncMock(return_value=mock_session_instance)
        mock_session_instance.__aexit__ = AsyncMock(return_value=None)
        
        # Mock aiohttp.ClientSession to return our mock session
        mock_client_session = MagicMock(return_value=mock_session_instance)
        
        with patch.object(aiohttp, 'ClientSession', mock_client_session):
            with patch.object(runner_audit, 'HAS_AIOHTTP', True):
                with patch.object(runner_audit, 'ROUTE_TO_MAIN_AUDIT', True):
                    # Create a test audit entry
                    test_entry = {
                        "action": "test_action",
                        "timestamp": "2026-02-12T19:00:00Z",
                        "user": "testuser",
                        "data": {"key": "value"}
                    }
                    
                    # Call the routing function
                    await runner_audit._route_audit_to_hub(test_entry)
                    
                    # Verify POST was called
                    mock_client_session.assert_called_once()
                    # Verify session.post was called
                    mock_session_instance.post.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_route_audit_to_hub_retry_on_failure(self):
        """Test that routing retries on failure."""
        from generator.runner import runner_audit
        import aiohttp
        
        # Mock aiohttp client that fails
        mock_response = AsyncMock()
        mock_response.status = 503  # Service unavailable
        
        # Create an AsyncContextManager class for session.post()
        class MockPostContextManager:
            def __init__(self, response):
                self.response = response
            
            async def __aenter__(self):
                return self.response
            
            async def __aexit__(self, exc_type, exc_val, exc_tb):
                return None
        
        # Create a mock session instance
        def create_mock_session():
            mock_session = MagicMock()
            # post should return an AsyncContextManager, not an async function
            mock_session.post = MagicMock(
                return_value=MockPostContextManager(mock_response)
            )
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)
            return mock_session
        
        # Mock ClientSession to return new mock sessions
        mock_sessions = [create_mock_session() for _ in range(2)]
        mock_client_session_class = MagicMock(side_effect=mock_sessions)
        
        with patch.object(aiohttp, 'ClientSession', mock_client_session_class):
            with patch.object(runner_audit, 'HAS_AIOHTTP', True):
                with patch.object(runner_audit, 'ROUTE_TO_MAIN_AUDIT', True):
                    with patch.object(runner_audit, 'ROUTING_MAX_ATTEMPTS', 2):
                        test_entry = {
                            "action": "test_action",
                            "data": {"key": "value"}
                        }
                        
                        # Call routing function
                        await runner_audit._route_audit_to_hub(test_entry)
                        
                        # Verify ClientSession was called twice (once for each retry)
                        assert mock_client_session_class.call_count == 2
    
    @pytest.mark.asyncio
    async def test_route_audit_fallback_to_local(self):
        """Test fallback to local logging when hub is unavailable."""
        from generator.runner import runner_audit
        
        # Mock aiohttp to raise exception
        with patch('aiohttp.ClientSession', side_effect=Exception("Network error")):
            with patch.object(runner_audit, 'HAS_AIOHTTP', True):
                with patch.object(runner_audit, 'ROUTE_TO_MAIN_AUDIT', True):
                    with patch.object(runner_audit, 'FALLBACK_TO_LOCAL', True):
                        with patch.object(runner_audit, 'ROUTING_MAX_ATTEMPTS', 1):
                            test_entry = {
                                "action": "test_action",
                                "data": {"key": "value"}
                            }
                            
                            # Should not raise exception - just fallback
                            await runner_audit._route_audit_to_hub(test_entry)
                            # If we get here without exception, fallback worked
    
    def test_load_audit_routing_config_function_exists(self):
        """Test that load_audit_routing_config function exists in main.py."""
        from generator.main import main as main_module
        
        # Check function exists
        assert hasattr(main_module, 'load_audit_routing_config')
        assert callable(main_module.load_audit_routing_config)
    
    def test_unified_routing_config_exists(self):
        """Test that audit_routing_config.yaml exists at repo root."""
        from pathlib import Path
        
        # Path to unified routing config from repo root
        repo_root = Path(__file__).parent.parent.parent
        routing_config_path = repo_root / "audit_routing_config.yaml"
        
        assert routing_config_path.exists(), "audit_routing_config.yaml not found at repo root"
        
        # Load and verify generator config
        import yaml
        with open(routing_config_path, 'r') as f:
            routing_config = yaml.safe_load(f)
        
        assert "generator" in routing_config
        generator_config = routing_config["generator"]
        
        # Verify routing is enabled
        assert "route_all_events_to_hub" in generator_config
        assert "hub_endpoint" in generator_config


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
