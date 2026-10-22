"""Turns that use tools, reported to MoonPie as jobs.

A MoonPie turn that runs at least one tool is a job: it is created when the first
tool starts, updated as tools start, and closed as done, failed or cancelled when
the turn ends. Progress is not reported because the gateway cannot know it.
A turn that runs no tools never becomes a job.
"""

from __future__ import annotations

import threading
import time
from typing import Any

_TITLE_LIMIT = 80


def job_title(text: Any) -> str:
    """A short title from the user's message; never empty."""
    first_paragraph = str(text or "").strip().split("\n\n")[0]  # the app appends a project block after a blank line
    flat = " ".join(first_paragraph.split())
    if not flat:
        return "Task"
    return flat if len(flat) <= _TITLE_LIMIT else flat[: _TITLE_LIMIT - 1].rstrip() + "…"


class TurnJobTracker:
    """Folds one turn's tool events into job rows and ``job.*`` notifications.

    Methods return the JSON-RPC notifications to send (possibly none); the caller sends them.
    """

    def __init__(self, db: Any, device_id: str, conversation_id: str, title: str) -> None:
        self._db = db
        self._device_id = device_id
        self._conversation_id = conversation_id
        self._title = job_title(title)
        self._lock = threading.Lock()
        self._job_id: str | None = None
        self._closed = False
        self.cancelled = False

    @property
    def job_id(self) -> str | None:
        return self._job_id

    def _notification(self, method: str, **params: Any) -> dict:
        return {
            "jsonrpc": "2.0",
            "method": method,
            "params": {"job_id": self._job_id, "conversation_id": self._conversation_id, **params},
        }

    def on_tool_event(self, event_type: str, data: dict) -> list[dict]:
        if event_type != "tool.started":
            return []
        name = str(data.get("tool_name") or "tool")
        message = f"Running {name}"
        with self._lock:
            if self._closed:
                return []
            if self._job_id is None:
                self._job_id = self._db.create_moonpie_job(self._device_id, self._title, "running")
                self._db.update_moonpie_job(self._job_id, message=message)
                return [self._notification("job.created", title=self._title, status="running", message=message)]
            self._db.update_moonpie_job(self._job_id, status="running", message=message)
            return [self._notification("job.updated", title=self._title, status="running", message=message)]

    def finish(self, outcome: str, error: str | None = None) -> list[dict]:
        """Close the job. *outcome* is ``done``, ``failed`` or ``cancelled`` (a cancelled job is stored as failed)."""
        with self._lock:
            if self._job_id is None or self._closed:
                return []
            self._closed = True
            if outcome == "done":
                self._db.update_moonpie_job(self._job_id, status="done", message="Finished", completed_at=time.time())
                return [self._notification("job.completed", status="done", message="Finished")]
            detail = "Cancelled" if outcome == "cancelled" else (error or "The turn failed")
            self._db.update_moonpie_job(
                self._job_id, status="failed", message=detail, error=detail, completed_at=time.time()
            )
            return [self._notification("job.failed", status="failed", message=detail, error=detail)]


class TurnJobRegistry:
    """The tracker of the turn in flight for each (device, conversation), so a cancel can reach it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._trackers: dict[tuple[str, str], TurnJobTracker] = {}

    def start(self, db: Any, device_id: str, conversation_id: str, title: Any) -> TurnJobTracker:
        tracker = TurnJobTracker(db, device_id, conversation_id, job_title(title))
        with self._lock:
            self._trackers[(device_id, conversation_id)] = tracker
        return tracker

    def mark_cancelled(self, device_id: str, conversation_id: str) -> None:
        with self._lock:
            tracker = self._trackers.get((device_id, conversation_id))
        if tracker is not None:
            tracker.cancelled = True

    def end(self, device_id: str, conversation_id: str, tracker: TurnJobTracker) -> None:
        with self._lock:
            if self._trackers.get((device_id, conversation_id)) is tracker:
                self._trackers.pop((device_id, conversation_id), None)
