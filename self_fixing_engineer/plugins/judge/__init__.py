# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
self_fixing_engineer.plugins.judge
==========================================

AI agent that evaluates code quality and produces scores and feedback.

Importing this package ensures the agent class is registered with
:class:`~self_fixing_engineer.agent_orchestration.crew_manager.CrewManager`
so it is discoverable by the Arbiter and the orchestration layer at startup.
"""

from __future__ import annotations

from self_fixing_engineer.plugins.judge.judge_agent import JudgeAgent

__all__ = ["JudgeAgent"]
