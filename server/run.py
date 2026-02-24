# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

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

    # Production mode with single worker (recommended for async FastAPI)
    python server/run.py --host 0.0.0.0 --port 8000 --workers 1

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
        default=None,  # Will use WORKER_COUNT, WEB_CONCURRENCY, or default to 1
        help="Number of worker processes (default: WORKER_COUNT/WEB_CONCURRENCY env var or 1)",
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
    
    # P3 FIX: Support WEB_CONCURRENCY and WORKER_COUNT environment variables for worker count
    # This allows easy scaling through environment configuration
    # WEB_CONCURRENCY is the Railway/Heroku standard, WORKER_COUNT is our K8s/Helm standard
    # FIX: Default to 1 worker - FastAPI is fully async and doesn't benefit from multiple workers
    # Multiple workers cause issues: jobs in one worker's memory aren't visible to other workers
    if args.workers is None:
        # Prefer WORKER_COUNT (K8s/Helm) over WEB_CONCURRENCY (Railway), fall back to 1
        # Use explicit checks to handle empty strings correctly
        worker_count = os.environ.get("WORKER_COUNT")
        if not worker_count:
            worker_count = os.environ.get("WEB_CONCURRENCY")
        if not worker_count:
            worker_count = "1"
        workers = int(worker_count)
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
    if workers > 1:
        logger.warning(f"  Note: Running with {workers} workers. Each worker has its own in-memory job cache, which may cause 'job not found' errors. Consider using 1 worker for async FastAPI.")
    logger.info(f"Reload: {args.reload}")
    logger.info(f"Log Level: {args.log_level}")
    logger.info("=" * 70)

    # Run the server
    # FIX: Add proper timeout configuration to prevent HTTP2 protocol errors
    # These errors occur when long-running requests (pipeline, codegen) exceed default timeouts
    # FIX: Reduced timeout_graceful_shutdown from 60 to 30 seconds
    # Railway may not wait 60 seconds - ensure process exits before force-kill
    uvicorn.run(
        "server.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        workers=workers if not args.reload else 1,
        log_level=args.log_level,
        access_log=True,
        timeout_keep_alive=300,  # 5 minutes for long-running operations (pipeline, codegen)
        timeout_graceful_shutdown=45,  # 45 seconds - gives more time to persist job states before force-kill
        h11_max_incomplete_event_size=16 * 1024 * 1024,  # 16MB for large responses
    )


if __name__ == "__main__":
    main()
