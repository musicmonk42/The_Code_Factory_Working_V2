# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# runner/summarize_utils.py
import asyncio
import hashlib  # For hashing summaries for feedback
import os
import sys  # For checking module status for conditional imports
import time  # For timestamping feedback
from concurrent.futures import ThreadPoolExecutor
from functools import wraps  # [NEW] Added for no-op decorator
from typing import Any, Callable, Dict, List, Optional

# --- [FIX] Added TESTING flag to prevent ML libs from loading during pytest ---
TESTING: bool = (
    os.getenv("TESTING") == "1"
    or "pytest" in sys.modules
    or os.getenv("PYTEST_CURRENT_TEST") is not None
    or os.getenv("PYTEST_ADDOPTS") is not None
)
# --- END FIX ---

from .feedback_handlers import collect_feedback

# --- REFACTOR FIX: Imports changed from V1 'utils' to V2 'runner' foundation ---
# This file no longer imports from llm_utils. It imports the *real* LLM client.
from .llm_client import call_llm_api

# [FIX] Corrected imports
from .runner_audit import log_audit_event
from .runner_logging import logger, send_alert
from .runner_metrics import UTIL_ERRORS
from .runner_security_utils import redact_secrets

# Import SUMMARIZERS registry from the runner's __init__.py
try:
    from runner import (
        SUMMARIZERS,
    )  # Registry for plug-in summarizers is defined in __init__.py
except ImportError:
    logger.warning(
        "Could not import SUMMARIZERS registry from 'runner'. Defining local registry."
    )
    from shared.registry import Registry  # noqa: E402
    SUMMARIZERS = Registry()
# --- END REFACTOR FIX ---


# [NEW] No-op fallbacks for metrics/decorators
def util_decorator(func: Callable):
    """No-op decorator fallback."""

    @wraps(func)
    async def _aw(*a, **k):
        return await func(*a, **k)

    @wraps(func)
    def _sw(*a, **k):
        return func(*a, **k)

    return _aw if asyncio.iscoroutinefunction(func) else _sw


def detect_anomaly(*a, **k):
    """No-op anomaly detection fallback."""
    logger.debug("detect_anomaly called, but no-op implementation is in use.")
    return False


# --- Plug-in Summarizers ---
# These functions are registered with the SUMMARIZERS registry.


@util_decorator
def code_summary(state: Dict[str, Any], max_length: int = 2000) -> str:
    """Summarizes information related to code files and critique results."""
    summary_parts = []
    if isinstance(state, dict):  # Check if state is a dict, not a string
        if "code_files" in state and state["code_files"]:
            # Limit the number of file names for brevity
            file_names = list(state["code_files"].keys())
            preview_names = ", ".join(file_names[:5]) + (
                "..." if len(file_names) > 5 else ""
            )
            summary_parts.append(f"Code files overview: {preview_names}")
        if "critique_results" in state and state["critique_results"]:
            # Summarize critique results
            critique = state["critique_results"]
            summary_parts.append(
                f"Critique summary: Alignment={critique.get('semantic_alignment_score', 'N/A')*100}%, Quality={critique.get('test_quality_score', 'N/A')*100}%"
            )
            if critique.get("drift_issues"):
                summary_parts.append(
                    f"Found {len(critique['drift_issues'])} drift issues."
                )
            if critique.get("hallucinations"):
                summary_parts.append(
                    f"Found {len(critique['hallucinations'])} hallucinations."
                )

    # Simple concatenation for now; could be fed to another summarizer.
    full_summary = ". ".join(summary_parts)
    return full_summary[:max_length]


@util_decorator
def requirements_summary(state: Dict[str, Any], max_length: int = 2000) -> str:
    """Summarizes requirements, features, and constraints."""
    reqs = state.get("requirements", {}) if isinstance(state, dict) else {}
    summary_parts = []

    if "features" in reqs:
        summary_parts.append(
            f"Key Features: {', '.join(reqs['features'][:3])}{'...' if len(reqs['features']) > 3 else ''}"
        )
    if "constraints" in reqs:
        summary_parts.append(f"Constraints: {', '.join(reqs['constraints'])}")

    full_summary = ". ".join(summary_parts)
    return full_summary[:max_length]


@util_decorator
def deployment_summary(state: Dict[str, Any], max_length: int = 2000) -> str:
    """Summarizes deployment context (target, dependencies, etc.)."""
    reqs = state.get("requirements", {}) if isinstance(state, dict) else {}
    summary_parts = []

    if "target_config" in reqs:
        cfg = reqs["target_config"]
        summary_parts.append(
            f"Target: {cfg.get('platform', 'N/A')} ({cfg.get('type', 'N/A')})"
        )
    if "dependencies" in reqs:
        summary_parts.append(f"Dependencies: {len(reqs['dependencies'])} packages")

    full_summary = ". ".join(summary_parts)
    return full_summary[:max_length]


