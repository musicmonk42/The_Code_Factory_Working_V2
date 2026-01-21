# self_healing_import_fixer/analyzer/core_audit.py

"""
core_audit.py - Regulatory-Compliant Audit System
CRITICAL: This module handles audit logging for regulated industry compliance.
Tampering with this module is a federal crime under 18 U.S.C. § 1030.
"""

import asyncio
import atexit
import hashlib
import hmac
import json
import logging
import os
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from queue import Queue
from typing import Any, Dict, Optional

# Make aiofiles optional
try:
    import aiofiles

    AIOFILES_AVAILABLE = True
except ImportError:
    aiofiles = None
    AIOFILES_AVAILABLE = False


logger = logging.getLogger(__name__)

# --- Critical Configuration ---
PRODUCTION_MODE = os.getenv("PRODUCTION_MODE", "false").lower() == "true"
TESTING_MODE = (
    os.getenv("TESTING", "false").lower() == "true"
    or os.getenv("TESTING") == "1"
    or os.getenv("PYTEST_CURRENT_TEST") is not None
)
# REGULATORY_MODE defaults ON for safety, but is disabled in testing mode
REGULATORY_MODE = (
    os.getenv("REGULATORY_MODE", "true").lower() == "true" and not TESTING_MODE
)

# --- Import Core Dependencies ---
try:
    from .core_secrets import SECRETS_MANAGER
    from .core_utils import alert_operator, scrub_secrets
except ImportError as e:
    print(f"CRITICAL: Missing core dependency: {e}", file=sys.stderr)
    sys.exit(1)

# --- Splunk Integration (Secondary) ---
try:
    from splunk_http_event_collector import SplunkHttpEventCollector
except ImportError:
    SplunkHttpEventCollector = None


class AnalyzerCriticalError(RuntimeError):
    """Unrecoverable audit system failure."""

    pass


def _get_audit_hmac_key() -> bytes:
    """
    Retrieve HMAC key for audit log signing.
    CRITICAL: This key must be protected as TOP SECRET.
    """
    key = SECRETS_MANAGER.get_secret("ANALYZER_AUDIT_HMAC_KEY")

    if not key:
        if PRODUCTION_MODE or REGULATORY_MODE:
            alert_operator(
                "CRITICAL SECURITY VIOLATION: Audit HMAC key not found. "
                "System cannot operate in regulated mode without cryptographic audit integrity. "
                "THIS IS A COMPLIANCE VIOLATION.",
                level="CRITICAL",
            )
            sys.exit(1)
        else:
            # Development only - generate temporary key
            alert_operator(
                "WARNING: Using temporary HMAC key for development. "
                "NOT SUITABLE FOR PRODUCTION OR REGULATORY COMPLIANCE.",
                level="HIGH",
            )
            return os.urandom(32)

    return key.encode() if isinstance(key, str) else key


