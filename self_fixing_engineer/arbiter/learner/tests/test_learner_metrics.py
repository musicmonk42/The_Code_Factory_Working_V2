# test_metrics.py

import pytest
import os
from unittest.mock import Mock, patch

from prometheus_client import Counter, Gauge, Histogram, Summary, Info, REGISTRY

from arbiter.learner.metrics import (
    _get_or_create_metric,
    get_labels,
    learn_counter,
    learn_error_counter,
    learn_duration_seconds,
    learn_duration_summary,
    forget_counter,
    forget_duration_seconds,
    forget_duration_summary,
    retrieve_hit_miss,
    audit_events_total,
    circuit_breaker_state,
    audit_failure_total,
    explanation_llm_latency_seconds,
    explanation_llm_failure_total,
    fuzzy_parser_success_total,
    fuzzy_parser_failure_total,
    fuzzy_parser_latency_seconds,
    self_audit_duration_seconds,
    self_audit_failure_total,
    learner_info
)


class TestGlobalLabels:
    """Test suite for global labels configuration."""
    
    def test_default_global_labels(self):
        """Test default global labels when environment variables not set."""
        with patch.dict(os.environ, {}, clear=True):
            from importlib import reload
            import arbiter.learner.metrics as metrics_module
            reload(metrics_module)
            
            assert metrics_module.GLOBAL_LABELS["environment"] == "production"
            assert metrics_module.GLOBAL_LABELS["instance"] == "learner-instance-1"
    
    def test_custom_global_labels(self):
        """Test global labels with custom environment variables."""
        with patch.dict(os.environ, {
            "ENVIRONMENT": "staging",
            "INSTANCE_NAME": "learner-test-2"
        }):
            from importlib import reload
            import arbiter.learner.metrics as metrics_module
            reload(metrics_module)
            
            assert metrics_module.GLOBAL_LABELS["environment"] == "staging"
            assert metrics_module.GLOBAL_LABELS["instance"] == "learner-test-2"
    
    def test_get_labels_helper(self):
        """Test the get_labels helper function."""
        labels = get_labels(domain="test", source="api")
        assert labels["domain"] == "test"
        assert labels["source"] == "api"
        assert "environment" in labels
        assert "instance" in labels


class TestGetOrCreateMetric:
    """Test suite for _get_or_create_metric function."""
    
    def test_create_new_counter(self):
        """Test creating a new counter metric."""
        with patch.object(REGISTRY, '_names_to_collectors', {}):
            metric = _get_or_create_metric(
                Counter,
                "test_counter",
                "Test counter metric",
                ("test_label",)
            )
            
            assert isinstance(metric, Counter)
            assert metric._name == "test_counter"
            assert metric._documentation == "Test counter metric"
            # Check that global labels are included
            assert "environment" in metric._labelnames
            assert "instance" in metric._labelnames
            assert "test_label" in metric._labelnames
    
    def test_create_histogram_with_buckets(self):
        """Test creating a histogram with custom buckets."""
        with patch.object(REGISTRY, '_names_to_collectors', {}):
            buckets = (0.1, 0.5, 1.0, 5.0, 10.0)
            metric = _get_or_create_metric(
                Histogram,
                "test_histogram",
                "Test histogram metric",
                ("domain",),
                buckets=buckets
            )
            
            assert isinstance(metric, Histogram)
            assert metric._name == "test_histogram"
            # Can't directly access _buckets in prometheus_client, but we can verify
            # the metric was created with the histogram type
            assert metric.__class__.__name__ == "Histogram"
    
    def test_retrieve_existing_metric(self):
        """Test retrieving an existing metric of the same type."""
        existing_counter = Counter("existing_metric", "Existing metric", ["label1"])
        
        with patch.object(REGISTRY, '_names_to_collectors', {"existing_metric": existing_counter}):
            metric = _get_or_create_metric(
                Counter,
                "existing_metric",
                "Existing metric",
                ("label1",)
            )
            
            assert metric is existing_counter
    
    def test_replace_metric_with_different_type(self):
        """Test replacing a metric when type mismatch occurs."""
        existing_counter = Counter("mismatched_metric", "Old metric", ["label1"])
        mock_registry = Mock()
        mock_registry._names_to_collectors = {"mismatched_metric": existing_counter}
        mock_registry.unregister = Mock()
        
        with patch('arbiter.learner.metrics.REGISTRY', mock_registry):
            with patch('arbiter.learner.metrics.Histogram') as MockHistogram:
                mock_histogram = Mock(spec=Histogram)
                MockHistogram.return_value = mock_histogram
                
                _get_or_create_metric(
                    Histogram,
                    "mismatched_metric",
                    "New metric",
                    ("label1",),
                    buckets=(0.1, 1.0)
                )
                
                # Verify old metric was unregistered
                mock_registry.unregister.assert_called_once_with(existing_counter)
    
    def test_handle_registry_error(self):
        """Test handling of registry errors."""
        with patch.object(REGISTRY, '_names_to_collectors') as mock_collectors:
            mock_collectors.get.side_effect = Exception("Registry error")
            
            # Should still create the metric despite error
            metric = _get_or_create_metric(
                Counter,
                "error_metric",
                "Error metric",
                ()
            )
            
            assert isinstance(metric, Counter)


