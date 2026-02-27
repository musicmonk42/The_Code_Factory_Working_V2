# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
self_fixing_engineer.plugins.human
==========================================

Human escalation node for mission-critical decisions.

Importing this package ensures the agent class is registered with
:class:`~self_fixing_engineer.agent_orchestration.crew_manager.CrewManager`
so it is discoverable by the Arbiter and the orchestration layer at startup.
"""

from __future__ import annotations

from self_fixing_engineer.plugins.human.human_in_the_loop import HumanInTheLoop

__all__ = ["HumanInTheLoop"]
