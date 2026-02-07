# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Shared in-memory storage for the server.

This module provides centralized in-memory storage that is shared across
all routers to ensure data consistency. In production, this should be
replaced with a proper database backend.
"""

from typing import Dict

from server.schemas import Fix, Job

# Shared storage dictionaries
jobs_db: Dict[str, Job] = {}
fixes_db: Dict[str, Fix] = {}

__all__ = ["jobs_db", "fixes_db"]
