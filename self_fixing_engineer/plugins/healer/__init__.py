# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
self_fixing_engineer.plugins.healer
==========================================

Mission-critical AI agent for self-healing and auto-fix operations.

Importing this package ensures the agent class is registered with
:class:`~self_fixing_engineer.agent_orchestration.crew_manager.CrewManager`
so it is discoverable by the Arbiter and the orchestration layer at startup.
"""

from __future__ import annotations

from self_fixing_engineer.plugins.healer.healer_agent import HealerAgent

__all__ = ["HealerAgent"]
