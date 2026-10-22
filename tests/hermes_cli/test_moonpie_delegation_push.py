"""A finished delegation is pushed to the MoonPie app once, in the right conversation, in readable form."""

pytest_plugins = ("tests.hermes_cli.test_moonpie_auth",)

import asyncio
import json
import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes_cli.moonpie_delegation_push import DelegationWatcher, build_completion
from hermes_cli.web_routers import moonpie as mp
from hermes_state import SessionDB

CHILD_JSON = json.dumps({
    "status": "completed", "path": "Sources/Greeter.swift",
    "changed": "Created a Swift Greeter struct.", "verification": "Read back; 98 bytes.", "blocker": "",
})


def _event(**over):
    base = {"delegation_id": "d1", "status": "completed", "goal": "Act as Neo. Add Sources/Greeter.swift. More detail follows.",
            "total_duration_seconds": 49.3,
            "results": [{"status": "completed", "summary": CHILD_JSON, "duration_seconds": 48.9}]}
    base.update(over)
    return base


# --- message ------------------------------------------------------------------------------------------------------

def test_structured_child_answer_becomes_readable_lines():
    msg = build_completion(_event())
    assert msg["status"] == "completed" and msg["delegation_id"] == "d1" and msg["duration_seconds"] == 49.3
    assert msg["text"].startswith("A delegated task finished: Act as Neo.")
    assert "Changed: Created a Swift Greeter struct." in msg["text"]
    assert "Verified: Read back; 98 bytes." in msg["text"]
    assert "Blocked" not in msg["text"]


def test_a_blocker_is_shown_and_a_failed_task_is_marked_failed():
    blocked = json.dumps({"status": "blocked", "changed": "No files changed.", "blocker": "Tools unavailable."})
    msg = build_completion(_event(results=[{"status": "failed", "summary": blocked}], status="failed"))
    assert msg["status"] == "failed" and msg["title"] == "A delegated task failed"
    assert "Blocked: Tools unavailable." in msg["text"]


def test_plain_text_answers_are_trimmed_and_missing_ones_do_not_break():
    long = build_completion(_event(results=[{"status": "completed", "summary": "word " * 400}]))
    assert len(long["text"]) < 900 and long["text"].endswith("…")
    empty = build_completion({"delegation_id": "d2", "status": "completed"})
    assert empty["text"] == "A delegated task finished."
    erred = build_completion({"delegation_id": "d3", "status": "failed", "error": "child crashed"})
    assert "child crashed" in erred["text"] and erred["status"] == "failed"


def test_several_tasks_are_numbered():
    msg = build_completion(_event(results=[{"status": "completed", "summary": "one"}, {"status": "completed", "summary": "two"}]))
    assert "Task 1: one" in msg["text"] and "Task 2: two" in msg["text"]


# --- watcher ------------------------------------------------------------------------------------------------------

def _row(delegation_id="d1", parent="moonpie_abc", completed_at=None, event=None):
    return {"delegation_id": delegation_id, "parent_session_id": parent,
            "completed_at": completed_at or time.time(), "event": _event() if event is None else event}


def _watcher(rows, mapping, sent, **kw):
    async def send(data):
        sent.append(data)
    return DelegationWatcher(lambda ids, since: list(rows), lambda: dict(mapping), send, **kw)


@pytest.mark.asyncio
async def test_a_new_completion_is_pushed_once_with_the_conversation_id():
    sent = []
    watcher = _watcher([_row()], {"moonpie_abc": "conv-1"}, sent)
    assert await watcher.poll_once() == 1
    assert await watcher.poll_once() == 0
    assert len(sent) == 1
    assert sent[0]["method"] == "delegation.completed"
    assert sent[0]["params"]["conversation_id"] == "conv-1"
    assert sent[0]["params"]["delegation_id"] == "d1"


@pytest.mark.asyncio
async def test_other_devices_sessions_and_unknown_sessions_are_ignored():
    sent = []
    watcher = _watcher([_row(parent="moonpie_other")], {"moonpie_abc": "conv-1"}, sent)
    assert await watcher.poll_once() == 0 and sent == []
    assert await _watcher([_row()], {}, sent).poll_once() == 0


@pytest.mark.asyncio
async def test_the_query_only_asks_for_completions_after_the_watcher_started():
    seen = {}

    def fetch(ids, since):
        seen["ids"], seen["since"] = ids, since
        return []

    async def send(data):
        pass

    started = time.time()
    watcher = DelegationWatcher(fetch, lambda: {"moonpie_abc": "c"}, send, started_at=started)
    await watcher.poll_once()
    assert seen["ids"] == ["moonpie_abc"] and started - 2 < seen["since"] <= started


@pytest.mark.asyncio
async def test_a_failing_query_is_survived():
    async def send(data):
        pass

    def boom(ids, since):
        raise RuntimeError("db locked")

    assert await DelegationWatcher(boom, lambda: {"s": "c"}, send).poll_once() == 0


# --- database -----------------------------------------------------------------------------------------------------

