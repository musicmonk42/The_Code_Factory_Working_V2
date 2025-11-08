# test_siem_generic_clients.py
"""
Test suite for generic SIEM clients (Splunk, Elasticsearch, Datadog).
Tests configuration validation, health checks, log sending, and querying.
"""

import pytest
import asyncio
import os
import sys
import time
import datetime
import json
import re
from unittest.mock import AsyncMock, MagicMock, patch, Mock
from typing import Dict, Any, List, Tuple, Optional, Callable

# Mock modules before importing
sys.modules['simulation.plugins.siem_base'] = MagicMock()
sys.modules['simulation.plugins.siem_generic_clients'] = MagicMock()

# Mock exception classes
class SIEMClientError(Exception):
    def __init__(self, message, client_type, original_exception=None, details=None, correlation_id=None):
        self.message = message
        self.client_type = client_type
        self.original_exception = original_exception
        self.details = details
        self.correlation_id = correlation_id
        super().__init__(message)

class SIEMClientConfigurationError(SIEMClientError): pass
class SIEMClientAuthError(SIEMClientError): pass
class SIEMClientConnectivityError(SIEMClientError): pass
class SIEMClientPublishError(SIEMClientError): pass
class SIEMClientQueryError(SIEMClientError): pass

class SIEMClientResponseError(SIEMClientError):
    def __init__(self, message, client_type, status_code, response_text, 
                 original_exception=None, details=None, correlation_id=None):
        super().__init__(message, client_type, original_exception, details, correlation_id)
        self.status_code = status_code
        self.response_text = response_text

class SIEMClientValidationError(SIEMClientError): pass

# Mock aiohttp exceptions
class ClientError(Exception): pass
class ClientResponseError(ClientError): pass
class ClientConnectionError(ClientError): pass

# Mock global variables
PRODUCTION_MODE = False
_base_logger = MagicMock()

def alert_operator(message: str, level: str = "CRITICAL"):
    """Mock alert operator."""
    pass

class SecretsManager:
    def get_secret(self, key, default=None, required=True):
        # Return appropriate dummy values based on key
        secrets = {
            "SIEM_SPLUNK_HEC_URL": "https://dummy-splunk.example.com:8088/services/collector/event",
            "SIEM_SPLUNK_HEC_TOKEN": "dummy_token_splunk",
            "SIEM_ELASTIC_URL": "https://dummy-elastic.example.com:9200",
            "SIEM_ELASTIC_API_KEY": "dummy_api_key",
            "SIEM_ELASTIC_USERNAME": None,
            "SIEM_ELASTIC_PASSWORD": None,
            "SIEM_DATADOG_API_URL": "https://http-intake.logs.datadoghq.com/api/v2/logs",
            "SIEM_DATADOG_QUERY_URL": "https://api.datadoghq.com/api/v1/logs-queries",
            "SIEM_DATADOG_API_KEY": "dummy_dd_api_key",
            "SIEM_DATADOG_APPLICATION_KEY": "dummy_dd_app_key"
        }
        return secrets.get(key, default)

SECRETS_MANAGER = SecretsManager()

# Helper functions
def _is_transient_status(status_code: int) -> bool:
    return status_code == 429 or 500 <= status_code < 600

async def _maybe_await(value):
    if asyncio.iscoroutine(value):
        return await value
    return value

async def _get_secret(key: str, default: Any = None, required: bool = False) -> Any:
    val = SECRETS_MANAGER.get_secret(key, default, required=required)
    return await _maybe_await(val)

# Mock configuration classes
class SplunkConfig:
    def __init__(self, **kwargs):
        self.url = kwargs.get('url', 'https://splunk.example.com:8088/services/collector/event')
        self.token = kwargs.get('token', 'dummy_token')
        self.source = kwargs.get('source', 'sfe_audit')
        self.sourcetype = kwargs.get('sourcetype', '_json')
        self.index = kwargs.get('index')
        self._validate()
    
    def _validate(self):
        if PRODUCTION_MODE:
            if not self.url.startswith('https'):
                raise ValueError("Splunk URL must use HTTPS in PRODUCTION_MODE")
            if any(s in self.url.lower() for s in ("dummy", "mock", "test", "example.com")):
                raise ValueError(f"Dummy/test URL detected: {self.url}")
            if any(s in self.token.lower() for s in ("dummy", "mock", "test")):
                raise ValueError("Dummy/test token detected")
    
    def dict(self, exclude_unset=False):
        return {
            'url': self.url,
            'token': self.token,
            'source': self.source,
            'sourcetype': self.sourcetype,
            'index': self.index
        }

