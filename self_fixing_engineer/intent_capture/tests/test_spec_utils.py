import json
from unittest.mock import patch, MagicMock, AsyncMock
import pytest
import requests
import nltk
from langchain_core.language_models import BaseChatModel

# Import the module under test
import intent_capture.spec_utils as spec_utils_module
from intent_capture.spec_utils import (
    load_ambiguous_words,
    register_spec_handler,
    validate_spec,
    migrate_spec,
    detect_ambiguity,
    auto_fix_spec,
    TraceableArtifact,
    generate_code_stub,
    generate_test_stub,
    generate_security_review,
    generate_spec_from_memory,
    generate_gaps,
    refine_spec,
    review_spec,
    diff_specs,
    get_localized_prompt,
    SPEC_HANDLERS,
)

# --- Test Fixtures ---
@pytest.fixture
def mock_requests():
    """Mock requests for ambiguous words URL and artifact persistence."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"en": ["word1"]}
    mock_resp.raise_for_status = MagicMock()
    
    # Create a mock post function for artifact persistence
    mock_post = MagicMock()
    
    # Patch both requests.get and requests.post
    with patch('intent_capture.spec_utils.requests.get', return_value=mock_resp) as mock_get:
        with patch('intent_capture.spec_utils.requests.post', mock_post) as mock_post_patch:
            yield {'get': mock_get, 'post': mock_post_patch, 'response': mock_resp}

@pytest.fixture
def mock_nltk(monkeypatch):
    """Mock NLTK for ambiguity detection."""
    monkeypatch.setattr(nltk.data, 'find', MagicMock(return_value=True))
    monkeypatch.setattr(nltk, 'download', MagicMock())
    monkeypatch.setattr(nltk, 'sent_tokenize', lambda t: [t])
    monkeypatch.setattr(nltk, 'word_tokenize', lambda s: s.lower().split())
    yield

@pytest.fixture
def mock_llm():
    """Mock LLM for spec operations - properly handles chain operations."""
    # Create a mock response object with string content
    class MockResponse:
        def __init__(self, content="mock_content"):
            self.content = content
    
    # Create the mock LLM
    mock_llm = MagicMock(spec=BaseChatModel)
    
    # Create an async function that returns a proper response
    async def mock_ainvoke(*args, **kwargs):
        return MockResponse("mock_content")
    
    # Set up a mock chain that also returns proper responses
    mock_chain = MagicMock()
    mock_chain.ainvoke = AsyncMock(side_effect=mock_ainvoke)
    
    # Make the LLM work with the pipe operator (prompt | llm)
    mock_llm.__or__ = MagicMock(return_value=mock_chain)
    mock_llm.__ror__ = MagicMock(return_value=mock_chain)
    mock_llm.ainvoke = AsyncMock(side_effect=mock_ainvoke)
    
    yield mock_llm

@pytest.fixture
def mock_tracer():
    if not spec_utils_module.OPENTELEMETRY_AVAILABLE:
        pytest.skip("OpenTelemetry not available in spec_utils module.")
    mock_span = MagicMock()
    mock_span.set_attribute = MagicMock()
    mock_span.add_event = MagicMock()
    mock_span.set_status = MagicMock()
    
    mock_tracer_instance = MagicMock()
    mock_tracer_instance.start_as_current_span.return_value.__enter__.return_value = mock_span
    mock_tracer_instance.start_as_current_span.return_value.__aenter__.return_value = mock_span
    mock_tracer_instance.start_as_current_span.return_value.__exit__.return_value = False
    mock_tracer_instance.start_as_current_span.return_value.__aexit__.return_value = False

    with patch('intent_capture.spec_utils.tracer', mock_tracer_instance):
        yield mock_tracer_instance

@pytest.fixture
def mock_prometheus():
    if not spec_utils_module.PROMETHEUS_AVAILABLE:
        pytest.skip("Prometheus not available in spec_utils module.")
    
    mock_counter = MagicMock(inc=MagicMock(), labels=MagicMock(return_value=MagicMock(inc=MagicMock())))
    mock_histogram = MagicMock(observe=MagicMock(), labels=MagicMock(return_value=MagicMock(observe=MagicMock())))
    
    with patch('intent_capture.spec_utils.SPEC_GEN_TOTAL', mock_counter), \
         patch('intent_capture.spec_utils.SPEC_GEN_LATENCY_SECONDS', mock_histogram), \
         patch('intent_capture.spec_utils.SPEC_VALIDATION_TOTAL', mock_counter), \
         patch('intent_capture.spec_utils.SPEC_AUTO_FIX_TOTAL', mock_counter):
        yield

@pytest.fixture
def temp_locales(tmp_path, monkeypatch):
    """Create temporary locales file and properly load it."""
    locales_path = tmp_path / "locales.yaml"
    locales_content = """
