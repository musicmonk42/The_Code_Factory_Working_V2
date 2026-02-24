# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
model_registry.py — Shared singleton registry for heavy ML models.

Loading models like SentenceTransformer on every agent invocation wastes ~400ms
per load and causes redundant memory allocation.  This module provides a
thread-safe, process-global singleton so the model is loaded at most once per
worker process.

Usage::

    from generator.utils.model_registry import get_sentence_transformer

    model = get_sentence_transformer()          # default: all-MiniLM-L6-v2
    model = get_sentence_transformer("model-name")
"""

import threading
from typing import Optional

_model_lock = threading.Lock()
_sentence_transformer_instances: dict = {}


def get_sentence_transformer(model_name: str = "all-MiniLM-L6-v2"):
    """
    Return a cached SentenceTransformer instance for *model_name*.

    The model is loaded lazily on the first call and then reused for all
    subsequent calls within the same process (double-checked locking pattern).

    Args:
        model_name: HuggingFace model identifier.  Defaults to
                    ``"all-MiniLM-L6-v2"``, which is the model used across
                    the deploy, testgen, and docgen agents.

    Returns:
        A ``SentenceTransformer`` instance, or ``None`` if the
        ``sentence_transformers`` package is not installed.
    """
    global _sentence_transformer_instances

    if model_name in _sentence_transformer_instances:
        return _sentence_transformer_instances[model_name]

    with _model_lock:
        # Re-check after acquiring the lock (double-checked locking)
        if model_name not in _sentence_transformer_instances:
            try:
                from sentence_transformers import SentenceTransformer
                _sentence_transformer_instances[model_name] = SentenceTransformer(model_name)
            except ImportError:
                _sentence_transformer_instances[model_name] = None
            except Exception:
                _sentence_transformer_instances[model_name] = None

    return _sentence_transformer_instances[model_name]
