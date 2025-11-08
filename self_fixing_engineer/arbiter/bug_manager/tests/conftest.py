
import pytest
import logging
import sys
from prometheus_client import CollectorRegistry, REGISTRY
from prometheus_client.metrics import MetricWrapperBase
from unittest.mock import patch

def setup_logging():
    """
    Configure logging to write to a file to avoid I/O errors on closed streams.
    """
    logger = logging.getLogger()
    logger.handlers = []
    handler = logging.FileHandler('test.log', mode='w')
    handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    """
    Reset the Prometheus registry before test collection to prevent duplicate metric registration.
    """
    setup_logging()
    logging.info("Configuring pytest and resetting Prometheus registry")
    
    # Mock the global REGISTRY to isolate test environment
    with patch('prometheus_client.REGISTRY', CollectorRegistry()):
        # Log initial registry state
        logging.debug(f"Initial collectors: {REGISTRY._collector_to_names}")
        logging.debug(f"Initial names: {REGISTRY._names_to_collectors}")
        
        # Unregister all valid collectors
        for collector in list(REGISTRY._collector_to_names.values()):
            if isinstance(collector, MetricWrapperBase):
                try:
                    REGISTRY.unregister(collector)
                except Exception as e:
                    logging.error(f"Failed to unregister collector {collector}: {e}")
            else:
                logging.warning(f"Skipping invalid collector type: {type(collector)} - {collector}")
        
        # Clear internal mappings
        try:
            REGISTRY._names_to_collectors.clear()
            REGISTRY._collector_to_names.clear()
        except Exception as e:
            logging.error(f"Failed to clear registry mappings: {e}")
        
        logging.debug(f"Collectors after reset: {REGISTRY._collector_to_names}")
        logging.debug(f"Names after reset: {REGISTRY._names_to_collectors}")
