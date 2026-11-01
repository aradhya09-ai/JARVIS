"""Assistant meta-tools: task control, memory, briefings, integration status."""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from security.permissions import Risk
from tools.base import ToolContext, ToolDefinition, ToolResult
from tools.system_monitor import collect_system_stats

INTEGRATIONS = {
    "gmail": "Gmail (read-only summaries, draft replies)",
    "outlook": "Outlook mail and calendar",
    "google_calendar": "Google Calendar events",
    "microsoft_calendar": "Microsoft Calendar events",
    "moodle": "Moodle assignment deadlines",
    "github": "GitHub notifications and pull requests",
}


class TaskListArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=10, ge=1, le=50)


class EmptyArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CancelArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str | None = Field(default=None, max_length=64)


class MemorySetArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=1000)


class MemoryForgetArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str | None = Field(default=None, max_length=80, description="Omit to clear all memory")


class IntegrationArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["gmail", "outlook", "google_calendar", "microsoft_calendar",
                      "moodle", "github"]


async def list_tasks_handler(args: TaskListArgs, ctx: ToolContext) -> ToolResult:
    tasks = ctx.repo.list_tasks(limit=args.limit)
    if not tasks:
        return ToolResult.success("There are no tasks in the history yet.", tasks=[])
    running = [t for t in tasks if t["status"] in {"running", "pending", "awaiting_confirmation"}]
    if running:
        summary = "Currently active: " + "; ".join(
            f"{t['text']} ({t['status']})" for t in running
        ) + "."
    else:
        summary = "No tasks are running. Latest: " + "; ".join(
            f"{t['text']} ({t['status']})" for t in tasks[:3]
        ) + "."
    return ToolResult.success(summary, tasks=tasks)


async def cancel_handler(args: CancelArgs, ctx: ToolContext) -> ToolResult:
    if ctx.executor is None:
        return ToolResult.failure("Task cancellation is unavailable in this context.")
    if args.task_id:
        cancelled = ctx.executor.cancel(args.task_id)
        return ToolResult.success(
            f"Cancelled task {args.task_id}." if cancelled else f"Task {args.task_id} was not running."
        )
    count = ctx.executor.cancel_all(exclude=ctx.task_id)
    if count == 0:
        return ToolResult.success("There was no other running task to stop.")
    return ToolResult.success(f"Stopped {count} running task(s).", cancelled=count)


async def briefing_handler(_: EmptyArgs, ctx: ToolContext) -> ToolResult:
    stats = await asyncio.to_thread(collect_system_stats, False)
    tasks = ctx.repo.list_tasks(limit=20)
    running = [t for t in tasks if t["status"] == "running"]
    failed = [t for t in tasks if t["status"] == "failed"][:2]
    phone = ctx.bridge.status() if ctx.bridge else {"connected": False}
    unread = [n for n in ctx.repo.list_notifications(20) if not n["read"]]
    hour = datetime.now().hour
    greeting = "Good morning" if hour < 12 else "Good afternoon" if hour < 17 else "Good evening"

    battery = stats["battery"]
    battery_text = (
        f"Battery {battery['percent']}% ({'charging' if battery['plugged_in'] else 'on battery'})"
        if battery.get("available") else "Battery status unavailable"
    )
    phone_text = (
        f"Phone connected ({phone.get('name', 'Android')})" if phone.get("connected")
        else "Phone not connected"
    )
    integrations = ctx.repo.list_integrations()
    enabled = [i["provider"] for i in integrations if i["enabled"]]
    lines = [
        f"{greeting}. It is {datetime.now().strftime('%H:%M on %A, %d %B %Y')}.",
        f"System: CPU {stats['cpu']['percent']}%, RAM {stats['memory']['percent']}% used, "
        f"{stats['disk']['free_gb']} GB free. {battery_text}. "
        f"Internet is {'up' if stats['network']['internet'] else 'down'}.",
        f"{phone_text}. {len(running)} task(s) running, {len(failed)} recent failure(s).",
        f"Calendar and email summaries are not available until an integration is authorised"
        f" ({', '.join(enabled) if enabled else 'none configured'}).",
    ]
    if unread:
        lines.append(f"You have {len(unread)} unread notification(s).")
    return ToolResult.success(
        " ".join(lines),
        stats=stats,
        tasks={"running": len(running), "failed": len(failed)},
        phone=phone,
        integrations=enabled,
    )


async def memory_set_handler(args: MemorySetArgs, ctx: ToolContext) -> ToolResult:
    if not ctx.repo.get_setting("privacy.memory_enabled", True):
        return ToolResult.failure("Memory is disabled in Settings → Privacy, so I did not store that.")
    ctx.repo.set_memory(args.key.strip(), args.value.strip())
    ctx.audit.record(ctx.actor, "memory.set", Risk.LOW.value, {"key": args.key}, "ok")
    return ToolResult.success(f"Remembered '{args.key}'.", key=args.key, value=args.value)


async def memory_list_handler(_: EmptyArgs, ctx: ToolContext) -> ToolResult:
    records = ctx.repo.get_memory() or []
    if not records:
        return ToolResult.success("I have no saved memories.", memories=[])
    summary = "Stored memories: " + "; ".join(f"{r['key']} = {r['value']}" for r in records[:10]) + "."
    return ToolResult.success(summary, memories=records)


