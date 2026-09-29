"""MoonPie native-client API routes.

REST endpoints for device registration, conversation sync, job queries,
approval responses, and a WebSocket gateway for real-time push.

All routes are prefixed with ``/api/moonpie`` by the mounting code in
``web_server.py``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from hermes_cli.moonpie_adapter import MoonPieHermesAdapter
from hermes_state import SessionDB

_log = logging.getLogger("hermes_cli.web_server")
router = APIRouter(prefix="/api/moonpie")


# Lazy SessionDB handle — one per process; thread-safe via WAL.
_moonpie_db: Optional[SessionDB] = None

def _get_moonpie_db() -> SessionDB:
    global _moonpie_db
    if _moonpie_db is None:
        _moonpie_db = SessionDB()
    return _moonpie_db

# ---------------------------------------------------------------------------
# Approval integration (Gate 3, B4)
# ---------------------------------------------------------------------------

from tools.approval import (
    list_gateway_approvals,
    register_gateway_notify,
    resolve_gateway_approval,
    unregister_gateway_notify,
)
from tools.approval_context import set_current_session_key, reset_current_session_key
from gateway.session_context import set_session_vars, clear_session_vars

# ---------------------------------------------------------------------------
# Agent integration — delegated to the Hermes adapter (Gate 2, B3)
# ---------------------------------------------------------------------------

_moonpie_adapter = MoonPieHermesAdapter()


def _approval_session_key(device_id: str) -> str:
    """Canonical approval session key for a MoonPie device.

    The gateway approval system scopes pending approvals by session_key.
    Using a moonpie-prefixed key ensures isolation from other platforms.
    """
    return f"moonpie_{device_id}"

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class DeviceRegisterRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    model: str = Field(default="", max_length=128)
    os_version: str = Field(default="", max_length=64)
    public_key: str = Field(..., min_length=1)


class DeviceRegisterResponse(BaseModel):
    device_id: str
    pairing_code: str
    expires_in: int = 300


class DeviceVerifyRequest(BaseModel):
    device_id: str
    pairing_code: str


class DeviceVerifyResponse(BaseModel):
    device_token: str
    token_type: str = "Bearer"
    expires_in: int = 7776000  # 90 days


class ConversationSummary(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str
    message_count: int = 0


class ConversationDetail(BaseModel):
    id: str
    title: str
    messages: List[Dict[str, Any]] = []
    created_at: str
    updated_at: str


class JobSummary(BaseModel):
    id: str
    title: str
    status: str
    progress: Optional[float] = None
    message: Optional[str] = None
    created_at: str
    completed_at: Optional[str] = None


class JobDetail(JobSummary):
    diff: Optional[str] = None
    result_url: Optional[str] = None
    error: Optional[str] = None


class ApprovalSummary(BaseModel):
    approval_id: str
    command: str
    description: str
    status: str


class ApprovalRespondRequest(BaseModel):
    action: str  # "approve", "reject", "view_diff"


# ---------------------------------------------------------------------------
# In-memory stores (replace with SessionDB / persistent storage)
# ---------------------------------------------------------------------------

_pending_pairings: Dict[str, Dict[str, Any]] = {}
_registered_devices: Dict[str, Dict[str, Any]] = {}
_device_tokens: Dict[str, str] = {}  # token -> device_id

# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------

def _bearer_token_from_header(authorization: str) -> str:
    if authorization.lower().startswith("bearer "):
        return authorization[7:]
    return ""


def _authenticate_device(authorization: str = Header(default="")) -> str:
    token = _bearer_token_from_header(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Missing authorization header")
    device_id = _device_tokens.get(token)
    if not device_id:
        raise HTTPException(status_code=401, detail="Invalid or expired device token")
    return device_id


# ---------------------------------------------------------------------------
# Device registration
# ---------------------------------------------------------------------------

@router.post("/devices/register", response_model=DeviceRegisterResponse)
async def device_register(req: DeviceRegisterRequest):
    """Register a new device and return a pairing code.

    The user must confirm the pairing code through an already-authenticated
    session (dashboard, CLI, or messaging) before the device receives a JWT.
    """
    device_id = f"moonpie-{uuid.uuid4().hex[:12]}"
    pairing_code = uuid.uuid4().hex[:6].upper()

    _pending_pairings[device_id] = {
        "device_id": device_id,
        "name": req.name,
        "model": req.model,
        "os_version": req.os_version,
        "public_key": req.public_key,
        "pairing_code": pairing_code,
        "created_at": time.time(),
        "confirmed": False,
    }

    _log.info("MoonPie device registered: %s (%s)", device_id, req.name)
    return DeviceRegisterResponse(
        device_id=device_id,
        pairing_code=pairing_code,
        expires_in=300,
    )


@router.post("/devices/verify", response_model=DeviceVerifyResponse)
async def device_verify(req: DeviceVerifyRequest):
    """Exchange a confirmed pairing code for a long-lived device JWT."""
    pending = _pending_pairings.get(req.device_id)
    if not pending:
        raise HTTPException(status_code=404, detail="Device not found or pairing expired")

    if pending["pairing_code"] != req.pairing_code:
        raise HTTPException(status_code=400, detail="Invalid pairing code")

    if not pending.get("confirmed"):
        raise HTTPException(status_code=403, detail="Pairing not yet confirmed by user")

    # Generate device JWT (placeholder — replace with real JWT signing)
    token = f"mpdt-{uuid.uuid4().hex}"
    _device_tokens[token] = req.device_id
    _registered_devices[req.device_id] = {
        **_pending_pairings[req.device_id],
        "token": token,
        "confirmed_at": time.time(),
    }
    del _pending_pairings[req.device_id]

    _log.info("MoonPie device verified: %s", req.device_id)
    return DeviceVerifyResponse(device_token=token)


@router.post("/devices/{device_id}/confirm")
async def device_confirm(device_id: str):
    """Confirm a pending device pairing (called by the dashboard / CLI)."""
    pending = _pending_pairings.get(device_id)
    if not pending:
        raise HTTPException(status_code=404, detail="Device not found or pairing expired")

    pending["confirmed"] = True
    _log.info("MoonPie device pairing confirmed: %s", device_id)
    return {"ok": True, "device_id": device_id}


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------

@router.get("/conversations", response_model=List[ConversationSummary])
async def list_conversations(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    search: Optional[str] = Query(None),
    authorization: str =  Header(default=""),
):
    device_id = _authenticate_device(authorization)
    _log.debug("list_conversations for %s", device_id)
    db = _get_moonpie_db()
    rows = db.list_moonpie_conversations(
        device_id, limit=limit, offset=offset, search=search,
    )
    return [
        ConversationSummary(
            id=r["id"],
            title=r["title"],
            message_count=r["message_count"],
            created_at=_ts_to_iso(r["created_at"]),
            updated_at=_ts_to_iso(r["updated_at"]),
        )
        for r in rows
    ]


def _ts_to_iso(ts: float) -> str:
    """Convert a Unix timestamp (REAL) to ISO 8601 UTC string."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


