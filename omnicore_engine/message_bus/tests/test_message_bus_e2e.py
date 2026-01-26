# test_message_bus_e2e.py
"""
End-to-End tests for the message bus system.
These tests verify the complete flow with minimal mocking.
"""

import asyncio
import logging
import random
import sys
import time
import unittest
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List
from unittest.mock import AsyncMock, Mock, patch

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Configure logging for debugging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class MessageData:
    """Test message for tracking in e2e tests."""

    id: str
    topic: str
    payload: Any
    timestamp: float
    received_by: List[str] = None

    def __post_init__(self):
        if self.received_by is None:
            self.received_by = []


class E2ETestConfig:
    """Configuration for e2e tests."""

    def __init__(self):
        self.message_bus_shard_count = 3
        self.message_bus_max_queue_size = 1000
        self.message_bus_workers_per_shard = 2
        self.message_bus_callback_workers = 4
        self.dynamic_shards_enabled = False
        self.USE_KAFKA = False
        self.USE_REDIS = False
        self.MESSAGE_BUS_RATE_LIMIT_MAX = 1000
        self.MESSAGE_BUS_RATE_LIMIT_WINDOW = 60
        self.ENABLE_MESSAGE_BUS_GUARDIAN = False
        self.priority_threshold = 5
        self.message_persistence_priority_threshold = 8
        self.DLQ_PRIORITY_THRESHOLD = 7
        self.DLQ_MAX_RETRIES = 3
        self.DLQ_BACKOFF_FACTOR = 0.1
        self.RETRY_POLICIES = {
            "critical": {"max_retries": 5, "backoff_factor": 0.01},
            "default": {"max_retries": 3, "backoff_factor": 0.01},
        }
        self.MESSAGE_DEDUP_CACHE_SIZE = 1000
        self.MESSAGE_DEDUP_TTL = 3600
        self.BACKPRESSURE_THRESHOLD = 0.8
        self.KAFKA_CIRCUIT_THRESHOLD = 5
        self.KAFKA_CIRCUIT_TIMEOUT = 60
        self.REDIS_CIRCUIT_THRESHOLD = 5
        self.REDIS_CIRCUIT_TIMEOUT = 60


