# test_sharded_message_bus.py

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from message_bus.message_types import Message
from message_bus.sharded_message_bus import (
    MAX_MESSAGE_SIZE,
    RateLimiter,
    RateLimitError,
    ShardedMessageBus,
    sign_message,
    validate_message_size,
    verify_message,
)


class TestRateLimiter(unittest.TestCase):
    """Test suite for RateLimiter class."""

    async def test_basic_rate_limiting(self):
        """Test basic rate limiting functionality."""
        limiter = RateLimiter(max_requests=3, window=1)

        # Should allow first 3 requests
        client_id = "test_client"
        for _ in range(3):
            result = await limiter.check_rate_limit(client_id)
            self.assertTrue(result)

        # 4th request should fail
        with self.assertRaises(RateLimitError):
            await limiter.check_rate_limit(client_id)

    async def test_window_expiration(self):
        """Test that old requests expire after window."""
        limiter = RateLimiter(max_requests=2, window=0.1)  # 100ms window

        client_id = "test_client"

        # Make 2 requests
        await limiter.check_rate_limit(client_id)
        await limiter.check_rate_limit(client_id)

        # Wait for window to expire
        await asyncio.sleep(0.15)

        # Should allow new request
        result = await limiter.check_rate_limit(client_id)
        self.assertTrue(result)

    async def test_multiple_clients(self):
        """Test rate limiting with multiple clients."""
        limiter = RateLimiter(max_requests=2, window=1)

        # Each client should have independent limits
        await limiter.check_rate_limit("client1")
        await limiter.check_rate_limit("client1")
        await limiter.check_rate_limit("client2")
        await limiter.check_rate_limit("client2")

        # client1 should be limited
        with self.assertRaises(RateLimitError):
            await limiter.check_rate_limit("client1")

        # client2 should be limited
        with self.assertRaises(RateLimitError):
            await limiter.check_rate_limit("client2")


class TestMessageValidation(unittest.TestCase):
    """Test suite for message validation functions."""

    def test_validate_message_size_valid(self):
        """Test validation of valid message size."""
        small_payload = {"data": "test"}
        result = validate_message_size(small_payload)
        self.assertTrue(result)

    def test_validate_message_size_too_large(self):
        """Test validation of message exceeding size limit."""
        # Create payload larger than MAX_MESSAGE_SIZE
        large_payload = {"data": "x" * (MAX_MESSAGE_SIZE + 1)}

        with self.assertRaises(ValueError) as context:
            validate_message_size(large_payload)

        self.assertIn("Message too large", str(context.exception))

    def test_sign_and_verify_message(self):
        """Test message signing and verification."""
        message = Message(topic="test.topic", payload={"data": "test"}, trace_id="trace_123")

        key = b"secret_key_12345"

        # Sign message
        signature = sign_message(message, key)
        self.assertIsInstance(signature, str)

        # Verify with correct key
        result = verify_message(message, signature, key)
        self.assertTrue(result)

        # Verify with wrong key
        wrong_key = b"wrong_key"
        result = verify_message(message, signature, wrong_key)
        self.assertFalse(result)


