"""
Simple test for ArbiterArena that avoids Pydantic validation issues.
"""

import shutil
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def temp_dir():
    """Create a temporary directory for tests."""
    path = tempfile.mkdtemp()
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def mock_config(temp_dir):
    """Create a mock config with all required fields."""
    config = MagicMock()
    config.DB_PATH = "sqlite:///test.db"
    config.ARENA_PORT = 8000
    config.ARENA_JWT_SECRET = MagicMock()
    config.ARENA_JWT_SECRET.get_secret_value.return_value = "test-secret"
    config.REPORTS_DIRECTORY = temp_dir
    config.CODEBASE_PATHS = ["./src"]
    config.EMAIL_ENABLED = False
    config.WORLD_SIZE = 2
    config.PERIODIC_SCAN_INTERVAL_S = 3600

    # Add DATABASE_URL that Arbiter expects
    config.DATABASE_URL = "postgresql://user:pass@localhost/testdb"

    # Email config - set to None to avoid Pydantic issues
    config.EMAIL_SMTP_SERVER = None
    config.EMAIL_SMTP_PORT = None
    config.EMAIL_SMTP_USERNAME = None
    config.EMAIL_SMTP_PASSWORD = None
    config.EMAIL_SENDER = None
    config.EMAIL_USE_TLS = False
    config.EMAIL_RECIPIENTS = {}
    config.SLACK_WEBHOOK_URL = None
    config.WEBHOOK_URL = None
    return config


@pytest.fixture
def mock_db_engine():
    """Create a mock database engine."""
    engine = MagicMock()
    engine.dispose = AsyncMock()
    return engine


class TestArbiterArena:
    """Test the ArbiterArena class without dealing with complex dependencies."""

    def test_arena_initialization(self, mock_config, mock_db_engine):
        """Test that arena can be initialized with mocked dependencies."""
        # Patch the human_loop module since it's imported lazily in __init__
        with patch("self_fixing_engineer.arbiter.human_loop.HumanInLoopConfig") as mock_hitl_config:
            mock_hitl_config.return_value = MagicMock()

            # Mock HumanInLoop class
            with patch("self_fixing_engineer.arbiter.human_loop.HumanInLoop") as mock_hitl:
                mock_hitl.return_value = MagicMock()

                # Mock the Arbiter class to avoid its initialization issues
                with patch("self_fixing_engineer.arbiter.arena.Arbiter") as mock_arbiter_class:
                    mock_arbiter_instance = MagicMock()
                    mock_arbiter_instance.name = "MockArbiter"
                    mock_arbiter_class.return_value = mock_arbiter_instance

                    # Import here after patching
                    from self_fixing_engineer.arbiter.arena import ArbiterArena

                    # Create arena
                    arena = ArbiterArena(
                        settings=mock_config,
                        name="TestArena",
                        db_engine=mock_db_engine,
                        port=9000,
                    )

                    # Basic assertions
                    assert arena.name == "TestArena"
                    assert arena.version == "1.1.0"
                    assert arena.settings == mock_config
                    assert arena._db_engine == mock_db_engine
                    assert arena.base_port == 9000
                    assert arena.http_port == 8000

                    # Verify Arbiter was created with correct parameters
                    assert mock_arbiter_class.call_count == mock_config.WORLD_SIZE

    @pytest.mark.asyncio
    async def test_arena_context_manager(self, mock_config, mock_db_engine):
        """Test arena works as an async context manager."""
        with patch("self_fixing_engineer.arbiter.human_loop.HumanInLoopConfig"):
            with patch("self_fixing_engineer.arbiter.human_loop.HumanInLoop"):
                with patch("self_fixing_engineer.arbiter.arena.Arbiter"):
                    from self_fixing_engineer.arbiter.arena import ArbiterArena

                    arena = ArbiterArena(settings=mock_config, db_engine=mock_db_engine)

                    # Mock the start and stop methods
                    with patch.object(
                        arena, "start_arena_services", new_callable=AsyncMock
                    ):
                        with patch.object(arena, "stop_all", new_callable=AsyncMock):
                            async with arena as a:
                                assert a == arena

                            arena.stop_all.assert_called_once()
                            mock_db_engine.dispose.assert_called_once()

    @pytest.mark.asyncio
    async def test_register_and_remove_arbiter(self, mock_config, mock_db_engine):
        """Test registering and removing arbiters from arena."""
        with patch("self_fixing_engineer.arbiter.human_loop.HumanInLoopConfig"):
            with patch("self_fixing_engineer.arbiter.human_loop.HumanInLoop"):
                with patch("self_fixing_engineer.arbiter.arena.Arbiter"):
                    from self_fixing_engineer.arbiter.arena import ArbiterArena

                    arena = ArbiterArena(settings=mock_config, db_engine=mock_db_engine)

                    # Create a mock arbiter
                    mock_arbiter = MagicMock()
                    mock_arbiter.name = "TestArbiter"

                    # Test registration
                    await arena.register(mock_arbiter)
                    assert mock_arbiter in arena.arbiters

                    # Test removal
                    await arena.remove(mock_arbiter)
                    assert mock_arbiter not in arena.arbiters

    @pytest.mark.asyncio
    async def test_get_random_arbiter(self, mock_config, mock_db_engine):
        """Test getting a random arbiter from the arena."""
        with patch("self_fixing_engineer.arbiter.human_loop.HumanInLoopConfig"):
            with patch("self_fixing_engineer.arbiter.human_loop.HumanInLoop"):
                with patch("self_fixing_engineer.arbiter.arena.Arbiter"):
                    from self_fixing_engineer.arbiter.arena import ArbiterArena

                    arena = ArbiterArena(settings=mock_config, db_engine=mock_db_engine)

                    # Test with no arbiters
                    arena.arbiters.clear()
                    with pytest.raises(ValueError, match="No arbiters available"):
                        await arena.get_random_arbiter()

                    # Add an arbiter and test
                    mock_arbiter = MagicMock()
                    mock_arbiter.name = "TestArbiter"
                    arena.arbiters.append(mock_arbiter)

                    result = await arena.get_random_arbiter()
                    assert result == mock_arbiter

    @pytest.mark.asyncio
    async def test_webhook_sending(self, mock_config, mock_db_engine):
        """Test that webhooks are sent correctly."""
        mock_config.WEBHOOK_URL = "http://example.com/webhook"

        with patch("self_fixing_engineer.arbiter.human_loop.HumanInLoopConfig"):
            with patch("self_fixing_engineer.arbiter.human_loop.HumanInLoop"):
                with patch("self_fixing_engineer.arbiter.arena.Arbiter"):
                    from self_fixing_engineer.arbiter.arena import ArbiterArena

                    arena = ArbiterArena(settings=mock_config, db_engine=mock_db_engine)

                    # Mock aiohttp session - patch in the module where it's used
                    with patch("self_fixing_engineer.arbiter.arena.aiohttp.ClientSession") as mock_session:
                        mock_response = AsyncMock()
                        mock_response.status = 200
                        mock_session.return_value.__aenter__.return_value.post.return_value.__aenter__.return_value = (
                            mock_response
                        )

                        await arena._send_webhook("test_event", {"key": "value"})

                        # Verify the webhook was called
                        mock_session.return_value.__aenter__.return_value.post.assert_called_once()

    def test_setup_routes(self, mock_config, mock_db_engine):
        """Test that FastAPI routes are set up correctly."""
        with patch("self_fixing_engineer.arbiter.human_loop.HumanInLoopConfig"):
            with patch("self_fixing_engineer.arbiter.human_loop.HumanInLoop"):
                with patch("self_fixing_engineer.arbiter.arena.Arbiter"):
                    from self_fixing_engineer.arbiter.arena import ArbiterArena

                    arena = ArbiterArena(settings=mock_config, db_engine=mock_db_engine)
                    arena._setup_routes()

                    # Check that basic routes exist
                    routes = [route.path for route in arena.app.routes]
                    assert "/health" in routes
                    assert "/version" in routes
                    assert "/status" in routes
                    assert "/arbiters" in routes


