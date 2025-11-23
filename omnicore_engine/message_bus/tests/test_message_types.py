# test_message_types.py

import json
import sys
import time
import unittest
import uuid
from dataclasses import asdict
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from message_bus.message_types import Message, MessageSchema
from pydantic import ValidationError


class TestMessage(unittest.TestCase):
    """Test suite for Message dataclass."""

    def test_basic_initialization(self):
        """Test basic Message initialization with required fields."""
        msg = Message(topic="test.topic", payload={"data": "test"})

        self.assertEqual(msg.topic, "test.topic")
        self.assertEqual(msg.payload, {"data": "test"})
        self.assertEqual(msg.priority, 0)  # Default
        self.assertFalse(msg.encrypted)  # Default
        self.assertIsNone(msg.idempotency_key)  # Default
        self.assertEqual(msg.context, {})  # Default
        self.assertIsNone(msg.processing_start)  # Default

    def test_initialization_with_all_fields(self):
        """Test Message initialization with all fields specified."""
        test_time = time.time()
        test_uuid = str(uuid.uuid4())

        msg = Message(
            topic="test.topic",
            payload={"data": "test"},
            priority=5,
            timestamp=test_time,
            trace_id=test_uuid,
            encrypted=True,
            idempotency_key="idem_123",
            context={"user_id": "user_456"},
            processing_start=123456789,
        )

        self.assertEqual(msg.topic, "test.topic")
        self.assertEqual(msg.payload, {"data": "test"})
        self.assertEqual(msg.priority, 5)
        self.assertEqual(msg.timestamp, test_time)
        self.assertEqual(msg.trace_id, test_uuid)
        self.assertTrue(msg.encrypted)
        self.assertEqual(msg.idempotency_key, "idem_123")
        self.assertEqual(msg.context, {"user_id": "user_456"})
        self.assertEqual(msg.processing_start, 123456789)

    def test_default_timestamp(self):
        """Test that timestamp is auto-generated if not provided."""
        before = time.time()
        msg = Message(topic="test", payload="data")
        after = time.time()

        self.assertGreaterEqual(msg.timestamp, before)
        self.assertLessEqual(msg.timestamp, after)

    def test_default_trace_id(self):
        """Test that trace_id is auto-generated as UUID if not provided."""
        msg = Message(topic="test", payload="data")

        # Should be a valid UUID string
        try:
            uuid.UUID(msg.trace_id)
        except ValueError:
            self.fail(f"trace_id is not a valid UUID: {msg.trace_id}")

    def test_default_context(self):
        """Test that context defaults to empty dict."""
        msg = Message(topic="test", payload="data")

        self.assertEqual(msg.context, {})
        self.assertIsInstance(msg.context, dict)

    def test_trace_id_uniqueness(self):
        """Test that auto-generated trace_ids are unique."""
        messages = [Message(topic="test", payload=f"data_{i}") for i in range(100)]
        trace_ids = [msg.trace_id for msg in messages]

        # All trace_ids should be unique
        self.assertEqual(len(trace_ids), len(set(trace_ids)))

    def test_payload_types(self):
        """Test Message with different payload types."""
        test_cases = [
            "string payload",
            123,
            3.14,
            True,
            None,
            [1, 2, 3],
            {"nested": {"data": "value"}},
            {"mixed": [1, "two", {"three": 3}]},
        ]

        for payload in test_cases:
            msg = Message(topic="test", payload=payload)
            self.assertEqual(msg.payload, payload)

    def test_dataclass_methods(self):
        """Test dataclass methods like equality and representation."""
        msg1 = Message(topic="test", payload="data", trace_id="fixed_id", timestamp=100.0)

        msg2 = Message(topic="test", payload="data", trace_id="fixed_id", timestamp=100.0)

        msg3 = Message(topic="different", payload="data", trace_id="fixed_id", timestamp=100.0)

        # Test equality
        self.assertEqual(msg1, msg2)
        self.assertNotEqual(msg1, msg3)

        # Test representation
        repr_str = repr(msg1)
        self.assertIn("Message", repr_str)
        self.assertIn("topic='test'", repr_str)

    def test_asdict_conversion(self):
        """Test converting Message to dictionary."""
        msg = Message(
            topic="test.topic",
            payload={"data": "test"},
            priority=5,
            trace_id="trace_123",
            idempotency_key="idem_456",
        )

        msg_dict = asdict(msg)

        self.assertEqual(msg_dict["topic"], "test.topic")
        self.assertEqual(msg_dict["payload"], {"data": "test"})
        self.assertEqual(msg_dict["priority"], 5)
        self.assertEqual(msg_dict["trace_id"], "trace_123")
        self.assertEqual(msg_dict["idempotency_key"], "idem_456")
        self.assertIn("timestamp", msg_dict)
        self.assertIn("context", msg_dict)

    def test_mutable_context(self):
        """Test that context is mutable."""
        msg = Message(topic="test", payload="data")

        # Should be able to modify context
        msg.context["key1"] = "value1"
        msg.context["key2"] = "value2"

        self.assertEqual(msg.context, {"key1": "value1", "key2": "value2"})

    def test_field_defaults(self):
        """Test that field defaults work correctly."""
        # Create multiple messages to ensure defaults are independent
        msg1 = Message(topic="test1", payload="data1")
        msg2 = Message(topic="test2", payload="data2")

        # Modify context of first message
        msg1.context["key"] = "value"

        # Second message context should not be affected
        self.assertEqual(msg2.context, {})

    def test_priority_values(self):
        """Test different priority values."""
        priorities = [-1, 0, 1, 5, 10, 100, 999]

        for priority in priorities:
            msg = Message(topic="test", payload="data", priority=priority)
            self.assertEqual(msg.priority, priority)

    def test_encrypted_flag(self):
        """Test encrypted flag behavior."""
        msg_encrypted = Message(topic="test", payload="data", encrypted=True)
        msg_plain = Message(topic="test", payload="data", encrypted=False)
        msg_default = Message(topic="test", payload="data")

        self.assertTrue(msg_encrypted.encrypted)
        self.assertFalse(msg_plain.encrypted)
        self.assertFalse(msg_default.encrypted)  # Default is False