@router.post("/conversations", response_model=ConversationDetail)
async def create_conversation(authorization: str =  Header(default="")):
    device_id = _authenticate_device(authorization)
    db = _get_moonpie_db()
    conv_id = db.create_moonpie_conversation(device_id, title="New Conversation")
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _log.info("MoonPie conversation created: %s by %s", conv_id, device_id)
    return ConversationDetail(
        id=conv_id,
        title="New Conversation",
        messages=[],
        created_at=now_iso,
        updated_at=now_iso,
    )


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(conversation_id: str, authorization: str = Header(default="")):
    device_id = _authenticate_device(authorization)
    _log.debug("get_conversation %s for %s", conversation_id, device_id)
    # TODO: Query SessionDB
    raise HTTPException(status_code=404, detail="Conversation not found")


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

@router.get("/jobs", response_model=List[JobSummary])
async def list_jobs(
    status: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    authorization: str =  Header(default=""),
):
    device_id = _authenticate_device(authorization)
    _log.debug("list_jobs for %s", device_id)
    db = _get_moonpie_db()
    rows = db.list_moonpie_jobs(device_id, limit=limit, offset=offset, status=status)
    return [
        JobSummary(
            id=r["id"],
            title=r["title"],
            status=r["status"],
            progress=r["progress"],
            message=r["message"],
            created_at=_ts_to_iso(r["created_at"]),
            completed_at=_ts_to_iso(r["completed_at"]) if r["completed_at"] else None,
        )
        for r in rows
    ]


@router.get("/jobs/{job_id}", response_model=JobDetail)
async def get_job(job_id: str, authorization: str = Header(default="")):
    device_id = _authenticate_device(authorization)
    _log.debug("get_job %s for %s", job_id, device_id)
    # TODO: Query job state
    raise HTTPException(status_code=404, detail="Job not found")


@router.get("/jobs/{job_id}/diff")
async def get_job_diff(job_id: str, authorization: str = Header(default="")):
    device_id = _authenticate_device(authorization)
    _log.debug("get_job_diff %s for %s", job_id, device_id)
    # TODO: Return job diff
    raise HTTPException(status_code=404, detail="Job diff not found")


