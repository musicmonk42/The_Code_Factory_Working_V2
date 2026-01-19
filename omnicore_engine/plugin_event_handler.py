import asyncio
import inspect
import logging
import os
import sys
import types
import uuid
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from .plugin_registry import PluginRegistry


def _create_fallback_settings():
    """Create a minimal settings object for when ArbiterConfig is unavailable."""
    return types.SimpleNamespace(
        log_level="INFO",
        LOG_LEVEL="INFO",
        plugin_dir="./plugins",
        PLUGIN_DIR="./plugins",
    )


def _get_settings():
    """Lazy import + defensive instantiation of settings."""
    ArbiterConfig = None
    try:
        # Try the full canonical path first (preferred)
        from self_fixing_engineer.arbiter.config import ArbiterConfig
    except ImportError:
        try:
            # Fall back to aliased path for backward compatibility
            from arbiter.config import ArbiterConfig
        except ImportError:
            pass
    
    if ArbiterConfig is None:
        logging.debug(
            "arbiter.config not available; using fallback settings."
        )
        return _create_fallback_settings()

    try:
        return ArbiterConfig()
    except Exception as e:
        logging.warning(
            "ArbiterConfig() raised during instantiation; falling back to minimal settings. Error: %s",
            e,
        )
        return _create_fallback_settings()


# Initialize the configuration object with graceful fallback
settings = _get_settings()

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)


class PluginEventHandler(FileSystemEventHandler):
    """
    A file system event handler for hot-reloading plugins when changes are detected in the plugin directory.
    This class refactors duplicated logic, ensures asynchronous operation for reloads,
    and includes robust error handling to prevent disruption.
    """

    def __init__(self, registry: PluginRegistry, plugin_dir: str = None):
        """
        Initialize the PluginEventHandler.

        Args:
            registry (PluginRegistry): The plugin registry instance to manage plugins.
            plugin_dir (str, optional): The directory path where plugins are stored. Defaults to settings.plugin_dir.
        """
        super().__init__()
        self.registry = registry
        # Use the plugin_dir from settings as a fallback
        self.plugin_dir = plugin_dir or settings.plugin_dir
        self.last_modified_times = {}
        # Fix: Handle the case when there's no current event loop in the main thread
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running event loop, create a new one
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)

    def _schedule_async_task(self, coro):
        """
        Safely schedules an asynchronous task on the captured event loop.
        Ensures tasks are created and errors are logged without blocking the watchdog thread.
        """
        try:
            if self._loop.is_running():
                asyncio.create_task(coro)
            else:
                logger.warning(
                    "Event loop not running when scheduling async task. Running coroutine directly."
                )
                self._loop.run_until_complete(coro)
        except Exception as e:
            logger.error(
                f"Failed to schedule async plugin event task: {e}", exc_info=True
            )

    async def _handle_plugin_file_event_async(
        self, event_src_path: str, event_type: str
    ):
        """
        Asynchronously handles plugin file modification or creation events.
        Centralizes the reload and database save logic.

        Args:
            event_src_path (str): The path to the file that triggered the event.
            event_type (str): The type of event ('modified' or 'created').
        """
        try:
            real_src_path = os.path.realpath(event_src_path)
            current_mtime = os.path.getmtime(real_src_path)

            if (
                event_type == "modified"
                and self.last_modified_times.get(real_src_path) == current_mtime
            ):
                logger.debug(
                    f"Skipping redundant hot-reload for {real_src_path}: mtime unchanged."
                )
                return

            self.last_modified_times[real_src_path] = current_mtime

            logger.info(f"Initiating {event_type} handling for plugin: {real_src_path}")

            if hasattr(
                self.registry, "load_from_directory"
            ) and inspect.iscoroutinefunction(self.registry.load_from_directory):
                await self.registry.load_from_directory(self.plugin_dir)
            else:
                logger.warning(
                    "PluginRegistry does not have an async 'load_from_directory' method. Plugin reload might not occur."
                )
                return

            filename = Path(real_src_path).name
            plugin_name_from_file = filename[:-3]

            found_plugin = None
            for kind_str in self.registry.plugins:
                if plugin_name_from_file in self.registry.plugins[kind_str]:
                    found_plugin = self.registry.plugins[kind_str][
                        plugin_name_from_file
                    ]
                    break

            if found_plugin:
                if (
                    hasattr(self.registry, "db")
                    and self.registry.db
                    and hasattr(self.registry.db, "save_plugin_legacy")
                ):
                    logger.info(
                        f"Attempting to save {event_type} plugin {found_plugin.meta.kind}:{found_plugin.meta.name} to DB."
                    )
                    await self.registry.db.save_plugin_legacy(
                        {
                            "uuid": str(uuid.uuid4()),
                            "name": found_plugin.meta.name,
                            "kind": found_plugin.meta.kind,
                            "version": found_plugin.meta.version,
                            "description": found_plugin.meta.description,
                            "safe": found_plugin.meta.safe,
                            "source": found_plugin.meta.source,
                            "params_schema": found_plugin.meta.params_schema,
                            "code": (
                                inspect.getsource(found_plugin.fn)
                                if callable(found_plugin.fn)
                                else str(found_plugin.fn)
                            ),
                        }
                    )
                    logger.info(
                        f"Successfully saved {event_type} plugin {found_plugin.meta.kind}:{found_plugin.meta.name} metadata to DB."
                    )
                else:
                    logger.warning(
                        f"PluginRegistry DB not available or 'save_plugin_legacy' method missing. Plugin '{found_plugin.meta.name}' metadata not persisted on {event_type}."
                    )
            else:
                logger.warning(
                    f"Could not find plugin '{plugin_name_from_file}' in registry after {event_type} event. It might not have been correctly decorated or loaded."
                )

        except Exception as e:
            logger.error(
                f"Error during async plugin {event_type} handling for {event_src_path}: {e}",
                exc_info=True,
            )

    def on_modified(self, event):
        """
        Handle file modification events. Schedule an async task to reload the plugin.

        Args:
            event: The file system event object containing details about the modification.
        """
        if event.is_directory or not event.src_path.endswith(".py"):
            return

        self._schedule_async_task(
            self._handle_plugin_file_event_async(event.src_path, "modified")
        )

    def on_created(self, event):
        """
        Handle file creation events. Schedule an async task to load the new plugin.

        Args:
            event: The file system event object containing details about the creation.
        """
        if event.is_directory or not event.src_path.endswith(".py"):
            return

        self._schedule_async_task(
            self._handle_plugin_file_event_async(event.src_path, "created")
        )


def start_plugin_observer(registry: PluginRegistry, plugin_dir: str):
    """
    Start the observer to monitor the plugin directory for changes.

    Args:
        registry (PluginRegistry): The plugin registry instance to manage plugins.
        plugin_dir (str): The directory path to monitor for plugin changes.
    """
    observer = Observer()
    event_handler = PluginEventHandler(registry, plugin_dir)
    observer.schedule(event_handler, plugin_dir, recursive=False)
    try:
        observer.start()
        logger.info(f"Watchdog started for plugin directory: {plugin_dir}")
    except Exception as e:
        logger.error(f"Failed to start watchdog for {plugin_dir}: {e}")
