# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Comprehensive tests for clarifier blank questions and generic boilerplate fixes.

Tests the five key bug fixes:
1. Bug 1: Format mismatch - _generate_clarification_questions returns List[Dict] not List[str]
2. Bug 2: Generic fallback removed - no hard-coded questions when README is clear
3. Bug 3: README context passed to generate_questions for context-aware questions
4. Bug 4: Expanded keyword matching in detect_ambiguities
5. Bug 5: Allow skip/empty responses in _submit_clarification_response

These tests validate industry-standard quality:
- Comprehensive coverage of edge cases
- Clear test names describing what is being tested
- Proper mocking to isolate units
- Async/await patterns properly handled
- Type safety validated
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Dict, Any, List

from server.services.omnicore_service import OmniCoreService, _clarification_sessions


class TestBug1FormatMismatch:
    """Test Bug 1: _generate_clarification_questions returns proper format."""

    def test_returns_list_of_dicts_not_strings(self):
        """Test that _generate_clarification_questions returns List[Dict] with proper keys."""
        service = OmniCoreService()
        
        # Test with README that triggers database question
        readme = "Build a web app that stores user data in a database"
        questions = service._generate_clarification_questions(readme)
        
        # Verify it returns a list
        assert isinstance(questions, list), "Should return a list"
        
        # If questions exist, verify structure
        if len(questions) > 0:
            for q in questions:
                assert isinstance(q, dict), f"Each question should be a dict, got {type(q)}"
                assert "id" in q, "Each question dict must have 'id' key"
                assert "question" in q, "Each question dict must have 'question' key"
                assert "category" in q, "Each question dict must have 'category' key"
                
                # Verify types
                assert isinstance(q["id"], str), "id should be string"
                assert isinstance(q["question"], str), "question should be string"
                assert isinstance(q["category"], str), "category should be string"
                
                # Verify id format
                assert q["id"].startswith("q"), "id should start with 'q'"
                
                # Verify question is not empty
                assert q["question"].strip() != "", "question text should not be empty"

    def test_questions_have_sequential_ids(self):
        """Test that generated questions have sequential IDs (q1, q2, q3, etc.)."""
        service = OmniCoreService()
        
        # README that triggers multiple questions
        readme = """
        Build a web application with:
        - User authentication
        - Database storage
        - REST API
        - Frontend interface
        """
        
        questions = service._generate_clarification_questions(readme)
        
        # Verify IDs are sequential
        expected_ids = [f"q{i+1}" for i in range(len(questions))]
        actual_ids = [q["id"] for q in questions]
        assert actual_ids == expected_ids, f"Expected sequential IDs {expected_ids}, got {actual_ids}"

    def test_backward_compatibility_with_llm_format(self):
        """Test that _generate_clarified_requirements handles both dict and string formats."""
        service = OmniCoreService()
        
        # Create session with dict format questions (new format)
        session_dict = {
            "requirements": "test",
            "questions": [
                {"id": "q1", "question": "What database?", "category": "database"}
            ],
            "answers": {
                "q1": "PostgreSQL"
            }
        }
        
        result = service._generate_clarified_requirements(session_dict)
        assert "database" in result["clarified_requirements"]
        assert result["clarified_requirements"]["database"] == "PostgreSQL"
        
        # Create session with string format questions (legacy format)
        session_string = {
            "requirements": "test",
            "questions": ["What database would you like to use?"],
            "answers": {
                "q1": "MySQL"
            }
        }
        
        result = service._generate_clarified_requirements(session_string)
        assert "database" in result["clarified_requirements"]
        assert result["clarified_requirements"]["database"] == "MySQL"


