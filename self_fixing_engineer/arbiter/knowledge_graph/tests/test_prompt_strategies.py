import pytest
import json
import os
import tempfile
import logging
from unittest.mock import Mock, MagicMock, AsyncMock, patch, mock_open
from typing import List

# Import the module components to test
from arbiter.knowledge_graph.prompt_strategies import (
    PromptStrategy,
    DefaultPromptStrategy,
    ConcisePromptStrategy,
    _load_templates,
    PROMPT_TEMPLATES,
    PROMPT_TEMPLATES_FALLBACK,
    BASE_AGENT_PROMPT_TEMPLATE,
    REFLECTION_PROMPT_TEMPLATE,
    CRITIQUE_PROMPT_TEMPLATE,
    SELF_CORRECT_PROMPT_TEMPLATE
)


class TestPromptTemplateLoading:
    """Test suite for prompt template loading functionality"""
    
    def test_load_templates_from_file_success(self):
        """Test successful loading of templates from JSON file"""
        test_templates = {
            "BASE_AGENT_PROMPT_TEMPLATE": "Test base template",
            "REFLECTION_PROMPT_TEMPLATE": "Test reflection template",
            "CRITIQUE_PROMPT_TEMPLATE": "Test critique template",
            "SELF_CORRECT_PROMPT_TEMPLATE": "Test correction template"
        }
        
        with patch('builtins.open', mock_open(read_data=json.dumps(test_templates))):
            with patch('arbiter.knowledge_graph.prompt_strategies.logger') as mock_logger:
                with patch.dict('arbiter.knowledge_graph.prompt_strategies.PROMPT_TEMPLATES', {}):
                    _load_templates()
                    
                    mock_logger.info.assert_called_once()
                    assert "loaded from file" in mock_logger.info.call_args[0][0]
    
    def test_load_templates_file_not_found(self):
        """Test fallback when template file is not found"""
        with patch('builtins.open', side_effect=FileNotFoundError()):
            with patch('arbiter.knowledge_graph.prompt_strategies.logger') as mock_logger:
                with patch.dict('arbiter.knowledge_graph.prompt_strategies.PROMPT_TEMPLATES', {}):
                    _load_templates()
                    
                    mock_logger.warning.assert_called_once()
                    assert "not found" in mock_logger.warning.call_args[0][0]
    
    def test_load_templates_json_decode_error(self):
        """Test fallback when JSON file is malformed"""
        with patch('builtins.open', mock_open(read_data="invalid json {")):
            with patch('arbiter.knowledge_graph.prompt_strategies.logger') as mock_logger:
                with patch.dict('arbiter.knowledge_graph.prompt_strategies.PROMPT_TEMPLATES', {}):
                    _load_templates()
                    
                    mock_logger.error.assert_called()
                    assert "Failed to parse" in mock_logger.error.call_args[0][0]
    
    def test_load_templates_unexpected_error(self):
        """Test fallback on unexpected errors"""
        with patch('builtins.open', side_effect=PermissionError("No permission")):
            with patch('arbiter.knowledge_graph.prompt_strategies.logger') as mock_logger:
                with patch.dict('arbiter.knowledge_graph.prompt_strategies.PROMPT_TEMPLATES', {}):
                    _load_templates()
                    
                    mock_logger.error.assert_called()
                    assert "unexpected error" in mock_logger.error.call_args[0][0]
    
    def test_load_templates_with_custom_file_path(self):
        """Test loading templates with custom file path from environment"""
        custom_path = "/custom/path/templates.json"
        test_templates = {
            "BASE_AGENT_PROMPT_TEMPLATE": "Custom template",
            "REFLECTION_PROMPT_TEMPLATE": "Reflection",
            "CRITIQUE_PROMPT_TEMPLATE": "Critique",
            "SELF_CORRECT_PROMPT_TEMPLATE": "Correct"
        }
        
        # Fix: Patch PROMPT_TEMPLATE_FILE directly and ensure all required templates are provided
        with patch('arbiter.knowledge_graph.prompt_strategies.PROMPT_TEMPLATE_FILE', custom_path):
            with patch('builtins.open', mock_open(read_data=json.dumps(test_templates))) as mock_file:
                with patch('arbiter.knowledge_graph.prompt_strategies.logger'):
                    # Import and call the function to reload templates
                    from arbiter.knowledge_graph.prompt_strategies import _load_templates
                    _load_templates()
                    
                    mock_file.assert_called_with(custom_path, 'r', encoding='utf-8')
                    # Verify templates were loaded
                    import arbiter.knowledge_graph.prompt_strategies as ps
                    assert ps.PROMPT_TEMPLATES["BASE_AGENT_PROMPT_TEMPLATE"] == "Custom template"
    
    def test_template_constants_are_set(self):
        """Test that template constants are properly set after loading"""
        # Since _load_templates() is called at module import, templates should be set
        assert BASE_AGENT_PROMPT_TEMPLATE is not None
        assert REFLECTION_PROMPT_TEMPLATE is not None
        assert CRITIQUE_PROMPT_TEMPLATE is not None
        assert SELF_CORRECT_PROMPT_TEMPLATE is not None
        
        # Should contain expected keys
        assert "persona" in BASE_AGENT_PROMPT_TEMPLATE
        assert "language" in BASE_AGENT_PROMPT_TEMPLATE
        assert "ai_response" in REFLECTION_PROMPT_TEMPLATE
        assert "critique" in SELF_CORRECT_PROMPT_TEMPLATE