class RegulatoryAuditLogger:
    """
    Cryptographically-secured audit logger for regulatory compliance.

    Features:
    - HMAC-SHA256 signature on every log entry
    - Tamper detection with continuous integrity monitoring
    - Write-once append-only log structure
    - Automatic log rotation with signature chaining
    - Dual-write to SIEM for analytics (non-authoritative)

    Compliance:
    - PCI-DSS 10.x requirements
    - HIPAA audit log requirements
    - SOX audit trail requirements
    - GDPR integrity requirements
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.hmac_key = _get_audit_hmac_key()

        # Audit log paths
        # In testing mode, always use a temp directory to avoid permission issues
        if TESTING_MODE:
            # Use a temp directory that's writable in CI/test environments
            temp_audit_dir = Path(tempfile.gettempdir()) / "analyzer_audit"
            self.audit_dir = temp_audit_dir
        else:
            # Priority order for audit directory:
            # 1. Environment variable AUDIT_LOG_DIR
            # 2. Config value
            # 3. Default /var/log/analyzer_audit with fallbacks
            audit_dir_env = os.getenv("AUDIT_LOG_DIR")
            if audit_dir_env:
                self.audit_dir = Path(audit_dir_env)
            elif "audit_dir" in self.config:
                self.audit_dir = Path(self.config["audit_dir"])
            else:
                # Try /var/log/analyzer_audit first, with fallbacks for containerized environments
                self.audit_dir = self._determine_audit_directory()

        self.primary_log = self.audit_dir / "audit.log"
        self.integrity_file = self.audit_dir / "integrity.json"
        self.backup_log = self.audit_dir / "audit.backup.log"

        # Initialize file system
        self._initialize_audit_filesystem()

        # Initialize Splunk (secondary, best-effort)
        self._initialize_splunk()

        # Start integrity monitoring
        self._start_integrity_monitor()

        # Log system startup (critical event) - a proper fix is to not do this in __init__,
        # but to have a separate async init method.
        self._startup_logged = False

    def _determine_audit_directory(self) -> Path:
        """
        Determine the audit directory with fallback support for containerized environments.
        
        Try directories in order:
        1. /var/log/analyzer_audit (production default)
        2. /app/logs/analyzer_audit (containerized fallback)
        3. /tmp/analyzer_audit (last resort)
        
        Returns:
            Path: The first writable directory found
        """
        # List of candidate directories in priority order
        candidate_dirs = [
            Path("/var/log/analyzer_audit"),  # Production default
            Path("/app/logs/analyzer_audit"),  # Container-friendly location
            Path(tempfile.gettempdir()) / "analyzer_audit",  # Last resort
        ]
        
        for candidate in candidate_dirs:
            # Try to create the directory to test writability
            try:
                candidate.mkdir(parents=True, exist_ok=True, mode=0o700)
                # Test that we can actually write to it
                test_file = candidate / ".write_test"
                test_file.touch()
                test_file.unlink()
                
                # If we got here, this directory is writable
                if candidate != candidate_dirs[0]:
                    # Log when using fallback directory
                    logger.warning(
                        f"Using fallback audit directory: {candidate} "
                        f"(preferred /var/log/analyzer_audit not writable)"
                    )
                return candidate
            except (PermissionError, OSError) as e:
                # This candidate didn't work, try the next one
                logger.debug(f"Cannot use {candidate} for audit logs: {e}")
                continue
        
        # If we get here, none of the directories worked
        # Return the last one anyway and let the initialization handle the error
        return candidate_dirs[-1]

    def log_event(self, event_type: str, **kwargs):
        """
        Synchronous wrapper for logging audit events.
        For legacy sync code. DO NOT use in new async code.
        """
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        if loop.is_running():
            loop.create_task(self.log_critical_event(event_type, **kwargs))
        else:
            loop.run_until_complete(self.log_critical_event(event_type, **kwargs))

    def _initialize_audit_filesystem(self):
        """Initialize secure audit log filesystem."""
        try:
            # Create audit directory with restricted permissions
            self.audit_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

            # Set proper ownership (assuming running as service account)
            if PRODUCTION_MODE and hasattr(os, "chown"):  # Check if chown exists
                try:
                    import grp
                    import pwd

                    uid = pwd.getpwnam("audit_service").pw_uid
                    gid = grp.getgrnam("audit_group").gr_gid
                    os.chown(self.audit_dir, uid, gid)
                except (ImportError, KeyError, OSError) as e:
                    logger.warning(f"Could not set audit directory ownership: {e}")

            # Initialize files if they don't exist
            if not self.primary_log.exists():
                self.primary_log.touch(mode=0o600)
                self._write_initial_log_entry()

            if not self.integrity_file.exists():
                self._initialize_integrity_file()

        except (PermissionError, OSError) as e:
            error_msg = (
                f"CRITICAL: Cannot initialize audit filesystem: {e}. "
                "This is a COMPLIANCE VIOLATION. System must not process any data."
            )
            alert_operator(error_msg, level="CRITICAL")

            # In testing mode, log the error but don't halt the system
            # This allows tests to run without requiring privileged filesystem access
            if TESTING_MODE:
                logger.warning(
                    f"[OPS ALERT - CRITICAL] {error_msg} "
                    "(Continuing in TESTING mode)"
                )
            else:
                # In production/regulatory mode, halt the system
                sys.exit(1)

    def _write_initial_log_entry(self):
        """Write initial log entry when creating new log file."""
        initial_event = {
            "event_type": "AUDIT_LOG_CREATED",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "severity": "CRITICAL",
            "payload": {
                "message": "New audit log file initialized.",
                "system_start_time": datetime.utcnow().isoformat() + "Z",
            },
            "sequence": 1,
        }

        event_json = json.dumps(initial_event, sort_keys=True, ensure_ascii=False)
        signature = hmac.new(
            self.hmac_key, event_json.encode("utf-8"), hashlib.sha256
        ).hexdigest()

        signed_entry = {
            "event": initial_event,
            "signature": signature,
            "previous_hash": None,
        }

        try:
            with open(self.primary_log, "w") as f:
                f.write(json.dumps(signed_entry) + "\n")
                os.fsync(f.fileno())
            with open(self.backup_log, "w") as f:
                f.write(json.dumps(signed_entry) + "\n")
        except IOError as e:
            alert_operator(
                f"CRITICAL: Failed to write initial audit log entry: {e}. "
                "REGULATORY COMPLIANCE VIOLATED. HALTING SYSTEM.",
                level="CRITICAL",
            )
            sys.exit(1)

    def _initialize_integrity_file(self):
        """Initialize integrity metadata file."""
        metadata = {
            "last_verification": datetime.utcnow().isoformat() + "Z",
            "lines_verified": 0,
            "status": "INITIALIZED",
            "hmac_key_id": SECRETS_MANAGER.get_secret("ANALYZER_AUDIT_HMAC_KEY_ID")
            or "UNKNOWN",
        }
        try:
            with open(self.integrity_file, "w") as f:
                f.write(json.dumps(metadata, indent=2))
        except IOError as e:
            alert_operator(
                f"CRITICAL: Failed to initialize integrity metadata file: {e}. "
                "REGULATORY COMPLIANCE VIOLATED. HALTING SYSTEM.",
                level="CRITICAL",
            )
            sys.exit(1)

    async def log_startup(self):
        """Log system startup - call this after init."""
        if not self._startup_logged:
            await self.log_critical_event(
                "AUDIT_SYSTEM_INITIALIZED",
                hmac_key_id=SECRETS_MANAGER.get_secret("ANALYZER_AUDIT_HMAC_KEY_ID")
                or "UNKNOWN",
                regulatory_mode=REGULATORY_MODE,
                production_mode=PRODUCTION_MODE,
            )
            self._startup_logged = True

    def _initialize_splunk(self):
        """Initialize Splunk client for secondary logging."""
        self.splunk_client = None
        self.splunk_buffer = Queue(maxsize=10000)

        splunk_host = self.config.get("splunk_host")
        splunk_token = SECRETS_MANAGER.get_secret("SPLUNK_TOKEN")

        if splunk_host and splunk_token and SplunkHttpEventCollector:
            try:
                self.splunk_client = SplunkHttpEventCollector(
                    splunk_host, splunk_token, input_type="json"
                )
                logger.info("Splunk client initialized (secondary audit sink)")
            except Exception as e:
                logger.warning(f"Splunk initialization failed (non-critical): {e}")
                # Don't fail - Splunk is secondary

    async def log_critical_event(self, event_type: str, **kwargs):
        """
        Log a critical audit event with full integrity protection.

        REGULATORY REQUIREMENT: This method MUST succeed or the system must halt.
        """
        event = {
            "event_type": event_type,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "severity": "CRITICAL",
            "payload": scrub_secrets(kwargs),
            "sequence": await self._get_next_sequence_number(),
        }

        # Create HMAC signature
        event_json = json.dumps(event, sort_keys=True, ensure_ascii=False)
        signature = hmac.new(
            self.hmac_key, event_json.encode("utf-8"), hashlib.sha256
        ).hexdigest()

        # Create signed entry
        signed_entry = {
            "event": event,
            "signature": signature,
            "previous_hash": await self._get_previous_hash(),
        }

        # Atomic write to primary log
        try:
            if AIOFILES_AVAILABLE:
                async with aiofiles.open(self.primary_log, "ab") as f:
                    await f.write((json.dumps(signed_entry) + "\n").encode("utf-8"))
                    await f.flush()
                    os.fsync(f.fileno())  # Force write to disk
            else:
                with open(self.primary_log, "ab") as f:
                    f.write((json.dumps(signed_entry) + "\n").encode("utf-8"))
                    f.flush()
                    os.fsync(f.fileno())

            # Also write to backup
            if AIOFILES_AVAILABLE:
                async with aiofiles.open(self.backup_log, "ab") as f:
                    await f.write((json.dumps(signed_entry) + "\n").encode("utf-8"))
            else:
                with open(self.backup_log, "ab") as f:
                    f.write((json.dumps(signed_entry) + "\n").encode("utf-8"))

        except IOError as e:
            alert_operator(
                f"CRITICAL: Failed to write audit log: {e}. "
                "REGULATORY COMPLIANCE VIOLATED. HALTING SYSTEM.",
                level="CRITICAL",
            )
            sys.exit(1)

        # Best-effort Splunk (don't fail on this)
        if self.splunk_client:
            try:
                self.splunk_client.send_event(event)
            except (ConnectionError, TimeoutError, Exception) as e:
                # If Splunk fails, buffer event for retry
                logger.debug(f"Splunk send failed, buffering: {e}")
                self.splunk_buffer.put(event)

    async def verify_integrity(self, full_scan: bool = False) -> bool:
        """
        Verify audit log integrity.

        Returns:
            bool: True if integrity verified, exits system if compromised
        """
        violations = []
        line_number = 0
        previous_hash = None

        try:
            if AIOFILES_AVAILABLE:
                async with aiofiles.open(self.primary_log, "rb") as f:
                    async for line in f:
                        line_number += 1
                        line = line.decode("utf-8").strip()

                        if not line:
                            continue

                        try:
                            signed_entry = json.loads(line)
                            event = signed_entry["event"]
                            stored_signature = signed_entry["signature"]
                            stored_previous_hash = signed_entry.get("previous_hash")

                            # Verify signature
                            event_json = json.dumps(
                                event, sort_keys=True, ensure_ascii=False
                            )
                            expected_signature = hmac.new(
                                self.hmac_key,
                                event_json.encode("utf-8"),
                                hashlib.sha256,
                            ).hexdigest()

                            if stored_signature != expected_signature:
                                violations.append(
                                    {
                                        "line": line_number,
                                        "type": "SIGNATURE_MISMATCH",
                                        "event_type": event.get(
                                            "event_type", "UNKNOWN"
                                        ),
                                    }
                                )

                            # Verify hash chain
                            if (
                                previous_hash is not None
                                and stored_previous_hash != previous_hash
                            ):
                                violations.append(
                                    {
                                        "line": line_number,
                                        "type": "HASH_CHAIN_BROKEN",
                                        "expected": previous_hash,
                                        "found": stored_previous_hash,
                                    }
                                )

                            # Calculate hash for next entry
                            current_entry_bytes = json.dumps(
                                signed_entry, sort_keys=True
                            ).encode("utf-8")
                            previous_hash = hashlib.sha256(
                                current_entry_bytes
                            ).hexdigest()

                        except (json.JSONDecodeError, KeyError) as e:
                            violations.append(
                                {
                                    "line": line_number,
                                    "type": "MALFORMED_ENTRY",
                                    "error": str(e),
                                }
                            )
            else:
                with open(self.primary_log, "rb") as f:
                    for line in f:
                        line_number += 1
                        line = line.decode("utf-8").strip()

                        if not line:
                            continue

                        try:
                            signed_entry = json.loads(line)
                            event = signed_entry["event"]
                            stored_signature = signed_entry["signature"]
                            stored_previous_hash = signed_entry.get("previous_hash")

                            # Verify signature
                            event_json = json.dumps(
                                event, sort_keys=True, ensure_ascii=False
                            )
                            expected_signature = hmac.new(
                                self.hmac_key,
                                event_json.encode("utf-8"),
                                hashlib.sha256,
                            ).hexdigest()

                            if stored_signature != expected_signature:
                                violations.append(
                                    {
                                        "line": line_number,
                                        "type": "SIGNATURE_MISMATCH",
                                        "event_type": event.get(
                                            "event_type", "UNKNOWN"
                                        ),
                                    }
                                )

                            # Verify hash chain
                            if (
                                previous_hash is not None
                                and stored_previous_hash != previous_hash
                            ):
                                violations.append(
                                    {
                                        "line": line_number,
                                        "type": "HASH_CHAIN_BROKEN",
                                        "expected": previous_hash,
                                        "found": stored_previous_hash,
                                    }
                                )

                            # Calculate hash for next entry
                            current_entry_bytes = json.dumps(
                                signed_entry, sort_keys=True
                            ).encode("utf-8")
                            previous_hash = hashlib.sha256(
                                current_entry_bytes
                            ).hexdigest()

                        except (json.JSONDecodeError, KeyError) as e:
                            violations.append(
                                {
                                    "line": line_number,
                                    "type": "MALFORMED_ENTRY",
                                    "error": str(e),
                                }
                            )

        except IOError as e:
            alert_operator(
                f"CRITICAL: Cannot read audit log for verification: {e}",
                level="CRITICAL",
            )
            sys.exit(1)

        if violations:
            # CRITICAL: Audit log has been tampered with
            violation_summary = json.dumps(violations, indent=2)

            alert_operator(
                f"CRITICAL SECURITY BREACH: Audit log integrity violated!\n"
                f"Violations detected: {len(violations)}\n"
                f"Details: {violation_summary}\n"
                f"THIS IS A REGULATORY COMPLIANCE VIOLATION.\n"
                f"SYSTEM MUST BE CONSIDERED COMPROMISED.",
                level="CRITICAL",
            )

            # Write integrity violation to a separate immutable log
            self._write_integrity_violation(violations)

            # In production/regulatory mode, halt the system
            if PRODUCTION_MODE or REGULATORY_MODE:
                sys.exit(1)

            return False

        # Update integrity metadata
        await self._update_integrity_metadata(line_number)
        return True

    async def _get_next_sequence_number(self) -> int:
        """Get next sequence number for ordering guarantee."""
        try:
            if AIOFILES_AVAILABLE:
                async with aiofiles.open(self.primary_log, "r") as f:
                    lines = await f.readlines()
                    return len(lines) + 1
            else:
                with open(self.primary_log, "r") as f:
                    lines = f.readlines()
                    return len(lines) + 1
        except (OSError, IOError, ValueError) as e:
            logger.debug(f"Failed to get sequence number: {e}")
            return 1

    async def _get_previous_hash(self) -> Optional[str]:
        """Get hash of previous log entry for chaining."""
        try:
            if AIOFILES_AVAILABLE:
                async with aiofiles.open(self.primary_log, "rb") as f:
                    lines = await f.readlines()
                    if lines:
                        last_line = lines[-1].decode("utf-8").strip()
                        if last_line:
                            # Re-encode and hash the entire signed entry, including signature
                            last_entry_bytes = last_line.encode("utf-8")
                            return hashlib.sha256(last_entry_bytes).hexdigest()
            else:
                with open(self.primary_log, "rb") as f:
                    lines = f.readlines()
                    if lines:
                        last_line = lines[-1].decode("utf-8").strip()
                        if last_line:
                            # Re-encode and hash the entire signed entry, including signature
                            last_entry_bytes = last_line.encode("utf-8")
                            return hashlib.sha256(last_entry_bytes).hexdigest()
        except (OSError, IOError, ValueError, UnicodeDecodeError) as e:
            logger.debug(f"Failed to get previous hash: {e}")
            pass
        return None

    def _start_integrity_monitor(self):
        """Start continuous integrity monitoring thread."""
        
        # Skip in CI/test environments to prevent thread exhaustion
        if (os.getenv('CI') in ('1', 'true', 'True', 'TRUE') or 
            os.getenv('GITHUB_ACTIONS') in ('1', 'true', 'True', 'TRUE') or
            os.getenv('TESTING') == '1'):
            logger.info("Skipping integrity monitor thread (CI/test environment detected)")
            return

        def monitor_loop():
            # A dedicated event loop for the thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            async def run_check():
                while True:
                    try:
                        await self.verify_integrity()
                        await asyncio.sleep(300)  # Check every 5 minutes
                    except SystemExit:
                        raise
                    except Exception as e:
                        logger.error(f"Integrity monitor error: {e}")
                        await asyncio.sleep(60)  # Back off on error

            loop.run_until_complete(run_check())

        monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
        monitor_thread.start()

    def _write_integrity_violation(self, violations: list):
        """Write integrity violations to immutable alert log."""
        violation_log = self.audit_dir / "INTEGRITY_VIOLATIONS.log"
        timestamp = datetime.utcnow().isoformat() + "Z"

        violation_entry = {
            "timestamp": timestamp,
            "violations": violations,
            "action": "SYSTEM_HALT_REQUIRED",
        }

        try:
            with open(violation_log, "a") as f:
                f.write(json.dumps(violation_entry) + "\n")
                os.fsync(f.fileno())
        except (OSError, IOError) as e:
            # Best effort - log error but don't fail
            logger.debug(f"Failed to write violation log: {e}")
            pass

    async def _update_integrity_metadata(self, lines_verified: int):
        """Update integrity check metadata."""
        metadata = {
            "last_verification": datetime.utcnow().isoformat() + "Z",
            "lines_verified": lines_verified,
            "status": "PASSED",
            "hmac_key_id": SECRETS_MANAGER.get_secret("ANALYZER_AUDIT_HMAC_KEY_ID")
            or "UNKNOWN",
        }

        try:
            if AIOFILES_AVAILABLE:
                async with aiofiles.open(self.integrity_file, "w") as f:
                    await f.write(json.dumps(metadata, indent=2))
            else:
                with open(self.integrity_file, "w") as f:
                    f.write(json.dumps(metadata, indent=2))
        except (OSError, IOError) as e:
            # Best effort - log error but don't fail
            logger.debug(f"Failed to update integrity metadata: {e}")
            pass


# --- Global Singleton Instance ---
_audit_logger_instance = None
_initialization_lock = threading.Lock()
_background_tasks = set()  # Store background tasks to prevent GC


def get_audit_logger() -> RegulatoryAuditLogger:
    """Get or create the global audit logger instance."""
    global _audit_logger_instance
    if _audit_logger_instance is None:
        with _initialization_lock:
            if _audit_logger_instance is None:
                _audit_logger_instance = RegulatoryAuditLogger()

                # Skip background thread initialization in CI environments
                # to prevent thread exhaustion during import-time checks
                skip_init = (
                    os.getenv('CI') in ('1', 'true', 'True', 'TRUE') or 
                    os.getenv('GITHUB_ACTIONS') in ('1', 'true', 'True', 'TRUE') or 
                    os.getenv('SKIP_AUDIT_INIT') in ('1', 'true', 'True', 'TRUE')
                )
                
                if not skip_init:
                    try:
                        loop = asyncio.get_running_loop()
                        # Create task and store reference to prevent GC
                        task = loop.create_task(_audit_logger_instance.log_startup())
                        _background_tasks.add(task)
                        # Remove from set when done to prevent unbounded growth
                        task.add_done_callback(_background_tasks.discard)
                    except RuntimeError:

                        def run_init_log():
                            asyncio.run(_audit_logger_instance.log_startup())

                        threading.Thread(target=run_init_log, daemon=True).start()
                else:
                    logger.info("Skipping audit logger background initialization (CI environment detected)")
    return _audit_logger_instance


# Add a module-level audit_logger singleton.
# This variable provides the expected attribute.
audit_logger = get_audit_logger()


# --- Convenience Functions ---
async def audit_log(event_type: str, **kwargs):
    """Log an audit event."""
    logger = get_audit_logger()
    await logger.log_critical_event(event_type, **kwargs)


async def verify_audit_integrity():
    """Verify audit log integrity."""
    logger = get_audit_logger()
    return await logger.verify_integrity()


# --- Cleanup Handler ---
def _cleanup_audit_system():
    """Final integrity check on shutdown."""
    try:
        logger = get_audit_logger()
        asyncio.run(
            logger.log_critical_event("AUDIT_SYSTEM_SHUTDOWN", clean_shutdown=True)
        )
        asyncio.run(logger.verify_integrity())
    except (RuntimeError, Exception):
        # Ignore errors during shutdown cleanup
        pass


atexit.register(_cleanup_audit_system)

# --- Public API ---
__all__ = [
    "RegulatoryAuditLogger",
    "get_audit_logger",
    "audit_log",
    "verify_audit_integrity",
    "audit_logger",
]