class TestMessageSchema(unittest.TestCase):
    """Test suite for MessageSchema Pydantic model."""

    def test_basic_validation(self):
        """Test basic MessageSchema validation."""
        schema = MessageSchema(topic="test.topic", payload={"data": "test"})

        self.assertEqual(schema.topic, "test.topic")
        self.assertEqual(schema.payload, {"data": "test"})
        self.assertEqual(schema.priority, 0)  # Default
        self.assertIsNone(schema.trace_id)  # Default
        self.assertIsNone(schema.idempotency_key)  # Default
        self.assertEqual(schema.context, {})  # Default

    def test_validation_with_all_fields(self):
        """Test MessageSchema with all fields."""
        schema = MessageSchema(
            topic="test.topic",
            payload={"data": "test"},
            priority=5,
            trace_id="trace_123",
            idempotency_key="idem_456",
            context={"user_id": "user_789"},
        )

        self.assertEqual(schema.topic, "test.topic")
        self.assertEqual(schema.payload, {"data": "test"})
        self.assertEqual(schema.priority, 5)
        self.assertEqual(schema.trace_id, "trace_123")
        self.assertEqual(schema.idempotency_key, "idem_456")
        self.assertEqual(schema.context, {"user_id": "user_789"})

    def test_missing_required_fields(self):
        """Test validation errors for missing required fields."""
        # Missing topic
        with self.assertRaises(ValidationError) as context:
            MessageSchema(payload={"data": "test"})

        errors = context.exception.errors()
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["loc"], ("topic",))
        self.assertEqual(errors[0]["type"], "missing")

        # Missing payload
        with self.assertRaises(ValidationError) as context:
            MessageSchema(topic="test.topic")

        errors = context.exception.errors()
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["loc"], ("payload",))

    def test_type_validation(self):
        """Test type validation for fields."""
        # Invalid priority type
        with self.assertRaises(ValidationError):
            MessageSchema(topic="test", payload={"data": "test"}, priority="high")  # Should be int

        # Invalid context type
        with self.assertRaises(ValidationError):
            MessageSchema(
                topic="test",
                payload={"data": "test"},
                context="invalid",  # Should be dict
            )

    def test_json_serialization(self):
        """Test JSON serialization of MessageSchema."""
        schema = MessageSchema(
            topic="test.topic",
            payload={"data": "test", "number": 123},
            priority=5,
            trace_id="trace_123",
        )

        # Convert to JSON
        json_str = schema.model_dump_json()

        # Parse back
        parsed = json.loads(json_str)

        self.assertEqual(parsed["topic"], "test.topic")
        self.assertEqual(parsed["payload"], {"data": "test", "number": 123})
        self.assertEqual(parsed["priority"], 5)
        self.assertEqual(parsed["trace_id"], "trace_123")

    def test_dict_conversion(self):
        """Test converting MessageSchema to dict."""
        schema = MessageSchema(topic="test.topic", payload={"data": "test"}, priority=5)

        schema_dict = schema.model_dump()

        self.assertEqual(schema_dict["topic"], "test.topic")
        self.assertEqual(schema_dict["payload"], {"data": "test"})
        self.assertEqual(schema_dict["priority"], 5)
        self.assertIsNone(schema_dict["trace_id"])
        self.assertIsNone(schema_dict["idempotency_key"])
        self.assertEqual(schema_dict["context"], {})

    def test_from_dict_creation(self):
        """Test creating MessageSchema from dictionary."""
        data = {
            "topic": "test.topic",
            "payload": {"data": "test"},
            "priority": 5,
            "trace_id": "trace_123",
            "context": {"key": "value"},
        }

        schema = MessageSchema(**data)

        self.assertEqual(schema.topic, "test.topic")
        self.assertEqual(schema.payload, {"data": "test"})
        self.assertEqual(schema.priority, 5)
        self.assertEqual(schema.trace_id, "trace_123")
        self.assertEqual(schema.context, {"key": "value"})

    def test_schema_validation_edge_cases(self):
        """Test edge cases in schema validation."""
        # Empty string topic (should be allowed)
        schema = MessageSchema(topic="", payload={})
        self.assertEqual(schema.topic, "")

        # Empty payload dict (should be allowed)
        schema = MessageSchema(topic="test", payload={})
        self.assertEqual(schema.payload, {})

        # Negative priority (should be allowed)
        schema = MessageSchema(topic="test", payload={}, priority=-10)
        self.assertEqual(schema.priority, -10)

    def test_optional_fields_none(self):
        """Test that optional fields can be explicitly set to None."""
        schema = MessageSchema(topic="test", payload={}, trace_id=None, idempotency_key=None)

        self.assertIsNone(schema.trace_id)
        self.assertIsNone(schema.idempotency_key)

    def test_context_default_factory(self):
        """Test that context uses default_factory correctly."""
        # Create two schemas without specifying context
        schema1 = MessageSchema(topic="test1", payload={})
        schema2 = MessageSchema(topic="test2", payload={})

        # Modify context of first schema
        schema1.context["key"] = "value"

        # Second schema context should not be affected
        self.assertEqual(schema2.context, {})


