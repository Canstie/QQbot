from __future__ import annotations

import argparse
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from qq_personal_bot.download_storage import get_download_storage
from qq_personal_bot.runtime import get_store

_CHINA_TZ = timezone(timedelta(hours=8))
_IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp"})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate local /download images to MinIO")
    parser.add_argument("--source", type=Path, default=Path("downloadimage"))
    parser.add_argument("--delete-source-after-verify", action="store_true")
    return parser.parse_args()


def migrate(source: Path, *, delete_source_after_verify: bool) -> dict[str, int]:
    source = source.resolve(strict=False)
    paths = sorted(
        path
        for path in source.rglob("*")
        if path.is_file() and path.suffix.casefold() in _IMAGE_SUFFIXES
    ) if source.is_dir() else []
    storage = get_download_storage()
    storage.ensure_available()
    store = get_store()
    succeeded = 0
    skipped = 0
    failed = 0
    expected: list[tuple[Path, str, int]] = []

    for path in paths:
        try:
            body = path.read_bytes()
            image_type = _image_type(body)
            if image_type is None:
                raise ValueError("unsupported image format")
            suffix, content_type = image_type
            digest = hashlib.sha256(body).hexdigest()
            downloaded_date = _date_for_path(path)
            expected.append((path, digest, len(body)))
            existing = store.get_download_image_by_hash(digest)
            if existing is not None:
                stat = storage.stat_image(existing["object_key"])
                _validate_object(stat, digest=digest, size=len(body))
                skipped += 1
                continue

            object_key = f"{downloaded_date}/{digest}{suffix}"
            storage.put_image(
                object_key,
                body,
                content_type,
                {"sha256": digest, "downloaded-date": downloaded_date},
            )
            stat = storage.stat_image(object_key)
            try:
                _validate_object(stat, digest=digest, size=len(body))
            except ValueError:
                storage.remove_image(object_key, missing_ok=True)
                raise
            record, created = store.record_download_image(
                sha256=digest,
                object_key=object_key,
                content_type=content_type,
                size_bytes=len(body),
                downloaded_date=downloaded_date,
            )
            if created:
                succeeded += 1
            else:
                if record["object_key"] != object_key:
                    storage.remove_image(object_key, missing_ok=True)
                skipped += 1
        except Exception as exc:
            failed += 1
            print(f"FAILED {path}: {exc}")

    verified = _verify_migration(expected, storage, store)
    if delete_source_after_verify:
        if failed or verified != len(paths):
            raise RuntimeError(
                f"migration verification failed; keeping source files "
                f"(total={len(paths)}, verified={verified}, failed={failed})"
            )
        for path, _, _ in expected:
            path.unlink(missing_ok=True)
        _remove_empty_directories(source)

    return {
        "total": len(paths),
        "succeeded": succeeded,
        "skipped": skipped,
        "failed": failed,
        "verified": verified,
    }


def _verify_migration(expected, storage, store) -> int:
    verified = 0
    for _, digest, size in expected:
        record = store.get_download_image_by_hash(digest)
        if record is None or int(record["size_bytes"]) != size:
            continue
        try:
            stat = storage.stat_image(record["object_key"])
        except Exception:
            continue
        try:
            _validate_object(stat, digest=digest, size=size)
        except ValueError:
            continue
        else:
            verified += 1
    return verified


def _validate_object(stat, *, digest: str, size: int) -> None:
    if int(getattr(stat, "size", -1)) != size:
        raise ValueError("MinIO object size mismatch")
    metadata = {
        str(key).casefold(): str(value).casefold()
        for key, value in (getattr(stat, "metadata", {}) or {}).items()
    }
    stored_hash = metadata.get("x-amz-meta-sha256") or metadata.get("sha256")
    if stored_hash != digest.casefold():
        raise ValueError("MinIO object sha256 metadata mismatch")


def _date_for_path(path: Path) -> str:
    if len(path.parent.name) == 8 and path.parent.name.isdigit():
        return path.parent.name
    return datetime.fromtimestamp(path.stat().st_mtime, _CHINA_TZ).strftime("%Y%m%d")


def _image_type(body: bytes) -> tuple[str, str] | None:
    if body.startswith(b"\xff\xd8\xff"):
        return ".jpg", "image/jpeg"
    if body.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png", "image/png"
    if body.startswith((b"GIF87a", b"GIF89a")):
        return ".gif", "image/gif"
    if len(body) >= 12 and body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return ".webp", "image/webp"
    return None


def _remove_empty_directories(root: Path) -> None:
    if not root.exists():
        return
    directories = (item for item in root.rglob("*") if item.is_dir())
    for path in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        path.rmdir()
    root.rmdir()


def main() -> None:
    args = parse_args()
    result = migrate(args.source, delete_source_after_verify=args.delete_source_after_verify)
    print(
        "migration complete: "
        + ", ".join(f"{key}={value}" for key, value in result.items())
    )


if __name__ == "__main__":
    main()
