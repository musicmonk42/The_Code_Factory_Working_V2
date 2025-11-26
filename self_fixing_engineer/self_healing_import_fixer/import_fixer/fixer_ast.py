"""
fixer_ast.py

Provides AST-based code healing for security and maintainability issues,
specifically targeting circular dependencies and dynamic imports.
"""

import ast
import asyncio  # For running async methods in a sync context
import hashlib
import logging
import os
import sys  # For sys.exit
import threading
from ast import NodeTransformer
from pathlib import Path
from typing import Any, List, Optional, Set, Tuple

import networkx as nx  # For graph operations in CycleHealer

# --- Guard POSIX-only imports ---
try:
    import resource  # For resource limits (Unix-like systems)
except ImportError:
    resource = None

# --- Global Production Mode Flag (from main orchestrator) ---
PRODUCTION_MODE = os.getenv("PRODUCTION_MODE", "false").lower() == "true"

logger = logging.getLogger(__name__)

# --- Centralized Utilities (replacing placeholders) ---
try:
    from self_healing_import_fixer.import_fixer.cache_layer import get_cache
    from self_healing_import_fixer.import_fixer.compat_core import (
        SECRETS_MANAGER,
        alert_operator,
        audit_logger,
        scrub_secrets,
    )
except ImportError as e:
    logger.critical(
        f"CRITICAL: Missing core dependency for fixer_ast: {e}. Aborting startup."
    )
    try:
        alert_operator(
            f"CRITICAL: AST healing missing core dependency: {e}. Aborting.",
            level="CRITICAL",
        )
    except Exception:
        pass
    raise RuntimeError(f"[CRITICAL][AST] Missing core dependency: {e}")

# --- Optional redis import (for caching) ---
try:
    import redis.asyncio as redis
except Exception:
    redis = None


class AnalyzerCriticalError(RuntimeError):
    """
    Custom exception for critical errors that should halt execution and alert ops.
    """

    def __init__(self, message: str, alert_level: str = "CRITICAL"):
        super().__init__(f"[CRITICAL][AST] {message}")
        try:
            alert_operator(message, alert_level)
        except Exception:
            pass


class NonCriticalError(Exception):
    """
    Custom exception for recoverable issues that should be logged but not halt execution.
    """

    pass


# --- Plugin Integration: Only allow AI suggestion hook to come from a signature-verified, whitelisted module. ---
# This is a conceptual check. In a real system, `fixer_ai` would be loaded via a PluginManager
# that performs signature verification and whitelisting.
# For now, we'll assume `get_ai_refactoring_suggestion_real` is the trusted source.
try:
    from self_healing_import_fixer.import_fixer.fixer_ai import (
        get_ai_patch as get_ai_patch_real,
    )
    from self_healing_import_fixer.import_fixer.fixer_ai import (
        get_ai_suggestions as get_ai_suggestions_real,
    )
    FIXER_AI_AVAILABLE = True
except ImportError as e:
    logger.warning(
        f"fixer_ai module not found: {e}. AI-powered refactoring suggestions will be unavailable. "
        f"Install openai and tiktoken to enable AI features."
    )
    FIXER_AI_AVAILABLE = False

    # Provide no-op fallbacks
    async def get_ai_patch_real(*args, **kwargs):
        return None

    async def get_ai_suggestions_real(*args, **kwargs):
        return []

_BG_LOOP = None
_BG_THREAD = None
_BG_LOOP_READY = threading.Event()


def _ensure_background_loop():
    """
    Starts (or reuses) a dedicated background event loop running in a daemon thread.
    Thread-safe and idempotent. Returns the running loop.
    """
    if _BG_LOOP and _BG_LOOP.is_running():
        return _BG_LOOP

    def _runner():
        global _BG_LOOP
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        _BG_LOOP = loop
        _BG_LOOP_READY.set()
        try:
            loop.run_forever()
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()

    _BG_LOOP_READY.clear()
    _BG_THREAD = threading.Thread(target=_runner, name="fixer-bg-loop", daemon=True)
    _BG_THREAD.start()
    # Wait briefly for loop to come up
    if not _BG_LOOP_READY.wait(timeout=2.0):
        raise RuntimeError("Background event loop failed to start")
    return _BG_LOOP


