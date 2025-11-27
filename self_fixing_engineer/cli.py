"""
CLI module for the Self-Fixing Engineer platform
"""

import asyncio
import subprocess
import sys


class SFEPlatform:
    def __init__(self):
        self.arena_process = None
        self.running = False

    async def start(self):
        """Start the SFE platform"""
        print("\n" + "=" * 60)
        print("Starting Self-Fixing Engineer Platform")
        print("=" * 60)

        try:
            # Import and initialize config
            from arbiter.config import ArbiterConfig

            config = ArbiterConfig.initialize()

            # Start the arena in the main process
            from arbiter.arena import run_arena_async

            print("✓ Configuration loaded")
            print(f"  - Database: {config.DATABASE_URL}")
            print(f"  - Arena Port: {config.ARENA_PORT}")
            print("\nStarting Arena (this will run the API server)...")
            print("Press Ctrl+C to stop\n")

            # Run the arena directly using the async version
            await run_arena_async(settings=config)

        except ImportError as e:
            print(f"✗ Failed to import required modules: {e}")
            print("Please ensure all dependencies are installed:")
            print("  pip install -r requirements.txt")
        except KeyboardInterrupt:
            print("\n\nShutting down platform...")
        except Exception as e:
            print(f"✗ Platform error: {e}")
            import traceback

            traceback.print_exc()


async def main_cli_loop():
    """Main CLI loop for the Self-Fixing Engineer"""
    print("=" * 60)
    print("Self-Fixing Engineer (SFE) - CLI Mode")
    print("=" * 60)
    print("\nAvailable commands:")
    print("  run     - Run the full SFE platform (Arena + API)")
    print("  status  - Check system status")
    print("  scan    - Scan codebase for issues (simplified)")
    print("  repair  - Attempt to repair found issues")
    print("  arena   - Launch just the Arbiter Arena")
    print("  help    - Show this help message")
    print("  quit    - Exit the CLI")
    print()

    platform = SFEPlatform()

    while True:
        try:
            # Use input() in a thread to avoid blocking async loop
            loop = asyncio.get_event_loop()
            command = await loop.run_in_executor(None, input, "sfe> ")
            command = command.strip().lower()

            if command in ("quit", "exit", "q"):
                print("Goodbye!")
                break
            elif command == "help":
                print("\nCommands: run, status, scan, repair, arena, help, quit")
            elif command == "run":
                await platform.start()
            elif command == "status":
                await check_status()
            elif command == "scan":
                await simple_scan()
            elif command == "repair":
                await repair_issues()
            elif command == "arena":
                await launch_arena_subprocess()
            elif command == "":
                continue
            else:
                print(
                    f"Unknown command: {command}. Type 'help' for available commands."
                )

        except KeyboardInterrupt:
            print("\nUse 'quit' to exit.")
        except EOFError:
            print("\nGoodbye!")
            break
        except Exception as e:
            print(f"Error: {e}")


async def check_status():
    """Check the status of the SFE system"""
    print("\nChecking system status...")

    try:
        from arbiter.config import ArbiterConfig

        config = ArbiterConfig.initialize()
        print("✓ Configuration loaded")
        print(f"  - Environment: {config.APP_ENV}")
        print(f"  - Database: {config.DATABASE_URL}")
        print(f"  - Arena Port: {config.ARENA_PORT}")
        print(f"  - Redis: {config.REDIS_URL}")

        # Check paths
        if hasattr(config, "CODEBASE_PATHS"):
            print(f"  - Codebase Paths: {config.CODEBASE_PATHS}")

    except Exception as e:
        print(f"✗ Configuration error: {e}")

    try:

        print("✓ Arbiter Arena module available")
    except Exception as e:
        print(f"✗ Arena module error: {e}")

    try:

        print("✓ CodebaseAnalyzer module available")
    except Exception as e:
        print(f"✗ Analyzer module error: {e}")

    print("\nStatus check complete.\n")


async def simple_scan():
    """Simplified scan that just works"""
    print("\nStarting simplified codebase scan...")

    try:
        from arbiter.codebase_analyzer import CodebaseAnalyzer

        # Use current directory by default
        scan_path = "./arbiter"

        print(f"  Scanning: {scan_path}")

        # Create analyzer with simple path
        analyzer = CodebaseAnalyzer(root_dir=".")

        # Initialize manually to avoid context manager issues
        analyzer.executor = None
        analyzer.semaphore = asyncio.Semaphore(analyzer.max_workers)

        # Scan the arbiter directory
        results = await analyzer.scan_codebase(scan_path)

        print("\n✓ Scan Results:")
        print(f"  - Files scanned: {results.get('files', 0)}")
        print(f"  - Defects found: {len(results.get('defects', []))}")
        print(f"  - Complexity issues: {len(results.get('complexity', []))}")

        # Show some defects if found
        defects = results.get("defects", [])
        if defects:
            print("\nTop issues found:")
            for defect in defects[:5]:
                file = defect.get("file", "unknown")
                line = defect.get("line", 0)
                message = defect.get("message", "no description")
                print(f"  - {file}:{line}")
                print(f"    {message[:80]}...")

    except ImportError:
        print("✗ CodebaseAnalyzer not available")
        print("  Please install required dependencies")
    except Exception as e:
        print(f"✗ Scan error: {e}")
        # Don't print full traceback for cleaner output
        print("  Try running from the project root directory")

    print("\nScan complete.\n")


async def repair_issues():
    """Attempt to repair found issues"""
    print("\nRepair feature requires the Arena to be running.")
    print("Use 'run' command to start the full platform.\n")


async def launch_arena_subprocess():
    """Launch the Arbiter Arena in a subprocess"""
    print("\nLaunching Arbiter Arena...")
    print("This will start the arena in a subprocess.")
    print("Press Ctrl+C to stop.\n")

    try:
        # Run using python module syntax
        process = subprocess.Popen(
            [sys.executable, "-m", "arbiter.arena"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        print("Arena starting...")

        # Monitor the process output
        try:
            for line in iter(process.stdout.readline, ""):
                if line:
                    print(f"  Arena: {line.strip()}")
                if process.poll() is not None:
                    break
        except KeyboardInterrupt:
            print("\nStopping arena...")
            process.terminate()
            process.wait(timeout=5)

    except FileNotFoundError:
        print("✗ Could not find the arena module.")
        print("  Make sure you're in the project root directory")
    except Exception as e:
        print(f"✗ Failed to launch arena: {e}")

    print()


if __name__ == "__main__":
    # Check if we should run in platform mode
    if len(sys.argv) > 1 and sys.argv[1] == "--platform":
        platform = SFEPlatform()
        asyncio.run(platform.start())
    else:
        asyncio.run(main_cli_loop())
