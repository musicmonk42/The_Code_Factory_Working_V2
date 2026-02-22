# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# __init__.py for arbiter.plugins.multimodal.providers
#
# Exports the PluginRegistry and the default provider classes for multimodal processing.
# This makes it easy for other modules to import and register/find processors in a standard way.

from .default_multimodal_providers import (
    DefaultAudioProcessor,
    DefaultImageProcessor,
    DefaultTextProcessor,
    DefaultVideoProcessor,
    PluginRegistry,
)

# Import real multimodal providers from the interface module
try:
    from self_fixing_engineer.arbiter.plugins.multimodal.interface import (
        OpenAIMultiModalProvider,
        XAIMultiModalProvider,
        get_multimodal_provider,
    )

    REAL_PROVIDERS_AVAILABLE = True
except ImportError:
    OpenAIMultiModalProvider = None  # type: ignore[assignment,misc]
    XAIMultiModalProvider = None  # type: ignore[assignment,misc]
    get_multimodal_provider = None  # type: ignore[assignment,misc]
    REAL_PROVIDERS_AVAILABLE = False

__all__ = [
    "PluginRegistry",
    "DefaultImageProcessor",
    "DefaultAudioProcessor",
    "DefaultVideoProcessor",
    "DefaultTextProcessor",
    "OpenAIMultiModalProvider",
    "XAIMultiModalProvider",
    "get_multimodal_provider",
    "REAL_PROVIDERS_AVAILABLE",
]
