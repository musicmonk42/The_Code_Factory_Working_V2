# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Template Configuration and Management for Code Generation

Industry Standard: Centralized configuration for template system with versioning,
caching, and validation capabilities.

This module provides enterprise-grade template management:
- Template discovery and validation
- Template versioning and compatibility checking
- Template caching for performance
- Template metrics and monitoring
- Hot-reload capability for development
"""

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set

from jinja2 import Environment, FileSystemLoader, Template, TemplateNotFound

logger = logging.getLogger(__name__)


@dataclass
class TemplateMetadata:
    """Metadata for a code generation template."""
    
    name: str
    path: Path
    language: str
    framework: Optional[str] = None
    version: str = "1.0.0"
    requires_macros: bool = True
    last_modified: Optional[datetime] = None
    checksum: Optional[str] = None
    
    def calculate_checksum(self) -> str:
        """Calculate SHA-256 checksum of template content."""
        if self.path.exists():
            content = self.path.read_bytes()
            self.checksum = hashlib.sha256(content).hexdigest()
            return self.checksum
        return ""


@dataclass
class TemplateConfig:
    """Configuration for template system."""
    
    # Template directories
    template_dir: Path = field(default_factory=lambda: Path(__file__).parent / "templates")
    
    # Caching
    enable_caching: bool = True
    cache_ttl_seconds: int = 3600
    
    # Hot-reloading (disable in production)
    enable_hot_reload: bool = False
    
    # Validation
    validate_on_load: bool = True
    require_macros: bool = True
    
    # Supported languages and frameworks
    supported_languages: Set[str] = field(default_factory=lambda: {
        "python", "javascript", "typescript", "java", "go", "rust", "csharp"
    })
    
    supported_frameworks: Dict[str, Set[str]] = field(default_factory=lambda: {
        "python": {"fastapi", "flask", "django"},
        "javascript": {"express", "nestjs", "nextjs"},
        "typescript": {"express", "nestjs", "nextjs"},
        "java": {"spring", "quarkus"},
    })
    
    def validate(self) -> bool:
        """Validate configuration."""
        if not self.template_dir.exists():
            logger.error(f"Template directory does not exist: {self.template_dir}")
            return False
        
        if not self.template_dir.is_dir():
            logger.error(f"Template path is not a directory: {self.template_dir}")
            return False
        
        # Check for required base template
        base_template = self.template_dir / "base.jinja2"
        if not base_template.exists():
            logger.error(f"Required base template not found: {base_template}")
            return False
        
        # Check for macros if required
        if self.require_macros:
            macros_file = self.template_dir / "_macros.jinja2"
            if not macros_file.exists():
                logger.warning(f"Macros file not found: {macros_file}")
        
        return True


class TemplateManager:
    """
    Enterprise-grade template management system.
    
    Responsibilities:
    - Template discovery and loading
    - Template caching and invalidation
    - Template validation
    - Template metrics
    - Hot-reload support
    """
    
    def __init__(self, config: Optional[TemplateConfig] = None):
        """
        Initialize template manager.
        
        Args:
            config: Template configuration. If None, uses default config.
        """
        self.config = config or TemplateConfig()
        
        if not self.config.validate():
            raise ValueError("Invalid template configuration")
        
        self._templates: Dict[str, TemplateMetadata] = {}
        self._template_cache: Dict[str, Template] = {}
        self._cache_timestamps: Dict[str, datetime] = {}
        
        # Initialize Jinja2 environment
        self._env = Environment(
            loader=FileSystemLoader(str(self.config.template_dir)),
            autoescape=False,  # Code generation shouldn't auto-escape
            trim_blocks=True,
            lstrip_blocks=True,
        )
        
        # Discover templates
        self._discover_templates()
        
        logger.info(
            f"TemplateManager initialized with {len(self._templates)} templates "
            f"from {self.config.template_dir}"
        )
    
    def _discover_templates(self):
        """Discover and catalog all available templates."""
        for template_file in self.config.template_dir.glob("*.jinja2"):
            # Skip macro files and private templates
            if template_file.name.startswith("_"):
                continue
            
            # Parse template name
            name = template_file.stem  # Remove .jinja2 extension
            
            # Determine language/framework from name
            language = None
            framework = None
            
            if name == "base":
                language = "base"
            elif "." in name:
                # Format: language.framework (e.g., python.fastapi)
                parts = name.split(".", 1)
                language = parts[0]
                framework = parts[1] if len(parts) > 1 else None
            else:
                # Format: language (e.g., python)
                language = name
            
            # Create metadata
            metadata = TemplateMetadata(
                name=name,
                path=template_file,
                language=language,
                framework=framework,
                last_modified=datetime.fromtimestamp(template_file.stat().st_mtime),
            )
            metadata.calculate_checksum()
            
            self._templates[name] = metadata
            
            logger.debug(f"Discovered template: {name} ({language}/{framework or 'base'})")
    
    def get_template(self, language: str, framework: Optional[str] = None) -> Template:
        """
        Get a template for the specified language and framework.
        
        Args:
            language: Programming language (e.g., "python", "javascript")
            framework: Optional framework (e.g., "fastapi", "flask")
            
        Returns:
            Jinja2 Template object
            
        Raises:
            TemplateNotFound: If no suitable template is found
        """
        # Build template name
        if framework:
            template_name = f"{language}.{framework}.jinja2"
        else:
            template_name = f"{language}.jinja2"
        
        # Check cache if enabled
        if self.config.enable_caching and template_name in self._template_cache:
            cached_time = self._cache_timestamps.get(template_name)
            if cached_time:
                age_seconds = (datetime.now() - cached_time).total_seconds()
                if age_seconds < self.config.cache_ttl_seconds:
                    logger.debug(f"Returning cached template: {template_name}")
                    return self._template_cache[template_name]
        
        # Try to load specific template
        try:
            template = self._env.get_template(template_name)
            
            # Cache if enabled
            if self.config.enable_caching:
                self._template_cache[template_name] = template
                self._cache_timestamps[template_name] = datetime.now()
            
            logger.info(f"Loaded template: {template_name}")
            return template
            
        except TemplateNotFound:
            # Fall back to language-only template
            if framework:
                logger.warning(
                    f"Template {template_name} not found, "
                    f"falling back to {language}.jinja2"
                )
                return self.get_template(language, framework=None)
            
            # Fall back to base template
            logger.warning(
                f"Template {template_name} not found, "
                f"falling back to base.jinja2"
            )
            template = self._env.get_template("base.jinja2")
            
            if self.config.enable_caching:
                self._template_cache[template_name] = template
                self._cache_timestamps[template_name] = datetime.now()
            
            return template
    
    def list_templates(self) -> List[TemplateMetadata]:
        """Get list of all available templates."""
        return list(self._templates.values())
    
    def clear_cache(self):
        """Clear template cache."""
        self._template_cache.clear()
        self._cache_timestamps.clear()
        logger.info("Template cache cleared")
    
    def reload_templates(self):
        """Reload all templates (useful for hot-reload)."""
        self._templates.clear()
        self.clear_cache()
        self._discover_templates()
        logger.info("Templates reloaded")
    
    def validate_template(self, template_name: str) -> bool:
        """
        Validate that a template can be loaded and rendered.
        
        Args:
            template_name: Name of template to validate
            
        Returns:
            True if template is valid, False otherwise
        """
        try:
            template = self._env.get_template(f"{template_name}.jinja2")
            
            # Try to render with minimal context
            template.render(
                requirements={},
                target_language="python",
                state_summary="test",
                best_practices=[],
            )
            
            logger.debug(f"Template validation passed: {template_name}")
            return True
            
        except Exception as e:
            logger.error(f"Template validation failed for {template_name}: {e}")
            return False
    
    def get_template_info(self, template_name: str) -> Optional[TemplateMetadata]:
        """Get metadata for a specific template."""
        return self._templates.get(template_name)


# Global template manager instance (initialized on first import)
_global_template_manager: Optional[TemplateManager] = None


def get_template_manager(config: Optional[TemplateConfig] = None) -> TemplateManager:
    """
    Get the global template manager instance.
    
    Args:
        config: Optional configuration. Only used on first call.
        
    Returns:
        Global TemplateManager instance
    """
    global _global_template_manager
    
    if _global_template_manager is None:
        _global_template_manager = TemplateManager(config)
    
    return _global_template_manager


def reload_templates():
    """Reload all templates (useful for development)."""
    manager = get_template_manager()
    manager.reload_templates()
