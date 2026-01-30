"""
Test that the Docker deployment plugin is properly loaded and functional.
"""

import asyncio
import tempfile
from pathlib import Path
import pytest

from generator.agents.deploy_agent.deploy_agent import (
    DeployAgent,
    PluginRegistry,
)


def test_docker_plugin_discovery():
    """Test that the Docker plugin is discovered and loaded."""
    # Get the plugin directory
    plugin_dir = Path(__file__).parent.parent / "agents" / "deploy_agent" / "plugins"
    
    # Create a plugin registry
    registry = PluginRegistry(plugin_dir=str(plugin_dir))
    
    # Check if docker plugin is loaded
    docker_plugin = registry.get_plugin("docker_plugin")
    
    # The plugin should be loaded with the module name as key
    assert docker_plugin is not None, "Docker plugin should be discovered and loaded"
    
    # Check that the plugin has the required methods
    assert hasattr(docker_plugin, "generate_config"), "Plugin should have generate_config method"
    assert hasattr(docker_plugin, "validate_config"), "Plugin should have validate_config method"
    assert hasattr(docker_plugin, "simulate_deployment"), "Plugin should have simulate_deployment method"
    assert hasattr(docker_plugin, "rollback"), "Plugin should have rollback method"
    assert hasattr(docker_plugin, "health_check"), "Plugin should have health_check method"
    
    # Test health check
    assert docker_plugin.health_check() is True, "Plugin health check should return True"


@pytest.mark.asyncio
async def test_docker_plugin_functionality():
    """Test that the Docker plugin can execute basic operations."""
    # Get the plugin directory
    plugin_dir = Path(__file__).parent.parent / "agents" / "deploy_agent" / "plugins"
    
    # Create a plugin registry
    registry = PluginRegistry(plugin_dir=str(plugin_dir))
    
    # Get docker plugin
    docker_plugin = registry.get_plugin("docker_plugin")
    
    if docker_plugin is None:
        pytest.skip("Docker plugin not found")
    
    # Test generate_config
    config_result = await docker_plugin.generate_config(
        target_files=["main.py", "requirements.txt"],
        instructions="Create a simple Docker setup",
        context={"language": "python"},
        previous_configs={},
    )
    assert isinstance(config_result, dict), "generate_config should return a dict"
    assert config_result.get("status") == "success", "generate_config should succeed"
    
    # Test validate_config
    validate_result = await docker_plugin.validate_config({"test": "config"})
    assert isinstance(validate_result, dict), "validate_config should return a dict"
    assert "valid" in validate_result, "validate_config should include 'valid' key"
    
    # Test simulate_deployment
    simulate_result = await docker_plugin.simulate_deployment({"test": "config"})
    assert isinstance(simulate_result, dict), "simulate_deployment should return a dict"
    assert simulate_result.get("status") == "success", "simulate_deployment should succeed"
    
    # Test rollback
    rollback_result = await docker_plugin.rollback({"test": "config"})
    assert isinstance(rollback_result, bool), "rollback should return a bool"


@pytest.mark.asyncio
async def test_deploy_agent_with_docker_plugin():
    """Test that DeployAgent can use the Docker plugin."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir)
        
        # Create repo structure
        (repo_path / ".git").mkdir()
        (repo_path / "src").mkdir()
        (repo_path / "src" / "main.py").write_text("print('Hello')")
        
        # Get the plugin directory
        plugin_dir = Path(__file__).parent.parent / "agents" / "deploy_agent" / "plugins"
        
        # Create DeployAgent
        agent = DeployAgent(
            repo_path=str(repo_path),
            plugin_dir=str(plugin_dir),
        )
        
        # Initialize the database
        await agent._init_db()
        
        # Check that docker plugin is available
        docker_plugin = agent.plugin_registry.get_plugin("docker_plugin")
        assert docker_plugin is not None, "DeployAgent should have access to Docker plugin"


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v"])
