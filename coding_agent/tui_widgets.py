from __future__ import annotations

import json
from pathlib import Path

from rich.syntax import Syntax
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Collapsible, Markdown, Static

from .policy import PolicyDecision
from .runtime import RunResult


def diff_stats(diff: str) -> dict[str, int]:
    files = sum(line.startswith("diff --git ") for line in diff.splitlines())
    additions = sum(line.startswith("+") and not line.startswith("+++") for line in diff.splitlines())
    deletions = sum(line.startswith("-") and not line.startswith("---") for line in diff.splitlines())
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
    RunResultView { height: auto; margin: 1 0; padding: 0 1; border: round $surface-lighten-2; }
    RunResultView .result-heading { color: $text-muted; margin-top: 1; }
    RunResultView Markdown { height: auto; }
    RunResultView Collapsible { margin-top: 1; }
    """

    def __init__(self, result: RunResult, quality_commands: int = 0, **kwargs):
        super().__init__(**kwargs)
        self.result = result
        self.quality_commands = quality_commands

    def compose(self) -> ComposeResult:
        stats = diff_stats(self.result.diff)
        gate_status = quality_gate_status(
            self.result.validation, self.quality_commands, self.result.status,
        )
        yield Static(
            f"{self.result.status.upper()}  ·  {self.result.run_id}  ·  {self.result.duration_seconds:.2f}s",
            classes="result-heading",
        )
        yield Markdown(self.result.answer or "_(no final answer)_")
        with Collapsible(
            title=f"Quality Gates · {gate_status} · {_plural(self.quality_commands, 'command')}",
            collapsed=True, id="quality-gates",
        ):
            yield Static(self.result.validation or "No quality gate commands configured.")
        with Collapsible(
            title=(f"Agent Changes · {_plural(stats['files'], 'file')} · "
                   f"+{stats['additions']} / -{stats['deletions']}"),
            collapsed=True, id="agent-changes",
        ):
            if self.result.diff:
                yield Static(Syntax(self.result.diff, "diff", theme="ansi_dark", word_wrap=True))
            else:
                yield Static("No code changes in this run.")

    def expand_all(self) -> None:
        for item in self.query(Collapsible):
            item.collapsed = False

    def collapse_all(self) -> None:
        for item in self.query(Collapsible):
            item.collapsed = True


class ApprovalScreen(ModalScreen[str]):
    """Application-level approval dialog returned to a blocked worker."""

    DEFAULT_CSS = """
    ApprovalScreen { align: center middle; background: $background 60%; }
    ApprovalScreen > Vertical { width: 80%; max-width: 100; height: auto; padding: 1 2; border: thick $warning; background: $surface; }
    ApprovalScreen #approval-details { height: auto; max-height: 20; margin: 1 0; }
    ApprovalScreen Horizontal { height: 3; align-horizontal: right; }
    ApprovalScreen Button { margin-left: 1; }
    """

    def __init__(self, tool_name: str, arguments: dict, decision: PolicyDecision, workspace: Path):
        super().__init__()
        self.tool_name = tool_name
        self.arguments = arguments
        self.decision = decision
        self.workspace = workspace

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("APPLICATION-LEVEL APPROVAL", classes="result-heading")
            yield Static(
                f"Tool: {self.tool_name}\nRisk: {self.decision.risk.value}\nReason: {self.decision.reason}\n"
                f"Workspace: {self.workspace}\nArguments:\n{json.dumps(self.arguments, ensure_ascii=False, indent=2)}",
                id="approval-details",
            )
            with Horizontal():
                yield Button("Deny", id="deny", variant="error")
                yield Button("Allow once", id="allow-once", variant="warning")
                yield Button("Allow all writes", id="allow-all", variant="success")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id or "deny")

    def on_mount(self) -> None:
        self.query_one("#deny", Button).focus()
