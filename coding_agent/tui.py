from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv
from rich.table import Table
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import Button, Collapsible, Footer, Markdown, RichLog, Static, TextArea

from .config import AgentConfig
from .events import AgentEvent, EventStore
from .plans import PlanStore
from .policy import RiskLevel
from .runtime import AgentRuntime, AnthropicModel, ModelClient, RunMode, RunResult
from .state import MessageBus, TaskManager
from .tui_commands import events_table, messages_table, runs_table
from .tui_widgets import ApprovalScreen, RunResultView, diff_stats, quality_gate_status


SUPPORTED_COMMANDS = {
    "/plan", "/plans", "/show-plan", "/implement", "/runs", "/inspect", "/replay",
    "/team", "/messages", "/retry-message", "/diff", "/test", "/abort", "/help",
}


class LiveAgentEvent(Message):
    def __init__(self, event: AgentEvent):
        super().__init__()
        self.event = event


def _event_summary(event: AgentEvent) -> str:
    payload = event.payload
    if event.type == "tool_requested":
        return f"requested {payload.get('tool', 'tool')}"
    if event.type == "tool_started":
        return f"started {payload.get('tool', 'tool')} · {payload.get('risk', 'unknown')}"
    if event.type == "tool_finished":
        state = "ok" if payload.get("ok", True) else "failed"
        return f"{payload.get('tool', 'tool')} · {state}"
    if event.type == "model_response":
        return f"model response · step {payload.get('step', '-')} · {payload.get('stop_reason', '')}"
    if event.type.startswith("message_"):
        return f"{event.type.replace('_', ' ')} · {payload.get('message_id', '')[:8]}"
    if event.type == "validation_finished":
        return f"quality gate · {'pass' if payload.get('passed') else 'fail'}"
    if event.type in {"run_completed", "run_failed"}:
        return event.type.replace("_", " ")
    return event.type.replace("_", " ")


