import aiofiles
import hashlib
import json
import logging
import os
import uuid
import asyncio
import datetime
import time
import cryptography.exceptions
from typing import Dict, Any, Tuple
from prometheus_client import Counter, Histogram
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import Prehashed
from cryptography.hazmat.primitives.serialization import load_pem_private_key, load_pem_public_key
from tenacity import retry, stop_after_attempt, wait_exponential
from pydantic import BaseModel

# Configuration from environment variables
AUDIT_LOG_PATH = os.getenv("AUDIT_LOG_PATH", "./audit_log.jsonl")
AUDIT_ENCRYPTION_KEY = os.getenv("AUDIT_ENCRYPTION_KEY")
AUDIT_LOG_ROTATION_SIZE_MB = int(os.getenv("AUDIT_LOG_ROTATION_SIZE_MB", 100))
AUDIT_LOG_MAX_FILES = int(os.getenv("AUDIT_LOG_MAX_FILES", 10))
AUDIT_RETENTION_DAYS = int(os.getenv("AUDIT_RETENTION_DAYS", 30))
USE_KAFKA_AUDIT = os.getenv("USE_KAFKA_AUDIT", "false").lower() == "true"
KAFKA_BROKERS = os.getenv("KAFKA_BROKERS", "localhost:9092").split(',')
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC", "audit_events")

logger = logging.getLogger(__name__)

# --- Prometheus Metrics Definitions ---
ML_AUDIT_HASH_MISMATCH = Counter(
    "ml_audit_hash_mismatch_total",
    "Total number of audit hash mismatches detected, indicating potential tampering."
)
ML_AUDIT_EVENTS_TOTAL = Counter(
    "ml_audit_events_total",
    "Total number of audit events logged.",
    ["event_type"]
)
ML_AUDIT_SIGNATURE_MISMATCH = Counter(
    "ml_audit_signature_mismatch_total",
    "Total number of audit signature mismatches detected, indicating potential tampering."
)
ML_AUDIT_ROTATIONS_TOTAL = Counter(
    "ml_audit_rotations_total", 
    "Total log rotations performed"
)
ML_AUDIT_CRYPTO_ERRORS = Counter(
    "ml_audit_crypto_errors_total", 
    "Total cryptographic errors (sign/verify/encrypt/decrypt)"
)
AUDIT_VALIDATION_LATENCY = Histogram(
    "audit_validation_latency_seconds", "Audit chain validation latency"
)

# Pydantic model for event validation
class AuditEvent(BaseModel):
    event_id: uuid.UUID
    timestamp: str
    event_type: str
    details: Any  # Can be dict or encrypted string
    event_hash: str
    prev_hash: str
    signature: str