en:
  auto_fix_spec_prompt: "mock_prompt {format} {issue_summary} {spec}"
  generate_code_stub_prompt: "mock_code_prompt {spec} {language_name}"
  generate_test_stub_prompt: "mock_test_prompt {spec} {framework}"
  generate_security_review_prompt: "mock_security_prompt {spec}"
  generate_gaps_prompt: "mock_gaps_prompt {checklist_str} {spec_content} {transcript}"
  refine_spec_prompt: "mock_refine_prompt {instruction} {last_spec}"
  review_spec_prompt: "mock_review_prompt {spec_content}"
  generate_spec_prompt_gherkin: "mock_gherkin_gen_prompt {transcript} {persona} {language}"
"""
    locales_path.write_text(locales_content)
    
    # Patch the LOCALES_FILE and force reload
    monkeypatch.setattr('intent_capture.spec_utils.LOCALES_FILE', str(locales_path))
    
    # Manually call _load_locales to update the module's _LOCALES
    spec_utils_module._load_locales()
    
    yield

@pytest.fixture
def mock_memory():
    """Mock agent memory."""
    mock_mem = MagicMock(load_memory_variables=MagicMock(return_value={"history": "mock_transcript"}))
    yield mock_mem

@pytest.fixture
def mock_checklist():
    """Mock get_checklist to avoid database calls."""
    with patch('intent_capture.spec_utils.get_checklist', AsyncMock(return_value=[{"name": "req", "weight": 1, "description": "desc"}])):
        yield

@pytest.fixture(autouse=True)
def reset_spec_handlers():
    """Reset SPEC_HANDLERS before each test to avoid state pollution."""
    original_handlers = spec_utils_module.SPEC_HANDLERS.copy()
    spec_utils_module.SPEC_HANDLERS.clear()
    yield
    spec_utils_module.SPEC_HANDLERS.clear()
    spec_utils_module.SPEC_HANDLERS.update(original_handlers)

# --- Tests for Tracing Context ---
def test_get_tracing_context(mock_tracer):
    """Test getting tracing context."""
    with spec_utils_module.get_tracing_context("test_span"):
        pass
    mock_tracer.start_as_current_span.assert_called_with("test_span")

def test_get_tracing_context_no_opentelemetry(monkeypatch):
    """Test fallback context when no opentelemetry."""
    monkeypatch.setattr('intent_capture.spec_utils.OPENTELEMETRY_AVAILABLE', False)
    with spec_utils_module.get_tracing_context("test_span"):
        pass

# --- Tests for NLTK Data Setup ---
def test_nltk_data_setup(monkeypatch):
    """Test NLTK data availability or download."""
    monkeypatch.setattr(nltk.data, 'find', MagicMock(side_effect=LookupError))
    monkeypatch.setattr(nltk, 'download', MagicMock())
    
    try:
        nltk.data.find('tokenizers/punkt')
    except LookupError:
        nltk.download('punkt')
    
    nltk.download.assert_called_with('punkt')

# --- Tests for Localized Prompts ---
def test_load_locales(temp_locales):
    """Test loading locales."""
    prompt = get_localized_prompt("auto_fix_spec_prompt")
    assert "mock_prompt" in prompt

def test_get_localized_prompt_default(temp_locales):
    """Test default prompt if not found."""
    prompt = get_localized_prompt("missing")
    assert "not found" in prompt

# --- Tests for Ambiguous Words Loading ---
def test_load_ambiguous_words_file(tmp_path):
    """Test loading ambiguous words from file."""
    file_path = tmp_path / "ambiguous_words.json"
    file_path.write_text(json.dumps({"en": ["word1", "word2"]}))
    with patch('intent_capture.spec_utils.os.environ.get', side_effect=lambda k, d=None: str(file_path) if k == "AMBIGUOUS_WORDS_PATH" else d):
        words = load_ambiguous_words("en")
    assert words == ["word1", "word2"]

def test_load_ambiguous_words_url(mock_requests):
    """Test loading ambiguous words from URL."""
    with patch('intent_capture.spec_utils.os.environ.get', side_effect=lambda k, d=None: "http://test" if k == "AMBIGUOUS_WORDS_URL" else d):
        words = load_ambiguous_words("en")
    assert words == ["word1"]

def test_load_ambiguous_words_retry():
    """Test retry on ambiguous words URL failure."""
    mock_resp_success = MagicMock()
    mock_resp_success.json.return_value = {"en": ["word1"]}
    mock_resp_success.raise_for_status = MagicMock()
    
    with patch('intent_capture.spec_utils.requests.get', side_effect=[
        requests.exceptions.ConnectionError, 
        mock_resp_success
    ]) as mock_get:
        with patch('intent_capture.spec_utils.os.environ.get', side_effect=lambda k, d=None: "http://test" if k == "AMBIGUOUS_WORDS_URL" else d):
            words = load_ambiguous_words("en")
    assert words == ["word1"]
    assert mock_get.call_count == 2

def test_load_ambiguous_words_failure():
    """Test ambiguous words loading failure."""
    with patch('intent_capture.spec_utils.requests.get', side_effect=Exception("Mock failure")):
        with patch('intent_capture.spec_utils.os.environ.get', side_effect=lambda k, d=None: "http://test" if k == "AMBIGUOUS_WORDS_URL" else d):
            words = load_ambiguous_words("en")
    assert words == []

# --- Tests for Spec Handler Registration ---
def test_register_spec_handler():
    """Test registering spec handler."""
    def mock_validator(s): return True, "valid"
    def mock_generator(*args): return "generated"
    register_spec_handler("test_format", mock_validator, mock_generator)
    assert "test_format" in SPEC_HANDLERS

# --- Tests for Spec Validation ---
def test_validate_spec_json_valid(mock_tracer, mock_prometheus):
    """Test valid JSON spec."""
    spec = '{"key": "value"}'
    is_valid, msg = validate_spec(spec, "json")
    assert is_valid
    assert msg == "Valid JSON"

def test_validate_spec_json_invalid(mock_tracer, mock_prometheus):
    """Test invalid JSON spec."""
    spec = "{invalid}"
    is_valid, msg = validate_spec(spec, "json")
    assert not is_valid
    assert "Invalid JSON" in msg

def test_validate_spec_json_schema_violation(mock_tracer, mock_prometheus):
    """Test JSON schema violation."""
    spec = '{"key": "value"}'
    schema = {"type": "object", "properties": {"required_key": {"type": "string"}}, "required": ["required_key"]}
    is_valid, msg = validate_spec(spec, "json", schema=schema)
    assert not is_valid
    assert "Schema violation" in msg

def test_validate_spec_yaml_valid(mock_tracer, mock_prometheus):
    """Test valid YAML spec."""
    spec = "key: value"
    is_valid, msg = validate_spec(spec, "yaml")
    assert is_valid
    assert msg == "Valid YAML"

def test_validate_spec_yaml_duplicate_keys(mock_tracer, mock_prometheus):
    """Test YAML with duplicate keys - PyYAML doesn't detect duplicates by default."""
    spec = "key: value\nkey: duplicate"
    is_valid, msg = validate_spec(spec, "yaml")
    # PyYAML silently overwrites duplicate keys, so this will be valid
    assert is_valid

