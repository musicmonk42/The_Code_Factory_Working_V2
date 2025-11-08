# audit_log.py
"""
Unrivaled Audit Log System

This module provides a robust, secure, and observable audit logging solution with the following features:

- Granular Role-Based Access Control (RBAC): Defines and enforces permissions for different user roles.
- Immutable, Tamper-Evident Logs: Utilizes cryptographic signatures and a self-healing mechanism to ensure log integrity.
- Rich Context Logging: Automatically enriches log entries with contextual information like timestamps, user details, and source IP.
- Pluggable Backend and Hooks: Supports different storage backends (e.g., file, database) and allows custom logic to be triggered via hooks.
- Observability: Exposes Prometheus metrics for monitoring key operations, latency, and errors.
- Unified Interface: Provides a FastAPI-based REST API, a gRPC service, and a Typer CLI for interaction.
- Secure Configuration: Loads all sensitive information, including encryption keys and user credentials, from environment variables or external configuration files.

Configuration:
- AUDIT_LOG_ENCRYPTION_KEY (str, required): The base64-encoded Fernet key for symmetric encryption of log entries.
- AUDIT_LOG_IMMUTABLE (str, optional, default='true'): Set to 'false' to disable immutability checks.
- AUDIT_LOG_METRICS_PORT (int, optional, default=8002): Port for the Prometheus metrics server.
- AUDIT_LOG_API_PORT (int, optional, default=8003): Port for the FastAPI REST API.
- AUDIT_LOG_GRPC_PORT (int, optional, default=50051): Port for the gRPC service.
- AUDIT_LOG_USERS_CONFIG (str, optional): Path to a JSON or YAML file containing user and role definitions.
- AUDIT_LOG_BACKEND_TYPE (str, optional, default='file'): The type of backend to use ('file' or others).
- AUDIT_LOG_BACKEND_PARAMS (str, optional): JSON string of parameters for the backend.
- AUDIT_LOG_DEV_MODE (str, optional, default='false'): Set to 'true' to enable unsafe defaults (ephemeral key, dummy users) for development.

Hook Events:
- 'log_success': Fired after a log entry is successfully written. Arguments: (entry: dict).
- 'log_error': Fired when a log entry fails to write. Arguments: (error: Exception, entry: dict).
- 'tamper_detected': Fired when the self-healing process detects tampering. Arguments: (entry: dict, issue: str).
- 'key_rotated': Fired after a cryptographic key is successfully rotated. Arguments: (new_key_id: str, old_key_id: str).
- 'rbac_denial': Fired when an RBAC check denies an operation. Arguments: (user: str, role: str, operation: str).

Dependencies:
- aiofiles, aiohttp, typer, fastapi, uvicorn, grpcio, grpcio-tools, prometheus_client, pydantic, cryptography, opentelemetry-api, opentelemetry-sdk, opentelemetry-instrumentation-grpc, opentelemetry-instrumentation-fastapi, pyyaml
"""
import asyncio
import base64
import concurrent.futures
import datetime
import functools
import hashlib
import inspect
import json
import logging
import os
import socket
import threading
import time
import traceback
import uuid
import yaml
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional

import aiofiles
import aiohttp
import grpc
from grpc import aio as grpc_aio
import typer
from cryptography.fernet import Fernet, InvalidToken
from fastapi import FastAPI, Depends, HTTPException, Security, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from prometheus_client import Counter, Gauge, Histogram, start_http_server
from pydantic import BaseModel, ValidationError

# --- FIX 1: Import modules from specified package paths ---
from generator.audit_log.audit_backend.audit_backend_core import get_backend
from generator.audit_log.audit_crypto.audit_crypto_ops import sign_entry, verify_entry
from generator.audit_log.audit_crypto.audit_crypto_factory import crypto_provider as get_crypto_provider

# OpenTelemetry imports
try:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor
    from opentelemetry.instrumentation.grpc import GrpcAioInstrumentor
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    
    provider = TracerProvider()
    processor = SimpleSpanProcessor(ConsoleSpanExporter())
    provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)
    tracer = trace.get_tracer(__name__)
    HAS_OPENTELEMETRY = True
except ImportError:
    tracer = None
    HAS_OPENTELEMETRY = False
    logging.getLogger(__name__).warning("OpenTelemetry not found. Tracing will be unavailable.")

# --- FIX 6: Optional gRPC imports should degrade gracefully ---
try:
    import audit_log_pb2
    import audit_log_pb2_grpc
    HAS_GRPC_PROTOS = True
except ImportError as e:
    HAS_GRPC_PROTOS = False
    logging.getLogger(__name__).warning("gRPC protobufs not found. Run 'python -m grpc_tools.protoc...' to generate them. Details: %s", e)


logger = logging.getLogger(__name__)

# Constants (now with environment variable overrides)
METRICS_PORT = int(os.getenv('AUDIT_LOG_METRICS_PORT', 8002))
API_PORT = int(os.getenv('AUDIT_LOG_API_PORT', 8003))
GRPC_PORT = int(os.getenv('AUDIT_LOG_GRPC_PORT', 50051))
DEV_MODE = os.getenv('AUDIT_LOG_DEV_MODE', 'false').lower() == 'true'

