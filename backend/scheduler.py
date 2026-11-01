"""Reminder delivery while JARVIS is running.

This is an asyncio loop inside the backend process, not a Windows scheduled
task: nothing fires while the app is closed. Reminders that came due in the
meantime are delivered on the next start and labelled late, which is the
honest version of 'I will remind you'.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from database.repository import Repository
from events import EventBus

logger = logging.getLogger("jarvis.reminders")

TICK_SECONDS = 15.0


def _local_now() -> datetime:
    return datetime.now()


def _parse(raw: Any) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is not None:
        moment = moment.astimezone().replace(tzinfo=None)
    return moment


class ReminderScheduler:
    def __init__(self, repo: Repository, bus: EventBus, tick: float = TICK_SECONDS):
        self.repo = repo
        self.bus = bus
        self.tick = tick
        self._task: asyncio.Task[None] | None = None
        self.delivered = 0

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop(), name="reminder-scheduler")

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self._task = None

    async def _loop(self) -> None:
        self._sweep(startup=True)  # anything due while the app was closed
        while True:
            await asyncio.sleep(self.tick)
            try:
                self._sweep()
            except Exception:  # noqa: BLE001 - one bad row must not kill the loop
                logger.exception("Reminder sweep failed")

    def _sweep(self, startup: bool = False) -> list[dict[str, Any]]:
        if not self.repo.get_setting("reminders.enabled", True):
            return []
        now = _local_now()
        fired = []
        for reminder in self.repo.due_reminders(now.isoformat(timespec="seconds")):
            moment = _parse(reminder.get("due_at"))
            late_by = (now - moment).total_seconds() if moment else None
            overdue_at_startup = bool(startup and late_by and late_by > 120)
            payload = {
                "id": reminder["id"],
                "text": reminder["text"],
                "due_at": reminder["due_at"],
                "speak": bool(reminder.get("speak", 1)),
                "late": bool(overdue_at_startup),
                "late_minutes": int(late_by // 60) if late_by and late_by > 0 else 0,
            }
            self.repo.mark_reminder_fired(reminder["id"])
            title = "Reminder" if not payload["late"] else "Reminder (late)"
            body = payload["text"] if not payload["late"] else (
                f"{payload['text']} — this was due about "
                f"{_human(payload['late_minutes'])} ago, while JARVIS was not running."
            )
            notification = self.repo.add_notification("reminder", title, body)
            self.repo.add_message("assistant", f"Reminder: {payload['text']}")
            self.bus.publish("reminder.due", payload)
            self.bus.publish("notification.created", notification)
            fired.append(payload)
        self.delivered += len(fired)
        if fired:
            logger.info("Delivered %s reminder(s)", len(fired))
        return fired

    async def deliver_now(self, reminder_id: int) -> dict[str, Any] | None:
        """Used by the 'test a reminder' button so the path can be checked."""
        reminder = self.repo.get_reminder(reminder_id)
        if reminder is None:
            return None
        payload = {
            "id": reminder["id"],
            "text": reminder["text"],
            "due_at": reminder["due_at"],
            "speak": bool(reminder.get("speak", 1)),
            "late": False,
            "late_minutes": 0,
            "test": True,
        }
        self.bus.publish("reminder.due", payload)
        return payload


def _human(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes} min"
    hours, rest = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} h {rest} min" if rest else f"{hours} hours"
    days, hours = divmod(hours, 24)
    return f"{days} day(s) {hours} h"