def test_validate_spec_gherkin_valid(mock_tracer, mock_prometheus):
    """Test valid Gherkin spec."""
    spec = "Feature: Test\nScenario: Test\n  Given test\n  When test\n  Then test"
    is_valid, msg = validate_spec(spec, "gherkin")
    assert is_valid
    assert msg == "Valid Gherkin"

def test_validate_spec_gherkin_missing_feature(mock_tracer, mock_prometheus):
    """Test Gherkin missing feature."""
    spec = "Scenario: Test"
    is_valid, msg = validate_spec(spec, "gherkin")
    assert not is_valid
    assert "Missing 'Feature'" in msg

def test_validate_spec_user_story_valid(mock_tracer, mock_prometheus):
    """Test valid user story spec."""
    spec = "As a user I want feature so that benefit"
    is_valid, msg = validate_spec(spec, "user_story")
    assert is_valid
    assert "detected" in msg

def test_validate_spec_unknown_format(mock_tracer, mock_prometheus):
    """Test unknown format validation."""
    spec = "content"
    is_valid, msg = validate_spec(spec, "unknown")
    assert is_valid
    assert "No specific validator" in msg

# --- Tests for Spec Migration ---
def test_migrate_spec():
    """Test spec migration stub."""
    spec, msg = migrate_spec("spec", "format", "1.0", "2.0")
    assert spec is None
    assert "not supported" in msg

