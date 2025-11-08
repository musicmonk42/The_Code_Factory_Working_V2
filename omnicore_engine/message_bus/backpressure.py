# message_bus/backpressure.py

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .sharded_message_bus import ShardedMessageBus
    from .message_types import Message


logger = logging.getLogger(__name__)


class BackpressureManager:
    def __init__(self, message_bus: "ShardedMessageBus", threshold: float = 0.8):
        if not (0 < threshold <= 1):
            raise ValueError("Backpressure threshold must be between 0 and 1 (exclusive of 0).")
        self.threshold = threshold
        self.message_bus = message_bus
        self.is_paused = [False] * self.message_bus.shard_count
        logger.info("BackpressureManager initialized.", threshold=threshold)

    async def check_and_notify(self, shard_id: int):
        queue = self.message_bus.queues[shard_id]
        hp_queue = self.message_bus.high_priority_queues[shard_id]

        current_queue_size = queue.qsize()
        current_hp_queue_size = hp_queue.qsize()
        max_size = self.message_bus.max_queue_size

        should_pause = (current_queue_size / max_size) >= self.threshold or \
                       (current_hp_queue_size / max_size) >= self.threshold

        if should_pause and not self.is_paused[shard_id]:
            self.is_paused[shard_id] = True
            try:
                # Add a new method to the ShardedMessageBus to pause publishes to this shard
                await self.message_bus.pause_publishes(shard_id)
                await self.message_bus.publish("message_bus.backpressure", {
                    "shard_id": shard_id,
                    "event": "pause",
                    "queue_size": current_queue_size,
                    "hp_queue_size": current_hp_queue_size,
                    "max_size": max_size
                }, priority=10, retries=0)
                logger.warning("Backpressure detected and publish paused.",
                               shard_id=shard_id,
                               queue_size=current_queue_size,
                               hp_queue_size=current_hp_queue_size)
            except Exception as e:
                logger.error(f"Failed to publish backpressure notification or pause publishes: {e}")

        elif not should_pause and self.is_paused[shard_id]:
            self.is_paused[shard_id] = False
            try:
                # Add a new method to the ShardedMessageBus to resume publishes to this shard
                await self.message_bus.resume_publishes(shard_id)
                await self.message_bus.publish("message_bus.backpressure", {
                    "shard_id": shard_id,
                    "event": "resume",
                    "queue_size": current_queue_size,
                    "hp_queue_size": current_hp_queue_size,
                    "max_size": max_size
                }, priority=10, retries=0)
                logger.info("Backpressure alleviated and publish resumed.",
                            shard_id=shard_id,
                            queue_size=current_queue_size,
                            hp_queue_size=current_hp_queue_size)
            except Exception as e:
                logger.error(f"Failed to publish backpressure resumption notification or resume publishes: {e}")