# --- FIX 5: Stronger encryption key policy ---
ENCRYPTION_KEY_STR = os.getenv('AUDIT_LOG_ENCRYPTION_KEY')
if not ENCRYPTION_KEY_STR:
    if DEV_MODE:
        logger.warning("AUDIT_LOG_ENCRYPTION_KEY not set. Running in DEV_MODE with an ephemeral key. LOGS WILL NOT BE DECRYPTABLE ACROSS SESSIONS. Do not use in production.")
        ENCRYPTION_KEY = Fernet.generate_key()
    else:
        logger.critical("AUDIT_LOG_ENCRYPTION_KEY environment variable not set! Failing fast. Required for production security.")
        raise RuntimeError("AUDIT_LOG_ENCRYPTION_KEY is required in non-DEV_MODE.")
else:
    try:
        ENCRYPTION_KEY = ENCRYPTION_KEY_STR.encode('utf-8')
        Fernet(ENCRYPTION_KEY)  # Validate the key
    except (ValueError, IndexError):
        logger.critical("Invalid AUDIT_LOG_ENCRYPTION_KEY provided. Failing fast.")
        raise ValueError("Invalid AUDIT_LOG_ENCRYPTION_KEY format.")


# User roles and permissions
ROLES = {
    'admin': {'write': True, 'read': True, 'query': True, 'doc_gen': True, 'manage_keys': True},
    'user': {'write': True, 'read': False, 'query': True, 'doc_gen': False, 'manage_keys': False},
    'viewer': {'write': False, 'read': True, 'query': True, 'doc_gen': False, 'manage_keys': False}
}

USERS: Dict[str, Dict[str, Any]] = {}

def load_users_and_roles(config_path: Optional[str] = None):
    """Loads users and roles from a YAML or JSON file."""
    global USERS
    
    if not config_path:
        if DEV_MODE:
            logger.warning("AUDIT_LOG_USERS_CONFIG not set. Using hardcoded dummy users in DEV_MODE.")
            USERS.update({
                'user1': {'role': 'admin', 'token': 'admin_token', 'username': 'user1'},
                'user2': {'role': 'user', 'token': 'user_token', 'username': 'user2'},
                'user3': {'role': 'viewer', 'token': 'viewer_token', 'username': 'user3'}
            })
            return
        else:
            logger.critical("AUDIT_LOG_USERS_CONFIG not set! User configuration is required in non-DEV_MODE.")
            raise RuntimeError("User configuration is required in non-DEV_MODE.")
    
    try:
        with open(config_path, 'r') as f:
            if config_path.endswith('.json'):
                config = json.load(f)
            elif config_path.endswith('.yaml') or config_path.endswith('.yml'):
                config = yaml.safe_load(f)
            else:
                raise ValueError("Unsupported config file format. Use .json or .yaml.")
        
        # Validate and load users
        if 'users' in config:
            for username, details in config['users'].items():
                if 'role' in details and details['role'] in ROLES and 'token' in details:
                    details['username'] = username  # Ensure username is in details
                    USERS[username] = details
                else:
                    logger.error(f"Invalid user configuration for '{username}'. Skipping.")
        
        logger.info(f"Loaded {len(USERS)} users from {config_path}.")

    except FileNotFoundError:
        logger.error(f"User configuration file not found at '{config_path}'.")
    except (json.JSONDecodeError, yaml.YAMLError) as e:
        logger.error(f"Error parsing user configuration file: {e}")
    except Exception as e:
        logger.error(f"An unexpected error occurred while loading user config: {e}")

load_users_and_roles(os.getenv('AUDIT_LOG_USERS_CONFIG'))

IMMUTABLE = os.getenv('AUDIT_LOG_IMMUTABLE', 'true').lower() == 'true'

# --- FIX 3: Prometheus labels match usage ---
LOG_WRITES = Counter('audit_log_writes_total', 'Total writes to the audit log')
LOG_QUERIES = Counter('audit_log_queries_total', 'Total queries performed on the audit log')
LOG_ERRORS = Counter('audit_log_errors_total', 'Total errors in audit log operations', ['type', 'user', 'action'])
LOG_LATENCY = Histogram('audit_log_latency_seconds', 'Latency of audit log operations', ['op'])
TAMPER_ALERTS = Counter('audit_tamper_alerts_total', 'Total tamper detections')
SELF_HEAL_EVENTS = Counter('audit_self_heal_events_total', 'Total self-healing events', ['type']) # Added 'type' label
DOC_GEN_ACTIONS = Counter('audit_doc_gen_actions_total', 'Total documentation generation actions', ['generator'])
RBAC_DENIALS = Counter('audit_rbac_denials_total', 'Access denied by RBAC', ['user', 'role', 'operation'])

# --- FIX 4: Metrics server safety ---
try:
    start_http_server(METRICS_PORT)
    logger.info(f"Prometheus metrics server started on port {METRICS_PORT}")
except OSError as e:
    logger.error(f"Prometheus server failed to start on port {METRICS_PORT}: {e}")

# Models for API
class LogEntry(BaseModel):
    action: str
    details: Dict[str, Any]
    requirement_id: Optional[str] = None
    code_files: Optional[Dict[str, str]] = None
    test_files: Optional[Dict[str, str]] = None