def get_ai_refactoring_suggestion(context: str) -> str:
    """Wrapper to call the real AI suggestion function."""
    # Secret Scrubbing: All code/context sent to AI for suggestions must be scrubbed of secrets.
    scrubbed_context = scrub_secrets(context)
    audit_logger.log_event(
        "ai_suggestion_request", context_snippet=scrubbed_context[:200]
    )

    if not FIXER_AI_AVAILABLE:
        logger.warning("AI suggestions unavailable - FIXER_AI_AVAILABLE is False")
        return ""

    try:
        suggestion = get_ai_suggestions_real(scrubbed_context)
        suggestion_str = "\n".join(suggestion)
        audit_logger.log_event(
            "ai_suggestion_response", suggestion_snippet=suggestion_str[:200]
        )
        return suggestion_str
    except Exception as e:
        logger.error(f"Error getting AI refactoring suggestion: {e}", exc_info=True)
        audit_logger.log_event(
            "ai_suggestion_failure",
            error=str(e),
            context_snippet=scrubbed_context[:200],
        )
        alert_operator(
            f"CRITICAL: AI refactoring suggestion failed: {e}. Aborting healing.",
            level="CRITICAL",
        )
        raise  # Re-raise to propagate fail-fast


def _run_async_in_sync(coro, *, timeout: float = 10.0):
    """
    Robust sync/async bridge:
    - Always submit to a dedicated background event loop using
      asyncio.run_coroutine_threadsafe.
    - Wait for result with a bounded timeout to prevent test hangs.
    """
    loop = _ensure_background_loop()
    fut = asyncio.run_coroutine_threadsafe(coro, loop)
    return fut.result(timeout=timeout)


class ImportResolver(NodeTransformer):
    """
    Resolves relative imports to absolute ones within a given project context.
    """

    def __init__(
        self,
        current_module_path: str,
        project_root: str,
        whitelisted_paths: List[str],
        root_package_names: List[str],
    ):
        """
        Initializes the ImportResolver.

        Args:
            current_module_path (str): The absolute module path of the file being processed
                                       (e.g., 'my_package.sub_module').
            project_root (str): The absolute path to the project's root directory.
            whitelisted_paths (List[str]): List of absolute paths to whitelisted directories.
            root_package_names (List[str]): A list of top-level package names that define
                                            the roots of the monorepo/project.
        """
        self.current_module_path = current_module_path
        self.project_root = project_root
        self.whitelisted_paths = whitelisted_paths
        self.root_package_names = root_package_names
        self.modified = False
        logger.debug(
            f"ImportResolver initialized for module: {self.current_module_path}"
        )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> ast.ImportFrom:
        """
        Visits an `ast.ImportFrom` node to convert relative imports to absolute.
        Input Path Validation: Never operate outside whitelisted project directories.
        """
        # Ensure the current file's path is within whitelisted directories
        current_file_path = (
            os.path.join(self.project_root, *self.current_module_path.split("."))
            + ".py"
        )
        if not any(current_file_path.startswith(wp) for wp in self.whitelisted_paths):
            logger.critical(
                f"CRITICAL: Attempted to resolve imports in '{current_file_path}' which is outside whitelisted paths: {self.whitelisted_paths}. Aborting operation."
            )
            audit_logger.log_event(
                "security_violation",
                type="path_traversal_attempt",
                file=current_file_path,
                whitelisted_paths=self.whitelisted_paths,
            )
            alert_operator(
                f"CRITICAL: ImportResolver operating outside whitelisted paths: {current_file_path}. Aborting.",
                level="CRITICAL",
            )
            raise AnalyzerCriticalError(
                f"ImportResolver operating outside whitelisted paths: {current_file_path}."
            )

        if node.level > 0:  # It's a relative import
            base_parts = self.current_module_path.split(".")
            source_parts = base_parts[: len(base_parts) - node.level]

            if node.module:
                source_parts.append(node.module)

            new_module_path = ".".join(filter(None, source_parts))

            is_under_root = False
            for r in self.root_package_names:
                if new_module_path == r or new_module_path.startswith(f"{r}."):
                    is_under_root = True
                    break

            if not is_under_root and self.root_package_names:
                candidate_module_path = (
                    f"{self.root_package_names[0]}.{new_module_path}".strip(".")
                )
                for root_name in self.root_package_names:
                    if candidate_module_path.startswith(f"{root_name}.{root_name}."):
                        candidate_module_path = candidate_module_path[
                            len(root_name) + 1 :
                        ]
                new_module_path = candidate_module_path

            logger.debug(
                f"Resolving in '{self.current_module_path}': from {'.' * node.level}{node.module or ''} -> {new_module_path}"
            )
            node.module, node.level = new_module_path, 0
            self.modified = True

        return node