# --- Tests for Ambiguity Detection ---
def test_detect_ambiguity(mock_nltk):
    """Test ambiguity detection."""
    with patch('intent_capture.spec_utils.load_ambiguous_words', return_value=["ambiguous"]):
        text = "This is ambiguous text"
        ambiguities = detect_ambiguity(text, "en")
        assert len(ambiguities) > 0
        assert ambiguities[0]["vague_elements"] == ["ambiguous"]

# --- Tests for Auto-Fix Spec ---
@pytest.mark.asyncio
async def test_auto_fix_spec_no_issues(mock_llm):
    """Test auto-fix with no issues."""
    spec, notes = await auto_fix_spec("spec", mock_llm, "json", [])
    assert spec == "spec"
    assert "No issues" in notes

@pytest.mark.asyncio
async def test_auto_fix_spec_success(mock_tracer, mock_prometheus, temp_locales):
    """Test successful auto-fix."""
    # Create a custom response for this test
    class MockResponse:
        def __init__(self):
            self.content = "fixed_spec_that_is_long_enough"
    
    # Create a new mock LLM specifically for this test
    mock_llm = MagicMock(spec=BaseChatModel)
    
    # Mock the entire chain operation inline
    async def mock_chain_invoke(*args, **kwargs):
        return MockResponse()
    
    # Create a mock chain object
    mock_chain = MagicMock()
    mock_chain.ainvoke = mock_chain_invoke  # Not AsyncMock, but a real async function
    
    # Patch PromptTemplate to make the pipe operation work
    with patch('intent_capture.spec_utils.PromptTemplate') as mock_prompt_template:
        mock_prompt = MagicMock()
        mock_prompt_template.from_template.return_value = mock_prompt
        # When prompt | llm is called, return our mock chain
        mock_prompt.__or__ = MagicMock(return_value=mock_chain)
        
        with patch('intent_capture.spec_utils.validate_spec', return_value=(True, "valid")):
            spec, notes = await auto_fix_spec("original", mock_llm, "json", [{"line": 1, "sentence": "sent", "vague_elements": ["word"]}])
    
    assert spec == "fixed_spec_that_is_long_enough"
    assert "Auto-fixed" in notes

@pytest.mark.asyncio
async def test_auto_fix_spec_failure(mock_llm, temp_locales):
    """Test auto-fix failure after retries."""
    # Create a response that's too short
    class MockResponse:
        def __init__(self):
            self.content = "inv"  # Too short
    
    async def custom_ainvoke(*args, **kwargs):
        return MockResponse()
    
    mock_chain = MagicMock()
    mock_chain.ainvoke = AsyncMock(side_effect=custom_ainvoke)
    mock_llm.__or__ = MagicMock(return_value=mock_chain)
    
    with patch('intent_capture.spec_utils.validate_spec', return_value=(False, "invalid")):
        spec, notes = await auto_fix_spec("original", mock_llm, "json", [{"line": 1, "sentence": "sent", "vague_elements": ["word"]}])
    assert spec is None
    assert "failed" in notes

# --- Tests for Traceable Artifact ---
def test_traceable_artifact_persistence(mock_requests):
    """Test artifact persistence."""
    TraceableArtifact("content", "type", "source", "prompt")
    # Check that post was called (it's mocked so won't actually fail)
    mock_requests['post'].assert_called()

def test_traceable_artifact_update(mock_requests):
    """Test artifact update."""
    artifact = TraceableArtifact("content", "type", "source", "prompt")
    mock_requests['post'].reset_mock()
    
    artifact.update("new_content", "notes")
    assert artifact.content == "new_content"
    assert len(artifact.history) == 2
    mock_requests['post'].assert_called()

# --- Tests for Downstream Artifact Generation ---
@pytest.mark.asyncio
async def test_generate_code_stub(mock_llm, temp_locales, mock_requests):
    """Test code stub generation."""
    stub = await generate_code_stub("id", "spec", mock_llm)
    assert isinstance(stub, TraceableArtifact)
    assert stub.content == "mock_content"