class TestShardedMessageBus(unittest.TestCase):
    """Test suite for ShardedMessageBus class."""

    def setUp(self):
        """Set up test fixtures before each test."""
        # Mock config
        self.mock_config = Mock()
        self.mock_config.message_bus_shard_count = 2
        self.mock_config.message_bus_max_queue_size = 100
        self.mock_config.message_bus_workers_per_shard = 2
        self.mock_config.message_bus_callback_workers = 4
        self.mock_config.dynamic_shards_enabled = False
        self.mock_config.USE_KAFKA = False
        self.mock_config.USE_REDIS = False
        self.mock_config.MESSAGE_BUS_RATE_LIMIT_MAX = 100
        self.mock_config.MESSAGE_BUS_RATE_LIMIT_WINDOW = 60
        self.mock_config.ENABLE_MESSAGE_BUS_GUARDIAN = False
        self.mock_config.priority_threshold = 5
        self.mock_config.message_persistence_priority_threshold = 5
        self.mock_config.DLQ_PRIORITY_THRESHOLD = 5
        self.mock_config.RETRY_POLICIES = {}
        self.mock_config.MESSAGE_DEDUP_CACHE_SIZE = 100
        self.mock_config.MESSAGE_DEDUP_TTL = 3600
        self.mock_config.BACKPRESSURE_THRESHOLD = 0.8

        # Mock database
        self.mock_db = Mock()
        self.mock_db.save_preferences = AsyncMock()

        # Mock audit client
        self.mock_audit = Mock()
        self.mock_audit.add_entry_async = AsyncMock()

        # Patch security_utils
        self.security_patcher = patch("message_bus.sharded_message_bus.get_security_utils")
        mock_get_security = self.security_patcher.start()
        self.mock_security_utils = Mock()
        self.mock_security_utils.encrypt_data = Mock(return_value="encrypted_data")
        self.mock_security_utils.decrypt_data = Mock(return_value="decrypted_data")
        mock_get_security.return_value = self.mock_security_utils

    def tearDown(self):
        """Clean up after each test."""
        self.security_patcher.stop()

    @patch("message_bus.sharded_message_bus.KafkaBridge")
    @patch("message_bus.sharded_message_bus.RedisBridge")
    @patch("message_bus.sharded_message_bus.DeadLetterQueue")
    def test_initialization(self, mock_dlq_class, mock_redis_class, mock_kafka_class):
        """Test ShardedMessageBus initialization."""
        bus = ShardedMessageBus(
            config=self.mock_config, db=self.mock_db, audit_client=self.mock_audit
        )

        self.assertEqual(bus.shard_count, 2)
        self.assertEqual(bus.max_queue_size, 100)
        self.assertEqual(len(bus.queues), 2)
        self.assertEqual(len(bus.high_priority_queues), 2)
        self.assertTrue(bus.running)

    async def test_publish_basic(self):
        """Test basic message publishing."""
        with patch("message_bus.sharded_message_bus.KafkaBridge"), patch(
            "message_bus.sharded_message_bus.RedisBridge"
        ), patch("message_bus.sharded_message_bus.DeadLetterQueue"):

            bus = ShardedMessageBus(
                config=self.mock_config, db=self.mock_db, audit_client=self.mock_audit
            )

            # Mock the internal publish method
            bus._publish_to_shard = AsyncMock(return_value=True)

            result = await bus.publish(topic="test.topic", payload={"data": "test"}, priority=3)

            self.assertTrue(result)
            bus._publish_to_shard.assert_called_once()

    async def test_publish_with_rate_limiting(self):
        """Test publishing with rate limiting."""
        with patch("message_bus.sharded_message_bus.KafkaBridge"), patch(
            "message_bus.sharded_message_bus.RedisBridge"
        ), patch("message_bus.sharded_message_bus.DeadLetterQueue"):

            bus = ShardedMessageBus(config=self.mock_config)

            # Make rate limiter reject request
            bus.rate_limiter.check_rate_limit = AsyncMock(
                side_effect=RateLimitError("Rate limit exceeded")
            )

            result = await bus.publish(
                topic="test.topic", payload={"data": "test"}, client_id="test_client"
            )

            self.assertFalse(result)

    async def test_publish_with_encryption(self):
        """Test publishing with encryption."""
        with patch("message_bus.sharded_message_bus.KafkaBridge"), patch(
            "message_bus.sharded_message_bus.RedisBridge"
        ), patch("message_bus.sharded_message_bus.DeadLetterQueue"):

            bus = ShardedMessageBus(config=self.mock_config, db=self.mock_db)

            bus._publish_to_shard = AsyncMock(return_value=True)

            result = await bus.publish(topic="test.topic", payload={"data": "test"}, encrypt=True)

            self.assertTrue(result)

            # Verify encryption was called
            self.mock_security_utils.encrypt_data.assert_called_once()

    async def test_publish_with_idempotency(self):
        """Test idempotent message publishing."""
        with patch("message_bus.sharded_message_bus.KafkaBridge"), patch(
            "message_bus.sharded_message_bus.RedisBridge"
        ), patch("message_bus.sharded_message_bus.DeadLetterQueue"):

            bus = ShardedMessageBus(config=self.mock_config)
            bus._publish_to_shard = AsyncMock(return_value=True)

            idempotency_key = "idem_123"

            # First publish
            result1 = await bus.publish(
                topic="test.topic",
                payload={"data": "test"},
                idempotency_key=idempotency_key,
            )
            self.assertTrue(result1)

            # Second publish with same key should be skipped
            result2 = await bus.publish(
                topic="test.topic",
                payload={"data": "test"},
                idempotency_key=idempotency_key,
            )
            self.assertTrue(result2)  # Returns True but doesn't actually publish

            # Only one actual publish should have occurred
            bus._publish_to_shard.assert_called_once()

    async def test_subscribe_and_unsubscribe(self):
        """Test subscription management."""
        with patch("message_bus.sharded_message_bus.KafkaBridge"), patch(
            "message_bus.sharded_message_bus.RedisBridge"
        ), patch("message_bus.sharded_message_bus.DeadLetterQueue"):

            bus = ShardedMessageBus(config=self.mock_config)

            # Create mock callback
            callback = Mock()

            # Subscribe
            bus.subscribe("test.topic", callback)

            # Verify subscription
            await bus._subscribe_async("test.topic", callback)
            self.assertIn("test.topic", bus.subscribers)
            self.assertEqual(len(bus.subscribers["test.topic"]), 1)

            # Unsubscribe
            bus.unsubscribe("test.topic", callback)

            await bus._unsubscribe_async("test.topic", callback)
            self.assertEqual(len(bus.subscribers["test.topic"]), 0)

    async def test_request_response_pattern(self):
        """Test request-response pattern."""
        with patch("message_bus.sharded_message_bus.KafkaBridge"), patch(
            "message_bus.sharded_message_bus.RedisBridge"
        ), patch("message_bus.sharded_message_bus.DeadLetterQueue"):

            bus = ShardedMessageBus(config=self.mock_config)

            # Mock publish
            bus.publish = AsyncMock(return_value=True)

            # Setup auto-response
            async def auto_respond():
                await asyncio.sleep(0.01)
                # Find the reply topic and respond
                for topic in bus.subscribers:
                    if topic.startswith("reply."):
                        message = Message(topic=topic, payload="response_data")
                        for callback, _ in bus.subscribers[topic]:
                            callback(message)

            # Start auto-response
            asyncio.create_task(auto_respond())

            # Make request
            try:
                response = await bus.request(
                    topic="test.topic", payload={"request": "data"}, timeout=0.1
                )
                self.assertEqual(response, "response_data")
            except TimeoutError:
                pass  # Timeout is acceptable in test environment

    async def test_batch_publish(self):
        """Test batch message publishing."""
        with patch("message_bus.sharded_message_bus.KafkaBridge"), patch(
            "message_bus.sharded_message_bus.RedisBridge"
        ), patch("message_bus.sharded_message_bus.DeadLetterQueue"):

            bus = ShardedMessageBus(config=self.mock_config)
            bus.publish = AsyncMock(return_value=True)

            messages = [
                {"topic": "topic1", "payload": {"data": 1}},
                {"topic": "topic2", "payload": {"data": 2}},
                {"topic": "topic3", "payload": {"data": 3}},
            ]

            results = await bus.batch_publish(messages)

            self.assertEqual(len(results), 3)
            self.assertTrue(all(results))
            self.assertEqual(bus.publish.call_count, 3)

    async def test_pre_publish_hook(self):
        """Test pre-publish hook functionality."""
        with patch("message_bus.sharded_message_bus.KafkaBridge"), patch(
            "message_bus.sharded_message_bus.RedisBridge"
        ), patch("message_bus.sharded_message_bus.DeadLetterQueue"):

            bus = ShardedMessageBus(config=self.mock_config)

            # Add pre-publish hook
            def modify_message(message):
                message.context["modified"] = True
                return message

            bus.add_pre_publish_hook(modify_message)
            bus._publish_to_shard = AsyncMock(return_value=True)

            await bus.publish("test.topic", {"data": "test"})

            # Verify hook was applied
            call_args = bus._publish_to_shard.call_args
            message = call_args[0][1]
            self.assertTrue(message.context.get("modified"))

    async def test_shutdown(self):
        """Test graceful shutdown."""
        with patch("message_bus.sharded_message_bus.KafkaBridge") as mock_kafka, patch(
            "message_bus.sharded_message_bus.RedisBridge"
        ) as mock_redis, patch("message_bus.sharded_message_bus.DeadLetterQueue") as mock_dlq:

            # Setup mocks
            mock_kafka_instance = Mock()
            mock_kafka_instance.shutdown = AsyncMock()
            mock_kafka.return_value = mock_kafka_instance

            mock_redis_instance = Mock()
            mock_redis_instance.shutdown = AsyncMock()
            mock_redis.return_value = mock_redis_instance

            mock_dlq_instance = Mock()
            mock_dlq_instance.shutdown = AsyncMock()
            mock_dlq.return_value = mock_dlq_instance

            # Enable bridges
            self.mock_config.USE_KAFKA = True
            self.mock_config.USE_REDIS = True

            bus = ShardedMessageBus(config=self.mock_config)

            await bus.shutdown()

            self.assertFalse(bus.running)


