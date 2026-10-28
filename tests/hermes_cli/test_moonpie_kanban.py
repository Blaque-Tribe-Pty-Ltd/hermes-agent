"""MoonPie device access stays on the canonical Kanban board and event log."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from hermes_state import SessionDB
from hermes_cli.web_routers import moonpie as moonpie_router


@pytest.fixture
def kanban_env(tmp_path, monkeypatch):
    home = tmp_path / "hermes_home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    for name in ("HERMES_KANBAN_DB", "HERMES_KANBAN_WORKSPACES_ROOT", "HERMES_KANBAN_HOME", "HERMES_KANBAN_BOARD"):
        monkeypatch.delenv(name, raising=False)

    from hermes_cli import kanban_db
    from hermes_cli import kanban_db_connect
    import hermes_constants
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(hermes_constants, "_default_hermes_root_memo", None)
    kanban_db._INITIALIZED_PATHS.clear()
    kanban_db.init_db()
    yield kanban_db, kanban_db_connect
    kanban_db._INITIALIZED_PATHS.clear()


@pytest.fixture
def moonpie_client(tmp_path, monkeypatch, kanban_env):
    db = SessionDB(db_path=tmp_path / "moonpie_state.db")
    original_db = moonpie_router._moonpie_db
    moonpie_router._moonpie_db = db
    app = FastAPI()
    app.include_router(moonpie_router.router)
    try:
        with TestClient(app) as client:
            yield client, db
    finally:
        moonpie_router._moonpie_db = original_db


@pytest.fixture
def token(moonpie_client):
    _, db = moonpie_client
    device_id = "moonpie-kanban-test-device"
    token_value = "mpdt-kanban-test-token"
    db.register_moonpie_device(device_id, name="Test", public_key="pk", pairing_code="PC")
    db.confirm_moonpie_device(device_id)
    db.store_moonpie_device_token(token_value, device_id)
    return token_value


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_device_authenticated_api_reaches_canonical_kanban_board(moonpie_client, token, kanban_env):
    client, _ = moonpie_client
    kanban_db, kanban_db_connect = kanban_env
    from hermes_cli.dashboard_auth.middleware import _path_is_public
    from hermes_cli.web_server import _is_handler_authenticated_api_path

    route = "/api/moonpie/kanban/board"
    assert _path_is_public(route)
    assert _is_handler_authenticated_api_path(route)
    assert client.get(route).status_code == 401
    assert client.get(route, headers=_auth("invalid")).status_code == 401

    created = client.post(
        "/api/moonpie/kanban/tasks",
        headers=_auth(token),
        json={"title": "Native task", "body": "Created from MoonPie", "idempotency_key": "native-task-1"},
    )
    assert created.status_code == 201, created.text
    task = created.json()["task"]
    task_id = task["id"]
    assert task["title"] == "Native task"

    # Creation is performed by the existing database layer and recorded with the
    # authenticated MoonPie device as actor.
    conn = kanban_db_connect.connect()
    try:
        stored = kanban_db.get_task(conn, task_id)
        assert stored.created_by == "moonpie:moonpie-kanban-test-device"
    finally:
        conn.close()

    duplicate = client.post(
        "/api/moonpie/kanban/tasks",
        headers=_auth(token),
        json={"title": "Duplicate should reuse", "idempotency_key": "native-task-1"},
    )
    assert duplicate.status_code == 201, duplicate.text
    assert duplicate.json()["task"]["id"] == task_id

    board = client.get("/api/moonpie/kanban/board", headers=_auth(token))
    assert board.status_code == 200, board.text
    matching = [item for column in board.json()["columns"] for item in column["tasks"] if item["id"] == task_id]
    assert len(matching) == 1

    # The canonical update handler does not allow clients to assign the
    # dispatcher-owned running state directly.
    rejected = client.patch(
        f"/api/moonpie/kanban/tasks/{task_id}", headers=_auth(token), json={"status": "running"}
    )
    assert rejected.status_code == 400

    detail = client.get(f"/api/moonpie/kanban/tasks/{task_id}", headers=_auth(token))
    assert detail.status_code == 200, detail.text
    assert detail.json()["task"]["id"] == task_id

    response = client.post(
        f"/api/moonpie/kanban/tasks/{task_id}/comments",
        headers=_auth(token),
        json={"body": "A note", "author": "forged-operator"},
    )
    assert response.status_code == 200, response.text
    detail = client.get(f"/api/moonpie/kanban/tasks/{task_id}", headers=_auth(token))
    assert detail.json()["comments"][-1]["author"] == "moonpie:moonpie-kanban-test-device"


def test_event_stream_rejects_missing_device_frame(moonpie_client, token):
    client, _ = moonpie_client
    with client.websocket_connect("/api/moonpie/kanban/events") as websocket:
        websocket.send_json({"jsonrpc": "2.0", "id": "1", "method": "auth.login", "params": {"device_token": "invalid"}})
        # TestClient raises WebSocketDisconnect when the server closes with a
        # policy violation; this ensures the stream never sends board data first.
        with pytest.raises(WebSocketDisconnect):
            websocket.receive_json()


def test_event_stream_replays_live_task_change(moonpie_client, token):
    client, _ = moonpie_client
    with client.websocket_connect("/api/moonpie/kanban/events") as rejected:
        rejected.send_json({
            "jsonrpc": "2.0", "id": "bad", "method": "auth.login",
            "params": {"device_token": "invalid"},
        })
        with pytest.raises(WebSocketDisconnect):
            rejected.receive_json()

    with client.websocket_connect("/api/moonpie/kanban/events") as websocket:
        websocket.send_json({
            "jsonrpc": "2.0", "id": "1", "method": "auth.login",
            "params": {"device_token": token},
        })
        assert websocket.receive_json()["result"]["status"] == "authenticated"
        assert websocket.receive_json()["method"] == "kanban.ready"

        created = client.post("/api/moonpie/kanban/tasks", headers=_auth(token), json={"title": "Live event"})
        assert created.status_code == 201, created.text
        event_message = websocket.receive_json()
        assert event_message["method"] == "kanban.events"
        assert event_message["params"]["events"]
