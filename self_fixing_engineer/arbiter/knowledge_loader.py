# knowledge_loader.py

import os
import json
import asyncio
import tempfile
import logging
import threading
from typing import Dict, Any, Optional, Union
from copy import deepcopy
from arbiter.arbiter_plugin_registry import register, PlugInKind

logger = logging.getLogger(__name__)

# --- Utility Functions (Thread-safe) ---

def merge_dict(orig: Dict[str, Any], new: Dict[str, Any]) -> None:
    """
    Recursively merges dictionary 'new' into dictionary 'orig'.
    
    Existing keys in 'orig' are updated with values from 'new'.
    Nested dictionaries are merged recursively.
    Lists are extended without duplicates.
    
    Args:
        orig (Dict[str, Any]): The original dictionary to merge into.
        new (Dict[str, Any]): The new dictionary to merge from.
    """
    for k, v in new.items():
        if k not in orig:
            orig[k] = v
        elif isinstance(v, dict) and isinstance(orig[k], dict):
            merge_dict(orig[k], v)
        elif isinstance(v, list) and isinstance(orig[k], list):
            for item in v:
                if item not in orig[k]:
                    orig[k].append(item)
        else:
            orig[k] = v

def save_knowledge_atomic(filename: Union[str, os.PathLike], knowledge_data: Dict[str, Any]) -> None:
    """
    Performs an atomic write for knowledge data, preventing partial or corrupt saves.
    
    Args:
        filename (Union[str, os.PathLike]): The path to the file to save.
        knowledge_data (Dict[str, Any]): The data to save.
        
    Raises:
        IOError: If saving the file fails.
    """
    dir_name = os.path.dirname(filename)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)
        
    fd, temp_path = tempfile.mkstemp(dir=dir_name, prefix='.tmp_sfe_', suffix='.json')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as tmp_file:
            json.dump(knowledge_data, tmp_file, indent=2)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(temp_path, filename)
        logger.info(f"Saved aggregated knowledge to {filename} atomically.")
    except (IOError, OSError, TypeError) as e:
        logger.error(f"ERROR saving knowledge to {filename}: {e}")
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise IOError(f"Failed to save knowledge file {filename}") from e

def _load_knowledge_sync(filename: Union[str, os.PathLike]) -> Optional[Dict[str, Any]]:
    """
    Loads knowledge from a file, returning None if the file is not found or malformed.
    
    Args:
        filename (Union[str, os.PathLike]): The path to the file to load.
        
    Returns:
        Optional[Dict[str, Any]]: The loaded knowledge dictionary, or None on failure.
    """
    if not os.path.exists(filename):
        return None
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError, OSError) as e:
        logger.error(f"Failed to load or parse JSON from {filename}: {e}")
        return None

# --- Main Class ---

