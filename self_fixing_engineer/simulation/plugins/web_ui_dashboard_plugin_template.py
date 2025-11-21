import asyncio
import logging
import time
import sys
import datetime
import os
import json
import re
from typing import Dict, Any, Optional, List, Callable, Union, Tuple
from pathlib import Path

# --- Conditional Imports for FastAPI, Pydantic, etc. ---
try:
    from fastapi import (
    APIRouter, Request, WebSocket, WebSocketDisconnect, Response, status, HTTPException, FastAPI, Depends
)
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel, Field, ValidationError
    import uvicorn
    FASTAPI_AVAILABLE = True
    PYDANTIC_AVAILABLE = True
except ImportError:
    logging.warning("FastAPI or Pydantic not found. Web UI Dashboard Plugin will not function in standalone API mode.")
    FASTAPI_AVAILABLE = False
    PYDANTIC_AVAILABLE = False
    APIRouter = object
    Request = object
    WebSocket = object
    WebSocketDisconnect = Exception
    JSONResponse = object
    BaseModel = object
    Field = lambda default, description=None: default
    uvicorn = None

try:
    from prometheus_client import Counter, Histogram, Gauge, REGISTRY
    PROMETHEUS_AVAILABLE = True
    def _get_or_create_metric(metric_type: type, name: str, documentation: str, labelnames: Optional[Tuple[str, ...]] = None, buckets: Optional[Tuple[float, ...]] = None) -> Any:
        if labelnames is None: labelnames = ()
        if name in REGISTRY._names_to_collectors:
            return REGISTRY._names_to_collectors[name]
        if metric_type == Histogram: return metric_type(name, documentation, labelnames=labelnames, buckets=buckets or Histogram.DEFAULT_BUCKETS)
        if metric_type == Counter: return metric_type(name, documentation, labelnames=labelnames)
        return metric_type(name, documentation, labelnames=labelnames)
except ImportError:
    PROMETHEUS_AVAILABLE = False
    logging.warning("Prometheus client not found. Metrics for dashboard plugin will be disabled.")
    class DummyMetric:
        def inc(self, amount: float = 1.0): pass
        def set(self, value: float): pass
        def observe(self, value: float): pass
        def labels(self, *args, **kwargs): return self
    _get_or_create_metric = lambda *args, **kwargs: DummyMetric()

try:
    from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
    TENACITY_AVAILABLE = True
except ImportError:
    TENACITY_AVAILABLE = False
    logging.warning("Tenacity not found. Retries for WebSocket sends will be disabled.")
    def retry(*args, **kwargs): return lambda f: f
    def stop_after_attempt(n): return None
    def wait_exponential(*args, **kwargs): return None
    def retry_if_exception_type(e): return lambda x: False

try:
    from detect_secrets.core import SecretsCollection
    from detect_secrets.settings import transient_settings
    DETECT_SECRETS_AVAILABLE = True
except ImportError:
    DETECT_SECRETS_AVAILABLE = False

try:
    from redis.asyncio import Redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False

# --- Logger Setup ---
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

# --- Pydantic Config Model ---
if PYDANTIC_AVAILABLE:
    class DashboardConfig(BaseModel):
        websocket_interval_seconds: float = Field(default=2.0, ge=0.1)
        state_storage: str = Field(default="memory", pattern="^(memory|redis)$")
        redis_url: Optional[str] = None
        frontend_version: str = Field(default=">=1.0.0")
        plugin_manifest: Dict[str, Any] = Field(default_factory=dict)
else:
    class DashboardConfig:
        def __init__(self):
            self.websocket_interval_seconds = 2.0
            self.state_storage = "memory"
            self.redis_url = None
            self.frontend_version = ">=1.0.0"
            self.plugin_manifest = {}

