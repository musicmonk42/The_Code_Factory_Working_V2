# Generator/agents/docgen_agent/__init__.py

# Exports from the main agent orchestrator
from .docgen_agent import SPHINX_AVAILABLE  # Sphinx availability flag
from .docgen_agent import PluginRegistry  # Compliance plugin registry from docgen_agent
from .docgen_agent import generate  # The omnicore plugin entry point
from .docgen_agent import (
    scrub_text,  # scrub_text is defined in multiple files, exporting agent's one
)
from .docgen_agent import (
    BatchProcessor,
    CompliancePlugin,
    CopyrightCompliance,
    DocGenAgent,
    LicenseCompliance,
    SphinxDocGenerator,
    doc_critique_summary,
)

# Exports from the prompt factory
from .docgen_prompt import DocGenPromptAgent

# Exports from the merged response handler and validator
from .docgen_response_validator import DocGenPlugin
from .docgen_response_validator import (
    PluginRegistry as ValidatorPluginRegistry,  # Rename to avoid conflict
)
from .docgen_response_validator import (
    ResponseValidator,
    ValidationReportResponse,
    ValidationRequest,
)
from .docgen_response_validator import (
    app as validator_api_app,
)  # Export the FastAPI app

# Re-export dependencies that tests need to patch
# These are imported in docgen_agent.py and need to be accessible for mocking
try:
    import tiktoken
    from runner.llm_client import call_ensemble_api, call_llm_api, CircuitBreaker
    from runner.summarize_utils import call_summarizer, ensemble_summarizers
except ImportError:
    # If dependencies aren't available (e.g., in test environment with mocks),
    # that's okay - the tests will mock them anyway
    pass
