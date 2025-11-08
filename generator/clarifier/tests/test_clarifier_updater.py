
import asyncio
import json
import sqlite3
import unittest
from unittest.mock import patch, AsyncMock, MagicMock
from datetime import datetime
from clarifier_updater import (
    RequirementsUpdater, HistoryStore, detect_pii, redact_sensitive,
    UPDATE_CYCLES, UPDATE_ERRORS, UPDATE_CONFLICTS, REDACTION_EVENTS,
    SCHEMA_MIGRATIONS, INFERENCE_LATENCY, HISTORY_STORAGE_LATENCY, ALERT_SEND_EVENTS
)
from cryptography.fernet import Fernet
import zstandard as zstd

# Mock dependencies
patch_config = patch('clarifier_updater.get_config', return_value=MagicMock(
    SCHEMA_VERSION=2,
    HISTORY_COMPRESSION=True,
    ALERT_ENDPOINT='http://mock-alert:8080',
    CONFLICT_STRATEGY='auto_merge'
))
mock_config = patch_config.start()

patch_fernet = patch('clarifier_updater.get_fernet', return_value=MagicMock(
    encrypt=lambda x: b'encrypted_' + x,
    decrypt=lambda x: x[len(b'encrypted_'):],
))
mock_fernet = patch_fernet.start()

patch_logger = patch('clarifier_updater.get_logger', return_value=MagicMock())
mock_logger = patch_logger.start()

patch_log_action = patch('clarifier_updater.log_action', AsyncMock())
mock_log_action = patch_log_action.start()

patch_send_alert = patch('clarifier_updater.send_alert', AsyncMock())
mock_send_alert = patch_send_alert.start()

patch_tracer = patch('clarifier_updater.tracer', new=MagicMock())
mock_tracer = patch_tracer.start()

patch_aiohttp_session = patch('aiohttp.ClientSession')
mock_aiohttp_session = patch_aiohttp_session.start()

