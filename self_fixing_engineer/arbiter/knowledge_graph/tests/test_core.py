import sys
import types
from unittest.mock import MagicMock, Mock, AsyncMock, patch, mock_open


# Create proper module mocks with all necessary attributes BEFORE any imports
def setup_langchain_mocks():
    """Setup all langchain module mocks with proper structure"""

    # Create langchain.memory module with all needed components
    memory_module = types.ModuleType("langchain.memory")
    memory_module.ConversationBufferWindowMemory = MagicMock()

    # Create langchain.memory.prompt submodule
    memory_prompt_module = types.ModuleType("langchain.memory.prompt")
    memory_prompt_module.SUMMARY_PROMPT = MagicMock()
    memory_prompt_module._DEFAULT_SUMMARIZER_TEMPLATE = MagicMock()
    memory_module.prompt = memory_prompt_module

    # Register the modules
    sys.modules["langchain.memory"] = memory_module
    sys.modules["langchain.memory.prompt"] = memory_prompt_module

    # Create langchain.schema modules
    schema_module = types.ModuleType("langchain.schema")
    messages_module = types.ModuleType("langchain.schema.messages")
    messages_module.messages_to_dict = MagicMock(return_value=[])
    schema_module.messages = messages_module

    sys.modules["langchain.schema"] = schema_module
    sys.modules["langchain.schema.messages"] = messages_module

    # Create langchain.chains modules
    chains_module = types.ModuleType("langchain.chains")
    chains_module.ConversationChain = MagicMock()

    conversation_module = types.ModuleType("langchain.chains.conversation")
    conversation_base_module = types.ModuleType("langchain.chains.conversation.base")
    conversation_prompt_module = types.ModuleType(
        "langchain.chains.conversation.prompt"
    )
    conversation_prompt_module.PROMPT = MagicMock()

    conversation_module.base = conversation_base_module
    conversation_module.prompt = conversation_prompt_module
    chains_module.conversation = conversation_module

    sys.modules["langchain.chains"] = chains_module
    sys.modules["langchain.chains.conversation"] = conversation_module
    sys.modules["langchain.chains.conversation.base"] = conversation_base_module
    sys.modules["langchain.chains.conversation.prompt"] = conversation_prompt_module

    # Create proper type mocks for LLM classes
    openai_module = types.ModuleType("langchain_openai")
    # Create a proper class type for ChatOpenAI
    ChatOpenAI = type("ChatOpenAI", (), {})
    openai_module.ChatOpenAI = ChatOpenAI
    sys.modules["langchain_openai"] = openai_module

    anthropic_module = types.ModuleType("langchain_anthropic")
    ChatAnthropic = type("ChatAnthropic", (), {})
    anthropic_module.ChatAnthropic = ChatAnthropic
    sys.modules["langchain_anthropic"] = anthropic_module

    google_module = types.ModuleType("langchain_google_genai")
    ChatGoogleGenerativeAI = type("ChatGoogleGenerativeAI", (), {})
    google_module.ChatGoogleGenerativeAI = ChatGoogleGenerativeAI
    sys.modules["langchain_google_genai"] = google_module


# Setup langchain mocks FIRST
setup_langchain_mocks()

# Mock other problematic modules
sys.modules["arbiter.models"] = MagicMock()
sys.modules["arbiter.models.redis_client"] = MagicMock()
sys.modules["arbiter.models.audit_ledger_client"] = MagicMock()
sys.modules["redis"] = MagicMock()
sys.modules["redis.asyncio"] = MagicMock()
sys.modules["asyncpg"] = MagicMock()

# Now we can safely import pytest and other testing modules
import pytest
import asyncio
import json
import pickle

# Now import the module components to test - this should work because all mocks are in place
from arbiter.knowledge_graph.core import (
    StateBackend,
    RedisStateBackend,
    PostgresStateBackend,
    InMemoryStateBackend,
    MetaLearning,
    CollaborativeAgent,
    AgentTeam,
    get_or_create_agent,
    setup_conversation,
    get_transcript,
    AgentCoreException,
    AgentErrorCode,
)