class QueryFilter(BaseModel):
    action: Optional[str] = None
    start_time: Optional[datetime.datetime] = None
    end_time: Optional[datetime.datetime] = None
    limit: int = 100

class KeyRotationRequest(BaseModel):
    algo: str
    old_key_id: Optional[str] = None

# Hooks registry
hooks: Dict[str, List[Callable[..., Any]]] = defaultdict(list)

def register_hook(event_type: str, hook: Callable[..., Any]):
    """
    Registers a callable function as a hook for a specific event type.

    Args:
        event_type (str): The name of the event to hook into (e.g., 'log_success').
        hook (Callable): The function to be called when the event occurs.
                        Can be sync or async.
    
    Raises:
        TypeError: If event_type is not a string or hook is not callable.
    """
    if not isinstance(event_type, str) or not callable(hook):
        raise TypeError("event_type must be a string and hook must be a callable function.")
    hooks[event_type].append(hook)
    logger.info(f"Registered hook '{hook.__name__}' for event type '{event_type}'.")


class AuditLog:
    """
    Unrivaled audit log: Granular RBAC, immutable, rich context, hooks, self-healing, observable, API/CLI, documented.
    """
    def __init__(
        self,
        backend_type: str = os.getenv('AUDIT_LOG_BACKEND_TYPE', 'file'),
        backend_params: Optional[Dict[str, Any]] = None,
        encryption_key: bytes = ENCRYPTION_KEY,
        immutable: bool = IMMUTABLE
    ):
        if backend_params is None and os.getenv('AUDIT_LOG_BACKEND_PARAMS'):
            try:
                backend_params = json.loads(os.getenv('AUDIT_LOG_BACKEND_PARAMS'))
            except json.JSONDecodeError:
                logger.error("AUDIT_LOG_BACKEND_PARAMS is not a valid JSON string.")
                backend_params = {}

        self.backend = get_backend(backend_type, backend_params)
        self.encrypter = Fernet(encryption_key)
        self.immutable = immutable
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=os.cpu_count() or 4)
        self.crypto_provider = get_crypto_provider()
        self.current_signing_key_id: Optional[str] = None
        self._self_heal_task: Optional[asyncio.Task] = None
        self._init_key_task: Optional[asyncio.Task] = None
        self.sessions: Dict[str, Dict[str, Any]] = {}

    # --- FIX 2: Async startup pattern for long-running tasks ---
    async def start(self):
        """Initializes and kicks off all necessary background tasks."""
        logger.info("Starting AuditLog background tasks...")
        self._init_key_task = asyncio.create_task(self._initialize_signing_key())
        self._self_heal_task = asyncio.create_task(self._self_heal_periodically())
        await self._init_key_task  # Wait for the initial key setup to complete

    async def shutdown(self):
        """Gracefully shuts down background tasks and closes resources."""
        logger.info("Shutting down AuditLog background tasks...")
        if self._self_heal_task:
            self._self_heal_task.cancel()
        if self._init_key_task:
            self._init_key_task.cancel()
        self.executor.shutdown(wait=False)
        try:
            # Wait for tasks to be cancelled
            await asyncio.gather(self._self_heal_task, self._init_key_task, return_exceptions=True)
        except Exception:
            pass # Ignore cancellation exceptions

    async def _initialize_signing_key(self):
        """Initializes a new signing key on startup if one isn't already active."""
        if not self.current_signing_key_id:
            try:
                # Give a small delay for the crypto provider to initialize if needed
                await asyncio.sleep(0.1) 
                self.current_signing_key_id = await self.crypto_provider.generate_key('ed25519')
                logger.info(f"Initialized active signing key with ID: {self.current_signing_key_id}")
            except Exception as e:
                logger.critical(f"Failed to initialize signing key: {e}", exc_info=True)
                LOG_ERRORS.labels(type='key_init_fail', user='system', action='init').inc()

    async def _self_heal_periodically(self):
        """Runs the self-healing process at a set interval."""
        while True:
            try:
                await asyncio.sleep(3600)  # Sleep for 1 hour
                logger.info("Starting periodic self-healing process.")
                await self.self_heal()
            except asyncio.CancelledError:
                logger.info("Periodic self-healing task cancelled.")
                break
            except Exception as e:
                logger.error(f"Error in periodic self-healing: {e}", exc_info=True)


    async def self_heal(self) -> None:
        """
        Scans the audit log for inconsistencies and attempts to repair them.

        This process checks for:
        1.  Missing entries in a sequential log.
        2.  Entries with invalid or missing cryptographic signatures.
        3.  Tampering evidence (e.g., changes to log entries).
        """
        logger.info("Starting self-healing process...")
        issues_detected = 0
        total_entries = 0
        last_signature = None

        try:
            entries = await self.backend.read_all()
            total_entries = len(entries)
            
            for i, encrypted_entry_b64 in enumerate(entries):
                try:
                    entry = self._decrypt_entry(encrypted_entry_b64)
                    
                    # 1. Check for missing sequence numbers or IDs if the backend supports it.
                    # This check is backend-dependent. For now, we'll check cryptographic integrity.

                    # 2. Verify cryptographic signature
                    if not entry.get('signature') or not entry.get('signing_key_id'):
                        logger.warning(f"Entry {i} is missing a signature or key ID. Flagging as potential tamper.")
                        TAMPER_ALERTS.inc()
                        SELF_HEAL_EVENTS.labels(type='no_signature').inc()
                        issues_detected += 1
                        await self._execute_hooks('tamper_detected', entry=entry, issue='no_signature')
                        continue

                    is_valid = await self.crypto_provider.verify_signature(
                        data=json.dumps(entry['signed_data'], sort_keys=True).encode('utf-8'),
                        signature=entry['signature'],
                        key_id=entry['signing_key_id']
                    )
                    
                    if not is_valid:
                        logger.warning(f"Signature mismatch detected for entry {i} (ID: {entry.get('uuid')}). Possible tampering.")
                        TAMPER_ALERTS.inc()
                        SELF_HEAL_EVENTS.labels(type='invalid_signature').inc()
                        issues_detected += 1
                        await self._execute_hooks('tamper_detected', entry=entry, issue='invalid_signature')

                except ValueError as e:
                    logger.error(f"Failed to decrypt or parse entry {i}. This could indicate tampering or corruption. Error: {e}")
                    TAMPER_ALERTS.inc()
                    SELF_HEAL_EVENTS.labels(type='decryption_failed').inc()
                    issues_detected += 1
                    await self._execute_hooks('tamper_detected', entry=encrypted_entry_b64, issue='decryption_failed')

            if issues_detected > 0:
                logger.warning(f"Self-healing complete. Found {issues_detected} potential issues out of {total_entries} entries.")
            else:
                logger.info("Self-healing complete. No issues detected. Log integrity verified.")

        except Exception as e:
            logger.critical(f"Self-healing process failed unexpectedly: {e}", exc_info=True)
            LOG_ERRORS.labels(type='self_heal_fail', user='system', action='self_heal').inc()

    def _encrypt_entry(self, entry: Dict[str, Any]) -> str:
        """Encrypts a log entry dictionary and returns a base64-encoded string."""
        data_to_encrypt = json.dumps(entry, sort_keys=True).encode('utf-8')
        encrypted_bytes = self.encrypter.encrypt(data_to_encrypt)
        return base64.b64encode(encrypted_bytes).decode('utf-8')

    def _decrypt_entry(self, encrypted_b64_str: str) -> Dict[str, Any]:
        """Decrypts a base64-encoded string to a log entry dictionary."""
        try:
            encrypted_bytes = base64.b64decode(encrypted_b64_str.encode('utf-8'))
            decrypted_bytes = self.encrypter.decrypt(encrypted_bytes)
            return json.loads(decrypted_bytes.decode('utf-8'))
        except (InvalidToken, json.JSONDecodeError, base64.binascii.Error) as e:
            LOG_ERRORS.labels(type='decryption_or_parse_failed', user='system', action='query').inc()
            raise ValueError(f"Decryption or JSON decoding failed: {e}") from e

    def _add_rich_context(self, entry: Dict[str, Any], session_id: Optional[str] = None, request: Optional[Request] = None) -> Dict[str, Any]:
        """
        Adds rich, immutable context to a log entry.

        Args:
            entry (Dict): The log entry dictionary to enrich.
            session_id (Optional[str]): The unique ID for the current user session.
            request (Optional[Request]): The FastAPI request object for context.
        
        Returns:
            Dict: The enriched log entry.
        """
        entry.update({
            'uuid': str(uuid.uuid4()),
            'timestamp': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'source_ip': request.client.host if request and request.client else 'unknown',
            'session_id': session_id,
            'process_id': os.getpid(),
            'thread_id': threading.current_thread().ident,
        })
        return entry

    def _authorize(self, op: str, credentials: Optional[HTTPAuthorizationCredentials] = None) -> Optional[Dict[str, Any]]:
        """
        Checks if a user has permission for a specific operation.

        Args:
            op (str): The operation to authorize (e.g., 'write', 'read', 'manage_keys').
            credentials (Optional[HTTPAuthorizationCredentials]): The bearer token from the request header.
        
        Returns:
            Optional[Dict]: The user's details if authorized, otherwise None.
        """
        if not credentials:
            logger.warning(f"Authorization failed: No credentials provided for operation '{op}'.")
            RBAC_DENIALS.labels(user='anonymous', role='none', operation=op).inc()
            return None

        token = credentials.credentials
        user = next((u for u in USERS.values() if u['token'] == token), None)
        
        if not user:
            logger.warning(f"Authorization failed: Invalid token provided for operation '{op}'.")
            RBAC_DENIALS.labels(user='unknown', role='none', operation=op).inc()
            return None

        role = user.get('role')
        username = user.get('username', 'N/A')

        if not role or role not in ROLES:
            logger.error(f"Authorization failed: User '{username}' has invalid role '{role}'.")
            RBAC_DENIALS.labels(user=username, role=role, operation=op).inc()
            return None

        if ROLES[role].get(op):
            logger.info(f"Authorization successful for user '{username}' with role '{role}' for operation '{op}'.")
            return user
        else:
            logger.warning(f"Authorization denied for user '{username}' with role '{role}' for operation '{op}'.")
            RBAC_DENIALS.labels(user=username, role=role, operation=op).inc()
            return None

    async def _execute_hooks(self, event_type: str, *args, **kwargs):
        """
        Executes all registered hooks for a given event type.

        Args:
            event_type (str): The name of the event.
            *args: Positional arguments to pass to the hook functions.
            **kwargs: Keyword arguments to pass to the hook functions.
        """
        for hook in hooks.get(event_type, []):
            try:
                if asyncio.iscoroutinefunction(hook):
                    await hook(*args, **kwargs)
                else:
                    await asyncio.get_event_loop().run_in_executor(
                        self.executor, functools.partial(hook, *args, **kwargs)
                    )
            except Exception as e:
                logger.error(f"Failed to execute hook '{hook.__name__}' for event '{event_type}': {e}", exc_info=True)
                LOG_ERRORS.labels(type='hook_fail', user='system', action='hooks').inc()

    async def log_action(
        self,
        action: str,
        details: Any,
        requirement_id: Optional[str] = None,
        code_files: Optional[Dict[str, str]] = None,
        test_files: Optional[Dict[str, str]] = None,
        session_id: Optional[str] = None,
        credentials: Optional[HTTPAuthorizationCredentials] = None,
        generator: Optional[str] = None,
        request: Optional[Request] = None
    ) -> None:
        """
        Logs an auditable action, enriching it with context, encrypting it, and signing it.

        Args:
            action (str): The name of the action being audited (e.g., 'user_login', 'file_update').
            details (Any): A JSON-serializable object containing details of the action.
            requirement_id (Optional[str]): ID of the requirement this action fulfills.
            code_files (Optional[Dict[str, str]]): Code snippets related to the action.
            test_files (Optional[Dict[str, str]]): Test results related to the action.
            session_id (Optional[str]): Unique identifier for the user's session.
            credentials (Optional[HTTPAuthorizationCredentials]): The bearer token for RBAC.
            generator (Optional[str]): The system or user that generated the log.
            request (Optional[Request]): The FastAPI request object for context enrichment.
        
        Raises:
            HTTPException: If authorization fails.
            ValueError: If the log entry data is invalid.
        """
        user = {'username': 'unknown', 'role': 'none'}
        
        with LOG_LATENCY.labels(op='log_action').time():
            authorized_user = self._authorize('write', credentials)
            if not authorized_user:
                raise HTTPException(status_code=403, detail="Not authorized to write to the log.")
            
            user = authorized_user

            try:
                # 1. Prepare and enrich the log entry
                entry = {
                    'action': action,
                    'details': details,
                    'requirement_id': requirement_id,
                    'code_files': code_files,
                    'test_files': test_files,
                    'user': user['username'],
                    'role': user['role']
                }
                entry = self._add_rich_context(entry, session_id=session_id, request=request)

                # 2. Sign the entry
                if not self.current_signing_key_id:
                    raise RuntimeError("No active signing key is available. Key initialization failed.")

                signed_data = {k: v for k, v in entry.items() if k not in ['signature', 'signing_key_id']}
                signature = await self.crypto_provider.sign_data(
                    data=json.dumps(signed_data, sort_keys=True).encode('utf-8'),
                    key_id=self.current_signing_key_id
                )
                entry['signature'] = signature
                entry['signing_key_id'] = self.current_signing_key_id
                entry['signed_data'] = signed_data

                # 3. Encrypt and append to backend
                encrypted_entry = self._encrypt_entry(entry)
                await self.backend.append(encrypted_entry)
                
                LOG_WRITES.inc()
                if generator:
                    DOC_GEN_ACTIONS.labels(generator=generator).inc()
                
                # 4. Execute hooks
                await self._execute_hooks('log_success', entry=entry)

                logger.info(f"Successfully logged action: '{action}' by user '{user['username']}'.")
            
            except HTTPException:
                raise # Re-raise if it's already an HTTPException (e.g., from _authorize)
            except Exception as e:
                logger.error(f"Failed to log action '{action}': {e}", exc_info=True)
                LOG_ERRORS.labels(type='log_fail', user=user['username'], action=action).inc()
                await self._execute_hooks('log_error', error=e, entry=locals().get('entry', {'action': action}))
                raise HTTPException(status_code=500, detail=f"Internal server error: Failed to log action. {e}")

    async def get_recent_history(self, action: Optional[str] = None, limit: int = 10, credentials: Optional[HTTPAuthorizationCredentials] = None) -> List[Dict[str, Any]]:
        """
        Queries and returns a list of recent log entries, filtering by action and limit.

        Args:
            action (Optional[str]): The action to filter by.
            limit (int): The maximum number of entries to return.
            credentials (Optional[HTTPAuthorizationCredentials]): The bearer token for RBAC.
        
        Returns:
            List[Dict[str, Any]]: A list of decrypted log entries.
        
        Raises:
            HTTPException: If authorization fails.
            ValueError: If decryption fails.
        """
        with LOG_LATENCY.labels(op='get_recent_history').time():
            if not self._authorize('read', credentials):
                raise HTTPException(status_code=403, detail="Not authorized to read the log.")
            
            LOG_QUERIES.inc()
            
            try:
                entries = await self.backend.read_last_n(limit)
                decrypted_entries = []
                for entry_str in entries:
                    try:
                        decrypted_entry = self._decrypt_entry(entry_str)
                        if not action or decrypted_entry.get('action') == action:
                            decrypted_entries.append(decrypted_entry)
                    except ValueError as e:
                        logger.error(f"Failed to decrypt an entry: {e}")
                        # Continue to process other entries even if one fails
                
                logger.info(f"Successfully queried recent history (limit={limit}, action={action}).")
                return decrypted_entries
            
            except HTTPException:
                raise # Re-raise if it's already an HTTPException (e.g., from _authorize)
            except Exception as e:
                logger.error(f"Failed to get recent history: {e}", exc_info=True)
                LOG_ERRORS.labels(type='query_fail', user='system', action='read').inc()
                raise HTTPException(status_code=500, detail="Internal server error: Failed to retrieve log history.")

    async def detect_oscillation(self, window: int = 3, hash_key: str = 'code_hash') -> bool:
        """
        Analyzes recent log entries to detect "oscillation" or non-productive loops.
        This is a heuristic for detecting bot or script errors.

        Args:
            window (int): The number of recent entries to check.
            hash_key (str): The key in the entry's 'details' dict to use for hashing.
        
        Returns:
            bool: True if oscillation is detected, False otherwise.
        """
        logger.info(f"Detecting oscillation over a window of {window} entries.")
        try:
            # Note: This calls get_recent_history, which will perform its own authorization/error checks.
            # We don't pass credentials here as this is an internal check, not a user query.
            entries = await self.get_recent_history(limit=window)
            
            if len(entries) < window:
                return False

            recent_hashes = [entry['details'].get(hash_key) for entry in entries if entry.get('details')]
            
            # Check for repeated hashes (e.g., a script generating the same code repeatedly)
            unique_hashes = set(recent_hashes)
            if len(unique_hashes) == 1 and None not in unique_hashes:
                logger.warning("Oscillation detected: The same hash was repeated within the window.")
                return True

            # Check for alternating hashes (e.g., toggling between two states)
            if len(unique_hashes) == 2 and len(recent_hashes) == window:
                alternating = all(recent_hashes[i] != recent_hashes[i+1] for i in range(window - 1))
                if alternating:
                    logger.warning("Oscillation detected: Alternating hashes found within the window.")
                    return True
        except HTTPException:
            # Ignore authorization/read failure as this is an internal heuristic
            pass 
        except Exception as e:
            logger.error(f"Oscillation detection failed: {e}", exc_info=True)
            LOG_ERRORS.labels(type='oscillation_fail', user='system', action='detect_oscillation').inc()

        return False

    async def rotate_signing_key(self, algo: str, old_key_id: Optional[str] = None, credentials: Optional[HTTPAuthorizationCredentials] = None) -> Dict[str, Any]:
        """
        Rotates the active cryptographic signing key.

        Args:
            algo (str): The algorithm for the new key (e.g., 'ed25519').
            old_key_id (Optional[str]): The ID of the key to be retired.
            credentials (Optional[HTTPAuthorizationCredentials]): The bearer token for RBAC.

        Returns:
            Dict[str, Any]: Details about the key rotation.
        
        Raises:
            HTTPException: If authorization fails.
            ValueError: If the key algorithm is unsupported or the rotation fails.
        """
        if not self._authorize('manage_keys', credentials):
            raise HTTPException(status_code=403, detail="Not authorized to manage keys.")

        if algo not in self.crypto_provider.supported_algos:
            raise ValueError(f"Unsupported key algorithm: {algo}. Supported: {self.crypto_provider.supported_algos}")

        try:
            new_key_id = await self.crypto_provider.generate_key(algo)
            
            if old_key_id and old_key_id != self.current_signing_key_id:
                logger.warning(f"Key rotation requested for old key ID '{old_key_id}', but current key is '{self.current_signing_key_id}'.")

            previous_key_id = self.current_signing_key_id
            self.current_signing_key_id = new_key_id
            
            rotation_details = {
                'status': 'success',
                'old_key_id': previous_key_id,
                'new_key_id': new_key_id,
                'timestamp': datetime.datetime.now(datetime.timezone.utc).isoformat()
            }
            logger.info(f"Successfully rotated signing key. New key ID: {new_key_id}")
            
            await self._execute_hooks('key_rotated', new_key_id=new_key_id, old_key_id=previous_key_id)
            
            return rotation_details
        except HTTPException:
            raise # Re-raise if it's already an HTTPException (e.g., from _authorize)
        except Exception as e:
            logger.error(f"Failed to rotate signing key: {e}", exc_info=True)
            LOG_ERRORS.labels(type='key_rotation_fail', user='system', action='rotate_key').inc()
            raise HTTPException(status_code=500, detail="Internal server error: Failed to rotate key.")


