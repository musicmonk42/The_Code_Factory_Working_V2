# simulation/viz.py
import asyncio
import functools
import hashlib
import json
import logging
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

# --- Conditional Imports for Enhancements ---
try:
    import matplotlib.pyplot as plt

    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False
    plt = None  # Placeholder for mocking

try:
    import plotly.graph_objects as go
    import plotly.io as pio

    PLOTLY_AVAILABLE = True
    PLOTLY_EXPORT_AVAILABLE = True  # Requires kaleido or orca
except ImportError:
    PLOTLY_AVAILABLE = False
    PLOTLY_EXPORT_AVAILABLE = False
    go = None  # Placeholder for mocking
    pio = None  # Placeholder for mocking

try:
    from pydantic import BaseModel, Field, ValidationError

    PYDANTIC_AVAILABLE = True
except ImportError:
    PYDANTIC_AVAILABLE = False

try:
    from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

    TENACITY_AVAILABLE = True
except ImportError:
    TENACITY_AVAILABLE = False

    def retry(*args, **kwargs):
        return lambda f: f

    def stop_after_attempt(n):
        return None

    def wait_exponential(*args, **kwargs):
        return None

    def retry_if_exception_type(e):
        return lambda x: False


try:
    from prometheus_client import REGISTRY, Counter, Histogram

    PROMETHEUS_AVAILABLE = True

    def _get_or_create_metric(
        metric_type: type,
        name: str,
        documentation: str,
        labelnames: Optional[Tuple[str, ...]] = None,
        buckets: Optional[Tuple[float, ...]] = None,
    ) -> Any:
        if labelnames is None:
            labelnames = ()
        if name in REGISTRY._names_to_collectors:
            return REGISTRY._names_to_collectors[name]
        if metric_type == Histogram:
            return metric_type(
                name,
                documentation,
                labelnames=labelnames,
                buckets=buckets or Histogram.DEFAULT_BUCKETS,
            )
        if metric_type == Counter:
            return metric_type(name, documentation, labelnames=labelnames)
        return metric_type(name, documentation, labelnames=labelnames)

except ImportError:
    PROMETHEUS_AVAILABLE = False

    class DummyMetric:
        def inc(self, amount: float = 1.0):
            pass

        def set(self, value: float):
            pass

        def observe(self, value: float):
            pass

        def labels(self, *args, **kwargs):
            return self

    def _get_or_create_metric(*args, **kwargs):
        return DummyMetric()


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
logger = logging.getLogger("simulation.viz")
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)


# --- Pydantic Config Model ---
if PYDANTIC_AVAILABLE:

    class VizConfig(BaseModel):
        results_dir: str = Field(default="./simulation_results")
        default_plot_format: str = Field(default="png", pattern="^(png|svg|html|jpeg|webp)$")
        redis_cache_url: Optional[str] = None
        redis_cache_ttl: int = Field(default=3600, ge=1)

else:

    class VizConfig:
        def __init__(self):
            self.results_dir = "./simulation_results"
            self.default_plot_format = "png"
            self.redis_cache_url = None
            self.redis_cache_ttl = 3600


# --- Load Config from File or Env ---
def _load_config() -> VizConfig:
    config_file_path = Path(__file__).parent / "configs" / "viz_config.json"
    config_dict = {}
    if config_file_path.exists():
        try:
            with open(config_file_path, "r") as f:
                config_dict = json.load(f)
        except (IOError, json.JSONDecodeError) as e:
            logger.warning(
                f"Could not load config file {config_file_path}: {e}. Using environment variables and defaults."
            )

    # Handle both Pydantic and non-Pydantic cases
    if PYDANTIC_AVAILABLE:
        config_fields = getattr(VizConfig, "__annotations__", {})
    else:
        # For non-Pydantic fallback, define expected fields
        config_fields = {
            "results_dir": str,
            "default_plot_format": str,
            "redis_cache_url": Optional[str],
            "redis_cache_ttl": int,
        }

    for key, field_type in config_fields.items():
        env_var = os.getenv(f"VIZ_{key.upper()}")
        if env_var:
            try:
                if field_type == int or (
                    hasattr(field_type, "__origin__") and int in field_type.__args__
                ):
                    config_dict[key] = int(env_var)
                else:
                    config_dict[key] = env_var
            except (ValueError, TypeError, AttributeError):
                logger.warning(
                    f"Invalid type for environment variable VIZ_{key.upper()}. Using default."
                )

    if PYDANTIC_AVAILABLE:
        try:
            if hasattr(VizConfig, "model_validate"):
                return VizConfig.model_validate(config_dict)
            else:  # Fallback for Pydantic v1
                return VizConfig.parse_obj(config_dict)
        except Exception as e:
            logger.error(f"Configuration validation failed: {e}. Using defaults.")
            return VizConfig()
    else:
        cfg = VizConfig()
        cfg.__dict__.update(config_dict)
        return cfg