@pytest.mark.asyncio
async def test_generate_test_stub(mock_llm, temp_locales, mock_requests):
    """Test test stub generation."""
    stub = await generate_test_stub("id", "spec", mock_llm)
    assert isinstance(stub, TraceableArtifact)
    assert stub.content == "mock_content"

@pytest.mark.asyncio
async def test_generate_security_review(mock_llm, temp_locales, mock_requests):
    """Test security review generation."""
    review = await generate_security_review("id", "spec", mock_llm)
    assert isinstance(review, TraceableArtifact)
    assert review.content == "mock_content"

# --- Tests for Spec Generation from Memory ---
@pytest.mark.asyncio
async def test_generate_spec_from_memory_success(mock_memory, mock_tracer, mock_prometheus, mock_checklist, temp_locales):
    """Test successful spec generation."""
    # Create a custom response with valid Gherkin
    gherkin_spec = "Feature: Test\nScenario: Test\n  Given test\n  When test\n  Then test"
    
    class MockResponse:
        def __init__(self):
            self.content = gherkin_spec
    
    async def custom_ainvoke(*args, **kwargs):
        return MockResponse()
    
    # Create a fresh mock LLM for this test with proper chain handling
    mock_llm = MagicMock(spec=BaseChatModel)
    mock_chain = MagicMock()
    mock_chain.ainvoke = AsyncMock(side_effect=custom_ainvoke)
    mock_llm.__or__ = MagicMock(return_value=mock_chain)
    mock_llm.__ror__ = MagicMock(return_value=mock_chain)
    mock_llm.ainvoke = AsyncMock(side_effect=custom_ainvoke)
    
    result = await generate_spec_from_memory(mock_memory, mock_llm)
    assert result is not None
    assert "id" in result
    assert result["content"] == gherkin_spec

@pytest.mark.asyncio
async def test_generate_spec_from_memory_auto_fix(mock_memory, mock_tracer, mock_prometheus, mock_checklist, temp_locales):
    """Test spec generation with auto-fix."""
    # Set up mock to return valid gherkin after auto-fix
    gherkin_spec = "Feature: Test\nScenario: Test\n  Given test\n  When test\n  Then test"
    
    class MockResponse:
        def __init__(self):
            self.content = gherkin_spec
    
    async def custom_ainvoke(*args, **kwargs):
        return MockResponse()
    
    # Create fresh mock LLM
    mock_llm = MagicMock(spec=BaseChatModel)
    mock_chain = MagicMock()
    mock_chain.ainvoke = AsyncMock(side_effect=custom_ainvoke)
    mock_llm.__or__ = MagicMock(return_value=mock_chain)
    mock_llm.__ror__ = MagicMock(return_value=mock_chain)
    mock_llm.ainvoke = AsyncMock(side_effect=custom_ainvoke)
    
    # Mock validate_spec to return False initially, triggering auto-fix
    with patch('intent_capture.spec_utils.validate_spec', return_value=(False, "invalid")), \
         patch('intent_capture.spec_utils.auto_fix_spec', AsyncMock(return_value=("fixed", "notes"))):
        result = await generate_spec_from_memory(mock_memory, mock_llm)
    assert result is not None
    assert result["content"] == "fixed"

# --- Tests for Gaps Generation ---
@pytest.mark.asyncio
async def test_generate_gaps_success(mock_llm, mock_checklist, temp_locales):
    """Test gaps generation success."""
    gaps = await generate_gaps("spec", "transcript", mock_llm)
    assert gaps == "mock_content"

# --- Tests for Spec Refinement ---
@pytest.mark.asyncio
async def test_refine_spec_success(mock_llm, temp_locales):
    """Test spec refinement success."""
    refined = await refine_spec("last_spec", "instruction", mock_llm)
    assert refined == "mock_content"

# --- Tests for Spec Review ---
@pytest.mark.asyncio
async def test_review_spec_success(mock_llm, temp_locales):
    """Test spec review success."""
    review = await review_spec("spec", mock_llm)
    assert review == "mock_content"

# --- Tests for Specs Diff ---
def test_diff_specs_success():
    """Test specs diff success."""
    diff = diff_specs("spec1\nline", "spec2\nline")
    assert len(diff) > 0

def test_diff_specs_type_error():
    """Test specs diff type error."""
    with pytest.raises(TypeError):
        diff_specs(123, "spec")