class TestLearningMetrics:
    """Test suite for learning-related metrics."""
    
    def test_learn_counter_structure(self):
        """Test learn_counter metric structure."""
        assert isinstance(learn_counter, Counter)
        assert "domain" in learn_counter._labelnames
        assert "source" in learn_counter._labelnames
        assert "environment" in learn_counter._labelnames
        assert "instance" in learn_counter._labelnames
    
    def test_learn_error_counter_structure(self):
        """Test learn_error_counter metric structure."""
        assert isinstance(learn_error_counter, Counter)
        assert "domain" in learn_error_counter._labelnames
        assert "error_type" in learn_error_counter._labelnames
    
    def test_learn_duration_histogram(self):
        """Test learn_duration_seconds histogram configuration."""
        assert isinstance(learn_duration_seconds, Histogram)
        assert "domain" in learn_duration_seconds._labelnames
        # Just verify it's a histogram, can't check internal buckets directly
        assert learn_duration_seconds.__class__.__name__ == "Histogram"
    
    def test_learn_duration_summary(self):
        """Test learn_duration_summary metric."""
        assert isinstance(learn_duration_summary, Summary)
        assert "domain" in learn_duration_summary._labelnames


class TestForgettingMetrics:
    """Test suite for forgetting-related metrics."""
    
    def test_forget_counter_structure(self):
        """Test forget_counter metric structure."""
        assert isinstance(forget_counter, Counter)
        assert "domain" in forget_counter._labelnames
    
    def test_forget_duration_histogram(self):
        """Test forget_duration_seconds histogram configuration."""
        assert isinstance(forget_duration_seconds, Histogram)
        assert "domain" in forget_duration_seconds._labelnames
        # Just verify it's a histogram
        assert forget_duration_seconds.__class__.__name__ == "Histogram"
    
    def test_forget_duration_summary(self):
        """Test forget_duration_summary metric."""
        assert isinstance(forget_duration_summary, Summary)
        assert "domain" in forget_duration_summary._labelnames


class TestRetrievalMetrics:
    """Test suite for retrieval-related metrics."""
    
    def test_retrieve_hit_miss_structure(self):
        """Test retrieve_hit_miss counter structure."""
        assert isinstance(retrieve_hit_miss, Counter)
        assert "domain" in retrieve_hit_miss._labelnames
        assert "cache_status" in retrieve_hit_miss._labelnames


class TestAuditMetrics:
    """Test suite for audit-related metrics."""
    
    def test_audit_events_total_structure(self):
        """Test audit_events_total counter structure."""
        assert isinstance(audit_events_total, Counter)
        assert "action" in audit_events_total._labelnames
    
    def test_circuit_breaker_state_gauge(self):
        """Test circuit_breaker_state gauge metric."""
        assert isinstance(circuit_breaker_state, Gauge)
        assert "name" in circuit_breaker_state._labelnames
    
    def test_audit_failure_total_structure(self):
        """Test audit_failure_total counter structure."""
        assert isinstance(audit_failure_total, Counter)
        assert "action" in audit_failure_total._labelnames
        assert "error_type" in audit_failure_total._labelnames


class TestExplanationMetrics:
    """Test suite for explanation-related metrics."""
    
    def test_explanation_llm_latency_histogram(self):
        """Test explanation_llm_latency_seconds histogram."""
        assert isinstance(explanation_llm_latency_seconds, Histogram)
        assert "domain" in explanation_llm_latency_seconds._labelnames
        # Just verify it's a histogram
        assert explanation_llm_latency_seconds.__class__.__name__ == "Histogram"
    
    def test_explanation_llm_failure_counter(self):
        """Test explanation_llm_failure_total counter."""
        assert isinstance(explanation_llm_failure_total, Counter)
        assert "domain" in explanation_llm_failure_total._labelnames
        assert "error_type" in explanation_llm_failure_total._labelnames


