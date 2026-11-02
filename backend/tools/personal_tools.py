"""Reminders and typed knowledge as spoken tools.

Honesty rules the handlers must keep: a reminder can only be delivered while
the backend runs, and a fact the assistant was not told stays verified=False.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field

from database.repository import KNOWLEDGE_CATEGORIES
from security.permissions import Risk
from tools.base import ToolContext, ToolDefinition, ToolResult


class EmptyArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReminderSetArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=300)
    due_at: str = Field(default="", max_length=40,
                        description="ISO date-time, local. Empty when a relative offset is given.")
    minutes: int | None = Field(default=None, ge=1, le=60 * 24 * 365)
    seconds: int | None = Field(
        default=None, ge=1, le=60 * 60 * 24 * 30,
        description="Offset in seconds, for a sub-minute alarm like 'ring in 10 seconds'.",
    )
    speak: bool = True


class ReminderCancelArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reminder_id: int | None = None
    fragment: str = Field(default="", max_length=120,
                          description="Word from the reminder text when the id is unknown.")


class ReminderListArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    include_finished: bool = False


class KnowledgeArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=1, max_length=120)
    value: str = Field(min_length=1, max_length=2000)
    category: str = Field(default="preference", max_length=40)
    verified: bool = True
    as_of: str = Field(default="", max_length=80)


def _local_now() -> datetime:
    return datetime.now()


def _parse(raw: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is not None:
        moment = moment.astimezone().replace(tzinfo=None)
    return moment


def _when(moment: datetime) -> str:
    # "%H:%M on Tuesday" is the wrong label for something due before the minute
    # it names, so a near-term alarm is read out to the second.
    if (moment - _local_now()).total_seconds() < 120:
        return moment.strftime("%H:%M:%S")
    return moment.strftime("%H:%M on %A %d %B")


# ------------------------------------------------------------------ reminders
async def reminder_set_handler(args: ReminderSetArgs, ctx: ToolContext) -> ToolResult:
    if args.seconds is not None:
        moment = _local_now() + timedelta(seconds=args.seconds)
    elif args.minutes is not None:
        moment = _local_now() + timedelta(minutes=args.minutes)
    elif args.due_at.strip():
        moment = _parse(args.due_at)
        if moment is None:
            return ToolResult.failure(
                "I could not read that time. Give me something like "
                "'2026-11-02T09:00' or 'in 20 minutes'."
            )
    else:
        return ToolResult.failure(
            "A reminder needs a time. For example: 'remind me to revise normalization "
            "at 9 pm', 'in 20 minutes' or 'in 30 seconds'."
        )
    if moment <= _local_now():
        return ToolResult.failure("That time is already past, so I did not create the reminder.")
    if not ctx.repo.get_setting("reminders.enabled", True):
        return ToolResult.failure(
            "Reminders are switched off in Settings, so I did not queue that."
        )
    due_in = (moment - _local_now()).total_seconds()
    reminder = ctx.repo.add_reminder(
        args.text, moment.isoformat(timespec="seconds"), speak=args.speak, source=ctx.actor
    )
    ctx.audit.record(ctx.actor, "reminder.set", Risk.LOW.value, {"id": reminder["id"]}, "ok")
    # The scheduler is asleep until the reminder it knew about; this wakes it so a
    # 4-second alarm rings in 4 seconds rather than at the end of the current sleep.
    ctx.bus.publish("reminder.created", {"id": reminder["id"], "due_at": reminder["due_at"]})
    if due_in < 120:
        # A timer he is about to hear ring does not need the caveats repeating.
        return ToolResult.success(
            f"Alarm set: {args.text} at {_when(moment)}.", reminder=reminder
        )
    return ToolResult.success(
        f"Reminder set: {args.text} at {_when(moment)}. It rings only while JARVIS is running; "
        "if it falls due while the app is closed you will get it as 'late' on the next start.",
        reminder=reminder,
    )


async def reminder_list_handler(args: ReminderListArgs, ctx: ToolContext) -> ToolResult:
    rows = ctx.repo.list_reminders(include_finished=args.include_finished)
    if not rows:
        return ToolResult.success("You have no pending reminders.", reminders=[])
    lines = []
    for row in rows[:10]:
        moment = _parse(str(row.get("due_at")))
        label = _when(moment) if moment else str(row.get("due_at"))
        state = ("fired" if row.get("fired") else
                 "cancelled" if row.get("cancelled") else "pending")
        lines.append(f"{row['text']} at {label} ({state})")
    return ToolResult.success("Your reminders: " + "; ".join(lines) + ".", reminders=rows)


async def reminder_cancel_handler(args: ReminderCancelArgs, ctx: ToolContext) -> ToolResult:
    target_id = args.reminder_id
    if target_id is None and args.fragment.strip():
        needle = args.fragment.lower()
        matches = [r for r in ctx.repo.list_reminders() if needle in r["text"].lower()]
        if not matches:
            return ToolResult.failure(
                f"No pending reminder matches '{args.fragment}', so nothing was cancelled."
            )
        if len(matches) > 1:
            return ToolResult.failure(
                "More than one reminder matches: "
                + "; ".join(m["text"] for m in matches[:4])
                + ". Tell me which one to cancel."
            )
        target_id = int(matches[0]["id"])
    if target_id is None:
        return ToolResult.failure("Say which reminder to cancel.")
    if not ctx.repo.cancel_reminder(target_id):
        return ToolResult.failure(f"There is no reminder {target_id}.")
    ctx.audit.record(ctx.actor, "reminder.cancel", Risk.LOW.value, {"id": target_id}, "ok")
    return ToolResult.success("Cancelled that reminder.", id=target_id)


# ------------------------------------------------------------------ knowledge
async def knowledge_remember_handler(args: KnowledgeArgs, ctx: ToolContext) -> ToolResult:
    if not ctx.repo.get_setting("privacy.memory_enabled", True):
        return ToolResult.failure(
            "Memory is switched off in Settings → Privacy, so I did not store that."
        )
    category = args.category if args.category in KNOWLEDGE_CATEGORIES else "unverified"
    item = ctx.repo.remember(
        args.key.strip(), args.value.strip(), category=category, source="user",
        verified=args.verified, as_of=(args.as_of.strip() or None),
    )
    ctx.audit.record(ctx.actor, "knowledge.remember", Risk.LOW.value,
                     {"key": args.key, "category": category}, "ok")
    return ToolResult.success(
        f"Noted under {category}: {args.value}"
        + ("" if args.verified else " (marked as needing confirmation)"),
        item=item,
    )


async def knowledge_list_handler(_: EmptyArgs, ctx: ToolContext) -> ToolResult:
    rows = ctx.repo.list_knowledge()
    if not rows:
        return ToolResult.success("I have no stored knowledge about you yet.", items=[])
    unverified = [r for r in rows if not r.get("verified")]
    summary = (
        f"I hold {len(rows)} facts. "
        + ("All are marked as things you confirmed." if not unverified else
           "Still to confirm: " + ", ".join(r["key"] for r in unverified[:6]) + ".")
    )
    return ToolResult.success(summary, items=rows, unverified=[r["key"] for r in unverified])


def build() -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name="reminders.set",
            description="Create a reminder or alarm for a clock time, in N minutes, or in N seconds.",
            args_model=ReminderSetArgs, risk=Risk.LOW,
            handler=reminder_set_handler, category="personal",
        ),
        ToolDefinition(
            name="reminders.list",
            description="List pending reminders.",
            args_model=ReminderListArgs, risk=Risk.LOW,
            handler=reminder_list_handler, category="personal",
        ),
        ToolDefinition(
            name="reminders.cancel",
            description="Cancel one reminder by id or by a word from its text.",
            args_model=ReminderCancelArgs, risk=Risk.LOW,
            handler=reminder_cancel_handler, category="personal",
        ),
        ToolDefinition(
            name="knowledge.remember",
            description="Store a typed personal fact with its category and verification state.",
            args_model=KnowledgeArgs, risk=Risk.LOW,
            handler=knowledge_remember_handler, category="memory",
        ),
        ToolDefinition(
            name="knowledge.list",
            description="Summarise stored personal knowledge.",
            args_model=EmptyArgs, risk=Risk.LOW,
            handler=knowledge_list_handler, category="memory",
        ),
    ]
