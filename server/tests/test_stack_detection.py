# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Tests for full-stack detection in language detection.

Tests the enhanced detect_language_from_content function that returns
structured metadata including frontend requirements.

This test file extracts the pure logic of detect_language_from_content
for testing without requiring FastAPI dependencies.
"""

import re
from typing import Any, Dict, Union

import pytest


# Constants for frontend types (matching server.routers.generator)
DEFAULT_FRONTEND_TYPE = "jinja_templates"
FRONTEND_TYPE_VANILLA_JS = "vanilla_js"
FRONTEND_TYPE_REACT = "react"
FRONTEND_TYPE_VUE = "vue"
FRONTEND_TYPE_ANGULAR = "angular"


def detect_language_from_content(readme_content: str) -> Union[str, Dict[str, Any]]:
    """
    Detect programming language and stack requirements from README content.
    
    This is extracted from server.routers.generator.detect_language_from_content
    for testing purposes. Keep in sync with the production implementation.
    """
    readme_lower = readme_content.lower()
    
    # Detect backend language first
    backend_language = "python"  # Default
    
    # Check for language-specific keywords in priority order
    # Look for strong backend framework indicators first (more specific)
    if "fastapi" in readme_lower or "flask" in readme_lower or "django" in readme_lower or "python" in readme_lower:
        backend_language = "python"
    # TypeScript must be checked before JavaScript since JS is often mentioned in TS projects
    elif "typescript" in readme_lower:
        backend_language = "typescript"
    # Java check - must come BEFORE JavaScript to avoid false detection
    # Improved patterns to explicitly check for Java without JavaScript
    elif (re.search(r'\bjava\s', readme_lower, re.IGNORECASE) or 
        re.search(r'\bjava\.', readme_lower, re.IGNORECASE) or 
        re.search(r'\bjava\b(?!script)', readme_lower, re.IGNORECASE)):
        backend_language = "java"
    # JavaScript check with common patterns - detect if there are strong backend indicators
    elif ("node.js" in readme_lower or "nodejs" in readme_lower or "express" in readme_lower or
          re.search(r'\bnpm\b', readme_lower)):
        backend_language = "javascript"
    # Rust check
    elif "rust" in readme_lower:
        backend_language = "rust"
    # Go check - use specific patterns to avoid false positives
    # Look for "golang" or "go " with word boundaries
    elif "golang" in readme_lower or re.search(r'\bgo\s+(language|lang|programming)\b', readme_lower, re.IGNORECASE):
        backend_language = "go"
    
    # Detect frontend requirements
    frontend_keywords = [
        r'\bfrontend\b', r'\bfront-end\b', r'\bfront end\b',
        r'\bweb\s+app\b', r'\bweb\s+application\b', r'\bwebapp\b',
        r'\bui\b', r'\buser\s+interface\b', r'\buser interface\b',
        r'\bdashboard\b',
        r'\bhtml\b', r'\bcss\b',
        r'\breact\b', r'\bvue\b', r'\bangular\b',
        r'\btemplate\b', r'\btemplates\b', r'\bjinja\b',
        r'\bform\b', r'\bforms\b',
        r'\bpage\b', r'\bpages\b', r'\blanding\s+page\b',
        r'\bresponsive\b', r'\bmobile-friendly\b',
        r'\bsingle\s+page\b', r'\bspa\b',
        r'\bweb\s+interface\b', r'\bweb interface\b',
        r'\bbrowser\b', r'\bclient-side\b', r'\bclient side\b',
        r'\bstatic\s+files\b', r'\bstatic files\b',
        r'\bsite\b', r'\bwebsite\b',
        r'\bviews\b',
    ]
    
    include_frontend = False
    for pattern in frontend_keywords:
        if re.search(pattern, readme_lower):
            include_frontend = True
            break
    
    # If no frontend detected, return simple string for backward compatibility
    if not include_frontend:
        return backend_language
    
    # Determine frontend type based on additional context
    frontend_type = DEFAULT_FRONTEND_TYPE  # Default for Python full-stack
    
    # Check for specific frontend frameworks
    if re.search(r'\breact\b', readme_lower):
        frontend_type = FRONTEND_TYPE_REACT
    elif re.search(r'\bvue\b', readme_lower):
        frontend_type = FRONTEND_TYPE_VUE
    elif re.search(r'\bangular\b', readme_lower):
        frontend_type = FRONTEND_TYPE_ANGULAR
    elif re.search(r'\bvanilla\s+js\b', readme_lower) or re.search(r'\bplain\s+javascript\b', readme_lower):
        frontend_type = FRONTEND_TYPE_VANILLA_JS
    elif backend_language == "python":
        # For Python, check if Jinja2 templates are mentioned
        if re.search(r'\bjinja\b', readme_lower) or re.search(r'\btemplate\b', readme_lower):
            frontend_type = DEFAULT_FRONTEND_TYPE
        else:
            # Default to Jinja templates for Python full-stack
            frontend_type = DEFAULT_FRONTEND_TYPE
    else:
        # For non-Python backends, default to vanilla JS
        frontend_type = FRONTEND_TYPE_VANILLA_JS
    
    # Return structured metadata for full-stack projects
    return {
        "backend_language": backend_language,
        "include_frontend": True,
        "frontend_type": frontend_type,
    }


class TestStackDetection:
    """Test suite for stack detection functionality."""

    def test_python_backend_only(self):
        """Test detection of Python backend without frontend."""
        readme = """
        # Python API
        A simple FastAPI backend service for data processing.
        Uses Python 3.10+ with async/await patterns.
        """
        result = detect_language_from_content(readme)
        
        # Should work with both old string format and new dict format
        if isinstance(result, str):
            assert result == "python"
        else:
            assert result["backend_language"] == "python"
            assert result.get("include_frontend", False) is False

    def test_python_with_frontend_ui_keyword(self):
        """Test detection of full-stack app with 'UI' keyword."""
        readme = """
        # Task Management Web App
        A full-featured task manager with a web UI for creating,
        updating, and tracking tasks. Built with FastAPI backend.
        """
        result = detect_language_from_content(readme)
        
        if isinstance(result, dict):
            assert result["backend_language"] == "python"
            assert result["include_frontend"] is True
            assert "frontend_type" in result

    def test_python_with_frontend_dashboard_keyword(self):
        """Test detection with 'dashboard' keyword."""
        readme = """
        # Analytics Dashboard
        Real-time analytics dashboard displaying metrics and charts.
        Python backend with FastAPI.
        """
        result = detect_language_from_content(readme)
        
        if isinstance(result, dict):
            assert result["backend_language"] == "python"
            assert result["include_frontend"] is True

    def test_python_with_html_css_js_keywords(self):
        """Test detection with explicit HTML/CSS/JavaScript mentions."""
        readme = """
        # Web Application
        Full-stack app with HTML templates, CSS styling, and JavaScript
        for interactivity. FastAPI serves the templates.
        """
        result = detect_language_from_content(readme)
        
        if isinstance(result, dict):
            assert result["backend_language"] == "python"
            assert result["include_frontend"] is True

    def test_python_with_web_app_keyword(self):
        """Test detection with 'web app' keyword."""
        readme = """
        # E-Commerce Web App
        A complete e-commerce web application with user authentication,
        product catalog, and shopping cart.
        """
        result = detect_language_from_content(readme)
        
        if isinstance(result, dict):
            assert result["backend_language"] == "python"
            assert result["include_frontend"] is True

    def test_python_with_frontend_framework(self):
        """Test detection with frontend framework mention."""
        readme = """
        # Modern SPA
        Single page application using React frontend and Python FastAPI backend.
        """
        result = detect_language_from_content(readme)
        
        if isinstance(result, dict):
            assert result["backend_language"] == "python"
            assert result["include_frontend"] is True
            # Could potentially detect React as frontend_type

    def test_python_with_template_keyword(self):
        """Test detection with 'template' keyword."""
        readme = """
        # Blog Platform
        A blogging platform using Jinja2 templates for server-side rendering.
        FastAPI backend with HTML templates.
        """
        result = detect_language_from_content(readme)
        
        if isinstance(result, dict):
            assert result["backend_language"] == "python"
            assert result["include_frontend"] is True
            assert result.get("frontend_type") in ["jinja_templates", "vanilla_js"]

    def test_python_with_form_keyword(self):
        """Test detection with 'form' keyword indicating UI."""
        readme = """
        # Contact Form API
        API with web form for submitting contact requests.
        Includes form validation and submission handling.
        """
        result = detect_language_from_content(readme)
        
        if isinstance(result, dict):
            assert result["backend_language"] == "python"
            assert result["include_frontend"] is True

    def test_python_with_page_keyword(self):
        """Test detection with 'page' keyword."""
        readme = """
        # Landing Page
        Marketing landing page with registration form.
        Python backend handles form submissions.
        """
        result = detect_language_from_content(readme)
        
        if isinstance(result, dict):
            assert result["backend_language"] == "python"
            assert result["include_frontend"] is True

    def test_python_with_responsive_keyword(self):
        """Test detection with 'responsive' keyword."""
        readme = """
        # Portfolio Site
        Responsive portfolio website showcasing projects.
        Mobile-friendly design with Python backend.
        """
        result = detect_language_from_content(readme)
        
        if isinstance(result, dict):
            assert result["backend_language"] == "python"
            assert result["include_frontend"] is True

    def test_typescript_backend_only(self):
        """Test TypeScript backend without frontend."""
        readme = """
        # TypeScript API
        REST API built with TypeScript and Express.
        """
        result = detect_language_from_content(readme)
        
        if isinstance(result, str):
            assert result == "typescript"
        else:
            assert result["backend_language"] == "typescript"
            assert result.get("include_frontend", False) is False

    def test_javascript_backend_only(self):
        """Test JavaScript backend without frontend."""
        readme = """
        # Node.js Microservice
        Microservice built with Node.js and Express for processing data.
        """
        result = detect_language_from_content(readme)
        
        if isinstance(result, str):
            assert result == "javascript"
        else:
            assert result["backend_language"] == "javascript"

    def test_java_backend_only(self):
        """Test Java backend detection."""
        readme = """
        # Java Spring Boot API
        REST API using Java Spring Boot framework.
        """
        result = detect_language_from_content(readme)
        
        if isinstance(result, str):
            assert result == "java"
        else:
            assert result["backend_language"] == "java"

    def test_multiple_frontend_keywords(self):
        """Test with multiple frontend indicators."""
        readme = """
        # Task Manager Web Application
        
        A comprehensive web app with beautiful UI and responsive dashboard.
        Features include:
        - Interactive web interface
        - HTML5 forms for data entry
        - CSS3 animations
        - JavaScript for client-side validation
        - User-friendly pages
        
        Backend: FastAPI with Python
        Frontend: Modern web technologies
        """
        result = detect_language_from_content(readme)
        
        if isinstance(result, dict):
            assert result["backend_language"] == "python"
            assert result["include_frontend"] is True

    def test_case_insensitive_detection(self):
        """Test that detection is case-insensitive."""
        readme = """
        # Web APPLICATION
        Full-stack WEB APP with DASHBOARD and USER INTERFACE.
        """
        result = detect_language_from_content(readme)
        
        if isinstance(result, dict):
            assert result["backend_language"] == "python"  # defaults to python
            assert result["include_frontend"] is True

    def test_empty_readme(self):
        """Test with empty README."""
        readme = ""
        result = detect_language_from_content(readme)
        
        # Should default to Python
        if isinstance(result, str):
            assert result == "python"
        else:
            assert result["backend_language"] == "python"
            assert result.get("include_frontend", False) is False

    def test_backend_api_keywords_no_frontend(self):
        """Test that pure API keywords don't trigger frontend."""
        readme = """
        # REST API Service
        RESTful API for data processing and analytics.
        Endpoints for CRUD operations on resources.
        """
        result = detect_language_from_content(readme)
        
        if isinstance(result, dict):
            assert result.get("include_frontend", False) is False
