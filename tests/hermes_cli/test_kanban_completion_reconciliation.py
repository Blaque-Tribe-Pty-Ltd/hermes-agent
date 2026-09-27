"""Tests for hermes_cli.kanban_completion_reconciliation."""

from pathlib import Path
from unittest.mock import patch

import pytest

from hermes_cli.kanban_completion_reconciliation import (
    _worker_produced_output,
    reconcile_unreported_completions,
)


class TestWorkerProducedOutput:
    def test_no_workspace(self):
        assert _worker_produced_output("/nonexistent/path") is False

    def test_with_design_md(self, tmp_path):
        (tmp_path / "DESIGN.md").write_text("# Design")
        assert _worker_produced_output(str(tmp_path)) is True

    def test_with_readme_md(self, tmp_path):
        (tmp_path / "README.md").write_text("# Readme")
        assert _worker_produced_output(str(tmp_path)) is True

    def test_with_any_md(self, tmp_path):
        (tmp_path / "notes.md").write_text("notes")
        assert _worker_produced_output(str(tmp_path)) is True

    def test_no_output_files(self, tmp_path):
        assert _worker_produced_output(str(tmp_path)) is False


class TestReconcileUnreportedCompletions:
    def test_no_running_tasks(self, tmp_path):
        """When no tasks are running, nothing to reconcile."""
        import sqlite3

        db_path = tmp_path / "kanban.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE tasks (
                id TEXT PRIMARY KEY,
                status TEXT,
                workspace_path TEXT,
                workspace_kind TEXT,
                worker_pid INTEGER,
                claim_lock TEXT,
                claim_expires INTEGER,
                worker_started_at TEXT,
                completed_at INTEGER,
                last_heartbeat_at INTEGER
            )
        """)
        conn.commit()

        result = reconcile_unreported_completions(conn)
        assert result == []
        conn.close()

    def test_running_task_with_live_worker(self, tmp_path):
        """A running task with a live worker should NOT be auto-completed."""
        import sqlite3

        db_path = tmp_path / "kanban.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE tasks (
                id TEXT PRIMARY KEY,
                status TEXT,
                workspace_path TEXT,
                workspace_kind TEXT,
                worker_pid INTEGER,
                claim_lock TEXT,
                claim_expires INTEGER,
                worker_started_at TEXT,
                completed_at INTEGER,
                last_heartbeat_at INTEGER
            )
        """)
        # Create a workspace with output
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "DESIGN.md").write_text("# Design")

        # Insert a running task with a fake PID (we'll mock _worker_alive to return True)
        conn.execute(
            "INSERT INTO tasks (id, status, workspace_path, workspace_kind, worker_pid, claim_lock) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("t_test", "running", str(ws), "worktree", 99999, "test:123"),
        )
        conn.commit()

        with patch("hermes_cli.kanban_db_dispatch._worker_alive", return_value=True):
            result = reconcile_unreported_completions(conn)

        assert result == []
        conn.close()

    def test_running_task_with_dead_worker_and_output(self, tmp_path):
        """A running task with a dead worker and output should be auto-completed."""
        import sqlite3

        db_path = tmp_path / "kanban.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        # Need task_runs table for _end_run
        conn.execute("""
            CREATE TABLE tasks (
                id TEXT PRIMARY KEY,
                status TEXT,
                workspace_path TEXT,
                workspace_kind TEXT,
                worker_pid INTEGER,
                claim_lock TEXT,
                claim_expires INTEGER,
                worker_started_at TEXT,
                completed_at INTEGER,
                last_heartbeat_at INTEGER
            )
        """)
        conn.execute("""
            CREATE TABLE task_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT,
                profile TEXT,
                step_key TEXT,
                status TEXT,
                claim_lock TEXT,
                claim_expires INTEGER,
                worker_pid INTEGER,
                max_runtime_seconds INTEGER,
                last_heartbeat_at INTEGER,
                started_at INTEGER,
                ended_at INTEGER,
                outcome TEXT,
                summary TEXT,
                metadata TEXT,
                error TEXT,
                worker_started_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE task_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT,
                run_id INTEGER,
                kind TEXT,
                payload TEXT,
                created_at INTEGER
            )
        """)

        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "DESIGN.md").write_text("# Design")

        conn.execute(
            "INSERT INTO tasks (id, status, workspace_path, workspace_kind, worker_pid, claim_lock) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("t_test", "running", str(ws), "worktree", 99999, "test:123"),
        )
        conn.commit()

        # Mock _worker_alive to return False (dead worker)
        # Also mock _end_run and _append_event to avoid schema dependencies
        with patch("hermes_cli.kanban_db_dispatch._worker_alive", return_value=False):
            with patch("hermes_cli.kanban_db._end_run", return_value=1):
                with patch("hermes_cli.kanban_db._append_event"):
                    result = reconcile_unreported_completions(conn)

        assert "t_test" in result

        # Verify task is now done
        row = conn.execute("SELECT status FROM tasks WHERE id = ?", ("t_test",)).fetchone()
        assert row["status"] == "done"
        conn.close()
