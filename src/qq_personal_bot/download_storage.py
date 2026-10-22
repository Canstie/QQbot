from __future__ import annotations

from typing import Any

from qq_personal_bot.object_storage import ObjectStorage
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
        self.objects = ObjectStorage(endpoint, access_key, secret_key, secure=secure, client=client)
        self.client = self.objects.client

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
            self.objects.put(self.bucket, object_key, body, content_type, metadata)
        except Exception as exc:
            raise DownloadStorageError(f"MinIO 上传失败：{object_key}") from exc

    def stat_image(self, object_key: str) -> Any:
        try:
            return self.objects.stat(self.bucket, object_key)
        except Exception as exc:
            raise DownloadStorageError(f"MinIO 对象校验失败：{object_key}") from exc

    def image_exists(self, object_key: str) -> bool:
        try:
            return self.objects.exists(self.bucket, object_key)
        except Exception as exc:
            raise DownloadStorageError(f"MinIO 对象校验失败：{object_key}") from exc

    def get_image(self, object_key: str) -> Any:
        try:
            return self.objects.get(self.bucket, object_key)
        except Exception as exc:
            raise DownloadStorageError(f"MinIO 图片读取失败：{object_key}") from exc

    def remove_image(self, object_key: str, *, missing_ok: bool = False) -> None:
        try:
            self.objects.remove(self.bucket, object_key, missing_ok=missing_ok)
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
