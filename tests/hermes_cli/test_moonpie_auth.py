"""Tests for MoonPie REST and WebSocket authentication boundary (Gate 1).

Verifies:
- REST endpoints require Authorization header (Bearer token)
- Query-string credentials are NOT accepted
- WebSocket rejects unauthenticated connections (no guest/fallback IDs)
- Valid device tokens authenticate successfully
- Token routes are registered with the dashboard auth system
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.testclient import TestClient as StarletteTestClient

from hermes_cli.dashboard_auth.moonpie_provider import MoonPieDeviceProvider
from hermes_cli.dashboard_auth.token_auth import (
    clear_token_routes, is_token_route, register_token_route,
)
from hermes_cli.web_routers import moonpie as moonpie_router
from hermes_state import SessionDB


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean_token_routes():
    """Ensure token-route registry is clean before each test."""
    clear_token_routes()
    yield
    clear_token_routes()


@pytest.fixture
def device_token_store(tmp_path):
    """Shared temporary SessionDB for tests."""
    from hermes_cli.web_routers import moonpie as mp
    db_path = tmp_path / "test_state.db"
    db = SessionDB(db_path=db_path)
    original_db = mp._moonpie_db
    mp._moonpie_db = db
    yield db
    mp._moonpie_db = original_db


@pytest.fixture
def moonpie_app(device_token_store):
    """Minimal FastAPI app with moonpie routes mounted."""
    app = FastAPI()
    app.include_router(moonpie_router.router)

    # Token routes are registered by moonpie.py at import time;
    # re-register them because clean_token_routes clears them.
    register_token_route("/api/moonpie/conversations")
    register_token_route("/api/moonpie/jobs")
    register_token_route("/api/moonpie/approvals")

    return app


@pytest.fixture
def client(moonpie_app):
    """HTTP test client."""
    return TestClient(moonpie_app)


@pytest.fixture
def valid_token(device_token_store):
    """Create and return a valid device token."""
    token = "mpdt-test-valid-token"
    device_id = "moonpie-test-device"
    device_token_store.register_moonpie_device(device_id, name="Test", public_key="pk", pairing_code="PC")
    device_token_store.confirm_moonpie_device(device_id)
    device_token_store.store_moonpie_device_token(token, device_id)
    return token


# ---------------------------------------------------------------------------
# REST Authentication Tests
# ---------------------------------------------------------------------------

class TestRestAuth:
    """REST endpoint authentication boundary."""

    def test_no_auth_returns_401(self, client):
        """No credentials → 401."""
        response = client.get("/api/moonpie/conversations")
        assert response.status_code == 401
        assert response.json()["detail"] == "Missing authorization header"

    def test_malformed_header_returns_401(self, client):
        """Malformed Authorization header → 401."""
        response = client.get(
            "/api/moonpie/conversations",
            headers={"Authorization": "not-bearer-format"},
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "Missing authorization header"

    def test_empty_bearer_returns_401(self, client):
        """Empty bearer value → 401."""
        response = client.get(
            "/api/moonpie/conversations",
            headers={"Authorization": "Bearer "},
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "Missing authorization header"

    def test_invalid_token_returns_401(self, client):
        """Invalid/expired token → 401."""
        response = client.get(
            "/api/moonpie/conversations",
            headers={"Authorization": "Bearer invalid-token"},
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid or expired device token"

    def test_valid_token_authenticates(self, client, valid_token):
        """Valid bearer token → request reaches handler."""
        response = client.get(
            "/api/moonpie/conversations",
            headers={"Authorization": f"Bearer {valid_token}"},
        )
        # The handler returns an empty list when authenticated
        assert response.status_code == 200
        assert response.json() == []

    def test_query_param_token_ignored(self, client, valid_token):
        """Token in query string must NOT authenticate."""
        response = client.get(
            f"/api/moonpie/conversations?token={valid_token}",
        )
        # Should be 401 because query param is not the Authorization header
        assert response.status_code == 401

    def test_header_overrides_query_param(self, client, valid_token):
        """Only Authorization header determines auth; query params ignored."""
        response = client.get(
            f"/api/moonpie/conversations?token=invalid",
            headers={"Authorization": f"Bearer {valid_token}"},
        )
        # Header should win
        assert response.status_code == 200
        assert response.json() == []

    def test_device_registration_no_auth(self, client):
        """Device registration endpoint requires no auth."""
        response = client.post(
            "/api/moonpie/devices/register",
            json={"name": "Test Device", "model": "Test", "os_version": "1.0", "public_key": "pk"},
        )
        assert response.status_code == 200
        assert "device_id" in response.json()
        assert "pairing_code" in response.json()

    def test_approvals_list_auth_required(self, client, valid_token):
        """Approvals list requires auth."""
        response = client.get("/api/moonpie/approvals")
        assert response.status_code == 401

        response = client.get(
            "/api/moonpie/approvals",
            headers={"Authorization": f"Bearer {valid_token}"},
        )
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Device Pairing Confirmation Auth Tests (Regression)
# ---------------------------------------------------------------------------

class TestDeviceConfirmAuth:
    """Device pairing confirmation must require a trusted authority (Gate 1 regression)."""

    def test_confirm_unauthenticated_returns_401(self, client):
        """Public self-confirmation without any auth must fail."""
        # Register a device
        resp = client.post(
            "/api/moonpie/devices/register",
            json={"name": "Test", "public_key": "pk"},
        )
        device_id = resp.json()["device_id"]

        # Attempt to confirm without auth
        resp = client.post(f"/api/moonpie/devices/{device_id}/confirm")
        assert resp.status_code == 401
        assert resp.json()["detail"] == "Unauthorized"

    def test_confirm_invalid_token_returns_401(self, client):
        """Confirmation with an invalid/untrusted token must fail."""
        resp = client.post(
            "/api/moonpie/devices/register",
            json={"name": "Test", "public_key": "pk"},
        )
        device_id = resp.json()["device_id"]

        resp = client.post(
            f"/api/moonpie/devices/{device_id}/confirm",
            headers={"Authorization": "Bearer invalid-token"},
        )
        assert resp.status_code == 401
        assert resp.json()["detail"] == "Unauthorized"

    def test_confirm_with_valid_device_token_is_rejected(self, client, device_token_store):
        """A device credential is not an operator credential."""
        # Seed a root/trusted device token
        device_token_store.register_moonpie_device("root-device", name="Root", public_key="pk", pairing_code="ROOT")
        device_token_store.confirm_moonpie_device("root-device")
        device_token_store.store_moonpie_device_token("mpdt-root-token", "root-device")

        # Register a new device
        resp = client.post(
            "/api/moonpie/devices/register",
            json={"name": "Test", "public_key": "pk"},
        )
        data = resp.json()
        device_id = data["device_id"]
        # A trusted client still cannot approve another client.
        resp = client.post(
            f"/api/moonpie/devices/{device_id}/confirm",
            headers={"Authorization": "Bearer mpdt-root-token"},
        )
        assert resp.status_code == 401

    def test_dashboard_operator_can_confirm_and_list(self, client):
        from hermes_cli.web_server import _SESSION_HEADER_NAME, _SESSION_TOKEN

        registered = client.post(
            "/api/moonpie/devices/register",
            json={
                "name": "Operator Test",
                "model": "MacBookPro18,2",
                "os_version": "15.1",
                "public_key": "pk",
            },
        ).json()
        headers = {_SESSION_HEADER_NAME: _SESSION_TOKEN}

        pending = client.get("/api/moonpie/devices/pending", headers=headers)
        assert pending.status_code == 200
        assert pending.json() == [{
            "device_id": registered["device_id"],
            "name": "Operator Test",
            "model": "MacBookPro18,2",
            "os_version": "15.1",
            "confirmed": False,
            "created_at": pending.json()[0]["created_at"],
        }]

        confirmed = client.post(
            f"/api/moonpie/devices/{registered['device_id']}/confirm", headers=headers,
        )
        assert confirmed.status_code == 200

        verified = client.post(
            "/api/moonpie/devices/verify",
            json={
                "device_id": registered["device_id"],
                "pairing_code": registered["pairing_code"],
            },
        )
        assert verified.status_code == 200
        assert verified.json()["expires_in"] == 90 * 24 * 60 * 60

        replay = client.post(
            "/api/moonpie/devices/verify",
            json={
                "device_id": registered["device_id"],
                "pairing_code": registered["pairing_code"],
            },
        )
        assert replay.status_code == 404

    def test_pairing_code_and_device_token_are_hashed_at_rest(self, client, device_token_store):
        from hermes_cli.web_server import _SESSION_HEADER_NAME, _SESSION_TOKEN

        registered = client.post(
            "/api/moonpie/devices/register",
            json={"name": "Hash Test", "public_key": "pk"},
        ).json()
        row = device_token_store._read_one(
            "SELECT pairing_code FROM moonpie_devices WHERE id = ?",
            (registered["device_id"],),
        )
        assert row["pairing_code"].startswith("pbkdf2_sha256$")
        assert registered["pairing_code"] not in row["pairing_code"]

        headers = {_SESSION_HEADER_NAME: _SESSION_TOKEN}
        client.post(f"/api/moonpie/devices/{registered['device_id']}/confirm", headers=headers)
        verified = client.post(
            "/api/moonpie/devices/verify",
            json={"device_id": registered["device_id"], "pairing_code": registered["pairing_code"]},
        ).json()
        token_row = device_token_store._read_one(
            "SELECT token, expires_at FROM moonpie_device_tokens WHERE device_id = ?",
            (registered["device_id"],),
        )
        assert token_row["token"].startswith("sha256$")
        assert verified["device_token"] not in token_row["token"]
        assert token_row["expires_at"] > 0

    def test_expired_pairing_is_not_listed_or_confirmed(self, client, device_token_store):
        from hermes_cli.web_server import _SESSION_HEADER_NAME, _SESSION_TOKEN

        registered = client.post(
            "/api/moonpie/devices/register",
            json={"name": "Expired", "public_key": "pk"},
        ).json()
        device_token_store._write_sql(
            "UPDATE moonpie_devices SET pairing_expires_at = ? WHERE id = ?",
            (0, registered["device_id"]),
        )
        headers = {_SESSION_HEADER_NAME: _SESSION_TOKEN}

        listed = client.get("/api/moonpie/devices", headers=headers)
        assert listed.status_code == 200
        assert listed.json() == []
        confirmed = client.post(
            f"/api/moonpie/devices/{registered['device_id']}/confirm", headers=headers,
        )
        assert confirmed.status_code == 410

    def test_verify_before_confirm_fails(self, client):
        """Exchanging pairing code before confirmation must fail."""
        resp = client.post(
            "/api/moonpie/devices/register",
            json={"name": "Test", "public_key": "pk"},
        )
        data = resp.json()
        device_id = data["device_id"]
        pairing_code = data["pairing_code"]

        resp = client.post(
            "/api/moonpie/devices/verify",
            json={"device_id": device_id, "pairing_code": pairing_code},
        )
        assert resp.status_code == 403
        assert "not yet confirmed" in resp.json()["detail"].lower()

    def test_full_flow_token_auth_login(self, client, device_token_store):
        """Token from verified device successfully authenticates via WebSocket auth.login."""
        # Register
        resp = client.post(
            "/api/moonpie/devices/register",
            json={"name": "Test", "public_key": "pk"},
        )
        data = resp.json()
        device_id = data["device_id"]
        pairing_code = data["pairing_code"]

        from hermes_cli.web_server import _SESSION_HEADER_NAME, _SESSION_TOKEN

        # Confirm with the dashboard operator token.
        client.post(
            f"/api/moonpie/devices/{device_id}/confirm",
            headers={_SESSION_HEADER_NAME: _SESSION_TOKEN},
        )

        # Verify
        resp = client.post(
            "/api/moonpie/devices/verify",
            json={"device_id": device_id, "pairing_code": pairing_code},
        )
        token = resp.json()["device_token"]

        # Token is already stored by device_verify; authenticate via WebSocket
        with client.websocket_connect("/api/moonpie/ws") as ws:
            ws.send_json({
                "jsonrpc": "2.0",
                "id": "auth-1",
                "method": "auth.login",
                "params": {"device_token": token},
            })
            msg = ws.receive_json()
            assert msg["result"]["status"] == "authenticated"
            assert msg["result"]["device_id"] == device_id


# ---------------------------------------------------------------------------
# WebSocket Authentication Tests
# ---------------------------------------------------------------------------

class TestWebSocketAuth:
    """WebSocket authentication boundary (Gate 1.1 message-based auth)."""

    # Note: Gate 1.1 changed WebSocket auth from query-string to message-based.
    # The connection is always accepted; authentication happens via auth.login.
    # These tests verify the new contract.  Old query-string tests removed.

    def test_ws_connection_accepted_without_token(self, client):
        """WebSocket connection is accepted even without credentials."""
        with client.websocket_connect("/api/moonpie/ws") as ws:
            # Connection accepted; protected ops are rejected, not the socket
            ws.send_json({"jsonrpc": "2.0", "id": 1, "method": "ping"})
            msg = json.loads(ws.receive_text())
            assert msg["error"]["code"] == -32003

    def test_ws_query_token_ignored(self, client, valid_token):
        """Query-string token does NOT authenticate; auth.login required."""
        with client.websocket_connect(f"/api/moonpie/ws?device_token={valid_token}") as ws:
            ws.send_json({"jsonrpc": "2.0", "id": 1, "method": "ping"})
            msg = json.loads(ws.receive_text())
            assert msg["error"]["code"] == -32003

    def test_ws_auth_login_flow(self, client, valid_token):
        """Valid auth.login → authenticated state → connection.ready."""
        with client.websocket_connect("/api/moonpie/ws") as ws:
            ws.send_json({"jsonrpc": "2.0", "id": 1, "method": "auth.login", "params": {"device_token": valid_token}})
            msg1 = json.loads(ws.receive_text())
            assert msg1["result"]["status"] == "authenticated"
            assert msg1["result"]["device_id"] == "moonpie-test-device"

            msg2 = json.loads(ws.receive_text())
            assert msg2["method"] == "connection.ready"
            assert msg2["params"]["device_id"] == "moonpie-test-device"
            # Must NOT be a guest- or fallback- ID
            assert not msg2["params"]["device_id"].startswith("guest-")
            assert not msg2["params"]["device_id"].startswith("fallback-")

    def test_ws_no_guest_id_after_auth(self, client, valid_token):
        """Authenticated device_id must never be a synthetic guest/fallback ID."""
        with client.websocket_connect("/api/moonpie/ws") as ws:
            ws.send_json({"jsonrpc": "2.0", "id": 1, "method": "auth.login", "params": {"device_token": valid_token}})
            msg = json.loads(ws.receive_text())
            device_id = msg["result"]["device_id"]
            assert not device_id.startswith("guest")
            assert not device_id.startswith("fallback")


# ---------------------------------------------------------------------------
# Token Route Registration Tests
# ---------------------------------------------------------------------------

class TestTokenRouteRegistration:
    """Token route registration verification."""

    def test_exact_routes_registered(self, clean_token_routes):
        """Exact-match moonpie paths are registered as token routes."""
        register_token_route("/api/moonpie/conversations")
        register_token_route("/api/moonpie/jobs")
        register_token_route("/api/moonpie/approvals")

        assert is_token_route("/api/moonpie/conversations")
        assert is_token_route("/api/moonpie/jobs")
        assert is_token_route("/api/moonpie/approvals")

    def test_dynamic_routes_not_registered(self, clean_token_routes):
        """Dynamic paths are NOT registered as token routes (exact match only)."""
        register_token_route("/api/moonpie/conversations")

        assert not is_token_route("/api/moonpie/conversations/abc123")
        assert not is_token_route("/api/moonpie/jobs/456")

    def test_provider_registered(self, device_token_store):
        """MoonPieDeviceProvider is registered and validates tokens."""
        provider = MoonPieDeviceProvider()
        # Set callback to use the real DB-backed verifier
        MoonPieDeviceProvider.set_verify_callback(
            moonpie_router._verify_device_token
        )

        device_token_store.register_moonpie_device("device-1", name="Test", public_key="pk", pairing_code="PC")
        device_token_store.confirm_moonpie_device("device-1")
        device_token_store.store_moonpie_device_token("test-token", "device-1")
        principal = provider.verify_token(token="test-token")
        assert principal is not None
        assert principal.principal == "device-1"

        assert provider.verify_token(token="bad-token") is None


# ---------------------------------------------------------------------------
# Credential Leakage Tests
# ---------------------------------------------------------------------------

class TestCredentialLeakage:
    """Verify credentials do not leak through unintended channels."""

    def test_token_not_in_error_response(self, client):
        """Error responses must not echo the provided token."""
        response = client.get(
            "/api/moonpie/conversations",
            headers={"Authorization": "Bearer leaked-secret-token"},
        )
        assert response.status_code == 401
        body = response.text
        assert "leaked-secret-token" not in body

    def test_token_not_in_query_logged(self, client, valid_token, caplog):
        """Query parameters should not be used for auth (no log leakage)."""
        # This test documents that query-param auth is intentionally not supported
        response = client.get(
            f"/api/moonpie/conversations?token={valid_token}",
        )
        assert response.status_code == 401
        # The token should not appear in application logs
        for record in caplog.records:
            assert valid_token not in record.message

class TestWebSocketAuthLogin:
    """Gate 1.1: Message-based WebSocket authentication via auth.login."""

    @pytest.fixture
    def valid_token(self, device_token_store):
        token = "mpdt-valid-token-12345"
        device_id = "device-test-123"
        device_token_store.register_moonpie_device(device_id, name="Test", public_key="pk", pairing_code="PC")
        device_token_store.confirm_moonpie_device(device_id)
        device_token_store.store_moonpie_device_token(token, device_id)
        return token

    def test_query_string_token_ignored(self, client, valid_token):
        """Token in WebSocket URL must not authenticate."""
        with client.websocket_connect(f"/api/moonpie/ws?device_token={valid_token}") as ws:
            # Connection should be accepted but unauthenticated
            # Protected operation should be rejected
            ws.send_json({"jsonrpc": "2.0", "id": 1, "method": "ping"})
            msg = json.loads(ws.receive_text())
            assert msg.get("error", {}).get("code") == -32003

    def test_unauthenticated_no_protected_access(self, client):
        """Unauthenticated socket cannot access protected operations."""
        with client.websocket_connect("/api/moonpie/ws") as ws:
            ws.send_json({"jsonrpc": "2.0", "id": 1, "method": "conversation.message", "params": {"text": "hello"}})
            msg = json.loads(ws.receive_text())
            assert msg["error"]["code"] == -32003
            assert "Authentication required" in msg["error"]["message"]

    def test_invalid_auth_login_rejected(self, client):
        """Invalid auth.login credentials are rejected."""
        with client.websocket_connect("/api/moonpie/ws") as ws:
            ws.send_json({"jsonrpc": "2.0", "id": 1, "method": "auth.login", "params": {"device_token": "invalid"}})
            msg = json.loads(ws.receive_text())
            assert msg["error"]["code"] == -32002
            assert "Invalid device token" in msg["error"]["message"]

    def test_valid_auth_login_authenticates(self, client, valid_token):
        """Valid auth.login transitions to authenticated state."""
        with client.websocket_connect("/api/moonpie/ws") as ws:
            ws.send_json({"jsonrpc": "2.0", "id": 1, "method": "auth.login", "params": {"device_token": valid_token}})
            msg = json.loads(ws.receive_text())
            assert msg.get("result", {}).get("status") == "authenticated"
            assert msg.get("result", {}).get("device_id") == "device-test-123"

    def test_connection_ready_after_auth(self, client, valid_token):
        """connection.ready is sent only after successful authentication."""
        with client.websocket_connect("/api/moonpie/ws") as ws:
            # Before auth: no connection.ready
            ws.send_json({"jsonrpc": "2.0", "id": 1, "method": "ping"})
            msg = json.loads(ws.receive_text())
            assert msg.get("error", {}).get("code") == -32003

            # Auth
            ws.send_json({"jsonrpc": "2.0", "id": 2, "method": "auth.login", "params": {"device_token": valid_token}})
            msg1 = json.loads(ws.receive_text())
            assert msg1["result"]["status"] == "authenticated"

            # After auth: connection.ready arrives
            msg2 = json.loads(ws.receive_text())
            assert msg2.get("method") == "connection.ready"
            assert msg2.get("params", {}).get("device_id") == "device-test-123"

    def test_no_guest_id_after_auth(self, client, valid_token):
        """Authenticated device_id must never be a synthetic guest/fallback ID."""
        with client.websocket_connect("/api/moonpie/ws") as ws:
            ws.send_json({"jsonrpc": "2.0", "id": 1, "method": "auth.login", "params": {"device_token": valid_token}})
            msg = json.loads(ws.receive_text())
            device_id = msg["result"]["device_id"]
            assert not device_id.startswith("guest")
            assert not device_id.startswith("fallback")

    def test_repeated_auth_login_rejected(self, client, valid_token):
        """Second auth.login on authenticated connection is rejected."""
        with client.websocket_connect("/api/moonpie/ws") as ws:
            # First login
            ws.send_json({"jsonrpc": "2.0", "id": 1, "method": "auth.login", "params": {"device_token": valid_token}})
            msg = json.loads(ws.receive_text())
            assert msg["result"]["status"] == "authenticated"
            # Drain connection.ready notification pushed after auth
            json.loads(ws.receive_text())

            # Second login
            ws.send_json({"jsonrpc": "2.0", "id": 2, "method": "auth.login", "params": {"device_token": valid_token}})
            msg = json.loads(ws.receive_text())
            assert msg["error"]["code"] == -32001
            assert "Already authenticated" in msg["error"]["message"]

    def test_auth_login_no_token_leak(self, client, valid_token):
        """auth.login response must not echo the token back."""
        with client.websocket_connect("/api/moonpie/ws") as ws:
            ws.send_json({"jsonrpc": "2.0", "id": 1, "method": "auth.login", "params": {"device_token": valid_token}})
            msg = json.loads(ws.receive_text())
            result = msg.get("result", {})
            assert "token" not in result
            assert valid_token not in json.dumps(msg)

    def test_protected_ping_after_auth(self, client, valid_token):
        """After auth, protected operations work."""
        with client.websocket_connect("/api/moonpie/ws") as ws:
            ws.send_json({"jsonrpc": "2.0", "id": 1, "method": "auth.login", "params": {"device_token": valid_token}})
            msg = json.loads(ws.receive_text())  # auth result
            msg = json.loads(ws.receive_text())  # connection.ready

            ws.send_json({"jsonrpc": "2.0", "id": 2, "method": "ping"})
            msg = json.loads(ws.receive_text())
            assert msg.get("result") == "pong"


class TestCredentialLeakage:
    """Verify that bearer credentials never leak into observable channels."""

    @pytest.fixture
    def valid_token(self, device_token_store):
        token = "mpdt-valid-token-12345"
        device_id = "device-test-123"
        device_token_store.register_moonpie_device(device_id, name="Test", public_key="pk", pairing_code="PC")
        device_token_store.confirm_moonpie_device(device_id)
        device_token_store.store_moonpie_device_token(token, device_id)
        return token

    def test_token_not_in_error_response(self, client, valid_token):
        """Error responses must never contain the token."""
        response = client.get(
            "/api/moonpie/conversations",
            headers={"Authorization": f"Bearer {valid_token}INVALID"},
        )
        assert valid_token not in response.text

    def test_token_not_in_query_logged(self, client, caplog):
        """Tokens supplied in query strings must not appear in gateway logs."""
        with caplog.at_level("INFO", logger="hermes_cli.web_server"):
            client.get("/api/moonpie/conversations?token=mpdt-secret-leak-test")
        # httpx client logs naturally contain the URL; we only assert on gateway logs
        assert "mpdt-secret-leak-test" not in caplog.text
