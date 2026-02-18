# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Spec Block Parser - Extracts structured specifications from fenced YAML blocks.

This module implements parsing for the ```code_factory: ... ``` YAML blocks embedded
in README files, providing authoritative structured specifications that override
unstructured text extraction.

Format Example:
    ```code_factory:
    project_type: fastapi_service
    package_name: my_app
    module_name: my_app
    output_dir: generated/my_app
    interfaces:
      http:
        - GET /health
        - POST /items
      events:
        - item.created
      queues:
        - task_queue
    dependencies:
      - fastapi>=0.100.0
      - pydantic>=2.0.0
    nonfunctional:
      - rate_limiting: 100/minute
      - authentication: jwt
    adapters:
      database: postgresql
      cache: redis
    acceptance_checks:
      - All endpoints return 200 OK
      - Database migrations applied
    ```

Industry Standards:
- YAML 1.2 specification
- Semantic versioning for schema_version
- Fail-fast validation with clear error messages
"""

import logging
import re
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)


class InterfacesSpec(BaseModel):
    """Specification for service interfaces."""
    
    http: List[str] = Field(default_factory=list, description="HTTP endpoints")
    events: List[str] = Field(default_factory=list, description="Event types")
    queues: List[str] = Field(default_factory=list, description="Queue names")
    grpc: List[str] = Field(default_factory=list, description="gRPC services")
    websocket: List[str] = Field(default_factory=list, description="WebSocket endpoints")


class SpecBlock(BaseModel):
    """
    Structured specification block for code generation.
    
    This model defines the authoritative schema for embedded YAML specs in READMEs.
    All fields that affect generation behavior should be explicitly defined here.
    """
    
    # Core project identification
    project_type: Optional[str] = Field(
        None,
        description="Type of project: fastapi_service, cli_tool, library, batch_job, etc."
    )
    package_name: Optional[str] = Field(
        None,
        description="Python package/module name (e.g., 'my_app')"
    )
    module_name: Optional[str] = Field(
        None,
        description="Main module name (often same as package_name)"
    )
    
    # Output configuration
    output_dir: Optional[str] = Field(
        None,
        description="Output directory for generated code (e.g., 'generated/my_app')"
    )
    
    # Interfaces and endpoints
    interfaces: Optional[InterfacesSpec] = Field(
        None,
        description="Service interfaces: HTTP endpoints, events, queues, etc."
    )
    
    # Dependencies
    dependencies: List[str] = Field(
        default_factory=list,
        description="Python package dependencies (pip-installable)"
    )
    
    # Non-functional requirements
    nonfunctional: List[str] = Field(
        default_factory=list,
        description="Non-functional requirements: rate limiting, auth, logging, etc."
    )
    
    # Adapters and backends
    adapters: Dict[str, str] = Field(
        default_factory=dict,
        description="Backend adapters: database, cache, message_broker, etc."
    )
    
    # Validation and acceptance
    acceptance_checks: List[str] = Field(
        default_factory=list,
        description="Post-generation validation checks"
    )
    
    # Metadata
    schema_version: str = Field(
        "1.0",
        description="Spec block schema version for compatibility"
    )
    
    @field_validator("project_type")
    @classmethod
    def validate_project_type(cls, v: Optional[str]) -> Optional[str]:
        """Validate and normalize project_type."""
        if v is None:
            return v
        
        # Known project types
        known_types = {
            "fastapi_service", "flask_service", "django_service",
            "cli_tool", "library", "batch_job", "lambda_function",
            "microservice", "api_gateway", "data_pipeline"
        }
        
        v_lower = v.lower()
        if v_lower not in known_types:
            logger.warning(
                f"Unknown project_type '{v}'. Known types: {', '.join(sorted(known_types))}"
            )
        
        return v_lower
    
    @field_validator("output_dir")
    @classmethod
    def validate_output_dir(cls, v: Optional[str]) -> Optional[str]:
        """Validate output directory format."""
        if v is None:
            return v
        
        # Remove leading/trailing slashes for consistency
        v = v.strip("/")
        
        # Warn about absolute paths
        if v.startswith("/") or ":" in v:
            logger.warning(
                f"output_dir '{v}' appears to be an absolute path. "
                f"Relative paths are recommended for portability."
            )
        
        return v
    
    def is_complete(self) -> bool:
        """Check if spec has all required fields for generation."""
        required = [
            self.project_type,
            self.package_name or self.module_name,
            self.output_dir,
        ]
        return all(required)
    
    def missing_fields(self) -> List[str]:
        """Return list of missing required fields."""
        missing = []
        if not self.project_type:
            missing.append("project_type")
        if not (self.package_name or self.module_name):
            missing.append("package_name or module_name")
        if not self.output_dir:
            missing.append("output_dir")
        return missing
    
    def has_http_interface(self) -> bool:
        """Check if spec defines HTTP endpoints."""
        return bool(self.interfaces and self.interfaces.http)
    
    def has_events_interface(self) -> bool:
        """Check if spec defines event handlers."""
        return bool(self.interfaces and self.interfaces.events)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return self.model_dump(exclude_none=True, exclude_defaults=True)


def extract_spec_block(content: str) -> Optional[SpecBlock]:
    """
    Extract and parse ```code_factory: ... ``` YAML block from README content.
    
    This function searches for fenced code blocks with the 'code_factory:' marker
    and parses the YAML content into a SpecBlock model.
    
    Args:
        content: README or documentation content
        
    Returns:
        Parsed SpecBlock if found and valid, None otherwise
        
    Example:
        ```python
        readme = Path("README.md").read_text()
        spec = extract_spec_block(readme)
        if spec and spec.is_complete():
            print(f"Generating {spec.project_type} in {spec.output_dir}")
        ```
    """
    # Pattern to match fenced code blocks with code_factory marker
    # Supports both ```code_factory: and ```yaml with code_factory content
    patterns = [
        r'```code_factory:\s*\n(.*?)\n```',  # Explicit marker
        r'```yaml\s*\n# code_factory\s*\n(.*?)\n```',  # Comment marker in YAML block
    ]
    
    for pattern in patterns:
        match = re.search(pattern, content, re.DOTALL | re.MULTILINE)
        if match:
            yaml_content = match.group(1)
            logger.debug(f"Found spec block with pattern: {pattern[:30]}...")
            
            try:
                # Parse YAML content
                data = yaml.safe_load(yaml_content)
                
                if not isinstance(data, dict):
                    logger.warning(
                        f"Spec block YAML did not parse to dict: {type(data)}"
                    )
                    continue
                
                # Create SpecBlock from parsed data
                spec = SpecBlock(**data)
                logger.info(
                    f"Parsed spec block: project_type={spec.project_type}, "
                    f"package={spec.package_name}, output={spec.output_dir}"
                )
                return spec
                
            except yaml.YAMLError as e:
                logger.error(f"Failed to parse spec block YAML: {e}")
                continue
            except Exception as e:
                logger.error(f"Failed to create SpecBlock from data: {e}")
                continue
    
    logger.debug("No spec block found in content")
    return None


def extract_spec_blocks_all(content: str) -> List[SpecBlock]:
    """
    Extract all spec blocks from content (supports multiple blocks).
    
    Args:
        content: README or documentation content
        
    Returns:
        List of all parsed SpecBlocks
    """
    specs = []
    
    patterns = [
        r'```code_factory:\s*\n(.*?)\n```',
        r'```yaml\s*\n# code_factory\s*\n(.*?)\n```',
    ]
    
    for pattern in patterns:
        for match in re.finditer(pattern, content, re.DOTALL | re.MULTILINE):
            yaml_content = match.group(1)
            
            try:
                data = yaml.safe_load(yaml_content)
                if isinstance(data, dict):
                    spec = SpecBlock(**data)
                    specs.append(spec)
                    logger.debug(f"Found spec block #{len(specs)}")
            except Exception as e:
                logger.warning(f"Skipping invalid spec block: {e}")
                continue
    
    return specs


__all__ = [
    "SpecBlock",
    "InterfacesSpec",
    "extract_spec_block",
    "extract_spec_blocks_all",
]
