
# test_runner_parsers.py
# Industry-grade test suite for runner_parsers.py, ensuring compliance with regulated standards.
# Covers unit and integration tests for all parsers, with traceability, reproducibility, and security.

import pytest
import asyncio
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock
from datetime import datetime, timezone
import logging
import re
from collections import defaultdict
from pydantic import ValidationError

# Import required classes and functions from runner_parsers
from runner.parsers import (
    ParserInfo, TestCaseResult, TestReportSchema, CoverageReportSchema,
    parse_junit_xml, parse_coverage_xml, parse_unittest_output, parse_behave_junit,
    parse_robot_xml, parse_jest_json, parse_go_test_json, parse_surefire_xml,
    parse_jacoco_xml, parse_istanbul_json, parse_go_coverprofile, parse_coverage_html,
    TOTAL_TESTS_KEY, PASSED_TESTS_KEY, FAILED_TESTS_KEY, ERROR_TESTS_KEY,
    SKIPPED_TESTS_KEY, PASS_RATE_KEY, COVERAGE_PERCENTAGE_KEY, COVERAGE_DETAILS_KEY,
    TEST_CASES_KEY, CURRENT_PARSER_SCHEMA_VERSION
)

# Import structured errors for testing error handling
from runner.errors import ParsingError
from runner.errors import ERROR_CODE_REGISTRY as error_codes

# Configure logging for traceability and auditability
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s [trace_id=%(trace_id)s]',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Mock OpenTelemetry tracer for testing without external dependencies
class MockSpan:
    def set_attribute(self, key, value): pass
    def set_status(self, status): pass
    def record_exception(self, exception): pass
    def end(self): pass
    def __enter__(self): return self
    def __exit__(self, exc_type, exc_val, exc_tb): pass

class MockTracer:
    def start_as_current_span(self, name, *args, **kwargs): return MockSpan()

mock_tracer = MockTracer()

# Fixture for temporary directory
@pytest.fixture
def tmp_path(tmp_path_factory):
    """Create a temporary directory for test files."""
    return tmp_path_factory.mktemp("parser_test")

# Fixture for mock OpenTelemetry tracer
@pytest.fixture(autouse=True)
def mock_opentelemetry():
    """Mock OpenTelemetry tracer for all tests."""
    with patch('runner.parsers.trace', mock_tracer):
        yield

# Fixture for audit log
@pytest.fixture
def audit_log(tmp_path):
    """Set up an audit log file for traceability."""
    log_file = tmp_path / "audit.log"
    handler = logging.FileHandler(log_file)
    handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s [trace_id=%(trace_id)s]'
    ))
    logger.addHandler(handler)
    yield log_file
    logger.removeHandler(handler)

# Helper function to log test execution for auditability
def log_test_execution(test_name, result, trace_id):
    """Log test execution details for audit trail."""
    logger.debug(
        f"Test {test_name}: {result}",
        extra={'trace_id': trace_id}
    )