# Global fixture to prevent all external connections
@pytest.fixture(autouse=True)
def mock_all_external_services():
    """Automatically mock all external services for all tests"""
    with patch("arbiter.knowledge_graph.core.ChatOpenAI") as mock_openai:
        with patch("arbiter.knowledge_graph.core.RedisClient") as mock_redis:
            with patch("arbiter.knowledge_graph.core.PostgresClient") as mock_postgres:
                with patch(
                    "arbiter.knowledge_graph.core.AuditLedgerClient"
                ) as mock_audit:
                    with patch(
                        "arbiter.knowledge_graph.core.DefaultMultiModalProcessor"
                    ) as mock_mm:
                        with patch(
                            "arbiter.knowledge_graph.core.DefaultPromptStrategy"
                        ) as mock_prompt:
                            with patch(
                                "arbiter.knowledge_graph.core.load_persona_dict",
                                return_value={
                                    "default": "Test persona",
                                    "expert": "Expert persona",
                                },
                            ):
                                with patch(
                                    "arbiter.knowledge_graph.core.AGENT_METRICS",
                                    {
                                        "meta_learning_corrections_logged_total": MagicMock(
                                            inc=MagicMock()
                                        ),
                                        "meta_learning_train_duration_seconds": MagicMock(
                                            observe=MagicMock()
                                        ),
                                        "meta_learning_train_errors_total": MagicMock(
                                            inc=MagicMock()
                                        ),
                                        "agent_team_task_duration_seconds": MagicMock(
                                            observe=MagicMock()
                                        ),
                                        "agent_team_task_errors_total": MagicMock(
                                            labels=MagicMock(
                                                return_value=MagicMock(inc=MagicMock())
                                            )
                                        ),
                                        "agent_creation_duration_seconds": MagicMock(
                                            observe=MagicMock()
                                        ),
                                        "state_backend_operations_total": MagicMock(
                                            labels=MagicMock(
                                                return_value=MagicMock(inc=MagicMock())
                                            )
                                        ),
                                        "state_backend_latency_seconds": MagicMock(
                                            labels=MagicMock(
                                                return_value=MagicMock(
                                                    observe=MagicMock()
                                                )
                                            )
                                        ),
                                        "state_backend_errors_total": MagicMock(
                                            labels=MagicMock(
                                                return_value=MagicMock(inc=MagicMock())
                                            )
                                        ),
                                        "agent_predict_total": MagicMock(
                                            labels=MagicMock(
                                                return_value=MagicMock(inc=MagicMock())
                                            )
                                        ),
                                        "agent_active_sessions_current": MagicMock(
                                            inc=MagicMock(), dec=MagicMock()
                                        ),
                                        "agent_predict_errors": MagicMock(
                                            labels=MagicMock(
                                                return_value=MagicMock(inc=MagicMock())
                                            )
                                        ),
                                        "agent_last_error_timestamp": MagicMock(
                                            labels=MagicMock(
                                                return_value=MagicMock(set=MagicMock())
                                            )
                                        ),
                                        "agent_heartbeat_timestamp": MagicMock(
                                            labels=MagicMock(
                                                return_value=MagicMock(set=MagicMock())
                                            )
                                        ),
                                    },
                                ):
                                    # Configure mocks to return proper instances
                                    mock_openai.return_value = MagicMock()
                                    mock_redis.return_value = AsyncMock()
                                    mock_postgres.return_value = AsyncMock()
                                    mock_audit.return_value = AsyncMock()
                                    mock_mm.return_value = MagicMock()
                                    mock_prompt.return_value = MagicMock()

                                    yield {
                                        "openai": mock_openai,
                                        "redis": mock_redis,
                                        "postgres": mock_postgres,
                                        "audit": mock_audit,
                                        "mm": mock_mm,
                                        "prompt": mock_prompt,
                                    }


