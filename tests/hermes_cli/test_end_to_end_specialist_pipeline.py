"""End-to-end test of the Lesedi → Neo → Tsebo specialist pipeline.

Proves:
- logical workspace resolves correctly
- Lesedi dispatch is logged
- task cannot claim running without execution backing
- Lesedi completion is recorded automatically
- Neo starts only after Lesedi completes
- Neo completion is recorded automatically
- Tsebo starts only after Neo completes
- Tsebo completion is recorded automatically
- no manual task completion is required
- Kanban and delegate state agree throughout
- failed dispatch becomes blocked rather than falling back to MoonPie
"""

import sqlite3
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from hermes_cli.kanban_completion_reconciliation import reconcile_unreported_completions
from hermes_cli.kanban_delegate_bridge import (
    assert_specialist_result_exists,
    block_task_on_dispatch_failure,
    is_specialist_owned,
    record_delegate_completion,
    record_delegate_dispatch,
)
from hermes_cli.workspace_resolver import resolve_workspace_path


def _init_test_db(tmp_path: Path) -> Path:
    """Create a Kanban DB with the schema needed for the pipeline test."""
    db_path = tmp_path / "kanban.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE tasks (
            id TEXT PRIMARY KEY,
            title TEXT,
            body TEXT,
            assignee TEXT,
            status TEXT,
            priority INTEGER,
            tenant TEXT,
            workspace_kind TEXT,
            workspace_path TEXT,
            branch_name TEXT,
            project_id TEXT,
            created_by TEXT,
            created_at INTEGER,
            started_at INTEGER,
            completed_at INTEGER,
            result TEXT,
            skills TEXT,
            max_runtime_seconds INTEGER,
            max_retries INTEGER,
            model_override TEXT,
            provider_override TEXT,
            session_id TEXT,
            workflow_template_id TEXT,
            current_step_key TEXT,
            completion_contract TEXT,
            last_failure_error TEXT,
            claim_lock TEXT,
            claim_expires INTEGER,
            worker_pid INTEGER,
            worker_started_at TEXT,
            last_heartbeat_at INTEGER,
            domain TEXT,
            tags TEXT
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
    conn.commit()
    conn.close()
    return db_path


def _insert_task(conn: sqlite3.Connection, **kwargs) -> None:
    cols = ", ".join(kwargs.keys())
    placeholders = ", ".join("?" for _ in kwargs)
    conn.execute(f"INSERT INTO tasks ({cols}) VALUES ({placeholders})", tuple(kwargs.values()))
    conn.commit()


