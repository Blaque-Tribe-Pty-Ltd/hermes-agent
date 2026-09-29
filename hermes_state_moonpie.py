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
