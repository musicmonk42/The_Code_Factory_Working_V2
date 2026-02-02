"""
Service for interacting with the OmniCore Engine module.

This service provides a mockable interface to the omnicore_engine module for
job coordination, plugin management, and inter-module communication.

This module implements proper agent integration with:
- Configuration-based LLM provider selection
- Graceful degradation when agents unavailable
- Proper error handling and logging
- Environment variable support for API keys
- Industry-standard observability (metrics, tracing, structured logging)
"""

import aiofiles
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from server.utils.agent_loader import get_agent_loader

logger = logging.getLogger(__name__)

# Observability imports with graceful degradation
try:
    from opentelemetry import trace
    from opentelemetry.trace import Status, StatusCode
    TRACING_AVAILABLE = True
    tracer = trace.get_tracer(__name__)
except ImportError:
    TRACING_AVAILABLE = False
    logger.warning("OpenTelemetry not available, tracing disabled")
    
try:
    from prometheus_client import Counter, Histogram, Gauge
    from prometheus_client.registry import REGISTRY
    METRICS_AVAILABLE = True
    
    # Helper functions for safe metric registration (idempotent pattern)
    def _get_or_create_counter(name: str, description: str, labelnames: list = None):
        """Create a Counter or return existing one with same name."""
        labelnames = labelnames or []
        try:
            collector = REGISTRY._names_to_collectors.get(name)
            if collector is not None:
                return collector
        except (AttributeError, KeyError):
            pass
        try:
            return Counter(name, description, labelnames)
        except ValueError as e:
            if "Duplicated timeseries" in str(e):
                existing = REGISTRY._names_to_collectors.get(name)
                if existing is not None:
                    return existing
            raise
    
    def _get_or_create_histogram(name: str, description: str, labelnames: list = None):
        """Create a Histogram or return existing one with same name."""
        labelnames = labelnames or []
        try:
            collector = REGISTRY._names_to_collectors.get(name)
            if collector is not None:
                return collector
        except (AttributeError, KeyError):
            pass
        try:
            return Histogram(name, description, labelnames)
        except ValueError as e:
            if "Duplicated timeseries" in str(e):
                existing = REGISTRY._names_to_collectors.get(name)
                if existing is not None:
                    return existing
            raise
    
    # Define metrics for code generation observability using safe registration
    codegen_requests_total = _get_or_create_counter(
        'codegen_requests_total',
        'Total number of code generation requests',
        ['job_id', 'language', 'status']
    )
    codegen_files_generated = _get_or_create_counter(
        'codegen_files_generated_total',
        'Total number of files generated',
        ['job_id', 'language']
    )
    codegen_duration_seconds = _get_or_create_histogram(
        'codegen_duration_seconds',
        'Code generation duration in seconds',
        ['job_id', 'language']
    )
    codegen_file_size_bytes = _get_or_create_histogram(
        'codegen_file_size_bytes',
        'Size of generated files in bytes',
        ['job_id', 'file_type']
    )
    codegen_errors_total = _get_or_create_counter(
        'codegen_errors_total',
        'Total number of code generation errors',
        ['job_id', 'error_type']
    )
except ImportError:
    METRICS_AVAILABLE = False
    logger.warning("Prometheus client not available, metrics disabled")

# Import configuration and helper functions
try:
    from server.config import (
        detect_available_llm_provider,
        get_agent_config,
        get_default_model_for_provider,
        get_llm_config,
    )
    CONFIG_AVAILABLE = True
except ImportError:
    logger.warning("server.config not available, using default configuration")
    CONFIG_AVAILABLE = False
    # Provide fallback implementations
    def detect_available_llm_provider():
        return None
    def get_default_model_for_provider(provider):
        return "gpt-4"

# In-memory storage for clarification sessions
_clarification_sessions = {}


