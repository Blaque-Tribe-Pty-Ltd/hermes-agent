"""Tell the MoonPie app when a delegated task finishes.

The gateway stores a finished delegation as a transcript row for the next turn, which the app only sees when the user
asks again. This module watches the durable ``async_delegations`` table for the conversations of one connected device
and pushes a short, readable ``delegation.completed`` message the moment a task completes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

_log = logging.getLogger("hermes_cli.moonpie_delegation_push")

_FAILED = {"failed", "error", "unknown", "cancelled", "interrupted", "timeout"}
_GOAL_LIMIT = 140
_TEXT_LIMIT = 600


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _summarise(summary: Any) -> str:
    """The child's final answer as short lines. Structured answers (changed / verification / blocker) are unpacked."""
    raw = str(summary or "").strip()
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        data = None
    if isinstance(data, dict):
        lines = []
        for key, label in (("changed", "Changed"), ("verification", "Verified"), ("blocker", "Blocked")):
            value = data.get(key)
            if value:
                lines.append(f"{label}: {_clip(value, _TEXT_LIMIT)}")
        if lines:
            return "\n".join(lines)
    return _clip(raw, _TEXT_LIMIT)


def build_completion(event: Dict[str, Any]) -> Dict[str, Any]:
    """The message the app shows for a finished delegation event (the ``event_json`` of an ``async_delegations`` row)."""
    results = [r for r in (event.get("results") or []) if isinstance(r, dict)]
    failed = str(event.get("status") or "").lower() in _FAILED or any(
        str(r.get("status") or "").lower() in _FAILED for r in results)
    goal = _clip(str(event.get("goal") or ""), _GOAL_LIMIT)
    lines: List[str] = []
    for index, result in enumerate(results):
        body = _summarise(result.get("summary")) or (_clip(result.get("error"), _TEXT_LIMIT) if result.get("error") else "")
        if body:
            lines.append(f"Task {index + 1}: {body}" if len(results) > 1 else body)
    if not lines and event.get("error"):
        lines.append(_clip(event["error"], _TEXT_LIMIT))
    title = "A delegated task failed" if failed else "A delegated task finished"
    text = title + (f": {goal}" if goal else ".")
    if lines:
        text += "\n\n" + "\n".join(lines)
    duration = event.get("total_duration_seconds") or event.get("duration_seconds")
    return {
        "delegation_id": str(event.get("delegation_id") or ""),
        "status": "failed" if failed else "completed",
        "title": title,
        "text": text,
        "duration_seconds": duration if isinstance(duration, (int, float)) else None,
    }


class DelegationWatcher:
    """Pushes each newly finished delegation of one device's conversations once.

    ``fetch(session_ids, since)`` returns dicts with ``delegation_id``, ``parent_session_id``, ``completed_at`` and
    ``event`` for completed delegations; ``sessions()`` maps the device's session ids to conversation ids; ``send`` delivers
    a JSON-RPC notification. Only completions after the watcher started are pushed (older ones are already in the
    transcript), and none twice.
    """

    def __init__(
        self,
        fetch: Callable[[List[str], float], List[Dict[str, Any]]],
        sessions: Callable[[], Dict[str, str]],
        send: Callable[[Dict[str, Any]], Awaitable[None]],
        *,
        interval: float = 3.0,
        started_at: Optional[float] = None,
    ) -> None:
        self._fetch = fetch
        self._sessions = sessions
        self._send = send
        self._interval = interval
        self._since = (started_at if started_at is not None else time.time()) - 1.0
        self._pushed: set = set()

    async def poll_once(self) -> int:
        mapping = self._sessions()
        if not mapping:
            return 0
        try:
            rows = await asyncio.to_thread(self._fetch, list(mapping), self._since)
        except Exception:
            _log.debug("delegation watch query failed", exc_info=True)
            return 0
        pushed = 0
        for row in rows:
            delegation_id = str(row.get("delegation_id") or "")
            conversation_id = mapping.get(str(row.get("parent_session_id") or ""))
            if not delegation_id or delegation_id in self._pushed or not conversation_id:
                continue
            event = row.get("event")
            if not isinstance(event, dict):
                continue
            self._pushed.add(delegation_id)
            completion = build_completion({**event, "delegation_id": delegation_id})
            await self._send({
                "jsonrpc": "2.0",
                "method": "delegation.completed",
                "params": {**completion, "conversation_id": conversation_id},
            })
            pushed += 1
        return pushed

    async def run(self) -> None:
        while True:
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                _log.debug("delegation watch failed", exc_info=True)
            await asyncio.sleep(self._interval)
