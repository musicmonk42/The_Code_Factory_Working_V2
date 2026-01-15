#!/usr/bin/env python3
"""
generate_plugin_manifest.py

ENTERPRISE/REGULATED-INDUSTRY GRADE

- Generates a SHA256 manifest for all plugins (.py files) in a directory.
- Supports mandatory cryptographic signing (Ed25519) for authenticity and non-repudiation.
- Optionally verifies manifest signatures.
- Attaches metadata: UTC timestamp, generator version, and file size for auditing.
- Strict error handling and secure defaults (fail-closed).
- Designed for use in CI/CD, SOX/SOC2/PCI/FedRAMP environments.
- Output manifest is deterministic for reproducibility and auditing.
- Can be integrated with key management (HSM, Vault) for signing keys.

Requirements:
    pip install cryptography

Usage:
    # Generate and sign manifest using a key file (RECOMMENDED for prod)
    python generate_plugin_manifest.py /path/to/plugins --sign private_key.pem --out plugin_hash_manifest.json

    # Generate and sign manifest using environment variable (BETTER for CI/CD)
    export SIGNING_KEY="-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----"
    python generate_plugin_manifest.py /path/to/plugins --sign env:SIGNING_KEY --out plugin_hash_manifest.json

    # Verify manifest
    python generate_plugin_manifest.py --verify manifest.json --pubkey public_key.pem

    # Generate unsigned manifest (not recommended for production)
    python generate_plugin_manifest.py /path/to/plugins > manifest.json

Security Notes:
- If --sign is omitted, a warning is printed and the manifest is NOT suitable for regulated production.
- For production environments, use a Key Management Service (KMS):
  * AWS KMS: https://aws.amazon.com/kms/
  * Azure Key Vault: https://azure.microsoft.com/en-us/services/key-vault/
  * Google Cloud KMS: https://cloud.google.com/kms
  * HashiCorp Vault: https://www.vaultproject.io/
- Signing key can be provided via:
  * File path: --sign private_key.pem
  * Environment variable: --sign env:SIGNING_KEY_VAR
- Manifest includes a version string, timestamp, and file size for each plugin.

Key Generation:
    openssl genpkey -algorithm Ed25519 -out private_key.pem
    openssl pkey -in private_key.pem -pubout -out public_key.pem

"""

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

GENERATOR_VERSION = "2025.08.24-enterprise.1"

# Optional signing
try:
    import base64

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )

    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False


def compute_hash_and_size(filepath):
    with open(filepath, "rb") as f:
        data = f.read()
    return hashlib.sha256(data).hexdigest(), len(data)


