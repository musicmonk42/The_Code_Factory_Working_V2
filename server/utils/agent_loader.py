"""
Agent Loader Module
===================

This module provides utilities for safely importing and tracking generator agents
with comprehensive error handling and diagnostics. It implements industry-standard
practices for dependency management and error visibility.

Key Features:
-------------
- Safe agent imports with full error tracking
- Detailed logging of import failures with stack traces
- Agent availability status tracking
- Environment variable validation
- Startup diagnostics for production readiness
- Singleton pattern for centralized agent status

Usage:
------
    from server.utils.agent_loader import get_agent_loader
    
    loader = get_agent_loader()
    status = loader.get_status()
    
    # Check if agent is available
    if loader.is_agent_available('codegen'):
        # Use agent
        pass
    else:
        # Fallback or error
        error = loader.get_agent_error('codegen')
        logger.error(f"Codegen agent unavailable: {error}")
"""

import logging
import os
import sys
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# Environment variable constants
ENV_VAR_TRUE = "1"
ENV_VAR_FALSE = "0"


class AgentType(str, Enum):
    """Enumeration of available agent types."""
    CODEGEN = "codegen"
    TESTGEN = "testgen"
    DEPLOY = "deploy"
    DOCGEN = "docgen"
    CRITIQUE = "critique"


@dataclass
class AgentImportError:
    """Detailed information about an agent import failure."""
    agent_name: str
    error_type: str
    error_message: str
    traceback: str
    timestamp: str
    missing_dependencies: List[str] = field(default_factory=list)
    environment_issues: List[str] = field(default_factory=list)


@dataclass
class AgentStatus:
    """Status information for an agent."""
    name: str
    available: bool
    error: Optional[AgentImportError] = None
    module_path: Optional[str] = None
    loaded_at: Optional[str] = None


