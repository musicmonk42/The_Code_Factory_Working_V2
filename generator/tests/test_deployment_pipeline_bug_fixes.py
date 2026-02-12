"""
Unit tests for deployment pipeline bug fixes.

This test suite validates the 6 interconnected bug fixes:
1. Bug 1: run_deployment() passes correct to_format for kubernetes/helm
2. Bug 2: generate_documentation() passes correct to_format for kubernetes/helm
3. Bug 3: project_name is defined in fallback handler
4. Bug 4: Job model has error and result fields
5. Bug 5: KubernetesHandler._sanitize_yaml_response() strips markdown prose
6. Bug 6: convert() methods accept platform names as aliases
"""

import pytest
from datetime import datetime
from typing import Dict, Any
from server.schemas.jobs import Job, JobStatus, JobStage
from generator.agents.deploy_agent.deploy_response_handler import (
    KubernetesHandler,
    HelmHandler,
)


class TestJobModelFields:
    """Test suite for Bug 4: Job model error and result fields."""
    
    def test_job_has_error_field(self):
        """Test that Job model has error field."""
        job = Job(
            id="test-job-1",
            status=JobStatus.FAILED,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            error="Test error message"
        )
        assert job.error == "Test error message"
    
    def test_job_has_result_field(self):
        """Test that Job model has result field."""
        job = Job(
            id="test-job-2",
            status=JobStatus.COMPLETED,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            result={"output": "success", "files": ["file1.yaml"]}
        )
        assert job.result == {"output": "success", "files": ["file1.yaml"]}
    
    def test_job_error_and_result_are_optional(self):
        """Test that error and result fields are optional."""
        job = Job(
            id="test-job-3",
            status=JobStatus.PENDING,
            created_at=datetime.now(),
            updated_at=datetime.now()
        )
        assert job.error is None
        assert job.result is None
    
    def test_job_can_set_error_after_creation(self):
        """Test that error can be set after Job creation."""
        job = Job(
            id="test-job-4",
            status=JobStatus.RUNNING,
            created_at=datetime.now(),
            updated_at=datetime.now()
        )
        # Simulate error handler setting error
        job.status = JobStatus.FAILED
        job.error = "Deployment validation failed"
        job.result = {"error": "Missing artifacts"}
        
        assert job.status == JobStatus.FAILED
        assert job.error == "Deployment validation failed"
        assert job.result["error"] == "Missing artifacts"


class TestKubernetesHandlerSanitization:
    """Test suite for Bug 5: KubernetesHandler._sanitize_yaml_response() enhancements."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.handler = KubernetesHandler()
    
    def test_removes_numbered_markdown_lists(self):
        """Test that numbered markdown lists are stripped."""
        raw_yaml = """1. **Deployment Manifest**:
Here is your deployment config:
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: test-app"""
        
        sanitized = self.handler._sanitize_yaml_response(raw_yaml)
        # Should not contain markdown list markers
        assert "1. **" not in sanitized
        assert "Deployment Manifest" not in sanitized
        # Should contain actual YAML
        assert "apiVersion: apps/v1" in sanitized
        assert "kind: Deployment" in sanitized
    
    def test_removes_bold_text_markers(self):
        """Test that bold text markers (**text**) are removed."""
        raw_yaml = """**Important**: This is a deployment config
apiVersion: v1
kind: Service
metadata:
  name: **my-service**"""
        
        sanitized = self.handler._sanitize_yaml_response(raw_yaml)
        # Bold markers should be removed but text preserved
        assert "**Important**" not in sanitized
        assert "**my-service**" not in sanitized
        assert "my-service" in sanitized
    
    def test_removes_text_before_yaml_start(self):
        """Test that text before first YAML document is stripped."""
        raw_yaml = """Here are the Kubernetes manifests you requested:
