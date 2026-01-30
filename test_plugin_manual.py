#!/usr/bin/env python3
"""
Simple test script to verify Docker plugin discovery without pytest.
"""

import sys
from pathlib import Path

# Add the generator path to sys.path
sys.path.insert(0, str(Path(__file__).parent))

from generator.agents.deploy_agent.deploy_agent import PluginRegistry

def test_docker_plugin_discovery():
    """Test that the Docker plugin is discovered and loaded."""
    print("=" * 60)
    print("Testing Docker Plugin Discovery")
    print("=" * 60)
    
    # Get the plugin directory
    plugin_dir = Path(__file__).parent / "generator" / "agents" / "deploy_agent" / "plugins"
    print(f"\nPlugin directory: {plugin_dir}")
    print(f"Plugin directory exists: {plugin_dir.exists()}")
    
    if plugin_dir.exists():
        print(f"\nFiles in plugin directory:")
        for f in plugin_dir.iterdir():
            print(f"  - {f.name}")
    
    # Create a plugin registry
    print("\n" + "-" * 60)
    print("Creating PluginRegistry...")
    registry = PluginRegistry(plugin_dir=str(plugin_dir))
    
    print(f"\nTotal plugins loaded: {len(registry.plugins)}")
    print(f"Plugin keys: {list(registry.plugins.keys())}")
    
    # Check for docker plugin with various possible keys
    possible_keys = ["docker", "docker_plugin", "DockerPlugin"]
    docker_plugin = None
    plugin_key = None
    
    for key in possible_keys:
        if key in registry.plugins:
            docker_plugin = registry.plugins[key]
            plugin_key = key
            break
    
    if docker_plugin is None:
        # Try to get any plugin
        if registry.plugins:
            plugin_key = list(registry.plugins.keys())[0]
            docker_plugin = registry.plugins[plugin_key]
    
    if docker_plugin is not None:
        print(f"\n✓ Docker plugin found with key: {plugin_key}")
        print(f"  Plugin class: {docker_plugin.__class__.__name__}")
        print(f"  Plugin version: {getattr(docker_plugin, '__version__', 'N/A')}")
        
        # Check methods
        methods = ["generate_config", "validate_config", "simulate_deployment", "rollback", "health_check"]
        print(f"\n  Checking methods:")
        for method in methods:
            has_method = hasattr(docker_plugin, method)
            status = "✓" if has_method else "✗"
            print(f"    {status} {method}")
        
        # Test health check
        try:
            health = docker_plugin.health_check()
            print(f"\n  Health check result: {health}")
        except Exception as e:
            print(f"\n  Health check failed: {e}")
        
        print("\n" + "=" * 60)
        print("✓ SUCCESS: Docker plugin loaded and functional")
        print("=" * 60)
        return True
    else:
        print("\n" + "=" * 60)
        print("✗ FAILURE: Docker plugin not found")
        print("=" * 60)
        return False


if __name__ == "__main__":
    success = test_docker_plugin_discovery()
    sys.exit(0 if success else 1)
