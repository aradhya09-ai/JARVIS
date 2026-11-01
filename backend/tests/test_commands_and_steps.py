"""Regressions for the command grammar and for multi-step execution.

Covers the phrases that used to collapse into a single tool call ('open Chrome
and search YouTube for X', 'open my latest screenshot'), the executor rules that
keep a partly-failed sequence honest, and the spoken reminders and knowledge
commands that have to reach the local store rather than the model.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from ai.model_router import ModelUnavailable
from ai.planner import Planner
from config import Settings
from database.db import Database
from database.repository import Repository
from events import EventBus
from executor import TaskExecutor
from security.audit_log import AuditLog
from security.permissions import PermissionPolicy
from tools.base import Risk, ToolDefinition, ToolRegistry, ToolResult


class OfflineRouter:
    async def complete(self, messages, json_mode=False):  # noqa: ANN001, ARG002
        raise ModelUnavailable("tests: model disabled")

    def active_name(self) -> str:
        return "none"


@pytest.fixture(scope="module")
def services():
    import main
    return main._build_services()


@pytest.fixture()
def planner(services):
    return Planner(services.settings, services.repo, services.registry, OfflineRouter())


@pytest.mark.parametrize("text,tool,args_subset", [
    ("open Chrome and search for Python recursion", "browser.search",
     {"query": "Python recursion", "browser": "chrome", "engine": "google"}),
    ("open Edge and search YouTube for machine learning basics", "browser.search",
     {"query": "machine learning basics", "browser": "edge", "engine": "youtube"}),
    ("search YouTube for recursion tutorial", "browser.search",
     {"query": "recursion tutorial", "engine": "youtube"}),
    ("google DBMS normalization in edge", "browser.search",
     {"query": "DBMS normalization", "browser": "edge"}),
    ("open Instagram in Edge", "browser.open_url", {"browser": "edge"}),
    ("open the Visual Studio Code shortcut", "shortcuts.open", {"name": "Visual Studio Code"}),
    ("open Downloads", "files.open_folder", {"folder": "downloads"}),
    ("open my Desktop", "files.open_folder", {"folder": "desktop"}),
    ("open my latest screenshot", "files.open_recent", {"kind": "screenshot"}),
    ("where did you save it", "files.locate_last", {}),
    ("create a folder named GATE Preparation", "files.create_folder",
     {"name": "GATE Preparation"}),
])
def test_phrases_map_to_the_right_tool(planner, text, tool, args_subset):
    plan = asyncio.run(planner.plan(text))
    assert plan.tool == tool, f"{text} became {plan.tool} {plan.args}"
    for key, value in args_subset.items():
        assert plan.args.get(key) == value


def test_screenshot_then_open_is_a_sequence(planner):
    plan = asyncio.run(planner.plan("take a screenshot and open it"))
    assert plan.tool == "system.screenshot"
    assert [step["tool"] for step in plan.steps] == ["files.open_recent"]
    assert plan.sequence()[1]["args"]["kind"] == "screenshot"


def test_create_and_open_is_a_sequence(planner):
    plan = asyncio.run(planner.plan("create a folder called Xentrai in Documents and open it"))
    assert plan.args["name"] == "Xentrai"
    assert [step["tool"] for step in plan.steps] == ["files.open_folder"]


def test_plain_open_stays_a_single_action(planner):
    plan = asyncio.run(planner.plan("open VS Code"))
    assert plan.tool == "app.open"
    assert plan.steps == []


class StubArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = ""


@pytest.fixture()
def executor_env(tmp_path):
    """An executor with only probe tools: no Windows call can happen in tests."""
    settings = Settings(data_dir=tmp_path, output_dir=tmp_path / "out",
                        project_root=tmp_path / "projects")
    database = Database(settings.db_path)
    database.initialize()
    repo = Repository(database)
    repo.seed()
    registry = ToolRegistry()
    calls: list[str] = []

    async def works(args: StubArgs, ctx) -> ToolResult:
        calls.append(f"ok:{args.label}")
        return ToolResult.success(f"First step done ({args.label})", value=1)

    async def fails(args: StubArgs, ctx) -> ToolResult:
        calls.append(f"bad:{args.label}")
        return ToolResult.failure("The second step could not run")

    async def risky(args: StubArgs, ctx) -> ToolResult:
        calls.append(f"risky:{args.label}")
        return ToolResult.success("Second step done")

    registry.register(ToolDefinition(
        name="probe.ok", description="t", args_model=StubArgs, risk=Risk.LOW,
        handler=works, category="probe"))
    registry.register(ToolDefinition(
        name="probe.fails", description="t", args_model=StubArgs, risk=Risk.LOW,
        handler=fails, category="probe"))
    registry.register(ToolDefinition(
        name="probe.risky", description="t", args_model=StubArgs, risk=Risk.HIGH,
        handler=risky, confirm=True, category="probe"))
    bus = EventBus()
    audit = AuditLog(repo, settings.audit_log_path)
    policy = PermissionPolicy(repo, settings.read_roots, settings.write_roots)
    executor = TaskExecutor(settings, repo, registry, bus, policy, audit)
    return executor, repo, calls


async def run_task(executor, repo, plan, text):
    """Submit and drive the task to a terminal status on one loop.

    The executor starts the run with asyncio.create_task, so the loop has to
    stay alive after submit() returns or the work never happens.
    """
    out = await executor.submit(text, plan)
    if out.get("task") is None:
        return out, None
    task_id = out["task"]["id"]
    for _ in range(200):
        task = repo.get_task(task_id)
        if task and task["status"] not in {"pending", "running"}:
            return out, task
        await asyncio.sleep(0.02)
    raise AssertionError("task never finished")


def test_sequence_stops_at_the_first_failure(executor_env):
    executor, repo, calls = executor_env
    plan = executor_plan("probe.ok", "a", [("probe.fails", "b")])
    out, task = asyncio.run(run_task(executor, repo, plan, "run the sequence"))
    assert task["status"] == "failed"
    assert task["error"] == "The second step could not run"
    assert calls == ["ok:a", "bad:b"]
    assert task["result"]["steps_completed"] == 2
    assert task["result"]["steps_planned"] == 2
    assert task["steps"][1]["tool"] == "probe.fails"
    reply = [m["content"] for m in repo.list_messages() if m["task_id"] == task["id"]][-1]
    assert "First step done" in reply and "could not run" in reply
    assert reply.count("could not run") == 1, "the failure must not be read out twice"


def test_confirmation_covers_every_step(executor_env):
    executor, repo, calls = executor_env
    plan = executor_plan("probe.ok", "x", [("probe.risky", "y")])

    async def flow():
        out = await executor.submit("risky sequence", plan)
        assert out["requires_confirmation"] is True
        assert out["task"]["risk"] == "high"
        assert calls == [], "nothing may run before the user approves"
        summary = out["confirmation"]["summary"]
        assert "1)" in summary and "2)" in summary
        assert executor.confirm(out["task"]["id"], out["confirmation"]["token"])["task"]
        task_id = out["task"]["id"]
        for _ in range(200):
            task = repo.get_task(task_id)
            if task and task["status"] not in {"pending", "running", "awaiting_confirmation"}:
                return task
            await asyncio.sleep(0.02)
        raise AssertionError("confirmed task never finished")

    task = asyncio.run(flow())
    assert task["status"] == "completed"
    assert calls == ["ok:x", "risky:y"]


def test_an_invalid_later_step_is_rejected_before_the_first_runs(executor_env):
    executor, repo, calls = executor_env
    plan = executor_plan("probe.ok", "z", [("probe.fails", {"bogus": 1})])
    out, task = asyncio.run(run_task(executor, repo, plan, "bad step two"))
    assert out["type"] == "reply"
    assert "Invalid arguments for probe.fails" in out["reply"]
    assert task is None
    assert calls == []


def test_unknown_tool_in_a_sequence_is_refused(executor_env):
    executor, repo, calls = executor_env
    plan = executor_plan("probe.ok", "z", [("shell.run", {})])
    out, task = asyncio.run(run_task(executor, repo, plan, "sneaky"))
    assert out["type"] == "reply"
    assert "shell.run" in out["reply"]
    assert task is None
    assert calls == []


def test_single_step_task_result_is_unchanged(executor_env):
    executor, repo, calls = executor_env
    plan = executor_plan("probe.ok", "solo")
    out, task = asyncio.run(run_task(executor, repo, plan, "one step"))
    assert task["status"] == "completed"
    assert task["steps"] is None
    assert task["result"] == {"value": 1}
    reply = [m["content"] for m in repo.list_messages() if m["task_id"] == task["id"]][-1]
    assert reply == "First step done (solo)"


def executor_plan(tool, args, steps=None):
    from ai.planner import Plan
    return Plan(kind="tool", tool=tool, args={"label": args},
                steps=[{"tool": name, "args": {"label": payload} if isinstance(payload, str)
                        else payload} for name, payload in (steps or [])],
                intent="probe", planner="rules")


@pytest.mark.parametrize("text,tool,args_subset", [
    ("remind me to revise normalization at 9 pm", "reminders.set",
     {"text": "revise normalization"}),
    ("remind me in 20 minutes to stretch", "reminders.set",
     {"text": "stretch", "minutes": 20}),
    ("mujhe 10 minute baad yaad dilana ki paani peena hai", "reminders.set",
     {"text": "paani peena hai", "minutes": 10}),
    ("what are my reminders", "reminders.list", {}),
    ("cancel the normalization reminder", "reminders.cancel", {"fragment": "normalization"}),
    ("what do you know about me", "knowledge.list", {}),
])
def test_personal_phrases_reach_the_personal_tools(planner, text, tool, args_subset):
    plan = asyncio.run(planner.plan(text))
    assert plan.tool == tool, f"{text} became {plan.tool} {plan.args}"
    for key, value in args_subset.items():
        assert plan.args.get(key) == value


def test_reminder_without_a_time_is_asked_for(planner):
    plan = asyncio.run(planner.plan("remind me to call Ankit"))
    assert plan.kind == "reply" and "needs a time" in plan.reply


def test_spoken_reminder_lands_in_the_reminder_store(services, planner):
    plan = asyncio.run(planner.plan("remind me in 20 minutes to stretch"))
    _, task = asyncio.run(run_task(services.executor, services.repo, plan, "reminder"))
    assert task["status"] == "completed", task["error"]
    stored = [row for row in services.repo.list_reminders() if row["text"] == "stretch"]
    assert stored and not stored[0]["fired"]


def test_a_reminder_missed_while_closed_is_delivered_as_late(services):
    from datetime import datetime, timedelta

    from scheduler import ReminderScheduler

    repo = services.repo
    missed = repo.add_reminder(
        "check the oven",
        (datetime.now() - timedelta(minutes=30)).isoformat(timespec="seconds"),
        speak=False, source="test",
    )
    scheduler = ReminderScheduler(repo, services.bus)
    fired = scheduler._sweep(startup=True)

    assert [entry["id"] for entry in fired] == [missed["id"]]
    assert fired[0]["late"] is True and fired[0]["late_minutes"] >= 29
    assert repo.get_reminder(missed["id"])["fired"] is True
    titles = [note["title"] for note in repo.list_notifications(30)]
    assert "Reminder (late)" in titles, "a late delivery must say so"
    # Sweeping again must not read the same reminder out twice.
    assert scheduler._sweep() == []


# Every card on the dashboard's Quick Actions grid must be a real command: a
# button that falls through to a freeform model reply is decoration, not an action.
QUICK_ACTIONS = [
    ("Open VS Code", "app.open"),
    ("Open PowerPoint", "app.open"),
    ("Open File Explorer", "app.open"),
    ("Open Chrome", "app.open"),
    ("Take a screenshot", "system.screenshot"),
    ("Lock my computer", "system.lock"),
    ("Open my Downloads folder", "files.open_folder"),
    ("Open my Documents folder", "files.open_folder"),
    ("What is my CPU and memory usage", "system.stats"),
]


@pytest.mark.parametrize("text,tool", QUICK_ACTIONS)
def test_quick_action_commands_are_routed_by_rules(planner, text, tool):
    plan = asyncio.run(planner.plan(text))
    assert plan.planner == "rules", f"{text} fell through to the model"
    assert plan.tool == tool, f"{text} became {plan.tool} {plan.args}"


def test_quick_action_grid_is_fully_wired():
    """The desktop list and this test must not drift apart."""
    source = Path(__file__).resolve().parents[2] / "desktop/src/components/QuickActions.tsx"
    if not source.exists():
        pytest.skip("desktop sources are not next to the backend in this checkout")
    commands = re.findall(r"command: '([^']+)'", source.read_text(encoding="utf-8"))
    assert len(commands) == 9
    assert commands == [text for text, _ in QUICK_ACTIONS]
