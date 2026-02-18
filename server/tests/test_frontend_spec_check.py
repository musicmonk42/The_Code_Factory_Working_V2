# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Tests for frontend file detection from spec and fallback generation.

Tests the pre-codegen spec check that detects frontend files in the MD spec
and the fallback frontend file generation when files are missing.
"""

import tempfile
from pathlib import Path
from typing import Set

import pytest

# Import the functions we're testing
# Note: These are module-level functions in omnicore_service
from server.services.omnicore_service import (
    _generate_fallback_frontend_files,
    FRONTEND_FILE_PATTERNS,
)


class TestFrontendFilePatterns:
    """Test suite for frontend file pattern constants."""
    
    def test_frontend_file_patterns_contains_index_html(self):
        """Ensure index.html is in the frontend file patterns."""
        assert 'index.html' in FRONTEND_FILE_PATTERNS
    
    def test_frontend_file_patterns_contains_css(self):
        """Ensure style.css is in the frontend file patterns."""
        assert 'style.css' in FRONTEND_FILE_PATTERNS
    
    def test_frontend_file_patterns_contains_js(self):
        """Ensure app.js is in the frontend file patterns."""
        assert 'app.js' in FRONTEND_FILE_PATTERNS


class TestFallbackFrontendGeneration:
    """Test suite for fallback frontend file generation."""
    
    def test_generate_index_html(self):
        """Test generating a fallback index.html file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            missing_files: Set[str] = {'index.html'}
            results = _generate_fallback_frontend_files(
                output_path=tmpdir,
                missing_files=missing_files,
                project_name="Test Project"
            )
            
            assert results.get('index.html') is True
            
            # Check the file was created in templates directory
            templates_dir = Path(tmpdir) / "templates"
            index_path = templates_dir / "index.html"
            assert index_path.exists()
            
            # Check content contains the project name
            content = index_path.read_text(encoding='utf-8')
            assert "Test Project" in content
            assert "<!DOCTYPE html>" in content
    
    def test_generate_style_css(self):
        """Test generating a fallback style.css file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            missing_files: Set[str] = {'style.css'}
            results = _generate_fallback_frontend_files(
                output_path=tmpdir,
                missing_files=missing_files,
                project_name="Test Project"
            )
            
            assert results.get('style.css') is True
            
            # Check the file was created in static directory
            static_dir = Path(tmpdir) / "static"
            css_path = static_dir / "style.css"
            assert css_path.exists()
            
            # Check content
            content = css_path.read_text(encoding='utf-8')
            assert "body" in content
    
    def test_generate_app_js(self):
        """Test generating a fallback app.js file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            missing_files: Set[str] = {'app.js'}
            results = _generate_fallback_frontend_files(
                output_path=tmpdir,
                missing_files=missing_files,
                project_name="Test Project"
            )
            
            assert results.get('app.js') is True
            
            # Check the file was created in static directory
            static_dir = Path(tmpdir) / "static"
            js_path = static_dir / "app.js"
            assert js_path.exists()
            
            # Check content
            content = js_path.read_text(encoding='utf-8')
            assert "DOMContentLoaded" in content
    
    def test_generate_multiple_files(self):
        """Test generating multiple fallback frontend files at once."""
        with tempfile.TemporaryDirectory() as tmpdir:
            missing_files: Set[str] = {'index.html', 'style.css', 'app.js'}
            results = _generate_fallback_frontend_files(
                output_path=tmpdir,
                missing_files=missing_files,
                project_name="Multi File Test"
            )
            
            # All files should succeed
            assert all(results.get(f) is True for f in missing_files)
    
    def test_generate_into_existing_templates_dir(self):
        """Test that files are placed in existing directories if present."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Pre-create templates directory
            templates_dir = Path(tmpdir) / "templates"
            templates_dir.mkdir()
            
            missing_files: Set[str] = {'index.html'}
            results = _generate_fallback_frontend_files(
                output_path=tmpdir,
                missing_files=missing_files,
                project_name="Existing Dir Test"
            )
            
            assert results.get('index.html') is True
            # File should be in the pre-existing templates directory
            assert (templates_dir / "index.html").exists()
    
    def test_generate_into_existing_static_dir(self):
        """Test that CSS/JS files are placed in existing static directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Pre-create static directory
            static_dir = Path(tmpdir) / "static"
            static_dir.mkdir()
            
            missing_files: Set[str] = {'style.css', 'app.js'}
            results = _generate_fallback_frontend_files(
                output_path=tmpdir,
                missing_files=missing_files,
                project_name="Static Dir Test"
            )
            
            assert results.get('style.css') is True
            assert results.get('app.js') is True
            # Files should be in the pre-existing static directory
            assert (static_dir / "style.css").exists()
            assert (static_dir / "app.js").exists()
    
    def test_unknown_file_returns_false(self):
        """Test that unknown files return False status."""
        with tempfile.TemporaryDirectory() as tmpdir:
            missing_files: Set[str] = {'unknown_file.xyz'}
            results = _generate_fallback_frontend_files(
                output_path=tmpdir,
                missing_files=missing_files,
                project_name="Unknown Test"
            )
            
            assert results.get('unknown_file.xyz') is False
    
    def test_project_name_in_generated_content(self):
        """Test that project name appears in generated HTML."""
        with tempfile.TemporaryDirectory() as tmpdir:
            missing_files: Set[str] = {'index.html'}
            project_name = "My Custom App Name"
            results = _generate_fallback_frontend_files(
                output_path=tmpdir,
                missing_files=missing_files,
                project_name=project_name
            )
            
            templates_dir = Path(tmpdir) / "templates"
            index_path = templates_dir / "index.html"
            content = index_path.read_text(encoding='utf-8')
            
            # Project name should appear in title and navbar
            assert project_name in content


class TestSpecFrontendDetection:
    """Test suite for spec-based frontend file detection.
    
    Note: These tests verify that FRONTEND_FILE_PATTERNS correctly 
    identifies frontend files that should trigger frontend generation.
    """
    
    def test_detect_index_html_in_spec_files(self):
        """Test that index.html triggers frontend detection."""
        spec_files = {'main.py', 'index.html', 'requirements.txt'}
        frontend_files = spec_files & FRONTEND_FILE_PATTERNS
        
        assert 'index.html' in frontend_files
    
    def test_detect_multiple_frontend_files_in_spec(self):
        """Test detecting multiple frontend files in spec."""
        spec_files = {'main.py', 'index.html', 'style.css', 'app.js', 'requirements.txt'}
        frontend_files = spec_files & FRONTEND_FILE_PATTERNS
        
        assert len(frontend_files) == 3
        assert 'index.html' in frontend_files
        assert 'style.css' in frontend_files
        assert 'app.js' in frontend_files
    
    def test_no_frontend_files_in_backend_only_spec(self):
        """Test that backend-only spec has no frontend files."""
        spec_files = {'main.py', 'routes.py', 'schemas.py', 'requirements.txt'}
        frontend_files = spec_files & FRONTEND_FILE_PATTERNS
        
        assert len(frontend_files) == 0
    
    def test_react_files_detected(self):
        """Test that React files are detected as frontend."""
        spec_files = {'main.py', 'App.tsx', 'main.tsx'}
        frontend_files = spec_files & FRONTEND_FILE_PATTERNS
        
        assert 'App.tsx' in frontend_files or 'main.tsx' in frontend_files


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
