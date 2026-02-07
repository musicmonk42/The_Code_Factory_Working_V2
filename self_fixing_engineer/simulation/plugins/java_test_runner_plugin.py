# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

import asyncio
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# Security fix: Use defusedxml to prevent XXE attacks
import defusedxml.ElementTree as ET

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

PLUGIN_MANIFEST = {
    "name": "JavaTestRunnerPlugin",
    "version": "1.2.0",  # Further hardened for production readiness
    "description": "Runs Maven-based Java tests with coverage (JaCoCo), parses JUnit/Failsafe reports, supports mvnw, timeouts, robust path handling, and multi-module selection.",
    "author": "Self-Fixing Engineer Team",
    "capabilities": [
        "java_test_execution",
        "java_coverage_analysis",
        "maven_integration",
    ],
    "permissions_required": [
        "filesystem_read",
        "filesystem_write",
        "process_execution",
    ],
    "compatibility": {
        "min_sim_runner_version": "1.0.0",
        "max_sim_runner_version": "2.0.0",
    },
    "entry_points": {
        "run_java_tests": {
            "description": "Executes Java (Maven) tests for a given source/test file.",
            "parameters": [
                "test_file_path",
                "target_identifier",
                "project_root",
                "temp_coverage_report_path_relative",
                "extra_maven_args",
            ],
        }
    },
    "health_check": "plugin_health",
    "api_version": "v1",
    "license": "MIT",
    "homepage": "",
    "tags": ["java", "maven", "junit", "jacoco", "test_runner"],
}

# ---------------- Utilities ----------------


def _shutil_which(cmd: str) -> Optional[str]:
    try:
        from shutil import which

        return which(cmd)
    except Exception:
        return None