class TestStateBackends:
    """Test suite for StateBackend implementations"""

    @pytest.mark.asyncio
    async def test_inmemory_state_backend_save_and_load(self):
        """Test InMemoryStateBackend save and load operations"""
        backend = InMemoryStateBackend()
        session_id = "test_session_123"
        test_state = {
            "history": [{"role": "user", "content": "Hello"}],
            "persona": "helpful assistant",
            "language": "en",
        }

        # Save state
        await backend.save_state(session_id, test_state)

        # Load state
        loaded_state = await backend.load_state(session_id)
        assert loaded_state == test_state

        # Load non-existent state
        non_existent = await backend.load_state("non_existent_session")
        assert non_existent is None

    @pytest.mark.asyncio
    async def test_redis_state_backend_initialization(self, mock_all_external_services):
        """Test RedisStateBackend initialization and error handling"""
        # Test successful initialization
        backend = RedisStateBackend("redis://localhost:6379/0")
        assert backend.client is not None

        # Test init_client with successful ping
        backend.client.ping = AsyncMock(return_value=True)
        await backend.init_client()
        backend.client.ping.assert_called()

    @pytest.mark.asyncio
    async def test_redis_state_backend_save_state(self, mock_all_external_services):
        """Test RedisStateBackend save_state operation"""
        backend = RedisStateBackend("redis://localhost:6379/0")
        backend.client = AsyncMock()

        session_id = "test_session"
        test_state = {"history": [], "persona": "test", "language": "en"}

        backend.client.set = AsyncMock(return_value=True)

        await backend.save_state(session_id, test_state)

        backend.client.set.assert_called_once()
        call_args = backend.client.set.call_args[0]
        assert call_args[0] == f"agent_state:{session_id}"
        assert json.loads(call_args[1]) == test_state

    @pytest.mark.asyncio
    async def test_redis_state_backend_load_state(self, mock_all_external_services):
        """Test RedisStateBackend load_state operation"""
        backend = RedisStateBackend("redis://localhost:6379/0")
        backend.client = AsyncMock()

        session_id = "test_session"
        test_state = {"history": [], "persona": "test", "language": "en"}

        backend.client.get = AsyncMock(return_value=json.dumps(test_state))

        loaded_state = await backend.load_state(session_id)

        assert loaded_state == test_state
        backend.client.get.assert_called_once_with(f"agent_state:{session_id}")

    @pytest.mark.asyncio
    async def test_postgres_state_backend_initialization(
        self, mock_all_external_services
    ):
        """Test PostgresStateBackend initialization"""
        backend = PostgresStateBackend("postgresql://localhost/testdb")
        assert backend.client is not None

        # Test init_client
        backend.client.connect = AsyncMock()
        await backend.init_client()
        backend.client.connect.assert_called()


class TestMetaLearning:
    """Test suite for MetaLearning class"""

    def test_meta_learning_initialization(self):
        """Test MetaLearning initialization"""
        with patch("builtins.open", side_effect=FileNotFoundError):
            ml = MetaLearning()
            assert ml.corrections == []
            assert ml.model_pipeline is not None

    def test_log_correction(self):
        """Test logging corrections"""
        with patch("builtins.open", side_effect=FileNotFoundError):
            ml = MetaLearning()

        with patch(
            "arbiter.knowledge_graph.core.Config.MAX_META_LEARNING_CORRECTIONS", 5
        ):
            with patch(
                "arbiter.knowledge_graph.core.Config.MAX_CORRECTION_ENTRY_SIZE", 1000
            ):
                # Log corrections
                ml.log_correction("input1", "response1", "corrected1")
                ml.log_correction("input2", "response2", "corrected2")

                assert len(ml.corrections) == 2
                assert ml.corrections[0] == ("input1", "response1", "corrected1")

                # Test FIFO when max corrections reached
                for i in range(4):
                    ml.log_correction(
                        f"input{i+3}", f"response{i+3}", f"corrected{i+3}"
                    )

                assert len(ml.corrections) == 5
                assert ml.corrections[0][0] == "input2"  # First one should be removed

    def test_log_correction_size_limit(self):
        """Test correction size limit"""
        with patch("builtins.open", side_effect=FileNotFoundError):
            ml = MetaLearning()

        with patch("arbiter.knowledge_graph.core.Config.MAX_CORRECTION_ENTRY_SIZE", 10):
            # This should be skipped due to size
            ml.log_correction(
                "very long input", "very long response", "very long correction"
            )
            assert len(ml.corrections) == 0

    def test_train_model(self):
        """Test model training"""
        with patch("builtins.open", side_effect=FileNotFoundError):
            ml = MetaLearning()

        with patch("arbiter.knowledge_graph.core.Config.MIN_RECORDS_FOR_TRAINING", 2):
            # Add enough corrections to trigger training
            ml.corrections = [
                ("input1", "response1", "corrected1"),
                ("input2", "response2", "corrected2"),
            ]

            ml.train_model()
            # Model should be fitted after training
            assert ml.model_pipeline is not None

    def test_apply_correction_no_model(self):
        """Test apply_correction when model is not ready"""
        with patch("builtins.open", side_effect=FileNotFoundError):
            ml = MetaLearning()

        with patch("arbiter.knowledge_graph.core.Config.MIN_RECORDS_FOR_TRAINING", 10):
            # Not enough corrections for training
            ml.corrections = [("input1", "response1", "corrected1")]

            result = ml.apply_correction("test response", "test input")
            assert result == "test response"  # Should return original

    def test_persist_and_load(self, tmp_path):
        """Test persisting and loading meta-learning data"""
        with patch("builtins.open", side_effect=FileNotFoundError):
            ml = MetaLearning()
        ml.corrections = [("input1", "response1", "corrected1")]

        # Mock file path
        test_file = tmp_path / "meta_learning.pkl"

        with patch("builtins.open", mock_open()) as mock_file:
            with patch("pickle.dump") as mock_dump:
                ml.persist()
                mock_dump.assert_called_once()

        # Test loading
        with patch("builtins.open", side_effect=FileNotFoundError):
            ml2 = MetaLearning()
        test_data = {
            "corrections": [("input1", "response1", "corrected1")],
            "model": ml.model_pipeline,
        }

        with patch("builtins.open", mock_open(read_data=pickle.dumps(test_data))):
            ml2.load()
            assert ml2.corrections == test_data["corrections"]


