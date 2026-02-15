#!/usr/bin/env python3
# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Audit Crypto Key Rotation Script

This script generates a new master encryption key for audit crypto operations.
It supports both Railway/PaaS deployments (plaintext base64 keys) and AWS KMS
deployments (KMS-encrypted keys).

Usage:
    # For Railway/PaaS deployment (plaintext key):
    python scripts/rotate_audit_crypto_key.py --mode railway

    # For AWS KMS deployment:
    python scripts/rotate_audit_crypto_key.py --mode kms --key-id alias/your-key

    # With validation:
    python scripts/rotate_audit_crypto_key.py --mode railway --validate

Security Notes:
    - Railway/PaaS: Railway encrypts environment variables at the platform level
    - AWS KMS: Keys are encrypted with KMS and decrypted at runtime
    - Always backup your current key before rotating
    - Changing the key will invalidate existing encrypted audit data
"""

import argparse
import base64
import os
import sys
from typing import Optional


def generate_master_key(length: int = 32) -> bytes:
    """
    Generate a cryptographically secure random key.
    
    Args:
        length: Key length in bytes (default: 32 bytes for Fernet/AES-256)
        
    Returns:
        Random bytes suitable for encryption
    """
    return os.urandom(length)


def encode_key_base64(key: bytes) -> str:
    """
    Encode key as base64 string.
    
    Args:
        key: Raw key bytes
        
    Returns:
        Base64-encoded string (URL-safe)
    """
    return base64.b64encode(key).decode('ascii')


def encrypt_with_kms(plaintext: bytes, key_id: str, region: Optional[str] = None) -> str:
    """
    Encrypt key using AWS KMS.
    
    Args:
        plaintext: Raw key bytes to encrypt
        key_id: KMS key ID or alias (e.g., 'alias/audit-crypto-key')
        region: AWS region (defaults to AWS_REGION env var or us-east-1)
        
    Returns:
        Base64-encoded ciphertext blob
        
    Raises:
        RuntimeError: If boto3 is not installed or KMS operation fails
    """
    try:
        import boto3
        from botocore.exceptions import ClientError
    except ImportError:
        raise RuntimeError(
            "boto3 is required for KMS encryption. Install it with: pip install boto3"
        )
    
    # Use provided region or environment variable or default
    aws_region = region or os.getenv('AWS_REGION', 'us-east-1')
    
    try:
        kms = boto3.client('kms', region_name=aws_region)
        response = kms.encrypt(
            KeyId=key_id,
            Plaintext=plaintext
        )
        ciphertext_blob = response['CiphertextBlob']
        return base64.b64encode(ciphertext_blob).decode('ascii')
    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', 'Unknown')
        error_msg = e.response.get('Error', {}).get('Message', str(e))
        raise RuntimeError(
            f"KMS encryption failed ({error_code}): {error_msg}\n"
            f"Make sure:\n"
            f"1. AWS credentials are configured (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY)\n"
            f"2. KMS key '{key_id}' exists in region '{aws_region}'\n"
            f"3. You have 'kms:Encrypt' permission for this key"
        )


def validate_key(key_b64: str) -> bool:
    """
    Validate that a base64-encoded key is valid.
    
    Args:
        key_b64: Base64-encoded key string
        
    Returns:
        True if valid, False otherwise
    """
    try:
        decoded = base64.b64decode(key_b64)
        if len(decoded) < 32:
            print(f"❌ Key is too short: {len(decoded)} bytes (need at least 32 bytes)")
            return False
        print(f"✓ Key is valid: {len(decoded)} bytes")
        return True
    except Exception as e:
        print(f"❌ Key validation failed: {e}")
        return False


def railway_mode(validate: bool = False) -> None:
    """
    Generate a plaintext base64 key for Railway/PaaS deployment.
    
    Args:
        validate: Whether to validate the generated key
    """
    print("=" * 70)
    print("RAILWAY/PaaS MODE: Generating Plaintext Base64 Key")
    print("=" * 70)
    print()
    
    # Generate new key
    master_key = generate_master_key(32)
    master_key_b64 = encode_key_base64(master_key)
    
    # Validate if requested
    if validate:
        print("Validating generated key...")
        if not validate_key(master_key_b64):
            sys.exit(1)
        print()
    
    # Display instructions
    print("✓ New master key generated successfully!")
    print()
    print("KEY (copy this to your Railway environment variables):")
    print("-" * 70)
    print(master_key_b64)
    print("-" * 70)
    print()
    print("DEPLOYMENT STEPS:")
    print("1. Go to Railway dashboard → Your Project → Variables")
    print("2. Update or add this variable:")
    print("   Variable Name: AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64")
    print("   Value: (paste the key above)")
    print()
    print("3. Ensure these variables are also set:")
    print("   USE_ENV_SECRETS=true")
    print("   AUDIT_CRYPTO_MODE=software")
    print()
    print("4. Redeploy your application")
    print()
    print("⚠️  IMPORTANT:")
    print("   - Save your OLD key before replacing it (for rollback)")
    print("   - Changing the key will invalidate existing encrypted audit data")
    print("   - Railway encrypts all environment variables at the platform level")
    print()
    print("ROLLBACK:")
    print("   If something goes wrong, restore the old key value in Railway")
    print("   and redeploy.")
    print()


def kms_mode(key_id: str, region: Optional[str] = None, validate: bool = False) -> None:
    """
    Generate a KMS-encrypted key for AWS deployment.
    
    Args:
        key_id: KMS key ID or alias
        region: AWS region (optional)
        validate: Whether to validate the generated key
    """
    print("=" * 70)
    print("AWS KMS MODE: Generating KMS-Encrypted Key")
    print("=" * 70)
    print()
    
    # Generate new key
    master_key = generate_master_key(32)
    
    print(f"Encrypting with KMS key: {key_id}")
    if region:
        print(f"Region: {region}")
    else:
        aws_region = os.getenv('AWS_REGION', 'us-east-1')
        print(f"Region: {aws_region} (from AWS_REGION env var or default)")
    print()
    
    try:
        encrypted_key_b64 = encrypt_with_kms(master_key, key_id, region)
    except RuntimeError as e:
        print(f"❌ Error: {e}")
        sys.exit(1)
    
    # Validate if requested
    if validate:
        print("Validating encrypted key...")
        if not validate_key(encrypted_key_b64):
            sys.exit(1)
        print()
    
    # Display instructions
    print("✓ New master key encrypted successfully!")
    print()
    print("ENCRYPTED KEY (copy this to your environment variables):")
    print("-" * 70)
    print(encrypted_key_b64)
    print("-" * 70)
    print()
    print("DEPLOYMENT STEPS:")
    print("1. Update your environment variable:")
    print("   Variable Name: AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64")
    print("   Value: (paste the encrypted key above)")
    print()
    print("2. Ensure these variables are set:")
    print(f"   AWS_REGION={region or os.getenv('AWS_REGION', 'us-east-1')}")
    print(f"   AUDIT_CRYPTO_KMS_KEY_ID={key_id}")
    print("   AUDIT_CRYPTO_MODE=software")
    print("   USE_ENV_SECRETS=false  (or not set)")
    print()
    print("3. Ensure your application has AWS credentials with 'kms:Decrypt' permission")
    print()
    print("4. Redeploy your application")
    print()
    print("⚠️  IMPORTANT:")
    print("   - Save your OLD key before replacing it (for rollback)")
    print("   - Changing the key will invalidate existing encrypted audit data")
    print("   - The key is encrypted with KMS and will be decrypted at runtime")
    print()
    print("ROLLBACK:")
    print("   If something goes wrong, restore the old key value and redeploy.")
    print()


def main():
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(
        description="Generate and rotate audit crypto master encryption key",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Generate a plaintext key for Railway/PaaS:
  python scripts/rotate_audit_crypto_key.py --mode railway

  # Generate a KMS-encrypted key for AWS:
  python scripts/rotate_audit_crypto_key.py --mode kms --key-id alias/audit-crypto

  # With validation:
  python scripts/rotate_audit_crypto_key.py --mode railway --validate

For more information, see docs/RAILWAY_DEPLOYMENT.md
        """
    )
    
    parser.add_argument(
        '--mode',
        choices=['railway', 'kms'],
        required=True,
        help='Deployment mode (railway for PaaS, kms for AWS KMS)'
    )
    
    parser.add_argument(
        '--key-id',
        help='KMS key ID or alias (required for kms mode, e.g., alias/audit-crypto-key)'
    )
    
    parser.add_argument(
        '--region',
        help='AWS region (optional, defaults to AWS_REGION env var or us-east-1)'
    )
    
    parser.add_argument(
        '--validate',
        action='store_true',
        help='Validate the generated key before displaying'
    )
    
    args = parser.parse_args()
    
    # Validate arguments
    if args.mode == 'kms' and not args.key_id:
        parser.error("--key-id is required when using --mode kms")
    
    # Execute the appropriate mode
    if args.mode == 'railway':
        railway_mode(validate=args.validate)
    else:  # kms
        kms_mode(args.key_id, args.region, validate=args.validate)


if __name__ == '__main__':
    main()
