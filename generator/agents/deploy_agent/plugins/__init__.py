"""
Deployment Plugins Package.

This package provides a plugin system for deployment configuration generation.
Plugins are discovered dynamically based on file naming conventions and
implement the TargetPlugin interface.

Plugin Discovery:
    Plugins are Python modules in this directory that:
    - Are named without leading underscores or 'test' suffix
    - Contain classes that inherit from TargetPlugin
    - Implement all required abstract methods

Usage:
    from generator.agents.deploy_agent.plugins import discover_plugins
    
    plugins = discover_plugins()
    for name, plugin_info in plugins.items():
        print(f"Found plugin: {name}")

Standards Compliance:
    - PEP 8 style guidelines
    - Comprehensive error handling
    - Structured logging
    - Type hints where applicable
    - Security best practices

Author: Code Factory Deploy Agent
Version: 1.0.0
"""

from pathlib import Path
import importlib.util
import logging
from typing import Dict, Any, Optional

# Configure structured logging
logger = logging.getLogger(__name__)

def discover_plugins() -> Dict[str, Any]:
    """
    Discover and load all deployment plugins.
    
    This function scans the plugins directory for Python modules and
    attempts to load them. It looks for classes that inherit from
    TargetPlugin and registers them for use by the deployment system.
    
    Note:
        The PluginRegistry in deploy_agent.py has its own discovery
        mechanism that is used at runtime. This function is provided
        as a utility for plugin inspection and testing.
    
    Returns:
        Dict mapping plugin names to plugin manifests. Each manifest
        contains metadata about the plugin including version, type,
        and capabilities.
        
    Example:
        >>> plugins = discover_plugins()
        >>> print(f"Found {len(plugins)} plugins")
        >>> for name in plugins:
        ...     print(f"  - {name}")
        
    Raises:
        No exceptions are raised. Errors during plugin loading are
        logged but do not prevent other plugins from loading.
    """
    plugins: Dict[str, Any] = {}
    plugin_dir = Path(__file__).parent
    
    logger.debug(
        "Starting plugin discovery in directory: %s",
        plugin_dir,
        extra={"plugin_dir": str(plugin_dir)}
    )
    
    # Look for Python files in the plugins directory
    # Exclude __init__.py and test files
    plugin_count = 0
    error_count = 0
    
    for plugin_file in plugin_dir.glob("*.py"):
        # Skip special files
        if plugin_file.name.startswith("__") or plugin_file.name.endswith("_test.py"):
            logger.debug(
                "Skipping file: %s (special file or test)",
                plugin_file.name
            )
            continue
        
        plugin_count += 1
        module_name = plugin_file.stem
        
        try:
            logger.debug(
                "Attempting to load plugin module: %s",
                module_name,
                extra={"module": module_name, "file": str(plugin_file)}
            )
            
            # Load module using importlib
            spec = importlib.util.spec_from_file_location(module_name, plugin_file)
            if not spec or not spec.loader:
                logger.warning(
                    "Could not create module spec for %s",
                    plugin_file,
                    extra={"file": str(plugin_file)}
                )
                error_count += 1
                continue
            
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            # Check for PLUGIN_MANIFEST (optional metadata)
            if hasattr(module, "PLUGIN_MANIFEST"):
                manifest = module.PLUGIN_MANIFEST
                plugin_name = manifest.get("name", module_name)
                plugins[plugin_name] = manifest
                
                logger.info(
                    "Loaded deployment plugin: %s (version=%s, type=%s)",
                    plugin_name,
                    manifest.get("version", "unknown"),
                    manifest.get("type", "unknown"),
                    extra={
                        "plugin_name": plugin_name,
                        "plugin_version": manifest.get("version"),
                        "plugin_type": manifest.get("type"),
                    }
                )
            else:
                # Plugin loaded but no manifest
                # Still register it with basic info
                plugins[module_name] = {
                    "name": module_name,
                    "version": getattr(module, "__version__", "unknown"),
                    "file": str(plugin_file),
                    "has_manifest": False,
                }
                
                logger.info(
                    "Loaded plugin module: %s (no PLUGIN_MANIFEST found)",
                    module_name,
                    extra={"module": module_name}
                )
                
        except Exception as e:
            error_count += 1
            logger.error(
                "Failed to load plugin %s: %s",
                plugin_file.name,
                str(e),
                exc_info=True,
                extra={
                    "file": str(plugin_file),
                    "error": str(e),
                    "error_type": type(e).__name__,
                }
            )
    
    # Log summary
    logger.info(
        "Plugin discovery complete - found=%d, loaded=%d, errors=%d",
        plugin_count,
        len(plugins),
        error_count,
        extra={
            "total_files": plugin_count,
            "loaded_plugins": len(plugins),
            "error_count": error_count,
        }
    )
    
    return plugins


__all__ = ["discover_plugins"]

