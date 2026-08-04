from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from memory_agent_demo.app.memory_store import MemoryCandidate, MemoryStore


def store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(tmp_path / "memory.db")


def test_hybrid_search_can_find_semantically_related_memory(tmp_path: Path):
    database = store(tmp_path)
    database.add_memory(
        "demo-user",
        MemoryCandidate(
            type="preference",
            content="用户吃饭时避免动物制品",
            normalized_key="diet_preference",
            confidence=0.95,
            importance=0.8,
        ),
        embedding=[1.0, 0.0],
    )
    database.add_memory(
        "demo-user",
        MemoryCandidate(
            type="semantic",
            content="用户主要使用 Python",
            normalized_key="programming_language",
        ),
        embedding=[0.0, 1.0],
    )

    results = database.search(
        "demo-user",
        "我有什么饮食偏好？",
        query_embedding=[1.0, 0.0],
        reinforce=False,
    )

    assert results[0]["normalized_key"] == "diet_preference"
    assert results[0]["score_breakdown"]["semantic"] == 1.0
    assert "语义相似" in results[0]["reason"]


def test_same_key_reinforces_or_supersedes_memory(tmp_path: Path):
    database = store(tmp_path)
    first, action = database.add_memory(
        "demo-user",
        MemoryCandidate("semantic", "用户的名字是小林", "name", confidence=0.8),
    )
    repeated, action_repeated = database.add_memory(
        "demo-user",
        MemoryCandidate("semantic", "用户的名字是小林", "name", confidence=0.95),
    )
    replacement, action_replacement = database.add_memory(
        "demo-user",
        MemoryCandidate("semantic", "用户的名字是林然", "name", confidence=0.99),
    )

    assert action == "created"
    assert action_repeated == "reinforced"
    assert repeated["id"] == first["id"]
    assert repeated["confidence"] == 0.95
    assert action_replacement == "superseded"
    assert replacement["status"] == "active"
    assert database.get_memory(first["id"])["status"] == "superseded"
    assert database.get_memory(first["id"])["superseded_by"] == replacement["id"]


def test_consolidation_archives_low_value_stale_memory(tmp_path: Path):
    database = store(tmp_path)
    memory, _ = database.add_memory(
        "demo-user",
        MemoryCandidate(
            "semantic",
            "一条很久没有使用的低价值信息",
            "old_fact",
            confidence=0.1,
            importance=0.1,
        ),
    )
    old = (datetime.now(UTC) - timedelta(days=240)).isoformat()
    with database.connect() as connection:
        connection.execute(
            "UPDATE memories SET updated_at=?, last_accessed_at=? WHERE id=?",
            (old, old, memory["id"]),
        )

    result = database.consolidate("demo-user")

    assert result["archived"] == 1
    assert database.get_memory(memory["id"])["status"] == "archived"


def test_memory_isolation_by_user(tmp_path: Path):
    database = store(tmp_path)
    database.add_memory(
        "alice",
        MemoryCandidate("preference", "Alice 喜欢咖啡", "drink_preference"),
    )
    database.add_memory(
        "bob",
        MemoryCandidate("preference", "Bob 喜欢茶", "drink_preference"),
    )

    assert [item["content"] for item in database.list_memories("alice")] == ["Alice 喜欢咖啡"]
    assert [item["content"] for item in database.list_memories("bob")] == ["Bob 喜欢茶"]