# Test class for parser schemas
class TestParserSchemas:
    """Tests for Pydantic schemas used in runner_parsers.py."""

    @pytest.mark.asyncio
    async def test_parser_info_schema_valid(self, audit_log):
        """Test valid ParserInfo schema creation."""
        trace_id = str(uuid.uuid4())
        parser_info = ParserInfo(
            parser_name="junit_xml",
            version="1.0",
            timestamp=datetime.now(timezone.utc).isoformat() + 'Z',
            status="success",
            message="Parsing completed successfully.",
            schema_version=CURRENT_PARSER_SCHEMA_VERSION
        )
        assert parser_info.parser_name == "junit_xml"
        assert parser_info.schema_version == CURRENT_PARSER_SCHEMA_VERSION
        log_test_execution("test_parser_info_schema_valid", "Passed", trace_id)

    @pytest.mark.asyncio
    async def test_test_case_result_schema_valid(self, audit_log):
        """Test valid TestCaseResult schema creation."""
        trace_id = str(uuid.uuid4())
        test_case = TestCaseResult(
            name="test_example",
            classname="module.test",
            status="passed",
            time=0.1,
            message="Test passed",
            failure_info=None
        )
        assert test_case.status == "passed"
        assert test_case.time == 0.1
        log_test_execution("test_test_case_result_schema_valid", "Passed", trace_id)

    @pytest.mark.asyncio
    async def test_test_report_schema_valid(self, audit_log):
        """Test valid TestReportSchema creation."""
        trace_id = str(uuid.uuid4())
        test_report = TestReportSchema(
            total_tests=10,
            passed_tests=8,
            failed_tests=1,
            error_tests=1,
            skipped_tests=0,
            pass_rate=0.8,
            test_cases=[
                TestCaseResult(name="test_one", status="passed"),
                TestCaseResult(name="test_two", status="failed", failure_info={"error": "AssertionError"})
            ],
            parser_info=ParserInfo(parser_name="junit_xml", version="1.0")
        )
        assert test_report.pass_rate == 0.8
        assert len(test_report.test_cases) == 2
        log_test_execution("test_test_report_schema_valid", "Passed", trace_id)

    @pytest.mark.asyncio
    async def test_coverage_report_schema_valid(self, audit_log):
        """Test valid CoverageReportSchema creation."""
        trace_id = str(uuid.uuid4())
        coverage_report = CoverageReportSchema(
            coverage_percentage=75.0,
            coverage_details={"file.py": {"lines_covered": 75, "lines_total": 100}},
            parser_info=ParserInfo(parser_name="coverage_xml", version="1.0")
        )
        assert coverage_report.coverage_percentage == 75.0
        assert "file.py" in coverage_report.coverage_details
        log_test_execution("test_coverage_report_schema_valid", "Passed", trace_id)

    @pytest.mark.asyncio
    async def test_schema_validation_error(self, audit_log):
        """Test schema validation failure handling."""
        trace_id = str(uuid.uuid4())
        with pytest.raises(ValidationError):
            TestReportSchema(
                total_tests=-1,  # Invalid negative value
                passed_tests=8,
                failed_tests=1,
                error_tests=1,
                skipped_tests=0,
                pass_rate=0.8,
                test_cases=[],
                parser_info=ParserInfo(parser_name="junit_xml", version="1.0")
            )
        log_test_execution("test_schema_validation_error", "Passed", trace_id)

# Test class for individual parsers
class TestParsers:
    """Tests for individual parser functions in runner_parsers.py."""

    @pytest.mark.asyncio
    async def test_parse_junit_xml_valid(self, tmp_path, audit_log):
        """Test parsing a valid JUnit XML file."""
        trace_id = str(uuid.uuid4())
        junit_content = """<?xml version="1.0" encoding="UTF-8"?>
<testsuites>
  <testsuite name="MyTestSuite" tests="2" failures="1" errors="0" skipped="0">
    <testcase classname="test_module" name="test_pass" time="0.1"/>
    <testcase classname="test_module" name="test_fail" time="0.2">
      <failure message="Assertion failed">Traceback...</failure>
    </testcase>
  </testsuite>
</testsuites>"""
        junit_file = tmp_path / "results.xml"
        junit_file.write_text(junit_content)
        result = await parse_junit_xml(junit_file)
        assert result.total_tests == 2
        assert result.passed_tests == 1
        assert result.failed_tests == 1
        assert result.pass_rate == 0.5
        assert result.parser_info.status == "success"
        log_test_execution("test_parse_junit_xml_valid", "Passed", trace_id)

    @pytest.mark.asyncio
    async def test_parse_junit_xml_invalid(self, tmp_path, audit_log):
        """Test parsing an invalid JUnit XML file."""
        trace_id = str(uuid.uuid4())
        invalid_content = """<?xml version="1.0" encoding="UTF-8"?>
<testsuites>
  <testsuite name="MyTestSuite" tests="2" failures="1" errors="0" skipped="0">
    <testcase classname="test_module" name="test_pass" time="invalid"/>
  </testsuite>
</testsuites>"""
        junit_file = tmp_path / "results.xml"
        junit_file.write_text(invalid_content)
        with pytest.raises(ParsingError) as exc_info:
            await parse_junit_xml(junit_file)
        assert exc_info.value.error_code == error_codes["PARSING_ERROR"]
        assert "Invalid time format" in exc_info.value.detail
        log_test_execution("test_parse_junit_xml_invalid", "Passed", trace_id)

    @pytest.mark.asyncio
    async def test_parse_coverage_xml_valid(self, tmp_path, audit_log):
        """Test parsing a valid Cobertura XML coverage file."""
        trace_id = str(uuid.uuid4())
        cobertura_content = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE coverage SYSTEM "http://cobertura.sourceforge.net/xml/coverage04.dtd">