CONFIG = _load_config()

# Ensure results directory exists
RESULTS_DIR = Path(CONFIG.results_dir)
os.makedirs(RESULTS_DIR, exist_ok=True)

# --- Prometheus Metrics ---
if PROMETHEUS_AVAILABLE:
    PLOT_GENERATIONS = _get_or_create_metric(
        Counter,
        "viz_plot_generations_total",
        "Total plots generated",
        ("plot_type", "status"),
    )
    PLOT_EXPORTS = _get_or_create_metric(
        Counter, "viz_plot_exports_total", "Total plot exports", ("format", "status")
    )
    PLOT_CACHE_HITS = _get_or_create_metric(
        Counter, "viz_plot_cache_hits_total", "Total plot cache hits"
    )
    PLOT_CACHE_MISSES = _get_or_create_metric(
        Counter, "viz_plot_cache_misses_total", "Total plot cache misses"
    )
else:
    # Create dummy metrics for when Prometheus is not available
    PLOT_GENERATIONS = _get_or_create_metric(
        None, "viz_plot_generations_total", "Total plots generated"
    )
    PLOT_EXPORTS = _get_or_create_metric(None, "viz_plot_exports_total", "Total plot exports")
    PLOT_CACHE_HITS = _get_or_create_metric(
        None, "viz_plot_cache_hits_total", "Total plot cache hits"
    )
    PLOT_CACHE_MISSES = _get_or_create_metric(
        None, "viz_plot_cache_misses_total", "Total plot cache misses"
    )


# --- Custom Plot Registration ---
_registered_viz_panels: Dict[str, Dict[str, Any]] = {}

_pre_plot_hooks: List[Callable[[str, Dict[str, Any]], Dict[str, Any]]] = []
_post_plot_hooks: List[Callable[[str, Any, Dict[str, Any]], Tuple[Any, Dict[str, Any]]]] = []


def register_pre_plot_hook(fn: Callable[[str, Dict[str, Any]], Dict[str, Any]]) -> None:
    """Registers a pre-plot hook function."""
    _pre_plot_hooks.append(fn)
    logger.info(f"Registered pre-plot hook: {fn.__name__}")


def register_post_plot_hook(
    fn: Callable[[str, Any, Dict[str, Any]], Tuple[Any, Dict[str, Any]]],
) -> None:
    """Registers a post-plot hook function."""
    _post_plot_hooks.append(fn)
    logger.info(f"Registered post-plot hook: {fn.__name__}")


def validate_panel_id(panel_id: str) -> str:
    """Validates a panel ID to prevent injection attacks."""
    if not re.match(r"^[a-zA-Z0-9_-]+$", panel_id):
        raise ValueError(f"Invalid panel ID: '{panel_id}'. Contains disallowed characters.")
    return panel_id


