# agents/codegen_prompt.py
import asyncio
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
import redis.asyncio as aioredis
from dotenv import load_dotenv
from jinja2 import Environment, FileSystemLoader, TemplateNotFound, select_autoescape

# OpenTelemetry
from opentelemetry import trace

# Prometheus
from prometheus_client import REGISTRY, Counter, Histogram  # <-- IMPORTED REGISTRY

try:
    from opentelemetry.exporter.jaeger.thrift import JaegerExporter
except ImportError:
    # Jaeger exporter not installed, use console exporter as fallback
    JaegerExporter = None

# ==============================================================================
# --- Production-Ready Imports & Dependency Handling ---
# This section manages optional dependencies for advanced features. This is a deliberate
# production-ready pattern. It allows the core service to remain functional even if heavy
# or platform-specific libraries (like CUDA-dependent sentence-transformers or faiss)
# are not installed, preventing import errors from crashing the entire application.
# ==============================================================================
try:
    import faiss
    from sentence_transformers import SentenceTransformer

    RAG_LIBRARIES_INSTALLED = True
except ImportError:
    RAG_LIBRARIES_INSTALLED = False
    faiss = None
    SentenceTransformer = None

try:
    from google.cloud import vision

    VISION_LIBRARIES_INSTALLED = True
except ImportError:
    VISION_LIBRARIES_INSTALLED = False
    vision = None

# Internal imports that exist in this package
# from .codegen_llm_call import SecretsManager  <-- DELETED DEAD IMPORT
# New imports for standardized utilities
# NOTE: The originals are removed as per instructions
# from .codegen_llm_call import get_token_count
# from utils.agents_utils import AuditLogger, JsonConsoleAuditLogger
# from utils import security_utils

# --- New Centralized Utility Imports (Hypothetical) ---
# Assuming 'runner' is an importable package structure
try:
    # --- FIX: Changed imports to be relative ---
    from ...runner.llm_client import SecretsManager  # <-- ADDED REAL IMPORT
    from ...runner.llm_client import count_tokens
    from ...runner.runner_logging import log_audit_event
    from ...runner.runner_security_utils import redact_secrets

    # We will need a placeholder or a default AuditLogger/security_utils for the function signature if we cannot remove the dependency fully.
    # For now, we will update the usage. The dummy AuditLogger in build_code_generation_prompt will be replaced.
except ImportError:
    # A placeholder/mock is often needed in production refactoring until the runner is fully available
    # For this exercise, we will assume the imports succeed and replace the usage.
    # We need to define a dummy function/class to prevent a crash if the imports fail, as the signature requires it.
    # Since the user instruction implies a replacement, we will remove the old imports and assume the new ones exist.
    class DummyAuditLogger:
        def log_action(self, *args, **kwargs):
            logging.warning(
                "Using dummy log_audit_event as runner utility is unavailable."
            )

    async def log_audit_event(*args, **kwargs):
        logging.warning("Using dummy log_audit_event as runner utility is unavailable.")

    def redact_secrets(text):
        logging.warning("Using dummy redact_secrets as runner utility is unavailable.")
        return text

    def count_tokens(prompt, model_name):
        logging.warning(
            "Using dummy count_tokens as runner utility is unavailable. Returning a safe estimate."
        )
        return len(prompt) // 4  # Simple char-to-token approximation

    # Need a dummy SecretsManager if the primary import fails, otherwise secrets_manager = SecretsManager() will fail
    class SecretsManager:
        def get(self, key: str) -> Optional[str]:
            logging.warning(f"Using dummy SecretsManager. {key} not found.")
            return os.getenv(key)


# --- End New Centralized Utility Imports ---

secrets_manager = SecretsManager()
load_dotenv()
# Get module logger - follows Python logging best practices.
# Do NOT call basicConfig() at module level to avoid duplicate logs.
logger = logging.getLogger(__name__)

# OpenTelemetry Setup
# Use the default/configured tracer provider instead of manually creating one
# This avoids version compatibility issues and respects OTEL_* environment variables
try:
    tracer = trace.get_tracer(__name__)
except TypeError:
    # Fallback for older OpenTelemetry versions
    tracer = None

# ==============================================================================
# --- Prometheus Metrics Access Helpers ---
# ==============================================================================


