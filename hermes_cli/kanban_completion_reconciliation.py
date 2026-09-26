"""Reconcile Kanban task state with actual worker output.

Detects workers that completed their work but failed to call kanban_complete.
This is a SAFETY NET, not the primary completion path. The authoritative
completion signal is an explicit kanban_complete call from the worker.

Rules:
- Worker alive + running = leave alone
- Worker dead + completion contract satisfied (expected artifacts exist) = auto-complete
- Worker dead + partial output only = mark failed (do NOT claim done)
- Worker dead + no output = mark failed
- Never fabricate result text
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Optional

# Imported lazily to avoid circular deps at module load time
_worker_alive = None

def _get_worker_alive():
    global _worker_alive
    # Re-import every time to respect test patches and avoid stale references.
    from hermes_cli.kanban_db_dispatch import _worker_alive as _wa
    _worker_alive = _wa
    return _worker_alive

_log = logging.getLogger(__name__)

# Expected artifacts by completion contract type
_COMPLETION_CONTRACTS = {
    "design": ["DESIGN.md", "DESIGN_SPEC.md"],
    "implementation": [".swift", ".py", ".rs", ".go", ".ts", ".js"],
    "qa": ["QA_REPORT.md", "REPORT.md", "report.md"],
    "default": ["README.md", "output.md", "result.md", ".md"],
}


def _detect_contract_type(task: dict) -> str:
    """Infer the expected completion contract from task metadata.

    Priority:
    1. Explicit domain field (if set and recognized)
    2. Title/body/tag keyword matching
    """
    domain = (task.get("domain") or "").lower().strip()
    # 1. Explicit domain takes precedence
    if domain in ("design", "ux", "ui", "visual"):
        return "design"
    if domain in ("implementation", "swiftui", "swift", "code", "develop"):
        return "implementation"
    if domain in ("qa", "quality", "review", "test", "assurance"):
        return "qa"

    # 2. Fallback to keyword matching in title/body/tags
    title = (task.get("title") or "").lower()
    body = (task.get("body") or "").lower()
    tags = task.get("tags") or []
    if isinstance(tags, str):
        try:
            import json
            tags = json.loads(tags)
        except Exception:
            tags = []
    tag_str = " ".join(str(t).lower() for t in tags)
    combined = f"{domain} {title} {body} {tag_str}"
    if any(k in combined for k in ("design", "ux", "ui", "visual")):
        return "design"
    if any(k in combined for k in ("implement", "swiftui", "swift", "code", "develop")):
        return "implementation"
    if any(k in combined for k in ("qa", "quality", "review", "test", "assurance")):
        return "qa"
    return "default"


def _worker_produced_output(workspace_path: str) -> bool:
    """Check if the worker's workspace contains ANY evidence of work."""
    wp = Path(workspace_path).expanduser()
    if not wp.exists():
        return False

    # Check for known output files
    for indicator in ["DESIGN.md", "README.md", "REPORT.md", "report.md", "output.md", "result.md", ".git/index"]:
        if (wp / indicator).exists():
            return True

    # Check for any .md files
    try:
        if list(wp.glob("**/*.md")):
            return True
    except Exception:
        pass

    # Check for source code files
    try:
        code_exts = {".swift", ".py", ".rs", ".go", ".ts", ".js", ".java", ".kt", ".cpp", ".c", ".h"}
        for f in wp.rglob("*"):
            if f.is_file() and f.suffix in code_exts:
                return True
    except Exception:
        pass

    return False


def _completion_contract_satisfied(workspace_path: str, contract_type: str) -> bool:
    """Check if the workspace satisfies the expected completion contract."""
    wp = Path(workspace_path).expanduser()
    if not wp.exists():
        return False

    expected = _COMPLETION_CONTRACTS.get(contract_type, _COMPLETION_CONTRACTS["default"])
    for artifact in expected:
        if artifact.startswith("."):
            # Extension check
            if list(wp.rglob(f"*{artifact}")):
                return True
        else:
            if (wp / artifact).exists():
                return True
            # Also check subdirectories
            if list(wp.rglob(f"**/{artifact}")):
                return True

    return False


