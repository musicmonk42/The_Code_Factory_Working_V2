# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
cli.py

Command-line interface for the Refactor Agent.

Commands:
    refactor  -- Load config, initialise CrewManager, run refactor agent.
    selftest  -- Validate YAML, check entrypoints exist, verify configdb resolution.
    status    -- Show agent crew status.
"""

import argparse
import asyncio
import json
import logging
import os
import sys

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default paths
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG = os.path.join(
    os.path.dirname(__file__), "refactor_agent.yaml"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _load_crew(config_path: str):
    from self_fixing_engineer.agent_orchestration.crew_manager import CrewManager

    return await CrewManager.from_config_yaml(config_path)


# ---------------------------------------------------------------------------
# Command: refactor
# ---------------------------------------------------------------------------


async def _cmd_refactor(args: argparse.Namespace) -> int:
    """Load config, initialise CrewManager, run refactor agent."""
    config_path = args.config
    if not os.path.exists(config_path):
        print(f"[ERROR] Config file not found: {config_path}", file=sys.stderr)
        return 1

    print(f"[refactor] Loading config: {config_path}")
    crew = await _load_crew(config_path)

    agent_name = "refactor_agent" if args.mode == "single" else None
    # Attempt to find the refactor agent by name or tag
    agents = crew.list_agents(tags=["refactor"])
    if not agents:
        agents = crew.list_agents()

    if not agents:
        print("[ERROR] No agents loaded from config.", file=sys.stderr)
        return 1

    target = agents[0]
    print(f"[refactor] Starting agent: {target}")
    try:
        await crew.start_agent(target, caller_role="admin")
        print(f"[refactor] Agent '{target}' started successfully.")
    except Exception as exc:
        print(f"[ERROR] Failed to start agent '{target}': {exc}", file=sys.stderr)
        return 1

    status = await crew.status()
    print(json.dumps(status, indent=2, default=str))
    await crew.close()
    return 0


# ---------------------------------------------------------------------------
# Command: selftest
# ---------------------------------------------------------------------------


async def _cmd_selftest(args: argparse.Namespace) -> int:
    """Validate YAML, check entrypoints, verify configdb resolution."""
    import yaml
    from self_fixing_engineer.refactor_agent.config_resolver import ConfigDBResolver

    config_path = args.config
    print(f"[selftest] Validating config: {config_path}")

    if not os.path.exists(config_path):
        print(f"[FAIL] Config file not found: {config_path}", file=sys.stderr)
        return 1

    with open(config_path, "r") as fh:
        try:
            config_data = yaml.safe_load(fh)
            print("[PASS] YAML is valid.")
        except yaml.YAMLError as exc:
            print(f"[FAIL] YAML parse error: {exc}", file=sys.stderr)
            return 1

    # Check entrypoints
    failed = False
    for agent_def in config_data.get("agents", []):
        ep = agent_def.get("entrypoint")
        if not ep:
            continue
        if ep.endswith(".py"):
            exists = os.path.exists(ep)
            tag = "[PASS]" if exists else "[FAIL]"
            print(f"{tag} entrypoint exists: {ep}")
            if not exists:
                failed = True

    # Verify configdb resolution
    resolver = ConfigDBResolver()
    test_uris = [
        "configdb://roles/refactor",
        "configdb://skills/healer",
    ]
    for uri in test_uris:
        try:
            result = await resolver.resolve(uri)
            tag = "[PASS]" if result is not None else "[WARN]"
            print(f"{tag} configdb resolved: {uri} → {list(result.keys()) if result else {}}")
        except Exception as exc:
            print(f"[FAIL] configdb resolution error for {uri}: {exc}", file=sys.stderr)
            failed = True

    return 1 if failed else 0


# ---------------------------------------------------------------------------
# Command: status
# ---------------------------------------------------------------------------


async def _cmd_status(args: argparse.Namespace) -> int:
    """Show agent crew status."""
    config_path = args.config
    if not os.path.exists(config_path):
        print(f"[ERROR] Config file not found: {config_path}", file=sys.stderr)
        return 1

    crew = await _load_crew(config_path)
    status = await crew.status()
    print(json.dumps(status, indent=2, default=str))
    await crew.close()
    return 0


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="refactor-agent",
        description="Refactor Agent CLI — manage the AI-powered refactoring crew.",
    )
    parser.add_argument(
        "--config",
        default=_DEFAULT_CONFIG,
        help="Path to the refactor_agent.yaml config file.",
    )
    parser.add_argument(
        "--log-level",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity.",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # refactor
    p_refactor = sub.add_parser("refactor", help="Run the refactor agent.")
    p_refactor.add_argument(
        "--mode",
        default="single",
        choices=["single", "swarm"],
        help="Execution mode.",
    )

    # selftest
    sub.add_parser("selftest", help="Validate config, entrypoints, and configdb.")

    # status
    sub.add_parser("status", help="Show current crew status.")

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(level=getattr(logging, args.log_level))

    dispatch = {
        "refactor": _cmd_refactor,
        "selftest": _cmd_selftest,
        "status": _cmd_status,
    }
    handler = dispatch.get(args.command)
    if handler is None:
        parser.print_help()
        return 1

    return asyncio.run(handler(args))


if __name__ == "__main__":
    sys.exit(main())
