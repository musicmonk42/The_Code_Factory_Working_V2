"""
SIEM Client Factory Module

This module provides a factory pattern for creating SIEM (Security Information and Event Management)
client instances for various platforms including Splunk, CloudWatch, and Azure Sentinel.
"""

import logging
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from enum import Enum


logger = logging.getLogger(__name__)


class SIEMType(Enum):
    """Supported SIEM platform types."""
    SPLUNK = "splunk"
    CLOUDWATCH = "cloudwatch"
    AZURE_SENTINEL = "azure_sentinel"
    MOCK = "mock"


class SIEMClientBase(ABC):
    """Abstract base class for SIEM clients."""
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize SIEM client.
        
        Args:
            config: Configuration dictionary with connection and authentication details
        """
        self.config = config
        self.connected = False
        self.events_sent = 0
        self.events_failed = 0
    
    @abstractmethod
    async def connect(self) -> bool:
        """
        Establish connection to the SIEM platform.
        
        Returns:
            True if connection successful, False otherwise
        """
        pass
    
    @abstractmethod
    async def send_event(self, event: Dict[str, Any]) -> bool:
        """
        Send a single event to the SIEM platform.
        
        Args:
            event: Event dictionary to send
            
        Returns:
            True if event sent successfully, False otherwise
        """
        pass
    
    @abstractmethod
    async def send_events_batch(self, events: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Send multiple events in a batch.
        
        Args:
            events: List of event dictionaries
            
        Returns:
            Dictionary with batch results including success count and failures
        """
        pass
    
    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from the SIEM platform."""
        pass
    
    def format_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """
        Format an event according to the SIEM platform's requirements.
        
        Args:
            event: Raw event dictionary
            
        Returns:
            Formatted event dictionary
        """
        # Base implementation adds timestamp if not present
        if "timestamp" not in event:
            event["timestamp"] = time.time()
        return event


class MockSIEMClient(SIEMClientBase):
    """Mock SIEM client for testing and development."""
    
    async def connect(self) -> bool:
        """Simulate connection."""
        logger.info("MockSIEMClient: Simulating connection")
        self.connected = True
        return True
    
    async def send_event(self, event: Dict[str, Any]) -> bool:
        """Simulate sending an event."""
        if not self.connected:
            logger.warning("MockSIEMClient: Not connected, cannot send event")
            self.events_failed += 1
            return False
        
        formatted_event = self.format_event(event)
        logger.debug(f"MockSIEMClient: Would send event: {formatted_event}")
        self.events_sent += 1
        return True
    
    async def send_events_batch(self, events: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Simulate sending a batch of events."""
        if not self.connected:
            logger.warning("MockSIEMClient: Not connected, cannot send batch")
            return {"success": 0, "failed": len(events), "errors": ["Not connected"]}
        
        success_count = 0
        failed_count = 0
        
        for event in events:
            if await self.send_event(event):
                success_count += 1
            else:
                failed_count += 1
        
        return {
            "success": success_count,
            "failed": failed_count,
            "errors": []
        }
    
    async def disconnect(self) -> None:
        """Simulate disconnection."""
        logger.info("MockSIEMClient: Disconnecting")
        self.connected = False