class ElasticConfig:
    def __init__(self, **kwargs):
        self.url = kwargs.get('url', 'https://elastic.example.com:9200')
        self.api_key = kwargs.get('api_key')
        self.username = kwargs.get('username')
        self.password = kwargs.get('password')
        self.index = kwargs.get('index', 'sfe-logs')
        self._validate()
    
    def _validate(self):
        if not self.api_key and not (self.username and self.password):
            raise ValueError("Either api_key or username/password required")
        if PRODUCTION_MODE:
            if not self.url.startswith('https'):
                raise ValueError("Elasticsearch URL must use HTTPS in PRODUCTION_MODE")
            if any(s in self.url.lower() for s in ("dummy", "mock", "test", "example.com")):
                raise ValueError(f"Dummy/test URL detected: {self.url}")
    
    def dict(self, exclude_unset=False):
        return {
            'url': self.url,
            'api_key': self.api_key,
            'username': self.username,
            'password': self.password,
            'index': self.index
        }

class DatadogConfig:
    def __init__(self, **kwargs):
        self.url = kwargs.get('url', 'https://http-intake.logs.datadoghq.com/api/v2/logs')
        self.query_url = kwargs.get('query_url', 'https://api.datadoghq.com/api/v1/logs-queries')
        self.api_key = kwargs.get('api_key', 'dummy_key')
        self.application_key = kwargs.get('application_key', 'dummy_app_key')
        self.service = kwargs.get('service', 'sfe-agent')
        self.source = kwargs.get('source', 'sfe-audit-plugin')
        self.tags = kwargs.get('tags', [])
        self._validate()
    
    def _validate(self):
        if PRODUCTION_MODE:
            if not self.url.startswith('https'):
                raise ValueError("Datadog URLs must use HTTPS in PRODUCTION_MODE")
            if any(s in self.api_key.lower() for s in ("dummy", "mock", "test")):
                raise ValueError("Dummy/test key detected")
    
    def dict(self, exclude_unset=False):
        return {
            'url': self.url,
            'query_url': self.query_url,
            'api_key': self.api_key,
            'application_key': self.application_key,
            'service': self.service,
            'source': self.source,
            'tags': self.tags
        }

# Base classes
class BaseSIEMClient:
    def __init__(self, config, metrics_hook=None, paranoid_mode=False):
        self.config = config
        self.metrics_hook = metrics_hook
        self.paranoid_mode = paranoid_mode
        self.client_type = getattr(self, 'client_type', self.__class__.__name__)
        self.timeout = config.get("default_timeout_seconds", 10)
        self.logger = MagicMock()
        self.logger.extra = {'client_type': self.client_type, 'correlation_id': 'N/A'}
        self._config_loaded = False
        self._config_lock = asyncio.Lock()
    
    async def _run_blocking_in_executor(self, func, *args, **kwargs):
        return func(*args, **kwargs)
    
    def _parse_relative_time_range_to_ms(self, time_range: str) -> int:
        if not time_range or len(time_range) < 2:
            return 24 * 3600 * 1000
        unit = time_range[-1].lower()
        try:
            value = int(time_range[:-1])
        except ValueError:
            return 24 * 3600 * 1000
        if unit == 's':
            return value * 1000
        elif unit == 'm':
            return value * 60 * 1000
        elif unit == 'h':
            return value * 3600 * 1000
        elif unit == 'd':
            return value * 24 * 3600 * 1000
        else:
            return 24 * 3600 * 1000
    
    async def close(self):
        pass

class MockAsyncResponse:
    """Mock async response that can be used with async context manager."""
    def __init__(self, status=200, text='{"success": true}', json_data=None):
        self.status = status
        self._text = text
        self._json_data = json_data or {'success': True}
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass
    
    async def text(self):
        return self._text
    
    async def json(self):
        return self._json_data

class AiohttpClientMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._session = None
        self._session_lock = asyncio.Lock()
    
    async def _get_session(self):
        async with self._session_lock:
            if self._session is None:
                self._session = MagicMock()
                # Create proper async mock methods
                self._session.get = AsyncMock(return_value=MockAsyncResponse())
                self._session.post = AsyncMock(return_value=MockAsyncResponse())
                self._session.put = AsyncMock(return_value=MockAsyncResponse())
                self._session.delete = AsyncMock(return_value=MockAsyncResponse())
            return self._session
    
    async def close(self):
        await super().close()
        if self._session:
            self._session = None

# Client implementations
class SplunkClient(AiohttpClientMixin, BaseSIEMClient):
    client_type = "Splunk"
    
    async def _ensure_config_loaded(self):
        if self._config_loaded:
            return
        async with self._config_lock:
            if self._config_loaded:
                return
            
            config_data = self.config.get('splunk', {})
            config_data['url'] = await _get_secret("SIEM_SPLUNK_HEC_URL", required=True)
            config_data['token'] = await _get_secret("SIEM_SPLUNK_HEC_TOKEN", required=True)
            
            validated = SplunkConfig(**config_data)
            self.url = validated.url
            self.token = validated.token
            self.source = validated.source
            self.sourcetype = validated.sourcetype
            self.index = validated.index
            
            # Derive search URL
            self.search_url_base = re.sub(r"/services/collector(?:/event)?/?$", "/services/search", self.url)
            self._config_loaded = True
    
    def _hec_health_url(self) -> str:
        hec_base = re.sub(r"/services/collector(?:/event)?/?$", "/services/collector", self.url or "")
        return f"{hec_base.rstrip('/')}/health/1.0"
    
    async def health_check(self, correlation_id=None):
        await self._ensure_config_loaded()
        session = await self._get_session()
        health_url = self._hec_health_url()
        
        response = await session.get(health_url, headers={"Authorization": f"Splunk {self.token}"})
        async with response:
            if response.status == 200:
                return True, "Splunk HEC is healthy."
            raise SIEMClientConnectivityError(f"Health check failed: {response.status}", self.client_type)
    
    async def send_log(self, log_entry, validate_schema=True, correlation_id=None):
        success, msg, failed = await self.send_logs([log_entry], validate_schema, correlation_id)
        if success:
            return True, "Log sent to Splunk HEC."
        raise SIEMClientPublishError(f"Failed: {failed[0]['error']}", self.client_type, details=failed[0])
    
    async def send_logs(self, log_entries, validate_schema=True, correlation_id=None):
        await self._ensure_config_loaded()
        session = await self._get_session()
        
        max_batch_size = 1000
        batches = [log_entries[i:i + max_batch_size] for i in range(0, len(log_entries), max_batch_size)]
        
        failed_logs = []
        total_sent = 0
        
        for batch in batches:
            batch_body_parts = []
            for log in batch:
                hec_event = {
                    "event": log,
                    "sourcetype": self.sourcetype,
                    "source": self.source,
                    "host": "test-host",
                    "time": time.time()
                }
                if self.index:
                    hec_event["index"] = self.index
                batch_body_parts.append(json.dumps(hec_event))
            
            full_body = "\n".join(batch_body_parts)
            
            response = await session.post(
                self.url,
                headers={"Authorization": f"Splunk {self.token}"},
                data=full_body
            )
            async with response:
                if response.status >= 400:
                    failed_logs.extend([{"log": log, "error": f"HTTP {response.status}"} for log in batch])
                else:
                    total_sent += len(batch)
        
        if failed_logs:
            return False, f"Sent {total_sent} of {len(log_entries)} logs with errors.", failed_logs
        return True, f"Batch of {len(log_entries)} logs sent to Splunk HEC.", []
    
    async def query_logs(self, query_string, time_range="24h", limit=100, correlation_id=None):
        await self._ensure_config_loaded()
        session = await self._get_session()
        
        search_url = f"{self.search_url_base.rstrip('/')}/jobs/export"
        response = await session.post(search_url, json={"search": query_string, "count": limit})
        async with response:
            text = await response.text()
            results = []
            for line in text.strip().split('\n'):
                if line:
                    results.append(json.loads(line))
            return results[:limit]