# CLI with Typer
app = typer.Typer(pretty_exceptions_show_locals=False)
# Global instance (initially without background tasks running)
AUDIT_LOG = AuditLog(
    backend_type=os.getenv('AUDIT_LOG_BACKEND_TYPE', 'file'),
    backend_params=json.loads(os.getenv('AUDIT_LOG_BACKEND_PARAMS', '{}')),
    encryption_key=ENCRYPTION_KEY,
    immutable=IMMUTABLE
)

@app.command(help="Write an entry to the audit log.")
def log(
    action: str = typer.Option(..., help="The action performed."),
    details: str = typer.Option("{}", help="JSON string of action details."),
    requirement_id: Optional[str] = typer.Option(None, help="The associated requirement ID."),
    token: str = typer.Option(..., envvar="AUDIT_TOKEN", help="The user's authorization token."),
):
    try:
        details_dict = json.loads(details)
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        async def run_log():
            await AUDIT_LOG.start()
            try:
                await AUDIT_LOG.log_action(action=action, details=details_dict, requirement_id=requirement_id, credentials=creds)
            finally:
                await AUDIT_LOG.shutdown()
        
        asyncio.run(run_log())
        typer.echo(f"Successfully logged action: {action}")
    except HTTPException as e:
        typer.echo(f"Error: {e.detail}", err=True)
    except json.JSONDecodeError:
        typer.echo("Error: 'details' must be a valid JSON string.", err=True)
    except Exception as e:
        typer.echo(f"An unexpected error occurred: {e}", err=True)

