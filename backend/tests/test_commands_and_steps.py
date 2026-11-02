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


@pytest.mark.parametrize("text,tools", [
    ("open Chrome, set volume to 40 percent and take a screenshot",
     ["app.open", "system.volume", "system.screenshot"]),
    ("mute the sound and take a screenshot", ["system.volume", "system.screenshot"]),
    ("set volume to 40 percent then open Notepad", ["system.volume", "app.open"]),
    ("turn up the brightness and open VS Code and take a screenshot",
     ["system.brightness", "app.open", "system.screenshot"]),
    ("open chrome and close spotify", ["app.open", "app.close"]),
])
def test_every_clause_of_a_compound_command_is_planned(planner, text, tools):
    """The old behaviour: the first rule matched, the rest was dropped in silence."""
    plan = asyncio.run(planner.plan(text))
    assert [step["tool"] for step in plan.sequence()] == tools
    assert plan.intent == "compound"


@pytest.mark.parametrize("text", [
    "open Chrome and search for Python recursion",
    "search YouTube for recursion and sorting tutorials",
])
def test_a_phrase_one_rule_already_fuses_is_not_split(planner, text):
    """Splitting these would launch the browser twice for one request."""
    plan = asyncio.run(planner.plan(text))
    assert plan.tool == "browser.search"
    assert plan.steps == []


@pytest.mark.parametrize("text,tools", [
    ("take a screenshot and open it", ["system.screenshot", "files.open_recent"]),
    ("create a folder called Xentrai in Documents and open it",
     ["files.create_folder", "files.open_folder"]),
])
def test_a_follow_up_that_only_says_it_keeps_the_chained_rule(planner, text, tools):
    plan = asyncio.run(planner.plan(text))
    assert [step["tool"] for step in plan.sequence()] == tools


@pytest.mark.parametrize("text", [
    "what is the time and date",
    "open VS Code",
    "organize Downloads",
])
def test_a_clause_that_is_not_an_action_never_forces_a_split(planner, text):
    plan = asyncio.run(planner.plan(text))
    assert plan.intent != "compound"


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


def test_multi_step_reply_is_only_the_step_reports(executor_env):
    """Spoken, "all 2 steps done" after two reports is words over words."""
    executor, repo, calls = executor_env
    plan = executor_plan("probe.ok", "one", [("probe.ok", "two")])
    out, task = asyncio.run(run_task(executor, repo, plan, "two steps"))
    assert task["status"] == "completed"
    reply = [m["content"] for m in repo.list_messages() if m["task_id"] == task["id"]][-1]
    assert reply == "First step done (one). First step done (two)."


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
     {"text": "stretch", "seconds": 20 * 60}),
    ("mujhe 10 minute baad yaad dilana ki paani peena hai", "reminders.set",
     {"text": "paani peena hai", "seconds": 10 * 60}),
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


# ------------------------------------------------------- sub-minute alarms
# "Set an alarm 4 seconds from now" used to become a one-minute reminder, because
# relative offsets were rounded to minutes, and the word 'alarm' sent it to the
# phone bridge instead, which only understands an hour and a minute.
@pytest.mark.parametrize("text,seconds", [
    ("set an alarm of 4 seconds from now", 4),
    ("set an alarm in 45 seconds", 45),
    ("remind me in 30 seconds to check the compile", 30),
    ("alarm 10 second baad", 10),
    ("set an alarm in two minutes", 120),
])
def test_second_offsets_survive_the_planner(planner, text, seconds):
    plan = asyncio.run(planner.plan(text))
    assert plan.tool == "reminders.set", f"{text} became {plan.tool} {plan.args}"
    assert plan.args.get("seconds") == seconds, f"{text} became {plan.args}"


@pytest.mark.parametrize("text", [
    "set an alarm of 4 seconds from now",
    "alarm 10 second baad",
])
def test_a_duration_alarm_never_reaches_the_phone(planner, text):
    plan = asyncio.run(planner.plan(text))
    assert plan.tool != "phone.alarm", f"{text} needs the paired phone: {plan.tool} {plan.args}"


def test_a_clock_alarm_still_goes_to_the_phone(planner):
    plan = asyncio.run(planner.plan("set an alarm for 7 am"))
    assert plan.tool == "phone.alarm", f"{plan.tool} {plan.args}"


def test_a_duration_alarm_remembers_what_it_was_for(planner):
    plan = asyncio.run(planner.plan("set an alarm of 4 seconds from now to stretch"))
    assert plan.args["text"].lower() == "stretch", plan.args


def test_seconds_reach_the_store_to_the_second(services, planner):
    from datetime import datetime

    plan = asyncio.run(planner.plan("set an alarm of 4 seconds from now to stretch"))
    _, task = asyncio.run(run_task(services.executor, services.repo, plan, "alarm"))
    assert task["status"] == "completed", task["error"]
    stored = [row for row in services.repo.list_reminders() if row["text"] == "stretch"]
    assert stored, "the alarm was not saved"
    due = datetime.fromisoformat(stored[0]["due_at"])
    lead = (due - datetime.now()).total_seconds()
    assert 2 < lead <= 5, f"expected a ~4 second lead, got {lead}"


def test_setting_a_reminder_wakes_the_scheduler(services):
    """The creation event has to be published, or an alarm set during a 15-second
    sleep waits for the sweep instead of ringing on time."""
    queue = services.bus.subscribe()
    try:
        plan = asyncio.run(Planner(
            services.settings, services.repo, services.registry, OfflineRouter()
        ).plan("set an alarm of 30 seconds from now to blink"))
        asyncio.run(run_task(services.executor, services.repo, plan, "alarm"))
        kinds = set()
        while not queue.empty():
            kinds.add(queue.get_nowait()["type"])
        assert "reminder.created" in kinds, kinds
    finally:
        services.bus.unsubscribe(queue)


def test_the_scheduler_rings_a_two_second_alarm_promptly(services):
    from datetime import datetime, timedelta

    from scheduler import ReminderScheduler

    repo = services.repo
    due = repo.add_reminder(
        "ring now",
        (datetime.now() + timedelta(seconds=2)).isoformat(timespec="seconds"),
        speak=False, source="test",
    )
    scheduler = ReminderScheduler(repo, services.bus, tick=15.0)

    async def flow():
        import time

        scheduler.start()
        started = time.monotonic()
        rang = False
        for _ in range(400):
            await asyncio.sleep(0.02)
            if repo.get_reminder(due["id"])["fired"]:
                rang = True
                break
        await scheduler.stop()
        return time.monotonic() - started, rang

    elapsed, rang = asyncio.run(flow())
    assert rang, "the alarm never fired"
    assert elapsed < 5.0, f"a 2-second alarm took {elapsed:.1f}s (the tick is 15s)"
