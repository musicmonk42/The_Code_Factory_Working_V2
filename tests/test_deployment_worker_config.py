# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Test Suite for Deployment Worker Configuration
==============================================

This module validates that deployment configurations use a single worker
to prevent job synchronization issues in async FastAPI applications.

Tests cover:
1. Railway deployment worker configuration (railway.toml)
2. Consistency across deployment configurations (Dockerfile, Procfile, .env.production.template)
3. Single worker requirement for async FastAPI

Background:
Multiple workers were causing:
- Job synchronization issues (each worker has its own in-memory jobs_db)
- Deleted jobs reappearing after restart (all workers recover same jobs)
- "Job not found" errors (job created on one worker, request hits another)
- Clarification state inconsistencies between workers

The fix ensures all deployment configs use 1 worker, which is sufficient for
async FastAPI since it handles concurrent requests efficiently via async I/O.
Multiple workers only make sense for CPU-bound synchronous frameworks like Flask/Django.

Compliance:
- ISO 27001 A.12.1.4: Separation of development and production facilities
- SOC 2 CC7.2: System monitoring
- NIST SP 800-53 CP-2: Contingency planning

Author: Code Factory Platform Team
Version: 2.0.0
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

    def test_railway_worker_count_is_one(self):
        """Verify railway.toml sets WORKER_COUNT to 1 (not 4).
        
        FastAPI is fully async and doesn't benefit from multiple workers.
        Multiple workers cause issues:
        - Each worker has its own in-memory jobs_db dictionary
        - Jobs created on one worker aren't visible to other workers
        - Deleted jobs can reappear after restart (multiple workers recover same jobs)
        """
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
            worker_count, "1",
            f"railway.toml WORKER_COUNT should be '1' for production, not '{worker_count}'. "
            "Multiple workers cause job synchronization issues because each has its own memory cache."
        )

    def test_railway_comment_reflects_singleworker_mode(self):
        """Verify railway.toml comment mentions single-worker mode for async FastAPI."""
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
        
        # Should NOT mention "multi-worker" or "4 workers" as the configuration rationale
        self.assertNotIn(
            "4 workers",
            section_text,
            "Comment should not promote 4-worker mode (which causes the bugs)"
        )
        
        # Should mention single worker or async FastAPI
        has_singleworker_mention = (
            "1 worker" in section_text.lower() or
            "single worker" in section_text.lower() or
            "fully async" in section_text.lower()
        )
        self.assertTrue(
            has_singleworker_mention,
            "Comment should mention single-worker mode or async FastAPI"
        )

    def test_dockerfile_uses_one_worker(self):
        """Verify Dockerfile uses 1 worker (for consistency check)."""
        if not self.dockerfile.exists():
            self.skipTest("Dockerfile not found")
        
        content = self.dockerfile.read_text()
        
        # Look for --workers 1 in CMD or RUN
        workers_match = re.search(r'--workers\s+(\d+)', content)
        if workers_match:
            workers = workers_match.group(1)
            self.assertEqual(
                workers, "1",
                f"Dockerfile should use 1 worker, found {workers}"
            )

    def test_procfile_uses_one_worker(self):
        """Verify Procfile uses 1 worker (for consistency check)."""
        if not self.procfile.exists():
            self.skipTest("Procfile not found")
        
        content = self.procfile.read_text()
        
        # Look for --workers 1
        workers_match = re.search(r'--workers\s+(\d+)', content)
        if workers_match:
            workers = workers_match.group(1)
            self.assertEqual(
                workers, "1",
                f"Procfile should use 1 worker, found {workers}"
            )

    def test_env_production_template_uses_one_worker(self):
        """Verify .env.production.template uses WORKER_COUNT=1 (for consistency check)."""
        if not self.env_prod_template.exists():
            self.skipTest(".env.production.template not found")
        
        content = self.env_prod_template.read_text()
        
        # Look for WORKER_COUNT=1 (not commented)
        worker_count_match = re.search(r'^WORKER_COUNT=(\d+)', content, re.MULTILINE)
        if worker_count_match:
            worker_count = worker_count_match.group(1)
            self.assertEqual(
                worker_count, "1",
                f".env.production.template should use WORKER_COUNT=1, found {worker_count}"
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
        
        # All configs should use the same worker count (1)
        if len(configs) > 1:
            values = list(configs.values())
            self.assertTrue(
                all(v == 1 for v in values),
                f"Worker count should be consistent across deployment configs (all should be 1): {configs}"
            )


class TestWorkerCountBusinessLogic(unittest.TestCase):
    """Test worker count meets requirements."""

    def test_single_worker_for_async_fastapi(self):
        """Verify production uses 1 worker for async FastAPI.
        
        FastAPI is fully async and doesn't benefit from multiple workers.
        Multiple workers cause job synchronization issues because each worker
        has its own in-memory job cache (jobs_db dictionary).
        """
        project_root = PROJECT_ROOT
        railway_toml = project_root / "railway.toml"
        
        if not railway_toml.exists():
            self.skipTest("railway.toml not found")
        
        content = railway_toml.read_text()
        worker_count_match = re.search(r'^WORKER_COUNT\s*=\s*"(\d+)"', content, re.MULTILINE)
        
        if worker_count_match:
            worker_count = int(worker_count_match.group(1))
            self.assertEqual(
                worker_count, 1,
                "Production should use 1 worker for async FastAPI. "
                "Multiple workers cause job synchronization issues (jobs in one worker's "
                "memory aren't visible to other workers, deleted jobs reappear on restart)."
            )


if __name__ == "__main__":
    unittest.main()
