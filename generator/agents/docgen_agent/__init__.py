# Generator/agents/docgen_agent/__init__.py

# Exports from the main agent orchestrator
from .docgen_agent import (
    DocGenAgent,
    scrub_text,  # scrub_text is defined in multiple files, exporting agent's one
    CompliancePlugin,
    LicenseCompliance,
    CopyrightCompliance,
    SphinxDocGenerator,
    BatchProcessor,
    doc_critique_summary,
    generate,  # The omnicore plugin entry point
    PluginRegistry,  # Compliance plugin registry from docgen_agent
    SPHINX_AVAILABLE,  # Sphinx availability flag
)

# Exports from the prompt factory
from .docgen_prompt import (
    DocGenPromptAgent,
)

# Exports from the merged response handler and validator
from .docgen_response_validator import (
    ResponseValidator,
    DocGenPlugin,
    PluginRegistry as ValidatorPluginRegistry,  # Rename to avoid conflict
    ValidationRequest,
    ValidationReportResponse,
    app as validator_api_app,  # Export the FastAPI app
)

# Re-export dependencies that tests need to patch
# These are imported in docgen_agent.py and need to be accessible for mocking
try:
    import tiktoken
    from runner.llm_client import call_llm_api, call_ensemble_api
    from runner.summarize_utils import call_summarizer, ensemble_summarizers
except ImportError:
    # If dependencies aren't available (e.g., in test environment with mocks),
    # that's okay - the tests will mock them anyway
    pass
