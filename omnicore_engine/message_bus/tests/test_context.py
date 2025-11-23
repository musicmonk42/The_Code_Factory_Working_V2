# test_context.py

import unittest
import asyncio
import concurrent.futures
from unittest.mock import Mock, AsyncMock
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from message_bus.context import ExecutionContext, ContextPropagationMiddleware
from message_bus.message_types import Message


class TestExecutionContext(unittest.TestCase):
    """Test suite for ExecutionContext class."""

    def setUp(self):
        """Set up test fixtures before each test."""
        # Clear any existing context before each test
        ExecutionContext.clear()

    def tearDown(self):
        """Clean up after each test."""
        ExecutionContext.clear()

    def test_get_current_empty(self):
        """Test getting current context when empty."""
        context = ExecutionContext.get_current()
        self.assertIsInstance(context, dict)
        self.assertEqual(len(context), 0)

    def test_set_single_value(self):
        """Test setting a single context value."""
        ExecutionContext.set(user_id="test_user")
        context = ExecutionContext.get_current()

        self.assertEqual(context["user_id"], "test_user")
        self.assertEqual(len(context), 1)

    def test_set_multiple_values(self):
        """Test setting multiple context values."""
        ExecutionContext.set(user_id="test_user", request_id="req_123", trace_id="trace_456")
        context = ExecutionContext.get_current()

        self.assertEqual(context["user_id"], "test_user")
        self.assertEqual(context["request_id"], "req_123")
        self.assertEqual(context["trace_id"], "trace_456")
        self.assertEqual(len(context), 3)

    def test_set_overwrites_existing(self):
        """Test that set overwrites existing values."""
        ExecutionContext.set(user_id="user1")
        ExecutionContext.set(user_id="user2")

        context = ExecutionContext.get_current()
        self.assertEqual(context["user_id"], "user2")

    def test_clear(self):
        """Test clearing the context."""
        ExecutionContext.set(user_id="test_user", request_id="req_123")

        # Verify context is set
        self.assertEqual(len(ExecutionContext.get_current()), 2)

        # Clear and verify
        ExecutionContext.clear()
        context = ExecutionContext.get_current()
        self.assertEqual(len(context), 0)

    def test_thread_local_isolation(self):
        """Test that context is thread-local."""
        results = {}

        def set_and_get_context(thread_id):
            # Set context unique to this thread
            ExecutionContext.set(thread_id=thread_id, value=f"thread_{thread_id}_value")

            # Get and store the context
            results[thread_id] = ExecutionContext.get_current().copy()

        # Run in multiple threads
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(set_and_get_context, i) for i in range(5)]
            concurrent.futures.wait(futures)

        # Verify each thread had its own context
        for thread_id in range(5):
            self.assertEqual(results[thread_id]["thread_id"], thread_id)
            self.assertEqual(results[thread_id]["value"], f"thread_{thread_id}_value")

        # Main thread should have empty context
        main_context = ExecutionContext.get_current()
        self.assertEqual(len(main_context), 0)

    def test_context_with_different_types(self):
        """Test setting context with different value types."""
        ExecutionContext.set(
            string_val="test",
            int_val=42,
            float_val=3.14,
            bool_val=True,
            list_val=[1, 2, 3],
            dict_val={"a": 1, "b": 2},
            none_val=None,
        )

        context = ExecutionContext.get_current()

        self.assertEqual(context["string_val"], "test")
        self.assertEqual(context["int_val"], 42)
        self.assertEqual(context["float_val"], 3.14)
        self.assertEqual(context["bool_val"], True)
        self.assertEqual(context["list_val"], [1, 2, 3])
        self.assertEqual(context["dict_val"], {"a": 1, "b": 2})
        self.assertIsNone(context["none_val"])

    def test_context_modification_affects_original(self):
        """Test that modifying returned context affects the original."""
        context = ExecutionContext.get_current()
        context["manual_key"] = "manual_value"

        # Get context again and verify modification persists
        new_context = ExecutionContext.get_current()
        self.assertEqual(new_context["manual_key"], "manual_value")

    def test_multiple_clear_calls(self):
        """Test that multiple clear calls don't cause errors."""
        ExecutionContext.set(key="value")
        ExecutionContext.clear()
        ExecutionContext.clear()  # Should not raise

        context = ExecutionContext.get_current()
        self.assertEqual(len(context), 0)


