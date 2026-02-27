# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.
"""
fixer.py - Main entry point for the import fixer CLI command.
"""
import asyncio
from typing import Any, Dict, List, Optional
from .import_fixer_engine import run_import_healer


async def main(
    project_root: str,
    whitelisted_paths: Optional[List[str]] = None,
    max_workers: int = 4,
    dry_run: bool = False,
    auto_add_deps: bool = False,
    ai_enabled: bool = False,
    output_dir: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Main entry point for CLI heal command."""
    return await run_import_healer(
        project_root=project_root,
        whitelisted_paths=whitelisted_paths or [project_root],
        max_workers=max_workers,
        dry_run=dry_run,
        auto_add_deps=auto_add_deps,
        ai_enabled=ai_enabled,
        output_dir=output_dir or "reports",
        **kwargs,
    )
