from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from qq_personal_bot.classic_storage import classic_image_type, get_classic_storage
from qq_personal_bot.runtime import get_store


_IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp"})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate local group classics to MinIO")
    parser.add_argument("--source", type=Path, default=Path("data/classics"))
    parser.add_argument("--delete-source-after-verify", action="store_true")
    return parser.parse_args()


def migrate(source: Path, *, delete_source_after_verify: bool) -> dict[str, int]:
    source = source.resolve(strict=False)
    paths = (
        sorted(
            path
            for path in source.rglob("*")
            if path.is_file() and path.suffix.casefold() in _IMAGE_SUFFIXES
        )
        if source.is_dir()
        else []
    )
    storage = get_classic_storage()
    store = get_store()
    succeeded = 0
    skipped = 0
    failed = 0
    expected: list[tuple[Path, int, str, int]] = []

    for path in paths:
        uploaded_object: tuple[int, str] | None = None
        try:
            group_id = _group_id_for_path(source, path)
            body = path.read_bytes()
            image_type = classic_image_type(body)
            if image_type is None:
                raise ValueError("unsupported image format")
            suffix, content_type = image_type
            digest = hashlib.sha256(body).hexdigest()
            expected.append((path, group_id, digest, len(body)))
            existing = store.get_classic_image_by_hash(group_id, digest)
            if existing is not None:
                stat = storage.stat_image(group_id, existing["object_key"])
                _validate_object(stat, digest=digest, size=len(body))
                skipped += 1
                continue

            object_key = f"{digest}{suffix}"
            storage.put_image(
                group_id,
                object_key,
                body,
                content_type,
                {"sha256": digest, "group-id": str(group_id)},
            )
            uploaded_object = (group_id, object_key)
            stat = storage.stat_image(group_id, object_key)
            try:
                _validate_object(stat, digest=digest, size=len(body))
            except ValueError:
                storage.remove_image(group_id, object_key, missing_ok=True)
                raise
            record, created = store.record_classic_image(
                group_id=group_id,
                sha256=digest,
                object_key=object_key,
                content_type=content_type,
                size_bytes=len(body),
                created_at=path.stat().st_mtime,
            )
            if created:
                succeeded += 1
            else:
                if record["object_key"] != object_key:
                    storage.remove_image(group_id, object_key, missing_ok=True)
                skipped += 1
            uploaded_object = None
        except Exception as exc:
            if uploaded_object is not None:
                try:
                    storage.remove_image(*uploaded_object, missing_ok=True)
                except Exception as cleanup_exc:
                    print(f"CLEANUP FAILED {path}: {cleanup_exc}")
            failed += 1
            print(f"FAILED {path}: {exc}")

    verified = _verify_migration(expected, storage, store)
    if delete_source_after_verify:
        if failed or verified != len(paths):
            raise RuntimeError(
                "migration verification failed; keeping source files "
                f"(total={len(paths)}, verified={verified}, failed={failed})"
            )
        for path, _, _, _ in expected:
            path.unlink(missing_ok=True)
        _remove_empty_directories(source)

    return {
        "total": len(paths),
        "succeeded": succeeded,
        "skipped": skipped,
        "failed": failed,
        "verified": verified,
        "groups": len({group_id for _, group_id, _, _ in expected}),
    }


def _verify_migration(expected, storage, store) -> int:
    verified = 0
    for _, group_id, digest, size in expected:
        record = store.get_classic_image_by_hash(group_id, digest)
        if record is None or int(record["size_bytes"]) != size:
            continue
        try:
            stat = storage.stat_image(group_id, record["object_key"])
            _validate_object(stat, digest=digest, size=size)
        except Exception:
            continue
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


def _group_id_for_path(root: Path, path: Path) -> int:
    relative = path.relative_to(root)
    if len(relative.parts) != 2 or not relative.parts[0].isdigit():
        raise ValueError("classic image must be stored under a numeric group directory")
    group_id = int(relative.parts[0])
    if group_id <= 0:
        raise ValueError("group_id must be positive")
    return group_id


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
