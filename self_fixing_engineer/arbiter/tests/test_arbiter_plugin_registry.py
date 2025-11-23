import pytest
from unittest.mock import patch, MagicMock
import time
import os
import sys

# Add the parent directory to path if needed
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Add self_healing_import_fixer to sys.path
self_healing_path = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "self_healing_import_fixer")
)
sys.path.insert(0, self_healing_path)

from arbiter_plugin_registry import (
    PluginBase,
    PluginMeta,
    PluginRegistry,
    PlugInKind,
    PluginDependencyError,
    PLUGIN_REGISTRY,
    logger,
)


# Fixture to reset the PluginRegistry and plugins.json before each test
@pytest.fixture(autouse=True)
def reset_registry(tmp_path):
    """Reset the PluginRegistry and plugins.json before each test."""
    persist_path = tmp_path / "plugins.json"
    # Clear any existing plugins.json in the project root
    root_plugins_json = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "plugins.json")
    )
    if os.path.exists(root_plugins_json):
        try:
            os.remove(root_plugins_json)
        except Exception as e:
            print(f"Warning: Failed to remove {root_plugins_json}: {e}")
    registry = PluginRegistry(persist_path=str(persist_path))
    registry._plugins.clear()
    registry._meta.clear()
    if os.path.exists(persist_path):
        os.remove(persist_path)
    yield registry
    if os.path.exists(persist_path):
        os.remove(persist_path)


@pytest.fixture(autouse=True)
def mock_anthropic():
    """Mock the anthropic module for tests."""
    anthropic = MagicMock()
    sys.modules["anthropic"] = anthropic
    yield
    sys.modules.pop("anthropic", None)


@pytest.fixture(autouse=True)
def mock_app():
    """Mock the app module for audit_log.py."""
    app = MagicMock()
    app.config = MagicMock()
    app.config.arbiter_config = None
    sys.modules["app"] = app
    yield
    sys.modules.pop("app", None)


@pytest.fixture(autouse=True)
def mock_test_generation_onboard():
    """Mock the test_generation.onboard module for tests."""
    onboard = MagicMock()
    sys.modules["test_generation.onboard"] = onboard
    yield
    sys.modules.pop("test_generation.onboard", None)


@pytest.fixture(autouse=True)
def mock_stable_baselines3():
    """Mock the stable_baselines3 module for tests."""
    stable_baselines3 = MagicMock()
    stable_baselines3.common = MagicMock()
    sys.modules["stable_baselines3"] = stable_baselines3
    sys.modules["stable_baselines3.common"] = stable_baselines3.common
    yield
    sys.modules.pop("stable_baselines3", None)
    sys.modules.pop("stable_baselines3.common", None)


@pytest.fixture(autouse=True)
def mock_omnicore_plugin_registry():
    """Mock omnicore_engine.plugin_registry to provide plugin_event_handler."""
    omnicore_plugin_registry = MagicMock()
    omnicore_plugin_registry.PLUGIN_REGISTRY = {}
    omnicore_plugin_registry.plugin_event_handler = MagicMock()
    sys.modules["omnicore_engine.plugin_registry"] = omnicore_plugin_registry
    yield
    sys.modules.pop("omnicore_engine.plugin_registry", None)


@pytest.fixture(autouse=True)
def mock_test_generation_utils():
    """Mock the test_generation.utils module to provide __version__."""
    utils = MagicMock()
    utils.__version__ = "1.0.0"
    sys.modules["test_generation.utils"] = utils
    yield
    sys.modules.pop("test_generation.utils", None)


# Fixture to mock logger
@pytest.fixture
def mock_logger():
    with patch.object(logger, "info") as mock_info, patch.object(
        logger, "error"
    ) as mock_error, patch.object(logger, "warning") as mock_warning:
        yield mock_info, mock_error, mock_warning


# Fixture for mock PluginBase subclass
class MockPlugin(PluginBase):
    async def initialize(self):
        pass

    async def start(self):
        pass

    async def stop(self):
        pass

    async def health_check(self):
        return True

    async def get_capabilities(self):
        return ["test"]


@pytest.fixture
def mock_plugin_class():
    return MockPlugin


# Test PluginMeta dataclass
def test_plugin_meta():
    meta = PluginMeta(
        name="test",
        kind=PlugInKind.CORE_SERVICE,
        version="1.0.0",
        author="Test",
        description="Desc",
        dependencies=[{"kind": "core_service", "name": "dep1", "version": ">=1.0"}],
    )
    assert meta.version == "1.0.0"
    assert meta.author == "Test"
    assert meta.description == "Desc"
    assert len(meta.dependencies) == 1