# ---------------------------------------------------------------------------
# Approvals
# ---------------------------------------------------------------------------

@router.get("/approvals", response_model=List[ApprovalSummary])
async def list_approvals(
    status: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    authorization: str = Header(default=""),
):
    device_id = _authenticate_device(authorization)
    _log.debug("list_approvals for %s", device_id)
    db = _get_moonpie_db()
    rows = db.list_moonpie_approvals(device_id, limit=limit, offset=offset, status=status)
    return [
        ApprovalSummary(
            approval_id=r["id"],
            command=r["command"],
            description=r["description"],
            status=r["status"],
        )
        for r in rows
    ]


@router.post("/approvals/{approval_id}/respond")
async def respond_approval(approval_id: str, req: ApprovalRespondRequest, authorization: str = Header(default="")):
    device_id = _authenticate_device(authorization)
    session_key = _approval_session_key(device_id)
    _log.info("MoonPie approval response: %s action=%s from %s", approval_id, req.action, device_id)
    resolved = resolve_gateway_approval(session_key, req.action, request_id=approval_id)
    if resolved == 0:
        raise HTTPException(status_code=404, detail="Approval not found or already resolved")

    # Persist the resolution so list_approvals reflects the outcome.
    try:
        db = _get_moonpie_db()
        db.resolve_moonpie_approval(approval_id, status="resolved", action=req.action)
    except Exception:
        _log.warning("Failed to persist approval resolution %s to SessionDB", approval_id, exc_info=True)

    return {"ok": True, "approval_id": approval_id, "action": req.action}


# ---------------------------------------------------------------------------
# WebSocket gateway for native clients
# ---------------------------------------------------------------------------

class _MoonPieConnection:
    """One WebSocket connection from a MoonPie client.

    Authentication is an explicit state transition:
    - ``device_id is None`` → unauthenticated (can only send ``auth.login``)
    - ``device_id is set`` → authenticated (protected operations available)
    """

    def __init__(self, websocket: WebSocket):
        self.websocket = websocket
        self.device_id: Optional[str] = None
        self.connected_at = time.time()
        self.authenticated_at: Optional[float] = None

    @property
    def is_authenticated(self) -> bool:
        return self.device_id is not None

    @property
    def session_key(self) -> str:
        return _approval_session_key(self.device_id) if self.device_id else ""

    async def send_json(self, data: dict):
        try:
            await self.websocket.send_text(json.dumps(data))
        except Exception:
            _log.warning("send_json failed to %s", self.device_id, exc_info=True)

    def _approval_notify(self, approval_data: dict) -> None:
        """Sync callback invoked from the agent thread when a dangerous command
        needs approval.  Bridges to the async WebSocket via the running loop
        and persists the approval record to SessionDB."""
        import asyncio
        approval_id = approval_data.get("request_id", "")
        command = approval_data.get("command", "")
        description = approval_data.get("description", "")

        # Persist the approval so list_approvals survives restart.
        try:
            db = _get_moonpie_db()
            db.create_moonpie_approval(
                device_id=self.device_id or "",
                session_key=self.session_key,
                command=command,
                description=description,
                approval_id=approval_id,
            )
        except Exception:
            _log.warning("Failed to persist approval %s to SessionDB", approval_id, exc_info=True)

        loop = asyncio.get_event_loop()
        asyncio.run_coroutine_threadsafe(
            self.send_json({
                "jsonrpc": "2.0",
                "method": "approval.request",
                "params": {
                    "approval_id": approval_id,
                    "command": command,
                    "description": description,
                    "actions": ["once", "session", "always", "deny"],
                },
            }),
            loop,
        )


_moonpie_connections: Dict[str, _MoonPieConnection] = {}


@router.websocket("/ws")
async def moonpie_websocket(websocket: WebSocket):
    """Bidirectional WebSocket for MoonPie native clients.

    Authentication is message-based via ``auth.login``. No credentials are
    accepted through the URL or query parameters.

    Connection lifecycle:
    1. Socket accepted → unauthenticated state
    2. Client sends ``auth.login`` with device_token in message body
    3. Server validates against shared ``_device_tokens``
    4. On success: state → authenticated, ``connection.ready`` emitted
    5. On failure: socket closed with code 4001

    Reauthentication policy: a second ``auth.login`` on an already-
    authenticated connection is rejected.
    """
    await websocket.accept()

    conn = _MoonPieConnection(websocket)
    _log.info("MoonPie WebSocket accepted (unauthenticated)")

    try:
        await _moonpie_loop(conn)
    except WebSocketDisconnect:
        _log.info("MoonPie WebSocket disconnected: %s",
                  conn.device_id or "unauthenticated")
    finally:
        if conn.device_id:
            _moonpie_connections.pop(conn.device_id, None)
            # Unregister from the gateway approval system so pending approvals
            # don't try to push to a dead connection.  The approval waiter will
            # wake with a "cancelled" cause (fail-closed).
            try:
                unregister_gateway_notify(conn.session_key)
            except Exception:
                _log.debug("unregister_gateway_notify failed for %s", conn.device_id, exc_info=True)


