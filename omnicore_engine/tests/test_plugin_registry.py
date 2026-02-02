"""
Test suite for omnicore_engine/plugin_registry.py
Tests plugin registration, execution, versioning, and security features.
"""

import hashlib
import hmac
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

# Add the parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from omnicore_engine.plugin_registry import (
    Plugin,
    PluginDependencyGraph,
    PlugInKind,
    PluginMarketplace,
    PluginMeta,
    PluginPerformanceTracker,
    PluginRegistry,
    PluginVersionManager,
    SecurityError,
    _all_picklable,
    _is_picklable,
    plugin,
    safe_exec_plugin,
    validate_plugin_path,
    verify_plugin_signature,
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
        """Test plugin signature verification with valid HMAC-SHA256 signature"""
        code = b"test plugin code"
        
        # Mock settings with a known signing key
        with patch("omnicore_engine.plugin_registry.settings") as mock_settings:
            test_key = "test_signing_key"
            mock_settings.PLUGIN_SIGNING_KEY = test_key
            
            # Calculate HMAC signature (not plain SHA256)
            import hmac
            import hashlib
            expected_signature = hmac.new(test_key.encode(), code, hashlib.sha256).hexdigest()
            
            result = verify_plugin_signature(code, expected_signature)
            assert result == True

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
        # Use safe=False to avoid sandbox execution in tests.
        # When safe=True (default), the Plugin.execute method uses asyncio.to_thread
        # with process sandboxing which doesn't work well with Mock objects.
        meta = PluginMeta(name="test", kind="execution", safe=False)

        # Use a real function instead of Mock to avoid the execute attribute issue.
        # Mock auto-creates any attribute on access, so self.fn.execute exists
        # and the Plugin class incorrectly calls fn.execute instead of fn.
        def fn(*args, **kwargs):
            return "test_result"

        tracker = Mock()
        tracker.record_performance = AsyncMock()
        return Plugin(meta, fn, tracker)

    @pytest.mark.asyncio
    async def test_execute_sync_function(self, plugin_instance):
        """Test executing synchronous plugin function"""
        result = await plugin_instance.execute(arg1="test")

        assert result == "test_result"

    @pytest.mark.asyncio
    async def test_execute_async_function(self):
        """Test executing asynchronous plugin function"""
        meta = PluginMeta(name="async_test", kind="execution", safe=False)

        # Use a real async function
        async def async_fn(*args, **kwargs):
            return "async_result"

        tracker = Mock()
        tracker.record_performance = AsyncMock()
        plugin = Plugin(meta, async_fn, tracker)

        result = await plugin.execute(arg1="test")

        assert result == "async_result"

    @pytest.mark.asyncio
    async def test_execute_with_performance_tracking(self):
        """Test plugin execution with performance tracking"""
        meta = PluginMeta(name="tracked", kind="execution", safe=False)
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
    async def test_execute_with_error(self):
        """Test plugin execution error handling"""
        meta = PluginMeta(name="error_test", kind="execution", safe=False)

        # Use a function that raises an error
        def error_fn(*args, **kwargs):
            raise Exception("Test error")

        tracker = Mock()
        tracker.record_performance = AsyncMock()
        plugin = Plugin(meta, error_fn, tracker)

        with pytest.raises(Exception, match="Test error"):
            await plugin.execute()


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

    @pytest.mark.asyncio
    async def test_register_plugin(self, registry):
        """Test registering a plugin"""
        plugin = Mock()
        plugin.meta = Mock(name="test", kind="execution")

        # Mock the audit_client to be None to avoid async task creation issues
        registry.audit_client = None

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
            # Create a test plugin file without decorator to avoid global registry issues
            plugin_file = Path(tmpdir) / "test_plugin_dir.py"
            plugin_file.write_text("""
def test_function():
    return "test"
""")

            # Test that load_from_directory doesn't raise an error
            # and can process a plugin file
            await registry.load_from_directory(tmpdir)

            # The plugin loading mechanism processes the file, but the registration
            # depends on the @plugin decorator which registers to the global registry.
            # For this test, we verify that the method completes without error.
            # The file was processed (no exceptions raised).

    @pytest.mark.asyncio
    async def test_pending_metadata_queue_thread_safety(self, registry):
        """Test that pending metadata queue operations use proper locking."""
        # Reset the pending metadata
        with registry._pending_metadata_lock:
            registry._pending_plugin_metadata.clear()
        
        # Verify lock exists and works
        assert hasattr(registry, '_pending_metadata_lock')
        
        # Test queue operation
        registry.queue_pending_metadata({'name': 'plugin1', 'kind': 'fix'})
        registry.queue_pending_metadata({'name': 'plugin2', 'kind': 'check'})
        
        # Verify count is correct
        assert registry.get_pending_metadata_count() == 2
        
        # Verify both operations used the lock (no data corruption)
        with registry._pending_metadata_lock:
            assert len(registry._pending_plugin_metadata) == 2

    @pytest.mark.asyncio
    async def test_persist_pending_metadata_success(self, registry):
        """Test successful persistence of pending metadata."""
        # Setup
        registry.db = Mock()
        registry.db.save_plugin_legacy = AsyncMock()
        
        # Queue test metadata
        registry.queue_pending_metadata({'name': 'plugin1', 'kind': 'fix'})
        registry.queue_pending_metadata({'name': 'plugin2', 'kind': 'check'})
        
        # Persist
        persisted, total = await registry._persist_pending_metadata()
        
        assert persisted == 2
        assert total == 2
        assert registry.get_pending_metadata_count() == 0
        assert registry.db.save_plugin_legacy.call_count == 2

    @pytest.mark.asyncio
    async def test_persist_pending_metadata_partial_failure(self, registry):
        """Test persistence with some failures."""
        # Setup
        registry.db = Mock()
        mock_save = AsyncMock()
        # Configure mock to succeed on first and third calls, fail on second
        mock_save.side_effect = [
            None,  # First call succeeds
            Exception("Database error"),  # Second call fails
            None,  # Third call succeeds
        ]
        registry.db.save_plugin_legacy = mock_save
        
        # Queue test metadata
        registry.queue_pending_metadata({'name': 'plugin1', 'kind': 'fix'})
        registry.queue_pending_metadata({'name': 'plugin2', 'kind': 'check'})  # Will fail
        registry.queue_pending_metadata({'name': 'plugin3', 'kind': 'validation'})
        
        # Persist
        persisted, total = await registry._persist_pending_metadata()
        
        assert persisted == 2  # 2 out of 3 succeeded
        assert total == 3
        assert registry.get_pending_metadata_count() == 0  # Queue is cleared
        assert mock_save.call_count == 3

    @pytest.mark.asyncio
    async def test_persist_pending_metadata_no_db(self, registry):
        """Test persistence when DB is not available."""
        registry.db = None
        
        # Queue test metadata
        registry.queue_pending_metadata({'name': 'plugin1', 'kind': 'fix'})
        
        # Persist should be a no-op
        persisted, total = await registry._persist_pending_metadata()
        
        assert persisted == 0
        assert total == 0
        assert registry.get_pending_metadata_count() == 1  # Metadata remains

    @pytest.mark.asyncio
    async def test_persist_pending_metadata_empty_queue(self, registry):
        """Test persistence with empty queue."""
        registry.db = Mock()
        registry.db.save_plugin_legacy = AsyncMock()
        
        # Don't queue anything
        persisted, total = await registry._persist_pending_metadata()
        
        assert persisted == 0
        assert total == 0
        registry.db.save_plugin_legacy.assert_not_called()


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
        audit.add_entry_async = AsyncMock()
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


