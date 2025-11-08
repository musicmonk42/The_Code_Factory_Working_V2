# OmniCore Omega Pro Engine Plugin Development Guide

## Overview

Plugins extend functionality (fixes, checks, simulations, services). Place plugins in `PLUGIN_DIR` and decorate with `@plugin`.

## Plugin Structure

```python
from omnicore_engine.plugin_registry import plugin, PlugInKind

@plugin(
    kind=PlugInKind.FIX,
    name="sync_plugin",
    version="1.0.0",
    params_schema={"input": {"type": "string"}, "output": {"type": "string"}},
    description="Synchronous plugin for processing strings",
    safe=True
)
def sync_plugin(input: str) -> dict:
    return {"output": f"Processed {input}"}
```

- `kind`: FIX, CHECK, SIMULATION_RUNNER, CORE_SERVICE
- `safe`: If True, runs in isolated process

## Plugin Lifecycle

- **Loading:** Registered at startup or file change
- **Execution:** Via API, CLI, or MessageBus
- **Hot-Reloading:** Watchdog reloads modified plugins
- **Rollback:** Use PluginRollbackHandler for revert

## Advanced Topics

- Use Prometheus metrics for plugin execution
- Integrate with PluginMarketplace for installation

## Best Practices

- Use async for I/O plugins
- Define params_schema for input validation
- Log with structlog
- Add tests for each plugin
- Mark plugins `safe=True` for critical tasks

**See:**  
- [TESTING.md](TESTING.md)  
- [ARCHITECTURE.md](ARCHITECTURE.md)