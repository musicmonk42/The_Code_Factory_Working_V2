# simulation/__init__.py

# --- Entry points for OmniCore ---
def simulation_run_entrypoint(*args, **kwargs):
    # This should call your orchestrator's main run logic (async or sync as needed)
    from .main import main as simulation_main
    import asyncio
    return asyncio.run(simulation_main(*args, **kwargs))

def simulation_health_check():
    # Returns a simple health dict or raises
    from .main import health_check as simulation_health_check_func
    return simulation_health_check_func()

def simulation_get_registry():
    # Return the SIM_REGISTRY or other registry dict
    from .registry import get_registry
    return get_registry()

# --- Register with OmniCore if running inside it ---
def _register_with_omnicore():
    try:
        from omnicore_engine.engines import register_engine  # or omnicore.engine_registry, adjust as needed
        register_engine(
            "simulation",
            entrypoints={
                "run": simulation_run_entrypoint,
                "health_check": simulation_health_check,
                "get_registry": simulation_get_registry,
            }
        )
        # Optionally emit to audit:
        from .policy_and_audit import emit_audit_event
        emit_audit_event("simulation_engine_registered", {"status": "success"})
    except ImportError:
        # Not running under OmniCore, skip registration
        pass

_register_with_omnicore()
