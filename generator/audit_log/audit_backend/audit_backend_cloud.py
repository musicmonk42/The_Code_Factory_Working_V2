# audit_backends/audit_backend_cloud.py
# S3, GCS, and AzureBlob audit backends for the audit platform.

import asyncio
import base64
import datetime
import json
import zlib
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional, AsyncIterator

import boto3
import botocore.exceptions
import google.cloud.storage as gcs
import aiohttp # Added for Azure SDK's potential HTTP client usage implicitly
from azure.storage.blob.aio import BlobServiceClient # For Azure Blob Storage
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError, AzureError # For Azure error handling
from azure.storage.blob import ContentSettings # For content_settings in upload_blob
from cryptography.fernet import InvalidToken # Needed for decryption errors

from .audit_backend_core import (
    LogBackend, SCHEMA_VERSION, BACKEND_ERRORS, logger, send_alert, retry_operation,
    compute_hash, ENCRYPTER, COMPRESSION_ALGO, COMPRESSION_LEVEL, MigrationError,
    _STATUS_OK, _STATUS_ERROR
)

# --- S3 Backend ---
class S3Backend(LogBackend):
    """S3 backend with batch writes, Athena integration, and explicit schema migration."""
    def _validate_params(self):
        if "bucket" not in self.params:
            raise ValueError("bucket parameter is required")
        if "athena_results_location" not in self.params:
            raise ValueError("athena_results_location parameter is required for Athena queries")

        self.bucket = self.params["bucket"]
        self.key_prefix = self.params.get("key_prefix", "audit_logs/")
        self.athena_database = self.params.get("athena_database", "audit_db")
        self.athena_table = self.params.get("athena_table", "audit_logs")
        self.athena_results_location = self.params["athena_results_location"]
        if not self.key_prefix.endswith('/'):
            self.key_prefix += '/'

        self.old_key_prefix = self.params.get("old_key_prefix", f"audit_logs_v{SCHEMA_VERSION - 1}/")


    def __init__(self, params: Dict[str, Any]):
        super().__init__(params)
        self.s3_client = boto3.client("s3")
        self.athena_client = boto3.client("athena")
        asyncio.create_task(self._init_athena())


    async def _init_athena(self):
        """Initializes Athena database and table for querying."""
        try:
            # 1. Create Database
            await retry_operation(
                lambda: asyncio.to_thread(self.athena_client.start_query_execution,
                                        QueryString=f"CREATE DATABASE IF NOT EXISTS {self.athena_database}",
                                        ResultConfiguration={"OutputLocation": self.athena_results_location}),
                backend_name=self.__class__.__name__, op_name="athena_create_db"
            )

            # 2. Create Table
            query = f"""
                CREATE EXTERNAL TABLE IF NOT EXISTS {self.athena_database}.{self.athena_table} (
                    entry_id STRING,
                    encrypted_data STRING,
                    timestamp STRING,
                    schema_version INT,
                    _audit_hash STRING
                )
                PARTITIONED BY (year STRING, month STRING, day STRING)
                ROW FORMAT DELIMITED
                FIELDS TERMINATED BY '\n'
                STORED AS TEXTFILE
                LOCATION 's3://{self.bucket}/{self.key_prefix}'
            """

            response = await retry_operation(
                lambda: asyncio.to_thread(self.athena_client.start_query_execution,
                                        QueryString=query,
                                        ResultConfiguration={"OutputLocation": self.athena_results_location}),
                backend_name=self.__class__.__name__, op_name="athena_create_table"
            )
            query_execution_id = response['QueryExecutionId']

            while True:
                status = await retry_operation(
                    lambda: asyncio.to_thread(self.athena_client.get_query_execution, QueryExecutionId=query_execution_id),
                    backend_name=self.__class__.__name__, op_name="athena_get_status"
                )
                state = status['QueryExecution']['Status']['State']
                if state in ['SUCCEEDED', 'FAILED', 'CANCELLED']:
                    break
                await asyncio.sleep(1)

            if state != 'SUCCEEDED':
                raise RuntimeError(f"Athena table initialization failed with state: {state}. Reason: {status['QueryExecution']['Status'].get('StateChangeReason', 'N/A')}")

            logger.info(f"Athena table '{self.athena_database}.{self.athena_table}' initialized.")
        except Exception as e:
            logger.critical(f"Athena initialization failed: {e}", exc_info=True)
            BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="AthenaInitError").inc()
            asyncio.create_task(send_alert(f"Athena initialization failed for S3Backend: {e}", severity="critical"))
            raise


    async def _append_single(self, prepared_entry: Dict[str, Any]) -> None:
        """
        No-op for S3Backend as the _atomic_context handles batch writing directly from the prepared_entries list.
        """
        pass


    async def _query_single(self, filters: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
        """Queries S3 using Athena with partitioning and column filtering."""
        if self.athena_client is None:
            await self._init_athena()

        query_columns = "entry_id, encrypted_data, timestamp, schema_version, _audit_hash"
        query = f"SELECT {query_columns} FROM {self.athena_database}.{self.athena_table}"
        where_clauses = []

        current_utc_dt = datetime.datetime.now(datetime.timezone.utc)
        
        # Determine Partition Predicates
        start_ts_filter = filters.get("timestamp >=")
        end_ts_filter = filters.get("timestamp <=")

        partition_preds = []
        try:
            # Safely parse dates for partition checks, fallback to 7 days for safety
            start_date = datetime.datetime.fromisoformat(start_ts_filter).astimezone(datetime.timezone.utc).date() if start_ts_filter else (current_utc_dt - datetime.timedelta(days=7)).date()
            end_date = datetime.datetime.fromisoformat(end_ts_filter).astimezone(datetime.timezone.utc).date() if end_ts_filter else current_utc_dt.date()
        except ValueError:
            logger.warning("Invalid timestamp format in filters. Defaulting partition check to last 7 days.")
            start_date = (current_utc_dt - datetime.timedelta(days=7)).date()
            end_date = current_utc_dt.date()


        delta = end_date - start_date
        for i in range(delta.days + 1):
            date = start_date + datetime.timedelta(days=i)
            partition_preds.append(f"(year = '{date.year}' AND month = '{date.month:02d}' AND day = '{date.day:02d}')")

        if partition_preds:
            where_clauses.append(f"({' OR '.join(partition_preds)})")

        # Add other filters
        if "entry_id" in filters:
            where_clauses.append(f"entry_id = '{filters['entry_id']}'")
        if "schema_version" in filters:
            where_clauses.append(f"schema_version = {filters['schema_version']}")
        if "timestamp >=" in filters:
            where_clauses.append(f"timestamp >= '{filters['timestamp >=']}'")
        if "timestamp <=" in filters:
            where_clauses.append(f"timestamp <= '{filters['timestamp <=']}'")

        if where_clauses:
            query += " WHERE " + " AND ".join(where_clauses)

        query += f" ORDER BY timestamp DESC LIMIT {limit}" # Order by timestamp descending (most recent first)

        try:
            # 1. Start Query Execution
            response = await retry_operation(
                lambda: asyncio.to_thread(self.athena_client.start_query_execution,
                                        QueryString=query,
                                        ResultConfiguration={"OutputLocation": self.athena_results_location}),
                backend_name=self.__class__.__name__, op_name="athena_start_query"
            )
            query_id = response["QueryExecutionId"]

            # 2. Poll Status
            while True:
                status = await retry_operation(
                    lambda: asyncio.to_thread(self.athena_client.get_query_execution, QueryExecutionId=query_id),
                    backend_name=self.__class__.__name__, op_name="athena_get_query_status"
                )
                state = status['QueryExecution']['Status']['State']
                if state in ['SUCCEEDED', 'FAILED', 'CANCELLED']:
                    break
                await asyncio.sleep(1)

            if state != 'SUCCEEDED':
                raise RuntimeError(f"Athena query failed: {status['QueryExecution']['Status'].get('StateChangeReason', 'N/A')}")

            # 3. Get Results
            results = await retry_operation(
                lambda: asyncio.to_thread(self.athena_client.get_query_results, QueryExecutionId=query_id),
                backend_name=self.__class__.__name__, op_name="athena_get_results"
            )

            # 4. Parse Results
            parsed_results = []
            if results["ResultSet"]["Rows"]:
                header = [col["VarCharValue"] for col in results["ResultSet"]["Rows"][0]["Data"]]
                for row_data in results["ResultSet"]["Rows"][1:]:
                    row_dict = {header[i]: col.get("VarCharValue") for i, col in enumerate(row_data["Data"])}
                    parsed_results.append({
                        "encrypted_data": row_dict.get("encrypted_data"),
                        "entry_id": row_dict.get("entry_id"),
                        "timestamp": row_dict.get("timestamp"),
                        "schema_version": int(row_dict.get("schema_version")) if row_dict.get("schema_version") and row_dict.get("schema_version").isdigit() else None,
                        "_audit_hash": row_dict.get("_audit_hash")
                    })
            return parsed_results
        except Exception as e:
            logger.error(f"S3 query (Athena) failed: {e}", exc_info=True)
            BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="AthenaQueryError").inc()
            raise


    async def _migrate_schema(self) -> None:
        """
        Migrates S3 objects from an old key prefix (schema) to the new one.
        This involves reading, decrypting, transforming, re-encrypting, and re-uploading.
        """
        current_on_disk_version = await self._get_current_schema_version()
        if current_on_disk_version >= self.schema_version:
            logger.info(f"S3Backend schema is already at v{self.schema_version} or newer. No migration needed.")
            return

        logger.info(f"Migrating S3Backend from v{current_on_disk_version} to v{self.schema_version}")
        old_prefix = self.old_key_prefix
        new_prefix = self.key_prefix

        migrated_count = 0
        failed_migrations = []

        try:
            paginator = self.s3_client.get_paginator('list_objects_v2')
            pages = paginator.paginate(Bucket=self.bucket, Prefix=old_prefix)

            for page in pages:
                for obj in page.get('Contents', []):
                    old_key = obj['Key']
                    try:
                        # 1. Download old object
                        response = await retry_operation(
                            lambda: asyncio.to_thread(self.s3_client.get_object, Bucket=self.bucket, Key=old_key),
                            backend_name=self.__class__.__name__, op_name=f"s3_get_object:{old_key}"
                        )
                        compressed_encrypted_data_bytes = await asyncio.to_thread(response['Body'].read)

                        # 2. Decompress if necessary
                        if old_key.endswith('.gz'):
                            decompressed_data = zlib.decompress(compressed_encrypted_data_bytes)
                        else:
                            decompressed_data = compressed_encrypted_data_bytes

                        # 3. Deserialize stored entry wrapper (which contains encrypted_data)
                        stored_entry_str = decompressed_data.decode('utf-8')
                        stored_entry_lines = stored_entry_str.strip().split('\n')
                        
                        migrated_lines = []
                        for line in stored_entry_lines:
                            if not line.strip(): continue
                            stored_entry = json.loads(line)

                            encrypted_b64 = stored_entry.get("encrypted_data")
                            if not encrypted_b64:
                                raise ValueError("Missing encrypted_data in old S3 object line.")

                            # 4. Decrypt, Decompress Audit Entry
                            decrypted_bytes = ENCRYPTER.decrypt(base64.b64decode(encrypted_b64))
                            decompressed_audit_entry_str = self._decompress(decrypted_bytes)
                            original_audit_entry = json.loads(decompressed_audit_entry_str)

                            # 5. Apply Migration Rules
                            if "entry_id" not in original_audit_entry or not original_audit_entry["entry_id"]:
                                original_audit_entry["entry_id"] = str(uuid.uuid4())

                            if "timestamp" not in original_audit_entry or not original_audit_entry["timestamp"]:
                                original_audit_entry["timestamp"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds') + 'Z'

                            if self.tamper_detection_enabled:
                                temp_audit_entry_for_hash = original_audit_entry.copy()
                                temp_audit_entry_for_hash.pop("_audit_hash", None)
                                new_hash = compute_hash(json.dumps(temp_audit_entry_for_hash, sort_keys=True).encode("utf-8"))
                                original_audit_entry["_audit_hash"] = new_hash

                            original_audit_entry["schema_version"] = self.schema_version

                            # 6. Re-encrypt and Re-compress
                            updated_data_str = json.dumps(original_audit_entry, sort_keys=True)
                            updated_compressed = self._compress(updated_data_str)
                            updated_encrypted = self._encrypt(updated_compressed)
                            updated_base64_data = base64.b64encode(updated_encrypted).decode("utf-8")

                            new_stored_entry = {
                                "encrypted_data": updated_base64_data,
                                "entry_id": original_audit_entry["entry_id"],
                                "timestamp": original_audit_entry["timestamp"],
                                "schema_version": self.schema_version,
                                "_audit_hash": original_audit_entry["_audit_hash"]
                            }
                            migrated_lines.append(json.dumps(new_stored_entry))
                        
                        migrated_count += len(migrated_lines)

                        # 7. Upload new object to the new prefix
                        ts_dt = datetime.datetime.fromisoformat(original_audit_entry["timestamp"].replace('Z', '+00:00'))
                        # Keep original file name structure but move to new prefix
                        new_key = f"{new_prefix}{ts_dt.year}/{ts_dt.month:02d}/{ts_dt.day:02d}/{original_audit_entry['entry_id']}.jsonl.gz"

                        new_object_body = zlib.compress(("\n".join(migrated_lines) + "\n").encode("utf-8"), level=COMPRESSION_LEVEL)

                        await retry_operation(
                            lambda: asyncio.to_thread(self.s3_client.put_object,
                                                    Bucket=self.bucket,
                                                    Key=new_key,
                                                    Body=new_object_body,
                                                    ContentEncoding="gzip",
                                                    ContentType="application/jsonl"),
                            backend_name=self.__class__.__name__, op_name=f"s3_put_object:{new_key}"
                        )
                        
                        logger.debug(f"Migrated {old_key} to {new_key}")

                    except InvalidToken as e:
                        logger.error(f"Failed to migrate S3 object {old_key}: Decryption failed. Key issue.", exc_info=True)
                        BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="MigrationDecryptionFail").inc()
                        failed_migrations.append(old_key)
                    except Exception as migrate_obj_e:
                        logger.error(f"Failed to migrate S3 object {old_key}: {migrate_obj_e}", exc_info=True)
                        BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="MigrationObjectError").inc()
                        failed_migrations.append(old_key)
            
            if failed_migrations:
                raise MigrationError(f"S3 migration completed with {len(failed_migrations)} failures. See logs for details.")
            
            logger.info(f"S3Backend migration completed. Migrated {migrated_count} objects.")
            await self._refresh_athena_partitions()

        except Exception as e:
            logger.error(f"S3Backend migration failed: {e}", exc_info=True)
            BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="MigrationFailed").inc()
            asyncio.create_task(send_alert(f"S3Backend migration failed. Manual intervention required.", severity="critical"))
            raise


    async def _refresh_athena_partitions(self):
        """Runs MSCK REPAIR TABLE to update Athena partitions."""
        logger.info(f"S3Backend: Running MSCK REPAIR TABLE for {self.athena_database}.{self.athena_table}")
        try:
            response = await retry_operation(
                lambda: asyncio.to_thread(self.athena_client.start_query_execution,
                                        QueryString=f"MSCK REPAIR TABLE {self.athena_database}.{self.athena_table}",
                                        ResultConfiguration={"OutputLocation": self.athena_results_location}),
                backend_name=self.__class__.__name__, op_name="athena_repair_table"
            )
            query_execution_id = response['QueryExecutionId']
            while True:
                status = await retry_operation(
                    lambda: asyncio.to_thread(self.athena_client.get_query_execution, QueryExecutionId=query_execution_id),
                    backend_name=self.__class__.__name__, op_name="athena_repair_status"
                )
                state = status['QueryExecution']['Status']['State']
                if state in ['SUCCEEDED', 'FAILED', 'CANCELLED']:
                    break
                await asyncio.sleep(1)

            if state != 'SUCCEEDED':
                raise RuntimeError(f"MSCK REPAIR TABLE failed with state: {state}. Reason: {status['QueryExecution']['Status'].get('StateChangeReason', 'N/A')}")

            logger.info("Athena partitions repaired successfully.")
        except Exception as e:
            logger.error(f"Failed to repair Athena partitions: {e}", exc_info=True)
            BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="AthenaPartitionRepairError").inc()
            asyncio.create_task(send_alert(f"Failed to refresh Athena partitions for S3Backend: {e}", severity="medium"))


    async def _health_check(self) -> bool:
        """Checks S3 bucket accessibility."""
        try:
            await retry_operation(
                lambda: asyncio.to_thread(self.s3_client.head_bucket, Bucket=self.bucket),
                backend_name=self.__class__.__name__, op_name="s3_head_bucket"
            )
            return True
        except botocore.exceptions.ClientError as e:
            error_code = int(e.response['Error']['Code'])
            if error_code == 404:
                logger.warning(f"S3 bucket '{self.bucket}' not found during health check.")
            elif error_code == 403:
                logger.warning(f"Access denied to S3 bucket '{self.bucket}' during health check.")
            else:
                logger.warning(f"S3 health check failed with ClientError: {e}")
            return False
        except Exception as e:
            logger.warning(f"S3 health check failed: {e}")
            return False

    async def _get_current_schema_version(self) -> int:
        """Determines the current schema version by checking S3 prefixes."""
        try:
            paginator = self.s3_client.get_paginator('list_objects_v2')
            pages_current = paginator.paginate(Bucket=self.bucket, Prefix=self.key_prefix, MaxKeys=1)
            for page in pages_current:
                if page.get('Contents'):
                    return self.schema_version

            pages_old = paginator.paginate(Bucket=self.bucket, Prefix=self.old_key_prefix, MaxKeys=1)
            for page in pages_old:
                if page.get('Contents'):
                    return SCHEMA_VERSION - 1

            return 1
        except Exception as e:
            logger.warning(f"Could not determine S3 schema version: {e}. Assuming v1.")
            return 1


    @asynccontextmanager
    async def _atomic_context(self, prepared_entries: List[Dict[str, Any]]) -> AsyncIterator[None]:
        """
        Atomicity for S3 batch writes by collecting entries and uploading as a single gzipped JSON Lines object.
        """
        try:
            if not prepared_entries:
                yield
                return

            batch_data_str = "\n".join(json.dumps(e) for e in prepared_entries) + "\n"

            timestamp_for_key = datetime.datetime.fromisoformat(prepared_entries[0]["timestamp"].replace('Z', '+00:00'))
            object_key = f"{self.key_prefix}{timestamp_for_key.year}/{timestamp_for_key.month:02d}/{timestamp_for_key.day:02d}/{uuid.uuid4()}.jsonl.gz"

            compressed_batch_data = zlib.compress(batch_data_str.encode("utf-8"), level=COMPRESSION_LEVEL)

            await retry_operation(
                lambda: asyncio.to_thread(self.s3_client.put_object,
                                        Bucket=self.bucket,
                                        Key=object_key,
                                        Body=compressed_batch_data,
                                        ContentEncoding="gzip",
                                        ContentType="application/jsonl"),
                backend_name=self.__class__.__name__, op_name="s3_put_batch_object"
            )
            logger.debug(f"S3Backend: Atomically flushed {len(prepared_entries)} entries to {object_key}")

            await self._refresh_athena_partitions()
            yield

        except Exception as e:
            logger.error(f"S3Backend atomic batch write failed: {e}", exc_info=True)
            BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="AtomicWriteError").inc()
            asyncio.create_task(send_alert(f"S3Backend atomic batch write failed. Data might be inconsistent.", severity="high"))
            raise


    async def _cleanup_orphaned_objects(self):
        """
        Placeholder for cleaning up orphaned objects (e.g., from failed migrations or old versions).
        This would typically be a separate, scheduled job.
        """
        logger.info("S3Backend: Running placeholder for orphaned object cleanup. Implement actual logic for old versions.")