def register_viz_panel(
    panel_id: str,
    title: str,
    description: str = "",
    plot_type: str = "matplotlib",
    roles: Optional[List[str]] = None,
    export_supported: bool = True,
    export_args: Optional[Tuple] = None,
    export_kwargs: Optional[Dict[str, Any]] = None,
    overwrite: bool = False,
) -> Callable[[Callable], Callable]:
    """
    Decorator to register a function as a custom visualization panel.
    """

    def decorator(func: Callable) -> Callable:
        validated_id = validate_panel_id(panel_id)  # Validate ID on registration

        if validated_id in _registered_viz_panels and not overwrite:
            raise ValueError(
                f"Visualization panel ID '{validated_id}' already registered. Use overwrite=True to replace."
            )
        elif validated_id in _registered_viz_panels:
            logger.warning(
                f"Visualization panel ID '{validated_id}' already registered. Overwriting."
            )

        final_description = description or (func.__doc__ or "").strip()

        _registered_viz_panels[validated_id] = {
            "title": title,
            "description": final_description,
            "plot_type": plot_type,
            "render_function": func,
            "roles": roles if roles is not None else [],
            "export_supported": export_supported,
            "export_args": export_args or (),
            "export_kwargs": export_kwargs or {},
        }
        logger.info(
            f"Registered visualization panel: {title} (ID: {validated_id}, Type: {plot_type})"
        )

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return func(*args, **kwargs)

        return wrapper

    return decorator


def unregister_viz_panel(panel_id: str) -> None:
    """Unregisters a visualization panel by ID."""
    if panel_id in _registered_viz_panels:
        del _registered_viz_panels[panel_id]
        logger.info(f"Unregistered visualization panel: {panel_id}")
    else:
        logger.warning(f"Cannot unregister non-existent panel: {panel_id}")


def get_registered_viz_panels() -> Dict[str, Dict[str, Any]]:
    """Returns a dictionary of all registered visualization panels."""
    return _registered_viz_panels


def list_panels_metadata() -> List[Dict[str, Any]]:
    """Return all registered panel metadata for UI or docs."""
    return [
        {k: v for k, v in panel.items() if k != "render_function"}
        for panel in _registered_viz_panels.values()
    ]


def get_panels_for_role(role: str) -> List[Dict[str, Any]]:
    """Filters panels by user role."""
    return [
        panel
        for panel in list_panels_metadata()
        if not panel.get("roles") or role in panel["roles"]
    ]


# --- End Custom Plot Registration ---


