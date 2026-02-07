# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Tests for flexible requirements parsing functionality.

This module tests the _parse_requirements_flexible function which handles
various input formats for code generation requirements.
"""
import pytest
from generator.agents.codegen_agent.codegen_prompt import _parse_requirements_flexible


class TestParseRequirementsFlexible:
    """Test suite for _parse_requirements_flexible function."""
    
    def test_parse_dict_with_features_passthrough(self):
        """Test that dict with 'features' list passes through unchanged."""
        requirements = {
            'features': ['Build REST API', 'Add authentication'],
            'description': 'A web service'
        }
        result = _parse_requirements_flexible(requirements)
        assert result == requirements
        assert 'features' in result
        assert isinstance(result['features'], list)
        assert len(result['features']) == 2
    
    def test_parse_simple_string(self):
        """Test parsing of simple string requirement."""
        requirements = "Build a REST API with user authentication"
        result = _parse_requirements_flexible(requirements)
        assert 'features' in result
        assert isinstance(result['features'], list)
        assert len(result['features']) == 1
        assert result['features'][0] == requirements
        assert result['description'] == requirements
    
    def test_parse_markdown_bullets(self):
        """Test extraction of features from markdown bullet points."""
        requirements = """
        Requirements:
        - Build a REST API
        - Add user authentication
        - Implement rate limiting
        - Add logging
        """
        result = _parse_requirements_flexible(requirements)
        assert 'features' in result
        assert isinstance(result['features'], list)
        assert len(result['features']) == 4
        assert 'Build a REST API' in result['features']
        assert 'Add user authentication' in result['features']
    
    def test_parse_numbered_list(self):
        """Test extraction of features from numbered list."""
        requirements = """
        Requirements:
        1. Create database schema
        2. Implement CRUD operations
        3. Add API endpoints
        """
        result = _parse_requirements_flexible(requirements)
        assert 'features' in result
        assert isinstance(result['features'], list)
        assert len(result['features']) == 3
        assert 'Create database schema' in result['features']
    
    def test_parse_feature_headers(self):
        """Test extraction from markdown headers."""
        requirements = """
        # Feature: User Management
        # Feature: API Gateway
        # Requirement: Database Setup
        """
        result = _parse_requirements_flexible(requirements)
        assert 'features' in result
        assert isinstance(result['features'], list)
        assert len(result['features']) == 3
        assert 'User Management' in result['features']
        assert 'API Gateway' in result['features']
        assert 'Database Setup' in result['features']
    
    def test_parse_json_string(self):
        """Test parsing of JSON-formatted string."""
        requirements = '{"features": ["API", "Auth", "Tests"], "lang": "python"}'
        result = _parse_requirements_flexible(requirements)
        assert 'features' in result
        assert isinstance(result['features'], list)
        assert len(result['features']) == 3
        assert 'API' in result['features']
    
    def test_parse_multiline_sentences(self):
        """Test parsing sentences separated by periods."""
        requirements = "Create a user service. Add authentication. Implement caching."
        result = _parse_requirements_flexible(requirements)
        assert 'features' in result
        assert isinstance(result['features'], list)
        assert len(result['features']) > 1
    
    def test_parse_empty_string(self):
        """Test handling of empty string."""
        requirements = ""
        result = _parse_requirements_flexible(requirements)
        assert 'features' in result
        assert isinstance(result['features'], list)
    
    def test_parse_other_type(self):
        """Test handling of other types (converts to string)."""
        requirements = 12345
        result = _parse_requirements_flexible(requirements)
        assert 'features' in result
        assert isinstance(result['features'], list)
        assert result['features'][0] == "12345"
    
    def test_parse_mixed_bullets(self):
        """Test handling of mixed bullet styles."""
        requirements = """
        - Feature one
        * Feature two
        • Feature three
        """
        result = _parse_requirements_flexible(requirements)
        assert 'features' in result
        assert len(result['features']) == 3
    
    def test_preserves_description(self):
        """Test that description field is preserved."""
        requirements = "Build a microservice"
        result = _parse_requirements_flexible(requirements)
        assert 'description' in result
        assert result['description'] == requirements


class TestPresidioPlaceholderDetection:
    """Test that Presidio placeholders are detected as non-code."""
    
    def test_detect_presidio_placeholders_in_response_handler(self):
        """Test that response containing Presidio placeholders is rejected."""
        from generator.agents.codegen_agent.codegen_response_handler import _contains_code_markers
        
        # Text with Presidio placeholders should not be considered code
        text_with_placeholders = """
        The provided requirements do not provide enough details to generate an implementation.
        Here are the reasons:
        - <ORGANIZATION> and <URL> are placeholders where actual information should be
        - <PERSON> is not specified
        """
        
        assert not _contains_code_markers(text_with_placeholders)
    
    def test_real_code_still_detected(self):
        """Test that real code is still properly detected."""
        from generator.agents.codegen_agent.codegen_response_handler import _contains_code_markers
        
        real_code = """
        import os
        import sys
        
        def main():
            print("Hello World")
            return 0
        
        if __name__ == "__main__":
            sys.exit(main())
        """
        
        assert _contains_code_markers(real_code)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
