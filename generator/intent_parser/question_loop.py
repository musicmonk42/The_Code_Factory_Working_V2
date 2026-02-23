# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Question Loop — interactive gap-filling for incomplete specifications.

This module implements a structured question system that identifies missing
required fields in a :class:`~generator.intent_parser.spec_block.SpecBlock`
and prompts the user to supply them interactively.  It persists the resolved
specification to ``spec.lock.yaml`` which drives deterministic code generation.

Architecture
------------
* :class:`Question`             — interrogation unit with optional inline
  validation via the *validation function registry*.
* :class:`QuestionResponse`     — typed answer container (Pydantic model).
* :class:`SpecLock`             — fully-resolved specification after all
  questions have been answered; persisted as YAML.
* :func:`generate_questions`    — derives the minimal set of questions from a
  potentially-incomplete :class:`SpecBlock`.
* :func:`register_validator`    — extend the built-in validation registry with
  domain-specific validators at runtime.
* :func:`run_question_loop`     — high-level entry-point: ask questions, build
  lock, write ``spec.lock.yaml``.

Validation Registry
-------------------
The registry maps *name strings* to callables
``(str) -> (bool, Optional[str])``.  When a :class:`Question` has a
non-``None`` :attr:`~Question.validation_fn`, its value is looked up in the
registry before the user's answer is accepted.  If the validator returns
``(False, <msg>)`` the error message is printed and the user is re-prompted
without incrementing a retry counter.

Built-in validators
~~~~~~~~~~~~~~~~~~~
* ``validate_package_name``   — PEP 8 module-name regex.
* ``validate_output_dir``     — safe relative path (no traversal, no absolute).
* ``validate_project_type``   — membership in the known project-type set.
* ``validate_not_empty``      — non-blank string.
* ``validate_python_version`` — ``MAJOR.MINOR[.PATCH]`` version string.
* ``validate_http_endpoint``  — ``METHOD /path`` format (comma-separated).

External code may add validators via :func:`register_validator` at any time.

