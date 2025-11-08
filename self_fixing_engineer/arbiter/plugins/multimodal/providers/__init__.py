# __init__.py for arbiter.plugins.multimodal.providers
#
# Exports the PluginRegistry and the default provider classes for multimodal processing.
# This makes it easy for other modules to import and register/find processors in a standard way.

from .default_multimodal_providers import (
    PluginRegistry,
    DefaultImageProcessor,
    DefaultAudioProcessor,
    DefaultVideoProcessor,
    DefaultTextProcessor
)

__all__ = [
    "PluginRegistry",
    "DefaultImageProcessor",
    "DefaultAudioProcessor",
    "DefaultVideoProcessor",
    "DefaultTextProcessor"
]