class TestCollaborativeAgent:
    """Test suite for CollaborativeAgent class"""

    @pytest.fixture
    def mock_llm_config(self):
        """Fixture for LLM configuration"""
        return {
            "provider": "openai",
            "model": "gpt-3.5-turbo",
            "temperature": 0.7,
            "api_key": "test_api_key",
        }

    def test_agent_initialization(self, mock_llm_config, mock_all_external_services):
        """Test CollaborativeAgent initialization"""
        with patch("builtins.open", side_effect=FileNotFoundError):
            agent = CollaborativeAgent(
                agent_id="test_agent",
                session_id="test_session",
                llm_config=mock_llm_config,
            )

            assert agent.agent_id == "test_agent"
            assert agent.session_id == "test_session"
            assert agent.llm_config == mock_llm_config
            assert agent.persona == "Test persona"

    def test_agent_invalid_api_key(self, mock_all_external_services):
        """Test agent initialization with invalid API key"""
        invalid_config = {
            "provider": "openai",
            "model": "gpt-3.5-turbo",
            "api_key": "invalid_key",
        }

        # Mock the ChatOpenAI to raise an exception for invalid key
        mock_all_external_services["openai"].side_effect = Exception("Invalid API key")

        with patch("builtins.open", side_effect=FileNotFoundError):
            with pytest.raises(AgentCoreException) as exc_info:
                agent = CollaborativeAgent(
                    agent_id="test_agent",
                    session_id="test_session",
                    llm_config=invalid_config,
                )

            assert exc_info.value.code == AgentErrorCode.LLM_INIT_FAILED

    @pytest.mark.asyncio
    async def test_agent_load_state(self, mock_llm_config, mock_all_external_services):
        """Test loading agent state"""
        with patch("builtins.open", side_effect=FileNotFoundError):
            agent = CollaborativeAgent(
                agent_id="test_agent",
                session_id="test_session",
                llm_config=mock_llm_config,
            )

            test_state = {"history": [], "persona": "loaded_persona", "language": "fr"}

            agent.state_backend = AsyncMock()
            agent.state_backend.load_state = AsyncMock(return_value=test_state)
            agent.audit_ledger = AsyncMock()

            await agent.load_state()

            assert agent.persona == "loaded_persona"
            assert agent.language == "fr"
            agent.state_backend.load_state.assert_called_once_with(agent.session_id)

    @pytest.mark.asyncio
    async def test_agent_save_state(self, mock_llm_config, mock_all_external_services):
        """Test saving agent state"""
        # Ensure messages_to_dict is properly mocked
        with patch("arbiter.knowledge_graph.core.messages_to_dict", return_value=[]):
            with patch("builtins.open", side_effect=FileNotFoundError):
                agent = CollaborativeAgent(
                    agent_id="test_agent",
                    session_id="test_session",
                    llm_config=mock_llm_config,
                )

                agent.state_backend = AsyncMock()
                agent.audit_ledger = AsyncMock()

                await agent.save_state()

                agent.state_backend.save_state.assert_called_once()
                call_args = agent.state_backend.save_state.call_args[0]
                assert call_args[0] == agent.session_id
                assert "history" in call_args[1]
                assert "persona" in call_args[1]
                assert "language" in call_args[1]

    @pytest.mark.asyncio
    async def test_agent_set_persona(self, mock_llm_config, mock_all_external_services):
        """Test setting agent persona"""
        with patch("builtins.open", side_effect=FileNotFoundError):
            agent = CollaborativeAgent(
                agent_id="test_agent",
                session_id="test_session",
                llm_config=mock_llm_config,
            )

            agent.state_backend = AsyncMock()
            agent.audit_ledger = AsyncMock()

            await agent.set_persona("expert")

            assert agent.persona == "Expert persona"
            agent.state_backend.save_state.assert_called()

    @pytest.mark.asyncio
    async def test_agent_predict_timeout(
        self, mock_llm_config, mock_all_external_services
    ):
        """Test agent prediction timeout"""
        with patch("builtins.open", side_effect=FileNotFoundError):
            agent = CollaborativeAgent(
                agent_id="test_agent",
                session_id="test_session",
                llm_config=mock_llm_config,
            )

            agent.audit_ledger = AsyncMock()

            # For Python 3.10, we need to mock the compatibility wrapper instead
            # Mock the async_timeout function from core module
            with patch("arbiter.knowledge_graph.core.async_timeout") as mock_timeout:
                # Make the context manager raise TimeoutError when entered
                mock_context = AsyncMock()
                mock_context.__aenter__.side_effect = asyncio.TimeoutError()
                mock_context.__aexit__.return_value = None
                mock_timeout.return_value = mock_context

                with pytest.raises(AgentCoreException) as exc_info:
                    await agent.predict("test input", timeout=1)

                assert exc_info.value.code == AgentErrorCode.TIMEOUT