def _get_existing_metric(name: str):
    """
    Try to find an existing metric in a way that works with both:
    - real prometheus_client.REGISTRY
    - the lightweight test stub from conftest.py
    """
    # Real prometheus_client often has _names_to_collectors
    try:
        mapping = getattr(REGISTRY, "_names_to_collectors", None)
        if isinstance(mapping, dict) and name in mapping:
            return mapping[name]
    except Exception:
        pass

    # Our test stub defines _collector_to_names instead
    try:
        reverse = getattr(REGISTRY, "_collector_to_names", None)
        if isinstance(reverse, dict):
            for collector, names in reverse.items():
                if name in (names or []):
                    return collector
    except Exception:
        pass

    return None


def get_or_create_histogram(name: str, description: str, labelnames=None):
    labelnames = labelnames or []
    existing = _get_existing_metric(name)
    if existing is not None:
        return existing
    return Histogram(name, description, labelnames)


def get_or_create_counter(name: str, description: str, labelnames=None):
    labelnames = labelnames or []
    existing = _get_existing_metric(name)
    if existing is not None:
        return existing
    return Counter(name, description, labelnames)


# ==============================================================================
# --- Constants and Configuration ---
# ==============================================================================
MAX_PROMPT_TOKENS = 8000
META_LLM_API_URL = "https://api.x.ai/v1/chat/completions"
META_LLM_MODEL = "grok-1.5-sonnet"
META_LLM_API_KEY = os.getenv("GROK_API_KEY")
SEARCH_API_KEY = os.getenv("SEARCH_API_KEY")

# PRODUCTION FIX: Add explicit configuration flags for features.
# This allows operators to disable features for cost/performance reasons, even if libraries are installed.
ENABLE_RAG_FEATURE = os.getenv("ENABLE_RAG_FEATURE", "true").lower() == "true"
ENABLE_VISION_FEATURE = os.getenv("ENABLE_VISION_FEATURE", "true").lower() == "true"

RAG_ENABLED = RAG_LIBRARIES_INSTALLED and ENABLE_RAG_FEATURE
VISION_ENABLED = VISION_LIBRARIES_INSTALLED and ENABLE_VISION_FEATURE


# Now define the metric safely (no AttributeError under stubs)
PROMPT_BUILD_LATENCY = get_or_create_histogram(
    "prompt_build_latency_seconds",
    "Time taken to build code generation prompts",
    ["template"],
)

PROMPT_ERRORS = get_or_create_counter(
    "prompt_errors_total", "Prompt build errors", ["error_type"]
)
# --- END FIX ---

# --- Expanded Best Practices ---
BEST_PRACTICES = {
    "python": [
        "Use modern tooling like Black/Ruff for code style and linting.",
        "Provide type hints for all function signatures and check with mypy.",
        "Write comprehensive docstrings following Google or NumPy style.",
        "Implement specific, non-generic exception handling with try-except blocks.",
        "Use f-strings for all string formatting.",
        "Leverage asyncio for I/O-bound operations.",
    ],
    "javascript": [
        "Use ESLint and Prettier for consistent code style.",
        "Prefer `const` and `let` over `var` to manage scope.",
        "Use Promises and async/await for handling asynchronous operations.",
        "Write clear JSDoc comments for all functions.",
        "Strictly avoid global variables to prevent side effects.",
    ],
    "java": [
        "Use a standard style guide like Google's Java Style Guide and tools like Checkstyle.",
        "Prefer immutable objects and classes where possible.",
        "Use specific checked and unchecked exceptions.",
        "Write thorough Javadoc for all public APIs.",
        "Utilize Streams and Lambdas for cleaner collection processing.",
    ],
    # Framework-specific best practices
    "python-flask": [
        "Use Blueprints to organize routes in larger applications.",
        "Use the application factory pattern (`create_app`) for testability and scalability.",
        "Manage configuration separately for different environments (dev, prod).",
    ],
}

# ==============================================================================
# --- RAG Setup (Redis and Sentence Transformers) ---
# ==============================================================================
# Global variables for RAG components, initialized if the feature is enabled.
encoder = None
embedding_dim = 0
knowledge_base = []
doc_embeddings = None

