# plugins/scala_test_runner_plugin.py

import os
import re
import asyncio
import shutil
import logging
import tempfile
import glob
import xml.etree.ElementTree as ET
from typing import Dict, Any, Optional, Callable, List, Tuple

# -----------------------------------------------------------------------------------
# Manifest aligned with PluginManager schema (AST-extracted by the manager)
# -----------------------------------------------------------------------------------
PLUGIN_MANIFEST = {
    "name": "ScalaTestRunnerPlugin",
    "version": "1.1.0",
    "description": "Scala test runner using SBT with coverage (Scoverage) and JUnit report parsing.",
    "author": "Self-Fixing Engineer Team",
    "capabilities": ["scala_test_execution", "scala_coverage_analysis", "sbt_integration"],
    "permissions": ["filesystem_read", "filesystem_write", "process_execution"],
    "dependencies": [],
    "type": "python",
    "entrypoint": "plugin_health",
    "health_check": "plugin_health",
    "api_version": "v1",
    "min_core_version": "0.0.0",
    "max_core_version": "9.9.9",
    "license": "MIT",
    "homepage": "",
    "tags": ["scala", "sbt", "scalatest", "scoverage", "test_runner"],
    "sandbox": {"enabled": False},
    "manifest_version": "2.0"
}

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# -----------------------------------------------------------------------------------
# Configuration (env with sane defaults)
# -----------------------------------------------------------------------------------
SCALA_RUNNER_TIMEOUT_SEC = int(os.getenv("SCALA_RUNNER_TIMEOUT_SEC", "600"))  # 10 minutes default
SCALA_TEMP_COPY_LIMIT_MB = int(os.getenv("SCALA_TEMP_COPY_LIMIT_MB", "20"))   # Max MB to copy for temp project
SCALA_TEMP_COPY_MAX_FILES = int(os.getenv("SCALA_TEMP_COPY_MAX_FILES", "2000"))  # Max files to copy
SCALA_DEFAULT_SCALA_VERSION = os.getenv("SCALA_DEFAULT_SCALA_VERSION", "2.13.12")
SCALATEST_VERSION = os.getenv("SCALATEST_VERSION", "3.2.17")
SCOVERAGE_PLUGIN_VERSION = os.getenv("SCOVERAGE_PLUGIN_VERSION", "2.0.9")
SBT_FLAGS = [
    "-no-colors",
    "-batch",
    "-Dsbt.log.noformat=true",
]

# -----------------------------------------------------------------------------------
# Executable Finder (cross-platform)
# -----------------------------------------------------------------------------------
def _which(cmd: str) -> Optional[str]:
    """Cross-platform executable resolver."""
    return shutil.which(cmd)

