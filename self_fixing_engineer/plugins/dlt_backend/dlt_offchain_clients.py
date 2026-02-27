# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
dlt_offchain_clients.py

Production off-chain storage client backed by AWS S3.

Drop-in replacement for the dev file-backed stub used when this module cannot
be imported.  Requires the ``boto3`` package and valid AWS credentials
(``AWS_ACCESS_KEY_ID`` / ``AWS_SECRET_ACCESS_KEY`` environment variables, an
IAM instance profile, or any other credential source supported by botocore).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from typing import Any, Dict, Optional, Union

logger = logging.getLogger(__name__)

try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError

    _BOTO3_AVAILABLE = True
except ImportError:
    _BOTO3_AVAILABLE = False


class S3OffChainClient:
    """Production AWS S3-backed off-chain blob storage.

    All I/O is delegated to ``asyncio.to_thread`` so it never blocks the
    event loop.  The underlying boto3 client is created lazily and is
    thread-safe for concurrent ``get_object`` / ``put_object`` calls.

    Configuration keys (passed as *config* dict):
        bucket_name        (str, required)  S3 bucket for checkpoint blobs.
        region_name        (str)            AWS region; default ``us-east-1``.
        aws_access_key_id  (str)            Explicit key ID (prefer IAM role).
        aws_secret_access_key (str)         Explicit secret (prefer IAM role).
        key_prefix         (str)            Optional S3 key prefix / folder.
        sse                (str)            Server-side encryption algorithm,
                                            e.g. ``"aws:kms"`` (default: none).
    """

    def __init__(self, config: Dict[str, Any]) -> None:
        if not _BOTO3_AVAILABLE:
            raise ImportError(
                "boto3 is required for production S3OffChainClient. "
                "Install it with: pip install boto3"
            )

        self._bucket = config["bucket_name"]
        self._key_prefix: str = config.get("key_prefix", "dlt-offchain/")
        self._sse: Optional[str] = config.get("sse")

        boto_kwargs: Dict[str, Any] = {
            "region_name": config.get("region_name", "us-east-1"),
        }
        if config.get("aws_access_key_id"):
            boto_kwargs["aws_access_key_id"] = config["aws_access_key_id"]
        if config.get("aws_secret_access_key"):
            boto_kwargs["aws_secret_access_key"] = config["aws_secret_access_key"]

        self._s3 = boto3.client("s3", **boto_kwargs)
        logger.info(
            "S3OffChainClient initialised (bucket=%r, region=%s).",
            self._bucket,
            boto_kwargs["region_name"],
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _object_key(self, blob_id: str) -> str:
        return f"{self._key_prefix}{blob_id}.json"

    def _put_sync(self, key: str, body: bytes) -> None:
        kwargs: Dict[str, Any] = {
            "Bucket": self._bucket,
            "Key": key,
            "Body": body,
            "ContentType": "application/json",
        }
        if self._sse:
            kwargs["ServerSideEncryption"] = self._sse
        self._s3.put_object(**kwargs)

    def _get_sync(self, key: str) -> bytes:
        response = self._s3.get_object(Bucket=self._bucket, Key=key)
        return response["Body"].read()

    # ------------------------------------------------------------------
    # Public async API (mirrors dev stub interface)
    # ------------------------------------------------------------------

    async def save_blob(
        self,
        checkpoint_name: str,
        blob: Union[str, bytes, dict, list],
        correlation_id: Optional[str] = None,
    ) -> str:
        """Upload *blob* to S3 and return the blob ID (S3 object key suffix)."""
        blob_id = str(uuid.uuid4())
        if isinstance(blob, (dict, list)):
            payload = json.dumps(
                {"checkpoint_name": checkpoint_name, "blob": blob}
            ).encode()
        elif isinstance(blob, str):
            payload = json.dumps(
                {"checkpoint_name": checkpoint_name, "blob": blob}
            ).encode()
        else:
            payload = json.dumps(
                {
                    "checkpoint_name": checkpoint_name,
                    "blob": blob.decode("utf-8", errors="replace"),
                }
            ).encode()

        key = self._object_key(blob_id)
        try:
            await asyncio.to_thread(self._put_sync, key, payload)
        except (BotoCoreError, ClientError) as exc:
            logger.error(
                "S3OffChainClient.save_blob failed (blob_id=%s, cid=%s): %s",
                blob_id,
                correlation_id,
                exc,
            )
            raise
        logger.debug("Saved blob %s → s3://%s/%s", blob_id, self._bucket, key)
        return blob_id

    async def get_blob(
        self,
        off_chain_id: str,
        correlation_id: Optional[str] = None,
    ) -> Any:
        """Download and deserialise the blob stored under *off_chain_id*."""
        key = self._object_key(off_chain_id)
        try:
            raw = await asyncio.to_thread(self._get_sync, key)
        except (BotoCoreError, ClientError) as exc:
            logger.error(
                "S3OffChainClient.get_blob failed (id=%s, cid=%s): %s",
                off_chain_id,
                correlation_id,
                exc,
            )
            raise
        return json.loads(raw).get("blob")

    async def health_check(self, correlation_id: Optional[str] = None) -> Dict[str, Any]:
        """Return a status dict; raises on critical S3 connectivity issues."""
        def _head():
            self._s3.head_bucket(Bucket=self._bucket)

        try:
            await asyncio.to_thread(_head)
            return {"status": True, "message": f"S3 bucket {self._bucket!r} is accessible."}
        except (BotoCoreError, ClientError) as exc:
            return {"status": False, "message": f"S3 bucket check failed: {exc}"}

    async def close(self) -> None:
        """No-op: boto3 sessions are not async and need no explicit teardown."""