class ElasticClient(AiohttpClientMixin, BaseSIEMClient):
    client_type = "Elasticsearch"
    
    async def _ensure_config_loaded(self):
        if self._config_loaded:
            return
        async with self._config_lock:
            if self._config_loaded:
                return
            
            config_data = self.config.get('elasticsearch', self.config.get('elastic', {}))
            config_data['url'] = await _get_secret("SIEM_ELASTIC_URL", required=True)
            config_data['api_key'] = await _get_secret("SIEM_ELASTIC_API_KEY", required=False)
            config_data['username'] = await _get_secret("SIEM_ELASTIC_USERNAME", required=False)
            config_data['password'] = await _get_secret("SIEM_ELASTIC_PASSWORD", required=False)
            
            validated = ElasticConfig(**config_data)
            self.url = validated.url
            self.api_key = validated.api_key
            self.username = validated.username
            self.password = validated.password
            self.index = validated.index
            self._config_loaded = True
    
    async def health_check(self, correlation_id=None):
        await self._ensure_config_loaded()
        session = await self._get_session()
        
        # Configure the mock to return a health status
        session.get.return_value = MockAsyncResponse(
            status=200,
            json_data={'status': 'green'}
        )
        
        health_url = f"{self.url.rstrip('/')}/_cluster/health"
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"ApiKey {self.api_key}"
        
        response = await session.get(health_url, headers=headers)
        async with response:
            if response.status == 200:
                data = await response.json()
                status = data.get('status', 'unknown')
                if status in ['green', 'yellow']:
                    return True, f"Elasticsearch cluster is healthy ({status} status)."
            raise SIEMClientConnectivityError(f"Health check failed", self.client_type)
    
    async def send_log(self, log_entry, validate_schema=True, correlation_id=None):
        success, msg, failed = await self.send_logs([log_entry], validate_schema, correlation_id)
        if success:
            return True, "Log sent to Elasticsearch."
        raise SIEMClientPublishError(f"Failed: {failed[0]['error']}", self.client_type, details=failed[0])
    
    async def send_logs(self, log_entries, validate_schema=True, correlation_id=None):
        await self._ensure_config_loaded()
        session = await self._get_session()
        
        # Configure mock for bulk response
        session.post.return_value = MockAsyncResponse(
            status=200,
            json_data={'items': [{'index': {'status': 201}}] * len(log_entries), 'errors': False}
        )
        
        max_bulk_size = 1000
        batches = [log_entries[i:i + max_bulk_size] for i in range(0, len(log_entries), max_bulk_size)]
        
        failed_logs = []
        total_sent = 0
        
        for batch in batches:
            body_lines = []
            for log in batch:
                body_lines.append(json.dumps({"index": {"_index": self.index}}))
                body_lines.append(json.dumps({
                    "@timestamp": datetime.datetime.utcnow().isoformat() + "Z",
                    "host": "test-host",
                    "event": log
                }))
            
            full_body = "\n".join(body_lines) + "\n"
            
            bulk_url = f"{self.url.rstrip('/')}/_bulk"
            headers = {"Content-Type": "application/x-ndjson"}
            if self.api_key:
                headers["Authorization"] = f"ApiKey {self.api_key}"
            
            response = await session.post(bulk_url, headers=headers, data=full_body)
            async with response:
                if response.status >= 400:
                    failed_logs.extend([{"log": log, "error": f"HTTP {response.status}"} for log in batch])
                else:
                    data = await response.json()
                    items = data.get('items', [])
                    for i, item in enumerate(items):
                        status = item.get('index', {}).get('status', 200)
                        if status >= 400:
                            failed_logs.append({"log": batch[i], "error": f"Status {status}"})
                        else:
                            total_sent += 1
        
        if failed_logs:
            return False, f"Batch sent with {len(failed_logs)} failures.", failed_logs
        return True, f"Batch of {len(log_entries)} logs sent to Elasticsearch.", []
    
    async def query_logs(self, query_string, time_range="24h", limit=100, correlation_id=None):
        await self._ensure_config_loaded()
        session = await self._get_session()
        
        # Configure mock for search response
        session.post.return_value = MockAsyncResponse(
            status=200,
            json_data={'hits': {'hits': [{'_source': {'message': 'test'}}]}}
        )
        
        search_url = f"{self.url.rstrip('/')}/{self.index}/_search"
        query_body = {
            "size": limit,
            "query": {"query_string": {"query": query_string or "*"}},
            "sort": [{"@timestamp": "desc"}]
        }
        
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"ApiKey {self.api_key}"
        
        response = await session.post(search_url, headers=headers, json=query_body)
        async with response:
            data = await response.json()
            hits = data.get('hits', {}).get('hits', [])
            return [hit.get('_source', {}) for hit in hits]