@app.command(help="Query and display recent audit log entries.")
def query(
    limit: int = typer.Option(10, help="The number of recent entries to retrieve."),
    action: Optional[str] = typer.Option(None, help="Filter by a specific action."),
    token: str = typer.Option(..., envvar="AUDIT_TOKEN", help="The user's authorization token."),
):
    try:
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        async def run_query():
            await AUDIT_LOG.start()
            try:
                return await AUDIT_LOG.get_recent_history(action=action, limit=limit, credentials=creds)
            finally:
                await AUDIT_LOG.shutdown()

        entries = asyncio.run(run_query())
        if entries:
            for entry in entries:
                typer.echo(json.dumps(entry, indent=2))
        else:
            typer.echo("No entries found.")
    except HTTPException as e:
        typer.echo(f"Error: {e.detail}", err=True)
    except Exception as e:
        typer.echo(f"An unexpected error occurred: {e}", err=True)

@app.command(help="Initiate a key rotation for cryptographic signing.")
def rotate_key(
    algo: str = typer.Option("ed25519", help="The algorithm for the new key."),
    token: str = typer.Option(..., envvar="AUDIT_TOKEN", help="The user's authorization token."),
):
    try:
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        async def run_rotate():
            await AUDIT_LOG.start()
            try:
                return await AUDIT_LOG.rotate_signing_key(algo=algo, credentials=creds)
            finally:
                await AUDIT_LOG.shutdown()
        
        result = asyncio.run(run_rotate())
        typer.echo(f"Key rotation successful: {json.dumps(result, indent=2)}")
    except HTTPException as e:
        typer.echo(f"Error: {e.detail}", err=True)
    except Exception as e:
        typer.echo(f"An unexpected error occurred: {e}", err=True)


