
import json
import os
import unittest
from unittest.mock import patch
from jinja2 import Environment, FileSystemLoader, select_autoescape

# Setup Jinja2 environment
template_dir = 'prompts'
env = Environment(
    loader=FileSystemLoader(template_dir),
    autoescape=select_autoescape(['html', 'xml'])
)
env.filters['join_list'] = lambda l: '\n'.join([f'- {item}' for item in l])

# Mock dependencies from clarifier_user_prompt.py
patch_redact_sensitive = patch('clarifier_user_prompt.redact_sensitive', side_effect=lambda x: x.replace('secret', '[REDACTED_SECRET]').replace('user@example.com', '[REDACTED_EMAIL]').replace('123-45-6789', '[REDACTED_SSN]'))
mock_redact_sensitive = patch_redact_sensitive.start()

patch_log_action = patch('clarifier_user_prompt.log_action', new=AsyncMock())
mock_log_action = patch_log_action.start()

class TestClarifierTemplatesRegulated(unittest.TestCase):
    def setUp(self):
        self.feedback_template = env.get_template('feedback_prompt.j2')
        self.doc_format_template = env.get_template('doc_format_question.j2')
        self.clarification_template = env.get_template('clarification_prompt.j2')

    def tearDown(self):
        patch_redact_sensitive.stop()
        patch_log_action.stop()

    def test_feedback_prompt_first_cycle(self):
        """Test feedback prompt for first cycle with comments."""
        params = {
            'cycle_count': 1,
            'allow_comments': True,
            'ask_rationale': True
        }
        rendered = self.feedback_template.render(**params)
        self.assertIn("Welcome to your first feedback cycle!", rendered)
        self.assertIn('"score": <float, 0 to 1>', rendered)
        self.assertIn('"comments":', rendered)
        self.assertIn('"rationale":', rendered)
        self.assertIn('"improvement_suggestions":', rendered)

    def test_feedback_prompt_pii_redaction(self):
        """Test feedback prompt with PII in comments."""
        params = {
            'cycle_count': 2,
            'prev_issues': ['Issue with secret API', 'user@example.com'],
            'allow_comments': True,
            'ask_rationale': True
        }
        with patch('clarifier_user_prompt.redact_sensitive') as mock_redact:
            mock_redact.side_effect = lambda x: x.replace('secret', '[REDACTED_SECRET]').replace('user@example.com', '[REDACTED_EMAIL]')
            rendered = self.feedback_template.render(**params)
        self.assertIn("[REDACTED_SECRET]", rendered)
        self.assertIn("[REDACTED_EMAIL]", rendered)
        self.assertNotIn("secret API", rendered)
        self.assertNotIn("user@example.com", rendered)
        mock_redact.assert_called()

    def test_doc_format_prompt_recommendations(self):
        """Test doc format prompt with recommendations and previous formats."""
        params = {
            'cycle_count': 2,
            'prev_doc_formats': ['Markdown', 'PDF'],
            'recommended_formats': ['Sphinx', 'OpenAPI']
        }
        rendered = self.doc_format_template.render(**params)
        self.assertIn("Cycle 2: Documentation Format Preferences", rendered)
        self.assertIn("- Markdown", rendered)
        self.assertIn("- PDF", rendered)
        self.assertIn("- Sphinx", rendered)
        self.assertIn("- OpenAPI", rendered)
        self.assertIn('["Markdown", "OpenAPI", "PDF"]', rendered)

    def test_doc_format_prompt_compliance(self):
        """Test doc format prompt for regulatory compliance formats."""
        params = {
            'cycle_count': 1,
            'custom_focus': 'Regulatory-compliant formats (e.g., PDF/A for FDA submissions)'
        }
        rendered = self.doc_format_template.render(**params)
        self.assertIn("Regulatory-compliant formats", rendered)
        self.assertIn('["Markdown", "OpenAPI", "PDF"]', rendered)

    def test_clarification_prompt_ambiguities(self):
        """Test clarification prompt with ambiguities and scores."""
        params = {
            'cycle_count': 2,
            'ambiguities': ['Feature X unclear', 'Secret API key usage'],
            'ambiguity_scores': [8, 10],
            'clarification_guidelines': 'Provide detailed explanations.'
        }
        rendered = self.clarification_template.render(**params)
        self.assertIn("Cycle 2: Requirement Clarification", rendered)
        self.assertIn("- Feature X unclear (Impact Score: 8)", rendered)
        self.assertIn("- Secret API key usage (Impact Score: 10)", rendered)
        self.assertIn("Provide detailed explanations.", rendered)
        self.assertIn('"Ambiguity 1": "Your clarification for Ambiguity 1."', rendered)

    def test_clarification_prompt_pii_redaction(self):
        """Test clarification prompt with PII in ambiguities."""
        params = {
            'ambiguities': ['Feature requires SSN 123-45-6789', 'Email user@example.com'],
            'prev_clarifications': ['Previous clarification with secret']
        }
        with patch('clarifier_user_prompt.redact_sensitive') as mock_redact:
            mock_redact.side_effect = lambda x: x.replace('secret', '[REDACTED_SECRET]').replace('user@example.com', '[REDACTED_EMAIL]').replace('123-45-6789', '[REDACTED_SSN]')
            rendered = self.clarification_template.render(**params)
        self.assertIn("[REDACTED_SSN]", rendered)
        self.assertIn("[REDACTED_EMAIL]", rendered)
        self.assertIn("[REDACTED_SECRET]", rendered)
        mock_redact.assert_called()

    def test_feedback_prompt_invalid_params(self):
        """Test feedback prompt with missing parameters."""
        rendered = self.feedback_template.render()
        self.assertIn("Cycle N/A Feedback:", rendered)
        self.assertIn('"score": <float, 0 to 1>', rendered)
        self.assertNotIn('"comments":', rendered)
        self.assertNotIn('"rationale":', rendered)

    def test_audit_logging(self):
        """Test that rendering triggers audit logging."""
        params = {
            'cycle_count': 1,
            'allow_comments': True,
            'ask_rationale': True
        }
        self.feedback_template.render(**params)
        mock_log_action.assert_called_with("Prompt Rendered", {
            "template": "feedback_prompt.j2", "params": params, "user_id": None
        })

    def test_compliance_warning(self):
        """Test adding compliance warning to templates."""
        params = {
            'cycle_count': 1,
            'compliance_warning': True
        }
        rendered = self.clarification_template.render(**params)
        self.assertIn("WARNING: Avoid including personal or sensitive information", rendered)

if __name__ == '__main__':
    unittest.main()