# Test PluginBase abstract methods
def test_plugin_base_abstract():
    with pytest.raises(TypeError):
        PluginBase()


# Test PluginRegistry singleton
def test_plugin_registry_singleton():
    # Because of the autouse fixture, we need to bypass it for this specific test
    # by creating instances directly.
    PluginRegistry._instance = None
    reg1 = PluginRegistry()
    reg2 = PluginRegistry()
    assert reg1 is reg2
    # Clean up
    PluginRegistry._instance = None


# Test register decorator
@pytest.mark.asyncio
async def test_register_decorator(reset_registry):
    reg = reset_registry
    kind = PlugInKind.CORE_SERVICE
    name = "TestPlugin"
    version_str = "1.0.0"
    author = "TestAuthor"

    @reg.register(kind=kind, name=name, version=version_str, author=author)
    class TestPlugin(MockPlugin):
        pass

    plugin = reg.get(kind, name)
    assert plugin is not None
    assert plugin.__name__ == "TestPlugin"
    meta = reg.get_metadata(kind, name)
    assert meta.name == name
    assert meta.kind == kind
    assert meta.version == version_str
    assert meta.author == author


# Test register with dependencies
@pytest.mark.asyncio
async def test_register_dependencies(reset_registry):
    reg = reset_registry
    kind = PlugInKind.CORE_SERVICE

    # Register dependency first
    @reg.register(kind=kind, name="BaseDep", version="1.0.0", author="Test")
    class BaseDep(MockPlugin):
        pass

    # Register plugin with dependency
    deps = [{"kind": "core_service", "name": "BaseDep", "version": ">=1.0"}]

    @reg.register(kind=kind, name="DepPlugin", version="1.0.0", author="Test", dependencies=deps)
    class DepPlugin(MockPlugin):
        pass

    meta = reg.get_metadata(kind, "DepPlugin")
    assert meta.dependencies == deps


# Test register duplicate with lower version should fail
def test_register_duplicate_lower_version(reset_registry):
    reg = reset_registry
    kind = PlugInKind.CORE_SERVICE
    name = "DupPlugin"

    @reg.register(kind=kind, name=name, version="2.0.0", author="Test")
    class DupPlugin(MockPlugin):
        pass

    with pytest.raises(ValueError, match="not newer than existing"):

        @reg.register(kind=kind, name=name, version="1.0.0", author="Test")
        class DupPlugin2(MockPlugin):
            pass


# Test unregister
@pytest.mark.asyncio
async def test_unregister(reset_registry, mock_plugin_class):
    reg = reset_registry
    kind = PlugInKind.CORE_SERVICE
    name = "UnregPlugin"

    @reg.register(kind=kind, name=name, version="1.0.0", author="Test")
    class UnregPlugin(mock_plugin_class):
        pass

    await reg.unregister(kind, name)
    assert reg.get(kind, name) is None


# Test get unregistered returns None
def test_get_unregistered(reset_registry):
    reg = reset_registry
    result = reg.get(PlugInKind.CORE_SERVICE, "missing")
    assert result is None


# Test list_plugins
def test_list_plugins(reset_registry):
    reg = reset_registry
    kind = PlugInKind.CORE_SERVICE
    name = "ListPlugin"

    @reg.register(kind=kind, name=name, version="1.0.0", author="Test")
    class ListPlugin(MockPlugin):
        pass

    plugins = reg.list_plugins()
    assert kind in plugins
    assert name in plugins[kind]


# Test list_plugins by kind
def test_list_plugins_by_kind(reset_registry):
    reg = reset_registry
    kind = PlugInKind.CORE_SERVICE
    name = "KindPlugin"

    @reg.register(kind=kind, name=name, version="1.0.0", author="Test")
    class KindPlugin(MockPlugin):
        pass

    plugins = reg.list_plugins(kind=kind)
    assert name in plugins


def test_export_registry(reset_registry, mock_plugin_class):
    reg = reset_registry
    kind = PlugInKind.CORE_SERVICE
    name = "ExportPlugin"

    @reg.register(kind=kind, name=name, version="1.0.0", author="Test")
    class ExportPlugin(mock_plugin_class):
        pass

    exported = reg.export_registry()
    assert kind.value in exported
    assert name in exported[kind.value]
    assert exported[kind.value][name]["name"] == name
    assert exported[kind.value][name]["author"] == "Test"
    assert exported[kind.value][name]["version"] == "1.0.0"