class TestAgentTeam:
    """Test suite for AgentTeam class"""

    @pytest.fixture
    def mock_team_config(self):
        """Fixture for team configuration"""
        return {"provider": "openai", "model": "gpt-3.5-turbo", "api_key": "test_key"}

    def test_team_initialization(self, mock_team_config, mock_all_external_services):
        """Test AgentTeam initialization"""
        state_backend = AsyncMock(spec=StateBackend)
        meta_learning = Mock(spec=MetaLearning)

        with patch("builtins.open", side_effect=FileNotFoundError):
            team = AgentTeam(
                session_id="team_session",
                llm_config=mock_team_config,
                state_backend=state_backend,
                meta_learning=meta_learning,
            )

            assert team.session_id == "team_session"
            assert "requirements" in team.agents
            assert "refiner" in team.agents

    def test_team_initialization_missing_dependencies(self):
        """Test AgentTeam initialization with missing dependencies"""
        with pytest.raises(ValueError) as exc_info:
            team = AgentTeam(
                session_id="team_session",
                llm_config=None,
                state_backend=Mock(),
                meta_learning=Mock(),
            )

        assert "required for AgentTeam" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_team_delegate_task(
        self, mock_team_config, mock_all_external_services
    ):
        """Test delegating task to agent team"""
        state_backend = AsyncMock(spec=StateBackend)
        meta_learning = Mock(spec=MetaLearning)

        with patch("builtins.open", side_effect=FileNotFoundError):
            team = AgentTeam(
                session_id="team_session",
                llm_config=mock_team_config,
                state_backend=state_backend,
                meta_learning=meta_learning,
            )

            # Mock the agents' predict methods
            mock_req_response = {
                "response": "Requirements response",
                "trace": {
                    "initial": "init",
                    "reflection": "ref",
                    "critique": "crit",
                    "corrected": "corr",
                },
            }
            mock_ref_response = {
                "response": "Refined response",
                "trace": {
                    "initial": "init2",
                    "reflection": "ref2",
                    "critique": "crit2",
                    "corrected": "corr2",
                },
            }

            team.agents["requirements"].predict = AsyncMock(
                return_value=mock_req_response
            )
            team.agents["refiner"].predict = AsyncMock(return_value=mock_ref_response)
            team.agents["requirements"].audit_ledger = AsyncMock()
            team.agents["refiner"].audit_ledger = AsyncMock()

            result = await team.delegate_task("Test input")

            assert result["final_response"] == "Refined response"
            assert "requirements_trace" in result
            assert "final_spec_trace" in result

            team.agents["requirements"].predict.assert_called_once()
            team.agents["refiner"].predict.assert_called_once()