class AgentTUI(App[None]):
    """Full-screen interview UI over the synchronous agent runtime."""

    TITLE = "Coding Agent"
    SUB_TITLE = "Observable multi-agent runtime"
    BINDINGS = [
        ("ctrl+enter", "submit_prompt", "Send"),
        ("ctrl+x", "abort_run", "Stop"),
        ("e", "expand_all", "Expand details"),
        ("c", "collapse_all", "Collapse details"),
        ("ctrl+q", "quit", "Quit"),
    ]
    CSS = """
    Screen { background: $background; }
    #status-bar {
        height: 3;
        padding: 1 2;
        background: $surface;
        color: $text-muted;
        border-bottom: solid $primary-darken-2;
    }
    #transcript { height: 1fr; padding: 0 2; }
    #timeline { height: auto; min-height: 6; max-height: 18; margin: 1 0; }
    #composer { height: 9; padding: 1 2; background: $surface; }
    #prompt { height: 6; border: round $primary-darken-1; }
    #composer-actions { width: 16; height: 6; margin-left: 1; }
    #composer-actions Button { width: 100%; margin-bottom: 1; }
    .user-message { border-left: thick $primary; padding: 1 2; margin-top: 1; height: auto; }
    """

    def __init__(self, workspace: Path | None = None, model_client: ModelClient | None = None):
        load_dotenv(override=True)
        super().__init__()
        self.workspace = (workspace or Path.cwd()).resolve()
        self.config = AgentConfig.load(self.workspace)
        self.model = model_client or self._create_model()
        self.plan_store = PlanStore(self.workspace, self.config.ignore_patterns, self.config.max_file_bytes)
        self.current_runtime: AgentRuntime | None = None
        self.last_result: RunResult | None = None
        self.busy = False
        self.run_started_at = 0.0

    def _create_model(self) -> AnthropicModel:
        model_name = os.getenv("MODEL_ID")
        api_key = os.getenv("ANTHROPIC_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
        if not model_name:
            raise RuntimeError("MODEL_ID is required; copy .env.example to .env")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY or DASHSCOPE_API_KEY is required")
        kwargs = {"api_key": api_key}
        if os.getenv("ANTHROPIC_BASE_URL"):
            os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)
            kwargs["base_url"] = os.environ["ANTHROPIC_BASE_URL"]
        return AnthropicModel(Anthropic(**kwargs), model_name)

    def compose(self) -> ComposeResult:
        model_name = getattr(self.model, "model", "custom")
        yield Static(f"Workspace  {self.workspace}    Model  {model_name}    Status  READY", id="status-bar")
        with VerticalScroll(id="transcript"):
            yield Static("Enter a request below. Paste multiple lines, then press Ctrl+Enter once to send.")
            yield RichLog(id="timeline", markup=True, wrap=True, highlight=False)
        with Horizontal(id="composer"):
            yield TextArea(id="prompt", language="markdown", show_line_numbers=False, tab_behavior="focus")
            with Vertical(id="composer-actions"):
                yield Button("Send  Ctrl+Enter", id="send", variant="primary")
                yield Button("Stop  Ctrl+X", id="stop", variant="error", disabled=True)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#prompt", TextArea).focus()
        self.set_interval(0.25, self._refresh_status)

    def _refresh_status(self) -> None:
        if not self.busy:
            return
        model_name = getattr(self.model, "model", "custom")
        run_id = self.current_runtime.events.run_id[:8] if self.current_runtime else "starting"
        elapsed = max(0.0, time.monotonic() - self.run_started_at) if self.run_started_at else 0.0
        self.query_one("#status-bar", Static).update(
            f"Workspace  {self.workspace}    Model  {model_name}    Run  {run_id}    "
            f"Status  RUNNING    Elapsed  {elapsed:.1f}s"
        )

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.query_one("#send", Button).disabled = busy
        self.query_one("#stop", Button).disabled = not busy
        self.query_one("#prompt", TextArea).disabled = busy

    def action_submit_prompt(self) -> None:
        if self.busy:
            self.notify("A run is already active.", severity="warning")
            return
        editor = self.query_one("#prompt", TextArea)
        prompt = editor.text.strip()
        if not prompt:
            self.notify("Enter a request first.", severity="warning")
            return
        editor.clear()
        self.query_one("#transcript", VerticalScroll).mount(Static(prompt, classes="user-message"))
        if prompt.startswith("/"):
            self.handle_command(prompt)
            return
        self.query_one("#timeline", RichLog).write("[bold cyan]USER[/bold cyan]  request submitted")
        self._set_busy(True)
        self._execute_prompt(prompt)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "send":
            self.action_submit_prompt()
        elif event.button.id == "stop":
            self.action_abort_run()

    def _post_event(self, event: AgentEvent) -> None:
        self.post_message(LiveAgentEvent(event))

    def on_live_agent_event(self, message: LiveAgentEvent) -> None:
        event = message.event
        self.query_one("#timeline", RichLog).write(
            f"[dim]{event.actor:>10}[/dim]  {_event_summary(event)}"
        )

    def _runtime_started(self, runtime: AgentRuntime) -> None:
        self.current_runtime = runtime
        self.run_started_at = time.monotonic()
        self._refresh_status()

    @work(thread=True, exclusive=True, group="agent-run")
    def _execute_prompt(self, prompt: str) -> None:
        runtime = AgentRuntime(
            self.workspace, self.config, self.model, self._approve,
            event_callback=self._post_event,
        )
        self.call_from_thread(self._runtime_started, runtime)
        result = runtime.run(prompt)
        self.call_from_thread(self._finish_run, result)

    def _finish_run(self, result: RunResult) -> None:
        self.last_result = result
        self.query_one("#transcript", VerticalScroll).mount(
            RunResultView(result, len(self.config.lint_commands) + len(self.config.test_commands))
        )
        model_name = getattr(self.model, "model", "custom")
        self.query_one("#status-bar", Static).update(
            f"Workspace  {self.workspace}    Model  {model_name}    Run  {result.run_id[:8]}    "
            f"Status  {result.status.upper()}    Duration  {result.duration_seconds:.2f}s"
        )
        self._set_busy(False)
        self.query_one("#prompt", TextArea).focus()
        self.query_one("#transcript", VerticalScroll).scroll_end(animate=False)

    def _approve(self, name: str, arguments: dict, decision) -> bool:
        resolved = threading.Event()
        choice = {"value": "deny"}

        def show_dialog() -> None:
            def receive(value: str | None) -> None:
                choice["value"] = value or "deny"
                resolved.set()

            self.push_screen(ApprovalScreen(name, arguments, decision, self.workspace), receive)

        self.call_from_thread(show_dialog)
        resolved.wait()
        if choice["value"] == "allow-all" and self.current_runtime:
            self.current_runtime.tools.approve_for_run(RiskLevel.WRITE)
        return choice["value"] in {"allow-once", "allow-all"}

    def _output(self, renderable) -> None:
        self.query_one("#transcript", VerticalScroll).mount(
            Static(renderable, classes="command-output")
        )
        self.query_one("#transcript", VerticalScroll).scroll_end(animate=False)

    def _error(self, message: str) -> None:
        self._output(f"ERROR · {message}")

    def handle_command(self, command: str) -> None:
        parts = command.strip().split(maxsplit=1)
        name = parts[0].lower()
        argument = parts[1] if len(parts) > 1 else ""
        if name not in SUPPORTED_COMMANDS:
            self._error(f"Unknown command: {name}")
            return
        if name == "/help":
            self._output(" · ".join(sorted(SUPPORTED_COMMANDS)))
        elif name == "/runs":
            self._output(runs_table(self.workspace))
        elif name in {"/inspect", "/replay"}:
            table = events_table(self.workspace, argument, replay=name == "/replay")
            self._output(table) if table else self._error(f"Unknown run: {argument}")
        elif name == "/plans":
            table = Table("Plan ID", "Status", "Request", "Planning Run", "Implementation Run")
            for plan in self.plan_store.list_all():
                table.add_row(
                    plan.get("plan_id", ""), plan.get("status", ""), plan.get("original_request", "")[:60],
                    plan.get("planning_run_id", "")[:8], (plan.get("implementation_run_id") or "")[:8],
                )
            self._output(table)
        elif name == "/show-plan":
            try:
                plan = self.plan_store.load(argument)
            except ValueError as exc:
                self._error(str(exc))
            else:
                self.query_one("#transcript", VerticalScroll).mount(
                    Markdown(plan["plan"], classes="command-output")
                )
        elif name == "/plan":
            if not argument:
                self._error("Usage: /plan REQUIREMENT")
            else:
                self._set_busy(True)
                self._execute_plan(argument)
        elif name == "/implement":
            self._start_implementation(argument)
        elif name == "/team":
            self._show_team()
        elif name == "/messages":
            bus = MessageBus(self.workspace / ".team" / "team.db", self.config.team_delivery_timeout_seconds)
            try:
                self._output(messages_table(bus, argument or None))
            except ValueError as exc:
                self._error(str(exc))
        elif name == "/retry-message":
            bus = MessageBus(self.workspace / ".team" / "team.db", self.config.team_delivery_timeout_seconds)
            if not argument:
                self._error("Usage: /retry-message MESSAGE_ID")
            else:
                self._output("Message queued for redelivery." if bus.retry(argument) else "Message not found or already acknowledged.")
        elif name == "/diff":
            views = list(self.query(RunResultView))
            if not views:
                self._error("No run yet.")
            else:
                views[-1].query_one("#agent-changes", Collapsible).collapsed = False
                views[-1].scroll_visible()
        elif name == "/test":
            self._set_busy(True)
            self._execute_quality_gates()
        elif name == "/abort":
            self.action_abort_run()

    def _show_team(self) -> None:
        if self.current_runtime and self.current_runtime.team:
            summary = self.current_runtime.team.list_all()
        else:
            config_path = self.workspace / ".team" / "config.json"
            try:
                team_config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {"team_name": "default", "members": []}
            except json.JSONDecodeError:
                team_config = {"team_name": "default", "members": []}
            summary = "\n".join(
                [f"Team: {team_config.get('team_name', 'default')}"]
                + [f"- {member.get('name')} ({member.get('role')}): {member.get('status')} task={member.get('current_task')} scope={member.get('write_scope', [])}"
                   for member in team_config.get("members", [])]
            )
        self._output(summary + "\n\n" + TaskManager(self.workspace / ".tasks").list_all())

    @work(thread=True, exclusive=True, group="agent-run")
    def _execute_plan(self, request: str) -> None:
        runtime = AgentRuntime(
            self.workspace, self.config, self.model, self._approve,
            interactive=False, enable_team=False, mode=RunMode.PLAN,
            event_callback=self._post_event,
        )
        self.call_from_thread(self._runtime_started, runtime)
        result = runtime.run(request)
        if result.status == "planned" and result.answer.strip():
            selected_files = [
                event.get("payload", {}).get("path") for event in runtime.events.read_events()
                if event.get("type") == "context_selected" and event.get("payload", {}).get("path")
            ]
            try:
                plan = self.plan_store.create(request, result.answer, result.run_id, selected_files)
                runtime.events.emit("plan_created", "lead", {
                    "plan_id": plan["plan_id"], "selected_files": plan["selected_files"],
                    "workspace_fingerprint": plan["workspace_fingerprint"], "git_head": plan["git_head"],
                })
                result.answer += f"\n\nPlan ID: `{plan['plan_id']}` · execute with `/implement {plan['plan_id']}`"
            except ValueError as exc:
                result = RunResult(result.run_id, "failed", str(exc), "", "", result.duration_seconds)
        self.call_from_thread(self._finish_run, result)

    def _start_implementation(self, plan_id: str) -> None:
        if not plan_id:
            self._error("Usage: /implement PLAN_ID")
            return
        try:
            plan = self.plan_store.begin(plan_id)
        except ValueError as exc:
            self._error(str(exc))
            return
        if plan["status"] == "stale":
            EventStore(self.workspace, plan["planning_run_id"]).emit(
                "plan_stale", "lead", {"plan_id": plan_id, "reason": "workspace or Git HEAD changed"},
            )
            self._error(f"Plan {plan_id} is stale. Generate a new plan with /plan.")
            return
        self._set_busy(True)
        self._execute_implementation(plan)

    @work(thread=True, exclusive=True, group="agent-run")
    def _execute_implementation(self, plan: dict) -> None:
        prompt = (
            "Implement the approved plan below. Recheck every assumption against the current repository, "
            "then modify files, run quality gates, and report truthfully.\n\n"
            f"ORIGINAL REQUEST:\n{plan['original_request']}\n\nAPPROVED PLAN:\n{plan['plan']}"
        )
        runtime = AgentRuntime(
            self.workspace, self.config, self.model, self._approve,
            mode=RunMode.ACT, event_callback=self._post_event,
        )
        self.call_from_thread(self._runtime_started, runtime)
        runtime.events.emit("plan_implementation_started", "lead", {
            "plan_id": plan["plan_id"], "planning_run_id": plan["planning_run_id"],
        })
        result = runtime.run(prompt)
        status = "completed" if result.status == "completed" else "failed"
        self.plan_store.finish(plan["plan_id"], status, result.run_id)
        runtime.events.emit(
            "plan_implementation_completed" if status == "completed" else "plan_implementation_failed",
            "lead", {"plan_id": plan["plan_id"], "planning_run_id": plan["planning_run_id"], "status": result.status},
        )
        self.call_from_thread(self._finish_run, result)

    @work(thread=True, exclusive=True, group="agent-run")
    def _execute_quality_gates(self) -> None:
        runtime = self.current_runtime or AgentRuntime(
            self.workspace, self.config, self.model, self._approve,
            event_callback=self._post_event,
        )
        self.call_from_thread(self._runtime_started, runtime)
        started = time.monotonic()
        passed, validation = runtime.tools.run_quality_gates()
        result = RunResult(
            runtime.events.run_id, "completed" if passed else "failed",
            "Quality gates completed.", runtime.tools.diff(), validation, time.monotonic() - started,
        )
        self.call_from_thread(self._finish_run, result)

    def action_abort_run(self) -> None:
        if self.current_runtime and self.busy:
            self.current_runtime.abort()
            self.notify("Abort requested; trajectory is preserved.", severity="warning")

    def action_expand_all(self) -> None:
        for view in self.query(RunResultView):
            view.expand_all()

    def action_collapse_all(self) -> None:
        for view in self.query(RunResultView):
            view.collapse_all()

    def on_unmount(self) -> None:
        if self.current_runtime and self.busy:
            self.current_runtime.abort()


def run_tui() -> None:
    AgentTUI().run()
