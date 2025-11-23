# test_backpressure.py

import asyncio
import unittest
from unittest.mock import Mock, AsyncMock, patch
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from message_bus.backpressure import BackpressureManager


class TestBackpressureManager(unittest.TestCase):
    """Test suite for BackpressureManager class."""

    def setUp(self):
        """Set up test fixtures before each test."""
        # Create mock message bus
        self.mock_message_bus = Mock()
        self.mock_message_bus.shard_count = 4
        self.mock_message_bus.max_queue_size = 100

        # Create mock queues for each shard
        self.mock_queues = []
        self.mock_hp_queues = []

        for i in range(4):
            mock_queue = Mock()
            mock_queue.qsize = Mock(return_value=50)  # Default to 50% full
            self.mock_queues.append(mock_queue)

            mock_hp_queue = Mock()
            mock_hp_queue.qsize = Mock(return_value=30)  # Default to 30% full
            self.mock_hp_queues.append(mock_hp_queue)

        self.mock_message_bus.queues = self.mock_queues
        self.mock_message_bus.high_priority_queues = self.mock_hp_queues

        # Add async mock methods for pause/resume
        self.mock_message_bus.pause_publishes = AsyncMock()
        self.mock_message_bus.resume_publishes = AsyncMock()
        self.mock_message_bus.publish = AsyncMock(return_value=True)

        # Create BackpressureManager instance
        self.manager = BackpressureManager(self.mock_message_bus, threshold=0.8)

    def test_initialization(self):
        """Test BackpressureManager initialization."""
        self.assertEqual(self.manager.threshold, 0.8)
        self.assertEqual(self.manager.message_bus, self.mock_message_bus)
        self.assertEqual(len(self.manager.is_paused), 4)
        self.assertFalse(any(self.manager.is_paused))

    def test_initialization_invalid_threshold(self):
        """Test initialization with invalid threshold values."""
        # Test threshold = 0
        with self.assertRaises(ValueError) as context:
            BackpressureManager(self.mock_message_bus, threshold=0)
        self.assertIn("between 0 and 1", str(context.exception))

        # Test threshold > 1
        with self.assertRaises(ValueError) as context:
            BackpressureManager(self.mock_message_bus, threshold=1.5)
        self.assertIn("between 0 and 1", str(context.exception))

        # Test negative threshold
        with self.assertRaises(ValueError) as context:
            BackpressureManager(self.mock_message_bus, threshold=-0.5)
        self.assertIn("between 0 and 1", str(context.exception))

    @patch("message_bus.backpressure.logger")
    async def test_check_and_notify_no_backpressure(self, mock_logger):
        """Test check_and_notify when no backpressure is needed."""
        shard_id = 0
        # Set queue sizes below threshold (50% and 30% of max)
        self.mock_queues[shard_id].qsize.return_value = 50
        self.mock_hp_queues[shard_id].qsize.return_value = 30

        await self.manager.check_and_notify(shard_id)

        # Should not pause or publish any notifications
        self.mock_message_bus.pause_publishes.assert_not_called()
        self.mock_message_bus.resume_publishes.assert_not_called()
        self.mock_message_bus.publish.assert_not_called()
        self.assertFalse(self.manager.is_paused[shard_id])

    @patch("message_bus.backpressure.logger")
    async def test_check_and_notify_trigger_pause(self, mock_logger):
        """Test triggering pause when queue reaches threshold."""
        shard_id = 0
        # Set normal queue size at threshold (80% of max)
        self.mock_queues[shard_id].qsize.return_value = 80
        self.mock_hp_queues[shard_id].qsize.return_value = 30

        await self.manager.check_and_notify(shard_id)

        # Should pause publishes
        self.mock_message_bus.pause_publishes.assert_called_once_with(shard_id)

        # Should publish backpressure notification
        self.mock_message_bus.publish.assert_called_once()
        call_args = self.mock_message_bus.publish.call_args
        self.assertEqual(call_args[0][0], "message_bus.backpressure")
        self.assertEqual(call_args[0][1]["event"], "pause")
        self.assertEqual(call_args[0][1]["shard_id"], shard_id)
        self.assertEqual(call_args[0][1]["queue_size"], 80)
        self.assertEqual(call_args[0][1]["hp_queue_size"], 30)

        # Should mark shard as paused
        self.assertTrue(self.manager.is_paused[shard_id])

        # Verify warning was logged
        mock_logger.warning.assert_called_once()

    @patch("message_bus.backpressure.logger")
    async def test_check_and_notify_trigger_pause_hp_queue(self, mock_logger):
        """Test triggering pause when high-priority queue reaches threshold."""
        shard_id = 1
        # Set HP queue size at threshold (80% of max)
        self.mock_queues[shard_id].qsize.return_value = 30
        self.mock_hp_queues[shard_id].qsize.return_value = 80

        await self.manager.check_and_notify(shard_id)

        # Should pause publishes
        self.mock_message_bus.pause_publishes.assert_called_once_with(shard_id)
        self.assertTrue(self.manager.is_paused[shard_id])

    @patch("message_bus.backpressure.logger")
    async def test_check_and_notify_trigger_resume(self, mock_logger):
        """Test triggering resume when queue drops below threshold."""
        shard_id = 0
        # First, mark shard as paused
        self.manager.is_paused[shard_id] = True

        # Set queue sizes below threshold
        self.mock_queues[shard_id].qsize.return_value = 70
        self.mock_hp_queues[shard_id].qsize.return_value = 60

        await self.manager.check_and_notify(shard_id)

        # Should resume publishes
        self.mock_message_bus.resume_publishes.assert_called_once_with(shard_id)

        # Should publish resume notification
        self.mock_message_bus.publish.assert_called_once()
        call_args = self.mock_message_bus.publish.call_args
        self.assertEqual(call_args[0][1]["event"], "resume")

        # Should mark shard as not paused
        self.assertFalse(self.manager.is_paused[shard_id])

        # Verify info was logged
        mock_logger.info.assert_called_once()

    @patch("message_bus.backpressure.logger")
    async def test_check_and_notify_already_paused(self, mock_logger):
        """Test check_and_notify when already paused and still over threshold."""
        shard_id = 0
        # Mark as already paused
        self.manager.is_paused[shard_id] = True

        # Set queue sizes still over threshold
        self.mock_queues[shard_id].qsize.return_value = 85
        self.mock_hp_queues[shard_id].qsize.return_value = 30

        await self.manager.check_and_notify(shard_id)

        # Should not call pause again
        self.mock_message_bus.pause_publishes.assert_not_called()
        self.mock_message_bus.publish.assert_not_called()

        # Should remain paused
        self.assertTrue(self.manager.is_paused[shard_id])

    @patch("message_bus.backpressure.logger")
    async def test_check_and_notify_pause_exception_handling(self, mock_logger):
        """Test exception handling when pause_publishes fails."""
        shard_id = 0
        # Set queue at threshold
        self.mock_queues[shard_id].qsize.return_value = 80

        # Make pause_publishes raise an exception
        self.mock_message_bus.pause_publishes.side_effect = Exception("Pause failed")

        await self.manager.check_and_notify(shard_id)

        # Should log error
        mock_logger.error.assert_called_once()
        self.assertIn(
            "Failed to publish backpressure notification",
            mock_logger.error.call_args[0][0],
        )

        # Should still mark as paused
        self.assertTrue(self.manager.is_paused[shard_id])

    @patch("message_bus.backpressure.logger")
    async def test_check_and_notify_resume_exception_handling(self, mock_logger):
        """Test exception handling when resume_publishes fails."""
        shard_id = 0
        # Mark as paused
        self.manager.is_paused[shard_id] = True

        # Set queue below threshold
        self.mock_queues[shard_id].qsize.return_value = 50

        # Make resume_publishes raise an exception
        self.mock_message_bus.resume_publishes.side_effect = Exception("Resume failed")

        await self.manager.check_and_notify(shard_id)

        # Should log error
        mock_logger.error.assert_called_once()
        self.assertIn(
            "Failed to publish backpressure resumption",
            mock_logger.error.call_args[0][0],
        )

        # Should still mark as not paused
        self.assertFalse(self.manager.is_paused[shard_id])

    @patch("message_bus.backpressure.logger")
    async def test_check_and_notify_publish_notification_failure(self, mock_logger):
        """Test handling when publishing notification fails."""
        shard_id = 0
        # Set queue at threshold
        self.mock_queues[shard_id].qsize.return_value = 80

        # Make publish raise an exception
        self.mock_message_bus.publish.side_effect = Exception("Publish failed")

        await self.manager.check_and_notify(shard_id)

        # Should still pause
        self.mock_message_bus.pause_publishes.assert_called_once_with(shard_id)

        # Should log error
        mock_logger.error.assert_called_once()

        # Should still mark as paused
        self.assertTrue(self.manager.is_paused[shard_id])

    async def test_multiple_shards_independent(self):
        """Test that multiple shards are managed independently."""
        # Pause shard 0
        self.mock_queues[0].qsize.return_value = 85
        await self.manager.check_and_notify(0)
        self.assertTrue(self.manager.is_paused[0])

        # Shard 1 should remain unpaused
        self.mock_queues[1].qsize.return_value = 50
        await self.manager.check_and_notify(1)
        self.assertFalse(self.manager.is_paused[1])

        # Pause shard 2
        self.mock_hp_queues[2].qsize.return_value = 90
        await self.manager.check_and_notify(2)
        self.assertTrue(self.manager.is_paused[2])

        # Check states
        self.assertTrue(self.manager.is_paused[0])
        self.assertFalse(self.manager.is_paused[1])
        self.assertTrue(self.manager.is_paused[2])
        self.assertFalse(self.manager.is_paused[3])

    async def test_threshold_boundary_conditions(self):
        """Test behavior at exact threshold boundaries."""
        shard_id = 0

        # Test at exactly threshold (80)
        self.mock_queues[shard_id].qsize.return_value = 80
        self.mock_hp_queues[shard_id].qsize.return_value = 0
        await self.manager.check_and_notify(shard_id)
        self.assertTrue(self.manager.is_paused[shard_id])

        # Reset
        self.manager.is_paused[shard_id] = False
        self.mock_message_bus.reset_mock()

        # Test just below threshold (79)
        self.mock_queues[shard_id].qsize.return_value = 79
        await self.manager.check_and_notify(shard_id)
        self.assertFalse(self.manager.is_paused[shard_id])

    async def test_custom_threshold(self):
        """Test with different threshold values."""
        # Create manager with 50% threshold
        manager_50 = BackpressureManager(self.mock_message_bus, threshold=0.5)

        shard_id = 0
        # Set at 50% capacity
        self.mock_queues[shard_id].qsize.return_value = 50

        await manager_50.check_and_notify(shard_id)
        self.assertTrue(manager_50.is_paused[shard_id])

        # Create manager with 90% threshold
        manager_90 = BackpressureManager(self.mock_message_bus, threshold=0.9)

        # 50% should not trigger pause for 90% threshold
        await manager_90.check_and_notify(shard_id)
        self.assertFalse(manager_90.is_paused[shard_id])


