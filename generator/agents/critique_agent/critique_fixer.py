import asyncio
import difflib
import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, Any, List, Callable, Optional, Tuple
import ast
# NOTE: esprima will only be available if installed. If not, the fix logic will raise a controlled exception.
try:
    import esprima
except ImportError:
    esprima = None

import git
from git import Actor
from filelock import FileLock, Timeout
from prometheus_client import Counter, Gauge, Histogram
from opentelemetry import trace

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

# --- RUNNER UTILITY IMPORTS (ENFORCED) ---
TESTING = os.getenv("TESTING") == "1"

try:
    # Renaming log_audit_event to log_action for consistency with other modules
    from runner.runner_logging import log_audit_event as log_action
    from runner.llm_client import call_llm_api
    from runner.runner_security_utils import scan_for_vulnerabilities, scrub_pii_and_secrets
    from runner.summarize_utils import check_owasp_compliance # Assuming this is a shared utility location
    
    # Assuming this is the canonical path provided by the user for the test runner sandbox
    from runner.runner_core import run_tests as run_tests_in_sandbox 
    
    # Placeholder for configuration and mock external tools (these should ideally be passed in or removed)
    # NOTE: The CritiqueConfig and get_plugin are remnants of a V0 system. Removing local defs.
    class CritiqueConfig: # Retaining minimal stub to prevent NameError in run_tests_in_sandbox call
        pass
    def get_plugin(*args): return None # Keeping a stub for possible external use
    # Removed LanguageCritiquePlugin and save_files_to_output local stubs as they are V0 architecture
    
except ImportError as e:
    if not TESTING:
        # Hard fail in non-testing environments if critical runner dependencies are missing
        raise ImportError(f"Missing critical runner dependency: {e}") from e
    
    # Define explicit, minimal test doubles ONLY for the testing path
    def log_action(*args, **kwargs): logging.warning("Audit logging disabled (TESTING).")
    async def call_llm_api(*args, **kwargs): return {"content": "Mocked LLM fix response", "fixed_code": args[0]}
    async def scan_for_vulnerabilities(*args, **kwargs): return {"vulnerabilities": []}
    async def scrub_pii_and_secrets(text): return text
    async def run_tests_in_sandbox(*args, **kwargs): return {"pass_rate": 1.0}
    def check_owasp_compliance(*args): return []
    class CritiqueConfig: pass
    def get_plugin(*args): return None


# Prometheus Metrics
FIX_SUCCESS = Counter('fix_success_total', 'Successful fixes applied', ['strategy'])
FIX_FAILURE = Counter('fix_failure_total', 'Failed fixes', ['strategy', 'reason'])
FIX_LATENCY = Histogram('fix_latency_seconds', 'Fix application latency', ['strategy'])

# Constants
FIX_HISTORY_DIR = Path('fix_history')
os.makedirs(FIX_HISTORY_DIR, exist_ok=True)

# --- Pluggable Fix Strategies ---
class FixStrategy(ABC):
    @abstractmethod
    async def apply_fix(self, code: str, fix_data: Any, lang: str) -> str:
        """Applies a fix to the code string and returns the potentially modified code."""
        pass

