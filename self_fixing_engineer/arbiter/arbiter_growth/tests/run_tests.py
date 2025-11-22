import pytest
import os
import sys
import base64


def main():
    """
    Main function to run the pytest suite with a predefined set of arguments.
    """
    # Set a valid 32-byte base64-encoded encryption key
    valid_key = base64.urlsafe_b64encode(os.urandom(32)).decode("utf-8")
    os.environ.setdefault("ARBITER_ENCRYPTION_KEY", valid_key)
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379")

    # Add project root to Python path
    project_root = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, project_root)

    # Add environment variables for etcd configuration
    os.environ.setdefault("ETCD_HOST", "localhost")
    os.environ.setdefault("ETCD_PORT", "2379")

    # Run tests with appropriate flags
    args = [
        "arbiter/arbiter_growth/tests",
        "-v",
        "--tb=short",
        "--disable-warnings",
        "-x",  # Stop on first failure for debugging
        "--log-level=DEBUG",  # Enable debug logging for tests
    ]

    sys.exit(pytest.main(args))


if __name__ == "__main__":
    main()
