from __future__ import annotations

import base64
import binascii
from io import BytesIO
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from minio import Minio
from minio.error import S3Error

from qq_personal_bot.menu_recipes import resolve_local_source
from qq_personal_bot.runtime import get_settings


_MAX_CLASSIC_IMAGE_BYTES = 20 * 1024 * 1024


class ClassicStorageError(RuntimeError):
    pass


def classic_bucket_name(group_id: int) -> str:
    normalized_group_id = int(group_id)
    if normalized_group_id <= 0:
        raise ValueError("group_id must be positive")
    return f"qqbot-classics-{normalized_group_id}"


class ClassicObjectStorage:
    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        *,
        secure: bool = False,
        client: Any | None = None,
    ) -> None:
        if not endpoint or not access_key or not secret_key:
            raise ClassicStorageError("MinIO 尚未配置。")
        self.client = client or Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
        )

    def ensure_group_bucket(self, group_id: int, *, create: bool = False) -> str:
        bucket = classic_bucket_name(group_id)
        try:
            if self.client.bucket_exists(bucket):
                return bucket
            if not create:
                raise ClassicStorageError(f"群典藏 bucket 不存在：{bucket}")
            self.client.make_bucket(bucket)
            return bucket
        except ClassicStorageError:
            raise
        except S3Error as exc:
            if create and exc.code in {"BucketAlreadyExists", "BucketAlreadyOwnedByYou"}:
                return bucket
            raise ClassicStorageError(f"群典藏 bucket 不可用：{bucket}") from exc
        except Exception as exc:
            raise ClassicStorageError(f"群典藏 bucket 不可用：{bucket}") from exc

    def put_image(
        self,
        group_id: int,
        object_key: str,
        body: bytes,
        content_type: str,
        metadata: dict[str, str],
    ) -> None:
        bucket = self.ensure_group_bucket(group_id, create=True)
        try:
            self.client.put_object(
                bucket,
                object_key,
                BytesIO(body),
                len(body),
                content_type=content_type,
                metadata=metadata,
            )
        except Exception as exc:
            raise ClassicStorageError(f"群典图片上传失败：{bucket}/{object_key}") from exc

    def stat_image(self, group_id: int, object_key: str) -> Any:
        bucket = classic_bucket_name(group_id)
        try:
            return self.client.stat_object(bucket, object_key)
        except Exception as exc:
            raise ClassicStorageError(f"群典图片校验失败：{bucket}/{object_key}") from exc

    def get_image(self, group_id: int, object_key: str) -> Any:
        bucket = classic_bucket_name(group_id)
        try:
            return self.client.get_object(bucket, object_key)
        except Exception as exc:
            raise ClassicStorageError(f"群典图片读取失败：{bucket}/{object_key}") from exc

    def read_image(self, group_id: int, object_key: str) -> bytes:
        response = self.get_image(group_id, object_key)
        try:
            body = response.read(_MAX_CLASSIC_IMAGE_BYTES + 1)
        finally:
            response.close()
            response.release_conn()
        if len(body) > _MAX_CLASSIC_IMAGE_BYTES:
            raise ClassicStorageError("群典图片超过 20 MiB。")
        return body

    def remove_image(self, group_id: int, object_key: str, *, missing_ok: bool = False) -> None:
        bucket = classic_bucket_name(group_id)
        try:
            self.client.remove_object(bucket, object_key)
        except S3Error as exc:
            if missing_ok and exc.code in {"NoSuchKey", "NoSuchObject", "NoSuchBucket"}:
                return
            raise ClassicStorageError(f"群典图片删除失败：{bucket}/{object_key}") from exc
        except Exception as exc:
            raise ClassicStorageError(f"群典图片删除失败：{bucket}/{object_key}") from exc

    def remove_group_bucket(self, group_id: int, object_keys: list[str]) -> None:
        bucket = classic_bucket_name(group_id)
        try:
            if not self.client.bucket_exists(bucket):
                return
            for object_key in object_keys:
                self.client.remove_object(bucket, object_key)
            self.client.remove_bucket(bucket)
        except Exception as exc:
            raise ClassicStorageError(f"群典藏 bucket 删除失败：{bucket}") from exc


def get_classic_storage() -> ClassicObjectStorage:
    settings = get_settings()
    return ClassicObjectStorage(
        settings.minio_endpoint,
        settings.minio_access_key,
        settings.minio_secret_key,
        secure=settings.minio_secure,
    )


def read_classic_image_source(source: str) -> tuple[bytes, str, str]:
    normalized_source = str(source or "").strip()
    if not normalized_source:
        raise ValueError("empty image source")
    if normalized_source.startswith("base64://"):
        try:
            body = base64.b64decode(
                normalized_source.removeprefix("base64://"),
                validate=True,
            )
        except (ValueError, binascii.Error) as exc:
            raise ValueError("invalid base64 image") from exc
    else:
        parsed = urlparse(normalized_source)
        if parsed.scheme in {"http", "https"}:
            request = Request(normalized_source, headers={"User-Agent": "qq-personal-bot/0.1"})
            with urlopen(request, timeout=30) as response:
                body = response.read(_MAX_CLASSIC_IMAGE_BYTES + 1)
        else:
            path = resolve_local_source(normalized_source, parsed=parsed).resolve(strict=True)
            with path.open("rb") as image_file:
                body = image_file.read(_MAX_CLASSIC_IMAGE_BYTES + 1)
    if not body:
        raise ValueError("empty image")
    if len(body) > _MAX_CLASSIC_IMAGE_BYTES:
        raise ValueError("image exceeds 20 MiB")
    image_type = classic_image_type(body)
    if image_type is None:
        raise ValueError("unsupported image format")
    suffix, content_type = image_type
    return body, suffix, content_type


def classic_image_type(body: bytes) -> tuple[str, str] | None:
    if body.startswith(b"\xff\xd8\xff"):
        return ".jpg", "image/jpeg"
    if body.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png", "image/png"
    if body.startswith((b"GIF87a", b"GIF89a")):
        return ".gif", "image/gif"
    if len(body) >= 12 and body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return ".webp", "image/webp"
    return None
