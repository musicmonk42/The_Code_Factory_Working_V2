"""
Job Dispatch Service

Handles dispatching job completion events to downstream systems with fallback strategies.
Implements circuit breaker pattern for Kafka operations and provides HTTP webhook fallback.

Dispatch Priority:
1. Kafka (if enabled and available)
2. HTTP Webhook (if configured)
3. Database queue (last resort for polling)
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Circuit breaker state
_kafka_available = None
_kafka_last_check = None
_kafka_consecutive_failures = 0
KAFKA_CIRCUIT_BREAKER_THRESHOLD = 3
KAFKA_HEALTH_CHECK_INTERVAL = 60  # seconds


def kafka_available() -> bool:
    """
    Check if Kafka is available using circuit breaker pattern.
    
    Returns:
        True if Kafka should be attempted, False if circuit is open
    """
    global _kafka_available, _kafka_last_check, _kafka_consecutive_failures
    
    # Check if Kafka is enabled
    kafka_enabled = os.getenv("KAFKA_ENABLED", "false").lower() in ("true", "1", "yes")
    if not kafka_enabled:
        return False
    
    # If we've never checked, assume available
    if _kafka_available is None:
        return True
    
    # If circuit breaker is open (too many failures), check if enough time has passed
    if not _kafka_available and _kafka_last_check:
        time_since_check = (datetime.now(timezone.utc) - _kafka_last_check).total_seconds()
        if time_since_check < KAFKA_HEALTH_CHECK_INTERVAL:
            # Circuit still open
            return False
        else:
            # Try again after cooldown
            logger.info("Kafka circuit breaker cooldown expired, allowing retry")
            _kafka_available = True
            _kafka_consecutive_failures = 0
    
    return _kafka_available if _kafka_available is not None else True


def mark_kafka_failure():
    """Record a Kafka failure and potentially open circuit breaker."""
    global _kafka_available, _kafka_last_check, _kafka_consecutive_failures
    
    _kafka_consecutive_failures += 1
    _kafka_last_check = datetime.now(timezone.utc)
    
    if _kafka_consecutive_failures >= KAFKA_CIRCUIT_BREAKER_THRESHOLD:
        _kafka_available = False
        logger.warning(
            f"Kafka circuit breaker OPENED after {_kafka_consecutive_failures} consecutive failures. "
            f"Will retry after {KAFKA_HEALTH_CHECK_INTERVAL}s cooldown."
        )
    else:
        logger.warning(
            f"Kafka failure recorded ({_kafka_consecutive_failures}/{KAFKA_CIRCUIT_BREAKER_THRESHOLD})"
        )


def mark_kafka_success():
    """Record a Kafka success and close circuit breaker."""
    global _kafka_available, _kafka_consecutive_failures
    
    if not _kafka_available or _kafka_consecutive_failures > 0:
        logger.info("Kafka circuit breaker CLOSED - connection restored")
    
    _kafka_available = True
    _kafka_consecutive_failures = 0


async def dispatch_job_completion(job_id: str, job_data: Dict[str, Any]) -> bool:
    """
    Notify downstream systems about job completion with fallback strategies.
    
    Dispatch order:
    1. Kafka (if enabled and available)
    2. HTTP webhook (if SFE_WEBHOOK_URL configured)
    3. Database queue (last resort - not implemented yet)
    
    Args:
        job_id: Unique job identifier
        job_data: Job information to dispatch (status, output_url, etc.)
        
    Returns:
        True if dispatch succeeded via any method, False otherwise
        
    Industry Standards:
    - Circuit breaker pattern for fault tolerance
    - Graceful degradation with fallbacks
    - Clear error logging for troubleshooting
    """
    event = {
        "job_id": job_id,
        "status": str(job_data.get("status", "unknown")),
        "output_files": job_data.get("output_files", []),
        "total_files": len(job_data.get("output_files", [])),
        "completed_at": job_data.get("completed_at", datetime.now(timezone.utc).isoformat()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    
    logger.info(f"Dispatching completion event for job {job_id}")
    
    # Method 1: Kafka (primary)
    if kafka_available():
        try:
            success = await _dispatch_via_kafka(event)
            if success:
                mark_kafka_success()
                logger.info(f"✓ Dispatched job {job_id} completion via Kafka")
                return True
            else:
                mark_kafka_failure()
        except Exception as e:
            mark_kafka_failure()
            logger.warning(f"Kafka dispatch failed for job {job_id}: {e}, trying fallback")
    
    # Method 2: HTTP Webhook (fallback)
    webhook_url = os.getenv("SFE_WEBHOOK_URL")
    if webhook_url:
        try:
            success = await _dispatch_via_webhook(webhook_url, event)
            if success:
                logger.info(f"✓ Dispatched job {job_id} completion via HTTP webhook")
                return True
        except Exception as e:
            logger.warning(f"Webhook dispatch failed for job {job_id}: {e}")
    
    # Method 3: Database queue (last resort - not yet implemented)
    # In production, you would store events in a database table for polling
    logger.warning(
        f"All dispatch methods failed for job {job_id}. "
        f"Event will not be delivered to downstream systems. "
        f"Consider implementing database queue fallback."
    )
    
    return False


async def _dispatch_via_kafka(event: Dict[str, Any]) -> bool:
    """
    Dispatch event via Kafka.
    
    Args:
        event: Event data to send
        
    Returns:
        True if successful, False otherwise
    """
    try:
        # Check if kafka-python is available
        try:
            from kafka import KafkaProducer
        except ImportError:
            logger.warning("kafka-python not installed, skipping Kafka dispatch")
            return False
        
        bootstrap_servers = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
        topic = os.getenv("KAFKA_TOPIC", "job-completed")
        
        # Create producer with short timeout
        producer = KafkaProducer(
            bootstrap_servers=bootstrap_servers.split(","),
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            request_timeout_ms=5000,
            api_version_auto_timeout_ms=3000,
            retries=1,
        )
        
        # Send event
        future = producer.send(topic, value=event)
        future.get(timeout=5)  # Wait for send to complete
        
        producer.flush(timeout=5)
        producer.close()
        
        logger.debug(f"Kafka message sent to topic {topic}")
        return True
        
    except Exception as e:
        logger.warning(f"Kafka dispatch error: {e}")
        return False


async def _dispatch_via_webhook(url: str, event: Dict[str, Any]) -> bool:
    """
    Dispatch event via HTTP webhook.
    
    Args:
        url: Webhook URL
        event: Event data to send
        
    Returns:
        True if successful, False otherwise
    """
    try:
        import aiohttp
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json=event,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status in (200, 201, 202):
                    logger.debug(f"Webhook dispatch successful: {response.status}")
                    return True
                else:
                    logger.warning(
                        f"Webhook returned non-success status: {response.status}"
                    )
                    return False
                    
    except Exception as e:
        logger.warning(f"Webhook dispatch error: {e}")
        return False


def get_kafka_health_status() -> Dict[str, Any]:
    """
    Get current Kafka health status for monitoring.
    
    Returns:
        Dictionary with Kafka health information
    """
    kafka_enabled = os.getenv("KAFKA_ENABLED", "false").lower() in ("true", "1", "yes")
    
    if not kafka_enabled:
        return {
            "enabled": False,
            "status": "disabled",
            "message": "Kafka is not enabled (KAFKA_ENABLED=false)",
        }
    
    status = {
        "enabled": True,
        "bootstrap_servers": os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092"),
        "circuit_breaker_open": not kafka_available(),
        "consecutive_failures": _kafka_consecutive_failures,
        "last_check": _kafka_last_check.isoformat() if _kafka_last_check else None,
    }
    
    if kafka_available():
        status["status"] = "available"
        status["message"] = "Kafka is available for dispatch"
    else:
        status["status"] = "unavailable"
        status["message"] = f"Circuit breaker open after {_kafka_consecutive_failures} failures"
    
    return status