class KnowledgeLoader:
    def __init__(
        self,
        knowledge_data_path: Union[str, os.PathLike] = "knowledge_data/",
        master_knowledge_file: str = "master_knowledge.json"
    ):
        """
        Initializes the KnowledgeLoader instance in a thread-safe manner.
        
        Args:
            knowledge_data_path (Union[str, os.PathLike]): Path to the directory
                                                            containing knowledge files.
            master_knowledge_file (str): The name of the master knowledge file
                                         within the knowledge_data_path.
        """
        self.knowledge_data_path = knowledge_data_path
        self.master_knowledge_file = os.path.join(knowledge_data_path, master_knowledge_file)
        self.loaded_knowledge: Dict[str, Any] = {}
        self._lock = threading.Lock()
        
        self._canonical_knowledge_data = {
            "SelfFixingEngineer": {
                "mission": "Automate, explain, and continually improve codebases using agentic, auditable, and secure AI-driven refactoring.",
                "features": [
                    "Semantic code refactoring",
                    "Multi-language support (Python, JS, Go, Rust, etc.)",
                    "Swarm-based analysis and decision making",
                    "Sandboxed plugin system (security-first)",
                    "Universal code formatting and style normalization",
                    "Automated import and dependency cleanup",
                    "Automated anti-pattern detection and correction",
                    "Explainable, auditable transformation log",
                    "Continuous self-testing and rollback",
                    "Open telemetry and observability integration"
                ],
                "core_principles": [
                    "Explainability",
                    "Security and isolation",
                    "Continuous learning and improvement",
                    "Full auditability",
                    "Reversible operations",
                    "Extensibility",
                    "Zero-trust by default"
                ],
                "architecture": {
                    "main_components": [
                        "Refactor Agent",
                        "Plugin Manager",
                        "Semantic Analyzer",
                        "Audit Ledger",
                        "Sandboxed Plugin Executor",
                        "Self-Test Harness",
                        "Universal Package Manager (UPM)"
                    ],
                    "data_flow": "Code is ingested, analyzed, and refactored by agent swarms. All changes are explainable and auditable, with automatic rollback if needed."
                },
                "story": "Started in 2025 to give developers autonomous, trustworthy tools for large-scale, continuous code improvement, with zero vendor lock-in.",
                "community": "Open-source contributors, AI engineers, security professionals, and maintainers."
            },
            "EngineeringPractices": {
                "best_practices": [
                    "Test-driven development",
                    "Code review before merge",
                    "Continuous integration and delivery",
                    "Static and dynamic code analysis",
                    "Plugin sandboxing and permissioning",
                    "Security-first design",
                    "Automated dependency management"
                ],
                "philosophy": [
                    "Fail fast, recover faster",
                    "Every change must be explainable",
                    "Logs are gold—never lose a step",
                    "Automate repetition, but keep humans in the loop",
                    "If you refactor, you must test and audit"
                ]
            },
            "AgenticAI": {
                "capabilities": [
                    "Autonomous code analysis and repair",
                    "Multi-agent coordination (swarm intelligence)",
                    "Reasoning with provenance and audit trails",
                    "Learning from codebase evolution and test results",
                    "Safe plugin negotiation and selection"
                ],
                "principles": [
                    "Agent actions must be auditable and reversible",
                    "No plugin runs unsandboxed",
                    "Learning must not compromise code health or security"
                ]
            },
            "PluginSystem": {
                "design": [
                    "All plugins run in secure sandboxes",
                    "Permission-based access to code, files, and network",
                    "Versioning and signature verification required",
                    "Plugins can be composed, replaced, or hot-swapped",
                    "Refactor plugins and analyzers share a unified API"
                ],
                "examples": [
                    "Import optimizer",
                    "Style normalizer (Black/Prettier-like)",
                    "Security linter",
                    "Dead code remover",
                    "Automated dependency updater"
                ]
            },
            "AuditAndExplainability": {
                "requirements": [
                    "Every agent and plugin action is logged (who, what, when, why)",
                    "Merkle tree or hash-chain based tamper evidence",
                    "Rollback and recovery possible from any state",
                    "Human-friendly change summaries and explanations"
                ],
                "tools": [
                    "Audit ledger",
                    "Prometheus/OpenTelemetry integration",
                    "Self-test runner"
                ]
            },
            "Security": {
                "threat_model": [
                    "Plugin sandbox escape",
                    "Malicious plugin supply chain",
                    "Unauthorized code changes",
                    "Loss of audit logs"
                ],
                "controls": [
                    "Sandboxed plugin execution (gVisor, WASM, subprocess isolation)",
                    "Cryptographically signed plugins and configs",
                    "RBAC and policy enforcement",
                    "CI-based and pre-commit test gates"
                ]
            }
        }
        
        self.load_all()

    def get_knowledge(self) -> Dict[str, Any]:
        """
        Returns a deep copy of the currently loaded knowledge.
        This prevents external code from modifying the internal state.
        """
        with self._lock:
            return deepcopy(self.loaded_knowledge)

    def save_current_knowledge(self) -> None:
        """Saves the entire aggregated knowledge to the master file atomically."""
        with self._lock:
            try:
                save_knowledge_atomic(self.master_knowledge_file, self.loaded_knowledge)
            except IOError as e:
                logger.error(f"Failed to save current knowledge: {e}")

    def load_all(self) -> None:
        """
        Loads all knowledge into the loader instance in a thread-safe manner.
        
        Prioritizes loading from the master_knowledge_file if it exists.
        If not, it loads from individual JSON files, merges with canonical data,
        and saves the aggregated knowledge to the master file for future use.
        """
        with self._lock:
            logger.info("Loading all knowledge...")

            # 1. Try to load from the master knowledge file first
            master_data = _load_knowledge_sync(self.master_knowledge_file)
            if master_data:
                self.loaded_knowledge = master_data
                logger.info(f"  Loaded knowledge from master file: {self.master_knowledge_file}")
                return

            # If no master file, proceed with merging logic
            temp_loaded_data = {}
            if os.path.exists(self.knowledge_data_path) and os.path.isdir(self.knowledge_data_path):
                for filename in os.listdir(self.knowledge_data_path):
                    if filename.endswith(".json") and filename != os.path.basename(self.master_knowledge_file):
                        filepath = os.path.join(self.knowledge_data_path, filename)
                        try:
                            with open(filepath, 'r', encoding="utf-8") as f:
                                data = json.load(f)
                            merge_dict(temp_loaded_data, data)
                            logger.info(f"  Loaded knowledge from {filename}")
                        except (IOError, json.JSONDecodeError, OSError) as e:
                            logger.error(f"  Error loading {filename}: {e}")
            else:
                logger.warning(f"  Knowledge data path '{self.knowledge_data_path}' not found or is not a directory. Using internal canonical knowledge.")

            # Always start with canonical and merge in loaded data
            self.loaded_knowledge = deepcopy(self._canonical_knowledge_data)
            merge_dict(self.loaded_knowledge, temp_loaded_data)

            # Save the initial aggregated knowledge to the master file
            self.save_current_knowledge()

    def inject_to_arbiter(self, arbiter_instance: Any) -> None:
        """
        Injects the loaded knowledge into an arbiter's state["memory"] using merge_dict.
        This method is synchronous as it only manipulates in-memory dictionaries.
        
        Args:
            arbiter_instance (Any): The arbiter object to inject knowledge into.
                                    Assumes it has a `state` attribute that is a dictionary
                                    containing a `memory` key.
        """
        with self._lock:
            if not hasattr(arbiter_instance, 'state') or not isinstance(arbiter_instance.state, dict):
                logger.error("Arbiter instance does not have a valid 'state' dictionary.")
                return

            logger.info(f"Injecting knowledge into {getattr(arbiter_instance, 'name', 'unnamed-arbiter')}...")

            # Use a defensive approach to access the memory dictionary
            mem = arbiter_instance.state.setdefault("memory", {})

            if not isinstance(mem, dict):
                logger.error("Arbiter state['memory'] is not a dictionary. Cannot inject knowledge.")
                return
            
            # Merge each knowledge domain into the arbiter's memory
            for domain_name, domain_data in self.loaded_knowledge.items():
                if domain_name not in mem:
                    mem[domain_name] = deepcopy(domain_data)
                    logger.debug(f"  Added new domain '{domain_name}'.")
                else:
                    if isinstance(domain_data, dict) and isinstance(mem.get(domain_name), dict):
                        merge_dict(mem[domain_name], deepcopy(domain_data))
                        logger.debug(f"  Merged data into existing domain '{domain_name}'.")
                    elif isinstance(domain_data, list) and isinstance(mem.get(domain_name), list):
                        for item in domain_data:
                            if item not in mem[domain_name]:
                                mem[domain_name].append(item)
                        logger.debug(f"  Extended list in existing domain '{domain_name}'.")
                    else:
                        mem[domain_name] = deepcopy(domain_data)  # Overwrite on type mismatch
                        logger.warning(f"  Overwrote domain '{domain_name}' due to type mismatch.")

            logger.info(f"Knowledge injection complete for {getattr(arbiter_instance, 'name', 'unnamed-arbiter')}.")

async def load_knowledge(filename: str) -> Optional[Dict[str, Any]]:
    """
    Asynchronous plugin wrapper for loading knowledge from a specified file.
    """
    try:
        # Run the synchronous file I/O in a thread pool to avoid blocking the event loop
        knowledge_data = await asyncio.to_thread(_load_knowledge_sync, filename)
        return knowledge_data
    except Exception as e:
        logger.error(f"Plugin 'load_knowledge' failed: {e}", exc_info=True)
        return None

register(kind=PlugInKind.CORE_SERVICE, name="knowledge_loader", version="1.0.0", author="Arbiter Team")(load_knowledge)