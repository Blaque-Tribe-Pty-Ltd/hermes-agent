"""Gate 4 tests: Session Parity / Persistence.

Verifies that list_conversations, list_jobs, and list_approvals are backed
by SessionDB, with correct pagination, search, isolation, and restart recovery.
"""

import pytest
from fastapi.testclient import TestClient

from hermes_cli.web_routers.moonpie import router, _device_tokens, _pending_pairings, _registered_devices
from hermes_state import SessionDB


@pytest.fixture(autouse=True)
def _reset_moonpie_state():
    """Clear in-memory device state before every test."""
    _device_tokens.clear()
    _pending_pairings.clear()
    _registered_devices.clear()
    yield


@pytest.fixture
def client():
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def tmp_db(tmp_path):
    """A fresh SessionDB in a temp directory."""
    db_path = tmp_path / "test_state.db"
    db = SessionDB(db_path=db_path)
    return db


@pytest.fixture
def valid_token():
    """Mint a valid device token."""
    token = "mpdt-test-token-abc123"
    device_id = "moonpie-test-device"
    _device_tokens[token] = device_id
    return token


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _auth_header(token: str):
    return {"Authorization": f"Bearer {token}"}


# ==================================================================
# Conversations
# ==================================================================

class TestConversations:
    def test_create_conversation_persists(self, client, valid_token, tmp_db):
        """POST /conversations writes a real row into SessionDB."""
        # Inject the temp DB into the router
        from hermes_cli.web_routers import moonpie as mp
        original_db = mp._moonpie_db
        mp._moonpie_db = tmp_db
        try:
            resp = client.post("/api/moonpie/conversations", headers=_auth_header(valid_token))
            assert resp.status_code == 200
            data = resp.json()
            conv_id = data["id"]

            # Verify directly in SessionDB
            device_id = _device_tokens[valid_token]
            row = tmp_db.get_moonpie_conversation(conv_id, device_id)
            assert row is not None
            assert row["title"] == "New Conversation"
            assert row["archived"] is False
        finally:
            mp._moonpie_db = original_db

    def test_list_conversations_returns_persisted(self, client, valid_token, tmp_db):
        """GET /conversations returns rows previously created in SessionDB."""
        from hermes_cli.web_routers import moonpie as mp
        original_db = mp._moonpie_db
        mp._moonpie_db = tmp_db
        try:
            device_id = _device_tokens[valid_token]
            tmp_db.create_moonpie_conversation(device_id, title="Alpha")
            tmp_db.create_moonpie_conversation(device_id, title="Beta")

            resp = client.get("/api/moonpie/conversations", headers=_auth_header(valid_token))
            assert resp.status_code == 200
            data = resp.json()
            assert len(data) == 2
            titles = [d["title"] for d in data]
            assert "Alpha" in titles
            assert "Beta" in titles
        finally:
            mp._moonpie_db = original_db

    def test_list_conversations_pagination(self, client, valid_token, tmp_db):
        """Pagination (limit / offset) is stable and deterministic."""
        from hermes_cli.web_routers import moonpie as mp
        original_db = mp._moonpie_db
        mp._moonpie_db = tmp_db
        try:
            device_id = _device_tokens[valid_token]
            for i in range(5):
                tmp_db.create_moonpie_conversation(device_id, title=f"Conv-{i}")

            # Page 1: limit=2
            r1 = client.get("/api/moonpie/conversations?limit=2&offset=0", headers=_auth_header(valid_token))
            assert len(r1.json()) == 2

            # Page 2: limit=2, offset=2
            r2 = client.get("/api/moonpie/conversations?limit=2&offset=2", headers=_auth_header(valid_token))
            assert len(r2.json()) == 2

            # Page 3: limit=2, offset=4
            r3 = client.get("/api/moonpie/conversations?limit=2&offset=4", headers=_auth_header(valid_token))
            assert len(r3.json()) == 1

            # No overlap between pages
            ids1 = {d["id"] for d in r1.json()}
            ids2 = {d["id"] for d in r2.json()}
            ids3 = {d["id"] for d in r3.json()}
            assert not ids1 & ids2
            assert not ids2 & ids3
            assert not ids1 & ids3
        finally:
            mp._moonpie_db = original_db

    def test_list_conversations_search(self, client, valid_token, tmp_db):
        """Search filters on title substring."""
        from hermes_cli.web_routers import moonpie as mp
        original_db = mp._moonpie_db
        mp._moonpie_db = tmp_db
        try:
            device_id = _device_tokens[valid_token]
            tmp_db.create_moonpie_conversation(device_id, title="Project Alpha")
            tmp_db.create_moonpie_conversation(device_id, title="Project Beta")
            tmp_db.create_moonpie_conversation(device_id, title="Unrelated")

            resp = client.get("/api/moonpie/conversations?search=Alpha", headers=_auth_header(valid_token))
            data = resp.json()
            assert len(data) == 1
            assert data[0]["title"] == "Project Alpha"
        finally:
            mp._moonpie_db = original_db

    def test_list_conversations_excludes_archived(self, client, valid_token, tmp_db):
        """Archived conversations are hidden by default."""
        from hermes_cli.web_routers import moonpie as mp
        original_db = mp._moonpie_db
        mp._moonpie_db = tmp_db
        try:
            device_id = _device_tokens[valid_token]
            cid = tmp_db.create_moonpie_conversation(device_id, title="Keep")
            cid_arch = tmp_db.create_moonpie_conversation(device_id, title="Archive Me")
            tmp_db.archive_moonpie_conversation(cid_arch)

            resp = client.get("/api/moonpie/conversations", headers=_auth_header(valid_token))
            data = resp.json()
            assert len(data) == 1
            assert data[0]["id"] == cid
        finally:
            mp._moonpie_db = original_db

    def test_conversation_cross_device_isolation(self, client, valid_token, tmp_db):
        """One device's conversations are not visible to another device."""
        from hermes_cli.web_routers import moonpie as mp
        original_db = mp._moonpie_db
        mp._moonpie_db = tmp_db
        try:
            device_a = _device_tokens[valid_token]
            token_b = "mpdt-test-token-b"
            device_b = "moonpie-device-b"
            _device_tokens[token_b] = device_b

            tmp_db.create_moonpie_conversation(device_a, title="A-only")
            tmp_db.create_moonpie_conversation(device_b, title="B-only")

            resp_a = client.get("/api/moonpie/conversations", headers=_auth_header(valid_token))
            assert len(resp_a.json()) == 1
            assert resp_a.json()[0]["title"] == "A-only"

            resp_b = client.get("/api/moonpie/conversations", headers=_auth_header(token_b))
            assert len(resp_b.json()) == 1
            assert resp_b.json()[0]["title"] == "B-only"
        finally:
            mp._moonpie_db = original_db