class TestRequirementsUpdaterRegulated(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Reset metrics
        UPDATE_CYCLES.clear()
        UPDATE_ERRORS.clear()
        UPDATE_CONFLICTS.clear()
        REDACTION_EVENTS.clear()
        SCHEMA_MIGRATIONS.clear()
        INFERENCE_LATENCY.clear()
        HISTORY_STORAGE_LATENCY.clear()
        ALERT_SEND_EVENTS.clear()

        # Reset mocks
        mock_log_action.reset_mock()
        mock_send_alert.reset_mock()
        mock_logger.reset_mock()
        mock_tracer.reset_mock()

        # Initialize HistoryStore with in-memory SQLite
        self.db_path = ':memory:'
        self.history_store = HistoryStore(self.db_path, mock_fernet.return_value)
        await self.history_store._init_db()

        # Mock conflict resolver
        self.mock_conflict_resolver = MagicMock()
        self.mock_conflict_resolver.resolve = lambda conflicts, reqs, clarifs, feedback: {
            k: v for k, v in reqs.items() if k != 'features' or 'contradictory_feature' not in v
        }

        # Initialize RequirementsUpdater
        with patch('clarifier_updater.DefaultConflictResolver', return_value=self.mock_conflict_resolver):
            self.updater = RequirementsUpdater()
            self.updater.history_store = self.history_store

        self.requirements = {
            "features": ["test_feature", "contradictory_feature"],
            "schema_version": 1
        }
        self.ambiguities = ["test_secret", "email_pii", "contradictory_feature"]
        self.answers = ["api_key=SECRET123", "user@example.com", "no"]
        self.user = "test_user"
        self.reason = "clarification"
        self.correlation_id = "test-correlation-id"

    async def asyncTearDown(self):
        await self.history_store.close()
        patch_config.stop()
        patch_fernet.stop()
        patch_logger.stop()
        patch_tracer.stop()
        patch_log_action.stop()
        patch_send_alert.stop()
        patch_aiohttp_session.stop()

    async def test_pii_redaction(self):
        """Test PII detection and redaction in clarifications."""
        with patch.object(self.updater, '_infer_updates', AsyncMock(return_value={
            "inferred_features": [], "inferred_constraints": []
        })):
            result = await self.updater.update(
                self.requirements, self.ambiguities, self.answers, self.user, self.reason, correlation_id=self.correlation_id
            )

        self.assertIn("clarifications", result)
        self.assertEqual(result["clarifications"]["test_secret"], "[REDACTED_API_KEY]")
        self.assertEqual(result["clarifications"]["email_pii"], "[REDACTED_EMAIL]")
        self.assertEqual(REDACTION_EVENTS.labels(pattern_type="api_key")._value, 1)
        self.assertEqual(REDACTION_EVENTS.labels(pattern_type="email")._value, 1)
        mock_log_action.assert_any_call("requirements_updated", category="update_workflow", version=Any, conflicts_detected=1, final_status="success")

    async def test_encrypted_history_storage(self):
        """Test that history entries are encrypted and compressed."""
        with patch.object(self.updater, '_infer_updates', AsyncMock(return_value={
            "inferred_features": [], "inferred_constraints": []
        })):
            result = await self.updater.update(
                self.requirements, self.ambiguities, self.answers, self.user, self.reason
            )

        history = await self.history_store.query(limit=1)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["clarifications"]["test_secret"], "[REDACTED_API_KEY]")
        self.assertGreater(HISTORY_STORAGE_LATENCY.labels(operation="store")._count, 0)
        mock_fernet.return_value.encrypt.assert_called()
        mock_log_action.assert_any_call("history_stored", category="history", entry_id=Any, version=1)

    async def test_schema_migration_v1_to_v2(self):
        """Test schema migration from v1 to v2."""
        with patch.object(self.updater, '_infer_updates', AsyncMock(return_value={
            "inferred_features": [], "inferred_constraints": []
        })):
            result = await self.updater.update(
                self.requirements, self.ambiguities, self.answers, self.user, self.reason
            )

        self.assertEqual(result["schema_version"], 2)
        self.assertIn("inferred_features", result)
        self.assertIn("inferred_constraints", result)
        self.assertIn("desired_doc_formats", result)
        self.assertEqual(SCHEMA_MIGRATIONS.labels(from_version="1", to_version="2")._value, 1)
        mock_tracer.start_as_current_span.assert_any_call("migrate_schema", attributes=Any)

    async def test_schema_validation_failure(self):
        """Test handling of invalid schema."""
        invalid_requirements = {"invalid_field": "test", "schema_version": 2}
        with patch.object(self.updater, '_infer_updates', AsyncMock(return_value={
            "inferred_features": [], "inferred_constraints": []
        })):
            with self.assertRaises(ValueError):
                await self.updater.update(
                    invalid_requirements, self.ambiguities, self.answers, self.user, self.reason
                )

        self.assertEqual(UPDATE_ERRORS.labels("schema", "validation_failed")._value, 1)
        mock_send_alert.assert_awaited_with(Any, severity="high")

    async def test_conflict_resolution(self):
        """Test conflict detection and resolution."""
        with patch.object(self.updater, '_infer_updates', AsyncMock(return_value={
            "inferred_features": [], "inferred_constraints": []
        })):
            result = await self.updater.update(
                self.requirements, self.ambiguities, self.answers, self.user, self.reason
            )

        self.assertNotIn("contradictory_feature", result["features"])
        self.assertEqual(UPDATE_CONFLICTS.labels(conflict_type="feature_contradiction")._value, 1)
        mock_tracer.start_as_current_span.assert_any_call("detect_conflicts")
        mock_log_action.assert_any_call("requirements_updated", category="update_workflow", version=Any, conflicts_detected=1, final_status="success")

    async def test_hash_chain_integrity(self):
        """Test versioning and hash chain integrity."""
        with patch.object(self.updater, '_infer_updates', AsyncMock(return_value={
            "inferred_features": [], "inferred_constraints": []
        })):
            result = await self.updater.update(
                self.requirements, self.ambiguities, self.answers, self.user, self.reason
            )

        self.assertTrue(self.updater._verify_hash_chain(result))
        self.assertIn("version_hash", result)
        self.assertIn("prev_hash", result)
        mock_log_action.assert_any_call("requirements_versioned", category="versioning", version=1, current_hash=Any, previous_hash=Any)

    async def test_corrupted_history_entry(self):
        """Test handling of corrupted history entries."""
        with patch.object(self.updater, '_infer_updates', AsyncMock(return_value={
            "inferred_features": [], "inferred_constraints": []
        })):
            await self.updater.update(
                self.requirements, self.ambiguities, self.answers, self.user, self.reason
            )

        # Corrupt an entry in the database
        await asyncio.to_thread(
            self.history_store.conn.execute,
            "UPDATE history SET encrypted_data = ? WHERE version = ?",
            (b'corrupted_data', 1)
        )
        await asyncio.to_thread(self.history_store.conn.commit)

        history = await self.history_store.query(limit=1)
        self.assertEqual(len(history), 0)  # Corrupted entry should be skipped
        self.assertEqual(UPDATE_ERRORS.labels("history", "decrypt_failed")._value, 1)
        mock_send_alert.assert_awaited_with(Any, severity="medium")

    async def test_concurrent_updates(self):
        """Test concurrent updates for metric and history accuracy."""
        with patch.object(self.updater, '_infer_updates', AsyncMock(return_value={
            "inferred_features": [], "inferred_constraints": []
        })):
            tasks = [
                self.updater.update(
                    self.requirements, self.ambiguities, self.answers, self.user, self.reason
                )
                for _ in range(3)
            ]
            results = await asyncio.gather(*tasks)

        self.assertEqual(len(results), 3)
        self.assertEqual(UPDATE_CYCLES._value, 3)
        history = await self.history_store.query(limit=3)
        self.assertEqual(len(history), 3)
        self.assertEqual(HISTORY_STORAGE_LATENCY.labels(operation="store")._count, 3)

    async def test_empty_ambiguities(self):
        """Test handling of empty ambiguities and answers."""
        with patch.object(self.updater, '_infer_updates', AsyncMock(return_value={
            "inferred_features": [], "inferred_constraints": []
        })):
            result = await self.updater.update(
                self.requirements, [], [], self.user, self.reason
            )

        self.assertEqual(result["clarifications"], {})
        self.assertEqual(UPDATE_CONFLICTS.labels(conflict_type="none")._value, 0)
        mock_log_action.assert_any_call("requirements_updated", category="update_workflow", version=Any, conflicts_detected=0, final_status="success")

    async def test_self_test_compliance(self):
        """Test self_test for regulatory compliance checks."""
        with patch.object(self.updater, '_infer_updates', AsyncMock(return_value={
            "inferred_features": ["inferred_self_test_feature"], "inferred_constraints": []
        })):
            result = self.updater.self_test()

        self.assertTrue(result)
        self.assertEqual(SELF_TEST_PASS._value, 1)
        history = await self.history_store.query(limit=1)
        self.assertEqual(len(history), 1)
        self.assertIn("[REDACTED_API_KEY]", history[0]["clarifications"]["test_secret"])
        self.assertIn("[REDACTED_EMAIL]", history[0]["clarifications"]["email_pii"])
        self.assertNotIn("contradictory_feature", history[0]["features"])

    async def test_data_residency_compliance(self):
        """Test that sensitive data is encrypted and not exposed."""
        with patch.object(self.updater, '_infer_updates', AsyncMock(return_value={
            "inferred_features": [], "inferred_constraints": []
        })):
            result = await self.updater.update(
                self.requirements, ["sensitive_data"], ["ssn=123-45-6789"], self.user, self.reason
            )

        self.assertEqual(result["clarifications"]["sensitive_data"], "[REDACTED_SSN]")
        history = await self.history_store.query(limit=1)
        self.assertEqual(history[0]["clarifications"]["sensitive_data"], "[REDACTED_SSN]")
        mock_fernet.return_value.encrypt.assert_called()
        log_calls = mock_log_action.call_args_list
        for call in log_calls:
            args, _ = call
            self.assertNotIn("123-45-6789", json.dumps(args))

    async def test_alert_on_critical_failure(self):
        """Test alerting on critical failures."""
        with patch.object(self.updater, '_infer_updates', AsyncMock(side_effect=Exception("Inference failure"))):
            with self.assertRaises(Exception):
                await self.updater.update(
                    self.requirements, self.ambiguities, self.answers, self.user, self.reason
                )

        self.assertEqual(UPDATE_ERRORS.labels("update_workflow", "Exception")._value, 1)
        mock_send_alert.assert_awaited_with(Any, severity="high")
        mock_log_action.assert_any_call("requirements_update_failed", category="update_workflow", error=Any, user=self.user, reason=self.reason, final_status="failure")

if __name__ == '__main__':
    unittest.main()