async def _get_sbt_version(sbt_path: str) -> Optional[str]:
    """Retrieves SBT version robustly."""
    # Try sbt --version first
    try:
        proc = await asyncio.create_subprocess_exec(
            sbt_path, "--version",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=20)
        out = (stdout or b"").decode() + "\n" + (stderr or b"").decode()
        # Try to find version-like token e.g., 1.9.7
        m = re.search(r"\b(\d+\.\d+\.\d+)\b", out)
        if proc.returncode == 0 and m:
            return m.group(1)
    except Exception:
        pass
    # Fallback: sbt sbtVersion (fast query)
    try:
        proc = await asyncio.create_subprocess_exec(
            sbt_path, *SBT_FLAGS, "sbtVersion",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        out = (stdout or b"").decode() + "\n" + (stderr or b"").decode()
        # Typical output contains "sbtVersion: 1.9.7" or just the version on its own line
        m = re.search(r"\b(\d+\.\d+\.\d+)\b", out)
        if proc.returncode == 0 and m:
            return m.group(1)
    except Exception:
        pass
    return None

async def plugin_health() -> Dict[str, Any]:
    """
    Health check: verifies Java and SBT availability and basic versions.
    Returns: {"status": "ok"|"degraded"|"error", "details": [str]}
    """
    status = "ok"
    details: List[str] = []

    # Java
    java_path = _which("java")
    if not java_path:
        status = "degraded"
        details.append("Java (JVM) not found in PATH. Scala test execution will fail.")
    else:
        try:
            proc = await asyncio.create_subprocess_exec(
                java_path, "-version",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
            java_out = (stderr or b"").decode() or (stdout or b"").decode()
            line = next((ln for ln in java_out.splitlines() if "version" in ln.lower()), "").strip()
            if line:
                details.append(f"Java detected: {line}")
            else:
                details.append("Java detected: version info not clearly found in output.")
        except Exception as e:
            status = "degraded"
            details.append(f"Error running java -version: {e}")

    # SBT
    sbt_path = _which("sbt")
    if not sbt_path:
        status = "degraded"
        details.append("SBT not found in PATH. Scala test execution will fail.")
    else:
        details.append(f"SBT detected: {sbt_path}")
        v = await _get_sbt_version(sbt_path)
        details.append(f"SBT version: {v or 'Unknown'}")

    logger.info(f"Plugin health check: {status}")
    return {"status": status, "details": details}

# -----------------------------------------------------------------------------------
# XML Parsing Helpers (JUnit & Scoverage) with robustness
# -----------------------------------------------------------------------------------
def _parse_junit_xml(xml_path: str) -> Dict[str, Any]:
    """Parses a JUnit XML file, returning summary dict."""
    summary = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    if not os.path.exists(xml_path):
        return summary
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        # Sum across <testsuite> children
        suites = root.findall('testsuite')
        if suites:
            for ts in suites:
                summary["tests"] += int(ts.attrib.get("tests", 0))
                summary["failures"] += int(ts.attrib.get("failures", 0))
                summary["errors"] += int(ts.attrib.get("errors", 0))
                summary["skipped"] += int(ts.attrib.get("skipped", 0))
        # If root itself is testsuite, prefer its attributes
        if root.tag == 'testsuite':
            summary["tests"] = int(root.attrib.get("tests", 0))
            summary["failures"] = int(root.attrib.get("failures", 0))
            summary["errors"] = int(root.attrib.get("errors", 0))
            summary["skipped"] = int(root.attrib.get("skipped", 0))
    except Exception as e:
        logger.warning(f"Error parsing JUnit XML {xml_path}: {e}")
    return summary

def _parse_scoverage_xml(xml_path: str) -> float:
    """
    Parses Scoverage XML to extract overall line/statement coverage percentage (0..100).
    Supports different schema flavors.
    """
    if not os.path.exists(xml_path):
        return 0.0
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()

        # Common cases:
        # 1) Root <scoverage statement-rate="0.85" ...>
        if root.tag.lower().endswith("scoverage"):
            stmt_rate = root.attrib.get("statement-rate")
            if stmt_rate:
                try:
                    v = float(stmt_rate)
                    return v * 100.0 if v <= 1.0 else v
                except Exception:
                    pass

        # 2) <project><measurement type="line" statement-rate="0.85"/></project>
        project_node = root.find(".//project")
        if project_node is not None:
            for m in project_node.findall(".//measurement"):
                if (m.attrib.get("type") or "").lower() in {"line", "statement"}:
                    val = m.attrib.get("statement-rate") or m.attrib.get("line-rate")
                    if val:
                        try:
                            v = float(val)
                            return v * 100.0 if v <= 1.0 else v
                        except Exception:
                            continue
            # Project-level attribute
            for key in ("statement-rate", "line-rate"):
                if key in project_node.attrib:
                    try:
                        v = float(project_node.attrib.get(key, "0"))
                        return v * 100.0 if v <= 1.0 else v
                    except Exception:
                        pass

        # 3) Some variants place rates at the root attributes
        for key in ("statement-rate", "line-rate"):
            if key in root.attrib:
                try:
                    v = float(root.attrib.get(key, "0"))
                    return v * 100.0 if v <= 1.0 else v
                except Exception:
                    pass

        return 0.0
    except Exception as e:
        logger.warning(f"Error parsing Scoverage XML {xml_path}: {e}")
        return 0.0

# -----------------------------------------------------------------------------------
# Helpers for temp project creation and safety
# -----------------------------------------------------------------------------------
def _sanitize_identifier(identifier: str) -> Optional[str]:
    """Allow only safe identifier characters to avoid accidental CLI abuse."""
    if re.fullmatch(r"[A-Za-z0-9_.\-]+", identifier or ""):
        return identifier
    return None

def _create_minimal_build_sbt(scala_version: str, scalatest_version: str) -> str:
    """Generates build.sbt content for a minimal project (dependencies and scalaVersion)."""
    return f"""scalaVersion := "{scala_version}"

libraryDependencies += "org.scalatest" %% "scalatest" % "{scalatest_version}" % Test
"""

def _create_plugins_sbt(scoverage_version: str) -> str:
    """Generates project/plugins.sbt content with Scoverage plugin."""
    return f'addSbtPlugin("org.scoverage" % "sbt-scoverage" % "{scoverage_version}")\n'

def _copy_tree_limited(src_dir: str, dest_dir: str, max_mb: int, max_files: int) -> Tuple[int, int]:
    """Copy .scala files from src_dir to dest_dir up to limits; returns (files_copied, bytes_copied)."""
    bytes_copied = 0
    files_copied = 0
    for root, dirs, files in os.walk(src_dir):
        # Skip hidden dirs to reduce noise
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for fname in files:
            if not fname.endswith(".scala"):
                continue
            src_path = os.path.join(root, fname)
            rel = os.path.relpath(src_path, src_dir)
            dest_path = os.path.join(dest_dir, rel)
            size = 0
            try:
                size = os.path.getsize(src_path)
            except Exception:
                continue
            if files_copied + 1 > max_files or (bytes_copied + size) > (max_mb * 1024 * 1024):
                return files_copied, bytes_copied
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            try:
                shutil.copy2(src_path, dest_path)
                files_copied += 1
                bytes_copied += size
            except Exception:
                continue
    return files_copied, bytes_copied

def _find_scoverage_xml(sbt_project_root: str, override_dir: Optional[str] = None) -> Optional[str]:
    """Find scoverage XML report path dynamically."""
    # If override dir provided, prefer it
    if override_dir:
        candidate = os.path.join(sbt_project_root, override_dir, "scoverage.xml")
        if os.path.exists(candidate):
            return candidate
    # Glob target/scala-*/scoverage-report/scoverage.xml
    pattern = os.path.join(sbt_project_root, "target", "scala-*", "scoverage-report", "scoverage.xml")
    paths = sorted(glob.glob(pattern))
    return paths[-1] if paths else None

# -----------------------------------------------------------------------------------
# PLUGIN FUNCTIONALITY
# -----------------------------------------------------------------------------------
async def run_scala_tests(
    test_file_path: str,  # Relative path to the generated test file
    target_identifier: str,  # E.g., 'com.example.MyClass' or 'src/main/scala/com/example/MyClass.scala'
    project_root: str,
    temp_coverage_report_path_relative: str,
    **kwargs
) -> Dict[str, Any]:
    """
    Executes Scala (SBT) tests and analyzes coverage.
    Returns a dict including success, coverage, reason, raw_log, temp_dirs_used, sbt_version, and test_summary.
    """
    full_test_file_path = os.path.abspath(os.path.join(project_root, test_file_path))
    full_coverage_report_path = os.path.abspath(os.path.join(project_root, temp_coverage_report_path_relative))

    result: Dict[str, Any] = {
        "success": False,
        "coverage_increase_percent": 0.0,
        "reason": "",
        "raw_log": "",
        "temp_dirs_used": [],
        "sbt_version": "N/A",
        "test_summary": {"tests": 0, "failures": 0, "errors": 0, "skipped": 0},
    }

    if not os.path.exists(full_test_file_path):
        error_msg = f"Scala test file not found: {full_test_file_path}"
        logger.error(error_msg)
        result["reason"] = error_msg
        return result

    sbt_path = _which("sbt")
    if not sbt_path:
        error_msg = "SBT not found in PATH. Cannot run Scala tests."
        logger.error(error_msg)
        result["reason"] = error_msg
        return result

    result["sbt_version"] = await _get_sbt_version(sbt_path) or "Unknown"

    # Determine SBT project root by searching upwards for build.sbt
    sbt_project_root = project_root
    search_dir = os.path.dirname(full_test_file_path)
    while True:
        if os.path.exists(os.path.join(search_dir, "build.sbt")):
            sbt_project_root = search_dir
            break
        parent = os.path.dirname(search_dir)
        if parent == search_dir:  # reached filesystem root
            break
        search_dir = parent

    # Setup temp project if build.sbt missing
    temp_sbt_dir_context = None
    if not os.path.exists(os.path.join(sbt_project_root, 'build.sbt')):
        # Create a temp SBT project under project_root/atco_artifacts/<nonce>
        base_artifacts = os.path.join(project_root, "atco_artifacts")
        os.makedirs(base_artifacts, exist_ok=True)
        prefix = f"sbt_run_{os.path.basename(project_root)}_"
        temp_dir_obj = tempfile.TemporaryDirectory(prefix=prefix, dir=base_artifacts)
        temp_sbt_dir = temp_dir_obj.name
        temp_sbt_dir_context = temp_dir_obj
        result["temp_dirs_used"].append(temp_sbt_dir)

        os.makedirs(temp_sbt_dir, exist_ok=True)
        os.makedirs(os.path.join(temp_sbt_dir, "project"), exist_ok=True)
        os.makedirs(os.path.join(temp_sbt_dir, 'src', 'main', 'scala'), exist_ok=True)
        os.makedirs(os.path.join(temp_sbt_dir, 'src', 'test', 'scala'), exist_ok=True)

        scala_version = kwargs.get("scala_version", SCALA_DEFAULT_SCALA_VERSION)
        scalatest_version = kwargs.get("scalatest_version", SCALATEST_VERSION)
        scoverage_version = kwargs.get("scoverage_version", SCOVERAGE_PLUGIN_VERSION)

        # Write build.sbt and project/plugins.sbt
        with open(os.path.join(temp_sbt_dir, "build.sbt"), "w", encoding="utf-8") as f:
            f.write(_create_minimal_build_sbt(scala_version, scalatest_version))
        with open(os.path.join(temp_sbt_dir, "project", "plugins.sbt"), "w", encoding="utf-8") as f:
            f.write(_create_plugins_sbt(scoverage_version))
        logger.debug("Wrote minimal build.sbt and project/plugins.sbt for temp SBT project.")

        # Copy source tree bounded by limits if original target is a .scala file
        if target_identifier.endswith(".scala"):
            # Attempt to copy src/main/scala subtree to ensure dependencies present
            src_main_scala = os.path.join(project_root, "src", "main", "scala")
            if os.path.isdir(src_main_scala):
                copied_files, copied_bytes = _copy_tree_limited(
                    src_main_scala,
                    os.path.join(temp_sbt_dir, "src", "main", "scala"),
                    SCALA_TEMP_COPY_LIMIT_MB,
                    SCALA_TEMP_COPY_MAX_FILES,
                )
                logger.info(f"Copied {copied_files} source files ({copied_bytes/1024/1024:.2f} MB) to temp SBT project (bounded).")
            else:
                # Best effort: copy only the single target file into corresponding package path
                rel_path = target_identifier
                if 'src/main/scala' in target_identifier:
                    rel_path = target_identifier.split('src/main/scala', 1)[1].lstrip(os.sep)
                target_file_in_temp = os.path.join(temp_sbt_dir, 'src', 'main', 'scala', rel_path)
                os.makedirs(os.path.dirname(target_file_in_temp), exist_ok=True)
                full_original_target_path = os.path.abspath(os.path.join(project_root, target_identifier))
                if os.path.exists(full_original_target_path):
                    shutil.copy2(full_original_target_path, target_file_in_temp)
                    logger.info(f"Copied target source file to temp SBT project: {full_original_target_path} -> {target_file_in_temp}")
                else:
                    logger.warning(f"Target source file {full_original_target_path} not found. Coverage might be inaccurate.")

        # Copy the generated test file under src/test/scala with package path preserved
        rel_test = test_file_path
        if 'src/test/scala' in test_file_path:
            rel_test = test_file_path.split('src/test/scala', 1)[1].lstrip(os.sep)
        elif test_file_path.endswith(".scala"):
            rel_test = os.path.basename(test_file_path)
        temp_test_path = os.path.join(temp_sbt_dir, 'src', 'test', 'scala', rel_test)
        os.makedirs(os.path.dirname(temp_test_path), exist_ok=True)
        shutil.copy2(full_test_file_path, temp_test_path)
        logger.info(f"Copied generated test file to temp SBT project: {full_test_file_path} -> {temp_test_path}")

        sbt_project_root = temp_sbt_dir

    # Report directories (configurable)
    junit_report_dir = kwargs.get("junit_report_dir") or os.path.join("target", "test-reports")
    junit_report_dir_abs = os.path.join(sbt_project_root, junit_report_dir)
    scoverage_report_dir_override = kwargs.get("scoverage_report_dir")  # relative path under project root

    # Build SBT command
    sbt_commands: List[str] = []

    # Apply ScalaTest reporters for JUnit XML + HTML (sbt 1.x syntax)
    sbt_commands.append(
        f'set Test / testOptions += Tests.Argument(TestFrameworks.ScalaTest, "-u", "{junit_report_dir}", "-h", "{os.path.join("target", "test-html")}")'
    )

    # Coverage lifecycle
    sbt_commands.extend([
        "clean",
        "coverage",
    ])

    # Optional: limit run to a single test class if identifier is safe and not a path
    safe_identifier = None
    if target_identifier and not target_identifier.endswith(".scala") and not target_identifier.startswith("src/"):
        safe_identifier = _sanitize_identifier(target_identifier)
        if safe_identifier:
            sbt_commands.extend(["testOnly", safe_identifier])
        else:
            logger.warning(f"Skipping testOnly due to unsafe target identifier: {target_identifier}")

    # If not limited, run all tests
    if not safe_identifier:
        sbt_commands.append("test")

    sbt_commands.append("coverageReport")

    # Base command with flags
    cmd: List[str] = [sbt_path, *SBT_FLAGS, *sbt_commands]

    # Extra SBT args
    extra_args = kwargs.get("extra_sbt_args", [])
    if extra_args:
        # Validate no obviously dangerous tokens are present; sbt is not run via shell, but be safe
        for token in extra_args:
            if token in {"--addPluginSbtFile"}:
                logger.warning(f"Potentially unsafe extra arg ignored: {token}")
                continue
            cmd.append(str(token))

    logger.info(f"Running SBT command: {' '.join(cmd)} in {sbt_project_root}")
    stdout_data = ""
    stderr_data = ""

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=sbt_project_root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=kwargs.get("timeout_seconds", SCALA_RUNNER_TIMEOUT_SEC))
        except asyncio.TimeoutError:
            logger.error(f"sbt timed out after {kwargs.get('timeout_seconds', SCALA_RUNNER_TIMEOUT_SEC)}s. Terminating...")
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=10)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            result["reason"] = "SBT execution timed out."
            result["raw_log"] = "TIMEOUT"
            return result

        stdout_data = stdout_bytes.decode(errors='replace')
        stderr_data = stderr_bytes.decode(errors='replace')
        result["raw_log"] = f"SBT STDOUT:\n{stdout_data}\nSBT STDERR:\n{stderr_data}"

        # Parse JUnit XML reports
        test_summary = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
        if os.path.exists(junit_report_dir_abs):
            for fname in os.listdir(junit_report_dir_abs):
                if fname.startswith("TEST-") and fname.endswith(".xml"):
                    summary = _parse_junit_xml(os.path.join(junit_report_dir_abs, fname))
                    for k in test_summary:
                        test_summary[k] += summary.get(k, 0)
        result["test_summary"] = test_summary

        if test_summary["tests"] > 0 and test_summary["failures"] == 0 and test_summary["errors"] == 0:
            result["success"] = True
            result["reason"] = f"SBT tests passed. {test_summary['tests']} tests, {test_summary['skipped']} skipped."
            logger.info(result["reason"])
        else:
            result["success"] = False
            result["reason"] = f"SBT tests finished. {test_summary['failures']} failures, {test_summary['errors']} errors, {test_summary['tests']} total."
            logger.warning(result["reason"])

        # Find Scoverage XML dynamically
        scoverage_xml_report_path = _find_scoverage_xml(sbt_project_root, scoverage_report_dir_override)
        coverage_percent = _parse_scoverage_xml(scoverage_xml_report_path) if scoverage_xml_report_path else 0.0
        result["coverage_increase_percent"] = coverage_percent
        logger.info(f"Scoverage coverage: {coverage_percent:.2f}%")

        # Save copy to requested location
        os.makedirs(os.path.dirname(full_coverage_report_path), exist_ok=True)
        if scoverage_xml_report_path and os.path.exists(scoverage_xml_report_path):
            shutil.copyfile(scoverage_xml_report_path, full_coverage_report_path)
            logger.info(f"Scoverage XML report saved to {full_coverage_report_path}")
        else:
            logger.warning("Scoverage XML report not found.")
            result["reason"] += " Scoverage XML report not found."

    except FileNotFoundError:
        result["reason"] = "SBT not found in PATH. Please ensure Java and SBT are installed."
        logger.error(result["reason"])
    except Exception as e:
        result["reason"] = f"An unexpected error occurred during SBT execution: {e}"
        logger.error(result["reason"], exc_info=True)
    finally:
        if temp_sbt_dir_context:
            temp_sbt_dir_context.cleanup()

    return result