class MessageBusE2ETest(unittest.TestCase):
    """End-to-end tests for the message bus system."""

    @classmethod
    def setUpClass(cls):
        """Set up test environment once for all tests."""
        cls.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(cls.loop)

    def setUp(self):
        """Set up test fixtures before each test."""
        self.config = E2ETestConfig()
        self.received_messages = {}
        self.error_messages = []
        self.test_messages = []

        # Mock external dependencies
        self.mock_db = Mock()
        self.mock_db.save_preferences = AsyncMock()

        # Patch external dependencies
        self.patchers = []

        # Patch security utils
        security_patcher = patch("message_bus.sharded_message_bus.get_security_utils")
        self.patchers.append(security_patcher)
        mock_security = security_patcher.start()
        mock_security.return_value = Mock(
            encrypt_data=lambda data, context: f"encrypted_{data}",
            decrypt_data=lambda data: data.replace("encrypted_", ""),
        )

        # Patch Kafka and Redis if not testing them
        if not self.config.USE_KAFKA:
            kafka_patcher = patch("message_bus.sharded_message_bus.KafkaBridge")
            self.patchers.append(kafka_patcher)
            kafka_patcher.start().return_value = None

        if not self.config.USE_REDIS:
            redis_patcher = patch("message_bus.sharded_message_bus.RedisBridge")
            self.patchers.append(redis_patcher)
            redis_patcher.start().return_value = None

    def tearDown(self):
        """Clean up after each test."""
        for patcher in self.patchers:
            patcher.stop()

    async def create_message_bus(self):
        """Create a message bus instance for testing."""
        from message_bus.sharded_message_bus import ShardedMessageBus

        bus = ShardedMessageBus(config=self.config, db=self.mock_db, audit_client=None)

        # Wait for initialization
        await asyncio.sleep(0.1)
        return bus

    async def test_simple_publish_subscribe(self):
        """Test simple publish-subscribe flow."""
        bus = await self.create_message_bus()

        received = []

        async def handler(message):
            received.append(message)
            logger.info(f"Received message: {message.topic}")

        # Subscribe
        await bus._subscribe_async("test.topic", handler)

        # Publish
        result = await bus.publish(
            topic="test.topic", payload={"data": "test_value"}, priority=1
        )

        self.assertTrue(result)

        # Wait for processing
        await asyncio.sleep(0.5)

        # Verify
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].topic, "test.topic")
        self.assertEqual(received[0].payload, {"data": "test_value"})

        await bus.shutdown()

    async def test_multiple_subscribers(self):
        """Test multiple subscribers to same topic."""
        bus = await self.create_message_bus()

        received_1 = []
        received_2 = []
        received_3 = []

        async def handler_1(msg):
            received_1.append(msg)

        async def handler_2(msg):
            received_2.append(msg)

        async def handler_3(msg):
            received_3.append(msg)

        # Subscribe multiple handlers
        await bus._subscribe_async("multi.topic", handler_1)
        await bus._subscribe_async("multi.topic", handler_2)
        await bus._subscribe_async("multi.topic", handler_3)

        # Publish once
        await bus.publish("multi.topic", {"value": 42})

        # Wait for processing
        await asyncio.sleep(0.5)

        # All should receive
        self.assertEqual(len(received_1), 1)
        self.assertEqual(len(received_2), 1)
        self.assertEqual(len(received_3), 1)

        await bus.shutdown()

    async def test_priority_ordering(self):
        """Test that higher priority messages are processed first."""
        bus = await self.create_message_bus()

        received_order = []

        async def handler(msg):
            received_order.append(msg.payload["id"])
            await asyncio.sleep(0.01)  # Simulate processing time

        await bus._subscribe_async("priority.test", handler)

        # Publish messages with different priorities
        # Note: Lower priority number = higher priority in the queue
        await bus.publish("priority.test", {"id": "low"}, priority=10)
        await bus.publish("priority.test", {"id": "high"}, priority=1)
        await bus.publish("priority.test", {"id": "medium"}, priority=5)

        # Wait for processing
        await asyncio.sleep(1)

        # Verify priority ordering
        self.assertEqual(received_order[0], "high")
        self.assertEqual(received_order[1], "medium")
        self.assertEqual(received_order[2], "low")

        await bus.shutdown()

    async def test_request_response_pattern(self):
        """Test request-response communication pattern."""
        bus = await self.create_message_bus()

        # Setup responder
        async def responder(msg):
            if msg.topic == "calc.add":
                result = msg.payload["a"] + msg.payload["b"]
                # Extract reply topic from payload
                reply_topic = msg.payload.get("reply_topic")
                if reply_topic:
                    await bus.publish(reply_topic, {"result": result})

        await bus._subscribe_async("calc.add", responder)

        # Make request
        reply_topic = f"reply.{uuid.uuid4()}"
        response_future = asyncio.Future()

        async def reply_handler(msg):
            response_future.set_result(msg.payload)

        await bus._subscribe_async(reply_topic, reply_handler)

        # Send request
        await bus.publish("calc.add", {"a": 5, "b": 3, "reply_topic": reply_topic})

        # Wait for response
        try:
            response = await asyncio.wait_for(response_future, timeout=2.0)
            self.assertEqual(response["result"], 8)
        except asyncio.TimeoutError:
            self.fail("Request-response timed out")

        await bus.shutdown()

    async def test_batch_publishing(self):
        """Test batch message publishing."""
        bus = await self.create_message_bus()

        received = []

        async def handler(msg):
            received.append(msg)

        # Subscribe to multiple topics
        await bus._subscribe_async("batch.topic1", handler)
        await bus._subscribe_async("batch.topic2", handler)
        await bus._subscribe_async("batch.topic3", handler)

        # Batch publish
        messages = [
            {"topic": "batch.topic1", "payload": {"id": 1}},
            {"topic": "batch.topic2", "payload": {"id": 2}},
            {"topic": "batch.topic3", "payload": {"id": 3}},
        ]

        results = await bus.batch_publish(messages)

        self.assertEqual(len(results), 3)
        self.assertTrue(all(results))

        # Wait for processing
        await asyncio.sleep(0.5)

        self.assertEqual(len(received), 3)

        await bus.shutdown()

    async def test_idempotency(self):
        """Test idempotent message delivery."""
        bus = await self.create_message_bus()

        received = []

        async def handler(msg):
            received.append(msg)

        await bus._subscribe_async("idempotent.topic", handler)

        idempotency_key = "unique_key_123"

        # Publish same message multiple times
        for _ in range(5):
            await bus.publish(
                topic="idempotent.topic",
                payload={"data": "test"},
                idempotency_key=idempotency_key,
            )

        # Wait for processing
        await asyncio.sleep(0.5)

        # Should only receive once
        self.assertEqual(len(received), 1)

        await bus.shutdown()

    async def test_concurrent_publishers(self):
        """Test concurrent publishing from multiple tasks."""
        bus = await self.create_message_bus()

        received = []

        async def handler(msg):
            received.append(msg)

        await bus._subscribe_async("concurrent.topic", handler)

        # Create multiple publisher tasks
        async def publisher(publisher_id, count):
            for i in range(count):
                await bus.publish(
                    "concurrent.topic", {"publisher": publisher_id, "seq": i}
                )
                await asyncio.sleep(random.uniform(0.001, 0.01))

        # Run publishers concurrently
        tasks = [publisher(f"pub_{i}", 10) for i in range(5)]

        await asyncio.gather(*tasks)

        # Wait for processing
        await asyncio.sleep(1)

        # Should receive all messages
        self.assertEqual(len(received), 50)  # 5 publishers * 10 messages

        # Verify no message loss
        publisher_counts = {}
        for msg in received:
            pub_id = msg.payload["publisher"]
            if pub_id not in publisher_counts:
                publisher_counts[pub_id] = set()
            publisher_counts[pub_id].add(msg.payload["seq"])

        for pub_id, sequences in publisher_counts.items():
            self.assertEqual(len(sequences), 10)
            self.assertEqual(sequences, set(range(10)))

        await bus.shutdown()

    async def test_error_handling_and_dlq(self):
        """Test error handling and dead letter queue."""
        bus = await self.create_message_bus()

        error_count = 0
        success_messages = []

        async def faulty_handler(msg):
            nonlocal error_count
            if msg.payload.get("should_fail"):
                error_count += 1
                raise Exception("Simulated error")
            success_messages.append(msg)

        await bus._subscribe_async("error.topic", faulty_handler)

        # Publish messages that will fail
        await bus.publish(
            "error.topic",
            {"should_fail": True, "id": "fail_1"},
            priority=10,  # High priority for DLQ
        )

        # Publish messages that will succeed
        await bus.publish(
            "error.topic", {"should_fail": False, "id": "success_1"}, priority=10
        )

        # Wait for processing
        await asyncio.sleep(0.5)

        # Verify error handling
        self.assertGreater(error_count, 0)
        self.assertEqual(len(success_messages), 1)
        self.assertEqual(success_messages[0].payload["id"], "success_1")

        await bus.shutdown()

    async def test_performance_load(self):
        """Test message bus under load."""
        bus = await self.create_message_bus()

        message_count = 1000
        received_count = 0
        start_time = time.time()

        async def handler(msg):
            nonlocal received_count
            received_count += 1

        await bus._subscribe_async("perf.topic", handler)

        # Publish many messages
        publish_tasks = []
        for i in range(message_count):
            task = bus.publish(
                "perf.topic",
                {"seq": i, "timestamp": time.time()},
                priority=random.randint(1, 10),
            )
            publish_tasks.append(task)

        # Wait for all publishes
        await asyncio.gather(*publish_tasks)

        # Wait for processing
        max_wait = 10
        wait_start = time.time()
        while received_count < message_count and (time.time() - wait_start) < max_wait:
            await asyncio.sleep(0.1)

        end_time = time.time()
        duration = end_time - start_time

        # Verify all messages received
        self.assertEqual(received_count, message_count)

        # Calculate throughput
        throughput = message_count / duration
        logger.info(f"Throughput: {throughput:.2f} messages/second")

        # Performance assertion (adjust based on your requirements)
        self.assertGreater(throughput, 100)  # At least 100 msg/sec

        await bus.shutdown()

    async def test_graceful_shutdown(self):
        """Test graceful shutdown with pending messages."""
        bus = await self.create_message_bus()

        processed = []

        async def slow_handler(msg):
            await asyncio.sleep(0.1)  # Simulate slow processing
            processed.append(msg)

        await bus._subscribe_async("shutdown.topic", slow_handler)

        # Publish messages
        for i in range(10):
            await bus.publish("shutdown.topic", {"id": i})

        # Start shutdown while messages are processing
        await asyncio.sleep(0.05)  # Let some messages start processing

        shutdown_task = asyncio.create_task(bus.shutdown())

        # Wait for shutdown
        await shutdown_task

        # Verify bus is stopped
        self.assertFalse(bus.running)

        logger.info(f"Processed {len(processed)} messages before shutdown")


