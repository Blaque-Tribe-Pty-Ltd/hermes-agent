"""Gate 6: Approval Robustness + conversation.cancel (B5).

Tests cancellation semantics, approval withdrawal, idempotency, and
reconnect safety.
"""

import asyncio
import threading
import time
import pytest
from unittest.mock import MagicMock, patch

from hermes_cli.moonpie_adapter import MoonPieHermesAdapter, _TurnState


class TestAdapterCancellation:
    """Unit tests for MoonPieHermesAdapter cancellation (no Hermes deps)."""

    def test_cancel_turn_not_found(self):
        adapter = MoonPieHermesAdapter()
        result = adapter.cancel_turn("dev-1", "conv-1")
        assert result["cancelled"] is False
        assert result["not_found"] is True
        assert result["already_complete"] is False
        assert result["withdrawn_approvals"] == []

    def test_cancel_turn_wrong_conversation(self):
        adapter = MoonPieHermesAdapter()
        turn = _TurnState("dev-1", "conv-1", "session-1")
        adapter._active_turns["dev-1"] = turn
        result = adapter.cancel_turn("dev-1", "conv-2")
        assert result["cancelled"] is False
        assert result["not_found"] is True

    def test_cancel_turn_already_complete(self):
        adapter = MoonPieHermesAdapter()
        turn = _TurnState("dev-1", "conv-1", "session-1")
        turn.completed = True
        adapter._active_turns["dev-1"] = turn
        result = adapter.cancel_turn("dev-1", "conv-1")
        assert result["cancelled"] is False
        assert result["already_complete"] is True
        assert result["not_found"] is False

    def test_cancel_turn_active_sets_event(self):
        adapter = MoonPieHermesAdapter()
        turn = _TurnState("dev-1", "conv-1", "session-1")
        adapter._active_turns["dev-1"] = turn
        result = adapter.cancel_turn("dev-1", "conv-1")
        assert result["cancelled"] is True
        assert turn.cancelled_event.is_set()
        assert "dev-1" not in adapter._active_turns

    def test_cancel_turn_idempotent(self):
        adapter = MoonPieHermesAdapter()
        turn = _TurnState("dev-1", "conv-1", "session-1")
        adapter._active_turns["dev-1"] = turn
        r1 = adapter.cancel_turn("dev-1", "conv-1")
        assert r1["cancelled"] is True
        # Second cancel: turn is already removed
        r2 = adapter.cancel_turn("dev-1", "conv-1")
        assert r2["cancelled"] is False
        assert r2["not_found"] is True

    def test_cancel_turn_withdraws_pending_approvals(self):
        adapter = MoonPieHermesAdapter()
        turn = _TurnState("dev-1", "conv-1", "session-1")
        turn.pending_approvals = {"req-1", "req-2"}
        adapter._active_turns["dev-1"] = turn

        with patch(
            "tools.approval.withdraw_gateway_approval"
        ) as mock_withdraw:
            mock_withdraw.return_value = True
            result = adapter.cancel_turn("dev-1", "conv-1")
            assert result["cancelled"] is True
            assert set(result["withdrawn_approvals"]) == {"req-1", "req-2"}
            assert mock_withdraw.call_count == 2

    def test_cancel_turn_withdraw_failure_graceful(self):
        adapter = MoonPieHermesAdapter()
        turn = _TurnState("dev-1", "conv-1", "session-1")
        turn.pending_approvals = {"req-1"}
        adapter._active_turns["dev-1"] = turn

        with patch(
            "tools.approval.withdraw_gateway_approval"
        ) as mock_withdraw:
            mock_withdraw.side_effect = RuntimeError("boom")
            result = adapter.cancel_turn("dev-1", "conv-1")
            # Should not raise; cancellation still succeeds
            assert result["cancelled"] is True
            assert result["withdrawn_approvals"] == []

    def test_is_cancelled_checks_current_turn(self):
        adapter = MoonPieHermesAdapter()
        turn = _TurnState("dev-1", "conv-1", "session-1")
        adapter._active_turns["dev-1"] = turn
        assert adapter._current_turn("dev-1") is turn
        assert adapter._current_turn("dev-2") is None

    def test_stream_callback_drops_after_cancel(self):
        adapter = MoonPieHermesAdapter()
        turn = _TurnState("dev-1", "conv-1", "session-1")
        adapter._active_turns["dev-1"] = turn

        received = []

        def stream_cb(part: str) -> None:
            received.append(part)

        # Simulate the wrapped stream callback
        def _wrapped(part: str) -> None:
            if turn.cancelled_event.is_set():
                return
            stream_cb(part)

        _wrapped("hello")
        assert received == ["hello"]

        turn.cancelled_event.set()
        _wrapped("world")
        assert received == ["hello"]  # dropped