async def _moonpie_loop(conn: _MoonPieConnection):
    """Read JSON-RPC requests from the client and dispatch them.

    Accepts both text and binary JSON frames so native clients can send
    ``URLSessionWebSocketTask.Message.data`` without special-casing.

    Authentication state machine:
    - Unauthenticated connections may only send ``auth.login``
    - After successful auth.login, protected operations become available
    - Re-authentication is rejected
    """
    while True:
        try:
            event = await conn.websocket.receive()
        except WebSocketDisconnect:
            break

        if event.get("type") == "websocket.disconnect":
            break

        if "text" in event and event["text"] is not None:
            raw = event["text"]
        elif "bytes" in event and event["bytes"] is not None:
            try:
                raw = event["bytes"].decode("utf-8", errors="ignore")
            except Exception:
                await conn.send_json({"jsonrpc": "2.0", "error": {"code": -32700, "message": "Invalid binary payload"}})
                continue
        else:
            # Ignore control/noop frames
            continue

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            await conn.send_json({"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}})
            continue

        method = data.get("method")
        req_id = data.get("id")
        params = data.get("params", {})

        # -- Authentication gate ------------------------------------------------
        if method == "auth.login":
            if conn.is_authenticated:
                await conn.send_json({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32001, "message": "Already authenticated"},
                })
                continue

            token = params.get("device_token", "")
            device_id = _device_tokens.get(token)
            if not device_id:
                await conn.send_json({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32002, "message": "Invalid device token"},
                })
                await conn.websocket.close(code=4001, reason="Invalid device token")
                return

            conn.device_id = device_id
            conn.authenticated_at = time.time()
            _moonpie_connections[device_id] = conn
            _log.info("MoonPie WebSocket authenticated: %s", device_id)

            # Register this device with the gateway approval system so
            # dangerous-command prompts are pushed to this connection.
            register_gateway_notify(conn.session_key, conn._approval_notify)

            await conn.send_json({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"status": "authenticated", "device_id": device_id},
            })
            # connection.ready is sent AFTER auth success (see below)
            await conn.send_json({
                "jsonrpc": "2.0",
                "method": "connection.ready",
                "params": {"device_id": device_id},
            })
            continue

        # -- Unauthenticated gate ---------------------------------------------
        if not conn.is_authenticated:
            await conn.send_json({
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32003, "message": "Authentication required"},
            })
            continue

        # -- Protected operations (require authentication) ---------------------
        if method == "conversation.message":
            payload = params.get("payload", {})
            content = payload.get("text", params.get("content", ""))
            conversation_id = params.get("conversation_id", "")

            loop = asyncio.get_running_loop()
            accumulated = []

            def stream_callback(delta: str):
                accumulated.append(delta)
                asyncio.run_coroutine_threadsafe(
                    conn.send_json({
                        "jsonrpc": "2.0",
                        "method": "conversation.delta",
                        "params": {
                            "content": delta,
                            "conversation_id": conversation_id,
                        },
                    }),
                    loop,
                )

            session_tokens = []
            try:
                # Gate 5: Tool visibility — wire tool lifecycle events to the client.
                def tool_event_callback(event_type: str, event_data: dict) -> None:
                    """Sync callback from the agent thread; bridge to async WebSocket."""
                    asyncio.run_coroutine_threadsafe(
                        conn.send_json({
                            "jsonrpc": "2.0",
                            "method": event_type,
                            "params": {
                                **event_data,
                                "conversation_id": conversation_id,
                            },
                        }),
                        loop,
                    )

                # Bind the gateway approval context so dangerous-command guards
                # route approval requests to this MoonPie session.
                session_tokens = set_session_vars(
                    platform="moonpie",
                    session_key=conn.session_key,
                    async_delivery=True,
                )
                set_current_session_key(conn.session_key)
                final_response = await _moonpie_adapter.chat(
                    content,
                    stream_callback=stream_callback,
                    tool_event_callback=tool_event_callback,
                )

                # If the model didn't stream deltas, send one full delta now so the
                # client has content to render before the complete notification.
                if final_response and not accumulated:
                    await conn.send_json({
                        "jsonrpc": "2.0",
                        "method": "conversation.delta",
                        "params": {
                            "content": final_response,
                            "conversation_id": conversation_id,
                        },
                    })

                await conn.send_json({
                    "jsonrpc": "2.0",
                    "method": "conversation.complete",
                    "params": {"conversation_id": conversation_id},
                })

                # Provide a JSON-RPC result for request/response clients (ignored by MoonPie UI)
                await conn.send_json({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {"status": "complete", "content": final_response},
                })

                # Stream TTS audio as binary frames after text completes
                audio = await _moonpie_adapter.synthesize_speech(final_response)
                if audio:
                    await conn.websocket.send_bytes(audio)
                    await conn.send_json({
                        "jsonrpc": "2.0",
                        "method": "tts.status",
                        "params": {"available": True},
                    })

            except RuntimeError as exc:
                _log.error("MoonPie agent turn failed: %s", exc, exc_info=True)
                await conn.send_json({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32000, "message": "Agent not available"},
                })
            except Exception as exc:
                _log.error("MoonPie agent turn failed: %s", exc, exc_info=True)
                await conn.send_json({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32000, "message": str(exc)},
                })
            finally:
                if session_tokens:
                    clear_session_vars(session_tokens)

        elif method == "conversation.start":
            conv_id = f"conv-{uuid.uuid4().hex[:12]}"
            await conn.send_json({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"conversation_id": conv_id},
            })

        elif method == "approval.respond":
            approval_id = params.get("approval_id", "")
            action = params.get("action", "")
            _log.info("MoonPie approval response: %s action=%s from %s", approval_id, action, conn.device_id)
            resolved = resolve_gateway_approval(conn.session_key, action, request_id=approval_id)

            # Persist the resolution so list_approvals reflects the outcome.
            if resolved:
                try:
                    db = _get_moonpie_db()
                    db.resolve_moonpie_approval(approval_id, status="resolved", action=action)
                except Exception:
                    _log.warning("Failed to persist WS approval resolution %s to SessionDB", approval_id, exc_info=True)

            if resolved == 0:
                await conn.send_json({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32004, "message": "Approval not found or already resolved"},
                })
            else:
                await conn.send_json({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {"status": "resolved", "approval_id": approval_id, "action": action},
                })

        elif method == "device.capabilities":
            await conn.send_json({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "workspaces": [],
                    "bridge_available": False,
                },
            })

        elif method == "ping":
            await conn.send_json({"jsonrpc": "2.0", "id": req_id, "result": "pong"})

        else:
            await conn.send_json({
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            })


