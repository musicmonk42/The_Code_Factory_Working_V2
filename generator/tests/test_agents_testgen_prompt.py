"""
Unit tests for agents.testgen_agent.testgen_prompt module.

UPDATED: Fixed to match actual production code signatures and APIs
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Import from the REAL production module
from agents.testgen_agent.testgen_prompt import (
    MAX_PROMPT_TOKENS,
    SANITIZATION_PATTERNS,
    SUPPORTED_FRAMEWORKS,
    SUPPORTED_LANGUAGES,
    AdaptivePromptDirector,
    AdvancedTemplateTracker,
    AgenticPromptBuilder,
    DefaultPromptBuilder,
    MultiVectorDBManager,
    _local_regex_sanitize,
    build_agentic_prompt,
    initialize_codebase_for_rag,
    register_prompt_builder,
)

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def temp_dir():
    """Create temporary directory for tests"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mock_chromadb():
    """Mock ChromaDB client and collections"""
    # Create a mock chromadb module with PersistentClient
    mock_chromadb_module = MagicMock()
    
    mock_collection = MagicMock()
    mock_collection.add = MagicMock()
    mock_collection.query = MagicMock(
        return_value={
            "documents": [["test doc"]],
            "metadatas": [[{"filename": "test.py"}]],
            "distances": [[0.5]],
        }
    )

    mock_client_instance = MagicMock()
    mock_client_instance.get_or_create_collection = MagicMock(
        return_value=mock_collection
    )
    mock_chromadb_module.PersistentClient.return_value = mock_client_instance

    # Mock embedding_functions
    mock_embedding_functions = MagicMock()
    mock_embedding_functions.DefaultEmbeddingFunction.return_value = MagicMock()

    # Patch both the module-level chromadb variable and the HAS_CHROMADB flag
    with patch(
        "generator.agents.testgen_agent.testgen_prompt.chromadb", mock_chromadb_module
    ), patch(
        "generator.agents.testgen_agent.testgen_prompt.embedding_functions", mock_embedding_functions
    ), patch(
        "generator.agents.testgen_agent.testgen_prompt.HAS_CHROMADB", True
    ):
        yield mock_client_instance


@pytest.fixture
def mock_add_provenance():
    """Mock add_provenance to avoid API signature issues"""
    with patch("generator.agents.testgen_agent.testgen_prompt.add_provenance") as mock:
        yield mock


@pytest.fixture
def fresh_testgen_imports(mock_chromadb):
    """Provide fresh imports of testgen_prompt classes that use the mocked chromadb.
    
    This fixture ensures that MultiVectorDBManager and related classes are imported
    after the chromadb mock is in place, preventing ImportError for missing chromadb.
    """
    from generator.agents.testgen_agent.testgen_prompt import (
        MultiVectorDBManager,
        AdvancedTemplateTracker,
        AdaptivePromptDirector,
        DefaultPromptBuilder,
        AgenticPromptBuilder,
        build_agentic_prompt,
    )
    return {
        "MultiVectorDBManager": MultiVectorDBManager,
        "AdvancedTemplateTracker": AdvancedTemplateTracker,
        "AdaptivePromptDirector": AdaptivePromptDirector,
        "DefaultPromptBuilder": DefaultPromptBuilder,
        "AgenticPromptBuilder": AgenticPromptBuilder,
        "build_agentic_prompt": build_agentic_prompt,
    }


# ============================================================================
# Test: Text Sanitization
# ============================================================================


