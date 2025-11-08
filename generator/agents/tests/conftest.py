# generator/agents/tests/conftest.py
import sys
import os
import types
from pathlib import Path

# --------------------------------------------------------------
# 1. ADD PROJECT ROOT TO PYTHONPATH (FIRST!)
# --------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[3]  # tests → generator
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# --------------------------------------------------------------
# 2. STUB OPENTELEMETRY **BEFORE** ANY IMPORT OF CODEGEN_AGENT
# --------------------------------------------------------------
def _stub_module(name: str, attrs: dict = None):
    attrs = attrs or {}
    mod = types.ModuleType(name)
    mod.__file__ = f"<stubbed {name}>"
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod

# --- STUB THE ENTIRE OPENTELEMETRY HIERARCHY ---
if 'opentelemetry' not in sys.modules:
    # Root
    otel = _stub_module('opentelemetry', {})
    # Trace
    trace = _stub_module('opentelemetry.trace', {})
    trace.get_tracer = lambda *a: None
    trace.get_current_span = lambda: None
    otel.trace = trace
    # SDK
    sdk = _stub_module('opentelemetry.sdk', {})
    resources = _stub_module('opentelemetry.sdk.resources', {})
    resources.Resource = lambda **kw: None
    export = _stub_module('opentelemetry.sdk.trace.export', {})
    export.ConsoleSpanExporter = lambda: None
    export.SimpleSpanProcessor = lambda e: None
    sdk.resources = resources
    sdk.trace = _stub_module('opentelemetry.sdk.trace', {})
    sdk.trace.export = export
    # Register
    sys.modules['opentelemetry'] = otel
    sys.modules['opentelemetry.trace'] = trace
    sys.modules['opentelemetry.sdk'] = sdk
    sys.modules['opentelemetry.sdk.resources'] = resources
    sys.modules['opentelemetry.sdk.trace.export'] = export

# --- STUB PROMETHEUS ---
if 'prometheus_client' not in sys.modules:
    prom = _stub_module('prometheus_client', {})
    class Dummy:
        def __init__(self, *a, **k): self._value = type('V', (), {'get': lambda: 0})()
        def labels(self, *a): return self
        def inc(self, *a): pass
        def set(self, v): pass
        def observe(self, v): pass
    prom.Counter = Dummy
    prom.Histogram = Dummy
    prom.Gauge = Dummy
    prom.REGISTRY = type('R', (), {'_collector_to_names': {}})()
    prom.generate_latest = lambda: b"# stub"
    prom.start_http_server = lambda p: None
    sys.modules['prometheus_client'] = prom

# --- STUB JAEGER ---
if 'opentelemetry.exporter.jaeger.thrift' not in sys.modules:
    sys.modules['opentelemetry.exporter.jaeger.thrift'] = _stub_module(
        'opentelemetry.exporter.jaeger.thrift', {'JaegerExporter': lambda **kw: None}
    )

# --------------------------------------------------------------
# 3. DISABLE WATCHERS
# --------------------------------------------------------------
os.environ["TESTING"] = "1"
print("TESTING=1 → watchers disabled")

# --------------------------------------------------------------
# 4. NOW IMPORT THE MODULE UNDER TEST (SAFE!)
# --------------------------------------------------------------
# This import will now succeed because opentelemetry is stubbed
from agents.codegen_agent.codegen_agent import (
    generate_code, hitl_review, CodeGenConfig, SQLiteFeedbackStore
)

# --------------------------------------------------------------
# 5. ENTERPRISE FIXTURES
# --------------------------------------------------------------
import pytest
import asyncio
import tempfile
import shutil
import yaml
import sqlite3
from unittest.mock import AsyncMock, patch

@pytest.fixture
def codegen_env():
    dir = Path(tempfile.mkdtemp(prefix="codegen_test_"))
    config = dir / "config.yaml"
    db = dir / "feedback.db"
    templates = dir / "templates"
    templates.mkdir()

    (templates / "python.jinja2").write_text(
        "Generate: {{ requirements.features }}. JSON: {\"files\": {\"main.py\": \"def x(): pass\"}}"
    )

    cfg = {
        "backend": "openai",
        "api_keys": {"openai": "sk-test"},
        "model": {"openai": "gpt-4o"},
        "allow_interactive_hitl": True,
        "enable_security_scan": True,
        "feedback_store": {"type": "sqlite", "path": str(db)},
        "template_dir": str(templates),
        "compliance": {"banned_functions": ["eval"], "max_line_length": 100}
    }
    with open(config, "w") as f:
        yaml.dump(cfg, f)

    yield {
        "config": str(config),
        "db": str(db),
        "req": {"features": ["fib"], "target_language": "python"}
    }
    shutil.rmtree(dir, ignore_errors=True)

@pytest.fixture
def mock_llm():
    with patch("agents.codegen_agent.codegen_agent.call_llm_api", new_callable=AsyncMock) as m:
        m.return_value = {"content": '{"files": {"main.py": "def fib(n): return n"}}'}
        yield m