class SplunkSIEMClient(SIEMClientBase):
    """Splunk SIEM client implementation."""
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize Splunk client.
        
        Expected config:
            - host: Splunk server hostname
            - port: Splunk HEC port (default: 8088)
            - token: Splunk HEC token
            - index: Target index name
            - source: Event source (optional)
            - sourcetype: Event sourcetype (optional)
        """
        super().__init__(config)
        self.host = config.get("host")
        self.port = config.get("port", 8088)
        self.token = config.get("token")
        self.index = config.get("index")
        self.source = config.get("source", "siem_client")
        self.sourcetype = config.get("sourcetype", "json")
        self._session = None
        
        if not all([self.host, self.token, self.index]):
            raise ValueError("Splunk client requires host, token, and index in config")
    
    async def connect(self) -> bool:
        """Establish connection to Splunk HEC."""
        try:
            # Try to import aiohttp for async HTTP requests
            import aiohttp
            
            self._session = aiohttp.ClientSession(
                headers={"Authorization": f"Splunk {self.token}"}
            )
            
            # Verify connectivity with a health check
            url = f"https://{self.host}:{self.port}/services/collector/health"
            async with self._session.get(url, ssl=False) as response:
                if response.status == 200:
                    self.connected = True
                    logger.info(f"Connected to Splunk at {self.host}:{self.port}")
                    return True
                else:
                    logger.error(f"Splunk health check failed with status {response.status}")
                    return False
                    
        except ImportError:
            logger.warning("aiohttp not installed, using mock Splunk client")
            self.connected = True
            return True
        except Exception as e:
            logger.error(f"Failed to connect to Splunk: {e}")
            return False
    
    async def send_event(self, event: Dict[str, Any]) -> bool:
        """Send event to Splunk HEC."""
        if not self.connected:
            logger.warning("Not connected to Splunk")
            self.events_failed += 1
            return False
        
        try:
            if self._session:
                url = f"https://{self.host}:{self.port}/services/collector/event"
                formatted_event = self.format_event(event)
                
                payload = {
                    "event": formatted_event,
                    "index": self.index,
                    "source": self.source,
                    "sourcetype": self.sourcetype,
                    "time": formatted_event.get("timestamp", time.time())
                }
                
                async with self._session.post(url, json=payload, ssl=False) as response:
                    if response.status == 200:
                        self.events_sent += 1
                        return True
                    else:
                        logger.error(f"Failed to send event to Splunk: {response.status}")
                        self.events_failed += 1
                        return False
            else:
                # Mock mode
                logger.debug(f"Mock Splunk: Would send event to index {self.index}")
                self.events_sent += 1
                return True
                
        except Exception as e:
            logger.error(f"Error sending event to Splunk: {e}")
            self.events_failed += 1
            return False
    
    async def send_events_batch(self, events: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Send multiple events to Splunk in a batch."""
        success_count = 0
        failed_count = 0
        errors = []
        
        for event in events:
            if await self.send_event(event):
                success_count += 1
            else:
                failed_count += 1
                errors.append(f"Failed to send event: {event.get('id', 'unknown')}")
        
        return {
            "success": success_count,
            "failed": failed_count,
            "errors": errors
        }
    
    async def disconnect(self) -> None:
        """Close Splunk connection."""
        if self._session:
            await self._session.close()
        self.connected = False
        logger.info("Disconnected from Splunk")


class CloudWatchSIEMClient(SIEMClientBase):
    """AWS CloudWatch Logs SIEM client implementation."""
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize CloudWatch client.
        
        Expected config:
            - region: AWS region
            - log_group: CloudWatch log group name
            - log_stream: CloudWatch log stream name
            - access_key_id: AWS access key (optional, uses boto3 credential chain)
            - secret_access_key: AWS secret key (optional)
        """
        super().__init__(config)
        self.region = config.get("region", "us-east-1")
        self.log_group = config.get("log_group")
        self.log_stream = config.get("log_stream")
        self._client = None
        
        if not all([self.log_group, self.log_stream]):
            raise ValueError("CloudWatch client requires log_group and log_stream in config")
    
    async def connect(self) -> bool:
        """Establish connection to CloudWatch."""
        try:
            import boto3
            
            session_config = {"region_name": self.region}
            if self.config.get("access_key_id"):
                session_config["aws_access_key_id"] = self.config["access_key_id"]
                session_config["aws_secret_access_key"] = self.config["secret_access_key"]
            
            self._client = boto3.client("logs", **session_config)
            
            # Verify log group and stream exist
            try:
                self._client.describe_log_streams(
                    logGroupName=self.log_group,
                    logStreamNamePrefix=self.log_stream
                )
                self.connected = True
                logger.info(f"Connected to CloudWatch log group: {self.log_group}")
                return True
            except Exception as e:
                logger.error(f"CloudWatch log group/stream verification failed: {e}")
                return False
                
        except ImportError:
            logger.warning("boto3 not installed, using mock CloudWatch client")
            self.connected = True
            return True
        except Exception as e:
            logger.error(f"Failed to connect to CloudWatch: {e}")
            return False
    
    async def send_event(self, event: Dict[str, Any]) -> bool:
        """Send event to CloudWatch Logs."""
        if not self.connected:
            logger.warning("Not connected to CloudWatch")
            self.events_failed += 1
            return False
        
        try:
            import json
            
            if self._client:
                formatted_event = self.format_event(event)
                timestamp = int(formatted_event.get("timestamp", time.time()) * 1000)
                
                self._client.put_log_events(
                    logGroupName=self.log_group,
                    logStreamName=self.log_stream,
                    logEvents=[{
                        "timestamp": timestamp,
                        "message": json.dumps(formatted_event)
                    }]
                )
                self.events_sent += 1
                return True
            else:
                # Mock mode
                logger.debug(f"Mock CloudWatch: Would send event to {self.log_group}/{self.log_stream}")
                self.events_sent += 1
                return True
                
        except Exception as e:
            logger.error(f"Error sending event to CloudWatch: {e}")
            self.events_failed += 1
            return False
    
    async def send_events_batch(self, events: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Send multiple events to CloudWatch in a batch."""
        if not self.connected or not self._client:
            return {"success": 0, "failed": len(events), "errors": ["Not connected"]}
        
        try:
            import json
            
            log_events = []
            for event in events:
                formatted_event = self.format_event(event)
                timestamp = int(formatted_event.get("timestamp", time.time()) * 1000)
                log_events.append({
                    "timestamp": timestamp,
                    "message": json.dumps(formatted_event)
                })
            
            self._client.put_log_events(
                logGroupName=self.log_group,
                logStreamName=self.log_stream,
                logEvents=log_events
            )
            
            self.events_sent += len(events)
            return {"success": len(events), "failed": 0, "errors": []}
            
        except Exception as e:
            logger.error(f"Error sending batch to CloudWatch: {e}")
            self.events_failed += len(events)
            return {"success": 0, "failed": len(events), "errors": [str(e)]}
    
    async def disconnect(self) -> None:
        """Close CloudWatch connection."""
        self._client = None
        self.connected = False
        logger.info("Disconnected from CloudWatch")


