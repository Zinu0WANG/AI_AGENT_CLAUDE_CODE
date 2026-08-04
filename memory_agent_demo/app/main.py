from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import load_settings
from .memory_store import MEMORY_TYPES, MemoryCandidate, MemoryStore
from .model_gateway import ChatModel, Embedder, create_embedder, create_model
from .service import MemoryAgentService


class SessionCreate(BaseModel):
    user_id: str = "demo-user"


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    session_id: str | None = None
    user_id: str = "demo-user"


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    user_id: str = "demo-user"
    types: list[str] | None = None
    limit: int = Field(default=6, ge=1, le=20)


class ManualMemoryCreate(BaseModel):
    user_id: str = "demo-user"
    type: Literal["episodic", "semantic", "preference", "prospective", "procedural"]
    content: str = Field(min_length=1, max_length=2000)
    normalized_key: str | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)
    importance: float = Field(default=0.7, ge=0, le=1)


class MemoryUpdate(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=2000)
    type: Literal["episodic", "semantic", "preference", "prospective", "procedural"] | None = None
    importance: float | None = Field(default=None, ge=0, le=1)
    confidence: float | None = Field(default=None, ge=0, le=1)
    status: Literal["active", "archived", "deleted"] | None = None
    valid_from: str | None = None
    valid_to: str | None = None


def create_app(
    *,
    db_path: Path | None = None,
    model: ChatModel | None = None,
    embedder: Embedder | None = None,
) -> FastAPI:
    settings = load_settings(db_path)
    store = MemoryStore(settings.db_path)
    selected_model = model or create_model(settings)
    selected_embedder = embedder or create_embedder(settings)
    service = MemoryAgentService(store, selected_model, selected_embedder)

    app = FastAPI(title="Memory Agent Demo", version="0.1.0")
    app.state.settings = settings
    app.state.store = store
    app.state.service = service
    app.state.embedder = selected_embedder

    @app.get("/api/health")
    def health() -> dict:
        return {
            "status": "ok",
            "chat_mode": selected_model.mode,
            "retrieval_mode": selected_embedder.mode,
            "database": str(settings.db_path),
        }

    @app.post("/api/sessions")
    def create_session(payload: SessionCreate) -> dict:
        return store.create_session(payload.user_id)

    @app.get("/api/sessions/{session_id}")
    def get_session(session_id: str) -> dict:
        try:
            session = store.get_session(session_id)
            session["messages"] = store.list_messages(session_id)
            return session
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/chat")
    def chat(payload: ChatRequest) -> dict:
        try:
            return service.chat(payload.user_id, payload.message, payload.session_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/memories")
    def list_memories(
        user_id: str = "demo-user",
        status: str = Query(default="active", pattern="^(active|archived|deleted|superseded)$"),
        memory_type: str | None = Query(default=None, alias="type"),
    ) -> list[dict]:
        if memory_type and memory_type not in MEMORY_TYPES:
            raise HTTPException(status_code=400, detail="invalid memory type")
        return store.list_memories(user_id, status=status, memory_type=memory_type)

    @app.post("/api/memories")
    def create_memory(payload: ManualMemoryCreate) -> dict:
        candidate = MemoryCandidate(
            type=payload.type,
            content=payload.content,
            normalized_key=payload.normalized_key or payload.content,
            confidence=payload.confidence,
            importance=payload.importance,
        )
        vector = selected_embedder.embed(payload.content)
        memory, action = store.add_memory(payload.user_id, candidate, embedding=vector)
        return {"action": action, "memory": memory}

    @app.post("/api/memories/search")
    def search_memories(payload: SearchRequest) -> list[dict]:
        if payload.types and not set(payload.types) <= MEMORY_TYPES:
            raise HTTPException(status_code=400, detail="invalid memory type")
        vector = selected_embedder.embed(payload.query)
        return store.search(
            payload.user_id,
            payload.query,
            query_embedding=vector,
            memory_types=payload.types,
            limit=payload.limit,
            reinforce=False,
        )

    @app.patch("/api/memories/{memory_id}")
    def update_memory(memory_id: str, payload: MemoryUpdate) -> dict:
        try:
            return store.update_memory(memory_id, payload.model_dump(exclude_none=True))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.delete("/api/memories/{memory_id}/permanent", status_code=204)
    def permanent_delete(memory_id: str) -> None:
        try:
            store.permanent_delete(memory_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/maintenance/consolidate")
    def consolidate(user_id: str = "demo-user") -> dict:
        return store.consolidate(user_id)

    static_dir = settings.project_dir / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("memory_agent_demo.app.main:app", host="127.0.0.1", port=8000, reload=True)

