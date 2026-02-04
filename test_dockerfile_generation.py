#!/usr/bin/env python3
"""
Test script to verify Dockerfile generation in the pipeline.

This script tests the deployment agent directly to ensure it can generate Dockerfiles.
"""

import asyncio
import logging
import sys
from pathlib import Path

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

async def test_deploy_agent():
    """Test deploy agent can generate Dockerfile."""
    try:
        # Import the deploy agent
        logger.info("=" * 80)
        logger.info("TESTING DOCKERFILE GENERATION")
        logger.info("=" * 80)
        
        from generator.agents.deploy_agent.deploy_agent import DeployAgent
        
        # Create temp directory for test
        test_dir = Path("/tmp/test_deploy_output")
        test_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Test directory: {test_dir}")
        
        # Initialize deploy agent
        logger.info("Initializing DeployAgent...")
        agent = DeployAgent(repo_path=str(test_dir))
        await agent._init_db()
        logger.info("✓ DeployAgent initialized")
        
        # Check plugin registry
        logger.info(f"Available plugins: {list(agent.plugin_registry.plugins.keys())}")
        
        # Test Docker plugin specifically
        docker_plugin = agent.plugin_registry.get_plugin("docker")
        if docker_plugin:
            logger.info(f"✓ Docker plugin found: {docker_plugin.name}")
        else:
            logger.error("✗ Docker plugin NOT found!")
            return False
        
        # Run deployment generation
        logger.info("Running deployment generation for 'docker' target...")
        requirements = {
            "pipeline_steps": ["generate", "validate"],
            "platform": "docker",
            "include_ci_cd": True,
        }
        
        result = await agent.run_deployment(target="docker", requirements=requirements)
        
        # Check result
        logger.info(f"Result keys: {list(result.keys())}")
        logger.info(f"Run ID: {result.get('run_id')}")
        logger.info(f"Target: {result.get('target')}")
        
        configs = result.get("configs", {})
        logger.info(f"Configs generated: {list(configs.keys())}")
        
        if "docker" in configs:
            docker_config = configs["docker"]
            logger.info(f"✓ Docker config generated ({len(docker_config)} chars)")
            logger.info("=" * 80)
            logger.info("DOCKERFILE CONTENT (first 500 chars):")
            logger.info("=" * 80)
            logger.info(docker_config[:500])
            logger.info("=" * 80)
            
            # Write to file
            dockerfile_path = test_dir / "Dockerfile"
            dockerfile_path.write_text(docker_config)
            logger.info(f"✓ Dockerfile written to: {dockerfile_path}")
            
            return True
        else:
            logger.error("✗ No docker config in result!")
            return False
            
    except Exception as e:
        logger.error(f"✗ Test failed: {e}", exc_info=True)
        return False

async def main():
    """Run the test."""
    success = await test_deploy_agent()
    
    if success:
        logger.info("=" * 80)
        logger.info("✓ TEST PASSED: Dockerfile generation works!")
        logger.info("=" * 80)
        sys.exit(0)
    else:
        logger.error("=" * 80)
        logger.error("✗ TEST FAILED: Dockerfile generation did not work!")
        logger.error("=" * 80)
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
