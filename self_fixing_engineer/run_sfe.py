#!/usr/bin/env python3
# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
SFE Platform Launcher
Runs the Self-Fixing Engineer platform components without import conflicts
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path


def set_environment():
    """Set up environment variables for SFE platform"""
    env_vars = {
        "PYTHONPATH": str(Path(__file__).parent),
        "AUDIT_LOG_PATH": "./audit_trail.log",
        "APP_ENV": "development",
        "ARENA_PORT": "8000",
        "REPORTS_DIRECTORY": "./reports",
        "DB_PATH": "sqlite:///./omnicore.db",
    }

    for key, value in env_vars.items():
        if key not in os.environ:
            os.environ[key] = value

    # Create necessary directories
    os.makedirs("./reports", exist_ok=True)
    os.makedirs("./logs", exist_ok=True)


def run_arena():
    """Run the Arbiter Arena in a separate process"""
    print("=" * 60)
    print("Starting Arbiter Arena...")
    print("=" * 60)

    # Run arena.py directly to avoid import issues
    arena_path = Path(__file__).parent / "arbiter" / "arena.py"

    if not arena_path.exists():
        print(f"ERROR: Arena file not found at {arena_path}")
        return None

    # Start arena in subprocess
    process = subprocess.Popen(
        [sys.executable, str(arena_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True,
    )

    # Give it time to start
    time.sleep(2)

    # Check if it's running
    if process.poll() is None:
        print(f"✓ Arena started (PID: {process.pid})")
        print("  HTTP API available at: http://localhost:8000")
        print()
    else:
        print("✗ Arena failed to start")
        # Read any error output
        if process.stdout:
            for line in process.stdout:
                print(f"  {line.strip()}")

    return process


def run_cli():
    """Run the CLI interface"""
    print("=" * 60)
    print("Starting SFE CLI Interface...")
    print("=" * 60)

    # Run the main.py in CLI mode
    subprocess.run([sys.executable, "main.py", "--mode", "cli"])


def run_api():
    """Run the API server"""
    print("=" * 60)
    print("Starting SFE API Server...")
    print("=" * 60)

    # Run the main.py in API mode
    subprocess.run([sys.executable, "main.py", "--mode", "api", "--port", "8080"])


def run_full_platform():
    """Run the complete SFE platform"""
    print("=" * 80)
    print(" SELF-FIXING ENGINEER PLATFORM LAUNCHER")
    print("=" * 80)
    print()

    # Set up environment
    set_environment()

    processes = []

    try:
        # Start Arena first (it's the core component)
        print("[1/2] Launching Arbiter Arena...")
        arena_proc = run_arena()
        if arena_proc:
            processes.append(arena_proc)
            time.sleep(3)  # Give arena time to initialize
        else:
            print("Warning: Arena failed to start, continuing with CLI...")

        # Then start CLI for interaction
        print("[2/2] Launching CLI Interface...")
        print()
        print("Note: Press Ctrl+C to stop all components")
        print()

        # Run CLI in the main thread so it's interactive
        run_cli()

    except KeyboardInterrupt:
        print("\n\nShutting down SFE platform...")

        # Terminate all subprocess
        for proc in processes:
            if proc and proc.poll() is None:
                print(f"Stopping process {proc.pid}...")
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()

        print("SFE platform stopped.")

    except Exception as e:
        print(f"Error running platform: {e}")
        sys.exit(1)


def run_component(component):
    """Run a specific component"""
    if component == "arena":
        proc = run_arena()
        if proc:
            try:
                # Keep it running
                while proc.poll() is None:
                    line = proc.stdout.readline()
                    if line:
                        print(line.strip())
            except KeyboardInterrupt:
                print("\nStopping arena...")
                proc.terminate()

    elif component == "cli":
        run_cli()

    elif component == "api":
        run_api()

    else:
        print(f"Unknown component: {component}")
        print("Available components: arena, cli, api")


def main():
    parser = argparse.ArgumentParser(description="SFE Platform Launcher")
    parser.add_argument(
        "--component",
        choices=["arena", "cli", "api", "full"],
        default="full",
        help="Component to run (default: full platform)",
    )
    parser.add_argument(
        "--dev", action="store_true", help="Run in development mode with debug output"
    )

    args = parser.parse_args()

    if args.dev:
        os.environ["APP_ENV"] = "development"
        os.environ["LOG_LEVEL"] = "DEBUG"

    if args.component == "full":
        run_full_platform()
    else:
        run_component(args.component)


if __name__ == "__main__":
    main()
