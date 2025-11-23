# audit_backends/audit_backend_file_sql.py
import asyncio
import os
import json
import sqlite3
import datetime
import uuid
import shutil  # For robust file copying
import base64  # Explicitly import base64
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional, AsyncIterator

import aiofiles

from .audit_backend_core import (
    LogBackend,
    BACKEND_ERRORS,
    logger,
    send_alert,
    compute_hash,
    ENCRYPTER,
    TamperDetectionError,
    MigrationError,  # Import MigrationError for explicit raising
)


# --- File Backend ---
class FileBackend(LogBackend):
    """File-based backend with atomic writes, WAL for recovery, and explicit schema migration."""

    def _validate_params(self):
        if "log_file" not in self.params or not self.params["log_file"]:
            raise ValueError("log_file parameter is required")
        # Normalize and store paths
        self.log_file = os.path.normpath(self.params["log_file"])
        self.wal_file = os.path.normpath(self.log_file + ".wal")
        self.dir_path = os.path.dirname(self.log_file) or os.path.curdir
        os.makedirs(self.dir_path, exist_ok=True)

    async def start(self):
        """Starts base tasks after WAL recovery."""
        # Recover WAL before starting any background tasks to avoid race conditions
        await self.recover_wal()
        await super().start()
        # FileBackend has no extra init tasks (like connections)

    async def close(self):
        """Closes the FileBackend."""
        # Ensure final batch is flushed before shutdown
        await self.flush_batch()
        await super().close()
        logger.info("FileBackend shutdown complete.")

    async def _append_single(self, prepared_entry: Dict[str, Any]) -> None:
        """
        Appends prepared entry to the WAL file. This is part of the durability mechanism
        before the full atomic batch write is committed to the main log file.
        """
        log_entry_for_wal = json.dumps(prepared_entry) + "\n"
        try:
            async with aiofiles.open(self.wal_file, "a") as wal:
                await wal.write(log_entry_for_wal)
                await wal.flush()
                # Ensure data is synced to disk for durability before atomic commit
                await asyncio.get_event_loop().run_in_executor(None, lambda: os.fsync(wal.fileno()))
        except Exception as e:
            logger.error(
                f"FileBackend WAL write failed for entry {prepared_entry.get('entry_id')}: {e}",
                exc_info=True,
            )
            BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="WALWriteError").inc()
            # Send alert, but this shouldn't stop the main flush flow immediately,
            # as retry_operation is wrapping the flush_batch.
            asyncio.create_task(
                send_alert(
                    "FileBackend WAL write failed. Potential data loss on crash.",
                    severity="high",
                )
            )
            raise  # Re-raise to signal failure to the caller (atomic context/retry logic)

    async def _query_single(self, filters: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
        """
        Queries log file with basic in-memory filtering based on top-level fields.
        Note: This is inefficient for very large files and should be replaced by an indexed solution
        (e.g., Elasticsearch, Splunk) for production scale.
        """
        if not os.path.exists(self.log_file):
            logger.info(f"FileBackend: Log file '{self.log_file}' not found during query.")
            return []

        raw_entries = []
        try:
            async with aiofiles.open(self.log_file, "r") as f:
                # Reading all lines into memory can be problematic for very large files.
                # For a real large-scale scenario, consider streaming reads and external processing.
                lines = await f.readlines()
                for line in reversed(lines):  # Read from end for more recent entries first
                    stripped_line = line.strip()
                    if not stripped_line:
                        continue
                    try:
                        stored_entry = json.loads(stripped_line)
                        # Apply filters on top-level stored fields for efficiency
                        match = True
                        if (
                            "entry_id" in filters
                            and stored_entry.get("entry_id") != filters["entry_id"]
                        ):
                            match = False
                        if (
                            "timestamp >=" in filters
                            and stored_entry.get("timestamp", "") < filters["timestamp >="]
                        ):
                            match = False
                        if (
                            "timestamp <=" in filters
                            and stored_entry.get("timestamp", "") > filters["timestamp <=="]
                        ):
                            match = False
                        if "schema_version" in filters:
                            stored_schema_version = stored_entry.get("schema_version")
                            if (
                                stored_schema_version is None
                                or stored_schema_version != filters["schema_version"]
                            ):
                                match = False

                        if match:
                            raw_entries.append(stored_entry)
                            if len(raw_entries) >= limit:
                                break
                    except json.JSONDecodeError as jde:
                        logger.warning(
                            f"FileBackend: Malformed JSON entry in log file, skipping: '{stripped_line[:100]}...'. Error: {jde}"
                        )
                        BACKEND_ERRORS.labels(
                            backend=self.__class__.__name__, type="MalformedEntry"
                        ).inc()
                    except Exception as parse_e:
                        logger.warning(
                            f"FileBackend: Unexpected error parsing log entry: '{stripped_line[:100]}...'. Error: {parse_e}"
                        )
                        BACKEND_ERRORS.labels(
                            backend=self.__class__.__name__, type="LogParseError"
                        ).inc()

        except IOError as io_e:
            logger.error(
                f"FileBackend: I/O error reading log file '{self.log_file}': {io_e}",
                exc_info=True,
            )
            BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="FileReadError").inc()
            asyncio.create_task(
                send_alert(
                    f"FileBackend: Failed to read log file '{self.log_file}'. Check permissions/disk.",
                    severity="critical",
                )
            )
            raise  # Re-raise to signify query failure
        except Exception as e:
            logger.error(f"FileBackend: Unexpected error during query: {e}", exc_info=True)
            BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="QueryUnknownError").inc()
            raise

        return raw_entries[::-1]

    async def _migrate_schema(self):
        """
        Migrates file-based logs to new schema.
        Handles missing fields, schema_version updates, and provides rollback on failure.
        """
        current_on_disk_version = await self._get_current_schema_version()

        if current_on_disk_version >= self.schema_version:
            logger.info(
                f"FileBackend schema is already at v{self.schema_version} or newer. No migration needed."
            )
            return

        logger.info(
            f"Migrating FileBackend from v{current_on_disk_version} to v{self.schema_version}"
        )

        # Create a unique timestamped backup file
        timestamp_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S")
        backup_file = os.path.normpath(
            f"{self.log_file}.backup.v{current_on_disk_version}.{timestamp_str}"
        )
        temp_new_file = os.path.normpath(f"{self.log_file}.tmp_migrate.{timestamp_str}")

        try:
            # Step 1: Create backup of the original file (preserving metadata)
            if os.path.exists(self.log_file):
                await asyncio.to_thread(shutil.copy2, self.log_file, backup_file)
                logger.info(f"FileBackend migration: Created backup '{backup_file}'.")
            else:
                logger.info(
                    f"FileBackend migration: No existing log file '{self.log_file}' found to backup. Proceeding with empty new file."
                )
                # If no old file, create an empty temp file and then replace it, effectively creating a new log.
                async with aiofiles.open(temp_new_file, "w") as f:
                    await f.write("")
                # We need the file descriptor to fsync, so we can't use aiofiles fully here if fsync is critical
                # Re-opening to get fd for fsync
                fd = await asyncio.to_thread(os.open, temp_new_file, os.O_WRONLY)
                await asyncio.to_thread(os.fsync, fd)
                await asyncio.to_thread(os.close, fd)

                await asyncio.to_thread(
                    os.replace, temp_new_file, self.log_file
                )  # Commit empty file
                return  # No old data to migrate if file didn't exist

            migrated_count = 0
            # Step 2: Read from backup (old data), modify, and write to new temp file
            async with aiofiles.open(backup_file, "r") as old_f:
                # Open the temp file for writing and get its descriptor for fsync
                new_f_handle = await aiofiles.open(temp_new_file, "w")
                try:
                    # --- FIX: Changed loop to be compatible with aiofiles ---
                    line_num = 0
                    async for line in old_f:
                        line_num += 1
                        # --- END FIX ---
                        stripped_line = line.strip()
                        if not stripped_line:
                            continue
                        try:
                            stored_entry = json.loads(stripped_line)

                            encrypted_b64 = stored_entry.get("encrypted_data")
                            if not encrypted_b64:
                                logger.warning(
                                    f"FileBackend migration: Skipping line {line_num} with empty encrypted_data. Line: '{stripped_line[:100]}...'"
                                )
                                BACKEND_ERRORS.labels(
                                    backend=self.__class__.__name__,
                                    type="MigrationEmptyEncData",
                                ).inc()
                                continue  # Skip to next line

                            # --- FIX: Corrected typo from base66 to base64 ---
                            decrypted = self._decrypt(base64.b64decode(encrypted_b64))
                            decompressed = self._decompress(decrypted)
                            original_audit_entry = json.loads(decompressed)

                            if (
                                "entry_id" not in original_audit_entry
                                or not original_audit_entry["entry_id"]
                            ):
                                original_audit_entry["entry_id"] = str(uuid.uuid4())
                                logger.debug(
                                    f"FileBackend migration: Assigned new entry_id for line {line_num}."
                                )

                            if (
                                "timestamp" not in original_audit_entry
                                or not original_audit_entry["timestamp"]
                            ):
                                original_audit_entry["timestamp"] = (
                                    datetime.datetime.now(datetime.timezone.utc).isoformat(
                                        timespec="milliseconds"
                                    )
                                    + "Z"
                                )
                                logger.debug(
                                    f"FileBackend migration: Assigned new timestamp for line {line_num}."
                                )

                            # --- FIX: Update schema version *before* re-calculating hash ---
                            original_audit_entry["schema_version"] = self.schema_version

                            if self.tamper_detection_enabled:
                                temp_audit_entry_for_hash = original_audit_entry.copy()
                                temp_audit_entry_for_hash.pop("_audit_hash", None)
                                new_hash = compute_hash(
                                    json.dumps(temp_audit_entry_for_hash, sort_keys=True).encode(
                                        "utf-8"
                                    )
                                )
                                original_audit_entry["_audit_hash"] = new_hash
                            # --- END FIX ---

                            updated_data_str = json.dumps(original_audit_entry, sort_keys=True)
                            updated_compressed = self._compress(updated_data_str)
                            updated_encrypted = self._encrypt(updated_compressed)
                            updated_base64_data = base64.b64encode(updated_encrypted).decode(
                                "utf-8"
                            )

                            new_stored_entry = {
                                "encrypted_data": updated_base64_data,
                                "entry_id": original_audit_entry["entry_id"],
                                "timestamp": original_audit_entry["timestamp"],
                                "schema_version": self.schema_version,
                                "_audit_hash": original_audit_entry[
                                    "_audit_hash"
                                ],  # Use the *new* hash
                            }
                            await new_f_handle.write(json.dumps(new_stored_entry) + "\n")
                            migrated_count += 1

                        except (
                            json.JSONDecodeError,
                            ValueError,
                            TamperDetectionError,
                        ) as jde:
                            logger.error(
                                f"FileBackend migration: Failed to process line {line_num} due to data corruption/format error: {jde}. Line: '{stripped_line[:100]}...'",
                                exc_info=True,
                            )
                            BACKEND_ERRORS.labels(
                                backend=self.__class__.__name__,
                                type="MigrationDataCorruption",
                            ).inc()
                            asyncio.create_task(
                                send_alert(
                                    f"FileBackend migration encountered corrupted data at line {line_num}. Migration failed.",
                                    severity="critical",
                                )
                            )
                            raise MigrationError(
                                f"Data corruption during migration at line {line_num}: {jde}"
                            )
                        except Exception as migrate_line_e:
                            logger.error(
                                f"FileBackend migration: Unexpected error processing line {line_num}: {migrate_line_e}. Line: '{stripped_line[:100]}...'"
                            )
                            BACKEND_ERRORS.labels(
                                backend=self.__class__.__name__,
                                type="MigrationLineError",
                            ).inc()
                            asyncio.create_task(
                                send_alert(
                                    f"FileBackend migration failed for a line. Error: {migrate_line_e}",
                                    severity="critical",
                                )
                            )
                            raise MigrationError(
                                f"Unexpected error during migration at line {line_num}: {migrate_line_e}"
                            )

                    await new_f_handle.flush()
                    await asyncio.get_event_loop().run_in_executor(
                        None, lambda: os.fsync(new_f_handle.fileno())
                    )

                finally:
                    await new_f_handle.close()  # Ensure file is closed

            # Step 3: Atomically replace the old file with the new one
            await asyncio.to_thread(os.replace, temp_new_file, self.log_file)
            logger.info(
                f"FileBackend migration completed successfully. Migrated {migrated_count} entries to '{self.log_file}'."
            )

            # Step 4: Clean up old WAL and temp migration file
            if os.path.exists(self.wal_file):
                await asyncio.to_thread(os.remove, self.wal_file)
                logger.debug(f"FileBackend migration: Cleared WAL file '{self.wal_file}'.")

            # Keep backup_file for manual inspection/recovery as per doc, but remove temp_new_file
            if os.path.exists(temp_new_file):  # Should have been replaced, but defensive check
                await asyncio.to_thread(os.remove, temp_new_file)

        except MigrationError:  # Re-raise custom migration errors
            raise
        except Exception as e:
            logger.error(f"FileBackend migration failed, attempting rollback: {e}", exc_info=True)
            BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="MigrationFailed").inc()
            asyncio.create_task(
                send_alert(
                    "FileBackend migration failed. Attempting rollback.",
                    severity="critical",
                )
            )

            # Rollback: restore original file from backup
            if os.path.exists(backup_file):
                try:
                    # Remove potentially partially written/corrupted new log file before restoring
                    if os.path.exists(self.log_file):
                        await asyncio.to_thread(os.remove, self.log_file)
                    await asyncio.to_thread(os.rename, backup_file, self.log_file)
                    logger.info(
                        f"FileBackend migration: Rollback successful. Restored '{self.log_file}' from backup."
                    )
                except Exception as rollback_e:
                    logger.critical(
                        f"FileBackend migration rollback failed critically: {rollback_e}",
                        exc_info=True,
                    )
                    asyncio.create_task(
                        send_alert(
                            "CRITICAL: FileBackend migration rollback failed. Data potentially corrupted/lost.",
                            severity="emergency",
                        )
                    )
            else:
                logger.warning(
                    f"FileBackend migration: No backup file '{backup_file}' found for rollback."
                )

            # Clean up any leftover temporary migration file
            if os.path.exists(temp_new_file):
                await asyncio.to_thread(os.remove, temp_new_file)

            raise MigrationError(f"FileBackend migration failed: {e}")

    async def _health_check(self) -> bool:
        """
        Checks file writability and conceptually checks for available disk space.
        """
        try:
            temp_health_file = os.path.normpath(
                f"{self.dir_path}/.health_check_{uuid.uuid4()}"
            )  # Unique name in the directory
            async with aiofiles.open(temp_health_file, "a") as f:
                await f.write("health check\n")
                await f.flush()
                await asyncio.get_event_loop().run_in_executor(None, lambda: os.fsync(f.fileno()))
            await asyncio.to_thread(os.remove, temp_health_file)

            # NOTE: Conceptual check for available disk space removed to avoid platform-specific dependencies.
            # In a real production system, this relies on os.statvfs.

            return True
        except IOError as io_e:
            logger.warning(
                f"FileBackend health check failed due to I/O error (e.g., permissions, disk full): {io_e}"
            )
            BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="HealthCheckIOError").inc()
            return False
        except Exception as e:
            logger.warning(f"FileBackend health check failed unexpectedly: {e}")
            BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="HealthCheckError").inc()
            return False

    async def _get_current_schema_version(self) -> int:
        """Determines the schema version of existing log files."""
        if not os.path.exists(self.log_file):
            return 1  # Assume default if file doesn't exist (new deployment)

        try:
            async with aiofiles.open(self.log_file, "r") as f:
                async for line in f:
                    stripped_line = line.strip()
                    if stripped_line:
                        try:
                            entry = json.loads(stripped_line)
                            return entry.get(
                                "schema_version", 1
                            )  # Default to 1 if not present (older schema)
                        except json.JSONDecodeError as jde:
                            logger.warning(
                                f"FileBackend: Could not parse first line of '{self.log_file}' to determine schema version. Assuming v1. Error: {jde}"
                            )
                            BACKEND_ERRORS.labels(
                                backend=self.__class__.__name__,
                                type="SchemaDetectParseError",
                            ).inc()
                            return 1  # Malformed line, assume it's old and needs migration
            return 1  # If file is empty after reading
        except IOError as io_e:
            logger.warning(
                f"FileBackend: Could not read '{self.log_file}' to determine schema version due to I/O error: {io_e}. Assuming v1."
            )
            BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="SchemaDetectIOError").inc()
            return 1
        except Exception as e:
            logger.warning(
                f"FileBackend: Unexpected error determining schema version for '{self.log_file}': {e}. Assuming v1."
            )
            BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="SchemaDetectError").inc()
            return 1

    @asynccontextmanager
    async def _atomic_context(self, prepared_entries: List[Dict[str, Any]]) -> AsyncIterator[None]:
        """
        Atomic batch writes for FileBackend using a temporary file and `os.replace`.
        Ensures that either the entire batch is committed, or none of it is, for the main log file.
        WAL is a separate durability layer.
        """
        temp_file = os.path.normpath(
            f"{self.dir_path}/.tmp_batch_write_{uuid.uuid4()}"
        )  # Unique temp file for concurrent safety

        try:
            # --- FIX: Write to WAL first for durability ---
            for prepared_entry in prepared_entries:
                await self._append_single(prepared_entry)
            # --- END FIX ---

            # Read existing content for append (atomically).
            existing_content_lines = []
            if os.path.exists(self.log_file):
                try:
                    async with aiofiles.open(self.log_file, "r") as f_read:
                        existing_content_lines = await f_read.readlines()
                except IOError as io_e:
                    logger.warning(
                        f"FileBackend atomic: Could not read existing log file '{self.log_file}'. Proceeding as if empty for this batch. Error: {io_e}"
                    )
                    BACKEND_ERRORS.labels(
                        backend=self.__class__.__name__, type="AtomicReadExistingError"
                    ).inc()

            # For deduplication, collect existing entry_ids from the *main* log file
            existing_entry_ids = set()
            for line in existing_content_lines:
                try:
                    entry = json.loads(line.strip())
                    if "entry_id" in entry:
                        existing_entry_ids.add(entry["entry_id"])
                except json.JSONDecodeError:
                    # Log malformed lines but don't stop the atomic operation.
                    logger.warning(
                        f"FileBackend atomic: Malformed existing line during deduplication: '{line.strip()[:100]}...'. Skipping."
                    )
                    BACKEND_ERRORS.labels(
                        backend=self.__class__.__name__, type="AtomicDedupeParseError"
                    ).inc()

            new_lines_for_file = []
            for prepared_entry in prepared_entries:
                if prepared_entry["entry_id"] not in existing_entry_ids:
                    new_lines_for_file.append(json.dumps(prepared_entry) + "\n")
                    existing_entry_ids.add(prepared_entry["entry_id"])
                else:
                    logger.info(
                        f"FileBackend atomic: Skipping duplicate entry_id '{prepared_entry['entry_id']}' found in batch."
                    )

            # Write all (existing + new unique) data to temp file
            all_lines_to_write = existing_content_lines + new_lines_for_file

            async with aiofiles.open(temp_file, "w") as tmp:
                await tmp.writelines(all_lines_to_write)
                await tmp.flush()
                # Ensure data is written to disk before rename
                await asyncio.get_event_loop().run_in_executor(None, lambda: os.fsync(tmp.fileno()))

            # Atomically replace the main log file
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: os.replace(temp_file, self.log_file)
            )
            logger.debug(
                f"FileBackend: Atomically flushed {len(new_lines_for_file)} new entries to '{self.log_file}'."
            )

            # After a successful atomic write, clear the WAL file as its contents are now in the main log.
            if os.path.exists(self.wal_file):
                try:
                    await asyncio.to_thread(os.remove, self.wal_file)
                    logger.debug(f"FileBackend: Cleared WAL file '{self.wal_file}'.")
                except IOError as io_e:
                    logger.warning(
                        f"FileBackend: Failed to clear WAL file '{self.wal_file}': {io_e}. Will retry later or require manual cleanup."
                    )
                    BACKEND_ERRORS.labels(
                        backend=self.__class__.__name__, type="WALCleanupError"
                    ).inc()

            yield  # Allow the block to execute successfully

        except Exception as e:
            logger.error(f"FileBackend atomic batch write failed: {e}", exc_info=True)
            BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="AtomicWriteError").inc()
            asyncio.create_task(
                send_alert(
                    "FileBackend atomic batch write failed. Data might be inconsistent or lost for batch.",
                    severity="high",
                )
            )

            # Clean up partial temp file on failure
            if os.path.exists(temp_file):
                try:
                    await asyncio.to_thread(os.remove, temp_file)
                    logger.debug(f"FileBackend: Cleaned up failed temp file '{temp_file}'.")
                except Exception as cleanup_e:
                    logger.error(
                        f"FileBackend: Failed to cleanup temp file '{temp_file}' after atomic write failure: {cleanup_e}",
                        exc_info=True,
                    )
            raise  # Re-raise to propagate the error

    async def recover_wal(self):
        """
        Recovers from WAL after a crash. Reads unique entries from WAL and appends them
        to the main log file, then clears the WAL.
        """
        if not os.path.exists(self.wal_file):
            logger.info("FileBackend WAL file not found. No recovery needed.")
            return

        logger.info(f"FileBackend: Attempting WAL recovery from '{self.wal_file}'.")

        wal_entries = []
        try:
            async with aiofiles.open(self.wal_file, "r") as wal_f:
                # --- FIX: Changed loop to be compatible with aiofiles ---
                line_num = 0
                async for line in wal_f:
                    line_num += 1
                    # --- END FIX ---
                    stripped_line = line.strip()
                    if not stripped_line:
                        continue
                    try:
                        wal_entries.append(json.loads(stripped_line))
                    except json.JSONDecodeError as jde:
                        logger.warning(
                            f"FileBackend WAL recovery: Malformed JSON entry in WAL file at line {line_num}, skipping. Error: {jde}. Line: '{stripped_line[:100]}...'"
                        )
                        BACKEND_ERRORS.labels(
                            backend=self.__class__.__name__, type="WALMalformedEntry"
                        ).inc()
                        # Consider moving malformed WAL to a quarantine folder for forensics.
                        # For now, just skip.
        except IOError as io_e:
            logger.error(
                f"FileBackend WAL recovery: I/O error reading WAL file '{self.wal_file}': {io_e}",
                exc_info=True,
            )
            BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="WALReadError").inc()
            asyncio.create_task(
                send_alert(
                    "FileBackend WAL read failed during recovery. Potential data loss.",
                    severity="high",
                )
            )
            return  # Cannot proceed if WAL cannot be read

        if not wal_entries:
            logger.info(
                "FileBackend WAL file is empty or contains no valid entries after parsing. No recovery action taken."
            )
            try:
                await asyncio.to_thread(os.remove, self.wal_file)  # Clean empty/invalid WAL
            except IOError as io_e:
                logger.warning(
                    f"FileBackend WAL recovery: Failed to remove empty WAL file '{self.wal_file}': {io_e}."
                )
            return

        existing_log_entries = []
        if os.path.exists(self.log_file):
            try:
                async with aiofiles.open(self.log_file, "r") as log_f:
                    async for line in log_f:
                        stripped_line = line.strip()
                        if stripped_line:
                            try:
                                existing_log_entries.append(json.loads(stripped_line))
                            except json.JSONDecodeError as jde:
                                logger.warning(
                                    f"FileBackend WAL recovery: Malformed main log entry during deduplication, skipping. Error: {jde}. Line: '{stripped_line[:100]}...'"
                                )
                                BACKEND_ERRORS.labels(
                                    backend=self.__class__.__name__,
                                    type="MainLogMalformedEntry",
                                ).inc()
            except IOError as io_e:
                logger.error(
                    f"FileBackend WAL recovery: I/O error reading main log for deduplication: {io_e}",
                    exc_info=True,
                )
                BACKEND_ERRORS.labels(
                    backend=self.__class__.__name__, type="MainLogReadError"
                ).inc()
                asyncio.create_task(
                    send_alert(
                        "FileBackend main log read failed during WAL recovery deduplication. Data might contain duplicates.",
                        severity="medium",
                    )
                )

        existing_entry_ids = {
            entry.get("entry_id") for entry in existing_log_entries if "entry_id" in entry
        }

        new_entries_from_wal_to_add = []
        for entry in wal_entries:
            if entry.get("entry_id") and entry["entry_id"] not in existing_entry_ids:
                new_entries_from_wal_to_add.append(entry)
                existing_entry_ids.add(entry["entry_id"])
            else:
                logger.debug(
                    f"FileBackend WAL recovery: Skipping duplicate entry_id '{entry.get('entry_id')}' found in WAL."
                )

        if new_entries_from_wal_to_add:
            try:
                temp_recovery_file = os.path.normpath(
                    f"{self.dir_path}/.tmp_recovery_{uuid.uuid4()}"
                )  # Unique temp for recovery

                all_lines_for_recovery = [json.dumps(e) + "\n" for e in existing_log_entries] + [
                    json.dumps(e) + "\n" for e in new_entries_from_wal_to_add
                ]

                async with aiofiles.open(temp_recovery_file, "w") as tmp_f:
                    await tmp_f.writelines(all_lines_for_recovery)
                    await tmp_f.flush()
                    await asyncio.get_event_loop().run_in_executor(
                        None, lambda: os.fsync(tmp_f.fileno())
                    )

                await asyncio.get_event_loop().run_in_executor(
                    None, lambda: os.replace(temp_recovery_file, self.log_file)
                )
                logger.info(
                    f"FileBackend WAL recovery: Successfully appended {len(new_entries_from_wal_to_add)} unique entries to '{self.log_file}'."
                )

                try:
                    await asyncio.to_thread(os.remove, self.wal_file)
                    logger.info(
                        f"FileBackend WAL file '{self.wal_file}' cleared after successful recovery."
                    )
                except IOError as io_e:
                    logger.warning(
                        f"FileBackend WAL recovery: Failed to remove WAL file '{self.wal_file}': {io_e}. Manual cleanup may be required."
                    )
                    BACKEND_ERRORS.labels(
                        backend=self.__class__.__name__, type="WALCleanupError"
                    ).inc()

            except Exception as recover_write_e:
                logger.error(
                    f"FileBackend WAL recovery failed during atomic write: {recover_write_e}",
                    exc_info=True,
                )
                BACKEND_ERRORS.labels(
                    backend=self.__class__.__name__, type="WALRecoveryWriteError"
                ).inc()
                asyncio.create_task(
                    send_alert(
                        "CRITICAL: FileBackend WAL recovery write failed. Data inconsistency likely.",
                        severity="emergency",
                    )
                )
                if os.path.exists(temp_recovery_file):
                    try:
                        await asyncio.to_thread(os.remove, temp_recovery_file)
                    except Exception as cleanup_e:
                        logger.error(
                            f"FileBackend: Failed to cleanup temp recovery file after failure: {cleanup_e}",
                            exc_info=True,
                        )
        else:
            logger.info(
                "FileBackend WAL recovery: No new unique entries to add to main log or WAL was empty."
            )
            try:
                await asyncio.to_thread(os.remove, self.wal_file)
            except IOError as io_e:
                logger.warning(
                    f"FileBackend WAL recovery: Failed to remove empty WAL file '{self.wal_file}': {io_e}."
                )


