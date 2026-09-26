"""Tests for hermes_cli.kanban_delegate_bridge."""

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from hermes_cli.kanban_delegate_bridge import (
    _DOMAIN_OWNERSHIP,
    assert_specialist_result_exists,
    block_task_on_dispatch_failure,
    get_specialist_owner,
    is_specialist_owned,
    record_delegate_dispatch,
)


class TestGetSpecialistOwner:
    def test_known_domains(self):
        assert get_specialist_owner("design") == "lesedi"
        assert get_specialist_owner("ux") == "lesedi"
        assert get_specialist_owner("implementation") == "neo"
        assert get_specialist_owner("swiftui") == "neo"
        assert get_specialist_owner("qa") == "tsebo"

    def test_case_insensitive(self):
        assert get_specialist_owner("DESIGN") == "lesedi"
        assert get_specialist_owner("  SwiftUI  ") == "neo"

    def test_unknown_domain(self):
        assert get_specialist_owner("marketing") is None
        assert get_specialist_owner("") is None
        assert get_specialist_owner(None) is None


class TestIsSpecialistOwned:
    def test_by_assignee(self):
        assert is_specialist_owned({"assignee": "lesedi"}) is True
        assert is_specialist_owned({"assignee": "neo"}) is True
        assert is_specialist_owned({"assignee": "tsebo"}) is True
        assert is_specialist_owned({"assignee": "moonpie"}) is False

    def test_by_title(self):
        assert is_specialist_owned({"title": "UI Redesign for macOS"}) is True
        assert is_specialist_owned({"title": "SwiftUI Implementation"}) is True
        assert is_specialist_owned({"title": "QA Review"}) is True
        assert is_specialist_owned({"title": "General cleanup"}) is False

    def test_by_tags(self):
        assert is_specialist_owned({"tags": ["design", "ux"]}) is True
        assert is_specialist_owned({"tags": ["backend", "api"]}) is False


class TestAssertSpecialistResultExists:
    def test_no_task(self, tmp_path):
        db_path = tmp_path / "kanban.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, status TEXT, assignee TEXT, result TEXT)")
        conn.commit()
        conn.close()

        with patch("hermes_cli.kanban_delegate_bridge.get_kanban_db_path", return_value=db_path):
            # No task → no enforcement
            assert_specialist_result_exists("t_missing")

    def test_done_with_result(self, tmp_path):
        db_path = tmp_path / "kanban.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, status TEXT, assignee TEXT, result TEXT)")
        conn.execute(
            "INSERT INTO tasks VALUES (?, ?, ?, ?)",
            ("t_done", "done", "lesedi", "DESIGN.md delivered"),
        )
        conn.commit()
        conn.close()

        with patch("hermes_cli.kanban_delegate_bridge.get_kanban_db_path", return_value=db_path):
            assert_specialist_result_exists("t_done")

    def test_running_raises(self, tmp_path):
        db_path = tmp_path / "kanban.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, status TEXT, assignee TEXT, result TEXT)")
        conn.execute(
            "INSERT INTO tasks VALUES (?, ?, ?, ?)",
            ("t_running", "running", "lesedi", None),
        )
        conn.commit()
        conn.close()

        with patch("hermes_cli.kanban_delegate_bridge.get_kanban_db_path", return_value=db_path):
            with pytest.raises(RuntimeError) as exc:
                assert_specialist_result_exists("t_running")
            assert "still running" in str(exc.value)

    def test_ready_raises(self, tmp_path):
        db_path = tmp_path / "kanban.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, status TEXT, assignee TEXT, result TEXT)")
        conn.execute(
            "INSERT INTO tasks VALUES (?, ?, ?, ?)",
            ("t_ready", "ready", "neo", None),
        )
        conn.commit()
        conn.close()

        with patch("hermes_cli.kanban_delegate_bridge.get_kanban_db_path", return_value=db_path):
            with pytest.raises(RuntimeError) as exc:
                assert_specialist_result_exists("t_ready")
            assert "has not been dispatched" in str(exc.value)

    def test_non_specialist_no_enforcement(self, tmp_path):
        db_path = tmp_path / "kanban.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, status TEXT, assignee TEXT, result TEXT)")
        conn.execute(
            "INSERT INTO tasks VALUES (?, ?, ?, ?)",
            ("t_moonpie", "running", "moonpie", None),
        )
        conn.commit()
        conn.close()

        with patch("hermes_cli.kanban_delegate_bridge.get_kanban_db_path", return_value=db_path):
            assert_specialist_result_exists("t_moonpie")


class TestBlockTaskOnDispatchFailure:
    def test_blocks_task(self, tmp_path):
        db_path = tmp_path / "kanban.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, status TEXT, last_failure_error TEXT)")
        conn.execute("CREATE TABLE task_events (id INTEGER PRIMARY KEY, task_id TEXT, run_id INTEGER, kind TEXT, payload TEXT, created_at INTEGER)")
        conn.execute("INSERT INTO tasks VALUES (?, ?, ?)", ("t_fail", "running", None))
        conn.commit()
        conn.close()

        with patch("hermes_cli.kanban_delegate_bridge.get_kanban_db_path", return_value=db_path):
            block_task_on_dispatch_failure("t_fail", "spawn timeout")

        conn = sqlite3.connect(str(db_path))
        row = conn.execute("SELECT status, last_failure_error FROM tasks WHERE id = ?", ("t_fail",)).fetchone()
        assert row[0] == "blocked"
        assert row[1] == "spawn timeout"

        event = conn.execute("SELECT kind, payload FROM task_events WHERE task_id = ?", ("t_fail",)).fetchone()
        assert event[0] == "dispatch_failure"
        assert event[1] == "spawn timeout"
        conn.close()


class TestRecordDelegateDispatch:
    def test_records_event(self, tmp_path):
        db_path = tmp_path / "kanban.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE task_events (id INTEGER PRIMARY KEY, task_id TEXT, run_id INTEGER, kind TEXT, payload TEXT, created_at INTEGER)")
        conn.commit()
        conn.close()

        with patch("hermes_cli.kanban_delegate_bridge.get_kanban_db_path", return_value=db_path):
            record_delegate_dispatch(
                "t_123",
                subagent_session_id="sess_abc",
                profile="lesedi",
                goal="Design the UI",
            )

        conn = sqlite3.connect(str(db_path))
        row = conn.execute("SELECT kind, payload FROM task_events WHERE task_id = ?", ("t_123",)).fetchone()
        assert row[0] == "delegate_dispatch"
        assert "profile=lesedi" in row[1]
        assert "sess_abc" in row[1]
        conn.close()