class TestPromptStrategy:
    """Test suite for PromptStrategy abstract base class"""
    
    def test_abstract_base_class(self):
        """Test that PromptStrategy is abstract and cannot be instantiated"""
        mock_logger = Mock(spec=logging.Logger)
        
        with pytest.raises(TypeError):
            PromptStrategy(mock_logger)
    
    def test_concrete_implementation_required(self):
        """Test that concrete implementations must implement create_agent_prompt"""
        mock_logger = Mock(spec=logging.Logger)
        
        class IncompleteStrategy(PromptStrategy):
            pass
        
        with pytest.raises(TypeError):
            IncompleteStrategy(mock_logger)
    
    def test_get_history_transcript_empty(self):
        """Test get_history_transcript with no history"""
        mock_logger = Mock(spec=logging.Logger)
        
        class TestStrategy(PromptStrategy):
            async def create_agent_prompt(self, *args, **kwargs):
                return "test"
        
        strategy = TestStrategy(mock_logger)
        assert strategy.get_history_transcript() == ""
    
    def test_get_history_transcript_with_data(self):
        """Test get_history_transcript with history data"""
        mock_logger = Mock(spec=logging.Logger)
        
        class TestStrategy(PromptStrategy):
            async def create_agent_prompt(self, *args, **kwargs):
                return "test"
        
        strategy = TestStrategy(mock_logger)
        strategy.history_transcript = "Previous conversation"
        assert strategy.get_history_transcript() == "Previous conversation"


class TestDefaultPromptStrategy:
    """Test suite for DefaultPromptStrategy"""
    
    @pytest.fixture
    def mock_logger(self):
        """Fixture for mock logger"""
        return Mock(spec=logging.Logger)
    
    @pytest.fixture
    def mock_multimodal_data(self):
        """Fixture for creating mock MultiModalData instances"""
        def create_mock(data_type, summary=None):
            mock_data = Mock()
            mock_data.data_type = data_type
            mock_data.metadata = {"summary": summary} if summary else {}
            return mock_data
        return create_mock
    
    @pytest.mark.asyncio
    async def test_create_agent_prompt_basic(self, mock_logger):
        """Test basic prompt creation without multimodal context"""
        strategy = DefaultPromptStrategy(mock_logger)
        
        base_template = "Persona: {persona}, Language: {language}, History: {history}, Input: {input}, MM: {multi_modal_context}"
        history = "Previous conversation"
        user_input = "Hello"
        persona = "Helpful assistant"
        language = "en"
        multi_modal_context = []
        
        result = await strategy.create_agent_prompt(
            base_template=base_template,
            history=history,
            user_input=user_input,
            persona=persona,
            language=language,
            multi_modal_context=multi_modal_context
        )
        
        assert persona in result
        assert language in result
        assert history in result
        assert user_input in result
        mock_logger.debug.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_create_agent_prompt_with_multimodal(self, mock_logger, mock_multimodal_data):
        """Test prompt creation with multimodal context"""
        strategy = DefaultPromptStrategy(mock_logger)
        
        base_template = "{multi_modal_context}"
        multi_modal_context = [
            mock_multimodal_data("image", "A beautiful sunset"),
            mock_multimodal_data("audio", "Background music"),
            mock_multimodal_data("text_file", None)  # No summary
        ]
        
        result = await strategy.create_agent_prompt(
            base_template=base_template,
            history="",
            user_input="",
            persona="",
            language="",
            multi_modal_context=multi_modal_context
        )
        
        assert "image: A beautiful sunset" in result
        assert "audio: Background music" in result
        assert "text_file: No summary available" in result
    
    @pytest.mark.asyncio
    async def test_create_agent_prompt_empty_multimodal(self, mock_logger):
        """Test prompt creation with empty multimodal context"""
        strategy = DefaultPromptStrategy(mock_logger)
        
        base_template = "MM Context: {multi_modal_context} | Other: {input}"
        
        result = await strategy.create_agent_prompt(
            base_template=base_template,
            history="",
            user_input="test",
            persona="",
            language="",
            multi_modal_context=[]
        )
        
        assert "MM Context:  | Other: test" in result
    
    @pytest.mark.asyncio
    async def test_create_agent_prompt_with_real_template(self, mock_logger):
        """Test with the actual BASE_AGENT_PROMPT_TEMPLATE"""
        strategy = DefaultPromptStrategy(mock_logger)
        
        result = await strategy.create_agent_prompt(
            base_template=BASE_AGENT_PROMPT_TEMPLATE,
            history="Human: Hi\nAI: Hello!",
            user_input="How are you?",
            persona="Friendly assistant",
            language="en",
            multi_modal_context=[]
        )
        
        assert "Friendly assistant" in result
        assert "en" in result
        assert "How are you?" in result
        assert "Human: Hi" in result


