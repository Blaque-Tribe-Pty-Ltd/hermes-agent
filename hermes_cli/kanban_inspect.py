"""Task lifecycle inspection: unified view of execution truth.

One query/view that shows, for each task:
- task ID
- owner/specialist
- state
- dispatch state
- host
- workspace logical ID
- resolved path
- run/session ID
- PID
- liveness
- result/completion record
- failure reason
- dependency/downstream gate status
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from hermes_cli.kanban_delegate_bridge import get_specialist_owner, is_specialist_owned
from hermes_cli.workspace_resolver import resolve_workspace_path

logger = logging.getLogger(__name__)


def _get_kanban_db_path() -> Path:
    from hermes_cli import kanban_db as _kb
    return _kb.kanban_db_path()


def inspect_task(task_id: str) -> Dict[str, Any]:
    """Return a unified lifecycle view for a single task."""
    db_path = _get_kanban_db_path()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    try:
        # Main task record
        task_row = conn.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if not task_row:
            return {"error": f"task {task_id} not found"}

        task = dict(task_row)

        # Latest run
        run_row = conn.execute(
            "SELECT * FROM task_runs WHERE task_id = ? ORDER BY id DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        run = dict(run_row) if run_row else None

        # Events (last 20)
        events = conn.execute(
            "SELECT kind, payload, created_at FROM task_events "
            "WHERE task_id = ? ORDER BY id DESC LIMIT 20",
            (task_id,),
        ).fetchall()
        events = [dict(e) for e in events]

        # Dependency chain (best-effort: table may not exist in all schemas)
        dep_ids: List[str] = []
        dependent_ids: List[str] = []
        try:
            deps = conn.execute(
                "SELECT parent_task_id FROM task_deps WHERE child_task_id = ?",
                (task_id,),
            ).fetchall()
            dep_ids = [d["parent_task_id"] for d in deps]

            dependents = conn.execute(
                "SELECT child_task_id FROM task_deps WHERE parent_task_id = ?",
                (task_id,),
            ).fetchall()
            dependent_ids = [d["child_task_id"] for d in dependents]
        except sqlite3.OperationalError:
            pass

        # Workspace resolution
        workspace_path = task.get("workspace_path") or ""
        workspace_kind = task.get("workspace_kind") or "scratch"
        resolved_path = None
        resolution_error = None
        try:
            if workspace_path:
                resolved_path = resolve_workspace_path(workspace_path)
        except Exception as e:
            resolution_error = str(e)

        # PID liveness
        pid = task.get("worker_pid")
        pid_alive = None
        if pid and run and run.get("status") == "running":
            try:
                os.kill(int(pid), 0)
                pid_alive = True
            except (OSError, ProcessLookupError, ValueError):
                pid_alive = False

        # Dispatch state from events
        dispatch_events = [e for e in events if e["kind"] in ("delegate_dispatch", "delegate_complete", "dispatch_failure")]
        latest_dispatch = dispatch_events[0] if dispatch_events else None

        # Specialist ownership
        specialist = task.get("assignee")
        if not specialist:
            specialist = get_specialist_owner(task.get("domain")) or get_specialist_owner(task.get("tags", [""])[0] if task.get("tags") else "")
        is_specialist = is_specialist_owned(task)

        return {
            "task_id": task_id,
            "title": task.get("title"),
            "specialist_owner": specialist,
            "is_specialist_owned": is_specialist,
            "state": task.get("status"),
            "dispatch_state": _dispatch_state(task, run, latest_dispatch, pid_alive),
            "execution_host": os.uname().nodename if hasattr(os, "uname") else "unknown",
            "workspace_kind": workspace_kind,
            "workspace_path": workspace_path,
            "resolved_workspace_path": resolved_path,
            "workspace_resolution_error": resolution_error,
            "latest_run_id": run.get("id") if run else None,
            "session_id": task.get("session_id") or (run.get("claim_lock") if run else None),
            "worker_pid": pid,
            "pid_alive": pid_alive,
            "result": task.get("result"),
            "failure_reason": task.get("last_failure_error"),
            "dependencies": dep_ids,
            "dependents": dependent_ids,
            "downstream_gate": _downstream_gate(task_id, dep_ids, dependent_ids, conn),
            "events": events,
            "created_at": task.get("created_at"),
            "started_at": task.get("started_at"),
            "completed_at": task.get("completed_at"),
        }
    finally:
        conn.close()


def _dispatch_state(task: dict, run: Optional[dict], latest_dispatch: Optional[dict], pid_alive: Optional[bool]) -> str:
    """Derive a unified dispatch state from task, run, events, and PID liveness."""
    status = task.get("status", "unknown")

    if status == "done" and task.get("result"):
        return "completed_with_result"

    if status == "blocked":
        return f"blocked:{task.get('last_failure_error', 'unknown')}"

    if status == "running":
        if pid_alive is True:
            return "running_with_live_worker"
        if pid_alive is False:
            return "running_with_dead_worker"
        if run and run.get("status") == "running":
            return "running_unverified"
        return "running_no_execution_proof"

    if latest_dispatch:
        if latest_dispatch["kind"] == "delegate_dispatch":
            return "delegate_dispatched"
        if latest_dispatch["kind"] == "delegate_complete":
            return "delegate_completed"
        if latest_dispatch["kind"] == "dispatch_failure":
            return f"delegate_failed:{latest_dispatch.get('payload', '')}"

    if status in ("ready", "todo"):
        return "pending_dispatch"

    return status


def _downstream_gate(task_id: str, deps: List[str], dependents: List[str], conn: sqlite3.Connection) -> Dict[str, Any]:
    """Check if downstream dependents can proceed."""
    if not dependents:
        return {"status": "terminal", "message": "No downstream tasks"}

    # Check if all dependencies of each dependent are satisfied
    blocked = []
    ready = []
    for dep_task_id in dependents:
        try:
            dep_row = conn.execute(
                "SELECT status, result FROM tasks WHERE id = ?", (dep_task_id,)
            ).fetchone()
        except sqlite3.OperationalError:
            dep_row = None
        if dep_row:
            dep_status = dep_row["status"]
            if dep_status == "done":
                ready.append(dep_task_id)
            else:
                blocked.append({"task_id": dep_task_id, "status": dep_status})

    if blocked and not ready:
        return {"status": "blocked", "blocked_dependents": blocked}
    if ready and not blocked:
        return {"status": "all_ready", "ready_dependents": ready}
    return {"status": "partial", "ready": ready, "blocked": blocked}


def inspect_all_tasks(
    *,
    status_filter: Optional[str] = None,
    assignee_filter: Optional[str] = None,
    specialist_only: bool = False,
) -> List[Dict[str, Any]]:
    """Return lifecycle views for all matching tasks."""
    db_path = _get_kanban_db_path()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        where = []
        params = []
        if status_filter:
            where.append("status = ?")
            params.append(status_filter)
        if assignee_filter:
            where.append("assignee = ?")
            params.append(assignee_filter)

        sql = "SELECT id FROM tasks"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC"

        rows = conn.execute(sql, params).fetchall()
        results = []
        for row in rows:
            view = inspect_task(row["id"])
            if specialist_only and not view.get("is_specialist_owned"):
                continue
            results.append(view)
        return results
    finally:
        conn.close()


def format_inspect(view: Dict[str, Any]) -> str:
    """Human-readable formatted output for a task lifecycle view."""
    lines = []
    lines.append(f"{'=' * 60}")
    lines.append(f"Task: {view['task_id']}")
    lines.append(f"Title: {view.get('title', 'N/A')}")
    lines.append(f"Owner: {view.get('specialist_owner', 'none')} (specialist={view.get('is_specialist_owned', False)})")
    lines.append(f"State: {view['state']}")
    lines.append(f"Dispatch: {view['dispatch_state']}")
    lines.append(f"Host: {view['execution_host']}")
    lines.append(f"Workspace: {view['workspace_kind']} → {view.get('resolved_workspace_path') or view['workspace_path']}")
    if view.get('workspace_resolution_error'):
        lines.append(f"  ⚠ Resolution error: {view['workspace_resolution_error']}")
    lines.append(f"Run ID: {view.get('latest_run_id') or 'none'}")
    lines.append(f"Session: {view.get('session_id') or 'none'}")
    lines.append(f"PID: {view.get('worker_pid') or 'none'} (alive={view.get('pid_alive')})")
    lines.append(f"Result: {view.get('result') or 'none'}")
    lines.append(f"Failure: {view.get('failure_reason') or 'none'}")
    lines.append(f"Dependencies: {view['dependencies'] or 'none'}")
    lines.append(f"Dependents: {view['dependents'] or 'none'}")
    downstream = view.get("downstream_gate", {})
    lines.append(f"Downstream: {downstream.get('status')} {downstream.get('message', '')}")
    lines.append(f"Events (last {len(view.get('events', []))}):")
    for ev in view.get("events", [])[:10]:
        payload = ev.get('payload') or ''
        lines.append(f"  [{ev.get('created_at')}] {ev['kind']}: {payload[:80]}")
    lines.append(f"{'=' * 60}")
    return "\n".join(lines)


def cli_inspect(task_id: Optional[str] = None, **filters) -> str:
    """CLI entrypoint for task inspection."""
    if task_id:
        view = inspect_task(task_id)
        if "error" in view:
            return view["error"]
        return format_inspect(view)

    views = inspect_all_tasks(**filters)
    if not views:
        return "No matching tasks found."

    parts = [f"Found {len(views)} task(s):\n"]
    for view in views:
        parts.append(format_inspect(view))
    return "\n".join(parts)
