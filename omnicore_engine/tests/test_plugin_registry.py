"""
Test suite for omnicore_engine/plugin_registry.py
Tests plugin registration, execution, versioning, and security features.
"""

import pytest
import os
import sys
import tempfile
import hashlib
import hmac
from pathlib import Path
from unittest.mock import Mock, patch, AsyncMock

# Add the parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from omnicore_engine.plugin_registry import (
    PluginRegistry,
    PluginMeta,
    Plugin,
    PlugInKind,
    SecurityError,
    safe_exec_plugin,
    verify_plugin_signature,
    validate_plugin_path,
    PluginPerformanceTracker,
    PluginVersionManager,
    PluginDependencyGraph,
    PluginMarketplace,
    plugin,
    _is_picklable,
    _all_picklable,
)


class TestSecurityFunctions:
    """Test security-related functions"""

    def test_safe_exec_plugin_allowed_imports(self):
        """Test safe_exec_plugin with allowed imports"""
        code = """
import math
import json

def test_func():
    return math.pi + json.dumps({"test": 1})
"""
        result = safe_exec_plugin(code, "test.py")
        assert "test_func" in result

    def test_safe_exec_plugin_blocked_imports(self):
        """Test safe_exec_plugin blocks dangerous imports"""
        code = """
import os
def test_func():
    return os.system('ls')
"""
        with pytest.raises(SecurityError, match="Import of os not allowed"):
            safe_exec_plugin(code, "test.py")

    def test_safe_exec_plugin_blocked_functions(self):
        """Test safe_exec_plugin blocks dangerous functions"""
        code = """
def test_func():
    return eval('1+1')
"""
        with pytest.raises(SecurityError, match="Dangerous function eval not allowed"):
            safe_exec_plugin(code, "test.py")

    def test_verify_plugin_signature_valid(self):
        """Test plugin signature verification with valid signature"""
        code = b"test plugin code"
        key = b"test_key"

        with patch("omnicore_engine.plugin_registry.settings") as mock_settings:
            mock_settings.PLUGIN_SIGNING_KEY = key.decode()

            expected_sig = hmac.new(key, code, hashlib.sha256).hexdigest()
            assert verify_plugin_signature(code, expected_sig) == True

    def test_verify_plugin_signature_invalid(self):
        """Test plugin signature verification with invalid signature"""
        code = b"test plugin code"

        with patch("omnicore_engine.plugin_registry.settings") as mock_settings:
            mock_settings.PLUGIN_SIGNING_KEY = "test_key"

            assert verify_plugin_signature(code, "invalid_signature") == False

    def test_validate_plugin_path_valid(self):
        """Test path validation for valid paths"""
        with tempfile.TemporaryDirectory() as tmpdir:
            plugin_dir = Path(tmpdir) / "plugins"
            plugin_dir.mkdir()

            valid_path = plugin_dir / "test_plugin.py"
            valid_path.touch()

            result = validate_plugin_path(valid_path, plugin_dir)
            assert result == valid_path.resolve()

    def test_validate_plugin_path_traversal(self):
        """Test path validation blocks path traversal"""
        with tempfile.TemporaryDirectory() as tmpdir:
            plugin_dir = Path(tmpdir) / "plugins"
            plugin_dir.mkdir()

            malicious_path = plugin_dir / ".." / "etc" / "passwd"

            with pytest.raises(SecurityError, match="Path traversal detected"):
                validate_plugin_path(malicious_path, plugin_dir)


class TestPluginMeta:
    """Test PluginMeta model"""

    def test_plugin_meta_creation(self):
        """Test creating PluginMeta with all fields"""
        meta = PluginMeta(
            name="test_plugin",
            kind="execution",
            description="Test plugin",
            version="1.0.0",
            safe=True,
            source="local",
            params_schema={"param1": "string"},
            signature="test_sig",
        )

        assert meta.name == "test_plugin"
        assert meta.kind == "execution"
        assert meta.version == "1.0.0"
        assert meta.safe == True

    def test_plugin_meta_defaults(self):
        """Test PluginMeta default values"""
        meta = PluginMeta(name="test", kind="check")

        assert meta.description == ""
        assert meta.version == "0.1.0"
        assert meta.safe == True
        assert meta.source == "local"
        assert meta.params_schema == {}
        assert meta.signature is None


