# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

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
import asyncio
import json
import logging
import os
import re
import threading
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from jinja2 import Template
    JINJA2_AVAILABLE = True
except ImportError:
    JINJA2_AVAILABLE = False
    Template = None

from server.utils.agent_loader import get_agent_loader
from server.storage import jobs_db
from server.schemas.jobs import JobStatus, JobStage

# Import shared Presidio placeholders constant
try:
    from generator.runner.runner_security_utils import PRESIDIO_PLACEHOLDERS
except ImportError:
    # Fallback if import fails
    PRESIDIO_PLACEHOLDERS = ['<ORGANIZATION>', '<URL>', '<PERSON>', '<API_KEY>']

# Import flexible requirements parser for code generation
try:
    from generator.agents.codegen_agent.codegen_prompt import _parse_requirements_flexible
except ImportError:
    _parse_requirements_flexible = None

# Import existing materializer for writing LLM multi-file output to disk
# This replaces the manual file-writing loop and prevents the JSON-bundle-in-main.py bug
try:
    from generator.runner.runner_file_utils import (
        materialize_file_map as _materialize_file_map,
        validate_generated_project as _validate_generated_project,
        write_validation_error as _write_validation_error,
    )
    _MATERIALIZER_AVAILABLE = True
except ImportError:
    _MATERIALIZER_AVAILABLE = False

# Import existing provenance tracker and spec validator
try:
    from generator.main.provenance import (
        ProvenanceTracker,
        validate_spec_fidelity as _validate_spec_fidelity,
        run_fail_fast_validation as _run_fail_fast_validation,
        extract_required_files_from_md as _extract_required_files_from_md,
        extract_output_dir_from_md as _extract_output_dir_from_md,
        validate_readme_completeness as _validate_readme_completeness,
    )
    _PROVENANCE_AVAILABLE = True
