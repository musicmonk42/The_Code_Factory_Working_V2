import os
import sys
import streamlit as st
import networkx as nx
import logging
import json
import re
import time
from typing import Dict, List, Any, Optional, Callable, Union, Tuple
from enum import Enum
from pathlib import Path

# --- Conditional Imports for Plotting Libraries ---
try:
    import plotly.graph_objects as go
    import plotly.io as pio

    PLOTLY_AVAILABLE = True
except ImportError:
    PLOTLY_AVAILABLE = False
    go = pio = None

try:
    import matplotlib.pyplot as plt

    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False
    plt = None

# --- Conditional Imports for Enhancements ---
try:
    from pydantic import BaseModel, Field, ValidationError

    PYDANTIC_AVAILABLE = True
except ImportError:
    PYDANTIC_AVAILABLE = False

    class BaseModel:
        def __init__(self, **data: Any):
            self.__dict__.update(data)

        def dict(self, *args, **kwargs):
            return self.__dict__

    class Field:
        def __new__(cls, default=None, **kwargs):
            return default

    ValidationError = Exception

try:
    from tenacity import retry, stop_after_attempt, wait_exponential

    TENACITY_AVAILABLE = True
except ImportError:
    TENACITY_AVAILABLE = False

    def retry(*args, **kwargs):
        return lambda f: f

    def stop_after_attempt(n):
        return None

    def wait_exponential(*args, **kwargs):
        return None


try:
    from prometheus_client import Counter, Histogram, REGISTRY

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
viz_logger = logging.getLogger(__name__)
viz_logger.setLevel(logging.INFO)
if not viz_logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        '{"time": "%(asctime)s", "name": "%(name)s", "level": "%(levelname)s", "message": "%(message)s"}'
    )
    handler.setFormatter(formatter)
    viz_logger.addHandler(handler)

# --- Metrics ---
if PROMETHEUS_AVAILABLE:
    VIZ_RENDER_TOTAL = _get_or_create_metric(
        Counter, "workflow_viz_render_total", "Total visualization renders", ["backend"]
    )
    VIZ_EXPORT_TOTAL = _get_or_create_metric(
        Counter,
        "workflow_viz_export_total",
        "Total visualization exports",
        ["format", "status"],
    )
    VIZ_RENDER_LATENCY = _get_or_create_metric(
        Histogram,
        "workflow_viz_render_latency_seconds",
        "Visualization render latency",
        ["backend"],
    )
    VIZ_RENDER_ERRORS = _get_or_create_metric(
        Counter,
        "workflow_viz_render_errors_total",
        "Total errors during rendering",
        ["backend", "error_type"],
    )

# --- Pydantic Config Model ---
if PYDANTIC_AVAILABLE:

    class WorkflowVizConfig(BaseModel):
        results_dir: str = Field(default="./simulation_results")
        default_backend: str = Field(
            default="streamlit", pattern="^(streamlit|dash|jupyter)$"
        )
        colorblind_palette: List[str] = Field(
            default=[
                "#0072B2",
                "#D55E00",
                "#CC79A7",
                "#F0E442",
                "#009E73",
                "#56B4E9",
                "#E69F00",
                "#000000",
                "#FFFFFF",
            ]
        )
        plotly_preferred: bool = Field(default=True)
        graph_layout: str = Field(default="dot", pattern="^(dot|spring)$")

else:

    class WorkflowVizConfig:
        def __init__(self):
            self.results_dir = "./simulation_results"
            self.default_backend = "streamlit"
            self.colorblind_palette = [
                "#0072B2",
                "#D55E00",
                "#CC79A7",
                "#F0E442",
                "#009E73",
                "#56B4E9",
                "#E69F00",
                "#000000",
                "#FFFFFF",
            ]
            self.plotly_preferred = True
            self.graph_layout = "dot"