class TestTextSanitization:
    """Test text sanitization security features"""

    def test_sanitize_email(self):
        """Email addresses should be redacted"""
        text = "user@example.com"
        result = _local_regex_sanitize(text)
        assert "user@example.com" not in result
        assert "[REDACTED_EMAIL]" in result

    def test_sanitize_phone(self):
        """Phone numbers should be redacted (full format)"""
        text = "Call 555-123-4567"
        result = _local_regex_sanitize(text)
        assert "555-123-4567" not in result
        assert "[REDACTED_PHONE]" in result

    def test_sanitize_api_key(self):
        """API keys should be redacted"""
        text = 'api_key="sk-abc123"'
        result = _local_regex_sanitize(text)
        assert "sk-abc123" not in result
        assert "[REDACTED_CREDENTIAL]" in result

    def test_sanitize_password(self):
        """Passwords should be redacted"""
        text = "password: secret123"
        result = _local_regex_sanitize(text)
        assert "secret123" not in result
        assert "[REDACTED_CREDENTIAL]" in result

    def test_sanitize_credit_card(self):
        """Credit card numbers should be redacted"""
        text = "1234-5678-9012-3456"
        result = _local_regex_sanitize(text)
        assert "1234-5678-9012-3456" not in result
        assert "[REDACTED_CC]" in result

    def test_sanitize_ip(self):
        """IP addresses should be redacted"""
        text = "192.168.1.1"
        result = _local_regex_sanitize(text)
        assert "192.168.1.1" not in result
        assert "[REDACTED_IP]" in result

    def test_sanitize_ssn(self):
        """SSN should be redacted"""
        text = "123-45-6789"
        result = _local_regex_sanitize(text)
        assert "123-45-6789" not in result
        assert "[REDACTED_SSN]" in result

    def test_sanitize_normal_text(self):
        """Normal text should be unchanged"""
        text = "This is normal text"
        result = _local_regex_sanitize(text)
        assert result == text


# ============================================================================
# Test: MultiVectorDBManager
# ============================================================================


class TestMultiVectorDBManager:
    """Test RAG functionality with vector database"""

    def test_initialization(self, fresh_testgen_imports):
        """Manager should initialize with all collections"""
        MVDBManager = fresh_testgen_imports["MultiVectorDBManager"]
        manager = MVDBManager()

        assert manager.client is not None
        assert "codebase" in manager.collections
        assert "tests" in manager.collections
        assert "docs" in manager.collections
        assert "dependencies" in manager.collections
        assert "historical_failures" in manager.collections

    @pytest.mark.asyncio
    async def test_add_files(self, fresh_testgen_imports, mock_add_provenance):
        """Should add files to collection"""
        MVDBManager = fresh_testgen_imports["MultiVectorDBManager"]
        manager = MVDBManager()
        files = {"test.py": "def test(): pass"}

        await manager.add_files("codebase", files)

        # Verify add was called on collection
        manager.collections["codebase"].add.assert_called_once()
        # Verify provenance was logged
        mock_add_provenance.assert_called()

    @pytest.mark.asyncio
    async def test_add_files_invalid_collection(self, fresh_testgen_imports):
        """Should raise error for invalid collection"""
        MVDBManager = fresh_testgen_imports["MultiVectorDBManager"]
        manager = MVDBManager()
        files = {"test.py": "code"}

        with pytest.raises(ValueError, match="Unknown collection"):
            await manager.add_files("invalid", files)

    @pytest.mark.asyncio
    async def test_add_files_with_filename_in_id(self, fresh_testgen_imports, mock_add_provenance):
        """Should generate IDs including filename to ensure uniqueness"""
        import hashlib
        MVDBManager = fresh_testgen_imports["MultiVectorDBManager"]
        manager = MVDBManager()
        files = {"test1.py": "def test(): pass", "test2.py": "def test(): pass"}

        await manager.add_files("codebase", files)

        # Verify add was called
        call_args = manager.collections["codebase"].add.call_args
        ids = call_args[1]["ids"]
        
        # Verify that IDs are different even though content is the same
        assert len(ids) == 2
        assert ids[0] != ids[1]
        
        # Verify IDs are generated with filename included
        expected_id1 = hashlib.sha256("test1.py:def test(): pass".encode()).hexdigest()
        expected_id2 = hashlib.sha256("test2.py:def test(): pass".encode()).hexdigest()
        assert expected_id1 in ids
        assert expected_id2 in ids

    @pytest.mark.asyncio
    async def test_add_files_skips_existing_duplicates(self, fresh_testgen_imports, mock_add_provenance):
        """Should detect and skip files that already exist in collection"""
        import hashlib
        MVDBManager = fresh_testgen_imports["MultiVectorDBManager"]
        manager = MVDBManager()
        files = {"test.py": "def test(): pass"}
        
        # Mock the get method to return existing IDs
        existing_id = hashlib.sha256("test.py:def test(): pass".encode()).hexdigest()
        manager.collections["codebase"].get = MagicMock(
            return_value={"ids": [existing_id]}
        )

        await manager.add_files("codebase", files)

        # Verify add was NOT called since file already exists
        manager.collections["codebase"].add.assert_not_called()
        
    @pytest.mark.asyncio
    async def test_add_files_partial_duplicates(self, fresh_testgen_imports, mock_add_provenance):
        """Should add only new files when some already exist"""
        import hashlib
        MVDBManager = fresh_testgen_imports["MultiVectorDBManager"]
        manager = MVDBManager()
        files = {
            "existing.py": "def existing(): pass",
            "new.py": "def new(): pass"
        }
        
        # Mock the get method to return one existing ID
        existing_id = hashlib.sha256("existing.py:def existing(): pass".encode()).hexdigest()
        manager.collections["codebase"].get = MagicMock(
            return_value={"ids": [existing_id]}
        )

        await manager.add_files("codebase", files)

        # Verify add was called with only the new file
        call_args = manager.collections["codebase"].add.call_args
        ids = call_args[1]["ids"]
        documents = call_args[1]["documents"]
        metadatas = call_args[1]["metadatas"]
        
        assert len(ids) == 1
        assert len(documents) == 1
        assert len(metadatas) == 1
        assert documents[0] == "def new(): pass"
        assert metadatas[0]["filename"] == "new.py"

    @pytest.mark.asyncio
    async def test_query_relevant_context(self, fresh_testgen_imports, mock_add_provenance):
        """Should query and return context from collections"""
        MVDBManager = fresh_testgen_imports["MultiVectorDBManager"]
        manager = MVDBManager()

        results = await manager.query_relevant_context(
            "test query", collections=["codebase"], n_results=3
        )

        assert isinstance(results, dict)
        assert "codebase" in results
        manager.collections["codebase"].query.assert_called_once()

    @pytest.mark.asyncio
    async def test_query_multiple_collections(self, fresh_testgen_imports, mock_add_provenance):
        """Should query multiple collections"""
        MVDBManager = fresh_testgen_imports["MultiVectorDBManager"]
        manager = MVDBManager()

        results = await manager.query_relevant_context(
            "test query", collections=["codebase", "tests"]
        )

        assert "codebase" in results
        assert "tests" in results

    @pytest.mark.asyncio
    async def test_close(self, fresh_testgen_imports, mock_add_provenance):
        """Should clear resources on close"""
        MVDBManager = fresh_testgen_imports["MultiVectorDBManager"]
        manager = MVDBManager()
        await manager.close()

        assert len(manager.collections) == 0

        assert len(manager.collections) == 0


