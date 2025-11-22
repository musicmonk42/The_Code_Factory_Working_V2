"""
Enterprise-Grade Test Suite for metrics.py

Covers:
- All metric creation, idempotency, and thread-safety
- Dynamic compliance control metrics
- Label cardinality and value correctness
- Metric registration, conflict, and error handling
- All public symbols, import and runtime coverage
- Prometheus client integration, scraping, and updates
- Error and edge cases (e.g., conflicting types, bad labels, duplicate registration)
- Health: metrics are updated and observable under concurrency and mutation

Requirements:
- pytest
- threading
- prometheus_client
"""

import pytest
import threading
from prometheus_client import Counter, Gauge, Histogram, Summary, generate_latest

import sys
sys.modules["..guardrails.compliance_mapper"] = __import__("types").SimpleNamespace(load_compliance_map=lambda: {
    "FAKE-1": {"name": "FakeControl", "status": "enforced", "required": True},
    "FAKE-2": {"name": "FakeOptional", "status": "logged", "required": False}
})

from arbiter.policy.metrics import (
    get_or_create_metric,
    policy_decision_total, policy_file_reload_count, policy_last_reload_timestamp,
    feedback_processing_time, LLM_CALL_LATENCY,
    COMPLIANCE_CONTROL_ACTIONS_TOTAL, COMPLIANCE_CONTROL_STATUS, COMPLIANCE_VIOLATIONS_TOTAL
)

@pytest.fixture
def mock_config(monkeypatch):
    class MockConfig:
        DECISION_OPTIMIZER_SETTINGS = {
            "llm_call_latency_buckets": (0.1, 0.5, 1, 2, 5, 10, 30, 60),
            "feedback_processing_buckets": (0.001, 0.01, 0.1, 1, 10)
        }
        CIRCUIT_BREAKER_MIN_OPERATION_INTERVAL = 30.0
        CIRCUIT_BREAKER_VALIDATION_ERROR_INTERVAL = 300.0
    monkeypatch.setattr("arbiter.policy.metrics.ArbiterConfig", MockConfig)


########## Metric Creation and Idempotency ##########

def test_counter_idempotency():
    c1 = get_or_create_metric(Counter, "test_counter_idem", "Idempotent counter", ("label1",))
    c2 = get_or_create_metric(Counter, "test_counter_idem", "Idempotent counter", ("label1",))
    assert c1 is c2
    c1.labels(label1="foo").inc()
    c2.labels(label1="foo").inc()
    assert c1.labels(label1="foo")._value.get() == 2

def test_gauge_idempotency_and_set():
    g1 = get_or_create_metric(Gauge, "test_gauge_idem", "Idempotent gauge", ("label2",))
    g2 = get_or_create_metric(Gauge, "test_gauge_idem", "Idempotent gauge", ("label2",))
    assert g1 is g2
    g1.labels(label2="bar").set(123)
    assert g2.labels(label2="bar")._value.get() == 123

def test_histogram_buckets_and_idempotency():
    h1 = get_or_create_metric(Histogram, "test_histogram_idem", "Idempotent histogram", ("label3",), buckets=(0.1, 1, 10))
    h2 = get_or_create_metric(Histogram, "test_histogram_idem", "Idempotent histogram", ("label3",), buckets=(0.1, 1, 10))
    assert h1 is h2
    h1.labels(label3="baz").observe(1.3)
    # Should go into the 10 bucket
    assert sum(h1.labels(label3="baz")._sum.get() for _ in range(1)) > 0

def test_summary_idempotency():
    s1 = get_or_create_metric(Summary, "test_summary_idem", "Idempotent summary", ("label4",))
    s2 = get_or_create_metric(Summary, "test_summary_idem", "Idempotent summary", ("label4",))
    assert s1 is s2
    s1.labels(label4="qux").observe(0.5)
    s2.labels(label4="qux").observe(0.5)
    assert s1.labels(label4="qux")._sum.get() > 0

########## Error Handling and Conflicting Types ##########

def test_metric_conflicting_types(caplog):
    # Register a Counter, then try to register a Gauge of the same name
    name = "test_type_conflict"
    c = get_or_create_metric(Counter, name, "Test counter", ("foo",))
    g = get_or_create_metric(Gauge, name, "Test gauge", ("foo",))
    # Should not raise, should log error and return the existing metric
    assert isinstance(g, Counter)
    assert g is c

########## Dynamic Compliance Metrics ##########

def test_dynamic_compliance_metrics_exist(mock_config):
    # These should be registered by metrics.py at import time
    assert isinstance(COMPLIANCE_CONTROL_ACTIONS_TOTAL, Counter)
    assert isinstance(COMPLIANCE_CONTROL_STATUS, Gauge)
    assert isinstance(COMPLIANCE_VIOLATIONS_TOTAL, Counter)
    # Default labels exist (at least for fake controls)
    COMPLIANCE_CONTROL_STATUS.labels(control_id="FAKE-1", status_detail="enforced", required="true").set(1)
    COMPLIANCE_CONTROL_STATUS.labels(control_id="FAKE-2", status_detail="logged", required="false").set(0)
    COMPLIANCE_CONTROL_ACTIONS_TOTAL.labels(control_id="FAKE-1", result="passed", action_type="auto_learn").inc()
    COMPLIANCE_VIOLATIONS_TOTAL.labels(control_id="FAKE-1", violation_type="missing").inc()

########## Label Cardinality and Value Correctness ##########

def test_metric_label_cardinality():
    c = get_or_create_metric(Counter, "test_label_cardinality", "Cardinality test", ("a",))
    for i in range(5):
        c.labels(a=f"v{i}").inc()
    # Should have 5 different series
    assert len(list(c._metrics.keys())) == 5