async def memory_forget_handler(args: MemoryForgetArgs, ctx: ToolContext) -> ToolResult:
    if args.key:
        ctx.repo.delete_memory(args.key)
        ctx.audit.record(ctx.actor, "memory.delete", Risk.LOW.value, {"key": args.key}, "ok")
        return ToolResult.success(f"Forgot '{args.key}'.")
    ctx.repo.clear_memory()
    ctx.audit.record(ctx.actor, "memory.clear", Risk.MODERATE.value, {}, "ok")
    return ToolResult.success("Cleared all stored memories.")


async def integration_status_handler(_: EmptyArgs, ctx: ToolContext) -> ToolResult:
    configured = {row["provider"]: row for row in ctx.repo.list_integrations()}
    report = []
    for key, label in INTEGRATIONS.items():
        row = configured.get(key)
        report.append(
            {
                "provider": key,
                "label": label,
                "configured": bool(row),
                "enabled": bool(row and row["enabled"]),
                "oauth": "not implemented",
            }
        )
    return ToolResult.success(
        "Email, calendar, Moodle and GitHub integrations are NOT IMPLEMENTED beyond local "
        "configuration storage. They require OAuth credentials and explicit permission grants; "
        "JARVIS will not invent emails, events or deadlines.",
        integrations=report,
    )


async def integration_summary_handler(args: IntegrationArgs, ctx: ToolContext) -> ToolResult:
    label = INTEGRATIONS[args.provider]
    row = ctx.repo.get_integration(args.provider)
    state = "configured but not authorised" if row else "not configured"
    return ToolResult.failure(
        f"{label} is {state}. Reading emails, events or assignment deadlines is NOT IMPLEMENTED "
        "until an OAuth application is registered and you grant read-only access.",
        provider=args.provider,
    )


async def capabilities_handler(_: EmptyArgs, ctx: ToolContext) -> ToolResult:
    # Spoken verbatim, so this is a short list of verbs, not a manual.
    supported = [
        "open any installed app, website or Settings page",
        "set screen brightness",
        "set, raise, lower and mute the speaker volume",
        "read real system telemetry",
        "lock the PC and take screenshots",
        "search, organise and summarise files",
        "build PowerPoint decks and Python projects",
        "run web, Amazon and Maps searches",
        "generate images when a provider is configured",
        "control a paired Android phone",
        "keep reminders and remembered facts",
    ]
    unsupported = [
        "email or calendar (no OAuth)",
        "reading back search or Amazon results",
        "WhatsApp without your confirmation",
        "placing calls directly",
        "image generation with no provider",
        "bypassing phone security",
    ]
    text = "I can " + ", ".join(supported[:-1]) + f" and {supported[-1]}. " \
           "Not implemented: " + ", ".join(unsupported) + "."
    return ToolResult.success(text, supported=supported, not_implemented=unsupported)


def build() -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name="tasks.list",
            description="List recent, running and failed tasks.",
            args_model=TaskListArgs,
            risk=Risk.LOW,
            handler=list_tasks_handler,
            category="tasks",
        ),
        ToolDefinition(
            name="tasks.cancel",
            description="Cancel the running task(s), excluding the current one.",
            args_model=CancelArgs,
            risk=Risk.MODERATE,
            handler=cancel_handler,
            confirm=False,
            category="tasks",
        ),
        ToolDefinition(
            name="assistant.briefing",
            description="Prepare a truthful morning briefing from live system, task and phone data.",
            args_model=EmptyArgs,
            risk=Risk.LOW,
            handler=briefing_handler,
            category="assistant",
        ),
        ToolDefinition(
            name="assistant.capabilities",
            description="Explain which features are implemented and which are not.",
            args_model=EmptyArgs,
            risk=Risk.LOW,
            handler=capabilities_handler,
            category="assistant",
        ),
        ToolDefinition(
            name="memory.set",
            description="Store a user-approved preference in local memory.",
            args_model=MemorySetArgs,
            risk=Risk.LOW,
            handler=memory_set_handler,
            category="memory",
        ),
        ToolDefinition(
            name="memory.list",
            description="List stored local memories.",
            args_model=EmptyArgs,
            risk=Risk.LOW,
            handler=memory_list_handler,
            category="memory",
        ),
        ToolDefinition(
            name="memory.forget",
            description="Delete one memory or clear all memories.",
            args_model=MemoryForgetArgs,
            risk=Risk.MODERATE,
            handler=memory_forget_handler,
            confirm=False,
            category="memory",
        ),
        ToolDefinition(
            name="integrations.status",
            description="Report the honest state of email/calendar/Moodle/GitHub integrations.",
            args_model=EmptyArgs,
            risk=Risk.LOW,
            handler=integration_status_handler,
            category="integrations",
        ),
        ToolDefinition(
            name="integrations.summary",
            description="Attempt to summarise data from an external integration (currently unsupported).",
            args_model=IntegrationArgs,
            risk=Risk.LOW,
            handler=integration_summary_handler,
            category="integrations",
        ),
    ]
