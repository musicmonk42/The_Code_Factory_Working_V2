#!/usr/bin/env python3
"""
Code Factory Platform - Unified Launcher
=========================================

Launch one, two, or all three modules of the Code Factory Platform with a single command.
This launcher provides convenience for local development while preserving the architectural
separation of the three main modules.

Modules:
    - Generator: README-to-App Code Generator (171 files)
    - OmniCore: Central Orchestration Engine (77 files)
    - SFE: Self-Fixing Engineer with Arbiter AI (552 files)

Usage:
    # Launch all modules
    python launch.py --all

    # Launch specific modules
    python launch.py --generator
    python launch.py --omnicore
    python launch.py --sfe

    # Launch combinations
    python launch.py --generator --sfe
    python launch.py --generator --omnicore

    # Custom ports (default: 8000, 8001, 8002)
    python launch.py --all --ports 8000,8001,8002

    # Development mode (with auto-reload where supported)
    python launch.py --all --dev

    # Show status without launching
    python launch.py --status

Environment Variables:
    GENERATOR_PORT: Default port for Generator (default: 8000)
    OMNICORE_PORT: Default port for OmniCore (default: 8001)
    SFE_PORT: Default port for SFE (default: 8002)
    REDIS_URL: Redis connection string (default: redis://localhost:6379)

Requirements:
    - Python 3.11+
    - Redis (running locally or via Docker)
    - All platform dependencies installed (pip install -r requirements.txt)

Examples:
    # Quick start - launch everything
    python launch.py --all

    # Generator only for code generation tasks
    python launch.py --generator

    # OmniCore + SFE for maintenance workflows
    python launch.py --omnicore --sfe

    # Development mode with custom ports
    python launch.py --all --dev --ports 9000,9001,9002

Author: Novatrax Labs
License: Proprietary
Version: 1.0.0
"""

import argparse
import asyncio
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

try:
    import aiohttp
    import redis
    from rich.console import Console
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
except ImportError:
    print("Error: Required packages not installed.")
    print("Please run: pip install aiohttp redis rich")
    sys.exit(1)

# Project root directory
PROJECT_ROOT = Path(__file__).parent.absolute()

# Module configurations
@dataclass
class ModuleConfig:
    name: str
    display_name: str
    command: List[str]
    cwd: Path
    port: int
    health_endpoint: str
    color: str
    description: str