# --- Load Config from File or Env ---
CONFIG_FILE = Path(__file__).parent / "configs/workflow_viz_config.json"
DEFAULT_CONFIG = WorkflowVizConfig()
CONFIG = DEFAULT_CONFIG
if CONFIG_FILE.exists():
    try:
        with open(CONFIG_FILE, "r") as f:
            file_config = json.load(f)
        env_overrides = {
            k.lower(): os.getenv(f"VIZ_{k.upper()}")
            for k in file_config.keys()
            if os.getenv(f"VIZ_{k.upper()}") is not None
        }
        if PYDANTIC_AVAILABLE:
            CONFIG = WorkflowVizConfig.parse_obj({**file_config, **env_overrides})
        else:
            CONFIG = {**file_config, **env_overrides}
    except (IOError, json.JSONDecodeError, ValidationError) as e:
        viz_logger.warning(
            f"Failed to load or validate workflow_viz_config.json: {e}. Using defaults."
        )

# Set up results directory from config
RESULTS_DIR = Path(CONFIG.results_dir)
os.makedirs(RESULTS_DIR, exist_ok=True)


class DashboardAPI:
    def __init__(self, backend: str = "streamlit"):
        self.backend = backend

    def markdown(self, text: str) -> None:
        if self.backend == "streamlit":
            st.markdown(text)

    def warning(self, text: str) -> None:
        if self.backend == "streamlit":
            st.warning(text)

    def plotly_chart(self, fig: Any, use_container_width: bool = True) -> None:
        if self.backend == "streamlit":
            st.plotly_chart(fig, use_container_width=use_container_width)

    def expander(self, label: str, expanded: bool = False) -> Any:
        if self.backend == "streamlit":
            return st.expander(label, expanded=expanded)

    def subheader(self, text: str) -> None:
        if self.backend == "streamlit":
            st.subheader(text)

    def info(self, text: str) -> None:
        if self.backend == "streamlit":
            st.info(text)

    def pyplot(self, fig: Any) -> None:
        if self.backend == "streamlit":
            st.pyplot(fig)

    def caption(self, text: str) -> None:
        if self.backend == "streamlit":
            st.caption(text)

    def button(self, label: str, key: Optional[str] = None) -> bool:
        if self.backend == "streamlit":
            return st.button(label, key=key)

    def download_button(
        self, label: str, data: bytes, file_name: str, mime: str
    ) -> None:
        if self.backend == "streamlit":
            st.download_button(label, data, file_name, mime)


class WorkflowPhase(Enum):
    LOAD_SPEC = ("Load Spec", "#B8B8FF")
    PLAN_TESTS = ("Plan Tests", "#B8B8FF")
    GENERATE_CODE = ("Generate Code", "#B8B8FF")
    SECURITY_TESTS = ("Security Tests", "#F7CAC9")
    PERFORMANCE_SCRIPT = ("Performance Script", "#FFDAC1")
    JUDGE_REVIEW = ("Judge Review", "#D5E1DD")
    REFINE = ("Refine", "#92A8D1")
    EXECUTE_TESTS = ("Execute Tests", "#B5EAD7")
    OUTPUT_RESULTS = ("Output Results", "#FFFACD")

    @property
    def label(self) -> str:
        return self.value[0]

    @property
    def color(self) -> str:
        return self.value[1]


def detect_dashboard_backend() -> str:
    if "streamlit" in sys.modules:
        return "streamlit"
    return "unknown"


def validate_custom_phases(phases: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    for label, color in phases:
        if not re.match(r"^[a-zA-Z0-9_ -]+$", label):
            raise ValueError(f"Invalid phase label: {label}")
        if not re.match(r"^#[0-9A-Fa-f]{6}$", color):
            raise ValueError(f"Invalid color code: {color}")
    return phases


def validate_export_path(path: Union[str, Path]) -> Path:
    path_obj = Path(path).resolve()
    results_dir_obj = RESULTS_DIR.resolve()
    # is_relative_to() added in Python 3.9, use fallback for compatibility
    try:
        if not path_obj.is_relative_to(results_dir_obj):
            raise ValueError("Export path must be within results directory")
    except AttributeError:
        # Python < 3.9 fallback
        try:
            path_obj.relative_to(results_dir_obj)
        except ValueError:
            raise ValueError("Export path must be within results directory")
    return path_obj


def _scrub_secrets(data: Union[Dict, List, str]) -> Union[Dict, List, str]:
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


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=5))
def get_graphviz_layout(edges):
    return nx.nx_agraph.graphviz_layout(nx.DiGraph(edges), prog=CONFIG.graph_layout)


