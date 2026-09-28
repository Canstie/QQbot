from __future__ import annotations

import hashlib
import threading
from typing import Any

from nonebot import logger

from qq_personal_bot.classic_storage import ClassicStorageError, get_classic_storage
from qq_personal_bot.runtime import get_store

# The bot and admin API share one process. This keeps a save or delete from crossing
# the final database switch while MinIO objects are copied.
classic_lock = threading.RLock()


def _verified_image(storage: Any, group_id: int, image: dict[str, Any]) -> bytes:
    body = storage.read_image(group_id, image["object_key"])
    if len(body) != int(image["size_bytes"]) or hashlib.sha256(body).hexdigest() != image["sha256"]:
        raise ClassicStorageError(f"群 {group_id} 的典图 {image['object_key']} 校验失败")
    return body


def _copy_image(storage: Any, target: int, image: dict[str, Any], body: bytes) -> None:
    storage.put_image(
        target,
        image["object_key"],
        body,
        image["content_type"],
        {"sha256": image["sha256"], "group-id": str(target)},
    )
    _verified_image(storage, target, image)


def bind_classic_group(master_group_id: int, group_id: int) -> dict[str, Any]:
    master, member = int(master_group_id), int(group_id)
    if master <= 0 or member <= 0 or master == member:
        raise ValueError("主群和副群必须是不同的正整数群号")
    with classic_lock:
        from qq_personal_bot.classic_forward import _active_groups

        if member in _active_groups:
            raise RuntimeError("副群正在发送全部典图，请发送完成后重试绑定")
        store = get_store()
        families = store.list_classic_bindings()
        if store.resolve_classic_group(master) != master or any(
            member == family["master_group_id"] or member in family["group_ids"]
            for family in families
        ):
            raise ValueError("主群或副群已绑定其他典藏")
        source = store.list_classic_images(member, limit=None)
        existing = {
            image["sha256"]: image
            for image in store.list_classic_images(master, limit=None)
        }
        storage = get_classic_storage() if source else None
        copied: list[str] = []
        try:
            for image in source:
                body = _verified_image(storage, member, image)
                target_image = existing.get(image["sha256"])
                if target_image is None:
                    _copy_image(storage, master, image, body)
                    copied.append(image["object_key"])
                else:
                    try:
                        _verified_image(storage, master, target_image)
                    except ClassicStorageError:
                        _copy_image(storage, master, target_image, body)
            merged = store.bind_classic_group(master, member, source)
        except BaseException:
            if storage is not None:
                for object_key in copied:
                    if store.get_classic_image_by_key(master, object_key) is None:
                        try:
                            storage.remove_image(master, object_key, missing_ok=True)
                        except ClassicStorageError:
                            logger.exception("Classic bind cleanup failed: %s/%s", master, object_key)
            raise
        cleanup_pending = False
        if source:
            try:
                storage.remove_group_bucket(member, [image["object_key"] for image in source])
            except ClassicStorageError:
                cleanup_pending = True
                logger.exception("Classic old group bucket cleanup failed: %s", member)
        return {
            "master_group_id": master,
            "group_id": member,
            "merged_count": merged,
            "duplicate_count": len(source) - merged,
            "cleanup_pending": cleanup_pending,
        }


def dissolve_classic_group(master_group_id: int) -> dict[str, Any]:
    master = int(master_group_id)
    if master <= 0:
        raise ValueError("主群号必须是正整数")
    with classic_lock:
        store = get_store()
        family = next(
            (item for item in store.list_classic_bindings() if item["master_group_id"] == master),
            None,
        )
        if family is None:
            raise ValueError("这个主群没有绑定的副群")
        source = store.list_classic_images(master, limit=None)
        if source:
            storage = get_classic_storage()
            for image in source:
                body = _verified_image(storage, master, image)
                for member in family["group_ids"]:
                    _copy_image(storage, member, image, body)
        members = store.dissolve_classic_group(master, source)
        return {
            "master_group_id": master,
            "group_ids": members,
            "images_per_group": len(source),
            "copied_count": len(source) * len(members),
        }