class AuditUtils:
    """
    A utility class for managing and validating a tamper-proof audit log for a distributed ML orchestrator.

    This class provides methods to:
    - Write new events to an append-only log file or Kafka topic with cryptographic protections.
    - Validate the integrity of the entire audit chain from file or Kafka.
    - Encrypt sensitive event details using Fernet symmetric encryption.
    - Sign event hashes with an ECDSA private key to ensure non-repudiation. Now with correct Prehashed ECDSA.
    - Rotate log files reliably to prevent them from growing indefinitely.
    - Handle concurrency safely using asynchronous locks.
    - Expose detailed Prometheus metrics for monitoring, observability, and alerting.
    """
    def __init__(self,
                 log_path: str = AUDIT_LOG_PATH,
                 rotation_size_mb: int = AUDIT_LOG_ROTATION_SIZE_MB,
                 max_files: int = AUDIT_LOG_MAX_FILES):
        
        self.log_path = log_path
        self.rotation_size_mb = rotation_size_mb * 1024 * 1024  # Convert MB to bytes
        self.max_files = max_files
        self._lock = asyncio.Lock()

        # Encryption setup
        self.encryption_key = AUDIT_ENCRYPTION_KEY
        self.fernet = Fernet(self.encryption_key.encode()) if self.encryption_key else None
        if not self.fernet:
            logger.warning("AUDIT_ENCRYPTION_KEY is not set. Audit log details will not be encrypted.")

        # ECDSA Signature setup with configurable curve
        curve_name = os.getenv("ECDSA_CURVE", "SECP256R1").upper()
        try:
            self.curve = getattr(ec, curve_name)()
        except AttributeError:
            logger.error(f"Invalid ECDSA curve {curve_name}. Falling back to SECP256R1.")
            self.curve = ec.SECP256R1()
        logger.info(f"Using ECDSA curve: {self.curve.name}")

        self.private_key = None
        self.public_key = None
        try:
            private_pem = os.getenv("AUDIT_SIGNING_PRIVATE_KEY")
            public_pem = os.getenv("AUDIT_SIGNING_PUBLIC_KEY")
            if private_pem:
                self.private_key = load_pem_private_key(private_pem.encode(), password=None)
            if public_pem:
                self.public_key = load_pem_public_key(public_pem.encode())
            
            if not self.private_key:
                logger.critical("AUDIT_SIGNING_PRIVATE_KEY missing. Logs will be unsigned—a major security risk!")
            if not self.public_key:
                logger.warning("AUDIT_SIGNING_PUBLIC_KEY missing. Signature verification will not be possible.")
                
        except Exception as e:
            logger.error(f"Failed to load signing keys: {e}", exc_info=True)
            self.private_key = None
            self.public_key = None
        
        # Kafka producer setup
        self.kafka_producer = None
        self.loop = asyncio.get_event_loop()
        if USE_KAFKA_AUDIT:
            try:
                from aiokafka import AIOKafkaProducer
                self.kafka_producer = AIOKafkaProducer(loop=self.loop, bootstrap_servers=KAFKA_BROKERS)
                self.loop.create_task(self.kafka_producer.start())
            except ImportError:
                logger.error("aiokafka not installed. Falling back to file-based logging.")
                self.kafka_producer = None
            except Exception as e:
                logger.error(f"Failed to connect to Kafka: {e}. Falling back to file-based logging.", exc_info=True)
                self.kafka_producer = None

        self._setup_log_file()
        
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.kafka_producer:
            await self.kafka_producer.stop()
        logger.info("AuditUtils shutdown complete.")

    def _setup_log_file(self):
        if not USE_KAFKA_AUDIT:
            log_dir = os.path.dirname(self.log_path)
            if log_dir and not os.path.exists(log_dir):
                os.makedirs(log_dir)
            if not os.path.exists(self.log_path):
                open(self.log_path, 'a').close()
                os.chmod(self.log_path, 0o600)

    async def _rotate_log(self):
        """
        Rotates the log file if it exceeds the configured size or retention period.
        Shifts backups as .1 (newest) to .{max_files} (oldest), deleting the oldest if needed.
        Also removes backups older than AUDIT_RETENTION_DAYS.
        """
        # This function is called from within a lock in _write_audit_event.
        if not os.path.exists(self.log_path):
            return

        # Time-based retention check
        if AUDIT_RETENTION_DAYS > 0:
            cutoff_time = time.time() - (AUDIT_RETENTION_DAYS * 86400)
            for i in range(1, self.max_files + 1):
                backup_path = f"{self.log_path}.{i}"
                if os.path.exists(backup_path) and os.path.getmtime(backup_path) < cutoff_time:
                    logger.info(f"Deleting old backup due to retention policy: {backup_path}")
                    os.remove(backup_path)

        # Size-based rotation check
        if os.path.getsize(self.log_path) <= self.rotation_size_mb:
            return

        logger.info(f"Log file {self.log_path} reached rotation size. Rotating...")
        
        # Delete the oldest backup file if it exists (e.g., audit_log.jsonl.10)
        oldest_path = f"{self.log_path}.{self.max_files}"
        if os.path.exists(oldest_path):
            os.remove(oldest_path)
        
        # Shift existing backups: .9 -> .10, .8 -> .9, ..., .1 -> .2
        for i in range(self.max_files - 1, 0, -1):
            source_path = f"{self.log_path}.{i}"
            dest_path = f"{self.log_path}.{i + 1}"
            if os.path.exists(source_path):
                os.rename(source_path, dest_path)
        
        # Rename current log to .1
        if os.path.exists(self.log_path):
            os.rename(self.log_path, f"{self.log_path}.1")
        
        # Create a new empty log file
        open(self.log_path, 'w').close()
        os.chmod(self.log_path, 0o600)
        
        ML_AUDIT_ROTATIONS_TOTAL.inc()
        logger.info(f"Log rotation complete. New file created at {self.log_path}")

    @retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, min=1, max=10))
    async def _write_audit_event(self, event: Dict[str, Any]):
        await self._rotate_log()
        async with aiofiles.open(self.log_path, "a", encoding='utf-8') as f:
            await f.write(json.dumps(event) + "\n")
        os.chmod(self.log_path, 0o600)

    async def _send_to_kafka(self, event: Dict[str, Any]):
        try:
            await self.kafka_producer.send_and_wait(KAFKA_TOPIC, json.dumps(event).encode('utf-8'))
        except Exception as e:
            logger.error(f"Kafka send failed: {e}. Falling back to file.", exc_info=True)
            async with self._lock:
                await self._write_audit_event(event)
            
    async def _get_last_hash(self) -> str:
        """Get the hash of the last event in the audit log."""
        if self.kafka_producer:
            try:
                from aiokafka import AIOKafkaConsumer
                from aiokafka.structs import TopicPartition
                consumer = AIOKafkaConsumer(
                    KAFKA_TOPIC, loop=self.loop, bootstrap_servers=KAFKA_BROKERS,
                    auto_offset_reset='latest', enable_auto_commit=False, consumer_timeout_ms=5000
                )
                await consumer.start()
                tp = TopicPartition(KAFKA_TOPIC, 0)
                if not consumer.partitions_for_topic(KAFKA_TOPIC) or tp not in await consumer.end_offsets([tp]):
                    await consumer.stop()
                    return "genesis_hash"
                end_offset = (await consumer.end_offsets([tp]))[tp]
                if end_offset == 0:
                    await consumer.stop()
                    return "genesis_hash"
                await consumer.seek(tp, end_offset - 1)
                msg = await consumer.getone()
                await consumer.stop()
                return json.loads(msg.value.decode('utf-8')).get("event_hash", "genesis_hash")
            except Exception as e:
                logger.error(f"Failed to get last hash from Kafka: {e}. Using genesis.", exc_info=True)
                return "genesis_hash"
        else:  # File-based logic
            if not os.path.exists(self.log_path) or os.path.getsize(self.log_path) == 0:
                return "genesis_hash"
            try:
                # Read the entire file and get the last line
                async with aiofiles.open(self.log_path, 'r', encoding='utf-8') as f:
                    lines = await f.readlines()
                    if not lines:
                        return "genesis_hash"
                    
                    # Find the last non-empty line
                    last_line = None
                    for line in reversed(lines):
                        line = line.strip()
                        if line:
                            last_line = line
                            break
                    
                    if not last_line:
                        return "genesis_hash"
                    
                    return json.loads(last_line).get("event_hash", "genesis_hash")
            except Exception as e:
                logger.error(f"Could not read last hash from file: {e}", exc_info=True)
                return "genesis_hash"

    def hash_event(self, event_data: Dict[str, Any], prev_hash: str) -> Tuple[str, bytes]:
        """
        Computes a SHA256 hash for an event, including the previous event's hash.
        Returns (hex_digest_string, raw_digest_bytes) for storage and signing efficiency.
        """
        payload = {"event": event_data, "prev_hash": prev_hash}
        data = json.dumps(payload, sort_keys=True).encode()
        digest = hashlib.sha256(data).digest()
        return digest.hex(), digest

    def _sign_hash(self, digest: bytes) -> str:
        """Signs the precomputed digest using the configured private key and ECDSA."""
        if not self.private_key:
            logger.warning("No private key configured. Audit event will not be signed.")
            return ""
        try:
            signature = self.private_key.sign(
                digest,
                ec.ECDSA(Prehashed(hashes.SHA256()))
            )
            return signature.hex()
        except Exception as e:
            logger.error(f"Failed to sign event digest: {e}", exc_info=True)
            ML_AUDIT_CRYPTO_ERRORS.inc()
            return ""

    def _verify_signature(self, digest: bytes, signature: str) -> bool:
        """Verifies the signature against the precomputed digest using the public key."""
        if not self.public_key:
            logger.warning("No public key configured. Cannot verify signature.")
            return False
        try:
            self.public_key.verify(
                bytes.fromhex(signature),
                digest,
                ec.ECDSA(Prehashed(hashes.SHA256()))
            )
            return True
        except cryptography.exceptions.InvalidSignature:
            return False
        except ValueError as e:
            logger.error(f"Invalid signature format: {e}")
            ML_AUDIT_CRYPTO_ERRORS.inc()
            return False
        except Exception as e:
            logger.error(f"Unexpected signature verification error: {e}", exc_info=True)
            ML_AUDIT_CRYPTO_ERRORS.inc()
            return False

    def get_current_timestamp(self) -> str:
        return datetime.datetime.now(datetime.timezone.utc).isoformat()

    async def add_audit_event(self, event_type: str, details: Dict[str, Any]):
        """
        Adds a new audit event with cryptographic protections.
        Handles locking, encryption, hashing, signing, and routing to file or Kafka.
        """
        event_id = uuid.uuid4()
        timestamp = self.get_current_timestamp()

        # Encrypt details if enabled
        encrypted_details = details
        if self.fernet:
            try:
                encrypted_details = self.fernet.encrypt(json.dumps(details).encode()).decode()
            except Exception as e:
                logger.error(f"Failed to encrypt details for event {event_id}: {e}", exc_info=True)
                ML_AUDIT_CRYPTO_ERRORS.inc()
        
        event_data = {
            "event_id": str(event_id),
            "timestamp": timestamp,
            "event_type": event_type,
            "details": encrypted_details
        }
        
        async with self._lock:
            prev_hash = await self._get_last_hash()
            event_hash, digest = self.hash_event(event_data, prev_hash)
            signature = self._sign_hash(digest)
            
            final_event = {
                **event_data,
                "event_hash": event_hash,
                "prev_hash": prev_hash,
                "signature": signature
            }
            
            if self.kafka_producer:
                await self._send_to_kafka(final_event)
            else:
                await self._write_audit_event(final_event)
        
        ML_AUDIT_EVENTS_TOTAL.labels(event_type=event_type).inc()
        logger.info(f"Audit event added: {event_id} (type: {event_type})")

    @AUDIT_VALIDATION_LATENCY.time()
    async def validate_audit_chain(self) -> Dict[str, Any]:
        """Reads all audit events and validates the entire hash chain and all signatures."""
        if USE_KAFKA_AUDIT:
            return await self._validate_kafka_chain()
        else:
            return await self._validate_file_chain()

    async def _validate_file_chain(self) -> Dict[str, Any]:
        report = {
            "is_valid": True,
            "mismatches": [],
            "total_events": 0,
            "log_path": self.log_path,
            "message": "Audit chain valid."
        }
        
        if not os.path.exists(self.log_path):
            report.update({"is_valid": False, "message": "Audit log file not found."})
            return report
        
        prev_hash = "genesis_hash"
        line_number = 0
        
        try:
            async with aiofiles.open(self.log_path, 'r', encoding='utf-8') as f:
                async for line in f:
                    line_number += 1
                    report["total_events"] = line_number
                    
                    if not line.strip():
                        continue
                    
                    try:
                        event = json.loads(line)
                        AuditEvent.model_validate(event)
                        
                        # FIXED: Use the stored details (encrypted) for hash validation,
                        # not the decrypted version. This matches what add_audit_event does.
                        event_for_hash = {
                            "event_id": event["event_id"],
                            "timestamp": event["timestamp"],
                            "event_type": event["event_type"],
                            "details": event["details"]  # Use as-stored (encrypted if applicable)
                        }
                        
                        expected_hash, expected_digest = self.hash_event(event_for_hash, prev_hash)
                        
                        if event["event_hash"] != expected_hash or event["prev_hash"] != prev_hash:
                            logger.critical(f"AUDIT CHAIN TAMPERED! Hash mismatch at line {line_number}.")
                            report["mismatches"].append({"line": line_number, "type": "hash_mismatch"})
                            ML_AUDIT_HASH_MISMATCH.inc()
                            report["is_valid"] = False
                            break
                        
                        if not self._verify_signature(expected_digest, event["signature"]):
                            logger.critical(f"AUDIT CHAIN TAMPERED! Invalid signature at line {line_number}.")
                            report["mismatches"].append({"line": line_number, "type": "signature_mismatch"})
                            ML_AUDIT_SIGNATURE_MISMATCH.inc()
                            report["is_valid"] = False
                            break
                        
                        prev_hash = event["event_hash"]
                        
                    except json.JSONDecodeError as e:
                        report["mismatches"].append({
                            "line": line_number,
                            "type": "malformed_event",
                            "error": str(e)
                        })
                        ML_AUDIT_HASH_MISMATCH.inc()
                        report["is_valid"] = False
                        break
                    except Exception as e:
                        report["mismatches"].append({
                            "line": line_number,
                            "type": "validation_error",
                            "error": str(e)
                        })
                        ML_AUDIT_HASH_MISMATCH.inc()
                        report["is_valid"] = False
                        break
            
            if not report["is_valid"]:
                report["message"] = "Audit chain validation FAILED."
                
        except Exception as e:
            report.update({
                "is_valid": False,
                "message": f"Unexpected error during validation: {e}"
            })
        
        return report

    async def _validate_kafka_chain(self) -> Dict[str, Any]:
        report = {
            "is_valid": True,
            "mismatches": [],
            "total_events": 0,
            "log_path": f"Kafka Topic: {KAFKA_TOPIC}",
            "message": "Audit chain valid."
        }
        
        try:
            from aiokafka import AIOKafkaConsumer
            consumer = AIOKafkaConsumer(
                KAFKA_TOPIC,
                loop=self.loop,
                bootstrap_servers=KAFKA_BROKERS,
                auto_offset_reset='earliest',
                enable_auto_commit=False,
                consumer_timeout_ms=10000
            )
            await consumer.start()
        except Exception as e:
            report.update({
                "is_valid": False,
                "message": f"Failed to connect to Kafka for validation: {e}"
            })
            return report
        
        prev_hash = "genesis_hash"
        offset = 0
        
        try:
            async for msg in consumer:
                offset = msg.offset
                report["total_events"] = report["total_events"] + 1
                event = json.loads(msg.value.decode('utf-8'))
                
                # FIXED: Use the stored details (encrypted) for hash validation
                event_for_hash = {
                    "event_id": event["event_id"],
                    "timestamp": event["timestamp"],
                    "event_type": event["event_type"],
                    "details": event["details"]  # Use as-stored (encrypted if applicable)
                }
                
                expected_hash, expected_digest = self.hash_event(event_for_hash, prev_hash)
                
                if event["event_hash"] != expected_hash or event["prev_hash"] != prev_hash:
                    report["mismatches"].append({"offset": offset, "type": "hash_mismatch"})
                    ML_AUDIT_HASH_MISMATCH.inc()
                    report["is_valid"] = False
                    break
                
                if not self._verify_signature(expected_digest, event["signature"]):
                    report["mismatches"].append({"offset": offset, "type": "signature_mismatch"})
                    ML_AUDIT_SIGNATURE_MISMATCH.inc()
                    report["is_valid"] = False
                    break
                
                prev_hash = event["event_hash"]
                
        except Exception as e:
            report["mismatches"].append({
                "offset": offset,
                "type": "processing_error",
                "error": str(e)
            })
            report["is_valid"] = False
        finally:
            await consumer.stop()
        
        if not report["is_valid"]:
            report["message"] = "Audit chain validation FAILED."
        
        return report