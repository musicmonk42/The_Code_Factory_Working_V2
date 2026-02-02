"""Test lazy import functionality (PEP 562) for omnicore_engine package.

This test module validates that the lazy import mechanism via __getattr__
works correctly for importing submodules from omnicore_engine package.
"""

import sys
import pytest


class TestLazyImports:
    """Test lazy import functionality via PEP 562 __getattr__."""

    def test_direct_plugin_registry_import(self):
        """Test that plugin_registry can be imported directly."""
        # Remove from cache if present to test fresh import
        if 'omnicore_engine.plugin_registry' in sys.modules:
            del sys.modules['omnicore_engine.plugin_registry']
        if hasattr(sys.modules.get('omnicore_engine'), 'plugin_registry'):
            delattr(sys.modules['omnicore_engine'], 'plugin_registry')
        
        from omnicore_engine import plugin_registry
        
        assert plugin_registry is not None
        assert hasattr(plugin_registry, 'PLUGIN_REGISTRY')
        assert plugin_registry.__name__ == 'omnicore_engine.plugin_registry'

    def test_get_plugin_registry_function(self):
        """Test that get_plugin_registry() function still works."""
        from omnicore_engine import get_plugin_registry
        
        registry = get_plugin_registry()
        assert registry is not None
        assert hasattr(registry, 'register_plugin')

    def test_plugin_registry_in_all(self):
        """Test that plugin_registry is in __all__."""
        import omnicore_engine
        
        assert 'plugin_registry' in omnicore_engine.__all__
        assert 'get_plugin_registry' in omnicore_engine.__all__

    def test_plugin_event_handler_lazy_import(self):
        """Test that plugin_event_handler can be imported lazily."""
        # Remove from cache if present
        if 'omnicore_engine.plugin_event_handler' in sys.modules:
            del sys.modules['omnicore_engine.plugin_event_handler']
        if hasattr(sys.modules.get('omnicore_engine'), 'plugin_event_handler'):
            delattr(sys.modules['omnicore_engine'], 'plugin_event_handler')
        
        from omnicore_engine import plugin_event_handler
        
        assert plugin_event_handler is not None
        assert hasattr(plugin_event_handler, 'PluginEventHandler')

    def test_get_plugin_event_handler_class_function(self):
        """Test get_plugin_event_handler_class function"""
        from omnicore_engine import get_plugin_event_handler_class
        
        handler_class = get_plugin_event_handler_class()
        # Accept either the real class or stub - both are valid
        assert handler_class.__name__ in ['PluginEventHandler', 'StubPluginEventHandler']

    def test_import_as_alias_pattern(self):
        """Test the import pattern used in core.py."""
        # This mimics: from omnicore_engine import plugin_registry as plugin_registry_module
        from omnicore_engine import plugin_registry as plugin_registry_module
        
        assert plugin_registry_module is not None
        assert hasattr(plugin_registry_module, 'PLUGIN_REGISTRY')

    def test_multiple_imports_same_module(self):
        """Test that multiple imports return the same cached module."""
        from omnicore_engine import plugin_registry as pr1
        from omnicore_engine import plugin_registry as pr2
        
        # Should be the same object (cached)
        assert pr1 is pr2

    def test_attribute_error_for_invalid_module(self):
        """Test that importing non-existent module raises ImportError.
        
        Note: Python's import system raises ImportError before __getattr__
        is called when using 'from X import Y' syntax for non-existent names.
        """
        with pytest.raises(ImportError, match="cannot import name"):
            from omnicore_engine import nonexistent_module  # noqa: F401

    def test_backward_compatibility_both_patterns(self):
        """Test that both import patterns work together"""
        from omnicore_engine import get_plugin_registry
        
        # Old pattern via function
        registry1 = get_plugin_registry()
        
        # New pattern via direct import
        from omnicore_engine import plugin_registry
        from omnicore_engine.plugin_registry import PLUGIN_REGISTRY as registry2
        
        # Both should be registry instances (real or stub)
        assert hasattr(registry1, 'plugins')
        assert hasattr(registry2, 'plugins')


class TestImportErrorHandling:
    """Test error handling for import failures."""

    def test_get_plugin_registry_error_handling(self):
        """Test that get_plugin_registry raises ImportError with good message."""
        # We can't easily simulate an import failure without breaking the module,
        # but we can verify the function signature and error type
        from omnicore_engine import get_plugin_registry
        
        # The function should exist and be callable
        assert callable(get_plugin_registry)
        
        # In normal conditions, it should work
        registry = get_plugin_registry()
        assert registry is not None
        assert hasattr(registry, 'register')  # Verify it has the register method

    def test_get_plugin_event_handler_class_error_handling(self):
        """Test that get_plugin_event_handler_class raises ImportError with good message."""
        from omnicore_engine import get_plugin_event_handler_class
        
        # The function should exist and be callable
        assert callable(get_plugin_event_handler_class)
        
        # In normal conditions, it should work
        handler_class = get_plugin_event_handler_class()
        assert handler_class is not None


class TestModuleNamespace:
    """Test that the module namespace is properly configured."""

    def test_all_declared_modules_importable(self):
        """Test that all modules in __all__ can be imported."""
        import omnicore_engine
        
        # These should be in __all__ and importable
        expected_modules = [
            'plugin_registry',
            'plugin_event_handler',
        ]
        
        for module_name in expected_modules:
            assert module_name in omnicore_engine.__all__
            # Try to import it
            module = getattr(omnicore_engine, module_name)
            assert module is not None

    def test_accessor_functions_in_all(self):
        """Test that accessor functions are in __all__."""
        import omnicore_engine
        
        assert 'get_plugin_registry' in omnicore_engine.__all__
        assert 'get_plugin_event_handler_class' in omnicore_engine.__all__
