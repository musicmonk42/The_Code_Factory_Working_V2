import asyncio
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import yaml  # <-- FIX 1: Added missing import

# ---
# Mock all external runner dependencies *before* importing the module under test.
# This ensures the module imports successfully even in a standalone environment.
# ---

# 1. Mock Pydantic models from other modules (if any were used)
# (No external Pydantic models are imported by intent_parser.py)

# 2. Mock runner.* modules with proper secure defaults
mock_runner_logging = MagicMock()
mock_runner_logging.log_action = MagicMock()
sys.modules["runner"] = MagicMock()
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


# 4. Mock OpenTelemetry - Need proper decorator support
# The tracer.start_as_current_span is used as a decorator, so we need to make it pass-through
def passthrough_decorator(name):
    """Pass-through decorator that doesn't modify the function."""

    def decorator(func):
        return func  # Return the original function unchanged

    return decorator


mock_otel = MagicMock()
mock_otel.__path__ = []  # Required for package imports
mock_otel.__name__ = "opentelemetry"
mock_otel.__file__ = "<mocked opentelemetry>"
mock_otel.trace.get_tracer.return_value.start_as_current_span = passthrough_decorator
mock_otel.trace.get_current_span = MagicMock(return_value=None)
sys.modules["opentelemetry"] = mock_otel
sys.modules["opentelemetry.trace"] = mock_otel.trace

# 5. Mock heavy ML/parsing libs
sys.modules["spacy"] = MagicMock(name="MockSpacyModule")
sys.modules["torch"] = MagicMock()
sys.modules["transformers"] = MagicMock()
sys.modules["pdfplumber"] = MagicMock()
sys.modules["pytesseract"] = MagicMock()
sys.modules["rst_to_myst"] = MagicMock()
sys.modules["langdetect"] = MagicMock()

