# message_bus/dead_letter_queue.py

import asyncio
import logging
import types
from typing import TYPE_CHECKING, Optional

# Conditional imports
try:
    from kafka import KafkaProducer

    KAFKA_AVAILABLE = True
except ImportError:
    KafkaProducer = None
    KAFKA_AVAILABLE = False


def _create_fallback_settings():
    """Create a minimal settings object for when ArbiterConfig is unavailable."""
    return types.SimpleNamespace(
        log_level="INFO",
        LOG_LEVEL="INFO",
        database_path="sqlite:///./omnicore.db",
        DB_PATH="sqlite:///./omnicore.db",
    )


def _get_settings():
    """Lazy import + defensive instantiation of settings."""
    try:
        from arbiter.config import ArbiterConfig

        return ArbiterConfig()
    except ImportError as e:
        logging.warning(
            "Could not import arbiter.config; using fallback settings. Import error: %s",
            e,
        )
        return _create_fallback_settings()
    except Exception as e:
        logging.warning(
            "ArbiterConfig() raised during instantiation; falling back to minimal settings. Error: %s",
            e,
        )
        return _create_fallback_settings()


# External project imports
from omnicore_engine.core import safe_serialize

# Relative imports from the new modular structure
from .message_types import Message

settings = _get_settings()

if TYPE_CHECKING:
    from omnicore_engine.database.database import Database
    from .integrations.kafka_bridge import KafkaBridge

logger = logging.getLogger(__name__)


class DeadLetterQueue:
    def __init__(
        self,
        db: "Database",
        kafka_bridge: Optional["KafkaBridge"],
        priority_threshold: int,
    ):
        self.db = db
        self.kafka_bridge = kafka_bridge
        self.priority_threshold = priority_threshold
        self.queue = asyncio.Queue()
        self.running = True
        self._dlq_task = asyncio.create_task(self._process_dlq())
        self.max_retries = getattr(settings, "DLQ_MAX_RETRIES", 3)
        self.backoff_factor = getattr(settings, "DLQ_BACKOFF_FACTOR", 1.5)
        logger.info("DeadLetterQueue initialized.")

    async def add(self, message: Message, error: str):
        full_error = f"Error Type: {type(error).__name__}, Message: {error}"
        logger.error(
            "Adding message to DLQ",
            topic=message.topic,
            trace_id=message.trace_id,
            error=full_error,
        )

        await self.queue.put(
            (message, full_error, 0)
        )  # Add message, error, and retry count

        try:
            # Persist message to database as a form of non-volatile storage
            await self.db.save_preferences(
                user_id=f"dlq_message_{message.trace_id}",
                prefs={
                    "topic": message.topic,
                    "payload": safe_serialize(message.payload),
                    "error": full_error,
                    "timestamp": message.timestamp,
                    "original_trace_id": message.trace_id,
                    "idempotency_key": message.idempotency_key,
                },
            )
            logger.debug(
                "DLQ message persisted to database.", trace_id=message.trace_id
            )
        except Exception as e:
            logger.error(
                f"Failed to persist DLQ message to database: {e}",
                trace_id=message.trace_id,
            )

    async def _process_dlq(self):
        while self.running:
            try:
                message, error, retries = await self.queue.get()
                logger.info(
                    "DLQ consumer received message (in-memory).",
                    topic=message.topic,
                    trace_id=message.trace_id,
                    current_retries=retries,
                )

                if self.kafka_bridge:
                    # Attempt to publish the DLQ message to Kafka if the circuit is closed
                    if self.kafka_bridge.circuit.can_attempt():
                        try:
                            await self.kafka_bridge.publish(message, topic="dlq_events")
                            logger.info(
                                "DLQ message published to Kafka bridge.",
                                trace_id=message.trace_id,
                            )
                        except Exception as e:
                            self.kafka_bridge.circuit.record_failure()
                            logger.error(
                                f"Failed to publish DLQ message to Kafka: {e}",
                                trace_id=message.trace_id,
                            )

                            # If publish fails, check retry count and re-queue
                            if retries < self.max_retries:
                                await asyncio.sleep(self.backoff_factor * (2**retries))
                                await self.queue.put((message, error, retries + 1))
                                logger.warning(
                                    "Re-queued DLQ message for retry.",
                                    trace_id=message.trace_id,
                                    next_retry=retries + 1,
                                )
                            else:
                                logger.critical(
                                    f"DLQ message failed to process after {self.max_retries} attempts. Dropping message.",
                                    trace_id=message.trace_id,
                                    error=error,
                                )
                    else:
                        logger.warning(
                            "Kafka circuit is open. Skipping DLQ message publish to Kafka.",
                            trace_id=message.trace_id,
                        )
                elif KAFKA_AVAILABLE and self.kafka_bridge is None:
                    logger.warning(
                        "Kafka is available but the Kafka bridge is not initialized. Skipping DLQ publish."
                    )

                self.queue.task_done()

            except asyncio.CancelledError:
                logger.info("DLQ processing task cancelled.")
                break
            except Exception as e:
                # Catch any unexpected errors in the processing loop itself
                logger.error(
                    f"Unexpected error in DLQ processing loop: {e}. Sleeping before next attempt.",
                    exc_info=True,
                )
                await asyncio.sleep(1)

    async def shutdown(self):
        self.running = False
        if self._dlq_task and not self._dlq_task.done():
            self._dlq_task.cancel()
            try:
                await self._dlq_task
            except asyncio.CancelledError:
                pass
        await self.queue.join()
        logger.info("DeadLetterQueue shutdown complete.")