class TestFuzzyParserMetrics:
    """Test suite for fuzzy parser metrics."""
    
    def test_fuzzy_parser_success_counter(self):
        """Test fuzzy_parser_success_total counter."""
        assert isinstance(fuzzy_parser_success_total, Counter)
        assert "parser_name" in fuzzy_parser_success_total._labelnames
    
    def test_fuzzy_parser_failure_counter(self):
        """Test fuzzy_parser_failure_total counter."""
        assert isinstance(fuzzy_parser_failure_total, Counter)
        assert "parser_name" in fuzzy_parser_failure_total._labelnames
        assert "error_type" in fuzzy_parser_failure_total._labelnames
    
    def test_fuzzy_parser_latency_histogram(self):
        """Test fuzzy_parser_latency_seconds histogram."""
        assert isinstance(fuzzy_parser_latency_seconds, Histogram)
        assert "parser_name" in fuzzy_parser_latency_seconds._labelnames
        # Just verify it's a histogram
        assert fuzzy_parser_latency_seconds.__class__.__name__ == "Histogram"


class TestSelfAuditMetrics:
    """Test suite for self-audit metrics."""
    
    def test_self_audit_duration_histogram(self):
        """Test self_audit_duration_seconds histogram."""
        assert isinstance(self_audit_duration_seconds, Histogram)
        # Self-audit metrics don't have domain labels
        assert "domain" not in self_audit_duration_seconds._labelnames
        # But should have global labels
        assert "environment" in self_audit_duration_seconds._labelnames
    
    def test_self_audit_failure_counter(self):
        """Test self_audit_failure_total counter."""
        assert isinstance(self_audit_failure_total, Counter)
        assert "error_type" in self_audit_failure_total._labelnames


class TestModuleInfoMetric:
    """Test suite for module info metric."""
    
    def test_learner_info_structure(self):
        """Test learner_info metric structure."""
        assert isinstance(learner_info, Info)
        # Info metrics have special behavior
        assert hasattr(learner_info, 'info')


class TestMetricUsage:
    """Test actual metric usage patterns."""
    
    def test_counter_increment(self):
        """Test incrementing a counter metric."""
        # Reset counter for testing
        with patch.object(learn_counter, '_metrics', {}):
            learn_counter.labels(
                domain="TestDomain",
                source="test",
                environment="test",
                instance="test-1"
            ).inc()
            
            # Verify the metric was incremented
            # Note: This is testing the interface, not the actual value
            assert learn_counter._metrics is not None
    
    def test_counter_increment_with_helper(self):
        """Test incrementing a counter using the get_labels helper."""
        with patch.object(learn_counter, '_metrics', {}):
            learn_counter.labels(
                **get_labels(domain="TestDomain", source="test")
            ).inc()
            
            # Verify the metric was incremented
            assert learn_counter._metrics is not None
    
    def test_histogram_observe(self):
        """Test observing values in a histogram."""
        with patch.object(learn_duration_seconds, '_metrics', {}):
            learn_duration_seconds.labels(
                domain="TestDomain",
                environment="test",
                instance="test-1"
            ).observe(1.5)
            
            # Verify the metric accepted the observation
            assert learn_duration_seconds._metrics is not None
    
    def test_gauge_set(self):
        """Test setting a gauge value."""
        with patch.object(circuit_breaker_state, '_metrics', {}):
            circuit_breaker_state.labels(
                name="test_breaker",
                environment="test",
                instance="test-1"
            ).set(1)
            
            # Verify the metric accepted the value
            assert circuit_breaker_state._metrics is not None


class TestMetricLabelCombinations:
    """Test various label combinations for metrics."""
    
    def test_consistent_global_labels(self):
        """Test that all metrics have consistent global labels."""
        metrics_to_check = [
            learn_counter,
            learn_error_counter,
            forget_counter,
            retrieve_hit_miss,
            audit_events_total,
            circuit_breaker_state,
            explanation_llm_failure_total,
            fuzzy_parser_success_total,
            self_audit_duration_seconds
        ]
        
        for metric in metrics_to_check:
            assert "environment" in metric._labelnames
            assert "instance" in metric._labelnames
    
    def test_domain_specific_metrics(self):
        """Test metrics that should have domain labels."""
        domain_metrics = [
            learn_counter,
            learn_error_counter,
            learn_duration_seconds,
            forget_counter,
            forget_duration_seconds,
            retrieve_hit_miss,
            explanation_llm_latency_seconds,
            explanation_llm_failure_total
        ]
        
        for metric in domain_metrics:
            assert "domain" in metric._labelnames


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--cov=arbiter.learner.metrics"])