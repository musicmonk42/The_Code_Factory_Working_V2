# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Service for interacting with the Generator module through OmniCore.

This service provides a mockable interface to the generator module for job creation,
file processing, and code generation tasks. ALL operations are routed through
OmniCore as the central coordinator.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class GeneratorService:
    """
    Service for interacting with the Generator (README-to-App Code Generator).

    This service acts as an abstraction layer for generator operations,
    providing methods for file upload, job creation, and generation tasks.
    All operations are routed through OmniCore's message bus and coordination layer.
    The implementation includes placeholder logic with extensible hooks for
    actual generator integration via OmniCore.
    """

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
            "uploaded_at": datetime.utcnow().isoformat(),
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
                "timestamp": datetime.utcnow().isoformat(),
                "level": "INFO",
                "message": f"Processing job {job_id}",
                "module": "generator",
            }
        ]

    async def clarify_requirements(
        self, job_id: str, readme_content: str, ambiguities: Optional[List[str]] = None
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

        Returns:
            Clarification results with clarified requirements

        Example integration:
            >>> # Route through OmniCore to generator.clarifier
            >>> # await omnicore.route_to_generator('clarify', {...})
        """
        logger.info(f"Running clarifier for job {job_id} via OmniCore")

        # Route through OmniCore if available
        if self.omnicore_service:
            try:
                payload = {
                    "action": "clarify_requirements",
                    "job_id": job_id,
                    "readme_content": readme_content,
                    "ambiguities": ambiguities or [],
                }
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

        # Placeholder: Call actual code generation
        return {
            "job_id": job_id,
            "generated_files": ["main.py", "config.py", "tests/test_main.py"],
            "status": "completed",
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
            return result.get("data", {})

        return {
            "job_id": job_id,
            "status": "completed",
            "generated_files": ["main.py"],
            "output_path": f"./uploads/{job_id}/generated",
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
            return result.get("data", {})

        return {
            "job_id": job_id,
            "status": "completed",
            "generated_tests": ["tests/test_main.py"],
            "coverage": coverage_target,
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
            return result.get("data", {})

        return {
            "job_id": job_id,
            "status": "completed",
            "generated_files": ["Dockerfile", "docker-compose.yml"],
            "platform": platform,
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
            return result.get("data", {})

        return {
            "job_id": job_id,
            "status": "completed",
            "generated_docs": ["docs/API.md", "docs/README.md"],
            "doc_type": doc_type,
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
            return result.get("data", {})

        return {
            "job_id": job_id,
            "status": "completed",
            "issues_found": 5,
            "issues_fixed": 3 if auto_fix else 0,
            "scan_types": scan_types,
        }

    async def run_full_pipeline(
        self,
        job_id: str,
        readme_content: str,
        language: str,
        include_tests: bool,
        include_deployment: bool,
        include_docs: bool,
        run_critique: bool,
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
            }
            result = await self.omnicore_service.route_job(
                job_id=job_id,
                source_module="api",
                target_module="generator",
                payload=payload,
            )
            return result.get("data", {})

        return {
            "job_id": job_id,
            "status": "completed",
            "stages_completed": ["clarify", "codegen", "testgen", "deploy", "docgen", "critique"],
            "output_path": f"./uploads/{job_id}/output",
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
                    "timestamp": datetime.utcnow().isoformat(),
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
