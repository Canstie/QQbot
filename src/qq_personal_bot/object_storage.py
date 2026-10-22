"""MinIO operations; bucket ownership and user-facing errors belong to callers."""

from __future__ import annotations

from io import BytesIO
from typing import Any

from minio import Minio
from minio.error import S3Error


class ObjectStorage:
    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        *,
        secure: bool = False,
        client: Any | None = None,
    ) -> None:
        self.client = client or Minio(
            endpoint, access_key=access_key, secret_key=secret_key, secure=secure
        )

    def put(
        self, bucket: str, key: str, body: bytes, content_type: str, metadata: dict[str, str]
    ) -> None:
        self.client.put_object(
            bucket, key, BytesIO(body), len(body), content_type=content_type, metadata=metadata
        )

    def stat(self, bucket: str, key: str) -> Any:
        return self.client.stat_object(bucket, key)

    def get(self, bucket: str, key: str) -> Any:
        return self.client.get_object(bucket, key)

    def exists(self, bucket: str, key: str) -> bool:
        try:
            self.stat(bucket, key)
        except S3Error as exc:
            if exc.code in {"NoSuchKey", "NoSuchObject"}:
                return False
            raise
        return True

    def remove(self, bucket: str, key: str, *, missing_ok: bool = False) -> None:
        try:
            self.client.remove_object(bucket, key)
        except S3Error as exc:
            if missing_ok and exc.code in {"NoSuchKey", "NoSuchObject", "NoSuchBucket"}:
                return
            raise
