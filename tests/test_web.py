from __future__ import annotations

import base64
import hashlib
import io
import json

from fastapi.testclient import TestClient

from qq_personal_bot import web as web_module
from qq_personal_bot.runtime import get_store, reset_runtime
from qq_personal_bot.web import create_app

GIF_DATA_URL = (
    "data:image/gif;base64,"
    "R0lGODlhAQABAIAAAP///wAAACH5BAEAAAAALAAAAAABAAEAAAICRAEAOw=="
)


class _FakeMinioResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.closed = False

    def stream(self, chunk_size: int):
        stream = io.BytesIO(self.body)
        while chunk := stream.read(chunk_size):
            yield chunk

    def close(self) -> None:
        self.closed = True

    def release_conn(self) -> None:
        pass


class _FakeDownloadStorage:
    bucket = "qqbot-downloads"

    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects

    def ensure_available(self) -> None:
        pass

    def get_image(self, object_key: str):
        return _FakeMinioResponse(self.objects[object_key])

    def remove_image(self, object_key: str, *, missing_ok: bool = False) -> None:
        if missing_ok:
            self.objects.pop(object_key, None)
        else:
            del self.objects[object_key]


class _FakeClassicStorage:
    def __init__(self, objects: dict[tuple[int, str], bytes]) -> None:
        self.objects = objects

    def get_image(self, group_id: int, object_key: str):
        return _FakeMinioResponse(self.objects[(int(group_id), object_key)])

    def remove_image(self, group_id: int, object_key: str, *, missing_ok: bool = False):
        key = (int(group_id), object_key)
        if missing_ok:
            self.objects.pop(key, None)
        else:
            del self.objects[key]

    def remove_group_bucket(self, group_id: int, object_keys: list[str]):
        for object_key in object_keys:
            self.objects.pop((int(group_id), object_key), None)


def test_replies_api_roundtrip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("QQBOT_WEB_TOKEN", raising=False)
    reset_runtime()
    client = TestClient(create_app())

    payload = {
        "empty": "empty",
        "fallback": "fallback {message}",
        "rules": [{"type": "exact", "pattern": "menu", "reply": "menu reply"}],
        "direct_rules": [{"type": "contains", "pattern": "keyword", "reply": "direct reply"}],
    }
    response = client.post("/api/replies", json=payload)

    assert response.status_code == 200
    assert response.json()["valid"] is True
    assert json.loads((tmp_path / "replies.json").read_text(encoding="utf-8")) == payload

    response = client.get("/api/replies")

    assert response.status_code == 200
    assert response.json()["config"]["rules"][0]["reply"] == "menu reply"
    assert response.json()["config"]["direct_rules"][0]["reply"] == "direct reply"


