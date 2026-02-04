"""
Startup script for the Code Factory Platform API Server.

Usage:
    python server/run.py [options]

Options:
    --host HOST         Host to bind to (default: 0.0.0.0)
    --port PORT         Port to bind to (default: 8000)
    --reload            Enable auto-reload for development
    --workers N         Number of worker processes (default: 1)
    --log-level LEVEL   Log level (default: info)

Examples:
    # Development mode with auto-reload
    python server/run.py --reload

    # Production mode with multiple workers
    python server/run.py --host 0.0.0.0 --port 8000 --workers 4

    # Custom log level
    python server/run.py --log-level debug
"""

import argparse
import logging
import os
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import uvicorn

logger = logging.getLogger(__name__)


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Code Factory Platform API Server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Host to bind to (default: 0.0.0.0)",
    )

    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT", 8000)),
        help="Port to bind to (default: 8000 or PORT env var)",
    )

    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload for development",
    )

    parser.add_argument(
        "--workers",
        type=int,
        default=None,  # Will use WEB_CONCURRENCY or default to 1
        help="Number of worker processes (default: WEB_CONCURRENCY env var or 1)",
    )

    parser.add_argument(
        "--log-level",
        type=str,
        default="info",
        choices=["critical", "error", "warning", "info", "debug", "trace"],
        help="Log level (default: info)",
    )

    return parser.parse_args()


def main():
    """Main entry point for the API server."""
    args = parse_args()
    
    # P3 FIX: Support WEB_CONCURRENCY environment variable for worker count
    # This allows easy scaling through environment configuration
    if args.workers is None:
        workers = int(os.environ.get("WEB_CONCURRENCY", "1"))
    else:
        workers = args.workers
    
    # Validate worker count
    if workers < 1:
        logger.warning(f"Invalid worker count {workers}, using 1")
        workers = 1
    elif workers > 16:
        logger.warning(f"High worker count {workers}, consider if this is intentional")

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    logger.info("=" * 70)
    logger.info("Code Factory Platform API Server")
    logger.info("=" * 70)
    logger.info(f"Host: {args.host}")
    logger.info(f"Port: {args.port}")
    logger.info(f"Workers: {workers}")
    if workers == 1:
        logger.info("  Note: Single worker mode. Set WEB_CONCURRENCY for more workers.")
    logger.info(f"Reload: {args.reload}")
    logger.info(f"Log Level: {args.log_level}")
    logger.info("=" * 70)

    # Run the server
    # FIX: Add proper timeout configuration to prevent HTTP2 protocol errors
    # These errors occur when long-running requests (pipeline, codegen) exceed default timeouts
    # FIX: Increase timeout_graceful_shutdown from 30 to 60 seconds for production resilience
    uvicorn.run(
        "server.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        workers=workers if not args.reload else 1,
        log_level=args.log_level,
        access_log=True,
        timeout_keep_alive=300,  # 5 minutes for long-running operations (pipeline, codegen)
        timeout_graceful_shutdown=60,  # 60 seconds for graceful shutdown (increased from 30)
        h11_max_incomplete_event_size=16 * 1024 * 1024,  # 16MB for large responses
    )


if __name__ == "__main__":
    main()
