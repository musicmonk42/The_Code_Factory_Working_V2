"""
OmniCore service integration utilities.

This module provides utilities for integrating with the OmniCore service,
including periodic audit flush functionality.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class OmniCoreService:
    """
    Wrapper for OmniCore service functionality.
    
    Provides graceful degradation when OmniCore engine is not available.
    """
    
    def __init__(self):
        self._omnicore_available = False
        self._initialize()
    
    def _initialize(self):
        """Initialize connection to OmniCore engine if available."""
        try:
            # Try to import OmniCore engine
            from omnicore_engine import engine
            self._omnicore_available = True
            logger.info("OmniCore engine available")
        except ImportError:
            logger.debug("OmniCore engine not available - running in standalone mode")
            self._omnicore_available = False
        except Exception as e:
            logger.warning(f"Failed to initialize OmniCore: {e}")
            self._omnicore_available = False
    
    async def start_periodic_audit_flush(self) -> bool:
        """
        Start periodic audit log flushing.
        
        Note: The audit logger manages its own background tasks internally.
        This method simply triggers the initialization.
        
        Returns:
            bool: True if flush task started successfully, False otherwise.
        """
        if not self._omnicore_available:
            logger.debug("Periodic audit flush not started - OmniCore not available")
            return False
        
        try:
            # Import audit functionality
            from generator.audit_log import audit_logger
            
            # Start periodic flush if audit logger is available
            # Note: audit_logger.start_periodic_flush() creates and manages its own tasks
            if hasattr(audit_logger, 'start_periodic_flush'):
                await audit_logger.start_periodic_flush()
                logger.info("Periodic audit flush started successfully")
                return True
            else:
                logger.debug("Audit logger does not support periodic flush")
                return False
                
        except ImportError as e:
            logger.debug(f"Audit functionality not available: {e}")
            return False
        except Exception as e:
            logger.warning(f"Failed to start periodic audit flush: {e}")
            return False
    
    async def stop(self):
        """
        Stop and cleanup resources.
        
        Note: The audit logger manages its own task lifecycle.
        This is provided for API consistency but currently doesn't need to do anything.
        """
        pass


# Singleton instance
_omnicore_service: Optional[OmniCoreService] = None


def get_omnicore_service() -> Optional[OmniCoreService]:
    """
    Get the singleton OmniCore service instance.
    
    Returns:
        Optional[OmniCoreService]: The service instance, or None if initialization fails.
    """
    global _omnicore_service
    
    if _omnicore_service is None:
        try:
            _omnicore_service = OmniCoreService()
        except Exception as e:
            logger.error(f"Failed to create OmniCore service: {e}")
            return None
    
    return _omnicore_service
