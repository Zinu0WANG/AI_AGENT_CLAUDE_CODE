from __future__ import annotations

import json
import math
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable


MEMORY_TYPES = {"episodic", "semantic", "preference", "prospective", "procedural"}
MEMORY_STATUSES = {"active", "archived", "deleted", "superseded"}
TYPE_HINTS = {
    "preference": ("喜欢", "偏好", "讨厌", "不喜欢", "风格", "prefer", "like"),
    "prospective": ("计划", "提醒", "待办", "下周", "明天", "准备", "todo", "plan"),
    "episodic": ("上次", "昨天", "曾经", "去过", "发生", "last time", "yesterday"),
    "procedural": ("每次", "总是", "按照", "步骤", "流程", "always", "workflow"),
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().casefold())


def _tokens(text: str) -> set[str]:
    normalized = normalize_text(text)
    latin = re.findall(r"[a-z0-9_]{2,}", normalized)
    han = re.findall(r"[\u3400-\u9fff]", normalized)
    han_bigrams = [a + b for a, b in zip(han, han[1:])]
    return set(latin + han + han_bigrams)


def lexical_similarity(query: str, content: str) -> float:
    left, right = _tokens(query), _tokens(content)
    if not left or not right:
        return 0.0
    return 2 * len(left & right) / (len(left) + len(right))


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    norm = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return dot / norm if norm else 0.0


def _age_days(timestamp: str | None) -> float:
    if not timestamp:
        return 3650.0
    try:
        value = datetime.fromisoformat(timestamp)
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return max(0.0, (datetime.now(UTC) - value).total_seconds() / 86400)
    except ValueError:
        return 3650.0


@dataclass(slots=True)
class MemoryCandidate:
    type: str
    content: str
    normalized_key: str
    confidence: float = 0.8
    importance: float = 0.5
    valid_from: str | None = None
    valid_to: str | None = None


