# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
engine.py - IntentCaptureEngine Adapter for Arbiter Integration

This module provides a real IntentCaptureEngine adapter class that bridges
the intent_capture module with the Arbiter and other platform components.

The engine provides:
- generate_report: Generate reports from agent data and metrics
- capture_intent: Capture user intent and generate specifications
- get_requirements: Retrieve and compute requirements coverage

All methods include proper error handling and fallback behavior to ensure
graceful degradation when dependencies are missing.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class IntentCaptureEngine:
    """
    Real IntentCaptureEngine adapter for Arbiter integration.
    
    This engine delegates to the actual intent_capture module's components:
    - CollaborativeAgent for intent capture
    - spec_utils for specification generation
    - requirements for checklist and coverage computation
    - session for state management
    
    Implements graceful degradation when optional dependencies are missing.
    """
    
    def __init__(
        self,
        llm_config: Optional[Dict[str, Any]] = None,
        session_backend: Optional[Any] = None,
    ):
        """
        Initialize the IntentCaptureEngine.
        
        Args:
            llm_config: Optional LLM configuration (provider, model, etc.)
            session_backend: Optional session backend for state persistence
        """
        self.llm_config = llm_config or {}
        self.session_backend = session_backend
        self._agent_cache = {}
        logger.info("IntentCaptureEngine initialized")
    
    async def generate_report(self, agent_name: str, **kwargs) -> Dict[str, Any]:
        """
        Generate a report based on agent state and metrics.
        
        Args:
            agent_name: Name of the agent to generate report for
            **kwargs: Additional parameters (metrics, events, etc.)
        
        Returns:
            Dict containing report data with timestamp, metrics, and summary
        """
        try:
            # Try to import and use real spec generation
            from .spec_utils import generate_spec_from_memory
            from .agent_core import get_or_create_agent
            
            # Get or create the agent
            agent = await get_or_create_agent(session_token=agent_name)
            
            # Generate spec from agent memory
            spec_data = await generate_spec_from_memory(
                agent.memory,
                output_format="dict"
            )
            
            report = {
                "agent_name": agent_name,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "metrics": kwargs.get("metrics", {}),
                "spec": spec_data,
                "summary": f"Report for {agent_name} generated with spec and {len(kwargs.get('events', []))} events.",
            }
            
            logger.info(f"Generated report for agent {agent_name}")
            return report
            
        except ImportError as e:
            logger.debug(f"Intent capture modules not available for report generation: {e}")
            # Fallback to basic report
            return self._generate_basic_report(agent_name, **kwargs)
        except Exception as e:
            logger.error(f"Error generating report for {agent_name}: {e}")
            return self._generate_basic_report(agent_name, **kwargs)
    
    def _generate_basic_report(self, agent_name: str, **kwargs) -> Dict[str, Any]:
        """Generate a basic report when full functionality is not available."""
        return {
            "agent_name": agent_name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "metrics": kwargs.get("metrics", {}),
            "summary": f"Basic report for {agent_name} generated with {len(kwargs.get('events', []))} events.",
        }
    
    async def capture_intent(
        self,
        user_input: str,
        session_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Capture user intent and generate a response.
        
        Args:
            user_input: The user's input text
            session_id: Optional session identifier for state persistence
        
        Returns:
            Dict containing the agent's response and any generated artifacts
        """
        try:
            from .agent_core import get_or_create_agent
            
            # Use provided session_id or default
            session_token = session_id or "default_intent_capture"
            
            # Get or create agent for this session
            agent = await get_or_create_agent(session_token=session_token)
            
            # Predict/respond to user input
            response = await agent.predict(user_input)
            
            logger.info(f"Captured intent for session {session_token}")
            return {
                "session_id": session_token,
                "user_input": user_input,
                "response": response.get("response", ""),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            
        except ImportError as e:
            logger.debug(f"Intent capture agent_core not available: {e}")
            return self._fallback_intent_capture(user_input, session_id)
        except Exception as e:
            logger.error(f"Error capturing intent: {e}")
            return self._fallback_intent_capture(user_input, session_id)
    
    def _fallback_intent_capture(
        self,
        user_input: str,
        session_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Fallback intent capture when agent is not available."""
        return {
            "session_id": session_id or "default",
            "user_input": user_input,
            "response": f"Intent captured (fallback mode): {user_input[:100]}...",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    
    async def get_requirements(
        self,
        project: Optional[str] = None,
        domain: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get requirements checklist and compute coverage.
        
        Args:
            project: Optional project name
            domain: Optional domain/category
        
        Returns:
            Dict containing checklist and coverage information
        """
        try:
            from .requirements import get_checklist, compute_coverage
            
            # Get the checklist for the project/domain
            checklist = await get_checklist(domain=domain, project=project)
            
            # Compute coverage (pass appropriate parameters based on your implementation)
            coverage = await compute_coverage(
                checklist_data=checklist,
                project=project
            )
            
            logger.info(f"Retrieved requirements for project={project}, domain={domain}")
            return {
                "project": project,
                "domain": domain,
                "checklist": checklist,
                "coverage": coverage,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            
        except ImportError as e:
            logger.debug(f"Requirements module not available: {e}")
            return self._fallback_requirements(project, domain)
        except Exception as e:
            logger.error(f"Error getting requirements: {e}")
            return self._fallback_requirements(project, domain)
    
    def _fallback_requirements(
        self,
        project: Optional[str] = None,
        domain: Optional[str] = None
    ) -> Dict[str, Any]:
        """Fallback requirements when module is not available."""
        return {
            "project": project,
            "domain": domain,
            "checklist": [],
            "coverage": {"total": 0, "completed": 0, "percentage": 0.0},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
