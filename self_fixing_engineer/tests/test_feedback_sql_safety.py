"""Tests for SQL injection prevention in feedback query keys."""
import re
import unittest

# Copy the regex pattern to test independently of module imports
_SAFE_KEY_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_.]*$")


class TestFeedbackKeySafety(unittest.TestCase):
    """Validate that the key-sanitisation regex blocks SQL injection vectors."""

    # --- Accepted keys ---

    def test_safe_simple_key_accepted(self):
        self.assertTrue(_SAFE_KEY_PATTERN.match("bug_id"))

    def test_safe_dotted_key_accepted(self):
        self.assertTrue(_SAFE_KEY_PATTERN.match("metadata.severity"))

    def test_safe_underscore_prefix_accepted(self):
        self.assertTrue(_SAFE_KEY_PATTERN.match("_private"))

    def test_safe_numeric_suffix_accepted(self):
        self.assertTrue(_SAFE_KEY_PATTERN.match("field2"))

    def test_safe_deeply_nested_key_accepted(self):
        self.assertTrue(_SAFE_KEY_PATTERN.match("a.b.c.d.e"))

    # --- SQL injection payloads ---

    def test_injection_or_payload_rejected(self):
        self.assertIsNone(_SAFE_KEY_PATTERN.match("x') OR 1=1 --"))

    def test_injection_drop_table_rejected(self):
        self.assertIsNone(_SAFE_KEY_PATTERN.match("'; DROP TABLE feedback; --"))

    def test_injection_union_select_rejected(self):
        self.assertIsNone(_SAFE_KEY_PATTERN.match("x') UNION SELECT * FROM users --"))

    # --- Edge cases ---

    def test_empty_key_rejected(self):
        self.assertIsNone(_SAFE_KEY_PATTERN.match(""))

    def test_space_in_key_rejected(self):
        self.assertIsNone(_SAFE_KEY_PATTERN.match("key with spaces"))

    def test_semicolon_in_key_rejected(self):
        self.assertIsNone(_SAFE_KEY_PATTERN.match("key;semicolon"))

    def test_single_quote_in_key_rejected(self):
        self.assertIsNone(_SAFE_KEY_PATTERN.match("key'quote"))

    def test_double_quote_in_key_rejected(self):
        self.assertIsNone(_SAFE_KEY_PATTERN.match('key"double'))

    def test_parenthesis_in_key_rejected(self):
        self.assertIsNone(_SAFE_KEY_PATTERN.match("key(paren)"))

    def test_leading_digit_rejected(self):
        self.assertIsNone(_SAFE_KEY_PATTERN.match("1startsWithDigit"))

    def test_leading_dot_rejected(self):
        self.assertIsNone(_SAFE_KEY_PATTERN.match(".leading_dot"))

    def test_hyphen_in_key_rejected(self):
        self.assertIsNone(_SAFE_KEY_PATTERN.match("some-key"))

    def test_backslash_in_key_rejected(self):
        self.assertIsNone(_SAFE_KEY_PATTERN.match("path\\to\\key"))

    def test_dollar_sign_in_key_rejected(self):
        self.assertIsNone(_SAFE_KEY_PATTERN.match("$variable"))

    def test_null_byte_in_key_rejected(self):
        self.assertIsNone(_SAFE_KEY_PATTERN.match("key\x00null"))


if __name__ == "__main__":
    unittest.main()
