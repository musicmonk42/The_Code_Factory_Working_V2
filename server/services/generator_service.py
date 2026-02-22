# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Service for interacting with the Generator module through OmniCore.

This service provides a mockable interface to the generator module for job creation,
file processing, and code generation tasks. ALL operations are routed through
OmniCore as the central coordinator.
"""

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class AgentNotReadyError(Exception):
    """Raised when agents are not ready for processing."""
    pass


class AgentLoadingTimeoutError(Exception):
    """Raised when agents fail to load within timeout period."""
    pass


class GeneratorService:
    """
    Service for interacting with the Generator (README-to-App Code Generator).

    This service acts as an abstraction layer for generator operations,
    providing methods for file upload, job creation, and generation tasks.
    All operations are routed through OmniCore's message bus and coordination layer.
    The implementation includes placeholder logic with extensible hooks for
    actual generator integration via OmniCore.
    """
    
    # Retry configuration for agent loading (configurable via environment variables)
    MAX_RETRY_ATTEMPTS = int(os.getenv("AGENT_RETRY_ATTEMPTS", "3"))  # Number of retry attempts after initial call
    RETRY_BASE_DELAY_SECONDS = int(os.getenv("AGENT_RETRY_BASE_DELAY", "5"))  # Base delay for exponential backoff
    RETRY_MAX_DELAY_SECONDS = int(os.getenv("AGENT_RETRY_MAX_DELAY", "30"))  # Maximum delay cap

    @staticmethod
    def _create_retryable_error(job_id: str, message: str) -> Dict[str, Any]:
        """
        Create a standardized retryable error response.
        
        Args:
            job_id: Job identifier
            message: Error message to display
            
        Returns:
            Dict with error status and retry flag
        """
        return {
            "status": "error",
            "retry": True,
            "message": message,
            "job_id": job_id,
        }

    def __init__(self, storage_path: Optional[Path] = None, omnicore_service=None):
        """
        Initialize the GeneratorService.

        Args:
            storage_path: Path for storing uploaded files. Defaults to ./uploads/
            omnicore_service: OmniCoreService instance for centralized routing
        """
        self.storage_path = storage_path or Path("./uploads")
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.omnicore_service = omnicore_service
        logger.info(f"GeneratorService initialized with storage: {self.storage_path}")

    async def save_upload(
        self, job_id: str, filename: str, content: bytes
    ) -> Dict[str, Any]:
        """
        Save an uploaded file for a job.

        Args:
            job_id: Unique job identifier
            filename: Original filename
            content: File content as bytes

        Returns:
            Dictionary with file metadata

        Example:
            >>> service = GeneratorService()
            >>> result = await service.save_upload("job-123", "README.md", b"content")
            >>> print(result['path'])
        """
        job_dir = self.storage_path / job_id
        job_dir.mkdir(parents=True, exist_ok=True)

        file_path = job_dir / filename
        file_path.write_bytes(content)

        logger.info(f"Saved file {filename} for job {job_id} at {file_path}")

        return {
            "filename": filename,
            "path": str(file_path),
            "size": len(content),
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
        }

    async def create_generation_job(
        self, job_id: str, files: List[str], metadata: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Create a generation job in the generator module via OmniCore.

        This method routes the job creation through OmniCore's message bus
        to the generator module.

        Args:
            job_id: Unique job identifier
            files: List of file paths to process
            metadata: Additional job metadata

        Returns:
            Job creation result with status

        Example integration:
            >>> # Route through OmniCore message bus
            >>> # from omnicore_engine.message_bus import publish_message
            >>> # await publish_message(topic='generator', payload={...})
        """
        logger.info(
            f"Creating generation job {job_id} with {len(files)} files via OmniCore"
        )

        # Route through OmniCore
        if self.omnicore_service:
            payload = {
                "action": "create_job",
                "job_id": job_id,
                "files": files,
                "metadata": metadata,
            }
            result = await self.omnicore_service.route_job(
                job_id=job_id,
                source_module="api",
                target_module="generator",
                payload=payload,
            )
            logger.info(f"Job {job_id} routed to generator via OmniCore")
            return result

        # Fallback if OmniCore not available
        logger.warning("OmniCore service not available, using direct fallback")
        return {
            "job_id": job_id,
            "status": "created",
            "message": "Generation job created successfully (direct fallback)",
            "files_count": len(files),
            "generator_module": "generator.runner",
        }

    async def get_job_status(self, job_id: str) -> Dict[str, Any]:
        """
        Get the status of a generator job via OmniCore.

        Args:
            job_id: Unique job identifier

        Returns:
            Job status information

        Example integration:
            >>> # Query through OmniCore
            >>> # status = await omnicore.query_module_status('generator', job_id)
        """
        logger.debug(f"Fetching generator status for job {job_id} via OmniCore")

        # Route query through OmniCore
        if self.omnicore_service:
            payload = {
                "action": "get_status",
                "job_id": job_id,
            }
            result = await self.omnicore_service.route_job(
                job_id=job_id,
                source_module="api",
                target_module="generator",
                payload=payload,
            )
            return result.get("data", {})

        # Fallback
        from datetime import timezone
        
        return {
            "job_id": job_id,
            "stage": "generator_generation",
            "status": "running",  # Required field
            "progress_percent": 35.0,  # Changed from 'progress' to 'progress_percent'
            "message": "Generating code from README (direct fallback)",
            "artifacts_generated": [],  # Required field
            "updated_at": datetime.now(timezone.utc),  # Required field
        }

    async def get_job_logs(self, job_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Get logs for a generator job.

        Args:
            job_id: Unique job identifier
            limit: Maximum number of log entries to return

        Returns:
            List of log entries

        Example integration:
            >>> # from generator.audit_log import get_job_logs
            >>> # logs = await get_job_logs(job_id, limit)
        """
        logger.debug(f"Fetching logs for generator job {job_id}")

        # Placeholder: Query actual logs
        return [
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "level": "INFO",
                "message": f"Processing job {job_id}",
                "module": "generator",
            }
        ]

    async def clarify_requirements(
        self, job_id: str, readme_content: str, ambiguities: Optional[List[str]] = None, channel: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Run the clarifier agent to analyze and clarify requirements via OmniCore.

        This method routes the clarification request through OmniCore's message bus
        to the generator's clarifier module, which uses LLM-based clarification
        and user feedback to resolve ambiguities.

        Args:
            job_id: Unique job identifier
            readme_content: README content to clarify
            ambiguities: Optional list of detected ambiguities to clarify
            channel: Optional clarification channel (cli, gui, voice, web, slack, email, sms)

        Returns:
            Clarification results with clarified requirements

        Example integration:
            >>> # Route through OmniCore to generator.clarifier
            >>> # await omnicore.route_to_generator('clarify', {...})
        """
        logger.info(f"Running clarifier for job {job_id} via OmniCore (channel: {channel or 'default'})")

        # Route through OmniCore if available
        if self.omnicore_service:
            try:
                payload = {
                    "action": "clarify_requirements",
                    "job_id": job_id,
                    "readme_content": readme_content,
                    "ambiguities": ambiguities or [],
                }
                # Add channel if specified
                if channel:
                    payload["channel"] = channel
                    
                result = await self.omnicore_service.route_job(
                    job_id=job_id,
                    source_module="api",
                    target_module="generator",
                    payload=payload,
                )
                logger.info(f"Clarification for job {job_id} routed to generator via OmniCore")
                data = result.get("data", {})
                # Ensure job_id is always included in the response
                if "job_id" not in data:
                    data["job_id"] = job_id
                return data
            except Exception as e:
                logger.error(
                    f"Error routing clarification through OmniCore for job {job_id}: {e}",
                    exc_info=True
                )
                # Fall through to fallback response

        # Fallback if OmniCore not available or failed
        logger.warning("OmniCore service not available or failed, using direct fallback")
        return {
            "job_id": job_id,
            "clarifications": [
                "Need to specify database type",
                "Authentication method not specified",
            ],
            "confidence": 0.85,
            "clarifier_module": "generator.clarifier (fallback)",
            "questions_count": 2,
        }

    async def get_clarification_feedback(
        self, job_id: str, interaction_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get feedback from the clarifier's user interaction process via OmniCore.

        This allows monitoring the clarification feedback loop, including
        questions asked, user responses, and clarification status.

        Args:
            job_id: Unique job identifier
            interaction_id: Optional specific interaction ID to query

        Returns:
            Clarification feedback status and history

        Example integration:
            >>> # Query clarifier feedback through OmniCore
            >>> # feedback = await omnicore.query_generator_clarifier(job_id)
        """
        logger.info(f"Fetching clarification feedback for job {job_id} via OmniCore")

        # Route through OmniCore
        if self.omnicore_service:
            payload = {
                "action": "get_clarification_feedback",
                "job_id": job_id,
                "interaction_id": interaction_id,
            }
            result = await self.omnicore_service.route_job(
                job_id=job_id,
                source_module="api",
                target_module="generator",
                payload=payload,
            )
            return result.get("data", {})

        # Fallback
        return {
            "job_id": job_id,
            "interaction_id": interaction_id,
            "status": "no_interactions",
            "clarifier_module": "generator.clarifier (fallback)",
        }

    async def submit_clarification_response(
        self, job_id: str, question_id: str, response: str
    ) -> Dict[str, Any]:
        """
        Submit a user response to a clarification question via OmniCore.

        This enables interactive feedback for the clarifier, allowing users
        to provide answers to clarification questions through the API.

        Args:
            job_id: Unique job identifier
            question_id: ID of the question being answered
            response: User's response to the question

        Returns:
            Response submission confirmation

        Example integration:
            >>> # Route response through OmniCore to clarifier
            >>> # await omnicore.submit_to_clarifier(job_id, question_id, response)
        """
        logger.info(f"Submitting clarification response for job {job_id} via OmniCore")

        # Route through OmniCore
        if self.omnicore_service:
            payload = {
                "action": "submit_clarification_response",
                "job_id": job_id,
                "question_id": question_id,
                "response": response,
            }
            result = await self.omnicore_service.route_job(
                job_id=job_id,
                source_module="api",
                target_module="generator",
                payload=payload,
            )
            logger.info(f"Clarification response for job {job_id} submitted via OmniCore")
            return result.get("data", {
                "job_id": job_id,
                "question_id": question_id,
                "status": "response_submitted",
            })

        # Fallback
        return {
            "job_id": job_id,
            "question_id": question_id,
            "status": "submitted",
            "clarifier_module": "generator.clarifier (fallback)",
        }

    async def generate_code(
        self, job_id: str, clarified_requirements: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Generate code from clarified requirements.

        Args:
            job_id: Unique job identifier
            clarified_requirements: Clarified requirements from clarifier

        Returns:
            Generation results

        Example integration:
            >>> # from generator.agents.codegen_agent import generate_code
            >>> # result = await generate_code(requirements)
        """
        logger.info(f"Generating code for job {job_id}")

        # Wire to real codegen agent
        try:
            from generator.agents.codegen_agent.codegen_agent import (
                generate_code as _codegen_agent_generate,
            )

            state_summary = clarified_requirements.get("state_summary", "")
            config_path = clarified_requirements.get("config_path", "prod_config.yaml")

            result = await _codegen_agent_generate(
                requirements=clarified_requirements,
                state_summary=state_summary,
                config_path_or_dict=config_path,
            )
            if isinstance(result, dict):
                result.setdefault("job_id", job_id)
                result.setdefault("status", "completed")
            return result
        except Exception as e:
            logger.error(
                f"Code generation failed for job {job_id}: {e}", exc_info=True
            )
            return {
                "job_id": job_id,
                "status": "error",
                "message": str(e),
            }

    async def run_codegen_agent(
        self, job_id: str, requirements: str, language: str, framework: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Run the codegen agent directly via OmniCore.

        Args:
            job_id: Unique job identifier
            requirements: Natural language requirements
            language: Target programming language
            framework: Optional framework specification

        Returns:
            Code generation results
        """
        logger.info(f"Running codegen agent for job {job_id} via OmniCore")

        if self.omnicore_service:
            payload = {
                "action": "run_codegen",
                "job_id": job_id,
                "requirements": requirements,
                "language": language,
                "framework": framework,
            }
            result = await self.omnicore_service.route_job(
                job_id=job_id,
                source_module="api",
                target_module="generator",
                payload=payload,
            )
            
            # Check if routing succeeded
            routed = result.get("routed", False)
            data = result.get("data", {})
            
            # Check if OmniCore returned an error in data
            if data.get("status") == "error":
                logger.error(f"OmniCore codegen execution failed for job {job_id}: {data.get('message', 'Unknown error')}")
                return data
            
            # If routing succeeded but data is empty, agents may still be loading - return retryable error
            if routed and not data:
                logger.warning(f"Codegen routing succeeded but no data returned for job {job_id} - agents may still be loading")
                return {
                    "job_id": job_id,
                    "status": "error",
                    "retry": True,
                    "message": "Code generation agents are still loading or returned no data. Please retry in a few seconds.",
                }
            
            # If routing succeeded and we have data, return it
            if routed and isinstance(data, dict):
                return data

        # No OmniCore or routing failed - return hard error (not retryable)
        logger.error(f"Codegen agent unavailable for job {job_id} - OmniCore service not available or routing failed")
        return {
            "job_id": job_id,
            "status": "error",
            "message": "Code generation agent unavailable. OmniCore service is not available or codegen agent is not loaded.",
        }

    async def run_testgen_agent(
        self, job_id: str, code_path: str, test_type: str, coverage_target: float
    ) -> Dict[str, Any]:
        """
        Run the testgen agent to generate tests via OmniCore.

        Args:
            job_id: Unique job identifier
            code_path: Path to code to test
            test_type: Type of tests (unit, integration, e2e)
            coverage_target: Target code coverage percentage

        Returns:
            Test generation results
        """
        logger.info(f"Running testgen agent for job {job_id} via OmniCore")

        if self.omnicore_service:
            payload = {
                "action": "run_testgen",
                "job_id": job_id,
                "code_path": code_path,
                "test_type": test_type,
                "coverage_target": coverage_target,
            }
            result = await self.omnicore_service.route_job(
                job_id=job_id,
                source_module="api",
                target_module="generator",
                payload=payload,
            )
            
            # Check if routing succeeded
            routed = result.get("routed", False)
            data = result.get("data", {})
            
            # Check if OmniCore returned an error in data
            if data.get("status") == "error":
                logger.error(f"OmniCore testgen execution failed for job {job_id}: {data.get('message', 'Unknown error')}")
                return data
            
            # If routing succeeded but data is empty, agents may still be loading - return retryable error
            if routed and not data:
                logger.warning(f"Testgen routing succeeded but no data returned for job {job_id} - agents may still be loading")
                return {
                    "job_id": job_id,
                    "status": "error",
                    "retry": True,
                    "message": "Test generation agents are still loading or returned no data. Please retry in a few seconds.",
                }
            
            # If routing succeeded and we have data, return it
            if routed and isinstance(data, dict):
                return data

        # No OmniCore or routing failed - return hard error (not retryable)
        logger.error(f"Testgen agent unavailable for job {job_id} - OmniCore service not available or routing failed")
        return {
            "job_id": job_id,
            "status": "error",
            "message": "Test generation agent unavailable. OmniCore service is not available or testgen agent is not loaded.",
        }

    async def run_deploy_agent(
        self, job_id: str, code_path: str, platform: str, include_ci_cd: bool
    ) -> Dict[str, Any]:
        """
        Run the deploy agent to generate deployment configs via OmniCore.

        Args:
            job_id: Unique job identifier
            code_path: Path to application code
            platform: Deployment platform (docker, kubernetes, aws)
            include_ci_cd: Whether to include CI/CD configuration

        Returns:
            Deployment configuration results
        """
        logger.info(f"Running deploy agent for job {job_id} via OmniCore")

        if self.omnicore_service:
            payload = {
                "action": "run_deploy",
                "job_id": job_id,
                "code_path": code_path,
                "platform": platform,
                "include_ci_cd": include_ci_cd,
            }
            result = await self.omnicore_service.route_job(
                job_id=job_id,
                source_module="api",
                target_module="generator",
                payload=payload,
            )
            
            # Check if routing succeeded
            routed = result.get("routed", False)
            data = result.get("data", {})
            
            # Check if OmniCore returned an error in data
            if data.get("status") == "error":
                logger.error(f"OmniCore deploy agent execution failed for job {job_id}: {data.get('message', 'Unknown error')}")
                return data
            
            # If routing succeeded but data is empty, agents may still be loading - return retryable error
            if routed and not data:
                logger.warning(f"Deploy routing succeeded but no data returned for job {job_id} - agents may still be loading")
                return {
                    "job_id": job_id,
                    "status": "error",
                    "retry": True,
                    "message": "Deployment agents are still loading or returned no data. Please retry in a few seconds.",
                }
            
            # If routing succeeded and we have data, return it
            if routed and isinstance(data, dict):
                return data

        # No OmniCore or routing failed - return hard error (not retryable)
        logger.error(f"Deploy agent unavailable for job {job_id} - OmniCore service not available or routing failed")
        return {
            "job_id": job_id,
            "status": "error",
            "message": "Deployment agent unavailable. OmniCore service is not available or deploy agent is not loaded.",
        }

    async def run_docgen_agent(
        self, job_id: str, code_path: str, doc_type: str, format: str
    ) -> Dict[str, Any]:
        """
        Run the docgen agent to generate documentation via OmniCore.

        Args:
            job_id: Unique job identifier
            code_path: Path to code to document
            doc_type: Documentation type (api, user, developer)
            format: Output format (markdown, html, pdf)

        Returns:
            Documentation generation results
        """
        logger.info(f"Running docgen agent for job {job_id} via OmniCore")

        if self.omnicore_service:
            payload = {
                "action": "run_docgen",
                "job_id": job_id,
                "code_path": code_path,
                "doc_type": doc_type,
                "format": format,
            }
            result = await self.omnicore_service.route_job(
                job_id=job_id,
                source_module="api",
                target_module="generator",
                payload=payload,
            )
            
            # Check if routing succeeded
            routed = result.get("routed", False)
            data = result.get("data", {})
            
            # Check if OmniCore returned an error in data
            if data.get("status") == "error":
                logger.error(f"OmniCore docgen execution failed for job {job_id}: {data.get('message', 'Unknown error')}")
                return data
            
            # If routing succeeded but data is empty, agents may still be loading - return retryable error
            if routed and not data:
                logger.warning(f"Docgen routing succeeded but no data returned for job {job_id} - agents may still be loading")
                return {
                    "job_id": job_id,
                    "status": "error",
                    "retry": True,
                    "message": "Documentation generation agents are still loading or returned no data. Please retry in a few seconds.",
                }
            
            # If routing succeeded and we have data, return it
            if routed and isinstance(data, dict):
                return data

        # No OmniCore or routing failed - return hard error (not retryable)
        logger.error(f"Docgen agent unavailable for job {job_id} - OmniCore service not available or routing failed")
        return {
            "job_id": job_id,
            "status": "error",
            "message": "Documentation generation agent unavailable. OmniCore service is not available or docgen agent is not loaded.",
        }

    async def run_critique_agent(
        self, job_id: str, code_path: str, scan_types: List[str], auto_fix: bool
    ) -> Dict[str, Any]:
        """
        Run the critique agent for security/quality scanning via OmniCore.

        Args:
            job_id: Unique job identifier
            code_path: Path to code to analyze
            scan_types: Types of scans (security, quality, performance)
            auto_fix: Whether to automatically apply fixes

        Returns:
            Critique analysis results
        """
        logger.info(f"Running critique agent for job {job_id} via OmniCore")

        if self.omnicore_service:
            payload = {
                "action": "run_critique",
                "job_id": job_id,
                "code_path": code_path,
                "scan_types": scan_types,
                "auto_fix": auto_fix,
            }
            result = await self.omnicore_service.route_job(
                job_id=job_id,
                source_module="api",
                target_module="generator",
                payload=payload,
            )
            
            # Check if routing succeeded
            routed = result.get("routed", False)
            data = result.get("data", {})
            
            # Check if OmniCore returned an error in data
            if data.get("status") == "error":
                logger.error(f"OmniCore critique execution failed for job {job_id}: {data.get('message', 'Unknown error')}")
                return data
            
            # If routing succeeded but data is empty, agents may still be loading - return retryable error
            if routed and not data:
                logger.warning(f"Critique routing succeeded but no data returned for job {job_id} - agents may still be loading")
                return {
                    "job_id": job_id,
                    "status": "error",
                    "retry": True,
                    "message": "Critique agents are still loading or returned no data. Please retry in a few seconds.",
                }
            
            # If routing succeeded and we have data, return it
            if routed and isinstance(data, dict):
                return data

        # No OmniCore or routing failed - return hard error (not retryable)
        logger.error(f"Critique agent unavailable for job {job_id} - OmniCore service not available or routing failed")
        return {
            "job_id": job_id,
            "status": "error",
            "message": "Critique agent unavailable. OmniCore service is not available or critique agent is not loaded.",
        }

    async def get_readme_content(self, job_id: str) -> Optional[str]:
        """
        Read README content from job's upload directory.
        
        Args:
            job_id: Unique job identifier
            
        Returns:
            README content as string, or None if not found
        """
        job_dir = self.storage_path / job_id
        
        if not job_dir.exists():
            logger.warning(f"Job directory not found: {job_dir}")
            return None
        
        # Priority order for README files
        readme_patterns = [
            "README.md",
            "readme.md", 
            "README.txt",
            "readme.txt",
        ]
        
        # Try exact filename matches first
        for pattern in readme_patterns:
            readme_path = job_dir / pattern
            if readme_path.exists() and readme_path.is_file():
                try:
                    content = readme_path.read_text(encoding="utf-8")
                    logger.info(
                        f"[PIPELINE] Loaded README from {readme_path.name} "
                        f"({len(content)} bytes) for job {job_id}"
                    )
                    return content
                except Exception as e:
                    logger.error(f"Error reading {readme_path}: {e}")
                    continue
        
        # Fallback: find any .md file
        try:
            for f in job_dir.glob("*.md"):
                if f.is_file():
                    content = f.read_text(encoding="utf-8")
                    logger.info(
                        f"[PIPELINE] Loaded README from {f.name} "
                        f"({len(content)} bytes) for job {job_id}"
                    )
                    return content
        except Exception as e:
            logger.error(f"Error scanning for .md files in {job_dir}: {e}")
        
        logger.warning(f"No README file found in job directory: {job_dir}")
        return None

    async def run_full_pipeline(
        self,
        job_id: str,
        readme_content: str,
        language: str,
        include_tests: bool,
        include_deployment: bool,
        include_docs: bool,
        run_critique: bool,
        skip_clarification: bool = False,
        output_dir: Optional[str] = None,
        doc_type: Optional[str] = None,
        doc_format: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Run the full generation pipeline via OmniCore.

        Args:
            job_id: Unique job identifier
            readme_content: README/requirements content
            language: Target programming language
            include_tests: Whether to generate tests
            include_deployment: Whether to generate deployment configs
            include_docs: Whether to generate documentation
            run_critique: Whether to run security/quality checks
            skip_clarification: Whether to skip the clarification step (used when resuming after clarification)
            doc_type: Documentation type to generate (e.g. "readme", "sphinx"). When "sphinx",
                      the docgen stage will trigger a full Sphinx HTML build under docs/_build/html.
            doc_format: Output format for documentation (e.g. "markdown", "html").

        Returns:
            Full pipeline execution results
        """
        logger.info(f"Running full generation pipeline for job {job_id} via OmniCore")

        if self.omnicore_service:
            payload = {
                "action": "run_full_pipeline",
                "job_id": job_id,
                "readme_content": readme_content,
                "language": language,
                "include_tests": include_tests,
                "include_deployment": include_deployment,
                "include_docs": include_docs,
                "run_critique": run_critique,
                "skip_clarification": skip_clarification,
            }
            # Add output_dir if specified
            if output_dir:
                payload["output_dir"] = output_dir
            # Pass doc_type/doc_format only when explicitly set so the pipeline
            # can honour them without overriding its own defaults otherwise.
            if doc_type:
                payload["doc_type"] = doc_type
            if doc_format:
                payload["doc_format"] = doc_format

            result = await self.omnicore_service.route_job(
                job_id=job_id,
                source_module="api",
                target_module="generator",
                payload=payload,
            )
            
            # Check if routing succeeded
            routed = result.get("routed", False)
            data = result.get("data", {})
            
            # If routing failed, treat as agents still loading (retryable)
            if not routed:
                logger.warning(f"Full pipeline routing failed for job {job_id} - code generation service is not ready")
                # Create retryable error response
                data = self._create_retryable_error(
                    job_id=job_id,
                    message="Code generation service is initializing. Please retry in a few seconds."
                )
            # If routing succeeded but data is empty, treat as agents still loading (retryable)
            elif routed and not data:
                logger.warning(f"Full pipeline routing succeeded but agents have not returned data for job {job_id}")
                # Create retryable error response
                data = self._create_retryable_error(
                    job_id=job_id,
                    message="Code generation agents are still loading or returned no data. Please retry in a few seconds."
                )
            
            # Check if OmniCore returned a retryable error (agents not loaded yet)
            if data.get("retry") and data.get("status") == "error":
                logger.info(
                    f"Agents not ready for job {job_id}, implementing retry with exponential backoff",
                    extra={
                        "job_id": job_id,
                        "max_retries": self.MAX_RETRY_ATTEMPTS,
                        "base_delay": self.RETRY_BASE_DELAY_SECONDS
                    }
                )
                # Retry with exponential backoff (3 retries after initial attempt = 4 total calls)
                # Implements resilience pattern with capped exponential backoff
                for attempt in range(self.MAX_RETRY_ATTEMPTS):
                    # Calculate delay with true exponential backoff
                    # attempt 0: 2^0 * 5 = 1 * 5 = 5s
                    # attempt 1: 2^1 * 5 = 2 * 5 = 10s
                    # attempt 2: 2^2 * 5 = 4 * 5 = 20s (capped at max)
                    delay = min(
                        self.RETRY_BASE_DELAY_SECONDS * (2 ** attempt),
                        self.RETRY_MAX_DELAY_SECONDS
                    )
                    logger.info(
                        f"Waiting {delay}s before retry attempt {attempt + 1}/{self.MAX_RETRY_ATTEMPTS}",
                        extra={
                            "job_id": job_id,
                            "attempt": attempt + 1,
                            "delay_seconds": delay
                        }
                    )
                    await asyncio.sleep(delay)
                    
                    logger.info(
                        f"Retry attempt {attempt + 1}/{self.MAX_RETRY_ATTEMPTS} for job {job_id}",
                        extra={
                            "job_id": job_id,
                            "attempt": attempt + 1,
                            "total_attempts": self.MAX_RETRY_ATTEMPTS
                        }
                    )
                    result = await self.omnicore_service.route_job(
                        job_id=job_id,
                        source_module="api",
                        target_module="generator",
                        payload=payload,
                    )
                    
                    # Update both routed and data from retry result
                    routed = result.get("routed", False)
                    data = result.get("data", {})
                    
                    # If routing failed on retry, create retryable error
                    if not routed:
                        logger.warning(f"Retry {attempt + 1} routing failed for job {job_id}")
                        data = self._create_retryable_error(
                            job_id=job_id,
                            message="Code generation service is still initializing."
                        )
                    # If routing succeeded but no data, create retryable error
                    elif routed and not data:
                        logger.warning(f"Retry {attempt + 1} succeeded but no data for job {job_id}")
                        data = self._create_retryable_error(
                            job_id=job_id,
                            message="Code generation agents are still loading."
                        )
                    
                    # Check if we can stop retrying (success or non-retryable error)
                    if not data.get("retry"):
                        logger.info(
                            f"Retry succeeded on attempt {attempt + 1}",
                            extra={
                                "job_id": job_id,
                                "attempt": attempt + 1,
                                "status": data.get("status"),
                                "routed": routed
                            }
                        )
                        break
                    logger.warning(
                        f"Retry {attempt + 1} still returned retry status for job {job_id}",
                        extra={
                            "job_id": job_id,
                            "attempt": attempt + 1,
                            "retry_message": data.get("message"),
                            "routed": routed
                        }
                    )
                else:
                    # All retries exhausted
                    logger.error(
                        f"All {self.MAX_RETRY_ATTEMPTS} retry attempts exhausted for job {job_id}",
                        extra={
                            "job_id": job_id,
                            "total_attempts": self.MAX_RETRY_ATTEMPTS + 1,
                            "final_status": data.get("status"),
                            "final_message": data.get("message")
                        }
                    )
            
            # Check if OmniCore returned an error
            if data.get("status") == "error":
                logger.error(f"OmniCore pipeline execution failed for job {job_id}: {data.get('message', 'Unknown error')}")
                return data
            
            # If routing succeeded and we have data, return it
            if routed and isinstance(data, dict):
                return data
            
            # If we got here with routed=True but no data, this shouldn't happen after our fix above
            # but just in case, treat it as a retryable error
            if routed:
                logger.warning(f"Full pipeline routing succeeded but ended without data for job {job_id}")
                return self._create_retryable_error(
                    job_id=job_id,
                    message="Code generation agents did not return data. Please retry in a few seconds."
                )

        # No OmniCore or routing failed - return hard error (not retryable)
        logger.error(f"Full pipeline execution unavailable for job {job_id} - OmniCore service not available or routing failed")
        return {
            "job_id": job_id,
            "status": "error",
            "message": "Code generation pipeline unavailable. OmniCore service is not available or agents are not loaded.",
        }

    async def configure_llm_provider(
        self, provider: str, api_key: Optional[str], model: Optional[str], config: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Configure LLM provider for generator via OmniCore.

        Args:
            provider: LLM provider name
            api_key: API key for provider
            model: Specific model to use
            config: Additional configuration

        Returns:
            Configuration result
        """
        logger.info(f"Configuring LLM provider {provider} via OmniCore")

        if self.omnicore_service:
            payload = {
                "action": "configure_llm",
                "provider": provider,
                "api_key": api_key,
                "model": model,
                "config": config or {},
            }
            result = await self.omnicore_service.route_job(
                job_id="llm_config",
                source_module="api",
                target_module="generator",
                payload=payload,
            )
            return result.get("data", {})

        return {
            "status": "configured",
            "provider": provider,
            "model": model or "default",
        }

    async def get_llm_provider_status(self) -> Dict[str, Any]:
        """
        Get status of configured LLM providers via OmniCore.

        Returns:
            LLM provider status information
        """
        logger.info("Fetching LLM provider status via OmniCore")

        if self.omnicore_service:
            payload = {"action": "get_llm_status"}
            result = await self.omnicore_service.route_job(
                job_id="llm_status",
                source_module="api",
                target_module="generator",
                payload=payload,
            )
            return result.get("data", {})

        return {
            "active_provider": "openai",
            "available_providers": ["openai", "anthropic", "google", "xai", "ollama"],
            "provider_configs": {
                "openai": {"model": "gpt-4", "configured": True},
                "anthropic": {"model": "claude-3-opus", "configured": False},
            },
        }

    async def query_audit_logs(
        self,
        start_time: Optional[str],
        end_time: Optional[str],
        event_type: Optional[str],
        job_id: Optional[str],
        limit: int,
    ) -> Dict[str, Any]:
        """
        Query generator audit logs via OmniCore.

        Args:
            start_time: Start timestamp
            end_time: End timestamp
            event_type: Filter by event type
            job_id: Filter by job ID
            limit: Max results

        Returns:
            Audit log entries
        """
        logger.info("Querying generator audit logs via OmniCore")

        if self.omnicore_service:
            payload = {
                "action": "query_audit_logs",
                "start_time": start_time,
                "end_time": end_time,
                "event_type": event_type,
                "job_id": job_id,
                "limit": limit,
            }
            result = await self.omnicore_service.route_job(
                job_id=job_id or "audit_query",
                source_module="api",
                target_module="generator",
                payload=payload,
            )
            return result.get("data", {})

        return {
            "logs": [
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "event_type": "code_generated",
                    "job_id": job_id or "test",
                    "details": {},
                }
            ],
            "count": 1,
        }


def get_generator_service() -> GeneratorService:
    """
    Dependency injection function for GeneratorService.
    
    Creates a GeneratorService instance with OmniCoreService for
    centralized routing of generator operations.
    
    Returns:
        GeneratorService: Configured generator service instance
        
    Example:
        >>> from fastapi import Depends
        >>> @router.post("/endpoint")
        >>> async def handler(service: GeneratorService = Depends(get_generator_service)):
        ...     result = await service.create_generation_job(...)
    """
    from server.services.omnicore_service import get_omnicore_service
    
    omnicore = get_omnicore_service()
    return GeneratorService(omnicore_service=omnicore)