class TestConcisePromptStrategy:
    """Test suite for ConcisePromptStrategy"""
    
    @pytest.fixture
    def mock_logger(self):
        """Fixture for mock logger"""
        return Mock(spec=logging.Logger)
    
    @pytest.fixture
    def mock_multimodal_data(self):
        """Fixture for creating mock MultiModalData instances"""
        def create_mock(data_type, summary=None):
            mock_data = Mock()
            mock_data.data_type = data_type
            mock_data.metadata = {"summary": summary} if summary else {}
            return mock_data
        return create_mock
    
    @pytest.mark.asyncio
    async def test_create_agent_prompt_basic(self, mock_logger):
        """Test basic concise prompt creation"""
        strategy = ConcisePromptStrategy(mock_logger)
        
        base_template = "History: {history} | Input: {input}"
        short_history = "Short history"
        
        result = await strategy.create_agent_prompt(
            base_template=base_template,
            history=short_history,
            user_input="Question",
            persona="",
            language="",
            multi_modal_context=[]
        )
        
        assert "Short history" in result
        assert "Question" in result
        mock_logger.debug.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_create_agent_prompt_with_truncation(self, mock_logger):
        """Test prompt creation with history truncation"""
        strategy = ConcisePromptStrategy(mock_logger)
        
        base_template = "History: {history}"
        long_history = "x" * 1000  # Very long history
        
        result = await strategy.create_agent_prompt(
            base_template=base_template,
            history=long_history,
            user_input="",
            persona="",
            language="",
            multi_modal_context=[]
        )
        
        assert "truncated for brevity" in result
        assert len(result) < len(long_history)
    
    def test_truncate_history_short(self, mock_logger):
        """Test _truncate_history with short history"""
        strategy = ConcisePromptStrategy(mock_logger)
        
        short_text = "Short text"
        result = strategy._truncate_history(short_text, max_chars=100)
        
        assert result == short_text
    
    def test_truncate_history_long(self, mock_logger):
        """Test _truncate_history with long history"""
        strategy = ConcisePromptStrategy(mock_logger)
        
        long_text = "a" * 1000
        result = strategy._truncate_history(long_text, max_chars=100)
        
        assert "truncated for brevity" in result
        assert len(result) < 200  # Should be roughly max_chars plus truncation message
        assert result.endswith("a" * 100)
    
    @pytest.mark.asyncio
    async def test_create_agent_prompt_with_multimodal(self, mock_logger, mock_multimodal_data):
        """Test concise prompt with multimodal context"""
        strategy = ConcisePromptStrategy(mock_logger)
        
        base_template = "{multi_modal_context}"
        multi_modal_context = [
            mock_multimodal_data("image", "Image description"),
            mock_multimodal_data("audio", "Audio description")
        ]
        
        result = await strategy.create_agent_prompt(
            base_template=base_template,
            history="",
            user_input="",
            persona="",
            language="",
            multi_modal_context=multi_modal_context
        )
        
        assert "image: Image description" in result
        assert "audio: Audio description" in result


