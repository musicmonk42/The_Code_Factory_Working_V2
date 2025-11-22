import sys
import os
import types
from pathlib import Path

# --------------------------------------------------------------
# 1. ADD PROJECT ROOT TO PYTHONPATH (FIRST!)
# --------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[3]  # tests → agents → generator → repo root
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _stub_module(name: str, attrs: dict = None):
    attrs = attrs or {}
    mod = types.ModuleType(name)
    mod.__file__ = f"<stubbed {name}>"
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


# --------------------------------------------------------------
# 2. STUB OPENTELEMETRY / PROMETHEUS / JAEGER
#    (must be before importing code that uses them)
# --------------------------------------------------------------

# --- STUB THE ENTIRE OPENTELEMETRY HIERARCHY ---
if "opentelemetry" not in sys.modules:
    otel = _stub_module("opentelemetry", {})
    # trace
    trace = _stub_module("opentelemetry.trace", {})
    trace.get_tracer = lambda *a, **k: None
    trace.get_current_span = lambda: None
    otel.trace = trace
    # sdk + resources + export
    sdk = _stub_module("opentelemetry.sdk", {})
    resources = _stub_module("opentelemetry.sdk.resources", {})
    resources.Resource = lambda **kw: None
    export = _stub_module("opentelemetry.sdk.trace.export", {})
    export.ConsoleSpanExporter = lambda *a, **k: None
    export.SimpleSpanProcessor = lambda *a, **k: None
    sdk.resources = resources
    sdk.trace = _stub_module("opentelemetry.sdk.trace", {})
    sdk.trace.export = export

    sys.modules["opentelemetry"] = otel
    sys.modules["opentelemetry.trace"] = trace
    sys.modules["opentelemetry.sdk"] = sdk
    sys.modules["opentelemetry.sdk.resources"] = resources
    sys.modules["opentelemetry.sdk.trace.export"] = export

# --- STUB PROMETHEUS ---
if "prometheus_client" not in sys.modules:
    prom = _stub_module("prometheus_client", {})

    class DummyMetric:
        def __init__(self, *a, **k):
            self._value = type("V", (), {"get": lambda self: 0})()

        def labels(self, *a, **k):
            return self

        def inc(self, *a, **k):
            return None

        def set(self, *a, **k):
            return None

        def observe(self, *a, **k):
            return None

    prom.Counter = DummyMetric
    prom.Histogram = DummyMetric
    prom.Gauge = DummyMetric
    prom.REGISTRY = type("R", (), {"_collector_to_names": {}})()
    prom.generate_latest = lambda: b"# stub"
    prom.start_http_server = lambda *a, **k: None

    sys.modules["prometheus_client"] = prom

# --- STUB JAEGER EXPORTER ---
if "opentelemetry.exporter.jaeger.thrift" not in sys.modules:
    _stub_module(
        "opentelemetry.exporter.jaeger.thrift",
        {"JaegerExporter": lambda **kw: None},
    )


# --------------------------------------------------------------
# 3. STUB SENTENCE_TRANSFORMERS (CRITICAL FOR deploy_prompt)
#    So we never import real transformers/torch in tests.
# --------------------------------------------------------------
def _install_dummy_sentence_transformers():
    # Always override with a safe stub to avoid DLL / GPU issues.
    if "sentence_transformers" in sys.modules:
        del sys.modules["sentence_transformers"]

    def semantic_search(query_emb, corpus_embs, top_k=3):
        hits = [
            {"corpus_id": i, "score": float(i + 1)}
            for i in range(min(top_k, len(corpus_embs)))
        ]
        return [hits]

    class DummyEmbeddingModel:
        def __init__(self, *a, **k):
            pass

        def encode(self, items, convert_to_tensor=True):
            if isinstance(items, str):
                return [float(len(items))]
            return [[float(len(x))] for x in items]

    m = types.ModuleType("sentence_transformers")
    m.SentenceTransformer = DummyEmbeddingModel
    m.util = types.SimpleNamespace(semantic_search=semantic_search)

    sys.modules["sentence_transformers"] = m


_install_dummy_sentence_transformers()


# --- STUB PRESIDIO ---
# NEW FIX: Add dummy presidio to stop Spacy/Torch from loading
def _install_dummy_presidio():
    # Stub presidio_analyzer
    if "presidio_analyzer" in sys.modules:
        del sys.modules["presidio_analyzer"]
    pa = types.ModuleType("presidio_analyzer")

    class DummyAnalyzerEngine:
        def __init__(self, *a, **k):
            pass

        def analyze(self, text, entities=None, language="en"):
            # Return empty results: we only care that calls don't explode
            return []

    pa.AnalyzerEngine = DummyAnalyzerEngine
    sys.modules["presidio_analyzer"] = pa

    # Stub presidio_anonymizer
    if "presidio_anonymizer" in sys.modules:
        del sys.modules["presidio_anonymizer"]
    pan = types.ModuleType("presidio_anonymizer")

    class DummyAnonymizerEngine:
        def __init__(self, *a, **k):
            pass

        def anonymize(self, text, analyzer_results=None, anonymizers=None):
            # Passthrough or trivial wrapper; tests that expect [REDACTED] can override.
            return types.SimpleNamespace(text=text)

    pan.AnonymizerEngine = DummyAnonymizerEngine
    sys.modules["presidio_anonymizer"] = pan


_install_dummy_presidio()
# End NEW FIX


# --------------------------------------------------------------
# 4. DISABLE WATCHERS / ENABLE TESTING MODE
# --------------------------------------------------------------
os.environ["TESTING"] = "1"
print("TESTING=1 → watchers disabled")


# --------------------------------------------------------------
# 5. IMPORT LIGHTWEIGHT PIECES THAT RELY ON STUBS
# --------------------------------------------------------------
#
# *** THIS IS THE FIX ***
#

# --------------------------------------------------------------
# 6. ENTERPRISE FIXTURES
# --------------------------------------------------------------
import pytest
import tempfile
import shutil
import yaml
from unittest.mock import AsyncMock, patch


@pytest.fixture
def codegen_env():
    dir_path = Path(tempfile.mkdtemp(prefix="codegen_test_"))
    config = dir_path / "config.yaml"
    db = dir_path / "feedback.db"
    templates = dir_path / "templates"
    templates.mkdir()

    (templates / "python.jinja2").write_text(
        "Generate: {{ requirements.features }}. "
        'JSON: {"files": {"main.py": "def x(): pass"}}',
        encoding="utf-8",
    )

    cfg = {
        "backend": "openai",
        "api_keys": {"openai": "sk-test"},
        "model": {"openai": "gpt-4o"},
        "allow_interactive_hitl": True,
        "enable_security_scan": True,
        "feedback_store": {"type": "sqlite", "path": str(db)},
        "template_dir": str(templates),
        "compliance": {
            "banned_functions": ["eval"],
            "max_line_length": 100,
        },
    }

    with open(config, "w", encoding="utf-8") as f:
        yaml.dump(cfg, f)

    yield {
        "config": str(config),
        "db": str(db),
        "req": {"features": ["fib"], "target_language": "python"},
    }

    shutil.rmtree(dir_path, ignore_errors=True)


@pytest.fixture
def mock_llm():
    #
    # *** THIS IS THE SECOND FIX ***
    #
    with patch(
        "generator.agents.codegen_agent.codegen_agent.call_llm_api",
        new_callable=AsyncMock,
    ) as m:
        m.return_value = {"content": '{"files": {"main.py": "def fib(n): return n"}}'}
        yield m