def test_the_database_query_filters_by_parent_session_and_time(tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        cols = [r[1] for r in db._read_all("PRAGMA table_info(async_delegations)")]
        assert "event_json" in cols and "parent_session_id" in cols

        def add(delegation_id, parent, completed_at):
            db._write_sql(
                "INSERT INTO async_delegations (delegation_id, origin_session, parent_session_id, state, dispatched_at, "
                "completed_at, updated_at, event_json, result_json) VALUES (?, ?, ?, 'completed', ?, ?, ?, ?, '{}')",
                (delegation_id, "k", parent, completed_at - 5, completed_at, completed_at, json.dumps(_event(delegation_id=delegation_id))),
            )

        now = time.time()
        add("old", "moonpie_abc", now - 1000)
        add("new", "moonpie_abc", now)
        add("elsewhere", "moonpie_zzz", now)
        rows = db.list_completed_delegations(["moonpie_abc"], now - 60)
        assert [r["delegation_id"] for r in rows] == ["new"]
        assert rows[0]["event"]["goal"].startswith("Act as Neo")
        assert db.list_completed_delegations([], now - 60) == []
    finally:
        db.close()


# --- WebSocket ----------------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    app = FastAPI()
    app.include_router(mp.router)
    with TestClient(app) as c:
        yield c


def _login(ws, token):
    ws.send_json({"jsonrpc": "2.0", "id": 1, "method": "auth.login", "params": {"device_token": token}})
    json.loads(ws.receive_text())
    json.loads(ws.receive_text())


def test_the_connected_app_receives_the_completion_for_its_conversation(client, valid_token):
    with patch.object(mp._moonpie_adapter, "conversations_for_device", return_value={"moonpie_conv": "conv-77"}), \
         patch.object(mp, "_fetch_delegations", return_value=[_row(parent="moonpie_conv")]):
        with client.websocket_connect("/api/moonpie/ws") as ws:
            _login(ws, valid_token)
            frames = []
            for _ in range(20):
                msg = json.loads(ws.receive_text())
                frames.append(msg)
                if msg.get("method") == "delegation.completed":
                    break
    pushed = [m for m in frames if m.get("method") == "delegation.completed"]
    assert pushed, [m.get("method") for m in frames]
    assert pushed[0]["params"]["conversation_id"] == "conv-77"
    assert "Changed: Created a Swift Greeter struct." in pushed[0]["params"]["text"]


def test_the_adapter_remembers_which_conversation_a_session_belongs_to():
    from hermes_cli.moonpie_adapter import MoonPieHermesAdapter
    adapter = MoonPieHermesAdapter()
    sid = adapter._session_id("dev", "conv-1")
    adapter._conversations[sid] = ("dev", "conv-1")
    adapter._conversations[adapter._session_id("other", "conv-9")] = ("other", "conv-9")
    assert adapter.conversations_for_device("dev") == {sid: "conv-1"}


# --- the user's own request as the headline ---------------------------------------------------------------------

from hermes_cli.moonpie_delegation_push import request_headline


def test_the_headline_is_the_users_request_without_the_apps_project_block():
    stored = ("Delegate this to Neo: add Sources/Farewell.swift.\n\n"
              "<moonpie_project name=\"p\" branch=\"main\">\nThe user is working in the project.\n</moonpie_project>")
    assert request_headline(stored) == "Delegate this to Neo: add Sources/Farewell.swift."
    assert request_headline("  one\n two  ") == "one two"
    assert request_headline(None) == ""
    assert len(request_headline("word " * 100)) <= 140


def test_the_message_uses_the_users_request_and_falls_back_to_the_delegated_prompt():
    event = _event()
    with_request = build_completion(event, "Add a greeter to the project")
    assert with_request["text"].startswith("A delegated task finished: Add a greeter to the project\n")
    assert "Act as Neo" not in with_request["text"]
    fallback = build_completion(event, None)
    assert fallback["text"].startswith("A delegated task finished: Act as Neo.")
    assert build_completion(event, "   ")["text"].startswith("A delegated task finished: Act as Neo.")


@pytest.mark.asyncio
async def test_the_watcher_looks_up_the_request_that_was_made_before_dispatch():
    sent, asked = [], {}

    async def send(data):
        sent.append(data)

    def request_for(session_id, before):
        asked["args"] = (session_id, before)
        return "Please add the Greeter"

    event = _event(dispatched_at=1000.0)
    watcher = DelegationWatcher(lambda ids, since: [_row(event=event)], lambda: {"moonpie_abc": "c1"}, send, request_for=request_for)
    await watcher.poll_once()
    assert asked["args"] == ("moonpie_abc", 1000.0)
    assert "Please add the Greeter" in sent[0]["params"]["text"]


@pytest.mark.asyncio
async def test_a_failing_request_lookup_still_delivers_with_the_fallback_goal():
    sent = []

    async def send(data):
        sent.append(data)

    def boom(session_id, before):
        raise RuntimeError("db locked")

    watcher = DelegationWatcher(lambda ids, since: [_row()], lambda: {"moonpie_abc": "c1"}, send, request_for=boom)
    assert await watcher.poll_once() == 1
    assert sent[0]["params"]["text"].startswith("A delegated task finished: Act as Neo.")


def test_the_database_returns_the_last_real_user_message_before_the_time(tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session("moonpie_abc", source="api_server")
        db.append_message("moonpie_abc", "user", "first request", timestamp=100.0)
        db.append_message("moonpie_abc", "assistant", "ok", timestamp=101.0)
        db.append_message("moonpie_abc", "user", "second request\n\n<moonpie_project name=\"p\">x</moonpie_project>", timestamp=200.0)
        db.append_message("moonpie_abc", "user", "[ASYNC DELEGATION BATCH COMPLETE]", timestamp=250.0,
                          display_kind="async_delegation_complete")
        db.append_message("moonpie_abc", "user", "a later request", timestamp=300.0)
        assert db.last_user_request("moonpie_abc", 260.0).startswith("second request")
        assert db.last_user_request("moonpie_abc", 150.0) == "first request"
        assert db.last_user_request("moonpie_abc", 50.0) is None
        assert db.last_user_request("moonpie_zzz", 999.0) is None
    finally:
        db.close()
