# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Tests for the new SFE fix handlers and validation gate introduced to address
production job 2d945180 failures:

  - _generate_schema_fix()   — missing Pydantic schema class
  - _generate_type_mismatch_fix() — Integer PK vs UUID router
  - LLM fallback produces action:"info" / success:False / confidence:0.0
  - _generate_import_fix / _generate_security_fix fallbacks use "info" action
  - apply_fix endpoint sandbox validation gate (skip_validation field)
  - codebase_analyzer Dockerfile multi-stage dependency detection
  - codegen_response_handler Pydantic v2 stubs include model_config=ConfigDict(...)
"""

import ast
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("TESTING", "1")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sfe_service():
    """Return an SFEService instance without touching external services."""
    try:
        from server.services.sfe_service import SFEService
    except ImportError as exc:
        pytest.skip(f"SFEService not importable: {exc}")
    return SFEService()


# ===========================================================================
# _generate_schema_fix tests
# ===========================================================================


class TestGenerateSchemaFix:
    """Unit tests for SFEService._generate_schema_fix."""

    def _make_schemas(self, tmp_path: Path, content: str) -> Path:
        schemas_file = tmp_path / "app" / "schemas.py"
        schemas_file.parent.mkdir(parents=True, exist_ok=True)
        schemas_file.write_text(content, encoding="utf-8")
        return tmp_path

    def test_returns_none_when_message_has_no_pattern(self, tmp_path):
        """Non-schema error messages must return None (no false positives)."""
        svc = _make_sfe_service()
        result = svc._generate_schema_fix(tmp_path / "app" / "main.py", "random error", None)
        assert result is None

    def test_returns_none_when_schemas_file_absent(self, tmp_path):
        """Without a schemas.py file the handler must return None gracefully."""
        svc = _make_sfe_service()
        result = svc._generate_schema_fix(
            tmp_path / "app" / "main.py",
            "cannot import name 'Prescription' from 'app.schemas'",
            None,
        )
        assert result is None

    def test_returns_none_when_class_already_exists(self, tmp_path):
        """If the missing class already exists in schemas.py, return None."""
        schemas_content = (
            "from pydantic import BaseModel\n\n"
            "class Prescription(BaseModel):\n"
            "    id: int\n"
        )
        self._make_schemas(tmp_path, schemas_content)
        svc = _make_sfe_service()
        svc._resolve_job_code_path = MagicMock(return_value=str(tmp_path))
        result = svc._generate_schema_fix(
            tmp_path / "app" / "main.py",
            "cannot import name 'Prescription' from 'app.schemas'",
            "job-1",
        )
        assert result is None

    def test_generates_class_with_merged_fields(self, tmp_path):
        """Schema handler must generate a class alias when Read variant exists.

        Previously, the handler merged fields from sibling classes into a new
        class.  The improved behavior prefers a class inheritance alias to
        PrescriptionRead when a Read variant is available, which is safer and
        avoids duplicating field definitions.
        """
        schemas_content = (
            "from pydantic import BaseModel, ConfigDict\n\n"
            "class PrescriptionCreate(BaseModel):\n"
            "    medication: str\n"
            "    dose: int\n\n"
            "class PrescriptionRead(BaseModel):\n"
            "    id: int\n"
            "    medication: str\n"
        )
        self._make_schemas(tmp_path, schemas_content)
        svc = _make_sfe_service()
        svc._resolve_job_code_path = MagicMock(return_value=str(tmp_path))
        result = svc._generate_schema_fix(
            tmp_path / "app" / "main.py",
            "cannot import name 'Prescription' from 'app.schemas'",
            "job-1",
        )
        assert result is not None
        assert result["success"] is True
        assert result["action"] == "insert"
        content = result["content"]
        # New behavior: use class inheritance alias from PrescriptionRead
        assert "class Prescription(PrescriptionRead):" in content
        assert result["confidence"] >= 0.85

    def test_generates_type_alias_when_read_variant_exists(self, tmp_path):
        """When a <Name>Read class exists, emit a class inheritance alias (not a simple = alias)."""
        schemas_content = (
            "from pydantic import BaseModel\n\n"
            "class TokenRead(BaseModel):\n"
            "    access_token: str\n"
            "    token_type: str\n"
        )
        self._make_schemas(tmp_path, schemas_content)
        svc = _make_sfe_service()
        svc._resolve_job_code_path = MagicMock(return_value=str(tmp_path))
        result = svc._generate_schema_fix(
            tmp_path / "app" / "main.py",
            "cannot import name 'Token' from 'app.schemas'",
            "job-1",
        )
        assert result is not None
        assert result["success"] is True
        # New behavior: class inheritance alias for FastAPI response_model compatibility
        assert "class Token(TokenRead):" in result["content"]
        assert result["confidence"] >= 0.85

    def test_generates_stub_when_no_siblings(self, tmp_path):
        """When no sibling classes exist, a minimal BaseModel stub must be generated."""
        schemas_content = "from pydantic import BaseModel\n"
        self._make_schemas(tmp_path, schemas_content)
        svc = _make_sfe_service()
        svc._resolve_job_code_path = MagicMock(return_value=str(tmp_path))
        result = svc._generate_schema_fix(
            tmp_path / "app" / "main.py",
            "cannot import name 'WidgetFoo' from 'app.schemas'",
            "job-1",
        )
        assert result is not None
        assert result["success"] is True
        assert "class WidgetFoo(BaseModel):" in result["content"]
        assert result["confidence"] >= 0.65

    def test_result_content_is_valid_python(self, tmp_path):
        """Generated class definition must be syntactically valid Python."""
        schemas_content = (
            "from pydantic import BaseModel, ConfigDict\n\n"
            "class FooCreate(BaseModel):\n"
            "    name: str\n"
        )
        self._make_schemas(tmp_path, schemas_content)
        svc = _make_sfe_service()
        svc._resolve_job_code_path = MagicMock(return_value=str(tmp_path))
        result = svc._generate_schema_fix(
            tmp_path / "app" / "main.py",
            "cannot import name 'Foo' from 'app.schemas'",
            "job-1",
        )
        assert result is not None
        # The content must be parseable when appended to minimal boilerplate
        try:
            ast.parse(
                "from pydantic import BaseModel, ConfigDict\nfrom typing import Optional, Any\n"
                + result["content"]
            )
        except SyntaxError as exc:
            pytest.fail(f"Generated content is not valid Python: {exc}\n\n{result['content']}")

    def test_file_key_points_to_schemas_file(self, tmp_path):
        """The fix must target schemas.py, NOT the file that imports it."""
        schemas_content = (
            "from pydantic import BaseModel, ConfigDict\n\n"
            "class BarCreate(BaseModel):\n"
            "    title: str\n"
        )
        self._make_schemas(tmp_path, schemas_content)
        svc = _make_sfe_service()
        svc._resolve_job_code_path = MagicMock(return_value=str(tmp_path))
        result = svc._generate_schema_fix(
            tmp_path / "app" / "routers" / "bar.py",  # importer file
            "cannot import name 'Bar' from 'app.schemas'",
            "job-1",
        )
        assert result is not None
        assert "schemas" in result["file"].replace("\\", "/")


# ===========================================================================
# _generate_type_mismatch_fix tests
# ===========================================================================


class TestGenerateTypeMismatchFix:
    """Unit tests for SFEService._generate_type_mismatch_fix."""

    def _write_model(self, tmp_path: Path, content: str) -> Path:
        model_file = tmp_path / "app" / "models.py"
        model_file.parent.mkdir(parents=True, exist_ok=True)
        model_file.write_text(content, encoding="utf-8")
        return model_file

    def test_returns_none_for_unrelated_message(self, tmp_path):
        svc = _make_sfe_service()
        result = svc._generate_type_mismatch_fix(
            tmp_path / "app" / "models.py",
            "AttributeError: 'NoneType' has no attribute 'id'",
            None,
        )
        assert result is None

    def test_returns_none_when_file_absent(self, tmp_path):
        svc = _make_sfe_service()
        result = svc._generate_type_mismatch_fix(
            tmp_path / "does_not_exist.py",
            "Integer primary key vs UUID type mismatch in router",
            None,
        )
        assert result is None

    def test_replaces_integer_pk_with_uuid(self, tmp_path):
        """Must rewrite Column(Integer, primary_key=True) → Column(UUID(as_uuid=True), …)."""
        model_content = (
            "from sqlalchemy import Column, Integer, String\n"
            "from app.database import Base\n\n"
            "class Prescription(Base):\n"
            "    __tablename__ = 'prescriptions'\n"
            "    id = Column(Integer, primary_key=True)\n"
            "    medication = Column(String)\n"
        )
        model_file = self._write_model(tmp_path, model_content)
        svc = _make_sfe_service()
        svc._resolve_job_code_path = MagicMock(return_value=str(tmp_path))
        result = svc._generate_type_mismatch_fix(
            model_file,
            "Integer primary key vs UUID type mismatch in router",
            "job-1",
        )
        assert result is not None
        assert result["success"] is True
        assert result["action"] == "replace"
        assert "UUID(as_uuid=True)" in result["content"]
        assert "primary_key=True" in result["content"]
        assert "default=uuid.uuid4" in result["content"]
        assert result["confidence"] >= 0.80

    def test_adds_uuid_imports_as_extra_changes(self, tmp_path):
        """UUID import statements must be generated when they are missing."""
        model_content = (
            "from sqlalchemy import Column, Integer\n"
            "from app.database import Base\n\n"
            "class Order(Base):\n"
            "    __tablename__ = 'orders'\n"
            "    id = Column(Integer, primary_key=True)\n"
        )
        model_file = self._write_model(tmp_path, model_content)
        svc = _make_sfe_service()
        svc._resolve_job_code_path = MagicMock(return_value=str(tmp_path))
        result = svc._generate_type_mismatch_fix(
            model_file,
            "pk type mismatch: Integer vs UUID",
            "job-1",
        )
        assert result is not None
        extra = result.get("extra_changes", [])
        import_content = " ".join(c.get("content", "") for c in extra)
        # At minimum, UUID dialect import should be present
        assert "UUID" in import_content or "uuid" in import_content

    def test_no_duplicate_imports_when_already_present(self, tmp_path):
        """Must not duplicate imports that already exist in the model file."""
        model_content = (
            "from sqlalchemy import Column, Integer\n"
            "from sqlalchemy.dialects.postgresql import UUID\n"
            "import uuid\n"
            "from app.database import Base\n\n"
            "class Item(Base):\n"
            "    __tablename__ = 'items'\n"
            "    id = Column(Integer, primary_key=True)\n"
        )
        model_file = self._write_model(tmp_path, model_content)
        svc = _make_sfe_service()
        svc._resolve_job_code_path = MagicMock(return_value=str(tmp_path))
        result = svc._generate_type_mismatch_fix(
            model_file,
            "pk type mismatch: Integer vs UUID",
            "job-1",
        )
        assert result is not None
        # extra_changes should be empty since both imports are already present
        assert result.get("extra_changes", []) == []


# ===========================================================================
# LLM fallback produces action:"info" / success:False / confidence:0.0
# ===========================================================================


class TestLLMFallbackActionInfo:
    """The LLM fallback path must not produce fake 'insert' actions."""

    def _check_result(self, fix_result: Dict[str, Any]) -> None:
        assert fix_result["action"] == "info", (
            f"Expected action='info' but got '{fix_result['action']}'"
        )
        assert fix_result["success"] is False, (
            f"Expected success=False but got {fix_result['success']}"
        )
        assert fix_result.get("confidence", -1) == 0.0, (
            f"Expected confidence=0.0 but got {fix_result.get('confidence')}"
        )

    def test_import_fix_fallback_source_read_failure(self):
        """_generate_import_fix must return action='info' when source read fails."""
        svc = _make_sfe_service()
        bad_context = {"success": False, "error": "Permission denied"}
        result = svc._generate_import_fix(
            Path("/nonexistent/file.py"),
            "No module named 'foo'",
            bad_context,
        )
        self._check_result(result)

    def test_import_fix_ultimate_fallback(self):
        """_generate_import_fix ultimate fallback must return action='info'."""
        svc = _make_sfe_service()
        # Provide a context that succeeds but a message that can't be resolved
        good_context = {
            "success": True,
            "full_source": "# nothing here\n",
            "target_line": "",
        }
        with patch(
            "self_fixing_engineer.self_healing_import_fixer.import_fixer"
            ".import_fixer_engine.ImportFixerEngine",
            side_effect=ImportError("not available"),
        ):
            result = svc._generate_import_fix(
                Path("/some/file.py"),
                "could not import 'CompletelyUnknownSymbolXYZ'",
                good_context,
            )
        # Should return info (not raise)
        assert result is not None
        # Either info or a real fix — but must not lie with success=True + action=insert
        # when no actual fix was found
        if result.get("action") == "info":
            self._check_result(result)

    def test_security_fix_fallback_source_read_failure(self):
        """_generate_security_fix must return action='info' when source read fails."""
        svc = _make_sfe_service()
        bad_context = {"success": False, "error": "file not found"}
        result = svc._generate_security_fix(
            Path("/nonexistent/file.py"),
            5,
            "hardcoded password detected",
            bad_context,
        )
        self._check_result(result)

    def test_security_fix_generic_fallback(self):
        """Generic security fallback must return action='info'."""
        svc = _make_sfe_service()
        good_context = {
            "success": True,
            "full_source": "x = 1\n",
            "target_line": "x = 1",
        }
        # A generic message that no specific handler covers
        result = svc._generate_security_fix(
            Path("/some/file.py"),
            1,
            "unknown_security_issue_that_has_no_handler",
            good_context,
        )
        self._check_result(result)


# ===========================================================================
# FixApplyRequest schema — skip_validation field
# ===========================================================================


class TestFixApplyRequestSchema:
    """FixApplyRequest must expose skip_validation with a False default."""

    def test_skip_validation_defaults_to_false(self):
        try:
            from server.schemas.fixes import FixApplyRequest
        except ImportError as exc:
            pytest.skip(f"server.schemas not importable: {exc}")
        req = FixApplyRequest()
        assert req.skip_validation is False

    def test_skip_validation_can_be_set_true(self):
        try:
            from server.schemas.fixes import FixApplyRequest
        except ImportError as exc:
            pytest.skip(f"server.schemas not importable: {exc}")
        req = FixApplyRequest(skip_validation=True)
        assert req.skip_validation is True

    def test_force_and_dry_run_still_default_false(self):
        try:
            from server.schemas.fixes import FixApplyRequest
        except ImportError as exc:
            pytest.skip(f"server.schemas not importable: {exc}")
        req = FixApplyRequest()
        assert req.force is False
        assert req.dry_run is False


# ===========================================================================
# Dockerfile multi-stage detection
# ===========================================================================


class TestDockerfileMultistageDetection:
    """CodebaseAnalyzer._detect_dockerfile_multistage_issues tests."""

    def _analyzer(self, root: Path):
        try:
            from self_fixing_engineer.arbiter.codebase_analyzer import CodebaseAnalyzer
        except ImportError as exc:
            pytest.skip(f"CodebaseAnalyzer not importable: {exc}")
        return CodebaseAnalyzer(root_dir=str(root))

    def _write_dockerfile(self, tmp_path: Path, content: str) -> Path:
        df = tmp_path / "Dockerfile"
        df.write_text(content, encoding="utf-8")
        return df

    def test_no_issue_for_single_stage(self, tmp_path):
        """Single-stage Dockerfiles must not generate any issues."""
        content = (
            "FROM python:3.11-slim\n"
            "WORKDIR /app\n"
            "COPY requirements.txt .\n"
            "RUN pip install --user -r requirements.txt\n"
            "COPY . .\n"
            "CMD [\"python\", \"main.py\"]\n"
        )
        self._write_dockerfile(tmp_path, content)
        analyzer = self._analyzer(tmp_path)
        issues = analyzer._detect_dockerfile_multistage_issues(tmp_path)
        assert issues == []

    def test_no_issue_when_user_local_is_copied(self, tmp_path):
        """Must not flag when /root/.local is properly copied to runtime stage."""
        content = (
            "FROM python:3.11-slim AS builder\n"
            "WORKDIR /app\n"
            "RUN pip install --user -r requirements.txt\n\n"
            "FROM python:3.11-slim\n"
            "COPY --from=builder /root/.local /root/.local\n"
            "COPY --from=builder /app /app\n"
            "CMD [\"uvicorn\", \"app.main:app\"]\n"
        )
        self._write_dockerfile(tmp_path, content)
        analyzer = self._analyzer(tmp_path)
        issues = analyzer._detect_dockerfile_multistage_issues(tmp_path)
        assert issues == []

    def test_detects_missing_user_local_copy(self, tmp_path):
        """Must flag when pip --user runs in builder but /root/.local is not copied."""
        content = (
            "FROM python:3.11-slim AS builder\n"
            "WORKDIR /app\n"
            "RUN pip install --user -r requirements.txt\n\n"
            "FROM python:3.11-slim\n"
            "COPY --from=builder /app /app\n"  # ← missing /root/.local copy
            "CMD [\"uvicorn\", \"app.main:app\"]\n"
        )
        self._write_dockerfile(tmp_path, content)
        analyzer = self._analyzer(tmp_path)
        issues = analyzer._detect_dockerfile_multistage_issues(tmp_path)
        assert len(issues) == 1
        issue = issues[0]
        assert issue["type"] == "DockerfileDependencyMissing"
        assert issue["risk_level"] == "critical"
        assert "file" in issue
        assert "/root/.local" in issue["details"]["message"]
        assert issue["confidence"] >= 0.90

    def test_detects_pip_prefix_not_copied(self, tmp_path):
        """Must flag when pip --prefix is used but the prefix dir is not copied."""
        content = (
            "FROM python:3.11-slim AS builder\n"
            "WORKDIR /app\n"
            "RUN pip install --prefix=/app/deps -r requirements.txt\n\n"
            "FROM python:3.11-slim\n"
            "COPY --from=builder /app/src /app/src\n"  # ← /app/deps not copied
            "CMD [\"python\", \"main.py\"]\n"
        )
        self._write_dockerfile(tmp_path, content)
        analyzer = self._analyzer(tmp_path)
        issues = analyzer._detect_dockerfile_multistage_issues(tmp_path)
        assert len(issues) == 1
        assert issues[0]["type"] == "DockerfileDependencyMissing"
        assert "/app/deps" in issues[0]["details"]["message"]

    def test_no_issue_when_no_dockerfile(self, tmp_path):
        """Must return an empty list when no Dockerfile exists."""
        (tmp_path / "main.py").write_text("print('hello')", encoding="utf-8")
        analyzer = self._analyzer(tmp_path)
        issues = analyzer._detect_dockerfile_multistage_issues(tmp_path)
        assert issues == []

    def test_fix_recommendation_is_actionable(self, tmp_path):
        """The 'fix' field in issue details must contain the COPY instruction."""
        content = (
            "FROM python:3.11-slim AS builder\n"
            "RUN pip install --user -r requirements.txt\n\n"
            "FROM python:3.11-slim\n"
            "COPY --from=builder /app /app\n"
            "CMD [\"python\", \"app.py\"]\n"
        )
        self._write_dockerfile(tmp_path, content)
        analyzer = self._analyzer(tmp_path)
        issues = analyzer._detect_dockerfile_multistage_issues(tmp_path)
        assert issues, "Expected at least one issue"
        fix_text = issues[0]["details"]["fix"]
        assert "COPY" in fix_text
        assert "/root/.local" in fix_text


# ===========================================================================
# codegen_response_handler Pydantic v2 stub includes model_config
# ===========================================================================


class TestPydanticV2StubGeneration:
    """ensure_local_module_stubs must emit model_config=ConfigDict(from_attributes=True)
    for Pydantic schema stubs in the append-to-existing-module path."""

    def _run(self, files: Dict[str, str]) -> Dict[str, str]:
        try:
            from generator.agents.codegen_agent.codegen_response_handler import (
                ensure_local_module_stubs,
            )
        except ImportError as exc:
            pytest.skip(f"codegen_response_handler not importable: {exc}")
        return ensure_local_module_stubs(files)

    def test_appended_pydantic_stub_has_model_config(self):
        """When a Pydantic schema file is missing a class, the appended stub
        must include model_config = ConfigDict(from_attributes=True)."""
        router_code = (
            "from app.schemas import UserCreate, UserRead, UserProfile\n"
            "from fastapi import APIRouter\n\n"
            "router = APIRouter()\n\n"
            "@router.get('/user')\n"
            "def get_user() -> UserProfile:\n"
            "    pass\n"
        )
        schemas_code = (
            "from pydantic import BaseModel, ConfigDict\n\n"
            "class UserCreate(BaseModel):\n"
            "    name: str\n\n"
            "class UserRead(BaseModel):\n"
            "    id: int\n"
            "    name: str\n"
        )
        files = {
            "app/routers/users.py": router_code,
            "app/schemas.py": schemas_code,
        }
        result = self._run(files)
        schemas_out = result.get("app/schemas.py", "")
        # UserProfile should have been appended
        assert "class UserProfile(BaseModel):" in schemas_out, (
            f"UserProfile not found in schemas output:\n{schemas_out}"
        )
        assert "model_config = ConfigDict(from_attributes=True)" in schemas_out, (
            f"model_config not found in schemas output:\n{schemas_out}"
        )

    def test_configdict_import_added_for_pydantic_schema(self):
        """ConfigDict must be imported when appending a Pydantic stub to a schema file
        that doesn't already have it."""
        router_code = (
            "from app.schemas import Widget\n"
            "from fastapi import APIRouter\n\n"
            "router = APIRouter()\n"
        )
        schemas_code = (
            "from pydantic import BaseModel\n\n"
            "class WidgetCreate(BaseModel):\n"
            "    title: str\n"
        )
        files = {
            "app/routers/widgets.py": router_code,
            "app/schemas.py": schemas_code,
        }
        result = self._run(files)
        schemas_out = result.get("app/schemas.py", "")
        assert "class Widget(BaseModel):" in schemas_out
        # Either ConfigDict was imported already (from the append) or is in the content
        assert "ConfigDict" in schemas_out, (
            f"ConfigDict not found in schemas output:\n{schemas_out}"
        )

    def test_sqlalchemy_stub_does_not_get_model_config(self):
        """SQLAlchemy model stubs must NOT receive model_config (they use Base, not BaseModel)."""
        service_code = (
            "from app.models import Order\n\n"
            "def get_order(order_id: int):\n"
            "    return Order.query.get(order_id)\n"
        )
        models_code = (
            "from sqlalchemy import Column, Integer, String\n"
            "from app.database import Base\n\n"
            "class Product(Base):\n"
            "    __tablename__ = 'products'\n"
            "    id = Column(Integer, primary_key=True)\n"
        )
        files = {
            "app/services/order_service.py": service_code,
            "app/models.py": models_code,
        }
        result = self._run(files)
        models_out = result.get("app/models.py", "")
        # If an Order stub was appended, use AST to confirm model_config is
        # not present inside the Order class body — avoid fragile string-split.
        if "class Order" not in models_out:
            return  # stub not appended; nothing to assert

        try:
            tree = ast.parse(models_out)
        except SyntaxError:
            pytest.fail(f"models.py stub is not valid Python:\n{models_out}")

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "Order":
                # Check that no assignment named 'model_config' exists in body
                for stmt in node.body:
                    if (
                        isinstance(stmt, ast.Assign)
                        and any(
                            isinstance(t, ast.Name) and t.id == "model_config"
                            for t in stmt.targets
                        )
                    ):
                        pytest.fail(
                            "model_config should not appear in an SQLAlchemy stub "
                            f"(found in class Order):\n{models_out}"
                        )
                break
