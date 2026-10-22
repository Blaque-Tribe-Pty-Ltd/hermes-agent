pytest_plugins = ("tests.hermes_cli.test_moonpie_auth",)

from hermes_cli.moonpie_jobs import TurnJobRegistry, TurnJobTracker, job_title


class _DB:
    def __init__(self):
        self.jobs = {}
        self.created = 0

    def create_moonpie_job(self, device_id, title="", status="pending"):
        self.created += 1
        job_id = f"job-{self.created}"
        self.jobs[job_id] = {"device_id": device_id, "title": title, "status": status}
        return job_id

    def update_moonpie_job(self, job_id, **fields):
        self.jobs[job_id].update({k: v for k, v in fields.items() if v is not None})
        return 1


def _tracker(db=None, title="Fix the build"):
    return TurnJobTracker(db or _DB(), "dev", "conv", title)


def test_turn_without_tools_is_never_a_job():
    db = _DB()
    tracker = _tracker(db)
    assert tracker.on_tool_event("tool.completed", {"tool_call_id": "c"}) == []
    assert tracker.finish("done") == []
    assert db.jobs == {}


def test_first_tool_creates_job_and_later_tools_update_it():
    db = _DB()
    tracker = _tracker(db)
    [created] = tracker.on_tool_event("tool.started", {"tool_name": "terminal"})
    assert created["method"] == "job.created"
    assert created["params"]["status"] == "running"
    assert created["params"]["conversation_id"] == "conv"
    assert created["params"]["message"] == "Running terminal"
    assert "progress" not in created["params"]
    [updated] = tracker.on_tool_event("tool.started", {"tool_name": "read_file"})
    assert updated["method"] == "job.updated"
    assert updated["params"]["job_id"] == created["params"]["job_id"]
    assert db.created == 1


def test_done_closes_job_once():
    db = _DB()
    tracker = _tracker(db)
    tracker.on_tool_event("tool.started", {"tool_name": "terminal"})
    [done] = tracker.finish("done")
    assert done["method"] == "job.completed"
    assert db.jobs[tracker.job_id]["status"] == "done"
    assert tracker.finish("failed", "late") == []
    assert tracker.on_tool_event("tool.started", {"tool_name": "x"}) == []


def test_failure_and_cancellation_are_failed_jobs_with_a_reason():
    db = _DB()
    failed = _tracker(db)
    failed.on_tool_event("tool.started", {"tool_name": "terminal"})
    [note] = failed.finish("failed", "boom")
    assert note["method"] == "job.failed"
    assert note["params"]["error"] == "boom"
    cancelled = _tracker(db)
    cancelled.on_tool_event("tool.started", {"tool_name": "terminal"})
    [note] = cancelled.finish("cancelled")
    assert note["method"] == "job.failed"
    assert note["params"]["message"] == "Cancelled"
    assert db.jobs[cancelled.job_id]["status"] == "failed"


def test_title_is_trimmed_flattened_and_never_empty():
    assert job_title("  a\n b ") == "a b"
    assert job_title("") == "Task"
    assert job_title("Fix the build\n\n<project name=\"x\"/>") == "Fix the build"
    assert len(job_title("x" * 300)) == 80


def test_registry_reaches_the_turn_in_flight_for_cancel_and_forgets_it_after():
    registry = TurnJobRegistry()
    tracker = registry.start(_DB(), "dev", "conv", "hi")
    registry.mark_cancelled("dev", "conv")
    assert tracker.cancelled is True
    registry.end("dev", "conv", tracker)
    registry.mark_cancelled("dev", "conv")  # no turn: harmless


# --- WebSocket and REST surface ----------------------------------------------
import json
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes_cli.web_routers import moonpie as mp


@pytest.fixture(scope="module")
def client():
    app = FastAPI()
    app.include_router(mp.router)
    with TestClient(app) as c:
        yield c


def _login(ws, token):
    ws.send_json({"jsonrpc": "2.0", "id": 1, "method": "auth.login", "params": {"device_token": token}})
    json.loads(ws.receive_text())
    json.loads(ws.receive_text())


def _run_turn(client, token, fake_chat, content="Fix the build"):
    notes = []
    with patch.object(mp._moonpie_adapter, "chat", side_effect=fake_chat), \
         patch.object(mp._moonpie_adapter, "usage_snapshot", return_value=None), \
         patch.object(mp._moonpie_adapter, "synthesize_speech", return_value=None):
        with client.websocket_connect("/api/moonpie/ws") as ws:
            _login(ws, token)
            ws.send_json({"jsonrpc": "2.0", "id": 3, "method": "conversation.message",
                          "params": {"conversation_id": "jc1", "content": content}})
            while True:
                msg = json.loads(ws.receive_text())
                notes.append(msg)
                if msg.get("id") == 3:
                    break
    return notes


def _jobs(notes):
    return [n for n in notes if str(n.get("method", "")).startswith("job.")]


def test_ws_turn_with_tools_reports_a_job_from_start_to_done(client, valid_token):
    async def fake_chat(content, stream_callback=None, tool_event_callback=None, **kwargs):
        tool_event_callback("tool.started", {"tool_call_id": "t1", "tool_name": "terminal", "preview": "terminal"})
        tool_event_callback("tool.completed", {"tool_call_id": "t1", "tool_name": "terminal", "duration_ms": 5})
        return "ok"

    notes = _run_turn(client, valid_token, fake_chat)
    jobs = _jobs(notes)
    assert [j["method"] for j in jobs] == ["job.created", "job.completed"]
    assert jobs[0]["params"]["title"] == "Fix the build"
    assert jobs[0]["params"]["conversation_id"] == "jc1"
    job_id = jobs[0]["params"]["job_id"]
    response = client.get(f"/api/moonpie/jobs/{job_id}", headers={"Authorization": f"Bearer {valid_token}"})
    assert response.status_code == 200
    assert response.json()["status"] == "done"
    assert response.json()["title"] == "Fix the build"


def test_ws_turn_without_tools_is_not_a_job(client, valid_token):
    async def fake_chat(content, stream_callback=None, tool_event_callback=None, **kwargs):
        return "just words"

    assert _jobs(_run_turn(client, valid_token, fake_chat)) == []


def test_ws_failed_turn_reports_job_failed_without_leaking_the_exception(client, valid_token):
    async def fake_chat(content, stream_callback=None, tool_event_callback=None, **kwargs):
        tool_event_callback("tool.started", {"tool_call_id": "t1", "tool_name": "terminal", "preview": "terminal"})
        raise ValueError("secret internal detail")

    jobs = _jobs(_run_turn(client, valid_token, fake_chat))
    assert [j["method"] for j in jobs] == ["job.created", "job.failed"]
    assert "secret" not in json.dumps(jobs[1])
    assert jobs[1]["params"]["error"] == "The turn failed"


def test_get_unknown_job_is_404_and_other_devices_jobs_are_not_visible(client, valid_token):
    response = client.get("/api/moonpie/jobs/job-nope", headers={"Authorization": f"Bearer {valid_token}"})
    assert response.status_code == 404