class TestPlugin:
    """Test Plugin class"""

    @pytest.fixture
    def plugin_instance(self):
        """Create a test plugin instance"""
        meta = PluginMeta(name="test", kind="execution")
        fn = Mock(return_value="test_result")
        tracker = Mock()
        return Plugin(meta, fn, tracker)

    @pytest.mark.asyncio
    async def test_execute_sync_function(self, plugin_instance):
        """Test executing synchronous plugin function"""
        result = await plugin_instance.execute(arg1="test")

        assert result == "test_result"
        plugin_instance.fn.assert_called_once_with(arg1="test")

    @pytest.mark.asyncio
    async def test_execute_async_function(self):
        """Test executing asynchronous plugin function"""
        meta = PluginMeta(name="async_test", kind="execution")
        async_fn = AsyncMock(return_value="async_result")
        plugin = Plugin(meta, async_fn)

        result = await plugin.execute(arg1="test")

        assert result == "async_result"
        async_fn.assert_called_once_with(arg1="test")

    @pytest.mark.asyncio
    async def test_execute_with_performance_tracking(self):
        """Test plugin execution with performance tracking"""
        meta = PluginMeta(name="tracked", kind="execution")
        fn = Mock(return_value="result")
        tracker = Mock()
        tracker.record_performance = AsyncMock()

        plugin = Plugin(meta, fn, tracker)

        await plugin.execute()

        tracker.record_performance.assert_called_once()
        call_args = tracker.record_performance.call_args[0]
        assert call_args[0] == "execution"  # kind
        assert call_args[1] == "tracked"  # name
        assert call_args[2] == "0.1.0"  # version
        assert "execution_time" in call_args[3]  # metrics

    @pytest.mark.asyncio
    async def test_execute_with_error(self, plugin_instance):
        """Test plugin execution error handling"""
        plugin_instance.fn.side_effect = Exception("Test error")

        with pytest.raises(Exception, match="Test error"):
            await plugin_instance.execute()


class TestPluginRegistry:
    """Test PluginRegistry class"""

    @pytest.fixture
    def registry(self):
        """Create a test registry"""
        reg = PluginRegistry()
        reg.db = Mock()
        reg.audit_client = Mock()
        reg.message_bus = Mock()
        return reg

    def test_register_plugin(self, registry):
        """Test registering a plugin"""
        plugin = Mock()
        plugin.meta = Mock(name="test", kind="execution")

        registry.register("execution", "test", plugin)

        assert registry.plugins["execution"]["test"] == plugin

    def test_unregister_plugin(self, registry):
        """Test unregistering a plugin"""
        plugin = Mock()
        plugin.message_bus_adapter = None
        registry.plugins["execution"]["test"] = plugin

        result = registry.unregister("execution", "test")

        assert result == True
        assert "test" not in registry.plugins["execution"]

    def test_unregister_nonexistent_plugin(self, registry):
        """Test unregistering non-existent plugin"""
        result = registry.unregister("execution", "nonexistent")
        assert result == False

    def test_get_plugin(self, registry):
        """Test getting a specific plugin"""
        plugin = Mock()
        registry.plugins["execution"]["test"] = plugin

        result = registry.get("execution", "test")
        assert result == plugin

        result = registry.get("execution", "nonexistent")
        assert result is None

    def test_get_plugins_by_kind(self, registry):
        """Test getting all plugins of a kind"""
        plugin1 = Mock()
        plugin2 = Mock()
        registry.plugins["execution"]["test1"] = plugin1
        registry.plugins["execution"]["test2"] = plugin2

        result = registry.get_plugins_by_kind("execution")
        assert len(result) == 2
        assert plugin1 in result
        assert plugin2 in result

    def test_list_plugins(self, registry):
        """Test listing all plugins"""
        registry.plugins["execution"]["test1"] = Mock()
        registry.plugins["validation"]["test2"] = Mock()

        result = registry.list_plugins()
        assert result == {"execution": ["test1"], "validation": ["test2"]}

    @pytest.mark.asyncio
    async def test_load_from_directory(self, registry):
        """Test loading plugins from directory"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a test plugin file
            plugin_file = Path(tmpdir) / "test_plugin.py"
            plugin_file.write_text(
                """
