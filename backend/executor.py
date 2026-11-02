"""Task execution: validation, confirmation, async execution, cancellation."""
from __future__ import annotations

import asyncio
import logging
import secrets
from pathlib import Path
from typing import Any

from ai.planner import Plan
from ai.model_router import ModelRouter
from config import Settings
from database.repository import Repository, utc_now
from events import EventBus
from security.audit_log import AuditLog
from security.permissions import PermissionPolicy
from tools.base import ToolContext, ToolDefinition, ToolError, ToolRegistry, ToolValidationError

logger = logging.getLogger("jarvis.executor")

ACTIVE_STATUSES = {"pending", "running", "awaiting_confirmation"}
RISK_ORDER = {"low": 0, "moderate": 1, "high": 2}


def _folder(args: dict[str, Any], key: str = "folder") -> str:
    """The folder's name, never its absolute path — this text is read aloud."""
    return Path(str(args.get(key, ""))).name or "that folder"


def _describe(tool: str, args: dict[str, Any], risk: str) -> str:
    """One plain sentence per action, for the card he approves and for the voice
    that reads it. A raw args dict is not a sentence: "docs.create_presentation
    (moderate risk) with {'topic': ...}" was being spoken aloud word by word."""
    if tool == "system.screenshot":
        return "Take a screenshot and open it on screen"
    if tool == "system.lock":
        return "Lock your screen"
    if tool == "app.close":
        return f"Close {args.get('name', 'the application')}"
    if tool == "image.generate":
        return f"Generate an image of \"{str(args.get('prompt', ''))[:80]}\""
    if tool == "docs.create_presentation":
        return (f"Write a {args.get('slides', 8)}-slide presentation on "
                f"{args.get('topic', 'the topic')}")
    if tool == "code.create_project":
        kind = str(args.get("kind", "calculator")).replace("_", " ")
        return f"Create a {kind} project and open it in VS Code"
    if tool == "shortcuts.open":
        return f"Open {args.get('name')} from your shortcuts"
    if tool == "tasks.cancel":
        return "Stop the task that is running right now"
    if tool == "memory.forget":
        key = args.get("key")
        return f"Forget the memory {key}" if key else "Delete every memory I have stored"
    if tool == "files.move":
        name = Path(str(args.get("source", ""))).name
        return f"Move {name} into {_folder(args, 'destination_folder')}"
    if tool == "files.rename":
        return (f"Rename {Path(str(args.get('path', ''))).name} to "
                f"{args.get('new_name', 'a new name')}")
    if tool == "files.create_folder":
        return f"Create a folder called {args.get('name', 'new folder')}"
    if tool == "phone.whatsapp":
        message = args.get("message", "")
        return (
            f"Open WhatsApp for {args.get('contact')} with the draft "
            f"\"{message[:120]}\" — sending remains manual."
        )
    if tool == "phone.call":
        return f"Open the phone dialer for {args.get('contact')} (no call is placed automatically)."
    if tool == "phone.alarm":
        return f"Set an alarm for {args.get('time')} on your phone."
    if tool == "phone.reels_start":
        return "Enable the accessibility auto-scroll for reels. You can stop it at any time."
    if tool == "files.organize_apply":
        return f"Move loose files in {_folder(args)} into category folders."
    if tool == "files.undo_organize":
        return f"Restore the previous layout of {_folder(args)}."
    if tool == "code.run_tests":
        return f"Execute the unit tests in {_folder(args, 'project_dir')}."
    detail = ", ".join(
        f"{key} {value}" for key, value in args.items() if value not in (None, "", False)
    )
    return f"{tool} ({risk} risk): {detail}" if detail else f"{tool} ({risk} risk)"


def _sequence_of(task: dict[str, Any]) -> list[dict[str, Any]]:
    """The ordered actions a task stands for, always at least one."""
    steps = task.get("steps")
    if isinstance(steps, list) and len(steps) > 1:
        return [dict(step) for step in steps]
    return [{"tool": task.get("tool"), "args": task.get("args") or {}}]


