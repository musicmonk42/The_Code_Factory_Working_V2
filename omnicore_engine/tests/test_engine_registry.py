"""
Test suite for omnicore_engine/engines.py
Tests engine registry, plugin service, and OmniCoreOmega orchestrator.
"""

import pytest
import os
from unittest.mock import Mock, patch, AsyncMock, mock_open

# Add the parent directory to path for imports
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from omnicore_engine.engines import (
    ENGINE_REGISTRY,
    register_engine,
    get_engine,
    PluginService,
    run_import_fixer,
    OmniCoreOmega,
)


class TestEngineRegistry:
    """Test the engine registry functions"""

    def setup_method(self):
        """Clear registry before each test"""
        ENGINE_REGISTRY.clear()

    def test_register_engine_success(self):
        """Test successful engine registration"""
        entrypoints = {"initialize": Mock(), "shutdown": Mock(), "execute": Mock()}

        register_engine("test_engine", entrypoints)

        assert "test_engine" in ENGINE_REGISTRY
        assert ENGINE_REGISTRY["test_engine"] == entrypoints

    def test_register_engine_invalid_entrypoints(self):
        """Test registration with invalid entrypoints"""
        with pytest.raises(TypeError, match="Entrypoints must be a dictionary"):
            register_engine("bad_engine", "not_a_dict")

        with pytest.raises(TypeError, match="Entrypoints must be a dictionary"):
            register_engine("bad_engine", ["list", "not", "dict"])

    def test_get_engine_exists(self):
        """Test retrieving an existing engine"""
        entrypoints = {"func": Mock()}
        ENGINE_REGISTRY["existing_engine"] = entrypoints

        result = get_engine("existing_engine")
        assert result == entrypoints

    def test_get_engine_not_exists(self):
        """Test retrieving non-existent engine"""
        result = get_engine("nonexistent_engine")
        assert result is None


class TestPluginService:
    """Test the PluginService class"""

    @pytest.fixture
    def mock_dependencies(self):
        """Create mock dependencies"""
        with patch("omnicore_engine.engines.Database") as mock_db:
            with patch("omnicore_engine.engines.ShardedMessageBus") as mock_bus:
                with patch("omnicore_engine.engines.ArbiterConfig") as mock_config:
                    mock_config.return_value.database_path = "test.db"
                    mock_registry = Mock()
                    mock_bus_instance = Mock()
                    mock_bus_instance.subscribe = AsyncMock()
                    mock_bus_instance.publish = AsyncMock()
                    mock_bus.return_value = mock_bus_instance

                    yield {
                        "registry": mock_registry,
                        "bus": mock_bus_instance,
                        "db": mock_db,
                        "config": mock_config,
                    }

    def test_plugin_service_initialization(self, mock_dependencies):
        """Test PluginService initialization"""
        service = PluginService(mock_dependencies["registry"])

        assert service.plugin_registry == mock_dependencies["registry"]
        assert service.message_bus is not None

        # Verify subscriptions were created
        calls = mock_dependencies["bus"].subscribe.call_args_list
        assert len(calls) >= 2
        topics = [call[0][0] for call in calls]
        assert "arbiter:bug_detected" in topics
        assert "shif:fix_import_request" in topics

    @pytest.mark.asyncio
    async def test_handle_arbiter_bug(self, mock_dependencies):
        """Test handling arbiter bug events"""
        service = PluginService(mock_dependencies["registry"])

        with patch("omnicore_engine.engines.BugManager") as mock_bug_manager:
            mock_manager_instance = Mock()
            mock_manager_instance.report_bug = AsyncMock()
            mock_bug_manager.return_value = mock_manager_instance

            message = Mock()
            message.payload = {"bug": "test_bug", "severity": "high"}

            await service.handle_arbiter_bug(message)

            mock_manager_instance.report_bug.assert_called_once_with(message.payload)

    @pytest.mark.asyncio
    async def test_handle_shif_request_with_path(self, mock_dependencies):
        """Test handling SHIF request with file path"""
        service = PluginService(mock_dependencies["registry"])

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
    async def test_handle_shif_request_with_code(self, mock_dependencies):
        """Test handling SHIF request with code string"""
        service = PluginService(mock_dependencies["registry"])

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
    async def test_handle_shif_request_no_engine(self, mock_dependencies):
        """Test handling SHIF request when engine not registered"""
        service = PluginService(mock_dependencies["registry"])
        ENGINE_REGISTRY.clear()

        message = Mock()
        message.payload = {"path": "/test/file.py"}

        await service.handle_shif_request(message)

        # Should log error and return early
        service.message_bus.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_shif_request_error(self, mock_dependencies):
        """Test handling SHIF request when fixer raises error"""
        service = PluginService(mock_dependencies["registry"])

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
    async def test_get_companies_success(self, mock_dependencies):
        """Test getting companies list"""
        service = PluginService(mock_dependencies["registry"])

        mock_fetcher = AsyncMock(return_value=["Company1", "Company2"])
        mock_dependencies["registry"].get.return_value = mock_fetcher

        result = await service.get_companies()

        assert result == ["Company1", "Company2"]
        mock_dependencies["registry"].get.assert_called_with("company_list")

    @pytest.mark.asyncio
    async def test_get_companies_no_plugin(self, mock_dependencies):
        """Test getting companies when plugin not registered"""
        service = PluginService(mock_dependencies["registry"])
        mock_dependencies["registry"].get.return_value = None

        with pytest.raises(RuntimeError, match="No company_list plugin registered"):
            await service.get_companies()

    @pytest.mark.asyncio
    async def test_get_esg_success(self, mock_dependencies):
        """Test getting ESG report"""
        service = PluginService(mock_dependencies["registry"])

        mock_fetcher = AsyncMock(return_value={"score": 85})
        mock_dependencies["registry"].get.return_value = mock_fetcher

        result = await service.get_esg("AAPL")

        assert result == {"score": 85}
        mock_fetcher.assert_called_with("AAPL")

    @pytest.mark.asyncio
    async def test_run_sim_success(self, mock_dependencies):
        """Test running simulation"""
        service = PluginService(mock_dependencies["registry"])

        mock_simulator = AsyncMock(return_value={"results": "simulation data"})
        mock_dependencies["registry"].get.return_value = mock_simulator

        result = await service.run_sim(["AAPL", "GOOGL"])

        assert result == {"results": "simulation data"}
        mock_simulator.assert_called_with(["AAPL", "GOOGL"])