class TestExtractSqliteDbFile:
    """Test the _extract_sqlite_db_file helper function."""

    def test_relative_path_with_dot_slash(self):
        """Test extracting path from relative URL with ./"""
        from self_fixing_engineer.arbiter.arena import _extract_sqlite_db_file

        result = _extract_sqlite_db_file("sqlite:///./omnicore.db")
        assert result == "./omnicore.db"

    def test_relative_path_without_dot_slash(self):
        """Test extracting path from relative URL without ./"""
        from self_fixing_engineer.arbiter.arena import _extract_sqlite_db_file

        result = _extract_sqlite_db_file("sqlite:///omnicore.db")
        assert result == "omnicore.db"

    def test_absolute_path(self):
        """Test extracting path from absolute URL."""
        from self_fixing_engineer.arbiter.arena import _extract_sqlite_db_file

        result = _extract_sqlite_db_file("sqlite:////tmp/omnicore.db")
        assert result == "/tmp/omnicore.db"

    def test_absolute_path_nested(self):
        """Test extracting nested absolute path."""
        from self_fixing_engineer.arbiter.arena import _extract_sqlite_db_file

        result = _extract_sqlite_db_file("sqlite:////var/data/db/omnicore.db")
        assert result == "/var/data/db/omnicore.db"

    def test_sqlite_aiosqlite_dialect(self):
        """Test extracting path from sqlite+aiosqlite URL."""
        from self_fixing_engineer.arbiter.arena import _extract_sqlite_db_file

        result = _extract_sqlite_db_file("sqlite+aiosqlite:///./test.db")
        assert result == "./test.db"

    def test_non_sqlite_url_unchanged(self):
        """Test that non-SQLite URLs are returned unchanged."""
        from self_fixing_engineer.arbiter.arena import _extract_sqlite_db_file

        result = _extract_sqlite_db_file("postgresql://user:pass@localhost/db")
        assert result == "postgresql://user:pass@localhost/db"

    def test_mysql_url_unchanged(self):
        """Test that MySQL URLs are returned unchanged."""
        from self_fixing_engineer.arbiter.arena import _extract_sqlite_db_file

        result = _extract_sqlite_db_file("mysql://user:pass@localhost/db")
        assert result == "mysql://user:pass@localhost/db"

    def test_relative_path_in_subdirectory(self):
        """Test extracting relative path in a subdirectory."""
        from self_fixing_engineer.arbiter.arena import _extract_sqlite_db_file

        result = _extract_sqlite_db_file("sqlite:///./data/omnicore.db")
        assert result == "./data/omnicore.db"

    def test_simple_filename(self):
        """Test extracting a simple filename without path."""
        from self_fixing_engineer.arbiter.arena import _extract_sqlite_db_file

        result = _extract_sqlite_db_file("sqlite:///mydb.db")
        assert result == "mydb.db"