# -----------------------------------------------------------------------------------
# Auto-registration with core system
# -----------------------------------------------------------------------------------
def register_plugin_entrypoints(register_func: Callable):
    """
    Registers this plugin's test runner function with the core simulation system.
    """
    logger.info("Registering ScalaTestRunnerPlugin entrypoints...")
    register_func(
        language_or_framework="scala",
        runner_info={
            "command": ["sbt", "test"],
            "extensions": [".scala"],
            "test_discovery": ["Test", "Spec"],
            "runner_function": run_scala_tests
        }
    )
    register_func(
        language_or_framework="scala_sbt",
        runner_info={
            "command": ["sbt", "test"],
            "extensions": [".scala"],
            "test_discovery": ["Test", "Spec"],
            "runner_function": run_scala_tests
        }
    )

# -----------------------------------------------------------------------------------
# Standalone demo (for local testing)
# -----------------------------------------------------------------------------------
if __name__ == "__main__":
    def _mock_register_test_runner(lang_or_framework: str, runner_info: Dict[str, Any]):
        print(f"Mocked registration for {lang_or_framework}: {runner_info}")

    _mock_runners: Dict[str, Any] = {}
    register_plugin_entrypoints(_mock_register_test_runner)  # sync mock

    print("\n--- Registered Runners ---")
    for lang, info in _mock_runners.items():
        print(f"Language: {lang}")
        print(f"  Command: {info.get('command')}")
        print(f"  Extensions: {info.get('extensions')}")
        print(f"  Runner Function: {info.get('runner_function').__name__}")
        print("-" * 20)

    async def main_test_run():
        print("\n--- Running Mock Scala Test (SBT) ---")
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create a dummy Scala source file (e.g., com.example.app.Calculator.scala)
            dummy_src_pkg = os.path.join("com", "example", "app")
            dummy_src_rel = os.path.join("src", "main", "scala", dummy_src_pkg, "Calculator.scala")
            os.makedirs(os.path.join(temp_dir, os.path.dirname(dummy_src_rel)), exist_ok=True)
            with open(os.path.join(temp_dir, dummy_src_rel), 'w', encoding="utf-8") as f:
                f.write("""
package com.example.app

class Calculator {
  def add(a: Int, b: Int): Int = a + b
}
""")
            # Create a dummy Scala test file
            dummy_test_rel = os.path.join("src", "test", "scala", dummy_src_pkg, "CalculatorSpec.scala")
            os.makedirs(os.path.join(temp_dir, os.path.dirname(dummy_test_rel)), exist_ok=True)
            with open(os.path.join(temp_dir, dummy_test_rel), 'w', encoding="utf-8") as f:
                f.write("""
package com.example.app

import org.scalatest.funsuite.AnyFunSuite
import org.scalatest.matchers.should.Matchers

class CalculatorSpec extends AnyFunSuite with Matchers {
  test("Calculator.add should add two numbers") {
    val calculator = new Calculator()
    calculator.add(1, 2) shouldBe 3
  }
}
""")
            temp_coverage_rel = os.path.join("atco_artifacts", "coverage_reports", "scala_coverage_output.xml")
            os.makedirs(os.path.join(temp_dir, "atco_artifacts", "coverage_reports"), exist_ok=True)

            result = await run_scala_tests(
                test_file_path=dummy_test_rel,
                target_identifier=dummy_src_rel,  # or "com.example.app.Calculator"
                project_root=temp_dir,
                temp_coverage_report_path_relative=temp_coverage_rel,
                extra_sbt_args=[],
            )

            print(f"\nTest Result: {'PASS' if result['success'] else 'FAIL'}")
            print(f"Coverage: {result['coverage_increase_percent']:.2f}%")
            print(f"Reason: {result['reason']}")
            print("Execution Log (partial):\n", (result["raw_log"] or "")[-2000:])
            print(f"Temporary Directories Used: {result['temp_dirs_used']}")
            print(f"SBT Version: {result.get('sbt_version', 'N/A')}")
            print(f"Test Summary: {result.get('test_summary', {})}")

            full_coverage_path = os.path.join(temp_dir, temp_coverage_rel)
            if os.path.exists(full_coverage_path):
                print(f"Coverage report (XML) exists at: {full_coverage_path}")
            else:
                print("Coverage report (XML) was NOT created.")

        print("\n--- Test Run Complete ---")

    asyncio.run(main_test_run())