class CycleHealer:
    """
    Heals circular import dependencies by transforming ASTs, potentially
    moving imports into function scope or suggesting AI-driven refactorings.
    """

    def __init__(
        self,
        file_path: str,
        cycle: List[str],
        graph: nx.DiGraph,
        project_root: str,
        whitelisted_paths: List[str],
    ):
        """
        Initialized the CycleHealer for a specific file involved in a cycle.
        """
        self.file_path = os.path.abspath(file_path)
        self.project_root = project_root
        self.whitelisted_paths = whitelisted_paths

        if not Path(self.file_path).is_file():
            raise AnalyzerCriticalError(
                f"File not found for CycleHealer: {self.file_path}."
            )

        if not any(
            Path(self.file_path).is_relative_to(wp)
            for wp in map(Path, whitelisted_paths)
        ):
            logger.critical(
                f"CRITICAL: Attempted to heal file '{self.file_path}' which is outside whitelisted paths: {whitelisted_paths}. Aborting operation."
            )
            audit_logger.log_event(
                "security_violation",
                type="path_traversal_attempt",
                file=self.file_path,
                whitelisted_paths=whitelisted_paths,
            )
            alert_operator(
                f"CRITICAL: CycleHealer operating outside whitelisted paths: {self.file_path}. Aborting.",
                level="CRITICAL",
            )
            raise AnalyzerCriticalError(
                f"CycleHealer operating outside whitelisted paths: {self.file_path}."
            )

        if not os.access(self.file_path, os.R_OK):
            raise AnalyzerCriticalError(f"No read access to {self.file_path}.")

        self.cycle = cycle
        self.graph = graph
        self.original_code = ""
        self.tree = None
        self.current_module_name = ""

        logger.debug(f"CycleHealer initialized for file: {self.file_path}")
        _run_async_in_sync(self._parse_ast_and_cache())

    async def _parse_ast_and_cache(self) -> None:
        """
        Parses the AST of the file, using a Redis cache if available.
        """
        cache = await get_cache(project_root=self.project_root)
        file_path_hash = hashlib.sha256(self.file_path.encode()).hexdigest()
        cache_key = f"ast:{file_path_hash}"

        if cache:
            cached_content = await cache.get(cache_key)
            if cached_content:
                self.original_code = cached_content
                self.tree = ast.parse(self.original_code)
                logger.debug(f"Loaded AST from cache for {self.file_path}")
                self.current_module_name = self._get_module_name_from_path(
                    self.file_path
                )
                return

        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                self.original_code = f.read()
            self.tree = await asyncio.to_thread(ast.parse, self.original_code)

            if cache:
                await cache.setex(cache_key, 86400, self.original_code)
                logger.debug(f"Cached AST for {self.file_path}")

        except SyntaxError as e:
            raise AnalyzerCriticalError(
                f"Syntax error in file {self.file_path} during AST parsing for CycleHealer: {e}."
            )
        except Exception as e:
            raise AnalyzerCriticalError(
                f"Unexpected error parsing file {self.file_path} for CycleHealer: {e}."
            )

        self.current_module_name = self._get_module_name_from_path(self.file_path)
        if not self.current_module_name:
            raise NonCriticalError(
                f"Could not determine module name for {self.file_path}. Cycle healing limited."
            )

    def _get_module_name_from_path(self, file_path: str) -> Optional[str]:
        # 1) use graph if present
        if self.graph is not None:
            for node, data in self.graph.nodes(data=True):
                if data.get("path") == file_path:
                    return node
        # 2) derive from project_root
        try:
            pr = Path(self.project_root).resolve()
            fp = Path(file_path).resolve()
            rel = fp.relative_to(pr)
            parts = rel.with_suffix("").parts
            if parts and parts[-1] == "__init__":
                parts = parts[:-1]
            if parts:
                return ".".join(parts)
        except ValueError:  # Path is not within the project root
            pass
        # 3) last resort: match stem to cycle entries
        stem = Path(file_path).stem
        for mod in self.cycle or []:
            if mod.split(".")[-1] == stem:
                return mod
        return None

    async def find_problematic_import(self) -> Optional[Tuple[ast.AST, Set[str]]]:
        """
        Identifies the specific import statement in the current file that contributes to the cycle.
        """
        if self.tree is None:
            await self._parse_ast_and_cache()

        if not self.current_module_name:
            raise NonCriticalError(
                f"Cannot find problematic import: current module name for {self.file_path} is unknown."
            )

        next_mod_name_in_cycle = None
        try:
            current_mod_index = self.cycle.index(self.current_module_name)
            next_mod_name_in_cycle = self.cycle[
                (current_mod_index + 1) % len(self.cycle)
            ]
        except ValueError:
            logger.error(
                f"Current module '{self.current_module_name}' not found in cycle {self.cycle}."
            )
            return None

        logger.debug(
            f"Looking for import from '{self.current_module_name}' to '{next_mod_name_in_cycle}' in {self.file_path}"
        )

        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if (
                        alias.name == next_mod_name_in_cycle
                        or alias.name.split(".")[0]
                        == next_mod_name_in_cycle.split(".")[0]
                    ):
                        imported_names = {a.asname or a.name for a in node.names}
                        logger.debug(
                            f"Found problematic ast.Import: {alias.name} at line {node.lineno}"
                        )
                        return node, imported_names
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                absolute_import_module = node.module
                if node.level > 0:
                    base_parts = self.current_module_name.split(".")
                    source_parts = base_parts[: len(base_parts) - node.level]
                    if node.module:
                        source_parts.append(node.module)
                    absolute_import_module = ".".join(filter(None, source_parts))

                if absolute_import_module == next_mod_name_in_cycle:
                    imported_names = {a.asname or a.name for a in node.names}
                    logger.debug(
                        f"Found problematic ast.ImportFrom: {node.module} at line {node.lineno}"
                    )
                    return node, imported_names
        logger.debug(
            f"No direct problematic import found in {self.file_path} for cycle {self.cycle}."
        )
        return None

    async def heal(self) -> Optional[str]:
        """
        Attempts to heal the circular import in the current file.
        Prioritizes moving the import into a function. If that's not feasible,
        it consults AI for refactoring suggestions.
        """
        if not os.access(self.file_path, os.W_OK):
            raise AnalyzerCriticalError(
                f"No write access to {self.file_path}. Aborting healing."
            )

        audit_logger.log_event(
            "cycle_heal_attempt", file=self.file_path, cycle=scrub_secrets(self.cycle)
        )

        try:
            result = await self.find_problematic_import()
        except NonCriticalError as e:
            logger.warning(str(e))
            result = None

        if result:
            import_to_move, usage_names = result
            transformer = self.MoveImportIntoFunction(import_to_move, usage_names)
            new_tree = await asyncio.to_thread(transformer.visit, self.tree)

            if transformer.modified:
                await asyncio.to_thread(ast.fix_missing_locations, new_tree)
                new_code = await asyncio.to_thread(ast.unparse, new_tree)
                logger.info(
                    f"Cycle fix applied to '{self.file_path}' by moving import into a function."
                )
                audit_logger.log_event(
                    "cycle_heal_mechanical_success",
                    file=self.file_path,
                    cycle=scrub_secrets(self.cycle),
                )
                return new_code
            else:
                logger.info(
                    f"Mechanical import move failed for '{self.file_path}'. Consulting AI for advanced strategies."
                )
        else:
            logger.info(
                f"No direct problematic import found in '{self.file_path}' for mechanical healing. Consulting AI."
            )

        # If mechanical fix failed or not applicable, consult AI
        ai_suggestion = self.extract_interface()
        if (
            ai_suggestion
            and "AI features are unavailable" not in ai_suggestion
            and "failed due to LLM API issue" not in ai_suggestion
        ):
            audit_logger.log_event(
                "cycle_heal_ai_suggestion",
                file=self.file_path,
                cycle=scrub_secrets(self.cycle),
                suggestion=scrub_secrets(ai_suggestion),
            )
            return ai_suggestion

        ai_suggestion = self.split_module()
        if (
            ai_suggestion
            and "AI features are unavailable" not in ai_suggestion
            and "failed due to LLM API issue" not in ai_suggestion
        ):
            audit_logger.log_event(
                "cycle_heal_ai_suggestion",
                file=self.file_path,
                cycle=scrub_secrets(self.cycle),
                suggestion=scrub_secrets(ai_suggestion),
            )
            return ai_suggestion

        logger.warning(
            f"Could not mechanically fix cycle in '{self.file_path}' and no AI suggestion generated."
        )
        audit_logger.log_event(
            "cycle_heal_failure",
            file=self.file_path,
            cycle=scrub_secrets(self.cycle),
            reason="no_mechanical_or_ai_fix",
        )
        return None

    def extract_interface(self) -> Optional[str]:
        """
        Generates an AI suggestion for extracting an interface to break the cycle.
        """
        context = (
            f"Python circular dependency detected: {' -> '.join(self.cycle)}. "
            f"The file '{os.path.basename(self.file_path)}' is involved. "
            f"Suggest a refactoring by extracting an interface/protocol or a common abstraction "
            f"to a new module to break the cycle. Provide Python code snippet if possible."
        )
        try:
            suggestion = get_ai_refactoring_suggestion(context)
            return suggestion
        except Exception as e:
            logger.error(
                f"Error getting AI suggestion for extracting interface: {e}",
                exc_info=True,
            )
            return f"AI features failed: {e}"

    def split_module(self) -> Optional[str]:
        """
        Generates an AI suggestion for splitting a module to break the cycle.
        """
        context = (
            f"Python circular dependency detected: {' -> '.join(self.cycle)}. "
            f"The file '{os.path.basename(self.file_path)}' is involved. "
            f"Suggest splitting the module '{os.path.basename(self.file_path)}' into smaller, more independent modules "
            f"to break the cycle. Identify independent parts and provide a high-level code structure or actionable steps."
        )
        try:
            suggestion = get_ai_refactoring_suggestion(context)
            return suggestion
        except Exception as e:
            logger.error(
                f"Error getting AI suggestion for splitting module: {e}", exc_info=True
            )
            return f"AI features failed: {e}"

    class MoveImportIntoFunction(NodeTransformer):
        """
        AST transformer to move a specific import statement into the first function
        that uses any of the imported names.
        """

        def __init__(self, import_to_move: ast.AST, usage_names: Set[str]):
            self.import_to_move = import_to_move
            self.usage_names = usage_names
            self.modified = False
            self.import_moved = False  # Flag to ensure import is moved only once

        def visit_Import(self, node: ast.Import) -> Optional[ast.AST]:
            if ast.dump(node) == ast.dump(self.import_to_move):
                self.modified = True
                return None  # Remove the import from the top level
            return node

        def visit_ImportFrom(self, node: ast.ImportFrom) -> Optional[ast.AST]:
            if ast.dump(node) == ast.dump(self.import_to_move):
                self.modified = True
                return None  # Remove the import from the top level
            return node

        def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.FunctionDef:
            # First, recursively visit child nodes to handle things like nested functions.
            self.generic_visit(node)

            # After visiting children, check if the import still needs to be moved
            # and if the current function uses any of the imported names.
            if not self.import_moved:
                # Check if any of the imported names are used within this function's body
                # This is a heuristic and might need refinement for complex usage patterns.
                for sub_node in ast.walk(node):
                    if (
                        isinstance(sub_node, ast.Name)
                        and sub_node.id in self.usage_names
                    ):
                        logger.info(
                            f"Moving cycle-causing import into function '{node.name}' in file."
                        )
                        # Insert the import at the top of the function body
                        node.body.insert(0, self.import_to_move)
                        self.modified = True
                        self.import_moved = True  # Set flag to prevent moving it again
                        break  # Stop checking this function once a usage is found

            return node


