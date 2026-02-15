#!/usr/bin/env python3
"""
Validate critical secrets are configured before application startup.
Run this before starting the application to catch configuration errors early.

Usage:
    python scripts/validate_secrets.py

Exit Codes:
    0 - All secrets validated successfully
    1 - One or more secrets are invalid or missing
"""

import os
import sys
import base64


def validate_audit_crypto():
    """Validate audit crypto configuration"""
    print("Validating audit crypto configuration...")
    
    mode = os.getenv('AUDIT_CRYPTO_MODE', 'software')
    use_env_secrets = os.getenv('USE_ENV_SECRETS', '').lower() in ('true', '1', 'yes')
    
    if mode == 'software':
        key_b64 = os.getenv('AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64')
        if not key_b64:
            print("❌ ERROR: AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64 not set")
            print("\nGenerate with:")
            print('  python -c "import base64, os; print(base64.b64encode(os.urandom(32)).decode())"')
            print("\nFor Railway deployment:")
            print("  1. Set USE_ENV_SECRETS=true")
            print("  2. Set AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64 to the generated key")
            print("\nFor AWS KMS deployment:")
            print("  1. Generate the key as above")
            print("  2. Encrypt with KMS:")
            print("     aws kms encrypt --key-id YOUR_KMS_KEY_ID \\")
            print("       --plaintext fileb://<(echo -n 'YOUR_KEY' | base64 -d) \\")
            print("       --query CiphertextBlob --output text")
            print("  3. Set AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64 to the ciphertext")
            return False
        
        # Validate it's valid base64
        try:
            decoded = base64.b64decode(key_b64)
            print(f"✓ AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64 is valid base64 ({len(decoded)} bytes)")
        except Exception as e:
            print(f"❌ ERROR: AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64 is not valid base64: {e}")
            return False
        
        # Check if using environment secrets (recommended for Railway)
        if use_env_secrets:
            print("✓ USE_ENV_SECRETS=true (environment variable secret manager enabled)")
        else:
            print("⚠️  WARNING: USE_ENV_SECRETS not set to 'true'")
            print("   For Railway deployments, set USE_ENV_SECRETS=true")
    
    return True


def validate_audit_hmac():
    """Validate audit HMAC key configuration"""
    print("\nValidating audit HMAC key...")
    
    hmac_key = os.getenv('AGENTIC_AUDIT_HMAC_KEY')
    if not hmac_key:
        print("❌ ERROR: AGENTIC_AUDIT_HMAC_KEY not set")
        print("\nGenerate with:")
        print('  openssl rand -hex 32')
        print("\nThis key is required for audit log integrity (must be exactly 64 hex characters)")
        return False
    
    # Validate it's 64 hex characters
    if len(hmac_key) != 64:
        print(f"❌ ERROR: AGENTIC_AUDIT_HMAC_KEY must be exactly 64 characters (got {len(hmac_key)})")
        return False
    
    try:
        int(hmac_key, 16)
        print(f"✓ AGENTIC_AUDIT_HMAC_KEY is valid (64 hex characters)")
    except ValueError:
        print("❌ ERROR: AGENTIC_AUDIT_HMAC_KEY contains non-hexadecimal characters")
        return False
    
    return True


def validate_encryption_key():
    """Validate encryption key configuration"""
    print("\nValidating encryption key...")
    
    enc_key = os.getenv('ENCRYPTION_KEY')
    if not enc_key:
        print("⚠️  WARNING: ENCRYPTION_KEY not set")
        print("\nGenerate with:")
        print('  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"')
        print("\nThis is required for data encryption at rest")
        return False
    
    # Basic validation - Fernet keys are 44 characters (32 bytes base64url encoded)
    if len(enc_key) != 44:
        print(f"⚠️  WARNING: ENCRYPTION_KEY length is {len(enc_key)}, expected 44 for Fernet key")
        return False
    
    print("✓ ENCRYPTION_KEY is set")
    return True


def validate_jwt_secrets():
    """Validate JWT and secret keys"""
    print("\nValidating JWT and secret keys...")
    
    secret_key = os.getenv('SECRET_KEY')
    jwt_secret = os.getenv('JWT_SECRET_KEY')
    
    issues = []
    if not secret_key:
        issues.append("SECRET_KEY")
    else:
        print("✓ SECRET_KEY is set")
    
    if not jwt_secret:
        issues.append("JWT_SECRET_KEY")
    else:
        print("✓ JWT_SECRET_KEY is set")
    
    if issues:
        print(f"⚠️  WARNING: Missing keys: {', '.join(issues)}")
        print("\nGenerate with:")
        print('  python -c "import secrets; print(secrets.token_urlsafe(32))"')
        return False
    
    return True


def validate_llm_keys():
    """Validate LLM API keys"""
    print("\nValidating LLM API keys...")
    
    openai_key = os.getenv('OPENAI_API_KEY')
    
    if not openai_key:
        print("⚠️  WARNING: OPENAI_API_KEY not set")
        print("   This is required for LLM functionality")
        return False
    
    print("✓ OPENAI_API_KEY is set")
    return True


def main():
    """Main validation function"""
    print("=" * 70)
    print("SECRET VALIDATION CHECK")
    print("=" * 70)
    
    results = []
    
    # Run all validations
    results.append(("Audit Crypto", validate_audit_crypto()))
    results.append(("Audit HMAC", validate_audit_hmac()))
    results.append(("Encryption Key", validate_encryption_key()))
    results.append(("JWT Secrets", validate_jwt_secrets()))
    results.append(("LLM Keys", validate_llm_keys()))
    
    # Summary
    print("\n" + "=" * 70)
    print("VALIDATION SUMMARY")
    print("=" * 70)
    
    all_passed = True
    critical_failed = False
    
    for name, passed in results:
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{status}: {name}")
        if not passed:
            all_passed = False
            # Audit Crypto and Audit HMAC are critical
            if name in ("Audit Crypto", "Audit HMAC"):
                critical_failed = True
    
    print("=" * 70)
    
    if critical_failed:
        print("\n❌ CRITICAL: Application cannot start with missing critical secrets")
        print("   Fix the errors above before starting the application")
        sys.exit(1)
    elif not all_passed:
        print("\n⚠️  WARNING: Some optional secrets are missing")
        print("   Application may start but some features will be disabled")
        print("   Review warnings above")
        sys.exit(0)  # Exit with 0 for warnings only
    else:
        print("\n✓ All secrets validated successfully")
        print("   Application is ready to start")
        sys.exit(0)


if __name__ == '__main__':
    main()
