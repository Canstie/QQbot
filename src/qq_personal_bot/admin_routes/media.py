import asyncio
import hashlib
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from fastapi import HTTPException, Request
from fastapi.responses import (
    StreamingResponse,
)
from pydantic import BaseModel
from starlette.background import BackgroundTask

from qq_personal_bot.classic_sharing import (
    bind_classic_group,
    classic_lock,
    dissolve_classic_group,
)
from qq_personal_bot.classic_storage import ClassicStorageError, get_classic_storage
from qq_personal_bot.download_storage import DownloadStorageError, get_download_storage
from qq_personal_bot.runtime import get_store


class ClassicBindingPayload(BaseModel):
    master_group_id: int
    group_id: int


def _list_classic_groups(search: str = "", limit: int = 200) -> list[dict[str, Any]]:
    store = get_store()
    normalized_search = str(search).strip()
    groups = {
        group["group_id"]: group
        for group in store.list_classic_groups(search=normalized_search, limit=500)
    }
    owners: dict[int, int] = {}
    for family in store.list_classic_bindings():
        master = family["master_group_id"]
        master_group = store.get_classic_group_stat(master) or {
            "group_id": master,
            "count": 0,
            "total_bytes": 0,
            "updated_at": None,
            "cover_id": None,
        }
        if not normalized_search or normalized_search in str(master):
            groups[master] = master_group
        for member in family["group_ids"]:
            if not normalized_search or normalized_search in str(member):
                owners[member] = master
                groups[member] = {**master_group, "group_id": member}
    selected = list(groups.values())
    selected.sort(key=lambda group: (group["updated_at"] or 0, group["group_id"]), reverse=True)
    return [
        {
            **group,
            "owner_group_id": owners.get(group["group_id"], group["group_id"]),
            "updated_at": (
                datetime.fromtimestamp(group["updated_at"], UTC).isoformat()
                if group["updated_at"] is not None
                else None
            ),
            "cover_url": (
                f"./api/classic-images/{owners.get(group['group_id'], group['group_id'])}/"
                f"{quote(_classic_cover_key(group['cover_id']))}"
                if group["cover_id"] is not None
                else None
            ),
        }
        for group in selected[: max(1, min(int(limit), 500))]
    ]


def _classic_group_detail(group_id: int, limit: int = 500) -> dict[str, Any]:
    group_id = _validate_group_id(group_id)
    store = get_store()
    owner_group_id = store.resolve_classic_group(group_id)
    all_images = store.list_classic_images(owner_group_id, limit=None)
    images = all_images[: max(1, min(int(limit), 1000))]

    return {
        "group_id": group_id,
        "owner_group_id": owner_group_id,
        "exists": bool(all_images),
        "count": len(all_images),
        "images": [_classic_image_for_api(image) for image in images],
    }


def _classic_cover_key(image_id: int) -> str:
    record = get_store().get_classic_image(image_id)
    return record["object_key"] if record is not None else "missing"


def _classic_image_for_api(record: dict[str, Any]) -> dict[str, Any]:
    group_id = int(record["group_id"])
    object_key = str(record["object_key"])
    return {
        "id": int(record["id"]),
        "filename": object_key,
        "relpath": f"{group_id}/{object_key}",
        "image_url": f"./api/classic-images/{group_id}/{quote(object_key)}",
        "size": int(record["size_bytes"]),
        "sha256": str(record["sha256"]),
        "modified_at": datetime.fromtimestamp(record["created_at"], UTC).isoformat(),
    }


async def _delete_classic_group(group_id: int) -> dict[str, Any]:
    return await asyncio.to_thread(_delete_classic_group_sync, group_id)


def _delete_classic_group_sync(group_id: int) -> dict[str, Any]:
    group_id = _validate_group_id(group_id)
    with classic_lock:
        store = get_store()
        if store.resolve_classic_group(group_id) != group_id or any(
            item["master_group_id"] == group_id for item in store.list_classic_bindings()
        ):
            raise HTTPException(status_code=409, detail="共享典藏请先解散，再删除整群典图")
        images = store.list_classic_images(group_id, limit=None)
        if not images:
            return {"deleted": False, "group_id": group_id, "deleted_count": 0}
        try:
            get_classic_storage().remove_group_bucket(
                group_id, [image["object_key"] for image in images]
            )
        except ClassicStorageError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        deleted_count = store.delete_classic_group(group_id)
        return {"deleted": True, "group_id": group_id, "deleted_count": deleted_count}


async def _delete_classic_image(group_id: int, filename: str) -> dict[str, Any]:
    return await asyncio.to_thread(_delete_classic_image_sync, group_id, filename)


def _delete_classic_image_sync(group_id: int, filename: str) -> dict[str, Any]:
    group_id = _validate_group_id(group_id)
    with classic_lock:
        store = get_store()
        owner_group_id = store.resolve_classic_group(group_id)
        record = store.get_classic_image_by_key(owner_group_id, filename)
        if record is None:
            raise HTTPException(status_code=404, detail="classic image not found")
        try:
            get_classic_storage().remove_image(
                owner_group_id, record["object_key"], missing_ok=True
            )
        except ClassicStorageError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        store.delete_classic_image(record["id"])
        return {
            "deleted": True,
            "group_id": group_id,
            "filename": record["object_key"],
            "group": _classic_group_detail(group_id),
        }


def _validate_group_id(group_id: int) -> int:
    group_id = int(group_id)
    if group_id <= 0:
        raise HTTPException(status_code=400, detail="group_id must be positive")
    return group_id