# Custom exception for security violations
class SecurityError(Exception):
    """Raised when a security violation is detected."""
    pass


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
        
        # Track LLM provider status
        self._llm_status = {
            "provider": None,
            "configured": False,
            "validated": False,
            "error": None,
        }
        
        # Initialize core OmniCore components
        self._message_bus = None
        self._plugin_registry = None
        self._metrics_client = None
        self._audit_client = None
        self._omnicore_components_available = {
            "message_bus": False,
            "plugin_registry": False,
            "metrics": False,
            "audit": False,
        }
        
        # Validate LLM provider configuration
        self._validate_llm_configuration()
        
        # DON'T call _load_agents() here to avoid circular imports
        self._agents_loaded = False  # Track if agents have been loaded
        
        # Initialize OmniCore integrations
        self._init_omnicore_components()
        
        # Log that agents will be loaded on-demand
        logger.info("OmniCore initialized - agents will be loaded on demand")
        
        # Log system state and what triggers agent execution
        self._log_system_ready_state()
    
    def _validate_llm_configuration(self):
        """
        Validate LLM provider configuration and log status.
        
        This helps diagnose issues where agents load but fail silently
        due to missing or invalid API keys.
        """
        provider = None
        api_key_configured = False
        
        if self.llm_config:
            provider = self.llm_config.default_llm_provider
            api_key_configured = self.llm_config.is_provider_configured(provider)
            
            if not api_key_configured:
                # Try auto-detection
                auto_provider = detect_available_llm_provider()
                if auto_provider:
                    provider = auto_provider
                    api_key_configured = True
                    logger.info(f"Auto-detected LLM provider: {auto_provider}")
        else:
            # Check environment directly
            auto_provider = detect_available_llm_provider()
            if auto_provider:
                provider = auto_provider
                api_key_configured = True
        
        # Use explicit status when no provider is configured
        if api_key_configured:
            self._llm_status["provider"] = provider
        else:
            # Keep the intended provider for diagnostics, but indicate it's not configured
            self._llm_status["provider"] = provider or "none"
        
        self._llm_status["configured"] = api_key_configured
        
        if api_key_configured:
            logger.info(f"✓ LLM provider '{provider}' is configured with API key")
        else:
            intended_provider = provider or "openai (default)"
            logger.warning(
                f"⚠ LLM provider '{intended_provider}' API key NOT configured. "
                "Agents will load but may fail when executing jobs."
            )
            logger.warning(
                "To configure an LLM provider, set one of the following environment variables:\n"
                "  - OPENAI_API_KEY for OpenAI (GPT-4)\n"
                "  - ANTHROPIC_API_KEY for Anthropic (Claude)\n"
                "  - XAI_API_KEY or GROK_API_KEY for xAI (Grok)\n"
                "  - GOOGLE_API_KEY for Google (Gemini)\n"
                "  - OLLAMA_HOST for Ollama (local LLM)"
            )
            self._llm_status["error"] = "API key not configured"
    
    def _log_system_ready_state(self):
        """
        Log the system's ready state and clarify what triggers agent execution.
        
        This helps users understand that the system is idle and waiting for input.
        """
        # Build LLM status message
        if self._llm_status["configured"]:
            llm_msg = f"LLM Provider: {self._llm_status['provider']} (configured)"
        else:
            llm_msg = f"LLM Provider: {self._llm_status['provider']} (NOT CONFIGURED - jobs will fail)"
        
        # Build agent status message
        available_agents = [k for k, v in self.agents_available.items() if v]
        agents_msg = ', '.join(available_agents) if available_agents else 'None'
        
        # Log as a single structured message for better log readability
        status_message = (
            "\n"
            "============================================================\n"
            "SYSTEM STATUS: Ready and waiting for input\n"
            "============================================================\n"
            f"  {llm_msg}\n"
            f"  Available Agents: {agents_msg}\n"
            "\n"
            "IMPORTANT: Agents are now PASSIVE and waiting for jobs.\n"
            "No code will be generated until you submit a job request.\n"
            "\n"
            "To trigger code generation, use one of these methods:\n"
            "  1. POST /api/jobs/ - Create a new job\n"
            "  2. POST /api/generator/upload - Upload a README file\n"
            "  3. POST /api/omnicore/route - Route a job directly\n"
            "\n"
            "Monitor job status at: GET /api/jobs/{job_id}/progress\n"
            "============================================================"
        )
        
        if self._llm_status["configured"]:
            logger.info(status_message)
        else:
            logger.warning(status_message)
    
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
    
    def _ensure_agents_loaded(self):
        """Lazy-load agents on first use to avoid circular imports."""
        if not self._agents_loaded:
            logger.info("Loading agents on demand...")
            self._load_agents()
            self._agents_loaded = True
            
            # Log initialization status after loading
            available = [k for k, v in self.agents_available.items() if v]
            unavailable = [k for k, v in self.agents_available.items() if not v]
            
            if available:
                logger.info(f"Agents loaded. Available: {', '.join(available)}")
            if unavailable:
                logger.warning(f"Some agents unavailable: {', '.join(unavailable)}")
                if self.agent_config and self.agent_config.strict_mode:
                    raise RuntimeError(
                        f"STRICT_MODE: Required agents are unavailable: {', '.join(unavailable)}. "
                        f"Install required dependencies or disable strict mode."
                    )
    
    def _build_llm_config(self) -> Dict[str, Any]:
        """
        Build LLM configuration dict for agents from our config.
        Auto-detects available LLM provider if default is not configured.
        
        Returns:
            Configuration dictionary compatible with agent requirements
        """
        if not self.llm_config:
            # Fallback configuration when config module not available
            # Try to auto-detect from environment
            auto_provider = detect_available_llm_provider()
            if auto_provider:
                logger.info(f"Auto-detected LLM provider: {auto_provider}")
                return {
                    "backend": auto_provider,
                    "model": {auto_provider: get_default_model_for_provider(auto_provider)},
                    "ensemble_enabled": False,
                }
            else:
                logger.warning("No LLM provider configured or auto-detected")
                return {
                    "backend": "openai",
                    "model": {"openai": "gpt-4"},
                    "ensemble_enabled": False,
                }
        
        provider = self.llm_config.default_llm_provider
        
        # Auto-detect if the default provider is not configured
        if not self.llm_config.is_provider_configured(provider):
            logger.warning(
                f"Default LLM provider '{provider}' is not configured. "
                "Attempting auto-detection..."
            )
            
            auto_provider = detect_available_llm_provider()
            if auto_provider:
                logger.info(f"Auto-detected LLM provider: {auto_provider}")
                provider = auto_provider
                # Update model to match auto-detected provider
                model = self.llm_config.get_provider_model(provider)
            else:
                logger.error(
                    "No LLM provider configured. Please set API keys in environment variables:\n"
                    "  - OPENAI_API_KEY for OpenAI\n"
                    "  - ANTHROPIC_API_KEY for Anthropic/Claude\n"
                    "  - XAI_API_KEY or GROK_API_KEY for xAI/Grok\n"
                    "  - GOOGLE_API_KEY for Google/Gemini\n"
                    "  - OLLAMA_HOST for Ollama (local)"
                )
                # Use default provider anyway, might be mocked
                model = self.llm_config.get_provider_model(provider)
        else:
            model = self.llm_config.get_provider_model(provider)
            logger.info(f"Using configured LLM provider: {provider} with model: {model}")
        
        api_key = self.llm_config.get_provider_api_key(provider)
        
        # Set environment variable for the agent to use
        if api_key:
            # For xAI/Grok, set both XAI_API_KEY and GROK_API_KEY
            if provider == "grok":
                os.environ["XAI_API_KEY"] = api_key
                os.environ["GROK_API_KEY"] = api_key
            else:
                env_var = f"{provider.upper()}_API_KEY"
                os.environ[env_var] = api_key
        
        # For Ollama, set the host
        if provider == "ollama" and self.llm_config.ollama_host:
            os.environ["OLLAMA_HOST"] = self.llm_config.ollama_host
        
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
        
        # Add Ollama host if configured
        if provider == "ollama" and self.llm_config.ollama_host:
            config["ollama_host"] = self.llm_config.ollama_host
        
        return config
    
    def _init_omnicore_components(self):
        """
        Initialize OmniCore Engine components with graceful degradation.
        
        Attempts to initialize:
        - ShardedMessageBus for inter-module communication
        - PluginRegistry for plugin management
        - Metrics client for monitoring
        - Audit client for compliance logging
        
        All components are optional and the service will operate in degraded mode
        if any component is unavailable.
        """
        # Initialize Message Bus
        try:
            # Skip during pytest collection to avoid event loop requirements
            if os.getenv("PYTEST_CURRENT_TEST") or os.getenv("PYTEST_COLLECTING"):
                logger.info("Skipping message bus initialization during pytest collection")
                self._message_bus = None
                return
                
            from omnicore_engine.message_bus.sharded_message_bus import ShardedMessageBus
            self._message_bus = ShardedMessageBus()
            self._omnicore_components_available["message_bus"] = True
            logger.info("✓ Message bus initialized successfully")
        except ImportError as e:
            logger.warning(f"Message bus not available (import failed): {e}")
        except Exception as e:
            logger.warning(f"Message bus initialization failed: {e}", exc_info=True)
        
        # Initialize Plugin Registry
        try:
            from omnicore_engine.plugin_registry import PLUGIN_REGISTRY
            self._plugin_registry = PLUGIN_REGISTRY
            self._omnicore_components_available["plugin_registry"] = True
            logger.info("✓ Plugin registry connected successfully")
        except ImportError as e:
            logger.warning(f"Plugin registry not available: {e}")
        except Exception as e:
            logger.warning(f"Plugin registry connection failed: {e}", exc_info=True)
        
        # Initialize Metrics Client
        try:
            from omnicore_engine import metrics
            self._metrics_client = metrics
            self._omnicore_components_available["metrics"] = True
            logger.info("✓ Metrics client connected successfully")
        except ImportError as e:
            logger.warning(f"Metrics client not available: {e}")
        except Exception as e:
            logger.warning(f"Metrics client connection failed: {e}", exc_info=True)
        
        # Initialize Audit Client
        try:
            from omnicore_engine.audit import ExplainAudit
            self._audit_client = ExplainAudit()
            self._omnicore_components_available["audit"] = True
            logger.info("✓ Audit client initialized successfully")
        except ImportError as e:
            logger.warning(f"Audit client not available: {e}")
        except Exception as e:
            logger.warning(f"Audit client initialization failed: {e}", exc_info=True)
        
        # Log component availability summary
        available_components = [k for k, v in self._omnicore_components_available.items() if v]
        unavailable_components = [k for k, v in self._omnicore_components_available.items() if not v]
        
        if available_components:
            logger.info(f"OmniCore components available: {', '.join(available_components)}")
        if unavailable_components:
            logger.info(f"OmniCore components unavailable (using fallback): {', '.join(unavailable_components)}")
            # Clarify that fallback mode doesn't block task execution
            logger.info(
                "Note: Fallback mode is active for unavailable components. "
                "Task execution will proceed normally - only logging/audit features may be limited."
            )
    
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
    
    def get_llm_status(self) -> Dict[str, Any]:
        """
        Get the current LLM provider status.
        
        Returns:
            Dictionary with LLM provider status information
        """
        return {
            "provider": self._llm_status.get("provider", "unknown"),
            "configured": self._llm_status.get("configured", False),
            "validated": self._llm_status.get("validated", False),
            "error": self._llm_status.get("error"),
            "available_providers": (
                self.llm_config.get_available_providers() if self.llm_config else []
            ),
        }
    
    def get_system_status(self) -> Dict[str, Any]:
        """
        Get comprehensive system status including agents and LLM.
        
        Returns:
            Dictionary with full system status
        """
        return {
            "state": "ready_idle",
            "message": "System is ready and waiting for job requests",
            "llm_status": self.get_llm_status(),
            "agents": {
                "available": [k for k, v in self.agents_available.items() if v],
                "unavailable": [k for k, v in self.agents_available.items() if not v],
            },
            "components": {
                "available": [k for k, v in self._omnicore_components_available.items() if v],
                "unavailable": [k for k, v in self._omnicore_components_available.items() if not v],
            },
            "instructions": {
                "to_generate_code": "POST /api/jobs/ with requirements",
                "to_upload_readme": "POST /api/generator/upload",
                "to_check_status": "GET /api/jobs/{job_id}/progress",
            },
        }

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
        # Log intent parsing event when job is received
        logger.info(f"Intent Parsed: Job {job_id} received from {source_module} targeting {target_module}")
        logger.info(f"Job Received: {job_id} with action: {payload.get('action', 'unknown')}")
        
        logger.info(f"Routing job {job_id} from {source_module} to {target_module}")

        # If target is generator, dispatch to actual generator agents
        if target_module == "generator":
            action = payload.get("action")
            logger.info(f"Task Dispatched: Job {job_id} dispatching generator action: {action}")
            
            try:
                result = await self._dispatch_generator_action(job_id, action, payload)
                logger.info(f"Task Completed: Job {job_id} action {action} finished successfully")
                return {
                    "job_id": job_id,
                    "routed": True,
                    "source": source_module,
                    "target": target_module,
                    "data": result,
                }
            except Exception as e:
                logger.error(f"Task Failed: Job {job_id} action {action} failed: {e}", exc_info=True)
                return {
                    "job_id": job_id,
                    "routed": False,
                    "source": source_module,
                    "target": target_module,
                    "error": str(e),
                    "data": {"status": "error", "message": str(e)},
                }

        # Use message bus if available for inter-module communication
        if self._message_bus and self._omnicore_components_available["message_bus"]:
            try:
                # Construct topic for target module
                topic = f"{target_module}.job_request"
                
                # Enrich payload with metadata
                enriched_payload = {
                    **payload,
                    "job_id": job_id,
                    "source_module": source_module,
                    "target_module": target_module,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                
                # Publish to message bus with priority
                priority = payload.get("priority", 5)
                success = await self._message_bus.publish(
                    topic=topic,
                    payload=enriched_payload,
                    priority=priority,
                )
                
                if success:
                    logger.info(f"Job {job_id} published to message bus topic: {topic}")
                    
                    # Log to audit if available
                    if self._audit_client and self._omnicore_components_available["audit"]:
                        try:
                            await self._audit_client.add_entry_async(
                                kind="job_routed",
                                name=f"job_{job_id}",
                                detail={
                                    "source": source_module,
                                    "target": target_module,
                                    "topic": topic,
                                    "priority": priority,
                                },
                                sim_id=None,
                                agent_id=None,
                                error=None,
                                context=None,
                                custom_attributes=None,
                                rationale=f"Routing job {job_id} from {source_module} to {target_module}",
                                simulation_outcomes=None,
                                tenant_id=None,
                                explanation_id=None,
                            )
                        except Exception as audit_error:
                            logger.warning(f"Audit logging failed: {audit_error}")
                    
                    return {
                        "job_id": job_id,
                        "routed": True,
                        "source": source_module,
                        "target": target_module,
                        "topic": topic,
                        "message_bus": "ShardedMessageBus",
                        "transport": "message_bus",
                    }
                else:
                    logger.warning(f"Failed to publish job {job_id} to message bus")
                    
            except Exception as e:
                logger.error(f"Message bus routing error: {e}", exc_info=True)
                # Fall through to direct dispatch fallback
        
        # Fallback: Direct dispatch for modules without message bus
        logger.info(f"Using direct dispatch for job {job_id} (message bus not available)")
        return {
            "job_id": job_id,
            "routed": True,
            "source": source_module,
            "target": target_module,
            "transport": "direct_dispatch_fallback",
            "note": "Message bus not available, job queued for direct processing",
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
        # Ensure agents are loaded before use
        self._ensure_agents_loaded()
        
        # Check if agent is available using service's own tracking
        if not self.agents_available.get('codegen', False) or self._codegen_func is None:
            error_msg = "Codegen agent not available"
            logger.error(f"Codegen agent unavailable for job {job_id}: {error_msg}")
            return {
                "status": "error",
                "message": f"Codegen agent not available: {error_msg}",
                "agent_available": False,
                "job_id": job_id,
            }
        
        # Check if LLM provider is configured
        llm_available = False
        llm_provider = None
        
        if self.llm_config:
            provider = self.llm_config.default_llm_provider
            if self.llm_config.is_provider_configured(provider):
                llm_available = True
                llm_provider = provider
            else:
                # Try auto-detection
                auto_provider = detect_available_llm_provider()
                if auto_provider:
                    llm_available = True
                    llm_provider = auto_provider
        else:
            # Check environment variables directly
            auto_provider = detect_available_llm_provider()
            if auto_provider:
                llm_available = True
                llm_provider = auto_provider
        
        if not llm_available:
            logger.error(
                f"No LLM provider configured for code generation job {job_id}. "
                "Please set one of the following environment variables:\n"
                "  - OPENAI_API_KEY for OpenAI\n"
                "  - ANTHROPIC_API_KEY for Anthropic/Claude\n"
                "  - XAI_API_KEY or GROK_API_KEY for xAI/Grok\n"
                "  - GOOGLE_API_KEY for Google/Gemini\n"
                "  - OLLAMA_HOST for Ollama (local)"
            )
            return {
                "status": "error",
                "message": (
                    "No LLM provider configured. Code generation requires an LLM API key. "
                    "Please set OPENAI_API_KEY, ANTHROPIC_API_KEY, XAI_API_KEY, GOOGLE_API_KEY, "
                    "or OLLAMA_HOST environment variable."
                ),
                "error_type": "LLMNotConfigured",
                "configuration_help": {
                    "openai": "Set OPENAI_API_KEY environment variable",
                    "anthropic": "Set ANTHROPIC_API_KEY environment variable",
                    "grok": "Set XAI_API_KEY or GROK_API_KEY environment variable",
                    "google": "Set GOOGLE_API_KEY environment variable",
                    "ollama": "Set OLLAMA_HOST environment variable (e.g., http://localhost:11434)",
                },
            }
        
        logger.info(f"Using LLM provider '{llm_provider}' for job {job_id}")
        
        # Start timing for metrics
        import time
        start_time = time.time()
        
        # Helper function to execute the codegen logic
        async def _execute_codegen(span=None):
            try:
                requirements = payload.get("requirements", "")
                language = payload.get("language", "python")
                framework = payload.get("framework")
                
                # Debug logging - only log metadata, not content to avoid PII exposure
                logger.info(f"[CODEGEN] Processing requirements for job {job_id}: length={len(requirements)} bytes")
                
                # Input validation - industry standard security check
                if not requirements or not isinstance(requirements, str):
                    raise ValueError("Requirements must be a non-empty string")
                if len(requirements) > 100000:  # 100KB limit
                    raise ValueError("Requirements exceed maximum length of 100KB")
                if not language or not isinstance(language, str):
                    raise ValueError("Language must be a non-empty string")
                
                # Build requirements dict
                requirements_dict = {
                    "description": requirements,
                    "target_language": language,
                    "framework": framework,
                }
                
                # Add span attributes for observability
                if span:
                    span.set_attribute("job.id", job_id)
                    span.set_attribute("job.language", language)
                    span.set_attribute("job.framework", framework or "none")
                    span.set_attribute("job.requirements_length", len(requirements))
                
                # Build configuration from our LLM config
                config = self._build_llm_config()
                
                state_summary = f"Generating code for job {job_id}"
                
                logger.info(
                    f"Starting code generation - job_id={job_id}, language={language}, "
                    f"framework={framework or 'none'}, requirements_length={len(requirements)}"
                )
                
                # Call the actual generator
                logger.info(f"Calling codegen agent for job {job_id}")
                result = await self._codegen_func(
                    requirements=requirements_dict,
                    state_summary=state_summary,
                    config_path_or_dict=config,
                )
                
                # Validate result structure - industry standard
                if not isinstance(result, dict):
                    raise TypeError(f"Code generation must return dict, got {type(result).__name__}")
                
                # Create output directory with security validation
                # Prevent path traversal attacks - industry standard security
                base_uploads_dir = Path("./uploads").resolve()
                output_path = (base_uploads_dir / job_id / "generated").resolve()
                
                # Ensure output path is within uploads directory
                if not str(output_path).startswith(str(base_uploads_dir)):
                    raise SecurityError(f"Invalid job_id: path traversal attempt detected")
                
                output_path.mkdir(parents=True, exist_ok=True)
                logger.info(
                    f"Created output directory - job_id={job_id}, path={output_path}",
                    extra={"job_id": job_id, "output_path": str(output_path)}
                )
                
                # Save generated files with comprehensive validation and observability
                generated_files = []
                total_bytes_written = 0
                files_failed = []
                
                if isinstance(result, dict):
                    for filename, content in result.items():
                        try:
                            # Security: Validate filename to prevent path traversal
                            if not filename or '..' in filename or filename.startswith('/'):
                                raise SecurityError(f"Invalid filename: {filename}")
                            
                            # Validate content
                            if not isinstance(content, str):
                                raise TypeError(f"File content must be string, got {type(content).__name__}")
                            if len(content) > 10 * 1024 * 1024:  # 10MB per file limit
                                raise ValueError(f"File {filename} exceeds 10MB size limit")
                            
                            # Resolve and validate file path
                            file_path = (output_path / filename).resolve()
                            if not str(file_path).startswith(str(output_path)):
                                raise SecurityError(f"Path traversal attempt in filename: {filename}")
                            
                            # Create parent directories if filename contains subdirectories
                            file_path.parent.mkdir(parents=True, exist_ok=True)
                            
                            # Write file with explicit encoding
                            file_path.write_text(content, encoding='utf-8')
                            generated_files.append(str(file_path))
                            total_bytes_written += len(content.encode('utf-8'))
                            
                            # Determine file type for metrics
                            file_ext = file_path.suffix.lstrip('.') or 'unknown'
                            
                            # Record metrics
                            if METRICS_AVAILABLE:
                                codegen_files_generated.labels(
                                    job_id=job_id,
                                    language=language
                                ).inc()
                                codegen_file_size_bytes.labels(
                                    job_id=job_id,
                                    file_type=file_ext
                                ).observe(len(content.encode('utf-8')))
                            
                            # Structured logging with context
                            logger.info(
                                f"✓ File written successfully - filename={filename}, size={len(content)} bytes, type={file_ext}",
                                extra={
                                    "job_id": job_id,
                                    "file_name": filename,
                                    "file_size": len(content),
                                    "file_type": file_ext,
                                    "status": "success"
                                }
                            )
                            
                        except SecurityError as sec_error:
                            # Security errors are critical - log and track
                            logger.error(
                                f"Security violation in file write - filename={filename}, error={sec_error}",
                                extra={
                                    "job_id": job_id,
                                    "file_name": filename,
                                    "error_type": "security_violation",
                                    "status": "failed"
                                },
                                exc_info=True
                            )
                            files_failed.append({"filename": filename, "error": "security_violation"})
                            if METRICS_AVAILABLE:
                                codegen_errors_total.labels(
                                    job_id=job_id,
                                    error_type="security_violation"
                                ).inc()
                            # Don't continue on security errors - this is critical
                            raise
                        
                        except TypeError as type_error:
                            # Type errors indicate invalid data structure
                            logger.error(
                                f"Type error in file write - filename={filename}, error={type_error}",
                                extra={
                                    "job_id": job_id,
                                    "file_name": filename,
                                    "error_type": "type_error",
                                    "error_message": str(type_error),
                                    "status": "failed"
                                },
                                exc_info=True
                            )
                            files_failed.append({"filename": filename, "error": "type_error"})
                            if METRICS_AVAILABLE:
                                codegen_errors_total.labels(
                                    job_id=job_id,
                                    error_type="type_error"
                                ).inc()
                            # Continue with other files (graceful degradation)
                            
                        except Exception as write_error:
                            # Log file write failures with full context
                            error_type = type(write_error).__name__
                            logger.error(
                                f"Failed to write file - filename={filename}, error={error_type}: {write_error}",
                                extra={
                                    "job_id": job_id,
                                    "file_name": filename,
                                    "error_type": error_type,
                                    "error_message": str(write_error),
                                    "status": "failed"
                                },
                                exc_info=True
                            )
                            files_failed.append({"filename": filename, "error": error_type})
                            if METRICS_AVAILABLE:
                                codegen_errors_total.labels(
                                    job_id=job_id,
                                    error_type=error_type
                                ).inc()
                            # Continue with other files (graceful degradation)
                else:
                    logger.warning(
                        f"Code generation returned non-dict result - type={type(result).__name__}",
                        extra={
                            "job_id": job_id,
                            "result_type": type(result).__name__,
                            "status": "warning"
                        }
                    )
                
                # Calculate duration and record metrics
                duration = time.time() - start_time
                if METRICS_AVAILABLE:
                    codegen_duration_seconds.labels(
                        job_id=job_id,
                        language=language
                    ).observe(duration)
                    codegen_requests_total.labels(
                        job_id=job_id,
                        language=language,
                        status="success" if not files_failed else "partial_success"
                    ).inc()
                
                # Update tracing span
                if span:
                    span.set_attribute("files.generated", len(generated_files))
                    span.set_attribute("files.failed", len(files_failed))
                    span.set_attribute("bytes.written", total_bytes_written)
                    span.set_attribute("duration.seconds", duration)
                    span.set_status(Status(StatusCode.OK))
                
                # Comprehensive completion log
                logger.info(
                    f"Code generation completed - job_id={job_id}, files_generated={len(generated_files)}, "
                    f"files_failed={len(files_failed)}, total_bytes={total_bytes_written}, "
                    f"duration={duration:.2f}s, output_path={output_path}",
                    extra={
                        "job_id": job_id,
                        "files_generated": len(generated_files),
                        "files_failed": len(files_failed),
                        "total_bytes": total_bytes_written,
                        "duration_seconds": duration,
                        "output_path": str(output_path),
                        "status": "completed"
                    }
                )
                
                result_dict = {
                    "status": "completed",
                    "generated_files": generated_files,
                    "output_path": str(output_path),
                    "files_count": len(generated_files),
                    "total_bytes_written": total_bytes_written,
                    "duration_seconds": round(duration, 2),
                }
                
                # Include failures in response if any
                if files_failed:
                    result_dict["files_failed"] = files_failed
                    result_dict["warning"] = f"{len(files_failed)} file(s) failed to write"
                
                return result_dict
                
            except SecurityError as sec_error:
                # Security errors are critical - comprehensive logging
                duration = time.time() - start_time
                logger.critical(
                    f"Security violation in code generation - job_id={job_id}, error={sec_error}",
                    extra={
                        "job_id": job_id,
                        "error_type": "security_violation",
                        "error_message": str(sec_error),
                        "duration_seconds": duration,
                        "status": "security_error"
                    },
                    exc_info=True
                )
                if METRICS_AVAILABLE:
                    codegen_requests_total.labels(
                        job_id=job_id,
                        language=language,
                        status="security_error"
                    ).inc()
                if span:
                    span.set_status(Status(StatusCode.ERROR, str(sec_error)))
                    span.record_exception(sec_error)
                
                return {
                    "status": "error",
                    "message": "Security violation detected",
                    "error_type": "SecurityError",
                    "error_details": str(sec_error),
                }
                
            except ValueError as val_error:
                # Validation errors - user input issues
                duration = time.time() - start_time
                logger.warning(
                    f"Validation error in code generation - job_id={job_id}, error={val_error}",
                    extra={
                        "job_id": job_id,
                        "error_type": "validation_error",
                        "error_message": str(val_error),
                        "duration_seconds": duration,
                        "status": "validation_error"
                    }
                )
                if METRICS_AVAILABLE:
                    codegen_requests_total.labels(
                        job_id=job_id,
                        language=language if 'language' in locals() else 'unknown',
                        status="validation_error"
                    ).inc()
                if span:
                    span.set_status(Status(StatusCode.ERROR, str(val_error)))
                
                return {
                    "status": "error",
                    "message": str(val_error),
                    "error_type": "ValidationError",
                }
                
            except Exception as e:
                # Unexpected errors - comprehensive logging
                duration = time.time() - start_time
                error_type = type(e).__name__
                logger.error(
                    f"Unexpected error in code generation - job_id={job_id}, error={error_type}: {e}",
                    extra={
                        "job_id": job_id,
                        "error_type": error_type,
                        "error_message": str(e),
                        "duration_seconds": duration,
                        "status": "error"
                    },
                    exc_info=True
                )
                if METRICS_AVAILABLE:
                    codegen_requests_total.labels(
                        job_id=job_id,
                        language=language if 'language' in locals() else 'unknown',
                        status="error"
                    ).inc()
                    codegen_errors_total.labels(
                        job_id=job_id,
                        error_type=error_type
                    ).inc()
                if span:
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    span.record_exception(e)
                
                return {
                    "status": "error",
                    "message": str(e),
                    "error_type": error_type,
                }
        
        # Execute with or without tracing
        if TRACING_AVAILABLE:
            with tracer.start_as_current_span("codegen_execution") as span:
                return await _execute_codegen(span)
        else:
            return await _execute_codegen()
    
    async def _run_testgen(self, job_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Execute test generation agent."""
        logger.info(f"[TESTGEN] Starting test generation for job {job_id}")
        
        # Ensure agents are loaded before use
        self._ensure_agents_loaded()
        
        # Check if agent is available using service's own tracking
        if not self.agents_available.get('testgen', False) or self._testgen_class is None:
            error_msg = "Testgen agent not available"
            logger.error(f"[TESTGEN] Testgen agent unavailable for job {job_id}: {error_msg}")
            return {
                "status": "error",
                "message": f"Testgen agent not available: {error_msg}",
                "agent_available": False,
                "job_id": job_id,
            }
        
        try:
            code_path = payload.get("code_path", f"./uploads/{job_id}/generated")
            test_type = payload.get("test_type", "unit")
            coverage_target = float(payload.get("coverage_target", 80.0))
            
            # Create testgen agent with correct repo path
            repo_path = Path(f"./uploads/{job_id}").resolve()  # Resolve to absolute
            agent = self._testgen_class(str(repo_path))
            
            # Initialize the agent's codebase asynchronously if method exists
            if hasattr(agent, '_async_init'):
                await agent._async_init()
            
            # Set up policy for test generation
            policy = self._testgen_policy_class(
                quality_threshold=coverage_target / 100.0,
                max_refinements=2,
                primary_metric="coverage",
            )
            
            # Find code files to test
            code_files = []
            code_dir = Path(code_path).resolve()  # Resolve to absolute path
            
            logger.info(f"[TESTGEN] Resolved repo_path: {repo_path}")
            logger.info(f"[TESTGEN] Resolved code_dir: {code_dir}")
            
            if code_dir.exists():
                # Convert absolute paths to relative paths from repo_path
                # This prevents path duplication when testgen agent prepends repo_path
                for f in code_dir.rglob("*.py"):
                    if not f.name.startswith("test_"):
                        try:
                            # Get absolute path and convert to relative
                            abs_file_path = f.resolve()
                            rel_path = abs_file_path.relative_to(repo_path)
                            code_files.append(str(rel_path))
                            logger.debug(f"[TESTGEN] Added file: {abs_file_path} -> {rel_path}")
                        except ValueError as e:
                            # File is outside repo_path
                            logger.warning(
                                f"[TESTGEN] File {f} is outside repo_path {repo_path}, skipping. Error: {e}"
                            )
                            continue
            
            if not code_files:
                logger.error(f"[TESTGEN] No code files found in {code_path} for job {job_id}")
                return {
                    "status": "error",
                    "message": f"No code files found in {code_path}",
                }
            
            logger.info(
                f"[TESTGEN] Running testgen agent for job {job_id} with {len(code_files)} code files"
            )
            logger.info(f"[TESTGEN] Code files (relative to repo_path): {code_files}")
            
            # Generate tests
            result = await agent.generate_tests(
                target_files=code_files,
                language="python",
                policy=policy
            )
            
            logger.info(f"[TESTGEN] Test generation completed for job {job_id}")
            return {
                "status": "success",
                "job_id": job_id,
                "result": result,
            }
            
        except Exception as e:
            logger.error(
                f"[TESTGEN] Error running testgen agent for job {job_id}: {str(e)}",
                exc_info=True
            )
            return {
                "status": "error",
                "message": str(e),
            }
    
    async def _run_deploy(self, job_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Execute deployment configuration generation."""
        # Ensure agents are loaded before use
        self._ensure_agents_loaded()
        
        # Check if agent is available using service's own tracking
        if not self.agents_available.get('deploy', False) or self._deploy_class is None:
            error_msg = "Deploy agent not available"
            logger.warning(f"Deploy agent unavailable for job {job_id}: {error_msg}")
            return {
                "status": "error",
                "message": f"Deploy agent not available: {error_msg}",
                "agent_available": False,
                "job_id": job_id,
            }
        
        try:
            code_path = payload.get("code_path", f"./uploads/{job_id}/generated")
            platform = payload.get("platform", "docker")
            include_ci_cd = payload.get("include_ci_cd", False)
            
            repo_path = Path(code_path)
            if not repo_path.exists():
                # Create the directory if it doesn't exist
                repo_path.mkdir(parents=True, exist_ok=True)
                logger.warning(f"Code path {code_path} did not exist, created directory. This may indicate an upstream issue.")
            
            # Initialize deploy agent
            logger.info(f"Initializing deploy agent for job {job_id} with platform: {platform}")
            agent = self._deploy_class(repo_path=str(repo_path))
            
            # Initialize the agent's database
            await agent._init_db()
            
            # Prepare requirements for deployment
            requirements = {
                "pipeline_steps": ["generate", "validate"],
                "platform": platform,
                "include_ci_cd": include_ci_cd,
            }
            
            # Run the deployment generation
            logger.info(f"Running deploy agent for job {job_id}")
            deploy_result = await agent.run_deployment(target=platform, requirements=requirements)
            
            # Extract generated config
            configs = deploy_result.get("configs", {})
            
            if not configs:
                logger.warning(f"Deploy agent returned no configurations for job {job_id}")
                return {
                    "status": "completed",
                    "generated_files": [],
                    "platform": platform,
                    "run_id": deploy_result.get("run_id"),
                    "warning": "No configuration files were generated",
                }
            
            generated_files = []
            
            # Write generated configs to files
            output_dir = repo_path / "deploy"
            output_dir.mkdir(parents=True, exist_ok=True)
            
            for target, config_content in configs.items():
                # Determine filename based on target
                if target == "docker" or target == "dockerfile":
                    filename = "Dockerfile"
                elif target == "kubernetes" or target == "k8s":
                    filename = "deployment.yaml"
                elif target == "docker-compose":
                    filename = "docker-compose.yml"
                elif target == "terraform":
                    filename = "main.tf"
                else:
                    filename = f"{target}.config"
                
                file_path = output_dir / filename
                
                # Write the file
                async with aiofiles.open(file_path, "w", encoding="utf-8") as f:
                    await f.write(config_content)
                
                generated_files.append(str(file_path.relative_to(repo_path)))
                logger.info(f"Generated deployment file: {file_path}")
            
            result = {
                "status": "completed",
                "generated_files": generated_files,
                "platform": platform,
                "run_id": deploy_result.get("run_id"),
                "validations": deploy_result.get("validations", {}),
            }
            
            logger.info(f"Deploy agent completed for job {job_id}, generated {len(generated_files)} files")
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
        # Ensure agents are loaded before use
        self._ensure_agents_loaded()
        
        # Check if agent is available using service's own tracking
        if not self.agents_available.get('docgen', False) or self._docgen_class is None:
            error_msg = "Docgen agent not available"
            logger.warning(f"Docgen agent unavailable for job {job_id}: {error_msg}")
            return {
                "status": "error",
                "message": f"Docgen agent not available: {error_msg}",
                "agent_available": False,
                "job_id": job_id,
            }
        
        try:
            code_path = payload.get("code_path", f"./uploads/{job_id}/generated")
            doc_type = payload.get("doc_type", "api")
            format = payload.get("format", "markdown")
            
            repo_path = Path(code_path)
            if not repo_path.exists():
                logger.warning(f"Code path {code_path} does not exist for job {job_id}")
                return {
                    "status": "error",
                    "message": f"Code path {code_path} does not exist",
                }
            
            logger.info(f"Running docgen agent for job {job_id} with doc_type: {doc_type}, format: {format}")
            
            # Initialize docgen agent
            agent = self._docgen_class(repo_path=str(repo_path))
            
            # Gather target files from code_path
            target_files = []
            for file_path in repo_path.rglob("*.py"):
                if not any(part.startswith('.') for part in file_path.parts):
                    target_files.append(str(file_path.relative_to(repo_path)))
            
            if not target_files:
                logger.warning(f"No Python files found in {code_path} for documentation generation")
                target_files = ["README.md"]  # Fallback to generating a README
            
            # Run documentation generation
            result_data = await agent.generate_documentation(
                target_files=target_files,
                doc_type=doc_type,
                instructions=payload.get("instructions"),
                stream=False,
            )
            
            # Extract generated documentation
            generated_docs = []
            docs_output = result_data.get("documentation", "")
            
            # Write documentation to file
            output_dir = repo_path / "docs"
            output_dir.mkdir(parents=True, exist_ok=True)
            
            # Determine filename based on doc_type
            if doc_type.lower() in ["api", "api_reference"]:
                doc_filename = "API.md"
            elif doc_type.lower() in ["readme", "user"]:
                doc_filename = "README.md"
            elif doc_type.lower() in ["developer", "dev"]:
                doc_filename = "DEVELOPER.md"
            else:
                doc_filename = f"{doc_type}.md"
            
            doc_path = output_dir / doc_filename
            async with aiofiles.open(doc_path, "w", encoding="utf-8") as f:
                await f.write(docs_output)
            
            generated_docs.append(str(doc_path.relative_to(repo_path)))
            logger.info(f"Generated documentation file: {doc_path}")
            
            result = {
                "status": "completed",
                "generated_docs": generated_docs,
                "doc_type": doc_type,
                "format": format,
                "file_count": len(target_files),
            }
            
            logger.info(f"Docgen agent completed for job {job_id}, generated {len(generated_docs)} files")
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
        # Ensure agents are loaded before use
        self._ensure_agents_loaded()
        
        # Check if agent is available using service's own tracking
        if not self.agents_available.get('critique', False) or self._critique_class is None:
            error_msg = "Critique agent not available"
            logger.warning(f"Critique agent unavailable for job {job_id}: {error_msg}")
            return {
                "status": "error",
                "message": f"Critique agent not available: {error_msg}",
                "agent_available": False,
                "job_id": job_id,
            }
        
        try:
            code_path = payload.get("code_path", f"./uploads/{job_id}/generated")
            scan_types = payload.get("scan_types", ["security", "quality"])
            auto_fix = payload.get("auto_fix", False)
            
            repo_path = Path(code_path)
            if not repo_path.exists():
                logger.warning(f"Code path {code_path} does not exist for job {job_id}")
                return {
                    "status": "error",
                    "message": f"Code path {code_path} does not exist",
                }
            
            logger.info(f"Running critique agent for job {job_id} with scan_types: {scan_types}, auto_fix: {auto_fix}")
            
            # Initialize critique agent
            agent = self._critique_class(repo_path=str(repo_path))
            
            # Gather code files from code_path
            code_files = {}
            for file_path in repo_path.rglob("*.py"):
                if not any(part.startswith('.') for part in file_path.parts):
                    rel_path = str(file_path.relative_to(repo_path))
                    try:
                        code_files[rel_path] = file_path.read_text(encoding="utf-8")
                    except Exception as e:
                        logger.warning(f"Failed to read file {file_path}: {e}")
            
            if not code_files:
                logger.warning(f"No Python files found in {code_path} for critique")
                return {
                    "status": "completed",
                    "issues_found": 0,
                    "issues_fixed": 0,
                    "scan_types": scan_types,
                    "warning": "No code files found to critique",
                }
            
            # Run critique
            critique_result = await agent.run(
                code_files=code_files,
                test_files={},
                requirements={"scan_types": scan_types, "auto_fix": auto_fix},
            )
            
            # Extract results
            issues_found = len(critique_result.get("issues", []))
            issues_fixed = len(critique_result.get("fixes_applied", []))
            
            # Write critique report
            output_dir = repo_path / "reports"
            output_dir.mkdir(parents=True, exist_ok=True)
            
            report_path = output_dir / "critique_report.json"
            import json
            async with aiofiles.open(report_path, "w", encoding="utf-8") as f:
                await f.write(json.dumps(critique_result, indent=2))
            
            logger.info(f"Generated critique report: {report_path}")
            
            result = {
                "status": "completed",
                "issues_found": issues_found,
                "issues_fixed": issues_fixed,
                "scan_types": scan_types,
                "report_path": str(report_path.relative_to(repo_path)),
                "file_count": len(code_files),
            }
            
            logger.info(f"Critique agent completed for job {job_id}, found {issues_found} issues")
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
        
        Uses the Clarifier class which auto-detects available LLM providers
        (OpenAI, Anthropic, xAI, Google, Ollama) via the central runner/llm_client.py.
        Falls back to rule-based clarification if no LLM is available.
        
        Args:
            job_id: Job identifier
            payload: Parameters including readme_content, ambiguities
        
        Returns:
            Dict with status and clarification questions
        """
        # Ensure agents are loaded before use
        self._ensure_agents_loaded()
        
        try:
            readme_content = payload.get("readme_content", "")
            
            if not readme_content:
                return {
                    "status": "error",
                    "message": "No README content provided for clarification",
                }
            
            # Try LLM-based clarification first (with auto-detection)
            if self.agents_available.get("clarifier"):
                logger.info(f"Running LLM-based clarifier for job {job_id}")
                try:
                    from generator.clarifier.clarifier import Clarifier
                    
                    # Create clarifier instance with auto-detection
                    clarifier = await Clarifier.create()
                    
                    # Check if LLM is actually available (not just rule-based fallback)
                    has_llm = hasattr(clarifier, 'llm') and clarifier.llm is not None
                    
                    if has_llm:
                        # Try to detect ambiguities using LLM
                        try:
                            detected_ambiguities = await clarifier.detect_ambiguities(readme_content)
                            # Generate questions based on detected ambiguities
                            questions = await clarifier.generate_questions(detected_ambiguities)
                            
                            logger.info(
                                f"LLM-based clarifier generated {len(questions)} questions for job {job_id}",
                                extra={"method": "llm", "questions_count": len(questions)}
                            )
                            
                            # Store session
                            _clarification_sessions[job_id] = {
                                "job_id": job_id,
                                "requirements": readme_content,
                                "questions": questions,
                                "answers": {},
                                "status": "in_progress",
                                "created_at": datetime.now().isoformat(),
                                "method": "llm",
                            }
                            
                            return {
                                "status": "clarification_initiated",
                                "job_id": job_id,
                                "clarifications": questions,
                                "confidence": 0.65,
                                "questions_count": len(questions),
                                "method": "llm",
                            }
                        except Exception as llm_error:
                            logger.warning(
                                f"LLM-based clarification failed: {llm_error}. "
                                "Falling back to rule-based.",
                                exc_info=True
                            )
                    else:
                        logger.info("No LLM configured, using rule-based clarification")
                    
                except ImportError as e:
                    logger.warning(f"Could not import Clarifier module: {e}. Using rule-based.")
                except Exception as e:
                    logger.warning(
                        f"Error initializing clarifier: {e}. Falling back to rule-based.",
                        exc_info=True
                    )
            
            # Fallback to rule-based clarification
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
                "method": "rule_based",
            }
            
            result = {
                "status": "clarification_initiated",
                "job_id": job_id,
                "clarifications": questions,
                "confidence": 0.65,  # Low confidence indicates need for clarification
                "questions_count": len(questions),
                "method": "rule_based",
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
        logger.info(f"[PIPELINE] Starting pipeline for job {job_id}")
        
        # Ensure agents are loaded before use
        self._ensure_agents_loaded()
        
        try:
            # Run pipeline stages sequentially
            stages_completed = []
            
            # 1. Clarify (optional)
            if payload.get("readme_content"):
                logger.info(f"[PIPELINE] Job {job_id} starting step: clarify")
                clarify_result = await self._run_clarifier(job_id, payload)
                if clarify_result.get("status") != "error":
                    stages_completed.append("clarify")
                    logger.info(f"[PIPELINE] Job {job_id} completed step: clarify")
            
            # 2. Codegen
            # Transform payload for codegen - it needs 'requirements' not 'readme_content'
            # Preserve all original payload fields that might be needed
            codegen_payload = {
                **payload,  # Preserve all original fields
                "requirements": payload.get("readme_content", payload.get("requirements", "")),
            }
            # Remove readme_content from codegen payload as it's now in requirements
            codegen_payload.pop("readme_content", None)
            
            logger.info(f"[PIPELINE] Job {job_id} starting step: codegen")
            codegen_result = await self._run_codegen(job_id, codegen_payload)
            if codegen_result.get("status") == "completed":
                stages_completed.append("codegen")
                logger.info(f"[PIPELINE] Job {job_id} completed step: codegen")
            else:
                logger.error(f"[PIPELINE] Job {job_id} failed step: codegen - {codegen_result.get('message', 'Unknown error')}")
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
                logger.info(f"[PIPELINE] Job {job_id} starting step: testgen")
                testgen_result = await self._run_testgen(job_id, testgen_payload)
                if testgen_result.get("status") == "completed":
                    stages_completed.append("testgen")
                    logger.info(f"[PIPELINE] Job {job_id} completed step: testgen")
                elif testgen_result.get("status") == "error":
                    logger.error(f"[PIPELINE] Job {job_id} failed step: testgen - {testgen_result.get('message', 'Unknown error')}")
                    return {
                        "status": "failed",
                        "message": f"Test generation failed: {testgen_result.get('message', 'Unknown error')}",
                        "stages_completed": stages_completed,
                    }
            
            # 4. Deploy (if requested)
            if payload.get("include_deployment", False):
                deploy_payload = {
                    "code_path": codegen_result.get("output_path"),
                    "platform": "docker",
                    "include_ci_cd": True,
                }
                logger.info(f"[PIPELINE] Job {job_id} starting step: deploy")
                deploy_result = await self._run_deploy(job_id, deploy_payload)
                if deploy_result.get("status") == "completed":
                    stages_completed.append("deploy")
                    logger.info(f"[PIPELINE] Job {job_id} completed step: deploy")
                elif deploy_result.get("status") == "error":
                    logger.warning(f"[PIPELINE] Job {job_id} failed step: deploy - {deploy_result.get('message', 'Unknown error')} (continuing pipeline)")
            
            # 5. Docgen (if requested)
            if payload.get("include_docs", False):
                docgen_payload = {
                    "code_path": codegen_result.get("output_path"),
                    "doc_type": "api",
                    "format": "markdown",
                }
                logger.info(f"[PIPELINE] Job {job_id} starting step: docgen")
                docgen_result = await self._run_docgen(job_id, docgen_payload)
                if docgen_result.get("status") == "completed":
                    stages_completed.append("docgen")
                    logger.info(f"[PIPELINE] Job {job_id} completed step: docgen")
                elif docgen_result.get("status") == "error":
                    logger.warning(f"[PIPELINE] Job {job_id} failed step: docgen - {docgen_result.get('message', 'Unknown error')} (continuing pipeline)")
            
            # 6. Critique (if requested)
            if payload.get("run_critique", False):
                critique_payload = {
                    "code_path": codegen_result.get("output_path"),
                    "scan_types": ["security", "quality"],
                    "auto_fix": False,
                }
                logger.info(f"[PIPELINE] Job {job_id} starting step: critique")
                critique_result = await self._run_critique(job_id, critique_payload)
                if critique_result.get("status") == "completed":
                    stages_completed.append("critique")
                    logger.info(f"[PIPELINE] Job {job_id} completed step: critique")
                elif critique_result.get("status") == "error":
                    logger.warning(f"[PIPELINE] Job {job_id} failed step: critique - {critique_result.get('message', 'Unknown error')} (continuing pipeline)")
            
            logger.info(f"[PIPELINE] Pipeline completed successfully for job {job_id}")
            return {
                "status": "completed",
                "stages_completed": stages_completed,
                "output_path": codegen_result.get("output_path"),
            }
            
        except Exception as e:
            logger.error(f"[PIPELINE] Job {job_id} FAILED with exception: {str(e)}", exc_info=True)
            return {
                "status": "failed",
                "message": str(e),
                "error_type": type(e).__name__,
            }
    
    async def _configure_llm(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Configure LLM provider."""
        try:
            provider = payload.get("provider", "openai")
            api_key = payload.get("api_key")
            model = payload.get("model")
            
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
            Plugin registry status including active plugins and their metadata

        Example integration:
            >>> # from omnicore_engine import get_plugin_registry
            >>> # registry = get_plugin_registry()
            >>> # plugins = registry.list_plugins()
        """
        logger.debug("Fetching plugin status")

        # Use actual plugin registry if available
        if self._plugin_registry and self._omnicore_components_available["plugin_registry"]:
            try:
                # Get all plugins from registry
                all_plugins = []
                plugin_details = []
                
                # Iterate through plugin kinds
                for kind, plugins_by_name in self._plugin_registry._plugins.items():
                    for name, plugin in plugins_by_name.items():
                        all_plugins.append(name)
                        plugin_details.append({
                            "name": name,
                            "kind": kind,
                            "version": getattr(plugin.meta, "version", "unknown") if hasattr(plugin, "meta") else "unknown",
                            "safe": getattr(plugin.meta, "safe", False) if hasattr(plugin, "meta") else False,
                        })
                
                logger.info(f"Retrieved {len(all_plugins)} plugins from registry")
                
                return {
                    "total_plugins": len(all_plugins),
                    "active_plugins": all_plugins[:10],  # Show first 10
                    "plugin_details": plugin_details,
                    "plugin_registry": "omnicore_engine.plugin_registry.PLUGIN_REGISTRY",
                    "source": "actual",
                }
            except Exception as e:
                logger.error(f"Error querying plugin registry: {e}", exc_info=True)
                # Fall through to fallback

        # Fallback: Return mock data
        logger.debug("Using fallback plugin status (registry not available)")
        return {
            "total_plugins": 3,
            "active_plugins": ["scenario_plugin", "audit_plugin", "metrics_plugin"],
            "plugin_registry": "omnicore_engine.plugin_registry",
            "source": "fallback",
        }

    async def get_job_metrics(self, job_id: str) -> Dict[str, Any]:
        """
        Get metrics for a specific job.

        Args:
            job_id: Unique job identifier

        Returns:
            Job metrics including processing time, resource usage

        Example integration:
            >>> # from omnicore_engine.metrics import get_job_metrics
            >>> # metrics = await get_job_metrics(job_id)
        """
        logger.debug(f"Fetching metrics for job {job_id}")

        # Use actual metrics client if available
        if self._metrics_client and self._omnicore_components_available["metrics"]:
            try:
                # Try to get actual metrics from Prometheus/InfluxDB
                metrics_data = {
                    "job_id": job_id,
                    "source": "actual",
                }
                
                # Try to get message bus metrics
                try:
                    if hasattr(self._metrics_client, "MESSAGE_BUS_DISPATCH_DURATION"):
                        dispatch_metric = self._metrics_client.MESSAGE_BUS_DISPATCH_DURATION
                        if hasattr(dispatch_metric, "_samples"):
                            # Get recent samples
                            metrics_data["dispatch_latency_samples"] = len(dispatch_metric._samples())
                except Exception:
                    pass
                
                # Try to get API metrics
                try:
                    if hasattr(self._metrics_client, "API_REQUESTS_TOTAL"):
                        requests_metric = self._metrics_client.API_REQUESTS_TOTAL
                        if hasattr(requests_metric, "_value"):
                            metrics_data["api_requests"] = requests_metric._value.get()
                except Exception:
                    pass
                
                logger.info(f"Retrieved actual metrics for job {job_id}")
                return metrics_data
                
            except Exception as e:
                logger.error(f"Error querying metrics: {e}", exc_info=True)
                # Fall through to fallback

        # Fallback: Return mock metrics
        logger.debug(f"Using fallback metrics for job {job_id} (metrics client not available)")
        return {
            "job_id": job_id,
            "processing_time": 125.5,
            "cpu_usage": 45.2,
            "memory_usage": 512.3,
            "metrics_module": "omnicore_engine.metrics",
            "source": "fallback",
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
            List of audit entries with timestamps and actions

        Example integration:
            >>> # from omnicore_engine.audit import get_audit_trail
            >>> # trail = await get_audit_trail(job_id, limit)
        """
        logger.debug(f"Fetching audit trail for job {job_id}")

        # Use actual audit client if available
        if self._audit_client and self._omnicore_components_available["audit"]:
            try:
                # Try to get audit entries from the database
                if hasattr(self._audit_client, "db") and self._audit_client.db:
                    # Query the audit records table
                    try:
                        from sqlalchemy import select, desc
                        from omnicore_engine.database import AuditRecord
                        
                        async with self._audit_client.db.async_session() as session:
                            # Query for audit records matching the job_id
                            stmt = (
                                select(AuditRecord)
                                .where(AuditRecord.name.like(f"%{job_id}%"))
                                .order_by(desc(AuditRecord.timestamp))
                                .limit(limit)
                            )
                            result = await session.execute(stmt)
                            records = result.scalars().all()
                            
                            audit_entries = []
                            for record in records:
                                audit_entries.append({
                                    "timestamp": record.timestamp.isoformat() if hasattr(record.timestamp, "isoformat") else str(record.timestamp),
                                    "action": record.kind,
                                    "name": record.name,
                                    "job_id": job_id,
                                    "module": "omnicore_engine.audit",
                                    "detail": record.detail if hasattr(record, "detail") else {},
                                })
                            
                            logger.info(f"Retrieved {len(audit_entries)} audit entries for job {job_id}")
                            
                            if audit_entries:
                                return audit_entries
                            
                    except ImportError as import_err:
                        logger.debug(f"Could not import audit database models: {import_err}")
                    except Exception as db_err:
                        logger.warning(f"Database query failed: {db_err}")
                
                # If no database entries found or database unavailable, check in-memory buffer
                if hasattr(self._audit_client, "buffer") and self._audit_client.buffer:
                    matching_entries = []
                    for entry in self._audit_client.buffer:
                        if isinstance(entry, dict) and job_id in entry.get("name", ""):
                            matching_entries.append({
                                "timestamp": entry.get("timestamp", datetime.now(timezone.utc).isoformat()),
                                "action": entry.get("kind", "unknown"),
                                "name": entry.get("name", ""),
                                "job_id": job_id,
                                "module": "omnicore_engine.audit",
                                "detail": entry.get("detail", {}),
                            })
                    
                    if matching_entries:
                        logger.info(f"Retrieved {len(matching_entries)} buffered audit entries for job {job_id}")
                        return matching_entries[:limit]
                
            except Exception as e:
                logger.error(f"Error querying audit trail: {e}", exc_info=True)
                # Fall through to fallback

        # Fallback: Return mock audit entry
        logger.debug(f"Using fallback audit trail for job {job_id} (audit client not available)")
        return [
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "action": "job_created",
                "job_id": job_id,
                "module": "omnicore_engine",
                "source": "fallback",
            }
        ]

    async def get_system_health(self) -> Dict[str, Any]:
        """
        Get overall system health from OmniCore perspective.

        Returns:
            System health status with component availability

        Example integration:
            >>> # from omnicore_engine.core import get_system_health
            >>> # health = await get_system_health()
        """
        logger.debug("Fetching system health")

        # Build health status from actual component checks
        health_status = {
            "status": "healthy",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "components": {},
        }
        
        # Check message bus health
        if self._message_bus and self._omnicore_components_available["message_bus"]:
            try:
                # Check if message bus is operational
                queue_sizes = []
                for queue in self._message_bus.queues:
                    queue_sizes.append(queue.qsize())
                
                health_status["components"]["message_bus"] = {
                    "status": "operational",
                    "shards": len(self._message_bus.queues),
                    "total_queued": sum(queue_sizes),
                }
            except Exception as e:
                health_status["components"]["message_bus"] = {
                    "status": "degraded",
                    "error": str(e),
                }
                health_status["status"] = "degraded"
        else:
            health_status["components"]["message_bus"] = {
                "status": "unavailable",
            }
        
        # Check plugin registry health
        if self._plugin_registry and self._omnicore_components_available["plugin_registry"]:
            try:
                plugin_count = sum(len(plugins) for plugins in self._plugin_registry._plugins.values())
                health_status["components"]["plugin_registry"] = {
                    "status": "operational",
                    "total_plugins": plugin_count,
                }
            except Exception as e:
                health_status["components"]["plugin_registry"] = {
                    "status": "degraded",
                    "error": str(e),
                }
                health_status["status"] = "degraded"
        else:
            health_status["components"]["plugin_registry"] = {
                "status": "unavailable",
            }
        
        # Check metrics health
        if self._metrics_client and self._omnicore_components_available["metrics"]:
            health_status["components"]["metrics"] = {
                "status": "operational",
            }
        else:
            health_status["components"]["metrics"] = {
                "status": "unavailable",
            }
        
        # Check audit health
        if self._audit_client and self._omnicore_components_available["audit"]:
            try:
                buffer_size = len(self._audit_client.buffer) if hasattr(self._audit_client, "buffer") else 0
                health_status["components"]["audit"] = {
                    "status": "operational",
                    "buffer_size": buffer_size,
                }
            except Exception as e:
                health_status["components"]["audit"] = {
                    "status": "degraded",
                    "error": str(e),
                }
                health_status["status"] = "degraded"
        else:
            health_status["components"]["audit"] = {
                "status": "unavailable",
            }
        
        # Overall status determination
        component_statuses = [c["status"] for c in health_status["components"].values()]
        if all(status == "operational" for status in component_statuses):
            health_status["status"] = "healthy"
        elif any(status == "operational" for status in component_statuses):
            health_status["status"] = "degraded"
        else:
            health_status["status"] = "critical"
        
        return health_status

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
            Publication result with message_id and status
        """
        logger.info(f"Publishing message to topic {topic}")

        # Use actual message bus if available
        if self._message_bus and self._omnicore_components_available["message_bus"]:
            try:
                # Publish to message bus
                success = await self._message_bus.publish(
                    topic=topic,
                    payload=payload,
                    priority=priority,
                )
                
                if success:
                    logger.info(f"Message published successfully to topic: {topic}")
                    
                    # Generate message ID based on topic and timestamp
                    import time
                    message_id = f"msg_{topic}_{int(time.time() * 1000)}"
                    
                    return {
                        "status": "published",
                        "topic": topic,
                        "message_id": message_id,
                        "priority": priority,
                        "transport": "message_bus",
                    }
                else:
                    logger.warning(f"Failed to publish message to topic: {topic}")
                    return {
                        "status": "failed",
                        "topic": topic,
                        "error": "Message bus publish returned False",
                        "transport": "message_bus",
                    }
                    
            except Exception as e:
                logger.error(f"Error publishing to message bus: {e}", exc_info=True)
                # Fall through to fallback

        # Fallback: Return mock publication result
        logger.debug(f"Using fallback for message publication to topic: {topic}")
        return {
            "status": "published",
            "topic": topic,
            "message_id": f"msg_{topic}_{hash(str(payload)) % 10000}",
            "priority": priority,
            "transport": "fallback",
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


def get_omnicore_service() -> OmniCoreService:
    """
    Dependency injection function for OmniCoreService.
    
    Creates an OmniCoreService instance for centralized routing
    and coordination of all module operations.
    
    Returns:
        OmniCoreService: Configured OmniCore service instance
        
    Example:
        >>> from fastapi import Depends
        >>> @router.post("/endpoint")
        >>> async def handler(service: OmniCoreService = Depends(get_omnicore_service)):
        ...     result = await service.route_job(...)
    """
    return OmniCoreService()
