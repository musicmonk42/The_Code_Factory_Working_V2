"""
OmniCore Omega Pro Engine CLI with DecisionOptimizer and audit integration.

Commands:
- serve: Run the FastAPI server.
- simulate: Run a simulation from a request file.
- list-plugins: List available plugins.
- benchmark: Run a benchmarking session.
- optimize: Run DecisionOptimizer for task prioritization and allocation.
- stream-events: Stream DecisionOptimizer events in real-time.
- explain-decision: Retrieve explanation for a specific decision.
- load-strategy: Load a custom strategy plugin for DecisionOptimizer.
- query-agents: Query agent states with filters and DreamMode.
- snapshot-world: Snapshot current world state (all agent states).
- restore-world: Restore world state from a snapshot.
- audit-query: Query audit records with filters and DreamMode.
- audit-snapshot: Snapshot current audit state for compliance.
- audit-replay: Replay audit events for troubleshooting or compliance.
- debug-info: Display system and backend debug information.
- plugin-install: Install a plugin from the marketplace.
- plugin-rate: Rate an installed plugin.
- metrics-status: Display current Prometheus metrics.
- feature-flag-set: Toggle a feature flag.
- generate-test-cases: Generate test cases from audit logs.
- docs: Generate Markdown documentation for CLI commands.
- repl: Enter an interactive shell (Read-Eval-Print Loop).
- message-bus: Commands for interacting with the message bus (publish, monitor, etc.).
- fix-imports: Use the AI-powered fixer to suggest refactoring for imports in a file.
"""

import argparse
import json
import asyncio
import sys
import os
import time
import yaml
import uvicorn
from typing import Dict, List, Any, Optional, Coroutine, Callable, Union, Tuple
from pathlib import Path
import hashlib
import redis.asyncio as redis
from datetime import datetime
from circuitbreaker import circuit
from omnicore_engine.retry_compat import retry
import uuid
import logging
import re
import shlex

# OmniCore imports
from omnicore_engine.core import logger, safe_serialize, settings # Import logger, safe_serialize, and settings from core
try:
    from omnicore_engine.core import omnicore_engine as OmniCoreOmega_instance # Import the singleton instance
except ImportError:
    OmniCoreOmega_instance = None
from omnicore_engine.database.database import Database
try:
    from omnicore_engine.audit import ExplainAudit
except ImportError:
    ExplainAudit = None
try:
    from self_healing_import_fixer.import_fixer.fixer_ai import AIManager
except ImportError:
    AIManager = None
from omnicore_engine.message_bus.message_types import Message

# Import the message_bus_cli from message_bus.py
try:
    from omnicore_engine.message_bus import message_bus_cli, RICH_CLI_AVAILABLE
    if not RICH_CLI_AVAILABLE:
        logger.warning("Rich CLI tools not available. Message bus CLI commands will be disabled.")
    
    # Define a runner function for message_bus_cli
    def message_bus_cli_runner(args):
        """
        Runner function to bridge argparse to click commands.
        
        Note: This is a stub implementation. Full integration of Click-based
        message bus CLI with argparse requires additional work. The message bus
        CLI can still be accessed directly via the click commands.
        """
        logger.info("Message bus CLI invoked via argparse bridge")
        print("Note: Message bus CLI argparse bridge not fully implemented.")
        print("Use the message bus CLI directly via click commands for full functionality.")
        return 0
except ImportError:
    message_bus_cli = None
    message_bus_cli_runner = None
    RICH_CLI_AVAILABLE = False
    logger.warning("Message bus CLI commands (click/rich) not available. Install 'rich' and 'click'.")


# DecisionOptimizer and related mocks
# REMOVED: DecisionOptimizer import and mock
# try:
#     from app.ai_assistant.decision_optimizer import DecisionOptimizer, Task, Agent
# except ImportError:
#     logger.warning("DecisionOptimizer module not found. Optimization features will be unavailable.")
#     class DecisionOptimizer:
#         def __init__(self, *args, **kwargs): pass
#         async def prioritize_tasks(self, *args, **kwargs): return []
#         async def allocate_resources(self, *args, **kwargs): return {}
#         async def explain_decision(self, *args, **kwargs): return "Mock Explanation"
#         async def load_strategy_plugin(self, *args, **kwargs): pass
#         async def _log_event(self, *args, **kwargs): pass
#         async def __aenter__(self): return self
#         async def __aexit__(self, exc_type, exc_val, exc_tb): pass
#     class Task:
#         def __init__(self, *args, **kwargs): pass
#     class Agent:
#         def __init__(self, *args, **kwargs): pass

# BenchmarkingEngine and related mocks
# BenchmarkingEngine mock - these modules don't exist in the project
try:
    from omnicore_engine.benchmarking_engine import BenchmarkingEngine, BenchmarkProfile, ConsoleReporter, JSONReporter, MonteCarloSimulator, MultiverseSimulator
except ImportError:
    logger.warning("BenchmarkingEngine module not found. Benchmarking features will be unavailable.")
    class BenchmarkingEngine:
        def __init__(self, *args, **kwargs): pass
        async def run_benchmark(self): return []
    class BenchmarkProfile:
        def __init__(self, *args, **kwargs): pass
    class ConsoleReporter:
        def __init__(self, *args, **kwargs): pass
    class JSONReporter:
        def __init__(self, *args, **kwargs): pass
    class MonteCarloSimulator:
        def __init__(self, *args, **kwargs): pass
    class MultiverseSimulator:
        def __init__(self, *args, **kwargs): pass

# PolicyEngine mock - module doesn't exist
try:
    from arbiter.policy.core import PolicyEngine
except ImportError:
    logger.warning("PolicyEngine module not found. Policy checks will be unavailable.")
    class PolicyEngine:
        def __init__(self, *args, **kwargs): pass
        async def should_auto_learn(self, *args, **kwargs): return True, "Mock Policy: Always allowed"

# FeedbackManager mock - module doesn't exist
try:
    from arbiter.feedback import FeedbackManager
    FeedbackType = None  # Define if needed
except ImportError:
    logger.warning("FeedbackManager module not found. Feedback features will be unavailable.")
    class FeedbackManager:
        def __init__(self, *args, **kwargs): pass
        async def record_feedback(self, user_id: str, feedback_type: Any, details: Dict[str, Any]): pass
        async def log_error(self, *args, **kwargs): pass
    FeedbackType = None

# Prometheus metrics
try:
    from omnicore_engine.metrics import CLI_COMMANDS, CLI_ERRORS, REGISTRY
except ImportError:
    logger.warning("Prometheus metrics not available. CLI metrics will be disabled.")
    class MockCounter:
        def labels(self, *args, **kwargs): return self
        def inc(self, *args, **kwargs): pass
        def observe(self, *args, **kwargs): pass
    CLI_COMMANDS = MockCounter()
    CLI_ERRORS = MockCounter()
    REGISTRY = None