if RAG_ENABLED:
    try:
        logger.info("RAG feature is enabled. Initializing SentenceTransformer model...")
        encoder = SentenceTransformer("all-MiniLM-L6-v2")
        embedding_dim = encoder.get_sentence_embedding_dimension()
        knowledge_base = [
            f"[{lang}] {p}"
            for lang, practices in BEST_PRACTICES.items()
            for p in practices
        ]
        doc_embeddings = encoder.encode(knowledge_base, convert_to_tensor=False)
        logger.info(
            "SentenceTransformer model loaded and knowledge base embeddings created."
        )
    except Exception as e:
        logger.error(
            f"Failed to initialize RAG components during import: {e}. RAG will be disabled."
        )
        RAG_ENABLED = False
elif not RAG_LIBRARIES_INSTALLED:
    logger.warning(
        "RAG dependencies (faiss, sentence-transformers) not found. Semantic retrieval will be disabled."
    )
else:  # RAG_LIBRARIES_INSTALLED but not ENABLE_RAG_FEATURE
    logger.info("RAG feature is disabled by configuration (ENABLE_RAG_FEATURE=false).")


async def initialize_rag_store(redis_client: aioredis.Redis):
    """
    Initializes the Redis vector store with embeddings. This should be called once on application startup.
    """
    if not RAG_ENABLED or not redis_client:
        logger.warning(
            "Skipping RAG store initialization because feature is disabled or Redis client is unavailable."
        )
        return

    try:
        # Populate the hash keys with embeddings
        for i, emb in enumerate(doc_embeddings):
            await redis_client.hset(
                f"rag:{i}", mapping={"embedding": json.dumps(emb.tolist())}
            )

        # Check if the search index already exists to prevent errors on restart
        try:
            await redis_client.execute_command("FT.INFO", "rag_index")
            logger.info("Redis search index 'rag_index' already exists.")
        except aioredis.ResponseError as e:
            if "unknown command" in str(e).lower():
                logger.error(
                    "Redis server does not support the 'FT.INFO' command. RediSearch module may not be installed or enabled."
                )
                PROMPT_ERRORS.labels("RedisSearchModuleMissing").inc()
                return  # Cannot proceed without RediSearch
            # If the index does not exist, a "Not found" or similar error is expected, so we create it.
            logger.info(
                "Redis search index 'rag_index' not found. Creating new index..."
            )
            await redis_client.execute_command(
                "FT.CREATE",
                "rag_index",
                "ON",
                "HASH",
                "PREFIX",
                1,
                "rag:",
                "SCHEMA",
                "embedding",
                "VECTOR",
                "HNSW",
                6,
                "TYPE",
                "FLOAT32",
                "DIM",
                embedding_dim,
                "DISTANCE_METRIC",
                "COSINE",
            )
            logger.info("Redis vector store and search index initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize Redis RAG store: {e}")
        PROMPT_ERRORS.labels("RedisRAGSetupFailure").inc()


# ==============================================================================
# --- Hot-Reloading Jinja2 Environment ---
# ==============================================================================
class HotReloadingFileSystemLoader(FileSystemLoader):
    """A Jinja2 loader that automatically reloads templates if they change on disk."""

    def __init__(self, searchpath, encoding="utf-8", followlinks=False):
        super().__init__(searchpath, encoding, followlinks)
        self._last_mtime = {}

    def get_template_path(self, template):
        # Resolve template path (compatible with multi-search paths)
        for base in self.searchpath:
            path = os.path.join(base, template)
            if os.path.exists(path):
                return path
        return os.path.join(self.searchpath[0], template)

    def get_source(self, environment, template):
        path = self.get_template_path(template)
        if not os.path.exists(path):
            raise TemplateNotFound(template)

        mtime = os.path.getmtime(path)
        if template not in self._last_mtime or mtime > self._last_mtime.get(
            template, 0
        ):
            logger.info(f"Template '{template}' has changed. Reloading.")
            self.clear_cache()
            self._last_mtime[template] = mtime

        return super().get_source(environment, template)


template_paths = ["templates", "project_templates"]
env = Environment(
    loader=HotReloadingFileSystemLoader(template_paths),
    autoescape=select_autoescape(["html", "xml"]),
)