async def _which(cmd: str) -> Optional[str]:
    """
    Async wrapper that prefers shutil.which and falls back to system 'which/where'.
    """
    path = _shutil_which(cmd)
    if path:
        return path
    try:
        proc = await asyncio.create_subprocess_exec(
            "which" if os.name != "nt" else "where",
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return stdout.decode().strip().splitlines()[0] if proc.returncode == 0 else None
    except Exception as e:
        logger.debug(f"Error finding executable '{cmd}': {e}")
        return None


async def _get_maven_version(mvn_path: str) -> Optional[str]:
    """Retrieves the Maven version (supports mvnw/mvn)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            mvn_path,
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        out = (stdout or b"").decode() + (stderr or b"").decode()
        if proc.returncode == 0:
            for line in out.splitlines():
                if "Apache Maven" in line:
                    # e.g., "Apache Maven 3.9.6 ..."
                    parts = line.split("Apache Maven ", 1)
                    if len(parts) > 1:
                        return parts[1].split()[0].strip()
        return None
    except Exception as e:
        logger.debug(f"Error getting Maven version: {e}")
        return None


def _hostname() -> str:
    try:
        import socket

        return socket.gethostname()
    except Exception:
        return "unknown-host"


def _cap_text_tail(s: str, max_bytes: int) -> str:
    if max_bytes <= 0:
        return ""
    encoded = s.encode(errors="replace")
    if len(encoded) <= max_bytes:
        return s
    tail = encoded[-max_bytes:]
    return tail.decode(errors="replace")


def _is_path_under(base: Path, child: Path) -> bool:
    try:
        base_res = base.resolve()
        child_res = child.resolve()
        return (
            str(child_res).startswith(str(base_res) + os.sep) or child_res == base_res
        )
    except Exception:
        return False


def _find_nearest_pom(start_at: Path, stop_at: Optional[Path] = None) -> Optional[Path]:
    """
    Find the closest parent directory (including start_at) containing a pom.xml,
    but do not traverse above stop_at (if provided).
    """
    cur = start_at.resolve()
    stop = stop_at.resolve() if stop_at else None
    while True:
        pom = cur / "pom.xml"
        if pom.exists():
            return cur
        if cur.parent == cur:
            break
        if stop is not None and (cur == stop or cur.parent == stop.parent):
            # Do not go above stop boundary
            break
        cur = cur.parent
    return None


def _detect_maven_exec(maven_root: Path) -> Optional[str]:
    """
    Prefer Maven Wrapper (mvnw/mvnw.cmd) if present in maven_root; otherwise use 'mvn' from PATH.
    """
    mvnw = maven_root / ("mvnw.cmd" if os.name == "nt" else "mvnw")
    if mvnw.exists():
        try:
            if os.name != "nt":
                mvnw.chmod(mvnw.stat().st_mode | 0o111)
        except Exception:
            pass
        return str(mvnw)
    return _shutil_which("mvn")


def _junit_patterns_for_target(target_identifier: str) -> Optional[str]:
    """
    Derive Surefire -Dtest patterns for a given target identifier that looks like a FQCN.
    Examples: com.example.MyClass -> MyClassTest, TestMyClass, MyClassIT, MyClassTests, MyClassSpec
    """
    if not target_identifier or target_identifier.endswith(".java"):
        return None
    if "." not in target_identifier:
        # Not a FQCN; cannot safely derive
        return None
    class_name = target_identifier.split(".")[-1].strip()
    if not class_name:
        return None
    patterns = [
        f"{class_name}Test",
        f"Test{class_name}",
        f"{class_name}IT",
        f"{class_name}Tests",
        f"{class_name}Spec",
    ]
    return ",".join(patterns)


def _parse_junit_dir(dir_path: Path) -> Dict[str, int]:
    """
    Aggregate JUnit-compatible XML reports in a directory.
    """
    summary = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    if not dir_path.exists() or not dir_path.is_dir():
        return summary
    for p in dir_path.iterdir():
        if p.is_file() and p.name.startswith("TEST-") and p.suffix.lower() == ".xml":
            sub = _parse_junit_xml(p)
            summary["tests"] += sub["tests"]
            summary["failures"] += sub["failures"]
            summary["errors"] += sub["errors"]
            summary["skipped"] += sub["skipped"]
    return summary


def _parse_junit_xml(xml_path: Any) -> Dict[str, Any]:
    """
    Parses a single JUnit-compatible XML report (Surefire/Failsafe) and extracts metrics.
    """
    summary = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    try:
        # Ensure xml_path is a Path object for consistent handling
        path_obj = Path(xml_path)
        if not path_obj.exists():
            return summary
        tree = ET.parse(str(path_obj))
        root = tree.getroot()
        # Direct root testsuite
        if root.tag == "testsuite":
            summary["tests"] = int(root.attrib.get("tests", 0))
            summary["failures"] = int(root.attrib.get("failures", 0))
            summary["errors"] = int(root.attrib.get("errors", 0))
            summary["skipped"] = int(root.attrib.get("skipped", 0))
            return summary
        # Aggregate testsuites
        for testsuite in root.findall(".//testsuite"):
            summary["tests"] += int(testsuite.attrib.get("tests", 0))
            summary["failures"] += int(testsuite.attrib.get("failures", 0))
            summary["errors"] += int(testsuite.attrib.get("errors", 0))
            summary["skipped"] += int(testsuite.attrib.get("skipped", 0))
    except Exception as e:
        logger.warning(f"Error parsing JUnit XML {xml_path}: {e}")
    return summary


def _parse_jacoco_xml(xml_path: Any) -> float:
    """
    Parses a JaCoCo XML report and extracts the overall line coverage percentage.
    Prefer root-level <counter type="LINE">; if missing, aggregate counters.
    """
    try:
        # Ensure xml_path is a Path object for consistent handling
        path_obj = Path(xml_path)
        if not path_obj.exists():
            return 0.0
        tree = ET.parse(str(path_obj))
        root = tree.getroot()
        # First, try root-level counter(s)
        for c in root.findall("./counter"):
            if c.attrib.get("type") == "LINE":
                missed = float(c.attrib.get("missed", 0))
                covered = float(c.attrib.get("covered", 0))
                total = missed + covered
                return (covered / total) * 100.0 if total > 0 else 0.0
        # Fallback: aggregate over all counters
        missed_sum = 0.0
        covered_sum = 0.0
        for c in root.iter("counter"):
            if c.attrib.get("type") == "LINE":
                missed_sum += float(c.attrib.get("missed", 0))
                covered_sum += float(c.attrib.get("covered", 0))
        total = missed_sum + covered_sum
        return (covered_sum / total) * 100.0 if total > 0 else 0.0
    except Exception as e:
        logger.warning(f"Error parsing JaCoCo XML {xml_path}: {e}")
        return 0.0


def _create_minimal_pom_xml(
    group_id: str, artifact_id: str, version: str, java_release: str = "17"
) -> str:
    """Generates a minimal Maven pom.xml content with Surefire and JaCoCo plugins and configurable Java release."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 https://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>

  <groupId>{group_id}</groupId>
  <artifactId>{artifact_id}</artifactId>
  <version>{version}</version>
  <packaging>jar</packaging>

  <properties>
    <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>
    <maven.compiler.release>{java_release}</maven.compiler.release>
    <junit.version>5.10.0</junit.version>
    <jacoco.version>0.8.11</jacoco.version>
    <maven.surefire.plugin.version>3.2.5</maven.surefire.plugin.version>
  </properties>

  <dependencies>
    <dependency>
      <groupId>org.junit.jupiter</groupId>
      <artifactId>junit-jupiter-api</artifactId>
      <version>${{junit.version}}</version>
      <scope>test</scope>
    </dependency>
    <dependency>
      <groupId>org.junit.jupiter</groupId>
      <artifactId>junit-jupiter-engine</artifactId>
      <version>${{junit.version}}</version>
      <scope>test</scope>
    </dependency>
  </dependencies>

  <build>
    <plugins>
      <plugin>
        <groupId>org.apache.maven.plugins</groupId>
        <artifactId>maven-surefire-plugin</artifactId>
        <version>${{maven.surefire.plugin.version}}</version>
        <configuration>
          <useSystemClassLoader>true</useSystemClassLoader>
          <includes>
            <include>**/*Test.java</include>
            <include>**/*Tests.java</include>
            <include>**/*IT.java</include>
          </includes>
        </configuration>
      </plugin>
      <plugin>
        <groupId>org.jacoco</groupId>
        <artifactId>jacoco-maven-plugin</artifactId>
        <version>${{jacoco.version}}</version>
        <executions>
          <execution>
            <goals>
              <goal>prepare-agent</goal>
            </goals>
          </execution>
          <execution>
            <id>report</id>
            <phase>test</phase>
            <goals>
              <goal>report</goal>
            </goals>
            <configuration>
              <outputDirectory>${{project.build.directory}}/site/jacoco</outputDirectory>
            </configuration>
          </execution>
        </executions>
      </plugin>
    </plugins>
  </build>
</project>
"""


def _java_class_name_to_path(class_name: str) -> str:
    """Converts a Java fully-qualified class name to a file path."""
    return class_name.replace(".", os.sep) + ".java"


def _copytree_compat(src: Path, dst: Path) -> None:
    """
    Copy a directory tree, allowing existing destination on Python < 3.8.
    """
    try:
        shutil.copytree(str(src), str(dst), dirs_exist_ok=True)  # Py 3.8+
    except TypeError:
        # Fallback for Py < 3.8
        if not dst.exists():
            dst.mkdir(parents=True, exist_ok=True)
        for root, dirs, files in os.walk(src):
            rel = Path(root).relative_to(src)
            target_dir = dst / rel
            target_dir.mkdir(parents=True, exist_ok=True)
            for f in files:
                shutil.copy2(str(Path(root) / f), str(target_dir))


def _filter_maven_args(args: List[str], strict: bool) -> List[str]:
    """
    Optionally filter extra Maven args for safety in multi-tenant contexts.
    Allowed prefixes if strict: -D, -P, -pl, -am, -B, -q, -e, -fae, -T, --fail-at-end, --batch-mode, --no-transfer-progress
    """
    if not strict:
        return args
    allowed = {
        "-pl",
        "-am",
        "-B",
        "-q",
        "-e",
        "-fae",
        "-T",
        "--fail-at-end",
        "--batch-mode",
        "--no-transfer-progress",
    }

    def ok(a: str) -> bool:
        return a.startswith("-D") or a.startswith("-P") or a in allowed

    filtered = [a for a in args if ok(a)]
    dropped = [a for a in args if a not in filtered]
    if dropped:
        logger.warning(f"Dropped disallowed Maven args (strict mode): {dropped}")
    return filtered


# ---------------- Health Check ----------------


async def plugin_health() -> Dict[str, Any]:
    """
    Health check for Java/Maven and wrapper presence.
    Tries to detect mvnw relative to JAVA_TEST_RUNNER_PROJECT_ROOT if provided, else cwd.
    """
    status = "ok"
    details: List[str] = []

    # Java (JVM)
    java_path = await _which("java")
    if not java_path:
        status = "degraded"
        details.append("Java (JVM) not found in PATH. Java test execution will fail.")
    else:
        try:
            proc = await asyncio.create_subprocess_exec(
                java_path,
                "-version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out = (await proc.communicate())[1].decode(errors="replace")
            version_line = next(
                (line for line in out.splitlines() if "version" in line.lower()), None
            )
            details.append(
                f"Java detected: {version_line.strip() if version_line else 'version unknown'}"
            )
        except Exception as e:
            status = "degraded"
            details.append(f"Error running java -version: {e}")

    # Maven (mvn or mvnw) — prefer project_root if provided via env
    env_root = os.getenv("JAVA_TEST_RUNNER_PROJECT_ROOT", "")
    base = Path(env_root).resolve() if env_root else Path.cwd()
    mvn_exec = _detect_maven_exec(base) or await _which("mvn")
    if not mvn_exec:
        status = "degraded"
        details.append("Maven not found (mvnw/mvn). Java test execution will fail.")
    else:
        ver = await _get_maven_version(mvn_exec)
        details.append(f"Maven exec: {mvn_exec}")
        details.append(f"Maven version: {ver or 'Unknown'}")

    logger.info(f"JavaTestRunnerPlugin health: {status} | {details}")
    return {"status": status, "details": details}


# ---------------- Main Functionality ----------------


async def run_java_tests(
    test_file_path: str,
    target_identifier: str,
    project_root: str,
    temp_coverage_report_path_relative: str,
    **kwargs,
) -> Dict[str, Any]:
    """
    Executes Java (Maven) tests and analyzes coverage using JaCoCo.

    Args:
        test_file_path: Relative path to the generated Java test file.
        target_identifier: Target FQCN (e.g., com.example.MyClass) or source path.
        project_root: Absolute path to project root on disk.
        temp_coverage_report_path_relative: Relative path within project_root to write JaCoCo XML.
        **kwargs:
            extra_maven_args: list[str] of extra Maven CLI args (filtered if strict).
            timeout_seconds: int (default 900).
            quiet: bool to pass -q (default False).
            batch_mode: bool to pass -B (default True).
            include_integration_tests: bool to run failsafe ITs via 'verify' (default False).
            enable_module_selection: bool to run from aggregator with -pl module (default True).
            log_max_bytes: int cap for returned raw_log (default 262144).
            log_artifact_path_relative: Optional[str] to save full logs under project_root.
            strict_extra_args: bool to enforce whitelist (default True).
            java_release: str Java release for minimal POM fallback (default env JAVA_TEST_RUNNER_JAVA_RELEASE or '17').
            test_patterns: Optional[str] explicit Surefire -Dtest patterns to run.

    Returns:
        Dict[str, Any] with:
            - success (bool)
            - coverage_percent (float)
            - coverage_increase_percent (float; alias of coverage_percent for back-compat)
            - reason (str)
            - raw_log (str, capped)
            - temp_dirs_used (list[str])
            - maven_version (str)
            - maven_project_root (str)
            - used_maven_executable (str)
    """
    project_root_path = Path(project_root).resolve()
    full_test_file_path = (project_root_path / test_file_path).resolve()
    full_coverage_report_path = (
        project_root_path / temp_coverage_report_path_relative
    ).resolve()

    result: Dict[str, Any] = {
        "success": False,
        "coverage_percent": 0.0,
        "coverage_increase_percent": 0.0,  # back-compat alias
        "reason": "",
        "raw_log": "",
        "temp_dirs_used": [],
        "maven_version": "N/A",
        "maven_project_root": "",
        "used_maven_executable": "",
    }

    # Path safety
    if not _is_path_under(project_root_path, full_test_file_path):
        error_msg = (
            f"Invalid test_file_path outside project_root: {full_test_file_path}"
        )
        logger.error(error_msg)
        result["reason"] = error_msg
        return result

    if not full_test_file_path.exists():
        error_msg = f"Java test file not found: {full_test_file_path}"
        logger.error(error_msg)
        result["reason"] = error_msg
        return result

    # Output path safety (coverage)
    if not _is_path_under(project_root_path, full_coverage_report_path):
        error_msg = f"Invalid coverage output path outside project_root: {full_coverage_report_path}"
        logger.error(error_msg)
        result["reason"] = error_msg
        return result

    # Determine Maven project root (nearest pom.xml upwards from test file), bounded by project_root
    nearest_pom_root = _find_nearest_pom(
        full_test_file_path.parent, stop_at=project_root_path
    )
    maven_project_root = nearest_pom_root if nearest_pom_root else project_root_path
    result["maven_project_root"] = str(maven_project_root)

    # Prefer mvnw if present
    mvn_exec = _detect_maven_exec(maven_project_root) or await _which("mvn")
    if not mvn_exec:
        error_msg = "Maven (mvn or mvnw) not found in PATH or project root. Cannot run Java tests."
        logger.error(error_msg)
        result["reason"] = error_msg
        return result

    result["used_maven_executable"] = mvn_exec
    result["maven_version"] = await _get_maven_version(mvn_exec) or "Unknown"

    # Build JaCoCo paths (default locations)
    jacoco_report_dir_default = maven_project_root / "target" / "site" / "jacoco"
    jacoco_xml_report_path_default = jacoco_report_dir_default / "jacoco.xml"

    # Construct Maven command
    timeout_seconds = int(kwargs.get("timeout_seconds", 900))
    quiet = bool(kwargs.get("quiet", False))
    batch_mode = bool(kwargs.get("batch_mode", True))
    include_it = bool(kwargs.get("include_integration_tests", False))
    enable_pl = bool(kwargs.get("enable_module_selection", True))
    log_max_bytes = int(kwargs.get("log_max_bytes", 262144))
    strict_extra_args = bool(kwargs.get("strict_extra_args", True))
    java_release = str(
        kwargs.get("java_release", os.getenv("JAVA_TEST_RUNNER_JAVA_RELEASE", "17"))
    )

    extra_args = kwargs.get("extra_maven_args", []) or []
    extra_args = _filter_maven_args(list(extra_args), strict=strict_extra_args)

    # Test selection
    explicit_patterns = kwargs.get("test_patterns")
    derived_patterns = _junit_patterns_for_target(target_identifier)
    test_filter = explicit_patterns or derived_patterns
    surefire_test_arg = [f"-Dtest={test_filter}"] if test_filter else []

    # Base flags (JUnit 5 is auto-detected by Surefire 3.x; no special flag needed)
    base_flags: List[str] = []
    if batch_mode:
        base_flags.append("-B")
    if quiet:
        base_flags.append("-q")

    # If no pom.xml found, build a temp minimal Maven project and copy sources
    temp_maven_dir_context = None
    used_root_for_build = maven_project_root
    reports_root_for_readback = (
        maven_project_root  # where to read surefire/failsafe and jacoco from
    )
    degraded_note = ""
    module_root: Optional[Path] = None
    aggregator_root: Optional[Path] = None
    module_pl_arg: List[str] = []

    if nearest_pom_root is None or not (maven_project_root / "pom.xml").exists():
        # Degraded fallback
        artifacts_dir = project_root_path / "atco_artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        temp_dir_obj = tempfile.TemporaryDirectory(
            prefix="maven_run_", dir=str(artifacts_dir)
        )
        temp_maven_dir = Path(temp_dir_obj.name)
        temp_maven_dir_context = temp_dir_obj
        result["temp_dirs_used"].append(str(temp_maven_dir))

        # Minimal pom
        pom_xml_content = _create_minimal_pom_xml(
            group_id="com.atco.temp",
            artifact_id="java-temp-test",
            version="1.0.0",
            java_release=java_release,
        )
        (temp_maven_dir / "pom.xml").write_text(pom_xml_content, encoding="utf-8")
        logger.debug(f"Generated pom.xml in {temp_maven_dir}")

        # Create dirs and copy sources
        src_main_java = project_root_path / "src" / "main" / "java"
        src_main_resources = project_root_path / "src" / "main" / "resources"
        (temp_maven_dir / "src" / "main" / "java").mkdir(parents=True, exist_ok=True)
        (temp_maven_dir / "src" / "test" / "java").mkdir(parents=True, exist_ok=True)
        if src_main_java.exists():
            _copytree_compat(src_main_java, temp_maven_dir / "src" / "main" / "java")
        if src_main_resources.exists():
            _copytree_compat(
                src_main_resources, temp_maven_dir / "src" / "main" / "resources"
            )

        # Copy test file into standard test location
        try:
            try_rel = full_test_file_path.relative_to(
                project_root_path / "src" / "test" / "java"
            )
            dest_test_path = temp_maven_dir / "src" / "test" / "java" / try_rel
        except Exception:
            dest_test_path = (
                temp_maven_dir
                / "src"
                / "test"
                / "java"
                / Path(full_test_file_path.name)
            )
        dest_test_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(str(full_test_file_path), str(dest_test_path))
        logger.info(f"Copied test file to temp Maven project: {dest_test_path}")

        used_root_for_build = temp_maven_dir
        reports_root_for_readback = temp_maven_dir
        degraded_note = " No pom.xml detected; used temporary minimal Maven project. Results may be limited."
    else:
        # Multi-module selection: if a parent pom exists above nearest module within project_root, use -pl
        module_root = nearest_pom_root
        # Search for a parent (aggregator) pom above module_root but not above project_root
        parent_search_start = module_root.parent
        aggregator_root = _find_nearest_pom(
            parent_search_start, stop_at=project_root_path
        )
        if enable_pl and aggregator_root and aggregator_root != module_root:
            used_root_for_build = aggregator_root
            # Compute -pl path relative to aggregator
            try:
                rel_module = module_root.relative_to(aggregator_root)
                module_pl_arg = ["-pl", str(rel_module), "-am"]
                reports_root_for_readback = module_root  # reports are under the module
                logger.info(
                    f"Detected aggregator at {aggregator_root}, module at {module_root}; using -pl {rel_module}"
                )
            except Exception:
                # Fallback: build from module root without -pl
                used_root_for_build = module_root
                reports_root_for_readback = module_root
        else:
            used_root_for_build = module_root
            reports_root_for_readback = module_root

    # Build the command:
    # - If include_integration_tests: 'verify' runs unit tests + failsafe ITs
    # - Else: run 'test' only
    goals = [
        "org.jacoco:jacoco-maven-plugin:prepare-agent",
        "verify" if include_it else "test",
        "org.jacoco:jacoco-maven-plugin:report",
    ]
    cmd: List[str] = (
        [mvn_exec] + base_flags + goals + module_pl_arg + surefire_test_arg + extra_args
    )

    logger.info(
        f"[host={_hostname()}] Running Maven command: {' '.join(cmd)} in {used_root_for_build}"
    )
    stdout_data = ""
    stderr_data = ""

    # Run process with timeout, handle cancellation and ensure termination
    proc: Optional[asyncio.subprocess.Process] = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(used_root_for_build),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_seconds
            )
        except asyncio.TimeoutError:
            logger.warning(
                f"Maven process timed out after {timeout_seconds}s; terminating..."
            )
            if proc.returncode is None:
                try:
                    proc.terminate()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(proc.wait(), timeout=10)
                except asyncio.TimeoutError:
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
            stdout_bytes = b""
            stderr_bytes = f"Process timed out after {timeout_seconds}s".encode()
        stdout_data = (stdout_bytes or b"").decode(errors="replace")
        stderr_data = (stderr_bytes or b"").decode(errors="replace")

        full_log = f"Maven STDOUT:\n{stdout_data}\n\nMaven STDERR:\n{stderr_data}"

        # Optionally save full logs (with path safety)
        log_artifact_rel = kwargs.get("log_artifact_path_relative")
        if log_artifact_rel:
            artifact_path = (project_root_path / log_artifact_rel).resolve()
            if _is_path_under(project_root_path, artifact_path):
                artifact_path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    artifact_path.write_text(full_log, encoding="utf-8")
                except Exception as e:
                    logger.warning(
                        f"Failed to write log artifact to {artifact_path}: {e}"
                    )
            else:
                logger.error(
                    f"Invalid log artifact path outside project_root: {artifact_path}"
                )

        # Cap logs in result
        result["raw_log"] = _cap_text_tail(full_log, log_max_bytes)

        # Parse test results from surefire and failsafe (read from reports_root_for_readback)
        surefire_dir = reports_root_for_readback / "target" / "surefire-reports"
        failsafe_dir = reports_root_for_readback / "target" / "failsafe-reports"
        test_summary = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
        for d in (surefire_dir, failsafe_dir):
            sub = _parse_junit_dir(d)
            for k in test_summary:
                test_summary[k] += sub[k]

        if (
            test_summary["tests"] > 0
            and test_summary["failures"] == 0
            and test_summary["errors"] == 0
        ):
            note = " (including integration tests)" if include_it else ""
            result["success"] = True
            result["reason"] = (
                f"Maven tests passed{note}. {test_summary['tests']} tests, {test_summary['skipped']} skipped."
                + degraded_note
            )
            logger.info(result["reason"])
        else:
            note = " (including integration tests)" if include_it else ""
            result["success"] = False
            result["reason"] = (
                f"Maven tests failed{note}. {test_summary['failures']} failures, "
                f"{test_summary['errors']} errors, {test_summary['tests']} total."
            ) + degraded_note
            logger.warning(result["reason"])

        # Parse JaCoCo XML report for coverage (prefer module reports when building with -pl)
        jacoco_xml_candidate = (
            reports_root_for_readback / "target" / "site" / "jacoco" / "jacoco.xml"
        )
        if reports_root_for_readback == maven_project_root:
            jacoco_xml_candidate = jacoco_xml_report_path_default
        coverage_percent = _parse_jacoco_xml(jacoco_xml_candidate)
        result["coverage_percent"] = coverage_percent
        result["coverage_increase_percent"] = coverage_percent  # alias for back-compat
        logger.info(f"JaCoCo line coverage: {coverage_percent:.2f}%")

        # Save JaCoCo XML report to requested path (path already validated above)
        try:
            full_coverage_report_path.parent.mkdir(parents=True, exist_ok=True)
            if jacoco_xml_candidate.exists():
                shutil.copyfile(
                    str(jacoco_xml_candidate), str(full_coverage_report_path)
                )
                logger.info(
                    f"JaCoCo coverage XML report saved to {full_coverage_report_path}"
                )
            else:
                logger.warning(
                    f"JaCoCo XML report not found at {jacoco_xml_candidate}."
                )
                result["reason"] += " JaCoCo XML report not found."
        except Exception as e:
            logger.warning(
                f"Failed to write coverage XML to {full_coverage_report_path}: {e}"
            )
            result["reason"] += f" Failed to write coverage XML: {e}"

    except asyncio.CancelledError:
        # Propagate cancellation; try to terminate process
        if proc and proc.returncode is None:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
        raise
    except FileNotFoundError:
        result["reason"] = (
            "Maven (mvn/mvnw) not found. Please ensure Java and Maven are installed."
        )
        logger.error(result["reason"])
    except Exception as e:
        result["reason"] = f"An unexpected error occurred during Maven execution: {e}"
        logger.error(result["reason"], exc_info=True)
    finally:
        # Cleanup temporary directory if it was created.
        if temp_maven_dir_context:
            try:
                temp_maven_dir_context.cleanup()
            except Exception:
                pass

    return result


# ---------------- Auto-registration ----------------


def register_plugin_entrypoints(register_func: Callable):
    """
    Registers this plugin's test runner function with the core simulation system.
    """
    logger.info("Registering JavaTestRunnerPlugin entrypoints...")
    register_func(
        language_or_framework="java",
        runner_info={
            "command": [
                "mvn",
                "test",
            ],  # Placeholder; actual execution uses run_java_tests
            "extensions": [".java"],
            "test_discovery": ["test", "IT"],
            "runner_function": run_java_tests,
        },
    )
    register_func(
        language_or_framework="java_maven",
        runner_info={
            "command": ["mvn", "test"],
            "extensions": [".java"],
            "test_discovery": ["test", "IT"],
            "runner_function": run_java_tests,
        },
    )


if __name__ == "__main__":

    async def _mock_register_test_runner(
        lang_or_framework: str, runner_info: Dict[str, Any]
    ):
        print(f"Mocked registration for {lang_or_framework}: {runner_info}")

    register_plugin_entrypoints(_mock_register_test_runner)

    async def main_test_run():
        print("\n--- Running Mock Java Test (Maven) ---")
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)

            # Create a dummy Java source file
            dummy_src = temp_root / "src" / "main" / "java" / "com" / "example" / "app"
            dummy_src.mkdir(parents=True, exist_ok=True)
            (dummy_src / "Calculator.java").write_text(
                """
package com.example.app;
public class Calculator {
    public int add(int a, int b) { return a + b; }
}
""",
                encoding="utf-8",
            )

            # Create a dummy Java test file
            dummy_test = temp_root / "src" / "test" / "java" / "com" / "example" / "app"
            dummy_test.mkdir(parents=True, exist_ok=True)
            (dummy_test / "CalculatorTest.java").write_text(
                """
package com.example.app;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.assertEquals;
public class CalculatorTest {
    @Test
    void testAdd() {
        Calculator calculator = new Calculator();
        assertEquals(3, calculator.add(1, 2), "1 + 2 should equal 3");
    }
}
""",
                encoding="utf-8",
            )

            # Coverage output location
            coverage_rel = (
                Path("atco_artifacts") / "coverage_reports" / "java_coverage_output.xml"
            )
            (temp_root / coverage_rel).parent.mkdir(parents=True, exist_ok=True)

            result = await run_java_tests(
                test_file_path=str(dummy_test / "CalculatorTest.java").replace(
                    str(temp_root) + os.sep, ""
                ),
                target_identifier="com.example.app.Calculator",
                project_root=str(temp_root),
                temp_coverage_report_path_relative=str(coverage_rel),
                extra_maven_args=["--no-transfer-progress"],
                batch_mode=True,
                quiet=False,
                include_integration_tests=True,
                timeout_seconds=600,
                log_artifact_path_relative=str(
                    Path("atco_artifacts") / "logs" / "maven_run.log"
                ),
            )

            print(f"\nTest Result: {'PASS' if result['success'] else 'FAIL'}")
            print(f"Coverage: {result['coverage_percent']:.2f}%")
            print(f"Reason: {result['reason']}")
            print("Execution Log (tail):\n", result["raw_log"][-1000:])
            print(f"Temporary Directories Used: {result['temp_dirs_used']}")
            print(f"Maven Version: {result.get('maven_version', 'N/A')}")
            cov_path = temp_root / coverage_rel
            print(f"Coverage report exists: {cov_path.exists()} at {cov_path}")

        print("\n--- Test Run Complete ---")

    asyncio.run(main_test_run())