Industry Standards
------------------
* Interactive CLI with clear prompts and context-sensitive hints.
* YAML-based persistence for answered specifications.
* Validation at every user-input step with human-readable error messages.
* Resume capability via ``spec.lock.yaml``.
* Structured logging with ``extra={}`` on every call.
"""

from __future__ import annotations

import json
import logging
import re as _re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import yaml
from pydantic import BaseModel, Field

from generator.intent_parser.spec_block import SpecBlock

logger = logging.getLogger(__name__)

# ===========================================================================
# Validation function registry
# ===========================================================================

# Canonical set of project types the platform can scaffold.
_KNOWN_PROJECT_TYPES: frozenset = frozenset(
    {
        "fastapi_service",
        "flask_service",
        "cli_tool",
        "library",
        "batch_job",
        "lambda_function",
        "microservice",
        "worker",
        "grpc_service",
        "graphql_service",
    }
)

# Registry: name → (value: str) -> (is_valid: bool, error_msg: Optional[str])
_VALIDATOR_REGISTRY: Dict[str, Callable[[str], Tuple[bool, Optional[str]]]] = {}


def register_validator(
    name: str,
    fn: Callable[[str], Tuple[bool, Optional[str]]],
) -> None:
    """Register or override a validation function in the global registry.

    Parameters
    ----------
    name:
        String key used in :attr:`Question.validation_fn`.  Existing entries
        are silently overwritten so that plugins can replace built-in validators.
    fn:
        Callable ``(str) -> (bool, Optional[str])``.  Return
        ``(True, None)`` for valid input, or ``(False, "<human-readable error
        message>")`` for invalid input.

    Raises
    ------
    TypeError
        If *name* is not a non-empty ``str`` or *fn* is not callable.
    """
    if not isinstance(name, str) or not name:
        raise TypeError("Validator name must be a non-empty string.")
    if not callable(fn):
        raise TypeError(f"Validator {name!r} must be callable.")
    _VALIDATOR_REGISTRY[name] = fn
    logger.debug(
        "register_validator: registered validator.",
        extra={"name": name},
    )


def get_validator(
    name: str,
) -> Optional[Callable[[str], Tuple[bool, Optional[str]]]]:
    """Return the validator registered under *name*, or ``None`` if absent.

    Parameters
    ----------
    name:
        Registry key to look up.

    Returns
    -------
    callable or None
    """
    return _VALIDATOR_REGISTRY.get(name)


# ---------------------------------------------------------------------------
# Built-in validator implementations
# ---------------------------------------------------------------------------


def _validate_package_name(value: str) -> Tuple[bool, Optional[str]]:
    """PEP 8 Python package / module name.

    Must start with a lowercase ASCII letter and consist only of lowercase
    letters, ASCII digits, and underscores.  Length is capped at 64
    characters to match PyPI naming conventions.
    """
    if not value:
        return False, "Package name must not be empty."
    if len(value) > 64:
        return False, "Package name must not exceed 64 characters."
    if _re.fullmatch(r"[a-z][a-z0-9_]*", value):
        return True, None
    return (
        False,
        "Package name must start with a lowercase letter and contain only "
        "lowercase letters, digits, and underscores (e.g. 'my_app', 'user_service').",
    )


def _validate_output_dir(value: str) -> Tuple[bool, Optional[str]]:
    """Safe relative output directory path.

    Rejects absolute paths, path-traversal components (``..``), and any
    characters outside the safe set ``[A-Za-z0-9_./ -]``.
    """
    if not value:
        return False, "Output directory must not be empty."
    # Reject absolute paths
    if value.startswith("/") or _re.match(r"^[A-Za-z]:[/\\]", value):
        return False, "Output directory must be a relative path, not an absolute path."
    # Reject traversal in any path segment (POSIX or Windows separators)
    parts = _re.split(r"[/\\]", value)
    if ".." in parts:
        return False, "Output directory must not contain path traversal components ('..')."
    # Allow only safe characters
    if not _re.fullmatch(r"[A-Za-z0-9_./ -]+", value):
        return (
            False,
            "Output directory contains invalid characters. "
            "Use only letters, digits, underscores, hyphens, spaces, and slashes.",
        )
    return True, None


def _validate_project_type(value: str) -> Tuple[bool, Optional[str]]:
    """Membership check against the known project-type set."""
    if value in _KNOWN_PROJECT_TYPES:
        return True, None
    known = ", ".join(sorted(_KNOWN_PROJECT_TYPES))
    return (
        False,
        f"Unknown project type {value!r}.\n"
        f"  Known types: {known}.",
    )


def _validate_not_empty(value: str) -> Tuple[bool, Optional[str]]:
    """Non-blank string check."""
    if value.strip():
        return True, None
    return False, "This field is required and must not be blank."


def _validate_python_version(value: str) -> Tuple[bool, Optional[str]]:
    """Semantic version string in ``MAJOR.MINOR`` or ``MAJOR.MINOR.PATCH`` form.

    Enforces Python ≥ 3.8 — the minimum version supported by the platform.
    Python 2.x and 3.0–3.7 are rejected with an explicit end-of-life message.
    """
    if _re.fullmatch(r"\d+\.\d+(\.\d+)?", value):
        parts = value.split(".")
        major, minor = int(parts[0]), int(parts[1])
        if major < 3 or (major == 3 and minor < 8):
            return (
                False,
                f"Python {value} is not supported by this platform. "
                "Minimum required version is Python 3.8.",
            )
        return True, None
    return (
        False,
        "Python version must be in the form MAJOR.MINOR or MAJOR.MINOR.PATCH "
        "(e.g. '3.11' or '3.12.4').",
    )


def _validate_http_endpoint(value: str) -> Tuple[bool, Optional[str]]:
    """HTTP endpoint in ``METHOD /path`` form (comma-separated list allowed)."""
    _VALID_METHODS = frozenset(
        {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
    )
    endpoints = [e.strip() for e in value.split(",") if e.strip()]
    if not endpoints:
        return False, "At least one HTTP endpoint must be specified."
    for ep in endpoints:
        parts = ep.split(None, 1)
        if len(parts) != 2:
            return (
                False,
                f"Endpoint {ep!r} must be in 'METHOD /path' format "
                "(e.g. 'GET /health, POST /items').",
            )
        method, path = parts
        if method.upper() not in _VALID_METHODS:
            return (
                False,
                f"Unknown HTTP method {method!r}. "
                f"Valid methods: {', '.join(sorted(_VALID_METHODS))}.",
            )
        if not path.startswith("/"):
            return False, f"Path {path!r} must start with '/'."
    return True, None


# Register all built-in validators.
register_validator("validate_package_name", _validate_package_name)
register_validator("validate_output_dir", _validate_output_dir)
register_validator("validate_project_type", _validate_project_type)
register_validator("validate_not_empty", _validate_not_empty)
register_validator("validate_python_version", _validate_python_version)
register_validator("validate_http_endpoint", _validate_http_endpoint)


class QuestionResponse(BaseModel):
    """Response to a specification question."""
    
    field_name: str
    value: Any
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source: str = Field(default="user", description="user, inferred, or default")


class SpecLock(BaseModel):
    """
    Locked specification after all questions are answered.
    
    This is the authoritative specification used for generation, combining:
    - Spec block from README
    - User answers to questions
    - Inferred values from README text
    - Sensible defaults
    
    Note: project_type is now Optional. When None, requires_clarification 
    will be True, and downstream stages should return clarification questions
    instead of proceeding with generation.
    """
    
    project_type: Optional[str] = None
    package_name: str
    module_name: Optional[str] = None
    output_dir: str
    interfaces: Dict[str, List[str]] = Field(default_factory=dict)
    dependencies: List[str] = Field(default_factory=list)
    nonfunctional: List[str] = Field(default_factory=list)
    adapters: Dict[str, str] = Field(default_factory=dict)
    acceptance_checks: List[str] = Field(default_factory=list)
    
    # Metadata
    schema_version: str = "1.0"
    generated_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    answered_questions: List[QuestionResponse] = Field(default_factory=list)
    requires_clarification: bool = False  # Flag when project_type is missing/uncertain
    
    def save(self, path: Path) -> None:
        """Save spec lock to YAML file."""
        with open(path, "w") as f:
            yaml.safe_dump(self.model_dump(), f, sort_keys=False, default_flow_style=False)
        logger.info(f"Saved spec lock to {path}")
    
    @classmethod
    def load(cls, path: Path) -> "SpecLock":
        """Load spec lock from YAML file."""
        with open(path, "r") as f:
            data = yaml.safe_load(f)
        return cls(**data)


class Question(BaseModel):
    """A question to ask the user about missing specification fields."""
    
    field_name: str
    prompt: str
    hint: Optional[str] = None
    default_value: Optional[Any] = None
    validation_fn: Optional[str] = None  # Name of validation function
    examples: List[str] = Field(default_factory=list)
    
    def ask(self, interactive: bool = True) -> QuestionResponse:
        """
        Ask the question and get a response.
        
        Args:
            interactive: If False, use default value without prompting
            
        Returns:
            QuestionResponse with the answer
        """
        if not interactive:
            if self.default_value is not None:
                logger.info(f"Using default for {self.field_name}: {self.default_value}")
                return QuestionResponse(
                    field_name=self.field_name,
                    value=self.default_value,
                    confidence=0.5,
                    source="default"
                )
            else:
                raise ValueError(f"No default available for required field: {self.field_name}")
        
        # Interactive prompt
        print(f"\n{'='*60}")
        print(f"Question: {self.prompt}")
        if self.hint:
            print(f"Hint: {self.hint}")
        if self.examples:
            print(f"Examples: {', '.join(self.examples)}")
        if self.default_value:
            print(f"Default: {self.default_value}")
        print('='*60)
        
        while True:
            if self.default_value:
                user_input = input(f"Your answer [{self.default_value}]: ").strip()
                if not user_input:
                    user_input = str(self.default_value)
            else:
                user_input = input("Your answer: ").strip()
            
            if user_input:
                # Validate using the registered validator, if any.
                if self.validation_fn and self.validation_fn in _VALIDATOR_REGISTRY:
                    valid, error_msg = _VALIDATOR_REGISTRY[self.validation_fn](user_input)
                    if not valid:
                        print(f"Invalid input: {error_msg}")
                        continue
                return QuestionResponse(
                    field_name=self.field_name,
                    value=user_input,
                    confidence=1.0,
                    source="user"
                )
            elif self.default_value:
                return QuestionResponse(
                    field_name=self.field_name,
                    value=self.default_value,
                    confidence=0.7,
                    source="default"
                )
            else:
                print("This field is required. Please provide a value.")


def generate_questions(spec: SpecBlock, readme_content: Optional[str] = None) -> List[Question]:
    """
    Generate questions for missing required fields in specification.
    
    Args:
        spec: The SpecBlock (may be incomplete)
        readme_content: Optional README content for context/inference
        
    Returns:
        List of Question objects for missing fields
    """
    questions = []
    
    # Question 1: Project Type
    if not spec.project_type:
        # Try to infer from readme
        inferred_type = None
        default_type = None  # No default - require explicit choice
        
        if readme_content:
            content_lower = readme_content.lower()
            if "fastapi" in content_lower or "rest api" in content_lower:
                inferred_type = "fastapi_service"
            elif "cli" in content_lower or "command line" in content_lower:
                inferred_type = "cli_tool"
            elif "library" in content_lower or "package" in content_lower:
                inferred_type = "library"
            elif "batch" in content_lower:  # Matches both 'batch' and 'batch job' (substring)
                inferred_type = "batch_job"
        
        questions.append(Question(
            field_name="project_type",
            prompt="What type of project are you building?",
            hint="This determines the scaffolding, structure, and generated files. Required.",
            default_value=inferred_type,  # Only use inferred type, no default
            validation_fn="validate_project_type",
            examples=[
                "fastapi_service",
                "cli_tool",
                "library",
                "batch_job",
                "lambda_function"
            ]
        ))
    
    # Question 2: Package/Module Name
    if not spec.package_name and not spec.module_name:
        # Try to extract from output_dir
        default_name = None
        if spec.output_dir:
            # e.g., "generated/my_app" -> "my_app"
            parts = spec.output_dir.split("/")
            default_name = parts[-1] if parts else None
        
        if not default_name and readme_content:
            # Look for "# ProjectName" style headers
            match = _re.search(r'^#\s+([A-Za-z_][A-Za-z0-9_]*)', readme_content, _re.MULTILINE)
            if match:
                default_name = match.group(1).lower().replace("-", "_")
        
        questions.append(Question(
            field_name="package_name",
            prompt="What is the Python package/module name?",
            hint="This will be used for imports: 'from <name> import ...'",
            default_value=default_name or "my_app",
            validation_fn="validate_package_name",
            examples=["my_app", "user_service", "data_pipeline"]
        ))
    
    # Question 3: Output Directory
    if not spec.output_dir:
        # Derive from package_name if available
        pkg_name = spec.package_name or spec.module_name
        default_dir = f"generated/{pkg_name}" if pkg_name else "generated/my_app"
        
        questions.append(Question(
            field_name="output_dir",
            prompt="Where should the generated code be written?",
            hint="Relative path from the current directory",
            default_value=default_dir,
            validation_fn="validate_output_dir",
            examples=["generated/my_app", "output/service", "my_project"]
        ))
    
    # Question 4: Interfaces (only if project_type suggests it's needed)
    if spec.project_type in ["fastapi_service", "flask_service", "microservice"]:
        if not spec.interfaces or not spec.interfaces.http:
            questions.append(Question(
                field_name="interfaces.http",
                prompt="What HTTP endpoints should this service expose? (comma-separated)",
                hint="Format: METHOD /path, e.g., 'GET /health, POST /items'",
                default_value="GET /health",
                examples=["GET /health", "GET /items, POST /items", "GET /api/v1/users"]
            ))
    
    return questions


def create_spec_lock_from_answers(
    spec: SpecBlock,
    answers: List[QuestionResponse],
    readme_content: Optional[str] = None
) -> SpecLock:
    """
    Create a locked specification from the original spec and user answers.
    
    Args:
        spec: Original (possibly incomplete) SpecBlock
        answers: List of QuestionResponse from answered questions
        readme_content: Optional README for additional inference
        
    Returns:
        Complete SpecLock ready for generation
    """
    # Start with values from spec
    data: Dict[str, Any] = {
        "project_type": spec.project_type,
        "package_name": spec.package_name or spec.module_name,
        "module_name": spec.module_name or spec.package_name,
        "output_dir": spec.output_dir,
        "interfaces": {},
        "dependencies": spec.dependencies.copy() if spec.dependencies else [],
        "nonfunctional": spec.nonfunctional.copy() if spec.nonfunctional else [],
        "adapters": spec.adapters.copy() if spec.adapters else {},
        "acceptance_checks": spec.acceptance_checks.copy() if spec.acceptance_checks else [],
    }
    
    # Add interfaces if present
    if spec.interfaces:
        if spec.interfaces.http:
            data["interfaces"]["http"] = spec.interfaces.http
        if spec.interfaces.events:
            data["interfaces"]["events"] = spec.interfaces.events
        if spec.interfaces.queues:
            data["interfaces"]["queues"] = spec.interfaces.queues
    
    # Apply answers to override/fill missing fields
    for answer in answers:
        field_name = answer.field_name
        value = answer.value
        
        if field_name == "project_type":
            data["project_type"] = value
        elif field_name == "package_name":
            data["package_name"] = value
            if not data["module_name"]:
                data["module_name"] = value
        elif field_name == "output_dir":
            data["output_dir"] = value
        elif field_name == "interfaces.http":
            # Parse comma-separated endpoints
            if isinstance(value, str):
                endpoints = [e.strip() for e in value.split(",") if e.strip()]
                data["interfaces"]["http"] = endpoints
            else:
                data["interfaces"]["http"] = value
        else:
            # Handle nested fields (e.g., "adapters.database")
            if "." in field_name:
                parts = field_name.split(".")
                current = data
                for part in parts[:-1]:
                    if part not in current:
                        current[part] = {}
                    current = current[part]
                current[parts[-1]] = value
            else:
                data[field_name] = value
    
    # Ensure required fields are present (except project_type which can be None)
    # If project_type is missing, we'll set requires_clarification flag
    if not data["package_name"]:
        data["package_name"] = "my_app"  # Fallback
    if not data["output_dir"]:
        data["output_dir"] = f"generated/{data['package_name']}"
    
    # Set requires_clarification flag if project_type is missing
    requires_clarification = not data.get("project_type")
    
    # Create and return SpecLock
    lock = SpecLock(**data)
    lock.answered_questions = answers
    lock.requires_clarification = requires_clarification
    return lock


def run_question_loop(
    spec: SpecBlock,
    readme_content: Optional[str] = None,
    output_path: Optional[Path] = None,
    interactive: bool = True
) -> SpecLock:
    """
    Run the interactive question loop to complete the specification.
    
    Args:
        spec: Initial SpecBlock (may be incomplete)
        readme_content: Optional README content for context
        output_path: Optional path to save spec.lock.yaml
        interactive: If False, use defaults without prompting
        
    Returns:
        Complete SpecLock ready for generation
        
    Raises:
        ValueError: If non-interactive mode and required fields lack defaults
    """
    logger.info("Starting question loop for specification gap-filling")
    
    # Check if spec is already complete
    if spec.is_complete():
        logger.info("Specification is already complete, no questions needed")
        # Still create SpecLock for consistency
        lock = SpecLock(
            project_type=spec.project_type,
            package_name=spec.package_name or spec.module_name,
            module_name=spec.module_name or spec.package_name,
            output_dir=spec.output_dir,
            interfaces=(
                {
                    "http": spec.interfaces.http if spec.interfaces else [],
                    "events": spec.interfaces.events if spec.interfaces else [],
                }
                if spec.interfaces
                else {}
            ),
            dependencies=spec.dependencies,
            nonfunctional=spec.nonfunctional,
            adapters=spec.adapters,
            acceptance_checks=spec.acceptance_checks,
        )
        # Set requires_clarification if project_type is missing
        lock.requires_clarification = not spec.project_type
        if output_path:
            lock.save(output_path)
        return lock
    
    # Generate questions for missing fields
    questions = generate_questions(spec, readme_content)
    
    if not questions:
        logger.warning("No questions generated but spec incomplete - using defaults")
        # Create lock with defaults (except project_type - allow None)
        lock = SpecLock(
            project_type=spec.project_type,  # No default - can be None
            package_name=spec.package_name or spec.module_name or "my_app",
            module_name=spec.module_name or spec.package_name or "my_app",
            output_dir=spec.output_dir or "generated/my_app",
        )
        # Set requires_clarification if project_type is missing
        lock.requires_clarification = not spec.project_type
        if output_path:
            lock.save(output_path)
        return lock
    
    logger.info(f"Generated {len(questions)} questions for missing fields")
    
    # Ask questions and collect answers
    answers: List[QuestionResponse] = []
    
    if interactive:
        print(f"\n{'*'*60}")
        print("SPECIFICATION GAP-FILLING")
        print(f"{'*'*60}")
        print(f"\nThe specification is incomplete. Please answer {len(questions)} question(s):")
    
    for i, question in enumerate(questions, 1):
        if interactive:
            print(f"\n[Question {i}/{len(questions)}]")
        
        try:
            answer = question.ask(interactive=interactive)
            answers.append(answer)
        except ValueError as e:
            logger.error(f"Failed to answer required question: {e}")
            raise
    
    # Create locked specification
    lock = create_spec_lock_from_answers(spec, answers, readme_content)
    
    # Save to file if path provided
    if output_path:
        lock.save(output_path)
        if interactive:
            print(f"\n✓ Specification saved to {output_path}")
    
    if interactive:
        print(f"\n{'*'*60}")
        print("SPECIFICATION COMPLETE")
        print(f"{'*'*60}")
        print(f"\nProject Type: {lock.project_type}")
        print(f"Package Name: {lock.package_name}")
        print(f"Output Directory: {lock.output_dir}")
        if lock.interfaces.get("http"):
            print(f"HTTP Endpoints: {', '.join(lock.interfaces['http'])}")
        print()
    
    logger.info("Question loop completed successfully")
    return lock


__all__ = [
    "Question",
    "QuestionResponse",
    "SpecLock",
    "generate_questions",
    "create_spec_lock_from_answers",
    "run_question_loop",
    "register_validator",
    "get_validator",
    "_KNOWN_PROJECT_TYPES",
    "_VALIDATOR_REGISTRY",
]