class AgentLoader:
    """
    Centralized agent loader with comprehensive error tracking.
    
    This class implements a singleton pattern to maintain consistent
    agent status across the application lifecycle.
    """
    
    _instance: Optional['AgentLoader'] = None
    _initialized: bool = False
    
    def __new__(cls) -> 'AgentLoader':
        """Implement singleton pattern."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        """Initialize the agent loader."""
        if self._initialized:
            return
            
        self._agent_status: Dict[str, AgentStatus] = {}
        self._import_attempts: Dict[str, int] = {}
        self._startup_time = datetime.utcnow().isoformat()
        self._strict_mode = os.getenv("GENERATOR_STRICT_MODE", ENV_VAR_FALSE) == ENV_VAR_TRUE
        self._debug_mode = os.getenv("DEBUG", ENV_VAR_FALSE) == ENV_VAR_TRUE
        
        # Track required environment variables
        self._required_env_vars: Set[str] = set()
        self._optional_env_vars: Set[str] = {
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "GOOGLE_API_KEY",
            "COHERE_API_KEY",
        }
        
        # Background loading support
        self._loading_task: Optional[Any] = None
        self._loading_started: bool = False
        self._loading_completed: bool = False
        self._loading_error: Optional[str] = None
        
        self._initialized = True
        logger.info("AgentLoader initialized")
    
    def safe_import_agent(
        self,
        agent_type: AgentType,
        module_path: str,
        import_names: List[str],
        description: str = "",
    ) -> Tuple[bool, Optional[object]]:
        """
        Safely import an agent with comprehensive error handling.
        
        Args:
            agent_type: Type of agent being imported
            module_path: Full module path (e.g., 'generator.agents.codegen_agent.codegen_agent')
            import_names: List of names to import from the module
            description: Human-readable description for logging
            
        Returns:
            Tuple of (success: bool, module: Optional[object])
        """
        agent_name = agent_type.value
        self._import_attempts[agent_name] = self._import_attempts.get(agent_name, 0) + 1
        
        try:
            logger.info(f"Attempting to import {agent_name} agent from {module_path}")
            
            # Ensure project root is in path
            project_root = Path(__file__).parent.parent.parent
            if str(project_root) not in sys.path:
                sys.path.insert(0, str(project_root))
            
            # Attempt import
            module = __import__(module_path, fromlist=import_names)
            
            # Verify all requested names are available
            missing_names = [name for name in import_names if not hasattr(module, name)]
            if missing_names:
                raise ImportError(
                    f"Module {module_path} imported but missing required names: {missing_names}"
                )
            
            # Success!
            self._agent_status[agent_name] = AgentStatus(
                name=agent_name,
                available=True,
                module_path=module_path,
                loaded_at=datetime.utcnow().isoformat(),
            )
            
            logger.info(
                f"✓ Successfully loaded {agent_name} agent "
                f"(attempt {self._import_attempts[agent_name]})"
            )
            
            return True, module
            
        except ImportError as e:
            return self._handle_import_error(agent_name, module_path, e, description)
        except Exception as e:
            return self._handle_import_error(agent_name, module_path, e, description)
    
    def _handle_import_error(
        self,
        agent_name: str,
        module_path: str,
        error: Exception,
        description: str,
    ) -> Tuple[bool, None]:
        """
        Handle and log an import error with full details.
        
        Args:
            agent_name: Name of the agent that failed to import
            module_path: Module path that was attempted
            error: The exception that occurred
            description: Human-readable description
            
        Returns:
            Tuple of (False, None) indicating failure
        """
        # Get full traceback
        tb_str = traceback.format_exc()
        error_type = type(error).__name__
        error_message = str(error)
        
        # Analyze the error to identify missing dependencies
        missing_deps = self._extract_missing_dependencies(error_message, tb_str)
        
        # Check for environment issues
        env_issues = self._check_environment_issues()
        
        # Create detailed error record
        import_error = AgentImportError(
            agent_name=agent_name,
            error_type=error_type,
            error_message=error_message,
            traceback=tb_str,
            timestamp=datetime.utcnow().isoformat(),
            missing_dependencies=missing_deps,
            environment_issues=env_issues,
        )
        
        # Update status
        self._agent_status[agent_name] = AgentStatus(
            name=agent_name,
            available=False,
            error=import_error,
            module_path=module_path,
        )
        
        # Log at appropriate level
        log_message = (
            f"✗ Agent '{agent_name}' failed to load and will be unavailable.\n"
            f"  Module: {module_path}\n"
            f"  Error: {error_type}: {error_message}\n"
            f"  Attempt: {self._import_attempts[agent_name]}"
        )
        
        if missing_deps:
            log_message += f"\n  Missing dependencies: {', '.join(missing_deps)}"
        
        if env_issues:
            log_message += f"\n  Environment issues: {'; '.join(env_issues)}"
        
        if description:
            log_message += f"\n  Description: {description}"
        
        # Log at ERROR level for visibility
        logger.error(log_message)
        
        # In strict/debug mode, also log full traceback
        if self._strict_mode or self._debug_mode:
            logger.error(f"Full traceback for '{agent_name}' import failure:\n{tb_str}")
        
        # In strict mode, raise the error
        if self._strict_mode:
            raise RuntimeError(
                f"Agent '{agent_name}' is required but failed to load: {error_message}"
            ) from error
        
        return False, None
    
    def _extract_missing_dependencies(
        self, error_message: str, traceback_str: str
    ) -> List[str]:
        """
        Extract missing dependency names from error messages.
        
        Args:
            error_message: The error message string
            traceback_str: The full traceback string
            
        Returns:
            List of missing dependency names
        """
        missing_deps = []
        
        # Common patterns for missing modules
        patterns = [
            "No module named '",
            "cannot import name '",
            "ModuleNotFoundError: No module named '",
            "ImportError: cannot import name '",
        ]
        
        combined_text = error_message + "\n" + traceback_str
        
        for pattern in patterns:
            if pattern in combined_text:
                # Extract module name after the pattern
                start_idx = combined_text.find(pattern) + len(pattern)
                end_idx = combined_text.find("'", start_idx)
                if end_idx > start_idx:
                    module_name = combined_text[start_idx:end_idx]
                    # Get base package name (e.g., 'aiofiles' from 'aiofiles.os')
                    base_module = module_name.split('.')[0]
                    if base_module and base_module not in missing_deps:
                        missing_deps.append(base_module)
        
        return missing_deps
    
    def _check_environment_issues(self) -> List[str]:
        """
        Check for environment configuration issues.
        
        Returns:
            List of environment issue descriptions
        """
        issues = []
        
        # Check required environment variables
        for env_var in self._required_env_vars:
            if not os.getenv(env_var):
                issues.append(f"Required environment variable {env_var} is not set")
        
        # Check Python path includes project root
        project_root = Path(__file__).parent.parent.parent
        if str(project_root) not in sys.path:
            issues.append(f"Project root {project_root} not in Python path")
        
        return issues
    
    def start_background_loading(self, agents_to_load: Optional[List[Tuple[AgentType, str, List[str]]]] = None):
        """
        Start background loading of agents without blocking.
        
        This method initiates agent loading in a background task, allowing the
        HTTP server to start accepting connections immediately.
        
        Args:
            agents_to_load: List of (agent_type, module_path, import_names) tuples.
                          If None, loads a default set of agents.
        """
        if self._loading_started:
            logger.warning("Background agent loading already started")
            return
        
        self._loading_started = True
        
        # Default agents to load
        if agents_to_load is None:
            agents_to_load = [
                (AgentType.CODEGEN, "generator.agents.codegen_agent.codegen_agent", ["generate_code"]),
                (AgentType.TESTGEN, "generator.agents.testgen_agent.testgen_agent", ["TestgenAgent"]),
                (AgentType.DEPLOY, "generator.agents.deploy_agent.deploy_agent", ["DeployAgent"]),
                (AgentType.DOCGEN, "generator.agents.docgen_agent.docgen_agent", ["DocgenAgent"]),
                (AgentType.CRITIQUE, "generator.agents.critique_agent.critique_agent", ["CritiqueAgent"]),
            ]
        
        import asyncio
        
        async def load_agents_async():
            """Load agents asynchronously."""
            logger.info("Starting background agent loading")
            try:
                for agent_type, module_path, import_names in agents_to_load:
                    self.safe_import_agent(
                        agent_type=agent_type,
                        module_path=module_path,
                        import_names=import_names,
                        description=f"Background load {agent_type.value} agent",
                    )
                
                self._loading_completed = True
                logger.info("✓ Background agent loading completed successfully")
                
                # Log summary
                status = self.get_status()
                logger.info(f"Loaded {len(status['available_agents'])}/{status['total_agents']} agents")
                
            except Exception as e:
                logger.error(f"Error during background agent loading: {e}", exc_info=True)
                self._loading_error = str(e)
                self._loading_completed = True  # Mark as completed even on error
        
        # Create the background task
        self._loading_task = asyncio.create_task(load_agents_async())
        logger.info("Background agent loading task created")
    
    def is_loading(self) -> bool:
        """
        Check if agents are currently being loaded in the background.
        
        Returns:
            True if background loading is in progress
        """
        return self._loading_started and not self._loading_completed
    
    def is_agent_available(self, agent_type: str) -> bool:
        """
        Check if an agent is available for use.
        
        Args:
            agent_type: Name of the agent (e.g., 'codegen', 'testgen')
            
        Returns:
            True if agent is loaded and available
        """
        status = self._agent_status.get(agent_type)
        return status is not None and status.available
    
    def get_agent_error(self, agent_type: str) -> Optional[AgentImportError]:
        """
        Get detailed error information for a failed agent import.
        
        Args:
            agent_type: Name of the agent
            
        Returns:
            AgentImportError if agent failed to load, None otherwise
        """
        status = self._agent_status.get(agent_type)
        return status.error if status else None
    
    def get_status(self) -> Dict[str, Any]:
        """
        Get comprehensive status of all agents.
        
        Returns:
            Dictionary with agent status and diagnostics
        """
        available_agents = [
            name for name, status in self._agent_status.items() if status.available
        ]
        unavailable_agents = [
            name for name, status in self._agent_status.items() if not status.available
        ]
        
        # Collect all missing dependencies
        all_missing_deps = set()
        for status in self._agent_status.values():
            if status.error and status.error.missing_dependencies:
                all_missing_deps.update(status.error.missing_dependencies)
        
        # Check environment variables
        env_status = {}
        for env_var in self._optional_env_vars:
            env_status[env_var] = "set" if os.getenv(env_var) else "not_set"
        
        return {
            "startup_time": self._startup_time,
            "strict_mode": self._strict_mode,
            "debug_mode": self._debug_mode,
            "loading_in_progress": self.is_loading(),
            "loading_completed": self._loading_completed,
            "loading_error": self._loading_error,
            "total_agents": len(self._agent_status),
            "available_agents": available_agents,
            "unavailable_agents": unavailable_agents,
            "availability_rate": (
                len(available_agents) / len(self._agent_status)
                if self._agent_status
                else 0.0
            ),
            "missing_dependencies": sorted(list(all_missing_deps)),
            "environment_variables": env_status,
            "agents": {
                name: {
                    "available": status.available,
                    "module_path": status.module_path,
                    "loaded_at": status.loaded_at,
                    "error": (
                        {
                            "type": status.error.error_type,
                            "message": status.error.error_message,
                            "missing_dependencies": status.error.missing_dependencies,
                            "environment_issues": status.error.environment_issues,
                            "timestamp": status.error.timestamp,
                        }
                        if status.error
                        else None
                    ),
                }
                for name, status in self._agent_status.items()
            },
            "import_attempts": self._import_attempts,
        }
    
    def get_detailed_error_report(self) -> str:
        """
        Generate a detailed human-readable error report.
        
        Returns:
            Formatted string with full diagnostic information
        """
        lines = [
            "=" * 80,
            "AGENT LOADER DIAGNOSTIC REPORT",
            "=" * 80,
            f"Generated at: {datetime.utcnow().isoformat()}",
            f"Startup time: {self._startup_time}",
            f"Strict mode: {self._strict_mode}",
            f"Debug mode: {self._debug_mode}",
            "",
        ]
        
        # Summary
        status = self.get_status()
        lines.extend([
            "SUMMARY",
            "-" * 80,
            f"Total agents: {status['total_agents']}",
            f"Available: {len(status['available_agents'])}",
            f"Unavailable: {len(status['unavailable_agents'])}",
            f"Availability rate: {status['availability_rate']:.1%}",
            "",
        ])
        
        # Available agents
        if status['available_agents']:
            lines.extend([
                "AVAILABLE AGENTS",
                "-" * 80,
            ])
            for agent_name in status['available_agents']:
                agent_info = status['agents'][agent_name]
                lines.append(f"✓ {agent_name}")
                lines.append(f"  Module: {agent_info['module_path']}")
                lines.append(f"  Loaded: {agent_info['loaded_at']}")
            lines.append("")
        
        # Unavailable agents with details
        if status['unavailable_agents']:
            lines.extend([
                "UNAVAILABLE AGENTS",
                "-" * 80,
            ])
            for agent_name in status['unavailable_agents']:
                agent_info = status['agents'][agent_name]
                error = agent_info.get('error', {})
                lines.append(f"✗ {agent_name}")
                lines.append(f"  Module: {agent_info['module_path']}")
                lines.append(f"  Error: {error.get('type', 'Unknown')}: {error.get('message', 'Unknown')}")
                
                if error.get('missing_dependencies'):
                    lines.append(f"  Missing deps: {', '.join(error['missing_dependencies'])}")
                
                if error.get('environment_issues'):
                    lines.append(f"  Env issues: {'; '.join(error['environment_issues'])}")
                
                lines.append(f"  Failed at: {error.get('timestamp', 'Unknown')}")
                lines.append("")
        
        # Missing dependencies summary
        if status['missing_dependencies']:
            lines.extend([
                "MISSING DEPENDENCIES",
                "-" * 80,
                "The following packages need to be installed:",
            ])
            for dep in status['missing_dependencies']:
                lines.append(f"  - {dep}")
            lines.append("")
            lines.append("Install with: pip install " + " ".join(status['missing_dependencies']))
            lines.append("")
        
        # Environment variables
        lines.extend([
            "ENVIRONMENT VARIABLES",
            "-" * 80,
        ])
        for env_var, env_status in status['environment_variables'].items():
            symbol = "✓" if env_status == "set" else "✗"
            lines.append(f"{symbol} {env_var}: {env_status}")
        lines.append("")
        
        lines.append("=" * 80)
        
        return "\n".join(lines)
    
    def validate_for_production(self, required_agents: Optional[List[str]] = None) -> bool:
        """
        Validate that all required agents are available for production use.
        
        This method is designed for use in production deployment validation
        and health checks. By default, it validates ALL known agents, but you
        can specify a subset of required agents for more flexible validation.
        
        **Production Deployment Example:**
        ```python
        # Validate only critical agents
        loader.validate_for_production(['codegen', 'testgen'])
        
        # Validate all agents (strict mode)
        loader.validate_for_production()
        ```
        
        Args:
            required_agents: List of agent names that must be available.
                           If None (default), validates ALL known agents.
                           Pass an empty list to skip validation.
                           
        Returns:
            True if all required agents are available
            
        Raises:
            RuntimeError: If any required agent is unavailable.
                        The error message includes a full diagnostic report.
        
        Note:
            For more flexible deployments where some agents are optional,
            call this with a specific list of required agents rather than
            relying on the default behavior of validating all agents.
        """
        if required_agents is None:
            required_agents = [agent.value for agent in AgentType]
        
        missing = []
        for agent_name in required_agents:
            if not self.is_agent_available(agent_name):
                error = self.get_agent_error(agent_name)
                error_msg = error.error_message if error else "Unknown error"
                missing.append(f"{agent_name}: {error_msg}")
        
        if missing:
            error_report = self.get_detailed_error_report()
            raise RuntimeError(
                f"Production validation failed. {len(missing)} required agent(s) unavailable:\n"
                + "\n".join(f"  - {m}" for m in missing)
                + f"\n\nFull diagnostic report:\n{error_report}"
            )
        
        logger.info(
            f"✓ Production validation passed. All {len(required_agents)} required agents available."
        )
        return True


# Global singleton instance
_agent_loader: Optional[AgentLoader] = None


def get_agent_loader() -> AgentLoader:
    """
    Get the global AgentLoader singleton instance.
    
    Returns:
        AgentLoader instance
    """
    global _agent_loader
    if _agent_loader is None:
        _agent_loader = AgentLoader()
    return _agent_loader


def safe_import_agent(
    agent_type: AgentType,
    module_path: str,
    import_names: List[str],
    description: str = "",
) -> Tuple[bool, Optional[object]]:
    """
    Convenience function to safely import an agent using the global loader.
    
    Args:
        agent_type: Type of agent being imported
        module_path: Full module path
        import_names: List of names to import from the module
        description: Human-readable description for logging
        
    Returns:
        Tuple of (success: bool, module: Optional[object])
    """
    loader = get_agent_loader()
    return loader.safe_import_agent(agent_type, module_path, import_names, description)
