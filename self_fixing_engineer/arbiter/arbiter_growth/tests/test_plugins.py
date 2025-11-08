import asyncio
import logging
from unittest.mock import AsyncMock
import pytest

# Assuming all modules are in a discoverable path
from arbiter.arbiter_growth.plugins import PluginHook
from arbiter.arbiter_growth.models import GrowthEvent, ArbiterState
from arbiter.arbiter_growth.exceptions import ArbiterGrowthError

# Fixture for capturing logs
@pytest.fixture
def caplog(caplog):
    """A fixture to capture log output during tests."""
    caplog.set_level(logging.INFO)
    yield caplog

# Fixture for a sample GrowthEvent
@pytest.fixture
def growth_event():
    """Provides a consistent GrowthEvent instance for tests."""
    return GrowthEvent(
        type="skill_improved",
        timestamp="2025-08-06T10:03:00+00:00",
        details={"skill_name": "data_analysis", "improvement": 0.1},
        event_version=1.0
    )

# Fixture for a sample ArbiterState
@pytest.fixture
def arbiter_state():
    """Provides a consistent ArbiterState instance for tests."""
    return ArbiterState(
        arbiter_id="test_arbiter",
        level=2,
        skills={"data_analysis": 0.5},
        user_preferences={"theme": "dark"},
        event_offset=10
    )

# --- Custom Plugin Implementations for Testing ---

class TestLoggingPlugin(PluginHook):
    """A concrete plugin implementation that logs lifecycle events."""
    __test__ = False # Prevent pytest from collecting this as a test class
    async def on_start(self, arbiter_name: str) -> None:
        logging.getLogger("TestLoggingPlugin").info(f"Started for {arbiter_name}")

    async def on_stop(self, arbiter_name: str) -> None:
        logging.getLogger("TestLoggingPlugin").info(f"Stopped for {arbiter_name}")

    async def on_error(self, arbiter_name: str, error: ArbiterGrowthError) -> None:
        logging.getLogger("TestLoggingPlugin").error(f"Error in {arbiter_name}: {error}")

    async def on_growth_event(self, event: GrowthEvent, state: ArbiterState) -> None:
        logging.getLogger("TestLoggingPlugin").info(f"Event {event.type} for {state.arbiter_id}")

class TestAsyncMockPlugin(PluginHook):
    """A plugin where all methods are mocked for testing interactions."""
    __test__ = False  # Prevent pytest from collecting this as a test class
    def __init__(self):
        self.on_start = AsyncMock()
        self.on_stop = AsyncMock()
        self.on_error = AsyncMock()
        self.on_growth_event = AsyncMock()

    async def on_growth_event(self, event: GrowthEvent, state: ArbiterState) -> None:
        """Concrete implementation to satisfy the abstract base class."""
        # This implementation is just to satisfy the ABC. The instance method
        # is replaced by an AsyncMock in __init__, which is what gets called.
        pass


# --- Unit Tests for PluginHook Interface ---

def test_plugin_hook_is_abstract():
    """Tests that the PluginHook abstract base class cannot be instantiated directly."""
    with pytest.raises(TypeError, match="Can't instantiate abstract class"):
        PluginHook()

def test_plugin_hook_must_implement_on_growth_event():
    """Tests that a subclass must implement the abstract on_growth_event method."""
    class InvalidPlugin(PluginHook):
        __test__ = False # Prevent pytest from collecting this as a test class
        # Missing on_growth_event implementation
        async def on_start(self, arbiter_name: str) -> None: pass
        async def on_stop(self, arbiter_name: str) -> None: pass
        async def on_error(self, arbiter_name: str, error: ArbiterGrowthError) -> None: pass


    with pytest.raises(TypeError, match="Can't instantiate abstract class InvalidPlugin with abstract method on_growth_event"):
        InvalidPlugin()

# --- Tests for TestLoggingPlugin ---

@pytest.mark.asyncio
async def test_logging_plugin_on_start(caplog):
    """Tests that the on_start method logs the correct message."""
    plugin = TestLoggingPlugin()
    await plugin.on_start("test_arbiter")
    assert "Started for test_arbiter" in caplog.text

@pytest.mark.asyncio
async def test_logging_plugin_on_stop(caplog):
    """Tests that the on_stop method logs the correct message."""
    plugin = TestLoggingPlugin()
    await plugin.on_stop("test_arbiter")
    assert "Stopped for test_arbiter" in caplog.text

@pytest.mark.asyncio
async def test_logging_plugin_on_error(caplog):
    """Tests that the on_error method logs the correct message."""
    plugin = TestLoggingPlugin()
    error = ArbiterGrowthError("Test error", {"context": "failure"})
    await plugin.on_error("test_arbiter", error)
    assert "Error in test_arbiter: Test error (Details: {'context': 'failure'})" in caplog.text

@pytest.mark.asyncio
async def test_logging_plugin_on_growth_event(growth_event, arbiter_state, caplog):
    """Tests that the on_growth_event method logs the correct message."""
    plugin = TestLoggingPlugin()
    await plugin.on_growth_event(growth_event, arbiter_state)
    assert "Event skill_improved for test_arbiter" in caplog.text

# --- Tests with Mocked Plugin ---

@pytest.mark.asyncio
async def test_mock_plugin_on_start():
    """Tests that the on_start method is called correctly on the mocked plugin."""
    plugin = TestAsyncMockPlugin()
    await plugin.on_start("test_arbiter")
    plugin.on_start.assert_awaited_with("test_arbiter")