# --- GCS Backend ---
class GCSBackend(LogBackend):
    """GCS backend with BigQuery integration and explicit schema migration."""
    def _validate_params(self):
        if "bucket" not in self.params:
            raise ValueError("bucket parameter is required")
        if "project_id" not in self.params:
            raise ValueError("project_id parameter is required for BigQuery")

        self.bucket_name = self.params["bucket"]
        self.key_prefix = self.params.get("key_prefix", "audit_logs/")
        self.bigquery_project_id = self.params["project_id"]
        self.bigquery_dataset = self.params.get("bigquery_dataset", "audit_dataset")
        self.bigquery_table = self.params.get("bigquery_table", "audit_logs")
        if not self.key_prefix.endswith('/'):
            self.key_prefix += '/'
        self.old_key_prefix = self.params.get("old_key_prefix", f"audit_logs_v{SCHEMA_VERSION - 1}/")


    def __init__(self, params: Dict[str, Any]):
        super().__init__(params)
        self.client = gcs.Client(project=self.bigquery_project_id)
        self.bucket = self.client.get_bucket(self.bucket_name)
        asyncio.create_task(self._init_bigquery())


    async def _init_bigquery(self):
        """Initializes BigQuery table."""
        from google.cloud import bigquery
        client = bigquery.Client(project=self.bigquery_project_id)

        dataset_ref = client.dataset(self.bigquery_dataset, project=self.bigquery_project_id)
        table_ref = dataset_ref.table(self.bigquery_table)

        schema = [
            bigquery.SchemaField("entry_id", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("encrypted_data", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("timestamp", "TIMESTAMP", mode="REQUIRED"),
            bigquery.SchemaField("schema_version", "INTEGER", mode="REQUIRED"),
            bigquery.SchemaField("_audit_hash", "STRING", mode="REQUIRED"),
        ]

        table = bigquery.Table(table_ref, schema=schema)
        table.time_partitioning = bigquery.TimePartitioning(
            type_=bigquery.TimePartitioningType.DAY,
            field="timestamp"
        )
        table.clustering_fields = ["entry_id", "schema_version"]

        try:
            await retry_operation(
                lambda: asyncio.to_thread(client.create_table, table, exists_ok=True),
                backend_name=self.__class__.__name__, op_name="bigquery_create_table"
            )
            logger.info(f"BigQuery table '{self.bigquery_dataset}.{self.bigquery_table}' initialized.")
        except Exception as e:
            logger.critical(f"BigQuery initialization failed: {e}", exc_info=True)
            BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="BigQueryInitError").inc()
            asyncio.create_task(send_alert(f"BigQuery initialization failed for GCSBackend: {e}", severity="critical"))
            raise


    async def _append_single(self, prepared_entry: Dict[str, Any]) -> None:
        """
        No-op for GCSBackend as the _atomic_context handles batch writing directly from the prepared_entries list.
        """
        pass


    async def _query_single(self, filters: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
        """Queries GCS using BigQuery with filtering."""
        from google.cloud import bigquery
        client = bigquery.Client(project=self.bigquery_project_id)

        query_columns = "entry_id, encrypted_data, timestamp, schema_version, _audit_hash"
        query = f"SELECT {query_columns} FROM `{self.bigquery_project_id}.{self.bigquery_dataset}.{self.bigquery_table}`"
        where_clauses = []

        if "timestamp >=" in filters:
            where_clauses.append(f"timestamp >= TIMESTAMP('{filters['timestamp >=']}')")
        if "timestamp <=" in filters:
            where_clauses.append(f"timestamp <= TIMESTAMP('{filters['timestamp <=']}')")
        if "entry_id" in filters:
            where_clauses.append(f"entry_id = '{filters['entry_id']}'")
        if "schema_version" in filters:
            where_clauses.append(f"schema_version = {filters['schema_version']}")

        if where_clauses:
            query += " WHERE " + " AND ".join(where_clauses)

        query += f" ORDER BY timestamp DESC LIMIT {limit}"

        try:
            job = await retry_operation(
                lambda: asyncio.to_thread(client.query, query),
                backend_name=self.__class__.__name__, op_name="bigquery_start_query"
            )
            results = await retry_operation(
                lambda: asyncio.to_thread(job.result),
                backend_name=self.__class__.__name__, op_name="bigquery_get_results"
            )

            parsed_results = []
            for row in results:
                # BigQuery timestamps are returned as datetime objects, convert to string (ISO 8601)
                parsed_results.append({
                    "encrypted_data": row.encrypted_data,
                    "entry_id": row.entry_id,
                    "timestamp": row.timestamp.isoformat(timespec='milliseconds') + 'Z',
                    "schema_version": row.schema_version,
                    "_audit_hash": row._audit_hash
                })
            return parsed_results
        except Exception as e:
            logger.error(f"GCS query (BigQuery) failed: {e}", exc_info=True)
            BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="BigQueryError").inc()
            asyncio.create_task(send_alert(f"GCSBackend BigQuery query failed: {e}", severity="high"))
            raise


    async def _migrate_schema(self) -> None:
        """
        Migrates GCS objects from an old key prefix (schema) to the new one.
        This involves reading, decrypting, transforming, re-encrypting, and re-uploading.
        """
        current_on_disk_version = await self._get_current_schema_version()
        if current_on_disk_version >= self.schema_version:
            logger.info(f"GCSBackend schema is already at v{self.schema_version} or newer. No migration needed.")
            return

        logger.info(f"Migrating GCSBackend from v{current_on_disk_version} to v{self.schema_version}")
        old_prefix = self.old_key_prefix
        new_prefix = self.key_prefix

        migrated_count = 0
        failed_migrations = []

        try:
            # 1. List Blobs
            blobs_iter = await retry_operation(
                lambda: asyncio.to_thread(self.client.list_blobs, self.bucket_name, prefix=old_prefix),
                backend_name=self.__class__.__name__, op_name="gcs_list_blobs"
            )
            blobs = list(blobs_iter)

            for blob in blobs:
                old_key = blob.name
                try:
                    # 2. Download old object
                    compressed_encrypted_data_bytes = await retry_operation(
                        lambda: asyncio.to_thread(blob.download_as_bytes),
                        backend_name=self.__class__.__name__, op_name=f"gcs_download_blob:{old_key}"
                    )

                    # 3. Decompress and Deserialize stored entry wrapper
                    if old_key.endswith('.gz'):
                        decompressed_data = zlib.decompress(compressed_encrypted_data_bytes)
                    else:
                        decompressed_data = compressed_encrypted_data_bytes

                    stored_entry_str = decompressed_data.decode('utf-8')
                    stored_entry_lines = stored_entry_str.strip().split('\n')
                    
                    migrated_lines = []
                    for line in stored_entry_lines:
                        if not line.strip(): continue
                        stored_entry = json.loads(line)

                        encrypted_b64 = stored_entry.get("encrypted_data")
                        if not encrypted_b64:
                            raise ValueError("Missing encrypted_data in old GCS object line.")

                        # 4. Decrypt, Decompress Audit Entry
                        decrypted_bytes = ENCRYPTER.decrypt(base64.b64decode(encrypted_b64))
                        decompressed_audit_entry_str = self._decompress(decrypted_bytes)
                        original_audit_entry = json.loads(decompressed_audit_entry_str)

                        # 5. Apply Migration Rules
                        if "entry_id" not in original_audit_entry or not original_audit_entry["entry_id"]:
                            original_audit_entry["entry_id"] = str(uuid.uuid4())

                        if "timestamp" not in original_audit_entry or not original_audit_entry["timestamp"]:
                            original_audit_entry["timestamp"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds') + 'Z'

                        if self.tamper_detection_enabled:
                            temp_audit_entry_for_hash = original_audit_entry.copy()
                            temp_audit_entry_for_hash.pop("_audit_hash", None)
                            new_hash = compute_hash(json.dumps(temp_audit_entry_for_hash, sort_keys=True).encode("utf-8"))
                            original_audit_entry["_audit_hash"] = new_hash

                        original_audit_entry["schema_version"] = self.schema_version

                        # 6. Re-encrypt and Re-compress
                        updated_data_str = json.dumps(original_audit_entry, sort_keys=True)
                        updated_compressed = self._compress(updated_data_str)
                        updated_encrypted = self._encrypt(updated_compressed)
                        updated_base64_data = base64.b64encode(updated_encrypted).decode("utf-8")

                        new_stored_entry = {
                            "encrypted_data": updated_base64_data,
                            "entry_id": original_audit_entry["entry_id"],
                            "timestamp": original_audit_entry["timestamp"],
                            "schema_version": self.schema_version,
                            "_audit_hash": original_audit_entry["_audit_hash"]
                        }
                        migrated_lines.append(json.dumps(new_stored_entry))

                    # 7. Upload new object to the new prefix
                    if migrated_lines:
                        ts_dt = datetime.datetime.fromisoformat(original_audit_entry["timestamp"].replace('Z', '+00:00'))
                        new_key = f"{new_prefix}{ts_dt.year}/{ts_dt.month:02d}/{ts_dt.day:02d}/{original_audit_entry['entry_id']}.jsonl.gz"
                        new_blob = self.bucket.blob(new_key)
                        new_object_body = zlib.compress(("\n".join(migrated_lines) + "\n").encode("utf-8"), level=COMPRESSION_LEVEL)

                        await retry_operation(
                            lambda: asyncio.to_thread(new_blob.upload_from_string, new_object_body, content_type="application/gzip"),
                            backend_name=self.__class__.__name__, op_name=f"gcs_upload_blob:{new_key}"
                        )
                        migrated_count += len(migrated_lines)
                        logger.debug(f"Migrated {old_key} to {new_key}")

                except InvalidToken as e:
                    logger.error(f"Failed to migrate GCS object {old_key}: Decryption failed. Key issue.", exc_info=True)
                    BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="MigrationDecryptionFail").inc()
                    failed_migrations.append(old_key)
                except Exception as migrate_obj_e:
                    logger.error(f"Failed to migrate GCS object {old_key}: {migrate_obj_e}", exc_info=True)
                    BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="MigrationObjectError").inc()
                    failed_migrations.append(old_key)

            if failed_migrations:
                raise MigrationError(f"GCS migration completed with {len(failed_migrations)} failures. See logs for details.")

            logger.info(f"GCSBackend migration completed. Migrated {migrated_count} objects.")

        except Exception as e:
            logger.error(f"GCSBackend migration failed: {e}", exc_info=True)
            BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="MigrationFailed").inc()
            asyncio.create_task(send_alert(f"GCSBackend migration failed. Manual intervention required.", severity="critical"))
            raise


    async def _health_check(self) -> bool:
        """Checks GCS bucket accessibility."""
        try:
            exists = await retry_operation(
                lambda: asyncio.to_thread(self.bucket.exists),
                backend_name=self.__class__.__name__, op_name="gcs_bucket_exists"
            )
            if not exists:
                logger.warning(f"GCS bucket '{self.bucket_name}' not found during health check.")
                return False

            await retry_operation(
                lambda: asyncio.to_thread(self.client.list_blobs, self.bucket_name, max_results=1),
                backend_name=self.__class__.__name__, op_name="gcs_list_blobs_check"
            )
            return True
        except Exception as e:
            logger.warning(f"GCS health check failed: {e}")
            return False

    async def _get_current_schema_version(self) -> int:
        """Determines the current schema version by checking GCS prefixes."""
        try:
            # Check for objects in current prefix
            blobs_current_iter = await retry_operation(
                lambda: asyncio.to_thread(self.client.list_blobs, self.bucket_name, prefix=self.key_prefix, max_results=1),
                backend_name=self.__class__.__name__, op_name="gcs_list_blobs_current_prefix"
            )
            if any(True for _ in blobs_current_iter):
                return self.schema_version

            # Check for objects in old prefix
            blobs_old_iter = await retry_operation(
                lambda: asyncio.to_thread(self.client.list_blobs, self.bucket_name, prefix=self.old_key_prefix, max_results=1),
                backend_name=self.__class__.__name__, op_name="gcs_list_blobs_old_prefix"
            )
            if any(True for _ in blobs_old_iter):
                return SCHEMA_VERSION - 1

            return 1
        except Exception as e:
            logger.warning(f"Could not determine GCS schema version: {e}. Assuming v1.")
            return 1

    @asynccontextmanager
    async def _atomic_context(self, prepared_entries: List[Dict[str, Any]]) -> AsyncIterator[None]:
        """
        Atomicity for GCS batch writes by collecting entries and uploading as a single gzipped JSON Lines object,
        then initiating a BigQuery load job.
        """
        try:
            if not prepared_entries:
                yield
                return

            batch_data_str = "\n".join(json.dumps(e) for e in prepared_entries) + "\n"

            timestamp_for_key = datetime.datetime.fromisoformat(prepared_entries[0]["timestamp"].replace('Z', '+00:00'))
            blob_name = f"{self.key_prefix}{timestamp_for_key.year}/{timestamp_for_key.month:02d}/{timestamp_for_key.day:02d}/{uuid.uuid4()}.jsonl.gz"
            blob = self.bucket.blob(blob_name)

            compressed_batch_data = zlib.compress(batch_data_str.encode("utf-8"), level=COMPRESSION_LEVEL)

            await retry_operation(
                lambda: asyncio.to_thread(blob.upload_from_string, compressed_batch_data, content_type="application/gzip"),
                backend_name=self.__class__.__name__, op_name="gcs_upload_blob"
            )
            logger.debug(f"GCSBackend: Atomically flushed {len(prepared_entries)} entries to {blob_name}")

            from google.cloud import bigquery
            bq_client = bigquery.Client(project=self.bigquery_project_id)
            table_ref = bq_client.dataset(self.bigquery_dataset).table(self.bigquery_table)

            job_config = bigquery.LoadJobConfig(
                source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
                schema=table_ref.schema,
                write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
                create_disposition=bigquery.CreateDisposition.CREATE_IF_NEEDED,
                compression=bigquery.Compression.GZIP
            )

            uri = f"gs://{self.bucket_name}/{blob_name}"

            load_job = await retry_operation(
                lambda: asyncio.to_thread(bq_client.load_table_from_uri, uri, table_ref, job_config=job_config),
                backend_name=self.__class__.__name__, op_name="bigquery_load_job"
            )
            await retry_operation(
                lambda: asyncio.to_thread(load_job.result),
                backend_name=self.__class__.__name__, op_name="bigquery_load_job_result"
            )

            if load_job.errors:
                raise RuntimeError(f"BigQuery load job failed: {load_job.errors}")
            yield

        except Exception as e:
            logger.error(f"GCSBackend atomic batch write failed: {e}", exc_info=True)
            BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="AtomicWriteError").inc()
            asyncio.create_task(send_alert(f"GCSBackend atomic batch write failed. Data might be inconsistent.", severity="high"))
            raise

    async def _cleanup_orphaned_objects(self):
        """
        Placeholder for cleaning up orphaned objects (e.g., from failed migrations or old versions).
        This would typically be a separate, scheduled job.
        """
        logger.info("GCSBackend: Running placeholder for orphaned object cleanup. Implement actual logic for old versions.")