# Test health_check
@pytest.mark.asyncio
async def test_health_check(reset_registry):
    reg = reset_registry
    kind = PlugInKind.CORE_SERVICE
    name = "HealthPlugin"

    plugin_instance = MockPlugin()
    reg.register_instance(kind, name, plugin_instance, version="1.0.0", author="Test")

    is_healthy = await reg.health_check(kind, name)
    assert is_healthy is True


# Test health_check with unhealthy plugin
@pytest.mark.asyncio
async def test_health_check_unhealthy(reset_registry):
    reg = reset_registry
    kind = PlugInKind.CORE_SERVICE
    name = "UnhealthyPlugin"

    class UnhealthyPlugin(MockPlugin):
        async def health_check(self):
            return False

    plugin_instance = UnhealthyPlugin()
    reg.register_instance(kind, name, plugin_instance, version="1.0.0", author="Test")

    is_healthy = await reg.health_check(kind, name)
    assert is_healthy is False


# Test health_check_all
@pytest.mark.asyncio
async def test_health_check_all(reset_registry):
    reg = reset_registry
    kind = PlugInKind.CORE_SERVICE

    # Register healthy plugin
    healthy_instance = MockPlugin()
    reg.register_instance(kind, "HealthyPlugin", healthy_instance, version="1.0.0", author="Test")

    # Register unhealthy plugin
    class UnhealthyPlugin(MockPlugin):
        async def health_check(self):
            return False

    unhealthy_instance = UnhealthyPlugin()
    reg.register_instance(
        kind, "UnhealthyPlugin", unhealthy_instance, version="1.0.0", author="Test"
    )

    health = await reg.health_check_all()
    assert health["overall_status"] == "degraded"
    assert health["plugins"][kind.value]["HealthyPlugin"] == "healthy"
    assert health["plugins"][kind.value]["UnhealthyPlugin"] == "unhealthy"


# Test initialize_all
@pytest.mark.asyncio
async def test_initialize_all(reset_registry):
    reg = reset_registry
    kind = PlugInKind.CORE_SERVICE

    initialized = []

    class InitPlugin(MockPlugin):
        async def initialize(self):
            initialized.append(self)

    plugin1 = InitPlugin()
    plugin2 = InitPlugin()
    reg.register_instance(kind, "InitPlugin1", plugin1, version="1.0.0", author="Test")
    reg.register_instance(kind, "InitPlugin2", plugin2, version="1.0.0", author="Test")

    await reg.initialize_all()
    assert len(initialized) == 2


# Test start_all
@pytest.mark.asyncio
async def test_start_all(reset_registry):
    reg = reset_registry
    kind = PlugInKind.CORE_SERVICE

    started = []

    class StartPlugin(MockPlugin):
        async def start(self):
            started.append(self)

    plugin1 = StartPlugin()
    plugin2 = StartPlugin()
    reg.register_instance(kind, "StartPlugin1", plugin1, version="1.0.0", author="Test")
    reg.register_instance(kind, "StartPlugin2", plugin2, version="1.0.0", author="Test")

    await reg.start_all()
    assert len(started) == 2


# Test stop_all
@pytest.mark.asyncio
async def test_stop_all(reset_registry):
    reg = reset_registry
    kind = PlugInKind.CORE_SERVICE

    stopped = []

    class StopPlugin(MockPlugin):
        async def stop(self):
            stopped.append(self)

    plugin1 = StopPlugin()
    plugin2 = StopPlugin()
    reg.register_instance(kind, "StopPlugin1", plugin1, version="1.0.0", author="Test")
    reg.register_instance(kind, "StopPlugin2", plugin2, version="1.0.0", author="Test")

    await reg.stop_all()
    assert len(stopped) == 2


# Test dependency validation
def test_dependency_validation_missing(reset_registry):
    reg = reset_registry
    kind = PlugInKind.CORE_SERVICE

    deps = [{"kind": "core_service", "name": "MissingDep", "version": ">=1.0"}]

    with pytest.raises(PluginDependencyError, match="not found"):

        @reg.register(
            kind=kind,
            name="NeedsDep",
            version="1.0.0",
            author="Test",
            dependencies=deps,
        )
        class NeedsDep(MockPlugin):
            pass