class TestBug2GenericFallbackRemoved:
    """Test Bug 2: Generic fallback questions removed."""

    def test_no_generic_fallback_for_clear_readme(self):
        """Test that clear README with all specs doesn't generate generic questions."""
        service = OmniCoreService()
        
        clear_readme = """
        # Complete Application Specification
        
        Build a task management web application with:
        - Backend: Python with Flask
        - Database: PostgreSQL with SQLAlchemy ORM
        - Authentication: JWT tokens
        - API: RESTful API with OpenAPI documentation
        - Frontend: React with TypeScript
        - Deployment: Docker containers on AWS ECS
        - Testing: pytest for unit tests, Cypress for e2e
        """
        
        questions = service._generate_clarification_questions(clear_readme)
        
        # Should return empty list for clear README
        assert len(questions) == 0, f"Expected no questions for clear README, got {len(questions)}: {questions}"

    def test_no_generic_programming_language_question(self):
        """Test that generic 'programming language' question is NOT asked."""
        service = OmniCoreService()
        
        # Short README that doesn't specify much
        readme = "Build a simple app"
        
        questions = service._generate_clarification_questions(readme)
        
        # Check that none of the old generic questions appear
        question_texts = [q["question"].lower() for q in questions]
        
        assert not any("primary programming language" in q for q in question_texts), \
            "Should not ask generic 'primary programming language' question"
        assert not any("target users" in q for q in question_texts), \
            "Should not ask generic 'target users' question"
        assert not any("third-party integrations" in q for q in question_texts), \
            "Should not ask generic 'third-party integrations' question"

    def test_only_specific_ambiguities_generate_questions(self):
        """Test that only specific detected ambiguities generate questions."""
        service = OmniCoreService()
        
        # README with specific ambiguity (mentions database but not which one)
        readme = "Build a web app with user login and data storage"
        
        questions = service._generate_clarification_questions(readme)
        
        # Should have specific questions about the ambiguous parts
        if len(questions) > 0:
            for q in questions:
                # Each question should be about a specific technology choice
                assert q["category"] in ["database", "authentication", "api", "frontend", 
                                        "deployment", "testing", "performance", "security"], \
                    f"Question should have a specific category, got: {q['category']}"


class TestBug3ReadmeContextAware:
    """Test Bug 3: Questions use README content for context."""

    @pytest.mark.asyncio
    async def test_generate_questions_accepts_readme_content(self):
        """Test that generate_questions accepts readme_content parameter."""
        from generator.clarifier.clarifier import Clarifier
        
        with patch('generator.clarifier.clarifier.get_config') as mock_config, \
             patch('generator.clarifier.clarifier.get_fernet'), \
             patch('generator.clarifier.clarifier.get_logger') as mock_logger, \
             patch('generator.clarifier.clarifier.get_tracer'), \
             patch('generator.clarifier.clarifier.get_circuit_breaker'):
            
            mock_config.return_value = MagicMock(
                TARGET_LANGUAGE='en',
                INTERACTION_MODE='cli',
                HISTORY_FILE='/tmp/test_history.json',
                is_production_env=False,
            )
            mock_logger.return_value = MagicMock()
            
            clarifier = Clarifier()
            clarifier.llm = None  # Force rule-based
            
            readme = "Build a web application with database"
            ambiguities = ["Database not specified"]
            
            # Call with readme_content parameter
            questions = await clarifier.generate_questions(ambiguities, readme_content=readme)
            
            # Should return questions
            assert isinstance(questions, list)

    @pytest.mark.asyncio
    async def test_llm_prompt_includes_readme_content(self):
        """Test that LLM prompt includes original README for context."""
        from generator.clarifier.clarifier import Clarifier
        
        with patch('generator.clarifier.clarifier.get_config') as mock_config, \
             patch('generator.clarifier.clarifier.get_fernet'), \
             patch('generator.clarifier.clarifier.get_logger') as mock_logger, \
             patch('generator.clarifier.clarifier.get_tracer'), \
             patch('generator.clarifier.clarifier.get_circuit_breaker'):
            
            mock_config.return_value = MagicMock(
                TARGET_LANGUAGE='en',
                INTERACTION_MODE='cli',
                HISTORY_FILE='/tmp/test_history.json',
                is_production_env=False,
            )
            mock_logger.return_value = MagicMock()
            
            clarifier = Clarifier()
            
            # Mock LLM
            mock_llm = AsyncMock()
            mock_llm.generate = AsyncMock(return_value='[{"question": "What database?", "category": "database"}]')
            clarifier.llm = mock_llm
            
            readme = "Build a task manager with PostgreSQL"
            ambiguities = ["Deployment not specified"]
            
            await clarifier.generate_questions(ambiguities, readme_content=readme)
            
            # Verify LLM was called with prompt containing README
            assert mock_llm.generate.called
            prompt = mock_llm.generate.call_args[0][0]
            assert "Original Requirements" in prompt, "Prompt should include README section"
            assert readme in prompt, "Prompt should include actual README content"