def pre_plot_hook(plot_type: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Hook called before a plot is generated. Plugins can modify `data`.
    """
    for hook in _pre_plot_hooks:
        data = hook(plot_type, data)
    return data


def post_plot_hook(
    plot_type: str, plot_object: Any, metadata: Dict[str, Any]
) -> Tuple[Any, Dict[str, Any]]:
    """
    Hook called after a plot is generated. Plugins can modify the plot object
    (e.g., add annotations) or add to metadata.
    """
    for hook in _post_plot_hooks:
        plot_object, metadata = hook(plot_type, plot_object, metadata)
    return plot_object, metadata


def _scrub_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively scrubs sensitive data from plot metadata."""
    if not DETECT_SECRETS_AVAILABLE:
        return metadata
    return _scrub_secrets(metadata)  # Assuming _scrub_secrets is defined in utils.py or similar


def _scrub_secrets(data: Union[Dict, List, str]) -> Union[Dict, List, str]:
    """Helper for scrubbing, copied here to avoid circular dependency if utils.py imports viz.py"""
    if not DETECT_SECRETS_AVAILABLE:
        return data
    if isinstance(data, str):
        secrets = SecretsCollection()
        with transient_settings():
            secrets.scan_string(data)
        for secret in secrets:
            data = data.replace(secret.secret_value, "[REDACTED]")
        return data
    if isinstance(data, dict):
        return {k: _scrub_secrets(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_scrub_secrets(item) for item in data]
    return data


@register_viz_panel(
    panel_id="flakiness_trend",
    title="Test Run Pass/Fail Trend",
    description="Shows the pass/fail trend of test runs.",
    plot_type="matplotlib",
    roles=None,
    export_supported=True,
    export_args=([], "default_test.py"),
)
def plot_flakiness_trend(runs: List[Dict[str, Any]], test_file_name: str) -> Optional[Any]:
    """
    Generates and saves a plot showing the pass/fail trend of test runs.
    Returns the figure object, or None if matplotlib is not available.
    """
    if not MATPLOTLIB_AVAILABLE:
        logger.warning("Matplotlib is not available. Flakiness plot will not be generated.")
        if PROMETHEUS_AVAILABLE:
            PLOT_GENERATIONS.labels(plot_type="flakiness", status="skipped_no_lib").inc()
        return None

    try:
        results = [r["returncode"] == 0 for r in runs]
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.plot(
            [i + 1 for i in range(len(results))],
            results,
            marker="o",
            linestyle="-",
            color="blue",
        )
        ax.set_title(f"Test Run Pass/Fail Trend for {os.path.basename(test_file_name)}")
        ax.set_xlabel("Run Number")
        ax.set_ylabel("Result (1=Pass, 0=Fail)")
        ax.set_yticks([0, 1])
        ax.set_yticklabels(["Fail", "Pass"])
        ax.grid(True, linestyle="--", alpha=0.7)
        fig.tight_layout()
        if PROMETHEUS_AVAILABLE:
            PLOT_GENERATIONS.labels(plot_type="flakiness", status="success").inc()
        return fig
    except Exception as e:
        logger.error(f"Matplotlib error generating flakiness plot: {e}", exc_info=True)
        if PROMETHEUS_AVAILABLE:
            PLOT_GENERATIONS.labels(plot_type="flakiness", status="error").inc()
        return None


@register_viz_panel(
    panel_id="coverage_history",
    title="Coverage History",
    description="Plots the history of code coverage values.",
    plot_type="matplotlib",
    roles=None,
    export_supported=True,
    export_args=([], "Coverage"),
)
def plot_coverage_history(coverage_data: List[float], label: str = "Coverage") -> Optional[Any]:
    """
    Plots the history of code coverage values.
    `coverage_data` should be a list of coverage percentages (e.g., 0.85 for 85%).
    """
    if not MATPLOTLIB_AVAILABLE:
        logger.warning("Matplotlib is not available. Coverage history plot will not be generated.")
        if PROMETHEUS_AVAILABLE:
            PLOT_GENERATIONS.labels(plot_type="coverage", status="skipped_no_lib").inc()
        return None

    if not coverage_data:
        logger.info("No coverage data to plot.")
        if PROMETHEUS_AVAILABLE:
            PLOT_GENERATIONS.labels(plot_type="coverage", status="no_data").inc()
        return None

    try:
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.plot(
            [i + 1 for i in range(len(coverage_data))],
            [c * 100 for c in coverage_data],
            marker="o",
            linestyle="-",
            color="green",
        )
        ax.set_title(f"{label} History")
        ax.set_xlabel("Simulation Run")
        ax.set_ylabel("Coverage (%)")
        ax.set_ylim(0, 100)
        ax.grid(True, linestyle="--", alpha=0.7)
        fig.tight_layout()
        if PROMETHEUS_AVAILABLE:
            PLOT_GENERATIONS.labels(plot_type="coverage", status="success").inc()
        return fig
    except Exception as e:
        logger.error(f"Matplotlib error generating coverage plot: {e}", exc_info=True)
        if PROMETHEUS_AVAILABLE:
            PLOT_GENERATIONS.labels(plot_type="coverage", status="error").inc()
        return None


@register_viz_panel(
    panel_id="metric_trend",
    title="Metric Trend",
    description="Plots a generic metric trend over simulation runs.",
    plot_type="matplotlib",
    roles=None,
    export_supported=True,
    export_args=([], "Metric", "", ""),
)
def plot_metric_trend(
    metrics: List[float], metric_name: str, unit: str = "", filename_suffix: str = ""
) -> Optional[Any]:
    """
    Plots a generic metric trend over simulation runs.
    """
    if not MATPLOTLIB_AVAILABLE:
        logger.warning("Matplotlib is not available. Metric trend plot will not be generated.")
        if PROMETHEUS_AVAILABLE:
            PLOT_GENERATIONS.labels(plot_type="metric_trend", status="skipped_no_lib").inc()
        return None

    if not metrics:
        logger.info(f"No data to plot for {metric_name}.")
        if PROMETHEUS_AVAILABLE:
            PLOT_GENERATIONS.labels(plot_type="metric_trend", status="no_data").inc()
        return None

    try:
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.plot(
            [i + 1 for i in range(len(metrics))],
            metrics,
            marker="o",
            linestyle="-",
            color="purple",
        )
        ax.set_title(f"{metric_name} Trend Over Runs")
        ax.set_xlabel("Simulation Run")
        ax.set_ylabel(f"{metric_name} ({unit})")
        ax.grid(True, linestyle="--", alpha=0.7)
        fig.tight_layout()
        if PROMETHEUS_AVAILABLE:
            PLOT_GENERATIONS.labels(plot_type="metric_trend", status="success").inc()
        return fig
    except Exception as e:
        logger.error(f"Matplotlib error generating metric trend plot: {e}", exc_info=True)
        if PROMETHEUS_AVAILABLE:
            PLOT_GENERATIONS.labels(plot_type="metric_trend", status="error").inc()
        return None


@register_viz_panel(
    panel_id="example_plugin_custom_metric_trend",
    title="Plugin Custom Metric Trend (Plotly)",
    description="Shows a dummy custom metric trend generated by an example plugin using Plotly.",
    plot_type="plotly",
    roles=["admin", "developer"],
    export_supported=True,
    export_args=([], {}),
)
def render_example_plugin_custom_metric_trend(
    st_dash_obj: Any, current_result: Dict[str, Any]
) -> Optional[Any]:
    """
    Renders a custom metric trend using Plotly, as if provided by a plugin.
    This function will be called by the dashboard.
    """
    data = pre_plot_hook("plotly", current_result)
    if not PLOTLY_AVAILABLE:
        st_dash_obj.warning("Plotly is not available. This plugin panel requires Plotly.")
        if PROMETHEUS_AVAILABLE:
            PLOT_GENERATIONS.labels(plot_type="plugin_custom", status="skipped_no_lib").inc()
        return None

    st_dash_obj.markdown("This is a custom metric panel from a plugin!")

    custom_metrics = data.get("plugin_runs", [])
    audit_data_points = []
    for p_run in custom_metrics:
        if (
            p_run.get("plugin_name") == "ExampleChaosSecurityPlugin"
            and p_run.get("function") == "perform_custom_security_audit"
            and p_run.get("result", {}).get("status") == "FINDINGS_DETECTED"
        ):
            audit_data_points.append(len(p_run["result"].get("findings", [])))
        else:
            audit_data_points.append(0)

    if not audit_data_points:
        st_dash_obj.info(
            "No relevant audit data from ExampleChaosSecurityPlugin found in this run."
        )
        if PROMETHEUS_AVAILABLE:
            PLOT_GENERATIONS.labels(plot_type="plugin_custom", status="no_data").inc()
        return None

    try:
        fig = go.Figure(
            data=[
                go.Scatter(
                    y=audit_data_points,
                    mode="lines+markers",
                    name="Audit Findings Count",
                )
            ]
        )
        fig.update_layout(
            title="Simulated Security Audit Findings Trend",
            xaxis_title="Run Index",
            yaxis_title="Number of Findings",
        )

        st_dash_obj.plotly_chart(fig, use_container_width=True)

        fig, metadata = post_plot_hook("plotly", fig, {"exported": False})

        if st_dash_obj.button("Export Plotly Plot as HTML", key="export_plotly_plot"):
            export_html_path = RESULTS_DIR / "plugin_plotly_plot.html"
            fig.write_html(export_html_path)
            st_dash_obj.success(f"Plot exported to {export_html_path}")
            if PROMETHEUS_AVAILABLE:
                PLOT_EXPORTS.labels(format="html", status="success").inc()
        if PROMETHEUS_AVAILABLE:
            PLOT_GENERATIONS.labels(plot_type="plugin_custom", status="success").inc()
        return fig
    except Exception as e:
        logger.error(f"Plotly error generating plugin custom plot: {e}", exc_info=True)
        if PROMETHEUS_AVAILABLE:
            PLOT_GENERATIONS.labels(plot_type="plugin_custom", status="error").inc()
        return None


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=1, max=5),
    retry=retry_if_exception_type(Exception) if TENACITY_AVAILABLE else lambda x: False,
)
async def _get_cached_plot_data(cache_key: str) -> Optional[Dict[str, Any]]:
    if not REDIS_AVAILABLE or not CONFIG.redis_cache_url:
        return None

    redis = None
    try:
        redis = Redis.from_url(CONFIG.redis_cache_url)
        cached_data = await redis.get(cache_key)
        if cached_data:
            logger.debug(f"Cache hit for plot data: {cache_key}")
            if PROMETHEUS_AVAILABLE:
                PLOT_CACHE_HITS.inc()
            return json.loads(cached_data)
    except Exception as e:
        logger.error(f"Failed to retrieve from Redis cache: {e}", exc_info=True)
    finally:
        if redis:
            await redis.close()

    if PROMETHEUS_AVAILABLE:
        PLOT_CACHE_MISSES.inc()
    return None


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=1, max=5),
    retry=retry_if_exception_type(Exception) if TENACITY_AVAILABLE else lambda x: False,
)
async def _cache_plot_data(cache_key: str, data: Dict[str, Any]):
    if not REDIS_AVAILABLE or not CONFIG.redis_cache_url:
        return

    redis = None
    try:
        redis = Redis.from_url(CONFIG.redis_cache_url)
        await redis.set(cache_key, json.dumps(data), ex=CONFIG.redis_cache_ttl)
        logger.debug(f"Cached plot data for: {cache_key}")
    except Exception as e:
        logger.error(f"Failed to set Redis cache: {e}", exc_info=True)
    finally:
        if redis:
            await redis.close()


