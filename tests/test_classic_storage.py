from __future__ import annotations

from io import BytesIO

import pytest

from qq_personal_bot.classic_storage import (
    ClassicObjectStorage,
    ClassicStorageError,
    classic_bucket_name,
    read_classic_image_source,
)


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self.stream = BytesIO(body)

    def read(self, size: int = -1) -> bytes:
        return self.stream.read(size)

    def close(self) -> None:
        self.stream.close()

    def release_conn(self) -> None:
        pass


class FakeMinioClient:
    def __init__(self) -> None:
        self.buckets: dict[str, dict[str, bytes]] = {}

    def bucket_exists(self, bucket: str) -> bool:
        return bucket in self.buckets

    def make_bucket(self, bucket: str) -> None:
        self.buckets[bucket] = {}

    def put_object(self, bucket, object_key, stream, length, **kwargs):
        self.buckets[bucket][object_key] = stream.read(length)

    def stat_object(self, bucket, object_key):
        return type("Stat", (), {"size": len(self.buckets[bucket][object_key])})()

    def get_object(self, bucket, object_key):
        return FakeResponse(self.buckets[bucket][object_key])

    def remove_object(self, bucket, object_key):
        self.buckets[bucket].pop(object_key, None)

    def remove_bucket(self, bucket):
        if self.buckets[bucket]:
            raise RuntimeError("bucket not empty")
        del self.buckets[bucket]


def _storage(client: FakeMinioClient) -> ClassicObjectStorage:
    return ClassicObjectStorage(
        "127.0.0.1:9000",
        "access",
        "secret",
        client=client,
    )


def test_classic_storage_uses_one_bucket_per_group():
    client = FakeMinioClient()
    storage = _storage(client)

    storage.put_image(123, "image.gif", b"GIF89a", "image/gif", {"sha256": "a" * 64})
    storage.put_image(456, "image.gif", b"GIF89a", "image/gif", {"sha256": "a" * 64})

    assert set(client.buckets) == {"qqbot-classics-123", "qqbot-classics-456"}
    assert storage.read_image(123, "image.gif") == b"GIF89a"
    storage.remove_group_bucket(123, ["image.gif"])
    assert "qqbot-classics-123" not in client.buckets


def test_classic_storage_does_not_create_bucket_for_read_check():
    client = FakeMinioClient()

    with pytest.raises(ClassicStorageError, match="不存在"):
        _storage(client).ensure_group_bucket(123)

    assert client.buckets == {}


def test_classic_image_source_hash_input_supports_local_file(tmp_path):
    image = tmp_path / "classic.gif"
    image.write_bytes(b"GIF89aclassic")

    body, suffix, content_type = read_classic_image_source(str(image))

    assert body == b"GIF89aclassic"
    assert suffix == ".gif"
    assert content_type == "image/gif"
    assert classic_bucket_name(123) == "qqbot-classics-123"
