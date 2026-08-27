from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from memory_agent_demo.app.main import create_app
from memory_agent_demo.app.memory_store import MemoryCandidate, MemoryStore
from memory_agent_demo.app.model_gateway import NoopEmbedder, OfflineDemoModel
from memory_agent_demo.app.service import MemoryAgentService


class TinyEmbedder:
    mode = "test-embedding"

    def embed(self, text: str) -> list[float]:
        return [1.0, 0.0] if any(word in text for word in ("名字", "叫", "姓名")) else [0.0, 1.0]


def test_service_recalls_memory_across_sessions(tmp_path: Path):
    database = MemoryStore(tmp_path / "memory.db")
    service = MemoryAgentService(database, OfflineDemoModel(), TinyEmbedder())

    first = service.chat("demo-user", "我叫小林")
    second_session = database.create_session("demo-user")
    second = service.chat("demo-user", "你还记得我的名字吗？", second_session["id"])

    assert first["written_memories"][0]["memory"]["normalized_key"] == "name"
    assert second["session_id"] != first["session_id"]
    assert second["recalled_memories"][0]["content"] == "用户的名字是小林"
    assert "用户的名字是小林" in second["answer"]
    assert second["written_memories"] == []
    assert database.list_memories("demo-user")[0]["content"] == "用户的名字是小林"


def test_offline_question_does_not_overwrite_name(tmp_path: Path):
    database = MemoryStore(tmp_path / "memory.db")
    service = MemoryAgentService(database, OfflineDemoModel(), NoopEmbedder())
    first = service.chat("demo-user", "我的名字是小林")

    result = service.chat("demo-user", "我的名字是什么？", first["session_id"])

    assert result["written_memories"] == []
    assert database.list_memories("demo-user")[0]["content"] == "用户的名字是小林"


def test_short_term_history_is_compacted_but_raw_messages_remain(tmp_path: Path):
    database = MemoryStore(tmp_path / "memory.db")
    service = MemoryAgentService(database, OfflineDemoModel(), NoopEmbedder(), short_term_limit=6)
    session_id = None
    for index in range(5):
        result = service.chat("demo-user", f"这是普通消息 {index}", session_id)
        session_id = result["session_id"]

    session = database.get_session(session_id)
    assert session["summary"]
    assert session["summary_cutoff"] > 0
    assert len(database.list_messages(session_id)) == 10
    assert len(database.recent_messages(session_id, 6)) == 6


def test_api_chat_memory_lifecycle_and_health(tmp_path: Path):
    app = create_app(
        db_path=tmp_path / "api.db",
        model=OfflineDemoModel(),
        embedder=NoopEmbedder(),
    )
    client = TestClient(app)

    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["retrieval_mode"] == "fts-only"

    chat = client.post(
        "/api/chat",
        json={"user_id": "demo-user", "message": "我喜欢喝乌龙茶"},
    )
    assert chat.status_code == 200
    assert chat.json()["written_memories"]

    memories = client.get("/api/memories").json()
    assert len(memories) == 1
    memory_id = memories[0]["id"]

    archived = client.patch(f"/api/memories/{memory_id}", json={"status": "archived"})
    assert archived.status_code == 200
    assert client.get("/api/memories?status=archived").json()[0]["id"] == memory_id

    restored = client.patch(f"/api/memories/{memory_id}", json={"status": "active"})
    assert restored.status_code == 200
    deleted = client.delete(f"/api/memories/{memory_id}/permanent")
    assert deleted.status_code == 204
    assert client.get("/api/memories").json() == []
