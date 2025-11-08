# message_bus/context.py

import threading
from typing import Any, Dict, Callable, TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .message_types import Message
    from .sharded_message_bus import ShardedMessageBus

class ExecutionContext:
    _local = threading.local()

    @classmethod
    def get_current(cls) -> Dict[str, Any]:
        if not hasattr(cls._local, 'context'):
            cls._local.context = {}
        return cls._local.context

    @classmethod
    def set(cls, **kwargs):
        cls.get_current().update(kwargs)

    @classmethod
    def clear(cls):
        if hasattr(cls._local, 'context'):
            cls._local.context.clear()


class ContextPropagationMiddleware:
    def __init__(self, message_bus: "ShardedMessageBus"):
        self.message_bus = message_bus
        message_bus.add_pre_publish_hook(self._inject_context)

    def _inject_context(self, message: "Message") -> "Message":
        if not message.context:
            message.context = ExecutionContext.get_current().copy()
        return message

    async def _restore_context_wrapper(self, callback: Callable[["Message"], Any], message: "Message", filter: Optional[Any]):
        old_context = ExecutionContext.get_current().copy()

        if message.context:
            ExecutionContext.clear()
            ExecutionContext.set(**message.context)

        try:
            return await self.message_bus._safe_callback_internal(callback, message, filter)
        finally:
            ExecutionContext.clear()
            ExecutionContext.set(**old_context)