class TestPytestCollectionFix:
    """Test that PLUGIN_REGISTRY initialization works during pytest collection"""

    def test_plugin_registry_always_initialized(self):
        """Test that PLUGIN_REGISTRY is never None, even during test collection"""
        # Import in a fresh context with collection mode set
        import os
        original_value = os.environ.get('PYTEST_COLLECTING')
        
        try:
            os.environ['PYTEST_COLLECTING'] = '1'
            # Verify current state - module is already imported, so we check existing instance
            from omnicore_engine.plugin_registry import PLUGIN_REGISTRY
            assert PLUGIN_REGISTRY is not None, "PLUGIN_REGISTRY should never be None"
            assert hasattr(PLUGIN_REGISTRY, 'performance_tracker'), "PLUGIN_REGISTRY should have performance_tracker attribute"
        finally:
            if original_value is None:
                os.environ.pop('PYTEST_COLLECTING', None)
            else:
                os.environ['PYTEST_COLLECTING'] = original_value

    def test_plugin_decorator_with_none_performance_tracker(self):
        """Test that @plugin decorator handles None performance_tracker gracefully"""
        # This simulates the scenario during pytest collection
        from omnicore_engine.plugin_registry import PlugInKind
        
        with patch("omnicore_engine.plugin_registry.PLUGIN_REGISTRY") as mock_registry:
            # Simulate early initialization where performance_tracker is None
            mock_registry.performance_tracker = None
            mock_registry.register = Mock()
            mock_registry.db = None
            
            # This should not raise AttributeError
            @plugin(
                kind=PlugInKind.FIX,
                name="test_collection_plugin",
                description="Test plugin for collection mode",
                version="1.0.0"
            )
            def test_func():
                return "test"
            
            # Verify the plugin was registered
            mock_registry.register.assert_called_once()

    def test_plugin_decorator_during_collection_mode(self):
        """Test that @plugin decorator works when PYTEST_COLLECTING=1"""
        import os
        original_value = os.environ.get('PYTEST_COLLECTING')
        
        try:
            os.environ['PYTEST_COLLECTING'] = '1'
            
            from omnicore_engine.plugin_registry import plugin, PlugInKind, PLUGIN_REGISTRY
            
            # Verify PLUGIN_REGISTRY exists
            assert PLUGIN_REGISTRY is not None
            
            # This should work without AttributeError
            @plugin(
                kind=PlugInKind.FIX,
                name="collection_mode_test",
                description="Test during collection",
                version="1.0.0"
            )
            def collection_test_func():
                return "collection test"
            
            # If we got here, the decorator worked
            assert True, "Plugin decorator should work during collection mode"
            
        finally:
            if original_value is None:
                os.environ.pop('PYTEST_COLLECTING', None)
            else:
                os.environ['PYTEST_COLLECTING'] = original_value


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--asyncio-mode=auto"])
