# message_bus/message_types.py

import time
import uuid
from dataclasses import dataclass, field
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


class MessageSchema(BaseModel):
    topic: str
    payload: Dict[str, Any]
    priority: int = 0
    trace_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    context: Dict[str, Any] = Field(default_factory=dict)
