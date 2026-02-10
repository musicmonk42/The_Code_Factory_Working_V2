# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Generator module specific schemas.

Request and response models for generator agent endpoints.
"""

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class LLMProvider(str, Enum):
    """Supported LLM providers."""
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    XAI = "xai"
    OLLAMA = "ollama"


class GenerationLanguage(str, Enum):
    """Supported programming languages."""
    PYTHON = "python"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    GO = "go"
    JAVA = "java"
    RUST = "rust"


class ClarificationChannel(str, Enum):
    """Supported clarification channels."""
    CLI = "cli"
    GUI = "gui"
    VOICE = "voice"
    WEB = "web"
    SLACK = "slack"
    EMAIL = "email"
    SMS = "sms"


class CodegenRequest(BaseModel):
    """Request for code generation."""
    requirements: str = Field(..., description="Natural language requirements")
    language: GenerationLanguage = Field(GenerationLanguage.PYTHON, description="Target language")
    framework: Optional[str] = Field(None, description="Optional framework (e.g., FastAPI, React)")
    include_tests: bool = Field(True, description="Generate tests alongside code")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional metadata")


class TestgenRequest(BaseModel):
    """Request for test generation."""
    code_path: str = Field(..., description="Path to code files to test")
    test_type: str = Field("unit", description="Test type (unit, integration, e2e)")
    coverage_target: float = Field(80.0, ge=0, le=100, description="Target coverage percentage")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional metadata")


class DeployRequest(BaseModel):
    """Request for deployment configuration generation."""
    code_path: str = Field(..., description="Path to application code")
    platform: str = Field("docker", description="Deployment platform (docker, kubernetes, aws)")
    include_ci_cd: bool = Field(True, description="Include CI/CD configuration")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional metadata")


class DocgenRequest(BaseModel):
    """Request for documentation generation."""
    code_path: str = Field(..., description="Path to code to document")
    doc_type: str = Field("api", description="Documentation type (api, user, developer)")
    format: str = Field("markdown", description="Output format (markdown, html, pdf)")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional metadata")


class CritiqueRequest(BaseModel):
    """Request for security/code critique."""
    code_path: str = Field(..., description="Path to code to analyze")
    scan_types: List[str] = Field(
        ["security", "quality", "performance"], 
        description="Types of scans to run"
    )
    auto_fix: bool = Field(False, description="Automatically apply fixes")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional metadata")


class PipelineRequest(BaseModel):
    """Request for full generation pipeline."""
    readme_content: str = Field(..., description="README/requirements content")
    language: GenerationLanguage = Field(GenerationLanguage.PYTHON, description="Target language")
    include_tests: bool = Field(True, description="Generate tests")
    include_deployment: bool = Field(True, description="Generate deployment configs")
    include_docs: bool = Field(True, description="Generate documentation")
    run_critique: bool = Field(True, description="Run security/quality checks")
    output_dir: Optional[str] = Field(None, description="Optional subdirectory within generated/ for output (e.g., 'hello_generator')")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional metadata")


class ClarifyRequest(BaseModel):
    """Request for requirements clarification."""
    readme_content: Optional[str] = Field(None, description="README/requirements content (optional if files uploaded)")
    ambiguities: Optional[List[str]] = Field(None, description="Specific ambiguities to clarify")
    channel: Optional[ClarificationChannel] = Field(
        ClarificationChannel.CLI, 
        description="Clarification channel (cli, gui, voice, web, slack, email, sms). Default: cli"
    )
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional metadata")


class ClarificationResponseRequest(BaseModel):
    """Request body for submitting a clarification response or skipping clarification."""
    question_id: Optional[str] = Field(None, description="ID of the question being answered")
    response: Optional[str] = Field(None, description="User's response to the question")
    responses: Optional[Dict[str, str]] = Field(None, description="Bulk responses keyed by question ID")
    skip: bool = Field(False, description="If true, skip all clarification questions and resume pipeline")


class LLMConfigRequest(BaseModel):
    """Request to configure LLM provider."""
    provider: LLMProvider = Field(..., description="LLM provider to configure")
    api_key: Optional[str] = Field(None, description="API key (if required)")
    model: Optional[str] = Field(None, description="Specific model to use")
    config: Optional[Dict[str, Any]] = Field(None, description="Additional provider config")


class GeneratorAuditQuery(BaseModel):
    """Query parameters for generator audit logs."""
    start_time: Optional[str] = Field(None, description="Start timestamp (ISO 8601)")
    end_time: Optional[str] = Field(None, description="End timestamp (ISO 8601)")
    event_type: Optional[str] = Field(None, description="Filter by event type")
    job_id: Optional[str] = Field(None, description="Filter by job ID")
    limit: int = Field(100, ge=1, le=1000, description="Max results")


class GeneratorResponse(BaseModel):
    """Generic generator operation response."""
    job_id: str = Field(..., description="Job identifier")
    status: str = Field(..., description="Operation status")
    message: str = Field(..., description="Status message")
    output_path: Optional[str] = Field(None, description="Path to generated output")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional metadata")


class LLMProviderStatus(BaseModel):
    """Status of configured LLM providers."""
    active_provider: str = Field(..., description="Currently active provider")
    available_providers: List[str] = Field(..., description="Available providers")
    provider_configs: Dict[str, Dict[str, Any]] = Field(..., description="Provider configurations")
