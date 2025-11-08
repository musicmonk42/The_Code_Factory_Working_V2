# test_dead_letter_queue.py

import unittest
import asyncio
import time
from unittest.mock import Mock, AsyncMock, MagicMock, patch, call
import sys
from pathlib import Path
import json

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from message_bus.dead_letter_queue import DeadLetterQueue, KAFKA_AVAILABLE
from message_bus.message_types import Message
from message_bus.resilience import CircuitBreaker


class TestDeadLetterQueue(unittest.TestCase):
    """Test suite for DeadLetterQueue class."""

    def setUp(self):
        """Set up test fixtures before each test."""
        # Create mock database
        self.mock_db = Mock()
        self.mock_db.save_preferences = AsyncMock()
        
        # Create mock Kafka bridge
        self.mock_kafka_bridge = Mock()
        self.mock_kafka_bridge.publish = AsyncMock()
        self.mock_kafka_bridge.circuit = Mock(spec=CircuitBreaker)
        self.mock_kafka_bridge.circuit.can_attempt = Mock(return_value=True)
        self.mock_kafka_bridge.circuit.record_failure = Mock()
        
        # Mock settings
        self.mock_settings = Mock()
        self.mock_settings.DLQ_MAX_RETRIES = 3
        self.mock_settings.DLQ_BACKOFF_FACTOR = 1.5
        
        # Create DLQ instance
        with patch('message_bus.dead_letter_queue.settings', self.mock_settings):
            self.dlq = DeadLetterQueue(
                db=self.mock_db,
                kafka_bridge=self.mock_kafka_bridge,
                priority_threshold=5
            )

    async def tearDown_async(self):
        """Async teardown to properly shutdown DLQ."""
        if hasattr(self, 'dlq'):
            await self.dlq.shutdown()

    def tearDown(self):
        """Synchronous teardown wrapper."""
        asyncio.run(self.tearDown_async())

    def test_initialization(self):
        """Test DeadLetterQueue initialization."""
        self.assertEqual(self.dlq.db, self.mock_db)
        self.assertEqual(self.dlq.kafka_bridge, self.mock_kafka_bridge)
        self.assertEqual(self.dlq.priority_threshold, 5)
        self.assertTrue(self.dlq.running)
        self.assertIsNotNone(self.dlq.queue)
        self.assertEqual(self.dlq.max_retries, 3)
        self.assertEqual(self.dlq.backoff_factor, 1.5)

    def test_initialization_without_kafka(self):
        """Test initialization without Kafka bridge."""
        dlq = DeadLetterQueue(
            db=self.mock_db,
            kafka_bridge=None,
            priority_threshold=5
        )
        
        self.assertIsNone(dlq.kafka_bridge)
        self.assertEqual(dlq.priority_threshold, 5)

    @patch('message_bus.dead_letter_queue.logger')
    async def test_add_message_basic(self, mock_logger):
        """Test adding a message to DLQ."""
        message = Message(
            topic="test.topic",
            payload={"data": "test"},
            trace_id="trace_123",
            timestamp=time.time(),
            idempotency_key="key_123"
        )
        
        error = "Test error message"
        
        await self.dlq.add(message, error)
        
        # Verify message was queued
        self.assertEqual(self.dlq.queue.qsize(), 1)
        
        # Verify database persistence was called
        self.mock_db.save_preferences.assert_called_once()
        call_args = self.mock_db.save_preferences.call_args
        
        # Check user_id format
        self.assertTrue(call_args[1]['user_id'].startswith('dlq_message_'))
        
        # Check preferences content
        prefs = call_args[1]['prefs']
        self.assertEqual(prefs['topic'], 'test.topic')
        self.assertEqual(prefs['original_trace_id'], 'trace_123')
        self.assertEqual(prefs['idempotency_key'], 'key_123')
        self.assertIn('Error Type:', prefs['error'])
        
        # Verify logging
        mock_logger.error.assert_called()

    @patch('message_bus.dead_letter_queue.logger')
    async def test_add_message_db_persistence_failure(self, mock_logger):
        """Test handling database persistence failure."""
        self.mock_db.save_preferences.side_effect = Exception("DB error")
        
        message = Message(
            topic="test.topic",
            payload={"data": "test"},
            trace_id="trace_123"
        )
        
        await self.dlq.add(message, "Test error")
        
        # Message should still be queued despite DB error
        self.assertEqual(self.dlq.queue.qsize(), 1)
        
        # Error should be logged
        mock_logger.error.assert_any_call(
            "Failed to persist DLQ message to database: DB error",
            trace_id="trace_123"
        )

    @patch('message_bus.dead_letter_queue.logger')
    @patch('message_bus.dead_letter_queue.asyncio.sleep', new_callable=AsyncMock)
    async def test_process_dlq_kafka_success(self, mock_sleep, mock_logger):
        """Test successful processing of DLQ message to Kafka."""
        message = Message(
            topic="test.topic",
            payload={"data": "test"},
            trace_id="trace_123"
        )
        
        # Add message to queue
        await self.dlq.queue.put((message, "error", 0))
        
        # Process one iteration
        self.dlq.running = False  # Stop after one iteration
        await self.dlq._process_dlq()
        
        # Verify Kafka publish was called
        self.mock_kafka_bridge.publish.assert_called_once_with(
            message, topic="dlq_events"
        )
        
        # Verify circuit breaker was checked
        self.mock_kafka_bridge.circuit.can_attempt.assert_called_once()
        
        # Verify logging
        mock_logger.info.assert_any_call(
            "DLQ message published to Kafka bridge.",
            trace_id="trace_123"
        )

    @patch('message_bus.dead_letter_queue.logger')
    @patch('message_bus.dead_letter_queue.asyncio.sleep', new_callable=AsyncMock)
    async def test_process_dlq_kafka_failure_with_retry(self, mock_sleep, mock_logger):
        """Test Kafka publish failure with retry logic."""
        message = Message(
            topic="test.topic",
            payload={"data": "test"},
            trace_id="trace_123"
        )
        
        # Make Kafka publish fail
        self.mock_kafka_bridge.publish.side_effect = Exception("Kafka error")
        
        # Add message to queue
        await self.dlq.queue.put((message, "error", 0))
        
        # Process one iteration
        self.dlq.running = False
        await self.dlq._process_dlq()
        
        # Verify circuit breaker recorded failure
        self.mock_kafka_bridge.circuit.record_failure.assert_called_once()
        
        # Verify backoff sleep was called
        mock_sleep.assert_called_once_with(1.5)  # backoff_factor * (2 ** 0)
        
        # Verify message was re-queued with incremented retry count
        self.assertEqual(self.dlq.queue.qsize(), 1)
        requeued_item = await self.dlq.queue.get()
        self.assertEqual(requeued_item[2], 1)  # retry count should be 1

    @patch('message_bus.dead_letter_queue.logger')
    @patch('message_bus.dead_letter_queue.asyncio.sleep', new_callable=AsyncMock)
    async def test_process_dlq_max_retries_exceeded(self, mock_sleep, mock_logger):
        """Test message dropped after max retries."""
        message = Message(
            topic="test.topic",
            payload={"data": "test"},
            trace_id="trace_123"
        )
        
        # Make Kafka publish fail
        self.mock_kafka_bridge.publish.side_effect = Exception("Kafka error")
        
        # Add message with max retries already reached
        await self.dlq.queue.put((message, "error", 3))
        
        # Process one iteration
        self.dlq.running = False
        await self.dlq._process_dlq()
        
        # Message should not be re-queued
        self.assertEqual(self.dlq.queue.qsize(), 0)
        
        # Critical log should be generated
        mock_logger.critical.assert_called_once()
        self.assertIn("failed to process after 3 attempts", 
                     mock_logger.critical.call_args[0][0])

    @patch('message_bus.dead_letter_queue.logger')
    async def test_process_dlq_circuit_open(self, mock_logger):
        """Test behavior when Kafka circuit breaker is open."""
        # Set circuit to open
        self.mock_kafka_bridge.circuit.can_attempt.return_value = False
        
        message = Message(
            topic="test.topic",
            payload={"data": "test"},
            trace_id="trace_123"
        )
        
        await self.dlq.queue.put((message, "error", 0))
        
        # Process one iteration
        self.dlq.running = False
        await self.dlq._process_dlq()
        
        # Kafka publish should not be called
        self.mock_kafka_bridge.publish.assert_not_called()
        
        # Warning should be logged
        mock_logger.warning.assert_called_with(
            "Kafka circuit is open. Skipping DLQ message publish to Kafka.",
            trace_id="trace_123"
        )

    @patch('message_bus.dead_letter_queue.KAFKA_AVAILABLE', True)
    @patch('message_bus.dead_letter_queue.logger')
    async def test_process_dlq_kafka_available_but_no_bridge(self, mock_logger):
        """Test when Kafka is available but bridge is not initialized."""
        # Create DLQ without Kafka bridge
        dlq = DeadLetterQueue(
            db=self.mock_db,
            kafka_bridge=None,
            priority_threshold=5
        )
        
        message = Message(
            topic="test.topic",
            payload={"data": "test"},
            trace_id="trace_123"
        )
        
        await dlq.queue.put((message, "error", 0))
        
        # Process one iteration
        dlq.running = False
        await dlq._process_dlq()
        
        # Warning should be logged
        mock_logger.warning.assert_called_with(
            "Kafka is available but the Kafka bridge is not initialized. Skipping DLQ publish."
        )
        
        await dlq.shutdown()

    @patch('message_bus.dead_letter_queue.logger')
    @patch('message_bus.dead_letter_queue.asyncio.sleep', new_callable=AsyncMock)
    async def test_process_dlq_cancellation(self, mock_sleep, mock_logger):
        """Test graceful cancellation of DLQ processing."""
        # Make queue.get block indefinitely
        self.dlq.queue.get = AsyncMock(side_effect=asyncio.CancelledError)
        
        await self.dlq._process_dlq()
        
        # Should handle cancellation gracefully
        mock_logger.info.assert_called_with("DLQ processing task cancelled.")

    @patch('message_bus.dead_letter_queue.logger')
    @patch('message_bus.dead_letter_queue.asyncio.sleep', new_callable=AsyncMock)
    async def test_process_dlq_unexpected_error(self, mock_sleep, mock_logger):
        """Test handling of unexpected errors in processing loop."""
        # Make queue.get raise unexpected error
        self.dlq.queue.get = AsyncMock(side_effect=RuntimeError("Unexpected"))
        
        # Process should continue after error
        call_count = 0
        
        async def side_effect():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Unexpected")
            else:
                self.dlq.running = False
                raise asyncio.CancelledError
        
        self.dlq.queue.get = AsyncMock(side_effect=side_effect)
        
        await self.dlq._process_dlq()
        
        # Error should be logged
        mock_logger.error.assert_any_call(
            "Unexpected error in DLQ processing loop: Unexpected. Sleeping before next attempt.",
            exc_info=True
        )
        
        # Should sleep before retry
        mock_sleep.assert_any_call(1)

    async def test_shutdown(self):
        """Test DLQ shutdown."""
        # Add some messages to queue
        message = Message(
            topic="test.topic",
            payload={"data": "test"}
        )
        await self.dlq.queue.put((message, "error", 0))
        
        # Shutdown
        await self.dlq.shutdown()
        
        # Verify state
        self.assertFalse(self.dlq.running)
        
        # Task should be cancelled
        self.assertTrue(self.dlq._dlq_task.cancelled() or self.dlq._dlq_task.done())

    @patch('message_bus.dead_letter_queue.logger')
    async def test_multiple_messages_processing(self, mock_logger):
        """Test processing multiple messages in sequence."""
        messages = [
            Message(topic=f"topic_{i}", payload={"data": f"test_{i}"}, trace_id=f"trace_{i}")
            for i in range(3)
        ]
        
        # Add messages to queue
        for msg in messages:
            await self.dlq.queue.put((msg, f"error_{msg.trace_id}", 0))
        
        # Process all messages
        processed_count = 0
        
        async def count_publishes(*args, **kwargs):
            nonlocal processed_count
            processed_count += 1
        
        self.mock_kafka_bridge.publish = count_publishes
        
        # Process until queue is empty
        while not self.dlq.queue.empty():
            try:
                message, error, retries = await self.dlq.queue.get()
                if self.mock_kafka_bridge.circuit.can_attempt():
                    await self.mock_kafka_bridge.publish(message, topic="dlq_events")
                self.dlq.queue.task_done()
            except:
                break
        
        self.assertEqual(processed_count, 3)

    async def test_backoff_calculation(self):
        """Test exponential backoff calculation."""
        backoff_factor = 1.5
        
        # Test different retry counts
        expected_delays = [
            (0, 1.5),      # 1.5 * (2 ** 0) = 1.5
            (1, 3.0),      # 1.5 * (2 ** 1) = 3.0
            (2, 6.0),      # 1.5 * (2 ** 2) = 6.0
        ]
        
        for retry_count, expected_delay in expected_delays:
            actual_delay = backoff_factor * (2 ** retry_count)
            self.assertEqual(actual_delay, expected_delay)

    def test_error_formatting(self):
        """Test error message formatting."""
        error = ValueError("Test error")
        full_error = f"Error Type: {type(error).__name__}, Message: {error}"
        
        self.assertEqual(full_error, "Error Type: ValueError, Message: Test error")


