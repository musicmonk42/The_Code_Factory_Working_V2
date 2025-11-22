import pytest
import os
import asyncio
import tempfile
import logging
from unittest.mock import patch, AsyncMock, MagicMock, mock_open
from test_generation.backends import BackendRegistry, PynguinBackend, JestLLMBackend, DiffblueBackend, _validate_inputs
from tenacity import RetryError as RetriesExceeded
import random

# Fix: Added imports for the new backends

# Mark all tests as unit tests
pytestmark = pytest.mark.unit

@pytest.fixture
def mock_config():
    """Fixture for a mock ATCO configuration."""
    return {
        "backend_timeouts": {"pynguin": 60, "jest_llm": 90, "diffblue": 180, "cargo": 120, "go": 120},
        "llm_model": "gpt-4o",
        "simulated_failure_rates": {"diffblue": 0.1}
    }

@pytest.fixture
def temp_project_root():
    """Fixture for a temporary project root directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir

# --- Tests for BackendRegistry ---

def test_backend_registry_register_and_get():
    """Test registering and retrieving a backend."""
    registry = BackendRegistry()
    mock_backend = MagicMock()
    registry.register_backend("test_lang", mock_backend)
    assert registry.get_backend("test_lang") == mock_backend

def test_backend_registry_overwrite_warning(caplog):
    """Test overwriting an existing backend logs a warning."""
    registry = BackendRegistry()
    mock_backend1 = MagicMock()
    mock_backend2 = MagicMock()
    registry.register_backend("test_lang", mock_backend1)
    with caplog.at_level(logging.WARNING):
        registry.register_backend("test_lang", mock_backend2)
    assert "already registered. Overwriting." in caplog.text
    assert registry.get_backend("test_lang") == mock_backend2

def test_backend_registry_list_backends():
    """Test listing registered backends."""
    registry = BackendRegistry()
    mock_backend = MagicMock()
    registry.register_backend("lang1", mock_backend)
    registry.register_backend("lang2", mock_backend)
    backends = registry.list_backends()
    assert set(backends) == {"lang1", "lang2"}

def test_backend_registry_get_nonexistent():
    """Test getting a non-existent backend returns None."""
    registry = BackendRegistry()
    assert registry.get_backend("nonexistent") is None

# --- Tests for Input Validation (_validate_inputs) ---

@pytest.mark.parametrize(
    "target_id, output_path, params, expected_exception",
    [
        ("valid.module", "output/dir", {"retry_count": 0, "timeout": 60}, None),  # Valid
        ("invalid@module", "output/dir", {"retry_count": 0, "timeout": 60}, ValueError),  # Invalid char in target_id
        ("valid.module", "../invalid", {"retry_count": 0, "timeout": 60}, ValueError),  # Path traversal in output_path
        ("valid.module", "", {"retry_count": 0, "timeout": 60}, ValueError),  # Empty output_path
        ("valid.module", "output/dir", {"retry_count": -1, "timeout": 60}, ValueError),  # Negative retry_count
        ("valid.module", "output/dir", {"retry_count": 0, "timeout": 0}, ValueError),  # Zero timeout
        ("valid.module", "output/dir", {"retry_count": "invalid", "timeout": 60}, ValueError),  # Non-int retry_count
    ]
)
def test_validate_inputs(target_id, output_path, params, expected_exception):
    """Test input validation for backend parameters."""
    if expected_exception:
        with pytest.raises(expected_exception):
            _validate_inputs(target_id, output_path, params)
    else:
        _validate_inputs(target_id, output_path, params)  # No exception

# --- Tests for PynguinBackend ---

def test_pynguin_backend_init_success(mock_config, temp_project_root):
    """Test successful initialization of PynguinBackend."""
    backend = PynguinBackend(mock_config, temp_project_root)
    assert backend.project_root == os.path.abspath(temp_project_root)
    assert "pynguin" in backend.config["backend_timeouts"]

def test_pynguin_backend_init_missing_config_key(mock_config, temp_project_root):
    """Test initialization fails if required config key is missing."""
    del mock_config["backend_timeouts"]
    with pytest.raises(ValueError, match="Missing required config key: backend_timeouts"):
        PynguinBackend(mock_config, temp_project_root)

@pytest.mark.asyncio
async def test_pynguin_backend_generate_success(mock_config, temp_project_root):
    """Test successful test generation with Pynguin."""
    backend = PynguinBackend(mock_config, temp_project_root)
    
    with patch("asyncio.create_subprocess_exec") as mock_exec, \
         patch("os.walk") as mock_walk, \
         patch("shutil.move") as mock_move:
        mock_process = AsyncMock()
        mock_process.communicate.return_value = (b"", b"")
        mock_process.returncode = 0
        mock_exec.return_value = mock_process
        
        mock_walk.return_value = [("/output", [], ["test_module.py"])]
        
        success, err, path = await backend.generate_tests("module", "output", {"retry_count": 0})
        assert success
        assert path == "output/module/test_module.py"
        mock_move.assert_called_once()

@pytest.mark.asyncio
async def test_pynguin_backend_generate_timeout(mock_config, temp_project_root):
    """Test Pynguin generation timeout handling."""
    backend = PynguinBackend(mock_config, temp_project_root)
    
    with patch("asyncio.create_subprocess_exec") as mock_exec:
        mock_process = AsyncMock()
        mock_process.communicate.side_effect = asyncio.TimeoutError
        mock_exec.return_value = mock_process
        
        success, err, path = await backend.generate_tests("module", "output", {"retry_count": 0})
        assert not success
        assert "timed out" in err
        assert path is None

@pytest.mark.asyncio
async def test_pynguin_backend_generate_no_file_generated(mock_config, temp_project_root):
    """Test Pynguin runs but no file is generated."""
    backend = PynguinBackend(mock_config, temp_project_root)
    
    with patch("asyncio.create_subprocess_exec") as mock_exec, \
         patch("os.walk") as mock_walk:
        mock_process = AsyncMock()
        mock_process.communicate.return_value = (b"", b"")
        mock_process.returncode = 0
        mock_exec.return_value = mock_process
        
        mock_walk.return_value = []  # No files found
        
        success, err, path = await backend.generate_tests("module", "output", {"retry_count": 0})
        assert not success
        assert "no test file was created" in err
        assert path is None

# --- Tests for JestLLMBackend ---

def test_jest_llm_backend_init_success(mock_config, temp_project_root, monkeypatch):
    """Test successful initialization of JestLLMBackend."""
    monkeypatch.setenv("OPENAI_API_KEY", "fake_key")
    backend = JestLLMBackend(mock_config, temp_project_root)
    assert backend.project_root == os.path.abspath(temp_project_root)
    assert backend.llm.model == "gpt-4o"

def test_jest_llm_backend_init_missing_config_key(mock_config, temp_project_root):
    """Test initialization fails if required config key is missing."""
    del mock_config["backend_timeouts"]
    with pytest.raises(ValueError, match="Missing required config key: backend_timeouts"):
        JestLLMBackend(mock_config, temp_project_root)

def test_jest_llm_backend_init_no_langchain(monkeypatch, mock_config, temp_project_root):
    """Test initialization fails if langchain-openai is not available."""
    monkeypatch.setattr("test_generation.backends.LANGCHAIN_OPENAI_AVAILABLE", False)
    with pytest.raises(ImportError, match="langchain-openai must be installed"):
        JestLLMBackend(mock_config, temp_project_root)

@pytest.mark.asyncio
async def test_jest_llm_backend_generate_success(mock_config, temp_project_root, monkeypatch):
    """Test successful test generation with JestLLMBackend."""
    # Fix: Set environment variable for LLM tests
    monkeypatch.setenv("OPENAI_API_KEY", "fake_key")
    backend = JestLLMBackend(mock_config, temp_project_root)
    
    with patch.object(backend.llm, "ainvoke") as mock_ainvoke, \
         patch("builtins.open", mock_open(read_data="source code")):
        mock_ainvoke.return_value.content = "// Generated test code"
        
        success, err, path = await backend.generate_tests("file.js", "output", {"timeout": 90})
        assert success
        assert path == "output/file.js.test.js"
        with open(os.path.join(temp_project_root, path), "r") as f:
            assert f.read() == "// Generated test code"

@pytest.mark.asyncio
async def test_jest_llm_backend_generate_timeout(mock_config, temp_project_root, monkeypatch):
    """Test JestLLM generation timeout handling."""
    # Fix: Set environment variable for LLM tests
    monkeypatch.setenv("OPENAI_API_KEY", "fake_key")
    backend = JestLLMBackend(mock_config, temp_project_root)
    
    with patch.object(backend.llm, "ainvoke", side_effect=asyncio.TimeoutError):
        success, err, path = await backend.generate_tests("file.js", "output", {"timeout": 90})
        assert not success
        assert "timed out" in err
        assert path is None

@pytest.mark.asyncio
async def test_jest_llm_backend_generate_retry(mock_config, temp_project_root, monkeypatch):
    """Test retry logic for LLM failures in JestLLMBackend."""
    # Fix: Set environment variable for LLM tests
    monkeypatch.setenv("OPENAI_API_KEY", "fake_key")
    backend = JestLLMBackend(mock_config, temp_project_root)
    
    with patch.object(backend.llm, "ainvoke", side_effect=[Exception("Fail1"), Exception("Fail2"), MagicMock(content="// Success")]):
        success, err, path = await backend.generate_tests("file.js", "output", {"retry_count": 2})
        assert success
        assert path.endswith(".test.js")

@pytest.mark.asyncio
async def test_jest_llm_backend_generate_retry_exceeded(mock_config, temp_project_root, monkeypatch):
    """Test retry exceeded in JestLLMBackend."""
    # Fix: Set environment variable for LLM tests
    monkeypatch.setenv("OPENAI_API_KEY", "fake_key")
    backend = JestLLMBackend(mock_config, temp_project_root)
    
    with patch.object(backend.llm, "ainvoke", side_effect=Exception("Persistent failure")):
        with pytest.raises(RetriesExceeded):
            await backend.generate_tests("file.js", "output", {"retry_count": 0})

# --- Tests for DiffblueBackend ---

def test_diffblue_backend_init_success(mock_config, temp_project_root):
    """Test successful initialization of DiffblueBackend."""
    backend = DiffblueBackend(mock_config, temp_project_root)
    assert backend.project_root == os.path.abspath(temp_project_root)
    assert "diffblue" in backend.config["backend_timeouts"]

def test_diffblue_backend_init_missing_config_key(mock_config, temp_project_root):
    """Test initialization fails if required config key is missing."""
    del mock_config["backend_timeouts"]
    with pytest.raises(ValueError, match="Missing required config key: backend_timeouts"):
        DiffblueBackend(mock_config, temp_project_root)

@pytest.mark.asyncio
async def test_diffblue_backend_generate_success(mock_config, temp_project_root, monkeypatch):
    """Test successful test generation with DiffblueBackend (simulated)."""
    backend = DiffblueBackend(mock_config, temp_project_root)
    # Fix: Mock random.random() to ensure a deterministic success path
    monkeypatch.setattr(random, "random", lambda: 0.2)
    
    success, err, path = await backend.generate_tests("ClassName", "output", {"retry_count": 0})
    assert success
    assert path.endswith("ATCOTest.java")
    full_path = os.path.join(temp_project_root, path)
    assert os.path.exists(full_path)
    with open(full_path, "r") as f:
        content = f.read()
        assert "Generated by ATCO" in content

@pytest.mark.asyncio
async def test_diffblue_backend_generate_simulated_failure(mock_config, temp_project_root, monkeypatch):
    """Test simulated failure in DiffblueBackend."""
    backend = DiffblueBackend(mock_config, temp_project_root)
    monkeypatch.setattr(random, "random", lambda: 0.0)  # Trigger failure
    
    success, err, path = await backend.generate_tests("ClassName", "output", {"retry_count": 0})
    assert not success
    assert "Simulated Diffblue Cover generation error" in err
    assert path is None

@pytest.mark.asyncio
async def test_diffblue_backend_generate_timeout(mock_config, temp_project_root):
    """Test Diffblue generation timeout handling."""
    backend = DiffblueBackend(mock_config, temp_project_root)
    
    with patch("asyncio.sleep", side_effect=asyncio.TimeoutError):
        success, err, path = await backend.generate_tests("ClassName", "output", {"timeout": 180})
        assert not success
        assert "timed out" in err
        assert path is None

# --- End-to-End Registry Test ---

def test_registry_with_all_backends():
    """Test registry with all backends registered."""
    registry = BackendRegistry()
    assert registry.get_backend("python") == PynguinBackend
    assert registry.get_backend("javascript") == JestLLMBackend
    assert registry.get_backend("typescript") == JestLLMBackend
    assert registry.get_backend("java") == DiffblueBackend
    # Fix: Updated the assertion to include rust and go backends
    assert set(registry.list_backends()) == {"python", "javascript", "typescript", "java", "rust", "go"}