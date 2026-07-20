import asyncio

from textual.app import App, ComposeResult
from textual.widgets import Collapsible

from coding_agent.runtime import RunResult
from coding_agent.tui import RunResultView, diff_stats, quality_gate_status


class ResultApp(App):
    def compose(self) -> ComposeResult:
        yield RunResultView(
            RunResult(
                run_id="run-1",
                status="completed",
                answer="Implemented safely.",
                diff="diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -1 +1,2 @@\n-old\n+new\n+line\n",
                validation="$ pytest\n2 passed",
                duration_seconds=1.25,
            ),
            quality_commands=1,
        )


def test_diff_stats_counts_files_and_changed_lines():
    stats = diff_stats(
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n-old\n+new\n"
        "diff --git a/b.py b/b.py\n--- a/b.py\n+++ b/b.py\n+added\n"
    )
    assert stats == {"files": 2, "additions": 2, "deletions": 1}


def test_quality_gate_status_distinguishes_pass_fail_and_unconfigured():
    assert quality_gate_status("2 passed", 1, "completed") == "PASS"
    assert quality_gate_status("1 failed", 1, "failed") == "FAIL"
    assert quality_gate_status("", 0, "completed") == "NOT CONFIGURED"


def test_result_view_starts_with_diff_and_quality_collapsed():
    async def exercise():
        async with ResultApp().run_test() as pilot:
            await pilot.pause()
            quality = pilot.app.query_one("#quality-gates", Collapsible)
            changes = pilot.app.query_one("#agent-changes", Collapsible)
            assert quality.collapsed is True
            assert changes.collapsed is True
            assert "PASS" in str(quality.title)
            assert "1 file" in str(changes.title)

    asyncio.run(exercise())


def test_result_view_expand_and_collapse_actions_toggle_both_panels():
    async def exercise():
        async with ResultApp().run_test() as pilot:
            await pilot.pause()
            result_view = pilot.app.query_one(RunResultView)
            result_view.expand_all()
            await pilot.pause()
            assert all(not item.collapsed for item in pilot.app.query(Collapsible))
            result_view.collapse_all()
            await pilot.pause()
            assert all(item.collapsed for item in pilot.app.query(Collapsible))

    asyncio.run(exercise())
