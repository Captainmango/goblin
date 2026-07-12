from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiosqlite


class Store:
    """SQLite-backed store for pairings, question mappings, inbox replies, and idempotency."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self._connection: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        """Open the database and create/migrate tables."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = await aiosqlite.connect(self.db_path)
        self._connection.row_factory = aiosqlite.Row
        await self._migrate()

    async def close(self) -> None:
        """Close the database connection."""
        if self._connection is not None:
            await self._connection.close()
            self._connection = None

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _expiry(self, ttl_seconds: int) -> str:
        return (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat()

    async def _execute(self, sql: str, parameters: tuple[Any, ...] = ()) -> aiosqlite.Cursor:
        if self._connection is None:
            raise RuntimeError("Store is not connected")
        return await self._connection.execute(sql, parameters)

    async def _executemany(self, sql: str, parameters: list[tuple[Any, ...]]) -> aiosqlite.Cursor:
        if self._connection is None:
            raise RuntimeError("Store is not connected")
        return await self._connection.executemany(sql, parameters)

    async def _migrate(self) -> None:
        await self._execute(
            """
            CREATE TABLE IF NOT EXISTS pairings (
                token TEXT PRIMARY KEY,
                label TEXT,
                username TEXT,
                telegram_user_id TEXT,
                chat_id TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                claimed_at TEXT
            )
            """
        )
        await self._execute(
            """
            CREATE TABLE IF NOT EXISTS question_mappings (
                telegram_message_id TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                token TEXT NOT NULL,
                question_id TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                PRIMARY KEY (chat_id, telegram_message_id)
            )
            """
        )
        await self._execute(
            """
            CREATE TABLE IF NOT EXISTS inbox (
                id TEXT PRIMARY KEY,
                token TEXT NOT NULL,
                telegram_user_id TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                question_id TEXT,
                text TEXT NOT NULL,
                received_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
            """
        )
        await self._execute(
            """
            CREATE TABLE IF NOT EXISTS idempotency_cache (
                key TEXT PRIMARY KEY,
                response_json TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
            """
        )
        await self._connection.commit()  # type: ignore[union-attr]

    # Pairings

    async def create_pairing(
        self,
        token: str,
        label: str | None,
        username: str | None,
        ttl_seconds: int,
    ) -> None:
        now = self._now()
        expires = self._expiry(ttl_seconds)
        await self._execute(
            "INSERT INTO pairings (token, label, username, status, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
            (token, label, username, "pending", now, expires),
        )
        await self._connection.commit()  # type: ignore[union-attr]

    async def get_pairing_by_token(self, token: str) -> dict[str, Any] | None:
        cursor = await self._execute(
            "SELECT * FROM pairings WHERE token = ?",
            (token,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_pairing_by_telegram_user_id(self, telegram_user_id: str) -> dict[str, Any] | None:
        cursor = await self._execute(
            "SELECT * FROM pairings WHERE telegram_user_id = ? AND status = 'claimed'",
            (telegram_user_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def list_active_pairings(self) -> list[dict[str, Any]]:
        cursor = await self._execute(
            "SELECT * FROM pairings WHERE status = 'claimed' ORDER BY created_at",
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def claim_pairing(
        self,
        token: str,
        telegram_user_id: str,
        chat_id: str,
    ) -> None:
        now = self._now()
        far_future = (datetime.now(timezone.utc) + timedelta(days=365 * 100)).isoformat()
        await self._execute(
            """
            UPDATE pairings
            SET status = 'claimed',
                telegram_user_id = ?,
                chat_id = ?,
                claimed_at = ?,
                expires_at = ?
            WHERE token = ? AND status = 'pending'
            """,
            (telegram_user_id, chat_id, now, far_future, token),
        )
        await self._connection.commit()  # type: ignore[union-attr]

    async def revoke_pairing(self, token: str) -> bool:
        cursor = await self._execute(
            "UPDATE pairings SET status = 'revoked' WHERE token = ? AND status = 'claimed'",
            (token,),
        )
        await self._connection.commit()  # type: ignore[union-attr]
        return cursor.rowcount > 0

    async def delete_expired_pending(self) -> int:
        now = self._now()
        cursor = await self._execute(
            "DELETE FROM pairings WHERE status = 'pending' AND expires_at < ?",
            (now,),
        )
        await self._connection.commit()  # type: ignore[union-attr]
        return cursor.rowcount

    # Question mappings

    async def create_question_mapping(
        self,
        chat_id: str,
        telegram_message_id: str,
        token: str,
        question_id: str,
        ttl_seconds: int,
    ) -> None:
        expires = self._expiry(ttl_seconds)
        await self._execute(
            """
            INSERT OR REPLACE INTO question_mappings
                (chat_id, telegram_message_id, token, question_id, expires_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (chat_id, telegram_message_id, token, question_id, expires),
        )
        await self._connection.commit()  # type: ignore[union-attr]

    async def get_question_mapping(self, chat_id: str, telegram_message_id: str) -> dict[str, Any] | None:
        cursor = await self._execute(
            "SELECT * FROM question_mappings WHERE chat_id = ? AND telegram_message_id = ?",
            (chat_id, telegram_message_id),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def delete_expired_question_mappings(self) -> int:
        now = self._now()
        cursor = await self._execute(
            "DELETE FROM question_mappings WHERE expires_at < ?",
            (now,),
        )
        await self._connection.commit()  # type: ignore[union-attr]
        return cursor.rowcount

    # Inbox

    async def create_inbox(
        self,
        message_id: str,
        token: str,
        telegram_user_id: str,
        chat_id: str,
        question_id: str | None,
        text: str,
        ttl_seconds: int,
    ) -> None:
        now = self._now()
        expires = self._expiry(ttl_seconds)
        await self._execute(
            """
            INSERT INTO inbox (id, token, telegram_user_id, chat_id, question_id, text, received_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (message_id, token, telegram_user_id, chat_id, question_id, text, now, expires),
        )
        await self._connection.commit()  # type: ignore[union-attr]

    async def fetch_inbox(
        self,
        question_id: str | None = None,
        token: str | None = None,
    ) -> list[dict[str, Any]]:
        if not question_id and not token:
            return []
        conditions: list[str] = []
        params: list[str | None] = []
        if question_id:
            conditions.append("question_id = ?")
            params.append(question_id)
        if token:
            conditions.append("token = ?")
            params.append(token)
        where = " AND ".join(conditions)
        cursor = await self._execute(
            f"SELECT * FROM inbox WHERE {where} ORDER BY received_at",
            tuple(params),
        )
        rows = await cursor.fetchall()
        messages = [dict(row) for row in rows]
        if messages:
            ids = tuple(str(row["id"]) for row in messages)
            placeholders = ",".join("?" * len(ids))
            await self._execute(
                f"DELETE FROM inbox WHERE id IN ({placeholders})",
                ids,
            )
            await self._connection.commit()  # type: ignore[union-attr]
        return messages

    async def delete_expired_inbox(self) -> int:
        now = self._now()
        cursor = await self._execute(
            "DELETE FROM inbox WHERE expires_at < ?",
            (now,),
        )
        await self._connection.commit()  # type: ignore[union-attr]
        return cursor.rowcount

    # Idempotency

    async def get_idempotency_response(self, key: str) -> dict[str, Any] | None:
        now = self._now()
        cursor = await self._execute(
            "SELECT response_json FROM idempotency_cache WHERE key = ? AND expires_at > ?",
            (key, now),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return json.loads(row["response_json"])

    async def set_idempotency_response(
        self,
        key: str,
        response: dict[str, Any],
        ttl_seconds: int = 86400,
    ) -> None:
        expires = self._expiry(ttl_seconds)
        await self._execute(
            "INSERT OR REPLACE INTO idempotency_cache (key, response_json, expires_at) VALUES (?, ?, ?)",
            (key, json.dumps(response), expires),
        )
        await self._connection.commit()  # type: ignore[union-attr]

    async def __aenter__(self) -> Store:
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: Any,
    ) -> None:
        await self.close()
