#!/usr/bin/env python3
"""
Plugin Registry Database Initialization Script

This script initializes the database for the PluginRegistry to enable
persistent storage of plugin metadata, versioning, and performance tracking.

Usage:
    python scripts/init_plugin_registry.py

Environment Variables:
    DATABASE_URL - Database connection string (required)
    TESTING - Set to 1 to use test database
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def init_plugin_registry_db():
    """Initialize the plugin registry database."""
    
    try:
        # Import database module
        from omnicore_engine.database import Database
        
        logger.info("Initializing Plugin Registry Database...")
        
        # Check if DATABASE_URL is configured
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            logger.warning(
                "DATABASE_URL not set. Plugin registry will run without persistence."
            )
            logger.info(
                "To enable plugin registry persistence, set DATABASE_URL in your .env file."
            )
            return False
        
        # Initialize database connection
        db = Database()
        await db.initialize()
        
        logger.info("✓ Database connection established")
        
        # Create plugin registry tables
        # The Database class should handle table creation automatically,
        # but we can verify the connection works
        try:
            # Test the connection with a simple query
            await db.health_check()
            logger.info("✓ Database health check passed")
        except Exception as e:
            logger.error(f"Database health check failed: {e}")
            return False
        
        # Initialize plugin registry with database
        from omnicore_engine.plugin_registry import PLUGIN_REGISTRY
        
        # Attach database to plugin registry
        PLUGIN_REGISTRY.db = db
        logger.info("✓ Plugin registry database attached")
        
        # Initialize the registry
        await PLUGIN_REGISTRY.initialize()
        logger.info("✓ Plugin registry initialized")
        
        logger.info("=" * 60)
        logger.info("Plugin Registry Database Initialization Complete!")
        logger.info("=" * 60)
        logger.info("")
        logger.info("Plugin metadata will now be persisted to the database.")
        logger.info("Plugin versioning and performance tracking are enabled.")
        logger.info("")
        
        return True
        
    except ImportError as e:
        logger.error(f"Failed to import required modules: {e}")
        logger.error(
            "Ensure omnicore_engine.database is properly installed and configured."
        )
        return False
    except Exception as e:
        logger.error(f"Failed to initialize plugin registry database: {e}", exc_info=True)
        return False


async def verify_plugin_registry():
    """Verify plugin registry is working correctly."""
    
    try:
        from omnicore_engine.plugin_registry import PLUGIN_REGISTRY
        
        logger.info("Verifying plugin registry...")
        
        # Check if database is attached
        if PLUGIN_REGISTRY.db is None:
            logger.warning("Plugin registry database not attached")
            return False
        
        # Check if registry is initialized
        if not PLUGIN_REGISTRY._is_initialized:
            logger.warning("Plugin registry not initialized")
            return False
        
        # List registered plugins
        plugin_count = sum(len(plugins) for plugins in PLUGIN_REGISTRY._plugins.values())
        logger.info(f"✓ Plugin registry has {plugin_count} registered plugins")
        
        # Show plugin kinds
        for kind, plugins in PLUGIN_REGISTRY._plugins.items():
            if plugins:
                logger.info(f"  - {kind}: {len(plugins)} plugins")
        
        logger.info("✓ Plugin registry verification passed")
        return True
        
    except Exception as e:
        logger.error(f"Plugin registry verification failed: {e}", exc_info=True)
        return False


def main():
    """Main entry point."""
    
    logger.info("=" * 60)
    logger.info("Plugin Registry Database Initialization")
    logger.info("=" * 60)
    logger.info("")
    
    # Check environment
    if os.getenv("TESTING") == "1":
        logger.info("Running in TESTING mode")
    
    # Run initialization
    success = asyncio.run(init_plugin_registry_db())
    
    if success:
        # Run verification
        asyncio.run(verify_plugin_registry())
        logger.info("")
        logger.info("✅ Plugin registry database initialized successfully!")
        sys.exit(0)
    else:
        logger.error("")
        logger.error("❌ Plugin registry database initialization failed!")
        logger.error("The application will still work, but plugin metadata won't be persisted.")
        sys.exit(1)


if __name__ == "__main__":
    main()