def register_routes(app, require_token):
    @app.get("/api/classics/groups")
    async def get_classic_groups(search: str = "", limit: int = 200) -> dict:
        return {
            "groups": _list_classic_groups(search=search, limit=limit),
            "storage": "minio",
        }

    @app.get("/api/classics/bindings")
    async def get_classic_bindings() -> dict:
        return {"bindings": get_store().list_classic_bindings()}

    @app.post("/api/classics/bindings")
    async def create_classic_binding(payload: ClassicBindingPayload, request: Request) -> dict:
        require_token(request)
        try:
            return await asyncio.to_thread(
                bind_classic_group, payload.master_group_id, payload.group_id
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ClassicStorageError, RuntimeError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.delete("/api/classics/bindings/{master_group_id}")
    async def delete_classic_binding(master_group_id: int, request: Request) -> dict:
        require_token(request)
        try:
            return await asyncio.to_thread(dissolve_classic_group, master_group_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ClassicStorageError, RuntimeError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/api/classics/groups/{group_id}")
    async def get_classic_group(group_id: int, limit: int = 500) -> dict:
        return _classic_group_detail(group_id, limit=limit)

    @app.delete("/api/classics/groups/{group_id}")
    async def delete_classic_group(group_id: int, request: Request) -> dict:
        require_token(request)
        return await _delete_classic_group(group_id)

    @app.delete("/api/classics/groups/{group_id}/images/{filename:path}")
    async def delete_classic_group_image(group_id: int, filename: str, request: Request) -> dict:
        require_token(request)
        return await _delete_classic_image(group_id, filename)

    @app.get("/api/classic-images/{group_id}/{filename}")
    async def get_classic_image(group_id: int, filename: str) -> StreamingResponse:
        record = get_store().get_classic_image_by_key(_validate_group_id(group_id), filename)
        if record is None:
            raise HTTPException(status_code=404, detail="classic image not found")
        try:
            response = await asyncio.to_thread(
                get_classic_storage().get_image,
                record["group_id"],
                record["object_key"],
            )
        except ClassicStorageError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        def close_response() -> None:
            response.close()
            response.release_conn()

        return StreamingResponse(
            response.stream(64 * 1024),
            media_type=record["content_type"],
            headers={
                "Cache-Control": "private, max-age=86400",
                "ETag": f'"{record["sha256"]}"',
                "Content-Length": str(record["size_bytes"]),
            },
            background=BackgroundTask(close_response),
        )

    @app.get("/api/download-images/overview")
    async def get_download_images_overview() -> dict:
        overview = get_store().download_image_overview()
        try:
            await asyncio.to_thread(get_download_storage().ensure_available)
        except DownloadStorageError:
            overview["storage_available"] = False
        else:
            overview["storage_available"] = True
        return overview

    @app.get("/api/download-images")
    async def get_download_images(
        date: str | None = None,
        offset: int = 0,
        limit: int = 60,
    ) -> dict:
        try:
            result = get_store().list_download_images(
                downloaded_date=date,
                offset=offset,
                limit=limit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        result["images"] = [
            {
                **item,
                "created_at": datetime.fromtimestamp(item["created_at"], UTC).isoformat(),
                "image_url": f"./api/download-images/{item['id']}/content",
            }
            for item in result["images"]
        ]
        return result

    @app.post("/api/download-images/match")
    async def match_download_image(request: Request) -> dict:
        require_token(request)
        digest = hashlib.sha256()
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > 50 * 1024 * 1024:
                raise HTTPException(status_code=413, detail="图片不能超过 50 MB")
            digest.update(chunk)
        if size == 0:
            raise HTTPException(status_code=400, detail="请选择图片")

        record = get_store().get_download_image_by_hash(digest.hexdigest())
        if record is None:
            return {"sha256": digest.hexdigest(), "matched": False, "image": None}
        try:
            exists = await asyncio.to_thread(
                get_download_storage().image_exists, record["object_key"]
            )
        except DownloadStorageError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if not exists:
            return {"sha256": digest.hexdigest(), "matched": False, "image": None}
        return {
            "sha256": digest.hexdigest(),
            "matched": True,
            "image": {
                **record,
                "created_at": datetime.fromtimestamp(record["created_at"], UTC).isoformat(),
                "image_url": f"./api/download-images/{record['id']}/content",
            },
        }

    @app.get("/api/download-images/{image_id}/content")
    async def get_download_image_content(image_id: int) -> StreamingResponse:
        record = get_store().get_download_image(image_id)
        if record is None:
            raise HTTPException(status_code=404, detail="download image not found")
        try:
            response = await asyncio.to_thread(
                get_download_storage().get_image,
                record["object_key"],
            )
        except DownloadStorageError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        def close_response() -> None:
            response.close()
            response.release_conn()

        return StreamingResponse(
            response.stream(64 * 1024),
            media_type=record["content_type"],
            headers={
                "Cache-Control": "private, max-age=86400",
                "ETag": f'"{record["sha256"]}"',
                "Content-Length": str(record["size_bytes"]),
            },
            background=BackgroundTask(close_response),
        )

    @app.delete("/api/download-images/{image_id}")
    async def delete_download_image(image_id: int, request: Request) -> dict:
        require_token(request)
        record = get_store().get_download_image(image_id)
        if record is None:
            raise HTTPException(status_code=404, detail="download image not found")
        try:
            await asyncio.to_thread(
                get_download_storage().remove_image,
                record["object_key"],
                missing_ok=True,
            )
        except DownloadStorageError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        get_store().delete_download_image(image_id)
        return {"deleted": True, "id": image_id, "object_key": record["object_key"]}
