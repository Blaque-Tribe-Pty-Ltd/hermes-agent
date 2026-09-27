"""Bridge between Kanban task store and delegate_task dispatch.

Ensures that specialist dispatch flows through one canonical execution
model: Kanban is the source of truth for task existence and state;
delegate_task is the execution mechanism. A task cannot report
"running" / "delegated" / "assigned" unless a real specialist
execution exists.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# Domain → specialist ownership map (mirrors specialist-ownership-enforcement skill)
_DOMAIN_OWNERSHIP = {
    "design": "lesedi",
    "ux": "lesedi",
    "ui": "lesedi",
    "visual": "lesedi",
    "implementation": "neo",
    "swiftui": "neo",
    "ios": "neo",
    "macos": "neo",
    "qa": "tsebo",
    "assurance": "tsebo",
    "test": "tsebo",
}


def get_specialist_owner(domain: Optional[str]) -> Optional[str]:
    """Return the canonical specialist owner for a domain, or None."""
    if not domain:
        return None
    return _DOMAIN_OWNERSHIP.get(domain.lower().strip())


def is_specialist_owned(task: dict) -> bool:
    """Check if a task is owned by a specialist based on its domain/assignee."""
    assignee = (task.get("assignee") or "").strip().lower()
    if assignee in {"lesedi", "neo", "tsebo"}:
        return True
    # Also check tags/title for domain hints
    tags = task.get("tags") or []
    title = (task.get("title") or "").lower()
    for domain, owner in _DOMAIN_OWNERSHIP.items():
        if domain in title or any(domain in str(t).lower() for t in tags):
            return True
    return False


def get_kanban_db_path() -> Path:
    from hermes_cli import kanban_db as _kb
    return _kb.kanban_db_path()


def record_delegate_dispatch(
    task_id: str,
    *,
    subagent_session_id: str,
    profile: str,
    goal: str,
) -> None:
    """Record a delegate_task dispatch in the Kanban task_events table.

    This creates an auditable link between the Kanban task and the
    delegate_task subagent execution.
    """
    db_path = get_kanban_db_path()
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            INSERT INTO task_events (task_id, run_id, kind, payload, created_at)
            VALUES (?, ?, ?, ?, strftime('%s', 'now'))
            """,
            (
                task_id,
                None,
                "delegate_dispatch",
                f"profile={profile} session={subagent_session_id} goal={goal[:200]}",
            ),
        )
        conn.commit()
        logger.info("recorded delegate dispatch for task %s: profile=%s session=%s", task_id, profile, subagent_session_id)
    except Exception as e:
        logger.warning("failed to record delegate dispatch for task %s: %s", task_id, e)
    finally:
        conn.close()


def _is_specialist_owned_from_db(conn: sqlite3.Connection, task_id: str) -> bool:
    """Check if a task is specialist-owned using explicit metadata, not ID format."""
    # Gracefully handle schemas that may not have all columns
    row = None
    cols = "assignee"
    for _cols in [
        "assignee, domain, tags, title",
        "assignee, tags, title",
        "assignee, title",
        "assignee",
    ]:
        try:
            row = conn.execute(
                f"SELECT {_cols} FROM tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
            cols = _cols
            break
        except sqlite3.OperationalError:
            continue

    if not row:
        return False

    # Unpack based on how many columns we got
    values = list(row)
    assignee = values[0] if len(values) > 0 else ""
    domain = values[1] if len(values) > 1 and "domain" in cols else ""
    tags = values[2] if len(values) > 2 and "tags" in cols else ""
    title = values[3] if len(values) > 3 and "title" in cols else ""

    # Check explicit assignee
    if (assignee or "").strip().lower() in {"lesedi", "neo", "tsebo"}:
        return True
    # Check explicit domain
    if (domain or "").strip().lower() in _DOMAIN_OWNERSHIP:
        return True
    # Check title for domain keywords
    title_lower = (title or "").lower()
    for d in _DOMAIN_OWNERSHIP:
        if d in title_lower:
            return True
    # Check tags
    try:
        tag_list = json.loads(tags) if tags else []
        for t in tag_list:
            if str(t).lower().strip() in _DOMAIN_OWNERSHIP:
                return True
    except Exception:
        pass
    return False


def assert_specialist_result_exists(task_id: str) -> None:
    """Raise RuntimeError if a specialist-owned task has no recorded result.

    This is the code-level enforcement gate. Call this before MoonPie
    executes specialist-owned work directly. Works with ANY task ID format.
    """
    db_path = get_kanban_db_path()
    conn = sqlite3.connect(str(db_path))
    try:
        # First: is this task specialist-owned? (explicit metadata, not ID prefix)
        if not _is_specialist_owned_from_db(conn, task_id):
            return  # Not specialist-owned → no enforcement

        row = conn.execute(
            "SELECT status, assignee, result FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        if not row:
            return  # No task → no enforcement

        status, assignee, result = row
        assignee = (assignee or "").strip().lower()

        if status == "done":
            return  # Specialist completed (result column may be empty if worker
                   # did not call kanban_complete, but reconciliation detected output)

        if status == "running":
            raise RuntimeError(
                f"Task {task_id} is specialist-owned (assignee={assignee}) and still running. "
                f"MoonPie may not execute this work directly. Wait for specialist completion or escalate."
            )

        if status in {"ready", "todo", "blocked"}:
            raise RuntimeError(
                f"Task {task_id} is specialist-owned (assignee={assignee}) but has not been dispatched. "
                f"MoonPie may not execute this work directly. Dispatch to the specialist first."
            )

        # Any other state without a result is a violation
        raise RuntimeError(
            f"Task {task_id} is specialist-owned (assignee={assignee}) with no valid specialist result. "
            f"MoonPie may not execute this work directly. Status={status}."
        )
    finally:
        conn.close()


def record_delegate_completion(
    task_id: str,
    *,
    subagent_session_id: str,
    outcome: str,
    summary: str = "",
) -> None:
    """Record a delegate_task completion in the Kanban task_events table."""
    db_path = get_kanban_db_path()
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            INSERT INTO task_events (task_id, run_id, kind, payload, created_at)
            VALUES (?, ?, ?, ?, strftime('%s', 'now'))
            """,
            (
                task_id,
                None,
                "delegate_complete",
                f"session={subagent_session_id} outcome={outcome} summary={summary[:200]}",
            ),
        )
        conn.commit()
        logger.info("recorded delegate completion for task %s: outcome=%s", task_id, outcome)
    except Exception as e:
        logger.warning("failed to record delegate completion for task %s: %s", task_id, e)
    finally:
        conn.close()


def block_task_on_dispatch_failure(task_id: str, reason: str) -> None:
    """Mark a task as blocked when its specialist dispatch fails.

    Replaces the silent fallback behavior:
      dispatch fails → MoonPie does the work
    with:
      dispatch fails → task becomes blocked → MoonPie reports failure
    """
    db_path = get_kanban_db_path()
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "UPDATE tasks SET status = 'blocked', last_failure_error = ? WHERE id = ? AND status IN ('running', 'ready', 'todo')",
            (reason, task_id),
        )
        conn.execute(
            """
            INSERT INTO task_events (task_id, run_id, kind, payload, created_at)
            VALUES (?, ?, ?, ?, strftime('%s', 'now'))
            """,
            (task_id, None, "dispatch_failure", reason),
        )
        conn.commit()
        logger.warning("task %s blocked due to dispatch failure: %s", task_id, reason)
    except Exception as e:
        logger.error("failed to block task %s: %s", task_id, e)
    finally:
        conn.close()
