# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Test suite for deployment pipeline fixes (Issues 1-3).

Tests the fixes for:
1. LLM Output Contains Explanatory Text Instead of Dockerfile
2. Multi-Document YAML Parsing Fails
3. Helm Templates with Jinja/Go Syntax Fail YAML Parsing
"""

import pytest
from generator.agents.deploy_agent.deploy_response_handler import (
    extract_config_from_response,
    YAMLHandler,
)


class TestIssue1DockerfileExtraction:
    """Test Issue 1: LLM Output Contains Explanatory Text Instead of Dockerfile."""

    def test_dockerfile_with_preamble(self):
        """Test extracting Dockerfile when LLM adds preamble text."""
        raw = """To create a production-ready Dockerfile according to the guidelines you've specified:

FROM python:3.11-slim
WORKDIR /app
RUN pip install fastapi
CMD ["python", "app.py"]"""
        
        extracted = extract_config_from_response(raw, "dockerfile")
        
        # Should extract from FROM onwards
        assert extracted.startswith("FROM python:3.11-slim")
        assert "WORKDIR /app" in extracted
        assert "RUN pip install fastapi" in extracted
        # Should not contain preamble
        assert "To create a production-ready" not in extracted
    
    def test_dockerfile_with_trailing_explanation(self):
        """Test extracting Dockerfile when LLM adds trailing explanation."""
        raw = """FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
CMD ["python", "app.py"]

This Dockerfile follows best practices by using a slim base image and non-root user."""
        
        extracted = extract_config_from_response(raw, "dockerfile")
        
        # Should extract Dockerfile content
        assert extracted.startswith("FROM python:3.11-slim")
        assert "CMD" in extracted
        # Should strip trailing explanation
        assert "This Dockerfile follows" not in extracted
        assert "best practices" not in extracted
    
    def test_dockerfile_with_both_preamble_and_trailing(self):
        """Test extracting Dockerfile with both preamble and trailing text."""
        raw = """Here is a production-ready Dockerfile:

FROM node:18-alpine
WORKDIR /app
COPY package*.json ./
RUN npm ci --only=production
COPY . .
EXPOSE 3000
CMD ["node", "server.js"]

You can build this with: docker build -t myapp ."""
        
        extracted = extract_config_from_response(raw, "dockerfile")
        
        # Should extract clean Dockerfile
        assert extracted.startswith("FROM node:18-alpine")
        assert "EXPOSE 3000" in extracted
        # Should not contain explanatory text
        assert "Here is a production" not in extracted
        assert "You can build" not in extracted
    
    def test_dockerfile_already_clean(self):
        """Test that clean Dockerfile is returned as-is."""
        raw = """FROM python:3.11
WORKDIR /app
CMD ["python", "app.py"]"""
        
        extracted = extract_config_from_response(raw, "dockerfile")
        
        # Should return unchanged
        assert extracted == raw
    
    def test_dockerfile_with_arg_instruction(self):
        """Test extracting Dockerfile starting with ARG instruction."""
        raw = """Let me create a Dockerfile with build arguments:

ARG BASE_IMAGE=python:3.11
FROM ${BASE_IMAGE}
WORKDIR /app
CMD ["python", "app.py"]"""
        
        extracted = extract_config_from_response(raw, "dockerfile")
        
        # Should extract from ARG onwards
        assert extracted.startswith("ARG BASE_IMAGE=python:3.11")
        assert "FROM ${BASE_IMAGE}" in extracted
        # Should not contain preamble
        assert "Let me create" not in extracted


class TestIssue2MultiDocumentYAML:
    """Test Issue 2: Multi-Document YAML Parsing."""

    def test_multi_document_kubernetes_yaml(self):
        """Test parsing multi-document YAML with --- separators."""
        raw = """---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: myapp
spec:
  replicas: 2
---
apiVersion: v1
kind: Service
metadata:
  name: myapp
spec:
  type: ClusterIP
  ports:
    - port: 80"""
        
        handler = YAMLHandler()
        result = handler.normalize(raw)
        
        # Should parse as list of documents
        assert isinstance(result, list)
        assert len(result) == 2
        
        # Check first document
        assert result[0]["kind"] == "Deployment"
        assert result[0]["metadata"]["name"] == "myapp"
        
        # Check second document
        assert result[1]["kind"] == "Service"
        assert result[1]["spec"]["type"] == "ClusterIP"
    
    def test_single_document_yaml(self):
        """Test parsing single document YAML."""
        raw = """apiVersion: v1
kind: ConfigMap
metadata:
  name: app-config