def _auto_complete_task(
    conn: sqlite3.Connection,
    task_id: str,
    workspace_path: str,
    *,
    board: Optional[str] = None,
) -> bool:
    """Auto-complete a task whose completion contract is satisfied.

    Returns True if the task was auto-completed.
    """
    try:
        from hermes_cli import kanban_db as _kb

        with _kb.write_txn(conn):
            # Re-verify the task is still running before mutating
            try:
                row = conn.execute(
                    "SELECT status, domain, title, tags, body FROM tasks WHERE id = ?", (task_id,)
                ).fetchone()
            except sqlite3.OperationalError:
                try:
                    row = conn.execute(
                        "SELECT status, title, tags, body FROM tasks WHERE id = ?", (task_id,)
                    ).fetchone()
                except sqlite3.OperationalError:
                    row = conn.execute(
                        "SELECT status FROM tasks WHERE id = ?", (task_id,)
                    ).fetchone()
            if not row or row["status"] != "running":
                return False

            task = dict(row)
            for col in ("domain", "title", "body", "tags"):
                if col not in task:
                    task[col] = ""
            contract_type = _detect_contract_type(task)

            # Must satisfy completion contract, not just have any file
            if not _completion_contract_satisfied(workspace_path, contract_type):
                _log.info(
                    "kanban reconciliation: task %s has output but contract %s not satisfied; leaving for manual review",
                    task_id, contract_type,
                )
                return False

            # Mark as completed
            import time as _time
            conn.execute(
                "UPDATE tasks SET status = 'done', completed_at = ?, "
                "claim_lock = NULL, claim_expires = NULL, "
                "worker_pid = NULL, worker_started_at = NULL, "
                "last_heartbeat_at = NULL "
                "WHERE id = ? AND status = 'running'",
                (int(_time.time()), task_id),
            )

            # Close the run
            run_id = _kb._end_run(
                conn, task_id,
                outcome="completed",
                status="completed",
                error=None,
                metadata={"auto_completed": True, "reason": "completion_contract_satisfied", "contract": contract_type},
            )

            _kb._append_event(
                conn, task_id, "auto_completed",
                {"reason": "worker output satisfied completion contract", "workspace": workspace_path, "contract": contract_type},
                run_id=run_id,
            )

        _log.info(
            "kanban reconciliation: auto-completed task %s (contract %s satisfied in %s)",
            task_id, contract_type, workspace_path,
        )
        return True
    except Exception as exc:
        _log.warning(
            "kanban reconciliation: failed to auto-complete task %s: %s",
            task_id, exc,
        )
        return False


def _mark_task_failed(
    conn: sqlite3.Connection,
    task_id: str,
    reason: str,
) -> bool:
    """Mark a task as failed when the worker died without satisfying completion."""
    try:
        from hermes_cli import kanban_db as _kb

        with _kb.write_txn(conn):
            try:
                row = conn.execute(
                    "SELECT status FROM tasks WHERE id = ?", (task_id,)
                ).fetchone()
            except Exception:
                return False
            if not row or row["status"] != "running":
                return False

            conn.execute(
                "UPDATE tasks SET status = 'failed', last_failure_error = ?, "
                "claim_lock = NULL, claim_expires = NULL, "
                "worker_pid = NULL, worker_started_at = NULL, "
                "last_heartbeat_at = NULL "
                "WHERE id = ? AND status = 'running'",
                (reason, task_id),
            )

            run_id = _kb._end_run(
                conn, task_id,
                outcome="failed",
                status="failed",
                error=reason,
                metadata={"auto_failed": True, "reason": reason},
            )

            _kb._append_event(
                conn, task_id, "auto_failed",
                {"reason": reason},
                run_id=run_id,
            )

        _log.info(
            "kanban reconciliation: marked task %s as failed: %s",
            task_id, reason,
        )
        return True
    except Exception as exc:
        _log.warning(
            "kanban reconciliation: failed to mark task %s as failed: %s",
            task_id, exc,
        )
        return False