class TestFactoryFunctions:
    """Test suite for factory functions"""

    @pytest.mark.asyncio
    async def test_get_or_create_agent_default(self, mock_all_external_services):
        """Test get_or_create_agent with default parameters"""
        with patch("arbiter.knowledge_graph.core.InMemoryStateBackend") as mock_backend:
            mock_backend_instance = AsyncMock()
            mock_backend.return_value = mock_backend_instance

            with patch("builtins.open", side_effect=FileNotFoundError):
                agent = await get_or_create_agent()

                assert agent is not None
                # Agent should have load_state called
                assert hasattr(agent, "load_state")

    @pytest.mark.asyncio
    async def test_get_or_create_agent_with_redis(self, mock_all_external_services):
        """Test get_or_create_agent with Redis backend"""
        with patch(
            "arbiter.knowledge_graph.core.Config.REDIS_URL", "redis://localhost:6379"
        ):
            with patch(
                "arbiter.knowledge_graph.core.RedisStateBackend"
            ) as mock_redis_backend:
                mock_backend_instance = AsyncMock()
                mock_redis_backend.return_value = mock_backend_instance
                mock_backend_instance.init_client = AsyncMock()

                with patch("builtins.open", side_effect=FileNotFoundError):
                    agent = await get_or_create_agent()

                    mock_backend_instance.init_client.assert_called()

    @pytest.mark.asyncio
    async def test_get_or_create_agent_with_postgres(self, mock_all_external_services):
        """Test get_or_create_agent with Postgres backend"""
        with patch(
            "arbiter.knowledge_graph.core.Config.POSTGRES_DB_URL",
            "postgresql://localhost/testdb",
        ):
            with patch("arbiter.knowledge_graph.core.Config.REDIS_URL", None):
                with patch(
                    "arbiter.knowledge_graph.core.PostgresStateBackend"
                ) as mock_pg_backend:
                    mock_backend_instance = AsyncMock()
                    mock_pg_backend.return_value = mock_backend_instance
                    mock_backend_instance.init_client = AsyncMock()

                    with patch("builtins.open", side_effect=FileNotFoundError):
                        agent = await get_or_create_agent()

                        mock_backend_instance.init_client.assert_called()

    @pytest.mark.asyncio
    async def test_setup_conversation_legacy(self, mock_all_external_services):
        """Test legacy setup_conversation function"""
        # Create a mock agent with all required attributes
        mock_agent = AsyncMock()
        mock_agent.llm = MagicMock()
        mock_agent.memory = MagicMock()
        mock_agent.set_persona = AsyncMock()
        mock_agent.language = "en"

        with patch(
            "arbiter.knowledge_graph.core.get_or_create_agent", return_value=mock_agent
        ):
            # Create a mock LLM object with __class__.__name__
            mock_llm = MagicMock()
            mock_llm.__class__.__name__ = "ChatOpenAI"
            mock_llm.model_name = "gpt-3.5-turbo"
            mock_llm.temperature = 0.7

            chain, memory = await setup_conversation(mock_llm, "expert", "en")

            assert chain is not None
            assert memory == mock_agent.memory
            mock_agent.set_persona.assert_called_once_with("expert")
            assert mock_agent.language == "en"


class TestUtilityFunctions:
    """Test suite for utility functions"""

    def test_get_transcript(self):
        """Test get_transcript function"""
        memory = MagicMock()
        memory.load_memory_variables.return_value = {
            "history": ["Message 1", "Message 2"]
        }

        with patch(
            "arbiter.knowledge_graph.core._sanitize_user_input",
            side_effect=lambda x: f"sanitized_{x}",
        ):
            result = get_transcript(memory)

            assert result == ["sanitized_Message 1", "sanitized_Message 2"]


class TestErrorHandling:
    """Test suite for error handling"""

    def test_agent_core_exception(self):
        """Test AgentCoreException"""
        original_error = ValueError("Original error")
        exc = AgentCoreException(
            "Test error",
            code=AgentErrorCode.LLM_CALL_FAILED,
            original_exception=original_error,
        )

        assert exc.message == "Test error"
        assert exc.code == AgentErrorCode.LLM_CALL_FAILED
        assert exc.original_exception == original_error
        assert "LLM_CALL_FAILED" in str(exc)

    @pytest.mark.asyncio
    async def test_state_backend_error_handling(self):
        """Test error handling in state backends"""
        backend = InMemoryStateBackend()

        # Create a proper async mock for the lock
        mock_lock = AsyncMock()
        mock_lock.__aenter__ = AsyncMock(side_effect=Exception("Lock error"))
        mock_lock.__aexit__ = AsyncMock(return_value=None)

        with patch.object(backend, "_lock", mock_lock):
            with pytest.raises(Exception) as exc_info:
                await backend.save_state("session", {"test": "data"})

            assert "Lock error" in str(exc_info.value)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