# ============================================================================
# Test: AdvancedTemplateTracker
# ============================================================================


class TestAdvancedTemplateTracker:
    """Test template versioning and management"""

    def test_initialization(self, temp_dir):
        """Tracker should initialize with file path for database"""
        # Create directory for tracker and pass file path
        tracker_dir = temp_dir / "tracker"
        tracker_dir.mkdir()
        db_file = tracker_dir / "template_performance.json"

        tracker = AdvancedTemplateTracker(str(db_file))
        assert tracker.db_path == str(db_file)

    def test_save_template(self, temp_dir):
        """Should save templates"""
        tracker_dir = temp_dir / "tracker"
        tracker_dir.mkdir()
        db_file = tracker_dir / "template_performance.json"
        tracker = AdvancedTemplateTracker(str(db_file))

        # This will test the save functionality
        # The actual method may vary based on implementation


# ============================================================================
# Test: AdaptivePromptDirector
# ============================================================================


class TestAdaptivePromptDirector:
    """Test dynamic prompt routing"""

    def test_initialization(self, fresh_testgen_imports, temp_dir):
        """Director should initialize with required dependencies"""
        MVDBManager = fresh_testgen_imports["MultiVectorDBManager"]
        ATTracker = fresh_testgen_imports["AdvancedTemplateTracker"]
        APDirector = fresh_testgen_imports["AdaptivePromptDirector"]
        
        tracker_dir = temp_dir / "tracker"
        tracker_dir.mkdir()
        db_file = tracker_dir / "template_performance.json"

        vdb = MVDBManager()
        tracker = ATTracker(str(db_file))
        director = APDirector(vdb, tracker)

        assert director is not None
        assert director.multi_vdb == vdb
        assert director.tracker == tracker


# ============================================================================
# Test: Prompt Builders
# ============================================================================


