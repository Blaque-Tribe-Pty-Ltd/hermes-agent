import asyncio
import concurrent.futures
import json
from types import SimpleNamespace

from hermes_cli.moonpie_adapter import _MOONPIE_APP_ACTION_SCHEMA, _enable_moonpie_app_action
from hermes_cli.web_routers.moonpie import _MoonPieConnection, _valid_app_action, _validated_application_context
from agent.inline_tool_executors import InlineToolContext, _moonpie_app_action

pytest_plugins = ("tests.hermes_cli.test_moonpie_auth",)


def _context():
    return {
        "active_section": "Tasks", "gateway_connection": "Connected", "kanban_data": "cached; reconnecting",
        "active_conversation_id": "c1", "selected_task": {"id": "t1", "title": "Task", "status": "todo"},
        "notifications": [], "pending_approvals": [], "active_jobs": [],
    }


def test_application_context_is_bounded_and_bound_to_conversation():
    assert _validated_application_context(_context(), "c1")["selected_task"]["id"] == "t1"
    assert _validated_application_context(_context(), "other") is None
    invalid = {**_context(), "device_token": "secret"}
    assert _validated_application_context(invalid, "c1") is None
    invalid = {**_context(), "notifications": [{"id": "x", "title": "x", "status": "new", "selector": "*"}]}
    assert _validated_application_context(invalid, "c1") is None


def test_action_result_requires_pending_id_and_same_conversation():
    conn = _MoonPieConnection(object())
    future = concurrent.futures.Future()
    conn.pending_app_actions["r1"] = (future, "c1")
    assert not conn.resolve_app_action({"request_id": "r1", "conversation_id": "c2", "result": {"request_id": "r1"}})
    assert not future.done()
    assert not conn.resolve_app_action({"request_id": "stale", "conversation_id": "c1", "result": {"request_id": "stale"}})
    assert conn.resolve_app_action({"request_id": "r1", "conversation_id": "c1", "result": {"request_id": "r1", "outcome": "opened"}})
    assert json.loads(future.result())["outcome"] == "opened"


def test_action_executor_is_moonpie_session_scoped():
    called = []
    agent = SimpleNamespace(platform="moonpie", moonpie_app_action_callback=lambda args: called.append(args) or "verified")
    args = {"action": "open_system_status", "arguments": {}}
    assert _moonpie_app_action(agent, args, InlineToolContext(effective_task_id="")) == "verified"
    assert called == [args]
    other = SimpleNamespace(platform="cli")
    assert json.loads(_moonpie_app_action(other, args, InlineToolContext(effective_task_id="")))["outcome"] == "unavailable"


def test_action_tool_schema_is_allowlisted_and_installed_only_for_moonpie():
    names = _MOONPIE_APP_ACTION_SCHEMA["function"]["parameters"]["properties"]["action"]["enum"]
    assert "open_task_by_title" in names and "update_task" in names
    agent = SimpleNamespace(platform="moonpie", tools=[], valid_tool_names=set())
    _enable_moonpie_app_action(agent, lambda args: "ok")
    assert "moonpie_app_action" in agent.valid_tool_names
    assert agent.tools[0] is _MOONPIE_APP_ACTION_SCHEMA
    established = SimpleNamespace(platform="moonpie", tools=[], valid_tool_names=set(), _cached_system_prompt="stable")
    _enable_moonpie_app_action(established, lambda args: "ok")
    assert established.tools == []
    assert established.valid_tool_names == set()
    other = SimpleNamespace(platform="cli", tools=[], valid_tool_names=set())
    _enable_moonpie_app_action(other, lambda args: "ok")
    assert other.tools == []


def test_authenticated_websocket_completes_an_app_action_roundtrip(valid_token):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from unittest.mock import patch
    from hermes_cli.web_routers import moonpie as mp

    app = FastAPI()
    app.include_router(mp.router)
    seen = {}

    async def fake_chat(content, stream_callback=None, tool_event_callback=None, app_action_callback=None, **kwargs):
        seen["content"] = content
        result = await asyncio.to_thread(app_action_callback, {
            "action": "open_system_status", "arguments": {}
        })
        return result

    def no_usage(*args, **kwargs):
        return None

    with patch.object(mp._moonpie_adapter, "chat", side_effect=fake_chat), \
         patch.object(mp._moonpie_adapter, "usage_snapshot", side_effect=no_usage), \
         patch.object(mp._moonpie_adapter, "synthesize_speech", return_value=None), \
         TestClient(app) as client:
        with client.websocket_connect("/api/moonpie/ws") as ws:
            ws.send_json({"jsonrpc": "2.0", "id": "auth", "method": "auth.login",
                          "params": {"device_token": valid_token}})
            ws.receive_json()
            ws.receive_json()
            context = _context()
            ws.send_json({"jsonrpc": "2.0", "id": "turn", "method": "conversation.message",
                          "params": {"conversation_id": "c1", "content": "Open system status",
                                     "application_context": context}})
            request = None
            for _ in range(8):
                event = ws.receive_json()
                if event.get("method") == "moonpie.app_action.request":
                    request = event["params"]
                    break
            assert request and request["action"] == "open_system_status"
            assert "trusted client snapshot" in seen["content"]
            ws.send_json({"jsonrpc": "2.0", "id": "result", "method": "moonpie.app_action.result",
                          "params": {"request_id": request["id"], "conversation_id": "c1",
                                     "result": {"request_id": request["id"], "outcome": "opened",
                                                "active_section": "System"}}})
            responses = []
            for _ in range(8):
                event = ws.receive_json()
                responses.append(event)
                if event.get("id") == "turn":
                    break
    assert any(event.get("method") == "conversation.complete" for event in responses)
    assert any(event.get("result", {}).get("status") == "complete" for event in responses)
    assert any("active_section" in str(event) for event in responses)


def test_server_action_allowlist_rejects_unknown_or_malformed_arguments():
    assert _valid_app_action("navigate_to_section", {"section": "tasks"})
    assert _valid_app_action("update_task", {"task_id": "t1", "changes": {"status": "done", "priority": 4}})
    assert not _valid_app_action("run_shell", {"command": "id"})
    assert not _valid_app_action("open_panel", {"panel": ["tasks"]})
    assert not _valid_app_action("open_task_by_id", {"task_id": "t1", "selector": "*"})
    assert not _valid_app_action("update_task", {"task_id": "t1", "changes": {"priority": True}})
    assert not _valid_app_action("update_task", {"task_id": "t1", "changes": {"status": ""}})