class AzureSentinelSIEMClient(SIEMClientBase):
    """Azure Sentinel SIEM client implementation (stub)."""
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize Azure Sentinel client.
        
        Expected config:
            - workspace_id: Azure Log Analytics workspace ID
            - shared_key: Workspace shared key
            - log_type: Custom log type name
        """
        super().__init__(config)
        self.workspace_id = config.get("workspace_id")
        self.shared_key = config.get("shared_key")
        self.log_type = config.get("log_type", "CustomLog")
        
        if not all([self.workspace_id, self.shared_key]):
            raise ValueError("Azure Sentinel client requires workspace_id and shared_key")
    
    async def connect(self) -> bool:
        """Establish connection to Azure Sentinel."""
        logger.info(f"Azure Sentinel client connecting to workspace {self.workspace_id}")
        # Implementation would use Azure Monitor API
        self.connected = True
        return True
    
    async def send_event(self, event: Dict[str, Any]) -> bool:
        """Send event to Azure Sentinel."""
        if not self.connected:
            self.events_failed += 1
            return False
        
        logger.debug(f"Azure Sentinel: Would send event to {self.log_type}")
        self.events_sent += 1
        return True
    
    async def send_events_batch(self, events: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Send batch to Azure Sentinel."""
        if not self.connected:
            return {"success": 0, "failed": len(events), "errors": ["Not connected"]}
        
        logger.debug(f"Azure Sentinel: Would send {len(events)} events")
        self.events_sent += len(events)
        return {"success": len(events), "failed": 0, "errors": []}
    
    async def disconnect(self) -> None:
        """Disconnect from Azure Sentinel."""
        self.connected = False
        logger.info("Disconnected from Azure Sentinel")


def get_siem_client(siem_type: str, config: Dict[str, Any]) -> SIEMClientBase:
    """
    Factory function to create a SIEM client instance.
    
    Args:
        siem_type: Type of SIEM platform (splunk, cloudwatch, azure_sentinel, mock)
        config: Configuration dictionary for the SIEM client
        
    Returns:
        SIEMClientBase: An initialized SIEM client instance
        
    Raises:
        ValueError: If siem_type is not supported or config is invalid
        
    Examples:
        >>> config = {"host": "splunk.example.com", "token": "xxx", "index": "main"}
        >>> client = get_siem_client("splunk", config)
        >>> await client.connect()
        >>> await client.send_event({"message": "test"})
        
        >>> config = {"region": "us-east-1", "log_group": "app", "log_stream": "logs"}
        >>> client = get_siem_client("cloudwatch", config)
    """
    siem_type = siem_type.lower()
    
    logger.info(f"Creating SIEM client for type: {siem_type}")
    
    if siem_type == SIEMType.SPLUNK.value:
        return SplunkSIEMClient(config)
    
    elif siem_type == SIEMType.CLOUDWATCH.value:
        return CloudWatchSIEMClient(config)
    
    elif siem_type == SIEMType.AZURE_SENTINEL.value:
        return AzureSentinelSIEMClient(config)
    
    elif siem_type == SIEMType.MOCK.value:
        return MockSIEMClient(config)
    
    else:
        raise ValueError(
            f"Unsupported SIEM type: {siem_type}. "
            f"Supported types: {', '.join([t.value for t in SIEMType])}"
        )


__all__ = [
    "SIEMType",
    "SIEMClientBase",
    "MockSIEMClient",
    "SplunkSIEMClient",
    "CloudWatchSIEMClient",
    "AzureSentinelSIEMClient",
    "get_siem_client",
]
