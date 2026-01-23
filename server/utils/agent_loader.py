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

import asyncio
import importlib
import logging
import os
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# Import phased loading support
try:
    from server.utils.agent_dependency_graph import get_load_phases, AgentConfig, AGENT_GRAPH
    PHASED_LOADING_AVAILABLE = True
except ImportError:
    PHASED_LOADING_AVAILABLE = False
    AGENT_GRAPH = {}
    logger.warning("Phased loading not available - agent_dependency_graph not found")


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
    
    # Agent dependency map for pre-loading dependencies
    # This prevents circular import deadlocks
    AGENT_DEPENDENCY_MAP = {
        "testgen": ["runner", "omnicore_engine"],
        "critique": ["runner", "omnicore_engine"],
        "deploy": ["runner"],
        "docgen": ["runner", "omnicore_engine"],
        "codegen": ["runner", "omnicore_engine"],
    }
    
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
        self._loading_lock: Optional[asyncio.Lock] = None  # Startup lock
        
        # Deadlock prevention support
        self._import_lock = threading.RLock()
        self._loaded_modules: Dict[str, Any] = {}
        
        # Feature flags
        self._parallel_loading = os.getenv("PARALLEL_AGENT_LOADING", "1") == "1"
        self._phased_loading = os.getenv("PHASED_AGENT_LOADING", "1") == "1" and PHASED_LOADING_AVAILABLE
        
        self._initialized = True
        logger.info("AgentLoader initialized")
        if self._phased_loading:
            logger.info("Phased loading enabled - will prevent import deadlocks")
    
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
    
    async def _load_agent_safe(self, agent_name: str, module_path: str, attempt: int = 1) -> Optional[Any]:
        """
        Safely load agent with deadlock prevention and retry logic.
        
        This method implements the following deadlock prevention strategies:
        1. Check if module is already loaded to avoid reimport
        2. Use a reentrant lock to prevent concurrent imports
        3. Pre-load dependencies to avoid circular import deadlocks
        4. Retry with exponential backoff on deadlock errors
        
        Args:
            agent_name: Name of the agent (e.g., 'codegen', 'testgen')
            module_path: Full module path to import
            attempt: Current attempt number (for retry logic)
            
        Returns:
            Loaded module or None on failure
        """
        max_attempts = 3
        retry_delay = 1.0  # seconds
        
        try:
            # Phase 1: Check if already loaded
            if module_path in sys.modules:
                logger.info(f"Agent {agent_name} already loaded, reusing module")
                return sys.modules[module_path]
            
            # Phase 2: Load dependencies first (before acquiring lock)
            await self._load_agent_dependencies(agent_name)
            
            # Phase 3: Import with lock (synchronous operation)
            # Use asyncio.to_thread to avoid blocking the event loop
            def _sync_import():
                with self._import_lock:
                    # Double-check after acquiring lock
                    if module_path in sys.modules:
                        return sys.modules[module_path]
                    
                    logger.info(f"Importing {agent_name} from {module_path} (attempt {attempt}/{max_attempts})")
                    
                    # Use importlib.import_module instead of __import__
                    module = importlib.import_module(module_path)
                    
                    # Cache the loaded module
                    self._loaded_modules[agent_name] = module
                    
                    logger.info(f"✓ Successfully loaded {agent_name} agent (attempt {attempt})")
                    return module
            
            # Run the synchronous import in a thread pool to avoid blocking
            module = await asyncio.to_thread(_sync_import)
            return module
                
        except ModuleNotFoundError as e:
            logger.error(f"Module not found for {agent_name}: {e}")
            if attempt < max_attempts:
                # Use exponential backoff for consistency
                await asyncio.sleep(retry_delay * (2 ** (attempt - 1)))
                return await self._load_agent_safe(agent_name, module_path, attempt + 1)
            raise
            
        except Exception as e:
            # Check if it's a deadlock or import lock error
            # Python's import system can raise various exceptions for deadlocks:
            # - _DeadlockError (internal)
            # - ImportError with "deadlock" in message
            # - RuntimeError from import locks
            error_msg = str(e).lower()
            error_type = type(e).__name__
            
            is_deadlock = (
                "_deadlock" in error_type.lower() or
                "deadlock" in error_msg or
                ("import" in error_msg and "lock" in error_msg) or
                ("circular" in error_msg and "import" in error_msg)
            )
            
            if is_deadlock:
                logger.warning(f"Deadlock/circular import detected loading {agent_name}, retrying (attempt {attempt}/{max_attempts})")
                
                if attempt < max_attempts:
                    # Exponential backoff
                    await asyncio.sleep(retry_delay * (2 ** (attempt - 1)))
                    
                    # Clear the problematic module from sys.modules if present
                    if module_path in sys.modules:
                        del sys.modules[module_path]
                    
                    return await self._load_agent_safe(agent_name, module_path, attempt + 1)
                else:
                    logger.error(f"Failed to load {agent_name} after {max_attempts} attempts due to deadlock")
                    raise
            else:
                logger.error(f"Unexpected error loading {agent_name}: {e}", exc_info=True)
                raise
    
    async def _load_agent_dependencies(self, agent_name: str) -> None:
        """
        Load known dependencies for an agent before loading the agent itself.
        This prevents circular import deadlocks.
        
        Uses the AGENT_DEPENDENCY_MAP class constant to determine which
        dependencies to pre-load for each agent.
        
        Args:
            agent_name: Name of the agent to load dependencies for
        """
        deps = self.AGENT_DEPENDENCY_MAP.get(agent_name, [])
        for dep in deps:
            dep_module = f"generator.{dep}" if dep != "omnicore_engine" else dep
            if dep_module not in sys.modules:
                try:
                    importlib.import_module(dep_module)
                    logger.debug(f"Pre-loaded dependency {dep_module} for {agent_name}")
                except Exception as e:
                    logger.warning(f"Could not pre-load dependency {dep_module}: {e}")
    
    async def load_agent_by_config_async(self, config: 'AgentConfig') -> Optional[object]:
        """
        Load a single agent by its configuration asynchronously.
        
        Args:
            config: AgentConfig with module path and import names
            
        Returns:
            Loaded module or None on failure
        """
        logger.info(f"Importing {config.name} from {config.module_path}")
        
        # Dynamic import with timeout protection
        try:
            # Use asyncio.timeout if available (Python 3.11+), otherwise use wait_for
            try:
                import asyncio
                # Try Python 3.11+ timeout
                async with asyncio.timeout(30):  # 30s per agent
                    success, module = await asyncio.to_thread(
                        self.safe_import_agent,
                        agent_type=AgentType(config.name),
                        module_path=config.module_path,
                        import_names=[],  # Already imported in safe_import_agent
                        description=f"Phased load {config.name} agent"
                    )
                    return module if success else None
            except AttributeError:
                # Fall back to wait_for for Python < 3.11
                success, module = await asyncio.wait_for(
                    asyncio.to_thread(
                        self.safe_import_agent,
                        agent_type=AgentType(config.name),
                        module_path=config.module_path,
                        import_names=[],
                        description=f"Phased load {config.name} agent"
                    ),
                    timeout=30.0
                )
                return module if success else None
        except asyncio.TimeoutError:
            error_msg = f"Agent {config.name} import timed out after 30s"
            logger.error(error_msg)
            raise RuntimeError(error_msg)
    
    async def load_agents_phased(self) -> Dict[str, Any]:
        """
        Load agents in phases to prevent import deadlocks.
        
        Phase 0: Foundation modules (sequential)
        Phase 1+: Parallel within phase, sequential between phases
        
        Returns:
            Dictionary of loaded agents
        """
        if not PHASED_LOADING_AVAILABLE:
            logger.warning("Phased loading not available - falling back to regular loading")
            return {}
        
        phases = get_load_phases()
        loaded_agents = {}
        
        logger.info("=" * 80)
        logger.info("Starting PHASED agent loading (deadlock prevention)")
        logger.info(f"Total phases: {len(phases)}")
        logger.info("=" * 80)
        
        for phase_num in sorted(phases.keys()):
            phase_agents = phases[phase_num]
            logger.info(f"Loading phase {phase_num}: {[a.name for a in phase_agents]}")
            
            # Load phase in parallel (safe within same dependency level)
            tasks = []
            for agent_config in phase_agents:
                task = self.load_agent_by_config_async(agent_config)
                tasks.append((agent_config, task))
            
            # Execute all tasks for this phase
            results = await asyncio.gather(*[t[1] for t in tasks], return_exceptions=True)
            
            # Check for failures
            for (agent_config, _), result in zip(tasks, results):
                if isinstance(result, Exception):
                    logger.error(f"Phase {phase_num} agent '{agent_config.name}' failed: {result}")
                    if self._strict_mode:
                        raise result
                elif result is not None:
                    loaded_agents[agent_config.name] = result
                    logger.info(f"✓ Phase {phase_num} agent '{agent_config.name}' loaded")
            
            # Small delay between phases to allow GIL release
            await asyncio.sleep(0.1)
        
        logger.info("=" * 80)
        logger.info(f"✓ Phased loading complete: {len(loaded_agents)}/{len(AGENT_GRAPH)} agents loaded")
        logger.info("=" * 80)
        
        return loaded_agents
    
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
        
        **IMPORTANT**: This method must be called from an async context (e.g., during
        FastAPI application lifespan startup). It will raise RuntimeError if called
        outside an async context.
        
        Features:
        - Startup lock to prevent duplicate initialization
        - Parallel loading support (configurable via PARALLEL_AGENT_LOADING env var)
        - Graceful error handling
        - Performance metrics logging
        
        Args:
            agents_to_load: List of (agent_type, module_path, import_names) tuples.
                          If None, loads a default set of agents.
                          
        Raises:
            RuntimeError: If called outside an async context
        """
        if self._loading_started:
            logger.warning("Background agent loading already started - preventing duplicate initialization")
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
            """Load agents asynchronously with startup lock."""
            # Initialize lock if not already done
            if self._loading_lock is None:
                self._loading_lock = asyncio.Lock()
            
            # Acquire lock to prevent concurrent initialization
            async with self._loading_lock:
                logger.info("=" * 80)
                logger.info("Starting background agent loading")
                logger.info(f"Parallel loading: {self._parallel_loading}")
                logger.info(f"Number of agents to load: {len(agents_to_load)}")
                logger.info("=" * 80)
                
                start_time = datetime.utcnow()
                
                try:
                    if self._parallel_loading:
                        # PHASED SEQUENTIAL LOADING - Prevents import deadlocks
                        logger.info("Phased loading enabled - will prevent import deadlocks")
                        
                        # Define loading phases based on dependencies
                        # Phase 1: Pre-load shared dependencies sequentially
                        shared_modules = ["runner", "omnicore_engine", "arbiter"]
                        logger.info(f"Phase 1: Pre-loading shared dependencies: {shared_modules}")
                        for module_name in shared_modules:
                            try:
                                logger.info(f"  Pre-loading {module_name}...")
                                importlib.import_module(module_name)
                                logger.info(f"  ✓ Pre-loaded {module_name}")
                            except Exception as e:
                                logger.warning(f"  Could not pre-load {module_name}: {e}")
                        
                        # Small delay after phase 1
                        await asyncio.sleep(0.5)
                        
                        # Phase 2: Load codegen first (minimal dependencies)
                        logger.info("Phase 2: Loading codegen agent")
                        codegen_agents = [(at, mp, names) for at, mp, names in agents_to_load 
                                         if at == AgentType.CODEGEN]
                        for agent_type, module_path, import_names in codegen_agents:
                            load_start = time.time()
                            await asyncio.to_thread(
                                self.safe_import_agent,
                                agent_type=agent_type,
                                module_path=module_path,
                                import_names=import_names,
                                description=f"Phase 2 load {agent_type.value} agent",
                            )
                            load_time = time.time() - load_start
                            logger.info(f"  ✓ Loaded {agent_type.value} agent in {load_time:.2f}s")
                            await asyncio.sleep(0.5)  # Give import locks time to release
                        
                        # Phase 3: Load remaining agents sequentially
                        remaining_agents = [(at, mp, names) for at, mp, names in agents_to_load 
                                           if at != AgentType.CODEGEN]
                        logger.info(f"Phase 3: Loading remaining agents: {[at.value for at, _, _ in remaining_agents]}")
                        for agent_type, module_path, import_names in remaining_agents:
                            load_start = time.time()
                            await asyncio.to_thread(
                                self.safe_import_agent,
                                agent_type=agent_type,
                                module_path=module_path,
                                import_names=import_names,
                                description=f"Phase 3 load {agent_type.value} agent",
                            )
                            load_time = time.time() - load_start
                            logger.info(f"  ✓ Loaded {agent_type.value} agent in {load_time:.2f}s")
                            await asyncio.sleep(0.5)  # Give import locks time to release
                    else:
                        # SEQUENTIAL LOADING - Slower but safer for debugging
                        logger.info("Loading agents SEQUENTIALLY (set PARALLEL_AGENT_LOADING=1 for better performance)")
                        for agent_type, module_path, import_names in agents_to_load:
                            self.safe_import_agent(
                                agent_type=agent_type,
                                module_path=module_path,
                                import_names=import_names,
                                description=f"Sequential load {agent_type.value} agent",
                            )
                    
                    self._loading_completed = True
                    end_time = datetime.utcnow()
                    duration = (end_time - start_time).total_seconds()
                    
                    logger.info("=" * 80)
                    logger.info("✓ Background agent loading completed successfully")
                    logger.info(f"Loading time: {duration:.2f}s")
                    
                    # Log summary
                    status = self.get_status()
                    available = len(status['available_agents'])
                    total = status['total_agents']
                    logger.info(f"Loaded {available}/{total} agents successfully")
                    
                    if status['unavailable_agents']:
                        logger.warning(f"Unavailable agents: {', '.join(status['unavailable_agents'])}")
                    
                    logger.info("=" * 80)
                    
                except Exception as e:
                    logger.error(f"Error during background agent loading: {e}", exc_info=True)
                    self._loading_error = str(e)
                    self._loading_completed = True  # Mark as completed even on error
        
        # Create the background task - must be in async context
        try:
            self._loading_task = asyncio.create_task(load_agents_async())
            logger.info("✓ Background agent loading task created with startup lock protection")
        except RuntimeError as e:
            # Not in an async context - this is an error
            logger.error("start_background_loading must be called from an async context")
            self._loading_started = False  # Reset since we failed
            raise RuntimeError(
                "start_background_loading must be called from an async context "
                "(e.g., during FastAPI application lifespan)"
            ) from e
    
    def is_loading(self) -> bool:
        """
        Check if agents are currently being loaded in the background.
        
        Note: This method checks two boolean flags (_loading_started and _loading_completed)
        without synchronization. While there is a theoretical race condition if these flags
        are modified during the check, this is acceptable for our use case because:
        1. The flags are only set once (started -> True, completed -> True)
        2. The worst case is a brief inconsistency during state transition
        3. This is only used for informational/status purposes, not critical logic
        
        Returns:
            True if background loading is in progress, False otherwise
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