def _describe_plan(steps: list[dict[str, Any]], registry: ToolRegistry) -> str:
    """A human-readable preview of every action, so confirmation is informed."""
    labels = []
    for index, step in enumerate(steps, start=1):
        name = str(step.get("tool") or "")
        args = step.get("args") or {}
        try:
            risk = registry.get(name).risk.value
        except ToolError:
            risk = "unknown"
        text = _describe(name, args, risk)
        labels.append(text if len(steps) == 1 else f"{index}) {text}")
    return " ".join(labels)


class TaskExecutor:
    def __init__(
        self,
        settings: Settings,
        repo: Repository,
        registry: ToolRegistry,
        bus: EventBus,
        policy: PermissionPolicy,
        audit: AuditLog,
        bridge: Any = None,
        router: ModelRouter | None = None,
    ):
        self.settings = settings
        self.repo = repo
        self.registry = registry
        self.bus = bus
        self.policy = policy
        self.audit = audit
        self.bridge = bridge
        self.router = router
        self._running: dict[str, asyncio.Task[None]] = {}

    # ---------------------------------------------------------------- submit
    async def submit(self, text: str, plan: Plan, source: str = "text") -> dict[str, Any]:
        if plan.kind == "reply" or not plan.is_tool:
            reply = plan.reply or "I don't have a reply for that."
            self.repo.add_message("user", text)
            message = self.repo.add_message("assistant", reply)
            self._publish_message(message)
            return {"type": "reply", "reply": reply, "task": None, "message": message,
                    "intent": plan.intent,
                    "requires_confirmation": False}

        # Validate the whole sequence up front: a plan whose third step is bogus
        # must fail before the first step touches the machine.
        try:
            steps = []
            for step in plan.sequence():
                name = str(step.get("tool") or "")
                definition = self.registry.get(name)
                args_model = self.registry.validate(name, step.get("args") or {})
                steps.append((definition, args_model))
        except (ToolError, ToolValidationError) as exc:
            self.repo.add_message("user", text)
            message = self.repo.add_message("assistant", str(exc))
            return {"type": "reply", "reply": str(exc), "task": None, "message": message,
                    "requires_confirmation": False}

        definition, args_model = steps[0]
        tool = definition.name
        args = args_model.model_dump()
        sequence = [{"tool": d.name, "args": a.model_dump()} for d, a in steps]
        normalized = text.strip().lower()
        for existing in self.repo.list_tasks(limit=20):
            if (existing["status"] in ACTIVE_STATUSES
                    and existing["text"].strip().lower() == normalized
                    and existing["tool"] == tool):
                return {
                    "type": "task",
                    "duplicate": True,
                    "task": existing,
                    "requires_confirmation": existing["status"] == "awaiting_confirmation",
                    "confirmation": self._confirmation_payload(existing),
                    "reply": f"That request is already {existing['status'].replace('_', ' ')}.",
                }

        # One confirmation covers the sequence: if any step needs approval, the
        # preview names every step so the user is not asked twice or surprised later.
        needs_confirmation = any(
            self.registry.requires_confirmation(d.name, self.policy, a, self.settings)
            for d, a in steps
        )
        risk = max((d.risk.value for d, _ in steps), key=lambda r: RISK_ORDER.get(r, 99))
        token = secrets.token_urlsafe(18) if needs_confirmation else None
        status = "awaiting_confirmation" if needs_confirmation else "pending"
        task = self.repo.create_task(
            text=text, source=source, tool=tool, args=args, risk=risk,
            status=status, planner=plan.planner, intent=plan.intent,
            confirmation_token=token, steps=sequence,
        )
        self.repo.add_message("user", text, task["id"])
        self.bus.publish("task.created", task)

        if needs_confirmation:
            summary = _describe_plan(sequence, self.registry)
            message = self.repo.add_message(
                "assistant",
                f"Confirmation required before I proceed: {summary}",
                task["id"],
            )
            self._publish_message(message)
            self.bus.publish("confirmation.required",
                             {"task": task, "summary": summary, "token": token})
            return {
                "type": "task", "task": task, "requires_confirmation": True,
                "confirmation": self._confirmation_payload(task, token),
                "reply": f"Confirmation required: {summary}", "message": message,
            }

        asyncio_task = asyncio.create_task(self._run(task["id"]))
        self._running[task["id"]] = asyncio_task
        return {"type": "task", "task": self.repo.get_task(task["id"]),
                "requires_confirmation": False, "confirmation": None,
                "reply": f"Working on: {text}"}

    def _confirmation_payload(self, task: dict[str, Any],
                              token: str | None = None) -> dict[str, Any] | None:
        if task.get("status") != "awaiting_confirmation":
            return None
        return {
            "task_id": task["id"],
            "token": token if token is not None else self.repo.confirmation_token(task["id"]),
            "summary": _describe_plan(_sequence_of(task), self.registry),
            "risk": task.get("risk"),
        }

    # --------------------------------------------------------------- confirm
    def confirm(self, task_id: str, token: str) -> dict[str, Any] | None:
        task = self.repo.get_task(task_id)
        if not task:
            return None
        if task["status"] != "awaiting_confirmation":
            return {"error": f"Task is {task['status']}, not awaiting confirmation."}
        stored = self.repo.confirmation_token(task_id)
        if not stored or not secrets.compare_digest(stored, token):
            return {"error": "Confirmation token is invalid."}
        try:
            for step in _sequence_of(task):
                self.registry.validate(str(step.get("tool") or ""), step.get("args") or {})
        except (ToolError, ToolValidationError) as exc:
            self.repo.update_task(task_id, status="failed", error=str(exc), finished_at=utc_now())
            return {"error": str(exc)}
        self.repo.update_task(task_id, confirmation_token=None)
        asyncio_task = asyncio.create_task(self._run(task_id))
        self._running[task_id] = asyncio_task
        self.audit.record("user", "task.confirm", task.get("risk") or "unknown",
                          {"task_id": task_id, "tool": task["tool"]}, "ok")
        return {"task": self.repo.get_task(task_id)}

    # ---------------------------------------------------------------- cancel
    def cancel(self, task_id: str) -> bool:
        task = self.repo.get_task(task_id)
        if not task or task["status"] not in ACTIVE_STATUSES:
            return False
        if task["status"] == "awaiting_confirmation":
            self.repo.update_task(task_id, status="cancelled", finished_at=utc_now(),
                                  confirmation_token=None)
            self.repo.add_task_event(task_id, "info", "Cancelled before execution")
            self._publish_update(task_id)
            return True
        running = self._running.get(task_id)
        self.repo.update_task(task_id, cancel_requested=1)
        if running and not running.done():
            running.cancel()
        return True

    def cancel_all(self, exclude: str | None = None) -> int:
        count = 0
        for task in self.repo.list_tasks(limit=50):
            if task["id"] == exclude:
                continue
            if task["status"] in ACTIVE_STATUSES and self.cancel(task["id"]):
                count += 1
        return count

    async def shutdown(self) -> None:
        for running in list(self._running.values()):
            if not running.done():
                running.cancel()
        if self._running:
            await asyncio.gather(*self._running.values(), return_exceptions=True)

    # ------------------------------------------------------------------- run
    async def _run(self, task_id: str) -> None:
        task = self.repo.get_task(task_id)
        if not task:
            return
        sequence = _sequence_of(task)
        multi = len(sequence) > 1
        self.repo.update_task(task_id, status="running", started_at=utc_now())
        self.repo.add_task_event(
            task_id, "info",
            f"Executing {len(sequence)} steps" if multi else f"Executing {sequence[0]['tool']}",
        )
        self._publish_update(task_id)
        context = ToolContext(
            settings=self.settings,
            repo=self.repo,
            policy=self.policy,
            audit=self.audit,
            bus=self.bus,
            bridge=self.bridge,
            router=self.router,
            executor=self,
            task_id=task_id,
        )

        outcomes: list[dict[str, Any]] = []
        failure: str | None = None
        for index, step in enumerate(sequence, start=1):
            name = str(step.get("tool") or "")
            definition = self.registry.get(name)
            if multi:
                self.repo.add_task_event(task_id, "info", f"Step {index} of {len(sequence)}: {name}")
                self._publish_update(task_id)
            try:
                args_model = self.registry.validate(name, step.get("args") or {})
            except ToolValidationError as exc:
                failure = str(exc) if not multi else f"Step {index} ({name}): {exc}"
                break
            try:
                result = await self.registry.execute(
                    name, args_model, context, timeout=self.settings.task_timeout_seconds
                )
            except asyncio.CancelledError:
                self.repo.update_task(task_id, status="cancelled", finished_at=utc_now())
                self.repo.add_task_event(task_id, "info", "Cancelled by user")
                stopped = (f"Stopped that task after {len(outcomes)} step(s)." if multi
                           else "Stopped that task.")
                self.repo.add_message("assistant", stopped, task_id)
                self.audit.record("user", name, definition.risk.value,
                                  {"task_id": task_id}, "cancelled")
                self._publish_update(task_id)
                self._running.pop(task_id, None)
                raise
            except Exception as exc:  # noqa: BLE001 - executor must stay alive
                logger.exception("Executor failure for %s", task_id)
                failure = str(exc) if not multi else f"Step {index} ({name}) failed: {exc}"
                self.audit.record("user", name, definition.risk.value,
                                  {"task_id": task_id, "step": index}, "failed")
                break

            outcomes.append({"tool": name, "ok": result.ok,
                             "summary": result.summary, "data": result.data})
            self.audit.record(
                "user", name, definition.risk.value,
                self._audit_args(task_id, index, multi, args_model),
                "ok" if result.ok else "failed",
            )
            if not result.ok:
                failure = result.error or result.summary
                break

        succeeded = failure is None
        if len(outcomes) <= 1 and not multi:
            report = (outcomes[0]["summary"] if outcomes
                      else failure or "Nothing was executed.")
        else:
            parts = [str(o["summary"]).rstrip(".!?") for o in outcomes if o["summary"]]
            if failure and failure.rstrip(".!?") not in parts:
                parts.append(failure.rstrip(".!?"))
            if not parts:
                parts.append("Nothing was executed")
            # Each step already names what it did, so the join is the whole
            # report; "all 3 steps done" afterwards is words over words. A run
            # that stopped short still has to say how far it got.
            report = ". ".join(parts) + "."
            if not succeeded:
                done = sum(1 for o in outcomes if o["ok"])
                report += f" {done} of {len(sequence)} steps finished."

        stored = (outcomes[0]["data"] if len(outcomes) == 1 and succeeded and not multi
                  else {"steps": outcomes, "steps_completed": len(outcomes),
                        "steps_planned": len(sequence)})

        self.repo.update_task(
            task_id, status="completed" if succeeded else "failed",
            result_json=stored, error=None if succeeded else failure,
            finished_at=utc_now(),
        )
        self.repo.add_task_event(task_id, "info" if succeeded else "error", report)
        message = self.repo.add_message("assistant", report, task_id)
        self._publish_message(message)
        self._publish_update(task_id)
        self._running.pop(task_id, None)
        if not self.repo.get_setting("privacy.task_history", True):
            self.repo.delete_task(task_id)
        self.repo.prune()

    @staticmethod
    def _audit_args(task_id: str, index: int, multi: bool, args_model: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {"task_id": task_id, "args": args_model.model_dump()}
        if multi:
            payload["step"] = index
        return payload

    def _publish_message(self, message: dict[str, Any]) -> None:
        """Publish the stored row verbatim.

        task_id must travel with it: the UI decides whether an assistant message
        is the result of a spoken command by matching on task_id, and a payload
        assembled by hand here used to omit it, so task results were never read
        aloud.
        """
        self.bus.publish("message.created", {
            "role": message["role"],
            "content": message["content"],
            "id": message["id"],
            "at": message["at"],
            "task_id": message["task_id"],
        })

    def _publish_update(self, task_id: str) -> None:
        task = self.repo.get_task(task_id)
        if task:
            self.bus.publish("task.updated", task)