class DynamicImportHealer:
    """
    Identifies and suggests fixes for dynamic import patterns (e.g., `__import__`, `exec`, `eval`)
    which can be security risks or hinder static analysis.
    """

    def __init__(self, file_path: str, project_root: str, whitelisted_paths: List[str]):
        """
        Prints and logs fixes for dynamic import patterns.

        Args:
            file_path (str): The absolute path to the Python file to analyze.
            project_root (str): The absolute path to the project's root directory.
            whitelisted_paths (List[str]): List of absolute paths to whitelisted directories.
        """
        self.file_path = os.path.abspath(file_path)
        self.project_root = project_root
        self.whitelisted_paths = whitelisted_paths

        if not Path(self.file_path).is_file():
            raise AnalyzerCriticalError(
                f"File not found for DynamicImportHealer: {self.file_path}."
            )

        if not any(
            Path(self.file_path).is_relative_to(wp)
            for wp in map(Path, whitelisted_paths)
        ):
            logger.critical(
                f"CRITICAL: Attempted to analyze file '{self.file_path}' which is outside whitelisted paths: {whitelisted_paths}. Aborting operation."
            )
            audit_logger.log_event(
                "security_violation",
                type="path_traversal_attempt",
                file=self.file_path,
                whitelisted_paths=whitelisted_paths,
            )
            alert_operator(
                f"CRITICAL: DynamicImportHealer operating outside whitelisted paths: {self.file_path}. Aborting.",
                level="CRITICAL",
            )
            raise AnalyzerCriticalError(
                f"DynamicImportHealer operating outside whitelisted paths: {self.file_path}."
            )

        if not os.access(self.file_path, os.R_OK):
            raise AnalyzerCriticalError(f"No read access to {self.file_path}.")

        self.original_code = ""
        self.tree = None

        logger.debug(f"DynamicImportHealer initialized for file: {self.file_path}.")
        _run_async_in_sync(self._parse_ast_and_cache())

    async def _parse_ast_and_cache(self) -> None:
        """
        Parses the AST of the file, using a Redis cache if available.
        """
        cache = await get_cache(project_root=self.project_root)
        file_path_hash = hashlib.sha256(self.file_path.encode()).hexdigest()
        cache_key = f"ast:{file_path_hash}"

        if cache:
            cached_content = await cache.get(cache_key)
            if cached_content:
                self.original_code = cached_content
                self.tree = ast.parse(self.original_code)
                logger.debug(f"Loaded AST from cache for {self.file_path}")
                return

        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                self.original_code = f.read()
            self.tree = await asyncio.to_thread(ast.parse, self.original_code)

            if cache:
                await cache.setex(cache_key, 86400, self.original_code)
                logger.debug(f"Cached AST for {self.file_path}")

        except SyntaxError as e:
            raise AnalyzerCriticalError(
                f"Syntax error in file {self.file_path} during AST parsing for DynamicImportHealer: {e}."
            )
        except Exception as e:
            raise AnalyzerCriticalError(
                f"Unexpected error parsing file {self.file_path} for DynamicImportHealer: {e}."
            )

        self.dynamic_nodes: List[ast.Call] = []
        for node in ast.walk(self.tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in ["__import__", "exec", "eval"]
            ):
                self.dynamic_nodes.append(node)
        logger.debug(
            f"DynamicImportHealer initialized for file: {self.file_path}. Found {len(self.dynamic_nodes)} dynamic import candidates."
        )

    def heal(self) -> List[Tuple[ast.AST, str]]:
        """
        Analyzes the file for dynamic import patterns and generates suggestions.
        Audit Logging: Log every “heal”, import change, and AI suggestion to tamper-evident audit log.
        """
        _run_async_in_sync(self._parse_ast_and_cache())

        audit_logger.log_event("dynamic_import_analysis_start", file=self.file_path)
        fixes = []
        for node in self.dynamic_nodes:
            suggestion = ""
            if node.func.id == "__import__":
                if len(node.args) > 0 and isinstance(
                    node.args[0], (ast.Constant, ast.Str)
                ):
                    mod_name = node.args[0].value
                    suggestion = (
                        f"Consider replacing `__import__('{mod_name}')` with "
                        f"`importlib.import_module('{mod_name}')` or, preferably, "
                        f"a static `import {mod_name}` if the module is known at compile time. "
                        f"This improves readability and static analysis."
                    )
                else:
                    suggestion = (
                        "Dynamic `__import__` with a variable argument. "
                        "This can make code harder to analyze statically and may pose security risks. "
                        "Consider refactoring to use `importlib.import_module` with a controlled set of module names, "
                        "or a static import if possible."
                    )
            elif node.func.id in ["exec", "eval"]:
                suggestion = (
                    f"The use of `{node.func.id}` can execute arbitrary code and is a significant security risk. "
                    f"Consider refactoring to avoid dynamic code execution. "
                    f"For `eval`, use safer alternatives like `ast.literal_eval` if evaluating data structures. "
                    f"For `exec`, consider a templating engine or a more structured approach instead of dynamic code generation."
                )
            fixes.append((node, suggestion))
            audit_logger.log_event(
                "dynamic_import_found",
                file=self.file_path,
                line=node.lineno,
                dynamic_call=node.func.id,
                suggestion=scrub_secrets(suggestion),
            )

        if not fixes:
            audit_logger.log_event(
                "dynamic_import_analysis_complete",
                file=self.file_path,
                status="no_dynamic_imports_found",
            )
        else:
            audit_logger.log_event(
                "dynamic_import_analysis_complete",
                file=self.file_path,
                status="dynamic_imports_found",
                count=len(fixes),
            )

        return fixes