data:
  key: value"""
        
        handler = YAMLHandler()
        result = handler.normalize(raw)
        
        # Should parse as single dict
        assert isinstance(result, dict)
        assert result["kind"] == "ConfigMap"
        assert result["metadata"]["name"] == "app-config"
    
    def test_three_document_yaml(self):
        """Test parsing three documents in one YAML."""
        raw = """---
apiVersion: v1
kind: Namespace
metadata:
  name: production
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: app
  namespace: production
---
apiVersion: v1
kind: Service
metadata:
  name: app-svc
  namespace: production"""
        
        handler = YAMLHandler()
        result = handler.normalize(raw)
        
        # Should parse as list of 3 documents
        assert isinstance(result, list)
        assert len(result) == 3
        assert result[0]["kind"] == "Namespace"
        assert result[1]["kind"] == "Deployment"
        assert result[2]["kind"] == "Service"


class TestIssue3HelmTemplates:
    """Test Issue 3: Helm Templates with Jinja/Go Syntax."""

    def test_helm_template_with_values(self):
        """Test that Helm template with .Values is not parsed as YAML."""
        raw = """apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ .Values.appName }}
spec:
  replicas: {{ .Values.replicas }}
  template:
    spec:
      containers:
        - name: app
          image: {{ .Values.image.repository }}:{{ .Values.image.tag }}"""
        
        handler = YAMLHandler()
        result = handler.normalize(raw)
        
        # Should detect as Helm template and return special structure
        assert isinstance(result, dict)
        assert result.get("_helm_template") is True
        assert "_raw_content" in result
        assert "{{ .Values.appName }}" in result["_raw_content"]
    
    def test_helm_template_with_range(self):
        """Test Helm template with range loop syntax."""
        raw = """apiVersion: v1
kind: ConfigMap
metadata:
  name: app-config
data:
  {{- range $key, $value := .Values.env }}
  {{ $key }}: {{ $value | quote }}
  {{- end }}"""
        
        handler = YAMLHandler()
        result = handler.normalize(raw)
        
        # Should detect as Helm template
        assert isinstance(result, dict)
        assert result.get("_helm_template") is True
        assert "range $key, $value" in result["_raw_content"]
    
    def test_helm_template_with_if_condition(self):
        """Test Helm template with conditional syntax."""
        raw = """apiVersion: apps/v1
kind: Deployment
spec:
  {{- if .Values.autoscaling.enabled }}
  replicas: 1
  {{- else }}
  replicas: {{ .Values.replicas }}
  {{- end }}"""
        
        handler = YAMLHandler()
        result = handler.normalize(raw)
        
        # Should detect as Helm template
        assert isinstance(result, dict)
        assert result.get("_helm_template") is True
        assert "{{- if .Values.autoscaling.enabled }}" in result["_raw_content"]
    
    def test_helm_template_with_include(self):
        """Test Helm template with include directive."""
        raw = """apiVersion: v1
kind: Service
metadata:
  name: {{ include "mychart.fullname" . }}
  labels:
    {{- include "mychart.labels" . | nindent 4 }}
spec:
  type: {{ .Values.service.type }}"""
        
        handler = YAMLHandler()
        result = handler.normalize(raw)
        
        # Should detect as Helm template
        assert isinstance(result, dict)
        assert result.get("_helm_template") is True
        assert 'include "mychart.fullname"' in result["_raw_content"]
    
    def test_regular_yaml_not_detected_as_helm(self):
        """Test that regular YAML is not incorrectly detected as Helm template."""
        raw = """apiVersion: v1
kind: ConfigMap
metadata:
  name: app-config
data:
  key1: value1
  key2: value2"""
        
        handler = YAMLHandler()
        result = handler.normalize(raw)
        
        # Should parse as regular YAML, not Helm template
        assert isinstance(result, dict)
        assert result.get("_helm_template") is not True
        assert result["kind"] == "ConfigMap"
        assert result["metadata"]["name"] == "app-config"
    
    def test_helm_values_yaml_without_templates(self):
        """Test that values.yaml (without templates) is parsed normally."""
        raw = """replicaCount: 2
image:
  repository: myapp
  tag: "1.0.0"
  pullPolicy: IfNotPresent
service:
  type: ClusterIP
  port: 80"""
        
        handler = YAMLHandler()
        result = handler.normalize(raw)
        
        # Should parse as regular YAML (values.yaml doesn't have templates)
        assert isinstance(result, dict)
        assert result.get("_helm_template") is not True
        assert result["replicaCount"] == 2
        assert result["image"]["repository"] == "myapp"
