# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Test Suite for Deployment Worker Configuration
==============================================

This module validates that deployment configurations use consistent worker counts
to prevent single-worker bottlenecks that amplify resource usage issues.

Tests cover:
1. Railway deployment worker configuration (railway.toml)
2. Consistency across deployment configurations (Dockerfile, Procfile, .env.production.template)
3. Minimum worker count requirements for production

Background:
Single-worker mode was causing:
- Event loop saturation under concurrent requests
- Amplified file discovery busy-loops
- Service initialization bottlenecks
- Reduced throughput and increased latency

The fix ensures Railway uses 4 workers (matching Dockerfile/Procfile) for proper
concurrent request handling, reducing the impact of filesystem walks and service
re-initialization on individual requests.

Compliance:
- ISO 27001 A.12.1.4: Separation of development and production facilities
- SOC 2 CC7.2: System monitoring
- NIST SP 800-53 CP-2: Contingency planning

Author: Code Factory Platform Team
Version: 1.0.0
"""

import re
import sys
import unittest
from pathlib import Path

# Ensure project root is in path
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class TestRailwayWorkerConfiguration(unittest.TestCase):
    """Test Railway deployment worker configuration."""

    def setUp(self):
        """Set up test fixtures."""
        self.project_root = PROJECT_ROOT
        self.railway_toml = self.project_root / "railway.toml"
        self.dockerfile = self.project_root / "Dockerfile"
        self.procfile = self.project_root / "Procfile"
        self.env_prod_template = self.project_root / ".env.production.template"

    def test_railway_toml_exists(self):
        """Verify railway.toml exists."""
        self.assertTrue(
            self.railway_toml.exists(),
            "railway.toml should exist in project root"
        )

    def test_railway_worker_count_is_four(self):
        """Verify railway.toml sets WORKER_COUNT to 4 (not 1)."""
        if not self.railway_toml.exists():
            self.skipTest("railway.toml not found")
        
        content = self.railway_toml.read_text()
        
        # Find WORKER_COUNT setting (not commented out)
        worker_count_match = re.search(r'^WORKER_COUNT\s*=\s*"(\d+)"', content, re.MULTILINE)
        self.assertIsNotNone(
            worker_count_match,
            "railway.toml should contain WORKER_COUNT setting"
        )
        
        worker_count = worker_count_match.group(1)
        self.assertEqual(
            worker_count, "4",
            f"railway.toml WORKER_COUNT should be '4' for production, not '{worker_count}'. "
            "Single worker causes event loop saturation and amplifies resource issues."
        )

    def test_railway_comment_reflects_multiworker_mode(self):
        """Verify railway.toml comment mentions multi-worker mode."""
        if not self.railway_toml.exists():
            self.skipTest("railway.toml not found")
        
        content = self.railway_toml.read_text()
        
        # Find the Performance section with WORKER_COUNT
        performance_section = re.search(
            r'# === Performance ===.*?WORKER_COUNT',
            content,
            re.DOTALL
        )
        self.assertIsNotNone(performance_section, "Performance section should exist")
        
        section_text = performance_section.group(0)
        
        # Should NOT mention "single worker" as the configuration rationale
        self.assertNotIn(
            "Single worker for Railway deployment",
            section_text,
            "Comment should not promote single-worker mode (which was the bug)"
        )
        
        # Should mention multi-worker or concurrent requests
        has_multiworker_mention = (
            "multi-worker" in section_text.lower() or
            "concurrent requests" in section_text.lower()
        )
        self.assertTrue(
            has_multiworker_mention,
            "Comment should mention multi-worker mode or concurrent request handling"
        )

    def test_dockerfile_uses_four_workers(self):
        """Verify Dockerfile uses 4 workers (for consistency check)."""
        if not self.dockerfile.exists():
            self.skipTest("Dockerfile not found")
        
        content = self.dockerfile.read_text()
        
        # Look for --workers 4 in CMD or RUN
        workers_match = re.search(r'--workers\s+(\d+)', content)
        if workers_match:
            workers = workers_match.group(1)
            self.assertEqual(
                workers, "4",
                f"Dockerfile should use 4 workers, found {workers}"
            )

    def test_procfile_uses_four_workers(self):
        """Verify Procfile uses 4 workers (for consistency check)."""
        if not self.procfile.exists():
            self.skipTest("Procfile not found")
        
        content = self.procfile.read_text()
        
        # Look for --workers 4
        workers_match = re.search(r'--workers\s+(\d+)', content)
        if workers_match:
            workers = workers_match.group(1)
            self.assertEqual(
                workers, "4",
                f"Procfile should use 4 workers, found {workers}"
            )

    def test_env_production_template_uses_four_workers(self):
        """Verify .env.production.template uses WORKER_COUNT=4 (for consistency check)."""
        if not self.env_prod_template.exists():
            self.skipTest(".env.production.template not found")
        
        content = self.env_prod_template.read_text()
        
        # Look for WORKER_COUNT=4 (not commented)
        worker_count_match = re.search(r'^WORKER_COUNT=(\d+)', content, re.MULTILINE)
        if worker_count_match:
            worker_count = worker_count_match.group(1)
            self.assertEqual(
                worker_count, "4",
                f".env.production.template should use WORKER_COUNT=4, found {worker_count}"
            )

    def test_worker_count_consistency(self):
        """Verify Railway worker count matches other deployment configs."""
        configs = {}
        
        # Railway
        if self.railway_toml.exists():
            content = self.railway_toml.read_text()
            match = re.search(r'^WORKER_COUNT\s*=\s*"(\d+)"', content, re.MULTILINE)
            if match:
                configs['railway.toml'] = int(match.group(1))
        
        # Dockerfile
        if self.dockerfile.exists():
            content = self.dockerfile.read_text()
            match = re.search(r'--workers\s+(\d+)', content)
            if match:
                configs['Dockerfile'] = int(match.group(1))
        
        # Procfile
        if self.procfile.exists():
            content = self.procfile.read_text()
            match = re.search(r'--workers\s+(\d+)', content)
            if match:
                configs['Procfile'] = int(match.group(1))
        
        # .env.production.template
        if self.env_prod_template.exists():
            content = self.env_prod_template.read_text()
            match = re.search(r'^WORKER_COUNT=(\d+)', content, re.MULTILINE)
            if match:
                configs['.env.production.template'] = int(match.group(1))
        
        # All configs should use the same worker count (4)
        if len(configs) > 1:
            values = list(configs.values())
            self.assertTrue(
                all(v == 4 for v in values),
                f"Worker count should be consistent across deployment configs (all should be 4): {configs}"
            )


class TestWorkerCountBusinessLogic(unittest.TestCase):
    """Test worker count meets minimum requirements."""

    def test_minimum_workers_for_production(self):
        """Verify production uses at least 2 workers (ideally 4+)."""
        project_root = PROJECT_ROOT
        railway_toml = project_root / "railway.toml"
        
        if not railway_toml.exists():
            self.skipTest("railway.toml not found")
        
        content = railway_toml.read_text()
        worker_count_match = re.search(r'^WORKER_COUNT\s*=\s*"(\d+)"', content, re.MULTILINE)
        
        if worker_count_match:
            worker_count = int(worker_count_match.group(1))
            self.assertGreaterEqual(
                worker_count, 2,
                "Production should use at least 2 workers to handle concurrent requests. "
                "Single worker causes event loop saturation."
            )
            
            # Recommended minimum is 4 for good throughput
            self.assertGreaterEqual(
                worker_count, 4,
                "Production should use at least 4 workers for optimal concurrent request handling. "
                "This prevents busy-loops and service re-init from blocking the event loop."
            )


if __name__ == "__main__":
    unittest.main()
