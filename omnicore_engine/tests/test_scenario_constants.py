"""
Test suite for omnicore_engine/scenario_constants.py
Tests scenario models and tracked dictionary functionality.
"""

import os

# Add the parent directory to path for imports
import sys
import threading
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from omnicore_engine.scenario_constants import (
    ScenarioMetric,
    ScenarioTemplate,
    TrackedDict,
)


class TestScenarioMetric:
    """Test the ScenarioMetric Pydantic model"""

    def test_valid_metric_creation(self):
        """Test creating a valid ScenarioMetric"""
        metric = ScenarioMetric(
            description="Test metric for performance",
            default_value=0.5,
            unit="percentage",
            range=[0.0, 1.0],
            aggregation_method="average",
        )

        assert metric.description == "Test metric for performance"
        assert metric.default_value == 0.5
        assert metric.unit == "percentage"
        assert metric.range == [0.0, 1.0]
        assert metric.aggregation_method == "average"

    def test_range_validation_valid(self):
        """Test range validation with valid ranges"""
        # Valid range with min < max
        metric = ScenarioMetric(
            description="Test",
            default_value=5.0,
            unit="units",
            range=[0.0, 10.0],
            aggregation_method="sum",
        )
        assert metric.range == [0.0, 10.0]

        # Valid range with min == max
        metric = ScenarioMetric(
            description="Test",
            default_value=5.0,
            unit="units",
            range=[5.0, 5.0],
            aggregation_method="sum",
        )
        assert metric.range == [5.0, 5.0]

    def test_range_validation_invalid_length(self):
        """Test range validation with wrong number of elements"""
        with pytest.raises(ValueError, match="Range must be a list of two floats"):
            ScenarioMetric(
                description="Test",
                default_value=0.5,
                unit="units",
                range=[0.0],  # Only one element
                aggregation_method="average",
            )

        with pytest.raises(ValueError, match="Range must be a list of two floats"):
            ScenarioMetric(
                description="Test",
                default_value=0.5,
                unit="units",
                range=[0.0, 0.5, 1.0],  # Three elements
                aggregation_method="average",
            )

    def test_range_validation_invalid_order(self):
        """Test range validation with min > max"""
        with pytest.raises(
            ValueError, match="Range must be a list of two floats where min <= max"
        ):
            ScenarioMetric(
                description="Test",
                default_value=0.5,
                unit="units",
                range=[1.0, 0.0],  # max < min
                aggregation_method="average",
            )

    def test_metric_serialization(self):
        """Test metric can be serialized to dict"""
        metric = ScenarioMetric(
            description="Test metric",
            default_value=0.75,
            unit="ratio",
            range=[0.0, 1.0],
            aggregation_method="max",
        )

        data = metric.dict()
        assert data["description"] == "Test metric"
        assert data["default_value"] == 0.75
        assert data["unit"] == "ratio"
        assert data["range"] == [0.0, 1.0]
        assert data["aggregation_method"] == "max"


class TestScenarioTemplate:
    """Test the ScenarioTemplate Pydantic model"""

    def test_valid_template_creation(self):
        """Test creating a valid ScenarioTemplate"""
        template = ScenarioTemplate(
            impact=0.8,
            label="High Impact Scenario",
            active=True,
            description="A scenario with significant impact on system performance",
            priority=0.9,
        )

        assert template.impact == 0.8
        assert template.label == "High Impact Scenario"
        assert template.active == True
        assert (
            template.description
            == "A scenario with significant impact on system performance"
        )
        assert template.priority == 0.9

    def test_priority_validation_valid(self):
        """Test priority validation with valid values"""
        # Minimum valid priority
        template = ScenarioTemplate(
            impact=0.5, label="Test", active=True, description="Test", priority=0.0
        )
        assert template.priority == 0.0

        # Maximum valid priority
        template = ScenarioTemplate(
            impact=0.5, label="Test", active=True, description="Test", priority=1.0
        )
        assert template.priority == 1.0

    def test_priority_validation_invalid(self):
        """Test priority validation with invalid values"""
        # Priority below minimum
        with pytest.raises(ValueError):
            ScenarioTemplate(
                impact=0.5, label="Test", active=True, description="Test", priority=-0.1
            )

        # Priority above maximum
        with pytest.raises(ValueError):
            ScenarioTemplate(
                impact=0.5, label="Test", active=True, description="Test", priority=1.1
            )

    def test_template_serialization(self):
        """Test template can be serialized to dict"""
        template = ScenarioTemplate(
            impact=0.6,
            label="Medium Impact",
            active=False,
            description="Inactive scenario",
            priority=0.5,
        )

        data = template.dict()
        assert data["impact"] == 0.6
        assert data["label"] == "Medium Impact"
        assert data["active"] == False
        assert data["description"] == "Inactive scenario"
        assert data["priority"] == 0.5


