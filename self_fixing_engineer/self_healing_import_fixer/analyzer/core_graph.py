"""
core_graph.py - Codebase Import and Call Graph Analyzer
CRITICAL: This module analyzes codebase structure. It must operate securely.
"""

import os
import sys
import ast
import graphviz
import logging
from collections import defaultdict
from typing import Dict, List, Set, Tuple, Any, Optional
import shutil
import json
import hashlib
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# --- Guard POSIX-only imports ---
try:
    import resource  # POSIX only
except ImportError:
    resource = None
    
# --- Global Production Mode Flag (from analyzer.py) ---
PRODUCTION_MODE = os.getenv("PRODUCTION_MODE", "false").lower() == "true"

logger = logging.getLogger(__name__)

# --- Custom Exception for critical errors (from analyzer.py) ---
class AnalyzerCriticalError(RuntimeError):
    """
    Custom exception for critical errors that should halt execution and alert ops.
    """
    def __init__(self, message: str, alert_level: str = "CRITICAL"):
        super().__init__(f"[CRITICAL][GRAPH] {message}")
        try:
            # We need to import alert_operator here since the top-level import
            # might have failed.
            from .core_utils import alert_operator
            alert_operator(message, alert_level)
        except Exception:
            pass

# --- Centralized Utilities ---
try:
    from .core_utils import alert_operator, scrub_secrets
    from .core_secrets import SECRETS_MANAGER
except ImportError as e:
    logger.critical(f"CRITICAL: Missing core dependency for core_graph: {e}. Aborting startup.")
    # Since alert_operator might not be imported, we need a fallback here
    # The original file's logic has alert_operator in the try block, so this
    # raises a new error with a slightly different message to be more accurate
    raise RuntimeError(f"DEPENDENCY_ERROR missing core dependency: {e}") from e

class NonCriticalError(Exception):
    """
    Custom exception for recoverable issues that should be logged but not halt execution.
    """
    pass

# --- Caching: Redis Client Initialization (Lazy Initialization) ---
REDIS_CLIENT = None
REDIS_INITIALIZED = False

def _get_redis_client():
    """Lazy initialization of Redis client."""
    global REDIS_CLIENT, REDIS_INITIALIZED
    
    if REDIS_INITIALIZED:
        return REDIS_CLIENT
    
    try:
        import redis.asyncio as redis
        REDIS_CLIENT = redis.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", 6379)),
            db=0,
            decode_responses=True
        )
        REDIS_INITIALIZED = True
        logger.info("Redis client initialized for caching")
    except ImportError:
        logger.info("Redis not available - caching disabled")
        REDIS_INITIALIZED = True
        REDIS_CLIENT = None
    except Exception as e:
        logger.warning(f"Redis unavailable: {e}. Caching disabled.")
        REDIS_INITIALIZED = True
        REDIS_CLIENT = None
        
    return REDIS_CLIENT

