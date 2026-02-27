# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

import pytest


def test_mesh_exports_checkpoint_factories():
    from self_fixing_engineer import mesh

    assert callable(mesh.checkpoint_manager)
    assert callable(mesh.get_checkpoint_manager)
    assert callable(mesh.checkpoint_session)
    assert mesh.Environment is not None


def test_checkpoint_retryable_error_is_exported():
    from self_fixing_engineer.mesh.checkpoint import CheckpointRetryableError

    assert issubclass(CheckpointRetryableError, Exception)
    assert CheckpointRetryableError is not None


@pytest.mark.asyncio
async def test_backend_registry_dispatch_uses_operation_handler():
    from unittest.mock import AsyncMock, patch

    from self_fixing_engineer.mesh.checkpoint.checkpoint_manager import CheckpointManager

    manager = CheckpointManager(backend_type="s3")
    manager._initialized = True

    with patch(
        "self_fixing_engineer.mesh.checkpoint.checkpoint_backends.get_backend_handler",
        new=AsyncMock(),
    ) as get_handler_mock:
        backend_save = AsyncMock(return_value="abc123")
        get_handler_mock.return_value = backend_save
        version_hash = await manager.save("checkpoint-name", {"n": 1})

    assert version_hash == "abc123"
    get_handler_mock.assert_awaited_once_with("s3", "save")
    backend_save.assert_awaited_once()
