"""A turn must not stop the server from reading the client's next frames (approval answers, cancel)."""

pytest_plugins = ("tests.hermes_cli.test_moonpie_auth",)

import asyncio
import json
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes_cli.web_routers import moonpie as mp


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


def _read_until(ws, predicate, limit=40):
    seen = []
    for _ in range(limit):
        msg = json.loads(ws.receive_text())
        seen.append(msg)
        if predicate(msg):
            return seen
    raise AssertionError(f"never saw the expected frame; got {[m.get('method') or m.get('id') for m in seen]}")


def test_approval_answer_is_read_while_the_turn_is_still_waiting_for_it(client, valid_token):
    """Before the fix the turn was awaited inside the receive loop: the answer sat unread until the turn gave up."""
    answered = {"flag": False}

    def fake_resolve(session_key, choice, request_id=None, **kwargs):
        answered["flag"] = True
        return 1

    async def fake_chat(content, stream_callback=None, tool_event_callback=None, **kwargs):
        for _ in range(300):  # up to 3 s
            if answered["flag"]:
                return "released by the answer"
            await asyncio.sleep(0.01)
        return "timed out waiting for the answer"

    with patch.object(mp._moonpie_adapter, "chat", side_effect=fake_chat), \
         patch.object(mp._moonpie_adapter, "usage_snapshot", return_value=None), \
         patch.object(mp._moonpie_adapter, "synthesize_speech", return_value=None), \
         patch.object(mp, "resolve_gateway_approval", side_effect=fake_resolve):
        with client.websocket_connect("/api/moonpie/ws") as ws:
            _login(ws, valid_token)
            ws.send_json({"jsonrpc": "2.0", "id": 10, "method": "conversation.message",
                          "params": {"conversation_id": "tc1", "content": "do it"}})
            ws.send_json({"jsonrpc": "2.0", "id": 11, "method": "approval.respond",
                          "params": {"approval_id": "abc", "action": "deny"}})
            frames = _read_until(ws, lambda m: m.get("id") == 10 and "result" in m)
    turn_result = next(m for m in frames if m.get("id") == 10)["result"]
    assert turn_result["content"] == "released by the answer"
    assert any(m.get("id") == 11 and m.get("result", {}).get("status") == "resolved" for m in frames)


def test_two_messages_on_one_connection_run_one_after_the_other(client, valid_token):
    running = {"now": 0, "max": 0}

    async def fake_chat(content, stream_callback=None, tool_event_callback=None, **kwargs):
        running["now"] += 1
        running["max"] = max(running["max"], running["now"])
        await asyncio.sleep(0.05)
        running["now"] -= 1
        return f"done {content}"

    with patch.object(mp._moonpie_adapter, "chat", side_effect=fake_chat), \
         patch.object(mp._moonpie_adapter, "usage_snapshot", return_value=None), \
         patch.object(mp._moonpie_adapter, "synthesize_speech", return_value=None):
        with client.websocket_connect("/api/moonpie/ws") as ws:
            _login(ws, valid_token)
            for rid, text in ((20, "first"), (21, "second")):
                ws.send_json({"jsonrpc": "2.0", "id": rid, "method": "conversation.message",
                              "params": {"conversation_id": "tc2", "content": text}})
            done = {}
            for _ in range(60):
                msg = json.loads(ws.receive_text())
                if msg.get("id") in (20, 21) and "result" in msg:
                    done[msg["id"]] = msg["result"]["content"]
                if len(done) == 2:
                    break
    assert done == {20: "done first", 21: "done second"}
    assert running["max"] == 1


def test_closing_the_socket_cancels_a_running_turn_task(client, valid_token):
    started = {"flag": False}
    finished = {"flag": False}

    async def fake_chat(content, stream_callback=None, tool_event_callback=None, **kwargs):
        started["flag"] = True
        try:
            await asyncio.sleep(5)
        finally:
            finished["flag"] = True
        return "late"

    with patch.object(mp._moonpie_adapter, "chat", side_effect=fake_chat), \
         patch.object(mp._moonpie_adapter, "usage_snapshot", return_value=None), \
         patch.object(mp._moonpie_adapter, "synthesize_speech", return_value=None):
        with client.websocket_connect("/api/moonpie/ws") as ws:
            _login(ws, valid_token)
            ws.send_json({"jsonrpc": "2.0", "id": 30, "method": "conversation.message",
                          "params": {"conversation_id": "tc3", "content": "long"}})
            for _ in range(100):
                if started["flag"]:
                    break
                import time; time.sleep(0.02)
        for _ in range(100):
            if finished["flag"]:
                break
            import time; time.sleep(0.02)
    assert started["flag"] and finished["flag"]