# ==================================================================
# Jobs
# ==================================================================

class TestJobs:
    def test_list_jobs_returns_persisted(self, client, valid_token, tmp_db):
        """GET /jobs returns rows previously created in SessionDB."""
        from hermes_cli.web_routers import moonpie as mp
        original_db = mp._moonpie_db
        mp._moonpie_db = tmp_db
        try:
            device_id = _device_tokens[valid_token]
            tmp_db.create_moonpie_job(device_id, title="Job-A", status="running")
            tmp_db.create_moonpie_job(device_id, title="Job-B", status="completed")

            resp = client.get("/api/moonpie/jobs", headers=_auth_header(valid_token))
            assert resp.status_code == 200
            data = resp.json()
            assert len(data) == 2
        finally:
            mp._moonpie_db = original_db

    def test_list_jobs_status_filter(self, client, valid_token, tmp_db):
        """Status query param filters exactly."""
        from hermes_cli.web_routers import moonpie as mp
        original_db = mp._moonpie_db
        mp._moonpie_db = tmp_db
        try:
            device_id = _device_tokens[valid_token]
            tmp_db.create_moonpie_job(device_id, title="Running", status="running")
            tmp_db.create_moonpie_job(device_id, title="Done", status="completed")

            resp = client.get("/api/moonpie/jobs?status=completed", headers=_auth_header(valid_token))
            data = resp.json()
            assert len(data) == 1
            assert data[0]["title"] == "Done"
        finally:
            mp._moonpie_db = original_db

    def test_list_jobs_pagination(self, client, valid_token, tmp_db):
        """Pagination is stable for jobs."""
        from hermes_cli.web_routers import moonpie as mp
        original_db = mp._moonpie_db
        mp._moonpie_db = tmp_db
        try:
            device_id = _device_tokens[valid_token]
            for i in range(5):
                tmp_db.create_moonpie_job(device_id, title=f"Job-{i}")

            r1 = client.get("/api/moonpie/jobs?limit=3&offset=0", headers=_auth_header(valid_token))
            r2 = client.get("/api/moonpie/jobs?limit=3&offset=3", headers=_auth_header(valid_token))
            assert len(r1.json()) == 3
            assert len(r2.json()) == 2
            ids1 = {d["id"] for d in r1.json()}
            ids2 = {d["id"] for d in r2.json()}
            assert not ids1 & ids2
        finally:
            mp._moonpie_db = original_db

    def test_job_cross_device_isolation(self, client, valid_token, tmp_db):
        """One device's jobs are not visible to another device."""
        from hermes_cli.web_routers import moonpie as mp
        original_db = mp._moonpie_db
        mp._moonpie_db = tmp_db
        try:
            device_a = _device_tokens[valid_token]
            token_b = "mpdt-test-token-job-b"
            device_b = "moonpie-job-b"
            _device_tokens[token_b] = device_b

            tmp_db.create_moonpie_job(device_a, title="A-job")
            tmp_db.create_moonpie_job(device_b, title="B-job")

            assert len(client.get("/api/moonpie/jobs", headers=_auth_header(valid_token)).json()) == 1
            assert len(client.get("/api/moonpie/jobs", headers=_auth_header(token_b)).json()) == 1
        finally:
            mp._moonpie_db = original_db