@pytest.mark.asyncio
async def test_mock_plugin_on_growth_event(growth_event, arbiter_state):
    """Tests that the on_growth_event method is called correctly on the mocked plugin."""
    plugin = TestAsyncMockPlugin()
    await plugin.on_growth_event(growth_event, arbiter_state)
    plugin.on_growth_event.assert_awaited_with(growth_event, arbiter_state)

# --- Integration-like Tests ---

@pytest.mark.asyncio
async def test_multiple_plugins_execution(growth_event, arbiter_state):
    """Tests that multiple registered plugins are all called."""
    plugin1 = TestAsyncMockPlugin()
    plugin2 = TestAsyncMockPlugin()
    plugins = [plugin1, plugin2]
    
    # This loop simulates how a manager would iterate through and call its hooks
    async def simulate_manager_call():
        tasks = [plugin.on_growth_event(growth_event, arbiter_state) for plugin in plugins]
        await asyncio.gather(*tasks)

    await simulate_manager_call()
    plugin1.on_growth_event.assert_awaited_with(growth_event, arbiter_state)
    plugin2.on_growth_event.assert_awaited_with(growth_event, arbiter_state)

# --- Edge Cases ---

@pytest.mark.asyncio
async def test_plugin_error_handling():
    """Tests that an error raised within a plugin is propagated correctly."""
    class FailingPlugin(PluginHook):
        __test__ = False # Prevent pytest from collecting this as a test class
        async def on_start(self, arbiter_name: str) -> None: pass
        async def on_stop(self, arbiter_name: str) -> None: pass
        async def on_error(self, arbiter_name: str, error: ArbiterGrowthError) -> None: pass
        async def on_growth_event(self, event: GrowthEvent, state: ArbiterState) -> None:
            raise RuntimeError("This plugin has failed intentionally.")

    plugin = FailingPlugin()
    with pytest.raises(RuntimeError, match="This plugin has failed intentionally."):
        await plugin.on_growth_event(
            GrowthEvent(type="test", timestamp="2025-08-06T10:03:00+00:00", details={}),
            ArbiterState(arbiter_id="test")
        )

# --- Concurrency Test ---

@pytest.mark.asyncio
async def test_concurrent_plugin_execution(growth_event, arbiter_state):
    """Tests that a single plugin instance can handle concurrent calls."""
    plugin = TestAsyncMockPlugin()
    
    async def call_plugin():
        await plugin.on_growth_event(growth_event, arbiter_state)
    
    tasks = [call_plugin() for _ in range(50)]
    await asyncio.gather(*tasks)
    
    assert plugin.on_growth_event.call_count == 50

# --- Example Plugin Implementation Test ---

@pytest.mark.asyncio
async def test_example_plugin_from_docstring(caplog, growth_event, arbiter_state):
    """Tests the example plugin implementation from the docstring."""
    # Define logger and plugin class inside the test function scope
    logger = logging.getLogger("ExamplePlugin")
    
    class LoggingAndMetricsPlugin(PluginHook):
        __test__ = False # Prevent pytest from collecting this as a test class
        async def on_start(self, arbiter_name: str) -> None: pass
        async def on_stop(self, arbiter_name: str) -> None: pass
        async def on_error(self, arbiter_name: str, error: ArbiterGrowthError) -> None: pass
        async def on_growth_event(self, event: GrowthEvent, state: ArbiterState) -> None:
            logger.info(f"Arbiter '{state.arbiter_id}' processed event: {event.type}")
            if event.type == "skill_improved" and event.details.get("skill_name") == "data_analysis":
                logger.info(f"METRIC: data_analysis skill improved for {state.arbiter_id}!")

    plugin = LoggingAndMetricsPlugin()
    await plugin.on_growth_event(growth_event, arbiter_state)
    
    assert "Arbiter 'test_arbiter' processed event: skill_improved" in caplog.text
    assert "METRIC: data_analysis skill improved for test_arbiter!" in caplog.text

# --- Reconstructed and New Tests ---

@pytest.mark.asyncio
async def test_multiple_hooks_execution(growth_event, arbiter_state, caplog):
    """Tests that multiple registered logging plugins are all called and log correctly."""
    plugins = [TestLoggingPlugin() for _ in range(2)]
    
    async def simulate_manager_call():
        tasks = [plugin.on_growth_event(growth_event, arbiter_state) for plugin in plugins]
        await asyncio.gather(*tasks)
    
    with caplog.at_level(logging.INFO):
        await simulate_manager_call()
        assert caplog.text.count("Event skill_improved for test_arbiter") == 2

@pytest.mark.asyncio
async def test_plugin_error_propagation():
    """Tests that an error raised from a plugin is propagated without being suppressed."""
    class ErrorPlugin(PluginHook):
        __test__ = False # Prevent pytest from collecting this as a test class
        async def on_start(self, arbiter_name: str) -> None: pass
        async def on_stop(self, arbiter_name: str) -> None: pass
        async def on_error(self, arbiter_name: str, error: ArbiterGrowthError) -> None: pass
        async def on_growth_event(self, event: GrowthEvent, state: ArbiterState) -> None:
            raise ArbiterGrowthError("Plugin error")

    plugin = ErrorPlugin()
    with pytest.raises(ArbiterGrowthError, match="Plugin error"):
        await plugin.on_growth_event(
            GrowthEvent(type="test", timestamp="2025-01-01T00:00:00+00:00", details={}),
            ArbiterState(arbiter_id="test")
        )