# --- Load Config from File or Env ---
def _load_config() -> DashboardConfig:
    config_file_path = Path(__file__).parent / "configs" / "web_ui_dashboard_config.json"
    config_dict = {}
    if config_file_path.exists():
        try:
            with open(config_file_path, 'r') as f:
                config_dict = json.load(f)
        except (IOError, json.JSONDecodeError) as e:
            logger.warning(f"Could not load config file {config_file_path}: {e}. Using environment variables and defaults.")

    # Override with environment variables
    for key, field in DashboardConfig.__annotations__.items():
        env_var = os.getenv(f"DASHBOARD_{key.upper()}")
        if env_var:
            try:
                if field == float:
                    config_dict[key] = float(env_var)
                elif field == int:
                    config_dict[key] = int(env_var)
                else:
                    config_dict[key] = env_var
            except ValueError:
                logger.warning(f"Invalid type for environment variable DASHBOARD_{key.upper()}. Using default.")
    
    # Load plugin manifest from config file if available, otherwise use hardcoded default
    default_manifest = {
        "name": "WebUIDashboardPluginTemplate",
        "version": "1.0.0",
        "description": "GOAT template for building rich, interactive dashboard UI plugins.",
        "author": "Self-Fixing Engineer Team",
        "tags": ["dashboard", "ui", "visualization", "plugin", "api"],
        "capabilities": ["ui_dashboard", "live_metrics", "interactivity"],
        "api_version": "v1",
        "entry_points": {
            "mount_router": "get_dashboard_router",
            "plugin_health_check": "plugin_health"
        },
        "license": "MIT",
        "homepage": "https://www.self-fixing.engineer",
        "required_frontend_version": ">=1.0.0"
    }
    config_dict["plugin_manifest"] = config_dict.get("plugin_manifest", default_manifest)

    if PYDANTIC_AVAILABLE:
        try:
            return DashboardConfig(**config_dict)
        except ValidationError as e:
            logger.error(f"Configuration validation failed: {e}. Using defaults.")
            return DashboardConfig(plugin_manifest=default_manifest) # Fallback with default manifest
    else:
        cfg = DashboardConfig()
        cfg.__dict__.update(config_dict)
        return cfg

CONFIG = _load_config()
PLUGIN_MANIFEST = CONFIG.plugin_manifest

# --- Prometheus Metrics ---
if PROMETHEUS_AVAILABLE:
    DASHBOARD_API_CALLS = _get_or_create_metric(Counter, 'dashboard_api_calls_total', 'Total API calls', ['endpoint'])
    WEBSOCKET_CONNECTIONS = _get_or_create_metric(Counter, 'dashboard_websocket_connections_total', 'Total WebSocket connections', ['status'])
    DASHBOARD_STATE_UPDATES = _get_or_create_metric(Counter, 'dashboard_state_updates_total', 'Total dashboard state updates')
    DASHBOARD_COMPONENT_RENDERS = _get_or_create_metric(Counter, 'dashboard_component_renders_total', 'Total component data renders', ['component_name'])
else:
    class DummyMetric:
        def inc(self, amount: float = 1.0): pass
        def set(self, value: float): pass
        def observe(self, value: float): pass
        def labels(self, *args, **kwargs): return self
    DASHBOARD_API_CALLS = WEBSOCKET_CONNECTIONS = DASHBOARD_STATE_UPDATES = DASHBOARD_COMPONENT_RENDERS = DummyMetric()


# --- Dashboard State Store (memory fallback, Redis in prod) ---
_DASHBOARD_MEMORY_STATE: Dict[str, Any] = {
    "example_metric": 42.0,
    "user_preferences": {},
    "live_data_counter": 0,
    "last_update": time.time(),
}

async def get_dashboard_state() -> Dict[str, Any]:
    """Retrieves the current dashboard state, preferring Redis if configured."""
    if CONFIG.state_storage == "redis" and REDIS_AVAILABLE and CONFIG.redis_url:
        try:
            redis_client = Redis.from_url(CONFIG.redis_url)
            state_json = await redis_client.get("dashboard_state")
            await redis_client.close()
            if state_json:
                logger.debug("Loaded state from Redis.")
                return json.loads(state_json)
        except Exception as e:
            logger.warning(f"Redis unavailable or error fetching state: {e}. Falling back to memory state.")
            if PROMETHEUS_AVAILABLE: DASHBOARD_API_CALLS.labels(endpoint='get_state_redis_fallback').inc()
    logger.debug("Using memory state.")
    return _DASHBOARD_MEMORY_STATE

async def update_dashboard_state(update_data: Dict[str, Any]):
    """Updates the dashboard state, persisting to Redis if configured."""
    global _DASHBOARD_MEMORY_STATE
    
    if CONFIG.state_storage == "redis" and REDIS_AVAILABLE and CONFIG.redis_url:
        try:
            redis_client = Redis.from_url(CONFIG.redis_url)
            current_state = await get_dashboard_state() # Get latest state including from Redis
            current_state.update(update_data)
            await redis_client.set("dashboard_state", json.dumps(current_state))
            await redis_client.close()
            logger.info("Dashboard state updated in Redis.")
        except Exception as e:
            logger.error(f"Failed to update Redis state: {e}. Updating memory state only.", exc_info=True)
            _DASHBOARD_MEMORY_STATE.update(update_data) # Fallback to memory
    else:
        _DASHBOARD_MEMORY_STATE.update(update_data)
    
    _DASHBOARD_MEMORY_STATE["last_update"] = time.time() # Update memory state timestamp regardless
    if PROMETHEUS_AVAILABLE: DASHBOARD_STATE_UPDATES.inc()

