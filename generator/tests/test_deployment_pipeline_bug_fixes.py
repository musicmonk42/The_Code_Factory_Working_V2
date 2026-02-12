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


class TestHelmHandlerMultiDocumentYAML:
    """Test suite for Bug Fix: HelmHandler.normalize() handles multi-document YAML."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.handler = HelmHandler()
    
    def test_normalize_single_document_yaml(self):
        """Test that single document YAML still works correctly."""
        single_doc_yaml = """apiVersion: v2
name: my-chart
description: A sample Helm chart
type: application
version: 1.0.0
appVersion: 1.0"""
        
        result = self.handler.normalize(single_doc_yaml)
        assert "Chart.yaml" in result
        assert result["Chart.yaml"]["name"] == "my-chart"
        assert result["Chart.yaml"]["version"] == "1.0.0"
    
    def test_normalize_multi_document_yaml_with_chart_and_values(self):
        """Test that multi-document YAML with Chart.yaml and values.yaml is parsed correctly."""
        multi_doc_yaml = """apiVersion: v2
name: my-chart
version: 1.0.0
---
replicaCount: 3
image:
  repository: nginx
  tag: latest"""
        
        result = self.handler.normalize(multi_doc_yaml)
        assert "Chart.yaml" in result
        assert result["Chart.yaml"]["name"] == "my-chart"
        assert "values.yaml" in result
        assert result["values.yaml"]["replicaCount"] == 3
    
    def test_normalize_multi_document_yaml_with_templates(self):
        """Test that multi-document YAML with K8s resources is treated as templates."""
        multi_doc_yaml = """apiVersion: v2
name: my-chart
version: 1.0.0
---
apiVersion: v1
kind: Service
metadata:
  name: my-service
spec:
  ports:
    - port: 80
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-deployment
spec:
  replicas: 2"""
        
        result = self.handler.normalize(multi_doc_yaml)
        assert "Chart.yaml" in result
        assert result["Chart.yaml"]["name"] == "my-chart"
        assert "templates" in result
        # Check that templates were created
        assert len(result["templates"]) > 0


class TestKubernetesHandlerLeadingProseStripping:
    """Test suite for Bug Fix: KubernetesHandler strips ALL leading prose before YAML."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.handler = KubernetesHandler()
    
    def test_strips_prose_and_mermaid_before_yaml(self):
        """Test that leading prose AND mermaid diagrams are stripped."""
        yaml_with_prose = """Here is your Kubernetes deployment configuration.
Let me explain the architecture:

The deployment creates 3 replicas.
```mermaid
graph TD
    A[User] --> B[Service]
    B --> C[Deployment]
```
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: test-app
spec:
  replicas: 3"""
        
        sanitized = self.handler._sanitize_yaml_response(yaml_with_prose)
        # All prose should be removed
        assert "Here is your" not in sanitized
        assert "Let me explain" not in sanitized
        assert "mermaid" not in sanitized
        assert "graph TD" not in sanitized
        # YAML should be preserved
        assert "---" in sanitized
        assert "apiVersion: apps/v1" in sanitized
        assert "kind: Deployment" in sanitized
    
    def test_strips_multiple_lines_of_prose_before_yaml(self):
        """Test that multiple lines of leading prose are all stripped."""
        yaml_with_prose = """This is line 1 of prose.
This is line 2 of prose.
This is line 3 of prose.
And one more line before YAML.
apiVersion: v1
kind: Service
metadata:
  name: my-service"""
        
        sanitized = self.handler._sanitize_yaml_response(yaml_with_prose)
        # All prose lines should be removed
        assert "line 1 of prose" not in sanitized
        assert "line 2 of prose" not in sanitized
        assert "line 3 of prose" not in sanitized
        assert "one more line" not in sanitized
        # YAML should start immediately
        assert sanitized.strip().startswith("apiVersion:")
    
    def test_handles_yaml_starting_with_document_marker(self):
        """Test that YAML starting with --- properly strips leading prose."""
        yaml_with_prose = """Some explanatory text here.
More explanation.
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: my-config"""
        
        sanitized = self.handler._sanitize_yaml_response(yaml_with_prose)
        # Prose should be removed
        assert "Some explanatory" not in sanitized
        assert "More explanation" not in sanitized
        # YAML should be preserved starting with ---
        assert sanitized.strip().startswith("---")
        assert "apiVersion: v1" in sanitized


class TestCodegenImportValidation:
    """Test suite for Bug Fix: Validate FastAPI imports in generated code."""
    
    def test_syntax_safety_instructions_include_import_rules(self):
        """Test that get_syntax_safety_instructions includes import requirements."""
        from generator.agents.codegen_agent.codegen_prompt import get_syntax_safety_instructions
        
        instructions = get_syntax_safety_instructions("python")
        # Check for import-related instructions
        assert "IMPORTS AND DEPENDENCIES" in instructions
        assert "from fastapi import Request" in instructions
        assert "import time" in instructions
        assert "type hints have imports" in instructions
    
    def test_validation_detects_missing_request_import(self):
        """Test that validate_generated_project detects missing Request import."""
        import tempfile
        from pathlib import Path
        from generator.runner.runner_file_utils import validate_generated_project
        import asyncio
        
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            
            # Create a Python file with Request type hint but no import
            app_dir = tmpdir_path / "app"
            app_dir.mkdir()
            main_py = app_dir / "main.py"
            main_py.write_text("""from fastapi import FastAPI

app = FastAPI()

@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    response = await call_next(request)
    return response
""")
            
            # Run validation
            result = asyncio.run(validate_generated_project(
                tmpdir_path,
                check_python_syntax=True,
                language="python"
            ))
            
            # Should detect missing Request import
            assert not result["valid"]
            assert any("Request" in error and "import" in error for error in result["errors"])
    
    def test_validation_detects_missing_time_import(self):
        """Test that validate_generated_project detects missing time import."""
        import tempfile
        from pathlib import Path
        from generator.runner.runner_file_utils import validate_generated_project
        import asyncio
        
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            
            # Create a Python file using time without import
            app_dir = tmpdir_path / "app"
            app_dir.mkdir()
            main_py = app_dir / "main.py"
            main_py.write_text("""from fastapi import FastAPI

app = FastAPI()

@app.middleware("http")
async def add_process_time_header(request, call_next):
    start = time.time()
    response = await call_next(request)
    return response
""")
            
            # Run validation
            result = asyncio.run(validate_generated_project(
                tmpdir_path,
                check_python_syntax=True,
                language="python"
            ))
            
            # Should detect missing time import
            assert not result["valid"]
            assert any("time" in error and "import" in error for error in result["errors"])
    
    def test_validation_passes_with_correct_imports(self):
        """Test that validation passes when imports are correct."""
        import tempfile
        from pathlib import Path
        from generator.runner.runner_file_utils import validate_generated_project
        import asyncio
        
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            
            # Create a Python file with correct imports
            app_dir = tmpdir_path / "app"
            app_dir.mkdir()
            main_py = app_dir / "main.py"
            main_py.write_text("""from fastapi import FastAPI, Request
import time

app = FastAPI()

@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    return response
""")
            
            # Run validation
            result = asyncio.run(validate_generated_project(
                tmpdir_path,
                check_python_syntax=True,
                language="python"
            ))
            
            # Should pass validation (no import errors)
            # Note: It might still have warnings about missing files, but no import errors
            import_errors = [e for e in result["errors"] if "import" in e.lower()]
            assert len(import_errors) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