# Global instances for CLI context
# omnicore_engine_global_instance: Optional[OmniCoreOmega] = None # Removed, using imported singleton
system_audit_merkle_tree_global: Optional[Any] = None # This will be set from core.py's instance

def sanitize_env_vars():
    """Redacts sensitive information from environment variables."""
    sensitive_vars = ['PASSWORD', 'SECRET', 'KEY', 'TOKEN', 'DSN', 'URL', 'PASS']
    for var in os.environ:
        if any(s in var.upper() for s in sensitive_vars):
            os.environ[var] = '[REDACTED]'

def safe_command(cmd: str) -> List[str]:
    """Uses shlex to safely parse a command string into a list of arguments."""
    return shlex.split(cmd)

def validate_file_path(path: str) -> Path:
    """
    Validates that a file path is within a list of allowed directories
    to prevent path traversal attacks.
    """
    resolved = Path(path).resolve()
    # Ensure path is within allowed directories
    allowed_dirs = [Path.cwd(), Path("/tmp")]
    if not any(str(resolved).startswith(str(d)) for d in allowed_dirs):
        raise ValueError(f"Access denied to path: {path}")
    return resolved

# --- Custom Error Codes for Automation ---
EXIT_CODE_SUCCESS = 0
EXIT_CODE_GENERIC_ERROR = 1
EXIT_CODE_POLICY_DENIED = 2
EXIT_CODE_FILE_ARGUMENT_ERROR = 3
EXIT_CODE_INITIALIZATION_ERROR = 4
EXIT_CODE_VALIDATION_ERROR = 5

