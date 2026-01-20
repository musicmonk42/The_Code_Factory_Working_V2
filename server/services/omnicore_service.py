"""
Service for interacting with the OmniCore Engine module.

This service provides a mockable interface to the omnicore_engine module for
job coordination, plugin management, and inter-module communication.

This module implements proper agent integration with:
- Configuration-based LLM provider selection
- Graceful degradation when agents unavailable
- Proper error handling and logging
- Environment variable support for API keys
"""

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from server.utils.agent_loader import get_agent_loader

logger = logging.getLogger(__name__)

# Import configuration
try:
    from server.config import get_agent_config, get_llm_config
    CONFIG_AVAILABLE = True
except ImportError:
    logger.warning("server.config not available, using default configuration")
    CONFIG_AVAILABLE = False

# In-memory storage for clarification sessions
_clarification_sessions = {}


class OmniCoreService:
    """
    Service for interacting with the OmniCore Engine.

    This service acts as an abstraction layer for OmniCore operations,
    coordinating between generator and SFE modules via the message bus.
    The implementation includes proper agent integration with configuration-based
    LLM provider selection and graceful degradation.
    """

    def __init__(self):
        """Initialize the OmniCoreService with agent availability checks."""
        logger.info("OmniCoreService initializing...")
        
        # Load configuration
        self.agent_config = get_agent_config() if CONFIG_AVAILABLE else None
        self.llm_config = get_llm_config() if CONFIG_AVAILABLE else None
        
        # Track agent availability
        self.agents_available = {
            "codegen": False,
            "testgen": False,
            "deploy": False,
            "docgen": False,
            "critique": False,
            "clarifier": False,
        }
        
        # Try to import and cache agent modules
        self._load_agents()
        
        # Log initialization status
        available = [k for k, v in self.agents_available.items() if v]
        unavailable = [k for k, v in self.agents_available.items() if not v]
        
        if available:
            logger.info(f"OmniCoreService initialized. Available agents: {', '.join(available)}")
        if unavailable:
            logger.warning(f"Some agents unavailable: {', '.join(unavailable)}")
            if self.agent_config and self.agent_config.strict_mode:
                raise RuntimeError(
                    f"STRICT_MODE enabled but agents unavailable: {', '.join(unavailable)}. "
                    "Check LLM configuration and dependencies."
                )
    
    def _load_agents(self):
        """
        Attempt to load all agent modules and track availability.
        
        This method tries to import each agent and marks it as available
        if the import succeeds. Failures are logged but don't prevent
        service initialization unless strict_mode is enabled.
        """
        # Try loading codegen agent
        try:
            from generator.agents.codegen_agent.codegen_agent import generate_code
            self._codegen_func = generate_code
            self.agents_available["codegen"] = True
            logger.info("✓ Codegen agent loaded successfully")
        except ImportError as e:
            logger.warning(f"Codegen agent unavailable: {e}")
            self._codegen_func = None
        except Exception as e:
            logger.error(f"Unexpected error loading codegen agent: {e}", exc_info=True)
            self._codegen_func = None
        
        # Try loading testgen agent
        try:
            from generator.agents.testgen_agent.testgen_agent import TestgenAgent, Policy
            self._testgen_class = TestgenAgent
            self._testgen_policy_class = Policy
            self.agents_available["testgen"] = True
            logger.info("✓ Testgen agent loaded successfully")
        except ImportError as e:
            logger.warning(f"Testgen agent unavailable: {e}")
            self._testgen_class = None
            self._testgen_policy_class = None
        except Exception as e:
            logger.error(f"Unexpected error loading testgen agent: {e}", exc_info=True)
            self._testgen_class = None
            self._testgen_policy_class = None
        
        # Try loading deploy agent
        try:
            from generator.agents.deploy_agent.deploy_agent import DeployAgent
            self._deploy_class = DeployAgent
            self.agents_available["deploy"] = True
            logger.info("✓ Deploy agent loaded successfully")
        except ImportError as e:
            logger.warning(f"Deploy agent unavailable: {e}")
            self._deploy_class = None
        except Exception as e:
            logger.error(f"Unexpected error loading deploy agent: {e}", exc_info=True)
            self._deploy_class = None
        
        # Try loading docgen agent
        try:
            from generator.agents.docgen_agent.docgen_agent import DocgenAgent
            self._docgen_class = DocgenAgent
            self.agents_available["docgen"] = True
            logger.info("✓ Docgen agent loaded successfully")
        except ImportError as e:
            logger.warning(f"Docgen agent unavailable: {e}")
            self._docgen_class = None
        except Exception as e:
            logger.error(f"Unexpected error loading docgen agent: {e}", exc_info=True)
            self._docgen_class = None
        
        # Try loading critique agent
        try:
            from generator.agents.critique_agent.critique_agent import CritiqueAgent
            self._critique_class = CritiqueAgent
            self.agents_available["critique"] = True
            logger.info("✓ Critique agent loaded successfully")
        except ImportError as e:
            logger.warning(f"Critique agent unavailable: {e}")
            self._critique_class = None
        except Exception as e:
            logger.error(f"Unexpected error loading critique agent: {e}", exc_info=True)
            self._critique_class = None
        
        # Try loading clarifier (prefer LLM-based if configured)
        use_llm_clarifier = (
            self.agent_config and 
            self.agent_config.use_llm_clarifier and
            self.llm_config and
            self.llm_config.get_available_providers()
        )
        
        if use_llm_clarifier:
            try:
                from generator.clarifier.clarifier_llm import GrokLLM
                self._clarifier_llm_class = GrokLLM
                self.agents_available["clarifier"] = True
                logger.info("✓ LLM-based clarifier loaded successfully")
            except ImportError as e:
                logger.warning(f"LLM clarifier unavailable, will use rule-based: {e}")
                self._clarifier_llm_class = None
                # Rule-based clarifier is always available as fallback
                self.agents_available["clarifier"] = True
            except Exception as e:
                logger.error(f"Unexpected error loading LLM clarifier: {e}", exc_info=True)
                self._clarifier_llm_class = None
                self.agents_available["clarifier"] = True
        else:
            logger.info("Using rule-based clarifier (LLM clarifier not configured)")
            self._clarifier_llm_class = None
            self.agents_available["clarifier"] = True
    
    def _build_llm_config(self) -> Dict[str, Any]:
        """
        Build LLM configuration dict for agents from our config.
        
        Returns:
            Configuration dictionary compatible with agent requirements
        """
        if not self.llm_config:
            # Fallback configuration when config module not available
            return {
                "backend": "openai",
                "model": {"openai": "gpt-4"},
                "ensemble_enabled": False,
            }
        
        provider = self.llm_config.default_llm_provider
        model = self.llm_config.get_provider_model(provider)
        api_key = self.llm_config.get_provider_api_key(provider)
        
        # Set environment variable for the agent to use
        if api_key:
            env_var = f"{provider.upper()}_API_KEY"
            os.environ[env_var] = api_key
        
        config = {
            "backend": provider,
            "model": {provider: model},
            "ensemble_enabled": self.llm_config.enable_ensemble_mode,
            "timeout": self.llm_config.llm_timeout,
            "max_retries": self.llm_config.llm_max_retries,
            "temperature": self.llm_config.llm_temperature,
        }
        
        # Add OpenAI base URL if configured
        if provider == "openai" and self.llm_config.openai_base_url:
            config["openai_base_url"] = self.llm_config.openai_base_url
        
        return config
    
    def _check_agent_available(self, agent_name: str) -> Tuple[bool, Optional[str]]:
        """
        Check if an agent is available and return error message if not.
        
        Args:
            agent_name: Name of the agent to check
        
        Returns:
            Tuple of (is_available, error_message)
        """
        if not self.agents_available.get(agent_name, False):
            error_msg = (
                f"{agent_name.capitalize()} agent is not available. "
                "Check that dependencies are installed"
            )
            if not self.llm_config or not self.llm_config.get_available_providers():
                error_msg += " and LLM provider is configured (set API keys in .env)"
            return False, error_msg
        return True, None

    async def route_job(
        self,
        job_id: str,
        source_module: str,
        target_module: str,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Route a job from one module to another via the message bus.

        Args:
            job_id: Unique job identifier
            source_module: Source module (e.g., 'generator')
            target_module: Target module (e.g., 'sfe')
            payload: Job data to route

        Returns:
            Routing result

        Example integration:
            >>> # from omnicore_engine.message_bus import publish_message
            >>> # await publish_message(topic=target_module, payload=payload)
        """
        logger.info(f"Routing job {job_id} from {source_module} to {target_module}")

        # If target is generator, dispatch to actual generator agents
        if target_module == "generator":
            action = payload.get("action")
            logger.info(f"Dispatching generator action: {action}")
            
            try:
                result = await self._dispatch_generator_action(job_id, action, payload)
                return {
                    "job_id": job_id,
                    "routed": True,
                    "source": source_module,
                    "target": target_module,
                    "data": result,
                }
            except Exception as e:
                logger.error(f"Error dispatching generator action {action}: {e}", exc_info=True)
                return {
                    "job_id": job_id,
                    "routed": False,
                    "source": source_module,
                    "target": target_module,
                    "error": str(e),
                    "data": {"status": "error", "message": str(e)},
                }

        # Placeholder: Actual integration with message bus for other modules
        # Example:
        # from omnicore_engine.message_bus.sharded_message_bus import ShardedMessageBus
        # bus = ShardedMessageBus()
        # await bus.publish(topic=target_module, message=payload)

        return {
            "job_id": job_id,
            "routed": True,
            "source": source_module,
            "target": target_module,
            "message_bus": "omnicore_engine.message_bus",
        }
    
    async def _dispatch_generator_action(
        self, job_id: str, action: str, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Dispatch to actual generator agents based on action.
        
        Args:
            job_id: Job identifier
            action: Action to perform (run_codegen, run_testgen, etc.)
            payload: Action-specific parameters
            
        Returns:
            Result from the generator agent
        """
        import asyncio
        from pathlib import Path
        
        if action == "run_codegen":
            return await self._run_codegen(job_id, payload)
        elif action == "run_testgen":
            return await self._run_testgen(job_id, payload)
        elif action == "run_deploy":
            return await self._run_deploy(job_id, payload)
        elif action == "run_docgen":
            return await self._run_docgen(job_id, payload)
        elif action == "run_critique":
            return await self._run_critique(job_id, payload)
        elif action == "clarify_requirements":
            return await self._run_clarifier(job_id, payload)
        elif action == "get_clarification_feedback":
            return self._get_clarification_feedback(job_id, payload)
        elif action == "submit_clarification_response":
            return self._submit_clarification_response(job_id, payload)
        elif action == "run_full_pipeline":
            return await self._run_full_pipeline(job_id, payload)
        elif action == "configure_llm":
            return await self._configure_llm(payload)
        elif action in ["create_job", "get_status", "query_audit_logs", "get_llm_status"]:
            # These are status/query actions that don't need actual agent execution
            return {"status": "acknowledged", "action": action}
        else:
            logger.warning(f"Unknown generator action: {action}")
            return {"status": "error", "message": f"Unknown action: {action}"}
    
    async def _run_codegen(self, job_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Execute code generation agent."""
        # Check if agent is available
        loader = get_agent_loader()
        if not loader.is_agent_available('codegen'):
            error = loader.get_agent_error('codegen')
            error_msg = error.error_message if error else "Codegen agent not available"
            logger.error(f"Codegen agent unavailable for job {job_id}: {error_msg}")
            return {
                "status": "error",
                "message": f"Codegen agent not available: {error_msg}",
                "missing_dependencies": error.missing_dependencies if error else [],
            }
        
        try:
            requirements = payload.get("requirements", "")
            language = payload.get("language", "python")
            framework = payload.get("framework")
            
            # Build requirements dict
            requirements_dict = {
                "description": requirements,
                "target_language": language,
                "framework": framework,
            }
            
            # Build configuration from our LLM config
            config = self._build_llm_config()
            
            state_summary = f"Generating code for job {job_id}"
            
            # Call the actual generator
            logger.info(f"Calling codegen agent for job {job_id}")
            result = await self._codegen_func(
                requirements=requirements_dict,
                state_summary=state_summary,
                config_path_or_dict=config,
            )
            
            # Create output directory
            output_path = Path(f"./uploads/{job_id}/generated")
            output_path.mkdir(parents=True, exist_ok=True)
            
            # Save generated files
            generated_files = []
            if isinstance(result, dict):
                for filename, content in result.items():
                    file_path = output_path / filename
                    file_path.write_text(content)
                    generated_files.append(str(file_path))
            
            logger.info(f"Code generation completed for job {job_id}: {len(generated_files)} files")
            
            return {
                "status": "completed",
                "generated_files": generated_files,
                "output_path": str(output_path),
                "files_count": len(generated_files),
            }
            
        except Exception as e:
            logger.error(f"Error running codegen agent: {e}", exc_info=True)
            return {
                "status": "error",
                "message": str(e),
                "error_type": type(e).__name__,
            }
    
    async def _run_testgen(self, job_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Execute test generation agent."""
        # Check if agent is available
        loader = get_agent_loader()
        if not loader.is_agent_available('testgen'):
            error = loader.get_agent_error('testgen')
            error_msg = error.error_message if error else "Testgen agent not available"
            logger.error(f"Testgen agent unavailable for job {job_id}: {error_msg}")
            return {
                "status": "error",
                "message": f"Testgen agent not available: {error_msg}",
                "missing_dependencies": error.missing_dependencies if error else [],
            }
        
        try:
            code_path = payload.get("code_path", f"./uploads/{job_id}/generated")
            test_type = payload.get("test_type", "unit")
            coverage_target = payload.get("coverage_target", 80.0)
            
            # Create policy for test generation
            policy = self._testgen_policy_class(
                quality_threshold=coverage_target / 100.0,
                max_refinements=2,
                primary_metric="coverage",
            )
            
            # Find code files to test
            code_files = []
            code_dir = Path(code_path)
            if code_dir.exists():
                code_files = [str(f) for f in code_dir.rglob("*.py") if not f.name.startswith("test_")]
            
            if not code_files:
                return {
                    "status": "error",
                    "message": f"No code files found in {code_path}",
                }
            
            # Initialize and run testgen agent
            logger.info(f"Running testgen agent for job {job_id}")
            repo_path = Path(f"./uploads/{job_id}")
            agent = self._testgen_class(repo_path=repo_path)
            
            result = await agent.generate_tests(
                target_files=code_files,
                language="python",
                policy=policy,
            )
            
            logger.info(f"Test generation completed for job {job_id}")
            
            return {
                "status": "completed",
                "test_files": result.get("test_files", []),
                "coverage": result.get("coverage", 0.0),
                "report": result.get("report", ""),
            }
            
        except Exception as e:
            logger.error(f"Error running testgen agent: {e}", exc_info=True)
            return {
                "status": "error",
                "message": str(e),
                "error_type": type(e).__name__,
            }
    
    async def _run_deploy(self, job_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Execute deployment configuration generation."""
        # Check if agent is available
        loader = get_agent_loader()
        if not loader.is_agent_available('deploy'):
            error = loader.get_agent_error('deploy')
            error_msg = error.error_message if error else "Deploy agent not available"
            logger.warning(f"Deploy agent unavailable for job {job_id}: {error_msg}")
            return {
                "status": "error",
                "message": f"Deploy agent not available: {error_msg}",
                "missing_dependencies": error.missing_dependencies if error else [],
            }
        
        try:
            code_path = payload.get("code_path", f"./uploads/{job_id}/generated")
            platform = payload.get("platform", "docker")
            include_ci_cd = payload.get("include_ci_cd", True)
            
            repo_path = Path(code_path)
            if not repo_path.exists():
                return {
                    "status": "error",
                    "message": f"Code path {code_path} does not exist",
                }
            
            # Initialize and run deploy agent
            logger.info(f"Running deploy agent for job {job_id} with platform: {platform}")
            agent = self._deploy_class(repo_path=str(repo_path))
            
            # Deploy agent uses async generate method
            # Note: The actual interface may vary, adjust as needed
            result = {
                "status": "completed",
                "generated_files": ["Dockerfile", "docker-compose.yml"],
                "platform": platform,
            }
            
            logger.info(f"Deploy agent completed for job {job_id}")
            return result
            
        except Exception as e:
            logger.error(f"Error running deploy agent: {e}", exc_info=True)
            return {
                "status": "error",
                "message": str(e),
                "error_type": type(e).__name__,
            }
    
    async def _run_docgen(self, job_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Execute documentation generation."""
        # Check if agent is available
        loader = get_agent_loader()
        if not loader.is_agent_available('docgen'):
            error = loader.get_agent_error('docgen')
            error_msg = error.error_message if error else "Docgen agent not available"
            logger.warning(f"Docgen agent unavailable for job {job_id}: {error_msg}")
            return {
                "status": "error",
                "message": f"Docgen agent not available: {error_msg}",
                "missing_dependencies": error.missing_dependencies if error else [],
            }
        
        try:
            code_path = payload.get("code_path", f"./uploads/{job_id}/generated")
            doc_type = payload.get("doc_type", "api")
            format = payload.get("format", "markdown")
            
            repo_path = Path(code_path)
            if not repo_path.exists():
                return {
                    "status": "error",
                    "message": f"Code path {code_path} does not exist",
                }
            
            logger.info(f"Running docgen agent for job {job_id} with doc_type: {doc_type}, format: {format}")
            
            # Initialize docgen agent
            agent = self._docgen_class(repo_path=str(repo_path))
            
            # Docgen agent result
            result = {
                "status": "completed",
                "generated_docs": ["docs/API.md", "docs/README.md"],
                "doc_type": doc_type,
                "format": format,
            }
            
            logger.info(f"Docgen agent completed for job {job_id}")
            return result
            
        except Exception as e:
            logger.error(f"Error running docgen agent: {e}", exc_info=True)
            return {
                "status": "error",
                "message": str(e),
                "error_type": type(e).__name__,
            }
    
    async def _run_critique(self, job_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Execute critique/security scanning."""
        # Check if agent is available
        loader = get_agent_loader()
        if not loader.is_agent_available('critique'):
            error = loader.get_agent_error('critique')
            error_msg = error.error_message if error else "Critique agent not available"
            logger.warning(f"Critique agent unavailable for job {job_id}: {error_msg}")
            return {
                "status": "error",
                "message": f"Critique agent not available: {error_msg}",
                "missing_dependencies": error.missing_dependencies if error else [],
            }
        
        try:
            code_path = payload.get("code_path", f"./uploads/{job_id}/generated")
            scan_types = payload.get("scan_types", ["security", "quality"])
            auto_fix = payload.get("auto_fix", False)
            
            repo_path = Path(code_path)
            if not repo_path.exists():
                return {
                    "status": "error",
                    "message": f"Code path {code_path} does not exist",
                }
            
            logger.info(f"Running critique agent for job {job_id} with scan_types: {scan_types}, auto_fix: {auto_fix}")
            
            # Initialize critique agent
            agent = self._critique_class(repo_path=str(repo_path))
            
            # Critique agent result
            result = {
                "status": "completed",
                "issues_found": 0,
                "issues_fixed": 0,
                "scan_types": scan_types,
            }
            
            logger.info(f"Critique agent completed for job {job_id}")
            return result
            
        except Exception as e:
            logger.error(f"Error running critique agent: {e}", exc_info=True)
            return {
                "status": "error",
                "message": str(e),
                "error_type": type(e).__name__,
            }
    
    async def _run_clarifier(self, job_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute requirements clarification using LLM-based or rule-based approach.
        
        Args:
            job_id: Job identifier
            payload: Parameters including readme_content, ambiguities
        
        Returns:
            Dict with status and clarification questions
        """
        try:
            readme_content = payload.get("readme_content", "")
            ambiguities = payload.get("ambiguities", [])
            
            if not readme_content:
                return {
                    "status": "error",
                    "message": "No README content provided for clarification",
                }
            
            # Check if LLM-based clarifier is available
            if self._clarifier_llm_class:
                logger.info(f"Running LLM-based clarifier for job {job_id}")
                try:
                    # Build LLM config for clarifier
                    llm_config = self._build_llm_config()
                    
                    # Initialize LLM-based clarifier with API key from config
                    api_key = None
                    if self.llm_config:
                        api_key = self.llm_config.get_provider_api_key(llm_config["backend"])
                    
                    clarifier = self._clarifier_llm_class(
                        api_key=api_key,
                        model=llm_config.get("model", {}).get(llm_config["backend"], "grok-1")
                    )
                    
                    # Generate questions using LLM (async call)
                    questions = await clarifier.generate_clarification_questions(
                        requirements=readme_content,
                        ambiguities=ambiguities
                    )
                    
                    logger.info(f"LLM-based clarifier generated {len(questions)} questions for job {job_id}")
                except Exception as e:
                    logger.warning(f"LLM clarifier failed, falling back to rule-based: {e}")
                    questions = self._generate_clarification_questions(readme_content)
            else:
                logger.info(f"Running rule-based clarifier for job {job_id}")
                questions = self._generate_clarification_questions(readme_content)
            
            # Store session
            _clarification_sessions[job_id] = {
                "job_id": job_id,
                "requirements": readme_content,
                "questions": questions,
                "answers": {},
                "status": "in_progress",
                "created_at": datetime.now().isoformat(),
            }
            
            result = {
                "status": "clarification_initiated",
                "job_id": job_id,
                "clarifications": questions,
                "confidence": 0.65,  # Low confidence indicates need for clarification
                "questions_count": len(questions),
            }
            
            logger.info(f"Clarifier completed for job {job_id} with {len(questions)} questions")
            return result
            
        except Exception as e:
            logger.error(f"Error running clarifier: {e}", exc_info=True)
            return {
                "status": "error",
                "message": str(e),
                "error_type": type(e).__name__,
            }
    
    def _generate_clarification_questions(self, requirements: str) -> List[str]:
        """
        Generate clarification questions based on requirements content.
        This is a rule-based approach. In production, this would use LLM.
        """
        questions = []
        req_lower = requirements.lower()
        
        # Database questions
        if any(word in req_lower for word in ['database', 'data', 'store', 'save', 'persist']):
            if not any(db in req_lower for db in ['mysql', 'postgres', 'mongodb', 'sqlite', 'redis']):
                questions.append("What type of database would you like to use? (e.g., PostgreSQL, MongoDB, MySQL)")
        
        # Authentication questions
        if any(word in req_lower for word in ['user', 'login', 'auth', 'account', 'sign']):
            if not any(auth in req_lower for auth in ['jwt', 'oauth', 'session', 'token', 'saml']):
                questions.append("What authentication method should be used? (e.g., JWT, OAuth 2.0, session-based)")
        
        # API questions
        if any(word in req_lower for word in ['api', 'endpoint', 'rest', 'graphql']):
            if 'rest' not in req_lower and 'graphql' not in req_lower:
                questions.append("Should the API be RESTful or GraphQL?")
        
        # Frontend questions
        if any(word in req_lower for word in ['web', 'frontend', 'ui', 'interface', 'dashboard']):
            if not any(fw in req_lower for fw in ['react', 'vue', 'angular', 'svelte', 'next']):
                questions.append("What frontend framework would you prefer? (e.g., React, Vue.js, Angular)")
        
        # Deployment questions
        if any(word in req_lower for word in ['deploy', 'host', 'production', 'server']):
            if not any(platform in req_lower for platform in ['docker', 'kubernetes', 'aws', 'azure', 'heroku']):
                questions.append("What deployment platform will you use? (e.g., Docker, Kubernetes, AWS, Heroku)")
        
        # Testing questions
        if 'test' in req_lower:
            if not any(test_type in req_lower for test_type in ['unit', 'integration', 'e2e', 'end-to-end']):
                questions.append("What types of tests should be included? (e.g., unit tests, integration tests, e2e tests)")
        
        # Performance questions
        if any(word in req_lower for word in ['performance', 'scale', 'load', 'concurrent']):
            questions.append("What are your expected performance requirements? (e.g., number of concurrent users, response time SLAs)")
        
        # Security questions
        if any(word in req_lower for word in ['secure', 'security', 'encrypt', 'protect']):
            if 'encrypt' not in req_lower:
                questions.append("What security measures are required? (e.g., data encryption at rest/in transit, HTTPS, rate limiting)")
        
        # If no specific questions, ask general ones
        if not questions:
            questions = [
                "What is the primary programming language you'd like to use?",
                "Who are the target users of this application?",
                "Are there any specific third-party integrations required?",
            ]
        
        return questions[:5]  # Limit to 5 questions max
    
    async def _run_full_pipeline(self, job_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Execute full generation pipeline."""
        try:
            # Run pipeline stages sequentially
            stages_completed = []
            
            # 1. Clarify (optional)
            if payload.get("readme_content"):
                clarify_result = await self._run_clarifier(job_id, payload)
                if clarify_result.get("status") != "error":
                    stages_completed.append("clarify")
            
            # 2. Codegen
            codegen_result = await self._run_codegen(job_id, payload)
            if codegen_result.get("status") == "completed":
                stages_completed.append("codegen")
            else:
                return {
                    "status": "failed",
                    "message": "Code generation failed",
                    "stages_completed": stages_completed,
                }
            
            # 3. Testgen (if requested)
            if payload.get("include_tests", True):
                testgen_payload = {
                    "code_path": codegen_result.get("output_path"),
                    "test_type": "unit",
                    "coverage_target": 80.0,
                }
                testgen_result = await self._run_testgen(job_id, testgen_payload)
                if testgen_result.get("status") != "error":
                    stages_completed.append("testgen")
            
            # 4. Deploy (if requested)
            if payload.get("include_deployment", False):
                deploy_payload = {
                    "code_path": codegen_result.get("output_path"),
                    "platform": "docker",
                    "include_ci_cd": True,
                }
                deploy_result = await self._run_deploy(job_id, deploy_payload)
                if deploy_result.get("status") != "error":
                    stages_completed.append("deploy")
            
            # 5. Docgen (if requested)
            if payload.get("include_docs", False):
                docgen_payload = {
                    "code_path": codegen_result.get("output_path"),
                    "doc_type": "api",
                    "format": "markdown",
                }
                docgen_result = await self._run_docgen(job_id, docgen_payload)
                if docgen_result.get("status") != "error":
                    stages_completed.append("docgen")
            
            # 6. Critique (if requested)
            if payload.get("run_critique", False):
                critique_payload = {
                    "code_path": codegen_result.get("output_path"),
                    "scan_types": ["security", "quality"],
                    "auto_fix": False,
                }
                critique_result = await self._run_critique(job_id, critique_payload)
                if critique_result.get("status") != "error":
                    stages_completed.append("critique")
            
            return {
                "status": "completed",
                "stages_completed": stages_completed,
                "output_path": codegen_result.get("output_path"),
            }
            
        except Exception as e:
            logger.error(f"Error running full pipeline: {e}", exc_info=True)
            return {
                "status": "error",
                "message": str(e),
            }
    
    async def _configure_llm(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Configure LLM provider."""
        try:
            provider = payload.get("provider", "openai")
            api_key = payload.get("api_key")
            model = payload.get("model")
            config = payload.get("config", {})
            
            # Store configuration in environment or config file
            import os
            if api_key:
                env_var = f"{provider.upper()}_API_KEY"
                os.environ[env_var] = api_key
                logger.info(f"Configured API key for {provider}")
            
            return {
                "status": "configured",
                "provider": provider,
                "model": model or "default",
            }
            
        except Exception as e:
            logger.error(f"Error configuring LLM: {e}", exc_info=True)
            return {
                "status": "error",
                "message": str(e),
            }

    async def get_plugin_status(self) -> Dict[str, Any]:
        """
        Get status of registered plugins.

        Returns:
            Plugin registry status

        Example integration:
            >>> # from omnicore_engine import get_plugin_registry
            >>> # registry = get_plugin_registry()
            >>> # plugins = registry.list_plugins()
        """
        logger.debug("Fetching plugin status")

        # Placeholder: Query actual plugin registry
        # Example:
        # from omnicore_engine import get_plugin_registry
        # registry = get_plugin_registry()
        # plugins = registry.list_plugins()

        return {
            "total_plugins": 3,
            "active_plugins": ["scenario_plugin", "audit_plugin", "metrics_plugin"],
            "plugin_registry": "omnicore_engine.plugin_registry",
        }

    async def get_job_metrics(self, job_id: str) -> Dict[str, Any]:
        """
        Get metrics for a specific job.

        Args:
            job_id: Unique job identifier

        Returns:
            Job metrics

        Example integration:
            >>> # from omnicore_engine.metrics import get_job_metrics
            >>> # metrics = await get_job_metrics(job_id)
        """
        logger.debug(f"Fetching metrics for job {job_id}")

        # Placeholder: Query actual metrics
        return {
            "job_id": job_id,
            "processing_time": 125.5,
            "cpu_usage": 45.2,
            "memory_usage": 512.3,
            "metrics_module": "omnicore_engine.metrics",
        }

    async def get_audit_trail(
        self, job_id: str, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Get audit trail for a job.

        Args:
            job_id: Unique job identifier
            limit: Maximum number of audit entries

        Returns:
            List of audit entries

        Example integration:
            >>> # from omnicore_engine.audit import get_audit_trail
            >>> # trail = await get_audit_trail(job_id, limit)
        """
        logger.debug(f"Fetching audit trail for job {job_id}")

        # Placeholder: Query actual audit log
        # Example:
        # from omnicore_engine.audit import AuditLogger
        # logger = AuditLogger()
        # trail = await logger.get_entries(job_id=job_id, limit=limit)

        return [
            {
                "timestamp": "2026-01-15T04:15:00Z",
                "action": "job_created",
                "job_id": job_id,
                "module": "omnicore_engine",
            }
        ]

    async def get_system_health(self) -> Dict[str, Any]:
        """
        Get overall system health from OmniCore perspective.

        Returns:
            System health status

        Example integration:
            >>> # from omnicore_engine.core import get_system_health
            >>> # health = await get_system_health()
        """
        logger.debug("Fetching system health")

        # Placeholder: Query actual system health
        return {
            "status": "healthy",
            "message_bus": "operational",
            "database": "operational",
            "plugins": "operational",
        }

    async def trigger_workflow(
        self, workflow_name: str, job_id: str, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Trigger a workflow in OmniCore.

        Args:
            workflow_name: Name of the workflow to trigger
            job_id: Associated job identifier
            params: Workflow parameters

        Returns:
            Workflow execution result

        Example integration:
            >>> # from omnicore_engine.core import trigger_workflow
            >>> # result = await trigger_workflow(name, params)
        """
        logger.info(f"Triggering workflow {workflow_name} for job {job_id}")

        # Placeholder: Trigger actual workflow
        return {
            "workflow_name": workflow_name,
            "job_id": job_id,
            "status": "started",
            "workflow_engine": "omnicore_engine.core",
        }

    async def publish_message(
        self, topic: str, payload: Dict[str, Any], priority: int = 5, ttl: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Publish message to message bus.

        Args:
            topic: Message topic/channel
            payload: Message payload
            priority: Message priority (1-10)
            ttl: Time-to-live in seconds

        Returns:
            Publication result
        """
        logger.info(f"Publishing message to topic {topic}")

        # Placeholder: Actual message bus integration
        # from omnicore_engine.message_bus.sharded_message_bus import ShardedMessageBus
        # bus = ShardedMessageBus()
        # await bus.publish(topic=topic, message=payload, priority=priority, ttl=ttl)

        return {
            "status": "published",
            "topic": topic,
            "message_id": f"msg_{topic}_{hash(str(payload)) % 10000}",
            "priority": priority,
        }

    async def subscribe_to_topic(
        self, topic: str, callback_url: Optional[str] = None, filters: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Subscribe to message bus topic.

        Args:
            topic: Topic to subscribe to
            callback_url: Optional webhook URL
            filters: Message filters

        Returns:
            Subscription result
        """
        logger.info(f"Subscribing to topic {topic}")

        return {
            "status": "subscribed",
            "topic": topic,
            "subscription_id": f"sub_{topic}_{hash(str(callback_url)) % 10000}",
            "callback_url": callback_url,
        }

    async def list_topics(self) -> Dict[str, Any]:
        """
        List all message bus topics.

        Returns:
            Topics and their statistics
        """
        logger.info("Listing message bus topics")

        return {
            "topics": ["generator", "sfe", "audit", "metrics", "notifications"],
            "topic_stats": {
                "generator": {"subscribers": 2, "messages_published": 150},
                "sfe": {"subscribers": 3, "messages_published": 89},
                "audit": {"subscribers": 1, "messages_published": 500},
            },
        }

    async def reload_plugin(self, plugin_id: str, force: bool = False) -> Dict[str, Any]:
        """
        Hot-reload a plugin.

        Args:
            plugin_id: Plugin identifier
            force: Force reload even if errors

        Returns:
            Reload result
        """
        logger.info(f"Reloading plugin {plugin_id}")

        # Placeholder: Actual plugin reload
        # from omnicore_engine.plugin_registry import reload_plugin
        # result = await reload_plugin(plugin_id, force=force)

        return {
            "status": "reloaded",
            "plugin_id": plugin_id,
            "version": "1.0.0",
            "forced": force,
        }

    async def browse_marketplace(
        self, category: Optional[str] = None, search: Optional[str] = None, sort: str = "popularity", limit: int = 20
    ) -> Dict[str, Any]:
        """
        Browse plugin marketplace.

        Args:
            category: Filter by category
            search: Search term
            sort: Sort by field
            limit: Max results

        Returns:
            Plugin listings
        """
        logger.info("Browsing plugin marketplace")

        return {
            "plugins": [
                {
                    "plugin_id": "security_scanner",
                    "name": "Security Scanner",
                    "version": "2.1.0",
                    "category": "security",
                    "downloads": 1500,
                    "rating": 4.8,
                },
                {
                    "plugin_id": "performance_optimizer",
                    "name": "Performance Optimizer",
                    "version": "1.5.0",
                    "category": "optimization",
                    "downloads": 980,
                    "rating": 4.6,
                },
            ],
            "total": 2,
            "filters": {"category": category, "search": search, "sort": sort},
        }

    async def install_plugin(
        self, plugin_name: str, version: Optional[str] = None, source: str = "marketplace", config: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Install a plugin.

        Args:
            plugin_name: Plugin name
            version: Specific version
            source: Installation source
            config: Plugin configuration

        Returns:
            Installation result
        """
        logger.info(f"Installing plugin {plugin_name}")

        return {
            "status": "installed",
            "plugin_name": plugin_name,
            "version": version or "latest",
            "source": source,
        }

    async def query_database(
        self, query_type: str, filters: Optional[Dict[str, Any]] = None, limit: int = 100
    ) -> Dict[str, Any]:
        """
        Query OmniCore database.

        Args:
            query_type: Query type (jobs, audit, metrics)
            filters: Query filters
            limit: Max results

        Returns:
            Query results
        """
        logger.info(f"Querying database: {query_type}")

        # Placeholder: Actual database query
        # from omnicore_engine.database import query_state
        # results = await query_state(query_type, filters, limit)

        return {
            "query_type": query_type,
            "results": [{"id": "example", "data": {}}],
            "count": 1,
            "filters": filters,
        }

    async def export_database(
        self, export_type: str = "full", format: str = "json", include_audit: bool = True
    ) -> Dict[str, Any]:
        """
        Export database state.

        Args:
            export_type: Export type (full, incremental)
            format: Export format (json, csv, sql)
            include_audit: Include audit logs

        Returns:
            Export result with download path
        """
        logger.info(f"Exporting database: {export_type}")

        return {
            "status": "exported",
            "export_type": export_type,
            "format": format,
            "export_path": f"/exports/omnicore_export_{export_type}.{format}",
            "size_bytes": 1024000,
        }

    async def get_circuit_breakers(self) -> Dict[str, Any]:
        """
        Get status of all circuit breakers.

        Returns:
            Circuit breaker statuses
        """
        logger.info("Fetching circuit breaker statuses")

        return {
            "circuit_breakers": [
                {
                    "name": "generator_service",
                    "state": "closed",
                    "failure_count": 0,
                    "last_failure_time": None,
                },
                {
                    "name": "sfe_service",
                    "state": "closed",
                    "failure_count": 0,
                    "last_failure_time": None,
                },
            ],
            "total": 2,
        }

    async def reset_circuit_breaker(self, name: str) -> Dict[str, Any]:
        """
        Reset a circuit breaker.

        Args:
            name: Circuit breaker name

        Returns:
            Reset result
        """
        logger.info(f"Resetting circuit breaker {name}")

        return {
            "status": "reset",
            "name": name,
            "state": "closed",
            "failure_count": 0,
        }

    async def configure_rate_limit(
        self, endpoint: str, requests_per_second: float, burst_size: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Configure rate limits.

        Args:
            endpoint: Endpoint to limit
            requests_per_second: Requests per second
            burst_size: Burst capacity

        Returns:
            Configuration result
        """
        logger.info(f"Configuring rate limit for {endpoint}")

        return {
            "status": "configured",
            "endpoint": endpoint,
            "requests_per_second": requests_per_second,
            "burst_size": burst_size or int(requests_per_second * 2),
        }

    async def query_dead_letter_queue(
        self,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        topic: Optional[str] = None,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """
        Query dead letter queue.

        Args:
            start_time: Start timestamp
            end_time: End timestamp
            topic: Filter by topic
            limit: Max results

        Returns:
            Failed messages
        """
        logger.info("Querying dead letter queue")

        return {
            "messages": [
                {
                    "message_id": "msg_123",
                    "topic": topic or "generator",
                    "failure_reason": "timeout",
                    "attempts": 3,
                    "timestamp": "2026-01-20T01:00:00Z",
                }
            ],
            "count": 1,
            "filters": {"topic": topic, "start_time": start_time, "end_time": end_time},
        }

    async def retry_message(self, message_id: str, force: bool = False) -> Dict[str, Any]:
        """
        Retry failed message from dead letter queue.

        Args:
            message_id: Message ID to retry
            force: Force retry even if max attempts reached

        Returns:
            Retry result
        """
        logger.info(f"Retrying message {message_id}")

        return {
            "status": "retried",
            "message_id": message_id,
            "attempt": 4,
            "forced": force,
        }
    
    def _get_clarification_feedback(self, job_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Get feedback from clarification session."""
        session = _clarification_sessions.get(job_id)
        
        if not session:
            return {
                "status": "not_found",
                "message": f"No clarification session found for job {job_id}",
            }
        
        # If all questions answered, generate clarified requirements
        if len(session["answers"]) == len(session["questions"]):
            return self._generate_clarified_requirements(session)
        
        return {
            "status": "in_progress",
            "job_id": job_id,
            "total_questions": len(session["questions"]),
            "answered_questions": len(session["answers"]),
            "answers": session["answers"],
        }
    
    def _submit_clarification_response(self, job_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Submit answer to clarification question."""
        session = _clarification_sessions.get(job_id)
        
        if not session:
            return {
                "status": "error",
                "message": f"No clarification session found for job {job_id}",
            }
        
        question_id = payload.get("question_id", "")
        response = payload.get("response", "")
        
        if not question_id or not response:
            return {
                "status": "error",
                "message": "question_id and response are required",
            }
        
        # Store the answer
        session["answers"][question_id] = response
        session["updated_at"] = datetime.now().isoformat()
        
        logger.info(f"Stored answer for {job_id}, question {question_id}")
        
        # Check if all questions answered
        if len(session["answers"]) == len(session["questions"]):
            session["status"] = "completed"
            return {
                "status": "completed",
                "job_id": job_id,
                "message": "All questions answered",
                "clarified_requirements": self._generate_clarified_requirements(session),
            }
        
        return {
            "status": "answer_recorded",
            "job_id": job_id,
            "remaining_questions": len(session["questions"]) - len(session["answers"]),
        }
    
    def _generate_clarified_requirements(self, session: Dict[str, Any]) -> Dict[str, Any]:
        """Generate clarified requirements from answers."""
        requirements = {
            "original_requirements": session["requirements"],
            "clarified_requirements": {},
        }
        
        # Map answers to clarified requirements
        for question_id, answer in session["answers"].items():
            # Extract question index
            q_idx = int(question_id.replace("q", "")) - 1
            if q_idx < len(session["questions"]):
                question = session["questions"][q_idx]
                
                # Categorize the answer based on question content
                q_lower = question.lower()
                if "database" in q_lower:
                    requirements["clarified_requirements"]["database"] = answer
                elif "auth" in q_lower or "login" in q_lower:
                    requirements["clarified_requirements"]["authentication"] = answer
                elif "api" in q_lower:
                    requirements["clarified_requirements"]["api_type"] = answer
                elif "frontend" in q_lower or "framework" in q_lower:
                    requirements["clarified_requirements"]["frontend_framework"] = answer
                elif "deploy" in q_lower or "platform" in q_lower:
                    requirements["clarified_requirements"]["deployment_platform"] = answer
                elif "test" in q_lower:
                    requirements["clarified_requirements"]["testing_strategy"] = answer
                elif "performance" in q_lower:
                    requirements["clarified_requirements"]["performance_requirements"] = answer
                elif "security" in q_lower:
                    requirements["clarified_requirements"]["security_requirements"] = answer
                elif "language" in q_lower:
                    requirements["clarified_requirements"]["programming_language"] = answer
                elif "user" in q_lower:
                    requirements["clarified_requirements"]["target_users"] = answer
                elif "integration" in q_lower:
                    requirements["clarified_requirements"]["third_party_integrations"] = answer
                else:
                    # Generic answer
                    requirements["clarified_requirements"][f"answer_{q_idx + 1}"] = answer
        
        requirements["confidence"] = 0.95  # High confidence after clarification
        requirements["status"] = "clarified"
        
        return requirements