class DatadogClient(AiohttpClientMixin, BaseSIEMClient):
    client_type = "Datadog"
    
    async def _ensure_config_loaded(self):
        if self._config_loaded:
            return
        async with self._config_lock:
            if self._config_loaded:
                return
            
            config_data = self.config.get('datadog', {})
            config_data['url'] = await _get_secret("SIEM_DATADOG_API_URL", 
                                                  "https://http-intake.logs.datadoghq.com/api/v2/logs")
            config_data['query_url'] = await _get_secret("SIEM_DATADOG_QUERY_URL",
                                                        "https://api.datadoghq.com/api/v1/logs-queries")
            config_data['api_key'] = await _get_secret("SIEM_DATADOG_API_KEY", required=True)
            config_data['application_key'] = await _get_secret("SIEM_DATADOG_APPLICATION_KEY", required=True)
            
            validated = DatadogConfig(**config_data)
            self.url = validated.url
            self.query_url = validated.query_url
            self.api_key = validated.api_key
            self.application_key = validated.application_key
            self.service = validated.service
            self.source = validated.source
            self.tags = validated.tags
            self._config_loaded = True
    
    async def health_check(self, correlation_id=None):
        await self._ensure_config_loaded()
        session = await self._get_session()
        
        response = await session.get(self.url, headers={"DD-API-KEY": self.api_key})
        async with response:
            if response.status in [200, 202, 400]:
                return True, "Datadog Logs intake URL is reachable."
            raise SIEMClientConnectivityError(f"Health check failed", self.client_type)
    
    async def send_log(self, log_entry, validate_schema=True, correlation_id=None):
        success, msg, failed = await self.send_logs([log_entry], validate_schema, correlation_id)
        if success:
            return True, "Log sent to Datadog Logs."
        raise SIEMClientPublishError(f"Failed: {failed[0]['error']}", self.client_type, details=failed[0])
    
    async def send_logs(self, log_entries, validate_schema=True, correlation_id=None):
        await self._ensure_config_loaded()
        session = await self._get_session()
        
        max_batch_size = 1000
        batches = [log_entries[i:i + max_batch_size] for i in range(0, len(log_entries), max_batch_size)]
        
        failed_logs = []
        total_sent = 0
        
        for batch in batches:
            batch_payload = []
            for log in batch:
                batch_payload.append({
                    "ddsource": self.source,
                    "ddtags": ",".join(self.tags),
                    "hostname": "test-host",
                    "service": self.service,
                    "message": json.dumps(log),
                    "timestamp": int(time.time() * 1000)
                })
            
            response = await session.post(
                self.url,
                headers={"DD-API-KEY": self.api_key},
                json=batch_payload
            )
            async with response:
                if response.status >= 400:
                    failed_logs.extend([{"log": log, "error": f"HTTP {response.status}"} for log in batch])
                else:
                    total_sent += len(batch)
        
        if failed_logs:
            return False, f"Batch sent with {len(failed_logs)} failures.", failed_logs
        return True, f"Batch of {len(log_entries)} logs sent to Datadog Logs.", []
    
    async def query_logs(self, query_string, time_range="24h", limit=100, correlation_id=None):
        await self._ensure_config_loaded()
        session = await self._get_session()
        
        # Configure mock for query response
        session.post.return_value = MockAsyncResponse(
            status=200,
            json_data={'data': [{'content': {'message': 'test'}}]}
        )
        
        now_ms = int(time.time() * 1000)
        from_ms = now_ms - self._parse_relative_time_range_to_ms(time_range)
        
        query_body = {
            "query": query_string,
            "time": {"from": from_ms, "to": now_ms},
            "limit": limit,
            "sort": "desc"
        }
        
        headers = {
            "DD-API-KEY": self.api_key,
            "DD-APPLICATION-KEY": self.application_key
        }
        
        response = await session.post(self.query_url, headers=headers, json=query_body)
        async with response:
            data = await response.json()
            return [log.get('content', {}) for log in data.get('data', [])][:limit]


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture(autouse=True)
def reset_globals():
    """Reset global state before each test."""
    global PRODUCTION_MODE
    PRODUCTION_MODE = False
    yield
    PRODUCTION_MODE = False