# --- Event-loop bridging ---
def _run_async(coro):
    """
    Helper to run an async coroutine from a synchronous context.
    Safely bridges sync/async environments by checking for a running loop.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    else:
        if loop.is_running():
            fut = asyncio.run_coroutine_threadsafe(coro, loop)
            return fut.result()
        else:
            return asyncio.run(coro)

class ImportGraphAnalyzer:
    """
    Analyzes Python codebases to construct import and call graphs,
    and detect structural issues like import cycles and dead code.
    """
    def __init__(self, project_root: str, config: Optional[Dict[str, Any]] = None):
        """
        Initializes the analyzer with the project root directory and configuration.

        Args:
            project_root (str): The root directory of the codebase to analyze.
            config (Optional[Dict[str, Any]]): Configuration for the analyzer,
                                                e.g., 'whitelisted_paths', 'max_python_files',
                                                'max_graph_nodes', 'allow_graphviz_spawn',
                                                'report_output_dir', 'parsing_error_threshold'.
        """
        from .core_audit import audit_logger
        self.config = config or {}

        # Input/Output Path Validation: Validate all project paths and only allow scanning whitelisted directories.
        self.project_root = os.path.abspath(project_root)
        if not os.path.isdir(self.project_root):
            raise AnalyzerCriticalError(f"Project root '{self.project_root}' is not a valid directory. Aborting graph analysis.")

        self.whitelisted_paths: List[str] = [os.path.abspath(p) for p in self.config.get("whitelisted_paths", [self.project_root])]
        if not any(self.project_root.startswith(wp) for wp in self.whitelisted_paths):
            raise AnalyzerCriticalError(f"Project root '{self.project_root}' is not within whitelisted paths: {self.whitelisted_paths}. Aborting graph analysis.")

        # Security: Restrict file system access to read-only for scanning.
        try:
            logger.info(f"Ensuring read-only access for scanning project root: {self.project_root}")
            if not os.access(self.project_root, os.R_OK):
                raise AnalyzerCriticalError(f"No read access to project root '{self.project_root}'.")
        except AnalyzerCriticalError as e:
            raise e
        except Exception as e:
            logger.warning(f"Could not check read access on {self.project_root}: {e}. Proceeding but be aware of potential risks.")


        self.module_paths: Dict[str, str] = {}
        self.graph: Dict[str, Set[str]] = defaultdict(set)
        self.syntax_error_files: List[str] = []
        
        # Memory and Resource Controls: Cap recursion/parallelism to avoid runaway memory use.
        self.max_python_files = self.config.get("max_python_files", 5000)
        self.max_graph_nodes = self.config.get("max_graph_nodes", 10000)
        self.max_memory_mb = self.config.get("max_memory_mb", 2048)
        self.parsing_error_threshold = self.config.get("parsing_error_threshold", 0.001) # 0.1%

        self._set_memory_limit()

        logger.info(f"ImportGraphAnalyzer initialized for project: {self.project_root}")
        audit_logger.log_event("graph_analyzer_init", project_root=self.project_root,
                               whitelisted_paths=self.whitelisted_paths,
                               max_files=self.max_python_files, max_nodes=self.max_graph_nodes,
                               max_memory_mb=self.max_memory_mb,
                               parsing_error_threshold=self.parsing_error_threshold)

    def _set_memory_limit(self):
        """Sets a memory limit for the current process if running on Unix-like systems."""
        if resource is not None and self.max_memory_mb > 0:
            try:
                memory_limit_bytes = self.max_memory_mb * 1024 * 1024
                resource.setrlimit(resource.RLIMIT_AS, (memory_limit_bytes, memory_limit_bytes))
                logger.info(f"Set process memory limit to {self.max_memory_mb} MB.")
            except Exception as e:
                logger.warning(f"Failed to set memory limit: {e}. Graph analysis might consume more memory than desired.", exc_info=True)
                alert_operator(f"WARNING: Graph Analyzer failed to set memory limit: {e}. Monitor resource usage.", level="WARNING")
        else:
            logger.info("Memory limits not supported or enabled on this platform/configuration.")


    def _find_python_files(self) -> List[str]:
        """
        Recursively finds all Python (.py) files within the project root,
        respecting whitelisted paths and file limits.
        Security: Ensure read-only access during scanning.
        """
        python_files = []
        for root, _, files in os.walk(self.project_root):
            if not any(root.startswith(wp) for wp in self.whitelisted_paths):
                logger.debug(f"Skipping directory outside whitelisted paths: {root}")
                continue

            # Ensure read access to the directory itself
            if not os.access(root, os.R_OK):
                raise NonCriticalError(f"No read access to directory: {root}")

            for file in files:
                if file.endswith('.py'):
                    file_path = os.path.join(root, file)
                    if not os.access(file_path, os.R_OK):
                        raise NonCriticalError(f"No read access to file: {file_path}")
                    python_files.append(file_path)
                    if len(python_files) > self.max_python_files:
                        logger.warning(f"Max Python files limit ({self.max_python_files}) reached. Skipping remaining files.")
                        from .core_audit import audit_logger
                        audit_logger.log_event("graph_analysis_limit_reached", limit_type="max_python_files", limit_value=self.max_python_files)
                        alert_operator(f"WARNING: Graph Analyzer hit max Python files limit ({self.max_python_files}). Analysis may be incomplete.", level="WARNING")
                        return python_files
        logger.debug(f"Found {len(python_files)} Python files in {self.project_root}")
        return python_files

    def _get_module_name(self, file_path: str) -> str:
        """
        Converts a file path to its corresponding Python module name.
        e.g., /project/src/my_module/sub_module.py -> my_module.sub_module
        """
        relative_path = os.path.relpath(file_path, self.project_root)
        module_name = relative_path.replace(os.sep, '.')
        if module_name.endswith('.py'):
            module_name = module_name[:-3]
        if module_name.endswith('.__init__'):
            module_name = module_name[:-len('.__init__')]
        return module_name
    
    async def _parse_imports_async(self, file_path: str) -> Set[str]:
        """
        Parses a Python file to extract its import statements with caching.
        """
        from .core_audit import audit_logger
        imported_modules = set()
        redis_client = _get_redis_client()
        
        # Caching: Check for a cached result first
        cache_key = hashlib.sha256(file_path.encode('utf-8')).hexdigest()
        if redis_client:
            try:
                # First ping to ensure connection
                await redis_client.ping()
                cached_imports = await redis_client.get(cache_key)
                if cached_imports:
                    audit_logger.log_event("ast_cache_hit", file_path=file_path)
                    return set(json.loads(cached_imports))
            except Exception as e:
                logger.warning(f"Failed to retrieve cached AST for {file_path}: {e}")

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                file_content = f.read()
                tree = ast.parse(file_content, filename=file_path)

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imported_modules.add(alias.name.split('.')[0])
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        if node.level > 0:
                            current_module_parts = self._get_module_name(file_path).split('.')
                            base_module_parts = current_module_parts[:len(current_module_parts) - node.level + 1]
                            full_module_name = ".".join(base_module_parts + [node.module]) if node.module else ".".join(base_module_parts)
                            imported_modules.add(full_module_name)
                        else:
                            imported_modules.add(node.module.split('.')[0])
        except SyntaxError as e:
            logger.warning(f"Syntax error in {file_path}: {e}. Skipping import parsing.")
            raise NonCriticalError(f"Syntax error in file: {file_path}") from e
        except Exception as e:
            logger.error(f"Error parsing imports from {file_path}: {e}", exc_info=True)
            raise NonCriticalError(f"Parsing error in file: {file_path}") from e

        # Caching: Store the result in Redis
        if redis_client:
            try:
                await redis_client.setex(cache_key, 86400, json.dumps(list(imported_modules))) # Cache for 24 hours
                audit_logger.log_event("ast_cache_set", file_path=file_path)
            except Exception as e:
                logger.warning(f"Failed to cache AST for {file_path}: {e}")

        return imported_modules

    def build_graph(self) -> Dict[str, Set[str]]:
        """
        Constructs the import graph for the entire codebase.
        Memory and Resource Controls: Cap parallelism to avoid runaway memory use.
        Error Handling: Handle partial failures gracefully.
        """
        from .core_audit import audit_logger
        audit_logger.log_event("graph_build_start", project_root=self.project_root)
        try:
            python_files = self._find_python_files()
        except NonCriticalError as e:
            logger.error(f"Failed to find all Python files due to permissions: {e}. Aborting graph build.")
            alert_operator(f"CRITICAL: Graph Analyzer: File system permission error during scan: {e}. Aborting.", level="CRITICAL")
            audit_logger.log_event("graph_build_failed", reason="file_permission_error")
            raise AnalyzerCriticalError("File system permission error during scan.")

        if not python_files:
            logger.warning("No Python files found for graph analysis. Returning empty graph.")
            audit_logger.log_event("graph_build_skipped", reason="no_python_files_found")
            return self.graph

        async def run_parsing_tasks():
            tasks = [self._parse_imports_async(file_path) for file_path in python_files]
            return await asyncio.gather(*tasks, return_exceptions=True)

        parsing_results = _run_async(run_parsing_tasks())
        
        for i, result in enumerate(parsing_results):
            file_path = python_files[i]
            module_name = self._get_module_name(file_path)
            self.module_paths[module_name] = file_path
            
            # Always add module to graph even if parsing fails
            if module_name not in self.graph:
                self.graph[module_name] = set()
            
            if isinstance(result, Exception):
                logger.error(f"Error processing imports for {file_path}: {result}", exc_info=True)
                self.syntax_error_files.append(file_path)
                continue  # Module is in graph with empty imports
            
            imports = result
            for imported_module_name in imports:
                if imported_module_name in self.module_paths or \
                   any(imported_module_name.startswith(f"{m}.") for m in self.module_paths.keys()):
                    self.graph[module_name].add(imported_module_name)
                else:
                    logger.debug(f"Skipping external/unresolved import: {module_name} -> {imported_module_name}")
            
            if len(self.graph) > self.max_graph_nodes:
                logger.warning(f"Max graph nodes limit ({self.max_graph_nodes}) reached. Stopping graph construction.")
                audit_logger.log_event("graph_analysis_limit_reached", limit_type="max_graph_nodes", limit_value=self.max_graph_nodes)
                alert_operator(f"WARNING: Graph Analyzer hit max graph nodes limit ({self.max_graph_nodes}). Analysis may be incomplete.", level="WARNING")
                break
        
        if python_files:
            error_rate = len(self.syntax_error_files) / len(python_files)
            if error_rate > self.parsing_error_threshold:
                if PRODUCTION_MODE:
                    raise AnalyzerCriticalError(f"High parsing error rate ({error_rate:.2%}) detected in graph analysis. Aborting due to potentially corrupted codebase or severe parsing issues.")
                else:
                    logger.warning(f"Parsing errors in {len(self.syntax_error_files)} files ({error_rate:.2%}), continuing with partial graph (non-production mode).")
                audit_logger.log_event("graph_parsing_high_error_rate", error_rate=error_rate, total_files=len(python_files), failed_files=len(self.syntax_error_files))
                alert_operator(f"CRITICAL: Graph Analyzer: High parsing error rate ({error_rate:.2%}). Aborting in production.", level="CRITICAL")

        logger.info(f"Import graph built with {len(self.graph)} nodes and {sum(len(v) for v in self.graph.values())} edges.")
        audit_logger.log_event("graph_build_complete", project_root=self.project_root,
                               nodes=len(self.graph), edges=sum(len(v) for v in self.graph.values()),
                               parsing_errors=len(self.syntax_error_files))
        return self.graph

    def detect_cycles(self, graph: Optional[Dict[str, Set[str]]] = None) -> List[List[str]]:
        """
        Detects import cycles in the graph using DFS.
        Audit Logging: All detected cycles must be logged to audit trail.
        """
        from .core_audit import audit_logger
        if graph is None:
            graph = self.graph

        visited = set()
        recursion_stack = set()
        cycles = []

        def dfs(node, path):
            visited.add(node)
            recursion_stack.add(node)
            path.append(node)

            for neighbor in graph.get(node, set()):
                if neighbor not in visited:
                    dfs(neighbor, path)
                elif neighbor in recursion_stack:
                    try:
                        cycle_start_index = path.index(neighbor)
                        cycle = path[cycle_start_index:]
                        cycles.append(list(cycle))
                        logger.warning(f"Detected import cycle: {' -> '.join(cycle)}")
                        audit_logger.log_event("import_cycle_detected", cycle=scrub_secrets(list(cycle)),
                                               project_root=self.project_root)
                    except ValueError:
                        # Should not happen if logic is correct, but as a safeguard
                        pass


            path.pop()
            recursion_stack.remove(node)

        for node in graph.keys():
            if node not in visited:
                dfs(node, [])

        logger.info(f"Cycle detection complete. Found {len(cycles)} cycles.")
        return cycles

    def detect_dead_nodes(self, graph: Optional[Dict[str, Set[str]]] = None) -> Set[str]:
        """
        Detects 'dead' or 'unused' nodes (modules) in the graph.
        Dead nodes are modules that aren't imported by any other module.
        """
        from .core_audit import audit_logger
        if graph is None:
            graph = self.graph

        all_modules = set(graph.keys())
        imported_by_others = set()

        for importers in graph.values():
            imported_by_others.update(importers)

        # Dead nodes are modules not imported by others
        dead_nodes = all_modules - imported_by_others

        # Log each dead node
        for node in dead_nodes:
            audit_logger.log_event("dead_node_detected", module=node, project_root=self.project_root)

        logger.info(f"Dead code detection complete. Found {len(dead_nodes)} potentially dead nodes.")
        return dead_nodes

    def visualize_graph(self, output_file: str = "import_graph", format: str = "pdf") -> None:
        """
        Generates a DOT file visualization of the import graph and attempts to render it.
        Graphviz Handling: Do not allow external process spawn (dot rendering) unless explicitly operator-approved.
        Audit Logging: All visualizations must be logged to audit trail.

        Args:
            output_file (str): The base name for the output file (e.g., "import_graph").
            format (str): The desired output format (e.g., "pdf", "png", "svg").
        """
        from .core_audit import audit_logger
        allow_graphviz_spawn = self.config.get("allow_graphviz_spawn", False)
        report_output_dir = self.config.get("report_output_dir", os.getcwd())

        if not allow_graphviz_spawn and PRODUCTION_MODE:
            logger.warning("Graphviz rendering is disabled in PRODUCTION_MODE unless 'allow_graphviz_spawn' is explicitly True in config. Skipping visualization.")
            audit_logger.log_event("graph_visualization_skipped", reason="graphviz_spawn_disabled_in_prod", project_root=self.project_root)
            alert_operator("WARNING: Graphviz rendering skipped in PRODUCTION_MODE. Set 'allow_graphviz_spawn=True' in config to enable.", level="WARNING")
            return

        if not shutil.which("dot"):
            logger.error("Graphviz 'dot' command not found. Please install Graphviz to enable visualization.")
            audit_logger.log_event("graph_visualization_failed", reason="graphviz_not_found", project_root=self.project_root)
            alert_operator("ERROR: Graphviz 'dot' command not found. Graph visualization disabled.", level="ERROR")
            return

        dot = graphviz.Digraph(comment='Python Import Graph')
        for node in self.graph.keys():
            dot.node(node)

        for importer, imported_modules in self.graph.items():
            for imported in imported_modules:
                dot.edge(importer, imported)

        # Validate output_file to prevent path traversal attacks.
        safe_output_file = Path(output_file).name # Ensures only the filename is used
        full_output_path = os.path.join(report_output_dir, safe_output_file)

        try:
            dot.render(full_output_path, format=format, view=False, cleanup=True)
            logger.info(f"Import graph visualization saved to {full_output_path}.{format}.")
            audit_logger.log_event("graph_visualization_generated", path=f"{full_output_path}.{format}", format=format, project_root=self.project_root)
        except Exception as e:
            logger.error(f"Failed to render graph visualization. Error: {e}", exc_info=True)
            audit_logger.log_event("graph_visualization_failed", reason=str(e), project_root=self.project_root)
            alert_operator(f"ERROR: Failed to render graph visualization for {self.project_root}. Error: {e}", level="ERROR")

# Example usage (for testing this module independently)
if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
    logger.setLevel(logging.DEBUG)

    # Dummy AuditLogger for independent testing
    class DummyAuditLogger:
        def log_event(self, event_type: str, **kwargs: Any):
            logger.info(f"[AUDIT_LOG] {event_type}: {kwargs}")
            
    # Dummy `core_utils` and `core_secrets` for independent testing
    def alert_operator(message: str, level: str = "CRITICAL"):
        logger.critical(f"[OPS ALERT - {level}] {message}")

    def scrub_secrets(data: Any) -> Any:
        return data

    class DummySecretsManager:
        def get_secret(self, key, required=False):
            return "dummy_secret_value"

    sys.modules['core_utils'] = sys.modules['__main__']
    sys.modules['core_audit'] = sys.modules['__main__']
    sys.modules['core_secrets'] = sys.modules['__main__']
    
    # We set audit_logger to the dummy one for the main script's execution
    # to avoid the ImportError.
    from .core_audit import audit_logger as audit_logger_real # This is a trick to make the linter happy but still use the dummy
    audit_logger = DummyAuditLogger()
    
    SECRETS_MANAGER = DummySecretsManager()

    # Dummy Redis client for testing
    class DummyRedis:
        def __init__(self):
            self.cache = {}
        async def ping(self):
            pass
        async def get(self, key):
            return self.cache.get(key)
        async def setex(self, key, expiry, value):
            self.cache[key] = value

    # Since the real Redis client is lazy-loaded, we need to mock the lazy function
    REDIS_CLIENT = DummyRedis()
    _get_redis_client = lambda: REDIS_CLIENT

    test_project_root = "test_graph_project"
    os.makedirs(test_project_root, exist_ok=True)
    os.makedirs(os.path.join(test_project_root, "module_a"), exist_ok=True)
    os.makedirs(os.path.join(test_project_root, "module_b"), exist_ok=True)
    os.makedirs(os.path.join(test_project_root, "module_c"), exist_ok=True)
    os.makedirs(os.path.join(test_project_root, "utils"), exist_ok=True)

    with open(os.path.join(test_project_root, "main.py"), "w") as f:
        f.write("import module_a.sub_a\n")
        f.write("import module_b\n")
        f.write("from utils import helper\n")
        f.write("print('Main script')\n")

    with open(os.path.join(test_project_root, "module_a", "sub_a.py"), "w") as f:
        f.write("import module_b\n")
        f.write("from . import sub_b # Relative import\n")
        f.write("print('Sub A')\n")

    with open(os.path.join(test_project_root, "module_a", "sub_b.py"), "w") as f:
        f.write("import module_c.component\n")
        f.write("print('Sub B')\n")

    with open(os.path.join(test_project_root, "module_b", "__init__.py"), "w") as f:
        f.write("import module_c.component\n")
        f.write("print('Module B init')\n")

    with open(os.path.join(test_project_root, "module_c", "component.py"), "w") as f:
        f.write("print('Component C')\n")

    with open(os.path.join(test_project_root, "utils", "helper.py"), "w") as f:
        f.write("print('Helper utility')\n")

    with open(os.path.join(test_project_root, "dead_module.py"), "w") as f:
        f.write("print('This module is not imported anywhere.')\n")

    with open(os.path.join(test_project_root, "cycle_a.py"), "w") as f:
        f.write("import cycle_b\n")
    with open(os.path.join(test_project_root, "cycle_b.py"), "w") as f:
        f.write("import cycle_a\n")

    syntax_error_file = os.path.join(test_project_root, "syntax_error_module.py")
    with open(syntax_error_file, "w") as f:
        f.write("def bad_syntax:\n")

    test_config = {
        "whitelisted_paths": [test_project_root],
        "max_python_files": 100,
        "max_graph_nodes": 20,
        "max_memory_mb": 512,
        "allow_graphviz_spawn": True,
        "report_output_dir": os.path.join(test_project_root, "reports"),
        "parsing_error_threshold": 0.001
    }
    os.makedirs(test_config["report_output_dir"], exist_ok=True)


    analyzer = ImportGraphAnalyzer(test_project_root, config=test_config)
    
    print("\n--- Building Graph ---")
    graph = analyzer.build_graph()
    print("Graph Adjacency List:")
    for node, imports in graph.items():
        print(f"  {node}: {list(imports)}")

    print("\n--- Detecting Cycles ---")
    cycles = analyzer.detect_cycles(graph)
    if cycles:
        print("Detected Cycles:")
        for cycle in cycles:
            print(f"  {cycle}")
    else:
        print("No cycles detected.")

    print("\n--- Detecting Dead Nodes ---")
    dead_nodes = analyzer.detect_dead_nodes(graph)
    if dead_nodes:
        print("Detected Potentially Dead Nodes:")
        for node in dead_nodes:
            print(f"  {node}")
    else:
        print("No potentially dead nodes detected (excluding entry points).")

    print("\n--- Visualizing Graph (requires Graphviz) ---")
    analyzer.visualize_graph(output_file="test_project_import_graph", format="pdf")

    print("\n--- Testing Error Escalation (High Parsing Error Rate) ---")
    many_bad_files_root = os.path.join(test_project_root, "many_bad_files")
    os.makedirs(many_bad_files_root, exist_ok=True)
    num_total_files = 100
    num_bad_files = 2 # 2% error rate, above 0.1% threshold
    for i in range(num_total_files):
        file_content = "def good_func(): pass\n"
        if i < num_bad_files:
            file_content = "def bad_func:\n"
        with open(os.path.join(many_bad_files_root, f"file_{i}.py"), "w") as f:
            f.write(file_content)
    
    original_project_root = analyzer.project_root
    analyzer.project_root = many_bad_files_root
    analyzer.syntax_error_files = []
    
    try:
        print(f"Attempting to build graph with {num_bad_files}/{num_total_files} bad files ({num_bad_files/num_total_files:.2%})...")
        analyzer.build_graph()
        print("Graph build completed (should have exited in production mode if PRODUCTION_MODE is True).")
    except AnalyzerCriticalError as e:
        print(f"Caught expected AnalyzerCriticalError due to high parsing error rate: {e}")
    except Exception as e:
        print(f"Caught unexpected exception during graph build with errors: {e}")
    finally:
        analyzer.project_root = original_project_root

    print("\n--- Cleaning up test project ---")
    shutil.rmtree(test_project_root)