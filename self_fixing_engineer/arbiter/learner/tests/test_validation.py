# test_validation.py

import pytest
import asyncio
import json
import os
import tempfile
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from jsonschema.exceptions import SchemaError
from tenacity import RetryError

from arbiter.learner.validation import (
    DomainNotFoundError,
    validate_data,
    register_validation_hook,
    reload_schemas,
    validation_failure_total,
    schema_reload_total,
    SCHEMA_CACHE_TTL_SECONDS,
)


class TestValidateData:
    """Test suite for validate_data function."""

    @pytest.fixture
    def mock_learner(self):
        """Create a mock learner with validation schemas and hooks."""
        learner = Mock()
        learner.validation_schemas = {}
        learner.validation_hooks = {}
        return learner

    @pytest.fixture
    def sample_schema(self):
        """Create a sample JSON schema."""
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer", "minimum": 0},
            },
            "required": ["name"],
        }

    @pytest.mark.asyncio
    async def test_validate_with_schema_success(self, mock_learner, sample_schema):
        """Test successful validation against JSON schema."""
        mock_learner.validation_schemas = {
            "TestDomain": {"schema": sample_schema, "version": "1.0"}
        }

        with patch("arbiter.learner.validation.time") as mock_time:
            mock_time.perf_counter.side_effect = [1.0, 2.0]

            result = await validate_data(
                mock_learner, "TestDomain", {"name": "John", "age": 30}
            )

            assert result["is_valid"] is True
            assert result["reason_code"] == "success"
            assert "All validations passed" in result["reason"]

    @pytest.mark.asyncio
    async def test_validate_with_schema_failure(self, mock_learner, sample_schema):
        """Test failed validation against JSON schema."""
        mock_learner.validation_schemas = {
            "TestDomain": {"schema": sample_schema, "version": "1.0"}
        }

        with patch("arbiter.learner.validation.time") as mock_time:
            mock_time.perf_counter.side_effect = [1.0, 2.0]

            with patch.object(validation_failure_total, "labels") as mock_metric:
                mock_labels = MagicMock()
                mock_metric.return_value = mock_labels

                # Missing required field "name"
                result = await validate_data(mock_learner, "TestDomain", {"age": 30})

                assert result["is_valid"] is False
                assert result["reason_code"] == "schema_validation_failed"
                assert "Schema validation failed" in result["reason"]

                mock_metric.assert_called_with(
                    domain="TestDomain", reason_code="schema_validation_failed"
                )
                mock_labels.inc.assert_called_once()

    @pytest.mark.asyncio
    async def test_validate_with_sync_hook(self, mock_learner):
        """Test validation with synchronous custom hook."""

        def custom_validator(value):
            return "valid" in value

        mock_learner.validation_hooks = {"TestDomain": custom_validator}

        with patch("arbiter.learner.validation.time") as mock_time:
            # Provide enough mock values for both calls (2 per call)
            mock_time.perf_counter.side_effect = [1.0, 2.0, 3.0, 4.0]

            # Valid case
            result = await validate_data(mock_learner, "TestDomain", {"valid": True})
            assert result["is_valid"] is True

            # Invalid case
            result = await validate_data(mock_learner, "TestDomain", {"invalid": True})
            assert result["is_valid"] is False
            assert result["reason_code"] == "custom_validation_failed"

    @pytest.mark.asyncio
    async def test_validate_with_async_hook(self, mock_learner):
        """Test validation with asynchronous custom hook."""

        async def async_validator(value):
            await asyncio.sleep(0.01)  # Simulate async operation
            return "valid" in value

        mock_learner.validation_hooks = {"TestDomain": async_validator}

        with patch("arbiter.learner.validation.time") as mock_time:
            mock_time.perf_counter.side_effect = [1.0, 2.0]

            result = await validate_data(mock_learner, "TestDomain", {"valid": True})

            assert result["is_valid"] is True

    @pytest.mark.asyncio
    async def test_validate_with_both_schema_and_hook(
        self, mock_learner, sample_schema
    ):
        """Test validation with both schema and hook."""

        def custom_validator(value):
            return value.get("age", 0) >= 18  # Additional age check

        mock_learner.validation_schemas = {
            "TestDomain": {"schema": sample_schema, "version": "1.0"}
        }
        mock_learner.validation_hooks = {"TestDomain": custom_validator}

        with patch("arbiter.learner.validation.time") as mock_time:
            mock_time.perf_counter.side_effect = [1.0, 2.0, 3.0, 4.0]

            # Passes schema but fails hook (age < 18)
            result = await validate_data(
                mock_learner, "TestDomain", {"name": "John", "age": 15}
            )
            assert result["is_valid"] is False
            assert result["reason_code"] == "custom_validation_failed"

            # Passes both
            result = await validate_data(
                mock_learner, "TestDomain", {"name": "Jane", "age": 25}
            )
            assert result["is_valid"] is True

    @pytest.mark.asyncio
    async def test_validate_invalid_domain(self, mock_learner):
        """Test validation with invalid domain."""
        with pytest.raises(ValueError, match="Invalid domain"):
            await validate_data(mock_learner, "", {"data": "test"})

        with pytest.raises(ValueError, match="Invalid domain"):
            await validate_data(mock_learner, None, {"data": "test"})

    @pytest.mark.asyncio
    async def test_validate_null_value(self, mock_learner):
        """Test validation with null value."""
        with pytest.raises(ValueError, match="Value cannot be None"):
            await validate_data(mock_learner, "TestDomain", None)

    @pytest.mark.asyncio
    async def test_validate_domain_not_found(self, mock_learner):
        """Test validation when domain has no schema or hook."""
        with patch("arbiter.learner.validation.time") as mock_time:
            mock_time.perf_counter.side_effect = [1.0, 2.0]

            with pytest.raises(
                DomainNotFoundError, match="No validation schema or hook found"
            ):
                await validate_data(mock_learner, "UnknownDomain", {"data": "test"})

    @pytest.mark.asyncio
    async def test_validate_invalid_schema_error(self, mock_learner):
        """Test handling of invalid schema structure."""
        mock_learner.validation_schemas = {
            "TestDomain": {
                "schema": {"type": "invalid_type"},  # Invalid schema
                "version": "1.0",
            }
        }

        with patch("arbiter.learner.validation.time") as mock_time:
            mock_time.perf_counter.side_effect = [1.0, 2.0]

            with patch(
                "jsonschema.validate", side_effect=SchemaError("Invalid schema")
            ):
                result = await validate_data(
                    mock_learner, "TestDomain", {"data": "test"}
                )

                assert result["is_valid"] is False
                assert result["reason_code"] == "invalid_schema"

    @pytest.mark.asyncio
    async def test_validate_hook_exception(self, mock_learner):
        """Test handling of hook exceptions."""

        def failing_hook(value):
            raise Exception("Hook failed")

        mock_learner.validation_hooks = {"TestDomain": failing_hook}

        with patch("arbiter.learner.validation.time") as mock_time:
            mock_time.perf_counter.side_effect = [1.0, 2.0]

            result = await validate_data(mock_learner, "TestDomain", {"data": "test"})

            assert result["is_valid"] is False
            assert result["reason_code"] == "custom_validation_error"
            assert "Hook failed" in result["reason"]