class PlatformLauncher:
    """Manages launching and monitoring of Code Factory Platform modules."""

    def __init__(self):
        self.console = Console()
        self.processes: Dict[str, subprocess.Popen] = {}
        self.shutdown_event = asyncio.Event()

    def get_module_configs(
        self, ports: Optional[List[int]] = None
    ) -> Dict[str, ModuleConfig]:
        """Get configuration for all modules."""
        if ports is None:
            ports = [
                int(os.getenv("GENERATOR_PORT", "8000")),
                int(os.getenv("OMNICORE_PORT", "8001")),
                int(os.getenv("SFE_PORT", "8002")),
            ]

        return {
            "generator": ModuleConfig(
                name="generator",
                display_name="Generator (RCG)",
                command=[
                    sys.executable,
                    "generator/main/main.py",
                    "--interface",
                    "api",
                    "--log-level",
                    "INFO",
                ],
                cwd=PROJECT_ROOT,
                port=ports[0],
                health_endpoint=f"http://localhost:{ports[0]}/health",
                color="cyan",
                description="README-to-App Code Generator",
            ),
            "omnicore": ModuleConfig(
                name="omnicore",
                display_name="OmniCore Engine",
                command=[
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "omnicore_engine.fastapi_app:app",
                    "--host",
                    "0.0.0.0",
                    "--port",
                    str(ports[1]),
                ],
                cwd=PROJECT_ROOT,
                port=ports[1],
                health_endpoint=f"http://localhost:{ports[1]}/health",
                color="green",
                description="Central Orchestration Hub",
            ),
            "sfe": ModuleConfig(
                name="sfe",
                display_name="Self-Fixing Engineer",
                command=[
                    sys.executable,
                    "self_fixing_engineer/main.py",
                    "--mode",
                    "api",
                    "--port",
                    str(ports[2]),
                ],
                cwd=PROJECT_ROOT,
                port=ports[2],
                health_endpoint=f"http://localhost:{ports[2]}/__sfe/healthz",
                color="magenta",
                description="AI-Powered Self-Healing System",
            ),
        }

    def setup_signal_handlers(self):
        """Setup graceful shutdown on SIGINT/SIGTERM."""

        def signal_handler(signum, frame):
            self.console.print("\n[yellow]Received shutdown signal. Stopping modules...[/yellow]")
            self.shutdown_event.set()

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

    async def check_redis(self) -> bool:
        """Check if Redis is available."""
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        try:
            r = redis.from_url(redis_url, socket_connect_timeout=2)
            await asyncio.to_thread(r.ping)
            r.close()
            return True
        except Exception:
            return False

    async def check_health(self, endpoint: str, timeout: float = 2.0) -> bool:
        """Check if a module's health endpoint responds."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(endpoint, timeout=timeout) as response:
                    return response.status == 200
        except Exception:
            return False

    def start_module(self, config: ModuleConfig, dev_mode: bool = False) -> subprocess.Popen:
        """Start a module subprocess."""
        command = config.command.copy()
        
        # Add reload flag for development mode where supported
        if dev_mode:
            if config.name == "omnicore":
                command.append("--reload")
            elif config.name == "generator":
                # Generator doesn't support reload in current implementation
                pass
            elif config.name == "sfe":
                command.append("--reload")

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"  # Ensure real-time output

        process = subprocess.Popen(
            command,
            cwd=config.cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        self.processes[config.name] = process
        return process

    async def wait_for_health(
        self, config: ModuleConfig, timeout: int = 60, check_interval: float = 2.0
    ) -> bool:
        """Wait for a module to become healthy."""
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            if await self.check_health(config.health_endpoint, timeout=check_interval):
                return True
            
            # Check if process died
            if self.processes[config.name].poll() is not None:
                return False
            
            await asyncio.sleep(check_interval)
        
        return False

    def create_status_table(self, modules: Dict[str, ModuleConfig], statuses: Dict[str, str]) -> Table:
        """Create a rich table showing module statuses."""
        table = Table(title="Code Factory Platform - Module Status", show_header=True, header_style="bold")
        table.add_column("Module", style="bold", width=25)
        table.add_column("Status", width=15)
        table.add_column("Port", width=10)
        table.add_column("Health Check", width=40)

        for name, config in modules.items():
            status = statuses.get(name, "unknown")
            
            if status == "healthy":
                status_text = Text("✓ Healthy", style="bold green")
            elif status == "starting":
                status_text = Text("⋯ Starting", style="bold yellow")
            elif status == "failed":
                status_text = Text("✗ Failed", style="bold red")
            elif status == "stopped":
                status_text = Text("○ Stopped", style="dim")
            else:
                status_text = Text("? Unknown", style="dim")

            table.add_row(
                f"[{config.color}]{config.display_name}[/{config.color}]\n{config.description}",
                status_text,
                str(config.port),
                config.health_endpoint,
            )

        return table

    async def launch_modules(
        self, modules_to_launch: List[str], configs: Dict[str, ModuleConfig], dev_mode: bool = False
    ):
        """Launch specified modules and monitor them."""
        
        # Check Redis first
        self.console.print("[blue]Checking Redis connection...[/blue]")
        if not await self.check_redis():
            self.console.print("[red]✗ Redis is not available. Please start Redis first:[/red]")
            self.console.print("  docker run -d -p 6379:6379 redis:7-alpine")
            sys.exit(1)
        self.console.print("[green]✓ Redis connection OK[/green]\n")

        # Display launch banner
        banner = Panel(
            "[bold cyan]Code Factory Platform Launcher[/bold cyan]\n\n"
            f"Launching: {', '.join([configs[m].display_name for m in modules_to_launch])}\n"
            f"Mode: {'Development (with reload)' if dev_mode else 'Production'}",
            border_style="blue",
        )
        self.console.print(banner)
        self.console.print()

        # Start all modules
        statuses = {name: "stopped" for name in configs.keys()}
        
        for module_name in modules_to_launch:
            config = configs[module_name]
            self.console.print(f"[{config.color}]Starting {config.display_name} on port {config.port}...[/{config.color}]")
            self.start_module(config, dev_mode)
            statuses[module_name] = "starting"

        self.console.print()

        # Wait for all modules to become healthy
        self.console.print("[blue]Waiting for modules to become healthy...[/blue]")
        
        for module_name in modules_to_launch:
            config = configs[module_name]
            self.console.print(f"  Checking {config.display_name}...", end="")
            
            if await self.wait_for_health(config, timeout=90):
                statuses[module_name] = "healthy"
                self.console.print(f" [{config.color}]✓ Ready[/{config.color}]")
            else:
                statuses[module_name] = "failed"
                self.console.print(f" [red]✗ Failed to start[/red]")
                
                # Show last error output
                stderr = self.processes[module_name].stderr
                if stderr:
                    error_lines = stderr.readlines()[-10:]  # Last 10 lines
                    self.console.print("[red]Error output:[/red]")
                    for line in error_lines:
                        self.console.print(f"  {line.rstrip()}")

        self.console.print()

        # Display final status table
        table = self.create_status_table(configs, statuses)
        self.console.print(table)
        self.console.print()

        # Check if any failed
        if any(s == "failed" for s in statuses.values()):
            self.console.print("[red]✗ Some modules failed to start. Check logs above.[/red]")
            await self.shutdown_all()
            sys.exit(1)

        # Display access URLs
        self.console.print("[bold green]✓ All modules are running![/bold green]\n")
        self.console.print("[bold]Access URLs:[/bold]")
        for module_name in modules_to_launch:
            config = configs[module_name]
            if config.name == "generator":
                self.console.print(f"  • {config.display_name}: http://localhost:{config.port}")
                self.console.print(f"    API Docs: http://localhost:{config.port}/docs")
            elif config.name == "omnicore":
                self.console.print(f"  • {config.display_name}: http://localhost:{config.port}")
                self.console.print(f"    API Docs: http://localhost:{config.port}/docs")
            elif config.name == "sfe":
                self.console.print(f"  • {config.display_name}: http://localhost:{config.port}")
                self.console.print(f"    Health: http://localhost:{config.port}/__sfe/healthz")

        self.console.print("\n[dim]Press Ctrl+C to stop all modules[/dim]\n")

        # Monitor modules until shutdown
        await self.monitor_modules(configs, modules_to_launch)

    async def monitor_modules(self, configs: Dict[str, ModuleConfig], active_modules: List[str]):
        """Monitor running modules and handle crashes."""
        check_interval = 5.0  # Check every 5 seconds

        while not self.shutdown_event.is_set():
            for module_name in active_modules:
                process = self.processes.get(module_name)
                if process and process.poll() is not None:
                    # Process died
                    config = configs[module_name]
                    self.console.print(f"\n[red]✗ {config.display_name} crashed! Exit code: {process.returncode}[/red]")
                    
                    # Trigger shutdown
                    self.shutdown_event.set()
                    break

            try:
                await asyncio.wait_for(self.shutdown_event.wait(), timeout=check_interval)
            except asyncio.TimeoutError:
                continue

        await self.shutdown_all()

    async def shutdown_all(self):
        """Gracefully shutdown all running modules."""
        if not self.processes:
            return

        self.console.print("\n[yellow]Shutting down modules...[/yellow]")

        for name, process in self.processes.items():
            if process.poll() is None:  # Still running
                self.console.print(f"  Stopping {name}...")
                process.terminate()

        # Wait for graceful shutdown
        await asyncio.sleep(2)

        # Force kill if still running
        for name, process in self.processes.items():
            if process.poll() is None:
                self.console.print(f"  Force killing {name}...")
                process.kill()

        self.console.print("[green]✓ All modules stopped[/green]")

    async def show_status(self, configs: Dict[str, ModuleConfig]):
        """Show current status of all modules without launching."""
        self.console.print("[blue]Checking module status...[/blue]\n")

        statuses = {}
        for name, config in configs.items():
            if await self.check_health(config.health_endpoint, timeout=1.0):
                statuses[name] = "healthy"
            else:
                statuses[name] = "stopped"

        table = self.create_status_table(configs, statuses)
        self.console.print(table)


async def main():
    parser = argparse.ArgumentParser(
        description="Code Factory Platform - Unified Launcher",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --all                    Launch all modules
  %(prog)s --generator              Launch Generator only
  %(prog)s --omnicore --sfe         Launch OmniCore and SFE
  %(prog)s --all --dev              Launch all in development mode
  %(prog)s --status                 Show current status
        """,
    )

    parser.add_argument("--all", action="store_true", help="Launch all three modules")
    parser.add_argument("--generator", action="store_true", help="Launch Generator module")
    parser.add_argument("--omnicore", action="store_true", help="Launch OmniCore Engine")
    parser.add_argument("--sfe", action="store_true", help="Launch Self-Fixing Engineer")
    parser.add_argument(
        "--ports",
        type=str,
        help="Custom ports (comma-separated, e.g., 8000,8001,8002)",
    )
    parser.add_argument("--dev", action="store_true", help="Enable development mode with auto-reload")
    parser.add_argument("--status", action="store_true", help="Show status of modules without launching")

    args = parser.parse_args()

    # Parse custom ports if provided
    ports = None
    if args.ports:
        try:
            ports = [int(p.strip()) for p in args.ports.split(",")]
            if len(ports) != 3:
                print("Error: --ports must specify exactly 3 ports (generator,omnicore,sfe)")
                sys.exit(1)
        except ValueError:
            print("Error: Invalid port format. Use: --ports 8000,8001,8002")
            sys.exit(1)

    launcher = PlatformLauncher()
    configs = launcher.get_module_configs(ports)

    # Determine which modules to launch
    modules_to_launch = []
    if args.all:
        modules_to_launch = ["generator", "omnicore", "sfe"]
    else:
        if args.generator:
            modules_to_launch.append("generator")
        if args.omnicore:
            modules_to_launch.append("omnicore")
        if args.sfe:
            modules_to_launch.append("sfe")

    # Status check mode
    if args.status:
        await launcher.show_status(configs)
        return

    # Validate that at least one module is selected
    if not modules_to_launch:
        parser.print_help()
        print("\nError: No modules selected. Use --all or specify individual modules.")
        sys.exit(1)

    # Setup signal handlers for graceful shutdown
    launcher.setup_signal_handlers()

    # Launch selected modules
    await launcher.launch_modules(modules_to_launch, configs, dev_mode=args.dev)


if __name__ == "__main__":
    asyncio.run(main())