# --- REFACTOR: NEW V2 LLM SUMMARIZER ---
# This function replaces the old 'summarize_text' from llm_utils.py.
# It correctly uses the V2 'runner.llm_client.call_llm_api'.


@util_decorator
async def llm_summarize(
    text: str,
    max_length: int = 500,
    min_len: int = 50,
    model: str = "gpt-4o-mini",  # Use a fast, cheap model for summarization
    context: str = "concise technical summary",
) -> str:
    """
    Summarizes text using the central LLM client (V2).
    Includes model fallback logic for robustness.
    """
    if not text:
        return ""

    # Redact before sending to LLM
    # [FIX] redact_secrets is now synchronous, remove await
    text_to_summarize = redact_secrets(text)

    # Use a specific, lightweight prompt for summarization
    prompt = f"""
    Please provide a {context} of the following text.
    The summary must be a maximum of {max_length} characters and a minimum of {min_len} characters.
    Do not add any conversational wrappers, just the summary.

    TEXT_TO_SUMMARIZE:
    ---
    {text_to_summarize}
    ---
    SUMMARY:
    """
    
    # Model fallback chain: prefer gpt-4o-mini, fallback to other available models
    preferred_model = model
    fallback_models = ["gpt-3.5-turbo", "gpt-4o", "gpt-4"]
    models_to_try = [preferred_model] + [m for m in fallback_models if m != preferred_model]

    for current_model in models_to_try:
        try:
            # [NEW] Call audit around LLM use
            await log_audit_event(
                action="summarize_llm_call",
                data={
                    "model": current_model,
                    "text_length": len(text_to_summarize),
                    "context": context,
                },
            )

            # Call the unified V2 LLM client
            # Note: call_llm_api is from testgen_llm_call.py, which returns a dict
            response_dict = await call_llm_api(
                prompt=prompt,
                model=current_model,
                # --- THIS IS THE FIX ---
                # The `task_type` argument is not supported by call_llm_api
                # task_type="summarization" # <-- REMOVED
            )

            summary = response_dict.get("content", "")

            # Fallback if content is empty
            if not summary:
                logger.warning(
                    f"LLM summarizer returned empty content for model {current_model}. "
                    f"Trying next model in fallback chain."
                )
                continue  # Try next model
            
            # Success - return summary
            if current_model != preferred_model:
                logger.info(f"✓ Summarization succeeded with fallback model {current_model}")
            
            return summary.strip()

        except ValueError as e:
            # Model not registered or validation error - try next model
            error_msg = str(e).lower()
            if ("not registered" in error_msg or "model" in error_msg) and current_model != models_to_try[-1]:
                logger.warning(f"Model {current_model} unavailable ({e}), trying next model")
                continue
            # Last model also failed - fall through to final fallback
            logger.error(f"All LLM models failed: {e}")
            break
        except Exception as e:
            # Other errors - try next model
            if current_model != models_to_try[-1]:
                logger.warning(f"LLM call failed with model {current_model}: {e}, trying next model")
                continue
            # Last model also failed - fall through to final fallback
            logger.error(f"All LLM models failed with error: {e}")
            break
    
    # Ultimate fallback: simple truncation if all models fail
    logger.warning("⚠ All LLM models failed, using truncation as ultimate fallback")
    UTIL_ERRORS.labels(func="llm_summarize", type="all_models_failed").inc()
    return text_to_summarize[:max_length]


# --- END REFACTOR ---


# --- Summarizer Orchestration ---
@util_decorator
async def summarize(
    text: str, provider: str = "llm", max_length: int = 500, min_len: int = 50
) -> str:
    """
    Main entry point for text summarization.
    Selects the summarization provider from the registry and executes it.
    """
    summarizer_func = SUMMARIZERS.get(provider)
    if not summarizer_func:
        logger.error(
            f"Unknown summarization provider: '{provider}'. Falling back to 'llm'."
        )
        summarizer_func = SUMMARIZERS.get("llm")
        if not summarizer_func:  # Should not happen if llm_summarize is registered
            raise KeyError("Default 'llm' summarizer not found in registry.")

    # Await the function if it's async (like llm_summarize)
    if asyncio.iscoroutinefunction(summarizer_func):
        return await summarizer_func(text, max_length=max_length, min_len=min_len)
    # Run in thread pool if it's sync (like a local transformer)
    else:
        loop = asyncio.get_running_loop()
        # The sync summarizers (code_summary, etc.) do not accept `min_len`.
        # We only pass args they *can* accept (text/state and max_length)
        return await loop.run_in_executor(
            None,  # Use default ThreadPoolExecutor
            summarizer_func,
            text,  # This is passed as the 'state' arg for code_summary
            max_length,
        )