class TestContextPropagationMiddleware(unittest.TestCase):
    """Test suite for ContextPropagationMiddleware class."""

    def setUp(self):
        """Set up test fixtures before each test."""
        # Clear context
        ExecutionContext.clear()

        # Create mock message bus
        self.mock_message_bus = Mock()
        self.mock_message_bus.add_pre_publish_hook = Mock()
        self.mock_message_bus._safe_callback_internal = AsyncMock()

        # Create middleware
        self.middleware = ContextPropagationMiddleware(self.mock_message_bus)

    def tearDown(self):
        """Clean up after each test."""
        ExecutionContext.clear()

    def test_initialization(self):
        """Test middleware initialization."""
        # Verify pre-publish hook was added
        self.mock_message_bus.add_pre_publish_hook.assert_called_once()

        # Get the hook function
        hook_func = self.mock_message_bus.add_pre_publish_hook.call_args[0][0]
        self.assertEqual(hook_func, self.middleware._inject_context)

    def test_inject_context_empty_message(self):
        """Test injecting context into a message with no existing context."""
        # Set execution context
        ExecutionContext.set(user_id="test_user", trace_id="trace_123")

        # Create message with no context
        message = Message(topic="test.topic", payload={"data": "test"}, context=None)

        # Inject context
        updated_message = self.middleware._inject_context(message)

        # Verify context was injected
        self.assertIsNotNone(updated_message.context)
        self.assertEqual(updated_message.context["user_id"], "test_user")
        self.assertEqual(updated_message.context["trace_id"], "trace_123")

    def test_inject_context_preserves_existing(self):
        """Test that existing message context is preserved."""
        # Set execution context
        ExecutionContext.set(user_id="test_user")

        # Create message with existing context
        existing_context = {"existing_key": "existing_value"}
        message = Message(topic="test.topic", payload={"data": "test"}, context=existing_context)

        # Inject context
        updated_message = self.middleware._inject_context(message)

        # Verify existing context is preserved
        self.assertEqual(updated_message.context["existing_key"], "existing_value")
        # ExecutionContext should NOT be added
        self.assertNotIn("user_id", updated_message.context)

    def test_inject_context_empty_execution_context(self):
        """Test injecting when execution context is empty."""
        # Ensure execution context is empty
        ExecutionContext.clear()

        # Create message with no context
        message = Message(topic="test.topic", payload={"data": "test"}, context=None)

        # Inject context
        updated_message = self.middleware._inject_context(message)

        # Verify empty context was injected
        self.assertIsNotNone(updated_message.context)
        self.assertEqual(len(updated_message.context), 0)

    async def test_restore_context_wrapper_basic(self):
        """Test basic context restoration in wrapper."""
        # Set initial context
        ExecutionContext.set(initial_key="initial_value")

        # Create message with different context
        message = Message(
            topic="test.topic",
            payload={"data": "test"},
            context={"message_key": "message_value"},
        )

        # Create mock callback
        callback = AsyncMock()

        # Run wrapper
        await self.middleware._restore_context_wrapper(callback, message, None)

        # Verify callback was called with correct context
        self.mock_message_bus._safe_callback_internal.assert_called_once_with(
            callback, message, None
        )

        # Verify original context is restored
        context = ExecutionContext.get_current()
        self.assertEqual(context["initial_key"], "initial_value")
        self.assertNotIn("message_key", context)

    async def test_restore_context_wrapper_with_filter(self):
        """Test context restoration with filter."""
        message = Message(
            topic="test.topic",
            payload={"data": "test"},
            context={"message_key": "message_value"},
        )

        callback = AsyncMock()
        mock_filter = Mock()

        await self.middleware._restore_context_wrapper(callback, message, mock_filter)

        # Verify filter was passed correctly
        self.mock_message_bus._safe_callback_internal.assert_called_once_with(
            callback, message, mock_filter
        )

    async def test_restore_context_wrapper_exception_handling(self):
        """Test that context is restored even if callback raises exception."""
        # Set initial context
        ExecutionContext.set(initial_key="initial_value")

        # Create message with different context
        message = Message(
            topic="test.topic",
            payload={"data": "test"},
            context={"message_key": "message_value"},
        )

        # Make callback raise exception
        self.mock_message_bus._safe_callback_internal.side_effect = Exception("Callback error")

        callback = AsyncMock()

        # Run wrapper and expect exception
        with self.assertRaises(Exception) as context:
            await self.middleware._restore_context_wrapper(callback, message, None)

        self.assertEqual(str(context.exception), "Callback error")

        # Verify original context is still restored
        context = ExecutionContext.get_current()
        self.assertEqual(context["initial_key"], "initial_value")
        self.assertNotIn("message_key", context)

    async def test_restore_context_wrapper_empty_message_context(self):
        """Test restoration when message has no context."""
        # Set initial context
        ExecutionContext.set(initial_key="initial_value")

        # Create message with no context
        message = Message(topic="test.topic", payload={"data": "test"}, context=None)

        callback = AsyncMock()

        # Capture context during callback
        captured_context = None

        async def capture_context(*args, **kwargs):
            nonlocal captured_context
            captured_context = ExecutionContext.get_current().copy()

        self.mock_message_bus._safe_callback_internal.side_effect = capture_context

        await self.middleware._restore_context_wrapper(callback, message, None)

        # During callback, context should have been cleared
        self.assertEqual(len(captured_context), 0)

        # After callback, original context should be restored
        context = ExecutionContext.get_current()
        self.assertEqual(context["initial_key"], "initial_value")

    async def test_context_isolation_between_callbacks(self):
        """Test that context changes in one callback don't affect others."""
        # Create two messages with different contexts
        message1 = Message(topic="topic1", payload={"data": "test1"}, context={"msg_id": "msg1"})

        message2 = Message(topic="topic2", payload={"data": "test2"}, context={"msg_id": "msg2"})

        callback1 = AsyncMock()
        callback2 = AsyncMock()

        captured_contexts = []

        async def capture_context(*args, **kwargs):
            captured_contexts.append(ExecutionContext.get_current().copy())

        self.mock_message_bus._safe_callback_internal.side_effect = capture_context

        # Run both wrappers concurrently
        await asyncio.gather(
            self.middleware._restore_context_wrapper(callback1, message1, None),
            self.middleware._restore_context_wrapper(callback2, message2, None),
        )

        # Verify each callback had its own context
        self.assertEqual(len(captured_contexts), 2)
        # Note: Order might vary due to concurrency
        msg_ids = [ctx.get("msg_id") for ctx in captured_contexts]
        self.assertIn("msg1", msg_ids)
        self.assertIn("msg2", msg_ids)

    def test_context_copy_is_shallow(self):
        """Test that context copying behavior."""
        # Set context with mutable object
        mutable_list = [1, 2, 3]
        ExecutionContext.set(my_list=mutable_list)

        # Create message and inject context
        message = Message(topic="test.topic", payload={"data": "test"}, context=None)

        updated_message = self.middleware._inject_context(message)

        # Modify the original list
        mutable_list.append(4)

        # The message context should have the modified list (shallow copy)
        self.assertEqual(updated_message.context["my_list"], [1, 2, 3, 4])