class DiffPatchStrategy(FixStrategy):
    async def apply_fix(self, code: str, fix_data: str, lang: str) -> str:
        with tracer.start_as_current_span("diff_patch_fix", attributes={"language": lang}):
            if not isinstance(fix_data, str) or not fix_data.strip().startswith('---'):
                logger.warning("Fix data not in unified diff format.")
                return code
            
            # Use a temporary file for the 'patch' command input/output
            with tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix=f'.{lang}') as original_file:
                original_file.write(code)
                original_filepath = original_file.name
            try:
                # Ensure 'patch' command is available (basic capability check)
                if not shutil.which('patch'):
                    logger.error("`patch` command not found.")
                    FIX_FAILURE.labels('diff', 'tool_not_found').inc()
                    return code
                
                proc = await asyncio.create_subprocess_exec(
                    'patch', '-p0', original_filepath, 
                    stdin=asyncio.subprocess.PIPE, 
                    stdout=asyncio.subprocess.PIPE, 
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await proc.communicate(input=fix_data.encode('utf-8'))
                if proc.returncode != 0:
                    logger.error(f"Patch failed (RC={proc.returncode}): {stderr.decode().strip()}")
                    FIX_FAILURE.labels('diff', 'patch_error').inc()
                    return code
                
                with open(original_filepath, 'r') as f:
                    return f.read()
            except Exception as e:
                logger.error(f"Unexpected error in DiffPatch: {e}", exc_info=True)
                FIX_FAILURE.labels('diff', 'unexpected').inc()
                return code
            finally:
                if os.path.exists(original_filepath):
                    os.remove(original_filepath)

class RegexStrategy(FixStrategy):
    async def apply_fix(self, code: str, fix_data: Dict[str, str], lang: str) -> str:
        with tracer.start_as_current_span("regex_fix", attributes={"language": lang}):
            if not isinstance(fix_data, dict):
                logger.warning(f"Regex fix_data not a dict: {type(fix_data)}.")
                FIX_FAILURE.labels('regex', 'invalid_format').inc()
                return code
            pattern = fix_data.get('pattern')
            replacement = fix_data.get('replacement')
            if not pattern or replacement is None:
                logger.warning(f"Invalid regex fix: {fix_data}")
                FIX_FAILURE.labels('regex', 'missing_pattern').inc()
                return code
            try:
                return re.sub(pattern, replacement, code)
            except re.error as e:
                logger.error(f"Invalid regex pattern '{pattern}': {e}")
                FIX_FAILURE.labels('regex', 'invalid_pattern').inc()
                return code

class LLMGenerateStrategy(FixStrategy):
    async def apply_fix(self, code: str, fix_details: Dict[str, Any], lang: str) -> str:
        with tracer.start_as_current_span("llm_generate_fix", attributes={"language": lang}):
            start_time = time.monotonic()
            result = code
            try:
                fix_type = fix_details.get('type')
                if not fix_type:
                    logger.warning("LLM fix missing 'type'.")
                    FIX_FAILURE.labels('llm_generate', 'missing_type').inc()
                    return code
                
                # Use AST/parsing if available for targeted, high-confidence fixes
                if lang == 'python':
                    result = await self._apply_fix_python_ast(code, fix_details)
                elif lang == 'javascript' and esprima:
                    result = await self._apply_fix_javascript(code, fix_details)
                else:
                    # Fallback to general LLM call for Go, other languages, or complex fixes
                    result = await self._apply_fix_generic_llm(code, fix_details, lang)

                FIX_SUCCESS.labels('llm_generate').inc()
                return result
            except Exception as e:
                logger.error(f"LLM fix application failed unexpectedly: {e}")
                FIX_FAILURE.labels('llm_generate', 'unexpected_error').inc()
                return code
            finally:
                FIX_LATENCY.labels('llm_generate').observe(time.monotonic() - start_time)

    async def _apply_fix_generic_llm(self, code: str, fix_details: Dict, lang: str) -> str:
        """Generic LLM call for languages without dedicated AST manipulation or for complex issues."""
        prompt = f"You are a code fixer. Apply the required fix to the provided {lang} code. Output *only* the fixed code block, ensuring valid syntax.\n\nFix Type: {fix_details.get('type')}\nDetails: {json.dumps(fix_details)}\n\nOriginal {lang} Code:\n```{lang}\n{code}\n```\n\nFixed {lang} Code:"
        
        response = await call_llm_api(prompt=prompt, model_name='grok-4', lang=lang)
        fixed_code = response.get('fixed_code') or response.get('content') or code
        
        # Simple attempt to extract code block if LLM added markdown
        code_match = re.search(rf"```{lang}\n(.*?)\n```", fixed_code, re.DOTALL)
        if code_match:
            fixed_code = code_match.group(1).strip()
            
        return fixed_code if fixed_code.strip() else code

    async def _apply_fix_python_ast(self, code: str, fix_details: Dict) -> str:
        with tracer.start_as_current_span("apply_fix_python_ast"):
            try:
                # 1. Attempt Concrete AST Manipulation
                ast.parse(code) 
                fix_type = fix_details.get('type')
                
                if fix_type == 'rename_variable':
                    class VariableRenamer(ast.NodeTransformer):
                        def visit_Name(self, node):
                            if node.id == fix_details.get('old_name'):
                                return ast.Name(id=fix_details.get('new_name'), ctx=node.ctx)
                            return node
                    tree = ast.parse(code)
                    new_tree = VariableRenamer().visit(tree)
                    fixed_code = ast.unparse(new_tree)
                    ast.parse(fixed_code) # Validate syntax
                    return fixed_code
                
                # 2. Fallback to LLM
                fixed_code = await self._apply_fix_generic_llm(code, fix_details, 'python')
                
                # 3. Final Validation
                ast.parse(fixed_code)
                return fixed_code
                
            except SyntaxError as e:
                logger.error(f"Python code syntax error during AST processing or LLM fix validation: {e}")
                FIX_FAILURE.labels('llm_generate', 'python_ast_syntax').inc()
                return code
            except Exception as e:
                logger.error(f"Python AST fix failed: {e}")
                FIX_FAILURE.labels('llm_generate', 'python_ast_error').inc()
                return code

    async def _apply_fix_javascript(self, code: str, fix_details: Dict) -> str:
        with tracer.start_as_current_span("apply_fix_javascript"):
            # esprima is guaranteed to be available here due to the check in apply_fix caller
            try:
                esprima.parseScript(code, {'loc': True}) # Ensure original code is parsable
                fix_type = fix_details.get('type')
                
                if fix_type == 'rename_variable':
                    # NOTE: String replace is simplified but needs context-awareness (e.g. within comments vs code)
                    new_code = code.replace(fix_details['old_name'], fix_details['new_name'])
                    esprima.parseScript(new_code, {'loc': True}) # Validate fix
                    return new_code
                
                # Fallback to LLM
                fixed_code = await self._apply_fix_generic_llm(code, fix_details, 'javascript')
                
                # Final Validation
                esprima.parseScript(fixed_code, {'loc': True})
                return fixed_code
            except Exception as e:
                logger.error(f"JavaScript fix failed: {e}")
                FIX_FAILURE.labels('llm_generate', 'javascript_error').inc()
                return code

# Strategy Registry
STRATEGIES: Dict[str, FixStrategy] = {
    'diff': DiffPatchStrategy(),
    'regex': RegexStrategy(),
    'llm_generate': LLMGenerateStrategy()
}

# Concurrency-Safe Fix History
class FixHistory:
    """Stores code history for undo/redo functionality."""
    def __init__(self, file_id: str):
        self.file_id = file_id
        self.history = []
        self.current_index = -1

    def push(self, code: str):
        self.history = self.history[:self.current_index + 1]
        self.history.append(code)
        self.current_index += 1

    def undo(self) -> Optional[str]:
        if self.current_index > 0:
            self.current_index -= 1
            return self.history[self.current_index]
        return None

    def redo(self) -> Optional[str]:
        if self.current_index < len(self.history) - 1:
            self.current_index += 1
            return self.history[self.current_index]
        return None

FIX_HISTORIES: Dict[str, FixHistory] = {}

def get_file_id(file_path: str) -> str:
    # Hash the file path relative to a known root or just the path itself for a unique ID
    return hashlib.sha256(file_path.encode('utf-8')).hexdigest()

async def security_check_fix(code_files: Dict[str, str], lang: str) -> bool:
    with tracer.start_as_current_span("security_check_fix"):
        # 1. Fast, internal OWASP compliance check
        issues = check_owasp_compliance(code_files)
        if issues:
            logger.warning(f"Security check failed (OWASP): {issues}")
            return False
            
        # 2. Comprehensive vulnerability scan via runner utility (Bandit, Semgrep, etc.)
        result = await scan_for_vulnerabilities(code_files)
        vulnerabilities = result.get('vulnerabilities', [])
        if vulnerabilities:
            logger.warning(f"Security vulnerabilities found: {vulnerabilities}")
            return False
        
        return True

async def safety_check_fix(code_files: Dict[str, str], test_files: Dict[str, str], lang: str) -> bool:
    with tracer.start_as_current_span("safety_check_fix"):
        if not test_files:
            logger.info("No test files provided, skipping safety check.")
            return True
            
        # Unified test runner call via runner utility
        # Build payload to match runner_run_tests signature
        temp_dir = Path(tempfile.gettempdir())
        payload = {
            "test_files": test_files,
            "code_files": code_files,
            "output_path": str(temp_dir),
            "config": {
                "language": lang,
                "timeout": 300
            }
        }
        result = await run_tests_in_sandbox(payload)
        
        pass_rate = result.get('pass_rate', 0.0) 
        
        if pass_rate < 1.0:
            logger.warning(f"Safety check failed: Test pass rate {pass_rate * 100:.2f}%")
            return False
        return True

def hitl_review_fixes(fixes: Dict[str, List[Dict]], callback: Optional[Callable] = None) -> Dict[str, List[Dict]]:
    if callback:
        return callback(fixes)
    # Default interactive review (as provided in the original file)
    approved_fixes: Dict[str, List[Dict]] = {}
    print("\n[--- HUMAN-IN-THE-LOOP FIX REVIEW ---]")
    for file, file_fixes in fixes.items():
        for i, fix in enumerate(file_fixes):
            print(f"\n--- Reviewing Fix {i+1}/{len(file_fixes)} for: {file} ---")
            print(f"  - Strategy: {fix.get('strategy', 'N/A')}")
            print(f"  - Rationale: {fix.get('rationale', 'N/A')}")
            print(f"  - Fix Details: {json.dumps(fix.get('fix'), indent=2)}")
            choice = input("Approve this fix? (y/n/skip all): ").strip().lower()
            if choice == 'y':
                approved_fixes.setdefault(file, []).append(fix)
            elif choice == 'skip all':
                return approved_fixes
    return approved_fixes

def _is_safe_relative_path(relative_path: str) -> bool:
    """
    Ensure we only write within the repo, preventing path traversal or absolute paths.
    """
    try:
        rp = Path(relative_path)
        if rp.is_absolute():
            return False
        # Disallow path traversal outside repo
        for part in rp.parts:
            if part in ('..',):
                return False
        return True
    except Exception:
        return False

def commit_fixes_to_git(code_files: Dict[str, str], repo_path: str, message: str):
    """
    Production-grade Git commit:
    - Initializes repo if absent
    - Writes files safely (no path traversal)
    - Stages only changed/new files
    - Handles initial-commit case
    - Commits with author/committer metadata
    - Optionally pushes to 'origin' if CRITIQUE_FIXER_PUSH=1
    """
    repo_root = Path(repo_path).resolve()
    repo_root.mkdir(parents=True, exist_ok=True)

    # Use a temporary directory for the lock file path for robustness
    with tempfile.TemporaryDirectory() as temp_dir:
        lock_path = Path(temp_dir) / ".critique_fixer.lock"
        lock = FileLock(str(lock_path))

        try:
            with lock.acquire(timeout=30):
                try:
                    repo = git.Repo(str(repo_root))
                except git.InvalidGitRepositoryError:
                    logger.warning(f"No valid git repo at '{repo_root}'. Initializing a new repository.")
                    repo = git.Repo.init(str(repo_root))

                # Write files and collect paths to add
                files_to_add: List[str] = []
                for rel_name, content in code_files.items():
                    if not isinstance(rel_name, str) or not isinstance(content, str):
                        logger.warning(f"Skipping non-string entry in code_files: {type(rel_name)} -> {type(content)}")
                        continue
                    if not _is_safe_relative_path(rel_name):
                        logger.warning(f"Skipping unsafe path: {rel_name}")
                        continue

                    dest_path = repo_root / rel_name
                    dest_path.parent.mkdir(parents=True, exist_ok=True)
                    
                    existing_text = None
                    if dest_path.exists():
                        try:
                            existing_text = dest_path.read_text(encoding='utf-8', errors='ignore')
                        except Exception:
                            existing_text = None

                    # Write only if content changes (reduces churn)
                    if existing_text != content:
                        try:
                            dest_path.write_text(content, encoding='utf-8')
                        except Exception as e:
                            logger.error(f"Failed writing {dest_path}: {e}", exc_info=True)
                            continue
                    files_to_add.append(str(dest_path))

                if not files_to_add:
                    logger.info("No files to add; skipping commit.")
                    return

                # Stage files
                try:
                    repo.index.add(files_to_add)
                except git.GitCommandError as e:
                    logger.error(f"Git add failed: {getattr(e, 'stderr', e)}", exc_info=True)
                    return

                # Determine if there are any staged changes compared to HEAD
                is_initial = False
                try:
                    _ = repo.head.commit  # Raises in empty repo
                except Exception:
                    is_initial = True

                has_changes = False
                try:
                    if is_initial:
                        # On initial commit, if index has entries, we have changes
                        has_changes = len(repo.index.entries) > 0
                    else:
                        has_changes = bool(repo.index.diff("HEAD"))
                except Exception as e:
                    logger.warning(f"Could not diff against HEAD: {e}. Assuming changes exist.")
                    has_changes = True

                if not has_changes:
                    logger.info("No changes staged for commit.")
                    return

                # Commit metadata
                author = Actor(
                    os.getenv("GIT_AUTHOR_NAME", "critique-fixer"),
                    os.getenv("GIT_AUTHOR_EMAIL", "critique-fixer@noreply.local"),
                )
                committer = Actor(
                    os.getenv("GIT_COMMITTER_NAME", "critique-fixer"),
                    os.getenv("GIT_COMMITTER_EMAIL", "critique-fixer@noreply.local"),
                )

                try:
                    repo.index.commit(message, author=author, committer=committer)
                    logger.info(f"Git commit successful: '{message}'")
                    log_action("Git Commit", {"message": message, "files": files_to_add})
                except git.GitCommandError as e:
                    logger.error(f"Git commit failed: {getattr(e, 'stderr', e)}", exc_info=True)
                    return

                # Optional push (safe/no-op if no 'origin' or credential issues)
                try:
                    if os.getenv("CRITIQUE_FIXER_PUSH", "0") == "1":
                        remotes = {r.name for r in repo.remotes}
                        if "origin" in remotes:
                            logger.info("Pushing commit to 'origin'...")
                            repo.remotes.origin.push()
                        else:
                            logger.info("No 'origin' remote configured; skipping push.")
                except Exception as e:
                    logger.warning(f"Optional push failed (non-fatal): {e}")

        except Timeout:
            logger.error("Timed out acquiring repository lock for commit operation.")
        except Exception as e:
            logger.error(f"Unexpected error in commit_fixes_to_git: {e}", exc_info=True)

async def apply_auto_fixes(
    code_files: Dict[str, str],
    fixes_data: Dict[str, List[Dict[str, Any]]],
    lang: str = 'python',
    test_files: Optional[Dict[str, str]] = None,
    hitl_enabled: bool = False,
    vc_path: Optional[str] = None,
    undo_redo_action: Optional[Tuple[str, str]] = None
) -> Dict[str, str]:
    with tracer.start_as_current_span("apply_auto_fixes", attributes={"language": lang}):
        start_time = time.monotonic()
        
        # Input Validation
        if not isinstance(code_files, dict) or not code_files or not all(isinstance(k, str) and isinstance(v, str) for k, v in code_files.items()):
            FIX_FAILURE.labels('none', 'invalid_input').inc()
            raise ValueError("code_files must be a non-empty dictionary with string keys and values.")
        if fixes_data and (not isinstance(fixes_data, dict) or not all(isinstance(k, str) and isinstance(v, list) for k, v in fixes_data.items())):
            FIX_FAILURE.labels('none', 'invalid_input').inc()
            raise ValueError("fixes_data must be a dictionary with string keys and list values.")
        if test_files and (not isinstance(test_files, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in test_files.items())):
            FIX_FAILURE.labels('none', 'invalid_input').inc()
            raise ValueError("test_files must be a dictionary with string keys and values.")

        updated_code_files = code_files.copy()
        
        # Handle Undo/Redo Action
        if undo_redo_action:
            file_path, action = undo_redo_action
            file_id = get_file_id(file_path)
            history = FIX_HISTORIES.setdefault(file_id, FixHistory(file_id))
            new_code = history.undo() if action == 'undo' else history.redo()
            if new_code:
                updated_code_files[file_path] = new_code
                log_action("Fix History", {"action": action, "file": file_path, "status": "success"})
                logger.info(f"Action '{action}' applied to {file_path}.")
            else:
                log_action("Fix History", {"action": action, "file": file_path, "status": "no_state"})
                logger.warning(f"No state for '{action}' on {file_path}.")
            return updated_code_files
        
        # Apply Fixes
        fixes_to_apply = hitl_review_fixes(fixes_data) if hitl_enabled else fixes_data
        
        async def apply_file_fixes(file_path: str, fixes: List[Dict]) -> Tuple[str, str]:
            original_code = updated_code_files[file_path]
            current_code = original_code
            file_id = get_file_id(file_path)
            history = FIX_HISTORIES.setdefault(file_id, FixHistory(file_id))
            
            # Start a new history thread if we aren't already at the latest state
            # If current_index == -1 (empty) or history.history[current_index] != original_code, 
            # we push the current state to start the fixes.
            if history.current_index == -1 or history.history[history.current_index] != original_code:
                 history.push(current_code)

            for fix_data in fixes:
                strategy_name = fix_data.get('strategy', 'diff')
                start_fix = time.monotonic()
                fixer = STRATEGIES.get(strategy_name)
                
                if not fixer:
                    FIX_FAILURE.labels(strategy_name, 'invalid_strategy').inc()
                    FIX_LATENCY.labels(strategy_name).observe(time.monotonic() - start_fix)
                    log_action("Fix Attempt", {"strategy": strategy_name, "file": file_path, "status": "failed", "reason": "invalid_strategy"})
                    continue
                
                try:
                    # Apply fix logic
                    potential_new_code = await fixer.apply_fix(current_code, fix_data.get('fix'), lang)
                except Exception as e:
                    logger.error(f"Fixer '{strategy_name}' raised: {e}", exc_info=True)
                    FIX_FAILURE.labels(strategy_name, 'exception').inc()
                    FIX_LATENCY.labels(strategy_name).observe(time.monotonic() - start_fix)
                    log_action("Fix Attempt", {"strategy": strategy_name, "file": file_path, "status": "failed", "reason": "exception"})
                    continue

                if potential_new_code == current_code:
                    FIX_LATENCY.labels(strategy_name).observe(time.monotonic() - start_fix)
                    log_action("Fix Attempt", {"strategy": strategy_name, "file": file_path, "status": "skipped", "reason": "no_change"})
                    continue

                # Validation step: Copy/update the file in a temporary set and run checks
                temp_files = updated_code_files.copy()
                temp_files[file_path] = potential_new_code

                is_secure = await security_check_fix(temp_files, lang)
                is_safe = await safety_check_fix(temp_files, test_files, lang) if test_files else True

                if is_secure and is_safe:
                    current_code = potential_new_code
                    history.push(current_code)
                    FIX_SUCCESS.labels(strategy_name).inc()
                    log_action("Fix Attempt", {"strategy": strategy_name, "file": file_path, "status": "success"})
                else:
                    FIX_FAILURE.labels(strategy_name, 'validation_failed').inc()
                    log_action("Fix Attempt", {"strategy": strategy_name, "file": file_path, "status": "failed", "reason": "validation_failed", "security_pass": is_secure, "safety_pass": is_safe})

                FIX_LATENCY.labels(strategy_name).observe(time.monotonic() - start_fix)

            return file_path, current_code
        
        tasks = [apply_file_fixes(file_path, fixes) for file_path, fixes in fixes_to_apply.items() if file_path in updated_code_files]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for item in results:
            if isinstance(item, Exception):
                logger.error("Error applying fixes to a file", exc_info=item)
                continue
            file_path, new_code = item
            if isinstance(new_code, str):
                updated_code_files[file_path] = new_code
        
        # Git Commit (only if configured and changes occurred)
        allow_git_commits = os.getenv("CRITIQUE_FIXER_ALLOW_GIT", "0") == "1"
        if vc_path and allow_git_commits and any(updated_code_files.get(f) != code_files.get(f) for f in updated_code_files.keys()):
            commit_fixes_to_git(updated_code_files, vc_path, f"Automated fixes for {lang}")
        
        log_action("Apply Fixes Finished", {"duration": time.monotonic() - start_time})
        logger.info(f"Fix application completed in {time.monotonic() - start_time:.2f}s")
        return updated_code_files

if __name__ == '__main__':
    import argparse
    import shutil
    parser = argparse.ArgumentParser(description="Apply code fixes")
    parser.add_argument('--code-dir', required=True)
    parser.add_argument('--fixes-file', required=True)
    parser.add_argument('--lang', default='python')
    parser.add_argument('--vc-path', default=None)
    args = parser.parse_args()
    
    # NOTE: This section is for local testing and should not be run in CI/CD without the proper
    # runner environment. The main goal here is to demonstrate the intended usage.
    
    try:
        # Check if python dependencies are present for the local execution path
        if not shutil.which('patch'):
             print("\n[WARNING] 'patch' command not found. DiffPatchStrategy will fail.")
        
        code_files = {}
        for f in os.listdir(args.code_dir):
            p = Path(args.code_dir) / f
            if p.is_file():
                # Read files as relative paths for the dictionary keys
                code_files[f] = p.read_text(encoding='utf-8')

        with open(args.fixes_file, 'r') as f:
            fixes_data = json.load(f)
        
        print("Starting auto-fix process...")
        
        async def run_fix_demo():
            return await apply_auto_fixes(code_files, fixes_data, args.lang, vc_path=args.vc_path, hitl_enabled=True)

        results = asyncio.run(run_fix_demo())
        print("\n--- Final Fixed Files ---")
        for k, v in results.items():
            print(f"File: {k}\nContent Length: {len(v)} bytes")
            print("-" * 20)
        
    except ImportError as e:
        print(f"\n[FATAL ERROR] Could not import runner dependencies. Please run 'pip install -e .' or set up the environment.")
        print(f"Details: {e}")
    except Exception as e:
        logger.error(f"An unexpected error occurred during execution: {e}", exc_info=True)
        print(f"\n[EXECUTION ERROR] {e}")