class TestMessageSchemaCompatibility(unittest.TestCase):
    """Test compatibility between Message and MessageSchema."""

    def test_message_to_schema(self):
        """Test converting Message to MessageSchema."""
        msg = Message(
            topic="test.topic",
            payload={"data": "test"},
            priority=5,
            trace_id="trace_123",
            idempotency_key="idem_456",
            context={"user": "test_user"},
        )

        # Convert to dict and create schema
        msg_dict = asdict(msg)
        # Remove fields not in schema
        msg_dict.pop("timestamp", None)
        msg_dict.pop("encrypted", None)
        msg_dict.pop("processing_start", None)

        schema = MessageSchema(**msg_dict)

        self.assertEqual(schema.topic, msg.topic)
        self.assertEqual(schema.payload, msg.payload)
        self.assertEqual(schema.priority, msg.priority)
        self.assertEqual(schema.trace_id, msg.trace_id)
        self.assertEqual(schema.idempotency_key, msg.idempotency_key)
        self.assertEqual(schema.context, msg.context)

    def test_schema_to_message(self):
        """Test converting MessageSchema to Message."""
        schema = MessageSchema(
            topic="test.topic",
            payload={"data": "test"},
            priority=5,
            trace_id="trace_123",
            idempotency_key="idem_456",
            context={"user": "test_user"},
        )

        # Convert to Message
        msg = Message(
            topic=schema.topic,
            payload=schema.payload,
            priority=schema.priority,
            trace_id=schema.trace_id or str(uuid.uuid4()),
            idempotency_key=schema.idempotency_key,
            context=schema.context,
        )

        self.assertEqual(msg.topic, schema.topic)
        self.assertEqual(msg.payload, schema.payload)
        self.assertEqual(msg.priority, schema.priority)
        self.assertEqual(msg.trace_id, schema.trace_id)
        self.assertEqual(msg.idempotency_key, schema.idempotency_key)
        self.assertEqual(msg.context, schema.context)


class TestMessageUsagePatterns(unittest.TestCase):
    """Test common usage patterns for Message and MessageSchema."""

    def test_priority_queue_compatibility(self):
        """Test that Messages work with priority queues."""
        import heapq

        # Create messages with different priorities
        msg1 = Message(topic="test", payload="1", priority=5)
        msg2 = Message(topic="test", payload="2", priority=1)
        msg3 = Message(topic="test", payload="3", priority=10)

        # Use in priority queue (lower number = higher priority)
        queue = []
        heapq.heappush(queue, (msg1.priority, msg1.trace_id, msg1))
        heapq.heappush(queue, (msg2.priority, msg2.trace_id, msg2))
        heapq.heappush(queue, (msg3.priority, msg3.trace_id, msg3))

        # Pop in priority order
        _, _, first = heapq.heappop(queue)
        self.assertEqual(first.payload, "2")  # Priority 1

        _, _, second = heapq.heappop(queue)
        self.assertEqual(second.payload, "1")  # Priority 5

        _, _, third = heapq.heappop(queue)
        self.assertEqual(third.payload, "3")  # Priority 10

    def test_json_roundtrip(self):
        """Test JSON serialization roundtrip."""
        original = Message(
            topic="test.topic",
            payload={"data": "test", "nested": {"value": 123}},
            priority=5,
            trace_id="trace_123",
            idempotency_key="idem_456",
            context={"user_id": "user_789"},
        )

        # Convert to JSON-serializable dict
        msg_dict = asdict(original)
        json_str = json.dumps(msg_dict)

        # Parse back
        parsed_dict = json.loads(json_str)
        reconstructed = Message(**parsed_dict)

        self.assertEqual(reconstructed.topic, original.topic)
        self.assertEqual(reconstructed.payload, original.payload)
        self.assertEqual(reconstructed.priority, original.priority)
        self.assertEqual(reconstructed.trace_id, original.trace_id)


if __name__ == "__main__":
    unittest.main(verbosity=2)
