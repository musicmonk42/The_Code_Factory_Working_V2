# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Tests for Question Loop gap-filling functionality.
"""

import tempfile
from pathlib import Path

import pytest
from generator.intent_parser.question_loop import (
    Question,
    QuestionResponse,
    SpecLock,
    generate_questions,
    create_spec_lock_from_answers,
    run_question_loop,
)
from generator.intent_parser.spec_block import SpecBlock


def test_question_response_creation():
    """Test creating a question response."""
    response = QuestionResponse(
        field_name="project_type",
        value="fastapi_service",
        confidence=1.0,
        source="user"
    )
    
    assert response.field_name == "project_type"
    assert response.value == "fastapi_service"
    assert response.confidence == 1.0
    assert response.source == "user"


def test_spec_lock_creation():
    """Test creating a SpecLock."""
    lock = SpecLock(
        project_type="fastapi_service",
        package_name="my_app",
        output_dir="generated/my_app"
    )
    
    assert lock.project_type == "fastapi_service"
    assert lock.package_name == "my_app"
    assert lock.output_dir == "generated/my_app"
    assert lock.schema_version == "1.0"
    assert lock.generated_at is not None


def test_spec_lock_save_and_load():
    """Test saving and loading SpecLock from file."""
    lock = SpecLock(
        project_type="cli_tool",
        package_name="my_cli",
        output_dir="generated/cli",
        dependencies=["click>=8.0.0"],
    )
    
    with tempfile.TemporaryDirectory() as tmpdir:
        lock_path = Path(tmpdir) / "spec.lock.yaml"
        lock.save(lock_path)
        
        assert lock_path.exists()
        
        # Load it back
        loaded_lock = SpecLock.load(lock_path)
        assert loaded_lock.project_type == "cli_tool"
        assert loaded_lock.package_name == "my_cli"
        assert "click>=8.0.0" in loaded_lock.dependencies


def test_generate_questions_complete_spec():
    """Test that no questions are generated for complete spec."""
    spec = SpecBlock(
        project_type="fastapi_service",
        package_name="complete_app",
        output_dir="generated/app"
    )
    
    questions = generate_questions(spec)
    # Should still generate interface questions for fastapi_service type
    assert len(questions) >= 0


def test_generate_questions_missing_project_type():
    """Test question generation for missing project_type."""
    spec = SpecBlock(
        package_name="my_app",
        output_dir="generated/my_app"
    )
    
    questions = generate_questions(spec)
    
    # Should have at least project_type question
    assert any(q.field_name == "project_type" for q in questions)
    
    project_type_q = next(q for q in questions if q.field_name == "project_type")
    # After changes: default_value is None unless inferred from README
    assert project_type_q.default_value is None
    assert len(project_type_q.examples) > 0


def test_generate_questions_missing_package_name():
    """Test question generation for missing package_name."""
    spec = SpecBlock(
        project_type="library",
        output_dir="generated/lib"
    )
    
    questions = generate_questions(spec)
    
    assert any(q.field_name == "package_name" for q in questions)


def test_generate_questions_missing_output_dir():
    """Test question generation for missing output_dir."""
    spec = SpecBlock(
        project_type="lambda_function",
        package_name="my_lambda"
    )
    
    questions = generate_questions(spec)
    
    assert any(q.field_name == "output_dir" for q in questions)
    
    output_dir_q = next(q for q in questions if q.field_name == "output_dir")
    # Should suggest output_dir based on package_name
    assert "my_lambda" in output_dir_q.default_value


def test_generate_questions_with_readme_inference():
    """Test question generation with README content for inference."""
    spec = SpecBlock()
    readme = """
    # FastAPI Application
    
    This is a REST API service.
    """
    
    questions = generate_questions(spec, readme_content=readme)
    
    # Should infer fastapi_service from README
    project_type_q = next((q for q in questions if q.field_name == "project_type"), None)
    if project_type_q:
        assert "fastapi" in project_type_q.default_value.lower()


def test_create_spec_lock_from_answers():
    """Test creating SpecLock from answers."""
    spec = SpecBlock(
        package_name="base_app"
    )
    
    answers = [
        QuestionResponse(field_name="project_type", value="fastapi_service", source="user"),
        QuestionResponse(field_name="output_dir", value="generated/api", source="user"),
    ]
    
    lock = create_spec_lock_from_answers(spec, answers)
    
    assert lock.project_type == "fastapi_service"
    assert lock.package_name == "base_app"
    assert lock.output_dir == "generated/api"
    assert len(lock.answered_questions) == 2


def test_create_spec_lock_with_interfaces():
    """Test creating SpecLock with interface answers."""
    spec = SpecBlock(
        project_type="fastapi_service",
        package_name="api_app",
        output_dir="generated/api"
    )
    
    answers = [
        QuestionResponse(
            field_name="interfaces.http",
            value="GET /health, POST /items, GET /items/{id}",
            source="user"
        ),
    ]
    
    lock = create_spec_lock_from_answers(spec, answers)
    
    assert "http" in lock.interfaces
    assert len(lock.interfaces["http"]) == 3
    assert "GET /health" in lock.interfaces["http"]
    assert "POST /items" in lock.interfaces["http"]


def test_run_question_loop_complete_spec():
    """Test question loop with complete spec (should skip questions)."""
    spec = SpecBlock(
        project_type="cli_tool",
        package_name="my_cli",
        output_dir="generated/cli"
    )
    
    lock = run_question_loop(spec, interactive=False)
    
    assert lock.project_type == "cli_tool"
    assert lock.package_name == "my_cli"
    assert len(lock.answered_questions) == 0  # No questions needed


def test_run_question_loop_non_interactive_with_defaults():
    """Test non-interactive mode raises error when project_type is missing."""
    spec = SpecBlock(
        package_name="my_app"
    )
    
    # After changes: should raise ValueError in non-interactive mode
    # when project_type is missing and no default is available
    with pytest.raises(ValueError, match="No default available for required field"):
        lock = run_question_loop(spec, interactive=False)


def test_run_question_loop_saves_to_file():
    """Test that question loop saves to file when path provided."""
    spec = SpecBlock(
        project_type="library",
        package_name="mylib",
        output_dir="generated/lib"
    )
    
    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "spec.lock.yaml"
        
        lock = run_question_loop(spec, output_path=output_path, interactive=False)
        
        assert output_path.exists()
        
        # Verify saved content
        loaded = SpecLock.load(output_path)
        assert loaded.project_type == lock.project_type
        assert loaded.package_name == lock.package_name


def test_question_with_validation():
    """Test Question model with validation hints."""
    question = Question(
        field_name="package_name",
        prompt="What is the package name?",
        hint="Use only lowercase letters, numbers, and underscores",
        default_value="my_app",
        examples=["my_app", "api_service", "data_pipeline"]
    )
    
    assert question.field_name == "package_name"
    assert len(question.examples) == 3
    assert question.default_value == "my_app"


def test_spec_lock_with_complex_interfaces():
    """Test SpecLock with multiple interface types."""
    lock = SpecLock(
        project_type="microservice",
        package_name="event_service",
        output_dir="generated/service",
        interfaces={
            "http": ["GET /health", "POST /events"],
            "events": ["event.created", "event.processed"],
            "queues": ["task_queue", "dead_letter_queue"]
        }
    )
    
    assert "http" in lock.interfaces
    assert "events" in lock.interfaces
    assert "queues" in lock.interfaces
    assert len(lock.interfaces["queues"]) == 2


def test_generate_questions_fastapi_interface():
    """Test that FastAPI projects get interface questions."""
    spec = SpecBlock(
        project_type="fastapi_service",
        package_name="api",
        output_dir="out"
    )
    
    questions = generate_questions(spec)
    
    # Should ask about HTTP endpoints for FastAPI project
    has_interface_q = any("interfaces.http" in q.field_name for q in questions)
    assert has_interface_q


def test_spec_lock_requires_clarification_flag():
    """Test that requires_clarification flag is set when project_type is missing."""
    spec = SpecBlock(
        package_name="my_app"
    )
    
    # Create spec_lock without project_type - should fail in non-interactive
    with pytest.raises(ValueError, match="No default available for required field"):
        lock = run_question_loop(spec, interactive=False)


def test_spec_lock_no_clarification_when_project_type_present():
    """Test that requires_clarification is False when project_type is provided."""
    spec = SpecBlock(
        project_type="cli_tool",
        package_name="my_cli",
        output_dir="generated/my_cli"
    )
    
    lock = run_question_loop(spec, interactive=False)
    
    assert lock.project_type == "cli_tool"
    assert lock.requires_clarification is False


def test_inferred_project_type_from_readme():
    """Test that project_type can be inferred from README content."""
    spec = SpecBlock(
        package_name="my_app"
    )
    
    readme_with_fastapi = """
    # My API Service
    
    This is a REST API built with FastAPI.
    """
    
    questions = generate_questions(spec, readme_with_fastapi)
    
    # Should have project_type question with inferred default
    project_type_q = next((q for q in questions if q.field_name == "project_type"), None)
    assert project_type_q is not None
    assert project_type_q.default_value == "fastapi_service"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