# Test dependency version conflict
def test_dependency_version_conflict(reset_registry):
    reg = reset_registry
    kind = PlugInKind.CORE_SERVICE

    # Register dependency with version 1.0.0
    @reg.register(kind=kind, name="OldDep", version="1.0.0", author="Test")
    class OldDep(MockPlugin):
        pass

    # Try to register plugin requiring version >= 2.0
    deps = [{"kind": "core_service", "name": "OldDep", "version": ">=2.0"}]

    with pytest.raises(PluginDependencyError, match="does not satisfy"):

        @reg.register(
            kind=kind,
            name="NeedsNewDep",
            version="1.0.0",
            author="Test",
            dependencies=deps,
        )
        class NeedsNewDep(MockPlugin):
            pass


# Test circular dependency detection
def test_circular_dependency(reset_registry):
    reg = reset_registry
    kind = PlugInKind.CORE_SERVICE

    # Register A depending on B
    deps_a = [{"kind": "core_service", "name": "PluginB", "version": ">=1.0"}]

    # First register B without dependencies
    @reg.register(kind=kind, name="PluginB", version="1.0.0", author="Test")
    class PluginB(MockPlugin):
        pass

    # Register A with dependency on B
    @reg.register(kind=kind, name="PluginA", version="1.0.0", author="Test", dependencies=deps_a)
    class PluginA(MockPlugin):
        pass

    # Now try to add C that would create a cycle: C -> A -> B, and if B depended on C
    deps_c = [{"kind": "core_service", "name": "PluginA", "version": ">=1.0"}]

    # This should work as there's no cycle yet
    @reg.register(kind=kind, name="PluginC", version="1.0.0", author="Test", dependencies=deps_c)
    class PluginC(MockPlugin):
        pass


# Test reload functionality - FIXED VERSION
@pytest.mark.asyncio
async def test_reload(reset_registry):
    reg = reset_registry
    kind = PlugInKind.CORE_SERVICE
    name = "ReloadPlugin"

    # Create a mock module with proper spec
    from importlib.machinery import ModuleSpec

    mock_module = MagicMock()
    mock_module.__name__ = "test_module"
    mock_module.__file__ = "/fake/path/test_module.py"
    mock_module.__loader__ = MagicMock()
    mock_module.__spec__ = ModuleSpec(
        "test_module", mock_module.__loader__, origin="/fake/path/test_module.py"
    )

    @reg.register(kind=kind, name=name, version="1.0.0", author="Test")
    class ReloadPlugin(MockPlugin):
        def on_reload(self):
            pass

    # Set up the mock module to have our plugin
    ReloadPlugin.__module__ = "test_module"
    mock_module.ReloadPlugin = ReloadPlugin

    # Add the module to sys.modules so importlib.reload can find it
    sys.modules["test_module"] = mock_module

    try:
        # Patch importlib functions in the arbiter_plugin_registry module directly
        with patch(
            "arbiter_plugin_registry.importlib.import_module", return_value=mock_module
        ), patch("arbiter_plugin_registry.importlib.reload", return_value=mock_module):
            result = await reg.reload(kind, name)
            assert result is True
    finally:
        # Clean up sys.modules
        sys.modules.pop("test_module", None)


# Test sandboxed_plugin context manager
def test_sandboxed_plugin(reset_registry):
    reg = reset_registry
    kind = PlugInKind.CORE_SERVICE
    name = "SandboxPlugin"

    class SandboxPlugin(MockPlugin):
        def execute(self):
            return "executed"

    @reg.register(kind=kind, name=name, version="1.0.0", author="Test")
    class SandboxPluginClass(SandboxPlugin):
        pass

    # Mock multiprocessing components
    with patch("multiprocessing.Queue") as mock_queue_class, patch(
        "multiprocessing.Process"
    ) as mock_process_class:

        mock_queue = MagicMock()
        mock_queue_class.return_value = mock_queue
        mock_queue.empty.return_value = False
        mock_queue.get.return_value = ("success", "executed")

        mock_process = MagicMock()
        mock_process_class.return_value = mock_process
        mock_process.is_alive.return_value = False

        with reg.sandboxed_plugin(kind, name):
            pass

        mock_process.start.assert_called_once()
        mock_process.join.assert_called()


# Test async context manager
@pytest.mark.asyncio
async def test_async_context_manager(reset_registry):
    reg = reset_registry
    kind = PlugInKind.CORE_SERVICE

    initialized = []
    stopped = []

    class ContextPlugin(MockPlugin):
        async def initialize(self):
            initialized.append(self)

        async def stop(self):
            stopped.append(self)

    plugin = ContextPlugin()
    reg.register_instance(kind, "ContextPlugin", plugin, version="1.0.0", author="Test")

    async with reg:
        assert len(initialized) == 1

    assert len(stopped) == 1


