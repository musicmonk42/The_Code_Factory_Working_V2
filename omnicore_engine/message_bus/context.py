# message_bus/context.py

import contextvars
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional

if TYPE_CHECKING:
    from .message_types import Message
    from .sharded_message_bus import ShardedMessageBus

# Issue #3 fix: Use contextvars instead of threading.local() for async compatibility
# Thread-locals don't work with asyncio tasks - context gets lost across awaits
_context_var: contextvars.ContextVar[Dict[str, Any]] = contextvars.ContextVar(
    'execution_context', default={}
)


class ExecutionContext:
    """
    Async-safe execution context using contextvars.
    
    This replaces threading.local() which doesn't work correctly with asyncio tasks.
    Context is preserved across awaits and properly isolated between concurrent tasks.
    """
    
    @classmethod
    def get_current(cls) -> Dict[str, Any]:
        """Get a copy of the current execution context."""
        ctx = _context_var.get()
        return dict(ctx)  # Return copy to prevent external modification

    @classmethod
    def set(cls, **kwargs):
        """Update the current execution context with new values."""
        ctx = _context_var.get()
        new_ctx = {**ctx, **kwargs}
        _context_var.set(new_ctx)

    @classmethod
    def clear(cls):
        """Clear the current execution context."""
        _context_var.set({})


class ContextPropagationMiddleware:
    def __init__(self, message_bus: "ShardedMessageBus"):
        self.message_bus = message_bus
        message_bus.add_pre_publish_hook(self._inject_context)

    def _inject_context(self, message: "Message") -> "Message":
        if not message.context:
            message.context = ExecutionContext.get_current().copy()
        return message

    async def _restore_context_wrapper(
        self,
        callback: Callable[["Message"], Any],
        message: "Message",
        filter: Optional[Any],
    ):
        old_context = ExecutionContext.get_current().copy()

        if message.context:
            ExecutionContext.clear()
            ExecutionContext.set(**message.context)

        try:
            return await self.message_bus._safe_callback_internal(
                callback, message, filter
            )
        finally:
            ExecutionContext.clear()
            ExecutionContext.set(**old_context)
