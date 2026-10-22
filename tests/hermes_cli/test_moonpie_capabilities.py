"""MoonPie composer capabilities: advertised options, per-turn reasoning/permission, usage events."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from hermes_cli.moonpie_adapter import MoonPieHermesAdapter
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes_cli.web_routers import moonpie as mp
from tools.approval import is_session_yolo_enabled, disable_session_yolo

pytest_plugins = ("tests.hermes_cli.test_moonpie_auth",)


class _Agent:
    """Minimal stand-in for AIAgent: records the settings seen during chat()."""

    def __init__(self, fail=False):
        self.model = "kimi-k2.6"
        self.reasoning_config = {"enabled": True, "effort": "medium"}
        self.seen = {}
        self.fail = fail

    session_id = "sess-agent"

    def run_conversation(self, content, conversation_history=None, task_id=None, stream_callback=None):
        self.seen["reasoning"] = self.reasoning_config
        self.seen["yolo"] = is_session_yolo_enabled(self.session_key)
        if self.fail:
            raise RuntimeError("boom")
        return {"final_response": "ok"}


class _DB:
    def get_messages_as_conversation(self, session_id):
        return []


def _adapter(agent):
    adapter = MoonPieHermesAdapter(session_db_provider=lambda: _DB())
    adapter._agents[adapter._session_id("d", "c")] = agent
    return adapter


def _chat(adapter, **kwargs):
    return asyncio.run(adapter.chat("hi", stream_callback=lambda d: None, device_id="d", conversation_id="c", session_key="sess-cap", **kwargs))


@pytest.fixture(autouse=True)
def _clean_yolo():
    disable_session_yolo("sess-cap")
    yield
    disable_session_yolo("sess-cap")


def _agent_with_key(fail=False):
    agent = _Agent(fail=fail)
    agent.session_key = "sess-cap"
    return agent


def test_capabilities_advertise_levels_modes_and_active_model():
    with patch("hermes_cli.config.load_config_readonly", return_value={"model": {"default": "kimi-k2.6"}}):
        caps = _adapter(_agent_with_key()).capabilities()
    assert caps["models"] == [{"id": "kimi-k2.6", "label": "kimi-k2.6", "active": True}]
    assert caps["reasoning_levels"] == ["low", "medium", "high"]
    assert caps["default_reasoning_level"] == "medium"
    assert caps["permission_modes"] == ["ask", "auto"]
    assert caps["default_permission_mode"] == "ask"
    assert caps["usage"] is True


def test_old_message_without_options_is_unchanged():
    agent = _agent_with_key()
    original = agent.reasoning_config
    assert _chat(_adapter(agent)) == "ok"
    assert agent.seen["reasoning"] == original
    assert agent.seen["yolo"] is False


def test_reasoning_level_applied_for_turn_and_restored():
    agent = _agent_with_key()
    original = agent.reasoning_config
    _chat(_adapter(agent), reasoning_level="high")
    assert agent.seen["reasoning"] == {"enabled": True, "effort": "high"}
    assert agent.reasoning_config is original


def test_invalid_values_are_ignored():
    agent = _agent_with_key()
    original = agent.reasoning_config
    _chat(_adapter(agent), reasoning_level="extreme", permission_mode="root")
    assert agent.seen["reasoning"] == original
    assert agent.seen["yolo"] is False


def test_auto_permission_enables_session_yolo_only_for_the_turn():
    agent = _agent_with_key()
    _chat(_adapter(agent), permission_mode="auto")
    assert agent.seen["yolo"] is True
    assert is_session_yolo_enabled("sess-cap") is False


def test_ask_permission_turns_an_existing_auto_session_off_for_the_turn_then_restores_it():
    from tools.approval import enable_session_yolo

    enable_session_yolo("sess-cap")
    agent = _agent_with_key()
    _chat(_adapter(agent), permission_mode="ask")
    assert agent.seen["yolo"] is False
    assert is_session_yolo_enabled("sess-cap") is True


def test_options_restored_after_agent_error():
    agent = _agent_with_key(fail=True)
    original = agent.reasoning_config
    with pytest.raises(RuntimeError):
        _chat(_adapter(agent), reasoning_level="low", permission_mode="auto")
    assert agent.reasoning_config is original
    assert is_session_yolo_enabled("sess-cap") is False


def test_usage_snapshot_reads_counters_and_context():
    agent = _agent_with_key()
    agent.session_input_tokens = 1200
    agent.session_output_tokens = 300
    agent.context_compressor = SimpleNamespace(context_length=128000, last_prompt_tokens=4000)
    assert _adapter(agent).usage_snapshot("d", "c") == {
        "input_tokens": 1200,
        "output_tokens": 300,
        "context_window": 128000,
        "context_used": 4000,
    }


def test_no_usage_when_agent_has_no_counters():
    agent = _agent_with_key()
    assert _adapter(agent).usage_snapshot("d", "c") is None
    assert MoonPieHermesAdapter().usage_snapshot() is None


# --- WebSocket surface -------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    app = FastAPI()
    app.include_router(mp.router)
    with TestClient(app) as c:
        yield c


def _login(ws, token):
    ws.send_json({"jsonrpc": "2.0", "id": 1, "method": "auth.login", "params": {"device_token": token}})
    json.loads(ws.receive_text())  # auth result
    json.loads(ws.receive_text())  # connection.ready


def test_ws_device_capabilities_include_composer_options(client, valid_token):
    with client.websocket_connect("/api/moonpie/ws") as ws:
        _login(ws, valid_token)
        ws.send_json({"jsonrpc": "2.0", "id": 2, "method": "device.capabilities", "params": {}})
        result = json.loads(ws.receive_text())["result"]
    assert result["bridge_available"] is False and result["workspaces"] == []
    assert result["reasoning_levels"] == ["low", "medium", "high"]
    assert result["permission_modes"] == ["ask", "auto"]
    assert result["usage"] is True


def test_ws_message_passes_options_and_emits_usage_after_complete(client, valid_token):
    seen = {}

    async def fake_chat(content, stream_callback=None, tool_event_callback=None, **kwargs):
        seen.update(kwargs)
        return "done"

    with patch.object(mp._moonpie_adapter, "chat", side_effect=fake_chat), \
         patch.object(mp._moonpie_adapter, "usage_snapshot", return_value={"input_tokens": 10, "output_tokens": 5}), \
         patch.object(mp._moonpie_adapter, "synthesize_speech", return_value=None):
        with client.websocket_connect("/api/moonpie/ws") as ws:
            _login(ws, valid_token)
            ws.send_json({"jsonrpc": "2.0", "id": 3, "method": "conversation.message",
                          "params": {"conversation_id": "c1", "content": "hi",
                                     "reasoning_level": "high", "permission_mode": "auto"}})
            methods = []
            for _ in range(6):
                msg = json.loads(ws.receive_text())
                methods.append(msg.get("method") or ("result" if "result" in msg else "?"))
                if msg.get("method") == "usage":
                    usage = msg["params"]
                    break
    assert seen["reasoning_level"] == "high" and seen["permission_mode"] == "auto"
    assert usage == {"input_tokens": 10, "output_tokens": 5, "conversation_id": "c1"}
    assert methods.index("conversation.complete") < methods.index("usage")


def test_ws_message_without_options_sends_none_and_no_usage_event(client, valid_token):
    seen = {}

    async def fake_chat(content, stream_callback=None, tool_event_callback=None, **kwargs):
        seen.update(kwargs)
        return "done"

    with patch.object(mp._moonpie_adapter, "chat", side_effect=fake_chat), \
         patch.object(mp._moonpie_adapter, "usage_snapshot", return_value=None), \
         patch.object(mp._moonpie_adapter, "synthesize_speech", return_value=None):
        with client.websocket_connect("/api/moonpie/ws") as ws:
            _login(ws, valid_token)
            ws.send_json({"jsonrpc": "2.0", "id": 3, "method": "conversation.message",
                          "params": {"conversation_id": "c1", "content": "hi"}})
            methods = []
            while True:
                msg = json.loads(ws.receive_text())
                methods.append(msg.get("method"))
                if "result" in msg and msg.get("id") == 3:
                    break
    assert seen["reasoning_level"] is None and seen["permission_mode"] is None
    assert "usage" not in methods