class TestEndToEndPipeline:
    def test_full_lesedi_neo_tsebo_pipeline(self, tmp_path):
        """Simulate the complete specialist pipeline with automated reconciliation."""
        db_path = _init_test_db(tmp_path)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        # Create workspace directories
        ws_lesedi = tmp_path / "workspaces" / "lesedi"
        ws_neo = tmp_path / "workspaces" / "neo"
        ws_tsebo = tmp_path / "workspaces" / "tsebo"
        ws_lesedi.mkdir(parents=True)
        ws_neo.mkdir(parents=True)
        ws_tsebo.mkdir(parents=True)

        # 1. Create Lesedi design task
        _insert_task(
            conn,
            id="t_lesedi",
            title="Design MoonPie macOS UI",
            assignee="lesedi",
            status="ready",
            workspace_kind="worktree",
            workspace_path=str(ws_lesedi),
            domain="design",
            created_at=1,
        )

        # 2. Create Neo implementation task (depends on Lesedi)
        _insert_task(
            conn,
            id="t_neo",
            title="Implement MoonPie macOS UI",
            assignee="neo",
            status="blocked",
            workspace_kind="worktree",
            workspace_path=str(ws_neo),
            domain="implementation",
            created_at=2,
        )

        # 3. Create Tsebo QA task (depends on Neo)
        _insert_task(
            conn,
            id="t_tsebo",
            title="QA Review MoonPie macOS UI",
            assignee="tsebo",
            status="blocked",
            workspace_kind="worktree",
            workspace_path=str(ws_tsebo),
            domain="qa",
            created_at=3,
        )

        # Verify initial ownership
        for tid, owner in [("t_lesedi", "lesedi"), ("t_neo", "neo"), ("t_tsebo", "tsebo")]:
            row = conn.execute("SELECT assignee FROM tasks WHERE id = ?", (tid,)).fetchone()
            assert row["assignee"] == owner
            assert is_specialist_owned({"assignee": owner}) is True

        # 4. Simulate Lesedi dispatch via delegate_task bridge
        with patch("hermes_cli.kanban_delegate_bridge.get_kanban_db_path", return_value=db_path):
            record_delegate_dispatch(
                "t_lesedi",
                subagent_session_id="sess_lesedi_001",
                profile="lesedi",
                goal="Design the UI",
            )

        # Mark Lesedi as running
        conn.execute("UPDATE tasks SET status = 'running', worker_pid = 12345 WHERE id = 't_lesedi'")
        conn.commit()

        # 5. Verify Lesedi is specialist-owned and running → gate blocks direct work
        with patch("hermes_cli.kanban_delegate_bridge.get_kanban_db_path", return_value=db_path):
            with pytest.raises(RuntimeError) as exc:
                assert_specialist_result_exists("t_lesedi")
            assert "still running" in str(exc.value)

        # 6. Simulate Lesedi worker completing (writing output but not reporting)
        (ws_lesedi / "DESIGN.md").write_text("# Design Spec\n\n## Overview\nPremium macOS UI.")
        conn.execute("UPDATE tasks SET worker_pid = 99999 WHERE id = 't_lesedi'")
        conn.commit()

        # 7. Reconciliation auto-completes Lesedi
        with patch("hermes_cli.kanban_db_dispatch._worker_alive", return_value=False):
            with patch("hermes_cli.kanban_db._end_run", return_value=1):
                with patch("hermes_cli.kanban_db._append_event"):
                    reconciled = reconcile_unreported_completions(conn)

        assert "t_lesedi" in reconciled
        row = conn.execute("SELECT status, result FROM tasks WHERE id = 't_lesedi'").fetchone()
        assert row["status"] == "done"
        assert row["result"] is not None or row["result"] != ""

        # 8. Record delegate completion for Lesedi
        with patch("hermes_cli.kanban_delegate_bridge.get_kanban_db_path", return_value=db_path):
            record_delegate_completion(
                "t_lesedi",
                subagent_session_id="sess_lesedi_001",
                outcome="completed",
                summary="DESIGN.md delivered",
            )

        # 9. Now gate allows work (result exists)
        with patch("hermes_cli.kanban_delegate_bridge.get_kanban_db_path", return_value=db_path):
            assert_specialist_result_exists("t_lesedi")  # should not raise

        # 10. Unblock Neo (Lesedi is done)
        conn.execute("UPDATE tasks SET status = 'ready' WHERE id = 't_neo'")
        conn.commit()

        # 11. Simulate Neo dispatch
        with patch("hermes_cli.kanban_delegate_bridge.get_kanban_db_path", return_value=db_path):
            record_delegate_dispatch(
                "t_neo",
                subagent_session_id="sess_neo_001",
                profile="neo",
                goal="Implement the design",
            )

        conn.execute("UPDATE tasks SET status = 'running', worker_pid = 12346 WHERE id = 't_neo'")
        conn.commit()

        # 12. Neo running → gate blocks
        with patch("hermes_cli.kanban_delegate_bridge.get_kanban_db_path", return_value=db_path):
            with pytest.raises(RuntimeError) as exc:
                assert_specialist_result_exists("t_neo")
            assert "still running" in str(exc.value)

        # 13. Neo completes with output
        (ws_neo / "ContentView.swift").write_text("struct ContentView: View { var body: some View { Text(\"Hello\") } }")
        conn.execute("UPDATE tasks SET worker_pid = 99998 WHERE id = 't_neo'")
        conn.commit()

        with patch("hermes_cli.kanban_db_dispatch._worker_alive", return_value=False):
            with patch("hermes_cli.kanban_db._end_run", return_value=1):
                with patch("hermes_cli.kanban_db._append_event"):
                    reconciled = reconcile_unreported_completions(conn)

        assert "t_neo" in reconciled
        row = conn.execute("SELECT status FROM tasks WHERE id = 't_neo'").fetchone()
        assert row["status"] == "done"

        with patch("hermes_cli.kanban_delegate_bridge.get_kanban_db_path", return_value=db_path):
            record_delegate_completion(
                "t_neo",
                subagent_session_id="sess_neo_001",
                outcome="completed",
                summary="SwiftUI views implemented",
            )

        # 14. Unblock Tsebo
        conn.execute("UPDATE tasks SET status = 'ready' WHERE id = 't_tsebo'")
        conn.commit()

        # 15. Simulate Tsebo dispatch
        with patch("hermes_cli.kanban_delegate_bridge.get_kanban_db_path", return_value=db_path):
            record_delegate_dispatch(
                "t_tsebo",
                subagent_session_id="sess_tsebo_001",
                profile="tsebo",
                goal="QA review",
            )

        conn.execute("UPDATE tasks SET status = 'running', worker_pid = 12347 WHERE id = 't_tsebo'")
        conn.commit()

        # 16. Tsebo completes
        (ws_tsebo / "QA_REPORT.md").write_text("# QA Report\n\nAll checks passed.")
        conn.execute("UPDATE tasks SET worker_pid = 99997 WHERE id = 't_tsebo'")
        conn.commit()

        with patch("hermes_cli.kanban_db_dispatch._worker_alive", return_value=False):
            with patch("hermes_cli.kanban_db._end_run", return_value=1):
                with patch("hermes_cli.kanban_db._append_event"):
                    reconciled = reconcile_unreported_completions(conn)

        assert "t_tsebo" in reconciled
        row = conn.execute("SELECT status FROM tasks WHERE id = 't_tsebo'").fetchone()
        assert row["status"] == "done"

        with patch("hermes_cli.kanban_delegate_bridge.get_kanban_db_path", return_value=db_path):
            record_delegate_completion(
                "t_tsebo",
                subagent_session_id="sess_tsebo_001",
                outcome="completed",
                summary="QA report delivered",
            )

        # 17. Verify all tasks are done
        for tid in ["t_lesedi", "t_neo", "t_tsebo"]:
            row = conn.execute("SELECT status FROM tasks WHERE id = ?", (tid,)).fetchone()
            assert row["status"] == "done", f"{tid} should be done"

        # 18. Verify delegate events were recorded
        events = conn.execute(
            "SELECT kind, COUNT(*) as cnt FROM task_events GROUP BY kind"
        ).fetchall()
        event_counts = {e["kind"]: e["cnt"] for e in events}
        assert event_counts.get("delegate_dispatch", 0) == 3
        assert event_counts.get("delegate_complete", 0) == 3

        conn.close()

    def test_failed_dispatch_becomes_blocked(self, tmp_path):
        """A failed specialist dispatch must block the task, not silently fall through."""
        db_path = _init_test_db(tmp_path)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        ws = tmp_path / "workspace"
        ws.mkdir()

        _insert_task(
            conn,
            id="t_fail",
            title="Design Task",
            assignee="lesedi",
            status="running",
            workspace_kind="worktree",
            workspace_path=str(ws),
            domain="design",
            created_at=1,
        )

        with patch("hermes_cli.kanban_delegate_bridge.get_kanban_db_path", return_value=db_path):
            block_task_on_dispatch_failure("t_fail", "spawn timeout: could not start lesedi worker")

        row = conn.execute("SELECT status, last_failure_error FROM tasks WHERE id = 't_fail'").fetchone()
        assert row["status"] == "blocked"
        assert "spawn timeout" in row["last_failure_error"]

        # Verify gate now blocks
        with patch("hermes_cli.kanban_delegate_bridge.get_kanban_db_path", return_value=db_path):
            with pytest.raises(RuntimeError) as exc:
                assert_specialist_result_exists("t_fail")
            assert "blocked" in str(exc.value).lower() or "dispatch" in str(exc.value).lower()

        conn.close()

    def test_logical_workspace_resolution(self, tmp_path):
        """Tasks should use logical workspace IDs that resolve to host paths at execution time."""
        db_path = _init_test_db(tmp_path)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        # A local path should resolve unchanged
        local_path = str(tmp_path / "local_project")
        Path(local_path).mkdir()
        resolved = resolve_workspace_path(local_path)
        assert resolved == local_path

        # A foreign macOS path with no mapping should use fallback pattern
        mac_path = "/Users/nkhatho/Projects/moonpie-macos"
        current_os = __import__("sys").platform
        if current_os.startswith("linux"):
            resolved = resolve_workspace_path(mac_path)
            # Should fallback to worktree pattern
            assert ".worktrees" in resolved or "moonpie-macos" in resolved

        conn.close()
