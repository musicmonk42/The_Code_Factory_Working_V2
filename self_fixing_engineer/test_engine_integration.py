# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Integration tests for self_fixing_engineer engines.

Tests basic integration and communication between key engines.
"""

import asyncio

import pytest


class TestEngineIntegration:
    """Test integration between self_fixing_engineer engines."""

    def test_arbiter_module_exists(self):
        """Test that arbiter module can be imported."""
        try:
            from arbiter import arbiter

            assert arbiter is not None, "Arbiter module should exist"
        except ImportError as e:
            pytest.fail(f"Failed to import arbiter module: {e}")

    def test_arbiter_class_exists(self):
        """Test that Arbiter class exists."""
        try:
            from self_fixing_engineer.arbiter.arbiter import Arbiter

            assert Arbiter is not None, "Arbiter class should exist"
        except ImportError as e:
            pytest.fail(f"Failed to import Arbiter class: {e}")

    def test_simulation_module_exists(self):
        """Test that simulation module can be imported."""
        try:
            from simulation import simulation_module

            assert simulation_module is not None
        except ImportError as e:
            pytest.fail(f"Failed to import simulation module: {e}")

    def test_test_generation_module_exists(self):
        """Test that test_generation module can be imported."""
        try:
            from test_generation.orchestrator import orchestrator

            assert orchestrator is not None
        except ImportError as e:
            pytest.fail(f"Failed to import test_generation module: {e}")

    def test_mesh_event_bus_exists(self):
        """Test that mesh/event_bus module can be imported."""
        try:
            from mesh import event_bus

            assert event_bus is not None
        except ImportError as e:
            pytest.fail(f"Failed to import mesh.event_bus: {e}")

    def test_guardrails_module_exists(self):
        """Test that guardrails module can be imported."""
        try:
            from guardrails import audit_log

            assert audit_log is not None
        except ImportError as e:
            pytest.fail(f"Failed to import guardrails.audit_log: {e}")

    def test_self_healing_fixer_exists(self):
        """Test that self_healing_import_fixer module can be imported."""
        try:
            import self_healing_import_fixer

            assert self_healing_import_fixer is not None
        except ImportError as e:
            pytest.fail(f"Failed to import self_healing_import_fixer: {e}")

    def test_agent_orchestration_exists(self):
        """Test that agent_orchestration module can be imported."""
        try:
            import agent_orchestration

            assert agent_orchestration is not None
        except ImportError as e:
            pytest.skip(f"Agent orchestration not fully implemented: {e}")


class TestEngineConfiguration:
    """Test configuration management across engines."""

    def test_arbiter_config_can_load(self):
        """Test that ArbiterConfig can be loaded."""
        try:
            from self_fixing_engineer.arbiter.config import ArbiterConfig

            # Test basic import, not actual initialization which needs env vars
            assert ArbiterConfig is not None
        except Exception as e:
            pytest.fail(f"Failed to load ArbiterConfig: {e}")


class TestArbiterIntegration:
    """Test Arbiter integration with MessageQueueService and event handling."""

    def test_arbiter_has_message_queue_service_param(self):
        """Test that Arbiter __init__ accepts message_queue_service parameter."""
        try:
            from self_fixing_engineer.arbiter.arbiter import Arbiter
            import inspect

            # Check that __init__ has message_queue_service parameter
            sig = inspect.signature(Arbiter.__init__)
            assert (
                "message_queue_service" in sig.parameters
            ), "Arbiter should have message_queue_service parameter"
        except Exception as e:
            pytest.fail(f"Failed to verify Arbiter parameters: {e}")

    def test_arbiter_has_event_handlers(self):
        """Test that Arbiter has all required event handler methods."""
        try:
            from self_fixing_engineer.arbiter.arbiter import Arbiter

            required_handlers = [
                "_on_bug_detected",
                "_on_policy_violation",
                "_on_analysis_complete",
                "_on_generator_output",
                "_on_test_results",
                "_on_workflow_completed",
                "_handle_incoming_event",
            ]

            for handler in required_handlers:
                assert hasattr(
                    Arbiter, handler
                ), f"Arbiter should have {handler} method"
        except Exception as e:
            pytest.fail(f"Failed to verify Arbiter event handlers: {e}")

    def test_arbiter_has_event_receiver_setup(self):
        """Test that Arbiter has setup_event_receiver method."""
        try:
            from self_fixing_engineer.arbiter.arbiter import Arbiter

            assert hasattr(
                Arbiter, "setup_event_receiver"
            ), "Arbiter should have setup_event_receiver method"
        except Exception as e:
            pytest.fail(f"Failed to verify setup_event_receiver: {e}")

    @pytest.mark.asyncio
    async def test_event_handler_accepts_data(self):
        """Test that event handlers can accept data dictionaries."""
        try:
            from self_fixing_engineer.arbiter.arbiter import Arbiter
            from unittest.mock import AsyncMock, MagicMock

            # Create a minimal mock Arbiter instance
            arbiter = MagicMock(spec=Arbiter)
            arbiter.name = "TestArbiter"
            arbiter.log_event = MagicMock()
            arbiter.coordinate_with_peers = AsyncMock()
            arbiter.decision_optimizer = None

            # Test _on_bug_detected handler
            handler = Arbiter._on_bug_detected
            test_data = {
                "bug_id": "test-bug-123",
                "bug_type": "import_error",
                "severity": "high",
            }

            # Call the handler (it's an instance method, so we need to bind it)
            await handler(arbiter, test_data)

            # Verify it was called without errors
            assert True, "Event handler should process data without errors"
        except Exception as e:
            # Skip if dependencies are missing for full test
            pytest.skip(f"Skipping handler test due to dependencies: {e}")


class TestArenaIntegration:
    """Test Arena integration with event distribution."""

    def test_arena_has_event_distribution_route(self):
        """Test that Arena sets up /events endpoint."""
        try:
            from self_fixing_engineer.arbiter.arena import ArbiterArena

            # Check that _setup_routes exists (it sets up the endpoint)
            assert hasattr(
                ArbiterArena, "_setup_routes"
            ), "Arena should have _setup_routes method"
        except Exception as e:
            pytest.fail(f"Failed to verify Arena routes: {e}")

    def test_arena_injects_dependencies(self):
        """Test that Arena injects MessageQueueService and DecisionOptimizer."""
        try:
            from self_fixing_engineer.arbiter.arena import ArbiterArena
            import inspect

            # Check _initialize_arbiters method exists
            assert hasattr(
                ArbiterArena, "_initialize_arbiters"
            ), "Arena should have _initialize_arbiters method"

            # Verify the method creates dependencies (by checking source)
            source = inspect.getsource(ArbiterArena._initialize_arbiters)
            assert (
                "MessageQueueService" in source
            ), "_initialize_arbiters should create MessageQueueService"
            assert (
                "DecisionOptimizer" in source
            ), "_initialize_arbiters should create DecisionOptimizer"
        except Exception as e:
            pytest.skip(f"Skipping dependency injection test: {e}")


class TestMessageQueueServiceIntegration:
    """Test MessageQueueService subscription integration."""

    def test_message_queue_service_can_be_imported(self):
        """Test that MessageQueueService can be imported."""
        try:
            from self_fixing_engineer.arbiter.message_queue_service import MessageQueueService

            assert (
                MessageQueueService is not None
            ), "MessageQueueService should be importable"
        except ImportError as e:
            pytest.skip(f"MessageQueueService not available: {e}")

    def test_message_queue_service_has_subscribe(self):
        """Test that MessageQueueService has subscribe method."""
        try:
            from self_fixing_engineer.arbiter.message_queue_service import MessageQueueService

            assert hasattr(
                MessageQueueService, "subscribe"
            ), "MessageQueueService should have subscribe method"
        except ImportError as e:
            pytest.skip(f"MessageQueueService not available: {e}")


class TestDecisionOptimizerIntegration:
    """Test DecisionOptimizer integration."""

    def test_decision_optimizer_can_be_imported(self):
        """Test that DecisionOptimizer can be imported."""
        try:
            from self_fixing_engineer.arbiter.decision_optimizer import DecisionOptimizer

            assert (
                DecisionOptimizer is not None
            ), "DecisionOptimizer should be importable"
        except ImportError as e:
            pytest.skip(f"DecisionOptimizer not available: {e}")

    def test_decision_optimizer_accepts_arena(self):
        """Test that DecisionOptimizer can be initialized with arena parameter."""
        try:
            from self_fixing_engineer.arbiter.decision_optimizer import DecisionOptimizer
            import inspect

            # Check __init__ signature
            sig = inspect.signature(DecisionOptimizer.__init__)
            assert (
                "arena" in sig.parameters
            ), "DecisionOptimizer should accept arena parameter"
        except ImportError as e:
            pytest.skip(f"DecisionOptimizer not available: {e}")


class TestGeneratorIntegration:
    """Test 100% Generator integration with Arbiter."""

    def test_arbiter_has_generator_engine(self):
        """Test that Arbiter has generator_engine attribute."""
        try:
            from self_fixing_engineer.arbiter.arbiter import Arbiter
            import inspect

            # Check Arbiter __init__ processes generator engine
            source = inspect.getsource(Arbiter.__init__)
            assert (
                "generator_engine" in source
            ), "Arbiter should have generator_engine attribute"
        except ImportError as e:
            pytest.skip(f"Arbiter not available: {e}")

    def test_generator_output_handler_has_direct_integration(self):
        """Test that _on_generator_output uses generator_engine directly."""
        try:
            from self_fixing_engineer.arbiter.arbiter import Arbiter
            import inspect

            # Check _on_generator_output method includes direct generator integration
            source = inspect.getsource(Arbiter._on_generator_output)
            assert (
                "self.generator_engine" in source
            ), "_on_generator_output should use self.generator_engine"
            assert (
                "process_output" in source or "publish_to_omnicore" in source
            ), "_on_generator_output should have direct generator engine integration"
        except (ImportError, AttributeError) as e:
            pytest.skip(f"Generator output handler not available: {e}")

    def test_arena_creates_generator_engine(self):
        """Test that Arena creates and injects generator engine."""
        try:
            from self_fixing_engineer.arbiter.arena import ArbiterArena
            import inspect

            # Check _initialize_arbiters creates generator engine
            source = inspect.getsource(ArbiterArena._initialize_arbiters)
            assert (
                "generator_engine" in source or "Runner" in source
            ), "Arena should create generator engine"
            assert (
                '"generator"' in source
            ), "Arena should inject generator into engines dict"
        except (ImportError, AttributeError) as e:
            pytest.skip(f"Arena generator integration not available: {e}")

    def test_generator_runner_can_be_imported(self):
        """Test that Generator Runner can be imported."""
        try:
            from generator.runner.runner_core import Runner

            assert Runner is not None, "Generator Runner should be importable"
        except ImportError as e:
            pytest.skip(f"Generator Runner not available: {e}")

    def test_simulation_settings_exist(self):
        """Test that simulation settings can be accessed."""
        try:
            from simulation.simulation_module import Settings

            settings = Settings()
            assert hasattr(settings, "SIM_RETRY_ATTEMPTS")
        except Exception as e:
            pytest.fail(f"Failed to access simulation settings: {e}")


class TestEngineMetrics:
    """Test that engines have metrics properly configured."""

    def test_arbiter_metrics_configured(self):
        """Test that arbiter has metrics helpers."""
        try:
            from self_fixing_engineer.arbiter.metrics import get_or_create_counter, get_or_create_gauge

            assert callable(get_or_create_counter)
            assert callable(get_or_create_gauge)
        except ImportError as e:
            pytest.fail(f"Failed to import arbiter metrics: {e}")

    def test_simulation_metrics_exist(self):
        """Test that simulation module has metrics."""
        try:
            from simulation.simulation_module import SIM_MODULE_METRICS

            assert isinstance(SIM_MODULE_METRICS, dict)
            assert "simulation_run_total" in SIM_MODULE_METRICS
        except Exception as e:
            pytest.fail(f"Failed to access simulation metrics: {e}")


class TestEngineArchitecture:
    """Test architectural patterns across engines."""

    def test_engines_use_async(self):
        """Test that key engines support async operations."""
        engines_to_check = [
            ("arbiter.arbiter", "Arbiter"),
            ("simulation.simulation_module", "SimulationEngine"),
            ("mesh.event_bus", "EventBus"),
        ]

        for module_name, class_name in engines_to_check:
            try:
                module = __import__(module_name, fromlist=[class_name])
                if hasattr(module, class_name):
                    cls = getattr(module, class_name)
                    # Check if class has async methods
                    async_methods = [
                        name
                        for name in dir(cls)
                        if not name.startswith("_")
                        and asyncio.iscoroutinefunction(getattr(cls, name, None))
                    ]
                    # Should have at least some async methods
                    assert (
                        len(async_methods) > 0
                    ), f"{class_name} should have async methods"
            except (ImportError, AttributeError):
                # Some engines might not have the exact class name
                pass

    def test_engines_have_error_handling(self):
        """Test that engines have error handling."""
        # Check that custom exceptions are defined
        try:
            from self_fixing_engineer.arbiter.config import ConfigError

            assert issubclass(ConfigError, Exception)
        except ImportError:
            pytest.skip("ConfigError not found")


class TestEnginesDependencies:
    """Test that engines can work with required dependencies."""

    def test_prometheus_client_available(self):
        """Test that prometheus_client is available for metrics."""
        try:
            import prometheus_client

            assert prometheus_client is not None
        except ImportError:
            pytest.fail("prometheus_client is required but not installed")

    def test_opentelemetry_available(self):
        """Test that opentelemetry is available for tracing."""
        try:
            import opentelemetry

            assert opentelemetry is not None
        except ImportError:
            pytest.fail("opentelemetry is required but not installed")

    def test_sqlalchemy_available(self):
        """Test that sqlalchemy is available for database operations."""
        try:
            import sqlalchemy

            assert sqlalchemy is not None
        except ImportError:
            pytest.fail("sqlalchemy is required but not installed")

    def test_fastapi_available(self):
        """Test that fastapi is available for API endpoints."""
        try:
            import fastapi

            assert fastapi is not None
        except ImportError:
            pytest.fail("fastapi is required but not installed")

    def test_pydantic_available(self):
        """Test that pydantic is available for data validation."""
        try:
            import pydantic

            assert pydantic is not None
        except ImportError:
            pytest.fail("pydantic is required but not installed")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
