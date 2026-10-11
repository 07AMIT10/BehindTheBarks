"""Snapshot storage abstraction (Cloudflare R2 / S3 object storage with fallback to base64).

Provides:
- EphemeralStorage: Zero-configuration default formatting snapshots as base64 data URIs.
- R2SnapshotStorage: Cloudflare R2 (S3-compatible) storage using private buckets and presigned URLs.
- build_storage: Configuration and environment factory.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import uuid
from typing import Any, Protocol

log = logging.getLogger("storage")


class SnapshotStorage(Protocol):
    async def store_snapshot(
        self,
        jpeg: bytes | None,
        *,
        household_id: str,
        ts: float,
        emotion: str,
    ) -> str | None:
        """Store or format a snapshot JPEG, returning an HTTPS URL or base64 data URI."""
        ...


class EphemeralStorage:
    """Default zero-cloud storage: inlines snapshots as base64 data URIs."""

    async def store_snapshot(
        self,
        jpeg: bytes | None,
        *,
        household_id: str,
        ts: float,
        emotion: str,
    ) -> str | None:
        if not jpeg:
            return None
        try:
            return f"data:image/jpeg;base64,{base64.b64encode(jpeg).decode('ascii')}"
        except Exception as exc:  # noqa: BLE001
            log.warning("failed to encode base64 snapshot: %s", exc)
            return None


class R2SnapshotStorage:
    """Cloudflare R2 (S3-compatible) object storage for alert snapshots with presigned URLs.

    Key properties:
    - Private bucket by default for domestic privacy.
    - Generates short-lived presigned GET URLs (default 900s / 15m).
    - Zero internet egress fees on Cloudflare R2.
    - Non-blocking: boto3 put_object and presign run in asyncio.to_thread.
    - Defensive fallback: on any upload or network failure, falls back to inlined base64.
    """

    def __init__(
        self,
        *,
        account_id: str,
        access_key_id: str,
        secret_access_key: str,
        bucket: str = "btb-snapshots",
        endpoint_url: str | None = None,
        presigned_ttl_s: int = 900,
        public_url: str | None = None,
        s3_client: Any = None,
    ) -> None:
        self.account_id = account_id
        self.bucket = bucket
        self.presigned_ttl_s = presigned_ttl_s
        self.public_url = public_url.rstrip("/") if public_url else None
        self.endpoint_url = (
            endpoint_url or f"https://{account_id}.r2.cloudflarestorage.com"
        )
        if s3_client is not None:
            self._client = s3_client
        else:
            import boto3
            from botocore.config import Config

            self._client = boto3.client(
                "s3",
                endpoint_url=self.endpoint_url,
                aws_access_key_id=access_key_id,
                aws_secret_access_key=secret_access_key,
                region_name="auto",
                config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
            )

    def url_for(self, key: str) -> str:
        """A time-limited GET URL for a stored object (the public URL when the bucket is public)."""
        if self.public_url:
            return f"{self.public_url}/{key}"
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=self.presigned_ttl_s,
        )

    def put_file(self, path: Any, key: str, content_type: str = "video/mp4") -> None:
        self._client.upload_file(str(path), self.bucket, key, ExtraArgs={"ContentType": content_type})

    def get_file(self, key: str, dest: Any) -> None:
        self._client.download_file(self.bucket, key, str(dest))

    def list_keys(self, prefix: str) -> list[tuple[str, Any]]:
        """(key, LastModified) for every object under prefix, oldest first."""
        out: list[tuple[str, Any]] = []
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            out.extend((o["Key"], o["LastModified"]) for o in page.get("Contents", []))
        return sorted(out, key=lambda item: item[1])

    def delete_key(self, key: str) -> None:
        self._client.delete_object(Bucket=self.bucket, Key=key)

    def object_size(self, key: str) -> int | None:
        """Size of a stored object, or None when it is missing (the uploader uses this to skip work)."""
        try:
            return int(self._client.head_object(Bucket=self.bucket, Key=key)["ContentLength"])
        except Exception:  # noqa: BLE001 - a missing key is the normal case here
            return None

    def _upload_and_presign(self, jpeg: bytes, key: str) -> str:
        self._client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=jpeg,
            ContentType="image/jpeg",
        )
        if self.public_url:
            return f"{self.public_url}/{key}"
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=self.presigned_ttl_s,
        )

    async def store_snapshot(
        self,
        jpeg: bytes | None,
        *,
        household_id: str,
        ts: float,
        emotion: str,
    ) -> str | None:
        if not jpeg:
            return None
        key = f"snapshots/{household_id}/{int(ts)}_{uuid.uuid4().hex[:8]}_{emotion}.jpg"
        try:
            url = await asyncio.to_thread(self._upload_and_presign, jpeg, key)
            return url
        except Exception as exc:  # noqa: BLE001
            log.warning("r2 upload failed for key %s: %s; falling back to base64", key, exc)
            return f"data:image/jpeg;base64,{base64.b64encode(jpeg).decode('ascii')}"


def build_r2(cfg: dict | None = None) -> R2SnapshotStorage | None:
    """The configured R2 client, or None when R2 is not set up (or the client fails to start).

    Reads the same environment variables as build_storage. Used by the snapshot path (with a base64
    fallback) and by the demo-video path (which has no fallback and serves local files instead).
    """
    r2_cfg = (cfg.get("web", {}).get("storage", {}) if cfg else {}) or {}
    acc_id = (
        os.environ.get("BTB_R2_ACCOUNT_ID")
        or os.environ.get("R2_ACCOUNT_ID")
        or r2_cfg.get("account_id")
        or ""
    ).strip()
    key_id = (
        os.environ.get("BTB_R2_ACCESS_KEY_ID")
        or os.environ.get("R2_ACCESS_KEY_ID")
        or r2_cfg.get("access_key_id")
        or ""
    ).strip()
    secret = (
        os.environ.get("BTB_R2_SECRET_ACCESS_KEY")
        or os.environ.get("R2_SECRET_ACCESS_KEY")
        or r2_cfg.get("secret_access_key")
        or ""
    ).strip()
    bucket = (
        os.environ.get("BTB_R2_BUCKET")
        or os.environ.get("R2_BUCKET")
        or r2_cfg.get("bucket")
        or "btb-snapshots"
    ).strip()
    endpoint = (
        os.environ.get("BTB_R2_ENDPOINT_URL")
        or os.environ.get("R2_ENDPOINT_URL")
        or r2_cfg.get("endpoint_url")
        or None
    )
    raw_ttl = (
        os.environ.get("BTB_R2_PRESIGNED_TTL_S")
        or os.environ.get("R2_PRESIGNED_TTL_S")
        or r2_cfg.get("presigned_ttl_s")
        or 900
    )
    try:
        ttl_s = int(raw_ttl)
    except (ValueError, TypeError):
        ttl_s = 900
    pub_url = (
        os.environ.get("BTB_R2_PUBLIC_URL")
        or os.environ.get("R2_PUBLIC_URL")
        or r2_cfg.get("public_url")
        or None
    )

    if acc_id and key_id and secret and bucket:
        try:
            storage = R2SnapshotStorage(
                account_id=acc_id,
                access_key_id=key_id,
                secret_access_key=secret,
                bucket=bucket,
                endpoint_url=endpoint,
                presigned_ttl_s=ttl_s,
                public_url=pub_url,
            )
            log.info("r2 storage enabled for bucket %r (account: %s...)", bucket, acc_id[:6])
            return storage
        except Exception as exc:  # noqa: BLE001
            log.warning("failed to initialize R2 storage (%s)", exc)
            return None

    return None


def build_storage(cfg: dict | None = None) -> SnapshotStorage:
    """Snapshot storage: R2 when configured, otherwise inline base64 (EphemeralStorage)."""
    return build_r2(cfg) or EphemeralStorage()