def test_index_serves_static_frontend(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("QQBOT_WEB_TOKEN", raising=False)
    reset_runtime()
    client = TestClient(create_app())

    response = client.get("/")

    assert response.status_code == 200
    assert "QQ Bot" in response.text
    assert '<div id="root"></div>' in response.text
    assert "/qqbot/static/assets/index-" in response.text
    assert ".js" in response.text
    assert ".css" in response.text


def test_replies_api_still_accepts_raw_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("QQBOT_WEB_TOKEN", raising=False)
    reset_runtime()
    client = TestClient(create_app())

    payload = {"rules": [{"type": "exact", "pattern": "menu", "reply": "menu reply"}]}
    response = client.post("/api/replies", json={"raw": json.dumps(payload)})

    assert response.status_code == 200
    assert response.json()["config"]["rules"][0]["pattern"] == "menu"


def test_replies_api_rejects_invalid_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("QQBOT_WEB_TOKEN", raising=False)
    reset_runtime()
    client = TestClient(create_app())

    response = client.post("/api/replies", json={"raw": "{bad json"})

    assert response.status_code == 400
    assert "detail" in response.json()


def test_replies_api_rejects_invalid_rule(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("QQBOT_WEB_TOKEN", raising=False)
    reset_runtime()
    client = TestClient(create_app())

    response = client.post(
        "/api/replies",
        json={"raw": json.dumps({"rules": [{"type": "regex", "pattern": "[", "reply": "bad"}]})},
    )

    assert response.status_code == 400
    assert "detail" in response.json()


def test_prefixes_api_defaults_empty_to_tilde(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("QQBOT_WEB_TOKEN", raising=False)
    monkeypatch.setenv("QQBOT_DB_PATH", str(tmp_path / "policy.sqlite3"))
    reset_runtime()
    client = TestClient(create_app())

    response = client.post("/api/policy/prefixes", json={"prefixes": []})

    assert response.status_code == 200
    assert response.json()["trigger"]["prefixes"] == ["~"]


def test_direct_trigger_percent_api_roundtrip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("QQBOT_WEB_TOKEN", raising=False)
    monkeypatch.setenv("QQBOT_DB_PATH", str(tmp_path / "policy.sqlite3"))
    reset_runtime()
    client = TestClient(create_app())

    response = client.post("/api/policy/direct-trigger-percent", json={"percent": 35})

    assert response.status_code == 200
    assert response.json()["trigger"]["direct_trigger_percent"] == 35.0

    response = client.post("/api/policy/direct-trigger-percent", json={"percent": 101})

    assert response.status_code == 400
    assert "between 0 and 100" in response.json()["detail"]


def test_dsapi_config_api_roundtrip_and_clear_history(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("QQBOT_WEB_TOKEN", raising=False)
    monkeypatch.setenv("QQBOT_DB_PATH", str(tmp_path / "policy.sqlite3"))
    monkeypatch.setenv("QQBOT_DSAPI_API_KEY", "secret")
    reset_runtime()
    client = TestClient(create_app())

    response = client.post(
        "/api/dsapi",
        json={
            "enabled": False,
            "enabled_groups": [123, 456],
            "history_turns": 8,
            "random_reply_percent": 7.5,
            "random_sticker_percent": 35,
            "knowledge_enabled": True,
            "knowledge_prompt": "扮演档案管理员",
            "clear_history": False,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["api_configured"] is True
    assert data["enabled"] is False
    assert data["enabled_groups"] == [123, 456]
    assert data["history_turns"] == 8
    assert data["random_reply_percent"] == 7.5
    assert data["random_sticker_percent"] == 35
    assert data["knowledge_enabled"] is True
    assert data["knowledge_prompt"] == "扮演档案管理员"
    assert [item["key"] for item in data["model_options"]] == [
        "flash",
        "pro",
        "vision",
    ]
    assert data["model_options"][2]["id"] == "deepseek-v4-flash-vision-exp"

    invalid = client.post(
        "/api/dsapi",
        json={
            "enabled_groups": [123],
            "history_turns": 2,
            "random_reply_percent": 101,
            "knowledge_enabled": False,
            "knowledge_prompt": "",
            "clear_history": False,
        },
    )
    assert invalid.status_code == 400
    assert "between 0 and 100" in invalid.json()["detail"]

    response = client.delete("/api/dsapi/history")
    assert response.status_code == 200
    assert response.json()["deleted"] == 0


def test_dsapi_knowledge_base_api_crud_and_switch(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("QQBOT_WEB_TOKEN", raising=False)
    monkeypatch.setenv("QQBOT_DB_PATH", str(tmp_path / "policy.sqlite3"))
    reset_runtime()
    client = TestClient(create_app())

    first = client.post(
        "/api/dsapi/knowledge",
        json={"name": "果果", "prompt": "可爱又呆呆"},
    )
    second = client.post(
        "/api/dsapi/knowledge",
        json={
            "name": "档案员",
            "prompt": "初始档案",
            "model": "deepseek-reasoner",
            "thinking_enabled": True,
            "max_tokens": 512,
            "history_turns": 20,
            "temperature": 0.4,
        },
    )

    assert first.status_code == 200
    assert second.status_code == 200
    first_id = first.json()["knowledge_base"]["id"]
    second_id = second.json()["knowledge_base"]["id"]
    updated = client.put(
        f"/api/dsapi/knowledge/{second_id}",
        json={
            "name": "群档案员",
            "prompt": "只依据群档案回答",
            "model": "deepseek-reasoner-v2",
            "thinking_enabled": True,
            "max_tokens": 640,
            "history_turns": 18,
            "temperature": 0.2,
        },
    )
    activated = client.post(
        f"/api/dsapi/knowledge/{second_id}/activate",
        json={
            "clear_history": True,
            "name": "立即启用的档案员",
            "prompt": "一次请求保存并启用",
            "model": "deepseek-reasoner-v3",
            "thinking_enabled": False,
            "max_tokens": 720,
            "history_turns": 20,
            "temperature": 0.1,
        },
    )

    assert updated.status_code == 200
    assert updated.json()["knowledge_base"]["name"] == "群档案员"
    assert activated.status_code == 200
    assert activated.json()["active_knowledge_id"] == second_id
    assert activated.json()["knowledge_prompt"] == "一次请求保存并启用"
    assert activated.json()["active_knowledge_name"] == "立即启用的档案员"
    assert activated.json()["model"] == "deepseek-reasoner-v3"
    assert activated.json()["thinking_enabled"] is False
    assert activated.json()["max_tokens"] == 720
    assert activated.json()["history_turns"] == 20
    assert activated.json()["active_knowledge"]["history_turns"] == 20
    assert activated.json()["temperature"] == 0.1

    deleted = client.delete(f"/api/dsapi/knowledge/{second_id}")
    assert deleted.status_code == 200
    assert deleted.json()["active_knowledge_id"] == first_id
    assert len(deleted.json()["knowledge_bases"]) == 1

    missing = client.post(
        "/api/dsapi/knowledge/999999/activate",
        json={"clear_history": False},
    )
    assert missing.status_code == 400
    assert "not found" in missing.json()["detail"]


def test_sticker_api_uploads_serves_and_deletes_images(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("QQBOT_WEB_TOKEN", raising=False)
    monkeypatch.setenv("QQBOT_DB_PATH", str(tmp_path / "policy.sqlite3"))
    monkeypatch.setenv("QQBOT_STICKER_DIR", str(tmp_path / "stickers"))
    reset_runtime()
    client = TestClient(create_app())

    created = client.post(
        "/api/stickers",
        json={"filename": "开心.gif", "image_data_url": GIF_DATA_URL},
    )

    assert created.status_code == 200
    sticker = created.json()
    assert sticker["filename"].endswith(".gif")
    listing = client.get("/api/stickers")
    assert listing.status_code == 200
    assert listing.json()["stickers"][0]["filename"] == sticker["filename"]
    image = client.get(sticker["image_url"].removeprefix("."))
    assert image.status_code == 200
    assert image.content.startswith(b"GIF89a")

    deleted = client.delete(f"/api/stickers/{sticker['filename']}")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert client.get("/api/stickers").json()["stickers"] == []


def test_core_policy_api_updates_full_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("QQBOT_WEB_TOKEN", raising=False)
    monkeypatch.setenv("QQBOT_DB_PATH", str(tmp_path / "policy.sqlite3"))
    reset_runtime()
    client = TestClient(create_app())

    response = client.post(
        "/api/policy/core",
        json={
            "mode": "blocklist",
            "enabled_groups": [123, 456],
            "blocked_groups": [789],
            "admins": [20000],
            "trigger": {
                "mention": False,
                "prefixes": ["!", "/"],
                "direct_trigger_percent": 42,
            },
            "limits": {
                "per_group_seconds": 2.5,
                "per_user_per_minute": 8,
            },
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["mode"] == "blocklist"
    assert data["enabled_groups"] == [123, 456]
    assert data["blocked_groups"] == [789]
    assert data["admins"] == [20000]
    assert data["trigger"]["mention"] is False
    assert data["trigger"]["prefixes"] == ["!", "/"]
    assert data["trigger"]["direct_trigger_percent"] == 42.0
    assert data["limits"] == {"per_group_seconds": 2.5, "per_user_per_minute": 8}


def test_web_can_remove_bootstrap_admin_permanently(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("QQBOT_WEB_TOKEN", raising=False)
    monkeypatch.setenv("QQBOT_DB_PATH", str(tmp_path / "policy.sqlite3"))
    monkeypatch.setenv("QQBOT_ADMINS", "10000")
    reset_runtime()
    client = TestClient(create_app())
    policy = client.get("/api/policy").json()

    policy["admins"] = []
    response = client.post("/api/policy/core", json=policy)

    assert response.status_code == 200
    assert response.json()["admins"] == []
    reset_runtime()
    restarted_client = TestClient(create_app())
    assert restarted_client.get("/api/policy").json()["admins"] == []


def test_lua_api_returns_example_when_script_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("QQBOT_WEB_TOKEN", raising=False)
    monkeypatch.setenv("QQBOT_LUA_SCRIPT", str(tmp_path / "scripts" / "main.lua"))
    reset_runtime()
    client = TestClient(create_app())

    response = client.get("/api/lua")

    assert response.status_code == 200
    assert response.json()["using_example"] is True
    assert "function on_message" in response.json()["content"]


def test_lua_api_saves_valid_script(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("QQBOT_WEB_TOKEN", raising=False)
    script_path = tmp_path / "scripts" / "main.lua"
    monkeypatch.setenv("QQBOT_LUA_SCRIPT", str(script_path))
    reset_runtime()
    client = TestClient(create_app())

    content = 'function on_message(event, api)\n  return "ok"\nend\n'
    response = client.post("/api/lua", json={"content": content})

    assert response.status_code == 200
    assert script_path.read_text(encoding="utf-8") == content
    assert response.json()["using_example"] is False


def test_lua_api_rejects_invalid_script(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("QQBOT_WEB_TOKEN", raising=False)
    monkeypatch.setenv("QQBOT_LUA_SCRIPT", str(tmp_path / "scripts" / "main.lua"))
    reset_runtime()
    client = TestClient(create_app())

    response = client.post("/api/lua", json={"content": "function nope() end"})

    assert response.status_code == 400
    assert "on_message" in response.json()["detail"]


def test_lua_commands_api_lists_saves_reads_and_deletes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("QQBOT_WEB_TOKEN", raising=False)
    lua_dir = tmp_path / "scripts" / "lua"
    monkeypatch.setenv("QQBOT_LUA_DIR", str(lua_dir))
    reset_runtime()
    client = TestClient(create_app())

    content = 'function on_command(event, api)\n  return event.command\nend\n'
    response = client.post("/api/lua/commands/hello", json={"content": content})

    assert response.status_code == 200
    assert response.json()["command"] == "hello"
    assert response.json()["using_example"] is False
    assert (lua_dir / "hello.lua").read_text(encoding="utf-8") == content

    response = client.get("/api/lua/commands")

    assert response.status_code == 200
    assert response.json()["commands"][0]["command"] == "hello"

    response = client.get("/api/lua/commands/hello")

    assert response.status_code == 200
    assert response.json()["content"] == content

    response = client.delete("/api/lua/commands/hello")

    assert response.status_code == 200
    assert response.json()["deleted"] is True
    assert not (lua_dir / "hello.lua").exists()


def test_lua_command_api_returns_example_when_script_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("QQBOT_WEB_TOKEN", raising=False)
    monkeypatch.setenv("QQBOT_LUA_DIR", str(tmp_path / "scripts" / "lua"))
    reset_runtime()
    client = TestClient(create_app())

    response = client.get("/api/lua/commands/抽群老婆")

    assert response.status_code == 200
    assert response.json()["exists"] is False
    assert response.json()["using_example"] is True
    assert "function on_command" in response.json()["content"]


def test_lua_command_api_rejects_invalid_command(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("QQBOT_WEB_TOKEN", raising=False)
    monkeypatch.setenv("QQBOT_LUA_DIR", str(tmp_path / "scripts" / "lua"))
    reset_runtime()
    client = TestClient(create_app())

    response = client.post(
        "/api/lua/commands/bad.name",
        json={"content": 'function on_command(event, api)\n  return "ok"\nend\n'},
    )

    assert response.status_code == 400
    assert "command" in response.json()["detail"]


def test_lua_command_api_rejects_invalid_script(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("QQBOT_WEB_TOKEN", raising=False)
    monkeypatch.setenv("QQBOT_LUA_DIR", str(tmp_path / "scripts" / "lua"))
    reset_runtime()
    client = TestClient(create_app())

    response = client.post("/api/lua/commands/hello", json={"content": "function nope() end"})

    assert response.status_code == 400
    assert "on_command" in response.json()["detail"]


def test_menu_api_creates_lists_updates_deletes_and_prunes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("QQBOT_WEB_TOKEN", raising=False)
    monkeypatch.setenv("QQBOT_DB_PATH", str(tmp_path / "policy.sqlite3"))
    monkeypatch.setenv("QQBOT_MENU_IMAGE_DIR", str(tmp_path / "menu_images"))
    reset_runtime()
    client = TestClient(create_app())

    response = client.post(
        "/api/menus",
        json={"title": "群友菜单", "image_data_url": GIF_DATA_URL, "enabled": True},
    )

    assert response.status_code == 200
    menu_id = response.json()["id"]
    assert response.json()["title"] == "群友菜单"
    assert response.json()["image_url"]

    response = client.get("/api/menus")

    assert response.status_code == 200
    assert response.json()["menus"][0]["title"] == "群友菜单"

    response = client.put(
        f"/api/menus/{menu_id}",
        json={"title": "群友菜单2", "enabled": False},
    )

    assert response.status_code == 200
    assert response.json()["title"] == "群友菜单2"
    assert response.json()["enabled"] is False

    response = client.post("/api/menus/prune-howtocook-without-images")

    assert response.status_code == 200
    assert "deleted" in response.json()

    response = client.delete(f"/api/menus/{menu_id}")

    assert response.status_code == 200
    assert response.json()["deleted"] is True


def test_restaurant_api_roundtrip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("QQBOT_WEB_TOKEN", raising=False)
    monkeypatch.setenv("QQBOT_DB_PATH", str(tmp_path / "policy.sqlite3"))
    reset_runtime()
    client = TestClient(create_app())

    response = client.post(
        "/api/restaurants",
        json={"name": "楼下小馆", "dishes": ["红烧肉"], "group_id": 123, "enabled": True},
    )

    assert response.status_code == 200
    restaurant_id = response.json()["id"]
    assert response.json()["name"] == "楼下小馆"

    response = client.put(
        f"/api/restaurants/{restaurant_id}",
        json={"name": "楼下小馆", "dishes": ["干锅牛蛙", "炒饭"], "group_id": 123, "enabled": False},
    )

    assert response.status_code == 200
    assert response.json()["dishes"] == ["干锅牛蛙", "炒饭"]
    assert response.json()["enabled"] is False

    response = client.get("/api/restaurants?group_id=123")

    assert response.status_code == 200
    assert response.json()["restaurants"][0]["name"] == "楼下小馆"

    response = client.delete(f"/api/restaurants/{restaurant_id}")

    assert response.status_code == 200
    assert response.json()["deleted"] is True


def test_menu_and_restaurant_write_apis_require_token_when_configured(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("QQBOT_WEB_TOKEN", "secret")
    monkeypatch.setenv("QQBOT_DB_PATH", str(tmp_path / "policy.sqlite3"))
    reset_runtime()
    client = TestClient(create_app())

    menu_response = client.post(
        "/api/menus",
        json={"title": "群友菜单", "image_data_url": GIF_DATA_URL, "enabled": True},
    )
    restaurant_response = client.post(
        "/api/restaurants",
        json={"name": "楼下小馆", "dishes": ["红烧肉"], "group_id": 123},
    )

    assert menu_response.status_code == 401
    assert restaurant_response.status_code == 401


def test_web_login_session_when_token_configured(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("QQBOT_WEB_TOKEN", "secret")
    monkeypatch.setenv("QQBOT_DB_PATH", str(tmp_path / "policy.sqlite3"))
    reset_runtime()
    client = TestClient(create_app())

    index_response = client.get("/", follow_redirects=False)
    api_response = client.get("/api/policy")
    bad_login = client.post("/login", data={"password": "bad"})

    assert index_response.status_code == 303
    assert index_response.headers["location"] == "./login"
    assert api_response.status_code == 401
    assert bad_login.status_code == 401

    login_response = client.post("/login", data={"password": "secret"}, follow_redirects=False)

    assert login_response.status_code == 303
    assert "qqbot_admin_session" in login_response.headers["set-cookie"]

    response = client.get("/api/policy")

    assert response.status_code == 200

    write_response = client.post("/api/policy/prefixes", json={"prefixes": ["~"]})

    assert write_response.status_code == 200

    logout_response = client.post("/logout", follow_redirects=False)

    assert logout_response.status_code == 303
    assert client.get("/api/policy").status_code == 401


def test_classics_api_lists_groups_reads_group_and_serves_images(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("QQBOT_WEB_TOKEN", raising=False)
    monkeypatch.setenv("QQBOT_DB_PATH", str(tmp_path / "policy.sqlite3"))
    reset_runtime()
    store = get_store()
    image_body = base64.b64decode(GIF_DATA_URL.split(",", 1)[1])
    digest = hashlib.sha256(image_body).hexdigest()
    object_key = f"{digest}.gif"
    store.record_classic_image(
        group_id=123,
        sha256=digest,
        object_key=object_key,
        content_type="image/gif",
        size_bytes=len(image_body),
    )
    storage = _FakeClassicStorage({(123, object_key): image_body})
    monkeypatch.setattr(web_module, "get_classic_storage", lambda: storage)
    client = TestClient(create_app())

    response = client.get("/api/classics/groups")

    assert response.status_code == 200
    data = response.json()
    assert data["groups"][0]["group_id"] == 123
    assert data["groups"][0]["count"] == 1
    assert data["groups"][0]["cover_url"].endswith(
        f"/api/classic-images/123/{object_key}"
    )

    response = client.get("/api/classics/groups/123")

    assert response.status_code == 200
    detail = response.json()
    assert detail["group_id"] == 123
    assert detail["count"] == 1
    assert detail["images"][0]["filename"] == object_key

    response = client.get(f"/api/classic-images/123/{object_key}")

    assert response.status_code == 200
    assert response.content == image_body
    assert response.headers["etag"] == f'"{digest}"'


def test_classics_api_deletes_single_image_and_whole_group(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("QQBOT_WEB_TOKEN", raising=False)
    monkeypatch.setenv("QQBOT_DB_PATH", str(tmp_path / "policy.sqlite3"))
    reset_runtime()
    store = get_store()
    base_body = base64.b64decode(GIF_DATA_URL.split(",", 1)[1])
    first_body = base_body + b"first"
    second_body = base_body + b"second"
    first_digest = hashlib.sha256(first_body).hexdigest()
    second_digest = hashlib.sha256(second_body).hexdigest()
    first_key = f"{first_digest}.gif"
    second_key = f"{second_digest}.gif"
    first, _ = store.record_classic_image(
        group_id=123,
        sha256=first_digest,
        object_key=first_key,
        content_type="image/gif",
        size_bytes=len(first_body),
        created_at=1,
    )
    store.record_classic_image(
        group_id=123,
        sha256=second_digest,
        object_key=second_key,
        content_type="image/gif",
        size_bytes=len(second_body),
        created_at=2,
    )
    storage = _FakeClassicStorage(
        {(123, first_key): first_body, (123, second_key): second_body}
    )
    monkeypatch.setattr(web_module, "get_classic_storage", lambda: storage)
    client = TestClient(create_app())

    response = client.delete(f"/api/classics/groups/123/images/{first_key}")

    assert response.status_code == 200
    assert response.json()["deleted"] is True
    assert response.json()["group"]["count"] == 1
    assert (123, first_key) not in storage.objects
    assert (123, second_key) in storage.objects
    assert store.get_classic_image(first["id"]) is None

    response = client.get("/api/classics/groups/123")

    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert response.json()["images"][0]["filename"] == second_key

    response = client.delete("/api/classics/groups/123")

    assert response.status_code == 200
    assert response.json()["deleted"] is True
    assert response.json()["deleted_count"] == 1
    assert storage.objects == {}

    response = client.get("/api/classics/groups")

    assert response.status_code == 200
    assert response.json()["groups"] == []


def test_classics_api_returns_empty_groups_when_directory_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("QQBOT_WEB_TOKEN", raising=False)
    monkeypatch.setenv("QQBOT_DB_PATH", str(tmp_path / "policy.sqlite3"))
    reset_runtime()
    client = TestClient(create_app())

    response = client.get("/api/classics/groups")

    assert response.status_code == 200
    assert response.json()["groups"] == []


def test_download_gallery_lists_streams_and_deletes_images(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("QQBOT_WEB_TOKEN", raising=False)
    monkeypatch.setenv("QQBOT_DB_PATH", str(tmp_path / "policy.sqlite3"))
    reset_runtime()
    store = get_store()
    body = b"GIF89a-gallery"
    digest = "c" * 64
    object_key = f"20260817/{digest}.gif"
    record, _ = store.record_download_image(
        sha256=digest,
        object_key=object_key,
        content_type="image/gif",
        size_bytes=len(body),
        downloaded_date="20260817",
    )
    storage = _FakeDownloadStorage({object_key: body})
    monkeypatch.setattr(web_module, "get_download_storage", lambda: storage)
    client = TestClient(create_app())

    overview = client.get("/api/download-images/overview")
    listing = client.get("/api/download-images?date=20260817&offset=0&limit=60")
    content = client.get(f"/api/download-images/{record['id']}/content")

    assert overview.status_code == 200
    assert overview.json()["total"] == 1
    assert overview.json()["storage_available"] is True
    assert listing.status_code == 200
    assert listing.json()["images"][0]["image_url"].endswith(
        f"/api/download-images/{record['id']}/content"
    )
    assert content.status_code == 200
    assert content.content == body
    assert content.headers["etag"] == f'"{digest}"'
    assert content.headers["cache-control"] == "private, max-age=86400"

    deleted = client.delete(f"/api/download-images/{record['id']}")

    assert deleted.status_code == 200
    assert object_key not in storage.objects
    assert store.get_download_image(record["id"]) is None


def test_download_gallery_rejects_invalid_date(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("QQBOT_WEB_TOKEN", raising=False)
    monkeypatch.setenv("QQBOT_DB_PATH", str(tmp_path / "policy.sqlite3"))
    reset_runtime()

    response = TestClient(create_app()).get("/api/download-images?date=2026-08-17")

    assert response.status_code == 400