class TestBug4ExpandedKeywordMatching:
    """Test Bug 4: Expanded keyword matching in detect_ambiguities."""

    @pytest.mark.asyncio
    async def test_detects_dynamodb_as_database(self):
        """Test that expanded keywords detect DynamoDB as a database."""
        from generator.clarifier.clarifier import Clarifier
        
        with patch('generator.clarifier.clarifier.get_config') as mock_config, \
             patch('generator.clarifier.clarifier.get_fernet'), \
             patch('generator.clarifier.clarifier.get_logger') as mock_logger, \
             patch('generator.clarifier.clarifier.get_tracer'), \
             patch('generator.clarifier.clarifier.get_circuit_breaker'):
            
            mock_config.return_value = MagicMock(
                TARGET_LANGUAGE='en',
                INTERACTION_MODE='cli',
                HISTORY_FILE='/tmp/test_history.json',
                is_production_env=False,
            )
            mock_logger.return_value = MagicMock()
            
            clarifier = Clarifier()
            clarifier.llm = None  # Force rule-based
            
            readme = "Build an app using DynamoDB for data storage"
            ambiguities = await clarifier.detect_ambiguities(readme)
            
            # Should NOT flag database as ambiguous since DynamoDB is specified
            assert "Database technology not specified" not in ambiguities

    @pytest.mark.asyncio
    async def test_detects_nextjs_as_frontend(self):
        """Test that expanded keywords detect Next.js as a frontend framework."""
        from generator.clarifier.clarifier import Clarifier
        
        with patch('generator.clarifier.clarifier.get_config') as mock_config, \
             patch('generator.clarifier.clarifier.get_fernet'), \
             patch('generator.clarifier.clarifier.get_logger') as mock_logger, \
             patch('generator.clarifier.clarifier.get_tracer'), \
             patch('generator.clarifier.clarifier.get_circuit_breaker'):
            
            mock_config.return_value = MagicMock(
                TARGET_LANGUAGE='en',
                INTERACTION_MODE='cli',
                HISTORY_FILE='/tmp/test_history.json',
                is_production_env=False,
            )
            mock_logger.return_value = MagicMock()
            
            clarifier = Clarifier()
            clarifier.llm = None  # Force rule-based
            
            readme = "Build a web application using nextjs for the frontend"
            ambiguities = await clarifier.detect_ambiguities(readme)
            
            # Should NOT flag frontend as ambiguous since Next.js is specified
            assert "Frontend framework not specified" not in ambiguities

    @pytest.mark.asyncio
    async def test_detects_k8s_as_deployment_platform(self):
        """Test that expanded keywords detect k8s/Kubernetes variants."""
        from generator.clarifier.clarifier import Clarifier
        
        with patch('generator.clarifier.clarifier.get_config') as mock_config, \
             patch('generator.clarifier.clarifier.get_fernet'), \
             patch('generator.clarifier.clarifier.get_logger') as mock_logger, \
             patch('generator.clarifier.clarifier.get_tracer'), \
             patch('generator.clarifier.clarifier.get_circuit_breaker'):
            
            mock_config.return_value = MagicMock(
                TARGET_LANGUAGE='en',
                INTERACTION_MODE='cli',
                HISTORY_FILE='/tmp/test_history.json',
                is_production_env=False,
            )
            mock_logger.return_value = MagicMock()
            
            clarifier = Clarifier()
            clarifier.llm = None  # Force rule-based
            
            readme = "Deploy the app to k8s cluster"
            ambiguities = await clarifier.detect_ambiguities(readme)
            
            # Should NOT flag deployment as ambiguous since k8s is specified
            assert "Deployment platform not specified" not in ambiguities


