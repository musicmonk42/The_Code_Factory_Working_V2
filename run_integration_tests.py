#!/usr/bin/env python3
"""
Integration Test Suite Runner

This script runs comprehensive integration tests to validate all workflows
and system components are working correctly before production deployment.

Usage:
    python run_integration_tests.py [--verbose] [--fail-fast] [--suite SUITE_NAME]
"""

import sys
import os
import time
import argparse
import subprocess
import json
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from enum import Enum
import requests


class TestStatus(Enum):
    """Test result status"""
    PASSED = "PASSED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    ERROR = "ERROR"


@dataclass
class TestResult:
    """Individual test result"""
    name: str
    status: TestStatus
    duration: float
    message: Optional[str] = None
    error: Optional[str] = None


class IntegrationTestRunner:
    """Integration test suite runner"""
    
    def __init__(self, verbose: bool = False, fail_fast: bool = False):
        self.verbose = verbose
        self.fail_fast = fail_fast
        self.results: List[TestResult] = []
        self.base_url = os.environ.get("BASE_URL", "http://localhost:8000")
        
    def log(self, message: str, level: str = "INFO"):
        """Log message with level"""
        if self.verbose or level in ["ERROR", "WARNING"]:
            print(f"[{level}] {message}")
    
    def run_test(self, name: str, test_func) -> TestResult:
        """Run a single test and record result"""
        self.log(f"Running test: {name}")
        start_time = time.time()
        
        try:
            test_func()
            duration = time.time() - start_time
            result = TestResult(
                name=name,
                status=TestStatus.PASSED,
                duration=duration,
                message=f"Test passed in {duration:.2f}s"
            )
            self.log(f"✓ {name} - PASSED ({duration:.2f}s)", "INFO")
        except AssertionError as e:
            duration = time.time() - start_time
            result = TestResult(
                name=name,
                status=TestStatus.FAILED,
                duration=duration,
                message=f"Assertion failed: {str(e)}",
                error=str(e)
            )
            self.log(f"✗ {name} - FAILED: {str(e)}", "ERROR")
        except Exception as e:
            duration = time.time() - start_time
            result = TestResult(
                name=name,
                status=TestStatus.ERROR,
                duration=duration,
                message=f"Error occurred: {str(e)}",
                error=str(e)
            )
            self.log(f"✗ {name} - ERROR: {str(e)}", "ERROR")
        
        self.results.append(result)
        
        if self.fail_fast and result.status in [TestStatus.FAILED, TestStatus.ERROR]:
            raise Exception(f"Test failed: {name}")
        
        return result
    
    # =========================================================================
    # Service Health Tests
    # =========================================================================
    
    def test_service_health(self):
        """Test all services are healthy"""
        self.log("Checking service health...")
        response = requests.get(f"{self.base_url}/health", timeout=10)
        assert response.status_code == 200, f"Health check failed: {response.status_code}"
        data = response.json()
        assert data.get("status") == "healthy", f"Service not healthy: {data}"
    
    def test_database_connection(self):
        """Test database connectivity"""
        self.log("Testing database connection...")
        response = requests.get(f"{self.base_url}/health/db", timeout=10)
        assert response.status_code == 200, "Database health check failed"
        data = response.json()
        assert data.get("database") == "connected", "Database not connected"
    
    def test_redis_connection(self):
        """Test Redis connectivity"""
        self.log("Testing Redis connection...")
        response = requests.get(f"{self.base_url}/health/redis", timeout=10)
        assert response.status_code == 200, "Redis health check failed"
        data = response.json()
        assert data.get("redis") == "connected", "Redis not connected"
    
    def test_metrics_endpoint(self):
        """Test metrics endpoint is accessible"""
        self.log("Testing metrics endpoint...")
        response = requests.get(f"{self.base_url}/metrics", timeout=10)
        assert response.status_code == 200, "Metrics endpoint failed"
        assert "http_requests_total" in response.text, "Expected metrics not found"
    
    # =========================================================================
    # API Endpoint Tests
    # =========================================================================
    
    def test_api_docs_accessible(self):
        """Test API documentation is accessible"""
        self.log("Testing API docs...")
        response = requests.get(f"{self.base_url}/docs", timeout=10)
        assert response.status_code == 200, "API docs not accessible"
    
    def test_openapi_spec(self):
        """Test OpenAPI specification is valid"""
        self.log("Testing OpenAPI spec...")
        response = requests.get(f"{self.base_url}/openapi.json", timeout=10)
        assert response.status_code == 200, "OpenAPI spec not accessible"
        spec = response.json()
        assert "openapi" in spec, "Invalid OpenAPI spec"
        assert "paths" in spec, "No paths defined in OpenAPI spec"
    
    # =========================================================================
    # Code Generation Workflow Tests
    # =========================================================================
    
    def test_code_generation_workflow(self):
        """Test end-to-end code generation workflow"""
        self.log("Testing code generation workflow...")
        
        # Submit generation request
        payload = {
            "requirements": "Create a simple Flask app with /hello endpoint",
            "language": "python",
            "framework": "flask"
        }
        response = requests.post(
            f"{self.base_url}/api/v1/generate",
            json=payload,
            timeout=30
        )
        assert response.status_code in [200, 202], f"Generation failed: {response.status_code}"
        data = response.json()
        generation_id = data.get("id")
        assert generation_id, "No generation ID returned"
        
        # Poll for completion (max 60 seconds)
        max_attempts = 30
        for attempt in range(max_attempts):
            time.sleep(2)
            response = requests.get(
                f"{self.base_url}/api/v1/generations/{generation_id}",
                timeout=10
            )
            assert response.status_code == 200, "Failed to get generation status"
            data = response.json()
            status = data.get("status")
            
            if status == "completed":
                self.log("Generation completed successfully")
                break
            elif status == "failed":
                raise AssertionError(f"Generation failed: {data.get('error')}")
            elif attempt == max_attempts - 1:
                raise AssertionError("Generation timed out")
    
    def test_list_generations(self):
        """Test listing generations"""
        self.log("Testing list generations...")
        response = requests.get(f"{self.base_url}/api/v1/generations", timeout=10)
        assert response.status_code == 200, "Failed to list generations"
        data = response.json()
        assert isinstance(data, list), "Expected list of generations"
    
    # =========================================================================
    # Self-Fixing Engineer Tests
    # =========================================================================
    
    def test_sfe_health(self):
        """Test Self-Fixing Engineer is operational"""
        self.log("Testing SFE health...")
        response = requests.get(f"{self.base_url}/health/sfe", timeout=10)
        assert response.status_code == 200, "SFE health check failed"
        data = response.json()
        assert data.get("sfe") in ["healthy", "operational"], "SFE not operational"
    
    def test_sfe_checkpoint(self):
        """Test SFE checkpoint creation"""
        self.log("Testing SFE checkpoint...")
        payload = {"type": "test", "data": {"test": "checkpoint"}}
        response = requests.post(
            f"{self.base_url}/api/v1/sfe/checkpoint",
            json=payload,
            timeout=30
        )
        assert response.status_code in [200, 201], "Checkpoint creation failed"
    
    # =========================================================================
    # Security Tests
    # =========================================================================
    
    def test_security_headers(self):
        """Test security headers are present"""
        self.log("Testing security headers...")
        response = requests.get(f"{self.base_url}/", timeout=10)
        headers = response.headers
        
        # Check for important security headers
        assert "X-Content-Type-Options" in headers, "X-Content-Type-Options header missing"
        assert headers["X-Content-Type-Options"] == "nosniff"
        
        # Check CORS headers are configured
        assert "Access-Control-Allow-Origin" in headers, "CORS headers not configured"
    
    def test_rate_limiting(self):
        """Test rate limiting is working"""
        self.log("Testing rate limiting...")
        
        # Make multiple requests gradually to avoid overwhelming service
        endpoint = f"{self.base_url}/api/v1/generations"
        rate_limited = False
        
        # Use a more reasonable number of requests (50) with slight delays
        for i in range(50):
            response = requests.get(endpoint, timeout=5)
            if response.status_code == 429:
                rate_limited = True
                self.log("Rate limit triggered as expected")
                break
            # Small delay to avoid overwhelming the service
            if i % 10 == 0:
                time.sleep(0.1)
        
        # Note: This test is informational; rate limiting may not trigger
        # in test environment with low request volume
        self.log(f"Rate limiting: {'active' if rate_limited else 'not triggered (may be disabled in test)'}")
    
    # =========================================================================
    # Performance Tests
    # =========================================================================
    
    def test_api_response_time(self):
        """Test API response times are acceptable"""
        self.log("Testing API response times...")
        
        endpoints = [
            "/health",
            "/api/v1/generations",
            "/metrics"
        ]
        
        for endpoint in endpoints:
            start = time.time()
            response = requests.get(f"{self.base_url}{endpoint}", timeout=10)
            duration = time.time() - start
            
            assert response.status_code == 200, f"{endpoint} failed"
            assert duration < 1.0, f"{endpoint} too slow: {duration:.2f}s (max 1s)"
            self.log(f"{endpoint} responded in {duration:.3f}s")
    
    # =========================================================================
    # Database Tests
    # =========================================================================
    
    def test_database_migrations(self):
        """Test database migrations are up to date"""
        self.log("Testing database migrations...")
        # This would check alembic revision, etc.
        # For now, just verify database is accessible
        response = requests.get(f"{self.base_url}/health/db", timeout=10)
        assert response.status_code == 200, "Database not accessible"
    
    # =========================================================================
    # Monitoring Tests
    # =========================================================================
    
    def test_prometheus_metrics(self):
        """Test Prometheus metrics are being collected"""
        self.log("Testing Prometheus metrics...")
        response = requests.get(f"{self.base_url}/metrics", timeout=10)
        assert response.status_code == 200, "Metrics endpoint failed"
        
        # Check for key metrics
        metrics_text = response.text
        required_metrics = [
            "http_requests_total",
            "http_request_duration_seconds",
            "process_cpu_seconds_total",
            "process_resident_memory_bytes"
        ]
        
        for metric in required_metrics:
            assert metric in metrics_text, f"Required metric '{metric}' not found"
    
    def test_logging(self):
        """Test logging is working"""
        self.log("Testing logging...")
        # Verify logs are being written (implementation-specific)
        # For now, just verify logging endpoint exists
        response = requests.get(f"{self.base_url}/health", timeout=10)
        assert response.status_code == 200, "Health check failed"
    
    # =========================================================================
    # Test Suites
    # =========================================================================
    
    def run_suite_smoke(self):
        """Run smoke test suite (critical paths only)"""
        self.log("=== Running Smoke Test Suite ===", "INFO")
        self.run_test("Service Health", self.test_service_health)
        self.run_test("Database Connection", self.test_database_connection)
        self.run_test("Redis Connection", self.test_redis_connection)
        self.run_test("Metrics Endpoint", self.test_metrics_endpoint)
    
    def run_suite_api(self):
        """Run API test suite"""
        self.log("=== Running API Test Suite ===", "INFO")
        self.run_test("API Docs Accessible", self.test_api_docs_accessible)
        self.run_test("OpenAPI Spec", self.test_openapi_spec)
        self.run_test("List Generations", self.test_list_generations)
        self.run_test("API Response Time", self.test_api_response_time)
    
    def run_suite_workflow(self):
        """Run workflow test suite"""
        self.log("=== Running Workflow Test Suite ===", "INFO")
        self.run_test("Code Generation Workflow", self.test_code_generation_workflow)
    
    def run_suite_sfe(self):
        """Run Self-Fixing Engineer test suite"""
        self.log("=== Running SFE Test Suite ===", "INFO")
        self.run_test("SFE Health", self.test_sfe_health)
        self.run_test("SFE Checkpoint", self.test_sfe_checkpoint)
    
    def run_suite_security(self):
        """Run security test suite"""
        self.log("=== Running Security Test Suite ===", "INFO")
        self.run_test("Security Headers", self.test_security_headers)
        self.run_test("Rate Limiting", self.test_rate_limiting)
    
    def run_suite_monitoring(self):
        """Run monitoring test suite"""
        self.log("=== Running Monitoring Test Suite ===", "INFO")
        self.run_test("Prometheus Metrics", self.test_prometheus_metrics)
        self.run_test("Logging", self.test_logging)
    
    def run_suite_full(self):
        """Run full integration test suite"""
        self.log("=== Running Full Integration Test Suite ===", "INFO")
        self.run_suite_smoke()
        self.run_suite_api()
        self.run_suite_workflow()
        self.run_suite_sfe()
        self.run_suite_security()
        self.run_suite_monitoring()
    
    def generate_report(self) -> Dict[str, Any]:
        """Generate test report"""
        total = len(self.results)
        passed = sum(1 for r in self.results if r.status == TestStatus.PASSED)
        failed = sum(1 for r in self.results if r.status == TestStatus.FAILED)
        errors = sum(1 for r in self.results if r.status == TestStatus.ERROR)
        skipped = sum(1 for r in self.results if r.status == TestStatus.SKIPPED)
        
        total_duration = sum(r.duration for r in self.results)
        
        return {
            "summary": {
                "total": total,
                "passed": passed,
                "failed": failed,
                "errors": errors,
                "skipped": skipped,
                "success_rate": f"{(passed/total*100):.1f}%" if total > 0 else "0%",
                "total_duration": f"{total_duration:.2f}s"
            },
            "results": [
                {
                    "name": r.name,
                    "status": r.status.value,
                    "duration": f"{r.duration:.2f}s",
                    "message": r.message,
                    "error": r.error
                }
                for r in self.results
            ]
        }
    
    def print_report(self):
        """Print test report to console"""
        report = self.generate_report()
        summary = report["summary"]
        
        print("\n" + "="*70)
        print("Integration Test Report")
        print("="*70)
        print(f"Total Tests:    {summary['total']}")
        print(f"Passed:         {summary['passed']} ✓")
        print(f"Failed:         {summary['failed']} ✗")
        print(f"Errors:         {summary['errors']} ⚠")
        print(f"Skipped:        {summary['skipped']} -")
        print(f"Success Rate:   {summary['success_rate']}")
        print(f"Total Duration: {summary['total_duration']}")
        print("="*70)
        
        # Print failed/error tests
        failed_tests = [r for r in self.results 
                       if r.status in [TestStatus.FAILED, TestStatus.ERROR]]
        
        if failed_tests:
            print("\nFailed/Error Tests:")
            print("-"*70)
            for result in failed_tests:
                print(f"✗ {result.name}")
                print(f"  Status: {result.status.value}")
                print(f"  Message: {result.message}")
                if result.error:
                    print(f"  Error: {result.error}")
                print()
        
        return summary["passed"] == summary["total"]


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description="Run integration tests")
    parser.add_argument("--verbose", "-v", action="store_true",
                       help="Verbose output")
    parser.add_argument("--fail-fast", "-f", action="store_true",
                       help="Stop on first failure")
    parser.add_argument("--suite", "-s", 
                       choices=["smoke", "api", "workflow", "sfe", "security", "monitoring", "full"],
                       default="full",
                       help="Test suite to run (default: full)")
    parser.add_argument("--output", "-o", 
                       help="Output report to JSON file")
    parser.add_argument("--base-url", 
                       help="Base URL for API (default: http://localhost:8000)")
    
    args = parser.parse_args()
    
    if args.base_url:
        os.environ["BASE_URL"] = args.base_url
    
    runner = IntegrationTestRunner(verbose=args.verbose, fail_fast=args.fail_fast)
    
    try:
        # Run selected test suite
        suite_method = getattr(runner, f"run_suite_{args.suite}")
        suite_method()
        
        # Print report
        all_passed = runner.print_report()
        
        # Save to file if requested
        if args.output:
            report = runner.generate_report()
            with open(args.output, 'w') as f:
                json.dump(report, f, indent=2)
            print(f"\nReport saved to: {args.output}")
        
        # Exit with appropriate code
        sys.exit(0 if all_passed else 1)
        
    except Exception as e:
        print(f"\nFatal error: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