# --- Batch Export API ---
async def batch_export_panels(
    panel_ids: Optional[List[str]] = None, format: Optional[str] = None
) -> Dict[str, Optional[str]]:
    """
    Exports all supported panels to files in the specified format.
    Returns a dict of panel_id to exported file path (or None if export failed/unsupported).
    """
    exports = {}
    panels = get_registered_viz_panels()
    target_ids = panel_ids or list(panels.keys())
    export_format = format or CONFIG.default_plot_format

    for pid in target_ids:
        panel = panels.get(pid)
        if not panel or not panel["export_supported"]:
            exports[pid] = None
            if PROMETHEUS_AVAILABLE:
                PLOT_EXPORTS.labels(format=export_format, status="unsupported").inc()
            continue

        try:
            args = panel.get("export_args", ())
            kwargs = panel.get("export_kwargs", {})

            # Generate a cache key for the plot data with better collision resistance
            # Include timestamp for dynamic data and type information
            cache_data = {
                "panel_id": pid,
                "plot_type": panel["plot_type"],
                "args": args,
                "kwargs": kwargs,
                # Add type information for better uniqueness
                "arg_types": [type(arg).__name__ for arg in args] if args else [],
                "kwarg_types": ({k: type(v).__name__ for k, v in kwargs.items()} if kwargs else {}),
            }
            try:
                # Use repr() for better object representation than default=str
                plot_data_json = json.dumps(cache_data, sort_keys=True, default=repr)
            except (TypeError, ValueError):
                # Fallback to str if repr fails
                plot_data_json = json.dumps(cache_data, sort_keys=True, default=str)

            plot_data_hash = hashlib.sha256(plot_data_json.encode()).hexdigest()
            cache_key = f"plot_data:{plot_data_hash}"

            # Try to get plot data from cache
            plot_data_from_cache = await _get_cached_plot_data(cache_key)
            if plot_data_from_cache:
                # Reconstruct figure from cached data if possible (e.g., Plotly JSON)
                if (
                    panel["plot_type"] == "plotly"
                    and "data" in plot_data_from_cache
                    and "layout" in plot_data_from_cache
                ):
                    fig = go.Figure(
                        data=plot_data_from_cache["data"],
                        layout=plot_data_from_cache["layout"],
                    )
                elif panel["plot_type"] == "matplotlib":
                    logger.debug(
                        f"Matplotlib plot {pid} not directly reconstructible from JSON cache. Re-rendering."
                    )
                    fig = panel["render_function"](*args, **kwargs)
                else:
                    fig = panel["render_function"](*args, **kwargs)  # Fallback to re-render
            else:
                # Render the figure if not in cache
                fig = panel["render_function"](*args, **kwargs)
                # Cache the plot data if it's a Plotly figure (serializable)
                if panel["plot_type"] == "plotly" and fig:
                    await _cache_plot_data(cache_key, fig.to_dict())

            if fig is None:
                exports[pid] = None
                if PROMETHEUS_AVAILABLE:
                    PLOT_EXPORTS.labels(format=export_format, status="render_failed").inc()
                continue

            unique_id = str(uuid.uuid4())[:8]
            timestamp = int(time.time())
            export_path = RESULTS_DIR / f"{pid}_{timestamp}_{unique_id}.{export_format}"

            if panel["plot_type"] == "matplotlib":
                fig.savefig(export_path)
                plt.close(fig)  # Close figure to free memory
            elif panel["plot_type"] == "plotly":
                if not PLOTLY_EXPORT_AVAILABLE:
                    logger.error("Plotly image export not available (missing kaleido or orca).")
                    exports[pid] = None
                    if PROMETHEUS_AVAILABLE:
                        PLOT_EXPORTS.labels(
                            format=export_format, status="plotly_export_lib_missing"
                        ).inc()
                    continue
                if export_format == "html":
                    fig.write_html(export_path)
                else:
                    fig.write_image(export_path, format=export_format)
            else:
                # Fallback for other types if they have a save/export method
                try:
                    if hasattr(fig, "savefig"):
                        fig.savefig(export_path)
                    elif hasattr(fig, "save"):
                        fig.save(export_path)
                    else:
                        raise AttributeError("Plot object has no recognized save/export method.")
                except AttributeError:
                    logger.warning(
                        f"Export not supported for {panel['plot_type']} to format {export_format}."
                    )
                    exports[pid] = None
                    if PROMETHEUS_AVAILABLE:
                        PLOT_EXPORTS.labels(
                            format=export_format, status="unsupported_type_format"
                        ).inc()
                    continue
            exports[pid] = str(export_path)
            if PROMETHEUS_AVAILABLE:
                PLOT_EXPORTS.labels(format=export_format, status="success").inc()
        except Exception as e:
            logger.error(f"Failed to export {pid} to {export_format}: {e}", exc_info=True)
            exports[pid] = None
            if PROMETHEUS_AVAILABLE:
                PLOT_EXPORTS.labels(format=export_format, status="error").inc()
    return exports