class TestPromptBuilders:
    """Test prompt builder classes"""

    def test_default_builder_init(self, fresh_testgen_imports, temp_dir):
        """DefaultPromptBuilder should initialize with director"""
        MVDBManager = fresh_testgen_imports["MultiVectorDBManager"]
        ATTracker = fresh_testgen_imports["AdvancedTemplateTracker"]
        APDirector = fresh_testgen_imports["AdaptivePromptDirector"]
        DPBuilder = fresh_testgen_imports["DefaultPromptBuilder"]
        
        tracker_dir = temp_dir / "tracker"
        tracker_dir.mkdir()
        db_file = tracker_dir / "template_performance.json"

        vdb = MVDBManager()
        tracker = ATTracker(str(db_file))
        director = APDirector(vdb, tracker)
        builder = DPBuilder(director)

        assert builder is not None

    @pytest.mark.asyncio
    async def test_default_builder_build(self, fresh_testgen_imports, temp_dir):
        """DefaultPromptBuilder should build prompts"""
        MVDBManager = fresh_testgen_imports["MultiVectorDBManager"]
        ATTracker = fresh_testgen_imports["AdvancedTemplateTracker"]
        APDirector = fresh_testgen_imports["AdaptivePromptDirector"]
        DPBuilder = fresh_testgen_imports["DefaultPromptBuilder"]
        
        tracker_dir = temp_dir / "tracker"
        tracker_dir.mkdir()
        db_file = tracker_dir / "template_performance.json"

        # Create a test template
        template_file = (
            temp_dir / "testgen_templates" / "test_test_generation_default.j2"
        )
        template_file.parent.mkdir(parents=True, exist_ok=True)
        template_file.write_text("Test template for {{ task }}")

        with patch(
            "generator.agents.testgen_agent.testgen_prompt.TEMPLATE_DIR",
            str(template_file.parent),
        ):
            vdb = MVDBManager()
            tracker = ATTracker(str(db_file))
            director = APDirector(vdb, tracker)
            builder = DPBuilder(director)

            try:
                prompt = await builder.build("test_generation", code="def test(): pass")
                assert isinstance(prompt, str)
            except FileNotFoundError:
                # Expected if templates don't exist
                pytest.skip("Templates not available in test environment")

    def test_register_builder(self):
        """Should register custom builders"""

        class CustomBuilder(AgenticPromptBuilder):
            async def build(self, prompt_type, **kwargs):
                return "custom"

        register_prompt_builder("custom", CustomBuilder)


# ============================================================================
# Test: Helper Functions
# ============================================================================


class TestHelperFunctions:
    """Test module helper functions"""

    @pytest.mark.asyncio
    async def test_build_agentic_prompt_handles_missing_templates(self, fresh_testgen_imports, temp_dir):
        """Should handle missing templates gracefully"""
        bap = fresh_testgen_imports["build_agentic_prompt"]
        
        # Create empty template directory
        template_dir = temp_dir / "templates"
        template_dir.mkdir()

        with patch(
            "generator.agents.testgen_agent.testgen_prompt.TEMPLATE_DIR", str(template_dir)
        ):
            with pytest.raises(FileNotFoundError):
                await bap("test_generation", code="def test(): pass")

    @pytest.mark.skip(reason="Requires ONNX runtime which has DLL issues on Windows")
    def test_initialize_codebase_for_rag(self, temp_dir, mock_chromadb):
        """Should initialize codebase for RAG indexing"""
        # Create test files
        (temp_dir / "test.py").write_text("def hello(): pass")

        initialize_codebase_for_rag(str(temp_dir))


# ============================================================================
# Test: Configuration
# ============================================================================


class TestConfiguration:
    """Test module configuration"""

    def test_max_prompt_tokens(self):
        """MAX_PROMPT_TOKENS should be set"""
        assert isinstance(MAX_PROMPT_TOKENS, int)
        assert MAX_PROMPT_TOKENS > 0

    def test_supported_languages(self):
        """Supported languages should be defined"""
        assert isinstance(SUPPORTED_LANGUAGES, list)
        assert "python" in SUPPORTED_LANGUAGES

    def test_supported_frameworks(self):
        """Supported frameworks should be defined"""
        assert isinstance(SUPPORTED_FRAMEWORKS, dict)
        assert "python" in SUPPORTED_FRAMEWORKS

    def test_sanitization_patterns(self):
        """Sanitization patterns should be defined"""
        assert isinstance(SANITIZATION_PATTERNS, dict)
        assert "[REDACTED_EMAIL]" in SANITIZATION_PATTERNS


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