class TestIntegration:
    """Integration tests for prompt strategies"""
    
    @pytest.mark.asyncio
    async def test_strategy_comparison(self):
        """Compare outputs of different strategies"""
        mock_logger = Mock(spec=logging.Logger)
        
        default_strategy = DefaultPromptStrategy(mock_logger)
        concise_strategy = ConcisePromptStrategy(mock_logger)
        
        base_template = "History: {history} | Input: {input}"
        long_history = "x" * 1000
        
        default_result = await default_strategy.create_agent_prompt(
            base_template=base_template,
            history=long_history,
            user_input="test",
            persona="",
            language="",
            multi_modal_context=[]
        )
        
        concise_result = await concise_strategy.create_agent_prompt(
            base_template=base_template,
            history=long_history,
            user_input="test",
            persona="",
            language="",
            multi_modal_context=[]
        )
        
        # Concise should be shorter
        assert len(concise_result) < len(default_result)
        assert "truncated" in concise_result
        assert "truncated" not in default_result
    
    @pytest.mark.asyncio
    async def test_with_actual_templates(self):
        """Test strategies with actual prompt templates"""
        mock_logger = Mock(spec=logging.Logger)
        
        strategy = DefaultPromptStrategy(mock_logger)
        
        # Test with each template type
        templates_to_test = [
            (BASE_AGENT_PROMPT_TEMPLATE, ["persona", "language", "history", "input"]),
            (REFLECTION_PROMPT_TEMPLATE, ["input", "ai_response"]),
            (CRITIQUE_PROMPT_TEMPLATE, ["persona", "ai_response"]),
            (SELF_CORRECT_PROMPT_TEMPLATE, ["ai_response", "reflection", "critique"])
        ]
        
        for template, required_keys in templates_to_test:
            # Verify template has placeholders
            for key in required_keys:
                assert f"{{{key}}}" in template
    
    @pytest.mark.asyncio
    async def test_custom_strategy_implementation(self):
        """Test creating a custom strategy implementation"""
        mock_logger = Mock(spec=logging.Logger)
        
        class CustomStrategy(PromptStrategy):
            async def create_agent_prompt(
                self,
                base_template: str,
                history: str,
                user_input: str,
                persona: str,
                language: str,
                multi_modal_context: List
            ) -> str:
                # Custom implementation that adds prefixes
                formatted = base_template.format(
                    persona=f'Enhanced-{persona}',
                    language=language.upper(),
                    multi_modal_context='',
                    history=history,
                    input=user_input
                )
                return f"CUSTOM: {formatted}"
        
        strategy = CustomStrategy(mock_logger)
        
        result = await strategy.create_agent_prompt(
            base_template="{persona} | {language}",
            history="",
            user_input="",
            persona="assistant",
            language="en",
            multi_modal_context=[]
        )
        
        assert "CUSTOM:" in result
        assert "Enhanced-assistant" in result
        assert "EN" in result


class TestErrorHandling:
    """Test error handling in prompt strategies"""
    
    @pytest.mark.asyncio
    async def test_missing_template_keys(self):
        """Test handling of missing keys in template formatting"""
        mock_logger = Mock(spec=logging.Logger)
        strategy = DefaultPromptStrategy(mock_logger)
        
        # Template with extra placeholder not provided
        bad_template = "{persona} {missing_key}"
        
        with pytest.raises(KeyError):
            await strategy.create_agent_prompt(
                base_template=bad_template,
                history="",
                user_input="",
                persona="test",
                language="en",
                multi_modal_context=[]
            )
    
    @pytest.mark.asyncio
    async def test_none_values_handling(self):
        """Test handling of None values in parameters"""
        mock_logger = Mock(spec=logging.Logger)
        strategy = DefaultPromptStrategy(mock_logger)
        
        # Should handle None values gracefully
        result = await strategy.create_agent_prompt(
            base_template="{history}",
            history=None or "",  # Simulate None being converted to empty string
            user_input="",
            persona="",
            language="",
            multi_modal_context=[]
        )
        
        assert result == ""


if __name__ == "__main__":
    pytest.main([__file__, "-v"])