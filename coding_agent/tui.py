from __future__ import annotations

import json
import os
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv
from rich.syntax import Syntax
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import Button, Collapsible, Footer, Markdown, RichLog, Static, TextArea

from .config import AgentConfig
from .events import AgentEvent
from .runtime import AgentRuntime, AnthropicModel, ModelClient, RunResult


def diff_stats(diff: str) -> dict[str, int]:
    """Return display-oriented unified diff counts."""
    files = sum(line.startswith("diff --git ") for line in diff.splitlines())
    additions = sum(
        line.startswith("+") and not line.startswith("+++")
        for line in diff.splitlines()
    )
    deletions = sum(
        line.startswith("-") and not line.startswith("---")
        for line in diff.splitlines()
    )
    return {"files": files, "additions": additions, "deletions": deletions}


def quality_gate_status(validation: str, command_count: int, run_status: str) -> str:
    if command_count == 0:
        return "NOT CONFIGURED"
    if run_status == "completed" and "failed" not in validation.lower():
        return "PASS"
    return "FAIL"


def _plural(count: int, singular: str) -> str:
    return f"{count} {singular}{'' if count == 1 else 's'}"


class RunResultView(Vertical):
    """One immutable run result with details hidden until requested."""

    DEFAULT_CSS = """
    RunResultView {
        height: auto;
        margin: 1 0;
        padding: 0 1;
        border: round $surface-lighten-2;
    }
    RunResultView .result-heading {
        color: $text-muted;
        margin-top: 1;
    }
    RunResultView Markdown {
        height: auto;
        max-height: 24;
    }
    RunResultView Collapsible {
        margin-top: 1;
    }
    """

    def __init__(self, result: RunResult, quality_commands: int = 0, **kwargs):
        super().__init__(**kwargs)
        self.result = result
        self.quality_commands = quality_commands

    def compose(self) -> ComposeResult:
        result = self.result
        stats = diff_stats(result.diff)
        gate_status = quality_gate_status(result.validation, self.quality_commands, result.status)
        yield Static(
            f"{result.status.upper()}  ·  {result.run_id}  ·  {result.duration_seconds:.2f}s",
            classes="result-heading",
        )
        yield Markdown(result.answer or "_(no final answer)_")
        with Collapsible(
            title=f"Quality Gates · {gate_status} · {_plural(self.quality_commands, 'command')}",
            collapsed=True,
            id="quality-gates",
        ):
            yield Static(result.validation or "No quality gate commands configured.")
        with Collapsible(
            title=(
                f"Agent Changes · {_plural(stats['files'], 'file')} · "
                f"+{stats['additions']} / -{stats['deletions']}"
            ),
            collapsed=True,
            id="agent-changes",
        ):
            if result.diff:
                yield Static(Syntax(result.diff, "diff", theme="ansi_dark", word_wrap=True))
            else:
                yield Static("No code changes in this run.")

    def expand_all(self) -> None:
        for item in self.query(Collapsible):
            item.collapsed = False

    def collapse_all(self) -> None:
        for item in self.query(Collapsible):
            item.collapsed = True


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
        self.current_runtime: AgentRuntime | None = None
        self.last_result: RunResult | None = None
        self.busy = False

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
        model_name = getattr(self.model, "model", "custom")
        self.query_one("#status-bar", Static).update(
            f"Workspace  {self.workspace}    Model  {model_name}    Run  {runtime.events.run_id[:8]}    Status  RUNNING"
        )

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
            f"Workspace  {self.workspace}    Model  {model_name}    Run  {result.run_id[:8]}    Status  {result.status.upper()}"
        )
        self._set_busy(False)
        self.query_one("#prompt", TextArea).focus()
        self.query_one("#transcript", VerticalScroll).scroll_end(animate=False)

    def _approve(self, name: str, arguments: dict, decision) -> bool:
        # The modal approval flow is added in the next increment. Safe default for
        # TUI writes is denial; allow_write projects never call this callback.
        self.post_message(LiveAgentEvent(AgentEvent(
            "approval", self.current_runtime.events.run_id if self.current_runtime else "",
            0, "approval_requested", "lead",
            {"tool": name, "risk": decision.risk.value, "arguments": json.dumps(arguments, ensure_ascii=False)},
        )))
        return False

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