def render_workflow_viz(
    result: Dict[str, Any],
    prefer_plotly: Optional[bool] = None,
    summary_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    dashboard_api: Optional[DashboardAPI] = None,
    custom_phases: Optional[List[Tuple[str, str]]] = None,
    colorblind_mode: bool = False,
) -> Optional[Any]:
    start_time = time.monotonic()

    if dashboard_api is None:
        dashboard_api = DashboardAPI(backend=CONFIG.default_backend)

    if prefer_plotly is None:
        prefer_plotly = CONFIG.plotly_preferred

    if not result:
        dashboard_api.warning("No result data available to visualize.")
        if PROMETHEUS_AVAILABLE:
            VIZ_RENDER_TOTAL.labels(backend=dashboard_api.backend).inc()
            VIZ_RENDER_ERRORS.labels(
                backend=dashboard_api.backend, error_type="no_data"
            ).inc()
        return None

    scrubbed_result = _scrub_secrets(result)

    findings = scrubbed_result.get("findings", [])
    actions = scrubbed_result.get("actions", [])
    status = scrubbed_result.get("status", "UNKNOWN")
    scores = scrubbed_result.get("review", {}).get("scores", {})
    coverage = scores.get("coverage", "N/A")

    base_phases = [(phase.label, phase.color) for phase in WorkflowPhase]
    phases = base_phases[:]
    if custom_phases:
        try:
            phases.extend(validate_custom_phases(custom_phases))
        except ValueError as e:
            dashboard_api.warning(
                f"Invalid custom phases: {e}. Skipping custom phases."
            )
            viz_logger.error(f"Invalid custom phases: {e}", exc_info=True)

    if colorblind_mode:
        colorblind_palette = CONFIG.colorblind_palette
        color_map = colorblind_palette[: len(phases)]
    else:
        color_map = [color for label, color in phases]

    nodes, edges = [], []
    for i in range(len(phases) - 1):
        nodes.append(phases[i][0])
        edges.append((phases[i][0], phases[i + 1][0]))
    nodes.append(phases[-1][0])

    node_labels = {}
    for label, color in phases:
        if "Security" in label:
            node_labels[label] = f"Security Tests (Findings: {len(findings)})"
        elif "Refine" in label:
            node_labels[label] = f"Refine (Actions: {len(actions)})"
        elif "Execute" in label:
            node_labels[label] = f"Execute Tests ({status})"
        elif "Output" in label:
            node_labels[label] = f"Output Results (Coverage: {coverage})"
        else:
            node_labels[label] = label

    fig = None

    # Fallback to text-based output if no libraries are available
    if not PLOTLY_AVAILABLE and not MATPLOTLIB_AVAILABLE:
        dashboard_api.warning(
            "No visualization libraries available (Plotly, Matplotlib). Displaying text summary."
        )
        dashboard_api.markdown("\n".join(f"- {label}" for label, _ in phases))
        return None

    if prefer_plotly and PLOTLY_AVAILABLE:
        try:
            G = nx.DiGraph()
            G.add_nodes_from(nodes)
            G.add_edges_from(edges)

            try:
                pos = get_graphviz_layout(edges)
            except Exception:
                viz_logger.warning(
                    "Graphviz layout failed, falling back to spring layout."
                )
                pos = nx.spring_layout(G, seed=42)

            node_x, node_y, hover_texts = [], [], []
            for n in nodes:
                x, y = pos[n]
                node_x.append(x)
                node_y.append(-y)
                hover_texts.append(node_labels.get(n, n))

            edge_x, edge_y = [], []
            for e in edges:
                x0, y0 = pos[e[0]]
                x1, y1 = pos[e[1]]
                edge_x += [x0, x1, None]
                edge_y += [-y0, -y1, None]

            edge_trace = go.Scatter(
                x=edge_x,
                y=edge_y,
                line=dict(width=2, color="#888"),
                hoverinfo="none",
                mode="lines",
            )
            node_trace = go.Scatter(
                x=node_x,
                y=node_y,
                mode="markers+text",
                text=[node_labels[n] for n in nodes],
                textposition="bottom center",
                marker=dict(color=color_map, size=60, line=dict(width=2, color="#222")),
                hoverinfo="text",
                hovertext=hover_texts,
            )

            fig = go.Figure(data=[edge_trace, node_trace])
            fig.update_layout(
                showlegend=False,
                margin=dict(l=10, r=10, t=30, b=10),
                xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
                yaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
                plot_bgcolor="#fff",
                title="Workflow Flowchart (Interactive)",
            )

            dashboard_api.plotly_chart(fig, use_container_width=True)

            if summary_callback:
                summary_callback(scrubbed_result)
            else:
                _default_summary_and_details(scrubbed_result, dashboard_api)

            dashboard_api.caption(
                "This interactive diagram shows the workflow of the self-fixing engine from specification to output, highlighting phases and counts of findings and actions. All colors and labels are chosen for clarity and accessibility."
            )

            if dashboard_api.button("Export Plotly as PNG", key="export_plotly_png"):
                try:
                    import kaleido
                except ImportError:
                    dashboard_api.warning(
                        "Kaleido is not installed. Install with 'pip install kaleido' for PNG export."
                    )
                    if PROMETHEUS_AVAILABLE:
                        VIZ_EXPORT_TOTAL.labels(
                            format="png", status="failed_dependency"
                        ).inc()
                else:
                    export_png_path = validate_export_path(
                        RESULTS_DIR / f"workflow_plotly_{int(time.time())}.png"
                    )
                    fig.write_image(export_png_path, format="png")
                    dashboard_api.success(f"Plot exported to {export_png_path}")
                    if PROMETHEUS_AVAILABLE:
                        VIZ_EXPORT_TOTAL.labels(format="png", status="success").inc()

            if PROMETHEUS_AVAILABLE:
                VIZ_RENDER_TOTAL.labels(backend="plotly").inc()
                VIZ_RENDER_LATENCY.labels(backend="plotly").observe(
                    time.monotonic() - start_time
                )
            return fig

        except Exception as e:
            viz_logger.error(
                f"Plotly rendering failed: {e}. Falling back to Matplotlib.",
                exc_info=True,
            )
            if PROMETHEUS_AVAILABLE:
                VIZ_RENDER_TOTAL.labels(backend="plotly").inc()
                VIZ_RENDER_ERRORS.labels(
                    backend="plotly", error_type="runtime_error"
                ).inc()
            prefer_plotly = False  # Fallback

    if not prefer_plotly and MATPLOTLIB_AVAILABLE:
        try:
            try:
                pos = get_graphviz_layout(edges)
            except Exception as e:
                viz_logger.warning(
                    f"Advanced layout unavailable ({e}). Using fallback."
                )
                pos = nx.spring_layout(nx.DiGraph(edges))

            fig, ax = plt.subplots(figsize=(13, 7))
            nx.draw(
                nx.DiGraph(edges),
                pos,
                with_labels=True,
                labels=node_labels,
                node_color=color_map,
                node_size=3400,
                font_size=12,
                font_weight="bold",
                edge_color="#555",
                arrows=True,
                ax=ax,
            )
            nx.draw_networkx_edge_labels(
                nx.DiGraph(edges),
                pos,
                edge_labels={e: "" for e in edges},
                font_color="#333",
                font_size=9,
                ax=ax,
            )
            legend_labels = {label: color for label, color in phases}
            for i, (name, color) in enumerate(legend_labels.items()):
                ax.scatter([], [], c=color, label=name, s=180)
            ax.legend(
                loc="upper center",
                bbox_to_anchor=(0.5, 1.15),
                ncol=4,
                fontsize=10,
                title="Workflow Phases",
            )
            plt.tight_layout()
            dashboard_api.pyplot(fig)
            if summary_callback:
                summary_callback(scrubbed_result)
            else:
                _default_summary_and_details(scrubbed_result, dashboard_api)
            dashboard_api.caption(
                "This diagram shows the workflow of the self-fixing engine from specification to output, highlighting phases and counts of findings and actions. All colors and labels are chosen for clarity and accessibility."
            )
            if dashboard_api.button(
                "Export Matplotlib as PNG", key="export_matplotlib_png"
            ):
                export_png_path = validate_export_path(
                    RESULTS_DIR / f"workflow_matplotlib_{int(time.time())}.png"
                )
                fig.savefig(export_png_path, format="png")
                dashboard_api.success(f"Plot exported to {export_png_path}")
                if PROMETHEUS_AVAILABLE:
                    VIZ_EXPORT_TOTAL.labels(format="png", status="success").inc()

            if PROMETHEUS_AVAILABLE:
                VIZ_RENDER_TOTAL.labels(backend="matplotlib").inc()
                VIZ_RENDER_LATENCY.labels(backend="matplotlib").observe(
                    time.monotonic() - start_time
                )
            return fig
        except Exception as e:
            viz_logger.error(
                f"Matplotlib rendering failed: {e}. Displaying text summary as final fallback.",
                exc_info=True,
            )
            if PROMETHEUS_AVAILABLE:
                VIZ_RENDER_TOTAL.labels(backend="matplotlib").inc()
                VIZ_RENDER_ERRORS.labels(
                    backend="matplotlib", error_type="runtime_error"
                ).inc()
            dashboard_api.warning(
                "Matplotlib rendering failed. Displaying text summary."
            )
            dashboard_api.markdown("\n".join(f"- {label}" for label, _ in phases))
            return None

    return None