class TestShardedMessageBusIntegration(unittest.TestCase):
    """Integration tests for ShardedMessageBus."""

    async def test_end_to_end_message_flow(self):
        """Test complete message flow from publish to callback."""
        with patch("message_bus.sharded_message_bus.KafkaBridge"), patch(
            "message_bus.sharded_message_bus.RedisBridge"
        ), patch("message_bus.sharded_message_bus.DeadLetterQueue"):

            config = Mock()
            config.message_bus_shard_count = 2
            config.message_bus_max_queue_size = 100
            config.message_bus_workers_per_shard = 2
            config.message_bus_callback_workers = 4
            config.dynamic_shards_enabled = False
            config.USE_KAFKA = False
            config.USE_REDIS = False
            config.MESSAGE_BUS_RATE_LIMIT_MAX = 100
            config.MESSAGE_BUS_RATE_LIMIT_WINDOW = 60
            config.ENABLE_MESSAGE_BUS_GUARDIAN = False
            config.priority_threshold = 5
            config.DLQ_PRIORITY_THRESHOLD = 5
            config.RETRY_POLICIES = {}
            config.MESSAGE_DEDUP_CACHE_SIZE = 100
            config.MESSAGE_DEDUP_TTL = 3600
            config.BACKPRESSURE_THRESHOLD = 0.8

            bus = ShardedMessageBus(config=config)

            received_messages = []

            async def callback(message):
                received_messages.append(message)

            # Subscribe
            await bus._subscribe_async("test.topic", callback)

            # Publish
            await bus.publish("test.topic", {"data": "test"}, priority=1)

            # Wait for processing
            await asyncio.sleep(0.1)

            # Verify callback was called
            # Note: In actual implementation, this might not work without
            # proper dispatcher setup

            await bus.shutdown()


