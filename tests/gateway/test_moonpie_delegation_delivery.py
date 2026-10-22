"""A MoonPie delegation result is stored in the conversation session that dispatched it (not under the device key)."""
import asyncio
import sqlite3
from types import SimpleNamespace

import pytest

from gateway.wake import persist_delegation_delivery
from hermes_state import SessionDB

DEVICE_KEY = "moonpie_moonpie-device"       # a routing key: no session row exists for it
CONVERSATION = "moonpie_0123456789abcdef"    # the conversation's real session


def _adapter(db):
    return SimpleNamespace(_ensure_session_db=lambda: db)


def _evt(delegation_id="deleg-1", **extra):
    return {"type": "async_delegation", "delegation_id": delegation_id, "session_key": DEVICE_KEY,
            "parent_session_id": CONVERSATION, "origin_session_id": "", **extra}


@pytest.mark.asyncio
async def test_result_lands_in_the_parent_conversation_when_the_device_key_has_no_session(tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session(CONVERSATION, source="api_server")
        await persist_delegation_delivery(_adapter(db), text="Neo finished: wrote Greeter.swift", session_id=DEVICE_KEY, evt=_evt())
        rows = db.get_messages(CONVERSATION)
        assert [r["content"] for r in rows] == ["Neo finished: wrote Greeter.swift"]
    finally:
        db.close()


@pytest.mark.asyncio
async def test_an_existing_routed_session_is_still_used(tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session(DEVICE_KEY, source="api_server")
        db.create_session(CONVERSATION, source="api_server")
        await persist_delegation_delivery(_adapter(db), text="RESULT", session_id=DEVICE_KEY, evt=_evt())
        assert len(db.get_messages(DEVICE_KEY)) == 1
        assert db.get_messages(CONVERSATION) == []
    finally:
        db.close()


@pytest.mark.asyncio
async def test_delivery_is_recorded_once_even_if_the_watcher_retries(tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session(CONVERSATION, source="api_server")
        for _ in range(3):
            await persist_delegation_delivery(_adapter(db), text="RESULT", session_id=DEVICE_KEY, evt=_evt("deleg-once"))
        assert len(db.get_messages(CONVERSATION)) == 1
    finally:
        db.close()


@pytest.mark.asyncio
async def test_when_no_session_exists_anywhere_the_write_still_fails_so_the_claim_is_released(tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        with pytest.raises(Exception):
            await persist_delegation_delivery(_adapter(db), text="RESULT", session_id=DEVICE_KEY, evt=_evt())
    finally:
        db.close()