def _default_summary_and_details(
    result: Dict[str, Any], dashboard_api: DashboardAPI
) -> None:
    findings = result.get("findings", [])
    actions = result.get("actions", [])
    status = result.get("status", "UNKNOWN")
    scores = result.get("review", {}).get("scores", {})
    coverage = scores.get("coverage", "N/A")
    dashboard_api.markdown(
        f"""
    ### Workflow Summary
    - **Status:** `{status}`
    - **Coverage:** `{coverage}`
    - **Security Findings:** `{len(findings)}`
    - **Actions Taken:** `{len(actions)}`
    """
    )
    with dashboard_api.expander("🔎 Detailed Findings & Actions", expanded=False):
        dashboard_api.subheader("Findings")
        if findings:
            for i, finding in enumerate(findings, 1):
                dashboard_api.markdown(f"- **{i}.** {finding}")
        else:
            dashboard_api.info("No findings detected.")
        dashboard_api.subheader("Actions Taken")
        if actions:
            for i, action in enumerate(actions, 1):
                dashboard_api.markdown(f"- **{i}.** {action}")
        else:
            dashboard_api.info("No refinements needed.")


if __name__ == "__main__":

    def test_graph_building():
        dummy_result = {
            "findings": ["Finding 1", "Finding 2"],
            "actions": ["Action 1"],
            "status": "SUCCESS",
            "review": {"scores": {"coverage": 0.85}},
        }
        try:
            render_workflow_viz(dummy_result, prefer_plotly=False)
            print("Graph building test passed (no errors).")
        except Exception as e:
            print(f"Graph building test failed: {e}")
        custom_phases = [("Custom Phase", "#FFFFFF")]
        try:
            render_workflow_viz(
                dummy_result, prefer_plotly=False, custom_phases=custom_phases
            )
            print("Custom phases test passed (no errors).")
        except Exception as e:
            print(f"Custom phases test failed: {e}")
        phases = [(phase.label, phase.color) for phase in WorkflowPhase]
        assert len(phases) == 9, "Expected 9 phases"
        print("Nodes/edges count test passed.")
        try:
            render_workflow_viz(dummy_result, prefer_plotly=False, colorblind_mode=True)
            print("Colorblind mode test passed (no errors).")
        except Exception as e:
            print(f"Colorblind mode test failed: {e}")

    test_graph_building()
