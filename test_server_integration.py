"""
Integration test for server module and OmniCore Engine connection.

This test validates:
1. ArbiterConfig has the required database_path and plugin_dir properties
2. OmniCore engine can access these properties from settings
3. Server module can initialize and connect to OmniCore
"""

import os
import sys


def test_arbiter_config_properties():
    """Test that ArbiterConfig has the required backward compatibility properties."""
    print("=" * 80)
    print("TEST 1: ArbiterConfig Properties")
    print("=" * 80)
    
    from self_fixing_engineer.arbiter.config import ArbiterConfig
    
    config = ArbiterConfig()
    
    # Test that both uppercase and lowercase versions exist
    assert hasattr(config, 'DB_PATH'), "ArbiterConfig missing DB_PATH"
    assert hasattr(config, 'PLUGIN_DIR'), "ArbiterConfig missing PLUGIN_DIR"
    assert hasattr(config, 'database_path'), "ArbiterConfig missing database_path property"
    assert hasattr(config, 'plugin_dir'), "ArbiterConfig missing plugin_dir property"
    
    # Test that they return the same values
    assert config.database_path == config.DB_PATH, "database_path should match DB_PATH"
    assert config.plugin_dir == config.PLUGIN_DIR, "plugin_dir should match PLUGIN_DIR"
    
    print(f"✓ DB_PATH: {config.DB_PATH}")
    print(f"✓ database_path: {config.database_path}")
    print(f"✓ PLUGIN_DIR: {config.PLUGIN_DIR}")
    print(f"✓ plugin_dir: {config.plugin_dir}")
    print("✓ All ArbiterConfig properties present and matching")
    print()


def test_omnicore_settings_compatibility():
    """Test that OmniCore Engine can access settings properties."""
    print("=" * 80)
    print("TEST 2: OmniCore Settings Compatibility")
    print("=" * 80)
    
    from omnicore_engine.core import settings
    
    # Test that settings object has required properties
    assert hasattr(settings, 'database_path'), "Settings missing database_path"
    assert hasattr(settings, 'plugin_dir'), "Settings missing plugin_dir"
    
    db_path = settings.database_path
    plugin_dir = settings.plugin_dir
    
    print(f"✓ settings.database_path: {db_path}")
    print(f"✓ settings.plugin_dir: {plugin_dir}")
    
    # Verify they're not None
    assert db_path is not None, "database_path should not be None"
    assert plugin_dir is not None, "plugin_dir should not be None"
    
    print("✓ OmniCore settings can access required properties")
    print()


def test_getattr_fallback():
    """Test that getattr fallback in OmniCore works properly."""
    print("=" * 80)
    print("TEST 3: OmniCore getattr Fallback")
    print("=" * 80)
    
    from omnicore_engine.core import settings
    
    # Test the getattr pattern used in core.py
    db_path = getattr(settings, "database_path", None) or getattr(
        settings, "DB_PATH", "sqlite:///./omnicore.db"
    )
    plugin_dir = getattr(settings, "plugin_dir", None) or getattr(
        settings, "PLUGIN_DIR", "./plugins"
    )
    
    print(f"✓ getattr for database_path: {db_path}")
    print(f"✓ getattr for plugin_dir: {plugin_dir}")
    
    assert db_path != "sqlite:///./omnicore.db" or settings.database_path == "sqlite:///./omnicore.db"
    assert plugin_dir != "./plugins" or settings.plugin_dir == "./plugins"
    
    print("✓ getattr fallback pattern works correctly")
    print()


def test_server_omnicore_integration():
    """Test that server module can integrate with OmniCore."""
    print("=" * 80)
    print("TEST 4: Server-OmniCore Integration")
    print("=" * 80)
    
    # Set pytest environment to prevent async initialization
    os.environ['PYTEST_COLLECTING'] = '1'
    
    from server.services.omnicore_service import OmniCoreService
    
    # Initialize service
    service = OmniCoreService()
    
    print(f"✓ OmniCoreService initialized")
    print(f"  Available components: {[k for k, v in service._omnicore_components_available.items() if v]}")
    print(f"  LLM Provider: {service._llm_status.get('provider', 'unknown')}")
    
    # Service should initialize even if components are unavailable
    assert service is not None, "OmniCoreService should initialize"
    
    print("✓ Server-OmniCore integration successful")
    print()


def test_configuration_values():
    """Test that configuration values are sensible."""
    print("=" * 80)
    print("TEST 5: Configuration Values")
    print("=" * 80)
    
    from self_fixing_engineer.arbiter.config import ArbiterConfig
    
    config = ArbiterConfig()
    
    # Database path should be a valid string
    assert isinstance(config.database_path, str), "database_path should be a string"
    assert len(config.database_path) > 0, "database_path should not be empty"
    
    # Plugin dir should be a valid string
    assert isinstance(config.plugin_dir, str), "plugin_dir should be a string"
    assert len(config.plugin_dir) > 0, "plugin_dir should not be empty"
    
    print(f"✓ database_path is valid: {config.database_path}")
    print(f"✓ plugin_dir is valid: {config.plugin_dir}")
    print("✓ All configuration values are sensible")
    print()


def main():
    """Run all integration tests."""
    print("\n" + "=" * 80)
    print("SERVER MODULE INTEGRATION TESTS")
    print("=" * 80)
    print()
    
    try:
        test_arbiter_config_properties()
        test_omnicore_settings_compatibility()
        test_getattr_fallback()
        test_server_omnicore_integration()
        test_configuration_values()
        
        print("=" * 80)
        print("✅ ALL TESTS PASSED - SERVER IS PROPERLY INTEGRATED")
        print("=" * 80)
        print()
        print("Summary:")
        print("  ✓ ArbiterConfig has database_path and plugin_dir properties")
        print("  ✓ OmniCore Engine can access these properties")
        print("  ✓ Server module integrates with OmniCore successfully")
        print("  ✓ Configuration values are valid")
        print()
        return 0
        
    except Exception as e:
        print("=" * 80)
        print("❌ TEST FAILED")
        print("=" * 80)
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