class TestRegisterValidationHook:
    """Test suite for register_validation_hook function."""

    @pytest.fixture
    def mock_learner(self):
        """Create a mock learner."""
        learner = Mock()
        learner.validation_hooks = {}
        learner.audit_logger = AsyncMock()
        learner.audit_logger.add_entry = AsyncMock()
        return learner

    def test_register_sync_hook(self, mock_learner):
        """Test registering a synchronous validation hook."""

        def sync_validator(value):
            return True

        # Note: The function has an await statement that needs fixing in original
        register_validation_hook(mock_learner, "TestDomain", sync_validator)

        assert "TestDomain" in mock_learner.validation_hooks
        assert mock_learner.validation_hooks["TestDomain"] == sync_validator

    def test_register_async_hook(self, mock_learner):
        """Test registering an asynchronous validation hook."""

        async def async_validator(value):
            return True

        register_validation_hook(mock_learner, "TestDomain", async_validator)

        assert "TestDomain" in mock_learner.validation_hooks
        assert mock_learner.validation_hooks["TestDomain"] == async_validator

    def test_register_non_callable(self, mock_learner):
        """Test registering a non-callable raises error."""
        with pytest.raises(TypeError, match="Validation hook must be a callable"):
            register_validation_hook(mock_learner, "TestDomain", "not_callable")

    def test_register_sync_hook_wrong_signature(self, mock_learner):
        """Test registering sync hook with wrong signature."""

        def wrong_signature(value, extra):
            return True

        with pytest.raises(TypeError, match="must accept exactly one argument"):
            register_validation_hook(mock_learner, "TestDomain", wrong_signature)

    def test_register_async_hook_wrong_signature(self, mock_learner):
        """Test registering async hook with wrong signature."""

        async def wrong_signature(value, extra):
            return True

        with pytest.raises(TypeError, match="must accept exactly one argument"):
            register_validation_hook(mock_learner, "TestDomain", wrong_signature)

    def test_register_lambda_hook(self, mock_learner):
        """Test registering a lambda function as hook."""
        lambda_validator = lambda v: v is not None

        register_validation_hook(mock_learner, "TestDomain", lambda_validator)

        assert "TestDomain" in mock_learner.validation_hooks
        assert mock_learner.validation_hooks["TestDomain"]({"test": "data"}) is True


