from __future__ import annotations

from io import BytesIO
from typing import Any

from minio import Minio
from minio.error import S3Error

from qq_personal_bot.runtime import get_settings


class DownloadStorageError(RuntimeError):
    pass


class DownloadObjectStorage:
    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        *,
        secure: bool = False,
        client: Any | None = None,
    ) -> None:
        if not endpoint or not access_key or not secret_key or not bucket:
            raise DownloadStorageError("MinIO 尚未配置。")
        self.bucket = bucket
        self.client = client or Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
        )

    def ensure_available(self) -> None:
        try:
            if not self.client.bucket_exists(self.bucket):
                raise DownloadStorageError(f"MinIO bucket 不存在：{self.bucket}")
        except DownloadStorageError:
            raise
        except Exception as exc:
            raise DownloadStorageError("MinIO 暂不可用。") from exc

    def put_image(
        self,
        object_key: str,
        body: bytes,
        content_type: str,
        metadata: dict[str, str],
    ) -> None:
        try:
            self.client.put_object(
                self.bucket,
                object_key,
                BytesIO(body),
                len(body),
                content_type=content_type,
                metadata=metadata,
            )
        except Exception as exc:
            raise DownloadStorageError(f"MinIO 上传失败：{object_key}") from exc

    def stat_image(self, object_key: str) -> Any:
        try:
            return self.client.stat_object(self.bucket, object_key)
        except Exception as exc:
            raise DownloadStorageError(f"MinIO 对象校验失败：{object_key}") from exc

    def get_image(self, object_key: str) -> Any:
        try:
            return self.client.get_object(self.bucket, object_key)
        except Exception as exc:
            raise DownloadStorageError(f"MinIO 图片读取失败：{object_key}") from exc

    def remove_image(self, object_key: str, *, missing_ok: bool = False) -> None:
        try:
            self.client.remove_object(self.bucket, object_key)
        except S3Error as exc:
            if missing_ok and exc.code in {"NoSuchKey", "NoSuchObject", "NoSuchBucket"}:
                return
            raise DownloadStorageError(f"MinIO 图片删除失败：{object_key}") from exc
        except Exception as exc:
            raise DownloadStorageError(f"MinIO 图片删除失败：{object_key}") from exc


def get_download_storage() -> DownloadObjectStorage:
    settings = get_settings()
    return DownloadObjectStorage(
        settings.minio_endpoint,
        settings.minio_access_key,
        settings.minio_secret_key,
        settings.minio_bucket,
        secure=settings.minio_secure,
    )
