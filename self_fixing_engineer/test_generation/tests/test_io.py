# test_generation/gen_agent/tests/test_io.py
import gzip
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# Module under test
from test_generation.gen_agent import io_utils as io_utils_mod


@pytest.fixture
def temp_dir():
    d = tempfile.TemporaryDirectory()
    try:
        yield Path(d.name)
    finally:
        d.cleanup()


def test_validate_path_prevents_traversal(temp_dir):
    """Path with '..' should raise ValueError."""
    base = temp_dir
    with pytest.raises(ValueError):
        io_utils_mod.validate_and_resolve_path(str(base / ".." / "evil.jsonl"))


@pytest.mark.asyncio
async def test_append_creates_file_and_writes_json(temp_dir):
    """Should create .jsonl file and append JSON-encoded entry."""
    log_path = temp_dir / "feedback.jsonl"
    entry = {"msg": "hello"}
    await io_utils_mod.append_to_feedback_log(str(log_path), entry)
    content = log_path.read_text(encoding="utf-8")
    assert json.loads(content.strip()) == entry


@pytest.mark.asyncio
async def test_append_existing_file_appends_newline(temp_dir):
    """Second append should result in multiple lines."""
    log_path = temp_dir / "feedback.jsonl"
    await io_utils_mod.append_to_feedback_log(str(log_path), {"n": 1})
    await io_utils_mod.append_to_feedback_log(str(log_path), {"n": 2})
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["n"] == 1
    assert json.loads(lines[1])["n"] == 2


@pytest.mark.asyncio
async def test_append_to_gzip_file(temp_dir):
    """Should append entry to existing gzip file."""
    log_path = temp_dir / "feedback.jsonl.gz"
    # Create initial gzip file
    with gzip.open(log_path, "wt", encoding="utf-8") as f:
        f.write(json.dumps({"n": 1}) + "\n")

    await io_utils_mod.append_to_feedback_log(str(log_path), {"n": 2})

    with gzip.open(log_path, "rt", encoding="utf-8") as f:
        lines = [json.loads(line) for line in f]
    assert len(lines) == 2
    assert lines[0]["n"] == 1
    assert lines[1]["n"] == 2


@pytest.mark.asyncio
async def test_auto_compress_when_threshold_exceeded(temp_dir):
    """
    Deterministic compression without env/threshold dependency.
    Also neutralize redaction so contents match exactly.
    """
    log_path = temp_dir / "feedback.jsonl"
    big_entry = {"data": "x" * (io_utils_mod.FEEDBACK_COMPRESS_BYTES + 100)}

    # Force compression AND bypass redaction so the payload is preserved
    with patch(
        "test_generation.gen_agent.io_utils.redact_sensitive", side_effect=lambda x: x
    ):
        await io_utils_mod.append_to_feedback_log(
            str(log_path),
            big_entry,
            {"enable_compression": True},
        )

    gz_path = log_path.with_suffix(".jsonl.gz")
    assert gz_path.exists()

    with gzip.open(gz_path, "rt", encoding="utf-8") as f:
        line = f.readline()
        assert json.loads(line) == big_entry


@pytest.mark.asyncio
async def test_append_respects_redaction(temp_dir):
    """Sensitive data should be redacted before writing."""
    log_path = temp_dir / "feedback.jsonl"
    entry = {"api_key": "12345-secret"}
    with patch(
        "test_generation.gen_agent.io_utils.redact_sensitive",
        return_value={"api_key": "[REDACTED]"},
    ) as mock_redact:
        await io_utils_mod.append_to_feedback_log(str(log_path), entry)

    content = log_path.read_text(encoding="utf-8")
    assert "[REDACTED]" in content
    mock_redact.assert_called_once()


@pytest.mark.asyncio
async def test_invalid_path_type_raises(temp_dir):
    """Non-string path should raise a ValueError."""
    with pytest.raises(ValueError, match="Path must be a string"):
        # Pass a non-string path on purpose
        await io_utils_mod.append_to_feedback_log(temp_dir / "x.jsonl", {"x": 1})  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_no_prometheus_duplicates(temp_dir):
    """
    Tests that the metric proxy correctly instantiates a real metric.
    """
    try:
        log_path = temp_dir / "test.jsonl"
        # Force metrics to be available for this test
        with patch("test_generation.gen_agent.io_utils._PROM_OK", True):
            # Append a log entry to trigger metric instantiation
            await io_utils_mod.append_to_feedback_log(str(log_path), {})

            # The metric proxy should be a real metric now, not a dummy
            from test_generation.gen_agent.io_utils import (
                _NoopMetric,
                io_write_duration,
            )

            assert not isinstance(io_write_duration, _NoopMetric)
    finally:
        if os.path.exists(log_path):
            os.remove(log_path)


def test_io_import():
    """
    Tests that the main io_utils functions can be imported and are callable.
    This serves as a guard against import chain failures.
    """
    from test_generation.gen_agent import io_utils

    assert callable(io_utils.append_to_feedback_log)