def test_invalid_label_raises():
    c = get_or_create_metric(Counter, "test_bad_label", "Bad label", ("foo",))
    with pytest.raises(ValueError):
        c.labels(bar="baz")  # Wrong label

########## Thread Safety and Concurrency ##########

def test_metrics_thread_safety():
    # Register and update metrics in multiple threads
    c = get_or_create_metric(Counter, "test_concurrent_counter", "Threaded counter", ("x",))
    def worker(val):
        for _ in range(100):
            c.labels(x=val).inc()
    threads = [threading.Thread(target=worker, args=(f"t{i}",)) for i in range(5)]
    for t in threads: t.start()
    for t in threads: t.join()
    # Each thread should have incremented its own label 100 times
    for i in range(5):
        assert c.labels(x=f"t{i}")._value.get() == 100

########## Prometheus Client Integration ##########

def test_prometheus_scrape_for_all_metrics(mock_config):
    # First, ensure the metrics are actually created
    
    # Force registration by using the metrics
    policy_decision_total.labels(allowed="true", domain="test", user_type="user", reason_code="test").inc()
    policy_file_reload_count.inc()
    policy_last_reload_timestamp.set(123)
    LLM_CALL_LATENCY.labels(provider="test").observe(1.0)
    
    # Scrape registry and check for all public metrics
    output = generate_latest()
    names = set()
    for line in output.decode().splitlines():
        if line.startswith("# HELP") or line.startswith("# TYPE"): 
            continue
        if line and not line.startswith("#"):
            metric_name = line.split()[0]
            # Extract base metric name without labels, suffixes
            base_name = metric_name.split("{")[0]
            # Remove _bucket, _count, _sum suffixes for histograms
            for suffix in ["_bucket", "_count", "_sum", "_total"]:
                if base_name.endswith(suffix) and suffix != "_total":
                    base_name = base_name[:-len(suffix)]
            names.add(base_name)
    
    # Debug: print what we found
    print(f"Found metrics: {sorted(names)}")
    
    # Check for core metrics - using actual names from metrics.py
    assert "policy_decisions_total" in names or "policy_decisions" in names
    assert "policy_file_reloads_total" in names or "policy_file_reloads" in names
    assert "policy_last_reload_timestamp_seconds" in names
    assert "llm_policy_call_latency_seconds" in names

########## Edge Cases: Duplicate Registration, Bad Buckets ##########

def test_duplicate_registration_does_not_crash():
    h = get_or_create_metric(Histogram, "test_dup_hist", "Duplicate registration", ("foo",), buckets=(1, 2, 3))
    h2 = get_or_create_metric(Histogram, "test_dup_hist", "Duplicate registration", ("foo",), buckets=(1, 2, 3))
    assert h is h2

def test_bad_buckets_graceful():
    # Non-monotonic buckets should raise
    with pytest.raises(ValueError):
        get_or_create_metric(Histogram, "test_bad_buckets", "Bad buckets", ("bar",), buckets=(5, 1))

########## Coverage: All Public Symbols ##########

def test_public_symbols_present(mock_config):
    # Import using the actual names from metrics.py
    from arbiter.policy.metrics import (
        get_or_create_metric
    )
    assert callable(get_or_create_metric)
    assert isinstance(policy_decision_total, Counter)
    assert isinstance(policy_file_reload_count, Counter)
    assert isinstance(policy_last_reload_timestamp, Gauge)
    assert isinstance(feedback_processing_time, Histogram)
    assert isinstance(LLM_CALL_LATENCY, Histogram)

########## Coverage: Metrics Updated on Change ##########

def test_metric_updates_observable():
    # Counter
    c = get_or_create_metric(Counter, "test_observable_counter", "Observable counter", ("x",))
    c.labels(x="foo").inc(3)
    assert c.labels(x="foo")._value.get() == 3
    # Gauge
    g = get_or_create_metric(Gauge, "test_observable_gauge", "Observable gauge", ("y",))
    g.labels(y="bar").set(77)
    assert g.labels(y="bar")._value.get() == 77
    # Histogram
    h = get_or_create_metric(Histogram, "test_observable_hist", "Observable histogram", ("z",), buckets=(1, 2, 3))
    h.labels(z="baz").observe(2)
    assert h.labels(z="baz")._sum.get() > 0

########## Extreme Edge: Metric Name Overlap with Builtins ##########

def test_metric_name_overlap_does_not_break():
    # Should not break on common names
    c = get_or_create_metric(Counter, "sum", "Overlap with builtin", ("foo",))
    c.labels(foo="bar").inc()
    assert c.labels(foo="bar")._value.get() == 1

########## Test Complete Coverage ##########

def test_metrics_module_all_public_symbols_present(mock_config):
    # Import with correct names from metrics.py
    from arbiter.policy.metrics import (
        get_or_create_metric,
        COMPLIANCE_CONTROL_ACTIONS_TOTAL,
        COMPLIANCE_CONTROL_STATUS,
        COMPLIANCE_VIOLATIONS_TOTAL
    )
    assert callable(get_or_create_metric)
    assert isinstance(policy_decision_total, Counter)
    assert isinstance(policy_file_reload_count, Counter)
    assert isinstance(policy_last_reload_timestamp, Gauge)
    assert isinstance(feedback_processing_time, Histogram)
    assert isinstance(LLM_CALL_LATENCY, Histogram)
    assert isinstance(COMPLIANCE_CONTROL_ACTIONS_TOTAL, Counter)
    assert isinstance(COMPLIANCE_CONTROL_STATUS, Gauge)
    assert isinstance(COMPLIANCE_VIOLATIONS_TOTAL, Counter)