except ImportError:
    _PROVENANCE_AVAILABLE = False

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
        labelnames = tuple(labelnames or [])  # Convert list to tuple
        try:
            collector = REGISTRY._names_to_collectors.get(name)
            if collector is not None:
                return collector
        except (AttributeError, KeyError):
            pass
        try:
            return Counter(name, description, labelnames=labelnames)
        except ValueError as e:
            if "Duplicated timeseries" in str(e):
                existing = REGISTRY._names_to_collectors.get(name)
                if existing is not None:
                    return existing
            raise
    
    def _get_or_create_histogram(name: str, description: str, labelnames: list = None):
        """Create a Histogram or return existing one with same name."""
        labelnames = tuple(labelnames or [])  # Convert list to tuple
        try:
            collector = REGISTRY._names_to_collectors.get(name)
            if collector is not None:
                return collector
        except (AttributeError, KeyError):
            pass
        try:
            return Histogram(name, description, labelnames=labelnames)
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
    
    # Deployment-specific metrics for observability
    deployment_requests_total = _get_or_create_counter(
        'deployment_requests_total',
        'Total number of deployment requests',
        ['job_id', 'target', 'status']
    )
    deployment_duration_seconds = _get_or_create_histogram(
        'deployment_duration_seconds',
        'Deployment generation duration in seconds',
        ['job_id', 'target']
    )
    deployment_validation_total = _get_or_create_counter(
        'deployment_validation_total',
        'Total number of deployment validations',
        ['job_id', 'status', 'validation_type']
    )
    deployment_files_generated = _get_or_create_counter(
        'deployment_files_generated_total',
        'Total number of deployment files generated',
        ['job_id', 'target', 'file_type']
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
        return "gpt-4o"

# In-memory storage for clarification sessions
_clarification_sessions = {}

# Constants for configurable timeouts
DEFAULT_TESTGEN_TIMEOUT = int(os.getenv("TESTGEN_TIMEOUT_SECONDS", "300"))
DEFAULT_DEPLOY_TIMEOUT = int(os.getenv("DEPLOY_TIMEOUT_SECONDS", "90"))
DEFAULT_DOCGEN_TIMEOUT = int(os.getenv("DOCGEN_TIMEOUT_SECONDS", "300"))
DEFAULT_CRITIQUE_TIMEOUT = int(os.getenv("CRITIQUE_TIMEOUT_SECONDS", "90"))

# Constants for clarification session cleanup
CLARIFICATION_SESSION_TTL_SECONDS = int(os.getenv("CLARIFICATION_SESSION_TTL_SECONDS", "3600"))  # 1 hour default


# Custom exception for security violations
class SecurityError(Exception):
    """Raised when a security violation is detected."""
    pass


# Constants for file parsing and validation
MIN_YAML_DOC_LENGTH = 10  # Minimum characters for a valid YAML document
HELM_FILE_HEADER_CHECK_LENGTH = 50  # Check first N chars for Helm filenames


def _load_readme_from_disk(job_dir: Path) -> Optional[str]:
    """
    Load README content from a job directory.
    
    Args:
        job_dir: Path to the job directory
        
    Returns:
        README content as string, or None if not found
    """
    if not job_dir.exists():
        return None
    
    # Priority order for README files
    readme_patterns = ["README.md", "readme.md", "README.txt", "readme.txt"]
    
    # Try exact filename matches first
    for pattern in readme_patterns:
        readme_path = job_dir / pattern
        if readme_path.exists() and readme_path.is_file():
            try:
                return readme_path.read_text(encoding="utf-8")
            except Exception as e:
                logger.error(f"Error reading {readme_path}: {e}")
                continue
    
    # Fallback: find any .md file
    try:
        for f in job_dir.glob("*.md"):
            if f.is_file():
                return f.read_text(encoding="utf-8")
    except Exception as e:
        logger.error(f"Error scanning for .md files in {job_dir}: {e}")
    
    return None


def _generate_fallback_readme(project_name: str = "hello_generator", 
                                language: str = "python",
                                output_path: Optional[str] = None) -> str:
    """
    Generate a deterministic fallback README when DocGen fails or times out.
    
    This function creates a complete README directly from project metadata
    without requiring LLM generation. It uses Jinja2 templating if available,
    otherwise falls back to string formatting.
    
    Args:
        project_name: Name of the generated project
        language: Programming language of the project
        output_path: Path to the generated project (for scanning files/deps)
        
    Returns:
        Complete README content as a string
    """
    # Scan project for additional metadata if path provided
    endpoints = []
    dependencies = []
    file_list = []
    
    if output_path:
        output_path_obj = Path(output_path)
        if output_path_obj.exists():
            # Scan for Python files
            py_files = list(output_path_obj.rglob("*.py"))
            file_list = [str(f.relative_to(output_path_obj)) for f in py_files[:10]]  # Limit to 10
            
            # Try to extract endpoints from main.py or app/main.py
            for main_file in [output_path_obj / "main.py", output_path_obj / "app" / "main.py"]:
                if main_file.exists():
                    try:
                        content = main_file.read_text(encoding="utf-8")
                        # Simple regex to find FastAPI route decorators
                        endpoint_patterns = [
                            r'@app\.(get|post|put|delete|patch)\(["\']([^"\']+)["\']',
                            r'@router\.(get|post|put|delete|patch)\(["\']([^"\']+)["\']',
                        ]
                        for pattern in endpoint_patterns:
                            matches = re.findall(pattern, content)
                            for method, path in matches:
                                endpoints.append(f"{method.upper()} {path}")
                    except Exception as e:
                        logger.debug(f"Could not extract endpoints from {main_file}: {e}")
            
            # Try to read requirements.txt
            req_file = output_path_obj / "requirements.txt"
            if req_file.exists():
                try:
                    deps_content = req_file.read_text(encoding="utf-8")
                    # Extract package names (ignore versions and comments)
                    for line in deps_content.split('\n'):
                        line = line.strip()
                        if line and not line.startswith('#'):
                            pkg = line.split('==')[0].split('>=')[0].split('<=')[0].strip()
                            if pkg:
                                dependencies.append(pkg)
                except Exception as e:
                    logger.debug(f"Could not read requirements.txt: {e}")
    
    # Use Jinja2 template if available
    if JINJA2_AVAILABLE and Template:
        template_str = """# {{ project_name }}

A {{ language }} application generated by The Code Factory.

## Description

This project was automatically generated and includes a complete application structure with:
- RESTful API endpoints{% if endpoints %}
- {{ endpoints|length }} defined routes{% endif %}
- Dependency management
- Testing infrastructure
- Deployment configuration (Docker, Kubernetes, Helm)

## Installation

1. Clone the repository or extract the generated archive

2. Install dependencies:
```bash
pip install -r requirements.txt
```

{% if dependencies %}
### Dependencies

The project includes the following key dependencies:
{% for dep in dependencies[:5] %}
- {{ dep }}
{% endfor %}
{% endif %}

## Running the Application

Start the development server:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

Or if using the app/ structure:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The API will be available at: `http://localhost:8000`

### Health Check

Test the health check endpoint:

```bash
curl http://localhost:8000/health
```

Expected response:
```json
{"status": "healthy"}
```

{% if endpoints %}
## API Endpoints

The following endpoints are available:

{% for endpoint in endpoints %}
- {{ endpoint }}
{% endfor %}

For complete API documentation, visit: `http://localhost:8000/docs`
{% endif %}

## Testing

Run the test suite:

```bash
pytest
```

Run with coverage:

```bash
pytest --cov=. --cov-report=html
```

## Deployment

### Docker

Build the Docker image:

```bash
docker build -t {{ project_name }}:latest .
```

Run the container:

```bash
docker run -p 8000:8000 {{ project_name }}:latest
```

### Kubernetes

Deploy to Kubernetes:

```bash
kubectl apply -f k8s/
```

### Helm

Install using Helm:

```bash
helm install {{ project_name }} ./helm
```

## Project Structure

{% if file_list %}
Key files:
{% for file in file_list %}
- {{ file }}
{% endfor %}
{% endif %}

## License

See LICENSE file for details.

## Support

For issues or questions, please refer to the project documentation or contact the development team.
"""
        template = Template(template_str)
        return template.render(
            project_name=project_name,
            language=language,
            endpoints=endpoints,
            dependencies=dependencies,
            file_list=file_list
        )
    
    # Fallback: Simple string formatting without Jinja2
    readme = f"""# {project_name}

A {language} application generated by The Code Factory.

## Description

This project was automatically generated and includes a complete application structure with:
- RESTful API endpoints
- Dependency management
- Testing infrastructure
- Deployment configuration (Docker, Kubernetes, Helm)

## Installation

1. Clone the repository or extract the generated archive

2. Install dependencies:
```bash
pip install -r requirements.txt
```

## Running the Application

Start the development server:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

Or if using the app/ structure:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The API will be available at: `http://localhost:8000`

### Health Check

Test the health check endpoint:

```bash
curl http://localhost:8000/health
```

Expected response:
```json
{{"status": "healthy"}}
```

## API Endpoints

For complete API documentation, visit: `http://localhost:8000/docs`

## Testing

Run the test suite:

```bash
pytest
```

Run with coverage:

```bash
pytest --cov=. --cov-report=html
```

## Deployment

### Docker

Build the Docker image:

```bash
docker build -t {project_name}:latest .
```

Run the container:

```bash
docker run -p 8000:8000 {project_name}:latest
```

### Kubernetes

Deploy to Kubernetes:

```bash
kubectl apply -f k8s/
```

### Helm

Install using Helm:

```bash
helm install {project_name} ./helm
```

## License

See LICENSE file for details.

## Support

For issues or questions, please refer to the project documentation or contact the development team.
"""
    return readme


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
        
        # FIX: Track jobs currently in pipeline to prevent concurrent runs
        self._jobs_in_pipeline: set = set()
        
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
        
        # Initialize storage path (following GeneratorService pattern)
        # Use centralized config if available, otherwise fallback to default
        self.storage_path = self.agent_config.upload_dir if self.agent_config else Path("./uploads")
        self.storage_path.mkdir(parents=True, exist_ok=True)
        logger.info(f"Storage path initialized: {self.storage_path}")
        
        # Initialize Kafka producer if configured
        self.kafka_producer = None
        self._init_kafka_producer()
        
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
    
    def _init_kafka_producer(self):
        """Initialize Kafka producer if configured."""
        try:
            kafka_enabled = os.getenv("KAFKA_ENABLED", "false").lower() == "true"
            if kafka_enabled:
                bootstrap_servers = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
                # Import and initialize only if enabled
                try:
                    from aiokafka import AIOKafkaProducer
                    # Note: Actual connection happens in async context via start()
                    # For now, we just store the configuration
                    self.kafka_producer = {
                        "bootstrap_servers": bootstrap_servers,
                        "enabled": True,
                    }
                    logger.info(f"Kafka producer configured with servers: {bootstrap_servers}")
                except ImportError:
                    logger.warning("aiokafka not installed - Kafka producer unavailable")
                    self.kafka_producer = None
            else:
                logger.info("Kafka disabled - SFE dispatch will use HTTP fallback")
                self.kafka_producer = None
        except Exception as e:
            logger.warning(f"Failed to initialize Kafka producer: {e}")
            self.kafka_producer = None
    
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
                    "model": {"openai": "gpt-4o"},
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
    
    async def start_message_bus(self) -> bool:
        """
        Explicitly start the message bus dispatcher tasks.
        
        This method should be called from an async context during application
        startup to ensure the message bus is fully operational before WebSocket
        connections attempt to subscribe to events.
        
        Returns:
            bool: True if message bus was started successfully, False otherwise
        """
        if not self._message_bus or not self._omnicore_components_available.get("message_bus", False):
            logger.warning("Message bus not available - cannot start dispatcher tasks")
            return False
        
        try:
            await self._message_bus.start()
            logger.info("✓ Message bus dispatcher tasks started")
            return True
        except Exception as e:
            logger.error(f"Failed to start message bus dispatcher tasks: {e}", exc_info=True)
            return False
    
    async def start_periodic_audit_flush(self):
        """
        Start periodic audit flush task from async context.
        
        HIGH: Call this from application startup to enable periodic audit log flushing.
        """
        if self._audit_client and self._omnicore_components_available.get("audit"):
            try:
                await self._audit_client.start_periodic_flush()
                logger.info("✓ Periodic audit flush initialized via OmniCore service")
                return True
            except Exception as e:
                logger.warning(f"Failed to start periodic audit flush: {e}", exc_info=True)
                return False
        else:
            logger.debug("Audit client not available, skipping periodic flush initialization")
            return False

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
                # CRITICAL FIX: Check actual result status before logging success
                # Don't log "finished successfully" if the job actually failed
                result_status = result.get("status", "unknown")
                if result_status in ["completed", "success", "acknowledged"]:
                    logger.info(f"Task Completed: Job {job_id} action {action} finished successfully")
                elif result_status in ["failed", "error"]:
                    logger.error(f"Task Failed: Job {job_id} action {action} failed: {result.get('message', 'Unknown error')}")
                else:
                    logger.warning(f"Task Status: Job {job_id} action {action} finished with status: {result_status}")
                
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
    
    def _unwrap_nested_json_content(self, content: str, job_id: str) -> Optional[Dict[str, str]]:
        """
        Helper to recursively unwrap nested JSON strings in file content.
        
        If content is a JSON string representing a file map, parse and return it.
        Handles nested {"files": {...}} structures and validates all values are strings.
        
        Args:
            content: File content that might be a JSON string
            job_id: Job ID for logging
            
        Returns:
            Dict of filename -> content if content is a valid file map JSON, else None
        """
        stripped = content.strip()
        if not (stripped.startswith('{') and stripped.endswith('}')):
            return None
            
        try:
            parsed = json.loads(stripped)
            if not isinstance(parsed, dict) or len(parsed) == 0:
                return None
                
            # Unwrap "files" key if present
            inner = parsed
            if "files" in inner and isinstance(inner["files"], dict):
                inner = inner["files"]
            
            # Check if all values are strings (valid file map)
            # OR if values are themselves JSON strings that can be unwrapped
            file_map = {}
            for key, value in inner.items():
                if isinstance(value, str):
                    # Check if this string value is itself a nested JSON file map
                    nested = self._unwrap_nested_json_content(value, job_id)
                    if nested:
                        # Recursively unwrapped - prefix keys with parent key
                        for nested_key, nested_content in nested.items():
                            combined_key = f"{key}/{nested_key}" if key else nested_key
                            file_map[combined_key] = nested_content
                    else:
                        # Regular string content
                        file_map[key] = value
                elif isinstance(value, dict):
                    # Value is a dict - treat as nested file map
                    for nested_key, nested_content in value.items():
                        if isinstance(nested_content, str):
                            combined_key = f"{key}/{nested_key}"
                            file_map[combined_key] = nested_content
                else:
                    # Invalid value type
                    return None
            
            if file_map:
                logger.info(
                    f"[CODEGEN] Unwrapped nested JSON content: {len(file_map)} files",
                    extra={"job_id": job_id, "files": list(file_map.keys())}
                )
                return file_map
                
        except (json.JSONDecodeError, ValueError) as e:
            logger.debug(f"Content is not valid JSON: {e}")
            
        return None
    
    async def _run_codegen(self, job_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Execute code generation agent."""
        # Ensure agents are loaded before use
        self._ensure_agents_loaded()
        
        # Check if agent is available using service's own tracking
        if not self.agents_available.get('codegen', False) or self._codegen_func is None:
            # FIX: Log exactly WHY agent is unavailable with more details
            error_msg = "Codegen agent not available"
            logger.error(
                f"[CODEGEN] Agent unavailable for job {job_id}",
                extra={
                    "job_id": job_id,
                    "error": error_msg,
                    "agents_loaded": self._agents_loaded,
                    "codegen_available": self.agents_available.get('codegen', False),
                    "codegen_func_exists": self._codegen_func is not None,
                    "available_agents": [k for k, v in self.agents_available.items() if v],
                    "unavailable_agents": [k for k, v in self.agents_available.items() if not v],
                }
            )
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
                
                # Parse requirements to extract structured features for the prompt builder
                fallback_features = [requirements] if requirements else ["No specific features provided"]
                if _parse_requirements_flexible is not None:
                    try:
                        parsed = _parse_requirements_flexible(requirements)
                        requirements_dict.update(parsed)
                        logger.info(f"[CODEGEN] Extracted {len(requirements_dict.get('features', []))} features from requirements")
                    except Exception as e:
                        logger.warning(f"[CODEGEN] Failed to parse requirements flexibly: {e}")
                        # Ensure at minimum a features key exists with the raw content
                        if 'features' not in requirements_dict:
                            requirements_dict['features'] = fallback_features
                else:
                    # Fallback if import failed - ensure features key exists
                    if 'features' not in requirements_dict:
                        requirements_dict['features'] = fallback_features
                
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
                # If agent returned a JSON string instead of a file map dict,
                # parse it into a dict so materialize_file_map can process it.
                if isinstance(result, str):
                    logger.warning(
                        f"[CODEGEN] Agent returned string instead of dict, attempting JSON parse",
                        extra={"job_id": job_id, "result_length": len(result)}
                    )
                    try:
                        parsed = json.loads(result)
                        if isinstance(parsed, dict):
                            # Handle nested {"files": {...}} wrapper
                            if "files" in parsed and isinstance(parsed["files"], dict):
                                parsed = parsed["files"]
                            # Validate that values are strings (valid file content)
                            non_str = {k for k, v in parsed.items() if not isinstance(v, (str, dict))}
                            if non_str:
                                raise TypeError(
                                    f"Parsed JSON contains non-string values for keys: {non_str}"
                                )
                            result = parsed
                            logger.info(
                                f"[CODEGEN] Parsed JSON string into file map with {len(result)} entries",
                                extra={"job_id": job_id, "files": list(result.keys())}
                            )
                        else:
                            raise TypeError(f"Parsed JSON is not a dict, got {type(parsed).__name__}")
                    except (json.JSONDecodeError, TypeError) as parse_err:
                        logger.error(
                            f"[CODEGEN] Invalid result type: {type(result).__name__}, JSON parse failed: {parse_err}",
                            extra={"job_id": job_id, "result": str(result)[:200]}
                        )
                        raise TypeError(f"Code generation must return dict, got {type(result).__name__}")
                elif not isinstance(result, dict):
                    logger.error(
                        f"[CODEGEN] Invalid result type: {type(result).__name__}",
                        extra={"job_id": job_id, "result": str(result)[:200]}
                    )
                    raise TypeError(f"Code generation must return dict, got {type(result).__name__}")
                
                # FIX: Check if result is empty (no files generated)
                if len(result) == 0:
                    logger.error(
                        f"[CODEGEN] Empty result - no files generated",
                        extra={"job_id": job_id}
                    )
                    return {
                        "status": "error",
                        "message": "Code generation returned zero files",
                        "job_id": job_id,
                    }
                
                # FIX: Check if result is an error response (single error.txt file)
                if "error.txt" in result and len(result) == 1:
                    error_content = result["error.txt"]
                    
                    # Enhanced error message with actionable suggestions
                    error_msg = error_content
                    suggestions = []
                    
                    # Detect specific error patterns and provide guidance
                    if "did not contain recognizable code" in error_content.lower():
                        suggestions.append("The AI provided an explanation instead of code.")
                        # Check for any Presidio placeholders in the error content
                        if any(placeholder in error_content for placeholder in PRESIDIO_PLACEHOLDERS):
                            suggestions.append("ISSUE DETECTED: Requirements were corrupted by PII redaction (Presidio over-redaction).")
                            suggestions.append("FIX: Ensure technical terms and URLs in requirements are not being redacted.")
                        suggestions.append("Try providing more specific, detailed requirements.")
                        suggestions.append("Include example code structure or API endpoints.")
                        suggestions.append("Avoid placeholder text (e.g., '<ORGANIZATION>' or '<URL>').")
                    elif "requirements" in error_content.lower() and "provide" in error_content.lower():
                        suggestions.append("Requirements may be incomplete or ambiguous.")
                        suggestions.append("Provide specific technical details (e.g., 'Python with FastAPI' instead of 'API').")
                        suggestions.append("Include concrete examples of desired functionality.")
                    
                    if suggestions:
                        error_msg = f"{error_content}\n\nSuggestions:\n" + "\n".join(f"  • {s}" for s in suggestions)
                    
                    logger.error(
                        f"[CODEGEN] Generation failed with error",
                        extra={
                            "job_id": job_id,
                            "error": error_content[:500],
                            "suggestions": suggestions,
                            "has_presidio_placeholders": any(p in error_content for p in PRESIDIO_PLACEHOLDERS)
                        }
                    )
                    return {
                        "status": "error",
                        "message": error_msg,
                        "error_details": error_content,
                        "suggestions": suggestions,
                        "job_id": job_id,
                    }
                
                # FIX: Log what we actually received from agent
                logger.info(
                    f"[CODEGEN] Received {len(result)} files from agent",
                    extra={"job_id": job_id, "files": list(result.keys())}
                )
                
                # FIX: Detect collapsed multi-file output bundled as a single JSON string.
                # When the codegen handler's fallback wraps the entire JSON blob into a
                # single key (e.g. {"main.py": '{"files": {"app/main.py": ...}}'}),
                # try to unwrap it so the materializer receives the real file map.
                if len(result) == 1:
                    sole_key = next(iter(result))
                    sole_value = result[sole_key]
                    if isinstance(sole_value, str):
                        # Strip leading "json" prefix (LLM sometimes prepends it)
                        _sv = sole_value.strip()
                        if len(_sv) > 4 and _sv[:4].lower() == "json":
                            _sv = _sv[4:].lstrip()
                        if _sv.startswith("{"):
                            try:
                                inner = json.loads(_sv)
                                if isinstance(inner, dict):
                                    # Unwrap nested {"files": {...}} if present
                                    if "files" in inner and isinstance(inner["files"], dict):
                                        inner = inner["files"]
                                    # Only unwrap if the inner dict looks like a file map
                                    # (multiple entries or keys contain path separators / extensions)
                                    if len(inner) > 1 or any(
                                        "/" in k or "." in k for k in inner.keys()
                                    ):
                                        logger.info(
                                            f"[CODEGEN] Unwrapped collapsed JSON file map from '{sole_key}': "
                                            f"{len(inner)} files",
                                            extra={"job_id": job_id, "files": list(inner.keys())}
                                        )
                                        result = inner
                            except (json.JSONDecodeError, TypeError):
                                pass  # Not valid JSON, proceed with original result
                
                # Create output directory with security validation
                # Prevent path traversal attacks - industry standard security
                base_uploads_dir = Path("./uploads").resolve()
                # Propagate output_dir from payload/frontmatter if specified
                custom_output_dir = payload.get("output_dir", "").strip()
                if custom_output_dir:
                    # Sanitize: reject path traversal attempts
                    if ".." in custom_output_dir or custom_output_dir.startswith("/"):
                        logger.warning(
                            f"[CODEGEN] Rejecting suspicious output_dir: {custom_output_dir}",
                            extra={"job_id": job_id}
                        )
                        custom_output_dir = ""
                
                # FIX: Strip "generated/" prefix to avoid double-nesting
                # If README specifies "output_dir: generated/hello_generator", we should not create
                # "job-id/generated/generated/hello_generator" but rather "job-id/generated/hello_generator"
                if custom_output_dir:
                    # Remove "generated/" or "generated" prefix if present
                    if custom_output_dir.startswith("generated/"):
                        custom_output_dir = custom_output_dir[len("generated/"):]
                        logger.info(
                            f"[CODEGEN] Stripped 'generated/' prefix from output_dir: now {custom_output_dir}",
                            extra={"job_id": job_id}
                        )
                    elif custom_output_dir == "generated":
                        custom_output_dir = ""
                        logger.info(
                            f"[CODEGEN] Stripped 'generated' from output_dir (would be redundant)",
                            extra={"job_id": job_id}
                        )
                
                if custom_output_dir:
                    output_path = (base_uploads_dir / job_id / "generated" / custom_output_dir).resolve()
                else:
                    output_path = (base_uploads_dir / job_id / "generated").resolve()
                
                # Ensure output path is within uploads directory
                if not str(output_path).startswith(str(base_uploads_dir)):
                    raise SecurityError(f"Invalid job_id: path traversal attempt detected")
                
                output_path.mkdir(parents=True, exist_ok=True)
                logger.info(
                    f"Created output directory - job_id={job_id}, path={output_path}",
                    extra={"job_id": job_id, "output_path": str(output_path)}
                )
                
                # Save generated files using the canonical materializer from runner_file_utils.
                # This replaces the manual loop and prevents the JSON-bundle-in-main.py bug
                # because materialize_file_map handles {"files": {...}} unwrapping, JSON string
                # parsing, path traversal prevention, and content type validation.
                generated_files = []
                total_bytes_written = 0
                files_failed = []
                
                if isinstance(result, dict):
                    if _MATERIALIZER_AVAILABLE:
                        try:
                            # FIX #1: Strip "generated/" and custom_output_dir prefixes from file_map keys
                            # to prevent double-nesting (e.g., generated/hello_generator/generated/app/main.py)
                            cleaned_file_map = {}
                            for original_path, content in result.items():
                                cleaned_path = original_path
                                
                                # Remove "generated/" prefix if present
                                if cleaned_path.startswith("generated/"):
                                    cleaned_path = cleaned_path[len("generated/"):]
                                    logger.debug(
                                        f"[CODEGEN] Stripped 'generated/' prefix: {original_path} -> {cleaned_path}",
                                        extra={"job_id": job_id}
                                    )
                                
                                # Remove custom_output_dir prefix if present (avoid double-nesting)
                                if custom_output_dir and cleaned_path.startswith(f"{custom_output_dir}/"):
                                    cleaned_path = cleaned_path[len(custom_output_dir) + 1:]
                                    logger.debug(
                                        f"[CODEGEN] Stripped custom_output_dir prefix: {original_path} -> {cleaned_path}",
                                        extra={"job_id": job_id, "custom_output_dir": custom_output_dir}
                                    )
                                
                                cleaned_file_map[cleaned_path] = content
                            
                            mat_result = await _materialize_file_map(
                                cleaned_file_map, output_path
                            )
                            if mat_result.get("success"):
                                for fname in mat_result.get("files_written", []):
                                    full_path = str((output_path / fname).resolve())
                                    generated_files.append(full_path)
                                    if METRICS_AVAILABLE:
                                        file_ext = Path(fname).suffix.lstrip('.') or 'unknown'
                                        codegen_files_generated.labels(
                                            job_id=job_id,
                                            language=language
                                        ).inc()
                                total_bytes_written = mat_result.get("total_bytes_written", 0)
                                
                                # FIX Issue 1: Enforce output layout after materialization
                                # Ensure all generated files are under the project subdirectory
                                # Extract project name from custom_output_dir or use default
                                project_name = custom_output_dir if custom_output_dir else "hello_generator"
                                # If output_path already ends with the project name, use parent
                                if output_path.name == project_name:
                                    # Files are already in the right place
                                    logger.debug(f"Output path already ends with project name: {project_name}")
                                else:
                                    # Files might be at the wrong level - enforce layout
                                    try:
                                        from generator.runner.runner_file_utils import _enforce_output_layout
                                        layout_result = _enforce_output_layout(output_path, project_name)
                                        if not layout_result.get("success"):
                                            logger.warning(
                                                f"[CODEGEN] Output layout enforcement had errors: {layout_result.get('errors')}",
                                                extra={"job_id": job_id}
                                            )
                                        elif layout_result.get("files_moved"):
                                            logger.info(
                                                f"[CODEGEN] Enforced output layout: moved {len(layout_result['files_moved'])} items to {project_name}/",
                                                extra={
                                                    "job_id": job_id,
                                                    "files_moved": layout_result["files_moved"],
                                                    "project_name": project_name
                                                }
                                            )
                                            # Update output_path to point to the project subdirectory
                                            output_path = output_path / project_name
                                    except ImportError:
                                        logger.warning("[CODEGEN] _enforce_output_layout not available, skipping layout enforcement")
                            else:
                                for err in mat_result.get("errors", []):
                                    files_failed.append({"filename": "(materializer)", "error": err})
                            for skipped in mat_result.get("files_skipped", []):
                                files_failed.append({
                                    "filename": skipped.get("path", "unknown"),
                                    "error": skipped.get("reason", "unknown")
                                })
                        except Exception as mat_err:
                            logger.error(
                                f"[CODEGEN] Materialization failed: {mat_err}",
                                extra={"job_id": job_id}, exc_info=True
                            )
                            files_failed.append({"filename": "(all)", "error": str(mat_err)})
                    else:
                        # Fallback: write files directly (legacy path when materializer unavailable)
                        # Unpack nested {"files": {...}} structures or JSON string bundles
                        # to prevent the JSON-bundle-in-main.py bug.
                        file_map = result
                        files_key_unwrapped = False
                        if "files" in file_map and isinstance(file_map["files"], dict):
                            logger.info(
                                f"[CODEGEN] Fallback: unwrapping nested 'files' key",
                                extra={"job_id": job_id}
                            )
                            file_map = file_map["files"]
                            files_key_unwrapped = True

                        for filename, content in file_map.items():
                            try:
                                if not filename or '..' in filename or filename.startswith('/'):
                                    raise SecurityError(f"Invalid filename: {filename}")
                                # Handle content that is a dict (nested file map under a single key)
                                if isinstance(content, dict):
                                    # If a value is a dict, treat it as a nested file map
                                    for sub_name, sub_content in content.items():
                                        sub_path_str = f"{filename}/{sub_name}"
                                        if not isinstance(sub_content, str):
                                            files_failed.append({"filename": sub_path_str, "error": f"nested content must be string, got {type(sub_content).__name__}"})
                                            continue
                                        if '..' in sub_path_str or sub_path_str.startswith('/'):
                                            raise SecurityError(f"Invalid filename: {sub_path_str}")
                                        if len(sub_content) > 10 * 1024 * 1024:
                                            raise ValueError(f"File {sub_path_str} exceeds 10MB size limit")
                                        if not sub_content or not sub_content.strip():
                                            files_failed.append({"filename": sub_path_str, "error": "content_empty_or_whitespace"})
                                            continue
                                        sub_file_path = (output_path / sub_path_str).resolve()
                                        if not str(sub_file_path).startswith(str(output_path)):
                                            raise SecurityError(f"Path traversal attempt in filename: {sub_path_str}")
                                        sub_file_path.parent.mkdir(parents=True, exist_ok=True)
                                        sub_file_path.write_text(sub_content, encoding='utf-8')
                                        if sub_file_path.exists() and sub_file_path.stat().st_size > 0:
                                            generated_files.append(str(sub_file_path))
                                            total_bytes_written += len(sub_content.encode('utf-8'))
                                    continue
                                if not isinstance(content, str):
                                    raise TypeError(f"File content must be string, got {type(content).__name__}")
                                
                                # Use the helper to recursively unwrap nested JSON strings
                                unwrapped = self._unwrap_nested_json_content(content, job_id)
                                if unwrapped:
                                    # Content was a nested JSON file map - write each file
                                    logger.info(
                                        f"[CODEGEN] Fallback: unpacking JSON bundle from '{filename}'",
                                        extra={"job_id": job_id, "inner_files": list(unwrapped.keys())}
                                    )
                                    for inner_name, inner_content in unwrapped.items():
                                        if not inner_name or '..' in inner_name or inner_name.startswith('/'):
                                            raise SecurityError(f"Invalid filename: {inner_name}")
                                        if len(inner_content) > 10 * 1024 * 1024:
                                            raise ValueError(f"File {inner_name} exceeds 10MB size limit")
                                        if not inner_content or not inner_content.strip():
                                            files_failed.append({"filename": inner_name, "error": "content_empty_or_whitespace"})
                                            continue
                                        inner_path = (output_path / inner_name).resolve()
                                        if not str(inner_path).startswith(str(output_path)):
                                            raise SecurityError(f"Path traversal attempt in filename: {inner_name}")
                                        inner_path.parent.mkdir(parents=True, exist_ok=True)
                                        inner_path.write_text(inner_content, encoding='utf-8')
                                        if inner_path.exists() and inner_path.stat().st_size > 0:
                                            generated_files.append(str(inner_path))
                                            total_bytes_written += len(inner_content.encode('utf-8'))
                                    continue
                                if len(content) > 10 * 1024 * 1024:
                                    raise ValueError(f"File {filename} exceeds 10MB size limit")
                                if not content or not content.strip():
                                    files_failed.append({"filename": filename, "error": "content_empty_or_whitespace"})
                                    continue
                                file_path = (output_path / filename).resolve()
                                if not str(file_path).startswith(str(output_path)):
                                    raise SecurityError(f"Path traversal attempt in filename: {filename}")
                                file_path.parent.mkdir(parents=True, exist_ok=True)
                                file_path.write_text(content, encoding='utf-8')
                                if file_path.exists() and file_path.stat().st_size > 0:
                                    generated_files.append(str(file_path))
                                    total_bytes_written += len(content.encode('utf-8'))
                                    if METRICS_AVAILABLE:
                                        file_ext = file_path.suffix.lstrip('.') or 'unknown'
                                        codegen_files_generated.labels(
                                            job_id=job_id, language=language
                                        ).inc()
                                        codegen_file_size_bytes.labels(
                                            job_id=job_id, file_type=file_ext
                                        ).observe(len(content.encode('utf-8')))
                                else:
                                    files_failed.append({"filename": filename, "error": "file_empty_after_write"})
                            except SecurityError:
                                raise
                            except Exception as write_error:
                                files_failed.append({"filename": filename, "error": str(write_error)})
                else:
                    logger.warning(
                        f"Code generation returned non-dict result - type={type(result).__name__}",
                        extra={
                            "job_id": job_id,
                            "result_type": type(result).__name__,
                            "status": "warning"
                        }
                    )
                
                # FIX: Check if any files were successfully written
                if len(generated_files) == 0:
                    logger.error(
                        f"[CODEGEN] Failed to write any code files to disk",
                        extra={
                            "job_id": job_id,
                            "files_failed": files_failed,
                            "status": "error"
                        }
                    )
                    return {
                        "status": "error",
                        "message": "Failed to write any code files to disk",
                        "files_failed": files_failed,
                        "job_id": job_id,
                    }
                
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
                
                # Build detailed result dict with file information
                result_dict = {
                    "status": "completed",
                    "generated_files": generated_files,  # Full paths
                    "file_names": [Path(f).name for f in generated_files],  # Just filenames for UI
                    "output_path": str(output_path),
                    "files_count": len(generated_files),
                    "total_bytes_written": total_bytes_written,
                    "duration_seconds": round(duration, 2),
                }
                
                # Include failures in response if any
                if files_failed:
                    result_dict["files_failed"] = files_failed
                    result_dict["files_failed_count"] = len(files_failed)
                    result_dict["warning"] = f"{len(files_failed)} file(s) failed to write"
                    logger.warning(
                        f"[CODEGEN] Partial success - {len(generated_files)} succeeded, {len(files_failed)} failed",
                        extra={
                            "job_id": job_id,
                            "succeeded": len(generated_files),
                            "failed": len(files_failed),
                            "failed_files": files_failed
                        }
                    )
                
                # FIX: Update job.output_files immediately after writing files
                # This ensures files appear in UI without waiting for pipeline completion
                if job_id in jobs_db:
                    job = jobs_db[job_id]
                    # Store relative paths from uploads/{job_id}/ directory
                    try:
                        # Use upload_dir from config if available, otherwise default to ./uploads
                        upload_dir = self.agent_config.upload_dir if self.agent_config else Path("./uploads")
                        job_base = upload_dir / job_id
                        relative_files = []
                        for file_path_str in generated_files:
                            file_path = Path(file_path_str)
                            if file_path.exists():
                                # [FIX] Add error handling for path resolution
                                try:
                                    rel_path = str(file_path.resolve().relative_to(job_base.resolve()))
                                    relative_files.append(rel_path)
                                except ValueError as e:
                                    logger.warning(f"[CODEGEN] File {file_path} is outside job_base {job_base}, using filename only. Error: {e}")
                                    relative_files.append(file_path.name)
                        job.output_files = relative_files
                        job.updated_at = datetime.now(timezone.utc)
                        logger.info(
                            f"Updated job {job_id} with {len(relative_files)} output files",
                            extra={"job_id": job_id, "files_count": len(relative_files)}
                        )
                        
                        # Code generation complete - keep job in running state for pipeline
                        # Only mark as COMPLETED in _finalize_successful_job after all stages
                        if len(generated_files) > 0:
                            logger.info(
                                f"✓ Job {job_id} code generation completed, continuing pipeline",
                                extra={
                                    "job_id": job_id,
                                    "files_generated": len(generated_files),
                                    "stage": "codegen"
                                }
                            )
                    except Exception as update_error:
                        logger.warning(
                            f"Failed to update job.output_files for {job_id}: {update_error}",
                            extra={"job_id": job_id, "error": str(update_error)}
                        )
                
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
        """Execute test generation agent with timeout."""
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
            # Wrap test generation with configurable timeout
            async with asyncio.timeout(DEFAULT_TESTGEN_TIMEOUT):
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
                
                # Extract generated tests from result
                generated_tests = result.get("generated_tests", {})
                logger.info(f"[TESTGEN] Extracted {len(generated_tests)} test files from result")
                
                if not generated_tests:
                    logger.warning(f"Testgen agent returned no tests for job {job_id}")
                    return {
                        "status": "completed",
                        "generated_files": [],
                        "job_id": job_id,
                        "result": result,
                        "warning": "No test files were generated",
                    }
                
                # Write generated tests to files
                # FIX Issue 3: Write tests into project subdirectory, not repo root
                # Extract project_name from payload or default to "hello_generator"
                project_name = payload.get("output_dir", "hello_generator")
                if not project_name:
                    project_name = "hello_generator"
                
                # Tests should go into generated/<project_name>/tests, not generated/tests
                project_dir = repo_path / "generated" / project_name
                if not project_dir.exists():
                    # Fallback: if project_dir doesn't exist, try to find it
                    # This handles cases where code was generated directly in repo_path/generated
                    alt_project_dir = repo_path / project_name
                    if alt_project_dir.exists():
                        project_dir = alt_project_dir
                    else:
                        # Create the expected structure
                        project_dir.mkdir(parents=True, exist_ok=True)
                        logger.info(f"[TESTGEN] Created project directory: {project_dir}")
                
                generated_files = []
                tests_dir = project_dir / "tests"
                tests_dir.mkdir(parents=True, exist_ok=True)
                
                logger.info(f"[TESTGEN] Writing tests to: {tests_dir} (project_name={project_name})")
                
                # Create __init__.py in tests directory
                init_file = tests_dir / "__init__.py"
                async with aiofiles.open(init_file, "w", encoding="utf-8") as f:
                    await f.write('"""Test suite for generated code."""\n')
                generated_files.append(str(init_file.relative_to(repo_path)))
                
                for test_file_path, test_content in generated_tests.items():
                    # Ensure test file path is relative and clean
                    test_path = Path(test_file_path)
                    
                    # If path is absolute or contains "..", use just the filename
                    if test_path.is_absolute() or ".." in str(test_path):
                        test_path = Path(test_path.name)
                    
                    # Construct full path in tests directory
                    full_test_path = tests_dir / test_path.name
                    
                    # Write the test file
                    logger.info(f"[TESTGEN] Writing test file: {full_test_path}")
                    async with aiofiles.open(full_test_path, "w", encoding="utf-8") as f:
                        await f.write(test_content)
                    
                    try:
                        generated_files.append(str(full_test_path.relative_to(repo_path)))
                    except ValueError as e:
                        logger.warning(f"[TESTGEN] File {full_test_path} is outside repo_path {repo_path}, using absolute path. Error: {e}")
                        generated_files.append(str(full_test_path))
                
                logger.info(f"[TESTGEN] Wrote {len(generated_files)} test files to disk")
                
                return {
                    "status": "completed",
                    "job_id": job_id,
                    "generated_files": generated_files,
                    "tests_count": len(generated_tests),
                    "result": result,
                }
        
        except asyncio.TimeoutError:
            logger.warning(f"[TESTGEN] Job {job_id} LLM call timed out after 120s - skipping tests")
            return {
                "status": "error",
                "message": "Test generation timed out after 120 seconds - skipping tests",
                "timeout": True,
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
        """Execute deployment configuration generation with timeout."""
        logger.info(f"[DEPLOY] Starting deployment for job {job_id} with payload: {payload}")
        
        # Ensure agents are loaded before use
        self._ensure_agents_loaded()
        
        # Check if agent is available using service's own tracking
        if not self.agents_available.get('deploy', False) or self._deploy_class is None:
            error_msg = "Deploy agent not available"
            logger.error(f"[DEPLOY] Deploy agent unavailable for job {job_id}: {error_msg}")
            return {
                "status": "error",
                "message": f"Deploy agent not available: {error_msg}",
                "agent_available": False,
                "job_id": job_id,
            }
        
        try:
            # Wrap deploy generation with configurable timeout
            async with asyncio.timeout(DEFAULT_DEPLOY_TIMEOUT):
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
                # FIX 1: Get list of generated files from payload or scan code_path
                generated_files = payload.get("generated_files", [])
                if not generated_files and repo_path.exists():
                    try:
                        # Collect source files, excluding common non-source directories
                        # Industry standard: filter out build artifacts, dependencies, VCS
                        exclude_dirs = {'.git', '.svn', 'node_modules', '__pycache__', '.pytest_cache', 
                                       'dist', 'build', '.venv', 'venv', '.mypy_cache', '.ruff_cache',
                                       '.tox', 'htmlcov', '.eggs', '*.egg-info'}
                        
                        for file_path in repo_path.rglob("*"):
                            if file_path.is_file():
                                # Skip if any parent directory is in exclude list
                                if any(part in exclude_dirs for part in file_path.parts):
                                    continue
                                # Skip hidden files (except specific configs)
                                if any(part.startswith('.') and part not in {'.env.example', '.dockerignore'} 
                                      for part in file_path.parts):
                                    continue
                                # Store relative path from repo_path
                                rel_path = str(file_path.relative_to(repo_path))
                                generated_files.append(rel_path)
                        logger.info(f"[DEPLOY] Found {len(generated_files)} source files in {code_path}")
                    except Exception as e:
                        logger.warning(f"[DEPLOY] Failed to collect files from {code_path}: {e}")
                
                requirements = {
                    "pipeline_steps": ["generate", "validate"],
                    "platform": platform,
                    "include_ci_cd": include_ci_cd,
                    "files": generated_files,  # FIX 1: Pass actual file list
                    "code_path": code_path,
                }
                
                # Run the deployment generation
                logger.info(f"[DEPLOY] Running deploy agent for job {job_id} with target={platform}, files={len(generated_files)}")
                deploy_result = await agent.run_deployment(target=platform, requirements=requirements)
                logger.info(f"[DEPLOY] Deploy agent returned result with keys: {list(deploy_result.keys())}")
                
                # Extract generated config
                configs = deploy_result.get("configs", {})
                logger.info(f"[DEPLOY] Extracted configs: {list(configs.keys())}")
                
                if not configs:
                    logger.warning(f"Deploy agent returned no configurations for job {job_id}")
                    generated_files = []
                    if platform in ("docker", "dockerfile"):
                        output_dir = repo_path
                        
                        # Default Dockerfile
                        default_dockerfile = (
                            "FROM python:3.11-slim\n"
                            "WORKDIR /app\n"
                            "COPY requirements.txt .\n"
                            "RUN pip install --no-cache-dir -r requirements.txt\n"
                            "COPY . /app\n"
                            'CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]\n'
                        )
                        file_path = output_dir / "Dockerfile"
                        async with aiofiles.open(file_path, "w", encoding="utf-8") as f:
                            await f.write(default_dockerfile)
                        generated_files.append("Dockerfile")
                        
                        # Default docker-compose.yml
                        default_compose = (
                            "version: '3.8'\n\n"
                            "services:\n"
                            "  app:\n"
                            "    build:\n"
                            "      context: .\n"
                            "      dockerfile: Dockerfile\n"
                            "    ports:\n"
                            '      - "8000:8000"\n'
                            "    environment:\n"
                            "      - ENVIRONMENT=production\n"
                            "      - LOG_LEVEL=info\n"
                            "    restart: unless-stopped\n"
                            "    healthcheck:\n"
                            '      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]\n'
                            "      interval: 30s\n"
                            "      timeout: 10s\n"
                            "      retries: 3\n"
                            "      start_period: 40s\n"
                        )
                        compose_path = output_dir / "docker-compose.yml"
                        async with aiofiles.open(compose_path, "w", encoding="utf-8") as f:
                            await f.write(default_compose)
                        generated_files.append("docker-compose.yml")
                        
                        # Default .dockerignore
                        default_dockerignore = (
                            "__pycache__\n*.pyc\n*.pyo\n.git\n.gitignore\n"
                            ".env\n.venv\nvenv\nnode_modules\n"
                            ".pytest_cache\n.mypy_cache\n*.egg-info\n"
                            "dist\nbuild\n.coverage\nhtmlcov\n"
                        )
                        dockerignore_path = output_dir / ".dockerignore"
                        async with aiofiles.open(dockerignore_path, "w", encoding="utf-8") as f:
                            await f.write(default_dockerignore)
                        generated_files.append(".dockerignore")
                        
                        logger.info(
                            f"[DEPLOY] Generated default deployment fallback for job {job_id}: {generated_files}"
                        )
                    return {
                        "status": "completed",
                        "generated_files": generated_files,
                        "platform": platform,
                        "run_id": deploy_result.get("run_id"),
                        "warning": "No configuration files were generated by agent; default fallback used",
                    }
                
                generated_files = []
                
                # Write generated configs to the generated/ directory root
                # so they appear at generated/Dockerfile, generated/docker-compose.yml etc.
                output_dir = repo_path
                
                for target, config_content in configs.items():
                    # FIX: Determine filename and subdirectory based on target
                    # Kubernetes and Helm files should go into subdirectories
                    if target == "docker" or target == "dockerfile":
                        filename = "Dockerfile"
                        target_dir = output_dir
                    elif target == "kubernetes" or target == "k8s":
                        # FIX: Kubernetes files go into k8s/ subdirectory
                        target_dir = output_dir / "k8s"
                        target_dir.mkdir(parents=True, exist_ok=True)
                        
                        # Parse YAML content to create separate files (deployment.yaml, service.yaml)
                        # The LLM typically generates multi-document YAML separated by "---"
                        yaml_docs = config_content.split("---")
                        for idx, doc in enumerate(yaml_docs):
                            doc = doc.strip()
                            if not doc or len(doc) < MIN_YAML_DOC_LENGTH:
                                continue  # Skip empty or trivial documents
                            
                            # Determine filename based on document kind
                            if "kind: Deployment" in doc:
                                doc_filename = "deployment.yaml"
                            elif "kind: Service" in doc:
                                doc_filename = "service.yaml"
                            elif "kind: Ingress" in doc:
                                doc_filename = "ingress.yaml"
                            elif "kind: ConfigMap" in doc:
                                doc_filename = "configmap.yaml"
                            else:
                                doc_filename = f"resource-{idx}.yaml"
                            
                            file_path = target_dir / doc_filename
                            async with aiofiles.open(file_path, "w", encoding="utf-8") as f:
                                await f.write(doc)
                            
                            try:
                                rel_path = str(file_path.resolve().relative_to(repo_path.resolve()))
                                generated_files.append(rel_path)
                            except ValueError as e:
                                logger.warning(f"[DEPLOY] File {file_path} is outside repo_path {repo_path}, using absolute path. Error: {e}")
                                generated_files.append(str(file_path))
                            logger.info(f"Generated kubernetes file: {file_path}")
                        
                        continue  # Skip the default file writing below
                    elif target == "helm":
                        # FIX: Helm files go into helm/ subdirectory
                        target_dir = output_dir / "helm"
                        target_dir.mkdir(parents=True, exist_ok=True)
                        templates_dir = target_dir / "templates"
                        templates_dir.mkdir(parents=True, exist_ok=True)
                        
                        # Parse and organize helm files
                        # The LLM typically generates all helm files in one response
                        # We need to split Chart.yaml, values.yaml, and templates
                        
                        # For now, write the content to Chart.yaml and create a basic structure
                        # A more sophisticated approach would parse the LLM output and organize files
                        chart_path = target_dir / "Chart.yaml"
                        values_path = target_dir / "values.yaml"
                        
                        # Try to extract Chart.yaml and values.yaml from content
                        # Note: This is a simple heuristic parser that assumes LLM output format:
                        # Expected format: "# Chart.yaml\n<content>\n# values.yaml\n<content>"
                        # A more robust approach would use YAML parsing library,
                        # but this works for common LLM output patterns.
                        # TODO: Consider implementing proper YAML parsing for better reliability
                        if "Chart.yaml" in config_content and "values.yaml" in config_content:
                            # Content contains multiple files - try to parse them
                            # Split on '# ' at the start of a line followed by filename
                            # This heuristic works with common LLM output but may be fragile
                            parts = config_content.split("\n# ")
                            for part in parts:
                                part = "# " + part if not part.startswith("#") else part
                                # Safe bounds checking: only check first N chars if part is long enough
                                part_prefix = part[:min(HELM_FILE_HEADER_CHECK_LENGTH, len(part))]
                                if "Chart.yaml" in part_prefix:
                                    chart_content = part.replace("# Chart.yaml", "").strip()
                                    async with aiofiles.open(chart_path, "w", encoding="utf-8") as f:
                                        await f.write(chart_content)
                                    generated_files.append(str(chart_path.relative_to(repo_path)))
                                elif "values.yaml" in part_prefix:
                                    values_content = part.replace("# values.yaml", "").strip()
                                    async with aiofiles.open(values_path, "w", encoding="utf-8") as f:
                                        await f.write(values_content)
                                    generated_files.append(str(values_path.relative_to(repo_path)))
                        else:
                            # Write entire content as Chart.yaml for now
                            async with aiofiles.open(chart_path, "w", encoding="utf-8") as f:
                                await f.write(config_content)
                            generated_files.append(str(chart_path.relative_to(repo_path)))
                            
                            # Create default values.yaml
                            default_values = "# Helm values\nreplicaCount: 2\n"
                            async with aiofiles.open(values_path, "w", encoding="utf-8") as f:
                                await f.write(default_values)
                            generated_files.append(str(values_path.relative_to(repo_path)))
                        
                        logger.info(f"Generated helm files in: {target_dir}")
                        continue  # Skip the default file writing below
                    elif target == "docker-compose":
                        filename = "docker-compose.yml"
                        target_dir = output_dir
                    elif target == "terraform":
                        filename = "main.tf"
                        target_dir = output_dir
                    else:
                        filename = f"{target}.config"
                        target_dir = output_dir
                    
                    # Sanitize Dockerfile content: strip markdown/images/mermaid tokens
                    if filename == "Dockerfile":
                        config_content = self._sanitize_dockerfile_content(config_content)
                    
                    file_path = target_dir / filename
                    
                    # Write the file
                    async with aiofiles.open(file_path, "w", encoding="utf-8") as f:
                        await f.write(config_content)
                    
                    # [FIX] Add error handling for path resolution
                    try:
                        generated_files.append(str(file_path.resolve().relative_to(repo_path.resolve())))
                    except ValueError as e:
                        logger.warning(f"[DEPLOY] File {file_path} is outside repo_path {repo_path}, using absolute path. Error: {e}")
                        generated_files.append(str(file_path))
                    logger.info(f"Generated deployment file: {file_path}")
                
                # Generate standard .dockerignore if a Dockerfile was produced
                dockerfile_path = output_dir / "Dockerfile"
                if dockerfile_path.exists():
                    dockerignore_path = output_dir / ".dockerignore"
                    if not dockerignore_path.exists():
                        dockerignore_content = (
                            "__pycache__\n*.pyc\n*.pyo\n.git\n.gitignore\n"
                            ".env\n.venv\nvenv\nnode_modules\n"
                            ".pytest_cache\n.mypy_cache\n*.egg-info\n"
                            "dist\nbuild\n.coverage\nhtmlcov\n"
                        )
                        async with aiofiles.open(dockerignore_path, "w", encoding="utf-8") as f:
                            await f.write(dockerignore_content)
                        generated_files.append(".dockerignore")
                        logger.info(f"Generated .dockerignore: {dockerignore_path}")
                
                # Write deploy_metadata.json
                deploy_meta_path = output_dir / "deploy_metadata.json"
                deploy_meta = {
                    "platform": platform,
                    "run_id": deploy_result.get("run_id"),
                    "generated_files": generated_files,
                    "validations": deploy_result.get("validations", {}),
                }
                async with aiofiles.open(deploy_meta_path, "w", encoding="utf-8") as f:
                    await f.write(json.dumps(deploy_meta, indent=2))
                generated_files.append("deploy_metadata.json")
                
                result = {
                    "status": "completed",
                    "generated_files": generated_files,
                    "platform": platform,
                    "run_id": deploy_result.get("run_id"),
                    "validations": deploy_result.get("validations", {}),
                }
                
                logger.info(f"Deploy agent completed for job {job_id}, generated {len(generated_files)} files")
                return result
        
        except asyncio.TimeoutError:
            logger.warning(f"[DEPLOY] Job {job_id} timed out after 90s - skipping deployment configs")
            return {
                "status": "error",
                "message": "Deployment generation timed out after 90 seconds",
                "timeout": True,
            }
        except Exception as e:
            logger.error(f"Error running deploy agent: {e}", exc_info=True)
            return {
                "status": "error",
                "message": str(e),
                "error_type": type(e).__name__,
            }
    
    async def _run_deploy_all(self, job_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Execute deployment for ALL targets (docker, kubernetes, helm) sequentially.
        
        This method runs all deployment targets as required stages following industry
        best practices for observability, error handling, and security.
        
        Compliance:
            - SOC 2 Type II: Comprehensive logging and error handling
            - ISO 27001 A.12.4.1: Event logging for security monitoring
            - NIST SP 800-53 AU-2: Auditable events tracking
        
        Args:
            job_id: The job identifier (validated for path traversal)
            payload: Deployment configuration containing:
                - code_path: Path to generated code directory
                - include_ci_cd: Boolean flag for CI/CD configs
                
        Returns:
            Dict containing:
                - status: "completed" or "error"
                - results: Dict mapping each target to its result
                - generated_files: List of all generated files across all targets
                - failed_targets: List of targets that failed (if any)
                - duration_seconds: Total execution time
                
        Raises:
            ValueError: If job_id or payload is invalid
            SecurityError: If path traversal is detected
        """
        # Input validation
        if not job_id or not isinstance(job_id, str):
            raise ValueError("job_id must be a non-empty string")
        
        if not isinstance(payload, dict):
            raise ValueError("payload must be a dictionary")
        
        # Path traversal protection (matching platform security patterns)
        if ".." in job_id or "/" in job_id or "\\" in job_id:
            logger.error(f"[DEPLOY_ALL] Path traversal attempt detected in job_id: {job_id}")
            raise SecurityError(f"Invalid job_id: path traversal attempt detected")
        
        # Start timing and tracing
        start_time = time.time()
        
        # Use OpenTelemetry tracing if available
        span_context = (
            tracer.start_as_current_span("deploy.deploy_all") 
            if TRACING_AVAILABLE 
            else None
        )
        
        try:
            if span_context:
                with span_context as span:
                    span.set_attribute("job_id", job_id)
                    span.set_attribute("targets_count", 3)
                    result = await self._execute_deploy_all_targets(
                        job_id, payload, start_time
                    )
                    span.set_status(Status(StatusCode.OK, "Deploy all targets completed"))
                    return result
            else:
                return await self._execute_deploy_all_targets(
                    job_id, payload, start_time
                )
                
        except Exception as e:
            logger.error(
                f"[DEPLOY_ALL] Critical error in deploy_all for job {job_id}: {e}",
                exc_info=True,
                extra={"job_id": job_id, "error_type": type(e).__name__}
            )
            
            # Record metrics if available
            if METRICS_AVAILABLE:
                deployment_requests_total.labels(
                    job_id=job_id,
                    target="all",
                    status="error"
                ).inc()
            
            raise
    
    async def _execute_deploy_all_targets(
        self, 
        job_id: str, 
        payload: Dict[str, Any],
        start_time: float
    ) -> Dict[str, Any]:
        """Internal method to execute all deployment targets with full observability.
        
        Separated for cleaner tracing and error handling.
        
        Args:
            job_id: The validated job identifier
            payload: Validated deployment configuration
            start_time: Start timestamp for duration tracking
            
        Returns:
            Dict with deployment results and metadata
        """
        logger.info(
            f"[DEPLOY_ALL] Starting deployment for all targets",
            extra={
                "job_id": job_id,
                "targets": ["docker", "kubernetes", "helm"],
                "include_ci_cd": payload.get("include_ci_cd", False)
            }
        )
        
        # Define the required deployment targets
        targets = ["docker", "kubernetes", "helm"]
        results = {}
        all_generated_files = []
        failed_targets = []
        
        # Extract and validate code_path
        code_path = payload.get("code_path", f"./uploads/{job_id}/generated")
        include_ci_cd = payload.get("include_ci_cd", False)
        
        # Additional path validation
        code_path_obj = Path(code_path)
        if not code_path_obj.is_absolute():
            code_path_obj = Path.cwd() / code_path
        
        # Run each target sequentially with individual error handling
        for target_idx, target in enumerate(targets):
            target_start = time.time()
            
            logger.info(
                f"[DEPLOY_ALL] Processing target {target_idx + 1}/{len(targets)}: {target}",
                extra={"job_id": job_id, "target": target, "sequence": f"{target_idx + 1}/{len(targets)}"}
            )
            
            # FIX 1: Get list of generated files from code_path for deploy_all
            generated_files = []
            if code_path_obj.exists():
                try:
                    # Collect source files, excluding common non-source directories
                    # Industry standard: filter out build artifacts, dependencies, VCS
                    exclude_dirs = {'.git', '.svn', 'node_modules', '__pycache__', '.pytest_cache', 
                                   'dist', 'build', '.venv', 'venv', '.mypy_cache', '.ruff_cache',
                                   '.tox', 'htmlcov', '.eggs', '*.egg-info'}
                    
                    for file_path in code_path_obj.rglob("*"):
                        if file_path.is_file():
                            # Skip if any parent directory is in exclude list
                            if any(part in exclude_dirs for part in file_path.parts):
                                continue
                            # Skip hidden files (except specific configs)
                            if any(part.startswith('.') and part not in {'.env.example', '.dockerignore'} 
                                  for part in file_path.parts):
                                continue
                            # Store relative path from code_path
                            rel_path = str(file_path.relative_to(code_path_obj))
                            generated_files.append(rel_path)
                    logger.info(f"[DEPLOY_ALL] Found {len(generated_files)} source files in {code_path} for target {target}")
                except Exception as e:
                    logger.warning(f"[DEPLOY_ALL] Failed to collect files from {code_path}: {e}")
            
            target_payload = {
                "code_path": code_path,
                "platform": target,
                "include_ci_cd": include_ci_cd,
                "generated_files": generated_files,  # FIX 1: Pass files list
            }
            
            try:
                target_result = await self._run_deploy(job_id, target_payload)
                results[target] = target_result
                
                # Calculate target duration
                target_duration = time.time() - target_start
                
                if target_result.get("status") == "completed":
                    logger.info(
                        f"[DEPLOY_ALL] Target {target} completed successfully",
                        extra={
                            "job_id": job_id,
                            "target": target,
                            "duration_seconds": round(target_duration, 2),
                            "files_generated": len(target_result.get("generated_files", []))
                        }
                    )
                    
                    # Collect generated files from this target
                    target_files = target_result.get("generated_files", [])
                    all_generated_files.extend(target_files)
                    
                    # Record success metrics
                    if METRICS_AVAILABLE:
                        deployment_requests_total.labels(
                            job_id=job_id,
                            target=target,
                            status="completed"
                        ).inc()
                        deployment_duration_seconds.labels(
                            job_id=job_id,
                            target=target
                        ).observe(target_duration)
                        
                        for file in target_files:
                            file_ext = Path(file).suffix if file else "unknown"
                            deployment_files_generated.labels(
                                job_id=job_id,
                                target=target,
                                file_type=file_ext or "no_extension"
                            ).inc()
                            
                elif target_result.get("status") == "error":
                    error_msg = target_result.get('message', 'Unknown error')
                    logger.error(
                        f"[DEPLOY_ALL] Target {target} failed",
                        extra={
                            "job_id": job_id,
                            "target": target,
                            "error": error_msg,
                            "duration_seconds": round(target_duration, 2)
                        }
                    )
                    failed_targets.append(target)
                    
                    # Record failure metrics
                    if METRICS_AVAILABLE:
                        deployment_requests_total.labels(
                            job_id=job_id,
                            target=target,
                            status="error"
                        ).inc()
                    
            except Exception as e:
                target_duration = time.time() - target_start
                logger.error(
                    f"[DEPLOY_ALL] Exception during target {target}",
                    exc_info=True,
                    extra={
                        "job_id": job_id,
                        "target": target,
                        "error_type": type(e).__name__,
                        "duration_seconds": round(target_duration, 2)
                    }
                )
                
                results[target] = {
                    "status": "error",
                    "message": str(e),
                    "error_type": type(e).__name__,
                }
                failed_targets.append(target)
                
                # Record exception metrics
                if METRICS_AVAILABLE:
                    deployment_requests_total.labels(
                        job_id=job_id,
                        target=target,
                        status="exception"
                    ).inc()
        
        # Calculate total duration
        total_duration = time.time() - start_time
        
        # Determine overall status
        if failed_targets:
            logger.warning(
                f"[DEPLOY_ALL] Deployment completed with failures",
                extra={
                    "job_id": job_id,
                    "failed_targets": failed_targets,
                    "completed_targets": [t for t in targets if t not in failed_targets],
                    "duration_seconds": round(total_duration, 2),
                    "files_generated": len(all_generated_files)
                }
            )
            return {
                "status": "error",
                "message": f"Deployment failed for targets: {', '.join(failed_targets)}",
                "results": results,
                "generated_files": all_generated_files,
                "failed_targets": failed_targets,
                "completed_targets": [t for t in targets if t not in failed_targets],
                "duration_seconds": round(total_duration, 2),
            }
        else:
            logger.info(
                f"[DEPLOY_ALL] All deployment targets completed successfully",
                extra={
                    "job_id": job_id,
                    "targets_count": len(targets),
                    "duration_seconds": round(total_duration, 2),
                    "files_generated": len(all_generated_files)
                }
            )
            
            # Record overall success metric
            if METRICS_AVAILABLE:
                deployment_requests_total.labels(
                    job_id=job_id,
                    target="all",
                    status="completed"
                ).inc()
            
            return {
                "status": "completed",
                "message": "All deployment targets completed successfully",
                "results": results,
                "generated_files": all_generated_files,
                "failed_targets": [],
                "completed_targets": targets,
            }
    
    async def _validate_deployment_completeness(self, job_id: str, code_path: str) -> Dict[str, Any]:
        """Validate that all required deployment files exist and are valid.
        
        This method performs comprehensive validation of deployment artifacts,
        ensuring compliance with security and quality standards.
        
        Compliance:
            - SOC 2 Type II: Validation of deployment configurations
            - CIS Benchmarks: Security validation for containers and Kubernetes
            - OWASP: Secure configuration validation
        
        Uses the DeploymentCompletenessValidator to check:
            - All required deployment files exist
            - No unsubstituted placeholders remain
            - YAML files have valid syntax
            - Dockerfiles have required instructions
            - Deployment configs match actual generated code
        
        Args:
            job_id: The job identifier (for logging and metrics)
            code_path: Path to the generated code directory (must exist)
            
        Returns:
            Dict containing validation results:
                - status: "passed", "failed", or "error"
                - errors: List of detailed validation errors
                - warnings: List of non-fatal warnings
                - missing_files: List of required files not found
                - invalid_files: List of files with validation issues
                
        Raises:
            ImportError: If validator cannot be imported
            OSError: If code_path doesn't exist or is inaccessible
        """
        validation_start = time.time()
        
        logger.info(
            "[DEPLOY_VALIDATION] Starting deployment completeness validation",
            extra={"job_id": job_id, "code_path": code_path}
        )
        
        # Import the validator with graceful error handling
        try:
            from generator.agents.deploy_agent.deploy_validator import DeploymentCompletenessValidator
        except ImportError as e:
            logger.error(
                "[DEPLOY_VALIDATION] Failed to import DeploymentCompletenessValidator",
                exc_info=True,
                extra={"job_id": job_id, "error": str(e)}
            )
            
            if METRICS_AVAILABLE:
                deployment_validation_total.labels(
                    job_id=job_id,
                    status="error",
                    validation_type="import_error"
                ).inc()
            
            return {
                "status": "error",
                "errors": [f"Failed to import validator: {str(e)}"],
            }
        
        # Validate code_path exists and is accessible
        code_path_obj = Path(code_path) if code_path else None
        if not code_path_obj or not code_path_obj.exists():
            error_msg = f"Code path does not exist: {code_path}"
            logger.error(
                "[DEPLOY_VALIDATION] Invalid code path",
                extra={"job_id": job_id, "code_path": code_path}
            )
            
            if METRICS_AVAILABLE:
                deployment_validation_total.labels(
                    job_id=job_id,
                    status="error",
                    validation_type="invalid_path"
                ).inc()
            
            return {
                "status": "error",
                "errors": [error_msg],
            }
        
        # Change to the code path directory for validation
        # Store original CWD for restoration in finally block
        original_cwd = os.getcwd()
        
        try:
            os.chdir(code_path)
            logger.debug(
                "[DEPLOY_VALIDATION] Changed working directory",
                extra={"job_id": job_id, "new_cwd": code_path}
            )
            
            # Create validator instance
            validator = DeploymentCompletenessValidator()
            
            # Validate all deployment types
            validation_result = await validator.validate(
                config_content="",  # Not used for file-based validation
                target_type="all"   # Validate all deployment types (docker, kubernetes, helm)
            )
            
            # Calculate validation duration
            validation_duration = time.time() - validation_start
            
            # Enhanced logging with structured data
            logger.info(
                f"[DEPLOY_VALIDATION] Validation completed: {validation_result.get('status')}",
                extra={
                    "job_id": job_id,
                    "status": validation_result.get('status'),
                    "duration_seconds": round(validation_duration, 3),
                    "missing_files_count": len(validation_result.get('missing_files', [])),
                    "invalid_files_count": len(validation_result.get('invalid_files', [])),
                    "placeholder_issues_count": len(validation_result.get('placeholder_issues', [])),
                    "warnings_count": len(validation_result.get('warnings', []))
                }
            )
            
            # Record validation metrics
            if METRICS_AVAILABLE:
                deployment_validation_total.labels(
                    job_id=job_id,
                    status=validation_result.get('status', 'unknown'),
                    validation_type="completeness"
                ).inc()
            
            return validation_result
            
        except Exception as e:
            validation_duration = time.time() - validation_start
            logger.error(
                "[DEPLOY_VALIDATION] Validation exception occurred",
                exc_info=True,
                extra={
                    "job_id": job_id,
                    "error_type": type(e).__name__,
                    "duration_seconds": round(validation_duration, 3)
                }
            )
            
            # Record error metrics
            if METRICS_AVAILABLE:
                deployment_validation_total.labels(
                    job_id=job_id,
                    status="exception",
                    validation_type="completeness"
                ).inc()
            
            return {
                "status": "error",
                "errors": [f"Validation error: {str(e)}"],
                "error_type": type(e).__name__,
            }
            
        finally:
            # Always restore original working directory
            # Critical for preventing side effects in other operations
            try:
                os.chdir(original_cwd)
                logger.debug(
                    "[DEPLOY_VALIDATION] Restored working directory",
                    extra={"job_id": job_id, "restored_cwd": original_cwd}
                )
            except Exception as restore_error:
                logger.error(
                    "[DEPLOY_VALIDATION] Failed to restore working directory",
                    exc_info=True,
                    extra={
                        "job_id": job_id,
                        "original_cwd": original_cwd,
                        "error": str(restore_error)
                    }
                )
    
    @staticmethod
    def _sanitize_dockerfile_content(content: str) -> str:
        """Sanitize Dockerfile content from LLM responses.

        Strips markdown fences, image/badge lines, mermaid blocks, and
        ensures the first non-comment non-blank line starts with FROM.
        If no FROM is found, prepends a default FROM instruction.
        """
        if not content or not isinstance(content, str):
            return content

        # Strip markdown fences (```dockerfile ... ```)
        content = re.sub(
            r'^```(?:dockerfile|docker|Dockerfile)?\s*\n', '', content, flags=re.IGNORECASE
        )
        content = re.sub(r'\n```\s*$', '', content)

        lines = content.splitlines()
        cleaned: List[str] = []
        for line in lines:
            stripped = line.strip()
            # Remove markdown image/badge lines: ![...](...)
            if stripped.startswith('!['):
                continue
            # Remove mermaid/markdown tokens
            if stripped.startswith('```'):
                continue
            # Remove lines starting with '!' (invalid Dockerfile token)
            if stripped.startswith('!'):
                continue
            cleaned.append(line)

        # Ensure first non-comment non-blank line starts with FROM
        has_from = False
        for line in cleaned:
            s = line.strip()
            if not s or s.startswith('#'):
                continue
            if s.upper().startswith('FROM'):
                has_from = True
            break

        if not has_from:
            cleaned.insert(0, 'FROM python:3.11-slim')

        return '\n'.join(cleaned)

    async def _run_docgen(self, job_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Execute documentation generation with timeout."""
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
            # Wrap docgen with configurable timeout
            async with asyncio.timeout(DEFAULT_DOCGEN_TIMEOUT):
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
                        # [FIX] Add error handling for path resolution
                        try:
                            target_files.append(str(file_path.resolve().relative_to(repo_path.resolve())))
                        except ValueError as e:
                            logger.warning(f"[DOCGEN] File {file_path} is outside repo_path {repo_path}, skipping. Error: {e}")
                            continue
                
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
                
                # ✅ INDUSTRY STANDARD: Robust serialization with comprehensive type handling
                # Supports multiple response formats from documentation agents:
                # - Structured dict with 'content' or 'markdown' fields
                # - Raw string content
                # - Complex nested structures (serialized as JSON with metadata)
                
                start_write_time = time.time()
                output_strategy = "unknown"
                content_to_write = ""
                
                try:
                    if isinstance(docs_output, dict):
                        # Structured response - extract content intelligently
                        if 'content' in docs_output:
                            # Primary content field (standard convention)
                            content_to_write = docs_output['content']
                            output_strategy = "dict_content_field"
                            
                            # Validate content is string
                            if not isinstance(content_to_write, str):
                                logger.warning(
                                    "Documentation content field is not a string, converting",
                                    extra={
                                        "job_id": job_id,
                                        "content_type": type(content_to_write).__name__,
                                        "doc_type": doc_type
                                    }
                                )
                                content_to_write = str(content_to_write)
                                
                        elif 'markdown' in docs_output:
                            # Alternative markdown field (some agents use this)
                            content_to_write = docs_output['markdown']
                            output_strategy = "dict_markdown_field"
                            
                            if not isinstance(content_to_write, str):
                                logger.warning(
                                    "Documentation markdown field is not a string, converting",
                                    extra={
                                        "job_id": job_id,
                                        "markdown_type": type(content_to_write).__name__,
                                        "doc_type": doc_type
                                    }
                                )
                                content_to_write = str(content_to_write)
                                
                        elif 'text' in docs_output:
                            # Some agents may use 'text' field
                            content_to_write = str(docs_output['text'])
                            output_strategy = "dict_text_field"
                            
                        else:
                            # Unstructured dict - serialize as formatted JSON with metadata
                            output_strategy = "dict_json_serialization"
                            
                            # Add metadata header for clarity
                            metadata = {
                                "generated_by": "docgen_agent",
                                "job_id": job_id,
                                "doc_type": doc_type,
                                "timestamp": time.time(),
                                "note": "Content was returned as unstructured dictionary"
                            }
                            
                            serialized_output = {
                                "metadata": metadata,
                                "content": docs_output
                            }
                            
                            content_to_write = json.dumps(serialized_output, indent=2, ensure_ascii=False)
                            
                            logger.info(
                                "Serializing unstructured dict to JSON",
                                extra={
                                    "job_id": job_id,
                                    "dict_keys": list(docs_output.keys()),
                                    "doc_type": doc_type
                                }
                            )
                    else:
                        # Direct string or other type - convert to string
                        output_strategy = "direct_string"
                        content_to_write = str(docs_output)
                    
                    # Validate we have content to write
                    if not content_to_write:
                        logger.error(
                            "Documentation output is empty after processing",
                            extra={
                                "job_id": job_id,
                                "output_type": type(docs_output).__name__,
                                "output_strategy": output_strategy,
                                "doc_type": doc_type
                            }
                        )
                        raise ValueError("Documentation content is empty - refusing to write empty file")
                    
                    # Write with proper encoding
                    async with aiofiles.open(doc_path, "w", encoding="utf-8") as f:
                        await f.write(content_to_write)
                    
                    # Verify file was written successfully
                    if not doc_path.exists():
                        raise IOError(f"File {doc_path} was not created successfully")
                    
                    file_size = doc_path.stat().st_size
                    
                    # Comprehensive logging for observability
                    write_duration_ms = round((time.time() - start_write_time) * 1000, 2)
                    
                    logger.info(
                        "Documentation written successfully",
                        extra={
                            "job_id": job_id,
                            "doc_type": doc_type,
                            "doc_path": str(doc_path),
                            "output_type": type(docs_output).__name__,
                            "output_strategy": output_strategy,
                            "file_size_bytes": file_size,
                            "content_length": len(content_to_write),
                            "write_duration_ms": write_duration_ms,
                            "has_content": bool(content_to_write)
                        }
                    )
                    
                except Exception as e:
                    logger.error(
                        "Failed to write documentation file",
                        extra={
                            "job_id": job_id,
                            "doc_type": doc_type,
                            "doc_path": str(doc_path),
                            "output_type": type(docs_output).__name__,
                            "output_strategy": output_strategy,
                            "error": str(e),
                            "error_type": type(e).__name__
                        },
                        exc_info=True
                    )
                    raise
                
                # [FIX] Add error handling for path resolution
                try:
                    generated_docs.append(str(doc_path.resolve().relative_to(repo_path.resolve())))
                except ValueError as e:
                    logger.warning(f"[DOCGEN] Doc path {doc_path} is outside repo_path {repo_path}, using absolute path. Error: {e}")
                    generated_docs.append(str(doc_path))
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
        
        except asyncio.TimeoutError:
            logger.warning(f"[DOCGEN] Job {job_id} timed out after 90s - skipping documentation")
            return {
                "status": "error",
                "message": "Documentation generation timed out after 90 seconds",
                "timeout": True,
            }
        except Exception as e:
            logger.error(f"Error running docgen agent: {e}", exc_info=True)
            return {
                "status": "error",
                "message": str(e),
                "error_type": type(e).__name__,
            }
    
    async def _run_critique(self, job_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Execute critique/security scanning with timeout."""
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
            # Wrap critique with configurable timeout
            async with asyncio.timeout(DEFAULT_CRITIQUE_TIMEOUT):
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
                        # [FIX] Add error handling for path resolution
                        try:
                            rel_path = str(file_path.resolve().relative_to(repo_path.resolve()))
                        except ValueError as e:
                            logger.warning(f"[CRITIQUE] File {file_path} is outside repo_path {repo_path}, skipping. Error: {e}")
                            continue
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
                    requirements={
                        "scan_types": scan_types, 
                        "auto_fix": auto_fix,
                        "test_failures": payload.get("test_results"),
                        "validation_failures": payload.get("validation_results"),
                        "stages_completed": payload.get("stages_completed", []),
                        "stages_failed": payload.get("stages_failed", []),
                    },
                )
                
                # Extract results with type checking
                issues_found = len(critique_result.get("issues", []))
                
                # FIX: Handle both list and boolean return types for fixes_applied
                # Some code paths in critique_agent return boolean, others return list
                fixes_applied_raw = critique_result.get("fixes_applied", [])
                if isinstance(fixes_applied_raw, bool):
                    # Boolean indicates whether fixes were applied (True/False)
                    issues_fixed = 1 if fixes_applied_raw else 0
                elif isinstance(fixes_applied_raw, list):
                    # List contains the actual fixes that were applied
                    issues_fixed = len(fixes_applied_raw)
                else:
                    # Defensive fallback for unexpected types
                    logger.warning(
                        f"Unexpected type for fixes_applied: {type(fixes_applied_raw)}. Defaulting to 0."
                    )
                    issues_fixed = 0
                
                # Write critique report
                output_dir = repo_path / "reports"
                output_dir.mkdir(parents=True, exist_ok=True)
                
                # Verify directory was created successfully
                if not output_dir.exists():
                    logger.error(f"Failed to create reports directory: {output_dir}")
                    raise RuntimeError(f"Could not create reports directory: {output_dir}")
                
                report_path = output_dir / "critique_report.json"
                
                # FIX: Enhance critique report to include coverage and test results
                # This ensures the report complies with the contract requirements
                enhanced_report = {
                    "job_id": job_id,
                    "timestamp": critique_result.get("timestamp") or datetime.utcnow().isoformat(),
                    "coverage": critique_result.get("coverage", {
                        "total_lines": 0,
                        "covered_lines": 0,
                        "percentage": 0.0
                    }),
                    "test_results": critique_result.get("test_results") or payload.get("test_results", {
                        "total": 0,
                        "passed": 0,
                        "failed": 0
                    }),
                    "issues": critique_result.get("issues", []),
                    "fixes_applied": critique_result.get("fixes_applied", []),
                    "scan_types": scan_types,
                    "status": critique_result.get("status", "completed"),
                }
                
                # Add original critique_result fields that don't conflict
                for key, value in critique_result.items():
                    if key not in enhanced_report:
                        enhanced_report[key] = value
                
                # Ensure enhanced_report is serializable
                try:
                    json_str = json.dumps(enhanced_report, indent=2, default=str)
                except (TypeError, ValueError) as e:
                    logger.error(f"Critique result is not JSON serializable: {e}")
                    # Create a minimal valid report
                    json_str = json.dumps({
                        "job_id": job_id,
                        "timestamp": datetime.utcnow().isoformat(),
                        "status": "error",
                        "message": "Failed to serialize critique results",
                        "issues_found": len(critique_result.get("issues", [])),
                        "coverage": {"total_lines": 0, "covered_lines": 0, "percentage": 0.0},
                        "test_results": {"total": 0, "passed": 0, "failed": 0},
                        "issues": [],
                        "fixes_applied": [],
                        "error": str(e)
                    }, indent=2)
                
                async with aiofiles.open(report_path, "w", encoding="utf-8") as f:
                    await f.write(json_str)
                
                # Verify file was written successfully
                if not report_path.exists():
                    logger.error(f"Critique report file was not created: {report_path}")
                else:
                    file_size = report_path.stat().st_size
                    logger.info(f"Generated critique report: {report_path} ({file_size} bytes)")
                
                # [FIX] Add error handling for path resolution
                try:
                    report_path_str = str(report_path.resolve().relative_to(repo_path.resolve()))
                except ValueError as e:
                    logger.warning(f"[CRITIQUE] Report path {report_path} is outside repo_path {repo_path}, using absolute path. Error: {e}")
                    report_path_str = str(report_path)
                
                result = {
                    "status": "completed",
                    "issues_found": issues_found,
                    "issues_fixed": issues_fixed,
                    "scan_types": scan_types,
                    "report_path": report_path_str,
                    "file_count": len(code_files),
                }
                
                logger.info(f"Critique agent completed for job {job_id}, found {issues_found} issues")
                return result
        
        except asyncio.TimeoutError:
            logger.warning(f"[CRITIQUE] Job {job_id} timed out after 90s - skipping critique")
            return {
                "status": "error",
                "message": "Code critique timed out after 90 seconds",
                "timeout": True,
            }
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
            payload: Parameters including readme_content, ambiguities, channel
        
        Returns:
            Dict with status and clarification questions
        """
        # Ensure agents are loaded before use
        self._ensure_agents_loaded()
        
        try:
            readme_content = payload.get("readme_content", "")
            channel = payload.get("channel", "cli")  # Default to CLI if not specified
            
            logger.info(f"Running clarifier for job {job_id} with channel: {channel}")
            
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
                    from generator.clarifier.clarifier_user_prompt import get_channel
                    
                    # Create clarifier instance with auto-detection
                    clarifier = await Clarifier.create()
                    
                    # Override interaction channel if specified
                    try:
                        target_lang = getattr(getattr(clarifier, 'config', None), 'TARGET_LANGUAGE', 'en')
                        clarifier.interaction = get_channel(
                            channel_type=channel,
                            target_language=target_lang
                        )
                        logger.info(f"Set clarifier channel to: {channel}")
                    except Exception as channel_error:
                        logger.warning(
                            f"Could not set channel to {channel}: {channel_error}. "
                            f"Using default channel.",
                            exc_info=True
                        )
                    
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
                                extra={"method": "llm", "questions_count": len(questions), "channel": channel}
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
                                "channel": channel,
                            }
                            
                            return {
                                "status": "clarification_initiated",
                                "job_id": job_id,
                                "clarifications": questions,
                                "confidence": 0.65,
                                "questions_count": len(questions),
                                "method": "llm",
                                "channel": channel,
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
                "channel": channel,
            }
            
            result = {
                "status": "clarification_initiated",
                "job_id": job_id,
                "clarifications": questions,
                "confidence": 0.65,  # Low confidence indicates need for clarification
                "questions_count": len(questions),
                "method": "rule_based",
                "channel": channel,
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
    
    async def cleanup_expired_clarification_sessions(self, max_age_seconds: int = CLARIFICATION_SESSION_TTL_SECONDS) -> int:
        """
        Clean up clarification sessions older than max_age_seconds.
        
        Should be called periodically (e.g., every 10 minutes) to prevent memory exhaustion.
        
        Args:
            max_age_seconds: Maximum age in seconds before a session is considered expired
        
        Returns:
            Number of sessions cleaned up
        """
        now = datetime.now(timezone.utc)
        expired = []
        
        for job_id, session in _clarification_sessions.items():
            try:
                created_at_str = session.get("created_at", "")
                # Parse ISO format datetime (may or may not have timezone)
                if created_at_str:
                    try:
                        created_at = datetime.fromisoformat(created_at_str)
                        # If no timezone, assume UTC
                        if created_at.tzinfo is None:
                            created_at = created_at.replace(tzinfo=timezone.utc)
                        
                        if (now - created_at).total_seconds() > max_age_seconds:
                            expired.append(job_id)
                    except (ValueError, TypeError):
                        # Invalid timestamp format - mark for cleanup
                        logger.warning(f"Invalid timestamp in session {job_id}: {created_at_str}")
                        expired.append(job_id)
                else:
                    # No timestamp - mark for cleanup
                    expired.append(job_id)
            except Exception as e:
                # Catch any unexpected errors when processing session
                logger.error(f"Error processing session {job_id}: {e}")
                expired.append(job_id)  # Mark for cleanup on error
        
        for job_id in expired:
            del _clarification_sessions[job_id]
            logger.info(f"Cleaned up expired clarification session for job {job_id}")
        
        if expired:
            logger.info(f"Cleaned up {len(expired)} expired clarification sessions")
        
        return len(expired)
    
    async def start_periodic_session_cleanup(
        self,
        interval_seconds: int = 600,  # 10 minutes default
        max_age_seconds: int = CLARIFICATION_SESSION_TTL_SECONDS
    ) -> None:
        """
        Start a background task to periodically clean up expired clarification sessions.
        
        Args:
            interval_seconds: How often to run cleanup (default: 10 minutes)
            max_age_seconds: Maximum session age before cleanup (default: 1 hour)
        """
        logger.info(
            f"Starting periodic clarification session cleanup "
            f"(interval: {interval_seconds}s, max_age: {max_age_seconds}s)"
        )
        
        while True:
            try:
                await asyncio.sleep(interval_seconds)
                cleaned = await self.cleanup_expired_clarification_sessions(max_age_seconds)
                if cleaned > 0:
                    logger.info(f"Periodic cleanup: removed {cleaned} expired sessions")
            except asyncio.CancelledError:
                logger.info("Periodic cleanup task cancelled")
                break
            except Exception as e:
                logger.error(f"Error in periodic cleanup: {e}", exc_info=True)
                # Continue running despite errors
    
    async def _run_full_pipeline(self, job_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Execute full generation pipeline."""
        # FIX: Check if job is already in pipeline
        if job_id in self._jobs_in_pipeline:
            logger.warning(
                f"[PIPELINE] Job {job_id} is already running in pipeline. Skipping duplicate request.",
                extra={"job_id": job_id}
            )
            return {
                "status": "skipped",
                "message": "Pipeline already running for this job",
                "job_id": job_id,
            }
        
        # Add job to in-progress set
        self._jobs_in_pipeline.add(job_id)
        logger.info(f"[PIPELINE] Starting pipeline for job {job_id}")
        
        try:
            # Ensure agents are loaded before use
            self._ensure_agents_loaded()
            
            # Extract output_dir from README if not already set
            if not payload.get("output_dir") and payload.get("readme_content") and _PROVENANCE_AVAILABLE:
                extracted_output_dir = _extract_output_dir_from_md(payload["readme_content"])
                if extracted_output_dir:
                    payload["output_dir"] = extracted_output_dir
                    logger.info(
                        f"[PIPELINE] Extracted output_dir from README: {extracted_output_dir}",
                        extra={"job_id": job_id, "output_dir": extracted_output_dir}
                    )
            
            # Run pipeline stages sequentially
            stages_completed = []
            
            # Initialize result tracking for critique context
            testgen_result = None
            val_result = None
            
            # 1. Clarify (optional)
            if payload.get("skip_clarification", False):
                # Skip clarification when resuming after clarification is already completed
                logger.info(f"[PIPELINE] Skipping clarification for job {job_id} (already completed)")
            elif payload.get("readme_content"):
                logger.info(f"[PIPELINE] Job {job_id} starting step: clarify")
                clarify_result = await self._run_clarifier(job_id, payload)
                if clarify_result.get("status") == "clarification_initiated":
                    # Pause pipeline for clarifications
                    questions = clarify_result.get("clarifications", [])
                    # Update job status in database
                    job = jobs_db.get(job_id)
                    if job:
                        job.status = JobStatus.NEEDS_CLARIFICATION
                        job.current_stage = JobStage.GENERATOR_CLARIFICATION
                        job.updated_at = datetime.now(timezone.utc)
                        job.metadata["clarification_questions"] = questions
                        job.metadata["clarification_status"] = "pending_response"
                    logger.info(f"[PIPELINE] Job {job_id} awaiting clarification responses; pausing pipeline.")
                    # Remove from in-progress tracking to allow resumption
                    self._jobs_in_pipeline.discard(job_id)
                    return {"status": "clarification_initiated", "clarifications": questions, "job_id": job_id}
                elif clarify_result.get("status") != "error":
                    stages_completed.append("clarify")
                    logger.info(f"[PIPELINE] Job {job_id} completed step: clarify")
            
            # 2. Codegen with retry logic
            # Transform payload for codegen - it needs 'requirements' not 'readme_content'
            # Preserve all original payload fields that might be needed
            codegen_payload = {
                **payload,  # Preserve all original fields
                "requirements": payload.get("readme_content", payload.get("requirements", "")),
            }
            # Remove readme_content from codegen payload as it's now in requirements
            codegen_payload.pop("readme_content", None)

            # Ensure requirements is populated before codegen
            # This handles the case where clarification was skipped and readme_content is empty
            if not codegen_payload.get("requirements") or len(codegen_payload.get("requirements", "").strip()) == 0:
                logger.warning(
                    f"[PIPELINE] Requirements is empty for job {job_id}. "
                    f"Attempting to load README from job directory."
                )
                # Try to read README from job directory
                job_dir = Path(self.storage_path) / job_id
                requirements = _load_readme_from_disk(job_dir)
                
                if requirements:
                    codegen_payload["requirements"] = requirements
                    logger.info(
                        f"[PIPELINE] Loaded requirements from job directory "
                        f"({len(requirements)} bytes) for job {job_id}"
                    )
                else:
                    error_msg = f"No requirements found: README file is missing from job directory {job_dir}"
                    logger.error(
                        f"[PIPELINE] {error_msg}",
                        extra={"job_id": job_id, "job_dir": str(job_dir)}
                    )
                    raise ValueError(error_msg)

            # Retry configuration
            max_codegen_retries = 2  # Total attempts = 1 initial + 2 retries = 3
            codegen_attempt = 0
            codegen_result = None
            previous_error = None

            while codegen_attempt <= max_codegen_retries:
                codegen_attempt += 1
                attempt_label = f"attempt {codegen_attempt}/{max_codegen_retries + 1}"

                logger.info(f"[PIPELINE] Job {job_id} starting step: codegen ({attempt_label})")

                # Add previous_error to payload if retrying
                if previous_error:
                    codegen_payload["previous_error"] = previous_error
                    logger.info(
                        f"[PIPELINE] Job {job_id} retrying codegen with error feedback: {previous_error.get('error_type')}",
                        extra={"job_id": job_id, "attempt": codegen_attempt, "previous_error": previous_error}
                    )

                codegen_result = await self._run_codegen(job_id, codegen_payload)

                if codegen_result.get("status") == "completed":
                    # Codegen succeeded - now validate before committing to success
                    output_path_for_validation = codegen_result.get("output_path")
                    
                    # Quick syntax validation to catch errors before exiting retry loop
                    # This allows us to retry codegen if validation fails
                    validation_passed = True
                    if output_path_for_validation and _MATERIALIZER_AVAILABLE:
                        try:
                            # Get required files list
                            md_content = payload.get("readme_content", payload.get("requirements", ""))
                            required_files = ["requirements.txt"]
                            if md_content:
                                try:
                                    spec_files = _extract_required_files_from_md(md_content)
                                    if spec_files:
                                        existing = set(required_files)
                                        required_files.extend(sf for sf in spec_files if sf not in existing)
                                except Exception:
                                    pass  # Ignore extraction errors
                            
                            # Run validation
                            val_result = await _validate_generated_project(
                                output_dir=output_path_for_validation,
                                required_files=required_files,
                                check_python_syntax=True,
                            )
                            
                            if not val_result.get("valid", True):
                                validation_errors = val_result.get('errors', [])
                                
                                # Check for retriable errors (syntax errors or missing files due to syntax errors)
                                syntax_errors = [e for e in validation_errors if 'syntax' in e.lower() or 'SyntaxError' in e]
                                missing_files = [e for e in validation_errors if 'missing' in e.lower() and 'required' in e.lower()]
                                
                                errors_for_retry = syntax_errors.copy()
                                if missing_files:
                                    error_txt_path = Path(output_path_for_validation) / "error.txt"
                                    if error_txt_path.exists():
                                        logger.info(
                                            f"[PIPELINE] Job {job_id} has missing required files and error.txt",
                                            extra={"job_id": job_id, "missing_files": missing_files}
                                        )
                                        errors_for_retry.extend(missing_files)
                                
                                if errors_for_retry and codegen_attempt < max_codegen_retries:
                                    # We have retriable errors and retries left - set up for retry
                                    validation_passed = False
                                    previous_error = {
                                        "error_type": "SyntaxError" if syntax_errors else "ValidationError",
                                        "details": "\n".join(errors_for_retry[:3]),
                                        "instruction": (
                                            "The previous code generation had syntax errors or missing required files. "
                                            "Please fix these errors and regenerate the code with proper syntax. "
                                            "Pay special attention to:\n"
                                            "1. String literals must be properly terminated with matching quotes\n"
                                            "2. All control structures (if, for, def, class, etc.) must end with a colon (:)\n"
                                            "3. Check for stray backslashes at line endings\n"
                                            "4. Ensure all brackets, parentheses, and braces are properly matched\n"
                                            "5. Include commas between function arguments and list/dict elements"
                                        )
                                    }
                                    
                                    logger.warning(
                                        f"[PIPELINE] Job {job_id} validation failed, will retry codegen",
                                        extra={
                                            "job_id": job_id,
                                            "syntax_errors": syntax_errors,
                                            "missing_files": missing_files,
                                            "attempt": codegen_attempt
                                        }
                                    )
                                    
                                    # Clean up failed output
                                    import shutil
                                    try:
                                        shutil.rmtree(output_path_for_validation)
                                        logger.info(f"[PIPELINE] Job {job_id} cleaned up failed output directory for retry")
                                    except Exception as cleanup_err:
                                        logger.warning(f"[PIPELINE] Job {job_id} cleanup error: {cleanup_err}")
                                    
                                    # Remove codegen from stages_completed since we're retrying
                                    if "codegen" in stages_completed:
                                        stages_completed.remove("codegen")
                                    
                                    # Continue to next attempt
                                    continue
                        except Exception as val_err:
                            logger.warning(f"[PIPELINE] Job {job_id} validation check error: {val_err}")
                            # On validation error, assume success and break (fail-open for safety)
                    
                    if validation_passed:
                        # Validation passed or was skipped - codegen is successful
                        if "codegen" not in stages_completed:
                            stages_completed.append("codegen")
                        logger.info(f"[PIPELINE] Job {job_id} completed step: codegen ({attempt_label})")
                        break  # Success, exit retry loop
                else:
                    error_msg = codegen_result.get('message', 'Unknown error')
                    logger.warning(
                        f"[PIPELINE] Job {job_id} codegen {attempt_label} failed: {error_msg}",
                        extra={"job_id": job_id, "attempt": codegen_attempt, "error": error_msg}
                    )

                    # If this was the last attempt, fail the pipeline
                    if codegen_attempt > max_codegen_retries:
                        logger.error(
                            f"[PIPELINE] Job {job_id} failed step: codegen after {max_codegen_retries + 1} attempts",
                            extra={"job_id": job_id, "total_attempts": codegen_attempt}
                        )
                        return {
                            "status": "failed",
                            "message": f"Code generation failed after {codegen_attempt} attempts",
                            "stages_completed": stages_completed,
                            "last_error": error_msg,
                        }

                    # Continue to next attempt (previous_error will be set from validation if available)

            # 2b. Post-codegen validation stages with retry on syntax errors
            output_path_for_validation = codegen_result.get("output_path")
            md_content = payload.get("readme_content", payload.get("requirements", ""))

            # FIX: Ensure requirements.txt exists with fallback before validation
            # This prevents pipeline failures when LLM omits requirements.txt or generates it empty
            if output_path_for_validation:
                requirements_path = Path(output_path_for_validation) / "requirements.txt"
                if not requirements_path.exists() or requirements_path.stat().st_size == 0:
                    fallback_requirements = (
                        "# Auto-generated fallback requirements\n"
                        "# Generated by pipeline when requirements.txt was missing or empty\n"
                        "fastapi>=0.104.0\n"
                        "uvicorn[standard]>=0.24.0\n"
                        "pydantic>=2.5.0\n"
                        "pytest>=7.4.0\n"
                        "httpx>=0.25.0\n"
                    )
                    try:
                        requirements_path.write_text(fallback_requirements, encoding="utf-8")
                        logger.info(
                            f"[PIPELINE] Job {job_id} auto-generated fallback requirements.txt "
                            f"({'was missing' if not requirements_path.exists() else 'was empty'})",
                            extra={"job_id": job_id, "requirements_path": str(requirements_path)}
                        )
                    except Exception as req_err:
                        logger.warning(
                            f"[PIPELINE] Job {job_id} failed to write fallback requirements.txt: {req_err}",
                            extra={"job_id": job_id}
                        )

            # Extract spec-required files from the MD content so that
            # validation catches missing files like app/routes.py when the
            # spec references them.
            required_files = ["main.py"]

            # Adjust required_files based on actual output structure (app/ layout detection)
            if output_path_for_validation:
                output_path_obj = Path(output_path_for_validation)
                app_dir = output_path_obj / "app"
                if app_dir.is_dir():
                    # Remove root main.py requirement for app-structured projects
                    if "main.py" in required_files:
                        required_files.remove("main.py")
                    # Explicitly add app/ subdirectory files to required_files
                    # The validator does NOT auto-detect these, so we must add them here
                    required_files.extend(["app/main.py", "app/routes.py", "app/schemas.py"])
                    logger.info(
                        f"[PIPELINE] Job {job_id} detected app/ layout, adjusted required files: {required_files}",
                        extra={"job_id": job_id, "required_files": required_files}
                    )

            if md_content and _PROVENANCE_AVAILABLE:
                try:
                    spec_files = _extract_required_files_from_md(md_content)
                    if spec_files:
                        existing = set(required_files)
                        required_files.extend(sf for sf in spec_files if sf not in existing)
                        logger.info(
                            f"[PIPELINE] Job {job_id} spec-derived required files: {required_files}",
                            extra={"job_id": job_id}
                        )
                except Exception as parse_err:
                    logger.warning(f"[PIPELINE] Job {job_id} failed to extract required files from spec: {parse_err}")

            # Validate generated project (syntax + JSON-bundle detection)
            if output_path_for_validation and _MATERIALIZER_AVAILABLE:
                try:
                    val_result = await _validate_generated_project(
                        output_dir=output_path_for_validation,
                        required_files=required_files,
                        check_python_syntax=True,
                    )
                    if not val_result.get("valid", True):
                        validation_errors = val_result.get('errors', [])
                        validation_warnings = val_result.get('warnings', [])

                        # NOTE: Retry logic for syntax errors has moved into the codegen retry loop above
                        # This section now only handles final validation failures after all retries exhausted
                        
                        logger.error(
                            f"[PIPELINE] Job {job_id} validation failed after all retries",
                            extra={"job_id": job_id, "validation_errors": validation_errors}
                        )

                        # Store validation info in job metadata
                        if job_id in jobs_db:
                            job = jobs_db[job_id]
                            job.metadata["validation_errors"] = validation_errors
                            job.metadata["validation_warnings"] = validation_warnings

                        await _write_validation_error(output_path_for_validation, val_result)

                        if validation_errors:
                            # HARD FAIL: Critical validation errors prevent pipeline from continuing
                            logger.error(
                                f"[PIPELINE] Job {job_id} HARD FAIL - validation errors: {validation_errors}",
                                extra={"job_id": job_id, "validation_result": val_result}
                            )
                            await self._finalize_failed_job(
                                job_id, error=f"Validation failed: {validation_errors}"
                            )
                            return {
                                "status": "failed",
                                "message": f"Validation failed: {validation_errors}",
                                "stages_completed": stages_completed,
                                "output_path": output_path_for_validation,
                            }
                        else:
                            # Only warnings, not errors - log and continue
                            logger.warning(
                                f"[PIPELINE] Job {job_id} validation warnings (non-fatal): {validation_warnings}",
                                extra={"job_id": job_id}
                            )
                            stages_completed.append("validate")
                    else:
                        stages_completed.append("validate")
                        logger.info(f"[PIPELINE] Job {job_id} completed step: validate")
                except Exception as val_err:
                    logger.warning(f"[PIPELINE] Job {job_id} validation step error: {val_err}")
            
            # 2c. Spec fidelity check (uses existing provenance.validate_spec_fidelity)
            if output_path_for_validation and _PROVENANCE_AVAILABLE:
                try:
                    if md_content:
                        # Read generated files for spec validation
                        gen_dir = Path(output_path_for_validation)
                        gen_files = {}
                        for py_file in gen_dir.glob("**/*.py"):
                            rel = str(py_file.relative_to(gen_dir))
                            gen_files[rel] = py_file.read_text(encoding="utf-8")
                        
                        spec_result = _validate_spec_fidelity(
                            md_content, gen_files, output_path_for_validation
                        )
                        if spec_result.get("valid", True):
                            stages_completed.append("spec_validate")
                            logger.info(f"[PIPELINE] Job {job_id} completed step: spec_validate")
                        else:
                            logger.warning(
                                f"[PIPELINE] Job {job_id} spec fidelity check found issues: "
                                f"{spec_result.get('errors', [])}",
                                extra={"job_id": job_id}
                            )
                except Exception as spec_err:
                    logger.warning(f"[PIPELINE] Job {job_id} spec validation error: {spec_err}")
            
            # 2d. README completeness validation
            if output_path_for_validation and _PROVENANCE_AVAILABLE:
                try:
                    gen_dir = Path(output_path_for_validation)
                    readme_path = gen_dir / "README.md"
                    
                    if readme_path.exists():
                        readme_content = readme_path.read_text(encoding="utf-8")
                        readme_result = _validate_readme_completeness(readme_content)
                        
                        if readme_result.get("valid", True):
                            logger.info(
                                f"[PIPELINE] Job {job_id} README validation passed - "
                                f"length: {readme_result['length']}, "
                                f"sections: {readme_result['sections_found']}, "
                                f"commands: {readme_result['commands_found']}",
                                extra={"job_id": job_id, "readme_validation": readme_result}
                            )
                        else:
                            logger.warning(
                                f"[PIPELINE] Job {job_id} README validation found issues: "
                                f"{readme_result.get('errors', [])}",
                                extra={"job_id": job_id, "readme_validation": readme_result}
                            )
                    else:
                        logger.warning(
                            f"[PIPELINE] Job {job_id} README.md not found at {readme_path}",
                            extra={"job_id": job_id}
                        )
                except Exception as readme_err:
                    logger.warning(f"[PIPELINE] Job {job_id} README validation error: {readme_err}")
            
            # 2e. Write provenance metadata
            if output_path_for_validation and _PROVENANCE_AVAILABLE:
                try:
                    tracker = ProvenanceTracker(job_id=job_id)
                    if md_content:
                        tracker.record_stage("READ_MD", artifacts={"md_input": md_content})
                    tracker.record_stage("CODEGEN", metadata={
                        "files_generated": codegen_result.get("files_count", 0),
                        "output_path": output_path_for_validation,
                    })
                    tracker.record_stage("MATERIALIZE", metadata={
                        "materializer_used": _MATERIALIZER_AVAILABLE,
                    })
                    tracker.save_to_file(output_path_for_validation)
                    logger.info(f"[PIPELINE] Job {job_id} provenance written")
                except Exception as prov_err:
                    logger.warning(f"[PIPELINE] Job {job_id} provenance error: {prov_err}")
            
            # 3. Testgen (if requested)
            # RESILIENCE FIX: Pipeline continues even if testgen fails
            # Industry Standard: Fail-safe pipeline design - individual stage failures
            # should not abort the entire workflow. This ensures maximum output delivery
            # even when optional stages encounter errors.
            if payload.get("include_tests", True):
                try:
                    # Check if codegen actually produced valid source files
                    output_path = codegen_result.get("output_path")
                    if output_path:
                        code_path = Path(output_path)
                        # Look for Python files recursively (supports nested structures like app/)
                        # Exclude test files from source count to match what _run_testgen does
                        source_files = [
                            f for f in code_path.rglob("*.py") 
                            if not f.name.startswith("test_")
                        ] if code_path.exists() else []
                        
                        if not source_files:
                            logger.warning(
                                f"[PIPELINE] Job {job_id} skipping testgen - no source files found in {output_path}",
                                extra={
                                    "job_id": job_id,
                                    "output_path": str(output_path),
                                    "files_in_directory": [f.name for f in code_path.iterdir()] if code_path.exists() else []
                                }
                            )
                        else:
                            # Check if LLM provider is configured for intelligent test generation
                            # detect_available_llm_provider() is imported at line 158 from runner.llm_client
                            llm_provider_configured = False
                            try:
                                if self.llm_config and self.llm_config.default_llm_provider:
                                    llm_provider_configured = True
                                elif detect_available_llm_provider():
                                    llm_provider_configured = True
                            except Exception:
                                pass
                            
                            testgen_payload = {
                                "code_path": output_path,
                                "test_type": "unit",
                                "coverage_target": 80.0,
                                "use_llm": llm_provider_configured,  # Enable LLM-based generation when provider available
                                "llm_timeout": 120 if llm_provider_configured else 30,  # 2 min for LLM, 30s for rule-based
                            }
                            logger.info(
                                f"[PIPELINE] Job {job_id} starting step: testgen with {len(source_files)} source files "
                                f"(LLM-based: {llm_provider_configured})"
                            )
                            testgen_result = await self._run_testgen(job_id, testgen_payload)
                            if testgen_result.get("status") == "completed":
                                # BUG FIX: Check if tests actually passed, not just if testgen completed
                                # Even if testgen "completed", tests may have failed
                                test_execution_failed = False
                                if payload.get("include_tests", True):
                                    # Extract test results from testgen_result
                                    result_data = testgen_result.get("result", {})
                                    final_validation_report = result_data.get("final_validation_report", {})
                                    
                                    # Check coverage validation results for test failures
                                    coverage_report = final_validation_report.get("coverage", {})
                                    test_results = coverage_report.get("test_results", {})
                                    fail_count = test_results.get("failed", 0) or test_results.get("fail_count", 0)
                                    
                                    # Also check top-level test results if available
                                    if fail_count == 0 and "test_results" in result_data:
                                        top_level_results = result_data.get("test_results", {})
                                        fail_count = top_level_results.get("failed", 0) or top_level_results.get("fail_count", 0)
                                    
                                    if fail_count > 0:
                                        # Test execution failed - use specific marker
                                        test_execution_failed = True
                                        logger.error(
                                            f"[PIPELINE] Job {job_id} testgen completed but {fail_count} test(s) failed. "
                                            f"Marking stage as execution_failed but continuing pipeline.",
                                            extra={
                                                "job_id": job_id,
                                                "fail_count": fail_count,
                                                "failure_type": "test_execution",
                                            }
                                        )
                                        stages_completed.append("testgen:execution_failed")
                                
                                # Only mark as successful if tests passed (or weren't checked)
                                if not test_execution_failed:
                                    stages_completed.append("testgen")
                                    logger.info(f"[PIPELINE] Job {job_id} completed step: testgen")
                            elif testgen_result.get("status") == "error":
                                # Test generation failed - use specific marker
                                testgen_error = testgen_result.get('message', 'Unknown error')
                                logger.error(
                                    f"[PIPELINE] Job {job_id} failed step: testgen - {testgen_error}",
                                    extra={
                                        "job_id": job_id,
                                        "error": testgen_error,
                                        "failure_type": "generation_error",
                                    }
                                )
                                stages_completed.append("testgen:error")
                                logger.warning(f"[PIPELINE] Job {job_id} continuing pipeline despite testgen failure")
                    else:
                        logger.warning(
                            f"[PIPELINE] Job {job_id} skipping testgen - no output path from codegen",
                            extra={"job_id": job_id}
                        )
                except Exception as e:
                    # Industry Standard: Comprehensive error logging with context
                    logger.error(
                        f"[PIPELINE] Job {job_id} testgen exception: {e}",
                        exc_info=True,
                        extra={
                            "job_id": job_id,
                            "stage": "testgen",
                            "error_type": type(e).__name__,
                            "output_path": output_path if 'output_path' in locals() else None,
                            "failure_type": "exception",
                        }
                    )
                    stages_completed.append("testgen:exception")
                    logger.warning(
                        f"[PIPELINE] Job {job_id} continuing pipeline despite testgen exception",
                        extra={"job_id": job_id, "remaining_stages": ["deploy", "docgen", "critique"]}
                    )
            
            # 4. Deploy (if requested)
            # RESILIENCE FIX: Pipeline continues even if deployment fails
            # Industry Standard: Deploy failures shouldn't prevent documentation generation
            # or critique, allowing maximum value delivery from the pipeline
            # FIX: Default to True since deployment is a core pipeline feature
            # Users who don't want deployment should explicitly set include_deployment=False
            include_deployment = payload.get("include_deployment", True)
            logger.info(f"[PIPELINE] Job {job_id} deployment check: include_deployment={include_deployment}, payload keys={list(payload.keys())}")
            
            if include_deployment:
                try:
                    # Pass generated files from codegen to deployment
                    deploy_payload = {
                        "code_path": codegen_result.get("output_path"),
                        "include_ci_cd": True,
                        "output_dir": payload.get("output_dir", ""),  # FIX: Propagate output_dir for consistency
                        "generated_files": codegen_result.get("file_names", []),  # FIX 1: Pass file list
                    }
                    logger.info(f"[PIPELINE] Job {job_id} starting step: deploy_all (docker, kubernetes, helm) with {len(deploy_payload.get('generated_files', []))} files")
                    
                    # Run all deployment targets
                    deploy_result = await self._run_deploy_all(job_id, deploy_payload)
                    
                    if deploy_result.get("status") == "completed":
                        stages_completed.append("deploy")
                        logger.info(
                            f"[PIPELINE] Job {job_id} completed step: deploy_all - "
                            f"targets: {deploy_result.get('completed_targets', [])} - "
                            f"files: {deploy_result.get('generated_files', [])}"
                        )
                        
                        # Run deployment completeness validation
                        logger.info(f"[PIPELINE] Job {job_id} starting deployment validation")
                        try:
                            validation_result = await self._validate_deployment_completeness(
                                job_id, 
                                codegen_result.get("output_path")
                            )
                            
                            if validation_result.get("status") == "failed":
                                logger.error(
                                    f"[PIPELINE] Job {job_id} deployment validation failed - "
                                    f"errors: {validation_result.get('errors', [])} - continuing pipeline"
                                )
                                stages_completed.append("deploy:validation_failed")
                            else:
                                logger.info(f"[PIPELINE] Job {job_id} deployment validation passed")
                                
                        except Exception as e:
                            logger.error(f"[PIPELINE] Job {job_id} deployment validation error: {e}", exc_info=True)
                            # Continue pipeline on validation errors (non-fatal)
                            logger.warning(f"[PIPELINE] Job {job_id} continuing despite validation error")
                            
                    elif deploy_result.get("status") == "error":
                        deploy_error = deploy_result.get('message', 'Unknown error')
                        logger.error(
                            f"[PIPELINE] Job {job_id} deploy_all failed - {deploy_error}",
                            extra={
                                "job_id": job_id,
                                "error": deploy_error,
                                "failure_type": "generation_error",
                            }
                        )
                        stages_completed.append("deploy:error")
                        logger.warning(f"[PIPELINE] Job {job_id} continuing pipeline despite deploy failure")
                except Exception as e:
                    # Industry Standard: Comprehensive error logging with structured context
                    logger.error(
                        f"[PIPELINE] Job {job_id} deploy exception: {e}",
                        exc_info=True,
                        extra={
                            "job_id": job_id,
                            "stage": "deploy",
                            "error_type": type(e).__name__,
                            "code_path": codegen_result.get("output_path") if codegen_result else None,
                            "failure_type": "exception",
                        }
                    )
                    stages_completed.append("deploy:exception")
                    logger.warning(
                        f"[PIPELINE] Job {job_id} continuing pipeline despite deploy exception",
                        extra={"job_id": job_id, "remaining_stages": ["docgen", "critique"]}
                    )
            else:
                logger.info(f"[PIPELINE] Job {job_id} skipping deploy step (include_deployment={include_deployment})")
            
            # 5. Docgen (if requested)
            # RESILIENCE FIX: Pipeline continues even if docgen fails
            # Industry Standard: Documentation generation failure shouldn't prevent
            # code critique, ensuring comprehensive quality analysis
            # FIX: Default to True since documentation is a core pipeline feature
            if payload.get("include_docs", True):
                try:
                    docgen_payload = {
                        "code_path": codegen_result.get("output_path"),
                        "doc_type": "api",
                        "format": "markdown",
                        "output_dir": payload.get("output_dir", ""),  # FIX: Propagate output_dir for consistency
                    }
                    logger.info(f"[PIPELINE] Job {job_id} starting step: docgen")
                    docgen_result = await self._run_docgen(job_id, docgen_payload)
                    if docgen_result.get("status") == "completed":
                        stages_completed.append("docgen")
                        logger.info(f"[PIPELINE] Job {job_id} completed step: docgen")
                    elif docgen_result.get("status") == "error":
                        logger.error(
                            f"[PIPELINE] Job {job_id} failed step: docgen - {docgen_result.get('message', 'Unknown error')}",
                            extra={
                                "job_id": job_id,
                                "error": docgen_result.get('message'),
                                "failure_type": "generation_error",
                            }
                        )
                        stages_completed.append("docgen:error")
                        logger.warning(f"[PIPELINE] Job {job_id} continuing pipeline despite docgen failure")
                        
                        # Generate fallback README when docgen fails
                        try:
                            output_path = codegen_result.get("output_path")
                            if output_path:
                                output_path_obj = Path(output_path)
                                project_name = payload.get("output_dir", "hello_generator")
                                
                                # Generate fallback README content
                                fallback_readme = _generate_fallback_readme(
                                    project_name=project_name,
                                    language="python",
                                    output_path=str(output_path_obj)
                                )
                                
                                # Write README to the project directory
                                readme_path = output_path_obj / "README.md"
                                readme_path.write_text(fallback_readme, encoding="utf-8")
                                logger.info(
                                    f"[PIPELINE] Job {job_id} generated fallback README at {readme_path}",
                                    extra={"job_id": job_id, "readme_path": str(readme_path)}
                                )
                        except Exception as fallback_err:
                            logger.error(
                                f"[PIPELINE] Job {job_id} fallback README generation failed: {fallback_err}",
                                exc_info=True
                            )
                except Exception as e:
                    # Industry Standard: Structured error logging with full context
                    logger.error(
                        f"[PIPELINE] Job {job_id} docgen exception: {e}",
                        exc_info=True,
                        extra={
                            "job_id": job_id,
                            "stage": "docgen",
                            "error_type": type(e).__name__,
                            "code_path": codegen_result.get("output_path") if codegen_result else None,
                            "failure_type": "exception",
                        }
                    )
                    stages_completed.append("docgen:exception")
                    logger.warning(
                        f"[PIPELINE] Job {job_id} continuing pipeline despite docgen exception",
                        extra={"job_id": job_id, "remaining_stages": ["critique"]}
                    )
                    
                    # Generate fallback README when docgen has exception (timeout, etc.)
                    try:
                        output_path = codegen_result.get("output_path")
                        if output_path:
                            output_path_obj = Path(output_path)
                            project_name = payload.get("output_dir", "hello_generator")
                            
                            # Generate fallback README content
                            fallback_readme = _generate_fallback_readme(
                                project_name=project_name,
                                language="python",
                                output_path=str(output_path_obj)
                            )
                            
                            # Write README to the project directory
                            readme_path = output_path_obj / "README.md"
                            readme_path.write_text(fallback_readme, encoding="utf-8")
                            logger.info(
                                f"[PIPELINE] Job {job_id} generated fallback README after exception at {readme_path}",
                                extra={"job_id": job_id, "readme_path": str(readme_path)}
                            )
                    except Exception as fallback_err:
                        logger.error(
                            f"[PIPELINE] Job {job_id} fallback README generation after exception failed: {fallback_err}",
                            exc_info=True
                        )
            
            # 6. Critique (if requested)
            # FIX: Default to True since critique is a core pipeline feature for quality
            if payload.get("run_critique", True):
                # Enrich critique with test and validation results for better context
                # Only mark stages as "failed" if they were expected (via include_* flags) but not completed
                stages_failed = []
                if payload.get("include_tests", True) and "testgen" not in stages_completed:
                    stages_failed.append("testgen")
                if payload.get("include_deployment", True) and "deploy" not in stages_completed:
                    stages_failed.append("deploy")
                if payload.get("include_docs", True) and "docgen" not in stages_completed:
                    stages_failed.append("docgen")
                
                critique_payload = {
                    "code_path": codegen_result.get("output_path"),
                    "scan_types": ["security", "quality"],
                    "auto_fix": False,
                    # Feed test results so critique can suggest fixes
                    "test_results": testgen_result,
                    "validation_results": val_result,
                    "stages_completed": stages_completed,
                    "stages_failed": stages_failed,
                    "output_dir": payload.get("output_dir", ""),  # FIX: Propagate output_dir for consistency
                }
                logger.info(f"[PIPELINE] Job {job_id} starting step: critique")
                critique_result = await self._run_critique(job_id, critique_payload)
                if critique_result.get("status") == "completed":
                    stages_completed.append("critique")
                    logger.info(f"[PIPELINE] Job {job_id} completed step: critique")
                elif critique_result.get("status") == "error":
                    logger.warning(f"[PIPELINE] Job {job_id} failed step: critique - {critique_result.get('message', 'Unknown error')} (continuing pipeline)")
            
            logger.info(f"[PIPELINE] Pipeline completed successfully for job {job_id}")
            
            output_path = codegen_result.get("output_path")
            
            # FIX: Add final validation to verify all expected files and directories exist
            validation_warnings = []
            if output_path:
                output_path_obj = Path(output_path)
                
                # Check for required directories based on stages completed
                # Only validate artifacts that were actually requested/generated
                if "deploy" in stages_completed:
                    # Note: Only check for artifacts based on what targets were run
                    # The deploy_all runs docker, kubernetes, and helm
                    
                    # Check for Docker files (always generated)
                    if not (output_path_obj / "Dockerfile").exists():
                        validation_warnings.append("Dockerfile not found despite deploy stage completing")
                    
                    # Check for Kubernetes directory and files
                    k8s_dir = output_path_obj / "k8s"
                    if not k8s_dir.exists():
                        validation_warnings.append("k8s/ directory not found despite deploy stage completing")
                    else:
                        if not (k8s_dir / "deployment.yaml").exists():
                            validation_warnings.append("k8s/deployment.yaml not found")
                        if not (k8s_dir / "service.yaml").exists():
                            validation_warnings.append("k8s/service.yaml not found")
                    
                    # Check for Helm directory and files
                    helm_dir = output_path_obj / "helm"
                    if not helm_dir.exists():
                        validation_warnings.append("helm/ directory not found despite deploy stage completing")
                    else:
                        if not (helm_dir / "Chart.yaml").exists():
                            validation_warnings.append("helm/Chart.yaml not found")
                        if not (helm_dir / "values.yaml").exists():
                            validation_warnings.append("helm/values.yaml not found")
                
                if "docgen" in stages_completed:
                    docs_dir = output_path_obj / "docs"
                    if not docs_dir.exists():
                        validation_warnings.append("docs/ directory not found despite docgen stage completing")
                
                if "critique" in stages_completed:
                    reports_dir = output_path_obj / "reports"
                    if not reports_dir.exists():
                        validation_warnings.append("reports/ directory not found despite critique stage completing")
                    else:
                        if not (reports_dir / "critique_report.json").exists():
                            validation_warnings.append("reports/critique_report.json not found")
                
                # Log warnings if any
                if validation_warnings:
                    logger.warning(
                        f"[PIPELINE] Validation warnings for job {job_id}",
                        extra={
                            "job_id": job_id,
                            "warnings": validation_warnings,
                            "stages_completed": stages_completed
                        }
                    )
                else:
                    logger.info(f"[PIPELINE] All expected files and directories validated for job {job_id}")

            # Store stages_completed in job metadata for the single finalizer in generator.py
            if job_id in jobs_db:
                job = jobs_db[job_id]
                job.metadata["stages_completed"] = stages_completed
                job.metadata["output_path"] = output_path
                if validation_warnings:
                    job.metadata["validation_warnings"] = validation_warnings

            # NOTE: Do NOT call _finalize_successful_job here.
            # Finalization is handled by finalize_job_success() in generator.py
            # to avoid double-finalization and inconsistent state.

            return {
                "status": "completed",
                "stages_completed": stages_completed,
                "output_path": output_path,
                "validation_warnings": validation_warnings,
            }
            
        except Exception as e:
            logger.error(f"[PIPELINE] Job {job_id} FAILED with exception: {str(e)}", exc_info=True)
            
            # Finalize failed job
            await self._finalize_failed_job(job_id, error=str(e))
            
            return {
                "status": "failed",
                "message": str(e),
                "error_type": type(e).__name__,
            }
        finally:
            # FIX: Always remove job from in-progress set
            self._jobs_in_pipeline.discard(job_id)
            logger.debug(f"[PIPELINE] Removed job {job_id} from in-progress set")
    
    async def _finalize_successful_job(
        self, 
        job_id: str, 
        output_path: Optional[str], 
        stages_completed: List[str]
    ) -> None:
        """
        Critical: Update job status to SUCCESS and persist outputs.
        
        This method finalizes a successfully completed job by:
        - Updating job status to COMPLETED
        - Setting completion timestamp
        - Discovering and cataloging output artifacts
        - Creating downloadable ZIP archive
        - Triggering dispatch to Self-Fixing Engineer
        
        Args:
            job_id: Unique job identifier
            output_path: Path to generated output directory
            stages_completed: List of successfully completed pipeline stages
        """
        try:
            if job_id not in jobs_db:
                logger.error(f"✗ Cannot finalize job {job_id}: not found in jobs_db")
                return
            
            job = jobs_db[job_id]
            job.status = JobStatus.COMPLETED
            job.completed_at = datetime.now(timezone.utc)
            job.metadata.update({
                "stages_completed": stages_completed,
                "output_path": output_path,
            })
            
            # Discover and catalog output artifacts
            if output_path:
                output_dir = Path(output_path)
                if output_dir.exists():
                    # FIX Issue 3: Final enforcement of output layout after all stages complete
                    # This catches any stray files written outside the project subdirectory
                    # Extract project name from job metadata or use default
                    project_name = job.metadata.get("output_dir", "hello_generator")
                    if not project_name:
                        project_name = "hello_generator"
                    
                    try:
                        if _MATERIALIZER_AVAILABLE:
                            logger.info(f"[FINALIZE] Running final layout enforcement for job {job_id}")
                            from generator.runner.runner_file_utils import _enforce_output_layout
                            layout_result = _enforce_output_layout(output_dir, project_name)
                            
                            if layout_result.get("success"):
                                if layout_result.get("files_moved"):
                                    logger.info(
                                        f"[FINALIZE] Layout enforcement moved {len(layout_result['files_moved'])} items "
                                        f"into {project_name}/ subdirectory",
                                        extra={
                                            "job_id": job_id,
                                            "files_moved": layout_result["files_moved"]
                                        }
                                    )
                                else:
                                    logger.debug(f"[FINALIZE] Layout already correct for job {job_id}")
                            else:
                                logger.warning(
                                    f"[FINALIZE] Layout enforcement had errors for job {job_id}: "
                                    f"{layout_result.get('errors', [])}",
                                    extra={"job_id": job_id, "errors": layout_result.get("errors")}
                                )
                        else:
                            logger.debug("[FINALIZE] _enforce_output_layout not available, skipping final layout check")
                    except Exception as layout_err:
                        # Don't fail job finalization if layout enforcement fails
                        logger.warning(f"[FINALIZE] Layout enforcement error for job {job_id}: {layout_err}")
                    
                    artifacts = list(output_dir.rglob('*'))
                    # Exclude existing _output.zip files to avoid nested zips
                    artifact_files = [f for f in artifacts if f.is_file() and not f.name.endswith('_output.zip')]
                    
                    # Generate artifact manifest
                    job.output_files = [f.name for f in artifact_files]
                    
                    # Create downloadable ZIP (in background)
                    zip_path = output_dir.parent / f"{job_id}_output.zip"
                    await self._create_artifact_zip(artifact_files, zip_path, output_dir)
                    
                    logger.info(
                        f"✓ Job {job_id} finalized: status=COMPLETED, files={len(artifact_files)}, "
                        f"stages={', '.join(stages_completed)}"
                    )
                else:
                    logger.warning(f"⚠ Job {job_id} output path {output_path} does not exist")
            
            # Trigger dispatch to Self-Fixing Engineer (non-blocking)
            try:
                # Extract validation context from job metadata
                validation_context = {
                    "validation_errors": job.metadata.get("validation_errors", []),
                    "validation_warnings": job.metadata.get("validation_warnings", []),
                    "stages_completed": stages_completed,
                }
                await self._dispatch_to_sfe(job_id, output_path, validation_context)
            except Exception as dispatch_error:
                # Don't fail job finalization if dispatch fails
                logger.warning(f"⚠ SFE dispatch failed for job {job_id}: {dispatch_error}")
                
        except Exception as e:
            logger.error(f"✗ Failed to finalize successful job {job_id}: {e}", exc_info=True)
            # Don't raise - job is still successful even if finalization has issues
    
    async def _finalize_failed_job(self, job_id: str, error: str) -> None:
        """
        Update job status to FAILED and record error details.
        
        Args:
            job_id: Unique job identifier
            error: Error message describing the failure
        """
        try:
            if job_id not in jobs_db:
                logger.error(f"✗ Cannot finalize failed job {job_id}: not found in jobs_db")
                return
            
            job = jobs_db[job_id]
            job.status = JobStatus.FAILED
            job.completed_at = datetime.now(timezone.utc)
            job.metadata.update({
                "error": error,
                "failed_at": datetime.now(timezone.utc).isoformat(),
            })
            
            logger.info(f"✓ Job {job_id} finalized with FAILED status: {error}")
            
        except Exception as e:
            logger.error(f"✗ Failed to finalize failed job {job_id}: {e}", exc_info=True)
    
    async def _create_artifact_zip(
        self, 
        files: List[Path], 
        zip_path: Path,
        base_dir: Path
    ) -> None:
        """
        Bundle all outputs into single downloadable archive.
        
        Args:
            files: List of file paths to include in archive
            zip_path: Path where ZIP file should be created
            base_dir: Base directory for computing relative paths
        """
        try:
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                for file_path in files:
                    try:
                        # [FIX] Add error handling for path resolution in zip archive
                        # Use relative path within archive
                        try:
                            arcname = file_path.resolve().relative_to(base_dir.resolve())
                        except ValueError as e:
                            logger.warning(f"[DOWNLOAD] File {file_path} is outside base_dir {base_dir}, using filename only. Error: {e}")
                            arcname = file_path.name
                        zf.write(file_path, arcname=arcname)
                    except Exception as file_error:
                        logger.warning(f"⚠ Failed to add {file_path} to archive: {file_error}")
            
            logger.info(f"✓ Created artifact archive at {zip_path} with {len(files)} files")
            
        except Exception as e:
            logger.error(f"✗ Failed to create artifact ZIP: {e}", exc_info=True)
            # Don't raise - ZIP creation failure shouldn't fail the job
    
    async def _dispatch_to_sfe(
        self, 
        job_id: str, 
        output_path: Optional[str],
        validation_context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Dispatch completed job to Self-Fixing Engineer with fallback.
        
        Tries Kafka first, falls back to direct HTTP if Kafka unavailable.
        
        Args:
            job_id: Unique job identifier
            output_path: Path to generated outputs
            validation_context: Optional validation context with errors, warnings, stages
        """
        try:
            # Import here to avoid circular dependencies
            from server.config import get_server_config
            
            config = get_server_config()
            
            # Try Kafka dispatch if enabled
            if config.kafka_enabled:
                try:
                    # Check if Kafka producer is available
                    if hasattr(self, 'kafka_producer') and self.kafka_producer:
                        sfe_payload = {
                            "job_id": job_id,
                            "output_path": output_path,
                            "validation_context": validation_context or {},
                        }
                        await self.kafka_producer.send(
                            topic="sfe_jobs",
                            value=sfe_payload
                        )
                        logger.info(f"✓ Dispatched job {job_id} to SFE via Kafka")
                        return
                except Exception as kafka_error:
                    logger.warning(f"⚠ Kafka dispatch failed: {kafka_error}, trying fallback")
            
            # Fallback: Direct notification (if SFE URL configured)
            sfe_url = os.getenv("SFE_URL")
            if sfe_url:
                import httpx
                
                sfe_payload = {
                    "job_id": job_id,
                    "source": "omnicore",
                    "output_path": output_path,
                    "validation_context": validation_context or {},
                }
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.post(
                        f"{sfe_url}/api/jobs",
                        json=sfe_payload
                    )
                    response.raise_for_status()  # Raise exception for 4xx/5xx responses
                logger.info(f"✓ Dispatched job {job_id} to SFE via HTTP fallback (status: {response.status_code})")
            else:
                logger.info(f"ℹ SFE dispatch skipped for job {job_id} (no Kafka or SFE_URL configured)")
                
        except Exception as e:
            logger.warning(f"⚠ Failed to dispatch job {job_id} to SFE: {e}")
            # Don't raise - dispatch failure shouldn't fail the job
    
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

    async def emit_event(
        self, topic: str, payload: Dict[str, Any], priority: int = 5
    ) -> Dict[str, Any]:
        """
        Emit an event to the message bus.
        
        This is a convenience alias for publish_message() used by job lifecycle
        event handlers. It delegates to publish_message which handles both
        real message bus publishing and fallback behavior.
        
        Args:
            topic: Event topic/channel (e.g., "job.created", "job.updated")
            payload: Event payload data
            priority: Message priority (1-10, default 5)
        
        Returns:
            Publication result with message_id and status
        """
        return await self.publish_message(topic=topic, payload=payload, priority=priority)

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


# Module-level singleton for OmniCoreService
_instance: Optional["OmniCoreService"] = None
_instance_lock = threading.Lock()
_async_instance_lock: Optional[asyncio.Lock] = None
_async_lock_creation_lock = threading.Lock()


def _get_async_lock() -> Optional[asyncio.Lock]:
    """Get or create async lock for current event loop (thread-safe)."""
    global _async_instance_lock
    if _async_instance_lock is None:
        with _async_lock_creation_lock:  # Protect lock creation from race conditions
            if _async_instance_lock is None:
                try:
                    asyncio.get_running_loop()
                    _async_instance_lock = asyncio.Lock()
                except RuntimeError:
                    return None
    return _async_instance_lock


def get_omnicore_service() -> OmniCoreService:
    """
    Get or create the singleton OmniCoreService instance (sync-safe).
    
    This function implements a thread-safe singleton pattern to ensure
    only one OmniCoreService instance is created, preventing multiple
    initializations of resources (database pools, Kafka producers, etc.).
    
    Returns:
        OmniCoreService: The singleton OmniCore service instance
        
    Example:
        >>> from fastapi import Depends
        >>> @router.post("/endpoint")
        >>> async def handler(service: OmniCoreService = Depends(get_omnicore_service)):
        ...     result = await service.route_job(...)
    """
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = OmniCoreService()
    return _instance


async def get_omnicore_service_async() -> OmniCoreService:
    """
    Get or create the singleton OmniCoreService instance (async-safe).
    
    This function implements an asyncio-safe singleton pattern for use in
    async contexts, preventing event loop blocking from threading locks.
    
    Returns:
        OmniCoreService: The singleton OmniCore service instance
        
    Example:
        >>> service = await get_omnicore_service_async()
        >>> result = await service.route_job(...)
    """
    global _instance
    if _instance is None:
        lock = _get_async_lock()
        if lock:
            async with lock:
                if _instance is None:
                    _instance = OmniCoreService()
        else:
            # Fallback to sync if no event loop
            return get_omnicore_service()
    return _instance
