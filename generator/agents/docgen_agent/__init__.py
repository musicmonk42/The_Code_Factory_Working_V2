# Generator/agents/docgen_agent/__init__.py

# Exports from the main agent orchestrator
from .docgen_agent import (
    DocGenAgent,
    scrub_text, # scrub_text is defined in multiple files, exporting agent's one
    CompliancePlugin,
    LicenseCompliance,
    CopyrightCompliance,
    generate, # The omnicore plugin entry point
)

# Exports from the prompt factory
from .docgen_prompt import (
    DocGenPromptAgent,
)

# Exports from the merged response handler and validator
from .docgen_response_validator import (
    ResponseValidator,
    DocGenPlugin,
    PluginRegistry,
    ValidationRequest,
    ValidationReportResponse,
    app as validator_api_app # Export the FastAPI app
)