def _scrub_secrets(data: Union[Dict, List, str]) -> Union[Dict, List, str]:
    """Recursively scrubs sensitive data from a dictionary, list, or string."""
    if not DETECT_SECRETS_AVAILABLE:
        return data
    if isinstance(data, str):
        secrets = SecretsCollection()
        with transient_settings():
            secrets.scan_string(data)
        for secret in secrets:
            data = data.replace(secret.secret_value, '[REDACTED]')
        return data
    if isinstance(data, dict):
        return {k: _scrub_secrets(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_scrub_secrets(item) for item in data]
    return data

# --- UI Component Registry ---
UI_COMPONENTS: Dict[str, Callable[[Any], Dict[str, Any]]] = {}

def register_ui_component(name: str, component_func: Callable[[Any], Dict[str, Any]]):
    """
    Registers a UI component function by name.
    
    Args:
        name (str): A unique name for the UI component (e.g., "metric_card_cpu_usage").
        component_func (Callable): A function that takes the current `DASHBOARD_STATE`
                                   and returns a dictionary describing the UI component
                                   and its data (e.g., type, title, value, data points).
    """
    if name in UI_COMPONENTS:
        logger.warning(f"UI component '{name}' already registered. Overwriting.")
    UI_COMPONENTS[name] = component_func
    logger.info(f"Registered UI component: '{name}'.")

# --- Example Components ---
def get_example_metric_panel(data: Dict[str, Any]) -> Dict[str, Any]:
    """Returns configuration for a simple example metric card."""
    return {
        "type": "metric_card",
        "id": "example_metric_panel_id",
        "title": "Current Example Metric",
        "value": f"{data.get('example_metric', 0):.2f}",
        "icon": "📈",
        "unit": "units",
        "trend": "+3.5%",
        "description": "A demo metric card showing a simulated live value.",
    }
register_ui_component("example_metric_panel", get_example_metric_panel)

def get_example_chart(data: Dict[str, Any]) -> Dict[str, Any]:
    """Returns configuration for a simple line chart."""
    current_metric = data.get("example_metric", 0)
    chart_data = [{"x": i, "y": current_metric - (10 - i) * 0.2 + (data["live_data_counter"] % 10)} for i in range(10)]
    
    return {
        "type": "line_chart",
        "id": "example_trend_chart_id",
        "title": "Example Trend Chart",
        "data": chart_data,
        "x_label": "Time Point",
        "y_label": "Metric Value",
        "description": "Simulated trend over time for the example metric.",
        "series_name": "Metric Trend"
    }
register_ui_component("example_trend_chart", get_example_chart)

def get_example_table(data: Dict[str, Any]) -> Dict[str, Any]:
    """Returns configuration for a simple data table."""
    current_counter = data.get("live_data_counter", 0)
    now = datetime.datetime.now().strftime('%H:%M:%S')
    return {
        "type": "data_table",
        "id": "example_data_table_id",
        "title": "Agent Performance Summary",
        "columns": ["Agent ID", "Performance Score", "Status", "Last Update"],
        "rows": [
            ["agent_alpha", 90 + (current_counter % 5), "OK", now],
            ["agent_beta", 82 + (current_counter % 3), "Warning", now],
            ["agent_gamma", 75 + (current_counter % 2), "Critical", now],
            ["agent_delta", 95, "OK", now],
        ],
        "description": "Simulated data table showing agent performance.",
        "sortable": True,
        "searchable": True
    }
register_ui_component("example_data_table", get_example_table)

# --- FastAPI Router ---
if FASTAPI_AVAILABLE:
    router = APIRouter(prefix="/plugin/dashboard", tags=["Plugin Dashboard"])

    def validate_component_name(name: str) -> str:
        """Validates component name to prevent path traversal/injection."""
        if not re.match(r'^[a-zA-Z0-9_-]+$', name):
            logger.error(f"Invalid component name requested: {name}")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid component name")
        return name

    # Basic API Key Authentication Middleware (Conceptual)
    # In a real app, this would be more robust, e.g., using FastAPI Security
    async def get_api_key(request: Request):
        # For demo, allow without API key or check a simple header
        api_key = request.headers.get("X-API-Key")
        if os.getenv("DASHBOARD_REQUIRE_API_KEY", "false").lower() == "true":
            if not api_key or api_key != os.getenv("DASHBOARD_API_KEY"):
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API Key")
        return api_key

    @router.get("/manifest", response_model=Dict[str, Any])
    async def dashboard_manifest(api_key: str = Depends(get_api_key)) -> Dict[str, Any]:
        """Returns the plugin's manifest."""
        if PROMETHEUS_AVAILABLE: DASHBOARD_API_CALLS.labels(endpoint='manifest').inc()
        return PLUGIN_MANIFEST

    @router.get("/components", response_model=Dict[str, List[str]])
    async def dashboard_components(api_key: str = Depends(get_api_key)) -> Dict[str, List[str]]:
        """Lists all names of registered UI components."""
        if PROMETHEUS_AVAILABLE: DASHBOARD_API_CALLS.labels(endpoint='components').inc()
        return {"components": list(UI_COMPONENTS.keys())}

    @router.get("/state", response_model=Dict[str, Any])
    async def dashboard_api_state(api_key: str = Depends(get_api_key)) -> Dict[str, Any]:
        """Returns the current overall dashboard state."""
        if PROMETHEUS_AVAILABLE: DASHBOARD_API_CALLS.labels(endpoint='state').inc()
        state = await get_dashboard_state()
        return _scrub_secrets(state)

    class UpdateStateRequest(BaseModel):
        update: Dict[str, Any] = Field(..., description="Partial update for the dashboard state.")

    @router.post("/state/update", response_model=Dict[str, Any])
    async def update_dashboard_api_state(request_data: UpdateStateRequest, api_key: str = Depends(get_api_key)) -> Dict[str, Any]:
        """Allows the frontend or other systems to update parts of the dashboard state."""
        if PROMETHEUS_AVAILABLE: DASHBOARD_API_CALLS.labels(endpoint='state_update').inc()
        logger.info(f"Dashboard state update request received: {_scrub_secrets(request_data.update)}")
        await update_dashboard_state(request_data.update)
        updated_state = await get_dashboard_state()
        return {"status": "ok", "state": _scrub_secrets(updated_state)}

    @router.get("/component/{component_name}", response_model=Dict[str, Any])
    async def get_dashboard_component(component_name: str, api_key: str = Depends(get_api_key)) -> Dict[str, Any]:
        """Fetches the configuration and data for a specific UI component."""
        if PROMETHEUS_AVAILABLE: DASHBOARD_API_CALLS.labels(endpoint=f'component_{component_name}').inc()
        component_name = validate_component_name(component_name) # Validate input
        func = UI_COMPONENTS.get(component_name)
        if not func:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Component '{component_name}' not found.")
        
        try:
            current_state = await get_dashboard_state()
            data = func(current_state)
            if PROMETHEUS_AVAILABLE: DASHBOARD_COMPONENT_RENDERS.labels(component_name=component_name).inc()
            return _scrub_secrets(data)
        except Exception as e:
            logger.error(f"Error generating data for component '{component_name}': {e}", exc_info=True)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Error generating component data: {e}")

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=5), retry=(retry_if_exception_type(Exception) if TENACITY_AVAILABLE else None))
    async def _send_websocket_update(websocket: WebSocket, data: Dict[str, Any]):
        """Helper to send JSON data over WebSocket with retries."""
        await websocket.send_json(data)

    @router.websocket("/ws")
    async def dashboard_ws(websocket: WebSocket):
        """Provides a WebSocket endpoint for real-time dashboard updates."""
        connection_established = False
        try:
            await websocket.accept()
            connection_established = True
            if PROMETHEUS_AVAILABLE: WEBSOCKET_CONNECTIONS.labels(status='connected').inc()
            logger.info("Dashboard WebSocket client connected.")
            
            disconnect_event = asyncio.Event()
            
            async def disconnect_watcher():
                """Watches for disconnect signals from the client."""
                try:
                    while True:
                        msg = await websocket.receive()
                        # Handle different message types that indicate disconnection
                        if msg.get('type') in ['websocket.disconnect', 'websocket.close']:
                            disconnect_event.set()
                            break
                        # You can also handle other message types here if needed
                        # For example, ping/pong or client-side state updates
                except (WebSocketDisconnect, RuntimeError, ConnectionError, Exception):
                    # Any exception in receive means the connection is broken
                    disconnect_event.set()
                    logger.debug("WebSocket disconnect detected in watcher")
            
            watcher_task = asyncio.create_task(disconnect_watcher())
            
            try:
                # Send initial state
                initial_state = await get_dashboard_state()
                await _send_websocket_update(websocket, {"type": "initial_state", "state": _scrub_secrets(initial_state)})
                
                # Main update loop
                while not disconnect_event.is_set():
                    # Simulate data update
                    current_state = await get_dashboard_state()
                    current_state["example_metric"] += 0.5 + (current_state["live_data_counter"] * 0.01)
                    current_state["live_data_counter"] += 1
                    await update_dashboard_state(current_state)
                    
                    # Get the latest state (which might have been updated by other sources too)
                    latest_state = await get_dashboard_state()
                    
                    # Try to send the update
                    try:
                        await _send_websocket_update(websocket, {"type": "update", "state": _scrub_secrets(latest_state)})
                        logger.debug(f"Pushed live update: example_metric={latest_state['example_metric']:.2f}")
                    except (RuntimeError, WebSocketDisconnect, ConnectionError) as e:
                        # Connection is closed, exit gracefully
                        logger.debug(f"WebSocket send failed, connection likely closed: {e}")
                        break
                    
                    # Wait for the next update interval or disconnection
                    try:
                        await asyncio.wait_for(
                            disconnect_event.wait(), 
                            timeout=CONFIG.websocket_interval_seconds
                        )
                        # If we get here, disconnect_event was set
                        break
                    except asyncio.TimeoutError:
                        # Timeout is normal - continue the loop for the next update
                        continue
                        
            except WebSocketDisconnect:
                logger.info("WebSocket disconnected by client.")
            except Exception as e:
                if PROMETHEUS_AVAILABLE: WEBSOCKET_CONNECTIONS.labels(status='error').inc()
                logger.error(f"Unexpected WebSocket error: {e}", exc_info=True)
                # Try to close gracefully with error code
                try:
                    await websocket.close(code=status.WS_1011_INTERNAL_ERROR, reason=str(e)[:125])
                except:
                    pass  # Connection already closed
            finally:
                # Clean up the watcher task
                watcher_task.cancel()
                try:
                    await watcher_task
                except asyncio.CancelledError:
                    pass
                    
        finally:
            # Always increment disconnected counter when a connection that was established ends
            if connection_established:
                if PROMETHEUS_AVAILABLE: WEBSOCKET_CONNECTIONS.labels(status='disconnected').inc()
                logger.info("Dashboard WebSocket client disconnected.")

    def get_dashboard_router() -> APIRouter:
        """
        Entrypoint: Called by the host platform (e.g., SFE's `api.py`) to mount this plugin's router.

        Returns:
            APIRouter: The FastAPI APIRouter instance containing all dashboard endpoints.
        """
        logger.info("Mounting Web UI Dashboard Plugin Template router.")
        return router