# --- Azure Blob Backend ---
class AzureBlobBackend(LogBackend):
    """
    Production-grade async Azure Blob Storage backend for audit logging.
    Handles atomic batch writes via block blobs, schema migrations, health checks, and secure data handling.
    """

    def _validate_params(self):
        required = ['connection_string', 'container_name']
        for r in required:
            if r not in self.params:
                raise ValueError(f"Missing required Azure Blob param: {r}")
        self.connection_string = self.params["connection_string"]
        self.container_name = self.params["container_name"]
        self.blob_prefix = self.params.get("blob_prefix", "audit_logs/")
        if not self.blob_prefix.endswith('/'):
            self.blob_prefix += '/'
        self.old_blob_prefix = self.params.get("old_blob_prefix", f"audit_logs_v{SCHEMA_VERSION - 1}/")


    def __init__(self, params: Dict[str, Any]):
        super().__init__(params)
        self.client: Optional[BlobServiceClient] = None
        self.container_client: Optional[any] = None
        asyncio.create_task(self._init_client())


    async def _init_client(self):
        """Initializes Azure Blob Service Client and ensures container exists."""
        try:
            self.client = BlobServiceClient.from_connection_string(self.connection_string)
            self.container_client = self.client.get_container_client(self.container_name)
            
            try: # Use try-except for container creation to handle ResourceExistsError
                await retry_operation(
                    lambda: self.container_client.create_container(),
                    backend_name=self.__class__.__name__, op_name="azure_create_container"
                )
                logger.info(f"Created Azure Blob container: {self.container_name}")
            except ResourceExistsError:
                logger.info(f"Azure Blob container '{self.container_name}' already exists.")
            except Exception as e: # Catch other potential errors during container creation
                raise e # Re-raise to be caught by the outer block

        except Exception as e: # Catch any errors during client initialization or overall container setup
            logger.critical(f"Azure Blob container initialization failed: {e}", exc_info=True)
            BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="AzureBlobInitError").inc()
            asyncio.create_task(send_alert(f"Azure Blob container initialization failed: {e}", severity="critical"))
            raise


    async def _append_single(self, prepared_entry: Dict[str, Any]) -> None:
        """
        No-op for AzureBlobBackend as the _atomic_context handles batch writing directly from the prepared_entries list.
        """
        pass


    async def _query_single(self, filters: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
        """Queries Azure Blob Storage, filtering by top-level attributes."""
        if self.container_client is None:
            await self._init_client()

        entries = []
        try:
            logger.warning("AzureBlobBackend: Querying performs full blob listing and in-memory filtering, which is inefficient for large datasets. Consider external indexing for production.")

            blob_iterator = await retry_operation(
                lambda: self.container_client.list_blobs(name_starts_with=self.blob_prefix),
                backend_name=self.__class__.__name__, op_name="azure_list_blobs"
            )

            async for blob in blob_iterator:
                if len(entries) >= limit:
                    break
                try:
                    blob_client = self.container_client.get_blob_client(blob)
                    stream = await retry_operation(
                        lambda: blob_client.download_blob(),
                        backend_name=self.__class__.__name__, op_name=f"azure_download_blob:{blob.name}"
                    )
                    compressed_data = await stream.readall()
                    
                    if blob.name.endswith('.gz'):
                        decompressed_data = zlib.decompress(compressed_data)
                    else:
                        decompressed_data = compressed_data
                    
                    stored_entry_str = decompressed_data.decode('utf-8')
                    # Handle multiple entries per blob file
                    for line in stored_entry_str.strip().split('\n'):
                        if not line:
                            continue
                        stored_entry = json.loads(line)

                        match = True
                        if "entry_id" in filters and stored_entry.get("entry_id") != filters["entry_id"]:
                            match = False
                        if "timestamp >=" in filters and stored_entry.get("timestamp", "") < filters["timestamp >="]:
                            match = False
                        if "timestamp <=" in filters and stored_entry.get("timestamp", "") > filters["timestamp <="]:
                            match = False
                        if "schema_version" in filters:
                            stored_schema_version = stored_entry.get("schema_version")
                            if stored_schema_version is None or stored_schema_version != filters["schema_version"]:
                                match = False
                        
                        if match:
                            entries.append(stored_entry)
                            if len(entries) >= limit:
                                break
                    if len(entries) >= limit:
                        break

                except Exception as e:
                    logger.error(f"Failed to process Azure blob {blob.name} during query: {e}", exc_info=True)
                    BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="AzureBlobQueryError").inc()
                    asyncio.create_task(send_alert(f"Failed to query Azure Blob {blob.name}. Data might be corrupted.", severity="medium"))
                    continue
        except Exception as e:
            logger.error(f"AzureBlobBackend query failed: {e}", exc_info=True)
            BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="AzureBlobQueryListError").inc()
            asyncio.create_task(send_alert(f"AzureBlobBackend query list failed: {e}", severity="high"))
            raise
        return entries


    async def _migrate_schema(self) -> None:
        """
        Migrates Azure Blob objects from an old blob prefix (schema) to the new one.
        This involves reading, decrypting, transforming, re-encrypting, and re-uploading.
        """
        current_on_disk_version = await self._get_current_schema_version()
        if current_on_disk_version >= self.schema_version:
            logger.info(f"AzureBlobBackend schema is already at v{self.schema_version} or newer. No migration needed.")
            return

        logger.info(f"Migrating AzureBlobBackend from v{current_on_disk_version} to v{self.schema_version}")
        old_prefix = self.old_blob_prefix
        new_prefix = self.blob_prefix

        migrated_count = 0
        failed_migrations = []

        if self.container_client is None:
            await self._init_client()

        try:
            blobs_iter = await retry_operation(
                lambda: self.container_client.list_blobs(name_starts_with=old_prefix),
                backend_name=self.__class__.__name__, op_name="azure_list_old_blobs"
            )
            blobs = [b async for b in blobs_iter]

            for blob in blobs:
                old_key = blob.name
                try:
                    blob_client = self.container_client.get_blob_client(old_key)
                    stream = await retry_operation(
                        lambda: blob_client.download_blob(),
                        backend_name=self.__class__.__name__, op_name=f"azure_download_blob_migrate:{old_key}"
                    )
                    compressed_encrypted_data_bytes = await stream.readall()

                    if old_key.endswith('.gz'):
                        decompressed_data = zlib.decompress(compressed_encrypted_data_bytes)
                    else:
                        decompressed_data = compressed_encrypted_data_bytes

                    stored_entry_str = decompressed_data.decode('utf-8')
                    stored_entries_from_blob = [json.loads(line) for line in stored_entry_str.strip().split('\n') if line.strip()]

                    migrated_batch_for_new_blob = []
                    for stored_entry in stored_entries_from_blob:
                        encrypted_b64 = stored_entry.get("encrypted_data")
                        if not encrypted_b64:
                            raise ValueError("Missing encrypted_data in old Azure Blob object.")

                        # Decrypt/Decompress Audit Entry
                        decrypted_bytes = ENCRYPTER.decrypt(base64.b64decode(encrypted_b64))
                        decompressed_audit_entry_str = self._decompress(decrypted_bytes)
                        original_audit_entry = json.loads(decompressed_audit_entry_str)

                        # Apply Migration Rules
                        if "entry_id" not in original_audit_entry or not original_audit_entry["entry_id"]:
                            original_audit_entry["entry_id"] = str(uuid.uuid4())

                        if "timestamp" not in original_audit_entry or not original_audit_entry["timestamp"]:
                            original_audit_entry["timestamp"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds') + 'Z'

                        if self.tamper_detection_enabled:
                            temp_audit_entry_for_hash = original_audit_entry.copy()
                            temp_audit_entry_for_hash.pop("_audit_hash", None)
                            new_hash = compute_hash(json.dumps(temp_audit_entry_for_hash, sort_keys=True).encode("utf-8"))
                            original_audit_entry["_audit_hash"] = new_hash

                        original_audit_entry["schema_version"] = self.schema_version

                        # Re-encrypt and Re-compress
                        updated_data_str = json.dumps(original_audit_entry, sort_keys=True)
                        updated_compressed = self._compress(updated_data_str)
                        updated_encrypted = self._encrypt(updated_compressed)
                        updated_base64_data = base64.b64encode(updated_encrypted).decode("utf-8")

                        new_stored_entry_for_blob = {
                            "encrypted_data": updated_base64_data,
                            "entry_id": original_audit_entry["entry_id"],
                            "timestamp": original_audit_entry["timestamp"],
                            "schema_version": self.schema_version,
                            "_audit_hash": original_audit_entry["_audit_hash"]
                        }
                        migrated_batch_for_new_blob.append(new_stored_entry_for_blob)

                    if migrated_batch_for_new_blob:
                        ts_dt = datetime.datetime.fromisoformat(migrated_batch_for_new_blob[0]["timestamp"].replace('Z', '+00:00'))
                        new_key_path = f"{new_prefix}{ts_dt.year}/{ts_dt.month:02d}/{ts_dt.day:02d}/{uuid.uuid4()}.jsonl.gz"
                        
                        new_object_body = zlib.compress("\n".join(json.dumps(e) for e in migrated_batch_for_new_blob).encode("utf-8"), level=COMPRESSION_LEVEL)

                        new_blob_client = self.container_client.get_blob_client(new_key_path)
                        await retry_operation(
                            lambda: new_blob_client.upload_blob(
                                data=new_object_body,
                                overwrite=True,
                                content_settings=ContentSettings(content_type="application/jsonl", content_encoding="gzip")
                            ),
                            backend_name=self.__class__.__name__, op_name=f"azure_upload_blob:{new_key_path}"
                        )
                        migrated_count += len(migrated_batch_for_new_blob)
                        logger.debug(f"Migrated {old_key} to {new_key_path}")

                except InvalidToken as e:
                    logger.error(f"Failed to migrate Azure Blob object {old_key}: Decryption failed. Key issue.", exc_info=True)
                    BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="MigrationDecryptionFail").inc()
                    failed_migrations.append(old_key)
                except Exception as migrate_obj_e:
                    logger.error(f"Failed to migrate Azure Blob object {old_key}: {migrate_obj_e}", exc_info=True)
                    BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="MigrationObjectError").inc()
                    failed_migrations.append(old_key)

            if failed_migrations:
                raise MigrationError(f"Azure Blob migration completed with {len(failed_migrations)} failures. See logs for details.")
            
            logger.info(f"AzureBlobBackend migration completed. Migrated {migrated_count} objects.")

        except Exception as e:
            logger.error(f"AzureBlobBackend migration failed: {e}", exc_info=True)
            BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="MigrationFailed").inc()
            asyncio.create_task(send_alert(f"AzureBlobBackend migration failed. Manual intervention required.", severity="critical"))
            raise


    async def _health_check(self) -> bool:
        """Checks Azure Blob container accessibility."""
        try:
            if self.container_client is None:
                await self._init_client()
            
            await retry_operation(
                lambda: self.container_client.get_container_properties(),
                backend_name=self.__class__.__name__, op_name="azure_get_container_properties"
            )
            # Check if any blobs can be listed.
            try:
                # Use async for to consume the first page of results
                async for _ in retry_operation(
                    lambda: self.container_client.list_blobs(results_per_page=1),
                    backend_name=self.__class__.__name__, op_name="azure_list_blobs_check"
                ):
                    break # Break after checking the first (or no) element
                return True
            except StopAsyncIteration: # Iterator is empty, but access is fine.
                return True
        except AzureError as e:
            logger.warning(f"AzureBlobBackend health check failed: {e}")
            return False
        except Exception as e:
            logger.warning(f"AzureBlobBackend health check failed with unexpected error: {e}")
            return False


    async def _get_current_schema_version(self) -> int:
        """Determines the current schema version by checking Azure Blob metadata or prefixes."""
        # This implementation attempts a lightweight client init if not already done.
        # It then checks for existing blobs in current and old prefixes.
        _client_was_none = False
        if self.container_client is None:
            _client_was_none = True
            try:
                # Attempt a lightweight client init just for schema version check
                temp_client = BlobServiceClient.from_connection_string(self.connection_string)
                temp_container_client = temp_client.get_container_client(self.container_name)
                # Check container existence - this will raise if container/access is problematic
                await temp_container_client.get_container_properties() 
                target_container_client = temp_container_client
            except Exception as e:
                logger.warning(f"Could not establish temporary connection to Azure Blob for schema version check: {e}. Assuming v1.")
                return 1 # Cannot connect, assume old schema or new DB
        else:
            target_container_client = self.container_client

        try:
            # Check for objects in current prefix
            blobs_current_iter = await retry_operation(
                lambda: target_container_client.list_blobs(name_starts_with=self.blob_prefix, results_per_page=1),
                backend_name=self.__class__.__name__, op_name="azure_list_current_prefix"
            )
            try:
                # __anext__() attempts to get the next item, StopAsyncIteration if empty
                async for _ in blobs_current_iter:
                    return self.schema_version
            except StopAsyncIteration:
                pass # Iterator is empty, no blobs found in current prefix

            # Check for objects in old prefix
            blobs_old_iter = await retry_operation(
                lambda: target_container_client.list_blobs(name_starts_with=self.old_blob_prefix, results_per_page=1),
                backend_name=self.__class__.__name__, op_name="azure_list_old_prefix"
            )
            try:
                async for _ in blobs_old_iter:
                    return SCHEMA_VERSION - 1
            except StopAsyncIteration:
                pass # Iterator is empty, no blobs found in old prefix

            return 1 # Assume v1 if no current/old prefixes found
        except Exception as e:
            logger.warning(f"Could not determine Azure Blob schema version: {e}. Assuming v1.")
            return 1
        finally:
            if _client_was_none and self.client:
                # If we created a temporary client, close it
                await self.client.close()


    @asynccontextmanager
    async def _atomic_context(self, prepared_entries: List[Dict[str, Any]]) -> AsyncIterator[None]:
        """
        Atomicity for Azure Blob batch writes by collecting entries and uploading as a single gzipped JSON Lines blob.
        """
        try:
            if not prepared_entries:
                yield
                return

            if self.container_client is None:
                await self._init_client()

            batch_data_str = "\n".join(json.dumps(e) for e in prepared_entries) + "\n"
            
            timestamp_for_blob_name = datetime.datetime.fromisoformat(prepared_entries[0]["timestamp"].replace('Z', '+00:00'))
            blob_name = f"{self.blob_prefix}{timestamp_for_blob_name.year}/{timestamp_for_blob_name.month:02d}/{timestamp_for_blob_name.day:02d}/{uuid.uuid4()}.jsonl.gz"
            
            compressed_batch_data = zlib.compress(batch_data_str.encode("utf-8"), level=COMPRESSION_LEVEL)

            await retry_operation(
                lambda: self.container_client.upload_blob(
                    name=blob_name,
                    data=compressed_batch_data,
                    overwrite=True,
                    content_settings=ContentSettings(content_type="application/jsonl", content_encoding="gzip") # Correct usage
                ),
                backend_name=self.__class__.__name__, op_name="azure_upload_batch_blob"
            )
            logger.debug(f"AzureBlobBackend: Atomically flushed {len(prepared_entries)} entries to {blob_name}")
            yield

        except Exception as e:
            logger.error(f"AzureBlobBackend atomic batch write failed: {e}", exc_info=True)
            BACKEND_ERRORS.labels(backend=self.__class__.__name__, type="AtomicWriteError").inc()
            asyncio.create_task(send_alert(f"AzureBlobBackend atomic batch write failed. Data might be inconsistent.", severity="high"))
            raise


    async def _cleanup_orphaned_objects(self):
        """
        Placeholder for cleaning up orphaned objects (e.g., from failed migrations or old versions).
        This would typically be a separate, scheduled job using Azure Functions or Azure Batch.
        """
        logger.info("AzureBlobBackend: Running placeholder for orphaned object cleanup. Implement actual logic for old versions using Azure Storage management APIs.")