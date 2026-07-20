from __future__ import annotations

from rich.syntax import Syntax
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Collapsible, Markdown, Static

from .runtime import RunResult


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