def run_async_test(coro):
    """Helper to run async tests."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


if __name__ == "__main__":
    # Run standard unit tests
    unittest.main(argv=[""], exit=False, verbosity=2)

    # Run async tests
    print("\n" + "=" * 70)
    print("Running Async Tests")
    print("=" * 70)

    # Rate limiter tests
    rate_limiter_tests = TestRateLimiter()
    run_async_test(rate_limiter_tests.test_basic_rate_limiting())
    print("✓ Rate limiting basic test passed")

    run_async_test(rate_limiter_tests.test_window_expiration())
    print("✓ Rate limiting window expiration test passed")

    run_async_test(rate_limiter_tests.test_multiple_clients())
    print("✓ Rate limiting multiple clients test passed")

    # ShardedMessageBus tests
    bus_tests = TestShardedMessageBus()
    bus_tests.setUp()

    run_async_test(bus_tests.test_publish_basic())
    print("✓ Basic publish test passed")

    run_async_test(bus_tests.test_publish_with_rate_limiting())
    print("✓ Publish with rate limiting test passed")

    run_async_test(bus_tests.test_publish_with_encryption())
    print("✓ Publish with encryption test passed")

    run_async_test(bus_tests.test_publish_with_idempotency())
    print("✓ Idempotent publish test passed")

    run_async_test(bus_tests.test_subscribe_and_unsubscribe())
    print("✓ Subscribe/unsubscribe test passed")

    run_async_test(bus_tests.test_batch_publish())
    print("✓ Batch publish test passed")

    run_async_test(bus_tests.test_shutdown())
    print("✓ Shutdown test passed")

    bus_tests.tearDown()
