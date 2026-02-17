# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# message_bus/message_types.py

import time
import uuid
from dataclasses import dataclass, field, replace
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


@dataclass
class Message:
    topic: str
    payload: Any
    priority: int = 0
    timestamp: float = field(default_factory=time.time)
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    encrypted: bool = False
    idempotency_key: Optional[str] = None
    context: Dict[str, Any] = field(default_factory=dict)
    processing_start: Optional[int] = None
    
    def __lt__(self, other):
        """
        Compare messages for ordering in PriorityQueue.
        Higher priority values are processed first (reverse order).
        If priorities are equal, earlier timestamps are processed first.
        
        Note: This only defines ordering for priority queue purposes.
        Equality (__eq__) is handled by the dataclass default implementation.
        """
        if not isinstance(other, Message):
            return NotImplemented
        # Negative priority for max-heap behavior (higher priority = lower value)
        if self.priority != other.priority:
            return self.priority > other.priority  # Reverse for max-heap
        return self.timestamp < other.timestamp  # Earlier timestamp first
    
    def __le__(self, other):
        if not isinstance(other, Message):
            return NotImplemented
        return self < other or self == other
    
    def __gt__(self, other):
        if not isinstance(other, Message):
            return NotImplemented
        return not self <= other
    
    def __ge__(self, other):
        if not isinstance(other, Message):
            return NotImplemented
        return not self < other
    
    def with_topic(self, new_topic: str) -> "Message":
        """
        Create a copy of this message with a different topic.
        Useful for republishing messages to different topics (e.g., DLQ).
        """
        return replace(self, topic=new_topic)


class MessageSchema(BaseModel):
    topic: str
    payload: Dict[str, Any]
    priority: int = 0
    trace_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    context: Dict[str, Any] = Field(default_factory=dict)
