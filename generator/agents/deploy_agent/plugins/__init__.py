"""Deployment plugins package."""

from pathlib import Path
import importlib.util
import logging

logger = logging.getLogger(__name__)

def discover_plugins():
    """Discover and load all deployment plugins."""
    plugins = {}
    plugin_dir = Path(__file__).parent
    
    for plugin_file in plugin_dir.glob("*_plugin.py"):
        try:
            module_name = plugin_file.stem
            spec = importlib.util.spec_from_file_location(module_name, plugin_file)
            if not spec or not spec.loader:
                logger.warning(f"Could not load spec for {plugin_file}")
                continue
                
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            if hasattr(module, "PLUGIN_MANIFEST"):
                manifest = module.PLUGIN_MANIFEST
                plugins[manifest["name"]] = manifest
                logger.info(f"Loaded deployment plugin: {manifest['name']}")
        except Exception as e:
            logger.error(f"Failed to load plugin {plugin_file}: {e}")
    
    return plugins

__all__ = ["discover_plugins"]