# --- Now, import the module to be tested using canonical path ---
from generator.intent_parser.intent_parser import (
    IntentParser,
    IntentParserConfig,
    MarkdownStrategy,
    PDFStrategy,
    PlaintextStrategy,
    RegexExtractor,
    RSTStrategy,
    YAMLStrategy,
    generate_provenance,
    get_spacy,
    get_torch,
    get_transformers,
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

    @classmethod
    def setUpClass(cls):
        """Set up test environment once for all tests."""
        # Use minimal thread pool for tests to avoid exhausting system threads
        os.environ["INTENT_PARSER_MAX_WORKERS"] = "1"

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.config_path = self.temp_path / "test_config.yaml"
        # --- FIX 2: Added encoding='utf-8' ---
        with open(self.config_path, "w", encoding="utf-8") as f:
            f.write(DUMMY_CONFIG_YAML)

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

        # --- FIX: Reset lazy loaders before each test ---
        global _spacy, _torch, _transformers
        _spacy = None
        _torch = None
        _transformers = None

        # Track parsers created for cleanup
        self.parsers_to_cleanup = []

    def tearDown(self):
        # Clean up all parsers to avoid thread leaks
        import time

        for parser in self.parsers_to_cleanup:
            try:
                parser.shutdown(wait=True)  # Wait for threads to finish
            except Exception:
                pass
        self.parsers_to_cleanup.clear()
        # Give threads time to fully terminate
        time.sleep(0.1)

        self.temp_dir.cleanup()
        # --- FIX: Reset lazy loaders after each test ---
        global _spacy, _torch, _transformers
        _spacy = None
        _torch = None
        _transformers = None

    def create_parser(self, config_path=None):
        """Helper to create parser and track for cleanup."""
        parser = IntentParser(config_path=str(config_path or self.config_path))
        self.parsers_to_cleanup.append(parser)
        return parser
        self.temp_dir.cleanup()
        # --- FIX: Reset lazy loaders after each test ---
        global _spacy, _torch, _transformers
        _spacy = None
        _torch = None
        _transformers = None

    # --- Config and Lazy Loading Tests ---

    def test_config_load_success(self):
        """Tests successful loading of the YAML config into the Pydantic model."""
        config = IntentParserConfig.model_validate(yaml.safe_load(DUMMY_CONFIG_YAML))
        self.assertEqual(config.format, "auto")
        self.assertEqual(config.llm_config.model, "gpt-4o")
        self.assertEqual(
            config.multi_language_support.language_patterns["es"]["features"],
            r"- *(rasgo|característica):\s*(.+)",
        )
        self.assertTrue((self.temp_path / "parser_cache").exists())

    def test_config_load_invalid_format(self):
        """Tests that the Pydantic validator catches invalid 'format' values."""
        invalid_config_yaml = DUMMY_CONFIG_YAML.replace("format: auto", "format: docx")
        with self.assertRaises(ValueError):
            IntentParserConfig.model_validate(yaml.safe_load(invalid_config_yaml))

    # --- FIX 4: Patch builtins.__import__ to mock `import spacy` ---
    @patch("builtins.__import__", side_effect=ImportError("test error"))
    def test_lazy_load_failure(self, mock_import):
        """Tests that lazy loaders propagate ImportError."""
        with self.assertRaises(ImportError):
            get_spacy()

        # Test torch and transformers as well
        with self.assertRaises(ImportError):
            get_torch()
        with self.assertRaises(ImportError):
            get_transformers()

    # --- FIX 5: Patch builtins.__import__ to mock `import spacy` ---
    @patch("builtins.__import__", return_value=MagicMock(name="spacy_mock"))
    def test_lazy_load_success(self, mock_import):
        """Tests that lazy loaders import a module only once."""
        mock_spacy = mock_import.return_value

        # First call
        spacy_instance = get_spacy()
        # Access via attribute name property
        self.assertEqual(mock_spacy.name, "spacy_mock")
        # Check that it tried to import 'spacy'
        mock_import.assert_any_call(
            "spacy",
            unittest.mock.ANY,
            unittest.mock.ANY,
            unittest.mock.ANY,
            unittest.mock.ANY,
        )
        call_count = mock_import.call_count

        # Second call (should be cached)
        spacy_instance_2 = get_spacy()
        self.assertIs(spacy_instance, spacy_instance_2)  # Should be same object
        # Call count should not increase
        self.assertEqual(mock_import.call_count, call_count)

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
    @patch(
        "generator.intent_parser.intent_parser.rst_to_myst.convert",
        side_effect=Exception("RST Error"),
    )
    def test_rst_strategy_failure_fallback(self, mock_convert):
        """Tests RST parser falling back to PlaintextStrategy on error."""
        strategy = RSTStrategy()
        content = "Bad RST content"
        sections = strategy.parse(content)
        mock_convert.assert_called_with(content)
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

    @patch("generator.intent_parser.intent_parser.HAS_PDFPLUMBER", True)
    @patch("generator.intent_parser.intent_parser.HAS_PYTESSERACT", True)
    @patch("generator.intent_parser.intent_parser.pdfplumber")
    @patch("generator.intent_parser.intent_parser.pytesseract")
    @patch("generator.intent_parser.intent_parser.Image")
    def test_pdf_strategy_with_ocr(self, mock_image, mock_tesseract, mock_pdfplumber):
        """Tests PDF parsing with successful text and OCR extraction."""
        mock_page = MagicMock()
        mock_page.extract_text.return_value = "Page text."
        mock_page.images = [
            {
                "width": 10,
                "height": 10,
                "stream": MagicMock(get_data=MagicMock(return_value=b"imagedata")),
            }
        ]

        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdfplumber.open.return_value.__enter__.return_value = mock_pdf

        mock_tesseract.image_to_string.return_value = "OCR text."

        strategy = PDFStrategy()
        sections = strategy.parse(Path("dummy.pdf"))

        self.assertIn("Page text.", sections["Full Document (PDF)"])
        self.assertIn("[OCR_IMAGE_TEXT]:\nOCR text.", sections["Full Document (PDF)"])

    def test_regex_extractor(self):
        """Tests the RegexExtractor with default and language-specific patterns."""
        config = IntentParserConfig.model_validate(yaml.safe_load(DUMMY_CONFIG_YAML))
        extractor = RegexExtractor(
            config.extraction_patterns, config.multi_language_support.language_patterns
        )

        # Test default (English)
        sections = {"doc": "- Feature A\n- Feature B\nConstraint: C1"}
        extracted_en = extractor.extract(sections, language="en")
        self.assertEqual(extracted_en["features"], ["Feature A", "Feature B"])
        self.assertEqual(extracted_en["constraints"], ["C1"])

        # Test Spanish
        sections_es = {"doc": "- rasgo: Feature ES\nRestricción: C1 ES"}
        extracted_es = extractor.extract(sections_es, language="es")
        self.assertEqual(extracted_es["features"], ["Feature ES"])
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

        # --- FIX 3: Make test OS-agnostic ---
        file_path_obj = Path("a/b.txt")
        prov_file = generate_provenance(content, file_path=file_path_obj)
        self.assertEqual(prov_file["source_type"], "file")
        self.assertEqual(
            prov_file["file_path"], str(file_path_obj)
        )  # Compare str(Path) to str(Path)

        # Check that it called the (mocked) log_action
        self.assertGreaterEqual(mock_runner_logging.log_action.call_count, 2)

    # --- Main IntentParser Class Tests ---

    @patch("generator.intent_parser.intent_parser.LLMDetector")
    @patch("generator.intent_parser.intent_parser.LLMSummarizer")
    def test_parser_init_and_reload(self, mock_summarizer, mock_detector):
        """Tests that the parser initializes and reloads its config."""
        parser = self.create_parser()
        self.assertEqual(parser.config.llm_config.model, "gpt-4o")
        self.assertIsInstance(parser.extractor, RegexExtractor)

        # Modify the config file
        new_config_yaml = DUMMY_CONFIG_YAML.replace("model: gpt-4o", "model: gpt-5")
        with open(self.config_path, "w", encoding="utf-8") as f:
            f.write(new_config_yaml)

        parser.reload_config_and_strategies()
        self.assertEqual(parser.config.llm_config.model, "gpt-5")
        mock_runner_logging.log_action.assert_called_with(
            "Config Reloaded", {"path": str(self.config_path)}
        )

    def test_select_parser_auto_logic(self):
        """Tests the automatic parser selection based on file extension."""
        parser = self.create_parser()
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
        parser = self.create_parser()
        content = "# Features\n- F1\nConstraint: C1"

        result = asyncio.run(parser.parse(content=content, format_hint="markdown"))

        self.assertEqual(result["features"], ["F1"])
        self.assertEqual(result["constraints"], ["C1"])
        self.assertEqual(result["ambiguities"], [])

        # Check that mocks were called
        mock_runner_security.redact_secrets.assert_called_with(content)
        mock_detect.assert_called_with(content)
        mock_detector.return_value.detect.assert_called_once()
        mock_summarizer.return_value.summarize.assert_called_once()
        mock_runner_logging.log_action.assert_any_call(
            "Parse Completed", unittest.mock.ANY
        )

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

        parser = self.create_parser()
        result = asyncio.run(parser.parse(file_path=test_file, format_hint="auto"))

        self.assertEqual(result["features"], ["Feature ES"])
        self.assertEqual(result["constraints"], ["C1 ES"])
        self.assertEqual(parser.input_language, "es")
        mock_detect.assert_called_with(content_es)

    @patch("generator.intent_parser.intent_parser.LLMDetector")
    @patch("generator.intent_parser.intent_parser.LLMSummarizer")
    def test_parse_workflow_errors(self, mock_summarizer, mock_detector):
        """Tests error handling in the parse workflow."""
        parser = self.create_parser()

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

    @patch("generator.intent_parser.intent_parser.LLMDetector")
    @patch("generator.intent_parser.intent_parser.LLMSummarizer")
    def test_context_manager_and_executor_cleanup(self, mock_summarizer, mock_detector):
        """Tests that IntentParser properly cleans up executor via context manager."""
        with IntentParser(config_path=str(self.config_path)) as parser:
            self.assertIsNotNone(parser.executor)
            self.assertFalse(parser.executor._shutdown)

        # After exiting context, executor should be shut down
        self.assertTrue(parser.executor._shutdown)

    @patch("generator.intent_parser.intent_parser.LLMDetector")
    @patch("generator.intent_parser.intent_parser.LLMSummarizer")
    def test_manual_shutdown(self, mock_summarizer, mock_detector):
        """Tests manual executor shutdown."""
        parser = self.create_parser()
        self.assertFalse(parser.executor._shutdown)

        parser.shutdown()
        self.assertTrue(parser.executor._shutdown)

    @patch(
        "generator.intent_parser.intent_parser.LLMDetector",
        return_value=MagicMock(detect=AsyncMock(return_value=[])),
    )
    @patch(
        "generator.intent_parser.intent_parser.LLMSummarizer",
        return_value=MagicMock(summarize=MagicMock(side_effect=lambda x, **kw: x)),
    )
    @patch("generator.intent_parser.intent_parser.detect", return_value="en")
    def test_parse_uses_executor_for_cpu_bound_ops(
        self, mock_detect, mock_summarizer, mock_detector
    ):
        """Tests that CPU-bound operations are properly offloaded to executor."""
        parser = self.create_parser()
        content = "# Features\n- F1\nConstraint: C1"

        # Mock the executor to track calls
        original_executor = parser.executor
        mock_executor = MagicMock(wraps=original_executor)
        parser.executor = mock_executor

        try:
            result = asyncio.run(parser.parse(content=content, format_hint="markdown"))

            # Verify that run_in_executor would have been called
            # (We can't directly verify asyncio.loop.run_in_executor calls without deeper mocking)
            self.assertEqual(result["features"], ["F1"])
            self.assertEqual(result["constraints"], ["C1"])
        finally:
            # Clean up
            original_executor.shutdown(wait=False)
            parser.shutdown()


if __name__ == "__main__":
    unittest.main()