class TestIntegration(unittest.TestCase):
    """Integration tests for ExecutionContext and ContextPropagationMiddleware."""

    async def test_full_flow(self):
        """Test full context propagation flow."""
        # Setup
        ExecutionContext.clear()

        # Create mock message bus with actual behavior
        mock_bus = Mock()
        mock_bus.add_pre_publish_hook = Mock()

        # Track callback invocations
        callback_contexts = []

        async def mock_safe_callback(callback, message, filter):
            # Capture current context
            callback_contexts.append(ExecutionContext.get_current().copy())
            # Actually call the callback
            if asyncio.iscoroutinefunction(callback):
                await callback(message)
            else:
                callback(message)

        mock_bus._safe_callback_internal = mock_safe_callback

        # Create middleware
        middleware = ContextPropagationMiddleware(mock_bus)

        # Set execution context
        ExecutionContext.set(user_id="user123", request_id="req456")

        # Create and process message
        message = Message(topic="test.topic", payload={"data": "test"}, context=None)

        # Inject context (simulating pre-publish hook)
        message = middleware._inject_context(message)

        # Create callback
        callback_executed = False

        async def test_callback(msg):
            nonlocal callback_executed
            callback_executed = True
            # Verify context during callback
            ctx = ExecutionContext.get_current()
            assert ctx["user_id"] == "user123"
            assert ctx["request_id"] == "req456"

        # Execute callback through wrapper
        await middleware._restore_context_wrapper(test_callback, message, None)

        # Verify callback was executed
        self.assertTrue(callback_executed)

        # Verify context was captured
        self.assertEqual(len(callback_contexts), 1)
        self.assertEqual(callback_contexts[0]["user_id"], "user123")


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

    integration_suite = unittest.TestLoader().loadTestsFromTestCase(TestIntegration)
    for test in integration_suite:
        test_method = getattr(test, test._testMethodName)
        if asyncio.iscoroutinefunction(test_method):
            print(f"Running: {test._testMethodName}")
            run_async_test(test_method())
            print("✓ Passed")
