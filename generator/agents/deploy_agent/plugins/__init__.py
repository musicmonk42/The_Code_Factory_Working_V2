"""Deployment plugins package."""

from pathlib import Path
import importlib.util
import logging

logger = logging.getLogger(__name__)

def discover_plugins():
    """
    Discover and load all deployment plugins.
    
    This function can be used for plugin discovery when needed.
    Note: The PluginRegistry in deploy_agent.py has its own discovery mechanism.
    """
    plugins = {}
    plugin_dir = Path(__file__).parent
    
    # Look for Python files in the plugins directory (excluding __init__.py and test files)
    for plugin_file in plugin_dir.glob("*.py"):
        if plugin_file.name.startswith("__") or plugin_file.name.endswith("_test.py"):
            continue
            
        try:
            module_name = plugin_file.stem
            spec = importlib.util.spec_from_file_location(module_name, plugin_file)
            if not spec or not spec.loader:
                logger.warning(f"Could not load spec for {plugin_file}")
                continue
                
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            # Look for PLUGIN_MANIFEST if it exists (optional)
            if hasattr(module, "PLUGIN_MANIFEST"):
                manifest = module.PLUGIN_MANIFEST
                plugins[manifest["name"]] = manifest
                logger.info(f"Loaded deployment plugin: {manifest['name']}")
            else:
                # Plugin loaded but no manifest - just log
                logger.info(f"Loaded plugin module: {module_name}")
        except Exception as e:
            logger.error(f"Failed to load plugin {plugin_file}: {e}")
    
    return plugins

__all__ = ["discover_plugins"]