class TestTrackedDict:
    """Test the TrackedDict class"""

    @pytest.fixture
    def mock_counter_class(self):
        """Create a mock Counter class"""
        mock_class = Mock()
        mock_instance = Mock()
        mock_instance.labels.return_value = Mock(inc=Mock())
        mock_class.return_value = mock_instance
        return mock_class

    @pytest.fixture
    def sample_data(self):
        """Sample data for TrackedDict"""
        return {"metric1": 100, "metric2": 200, "metric3": 300}

    def test_initialization(self, sample_data):
        """Test TrackedDict initialization"""
        tracked = TrackedDict(sample_data, is_metrics=True)

        assert tracked._data == sample_data
        assert tracked._is_metrics == True
        assert len(tracked) == 3

    def test_getitem_existing_key(self, sample_data):
        """Test accessing existing keys"""
        with patch(
            "omnicore_engine.scenario_constants.get_or_create_counter"
        ) as mock_get_counter:
            mock_counter = Mock()
            mock_counter.labels.return_value = Mock(inc=Mock())
            mock_get_counter.return_value = mock_counter

            tracked = TrackedDict(sample_data, is_metrics=True)

            value = tracked["metric1"]
            assert value == 100

            # Counter should be created and incremented
            mock_get_counter.assert_called_once()
            mock_counter.labels.assert_called_with(metric_name="metric1")
            mock_counter.labels.return_value.inc.assert_called_once()

    def test_getitem_nonexistent_key(self, sample_data):
        """Test accessing non-existent keys raises KeyError"""
        with patch(
            "omnicore_engine.scenario_constants.get_or_create_counter"
        ) as mock_get_counter:
            mock_counter = Mock()
            mock_counter.labels.return_value = Mock(inc=Mock())
            mock_get_counter.return_value = mock_counter

            tracked = TrackedDict(sample_data, is_metrics=True)

            with pytest.raises(KeyError, match="Key 'nonexistent' not found"):
                _ = tracked["nonexistent"]

    def test_metrics_counter_initialization(self, sample_data):
        """Test metrics counter is initialized once"""
        with patch(
            "omnicore_engine.scenario_constants.get_or_create_counter"
        ) as mock_get_counter:
            mock_counter = Mock()
            mock_counter.labels.return_value = Mock(inc=Mock())
            mock_get_counter.return_value = mock_counter

            tracked = TrackedDict(sample_data, is_metrics=True)

            # Access multiple keys
            _ = tracked["metric1"]
            _ = tracked["metric2"]

            # Counter should only be created once
            mock_get_counter.assert_called_once_with(
                "omnicore_scenario_metrics_accessed_total",
                "Total accesses to scenario metrics",
                ["metric_name"],
            )

    def test_templates_counter_initialization(self, sample_data):
        """Test templates counter is initialized correctly"""
        with patch(
            "omnicore_engine.scenario_constants.get_or_create_counter"
        ) as mock_get_counter:
            mock_counter = Mock()
            mock_counter.labels.return_value = Mock(inc=Mock())
            mock_get_counter.return_value = mock_counter

            tracked = TrackedDict(sample_data, is_metrics=False)

            _ = tracked["metric1"]

            # Templates counter should be created
            mock_get_counter.assert_called_once_with(
                "omnicore_scenario_templates_accessed_total",
                "Total accesses to scenario templates",
                ["template_name"],
            )

    def test_iteration(self, sample_data):
        """Test iterating over TrackedDict"""
        tracked = TrackedDict(sample_data, is_metrics=True)

        keys = list(tracked)
        assert set(keys) == {"metric1", "metric2", "metric3"}

    def test_len(self, sample_data):
        """Test len() on TrackedDict"""
        tracked = TrackedDict(sample_data, is_metrics=True)
        assert len(tracked) == 3

        empty_tracked = TrackedDict({}, is_metrics=False)
        assert len(empty_tracked) == 0

    def test_immutability(self, sample_data):
        """Test that TrackedDict data cannot be modified externally"""
        tracked = TrackedDict(sample_data, is_metrics=True)

        # Original data modification shouldn't affect TrackedDict
        sample_data["metric1"] = 999

        with patch(
            "omnicore_engine.scenario_constants.get_or_create_counter"
        ) as mock_get_counter:
            mock_counter = Mock()
            mock_counter.labels.return_value = Mock(inc=Mock())
            mock_get_counter.return_value = mock_counter

            assert tracked["metric1"] == 100  # Original value preserved

    def test_set_counter_class(self, mock_counter_class):
        """Test setting a custom counter class"""
        TrackedDict.set_counter_class(mock_counter_class)
        assert TrackedDict._counter_class == mock_counter_class

    def test_thread_safety(self, sample_data):
        """Test that counter initialization is thread-safe"""
        with patch(
            "omnicore_engine.scenario_constants.get_or_create_counter"
        ) as mock_get_counter:
            mock_counter = Mock()
            mock_counter.labels.return_value = Mock(inc=Mock())
            mock_get_counter.return_value = mock_counter

            tracked = TrackedDict(sample_data, is_metrics=True)

            # Simulate concurrent access
            def access_key():
                _ = tracked["metric1"]

            threads = [threading.Thread(target=access_key) for _ in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            # Counter should still only be created once
            assert mock_get_counter.call_count == 1

    def test_mapping_protocol(self, sample_data):
        """Test that TrackedDict implements Mapping protocol"""
        from collections.abc import Mapping

        tracked = TrackedDict(sample_data, is_metrics=True)
        assert isinstance(tracked, Mapping)

        # Test that it supports common mapping operations
        assert "metric1" in tracked
        assert "nonexistent" not in tracked
        assert list(tracked.keys()) == list(sample_data.keys())


class TestIntegration:
    """Integration tests for the module"""

    def test_scenario_with_tracked_metrics(self):
        """Test using TrackedDict with scenario metrics"""
        # Create scenario metrics
        metrics = {
            "latency": ScenarioMetric(
                description="Response latency",
                default_value=100.0,
                unit="ms",
                range=[0.0, 1000.0],
                aggregation_method="average",
            ).dict(),
            "throughput": ScenarioMetric(
                description="Request throughput",
                default_value=1000.0,
                unit="req/s",
                range=[0.0, 10000.0],
                aggregation_method="sum",
            ).dict(),
        }

        with patch(
            "omnicore_engine.scenario_constants.get_or_create_counter"
        ) as mock_get_counter:
            mock_counter = Mock()
            mock_counter.labels.return_value = Mock(inc=Mock())
            mock_get_counter.return_value = mock_counter

            tracked_metrics = TrackedDict(metrics, is_metrics=True)

            # Access metrics
            latency_config = tracked_metrics["latency"]
            assert latency_config["description"] == "Response latency"
            assert latency_config["default_value"] == 100.0

            # Verify tracking
            mock_counter.labels.assert_called_with(metric_name="latency")

    def test_scenario_with_tracked_templates(self):
        """Test using TrackedDict with scenario templates"""
        templates = {
            "high_load": ScenarioTemplate(
                impact=0.9,
                label="High Load Scenario",
                active=True,
                description="Simulates high system load",
                priority=0.8,
            ).dict(),
            "failure": ScenarioTemplate(
                impact=1.0,
                label="System Failure",
                active=False,
                description="Simulates system failure",
                priority=1.0,
            ).dict(),
        }

        with patch(
            "omnicore_engine.scenario_constants.get_or_create_counter"
        ) as mock_get_counter:
            mock_counter = Mock()
            mock_counter.labels.return_value = Mock(inc=Mock())
            mock_get_counter.return_value = mock_counter

            tracked_templates = TrackedDict(templates, is_metrics=False)

            # Access templates
            high_load = tracked_templates["high_load"]
            assert high_load["label"] == "High Load Scenario"
            assert high_load["impact"] == 0.9

            # Verify tracking uses template counter
            mock_get_counter.assert_called_with(
                "omnicore_scenario_templates_accessed_total",
                "Total accesses to scenario templates",
                ["template_name"],
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
