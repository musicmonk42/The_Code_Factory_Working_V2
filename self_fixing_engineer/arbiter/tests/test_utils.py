import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import asyncio
import random
import psutil
import aiohttp
from tenacity import RetryError
from arbiter.utils import random_chance, get_system_metrics, get_system_metrics_async, check_service_health

@pytest.mark.parametrize("prob, mock_value, expected", [
    (0.0, 0.5, False),
    (1.0, 0.5, True),
])
def test_random_chance_deterministic(prob, mock_value, expected):
    with patch('random.random') as mock_random:
        mock_random.return_value = mock_value
        assert random_chance(prob) == expected

def test_random_chance_statistical():
    true_count = 0
    for _ in range(1000):
        if random_chance(0.5):
            true_count += 1
    # Wider bounds to prevent flaky failures
    assert 400 < true_count < 600  # Wider bounds for reliability

@pytest.mark.parametrize("invalid_prob", [-0.1, 1.1, 2.0, -1.0])
def test_random_chance_invalid(invalid_prob):
    with pytest.raises(ValueError, match="Probability must be between 0.0 and 1.0"):
        random_chance(invalid_prob)

def test_get_system_metrics_normal():
    metrics = get_system_metrics()
    assert isinstance(metrics, dict)
    assert 'cpu_percent' in metrics
    assert isinstance(metrics['cpu_percent'], float)
    assert 0 <= metrics['cpu_percent'] <= 100
    assert 'memory_percent' in metrics
    assert isinstance(metrics['memory_percent'], float)
    assert 0 <= metrics['memory_percent'] <= 100
    assert 'disk_usage_percent' in metrics
    assert isinstance(metrics['disk_usage_percent'], float)
    assert 0 <= metrics['disk_usage_percent'] <= 100

@patch('psutil.cpu_percent', side_effect=Exception("Mock psutil error"))
@patch('psutil.virtual_memory')
@patch('psutil.disk_usage')
def test_get_system_metrics_error(mock_disk, mock_mem, mock_cpu):
    metrics = get_system_metrics()
    assert isinstance(metrics, dict)
    assert 'error' in metrics
    assert "Failed to collect system metrics" in metrics['error']
    assert "Mock psutil error" in metrics['error']

@pytest.mark.asyncio
async def test_get_system_metrics_async_normal():
    metrics = await get_system_metrics_async()
    assert isinstance(metrics, dict)
    assert 'cpu_percent' in metrics
    assert isinstance(metrics['cpu_percent'], float)
    assert 0 <= metrics['cpu_percent'] <= 100
    assert 'memory_percent' in metrics
    assert isinstance(metrics['memory_percent'], float)
    assert 0 <= metrics['memory_percent'] <= 100
    assert 'disk_usage_percent' in metrics
    assert isinstance(metrics['disk_usage_percent'], float)
    assert 0 <= metrics['disk_usage_percent'] <= 100

@pytest.mark.asyncio
@patch('asyncio.to_thread', side_effect=Exception("Mock async error"))
async def test_get_system_metrics_async_error(mock_to_thread):
    metrics = await get_system_metrics_async()
    assert isinstance(metrics, dict)
    assert 'error' in metrics
    assert "Failed to collect metrics asynchronously" in metrics['error']
    assert "Mock async error" in metrics['error']

@pytest.mark.asyncio
@patch('arbiter.utils.get_health_session')
async def test_check_service_health_success(mock_get_session):
    # Create mock response
    mock_response = AsyncMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = AsyncMock(return_value={'status': 'ok'})
    
    # Create mock session with proper async context manager
    mock_session = MagicMock()
    mock_session.get.return_value.__aenter__ = AsyncMock(return_value=mock_response)
    mock_session.get.return_value.__aexit__ = AsyncMock(return_value=None)
    
    mock_get_session.return_value = mock_session
    
    result = await check_service_health("http://test.com/health")
    assert result == {'status': 'ok'}

@pytest.mark.asyncio
@patch('arbiter.utils.get_health_session')
async def test_check_service_health_non_json(mock_get_session):
    # Create mock response
    mock_response = AsyncMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = AsyncMock(side_effect=aiohttp.ContentTypeError(MagicMock(), MagicMock()))
    mock_response.text = AsyncMock(return_value='Plain text')
    
    # Create mock session with proper async context manager
    mock_session = MagicMock()
    mock_session.get.return_value.__aenter__ = AsyncMock(return_value=mock_response)
    mock_session.get.return_value.__aexit__ = AsyncMock(return_value=None)
    
    mock_get_session.return_value = mock_session
    
    result = await check_service_health("http://test.com/health")
    assert 'error' in result
    assert "Non-JSON response" in result['error']
    assert "Plain text" in result['error']

@pytest.mark.asyncio
@patch('arbiter.utils.get_health_session')
async def test_check_service_health_client_error(mock_get_session):
    # Create mock response that raises on raise_for_status
    mock_response = AsyncMock()
    mock_response.raise_for_status = MagicMock(
        side_effect=aiohttp.ClientResponseError(
            MagicMock(), MagicMock(), status=500, message="Server Error"
        )
    )
    
    # Create mock session with proper async context manager
    mock_session = MagicMock()
    mock_session.get.return_value.__aenter__ = AsyncMock(return_value=mock_response)
    mock_session.get.return_value.__aexit__ = AsyncMock(return_value=None)
    
    mock_get_session.return_value = mock_session
    
    # The function retries 3 times for ClientError, then raises RetryError
    with pytest.raises(RetryError):
        await check_service_health("http://test.com/health")

@pytest.mark.asyncio
@patch('arbiter.utils.get_health_session')
async def test_check_service_health_timeout(mock_get_session):
    # Create mock session that raises on entering context
    mock_session = MagicMock()
    mock_session.get.return_value.__aenter__ = AsyncMock(
        side_effect=aiohttp.ServerTimeoutError("Timeout occurred")
    )
    
    mock_get_session.return_value = mock_session
    
    # ServerTimeoutError is a ClientError, so it retries 3 times then raises RetryError
    with pytest.raises(RetryError):
        await check_service_health("http://test.com/health")

@pytest.mark.asyncio
@patch('arbiter.utils.get_health_session')
async def test_check_service_health_unexpected_error(mock_get_session):
    # Create mock session that raises a generic exception
    mock_session = MagicMock()
    mock_session.get.return_value.__aenter__ = AsyncMock(
        side_effect=Exception("Unexpected error")
    )
    
    mock_get_session.return_value = mock_session
    
    # Generic exceptions are not retried, they're raised immediately
    with pytest.raises(Exception, match="Unexpected error"):
        await check_service_health("http://test.com/health")

@pytest.mark.asyncio
@patch('arbiter.utils.get_health_session')
async def test_check_service_health_invalid_url(mock_get_session):
    # Create mock session that raises InvalidURL
    mock_session = MagicMock()
    mock_session.get.return_value.__aenter__ = AsyncMock(
        side_effect=aiohttp.InvalidURL("http://invalid_url")
    )
    
    mock_get_session.return_value = mock_session
    
    # InvalidURL is a ClientError, so it retries 3 times then raises RetryError
    with pytest.raises(RetryError):
        await check_service_health("http://invalid_url")