from __future__ import annotations

import json

from fastapi.testclient import TestClient

from qq_personal_bot.runtime import reset_runtime
from qq_personal_bot.web import create_app

GIF_DATA_URL = (
    "data:image/gif;base64,"
    "R0lGODlhAQABAIAAAP///wAAACH5BAEAAAAALAAAAAABAAEAAAICRAEAOw=="
)


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
    assert "static/app.js" in response.text
    assert "luaImport" in response.text
    assert "luaCommandList" in response.text


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
