from __future__ import annotations

from types import SimpleNamespace

import pytest

from qq_personal_bot.download_storage import DownloadObjectStorage, DownloadStorageError


class FakeMinioClient:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.objects: dict[str, bytes] = {}

    def bucket_exists(self, bucket: str) -> bool:
        return self.available

    def put_object(self, bucket, object_key, stream, length, **kwargs):
        self.objects[object_key] = stream.read(length)

    def stat_object(self, bucket, object_key):
        return SimpleNamespace(size=len(self.objects[object_key]))

    def get_object(self, bucket, object_key):
        return self.objects[object_key]

    def remove_object(self, bucket, object_key):
        self.objects.pop(object_key, None)


def _storage(client: FakeMinioClient) -> DownloadObjectStorage:
    return DownloadObjectStorage(
        "127.0.0.1:9000",
        "access",
        "secret",
        "qqbot-downloads",
        client=client,
    )


def test_minio_storage_uploads_reads_stats_and_removes():
    client = FakeMinioClient()
    storage = _storage(client)

    storage.ensure_available()
    storage.put_image("20260817/image.png", b"image", "image/png", {"sha256": "a" * 64})

    assert storage.stat_image("20260817/image.png").size == 5
    assert storage.get_image("20260817/image.png") == b"image"
    storage.remove_image("20260817/image.png")
    assert client.objects == {}


def test_minio_storage_reports_missing_bucket():
    with pytest.raises(DownloadStorageError, match="bucket"):
        _storage(FakeMinioClient(available=False)).ensure_available()


def test_minio_storage_requires_credentials():
    with pytest.raises(DownloadStorageError, match="尚未配置"):
        DownloadObjectStorage("127.0.0.1:9000", "", "", "qqbot-downloads")