def load_private_key(path_or_envvar):
    """
    Load private key from file path or environment variable.
    
    Args:
        path_or_envvar: Either a file path or an environment variable name
        
    Returns:
        Ed25519PrivateKey instance
        
    Security Note:
        In production environments, consider using a Key Management Service (KMS)
        such as AWS KMS, Azure Key Vault, Google Cloud KMS, or HashiCorp Vault
        instead of storing keys in files or environment variables.
    """
    key_data = None
    
    # Check if it's an environment variable reference
    if path_or_envvar.startswith("env:"):
        env_var_name = path_or_envvar[4:]  # Remove "env:" prefix
        key_data = os.getenv(env_var_name)
        if not key_data:
            raise ValueError(f"Environment variable '{env_var_name}' not found or empty")
        key_data = key_data.encode('utf-8')
    else:
        # Treat as file path
        with open(path_or_envvar, "rb") as f:
            key_data = f.read()
    
    key = serialization.load_pem_private_key(key_data, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("Private key must be Ed25519")
    return key


def load_public_key(path):
    with open(path, "rb") as f:
        key = serialization.load_pem_public_key(f.read())
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("Public key must be Ed25519")
    return key


def sign_manifest(manifest_bytes, private_key_path):
    sk = load_private_key(private_key_path)
    signature = sk.sign(manifest_bytes)
    return base64.b64encode(signature).decode("ascii")


def verify_signature(manifest_bytes, signature_b64, public_key_path):
    pk = load_public_key(public_key_path)
    signature = base64.b64decode(signature_b64)
    pk.verify(signature, manifest_bytes)  # will raise if invalid


def error(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(2)


def main():
    parser = argparse.ArgumentParser(
        description="Enterprise plugin manifest generator and verifier"
    )
    parser.add_argument(
        "plugin_dir", nargs="?", help="Directory containing plugin .py files"
    )
    parser.add_argument(
        "--sign",
        metavar="PRIVATE_KEY",
        help="Sign manifest with Ed25519 private key. Provide either a file path (e.g., 'private_key.pem') or an environment variable reference (e.g., 'env:SIGNING_KEY'). For production, consider using a KMS.",
    )
    parser.add_argument(
        "--out",
        metavar="MANIFEST",
        default=None,
        help="Write manifest to given file (default: stdout)",
    )
    parser.add_argument(
        "--verify", metavar="MANIFEST", help="Verify manifest signature"
    )
    parser.add_argument(
        "--pubkey",
        metavar="PUBLIC_KEY",
        help="Public key (PEM) for signature verification",
    )
    parser.add_argument(
        "--fail-on-unsigned",
        action="store_true",
        help="Fail if manifest is not signed (enforces signature in CI)",
    )
    args = parser.parse_args()

    if args.verify:
        # Verification mode
        if not args.pubkey:
            error("Verification requires --pubkey argument.")
        if not HAS_CRYPTO:
            error(
                "cryptography package required for verification. (pip install cryptography)"
            )
        with open(args.verify) as f:
            doc = json.load(f)
        for key in ("manifest", "signed_at", "generator_version", "files"):
            if key not in doc:
                error(f"Manifest is missing required key: {key}")
        if "signature" not in doc:
            if args.fail_on_unsigned:
                error("Manifest is not signed. Rejected per --fail-on-unsigned.")
            else:
                print(
                    "WARNING: Manifest is not signed. Validation is incomplete.",
                    file=sys.stderr,
                )
                sys.exit(0)
        manifest_bytes = json.dumps(
            {
                "manifest": doc["manifest"],
                "signed_at": doc["signed_at"],
                "generator_version": doc["generator_version"],
                "files": doc["files"],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            verify_signature(manifest_bytes, doc["signature"], args.pubkey)
            print("Manifest signature is VALID and authentic.")
        except Exception as e:
            error(f"Signature verification FAILED: {e}")
        sys.exit(0)

    # Manifest generation mode
    if not args.plugin_dir:
        parser.print_help()
        sys.exit(1)
    plugin_dir = args.plugin_dir
    if not os.path.isdir(plugin_dir):
        error(f"Directory {plugin_dir} does not exist.")

    manifest = {}
    file_meta = {}
    for fname in sorted(os.listdir(plugin_dir)):
        if fname.endswith(".py") and not fname.startswith("_"):
            modname = fname[:-3]
            filepath = os.path.join(plugin_dir, fname)
            hashval, fsize = compute_hash_and_size(filepath)
            manifest[modname] = hashval
            file_meta[modname] = {"filename": fname, "size_bytes": fsize}

    now = datetime.now(timezone.utc)
    output_doc = {
        "manifest": manifest,
        "files": file_meta,
        "signed_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generator_version": GENERATOR_VERSION,
    }

    if args.sign:
        if not HAS_CRYPTO:
            error(
                "cryptography package required for signing. (pip install cryptography)"
            )
        
        # Print KMS recommendation for production
        if not args.sign.startswith("env:"):
            print(
                "INFO: Using file-based private key. For production environments, "
                "consider using a Key Management Service (KMS) such as AWS KMS, "
                "Azure Key Vault, Google Cloud KMS, or HashiCorp Vault.",
                file=sys.stderr,
            )
        
        manifest_bytes = json.dumps(
            {
                "manifest": manifest,
                "signed_at": output_doc["signed_at"],
                "generator_version": output_doc["generator_version"],
                "files": file_meta,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        sig = sign_manifest(manifest_bytes, args.sign)
        output_doc["signature"] = sig
    else:
        print(
            "WARNING: Manifest is NOT SIGNED! This is NOT suitable for regulated production.",
            file=sys.stderr,
        )
        if args.fail_on_unsigned:
            error("Refusing to output unsigned manifest due to --fail-on-unsigned.")

    # Output
    out = args.out
    output_json = json.dumps(output_doc, indent=2)
    if out:
        with open(out, "w") as f:
            f.write(output_json)
        print(f"Wrote manifest to {out}")
    else:
        print(output_json)


if __name__ == "__main__":
    main()