<coverage line-rate="0.75">
  <packages>
    <package name="com.example.app">
      <classes>
        <class name="MyClass" filename="com/example/app/MyClass.py">
          <lines>
            <line number="1" hits="1"/>
            <line number="2" hits="0"/>
            <line number="3" hits="1"/>
            <line number="4" hits="1"/>
          </lines>
        </class>
      </classes>
    </package>
  </packages>
</coverage>"""
        cobertura_file = tmp_path / "cov.xml"
        cobertura_file.write_text(cobertura_content)
        result = await parse_coverage_xml(cobertura_file)
        assert result.coverage_percentage == 75.0
        assert "com/example/app/MyClass.py" in result.coverage_details
        assert result.parser_info.status == "success"
        log_test_execution("test_parse_coverage_xml_valid", "Passed", trace_id)

    @pytest.mark.asyncio
    async def test_parse_unittest_output_valid(self, tmp_path, audit_log):
        """Test parsing valid unittest output."""
        trace_id = str(uuid.uuid4())
        unittest_content = """
test_pass (test_module.TestCase) ... ok
test_fail (test_module.TestCase) ... FAIL

FAIL: test_fail (test_module.TestCase)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "test_module.py", line 10, in test_fail
    self.assertEqual(1, 2)
AssertionError: 1 != 2

----------------------------------------------------------------------
Ran 2 tests in 0.002s

FAILED (failures=1)
"""
        unittest_file = tmp_path / "results.txt"
        unittest_file.write_text(unittest_content)
        result = await parse_unittest_output(unittest_file)
        assert result.total_tests == 2
        assert result.passed_tests == 1
        assert result.failed_tests == 1
        assert result.pass_rate == 0.5
        assert result.parser_info.status == "success"
        log_test_execution("test_parse_unittest_output_valid", "Passed", trace_id)

    @pytest.mark.asyncio
    async def test_parse_behave_junit_valid(self, tmp_path, audit_log):
        """Test parsing valid Behave JUnit output."""
        trace_id = str(uuid.uuid4())
        behave_content = """<?xml version="1.0" encoding="UTF-8"?>
<testsuites>
  <testsuite name="MyFeature" tests="2" failures="1">
    <testcase classname="feature1" name="Scenario1" time="0.1"/>
    <testcase classname="feature1" name="Scenario2" time="0.2">
      <failure message="Step failed">Step definition failed</failure>
    </testcase>
  </testsuite>
</testsuites>"""
        behave_file = tmp_path / "behave.xml"
        behave_file.write_text(behave_content)
        result = await parse_behave_junit(behave_file)
        assert result.total_tests == 2
        assert result.passed_tests == 1
        assert result.failed_tests == 1
        assert result.parser_info.status == "success"
        log_test_execution("test_parse_behave_junit_valid", "Passed", trace_id)

    @pytest.mark.asyncio
    async def test_parse_robot_xml_valid(self, tmp_path, audit_log):
        """Test parsing valid Robot Framework XML output."""
        trace_id = str(uuid.uuid4())
        robot_content = """<?xml version="1.0" encoding="UTF-8"?>
