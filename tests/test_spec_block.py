# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Tests for Spec Block parsing functionality.
"""

import pytest
from generator.intent_parser.spec_block import (
    SpecBlock,
    InterfacesSpec,
    extract_spec_block,
    extract_spec_blocks_all,
)


def test_spec_block_basic():
    """Test basic SpecBlock creation and validation."""
    spec = SpecBlock(
        project_type="fastapi_service",
        package_name="my_app",
        output_dir="generated/my_app"
    )
    
    assert spec.project_type == "fastapi_service"
    assert spec.package_name == "my_app"
    assert spec.output_dir == "generated/my_app"
    assert spec.is_complete()
    assert len(spec.missing_fields()) == 0


def test_spec_block_incomplete():
    """Test incomplete SpecBlock detection."""
    spec = SpecBlock(
        package_name="my_app"
    )
    
    assert not spec.is_complete()
    missing = spec.missing_fields()
    assert "project_type" in missing
    assert "output_dir" in missing


def test_extract_spec_block_explicit():
    """Test extraction of spec block with explicit marker."""
    readme = """
# My Project

Some description here.

```code_factory:
project_type: fastapi_service
package_name: test_app
output_dir: generated/test_app
dependencies:
  - fastapi>=0.100.0
  - pydantic>=2.0.0
```

More content.
"""
    
    spec = extract_spec_block(readme)
    assert spec is not None
    assert spec.project_type == "fastapi_service"
    assert spec.package_name == "test_app"
    assert spec.output_dir == "generated/test_app"
    assert len(spec.dependencies) == 2
    assert "fastapi>=0.100.0" in spec.dependencies


def test_extract_spec_block_with_interfaces():
    """Test extraction of spec block with interfaces."""
    readme = """
# API Service

```code_factory:
project_type: fastapi_service
package_name: api_service
output_dir: generated/api_service
interfaces:
  http:
    - GET /health
    - POST /items
    - GET /items/{id}
  events:
    - item.created
    - item.updated
adapters:
  database: postgresql
  cache: redis
```
"""
    
    spec = extract_spec_block(readme)
    assert spec is not None
    assert spec.has_http_interface()
    assert spec.has_events_interface()
    assert len(spec.interfaces.http) == 3
    assert "GET /health" in spec.interfaces.http
    assert len(spec.interfaces.events) == 2
    assert spec.adapters["database"] == "postgresql"
    assert spec.adapters["cache"] == "redis"


def test_extract_spec_block_not_found():
    """Test when no spec block is present."""
    readme = """
# Regular README

- Feature 1
- Feature 2

## Setup

Install dependencies...
"""
    
    spec = extract_spec_block(readme)
    assert spec is None


def test_extract_multiple_spec_blocks():
    """Test extraction of multiple spec blocks."""
    readme = """
# Multi-Service Project

First service:

```code_factory:
project_type: fastapi_service
package_name: service_a
output_dir: generated/service_a
```

Second service:

```code_factory:
project_type: cli_tool
package_name: service_b
output_dir: generated/service_b
```
"""
    
    specs = extract_spec_blocks_all(readme)
    assert len(specs) == 2
    assert specs[0].package_name == "service_a"
    assert specs[1].package_name == "service_b"
    assert specs[0].project_type == "fastapi_service"
    assert specs[1].project_type == "cli_tool"


def test_spec_block_validation_output_dir():
    """Test output_dir normalization strips trailing slashes from relative paths."""
    spec = SpecBlock(
        project_type="library",
        package_name="mylib",
        output_dir="generated/mylib/"
    )

    # Should strip trailing slash
    assert not spec.output_dir.endswith("/")


def test_spec_block_validation_output_dir_rejects_absolute():
    """Test that absolute output_dir paths are rejected for security."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError) as exc_info:
        SpecBlock(
            project_type="library",
            package_name="mylib",
            output_dir="/generated/mylib/",
        )
    errors = exc_info.value.errors()
    assert any(e["loc"] == ("output_dir",) for e in errors), (
        f"Expected 'output_dir' in error locations; got: {errors}"
    )


def test_spec_block_to_dict():
    """Test spec block serialization."""
    spec = SpecBlock(
        project_type="lambda_function",
        package_name="my_lambda",
        output_dir="generated/lambda",
        dependencies=["boto3>=1.20.0"],
        adapters={"storage": "s3"}
    )
    
    data = spec.to_dict()
    assert data["project_type"] == "lambda_function"
    assert data["package_name"] == "my_lambda"
    assert "boto3>=1.20.0" in data["dependencies"]
    assert data["adapters"]["storage"] == "s3"


def test_spec_block_project_type_normalization():
    """Test project_type is normalized to lowercase."""
    spec = SpecBlock(
        project_type="FastAPI_Service",
        package_name="test",
        output_dir="out"
    )
    
    assert spec.project_type == "fastapi_service"


@pytest.mark.parametrize("project_type,expected_http", [
    ("fastapi_service", False),
    ("flask_service", False),
    ("cli_tool", False),
])
def test_spec_block_interface_detection(project_type, expected_http):
    """Test interface detection for different project types."""
    spec = SpecBlock(
        project_type=project_type,
        package_name="test",
        output_dir="out"
    )
    
    # Without explicit interfaces, should return False
    assert spec.has_http_interface() == expected_http


def test_extract_spec_block_yaml_comment_marker():
    """Test extraction with YAML comment marker."""
    readme = """
# My Project

```yaml
# code_factory
project_type: cli_tool
package_name: my_cli
output_dir: generated/cli
```
"""
    
    spec = extract_spec_block(readme)
    assert spec is not None
    assert spec.project_type == "cli_tool"
    assert spec.package_name == "my_cli"


def test_spec_block_with_acceptance_checks():
    """Test spec block with acceptance checks."""
    readme = """
```code_factory:
project_type: fastapi_service
package_name: verified_app
output_dir: generated/app
acceptance_checks:
  - All endpoints return proper status codes
  - Database migrations applied
  - Tests pass with >80% coverage
```
"""
    
    spec = extract_spec_block(readme)
    assert spec is not None
    assert len(spec.acceptance_checks) == 3
    assert "Database migrations applied" in spec.acceptance_checks


def test_extract_spec_block_from_fastapi_readme():
    """Test that a FastAPI-structured README without explicit code_factory block
    produces a valid SpecBlock with project_type='fastapi_service' and HTTP endpoints."""
    readme = """
# E-Commerce API

A production-ready e-commerce service built with FastAPI.

## Technology Stack
- Framework: FastAPI
- Database: PostgreSQL
- Cache: Redis

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | /products | List all products |
| POST | /products | Create a product |
| GET | /products/{id} | Get a product |
| PUT | /products/{id} | Update a product |
| DELETE | /products/{id} | Delete a product |

GET /products
POST /products
GET /products/{id}
PUT /products/{id}
DELETE /products/{id}

## Data Models

### Product
- id: int
- name: str
- price: float
- stock: int
"""

    spec = extract_spec_block(readme)
    assert spec is not None
    assert spec.project_type == "fastapi_service"
    assert spec.has_http_interface()
    assert len(spec.interfaces.http) >= 5


def test_extract_spec_block_not_found_still_returns_none():
    """Test that a truly unstructured README returns None (no tech keywords, no endpoints)."""
    readme = """
# Regular README

- Feature 1
- Feature 2

## Setup

Install dependencies...
"""

    spec = extract_spec_block(readme)
    assert spec is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
