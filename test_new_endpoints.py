#!/usr/bin/env python
"""
Test script to validate all new API endpoints are accessible.
"""

from server.main import app

def test_all_endpoints():
    """Test that all new endpoints are registered."""
    routes = [r for r in app.routes if hasattr(r, 'path') and hasattr(r, 'methods')]
    
    # Group routes by module
    generator_routes = [r for r in routes if '/generator' in r.path]
    omnicore_routes = [r for r in routes if '/omnicore' in r.path]
    sfe_routes = [r for r in routes if '/sfe' in r.path]
    
    print(f"✓ Total API routes: {len(routes)}")
    print(f"\n📊 Route Summary:")
    print(f"  - Generator routes: {len(generator_routes)}")
    print(f"  - OmniCore routes: {len(omnicore_routes)}")
    print(f"  - SFE routes: {len(sfe_routes)}")
    
    # Check for new Generator endpoints
    print(f"\n🔧 Generator Endpoints ({len(generator_routes)} total):")
    generator_expected = [
        'upload', 'status', 'logs', 'clarify', 'codegen', 'testgen',
        'deploy', 'docgen', 'critique', 'pipeline', 'llm', 'audit'
    ]
    
    for endpoint in generator_expected:
        found = any(endpoint in r.path for r in generator_routes)
        status = "✓" if found else "✗"
        print(f"  {status} {endpoint}")
    
    # Check for new OmniCore endpoints
    print(f"\n⚙️  OmniCore Endpoints ({len(omnicore_routes)} total):")
    omnicore_expected = [
        'plugins', 'metrics', 'audit', 'health', 'workflow',
        'message-bus', 'marketplace', 'database', 'circuit-breakers',
        'rate-limits', 'dead-letter-queue'
    ]
    
    for endpoint in omnicore_expected:
        found = any(endpoint in r.path for r in omnicore_routes)
        status = "✓" if found else "✗"
        print(f"  {status} {endpoint}")
    
    # Check for new SFE endpoints
    print(f"\n🤖 SFE Endpoints ({len(sfe_routes)} total):")
    sfe_expected = [
        'analyze', 'errors', 'propose-fix', 'apply', 'rollback',
        'arbiter', 'arena', 'bugs', 'codebase', 'knowledge-graph',
        'sandbox', 'compliance', 'dlt', 'siem', 'rl', 'imports'
    ]
    
    for endpoint in sfe_expected:
        found = any(endpoint in r.path for r in sfe_routes)
        status = "✓" if found else "✗"
        print(f"  {status} {endpoint}")
    
    # Print all routes for verification
    print(f"\n📋 All Generator Routes:")
    for r in generator_routes:
        methods = ', '.join(sorted(r.methods))
        print(f"  {methods:8} {r.path}")
    
    print(f"\n📋 All OmniCore Routes:")
    for r in omnicore_routes:
        methods = ', '.join(sorted(r.methods))
        print(f"  {methods:8} {r.path}")
    
    print(f"\n📋 All SFE Routes:")
    for r in sfe_routes:
        methods = ', '.join(sorted(r.methods))
        print(f"  {methods:8} {r.path}")
    
    print(f"\n✅ Endpoint validation complete!")
    print(f"Total routes implemented: {len(routes)}")

if __name__ == "__main__":
    test_all_endpoints()