<robot>
  <suite name="MySuite">
    <test name="Test1" id="s1-t1">
      <status status="PASS" starttime="2025-01-01 12:00:00.000" endtime="2025-01-01 12:00:00.100"/>
    </test>
    <test name="Test2" id="s1-t2">
      <status status="FAIL" starttime="2025-01-01 12:00:00.100" endtime="2025-01-01 12:00:00.300">Error message</status>
    </test>
  </suite>
</robot>"""
        robot_file = tmp_path / "output.xml"
        robot_file.write_text(robot_content)
        result = await parse_robot_xml(robot_file)
        assert result.total_tests == 2
        assert result.passed_tests == 1
        assert result.failed_tests == 1
        assert result.parser_info.status == "success"
        log_test_execution("test_parse_robot_xml_valid", "Passed", trace_id)

    @pytest.mark.asyncio
    async def test_parse_jest_json_valid(self, tmp_path, audit_log):
        """Test parsing valid Jest JSON output."""
        trace_id = str(uuid.uuid4())
        jest_content = {
            "numTotalTests": 3,
            "numPassedTests": 2,
            "numFailedTests": 1,
            "testResults": [
                {"name": "test1", "status": "passed"},
                {"name": "test2", "status": "passed"},
                {"name": "test3", "status": "failed", "message": "AssertionError"}
            ]
        }
        jest_file = tmp_path / "results.json"
        jest_file.write_text(json.dumps(jest_content))
        result = await parse_jest_json(jest_file)
        assert result.total_tests == 3
        assert result.passed_tests == 2
        assert result.failed_tests == 1
        assert result.pass_rate == pytest.approx(2/3)
        assert result.parser_info.status == "success"
        log_test_execution("test_parse_jest_json_valid", "Passed", trace_id)

    @pytest.mark.asyncio
    async def test_parse_go_test_json_valid(self, tmp_path, audit_log):
        """Test parsing valid Go test JSON output."""
        trace_id = str(uuid.uuid4())
        go_test_content = """
{"Time":"2025-01-01T12:00:00Z","Action":"pass","Package":"example.com/test","Test":"Test1"}
{"Time":"2025-01-01T12:00:01Z","Action":"fail","Package":"example.com/test","Test":"Test2"}
"""
        go_test_file = tmp_path / "go_test.json"
        go_test_file.write_text(go_test_content)
        result = await parse_go_test_json(go_test_file)
        assert result.total_tests == 2
        assert result.passed_tests == 1
        assert result.failed_tests == 1
        assert result.pass_rate == 0.5
        assert result.parser_info.status == "success"
        log_test_execution("test_parse_go_test_json_valid", "Passed", trace_id)

    @pytest.mark.asyncio
    async def test_parse_surefire_xml_valid(self, tmp_path, audit_log):
        """Test parsing valid Maven Surefire XML output."""
        trace_id = str(uuid.uuid4())
        surefire_content = """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="MyTestSuite" tests="2" failures="1" errors="0" skipped="0">
  <testcase classname="com.example.Test" name="testPass" time="0.1"/>
  <testcase classname="com.example.Test" name="testFail" time="0.2">
    <failure message="Assertion failed">Traceback...</failure>
  </testcase>
