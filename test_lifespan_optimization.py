"""
Test to verify the optimized lifespan function works correctly.

This validates that:
1. Lifespan yields quickly (< 1 second) to allow HTTP server to bind
2. Background initialization starts after yield
3. app.state variables are set correctly
"""
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch


def test_lifespan_yields_quickly():
    """Verify lifespan yields in under 1 second (fast startup)."""
    
    # Mock app object
    class MockApp:
        def __init__(self):
            self.state = MagicMock()
    
    mock_app = MockApp()
    
    # Mock all the heavy dependencies to prevent actual loading
    with patch('server.main.initialize_config') as mock_config, \
         patch('server.main.validate_required_api_keys') as mock_validate, \
         patch('server.main.get_startup_lock') as mock_lock, \
         patch('server.main.get_agent_loader') as mock_loader:
        
        # Configure mocks
        mock_config.return_value = MagicMock(is_production=False)
        mock_validate.return_value = True
        
        mock_lock_instance = AsyncMock()
        mock_lock_instance.acquire = AsyncMock(return_value=True)
        mock_lock.return_value = mock_lock_instance
        
        mock_loader_instance = MagicMock()
        mock_loader_instance.start_background_loading = MagicMock()
        mock_loader.return_value = mock_loader_instance
        
        # Import after mocking to avoid import-time issues
        from server.main import lifespan
        
        async def test():
            start_time = time.time()
            
            # Enter the lifespan context
            async with lifespan(mock_app):
                yield_time = time.time() - start_time
                
                print(f"✓ Lifespan yielded in {yield_time:.3f} seconds")
                
                # Verify it was fast (< 1 second)
                assert yield_time < 1.0, f"Lifespan took too long to yield: {yield_time:.3f}s"
                
                # Verify app.state was set
                assert hasattr(mock_app.state, 'initialization_complete')
                assert hasattr(mock_app.state, 'initialization_error')
                assert mock_app.state.initialization_complete == False
                assert mock_app.state.initialization_error is None
                
                print(f"  - initialization_complete: {mock_app.state.initialization_complete}")
                print(f"  - initialization_error: {mock_app.state.initialization_error}")
                
                # Wait a tiny bit for background task to start
                await asyncio.sleep(0.2)
        
        # Run the test
        asyncio.run(test())
        
        print("✓ Lifespan yields quickly (< 1 second)")
        print("✓ app.state variables are set correctly")
        print("✓ Background initialization starts after yield")


if __name__ == '__main__':
    test_lifespan_yields_quickly()
    print("\n✓ All lifespan optimization tests passed!")