else:
    def get_dashboard_router() -> object:
        logger.error("FastAPI not available. Cannot return a functional APIRouter.")
        return object()

# --- Standalone Test Mode ---
if __name__ == "__main__":
    if not FASTAPI_AVAILABLE:
        print("FastAPI is not installed. Cannot run the plugin's API in standalone mode.", file=sys.stderr)
        sys.exit(1)
    
    # Create a dummy FastAPI app to mount the router
    app = FastAPI(title="Web UI Dashboard Plugin Test Host")
    
    # Dependency for API key (mocked for standalone)
    from fastapi import Depends
    app.dependency_overrides[get_api_key] = lambda: "mock_api_key"

    app.include_router(get_dashboard_router())

    @app.get("/")
    async def root():
        return {"message": "Web UI Dashboard Plugin Test Host is running. Access /plugin/dashboard/manifest"}

    print("\n--- Running Web UI Dashboard Plugin in Standalone Mode (Uvicorn) ---")
    print("Access manifest:         http://localhost:8000/plugin/dashboard/manifest")
    print("Access dashboard state:    http://localhost:8000/plugin/dashboard/state")
    print("WebSocket for live:        ws://localhost:8000/plugin/dashboard/ws")
    
    uvicorn.run(app, host="0.0.0.0", port=8000)