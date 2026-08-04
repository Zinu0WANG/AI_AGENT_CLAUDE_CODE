from __future__ import annotations

from .memory_store import MemoryStore
from .model_gateway import ChatModel, Embedder


class MemoryAgentService:
    def __init__(
        self,
        store: MemoryStore,
        model: ChatModel,
        embedder: Embedder,
        *,
        short_term_limit: int = 12,
        confidence_threshold: float = 0.75,
    ):
        self.store = store
        self.model = model
        self.embedder = embedder
        self.short_term_limit = short_term_limit
        self.confidence_threshold = confidence_threshold

    def chat(self, user_id: str, message: str, session_id: str | None = None) -> dict:
        message = message.strip()
        if not message:
            raise ValueError("message is required")
        session = self.store.get_session(session_id) if session_id else self.store.create_session(user_id)
        if session["user_id"] != user_id:
            raise ValueError("session does not belong to user")

        user_message = self.store.add_message(session["id"], "user", message)
        query_embedding = self.embedder.embed(message)
        recalled = self.store.search(
            user_id,
            message,
            query_embedding=query_embedding,
            limit=6,
        )
        recent = self.store.recent_messages(session["id"], self.short_term_limit)
        answer = self.model.reply(message, recent, session["summary"], recalled)
        assistant_message = self.store.add_message(session["id"], "assistant", answer)

        written = []
        for candidate in self.model.extract_memories(message):
            if candidate.confidence < self.confidence_threshold:
                continue
            embedding = self.embedder.embed(candidate.content)
            memory, action = self.store.add_memory(
                user_id,
                candidate,
                embedding=embedding,
                source_session_id=session["id"],
                source_message_id=user_message["id"],
            )
            written.append({"action": action, "memory": memory})

        self._compact_session(session["id"])
        session = self.store.get_session(session["id"])
        return {
            "session_id": session["id"],
            "answer": answer,
            "recalled_memories": recalled,
            "written_memories": written,
            "short_term": {
                "summary": session["summary"],
                "recent_messages": self.store.recent_messages(session["id"], self.short_term_limit),
            },
            "mode": {"chat": self.model.mode, "retrieval": self.embedder.mode},
            "assistant_message_id": assistant_message["id"],
        }

    def _compact_session(self, session_id: str) -> None:
        session = self.store.get_session(session_id)
        unsummarized = self.store.list_messages(
            session_id, after_sequence=session["summary_cutoff"]
        )
        if len(unsummarized) <= self.short_term_limit:
            return
        retain_count = max(2, min(8, self.short_term_limit - 2))
        to_summarize = unsummarized[:-retain_count]
        if not to_summarize:
            return
        summary = self.model.summarize(session["summary"], to_summarize)
        self.store.update_summary(session_id, summary, to_summarize[-1]["sequence"])
