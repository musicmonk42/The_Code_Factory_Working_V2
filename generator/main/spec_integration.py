# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Spec-Driven Generation Pipeline Integration.

This module integrates the new spec block, question loop, and validation
components into the existing generator workflow, ensuring proper routing
and orchestration.

Architecture:
    README → IntentParser → SpecBlock → QuestionLoop → SpecLock → 
    CodeGen → Validation → Output

Industry Standards:
    - OpenTelemetry distributed tracing
    - Prometheus metrics
    - Structured audit logging
    - Fail-fast error handling
    - Clear separation of concerns
"""

import logging
from pathlib import Path
from typing import Any, Dict, Optional

from opentelemetry import trace

from generator.intent_parser.spec_block import extract_spec_block, SpecBlock
from generator.intent_parser.question_loop import (
    run_question_loop,
    SpecLock,
)
from generator.main.validation import validate_generated_code, ValidationReport

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


class SpecDrivenPipeline:
    """
    Orchestrates spec-driven code generation pipeline.
    
    This class integrates spec block parsing, question loop gap-filling,
    and post-generation validation into a cohesive workflow.
    
    Usage:
        pipeline = SpecDrivenPipeline()
        
        # Phase 1: Parse and complete spec
        spec_lock = await pipeline.process_requirements(
            readme_content=readme,
            interactive=True
        )
        
        # Phase 2: Generate code (existing codegen agents)
        # ... code generation happens here ...
        
        # Phase 3: Validate output
        report = pipeline.validate_output(
            output_dir=Path("generated/my_app"),
            spec_lock=spec_lock
        )
        
        if not report.is_valid():
            raise ValidationError(report.to_text())
    """
    
    def __init__(self, job_id: Optional[str] = None):
        """
        Initialize spec-driven pipeline.
        
        Args:
            job_id: Optional job identifier for tracking
        """
        self.job_id = job_id or "unknown"
        logger.info(
            f"Initialized SpecDrivenPipeline",
            extra={"job_id": self.job_id}
        )
    
    async def process_requirements(
        self,
        readme_content: str,
        interactive: bool = False,
        output_path: Optional[Path] = None
    ) -> SpecLock:
        """
        Process README requirements into a complete SpecLock.
        
        This method:
        1. Extracts spec block from README (if present)
        2. Runs question loop to fill gaps
        3. Creates and saves SpecLock
        
        Args:
            readme_content: README file content
            interactive: If True, prompts user for missing fields
            output_path: Optional path to save spec.lock.yaml
            
        Returns:
            Complete SpecLock ready for generation
            
        Raises:
            ValueError: If spec cannot be completed in non-interactive mode
        """
        with tracer.start_as_current_span("process_requirements") as span:
            span.set_attribute("job_id", self.job_id)
            span.set_attribute("interactive", interactive)
            span.set_attribute("readme_length", len(readme_content))
            
            # Phase 1: Extract spec block
            logger.info(
                f"[{self.job_id}] Extracting spec block from README",
                extra={"job_id": self.job_id, "readme_length": len(readme_content)}
            )
            
            spec_block = extract_spec_block(readme_content)
            
            if spec_block:
                logger.info(
                    f"[{self.job_id}] Found spec block: "
                    f"project_type={spec_block.project_type}, "
                    f"complete={spec_block.is_complete()}",
                    extra={
                        "job_id": self.job_id,
                        "has_spec_block": True,
                        "project_type": spec_block.project_type,
                        "is_complete": spec_block.is_complete(),
                    }
                )
                span.set_attribute("spec_block_found", True)
                span.set_attribute("spec_complete", spec_block.is_complete())
            else:
                logger.info(
                    f"[{self.job_id}] No spec block found, creating empty spec",
                    extra={"job_id": self.job_id, "has_spec_block": False}
                )
                spec_block = SpecBlock()
                span.set_attribute("spec_block_found", False)
            
            # Phase 2: Run question loop to complete spec
            if not spec_block.is_complete():
                missing_fields = spec_block.missing_fields()
                logger.info(
                    f"[{self.job_id}] Spec incomplete, running question loop. "
                    f"Missing: {', '.join(missing_fields)}",
                    extra={
                        "job_id": self.job_id,
                        "missing_fields": missing_fields,
                        "interactive": interactive,
                    }
                )
                span.add_event("question_loop_started", {
                    "missing_fields_count": len(missing_fields),
                    "interactive": interactive,
                })
            
            # Determine output path for spec.lock.yaml
            if output_path is None and spec_block.output_dir:
                # Save next to generated output
                output_path = Path(spec_block.output_dir).parent / "spec.lock.yaml"
            
            spec_lock = run_question_loop(
                spec=spec_block,
                readme_content=readme_content,
                output_path=output_path,
                interactive=interactive
            )
            
            # GATING: Check if clarification is required before proceeding
            if spec_lock.requires_clarification:
                logger.warning(
                    f"[{self.job_id}] Spec incomplete: project_type missing or uncertain. "
                    f"Clarification required before code generation.",
                    extra={
                        "job_id": self.job_id,
                        "requires_clarification": True,
                        "project_type": spec_lock.project_type,
                    }
                )
                # In interactive mode, this should have been resolved
                # In non-interactive mode, we should not proceed
                if not interactive:
                    raise ValueError(
                        "Cannot proceed with code generation: project_type is missing or uncertain. "
                        "Please specify project_type explicitly in the spec block or run in interactive mode."
                    )
            
            logger.info(
                f"[{self.job_id}] Spec processing complete: "
                f"project_type={spec_lock.project_type}, "
                f"package={spec_lock.package_name}",
                extra={
                    "job_id": self.job_id,
                    "project_type": spec_lock.project_type,
                    "package_name": spec_lock.package_name,
                    "output_dir": spec_lock.output_dir,
                    "questions_answered": len(spec_lock.answered_questions),
                }
            )
            
            span.set_attribute("project_type", spec_lock.project_type)
            span.set_attribute("package_name", spec_lock.package_name)
            span.set_attribute("questions_answered", len(spec_lock.answered_questions))
            
            return spec_lock
    
    def validate_output(
        self,
        output_dir: Path,
        spec_lock: Optional[SpecLock] = None,
        language: str = "python"
    ) -> ValidationReport:
        """
        Validate generated code against contract and spec.
        
        Args:
            output_dir: Directory containing generated code
            spec_lock: Optional SpecLock to validate against
            language: Programming language (default: python)
            
        Returns:
            ValidationReport with results
        """
        with tracer.start_as_current_span("validate_output") as span:
            span.set_attribute("job_id", self.job_id)
            span.set_attribute("output_dir", str(output_dir))
            span.set_attribute("has_spec_lock", spec_lock is not None)
            
            logger.info(
                f"[{self.job_id}] Validating generated code at {output_dir}",
                extra={
                    "job_id": self.job_id,
                    "output_dir": str(output_dir),
                    "has_spec_lock": spec_lock is not None,
                }
            )
            
            # Convert SpecLock to dict for validation
            spec_dict = None
            if spec_lock:
                spec_dict = {
                    "project_type": spec_lock.project_type,
                    "package_name": spec_lock.package_name,
                    "output_dir": spec_lock.output_dir,
                    "interfaces": spec_lock.interfaces,
                    "dependencies": spec_lock.dependencies,
                }
            
            report = validate_generated_code(
                output_dir=output_dir,
                language=language,
                spec_block=spec_dict
            )
            
            logger.info(
                f"[{self.job_id}] Validation complete: "
                f"valid={report.is_valid()}, "
                f"passed={len(report.checks_passed)}/{len(report.checks_run)}",
                extra={
                    "job_id": self.job_id,
                    "valid": report.is_valid(),
                    "checks_run": len(report.checks_run),
                    "checks_passed": len(report.checks_passed),
                    "checks_failed": len(report.checks_failed),
                    "errors": report.errors,
                }
            )
            
            span.set_attribute("valid", report.is_valid())
            span.set_attribute("checks_run", len(report.checks_run))
            span.set_attribute("checks_passed", len(report.checks_passed))
            span.set_attribute("checks_failed", len(report.checks_failed))
            
            if not report.is_valid():
                span.add_event("validation_failed", {
                    "error_count": len(report.errors),
                    "failed_checks": report.checks_failed,
                })
            
            return report


def enhance_requirements_with_spec(
    requirements: Dict[str, Any],
    readme_content: str
) -> Dict[str, Any]:
    """
    Enhance parsed requirements dict with spec block data.
    
    This function is called by the existing IntentParser to inject
    spec block data into the requirements dict.
    
    Args:
        requirements: Existing requirements dict from IntentParser
        readme_content: Original README content
        
    Returns:
        Enhanced requirements dict with spec_block fields
    """
    # Extract spec block if not already present
    if not requirements.get("spec_block"):
        spec_block = extract_spec_block(readme_content)
        if spec_block:
            requirements["spec_block"] = spec_block.to_dict()
            requirements["has_spec_block"] = True
            
            # Override text-extracted values with spec block values
            if spec_block.project_type:
                requirements["project_type"] = spec_block.project_type
            
            if spec_block.interfaces:
                if spec_block.interfaces.http:
                    requirements["endpoints"] = spec_block.interfaces.http
                if spec_block.interfaces.events:
                    requirements["events"] = spec_block.interfaces.events
            
            if spec_block.dependencies:
                requirements["dependencies"] = spec_block.dependencies
            
            logger.info(
                f"Enhanced requirements with spec block: project_type={spec_block.project_type}",
                extra={
                    "has_spec_block": True,
                    "project_type": spec_block.project_type,
                }
            )
    
    return requirements


__all__ = [
    "SpecDrivenPipeline",
    "enhance_requirements_with_spec",
]
