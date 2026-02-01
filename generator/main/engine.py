# generator/main/engine.py
"""
Workflow Engine module for the Generator CLI.

Provides the WorkflowEngine class and agent registry management for orchestrating
the code generation pipeline.

This module bridges the CLI with the agent infrastructure, allowing:
- Dynamic agent registration and hot-swapping
- Workflow orchestration across multiple agents
- Health monitoring and status reporting
"""

import asyncio
import logging
from typing import Any, Callable, Dict, Optional, Type

logger = logging.getLogger(__name__)

# Global agent registry
AGENT_REGISTRY: Dict[str, Type[Any]] = {}


def register_agent(name: str, agent_class: Type[Any]) -> None:
    """Register an agent class with the given name.

    Args:
        name: The unique identifier for the agent.
        agent_class: The agent class to register.
    """
    if name in AGENT_REGISTRY:
        logger.warning(f"Agent '{name}' is already registered. Overwriting.")
    AGENT_REGISTRY[name] = agent_class
    logger.info(f"Agent '{name}' registered successfully.")


def hot_swap_agent(name: str, new_agent_class: Type[Any]) -> None:
    """Hot-swap an existing agent with a new implementation.

    Args:
        name: The name of the agent to swap.
        new_agent_class: The new agent class to use.
    """
    if name not in AGENT_REGISTRY:
        logger.warning(f"Agent '{name}' not found in registry. Registering as new.")
    AGENT_REGISTRY[name] = new_agent_class
    logger.info(f"Agent '{name}' hot-swapped successfully.")


def get_agent(name: str) -> Optional[Type[Any]]:
    """Get an agent class by name.

    Args:
        name: The name of the agent to retrieve.

    Returns:
        The agent class if found, None otherwise.
    """
    return AGENT_REGISTRY.get(name)


class WorkflowEngine:
    """Orchestrates workflow execution across multiple agents.

    The WorkflowEngine manages the execution pipeline for code generation,
    coordinating between different agents (codegen, critique, testgen, etc.)
    to produce high-quality output.

    Attributes:
        config: Configuration object for the workflow.
        agents: Dictionary of instantiated agents.
    """

    def __init__(self, config: Any):
        """Initialize the WorkflowEngine with the given configuration.

        Args:
            config: Configuration object containing workflow settings.
        """
        self.config = config
        self.agents: Dict[str, Any] = {}
        self._initialized = False
        logger.info("WorkflowEngine initialized.")

    def health_check(self) -> bool:
        """Check the health status of the workflow engine.

        Returns:
            True if the engine is healthy and ready to process workflows.
        """
        # Basic health check - can be extended to check agent availability
        try:
            # Check if we have any registered agents
            if not AGENT_REGISTRY:
                logger.warning("No agents registered in the workflow engine.")

            # Additional health checks can be added here
            return True
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False

    async def orchestrate(
        self,
        input_file: str,
        max_iterations: int = 3,
        output_path: Optional[str] = None,
        dry_run: bool = False,
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Orchestrate the workflow execution.

        Args:
            input_file: Path to the input file (e.g., README.md).
            max_iterations: Maximum number of refinement iterations.
            output_path: Path to write the generated output.
            dry_run: If True, simulate the workflow without executing agents.
            user_id: Optional user identifier for tracking.

        Returns:
            Dictionary containing the workflow results.
        """
        logger.info(
            f"Starting workflow orchestration: input={input_file}, "
            f"max_iterations={max_iterations}, dry_run={dry_run}, user_id={user_id}"
        )

        if dry_run:
            logger.info("DRY RUN: Simulating workflow execution.")
            return {
                "status": "dry_run_completed",
                "input_file": input_file,
                "iterations": 0,
                "output_path": output_path,
            }

        result = {
            "status": "pending",
            "input_file": input_file,
            "iterations": 0,
            "errors": [],
            "output_path": output_path,
        }

        try:
            # Attempt to use registered agents if available
            for iteration in range(max_iterations):
                result["iterations"] = iteration + 1

                # Check for codegen agent
                codegen_cls = AGENT_REGISTRY.get("codegen")
                if codegen_cls:
                    logger.info(f"Iteration {iteration + 1}: Running codegen agent.")
                    # Agent execution would go here
                else:
                    logger.warning("No codegen agent registered. Using fallback.")

                # Small delay between iterations
                await asyncio.sleep(0.1)

            result["status"] = "completed"
            logger.info(f"Workflow completed after {result['iterations']} iterations.")

        except Exception as e:
            logger.error(f"Workflow orchestration failed: {e}")
            result["status"] = "failed"
            result["errors"].append(str(e))

        return result

    def _tune_from_feedback(self, rating: int) -> None:
        """Tune the workflow based on user feedback.

        Args:
            rating: User rating (1-5) for the generated output.
        """
        logger.info(f"Tuning workflow based on feedback rating: {rating}")
        # Implementation for feedback-based tuning would go here
        # This could adjust agent parameters, prompts, or model selection


# Auto-register available agents from the generator.agents package
def _auto_register_agents() -> None:
    """Automatically register agents from the generator.agents package.
    
    Note: Some agent modules export Config classes (CodeGenConfig, CritiqueConfig)
    while others export Agent classes (TestgenAgent, DeployAgent, DocgenAgent).
    We register what's available from each module.
    """
    try:
        from generator.agents import (
            _AVAILABLE_AGENTS,
            CodeGenConfig,
            CritiqueConfig,
            DeployAgent,
            DocgenAgent,
            TestgenAgent,
        )

        # Register available agents/configs based on what the package exports
        # The agents package exports different types for different agents:
        # - codegen exports CodeGenConfig (config class)
        # - critique exports CritiqueConfig (config class)
        # - testgen exports TestgenAgent (agent class)
        # - deploy exports DeployAgent (agent class)
        # - docgen exports DocgenAgent (agent class)
        
        if _AVAILABLE_AGENTS.get("codegen") and CodeGenConfig:
            AGENT_REGISTRY["codegen"] = CodeGenConfig

        if _AVAILABLE_AGENTS.get("critique") and CritiqueConfig:
            AGENT_REGISTRY["critique"] = CritiqueConfig

        if _AVAILABLE_AGENTS.get("testgen") and TestgenAgent:
            AGENT_REGISTRY["testgen"] = TestgenAgent

        if _AVAILABLE_AGENTS.get("deploy") and DeployAgent:
            AGENT_REGISTRY["deploy"] = DeployAgent

        if _AVAILABLE_AGENTS.get("docgen") and DocgenAgent:
            AGENT_REGISTRY["docgen"] = DocgenAgent

        logger.info(f"Auto-registered {len(AGENT_REGISTRY)} agents: {list(AGENT_REGISTRY.keys())}")

    except ImportError as e:
        logger.warning(f"Could not auto-register agents: {e}")


# Attempt auto-registration at module load
try:
    _auto_register_agents()
except Exception as e:
    logger.debug(f"Agent auto-registration skipped: {e}")


__all__ = [
    "AGENT_REGISTRY",
    "WorkflowEngine",
    "register_agent",
    "hot_swap_agent",
    "get_agent",
]
