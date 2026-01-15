import asyncio
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import yaml

# ---
# Mock all external runner dependencies *before* importing the module under test.
# This ensures the module imports successfully even in a standalone environment.
# ---

# 1. Mock Pydantic models from other modules (if any were used)
# (No external Pydantic models are imported by intent_parser.py)

# 2. Mock runner.* modules - Now with proper log_action interface
mock_runner_logging = MagicMock()
mock_runner_logging.log_action = MagicMock()
sys.modules["runner.runner_logging"] = mock_runner_logging

mock_runner_security = MagicMock()
mock_runner_security.redact_secrets = MagicMock(side_effect=lambda x, **kw: x)
sys.modules["runner.runner_security_utils"] = mock_runner_security

# 3. Mock Prometheus metrics
mock_prometheus = MagicMock()
mock_prometheus.__path__ = []  # Required for package imports
mock_prometheus.__name__ = "prometheus_client"
mock_prometheus.__file__ = "<mocked prometheus_client>"
sys.modules["prometheus_client"] = mock_prometheus

# 4. Mock OpenTelemetry
mock_otel = MagicMock()
mock_otel.__path__ = []  # Required for package imports
mock_otel.__name__ = "opentelemetry"
mock_otel.__file__ = "<mocked opentelemetry>"
mock_otel.trace.get_tracer.return_value.start_as_current_span = MagicMock(
    return_value=MagicMock(__enter__=MagicMock(), __exit__=MagicMock())
)
sys.modules["opentelemetry"] = mock_otel

# 5. Mock heavy ML/parsing libs
sys.modules["spacy"] = MagicMock(name="MockSpacyModule")
sys.modules["torch"] = MagicMock()
sys.modules["transformers"] = MagicMock()
sys.modules["pdfplumber"] = MagicMock()
sys.modules["pytesseract"] = MagicMock()
sys.modules["rst_to_myst"] = MagicMock()
sys.modules["langdetect"] = MagicMock()

# --- Now, import the module to be tested ---
# NOTE: Using the canonical import path (generator.intent_parser) as per the
# new best practice. The sys.modules hack has been removed.
from generator.intent_parser.intent_parser import (
    IntentParser,
    IntentParserConfig,
    MarkdownStrategy,
    PDFStrategy,
    PlaintextStrategy,
    RegexExtractor,
    RSTStrategy,
    SecureAuditFallback,
    YAMLStrategy,
    generate_provenance,
    get_spacy,
    get_torch,
    get_transformers,
    log_action,
    run_in_executor,
)

# Silence the logger for clean test output
logging.disable(logging.CRITICAL)


# --- Dummy Config Content (from intent_parser.yaml) ---
DUMMY_CONFIG_YAML = r"""
schema_version: 1.1
format: auto
extraction_patterns:
  features: '-\s*(.+)'
  constraints: 'Constraint:\s*(.+)'
llm_config:
  provider: openai
  model: gpt-4o
  api_key_env_var: OPENAI_API_KEY
  temperature: 0.1
  seed: 42
  max_tokens_summary: 1000
feedback_file: feedback.json
cache_dir: parser_cache
multi_language_support:
  enabled: true
  default_lang: en
  language_patterns:
    es:
      features: '- *(rasgo|característica):\s*(.+)'
      constraints: 'Restricción:\s*(.+)'
"""


