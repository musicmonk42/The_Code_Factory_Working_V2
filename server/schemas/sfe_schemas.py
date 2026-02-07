# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Self-Fixing Engineer specific schemas.

Request and response models for SFE control endpoints.
"""

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ArbiterCommand(str, Enum):
    """Arbiter AI commands."""
    START = "start"
    STOP = "stop"
    PAUSE = "pause"
    RESUME = "resume"
    CONFIGURE = "configure"
    STATUS = "status"


class ArbiterControlRequest(BaseModel):
    """Request to control Arbiter AI."""
    command: ArbiterCommand = Field(..., description="Command to execute")
    job_id: Optional[str] = Field(None, description="Job ID (if applicable)")
    config: Optional[Dict[str, Any]] = Field(None, description="Configuration for command")


class ArenaCompetitionRequest(BaseModel):
    """Request to trigger arena competition."""
    problem_type: str = Field(..., description="Type of problem (bug_fix, optimization, refactor)")
    code_path: str = Field(..., description="Path to code for competition")
    agents: Optional[List[str]] = Field(None, description="Specific agents to compete")
    rounds: int = Field(3, ge=1, le=10, description="Number of competition rounds")
    evaluation_criteria: List[str] = Field(
        ["correctness", "performance", "maintainability"],
        description="Evaluation criteria"
    )


class BugDetectionRequest(BaseModel):
    """Request for bug detection."""
    code_path: str = Field(..., description="Path to code to analyze")
    scan_depth: str = Field("standard", description="Scan depth (quick, standard, deep)")
    include_potential: bool = Field(False, description="Include potential issues")


class BugAnalysisRequest(BaseModel):
    """Request for bug analysis."""
    bug_id: str = Field(..., description="Bug identifier")
    include_root_cause: bool = Field(True, description="Perform root cause analysis")
    suggest_fixes: bool = Field(True, description="Generate fix suggestions")


class BugPrioritizationRequest(BaseModel):
    """Request to prioritize bugs."""
    job_id: str = Field(..., description="Job identifier")
    criteria: Optional[List[str]] = Field(
        None, 
        description="Prioritization criteria (severity, impact, effort)"
    )


class CodebaseAnalysisRequest(BaseModel):
    """Request for deep codebase analysis."""
    code_path: str = Field(..., description="Path to codebase")
    analysis_type: List[str] = Field(
        ["structure", "dependencies", "complexity", "quality"],
        description="Types of analysis to perform"
    )
    generate_report: bool = Field(True, description="Generate detailed report")


class KnowledgeGraphQuery(BaseModel):
    """Query for knowledge graph."""
    query_type: str = Field(..., description="Query type (entity, relationship, pattern)")
    query: str = Field(..., description="Query string or pattern")
    depth: int = Field(1, ge=1, le=5, description="Traversal depth")
    limit: int = Field(100, ge=1, le=1000, description="Max results")


class KnowledgeGraphUpdate(BaseModel):
    """Update to knowledge graph."""
    operation: str = Field(..., description="Operation (add_node, add_edge, update, delete)")
    entity_type: str = Field(..., description="Entity type")
    entity_data: Dict[str, Any] = Field(..., description="Entity data")


class SandboxExecutionRequest(BaseModel):
    """Request to execute code in sandbox."""
    code: str = Field(..., description="Code to execute")
    language: str = Field("python", description="Programming language")
    timeout: int = Field(30, ge=1, le=300, description="Execution timeout (seconds)")
    resource_limits: Optional[Dict[str, Any]] = Field(
        None,
        description="Resource limits (memory, cpu)"
    )


class ComplianceCheckRequest(BaseModel):
    """Request for compliance checking."""
    code_path: str = Field(..., description="Path to code to check")
    standards: List[str] = Field(
        ["SOC2", "HIPAA", "GDPR"],
        description="Compliance standards to check"
    )
    generate_report: bool = Field(True, description="Generate compliance report")


class DLTAuditQuery(BaseModel):
    """Query for blockchain audit logs."""
    start_block: Optional[int] = Field(None, description="Starting block number")
    end_block: Optional[int] = Field(None, description="Ending block number")
    transaction_type: Optional[str] = Field(None, description="Filter by transaction type")
    limit: int = Field(100, ge=1, le=1000, description="Max results")


class SIEMConfigRequest(BaseModel):
    """Request to configure SIEM integration."""
    siem_type: str = Field(..., description="SIEM type (splunk, qradar, sentinel)")
    endpoint: str = Field(..., description="SIEM endpoint URL")
    api_key: Optional[str] = Field(None, description="SIEM API key")
    export_config: Dict[str, Any] = Field(..., description="Export configuration")


class RLEnvironmentStatus(BaseModel):
    """Status of RL environment."""
    environment_id: str = Field(..., description="Environment identifier")
    status: str = Field(..., description="Status (running, paused, stopped)")
    episodes: int = Field(..., description="Total episodes")
    average_reward: float = Field(..., description="Average reward")
    agent_performance: Dict[str, Any] = Field(..., description="Agent performance metrics")


class ImportFixRequest(BaseModel):
    """Request to fix imports."""
    code_path: str = Field(..., description="Path to code with import issues")
    auto_install: bool = Field(False, description="Auto-install missing packages")
    fix_style: bool = Field(True, description="Fix import style issues")


class ArbiterStatus(BaseModel):
    """Arbiter AI status."""
    status: str = Field(..., description="Arbiter status (active, idle, error)")
    active_jobs: List[str] = Field(..., description="Active job IDs")
    queue_depth: int = Field(..., description="Queue depth")
    agent_count: int = Field(..., description="Number of active agents")
    performance_metrics: Dict[str, Any] = Field(..., description="Performance metrics")


class ArenaResult(BaseModel):
    """Arena competition result."""
    competition_id: str = Field(..., description="Competition identifier")
    winner: str = Field(..., description="Winning agent")
    results: List[Dict[str, Any]] = Field(..., description="Detailed results")
    evaluation_scores: Dict[str, float] = Field(..., description="Evaluation scores")
    selected_solution: Optional[str] = Field(None, description="Selected solution path")


class BugReport(BaseModel):
    """Bug detection report."""
    bug_id: str = Field(..., description="Bug identifier")
    severity: str = Field(..., description="Bug severity (critical, high, medium, low)")
    type: str = Field(..., description="Bug type")
    location: str = Field(..., description="Bug location")
    description: str = Field(..., description="Bug description")
    impact: Optional[str] = Field(None, description="Impact assessment")
    fix_difficulty: Optional[str] = Field(None, description="Fix difficulty")


class CodebaseAnalysisReport(BaseModel):
    """Codebase analysis report."""
    analysis_id: str = Field(..., description="Analysis identifier")
    code_path: str = Field(..., description="Analyzed code path")
    summary: Dict[str, Any] = Field(..., description="Analysis summary")
    findings: List[Dict[str, Any]] = Field(..., description="Detailed findings")
    recommendations: List[str] = Field(..., description="Recommendations")
    metrics: Dict[str, Any] = Field(..., description="Code metrics")
