import os
import json
import time
import asyncio
import threading
import uuid
import io
import re
from unittest.mock import patch, MagicMock, AsyncMock, mock_open, PropertyMock
import pytest
from pytest_asyncio import fixture
from tenacity import Retrying, stop_after_attempt, wait_exponential, retry_if_exception_type, before_sleep_log
import pandas as pd
from langchain_core.language_models.base import BaseLanguageModel

# Import asyncpg types for mocking
try:
    import asyncpg
    import asyncpg.exceptions
except ImportError:
    # Create mock asyncpg module if not available
    class AsyncPGExceptions:
        class PostgresError(Exception):
            pass
    
    class MockAsyncPG:
        exceptions = AsyncPGExceptions()
    
    asyncpg = MockAsyncPG()

# Import the module under test
import intent_capture.requirements as requirements_module
from intent_capture.requirements import (
    get_embedding_model,
    get_db_conn_pool,
    db_get_custom_checklists,
    db_save_custom_checklists,
    get_global_custom_checklists,
    set_global_custom_checklists,
    get_checklist,
    add_item,
    update_item_status,
    _generate_novel_requirements,
    suggest_requirements,
    propose_checklist_updates,
    log_coverage_snapshot,
    get_coverage_history,
    generate_coverage_report,
    compute_coverage,
    register_plugin_requirements,
    REQUIREMENTS_CHECKLIST,
    DOMAIN_SPECIFIC,
    CUSTOM_CHECKLISTS_FILE,
    COVERAGE_HISTORY_FILE,
    logger,
    _file_lock,
    _model_lock,
    _EMBEDDING_MODEL,
    DB_AVAILABLE,
    REDIS_AVAILABLE,
    ML_ENABLED,
    PANDAS_AVAILABLE
)

# --- Test Fixtures ---
@pytest.fixture
def mock_asyncpg():
    """Mock asyncpg for DB operations."""
    if not DB_AVAILABLE:
        pytest.skip("asyncpg not available for testing")
    
    mock_pool = AsyncMock()
    mock_conn = AsyncMock()
    # Return already parsed JSON, not a JSON string
    mock_conn.fetch = AsyncMock(return_value=[("proj", "dom", [{"id": "1"}])])
    mock_conn.execute = AsyncMock(return_value="success")
    
    # Create a proper async context manager for transaction
    mock_transaction = AsyncMock()
    mock_transaction.__aenter__ = AsyncMock(return_value=None)
    mock_transaction.__aexit__ = AsyncMock(return_value=None)
    mock_conn.transaction = MagicMock(return_value=mock_transaction)
    
    # Create a proper async context manager for acquire
    mock_acquire_context = AsyncMock()
    mock_acquire_context.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_acquire_context.__aexit__ = AsyncMock(return_value=None)
    mock_pool.acquire = MagicMock(return_value=mock_acquire_context)
    
    with patch('intent_capture.requirements.asyncpg.create_pool', AsyncMock(return_value=mock_pool)):
        yield mock_pool

@pytest.fixture
def mock_redis():
    """Mock redis.asyncio client."""
    if not REDIS_AVAILABLE:
        pytest.skip("redis.asyncio not available for testing")
    
    mock_client = AsyncMock()
    mock_client.rpush = AsyncMock(return_value=1)
    mock_client.lrange = AsyncMock(return_value=[json.dumps({"domain": "dom", "coverage_percent": 50}).encode('utf-8')])
    mock_client.get = AsyncMock(return_value=json.dumps({"key": "value"}).encode('utf-8'))
    mock_client.set = AsyncMock(return_value=True)
    mock_client.smembers = AsyncMock(return_value=set())
    
    # Create async Redis.from_url that returns the mock client
    async def async_from_url(*args, **kwargs):
        return mock_client
    
    with patch('intent_capture.requirements.redis.Redis.from_url', side_effect=async_from_url):
        yield mock_client

@pytest.fixture
def mock_sentence_transformer():
    """Mock SentenceTransformer."""
    if not ML_ENABLED:
        pytest.skip("sentence-transformers not available for testing")
    
    mock_model = MagicMock()
    mock_model.encode = MagicMock(return_value=MagicMock())
    
    with patch('intent_capture.requirements.SentenceTransformer', return_value=mock_model):
        # Reset the singleton's embedding model
        requirements_module.manager._embedding_model = None
        yield mock_model

