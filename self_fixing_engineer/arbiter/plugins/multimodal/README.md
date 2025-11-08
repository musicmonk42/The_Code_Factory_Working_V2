# Multimodal Subpackage – Arbiter Plugins (Self-Fixing Engineer)

## Overview

The **multimodal subpackage** provides interfaces and providers for processing multi-modal data (image, audio, video, text) in Arbiter's plugin system. It enables AI-driven analysis with pluggable providers, supporting both testing mocks and real models (e.g., via LLMs). Integrated with observability (Prometheus, OTEL), security (sandboxing, PII masking), and extensibility (registry/hooks), it powers SFE's multi-modal capabilities, such as analyzing code screenshots or audio feedback.

---

## Key Components

- **Interface (`interface.py`):**  
  - Abstracts for `ImageProcessor`, `AudioProcessor`, `VideoProcessor`, and `TextProcessor`
  - Results: Pydantic generics/validation, summaries, provenance
  - Exceptions: Hierarchy for invalid input/config/provider/processing

- **Providers (`providers/`):**  
  - Defaults/mocks (`default_multimodal_providers.py`) with latency simulation, validation, metrics
  - Registry for provider registration/unregistration with type-checking and configs

---

## Setup

### Prerequisites

- **Dependencies:**  
  ```bash
  pip install pydantic prometheus-client cv2 pydub pillow docker
  ```
  (for real processing/sandboxing)

- **Configuration:**  
  Via `multi_modal_config.py` (Pydantic: enabled/providers/security/audits/metrics/cache/breakers)

---

## Configuration

```python
from arbiter.plugins.multi_modal_config import MultiModalConfig
config = MultiModalConfig.from_yaml("config.yaml")  # Validates env/YAML
```

---

## Usage

### Example: Registry & Processing

```python
from arbiter.plugins.multimodal.providers import PluginRegistry
from arbiter.plugins.multimodal.interface import ImageProcessor

# Register custom processor
class CustomImageProcessor(ImageProcessor):
    async def process(self, data: bytes) -> ProcessingResult:
        # Implementation here
        pass

PluginRegistry.register_processor("image", "custom", CustomImageProcessor, config={})

# Get and use a processor
processor = PluginRegistry.get_processor("image", "default", config={})
result = await processor.process(b"data")
print(result.model_dump_json())
```

---

## Extensibility

- **New Providers:** Inherit abstracts, implement `process`, register with configs (Pydantic-validated)
- **Hooks:** Add pre/post hooks via plugin (sync/async)
- **Sandbox:** Enable Docker for isolation in config

---

## Security & Observability

- **Security:** Input/output validation (rules/max size), PII patterns, compliance (NIST/ISO)
- **Metrics:** Counters/histograms for ops, latency, errors (per modality/provider)
- **Tracing:** OTEL spans for processing

---

## Testing

- Add tests to `plugins/tests/`; use mocks for offline testing

---

## Contributing

See root `CONTRIBUTING.md`.

---

## License

**Apache-2.0** (see root `LICENSE.md`)