class TestBackpressureIntegration(unittest.TestCase):
    """Integration tests for BackpressureManager with real async queues."""

    async def test_real_queue_integration(self):
        """Test with actual asyncio.Queue objects."""
        # Create mock message bus with real queues
        mock_bus = Mock()
        mock_bus.shard_count = 2
        mock_bus.max_queue_size = 10

        # Create real async queues
        queue1 = asyncio.Queue(maxsize=10)
        queue2 = asyncio.Queue(maxsize=10)
        hp_queue1 = asyncio.Queue(maxsize=10)
        hp_queue2 = asyncio.Queue(maxsize=10)

        mock_bus.queues = [queue1, queue2]
        mock_bus.high_priority_queues = [hp_queue1, hp_queue2]
        mock_bus.pause_publishes = AsyncMock()
        mock_bus.resume_publishes = AsyncMock()
        mock_bus.publish = AsyncMock()

        manager = BackpressureManager(mock_bus, threshold=0.8)

        # Fill queue1 to 80%
        for _ in range(8):
            await queue1.put("item")

        # Check should trigger pause
        await manager.check_and_notify(0)
        mock_bus.pause_publishes.assert_called_once_with(0)

        # Drain queue1 to 50%
        for _ in range(3):
            await queue1.get()

        # Check should trigger resume
        await manager.check_and_notify(0)
        mock_bus.resume_publishes.assert_called_once_with(0)


def run_async_test(coro):
    """Helper to run async tests."""
    loop = asyncio.get_event_loop()
    return loop.run_until_complete(coro)


if __name__ == "__main__":
    # Run standard unit tests
    unittest.main(argv=[""], exit=False, verbosity=2)

    # Run async integration tests
    print("\n" + "=" * 70)
    print("Running Async Integration Tests")
    print("=" * 70)

    integration_suite = unittest.TestLoader().loadTestsFromTestCase(TestBackpressureIntegration)
    for test in integration_suite:
        test_method = getattr(test, test._testMethodName)
        if asyncio.iscoroutinefunction(test_method):
            print(f"Running: {test._testMethodName}")
            run_async_test(test_method())
            print("✓ Passed")