from omnicore_engine.plugin_registry import plugin, PlugInKind

@plugin(kind=PlugInKind.EXECUTION, name="test_plugin")
def test_function():
    return "test"
"""
            )

            await registry.load_from_directory(tmpdir)

            # Plugin should be registered
            assert "test_plugin" in registry.get_plugin_names("execution")


class TestPluginPerformanceTracker:
    """Test PluginPerformanceTracker class"""

    @pytest.fixture
    def tracker(self):
        """Create a test tracker"""
        db = Mock()
        db.save_audit_record = AsyncMock()
        db.query_audit_records = AsyncMock()
        audit = Mock()
        return PluginPerformanceTracker(db, audit)

    @pytest.mark.asyncio
    async def test_record_performance(self, tracker):
        """Test recording performance metrics"""
        metrics = {"execution_time": 1.5, "error_rate": 0.1}

        await tracker.record_performance("execution", "test", "1.0.0", metrics)

        tracker.db.save_audit_record.assert_called_once()
        call_arg = tracker.db.save_audit_record.call_args[0][0]
        assert call_arg["kind"] == "plugin_performance"
        assert call_arg["name"] == "execution:test"
        assert call_arg["detail"] == metrics

    @pytest.mark.asyncio
    async def test_get_performance_history(self, tracker):
        """Test retrieving performance history"""
        tracker.db.query_audit_records.return_value = [
            {
                "detail": {"execution_time": 1.0},
                "custom_attributes": {"version": "1.0.0"},
            },
            {
                "detail": {"execution_time": 2.0},
                "custom_attributes": {"version": "1.0.0"},
            },
        ]

        history = await tracker.get_performance_history("execution", "test", "1.0.0")

        assert len(history) == 2
        assert history[0]["execution_time"] == 1.0
        assert history[1]["execution_time"] == 2.0


class TestPluginVersionManager:
    """Test PluginVersionManager class"""

    @pytest.fixture
    def version_manager(self):
        """Create a test version manager"""
        registry = Mock()
        db = Mock()
        db.save_plugin_legacy = AsyncMock()
        db.get_plugin_legacy = AsyncMock()
        audit = Mock()
        return PluginVersionManager(registry, db, audit)

    @pytest.mark.asyncio
    async def test_register_version(self, version_manager):
        """Test registering a plugin version"""
        plugin = Mock()
        plugin.meta = Mock(
            description="test", safe=True, source="local", params_schema={}
        )
        plugin.fn = lambda: "test"

        await version_manager.register_version("execution", "test", plugin, "1.0.0")

        assert len(version_manager.versions["execution"]["test"]) == 1
        version_manager.db.save_plugin_legacy.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_version_from_memory(self, version_manager):
        """Test getting version from memory"""
        plugin = Mock()
        plugin.meta = Mock(version="1.0.0")
        version_manager.versions["execution"]["test"] = [plugin]

        result = await version_manager.get_version("execution", "test", "1.0.0")
        assert result == plugin

    @pytest.mark.asyncio
    async def test_get_version_from_db(self, version_manager):
        """Test getting version from database"""
        version_manager.db.get_plugin_legacy.return_value = {
            "name": "test",
            "kind": "execution",
            "version": "1.0.0",
            "code": "def test(): return 'test'",
        }

        result = await version_manager.get_version("execution", "test", "1.0.0")
        assert result is not None
        assert result.meta.version == "1.0.0"


class TestPluginDependencyGraph:
    """Test PluginDependencyGraph class"""

    def test_add_dependency(self):
        """Test adding dependencies"""
        registry = Mock()
        graph = PluginDependencyGraph(registry)

        graph.add_dependency("plugin1", "plugin2")
        graph.add_dependency("plugin2", "plugin3")

        if graph.is_networkx_available:
            assert ("plugin1", "plugin2") in graph.graph.edges()
        else:
            assert "plugin2" in graph.graph["plugin1"]

    def test_resolve_order_simple(self):
        """Test resolving plugin order"""
        registry = Mock()
        graph = PluginDependencyGraph(registry)

        graph.add_dependency("plugin1", "plugin2")
        graph.add_dependency("plugin2", "plugin3")

        order = graph.resolve_order()

        # plugin1 should come before plugin2, plugin2 before plugin3
        assert order.index("plugin1") < order.index("plugin2")
        assert order.index("plugin2") < order.index("plugin3")

    def test_resolve_order_cyclic(self):
        """Test detecting cyclic dependencies"""
        registry = Mock()
        graph = PluginDependencyGraph(registry)

        graph.add_dependency("plugin1", "plugin2")
        graph.add_dependency("plugin2", "plugin3")
        graph.add_dependency("plugin3", "plugin1")  # Creates cycle

        with pytest.raises(ValueError, match="Cyclic dependency detected"):
            graph.resolve_order()


class TestPluginDecorator:
    """Test the @plugin decorator"""

    def test_plugin_decorator_registration(self):
        """Test that decorator registers plugin"""
        with patch("omnicore_engine.plugin_registry.PLUGIN_REGISTRY") as mock_registry:
            mock_registry.performance_tracker = None
            mock_registry.register = Mock()
            mock_registry.db = None

            @plugin(
                kind=PlugInKind.EXECUTION,
                name="decorated_test",
                description="Test decorated plugin",
            )
            def test_func():
                return "test"

            mock_registry.register.assert_called_once()
            call_args = mock_registry.register.call_args[0]
            assert call_args[0] == "execution"
            assert call_args[1] == "decorated_test"


class TestHelperFunctions:
    """Test helper functions"""

    def test_is_picklable_true(self):
        """Test _is_picklable with picklable object"""
        assert _is_picklable("test") == True
        assert _is_picklable([1, 2, 3]) == True
        assert _is_picklable({"key": "value"}) == True

    def test_is_picklable_false(self):
        """Test _is_picklable with non-picklable object"""
        import threading

        lock = threading.Lock()
        assert _is_picklable(lock) == False

    def test_all_picklable_true(self):
        """Test _all_picklable with all picklable objects"""
        assert _all_picklable("test", 123, [1, 2]) == True

    def test_all_picklable_false(self):
        """Test _all_picklable with non-picklable object"""
        import threading

        lock = threading.Lock()
        assert _all_picklable("test", lock) == False


class TestPluginMarketplace:
    """Test PluginMarketplace class"""

    @pytest.fixture
    def marketplace(self):
        """Create test marketplace"""
        db = Mock()
        db.save_plugin_legacy = AsyncMock()
        db.save_preferences = AsyncMock()
        redis = Mock()
        audit = Mock()
        return PluginMarketplace(db, redis, audit)

    @pytest.mark.asyncio
    async def test_install_plugin(self, marketplace):
        """Test installing a plugin"""
        await marketplace.install_plugin("execution", "test", "1.0.0")

        marketplace.db.save_plugin_legacy.assert_called_once()
        call_arg = marketplace.db.save_plugin_legacy.call_args[0][0]
        assert call_arg["name"] == "test"
        assert call_arg["kind"] == "execution"
        assert call_arg["version"] == "1.0.0"

    @pytest.mark.asyncio
    async def test_rate_plugin_valid(self, marketplace):
        """Test rating a plugin with valid rating"""
        await marketplace.rate_plugin(
            "execution", "test", "1.0.0", rating=5, comment="Great!", user_id="user123"
        )

        marketplace.db.save_preferences.assert_called_once()

    @pytest.mark.asyncio
    async def test_rate_plugin_invalid_rating(self, marketplace):
        """Test rating with invalid value"""
        with pytest.raises(ValueError, match="Rating must be between"):
            await marketplace.rate_plugin(
                "execution",
                "test",
                "1.0.0",
                rating=10,
                comment="Test",
                user_id="user123",
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--asyncio-mode=auto"])