# Minimum interface for dashboard objects (for robustness)
class DashboardInterface:
    def markdown(self, text: str) -> None:
        pass

    def warning(self, text: str) -> None:
        pass

    def plotly_chart(self, fig: Any, **kwargs) -> None:
        pass


# Example usage: Check if st_dash_obj conforms, else fallback
def check_dashboard_interface(dash_obj: Any) -> bool:
    required_methods = ["markdown", "warning", "plotly_chart"]
    missing = [method for method in required_methods if not hasattr(dash_obj, method)]
    if missing:
        logger.error(f"Dashboard object missing methods: {', '.join(missing)}")
        return False
    return True


if __name__ == "__main__":
    # Demo for viz.py
    logger.setLevel(logging.DEBUG)  # Show debug logs for demo

    print("\n--- Running Viz Module Demo ---")

    # Example: Plot Flakiness Trend
    print("\nGenerating Flakiness Trend Plot...")
    sample_runs = [
        {"run": 1, "returncode": 0, "metrics": {"duration_seconds": 1.2}},
        {"run": 2, "returncode": 1, "metrics": {"duration_seconds": 1.5}},
        {"run": 3, "returncode": 0, "metrics": {"duration_seconds": 1.1}},
        {"run": 4, "returncode": 0, "metrics": {"duration_seconds": 1.3}},
        {"run": 5, "returncode": 1, "metrics": {"duration_seconds": 1.8}},
    ]
    flakiness_fig = plot_flakiness_trend(sample_runs, "my_test_suite.py")
    if flakiness_fig:
        flakiness_output_path = RESULTS_DIR / "flakiness_trend_demo.png"
        flakiness_fig.savefig(flakiness_output_path)
        print(f"Flakiness trend plot saved to: {flakiness_output_path}")
        plt.close(flakiness_fig)  # Close figure to free memory
    else:
        print("Flakiness trend plot not generated.")

    # Example: Plot Coverage History
    print("\nGenerating Coverage History Plot...")
    sample_coverage_data = [0.75, 0.78, 0.80, 0.82, 0.85]
    coverage_fig = plot_coverage_history(sample_coverage_data, "Backend Service Coverage")
    if coverage_fig:
        coverage_output_path = RESULTS_DIR / "coverage_history_demo.png"
        coverage_fig.savefig(coverage_output_path)
        print(f"Coverage history plot saved to: {coverage_output_path}")
        plt.close(coverage_fig)
    else:
        print("Coverage history plot not generated.")

    # Example: Plot Metric Trend
    print("\nGenerating Metric Trend Plot (Latency)...")
    sample_latency_data = [120.5, 115.2, 130.1, 110.8, 125.0]
    latency_fig = plot_metric_trend(sample_latency_data, "API Latency", "ms")
    if latency_fig:
        latency_output_path = RESULTS_DIR / "api_latency_trend_demo.png"
        latency_fig.savefig(latency_output_path)
        print(f"API Latency trend plot saved to: {latency_output_path}")
        plt.close(latency_fig)
    else:
        print("API Latency trend plot not generated.")

    # Example: Batch Export
    print("\nRunning Batch Export of Panels...")

    # This will call the registered functions. For `render_example_plugin_custom_metric_trend`,
    # it expects a `st_dash_obj` and `current_result`. We'll mock these.
    class MockDashboardObject:
        def markdown(self, text: str):
            print(f"Mock Markdown: {text}")

        def warning(self, text: str):
            print(f"Mock Warning: {text}")

        def plotly_chart(self, fig: Any, **kwargs):
            print(
                f"Mock Plotly Chart Rendered: {fig.layout.title.text if hasattr(fig, 'layout') else 'Unknown'}"
            )

        def info(self, text: str):
            print(f"Mock Info: {text}")

        def button(self, label: str, key: str):
            return True  # Simulate button click for export

        def success(self, text: str):
            print(f"Mock Success: {text}")

    mock_dash_obj = MockDashboardObject()
    mock_current_result = {
        "plugin_runs": [
            {
                "plugin_name": "ExampleChaosSecurityPlugin",
                "function": "perform_custom_security_audit",
                "result": {
                    "status": "FINDINGS_DETECTED",
                    "findings": ["vuln1", "vuln2"],
                },
            },
            {
                "plugin_name": "OtherPlugin",
                "function": "do_something",
                "result": {"status": "OK"},
            },
            {
                "plugin_name": "ExampleChaosSecurityPlugin",
                "function": "perform_custom_security_audit",
                "result": {"status": "FINDINGS_DETECTED", "findings": ["vuln3"]},
            },
        ]
    }
    # Manually call the render function for the plotly panel to simulate it being part of a dashboard
    # This populates its internal state if it needs to for export.
    render_example_plugin_custom_metric_trend(mock_dash_obj, mock_current_result)

    # Now, run batch export for all registered panels
    async def run_batch_export_demo():
        exported_files = await batch_export_panels(format="png")
        print("\nBatch Export Results (PNG):")
        for panel_id, file_path in exported_files.items():
            if file_path:
                print(f"  - {panel_id}: {file_path}")
            else:
                print(f"  - {panel_id}: Export skipped or failed.")

        # Test HTML export for plotly panel
        exported_html_files = await batch_export_panels(
            panel_ids=["example_plugin_custom_metric_trend"], format="html"
        )
        print("\nBatch Export Results (HTML for Plotly):")
        for panel_id, file_path in exported_html_files.items():
            if file_path:
                print(f"  - {panel_id}: {file_path}")
            else:
                print(f"  - {panel_id}: Export skipped or failed.")

    asyncio.run(run_batch_export_demo())

    print("\n--- Viz Module Demo Complete ---")