# ==================================================================
# Approvals
# ==================================================================

class TestApprovals:
    def test_list_approvals_returns_persisted(self, client, valid_token, tmp_db):
        """GET /approvals returns rows previously created in SessionDB."""
        from hermes_cli.web_routers import moonpie as mp
        original_db = mp._moonpie_db
        mp._moonpie_db = tmp_db
        try:
            device_id = _device_tokens[valid_token]
            tmp_db.create_moonpie_approval(
                device_id, session_key="sk", command="rm -rf /", description="Danger",
            )

            resp = client.get("/api/moonpie/approvals", headers=_auth_header(valid_token))
            assert resp.status_code == 200
            data = resp.json()
            assert len(data) == 1
            assert data[0]["command"] == "rm -rf /"
            assert data[0]["status"] == "pending"
        finally:
            mp._moonpie_db = original_db

    def test_list_approvals_status_filter(self, client, valid_token, tmp_db):
        """Status query param filters approvals."""
        from hermes_cli.web_routers import moonpie as mp
        original_db = mp._moonpie_db
        mp._moonpie_db = tmp_db
        try:
            device_id = _device_tokens[valid_token]
            aid1 = tmp_db.create_moonpie_approval(device_id, "sk", "cmd1", "desc1")
            aid2 = tmp_db.create_moonpie_approval(device_id, "sk", "cmd2", "desc2")
            tmp_db.resolve_moonpie_approval(aid2, status="resolved", action="deny")

            resp = client.get("/api/moonpie/approvals?status=pending", headers=_auth_header(valid_token))
            data = resp.json()
            assert len(data) == 1
            assert data[0]["approval_id"] == aid1
        finally:
            mp._moonpie_db = original_db

    def test_approval_cross_device_isolation(self, client, valid_token, tmp_db):
        """One device's approvals are not visible to another device."""
        from hermes_cli.web_routers import moonpie as mp
        original_db = mp._moonpie_db
        mp._moonpie_db = tmp_db
        try:
            device_a = _device_tokens[valid_token]
            token_b = "mpdt-test-token-aprv-b"
            device_b = "moonpie-aprv-b"
            _device_tokens[token_b] = device_b

            tmp_db.create_moonpie_approval(device_a, "sk-a", "cmd-a", "desc-a")
            tmp_db.create_moonpie_approval(device_b, "sk-b", "cmd-b", "desc-b")

            assert len(client.get("/api/moonpie/approvals", headers=_auth_header(valid_token)).json()) == 1
            assert len(client.get("/api/moonpie/approvals", headers=_auth_header(token_b)).json()) == 1
        finally:
            mp._moonpie_db = original_db