class MessageBusIntegrationTest(unittest.TestCase):
    """Integration tests with external services (when available)."""

    async def test_with_redis_if_available(self):
        """Test with Redis if available."""
        # This would test with actual Redis connection
        # Skipped if Redis not available
        pass

    async def test_with_kafka_if_available(self):
        """Test with Kafka if available."""
        # This would test with actual Kafka connection
        # Skipped if Kafka not available
        pass


def run_async_test(test_func):
    """Helper to run async test functions."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(test_func())
    finally:
        loop.close()


def run_e2e_tests():
    """Run all e2e tests with proper reporting."""
    print("=" * 70)
    print("Running Message Bus End-to-End Tests")
    print("=" * 70)

    test_suite = MessageBusE2ETest()
    test_suite.setUp()

    tests = [
        ("Simple Publish-Subscribe", test_suite.test_simple_publish_subscribe),
        ("Multiple Subscribers", test_suite.test_multiple_subscribers),
        ("Priority Ordering", test_suite.test_priority_ordering),
        ("Request-Response Pattern", test_suite.test_request_response_pattern),
        ("Batch Publishing", test_suite.test_batch_publishing),
        ("Idempotency", test_suite.test_idempotency),
        ("Concurrent Publishers", test_suite.test_concurrent_publishers),
        ("Error Handling and DLQ", test_suite.test_error_handling_and_dlq),
        ("Performance Load Test", test_suite.test_performance_load),
        ("Graceful Shutdown", test_suite.test_graceful_shutdown),
    ]

    passed = 0
    failed = 0

    for test_name, test_func in tests:
        print(f"\nRunning: {test_name}")
        try:
            run_async_test(test_func)
            print(f"✓ {test_name} passed")
            passed += 1
        except Exception as e:
            print(f"✗ {test_name} failed: {e}")
            failed += 1
            import traceback

            traceback.print_exc()

    test_suite.tearDown()

    print("\n" + "=" * 70)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 70)

    return failed == 0


if __name__ == "__main__":
    success = run_e2e_tests()
    sys.exit(0 if success else 1)
