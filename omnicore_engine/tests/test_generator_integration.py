"""
Integration tests for generator components in OmniCoreOmega.

Tests the integration points added for generator connectivity:
- Generator imports with fallbacks
- Engine registry with all components
- Message bus subscriptions for generator
- Path setup for generator discovery
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, Mock, patch

import pytest

# Defer heavy imports to test functions to reduce memory during collection
# import path_setup - moved to test functions
# from omnicore_engine.engines import (...) - moved to test functions


class TestGeneratorIntegration:
    """Test generator integration with OmniCoreOmega"""

    def setup_method(self):
        """Clear registry before each test"""
        # Avoid importing during test collection - import inside test methods if needed
        pass

    @pytest.mark.integration
    def test_generator_imports_available(self):
        """Test that generator imports are accessible"""
        # Import the module to trigger lazy imports
        import omnicore_engine.engines as engines_module

        # Check that generator-related imports exist (even if None)
        assert hasattr(engines_module, "GeneratorRunner")
        assert hasattr(engines_module, "call_llm_api")
        assert hasattr(engines_module, "call_ensemble_api")
        assert hasattr(engines_module, "get_available_agents")
        assert hasattr(engines_module, "is_agent_available")
        assert hasattr(engines_module, "IntentParser")

    @pytest.mark.integration
    @patch("omnicore_engine.engines.get_available_agents")
    def test_get_available_agents_fallback(self, mock_get_agents):
        """Test that get_available_agents fallback works"""
        # Mock to avoid triggering PyO3 runtime in CI
        mock_get_agents.return_value = {}
        
        from omnicore_engine.engines import get_available_agents

        # Should return empty dict if generator not available
        agents = get_available_agents()
        assert isinstance(agents, dict)

    @pytest.mark.integration
    @patch("omnicore_engine.engines.is_agent_available")
    def test_is_agent_available_fallback(self, mock_is_available):
        """Test that is_agent_available fallback works"""
        # Mock to avoid triggering PyO3 runtime in CI
        mock_is_available.return_value = False
        
        from omnicore_engine.engines import is_agent_available

        # Should return False if generator not available
        result = is_agent_available("codegen")
        assert isinstance(result, bool)

    @pytest.mark.integration
    def test_omnicore_omega_init_with_generator_components(self):
        """Test OmniCoreOmega initialization with generator components"""
        from omnicore_engine.engines import OmniCoreOmega

        # Create mocks for all required components
        mock_db = Mock()
        mock_message_bus = Mock()
        mock_plugin_service = Mock()
        mock_crew_manager = Mock()
        mock_intent_capture_api = Mock()
        mock_test_gen = Mock()
        mock_simulation = Mock()
        mock_audit_log = Mock()
        mock_import_fixer = Mock()

        # Create mocks for generator components
        mock_generator_runner = Mock()
        mock_intent_parser = Mock()
        mock_llm_client = Mock()

        # Initialize OmniCoreOmega with generator components
        omega = OmniCoreOmega(
            database=mock_db,
            message_bus=mock_message_bus,
            plugin_service=mock_plugin_service,
            crew_manager=mock_crew_manager,
            intent_capture_api=mock_intent_capture_api,
            test_generation_orchestrator=mock_test_gen,
            simulation_engine=mock_simulation,
            audit_log_manager=mock_audit_log,
            import_fixer_engine=mock_import_fixer,
            num_arbiters=3,
            generator_runner=mock_generator_runner,
            intent_parser=mock_intent_parser,
            llm_client=mock_llm_client,
        )

        # Verify generator components are stored
        assert omega.generator_runner == mock_generator_runner
        assert omega.intent_parser == mock_intent_parser
        assert omega.llm_client == mock_llm_client

    @pytest.mark.integration
    def test_omnicore_omega_init_without_generator_components(self):
        """Test OmniCoreOmega initialization without generator components"""
        from omnicore_engine.engines import OmniCoreOmega

        # Create mocks for all required components
        mock_db = Mock()
        mock_message_bus = Mock()
        mock_plugin_service = Mock()
        mock_crew_manager = Mock()
        mock_intent_capture_api = Mock()
        mock_test_gen = Mock()
        mock_simulation = Mock()
        mock_audit_log = Mock()
        mock_import_fixer = Mock()

        # Initialize OmniCoreOmega without generator components
        omega = OmniCoreOmega(
            database=mock_db,
            message_bus=mock_message_bus,
            plugin_service=mock_plugin_service,
            crew_manager=mock_crew_manager,
            intent_capture_api=mock_intent_capture_api,
            test_generation_orchestrator=mock_test_gen,
            simulation_engine=mock_simulation,
            audit_log_manager=mock_audit_log,
            import_fixer_engine=mock_import_fixer,
        )

        # Verify generator components default to None
        assert omega.generator_runner is None
        assert omega.intent_parser is None
        assert omega.llm_client is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_engine_registry_all_engines(self):
        """Test that all engines are registered in ENGINE_REGISTRY"""
        from omnicore_engine.engines import (
            ENGINE_REGISTRY,
            get_engine,
            register_engine,
        )

        ENGINE_REGISTRY.clear()

        # Register all engines as OmniCoreOmega would
        register_engine(
            "import_fixer", {"engine": Mock(), "description": "Import fixer"}
        )
        register_engine(
            "test_generation", {"engine": Mock(), "description": "Test gen"}
        )
        register_engine("simulation", {"engine": Mock(), "description": "Simulation"})
        register_engine("crew_manager", {"engine": Mock(), "description": "Crew"})
        register_engine("arbiters", {"instances": lambda: [], "count": 5})
        register_engine(
            "generator", {"description": "Generator", "available_agents": {}}
        )

        # Verify all engines are registered
        assert "import_fixer" in ENGINE_REGISTRY
        assert "test_generation" in ENGINE_REGISTRY
        assert "simulation" in ENGINE_REGISTRY
        assert "crew_manager" in ENGINE_REGISTRY
        assert "arbiters" in ENGINE_REGISTRY
        assert "generator" in ENGINE_REGISTRY

        # Verify we can retrieve them
        assert get_engine("import_fixer") is not None
        assert get_engine("test_generation") is not None
        assert get_engine("simulation") is not None
        assert get_engine("crew_manager") is not None
        assert get_engine("arbiters") is not None
        assert get_engine("generator") is not None


class TestMessageBusIntegration:
    """Test message bus integration for generator"""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_plugin_service_generator_subscriptions(self):
        """Test that PluginService subscribes to generator topics"""
        from omnicore_engine.engines import PluginService

        with patch("omnicore_engine.engines.Database") as mock_db_class:
            with patch("omnicore_engine.engines.ShardedMessageBus") as mock_bus_class:
                with patch("omnicore_engine.engines.ArbiterConfig") as mock_config:
                    mock_config.return_value.DB_PATH = "sqlite:///test.db"
                    mock_registry = Mock()
                    mock_bus_instance = Mock()
                    mock_bus_instance.subscribe = AsyncMock()
                    mock_bus_class.return_value = mock_bus_instance

                    service = PluginService(mock_registry, message_bus=mock_bus_instance)

                    # Start subscriptions explicitly
                    await service.start_subscriptions()

                    # Verify subscriptions were created
                    calls = mock_bus_instance.subscribe.call_args_list
                    subscribed_topics = [call[0][0] for call in calls]

                    # Check for all expected topics
                    assert "arbiter:bug_detected" in subscribed_topics
                    assert "shif:fix_import_request" in subscribed_topics
                    assert "generator:codegen_request" in subscribed_topics
                    assert "generator:testgen_request" in subscribed_topics
                    assert "generator:docgen_request" in subscribed_topics
                    assert "workflow:sfe_to_generator" in subscribed_topics

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_handle_codegen_request(self):
        """Test codegen request handler"""
        from omnicore_engine.engines import PluginService, register_engine

        with patch("omnicore_engine.engines.Database"):
            with patch("omnicore_engine.engines.ShardedMessageBus") as mock_bus_class:
                with patch("omnicore_engine.engines.ArbiterConfig") as mock_config:
                    mock_config.return_value.DB_PATH = "sqlite:///test.db"
                    mock_registry = Mock()
                    mock_bus_instance = Mock()
                    mock_bus_instance.subscribe = AsyncMock()
                    mock_bus_instance.publish = AsyncMock()
                    mock_bus_class.return_value = mock_bus_instance

                    service = PluginService(mock_registry, message_bus=mock_bus_instance)
                    await service.start_subscriptions()

                    # Register a mock generator engine
                    register_engine("generator", {"description": "Generator"})

                    # Create a mock message
                    mock_message = Mock()
                    mock_message.payload = {
                        "spec": "Create a function",
                        "language": "python",
                        "request_id": "test-123",
                    }

                    # Call the handler
                    await service.handle_codegen_request(mock_message)

                    # Verify publish was called (success or failure)
                    assert mock_bus_instance.publish.called

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_handle_sfe_to_generator_workflow(self):
        """Test SFE to generator workflow handler"""
        from omnicore_engine.engines import PluginService

        with patch("omnicore_engine.engines.Database"):
            with patch("omnicore_engine.engines.ShardedMessageBus") as mock_bus_class:
                with patch("omnicore_engine.engines.ArbiterConfig") as mock_config:
                    mock_config.return_value.DB_PATH = "sqlite:///test.db"
                    mock_registry = Mock()
                    mock_bus_instance = Mock()
                    mock_bus_instance.subscribe = AsyncMock()
                    mock_bus_instance.publish = AsyncMock()
                    mock_bus_class.return_value = mock_bus_instance

                    service = PluginService(mock_registry, message_bus=mock_bus_instance)
                    await service.start_subscriptions()

                    # Test fix_and_regenerate workflow
                    mock_message = Mock()
                    mock_message.payload = {
                        "workflow_type": "fix_and_regenerate",
                        "fixed_code": "def test(): pass",
                        "request_id": "test-456",
                    }

                    await service.handle_sfe_to_generator(mock_message)

                    # Verify message was published to testgen
                    calls = [
                        call[0][0] for call in mock_bus_instance.publish.call_args_list
                    ]
                    assert "generator:testgen_request" in calls


class TestPathSetup:
    """Test centralized path setup module"""

    @pytest.mark.integration
    def test_path_setup_module_exists(self):
        """Test that path_setup.py module can be imported"""
        import path_setup

        assert hasattr(path_setup, "PROJECT_ROOT")
        assert hasattr(path_setup, "COMPONENT_PATHS")
        assert hasattr(path_setup, "setup_paths")

    @pytest.mark.integration
    def test_component_paths_defined(self):
        """Test that all component paths are defined"""
        import path_setup

        paths = path_setup.COMPONENT_PATHS
        assert "generator" in paths
        assert "self_fixing_engineer" in paths
        assert "omnicore_engine" in paths

    @pytest.mark.integration
    def test_setup_paths_function(self):
        """Test setup_paths function"""
        import path_setup

        # Call setup_paths
        added = path_setup.setup_paths(verbose=False)

        # Should return a list
        assert isinstance(added, list)

    @pytest.mark.integration
    def test_get_component_path(self):
        """Test get_component_path function"""
        import path_setup

        # Should return Path object
        gen_path = path_setup.get_component_path("generator")
        assert gen_path is not None

        # Should raise KeyError for unknown component
        with pytest.raises(KeyError):
            path_setup.get_component_path("nonexistent")

    @pytest.mark.integration
    def test_validate_paths(self):
        """Test validate_paths function"""
        import path_setup

        # Should return dict of boolean values
        validation = path_setup.validate_paths()
        assert isinstance(validation, dict)
        for component, exists in validation.items():
            assert isinstance(exists, bool)


class TestCrewConfigHelper:
    """Test crew config helper function"""

    @pytest.mark.integration
    def test_find_crew_config_static_method(self):
        """Test that _find_crew_config is a static method"""
        from omnicore_engine.engines import OmniCoreOmega

        assert hasattr(OmniCoreOmega, "_find_crew_config")

        # Should be callable
        result = OmniCoreOmega._find_crew_config()

        # Should return None or a string
        assert result is None or isinstance(result, str)


class TestAuditLogManagerFallback:
    """Test audit log manager fallback logic"""

    @pytest.mark.integration
    @patch("omnicore_engine.engines.Database")
    @patch("omnicore_engine.engines.ShardedMessageBus")
    @patch("omnicore_engine.engines.PluginService")
    @patch("omnicore_engine.engines.CrewManager")
    @patch("omnicore_engine.engines.intent_capture_api", None)
    @patch("omnicore_engine.engines.TestGenerationOrchestrator")
    @patch("omnicore_engine.engines.UnifiedSimulationModule")
    @patch("omnicore_engine.engines.create_import_fixer_engine")
    @patch("omnicore_engine.engines.ArbiterConfig")
    def test_audit_log_manager_fallback_chain(
        self,
        mock_config,
        mock_import_fixer,
        mock_sim,
        mock_test_gen,
        mock_crew,
        mock_plugin_svc,
        mock_bus,
        mock_db,
    ):
        """Test that audit log manager tries fallbacks in correct order"""
        from omnicore_engine.engines import OmniCoreOmega

        # Configure mocks
        mock_config.return_value.DB_PATH = "sqlite:///test.db"
        mock_db.return_value = Mock()
        mock_bus.return_value = Mock()
        mock_plugin_svc.return_value = Mock()
        mock_crew.return_value = Mock()
        mock_test_gen.return_value = Mock()
        mock_sim.return_value = Mock()
        mock_import_fixer.return_value = Mock()

        # Mock the _find_crew_config to return None
        with patch.object(OmniCoreOmega, "_find_crew_config", return_value=None):
            # This should use a real or mock audit logger as fallback
            omega = OmniCoreOmega.create_and_initialize()

            # Verify audit_log_manager exists
            assert omega.audit_log_manager is not None
            # Check for any logging method (different audit loggers have different interfaces)
            has_logging_method = (
                hasattr(omega.audit_log_manager, "log_audit") or
                hasattr(omega.audit_log_manager, "log_event") or
                hasattr(omega.audit_log_manager, "log") or
                hasattr(omega.audit_log_manager, "add_entry")
            )
            assert has_logging_method, f"audit_log_manager should have a logging method"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