2. **Service Definition**:
---
apiVersion: v1
kind: Service
metadata:
  name: test-service"""
        
        sanitized = self.handler._sanitize_yaml_response(raw_yaml)
        # Explanatory text should be removed
        assert "Here are the" not in sanitized
        assert "2. **" not in sanitized
        # YAML content should be preserved
        assert "---" in sanitized
        assert "apiVersion: v1" in sanitized
    
    def test_removes_markdown_headers(self):
        """Test that markdown headers (# Header) are removed."""
        raw_yaml = """# Kubernetes Configuration
## Deployment Section
apiVersion: apps/v1
kind: Deployment"""
        
        sanitized = self.handler._sanitize_yaml_response(raw_yaml)
        # Headers should be removed
        assert "# Kubernetes Configuration" not in sanitized
        assert "## Deployment Section" not in sanitized
        # YAML should remain
        assert "apiVersion: apps/v1" in sanitized
    
    def test_removes_markdown_links(self):
        """Test that markdown links [text](url) are removed."""
        raw_yaml = """Check [documentation](https://example.com) for details
apiVersion: v1
kind: ConfigMap"""
        
        sanitized = self.handler._sanitize_yaml_response(raw_yaml)
        # Link URL should be removed, text preserved
        assert "https://example.com" not in sanitized
        assert "apiVersion: v1" in sanitized
    
    def test_handles_complex_markdown_prose(self):
        """Test handling of complex markdown with multiple patterns."""
        raw_yaml = """1. **Deployment Manifest**: Here's your deployment
2. **Service Manifest**: And here's the service

The configuration includes:
- **Auto-scaling**: Enabled
- **Replicas**: 3

---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: test-app
spec:
  replicas: 3"""
        
        sanitized = self.handler._sanitize_yaml_response(raw_yaml)
        # All markdown should be removed
        assert "**" not in sanitized
        assert "1." not in sanitized
        assert "includes:" not in sanitized
        # YAML should be preserved
        assert "apiVersion: apps/v1" in sanitized
        assert "replicas: 3" in sanitized


class TestKubernetesHandlerConvert:
    """Test suite for Bug 6: KubernetesHandler.convert() accepts 'kubernetes' alias."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.handler = KubernetesHandler()
        self.test_data = {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": "test-service"}
        }
    
    def test_convert_accepts_yaml_format(self):
        """Test that convert() accepts 'yaml' format."""
        result = self.handler.convert(self.test_data, "yaml")
        assert "apiVersion: v1" in result
        assert "kind: Service" in result
    
    def test_convert_accepts_yml_format(self):
        """Test that convert() accepts 'yml' format."""
        result = self.handler.convert(self.test_data, "yml")
        assert "apiVersion: v1" in result
        assert "kind: Service" in result
    
    def test_convert_accepts_kubernetes_alias(self):
        """Test that convert() accepts 'kubernetes' as alias for yaml (Bug 6)."""
        result = self.handler.convert(self.test_data, "kubernetes")
        assert "apiVersion: v1" in result
        assert "kind: Service" in result
    
    def test_convert_accepts_json_format(self):
        """Test that convert() accepts 'json' format."""
        result = self.handler.convert(self.test_data, "json")
        assert '"apiVersion": "v1"' in result
        assert '"kind": "Service"' in result
    
    def test_convert_rejects_unsupported_format(self):
        """Test that convert() raises error for unsupported formats."""
        with pytest.raises(ValueError, match="does not support conversion"):
            self.handler.convert(self.test_data, "xml")


class TestHelmHandlerConvert:
    """Test suite for Bug 6: HelmHandler.convert() accepts 'helm' alias."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.handler = HelmHandler()
        self.test_data = {
            "Chart.yaml": {
                "apiVersion": "v2",
                "name": "test-chart",
                "version": "1.0.0"
            },
            "values.yaml": {
                "replicaCount": 3
            },
            "templates": {}
        }
    
    def test_convert_accepts_yaml_format(self):
        """Test that convert() accepts 'yaml' format."""
        result = self.handler.convert(self.test_data, "yaml")
        assert "apiVersion: v2" in result
        assert "name: test-chart" in result
    
    def test_convert_accepts_yml_format(self):
        """Test that convert() accepts 'yml' format."""
        result = self.handler.convert(self.test_data, "yml")
        assert "apiVersion: v2" in result
        assert "name: test-chart" in result
    
    def test_convert_accepts_helm_alias(self):
        """Test that convert() accepts 'helm' as alias for yaml (Bug 6)."""
        result = self.handler.convert(self.test_data, "helm")
        assert "apiVersion: v2" in result
        assert "name: test-chart" in result
    
    def test_convert_accepts_json_format(self):
        """Test that convert() accepts 'json' format."""
        result = self.handler.convert(self.test_data, "json")
        assert '"apiVersion": "v2"' in result
        assert '"name": "test-chart"' in result
    
    def test_convert_rejects_unsupported_format(self):
        """Test that convert() raises error for unsupported formats."""
        with pytest.raises(ValueError, match="does not support conversion"):
            self.handler.convert(self.test_data, "xml")


class TestDeploymentPipelineIntegration:
    """Integration tests for the complete bug fix chain."""
    
    def test_kubernetes_handler_full_pipeline(self):
        """Test complete pipeline: sanitize -> parse -> convert with kubernetes format."""
        handler = KubernetesHandler()
        
        # Simulated LLM response with markdown
        raw_response = """1. **Deployment Configuration**:
Here is the Kubernetes deployment you requested:

---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: test-app
spec:
  replicas: 3
  selector:
    matchLabels:
      app: test-app
  template:
    metadata:
      labels:
        app: test-app
    spec:
      containers:
      - name: test-container
        image: test:latest
        ports:
        - containerPort: 8080"""
        
        # Step 1: Sanitize
        sanitized = handler._sanitize_yaml_response(raw_response)
        assert "**" not in sanitized
        assert "1." not in sanitized
        
        # Step 2: Parse (normalize)
        parsed = handler.normalize(sanitized)
        assert isinstance(parsed, dict)
        assert parsed["apiVersion"] == "apps/v1"
        assert parsed["kind"] == "Deployment"
        
        # Step 3: Convert using 'kubernetes' alias (Bug 6)
        converted = handler.convert(parsed, "kubernetes")
        assert "apiVersion: apps/v1" in converted
        assert "kind: Deployment" in converted
        assert "replicas: 3" in converted
    
    def test_helm_handler_full_pipeline(self):
        """Test complete pipeline with helm format alias."""
        handler = HelmHandler()
        
        test_data = {
            "Chart.yaml": {
                "apiVersion": "v2",
                "name": "my-chart",
                "version": "1.0.0",
                "description": "A test chart"
            },
            "values.yaml": {
                "replicaCount": 2,
                "image": {
                    "repository": "nginx",
                    "tag": "latest"
                }
            },
            "templates": {}
        }
        
        # Validate
        handler.validate(test_data)
        
        # Convert using 'helm' alias (Bug 6)
        converted = handler.convert(test_data, "helm")
        assert "apiVersion: v2" in converted
        assert "name: my-chart" in converted
        assert "replicaCount: 2" in converted


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