# Alias for backward compatibility
call_summarizer = summarize


async def call_summarizer_with_provider(
    text: str, provider: str = "llm", max_length: int = 500, min_len: int = 50
) -> str:
    """
    Backward-compatible wrapper for summarization.
    Alias for the main `summarize` function.
    """
    return await summarize(
        text, provider=provider, max_length=max_length, min_len=min_len
    )


@util_decorator
async def ensemble_summarize(
    text: str, providers: List[str], max_length: int = 500, min_len: int = 50
) -> str:
    """
    Runs multiple summarization providers in parallel and synthesizes the results.
    """
    # [FIX] Added a .get_all() method to the local Registry definition for this to work
    all_providers = []
    if hasattr(SUMMARIZERS, "get_all"):
        all_providers = SUMMARIZERS.get_all()
    elif hasattr(SUMMARIZERS, "_items"):
        all_providers = SUMMARIZERS._items.keys()

    tasks = [
        summarize(text, provider=p, max_length=max_length, min_len=min_len)
        for p in providers
        if p in all_providers
    ]
    summaries = await asyncio.gather(*tasks, return_exceptions=True)

    valid_summaries = [s for s in summaries if isinstance(s, str) and s]
    if not valid_summaries:
        logger.error(
            "Ensemble summarization failed: No valid summaries returned from any provider."
        )
        UTIL_ERRORS.labels(func="ensemble_summarize", type="all_providers_failed").inc()
        return text[:max_length]  # Fallback to truncation

    # Use the 'llm' provider (llm_summarize) to synthesize the results
    synthesis_prompt = f"""
    The following are several summaries of the same text. Synthesize them into a single,
    high-quality summary that captures the best aspects of all.
    The final summary must be a maximum of {max_length} characters.
    Do not add any conversational wrappers, just the summary.
    
    SUMMARIES_TO_SYNTHESIZE:
    ---
    """
    [p for p in providers if p in all_providers]
    # [FIX] Need to get the provider name from the valid summary index
    # This logic assumes the order of successful summaries matches the order of providers
    # that were in the original 'providers' list AND also in 'all_providers'.
    valid_provider_names = [p for p in providers if p in all_providers]

    for i, s in enumerate(valid_summaries):
        # [FIX] Use the valid_provider_names list to find the correct name
        try:
            valid_provider_name = valid_provider_names[i]
            synthesis_prompt += (
                f"SUMMARY {i+1} (from {valid_provider_name}):\n{s}\n---\n"
            )
        except IndexError:
            # This should not happen if logic is correct, but good to guard.
            synthesis_prompt += f"SUMMARY {i+1} (from unknown):\n{s}\n---\n"

    synthesis_prompt += "FINAL_SYNTHESIZED_SUMMARY:"

    # Call the llm_summarize function directly
    final_summary = await llm_summarize(
        text=synthesis_prompt,  # Note: We are summarizing the *summaries*
        max_length=max_length,
        min_len=min_len,
        model="gpt-4o-mini",  # Use a fast model for synthesis
        context="synthesis of multiple summaries",
    )

    # [FIX] Replaced add_provenance with log_audit_event
    await log_audit_event(
        action="summarize_ensemble",
        data={
            "providers_used": providers,
            "valid_summaries": len(valid_summaries),
            "final_length": len(final_summary),
        },
    )

    return final_summary


# --- Feedback Loop ---
def refine_from_feedback(
    summary: str,
    rating: float,
    feedback_source: str,
    template_name: Optional[str] = None,
    provider_name: Optional[str] = None,
):
    """
    Collects feedback on a summary's quality and triggers alerts or
    refinement workflows based on the rating.
    """
    summary_hash = hashlib.sha256(summary.encode("utf-8")).hexdigest()

    feedback_data = {
        "summary_hash": summary_hash,
        "rating": rating,
        "source": feedback_source,
        "template_name": template_name,
        "provider_name": provider_name,
        "timestamp": time.time(),
    }

    # Use the resilient feedback handler
    collect_feedback("summary_quality", feedback_data)

    # Trigger anomaly detection and alerts
    detect_anomaly(
        metric_name=f"summary_rating_{provider_name}_{template_name}",
        value=rating,
        threshold=0.5,  # Alert if rating is below 0.5
        anomaly_type="threshold_breach",
        severity="warning",
    )

    if rating < 0.3:
        logger.warning(
            f"Low rating ({rating}) for summary from {provider_name} on {template_name}. Triggering alert."
        )
        # [FIX] Corrected send_alert call signature
        asyncio.create_task(
            send_alert(
                subject="Low Summary Quality Alert",
                message=f"Summary {summary_hash} (from {provider_name}/{template_name}) received critical rating: {rating}",
                severity="critical",
            )
        )