</testsuite>"""
        surefire_file = tmp_path / "surefire.xml"
        surefire_file.write_text(surefire_content)
        result = await parse_surefire_xml(surefire_file)
        assert result.total_tests == 2
        assert result.passed_tests == 1
        assert result.failed_tests == 1
        assert result.pass_rate == 0.5
        assert result.parser_info.status == "success"
        log_test_execution("test_parse_surefire_xml_valid", "Passed", trace_id)

    @pytest.mark.asyncio
    async def test_parse_jacoco_xml_valid(self, tmp_path, audit_log):
        """Test parsing valid JaCoCo XML coverage file."""
        trace_id = str(uuid.uuid4())
        jacoco_content = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE report PUBLIC "-//JACOCO//DTD Report 1.1//EN" "report.dtd">
<report name="My Java Project">
  <package name="com/example/java">
    <class name="com/example/java/MyService">
      <counter type="INSTRUCTION" missed="3" covered="17"/>
    </class>
  </package>
</report>"""
        jacoco_file = tmp_path / "jacoco_coverage.xml"
        jacoco_file.write_text(jacoco_content)
        result = await parse_jacoco_xml(jacoco_file)
        assert result.coverage_percentage == pytest.approx((17/(17+3))*100)
        assert "com/example/java/MyService" in result.coverage_details
        assert result.parser_info.status == "success"
        log_test_execution("test_parse_jacoco_xml_valid", "Passed", trace_id)

    @pytest.mark.asyncio
    async def test_parse_istanbul_json_valid(self, tmp_path, audit_log):
        """Test parsing valid Istanbul JSON coverage output."""
        trace_id = str(uuid.uuid4())
        istanbul_content = {
            "/path/to/project/src/app.js": {
                "path": "/path/to/project/src/app.js",
                "s": {"1": 1, "2": 1, "3": 0, "4": 1},
                "b": {}, "f": {}, "inputSourceMap": None
            },
            "/path/to/project/src/util.js": {
                "path": "/path/to/project/src/util.js",
                "s": {"1": 1, "2": 0},
                "b": {}, "f": {}, "inputSourceMap": None
            }
        }
        istanbul_file = tmp_path / "istanbul_coverage.json"
        istanbul_file.write_text(json.dumps(istanbul_content))
        result = await parse_istanbul_json(istanbul_file)
        assert result.coverage_percentage == pytest.approx(66.67, rel=1e-2)
        assert "/path/to/project/src/app.js" in result.coverage_details
        assert result.parser_info.status == "success"
        log_test_execution("test_parse_istanbul_json_valid", "Passed", trace_id)

    @pytest.mark.asyncio
    async def test_parse_go_coverprofile_valid(self, tmp_path, audit_log):
        """Test parsing valid Go coverprofile output."""
        trace_id = str(uuid.uuid4())
        go_cover_content = """mode: count
github.com/myuser/myproject/main.go:8.26,10.2 1 1
github.com/myuser/myproject/main.go:12.3,13.2 1 0
github.com/myuser/myproject/util.go:5.5,6.2 1 1
"""
        go_cover_file = tmp_path / "coverage.out"
        go_cover_file.write_text(go_cover_content)
        result = await parse_go_coverprofile(go_cover_file)
        assert result.coverage_percentage == pytest.approx(66.67, rel=1e-2)
        assert "github.com/myuser/myproject/main.go" in result.coverage_details
        assert result.parser_info.status == "success"
        log_test_execution("test_parse_go_coverprofile_valid", "Passed", trace_id)

    @pytest.mark.asyncio
    async def test_parse_coverage_html_valid(self, tmp_path, audit_log):
        """Test parsing valid HTML coverage output."""
        trace_id = str(uuid.uuid4())
        html_cov_content = """<html><body><span class="pc_cov">Overall coverage: 85.5%</span></body></html>"""
        html_cov_dir = tmp_path / "htmlcov"
        html_cov_dir.mkdir(exist_ok=True)
        html_cov_file = html_cov_dir / "index.html"
        html_cov_file.write_text(html_cov_content)
        result = await parse_coverage_html(html_cov_file)
        assert result.coverage_percentage == 85.5
        assert result.parser_info.status == "success"
        log_test_execution("test_parse_coverage_html_valid", "Passed", trace_id)

    @pytest.mark.asyncio
    async def test_parse_file_not_found(self, tmp_path, audit_log):
        """Test handling of missing file for parsing."""
        trace_id = str(uuid.uuid4())
        nonexistent_file = tmp_path / "nonexistent.xml"
        with pytest.raises(ParsingError) as exc_info:
            await parse_junit_xml(nonexistent_file)
        assert exc_info.value.error_code == error_codes["PARSING_ERROR"]
        assert "File not found" in exc_info.value.detail
        log_test_execution("test_parse_file_not_found", "Passed", trace_id)

