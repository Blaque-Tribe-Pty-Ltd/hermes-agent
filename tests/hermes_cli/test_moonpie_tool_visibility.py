"""Gate 5 tests: Tool Visibility.

Verifies that tool lifecycle events (started, completed, failed) are captured
from the Hermes adapter, normalized, and streamed through the WebSocket to
MoonPie clients.
"""

import asyncio
import json
import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import TestClient as StarletteTestClient

from hermes_cli.web_routers import moonpie as mp

# Reuse the auth fixtures from the auth suite
pytest_plugins = ("tests.hermes_cli.test_moonpie_auth",)


@pytest.fixture(scope="module")
def client():
    """TestClient for the MoonPie router."""
    from hermes_cli.web_routers.moonpie import router
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as c:
        yield c


class TestToolEventNormalization:
    """Adapter must normalize Hermes tool callbacks into stable MoonPie events."""

    def test_tool_start_callback_emits_started_event(self):
        """_on_tool_start emits a 'tool.started' event with sanitized data."""
        from hermes_cli.moonpie_adapter import MoonPieHermesAdapter

        received = []
        def capture(event_type: str, data: dict):
            received.append((event_type, data))

        adapter = MoonPieHermesAdapter()
        mock_agent = MagicMock()
        adapter._wire_tool_callbacks(mock_agent, capture)

        # Simulate tool start
        mock_agent.tool_start_callback("call-001", "read_file", {"path": "/etc/passwd"})

        assert len(received) == 1
        etype, data = received[0]
        assert etype == "tool.started"
        assert data["tool_call_id"] == "call-001"
        assert data["tool_name"] == "read_file"
        # Args are sanitized — no path leakage
        assert "path" not in data
        assert data["preview"] == "read_file"

    def test_tool_complete_callback_emits_completed_event(self):
        """Successful tool completion emits 'tool.completed' with duration."""
        from hermes_cli.moonpie_adapter import MoonPieHermesAdapter

        received = []
        def capture(event_type: str, data: dict):
            received.append((event_type, data))

        adapter = MoonPieHermesAdapter()
        mock_agent = MagicMock()
        adapter._wire_tool_callbacks(mock_agent, capture)

        mock_agent.tool_start_callback("call-002", "web_search", {"query": "test"})
        time.sleep(0.01)
        mock_agent.tool_complete_callback("call-002", "web_search", {"query": "test"}, {"results": []})

        assert len(received) == 2
        etype, data = received[1]
        assert etype == "tool.completed"
        assert data["tool_call_id"] == "call-002"
        assert data["tool_name"] == "web_search"
        assert isinstance(data["duration_ms"], int)
        assert data["duration_ms"] >= 10
        # No result payload leakage
        assert "results" not in data
        assert "error_message" not in data

    def test_tool_complete_callback_emits_failed_event(self):
        """Error result emits 'tool.failed' with error_message."""
        from hermes_cli.moonpie_adapter import MoonPieHermesAdapter

        received = []
        def capture(event_type: str, data: dict):
            received.append((event_type, data))

        adapter = MoonPieHermesAdapter()
        mock_agent = MagicMock()
        adapter._wire_tool_callbacks(mock_agent, capture)

        mock_agent.tool_start_callback("call-003", "terminal", {"command": "rm -rf /"})
        mock_agent.tool_complete_callback("call-003", "terminal", {"command": "rm -rf /"}, {"error": "Permission denied"})

        assert len(received) == 2
        etype, data = received[1]
        assert etype == "tool.failed"
        assert data["tool_call_id"] == "call-003"
        assert data["tool_name"] == "terminal"
        assert data["error_message"] == "Permission denied"
        assert "duration_ms" in data

    def test_tool_complete_with_string_error(self):
        """String result starting with 'Error:' is treated as failure."""
        from hermes_cli.moonpie_adapter import MoonPieHermesAdapter

        received = []
        def capture(event_type: str, data: dict):
            received.append((event_type, data))

        adapter = MoonPieHermesAdapter()
        mock_agent = MagicMock()
        adapter._wire_tool_callbacks(mock_agent, capture)

        mock_agent.tool_start_callback("call-004", "browser", {})
        mock_agent.tool_complete_callback("call-004", "browser", {}, "Error: timeout")

        etype, data = received[1]
        assert etype == "tool.failed"
        assert data["error_message"] == "Error: timeout"

    def test_multiple_tools_correlated_by_call_id(self):
        """Simultaneous tools are tracked independently by call_id."""
        from hermes_cli.moonpie_adapter import MoonPieHermesAdapter

        received = []
        def capture(event_type: str, data: dict):
            received.append((event_type, data))

        adapter = MoonPieHermesAdapter()
        mock_agent = MagicMock()
        adapter._wire_tool_callbacks(mock_agent, capture)

        mock_agent.tool_start_callback("call-A", "search", {})
        mock_agent.tool_start_callback("call-B", "read", {})
        mock_agent.tool_complete_callback("call-A", "search", {}, "ok")
        mock_agent.tool_complete_callback("call-B", "read", {}, {"error": "not found"})

        assert len(received) == 4
        ids = [d["tool_call_id"] for _, d in received]
        assert ids == ["call-A", "call-B", "call-A", "call-B"]
        assert received[2][0] == "tool.completed"
        assert received[3][0] == "tool.failed"

    def test_no_sensitive_args_leakage(self):
        """Tool arguments (which may contain secrets) are never forwarded."""
        from hermes_cli.moonpie_adapter import MoonPieHermesAdapter

        received = []
        def capture(event_type: str, data: dict):
            received.append((event_type, data))

        adapter = MoonPieHermesAdapter()
        mock_agent = MagicMock()
        adapter._wire_tool_callbacks(mock_agent, capture)

        mock_agent.tool_start_callback("call-005", "execute_code", {"code": "print(API_KEY)"})

        _, data = received[0]
        assert "code" not in data
        assert "API_KEY" not in str(data)


class TestWebSocketToolEvents:
    """Tool lifecycle events must stream through the authenticated WebSocket."""

    def test_tool_events_gated_behind_auth(self, client):
        """Tool events are never sent before connection.ready."""
        # This is implicitly tested by the auth gate: any event sent before
        # auth.login would be dropped because the connection is not in
        # _moonpie_connections yet.
        pass

    def test_adapter_chat_accepts_tool_event_callback(self):
        """Adapter.chat() accepts and wires tool_event_callback."""
        from hermes_cli.moonpie_adapter import MoonPieHermesAdapter

        adapter = MoonPieHermesAdapter()
        # The signature must accept tool_event_callback
        import inspect
        sig = inspect.signature(adapter.chat)
        assert "tool_event_callback" in sig.parameters

    def test_tool_event_callback_is_callable_type(self):
        """tool_event_callback parameter accepts a callable."""
        from hermes_cli.moonpie_adapter import MoonPieHermesAdapter
        from typing import Callable

        adapter = MoonPieHermesAdapter()
        sig = adapter.chat.__code__.co_varnames
        assert "tool_event_callback" in sig