def main():
    sanitize_env_vars()
    parser = argparse.ArgumentParser(
        description="OmniCore Omega Pro Engine CLI with DecisionOptimizer and audit integration",
        epilog="Examples:\n"
               "  python -m app.cli optimize --task_file tasks.json --output results.json\n"
               "  python -m app.cli query-agents --filters filters.json --use_dream_mode\n"
               "  python -m app.cli audit-replay --sim_id sim1 --start_time 1624556800 --end_time 1624556900\n"
               "  python -m app.cli plugin-install --kind CUSTOM --name my_new_plugin --version 1.0.0\n"
               "  python -m app.cli message-bus publish my.topic '{\"key\": \"value\"}' --encrypt"
    )
    parser.add_argument("--host", type=str, default=settings.API_HOST, help="Host address for the FastAPI server")
    parser.add_argument("--port", type=int, default=settings.API_PORT, help="Port for the FastAPI server")
    parser.add_argument("--version", action="version", version=f"%(prog)s {settings.VERSION if hasattr(settings, 'VERSION') else '0.1.0'}")

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Serve command
    serve_parser = subparsers.add_parser("serve", help="Run the FastAPI server")
    serve_parser.add_argument("--reload", action="store_true", help="Enable auto-reloading (for development)")

    # Simulate command
    simulate_parser = subparsers.add_parser("simulate", aliases=["sim"], help="Run a simulation from a request file")
    simulate_parser.add_argument("--request_file", type=str, required=True, help="Path to the JSON/YAML request file")
    simulate_parser.add_argument("--user_id", type=str, default="system", help="User ID for policy checks")
    simulate_parser.add_argument("--output", type=str, help="Path to save results")
    simulate_parser.add_argument("--output-format", type=str, choices=['json', 'yaml', 'pretty'], default='pretty', help="Output format")

    # List-plugins command
    list_plugins_parser = subparsers.add_parser("list-plugins", aliases=["lp"], help="List available plugins")
    list_plugins_parser.add_argument("--kind", type=str, help="Optional: Filter plugins by kind (e.g., 'SCENARIO')")
    list_plugins_parser.add_argument("--output", type=str, help="Path to save results")
    list_plugins_parser.add_argument("--output-format", type=str, choices=['json', 'yaml', 'pretty'], default='pretty', help="Output format")

    # Benchmark command
    benchmark_parser = subparsers.add_parser("benchmark", aliases=["bench"], help="Run a benchmarking session from a request file")
    benchmark_parser.add_argument("--request_file", type=str, required=True, help="Path to the JSON/YAML benchmark request file")
    benchmark_parser.add_argument("--user_id", type=str, default="system", help="User ID for policy checks")
    benchmark_parser.add_argument("--output", type=str, help="Path to save results")
    benchmark_parser.add_argument("--output-format", type=str, choices=['json', 'yaml', 'pretty'], default='pretty', help="Output format")

    # REMOVED: Optimize command
    # optimize_parser = subparsers.add_parser("optimize", aliases=["opt"], help="Run DecisionOptimizer for task prioritization and allocation")
    # optimize_parser.add_argument("--task_file", type=str, required=True, help="Path to JSON/YAML file with tasks and agents")
    # optimize_parser.add_argument("--output", type=str, help="Path to save results")
    # optimize_parser.add_argument("--output-format", type=str, choices=['json', 'yaml', 'pretty'], default='pretty', help="Output format")
    # optimize_parser.add_argument("--criteria", type=str, help="Path to JSON/YAML criteria file")
    # optimize_parser.add_argument("--user_id", type=str, default="system", help="User ID for policy checks")

    # REMOVED: Stream-events command
    # stream_parser = subparsers.add_parser("stream-events", aliases=["stream"], help="Stream DecisionOptimizer events in real-time")
    # stream_parser.add_argument("--output", type=str, help="Path to save streamed events (JSONL)")
    # stream_parser.add_argument("--duration", type=int, default=60, help="Duration to stream events (seconds)")

    # REMOVED: Explain-decision command
    # explain_parser = subparsers.add_parser("explain-decision", aliases=["exp"], help="Retrieve explanation for a specific decision")
    # explain_parser.add_argument("--decision_id", type=str, required=True, help="Decision ID to explain")
    # explain_parser.add_argument("--output", type=str, help="Path to save results")
    # explain_parser.add_argument("--output-format", type=str, choices=['json', 'yaml', 'pretty'], default='pretty', help="Output format")

    # REMOVED: Load-strategy command
    # strategy_parser = subparsers.add_parser("load-strategy", aliases=["ls"], help="Load a custom strategy plugin for DecisionOptimizer")
    # strategy_parser.add_argument("--kind", type=str, required=True, help="Plugin kind (e.g., CUSTOM)")
    # strategy_parser.add_argument("--name", type=str, required=True, help="Plugin name")
    # strategy_parser.add_argument("--strategy_type", type=str, required=True, help="Strategy type (prioritizer, allocator, coordinator)")

    # Query-agents command
    query_agents_parser = subparsers.add_parser("query-agents", aliases=["qa"], help="Query agent states with filters")
    query_agents_parser.add_argument("--filters", type=str, help="JSON/YAML file with filters (e.g., agent_type, custom_attributes)")
    # REMOVED: query_agents_parser.add_argument("--use_dream_mode", action="store_true", help="Use DreamMode for AI-driven filtering")
    query_agents_parser.add_argument("--output", type=str, help="Path to save results")
    query_agents_parser.add_argument("--output-format", type=str, choices=['json', 'yaml', 'pretty'], default='pretty', help="Output format")
    query_agents_parser.add_argument("--user_id", type=str, default="system", help="User ID for policy checks")

    # Snapshot-world command
    snapshot_world_parser = subparsers.add_parser("snapshot-world", aliases=["sw"], help="Snapshot current world state (all agent states)")
    snapshot_world_parser.add_argument("--user_id", type=str, required=True, help="User ID for policy checks")
    snapshot_world_parser.add_argument("--output", type=str, help="Path to save snapshot ID")
    snapshot_world_parser.add_argument("--output-format", type=str, choices=['json', 'yaml', 'pretty'], default='pretty', help="Output format")

    # Restore-world command
    restore_world_parser = subparsers.add_parser("restore-world", aliases=["rw"], help="Restore world state from a snapshot")
    restore_world_parser.add_argument("--snapshot_id", type=str, required=True, help="Snapshot ID")
    restore_world_parser.add_argument("--user_id", type=str, required=True, help="User ID for policy checks")

    # Audit-query command
    audit_query_parser = subparsers.add_parser("audit-query", aliases=["aq"], help="Query audit records with filters")
    audit_query_parser.add_argument("--filters", type=str, help="JSON/YAML file with filters (e.g., kind, sim_id, agent_id)")
    # REMOVED: audit_query_parser.add_argument("--use_dream_mode", action="store_true", help="Use DreamMode for AI-driven filtering")
    audit_query_parser.add_argument("--output", type=str, help="Path to save results")
    audit_query_parser.add_argument("--output-format", type=str, choices=['json', 'yaml', 'pretty'], default='pretty', help="Output format")
    audit_query_parser.add_argument("--user_id", type=str, default="system", help="User ID for policy checks")

    # Audit-snapshot command
    audit_snapshot_parser = subparsers.add_parser("audit-snapshot", aliases=["as"], help="Snapshot current audit state for compliance")
    audit_snapshot_parser.add_argument("--user_id", type=str, required=True, help="User ID for policy checks")
    audit_snapshot_parser.add_argument("--output", type=str, help="Path to save snapshot ID")
    audit_snapshot_parser.add_argument("--output-format", type=str, choices=['json', 'yaml', 'pretty'], default='pretty', help="Output format")

    # Audit-replay command
    audit_replay_parser = subparsers.add_parser("audit-replay", aliases=["ar"], help="Replay audit events for troubleshooting or compliance")
    audit_replay_parser.add_argument("--sim_id", type=str, required=True, help="Simulation ID")
    audit_replay_parser.add_argument("--start_time", type=float, required=True, help="Start timestamp (Unix epoch)")
    audit_replay_parser.add_argument("--end_time", type=float, required=True, help="End timestamp (Unix epoch)")
    audit_replay_parser.add_argument("--output", type=str, help="Path to save results")
    audit_replay_parser.add_argument("--output-format", type=str, choices=['json', 'yaml', 'pretty'], default='pretty', help="Output format")
    audit_replay_parser.add_argument("--user_id", type=str, default="system", help="User ID for policy checks")

    # --- NEW CLI COMMANDS ---

    # Workflow command
    workflow_parser = subparsers.add_parser("workflow", aliases=["wf"], help="Trigger the Generator-to-SFE workflow from an input file")
    workflow_parser.add_argument("--input_file", type=str, required=True, help="Path to the input requirements file (e.g., README.md)")
    workflow_parser.add_argument("--user_id", type=str, default="system", help="User ID for policy checks")

    # Debug-info command
    debug_info_parser = subparsers.add_parser("debug-info", aliases=["dbg"], help="Display system and backend debug information")
    debug_info_parser.add_argument("--output", type=str, help="Path to save results")
    debug_info_parser.add_argument("--output-format", type=str, choices=['json', 'yaml', 'pretty'], default='pretty', help="Output format")


    # Plugin-install command
    plugin_install_parser = subparsers.add_parser("plugin-install", aliases=["pi"], help="Install a plugin from the marketplace")
    plugin_install_parser.add_argument("--kind", type=str, required=True, help="Kind of the plugin (e.g., 'CUSTOM', 'SIM_ENGINE')")
    plugin_install_parser.add_argument("--name", type=str, required=True, help="Name of the plugin to install")
    plugin_install_parser.add_argument("--version", type=str, required=True, help="Version of the plugin to install")
    plugin_install_parser.add_argument("--user_id", type=str, default="system", help="User ID for policy checks")

    # Plugin-rate command
    plugin_rate_parser = subparsers.add_parser("plugin-rate", aliases=["pr"], help="Rate an installed plugin")
    plugin_rate_parser.add_argument("--kind", type=str, required=True, help="Kind of the plugin")
    plugin_rate_parser.add_argument("--name", type=str, required=True, help="Name of the plugin")
    plugin_rate_parser.add_argument("--version", type=str, required=True, help="Version of the plugin")
    plugin_rate_parser.add_argument("--rating", type=int, choices=range(1, 6), required=True, help="Rating from 1 (bad) to 5 (excellent)")
    plugin_rate_parser.add_argument("--comment", type=str, default="", help="Optional comment about the rating")
    plugin_rate_parser.add_argument("--user_id", type=str, default="system", help="User ID for policy checks")

    # Metrics-status command
    metrics_status_parser = subparsers.add_parser("metrics-status", aliases=["ms"], help="Display current Prometheus metrics")
    metrics_status_parser.add_argument("--output", type=str, help="Path to save metrics")


    # Feature-flag-set command
    feature_flag_set_parser = subparsers.add_parser("feature-flag-set", aliases=["ffs"], help="Toggle a feature flag")
    feature_flag_set_parser.add_argument("--flag_name", type=str, required=True, help="Name of the feature flag")
    feature_flag_set_parser.add_argument("--value", type=str, choices=['true', 'false'], required=True, help="Set flag to 'true' or 'false'")
    feature_flag_set_parser.add_argument("--user_id", type=str, default="system", help="User ID for policy checks")

    # Generate-test-cases command
    generate_test_cases_parser = subparsers.add_parser("generate-test-cases", aliases=["gtc"], help="Generate test cases from audit logs")
    generate_test_cases_parser.add_argument("--output", type=str, help="Path to save generated test cases")
    generate_test_cases_parser.add_argument("--output-format", type=str, choices=['json', 'yaml', 'pretty'], default='pretty', help="Output format")
    generate_test_cases_parser.add_argument("--user_id", type=str, default="system", help="User ID for policy checks")

    # Docs autogen command
    docs_parser = subparsers.add_parser("docs", help="Generate Markdown documentation for CLI commands")
    docs_parser.add_argument("--output", type=str, help="Path to save the generated Markdown documentation")

    # REPL mode
    repl_parser = subparsers.add_parser("repl", aliases=["shell"], help="Enter an interactive shell (Read-Eval-Print Loop)")

    # Fix imports command
    fixer_parser = subparsers.add_parser("fix-imports", help="Use the AI-powered fixer to suggest refactoring for imports in a file.")
    fixer_parser.add_argument("target_path", type=str, help="Path to the file to be fixed.")

    # --- Integrate message_bus_cli (Click-based) ---
    if RICH_CLI_AVAILABLE and message_bus_cli:
        # Add a subparser that will delegate to the click command
        message_bus_subparser = subparsers.add_parser("message-bus", help="Commands for interacting with the message bus (publish, monitor, etc.)")
        message_bus_subparser.set_defaults(func=message_bus_cli_runner) # Set a default function to call

    args = parser.parse_args()
    
    # Initialize FeedbackManager and PolicyEngine for CLI context
    feedback_manager_cli = FeedbackManager(config=settings)
    policy_engine_cli = PolicyEngine(settings=settings)

    # Initialize MerkleTree for CLI audit operations
    try:
        from omnicore_engine.merkle_tree import MerkleTree
        system_audit_merkle_tree_cli = MerkleTree()
    except ImportError:
        logger.warning("MerkleTree not found for CLI audit operations. Mocking.")
        class MockMerkleTreeCLI:
            def __init__(self, *args, **kwargs): pass
            def get_root(self): return b"mock_root"
            def get_merkle_root(self): return "mock_root_hex"
            def add_leaf(self, *args): pass
            def _recalculate_root(self): pass
        system_audit_merkle_tree_cli = MockMerkleTreeCLI()

    audit_cli_instance = ExplainAudit(system_audit_merkle_tree=system_audit_merkle_tree_cli)

    async def load_file(file_path: str) -> Dict[str, Any]:
        """Load and validate JSON/YAML file with sanitization."""
        try:
            validated_path = validate_file_path(file_path)
            if not validated_path.exists():
                logger.error(f"File not found: {file_path}")
                sys.exit(EXIT_CODE_FILE_ARGUMENT_ERROR)
            if validated_path.suffix not in ['.json', '.yaml', '.yml']:
                logger.error(f"Invalid file extension: {file_path}. Use JSON or YAML.")
                sys.exit(EXIT_CODE_FILE_ARGUMENT_ERROR)
            with open(validated_path, 'r') as f:
                if validated_path.suffix in ['.yaml', '.yml']:
                    return yaml.safe_load(f)
                return json.load(f)
        except (json.JSONDecodeError, yaml.YAMLError) as e:
            logger.error(f"Failed to parse file '{file_path}': {e}")
            sys.exit(EXIT_CODE_FILE_ARGUMENT_ERROR)
        except ValueError as e:
            logger.error(f"File path validation failed: {e}")
            sys.exit(EXIT_CODE_FILE_ARGUMENT_ERROR)

    async def anonymize_data(data: Dict[str, Any], user_id: str) -> Dict[str, Any]:
        """Anonymize user-related data for GDPR compliance."""
        if 'user_id' in data:
            data['user_id'] = hashlib.sha256(data['user_id'].encode()).hexdigest()
        if 'name' in data:
            data['name'] = hashlib.sha256(data['name'].encode()).hexdigest()
        if 'agent_id' in data:
            data['agent_id'] = hashlib.sha256(data['agent_id'].encode()).hexdigest()
        return data

    def print_output(data: Any, output_path: Optional[str], output_format: str, has_output_data: bool = True):
        """
        Prints data to console or file in specified format.
        Args:
            data (Any): The data to print.
            output_path (Optional[str]): Path to save the output.
            output_format (str): Desired output format ('json', 'yaml', 'pretty').
            has_output_data (bool): True if the command is expected to produce data output.
        """
        if not has_output_data and (output_path or output_format != 'pretty'):
            logger.warning(f"Command does not produce structured data output. Ignoring --output and --output-format options.")
            return

        serialized_data = safe_serialize(data)
        
        output_string = ""
        if output_format == 'json':
            output_string = json.dumps(serialized_data, indent=2)
        elif output_format == 'yaml':
            output_string = yaml.safe_dump(serialized_data, indent=2)
        else: # 'pretty'
            output_string = json.dumps(serialized_data, indent=2) # Default to pretty JSON

        if output_path:
            try:
                validated_path = validate_file_path(output_path)
                with open(validated_path, 'w') as f:
                    f.write(output_string)
                logger.info(f"Output saved to {output_path} in {output_format} format.")
            except ValueError as e:
                logger.error(f"Invalid output path: {e}")
                sys.exit(EXIT_CODE_FILE_ARGUMENT_ERROR)
        else:
            print(output_string)

    @circuit(failure_threshold=5, recovery_timeout=60)
    @retry(tries=3, delay=1, backoff=2)
    async def run_with_policy_check(coro_func: Callable[[argparse.Namespace], Coroutine], parsed_args: argparse.Namespace) -> Any:
        """
        Runs a coroutine function with policy checks, audit logging, and error handling.
        The coroutine function must accept a single `argparse.Namespace` object as its argument.
        """
        action = parsed_args.command
        user_id = getattr(parsed_args, 'user_id', 'system')
        metadata = vars(parsed_args)
        
        CLI_COMMANDS.labels(command=action).inc()
        start_time_cli = time.time()
        
        allowed, reason = await policy_engine_cli.should_auto_learn('CLI', action, user_id, metadata)
        if not allowed:
            logger.error(f"Policy denied for {action}: {reason}")
            sys.exit(EXIT_CODE_POLICY_DENIED)
        try:
            result = await coro_func(parsed_args)
            await feedback_manager_cli.record_feedback(
                user_id=user_id,
                feedback_type=FeedbackType.GENERAL,
                details={'event': f'cli_{action}_success', 'metadata': metadata}
            )
            await audit_cli_instance.add_entry_async(
                kind='cli', name=action, detail={'metadata': metadata, 'result': 'success'},
                sim_id=str(uuid.uuid4()), agent_id=user_id
            )
            async with redis.from_url(settings.REDIS_URL, decode_responses=True) as redis_client:
                await redis_client.publish('cli_events', json.dumps({
                    'event_type': action, 'user_id': hashlib.sha256(user_id.encode()).hexdigest(),
                    'timestamp': datetime.utcnow().isoformat()
                }))
            return result
        except Exception as e:
            CLI_ERRORS.labels(command=action).observe(time.time() - start_time_cli)
            logger.error(f"Command {action} failed: {e}", exc_info=True)
            await feedback_manager_cli.record_feedback(
                user_id=user_id,
                feedback_type=FeedbackType.BUG_REPORT,
                details={'event': f'cli_{action}_error', 'error': str(e), 'metadata': metadata}
            )
            await audit_cli_instance.add_entry_async(
                kind='cli', name=action, detail={'metadata': metadata, 'error': str(e)},
                sim_id=str(uuid.uuid4()), agent_id=user_id, error=str(e)
            )
            sys.exit(EXIT_CODE_GENERIC_ERROR)

    async def _initialize_omnicore_engine():
        if not OmniCoreOmega_instance.is_initialized:
            logger.info("Initializing OmniCore Engine for CLI command...")
            await OmniCoreOmega_instance.initialize()
            if not OmniCoreOmega_instance.is_initialized:
                logger.critical("OmniCore Engine initialization failed.")
                sys.exit(EXIT_CODE_INITIALIZATION_ERROR)
        return OmniCoreOmega_instance

    async def _run_simulate_cmd(current_args: argparse.Namespace):
        data = await load_file(current_args.request_file)
        data = await anonymize_data(data, current_args.user_id)
        engine = await _initialize_omnicore_engine()
        try:
            from omnicore_engine.simulation import SimRequest
        except ImportError:
            logger.error("SimRequest not found. Simulation unavailable.")
            return {"error": "Simulation module not available"}
        sim_request = SimRequest(**data)
        return await engine.simulate(sim_request)

    async def _run_list_plugins_cmd(current_args: argparse.Namespace):
        engine = await _initialize_omnicore_engine()
        return engine.plugin_registry.get_plugin_names(current_args.kind.upper() if current_args.kind else None)

    async def _run_benchmark_cmd(current_args: argparse.Namespace):
        data = await load_file(current_args.request_file)
        data = await anonymize_data(data, current_args.user_id)
        engine_instance = await _initialize_omnicore_engine()

        financial_plugin = engine_instance.plugin_registry.get('CORE_SERVICE', 'financial_engine')
        
        functions = [
            lambda x: x * 2,
        ]
        if financial_plugin and hasattr(financial_plugin, 'execute'):
            functions.append(financial_plugin.execute)
        else:
            logger.warning("Financial engine plugin not found for benchmarking. Using dummy function.")
            functions.append(lambda x: x)

        # BenchmarkingEngine already imported at top with fallback mock
        # No need for duplicate import here

        profiles = [
            BenchmarkProfile(
                name=p.get("name", "default"),
                input_data=p.get("input_data", 100),
                description=p.get("description", ""),
                validator=None
            ) for p in data.get("profiles", [])
        ]
        sim_plugins = []
        try:
            for sim_name in data.get("simulation_plugins", []):
                if sim_name == "monte_carlo":
                    sim_plugins.append(MonteCarloSimulator(runs=10))
                elif sim_name == "multiverse":
                    def universe_A_gen(x): return {"type": "Optimistic", "value": x * 2}
                    def universe_B_gen(x): return {"type": "Pessimistic", "value": x // 2}
                    sim_plugins.append(MultiverseSimulator(generators=[universe_A_gen, universe_B_gen]))
        except ImportError:
            logger.warning("Benchmarking simulation plugins (MonteCarloSimulator, MultiverseSimulator) not found.")

        bench_engine = BenchmarkingEngine(
            functions=functions,
            profiles=profiles,
            iterations_per_run=data.get("iterations", 20),
            warmup_iterations_per_run=data.get("warmup_iterations", 5),
            reporters=[ConsoleReporter(), JSONReporter(current_args.output or "benchmark_results.json")],
            enable_ai_scenarios=data.get("enable_ai_scenarios", False),
            enable_adversarial_testing=data.get("enable_adversarial_testing", False),
            enable_c_profiling=data.get("enable_c_profiling", True),
            simulation_plugins=sim_plugins,
            settings=settings
        )
        await bench_engine.run_benchmark()
        return bench_engine.all_results

    # REMOVED: _run_optimize_cmd
    # async def _run_optimize_cmd(current_args: argparse.Namespace):
    #     data = await load_file(current_args.task_file)
    #     data = await anonymize_data(data, current_args.user_id)
    #     criteria = await load_file(current_args.criteria) if current_args.criteria else {}
    #     engine_instance = await _initialize_omnicore_engine()
    #     from app.ai_assistant.decision_optimizer import DecisionOptimizer, Task, Agent
    #     from app.omnicore_engine.plugin_registry import PLUGIN_REGISTRY # Ensure PLUGIN_REGISTRY is imported for DecisionOptimizer
    #     async with DecisionOptimizer(PLUGIN_REGISTRY=PLUGIN_REGISTRY, settings=settings, logger=logger, safe_serialize_func=safe_serialize) as optimizer: # Pass PLUGIN_REGISTRY
    #         tasks = [Task(**t) for t in data.get('tasks', [])]
    #         agents = [Agent(**a) for a in data.get('agents', [])]
    #         prioritized = await optimizer.prioritize_tasks(agents, tasks, criteria)
    #         assignments = await optimizer.allocate_resources(agents, prioritized)
    #         result = {'prioritized': [t.model_dump() if isinstance(t, BaseModel) else safe_serialize(t) for t in prioritized],
    #                       'assignments': {k: [a.model_dump() if isinstance(a, BaseModel) else safe_serialize(a) for a in v] for k, v in assignments.items()}}
    #         return result

    # REMOVED: _run_stream_events_cmd
    # async def _run_stream_events_cmd(current_args: argparse.Namespace):
    #     engine_instance = await _initialize_omnicore_engine()
    #     from app.ai_assistant.decision_optimizer import DecisionOptimizer
    #     from app.omnicore_engine.plugin_registry import PLUGIN_REGISTRY
    #     async with DecisionOptimizer(PLUGIN_REGISTRY=PLUGIN_REGISTRY, settings=settings, logger=logger, safe_serialize_func=safe_serialize) as optimizer:
    #         output_file = open(current_args.output, 'a') if current_args.output else None
    #         try:
    #             async def stream_to_console():
    #                 async with redis.from_url(settings.REDIS_URL, decode_responses=True) as redis_client:
    #                     pubsub = redis_client.pubsub()
    #                     await pubsub.subscribe('decision_optimizer_events', 'cli_events')
    #                     start_time_stream = time.time()
    #                     async for message in pubsub.listen():
    #                         if message['type'] == 'message':
    #                                 event = json.loads(message['data'])
    #                             print(json.dumps(safe_serialize(event), indent=2))
    #                             if output_file:
    #                                 output_file.write(json.dumps(safe_serialize(event)) + '\n')
    #                                 output_file.flush()
    #                         if time.time() - start_time_stream > current_args.duration:
    #                             break
    #             await stream_to_console()
    #         finally:
    #             if output_file:
    #                 output_file.close()

    # REMOVED: _run_explain_decision_cmd
    # async def _run_explain_decision_cmd(current_args: argparse.Namespace):
    #     engine_instance = await _initialize_omnicore_engine()
    #     from app.ai_assistant.decision_optimizer import DecisionOptimizer
    #     from app.omnicore_engine.plugin_registry import PLUGIN_REGISTRY
    #     async with DecisionOptimizer(PLUGIN_REGISTRY=PLUGIN_REGISTRY, settings=settings, logger=logger, safe_serialize_func=safe_serialize) as optimizer:
    #         explanation = await optimizer.explain_decision(current_args.decision_id)
    #         return explanation

    # REMOVED: _run_load_strategy_cmd
    # async def _run_load_strategy_cmd(current_args: argparse.Namespace):
    #     engine_instance = await _initialize_omnicore_engine()
    #     from app.ai_assistant.decision_optimizer import DecisionOptimizer
    #     from app.omnicore_engine.plugin_registry import PLUGIN_REGISTRY
    #     async with DecisionOptimizer(PLUGIN_REGISTRY=PLUGIN_REGISTRY, settings=settings, logger=logger, safe_serialize_func=safe_serialize) as optimizer:
    #         await optimizer.load_strategy_plugin(current_args.kind, current_args.name, current_args.strategy_type)
    #         return {"message": f"Loaded {current_args.kind}:{current_args.name} as {current_args.strategy_type}"}

    async def _run_query_agents_cmd(current_args: argparse.Namespace):
        filters = await load_file(current_args.filters) if current_args.filters else {}
        filters = await anonymize_data(filters, current_args.user_id)
        engine_instance = await _initialize_omnicore_engine()
        db = engine_instance.database
        # REMOVED: use_dream_mode argument
        states = await db.query_agent_states(filters)
        return states

    async def _run_snapshot_world_cmd(current_args: argparse.Namespace):
        engine_instance = await _initialize_omnicore_engine()
        db = engine_instance.database
        snapshot_id = await db.snapshot_world_state(current_args.user_id)
        return {"snapshot_id": snapshot_id}

    async def _run_restore_world_cmd(current_args: argparse.Namespace):
        engine_instance = await _initialize_omnicore_engine()
        db = engine_instance.database
        await db.restore_world_state(current_args.snapshot_id, current_args.user_id)
        return {"message": f"World state restored from snapshot {current_args.snapshot_id}"}

    async def _run_audit_query_cmd(current_args: argparse.Namespace):
        filters = await load_file(current_args.filters) if current_args.filters else {}
        engine_instance = await _initialize_omnicore_engine()
        audit = engine_instance.audit
        # REMOVED: use_dream_mode argument
        records = await audit.query_audit_records(filters)
        return records

    async def _run_audit_snapshot_cmd(current_args: argparse.Namespace):
        engine_instance = await _initialize_omnicore_engine()
        audit = engine_instance.audit
        snapshot_id = await audit.snapshot_audit_state(current_args.user_id)
        return {"snapshot_id": snapshot_id}

    async def _run_audit_replay_cmd(current_args: argparse.Namespace):
        engine_instance = await _initialize_omnicore_engine()
        audit = engine_instance.audit
        records = await audit.replay_events(current_args.sim_id, current_args.start_time, current_args.end_time, current_args.user_id)
        return records

    async def _run_workflow_cmd(current_args: argparse.Namespace):
        engine = await _initialize_omnicore_engine()
        input_file_path = validate_file_path(current_args.input_file)

        if not input_file_path.exists():
            logger.error(f"Error: Input file {input_file_path} does not exist")
            sys.exit(EXIT_CODE_FILE_ARGUMENT_ERROR)

        message = Message(
            topic="start_workflow",
            payload={
                "requirements": input_file_path.read_text(),
                "config": {"model": "grok"}
            }
        )
        await engine.components["message_bus"].publish(message.topic, message.payload)
        
        result_message = f"Started Code Factory workflow with trace_id: {message.trace_id}"
        logger.info(result_message)
        return {"message": result_message, "trace_id": str(message.trace_id)}

    async def _run_debug_info_cmd(current_args: argparse.Namespace):
        engine = await _initialize_omnicore_engine()
        
        array_backend_mode = "N/A"
        # Access array_backend mode directly from the engine instance
        if hasattr(engine, 'array_backend') and hasattr(engine.array_backend, 'mode'):
            array_backend_mode = engine.array_backend.mode
        else:
            logger.warning("Could not determine array backend mode from engine instance.")

        info = {
            "system_version": settings.VERSION if hasattr(settings, 'VERSION') else '0.1.0', # Use settings.VERSION
            "api_host": settings.API_HOST,
            "api_port": settings.API_PORT,
            "db_path": settings.DATABASE_URL,
            "log_level": settings.LOG_LEVEL,
            "plugins_loaded": engine.plugin_registry.get_plugin_names(),
            # "feature_flags": engine.orchestrator.feature_flags.flags, # Assuming orchestrator is part of engine
            "active_backends": {
                "array_backend_mode": array_backend_mode,
                "numpy_available": True,
                "cupy_available": hasattr(sys.modules.get('cupy'), '__version__') if 'cupy' in sys.modules else False,
                "dask_available": hasattr(sys.modules.get('dask.array'), '__version__') if 'dask.array' in sys.modules else False,
                "torch_available": hasattr(sys.modules.get('torch'), '__version__') if 'torch' in sys.modules else False,
                "qiskit_available": hasattr(sys.modules.get('qiskit'), '__version__') if 'qiskit' in sys.modules else False,
                "nengo_loihi_available": hasattr(sys.modules.get('nengo_loihi'), '__version__') if 'nengo_loihi' in sys.modules else False,
            },
            "merkle_tree_root": system_audit_merkle_tree_cli.get_merkle_root() if system_audit_merkle_tree_cli else "N/A",
            "message_bus_health": await engine.components["message_bus"].health_check() if "message_bus" in engine.components else "N/A" # Access health_check via component wrapper
        }
        return info

    async def _run_plugin_install_cmd(current_args: argparse.Namespace):
        engine_instance = await _initialize_omnicore_engine()
        try:
            from omnicore_engine.plugin_registry import PluginMarketplace, PluginVersionManager
        except ImportError:
            logger.error("PluginMarketplace and PluginVersionManager not available")
            return {"error": "Plugin marketplace not available"}
        # Initialize PluginVersionManager explicitly if not already a direct property of engine_instance
        # This assumes engine_instance has attributes like database and audit
        plugin_version_manager = PluginVersionManager(
            registry=engine_instance.plugin_registry,
            db=engine_instance.database,
            audit_client=engine_instance.audit
        )
        marketplace = PluginMarketplace(
            db=engine_instance.database,
            audit_client=engine_instance.audit
        )
        await marketplace.install_plugin(current_args.kind, current_args.name, current_args.version)
        return {"message": f"Plugin {current_args.name} (v{current_args.version}, kind: {current_args.kind}) installed successfully."}

    async def _run_plugin_rate_cmd(current_args: argparse.Namespace):
        engine_instance = await _initialize_omnicore_engine()
        try:
            from omnicore_engine.plugin_registry import PluginMarketplace, PluginVersionManager
        except ImportError:
            logger.error("PluginMarketplace and PluginVersionManager not available")
            return {"error": "Plugin marketplace not available"}
        plugin_version_manager = PluginVersionManager(
            registry=engine_instance.plugin_registry,
            db=engine_instance.database,
            audit_client=engine_instance.audit
        )
        marketplace = PluginMarketplace(
            db=engine_instance.database,
            audit_client=engine_instance.audit
        )
        
        await marketplace.rate_plugin(
            current_args.kind, current_args.name, current_args.version, 
            current_args.rating, current_args.comment, current_args.user_id
        )
        return {"message": f"Plugin {current_args.name} (v{current_args.version}) rated {current_args.rating}/5 by {current_args.user_id}."}


    async def _run_metrics_status_cmd(current_args: argparse.Namespace):
        from prometheus_client import generate_latest
        metrics_text = generate_latest().decode('utf-8')
        if current_args.output:
            try:
                validated_path = validate_file_path(current_args.output)
                with open(validated_path, 'w') as f:
                    f.write(metrics_text)
                logger.info(f"Metrics saved to {current_args.output}")
            except ValueError as e:
                logger.error(f"Invalid output path: {e}")
                sys.exit(EXIT_CODE_FILE_ARGUMENT_ERROR)
        else:
            print(metrics_text)
        return None

    async def _run_feature_flag_set_cmd(current_args: argparse.Namespace):
        engine_instance = await _initialize_omnicore_engine()
        value = True if current_args.value == 'true' else False
        setattr(settings, current_args.flag_name.upper(), value)
        logger.info(f"Feature flag '{current_args.flag_name}' set to {value} in settings.")
        return {"message": f"Feature flag '{current_args.flag_name}' set to {value}."}

    async def _run_generate_test_cases_cmd(current_args: argparse.Namespace):
        engine_instance = await _initialize_omnicore_engine()
        try:
            from omnicore_engine.snapshot_manager import SnapshotManager
            snapshot_manager = SnapshotManager(db=engine_instance.database, audit_client=engine_instance.audit)
            test_cases = await snapshot_manager.generate_test_cases()
            return test_cases
        except ImportError:
            logger.error("SnapshotManager not found. Cannot generate test cases.")
            return {"error": "SnapshotManager not available."}
        except Exception as e:
            logger.error(f"Error generating test cases: {e}", exc_info=True)
            return {"error": str(e)}


    async def _run_docs_autogen_cmd(current_args: argparse.Namespace):
        docs_content = "# OmniCore Omega CLI Commands\n\n"
        docs_content += "This document is auto-generated from the CLI's argparse definitions.\n\n"
        
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                for cmd_name, subparser_obj in action.choices.items():
                    docs_content += f"## `{cmd_name}`\n\n"
                    docs_content += f"**Description**: {subparser_obj.description}\n\n"
                    
                    if subparser_obj.aliases:
                        docs_content += f"**Aliases**: {', '.join([f'`{a}`' for a in subparser_obj.aliases])}\n\n"

                    docs_content += "**Arguments**:\n\n"
                    
                    for sub_action in subparser_obj._actions:
                        if sub_action.dest != argparse.SUPPRESS and sub_action.dest != 'help':
                            arg_name = sub_action.option_strings[0] if sub_action.option_strings else sub_action.dest
                            arg_help = sub_action.help if sub_action.help else ""
                            arg_default = f" (default: `{sub_action.default}`)" if sub_action.default is not argparse.SUPPRESS and sub_action.default is not None else ""
                            arg_required = " (required)" if sub_action.required else ""
                            arg_choices = f" (choices: `{', '.join(map(str, sub_action.choices))}`)" if sub_action.choices else ""
                            
                            docs_content += f"- `{arg_name}`: {arg_help}{arg_required}{arg_choices}{arg_default}\n"
                    docs_content += "\n"
                    
                    if subparser_obj.epilog:
                        docs_content += "**Examples**:\n"
                        examples = re.sub(r'^(Examples:\s*\n)?', '', subparser_obj.epilog.strip(), flags=re.IGNORECASE)
                        docs_content += f"```bash\n{examples}\n```\n\n"
        
        if current_args.output:
            try:
                validated_path = validate_file_path(current_args.output)
                with open(validated_path, 'w') as f:
                    f.write(docs_content)
                logger.info(f"CLI documentation saved to {current_args.output}")
            except ValueError as e:
                logger.error(f"Invalid output path: {e}")
                sys.exit(EXIT_CODE_FILE_ARGUMENT_ERROR)
        else:
            print(docs_content)
        return {"message": "CLI documentation generated."}

    async def message_bus_cli_runner(current_args: argparse.Namespace):
        if not RICH_CLI_AVAILABLE or not message_bus_cli:
            logger.error("Message bus CLI commands are not available. Please install 'rich' and 'click'.")
            sys.exit(EXIT_CODE_GENERIC_ERROR)
        
        # Initialize the core engine before running the message-bus command
        await _initialize_omnicore_engine()

        try:
            message_bus_cmd_index = sys.argv.index('message-bus')
        except ValueError:
            logger.error("Internal error: 'message-bus' command not found in sys.argv.")
            sys.exit(EXIT_CODE_GENERIC_ERROR)

        # The click library expects sys.argv to be structured for it.
        # We need to construct the correct arguments to pass to the click command.
        click_args = sys.argv[message_bus_cmd_index + 1:]
        
        original_sys_argv = sys.argv
        sys.argv = [original_sys_argv[0], *click_args]
        
        try:
            message_bus_cli.main(args=click_args, standalone_mode=False)
        except SystemExit as e:
            sys.exit(e.code)
        finally:
            sys.argv = original_sys_argv

    async def _run_fix_imports_cmd(current_args: argparse.Namespace):
        # The fixer command handler
        try:
            validated_path = validate_file_path(current_args.target_path)
        except ValueError as e:
            logger.error(f"Invalid file path: {e}")
            sys.exit(EXIT_CODE_FILE_ARGUMENT_ERROR)

        if not validated_path.exists():
            logger.error(f"File not found: {validated_path}")
            sys.exit(EXIT_CODE_FILE_ARGUMENT_ERROR)

        ai = AIManager()
        with open(validated_path, "r") as f:
            code = f.read()

        suggestion = ai.get_refactoring_suggestion(code)
        
        print(f"AI Refactoring Suggestion for {current_args.target_path}:\n")
        print(suggestion)
        return {"suggestion": suggestion}

    command_handlers: Dict[str, Tuple[Callable[[argparse.Namespace], Coroutine], bool]] = {
        "simulate": (_run_simulate_cmd, True), "sim": (_run_simulate_cmd, True),
        "list-plugins": (_run_list_plugins_cmd, True), "lp": (_run_list_plugins_cmd, True),
        "benchmark": (_run_benchmark_cmd, True), "bench": (_run_benchmark_cmd, True),
        # REMOVED: "optimize": (_run_optimize_cmd, True), "opt": (_run_optimize_cmd, True),
        # REMOVED: "stream-events": (_run_stream_events_cmd, False), "stream": (_run_stream_events_cmd, False),
        # REMOVED: "explain-decision": (_run_explain_decision_cmd, True), "exp": (_run_explain_decision_cmd, True),
        # REMOVED: "load-strategy": (_run_load_strategy_cmd, False), "ls": (_run_load_strategy_cmd, False),
        "query-agents": (_run_query_agents_cmd, True), "qa": (_run_query_agents_cmd, True),
        "snapshot-world": (_run_snapshot_world_cmd, True), "sw": (_run_snapshot_world_cmd, True),
        "restore-world": (_run_restore_world_cmd, False), "rw": (_run_restore_world_cmd, False),
        "audit-query": (_run_audit_query_cmd, True), "aq": (_run_audit_query_cmd, True),
        "audit-snapshot": (_run_audit_snapshot_cmd, True), "as": (_run_audit_snapshot_cmd, True),
        "audit-replay": (_run_audit_replay_cmd, True), "ar": (_run_audit_replay_cmd, True),
        "workflow": (_run_workflow_cmd, True), "wf": (_run_workflow_cmd, True),
        "debug-info": (_run_debug_info_cmd, True), "dbg": (_run_debug_info_cmd, True),
        "plugin-install": (_run_plugin_install_cmd, False), "pi": (_run_plugin_install_cmd, False),
        "plugin-rate": (_run_plugin_rate_cmd, False), "pr": (_run_plugin_rate_cmd, False),
        "metrics-status": (_run_metrics_status_cmd, False), "ms": (_run_metrics_status_cmd, False),
        "feature-flag-set": (_run_feature_flag_set_cmd, False), "ffs": (_run_feature_flag_set_cmd, False),
        "generate-test-cases": (_run_generate_test_cases_cmd, True), "gtc": (_run_generate_test_cases_cmd, True),
        "docs": (_run_docs_autogen_cmd, False),
        "fix-imports": (_run_fix_imports_cmd, True),
    }

    if RICH_CLI_AVAILABLE and message_bus_cli:
        command_handlers["message-bus"] = (message_bus_cli_runner, False)

    if args.command == "serve":
        CLI_COMMANDS.labels(command='serve').inc()
        logger.info(f"Starting Uvicorn server on http://{args.host}:{args.port}")
        uvicorn.run("app.fastapi_app:app", host=args.host, port=args.port, reload=args.reload, log_level="info")

    elif args.command == "repl":
        async def _run_repl_mode_cmd():
            await _initialize_omnicore_engine()
            
            repl_prompt_prefix = f"omnicore[{settings.ENVIRONMENT_NAME}]> "
            
            print("Entering OmniCore Omega interactive shell. Type 'exit' to quit.")
            print("Type 'help' for available commands.")
            
            while True:
                try:
                    user_input = await asyncio.to_thread(input, repl_prompt_prefix).strip()
                    if not user_input:
                        continue
                    if user_input.lower() == "exit":
                        print("Exiting interactive shell.")
                        break
                    if user_input.lower() == "help":
                        parser.print_help()
                        continue

                    shlexed_input = safe_command(user_input)

                    if not shlexed_input:
                        continue

                    # Special handling for message-bus command in REPL
                    if RICH_CLI_AVAILABLE and message_bus_cli and shlexed_input[0] == "message-bus":
                        try:
                            # Re-run the click command with the provided arguments
                            original_sys_argv = sys.argv
                            sys.argv = [original_sys_argv[0], *shlexed_input]
                            message_bus_cli.main(args=shlexed_input[1:], standalone_mode=False)
                        except SystemExit as e:
                            if e.code != 0:
                                logger.error(f"Error in message-bus REPL command: {e}")
                        finally:
                            sys.argv = original_sys_argv
                        continue
                        
                    try:
                        repl_args = parser.parse_args(shlexed_input)
                    except SystemExit as e:
                        if e.code != 0:
                            logger.error(f"Invalid command or arguments in REPL. See command-specific help above.")
                        continue

                    command_tuple = command_handlers.get(repl_args.command)
                    
                    if command_tuple:
                        handler_func, has_structured_output = command_tuple
                        try:
                            result = await run_with_policy_check(
                                handler_func,
                                repl_args
                            )
                            
                            if result is not None:
                                print_output(
                                    result, 
                                    None,
                                    getattr(repl_args, 'output_format', 'pretty'),
                                    has_output_data=has_structured_output
                                )
                            
                        except Exception as e:
                            logger.error(f"Error executing REPL command '{repl_args.command}': {e}", exc_info=True)
                    else:
                        logger.error(f"Unknown command: '{repl_args.command}'. Type 'help' for a list of commands.")

                except EOFError:
                    print("\nExiting interactive shell.")
                    break
                except KeyboardInterrupt:
                    print("\nExiting interactive shell.")
                    break
                except SystemExit as e:
                    # Catch SystemExit from any command in the REPL
                    if e.code != 0:
                        logger.error(f"REPL command exited with error code {e.code}.")
        asyncio.run(_run_repl_mode_cmd())
        sys.exit(EXIT_CODE_SUCCESS)

    else:
        if args.command == "message-bus" and RICH_CLI_AVAILABLE and message_bus_cli:
            asyncio.run(message_bus_cli_runner(args))
            sys.exit(EXIT_CODE_SUCCESS)
        
        command_tuple = command_handlers.get(args.command)
        if command_tuple:
            handler_func, has_structured_output = command_tuple
            
            result = asyncio.run(run_with_policy_check(
                handler_func,
                args
            ))
            
            if result is not None:
                print_output(
                    result, 
                    getattr(args, 'output', None),
                    getattr(args, 'output_format', 'pretty'),
                    has_output_data=has_structured_output
                )
            sys.exit(EXIT_CODE_SUCCESS)
        else:
            parser.print_help()
            sys.exit(EXIT_CODE_FILE_ARGUMENT_ERROR)

if __name__ == "__main__":
    main()