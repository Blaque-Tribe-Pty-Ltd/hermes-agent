"""Approval.request actions: an MCP tool consent is a per-call yes/no."""

import asyncio
from unittest.mock import MagicMock, patch

from hermes_cli.web_routers import moonpie as mp


def _notify(approval_data):
    sent = []

    async def run():
        conn = mp._MoonPieConnection(MagicMock())
        conn.device_id = "dev"

        async def record(data):
            sent.append(data)

        conn.send_json = record
        with patch.object(mp, "_get_moonpie_db", return_value=MagicMock()):
            conn._approval_notify(approval_data)
        await asyncio.sleep(0.05)

    asyncio.run(run())
    return sent[0]["params"]


def test_dangerous_command_offers_all_four_choices():
    params = _notify({"request_id": "r1", "command": "rm -rf /tmp/x", "description": "d", "pattern_key": "recursive delete"})
    assert params["actions"] == ["once", "session", "always", "deny"]


def test_mcp_tool_consent_offers_only_once_or_deny():
    params = _notify({
        "request_id": "r2",
        "command": "MCP tool 'write_file' on UNTRUSTED server 'mac_workspace' wants to run.",
        "description": "Approve to run 'write_file' once, or deny to block it.",
        "pattern_key": "mcp_elicitation",
        "pattern_keys": ["mcp_elicitation"],
    })
    assert params["actions"] == ["once", "deny"]
    assert params["approval_id"] == "r2"
    assert "write_file" in params["command"]


def test_missing_pattern_key_keeps_the_existing_behaviour():
    params = _notify({"request_id": "r3", "command": "ls", "description": ""})
    assert params["actions"] == ["once", "session", "always", "deny"]


def test_approval_is_delivered_when_raised_from_a_worker_thread_without_an_event_loop():
    """The agent raises approvals from a ThreadPoolExecutor thread. Asking that thread for 'the current loop'
    raised, the notify failed and every approval was refused (observed live)."""
    import threading

    sent = []
    errors = []

    async def run():
        conn = mp._MoonPieConnection(MagicMock())
        conn.device_id = "dev"

        async def record(data):
            sent.append(data)

        conn.send_json = record

        def from_worker():
            try:
                with patch.object(mp, "_get_moonpie_db", return_value=MagicMock()):
                    conn._approval_notify({"request_id": "w1", "command": "ls", "description": "d", "pattern_key": "x"})
            except Exception as exc:  # the failure mode being pinned
                errors.append(exc)

        worker = threading.Thread(target=from_worker)
        worker.start()
        worker.join()
        await asyncio.sleep(0.1)

    asyncio.run(run())
    assert errors == []
    assert [m["params"]["approval_id"] for m in sent] == ["w1"]