@pytest.fixture
def mock_llm():
    """Mock LLM for suggestions."""
    mock_response = MagicMock(content='[{"name": "novel_req", "description": "desc"}]')
    mock_llm = MagicMock(ainvoke=AsyncMock(return_value=mock_response))
    yield mock_llm

@pytest.fixture
def mock_tracer():
    if not requirements_module.OPENTELEMETRY_AVAILABLE:
        pytest.skip("OpenTelemetry not available in requirements module.")
    
    mock_span = MagicMock()
    mock_span.set_attribute = MagicMock()
    mock_span.add_event = MagicMock()
    mock_span.set_status = MagicMock()
    mock_span.__enter__ = MagicMock(return_value=mock_span)
    mock_span.__exit__ = MagicMock(return_value=None)
    
    mock_tracer_instance = MagicMock()
    mock_tracer_instance.start_as_current_span = MagicMock(return_value=mock_span)
    
    with patch('intent_capture.requirements.tracer', mock_tracer_instance):
        yield mock_tracer_instance

@pytest.fixture
def mock_prometheus():
    if not requirements_module.PROMETHEUS_AVAILABLE:
        pytest.skip("Prometheus not available in requirements module.")
    
    mock_counter = MagicMock(inc=MagicMock(), labels=MagicMock(return_value=MagicMock(inc=MagicMock())))
    mock_histogram = MagicMock(observe=MagicMock(), labels=MagicMock(return_value=MagicMock(observe=MagicMock())))
    
    with patch('intent_capture.requirements.REQ_SUGGESTIONS_TOTAL', mock_counter), \
         patch('intent_capture.requirements.REQ_SUGGESTIONS_LATENCY_SECONDS', mock_histogram), \
         patch('intent_capture.requirements.DB_OPS_TOTAL', mock_counter), \
         patch('intent_capture.requirements.DB_OPS_LATENCY_SECONDS', mock_histogram), \
         patch('intent_capture.requirements.ML_MODEL_LOAD_LATENCY_SECONDS', mock_histogram):
        yield

@pytest.fixture
def temp_files(tmp_path, monkeypatch):
    """Create temporary files for testing."""
    custom_file = tmp_path / CUSTOM_CHECKLISTS_FILE
    custom_file.write_text(json.dumps({"proj": {"dom": [{"id": "1"}]}}))
    coverage_file = tmp_path / COVERAGE_HISTORY_FILE
    coverage_file.write_text(json.dumps({"proj": [{"domain": "dom", "coverage_percent": 50, "covered_items": 5, "total_items": 10, "timestamp": "2025-09-11T19:00:25.233082"}]}))
    
    monkeypatch.setattr('intent_capture.requirements.CUSTOM_CHECKLISTS_FILE', str(custom_file))
    monkeypatch.setattr('intent_capture.requirements.COVERAGE_HISTORY_FILE', str(coverage_file))
    
    yield custom_file, coverage_file

@pytest.fixture
def mock_cachetools():
    """Mock cachetools TTLCache."""
    mock_cache = MagicMock()
    mock_cache.get.return_value = None
    mock_cache.__contains__.return_value = False
    mock_cache.__setitem__ = MagicMock()
    mock_cache.__getitem__ = MagicMock()
    
    with patch('intent_capture.requirements.TTLCache', return_value=mock_cache):
        yield mock_cache

@pytest.fixture(autouse=True)
def reset_manager():
    """Reset the manager singleton before each test."""
    requirements_module.manager = requirements_module.RequirementsManager()
    yield

# --- Tests for Tracing Context ---
def test_get_tracing_context(mock_tracer):
    """Test getting tracing context."""
    with requirements_module.get_tracing_context("test_span"):
        pass
    mock_tracer.start_as_current_span.assert_called_with("test_span")

def test_get_tracing_context_no_opentelemetry(monkeypatch):
    """Test fallback context when no opentelemetry."""
    monkeypatch.setattr('intent_capture.requirements.OPENTELEMETRY_AVAILABLE', False)
    with requirements_module.get_tracing_context("test_span"):
        pass

# --- Tests for Embedding Model Loading ---
@pytest.mark.asyncio
async def test_get_embedding_model_success(mock_sentence_transformer, mock_tracer, mock_prometheus):
    """Test successful embedding model loading."""
    model = await get_embedding_model()
    assert model is not None

@pytest.mark.asyncio
async def test_get_embedding_model_no_ml():
    """Test model loading when ML not enabled."""
    with patch('intent_capture.requirements.ML_ENABLED', False):
        with pytest.raises(ImportError):
            await get_embedding_model()