# Example usage (for testing this module independently)
if __name__ == "__main__":
    import shutil

    logging.basicConfig(
        level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s"
    )
    logger.setLevel(logging.DEBUG)  # Set this module's logger to DEBUG

    # Dummy core_utils and core_secrets for testing
    def alert_operator(message: str, level: str = "CRITICAL"):
        print(f"[OPS ALERT - {level}] {message}")

    def scrub_secrets(data: Any) -> Any:
        return data

    class DummyAuditLogger:
        def log_event(self, event_type: str, **kwargs: Any):
            print(f"[AUDIT_LOG] {event_type}: {kwargs}")

    class DummySecretsManager:
        def get_secret(self, key, required=False):
            return "dummy_secret_value"

    class DummyCache:
        def __init__(self):
            self.cache = {}

        async def get(self, key):
            return self.cache.get(key)

        async def setex(self, key, expiry, value):
            self.cache[key] = value

        async def ping(self):
            return True

    async def get_cache(*a, **k):
        return DummyCache()

    # Overwrite imports with dummy implementations
    try:
        from self_healing_import_fixer.import_fixer.compat_core import (
            SECRETS_MANAGER,
            alert_operator,
            audit_logger,
            scrub_secrets,
        )
    except ImportError:
        pass  # The outer try-except block has handled this
    sys.modules["core_utils"] = sys.modules["__main__"]
    sys.modules["core_audit"] = sys.modules["__main__"]
    sys.modules["core_secrets"] = sys.modules["__main__"]
    audit_logger = DummyAuditLogger()
    SECRETS_MANAGER = DummySecretsManager()

    # Clean up any old test files
    if os.path.exists("test_ast_healing_project"):
        shutil.rmtree("test_ast_healing_project")

    # Create a dummy project structure for testing
    test_project_root = "test_ast_healing_project"
    os.makedirs(test_project_root, exist_ok=True)
    os.makedirs(
        os.path.join(test_project_root, "my_package", "sub_module"), exist_ok=True
    )

    # Test file for ImportResolver
    resolver_file = os.path.join(
        test_project_root, "my_package", "sub_module", "analyzer.py"
    )
    with open(resolver_file, "w") as f:
        f.write("from .. import utils\n")
        f.write("from . import helper\n")
        f.write("from ...another_package import feature\n")
        f.write("import os\n")  # Absolute import
        f.write(
            "from my_package.sub_module import config\n"
        )  # Absolute import within same root

    # Test file for CycleHealer
    cycle_file_a = os.path.join(test_project_root, "module_a.py")
    cycle_file_b = os.path.join(test_project_root, "module_b.py")
    with open(cycle_file_a, "w") as f:
        f.write("import module_b\n")
        f.write("def func_a():\n    return module_b.func_b()\n")
    with open(cycle_file_b, "w") as f:
        f.write("import module_a\n")
        f.write("def func_b():\n    return module_a.func_a()\n")

    # Test file for DynamicImportHealer
    dynamic_file = os.path.join(test_project_root, "dynamic_importer.py")
    with open(dynamic_file, "w") as f:
        f.write("mod_name = 'sys'\n")
        f.write("my_sys = __import__(mod_name)\n")
        f.write("exec('print(\"Dynamic exec\")')\n")
        f.write("result = eval('1 + 2')\n")
        f.write("another_mod = __import__('collections.abc', fromlist=['Coroutine'])\n")

    # Test file with syntax error for Exception Handling
    syntax_error_file = os.path.join(test_project_root, "syntax_error_file.py")
    with open(syntax_error_file, "w") as f:
        f.write("def bad_syntax:\n")

    # --- Test ImportResolver ---
    print("\n--- Testing ImportResolver ---")
    current_module_path = "my_package.sub_module.analyzer"
    root_package_names = ["my_package", "another_package"]
    whitelisted_paths = [test_project_root]  # Whitelist the test project root

    with open(resolver_file, "r", encoding="utf-8") as f:
        original_code = f.read()

    resolver = ImportResolver(
        current_module_path, test_project_root, whitelisted_paths, root_package_names
    )
    new_tree = resolver.visit(ast.parse(original_code))

    if resolver.modified:
        new_code = ast.unparse(new_tree)
        print(f"Original:\n{original_code}\n")
        print(f"Modified:\n{new_code}\n")
    else:
        print("No changes by ImportResolver.")

    # Test Input Path Validation (outside whitelisted)
    print("\n--- Testing ImportResolver with Unwhitelisted Path (expecting abort) ---")
    unwhitelisted_file = os.path.abspath("unwhitelisted_file.py")
    with open(unwhitelisted_file, "w") as f:
        f.write("import os\n")
    try:
        # Simulate PRODUCTION_MODE for this test
        original_production_mode = PRODUCTION_MODE
        os.environ["PRODUCTION_MODE"] = "true"

        ImportResolver(
            "unwhitelisted_file",
            os.path.dirname(unwhitelisted_file),
            [test_project_root],
            ["dummy"],
        )
    except AnalyzerCriticalError:
        print("Caught expected AnalyzerCriticalError for unwhitelisted path.")
    except Exception as e:
        print(f"Caught unexpected exception: {e}")
    finally:
        os.environ["PRODUCTION_MODE"] = str(original_production_mode).lower()  # Reset
        if os.path.exists(unwhitelisted_file):
            os.remove(unwhitelisted_file)

    # --- Test CycleHealer ---
    print("\n--- Testing CycleHealer ---")
    # Simulate graph for CycleHealer
    simulated_graph = nx.DiGraph()
    simulated_graph.add_edge("module_a", "module_b")
    simulated_graph.add_edge("module_b", "module_a")
    simulated_graph.add_node("module_a", path=cycle_file_a)
    simulated_graph.add_node("module_b", path=cycle_file_b)

    # Mock AI to return a suggestion
    def mock_get_ai_suggestions(context):
        return ["This is a mocked AI suggestion to break the cycle."]

    def mock_get_ai_patch(problem, code, suggestions):
        return ["This is a mocked AI patch suggestion."]

    import fixer_ai

    fixer_ai.get_ai_suggestions = mock_get_ai_suggestions
    fixer_ai.get_ai_patch = mock_get_ai_patch

    # The original test logic in the provided file has a bug where it doesn't await the inner call.
    # The refactored code fixes this by making the `heal` method async.
    async def run_cycle_healing_test():
        cycle_healer_a = CycleHealer(
            cycle_file_a,
            ["module_a", "module_b"],
            simulated_graph,
            test_project_root,
            whitelisted_paths,
        )
        cycle_healer_b = CycleHealer(
            cycle_file_b,
            ["module_a", "module_b"],
            simulated_graph,
            test_project_root,
            whitelisted_paths,
        )

        print(
            f"Original {os.path.basename(cycle_file_a)}:\n{cycle_healer_a.original_code}\n"
        )
        print(
            f"Original {os.path.basename(cycle_file_b)}:\n{cycle_healer_b.original_code}\n"
        )

        # Attempt to heal module_a
        healed_code_a = await cycle_healer_a.heal()  # Await the async heal method
        if healed_code_a:
            print(f"Healed {os.path.basename(cycle_file_a)}:\n{healed_code_a}\n")
        else:
            print(
                f"Could not heal {os.path.basename(cycle_file_a)} mechanically or with AI suggestion."
            )

    _run_async_in_sync(run_cycle_healing_test())

    # Test Exception Handling (Syntax Error in CycleHealer)
    print("\n--- Testing CycleHealer with Syntax Error (expecting abort) ---")
    try:
        # Simulate PRODUCTION_MODE for this test
        original_production_mode = PRODUCTION_MODE
        os.environ["PRODUCTION_MODE"] = "true"

        CycleHealer(
            syntax_error_file,
            ["syntax_error_file"],
            nx.DiGraph(),
            test_project_root,
            whitelisted_paths,
        )
    except AnalyzerCriticalError:
        print("Caught expected AnalyzerCriticalError for syntax error in CycleHealer.")
    except Exception as e:
        print(f"Caught unexpected exception: {e}")
    finally:
        os.environ["PRODUCTION_MODE"] = str(original_production_mode).lower()  # Reset

    # --- Test DynamicImportHealer ---
    print("\n--- Testing DynamicImportHealer ---")
    dynamic_healer = DynamicImportHealer(
        dynamic_file, test_project_root, whitelisted_paths
    )
    dynamic_fixes = dynamic_healer.heal()
    if dynamic_fixes:
        print(f"Dynamic imports found in {os.path.basename(dynamic_file)}:")
        for node, suggestion in dynamic_fixes:
            print(f"  Line {node.lineno}: {suggestion}")
    else:
        print(f"No dynamic imports found in {os.path.basename(dynamic_file)}.")

    # Test Exception Handling (Syntax Error in DynamicImportHealer)
    print("\n--- Testing DynamicImportHealer with Syntax Error (expecting abort) ---")
    try:
        # Simulate PRODUCTION_MODE for this test
        original_production_mode = PRODUCTION_MODE
        os.environ["PRODUCTION_MODE"] = "true"

        DynamicImportHealer(syntax_error_file, test_project_root, whitelisted_paths)
    except AnalyzerCriticalError:
        print(
            "Caught expected AnalyzerCriticalError for syntax error in DynamicImportHealer."
        )
    except Exception as e:
        print(f"Caught unexpected exception: {e}")
    finally:
        os.environ["PRODUCTION_MODE"] = str(original_production_mode).lower()  # Reset

    # Clean up dummy project
    print("\n--- Cleaning up test project ---")
    shutil.rmtree(test_project_root)