class MemoryStore:
    def __init__(self, path: Path):
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    summary TEXT NOT NULL DEFAULT '',
                    summary_cutoff INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    sequence INTEGER NOT NULL,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(session_id, sequence)
                );
                CREATE INDEX IF NOT EXISTS idx_messages_session
                    ON messages(session_id, sequence);
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    normalized_key TEXT NOT NULL,
                    importance REAL NOT NULL,
                    confidence REAL NOT NULL,
                    embedding TEXT,
                    status TEXT NOT NULL,
                    valid_from TEXT,
                    valid_to TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_accessed_at TEXT,
                    access_count INTEGER NOT NULL DEFAULT 0,
                    source_session_id TEXT,
                    source_message_id TEXT,
                    superseded_by TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_memories_lookup
                    ON memories(user_id, status, type);
                CREATE INDEX IF NOT EXISTS idx_memories_key
                    ON memories(user_id, type, normalized_key);
                CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                    memory_id UNINDEXED,
                    content,
                    normalized_key,
                    tokenize='unicode61'
                );
                """
            )

    @staticmethod
    def _decode(row: sqlite3.Row | dict) -> dict:
        item = dict(row)
        if item.get("embedding"):
            try:
                item["embedding"] = json.loads(item["embedding"])
            except (TypeError, json.JSONDecodeError):
                item["embedding"] = None
        return item

    def create_session(self, user_id: str) -> dict:
        session_id, now = uuid.uuid4().hex, utc_now()
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO sessions(id, user_id, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (session_id, user_id, now, now),
            )
        return self.get_session(session_id)

    def get_session(self, session_id: str) -> dict:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if not row:
            raise ValueError("session not found")
        return dict(row)

    def add_message(self, session_id: str, role: str, content: str) -> dict:
        if role not in {"user", "assistant"}:
            raise ValueError("invalid message role")
        with self.connect() as connection:
            if not connection.execute("SELECT 1 FROM sessions WHERE id=?", (session_id,)).fetchone():
                raise ValueError("session not found")
            sequence = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM messages WHERE session_id=?",
                (session_id,),
            ).fetchone()[0]
            item = {
                "id": uuid.uuid4().hex,
                "sequence": sequence,
                "session_id": session_id,
                "role": role,
                "content": content,
                "created_at": utc_now(),
            }
            connection.execute(
                """INSERT INTO messages(id, sequence, session_id, role, content, created_at)
                   VALUES (:id, :sequence, :session_id, :role, :content, :created_at)""",
                item,
            )
            connection.execute("UPDATE sessions SET updated_at=? WHERE id=?", (utc_now(), session_id))
        return item

    def list_messages(
        self, session_id: str, *, after_sequence: int = 0, limit: int | None = None
    ) -> list[dict]:
        sql = "SELECT * FROM messages WHERE session_id=? AND sequence>? ORDER BY sequence"
        values: list[object] = [session_id, after_sequence]
        if limit is not None:
            sql += " LIMIT ?"
            values.append(limit)
        with self.connect() as connection:
            rows = connection.execute(sql, values).fetchall()
        return [dict(row) for row in rows]

    def recent_messages(self, session_id: str, limit: int = 12) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM (
                       SELECT * FROM messages WHERE session_id=? ORDER BY sequence DESC LIMIT ?
                   ) ORDER BY sequence""",
                (session_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_summary(self, session_id: str, summary: str, cutoff: int) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE sessions SET summary=?, summary_cutoff=?, updated_at=? WHERE id=?",
                (summary, cutoff, utc_now(), session_id),
            )

    def add_memory(
        self,
        user_id: str,
        candidate: MemoryCandidate,
        *,
        embedding: list[float] | None = None,
        source_session_id: str | None = None,
        source_message_id: str | None = None,
    ) -> tuple[dict, str]:
        if candidate.type not in MEMORY_TYPES:
            raise ValueError(f"invalid memory type: {candidate.type}")
        content = candidate.content.strip()
        if not content:
            raise ValueError("memory content is required")
        key = normalize_text(candidate.normalized_key or content)
        now = utc_now()
        with self.connect() as connection:
            existing = connection.execute(
                """SELECT * FROM memories
                   WHERE user_id=? AND type=? AND normalized_key=? AND status='active'
                   ORDER BY updated_at DESC LIMIT 1""",
                (user_id, candidate.type, key),
            ).fetchone()
            if existing and normalize_text(existing["content"]) == normalize_text(content):
                connection.execute(
                    """UPDATE memories SET confidence=MAX(confidence, ?),
                       importance=MAX(importance, ?), updated_at=? WHERE id=?""",
                    (candidate.confidence, candidate.importance, now, existing["id"]),
                )
                connection.commit()
                return self.get_memory(existing["id"]), "reinforced"

            memory_id = uuid.uuid4().hex
            if existing:
                connection.execute(
                    "UPDATE memories SET status='superseded', superseded_by=?, updated_at=? WHERE id=?",
                    (memory_id, now, existing["id"]),
                )
                connection.execute("DELETE FROM memories_fts WHERE memory_id=?", (existing["id"],))
            item = {
                "id": memory_id,
                "user_id": user_id,
                "type": candidate.type,
                "content": content,
                "normalized_key": key,
                "importance": max(0.0, min(1.0, candidate.importance)),
                "confidence": max(0.0, min(1.0, candidate.confidence)),
                "embedding": json.dumps(embedding) if embedding else None,
                "status": "active",
                "valid_from": candidate.valid_from,
                "valid_to": candidate.valid_to,
                "created_at": now,
                "updated_at": now,
                "last_accessed_at": None,
                "access_count": 0,
                "source_session_id": source_session_id,
                "source_message_id": source_message_id,
                "superseded_by": None,
            }
            connection.execute(
                """INSERT INTO memories(
                    id,user_id,type,content,normalized_key,importance,confidence,embedding,
                    status,valid_from,valid_to,created_at,updated_at,last_accessed_at,
                    access_count,source_session_id,source_message_id,superseded_by
                ) VALUES (
                    :id,:user_id,:type,:content,:normalized_key,:importance,:confidence,:embedding,
                    :status,:valid_from,:valid_to,:created_at,:updated_at,:last_accessed_at,
                    :access_count,:source_session_id,:source_message_id,:superseded_by
                )""",
                item,
            )
            connection.execute(
                "INSERT INTO memories_fts(memory_id, content, normalized_key) VALUES (?, ?, ?)",
                (memory_id, content, key),
            )
        return self.get_memory(memory_id), "superseded" if existing else "created"

    def get_memory(self, memory_id: str) -> dict:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
        if not row:
            raise ValueError("memory not found")
        return self._decode(row)

    def list_memories(
        self, user_id: str, *, status: str = "active", memory_type: str | None = None
    ) -> list[dict]:
        clauses, values = ["user_id=?", "status=?"], [user_id, status]
        if memory_type:
            clauses.append("type=?")
            values.append(memory_type)
        with self.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM memories WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC",
                values,
            ).fetchall()
        return [self._decode(row) for row in rows]

    def update_memory(self, memory_id: str, changes: dict) -> dict:
        allowed = {"content", "type", "importance", "confidence", "status", "valid_from", "valid_to"}
        updates = {key: value for key, value in changes.items() if key in allowed}
        if not updates:
            return self.get_memory(memory_id)
        if "type" in updates and updates["type"] not in MEMORY_TYPES:
            raise ValueError("invalid memory type")
        if "status" in updates and updates["status"] not in MEMORY_STATUSES:
            raise ValueError("invalid memory status")
        for key in ("importance", "confidence"):
            if key in updates:
                updates[key] = max(0.0, min(1.0, float(updates[key])))
        updates["updated_at"] = utc_now()
        values = list(updates.values()) + [memory_id]
        assignments = ", ".join(f"{key}=?" for key in updates)
        with self.connect() as connection:
            if not connection.execute("SELECT 1 FROM memories WHERE id=?", (memory_id,)).fetchone():
                raise ValueError("memory not found")
            connection.execute(f"UPDATE memories SET {assignments} WHERE id=?", values)
            connection.execute("DELETE FROM memories_fts WHERE memory_id=?", (memory_id,))
            row = connection.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
            if row["status"] == "active":
                connection.execute(
                    "INSERT INTO memories_fts(memory_id, content, normalized_key) VALUES (?, ?, ?)",
                    (memory_id, row["content"], row["normalized_key"]),
                )
        return self.get_memory(memory_id)

    def permanent_delete(self, memory_id: str) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM memories_fts WHERE memory_id=?", (memory_id,))
            if not connection.execute("DELETE FROM memories WHERE id=?", (memory_id,)).rowcount:
                raise ValueError("memory not found")

    @staticmethod
    def _type_match(query: str, memory_type: str) -> float:
        hints = TYPE_HINTS.get(memory_type, ())
        return 1.0 if any(hint in query.casefold() for hint in hints) else 0.5

    def search(
        self,
        user_id: str,
        query: str,
        *,
        query_embedding: list[float] | None = None,
        memory_types: Iterable[str] | None = None,
        limit: int = 6,
        reinforce: bool = True,
    ) -> list[dict]:
        types = set(memory_types or MEMORY_TYPES)
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM memories WHERE user_id=? AND status='active'",
                (user_id,),
            ).fetchall()
        results = []
        for row in rows:
            item = self._decode(row)
            if item["type"] not in types:
                continue
            lexical = lexical_similarity(query, item["content"] + " " + item["normalized_key"])
            semantic = 0.0
            if query_embedding and item.get("embedding"):
                semantic = (cosine_similarity(query_embedding, item["embedding"]) + 1) / 2
            half_life = 180 if item["type"] in {"semantic", "preference", "procedural"} else 30
            recency = 0.5 ** (_age_days(item["last_accessed_at"] or item["updated_at"]) / half_life)
            type_match = self._type_match(query, item["type"])
            if query_embedding and item.get("embedding"):
                score = (
                    0.45 * semantic
                    + 0.25 * lexical
                    + 0.10 * recency
                    + 0.10 * item["importance"]
                    + 0.05 * item["confidence"]
                    + 0.05 * type_match
                )
            else:
                score = (
                    0.55 * lexical
                    + 0.15 * recency
                    + 0.15 * item["importance"]
                    + 0.10 * item["confidence"]
                    + 0.05 * type_match
                )
            if query_embedding and item.get("embedding"):
                if lexical < 0.12 and semantic < 0.60:
                    continue
            elif lexical < 0.12:
                continue
            reasons = []
            if semantic >= 0.75:
                reasons.append("语义相似")
            if lexical >= 0.25:
                reasons.append("关键词匹配")
            if item["importance"] >= 0.75:
                reasons.append("高重要度")
            if type_match == 1:
                reasons.append("记忆类型匹配")
            item["score"] = round(score, 4)
            item["score_breakdown"] = {
                "semantic": round(semantic, 4),
                "lexical": round(lexical, 4),
                "recency": round(recency, 4),
                "importance": item["importance"],
                "confidence": item["confidence"],
                "type_match": type_match,
            }
            item["reason"] = " + ".join(reasons) or "综合相关性"
            results.append(item)
        results.sort(key=lambda item: (item["score"], item["updated_at"]), reverse=True)

        selected, per_type = [], {}
        for item in results:
            if per_type.get(item["type"], 0) >= 4:
                continue
            selected.append(item)
            per_type[item["type"]] = per_type.get(item["type"], 0) + 1
            if len(selected) >= max(1, min(limit, 20)):
                break

        if reinforce and selected:
            now = utc_now()
            with self.connect() as connection:
                connection.executemany(
                    """UPDATE memories SET last_accessed_at=?, access_count=access_count+1
                       WHERE id=?""",
                    [(now, item["id"]) for item in selected],
                )
        return selected

    def consolidate(self, user_id: str, *, inactive_days: int = 90, threshold: float = 0.35) -> dict:
        active = self.list_memories(user_id, status="active")
        archived = []
        for item in active:
            if item["type"] in {"preference", "prospective"}:
                continue
            age = _age_days(item["last_accessed_at"] or item["updated_at"])
            recency = 0.5 ** (age / 90)
            retention = 0.5 * item["importance"] + 0.3 * item["confidence"] + 0.2 * recency
            if age >= inactive_days and retention < threshold:
                self.update_memory(item["id"], {"status": "archived"})
                archived.append(item["id"])
        return {"examined": len(active), "archived": len(archived), "archived_ids": archived}
