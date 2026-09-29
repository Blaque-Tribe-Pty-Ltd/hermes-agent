"""SessionDB mixin for MoonPie domain objects (Gate 4: Session Parity / Persistence).

Provides CRUD and list operations for conversations, jobs, and approvals
scoped to a MoonPie device_id. All operations use the SessionDB read/write
primitives (_read_all, _write_sql, _execute_write) for WAL-safe concurrency.
"""

import logging
import time
import uuid
from typing import Any, Dict, List, Optional

from hermes_state_common import escape_like

logger = logging.getLogger(__name__)


class SessionMoonpieMixin:
    # ------------------------------------------------------------------
    # Conversations
    # ------------------------------------------------------------------

    def list_moonpie_conversations(
        self,
        device_id: str,
        *,
        limit: int = 20,
        offset: int = 0,
        search: Optional[str] = None,
        include_archived: bool = False,
    ) -> List[Dict[str, Any]]:
        """Return conversation summaries for *device_id*, ordered by updated_at DESC.

        *search* filters on title (case-insensitive substring match).
        *include_archived* controls whether archived rows are returned.
        """
        conditions = ["device_id = ?"]
        params: List[Any] = [device_id]

        if not include_archived:
            conditions.append("archived = 0")

        if search:
            conditions.append("title LIKE ? ESCAPE '\\'")
            params.append(f"%{escape_like(search)}%")

        where_clause = " AND ".join(conditions)
        sql = (
            f"SELECT id, title, message_count, created_at, updated_at, archived "
            f"FROM moonpie_conversations WHERE {where_clause} "
            f"ORDER BY updated_at DESC LIMIT ? OFFSET ?"
        )
        params.extend([limit, offset])

        rows = self._read_all(sql, params)
        return [
            {
                "id": row["id"],
                "title": row["title"],
                "message_count": row["message_count"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "archived": bool(row["archived"]),
            }
            for row in rows
        ]

    def create_moonpie_conversation(
        self, device_id: str, title: str = "", message_count: int = 0,
    ) -> str:
        """Insert a new conversation and return its id."""
        conv_id = f"conv-{uuid.uuid4().hex[:12]}"
        now = time.time()
        self._write_sql(
            "INSERT INTO moonpie_conversations "
            "(id, device_id, title, message_count, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (conv_id, device_id, title, message_count, now, now),
        )
        return conv_id

    def archive_moonpie_conversation(self, conversation_id: str) -> int:
        """Mark a conversation as archived. Returns rows affected (0 or 1)."""
        return self._write_rowcount(
            "UPDATE moonpie_conversations SET archived = 1 WHERE id = ?",
            (conversation_id,),
        )

    def get_moonpie_conversation(self, conversation_id: str, device_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a single conversation if it belongs to *device_id*."""
        row = self._read_one(
            "SELECT id, title, message_count, created_at, updated_at, archived "
            "FROM moonpie_conversations WHERE id = ? AND device_id = ?",
            (conversation_id, device_id),
        )
        if row is None:
            return None
        return {
            "id": row["id"],
            "title": row["title"],
            "message_count": row["message_count"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "archived": bool(row["archived"]),
        }

    # ------------------------------------------------------------------
    # Jobs
    # ------------------------------------------------------------------

    def list_moonpie_jobs(
        self,
        device_id: str,
        *,
        limit: int = 20,
        offset: int = 0,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return job summaries for *device_id*, ordered by created_at DESC.

        *status* filters on the status column (exact match).
        """
        conditions = ["device_id = ?"]
        params: List[Any] = [device_id]

        if status:
            conditions.append("status = ?")
            params.append(status)

        where_clause = " AND ".join(conditions)
        sql = (
            f"SELECT id, title, status, progress, message, created_at, completed_at, error "
            f"FROM moonpie_jobs WHERE {where_clause} "
            f"ORDER BY created_at DESC LIMIT ? OFFSET ?"
        )
        params.extend([limit, offset])

        rows = self._read_all(sql, params)
        return [
            {
                "id": row["id"],
                "title": row["title"],
                "status": row["status"],
                "progress": row["progress"],
                "message": row["message"],
                "created_at": row["created_at"],
                "completed_at": row["completed_at"],
                "error": row["error"],
            }
            for row in rows
        ]

    def create_moonpie_job(
        self, device_id: str, title: str = "", status: str = "pending",
    ) -> str:
        """Insert a new job and return its id."""
        job_id = f"job-{uuid.uuid4().hex[:12]}"
        now = time.time()
        self._write_sql(
            "INSERT INTO moonpie_jobs "
            "(id, device_id, title, status, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (job_id, device_id, title, status, now),
        )
        return job_id

    def update_moonpie_job(
        self,
        job_id: str,
        *,
        status: Optional[str] = None,
        progress: Optional[float] = None,
        message: Optional[str] = None,
        error: Optional[str] = None,
        completed_at: Optional[float] = None,
    ) -> int:
        """Update mutable job fields. Returns rows affected (0 or 1)."""
        fields: List[str] = []
        params: List[Any] = []

        if status is not None:
            fields.append("status = ?")
            params.append(status)
        if progress is not None:
            fields.append("progress = ?")
            params.append(progress)
        if message is not None:
            fields.append("message = ?")
            params.append(message)
        if error is not None:
            fields.append("error = ?")
            params.append(error)
        if completed_at is not None:
            fields.append("completed_at = ?")
            params.append(completed_at)

        if not fields:
            return 0

        params.append(job_id)
        sql = f"UPDATE moonpie_jobs SET {', '.join(fields)} WHERE id = ?"
        return self._write_rowcount(sql, params)

    def get_moonpie_job(self, job_id: str, device_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a single job if it belongs to *device_id*."""
        row = self._read_one(
            "SELECT id, title, status, progress, message, created_at, completed_at, error "
            "FROM moonpie_jobs WHERE id = ? AND device_id = ?",
            (job_id, device_id),
        )
        if row is None:
            return None
        return {
            "id": row["id"],
            "title": row["title"],
            "status": row["status"],
            "progress": row["progress"],
            "message": row["message"],
            "created_at": row["created_at"],
            "completed_at": row["completed_at"],
            "error": row["error"],
        }

    # ------------------------------------------------------------------
    # Approvals
    # ------------------------------------------------------------------

    def list_moonpie_approvals(
        self,
        device_id: str,
        *,
        limit: int = 20,
        offset: int = 0,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return approval records for *device_id*, ordered by created_at DESC.

        *status* filters on the status column (exact match).
        """
        conditions = ["device_id = ?"]
        params: List[Any] = [device_id]

        if status:
            conditions.append("status = ?")
            params.append(status)

        where_clause = " AND ".join(conditions)
        sql = (
            f"SELECT id, session_key, command, description, status, action, created_at, resolved_at "
            f"FROM moonpie_approvals WHERE {where_clause} "
            f"ORDER BY created_at DESC LIMIT ? OFFSET ?"
        )
        params.extend([limit, offset])

        rows = self._read_all(sql, params)
        return [
            {
                "id": row["id"],
                "session_key": row["session_key"],
                "command": row["command"],
                "description": row["description"],
                "status": row["status"],
                "action": row["action"],
                "created_at": row["created_at"],
                "resolved_at": row["resolved_at"],
            }
            for row in rows
        ]

    def create_moonpie_approval(
        self,
        device_id: str,
        session_key: str,
        command: str,
        description: str,
        approval_id: Optional[str] = None,
    ) -> str:
        """Insert a new approval record and return its id.

        If *approval_id* is provided it is used as the primary key;
        otherwise a new UUID is minted.
        """
        aid = approval_id or f"aprv-{uuid.uuid4().hex[:12]}"
        now = time.time()
        self._write_sql(
            "INSERT INTO moonpie_approvals "
            "(id, device_id, session_key, command, description, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (aid, device_id, session_key, command, description, "pending", now),
        )
        return aid

    def resolve_moonpie_approval(
        self, approval_id: str, status: str, action: Optional[str] = None,
    ) -> int:
        """Mark an approval as resolved. Returns rows affected (0 or 1)."""
        now = time.time()
        return self._write_rowcount(
            "UPDATE moonpie_approvals SET status = ?, action = ?, resolved_at = ? WHERE id = ?",
            (status, action, now, approval_id),
        )

    def get_moonpie_approval(self, approval_id: str, device_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a single approval if it belongs to *device_id*."""
        row = self._read_one(
            "SELECT id, session_key, command, description, status, action, created_at, resolved_at "
            "FROM moonpie_approvals WHERE id = ? AND device_id = ?",
            (approval_id, device_id),
        )
        if row is None:
            return None
        return {
            "id": row["id"],
            "session_key": row["session_key"],
            "command": row["command"],
            "description": row["description"],
            "status": row["status"],
            "action": row["action"],
            "created_at": row["created_at"],
            "resolved_at": row["resolved_at"],
        }

    # ------------------------------------------------------------------
    # Devices & Tokens (Gate 8: Gateway Convergence — persistent state)
    # ------------------------------------------------------------------

    def register_moonpie_device(
        self, device_id: str, name: str, public_key: str, pairing_code: str,
    ) -> None:
        """Insert a pending (unconfirmed) device record."""
        now = time.time()
        self._write_sql(
            "INSERT INTO moonpie_devices "
            "(id, name, public_key, pairing_code, confirmed, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (device_id, name, public_key, pairing_code, 0, now, now),
        )

    def confirm_moonpie_device(self, device_id: str) -> int:
        """Mark a device as confirmed. Returns rows affected (0 or 1)."""
        now = time.time()
        return self._write_rowcount(
            "UPDATE moonpie_devices SET confirmed = 1, updated_at = ? WHERE id = ?",
            (now, device_id),
        )

    def get_moonpie_device(self, device_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a single device by id (any confirmation state)."""
        row = self._read_one(
            "SELECT id, name, public_key, pairing_code, confirmed, created_at, updated_at "
            "FROM moonpie_devices WHERE id = ?",
            (device_id,),
        )
        if row is None:
            return None
        return {
            "id": row["id"],
            "name": row["name"],
            "public_key": row["public_key"],
            "pairing_code": row["pairing_code"],
            "confirmed": bool(row["confirmed"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_moonpie_devices(self, *, confirmed_only: bool = True) -> List[Dict[str, Any]]:
        """Return device records, optionally filtering to confirmed only."""
        sql = (
            "SELECT id, name, public_key, pairing_code, confirmed, created_at, updated_at "
            "FROM moonpie_devices"
        )
        params: List[Any] = []
        if confirmed_only:
            sql += " WHERE confirmed = 1"
        sql += " ORDER BY updated_at DESC"
        rows = self._read_all(sql, params)
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "public_key": row["public_key"],
                "pairing_code": row["pairing_code"],
                "confirmed": bool(row["confirmed"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def delete_moonpie_device(self, device_id: str) -> int:
        """Delete a device and its tokens (CASCADE). Returns rows affected."""
        return self._write_rowcount(
            "DELETE FROM moonpie_devices WHERE id = ?",
            (device_id,),
        )

    def store_moonpie_device_token(
        self, token: str, device_id: str, expires_at: Optional[float] = None,
    ) -> None:
        """Insert a token for a device."""
        now = time.time()
        self._write_sql(
            "INSERT INTO moonpie_device_tokens (token, device_id, created_at, expires_at) "
            "VALUES (?, ?, ?, ?)",
            (token, device_id, now, expires_at),
        )

    def get_moonpie_device_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Fetch a token record by token string."""
        row = self._read_one(
            "SELECT token, device_id, created_at, expires_at "
            "FROM moonpie_device_tokens WHERE token = ?",
            (token,),
        )
        if row is None:
            return None
        return {
            "token": row["token"],
            "device_id": row["device_id"],
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
        }

    def delete_moonpie_device_token(self, token: str) -> int:
        """Delete a single token. Returns rows affected."""
        return self._write_rowcount(
            "DELETE FROM moonpie_device_tokens WHERE token = ?",
            (token,),
        )

    def list_moonpie_device_tokens(self, device_id: str) -> List[Dict[str, Any]]:
        """Return all tokens for a device."""
        rows = self._read_all(
            "SELECT token, device_id, created_at, expires_at "
            "FROM moonpie_device_tokens WHERE device_id = ?",
            (device_id,),
        )
        return [
            {
                "token": row["token"],
                "device_id": row["device_id"],
                "created_at": row["created_at"],
                "expires_at": row["expires_at"],
            }
            for row in rows
        ]