# --- Tests for DB Connection Pool ---
@pytest.mark.asyncio
async def test_get_db_conn_pool_success(mock_asyncpg, monkeypatch):
    """Test successful DB connection pool creation."""
    monkeypatch.setenv("REQ_DB_NAME", "test_db")
    monkeypatch.setenv("REQ_DB_USER", "test_user")
    monkeypatch.setenv("REQ_DB_PASS", "test_pass")
    monkeypatch.setenv("REQ_DB_HOST", "test_host")
    
    pool = await get_db_conn_pool()
    assert pool is not None

@pytest.mark.asyncio
async def test_get_db_conn_pool_no_db():
    """Test pool when DB not available."""
    with patch('intent_capture.requirements.DB_AVAILABLE', False):
        with pytest.raises(ImportError):
            await get_db_conn_pool()

@pytest.mark.asyncio
async def test_get_db_conn_pool_missing_vars(monkeypatch):
    """Test pool failure with missing vars."""
    monkeypatch.delenv("REQ_DB_NAME", raising=False)
    monkeypatch.delenv("REQ_DB_USER", raising=False)
    monkeypatch.delenv("REQ_DB_PASS", raising=False)
    monkeypatch.delenv("REQ_DB_HOST", raising=False)
    
    # Set PROD_MODE to trigger the localhost check
    monkeypatch.setenv("PROD_MODE", "true")
    # The code will use default "localhost" for REQ_DB_HOST
    
    # Mock asyncpg.create_pool to prevent actual connection attempts
    with patch('intent_capture.requirements.asyncpg.create_pool', AsyncMock()) as mock_create_pool:
        with pytest.raises(SystemExit):
            await get_db_conn_pool()

# --- Tests for DB Get Custom Checklists ---
@pytest.mark.asyncio
async def test_db_get_custom_checklists_success(mock_asyncpg, mock_tracer, mock_prometheus):
    """Test successful DB get checklists."""
    checklists = await db_get_custom_checklists("proj")
    assert checklists == {"proj": {"dom": [{"id": "1"}]}}

@pytest.mark.asyncio
async def test_db_get_custom_checklists_retry(monkeypatch):
    """Test DB get checklists retry on error."""
    if not DB_AVAILABLE:
        pytest.skip("asyncpg not available for testing")
    
    # Mock pool with retry behavior
    mock_pool = AsyncMock()
    mock_conn = AsyncMock()
    # Return parsed JSON object, not JSON string
    mock_conn.fetch = AsyncMock(return_value=[("proj", "dom", [{"id": "1"}])])
    
    # First call raises error, second succeeds
    call_count = 0
    def acquire_side_effect():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise asyncpg.exceptions.PostgresError("Connection error")
        mock_context = AsyncMock()
        mock_context.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_context.__aexit__ = AsyncMock(return_value=None)
        return mock_context
    
    mock_pool.acquire = MagicMock(side_effect=acquire_side_effect)
    
    with patch('intent_capture.requirements.asyncpg.create_pool', AsyncMock(return_value=mock_pool)):
        checklists = await db_get_custom_checklists("proj")
        assert checklists == {"proj": {"dom": [{"id": "1"}]}}

# --- Tests for DB Save Custom Checklists ---
@pytest.mark.asyncio
async def test_db_save_custom_checklists_success(mock_asyncpg, mock_tracer, mock_prometheus):
    """Test successful DB save checklists."""
    customs = {"proj": {"dom": [{"id": "1"}]}}
    await db_save_custom_checklists(customs)

# --- Tests for Global Custom Checklists ---
@pytest.mark.asyncio
async def test_get_global_custom_checklists_db(mock_asyncpg, temp_files):
    """Test getting global checklists from DB."""
    checklists = await get_global_custom_checklists()
    assert checklists == {"proj": {"dom": [{"id": "1"}]}}

@pytest.mark.asyncio
async def test_get_global_custom_checklists_file(temp_files):
    """Test getting global checklists from file."""
    with patch('intent_capture.requirements.DB_AVAILABLE', False):
        checklists = await get_global_custom_checklists()
    assert checklists == {"proj": {"dom": [{"id": "1"}]}}

@pytest.mark.asyncio
async def test_set_global_custom_checklists_db(mock_asyncpg):
    """Test setting global checklists to DB."""
    customs = {"proj": {"dom": [{"id": "1"}]}}
    await set_global_custom_checklists(customs)