class TestDeadLetterQueueIntegration(unittest.TestCase):
    """Integration tests for DeadLetterQueue."""

    async def test_full_flow_with_real_queue(self):
        """Test full DLQ flow with real async queue."""
        mock_db = Mock()
        mock_db.save_preferences = AsyncMock()
        
        mock_kafka = Mock()
        mock_kafka.publish = AsyncMock()
        mock_kafka.circuit = Mock(spec=CircuitBreaker)
        mock_kafka.circuit.can_attempt = Mock(return_value=True)
        
        dlq = DeadLetterQueue(
            db=mock_db,
            kafka_bridge=mock_kafka,
            priority_threshold=5
        )
        
        # Add multiple messages
        messages = []
        for i in range(5):
            msg = Message(
                topic=f"topic_{i}",
                payload={"data": f"test_{i}"},
                trace_id=f"trace_{i}"
            )
            messages.append(msg)
            await dlq.add(msg, f"Error {i}")
        
        # Wait for processing
        await asyncio.sleep(0.1)
        
        # Verify all messages were persisted
        self.assertEqual(mock_db.save_preferences.call_count, 5)
        
        # Shutdown and verify
        await dlq.shutdown()
        self.assertFalse(dlq.running)


def run_async_test(coro):
    """Helper to run async tests."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


if __name__ == '__main__':
    # Run standard unit tests
    unittest.main(argv=[''], exit=False, verbosity=2)
    
    # Run async integration tests
    print("\n" + "="*70)
    print("Running Async Integration Tests")
    print("="*70)
    
    integration_suite = unittest.TestLoader().loadTestsFromTestCase(TestDeadLetterQueueIntegration)
    for test in integration_suite:
        test_method = getattr(test, test._testMethodName)
        if asyncio.iscoroutinefunction(test_method):
            print(f"Running: {test._testMethodName}")
            run_async_test(test_method())
            print("✓ Passed")