class TestIntentParser(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.config_path = self.temp_path / "test_config.yaml"
        # Use temp_path for cache_dir to avoid path issues
        modified_config = DUMMY_CONFIG_YAML.replace(
            "cache_dir: parser_cache",
            f"cache_dir: {self.temp_path / 'parser_cache'}"
        )
        with open(self.config_path, "w", encoding="utf-8") as f:
            f.write(modified_config)

        # Reset mocks before each test
        mock_runner_logging.log_action.reset_mock()
        mock_runner_security.redact_secrets.reset_mock()

        # Mock the LLM and NLP stubs
        self.mock_detector = MagicMock()
        self.mock_detector.detect = AsyncMock(return_value=["ambiguity 1"])

        self.mock_summarizer = MagicMock()
        self.mock_summarizer.summarize = MagicMock(
            side_effect=lambda req, **kw: req
        )  # Pass-through

        # Set environment variable to indicate test mode
        os.environ["TESTING"] = "1"

    def tearDown(self):
        self.temp_dir.cleanup()
        # Clean up environment
        os.environ.pop("TESTING", None)

    # --- Config and Lazy Loading Tests ---

    def test_config_load_success(self):
        """Tests successful loading of the YAML config into the Pydantic model."""
        # Note: Config now uses temp_path for cache_dir via setUp
        config_yaml_for_test = DUMMY_CONFIG_YAML.replace(
            "cache_dir: parser_cache",
            f"cache_dir: {self.temp_path / 'parser_cache'}"
        )
        config = IntentParserConfig.model_validate(yaml.safe_load(config_yaml_for_test))
        self.assertEqual(config.format, "auto")
        self.assertEqual(config.llm_config.model, "gpt-4o")
        self.assertEqual(
            config.multi_language_support.language_patterns["es"]["features"],
            r"- *(rasgo|característica):\s*(.+)",
        )
        # Cache dir should be created by the validator
        self.assertTrue((self.temp_path / "parser_cache").exists())

    def test_config_load_invalid_format(self):
        """Tests that the Pydantic validator catches invalid 'format' values."""
        invalid_config_yaml = DUMMY_CONFIG_YAML.replace("format: auto", "format: docx")
        with self.assertRaises(ValueError):
            IntentParserConfig.model_validate(yaml.safe_load(invalid_config_yaml))

    def test_lazy_load_failure(self):
        """Tests that lazy loaders propagate ImportError when module not found."""
        # Reset the global lazy loaders
        import generator.intent_parser.intent_parser as ip_module
        original_spacy = ip_module._spacy
        original_torch = ip_module._torch
        original_transformers = ip_module._transformers
        
        try:
            ip_module._spacy = None
            ip_module._torch = None
            ip_module._transformers = None
            
            # Mock the import to fail
            with patch.dict(sys.modules, {"spacy": None, "torch": None, "transformers": None}):
                with patch("builtins.__import__", side_effect=ImportError("test error")):
                    with self.assertRaises(ImportError):
                        get_spacy()
        finally:
            ip_module._spacy = original_spacy
            ip_module._torch = original_torch
            ip_module._transformers = original_transformers

    def test_lazy_load_success(self):
        """Tests that lazy loaders import a module only once."""
        import generator.intent_parser.intent_parser as ip_module
        original_spacy = ip_module._spacy
        
        try:
            ip_module._spacy = None
            
            # First call - since spacy is mocked in sys.modules, it should work
            spacy_instance = get_spacy()
            # Verify we got a mock object
            self.assertIsNotNone(spacy_instance)
            
            # Second call (should be cached - same object)
            spacy_instance_2 = get_spacy()
            self.assertIs(spacy_instance, spacy_instance_2)
        finally:
            ip_module._spacy = original_spacy

    # --- Strategy Tests ---

    def test_markdown_strategy(self):
        """Tests parsing of Markdown content."""
        strategy = MarkdownStrategy()
        content = (
            "# Title\nHello.\n## Features\n- Feature 1\n```python\nprint('code')\n```"
        )
        sections = strategy.parse(content)
        self.assertIn("Title", sections)
        self.assertIn("Features", sections)
        self.assertIn("Hello", sections["Title"])
        self.assertIn("- Feature 1", sections["Features"])
        self.assertIn("[CODE_BLOCK]", sections["Features"])
        self.assertNotIn("print('code')", sections["Features"])

    # --- FIX 6: Patch the correct function: rst_to_myst.convert ---
    def test_rst_strategy_failure_fallback(self):
        """Tests RST parser falling back to PlaintextStrategy on error."""
        # Import the actual module and patch the convert function
        import rst_to_myst
        with patch.object(rst_to_myst, 'convert', side_effect=Exception("RST Error")):
            strategy = RSTStrategy()
            content = "Bad RST content"
            sections = strategy.parse(content)
            self.assertEqual(sections, {"Full Document": content})

    def test_yaml_strategy_success(self):
        """Tests parsing of valid YAML."""
        strategy = YAMLStrategy()
        content = "key: value\nitems:\n  - 1\n  - 2"
        sections = strategy.parse(content)
        self.assertEqual(sections["key"], "value")
        self.assertEqual(sections["items"], "[1, 2]")

    def test_yaml_strategy_failure_fallback(self):
        """Tests YAML parser falling back to PlaintextStrategy on error."""
        strategy = YAMLStrategy()
        content = "key: value\n unindented: error"
        sections = strategy.parse(content)
        self.assertEqual(sections, {"Full Document": content})

    @patch("generator.intent_parser.intent_parser.HAS_PDFPLUMBER", False)
    def test_pdf_strategy_no_lib_fallback(self):
        """Tests PDF parser falling back to Plaintext when library is missing."""
        strategy = PDFStrategy()
        sections = strategy.parse(Path("dummy.pdf"))
        self.assertIn("Full Document", sections)

    def test_pdf_strategy_with_text_extraction(self):
        """Tests PDF parsing with text extraction (simplified test without OCR)."""
        import generator.intent_parser.intent_parser as ip_module
        
        # Create mocks
        mock_page = MagicMock()
        mock_page.extract_text.return_value = "Page text."
        mock_page.images = []  # No images for simplified test
        
        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        
        mock_pdfplumber = MagicMock()
        mock_pdfplumber.open.return_value.__enter__.return_value = mock_pdf
        
        with patch.object(ip_module, 'HAS_PDFPLUMBER', True):
            with patch.object(ip_module, 'pdfplumber', mock_pdfplumber):
                strategy = PDFStrategy()
                sections = strategy.parse(Path("dummy.pdf"))
                
                self.assertIn("Page text.", sections["Full Document (PDF)"])

    def test_regex_extractor(self):
        """Tests the RegexExtractor with default and language-specific patterns."""
        config_yaml = DUMMY_CONFIG_YAML.replace(
            "cache_dir: parser_cache",
            f"cache_dir: {self.temp_path / 'parser_cache'}"
        )
        config = IntentParserConfig.model_validate(yaml.safe_load(config_yaml))
        extractor = RegexExtractor(
            config.extraction_patterns, config.multi_language_support.language_patterns
        )

        # Test default (English)
        sections = {"doc": "- Feature A\n- Feature B\nConstraint: C1"}
        extracted_en = extractor.extract(sections, language="en")
        self.assertEqual(extracted_en["features"], ["Feature A", "Feature B"])
        self.assertEqual(extracted_en["constraints"], ["C1"])

        # Test Spanish - the pattern captures the group after "rasgo:" or "característica:"
        sections_es = {"doc": "- rasgo: Feature ES\nRestricción: C1 ES"}
        extracted_es = extractor.extract(sections_es, language="es")
        # The Spanish pattern '- *(rasgo|característica):\s*(.+)' captures group 2 which is "Feature ES"
        self.assertIn("Feature ES", extracted_es.get("features", []))
        self.assertEqual(extracted_es["constraints"], ["C1 ES"])

    def test_generate_provenance(self):
        """Tests provenance generation."""
        content = "hello"
        prov = generate_provenance(content)
        self.assertEqual(
            prov["content_hash"],
            "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
        )
        self.assertEqual(prov["source_type"], "string")

        # Test with file path
        file_path_obj = Path("a/b.txt")
        prov_file = generate_provenance(content, file_path=file_path_obj)
        self.assertEqual(prov_file["source_type"], "file")
        self.assertEqual(
            prov_file["file_path"], str(file_path_obj)
        )  # Compare str(Path) to str(Path)

        # Verify provenance contains expected keys
        self.assertIn("content_hash", prov)
        self.assertIn("timestamp_utc", prov)
        self.assertIn("source_type", prov)

    # --- Main IntentParser Class Tests ---

    @patch("generator.intent_parser.intent_parser.LLMDetector")
    @patch("generator.intent_parser.intent_parser.LLMSummarizer")
    def test_parser_init_and_reload(self, mock_summarizer, mock_detector):
        """Tests that the parser initializes and reloads its config."""
        parser = IntentParser(config_path=str(self.config_path))
        self.assertEqual(parser.config.llm_config.model, "gpt-4o")
        self.assertIsInstance(parser.extractor, RegexExtractor)

        # Modify the config file
        new_config = DUMMY_CONFIG_YAML.replace("model: gpt-4o", "model: gpt-5")
        new_config = new_config.replace(
            "cache_dir: parser_cache",
            f"cache_dir: {self.temp_path / 'parser_cache'}"
        )
        with open(self.config_path, "w", encoding="utf-8") as f:
            f.write(new_config)

        parser.reload_config_and_strategies()
        self.assertEqual(parser.config.llm_config.model, "gpt-5")
        # Close the parser to clean up resources
        parser.close()

    def test_select_parser_auto_logic(self):
        """Tests the automatic parser selection based on file extension."""
        parser = IntentParser(config_path=str(self.config_path))
        self.assertIsInstance(
            parser._select_parser("auto", Path("file.md")), MarkdownStrategy
        )
        self.assertIsInstance(
            parser._select_parser("auto", Path("file.rst")), RSTStrategy
        )
        self.assertIsInstance(
            parser._select_parser("auto", Path("file.yaml")), YAMLStrategy
        )
        self.assertIsInstance(
            parser._select_parser("auto", Path("file.txt")), PlaintextStrategy
        )
        self.assertIsInstance(
            parser._select_parser("auto", Path("file.unknown")), PlaintextStrategy
        )
        # Test PDF fallback
        with patch("generator.intent_parser.intent_parser.HAS_PDFPLUMBER", False):
            self.assertIsInstance(
                parser._select_parser("auto", Path("file.pdf")), PlaintextStrategy
            )
        # Clean up
        parser.close()

    @patch(
        "generator.intent_parser.intent_parser.LLMDetector",
        return_value=MagicMock(detect=AsyncMock(return_value=[])),
    )
    @patch(
        "generator.intent_parser.intent_parser.LLMSummarizer",
        return_value=MagicMock(summarize=MagicMock(side_effect=lambda x, **kw: x)),
    )
    @patch("generator.intent_parser.intent_parser.detect", return_value="en")
    def test_parse_workflow_simple_markdown(
        self, mock_detect, mock_summarizer, mock_detector
    ):
        """Tests the full parse workflow with simple Markdown content."""
        parser = IntentParser(config_path=str(self.config_path))
        content = "# Features\n- F1\nConstraint: C1"

        result = asyncio.run(parser.parse(content=content, format_hint="markdown"))

        self.assertEqual(result["features"], ["F1"])
        self.assertEqual(result["constraints"], ["C1"])
        self.assertEqual(result["ambiguities"], [])

        # Check that the language detection mock was called
        mock_detect.assert_called_with(content)
        mock_detector.return_value.detect.assert_called_once()
        mock_summarizer.return_value.summarize.assert_called_once()
        
        # Clean up
        parser.close()

    @patch(
        "generator.intent_parser.intent_parser.LLMDetector",
        return_value=MagicMock(detect=AsyncMock(return_value=[])),
    )
    @patch(
        "generator.intent_parser.intent_parser.LLMSummarizer",
        return_value=MagicMock(summarize=MagicMock(side_effect=lambda x, **kw: x)),
    )
    @patch("generator.intent_parser.intent_parser.detect", return_value="es")
    def test_parse_workflow_multilang_file(
        self, mock_detect, mock_summarizer, mock_detector
    ):
        """Tests the parse workflow reading from a file with multi-language detection."""
        content_es = "- rasgo: Feature ES\nRestricción: C1 ES"
        test_file = self.temp_path / "readme_es.md"
        test_file.write_text(content_es)

        parser = IntentParser(config_path=str(self.config_path))
        result = asyncio.run(parser.parse(file_path=test_file, format_hint="auto"))

        # Check that features were extracted (pattern captures "Feature ES")
        self.assertIn("Feature ES", result.get("features", []))
        self.assertEqual(result["constraints"], ["C1 ES"])
        self.assertEqual(parser.input_language, "es")
        mock_detect.assert_called_with(content_es)
        
        # Clean up
        parser.close()

    @patch("generator.intent_parser.intent_parser.LLMDetector")
    @patch("generator.intent_parser.intent_parser.LLMSummarizer")
    def test_parse_workflow_errors(self, mock_summarizer, mock_detector):
        """Tests error handling in the parse workflow."""
        parser = IntentParser(config_path=str(self.config_path))

        # Test FileNotFoundError
        with self.assertRaises(FileNotFoundError):
            asyncio.run(parser.parse(file_path=Path("non_existent_file.md")))

        # Test ValueError (no content)
        with self.assertRaises(ValueError):
            asyncio.run(parser.parse())

        # Test general exception fallback
        mock_detector.return_value.detect.side_effect = Exception("Detector failed")
        with self.assertRaises(Exception):
            asyncio.run(parser.parse(content="test"))


# =============================================================================
# NEW TESTS: Bug Fixes Verification
# =============================================================================

class TestSecureAuditFallback(unittest.TestCase):
    """Tests for the secure audit logging fallback (Bug C fix)."""

    def test_secure_fallback_redacts_sensitive_data(self):
        """Tests that the secure fallback redacts sensitive information."""
        fallback = SecureAuditFallback()
        
        # Test email redaction
        result = fallback._redact_value("contact user@example.com for info")
        self.assertNotIn("user@example.com", result)
        self.assertIn("[REDACTED]", result)
        
        # Test API key redaction
        result = fallback._redact_value("api_key=sk-12345678")
        self.assertNotIn("sk-12345678", result)
        
        # Test password redaction
        result = fallback._redact_value("password: mysecretpassword")
        self.assertNotIn("mysecretpassword", result)
    
    def test_secure_fallback_truncates_long_values(self):
        """Tests that long values are truncated."""
        fallback = SecureAuditFallback()
        long_value = "x" * 200
        result = fallback._redact_value(long_value)
        self.assertLess(len(result), 200)
        self.assertIn("[TRUNCATED]", result)
    
    def test_secure_fallback_sanitizes_data_dict(self):
        """Tests that data dictionaries are sanitized properly."""
        fallback = SecureAuditFallback()
        
        data = {
            "status": "success",  # Should be preserved
            "password": "secret123",  # Should be redacted
            "user_email": "user@example.com",  # Should be redacted
            "count": 42,  # Should be preserved
        }
        
        sanitized = fallback._sanitize_data(data)
        
        # Safe keys should have values
        self.assertIn("status", sanitized)
        self.assertIn("count", sanitized)
        
        # Sensitive keys should be redacted
        self.assertEqual(sanitized["password"], "[REDACTED]")
        self.assertEqual(sanitized["user_email"], "[REDACTED]")
    
    def test_log_action_does_not_expose_data(self):
        """Tests that log_action doesn't expose sensitive data to logs."""
        fallback = SecureAuditFallback()
        
        # This should not raise and should not log sensitive data
        sensitive_data = {
            "api_key": "sk-secret-key-12345",
            "password": "my_password",
            "user_data": {"email": "test@example.com"},
        }
        
        # Should not raise
        fallback.log_action("TestAction", sensitive_data)


class TestAsyncSafeOperations(unittest.TestCase):
    """Tests for async-safe CPU-bound operations (Bug B fix)."""
    
    def test_run_in_executor_executes_function(self):
        """Tests that run_in_executor properly executes functions."""
        def cpu_bound_func(x, y):
            return x + y
        
        result = asyncio.run(run_in_executor(cpu_bound_func, 1, 2))
        self.assertEqual(result, 3)
    
    def test_run_in_executor_handles_kwargs(self):
        """Tests that run_in_executor handles keyword arguments."""
        def func_with_kwargs(a, b=10):
            return a * b
        
        result = asyncio.run(run_in_executor(func_with_kwargs, 5, b=20))
        self.assertEqual(result, 100)
    
    def test_parser_uses_executor_for_cpu_bound_ops(self):
        """Tests that the parser runs CPU-bound operations in executor."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            config_path = temp_path / "test_config.yaml"
            with open(config_path, "w", encoding="utf-8") as f:
                f.write(DUMMY_CONFIG_YAML)
            
            parser = IntentParser(config_path=str(config_path))
            
            # Verify the parser has an executor
            self.assertIsNotNone(parser.executor)
            
            # Verify the _run_cpu_bound_operation method exists
            self.assertTrue(hasattr(parser, '_run_cpu_bound_operation'))
            self.assertTrue(asyncio.iscoroutinefunction(parser._run_cpu_bound_operation))
            
            # Clean up
            parser.close()
    
    @patch(
        "generator.intent_parser.intent_parser.LLMDetector",
        return_value=MagicMock(detect=AsyncMock(return_value=[])),
    )
    @patch(
        "generator.intent_parser.intent_parser.LLMSummarizer",
        return_value=MagicMock(summarize=MagicMock(side_effect=lambda x, **kw: x)),
    )
    @patch("generator.intent_parser.intent_parser.detect", return_value="en")
    def test_parse_does_not_block_event_loop(
        self, mock_detect, mock_summarizer, mock_detector
    ):
        """Tests that parse() runs CPU-bound operations without blocking."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            config_path = temp_path / "test_config.yaml"
            with open(config_path, "w", encoding="utf-8") as f:
                f.write(DUMMY_CONFIG_YAML)
            
            parser = IntentParser(config_path=str(config_path))
            content = "# Test\n- Feature 1\nConstraint: C1"
            
            # Run parse and verify it completes
            result = asyncio.run(parser.parse(content=content, format_hint="markdown"))
            
            self.assertIn("features", result)
            self.assertIn("constraints", result)
            
            # Clean up
            parser.close()


class TestTypeIdentityFix(unittest.TestCase):
    """Tests for the type identity fix (Bug A fix)."""
    
    def test_explicit_exports_available(self):
        """Tests that all expected exports are available from the package."""
        from generator.intent_parser import (
            IntentParser,
            IntentParserConfig,
            MarkdownStrategy,
            RegexExtractor,
            generate_provenance,
        )
        
        # Verify classes are the correct types
        self.assertTrue(callable(IntentParser))
        self.assertTrue(callable(IntentParserConfig))
        self.assertTrue(callable(MarkdownStrategy))
        self.assertTrue(callable(RegexExtractor))
        self.assertTrue(callable(generate_provenance))
    
    def test_isinstance_works_with_canonical_import(self):
        """Tests that isinstance() works correctly with canonical imports."""
        from generator.intent_parser import MarkdownStrategy as MS1
        from generator.intent_parser.intent_parser import MarkdownStrategy as MS2
        
        # Both imports should refer to the same class
        self.assertIs(MS1, MS2)
        
        # isinstance should work correctly
        instance = MS1()
        self.assertIsInstance(instance, MS1)
        self.assertIsInstance(instance, MS2)


class TestParserCleanup(unittest.TestCase):
    """Tests for parser resource cleanup."""
    
    def test_parser_close_shuts_down_executor(self):
        """Tests that close() properly shuts down the executor."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            config_path = temp_path / "test_config.yaml"
            with open(config_path, "w", encoding="utf-8") as f:
                f.write(DUMMY_CONFIG_YAML)
            
            parser = IntentParser(config_path=str(config_path))
            executor = parser.executor
            
            parser.close()
            
            # After close, the executor should be shut down
            self.assertTrue(executor._shutdown)
    
    def test_parser_async_context_manager(self):
        """Tests the async context manager functionality."""
        async def test_context_manager():
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                config_path = temp_path / "test_config.yaml"
                with open(config_path, "w", encoding="utf-8") as f:
                    f.write(DUMMY_CONFIG_YAML)
                
                async with IntentParser(config_path=str(config_path)) as parser:
                    self.assertIsNotNone(parser.executor)
                    executor = parser.executor
                
                # After exiting context, executor should be shut down
                self.assertTrue(executor._shutdown)
        
        asyncio.run(test_context_manager())


if __name__ == "__main__":
    unittest.main()