def reconcile_unreported_completions(
    conn: sqlite3.Connection,
    *,
    board: Optional[str] = None,
) -> list[str]:
    """Find running tasks whose workers exited and reconcile their state.

    Returns list of task IDs that were auto-completed.
    """
    auto_completed: list[str] = []
    marked_failed: list[str] = []

    # Query running tasks. Gracefully handle schemas without all columns.
    try:
        rows = conn.execute(
            "SELECT id, workspace_path, workspace_kind, worker_pid, claim_lock, domain, title, tags, body "
            "FROM tasks "
            "WHERE status = 'running' AND worker_pid IS NOT NULL"
        ).fetchall()
    except sqlite3.OperationalError:
        try:
            rows = conn.execute(
                "SELECT id, workspace_path, workspace_kind, worker_pid, claim_lock, title, tags, body "
                "FROM tasks "
                "WHERE status = 'running' AND worker_pid IS NOT NULL"
            ).fetchall()
        except sqlite3.OperationalError:
            try:
                rows = conn.execute(
                    "SELECT id, workspace_path, workspace_kind, worker_pid, claim_lock "
                    "FROM tasks "
                    "WHERE status = 'running' AND worker_pid IS NOT NULL"
                ).fetchall()
            except Exception as exc:
                _log.debug("kanban reconciliation: query failed: %s", exc)
                return auto_completed
    except Exception as exc:
        _log.debug("kanban reconciliation: query failed: %s", exc)
        return auto_completed

    _log.debug("kanban reconciliation: found %d running tasks with worker_pid", len(rows))

    for row in rows:
        task_id = row["id"]
        workspace_path = row["workspace_path"]
        worker_pid = row["worker_pid"]

        _log.debug("kanban reconciliation: checking task %s pid=%s wp=%s", task_id, worker_pid, workspace_path)

        if not workspace_path:
            _log.debug("kanban reconciliation: task %s has no workspace_path, skipping", task_id)
            continue

        # Check if the worker is still alive
        started_row = conn.execute(
            "SELECT worker_started_at FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        started_at = started_row["worker_started_at"] if started_row else None

        alive = _get_worker_alive()(worker_pid, started_at)
        _log.debug("kanban reconciliation: task %s worker_alive=%s", task_id, alive)
        if alive:
            continue  # Worker is still running, don't interfere

        # Worker is dead — assess what it left behind
        task = dict(row)
        # Ensure missing columns default to empty strings
        for col in ("domain", "title", "body", "tags"):
            if col not in task:
                task[col] = ""
        contract_type = _detect_contract_type(task)
        contract_satisfied = _completion_contract_satisfied(workspace_path, contract_type)
        has_output = _worker_produced_output(workspace_path)
        _log.debug("kanban reconciliation: task %s contract=%s satisfied=%s has_output=%s", task_id, contract_type, contract_satisfied, has_output)

        if contract_satisfied:
            if _auto_complete_task(conn, task_id, workspace_path, board=board):
                auto_completed.append(task_id)
        elif has_output:
            # Output exists but contract NOT satisfied = partial work
            if _mark_task_failed(
                conn, task_id,
                f"Worker died with partial output but completion contract ({contract_type}) not satisfied",
            ):
                marked_failed.append(task_id)
        else:
            # No output at all
            if _mark_task_failed(
                conn, task_id,
                "Worker died without producing any output",
            ):
                marked_failed.append(task_id)

    if marked_failed:
        _log.info("kanban reconciliation: marked %d task(s) as failed: %s", len(marked_failed), marked_failed)

    _log.debug("kanban reconciliation: auto_completed=%s", auto_completed)
    return auto_completed