class TestReloadSchemas:
    """Test suite for reload_schemas function."""

    @pytest.fixture
    def mock_learner(self):
        """Create a mock learner."""
        learner = Mock()
        learner.validation_schemas = {}
        learner.event_hooks = {"on_schema_reload": []}
        learner.redis = AsyncMock()
        learner.redis.setex = AsyncMock()
        learner.audit_logger = AsyncMock()
        learner.audit_logger.add_entry = AsyncMock()
        return learner

    @pytest.fixture
    def temp_schema_dir(self):
        """Create a temporary directory with test schemas."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create test schema files
            schema1 = {
                "type": "object",
                "properties": {"field1": {"type": "string"}},
                "version": "1.0",
            }
            schema2 = {"type": "array", "items": {"type": "number"}, "version": "2.0"}

            with open(os.path.join(tmpdir, "Domain1.json"), "w") as f:
                json.dump(schema1, f)

            with open(os.path.join(tmpdir, "Domain2.json"), "w") as f:
                json.dump(schema2, f)

            yield tmpdir

    @pytest.mark.asyncio
    async def test_reload_schemas_success(self, mock_learner, temp_schema_dir):
        """Test successful schema reload from directory."""
        with patch("arbiter.learner.validation.time") as mock_time:
            mock_time.perf_counter.side_effect = [1.0, 2.0]

            await reload_schemas(mock_learner, temp_schema_dir)

            assert len(mock_learner.validation_schemas) == 2
            assert "Domain1" in mock_learner.validation_schemas
            assert "Domain2" in mock_learner.validation_schemas

            # Verify schema content
            assert mock_learner.validation_schemas["Domain1"]["version"] == "1.0"
            assert mock_learner.validation_schemas["Domain2"]["version"] == "2.0"

            # Verify Redis caching
            mock_learner.redis.setex.assert_called_once()
            call_args = mock_learner.redis.setex.call_args
            assert call_args[0][0] == "learner_validation_schemas_cache"
            assert call_args[0][1] == SCHEMA_CACHE_TTL_SECONDS

    @pytest.mark.asyncio
    async def test_reload_schemas_invalid_json(self, mock_learner):
        """Test handling of invalid JSON schema files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create invalid JSON file
            with open(os.path.join(tmpdir, "Invalid.json"), "w") as f:
                f.write("invalid json {")

            with patch("arbiter.learner.validation.time") as mock_time:
                mock_time.perf_counter.side_effect = [1.0, 2.0]

                await reload_schemas(mock_learner, tmpdir)

                # Should skip invalid file
                assert "Invalid" not in mock_learner.validation_schemas

    @pytest.mark.asyncio
    async def test_reload_schemas_invalid_schema_structure(self, mock_learner):
        """Test handling of structurally invalid schemas."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create schema with invalid structure
            invalid_schema = {"type": "invalid_type"}

            with open(os.path.join(tmpdir, "BadSchema.json"), "w") as f:
                json.dump(invalid_schema, f)

            with patch("arbiter.learner.validation.time") as mock_time:
                mock_time.perf_counter.side_effect = [1.0, 2.0]

                with patch(
                    "jsonschema.Draft7Validator.check_schema",
                    side_effect=SchemaError("Invalid"),
                ):
                    await reload_schemas(mock_learner, tmpdir)

                    # Should skip invalid schema
                    assert "BadSchema" not in mock_learner.validation_schemas

    @pytest.mark.asyncio
    async def test_reload_schemas_directory_not_found(self, mock_learner):
        """Test handling of non-existent directory."""
        with patch("arbiter.learner.validation.time") as mock_time:
            # Provide enough values for retry attempts
            mock_time.perf_counter.return_value = 1.0

            with patch.object(schema_reload_total, "labels") as mock_metric:
                mock_labels = MagicMock()
                mock_metric.return_value = mock_labels

                # Disable permission check so it reaches the exists check
                with patch(
                    "arbiter.learner.validation.SCHEMA_DIR_PERMISSION_CHECK", False
                ):
                    with patch("os.path.exists", return_value=False):
                        # When directory doesn't exist, function should return normally
                        await reload_schemas(mock_learner, "/non/existent/directory")

                        mock_metric.assert_called_with(status="not_found")
                        mock_labels.inc.assert_called_once()

    @pytest.mark.asyncio
    async def test_reload_schemas_permission_denied(self, mock_learner):
        """Test handling of permission denied error."""
        with patch("arbiter.learner.validation.SCHEMA_DIR_PERMISSION_CHECK", True):
            with patch("os.access", return_value=False):
                # The function should raise OSError on permission denied
                # But it's wrapped with retry, which will retry 3 times and wrap in RetryError
                with pytest.raises(RetryError) as exc_info:
                    await reload_schemas(mock_learner, "/some/directory")

                # Verify the original error was OSError about readability
                assert "not readable" in str(exc_info.value.__cause__)

    @pytest.mark.asyncio
    async def test_reload_schemas_with_hooks(self, mock_learner, temp_schema_dir):
        """Test that on_schema_reload hooks are called."""
        sync_hook = Mock()
        async_hook = AsyncMock()

        mock_learner.event_hooks["on_schema_reload"] = [sync_hook, async_hook]

        with patch("arbiter.learner.validation.time") as mock_time:
            mock_time.perf_counter.side_effect = [1.0, 2.0]

            await reload_schemas(mock_learner, temp_schema_dir)

            # Verify hooks were called with new schemas
            sync_hook.assert_called_once_with(mock_learner.validation_schemas)
            async_hook.assert_called_once_with(mock_learner.validation_schemas)

    @pytest.mark.asyncio
    async def test_reload_schemas_redis_failure(self, mock_learner, temp_schema_dir):
        """Test that Redis failure doesn't break schema reload."""
        mock_learner.redis.setex = AsyncMock(side_effect=Exception("Redis error"))

        with patch("arbiter.learner.validation.time") as mock_time:
            mock_time.perf_counter.side_effect = [1.0, 2.0]

            # Should not raise, just log warning
            await reload_schemas(mock_learner, temp_schema_dir)

            # Schemas should still be loaded
            assert len(mock_learner.validation_schemas) == 2

    @pytest.mark.asyncio
    async def test_reload_schemas_retry_on_failure(self, mock_learner):
        """Test retry mechanism on schema reload failure."""
        with patch("os.path.exists", return_value=True):
            with patch("os.access", return_value=True):
                # Always fail with OSError to test retry logic
                with patch("os.listdir", side_effect=OSError("Temporary failure")):
                    with patch("arbiter.learner.validation.SCHEMA_RELOAD_RETRIES", 2):
                        # Should raise RetryError after 2 retries (wrapping the OSError)
                        with pytest.raises(RetryError) as exc_info:
                            await reload_schemas(mock_learner, "/test/dir")

                        # Verify the original error was the expected OSError
                        assert "Temporary failure" in str(exc_info.value.__cause__)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--cov=arbiter.learner.validation"])