# ==============================================================================
# --- Core Prompt Engineering Functions ---
# ==============================================================================
async def retrieve_augmented_context(
    requirements: Dict[str, Any],
    target_language: str,
    redis_client: Optional[aioredis.Redis] = None,
) -> str:
    """
    Retrieval-Augmented Generation: Fetches relevant snippets from an external search API and Redis.
    """
    with tracer.start_as_current_span("retrieve_augmented_context"):
        context_snippets = []

        # 1. External Search
        if SEARCH_API_KEY:
            try:
                search_query = f"{target_language} " + " ".join(
                    requirements.get("features", [])[:2]
                )
                headers = {
                    "Authorization": f"Bearer {SEARCH_API_KEY}",
                    "Accept-Encoding": "gzip",
                }
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"https://api.brave.com/search?q={search_query}",
                        headers=headers,
                        timeout=10,
                    ) as resp:
                        resp.raise_for_status()
                        # NB: Adjust to the true Brave API shape in your environment
                        results = (
                            (await resp.json()).get("web", {}).get("results", [])[:3]
                        )
                        if results:
                            context_snippets.append(
                                "Relevant Context (from Brave Search):\n- "
                                + "\n- ".join(
                                    result["snippet"]
                                    for result in results
                                    if "snippet" in result
                                )
                            )
            except Exception as e:
                logger.error(f"Error during external search augmentation: {e}")
                PROMPT_ERRORS.labels("ExternalSearchFailure").inc()

        # 2. Redis Vector Search
        if RAG_ENABLED and redis_client and encoder:
            try:
                query_text = f"{target_language} " + json.dumps(
                    requirements.get("features")
                )
                query_embedding = encoder.encode(query_text).tolist()

                # KNN search query for RediSearch
                search_query = "(@embedding:[VECTOR_RANGE $radius $embedding])=>{$yield_distance_as: score}"
                params = {
                    "embedding": json.dumps(query_embedding),
                    "radius": 0.8,  # Cosine similarity threshold
                }

                results = await redis_client.execute_command(
                    "FT.SEARCH",
                    "rag_index",
                    search_query,
                    "PARAMS",
                    json.dumps(params),
                    "RETURN",
                    2,
                    "id",
                    "score",
                    "LIMIT",
                    0,
                    3,
                    "DIALECT",
                    2,
                )

                retrieved_docs = []
                if isinstance(results, list) and len(results) > 1:
                    for i in range(1, len(results), 2):
                        doc_data = results[i]
                        # Expected shape: ['id', 'rag:123', 'score', '0.9', ...]
                        if isinstance(doc_data, list) and "id" in doc_data:
                            key_index = doc_data.index("id") + 1
                            key = doc_data[key_index]
                            if isinstance(key, str) and re.match(r"rag:\d+", key):
                                doc_id = int(key.split(":")[1])
                                if 0 <= doc_id < len(knowledge_base):
                                    retrieved_docs.append(knowledge_base[doc_id])

                if retrieved_docs:
                    context_snippets.append(
                        "Relevant Context (from Redis Vector Store):\n- "
                        + "\n- ".join(retrieved_docs)
                    )
            except Exception as e:
                logger.error(f"Failed to use Redis RAG: {e}")
                PROMPT_ERRORS.labels("RedisRAGFailure").inc()
        return "\n\n".join(context_snippets)


async def process_multi_modal_input(
    input_data: Dict[str, Any],
) -> Tuple[Optional[List[str]], Optional[List[str]]]:
    """
    Processes multi-modal input using Google Cloud Vision API.
    """
    with tracer.start_as_current_span("process_multi_modal_input"):
        image_descriptions, diagram_descriptions = [], []
        if not VISION_ENABLED:
            if input_data and input_data.get("image_urls"):
                logger.warning(
                    "Multi-modal inputs provided, but feature is disabled by configuration or missing dependencies. Inputs will be ignored."
                )
            return None, None
        if not input_data or "image_urls" not in input_data:
            return None, None

        try:
            client = vision.ImageAnnotatorAsyncClient()
            tasks = []
            for url in input_data["image_urls"]:
                image = vision.Image(source=vision.ImageSource(image_uri=url))
                features = [
                    vision.Feature(type_=vision.Feature.Type.TEXT_DETECTION),
                    vision.Feature(type_=vision.Feature.Type.OBJECT_LOCALIZATION),
                ]
                request = vision.AnnotateImageRequest(image=image, features=features)
                tasks.append(client.annotate_image(request=request))

            responses = await asyncio.gather(*tasks, return_exceptions=True)

            for i, response in enumerate(responses):
                url = input_data["image_urls"][i]
                if isinstance(response, Exception):
                    logger.error(f"Error processing image at {url}: {response}")
                    continue
                if response.error.message:
                    raise Exception(
                        f"Vision API error for {url}: {response.error.message}"
                    )

                # Text Detection
                full_text = (
                    response.text_annotations[0].description
                    if response.text_annotations
                    else "No text detected."
                )
                image_descriptions.append(
                    f"Text from image at {url}:\n---\n{full_text}\n---"
                )

                # Diagram/Object Detection
                if response.localized_object_annotations:
                    diagram_objects = [
                        obj.name for obj in response.localized_object_annotations
                    ]
                    diagram_descriptions.append(
                        f"Diagram objects at {url}:\n- " + "\n- ".join(diagram_objects)
                    )

        except Exception as e:
            logger.error(f"Error processing multi-modal input with Google Vision: {e}")
            PROMPT_ERRORS.labels("GoogleVisionFailure").inc()
            return None, None

        return (
            image_descriptions if image_descriptions else None,
            diagram_descriptions if diagram_descriptions else None,
        )


