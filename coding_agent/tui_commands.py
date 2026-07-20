from __future__ import annotations

import json
from pathlib import Path

from rich.table import Table

from .events import EventStore
from .state import MessageBus


def runs_table(workspace: Path) -> Table:
    table = Table("Run ID", "Status", "Events", "Tools", "Tokens", "Duration")
    for run in EventStore.list_runs(workspace):
        table.add_row(
            run["run_id"], run["status"], str(run["events"]), str(run["tool_calls"]),
            str(run["input_tokens"] + run["output_tokens"]), f"{run['duration_seconds']:.2f}s",
        )
    return table


def events_table(workspace: Path, run_id: str, replay: bool = False) -> Table | None:
    path = workspace / ".runs" / run_id / "events.jsonl"
    if not run_id or not path.exists():
        return None
    events = EventStore(workspace, run_id).read_events()
    table = Table("Time", "Actor", "Event", "Details", title="Read-only replay" if replay else "Run inspection")
    started = events[0]["timestamp"] if events else 0
    for event in events:
        payload = json.dumps(event.get("payload", {}), ensure_ascii=False, default=str)
        table.add_row(
            f"+{event.get('timestamp', 0) - started:.2f}s", event.get("actor", ""),
            event.get("type", ""), payload[:160],
        )
    return table


def messages_table(bus: MessageBus, status: str | None = None) -> Table:
    table = Table("ID", "Status", "Type", "From", "To", "Task", "Attempts", "Content")
    for message in bus.list_messages(status=status, limit=100):
        table.add_row(
            message["message_id"][:8], message["status"], message["type"], message["sender"],
            message["recipient"], str(message.get("task_id") or ""),
            str(message["delivery_attempts"]),
            json.dumps(message["content"], ensure_ascii=False, default=str)[:100],
        )
    return table