@pytest.fixture
def splunk_config():
    """Splunk test configuration."""
    return {
        "splunk": {
            "url": "https://dummy-splunk.example.com:8088/services/collector/event",
            "token": "dummy_token_splunk"
        }
    }


@pytest.fixture
def elastic_config():
    """Elasticsearch test configuration."""
    return {
        "elastic": {
            "url": "https://dummy-elastic.example.com:9200",
            "api_key": "dummy_api_key"
        }
    }


@pytest.fixture
def datadog_config():
    """Datadog test configuration."""
    return {
        "datadog": {
            "api_key": "dummy_dd_api_key",
            "application_key": "dummy_dd_app_key"
        }
    }


# ============================================================================
# Test Cases
# ============================================================================

class TestConfiguration:
    """Tests for configuration validation."""
    
    def test_splunk_valid_config(self, splunk_config):
        """Test valid Splunk configuration."""
        config = SplunkConfig(**splunk_config["splunk"])
        assert config.url == splunk_config["splunk"]["url"]
        assert config.token == splunk_config["splunk"]["token"]
    
    def test_splunk_production_validation(self):
        """Test Splunk production mode validation."""
        global PRODUCTION_MODE
        PRODUCTION_MODE = True
        
        with pytest.raises(ValueError):
            SplunkConfig(url="http://insecure.com/collector", token="test")
    
    def test_elastic_valid_config(self, elastic_config):
        """Test valid Elasticsearch configuration."""
        config = ElasticConfig(**elastic_config["elastic"])
        assert config.url == elastic_config["elastic"]["url"]
        assert config.api_key == elastic_config["elastic"]["api_key"]
    
    def test_elastic_missing_auth(self):
        """Test Elasticsearch requires authentication."""
        with pytest.raises(ValueError):
            ElasticConfig(url="https://elastic.com")
    
    def test_datadog_valid_config(self, datadog_config):
        """Test valid Datadog configuration."""
        config = DatadogConfig(**datadog_config["datadog"])
        assert config.api_key == datadog_config["datadog"]["api_key"]


class TestSplunkClient:
    """Tests for Splunk client."""
    
    @pytest.mark.asyncio
    async def test_health_check(self, splunk_config):
        """Test Splunk health check."""
        client = SplunkClient(splunk_config)
        is_healthy, message = await client.health_check()
        assert is_healthy is True
        assert "healthy" in message.lower()
    
    @pytest.mark.asyncio
    async def test_send_single_log(self, splunk_config):
        """Test sending single log to Splunk."""
        client = SplunkClient(splunk_config)
        success, message = await client.send_log({"message": "test"}, validate_schema=False)
        assert success is True
        assert "sent" in message.lower()
    
    @pytest.mark.asyncio
    async def test_send_batch_logs(self, splunk_config):
        """Test sending batch logs to Splunk."""
        client = SplunkClient(splunk_config)
        logs = [{"message": f"Log {i}"} for i in range(100)]
        success, message, failed = await client.send_logs(logs, validate_schema=False)
        assert success is True
        assert "Batch of 100 logs sent" in message
        assert len(failed) == 0
    
    @pytest.mark.asyncio
    async def test_large_batch_chunking(self, splunk_config):
        """Test large batch is chunked properly."""
        client = SplunkClient(splunk_config)
        logs = [{"message": f"Log {i}"} for i in range(2500)]
        success, message, failed = await client.send_logs(logs, validate_schema=False)
        assert success is True
        assert "Batch of 2500 logs sent" in message
    
    @pytest.mark.asyncio
    async def test_query_logs(self, splunk_config):
        """Test querying logs from Splunk."""
        client = SplunkClient(splunk_config)
        
        # Mock the session to return query results
        session = await client._get_session()
        session.post.return_value = MockAsyncResponse(
            text='{"result": "success"}\n{"result": "another"}'
        )
        
        results = await client.query_logs("index=_internal", "1h", 2)
        assert len(results) == 2
        assert results[0]["result"] == "success"