class TestBug5SkipEmptyResponses:
    """Test Bug 5: Allow skip/empty responses."""

    def test_skip_response_allowed(self):
        """Test that empty response is allowed and marked as [SKIPPED]."""
        service = OmniCoreService()
        job_id = "test-skip-001"
        
        # Create a clarification session
        _clarification_sessions[job_id] = {
            "job_id": job_id,
            "requirements": "test",
            "questions": [
                {"id": "q1", "question": "What database?", "category": "database"}
            ],
            "answers": {},
            "status": "in_progress",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        
        try:
            # Submit empty response (skip)
            result = service._submit_clarification_response(job_id, {
                "question_id": "q1",
                "response": ""
            })
            
            # Should not return error
            assert result["status"] != "error", f"Should allow skip, got: {result}"
            
            # Should mark as skipped
            session = _clarification_sessions[job_id]
            assert session["answers"]["q1"] == "[SKIPPED]", \
                f"Expected [SKIPPED] marker, got: {session['answers']['q1']}"
        finally:
            # Cleanup
            if job_id in _clarification_sessions:
                del _clarification_sessions[job_id]

    def test_whitespace_only_response_treated_as_skip(self):
        """Test that whitespace-only response is treated as skip."""
        service = OmniCoreService()
        job_id = "test-skip-002"
        
        _clarification_sessions[job_id] = {
            "job_id": job_id,
            "requirements": "test",
            "questions": [
                {"id": "q1", "question": "What database?", "category": "database"}
            ],
            "answers": {},
            "status": "in_progress",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        
        try:
            # Submit whitespace-only response
            result = service._submit_clarification_response(job_id, {
                "question_id": "q1",
                "response": "   "
            })
            
            assert result["status"] != "error"
            session = _clarification_sessions[job_id]
            assert session["answers"]["q1"] == "[SKIPPED]"
        finally:
            if job_id in _clarification_sessions:
                del _clarification_sessions[job_id]

    def test_skipped_answers_count_toward_completion(self):
        """Test that skipped answers count toward question completion."""
        service = OmniCoreService()
        job_id = "test-skip-003"
        
        _clarification_sessions[job_id] = {
            "job_id": job_id,
            "requirements": "test",
            "questions": [
                {"id": "q1", "question": "Q1?", "category": "test"},
                {"id": "q2", "question": "Q2?", "category": "test"},
                {"id": "q3", "question": "Q3?", "category": "test"}
            ],
            "answers": {},
            "status": "in_progress",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        
        try:
            # Answer q1, skip q2, answer q3
            service._submit_clarification_response(job_id, {
                "question_id": "q1",
                "response": "Answer 1"
            })
            
            service._submit_clarification_response(job_id, {
                "question_id": "q2",
                "response": ""  # Skip
            })
            
            result = service._submit_clarification_response(job_id, {
                "question_id": "q3",
                "response": "Answer 3"
            })
            
            # Should be completed
            assert result["status"] == "completed", \
                f"Expected completed status, got: {result['status']}"
            
            session = _clarification_sessions[job_id]
            assert len(session["answers"]) == 3
            assert session["status"] == "completed"
        finally:
            if job_id in _clarification_sessions:
                del _clarification_sessions[job_id]

    def test_question_id_required(self):
        """Test that question_id is still required even if response is optional."""
        service = OmniCoreService()
        job_id = "test-skip-004"
        
        _clarification_sessions[job_id] = {
            "job_id": job_id,
            "requirements": "test",
            "questions": [
                {"id": "q1", "question": "What database?", "category": "database"}
            ],
            "answers": {},
            "status": "in_progress",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        
        try:
            # Submit without question_id
            result = service._submit_clarification_response(job_id, {
                "response": "Some answer"
            })
            
            # Should return error for missing question_id
            assert result["status"] == "error"
            assert "question_id" in result["message"].lower()
        finally:
            if job_id in _clarification_sessions:
                del _clarification_sessions[job_id]


class TestEndToEndIntegration:
    """Integration tests for the complete clarification flow."""

    def test_complete_flow_with_dict_format(self):
        """Test complete flow using new dict format."""
        service = OmniCoreService()
        
        # Generate questions (should return dict format)
        readme = "Build a web app with database and authentication"
        questions = service._generate_clarification_questions(readme)
        
        # Verify format
        assert all(isinstance(q, dict) for q in questions)
        assert all("id" in q and "question" in q and "category" in q for q in questions)
        
        # Simulate session creation and responses
        job_id = "test-integration-001"
        _clarification_sessions[job_id] = {
            "job_id": job_id,
            "requirements": readme,
            "questions": questions,
            "answers": {},
            "status": "in_progress",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        
        try:
            # Answer questions (including skip)
            for i, q in enumerate(questions):
                if i == 0:
                    # Answer first question
                    result = service._submit_clarification_response(job_id, {
                        "question_id": q["id"],
                        "response": "PostgreSQL"
                    })
                else:
                    # Skip remaining
                    result = service._submit_clarification_response(job_id, {
                        "question_id": q["id"],
                        "response": ""
                    })
            
            # Final result should be completed
            session = _clarification_sessions[job_id]
            assert session["status"] == "completed"
            assert len(session["answers"]) == len(questions)
        finally:
            if job_id in _clarification_sessions:
                del _clarification_sessions[job_id]

    @pytest.mark.asyncio
    async def test_no_clarification_for_complete_spec(self):
        """Test that complete specification doesn't trigger clarification."""
        from generator.clarifier.clarifier import Clarifier
        
        with patch('generator.clarifier.clarifier.get_config') as mock_config, \
             patch('generator.clarifier.clarifier.get_fernet'), \
             patch('generator.clarifier.clarifier.get_logger') as mock_logger, \
             patch('generator.clarifier.clarifier.get_tracer'), \
             patch('generator.clarifier.clarifier.get_circuit_breaker'):
            
            mock_config.return_value = MagicMock(
                TARGET_LANGUAGE='en',
                INTERACTION_MODE='cli',
                HISTORY_FILE='/tmp/test_history.json',
                is_production_env=False,
            )
            mock_logger.return_value = MagicMock()
            
            clarifier = Clarifier()
            clarifier.llm = None  # Force rule-based
            
            complete_readme = """
            # E-commerce Platform
            
            - Backend: Python/Django
            - Database: PostgreSQL
            - Authentication: OAuth 2.0 with Auth0
            - API: RESTful with OpenAPI 3.0
            - Frontend: React with Next.js
            - Deployment: Docker on AWS ECS
            - Testing: pytest, Jest, Cypress
            - Security: HTTPS, encryption at rest
            """
            
            # Detect ambiguities
            ambiguities = await clarifier.detect_ambiguities(complete_readme)
            
            # Should find no ambiguities
            assert len(ambiguities) == 0, \
                f"Expected no ambiguities for complete spec, got: {ambiguities}"
            
            # Generate questions
            questions = await clarifier.generate_questions(ambiguities, complete_readme)
            
            # Should generate no questions
            assert len(questions) == 0, \
                f"Expected no questions for complete spec, got: {questions}"
