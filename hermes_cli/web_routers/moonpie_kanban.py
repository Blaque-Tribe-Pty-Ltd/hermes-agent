"""Device-authenticated Kanban access for the MoonPie native client.

All task reads and mutations delegate to the existing Kanban dashboard
handlers/database helpers. MoonPie gets no shadow task store or alternate
transition rules.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect

from hermes_cli.web_routers.moonpie import _authenticate_device, _verify_device_token

log = logging.getLogger("hermes_cli.web_server")
router = APIRouter(prefix="/kanban", tags=["moonpie-kanban"])


def _api():
    try:
        return importlib.import_module("plugins.kanban.dashboard.plugin_api")
    except (ImportError, ModuleNotFoundError) as exc:
        raise HTTPException(status_code=503, detail="Moonbeam Kanban is unavailable.") from exc


def _client_safe(value: Any) -> Any:
    """Remove server-local filesystem paths from otherwise canonical task data."""
    if isinstance(value, dict):
        return {
            key: _client_safe(item)
            for key, item in value.items()
            if key not in {"default_workdir"} and not key.endswith("_path")
        }
    if isinstance(value, list):
        return [_client_safe(item) for item in value]
    return value


@router.get("/board")
def board_snapshot(
    device_id: str = Depends(_authenticate_device),
    board: Optional[str] = Query(None),
    tenant: Optional[str] = Query(None),
    include_archived: bool = Query(False),
):
    api = _api()
    try:
        result = api.get_board(
            tenant=tenant,
            include_archived=include_archived,
            board=board,
            workflow_template_id=None,
            current_step_key=None,
        )
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("MoonPie Kanban snapshot failed for device %s", device_id)
        raise HTTPException(status_code=503, detail="Could not load the Moonbeam task board.") from exc
    return _client_safe(result)


@router.get("/tasks/{task_id}")
def task_detail(
    task_id: str,
    device_id: str = Depends(_authenticate_device),
    board: Optional[str] = Query(None),
):
    del device_id
    return _client_safe(_api().get_task(task_id, board=board, run_state_type=None, run_state_name=None))


@router.post("/tasks", status_code=201)
def create_task(
    payload: dict[str, Any] = Body(...),
    device_id: str = Depends(_authenticate_device),
    board: Optional[str] = Query(None),
):
    api = _api()
    task_input = api.CreateTaskBody.model_validate(payload)
    with api._board_conn(board) as (resolved_board, conn), api._value_error_400():
        task_id = api.kanban_db.create_task(
            conn,
            created_by=f"moonpie:{device_id}",
            board=resolved_board,
            **task_input.model_dump(),
        )
        task = api.kanban_db.get_task(conn, task_id)
        return _client_safe({"task": api._task_dict(task) if task else None})


@router.patch("/tasks/{task_id}")
def update_task(
    task_id: str,
    payload: dict[str, Any] = Body(...),
    device_id: str = Depends(_authenticate_device),
    board: Optional[str] = Query(None),
):
    del device_id
    api = _api()
    update = api.UpdateTaskBody.model_validate(payload)
    return _client_safe(api.update_task(task_id, payload=update, board=board))


@router.post("/tasks/{task_id}/comments")
def add_comment(
    task_id: str,
    payload: dict[str, Any] = Body(...),
    device_id: str = Depends(_authenticate_device),
    board: Optional[str] = Query(None),
):
    api = _api()
    comment = api.CommentBody.model_validate(payload)
    comment.author = f"moonpie:{device_id}"
    return api.add_comment(task_id, payload=comment, board=board)


@router.get("/assignees")
def list_assignees(
    device_id: str = Depends(_authenticate_device),
    board: Optional[str] = Query(None),
):
    del device_id
    return _api().get_assignees(board=board)


@router.get("/projects")
def list_projects(device_id: str = Depends(_authenticate_device)):
    del device_id
    return _client_safe(_api().list_kanban_projects())


def _event_cursor(websocket: WebSocket) -> Optional[int]:
    raw = websocket.query_params.get("since")
    if raw is None:
        return None
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return None


@router.websocket("/events")
async def stream_events(websocket: WebSocket):
    """Authenticate in the first frame, then replay ordered task_events by cursor."""
    await websocket.accept()
    tail = None
    try:
        try:
            raw = await asyncio.wait_for(websocket.receive_text(), timeout=10)
            if len(raw) > 4096:
                raise ValueError("authentication frame too large")
            auth = json.loads(raw)
            if not isinstance(auth, dict):
                raise ValueError("authentication frame must be an object")
            params = auth.get("params", {})
            token = params.get("device_token") if auth.get("method") == "auth.login" and isinstance(params, dict) else None
            device_id = _verify_device_token(token) if isinstance(token, str) else None
            if not device_id:
                await websocket.close(code=1008, reason="MoonPie device authentication required")
                return
        except (asyncio.TimeoutError, ValueError, json.JSONDecodeError):
            await websocket.close(code=1008, reason="Invalid MoonPie authentication frame")
            return

        api = _api()
        try:
            board = api._resolve_board(websocket.query_params.get("board"))
        except HTTPException:
            await websocket.close(code=1008, reason="Unknown Kanban board")
            return

        tail = api._EventTail(board)
        cursor = _event_cursor(websocket)
        if cursor is None:
            cursor = await tail.latest()
        await websocket.send_json({
            "jsonrpc": "2.0",
            "id": auth.get("id"),
            "result": {"status": "authenticated"},
        })
        await websocket.send_json({"jsonrpc": "2.0", "method": "kanban.ready", "params": {"cursor": cursor}})

        while True:
            try:
                message = await asyncio.wait_for(websocket.receive(), timeout=0.3)
                if message.get("type") == "websocket.disconnect":
                    return
            except asyncio.TimeoutError:
                pass
            cursor, events = await tail.poll(cursor)
            if events:
                await websocket.send_json({
                    "jsonrpc": "2.0",
                    "method": "kanban.events",
                    "params": {"events": _client_safe(events), "cursor": cursor},
                })
    except WebSocketDisconnect:
        return
    except asyncio.CancelledError:
        return
    except Exception:
        log.exception("MoonPie Kanban event stream failed")
        try:
            await websocket.close()
        except Exception:
            pass
    finally:
        if tail is not None:
            await tail.shutdown()