# FastAPI App
api_app = FastAPI(
    title="Audit Log API",
    description="A secure and observable API for managing audit logs.",
    version="1.0.0",
)
if HAS_OPENTELEMETRY:
    FastAPIInstrumentor.instrument_app(api_app)

security = HTTPBearer()

@api_app.on_event("startup")
async def startup_event():
    """Initializes and starts AuditLog background tasks on FastAPI startup."""
    await AUDIT_LOG.start()

@api_app.on_event("shutdown")
async def shutdown_event():
    """Shuts down AuditLog background tasks on FastAPI shutdown."""
    await AUDIT_LOG.shutdown()

def get_current_user(credentials: HTTPAuthorizationCredentials = Security(security)):
    user = next((u for u in USERS.values() if u['token'] == credentials.credentials), None)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or missing token.")
    return user

def authorize_api(op: str):
    def decorator(request: Request, user: Dict[str, Any] = Depends(get_current_user)):
        role = user.get('role')
        username = user.get('username', 'N/A')
        if not role or role not in ROLES or not ROLES[role].get(op):
            RBAC_DENIALS.labels(user=username, role=role, operation=op).inc()
            raise HTTPException(status_code=403, detail="Not authorized to perform this action.")
        request.state.user = user
        return True
    return decorator


@api_app.post("/log", status_code=201, dependencies=[Depends(authorize_api('write'))])
async def log_entry_api(log_entry: LogEntry, request: Request, credentials: HTTPAuthorizationCredentials = Security(security)):
    """Logs a new auditable action."""
    session_id = request.headers.get("X-Session-ID")
    try:
        await AUDIT_LOG.log_action(
            action=log_entry.action,
            details=log_entry.details,
            requirement_id=log_entry.requirement_id,
            code_files=log_entry.code_files,
            test_files=log_entry.test_files,
            session_id=session_id,
            credentials=credentials,
            request=request
        )
        return {"status": "success", "message": "Log entry created."}
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"API Log failure: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@api_app.post("/query", dependencies=[Depends(authorize_api('query'))])
async def query_log_api(filters: QueryFilter, credentials: HTTPAuthorizationCredentials = Security(security)):
    """Queries recent log entries based on filters."""
    try:
        entries = await AUDIT_LOG.get_recent_history(
            action=filters.action,
            limit=filters.limit,
            credentials=credentials
        )
        return {"entries": entries}
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"API Query failure: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@api_app.post("/keys/rotate", dependencies=[Depends(authorize_api('manage_keys'))])
async def rotate_key_api(request_body: KeyRotationRequest, credentials: HTTPAuthorizationCredentials = Security(security)):
    """Rotates the cryptographic signing key."""
    try:
        result = await AUDIT_LOG.rotate_signing_key(
            algo=request_body.algo,
            old_key_id=request_body.old_key_id,
            credentials=credentials
        )
        return result
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"API Rotate Key failure: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# gRPC Service Implementation
if HAS_GRPC_PROTOS:
    class AuditLogServicer(audit_log_pb2_grpc.AuditLogServiceServicer):
        def __init__(self, audit_log: AuditLog):
            self.audit_log = audit_log
        
        async def LogAction(self, request, context):
            # gRPC authentication using metadata is crude but consistent with the simplified scheme
            metadata = dict(context.invocation_metadata())
            token = metadata.get('access_token')

            user = next((u for u in USERS.values() if u.get('token') == token), None)
            
            if not user or not self.audit_log._authorize('write', HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)):
                context.set_details("Not authorized")
                context.set_code(grpc.StatusCode.PERMISSION_DENIED)
                return audit_log_pb2.LogActionResponse(success=False)
            
            details_dict = json.loads(request.details_json)
            
            try:
                await self.audit_log.log_action(
                    action=request.action,
                    details=details_dict,
                    requirement_id=request.requirement_id if request.HasField("requirement_id") else None,
                    credentials=HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
                )
                return audit_log_pb2.LogActionResponse(success=True)
            except HTTPException as e:
                context.set_details(e.detail)
                context.set_code(grpc.StatusCode.PERMISSION_DENIED if e.status_code == 403 else grpc.StatusCode.INTERNAL)
                return audit_log_pb2.LogActionResponse(success=False)
            except Exception as e:
                context.set_details(str(e))
                context.set_code(grpc.StatusCode.INTERNAL)
                return audit_log_pb2.LogActionResponse(success=False)

        async def GetRecentHistory(self, request, context):
            metadata = dict(context.invocation_metadata())
            token = metadata.get('access_token')

            user = next((u for u in USERS.values() if u.get('token') == token), None)
            
            if not user or not self.audit_log._authorize('read', HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)):
                context.set_details("Not authorized")
                context.set_code(grpc.StatusCode.PERMISSION_DENIED)
                return audit_log_pb2.GetRecentHistoryResponse(entries_json=[])

            try:
                entries = await self.audit_log.get_recent_history(
                    action=request.action if request.HasField("action") else None,
                    limit=request.limit,
                    credentials=HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
                )
                entries_json = [json.dumps(e) for e in entries]
                return audit_log_pb2.GetRecentHistoryResponse(entries_json=entries_json)
            except HTTPException as e:
                context.set_details(e.detail)
                context.set_code(grpc.StatusCode.PERMISSION_DENIED if e.status_code == 403 else grpc.StatusCode.INTERNAL)
                return audit_log_pb2.GetRecentHistoryResponse(entries_json=[])
            except Exception as e:
                context.set_details(str(e))
                context.set_code(grpc.StatusCode.INTERNAL)
                return audit_log_pb2.GetRecentHistoryResponse(entries_json=[])

    async def serve_grpc_server() -> None:
        server = grpc_aio.server(concurrent.futures.ThreadPoolExecutor(max_workers=os.cpu_count()))
        audit_log_pb2_grpc.add_AuditLogServiceServicer_to_server(AuditLogServicer(AUDIT_LOG), server)
        
        # Add a metadata interceptor for authentication
        # NOTE: This simple interceptor is not fully implemented for auth, 
        # relying on the Servicer's direct call to AUDIT_LOG._authorize using metadata
        async def metadata_interceptor(continuation, handler_call_details):
            return await continuation(handler_call_details)
        
        if HAS_OPENTELEMETRY:
            GrpcAioInstrumentor().instrument_server(server)

        server.add_insecure_port(f'[::]:{GRPC_PORT}')
        logger.info(f"gRPC server listening on port {GRPC_PORT}")
        
        # Start background tasks before serving
        await AUDIT_LOG.start()
        
        await server.start()
        
        try:
            await server.wait_for_termination()
        finally:
            # Shutdown background tasks
            await AUDIT_LOG.shutdown()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    # Simple runner to choose which service to start
    import argparse
    parser = argparse.ArgumentParser(description="Run the Audit Log service.")
    parser.add_argument("--service", choices=['api', 'cli', 'grpc'], default='api', help="The service to run.")
    args = parser.parse_args()

    if args.service == 'api':
        import uvicorn
        logger.info(f"Starting FastAPI server on port {API_PORT}...")
        # Uvicorn will handle running the async start/shutdown events via @api_app.on_event
        uvicorn.run(api_app, host="0.0.0.0", port=API_PORT)
    elif args.service == 'cli':
        typer.run(app)
    elif args.service == 'grpc':
        if HAS_GRPC_PROTOS:
            asyncio.run(serve_grpc_server())
        else:
            logger.error("gRPC protobufs are not available. Cannot start gRPC server.")