# ==================================================================
# Restart / Recovery
# ==================================================================

class TestRestartRecovery:
    def test_conversations_survive_restart(self, tmp_path):
        """Closing and reopening SessionDB preserves conversation rows."""
        db_path = tmp_path / "restart.db"
        db1 = SessionDB(db_path=db_path)
        cid = db1.create_moonpie_conversation("dev-1", title="Survive")
        del db1

        db2 = SessionDB(db_path=db_path)
        row = db2.get_moonpie_conversation(cid, "dev-1")
        assert row is not None
        assert row["title"] == "Survive"

    def test_jobs_survive_restart(self, tmp_path):
        """Closing and reopening SessionDB preserves job rows."""
        db_path = tmp_path / "restart.db"
        db1 = SessionDB(db_path=db_path)
        jid = db1.create_moonpie_job("dev-1", title="Survive", status="running")
        del db1

        db2 = SessionDB(db_path=db_path)
        row = db2.get_moonpie_job(jid, "dev-1")
        assert row is not None
        assert row["status"] == "running"

    def test_approvals_survive_restart(self, tmp_path):
        """Closing and reopening SessionDB preserves approval rows."""
        db_path = tmp_path / "restart.db"
        db1 = SessionDB(db_path=db_path)
        aid = db1.create_moonpie_approval("dev-1", "sk", "cmd", "desc")
        db1.resolve_moonpie_approval(aid, status="resolved", action="once")
        del db1

        db2 = SessionDB(db_path=db_path)
        row = db2.get_moonpie_approval(aid, "dev-1")
        assert row is not None
        assert row["status"] == "resolved"
        assert row["action"] == "once"


# ==================================================================
# Malformed / Stale record safety
# ==================================================================

class TestMalformedSafety:
    def test_get_conversation_wrong_device_returns_none(self, tmp_db):
        """A conversation lookup for the wrong device returns None, not a leak."""
        cid = tmp_db.create_moonpie_conversation("dev-a", title="Secret")
        assert tmp_db.get_moonpie_conversation(cid, "dev-b") is None

    def test_get_job_wrong_device_returns_none(self, tmp_db):
        """A job lookup for the wrong device returns None, not a leak."""
        jid = tmp_db.create_moonpie_job("dev-a", title="Secret")
        assert tmp_db.get_moonpie_job(jid, "dev-b") is None

    def test_get_approval_wrong_device_returns_none(self, tmp_db):
        """An approval lookup for the wrong device returns None, not a leak."""
        aid = tmp_db.create_moonpie_approval("dev-a", "sk", "cmd", "desc")
        assert tmp_db.get_moonpie_approval(aid, "dev-b") is None

    def test_resolve_missing_approval_returns_zero(self, tmp_db):
        """Resolving a non-existent approval returns 0 rows affected."""
        assert tmp_db.resolve_moonpie_approval("no-such-id", status="resolved") == 0

    def test_archive_missing_conversation_returns_zero(self, tmp_db):
        """Archiving a non-existent conversation returns 0 rows affected."""
        assert tmp_db.archive_moonpie_conversation("no-such-id") == 0