# Integration test class
class TestParserIntegration:
    """Integration tests for parser interactions with runner components."""

    @pytest.mark.asyncio
    async def test_junit_and_coverage_integration(self, tmp_path, audit_log):
        """Test combined parsing of JUnit and coverage XML files."""
        trace_id = str(uuid.uuid4())
        junit_content = """<?xml version="1.0" encoding="UTF-8"?>
<testsuites>
  <testsuite name="MyTestSuite" tests="3" failures="1" errors="0" skipped="0">
    <testcase classname="test_module" name="test_pass1" time="0.1"/>
    <testcase classname="test_module" name="test_pass2" time="0.2"/>
    <testcase classname="test_module" name="test_fail" time="0.3">
      <failure message="Assertion failed">Traceback...</failure>
    </testcase>
  </testsuite>
</testsuites>"""
        cobertura_content = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE coverage SYSTEM "http://cobertura.sourceforge.net/xml/coverage04.dtd">
<coverage line-rate="0.8">
  <packages>
    <package name="com.example.app">
      <classes>
        <class name="MyClass" filename="com/example/app/MyClass.py">
          <lines>
            <line number="1" hits="1"/>
            <line number="2" hits="1"/>
            <line number="3" hits="1"/>
            <line number="4" hits="0"/>
            <line number="5" hits="1"/>
          </lines>
        </class>
      </classes>
    </package>
  </packages>
</coverage>"""
        junit_file = tmp_path / "results.xml"
        cobertura_file = tmp_path / "cov.xml"
        junit_file.write_text(junit_content)
        cobertura_file.write_text(cobertura_content)

        # Mock aiofiles.open to simulate file reading
        with patch('aiofiles.open', AsyncMock()) as mock_open:
            mock_open.side_effect = [
                AsyncMock(__aenter__=AsyncMock(return_value=MagicMock(read=AsyncMock(return_value=junit_content))),
                AsyncMock(__aenter__=AsyncMock(return_value=MagicMock(read=AsyncMock(return_value=cobertura_content)))
            ]
            test_result = await parse_junit_xml(junit_file)
            coverage_result = await parse_coverage_xml(cobertura_file)

        assert test_result.total_tests == 3
        assert test_result.passed_tests == 2
        assert test_result.pass_rate == pytest.approx(2/3)
        assert coverage_result.coverage_percentage == 80.0
        assert "com/example/app/MyClass.py" in coverage_result.coverage_details
        assert test_result.parser_info.status == "success"
        assert coverage_result.parser_info.status == "success"
        log_test_execution("test_junit_and_coverage_integration", "Passed", trace_id)

    @pytest.mark.asyncio
    async def test_parser_error_propagation(self, tmp_path, audit_log):
        """Test error propagation with structured error handling."""
        trace_id = str(uuid.uuid4())
        invalid_content = """<invalid>not xml</invalid>"""
        invalid_file = tmp_path / "invalid.xml"
        invalid_file.write_text(invalid_content)
        with pytest.raises(ParsingError) as exc_info:
            await parse_junit_xml(invalid_file)
        assert exc_info.value.error_code == error_codes["PARSING_ERROR"]
        assert "XML parsing failed" in exc_info.value.detail
        assert exc_info.value.as_dict()['timestamp_utc']
        log_test_execution("test_parser_error_propagation", "Passed", trace_id)

# Run tests with audit logging
if __name__ == "__main__":
    pytest.main(["-v", "--log-level=DEBUG"])