# Test event hook
def test_event_hook(reset_registry):
    reg = reset_registry
    events = []

    def event_handler(event_dict):
        events.append(event_dict)

    reg.set_event_hook(event_handler)

    kind = PlugInKind.CORE_SERVICE
    name = "EventPlugin"

    @reg.register(kind=kind, name=name, version="1.0.0", author="Test")
    class EventPlugin(MockPlugin):
        pass

    assert len(events) == 1
    assert events[0]["event"] == "plugin_registered"
    assert events[0]["name"] == name


def test_persist_and_load(reset_registry, mocker, mock_plugin_class):
    reg = reset_registry
    kind = PlugInKind.CORE_SERVICE
    name = "LoadedPlugin"

    @reg.register(kind=kind, name=name, version="1.0.0", author="Test")
    class LoadedPlugin(mock_plugin_class):
        pass

    mocker.patch("os.path.exists", return_value=True)
    mocker.patch(
        "json.load",
        return_value={
            "core_service": {
                "LoadedPlugin": {
                    "name": "LoadedPlugin",
                    "kind": "core_service",
                    "version": "1.0.0",
                    "author": "Test",
                    "description": None,
                    "tags": [],
                    "loaded_at": time.time(),
                    "plugin_type": "class",
                    "dependencies": [],
                    "rbac_roles": [],
                    "signature": None,
                    "is_quarantined": False,
                    "health": None,
                }
            }
        },
    )

    new_reg = PluginRegistry(persist_path=str(reg._persist_path))
    meta = new_reg.get_metadata(kind, name)
    assert meta is not None
    assert meta.name == "LoadedPlugin"
    assert meta.version == "1.0.0"
    assert meta.author == "Test"


# Test validate name
def test_validate_name(reset_registry):
    reg = reset_registry

    # Valid names
    reg._validate_name("valid_name")
    reg._validate_name("valid-name")
    reg._validate_name("ValidName123")

    # Invalid names
    with pytest.raises(ValueError, match="Invalid plugin name"):
        reg._validate_name("")

    with pytest.raises(ValueError, match="Invalid plugin name"):
        reg._validate_name("invalid name")

    with pytest.raises(ValueError, match="Invalid plugin name"):
        reg._validate_name("invalid@name")


# Test validate version
def test_validate_version(reset_registry):
    reg = reset_registry

    # Valid versions
    reg._validate_version("1.0.0")
    reg._validate_version("2.1.3")
    reg._validate_version("0.0.1")

    # Invalid versions
    with pytest.raises(ValueError, match="Invalid version"):
        reg._validate_version("invalid")

    with pytest.raises(ValueError, match="Invalid version"):
        reg._validate_version("1.0")


# Test quarantined plugin health check
@pytest.mark.asyncio
async def test_quarantined_plugin_health_check(reset_registry):
    reg = reset_registry
    kind = PlugInKind.CORE_SERVICE
    name = "QuarantinedPlugin"

    # Create a quarantined plugin
    meta = PluginMeta(name=name, kind=kind, version="1.0.0", author="Test", is_quarantined=True)

    plugin = MockPlugin()
    reg._plugins[kind] = {name: plugin}
    reg._meta[kind] = {name: meta}

    is_healthy = await reg.health_check(kind, name)
    assert is_healthy is False


# Test health check for non-existent plugin
@pytest.mark.asyncio
async def test_health_check_nonexistent(reset_registry):
    reg = reset_registry
    is_healthy = await reg.health_check(PlugInKind.CORE_SERVICE, "NonExistent")
    assert is_healthy is False


# Test that PLUGIN_REGISTRY is a dictionary
def test_plugin_registry_constant():
    assert isinstance(PLUGIN_REGISTRY, dict)
    # It should be empty initially (in test environment)
    assert len(PLUGIN_REGISTRY) == 0


# Test register_instance
def test_register_instance(reset_registry):
    reg = reset_registry
    kind = PlugInKind.CORE_SERVICE
    name = "InstancePlugin"

    instance = MockPlugin()
    reg.register_instance(kind, name, instance, version="1.0.0", author="Test")

    retrieved = reg.get(kind, name)
    assert retrieved is instance

    meta = reg.get_metadata(kind, name)
    assert meta.plugin_type == "instance"