# ---------------------------------------------------------------------------
# Broadcast helpers (called by the gateway / agent loop)
# ---------------------------------------------------------------------------

async def broadcast_to_device(device_id: str, payload: dict):
    """Push a JSON-RPC notification to a connected MoonPie device."""
    conn = _moonpie_connections.get(device_id)
    if conn:
        await conn.send_json({"jsonrpc": "2.0", "method": payload["method"], "params": payload.get("params", {})})


async def broadcast_to_all(payload: dict):
    """Push a JSON-RPC notification to every connected MoonPie device."""
    for conn in list(_moonpie_connections.values()):
        await conn.send_json({"jsonrpc": "2.0", "method": payload["method"], "params": payload.get("params", {})})

# ---------------------------------------------------------------------------
# Token-auth integration
#
# NOTE: These imports are intentional remaining Hermes coupling.  The
# dashboard_auth subsystem is shared across all Hermes web routers and is
# NOT MoonPie-specific.  Extracting it behind a generic auth seam is
# deferred to a future gate (auth-seam extraction) so that Gate 2 stays
# scoped to AIAgent adapter isolation only.
# ---------------------------------------------------------------------------

from hermes_cli.dashboard_auth.moonpie_provider import MoonPieDeviceProvider
from hermes_cli.dashboard_auth.registry import register_global_provider
from hermes_cli.dashboard_auth.token_auth import register_token_route

# Inject the verify callback so the provider can validate device tokens
MoonPieDeviceProvider.set_verify_callback(lambda token: _device_tokens.get(token))

# Register the provider with the dashboard auth system
register_global_provider(MoonPieDeviceProvider())

# Register exact-match REST paths as token-authable so the middleware
# validates the Authorization header before the cookie gate.
# Dynamic paths (e.g. /conversations/{id}) are validated by the handler.
register_token_route("/api/moonpie/conversations")
register_token_route("/api/moonpie/jobs")
register_token_route("/api/moonpie/approvals")
