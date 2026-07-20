import asyncio
import time
from pathlib import Path

from textual.app import App, ComposeResult
from textual.widgets import Collapsible, Static, TextArea

from coding_agent.policy import PolicyDecision, RiskLevel
from coding_agent.runtime import RunResult
from coding_agent.tui import (
    SUPPORTED_COMMANDS,
    AgentTUI,
    ApprovalScreen,
    RunResultView,
    diff_stats,
    quality_gate_status,
)


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


class TwoResultsApp(App):
    def compose(self) -> ComposeResult:
        for run_id in ("run-1", "run-2"):
            yield RunResultView(
                RunResult(run_id, "completed", "Done", "", "", 0.1),
                quality_commands=0,
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


def test_multiple_result_cards_can_coexist():
    async def exercise():
        async with TwoResultsApp().run_test() as pilot:
            await pilot.pause()
            assert len(pilot.app.query(RunResultView)) == 2
            assert len(pilot.app.query(Collapsible)) == 4

    asyncio.run(exercise())


class RecordingModel:
    model = "fake-qwen"

    def __init__(self):
        self.prompts = []

    def create(self, **kwargs):
        self.prompts.append(kwargs["messages"][0]["content"])
        return {"stop_reason": "end_turn", "content": [{"type": "text", "text": "Finished"}]}


def test_tui_submits_multiline_prompt_once_and_renders_result(tmp_path):
    async def exercise():
        model = RecordingModel()
        app = AgentTUI(workspace=tmp_path, model_client=model)
        async with app.run_test() as pilot:
            editor = app.query_one("#prompt", TextArea)
            editor.text = "first line\nsecond line"
            await pilot.press("ctrl+enter")
            for _ in range(20):
                await pilot.pause(0.05)
                if len(app.query(RunResultView)) == 1:
                    break
            assert model.prompts == ["first line\nsecond line"]
            assert len(app.query(RunResultView)) == 1
            assert editor.text == ""

    asyncio.run(exercise())


def test_tui_recognizes_every_classic_slash_command():
    assert SUPPORTED_COMMANDS == {
        "/plan", "/plans", "/show-plan", "/implement", "/runs", "/inspect", "/replay",
        "/team", "/messages", "/retry-message", "/diff", "/test", "/abort", "/help",
    }


def test_slash_command_is_rendered_without_calling_model(tmp_path):
    async def exercise():
        model = RecordingModel()
        app = AgentTUI(workspace=tmp_path, model_client=model)
        async with app.run_test() as pilot:
            editor = app.query_one("#prompt", TextArea)
            editor.text = "/runs"
            app.action_submit_prompt()
            await pilot.pause()
            assert model.prompts == []
            assert len(app.query(".command-output")) == 1

    asyncio.run(exercise())


class ApprovalModel:
    model = "fake-qwen"

    def __init__(self):
        self.call = 0

    def create(self, **_kwargs):
        self.call += 1
        if self.call == 1:
            return {
                "stop_reason": "tool_use",
                "content": [{
                    "type": "tool_use", "id": "write-1", "name": "write_file",
                    "input": {"path": "denied.txt", "content": "no"},
                }],
            }
        return {"stop_reason": "end_turn", "content": [{"type": "text", "text": "Denied safely"}]}


def test_tui_approval_modal_denies_worker_write(tmp_path):
    async def exercise():
        app = AgentTUI(workspace=tmp_path, model_client=ApprovalModel())
        async with app.run_test() as pilot:
            app.query_one("#prompt", TextArea).text = "Try a write"
            app.action_submit_prompt()
            for _ in range(30):
                await pilot.pause(0.05)
                if isinstance(app.screen, ApprovalScreen):
                    break
            assert isinstance(app.screen, ApprovalScreen)
            await pilot.click("#deny")
            for _ in range(30):
                await pilot.pause(0.05)
                if len(app.query(RunResultView)) == 1:
                    break
            assert not (tmp_path / "denied.txt").exists()
            assert len(app.query(RunResultView)) == 1

    asyncio.run(exercise())


def test_approval_screen_exposes_risk_and_three_choices():
    screen = ApprovalScreen(
        "write_file", {"path": "app.py"},
        PolicyDecision(RiskLevel.WRITE, "modifies a workspace file"),
        Path("C:/workspace"),
    )
    assert screen.tool_name == "write_file"
    assert screen.decision.risk is RiskLevel.WRITE


def test_approval_screen_returns_each_button_choice(tmp_path):
    async def exercise(button_id: str):
        results = []

        class ApprovalHost(App):
            def on_mount(self):
                self.push_screen(
                    ApprovalScreen(
                        "write_file", {"path": "app.py"},
                        PolicyDecision(RiskLevel.WRITE, "workspace write"), tmp_path,
                    ),
                    lambda value: results.append(value),
                )

        async with ApprovalHost().run_test() as pilot:
            await pilot.pause()
            await pilot.click(f"#{button_id}")
            await pilot.pause()
            assert results == [button_id]

    for button_id in ("deny", "allow-once", "allow-all"):
        asyncio.run(exercise(button_id))


PLAN_TEXT = "\n".join([
    "## 目标与验收标准", "## 仓库现状", "## 实施步骤",
    "## 预计修改文件及原因", "## 测试方案", "## 风险与假设",
])


class PlanModel:
    model = "fake-qwen"

    def create(self, **_kwargs):
        return {"stop_reason": "end_turn", "content": [{"type": "text", "text": PLAN_TEXT}]}


def test_tui_plan_command_persists_ready_plan(tmp_path):
    async def exercise():
        app = AgentTUI(workspace=tmp_path, model_client=PlanModel())
        async with app.run_test() as pilot:
            app.query_one("#prompt", TextArea).text = "/plan improve calculator"
            app.action_submit_prompt()
            for _ in range(30):
                await pilot.pause(0.05)
                if app.plan_store.list_all():
                    break
            plans = app.plan_store.list_all()
            assert len(plans) == 1
            assert plans[0]["status"] == "ready"
            assert plans[0]["original_request"] == "improve calculator"

    asyncio.run(exercise())


def test_running_status_bar_includes_elapsed_time(tmp_path):
    async def exercise():
        app = AgentTUI(workspace=tmp_path, model_client=RecordingModel())
        async with app.run_test() as pilot:
            app.busy = True
            app.run_started_at = time.monotonic() - 1.0
            app._refresh_status()
            await pilot.pause()
            assert "Elapsed" in str(app.query_one("#status-bar", Static).render())

    asyncio.run(exercise())