def get_best_practices(
    target_language: str, framework: Optional[str] = None
) -> List[str]:
    """Dynamically injects language and framework-specific best practices."""
    practices = BEST_PRACTICES.get(target_language, [])
    if framework:
        framework_key = f"{target_language}-{framework}"
        practices.extend(BEST_PRACTICES.get(framework_key, []))
    return practices


async def translate_requirements_if_needed(
    requirements: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Translates non-English requirements to English using the Google Cloud Translate API.
    """
    features = requirements.get("features", [])
    if not features:
        return requirements

    try:
        # Note: This requires a Google Cloud Translate API key/credentials.
        api_key = secrets_manager.get("GOOGLE_TRANSLATE_API_KEY")
        if not api_key:
            raise ValueError("GOOGLE_TRANSLATE_API_KEY not found.")

        # Use Google Cloud Natural Language API for language detection
        nlp_api_key = secrets_manager.get("GOOGLE_CLOUD_NLP_API_KEY")
        if not nlp_api_key:
            raise ValueError("GOOGLE_CLOUD_NLP_API_KEY not found.")

        async with aiohttp.ClientSession() as session:
            # Language detection
            async with session.post(
                "https://language.googleapis.com/v1/documents:analyzeEntities",
                json={
                    "document": {"type": "PLAIN_TEXT", "content": " ".join(features)}
                },
                params={"key": nlp_api_key},
                timeout=10,
            ) as resp:
                resp.raise_for_status()
                # If not English, we translate. (In real API, detect language in the response.)
                # Adjust to your actual response shape.
                result = (await resp.json()).get("language", "en")
                if result == "en":
                    return requirements

            # Translation if non-English
            async with session.post(
                "https://translation.googleapis.com/language/translate/v2",
                json={"q": features, "target": "en"},
                params={"key": api_key},
                timeout=10,
            ) as resp:
                resp.raise_for_status()
                translated = (await resp.json())["data"]["translations"]
                requirements["features"] = [t["translatedText"] for t in translated]
    except Exception as e:
        logger.error(f"Language detection/translation failed: {e}")
        PROMPT_ERRORS.labels("TranslationFailure").inc()
    return requirements


def _parse_requirements_flexible(requirements: Any) -> Dict[str, Any]:
    """
    Parse requirements in any format into a structured dict with 'features' list.
    
    Handles multiple input formats:
    - Dict with 'features' key (pass through)
    - String (markdown, plain text, or JSON string)
    - Other formats (convert to string and extract features)
    
    Args:
        requirements: Requirements in any format
    
    Returns:
        Dict with 'features' list and optional 'description'
    
    Example:
        >>> _parse_requirements_flexible("Build a REST API")
        {'features': ['Build a REST API'], 'description': 'Build a REST API'}
        
        >>> _parse_requirements_flexible({'features': ['API', 'Auth']})
        {'features': ['API', 'Auth']}
    """
    import json
    import re
    
    # Already in correct format
    if isinstance(requirements, dict) and 'features' in requirements and isinstance(requirements['features'], list):
        return requirements
    
    # Try to parse as JSON string
    if isinstance(requirements, str):
        # Try JSON parsing first
        try:
            parsed = json.loads(requirements)
            if isinstance(parsed, dict) and 'features' in parsed:
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
        
        # Extract features from markdown/text
        features = []
        description = requirements
        
        # Look for bullet points (-, *, •)
        bullet_pattern = r'^[\s]*[-*•]\s+(.+)$'
        for line in requirements.split('\n'):
            match = re.match(bullet_pattern, line.strip())
            if match:
                features.append(match.group(1).strip())
        
        # Look for numbered lists (1., 2., etc.)
        numbered_pattern = r'^[\s]*\d+[\.)]\s+(.+)$'
        if not features:
            for line in requirements.split('\n'):
                match = re.match(numbered_pattern, line.strip())
                if match:
                    features.append(match.group(1).strip())
        
        # Look for ## Feature: or # Feature: headers
        feature_header_pattern = r'^#+\s*(feature|requirement|task)s?:\s*(.+)$'
        if not features:
            for line in requirements.split('\n'):
                match = re.match(feature_header_pattern, line.strip(), re.IGNORECASE)
                if match:
                    features.append(match.group(2).strip())
        
        # If no structured features found, split on sentences or use whole text
        if not features:
            # Split on periods for multiple requirements
            sentences = [s.strip() for s in requirements.split('.') if s.strip()]
            if len(sentences) > 1:
                features = sentences[:10]  # Limit to 10 features
            else:
                # Single requirement
                features = [requirements.strip()]
        
        return {
            'features': features,
            'description': description,
        }
    
    # Convert other types to string and treat as single feature
    return {
        'features': [str(requirements)],
        'description': str(requirements),
    }


# ==============================================================================
# --- Main Prompt Builder ---
# ==============================================================================
async def build_code_generation_prompt(
    requirements: Dict[str, Any],
    state_summary: str,
    previous_feedback: Optional[str] = None,
    target_language: str = "python",
    target_framework: Optional[str] = None,
    enable_meta_llm_critique: bool = False,
    multi_modal_inputs: Optional[Dict[str, Any]] = None,
    audit_logger: Any = None,  # <-- FIX: Defaulted to None. Implementation uses log_audit_event.
    redis_client: Optional[aioredis.Redis] = None,
) -> str:
    """
    Builds a production-ready, context-aware, and optimized prompt for code generation.
    """
    # Determine template name for metric labeling
    template_name = f"{target_language}_{target_framework}" if target_framework else target_language
    
    with PROMPT_BUILD_LATENCY.labels(template=template_name).time():
        with tracer.start_as_current_span(
            "build_prompt",
            attributes={
                "target_language": target_language,
                "target_framework": target_framework,
            },
        ):
            logger.info(
                f"Building prompt for language='{target_language}', framework='{target_framework}'"
            )

            # 1. Input Validation & Flexible Parsing
            # FIX: Accept requirements in any format and normalize to dict with 'features'
            try:
                requirements = _parse_requirements_flexible(requirements)
            except Exception as e:
                logger.warning(f"Failed to parse requirements flexibly: {e}. Using as-is.")
                # If parsing fails completely, wrap in basic structure
                if not isinstance(requirements, dict):
                    requirements = {
                        'features': [str(requirements)],
                        'description': str(requirements)
                    }
            
            # Validate after parsing
            if (
                not isinstance(requirements, dict)
                or "features" not in requirements
                or not isinstance(requirements["features"], list)
            ):
                PROMPT_ERRORS.labels("InvalidInput").inc()
                raise ValueError(
                    "Requirements must be a dictionary with a 'features' list."
                )
            if not isinstance(target_language, str) or not target_language:
                PROMPT_ERRORS.labels("InvalidInput").inc()
                raise ValueError("Target language must be a non-empty string.")
            if multi_modal_inputs and (
                not isinstance(multi_modal_inputs.get("image_urls"), list)
                or not all(
                    isinstance(url, str) and url.startswith(("http://", "https://"))
                    for url in multi_modal_inputs.get("image_urls", [])
                )
            ):
                PROMPT_ERRORS.labels("InvalidInput").inc()
                raise ValueError(
                    "Multi-modal inputs must include a list of valid URLs."
                )

            # 2. Internationalization
            requirements = await translate_requirements_if_needed(requirements)

            # 3. Process Multi-modal inputs
            image_desc, diagram_desc = await process_multi_modal_input(
                multi_modal_inputs
            )

            # 4. Context Augmentation (RAG)
            rag_context = await retrieve_augmented_context(
                requirements, target_language, redis_client
            )

            # 5. Inject Best Practices
            best_practices = get_best_practices(target_language, target_framework)

            # 6. Load and Render Template
            template_name = f"{target_language}.jinja2"
            try:
                template = env.get_template(template_name)
                logger.info(f"Using template: {template_name}")
            except TemplateNotFound:
                logger.warning(
                    f"Template '{template_name}' not found. Falling back to base.jinja2."
                )
                template = env.get_template(
                    "base.jinja2"
                )  # Assume a generic fallback template exists

            prompt = template.render(
                requirements=requirements,
                state_summary=state_summary,
                previous_feedback=previous_feedback,
                rag_context=rag_context,
                best_practices=best_practices,
                image_descriptions=image_desc,
                diagram_descriptions=diagram_desc,
                target_language=target_language,
                target_framework=target_framework,
            )

            # 7. Final self-critique and refinement (if enabled)
            if enable_meta_llm_critique and META_LLM_API_KEY:
                with tracer.start_as_current_span("meta_llm_critique"):
                    try:
                        critique_prompt = f"Critique and suggest one key improvement for this code generation prompt to ensure correctness and adherence to best practices. Be concise. Prompt to critique:\n\n{prompt[:3000]}"

                        # --- Security Change: Replace security_utils.scrub_text with redact_secrets ---
                        redacted_critique_prompt = redact_secrets(critique_prompt)
                        # --- End Security Change ---

                        headers = {
                            "Authorization": f"Bearer {META_LLM_API_KEY}",
                            "Content-Type": "application/json",
                        }
                        # --- Security Change: Use redacted prompt in API call ---
                        data = {
                            "model": META_LLM_MODEL,
                            "messages": [
                                {"role": "user", "content": redacted_critique_prompt}
                            ],
                        }
                        # --- End Security Change ---

                        async with aiohttp.ClientSession() as session:
                            async with session.post(
                                META_LLM_API_URL, headers=headers, json=data, timeout=20
                            ) as resp:
                                resp.raise_for_status()
                                jr = await resp.json()
                                critique = jr["choices"][0]["message"][
                                    "content"
                                ].strip()
                        prompt += f"\n\n--- Self-Correction Advisory ---\n{critique}\n--- End Advisory ---"
                        # --- Logging Change: Replace audit_logger.log_action with log_audit_event ---
                        await log_audit_event("Prompt Self-Refined", {"refinement": critique})
                        # --- End Logging Change ---
                    except Exception as e:
                        logger.error(f"Meta-LLM critique failed: {e}")
                        PROMPT_ERRORS.labels("MetaLLMFailure").inc()

            # 8. Add critical output requirements
            # These requirements are appended to every prompt to ensure consistent,
            # parseable output from the LLM regardless of the template used.
            # This prevents common issues like markdown-wrapped responses and conversational text.
            output_requirements = """

========================================
CRITICAL OUTPUT REQUIREMENTS
========================================

Your response MUST adhere to these requirements:

1. CODE ONLY: Output ONLY executable code - no explanations, no markdown formatting, 
   no conversational text like "Here's the code" or "Let me know if you need changes"

2. NO MARKDOWN FENCES: Do NOT wrap code in markdown code fences (```python```)

3. IMMEDIATE CODE: Start your response with the first line of code 
   (import statements, docstrings, or actual code)

4. VALID SYNTAX: The entire response must be valid {language} code that can be 
   executed directly without modification

5. MULTI-FILE FORMAT: If generating multiple files, respond with valid JSON ONLY:
   {{"files": {{"filename.ext": "code content", "other.ext": "other code"}}}}

FAILURE TO FOLLOW THESE REQUIREMENTS WILL RESULT IN PARSE ERRORS.
""".format(language=target_language)
            
            prompt = prompt + output_requirements
            
            logger.debug(
                "Added critical output requirements to prompt (target_language: %s)",
                target_language
            )

            # 9. Final token check
            # --- Token Counting Change: Replace codegen_llm_call.get_token_count with count_tokens ---
            token_count = count_tokens(
                prompt, META_LLM_MODEL
            )  # Using META_LLM_MODEL as a representative LLM model name
            # --- End Token Counting Change ---

            if token_count > MAX_PROMPT_TOKENS:
                logger.warning(
                    f"Final prompt exceeds token limit ({token_count} > {MAX_PROMPT_TOKENS})."
                )

            # --- Logging Change: Replace audit_logger.log_action with log_audit_event ---
            await log_audit_event(
                "Code Generation Prompt Built",
                {
                    "prompt_length": len(prompt),
                    "token_count": token_count,
                    "target_language": target_language,
                },
            )
            # --- End Logging Change ---
            return prompt