class TestRunImportFixer:
    """Test the run_import_fixer helper function"""

    @patch("omnicore_engine.engines.asyncio.get_event_loop")
    def test_run_import_fixer(self, mock_get_loop):
        """Test synchronous import fixer wrapper"""
        mock_fixer = Mock()
        mock_fixer.fix_file = AsyncMock(return_value="fixed code")
        ENGINE_REGISTRY["import_fixer"] = {"engine": mock_fixer}

        mock_loop = Mock()
        mock_loop.run_until_complete = Mock(return_value="fixed code")
        mock_get_loop.return_value = mock_loop

        result = run_import_fixer("/test/file.py")

        assert result == "fixed code"
        mock_loop.run_until_complete.assert_called_once()


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

    def test_initialization(self, mock_components):
        """Test OmniCoreOmega initialization"""
        omega = OmniCoreOmega(**mock_components, num_arbiters=3)

        assert omega.db == mock_components["database"]
        assert omega.message_bus == mock_components["message_bus"]
        assert omega.plugin_service == mock_components["plugin_service"]
        assert omega.crew_manager == mock_components["crew_manager"]
        assert omega.num == 3
        assert not omega._is_initialized
        assert omega.arbiters == []

    @patch("omnicore_engine.engines.Database")
    @patch("omnicore_engine.engines.ShardedMessageBus")
    @patch("omnicore_engine.engines.PluginService")
    @patch("omnicore_engine.engines.UnifiedSimulationModule")
    @patch("omnicore_engine.engines.CrewManager")
    @patch("omnicore_engine.engines.TestGenerationOrchestrator")
    @patch("omnicore_engine.engines.create_import_fixer_engine")
    @patch("builtins.open", new_callable=mock_open, read_data="agents: []")
    def test_create_and_initialize(
        self,
        mock_file,
        mock_fixer,
        mock_test_gen,
        mock_crew,
        mock_sim,
        mock_plugin_service,
        mock_bus,
        mock_db,
    ):
        """Test factory method create_and_initialize"""
        mock_fixer.return_value = Mock()

        omega = OmniCoreOmega.create_and_initialize()

        assert isinstance(omega, OmniCoreOmega)
        mock_db.assert_called_once()
        mock_bus.assert_called_once()
        mock_sim.assert_called_once()
        mock_crew.assert_called_once()

    @patch("builtins.open", side_effect=FileNotFoundError)
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
        mock_file,
    ):
        """Test create_and_initialize when crew_config.yaml not found"""
        mock_fixer.return_value = Mock()

        omega = OmniCoreOmega.create_and_initialize()

        assert isinstance(omega, OmniCoreOmega)
        # Should continue without loading agents

    def test_initialize_arbiters(self, mock_components):
        """Test arbiter initialization"""
        omega = OmniCoreOmega(**mock_components, num_arbiters=2)

        mock_components["database"].engine = Mock()
        mock_components["audit_log_manager"].log_audit = AsyncMock()

        with patch("omnicore_engine.engines.get_system_metrics_async") as mock_metrics:
            with patch("omnicore_engine.engines.CodeHealthEnv") as mock_env:
                with patch("omnicore_engine.engines.Arbiter") as mock_arbiter:
                    mock_metrics.return_value = {"pass_rate": 0.95}
                    mock_arbiter.return_value = Mock()

                    omega._initialize_arbiters()

                    assert len(omega.arbiters) == 2
                    assert mock_arbiter.call_count == 2

    @pytest.mark.asyncio
    async def test_initialize_asset_data(self, mock_components):
        """Test asset data initialization"""
        omega = OmniCoreOmega(**mock_components, num_arbiters=1)

        mock_components["import_fixer_engine"].initialize = AsyncMock()
        mock_components["database"].initialize = AsyncMock()
        mock_components["message_bus"].initialize = AsyncMock()
        mock_components["simulation_engine"].initialize = AsyncMock()
        mock_components["crew_manager"].start_all = AsyncMock()

        with patch.object(omega, "_initialize_arbiters") as mock_init_arbiters:
            await omega.initialize_asset_data()

            assert omega._is_initialized
            mock_components["import_fixer_engine"].initialize.assert_called_once()
            mock_init_arbiters.assert_called_once()

            # Check engine was registered
            assert "import_fixer" in ENGINE_REGISTRY

    @pytest.mark.asyncio
    async def test_initialize_asset_data_component_error(self, mock_components):
        """Test asset initialization with component error"""
        omega = OmniCoreOmega(**mock_components, num_arbiters=1)

        mock_components["import_fixer_engine"].initialize = AsyncMock()
        mock_components["database"].initialize = AsyncMock(side_effect=Exception("DB Error"))
        mock_components["crew_manager"].start_all = AsyncMock()

        with patch.object(omega, "_initialize_arbiters"):
            await omega.initialize_asset_data()

            # Should complete despite error
            assert omega._is_initialized

    @pytest.mark.asyncio
    async def test_get_companies(self, mock_components):
        """Test get_companies delegation"""
        omega = OmniCoreOmega(**mock_components)

        mock_components["plugin_service"].get_companies = AsyncMock(
            return_value=["Company1", "Company2"]
        )

        result = await omega.get_companies()

        assert result == ["Company1", "Company2"]
        mock_components["plugin_service"].get_companies.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_esg(self, mock_components):
        """Test get_esg delegation"""
        omega = OmniCoreOmega(**mock_components)

        mock_components["plugin_service"].get_esg = AsyncMock(return_value={"score": 85})

        result = await omega.get_esg("AAPL")

        assert result == {"score": 85}
        mock_components["plugin_service"].get_esg.assert_called_once_with("AAPL")

    @pytest.mark.asyncio
    async def test_run_sim(self, mock_components):
        """Test run_sim delegation"""
        omega = OmniCoreOmega(**mock_components)

        mock_components["plugin_service"].run_sim = AsyncMock(
            return_value={"simulation": "results"}
        )

        result = await omega.run_sim(["AAPL", "GOOGL"])

        assert result == {"simulation": "results"}
        mock_components["plugin_service"].run_sim.assert_called_once_with(["AAPL", "GOOGL"])


class TestCrewConfigLoading:
    """Test crew configuration loading"""

    @patch("builtins.open", new_callable=mock_open)
    @patch("omnicore_engine.engines.yaml.safe_load")
    @patch("omnicore_engine.engines.Database")
    @patch("omnicore_engine.engines.ShardedMessageBus")
    @patch("omnicore_engine.engines.PluginService")
    @patch("omnicore_engine.engines.UnifiedSimulationModule")
    @patch("omnicore_engine.engines.CrewManager")
    @patch("omnicore_engine.engines.TestGenerationOrchestrator")
    @patch("omnicore_engine.engines.create_import_fixer_engine")
    def test_load_crew_config_with_agents(
        self,
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
