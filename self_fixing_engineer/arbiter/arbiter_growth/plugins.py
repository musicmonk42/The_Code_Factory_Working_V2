from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

# Use forward references for type hints to avoid circular imports.
# The ArbiterGrowthManager will have access to the actual types.
if TYPE_CHECKING:
    from .models import GrowthEvent, ArbiterState
    from .exceptions import ArbiterGrowthError


class PluginHook(ABC):
    """
    Defines the interface for plugins that can hook into the lifecycle
    of the ArbiterGrowthManager.

    Plugins allow for extending the core functionality of the manager
    without modifying its code. This is useful for custom logging, metrics,
    notifications, or triggering external workflows.

    To create a plugin, inherit from this class and implement the desired
    methods. The `on_growth_event` method is required, while the others
    are optional.

    Example Implementation:
    -----------------------

    .. code-block:: python

        import logging
        from .plugins import PluginHook
        from .models import GrowthEvent, ArbiterState

        logger = logging.getLogger("MyCustomPlugin")

        class LoggingAndMetricsPlugin(PluginHook):
            \"\"\"
            An example plugin that logs lifecycle events and sends a metric
            when a specific skill is improved.
            \"\"\"
            async def on_start(self, arbiter_name: str) -> None:
                logger.info(f"Plugin enabled for arbiter: {arbiter_name}")

            async def on_stop(self, arbiter_name: str) -> None:
                logger.info(f"Plugin shutting down for arbiter: {arbiter_name}")

            async def on_error(self, arbiter_name: str, error: "ArbiterGrowthError") -> None:
                logger.error(
                    f"Caught an error from the manager for {arbiter_name}: {error}",
                    exc_info=True
                )

            async def on_growth_event(self, event: GrowthEvent, state: ArbiterState) -> None:
                logger.info(
                    f"Arbiter '{state.arbiter_id}' processed event: {event.type}"
                )
                if event.type == "skill_improved" and event.details.get("skill_name") == "data_analysis":
                    # In a real scenario, you would use a metrics library here.
                    print(f"METRIC: data_analysis skill improved for {state.arbiter_id}!")

    """

    async def on_start(self, arbiter_name: str) -> None:
        """
        (Optional) Called when the ArbiterGrowthManager starts successfully,
        after the initial state has been loaded.

        This is a good place for initialization logic that depends on the
        manager being ready.

        Args:
            arbiter_name (str): The name of the arbiter the manager belongs to.
        """
        pass

    async def on_stop(self, arbiter_name: str) -> None:
        """
        (Optional) Called just before the ArbiterGrowthManager shuts down.

        This is a good place for cleanup logic, like closing connections
        or flushing buffers.

        Args:
            arbiter_name (str): The name of the arbiter the manager belongs to.
        """
        pass

    async def on_error(self, arbiter_name: str, error: "ArbiterGrowthError") -> None:
        """
        (Optional) Called when an unhandled exception occurs within one of the
        manager's background tasks (e.g., periodic flush or evolution).

        Args:
            arbiter_name (str): The name of the arbiter where the error occurred.
            error (ArbiterGrowthError): The exception that was caught.
        """
        pass

    @abstractmethod
    async def on_growth_event(
        self, event: "GrowthEvent", state: "ArbiterState"
    ) -> None:
        """
        (Required) Called after a growth event has been successfully applied to the state.
        This is the primary method for reacting to changes in the arbiter.

        Args:
            event (GrowthEvent): The event that was just processed.
            state (ArbiterState): The current, read-only state of the arbiter
                                 after the event was applied.
        """
        pass