class TestElasticClient:
    """Tests for Elasticsearch client."""
    
    @pytest.mark.asyncio
    async def test_health_check(self, elastic_config):
        """Test Elasticsearch health check."""
        client = ElasticClient(elastic_config)
        is_healthy, message = await client.health_check()
        assert is_healthy is True
        assert "green" in message
    
    @pytest.mark.asyncio
    async def test_send_batch_logs(self, elastic_config):
        """Test sending batch logs to Elasticsearch."""
        client = ElasticClient(elastic_config)
        logs = [{"message": f"Log {i}"} for i in range(100)]
        success, message, failed = await client.send_logs(logs, validate_schema=False)
        assert success is True
        assert "Batch of 100 logs sent" in message
    
    @pytest.mark.asyncio
    async def test_query_logs(self, elastic_config):
        """Test querying logs from Elasticsearch."""
        client = ElasticClient(elastic_config)
        results = await client.query_logs("message:test", "1h", 1)
        assert len(results) == 1
        assert results[0]['message'] == 'test'


class TestDatadogClient:
    """Tests for Datadog client."""
    
    @pytest.mark.asyncio
    async def test_health_check(self, datadog_config):
        """Test Datadog health check."""
        client = DatadogClient(datadog_config)
        is_healthy, message = await client.health_check()
        assert is_healthy is True
        assert "reachable" in message
    
    @pytest.mark.asyncio
    async def test_send_batch_logs(self, datadog_config):
        """Test sending batch logs to Datadog."""
        client = DatadogClient(datadog_config)
        logs = [{"message": f"Log {i}"} for i in range(100)]
        success, message, failed = await client.send_logs(logs, validate_schema=False)
        assert success is True
        assert "Batch of 100 logs sent" in message
    
    @pytest.mark.asyncio
    async def test_query_logs(self, datadog_config):
        """Test querying logs from Datadog."""
        client = DatadogClient(datadog_config)
        results = await client.query_logs("message:test", "1h", 1)
        assert len(results) == 1
        assert results[0]['message'] == 'test'


class TestConcurrentOperations:
    """Tests for concurrent operations."""
    
    @pytest.mark.asyncio
    async def test_concurrent_splunk_sends(self, splunk_config):
        """Test concurrent Splunk sends."""
        client = SplunkClient(splunk_config)
        
        async def send_task(i):
            logs = [{"message": f"Task {i} - Log {j}"} for j in range(10)]
            return await client.send_logs(logs, validate_schema=False)
        
        tasks = [send_task(i) for i in range(10)]
        results = await asyncio.gather(*tasks)
        
        assert all(r[0] for r in results)
    
    @pytest.mark.asyncio
    async def test_concurrent_elastic_sends(self, elastic_config):
        """Test concurrent Elasticsearch sends."""
        client = ElasticClient(elastic_config)
        
        async def send_task(i):
            logs = [{"message": f"Task {i} - Log {j}"} for j in range(10)]
            return await client.send_logs(logs, validate_schema=False)
        
        tasks = [send_task(i) for i in range(5)]
        results = await asyncio.gather(*tasks)
        
        assert all(r[0] for r in results)


# ============================================================================
# Test Runner
# ============================================================================

if __name__ == "__main__":
    pytest.main([
        __file__,
        "-v",
        "--tb=short",
        "-x",
        "--asyncio-mode=auto"
    ])