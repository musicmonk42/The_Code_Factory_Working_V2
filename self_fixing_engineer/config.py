"""
Config wrapper to make main.py work with the existing arbiter.config module
"""

import logging
import os

# Try to import from arbiter.config if it exists
try:
    from arbiter.config import ArbiterConfig

    _has_arbiter_config = True
except ImportError:
    _has_arbiter_config = False


class ConfigWrapper:
    """Wrapper that combines ArbiterConfig with additional fields needed by main.py"""

    def __init__(self, arbiter_config=None):
        self._arbiter_config = arbiter_config
        # Add fields that main.py expects but ArbiterConfig doesn't have
        self.AUDIT_LOG_PATH = os.getenv("AUDIT_LOG_PATH", "./audit_trail.log")
        self.REDIS_URL = os.getenv("REDIS_URL", "")
        self.APP_ENV = os.getenv("APP_ENV", "development")

    def __getattr__(self, name):
        """Forward attribute access to ArbiterConfig if it exists, otherwise return None"""
        if self._arbiter_config and hasattr(self._arbiter_config, name):
            return getattr(self._arbiter_config, name)
        return None


class GlobalConfigManager:
    """Wrapper to provide config compatibility for main.py"""

    _instance = None

    @classmethod
    def get_config(cls):
        """Get configuration instance"""
        if cls._instance is None:
            cls._instance = cls._load_config()
        return cls._instance

    @classmethod
    def _load_config(cls):
        """Load configuration from arbiter.config or create minimal config"""
        arbiter_config = None

        if _has_arbiter_config:
            try:
                # Try to get the ArbiterConfig instance
                arbiter_config = ArbiterConfig()
            except Exception:
                try:
                    # If that fails, try the initialize method
                    arbiter_config = ArbiterConfig.initialize()
                except Exception as e:
                    logging.warning(f"Failed to initialize ArbiterConfig: {e}")
                    arbiter_config = None

        if arbiter_config:
            # Wrap the ArbiterConfig with our wrapper that adds missing fields
            return ConfigWrapper(arbiter_config)

        # Fallback: Create a minimal config object
        class MinimalConfig:
            def __init__(self):
                self.REDIS_URL = os.getenv("REDIS_URL", "")
                self.AUDIT_LOG_PATH = os.getenv("AUDIT_LOG_PATH", "./audit_trail.log")
                self.DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///sfe.db")
                self.APP_ENV = os.getenv("APP_ENV", "development")
                self.DB_PATH = os.getenv("DB_PATH", "sqlite:///sfe.db")
                self.ARENA_PORT = int(os.getenv("ARENA_PORT", "8000"))
                self.REPORTS_DIRECTORY = os.getenv("REPORTS_DIRECTORY", "./reports")

            def __getattr__(self, name):
                # Return None for any missing attributes
                return None

        return MinimalConfig()


def setup_logging():
    """Setup logging configuration"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