@pytest.mark.asyncio
async def test_set_global_custom_checklists_file(temp_files):
    """Test setting global checklists to file."""
    with patch('intent_capture.requirements.DB_AVAILABLE', False):
        customs = {"proj": {"dom": [{"id": "1"}]}}
        await set_global_custom_checklists(customs)
    with open(temp_files[0], "r") as f:
        data = json.load(f)
    assert data == {"proj": {"dom": [{"id": "1"}]}}

# --- Tests for Get Checklist ---
@pytest.mark.asyncio
async def test_get_checklist(mock_asyncpg):
    """Test getting combined checklist."""
    checklist = await get_checklist("fintech")
    assert any(c["id"] == "FIN001" for c in checklist)

# --- Tests for Add Item ---
@pytest.mark.asyncio
async def test_add_item_success(mock_asyncpg):
    """Test adding item successfully."""
    with patch('intent_capture.requirements.CACHETOOLS_AVAILABLE', False):
        result = await add_item("dom", "name", 1, "desc")
    assert "Added 'name'" in result

@pytest.mark.asyncio
async def test_add_item_invalid_name():
    """Test adding item with invalid name."""
    with pytest.raises(ValueError):
        await add_item("dom", "", 1, "desc")

# --- Tests for Update Item Status ---
@pytest.mark.asyncio
async def test_update_item_status_custom(mock_asyncpg):
    """Test updating custom item status."""
    # Create a valid UUID for testing
    test_uuid = str(uuid.uuid4())
    test_id = f"CUST-{test_uuid}"
    
    with patch('intent_capture.requirements.RequirementsManager.get_global_custom_checklists', 
               AsyncMock(return_value={"default_project": {"dom": [{"id": test_id, "name": "test", "status": "Uncovered"}]}})):
        with patch('intent_capture.requirements.RequirementsManager.set_global_custom_checklists', AsyncMock(return_value=True)):
            success = await update_item_status(test_id, "Covered", domain="dom")
            assert success

@pytest.mark.asyncio
async def test_update_item_status_global():
    """Test updating global item status."""
    # Mock the DB operations to prevent actual connection attempts
    with patch('intent_capture.requirements.DB_AVAILABLE', False):
        # For global items, the UUID validation should be skipped
        # REQ001 doesn't have a UUID suffix, so it should be handled differently
        success = await update_item_status("REQ001", "Covered")
        # Since REQ001 is in REQUIREMENTS_CHECKLIST, it should update in memory and return True
        assert success

@pytest.mark.asyncio
async def test_update_item_status_invalid():
    """Test invalid status update."""
    success = await update_item_status("invalid", "Invalid")
    assert not success

# --- Tests for Generate Novel Requirements ---
@pytest.mark.asyncio
async def test_generate_novel_requirements_success(mock_llm, mock_tracer, mock_prometheus):
    """Test successful novel requirements generation."""
    reqs = await _generate_novel_requirements("context", mock_llm)
    assert len(reqs) == 1
    assert reqs[0]["name"] == "novel_req"

@pytest.mark.asyncio
async def test_generate_novel_requirements_timeout(mock_llm):
    """Test timeout in novel requirements."""
    mock_llm.ainvoke = AsyncMock(side_effect=asyncio.TimeoutError)
    # Should raise after retries
    with pytest.raises(Exception):  # Will be wrapped in RetryError
        await _generate_novel_requirements("context", mock_llm)

@pytest.mark.asyncio
async def test_generate_novel_requirements_invalid_response(mock_llm):
    """Test invalid LLM response."""
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="not a list"))
    # Should raise after retries
    with pytest.raises(Exception):  # Will be wrapped in RetryError
        await _generate_novel_requirements("context", mock_llm)

# --- Tests for Suggest Requirements ---
@pytest.mark.asyncio
async def test_suggest_requirements_ml(mock_sentence_transformer, mock_llm):
    """Test suggestions with ML."""
    mock_sentence_transformer.encode.return_value = MagicMock(spec=list)
    
    with patch('intent_capture.requirements.util.pytorch_cos_sim', 
               return_value=MagicMock(spec=list, __getitem__=MagicMock(return_value=[MagicMock(item=MagicMock(return_value=0.6))]))):
        suggestions = await suggest_requirements("fintech", "snippet", REQUIREMENTS_CHECKLIST, mock_llm)
    assert len(suggestions) > 0

