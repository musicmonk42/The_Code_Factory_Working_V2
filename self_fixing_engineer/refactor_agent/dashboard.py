# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
dashboard.py

Streamlit dashboard for the Refactor Agent.

Features:
    - Agent status overview
    - Event log viewer
    - Metrics display

Run with:
    streamlit run self_fixing_engineer/refactor_agent/dashboard.py
"""

import logging
import os
import time

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG = os.path.join(os.path.dirname(__file__), "refactor_agent.yaml")

try:
    import streamlit as st  # type: ignore[import]

    _STREAMLIT_AVAILABLE = True
except ImportError:
    _STREAMLIT_AVAILABLE = False
    logger.warning(
        "Streamlit not installed; dashboard.py cannot render UI. "
        "Install with: pip install streamlit"
    )


def _get_crew_status():
    """Return current crew status as a dict (synchronous wrapper)."""
    import asyncio

    async def _inner():
        from self_fixing_engineer.agent_orchestration.crew_manager import CrewManager

        config_path = os.environ.get("REFACTOR_AGENT_CONFIG", _DEFAULT_CONFIG)
        if os.path.exists(config_path):
            crew = await CrewManager.from_config_yaml(config_path)
        else:
            crew = CrewManager()
        s = await crew.status()
        await crew.close()
        return s

    try:
        return asyncio.run(_inner())
    except Exception as exc:
        return {"error": str(exc)}


def render():
    """Render the Streamlit dashboard."""
    if not _STREAMLIT_AVAILABLE:
        print("Streamlit is not installed. Cannot render dashboard.")
        return

    st.set_page_config(
        page_title="Refactor Agent Dashboard",
        page_icon="🤖",
        layout="wide",
    )
    st.title("🤖 Refactor Agent Dashboard")
    st.caption("Real-time status of the Self-Fixing Engineer agent crew.")

    # Sidebar controls
    with st.sidebar:
        st.header("Controls")
        if st.button("🔄 Refresh"):
            st.rerun()
        st.write(f"Last refresh: {time.strftime('%H:%M:%S')}")

    # Fetch status
    with st.spinner("Loading crew status…"):
        status = _get_crew_status()

    if "error" in status:
        st.error(f"Failed to fetch status: {status['error']}")
        return

    # --- Agent Status Overview ---
    st.header("Agent Status Overview")
    agents = status.get("agents", {})
    if not agents:
        st.info("No agents loaded.")
    else:
        cols = st.columns(min(len(agents), 4))
        for idx, (name, info) in enumerate(agents.items()):
            with cols[idx % 4]:
                agent_status = info.get("status", "UNKNOWN")
                colour = "🟢" if agent_status == "RUNNING" else "🔴"
                st.metric(
                    label=f"{colour} {name}",
                    value=agent_status,
                    delta=None,
                )

    # --- Metrics ---
    st.header("Metrics")
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Total Agents", len(agents))
    with col2:
        running = sum(1 for a in agents.values() if a.get("status") == "RUNNING")
        st.metric("Running", running)
    with col3:
        failed = sum(1 for a in agents.values() if a.get("status") == "FAILED")
        st.metric("Failed", failed)

    # --- Raw Status JSON ---
    with st.expander("Raw Status JSON"):
        st.json(status)


if __name__ == "__main__":
    if _STREAMLIT_AVAILABLE:
        render()
    else:
        print(
            "Streamlit is not installed. Run: pip install streamlit\n"
            "Then: streamlit run self_fixing_engineer/refactor_agent/dashboard.py"
        )