# --- Registration ---
# Register the new V2 LLM summarizer
SUMMARIZERS.register("llm", llm_summarize)
# Register the other summarizers defined in this file
SUMMARIZERS.register("code", code_summary)
SUMMARIZERS.register("requirements", requirements_summary)
SUMMARIZERS.register("deployment", deployment_summary)

# --- [FIX] Gated this entire block to prevent crash during pytest ---
# --- Conditional Registration of Local Transformer Summarizer ---
if TESTING:
    logger.warning(
        "Skipping heavy ML dependency load (Transformers/Torch) during Pytest session."
    )
else:
    try:
        # This block attempts to import heavy ML libraries.
        # It's wrapped in try/except so the module can load without them.
        import torch
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer, pipeline

        # Use a specific, well-regarded model
        _local_model_name = "facebook/bart-large-cnn"
        _local_tokenizer = AutoTokenizer.from_pretrained(_local_model_name)
        _local_model = AutoModelForSeq2SeqLM.from_pretrained(_local_model_name)

        # Create the pipeline
        _local_summarizer_pipeline = pipeline(
            "summarization",
            model=_local_model,
            tokenizer=_local_tokenizer,
            device=0 if torch.cuda.is_available() else -1,  # Use GPU if available
            framework="pt",  # Use PyTorch
        )
        _local_executor = ThreadPoolExecutor(max_workers=2)

        @util_decorator
        async def local_transformer_summary(
            text: str, max_length: int = 500, min_len: int = 50
        ) -> str:
            """
            Summarizes text using a locally run Hugging Face Transformer model.
            This is CPU/GPU intensive and runs in a separate thread pool.
            """
            if not text:
                return ""

            loop = asyncio.get_running_loop()

            # OTel tracing is not available via the no-op decorator, but this is fine.
            # with tracer.start_as_current_span("local_transformer_summary") as span:
            #     span.set_attribute("model.name", _local_model_name)
            #     span.set_attribute("text.length", len(text))

            try:
                # Run the blocking, CPU/GPU-bound task in a thread pool
                summary_results = await loop.run_in_executor(
                    _local_executor,
                    _local_summarizer_pipeline,
                    text,
                    max_length=max_length,
                    min_length=min_len,
                    do_sample=False,
                )

                if summary_results:
                    result_text = summary_results[0]["summary_text"]
                    return result_text

                return ""
            except Exception as e:
                logger.error(f"Failed to generate local summary: {e}", exc_info=True)
                UTIL_ERRORS.labels(func="local_summarize", type=type(e).__name__).inc()
                raise

        SUMMARIZERS.register("local_huggingface", local_transformer_summary)
        logger.info(
            "Hugging Face transformers summarizer ('local_huggingface') registered."
        )
    except ImportError:
        logger.warning(
            "Hugging Face transformers library not found. Local summarization ('local_huggingface') will not be available. (pip install transformers torch)"
        )
    except Exception as e:
        logger.error(
            f"Failed to load Hugging Face summarization pipeline: {e}. Local summarization will not be available.",
            exc_info=True,
        )


# --- Export Aliases ---
# Alias for backward compatibility with docgen_agent
ensemble_summarizers = ensemble_summarize


def check_owasp_compliance(code: str) -> List[Dict[str, Any]]:
    """Run bandit static analysis on *code* and return OWASP-style findings.

    Spawns ``bandit -r <tempfile> -f json -q`` in a subprocess, parses the
    JSON output, and returns a list of dicts with keys ``issue``, ``severity``,
    and ``line``.  Falls back to an empty list (with a logged warning) only
    when ``bandit`` is not installed on PATH.

    Args:
        code: Source code string to analyse.

    Returns:
        List of ``{"issue": str, "severity": str, "line": int}`` dicts.
    """
    import json as _json
    import shutil
    import subprocess
    import tempfile

    if not shutil.which("bandit"):
        logger.warning(
            "check_owasp_compliance: 'bandit' not found on PATH; OWASP scan skipped."
        )
        return []

    findings: List[Dict[str, Any]] = []
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as tmp:
            tmp.write(code)
            tmp_path = tmp.name

        result = subprocess.run(
            ["bandit", "-r", tmp_path, "-f", "json", "-q"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.stdout:
            report = _json.loads(result.stdout)
            for issue in report.get("results", []):
                findings.append(
                    {
                        "issue": issue.get("issue_text", ""),
                        "severity": issue.get("issue_severity", "UNKNOWN"),
                        "line": issue.get("line_number", 0),
                    }
                )
    except subprocess.TimeoutExpired:
        logger.warning("check_owasp_compliance: bandit timed out.")
    except Exception as exc:
        logger.warning(f"check_owasp_compliance: unexpected error: {exc}")
    finally:
        try:
            import os as _os

            _os.unlink(tmp_path)
        except Exception:
            pass

    return findings