@pytest.mark.asyncio
async def test_suggest_requirements_no_ml(mock_llm):
    """Test suggestions without ML."""
    with patch('intent_capture.requirements.ML_ENABLED', False):
        suggestions = await suggest_requirements("fintech", "snippet", REQUIREMENTS_CHECKLIST, mock_llm)
    assert len(suggestions) == 1

# --- Tests for Propose Checklist Updates ---
@pytest.mark.asyncio
async def test_propose_checklist_updates_success(mock_llm):
    """Test successful checklist updates proposal."""
    proposals = await propose_checklist_updates("transcript", REQUIREMENTS_CHECKLIST, mock_llm)
    assert len(proposals) == 1

@pytest.mark.asyncio
async def test_propose_checklist_updates_timeout(mock_llm):
    """Test timeout in checklist updates."""
    mock_llm.ainvoke = AsyncMock(side_effect=asyncio.TimeoutError)
    # Should raise after retries
    with pytest.raises(Exception):  # Will be wrapped in RetryError
        await propose_checklist_updates("transcript", REQUIREMENTS_CHECKLIST, mock_llm)

# --- Tests for Coverage Analytics ---
@pytest.mark.asyncio
async def test_log_coverage_snapshot_redis(mock_redis):
    """Test logging coverage to Redis."""
    await log_coverage_snapshot("proj", "dom", 50.0, 5, 10)
    mock_redis.rpush.assert_called()

@pytest.mark.asyncio
async def test_log_coverage_snapshot_file(temp_files):
    """Test logging coverage to file."""
    with patch('intent_capture.requirements.REDIS_AVAILABLE', False):
        await log_coverage_snapshot("proj", "dom", 50.0, 5, 10)
    with open(temp_files[1], "r") as f:
        data = json.load(f)
    assert "proj" in data

@pytest.mark.asyncio
async def test_get_coverage_history_redis(mock_redis):
    """Test getting history from Redis."""
    history = await get_coverage_history("proj")
    assert len(history) == 1

@pytest.mark.asyncio
async def test_get_coverage_history_file(temp_files):
    """Test getting history from file."""
    with patch('intent_capture.requirements.REDIS_AVAILABLE', False):
        history = await get_coverage_history("proj")
    assert len(history) == 1

@pytest.mark.asyncio
async def test_generate_coverage_report():
    """Test generating coverage report."""
    with patch('intent_capture.requirements.RequirementsManager.get_coverage_history', 
               AsyncMock(return_value=[{"domain": "dom", "coverage_percent": 50, "covered_items": 5, "total_items": 10}])):
        report = await generate_coverage_report("proj")
    assert "Coverage Report" in report

@pytest.mark.asyncio
async def test_compute_coverage_pandas():
    """Test computing coverage with pandas."""
    if not PANDAS_AVAILABLE:
        pytest.skip("pandas not available for testing")
    
    # Create a proper markdown table with correct formatting
    markdown = """| ID | Status |
| REQ1 | Covered |
| REQ2 | Uncovered |"""
    
    coverage = await compute_coverage(markdown)
    assert coverage["percent"] == 50.0
    assert coverage["covered"] == 1
    assert coverage["total"] == 2

@pytest.mark.asyncio
async def test_compute_coverage_llm_fallback(mock_llm):
    """Test LLM fallback for coverage computation."""
    with patch('intent_capture.requirements.PANDAS_AVAILABLE', False):
        markdown = "| ID | Status |\n| REQ1 | Covered |\n| REQ2 | Uncovered |"
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content='{"percent": 50.0, "covered": 1, "total": 2}'))
        coverage = await compute_coverage(markdown, mock_llm)
    assert coverage["percent"] == 50.0

@pytest.mark.asyncio
async def test_compute_coverage_timeout(mock_llm):
    """Test timeout in LLM coverage computation."""
    with patch('intent_capture.requirements.PANDAS_AVAILABLE', False):
        mock_llm.ainvoke = AsyncMock(side_effect=asyncio.TimeoutError)
        coverage = await compute_coverage("markdown", mock_llm)
    assert coverage["percent"] == 0.0

# --- Tests for Plugin Requirements Registration ---
def test_register_plugin_requirements():
    """Test registering plugin requirements."""
    requirements = [{"name": "plug_req", "description": "desc"}]
    register_plugin_requirements("plug_dom", requirements)
    assert "plug_dom" in DOMAIN_SPECIFIC
    assert DOMAIN_SPECIFIC["plug_dom"][0]["name"] == "plug_req"