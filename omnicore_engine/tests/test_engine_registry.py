# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Test suite for omnicore_engine/engines.py
Tests engine registry, plugin service, and OmniCoreOmega orchestrator.
"""

import asyncio
import copy
import os

# Add the parent directory to path for imports
import sys
from unittest.mock import AsyncMock, Mock, mock_open, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Defer heavy imports to test functions to reduce memory during collection
# from omnicore_engine.engines import (...) - moved to test functions

# Disable parallel execution for tests that modify shared ENGINE_REGISTRY
# Also mark as not requiring forked mode to avoid subprocess crashes
# when running with pytest-xdist --forked flag
pytestmark = [
    pytest.mark.xdist_group(name="engine_registry_serial"),
]


class TestEngineRegistry:
    """Test the engine registry functions"""

    @pytest.mark.integration
    def test_register_engine_success(self):
        """Test successful engine registration"""
        from omnicore_engine.engines import ENGINE_REGISTRY, register_engine

        entrypoints = {"initialize": Mock(), "shutdown": Mock(), "execute": Mock()}
        
        register_engine("test_engine", entrypoints)
        
        assert "test_engine" in ENGINE_REGISTRY
        assert ENGINE_REGISTRY["test_engine"] == entrypoints

    @pytest.mark.integration
    def test_register_engine_invalid_entrypoints(self):
        """Test registration with invalid entrypoints"""
        from omnicore_engine.engines import register_engine

        with pytest.raises(TypeError, match="Entrypoints must be a dictionary"):
            register_engine("bad_engine", "not_a_dict")

        with pytest.raises(TypeError, match="Entrypoints must be a dictionary"):
            register_engine("bad_engine", ["list", "not", "dict"])

    @pytest.mark.integration
    def test_get_engine_exists(self):
        """Test retrieving an existing engine"""
        from omnicore_engine.engines import ENGINE_REGISTRY, get_engine

        entrypoints = {"func": Mock()}
        ENGINE_REGISTRY["existing_engine"] = entrypoints

        result = get_engine("existing_engine")
        assert result == entrypoints

    @pytest.mark.integration
    def test_get_engine_not_exists(self):
        """Test retrieving non-existent engine"""
        from omnicore_engine.engines import get_engine

        result = get_engine("nonexistent_engine")
        assert result is None


class TestPluginService:
    """Test the PluginService class"""

    @pytest.fixture
    def mock_dependencies(self):
        """Create mock dependencies without module-level patches"""
        mock_registry = Mock()
        
        # Each connection to :memory: creates an isolated database
        # No need for worker-specific paths as connections are already isolated
        db_path = "sqlite:///:memory:"
        
        # Create mock performance tracker
        mock_performance_tracker = Mock()
        mock_performance_tracker.track_operation = AsyncMock()
        mock_performance_tracker.record_metric = Mock()
        
        mock_bus_instance = Mock()
        mock_bus_instance.subscribe = AsyncMock()
        mock_bus_instance.publish = AsyncMock()
        mock_bus_instance.performance_tracker = mock_performance_tracker
        
        mock_db = Mock()
        mock_db.DB_PATH = db_path
        
        mock_config = Mock()
        mock_config.return_value.DB_PATH = db_path
        
        return {
            "registry": mock_registry,
            "bus": mock_bus_instance,
            "db": mock_db,
            "config": mock_config,
        }

    @pytest.mark.asyncio
    async def test_plugin_service_initialization(self, mock_dependencies):
        """Test PluginService initialization"""
        # Import INSIDE test to avoid collection-time failures
        from omnicore_engine.engines import PluginService

        service = PluginService(
            mock_dependencies["registry"],
            message_bus=mock_dependencies["bus"]
        )
        await service.start_subscriptions()
        assert service.plugin_registry == mock_dependencies["registry"]
        assert service.message_bus is not None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_handle_arbiter_bug(self, mock_dependencies):
        """Test handling arbiter bug events"""
        from omnicore_engine.engines import PluginService

        service = PluginService(
            mock_dependencies["registry"],
            message_bus=mock_dependencies["bus"]
        )
        await service.start_subscriptions()

        with patch("omnicore_engine.engines.BugManager") as mock_bug_manager:
            mock_manager_instance = Mock()
            mock_manager_instance.report_bug = AsyncMock()
            mock_bug_manager.return_value = mock_manager_instance

            message = Mock()
            message.payload = {"bug": "test_bug", "severity": "high"}

            await service.handle_arbiter_bug(message)

            mock_manager_instance.report_bug.assert_called_once_with(message.payload)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_handle_shif_request_with_path(self, mock_dependencies):
        """Test handling SHIF request with file path"""
        from omnicore_engine.engines import ENGINE_REGISTRY, PluginService

        with patch.dict(ENGINE_REGISTRY, {}, clear=True):
            service = PluginService(
                mock_dependencies["registry"],
                message_bus=mock_dependencies["bus"]
            )
            await service.start_subscriptions()

            # Setup mock import fixer engine
            mock_fixer = Mock()
            mock_fixer.fix_file = AsyncMock(return_value="fixed code")
            ENGINE_REGISTRY["import_fixer"] = {"engine": mock_fixer}

            message = Mock()
            message.payload = {"path": "/test/file.py"}

            await service.handle_shif_request(message)

            mock_fixer.fix_file.assert_called_once_with("/test/file.py")
            service.message_bus.publish.assert_called_with(
                "shif:fix_import_success",
                {"path": "/test/file.py", "fixed_code": "fixed code"},
            )

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_handle_shif_request_with_code(self, mock_dependencies):
        """Test handling SHIF request with code string"""
        from omnicore_engine.engines import ENGINE_REGISTRY, PluginService

        with patch.dict(ENGINE_REGISTRY, {}, clear=True):
            service = PluginService(
                mock_dependencies["registry"],
                message_bus=mock_dependencies["bus"]
            )
            await service.start_subscriptions()

            mock_fixer = Mock()
            mock_fixer.fix_code = AsyncMock(return_value="fixed code")
            ENGINE_REGISTRY["import_fixer"] = {"engine": mock_fixer}

            message = Mock()
            message.payload = {"code": "import broken"}

            await service.handle_shif_request(message)

            mock_fixer.fix_code.assert_called_once_with("import broken")
            service.message_bus.publish.assert_called_with(
                "shif:fix_import_success", {"fixed_code": "fixed code"}
            )

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_handle_shif_request_no_engine(self, mock_dependencies):
        """Test handling SHIF request when engine not registered"""
        from omnicore_engine.engines import ENGINE_REGISTRY, PluginService

        with patch.dict(ENGINE_REGISTRY, {}, clear=True):
            service = PluginService(
                mock_dependencies["registry"],
                message_bus=mock_dependencies["bus"]
            )
            await service.start_subscriptions()
            ENGINE_REGISTRY.clear()

            message = Mock()
            message.payload = {"path": "/test/file.py"}

            await service.handle_shif_request(message)

            # Should log error and return early
            service.message_bus.publish.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_handle_shif_request_error(self, mock_dependencies):
        """Test handling SHIF request when fixer raises error"""
        from omnicore_engine.engines import ENGINE_REGISTRY, PluginService

        with patch.dict(ENGINE_REGISTRY, {}, clear=True):
            service = PluginService(
                mock_dependencies["registry"],
                message_bus=mock_dependencies["bus"]
            )
            await service.start_subscriptions()

            mock_fixer = Mock()
            mock_fixer.fix_file = AsyncMock(side_effect=Exception("Fix failed"))
            ENGINE_REGISTRY["import_fixer"] = {"engine": mock_fixer}

            message = Mock()
            message.payload = {"path": "/test/file.py"}

            await service.handle_shif_request(message)

            service.message_bus.publish.assert_called_with(
                "shif:fix_import_failure", {"error": "Fix failed", "path": "/test/file.py"}
            )

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_companies_success(self, mock_dependencies):
        """Test getting companies list"""
        from omnicore_engine.engines import PluginService

        service = PluginService(
            mock_dependencies["registry"],
            message_bus=mock_dependencies["bus"]
        )
        await service.start_subscriptions()

        mock_fetcher = AsyncMock(return_value=["Company1", "Company2"])
        mock_dependencies["registry"].get.return_value = mock_fetcher

        result = await service.get_companies()

        assert result == ["Company1", "Company2"]
        mock_dependencies["registry"].get.assert_called_with("company_list")

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_companies_no_plugin(self, mock_dependencies):
        """Test getting companies when plugin not registered"""
        from omnicore_engine.engines import PluginService

        service = PluginService(
            mock_dependencies["registry"],
            message_bus=mock_dependencies["bus"]
        )
        await service.start_subscriptions()
        mock_dependencies["registry"].get.return_value = None

        with pytest.raises(RuntimeError, match="No company_list plugin registered"):
            await service.get_companies()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_esg_success(self, mock_dependencies):
        """Test getting ESG report"""
        from omnicore_engine.engines import PluginService

        service = PluginService(
            mock_dependencies["registry"],
            message_bus=mock_dependencies["bus"]
        )
        await service.start_subscriptions()

        mock_fetcher = AsyncMock(return_value={"score": 85})
        mock_dependencies["registry"].get.return_value = mock_fetcher

        result = await service.get_esg("AAPL")

        assert result == {"score": 85}
        mock_fetcher.assert_called_with("AAPL")

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_run_sim_success(self, mock_dependencies):
        """Test running simulation"""
        from omnicore_engine.engines import PluginService

        service = PluginService(
            mock_dependencies["registry"],
            message_bus=mock_dependencies["bus"]
        )
        await service.start_subscriptions()

        mock_simulator = AsyncMock(return_value={"results": "simulation data"})
        mock_dependencies["registry"].get.return_value = mock_simulator

        result = await service.run_sim(["AAPL", "GOOGL"])

        assert result == {"results": "simulation data"}
        mock_simulator.assert_called_with(["AAPL", "GOOGL"])


class TestRunImportFixer:
    """Test the run_import_fixer helper function"""

    @pytest.mark.integration
    @patch("omnicore_engine.engines.asyncio.run")
    def test_run_import_fixer(self, mock_asyncio_run):
        """Test synchronous import fixer wrapper"""
        from omnicore_engine.engines import ENGINE_REGISTRY, run_import_fixer

        with patch.dict(ENGINE_REGISTRY, {}, clear=True):
            mock_fixer = Mock()
            mock_fixer.fix_file = AsyncMock(return_value="fixed code")
            ENGINE_REGISTRY["import_fixer"] = {"engine": mock_fixer}

            mock_asyncio_run.return_value = "fixed code"

            result = run_import_fixer("/test/file.py")

            assert result == "fixed code"
            mock_asyncio_run.assert_called_once()


class TestOmniCoreOmega:
    """Test the OmniCoreOmega orchestrator class"""

    @pytest.fixture
    def mock_components(self):
        """Create mock components for OmniCoreOmega"""
        return {
            "database": Mock(),
            "message_bus": Mock(),
            "plugin_service": Mock(),
            "crew_manager": Mock(),
            "intent_capture_api": Mock(),
            "test_generation_orchestrator": Mock(),
            "simulation_engine": Mock(),
            "audit_log_manager": Mock(),
            "import_fixer_engine": Mock(),
        }

    @pytest.mark.integration
    def test_initialization(self, mock_components):
        """Test OmniCoreOmega initialization"""
        from omnicore_engine.engines import OmniCoreOmega

        omega = OmniCoreOmega(**mock_components, num_arbiters=3)

        assert omega.db == mock_components["database"]
        assert omega.message_bus == mock_components["message_bus"]
        assert omega.plugin_service == mock_components["plugin_service"]
        assert omega.crew_manager == mock_components["crew_manager"]
        assert omega.num == 3
        assert not omega._is_initialized
        assert omega.arbiters == []

    @pytest.mark.skip(reason="Factory method causes mmap errors in CI with xdist parallelization - covered by other tests")
    @pytest.mark.integration
    @patch("omnicore_engine.engines.Database")
    @patch("omnicore_engine.engines.ShardedMessageBus")
    @patch("omnicore_engine.engines.PluginService")
    @patch("omnicore_engine.engines.UnifiedSimulationModule")
    @patch("omnicore_engine.engines.CrewManager")
    @patch("omnicore_engine.engines.TestGenerationOrchestrator")
    @patch("omnicore_engine.engines.create_import_fixer_engine")
    @patch("omnicore_engine.engines.OmniCoreOmega._find_crew_config")
    @patch("omnicore_engine.engines.yaml.safe_load")
    @patch("builtins.open", new_callable=mock_open, read_data="agents: []")
    def test_create_and_initialize(
        self,
        mock_file,
        mock_yaml_load,
        mock_find_config,
        mock_fixer,
        mock_test_gen,
        mock_crew,
        mock_sim,
        mock_plugin_service,
        mock_bus,
        mock_db,
    ):
        """Test factory method create_and_initialize"""
        from omnicore_engine.engines import OmniCoreOmega

        # Mock _find_crew_config to return a valid path
        mock_find_config.return_value = "/mock/crew_config.yaml"
        # Mock yaml.safe_load to return an empty agents list
        mock_yaml_load.return_value = {"agents": []}
        mock_fixer.return_value = Mock()

        omega = OmniCoreOmega.create_and_initialize()

        assert isinstance(omega, OmniCoreOmega)
        mock_db.assert_called_once()
        mock_bus.assert_called_once()
        mock_sim.assert_called_once()
        mock_crew.assert_called_once()

    @pytest.mark.integration
    @patch("omnicore_engine.engines.OmniCoreOmega._find_crew_config")
    @patch("omnicore_engine.engines.Database")
    @patch("omnicore_engine.engines.ShardedMessageBus")
    @patch("omnicore_engine.engines.PluginService")
    @patch("omnicore_engine.engines.UnifiedSimulationModule")
    @patch("omnicore_engine.engines.CrewManager")
    @patch("omnicore_engine.engines.TestGenerationOrchestrator")
    @patch("omnicore_engine.engines.create_import_fixer_engine")
    def test_create_and_initialize_no_config(
        self,
        mock_fixer,
        mock_test_gen,
        mock_crew,
        mock_sim,
        mock_plugin_service,
        mock_bus,
        mock_db,
        mock_find_config,
    ):
        """Test create_and_initialize when crew_config.yaml not found"""
        from omnicore_engine.engines import OmniCoreOmega

        # Mock _find_crew_config to return None (file not found)
        mock_find_config.return_value = None
        mock_fixer.return_value = Mock()

        omega = OmniCoreOmega.create_and_initialize()

        assert isinstance(omega, OmniCoreOmega)
        # Should continue without loading agents

    @pytest.mark.integration
    @patch("omnicore_engine.engines.Arbiter")
    @patch("omnicore_engine.engines.CodeHealthEnv")
    @patch.dict("os.environ", clear=True)
    def test_initialize_arbiters(self, mock_code_health_env, mock_arbiter, mock_components):
        """Test _initialize_arbiters method"""
        from omnicore_engine.engines import OmniCoreOmega

        mock_db = Mock()
        mock_db.engine = Mock()
        mock_bus = Mock()
        mock_plugin = Mock()
        mock_crew = Mock()
        mock_intent = Mock()
        mock_test_gen = Mock()
        mock_sim = Mock()
        mock_audit = Mock()
        mock_fixer = Mock()

        omega = OmniCoreOmega(
            database=mock_db,
            message_bus=mock_bus,
            plugin_service=mock_plugin,
            crew_manager=mock_crew,
            intent_capture_api=mock_intent,
            test_generation_orchestrator=mock_test_gen,
            simulation_engine=mock_sim,
            audit_log_manager=mock_audit,
            import_fixer_engine=mock_fixer,
            num_arbiters=3,
        )

        # Use the mock_arbiter and mock_code_health_env from @patch decorators
        # Decorator patches are applied at method level and provide mocks as parameters
        omega._initialize_arbiters()

        assert len(omega.arbiters) == 3
        assert mock_arbiter.call_count == 3
        assert mock_code_health_env.call_count == 1

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_initialize_asset_data(self, mock_components):
        """Test asset data initialization"""
        from omnicore_engine.engines import ENGINE_REGISTRY, OmniCoreOmega

        omega = OmniCoreOmega(**mock_components, num_arbiters=1)

        mock_components["import_fixer_engine"].initialize = AsyncMock()
        mock_components["database"].initialize = AsyncMock()
        mock_components["message_bus"].initialize = AsyncMock()
        mock_components["simulation_engine"].initialize = AsyncMock()
        mock_components["crew_manager"].start_all = AsyncMock()
        mock_components["plugin_service"].start_subscriptions = AsyncMock()

        with patch.object(omega, "_initialize_arbiters") as mock_init_arbiters:
            await omega.initialize_asset_data()

            assert omega._is_initialized
            mock_components["import_fixer_engine"].initialize.assert_called_once()
            mock_init_arbiters.assert_called_once()

            # Check engine was registered
            assert "import_fixer" in ENGINE_REGISTRY

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_initialize_asset_data_component_error(self, mock_components):
        """Test asset initialization with component error"""
        from omnicore_engine.engines import OmniCoreOmega

        omega = OmniCoreOmega(**mock_components, num_arbiters=1)

        mock_components["import_fixer_engine"].initialize = AsyncMock()
        mock_components["database"].initialize = AsyncMock(
            side_effect=Exception("DB Error")
        )
        mock_components["crew_manager"].start_all = AsyncMock()
        mock_components["plugin_service"].start_subscriptions = AsyncMock()

        with patch.object(omega, "_initialize_arbiters"):
            await omega.initialize_asset_data()

            # Should complete despite error
            assert omega._is_initialized

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_companies(self, mock_components):
        """Test get_companies delegation"""
        from omnicore_engine.engines import OmniCoreOmega

        omega = OmniCoreOmega(**mock_components)

        mock_components["plugin_service"].get_companies = AsyncMock(
            return_value=["Company1", "Company2"]
        )

        result = await omega.get_companies()

        assert result == ["Company1", "Company2"]
        mock_components["plugin_service"].get_companies.assert_called_once()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_esg(self, mock_components):
        """Test get_esg delegation"""
        from omnicore_engine.engines import OmniCoreOmega

        omega = OmniCoreOmega(**mock_components)

        mock_components["plugin_service"].get_esg = AsyncMock(
            return_value={"score": 85}
        )

        result = await omega.get_esg("AAPL")

        assert result == {"score": 85}
        mock_components["plugin_service"].get_esg.assert_called_once_with("AAPL")

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_run_sim(self, mock_components):
        """Test run_sim delegation"""
        from omnicore_engine.engines import OmniCoreOmega

        omega = OmniCoreOmega(**mock_components)

        mock_components["plugin_service"].run_sim = AsyncMock(
            return_value={"simulation": "results"}
        )

        result = await omega.run_sim(["AAPL", "GOOGL"])

        assert result == {"simulation": "results"}
        mock_components["plugin_service"].run_sim.assert_called_once_with(
            ["AAPL", "GOOGL"]
        )


class TestCrewConfigLoading:
    """Test crew configuration loading"""

    @pytest.mark.integration
    @patch("builtins.open", new_callable=mock_open)
    @patch("omnicore_engine.engines.yaml.safe_load")
    @patch("omnicore_engine.engines.Database")
    @patch("omnicore_engine.engines.ShardedMessageBus")
    @patch("omnicore_engine.engines.PluginService")
    @patch("omnicore_engine.engines.UnifiedSimulationModule")
    @patch("omnicore_engine.engines.CrewManager")
    @patch("omnicore_engine.engines.TestGenerationOrchestrator")
    @patch("omnicore_engine.engines.create_import_fixer_engine")
    @patch("omnicore_engine.engines.OmniCoreOmega._find_crew_config")
    def test_load_crew_config_with_agents(
        self,
        mock_find_config,
        mock_fixer,
        mock_test_gen,
        mock_crew_class,
        mock_sim,
        mock_plugin_service,
        mock_bus,
        mock_db,
        mock_yaml,
        mock_file,
    ):
        """Test loading crew config with agents"""
        from omnicore_engine.engines import OmniCoreOmega

        # Make _find_crew_config return a valid path
        mock_find_config.return_value = "/path/to/crew_config.yaml"
        
        mock_fixer.return_value = Mock()
        mock_crew_instance = Mock()
        mock_crew_instance.add_agent = Mock()
        mock_crew_class.return_value = mock_crew_instance

        mock_yaml.return_value = {
            "agents": [
                {
                    "name": "agent1",
                    "class": "TestAgent",
                    "config": {"key": "value"},
                    "tags": ["tag1"],
                    "metadata": {"meta": "data"},
                }
            ]
        }

        omega = OmniCoreOmega.create_and_initialize()

        mock_crew_instance.add_agent.assert_called_once_with(
            name="agent1",
            agent_class="TestAgent",
            config={"key": "value"},
            tags=["tag1"],
            metadata={"meta": "data"},
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--asyncio-mode=auto"])
