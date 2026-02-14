#!/usr/bin/env python3
"""
Configuration Verification Script

This script verifies that all three fixes for job vanishing are properly configured:
1. Kafka bridge enabled
2. Agent readiness gates in place
3. NLTK data accessible

Run this script after deployment to verify configuration.
"""

import os
import sys

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

def verify_kafka_config():
    """Verify Kafka configuration."""
    print("=" * 70)
    print("1. VERIFYING KAFKA CONFIGURATION")
    print("=" * 70)
    
    # Check environment variables
    enable_kafka = os.getenv("ENABLE_KAFKA", "0")
    kafka_enabled = os.getenv("KAFKA_ENABLED", "false")
    use_kafka_ingestion = os.getenv("USE_KAFKA_INGESTION", "false")
    use_kafka_audit = os.getenv("USE_KAFKA_AUDIT", "false")
    
    print(f"Environment Variables:")
    print(f"  ENABLE_KAFKA: {enable_kafka}")
    print(f"  KAFKA_ENABLED: {kafka_enabled}")
    print(f"  USE_KAFKA_INGESTION: {use_kafka_ingestion}")
    print(f"  USE_KAFKA_AUDIT: {use_kafka_audit}")
    
    # Try to load ArbiterConfig
    try:
        from self_fixing_engineer.arbiter.config import ArbiterConfig
        config = ArbiterConfig()
        print(f"\nArbiterConfig:")
        print(f"  KAFKA_ENABLED: {config.KAFKA_ENABLED} (type: {type(config.KAFKA_ENABLED).__name__})")
        print(f"  KAFKA_BOOTSTRAP_SERVERS: {config.KAFKA_BOOTSTRAP_SERVERS}")
        
        if config.KAFKA_ENABLED:
            print("  ✅ Kafka is ENABLED in ArbiterConfig")
        else:
            print("  ❌ Kafka is DISABLED in ArbiterConfig")
            print("     This will cause ShardedMessageBus to use local queue only!")
            return False
    except Exception as e:
        print(f"  ❌ Failed to load ArbiterConfig: {e}")
        return False
    
    # Try to check ShardedMessageBus
    try:
        from omnicore_engine.message_bus.sharded_message_bus import ShardedMessageBus
        print(f"\nShardedMessageBus:")
        print(f"  Module loaded successfully")
        # Note: We don't instantiate here to avoid startup side effects
    except Exception as e:
        print(f"  ❌ Failed to import ShardedMessageBus: {e}")
        return False
    
    print("\n✅ Kafka configuration looks correct!\n")
    return True


def verify_agent_readiness():
    """Verify agent readiness dependency is in place."""
    print("=" * 70)
    print("2. VERIFYING AGENT READINESS GATES")
    print("=" * 70)
    
    # Check that dependency exists
    try:
        from server.dependencies import require_agents_ready
        print(f"✅ require_agents_ready dependency exists")
    except ImportError as e:
        print(f"❌ Failed to import require_agents_ready: {e}")
        return False
    
    # Check that it's used in job routes
    try:
        with open('server/routers/jobs.py', 'r') as f:
            jobs_content = f.read()
            if 'require_agents_ready' in jobs_content and 'Depends(require_agents_ready)' in jobs_content:
                print(f"✅ require_agents_ready is used in server/routers/jobs.py")
            else:
                print(f"❌ require_agents_ready not found in server/routers/jobs.py")
                return False
    except Exception as e:
        print(f"❌ Failed to check jobs.py: {e}")
        return False
    
    # Check generator routes
    try:
        with open('server/routers/generator.py', 'r') as f:
            gen_content = f.read()
            if 'require_agents_ready' in gen_content and 'Depends(require_agents_ready)' in gen_content:
                print(f"✅ require_agents_ready is used in server/routers/generator.py")
            else:
                print(f"❌ require_agents_ready not found in server/routers/generator.py")
                return False
    except Exception as e:
        print(f"❌ Failed to check generator.py: {e}")
        return False
    
    print("\n✅ Agent readiness gates are in place!\n")
    return True


def verify_nltk_config():
    """Verify NLTK data configuration."""
    print("=" * 70)
    print("3. VERIFYING NLTK DATA CONFIGURATION")
    print("=" * 70)
    
    # Check environment variable
    nltk_data = os.getenv("NLTK_DATA")
    print(f"NLTK_DATA environment variable: {nltk_data or '(not set)'}")
    
    # Check if directory exists
    expected_path = "/opt/nltk_data"
    if os.path.exists(expected_path):
        print(f"✅ {expected_path} directory exists")
        
        # Check vader_lexicon specifically
        vader_path = os.path.join(expected_path, "sentiment", "vader_lexicon")
        if os.path.exists(vader_path):
            print(f"✅ vader_lexicon found at {vader_path}")
        else:
            print(f"⚠️  vader_lexicon not found at {vader_path}")
            print(f"   (This may be expected in development, should be present in Docker)")
    else:
        print(f"⚠️  {expected_path} directory does not exist")
        print(f"   (This is expected in development, should exist in Docker container)")
    
    # Check Dockerfile
    try:
        with open('Dockerfile', 'r') as f:
            dockerfile_content = f.read()
            if 'NLTK_DATA="/opt/nltk_data"' in dockerfile_content or 'NLTK_DATA=/opt/nltk_data' in dockerfile_content:
                print(f"✅ NLTK_DATA is set in Dockerfile")
            else:
                print(f"❌ NLTK_DATA not found in Dockerfile")
                return False
            
            if 'COPY --from=builder --chown=appuser:appgroup /opt/nltk_data /opt/nltk_data' in dockerfile_content:
                print(f"✅ NLTK data is copied with correct ownership in Dockerfile")
            else:
                print(f"❌ NLTK data copy not found in Dockerfile")
                return False
    except Exception as e:
        print(f"❌ Failed to check Dockerfile: {e}")
        return False
    
    print("\n✅ NLTK data configuration looks correct!\n")
    return True


def main():
    """Main verification routine."""
    print("\n" + "=" * 70)
    print("CODE FACTORY CONFIGURATION VERIFICATION")
    print("Checking fixes for job vanishing issues")
    print("=" * 70 + "\n")
    
    results = []
    results.append(("Kafka Configuration", verify_kafka_config()))
    results.append(("Agent Readiness Gates", verify_agent_readiness()))
    results.append(("NLTK Data Configuration", verify_nltk_config()))
    
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    
    all_passed = True
    for name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status}: {name}")
        if not passed:
            all_passed = False
    
    print("=" * 70)
    
    if all_passed:
        print("\n🎉 All configuration checks passed!")
        print("The fixes for job vanishing should be working correctly.\n")
        return 0
    else:
        print("\n⚠️  Some configuration checks failed!")
        print("Jobs may still vanish until these issues are resolved.\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