# --- SQLite Backend ---
class SQLiteBackend(LogBackend):
    """SQLite backend with ACID transactions and migrations."""

    def _validate_params(self):
        if "db_file" not in self.params or not self.params["db_file"]:
            raise ValueError("db_file parameter is required")
        self.db_file = os.path.normpath(self.params["db_file"])
        self.dir_path = os.path.dirname(self.db_file) or os.path.curdir
        os.makedirs(self.dir_path, exist_ok=True)

    def __init__(self, params: Dict[str, Any]):
        super().__init__(params)
        self.conn: Optional[sqlite3.Connection] = None
        # --- FIX: Task creation moved to start() ---
        # self._init_conn_task = asyncio.create_task(self._init_connection())
        # self._async_tasks.add(self._init_conn_task)
        # self._init_conn_task.add_done_callback(self._async_tasks.discard)

    # --- START: FIX (Moved task creation from __init__ to start) ---
    async def start(self):
        """Initializes connection and starts base tasks."""
        # Initialize connection *before* starting base tasks (like migration)
        loop = asyncio.get_running_loop()
        self._init_conn_task = loop.create_task(self._init_connection())
        self._async_tasks.add(self._init_conn_task)
        self._init_conn_task.add_done_callback(self._async_tasks.discard)

        await self.wait_for_init()  # Wait for connection to be ready

        # Now start the base tasks (migrate, flush, health)
        await super().start()

    # --- END: FIX ---

    async def close(self):
        """Closes the SQLite connection and shuts down base tasks."""
        # Ensure final batch is flushed
        await self.flush_batch()

        # First, shut down base tasks (flush, health, etc.)
        await super().close()

        # Now, close the connection
        if self.conn:
            try:
                await asyncio.to_thread(self.conn.close)
                logger.info("SQLiteBackend connection closed.")
                self.conn = None
            except sqlite3.Error as e:
                logger.error(f"Error closing SQLiteBackend connection: {e}", exc_info=True)
                BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="CloseError").inc()

    async def _init_connection(self):
        """Initializes SQLite connection."""

        def connect_sync():
            try:
                conn = sqlite3.connect(self.db_file, check_same_thread=False)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA synchronous=NORMAL;")
                # Note: Schema version is dynamically created in _migrate_schema if needed.
                conn.commit()
                return conn
            except sqlite3.Error as db_e:
                logger.critical(
                    f"SQLiteBackend: Database error during connection: {db_e}",
                    exc_info=True,
                )
                raise
            except Exception as e:
                logger.critical(
                    f"SQLiteBackend: Unexpected error during connection: {e}",
                    exc_info=True,
                )
                raise

        try:
            self.conn = await asyncio.to_thread(connect_sync)
            logger.info(f"SQLiteBackend initialized for '{self.db_file}'.")
        except Exception as e:
            logger.critical(f"SQLiteBackend initialization failed: {e}", exc_info=True)
            BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="InitError").inc()
            asyncio.create_task(
                send_alert(f"SQLiteBackend initialization failed: {e}", severity="critical")
            )
            raise

    async def _append_single(self, prepared_entry: Dict[str, Any]) -> None:
        """Inserts single prepared entry with deduplication."""
        if self.conn is None:
            await self.wait_for_init()  # Wait for connection to be ready

        try:
            # logs_v{SCHEMA_VERSION} ensures we write to the currently active table
            await asyncio.to_thread(
                self.conn.execute,
                f"INSERT OR IGNORE INTO logs_v{self.schema_version} (entry_id, data, timestamp, schema_version, _audit_hash) VALUES (?, ?, ?, ?, ?)",
                (
                    prepared_entry["entry_id"],
                    prepared_entry["encrypted_data"],
                    prepared_entry["timestamp"],
                    prepared_entry["schema_version"],
                    prepared_entry["_audit_hash"],
                ),
            )
            # No commit here; commit is handled by the _atomic_context (transaction)
        except sqlite3.Error as db_e:
            logger.error(
                f"SQLiteBackend: Database error during append for entry '{prepared_entry.get('entry_id')}': {db_e}",
                exc_info=True,
            )
            BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="AppendDBError").inc()
            raise  # Re-raise to trigger transaction rollback
        except Exception as e:
            logger.error(
                f"SQLiteBackend: Unexpected error during append for entry '{prepared_entry.get('entry_id')}': {e}",
                exc_info=True,
            )
            BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="AppendError").inc()
            raise

    async def _query_single(self, filters: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
        """Queries SQLite with timestamp, entry_id, and schema_version filtering."""
        if self.conn is None:
            await self.wait_for_init()

        # Query the currently active table for the schema version
        query = f"SELECT entry_id, data, timestamp, schema_version, _audit_hash FROM logs_v{self.schema_version}"
        where_clauses = []
        values = []

        if "timestamp >=" in filters:
            where_clauses.append("timestamp >= ?")
            values.append(filters["timestamp >="])
        if "timestamp <=" in filters:
            where_clauses.append("timestamp <= ?")
            values.append(filters["timestamp <=="])
        if "entry_id" in filters:
            where_clauses.append("entry_id = ?")
            values.append(filters["entry_id"])
        if "schema_version" in filters:
            where_clauses.append("schema_version = ?")
            values.append(filters["schema_version"])

        if where_clauses:
            query += " WHERE " + " AND ".join(where_clauses)
        query += f" ORDER BY id DESC LIMIT {limit}"

        try:
            rows_cursor = await asyncio.to_thread(self.conn.execute, query, tuple(values))
            fetched_rows = await asyncio.to_thread(rows_cursor.fetchall)
            return [
                {
                    "encrypted_data": row["data"],
                    "entry_id": row["entry_id"],
                    "timestamp": row["timestamp"],
                    "schema_version": row["schema_version"],
                    "_audit_hash": row["_audit_hash"],
                }
                for row in fetched_rows
            ]
        except sqlite3.Error as db_e:
            logger.error(f"SQLiteBackend: Database error during query: {db_e}", exc_info=True)
            BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="QueryDBError").inc()
            asyncio.create_task(
                send_alert(
                    f"SQLiteBackend query failed due to database error: {db_e}",
                    severity="high",
                )
            )
            raise  # Re-raise to signal query failure
        except Exception as e:
            logger.error(f"SQLiteBackend: Unexpected error during query: {e}", exc_info=True)
            BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="QueryError").inc()
            asyncio.create_task(
                send_alert(f"SQLiteBackend query failed unexpectedly: {e}", severity="high")
            )
            raise

    async def _migrate_schema(self):
        """
        Migrates SQLite schema, including new columns and data transformation.
        Uses a transactional approach with a temporary table for atomicity and rollback.
        """
        # Wait for connection to be established before trying to migrate
        await self.wait_for_init()

        current_on_disk_version = await self._get_current_schema_version()
        if current_on_disk_version >= self.schema_version:
            logger.info(
                f"SQLiteBackend schema is already at v{current_on_disk_version} "
                f"(target v{self.schema_version}). No migration needed."
            )
            return

        logger.info(
            f"SQLiteBackend: Starting migration from v{current_on_disk_version} to v{self.schema_version}."
        )

        # --- FIX: Changed 'async def' to 'def' ---
        def migrate_sync_logic():
            # --- FIX: Changed 'await self._init_connection()' to a sync check ---
            if self.conn is None:
                # This should be impossible after wait_for_init(), but safety check
                raise ConnectionError("SQLiteBackend connection not initialized for migration.")
            # --- END FIX ---

            cursor = self.conn.cursor()

            # Create the new table name
            new_table_name = f"logs_v{self.schema_version}"
            temp_table_name = f"{new_table_name}_tmp"
            old_table_name = f"logs_v{current_on_disk_version}"

            cursor.execute("BEGIN TRANSACTION")
            try:
                # 1. Create the new table schema (temporary name)
                cursor.execute(
                    f"""
                    CREATE TABLE {temp_table_name} (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                        entry_id TEXT UNIQUE NOT NULL,
                        schema_version INTEGER NOT NULL,
                        _audit_hash TEXT NOT NULL,
                        data TEXT NOT NULL
                    )
                """
                )
                logger.debug(
                    f"SQLiteBackend migration: Created temporary table '{temp_table_name}'."
                )

                # 2. Migrate data from old schema tables to the new temporary table.
                if old_table_name != temp_table_name:
                    cursor.execute(
                        f"SELECT name FROM sqlite_master WHERE type='table' AND name='{old_table_name}'"
                    )
                    old_table_exists = cursor.fetchone()
                else:
                    old_table_exists = (
                        False  # Old table is the same as the target (already migrated)
                    )

                migrated_count = 0
                if old_table_exists:
                    logger.info(
                        f"SQLiteBackend migration: Copying data from '{old_table_name}' to new schema."
                    )
                    # Select all data from the old table
                    # Ensure we select all columns that existed, even if we don't use them all
                    old_rows_cursor = cursor.execute(f"SELECT * FROM {old_table_name}")

                    for row in old_rows_cursor:
                        # Data is already encrypted/compressed/base64 encoded from old table
                        encrypted_data_b64 = row["data"]

                        # Decrypt, decompress to get original audit entry
                        decrypted_bytes = ENCRYPTER.decrypt(base64.b64decode(encrypted_data_b64))
                        decompressed_str = self._decompress(decrypted_bytes)
                        original_audit_entry = json.loads(decompressed_str)

                        # Apply migration rules: Ensure all new fields are present/updated
                        entry_id = original_audit_entry.get(
                            "entry_id",
                            (row["entry_id"] if "entry_id" in row.keys() else str(uuid.uuid4())),
                        )
                        timestamp = original_audit_entry.get(
                            "timestamp",
                            (
                                row["timestamp"]
                                if "timestamp" in row.keys()
                                else datetime.datetime.now(datetime.timezone.utc).isoformat(
                                    timespec="milliseconds"
                                )
                                + "Z"
                            ),
                        )

                        original_audit_entry["entry_id"] = entry_id
                        original_audit_entry["timestamp"] = timestamp

                        # --- FIX: Update schema version *before* re-calculating hash ---
                        original_audit_entry["schema_version"] = self.schema_version

                        # Recalculate hash and update schema version for the migrated *content*
                        if self.tamper_detection_enabled:
                            temp_audit_entry_for_hash = original_audit_entry.copy()
                            temp_audit_entry_for_hash.pop("_audit_hash", None)
                            audit_hash = compute_hash(
                                json.dumps(temp_audit_entry_for_hash, sort_keys=True).encode(
                                    "utf-8"
                                )
                            )
                            original_audit_entry["_audit_hash"] = audit_hash
                        else:
                            original_audit_entry.pop("_audit_hash", None)
                            audit_hash = ""
                        # --- END FIX ---

                        # Re-encrypt and re-compress the updated audit log entry with current key
                        updated_data_str = json.dumps(original_audit_entry, sort_keys=True)
                        updated_compressed = self._compress(updated_data_str)
                        updated_encrypted = self._encrypt(updated_compressed)
                        updated_base64_data = base64.b64encode(updated_encrypted).decode("utf-8")

                        cursor.execute(
                            f"INSERT INTO {temp_table_name} (timestamp, entry_id, schema_version, _audit_hash, data) VALUES (?, ?, ?, ?, ?)",
                            (
                                timestamp,
                                entry_id,
                                self.schema_version,
                                audit_hash,
                                updated_base64_data,
                            ),
                        )
                        migrated_count += 1

                    logger.info(
                        f"SQLiteBackend migration: Copied {migrated_count} entries from '{old_table_name}'."
                    )
                    cursor.execute(f"DROP TABLE {old_table_name}")
                    logger.debug(f"SQLiteBackend migration: Dropped old table '{old_table_name}'.")
                else:
                    logger.info(
                        "SQLiteBackend migration: No old schema table found to copy data from."
                    )

                # 3. Rename the temporary table to the final table name
                cursor.execute(f"ALTER TABLE {temp_table_name} RENAME TO {new_table_name}")
                logger.debug(
                    f"SQLiteBackend migration: Renamed '{temp_table_name}' to '{new_table_name}'."
                )

                # 4. Recreate indexes for the new table
                cursor.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{new_table_name}_timestamp ON {new_table_name} (timestamp DESC);"
                )
                cursor.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{new_table_name}_entry_id ON {new_table_name} (entry_id);"
                )
                logger.debug(f"SQLiteBackend migration: Recreated indexes for '{new_table_name}'.")

                # 5. Validate database integrity after migration (important for robustness)
                cursor.execute("PRAGMA integrity_check;")
                integrity_result = cursor.fetchone()
                if integrity_result and integrity_result[0] != "ok":
                    raise MigrationError(
                        f"Database integrity check failed after migration: {integrity_result[0]}"
                    )
                logger.info("SQLiteBackend migration: Database integrity check passed.")

                cursor.execute("COMMIT")
                logger.info(
                    f"SQLiteBackend migration completed successfully to v{self.schema_version}."
                )

            except sqlite3.Error as db_e:
                cursor.execute("ROLLBACK")
                logger.error(
                    f"SQLiteBackend migration failed due to database error: {db_e}",
                    exc_info=True,
                )
                BACKEND_ERRORS.labels(
                    backend=self.__class__.__name__, type="MigrationDBError"
                ).inc()
                asyncio.create_task(
                    send_alert(
                        f"SQLiteBackend migration failed due to DB error: {db_e}. Rolling back.",
                        severity="critical",
                    )
                )
                cursor.execute(
                    f"DROP TABLE IF EXISTS {temp_table_name}"
                )  # Clean up temp table on rollback
                raise MigrationError(f"SQLite migration failed due to database error: {db_e}")
            except Exception as e:
                cursor.execute("ROLLBACK")
                logger.error(f"SQLiteBackend migration failed unexpectedly: {e}", exc_info=True)
                BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="MigrationFailed").inc()
                asyncio.create_task(
                    send_alert(
                        f"SQLiteBackend migration failed unexpectedly: {e}. Rolling back.",
                        severity="critical",
                    )
                )
                cursor.execute(
                    f"DROP TABLE IF EXISTS {temp_table_name}"
                )  # Clean up temp table on rollback
                raise MigrationError(f"SQLite migration failed: {e}")

        await asyncio.to_thread(migrate_sync_logic)

    async def _health_check(self) -> bool:
        """Checks SQLite connectivity and database integrity."""
        try:
            if self.conn is None:
                await self.wait_for_init()

            # Simple connection test
            await asyncio.to_thread(self.conn.execute, "SELECT 1;")

            # More robust: check database integrity
            integrity_check_result = await asyncio.to_thread(
                self.conn.execute, "PRAGMA integrity_check;"
            )
            result = await asyncio.to_thread(integrity_check_result.fetchone)
            if result and result[0] != "ok":
                logger.warning(
                    f"SQLiteBackend health check: Database integrity check failed: {result[0]}"
                )
                BACKEND_ERRORS.labels(
                    backend=self.__class__.__name__, type="IntegrityCheckFailed"
                ).inc()
                asyncio.create_task(
                    send_alert(
                        "SQLiteBackend database integrity check failed. DB may be corrupted.",
                        severity="high",
                    )
                )
                return False

            return True
        except sqlite3.Error as db_e:
            logger.warning(f"SQLiteBackend health check failed due to database error: {db_e}")
            BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="HealthCheckDBError").inc()
            return False
        except Exception as e:
            logger.warning(f"SQLiteBackend health check failed unexpectedly: {e}")
            BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="HealthCheckError").inc()
            return False

    async def _get_current_schema_version(self) -> int:
        """Determines the current schema version from the SQLite database."""
        _conn_was_none = False
        if self.conn is None:
            _conn_was_none = True
            try:
                # Try to connect passively just for schema version check
                temp_conn = await asyncio.to_thread(
                    lambda: sqlite3.connect(self.db_file, check_same_thread=False)
                )
                temp_conn.row_factory = sqlite3.Row  # Ensure row_factory is set for temp_conn
                cursor = await asyncio.to_thread(temp_conn.cursor)
            except sqlite3.Error as db_e:
                logger.warning(
                    f"SQLiteBackend: Could not connect to '{self.db_file}' to determine schema version. Assuming v1. Error: {db_e}"
                )
                BACKEND_ERRORS.labels(
                    backend=self.__class__.__name__, type="SchemaDetectDBError"
                ).inc()
                return 1
            except Exception as e:
                logger.warning(
                    f"SQLiteBackend: Unexpected error connecting to '{self.db_file}' for schema version. Assuming v1. Error: {e}"
                )
                BACKEND_ERRORS.labels(
                    backend=self.__class__.__name__, type="SchemaDetectError"
                ).inc()
                return 1
        else:
            cursor = await asyncio.to_thread(self.conn.cursor)

        try:
            # Check for logs_v{SCHEMA_VERSION} first (current target)
            cursor.execute(
                f"SELECT name FROM sqlite_master WHERE type='table' AND name='logs_v{self.schema_version}';"
            )
            if await asyncio.to_thread(cursor.fetchone):
                return self.schema_version

            # Check for logs_v1 (base version)
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='logs_v1';")
            if await asyncio.to_thread(cursor.fetchone):
                return 1  # Found logs_v1, so it's version 1

            return 1  # No known schema tables found, assume base version 1
        except sqlite3.Error as db_e:
            logger.warning(
                f"SQLiteBackend: Database error checking schema version: {db_e}. Assuming v1."
            )
            BACKEND_ERRORS.labels(
                backend=self.__class__.__name__, type="SchemaDetectQueryError"
            ).inc()
            return 1
        finally:
            if _conn_was_none and "temp_conn" in locals() and temp_conn:
                await asyncio.to_thread(temp_conn.close)

    @asynccontextmanager
    async def _atomic_context(self, prepared_entries: List[Dict[str, Any]]) -> AsyncIterator[None]:
        """
        Manages SQLite transactions for a batch of prepared entries.
        Ensures all entries in the batch are committed together or none are.
        """
        if self.conn is None:
            await self.wait_for_init()

        try:
            await asyncio.to_thread(self.conn.execute, "BEGIN TRANSACTION;")
            for prepared_entry in prepared_entries:
                await self._append_single(prepared_entry)
            await asyncio.to_thread(self.conn.commit)
            logger.debug(
                f"SQLiteBackend: Transaction committed successfully for {len(prepared_entries)} entries."
            )
            yield  # Success
        except Exception as e:
            await asyncio.to_thread(self.conn.rollback)
            logger.error(
                f"SQLiteBackend transaction rolled back due to error: {e}",
                exc_info=True,
            )
            BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="TransactionRollback").inc()
            asyncio.create_task(
                send_alert(
                    "SQLiteBackend transaction rolled back. Data inconsistency possible for batch.",
                    severity="high",
                )
            )
            raise  # Re-raise to signal failure to higher-level retry logic

    async def recover_wal(self):
        """
        Recovers from SQLite WAL.
        In SQLite with `journal_mode=WAL`, recovery is automatic upon connecting to the database
        after a crash. This method primarily serves as a confirmation log.
        """
        logger.info(
            f"SQLiteBackend: WAL recovery is handled automatically by PRAGMA journal_mode=WAL upon connection to '{self.db_file}'."
        )

    async def wait_for_init(self):
        """Waits for the async _init_connection task to complete."""
        if self._init_conn_task and not self._init_conn_task.done():
            await self._init_conn_task
        if self.conn is None:
            raise ConnectionError("SQLiteBackend connection failed to initialize.")