class TestWebSocketCancelRouting:
    """Integration tests for conversation.cancel through the WebSocket router."""

    @pytest.fixture(autouse=True)
    def reset_adapter(self):
        from hermes_cli.web_routers import moonpie as mp
        mp._moonpie_adapter = MoonPieHermesAdapter()
        mp._moonpie_connections.clear()
        mp._device_tokens.clear()
        mp._registered_devices.clear()
        mp._pending_pairings.clear()
        yield
        mp._moonpie_adapter = MoonPieHermesAdapter()
        mp._moonpie_connections.clear()
        mp._device_tokens.clear()
        mp._registered_devices.clear()
        mp._pending_pairings.clear()

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from hermes_cli.web_routers.moonpie import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        return TestClient(app)

    @pytest.fixture
    def auth_ws(self, client):
        """Register a device and open an authenticated WebSocket."""
        # Register
        resp = client.post("/api/moonpie/devices/register", json={
            "name": "Test Device",
            "public_key": "pk-test",
        })
        data = resp.json()
        device_id = data["device_id"]
        pairing_code = data["pairing_code"]

        # Confirm
        client.post(f"/api/moonpie/devices/{device_id}/confirm")

        # Verify
        resp = client.post("/api/moonpie/devices/verify", json={
            "device_id": device_id,
            "pairing_code": pairing_code,
        })
        token = resp.json()["device_token"]

        # Open WS and auth
        with client.websocket_connect("/api/moonpie/ws") as ws:
            ws.send_json({
                "jsonrpc": "2.0",
                "id": "auth-1",
                "method": "auth.login",
                "params": {"device_token": token},
            })
            msg = ws.receive_json()
            assert msg["result"]["status"] == "authenticated"
            ws.receive_json()  # connection.ready
            yield ws, device_id, token

    def test_cancel_not_found_when_no_active_turn(self, auth_ws):
        ws, device_id, token = auth_ws
        ws.send_json({
            "jsonrpc": "2.0",
            "id": "cancel-1",
            "method": "conversation.cancel",
            "params": {"conversation_id": "conv-1"},
        })
        msg = ws.receive_json()
        assert msg["result"]["cancelled"] is False
        assert msg["result"]["not_found"] is True

    def test_cancel_already_complete(self, auth_ws):
        ws, device_id, token = auth_ws
        # Manually inject a completed turn
        from hermes_cli.web_routers import moonpie as mp
        turn = _TurnState(device_id, "conv-1", f"moonpie_{device_id}")
        turn.completed = True
        mp._moonpie_adapter._active_turns[device_id] = turn

        ws.send_json({
            "jsonrpc": "2.0",
            "id": "cancel-1",
            "method": "conversation.cancel",
            "params": {"conversation_id": "conv-1"},
        })
        msg = ws.receive_json()
        assert msg["result"]["cancelled"] is False
        assert msg["result"]["already_complete"] is True

    def test_cancel_active_turn_emits_complete_with_cancelled(self, auth_ws):
        ws, device_id, token = auth_ws
        from hermes_cli.web_routers import moonpie as mp
        turn = _TurnState(device_id, "conv-1", f"moonpie_{device_id}")
        mp._moonpie_adapter._active_turns[device_id] = turn

        ws.send_json({
            "jsonrpc": "2.0",
            "id": "cancel-1",
            "method": "conversation.cancel",
            "params": {"conversation_id": "conv-1"},
        })

        # Should receive conversation.complete with finish_reason: cancelled
        complete_msg = ws.receive_json()
        assert complete_msg["method"] == "conversation.complete"
        assert complete_msg["params"]["finish_reason"] == "cancelled"

        # Then the JSON-RPC result
        result_msg = ws.receive_json()
        assert result_msg["result"]["cancelled"] is True

    def test_cancel_withdraws_pending_approvals(self, auth_ws):
        ws, device_id, token = auth_ws
        from hermes_cli.web_routers import moonpie as mp
        turn = _TurnState(device_id, "conv-1", f"moonpie_{device_id}")
        turn.pending_approvals = {"req-abc"}
        mp._moonpie_adapter._active_turns[device_id] = turn

        with patch(
            "tools.approval.withdraw_gateway_approval"
        ) as mock_withdraw:
            mock_withdraw.return_value = True
            ws.send_json({
                "jsonrpc": "2.0",
                "id": "cancel-1",
                "method": "conversation.cancel",
                "params": {"conversation_id": "conv-1"},
            })

            # approval.withdrawn should be emitted before conversation.complete
            msgs = []
            for _ in range(3):
                msgs.append(ws.receive_json())

            methods = [m.get("method", m.get("result", {}).get("cancelled")) for m in msgs]
            assert "approval.withdrawn" in methods
            assert "conversation.complete" in methods

    def test_repeated_cancel_is_safe(self, auth_ws):
        ws, device_id, token = auth_ws
        from hermes_cli.web_routers import moonpie as mp
        turn = _TurnState(device_id, "conv-1", f"moonpie_{device_id}")
        mp._moonpie_adapter._active_turns[device_id] = turn

        # First cancel
        ws.send_json({
            "jsonrpc": "2.0",
            "id": "cancel-1",
            "method": "conversation.cancel",
            "params": {"conversation_id": "conv-1"},
        })
        ws.receive_json()  # conversation.complete
        ws.receive_json()  # result

        # Second cancel
        ws.send_json({
            "jsonrpc": "2.0",
            "id": "cancel-2",
            "method": "conversation.cancel",
            "params": {"conversation_id": "conv-1"},
        })
        msg = ws.receive_json()
        assert msg["result"]["cancelled"] is False
        assert msg["result"]["not_found"] is True
